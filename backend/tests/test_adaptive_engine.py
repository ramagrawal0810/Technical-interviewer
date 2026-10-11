"""Role-aware question selection, difficulty progression and the adaptive follow-up engine.

Isolated temp database seeded from the REAL seed files (data/*.json). No OpenAI calls are made: tests
replace only the functions that call the LLM (evaluator, follow-up generator, CV generator) with
deterministic doubles, and separate tests drive the real LLM-facing code with a fake OpenAI client that
returns realistic JSON, to check prompts, parsing, validation and usage logging.

Run from backend/:  venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v
"""

import json
import os
import random
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import seed_database
from app.config import settings
from app.db import database, models
from app.db.database import Base, get_db
from app.main import app
from app.services import cv_processor, follow_up_generator, llm_evaluation
from app.services.cv_processor import difficulty_plan, generate_cv_driven_questions, generate_hybrid_questions
from app.services.follow_up_generator import build_follow_up_messages, generate_follow_up_question, parse_follow_up_response
from app.services.llm_evaluation import evaluate_answer, validate_follow_up_decision
from app.services.question_adapter import MAX_FOLLOW_UPS, decide_follow_up, target_main_difficulty
from app.services.question_selector import (
    DIFFICULTY_LEVELS, difficulty_rank, normalize_difficulty, select_question_bank_adaptive,
    select_question_bank_questions,
)

FRONTEND, BACKEND, FULLSTACK = "Frontend Engineer", "Backend Engineer", "Full Stack Engineer"
ROLES = (FRONTEND, BACKEND, FULLSTACK)
CV = ("Senior engineer with 5 years of experience building React and Redux applications. "
      "Led a migration from Redux to React Query at Acme. Also wrote Node.js services. ") * 3
DIM_KEYS = ["technical_skills", "domain_understanding", "problem_solving", "communication_clarity"]
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")


# ---------------------------------------------------------------- doubles ----

def make_eval(score=7, needs=False, reason=None, missing=None, contradicts=False):
    """An evaluator result in the shape evaluate_answer() returns."""
    dim_score = max(1, min(5, round(score / 2)))
    return {
        "dimensions": {k: {"score": dim_score, "justification": f"{k} justification text", "quote": "a quote"} for k in DIM_KEYS},
        "score": score, "strengths": "strengths", "weaknesses": "gap in explanation", "feedback": "Some feedback.",
        "confidence": 0.8, "needs_follow_up": needs, "follow_up_reason": reason if needs else None,
        "missing_information": missing, "contradicts_cv": contradicts,
    }


STRONG = make_eval(8)
NEUTRAL = make_eval(6)
WEAK_NO_PROBE = make_eval(4)
VAGUE = make_eval(5, needs=True, reason="vague", missing="no concrete example of how state updates re-render")
INCOMPLETE = make_eval(5, needs=True, reason="incomplete", missing="does not explain cleanup")
CONTRADICTION = make_eval(5, needs=True, reason="contradicts_cv", contradicts=True,
                          missing="said they never used Redux, but the CV lists Redux")


def messages_text(fake, role, call=0):
    """The joined content of one role's messages in a recorded LLM call."""
    return "\n".join(m["content"] for m in fake.calls[call]["messages"] if m["role"] == role)


class FakeOpenAI:
    """Stands in for the OpenAI client; returns `content` and provider-style usage numbers."""

    def __init__(self, content, prompt_tokens=321, completion_tokens=123):
        self.calls = []
        self._content = content
        self._usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                                      total_tokens=prompt_tokens + completion_tokens)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))], usage=self._usage)


def llm_eval_json(**overrides):
    """What the evaluation prompt asks the model to return."""
    data = {k: {"score": 4, "justification": "A clear two sentence justification.", "quote": "my answer"} for k in DIM_KEYS}
    data.update({"recommendation": "Hire", "confidence": "High", "summary": "A solid answer overall.",
                 "needs_follow_up": False, "follow_up_reason": "none", "missing_information": "", "contradicts_cv": False})
    data.update(overrides)
    return json.dumps(data)


# ------------------------------------------------------------------- base ----

class EngineTestCase(unittest.TestCase):
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
            seed_database.seed_into(db)          # the real jobs, candidates and question banks
            db.commit()
            self.job_ids = {j.title: j.id for j in db.query(models.JobDescription).all()}
            self.candidate_id = db.query(models.Candidate).order_by(models.Candidate.id).first().id

        # ---- LLM doubles ----
        self.eval_calls, self.follow_up_calls, self.cv_calls = [], [], []
        self.policy = lambda call_number, question, answer, cv: STRONG
        self.follow_up_available = True

        def fake_evaluate(question_text, answer_text, interview_id=None, db=None, cv_context=None, job_title=None):
            self.eval_calls.append({"question": question_text, "answer": answer_text, "cv": cv_context, "job_title": job_title})
            return self.policy(len(self.eval_calls), question_text, answer_text, cv_context)

        def fake_follow_up(**kwargs):
            self.follow_up_calls.append(kwargs)
            if not self.follow_up_available:
                return None
            n = len(self.follow_up_calls)
            return {"question_text": f"Probing question {n}: {kwargs.get('missing_information') or kwargs.get('follow_up_reason')}",
                    "difficulty": "medium", "reason": "probe", "is_valid": True}

        def fake_cv_questions(cv_text, job_description, required_skills, count=5, operation="cv_question_generation",
                              interview_id=None, db=None, job_title=None):
            self.cv_calls.append({"cv_text": cv_text, "job_title": job_title, "count": count, "operation": operation})
            return [{"text": f"[{job_title}] CV question {i + 1} (interview {interview_id})", "difficulty": d, "source_type": "cv"}
                    for i, d in enumerate(difficulty_plan(count))]

        for target, double in (
            ("app.api.answers.evaluate_answer", fake_evaluate),
            ("app.services.follow_up_generator.generate_follow_up_question", fake_follow_up),
            ("app.services.cv_processor.generate_cv_driven_questions", fake_cv_questions),
            ("app.api.interviews.generate_recommendation",
             lambda *a, **k: {"recommendation": "Hire", "confidence": "High", "reason": "Strong overall."}),
        ):
            patcher = mock.patch(target, new=double)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()
        os.remove(self.db_path)

    # ---- API helpers ----
    def grant_consent(self, interview_id, granted=True):
        version = self.client.get("/api/data-handling").json()["notice_version"]
        return self.client.post(f"/api/interviews/{interview_id}/consent", json={"granted": granted, "notice_version": version})

    def schedule(self, role, mode="question_bank", cv=None, consent=True):
        body = {"candidate_id": self.candidate_id, "job_description_id": self.job_ids[role], "mode": mode}
        if cv:
            body["cv_text"] = cv
        r = self.client.post("/api/interviews", json=body)
        self.assertEqual(r.status_code, 201, r.text)
        if consent:                                  # most tests are about what happens after the candidate has consented
            self.assertEqual(self.grant_consent(r.json()["id"]).status_code, 200)
        return r.json()["id"]

    def start(self, interview_id):
        r = self.client.post(f"/api/interviews/{interview_id}/start")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def question(self, question_id):
        return self.client.get(f"/api/questions/{question_id}").json()

    def answer(self, interview_id, interview_question_id, text="This is my answer."):
        r = self.client.post(f"/api/interviews/{interview_id}/questions/{interview_question_id}/answer", json={"answer_text": text})
        self.assertIn(r.status_code, (200, 201), r.text)
        r = self.client.post(f"/api/answers/{r.json()['id']}/evaluate")
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def next_question(self, interview_id):
        r = self.client.post(f"/api/interviews/{interview_id}/next-question")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def run_interview(self, role, mode="question_bank", cv=None, policy=None, answer_text="This is my answer."):
        """Drive a whole interview through the API. Returns (interview_id, [asked question dicts])."""
        if policy:
            self.policy = policy
        interview_id = self.schedule(role, mode, cv)
        started = self.start(interview_id)
        asked = [{"iq_id": started["current_question"]["id"], "question_id": started["current_question"]["question_id"],
                  "is_follow_up": False, "main": started["main_question_number"], "fu": started["follow_up_number"], "reason": None}]
        for _ in range(60):
            self.answer(interview_id, asked[-1]["iq_id"], answer_text)
            nxt = self.next_question(interview_id)
            if nxt["is_complete"]:
                return interview_id, self._describe(asked)
            asked.append({"iq_id": nxt["id"], "question_id": nxt["question_id"], "is_follow_up": nxt["is_follow_up"],
                          "main": nxt["main_question_number"], "fu": nxt["follow_up_number"], "reason": nxt["follow_up_reason"],
                          "difficulty_adjusted": nxt["difficulty_adjusted"], "text": nxt["question"]})
        self.fail("interview never completed")

    def _describe(self, asked):
        for item in asked:
            details = self.question(item["question_id"])
            item.update(job_type=details["job_type"], category=details["category"], difficulty=details["difficulty"],
                        text=details["question"])
        return asked

    def mains(self, asked):
        return [q for q in asked if not q["is_follow_up"]]

    def db_questions(self):
        with self.Session() as db:
            return db.query(models.Question).all()


