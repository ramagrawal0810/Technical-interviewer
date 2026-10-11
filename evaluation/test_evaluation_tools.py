"""Tests for the evaluation tooling itself (no provider calls, no real data).

The "human" numbers below exist only inside temporary directories created by these tests to check the arithmetic and the status rules.
They are never written to evaluation/human_review/ and are not results.

Run from the repository root:   backend\\venv\\Scripts\\python.exe -m unittest evaluation.test_evaluation_tools -v
"""

import csv
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import human_agreement as ha  # noqa: E402

DIMS = ha.DIMS


def write(directory, name, rows):
    with open(os.path.join(directory, name), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def sheets(directory, interviews, answers_per_interview, human_scores=None, human_rec=None, ai_scores=(4, 4, 4, 4), ai_rec="Hire"):
    blind_a, full_a, blind_i, full_i = [], [], [], []
    for n in range(1, interviews + 1):
        for k in range(answers_per_interview):
            ref = f"a{n}_{k}"
            blind_a.append({"answer_ref": ref, "interview_id": str(n), "position": f"Q{k + 1}",
                            **{f"human_{d}": (str(human_scores[i]) if human_scores else "") for i, d in enumerate(DIMS)}, "human_comment": "", "reviewer": "R" if human_scores else ""})
            full_a.append({"answer_ref": ref, **{f"ai_{d}": str(ai_scores[i]) for i, d in enumerate(DIMS)}, "ai_overall_0_10": "7", "disagreement_explanation": ""})
        blind_i.append({"interview_id": str(n), "answers_in_interview": str(answers_per_interview), "human_recommendation": human_rec or "", "human_rationale": "", "reviewer": "R" if human_rec else ""})
        full_i.append({"interview_id": str(n), "ai_recommendation": ai_rec, "ai_confidence": "High", "disagreement_explanation": ""})
    write(directory, "review_sheet_BLIND.csv", blind_a)
    write(directory, "review_sheet_with_ai.csv", full_a)
    write(directory, "interview_sheet_BLIND.csv", blind_i)
    write(directory, "interview_sheet_with_ai.csv", full_i)


class HumanAgreementTests(unittest.TestCase):
    def run_analysis(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            sheets(tmp, **kwargs)
            original = ha.SHEETS
            ha.SHEETS = tmp
            try:
                return ha.analyse()
            finally:
                ha.SHEETS = original

    def test_blank_sheets_are_not_verified_and_report_no_agreement(self):
        r = self.run_analysis(interviews=5, answers_per_interview=3)
        self.assertEqual(r["status"], "NOT VERIFIED")
        self.assertEqual((r["answers_scored_by_human"], r["interviews_with_human_recommendation"]), (0, 0))
        self.assertIsNone(r["overall_score_agreement"])
        self.assertIsNone(r["recommendation_agreement"])

    def test_fewer_than_five_interviews_is_partial_even_when_fully_scored(self):
        r = self.run_analysis(interviews=4, answers_per_interview=2, human_scores=(4, 4, 4, 4), human_rec="Hire")
        self.assertEqual(r["status"], "PARTIAL")
        self.assertEqual(r["interviews_fully_scored_by_human"], 4)

    def test_five_fully_scored_interviews_is_complete_and_arithmetic_is_right(self):
        r = self.run_analysis(interviews=5, answers_per_interview=2, human_scores=(2, 4, 4, 5), human_rec="Hold", ai_scores=(4, 4, 4, 4))
        self.assertEqual(r["status"], "COMPLETE")
        tech = r["dimension_agreement"]["technical_skills"]
        self.assertEqual((tech["n"], tech["exact_agreement"], tech["mean_signed_difference_ai_minus_human"]), (10, 0.0, 2.0))
        self.assertEqual(r["dimension_agreement"]["problem_solving"]["exact_agreement"], 1.0)
        self.assertEqual(r["dimension_agreement"]["communication_clarity"]["mean_signed_difference_ai_minus_human"], -1.0)
        rec = r["recommendation_agreement"]
        self.assertEqual((rec["n"], rec["exact_agreement"], rec["within_one_level"], rec["same_side_of_hire_line"]), (5, 0.0, 1.0, 0.0))
        self.assertEqual(len(rec["disagreements"]), 5)
        self.assertTrue(all(d["explanation"].startswith("MISSING") for d in rec["disagreements"]))     # unexplained gaps are flagged
        self.assertEqual(len(r["score_disagreements_2plus_points"]), 10)                                # 2-point technical gap on every answer

    def test_a_half_filled_row_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            sheets(tmp, interviews=1, answers_per_interview=1, human_scores=(4, 4, 4, 4), human_rec="Hire")
            path = os.path.join(tmp, "review_sheet_BLIND.csv")
            rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
            rows[0]["human_communication_clarity"] = ""
            write(tmp, "review_sheet_BLIND.csv", rows)
            ha.SHEETS, original = tmp, ha.SHEETS
            try:
                r = ha.analyse()
            finally:
                ha.SHEETS = original
        self.assertEqual(r["answers_scored_by_human"], 0)

    def test_out_of_range_scores_are_ignored(self):
        self.assertIsNone(ha.to_score("0"))
        self.assertIsNone(ha.to_score("6"))
        self.assertIsNone(ha.to_score("abc"))
        self.assertEqual(ha.to_score(" 3 "), 3)

    def test_section_text_never_claims_complete_without_data(self):
        text = "\n".join(ha.section_text(self.run_analysis(interviews=1, answers_per_interview=1)))
        self.assertIn("NOT VERIFIED", text)
        self.assertNotIn("COMPLETE", text)


class StatisticsTests(unittest.TestCase):
    def test_permutation_test_and_effect_size(self):
        import run_experiments as rx
        same = [5, 6, 7, 6, 5, 7, 6, 6]
        self.assertGreater(rx.permutation_p(same, list(reversed(same))), 0.9)
        self.assertLess(rx.permutation_p([1, 1, 2, 1, 2, 1, 1, 2], [6, 7, 6, 7, 7, 6, 7, 6]), 0.01)
        self.assertEqual(rx.cohens_d([3, 3, 3], [3, 3, 3]), 0.0)
        self.assertGreater(rx.cohens_d([6, 7, 8], [1, 2, 3]), 3)


if __name__ == "__main__":
    unittest.main()
