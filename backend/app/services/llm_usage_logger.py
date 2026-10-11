"""Centralized LLM/API usage logging service."""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import LLMUsageLog

logger = logging.getLogger(__name__)


def log_usage(
    operation: str,
    provider: str,
    model: str,
    latency_ms: int,
    status: str,
    interview_id: Optional[int] = None,
    question_id: Optional[int] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    error_message: Optional[str] = None,
    db: Optional[Session] = None,
) -> Optional[LLMUsageLog]:
    """
    Log LLM/API usage to database and structured logging.

    This function is non-critical: if logging fails, it will not crash the interview flow.

    Args:
        operation: Operation name (evaluation, recommendation_generation, etc.)
        provider: API provider ("openai")
        model: Model name ("gpt-4o-mini", "tts-1", "whisper-1")
        latency_ms: API call latency in milliseconds
        status: "success" or "error"
        interview_id: Interview ID (if available)
        question_id: Question ID (if available)
        prompt_tokens: Prompt tokens (for chat completions)
        completion_tokens: Completion tokens (for chat completions)
        total_tokens: Total tokens (for chat completions)
        error_message: Error message if status is "error" (safe info only)
        db: SQLAlchemy session (if available)

    Returns:
        LLMUsageLog record if created, None if logging failed
    """
    try:
        # Log to structured application logging first
        log_data = {
            "operation": operation,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "status": status,
        }
        if interview_id:
            log_data["interview_id"] = interview_id
        if question_id:
            log_data["question_id"] = question_id
        if prompt_tokens:
            log_data["prompt_tokens"] = prompt_tokens
        if completion_tokens:
            log_data["completion_tokens"] = completion_tokens
        if total_tokens:
            log_data["total_tokens"] = total_tokens

        log_message = " ".join(f"{k}={v}" for k, v in log_data.items())
        if status == "success":
            logger.info(f"LLM_USAGE {log_message}")
        else:
            logger.warning(f"LLM_USAGE {log_message} error={error_message}")

        # If database session not provided, just return after logging
        if not db:
            return None

        # Create database record
        usage_log = LLMUsageLog(
            interview_id=interview_id,
            question_id=question_id,
            operation=operation,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
        )

        db.add(usage_log)
        db.commit()
        db.refresh(usage_log)

        logger.debug(f"Created LLMUsageLog record {usage_log.id}")
        return usage_log

    except Exception as e:
        # Logging failure should not crash the application
        logger.error(f"Error logging LLM usage: {type(e).__name__}: {str(e)}")
        return None