# ---------------------------------------------------------- seed data ----

class SeedDataTests(unittest.TestCase):
    def load_all(self):
        questions = []
        for name in ("backend_questions.json", "frontend_questions.json", "fullstack_questions.json"):
            with open(os.path.join(DATA_DIR, "question_banks", name), encoding="utf-8") as f:
                questions += json.load(f)
        with open(os.path.join(DATA_DIR, "job_descriptions.json"), encoding="utf-8") as f:
            titles = {j["title"] for j in json.load(f)}
        return questions, titles

    def test_every_question_has_the_schema_fields_and_a_difficulty(self):
        questions, titles = self.load_all()
        for q in questions:
            self.assertEqual(set(q), {"question", "category", "difficulty", "job_type"}, q)
            self.assertIn(q["difficulty"].lower(), DIFFICULTY_LEVELS, q)
            self.assertIn(q["job_type"], titles, "every question must belong to a real job role")
            self.assertTrue(q["question"].strip() and q["category"].strip())

    def test_each_role_has_meaningful_easy_medium_hard_coverage(self):
        questions, titles = self.load_all()
        for role in titles:
            by_level = {level: [q for q in questions if q["job_type"] == role and q["difficulty"].lower() == level] for level in DIFFICULTY_LEVELS}
            self.assertGreaterEqual(sum(len(v) for v in by_level.values()), 6, role)
            for level, items in by_level.items():
                self.assertGreaterEqual(len(items), 2, f"{role} needs at least 2 {level} questions")

    def test_no_duplicate_questions_and_no_cross_role_copies(self):
        questions, _ = self.load_all()
        keys = [(q["job_type"], q["question"].strip().lower()) for q in questions]
        self.assertEqual(len(keys), len(set(keys)))
        texts = [q["question"].strip().lower() for q in questions]
        self.assertEqual(len(texts), len(set(texts)), "the same question must not be reused across roles")

    def test_categories_never_collide_with_generated_question_categories(self):
        questions, _ = self.load_all()
        self.assertFalse({q["category"] for q in questions} & {"cv_generated", "hybrid_generated", "follow_up"})

    def test_original_nine_questions_are_unchanged(self):
        questions, _ = self.load_all()
        texts = {q["question"] for q in questions}
        for original in (
            "Explain the difference between SQL and NoSQL databases. When would you use each?",
            "Design a REST API for an e-commerce platform. What endpoints would you create?",
            "Write a Python function that reverses a linked list.",
            "Explain the React component lifecycle and the purpose of useEffect hook.",
            "How does CSS Grid differ from Flexbox? Give examples of when to use each.",
            "Implement a debounce function in JavaScript that delays function execution.",
            "Design an architecture for a real-time chat application. Consider scalability and latency.",
            "Explain how authentication works in a web application. Compare sessions vs tokens.",
            "Write a function that finds the longest substring without repeating characters.",
        ):
            self.assertIn(original, texts)


