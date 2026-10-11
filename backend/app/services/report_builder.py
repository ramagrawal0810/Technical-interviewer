"""Build the per-interview feedback report from persisted data.

No LLM is called: every section is assembled from stored interview, answer, evaluation, recommendation, review
and security-check records, so opening or downloading a report is free, repeatable and creates nothing.
Evidence quotes are only shown if they are verbatim text from the candidate's actual answer. Anything that was
not stored is reported as unavailable, never filled in.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Candidate, HumanReview, Interview, JobDescription, Recommendation
from app.schemas import (
    DimensionEvaluation, FeedbackReport, ReportConsent, ReportDimension, ReportEvidence, ReportHumanReview,
    ReportPoint, ReportQA, ReportQADimension, ReportSecurity, ReportSecurityFinding,
)
from app.services.evaluation_aggregator import DIMENSION_KEYS, aggregate_overall_evaluation
from app.services.interview_session import InterviewStep, load_steps_for_interview, number_steps
from app.services.question_adapter import CV_MODES
from app.services.security_checks import CV_SOURCE_ID, SOURCE_ANSWER, SOURCE_CV, STATUS_FAILED, summarise_checks
from app.services.injection_detector import CATEGORY_DESCRIPTIONS, STATUS_CLEAN, STATUS_SUSPECTED

logger = logging.getLogger(__name__)

DIMENSION_LABELS = {
    "technical_skills": "Technical Skills",
    "domain_understanding": "Domain Understanding",
    "problem_solving": "Problem-Solving Approach",
    "communication_clarity": "Communication Clarity",
}
STRENGTH_MIN_SCORE = 4
GAP_MAX_SCORE = 2
NEXT_ROUND_AVERAGE_BELOW = 3.5
MAX_NEXT_ROUND_ITEMS = 8

_LEGACY_RECOMMENDATIONS = {"strong_hire": "Strong Hire", "hire": "Hire", "consider": "Hold", "reject": "No Hire"}
_VALID_RECOMMENDATIONS = {"Strong Hire", "Hire", "Hold", "No Hire"}


class ReportNotAvailable(Exception):
    """The interview does not exist or is not completed."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def normalise_recommendation(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if value in _VALID_RECOMMENDATIONS:
        return value
    return _LEGACY_RECOMMENDATIONS.get(value.strip().lower().replace(" ", "_"), value)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def verified_quote(quote: Optional[str], answer: Optional[str]) -> Optional[str]:
    """The quote, only if it is verbatim text from the candidate's answer (ignoring case and whitespace)."""
    if not quote or not answer or quote.startswith("[Paraphrased]"):
        return None
    candidate = _collapse(quote).rstrip(".…").strip()
    return quote.strip() if candidate and candidate in _collapse(answer) else None


def _parse_dimensions(step: InterviewStep) -> Optional[DimensionEvaluation]:
    evaluation = step.evaluation
    if evaluation is None or not evaluation.dimensions:
        return None
    try:
        return DimensionEvaluation(**json.loads(evaluation.dimensions))
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning(f"Evaluation {evaluation.id} has malformed dimensions JSON; treating 4D data as unavailable")
        return None


def _label(numbers: tuple) -> str:
    main, follow_up = numbers
    return f"Question {main}" if follow_up == 0 else f"Question {main}, follow-up {follow_up}"


