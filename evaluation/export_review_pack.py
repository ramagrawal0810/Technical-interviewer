"""Build the human-review sheets for experiment D from COMPLETED interviews in the application database.

The database is opened READ-ONLY; nothing is modified. No answers, scores or judgements are invented: every row comes from a stored
interview, and every human field is left blank for a real reviewer.

Files written to evaluation/human_review/ (existing files are MERGED, never overwritten: rows already there keep whatever a human
typed, only new interviews are appended; use --force-new to start over):

  review_sheet_BLIND.csv         what the human reviewer fills in. One row per answer, WITHOUT any AI score or recommendation.
  review_sheet_with_ai.csv       the same rows with the AI's dimension scores/overall score and a column for explaining disagreements.
  interview_sheet_BLIND.csv      one row per interview: the human's overall recommendation (no AI information).
  interview_sheet_with_ai.csv    the same with the AI recommendation, confidence and a disagreement-explanation column.

Run from the repository root:   backend\\venv\\Scripts\\python.exe evaluation\\export_review_pack.py
"""

import argparse
import csv
import json
import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(ROOT, "backend", "technical_interviewer.db")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "human_review")
DIMS = ["technical_skills", "domain_understanding", "problem_solving", "communication_clarity"]
HUMAN_DIMS = [f"human_{d}" for d in DIMS]

ANSWER_BLIND = ["answer_ref", "interview_id", "job_role", "interview_mode", "candidate_cv_file", "position", "question", "candidate_answer",
                *HUMAN_DIMS, "human_comment", "reviewer"]
ANSWER_FULL = ["answer_ref", "interview_id", "position", "question", *[f"ai_{d}" for d in DIMS], "ai_overall_0_10", *HUMAN_DIMS,
               "human_overall_0_10_derived", "disagreement_explanation"]
INTERVIEW_BLIND = ["interview_id", "job_role", "interview_mode", "answers_in_interview", "human_recommendation", "human_rationale", "reviewer"]
INTERVIEW_FULL = ["interview_id", "ai_overall_score_0_10", "ai_recommendation", "ai_confidence", "human_recommendation", "disagreement_explanation"]


def read_existing(path, key):
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {row[key]: row for row in csv.DictReader(f)}


def write(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def merge(path, columns, key, new_rows, human_columns):
    """Keep what a human already typed for rows that already exist; append rows for new interviews."""
    existing = read_existing(path, key)
    merged = []
    for row in new_rows:
        old = existing.get(row[key])
        if old:
            for col in human_columns:
                if (old.get(col) or "").strip():
                    row[col] = old[col]
        merged.append(row)
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-new", action="store_true", help="ignore existing sheets (human entries in them are NOT kept)")
    args = parser.parse_args()
    if not os.path.exists(DB_PATH):
        sys.exit(f"Database not found: {DB_PATH}")
    os.makedirs(OUT_DIR, exist_ok=True)
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    interviews = db.execute(
        "select i.id, i.mode, jd.title as role, c.cv_file_name as cv_file from interviews i "
        "join job_descriptions jd on jd.id=i.job_description_id join candidates c on c.id=i.candidate_id "
        "where i.status='completed' order by i.id").fetchall()
    answer_rows_blind, answer_rows_full, interview_rows_blind, interview_rows_full = [], [], [], []
    for iv in interviews:
        steps = db.execute(
            "select iq.question_order as pos, q.question, q.category, a.id as answer_id, a.answer_text, e.dimensions, e.score "
            "from interview_questions iq join questions q on q.id=iq.question_id "
            "left join answers a on a.interview_question_id=iq.id left join evaluations e on e.answer_id=a.id "
            "where iq.interview_id=? order by iq.question_order", (iv["id"],)).fetchall()
        main_no, follow = 0, 0
        count = 0
        for s in steps:
            if s["category"] == "follow_up":
                follow += 1
                position = f"Q{main_no} follow-up {follow}"
            else:
                main_no, follow = main_no + 1, 0
                position = f"Q{main_no}"
            if s["answer_id"] is None or s["dimensions"] is None:
                continue          # unanswered, or answered before 4D scoring existed: nothing to compare
            try:
                dims = json.loads(s["dimensions"])
                ai = {d: dims[d]["score"] for d in DIMS}
            except (ValueError, KeyError, TypeError):
                continue
            ref = f"a{s['answer_id']}"
            count += 1
            answer_rows_blind.append({"answer_ref": ref, "interview_id": iv["id"], "job_role": iv["role"], "interview_mode": iv["mode"],
                                      "candidate_cv_file": iv["cv_file"] or "", "position": position, "question": s["question"],
                                      "candidate_answer": s["answer_text"]})
            answer_rows_full.append({"answer_ref": ref, "interview_id": iv["id"], "position": position, "question": s["question"],
                                     **{f"ai_{d}": ai[d] for d in DIMS}, "ai_overall_0_10": s["score"]})
        rec = db.execute("select recommendation, confidence from recommendations where interview_id=? order by id desc limit 1", (iv["id"],)).fetchone()
        scores = [r["ai_overall_0_10"] for r in answer_rows_full if r["interview_id"] == iv["id"]]
        interview_rows_blind.append({"interview_id": iv["id"], "job_role": iv["role"], "interview_mode": iv["mode"], "answers_in_interview": count})
        interview_rows_full.append({"interview_id": iv["id"],
                                    "ai_overall_score_0_10": int(sum(scores) / len(scores) + 0.5) if scores else "",
                                    "ai_recommendation": rec["recommendation"] if rec else "", "ai_confidence": rec["confidence"] if rec else ""})
    db.close()

    files = [("review_sheet_BLIND.csv", ANSWER_BLIND, "answer_ref", answer_rows_blind, HUMAN_DIMS + ["human_comment", "reviewer"]),
             ("review_sheet_with_ai.csv", ANSWER_FULL, "answer_ref", answer_rows_full, HUMAN_DIMS + ["human_overall_0_10_derived", "disagreement_explanation"]),
             ("interview_sheet_BLIND.csv", INTERVIEW_BLIND, "interview_id", interview_rows_blind, ["human_recommendation", "human_rationale", "reviewer"]),
             ("interview_sheet_with_ai.csv", INTERVIEW_FULL, "interview_id", interview_rows_full, ["human_recommendation", "disagreement_explanation"])]
    for name, columns, key, rows, human_cols in files:
        path = os.path.join(OUT_DIR, name)
        for row in rows:
            row[key] = str(row[key])
        out = rows if args.force_new else merge(path, columns, key, rows, human_cols)
        write(path, columns, out)
    print(f"Completed interviews found: {len(interviews)}  |  answers exported: {len(answer_rows_blind)}")
    if len(interviews) < 5:
        print(f"NOTE: the experiment needs at least 5 completed interviews; only {len(interviews)} exist. Complete more interviews in the app, "
              "then run this script again (it merges, keeping anything already typed).")
    print(f"Sheets are in {OUT_DIR}")


if __name__ == "__main__":
    main()
