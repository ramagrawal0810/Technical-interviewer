# Technical Interviewer

A text-based AI interviewer. It asks role-specific technical questions, probes vague or incomplete answers with follow-ups, evaluates
every answer on four dimensions, and produces an evidence-backed report and a hiring recommendation that a human interviewer reviews
and can override. It is a single LLM-driven system (OpenAI); there is no multi-agent orchestration.

Scope: **text interviews only.** The repository still contains an unused speech-to-text endpoint and some voice helper files from an
earlier phase; the interface does not expose them and nothing in this document or the evaluation covers audio or video.

## Documentation
Full documentation lives in [`docs/`](docs/README.md): [architecture](docs/ARCHITECTURE.md), [technology stack](docs/TECHNOLOGY_STACK.md), [feature-to-code map](docs/FEATURES_AND_CODE_MAP.md),
[setup and operations](docs/SETUP_AND_OPERATIONS.md), [security and privacy](docs/SECURITY_AND_PRIVACY.md), [testing and evaluation](docs/TESTING_AND_EVALUATION.md),
[limitations and roadmap](docs/KNOWN_LIMITATIONS_AND_ROADMAP.md) and a [client handover guide](docs/CLIENT_HANDOVER.md). Historical build reports are archived in [`docs/archive/`](docs/archive/README.md).
The client evaluation deliverables (report, evidence index, Word and PDF) are in `evaluation/` and `evaluation/client_documents/`.

## Contents

