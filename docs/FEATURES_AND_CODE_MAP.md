# Features and Code Map

Status legend: **Implemented** = code exists and is exercised; **Tested** = automated tests exist (backend 304, UI 88, tooling 27 tests passed at the last runs);
**Partial** = works with a stated gap; **Not implemented** = absent. "Tested" uses fake LLM clients unless stated, so it shows behaviour, not model quality.

## Interview modes and inputs
| Feature | Status | Where | What it does |
|---|---|---|---|
| Job descriptions (3 roles) | Implemented, tested | `data/job_descriptions.json` (loaded), `data/job_descriptions/*.md` (Markdown copies), table `job_descriptions`, `api/jobs.py` | Role title, description, required skills. |
| Question bank (role, skill area `category`, `difficulty`) | Implemented, tested | `data/question_banks/*.json` (41 questions), table `questions`, `seed_database.py: seed_into`, `services/question_selector.py` | Role-tagged easy/medium/hard questions. |
| Question Bank mode | Implemented, tested | `question_selector.select_question_bank_adaptive`, `api/interviews.py: start_interview` | Main questions from the role's bank only; no CV needed. |
| CV-Driven mode | Implemented, tested (fake generator) | `services/cv_processor.py: generate_cv_driven_questions`, `start_interview` | Questions generated from the interview's CV and the role. |
| Hybrid mode | Implemented, tested (fake generator) | `cv_processor.generate_hybrid_questions`, `question_selector._pools_for_mode` | Bank and CV questions alternate; follow-ups see the CV. |
| Candidate CVs (10 synthetic) | Implemented, tested | `backend/sample_data/synthetic_cvs/*.pdf` + `manifest.json`, `sample_data/generate_synthetic_cvs.py`, `services/candidate_cv.py`, columns `candidates.cv_*` | Fictional CVs matching each seeded candidate; linked by e-mail with a name check. |
| CV upload / replacement | Implemented, tested | `api/interviews.py: upload_cv`, `api/candidates.py: save_candidate_cv_endpoint` | Interview-only replacement, or explicit candidate replacement. |
| Interview CV snapshot | Implemented, tested | `api/interviews.py: create_interview` (copies `candidate.cv_text` to `interviews.cv_text`) | Later CV changes do not alter existing interviews. |

