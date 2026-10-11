"""Interview API endpoints."""

import logging
import math
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, File
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Interview, Candidate, JobDescription, Question, InterviewQuestion, Answer, Evaluation, Recommendation, HumanReview
from app.schemas import (
    InterviewResponse, InterviewCreate, InterviewStartResponse, AnswerResponse, AnswerSubmission, InterviewCompletionResponse, NextQuestionResponse, InterviewQuestionBrief,
    ConsentRequest, ConsentResponse, DimensionEvaluation, InterviewSessionResponse, SessionStep, InterviewProgressResponse, ProgressMainQuestion, ProgressStep,
)
from app.services.interview_session import (
    ACTION_COMPLETE, InterviewStep, last_activity, load_interview_steps, load_steps_for_interview,
    main_question_scores, next_action, number_steps, step_state, trailing_follow_ups,
)
from app.services.data_policy import CONSENT_NOTICE_VERSION, require_consent
from app.services.interview_status import compute_display_status
from app.services.locks import keyed_lock
from app.services.llm_evaluation import generate_recommendation
from app.services.question_adapter import (
    CV_MODES, MAIN_QUESTION_COUNT, MAX_FOLLOW_UPS, decide_follow_up, target_main_difficulty,
)
from app.services.question_selector import FOLLOW_UP_CATEGORY, difficulty_rank, select_question_bank_adaptive
from app.services.security_checks import CV_SOURCE_ID, SOURCE_ANSWER, SOURCE_CV, record_security_check

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/interviews", tags=["interviews"])

_STORED_STATUSES = {"not_started", "in_progress", "completed", "abandoned"}
# Display-status aliases accepted by the list filter
_STATUS_FILTER_ALIASES = {"scheduled": "not_started", "reviewed": "completed"}


def _build_interview_responses(db: Session, interviews: list[Interview]) -> list[InterviewResponse]:
    """Interview responses with derived display_status, job_title and candidate_name."""
    if not interviews:
        return []

    candidate_ids = {iv.candidate_id for iv in interviews}
    job_ids = {iv.job_description_id for iv in interviews}
    interview_ids = [iv.id for iv in interviews]

    candidates = {c.id: c for c in db.query(Candidate).filter(Candidate.id.in_(candidate_ids)).all()}
    candidate_names = {cid: c.name for cid, c in candidates.items()}
    job_titles = {
        j.id: j.title for j in db.query(JobDescription).filter(JobDescription.id.in_(job_ids)).all()
    }
    reviewed_ids = {
        row[0] for row in db.query(HumanReview.interview_id).filter(HumanReview.interview_id.in_(interview_ids)).all()
    }

    responses = []
    for iv in interviews:
        response = InterviewResponse.model_validate(iv)
        response.display_status = compute_display_status(iv.status, iv.id in reviewed_ids)
        response.job_title = job_titles.get(iv.job_description_id)
        response.candidate_name = candidate_names.get(iv.candidate_id)
        saved_cv = candidates[iv.candidate_id].has_cv if iv.candidate_id in candidates else False
        response.cv_available = bool((iv.cv_text or "").strip()) or (saved_cv and iv.status == "not_started")
        responses.append(response)
    return responses


