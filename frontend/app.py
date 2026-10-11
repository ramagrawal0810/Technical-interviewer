"""Streamlit frontend for Technical Interviewer."""

import os
import re
from decimal import Decimal, ROUND_HALF_UP

import requests
import streamlit as st
from dotenv import load_dotenv

from ui_theme import (
    badge, dimension_card, esc, eyebrow, fields_grid, inject_theme, prose,
    recommendation_badge, render, section_title, stat_card, status_badge,
)

load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Technical Interviewer",
    page_icon="🧠",
    layout="wide",
)

# ===== API Helper Functions =====

def create_interview(candidate_id: int, job_description_id: int, mode: str = "question_bank", cv_text: str = None) -> dict:
    """Create a new interview with mode and optional CV text."""
    data = {
        "candidate_id": candidate_id,
        "job_description_id": job_description_id,
        "mode": mode,
    }
    if cv_text:
        data["cv_text"] = cv_text

    response = requests.post(
        f"{BACKEND_URL}/api/interviews",
        json=data,
    )
    response.raise_for_status()
    return response.json()


def start_interview(interview_id: int) -> dict:
    """Start an interview and get assigned questions."""
    response = requests.post(f"{BACKEND_URL}/api/interviews/{interview_id}/start")
    response.raise_for_status()
    return response.json()


def get_interview(interview_id: int) -> dict:
    """Get interview details."""
    response = requests.get(f"{BACKEND_URL}/api/interviews/{interview_id}")
    response.raise_for_status()
    return response.json()


def submit_answer(interview_id: int, interview_question_id: int, answer_text: str) -> dict:
    """Submit an answer to an interview question."""
    response = requests.post(
        f"{BACKEND_URL}/api/interviews/{interview_id}/questions/{interview_question_id}/answer",
        json={"answer_text": answer_text},
    )
    response.raise_for_status()
    return response.json()


def evaluate_answer(answer_id: int) -> dict:
    """Evaluate an answer using AI."""
    response = requests.post(f"{BACKEND_URL}/api/answers/{answer_id}/evaluate")
    response.raise_for_status()
    return response.json()


def get_question(question_id: int) -> dict:
    """Fetch full question details by ID."""
    response = requests.get(f"{BACKEND_URL}/api/questions/{question_id}")
    response.raise_for_status()
    return response.json()


def complete_interview(interview_id: int) -> dict:
    """Complete an interview and get the final result."""
    response = requests.post(f"{BACKEND_URL}/api/interviews/{interview_id}/complete")
    response.raise_for_status()
    return response.json()


def upload_cv(interview_id: int, pdf_file, save_to_candidate: bool = False) -> dict:
    """Upload a CV for this interview (and, only when asked, save it as the candidate's CV)."""
    files = {"file": ("cv.pdf", pdf_file, "application/pdf")}
    response = requests.post(
        f"{BACKEND_URL}/api/interviews/{interview_id}/upload-cv",
        files=files,
        params={"save_to_candidate": "true" if save_to_candidate else "false"},
    )
    response.raise_for_status()
    return response.json()


def get_next_question(interview_id: int) -> dict:
    """Phase 5: Fetch next adaptive question."""
    response = requests.post(f"{BACKEND_URL}/api/interviews/{interview_id}/next-question")
    response.raise_for_status()
    return response.json()


# ===== NEW: API Helpers for Interviewer Panel =====

def get_candidates() -> list:
    """Fetch all candidates."""
    response = requests.get(f"{BACKEND_URL}/api/candidates")
    response.raise_for_status()
    return response.json()


def get_jobs() -> list:
    """Fetch all job descriptions."""
    response = requests.get(f"{BACKEND_URL}/api/jobs")
    response.raise_for_status()
    return response.json()


def get_interviews(candidate_id: int = None, status: str = None) -> list:
    """Fetch interviews, optionally filtered by candidate and/or status."""
    params = {}
    if candidate_id is not None:
        params["candidate_id"] = candidate_id
    if status is not None:
        params["status"] = status
    response = requests.get(f"{BACKEND_URL}/api/interviews", params=params)
    response.raise_for_status()
    return response.json()


def get_review_data(interview_id: int) -> dict:
    """Get interview review data (transcript, evaluation, recommendation)."""
    response = requests.get(f"{BACKEND_URL}/api/interviews/{interview_id}/review-data")
    response.raise_for_status()
    return response.json()


def get_interview_session(interview_id: int) -> dict:
    """Persisted state of an interview (for resuming it)."""
    response = requests.get(f"{BACKEND_URL}/api/interviews/{interview_id}/session")
    response.raise_for_status()
    return response.json()


def get_interview_progress(interview_id: int) -> dict:
    """Read-only progress of an interview (questions, answers and evaluations saved so far)."""
    response = requests.get(f"{BACKEND_URL}/api/interviews/{interview_id}/progress")
    response.raise_for_status()
    return response.json()


def abandon_interview(interview_id: int) -> dict:
    """End an in-progress interview for good (the candidate confirmed they cannot resume it)."""
    response = requests.post(f"{BACKEND_URL}/api/interviews/{interview_id}/abandon", timeout=15)
    response.raise_for_status()
    return response.json()


def get_data_handling() -> dict:
    """The consent notice facts (storage, retention, sharing); the same source the documentation is checked against."""
    response = requests.get(f"{BACKEND_URL}/api/data-handling", timeout=10)
    response.raise_for_status()
    return response.json()


