"""Aggregate LLM usage metrics for interviews."""

import logging
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.db.models import LLMUsageLog
from app.services.llm_pricing import estimate_cost_usd, get_model_pricing

logger = logging.getLogger(__name__)

# Not LLM chat completions: reported separately and excluded from LLM call/cost metrics
AUDIO_OPERATIONS = ("tts", "stt")

# Display labels derived from LLMUsageLog.operation. LLMUsageLog has no agent_name column;
# these labels are presentation only.
OPERATION_LABELS = {
    "evaluation": "Technical Evaluation",
    "adaptive_follow_up": "Adaptive Interview",
    "cv_question_generation": "CV Question Generation",
    "hybrid_question_generation": "Hybrid Question Generation",
    "recommendation_generation": "Recommendation",
    "tts": "Text-to-Speech",
    "stt": "Speech-to-Text",
}


def operation_label(operation: str) -> str:
    return OPERATION_LABELS.get(operation) or operation.replace("_", " ").title()


def compute_usage_metrics(logs) -> Dict:
    """
    Headline metrics over LLM (non-audio) usage logs.

    Token counts are the provider-reported values stored in each log row. Cost is an estimate
    from llm_pricing; calls on models without a known price are counted in unpriced_calls
    rather than being given an invented cost.
    """
    llm_logs = [log for log in logs if log.operation not in AUDIO_OPERATIONS]
    calls = len(llm_logs)
    successes = sum(1 for log in llm_logs if log.status == "success")

    estimated_cost = 0.0
    unpriced_calls = 0
    for log in llm_logs:
        cost = estimate_cost_usd(log.model, log.prompt_tokens, log.completion_tokens)
        if cost is not None:
            estimated_cost += cost
        elif get_model_pricing(log.model) is None and (log.total_tokens or 0) > 0:
            unpriced_calls += 1

    return {
        "total_llm_calls": calls,
        "success_calls": successes,
        "error_calls": calls - successes,
        "success_rate": successes / calls if calls else None,
        "average_latency_ms": round(sum(log.latency_ms for log in llm_logs) / calls, 1) if calls else None,
        "prompt_tokens": sum(log.prompt_tokens or 0 for log in llm_logs),
        "completion_tokens": sum(log.completion_tokens or 0 for log in llm_logs),
        "total_tokens": sum(log.total_tokens or 0 for log in llm_logs),
        "estimated_cost_usd": round(estimated_cost, 6),
        "unpriced_calls": unpriced_calls,
    }


def _extra_summary_fields(metrics: Dict) -> Dict:
    """The metrics added to the per-interview summary (existing fields are unchanged)."""
    return {
        key: metrics[key]
        for key in ("success_calls", "error_calls", "success_rate", "average_latency_ms", "estimated_cost_usd", "unpriced_calls")
    }


def get_interview_usage_summary(interview_id: int, db: Session) -> Dict:
    """
    Aggregate LLM/API usage metrics for a complete interview.

    Args:
        interview_id: The interview ID
        db: Database session

    Returns:
        Dictionary with aggregated usage metrics
    """
    try:
        # Get all usage logs for this interview
        usage_logs = db.query(LLMUsageLog).filter(
            LLMUsageLog.interview_id == interview_id
        ).all()

        if not usage_logs:
            logger.info(f"No usage logs found for interview {interview_id}")
            return {
                "interview_id": interview_id,
                "total_llm_calls": 0,
                "total_latency_ms": 0,
                "tokens": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "by_operation": {},
                "audio": {
                    "tts_calls": 0,
                    "tts_latency_ms": 0,
                    "stt_calls": 0,
                    "stt_latency_ms": 0,
                },
                **_extra_summary_fields(compute_usage_metrics([])),
            }

        # Aggregate by operation
        by_operation = {}
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        total_latency_ms = 0
        tts_calls = 0
        tts_latency = 0
        stt_calls = 0
        stt_latency = 0

        for log in usage_logs:
            if log.operation in ["tts", "stt"]:
                # Handle audio operations separately
                if log.operation == "tts":
                    tts_calls += 1
                    tts_latency += log.latency_ms
                elif log.operation == "stt":
                    stt_calls += 1
                    stt_latency += log.latency_ms
            else:
                # Handle LLM operations
                if log.operation not in by_operation:
                    by_operation[log.operation] = {
                        "calls": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "latency_ms": 0,
                    }

                by_operation[log.operation]["calls"] += 1

                if log.prompt_tokens:
                    by_operation[log.operation]["prompt_tokens"] += log.prompt_tokens
                    total_prompt_tokens += log.prompt_tokens

                if log.completion_tokens:
                    by_operation[log.operation]["completion_tokens"] += log.completion_tokens
                    total_completion_tokens += log.completion_tokens

                if log.total_tokens:
                    by_operation[log.operation]["total_tokens"] += log.total_tokens
                    total_tokens += log.total_tokens

                by_operation[log.operation]["latency_ms"] += log.latency_ms
                total_latency_ms += log.latency_ms

        # Count total LLM calls (non-audio)
        total_llm_calls = sum(op["calls"] for op in by_operation.values())

        result = {
            "interview_id": interview_id,
            "total_llm_calls": total_llm_calls,
            "total_latency_ms": total_latency_ms,
            "tokens": {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_tokens,
            },
            "by_operation": by_operation,
            "audio": {
                "tts_calls": tts_calls,
                "tts_latency_ms": tts_latency,
                "stt_calls": stt_calls,
                "stt_latency_ms": stt_latency,
            },
            **_extra_summary_fields(compute_usage_metrics(usage_logs)),
        }

        logger.info(f"Aggregated usage for interview {interview_id}: {total_llm_calls} LLM calls, {total_tokens} tokens, {total_latency_ms}ms latency")
        return result

    except Exception as e:
        logger.error(f"Error aggregating usage for interview {interview_id}: {type(e).__name__}: {str(e)}")
        return {
            "interview_id": interview_id,
            "error": str(e),
        }
