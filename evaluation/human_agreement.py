"""Experiment D: agreement between the AI evaluator and independent human judgement.

This script COMPUTES agreement from scores a real human reviewer typed into the sheets; it never creates or guesses them.
See HUMAN_REVIEW_GUIDE.md for the step-by-step process. Inputs (in evaluation/human_review/, made by export_review_pack.py):

  review_sheet_BLIND.csv + review_sheet_with_ai.csv     answer level: four 1-5 dimension scores per answer
  interview_sheet_BLIND.csv + interview_sheet_with_ai.csv   interview level: overall recommendation per interview

Status rules (nothing is called complete early):
  COMPLETE      >= 5 completed interviews, each with a human recommendation and every one of its answers scored by the human
  PARTIAL       some human judgements exist but the above is not met (numbers are still reported, labelled with their sample size)
  NOT VERIFIED  no human judgement has been entered

Run from the repository root:   backend\\venv\\Scripts\\python.exe evaluation\\human_agreement.py
Output: evaluation/results/human_agreement.json and the experiment-D section of evaluation/RESULTS.md.
"""

import csv
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHEETS = os.path.join(HERE, "human_review")
RESULTS = os.path.join(HERE, "results", "human_agreement.json")
DIMS = ["technical_skills", "domain_understanding", "problem_solving", "communication_clarity"]
LEVELS = ["No Hire", "Hold", "Hire", "Strong Hire"]
MIN_INTERVIEWS = 5
BIG_GAP = 2   # a dimension or overall difference of 2+ points must be explained


def read(name):
    path = os.path.join(SHEETS, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_score(value):
    try:
        score = int(str(value).strip())
    except ValueError:
        return None
    return score if 1 <= score <= 5 else None


def legacy_overall(scores):
    """The application's own 0-10 formula, so the human overall is derived exactly like the AI's."""
    sys.path.insert(0, os.path.join(HERE, "..", "backend"))
    from app.services.llm_evaluation import calculate_legacy_score
    return calculate_legacy_score({d: {"score": s} for d, s in zip(DIMS, scores)})


def pearson(x, y):
    if len(x) < 3 or statistics.pstdev(x) == 0 or statistics.pstdev(y) == 0:
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    return round(sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) * statistics.pstdev(x) * statistics.pstdev(y)), 3)


def analyse():
    blind = {r["answer_ref"]: r for r in read("review_sheet_BLIND.csv")}
    full = {r["answer_ref"]: r for r in read("review_sheet_with_ai.csv")}
    iv_blind = {r["interview_id"]: r for r in read("interview_sheet_BLIND.csv")}
    iv_full = {r["interview_id"]: r for r in read("interview_sheet_with_ai.csv")}

    answers = []
    for ref, row in blind.items():
        human = [to_score(row.get(f"human_{d}")) for d in DIMS]
        if None in human or ref not in full:
            continue
        ai = [int(full[ref][f"ai_{d}"]) for d in DIMS]
        answers.append({"ref": ref, "interview_id": row["interview_id"], "position": row["position"], "ai": ai, "human": human,
                        "ai_overall": int(full[ref]["ai_overall_0_10"]), "human_overall": legacy_overall(human),
                        "reviewer": (row.get("reviewer") or "").strip(), "comment": row.get("human_comment", ""),
                        "explanation": (full[ref].get("disagreement_explanation") or "").strip()})
    n = len(answers)
    per_dim = {}
    for i, d in enumerate(DIMS):
        diffs = [a["ai"][i] - a["human"][i] for a in answers]
        per_dim[d] = ({"n": n, "exact_agreement": round(sum(x == 0 for x in diffs) / n, 3), "within_one_point": round(sum(abs(x) <= 1 for x in diffs) / n, 3),
                       "mean_abs_difference": round(statistics.mean(abs(x) for x in diffs), 3), "mean_signed_difference_ai_minus_human": round(statistics.mean(diffs), 3),
                       "correlation": pearson([a["ai"][i] for a in answers], [a["human"][i] for a in answers])} if n else None)
    overall = None
    if n:
        od = [a["ai_overall"] - a["human_overall"] for a in answers]
        overall = {"n": n, "mean_abs_difference_0_10": round(statistics.mean(abs(x) for x in od), 3), "mean_signed_difference_ai_minus_human": round(statistics.mean(od), 3),
                   "within_two_points": round(sum(abs(x) <= 2 for x in od) / n, 3),
                   "correlation": pearson([a["ai_overall"] for a in answers], [a["human_overall"] for a in answers])}
    disagreements = []
    for a in answers:
        gaps = {d: a["ai"][i] - a["human"][i] for i, d in enumerate(DIMS) if abs(a["ai"][i] - a["human"][i]) >= BIG_GAP}
        if gaps or abs(a["ai_overall"] - a["human_overall"]) >= BIG_GAP:
            disagreements.append({"ref": a["ref"], "interview_id": a["interview_id"], "position": a["position"], "dimension_gaps_ai_minus_human": gaps,
                                  "ai_overall": a["ai_overall"], "human_overall": a["human_overall"], "explanation": a["explanation"] or "MISSING - please explain"})

    interviews = []
    for iid, row in iv_blind.items():
        human_rec = (row.get("human_recommendation") or "").strip()
        ai_rec = (iv_full.get(iid, {}).get("ai_recommendation") or "").strip()
        expected = int(row.get("answers_in_interview") or 0)
        scored = sum(1 for a in answers if a["interview_id"] == iid)
        if human_rec in LEVELS and ai_rec in LEVELS:
            interviews.append({"interview_id": iid, "ai": ai_rec, "human": human_rec, "reviewer": (row.get("reviewer") or "").strip(),
                               "rationale": row.get("human_rationale", ""), "answers_scored": scored, "answers_expected": expected,
                               "explanation": (iv_full[iid].get("disagreement_explanation") or "").strip()})
    m = len(interviews)
    rec_result = None
    if m:
        gap = lambda r: abs(LEVELS.index(r["ai"]) - LEVELS.index(r["human"]))  # noqa: E731
        same_side = lambda r: (r["ai"] in LEVELS[2:]) == (r["human"] in LEVELS[2:])  # noqa: E731
        rec_result = {"n": m, "exact_agreement": round(sum(gap(r) == 0 for r in interviews) / m, 3), "within_one_level": round(sum(gap(r) <= 1 for r in interviews) / m, 3),
                      "same_side_of_hire_line": round(sum(same_side(r) for r in interviews) / m, 3),
                      "disagreements": [{**r, "explanation": r["explanation"] or "MISSING - please explain"} for r in interviews if gap(r) > 0]}

    fully_scored = [r for r in interviews if r["answers_expected"] and r["answers_scored"] >= r["answers_expected"]]
    reviewers = sorted({r["reviewer"] for r in interviews} | {a["reviewer"] for a in answers if a["reviewer"]})
    if not answers and not interviews:
        status = "NOT VERIFIED"
    elif len(fully_scored) >= MIN_INTERVIEWS:
        status = "COMPLETE"
    else:
        status = "PARTIAL"
    return {"status": status, "interviews_with_human_recommendation": m, "interviews_fully_scored_by_human": len(fully_scored),
            "interviews_required": MIN_INTERVIEWS, "answers_scored_by_human": n, "reviewers": reviewers, "dimension_agreement": per_dim,
            "overall_score_agreement": overall, "score_disagreements_2plus_points": disagreements, "recommendation_agreement": rec_result,
            "sheets_completed_in_database_export": len(iv_blind)}


