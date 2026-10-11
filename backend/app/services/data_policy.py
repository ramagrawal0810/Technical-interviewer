"""The data-handling facts shown to candidates in the consent notice, taken from the real configuration.

Nothing here is hard-coded prose about guarantees: storage and retention values are read from the application's
settings, and every statement that something is NOT implemented reflects the current code.
"""

from pathlib import Path

from fastapi import HTTPException

from app.config import settings
from app.db import database

# Bump when the wording of the consent notice or the facts it relies on change materially
CONSENT_NOTICE_VERSION = "2026-10-10"


def database_file_name() -> str:
    url = database.DATABASE_URL
    return Path(url.split("///", 1)[1]).name if url.startswith("sqlite:///") else url


def require_consent(interview) -> None:
    """Candidate data may only be processed once the candidate has granted consent for this interview."""
    if interview.consent_status == "granted":
        return
    detail = ("Consent was declined for this interview." if interview.consent_status == "declined"
              else "Consent has not been recorded for this interview.")
    raise HTTPException(status_code=403, detail=detail)


def get_data_handling_policy() -> dict:
    return {
        "notice_version": CONSENT_NOTICE_VERSION,
        "storage": (
            f"A SQLite database file named {database_file_name()} on the server that runs this application. "
            "It is not an external or cloud database."
        ),
        "database_file": database_file_name(),
        "retention_days": settings.data_retention_days,
        "retention_enforcement": (
            f"Interview records are intended to be kept for {settings.data_retention_days} days after the interview is "
            "completed. This is a policy: nothing is deleted automatically. An administrator runs a cleanup tool that "
            "lists records past that period and deletes them only when explicitly told to."
        ),
        "deletion_requests": (
            "There is no self-service deletion. To ask what is stored about you or to ask for your interview data to be "
            "deleted, contact the interviewer or administrator, who can delete it with the administrator cleanup tool. "
            "Your name and email stay in the candidate list unless an administrator removes them separately."
        ),
        "stored": [
            "your answers (the interview transcript) and the questions asked",
            "the AI evaluation: scores, written justifications and supporting quotes",
            "the AI recommendation and the interviewer's decision",
            "your CV text, if a CV is used for your interview",
        ],
        "shared_with_llm_provider": (
            "To evaluate your answers and generate questions, your answers, your CV text (for CV-based interviews) and the "
            "interview questions are sent to the external AI provider, OpenAI."
        ),
        "automatic_deletion_implemented": False,
        "encryption_implemented": False,
    }