def record_consent(interview_id: int, granted: bool, notice_version: str) -> dict:
    """Store the candidate's consent decision for the notice version they were shown."""
    response = requests.post(
        f"{BACKEND_URL}/api/interviews/{interview_id}/consent",
        json={"granted": granted, "notice_version": notice_version},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_feedback_report(interview_id: int) -> dict:
    """The structured feedback report of a completed interview (built from stored data, no AI call)."""
    response = requests.get(f"{BACKEND_URL}/api/interviews/{interview_id}/report", timeout=30)
    response.raise_for_status()
    return response.json()


def get_report_file(interview_id: int, extension: str) -> bytes:
    """The report rendered as a PDF ('pdf') or Markdown ('md') download."""
    response = requests.get(f"{BACKEND_URL}/api/interviews/{interview_id}/report.{extension}", timeout=60)
    response.raise_for_status()
    return response.content


def accept_human_review(interview_id: int) -> dict:
    """Accept AI recommendation for an interview."""
    response = requests.post(f"{BACKEND_URL}/api/interviews/{interview_id}/human-review")
    response.raise_for_status()
    return response.json()


def override_human_review(interview_id: int, decision: str, override_reason: str) -> dict:
    """Override AI recommendation with human decision."""
    data = {
        "decision": decision,
        "override_reason": override_reason,
    }
    response = requests.post(
        f"{BACKEND_URL}/api/interviews/{interview_id}/human-review/override",
        json=data,
    )
    response.raise_for_status()
    return response.json()


def verify_interviewer_passcode(passcode: str):
    """Ask the backend to check the demo passcode. Returns (authorized, message); the UI never holds the expected value."""
    try:
        response = requests.post(f"{BACKEND_URL}/api/interviewer/verify-passcode", json={"passcode": passcode}, timeout=10)
    except requests.RequestException:
        return False, "Could not reach the server. Please try again."
    if response.status_code == 200:
        return True, ""
    if response.status_code == 503:
        return False, "Interviewer access is not configured."
    return False, "Invalid passcode."


def get_usage_dashboard(candidate_id: int = None, interview_id: int = None, operation: str = None,
                        status: str = None, limit: int = 200) -> dict:
    """LLM usage (summary + latest rows) from the existing LLMUsageLog, optionally filtered."""
    params = {"limit": limit}
    for key, value in (("candidate_id", candidate_id), ("interview_id", interview_id),
                       ("operation", operation), ("status", status)):
        if value is not None:
            params[key] = value
    response = requests.get(f"{BACKEND_URL}/api/usage", params=params)
    response.raise_for_status()
    return response.json()


def get_interview_usage(interview_id: int) -> dict:
    """Aggregated LLM usage for one interview."""
    response = requests.get(f"{BACKEND_URL}/api/interviews/{interview_id}/usage")
    response.raise_for_status()
    return response.json()


# ===== Shared UI helpers =====

CV_MODES = ("cv_driven", "hybrid")
DECISION_OPTIONS = ["Strong Hire", "Hire", "Hold", "No Hire"]
STATUS_FILTER_OPTIONS = ["All Statuses", "Scheduled", "In Progress", "Completed", "Reviewed", "Abandoned"]
DASHBOARD_PAGE_SIZE = 30

EVALUATION_DIMENSIONS = [
    ("Technical Skills", "technical_skills"),
    ("Domain Understanding", "domain_understanding"),
    ("Problem-Solving Approach", "problem_solving"),
    ("Communication Clarity", "communication_clarity"),
]

NARROW_PAGES = {"interview", "complete", "results", "interviewer_login", "interviewer_schedule"}


def _mode_label(mode) -> str:
    return {"question_bank": "Question Bank", "cv_driven": "CV Driven", "hybrid": "Hybrid"}.get(mode, mode or "N/A")


def _fmt_ts(value, suffix: str = "") -> str:
    """Format an ISO timestamp from the API; tolerate null."""
    if not value:
        return "N/A"
    return str(value)[:19].replace("T", " ") + suffix


def _api_error_detail(err: requests.RequestException) -> str:
    """Extract FastAPI's `detail` message from a failed request, falling back to the exception text."""
    response = getattr(err, "response", None)
    if response is not None:
        try:
            body = response.json()
            if isinstance(body, dict) and body.get("detail"):
                return str(body["detail"])
        except ValueError:
            pass
    return str(err)


def _fmt_int(value) -> str:
    return f"{value:,}" if value is not None else "—"


def _fmt_cost(value) -> str:
    """Dollar amount rounded half-up (float formatting would turn 0.00045 into $0.0004)."""
    if value is None:
        return "—"
    amount = Decimal(str(value))
    if amount == 0:
        return "$0.00"
    places = Decimal("0.0001") if amount < Decimal("0.01") else Decimal("0.01")
    rounded = amount.quantize(places, rounding=ROUND_HALF_UP)
    return f"${rounded}" if rounded > 0 else "<$0.0001"


def _fmt_latency(ms) -> str:
    return f"{ms / 1000:.1f}s" if ms is not None else "—"


def _fmt_rate(rate) -> str:
    return f"{rate * 100:.0f}%" if rate is not None else "—"


def _go(page: str) -> None:
    st.session_state.page = page
    st.rerun()


def _back_button(label: str, page: str, key: str) -> None:
    with st.container(key=f"back-{key}"):
        if st.button(label, key=f"btn-{key}"):
            _go(page)


def _show_flash(state_key: str) -> None:
    flash = st.session_state.pop(state_key, None)
    if flash:
        getattr(st, flash[0])(flash[1])


# ===== Page: Landing =====

def show_landing():
    render(
        '<div class="ti-hero">'
        + eyebrow("Agentic AI Interview Engine", accent=True)
        + '<div class="ti-hero-title">TECHNICAL INTERVIEWER</div>'
        + '<div class="ti-hero-sub">AI-Powered Technical Interview &amp; Evaluation Platform</div>'
        + '<div class="ti-hero-copy">Conduct structured technical interviews with adaptive AI questioning, '
        "evidence-based evaluation, and human review.</div></div>"
    )

    col1, col2 = st.columns(2, gap="large")

    with col1:
        with st.container(key="card-candidate"):
            render(
                eyebrow("Candidate")
                + '<div class="ti-card-title">Candidate</div>'
                + '<ul class="ti-checklist"><li>View scheduled interview</li><li>Complete technical interview</li>'
                "<li>Submit answers</li><li>Receive interview submission confirmation</li></ul>"
            )
            if st.button("Continue as Candidate", type="primary", use_container_width=True, key="landing_candidate"):
                _go("candidate")

    with col2:
        with st.container(key="card-interviewer"):
            render(
                eyebrow("Interviewer")
                + '<div class="ti-card-title">Interviewer</div>'
                + '<ul class="ti-checklist"><li>Schedule interviews</li><li>Monitor interview status</li>'
                "<li>Review AI evaluation</li><li>Make final human decisions</li><li>View AI usage and cost</li></ul>"
            )
            if st.button("Interviewer Access", type="primary", use_container_width=True, key="landing_interviewer"):
                _go("interviewer_dashboard" if st.session_state.get("interviewer_authenticated") else "interviewer_login")


# ===== Page: Candidate Dashboard =====

def _candidate_status_label(display_status) -> str:
    """Candidates only see Scheduled / In Progress / Completed; review state stays interviewer-side."""
    if display_status == "Reviewed":
        return "Completed"
    return display_status or "N/A"


def _reset_interview_session_state() -> None:
    """Drop per-interview widget state so a new interview never inherits a previous one's answers."""
    for key in list(st.session_state.keys()):
        if re.fullmatch(r"(answer|submitting)_\d+", key):
            del st.session_state[key]
    st.session_state.pop("confirm_exit", None)


def _start_candidate_interview(interview: dict, cv_file=None) -> bool:
    """Upload the candidate's CV if one is needed, start the interview, and switch to the interview page."""
    interview_id = interview["id"]
    try:
        if cv_file is not None:
            with st.spinner("Uploading your resume..."):
                upload_cv(interview_id, cv_file)
        with st.spinner("Starting interview..."):
            started = start_interview(interview_id)
    except requests.RequestException as err:
        st.error(f"Could not start the interview: {_api_error_detail(err)}")
        return False

    _reset_interview_session_state()
    st.session_state.interview_id = interview_id
    st.session_state.candidate_id = interview["candidate_id"]
    st.session_state.selected_candidate_name = interview.get("candidate_name")
    st.session_state.job_description_id = interview["job_description_id"]
    st.session_state.selected_job_title = interview.get("job_title")
    st.session_state.interview_mode = interview["mode"]
    # Adaptive flow: only the first question is assigned up front
    st.session_state.current_question = started.get("current_question")
    st.session_state.current_question_index = started.get("current_index", 0)
    st.session_state.total_questions_in_interview = started.get("total_questions", 5)
    st.session_state.main_question_number = started.get("main_question_number", 1)
    st.session_state.follow_up_number = started.get("follow_up_number", 0)
    st.session_state.max_follow_ups = started.get("max_follow_ups", 3)
    st.session_state.submitted_answers = {}
    st.session_state.page = "interview"
    _remember_interview(interview_id)
    return True


def _remember_interview(interview_id: int) -> None:
    """Keep the interview id in the URL: a browser refresh wipes session_state, this brings the candidate back."""
    st.query_params["resume"] = str(interview_id)


def _forget_interview() -> None:
    if "resume" in st.query_params:
        del st.query_params["resume"]


def _resume_interview(interview_id: int, quiet: bool = False) -> bool:
    """
    Reopen an in-progress interview exactly where it stopped, from what is saved in the database.

    Recovers a step that was interrupted half-way: an answer saved but not evaluated is evaluated, and an
    evaluated answer whose next question was never fetched gets it now. Both are safe to repeat on the
    backend, so nothing is duplicated. Returns False (without an error when `quiet`) if there is nothing to resume.
    """
    try:
        with st.spinner("Restoring your interview..."):
            session = get_interview_session(interview_id)
            if session["status"] != "in_progress":
                return False
            if session.get("consent_status") != "granted":
                if not quiet:
                    st.warning("Please read and accept the data-handling notice before continuing this interview.")
                return False
            action = session["next_action"]
            if action == "evaluate":
                try:
                    evaluate_answer(session["last_answer_id"])
                except requests.RequestException as err:
                    if "already been evaluated" not in _api_error_detail(err):
                        raise
                action = "next_question"
            if action == "next_question":
                if get_next_question(interview_id).get("is_complete"):
                    _reset_interview_session_state()
                    st.session_state.interview_id = interview_id
                    st.session_state.page = "complete"
                    return True
                session = get_interview_session(interview_id)
    except requests.RequestException as err:
        if not quiet:
            st.error(f"Could not restore the interview: {_api_error_detail(err)}")
        return False

    pending = session.get("pending")
    if not pending:
        return False

    _reset_interview_session_state()
    st.session_state.interview_id = interview_id
    st.session_state.candidate_id = session["candidate_id"]
    st.session_state.selected_candidate_name = session.get("candidate_name")
    st.session_state.job_description_id = session["job_description_id"]
    st.session_state.selected_job_title = session.get("job_title")
    st.session_state.interview_mode = session["mode"]
    st.session_state.current_question = {
        "id": pending["interview_question_id"], "question_id": pending["question_id"], "question_order": pending["order"],
    }
    st.session_state.current_question_index = pending["order"] - 1
    st.session_state.total_questions_in_interview = session["total_questions"]
    st.session_state.main_question_number = pending["main_question_number"]
    st.session_state.follow_up_number = pending["follow_up_number"]
    st.session_state.max_follow_ups = session["max_follow_ups"]
    st.session_state.submitted_answers = {step["order"] - 1: step["answer"] for step in session["steps"] if step.get("answer")}
    st.session_state.question_details_cache = {pending["question_id"]: {"question": pending["question"]}}
    st.session_state.page = "interview"
    _remember_interview(interview_id)
    return True


def _restore_interview_after_refresh() -> None:
    """A fresh session with an interview id in the URL means the page was refreshed mid-interview."""
    raw = st.query_params.get("resume")
    if not raw:
        return
    if not str(raw).isdigit() or not _resume_interview(int(raw), quiet=True):
        _forget_interview()


def _consent_notice_points(policy: dict) -> list:
    """The plain-language notice as (heading, [paragraphs or bullet items]), built only from what the backend says is true."""
    return [
        ("AI Evaluation", False, ["Your answers are evaluated by an AI model. Its score and hiring recommendation are advice only; "
                          "a human interviewer makes the final decision."]),
        ("What Information Is Stored", True, [item.strip().rstrip(".") for item in (policy.get("stored") or [])]),
        ("Who Can See It", False, ["Interviewers and administrators of this system can see your answers, the AI evaluation, "
                                   "the recommendation and the interviewer's decision. You do not see the internal evaluation."]),
        ("Where Data Is Stored", False, [policy.get("storage", "")]),
        ("Sharing With the AI Provider", False, [policy.get("shared_with_llm_provider", "")]),
        ("Data Retention", False, [policy.get("retention_enforcement", "")]),
        ("Data or Deletion Requests", False, [policy.get("deletion_requests", "")]),
    ]


def _consent_notice_html(policy: dict) -> str:
    sections = []
    for heading, as_list, items in _consent_notice_points(policy):
        body = ("<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>") if as_list else "".join(f"<p>{esc(i)}</p>" for i in items)
        sections.append(f'<div class="ti-consent-section"><h4>{esc(heading)}</h4>{body}</div>')
    return (
        '<div class="ti-consent"><div class="ti-consent-title">Before You Begin</div>'
        '<div class="ti-consent-intro">Please read how your interview data is handled. You choose whether to continue.</div>'
        + "".join(sections)
        + '<div class="ti-consent-decline"><b>If you decline,</b> the interview will not start. It stays Scheduled and nothing is '
          'evaluated; you can read this notice again and agree later.</div></div>'
    )


def _render_consent_notice(interview: dict):
    """
    The consent notice for an interview. Returns (policy, agreed): `agreed` is the unchecked-by-default checkbox.
    Returns (None, False) if the notice cannot be loaded, because nobody can consent to a notice they cannot read.
    """
    interview_id = interview["id"]
    try:
        policy = get_data_handling()
    except requests.RequestException as err:
        st.error(f"The data-handling notice could not be loaded, so the interview cannot start: {_api_error_detail(err)}")
        return None, False

    if interview.get("consent_status") == "declined":
        st.warning("You declined consent, so this interview has not started. You can change your mind below.")
    with st.container(key=f"consent-notice-{interview_id}"):
        render(_consent_notice_html(policy))
        agreed = st.checkbox(
            "I have read this notice and agree to the AI evaluation and the storage of my interview data as described.",
            value=False, key=f"consent_check_{interview_id}",
        )
    return policy, agreed


def _grant_consent(interview_id: int, policy: dict) -> bool:
    try:
        record_consent(interview_id, True, policy["notice_version"])
    except requests.RequestException as err:
        st.error(f"Could not record your consent: {_api_error_detail(err)}")
        return False
    return True


def _decline_consent(interview_id: int, policy: dict) -> None:
    try:
        record_consent(interview_id, False, policy["notice_version"])
    except requests.RequestException as err:
        st.error(f"Could not record your decision: {_api_error_detail(err)}")
        return
    st.session_state.pop(f"consent_check_{interview_id}", None)   # consent is never pre-ticked, even after a decline
    st.rerun()


def _render_candidate_interview_card(interview: dict) -> None:
    interview_id = interview["id"]
    status = interview.get("status")

    with st.container(key=f"card-interview-{interview_id}"):
        render(fields_grid([
            ("Candidate", esc(interview.get("candidate_name") or "N/A")),
            ("Job Role", esc(interview.get("job_title") or "Unknown role")),
            ("Interview Mode", esc(_mode_label(interview.get("mode")))),
            ("Status", status_badge(_candidate_status_label(interview.get("display_status")))),
        ]))

        if status == "not_started":
            needs_cv = interview.get("mode") in CV_MODES
            has_cv = bool((interview.get("cv_text") or "").strip()) or bool(interview.get("cv_available"))
            cv_file = None

            if needs_cv and not has_cv:
                notice = st.empty()
                cv_file = st.file_uploader("Upload your resume (PDF)", type="pdf", key=f"cv_upload_{interview_id}")
                if cv_file is None:
                    notice.warning("Resume required before starting this interview.")
                else:
                    notice.success("Resume attached. You can start the interview.")
            elif needs_cv:
                st.caption("Your resume has already been provided.")

            can_start = (not needs_cv) or has_cv or cv_file is not None
            consented = interview.get("consent_status") == "granted"
            policy, agreed = (None, True) if consented else _render_consent_notice(interview)
            if not consented and policy is None:
                return
            start_col, decline_col = st.columns([3, 1])
            with start_col:
                start_clicked = st.button(
                    "Start Interview" if consented else "Agree and Start Interview",
                    type="primary",
                    use_container_width=True,
                    disabled=not (can_start and agreed),
                    key=f"start_{interview_id}",
                )
            if not consented:
                with decline_col:
                    if st.button("Decline", use_container_width=True, key=f"decline_{interview_id}"):
                        _decline_consent(interview_id, policy)
            if start_clicked:
                if (consented or _grant_consent(interview_id, policy)) and _start_candidate_interview(interview, cv_file):
                    st.rerun()
        elif status == "in_progress":
            consented = interview.get("consent_status") == "granted"
            policy, agreed = (None, True) if consented else _render_consent_notice(interview)
            if not consented and policy is None:
                return
            if consented:
                st.caption("Your progress is saved. Pick up exactly where you left off.")
            else:
                st.caption("This interview was started before consent was recorded. Your progress is saved and has not been changed.")
            resume_col, decline_col = st.columns([3, 1])
            with resume_col:
                resume_clicked = st.button(
                    "Resume Interview" if consented else "Agree and Resume Interview",
                    type="primary", use_container_width=True, disabled=not agreed, key=f"resume_{interview_id}",
                )
            if not consented:
                with decline_col:
                    if st.button("Decline", use_container_width=True, key=f"decline_{interview_id}"):
                        _decline_consent(interview_id, policy)
            if resume_clicked:
                if (consented or _grant_consent(interview_id, policy)) and _resume_interview(interview_id):
                    st.rerun()
        elif status == "abandoned":
            st.info("You ended this interview, so it cannot be resumed. Your saved answers were kept for the interviewer.")
        else:
            st.success("Interview submitted. Thank you!")


def show_candidate():
    _back_button("← Back", "landing", "candidate")
    render(
        eyebrow("Candidate", accent=True)
        + '<div class="ti-page-title">My Interviews</div>'
        + '<div class="ti-page-sub">Select your name to see the interviews assigned to you.</div>'
    )

    try:
        candidates = get_candidates()
    except requests.RequestException as e:
        st.error("Failed to load candidates from backend.")
        st.caption(f"Error: {_api_error_detail(e)}")
        return

    if not candidates:
        st.info("No candidates found.")
        return

    candidate_map = {c["name"]: c["id"] for c in candidates}
    candidate_name = st.selectbox("Select your name", options=list(candidate_map.keys()), key="candidate_selector")
    st.caption("Demo mode: there is no sign-in. Choose your name to see the interviews assigned to you.")

    try:
        interviews = get_interviews(candidate_id=candidate_map[candidate_name])
    except requests.RequestException as e:
        st.error("Failed to load your interviews.")
        st.caption(f"Error: {_api_error_detail(e)}")
        return

    render(section_title("My Interviews"))
    if not interviews:
        st.info("You have no interviews assigned yet.")
        return

    for interview in sorted(interviews, key=lambda iv: iv["id"], reverse=True):
        _render_candidate_interview_card(interview)


# ===== Page: Interview (text only) =====

def _process_answer_submission(interview_id: int, current_question: dict, current_idx: int,
                               answer_text: str, flag_key: str) -> None:
    """Save -> evaluate -> next question. Saving is idempotent, so a retry after a later failure is safe."""
    st.session_state.setdefault("question_details_cache", {})

    try:
        with st.spinner("Saving your answer..."):
            answer_id = submit_answer(interview_id, current_question["id"], answer_text)["id"]
        st.session_state.submitted_answers[current_idx] = answer_text
    except requests.RequestException as err:
        st.session_state[flag_key] = False
        st.error(f"We couldn't save your answer. {_api_error_detail(err)}")
        st.stop()

    try:
        with st.spinner("Evaluating your answer..."):
            evaluate_answer(answer_id)
    except requests.RequestException as err:
        # On a retry after a later step failed, the evaluation already exists; that is not an error
        if "already been evaluated" not in _api_error_detail(err):
            st.session_state[flag_key] = False
            st.warning(f"Your answer was saved, but it could not be evaluated. {_api_error_detail(err)}")
            st.caption("Select Submit Answer again to retry; your answer will not be duplicated.")
            st.stop()

    try:
        with st.spinner("Preparing your next question..."):
            next_q = get_next_question(interview_id)
    except requests.RequestException as err:
        st.session_state[flag_key] = False
        st.warning(f"Your answer was saved and evaluated, but the next question could not be loaded. {_api_error_detail(err)}")
        st.caption("Select Submit Answer again to continue.")
        st.stop()

    st.session_state[flag_key] = False

    if next_q.get("is_complete"):
        st.session_state.page = "complete"
        st.rerun()

    st.session_state.current_question = {
        "id": next_q["id"],
        "question_id": next_q["question_id"],
        "question_order": next_q["current_index"] + 1,
    }
    st.session_state.current_question_index = next_q["current_index"]
    st.session_state.main_question_number = next_q.get("main_question_number", 1)
    st.session_state.follow_up_number = next_q.get("follow_up_number", 0)
    st.session_state.max_follow_ups = next_q.get("max_follow_ups", 3)
    st.session_state.question_details_cache.setdefault(next_q["question_id"], {"question": next_q.get("question", "")})
    st.rerun()


def _exit_interview() -> None:
    """Confirmed exit: the interview is ended for good (Abandoned). Nothing is cleared locally unless the server accepted it."""
    interview_id = st.session_state.get("interview_id")
    try:
        if interview_id:
            abandon_interview(interview_id)
    except requests.RequestException as err:
        st.error(f"Could not end the interview: {_api_error_detail(err)}")
        return
    _reset_interview_session_state()
    _forget_interview()
    st.session_state.page = "candidate"
    st.rerun()


def show_interview():
    """Text-only interview screen."""
    interview_id = st.session_state.get("interview_id")
    current_question = st.session_state.get("current_question")
    current_idx = st.session_state.get("current_question_index", 0)
    total_questions = st.session_state.get("total_questions_in_interview", 5)

    if not interview_id or not current_question:
        st.error("No question assigned to this interview.")
        _back_button("← Back to Dashboard", "candidate", "no-question")
        return

    st.session_state.setdefault("question_details_cache", {})
    question_id = current_question.get("question_id")

    head_left, head_right = st.columns([3, 1.4], vertical_alignment="center")
    with head_left:
        render(eyebrow("Technical Interviewer", accent=True))
    with head_right:
        with st.container(key="back-exit"):
            if st.button("← Exit Interview", key="btn-exit", use_container_width=True):
                st.session_state.confirm_exit = True

    if st.session_state.get("confirm_exit"):
        st.warning("Are you sure you want to end this interview? You will not be able to resume it.")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("Yes, end interview", key="confirm_exit_yes", use_container_width=True):
                _exit_interview()
        with col_no:
            if st.button("Stay in interview", key="confirm_exit_no", type="primary", use_container_width=True):
                st.session_state.confirm_exit = False
                st.rerun()

    # Progress counts MAIN questions; follow-up probing is shown separately and never inflates the total
    main_number = st.session_state.get("main_question_number", current_idx + 1)
    follow_up_number = st.session_state.get("follow_up_number", 0)
    max_follow_ups = st.session_state.get("max_follow_ups", 3)
    progress_label = f"Question {main_number} of {total_questions}"
    if follow_up_number:
        progress_label += f" · Follow-up {follow_up_number} of {max_follow_ups}"

    render(fields_grid([
        ("Candidate", esc(st.session_state.get("selected_candidate_name") or "N/A")),
        ("Job Role", esc(st.session_state.get("selected_job_title") or "N/A")),
        ("Progress", esc(progress_label)),
    ]))
    st.progress(min(max(main_number - 1, 0) / total_questions, 1.0) if total_questions else 0.0)

    question_text = None
    if question_id:
        if question_id not in st.session_state.question_details_cache:
            try:
                with st.spinner(f"Loading question {current_idx + 1}..."):
                    st.session_state.question_details_cache[question_id] = get_question(question_id)
            except requests.RequestException as e:
                st.error("Failed to load the question.")
                st.caption(f"Error: {_api_error_detail(e)}")
                return
        question_text = (st.session_state.question_details_cache.get(question_id) or {}).get("question")

    if not question_text:
        st.error("Could not retrieve the question text.")
        return

    render(
        '<div class="ti-question">' + eyebrow("Follow-up Question" if follow_up_number else "Interview Question")
        + f'<div class="ti-question-text">{esc(question_text)}</div></div>'
    )

    render(eyebrow("Your Answer"))
    answer_text = st.text_area(
        "Your answer",
        height=260,
        key=f"answer_{current_idx}",
        placeholder="Type your answer here...",
        label_visibility="collapsed",
    )

    flag_key = f"submitting_{current_idx}"
    is_submitting = st.session_state.get(flag_key, False)

    if st.button("Submit Answer", type="primary", use_container_width=True, disabled=is_submitting, key="submit_answer"):
        if not answer_text.strip():
            st.error("Please enter an answer before submitting.")
        else:
            st.session_state[flag_key] = True
            st.rerun()

    if is_submitting:
        _process_answer_submission(interview_id, current_question, current_idx, answer_text, flag_key)


def show_complete():
    """Finalise the interview automatically once the last answer is evaluated."""
    interview_id = st.session_state.get("interview_id")
    if not interview_id:
        _go("candidate")

    render('<div class="ti-center">' + eyebrow("Technical Interviewer", accent=True) + "</div>")
    try:
        with st.spinner("Finalizing your interview..."):
            # The response carries AI scores/recommendation, which candidates must not see
            complete_interview(interview_id)
    except requests.RequestException as err:
        detail = _api_error_detail(err)
        if "already been completed" not in detail:
            st.error(f"We couldn't finalize your interview. {detail}")
            if st.button("Try again", type="primary", key="retry_finalize"):
                st.rerun()
            return

    _forget_interview()
    st.session_state.page = "results"
    st.rerun()


def show_results():
    """Candidate-facing confirmation. AI scores, recommendation, cost and review decisions are interviewer-only."""
    render(
        '<div class="ti-center"><div class="ti-check">✓</div>'
        + eyebrow("Interview Submitted", accent=True)
        + '<div class="ti-page-title">INTERVIEW SUBMITTED</div>'
        + '<div class="ti-page-sub">Your interview has been successfully submitted.</div></div>'
    )
    if st.button("← Back to Candidate Dashboard", key="results_back", type="primary", use_container_width=True):
        _reset_interview_session_state()
        for key in ("interview_id", "current_question", "current_question_index", "total_questions_in_interview",
                    "main_question_number", "follow_up_number", "max_follow_ups",
                    "submitted_answers", "selected_candidate_name", "selected_job_title", "interview_mode"):
            st.session_state.pop(key, None)
        _go("candidate")


# ===== Page: Interviewer Access =====

def show_interviewer_login():
    _back_button("← Back", "landing", "login")

    with st.container(key="card-login"):
        render(
            eyebrow("Interviewer Access", accent=True)
            + '<div class="ti-card-title">INTERVIEWER ACCESS</div>'
            + '<div class="ti-card-copy">Demo access gate. Enter the interviewer passcode to open the dashboard.</div>'
        )
        with st.form("passcode_form", clear_on_submit=True):
            passcode = st.text_input("Passcode", type="password", key="passcode_input", placeholder="Passcode")
            submitted = st.form_submit_button("Enter Interviewer Panel", type="primary", use_container_width=True,
                                              key="passcode_submit")

        if submitted:
            authorized, message = verify_interviewer_passcode(passcode)
            if authorized:
                st.session_state.interviewer_authenticated = True
                _go("interviewer_dashboard")
            st.error(message)


def _logout() -> None:
    st.session_state.clear()
    st.session_state.page = "landing"
    st.rerun()


# ===== Page: Interviewer Dashboard =====

def _usage_metrics_from_interview_summary(usage: dict) -> dict:
    """Map GET /api/interviews/{id}/usage onto the shape used by the dashboard usage cards."""
    tokens = usage.get("tokens") or {}
    return {
        "total_llm_calls": usage.get("total_llm_calls", 0),
        "success_calls": usage.get("success_calls", 0),
        "success_rate": usage.get("success_rate"),
        "average_latency_ms": usage.get("average_latency_ms"),
        "prompt_tokens": tokens.get("prompt_tokens", 0),
        "completion_tokens": tokens.get("completion_tokens", 0),
        "total_tokens": tokens.get("total_tokens", 0),
        "estimated_cost_usd": usage.get("estimated_cost_usd", 0.0),
        "unpriced_calls": usage.get("unpriced_calls", 0),
    }


def _render_usage_cards(metrics: dict) -> None:
    calls = metrics.get("total_llm_calls", 0)
    unpriced = metrics.get("unpriced_calls", 0)
    cost_note = f"Excludes {unpriced} call(s) on unpriced models" if unpriced else "From actual token usage"

    cards = [
        ("Total LLM Calls", _fmt_int(calls), ""),
        ("Total Tokens", _fmt_int(metrics.get("total_tokens", 0)),
         f"{_fmt_int(metrics.get('prompt_tokens', 0))} in / {_fmt_int(metrics.get('completion_tokens', 0))} out"),
        ("Estimated Cost", _fmt_cost(metrics.get("estimated_cost_usd")), cost_note),
        ("Average Latency", _fmt_latency(metrics.get("average_latency_ms")), ""),
        ("Success Rate", _fmt_rate(metrics.get("success_rate")),
         f"{metrics.get('success_calls', 0)} of {calls} calls" if calls else "No calls yet"),
    ]
    for column, (label, value, sub) in zip(st.columns(5), cards):
        with column:
            render(stat_card(label, value, sub))


def _usage_table_rows(logs: list) -> list:
    return [
        {
            "Time (UTC)": _fmt_ts(row.get("created_at")),
            "Candidate": row.get("candidate_name") or "—",
            "Interview": row.get("job_title") or "—",
            "AI Operation": row.get("operation_label") or row.get("operation"),
            "Model": row.get("model"),
            "Input Tokens": _fmt_int(row.get("prompt_tokens")),
            "Output Tokens": _fmt_int(row.get("completion_tokens")),
            "Total Tokens": _fmt_int(row.get("total_tokens")),
            "Latency": _fmt_latency(row.get("latency_ms")),
            "Status": (row.get("status") or "").title(),
            "Estimated Cost": _fmt_cost(row.get("estimated_cost_usd")),
        }
        for row in logs
    ]


def _render_usage_details(candidates: list, interviews: list, available_operations: list) -> None:
    """Filters plus the per-call table. Everything shown comes from GET /api/usage."""
    candidate_map = {c["name"]: c["id"] for c in candidates}
    operation_map = {o["label"]: o["value"] for o in available_operations}

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        candidate_choice = st.selectbox("Candidate", ["All Candidates"] + list(candidate_map), key="usage_candidate")
    candidate_id = candidate_map.get(candidate_choice)

    interview_map = {}
    for iv in sorted(interviews, key=lambda i: i["id"], reverse=True):
        if candidate_id is not None and iv["candidate_id"] != candidate_id:
            continue
        label = (
            f"{iv.get('candidate_name') or 'Unknown'} — {iv.get('job_title') or 'Unknown role'} — "
            f"{_mode_label(iv.get('mode'))} — {_fmt_ts(iv.get('created_at'))[:16]}"
        )
        # An id is added only when two interviews would otherwise be indistinguishable
        interview_map[label if label not in interview_map else f"{label} (#{iv['id']})"] = iv["id"]

    with col2:
        interview_choice = st.selectbox("Interview", ["All Interviews"] + list(interview_map), key="usage_interview")
    with col3:
        operation_choice = st.selectbox("AI Operation", ["All Operations"] + list(operation_map), key="usage_operation")
    with col4:
        status_choice = st.selectbox("Status", ["All", "Success", "Error"], key="usage_status")

    try:
        data = get_usage_dashboard(
            candidate_id=candidate_id,
            interview_id=interview_map.get(interview_choice),
            operation=operation_map.get(operation_choice),
            status=None if status_choice == "All" else status_choice.lower(),
        )
    except requests.RequestException as e:
        st.warning(f"Usage details are unavailable: {_api_error_detail(e)}")
        return

    summary = data["summary"]
    cost_text = _fmt_cost(summary["estimated_cost_usd"]).replace("$", "\\$")  # keep markdown from reading $ as math
    st.caption(
        f"{summary['total_llm_calls']} LLM call(s) · {_fmt_int(summary['total_tokens'])} tokens · "
        f"{cost_text} estimated for the current filters"
    )

    if not data["logs"]:
        st.info("No usage recorded for these filters.")
        return

    st.dataframe(_usage_table_rows(data["logs"]), use_container_width=True, hide_index=True)
    if data["total_logs"] > len(data["logs"]):
        st.caption(f"Showing the latest {len(data['logs'])} of {data['total_logs']} calls.")


def _render_usage_section(candidates: list, interviews: list) -> None:
    render(section_title("AI Usage & Cost", "Token counts are recorded from the LLM API; cost is an estimate"))
    try:
        overall = get_usage_dashboard(limit=1)
    except requests.RequestException as e:
        st.warning(f"AI usage data is unavailable: {_api_error_detail(e)}")
        return

    _render_usage_cards(overall["summary"])
    with st.expander("LLM Usage Details", expanded=False):
        _render_usage_details(candidates, interviews, overall["available_operations"])


def _render_interview_rows(interviews: list) -> None:
    for iv in interviews[:DASHBOARD_PAGE_SIZE]:
        display_status = iv.get("display_status")
        with st.container(key=f"row-{iv['id']}"):
            info, status, action = st.columns([5, 2, 1.6], vertical_alignment="center")
            with info:
                render(
                    f'<div class="ti-field-value">{esc(iv.get("candidate_name") or "Unknown")}</div>'
                    f'<div class="ti-note">{esc(iv.get("job_title") or "Unknown role")} · {esc(_mode_label(iv.get("mode")))}'
                    f' · Scheduled {esc(_fmt_ts(iv.get("created_at"))[:16])}</div>'
                )
            with status:
                render(status_badge(display_status))
            with action:
                if display_status in ("Completed", "Reviewed", "Abandoned"):
                    if st.button("Review", key=f"review_{iv['id']}", use_container_width=True):
                        st.session_state.review_interview_id = iv["id"]
                        _go("interviewer_review")
                elif display_status == "In Progress":
                    if st.button("Review Progress", key=f"progress_{iv['id']}", use_container_width=True):
                        st.session_state.progress_interview_id = iv["id"]
                        _go("interviewer_progress")
    if len(interviews) > DASHBOARD_PAGE_SIZE:
        st.caption(f"Showing the latest {DASHBOARD_PAGE_SIZE} of {len(interviews)} interviews. Use the filters to narrow the list.")


def show_interviewer_dashboard():
    head, schedule_col, logout_col = st.columns([5, 2.2, 1.2], vertical_alignment="center")
    with head:
        render(eyebrow("Technical Interviewer", accent=True) + '<div class="ti-page-title">Interviewer Dashboard</div>')
    with schedule_col:
        if st.button("+ Schedule Interview", type="primary", use_container_width=True, key="dash_schedule"):
            _go("interviewer_schedule")
    with logout_col:
        if st.button("Logout", use_container_width=True, key="dash_logout"):
            _logout()

    _show_flash("dashboard_flash")

    try:
        interviews = get_interviews()
        candidates = get_candidates()
    except requests.RequestException as e:
        st.error("Failed to load interviews from the backend.")
        st.caption(f"Error: {_api_error_detail(e)}")
        return

    counts = {name: sum(1 for iv in interviews if iv.get("display_status") == name)
              for name in ("Scheduled", "In Progress", "Completed", "Reviewed", "Abandoned")}
    for column, (label, count) in zip(st.columns(5), counts.items()):
        with column:
            render(stat_card(label, count))

    _render_usage_section(candidates, interviews)

    render(section_title("Interviews", "Select a completed interview to review it"))
    col_candidate, col_status = st.columns(2)
    with col_candidate:
        candidate_choice = st.selectbox("Candidate", ["All Candidates"] + sorted({c["name"] for c in candidates}), key="dash_candidate")
    with col_status:
        status_choice = st.selectbox("Status", STATUS_FILTER_OPTIONS, key="dash_status")

    visible = [
        iv for iv in sorted(interviews, key=lambda i: i["id"], reverse=True)
        if (candidate_choice == "All Candidates" or iv.get("candidate_name") == candidate_choice)
        and (status_choice == "All Statuses" or iv.get("display_status") == status_choice)
    ]
    if not visible:
        st.info("No interviews match these filters.")
        return
    _render_interview_rows(visible)


# ===== Page: Schedule Interview =====

def _schedule_interview(candidate_id: int, candidate_name: str, job_id: int, mode: str, cv_file, save_to_candidate: bool = False) -> None:
    """Create the interview (status Scheduled), then attach the optional CV. Never starts the interview."""
    try:
        with st.spinner("Scheduling interview..."):
            interview = create_interview(candidate_id, job_id, mode=mode)
    except requests.RequestException as err:
        st.error(f"Failed to schedule interview: {_api_error_detail(err)}")
        return

    flash = ("success", "Interview scheduled successfully.")
    if cv_file is not None:
        try:
            with st.spinner("Uploading CV..."):
                upload_cv(interview["id"], cv_file, save_to_candidate=save_to_candidate)
        except requests.RequestException as err:
            flash = (
                "warning",
                f"Interview scheduled successfully, but the CV upload failed ({_api_error_detail(err)}). "
                "The candidate will be asked to upload a resume before starting.",
            )

    st.session_state.dashboard_flash = flash
    # New uploader key clears the CV field for the next scheduling
    st.session_state.schedule_form_n = st.session_state.get("schedule_form_n", 0) + 1
    _go("interviewer_dashboard")


def show_interviewer_schedule():
    _back_button("← Back to Interviewer Dashboard", "interviewer_dashboard", "schedule")
    render(eyebrow("Technical Interviewer", accent=True) + '<div class="ti-page-title">Schedule Interview</div>'
           '<div class="ti-page-sub">Assign an interview to a candidate. The candidate starts it from their own dashboard.</div>')

    try:
        candidates = get_candidates()
        jobs = get_jobs()
    except requests.RequestException as e:
        st.error("Failed to load candidates or jobs.")
        st.caption(f"Error: {_api_error_detail(e)}")
        return

    if not candidates or not jobs:
        st.warning("At least one candidate and one job role are needed to schedule an interview.")
        return

    candidate_map = {c["name"]: c["id"] for c in candidates}
    job_map = {j["title"]: j["id"] for j in jobs}

    with st.container(key="card-schedule"):
        candidate_name = st.selectbox("Candidate", options=list(candidate_map.keys()), key="schedule_candidate")
        job_title = st.selectbox("Job Role", options=list(job_map.keys()), key="schedule_job")
        mode_label = st.radio(
            "Interview Mode",
            options=["Question Bank", "CV Driven", "Hybrid"],
            horizontal=True,
            key="schedule_mode",
        )
        mode = {"Question Bank": "question_bank", "CV Driven": "cv_driven", "Hybrid": "hybrid"}[mode_label]

        cv_file, save_to_candidate, cv_ready = None, False, True
        if mode in CV_MODES:
            selected = next(c for c in candidates if c["id"] == candidate_map[candidate_name])
            upload_key = f"schedule_cv_{st.session_state.get('schedule_form_n', 0)}"
            if selected.get("has_cv"):
                updated = _fmt_ts(selected.get("cv_updated_at"))[:16]
                st.success(f"Saved CV on file for {candidate_name}: {selected.get('cv_file_name') or 'CV'} (saved {updated}). "
                           "It will be used for this interview; no upload is needed.")
                with st.expander("Use a different CV for this interview only"):
                    cv_file = st.file_uploader("Replacement CV (PDF)", type="pdf", key=upload_key)
                    if cv_file is not None:
                        save_to_candidate = st.checkbox(f"Also replace {candidate_name}'s saved CV with this file", value=False,
                                                        key="schedule_save_cv")
                        st.caption("Otherwise only this interview uses the replacement; the saved CV and other interviews are unchanged.")
            else:
                st.warning(f"{candidate_name} has no saved CV. Upload one to schedule a {mode_label} interview.")
                cv_file = st.file_uploader("CV / Resume (PDF) - required", type="pdf", key=upload_key)
                if cv_file is not None:
                    save_to_candidate = st.checkbox(f"Save as {candidate_name}'s CV for future interviews", value=True, key="schedule_save_cv")
                cv_ready = cv_file is not None

        if st.button("Schedule Interview", type="primary", use_container_width=True, key="schedule_submit", disabled=not cv_ready):
            _schedule_interview(candidate_map[candidate_name], candidate_name, job_map[job_title], mode, cv_file, save_to_candidate)


# ===== Page: Interview Review =====

def _render_evaluation(evaluation) -> None:
    """One question's four-dimensional AI evaluation (read-only). Never fabricates scores."""
    if not evaluation:
        st.info("4D evaluation unavailable for this question.")
        return

    for label, key in EVALUATION_DIMENSIONS:
        dim = evaluation.get(key)
        if not dim:
            render(f'<div class="ti-field-label">{esc(label)}</div><div class="ti-note">Not available.</div>')
            continue
        score = dim.get("score")
        width = max(0, min(100, (score or 0) / 5 * 100))
        quote = f'<div class="ti-quote">Evidence: “{esc(dim["quote"])}”</div>' if dim.get("quote") else ""
        render(
            f'<div class="ti-field-label">{esc(label)}</div>'
            f'<div class="ti-field-value">Score: {esc(score if score is not None else "N/A")} / 5</div>'
            f'<div class="ti-bar"><span style="width:{width:.0f}%"></span></div>'
            f'<div class="ti-note">{esc(dim.get("justification") or "N/A")}</div>{quote}'
        )


def _render_overall_evaluation(review_data: dict):
    """Interview-level 4D scores, overall score and the (advisory) AI recommendation. Returns the recommendation label."""
    render(section_title("Overall AI Evaluation"))

    overall = review_data.get("overall_evaluation")
    if overall:
        evaluated, total = overall.get("evaluated_questions", 0), overall.get("total_questions", 0)
        average_note = f"Average of {evaluated} question{'' if evaluated == 1 else 's'}"
        for column, (label, key) in zip(st.columns(4), EVALUATION_DIMENSIONS):
            with column:
                render(dimension_card(label, overall.get(key), sub=average_note))
        if evaluated < total:
            render(f'<div class="ti-note">Only {evaluated} of {total} questions have a 4D evaluation; the rest were evaluated before 4D.</div>')
    else:
        st.info("Overall 4D evaluation unavailable for this interview.")

    render(section_title("AI Recommendation — Not Final Decision", "Advisory only; the human decision below is final"))

    ai_assessment = review_data.get("ai_assessment") or {}
    ai_recommendation = ai_assessment.get("recommendation")

    if not ai_recommendation:
        st.info("AI recommendation unavailable.")
        return None

    col1, col2, col3 = st.columns(3)
    with col1:
        render(dimension_card("Overall Score", ai_assessment.get("overall_score"), scale=10, decimals=0, sub="0–10"))
    with col2:
        render(
            '<div class="ti-stat" style="min-height:150px"><div class="ti-stat-label">AI Recommendation</div>'
            f'<div style="margin-top:.7rem">{recommendation_badge(ai_recommendation, large=True)}</div>'
            '<div class="ti-stat-sub">Not final decision</div></div>'
        )
    with col3:
        render(stat_card("Confidence", ai_assessment.get("confidence") or "N/A", min_height=150))

    render(
        '<div class="ti-field-label" style="margin-top:1rem">AI Reasoning</div>'
        + prose(ai_assessment.get("reason") or "N/A")
    )
    return ai_recommendation


def _render_human_review(interview_id: int, ai_recommendation, human_review) -> None:
    """Human decision: the final decision, kept separate from (and never altering) the AI recommendation."""
    render(section_title("Human Review"))

    if human_review:
        decision_type = "AI Accepted" if human_review.get("decision_type") == "ACCEPT" else "Human Override"
        override_html = ""
        if human_review.get("decision_type") == "OVERRIDE":
            override_html = (
                '<div class="ti-field-label" style="margin-top:1rem">Override Reason</div>'
                + prose(human_review.get("override_reason") or "N/A")
                + f'<div class="ti-note">AI recommended: {esc(human_review.get("ai_recommendation") or "N/A")} (unchanged)</div>'
            )
        render(
            '<div class="ti-final">' + eyebrow("FINAL HUMAN DECISION")
            + f'<div class="ti-final-decision">{esc(human_review.get("decision") or "N/A")}</div>'
            + fields_grid([
                ("Decision Type", esc(decision_type)),
                ("Interviewer", esc(human_review.get("interviewer_name") or "N/A")),
                ("Reviewed At", esc(_fmt_ts(human_review.get("reviewed_at"), " UTC"))),
            ])
            + override_html + "</div>"
        )
        st.caption("This review is saved and read-only.")
        return

    if not ai_recommendation:
        st.warning("A decision cannot be recorded because the AI recommendation is unavailable for this interview.")
        return

    st.caption(f"Current AI recommendation: {ai_recommendation}")

    override_key = f"override_open_{interview_id}"
    col1, col2 = st.columns(2)

    with col1:
        if st.button("Accept AI Recommendation", type="primary", use_container_width=True, key=f"accept_{interview_id}"):
            try:
                with st.spinner("Saving decision..."):
                    accept_human_review(interview_id)
                st.rerun()
            except requests.RequestException as err:
                st.error(f"Failed to save decision: {_api_error_detail(err)}")

    with col2:
        if st.button("Override AI Recommendation", use_container_width=True, key=f"override_btn_{interview_id}"):
            st.session_state[override_key] = True

    if st.session_state.get(override_key):
        with st.container(key="card-override"):
            render(eyebrow("Override AI Recommendation"))
            override_decision = st.selectbox(
                "Final Decision",
                options=[d for d in DECISION_OPTIONS if d != ai_recommendation],
                key=f"override_decision_{interview_id}",
            )
            override_reason = st.text_area(
                "Reason for Override (minimum 20 characters)",
                height=120,
                key=f"override_reason_{interview_id}",
                placeholder="Explain why you are overriding the AI recommendation...",
            )
            reason = override_reason.strip()
            st.caption(f"{len(reason)} characters")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Submit Override", type="primary", use_container_width=True, key=f"override_submit_{interview_id}"):
                    if not reason:
                        st.error("Reason is required.")
                    elif len(reason) < 20:
                        st.error(f"Reason must be at least 20 characters (currently {len(reason)}).")
                    else:
                        try:
                            with st.spinner("Saving decision..."):
                                override_human_review(interview_id, override_decision, reason)
                            st.rerun()
                        except requests.RequestException as err:
                            st.error(f"Failed to save override: {_api_error_detail(err)}")
            with col2:
                if st.button("Cancel", use_container_width=True, key=f"override_cancel_{interview_id}"):
                    st.session_state[override_key] = False
                    st.rerun()


def _render_question_details(qa_data: list) -> None:
    """Question, answer and per-question 4D evaluation: the evidence behind the overall evaluation."""
    with st.expander("Question-wise Details", expanded=False):
        if not qa_data:
            st.info("No Q&A data available.")
            return

        # Streamlit does not allow expanders inside expanders, so questions are separated by dividers
        main_number = follow_up_number = 0
        for idx, qa in enumerate(qa_data, 1):
            if qa.get("is_follow_up"):
                follow_up_number += 1
                title = f"Question {main_number} · Follow-up {follow_up_number}"
            else:
                main_number, follow_up_number = main_number + 1, 0
                title = f"Question {main_number}"
            render(
                f'<div class="ti-section-title">{esc(title)}</div>'
                '<div class="ti-field-label" style="margin-top:.7rem">Question</div>' + prose(qa.get("question") or "N/A")
                + '<div class="ti-field-label" style="margin-top:.7rem">Candidate Answer</div>' + prose(qa.get("answer") or "N/A")
                + '<div class="ti-field-label" style="margin:.9rem 0 .4rem">AI Evaluation (4-Dimensional)</div>'
            )
            # "evaluation" is present-but-null when the AI evaluation never completed
            _render_evaluation(qa.get("evaluation"))
            if idx < len(qa_data):
                st.divider()


def _report_point_html(point: dict) -> str:
    quote = f'<div class="ti-note">&ldquo;{esc(point["quote"])}&rdquo;</div>' if point.get("quote") else ""
    source = f'<div class="ti-note">Source: {esc(point["source"])}</div>' if point.get("source") else ""
    return prose(point.get("text") or "") + source + quote


_SECURITY_BADGE = {"clean": ("Checked: no suspected injection", "good"), "suspected": ("Suspected prompt injection", "bad"),
                   "failed": ("Security check failed", "warn"), "unknown": ("Security status unknown", "warn")}


def _render_feedback_report(interview_id: int) -> None:
    """The persisted feedback report (strengths, gaps, next-round areas, security status) and its two download formats."""
    render(section_title("Feedback Report", "Built from the saved interview; no AI call is made to produce it"))
    try:
        report = get_feedback_report(interview_id)
    except requests.RequestException as e:
        st.warning(f"The feedback report is unavailable: {_api_error_detail(e)}")
        return

    with st.container(key="card-feedback-report"):
        render('<div class="ti-field-label">Overall Assessment</div>' + prose(report.get("overall_assessment") or "Unavailable: no AI assessment text was stored."))
        for title, key, note_key in (("Strengths", "strengths", "strengths_note"), ("Knowledge Gaps", "gaps", "gaps_note"),
                                     ("Suggested Areas for the Next Round", "next_round_areas", None)):
            render(f'<div class="ti-field-label" style="margin-top:1rem">{esc(title)}</div>')
            points = report.get(key) or []
            if points:
                render("".join(_report_point_html(p) for p in points))
            else:
                render(prose((report.get(note_key) if note_key else None) or "Unavailable: nothing was recorded for this section."))
        security = report.get("security") or {}
        label, kind = _SECURITY_BADGE.get(security.get("status"), _SECURITY_BADGE["unknown"])
        render('<div class="ti-field-label" style="margin-top:1rem">Prompt-Injection Check</div>'
               + badge(label, kind) + prose(security.get("summary") or "Unavailable."))
        for finding in security.get("findings") or []:
            render(f'<div class="ti-note">{esc(finding.get("source"))}: {esc(finding.get("explanation") or "")}</div>')

        pdf_col, md_col, _ = st.columns([1, 1, 2])
        for column, extension, label, mime in ((pdf_col, "pdf", "Download PDF", "application/pdf"),
                                               (md_col, "md", "Download Markdown", "text/markdown")):
            with column:
                try:
                    data = get_report_file(interview_id, extension)
                except requests.RequestException as e:
                    st.warning(f"{label} unavailable: {_api_error_detail(e)}")
                    continue
                st.download_button(label, data=data, file_name=f"interview-{interview_id}-report.{extension}", mime=mime,
                                   use_container_width=True, key=f"download_{extension}_{interview_id}")


def _render_partial_review(interview_id: int) -> None:
    """Review of an abandoned interview: saved data only, labelled partial, with no decision controls."""
    try:
        report = get_feedback_report(interview_id)
    except requests.RequestException as e:
        st.error("Failed to load the partial interview report.")
        st.caption(f"Error: {_api_error_detail(e)}")
        return

    render(eyebrow("Technical Interviewer", accent=True) + '<div class="ti-page-title">Interview Review</div>')
    with st.container(key="card-review-info"):
        render(fields_grid([
            ("Candidate Name", esc(report.get("candidate_name") or "N/A")),
            ("Job Role", esc(report.get("job_title") or "N/A")),
            ("Interview Mode", esc(_mode_label(report.get("mode")))),
            ("Interview Status", status_badge("Abandoned")),
        ]))
        render(f'<div class="ti-note">Abandoned: {esc(_fmt_ts(report.get("abandoned_at"), " UTC"))} · '
               f'{esc(report.get("answered_count"))} answered · {esc(report.get("main_questions_asked"))} main question(s) asked</div>')
    render(f'<div class="ti-partial-banner"><b>{esc(report.get("partial_label") or "Partial Interview")}</b>'
           'The candidate ended this interview before finishing it. Only saved data is shown; no full-interview score or hiring '
           'recommendation is produced from incomplete evidence, and there is no final decision to record.</div>')

    if report.get("ai_recommendation"):
        render(section_title("Preliminary AI Recommendation", "Not a full-interview result"))
        render(recommendation_badge(report["ai_recommendation"]) + prose(report.get("ai_recommendation_note") or ""))

    security = report.get("security") or {}
    label, kind = _SECURITY_BADGE.get(security.get("status"), _SECURITY_BADGE["unknown"])
    render(section_title("Prompt-Injection Check")
           + badge(label, kind) + prose(security.get("summary") or "Unavailable."))
    for finding in security.get("findings") or []:
        render(f'<div class="ti-note">{esc(finding.get("source"))}: {esc(finding.get("explanation") or "")}</div>')

    render(section_title("Questions, Answers and Evaluations", report.get("evaluation_coverage") or ""))
    transcript = report.get("transcript") or []
    if not transcript:
        st.info("No questions were asked before the interview ended.")
    for qa in transcript:
        state = qa.get("evaluation_status")
        state_badge = (badge(f"Evaluated {qa['score']}/10", "good") if state == "evaluated" and qa.get("score") is not None
                       else badge("Not Evaluated", "warn") if state == "not_evaluated"
                       else badge("No Answer", "bad"))
        with st.container(key=f"partial-qa-{qa['label']}".replace(" ", "-").replace(",", "")):
            render(f'<div class="ti-section"><span class="ti-section-title">{esc(qa["label"])}</span>{state_badge}</div>'
                   '<div class="ti-field-label" style="margin-top:.6rem">Question</div>' + prose(qa.get("question") or "N/A")
                   + '<div class="ti-field-label" style="margin-top:.6rem">Candidate Answer</div>'
                   + prose(qa.get("answer") or "No answer was submitted."))
            if qa.get("note"):
                render(f'<div class="ti-note">{esc(qa["note"])}</div>')
            for dim in qa.get("dimensions") or []:
                quote = f' &ldquo;{esc(dim["quote"])}&rdquo;' if dim.get("quote") else ""
                render(f'<div class="ti-note"><b>{esc(dim["label"])}: {esc(dim["score"])}/5</b> &mdash; {esc(dim["justification"])}{quote}</div>')
    pdf_col, md_col, _ = st.columns([1, 1, 2])
    for column, extension, text, mime in ((pdf_col, "pdf", "Download PDF", "application/pdf"), (md_col, "md", "Download Markdown", "text/markdown")):
        with column:
            try:
                st.download_button(text, data=get_report_file(interview_id, extension),
                                   file_name=f"interview-{interview_id}-partial-report.{extension}", mime=mime,
                                   use_container_width=True, key=f"download_{extension}_{interview_id}")
            except requests.RequestException as e:
                st.warning(f"{text} unavailable: {_api_error_detail(e)}")


def _render_interview_usage(interview_id: int) -> None:
    render(section_title("AI Usage for This Interview"))
    try:
        usage = get_interview_usage(interview_id)
    except requests.RequestException as e:
        st.warning(f"AI usage data is unavailable: {_api_error_detail(e)}")
        return
    _render_usage_cards(_usage_metrics_from_interview_summary(usage))


def show_interviewer_review():
    _back_button("← Back to Interviewer Dashboard", "interviewer_dashboard", "review")

    interview_id = st.session_state.get("review_interview_id")
    if not interview_id:
        _go("interviewer_dashboard")

    try:
        if get_interview(interview_id).get("status") == "abandoned":
            _render_partial_review(interview_id)
            return
    except requests.RequestException:
        pass   # fall through: the normal review reports the real problem

    try:
        review_data = get_review_data(interview_id)
    except requests.RequestException as e:
        st.error("Failed to load interview review data.")
        st.caption(f"Error: {_api_error_detail(e)}")
        return

    info = review_data.get("interview") or {}
    render(eyebrow("Technical Interviewer", accent=True) + '<div class="ti-page-title">Interview Review</div>')

    with st.container(key="card-review-info"):
        render(fields_grid([
            ("Candidate Name", esc(info.get("candidate_name") or "N/A")),
            ("Job Role", esc(info.get("job_title") or "N/A")),
            ("Interview Mode", esc(_mode_label(info.get("mode")))),
            ("Interview Status", status_badge(review_data.get("display_status"))),
        ]))
        render(f'<div class="ti-note">Email: {esc(info.get("candidate_email") or "N/A")} · Completed: {esc(_fmt_ts(info.get("completed_at")))}</div>')

    ai_recommendation = _render_overall_evaluation(review_data)
    _render_human_review(interview_id, ai_recommendation, review_data.get("human_review"))
    _render_feedback_report(interview_id)
    _render_question_details(review_data.get("questions_answers_evaluations") or [])
    _render_interview_usage(interview_id)


# ===== Page: Interview Progress (interviewer) =====

_PROGRESS_STATE_BADGES = {
    "evaluated": ("Evaluated", "good"),
    "submitted": ("Awaiting evaluation", "warn"),
    "pending": ("Pending answer", "scheduled"),
    "unanswered": ("Unanswered", ""),
}


def _render_progress_step(step: dict, title: str) -> None:
    """One asked question and whatever is saved about it: answer, evaluation, or an explicit empty state."""
    label, kind = _PROGRESS_STATE_BADGES.get(step["state"], (step["state"], ""))
    render(f'<div class="ti-section" style="margin:.2rem 0 .4rem"><span class="ti-section-title">{esc(title)}</span>'
           f'{badge(label, kind)}</div>')
    render('<div class="ti-field-label">Question</div>' + prose(step["question"]))

    if step.get("answer"):
        render('<div class="ti-field-label" style="margin-top:.7rem">Candidate Answer</div>' + prose(step["answer"]))
    else:
        render('<div class="ti-note">No answer submitted yet.</div>')

    if step["state"] == "evaluated":
        score = step.get("score")
        score_note = f'<div class="ti-note">Evaluation score: {esc(score)} / 10</div>' if score is not None else ""
        render('<div class="ti-field-label" style="margin:.9rem 0 .4rem">AI Evaluation (4-Dimensional)</div>' + score_note)
        _render_evaluation(step.get("evaluation"))
    elif step["state"] == "submitted":
        render('<div class="ti-note">The answer is saved; its evaluation has not been stored yet.</div>')


def show_interviewer_progress():
    _back_button("← Back to Interviewer Dashboard", "interviewer_dashboard", "progress")

    interview_id = st.session_state.get("progress_interview_id")
    if not interview_id:
        _go("interviewer_dashboard")

    try:
        progress = get_interview_progress(interview_id)
    except requests.RequestException as e:
        st.error("Failed to load the interview progress.")
        st.caption(f"Error: {_api_error_detail(e)}")
        return

    info = progress["interview"]
    status = info.get("display_status")
    render(eyebrow("Technical Interviewer", accent=True) + '<div class="ti-page-title">Interview Progress</div>')

    with st.container(key="card-progress-info"):
        render(fields_grid([
            ("Candidate Name", esc(info.get("candidate_name") or "N/A")),
            ("Job Role", esc(info.get("job_title") or "N/A")),
            ("Interview Mode", esc(_mode_label(info.get("mode")))),
            ("Interview Status", status_badge(status)),
        ]))
        times = [f"Started: {_fmt_ts(info.get('started_at'))}", f"Last activity: {_fmt_ts(progress.get('last_activity_at'))}"]
        if info.get("completed_at"):
            times.append(f"Completed: {_fmt_ts(info.get('completed_at'))}")
        render(f'<div class="ti-note">{esc(" · ".join(times))}</div>')

    if status in ("Completed", "Reviewed"):
        st.info("This interview is complete. Open the full review to see the overall evaluation and record the final decision.")
        if st.button("Open Full Review", type="primary", key="progress_open_review"):
            st.session_state.review_interview_id = interview_id
            _go("interviewer_review")
    else:
        st.info(
            "This interview is still in progress. You are seeing what has been saved so far. "
            "No decision can be recorded until the candidate completes it."
        )

    total = progress["total_questions"]
    cards = [
        ("Main Questions Answered", progress["main_questions_answered"], f"/ {total}"),
        ("Follow-ups Asked", progress["follow_ups_asked"], ""),
        ("Answers Submitted", progress["answers_submitted"], ""),
        ("Answers Evaluated", progress["answers_evaluated"], ""),
    ]
    for column, (label, value, unit) in zip(st.columns(4), cards):
        with column:
            render(stat_card(label, value, unit=unit))

    if progress["answers_submitted"] == 0:
        st.info("No answers have been submitted yet. The candidate has not answered the first question.")

    render(section_title("Questions So Far", f"Follow-ups are limited to {progress['max_follow_ups']} per main question"))
    for group in progress["main_questions"]:
        number = group["main_question_number"]
        with st.container(key=f"card-progress-{number}"):
            _render_progress_step(group["question"], f"Main question {number} of {total}")
            render(f'<div class="ti-note" style="margin-top:.6rem">Follow-ups: {group["follow_up_count"]} of {group["max_follow_ups"]}</div>')
            for follow_up in group["follow_ups"]:
                st.divider()
                _render_progress_step(follow_up, f"Follow-up {follow_up['follow_up_number']} of {group['max_follow_ups']}")


# ===== Main App =====

PAGES = {
    "landing": show_landing,
    "candidate": show_candidate,
    "interview": show_interview,
    "complete": show_complete,
    "results": show_results,
    "interviewer_login": show_interviewer_login,
    "interviewer_dashboard": show_interviewer_dashboard,
    "interviewer_schedule": show_interviewer_schedule,
    "interviewer_review": show_interviewer_review,
    "interviewer_progress": show_interviewer_progress,
}


def main():
    """Main application entry point."""
    if "page" not in st.session_state:
        _restore_interview_after_refresh()
    st.session_state.setdefault("page", "landing")
    page = st.session_state.page

    # Demo gate: every interviewer page except the login screen requires the session flag
    if page.startswith("interviewer_") and page != "interviewer_login" and not st.session_state.get("interviewer_authenticated"):
        page = st.session_state.page = "interviewer_login"

    inject_theme(narrow=page in NARROW_PAGES)

    try:
        handler = PAGES.get(page)
        if handler is None:
            st.session_state.page = "landing"
            st.rerun()
        handler()
    except Exception as e:
        st.error(f"An error occurred: {str(e)}")
        if st.button("Reset"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


if __name__ == "__main__":
    main()
