"""Candidate exit: Abandoned status, partial interviewer reports, and their safety against repeats and races.

Run from backend/:  venv\\Scripts\\python.exe -m unittest tests.test_abandon -v
"""

import io
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pypdf

from app.db import models
from app.services import retention
from tests.test_adaptive_engine import FRONTEND
from tests.test_report_export import ReportTestCase, Step, collapse
from tests.test_security_consent_retention import ATTACKS


class AbandonBase(ReportTestCase):
    def active_interview(self, answers=("My first answer.",)):
        """A real interview driven through the API: started, with `answers` submitted and evaluated."""
        interview_id = self.schedule(FRONTEND)
        iq = self.start(interview_id)["current_question"]["id"]
        for i, text in enumerate(answers):
            self.answer(interview_id, iq, text)
            if i < len(answers) - 1:
                iq = self.next_question(interview_id)["id"]
        return interview_id, iq

    def abandon(self, interview_id):
        return self.client.post(f"/api/interviews/{interview_id}/abandon")

    def row(self, interview_id):
        with self.Session() as db:
            r = db.get(models.Interview, interview_id)
            return r.status, r.abandoned_at, r.completed_at


class AbandonTests(AbandonBase):
    def test_confirmed_exit_abandons_and_keeps_everything_saved(self):
        interview_id, _ = self.active_interview(("Answer one.", "Answer two."))
        with self.Session() as db:
            before = {n: db.query(t).count() for n, t in models.Base.metadata.tables.items()
                      if n in ("answers", "evaluations", "interview_questions", "content_security_checks")}
            consent = db.get(models.Interview, interview_id).consent_status
        r = self.abandon(interview_id)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["status"], body["display_status"]), ("abandoned", "Abandoned"))
        self.assertTrue(body["abandoned_at"])
        self.assertIsNone(body["completed_at"])                                  # never Completed
        with self.Session() as db:
            after = {n: db.query(t).count() for n, t in models.Base.metadata.tables.items() if n in before}
            self.assertEqual(after, before)
            self.assertEqual(db.get(models.Interview, interview_id).consent_status, consent)
            self.assertEqual(db.query(models.HumanReview).count(), 0)

    def test_repeated_and_concurrent_exit_requests_keep_the_first_timestamp(self):
        interview_id, _ = self.active_interview()
        first = self.abandon(interview_id).json()["abandoned_at"]
        self.assertEqual(self.abandon(interview_id).json()["abandoned_at"], first)
        other, _ = self.active_interview()
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _: self.abandon(other), range(8)))
        self.assertEqual({r.status_code for r in results}, {200})
        self.assertEqual(len({r.json()["abandoned_at"] for r in results}), 1)
        self.assertEqual(self.row(other)[0], "abandoned")

    def test_only_in_progress_interviews_can_be_abandoned(self):
        scheduled = self.schedule(FRONTEND)
        self.assertEqual(self.abandon(scheduled).status_code, 400)
        self.assertEqual(self.row(scheduled)[0], "not_started")
        done = self.seed_interview([Step("Q?", "A.")])
        self.assertEqual(self.abandon(done).status_code, 400)
        self.assertEqual(self.row(done)[0], "completed")
        self.assertEqual(self.abandon(99999).status_code, 404)

    def test_declining_consent_keeps_the_interview_scheduled_not_abandoned(self):
        interview_id = self.schedule(FRONTEND, consent=False)
        self.grant_consent(interview_id, granted=False)
        status = self.client.get(f"/api/interviews/{interview_id}").json()
        self.assertEqual((status["status"], status["display_status"]), ("not_started", "Scheduled"))

    def test_an_abandoned_interview_cannot_be_continued_through_the_api(self):
        interview_id, iq = self.active_interview()
        pending = self.next_question(interview_id)["id"]
        self.abandon(interview_id)
        calls = {
            "answer": self.client.post(f"/api/interviews/{interview_id}/questions/{pending}/answer", json={"answer_text": "late"}),
            "next-question": self.client.post(f"/api/interviews/{interview_id}/next-question"),
            "complete": self.client.post(f"/api/interviews/{interview_id}/complete"),
            "start": self.client.post(f"/api/interviews/{interview_id}/start"),
            "consent": self.grant_consent(interview_id),
        }
        for name, r in calls.items():
            self.assertEqual(r.status_code, 400, f"{name}: {r.text}")
        with self.Session() as db:
            self.assertEqual(db.query(models.Answer).filter_by(interview_question_id=pending).count(), 0)
        self.assertEqual(self.row(interview_id)[0], "abandoned")

    def test_a_saved_but_unevaluated_answer_is_not_evaluated_after_abandoning(self):
        interview_id = self.schedule(FRONTEND)
        iq = self.start(interview_id)["current_question"]["id"]
        answer_id = self.client.post(f"/api/interviews/{interview_id}/questions/{iq}/answer", json={"answer_text": "Saved."}).json()["id"]
        self.abandon(interview_id)
        self.assertEqual(self.client.post(f"/api/answers/{answer_id}/evaluate").status_code, 400)
        self.assertEqual(self.eval_calls, [])
        report = self.report(interview_id)
        self.assertEqual(report["transcript"][0]["evaluation_status"], "not_evaluated")
        self.assertEqual(report["transcript"][0]["answer"], "Saved.")

    def test_the_session_is_readable_but_offers_nothing_to_resume(self):
        interview_id, _ = self.active_interview()
        self.abandon(interview_id)
        s = self.client.get(f"/api/interviews/{interview_id}/session").json()
        self.assertEqual((s["status"], s["display_status"], s["pending"]), ("abandoned", "Abandoned", None))

    def test_the_answer_race_never_leaves_an_answer_after_the_abandon(self):
        interview_id = self.schedule(FRONTEND)
        iq = self.start(interview_id)["current_question"]["id"]
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(lambda: self.client.post(f"/api/interviews/{interview_id}/questions/{iq}/answer", json={"answer_text": "racing"}))
            b = pool.submit(lambda: self.abandon(interview_id))
            a, b = a.result(), b.result()
        self.assertEqual(b.status_code, 200)
        self.assertIn(a.status_code, (200, 201, 400))                            # saved just before, or refused just after
        with self.Session() as db:
            count = db.query(models.Answer).filter_by(interview_question_id=iq).count()
        self.assertEqual(count, 1 if a.status_code in (200, 201) else 0)

    def test_dashboard_lists_it_as_abandoned_and_the_filter_works(self):
        interview_id, _ = self.active_interview()
        self.abandon(interview_id)
        rows = self.client.get("/api/interviews", params={"status": "abandoned"}).json()
        self.assertEqual([(r["id"], r["display_status"]) for r in rows], [(interview_id, "Abandoned")])
        self.assertNotIn(interview_id, [r["id"] for r in self.client.get("/api/interviews", params={"status": "in_progress"}).json()])


