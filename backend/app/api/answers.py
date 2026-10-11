"""Answer API endpoints."""

import logging
import traceback
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Answer, InterviewQuestion, Question, Evaluation, Interview, JobDescription
from app.schemas import AnswerResponse, AnswerCreate, EvaluationResponse
from app.services.data_policy import require_consent
from app.services.llm_evaluation import evaluate_answer
from app.services.locks import keyed_lock
from app.services.question_adapter import CV_MODES

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

router = APIRouter(prefix="/api/answers", tags=["answers"])


@router.get("", response_model=list[AnswerResponse])
def get_all_answers(db: Session = Depends(get_db)):
    """
    Get all answers.

    Returns a list of all candidate answers submitted during interviews.
    """
    answers = db.query(Answer).all()
    return answers


@router.get("/{answer_id}", response_model=AnswerResponse)
def get_answer(answer_id: int, db: Session = Depends(get_db)):
    """
    Get a specific answer by ID.

    Args:
        answer_id: The ID of the answer to retrieve

    Returns:
        The answer details

    Raises:
        HTTPException: 404 if answer not found
    """
    answer = db.query(Answer).filter(Answer.id == answer_id).first()
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")
    return answer


@router.post("", response_model=AnswerResponse, status_code=201)
def create_answer(answer_data: AnswerCreate, db: Session = Depends(get_db)):
    """
    Create a new answer.

    Args:
        answer_data: The answer details

    Returns:
        The created answer

    Raises:
        HTTPException: 400 if interview question not found
    """
    interview_question = db.query(InterviewQuestion).filter(InterviewQuestion.id == answer_data.interview_question_id).first()
    if not interview_question:
        raise HTTPException(status_code=400, detail="Interview question not found")

    answer = Answer(**answer_data.model_dump())
    db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer


@router.post("/{answer_id}/evaluate", response_model=EvaluationResponse, status_code=201)
def evaluate_answer_endpoint(answer_id: int, db: Session = Depends(get_db)):
    """
    Evaluate a candidate's answer using AI.

    Args:
        answer_id: The ID of the answer to evaluate

    Returns:
        The created evaluation

    Raises:
        HTTPException: 404 if answer not found
        HTTPException: 400 if answer already evaluated
        HTTPException: 500 if AI evaluation fails
    """
    # One evaluation at a time per answer: a repeated or raced request waits, then sees it already exists
    with keyed_lock("evaluate", answer_id):
        return _evaluate_answer(answer_id, db)


def _evaluate_answer(answer_id: int, db: Session):
    answer = db.query(Answer).filter(Answer.id == answer_id).first()
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")

    existing_evaluation = db.query(Evaluation).filter(Evaluation.answer_id == answer_id).first()
    if existing_evaluation:
        raise HTTPException(status_code=400, detail="Answer has already been evaluated")

    interview_question = db.query(InterviewQuestion).filter(InterviewQuestion.id == answer.interview_question_id).first()
    if not interview_question:
        raise HTTPException(status_code=400, detail="Interview question not found")

    question = db.query(Question).filter(Question.id == interview_question.question_id).first()
    if not question:
        raise HTTPException(status_code=400, detail="Question not found")

    consenting_interview = db.query(Interview).filter(Interview.id == interview_question.interview_id).first()
    if consenting_interview is not None:
        if consenting_interview.status == "abandoned":      # no new AI work on an ended interview; it shows as Not Evaluated
            raise HTTPException(status_code=400, detail="This interview was ended by the candidate")
        require_consent(consenting_interview)

    try:
        logger.info(f"Evaluating answer {answer_id} using AI")
        logger.debug(f"Question text: {question.question[:100]}...")
        logger.debug(f"Answer text: {answer.answer_text[:100]}...")

        # Role and CV context let the evaluator judge the answer for this role and spot CV contradictions
        interview = db.query(Interview).filter(Interview.id == interview_question.interview_id).first()
        job = db.query(JobDescription).filter(JobDescription.id == interview.job_description_id).first() if interview else None
        cv_context = interview.cv_text if interview and interview.mode in CV_MODES else None

        # Pass interview_id and db for usage logging
        # interview_id obtained from: Answer -> InterviewQuestion -> interview_id
        evaluation_result = evaluate_answer(
            question.question,
            answer.answer_text,
            interview_id=interview_question.interview_id,
            db=db,
            cv_context=cv_context,
            job_title=job.title if job else None,
        )
        logger.debug(f"Evaluation result received with dimensions")

        # Serialize dimensions to JSON
        import json
        dimensions_json = json.dumps(evaluation_result["dimensions"])

        evaluation = Evaluation(
            answer_id=answer_id,
            dimensions=dimensions_json,
            score=evaluation_result["score"],
            strengths=evaluation_result["strengths"],
            weaknesses=evaluation_result["weaknesses"],
            feedback=evaluation_result["feedback"],
            confidence=evaluation_result["confidence"],
            # Follow-up decision (absent only from evaluators that predate it, e.g. test doubles)
            needs_follow_up=evaluation_result.get("needs_follow_up"),
            follow_up_reason=evaluation_result.get("follow_up_reason"),
            missing_information=evaluation_result.get("missing_information"),
            contradicts_cv=evaluation_result.get("contradicts_cv"),
        )
        db.add(evaluation)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent request stored this answer's evaluation first (unique index)
            db.rollback()
            raise HTTPException(status_code=400, detail="Answer has already been evaluated")
        db.refresh(evaluation)

        logger.info(f"Successfully created evaluation {evaluation.id} for answer {answer_id}")
        return evaluation

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Evaluation ValueError: {str(e)}")
        logger.error(traceback.format_exc())
        if "API key" in str(e):
            raise HTTPException(status_code=500, detail="AI service not configured")
        # The model answered but its output was malformed; it is never accepted silently
        raise HTTPException(status_code=502, detail=f"AI evaluation returned an invalid response ({e}). Please retry.")
    except Exception as e:
        error_msg = f"Error evaluating answer: {type(e).__name__}: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Error evaluating answer with AI service")
