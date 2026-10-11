"""Derived interview display status.

Only not_started / in_progress / completed are stored. "Scheduled" and "Reviewed"
are derived, so they can never drift from the interview row or the HumanReview row.
"""

_DISPLAY_BY_STORED_STATUS = {
    "not_started": "Scheduled",
    "in_progress": "In Progress",
    "abandoned": "Abandoned",
}


def compute_display_status(status: str, has_human_review: bool) -> str:
    if status == "completed":
        return "Reviewed" if has_human_review else "Completed"
    return _DISPLAY_BY_STORED_STATUS.get(status, status)
