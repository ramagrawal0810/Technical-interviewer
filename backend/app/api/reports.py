"""Feedback report endpoints for completed interviews (interviewer-facing)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas import FeedbackReport
from app.services.report_builder import ReportNotAvailable, build_feedback_report
from app.services.report_export import render_markdown, render_pdf, safe_filename

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/interviews", tags=["reports"])

_DOWNLOAD_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}


def _report(db: Session, interview_id: int) -> FeedbackReport:
    try:
        return build_feedback_report(db, interview_id)
    except ReportNotAvailable as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.get("/{interview_id}/report", response_model=FeedbackReport)
def get_feedback_report(interview_id: int, db: Session = Depends(get_db)):
    """
    The feedback report for a completed interview, built from stored data only (no LLM call, nothing created).

    Raises:
        HTTPException: 404 if the interview does not exist, 400 if it is not completed
    """
    return _report(db, interview_id)


@router.get("/{interview_id}/report.md")
def download_report_markdown(interview_id: int, db: Session = Depends(get_db)):
    """The same report as a Markdown download. Generated in memory; nothing is written to disk."""
    report = _report(db, interview_id)
    return Response(
        content=render_markdown(report).encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={**_DOWNLOAD_HEADERS, "Content-Disposition": f'attachment; filename="{safe_filename(report, "md")}"'},
    )


@router.get("/{interview_id}/report.pdf")
def download_report_pdf(interview_id: int, db: Session = Depends(get_db)):
    """The same report as a PDF download. Generated in memory; nothing is written to disk."""
    report = _report(db, interview_id)
    return Response(
        content=render_pdf(report),
        media_type="application/pdf",
        headers={**_DOWNLOAD_HEADERS, "Content-Disposition": f'attachment; filename="{safe_filename(report, "pdf")}"'},
    )