def section_text(r):
    lines = ["## D. Human agreement", "", f"**Status: {r['status']}**", ""]
    if r["status"] == "NOT VERIFIED":
        lines += ["No human scores have been entered, so no agreement figure is reported. Process: `evaluation/HUMAN_REVIEW_GUIDE.md`. "
                  f"The export currently contains {r['sheets_completed_in_database_export']} completed interview(s); at least {r['interviews_required']} are required.", ""]
        return lines
    lines += [f"Interviews with a human recommendation: {r['interviews_with_human_recommendation']} (fully scored by the human: {r['interviews_fully_scored_by_human']}; "
              f"required: {r['interviews_required']}). Answers scored by a human: {r['answers_scored_by_human']}. Reviewer(s): {', '.join(r['reviewers']) or 'not recorded'}.", ""]
    if r["status"] == "PARTIAL":
        lines += ["**This is PARTIAL: the sample is smaller than required, so treat every figure below as indicative only.**", ""]
    if r["dimension_agreement"] and r["answers_scored_by_human"]:
        lines += ["| Dimension | n | exact | within 1 point | mean abs diff | mean (AI - human) | correlation |", "|---|---|---|---|---|---|---|"]
        for d, v in r["dimension_agreement"].items():
            lines.append(f"| {d} | {v['n']} | {v['exact_agreement']} | {v['within_one_point']} | {v['mean_abs_difference']} | {v['mean_signed_difference_ai_minus_human']} | {v['correlation']} |")
        o = r["overall_score_agreement"]
        lines += ["", f"Overall 0-10 score (human value derived with the application's formula): mean abs difference {o['mean_abs_difference_0_10']}, "
                  f"mean (AI - human) {o['mean_signed_difference_ai_minus_human']}, within 2 points {o['within_two_points']}, correlation {o['correlation']}.", ""]
    if r["score_disagreements_2plus_points"]:
        lines += ["Answers where AI and human differ by 2 or more points:", "", "| Answer | Interview | Position | gaps (AI - human) | overall AI / human | explanation |", "|---|---|---|---|---|---|"]
        for d in r["score_disagreements_2plus_points"]:
            lines.append(f"| {d['ref']} | {d['interview_id']} | {d['position']} | {d['dimension_gaps_ai_minus_human']} | {d['ai_overall']} / {d['human_overall']} | {d['explanation']} |")
        lines.append("")
    rec = r["recommendation_agreement"]
    if rec:
        lines += [f"Recommendation agreement over {rec['n']} interview(s): exact {rec['exact_agreement']}, within one level {rec['within_one_level']}, same side of the hire line {rec['same_side_of_hire_line']}.", ""]
        if rec["disagreements"]:
            lines += ["| Interview | AI | Human | explanation |", "|---|---|---|---|"]
            lines += [f"| {d['interview_id']} | {d['ai']} | {d['human']} | {d['explanation']} |" for d in rec["disagreements"]]
            lines.append("")
    return lines


def main():
    result = analyse()
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({k: result[k] for k in ("status", "interviews_with_human_recommendation", "interviews_fully_scored_by_human", "interviews_required", "answers_scored_by_human")}, indent=2))
    missing = [d for d in result["score_disagreements_2plus_points"] if d["explanation"].startswith("MISSING")]
    if missing:
        print(f"{len(missing)} large score disagreement(s) still need an explanation in review_sheet_with_ai.csv (column disagreement_explanation).")
    try:
        sys.path.insert(0, HERE)
        import run_experiments
        run_experiments.rebuild_report()
        print("Updated evaluation/RESULTS.md")
    except Exception as e:  # the JSON is the source of truth; the report can be rebuilt later
        print(f"Could not rebuild RESULTS.md: {type(e).__name__}: {e}")
    return 0 if result["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    sys.exit(main())
