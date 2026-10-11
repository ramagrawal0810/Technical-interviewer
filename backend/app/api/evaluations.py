"""Evaluation API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Evaluation, Answer
from app.schemas import EvaluationResponse, EvaluationCreate

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


@router.get("", response_model=list[EvaluationResponse])
def get_all_evaluations(db: Session = Depends(get_db)):
    """
    Get all evaluations.

    Returns a list of all answer evaluations.
    """
    evaluations = db.query(Evaluation).all()
    return evaluations


@router.get("/{evaluation_id}", response_model=EvaluationResponse)
def get_evaluation(evaluation_id: int, db: Session = Depends(get_db)):
    """
    Get a specific evaluation by ID.

    Args:
        evaluation_id: The ID of the evaluation to retrieve

    Returns:
        The evaluation details

    Raises:
        HTTPException: 404 if evaluation not found
    """
    evaluation = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return evaluation


@router.post("", response_model=EvaluationResponse, status_code=201)
def create_evaluation(eval_data: EvaluationCreate, db: Session = Depends(get_db)):
    """
    Create a new evaluation.

    Args:
        eval_data: The evaluation details

    Returns:
        The created evaluation

    Raises:
        HTTPException: 400 if answer not found
    """
    answer = db.query(Answer).filter(Answer.id == eval_data.answer_id).first()
    if not answer:
        raise HTTPException(status_code=400, detail="Answer not found")

    evaluation = Evaluation(**eval_data.model_dump())
    db.add(evaluation)
    db.commit()
    db.refresh(evaluation)
    return evaluation
