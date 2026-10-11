"""Resuming interrupted interviews, status lifecycle, interviewer progress review, retries and races.

Isolated temp database seeded from the real seed files; only the LLM calls are replaced by doubles
(see tests.test_adaptive_engine.EngineTestCase). Run from backend/:
    venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v
"""

import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app.db import database, models
from app.main import app
from app.services.question_adapter import MAIN_QUESTION_COUNT, MAX_FOLLOW_UPS
from tests.test_adaptive_engine import (
    BACKEND, CV, FRONTEND, FULLSTACK, NEUTRAL, STRONG, VAGUE, EngineTestCase, make_eval,
)


class SessionTestCase(EngineTestCase):
    def session(self, interview_id):
        r = self.client.get(f"/api/interviews/{interview_id}/session")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def progress(self, interview_id):
        r = self.client.get(f"/api/interviews/{interview_id}/progress")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def submit_only(self, interview_id, interview_question_id, text="An answer."):
        r = self.client.post(f"/api/interviews/{interview_id}/questions/{interview_question_id}/answer", json={"answer_text": text})
        self.assertIn(r.status_code, (200, 201), r.text)
        return r.json()

    def evaluate(self, answer_id):
        return self.client.post(f"/api/answers/{answer_id}/evaluate")

    def counts(self, interview_id):
        with self.Session() as db:
            iq_ids = [iq.id for iq in db.query(models.InterviewQuestion).filter_by(interview_id=interview_id)]
            answers = db.query(models.Answer).filter(models.Answer.interview_question_id.in_(iq_ids)).all()
            evaluations = db.query(models.Evaluation).filter(models.Evaluation.answer_id.in_([a.id for a in answers])).count() if answers else 0
            follow_ups = db.query(models.Question).filter_by(source_interview_id=interview_id, category="follow_up").count()
            return {"interviews": db.query(models.Interview).count(), "questions": len(iq_ids), "answers": len(answers),
                    "evaluations": evaluations, "follow_ups": follow_ups, "reviews": db.query(models.HumanReview).count()}

    def drive_via_session(self, interview_id, max_steps=200):
        """A client that re-reads the persisted session before EVERY step (as if it had been interrupted each time)."""
        for _ in range(max_steps):
            s = self.session(interview_id)
            action = s["next_action"]
            if action == "answer":
                self.submit_only(interview_id, s["pending"]["interview_question_id"])
            elif action == "evaluate":
                self.assertEqual(self.evaluate(s["last_answer_id"]).status_code, 201)
            elif action == "next_question":
                if self.next_question(interview_id)["is_complete"]:
                    return
            else:
                self.fail(f"unexpected action {action}")
        self.fail("interview never completed")