@router.get("", response_model=list[InterviewResponse])
def get_all_interviews(
    candidate_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Get interviews, optionally filtered.

    Args:
        candidate_id: Only this candidate's interviews
        status: not_started, in_progress or completed (stored status; "completed" includes
            reviewed interviews), or the aliases "scheduled" (= not_started) and
            "reviewed" (= completed with a human review)

    Raises:
        HTTPException: 400 if status is not recognised
    """
    query = db.query(Interview)

    if candidate_id is not None:
        query = query.filter(Interview.candidate_id == candidate_id)

    if status is not None:
        normalized = status.strip().lower().replace(" ", "_")
        if normalized not in _STORED_STATUSES and normalized not in _STATUS_FILTER_ALIASES:
            allowed = sorted(_STORED_STATUSES | set(_STATUS_FILTER_ALIASES))
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(allowed)}")
        query = query.filter(Interview.status == _STATUS_FILTER_ALIASES.get(normalized, normalized))
        if normalized == "reviewed":
            query = query.filter(Interview.id.in_(db.query(HumanReview.interview_id)))

    return _build_interview_responses(db, query.order_by(Interview.id).all())


@router.get("/{interview_id}", response_model=InterviewResponse)
def get_interview(interview_id: int, db: Session = Depends(get_db)):
    """
    Get a specific interview by ID.

    Args:
        interview_id: The ID of the interview to retrieve

    Returns:
        The interview details

    Raises:
        HTTPException: 404 if interview not found
    """
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    return _build_interview_responses(db, [interview])[0]


@router.post("", response_model=InterviewResponse, status_code=201)
def create_interview(interview_data: InterviewCreate, db: Session = Depends(get_db)):
    """
    Create (schedule) a new interview.

    The CV is optional here: for cv_driven/hybrid it can be supplied now (interviewer) or
    uploaded later (candidate), but /start refuses to run without one.

    Args:
        interview_data: The interview details including mode and optional CV text

    Returns:
        The created interview

    Raises:
        HTTPException: 400 if candidate, job not found, or invalid mode
    """
    candidate = db.query(Candidate).filter(Candidate.id == interview_data.candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=400, detail="Candidate not found")

    job = db.query(JobDescription).filter(JobDescription.id == interview_data.job_description_id).first()
    if not job:
        raise HTTPException(status_code=400, detail="Job description not found")

    # Validate mode
    valid_modes = ["question_bank", "cv_driven", "hybrid"]
    if interview_data.mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Must be one of: {', '.join(valid_modes)}")

    values = interview_data.model_dump()
    if not (values.get("cv_text") or "").strip():
        values["cv_text"] = None
    # CV-based modes use the candidate's saved CV unless the interviewer supplied a replacement for this interview.
    # The text is copied (a snapshot), so later changes to the saved CV never alter this interview.
    if values["cv_text"] is None and interview_data.mode in ("cv_driven", "hybrid") and candidate.has_cv:
        values["cv_text"] = candidate.cv_text
    # Status only ever changes through real progress (start, complete); a client cannot create a started/completed interview
    values.update(status="not_started", started_at=None, completed_at=None)

    interview = Interview(**values)
    db.add(interview)
    db.commit()
    db.refresh(interview)
    logger.info(f"Created interview {interview.id} with mode: {interview.mode}")
    if interview.cv_text:
        record_security_check(db, interview.id, SOURCE_CV, CV_SOURCE_ID, interview.cv_text)
    return _build_interview_responses(db, [interview])[0]


@router.post("/{interview_id}/start", response_model=InterviewStartResponse)
def start_interview(interview_id: int, db: Session = Depends(get_db)):
    """
    Start an interview and assign questions based on interview mode.

    Modes:
    - question_bank: Select from existing question bank
    - cv_driven: Generate questions based on candidate's CV
    - hybrid: Mix of question bank and CV-driven questions

    Args:
        interview_id: The ID of the interview to start

    Returns:
        The started interview with assigned questions

    Raises:
        HTTPException: 404 if interview not found
        HTTPException: 400 if interview already started, completed, no questions available, or CV extraction fails
        HTTPException: 500 if LLM question generation fails
    """
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    if interview.status == "in_progress":
        raise HTTPException(status_code=400, detail="Interview has already been started")

    if interview.status == "completed":
        raise HTTPException(status_code=400, detail="Interview has already been completed")

    if interview.status == "abandoned":
        raise HTTPException(status_code=400, detail="This interview was ended by the candidate and cannot be restarted")

    require_consent(interview)

    if interview.mode in CV_MODES and not (interview.cv_text and interview.cv_text.strip()):
        saved = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
        if saved is not None and saved.has_cv:     # scheduled before the candidate's CV was saved: use it now, no re-upload
            interview.cv_text = saved.cv_text
            db.commit()
            record_security_check(db, interview.id, SOURCE_CV, CV_SOURCE_ID, interview.cv_text)

    if interview.mode in CV_MODES and not (interview.cv_text and interview.cv_text.strip()):
        mode_label = "CV Driven" if interview.mode == "cv_driven" else "Hybrid"
        raise HTTPException(
            status_code=400,
            detail=f"{mode_label} interviews require a CV. Upload a CV before starting the interview."
        )

    logger.info(f"Starting interview {interview_id} with mode: {interview.mode}")

    # The job role is a hard constraint for every question in this interview
    job = db.query(JobDescription).filter(JobDescription.id == interview.job_description_id).first()
    if not job:
        raise HTTPException(status_code=400, detail="Job description not found")

    # Questions generated below belong to THIS interview (source_interview_id) and this role only
    try:
        if interview.mode == "question_bank":
            pass  # role-specific bank questions are selected below

        elif interview.mode == "cv_driven":
            from app.services.cv_processor import generate_cv_driven_questions

            try:
                # Pass interview_id and db for usage logging
                generated_questions = generate_cv_driven_questions(
                    interview.cv_text,
                    job.description,
                    job.required_skills,
                    count=MAIN_QUESTION_COUNT,
                    interview_id=interview.id,
                    db=db,
                    job_title=job.title,
                )

                # Create Question records for generated questions
                for gen_q in generated_questions:
                    db.add(Question(
                        question=gen_q["text"],
                        category="cv_generated",
                        difficulty=gen_q["difficulty"],
                        job_type=job.title,
                        is_active=True,
                        source_interview_id=interview.id,
                    ))
                db.flush()
                logger.info(f"Created {len(generated_questions)} CV-generated questions")

            except Exception as e:
                logger.error(f"Failed to generate CV questions: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Failed to generate questions from CV: {str(e)}")

        elif interview.mode == "hybrid":
            from app.services.cv_processor import generate_hybrid_questions

            try:
                # generate_hybrid_questions calls generate_cv_driven_questions internally
                # which will log as "hybrid_question_generation" due to operation parameter
                mixed_questions = generate_hybrid_questions(
                    db,
                    interview.cv_text,
                    job.description,
                    job.required_skills,
                    question_bank_count=2,
                    cv_driven_count=2,
                    job_skill_count=1,
                    interview_id=interview.id,
                    job_title=job.title,
                    job_description_id=job.id,
                )

                # Create Question records for generated questions; role bank questions already exist
                for q in mixed_questions:
                    if isinstance(q, dict):  # Generated question data
                        db.add(Question(
                            question=q["text"],
                            category="hybrid_generated",
                            difficulty=q.get("difficulty", "medium"),
                            job_type=job.title,
                            is_active=True,
                            source_interview_id=interview.id,
                        ))
                db.flush()
                logger.info(f"Created hybrid generated questions for interview {interview.id}")

            except Exception as e:
                logger.error(f"Failed to generate hybrid questions: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Failed to generate hybrid questions: {str(e)}")

        else:
            raise HTTPException(status_code=400, detail=f"Invalid interview mode: {interview.mode}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error selecting questions: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to select interview questions")

    # Main question 1: foundational (easy), from this role's pool for this mode
    first_question = select_question_bank_adaptive(
        db, interview.id, difficulty=target_main_difficulty(1, []), exclude_question_ids=[], mode=interview.mode
    )
    if not first_question:
        raise HTTPException(status_code=400, detail=f"No questions available for the {job.title} role")

    interview.status = "in_progress"
    interview.started_at = datetime.utcnow()
    db.add(interview)
    db.flush()

    # Phase 5: Create ONLY the first question for adaptive flow
    first_interview_question = InterviewQuestion(
        interview_id=interview.id,
        question_id=first_question.id,
        question_order=1
    )
    db.add(first_interview_question)

    db.commit()
    db.refresh(interview)
    db.refresh(first_interview_question)

    return InterviewStartResponse(
        id=interview.id,
        candidate_id=interview.candidate_id,
        job_description_id=interview.job_description_id,
        mode=interview.mode,
        status=interview.status,
        created_at=interview.created_at,
        started_at=interview.started_at,
        completed_at=interview.completed_at,
        current_question=InterviewQuestionBrief(
            id=first_interview_question.id,
            question_id=first_interview_question.question_id,
            question_order=first_interview_question.question_order
        ),
        current_index=0,
        total_questions=5,
        questions=[]  # Legacy field, empty for adaptive flow
    )


@router.post("/{interview_id}/questions/{interview_question_id}/answer", response_model=AnswerResponse)
def submit_answer(interview_id: int, interview_question_id: int, submission: AnswerSubmission, response: Response, db: Session = Depends(get_db)):
    """See _submit_answer. Runs under the interview-state lock so an answer cannot slip in while the interview is being abandoned."""
    with keyed_lock("interview-state", interview_id):
        db.expire_all()   # re-read the status: another request may have changed it while this one waited
        return _submit_answer(interview_id, interview_question_id, submission, response, db)


def _submit_answer(interview_id: int, interview_question_id: int, submission: AnswerSubmission, response: Response, db: Session):
    """
    Submit an answer to an interview question (idempotent).

    If the answer already exists for this question, returns the existing answer with HTTP 200.
    If the answer is new, creates it and returns with HTTP 201.

    This makes the endpoint safe for retries: if answer submission succeeds but downstream
    steps (evaluation, next-question) fail, the frontend can retry without getting a duplicate
    submission error.

    Args:
        interview_id: The ID of the interview
        interview_question_id: The ID of the interview question
        submission: The answer text

    Returns:
        The answer (existing or newly created)

    Raises:
        HTTPException: 404 if interview or interview question not found
        HTTPException: 400 if interview not in progress or answer text is empty
    """
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    if interview.status != "in_progress":
        raise HTTPException(status_code=400, detail="Interview is not in progress")

    require_consent(interview)

    interview_question = db.query(InterviewQuestion).filter(InterviewQuestion.id == interview_question_id).first()
    if not interview_question:
        raise HTTPException(status_code=404, detail="Interview question not found")

    if interview_question.interview_id != interview_id:
        raise HTTPException(status_code=400, detail="Interview question does not belong to this interview")

    answer_text = submission.answer_text.strip()
    if not answer_text:
        raise HTTPException(status_code=400, detail="Answer text cannot be empty")

    # Check if answer already exists (idempotent: return existing instead of error)
    existing_answer = db.query(Answer).filter(Answer.interview_question_id == interview_question_id).first()
    if existing_answer:
        logger.debug(f"Answer {existing_answer.id} already exists for question {interview_question_id}, returning existing")
        response.status_code = 200
        return existing_answer

    # Create new answer
    answer = Answer(
        interview_question_id=interview_question_id,
        answer_text=answer_text
    )
    db.add(answer)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request saved this question's answer first (unique index); keep that one
        db.rollback()
        existing_answer = db.query(Answer).filter(Answer.interview_question_id == interview_question_id).first()
        if not existing_answer:
            raise
        response.status_code = 200
        return existing_answer
    db.refresh(answer)
    logger.info(f"Created new answer {answer.id} for question {interview_question_id}")
    # Prompt-injection check, recorded for the interviewer. It never blocks or alters the answer or its evaluation.
    record_security_check(db, interview_id, SOURCE_ANSWER, answer.id, answer_text)
    response.status_code = 201
    return answer


def _pending_question_response(steps: list[InterviewStep]) -> NextQuestionResponse:
    """The last asked question, still waiting for its answer, as a next-question response."""
    last = steps[-1]
    main_number, follow_up_number = number_steps(steps)[-1]
    return NextQuestionResponse(
        id=last.interview_question.id,
        question_id=last.question.id,
        question=last.question.question,
        current_index=len(steps) - 1,
        total_questions=MAIN_QUESTION_COUNT,
        is_follow_up=last.is_follow_up,
        follow_up_reason=None,
        difficulty_adjusted=None,
        is_complete=False,
        main_question_number=main_number,
        follow_up_number=follow_up_number,
        max_follow_ups=MAX_FOLLOW_UPS,
    )


def _completion_response(steps: list[InterviewStep], main_asked: int) -> NextQuestionResponse:
    return NextQuestionResponse(
        id=-1,
        question_id=-1,
        question=None,
        current_index=len(steps),
        total_questions=MAIN_QUESTION_COUNT,
        is_follow_up=False,
        follow_up_reason=None,
        difficulty_adjusted=None,
        is_complete=True,
        main_question_number=main_asked,
        follow_up_number=0,
        max_follow_ups=MAX_FOLLOW_UPS,
    )


@router.post("/{interview_id}/next-question", response_model=NextQuestionResponse)
def get_next_question(interview_id: int, db: Session = Depends(get_db)):
    """
    Decide and return what to ask next: a probing follow-up or the next main question.

    After the candidate's last answer has been evaluated:
    1. If the evaluator flagged it as vague, incomplete, incorrect or contradicting the CV (or the score is
       too low to move on) and fewer than MAX_FOLLOW_UPS follow-ups were asked for this main question,
       generate a contextual follow-up. The limit is a hard cap; the count is derived from persisted rows.
    2. Otherwise, move to the next MAIN question: role-specific, not asked before, at a difficulty that rises
       from easy to hard and adapts to how the candidate is doing. Follow-ups never change that progression.
    3. After MAIN_QUESTION_COUNT main questions (and no pending follow-up) the interview is complete.

    Calling this again while the last question is still unanswered returns that same question, so a retry
    after a lost response is safe.

    Args:
        interview_id: The ID of the interview

    Returns:
        NextQuestionResponse with the next question or is_complete flag

    Raises:
        HTTPException: 404 if interview not found
        HTTPException: 400 if interview state is invalid
    """
    # One decision at a time per interview: a repeated or raced request waits and then sees the first one's result
    with keyed_lock("next-question", interview_id):
        return _decide_next_question(interview_id, db)


def _decide_next_question(interview_id: int, db: Session) -> NextQuestionResponse:
    logger.info(f"Fetching next question for interview {interview_id}")

    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    if interview.status != "in_progress":
        raise HTTPException(status_code=400, detail="Interview is not in progress")

    require_consent(interview)

    interview_questions = db.query(InterviewQuestion).filter(
        InterviewQuestion.interview_id == interview_id
    ).order_by(InterviewQuestion.question_order).all()

    if not interview_questions:
        raise HTTPException(status_code=400, detail="No questions have been answered yet")

    steps = load_interview_steps(db, interview_questions)
    last = steps[-1]
    main_asked = sum(1 for step in steps if not step.is_follow_up)
    follow_ups_asked = trailing_follow_ups(steps)
    logger.info(
        f"Interview {interview_id} has {len(steps)} questions ({main_asked} main); "
        f"{follow_ups_asked} follow-up(s) so far for the current main question"
    )

    # Retry safety: the previous call already created the next question and it has not been answered yet
    if last.answer is None:
        return _pending_question_response(steps)

    if last.evaluation is None:
        raise HTTPException(status_code=400, detail="Last answer has not been evaluated")
    evaluation = last.evaluation
    logger.info(f"Last evaluation score: {evaluation.score}")

    job = db.query(JobDescription).filter(JobDescription.id == interview.job_description_id).first()
    job_title = job.title if job else None
    cv_context = interview.cv_text if interview.mode in CV_MODES else None

    # ---- 1. Probe before moving on? ----
    decision = decide_follow_up(evaluation, follow_ups_asked, interview.mode)

    if decision["should_follow_up"]:
        logger.info(f"Follow-up needed ({decision['reason']}); this would be follow-up {follow_ups_asked + 1} of {MAX_FOLLOW_UPS}")

        from app.services.follow_up_generator import generate_follow_up_question

        chain_start = len(steps) - 1 - follow_ups_asked  # index of the current main question
        main_question = steps[chain_start].question
        follow_up = generate_follow_up_question(
            original_question=last.question.question,
            candidate_answer=last.answer.answer_text,
            weaknesses=evaluation.weaknesses,
            feedback=evaluation.feedback,
            cv_context=cv_context,
            job_context=job.description if job else None,
            interview_id=interview_id,
            db=db,
            job_title=job_title,
            follow_up_reason=decision["reason"],
            missing_information=decision["missing_information"],
            follow_up_number=follow_ups_asked + 1,
            main_question=main_question.question,
            previous_exchanges=[
                (step.question.question, step.answer.answer_text)
                for step in steps[chain_start + 1:-1] if step.answer is not None
            ],
        )

        if follow_up and follow_up.get("is_valid"):
            follow_up_question = Question(
                question=follow_up["question_text"],
                category=FOLLOW_UP_CATEGORY,
                difficulty=follow_up["difficulty"],
                job_type=job_title or interview.mode,
                is_active=True,
                source_interview_id=interview.id,
            )
            db.add(follow_up_question)
            db.flush()

            follow_up_iq = InterviewQuestion(
                interview_id=interview_id,
                question_id=follow_up_question.id,
                question_order=len(steps) + 1,
            )
            db.add(follow_up_iq)
            try:
                db.commit()
            except IntegrityError:
                # Another request already created this position (unique index): return what is pending now
                db.rollback()
                return _pending_question_response(load_steps_for_interview(db, interview_id))
            db.refresh(follow_up_iq)
            logger.info(f"Created follow-up InterviewQuestion {follow_up_iq.id} (follow-up {follow_ups_asked + 1})")

            return NextQuestionResponse(
                id=follow_up_iq.id,
                question_id=follow_up_question.id,
                question=follow_up_question.question,
                current_index=len(steps),
                total_questions=MAIN_QUESTION_COUNT,
                is_follow_up=True,
                follow_up_reason=decision["reason"],
                difficulty_adjusted=None,
                is_complete=False,
                main_question_number=main_asked,
                follow_up_number=follow_ups_asked + 1,
                max_follow_ups=MAX_FOLLOW_UPS,
            )

        logger.warning("Follow-up generation failed or was invalid; moving on to the next main question")

    # ---- 2. Next main question (or the end) ----
    if main_asked >= MAIN_QUESTION_COUNT:
        logger.info(f"Interview {interview_id} has asked all {MAIN_QUESTION_COUNT} main questions")
        return _completion_response(steps, main_asked)

    next_main_number = main_asked + 1
    previous_main_scores = main_question_scores(steps)
    target_difficulty = target_main_difficulty(next_main_number, previous_main_scores)

    selected_question = select_question_bank_adaptive(
        db,
        interview_id,
        difficulty=target_difficulty,
        exclude_question_ids=[step.question.id for step in steps],
        mode=interview.mode,
        asked_categories={step.question.category for step in steps if not step.is_follow_up},
    )

    if not selected_question:
        logger.warning(f"No suitable question available for interview {interview_id} (role '{job_title}')")
        return _completion_response(steps, main_asked)

    last_main = next(step for step in reversed(steps) if not step.is_follow_up)
    previous_rank = difficulty_rank(last_main.question.difficulty)
    selected_rank = difficulty_rank(selected_question.difficulty)
    difficulty_adjusted = "increased" if selected_rank > previous_rank else "decreased" if selected_rank < previous_rank else "same"

    next_interview_question = InterviewQuestion(
        interview_id=interview_id,
        question_id=selected_question.id,
        question_order=len(steps) + 1,
    )
    db.add(next_interview_question)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _pending_question_response(load_steps_for_interview(db, interview_id))
    db.refresh(next_interview_question)

    logger.info(
        f"Created InterviewQuestion {next_interview_question.id} for interview {interview_id}: main question "
        f"{next_main_number}, target difficulty {target_difficulty}, question {selected_question.id} "
        f"({selected_question.difficulty})"
    )

    return NextQuestionResponse(
        id=next_interview_question.id,
        question_id=selected_question.id,
        question=selected_question.question,
        current_index=len(steps),
        total_questions=MAIN_QUESTION_COUNT,
        is_follow_up=False,
        follow_up_reason=None,
        difficulty_adjusted=difficulty_adjusted,
        is_complete=False,
        main_question_number=next_main_number,
        follow_up_number=0,
        max_follow_ups=MAX_FOLLOW_UPS,
    )


@router.post("/{interview_id}/consent", response_model=ConsentResponse)
def record_consent(interview_id: int, consent: ConsentRequest, db: Session = Depends(get_db)):
    """
    Record the candidate's decision on the data-handling notice (granted or declined), with a timestamp.

    Declining never starts, completes or reviews anything; the interview simply cannot proceed until consent is
    granted. Only the latest decision is kept. The notice version sent must match the current notice, so a
    decision is never recorded against wording the candidate did not see.

    Raises:
        HTTPException: 404 if the interview does not exist
        HTTPException: 400 if the interview is already completed
        HTTPException: 409 if the notice version is out of date
    """
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.status in ("completed", "abandoned"):
        raise HTTPException(status_code=400, detail=f"Consent cannot be changed for a {interview.status} interview")
    if consent.notice_version != CONSENT_NOTICE_VERSION:
        raise HTTPException(status_code=409, detail="The consent notice has changed. Reload the page and read the current notice.")

    interview.consent_status = "granted" if consent.granted else "declined"
    interview.consent_recorded_at = datetime.utcnow()
    interview.consent_notice_version = consent.notice_version
    db.commit()
    db.refresh(interview)
    logger.info(f"Consent {interview.consent_status} recorded for interview {interview_id}")
    return ConsentResponse(
        interview_id=interview.id,
        status=interview.status,
        consent_status=interview.consent_status,
        consent_recorded_at=interview.consent_recorded_at,
        consent_notice_version=interview.consent_notice_version,
    )


def _session_step(step: InterviewStep, numbers: tuple, is_last: bool) -> SessionStep:
    return SessionStep(
        interview_question_id=step.interview_question.id,
        question_id=step.question.id,
        order=step.interview_question.question_order,
        question=step.question.question,
        is_follow_up=step.is_follow_up,
        main_question_number=numbers[0],
        follow_up_number=numbers[1],
        answer_id=step.answer.id if step.answer else None,
        answer=step.answer.answer_text if step.answer else None,
        state=step_state(step, is_last),
    )


@router.get("/{interview_id}/session", response_model=InterviewSessionResponse)
def get_interview_session(interview_id: int, db: Session = Depends(get_db)):
    """
    The persisted state of an interview, so an interrupted candidate can resume exactly where they stopped.

    Read-only. `next_action` says what a client must do: show the `pending` question, run the evaluation of
    an answer that was saved but not evaluated, or ask `/next-question` for what comes next. Every one of
    those operations is safe to repeat, so a failed request in the middle of a step is recoverable.

    Raises:
        HTTPException: 404 if the interview does not exist
        HTTPException: 400 if it has not been started
    """
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.status == "not_started":
        raise HTTPException(status_code=400, detail="Interview has not been started")

    steps = load_steps_for_interview(db, interview_id)
    if not steps:
        raise HTTPException(status_code=400, detail="Interview has no questions")

    numbers = number_steps(steps)
    session_steps = [_session_step(step, numbers[i], i == len(steps) - 1) for i, step in enumerate(steps)]
    last = steps[-1]
    action = ACTION_COMPLETE if interview.status == "completed" else next_action(steps)
    candidate = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
    job = db.query(JobDescription).filter(JobDescription.id == interview.job_description_id).first()
    has_review = db.query(HumanReview.id).filter(HumanReview.interview_id == interview_id).first() is not None

    return InterviewSessionResponse(
        interview_id=interview.id,
        status=interview.status,
        display_status=compute_display_status(interview.status, has_review),
        mode=interview.mode,
        candidate_id=interview.candidate_id,
        job_description_id=interview.job_description_id,
        candidate_name=candidate.name if candidate else None,
        job_title=job.title if job else None,
        total_questions=MAIN_QUESTION_COUNT,
        max_follow_ups=MAX_FOLLOW_UPS,
        consent_status=interview.consent_status,
        main_question_number=numbers[-1][0],
        follow_up_number=numbers[-1][1],
        main_questions_answered=sum(1 for step in steps if not step.is_follow_up and step.answer is not None),
        next_action=action,
        last_answer_id=last.answer.id if last.answer else None,
        pending=session_steps[-1] if last.answer is None and interview.status == "in_progress" else None,
        steps=session_steps,
    )


def _progress_step(step: InterviewStep, numbers: tuple, is_last: bool) -> ProgressStep:
    evaluation = step.evaluation
    dimensions = None
    if evaluation is not None and evaluation.dimensions:
        try:
            dimensions = DimensionEvaluation(**json.loads(evaluation.dimensions))
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(f"Evaluation {evaluation.id} has malformed dimensions JSON: {e}")
    return ProgressStep(
        interview_question_id=step.interview_question.id,
        order=step.interview_question.question_order,
        question=step.question.question,
        is_follow_up=step.is_follow_up,
        follow_up_number=numbers[1],
        state=step_state(step, is_last),
        asked_at=step.interview_question.created_at,
        answer=step.answer.answer_text if step.answer else None,
        answered_at=step.answer.created_at if step.answer else None,
        evaluation=dimensions,
        score=evaluation.score if evaluation is not None else None,
        follow_up_reason=evaluation.follow_up_reason if evaluation is not None else None,
        missing_information=evaluation.missing_information if evaluation is not None else None,
        evaluated_at=evaluation.created_at if evaluation is not None else None,
    )


@router.get("/{interview_id}/progress", response_model=InterviewProgressResponse)
def get_interview_progress(interview_id: int, db: Session = Depends(get_db)):
    """
    Read-only progress of an interview, for the interviewer: questions asked so far grouped by main question,
    the answers submitted, and the evaluations that exist. Only persisted data is returned.

    Viewing progress never changes the interview or creates a review: the final Human Review stays a
    separate step that is only available once the interview is Completed.

    Raises:
        HTTPException: 404 if the interview does not exist
        HTTPException: 400 if it has not been started
    """
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.status == "not_started":
        raise HTTPException(status_code=400, detail="Interview has not been started")

    steps = load_steps_for_interview(db, interview_id)
    numbers = number_steps(steps)
    progress_steps = [_progress_step(step, numbers[i], i == len(steps) - 1) for i, step in enumerate(steps)]

    groups: list[ProgressMainQuestion] = []
    for step, numbers_for_step, progress_step in zip(steps, numbers, progress_steps):
        if not step.is_follow_up:
            groups.append(ProgressMainQuestion(
                main_question_number=numbers_for_step[0], question=progress_step, follow_ups=[],
                follow_up_count=0, max_follow_ups=MAX_FOLLOW_UPS,
            ))
        elif groups:
            groups[-1].follow_ups.append(progress_step)
            groups[-1].follow_up_count += 1

    candidate = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
    job = db.query(JobDescription).filter(JobDescription.id == interview.job_description_id).first()
    has_review = db.query(HumanReview.id).filter(HumanReview.interview_id == interview_id).first() is not None

    return InterviewProgressResponse(
        interview={
            "id": interview.id,
            "candidate_name": candidate.name if candidate else "Unknown",
            "job_title": job.title if job else "Unknown",
            "mode": interview.mode,
            "status": interview.status,
            "display_status": compute_display_status(interview.status, has_review),
            "created_at": interview.created_at,
            "started_at": interview.started_at,
            "completed_at": interview.completed_at,
        },
        total_questions=MAIN_QUESTION_COUNT,
        max_follow_ups=MAX_FOLLOW_UPS,
        main_questions_asked=len(groups),
        main_questions_answered=sum(1 for g in groups if g.question.answer is not None),
        follow_ups_asked=sum(g.follow_up_count for g in groups),
        answers_submitted=sum(1 for s in steps if s.answer is not None),
        answers_evaluated=sum(1 for s in steps if s.evaluation is not None),
        pending_question=next((p for p in progress_steps if p.state == "pending"), None),
        last_activity_at=last_activity(interview, steps),
        main_questions=groups,
    )


@router.post("/{interview_id}/upload-cv", status_code=200)
def upload_cv(interview_id: int, file: UploadFile = File(...), save_to_candidate: bool = False, db: Session = Depends(get_db)):
    """
    Upload and extract CV/resume text for CV-driven or hybrid interviews.

    The CV is used for THIS interview only (it replaces the CV the interview was scheduled with). The candidate's saved CV is
    changed only when `save_to_candidate=true` is passed explicitly, and then only for this interview's own candidate.

    Args:
        interview_id: The ID of the interview
        file: PDF file uploaded by user

    Returns:
        Confirmation with extracted text preview

    Raises:
        HTTPException: 404 if interview not found
        HTTPException: 400 if file is not PDF or CV mode not selected
        HTTPException: 400 if CV text extraction fails or is too short
    """
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    if interview.status != "not_started":
        raise HTTPException(status_code=400, detail="A CV can only be uploaded before the interview starts")

    # Validate mode supports CV
    if interview.mode not in CV_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"CV upload only allowed for cv_driven or hybrid modes. Current mode: {interview.mode}"
        )

    # Validate file type
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    logger.info(f"Processing CV upload for interview {interview_id}")

    try:
        # Read file bytes
        file_bytes = file.file.read()

        # Extract text from PDF
        from app.services.cv_processor import extract_text_from_pdf, validate_cv_text

        try:
            cv_text = extract_text_from_pdf(file_bytes)
        except Exception as e:     # an empty, corrupt or non-PDF upload is the uploader's problem, not a server fault
            logger.warning(f"Unreadable CV for interview {interview_id}: {type(e).__name__}")
            raise HTTPException(status_code=400, detail="The file could not be read as a PDF. Please upload a valid, text-based PDF.")

        # Validate extracted text
        is_valid, error_msg = validate_cv_text(cv_text)
        if not is_valid:
            logger.warning(f"Invalid CV text for interview {interview_id}: {error_msg}")
            raise HTTPException(status_code=400, detail=error_msg)

        # Store CV text in interview
        interview.cv_text = cv_text
        db.add(interview)
        db.commit()
        db.refresh(interview)
        record_security_check(db, interview_id, SOURCE_CV, CV_SOURCE_ID, cv_text)

        saved_to_candidate = False
        if save_to_candidate:
            from app.services.candidate_cv import save_candidate_cv
            owner = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
            if owner is not None:
                save_candidate_cv(db, owner, cv_text, file.filename)
                db.commit()
                saved_to_candidate = True

        logger.info(f"Successfully extracted CV text for interview {interview_id}: {len(cv_text)} characters")

        return {
            "interview_id": interview_id,
            "saved_to_candidate": saved_to_candidate,
            "cv_text_length": len(cv_text),
            "status": "success",
            "preview": cv_text[:200] + "..." if len(cv_text) > 200 else cv_text
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing CV upload: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process CV: {str(e)}")


@router.post("/{interview_id}/abandon", response_model=InterviewResponse)
def abandon_interview(interview_id: int, db: Session = Depends(get_db)):
    """
    The candidate ends an in-progress interview for good ("Abandoned"). It cannot be resumed or answered again.

    Idempotent: repeating the request returns the interview unchanged and keeps the first abandonment time.
    Everything saved (answers, evaluations, questions, consent, security checks) is kept for the interviewer.
    The status change is a single conditional UPDATE, so a racing completion or a duplicate request cannot corrupt it.

    Raises:
        HTTPException: 404 if the interview does not exist
        HTTPException: 400 if it was never started (a Scheduled interview stays Scheduled) or is already completed
    """
    with keyed_lock("next-question", interview_id), keyed_lock("interview-state", interview_id):
        db.expire_all()
        interview = db.query(Interview).filter(Interview.id == interview_id).first()
        if not interview:
            raise HTTPException(status_code=404, detail="Interview not found")
        if interview.status == "abandoned":
            return _build_interview_responses(db, [interview])[0]
        if interview.status == "completed":
            raise HTTPException(status_code=400, detail="A completed interview cannot be abandoned")
        if interview.status != "in_progress":
            raise HTTPException(status_code=400, detail="This interview has not started, so there is nothing to end")
        changed = db.query(Interview).filter(Interview.id == interview_id, Interview.status == "in_progress").update(
            {Interview.status: "abandoned", Interview.abandoned_at: datetime.utcnow()}, synchronize_session=False)
        db.commit()
        db.expire_all()
        interview = db.query(Interview).filter(Interview.id == interview_id).first()
        if changed == 0 and interview.status != "abandoned":
            raise HTTPException(status_code=409, detail="The interview changed state while ending it; please check its status")
        logger.info(f"Interview {interview_id} abandoned by the candidate")
        return _build_interview_responses(db, [interview])[0]


@router.post("/{interview_id}/complete", response_model=InterviewCompletionResponse, status_code=200)
def complete_interview(interview_id: int, db: Session = Depends(get_db)):
    """See _complete_interview. Serialised with abandonment."""
    with keyed_lock("interview-state", interview_id):
        db.expire_all()
        return _complete_interview(interview_id, db)


def _complete_interview(interview_id: int, db: Session):
    """
    Complete an interview, calculate overall score, and generate a final recommendation.

    Args:
        interview_id: The ID of the interview to complete

    Returns:
        Interview completion details with overall score and recommendation

    Raises:
        HTTPException: 404 if interview not found
        HTTPException: 400 if interview not in progress, or missing answers/evaluations
        HTTPException: 500 if AI recommendation generation fails
    """
    logger.info(f"Starting interview completion for interview {interview_id}")

    # VALIDATION 1: Interview exists
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        logger.warning(f"Interview {interview_id} not found")
        raise HTTPException(status_code=404, detail="Interview not found")

    # VALIDATION 2: Interview must be in progress
    if interview.status == "completed":
        logger.warning(f"Interview {interview_id} already completed")
        raise HTTPException(status_code=400, detail="Interview has already been completed")

    if interview.status != "in_progress":
        logger.warning(f"Interview {interview_id} is not in progress (status: {interview.status})")
        detail = "This interview was ended by the candidate" if interview.status == "abandoned" else "Interview is not in progress"
        raise HTTPException(status_code=400, detail=detail)

    require_consent(interview)

    # VALIDATION 3: All interview questions must have answers
    interview_questions = db.query(InterviewQuestion).filter(
        InterviewQuestion.interview_id == interview_id
    ).all()

    logger.debug(f"Interview {interview_id} has {len(interview_questions)} questions")

    answers = []
    for iq in interview_questions:
        answer = db.query(Answer).filter(Answer.interview_question_id == iq.id).first()
        if not answer:
            logger.warning(f"Interview {interview_id}: missing answer for question {iq.id}")
            raise HTTPException(status_code=400, detail="Not all questions have been answered")
        answers.append(answer)

    logger.debug(f"All {len(answers)} questions have answers")

    # VALIDATION 4: All answers must have evaluations
    evaluations = []
    for answer in answers:
        evaluation = db.query(Evaluation).filter(Evaluation.answer_id == answer.id).first()
        if not evaluation:
            logger.warning(f"Interview {interview_id}: missing evaluation for answer {answer.id}")
            raise HTTPException(status_code=400, detail="Not all answers have been evaluated")
        evaluations.append(evaluation)

    logger.debug(f"All {len(evaluations)} answers have evaluations")

    # VALIDATION 5: an interview is only Completed once all required main questions have been asked and answered
    steps = load_interview_steps(db, interview_questions)
    main_asked = sum(1 for step in steps if not step.is_follow_up)
    if main_asked < MAIN_QUESTION_COUNT:
        more_available = select_question_bank_adaptive(
            db, interview_id, difficulty="medium",
            exclude_question_ids=[step.question.id for step in steps], mode=interview.mode,
        )
        if more_available is not None:  # (if the role's questions ran out, the interview legitimately ends early)
            raise HTTPException(
                status_code=400,
                detail=f"Interview is not finished: {main_asked} of {MAIN_QUESTION_COUNT} main questions asked",
            )

    # CALCULATE OVERALL SCORE (deterministic, backend only)
    scores = [e.score for e in evaluations]
    overall_score = int(sum(scores) / len(scores) + 0.5)  # Explicit .5-up rounding
    logger.info(f"Calculated overall score for interview {interview_id}: {overall_score}")

    # CALCULATE AVERAGE CONFIDENCE
    confidences = [e.confidence for e in evaluations]
    average_confidence = sum(confidences) / len(confidences)
    logger.debug(f"Average confidence: {average_confidence}")

    # BUILD EVALUATIONS SUMMARY FOR OPENAI PROMPT
    summary_parts = []
    for i, (iq, answer, evaluation) in enumerate(zip(interview_questions, answers, evaluations), 1):
        question = db.query(Question).filter(Question.id == iq.question_id).first()
        summary_parts.append(f"Question {i}: {question.question}")
        summary_parts.append(f"Strengths: {evaluation.strengths}")
        summary_parts.append(f"Weaknesses: {evaluation.weaknesses}")
        summary_parts.append(f"Feedback: {evaluation.feedback}")
        summary_parts.append("")

    evaluations_summary = "\n".join(summary_parts)

    # GET JOB TITLE FOR CONTEXT
    job = db.query(JobDescription).filter(JobDescription.id == interview.job_description_id).first()
    job_title = job.title if job else "Unknown Position"

    # GENERATE RECOMMENDATION USING OPENAI
    try:
        logger.info(f"Calling OpenAI to generate recommendation for interview {interview_id}")
        # Pass interview_id and db for usage logging
        recommendation_result = generate_recommendation(overall_score, evaluations_summary, job_title, interview_id=interview_id, db=db)
        logger.info(f"OpenAI recommendation: {recommendation_result['recommendation']}")
    except Exception as e:
        logger.error(f"Error generating recommendation for interview {interview_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error generating recommendation with AI service")

    # CREATE RECOMMENDATION RECORD
    try:
        recommendation = Recommendation(
            interview_id=interview_id,
            recommendation=recommendation_result["recommendation"],
            confidence=recommendation_result.get("confidence", "Medium"),
            reason=recommendation_result["reason"]
        )
        db.add(recommendation)
        logger.debug(f"Created recommendation record for interview {interview_id}")
    except Exception as e:
        logger.error(f"Error creating recommendation record for interview {interview_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating recommendation")

    # COMPLETE THE INTERVIEW (transaction safety: create recommendation first, then update interview)
    interview.status = "completed"
    interview.completed_at = datetime.utcnow()
    db.add(interview)

    # SINGLE COMMIT FOR BOTH RECOMMENDATION AND INTERVIEW UPDATE
    try:
        db.commit()
        db.refresh(interview)
        db.refresh(recommendation)
        logger.info(f"Successfully completed interview {interview_id}")
    except Exception as e:
        db.rollback()
        logger.error(f"Error committing interview completion for interview {interview_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error completing interview")

    # RETURN COMPLETION RESPONSE
    return InterviewCompletionResponse(
        id=interview.id,
        candidate_id=interview.candidate_id,
        job_description_id=interview.job_description_id,
        status=interview.status,
        created_at=interview.created_at,
        started_at=interview.started_at,
        completed_at=interview.completed_at,
        overall_score=overall_score,
        evaluation_count=len(evaluations),
        average_confidence=average_confidence,
        recommendation=recommendation.recommendation,
        recommendation_reason=recommendation.reason,
        recommendation_confidence=recommendation.confidence
    )
