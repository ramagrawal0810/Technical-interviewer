"""Human review API endpoints for interviewer decision-making."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import (
    Interview, InterviewQuestion, Answer, Evaluation, Recommendation, HumanReview, Question, Candidate, JobDescription
)
from app.schemas import HumanReviewCreate, HumanReviewResponse, InterviewReviewData, QuestionAnswerEvaluation, DimensionEvaluation
from app.services.evaluation_aggregator import aggregate_overall_evaluation
from app.services.interview_status import compute_display_status
import json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/interviews", tags=["human_review"])

# Demo interviewer identity (no auth)
DEMO_INTERVIEWER_ID = "demo-interviewer"
DEMO_INTERVIEWER_NAME = "Demo Interviewer"

# Valid values
VALID_DECISIONS = {"Strong Hire", "Hire", "Hold", "No Hire"}
VALID_DECISION_TYPES = {"ACCEPT", "OVERRIDE"}
MIN_OVERRIDE_REASON_LENGTH = 20


def _validate_interview_completed(interview: Interview) -> None:
    """Validate that interview exists and is completed."""
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.status != "completed":
        raise HTTPException(status_code=400, detail="Interview must be completed before review")


# Recommendations stored before the four-label vocabulary existed
_LEGACY_RECOMMENDATION_LABELS = {
    "strong_hire": "Strong Hire",
    "hire": "Hire",
    "consider": "Hold",
    "reject": "No Hire",
}


def _normalize_recommendation(value: str) -> str:
    """Map a stored AI recommendation onto the Strong Hire/Hire/Hold/No Hire labels."""
    if value in VALID_DECISIONS:
        return value
    return _LEGACY_RECOMMENDATION_LABELS.get(value.strip().lower().replace(" ", "_"), value)


def _find_latest_recommendation(db: Session, interview_id: int):
    """Latest AI recommendation for an interview, or None (an interview may have several rows)."""
    return db.query(Recommendation).filter(
        Recommendation.interview_id == interview_id
    ).order_by(Recommendation.id.desc()).first()


def _get_interview_recommendation(db: Session, interview_id: int) -> Recommendation:
    """Get AI recommendation for interview."""
    recommendation = _find_latest_recommendation(db, interview_id)
    if not recommendation:
        raise HTTPException(status_code=400, detail="No AI recommendation found for this interview")
    if _normalize_recommendation(recommendation.recommendation) not in VALID_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail=f"AI recommendation '{recommendation.recommendation}' is not a recognised value"
        )
    return recommendation


# ===== GET: Review Data (View Phase) =====

@router.get("/{interview_id}/review-data", response_model=InterviewReviewData)
def get_review_data(interview_id: int, db: Session = Depends(get_db)):
    """
    Get complete interview data needed for human review.

    Returns:
    - Interview details
    - Full Q&A pairs with complete 4D evaluations
    - AI assessment (recommendation, confidence, reasoning)
    - Existing human review if available

    Does NOT allow modification of AI evaluation.
    """
    logger.info(f"Fetching review data for interview {interview_id}")

    # Get interview
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    _validate_interview_completed(interview)

    # Get candidate and job
    candidate = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
    job = db.query(JobDescription).filter(JobDescription.id == interview.job_description_id).first()

    # Build interview dict
    interview_data = {
        "id": interview.id,
        "candidate_name": candidate.name if candidate else "Unknown",
        "candidate_email": candidate.email if candidate else "Unknown",
        "job_title": job.title if job else "Unknown",
        "mode": interview.mode,
        "status": interview.status,
        "created_at": interview.created_at,
        "completed_at": interview.completed_at,
    }

    # Get all Q&A pairs with evaluations
    interview_questions = db.query(InterviewQuestion).filter(
        InterviewQuestion.interview_id == interview_id
    ).order_by(InterviewQuestion.question_order).all()

    qa_evals = []
    evaluation_scores = []
    question_dimensions = []
    for iq in interview_questions:
        question = db.query(Question).filter(Question.id == iq.question_id).first()
        answer = db.query(Answer).filter(Answer.interview_question_id == iq.id).first()
        evaluation = db.query(Evaluation).filter(Evaluation.answer_id == answer.id).first() if answer else None

        if evaluation:
            evaluation_scores.append(evaluation.score)

        # Parse dimensions JSON. Evaluations created before the 4D system have none.
        dimensions = None
        if evaluation and evaluation.dimensions:
            try:
                dimensions = DimensionEvaluation(**json.loads(evaluation.dimensions))
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(f"Evaluation {evaluation.id} has malformed dimensions JSON: {e}")
        question_dimensions.append(dimensions)

        qa_eval = QuestionAnswerEvaluation(
            order=iq.question_order,
            question=question.question if question else "Unknown",
            answer=answer.answer_text if answer else "No answer",
            evaluation=dimensions,
            is_follow_up=bool(question and question.category == "follow_up"),
        )
        qa_evals.append(qa_eval)

    # Get AI recommendation (if available)
    ai_rec = _find_latest_recommendation(db, interview_id)

    # Same calculation as the interview completion endpoint
    overall_score = (
        int(sum(evaluation_scores) / len(evaluation_scores) + 0.5) if evaluation_scores else None
    )

    ai_assessment = {
        "overall_score": overall_score,
        "recommendation": _normalize_recommendation(ai_rec.recommendation) if ai_rec else None,
        "confidence": ai_rec.confidence if ai_rec else None,
        "reason": ai_rec.reason if ai_rec else None,
    }

    # Get existing human review if any
    human_review = db.query(HumanReview).filter(
        HumanReview.interview_id == interview_id
    ).first()

    human_review_response = None
    if human_review:
        human_review_response = HumanReviewResponse.model_validate(human_review)

    logger.info(f"Successfully fetched review data for interview {interview_id}")

    return InterviewReviewData(
        interview=interview_data,
        display_status=compute_display_status(interview.status, human_review is not None),
        cv_text=interview.cv_text,
        questions_answers_evaluations=qa_evals,
        overall_evaluation=aggregate_overall_evaluation(question_dimensions, total_questions=len(interview_questions)),
        ai_assessment=ai_assessment,
        human_review=human_review_response
    )


# ===== GET: Existing Human Review =====

@router.get("/{interview_id}/human-review", response_model=HumanReviewResponse)
def get_human_review(interview_id: int, db: Session = Depends(get_db)):
    """
    Get existing human review for an interview.

    Returns the human review if it exists, or 404 if not reviewed yet.
    """
    logger.info(f"Fetching human review for interview {interview_id}")

    # Verify interview exists
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    # Get human review
    human_review = db.query(HumanReview).filter(
        HumanReview.interview_id == interview_id
    ).first()

    if not human_review:
        raise HTTPException(status_code=404, detail="No human review exists for this interview")

    logger.info(f"Successfully fetched human review for interview {interview_id}")

    return HumanReviewResponse.model_validate(human_review)


# ===== POST: Accept AI Recommendation =====

@router.post("/{interview_id}/human-review", response_model=HumanReviewResponse, status_code=201)
def accept_review(interview_id: int, db: Session = Depends(get_db)):
    """
    Accept the AI recommendation.

    - Captures AI recommendation at review time
    - Sets decision = AI recommendation
    - decision_type = "ACCEPT"
    - No override reason
    """
    logger.info(f"Submitting ACCEPT review for interview {interview_id}")

    # Validate interview
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    _validate_interview_completed(interview)

    # Check if already reviewed
    existing_review = db.query(HumanReview).filter(
        HumanReview.interview_id == interview_id
    ).first()
    if existing_review:
        raise HTTPException(status_code=400, detail="Interview has already been reviewed")

    # Get AI recommendation (this validates it exists)
    ai_rec = _get_interview_recommendation(db, interview_id)

    ai_label = _normalize_recommendation(ai_rec.recommendation)

    # Create human review
    try:
        human_review = HumanReview(
            interview_id=interview_id,
            interviewer_id=DEMO_INTERVIEWER_ID,
            interviewer_name=DEMO_INTERVIEWER_NAME,
            ai_recommendation=ai_label,
            ai_confidence=ai_rec.confidence,
            ai_reasoning=ai_rec.reason,
            decision=ai_label,  # Accept AI recommendation
            decision_type="ACCEPT",
            override_reason=None,
            reviewed_at=datetime.utcnow()
        )
        db.add(human_review)
        db.commit()
        db.refresh(human_review)

        logger.info(f"Successfully created ACCEPT review for interview {interview_id}")

        return HumanReviewResponse.model_validate(human_review)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating human review for interview {interview_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating review")


# ===== POST: Override AI Recommendation =====

@router.post("/{interview_id}/human-review/override", response_model=HumanReviewResponse, status_code=201)
def override_review(interview_id: int, review_data: HumanReviewCreate, db: Session = Depends(get_db)):
    """
    Override the AI recommendation with a human decision.

    - Provide final decision (Strong Hire, Hire, Hold, No Hire)
    - Provide override reason (mandatory, min 20 characters)
    - Captures AI recommendation at review time for audit trail
    - decision_type = "OVERRIDE"
    """
    logger.info(f"Submitting OVERRIDE review for interview {interview_id}")

    # Validate interview
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    _validate_interview_completed(interview)

    # Check if already reviewed
    existing_review = db.query(HumanReview).filter(
        HumanReview.interview_id == interview_id
    ).first()
    if existing_review:
        raise HTTPException(status_code=400, detail="Interview has already been reviewed")

    # Validate decision
    if review_data.decision not in VALID_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Decision must be one of: {VALID_DECISIONS}"
        )

    # Validate override reason
    if not review_data.override_reason:
        raise HTTPException(status_code=400, detail="Override reason is mandatory")

    override_reason = review_data.override_reason.strip()
    if len(override_reason) < MIN_OVERRIDE_REASON_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Override reason must be at least {MIN_OVERRIDE_REASON_LENGTH} characters"
        )

    # Get AI recommendation (validates it exists)
    ai_rec = _get_interview_recommendation(db, interview_id)
    ai_label = _normalize_recommendation(ai_rec.recommendation)

    if review_data.decision == ai_label:
        raise HTTPException(
            status_code=400,
            detail="Override decision must differ from the AI recommendation; use accept instead"
        )

    # Create human review
    try:
        human_review = HumanReview(
            interview_id=interview_id,
            interviewer_id=DEMO_INTERVIEWER_ID,
            interviewer_name=DEMO_INTERVIEWER_NAME,
            ai_recommendation=ai_label,
            ai_confidence=ai_rec.confidence,
            ai_reasoning=ai_rec.reason,
            decision=review_data.decision,  # Human's decision
            decision_type="OVERRIDE",
            override_reason=override_reason,
            reviewed_at=datetime.utcnow()
        )
        db.add(human_review)
        db.commit()
        db.refresh(human_review)

        logger.info(f"Successfully created OVERRIDE review for interview {interview_id}")
        logger.info(f"AI recommended: {ai_label}, Human decided: {review_data.decision}")

        return HumanReviewResponse.model_validate(human_review)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating human review for interview {interview_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating review")