class ResumeTests(SessionTestCase):
    def test_leave_and_return_resumes_the_same_interview_at_the_same_question(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])
        nxt = self.next_question(interview_id)                  # question 2 is now waiting for an answer

        before = self.counts(interview_id)
        s = self.session(interview_id)                          # the candidate left, then came back
        self.assertEqual(s["interview_id"], interview_id)
        self.assertEqual((s["status"], s["display_status"], s["next_action"]), ("in_progress", "In Progress", "answer"))
        self.assertEqual((s["pending"]["interview_question_id"], s["pending"]["question_id"]), (nxt["id"], nxt["question_id"]))
        self.assertEqual((s["main_question_number"], s["follow_up_number"], s["total_questions"], s["max_follow_ups"]), (2, 0, 5, 3))
        self.assertEqual(s["pending"]["question"], nxt["question"])
        self.assertEqual(self.counts(interview_id), before, "resuming must not create anything")
        self.assertEqual(self.session(interview_id), s, "resume is read-only and repeatable")

    def test_resume_shows_answered_questions_with_their_answers_and_never_asks_them_again(self):
        interview_id = self.schedule(BACKEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"], "My answer to question one.")
        second = self.next_question(interview_id)
        self.answer(interview_id, second["id"], "My answer to question two.")
        third = self.next_question(interview_id)

        s = self.session(interview_id)
        self.assertEqual([step["state"] for step in s["steps"]], ["evaluated", "evaluated", "pending"])
        self.assertEqual([step["answer"] for step in s["steps"]], ["My answer to question one.", "My answer to question two.", None])
        self.assertEqual(s["main_questions_answered"], 2)
        self.assertEqual(s["pending"]["interview_question_id"], third["id"])
        asked = [step["question_id"] for step in s["steps"]]
        self.assertEqual(len(set(asked)), 3)
        self.assertNotIn(s["pending"]["question_id"], asked[:-1])

    def test_resume_in_the_middle_of_a_follow_up_chain_restores_the_follow_up_history(self):
        self.policy = lambda n, q, a, cv: VAGUE
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])
        fu1 = self.next_question(interview_id)
        self.answer(interview_id, fu1["id"])
        fu2 = self.next_question(interview_id)                 # follow-up 2 is waiting; the candidate refreshes here

        s = self.session(interview_id)
        self.assertEqual((s["main_question_number"], s["follow_up_number"]), (1, 2))
        self.assertTrue(s["pending"]["is_follow_up"])
        self.assertEqual(s["pending"]["interview_question_id"], fu2["id"])
        self.assertEqual([(st["is_follow_up"], st["follow_up_number"], st["state"]) for st in s["steps"]],
                         [(False, 0, "evaluated"), (True, 1, "evaluated"), (True, 2, "pending")])

        # carry on after resuming: the limit of 3 still holds
        self.answer(interview_id, fu2["id"])
        fu3 = self.next_question(interview_id)
        self.assertEqual((fu3["is_follow_up"], fu3["follow_up_number"]), (True, 3))
        self.answer(interview_id, fu3["id"])
        main2 = self.next_question(interview_id)
        self.assertEqual((main2["is_follow_up"], main2["main_question_number"], main2["follow_up_number"]), (False, 2, 0))
        self.assertEqual(len(self.follow_up_calls), 3)

    def test_answer_saved_but_not_evaluated_is_recovered_without_duplicates(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        saved = self.submit_only(interview_id, started["current_question"]["id"])    # ...then the evaluation request never completed
        s = self.session(interview_id)
        self.assertEqual((s["next_action"], s["last_answer_id"], s["steps"][0]["state"]), ("evaluate", saved["id"], "submitted"))
        self.assertIsNone(s["pending"])

        self.assertEqual(self.evaluate(s["last_answer_id"]).status_code, 201)
        self.assertEqual(self.session(interview_id)["next_action"], "next_question")
        self.assertFalse(self.next_question(interview_id)["is_complete"])
        counts = self.counts(interview_id)
        self.assertEqual((counts["answers"], counts["evaluations"], counts["questions"]), (1, 1, 2))

    def test_answer_and_evaluation_saved_but_next_question_failed_is_recovered(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])

        failing = TestClient(app, raise_server_exceptions=False)
        with mock.patch("app.api.interviews.select_question_bank_adaptive", side_effect=RuntimeError("boom")):
            self.assertEqual(failing.post(f"/api/interviews/{interview_id}/next-question").status_code, 500)
        failing.close()

        s = self.session(interview_id)
        self.assertEqual(s["next_action"], "next_question")
        self.assertEqual(self.counts(interview_id)["questions"], 1, "the failed request left no half-created question")
        nxt = self.next_question(interview_id)
        self.assertFalse(nxt["is_complete"])
        counts = self.counts(interview_id)
        self.assertEqual((counts["answers"], counts["evaluations"], counts["questions"]), (1, 1, 2))

    def test_a_client_interrupted_before_every_step_still_finishes_with_the_limits_intact(self):
        self.policy = lambda n, q, a, cv: VAGUE
        interview_id = self.schedule(FULLSTACK)
        self.start(interview_id)
        self.drive_via_session(interview_id)

        s = self.session(interview_id)
        mains = [st for st in s["steps"] if not st["is_follow_up"]]
        self.assertEqual(len(mains), MAIN_QUESTION_COUNT)
        for number in range(1, MAIN_QUESTION_COUNT + 1):
            follow_ups = [st for st in s["steps"] if st["is_follow_up"] and st["main_question_number"] == number]
            self.assertEqual([st["follow_up_number"] for st in follow_ups], [1, 2, 3], number)
        self.assertEqual(len(s["steps"]), MAIN_QUESTION_COUNT * (1 + MAX_FOLLOW_UPS))
        self.assertEqual(len(self.follow_up_calls), MAIN_QUESTION_COUNT * MAX_FOLLOW_UPS)
        self.assertEqual({self.question(st["question_id"])["job_type"] for st in s["steps"]}, {FULLSTACK})
        counts = self.counts(interview_id)
        self.assertEqual((counts["answers"], counts["evaluations"]), (len(s["steps"]), len(s["steps"])))

    def test_session_errors(self):
        self.assertEqual(self.client.get("/api/interviews/9999/session").status_code, 404)
        interview_id = self.schedule(FRONTEND)
        r = self.client.get(f"/api/interviews/{interview_id}/session")
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "Interview has not been started"))

    def test_starting_again_never_creates_a_second_interview_or_question(self):
        interview_id = self.schedule(FRONTEND)
        self.start(interview_id)
        before = self.counts(interview_id)
        r = self.client.post(f"/api/interviews/{interview_id}/start")
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "Interview has already been started"))
        self.assertEqual(self.counts(interview_id), before)

    def test_resume_works_in_cv_and_hybrid_modes_and_keeps_the_role(self):
        for mode in ("cv_driven", "hybrid"):
            interview_id = self.schedule(BACKEND, mode, CV)
            started = self.start(interview_id)
            self.answer(interview_id, started["current_question"]["id"])
            nxt = self.next_question(interview_id)
            s = self.session(interview_id)
            self.assertEqual((s["mode"], s["pending"]["question_id"], s["job_title"]), (mode, nxt["question_id"], BACKEND), mode)
            self.assertEqual(self.question(s["pending"]["question_id"])["job_type"], BACKEND)


