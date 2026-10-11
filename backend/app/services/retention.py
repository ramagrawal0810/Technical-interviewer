"""Retention: find interview data past the configured retention period, and delete it only on explicit request.

Never called automatically (not at startup, not on a schedule). The entry point is retention_cleanup.py,
which is a dry run unless told otherwise. Interviews that are in progress are never deleted, and the shared
question bank is never touched.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.db.models import (
    Answer, ContentSecurityCheck, Evaluation, HumanReview, Interview, InterviewQuestion, LLMUsageLog, Question,
    Recommendation,
)

logger = logging.getLogger(__name__)

DELETABLE_STATUSES = ("completed", "abandoned", "not_started")


class RetentionError(Exception):
    """The requested deletion is not allowed (e.g. the interview is still active)."""


def retention_cutoff(retention_days: int, now: Optional[datetime] = None) -> datetime:
    return (now or datetime.utcnow()) - timedelta(days=retention_days)


def find_expired_interviews(db: Session, retention_days: int, now: Optional[datetime] = None) -> list[Interview]:
    """Completed or abandoned interviews whose end is older than the retention period. Active interviews never qualify."""
    cutoff = retention_cutoff(retention_days, now)
    finished = or_(
        and_(Interview.status == "completed", Interview.completed_at.isnot(None), Interview.completed_at < cutoff),
        and_(Interview.status == "abandoned", Interview.abandoned_at.isnot(None), Interview.abandoned_at < cutoff),
    )
    return db.query(Interview).filter(finished).order_by(Interview.id).all()


def describe_interview_data(db: Session, interview_id: int) -> dict:
    """Row counts that deleting this interview would remove (used for the dry run)."""
    iq_ids = [row[0] for row in db.query(InterviewQuestion.id).filter(InterviewQuestion.interview_id == interview_id)]
    answer_ids = [row[0] for row in db.query(Answer.id).filter(Answer.interview_question_id.in_(iq_ids))] if iq_ids else []
    return {
        "interview_questions": len(iq_ids),
        "answers": len(answer_ids),
        "evaluations": db.query(Evaluation).filter(Evaluation.answer_id.in_(answer_ids)).count() if answer_ids else 0,
        "recommendations": db.query(Recommendation).filter(Recommendation.interview_id == interview_id).count(),
        "human_reviews": db.query(HumanReview).filter(HumanReview.interview_id == interview_id).count(),
        "security_checks": db.query(ContentSecurityCheck).filter(ContentSecurityCheck.interview_id == interview_id).count(),
        "generated_questions": db.query(Question).filter(Question.source_interview_id == interview_id).count(),
        "usage_logs_anonymised": db.query(LLMUsageLog).filter(LLMUsageLog.interview_id == interview_id).count(),
    }


def delete_interview_data(db: Session, interview_id: int) -> dict:
    """
    Delete one interview and everything stored about it: transcript, evaluations, recommendation, human review,
    security checks and the questions generated for it. LLM usage logs hold no candidate content (only tokens,
    latency and status), so they are kept for cost accounting but detached from the interview (interview_id set to NULL).
    The candidate record and the shared question bank are not touched.

    Raises:
        RetentionError: if the interview does not exist or is not in a deletable state (in progress)
    """
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if interview is None:
        raise RetentionError(f"Interview {interview_id} not found")
    if interview.status not in DELETABLE_STATUSES:
        raise RetentionError(f"Interview {interview_id} is {interview.status}; only completed or not-started interviews can be deleted")

    counts = describe_interview_data(db, interview_id)
    try:
        iq_ids = [row[0] for row in db.query(InterviewQuestion.id).filter(InterviewQuestion.interview_id == interview_id)]
        answer_ids = [row[0] for row in db.query(Answer.id).filter(Answer.interview_question_id.in_(iq_ids))] if iq_ids else []
        if answer_ids:
            db.query(Evaluation).filter(Evaluation.answer_id.in_(answer_ids)).delete(synchronize_session=False)
            db.query(Answer).filter(Answer.id.in_(answer_ids)).delete(synchronize_session=False)
        db.query(HumanReview).filter(HumanReview.interview_id == interview_id).delete(synchronize_session=False)
        db.query(Recommendation).filter(Recommendation.interview_id == interview_id).delete(synchronize_session=False)
        db.query(ContentSecurityCheck).filter(ContentSecurityCheck.interview_id == interview_id).delete(synchronize_session=False)
        if iq_ids:
            db.query(InterviewQuestion).filter(InterviewQuestion.id.in_(iq_ids)).delete(synchronize_session=False)
        db.query(Question).filter(Question.source_interview_id == interview_id).delete(synchronize_session=False)
        db.query(LLMUsageLog).filter(LLMUsageLog.interview_id == interview_id).update(
            {LLMUsageLog.interview_id: None}, synchronize_session=False)
        db.query(Interview).filter(Interview.id == interview_id).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info(f"Deleted interview {interview_id} data: {counts}")
    return counts
