# Client Handover

## What the system does
Schedules and runs text-based technical interviews for Backend, Frontend and Full Stack roles in three modes (Question Bank, CV-Driven, Hybrid). It asks five main questions, asks up to three
follow-ups when an answer is vague, incomplete, incorrect or contradicts the CV, scores each answer on four dimensions with quotes from the candidate's own words, and produces a report and an advisory
recommendation (Strong Hire / Hire / Hold / No Hire with confidence). A human interviewer accepts or overrides it; the AI recommendation is stored separately and never overwritten.

## How to run it
Backend: `cd backend; python seed_database.py; python run.py`. Frontend: `cd frontend; streamlit run app.py`. Health check <http://localhost:8000/health>, UI <http://localhost:8501>.
Keys and the interviewer passcode go in `backend/.env` only. Full steps: [SETUP_AND_OPERATIONS.md](SETUP_AND_OPERATIONS.md).

## Demo script (about 10 minutes)
1. Interviewer sign-in (passcode from your `.env`), then **Schedule Interview**: pick a candidate, a role and **Hybrid**. Point out "Saved CV on file": no upload is needed.
2. Candidate view: pick the same candidate. Show the "Before You Begin" notice, the unticked box and the disabled Start button; tick it and start.
3. Answer question 1 with one vague sentence: a follow-up appears (at most three per question). Answer the next with a good answer.
4. Click **Exit Interview** on a second interview to show the confirmation and that it cannot be resumed; open it as the interviewer to show the **Partial Interview** report.
5. Finish a full interview (or use a completed one): open **Review**, show the overall score, dimension scores with quotes, strengths, gaps, next-round areas and the Prompt-Injection Check.
6. Accept or override the recommendation (an override needs a reason) and show that the AI recommendation stays visible; download the PDF and Markdown report.
7. Show the usage and estimated-cost panel.
Expected outcomes: status labels Scheduled, In Progress, Completed, Reviewed, Abandoned; reports contain no keys or costs.

## Evidence available
| What | Where |
|---|---|
| Client evaluation report (Word, PDF) and evidence index | `evaluation/client_documents/` (sources `evaluation/FINAL_CLIENT_EVALUATION_REPORT.md`, `EVIDENCE_INDEX.md`) |
| Raw experiment results | `evaluation/results/` |
| Interview reports of the five completed interviews | `evaluation/evidence/reports/` |
| Simulated-answer transcripts (interviews 2-5) | `evaluation/sample_interviews/runs/` |
| Technical documentation | this `docs/` folder; archived build history in `docs/archive/` |

## Status of the four evaluation requirements
Scoring consistency: PARTIALLY VERIFIED. Bias testing: VERIFIED within its stated scope. Prompt-injection resistance: PARTIALLY VERIFIED. Human agreement: **NOT VERIFIED** (two review decisions are saved, but none is an independent rating).
Details and reasons: [TESTING_AND_EVALUATION.md](TESTING_AND_EVALUATION.md).

## What is not yet verified
Independent human agreement; consistency on real interview transcripts; injection resistance beyond a small set of phrasings; behaviour with real candidates (four of the five completed interviews used simulated answers); production security.

## Before any production deployment
Add real authentication and per-user reviewer identity, HTTPS and restricted CORS, upload limits, encryption at rest, automatic retention if required, and complete the human-agreement study. The current system is a demonstration build:
see [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) and [KNOWN_LIMITATIONS_AND_ROADMAP.md](KNOWN_LIMITATIONS_AND_ROADMAP.md). Provider costs: read the billing page; the report's cost figures are estimates.