def build_feedback_report(db: Session, interview_id: int, now: Optional[datetime] = None) -> FeedbackReport:
    """
    Raises:
        ReportNotAvailable: 404 if the interview does not exist, 400 if it is not completed
    """
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if interview is None:
        raise ReportNotAvailable("Interview not found", 404)
    if interview.status not in ("completed", "abandoned"):
        raise ReportNotAvailable("A feedback report is only available for completed or abandoned interviews", 400)
    partial = interview.status == "abandoned"

    candidate = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
    job = db.query(JobDescription).filter(JobDescription.id == interview.job_description_id).first()
    steps = load_steps_for_interview(db, interview_id)
    numbers = number_steps(steps)
    labels = [_label(n) for n in numbers]
    parsed = [_parse_dimensions(step) for step in steps]

    evaluated_steps = [i for i, s in enumerate(steps) if s.evaluation is not None]
    answered = sum(1 for s in steps if s.answer is not None)

    # ---- overall ----
    scores = [steps[i].evaluation.score for i in evaluated_steps]
    overall_score = int(sum(scores) / len(scores) + 0.5) if scores and not partial else None   # no full-interview score for partial evidence
    recommendation = db.query(Recommendation).filter(
        Recommendation.interview_id == interview_id).order_by(Recommendation.id.desc()).first()
    ai_recommendation = normalise_recommendation(recommendation.recommendation) if recommendation else None

    # ---- 4D dimensions with evidence ----
    overall_4d = aggregate_overall_evaluation(parsed, total_questions=len(steps))
    dimensions = []
    for key in DIMENSION_KEYS:
        evidence = []
        for i, dims in enumerate(parsed):
            if dims is None:
                continue
            dim = getattr(dims, key)
            quote = verified_quote(dim.quote, steps[i].answer.answer_text if steps[i].answer else None)
            evidence.append(ReportEvidence(
                question=labels[i], score=dim.score, justification=dim.justification, quote=quote,
                quote_note=None if quote else "No verbatim quote from the answer was stored for this score.",
            ))
        average = getattr(overall_4d, key) if overall_4d else None
        dimensions.append(ReportDimension(
            key=key, label=DIMENSION_LABELS[key], average=average, evaluated_answers=len(evidence), evidence=evidence,
            note=None if evidence else "No answer in this interview has a 4D evaluation, so this dimension has no score.",
        ))

    # ---- strengths and gaps: from stored scores, justifications and evidence only ----
    strengths, gaps = [], []
    for key in DIMENSION_KEYS:
        scored = [(i, getattr(d, key)) for i, d in enumerate(parsed) if d is not None]
        if not scored:
            continue
        best_i, best = max(scored, key=lambda pair: (pair[1].score, -pair[0]))
        if best.score >= STRENGTH_MIN_SCORE:
            strengths.append(ReportPoint(
                text=f"{DIMENSION_LABELS[key]} ({best.score}/5): {best.justification}", source=labels[best_i],
                quote=verified_quote(best.quote, steps[best_i].answer.answer_text)))
        worst_i, worst = min(scored, key=lambda pair: (pair[1].score, pair[0]))
        if worst.score <= GAP_MAX_SCORE:
            gaps.append(ReportPoint(
                text=f"{DIMENSION_LABELS[key]} ({worst.score}/5): {worst.justification}", source=labels[worst_i],
                quote=verified_quote(worst.quote, steps[worst_i].answer.answer_text)))
    missing_items = []
    for i in evaluated_steps:
        missing = (steps[i].evaluation.missing_information or "").strip()
        if missing and missing not in [m for m, _ in missing_items]:
            missing_items.append((missing, labels[i]))
    for missing, label in missing_items:
        gaps.append(ReportPoint(text=f"Gap noted by the evaluator: {missing}", source=label))

    # ---- suggested areas for the next round ----
    next_round = []
    for key in DIMENSION_KEYS:
        average = getattr(overall_4d, key) if overall_4d else None
        if average is not None and average < NEXT_ROUND_AVERAGE_BELOW:
            lowest = min((getattr(d, key) for d in parsed if d is not None), key=lambda dim: dim.score)
            next_round.append(ReportPoint(
                text=f"Probe {DIMENSION_LABELS[key]} further (average {average:.1f}/5): {lowest.justification}", source=None))
    for missing, label in missing_items:
        next_round.append(ReportPoint(text=f"Follow up on: {missing}", source=label))
    for i, step in enumerate(steps):
        if step.answer is None:
            next_round.append(ReportPoint(text=f"{labels[i]} was never answered; assess this topic directly.", source=labels[i]))
        elif step.evaluation is None:
            next_round.append(ReportPoint(text=f"{labels[i]} was answered but not evaluated; assess this topic directly.", source=labels[i]))

    # ---- transcript ----
    transcript = []
    for i, step in enumerate(steps):
        dims = parsed[i]
        answer_text = step.answer.answer_text if step.answer else None
        if step.answer is None:
            status, note = "no_answer", "No answer was submitted for this question."
        elif step.evaluation is None:
            status, note = "not_evaluated", "The answer was saved but has no stored evaluation."
        elif dims is None:
            status, note = "evaluated", "Scored, but this answer has no 4D breakdown (evaluated before 4D scoring or the stored data is unreadable)."
        else:
            status, note = "evaluated", None
        qa_dims = None
        if dims is not None:
            qa_dims = [ReportQADimension(
                label=DIMENSION_LABELS[key], score=getattr(dims, key).score, justification=getattr(dims, key).justification,
                quote=verified_quote(getattr(dims, key).quote, answer_text)) for key in DIMENSION_KEYS]
        transcript.append(ReportQA(
            label=labels[i], is_follow_up=step.is_follow_up, question=step.question.question, answer=answer_text,
            evaluation_status=status, score=step.evaluation.score if step.evaluation else None, dimensions=qa_dims,
            follow_up_reason=step.evaluation.follow_up_reason if step.evaluation else None,
            missing_information=(step.evaluation.missing_information or None) if step.evaluation else None, note=note))

    # ---- human review (kept separate from the AI recommendation) ----
    review = db.query(HumanReview).filter(HumanReview.interview_id == interview_id).first()
    if review is None and partial:
        human_review = ReportHumanReview(present=False, note="Human review is not offered for an abandoned (partial) interview.")
    elif review is None:
        human_review = ReportHumanReview(present=False, note="No human review has been recorded for this interview yet.")
    else:
        accepted = review.decision_type == "ACCEPT"
        human_review = ReportHumanReview(
            present=True,
            decision=review.decision,
            decision_type="Accepted the AI recommendation" if accepted else "Overrode the AI recommendation",
            interviewer_id=review.interviewer_id,
            interviewer_name=review.interviewer_name,
            reviewed_at=review.reviewed_at or review.created_at,
            override_reason=review.override_reason,
            ai_recommendation_at_review=normalise_recommendation(review.ai_recommendation),
            ai_confidence_at_review=review.ai_confidence,
            note=None if review.override_reason else "No written feedback is stored: the interviewer accepted the AI recommendation without a comment.",
        )

    # ---- security ----
    answer_ids = [s.answer.id for s in steps if s.answer is not None]
    from app.db.models import ContentSecurityCheck
    checks = db.query(ContentSecurityCheck).filter(ContentSecurityCheck.interview_id == interview_id).all()
    summary = summarise_checks(checks, expected_cv=bool(interview.cv_text), answer_ids=answer_ids)
    answer_label = {s.answer.id: labels[i] for i, s in enumerate(steps) if s.answer is not None}
    findings = []
    for check in summary["flagged"]:
        source_label = "CV" if check.source == SOURCE_CV else f"Answer to {answer_label.get(check.source_id, 'a question')}"
        try:
            categories = json.loads(check.categories) if check.categories else []
            categories = [str(c) for c in categories] if isinstance(categories, list) else []
        except (json.JSONDecodeError, TypeError):
            categories = []  # a damaged stored record must not break the report
        findings.append(ReportSecurityFinding(
            source=source_label, categories=[CATEGORY_DESCRIPTIONS.get(c, c) for c in categories],
            excerpt=check.excerpt, explanation=check.explanation))
    checked_what = ("the CV and " if interview.cv_text else "") + f"{len(answer_ids)} answer(s)"  # all clean when this is shown
    if summary["overall"] == STATUS_SUSPECTED:
        text = ("Suspected prompt-injection attempt detected. The flagged content was evaluated as ordinary text: the "
                "instruction was not followed and scores were not adjusted because of it.")
    elif summary["overall"] == STATUS_FAILED:
        text = f"The security check failed for {summary['failed']} item(s), so their status is unknown."
    elif summary["overall"] == STATUS_CLEAN:
        text = f"No suspected prompt-injection attempt detected (checked {checked_what})."
    else:
        text = (f"Unknown: {summary['unchecked']} of {summary['total']} items were not checked "
                "(this interview predates the security check, or the check did not run).")
    security = ReportSecurity(status=summary["overall"], summary=text, findings=findings)

    consent = ReportConsent(status=interview.consent_status or "not_recorded", recorded_at=interview.consent_recorded_at,
                            notice_version=interview.consent_notice_version)

    return FeedbackReport(
        generated_at=now or datetime.utcnow(),
        interview_id=interview.id,
        candidate_name=candidate.name if candidate else "Unknown",
        job_title=job.title if job else "Unknown",
        mode=interview.mode,
        completed_at=interview.completed_at,
        is_partial=partial,
        interview_status=interview.status,
        abandoned_at=interview.abandoned_at,
        partial_label="Partial Interview \u2014 Abandoned" if partial else None,
        answered_count=answered,
        main_questions_asked=sum(1 for s in steps if not s.is_follow_up),
        ai_recommendation_note=(
            "Preliminary only: the candidate ended the interview before finishing, so this recommendation does not reflect a "
            "full interview." if partial and recommendation else None),
        consent=consent,
        overall_score=overall_score,
        ai_recommendation=ai_recommendation,
        ai_confidence=recommendation.confidence if recommendation else None,
        overall_assessment=recommendation.reason if recommendation else None,
        evaluation_coverage=f"{len(evaluated_steps)} of {len(steps)} asked questions evaluated; {answered} answered",
        strengths=strengths,
        strengths_note=None if strengths else f"No answer scored {STRENGTH_MIN_SCORE} or higher on any dimension, so no strengths are recorded.",
        gaps=gaps,
        gaps_note=None if gaps else "No low scores or evaluator-noted gaps were stored for this interview.",
        dimensions=dimensions,
        next_round_areas=next_round[:MAX_NEXT_ROUND_ITEMS],
        next_round_note=None if next_round else
        "No weak areas were recorded, so there is no evidence-based focus for the next round; topics to cover are at the interviewers' discretion.",
        transcript=transcript,
        human_review=human_review,
        security=security,
    )
