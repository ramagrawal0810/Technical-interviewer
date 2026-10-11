"""Recommendation API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Recommendation, Interview
from app.schemas import RecommendationResponse, RecommendationCreate

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get("", response_model=list[RecommendationResponse])
def get_all_recommendations(db: Session = Depends(get_db)):
    """
    Get all recommendations.

    Returns a list of all hiring recommendations.
    """
    recommendations = db.query(Recommendation).all()
    return recommendations


@router.get("/{recommendation_id}", response_model=RecommendationResponse)
def get_recommendation(recommendation_id: int, db: Session = Depends(get_db)):
    """
    Get a specific recommendation by ID.

    Args:
        recommendation_id: The ID of the recommendation to retrieve

    Returns:
        The recommendation details

    Raises:
        HTTPException: 404 if recommendation not found
    """
    recommendation = db.query(Recommendation).filter(Recommendation.id == recommendation_id).first()
    if not recommendation:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return recommendation


@router.post("", response_model=RecommendationResponse, status_code=201)
def create_recommendation(rec_data: RecommendationCreate, db: Session = Depends(get_db)):
    """
    Create a new recommendation.

    Args:
        rec_data: The recommendation details

    Returns:
        The created recommendation

    Raises:
        HTTPException: 400 if interview not found
    """
    interview = db.query(Interview).filter(Interview.id == rec_data.interview_id).first()
    if not interview:
        raise HTTPException(status_code=400, detail="Interview not found")

    recommendation = Recommendation(**rec_data.model_dump())
    db.add(recommendation)
    db.commit()
    db.refresh(recommendation)
    return recommendation
