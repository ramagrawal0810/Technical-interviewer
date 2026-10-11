"""A candidate's saved CV: reading a PDF into the candidate record, and attaching the synthetic sample CVs.

The CV text is stored on the candidate row (`candidates.cv_text`); the PDF file itself is not kept. An interview takes a snapshot of
that text in `interviews.cv_text` when it is scheduled, so every question, follow-up and evaluation of that interview uses the CV that
was in force then, whatever happens to the candidate's saved CV later.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Candidate
from app.services.cv_processor import extract_text_from_pdf, validate_cv_text

logger = logging.getLogger(__name__)

SYNTHETIC_CV_DIR = Path(__file__).resolve().parents[2] / "sample_data" / "synthetic_cvs"


class CvError(ValueError):
    """The uploaded file could not be used as a CV (message is safe to show)."""


def read_cv_pdf(file_bytes: bytes) -> str:
    try:
        text = extract_text_from_pdf(file_bytes)
    except Exception as e:  # empty, corrupt or non-PDF input
        logger.warning(f"Unreadable CV: {type(e).__name__}")
        raise CvError("The file could not be read as a PDF. Please upload a valid, text-based PDF.")
    ok, message = validate_cv_text(text)
    if not ok:
        raise CvError(message)
    return text


def save_candidate_cv(db: Session, candidate: Candidate, text: str, file_name: Optional[str]) -> None:
    """Store `text` as this candidate's saved CV (replacing any earlier one). Does not commit."""
    candidate.cv_text = text
    candidate.cv_file_name = (file_name or "")[:255] or None
    candidate.cv_updated_at = datetime.utcnow()


def attach_synthetic_cvs(db: Session, cv_dir: Path = SYNTHETIC_CV_DIR) -> dict:
    """
    Link each sample CV in `cv_dir/manifest.json` to its candidate (matched by email) and store its text.

    Safe to repeat: a candidate that already has a saved CV is never overwritten. A CV is attached only if the name printed at the
    top of the PDF equals the candidate's name, so a file can never end up on the wrong person. Does not commit.
    """
    result = {"attached": 0, "already_had_cv": 0, "skipped": []}
    manifest_path = cv_dir / "manifest.json"
    if not manifest_path.exists():
        return result
    with open(manifest_path, encoding="utf-8") as f:
        entries = json.load(f)
    for entry in entries:
        candidate = db.query(Candidate).filter(Candidate.email == entry["candidate_email"]).first()
        if candidate is None:
            result["skipped"].append(f"{entry['file']}: no candidate with that email")
            continue
        if candidate.cv_text:
            result["already_had_cv"] += 1
            continue
        try:
            text = read_cv_pdf((cv_dir / entry["file"]).read_bytes())
        except (CvError, OSError) as e:
            result["skipped"].append(f"{entry['file']}: {e}")
            continue
        printed_name = text.splitlines()[0].strip()
        if printed_name != candidate.name:
            result["skipped"].append(f"{entry['file']}: CV is for '{printed_name}', candidate is '{candidate.name}'")
            continue
        save_candidate_cv(db, candidate, text, entry["file"])
        result["attached"] += 1
    return result
