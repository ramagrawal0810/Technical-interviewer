"""Job Description API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import JobDescription
from app.schemas import JobDescriptionResponse

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=list[JobDescriptionResponse])
def get_all_jobs(db: Session = Depends(get_db)):
    """
    Get all job descriptions.

    Returns a list of all job postings in the database.
    """
    jobs = db.query(JobDescription).all()
    return jobs


@router.get("/{job_id}", response_model=JobDescriptionResponse)
def get_job(job_id: int, db: Session = Depends(get_db)):
    """
    Get a specific job description by ID.

    Args:
        job_id: The ID of the job description to retrieve

    Returns:
        The job description details

    Raises:
        HTTPException: 404 if job not found
    """
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job description not found")
    return job
