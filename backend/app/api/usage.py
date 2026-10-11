"""LLM usage tracking API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Candidate, Interview, JobDescription, LLMUsageLog
from app.schemas import (
    UsageDashboardResponse,
    UsageLogRow,
    UsageMetrics,
    UsageOperationOption,
    UsageSummaryResponse,
)
from app.services.llm_pricing import estimate_cost_usd
from app.services.usage_aggregator import (
    AUDIO_OPERATIONS,
    compute_usage_metrics,
    get_interview_usage_summary,
    operation_label,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/interviews", tags=["usage"])
dashboard_router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/{interview_id}/usage", response_model=UsageSummaryResponse)
def get_interview_usage(interview_id: int, db: Session = Depends(get_db)):
    """
    Get aggregated LLM/API usage metrics for an interview.

    Args:
        interview_id: The ID of the interview

    Returns:
        Aggregated usage statistics including tokens, latency, and operation breakdown

    Raises:
        HTTPException: 404 if interview not found
    """
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    logger.info(f"Fetching usage summary for interview {interview_id}")

    usage_summary = get_interview_usage_summary(interview_id, db)

    return UsageSummaryResponse(**usage_summary)


@dashboard_router.get("", response_model=UsageDashboardResponse)
def get_usage_dashboard(
    candidate_id: Optional[int] = None,
    interview_id: Optional[int] = None,
    operation: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """
    LLM usage across interviews, from the existing LLMUsageLog table.

    Filters (all optional, combinable): candidate_id, interview_id, operation, status
    ("success" or "error"). `summary` covers every matching LLM call (audio operations excluded);
    `logs` is the newest `limit` matching rows, including audio operations, which have no price.

    Raises:
        HTTPException: 400 if status is not "success" or "error"
    """
    query = db.query(LLMUsageLog)

    if candidate_id is not None:
        candidate_interviews = db.query(Interview.id).filter(Interview.candidate_id == candidate_id)
        query = query.filter(LLMUsageLog.interview_id.in_(candidate_interviews))
    if interview_id is not None:
        query = query.filter(LLMUsageLog.interview_id == interview_id)
    if operation:
        query = query.filter(LLMUsageLog.operation == operation)
    if status:
        normalized = status.strip().lower()
        if normalized not in ("success", "error"):
            raise HTTPException(status_code=400, detail="Invalid status. Must be 'success' or 'error'")
        query = query.filter(LLMUsageLog.status == normalized)

    matching = query.order_by(LLMUsageLog.created_at.desc(), LLMUsageLog.id.desc()).all()
    shown = matching[:limit]

    interview_ids = {log.interview_id for log in shown if log.interview_id is not None}
    interviews = {i.id: i for i in db.query(Interview).filter(Interview.id.in_(interview_ids)).all()} if interview_ids else {}
    candidate_names = (
        {c.id: c.name for c in db.query(Candidate).filter(Candidate.id.in_({i.candidate_id for i in interviews.values()})).all()}
        if interviews else {}
    )
    job_titles = (
        {j.id: j.title for j in db.query(JobDescription).filter(JobDescription.id.in_({i.job_description_id for i in interviews.values()})).all()}
        if interviews else {}
    )

    rows = []
    for log in shown:
        interview = interviews.get(log.interview_id)
        rows.append(UsageLogRow(
            id=log.id,
            created_at=log.created_at,
            interview_id=log.interview_id,
            candidate_name=candidate_names.get(interview.candidate_id) if interview else None,
            job_title=job_titles.get(interview.job_description_id) if interview else None,
            operation=log.operation,
            operation_label=operation_label(log.operation),
            model=log.model,
            prompt_tokens=log.prompt_tokens,
            completion_tokens=log.completion_tokens,
            total_tokens=log.total_tokens,
            latency_ms=log.latency_ms,
            status=log.status,
            estimated_cost_usd=(
                None if log.operation in AUDIO_OPERATIONS
                else estimate_cost_usd(log.model, log.prompt_tokens, log.completion_tokens)
            ),
        ))

    operations = [row[0] for row in db.query(LLMUsageLog.operation).distinct().order_by(LLMUsageLog.operation).all()]

    return UsageDashboardResponse(
        summary=UsageMetrics(**compute_usage_metrics(matching)),
        logs=rows,
        total_logs=len(matching),
        available_operations=[UsageOperationOption(value=op, label=operation_label(op)) for op in operations],
    )
