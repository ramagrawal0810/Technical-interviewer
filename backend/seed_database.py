"""Populate the database with synthetic test data.

Safe to re-run: rows that already exist are skipped (job descriptions by title, candidates by
email, questions by role + text), so a re-run on an already-seeded database adds only what is missing,
e.g. questions added to the question banks later.
"""

import json
from pathlib import Path

from app.db.database import SessionLocal, init_db
from app.db.models import Candidate, JobDescription, Question

DATA_DIR = Path(__file__).parent.parent / "data"


def load_json(file_path):
    """Load data from a JSON file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def seed_into(db, data_dir: Path = DATA_DIR) -> dict:
    """Insert any missing seed rows into `db` (does not commit). Returns counts of rows added."""
    added = {"job_descriptions": 0, "candidates": 0, "questions": 0, "candidate_cvs": 0}

    existing_jobs = {j.title for j in db.query(JobDescription).all()}
    for job in load_json(data_dir / "job_descriptions.json"):
        if job["title"] not in existing_jobs:
            db.add(JobDescription(**job))
            existing_jobs.add(job["title"])
            added["job_descriptions"] += 1

    existing_emails = {c.email for c in db.query(Candidate).all()}
    for candidate in load_json(data_dir / "candidates.json"):
        if candidate["email"] not in existing_emails:
            db.add(Candidate(**candidate))
            existing_emails.add(candidate["email"])
            added["candidates"] += 1

    # Only shared question-bank rows (not interview-generated ones) count as "already seeded"
    existing_questions = {
        (q.job_type, q.question)
        for q in db.query(Question).filter(Question.source_interview_id.is_(None)).all()
    }
    for question_file in sorted((data_dir / "question_banks").glob("*.json")):
        for question in load_json(question_file):
            key = (question["job_type"], question["question"])
            if key not in existing_questions:
                db.add(Question(**question))
                existing_questions.add(key)
                added["questions"] += 1

    db.flush()
    # Link the synthetic sample CVs to their candidates (never overwrites a CV a candidate already has)
    from app.services.candidate_cv import attach_synthetic_cvs
    added["candidate_cvs"] = attach_synthetic_cvs(db)["attached"]
    return added


def seed_database():
    """Populate the database with test data."""
    init_db()   # create missing tables / add missing columns first (additive only), so an older database can be seeded
    db = SessionLocal()

    try:
        added = seed_into(db)
        db.commit()

        print()
        if not any(added.values()):
            print("Database already contains all seed data. Nothing to add.")
        else:
            print("Database seeding complete!")
            print(f"  - Job descriptions added: {added['job_descriptions']}")
            print(f"  - Candidates added: {added['candidates']}")
            print(f"  - Candidate CVs attached: {added['candidate_cvs']}")
            print(f"  - Questions added: {added['questions']}")

        per_role = {}
        for question in db.query(Question).filter(Question.source_interview_id.is_(None)).all():
            per_role.setdefault(question.job_type, {}).setdefault(question.difficulty.lower(), 0)
            per_role[question.job_type][question.difficulty.lower()] += 1
        for role, levels in sorted(per_role.items()):
            print(f"  - {role}: {sum(levels.values())} questions "
                  f"(easy {levels.get('easy', 0)}, medium {levels.get('medium', 0)}, hard {levels.get('hard', 0)})")

    except Exception as e:
        print(f"Error seeding database: {e}")
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    seed_database()