class DuplicateProtectionTests(SessionTestCase):
    def test_repeating_each_request_creates_nothing_extra(self):
        self.policy = lambda n, q, a, cv: VAGUE
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        iq = started["current_question"]["id"]

        first, again = self.submit_only(interview_id, iq, "same"), self.submit_only(interview_id, iq, "same")
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(self.evaluate(first["id"]).status_code, 201)
        repeat = self.evaluate(first["id"])
        self.assertEqual((repeat.status_code, repeat.json()["detail"]), (400, "Answer has already been evaluated"))
        self.assertEqual(len(self.eval_calls), 1, "the second evaluate must not call the LLM again")

        a, b, c = (self.next_question(interview_id) for _ in range(3))
        self.assertEqual({a["id"], b["id"], c["id"]}, {a["id"]})
        self.assertEqual(len(self.follow_up_calls), 1, "repeating next-question must not generate another follow-up")
        counts = self.counts(interview_id)
        self.assertEqual((counts["answers"], counts["evaluations"], counts["questions"], counts["follow_ups"]), (1, 1, 2, 1))

    def test_concurrent_next_question_requests_create_exactly_one_question(self):
        self.policy = lambda n, q, a, cv: VAGUE
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])

        def call(_):
            with TestClient(app) as client:
                r = client.post(f"/api/interviews/{interview_id}/next-question")
                return r.status_code, r.json()["id"]

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(call, range(6)))
        self.assertEqual({status for status, _ in results}, {200})
        self.assertEqual(len({question_id for _, question_id in results}), 1)
        counts = self.counts(interview_id)
        self.assertEqual((counts["questions"], counts["follow_ups"]), (2, 1))
        self.assertEqual(len(self.follow_up_calls), 1)

    def test_concurrent_answer_and_evaluation_requests_create_one_each(self):
        interview_id = self.schedule(BACKEND)
        started = self.start(interview_id)
        iq = started["current_question"]["id"]

        def submit(_):
            with TestClient(app) as client:
                r = client.post(f"/api/interviews/{interview_id}/questions/{iq}/answer", json={"answer_text": "racing"})
                return r.status_code, r.json()["id"]

        with ThreadPoolExecutor(max_workers=6) as pool:
            submitted = list(pool.map(submit, range(6)))
        self.assertTrue({status for status, _ in submitted} <= {200, 201})
        self.assertEqual(sum(status == 201 for status, _ in submitted), 1, "exactly one request created the answer")
        answer_id = submitted[0][1]
        self.assertEqual(len({i for _, i in submitted}), 1)

        def evaluate(_):
            with TestClient(app) as client:
                return client.post(f"/api/answers/{answer_id}/evaluate").status_code

        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(evaluate, range(6)))
        self.assertEqual(sorted(statuses), [201] + [400] * 5)
        self.assertEqual(len(self.eval_calls), 1)
        counts = self.counts(interview_id)
        self.assertEqual((counts["answers"], counts["evaluations"]), (1, 1))

    def test_the_database_itself_rejects_duplicates(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        iq = started["current_question"]["id"]
        answer = self.submit_only(interview_id, iq)
        self.assertEqual(self.evaluate(answer["id"]).status_code, 201)
        with self.Session() as db:
            question_id = db.query(models.InterviewQuestion).get(iq).question_id
            for duplicate in (
                models.Answer(interview_question_id=iq, answer_text="dup"),
                models.Evaluation(answer_id=answer["id"], score=1, strengths="s", weaknesses="w", feedback="f", confidence=0.5),
                models.InterviewQuestion(interview_id=interview_id, question_id=question_id, question_order=1),
            ):
                db.add(duplicate)
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()

    def test_unique_index_migration_is_safe_on_existing_data(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        raw = sqlite3.connect(path)
        raw.executescript("""
            CREATE TABLE answers (id INTEGER PRIMARY KEY, interview_question_id INTEGER NOT NULL, answer_text TEXT, created_at DATETIME);
            CREATE TABLE evaluations (id INTEGER PRIMARY KEY, answer_id INTEGER NOT NULL, score INTEGER);
            CREATE TABLE interview_questions (id INTEGER PRIMARY KEY, interview_id INTEGER NOT NULL, question_id INTEGER, question_order INTEGER NOT NULL);
            INSERT INTO answers (interview_question_id, answer_text) VALUES (1, 'a'), (1, 'duplicate of a'), (2, 'b');
            INSERT INTO evaluations (answer_id, score) VALUES (1, 5), (2, 6);
            INSERT INTO interview_questions (interview_id, question_id, question_order) VALUES (1, 1, 1), (1, 2, 2);
        """)
        raw.commit()
        raw.close()
        engine = create_engine(f"sqlite:///{path}")
        try:
            with mock.patch.object(database, "engine", engine):
                database._migrate_integrity_indexes()            # answers contain a duplicate: skipped, nothing deleted
                database._migrate_integrity_indexes()            # and running it again is harmless
            indexes = {table: {i["name"] for i in inspect(engine).get_indexes(table)}
                       for table in ("answers", "evaluations", "interview_questions")}
            self.assertNotIn("uq_answers_interview_question", indexes["answers"])
            self.assertIn("uq_evaluations_answer", indexes["evaluations"])
            self.assertIn("uq_interview_questions_order", indexes["interview_questions"])
            with engine.connect() as conn:
                self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM answers").scalar(), 3)
        finally:
            engine.dispose()
            os.remove(path)


class StatusLifecycleTests(SessionTestCase):
    def status_of(self, interview_id):
        return self.client.get(f"/api/interviews/{interview_id}").json()["display_status"]

    def test_status_follows_real_progress_and_is_the_same_on_every_endpoint(self):
        interview_id = self.schedule(FRONTEND)
        self.assertEqual(self.status_of(interview_id), "Scheduled")
        started = self.start(interview_id)
        self.assertEqual(self.status_of(interview_id), "In Progress")
        self.answer(interview_id, started["current_question"]["id"])         # leaving now must not change anything
        self.next_question(interview_id)

        listed = {i["id"]: i for i in self.client.get("/api/interviews").json()}[interview_id]
        by_candidate = {i["id"]: i for i in self.client.get("/api/interviews", params={"candidate_id": self.candidate_id}).json()}[interview_id]
        by_status = {i["id"] for i in self.client.get("/api/interviews", params={"status": "in_progress"}).json()}
        self.assertEqual((listed["display_status"], listed["status"]), ("In Progress", "in_progress"))
        self.assertEqual(by_candidate["display_status"], "In Progress")
        self.assertIn(interview_id, by_status)
        self.assertEqual(self.session(interview_id)["display_status"], "In Progress")
        self.assertEqual(self.progress(interview_id)["interview"]["display_status"], "In Progress")

    def test_an_unfinished_interview_cannot_be_completed(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])
        r = self.client.post(f"/api/interviews/{interview_id}/complete")        # everything asked so far IS answered and evaluated
        self.assertEqual(r.status_code, 400)
        self.assertIn("not finished", r.json()["detail"])
        self.assertIn("1 of 5", r.json()["detail"])
        self.assertEqual(self.status_of(interview_id), "In Progress")

    def test_completing_with_an_unanswered_question_is_refused(self):
        interview_id = self.schedule(FRONTEND)
        self.start(interview_id)
        r = self.client.post(f"/api/interviews/{interview_id}/complete")
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "Not all questions have been answered"))
        self.assertEqual(self.status_of(interview_id), "In Progress")

    def test_finishing_all_main_questions_completes_the_interview(self):
        interview_id = self.schedule(BACKEND)
        self.start(interview_id)
        self.drive_via_session(interview_id)
        self.assertEqual(self.status_of(interview_id), "In Progress", "answering everything does not complete it by itself")
        r = self.client.post(f"/api/interviews/{interview_id}/complete")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.status_of(interview_id), "Completed")
        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/complete").status_code, 400)
        done = self.session(interview_id)
        self.assertEqual((done["next_action"], done["pending"], done["display_status"]), ("complete", None, "Completed"))
        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/start").status_code, 400)

    def test_the_interview_ends_early_only_when_the_roles_questions_run_out(self):
        with self.Session() as db:        # leave the Backend role with just 2 usable questions
            for q in db.query(models.Question).filter_by(job_type=BACKEND).order_by(models.Question.id).all()[2:]:
                q.is_active = False
            db.commit()
        interview_id = self.schedule(BACKEND)
        self.start(interview_id)
        self.drive_via_session(interview_id)
        self.assertEqual(len(self.session(interview_id)["steps"]), 2)
        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/complete").status_code, 200)

    def test_a_client_cannot_create_an_interview_in_any_state_but_scheduled(self):
        r = self.client.post("/api/interviews", json={
            "candidate_id": self.candidate_id, "job_description_id": self.job_ids[FRONTEND],
            "status": "completed", "started_at": "2026-01-01T00:00:00", "completed_at": "2026-01-02T00:00:00"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.json()["status"], r.json()["display_status"], r.json()["started_at"], r.json()["completed_at"]),
                         ("not_started", "Scheduled", None, None))

    def test_reviewed_still_follows_a_completed_interview_with_a_human_review(self):
        interview_id = self.schedule(FRONTEND)
        self.start(interview_id)
        self.drive_via_session(interview_id)
        self.client.post(f"/api/interviews/{interview_id}/complete")
        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/human-review").status_code, 201)
        self.assertEqual(self.status_of(interview_id), "Reviewed")


