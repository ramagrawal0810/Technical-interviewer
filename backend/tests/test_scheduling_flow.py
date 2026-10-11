"""Scheduling workflow, CV rules, derived status and overall-4D tests.

Runs against an isolated temporary SQLite database (never the real one) and never calls
OpenAI: the CV question generators are replaced with stubs.

Run from backend/:  venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import models
from app.db.database import Base, get_db
from app.main import app
from app.schemas import DimensionEvaluation
from app.services.evaluation_aggregator import aggregate_overall_evaluation
from app.services.interview_status import compute_display_status

CV_TEXT = "Experienced backend engineer. " * 12  # > 100 chars, passes validate_cv_text
PDF = ("cv.pdf", b"%PDF-1.4 stub", "application/pdf")
DIM_KEYS = ["technical_skills", "domain_understanding", "problem_solving", "communication_clarity"]


def dims(ts, du, ps, cc):
    return {
        key: {"score": score, "justification": f"{key} justification text", "quote": f"{key} quote"}
        for key, score in zip(DIM_KEYS, (ts, du, ps, cc))
    }


def dim_eval(ts, du, ps, cc):
    return DimensionEvaluation(**dims(ts, du, ps, cc))


def stub_cv_questions(*args, **kwargs):
    return [{"text": f"CV question {i}", "difficulty": "medium"} for i in range(5)]


def stub_hybrid_questions(*args, **kwargs):
    return [{"text": f"Hybrid question {i}", "difficulty": "medium"} for i in range(5)]


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

        with self.Session() as db:
            self.aarav = models.Candidate(name="Aarav Sharma", email="aarav@example.com", skills="py", experience_years=3, resume_summary="x")
            self.priya = models.Candidate(name="Priya Patel", email="priya@example.com", skills="js", experience_years=2, resume_summary="y")
            self.job = models.JobDescription(title="Backend Engineer", description="Build APIs", required_skills="python")
            db.add_all([self.aarav, self.priya, self.job])
            db.add_all([
                models.Question(question=f"Bank question {i}", category="general", difficulty="medium", job_type="Backend Engineer")
                for i in range(6)
            ])
            db.commit()
            self.aarav_id, self.priya_id, self.job_id = self.aarav.id, self.priya.id, self.job.id

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()
        os.remove(self.db_path)

    # ---- helpers ----
    def grant_consent(self, interview_id, granted=True):
        version = self.client.get("/api/data-handling").json()["notice_version"]
        return self.client.post(f"/api/interviews/{interview_id}/consent", json={"granted": granted, "notice_version": version})

    def schedule(self, candidate_id=None, mode="question_bank", cv_text=None, consent=True):
        body = {"candidate_id": candidate_id or self.aarav_id, "job_description_id": self.job_id, "mode": mode}
        if cv_text is not None:
            body["cv_text"] = cv_text
        r = self.client.post("/api/interviews", json=body)
        if consent and r.status_code == 201:       # most tests are about what happens after the candidate has consented
            self.assertEqual(self.grant_consent(r.json()["id"]).status_code, 200)
        return r

    def upload_cv(self, interview_id):
        with mock.patch("app.services.cv_processor.extract_text_from_pdf", return_value=CV_TEXT):
            return self.client.post(f"/api/interviews/{interview_id}/upload-cv", files={"file": PDF})

    def start(self, interview_id, **kwargs):
        with mock.patch("app.services.cv_processor.generate_cv_driven_questions", side_effect=stub_cv_questions), \
                mock.patch("app.services.cv_processor.generate_hybrid_questions", side_effect=stub_hybrid_questions):
            return self.client.post(f"/api/interviews/{interview_id}/start", **kwargs)

    def seed_completed_interview(self, per_question, recommendation="Hire", human_review=None):
        """per_question: list of (legacy_score, dimensions_value) where dimensions_value is a dict/str/None."""
        with self.Session() as db:
            interview = models.Interview(candidate_id=self.aarav_id, job_description_id=self.job_id, status="completed", mode="question_bank")
            db.add(interview)
            db.flush()
            for order, (score, dimensions) in enumerate(per_question, 1):
                question = db.query(models.Question).order_by(models.Question.id).all()[order - 1]
                iq = models.InterviewQuestion(interview_id=interview.id, question_id=question.id, question_order=order)
                db.add(iq)
                db.flush()
                answer = models.Answer(interview_question_id=iq.id, answer_text=f"answer {order}")
                db.add(answer)
                db.flush()
                stored = dimensions if isinstance(dimensions, str) or dimensions is None else json.dumps(dimensions)
                db.add(models.Evaluation(answer_id=answer.id, dimensions=stored, score=score, strengths="s", weaknesses="w", feedback="f", confidence=0.7))
            if recommendation:
                db.add(models.Recommendation(interview_id=interview.id, recommendation=recommendation, confidence="Medium", reason="because"))
            if human_review:
                db.add(models.HumanReview(
                    interview_id=interview.id, interviewer_id="demo-interviewer", interviewer_name="Demo Interviewer",
                    ai_recommendation=recommendation, decision=recommendation, decision_type="ACCEPT",
                ))
            db.commit()
            return interview.id


class SchedulingTests(ApiTestCase):
    def test_schedule_question_bank_interview(self):
        r = self.schedule()
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["status"], "not_started")
        self.assertEqual(body["display_status"], "Scheduled")
        self.assertEqual(body["job_title"], "Backend Engineer")
        self.assertEqual(body["candidate_name"], "Aarav Sharma")
        self.assertEqual(body["mode"], "question_bank")

    def test_cv_modes_can_be_scheduled_without_cv(self):
        for mode in ("cv_driven", "hybrid"):
            r = self.schedule(mode=mode)
            self.assertEqual(r.status_code, 201, mode)
            self.assertIsNone(r.json()["cv_text"])

    def test_blank_cv_text_is_stored_as_missing(self):
        r = self.schedule(mode="cv_driven", cv_text="   ")
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(r.json()["cv_text"])

    def test_scheduling_still_validates_inputs(self):
        self.assertEqual(self.schedule(mode="telepathy").status_code, 400)
        self.assertEqual(self.schedule(candidate_id=9999).status_code, 400)
        r = self.client.post("/api/interviews", json={"candidate_id": self.aarav_id, "job_description_id": 9999, "mode": "hybrid"})
        self.assertEqual(r.status_code, 400)

    def test_legacy_payload_with_cv_text_still_works(self):
        r = self.schedule(mode="hybrid", cv_text=CV_TEXT)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["cv_text"], CV_TEXT)

    def test_candidate_sees_only_their_interviews(self):
        mine = self.schedule(candidate_id=self.aarav_id).json()["id"]
        theirs = self.schedule(candidate_id=self.priya_id, mode="hybrid").json()["id"]

        aarav_list = self.client.get("/api/interviews", params={"candidate_id": self.aarav_id}).json()
        priya_list = self.client.get("/api/interviews", params={"candidate_id": self.priya_id}).json()
        self.assertEqual([i["id"] for i in aarav_list], [mine])
        self.assertEqual([i["id"] for i in priya_list], [theirs])
        self.assertEqual(self.client.get("/api/interviews", params={"candidate_id": 9999}).json(), [])
        self.assertEqual(len(self.client.get("/api/interviews").json()), 2)

    def test_status_filter_and_aliases(self):
        scheduled = self.schedule().json()["id"]
        in_progress = self.schedule().json()["id"]
        self.assertEqual(self.start(in_progress).status_code, 200)
        completed = self.seed_completed_interview([(5, None)])
        reviewed = self.seed_completed_interview([(5, None)], human_review=True)

        def ids(status):
            r = self.client.get("/api/interviews", params={"status": status})
            self.assertEqual(r.status_code, 200, status)
            return {i["id"] for i in r.json()}

        self.assertEqual(ids("not_started"), {scheduled})
        self.assertEqual(ids("scheduled"), {scheduled})
        self.assertEqual(ids("Scheduled"), {scheduled})
        self.assertEqual(ids("in_progress"), {in_progress})
        self.assertEqual(ids("completed"), {completed, reviewed})
        self.assertEqual(ids("reviewed"), {reviewed})
        self.assertEqual(self.client.get("/api/interviews", params={"status": "bogus"}).status_code, 400)

        combined = self.client.get("/api/interviews", params={"candidate_id": self.aarav_id, "status": "completed"}).json()
        self.assertEqual({i["id"] for i in combined}, {completed, reviewed})

    def test_get_single_interview_has_derived_fields(self):
        iid = self.schedule().json()["id"]
        body = self.client.get(f"/api/interviews/{iid}").json()
        self.assertEqual((body["display_status"], body["job_title"], body["candidate_name"]), ("Scheduled", "Backend Engineer", "Aarav Sharma"))


class StartAndCvTests(ApiTestCase):
    def test_question_bank_starts_without_cv(self):
        iid = self.schedule().json()["id"]
        r = self.start(iid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "in_progress")
        self.assertEqual(self.client.get(f"/api/interviews/{iid}").json()["display_status"], "In Progress")

    def test_cv_driven_requires_cv(self):
        iid = self.schedule(mode="cv_driven").json()["id"]
        r = self.start(iid)
        self.assertEqual(r.status_code, 400)
        self.assertIn("CV Driven", r.json()["detail"])
        self.assertIn("CV", r.json()["detail"])
        self.assertEqual(self.client.get(f"/api/interviews/{iid}").json()["status"], "not_started")

    def test_hybrid_requires_cv(self):
        iid = self.schedule(mode="hybrid").json()["id"]
        r = self.start(iid)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Hybrid", r.json()["detail"])
        self.assertEqual(self.client.get(f"/api/interviews/{iid}").json()["status"], "not_started")

    def test_start_cannot_override_mode(self):
        iid = self.schedule(mode="cv_driven").json()["id"]
        r = self.start(iid, json={"mode": "question_bank"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get(f"/api/interviews/{iid}").json()["mode"], "cv_driven")

    def test_interviewer_uploaded_cv_is_reused_at_candidate_start(self):
        for mode, stub_name in (("cv_driven", "generate_cv_driven_questions"), ("hybrid", "generate_hybrid_questions")):
            iid = self.schedule(mode=mode).json()["id"]
            self.assertEqual(self.upload_cv(iid).status_code, 200)
            with mock.patch(f"app.services.cv_processor.{stub_name}", side_effect=stub_cv_questions) as generator:
                r = self.client.post(f"/api/interviews/{iid}/start")
            self.assertEqual(r.status_code, 200, f"{mode}: {r.text}")
            self.assertEqual(r.json()["status"], "in_progress")
            self.assertTrue(generator.called)
            self.assertIn(CV_TEXT, [a for a in generator.call_args.args if isinstance(a, str)], mode)

    def test_candidate_uploaded_cv_enables_start(self):
        for mode in ("cv_driven", "hybrid"):
            iid = self.schedule(mode=mode).json()["id"]
            self.assertEqual(self.start(iid).status_code, 400, mode)
            self.assertEqual(self.upload_cv(iid).status_code, 200, mode)
            self.assertEqual(self.start(iid).status_code, 200, mode)

    def test_cv_upload_only_before_start(self):
        iid = self.schedule(mode="cv_driven", cv_text=CV_TEXT).json()["id"]
        self.assertEqual(self.start(iid).status_code, 200)
        r = self.upload_cv(iid)
        self.assertEqual(r.status_code, 400)
        self.assertIn("before the interview starts", r.json()["detail"])

        done = self.seed_completed_interview([(5, None)])
        self.assertEqual(self.upload_cv(done).status_code, 400)

    def test_cv_upload_rules_still_apply(self):
        qb = self.schedule(mode="question_bank").json()["id"]
        self.assertEqual(self.upload_cv(qb).status_code, 400)

        cv = self.schedule(mode="cv_driven").json()["id"]
        r = self.client.post(f"/api/interviews/{cv}/upload-cv", files={"file": ("cv.txt", b"x", "text/plain")})
        self.assertEqual(r.status_code, 400)
        with mock.patch("app.services.cv_processor.extract_text_from_pdf", return_value="too short"):
            r = self.client.post(f"/api/interviews/{cv}/upload-cv", files={"file": PDF})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.post("/api/interviews/9999/upload-cv", files={"file": PDF}).status_code, 404)

    def test_cannot_start_twice(self):
        iid = self.schedule().json()["id"]
        self.assertEqual(self.start(iid).status_code, 200)
        self.assertEqual(self.start(iid).status_code, 400)


class StatusMappingTests(ApiTestCase):
    def test_display_status_mapping(self):
        self.assertEqual(compute_display_status("not_started", False), "Scheduled")
        self.assertEqual(compute_display_status("in_progress", False), "In Progress")
        self.assertEqual(compute_display_status("completed", False), "Completed")
        self.assertEqual(compute_display_status("completed", True), "Reviewed")
        self.assertEqual(compute_display_status("in_progress", True), "In Progress")

    def test_display_status_through_api(self):
        completed = self.seed_completed_interview([(5, None)])
        reviewed = self.seed_completed_interview([(5, None)], human_review=True)
        listing = {i["id"]: i["display_status"] for i in self.client.get("/api/interviews").json()}
        self.assertEqual(listing[completed], "Completed")
        self.assertEqual(listing[reviewed], "Reviewed")
        self.assertEqual(self.client.get(f"/api/interviews/{completed}/review-data").json()["display_status"], "Completed")
        self.assertEqual(self.client.get(f"/api/interviews/{reviewed}/review-data").json()["display_status"], "Reviewed")

    def test_no_new_stored_status_values(self):
        scheduled = self.schedule().json()["id"]
        reviewed = self.seed_completed_interview([(5, None)], human_review=True)
        with self.Session() as db:
            stored = {i.id: i.status for i in db.query(models.Interview).all()}
        self.assertEqual(stored[scheduled], "not_started")
        self.assertEqual(stored[reviewed], "completed")


class AggregatorTests(unittest.TestCase):
    def test_mean_per_dimension_rounded_half_up(self):
        result = aggregate_overall_evaluation([dim_eval(4, 2, 5, 1), dim_eval(3, 3, 5, 2)], total_questions=2)
        self.assertEqual(
            (result.technical_skills, result.domain_understanding, result.problem_solving, result.communication_clarity),
            (3.5, 2.5, 5.0, 1.5),
        )
        # 3,3,3,4 -> 3.25 must round half UP to 3.3 (banker's rounding would give 3.2)
        result = aggregate_overall_evaluation([dim_eval(3, 1, 1, 1), dim_eval(3, 1, 1, 1), dim_eval(3, 1, 1, 1), dim_eval(4, 1, 1, 1)], 4)
        self.assertEqual(result.technical_skills, 3.3)
        # 4,4,5 -> 4.333 -> 4.3
        result = aggregate_overall_evaluation([dim_eval(4, 1, 1, 1), dim_eval(4, 1, 1, 1), dim_eval(5, 1, 1, 1)], 3)
        self.assertEqual(result.technical_skills, 4.3)

    def test_counts_and_skipping_of_missing_evaluations(self):
        result = aggregate_overall_evaluation([dim_eval(5, 5, 5, 5), None, dim_eval(3, 3, 3, 3), None, None], total_questions=5)
        self.assertEqual((result.evaluated_questions, result.total_questions), (2, 5))
        self.assertEqual(result.technical_skills, 4.0)

    def test_out_of_range_scores_are_not_valid_4d(self):
        result = aggregate_overall_evaluation([dim_eval(5, 5, 5, 5), dim_eval(0, 3, 3, 3), dim_eval(3, 3, 3, 6)], total_questions=3)
        self.assertEqual(result.evaluated_questions, 1)
        self.assertEqual(result.technical_skills, 5.0)

    def test_no_valid_evaluation_returns_none(self):
        self.assertIsNone(aggregate_overall_evaluation([], 0))
        self.assertIsNone(aggregate_overall_evaluation([None, None], 2))
        self.assertIsNone(aggregate_overall_evaluation([dim_eval(9, 9, 9, 9)], 1))

    def test_single_question_equals_that_question(self):
        result = aggregate_overall_evaluation([dim_eval(2, 4, 1, 5)], 1)
        self.assertEqual((result.technical_skills, result.domain_understanding, result.problem_solving, result.communication_clarity), (2.0, 4.0, 1.0, 5.0))


class ReviewDataTests(ApiTestCase):
    def test_overall_4d_with_mixed_4d_and_legacy_questions(self):
        iid = self.seed_completed_interview([
            (6, dims(4, 3, 5, 2)),
            (8, dims(5, 3, 4, 4)),
            (4, None),  # evaluated before 4D existed
        ], recommendation="Hire")
        r = self.client.get(f"/api/interviews/{iid}/review-data")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()

        overall = data["overall_evaluation"]
        self.assertEqual(overall["evaluated_questions"], 2)
        self.assertEqual(overall["total_questions"], 3)
        self.assertEqual(
            (overall["technical_skills"], overall["domain_understanding"], overall["problem_solving"], overall["communication_clarity"]),
            (4.5, 3.0, 4.5, 3.0),
        )

        # question-level evidence is unchanged and still present
        qa = data["questions_answers_evaluations"]
        self.assertEqual([q["order"] for q in qa], [1, 2, 3])
        self.assertEqual(qa[0]["evaluation"]["problem_solving"]["score"], 5)
        self.assertEqual(qa[0]["evaluation"]["technical_skills"]["quote"], "technical_skills quote")
        self.assertIsNone(qa[2]["evaluation"])
        self.assertEqual(qa[1]["answer"], "answer 2")

        # existing 0-10 overall score is unchanged: round-half-up mean of the legacy scores
        self.assertEqual(data["ai_assessment"]["overall_score"], 6)
        self.assertEqual(data["ai_assessment"]["recommendation"], "Hire")
        self.assertEqual(data["ai_assessment"]["confidence"], "Medium")
        self.assertEqual(data["ai_assessment"]["reason"], "because")

    def test_no_4d_data_gives_null_overall_but_keeps_ai_assessment(self):
        iid = self.seed_completed_interview([(5, None), (7, None)], recommendation="consider")
        data = self.client.get(f"/api/interviews/{iid}/review-data").json()
        self.assertIsNone(data["overall_evaluation"])
        self.assertEqual(data["ai_assessment"]["overall_score"], 6)
        self.assertEqual(data["ai_assessment"]["recommendation"], "Hold")  # legacy label normalised
        self.assertEqual(data["ai_assessment"]["reason"], "because")
        self.assertEqual(len(data["questions_answers_evaluations"]), 2)

    def test_malformed_dimensions_are_ignored_not_fatal(self):
        iid = self.seed_completed_interview([
            (5, '{"technical_skills": '),                  # truncated JSON
            (5, json.dumps(json.dumps(dims(3, 3, 3, 3)))),  # double-encoded string
            (5, dims(2, 4, 3, 5)),
        ])
        r = self.client.get(f"/api/interviews/{iid}/review-data")
        self.assertEqual(r.status_code, 200, r.text)
        overall = r.json()["overall_evaluation"]
        self.assertEqual((overall["evaluated_questions"], overall["total_questions"]), (1, 3))
        self.assertEqual(overall["technical_skills"], 2.0)

    def test_interview_without_recommendation_is_still_readable(self):
        iid = self.seed_completed_interview([(5, dims(3, 3, 3, 3))], recommendation=None)
        r = self.client.get(f"/api/interviews/{iid}/review-data")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["ai_assessment"]["recommendation"])
        self.assertIsNotNone(r.json()["overall_evaluation"])

    def test_review_only_for_completed_interviews(self):
        iid = self.schedule().json()["id"]
        self.assertEqual(self.client.get(f"/api/interviews/{iid}/review-data").status_code, 400)
        self.assertEqual(self.client.get("/api/interviews/9999/review-data").status_code, 404)


class HumanReviewStillWorksTests(ApiTestCase):
    def test_accept_flow(self):
        iid = self.seed_completed_interview([(8, dims(4, 4, 4, 4))], recommendation="Hire")
        r = self.client.post(f"/api/interviews/{iid}/human-review")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["decision"], r.json()["decision_type"], r.json()["ai_recommendation"]), ("Hire", "ACCEPT", "Hire"))

        data = self.client.get(f"/api/interviews/{iid}/review-data").json()
        self.assertEqual(data["display_status"], "Reviewed")
        self.assertEqual(data["human_review"]["decision"], "Hire")
        self.assertEqual(data["ai_assessment"]["recommendation"], "Hire")  # AI recommendation stays separate
        self.assertEqual(self.client.post(f"/api/interviews/{iid}/human-review").status_code, 400)

    def test_override_flow_keeps_ai_recommendation_separate(self):
        iid = self.seed_completed_interview([(8, dims(4, 4, 4, 4))], recommendation="Hire")
        reason = "Candidate showed exceptional depth in follow-up discussion."
        r = self.client.post(f"/api/interviews/{iid}/human-review/override", json={"decision": "Strong Hire", "override_reason": reason})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["decision"], r.json()["decision_type"], r.json()["override_reason"]), ("Strong Hire", "OVERRIDE", reason))

        data = self.client.get(f"/api/interviews/{iid}/review-data").json()
        self.assertEqual(data["display_status"], "Reviewed")
        self.assertEqual((data["ai_assessment"]["recommendation"], data["human_review"]["decision"]), ("Hire", "Strong Hire"))
        with self.Session() as db:
            self.assertEqual(db.query(models.Recommendation).filter_by(interview_id=iid).one().recommendation, "Hire")

    def test_override_validation_unchanged(self):
        iid = self.seed_completed_interview([(8, dims(4, 4, 4, 4))], recommendation="Hire")
        url = f"/api/interviews/{iid}/human-review/override"
        self.assertEqual(self.client.post(url, json={"decision": "Hire", "override_reason": "x" * 30}).status_code, 400)   # same as AI
        self.assertEqual(self.client.post(url, json={"decision": "No Hire", "override_reason": "short"}).status_code, 400)
        self.assertEqual(self.client.post(url, json={"decision": "No Hire"}).status_code, 400)
        self.assertEqual(self.client.post(url, json={"decision": "Maybe", "override_reason": "x" * 30}).status_code, 400)
        self.assertEqual(self.client.get(f"/api/interviews/{iid}/human-review").status_code, 404)  # nothing was saved


class ExistingDataCompatibilityTests(ApiTestCase):
    def test_legacy_interviews_remain_readable(self):
        legacy = self.seed_completed_interview([(5, None)], recommendation="reject")
        modern = self.seed_completed_interview([(8, dims(5, 4, 4, 5))], recommendation="Strong Hire")
        listing = self.client.get("/api/interviews").json()
        self.assertEqual({i["id"] for i in listing}, {legacy, modern})
        for iid in (legacy, modern):
            self.assertEqual(self.client.get(f"/api/interviews/{iid}").status_code, 200)
            self.assertEqual(self.client.get(f"/api/interviews/{iid}/review-data").status_code, 200)
        self.assertEqual(self.client.get(f"/api/interviews/{legacy}/review-data").json()["ai_assessment"]["recommendation"], "No Hire")

    def test_review_endpoints_do_not_modify_data(self):
        iid = self.seed_completed_interview([(5, dims(3, 3, 3, 3)), (5, None)])
        with self.Session() as db:
            before = [(e.id, e.dimensions, e.score) for e in db.query(models.Evaluation).order_by(models.Evaluation.id)]
        self.client.get(f"/api/interviews/{iid}/review-data")
        self.client.get("/api/interviews")
        with self.Session() as db:
            after = [(e.id, e.dimensions, e.score) for e in db.query(models.Evaluation).order_by(models.Evaluation.id)]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
