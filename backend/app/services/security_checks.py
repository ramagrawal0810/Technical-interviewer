"""Record prompt-injection check results against the interview and the content they came from.

A check is recorded for every piece of candidate content that was actually inspected (clean or not), so the
report can say "no suspected attempt" only when content really was checked. If the detector itself fails the
result is recorded as "failed", never as clean. Nothing here can raise into the interview flow.
"""

import json
import logging
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ContentSecurityCheck
from app.services.injection_detector import STATUS_CLEAN, STATUS_SUSPECTED, detect_prompt_injection

logger = logging.getLogger(__name__)

STATUS_FAILED = "failed"
SOURCE_CV = "cv"
SOURCE_ANSWER = "answer"
CV_SOURCE_ID = 0


def _run_detector(text: Optional[str]) -> dict:
    try:
        result = detect_prompt_injection(text)
    except Exception as e:  # the check must never break the interview, and must never claim "clean" after failing
        logger.error(f"Prompt-injection check failed: {type(e).__name__}: {e}")
        return {"status": STATUS_FAILED, "categories": None, "excerpt": None,
                "explanation": f"The security check could not be completed ({type(e).__name__}); the content's status is unknown."}
    return {
        "status": result.status,
        "categories": json.dumps(result.categories) if result.categories else None,
        "excerpt": " | ".join(result.excerpts)[:800] if result.excerpts else None,
        "explanation": result.explanation,
    }


def record_security_check(db: Session, interview_id: int, source: str, source_id: int, text: Optional[str]) -> Optional[ContentSecurityCheck]:
    """Check `text` and store (or refresh) the result for this interview/source. Returns None only if storing failed."""
    values = _run_detector(text)
    try:
        row = db.query(ContentSecurityCheck).filter_by(interview_id=interview_id, source=source, source_id=source_id).first()
        if row is None:
            row = ContentSecurityCheck(interview_id=interview_id, source=source, source_id=source_id, **values)
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        try:
            db.commit()
        except IntegrityError:  # a concurrent request stored the same check first
            db.rollback()
            row = db.query(ContentSecurityCheck).filter_by(interview_id=interview_id, source=source, source_id=source_id).first()
        if row is not None:
            db.refresh(row)
        if values["status"] == STATUS_SUSPECTED:
            logger.warning(f"Suspected prompt injection in {source} (interview {interview_id}, source id {source_id}): {values['categories']}")
        return row
    except Exception as e:
        db.rollback()
        logger.error(f"Could not store the security check for interview {interview_id}: {type(e).__name__}: {e}")
        return None


def summarise_checks(checks: list, expected_cv: bool, answer_ids: list[int]) -> dict:
    """
    Combine stored checks into one interviewer-facing status.

    overall: "suspected" (any attempt flagged) > "failed" (a check failed) > "unknown" (some content was never
    checked) > "clean" (everything relevant was checked and nothing was flagged).
    """
    by_key = {(c.source, c.source_id): c for c in checks}
    items = []
    if expected_cv:
        items.append((SOURCE_CV, CV_SOURCE_ID))
    items += [(SOURCE_ANSWER, answer_id) for answer_id in answer_ids]

    flagged = [by_key[key] for key in items if key in by_key and by_key[key].status == STATUS_SUSPECTED]
    failed = [key for key in items if key in by_key and by_key[key].status == STATUS_FAILED]
    unchecked = [key for key in items if key not in by_key]
    clean = [key for key in items if key in by_key and by_key[key].status == STATUS_CLEAN]

    if flagged:
        overall = STATUS_SUSPECTED
    elif failed:
        overall = STATUS_FAILED
    elif unchecked:
        overall = "unknown"
    else:
        overall = STATUS_CLEAN if items else "unknown"
    return {"overall": overall, "flagged": flagged, "checked_clean": len(clean), "failed": len(failed),
            "unchecked": len(unchecked), "total": len(items)}
