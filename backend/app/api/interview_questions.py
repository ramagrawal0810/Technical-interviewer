"""Interview Question API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import InterviewQuestion, Interview, Question
from app.schemas import InterviewQuestionResponse, InterviewQuestionCreate

router = APIRouter(prefix="/api/interview-questions", tags=["interview-questions"])


@router.get("", response_model=list[InterviewQuestionResponse])
def get_all_interview_questions(db: Session = Depends(get_db)):
    """
    Get all interview questions.

    Returns a list of all questions assigned to interviews.
    """
    interview_questions = db.query(InterviewQuestion).all()
    return interview_questions


@router.get("/{interview_question_id}", response_model=InterviewQuestionResponse)
def get_interview_question(interview_question_id: int, db: Session = Depends(get_db)):
    """
    Get a specific interview question by ID.

    Args:
        interview_question_id: The ID of the interview question to retrieve

    Returns:
        The interview question details

    Raises:
        HTTPException: 404 if interview question not found
    """
    interview_question = db.query(InterviewQuestion).filter(InterviewQuestion.id == interview_question_id).first()
    if not interview_question:
        raise HTTPException(status_code=404, detail="Interview question not found")
    return interview_question


@router.post("", response_model=InterviewQuestionResponse, status_code=201)
def create_interview_question(iq_data: InterviewQuestionCreate, db: Session = Depends(get_db)):
    """
    Create a new interview question.

    Args:
        iq_data: The interview question details

    Returns:
        The created interview question

    Raises:
        HTTPException: 400 if interview or question not found
    """
    interview = db.query(Interview).filter(Interview.id == iq_data.interview_id).first()
    if not interview:
        raise HTTPException(status_code=400, detail="Interview not found")

    question = db.query(Question).filter(Question.id == iq_data.question_id).first()
    if not question:
        raise HTTPException(status_code=400, detail="Question not found")

    interview_question = InterviewQuestion(**iq_data.model_dump())
    db.add(interview_question)
    db.commit()
    db.refresh(interview_question)
    return interview_question