- [How it works](#how-it-works)
- [Run it](#run-it)
- [Interview modes and inputs](#interview-modes-and-inputs)
- [Evaluation and reports](#evaluation-and-reports)
- [Data storage, retention and consent](#data-storage-retention-and-consent)
- [Avoiding judgements on non-job-related signals](#avoiding-judgements-on-non-job-related-signals)
- [Prompt-injection handling](#prompt-injection-handling)
- [LLM usage logging](#llm-usage-logging)
- [Tests and evaluation experiments](#tests-and-evaluation-experiments)
- [Known limitations](#known-limitations)

## How it works

```
technical-interviewer/
  backend/     FastAPI + SQLAlchemy + SQLite (the API, the evaluator, reports, seed data loader)
  frontend/    Streamlit app (candidate screens and the interviewer panel)
  data/        job descriptions (JSON and Markdown), question banks (JSON), seeded candidates
  docs/        DATA_HANDLING.md (what is stored, who can see it, retention)
  evaluation/  reproducible evaluation experiments and their recorded results
```

1. An interviewer schedules an interview (candidate, role, mode). The candidate opens it, reads the data-handling notice and must
   explicitly consent before it can start.
2. The system asks five main questions of the interview's own role, easy to hard. After each answer the evaluator scores it; a
   vague, incomplete, incorrect or CV-contradicting answer triggers a follow-up, at most three per main question.
3. When the last main question is answered the interview is completed, a recommendation is generated, and the interviewer reviews
   the report and records their own decision (accept or override).
4. A candidate can end an interview early (Abandoned); it cannot be resumed and produces a partial report only.

Statuses: Scheduled, In Progress, Completed, Reviewed (completed + human decision), Abandoned.

## Run it

Prerequisites: Python 3.10+ and an OpenAI API key. The database is created automatically.

```powershell
# Backend (terminal 1)
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # then edit .env: OPENAI_API_KEY, INTERVIEWER_PASSCODE (never commit .env)
python seed_database.py         # additive and safe to repeat: roles, candidates, question bank, sample CVs
python run.py                   # http://localhost:8000  (API docs at /docs)

# Frontend (terminal 2)
cd frontend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py            # http://localhost:8501
```

Settings in `backend/.env`: `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-4o-mini`; `.env.example` shows `gpt-4`),
`INTERVIEWER_PASSCODE` (demo gate for the interviewer panel), `DATA_RETENTION_DAYS` (policy value, default 180).
`DATABASE_URL` in `.env.example` is **not used**: the application always uses the SQLite file `technical_interviewer.db`.

## Interview modes and inputs

| Input | Where | Format |
|---|---|---|
| Job descriptions (3 roles: Backend, Frontend, Full Stack) | `data/job_descriptions.json`, mirrored in `data/job_descriptions/*.md` | JSON (loaded into the database) and Markdown |
| Question bank (role, skill area = `category`, `difficulty`) | `data/question_banks/*.json` | JSON, 41 questions, easy/medium/hard for every role |
| Candidates | `data/candidates.json` | JSON |
| Candidate CVs (10 synthetic, fictional people) | `backend/sample_data/synthetic_cvs/*.pdf` + `manifest.json` | PDF |

- **Question Bank:** questions come from the interview's own role in the bank. No CV needed.
- **CV-Driven:** questions are generated from the candidate's saved CV and the role.
- **Hybrid:** alternates question-bank questions of the role with CV-based questions; follow-ups see the same CV.

Each candidate has a saved CV (`candidates.cv_text`; `seed_database.py` attaches the sample CVs by matching e-mail and checking the
name printed in the PDF). Scheduling a CV-based interview copies that text into the interview, so later changes to the saved CV never
alter an existing interview. An interviewer can upload a replacement for one interview, or explicitly replace the candidate's saved
CV; neither touches any other candidate.

## Evaluation and reports

Every answer is scored 1-5 on **Technical Skills, Domain Understanding, Problem-Solving Approach and Communication Clarity**, each with a
written justification and a quote from the answer (a quote is shown in reports only if it appears verbatim in the answer). The 0-10
score is derived from the dimensions in code, not taken from the model. The overall score is the mean of the answer scores.

The report for a completed interview contains the overall score, AI recommendation and confidence, an overall assessment, strengths,
knowledge gaps, per-dimension scores with evidence, suggested areas for the next round, the question-by-question transcript with
evaluations, the prompt-injection check, and the human decision. Strengths, gaps and next-round areas are derived from stored scores and
evaluator notes; opening or exporting a report makes no LLM call. PDF and Markdown downloads come from the same data.

The AI recommendation is advisory and is stored separately from the interviewer's decision; an override never replaces it. An abandoned
interview gets a clearly labelled **partial** report (no overall score, no final recommendation, no human decision).

## Data storage, retention and consent

Full detail: [docs/DATA_HANDLING.md](docs/DATA_HANDLING.md). In short:

- **Where:** one local SQLite file, `backend/technical_interviewer.db`. Transcripts are rows in `answers`; evaluations, recommendations,
  human reviews, security checks and consent decisions are in their own tables. CV text is stored as extracted text only (the PDF is not
  kept). Reports are generated in memory and are not written to disk by the application.
- **Sent to OpenAI:** questions, answers, CV text and job role, and evaluation summaries for the final recommendation.
- **Retention:** the policy is `DATA_RETENTION_DAYS` (default 180) after an interview is completed or abandoned. **Nothing is deleted
  automatically.** An administrator runs `python retention_cleanup.py` from `backend/`; it is a dry run unless `--execute` is given
  and `DELETE` is typed, it never deletes in-progress interviews, and it keeps the candidate record and question bank. Abandoned
  interviews are retained (answers, evaluations, security checks) until that cleanup removes them.
- **Consent:** before an interview starts the candidate sees a notice and must tick an unchecked box. Declining stores the decision, the
  interview stays Scheduled and does not start. The backend enforces consent on start, answer, evaluation, next question and completion.
- **Not provided:** no encryption at rest, no real authentication (a shared demo passcode only; every review is stored as
  `demo-interviewer`), no self-service deletion.

## Avoiding judgements on non-job-related signals

The system is text-only and judges candidates only on what they write: the evaluator receives the job role, the question, the answer
text and, for CV-based modes, the CV text, and scores four job-related dimensions defined in its prompt. No audio, video, images or
sensor data are collected or analysed, and the code contains no facial-expression, emotion, sentiment or body-language analysis.
Because a CV can contain a name, pronouns or photo captions, this reduces but does not eliminate the chance of such cues reaching the
model; the prompt does not use them, and the bias experiment in `evaluation/RESULTS.md` measures score differences when only the
name and pronouns change. That experiment is small and is evidence, not a guarantee of fairness. A human interviewer makes the final decision.

## Prompt-injection handling

CVs, answers, job descriptions and questions are treated as untrusted. Instructions are in the system message; candidate content is
placed in the user message as delimited, escaped data; output is requested as JSON where the model supports it and strictly
validated. A separate rule-based detector flags instruction-like text (score or recommendation demands, "ignore previous
instructions", prompt-extraction requests) and stores the result per interview and per answer or CV; interviewers see it in the
report and candidates never do. Detection never changes a score. A check that did not run is shown as *unknown* and a failed check as
*failed*, never as clean. **The detector is a list of patterns; it does not catch every possible injection** (paraphrases, other
languages and encoded text can slip through), and it can flag harmless text. The real-model results in `evaluation/RESULTS.md` show
how the evaluator behaved on specific attempts, not that it is immune.

## LLM usage logging

Every OpenAI call (question generation, evaluation, follow-up, recommendation) is logged to `llm_usage_logs` by
`backend/app/services/llm_usage_logger.py`. Token counts are the `usage` values returned by the API, not estimates; failed calls are
logged with status `error` and no tokens.

| Assignment field | Stored as |
|---|---|
| Timestamp | `created_at` |
| User ID | `interview_id` (an interview belongs to one candidate); there are no user accounts, so there is no separate user id |
| Agent name | `operation` (`evaluation`, `adaptive_follow_up`, `hybrid_question_generation`, `cv_question_generation`, `recommendation_generation`) with `provider`; there is one LLM system, so the operation identifies which role the call played |
| Model | `model` |
| Input / output tokens | `prompt_tokens` / `completion_tokens` (and `total_tokens`) |
| Latency | `latency_ms` |
| Success / error | `status` (+ `error_message`, class name only) |
| Estimated cost | computed when read from stored tokens and the price table in `llm_pricing.py` (an estimate; unpriced models show no cost) |

The interviewer dashboard shows usage and estimated cost. Usage and cost are not part of candidate-facing screens or exported reports.

## Tests and evaluation experiments

```powershell
cd backend;  .\venv\Scripts\python.exe -m unittest discover -s tests -t .     # API, engine, reports, security, consent, CVs
cd frontend; .\venv\Scripts\python.exe -m unittest discover -s tests -t .     # Streamlit UI flows against a real test backend
```

The automated tests replace the OpenAI client with doubles, so they verify behaviour and prompt construction, not model quality.
Experiments against the real model are in [`evaluation/`](evaluation/): scoring consistency, bias check and prompt-injection
resistance (`run_experiments.py`, results in `evaluation/RESULTS.md`), and human agreement. For the last one, `export_review_pack.py`
builds blind review sheets from completed interviews, a person scores them following `evaluation/HUMAN_REVIEW_GUIDE.md`, and
`human_agreement.py` analyses the result; it reports NOT VERIFIED until real human scores exist. `evaluation/test_evaluation_tools.py`
tests that tooling.

## Known limitations

- Demo-grade access control only; the API itself is unauthenticated.
- No encryption at rest and no automatic deletion.
- The prompt-injection detector is heuristic.
- The evaluator uses temperature 0.7, so repeated scoring of the same answer can vary (measured in `evaluation/RESULTS.md`).
- Sample CVs and candidates are synthetic; question quality of generated questions depends on the model and was not independently rated.