class ProgressReviewTests(SessionTestCase):
    def test_a_just_started_interview_has_a_valid_empty_progress_screen(self):
        interview_id = self.schedule(FRONTEND, "question_bank")
        self.start(interview_id)
        p = self.progress(interview_id)
        self.assertEqual((p["interview"]["candidate_name"], p["interview"]["job_title"], p["interview"]["mode"],
                          p["interview"]["display_status"]), (self.candidate_name(), FRONTEND, "question_bank", "In Progress"))
        self.assertEqual((p["answers_submitted"], p["answers_evaluated"], p["main_questions_asked"], p["main_questions_answered"],
                          p["follow_ups_asked"], p["total_questions"], p["max_follow_ups"]), (0, 0, 1, 0, 0, 5, 3))
        self.assertEqual(p["pending_question"]["state"], "pending")
        self.assertIsNone(p["pending_question"]["answer"])
        self.assertIsNone(p["pending_question"]["evaluation"])
        self.assertIsNone(p["pending_question"]["score"])
        self.assertEqual(len(p["main_questions"]), 1)
        self.assertEqual(p["main_questions"][0]["follow_ups"], [])
        self.assertIsNotNone(p["interview"]["started_at"])
        self.assertIsNotNone(p["last_activity_at"])
        self.assertIsNone(p["interview"]["completed_at"])

    def candidate_name(self):
        with self.Session() as db:
            return db.query(models.Candidate).get(self.candidate_id).name

    def test_partial_progress_groups_follow_ups_under_their_main_question(self):
        self.policy = lambda n, q, a, cv: VAGUE if n <= 3 else NEUTRAL
        interview_id = self.schedule(BACKEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"], "Answer to the main question.")
        fu1 = self.next_question(interview_id)
        self.answer(interview_id, fu1["id"], "Answer to follow-up one.")
        fu2 = self.next_question(interview_id)
        self.answer(interview_id, fu2["id"], "Answer to follow-up two.")
        fu3 = self.next_question(interview_id)                          # follow-up 3 is pending

        p = self.progress(interview_id)
        self.assertEqual((p["main_questions_asked"], p["main_questions_answered"], p["follow_ups_asked"]), (1, 1, 3))
        self.assertEqual((p["answers_submitted"], p["answers_evaluated"]), (3, 3))
        group = p["main_questions"][0]
        self.assertEqual((group["main_question_number"], group["follow_up_count"], group["max_follow_ups"]), (1, 3, 3))
        self.assertEqual([f["state"] for f in group["follow_ups"]], ["evaluated", "evaluated", "pending"])
        self.assertEqual([f["follow_up_number"] for f in group["follow_ups"]], [1, 2, 3])
        self.assertEqual(group["question"]["answer"], "Answer to the main question.")
        self.assertEqual(group["follow_ups"][0]["answer"], "Answer to follow-up one.")
        # each reason belongs to the evaluation of THAT answer (the first three answers were scripted as vague)
        self.assertEqual([group["question"]["follow_up_reason"]] + [f["follow_up_reason"] for f in group["follow_ups"]],
                         ["vague", "vague", "vague", None])
        self.assertIn("re-render", group["question"]["missing_information"])
        self.assertEqual(p["pending_question"]["interview_question_id"], fu3["id"])
        self.assertIsNone(group["follow_ups"][2]["answer"])

    def test_evaluated_answers_carry_4d_scores_and_evidence(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])
        step = self.progress(interview_id)["main_questions"][0]["question"]
        self.assertEqual(step["state"], "evaluated")
        self.assertEqual(step["score"], 8)
        self.assertEqual(set(step["evaluation"]), {"technical_skills", "domain_understanding", "problem_solving", "communication_clarity"})
        self.assertTrue(all(step["evaluation"][k]["quote"] and 1 <= step["evaluation"][k]["score"] <= 5 for k in step["evaluation"]))
        self.assertIsNotNone(step["answered_at"])
        self.assertIsNotNone(step["evaluated_at"])

    def test_states_are_distinguished_and_nothing_is_invented(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        first_iq = started["current_question"]["id"]
        # an earlier question that never got an answer, followed by the current one
        with self.Session() as db:
            first = db.query(models.InterviewQuestion).get(first_iq)
            spare = db.query(models.Question).filter(models.Question.job_type == FRONTEND, models.Question.id != first.question_id).first()
            db.add(models.InterviewQuestion(interview_id=interview_id, question_id=spare.id, question_order=2))
            db.commit()
        states = [s["state"] for g in self.progress(interview_id)["main_questions"] for s in [g["question"]] + g["follow_ups"]]
        self.assertEqual(states, ["unanswered", "pending"])
        for group in self.progress(interview_id)["main_questions"]:
            self.assertIsNone(group["question"]["answer"])
            self.assertIsNone(group["question"]["evaluation"])
            self.assertIsNone(group["question"]["score"])

        # saved answer, evaluation not yet stored
        self.submit_only(interview_id, first_iq, "Saved answer.")
        step = self.progress(interview_id)["main_questions"][0]["question"]
        self.assertEqual((step["state"], step["answer"], step["evaluation"], step["score"]), ("submitted", "Saved answer.", None, None))

    def test_incomplete_or_malformed_evaluations_do_not_break_the_progress_page(self):
        interview_id = self.schedule(BACKEND)
        started = self.start(interview_id)
        first_answer = self.answer(interview_id, started["current_question"]["id"])["answer_id"]
        second = self.next_question(interview_id)
        self.answer(interview_id, second["id"])
        third = self.next_question(interview_id)
        self.answer(interview_id, third["id"])
        with self.Session() as db:
            evaluations = db.query(models.Evaluation).order_by(models.Evaluation.id).all()
            evaluations[0].dimensions = None                                  # evaluated before 4D existed
            evaluations[1].dimensions = '{"technical_skills": '               # corrupted
            db.commit()
        steps = [g["question"] for g in self.progress(interview_id)["main_questions"]]
        self.assertEqual([s["state"] for s in steps], ["evaluated", "evaluated", "evaluated"])
        self.assertEqual([s["evaluation"] is None for s in steps], [True, True, False])
        self.assertTrue(all(s["score"] is not None for s in steps))
        self.assertIsNotNone(first_answer)

    def test_viewing_progress_changes_nothing_and_creates_no_review(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])
        self.next_question(interview_id)
        before = self.counts(interview_id)
        for _ in range(3):
            self.progress(interview_id)
            self.session(interview_id)
        self.assertEqual(self.counts(interview_id), before)
        self.assertEqual(before["reviews"], 0)
        detail = self.client.get(f"/api/interviews/{interview_id}").json()
        self.assertEqual((detail["status"], detail["display_status"], detail["completed_at"]), ("in_progress", "In Progress", None))

    def test_final_human_review_stays_unavailable_until_the_interview_is_completed(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])
        self.assertEqual(self.client.get(f"/api/interviews/{interview_id}/review-data").status_code, 400)
        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/human-review").status_code, 400)
        r = self.client.post(f"/api/interviews/{interview_id}/human-review/override",
                             json={"decision": "Hire", "override_reason": "Looks promising from what I have seen."})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counts(interview_id)["reviews"], 0)
        self.assertEqual(self.client.get(f"/api/interviews/{interview_id}").json()["display_status"], "In Progress")

    def test_progress_errors_and_scope(self):
        self.assertEqual(self.client.get("/api/interviews/9999/progress").status_code, 404)
        interview_id = self.schedule(FRONTEND)
        r = self.client.get(f"/api/interviews/{interview_id}/progress")
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "Interview has not been started"))

    def test_completed_interviews_keep_the_full_evaluation_and_human_review_workflow(self):
        self.policy = lambda n, q, a, cv: NEUTRAL
        interview_id = self.schedule(BACKEND)
        self.start(interview_id)
        self.drive_via_session(interview_id)
        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/complete").status_code, 200)

        data = self.client.get(f"/api/interviews/{interview_id}/review-data").json()
        self.assertEqual(data["display_status"], "Completed")
        self.assertEqual(len(data["questions_answers_evaluations"]), 5)
        self.assertIsNotNone(data["overall_evaluation"])
        self.assertEqual(data["ai_assessment"]["recommendation"], "Hire")
        p = self.progress(interview_id)                                   # the progress view also works once completed
        self.assertEqual((p["interview"]["display_status"], p["main_questions_answered"], p["pending_question"]), ("Completed", 5, None))

        reason = "Clear reasoning throughout, stronger than the AI scored."
        r = self.client.post(f"/api/interviews/{interview_id}/human-review/override", json={"decision": "Strong Hire", "override_reason": reason})
        self.assertEqual((r.status_code, r.json()["decision_type"], r.json()["ai_recommendation"]), (201, "OVERRIDE", "Hire"))
        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/human-review").status_code, 400)
        self.assertEqual(self.client.get(f"/api/interviews/{interview_id}").json()["display_status"], "Reviewed")


if __name__ == "__main__":
    unittest.main()
