"""Administrator tool: list, and optionally delete, interview data past the retention period.

    python retention_cleanup.py                       # dry run: list completed interviews past DATA_RETENTION_DAYS
    python retention_cleanup.py --days 90             # dry run with a different period
    python retention_cleanup.py --execute             # delete them (asks you to type DELETE first)
    python retention_cleanup.py --interview-id 12     # dry run for one interview (deletion on a candidate's request)
    python retention_cleanup.py --interview-id 12 --execute

Safety: dry run by default; never runs automatically; interviews in progress are refused; the shared
question bank and the candidate list are never touched. Back up the database file before deleting.
"""

import argparse
import sys

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Candidate, Interview
from app.services.retention import (
    RetentionError, delete_interview_data, describe_interview_data, find_expired_interviews,
)


def _label(db, interview: Interview) -> str:
    candidate = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
    when = interview.completed_at.strftime("%Y-%m-%d") if interview.completed_at else "n/a"
    return f"#{interview.id} {candidate.name if candidate else 'unknown'} | {interview.status} | completed {when}"


def main(argv=None, session_factory=SessionLocal, input_fn=input, out=print) -> int:
    parser = argparse.ArgumentParser(description="List or delete interview data past the retention period.")
    parser.add_argument("--days", type=int, default=settings.data_retention_days, help="retention period in days")
    parser.add_argument("--interview-id", type=int, help="act on one interview instead of everything past retention")
    parser.add_argument("--execute", action="store_true", help="actually delete (default is a dry run)")
    args = parser.parse_args(argv)

    db = session_factory()
    try:
        if args.interview_id is not None:
            interview = db.query(Interview).filter(Interview.id == args.interview_id).first()
            if interview is None:
                out(f"Interview {args.interview_id} not found.")
                return 1
            targets = [interview]
            if interview.status not in ("completed", "abandoned", "not_started"):
                out(f"Refusing: interview {interview.id} is {interview.status}; active interviews are never deleted.")
                return 1
        else:
            targets = find_expired_interviews(db, args.days)
            out(f"Retention period: {args.days} days. Completed or abandoned interviews past it: {len(targets)}")

        for interview in targets:
            out(f"  {_label(db, interview)} -> {describe_interview_data(db, interview.id)}")

        if not targets:
            return 0
        if not args.execute:
            out("Dry run only: nothing was deleted. Re-run with --execute to delete the interviews listed above.")
            return 0

        if input_fn(f"Type DELETE to permanently delete {len(targets)} interview(s): ").strip() != "DELETE":
            out("Cancelled: nothing was deleted.")
            return 1
        for interview_id in [i.id for i in targets]:   # ids first: the ORM objects are gone once deleted
            try:
                counts = delete_interview_data(db, interview_id)
                out(f"Deleted interview {interview_id}: {counts}")
            except RetentionError as e:
                out(f"Skipped: {e}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
