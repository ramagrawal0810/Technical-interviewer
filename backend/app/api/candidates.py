"""Candidate API endpoints."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Candidate
from app.schemas import CandidateCvResponse, CandidateResponse
from app.services.candidate_cv import CvError, read_cv_pdf, save_candidate_cv

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


@router.get("", response_model=list[CandidateResponse])
def get_all_candidates(db: Session = Depends(get_db)):
    """
    Get all candidates.

    Returns a list of all candidates in the database.
    """
    candidates = db.query(Candidate).all()
    return candidates


@router.get("/{candidate_id}", response_model=CandidateResponse)
def get_candidate(candidate_id: int, db: Session = Depends(get_db)):
    """
    Get a specific candidate by ID.

    Args:
        candidate_id: The ID of the candidate to retrieve

    Returns:
        The candidate details

    Raises:
        HTTPException: 404 if candidate not found
    """
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@router.post("/{candidate_id}/cv", response_model=CandidateCvResponse)
def save_candidate_cv_endpoint(candidate_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Save a PDF as this candidate's CV, replacing the one on file (if any).

    Only this candidate's record changes. Interviews that already exist keep the CV they were scheduled with.

    Raises:
        HTTPException: 404 if the candidate does not exist, 400 if the file is not a readable text PDF
    """
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    try:
        text = read_cv_pdf(file.file.read())
    except CvError as e:
        raise HTTPException(status_code=400, detail=str(e))
    save_candidate_cv(db, candidate, text, file.filename)
    db.commit()
    db.refresh(candidate)
    return CandidateCvResponse(candidate_id=candidate.id, has_cv=True, cv_file_name=candidate.cv_file_name,
                               cv_updated_at=candidate.cv_updated_at, cv_text_length=len(text))
