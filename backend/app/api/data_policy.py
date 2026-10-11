
"""Public description of how candidate data is handled (shown to candidates in the consent notice)."""

from fastapi import APIRouter

from app.services.data_policy import get_data_handling_policy

router = APIRouter(prefix="/api/data-handling", tags=["data-handling"])


@router.get("")
def get_data_handling():
    """Storage location, retention period and LLM-provider sharing, read from the real configuration."""
    return get_data_handling_policy()
