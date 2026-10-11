# Data Handling

This document describes what the Technical Interviewer demo actually stores, who can see it, what leaves the machine, and what
is **not** protected. Every statement here reflects the current code; the automated tests in
`backend/tests/test_data_handling_doc.py` check the claims that can be checked mechanically (settings, tables, API values).

This is a demo/prototype, not a hardened production system. See [Security limitations](#security-limitations).

## Where data is stored

All application data lives in **one local SQLite file, `technical_interviewer.db`**, created in the working directory of the
backend process (the `backend/` folder when started the documented way). It is not a cloud or external database.

| Data | Table / column | Notes |
|---|---|---|
| Candidate name, email, skills, experience, resume summary | `candidates` | Entered by the interviewer or seeded. |
| Job roles and descriptions (required skills) | `job_descriptions` | Seeded/entered by the interviewer; not candidate data. |
| Interview (status, mode, timestamps) | `interviews` | Status is `not_started`, `in_progress`, `completed` or `abandoned`. `abandoned_at` is set when the candidate confirms they are ending an interview; it cannot be resumed, and everything saved is kept for the interviewer. |
| **Consent decision, timestamp, notice version** | `interviews.consent_status`, `consent_recorded_at`, `consent_notice_version` | `granted` or `declined`; empty for interviews created before consent existed. |
| CV text (only when a CV is supplied) | `interviews.cv_text` | The **extracted text** only. The uploaded PDF file itself is read in memory and is **not saved** anywhere. |
| Questions asked, in order | `interview_questions`, `questions` | The shared question bank also lives in `questions`. Follow-up and CV-generated questions are stored there too, tagged with their interview. |
| Candidate answers (the transcript) | `answers` | Stored verbatim. |
| AI evaluation (scores, justifications, supporting quotes, gaps) | `evaluations` | |
| AI recommendation | `recommendations` | Never overwritten by a human decision. |
| Interviewer decision (accept / override, reason, time) | `human_reviews` | Stored separately from the AI recommendation. |
| Prompt-injection check results | `content_security_checks` | Per interview and source (CV or a specific answer): status, categories, a short excerpt, explanation. |
| LLM usage (operation, model, token counts, latency, status, error text) | `llm_usage_logs` | Contains **no** prompts, answers or CV text. |

Exported reports (PDF / Markdown) are generated in memory and sent to the browser as a download. **The application does not write
reports to disk and does not publish them to any public directory.** Where the downloaded file ends up is up to the person who
downloads it.

The optional speech-to-text endpoint (`/api/audio/speech-to-text`) is not used by the current interface; audio is processed in
memory and forwarded to OpenAI, not saved by the application.

## Who can access it

- The backend API has **no authentication**. Anyone who can reach the API address can call it.
- The interviewer pages in the Streamlit app are behind a **demo passcode** (`INTERVIEWER_PASSCODE` in `backend/.env`). This is a
  convenience gate in the user interface, not real access control: it is not tied to a user account, and it does not protect the API.
- Because there are no accounts, every Human Review is recorded with the fixed identity `demo-interviewer` / `Demo Interviewer`.
  The application does **not** know which real person made a decision. Interview id, decision, accept-or-override, reason and
  timestamp are stored faithfully; the person is not.
- Security-check results (prompt-injection flags) appear only in the interviewer report. They are not part of any candidate-facing
  response.

## What is sent to an external provider

The application sends data to **OpenAI** (the LLM provider) to evaluate answers, generate follow-up questions, generate questions from a CV and produce a recommendation:

- the interview question and the candidate's answer;
- the job role, and for CV-based interviews the CV text, job description and required skills;
- for the final recommendation, the per-question evaluation summaries.

Candidate text is sent as clearly delimited, untrusted data (see below). Feedback reports, exports, consent handling, the
prompt-injection check and retention tooling make **no** LLM calls. The API key is read from `backend/.env` and is never included in
responses or reports. OpenAI's own retention and processing terms apply to what is sent; they are outside this application.

## Retention

- Retention is configurable with `DATA_RETENTION_DAYS` in `backend/.env` (default **180** days after an interview is completed).
- This is a **policy**. **Nothing is deleted automatically**: there is no scheduled job, and nothing runs at application startup.
- Cleanup is a manual administrator command, run from `backend/`:

```
python retention_cleanup.py                      # dry run (default): lists completed interviews past the retention period
python retention_cleanup.py --days 90            # dry run with a different period
python retention_cleanup.py --interview-id 12    # dry run for one interview
python retention_cleanup.py --execute            # delete what the dry run listed (you must type DELETE to confirm)
```

- Only **completed**, **abandoned** (the candidate ended it) or never-started interviews can be deleted; abandoned ones count from `abandoned_at`. In-progress interviews are refused.
- Deleting an interview removes its answers, evaluations, recommendation, human review, security checks, generated questions and
  question assignments. LLM usage rows are kept for cost accounting but detached from the interview. The **candidate record
  (name, email, skills, resume summary) and the question bank are not deleted** and must be removed separately by an administrator
  if required.
- Deletion removes rows from the SQLite file. It does not securely wipe disk space, and it cannot remove copies you have exported
  or backups of the database file.

## Consent

Before an interview starts, the candidate sees a plain-language notice (served by `GET /api/data-handling`) covering AI evaluation,
what is stored, what interviewers can see, where it is stored, the retention period and how to ask for information or deletion.
The candidate must tick an unchecked box and press *Agree and start*. The decision and time are stored with the notice version.

- Declining records `declined`; the interview does not start and is never marked Completed or Reviewed.
- The backend enforces this (start, answer, evaluation, next question, completion), not only the interface.
- Interviews that were already in progress before consent was introduced have no recorded decision. They are **not** deleted or
  completed automatically; the candidate sees the notice when resuming and must accept before continuing.
- There is no self-service deletion or data-request form. Requests go to the interviewer/administrator, who can use the cleanup tool.

## Prompt-injection handling

CVs, answers, job descriptions and question text are treated as untrusted. Instructions live in the system message; candidate
content is placed in the user message inside delimited, JSON-escaped blocks, and model output is requested as constrained JSON
where the model supports it. A separate, rule-based detector (a fixed list of patterns, not a model) flags instruction-like content (for example "ignore previous
instructions", requests for a score or recommendation, requests to reveal the prompt). Detection is recorded per interview and
source, shown to the interviewer, hidden from candidates, and **never changes scores**. It is a heuristic: it cannot catch every attack (paraphrases, other languages, text split across answers or encoded content can slip past) and can occasionally flag harmless text; it can miss novel
phrasing, and the tests use fake model clients, so they cannot prove how a real model behaves. Human review remains the safeguard.

## Security limitations

- No encryption at rest is implemented. The database is a plain SQLite file; protect the machine and its backups accordingly.
- No transport encryption is configured by the app; run it behind HTTPS if it is ever exposed beyond localhost.
- No real authentication or authorization (see *Who can access it*).
- No automatic deletion or expiry.
- Server logs may include operational messages; they are not designed to be a safe place for candidate data.