class PartialReportTests(AbandonBase):
    def abandoned_with_data(self):
        interview_id, _ = self.active_interview(("React state is data owned by a component.", ATTACKS[0]))
        self.next_question(interview_id)                                          # a third question asked, never answered
        self.abandon(interview_id)
        return interview_id

    def test_partial_report_shows_saved_data_and_marks_it_partial(self):
        interview_id = self.abandoned_with_data()
        r = self.report(interview_id)
        self.assertTrue(r["is_partial"])
        self.assertEqual((r["interview_status"], r["partial_label"]), ("abandoned", "Partial Interview — Abandoned"))
        self.assertTrue(r["abandoned_at"])
        self.assertEqual((r["answered_count"], r["main_questions_asked"]), (2, 3))
        self.assertEqual([q["evaluation_status"] for q in r["transcript"]], ["evaluated", "evaluated", "no_answer"])
        self.assertEqual(r["transcript"][1]["answer"], ATTACKS[0])
        self.assertEqual(r["candidate_name"], "Aarav Sharma")

    def test_no_full_interview_score_or_recommendation_is_generated(self):
        r = self.report(self.abandoned_with_data())
        self.assertIsNone(r["overall_score"])
        self.assertIsNone(r["ai_recommendation"])
        self.assertIsNone(r["overall_assessment"])
        with self.Session() as db:
            self.assertEqual(db.query(models.Recommendation).count(), 0)         # abandoning never triggers a recommendation
        self.assertEqual(self.eval_calls.__len__(), 2)                           # and no extra evaluation call

    def test_an_existing_recommendation_is_labelled_preliminary(self):
        interview_id = self.abandoned_with_data()
        with self.Session() as db:
            db.add(models.Recommendation(interview_id=interview_id, recommendation="Hire", confidence="High", reason="Earlier."))
            db.commit()
        r = self.report(interview_id)
        self.assertEqual(r["ai_recommendation"], "Hire")
        self.assertIn("Preliminary only", r["ai_recommendation_note"])
        md = self.client.get(f"/api/interviews/{interview_id}/report.md").text
        self.assertIn("Preliminary AI recommendation", md)
        self.assertNotIn("AI recommendation (advisory)", md)

    def test_unanswered_questions_are_marked_not_evaluated_in_both_exports(self):
        interview_id = self.abandoned_with_data()
        md = collapse(self.client.get(f"/api/interviews/{interview_id}/report.md").text)
        self.assertIn("Partial Interview", md)
        self.assertIn("Abandoned at", md)
        self.assertIn("No answer was submitted", md)
        pdf = self.client.get(f"/api/interviews/{interview_id}/report.pdf")
        self.assertEqual(pdf.status_code, 200)
        text = collapse("\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf.content)).pages))
        self.assertIn("Partial Interview", text)

    def test_security_results_survive_abandonment_in_the_partial_report(self):
        interview_id = self.abandoned_with_data()
        r = self.report(interview_id)
        self.assertEqual(r["security"]["status"], "suspected")
        self.assertTrue(r["security"]["findings"][0]["source"].startswith("Answer to Question"))
        self.assertIn(ATTACKS[0], r["security"]["findings"][0]["excerpt"])

    def test_a_partial_interview_with_unchecked_answers_is_unknown_not_clean(self):
        interview_id = self.seed_interview([Step("Q?", "An answer.")], status="abandoned", recommendation=None)
        r = self.report(interview_id)
        self.assertTrue(r["is_partial"])
        self.assertEqual(r["security"]["status"], "unknown")

    def test_human_review_is_not_possible_for_an_abandoned_interview(self):
        interview_id = self.abandoned_with_data()
        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/human-review").status_code, 400)
        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/human-review/override",
                                          json={"decision": "No Hire", "override_reason": "x"}).status_code, 400)
        self.assertFalse(self.report(interview_id)["human_review"]["present"])

    def test_completed_reports_are_unchanged(self):
        r = self.report(self.standard_interview())
        self.assertFalse(r["is_partial"])
        self.assertIsNone(r["partial_label"])
        self.assertEqual((r["overall_score"], r["ai_recommendation"]), (6, "Hire"))

    def test_injection_text_does_not_change_the_stored_score(self):
        a, _ = self.active_interview(("Plain answer.",))
        b, _ = self.active_interview((ATTACKS[0],))
        with self.Session() as db:
            scores = [db.query(models.Evaluation).join(models.Answer).join(models.InterviewQuestion)
                      .filter(models.InterviewQuestion.interview_id == i).one().score for i in (a, b)]
        self.assertEqual(scores[0], scores[1])


class AbandonedRetentionTests(ReportTestCase):
    def test_old_abandoned_interviews_are_listed_but_active_ones_never(self):
        old = self.seed_interview([Step("Q?", "A.")], status="abandoned", recommendation=None)
        active = self.seed_interview([Step("Q?", "A.")], status="in_progress", recommendation=None)
        with self.Session() as db:
            db.get(models.Interview, old).abandoned_at = datetime.utcnow() - timedelta(days=400)
            db.get(models.Interview, active).started_at = datetime.utcnow() - timedelta(days=400)
            db.commit()
            self.assertEqual([i.id for i in retention.find_expired_interviews(db, 180)], [old])
            with self.assertRaises(retention.RetentionError):
                retention.delete_interview_data(db, active)


if __name__ == "__main__":
    unittest.main()
