"""UI tests for the full Technical Interviewer workflow, driving the real Streamlit pages with AppTest.

Self-contained: starts the real backend on a free port against a throwaway SQLite database
(seeded with raw SQL). Only the OpenAI-calling functions (answer evaluation, recommendation,
follow-up generation) are replaced with deterministic stubs, so a full interview runs offline.
The interviewer passcode is set by the test launcher, independent of backend/.env.

This is AppTest (a headless Streamlit script runner), not a browser test: it checks behaviour
and content, not how the CSS renders.

Run from frontend/:  venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v
Optionally set BACKEND_PYTHON to the backend venv's python.exe (default: ..\\backend\\venv\\Scripts\\python.exe).
"""

import html
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.request

FRONTEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(os.path.dirname(FRONTEND_DIR), "backend")
BACKEND_PYTHON = os.environ.get("BACKEND_PYTHON", os.path.join(BACKEND_DIR, "venv", "Scripts", "python.exe"))

from streamlit.testing.v1 import AppTest  # noqa: E402

PASSCODE = "ui-test-passcode"
NOW = "2026-10-06 10:00:00"
SECRET_REASON = "SECRET_AI_REASON_TEXT"
CV = "Experienced backend engineer. " * 12
DIM_KEYS = ["technical_skills", "domain_understanding", "problem_solving", "communication_clarity"]
STUB_DIMS = (4, 3, 5, 2)

LAUNCHER = textwrap.dedent("""
    import sys
    from app.main import app
    from app.config import settings
    import app.api.answers as answers_api
    import app.api.interviews as interviews_api
    import app.services.follow_up_generator as follow_up

    settings.interviewer_passcode = {passcode!r}

    def stub_evaluate(question_text, answer_text, interview_id=None, db=None, cv_context=None, job_title=None):
        keys = ["technical_skills", "domain_understanding", "problem_solving", "communication_clarity"]
        dims = {{k: {{"score": s, "justification": "Stub justification text.", "quote": "stub quote"}}
                 for k, s in zip(keys, {dims!r})}}
        vague = "vague" in answer_text.lower()      # an answer containing "vague" asks the engine to probe
        return {{"dimensions": dims, "score": 6, "strengths": "stub", "weaknesses": "stub",
                "feedback": "stub feedback", "confidence": 0.7,
                "needs_follow_up": vague, "follow_up_reason": "vague" if vague else None,
                "missing_information": "no concrete example" if vague else None, "contradicts_cv": False}}

    follow_up_counter = []

    def stub_follow_up(**kwargs):
        follow_up_counter.append(1)
        return {{"question_text": "Probing follow-up number %d" % len(follow_up_counter), "difficulty": "medium",
                "reason": "probe", "is_valid": True}}

    def stub_recommendation(overall_score, evaluations_summary, job_title, interview_id=None, db=None):
        return {{"recommendation": "Hire", "confidence": "High", "reason": "Stub recommendation reasoning."}}

    answers_api.evaluate_answer = stub_evaluate
    interviews_api.generate_recommendation = stub_recommendation
    follow_up.generate_follow_up_question = stub_follow_up

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port={port}, log_level="warning")
""")

AUDIO_CONTROL_WORDS = ("voice", "audio", "speak", "microphone", "transcrib", "webrtc", "recording", "play question")
AI_PRIVATE_WORDS = ("Recommendation", "Confidence", "Overall Score", "Strong Hire", "No Hire", "Hold", "FINAL",
                    "Override", "token", "Token", "latency", "Latency", "gpt-", "Estimated Cost", "LLM")


def dims(*scores):
    return json.dumps({k: {"score": s, "justification": f"{k} justification text", "quote": f"{k} quote"} for k, s in zip(DIM_KEYS, scores)})


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed(db_path):
    c = sqlite3.connect(db_path)
    ins = lambda sql, args: c.execute(sql, args).lastrowid  # noqa: E731

    for name, email in (("Aarav Sharma", "aarav@example.com"), ("Priya Patel", "priya@example.com"), ("Marcus Johnson", "marcus@example.com"), ("Chen Wei", "chen@example.com"), ("Dana Lopez", "dana@example.com")):
        ins("insert into candidates (name,email,skills,experience_years,resume_summary,created_at) values (?,?,?,?,?,?)",
            (name, email, "python", 3, "summary", NOW))
    for title in ("Backend Engineer", "Frontend Engineer"):
        ins("insert into job_descriptions (title,description,required_skills,created_at) values (?,?,?,?)",
            (title, f"{title} role", "python", NOW))
    for title in ("Backend Engineer", "Frontend Engineer"):          # the engine only asks questions of the interview's role
        for i, difficulty in enumerate(["easy", "easy", "medium", "medium", "medium", "medium", "hard", "hard"]):
            ins("insert into questions (question,category,difficulty,job_type,is_active) values (?,?,?,?,1)",
                (f"{title} bank question {i}", f"topic {i}", difficulty, title))

    def interview(candidate, job, status, mode, cv=None, consent="granted"):
        completed = NOW if status == "completed" else None
        started = NOW if status in ("in_progress", "completed") else None
        return ins("insert into interviews (candidate_id,job_description_id,status,created_at,started_at,completed_at,mode,cv_text,"
                   "consent_status,consent_recorded_at,consent_notice_version) values (?,?,?,?,?,?,?,?,?,?,?)",
                   (candidate, job, status, NOW, started, completed, mode, cv,
                    consent, NOW if consent else None, "2026-10-10" if consent else None))

    def completed_with_answers(candidate, job, per_question, recommendation, reason=SECRET_REASON, review=None):
        iid = interview(candidate, job, "completed", "question_bank")
        for order, (score, dimensions) in enumerate(per_question, 1):
            iq = ins("insert into interview_questions (interview_id,question_id,question_order,created_at) values (?,?,?,?)", (iid, order, order, NOW))
            ans = ins("insert into answers (interview_question_id,answer_text,created_at) values (?,?,?)", (iq, f"My answer {order}", NOW))
            ins("insert into evaluations (answer_id,dimensions,score,strengths,weaknesses,feedback,confidence,created_at) values (?,?,?,?,?,?,?,?)",
                (ans, dimensions, score, "s", "w", "f", 0.7, NOW))
        ins("insert into recommendations (interview_id,recommendation,confidence,reason,created_at) values (?,?,?,?,?)",
            (iid, recommendation, "High", reason, NOW))
        if review:
            ins("insert into human_reviews (interview_id,interviewer_id,interviewer_name,ai_recommendation,ai_reasoning,decision,decision_type,created_at,reviewed_at) values (?,?,?,?,?,?,?,?,?)",
                (iid, "demo-interviewer", "Demo Interviewer", recommendation, reason, review, "OVERRIDE", NOW, NOW))
        return iid

    def usage(interview_id, operation, model, prompt, completion, latency, status="success", minute=0):
        total = None if prompt is None else prompt + completion
        ins("insert into llm_usage_logs (interview_id,operation,provider,model,prompt_tokens,completion_tokens,total_tokens,latency_ms,status,created_at) values (?,?,?,?,?,?,?,?,?,?)",
            (interview_id, operation, "openai", model, prompt, completion, total, latency, status, f"2026-10-06 10:{minute:02d}:00"))

    # Priya Patel has a saved CV on file (as the seeded sample candidates do); Aarav Sharma does not
    c.execute("update candidates set cv_text=?, cv_file_name=?, cv_updated_at=? where id=2", (CV, "priya_patel_cv.pdf", NOW))

    ids = {}
    ids["scheduled_qb"] = interview(1, 1, "not_started", "question_bank")
    ids["scheduled_cv_missing"] = interview(1, 1, "not_started", "cv_driven")
    ids["scheduled_hybrid_with_cv"] = interview(1, 2, "not_started", "hybrid", CV)
    ids["in_progress"] = interview(1, 1, "in_progress", "question_bank")
    ids["with_4d"] = completed_with_answers(1, 1, [(6, dims(4, 3, 5, 2)), (8, dims(5, 3, 4, 4))], "Hire")
    ids["legacy"] = completed_with_answers(1, 1, [(5, None), (7, None)], "consider")
    ids["priya_scheduled"] = interview(2, 2, "not_started", "question_bank")
    ids["reviewed"] = completed_with_answers(1, 1, [(6, dims(3, 3, 3, 3))], "Hire", review="Strong Hire")
    ids["accept_me"] = completed_with_answers(1, 1, [(6, dims(3, 3, 3, 3))], "Hire")
    ids["override_me"] = completed_with_answers(1, 1, [(6, dims(3, 3, 3, 3))], "Hire")
    ids["e2e"] = interview(3, 1, "not_started", "question_bank")                       # Marcus: full interview in the e2e test
    ids["followup_ui"] = interview(3, 2, "not_started", "question_bank")               # Marcus: Frontend role, vague answers

    # ---- interrupted (In Progress) interviews for Chen Wei: Backend role, ids 1-8 are the Backend bank questions ----
    def add_step(iid, order, question_id, answer=None, evaluated=True):
        iq = ins("insert into interview_questions (interview_id,question_id,question_order,created_at) values (?,?,?,?)", (iid, question_id, order, NOW))
        if answer is not None:
            ans = ins("insert into answers (interview_question_id,answer_text,created_at) values (?,?,?)", (iq, answer, NOW))
            if evaluated:
                ins("insert into evaluations (answer_id,dimensions,score,strengths,weaknesses,feedback,confidence,created_at) values (?,?,?,?,?,?,?,?)",
                    (ans, dims(4, 3, 5, 2), 6, "s", "w", "f", 0.7, NOW))
        return iq

    def follow_up_question(iid, text):
        return ins("insert into questions (question,category,difficulty,job_type,is_active,source_interview_id) values (?,?,?,?,1,?)",
                   (text, "follow_up", "medium", "Backend Engineer", iid))

    ids["resume_pending"] = interview(4, 1, "in_progress", "question_bank")             # Q1 answered+evaluated, Q2 waiting
    add_step(ids["resume_pending"], 1, 1, "My first answer.")
    add_step(ids["resume_pending"], 2, 3)
    ids["refresh_pending"] = interview(4, 1, "in_progress", "question_bank")            # same shape, refreshed in the browser
    add_step(ids["refresh_pending"], 1, 2, "Answer before the refresh.")
    add_step(ids["refresh_pending"], 2, 4)
    ids["resume_followup"] = interview(4, 1, "in_progress", "question_bank")            # main + follow-up 1 done, follow-up 2 waiting
    add_step(ids["resume_followup"], 1, 5, "Main answer.")
    add_step(ids["resume_followup"], 2, follow_up_question(ids["resume_followup"], "Seeded follow-up one?"), "Follow-up one answer.")
    add_step(ids["resume_followup"], 3, follow_up_question(ids["resume_followup"], "Seeded follow-up two?"))
    ids["resume_eval_missing"] = interview(4, 1, "in_progress", "question_bank")        # answer saved, evaluation never ran
    add_step(ids["resume_eval_missing"], 1, 6, "Saved but not evaluated.", evaluated=False)
    ids["resume_next_missing"] = interview(4, 1, "in_progress", "question_bank")        # evaluated, next question never fetched
    add_step(ids["resume_next_missing"], 1, 7, "Evaluated, then the request failed.")
    ids["progress_empty"] = interview(4, 1, "in_progress", "question_bank")             # started, nothing answered
    add_step(ids["progress_empty"], 1, 8)
    ids["progress_partial"] = interview(4, 1, "in_progress", "question_bank")           # main evaluated, follow-up answered but not evaluated
    add_step(ids["progress_partial"], 1, 2, "Main answer text.")
    add_step(ids["progress_partial"], 2, follow_up_question(ids["progress_partial"], "Partial follow-up question?"), "Follow-up answer text.", evaluated=False)

    # ---- consent scenarios for Dana Lopez (id 5): nothing recorded yet, as for a newly scheduled or pre-consent interview ----
    ids["consent_agree"] = interview(5, 1, "not_started", "question_bank", consent=None)
    ids["consent_decline"] = interview(5, 1, "not_started", "question_bank", consent=None)
    ids["legacy_no_consent"] = interview(5, 1, "in_progress", "question_bank", consent=None)     # started before consent existed
    add_step(ids["legacy_no_consent"], 1, 1, "Answer given before consent was recorded.")
    add_step(ids["legacy_no_consent"], 2, 3)

    # ---- exit scenarios (Chen Wei): dedicated rows, because confirming an exit is permanent ----
    ids["exit_url"] = interview(4, 1, "in_progress", "question_bank")
    add_step(ids["exit_url"], 1, 1, "Answer before the exit.")
    add_step(ids["exit_url"], 2, 3)
    ids["exit_flow"] = interview(4, 1, "in_progress", "question_bank")
    add_step(ids["exit_flow"], 1, 2, "Saved answer one.")
    add_step(ids["exit_flow"], 2, 4, "Saved answer two, never evaluated.", evaluated=False)
    add_step(ids["exit_flow"], 3, 5)

    # ---- an interview the candidate already abandoned (Chen Wei): one evaluated answer, one not, one unanswered ----
    ids["abandoned_partial"] = interview(4, 1, "abandoned", "question_bank")
    c.execute("update interviews set abandoned_at=? where id=?", (NOW, ids["abandoned_partial"]))
    add_step(ids["abandoned_partial"], 1, 6, "Abandoned-interview answer one.")
    add_step(ids["abandoned_partial"], 2, 7, "Abandoned-interview answer two, no evaluation.", evaluated=False)
    add_step(ids["abandoned_partial"], 3, 8)
    answer_one = c.execute("select a.id from answers a join interview_questions iq on iq.id=a.interview_question_id "
                           "where iq.interview_id=? order by a.id", (ids["abandoned_partial"],)).fetchone()[0]
    ins("insert into content_security_checks (interview_id,source,source_id,status,categories,excerpt,explanation,checked_at) values (?,?,?,?,?,?,?,?)",
        (ids["abandoned_partial"], "answer", answer_one, "suspected", '["score_manipulation"]', "rate me 5/5", "asks the AI to give a particular score", NOW))

    usage(ids["with_4d"], "evaluation", "gpt-4o-mini", 1000, 500, 2000, minute=1)
    usage(ids["with_4d"], "evaluation", "gpt-4o-mini", 2000, 1000, 4000, minute=2)
    usage(ids["with_4d"], "recommendation_generation", "gpt-4o-mini", 500, 100, 3000, minute=3)
    usage(ids["legacy"], "evaluation", "gpt-4", None, None, 1000, status="error", minute=4)
    usage(ids["legacy"], "evaluation", "gpt-4", 800, 200, 2000, minute=5)
    usage(ids["priya_scheduled"], "cv_question_generation", "gpt-4o-mini", 3000, 1500, 5000, minute=6)
    usage(None, "tts", "tts-1", None, None, 500, minute=7)
    c.commit()
    c.close()
    return ids