## Interview flow
| Feature | Status | Where |
|---|---|---|
| Lifecycle and answer storage | Implemented, tested | `api/interviews.py` (`start_interview`, `submit_answer`, `get_next_question`, `complete_interview`), tables `interviews`, `interview_questions`, `answers` |
| Adaptive follow-ups, **max 3 per main question** | Implemented, tested | `services/question_adapter.py: decide_follow_up` (`MAX_FOLLOW_UPS = 3`), `services/follow_up_generator.py` |
| Difficulty progression across 5 main questions | Implemented, tested | `question_adapter.target_main_difficulty`, `question_selector._choose` |
| Role-specific selection | Implemented, tested | `question_selector._role_filter` (never selects another role's question) |
| Resume after interruption | Implemented, tested | `GET /session` (`interviews.py: get_interview_session`), `services/interview_session.py`, `frontend/app.py: _resume_interview` |
| Progress view for in-progress interviews | Implemented, tested | `GET /progress`, `frontend/app.py: show_interviewer_progress` |
| Abandonment | Implemented, tested | `POST /abandon`, status `abandoned`, column `abandoned_at`, `frontend/app.py: _exit_interview`; partial report for abandoned interviews |
| Duplicate-request safety | Partial | `services/locks.py: keyed_lock` (in-process only) plus unique indexes `uq_answers_interview_question`, `uq_evaluations_answer`, `uq_interview_questions_order` |

## Evaluation and recommendation
| Feature | Status | Where |
|---|---|---|
| Four dimensions (technical skills, domain understanding, problem-solving, communication clarity), 1-5 each | Implemented, tested | `services/llm_evaluation.py` (`EVALUATION_SYSTEM_PROMPT`, `evaluate_answer`, `validate_evaluation_output`), table `evaluations.dimensions` (JSON) |
| Evidence quotes | Implemented, tested | `llm_evaluation.extract_quote_from_answer` marks non-verbatim quotes `[Paraphrased]`; `report_builder.verified_quote` shows a quote only if it is verbatim in the answer |
| 0-10 score | Implemented, tested | `llm_evaluation.calculate_legacy_score` (computed in code from the dimensions) |
| Overall score, recommendation, confidence | Implemented, tested | `interviews.py: complete_interview`, `llm_evaluation.generate_recommendation`, table `recommendations` |
| Strengths, gaps, next-round areas | Implemented, tested | `services/report_builder.py` (derived from stored scores and evaluator notes; no LLM call) |
| Overall 4D aggregation | Implemented, tested | `services/evaluation_aggregator.py` |

## Security, privacy and governance
| Feature | Status | Where |
|---|---|---|
| Prompt hardening (system/user separation, delimited escaped data, JSON mode where supported) | Implemented, tested | `services/prompt_safety.py` and the prompt builders in the three LLM services |
| Prompt-injection detection (heuristic) | Implemented, tested | `services/injection_detector.py`, `services/security_checks.py`, table `content_security_checks`; shown only in interviewer reports |
| Consent | Implemented, tested | `POST /consent`, `services/data_policy.py: require_consent`, `GET /api/data-handling`, UI notice in `frontend/app.py` |
| Retention policy and cleanup tool | Implemented (policy + manual tool), tested | `DATA_RETENTION_DAYS`, `services/retention.py`, `backend/retention_cleanup.py` (dry run by default; nothing automatic) |
| Interviewer gate | Partial (demo only) | `api/interviewer_access.py: verify_passcode` (constant-time compare against `INTERVIEWER_PASSCODE`), UI gate in `frontend/app.py: main` |
| Real authentication / authorization | **Not implemented** | The API has no auth; see [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) |

## Human review
| Feature | Status | Where |
|---|---|---|
| Accept the AI recommendation | Implemented, tested | `api/human_review.py: accept_review`, table `human_reviews` (`decision_type = ACCEPT`) |
| Override with a reason | Implemented, tested | `override_review` (`decision_type = OVERRIDE`, `override_reason`); the AI recommendation row is never changed |
| Interviewer identity | Partial | constant `demo-interviewer`, `Demo Interviewer` (no accounts) |
| Independent human scoring for agreement experiments | Tooling only | `evaluation/export_review_pack.py`, `human_agreement.py`; no independent ratings exist yet |

## Reports and monitoring
| Feature | Status | Where |
|---|---|---|
| Feedback report (JSON, Markdown, PDF) | Implemented, tested | `api/reports.py`, `services/report_builder.py`, `services/report_export.py` |
| Partial report for abandoned interviews | Implemented, tested | `report_builder.build_feedback_report` (`is_partial`) |
| LLM usage logging | Implemented, tested | `services/llm_usage_logger.py`, table `llm_usage_logs`, `services/llm_pricing.py`, `services/usage_aggregator.py`, `api/usage.py`; usage dashboard in `frontend/app.py` |

## Database
Tables (`backend/app/db/models.py`): `candidates`, `job_descriptions`, `questions`, `interviews`, `interview_questions`, `answers`, `evaluations`, `recommendations`,
`content_security_checks`, `human_reviews`, `llm_usage_logs`. Relationships: interview -> candidate and job; interview_questions link an interview to questions in order; one answer per
interview question; one evaluation per answer; recommendation, security checks, human review and usage logs reference the interview. Additive migrations: `database.py: init_db` and its `_migrate_*` helpers.

## API endpoints (from the running app's route table)
- Candidates and jobs: `GET /api/candidates`, `GET /api/candidates/{id}`, `POST /api/candidates/{id}/cv`, `GET /api/jobs`, `GET /api/jobs/{id}`.
- Interviews: `GET|POST /api/interviews`, `GET /api/interviews/{id}`, `POST .../start`, `.../consent`, `.../abandon`, `.../complete`, `.../next-question`, `.../upload-cv`,
  `.../questions/{iq}/answer`, `GET .../session`, `.../progress`, `.../usage`.
- Evaluation: `POST /api/answers/{id}/evaluate`, `GET /api/answers`, `/api/evaluations`, `/api/recommendations`, `/api/interview-questions` (plain list/create helpers).
- Review and reports: `GET .../review-data`, `GET|POST .../human-review`, `POST .../human-review/override`, `GET .../report`, `.../report.md`, `.../report.pdf`.
- Other: `GET /api/data-handling`, `POST /api/interviewer/verify-passcode`, `GET /api/usage`, `GET /api/questions[/{id}]`, `GET /health`, `GET /`.
- Voice (unused by the text UI): `POST /api/audio/speech-to-text`, `POST /api/questions/{id}/speak`.

## Frontend pages (`frontend/app.py`, `PAGES` table)
`show_landing`, `show_candidate`, `show_interview`, `show_complete`, `show_results`, `show_interviewer_login`, `show_interviewer_dashboard`, `show_interviewer_schedule`,
`show_interviewer_review` (with `_render_human_review`, `_render_feedback_report`, `_render_partial_review`), `show_interviewer_progress`. Theme: `frontend/ui_theme.py`, `frontend/.streamlit/config.toml`.

## Evaluation and validation tooling (`evaluation/`)
| File | Purpose |
|---|---|
| `run_experiments.py` | Experiments A (consistency), B (bias, resumable checkpoint), C (injection) against the real model |
| `run_final_pass.py` | Second pass with token telemetry and recommendations; resumable, budget-guarded (incomplete: ran out of credit) |
| `sample_interviews/drive_interviews.py`, `personas.json` | Completes scheduled interviews through the HTTP API with simulated answers |
| `export_review_pack.py`, `human_agreement.py`, `HUMAN_REVIEW_GUIDE.md` | Blind human-review sheets and the agreement analysis |
| `build_client_report.py`, `make_client_documents.py`, `validate_client_documents.py` | Generate and check the client report, evidence index, Word and PDF files |
| `test_evaluation_tools.py`, `sample_interviews/test_driver.py` | Tests for the tooling (no network) |