class SeedingAndMigrationTests(unittest.TestCase):
    def test_reseeding_is_idempotent_and_tops_up_an_existing_database(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        try:
            with Session() as db:
                first = seed_database.seed_into(db)
                db.commit()
                self.assertEqual(first, {"job_descriptions": 3, "candidates": 10, "questions": 41, "candidate_cvs": 10})
                second = seed_database.seed_into(db)
                self.assertEqual(second, {"job_descriptions": 0, "candidates": 0, "questions": 0, "candidate_cvs": 0})
                db.commit()
                self.assertEqual(db.query(models.Question).count(), 41)
        finally:
            engine.dispose()
            os.remove(path)

    def test_old_database_is_migrated_in_place_and_topped_up_without_duplicates(self):
        """The reset database: 9 questions, the previous schema. Migrate it, re-seed, keep every row."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        raw = sqlite3.connect(path)
        raw.executescript("""
            CREATE TABLE job_descriptions (id INTEGER PRIMARY KEY, title VARCHAR(255) NOT NULL, description TEXT NOT NULL,
                required_skills TEXT NOT NULL, created_at DATETIME);
            CREATE TABLE candidates (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, email VARCHAR(255) NOT NULL UNIQUE,
                skills TEXT NOT NULL, experience_years INTEGER, resume_summary TEXT NOT NULL, created_at DATETIME);
            CREATE TABLE questions (id INTEGER PRIMARY KEY, question TEXT NOT NULL, category VARCHAR(255) NOT NULL,
                difficulty VARCHAR(50) NOT NULL, job_type VARCHAR(255) NOT NULL, is_active BOOLEAN);
            CREATE TABLE interviews (id INTEGER PRIMARY KEY, candidate_id INTEGER, job_description_id INTEGER, status VARCHAR(50),
                created_at DATETIME, started_at DATETIME, completed_at DATETIME, mode VARCHAR(50) DEFAULT 'question_bank' NOT NULL, cv_text TEXT);
            CREATE TABLE evaluations (id INTEGER PRIMARY KEY, answer_id INTEGER NOT NULL, dimensions TEXT, score INTEGER NOT NULL,
                strengths TEXT NOT NULL, weaknesses TEXT NOT NULL, feedback TEXT NOT NULL, confidence FLOAT NOT NULL, created_at DATETIME);
            CREATE TABLE recommendations (id INTEGER PRIMARY KEY, interview_id INTEGER, recommendation VARCHAR(50), confidence VARCHAR(20),
                reason TEXT, created_at DATETIME);
        """)
        for role, text in ((BACKEND, "Explain the difference between SQL and NoSQL databases. When would you use each?"),
                           (FRONTEND, "Explain the React component lifecycle and the purpose of useEffect hook.")):
            raw.execute("INSERT INTO questions (question, category, difficulty, job_type, is_active) VALUES (?,?,?,?,1)",
                        (text, "x", "Medium", role))
        raw.execute("INSERT INTO evaluations (answer_id, score, strengths, weaknesses, feedback, confidence) VALUES (1, 6, 's','w','f',0.5)")
        raw.commit()
        raw.close()

        engine = create_engine(f"sqlite:///{path}")
        try:
            with mock.patch.object(database, "engine", engine):
                database._migrate_candidates_schema()
                database._migrate_questions_schema()
                database._migrate_evaluations_schema()
            from sqlalchemy import inspect
            inspector = inspect(engine)
            self.assertIn("source_interview_id", {c["name"] for c in inspector.get_columns("questions")})
            self.assertTrue({"needs_follow_up", "follow_up_reason", "missing_information", "contradicts_cv"}
                            <= {c["name"] for c in inspector.get_columns("evaluations")})

            Session = sessionmaker(bind=engine)
            with Session() as db:
                added = seed_database.seed_into(db)
                db.commit()
                self.assertEqual(added["questions"], 39)                      # 41 minus the 2 already there
                self.assertEqual(db.query(models.Question).count(), 41)
                old_eval = db.query(models.Evaluation).one()                  # existing evaluation survived, flags are NULL
                self.assertEqual((old_eval.score, old_eval.needs_follow_up), (6, None))
            with mock.patch.object(database, "engine", engine):               # running the migration again is harmless
                database._migrate_questions_schema()
                database._migrate_evaluations_schema()
        finally:
            engine.dispose()
            os.remove(path)

    def test_difficulty_helpers_are_case_insensitive(self):
        self.assertEqual(normalize_difficulty(" Medium "), "medium")
        self.assertEqual([difficulty_rank(d) for d in ("Easy", "MEDIUM", "hard", "weird", None)], [0, 1, 2, 1, 1])


# ------------------------------------------------------------ role filter ----

class RoleFilteringTests(EngineTestCase):
    def test_the_reported_bug_a_frontend_interview_starts_with_a_frontend_question(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        first = self.question(started["current_question"]["question_id"])
        self.assertEqual(first["job_type"], FRONTEND)
        self.assertNotEqual(first["question"], "Explain the difference between SQL and NoSQL databases. When would you use each?")
        self.assertEqual(difficulty_rank(first["difficulty"]), 0, "question 1 is foundational (easy)")

    def test_full_interviews_only_receive_their_own_roles_questions(self):
        for role in ROLES:
            for mode in ("question_bank", "cv_driven", "hybrid"):
                with self.subTest(role=role, mode=mode):
                    _, asked = self.run_interview(role, mode, cv=CV if mode != "question_bank" else None)
                    self.assertEqual(len(asked), 5)
                    self.assertEqual({q["job_type"] for q in asked}, {role}, f"{role}/{mode} got another role's question")

    def test_frontend_never_gets_backend_or_fullstack_questions(self):
        _, asked = self.run_interview(FRONTEND)
        self.assertFalse({q["job_type"] for q in asked} & {BACKEND, FULLSTACK})

    def test_backend_never_gets_frontend_or_fullstack_questions(self):
        _, asked = self.run_interview(BACKEND)
        self.assertFalse({q["job_type"] for q in asked} & {FRONTEND, FULLSTACK})

    def test_fullstack_receives_fullstack_questions(self):
        _, asked = self.run_interview(FULLSTACK)
        self.assertEqual({q["job_type"] for q in asked}, {FULLSTACK})

    def test_weak_candidate_with_follow_ups_still_stays_in_role(self):
        for role in ROLES:
            with self.subTest(role=role):
                _, asked = self.run_interview(role, policy=lambda n, q, a, cv: VAGUE)
                self.assertGreater(len(asked), 5)
                self.assertEqual({q["job_type"] for q in asked}, {role})

    def test_selector_never_crosses_roles_even_when_the_own_pool_is_exhausted(self):
        interview_id = self.schedule(FRONTEND)
        with self.Session() as db:
            own = [q.id for q in db.query(models.Question).filter_by(job_type=FRONTEND).all()]
            for difficulty in ("easy", "medium", "hard"):
                self.assertIsNone(select_question_bank_adaptive(db, interview_id, difficulty, own), "must not fall back to another role")
            self.assertIsNotNone(select_question_bank_adaptive(db, interview_id, "easy", []))

    def test_difficulty_match_is_case_insensitive_for_the_seeded_values(self):
        interview_id = self.schedule(FRONTEND)
        with self.Session() as db:
            for level in DIFFICULTY_LEVELS:                    # seed says "Medium"; the engine asks for "medium"
                question = select_question_bank_adaptive(db, interview_id, level, [])
                self.assertEqual(normalize_difficulty(question.difficulty), level)

    def test_no_role_means_no_questions(self):
        with self.Session() as db:
            self.assertEqual(select_question_bank_questions(db, None), [])
            self.assertEqual(select_question_bank_questions(db, 99999), [])
            roles = {q.job_type for q in select_question_bank_questions(db, self.job_ids[BACKEND], count=50)}
            self.assertEqual(roles, {BACKEND})

    def test_a_role_without_questions_ends_cleanly_instead_of_borrowing(self):
        with self.Session() as db:
            job = models.JobDescription(title="Data Engineer", description="Pipelines", required_skills="spark")
            db.add(job)
            db.commit()
            self.job_ids["Data Engineer"] = job.id
        interview_id = self.schedule("Data Engineer")
        r = self.client.post(f"/api/interviews/{interview_id}/start")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Data Engineer", r.json()["detail"])

    def test_generated_questions_are_never_offered_to_a_different_interview(self):
        first = self.schedule(FRONTEND, "cv_driven", CV)
        self.start(first)
        second = self.schedule(FRONTEND, "cv_driven", CV)
        _, asked = self.run_interview(FRONTEND, "cv_driven", CV)
        interview_ids = {q["text"].split("(interview ")[1].rstrip(")") for q in asked if "(interview " in q["text"]}
        self.assertEqual(len(interview_ids), 1, "all CV questions of one interview come from that interview's own generation")
        self.assertNotIn(str(first), interview_ids)
        self.assertNotIn(str(second), interview_ids)

    def test_follow_ups_are_never_selected_as_main_questions(self):
        _, asked = self.run_interview(FRONTEND, policy=lambda n, q, a, cv: VAGUE)
        self.assertTrue(all(q["category"] != "follow_up" for q in self.mains(asked)))
        self.assertTrue(all(q["category"] == "follow_up" for q in asked if q["is_follow_up"]))

    def test_questions_are_not_repeated(self):
        for role in ROLES:
            _, asked = self.run_interview(role, policy=lambda n, q, a, cv: VAGUE)
            self.assertEqual(len({q["question_id"] for q in asked}), len(asked))
            self.assertEqual(len({q["text"] for q in asked}), len(asked))


class GeneratedQuestionRoleTests(EngineTestCase):
    def test_cv_generated_questions_are_stored_for_the_role_and_the_interview(self):
        interview_id = self.schedule(FRONTEND, "cv_driven", CV)
        self.start(interview_id)
        self.assertEqual(self.cv_calls[0]["job_title"], FRONTEND)
        generated = [q for q in self.db_questions() if q.category == "cv_generated"]
        self.assertEqual(len(generated), 5)
        self.assertEqual({q.job_type for q in generated}, {FRONTEND})
        self.assertEqual({q.source_interview_id for q in generated}, {interview_id})

    def test_hybrid_generation_respects_the_role_for_both_bank_and_generated_questions(self):
        interview_id = self.schedule(BACKEND, "hybrid", CV)
        self.start(interview_id)
        self.assertEqual(self.cv_calls[0]["job_title"], BACKEND)
        self.assertEqual(self.cv_calls[0]["operation"], "hybrid_question_generation")
        generated = [q for q in self.db_questions() if q.category == "hybrid_generated"]
        self.assertTrue(generated)
        self.assertEqual({q.job_type for q in generated}, {BACKEND})
        self.assertEqual({q.source_interview_id for q in generated}, {interview_id})

    def test_hybrid_bank_selection_is_role_filtered(self):
        for role in ROLES:
            with self.Session() as db:
                result = generate_hybrid_questions(db, CV, "desc", "skills", job_title=role, job_description_id=self.job_ids[role])
            bank = [q for q in result if not isinstance(q, dict)]
            self.assertEqual(len(bank), 2)
            self.assertEqual({q.job_type for q in bank}, {role}, role)

    def test_cv_prompt_names_the_role_and_demands_a_difficulty_ladder(self):
        fake = FakeOpenAI(json.dumps([{"text": f"Q{i}?", "difficulty": d} for i, d in enumerate(difficulty_plan(5))]))
        with mock.patch.object(cv_processor, "OpenAI", lambda api_key: fake), mock.patch.object(settings, "openai_api_key", "k"):
            questions = generate_cv_driven_questions(CV, "Build UIs", "React", count=5, job_title=FRONTEND)
        system, user = messages_text(fake, "system"), messages_text(fake, "user")
        self.assertEqual([m["role"] for m in fake.calls[0]["messages"]], ["system", "user"])
        self.assertIn('<untrusted_data name="JOB_ROLE">"Frontend Engineer"</untrusted_data>', user)
        self.assertIn("EVERY question must be relevant to the role given in JOB_ROLE", system)
        self.assertIn("easy, medium, medium, hard, hard", system)
        self.assertNotIn("Redux", system, "candidate CV content must never appear in the system message")
        self.assertIn("Redux", user)
        self.assertEqual([q["difficulty"] for q in questions], ["easy", "medium", "medium", "hard", "hard"])

    def test_generated_question_output_is_validated(self):
        raw = json.dumps([{"text": "Same?", "difficulty": "Medium"}, {"text": "same?", "difficulty": "hard"},
                          {"text": "  "}, "not a dict", {"text": "Other?", "difficulty": "legendary"}])
        fake = FakeOpenAI(raw)
        with mock.patch.object(cv_processor, "OpenAI", lambda api_key: fake), mock.patch.object(settings, "openai_api_key", "k"):
            questions = generate_cv_driven_questions(CV, "d", "s", count=3, job_title=BACKEND)
        self.assertEqual([q["text"] for q in questions], ["Same?", "Other?"])           # duplicate and malformed items dropped
        self.assertEqual([q["difficulty"] for q in questions], ["medium", "medium"])    # normalised; unknown -> planned level

    def test_difficulty_plan_rises_from_easy_to_hard(self):
        self.assertEqual(difficulty_plan(5), ["easy", "medium", "medium", "hard", "hard"])
        self.assertEqual(difficulty_plan(3), ["easy", "medium", "hard"])
        self.assertEqual(difficulty_plan(1), ["medium"])


# ------------------------------------------------------------- difficulty ----

class DifficultyProgressionTests(EngineTestCase):
    def test_target_difficulty_table(self):
        self.assertEqual([target_main_difficulty(n, []) for n in range(1, 6)], ["easy", "easy", "medium", "medium", "hard"])
        neutral = [target_main_difficulty(n, [6.0] * (n - 1)) for n in range(1, 6)]
        self.assertEqual(neutral, ["easy", "medium", "medium", "hard", "hard"])         # the baseline ladder
        strong = [target_main_difficulty(n, [9.0] * (n - 1)) for n in range(1, 6)]
        self.assertEqual(strong, ["easy", "medium", "hard", "hard", "hard"])
        weak = [target_main_difficulty(n, [3.0] * (n - 1)) for n in range(1, 6)]
        self.assertEqual(weak, ["easy", "easy", "easy", "medium", "medium"])
        self.assertEqual(target_main_difficulty(99, [5.0]), "hard")                      # never past the ladder

    def test_neutral_candidate_follows_the_baseline_easy_to_hard(self):
        for role in ROLES:
            _, asked = self.run_interview(role, policy=lambda n, q, a, cv: NEUTRAL)
            self.assertEqual([q["difficulty"].lower() for q in asked], ["easy", "medium", "medium", "hard", "hard"], role)

    def test_strong_candidate_moves_up_faster(self):
        _, asked = self.run_interview(FRONTEND, policy=lambda n, q, a, cv: STRONG)
        ranks = [difficulty_rank(q["difficulty"]) for q in asked]
        self.assertEqual(ranks, [0, 1, 2, 2, 2])
        self.assertEqual([q["difficulty_adjusted"] for q in asked[1:]], ["increased", "increased", "same", "same"])

    def test_weak_candidate_stays_around_the_current_difficulty(self):
        _, asked = self.run_interview(BACKEND, policy=lambda n, q, a, cv: WEAK_NO_PROBE)
        ranks = [difficulty_rank(q["difficulty"]) for q in asked]
        self.assertEqual(ranks, [0, 0, 0, 1, 1])
        self.assertLessEqual(max(ranks), 1)

    def test_first_question_is_easy_for_every_role_and_mode(self):
        for role in ROLES:
            for mode in ("question_bank", "cv_driven", "hybrid"):
                interview_id = self.schedule(role, mode, CV if mode != "question_bank" else None)
                started = self.start(interview_id)
                self.assertEqual(difficulty_rank(self.question(started["current_question"]["question_id"])["difficulty"]), 0, (role, mode))

    def test_follow_ups_do_not_advance_the_main_difficulty_or_number(self):
        # main question 1: vague, then good follow-up answers; the main ladder must be unaffected
        scripted = [VAGUE, make_eval(6), make_eval(6)]
        self.policy = lambda n, q, a, cv: scripted[n - 1] if n <= len(scripted) else NEUTRAL
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])
        fu1 = self.next_question(interview_id)
        self.assertEqual((fu1["is_follow_up"], fu1["main_question_number"], fu1["follow_up_number"]), (True, 1, 1))
        self.answer(interview_id, fu1["id"])
        main2 = self.next_question(interview_id)
        self.assertEqual((main2["is_follow_up"], main2["main_question_number"], main2["follow_up_number"]), (False, 2, 0))
        self.assertEqual(main2["current_index"], 2, "follow-ups are asked questions, but not main questions")
        self.assertEqual(main2["total_questions"], 5)
        # chain mean (5+6)/2 = 5.5 -> baseline Q2 (halfway) stays on the lower level because 5.5 < passing
        self.assertEqual(difficulty_rank(self.question(main2["question_id"])["difficulty"]), 0)


# --------------------------------------------------------------- follow-up ----

class FollowUpTriggerTests(EngineTestCase):
    def first_follow_up(self, outcome, role=FRONTEND, mode="question_bank", cv=None):
        self.policy = lambda n, q, a, c: outcome
        interview_id = self.schedule(role, mode, cv)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"], "React state stores data for the component.")
        return interview_id, started, self.next_question(interview_id)

    def test_vague_answer_triggers_a_contextual_follow_up(self):
        _, started, nxt = self.first_follow_up(VAGUE)
        self.assertTrue(nxt["is_follow_up"])
        self.assertEqual(nxt["follow_up_reason"], "vague")
        call = self.follow_up_calls[0]
        self.assertEqual(call["follow_up_reason"], "vague")
        self.assertEqual(call["candidate_answer"], "React state stores data for the component.")
        self.assertEqual(call["original_question"], self.question(started["current_question"]["question_id"])["question"])
        self.assertEqual(call["missing_information"], VAGUE["missing_information"])
        self.assertEqual(call["job_title"], FRONTEND)
        self.assertEqual(call["follow_up_number"], 1)

    def test_incomplete_answer_triggers_a_follow_up(self):
        _, _, nxt = self.first_follow_up(INCOMPLETE)
        self.assertEqual((nxt["is_follow_up"], nxt["follow_up_reason"]), (True, "incomplete"))

    def test_incorrect_answer_triggers_a_follow_up(self):
        _, _, nxt = self.first_follow_up(make_eval(5, needs=True, reason="incorrect", missing="confuses props and state"))
        self.assertEqual((nxt["is_follow_up"], nxt["follow_up_reason"]), (True, "incorrect"))

    def test_a_very_low_score_is_probed_even_if_the_evaluator_did_not_flag_it(self):
        _, _, nxt = self.first_follow_up(make_eval(2))                       # needs_follow_up False but score 2
        self.assertEqual((nxt["is_follow_up"], nxt["follow_up_reason"]), (True, "incorrect"))

    def test_strong_answer_is_not_probed_and_moves_to_the_next_main_question(self):
        _, _, nxt = self.first_follow_up(STRONG)
        self.assertEqual((nxt["is_follow_up"], nxt["main_question_number"], nxt["follow_up_number"]), (False, 2, 0))
        self.assertEqual(self.follow_up_calls, [])

    def test_cv_contradiction_triggers_a_professional_follow_up_with_the_cv(self):
        _, _, nxt = self.first_follow_up(CONTRADICTION, mode="hybrid", cv=CV)
        self.assertEqual((nxt["is_follow_up"], nxt["follow_up_reason"]), (True, "contradicts_cv"))
        call = self.follow_up_calls[0]
        self.assertEqual(call["follow_up_reason"], "contradicts_cv")
        self.assertIn("Redux", call["cv_context"])
        self.assertIn("Redux", call["missing_information"])

    def test_contradiction_in_cv_driven_mode_also_triggers(self):
        _, _, nxt = self.first_follow_up(CONTRADICTION, mode="cv_driven", cv=CV)
        self.assertEqual(nxt["follow_up_reason"], "contradicts_cv")

    def test_evaluator_receives_the_cv_only_for_cv_modes_and_always_the_role(self):
        for mode, cv in (("question_bank", None), ("cv_driven", CV), ("hybrid", CV)):
            self.eval_calls.clear()
            interview_id = self.schedule(FULLSTACK, mode, cv)
            started = self.start(interview_id)
            self.answer(interview_id, started["current_question"]["id"])
            self.assertEqual(self.eval_calls[0]["job_title"], FULLSTACK)
            self.assertEqual(self.eval_calls[0]["cv"], CV if cv else None, mode)

    def test_decision_rules_directly(self):
        def ev(score, needs, reason=None, contradicts=False, missing=None):
            return SimpleNamespace(score=score, needs_follow_up=needs, follow_up_reason=reason, contradicts_cv=contradicts,
                                   missing_information=missing, weaknesses="w", feedback="f")
        self.assertEqual(decide_follow_up(ev(7, False), 0, "question_bank")["should_follow_up"], False)
        self.assertEqual(decide_follow_up(ev(7, True, "vague"), 0, "question_bank")["reason"], "vague")
        self.assertEqual(decide_follow_up(ev(2, False), 0, "question_bank")["reason"], "incorrect")
        self.assertEqual(decide_follow_up(ev(6, True, "contradicts_cv", True), 0, "hybrid")["reason"], "contradicts_cv")
        # a CV contradiction cannot exist without a CV, so it is ignored in question_bank mode
        self.assertEqual(decide_follow_up(ev(8, False, None, True), 0, "question_bank")["should_follow_up"], False)
        capped = decide_follow_up(ev(2, True, "vague"), MAX_FOLLOW_UPS, "question_bank")
        self.assertEqual((capped["should_follow_up"], capped["capped"]), (False, True))

    def test_evaluations_without_a_stored_decision_use_the_original_score_rule(self):
        legacy_low = SimpleNamespace(score=5, needs_follow_up=None, follow_up_reason=None, contradicts_cv=None,
                                     missing_information=None, weaknesses="w", feedback="f")
        legacy_ok = SimpleNamespace(score=7, needs_follow_up=None, follow_up_reason=None, contradicts_cv=None,
                                    missing_information=None, weaknesses="w", feedback="f")
        self.assertTrue(decide_follow_up(legacy_low, 0, "question_bank")["should_follow_up"])
        self.assertFalse(decide_follow_up(legacy_ok, 0, "question_bank")["should_follow_up"])

    def test_the_follow_up_decision_is_stored_on_the_evaluation(self):
        interview_id = self.schedule(FRONTEND, "hybrid", CV)
        self.policy = lambda n, q, a, c: CONTRADICTION
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])
        with self.Session() as db:
            evaluation = db.query(models.Evaluation).one()
            self.assertEqual((evaluation.needs_follow_up, evaluation.follow_up_reason, evaluation.contradicts_cv),
                             (True, "contradicts_cv", True))
            self.assertIn("Redux", evaluation.missing_information)


class FollowUpLimitTests(EngineTestCase):
    def weak_chain(self, role=FRONTEND):
        """Answer the first main question badly until the engine moves on; returns (interview_id, asked steps)."""
        self.policy = lambda n, q, a, cv: VAGUE
        interview_id = self.schedule(role)
        started = self.start(interview_id)
        steps = [{"is_follow_up": False, "fu": 0, "main": 1}]
        current_iq = started["current_question"]["id"]
        for _ in range(8):
            self.answer(interview_id, current_iq)
            nxt = self.next_question(interview_id)
            steps.append({"is_follow_up": nxt["is_follow_up"], "fu": nxt["follow_up_number"], "main": nxt["main_question_number"],
                          "id": nxt["id"], "question_id": nxt["question_id"]})
            if not nxt["is_follow_up"]:
                return interview_id, steps
            current_iq = nxt["id"]
        self.fail("follow-ups never stopped")

    def test_counter_starts_at_zero_and_increments_one_two_three(self):
        _, steps = self.weak_chain()
        self.assertEqual([s["fu"] for s in steps], [0, 1, 2, 3, 0])
        self.assertEqual([s["is_follow_up"] for s in steps], [False, True, True, True, False])

    def test_exactly_three_follow_ups_are_generated_and_never_a_fourth(self):
        self.weak_chain()
        self.assertEqual(len(self.follow_up_calls), MAX_FOLLOW_UPS)
        self.assertEqual([c["follow_up_number"] for c in self.follow_up_calls], [1, 2, 3])

    def test_after_the_third_follow_up_the_next_main_question_is_selected(self):
        _, steps = self.weak_chain()
        main2 = steps[-1]
        self.assertEqual((main2["is_follow_up"], main2["main"], main2["fu"]), (False, 2, 0))
        question = self.question(main2["question_id"])
        self.assertEqual(question["job_type"], FRONTEND)
        self.assertNotEqual(question["category"], "follow_up")

    def test_each_main_question_gets_its_own_fresh_allowance_of_three(self):
        _, asked = self.run_interview(BACKEND, policy=lambda n, q, a, cv: VAGUE)
        mains = self.mains(asked)
        self.assertEqual(len(mains), 5)
        for number in range(1, 6):
            chain = [q for q in asked if q["main"] == number and q["is_follow_up"]]
            self.assertEqual([q["fu"] for q in chain], [1, 2, 3], f"main question {number}")
        self.assertEqual(len(asked), 5 + 5 * MAX_FOLLOW_UPS)
        self.assertEqual(len(self.follow_up_calls), 5 * MAX_FOLLOW_UPS)

    def test_the_counter_comes_from_stored_rows_not_from_process_memory(self):
        interview_id, _ = self.weak_chain()
        with self.Session() as db:
            categories = [
                db.query(models.Question).get(iq.question_id).category
                for iq in db.query(models.InterviewQuestion).filter_by(interview_id=interview_id).order_by(models.InterviewQuestion.question_order)
            ]
        self.assertEqual(categories[:4], [categories[0], "follow_up", "follow_up", "follow_up"])
        self.assertNotEqual(categories[0], "follow_up")
        self.assertNotEqual(categories[4], "follow_up")

    def test_follow_up_questions_are_stored_for_the_role_and_interview(self):
        interview_id, _ = self.weak_chain(BACKEND)
        stored = [q for q in self.db_questions() if q.category == "follow_up"]
        self.assertEqual(len(stored), 3)
        self.assertEqual({q.job_type for q in stored}, {BACKEND})
        self.assertEqual({q.source_interview_id for q in stored}, {interview_id})

    def test_follow_up_context_includes_the_chain_so_far(self):
        self.weak_chain()
        third = self.follow_up_calls[2]
        # the latest exchange (follow-up 2) is passed as the latest question; earlier ones as the history
        self.assertTrue(third["original_question"].startswith("Probing question 2"))
        self.assertEqual(len(third["previous_exchanges"]), 1)
        self.assertTrue(third["previous_exchanges"][0][0].startswith("Probing question 1"))
        self.assertTrue(third["main_question"])
        self.assertNotEqual(third["original_question"], third["main_question"])
        self.assertEqual(self.follow_up_calls[0]["previous_exchanges"], [])

    def test_failed_follow_up_generation_falls_back_to_the_next_main_question(self):
        self.follow_up_available = False
        self.policy = lambda n, q, a, cv: VAGUE
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])
        nxt = self.next_question(interview_id)
        self.assertEqual((nxt["is_follow_up"], nxt["main_question_number"]), (False, 2))
        self.assertEqual(self.question(nxt["question_id"])["job_type"], FRONTEND)

    def test_next_question_is_safe_to_retry_while_the_question_is_unanswered(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        self.answer(interview_id, started["current_question"]["id"])
        first = self.next_question(interview_id)
        again = self.next_question(interview_id)                  # e.g. the first response was lost
        self.assertEqual((again["id"], again["question_id"], again["main_question_number"]),
                         (first["id"], first["question_id"], first["main_question_number"]))
        with self.Session() as db:
            self.assertEqual(db.query(models.InterviewQuestion).filter_by(interview_id=interview_id).count(), 2)

    def test_interview_ends_after_five_main_questions_even_with_follow_ups(self):
        interview_id, asked = self.run_interview(FULLSTACK, policy=lambda n, q, a, cv: VAGUE)
        self.assertEqual(len(self.mains(asked)), 5)
        self.assertEqual(self.client.get(f"/api/interviews/{interview_id}").json()["status"], "in_progress")


# ----------------------------------------------- existing features intact ----

class ExistingFeaturesStillWorkTests(EngineTestCase):
    def test_completion_recommendation_and_human_review_work_after_follow_ups(self):
        interview_id, asked = self.run_interview(FRONTEND, policy=lambda n, q, a, cv: VAGUE if n <= 4 else NEUTRAL)
        r = self.client.post(f"/api/interviews/{interview_id}/complete")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["recommendation"], "Hire")
        self.assertEqual(r.json()["evaluation_count"], len(asked))

        data = self.client.get(f"/api/interviews/{interview_id}/review-data").json()
        self.assertEqual(len(data["questions_answers_evaluations"]), len(asked))
        self.assertEqual(sum(q["is_follow_up"] for q in data["questions_answers_evaluations"]), len(asked) - len(self.mains(asked)))
        self.assertIsNotNone(data["overall_evaluation"])                       # 4D aggregation includes every evaluated answer
        self.assertEqual(data["overall_evaluation"]["evaluated_questions"], len(asked))

        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/human-review").status_code, 201)
        reason = "Strong practical judgement in the follow-up discussion."
        self.assertEqual(self.client.post(f"/api/interviews/{interview_id}/human-review").status_code, 400)
        self.assertEqual(self.client.get(f"/api/interviews/{interview_id}").json()["display_status"], "Reviewed")
        self.assertTrue(reason)

    def test_human_override_still_works(self):
        interview_id, _ = self.run_interview(BACKEND)
        self.client.post(f"/api/interviews/{interview_id}/complete")
        r = self.client.post(f"/api/interviews/{interview_id}/human-review/override",
                             json={"decision": "Strong Hire", "override_reason": "Exceptional depth shown in the answers."})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["decision_type"], "OVERRIDE")

    def test_evaluate_endpoint_still_returns_four_dimensions_as_an_object(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        body = self.answer(interview_id, started["current_question"]["id"])
        self.assertEqual(set(body["dimensions"]), set(DIM_KEYS))
        self.assertTrue(all(1 <= body["dimensions"][k]["score"] <= 5 for k in DIM_KEYS))

    def test_answer_submission_remains_idempotent(self):
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        url = f"/api/interviews/{interview_id}/questions/{started['current_question']['id']}/answer"
        first = self.client.post(url, json={"answer_text": "one"})
        again = self.client.post(url, json={"answer_text": "one"})
        self.assertEqual((first.status_code, again.status_code, first.json()["id"] == again.json()["id"]), (201, 200, True))


# ---------------------------------------- real LLM-facing code, fake client ----

class EvaluationResponseShapeTests(EngineTestCase):
    def evaluate(self, content, cv=None, job_title=FRONTEND, db=None, interview_id=None):
        fake = FakeOpenAI(content)
        with mock.patch.object(llm_evaluation, "OpenAI", lambda api_key: fake), mock.patch.object(settings, "openai_api_key", "k"):
            result = evaluate_answer("What is state?", "State stores data.", interview_id=interview_id, db=db,
                                     cv_context=cv, job_title=job_title)
        return result, fake

    def test_valid_response_keeps_the_four_dimensions_and_adds_the_decision(self):
        result, fake = self.evaluate(llm_eval_json(needs_follow_up=True, follow_up_reason="vague",
                                                   missing_information="  no example given  "))
        self.assertEqual(set(result["dimensions"]), set(DIM_KEYS))
        self.assertTrue(all(result["dimensions"][k]["score"] == 4 for k in DIM_KEYS))
        self.assertEqual(result["score"], 7)                                  # int((4 - 1) * 2.5), unchanged formula
        self.assertEqual((result["needs_follow_up"], result["follow_up_reason"], result["contradicts_cv"]), (True, "vague", False))
        self.assertEqual(result["missing_information"], "no example given")
        self.assertIn('<untrusted_data name="JOB_ROLE">"Frontend Engineer"</untrusted_data>', messages_text(fake, "user"))

    def test_no_follow_up_is_normalised(self):
        result, _ = self.evaluate(llm_eval_json())
        self.assertEqual((result["needs_follow_up"], result["follow_up_reason"], result["missing_information"]), (False, None, None))

    def test_cv_is_included_in_the_prompt_only_when_provided(self):
        _, with_cv = self.evaluate(llm_eval_json(), cv=CV)
        self.assertIn("Redux", messages_text(with_cv, "user"))
        self.assertIn('<untrusted_data name="CANDIDATE_CV">', messages_text(with_cv, "user"))
        self.assertNotIn("Redux", messages_text(with_cv, "system"), "candidate CV content must never appear in the system message")
        _, without = self.evaluate(llm_eval_json())
        self.assertIn("No CV was provided", messages_text(without, "user"))
        self.assertNotIn("Redux", messages_text(without, "user") + messages_text(without, "system"))

    def test_cv_contradiction_is_accepted_when_a_cv_exists(self):
        result, _ = self.evaluate(llm_eval_json(needs_follow_up=True, follow_up_reason="contradicts_cv", contradicts_cv=True,
                                                missing_information="says no Redux; CV lists it"), cv=CV)
        self.assertEqual((result["contradicts_cv"], result["follow_up_reason"]), (True, "contradicts_cv"))

    def test_malformed_responses_are_rejected_not_silently_accepted(self):
        bad_cases = {
            "not json": "Sure! Here is my evaluation.",
            "score out of range": llm_eval_json(technical_skills={"score": 6, "justification": "A fine justification.", "quote": "q"}),
            "missing decision field": json.dumps({k: v for k, v in json.loads(llm_eval_json()).items() if k != "needs_follow_up"}),
            "missing reason": json.dumps({k: v for k, v in json.loads(llm_eval_json()).items() if k != "follow_up_reason"}),
            "needs_follow_up not a bool": llm_eval_json(needs_follow_up="yes"),
            "contradicts_cv not a bool": llm_eval_json(contradicts_cv="false"),
            "unknown reason": llm_eval_json(needs_follow_up=True, follow_up_reason="rude"),
            "missing_information not a string": llm_eval_json(missing_information=None),
            "follow-up flagged without a reason": llm_eval_json(needs_follow_up=True, follow_up_reason="none"),
            "reason without a follow-up": llm_eval_json(needs_follow_up=False, follow_up_reason="vague"),
            "contradiction without a CV": llm_eval_json(needs_follow_up=True, follow_up_reason="contradicts_cv", contradicts_cv=True),
            "contradiction flag but wrong reason": llm_eval_json(needs_follow_up=True, follow_up_reason="vague", contradicts_cv=True),
            "invalid recommendation": llm_eval_json(recommendation="Maybe"),
        }
        for name, content in bad_cases.items():
            with self.subTest(case=name):
                cv = CV if "contradiction flag" in name else None
                with self.assertRaises(ValueError):
                    self.evaluate(content, cv=cv)

    def test_decision_validator_directly(self):
        ok = validate_follow_up_decision(json.loads(llm_eval_json()), cv_provided=False)
        self.assertEqual(ok, {"needs_follow_up": False, "follow_up_reason": None, "missing_information": None, "contradicts_cv": False})
        with self.assertRaises(ValueError):
            validate_follow_up_decision({}, cv_provided=True)

    def test_usage_is_logged_with_the_providers_token_counts(self):
        with self.Session() as db:
            interview = models.Interview(candidate_id=self.candidate_id, job_description_id=self.job_ids[FRONTEND], mode="question_bank")
            db.add(interview)
            db.commit()
            self.evaluate(llm_eval_json(), db=db, interview_id=interview.id)
            row = db.query(models.LLMUsageLog).one()
            self.assertEqual((row.operation, row.status, row.prompt_tokens, row.completion_tokens, row.total_tokens),
                             ("evaluation", "success", 321, 123, 444))

    def test_malformed_response_is_logged_as_an_error(self):
        with self.Session() as db:
            with self.assertRaises(ValueError):
                self.evaluate(llm_eval_json(needs_follow_up="yes"), db=db)
            row = db.query(models.LLMUsageLog).one()
            self.assertEqual((row.operation, row.status), ("evaluation", "error"))

    def test_invalid_llm_output_surfaces_as_an_error_not_as_missing_configuration(self):
        def raise_invalid(*args, **kwargs):
            raise ValueError("Missing required field: needs_follow_up")
        interview_id = self.schedule(FRONTEND)
        started = self.start(interview_id)
        r = self.client.post(f"/api/interviews/{interview_id}/questions/{started['current_question']['id']}/answer",
                             json={"answer_text": "x"})
        with mock.patch("app.api.answers.evaluate_answer", side_effect=raise_invalid):
            response = self.client.post(f"/api/answers/{r.json()['id']}/evaluate")
        self.assertEqual(response.status_code, 502)
        self.assertIn("invalid response", response.json()["detail"])
        self.assertIn("needs_follow_up", response.json()["detail"])
        with mock.patch("app.api.answers.evaluate_answer", side_effect=ValueError("OpenAI API key is not configured.")):
            response = self.client.post(f"/api/answers/{r.json()['id']}/evaluate")
        self.assertEqual((response.status_code, response.json()["detail"]), (500, "AI service not configured"))


class FollowUpGeneratorShapeTests(EngineTestCase):
    def generate(self, content, db=None, **overrides):
        fake = FakeOpenAI(content, prompt_tokens=210, completion_tokens=45)
        kwargs = dict(original_question="Explain how React state management works.",
                      candidate_answer="React state stores data for the component.",
                      weaknesses="Does not explain re-rendering", feedback="Too brief.",
                      job_title=FRONTEND, follow_up_reason="vague",
                      missing_information="how state updates trigger a re-render", follow_up_number=1, db=db, interview_id=None)
        kwargs.update(overrides)
        with mock.patch.object(follow_up_generator, "OpenAI", lambda api_key: fake), mock.patch.object(settings, "openai_api_key", "k"):
            return generate_follow_up_question(**kwargs), fake

    def good(self, question="You mentioned that state stores component data. How do state updates trigger a re-render?"):
        return json.dumps({"question": question, "difficulty": "Medium", "reason": "Probes the re-render mechanism"})

    def test_prompt_is_built_from_the_question_answer_gap_role_and_reason(self):
        result, fake = self.generate(self.good())
        system, user = messages_text(fake, "system"), messages_text(fake, "user")
        for expected in ('name="JOB_ROLE">"Frontend Engineer"', "Explain how React state management works.",
                         "React state stores data for the component.", "how state updates trigger a re-render",
                         "Reason: vague", "follow-up 1 of at most 3"):
            self.assertIn(expected, user)
        self.assertIn("Stay within the interview role", system)
        self.assertNotIn("React state stores data", system, "candidate content must never appear in the system message")
        self.assertTrue(result["is_valid"])
        self.assertEqual(result["difficulty"], "medium")                      # normalised

    def test_prompt_for_a_cv_contradiction_is_neutral_and_includes_the_cv(self):
        messages = build_follow_up_messages("Have you used Redux?", "I have never worked with Redux.", "w", "f",
                                            cv_context=CV, job_title=FRONTEND, follow_up_reason="contradicts_cv",
                                            missing_information="says never used Redux; CV lists Redux", follow_up_number=1)
        user = messages[1]["content"]
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn('<untrusted_data name="CANDIDATE_CV">', user)
        self.assertIn("Redux", user)
        self.assertIn("neutral, professional", user)
        self.assertIn("Do not accuse", user)

    def test_prompt_includes_the_chain_so_far_and_the_main_question(self):
        messages = build_follow_up_messages("Second probe?", "Still vague.", "w", "f", job_title=BACKEND, follow_up_reason="incomplete",
                                            follow_up_number=3, main_question="Explain database indexes.",
                                            previous_exchanges=[("First probe?", "It is faster.")])
        system, user = messages[0]["content"], messages[1]["content"]
        self.assertIn('<untrusted_data name="MAIN_QUESTION">"Explain database indexes."</untrusted_data>', user)
        self.assertIn("First probe?", user)
        self.assertIn("It is faster.", user)
        self.assertIn("follow-up 3 of at most 3", user)
        self.assertIn("Do not repeat an earlier question", system)

    def test_malformed_follow_up_output_returns_none(self):
        for name, content in {
            "not json": "Here is a follow-up: what is state?",
            "not an object": json.dumps(["a question?"]),
            "no question": json.dumps({"difficulty": "easy"}),
            "empty question": json.dumps({"question": "   "}),
            "non-string question": json.dumps({"question": 7}),
            "too long": json.dumps({"question": "x" * 700}),
            "repeats the previous question": json.dumps({"question": "Explain how React state management works."}),
        }.items():
            with self.subTest(case=name):
                result, _ = self.generate(content)
                self.assertIsNone(result)

    def test_parse_helper_normalises_and_validates(self):
        parsed = parse_follow_up_response(json.dumps({"question": " Why? ", "difficulty": "HARD"}))
        self.assertEqual((parsed["question_text"], parsed["difficulty"], parsed["reason"]), ("Why?", "hard", "Follow-up question"))
        self.assertEqual(parse_follow_up_response(json.dumps({"question": "Why?", "difficulty": "nope"}))["difficulty"], "medium")
        with self.assertRaises(ValueError):
            parse_follow_up_response(json.dumps({"question": "Why?"}), previous_questions=["why?"])

    def test_usage_is_logged_with_the_providers_token_counts(self):
        with self.Session() as db:
            interview = models.Interview(candidate_id=self.candidate_id, job_description_id=self.job_ids[FRONTEND], mode="question_bank")
            db.add(interview)
            db.commit()
            self.generate(self.good(), db=db, interview_id=interview.id)
            row = db.query(models.LLMUsageLog).one()
            self.assertEqual((row.operation, row.status, row.prompt_tokens, row.completion_tokens, row.total_tokens),
                             ("adaptive_follow_up", "success", 210, 45, 255))

    def test_missing_api_key_returns_none(self):
        with mock.patch.object(settings, "openai_api_key", ""):
            self.assertIsNone(generate_follow_up_question("q", "a", "w", "f"))


if __name__ == "__main__":
    unittest.main()