# ---- AppTest helpers ----

def visible_texts(at):
    """Rendered text of the page, excluding the injected stylesheet."""
    out = []
    for group in (at.title, at.header, at.subheader, at.markdown, at.info, at.warning, at.error, at.success, at.caption):
        out += [str(e.value) for e in group if not str(e.value).lstrip().startswith("<style")]
    return html.unescape("\n".join(out))


def plain_texts(at):
    """visible_texts with HTML tags removed, for readable substring checks."""
    return re.sub(r"<[^>]+>", " ", visible_texts(at))


STAT_RE = re.compile(r'ti-stat-label">(.*?)</div><div class="ti-stat-value">(.*?)(?: <small>.*?</small>)?</div>')


def stats(at):
    """{label: value} for every stat/dimension card on the page."""
    found = {}
    for element in at.markdown:
        for label, value in STAT_RE.findall(str(element.value)):
            found[html.unescape(label)] = html.unescape(value)
    return found


def walk(node):
    for key in sorted(node.children):
        child = node.children[key]
        yield child
        if hasattr(child, "children"):
            yield from walk(child)


def button_labels(at):
    return [b.label for b in at.button]


def is_authenticated(at):
    try:
        return bool(at.session_state["interviewer_authenticated"])
    except KeyError:
        return False


class UiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        env = dict(os.environ, PYTHONPATH=BACKEND_DIR)
        cls.log = open(os.path.join(cls.tmp.name, "server.log"), "w")
        launcher = LAUNCHER.format(passcode=PASSCODE, dims=STUB_DIMS, port=cls.port)
        cls.server = subprocess.Popen([BACKEND_PYTHON, "-c", launcher], cwd=cls.tmp.name, env=env,
                                      stdout=cls.log, stderr=subprocess.STDOUT)
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(80):
            try:
                urllib.request.urlopen(f"{cls.base}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.tearDownClass()
            raise RuntimeError("test backend did not start")
        cls.ids = seed(os.path.join(cls.tmp.name, "technical_interviewer.db"))
        os.environ["BACKEND_URL"] = cls.base

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.server.kill()
        cls.log.close()
        try:
            cls.tmp.cleanup()
        except PermissionError:
            pass

    def api(self, path, **params):
        import requests
        r = requests.get(f"{self.base}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def app(self, page=None, authenticated=False, query=None, state=None):
        os.chdir(FRONTEND_DIR)
        if FRONTEND_DIR not in sys.path:
            sys.path.insert(0, FRONTEND_DIR)
        at = AppTest.from_file(os.path.join(FRONTEND_DIR, "app.py"), default_timeout=90)
        if page:
            at.session_state["page"] = page
        if authenticated:
            at.session_state["interviewer_authenticated"] = True
        for key, value in (query or {}).items():
            at.query_params[key] = value
        for key, value in (state or {}).items():
            at.session_state[key] = value
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def click(self, at, key):
        at.button(key=key).click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def candidate_view(self, name="Aarav Sharma"):
        at = self.app("candidate")
        at.selectbox(key="candidate_selector").select(name).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def dashboard(self):
        return self.app("interviewer_dashboard", authenticated=True)

    def review_view(self, interview_id):
        at = self.dashboard()
        return self.click(at, f"review_{interview_id}")

    def login(self, passcode):
        at = self.app("interviewer_login")
        at.text_input(key="passcode_input").set_value(passcode)
        at.button(key="passcode_submit").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at


class LandingTests(UiTestCase):
    def test_landing_content_and_two_entry_points(self):
        at = self.app()
        t = plain_texts(at)
        self.assertEqual(at.session_state["page"], "landing")
        for text in ("TECHNICAL INTERVIEWER", "AI-Powered Technical Interview & Evaluation Platform",
                     "Conduct structured technical interviews with adaptive AI questioning, evidence-based evaluation, and human review."):
            self.assertIn(text, t)
        for item in ("View scheduled interview", "Complete technical interview", "Submit answers",
                     "Receive interview submission confirmation", "Schedule interviews", "Monitor interview status",
                     "Review AI evaluation", "Make final human decisions", "View AI usage and cost"):
            self.assertIn(item, t)
        self.assertEqual(sorted(button_labels(at)), ["Continue as Candidate", "Interviewer Access"])

    def test_continue_as_candidate_opens_candidate_dashboard(self):
        at = self.click(self.app(), "landing_candidate")
        self.assertEqual(at.session_state["page"], "candidate")
        self.assertIn("My Interviews", plain_texts(at))

    def test_interviewer_access_leads_to_passcode_screen(self):
        at = self.click(self.app(), "landing_interviewer")
        self.assertEqual(at.session_state["page"], "interviewer_login")
        self.assertIn("INTERVIEWER ACCESS", plain_texts(at))

    def test_landing_has_no_internal_or_ai_details(self):
        t = plain_texts(self.app())
        for word in ("token", "gpt-", "Estimated Cost", SECRET_REASON):
            self.assertNotIn(word, t)


class CandidateFlowTests(UiTestCase):
    def test_back_button_returns_to_landing(self):
        at = self.click(self.app("candidate"), "btn-candidate")
        self.assertEqual(at.session_state["page"], "landing")

    def test_candidate_sees_only_own_interviews_with_job_mode_status(self):
        at = self.candidate_view("Aarav Sharma")
        cards = [c for c in at.markdown if 'class="ti-fields"' in str(c.value)]
        self.assertEqual(len(cards), len(self.api("/api/interviews", candidate_id=1)))   # exactly their own, no one else's
        joined = html.unescape(" ".join(str(c.value) for c in cards))
        self.assertEqual(joined.count("Aarav Sharma"), len(cards))
        self.assertNotIn("Priya", joined)
        self.assertNotIn("Marcus", joined)
        self.assertEqual(joined.count("Scheduled</span>"), 3)
        self.assertEqual(joined.count("In Progress</span>"), 1)
        self.assertIn("Backend Engineer", joined)
        self.assertIn("Frontend Engineer", joined)
        for mode in ("Question Bank", "CV Driven", "Hybrid"):
            self.assertIn(mode, joined)

        priya = html.unescape(" ".join(str(c.value) for c in self.candidate_view("Priya Patel").markdown if 'class="ti-fields"' in str(c.value)))
        self.assertEqual(priya.count("Scheduled</span>"), 1)
        self.assertNotIn("Aarav", priya)

    def test_candidate_cannot_choose_candidate_job_mode_or_answer_method(self):
        at = self.candidate_view()
        self.assertEqual([s.key for s in at.selectbox], ["candidate_selector"])    # identity picker only
        self.assertEqual(len(at.radio), 0)                                         # no mode or answer-method choice
        self.assertFalse(any(label in ("Interview Mode", "Job Role") for label in [s.label for s in at.selectbox]))

    def test_candidate_never_sees_ai_review_or_usage_information(self):
        t = plain_texts(self.candidate_view())
        self.assertNotIn(SECRET_REASON, t)
        for word in AI_PRIVATE_WORDS:
            self.assertNotIn(word, t, word)
        cards = html.unescape(" ".join(str(c.value) for c in self.candidate_view().markdown if 'class="ti-fields"' in str(c.value)))
        self.assertNotIn("Reviewed</span>", cards)             # reviewed looks like completed to the candidate
        self.assertIn("Completed</span>", cards)
        self.assertIn("Interview submitted. Thank you!", plain_texts(self.candidate_view()))

    def test_cv_missing_shows_resume_warning_and_blocks_start(self):
        at = self.candidate_view()
        self.assertIn("Resume required before starting this interview.", plain_texts(at))
        self.assertTrue(at.button(key=f"start_{self.ids['scheduled_cv_missing']}").disabled)
        self.assertTrue(at.get("file_uploader"))

    def test_interviewer_uploaded_cv_lets_candidate_start_directly(self):
        at = self.candidate_view()
        self.assertIn("Your resume has already been provided.", plain_texts(at))
        self.assertFalse(at.button(key=f"start_{self.ids['scheduled_hybrid_with_cv']}").disabled)

    def test_in_progress_interview_offers_resume_not_start(self):
        at = self.candidate_view()
        self.assertTrue(at.button(key=f"resume_{self.ids['in_progress']}"))
        self.assertEqual(at.button(key=f"resume_{self.ids['in_progress']}").label, "Resume Interview")
        self.assertNotIn("cannot be resumed", plain_texts(at))
        with self.assertRaises(KeyError):
            at.button(key=f"start_{self.ids['in_progress']}")

    def test_start_scheduled_interview_opens_text_only_interview_screen(self):
        at = self.candidate_view()
        self.click(at, f"start_{self.ids['scheduled_qb']}")
        self.assertEqual(at.session_state["page"], "interview")
        self.assertEqual(at.session_state["interview_id"], self.ids["scheduled_qb"])
        self.assertEqual(self.api(f"/api/interviews/{self.ids['scheduled_qb']}")["display_status"], "In Progress")

        t = plain_texts(at)
        self.assertIn("Aarav Sharma", t)
        self.assertIn("Backend Engineer", t)
        self.assertIn("Question 1 of 5", t)
        self.assertIn("INTERVIEW QUESTION", t.upper())
        self.assertIn("YOUR ANSWER", t.upper())
        self.assertEqual(len(at.text_area), 1)
        self.assertEqual(len(at.get("progress")), 1)
        self.assertEqual(len(at.radio), 0)
        self.assertIn("Submit Answer", button_labels(at))
        self.assertIn("← Exit Interview", button_labels(at))
        for word in AI_PRIVATE_WORDS:
            self.assertNotIn(word, t, word)

    def test_empty_answer_is_rejected(self):
        at = self.candidate_view("Priya Patel")
        self.click(at, f"start_{self.ids['priya_scheduled']}")
        self.click(at, "submit_answer")
        self.assertIn("Please enter an answer before submitting.", [e.value for e in at.error])
        self.assertEqual(at.session_state["page"], "interview")

    def test_exit_interview_asks_for_confirmation(self):
        at = self.app("interview")
        at.session_state["interview_id"] = 12345
        at.session_state["current_question"] = {"id": 1, "question_id": 1}
        at.session_state["current_question_index"] = 0
        at.session_state["question_details_cache"] = {1: {"question": "What is an index?"}}
        at.session_state["submitted_answers"] = {}
        at.run()
        self.assertFalse(at.exception)
        self.click(at, "btn-exit")
        warning = " ".join(w.value for w in at.warning)
        self.assertIn("Are you sure you want to end this interview? You will not be able to resume it.", warning)
        self.assertNotIn("Resume Interview", warning)
        self.click(at, "confirm_exit_no")
        self.assertEqual(at.session_state["page"], "interview")
        self.assertEqual(len(at.warning), 0)
        self.assertEqual(at.session_state["page"], "interview")                  # cancelling keeps the interview open

    def test_full_interview_submit_answers_next_question_completion_and_review(self):
        at = self.candidate_view("Marcus Johnson")
        self.click(at, f"start_{self.ids['e2e']}")
        self.assertEqual(at.session_state["page"], "interview")

        seen_questions = []
        for expected_idx in range(5):
            self.assertEqual(at.session_state["page"], "interview", f"question {expected_idx + 1}")
            self.assertEqual(at.session_state["current_question_index"], expected_idx)
            self.assertIn(f"Question {expected_idx + 1} of 5", plain_texts(at))
            question_block = next(str(m.value) for m in at.markdown if 'class="ti-question-text"' in str(m.value))
            self.assertNotIn(question_block, seen_questions, "the next question must be a new one")
            seen_questions.append(question_block)
            at.text_area(key=f"answer_{expected_idx}").set_value(f"My considered answer number {expected_idx + 1}.").run()
            self.click(at, "submit_answer")

        # last answer evaluated -> interview finalised automatically -> plain confirmation
        self.assertEqual(at.session_state["page"], "results")
        t = plain_texts(at)
        self.assertIn("INTERVIEW SUBMITTED", t)
        self.assertIn("Your interview has been successfully submitted.", t)
        self.assertEqual(len(at.metric), 0)
        for word in AI_PRIVATE_WORDS:
            self.assertNotIn(word, t, word)
        self.assertNotIn("Stub recommendation reasoning.", t)

        self.assertEqual(self.api(f"/api/interviews/{self.ids['e2e']}")["display_status"], "Completed")
        review = self.api(f"/api/interviews/{self.ids['e2e']}/review-data")
        self.assertEqual(len(review["questions_answers_evaluations"]), 5)
        self.assertEqual(review["overall_evaluation"]["evaluated_questions"], 5)
        self.assertEqual(review["ai_assessment"]["recommendation"], "Hire")

        # candidate dashboard now shows the interview as submitted, still without any AI information
        self.click(at, "results_back")
        self.assertEqual(at.session_state["page"], "candidate")
        at.selectbox(key="candidate_selector").select("Marcus Johnson").run()
        t = plain_texts(at)
        self.assertIn("Interview submitted. Thank you!", t)
        self.assertNotIn("Hire", t)

        # interviewer sees the new interview with its overall 4D evaluation
        review_page = self.review_view(self.ids["e2e"])
        values = stats(review_page)
        self.assertEqual(
            (values["Technical Skills"], values["Domain Understanding"], values["Problem-Solving Approach"], values["Communication Clarity"]),
            ("4.0", "3.0", "5.0", "2.0"),
        )


class AdaptiveInterviewUiTests(UiTestCase):
    def role_of(self, at):
        return self.api(f"/api/questions/{at.session_state['current_question']['question_id']}")["job_type"]

    def test_follow_ups_are_labelled_and_main_progress_is_not_inflated(self):
        at = self.candidate_view("Marcus Johnson")
        self.click(at, f"start_{self.ids['followup_ui']}")
        self.assertEqual(self.role_of(at), "Frontend Engineer")                       # role of the scheduled interview
        t = plain_texts(at)
        self.assertIn("Question 1 of 5", t)
        self.assertNotIn("Follow-up", t.replace("Follow-up Question", ""))
        self.assertIn("INTERVIEW QUESTION", t.upper())

        for expected in (1, 2, 3):                                                   # vague answers: three probes, then move on
            idx = at.session_state["current_question_index"]
            at.text_area(key=f"answer_{idx}").set_value("A vague answer.").run()
            self.click(at, "submit_answer")
            t = plain_texts(at)
            if expected <= 3:
                self.assertIn("Question 1 of 5", t, "follow-ups never change the main question number")
                self.assertIn(f"Follow-up {expected} of 3", t)
                self.assertIn("FOLLOW-UP QUESTION", t.upper())
                self.assertIn(f"Probing follow-up number {expected}", t)
                self.assertEqual(self.role_of(at), "Frontend Engineer")

        idx = at.session_state["current_question_index"]
        at.text_area(key=f"answer_{idx}").set_value("A vague answer.").run()
        self.click(at, "submit_answer")                                              # the 3rd follow-up was answered: no 4th
        t = plain_texts(at)
        self.assertIn("Question 2 of 5", t)
        self.assertNotIn("Follow-up 1 of 3", t)
        self.assertEqual(at.session_state["follow_up_number"], 0)
        self.assertEqual(self.role_of(at), "Frontend Engineer")
        self.assertIn("INTERVIEW QUESTION", t.upper())
        self.assertNotIn("FOLLOW-UP QUESTION", t.upper())


class ResumeAndProgressUiTests(UiTestCase):
    def chen(self):
        return self.candidate_view("Chen Wei")

    def session(self, key):
        return self.api(f"/api/interviews/{self.ids[key]}/session")

    def in_progress_count(self, candidate_id=None):
        params = {"candidate_id": candidate_id} if candidate_id else {}
        return sum(1 for i in self.api("/api/interviews", **params) if i["display_status"] == "In Progress")

    def answer_and_submit(self, at, text):
        idx = at.session_state["current_question_index"]
        at.text_area(key=f"answer_{idx}").set_value(text).run()
        self.click(at, "submit_answer")

    # ---- status on both panels ----
    def test_in_progress_shows_resume_on_the_candidate_panel_and_review_progress_on_the_interviewer_panel(self):
        interview_id = self.ids["resume_pending"]
        chen = self.chen()
        self.assertEqual(chen.button(key=f"resume_{interview_id}").label, "Resume Interview")
        with self.assertRaises(KeyError):
            chen.button(key=f"start_{interview_id}")
        cards = html.unescape(" ".join(str(m.value) for m in chen.markdown if 'class="ti-fields"' in str(m.value)))
        self.assertEqual(cards.count("In Progress</span>"), self.in_progress_count(candidate_id=4))

        dashboard = self.dashboard()
        self.assertEqual(dashboard.button(key=f"progress_{interview_id}").label, "Review Progress")
        with self.assertRaises(KeyError):
            dashboard.button(key=f"review_{interview_id}")             # the full review is only for completed interviews
        rows = html.unescape(" ".join(str(m.value) for m in dashboard.markdown))
        self.assertEqual(rows.count("In Progress</span>"), self.in_progress_count())
        self.assertEqual(stats(dashboard)["In Progress"], str(self.in_progress_count()))

    def test_status_is_in_progress_in_the_api_for_every_interrupted_interview(self):
        for key in ("resume_pending", "refresh_pending", "resume_followup", "resume_eval_missing", "resume_next_missing", "progress_empty"):
            self.assertEqual(self.api(f"/api/interviews/{self.ids[key]}")["display_status"], "In Progress", key)

    # ---- resume ----
    def test_resume_reopens_the_same_interview_at_the_pending_question(self):
        interview_id = self.ids["resume_pending"]
        session = self.session("resume_pending")
        answered_text = session["steps"][0]["question"]
        pending_text = session["pending"]["question"]
        interviews_before = len(self.api("/api/interviews"))

        at = self.chen()
        self.click(at, f"resume_{interview_id}")
        self.assertEqual((at.session_state["page"], at.session_state["interview_id"]), ("interview", interview_id))
        t = plain_texts(at)
        self.assertIn("Question 2 of 5", t)
        self.assertIn(pending_text, t)
        self.assertNotIn(answered_text, t, "an answered question must not be asked again")
        self.assertEqual(at.session_state["submitted_answers"], {0: "My first answer."})
        self.assertEqual(len(self.api("/api/interviews")), interviews_before, "resuming must not create an interview")
        self.assertEqual(self.api(f"/api/interviews/{interview_id}")["display_status"], "In Progress")
        for word in AI_PRIVATE_WORDS:
            self.assertNotIn(word, t, word)

        # the resumed interview carries on normally
        self.answer_and_submit(at, "Answer after resuming.")
        self.assertIn("Question 3 of 5", plain_texts(at))
        self.assertEqual(len(self.session("resume_pending")["steps"]), 3)

    def test_resume_restores_the_follow_up_position_and_history(self):
        at = self.chen()
        self.click(at, f"resume_{self.ids['resume_followup']}")
        t = plain_texts(at)
        self.assertIn("Question 1 of 5", t)
        self.assertIn("Follow-up 2 of 3", t)
        self.assertIn("FOLLOW-UP QUESTION", t.upper())
        self.assertIn("Seeded follow-up two?", t)
        self.assertNotIn("Seeded follow-up one?", t)
        self.assertEqual((at.session_state["main_question_number"], at.session_state["follow_up_number"]), (1, 2))
        self.assertEqual(sorted(at.session_state["submitted_answers"].values()), ["Follow-up one answer.", "Main answer."])

    def test_resume_recovers_an_answer_that_was_saved_but_never_evaluated(self):
        interview_id = self.ids["resume_eval_missing"]
        at = self.chen()
        self.click(at, f"resume_{interview_id}")
        self.assertEqual(at.session_state["page"], "interview")
        self.assertIn("Question 2 of 5", plain_texts(at))
        session = self.session("resume_eval_missing")
        self.assertEqual([s["state"] for s in session["steps"]], ["evaluated", "pending"])
        self.assertEqual(session["steps"][0]["answer"], "Saved but not evaluated.")
        review = self.api(f"/api/interviews/{interview_id}/progress")
        self.assertEqual((review["answers_submitted"], review["answers_evaluated"]), (1, 1), "evaluated once, answer not duplicated")

    def test_resume_recovers_when_the_next_question_was_never_fetched(self):
        at = self.chen()
        self.click(at, f"resume_{self.ids['resume_next_missing']}")
        self.assertIn("Question 2 of 5", plain_texts(at))
        session = self.session("resume_next_missing")
        self.assertEqual([s["state"] for s in session["steps"]], ["evaluated", "pending"])
        self.assertEqual(session["pending"]["interview_question_id"], at.session_state["current_question"]["id"])

    # ---- refresh ----
    def test_refreshing_the_browser_restores_the_interview_and_its_progress(self):
        interview_id = self.ids["refresh_pending"]
        session = self.session("refresh_pending")
        at = self.app(query={"resume": str(interview_id)})                 # a refresh: empty session_state, same URL
        self.assertEqual((at.session_state["page"], at.session_state["interview_id"]), ("interview", interview_id))
        t = plain_texts(at)
        self.assertIn("Question 2 of 5", t)
        self.assertIn(session["pending"]["question"], t)
        self.assertNotIn(session["steps"][0]["question"], t)
        self.assertIn("Chen Wei", t)
        self.assertIn("Backend Engineer", t)
        self.assertEqual(at.session_state["submitted_answers"], {0: "Answer before the refresh."})

        self.answer_and_submit(at, "Carrying on after the refresh.")
        self.assertIn("Question 3 of 5", plain_texts(at))

    def test_a_refresh_with_nothing_to_resume_falls_back_to_the_start_page(self):
        for value in (str(self.ids["with_4d"]), "999999", "not-a-number", str(self.ids["scheduled_qb"])):
            at = self.app(query={"resume": value})
            self.assertEqual(at.session_state["page"], "landing", value)
            self.assertNotIn("resume", at.query_params, value)

    def test_the_interview_id_is_kept_in_the_url_while_interviewing_and_dropped_after_exit(self):
        interview_id = self.ids["exit_url"]
        at = self.app(query={"resume": str(interview_id)})
        self.assertEqual(at.query_params["resume"], [str(interview_id)] if isinstance(at.query_params["resume"], list) else str(interview_id))
        self.click(at, "btn-exit")
        self.click(at, "confirm_exit_yes")
        self.assertEqual(at.session_state["page"], "candidate")
        self.assertNotIn("resume", at.query_params)

    def test_cancelling_the_exit_keeps_the_interview_in_progress(self):
        interview_id = self.ids["exit_flow"]
        at = self.chen()
        self.click(at, f"resume_{interview_id}")
        self.click(at, "btn-exit")
        self.click(at, "confirm_exit_no")
        self.assertEqual(at.session_state["page"], "interview")
        self.assertEqual(self.api(f"/api/interviews/{interview_id}")["display_status"], "In Progress")

    def test_confirmed_exit_abandons_keeps_the_data_and_removes_resume(self):
        import requests
        interview_id = self.ids["exit_flow"]
        before = self.api(f"/api/interviews/{interview_id}/progress")
        at = self.chen()
        self.click(at, f"resume_{interview_id}")
        self.click(at, "btn-exit")
        self.click(at, "confirm_exit_yes")
        self.assertEqual(at.session_state["page"], "candidate")
        iv = self.api(f"/api/interviews/{interview_id}")
        self.assertEqual((iv["display_status"], iv["status"]), ("Abandoned", "abandoned"))
        self.assertTrue(iv["abandoned_at"])
        self.assertIsNone(iv["completed_at"])
        self.assertEqual(self.api(f"/api/interviews/{interview_id}/progress")["main_questions"], before["main_questions"])   # nothing lost
        at.selectbox(key="candidate_selector").select("Chen Wei").run()
        with self.assertRaises(KeyError):
            at.button(key=f"resume_{interview_id}")                                  # no Resume option any more
        self.assertIn("cannot be resumed", plain_texts(at))
        refreshed = self.app(query={"resume": str(interview_id)})                    # a stale refresh link cannot bring it back
        self.assertNotEqual(refreshed.session_state["page"], "interview")
        question_id = before["main_questions"][0]["question"]["interview_question_id"]
        r = requests.post(f"{self.base}/api/interviews/{interview_id}/questions/{question_id}/answer", json={"answer_text": "late"})
        self.assertEqual(r.status_code, 400)
        again = requests.post(f"{self.base}/api/interviews/{interview_id}/abandon")           # a repeated exit is harmless
        self.assertEqual((again.status_code, again.json()["abandoned_at"]), (200, iv["abandoned_at"]))

    def test_candidates_never_see_abandoned_interview_internals(self):
        t = plain_texts(self.chen())
        self.assertNotIn("Partial Interview", t)
        self.assertNotIn("injection", t.lower())

    # ---- interviewer: partial progress ----
    def test_review_progress_shows_what_has_been_saved_so_far(self):
        interview_id = self.ids["progress_partial"]
        at = self.click(self.dashboard(), f"progress_{interview_id}")
        self.assertEqual(at.session_state["page"], "interviewer_progress")
        t = plain_texts(at)
        for expected in ("Interview Progress", "Chen Wei", "Backend Engineer", "Question Bank", "In Progress",
                         "Main question 1 of 5", "Follow-up 1 of 3", "Follow-ups: 1 of 3",
                         "Main answer text.", "Follow-up answer text.", "Partial follow-up question?",
                         "Evaluation score: 6 / 10", "Started:", "Last activity:"):
            self.assertIn(expected, t)
        self.assertIn("Evidence: “problem_solving quote”", html.unescape(visible_texts(at)))
        info = next(str(m.value) for m in at.markdown if "Interview Status" in str(m.value))
        self.assertIn("In Progress", info)

        values = stats(at)
        self.assertEqual((values["Main Questions Answered"], values["Follow-ups Asked"], values["Answers Submitted"], values["Answers Evaluated"]),
                         ("1", "1", "2", "1"))
        html_text = html.unescape(" ".join(str(m.value) for m in at.markdown))
        self.assertIn("Evaluated</span>", html_text)                     # the main answer
        self.assertIn("Awaiting evaluation</span>", html_text)           # the follow-up answer: saved, not yet evaluated
        self.assertEqual(len(at.get("expander")), 0)

    def test_review_progress_is_not_a_final_review_and_changes_nothing(self):
        interview_id = self.ids["progress_partial"]
        before = self.api("/api/interviews")
        at = self.click(self.dashboard(), f"progress_{interview_id}")
        labels = button_labels(at)
        self.assertFalse(any("Accept" in label or "Override" in label for label in labels), labels)
        self.assertIn("No decision can be recorded", plain_texts(at))
        self.assertNotIn("Recommendation", plain_texts(at))
        self.assertNotIn("FINAL", plain_texts(at))

        import requests
        self.assertEqual(requests.get(f"{self.base}/api/interviews/{interview_id}/human-review").status_code, 404)
        self.assertEqual(requests.get(f"{self.base}/api/interviews/{interview_id}/review-data").status_code, 400)
        after = self.api("/api/interviews")
        self.assertEqual([(i["id"], i["status"], i["display_status"], i["completed_at"]) for i in before],
                         [(i["id"], i["status"], i["display_status"], i["completed_at"]) for i in after])

    def test_progress_with_no_answers_shows_a_helpful_empty_state(self):
        at = self.click(self.dashboard(), f"progress_{self.ids['progress_empty']}")
        self.assertFalse(at.exception)
        t = plain_texts(at)
        self.assertIn("No answers have been submitted yet.", t)
        self.assertIn("Pending answer", t)
        self.assertIn("No answer submitted yet.", t)
        values = stats(at)
        self.assertEqual((values["Answers Submitted"], values["Answers Evaluated"], values["Main Questions Answered"]), ("0", "0", "0"))
        self.assertEqual(len(at.error), 0)

    def test_progress_page_for_a_completed_interview_points_to_the_full_review(self):
        at = self.app("interviewer_progress", authenticated=True, state={"progress_interview_id": self.ids["with_4d"]})
        self.assertIn("This interview is complete", plain_texts(at))
        self.click(at, "progress_open_review")
        self.assertEqual((at.session_state["page"], at.session_state["review_interview_id"]), ("interviewer_review", self.ids["with_4d"]))

    def test_progress_page_is_gated_behind_the_interviewer_passcode(self):
        at = self.app("interviewer_progress")
        self.assertEqual(at.session_state["page"], "interviewer_login")

    def test_candidates_never_see_scores_or_evaluations_while_resuming(self):
        at = self.chen()
        self.click(at, f"resume_{self.ids['resume_pending']}")
        t = plain_texts(at)
        for word in ("Evaluation score", "Awaiting evaluation", "Evaluated", "Score:", "Evidence"):
            self.assertNotIn(word, t, word)


class InterviewerAccessTests(UiTestCase):
    def test_wrong_passcode_is_rejected(self):
        at = self.login("0000")
        self.assertIn("Invalid passcode.", [e.value for e in at.error])
        self.assertEqual(at.session_state["page"], "interviewer_login")
        self.assertFalse(is_authenticated(at))

    def test_empty_passcode_is_rejected(self):
        at = self.login("")
        self.assertIn("Invalid passcode.", [e.value for e in at.error])
        self.assertEqual(at.session_state["page"], "interviewer_login")

    def test_default_demo_passcode_is_not_hardcoded_in_the_ui(self):
        at = self.login("0810")                       # the real .env value; this test server uses a different one
        self.assertIn("Invalid passcode.", [e.value for e in at.error])
        source = open(os.path.join(FRONTEND_DIR, "app.py"), encoding="utf-8").read()
        self.assertNotIn("0810", source)
        self.assertNotIn("INTERVIEWER_PASSCODE", source)

    def test_correct_passcode_opens_dashboard(self):
        at = self.login(PASSCODE)
        self.assertEqual(at.session_state["page"], "interviewer_dashboard")
        self.assertTrue(at.session_state["interviewer_authenticated"])
        self.assertIn("Interviewer Dashboard", plain_texts(at))

    def test_pages_are_gated_without_login(self):
        for page in ("interviewer_dashboard", "interviewer_schedule", "interviewer_review"):
            at = self.app(page)
            self.assertEqual(at.session_state["page"], "interviewer_login", page)
            self.assertIn("INTERVIEWER ACCESS", plain_texts(at))
            self.assertNotIn("Interviewer Dashboard", plain_texts(at))

    def test_logout_clears_access(self):
        at = self.dashboard()
        self.click(at, "dash_logout")
        self.assertEqual(at.session_state["page"], "landing")
        self.assertFalse(is_authenticated(at))
        at.session_state["page"] = "interviewer_dashboard"
        at.run()
        self.assertEqual(at.session_state["page"], "interviewer_login")

    def test_interviewer_access_button_skips_login_when_already_authenticated(self):
        at = self.app("landing", authenticated=True)
        self.click(at, "landing_interviewer")
        self.assertEqual(at.session_state["page"], "interviewer_dashboard")

    def test_passcode_field_is_masked(self):
        at = self.app("interviewer_login")
        self.assertEqual(at.text_input(key="passcode_input").proto.type, 1)       # PASSWORD


class DashboardAndScheduleTests(UiTestCase):
    def status_counts_from_api(self):
        interviews = self.api("/api/interviews")
        return {name: sum(1 for i in interviews if i["display_status"] == name) for name in ("Scheduled", "In Progress", "Completed", "Reviewed")}

    def test_summary_cards_match_the_interview_api(self):
        at = self.dashboard()
        values = stats(at)
        expected = self.status_counts_from_api()
        for name, count in expected.items():
            self.assertEqual(values[name], str(count), name)
        self.assertGreater(expected["Scheduled"], 0)

    def test_dashboard_header_and_actions(self):
        at = self.dashboard()
        t = plain_texts(at)
        self.assertIn("Interviewer Dashboard", t)
        self.assertIn("TECHNICAL INTERVIEWER", t.upper())
        self.assertIn("+ Schedule Interview", button_labels(at))
        self.assertIn("Logout", button_labels(at))

    def test_interview_list_shows_names_status_and_review_only_for_completed(self):
        at = self.dashboard()
        self.assertTrue(at.button(key=f"review_{self.ids['with_4d']}"))
        self.assertTrue(at.button(key=f"review_{self.ids['reviewed']}"))
        for key in ("scheduled_qb", "in_progress", "scheduled_cv_missing"):
            with self.assertRaises(KeyError):
                at.button(key=f"review_{self.ids[key]}")
        rows = html.unescape(" ".join(str(m.value) for m in at.markdown))
        self.assertIn("Aarav Sharma", rows)
        self.assertIn("Backend Engineer", rows)

    def test_dashboard_status_filter(self):
        at = self.dashboard()
        at.selectbox(key="dash_status").select("Reviewed").run()
        self.assertTrue(at.button(key=f"review_{self.ids['reviewed']}"))
        with self.assertRaises(KeyError):
            at.button(key=f"review_{self.ids['with_4d']}")

    def test_schedule_interview_returns_to_dashboard_with_success_message(self):
        before = self.status_counts_from_api()["Scheduled"]
        at = self.click(self.dashboard(), "dash_schedule")
        self.assertEqual(at.session_state["page"], "interviewer_schedule")
        self.assertIn("Schedule Interview", plain_texts(at))

        at.selectbox(key="schedule_candidate").select("Priya Patel")
        at.selectbox(key="schedule_job").select("Frontend Engineer")
        at.radio(key="schedule_mode").set_value("Question Bank")
        self.click(at, "schedule_submit")

        self.assertEqual(at.session_state["page"], "interviewer_dashboard")
        self.assertIn("Interview scheduled successfully.", [s.value for s in at.success])
        self.assertEqual(self.status_counts_from_api()["Scheduled"], before + 1)
        self.assertEqual(stats(at)["Scheduled"], str(before + 1))
        newest = max(self.api("/api/interviews", candidate_id=2), key=lambda i: i["id"])
        self.assertEqual((newest["mode"], newest["display_status"], newest["job_title"]), ("question_bank", "Scheduled", "Frontend Engineer"))

    def test_cv_modes_for_a_candidate_without_a_saved_cv_require_an_upload(self):
        for mode in ("Hybrid", "CV Driven"):
            at = self.click(self.dashboard(), "dash_schedule")
            at.selectbox(key="schedule_candidate").select("Aarav Sharma")
            at.radio(key="schedule_mode").set_value(mode).run()
            self.assertIn("Aarav Sharma has no saved CV", " ".join(w.value for w in at.warning))
            self.assertTrue(at.get("file_uploader"))
            self.assertTrue(at.button(key="schedule_submit").disabled)               # cannot schedule without a CV
        before = len(self.api("/api/interviews"))
        self.assertEqual(len(self.api("/api/interviews")), before)

    def test_cv_modes_for_a_candidate_with_a_saved_cv_need_no_upload(self):
        for mode, key in (("CV Driven", "cv_driven"), ("Hybrid", "hybrid")):
            at = self.click(self.dashboard(), "dash_schedule")
            at.selectbox(key="schedule_candidate").select("Priya Patel")
            at.selectbox(key="schedule_job").select("Frontend Engineer")
            at.radio(key="schedule_mode").set_value(mode).run()
            self.assertIn("Saved CV on file for Priya Patel: priya_patel_cv.pdf", " ".join(s.value for s in at.success))
            self.assertFalse(at.button(key="schedule_submit").disabled)
            self.assertEqual([e.label for e in at.expander], ["Use a different CV for this interview only"])   # replacement is optional
            before = {i["id"] for i in self.api("/api/interviews")}
            self.click(at, "schedule_submit")
            created = [i for i in self.api("/api/interviews") if i["id"] not in before]
            self.assertEqual(len(created), 1)
            self.assertEqual((created[0]["mode"], created[0]["candidate_name"], created[0]["display_status"]), (key, "Priya Patel", "Scheduled"))
            self.assertEqual(created[0]["cv_text"], CV)                              # the saved CV was copied to the interview
            self.assertTrue(created[0]["cv_available"])

    def test_the_candidate_is_not_asked_to_upload_a_cv_that_is_already_saved(self):
        at = self.candidate_view("Priya Patel")
        cv_interviews = [i for i in self.api("/api/interviews", candidate_id=2) if i["mode"] in ("cv_driven", "hybrid") and i["status"] == "not_started"]
        self.assertTrue(cv_interviews)
        self.assertEqual(len(at.get("file_uploader")), 0)
        self.assertNotIn("Resume required", plain_texts(at))

    def test_schedule_question_bank_mode_has_no_cv_field(self):
        at = self.click(self.dashboard(), "dash_schedule")
        at.radio(key="schedule_mode").set_value("Question Bank").run()
        self.assertFalse(at.get("file_uploader"))

    def test_back_button_on_schedule_page(self):
        at = self.click(self.dashboard(), "dash_schedule")
        self.click(at, "btn-schedule")
        self.assertEqual(at.session_state["page"], "interviewer_dashboard")


class UsageDashboardTests(UiTestCase):
    EXPECTED_COLUMNS = ["Time (UTC)", "Candidate", "Interview", "AI Operation", "Model", "Input Tokens",
                        "Output Tokens", "Total Tokens", "Latency", "Status", "Estimated Cost"]

    def usage_table(self, at):
        self.assertEqual(len(at.dataframe), 1)
        return at.dataframe[0].value

    def test_usage_section_sits_below_status_cards_and_uses_real_values(self):
        at = self.dashboard()
        t = plain_texts(at)
        self.assertLess(t.index("Scheduled"), t.index("AI Usage & Cost"))
        values = stats(at)
        # 6 LLM calls (tts excluded), 5 successful; tokens are the recorded provider counts
        self.assertEqual(values["Total LLM Calls"], "6")
        self.assertEqual(values["Total Tokens"], "10,600")
        self.assertEqual(values["Success Rate"], "83%")
        self.assertEqual(values["Average Latency"], "2.8s")
        self.assertEqual(values["Estimated Cost"], "$0.04")
        self.assertIn("7,300 in / 3,300 out", html.unescape(" ".join(str(m.value) for m in at.markdown)))

    def test_cost_is_labelled_as_an_estimate_from_actual_tokens(self):
        at = self.dashboard()
        t = plain_texts(at)
        self.assertIn("Estimated Cost", t)
        self.assertIn("From actual token usage", t)
        self.assertIn("cost is an estimate", t)

    def test_usage_details_expander_and_table_columns(self):
        at = self.dashboard()
        details = [x for x in at.expander if x.label == "LLM Usage Details"]
        self.assertEqual(len(details), 1)
        self.assertFalse(details[0].proto.expanded)
        table = self.usage_table(at)
        self.assertEqual(list(table.columns), self.EXPECTED_COLUMNS)
        self.assertEqual(len(table), 7)                                       # 6 LLM rows + 1 tts row

    def test_usage_table_uses_names_and_friendly_operation_labels_not_ids(self):
        rows = self.usage_table(self.dashboard()).to_dict("records")
        evaluation = [r for r in rows if r["AI Operation"] == "Technical Evaluation" and r["Input Tokens"] == "1,000"][0]
        self.assertEqual(evaluation["Candidate"], "Aarav Sharma")
        self.assertEqual(evaluation["Interview"], "Backend Engineer")
        self.assertEqual((evaluation["Model"], evaluation["Output Tokens"], evaluation["Total Tokens"]), ("gpt-4o-mini", "500", "1,500"))
        self.assertEqual((evaluation["Latency"], evaluation["Status"], evaluation["Estimated Cost"]), ("2.0s", "Success", "$0.0005"))
        self.assertEqual({r["AI Operation"] for r in rows},
                         {"Technical Evaluation", "Recommendation", "CV Question Generation", "Text-to-Speech"})
        failed = [r for r in rows if r["Status"] == "Error"][0]
        self.assertEqual((failed["Input Tokens"], failed["Estimated Cost"]), ("—", "—"))     # nothing invented
        tts = [r for r in rows if r["AI Operation"] == "Text-to-Speech"][0]
        self.assertEqual((tts["Candidate"], tts["Estimated Cost"]), ("—", "—"))
        self.assertNotIn("agent", " ".join(self.EXPECTED_COLUMNS).lower())

    def test_candidate_filter(self):
        at = self.dashboard()
        at.selectbox(key="usage_candidate").select("Priya Patel").run()
        rows = self.usage_table(at).to_dict("records")
        self.assertEqual({r["Candidate"] for r in rows}, {"Priya Patel"})
        self.assertEqual({r["AI Operation"] for r in rows}, {"CV Question Generation"})
        self.assertIn("1 LLM call(s)", plain_texts(at))

    def test_operation_filter(self):
        at = self.dashboard()
        at.selectbox(key="usage_operation").select("Technical Evaluation").run()
        rows = self.usage_table(at).to_dict("records")
        self.assertEqual({r["AI Operation"] for r in rows}, {"Technical Evaluation"})
        self.assertEqual(len(rows), 4)

    def test_status_filter(self):
        at = self.dashboard()
        at.selectbox(key="usage_status").select("Error").run()
        rows = self.usage_table(at).to_dict("records")
        self.assertEqual([r["Status"] for r in rows], ["Error"])
        at.selectbox(key="usage_status").select("Success").run()
        self.assertEqual({r["Status"] for r in self.usage_table(at).to_dict("records")}, {"Success"})

    def test_interview_filter_and_combined_filters(self):
        at = self.dashboard()
        options = at.selectbox(key="usage_interview").options
        self.assertEqual(options[0], "All Interviews")
        self.assertEqual(len(set(options)), len(options), "interview options must be distinguishable")
        # names, not ids, unless two interviews would otherwise look identical
        self.assertTrue(any(o.startswith("Priya Patel — Frontend Engineer — Question Bank") and "#" not in o for o in options))
        self.assertTrue(all(re.search(r"\(#\d+\)$", o) for o in options if "#" in o))

        # pick one specific interview: only its usage rows remain
        at.selectbox(key="usage_candidate").select("Priya Patel").run()
        priya_options = at.selectbox(key="usage_interview").options
        self.assertEqual(len(priya_options), 2)                                      # All + her one interview
        at.selectbox(key="usage_interview").select(priya_options[1]).run()
        rows = self.usage_table(at).to_dict("records")
        self.assertEqual({r["Candidate"] for r in rows}, {"Priya Patel"})
        self.assertEqual(len(rows), 1)

        # combined: candidate + operation with no overlap shows the empty state
        at.selectbox(key="usage_operation").select("Technical Evaluation").run()
        self.assertEqual(len(at.dataframe), 0)
        self.assertIn("No usage recorded for these filters.", plain_texts(at))

    def test_filters_with_no_matches_show_an_empty_state(self):
        at = self.dashboard()
        at.selectbox(key="usage_candidate").select("Marcus Johnson").run()
        self.assertEqual(len(at.dataframe), 0)
        self.assertIn("No usage recorded for these filters.", plain_texts(at))

    def test_interview_specific_usage_on_review_page(self):
        values = stats(self.review_view(self.ids["with_4d"]))
        # three logged calls: 1000/500, 2000/1000, 500/100 on gpt-4o-mini
        self.assertEqual(values["Total LLM Calls"], "3")
        self.assertEqual(values["Total Tokens"], "5,100")
        self.assertEqual(values["Estimated Cost"], "$0.0015")
        self.assertEqual(values["Average Latency"], "3.0s")
        self.assertEqual(values["Success Rate"], "100%")

        legacy = stats(self.review_view(self.ids["legacy"]))
        self.assertEqual((legacy["Total LLM Calls"], legacy["Success Rate"], legacy["Total Tokens"]), ("2", "50%", "1,000"))
        self.assertEqual(legacy["Estimated Cost"], "$0.04")                    # gpt-4: 800 in / 200 out

        none_logged = stats(self.review_view(self.ids["accept_me"]))
        self.assertEqual((none_logged["Total LLM Calls"], none_logged["Total Tokens"], none_logged["Estimated Cost"]), ("0", "0", "$0.00"))
        self.assertEqual((none_logged["Average Latency"], none_logged["Success Rate"]), ("—", "—"))

    def test_candidate_pages_never_show_usage(self):
        for at in (self.candidate_view(), self.app("results")):
            t = plain_texts(at)
            for word in ("token", "Token", "Estimated Cost", "gpt-", "Latency", "LLM", "AI Usage"):
                self.assertNotIn(word, t, word)
            self.assertEqual(len(at.dataframe), 0)


class ReviewTests(UiTestCase):
    def test_review_page_layout_and_overall_4d(self):
        at = self.review_view(self.ids["with_4d"])
        self.assertEqual(at.session_state["page"], "interviewer_review")
        values = stats(at)
        # per-dimension means: TS (4+5)/2=4.5, DU 3.0, PS (5+4)/2=4.5, CC (2+4)/2=3.0
        self.assertEqual(
            (values["Technical Skills"], values["Domain Understanding"], values["Problem-Solving Approach"], values["Communication Clarity"]),
            ("4.5", "3.0", "4.5", "3.0"),
        )
        self.assertEqual(values["Overall Score"], "7")                          # existing 0-10 score unchanged
        self.assertEqual(values["Confidence"], "High")
        t = plain_texts(at)
        self.assertIn("Overall AI Evaluation", t)
        self.assertIn("AI Recommendation — Not Final Decision", t)
        self.assertIn("Hire", t)
        self.assertIn(SECRET_REASON, t)
        self.assertIn("Average of 2 questions", t)
        self.assertNotIn("Overall 4D evaluation unavailable", t)

        info = next(str(m.value) for m in at.markdown if "Candidate Name" in str(m.value))
        for expected in ("Aarav Sharma", "Backend Engineer", "Question Bank", "Completed"):
            self.assertIn(expected, info)

        flat = [(e.type, html.unescape(str(getattr(e, "value", "") or getattr(e, "label", "")))) for e in walk(at.main)]
        index = lambda pred: next(i for i, (typ, v) in enumerate(flat) if pred(typ, v))  # noqa: E731
        overall = index(lambda typ, v: typ == "markdown" and "Overall AI Evaluation" in v)
        human = index(lambda typ, v: typ == "markdown" and ">Human Review<" in v)
        details = index(lambda typ, v: typ == "expander" and v == "Question-wise Details")
        usage = index(lambda typ, v: typ == "markdown" and "AI Usage for This Interview" in v)
        self.assertLess(overall, human)
        self.assertLess(human, details)
        self.assertLess(details, usage)

    def test_question_wise_details_collapsed_and_keep_evidence(self):
        at = self.review_view(self.ids["with_4d"])
        details = [x for x in at.expander if x.label == "Question-wise Details"]
        self.assertEqual(len(details), 1)
        self.assertFalse(details[0].proto.expanded, "Question-wise Details must be collapsed by default")
        inner = html.unescape("\n".join(str(m.value) for m in details[0].markdown))
        for expected in ("My answer 1", "My answer 2", "Candidate Answer", "Score: 5 / 5",
                         "Evidence: “problem_solving quote”", "technical_skills justification text"):
            self.assertIn(expected, inner)

    def test_old_interview_without_4d_does_not_crash_or_fabricate(self):
        at = self.review_view(self.ids["legacy"])
        t = plain_texts(at)
        self.assertIn("Overall 4D evaluation unavailable for this interview.", t)
        values = stats(at)
        for label in ("Technical Skills", "Domain Understanding", "Problem-Solving Approach", "Communication Clarity"):
            self.assertNotIn(label, values)                                    # no invented dimension scores
        self.assertEqual(values["Overall Score"], "6")                         # existing score still shown
        self.assertEqual(values["Confidence"], "High")
        self.assertIn("Hold", t)                                               # legacy 'consider' normalised
        self.assertIn(SECRET_REASON, t)
        details = next(x for x in at.expander if x.label == "Question-wise Details")
        self.assertEqual(sum("4D evaluation unavailable for this question." in str(i.value) for i in details.info), 2)

    def test_reviewed_interview_shows_final_human_decision_separately(self):
        at = self.review_view(self.ids["reviewed"])
        t = plain_texts(at)
        self.assertIn("FINAL HUMAN DECISION", t)
        self.assertIn("Strong Hire", t)                                         # human decision
        self.assertIn("Human Override", t)
        self.assertIn("AI recommended: Hire (unchanged)", t)
        self.assertIn("Reviewed", t)
        with self.assertRaises(KeyError):
            at.button(key=f"accept_{self.ids['reviewed']}")                     # read-only once reviewed

    def test_accept_ai_recommendation(self):
        iid = self.ids["accept_me"]
        at = self.review_view(iid)
        self.assertIn("Accept AI Recommendation", button_labels(at))
        self.click(at, f"accept_{iid}")
        t = plain_texts(at)
        self.assertIn("FINAL HUMAN DECISION", t)
        self.assertIn("AI Accepted", t)
        review = self.api(f"/api/interviews/{iid}/human-review")
        self.assertEqual((review["decision_type"], review["decision"]), ("ACCEPT", "Hire"))
        self.assertEqual(self.api(f"/api/interviews/{iid}")["display_status"], "Reviewed")
        with self.assertRaises(KeyError):
            at.button(key=f"accept_{iid}")

    def test_override_requires_reason_and_a_different_decision(self):
        iid = self.ids["override_me"]
        at = self.review_view(iid)
        self.click(at, f"override_btn_{iid}")
        self.assertNotIn("Hire", at.selectbox(key=f"override_decision_{iid}").options)     # AI's own decision not offered

        self.click(at, f"override_submit_{iid}")
        self.assertTrue(any("Reason is required" in e.value for e in at.error))
        at.text_area(key=f"override_reason_{iid}").set_value("too short").run()
        self.click(at, f"override_submit_{iid}")
        self.assertTrue(any("at least 20 characters" in e.value for e in at.error))
        import requests
        self.assertEqual(requests.get(f"{self.base}/api/interviews/{iid}/human-review").status_code, 404)   # nothing saved

        reason = "Candidate showed strong practical judgement in follow-ups."
        at.selectbox(key=f"override_decision_{iid}").select("Strong Hire").run()
        at.text_area(key=f"override_reason_{iid}").set_value(reason).run()
        self.click(at, f"override_submit_{iid}")
        t = plain_texts(at)
        self.assertIn("FINAL HUMAN DECISION", t)
        self.assertIn("Human Override", t)
        self.assertIn(reason, t)
        self.assertIn("AI recommended: Hire (unchanged)", t)
        review = self.api(f"/api/interviews/{iid}/human-review")
        self.assertEqual((review["decision_type"], review["decision"], review["ai_recommendation"]), ("OVERRIDE", "Strong Hire", "Hire"))

    def test_back_button_returns_to_dashboard(self):
        at = self.review_view(self.ids["with_4d"])
        self.click(at, "btn-review")
        self.assertEqual(at.session_state["page"], "interviewer_dashboard")

    def test_review_content_is_html_escaped(self):
        # dynamic text is escaped before being placed into HTML blocks
        from ui_theme import esc
        self.assertEqual(esc("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertEqual(esc("cost $1 and $2"), "cost &#36;1 and &#36;2")
        self.assertEqual(esc("line1\n\nline2"), "line1<br><br>line2")


class ConsentUiTests(UiTestCase):
    def dana(self):
        return self.candidate_view("Dana Lopez")

    def consent_of(self, key):
        return self.api(f"/api/interviews/{self.ids[key]}")["consent_status"]

    def test_the_notice_is_shown_before_start_with_an_unchecked_box_and_a_disabled_start_button(self):
        at = self.dana()
        t = plain_texts(at)
        for expected in ("Before You Begin", "AI Evaluation", "What Information Is Stored", "Who Can See It", "Where Data Is Stored",
                         "technical_interviewer.db", "Sharing With the AI Provider", "OpenAI", "Data Retention", "180 days",
                         "nothing is deleted automatically", "Data or Deletion Requests", "the interview will not start"):
            self.assertIn(expected, t)
        sections = [m for m in at.markdown if 'class="ti-consent-section"' in str(m.value)]
        self.assertTrue(sections)
        self.assertNotIn("<table", " ".join(str(m.value) for m in sections))        # vertical sections, not a table
        self.assertEqual(str(sections[0].value).count('class="ti-consent-section"'), 7)
        key = self.ids["consent_agree"]
        self.assertFalse(at.checkbox(key=f"consent_check_{key}").value)
        self.assertTrue(at.button(key=f"start_{key}").disabled)
        self.assertEqual(at.button(key=f"start_{key}").label, "Agree and Start Interview")
        self.assertEqual(at.button(key=f"decline_{key}").label, "Decline")
        self.assertIsNone(self.consent_of("consent_agree"))                      # merely viewing the notice records nothing
        self.assertNotIn("claim", t.lower().replace("claimed", ""))

    def test_ticking_the_box_enables_start_which_records_consent_and_starts(self):
        at = self.dana()
        key = self.ids["consent_agree"]
        at.checkbox(key=f"consent_check_{key}").check().run()
        self.assertFalse(at.button(key=f"start_{key}").disabled)
        self.assertIsNone(self.consent_of("consent_agree"))                      # ticking alone stores nothing
        self.click(at, f"start_{key}")
        self.assertEqual(at.session_state["page"], "interview")
        interview = self.api(f"/api/interviews/{key}")
        self.assertEqual((interview["consent_status"], interview["status"]), ("granted", "in_progress"))
        self.assertTrue(interview["consent_recorded_at"])

    def test_declining_stores_the_decision_and_nothing_starts(self):
        at = self.dana()
        key = self.ids["consent_decline"]
        at.checkbox(key=f"consent_check_{key}").check().run()
        self.click(at, f"decline_{key}")
        self.assertEqual(at.session_state["page"], "candidate")
        interview = self.api(f"/api/interviews/{key}")
        self.assertEqual((interview["consent_status"], interview["status"], interview["display_status"]), ("declined", "not_started", "Scheduled"))
        self.assertIsNone(interview["completed_at"])
        self.assertIn("You declined consent", plain_texts(at))
        self.assertFalse(at.checkbox(key=f"consent_check_{key}").value)          # a fresh, unchecked box: no pre-ticked consent
        self.assertTrue(at.button(key=f"start_{key}").disabled)
        import requests
        self.assertEqual(requests.post(f"{self.base}/api/interviews/{key}/start").status_code, 403)

    def test_an_in_progress_interview_without_consent_asks_before_resuming_and_keeps_its_progress(self):
        key = self.ids["legacy_no_consent"]
        before = self.api(f"/api/interviews/{key}/progress")
        at = self.dana()
        self.assertEqual(at.button(key=f"resume_{key}").label, "Agree and Resume Interview")
        self.assertTrue(at.button(key=f"resume_{key}").disabled)
        self.assertIn("started before consent was recorded", plain_texts(at))
        self.assertEqual(at.session_state["page"], "candidate")
        self.assertEqual(self.api(f"/api/interviews/{key}/progress"), before)    # nothing changed, nothing deleted
        at.checkbox(key=f"consent_check_{key}").check().run()
        self.click(at, f"resume_{key}")
        self.assertEqual(at.session_state["page"], "interview")
        self.assertEqual(self.consent_of("legacy_no_consent"), "granted")
        self.assertIn("Answer given before consent was recorded.", plain_texts(at) + str(at.session_state["submitted_answers"]))

    def test_a_refresh_link_cannot_bypass_consent(self):
        at = self.app("candidate", query={"resume": str(self.ids["legacy_no_consent"])})
        self.assertNotEqual(at.session_state["page"], "interview")

    def test_consented_interviews_show_no_notice(self):
        t = plain_texts(self.candidate_view("Aarav Sharma"))
        self.assertNotIn("Before You Begin", t)

    def test_candidates_never_see_security_flags(self):
        for name in ("Aarav Sharma", "Dana Lopez"):
            self.assertNotIn("injection", plain_texts(self.candidate_view(name)).lower())


class FeedbackReportUiTests(UiTestCase):
    def downloads(self, at):
        return [e for e in walk(at.main) if e.type == "download_button"]

    def test_the_review_page_has_the_report_and_both_download_buttons(self):
        at = self.review_view(self.ids["with_4d"])
        t = plain_texts(at)
        for expected in ("Feedback Report", "Overall Assessment", "Strengths", "Knowledge Gaps", "Suggested Areas for the Next Round",
                         "Prompt-Injection Check", SECRET_REASON):
            self.assertIn(expected, t)
        downloads = self.downloads(at)
        self.assertEqual(sorted(d.proto.label for d in downloads), ["Download Markdown", "Download PDF"])

    def test_nothing_checked_is_shown_as_unknown_never_as_clean(self):
        t = plain_texts(self.review_view(self.ids["with_4d"]))
        self.assertIn("Security status unknown", t)
        self.assertNotIn("No suspected prompt-injection attempt detected", t)

    def test_a_suspected_injection_is_shown_to_the_interviewer(self):
        import sqlite3
        db = sqlite3.connect(os.path.join(self.tmp.name, "technical_interviewer.db"))
        answer_id = db.execute("select a.id from answers a join interview_questions iq on iq.id=a.interview_question_id where iq.interview_id=? order by a.id",
                               (self.ids["accept_me"],)).fetchone()[0]
        db.execute("insert into content_security_checks (interview_id,source,source_id,status,categories,excerpt,explanation,checked_at) values (?,?,?,?,?,?,?,?)",
                   (self.ids["accept_me"], "answer", answer_id, "suspected", '["instruction_override"]', "Ignore previous instructions", "asks the AI to ignore its rules", NOW))
        db.commit()
        db.close()
        t = plain_texts(self.review_view(self.ids["accept_me"]))
        self.assertIn("Suspected prompt injection", t)
        self.assertIn("Answer to Question 1", t)

    def test_the_report_files_come_from_the_backend_and_open_as_real_documents(self):
        import requests
        pdf = requests.get(f"{self.base}/api/interviews/{self.ids['with_4d']}/report.pdf")
        md = requests.get(f"{self.base}/api/interviews/{self.ids['with_4d']}/report.md")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        self.assertIn("# Interview Feedback Report", md.text)
        self.assertEqual(requests.get(f"{self.base}/api/interviews/{self.ids['in_progress']}/report.pdf").status_code, 400)


class AbandonedReviewUiTests(UiTestCase):
    def test_dashboard_lists_it_as_abandoned_with_a_review_action(self):
        at = self.dashboard()
        key = self.ids["abandoned_partial"]
        self.assertEqual(at.button(key=f"review_{key}").label, "Review")
        row = html.unescape(" ".join(str(m.value) for m in at.markdown))
        self.assertIn("Abandoned</span>", row)
        self.assertGreaterEqual(int(stats(at)["Abandoned"]), 1)
        with self.assertRaises(KeyError):
            at.button(key=f"progress_{key}")

    def test_status_filter_includes_abandoned(self):
        at = self.dashboard()
        at.selectbox(key="dash_status").select("Abandoned").run()
        self.assertFalse(at.exception)
        self.assertTrue(at.button(key=f"review_{self.ids['abandoned_partial']}"))
        with self.assertRaises(KeyError):
            at.button(key=f"review_{self.ids['with_4d']}")

    def test_partial_review_shows_saved_data_labels_and_offers_no_decision(self):
        at = self.click(self.dashboard(), f"review_{self.ids['abandoned_partial']}")
        self.assertEqual(at.session_state["page"], "interviewer_review")
        t = plain_texts(at)
        for expected in ("Partial Interview", "Abandoned", "Chen Wei", "Backend Engineer", "Question Bank",
                         "Abandoned-interview answer one.", "Abandoned-interview answer two, no evaluation.",
                         "Not Evaluated", "No answer was submitted.", "no full-interview score or hiring recommendation",
                         "Suspected prompt injection", "Answer to Question 1"):
            self.assertIn(expected, t)
        for absent in ("Accept AI Recommendation", "Override", "Final Decision", "Overall Score"):
            self.assertNotIn(absent, t)
        self.assertEqual([b.label for b in at.button if "ccept" in b.label or "verride" in b.label], [])
        self.assertEqual(sorted(d.proto.label for d in [e for e in walk(at.main) if e.type == "download_button"]),
                         ["Download Markdown", "Download PDF"])

    def test_a_completed_review_is_unchanged_and_shows_security_status(self):
        t = plain_texts(self.review_view(self.ids["with_4d"]))
        self.assertNotIn("Partial Interview", t)
        self.assertIn("Overall AI Evaluation", t)
        self.assertIn("Prompt-Injection Check", t)


class NoAudioTests(UiTestCase):
    AUDIO_TYPES = {"audio", "video", "webrtc", "camera_input", "audio_input"}

    def assert_no_audio_controls(self, at, scan_text=True):
        self.assertFalse(at.get("audio"))
        self.assertFalse(at.get("audio_input"))
        for b in at.button:
            self.assertFalse(any(w in b.label.lower() for w in AUDIO_CONTROL_WORDS), b.label)
        for r in at.radio:
            self.assertFalse(any(w in str(o).lower() for o in r.options for w in AUDIO_CONTROL_WORDS))
            self.assertNotIn("Voice", list(r.options))
        types = {e.type for e in walk(at.main)}
        self.assertFalse(types & self.AUDIO_TYPES, types & self.AUDIO_TYPES)
        if scan_text:
            t = plain_texts(at).lower()
            for word in AUDIO_CONTROL_WORDS:
                self.assertNotIn(word, t, word)

    def test_no_audio_controls_on_any_page(self):
        self.assert_no_audio_controls(self.app())
        self.assert_no_audio_controls(self.candidate_view())
        self.assert_no_audio_controls(self.app("results"))
        self.assert_no_audio_controls(self.app("interviewer_login"))
        self.assert_no_audio_controls(self.app("interviewer_schedule", authenticated=True))
        self.assert_no_audio_controls(self.review_view(self.ids["with_4d"]))
        # the dashboard's usage table can list historical Text-to-Speech rows as data; only controls are checked there
        self.assert_no_audio_controls(self.dashboard(), scan_text=False)

        started = self.candidate_view("Priya Patel")
        self.click(started, f"start_{self.ids['priya_scheduled']}")
        self.assertEqual(started.session_state["page"], "interview")
        self.assert_no_audio_controls(started)

    def test_frontend_source_has_no_audio_ui_code(self):
        source = open(os.path.join(FRONTEND_DIR, "app.py"), encoding="utf-8").read().lower()
        for needle in ("streamlit_webrtc", "webrtc_streamer", "st.audio", "audio_utils", "speak_question",
                       "transcribe_audio", "speech-to-text", "answer_method", "microphone"):
            self.assertNotIn(needle, source, needle)


if __name__ == "__main__":
    unittest.main()
