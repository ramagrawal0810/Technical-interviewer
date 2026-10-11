# Known Limitations and Roadmap

## Current limitations
### Security and privacy (highest priority)
| Limitation | Why it matters | Affected files |
|---|---|---|
| No API authentication or authorization; interviewer gate is a shared demo passcode held in a Streamlit session | Anyone who can reach the server can read reports, candidate data and write human reviews | `backend/app/api/*.py`, `api/interviewer_access.py`, `frontend/app.py: main` |
| Reviewer identity is the constant `demo-interviewer` | Decisions cannot be attributed to a real person (weak audit trail) | `api/human_review.py` |
| `run.py` binds `0.0.0.0` with `reload=True` | Exposes the unauthenticated API on every network interface | `backend/run.py` |
| No encryption at rest; plain HTTP | Candidate answers and CVs are stored and sent unencrypted by the app | `db/database.py` |
| No upload size limit; content-type check relies on the client | Large or hostile uploads can strain the server | `api/interviews.py: upload_cv`, `api/candidates.py` |
| Prompt-injection detector is a pattern list | Misses paraphrases and other languages; flags are advisory | `services/injection_detector.py` |
| Locks are in-process only | Not safe for several server processes (unique indexes remain the guard) | `services/locks.py` |
| Retention is a policy plus a manual tool | No automatic deletion; the candidate record is kept | `services/retention.py`, `backend/retention_cleanup.py` |
| This folder is not a git repository | No version history; easy to lose changes or commit `.env` later | project root |

### Correctness and operability
| Limitation | Detail / files |
|---|---|
| Real error cause hidden from clients | Provider failures return a generic HTTP 500; the cause (credit, rate limit) is only in the server log (`api/answers.py`, `interviews.py`). The usage log keeps only the exception class (`RateLimitError`). |
| Scoring varies between runs | Evaluator temperature is 0.7 (`services/llm_evaluation.py`); measured variance is small for strong/medium answers, larger for weak ones. |
| Dependency pins differ from the tested environment | `requirements.txt` pins `uvicorn==0.29.0`, `pypdf==5.0.1`; installed: 0.54.0 and 6.19.0. |
| Unused code and packages from the voice phase | `stt_service.py`, `tts_service.py`, `api/audio.py`, `frontend/audio_utils.py`, `voice_mode_phase4.py`, `streamlit-webrtc` in `frontend/requirements.txt`. |
| Leftover ad-hoc debug scripts in `backend/` | `test_category_filtering.py`, `trace_question.py`, `trace_questions_origin.py`, `trace_questions_origin2.py`, `verify_fix_logic.py` (not part of the test suites; purpose from their first lines only). |
| Migrations are additive `ALTER TABLE` statements | `db/database.py`; no Alembic, no downgrade path. |
| `.env.example` lists a PostgreSQL `DATABASE_URL` that is ignored | `backend/.env.example`, `db/database.py`. |
| Only text PDFs are supported for CVs | `services/cv_processor.py` (no OCR). |

### Evaluation evidence
| Gap | Needed to close it |
|---|---|
| Human agreement NOT VERIFIED | Independent blind human ratings for at least 5 interviews, then `evaluation/human_agreement.py` |
| Consistency PARTIALLY VERIFIED | Repeated runs of real interview transcripts and the full recommendation pass, with token telemetry (`evaluation/run_final_pass.py`) |
| Injection resistance PARTIALLY VERIFIED | Rerun of experiment C keeping raw model outputs, plus more phrasings and languages |
| Interviews 2-5 use simulated answers | Real candidate interviews |
| Bias tested for one role, one transcript, name and pronouns only | Other roles, qualities, attributes, larger samples |

## Recommended roadmap
1. **Security:** real authentication and roles, authenticated reviewer identity, HTTPS, restricted CORS, no `0.0.0.0` default, upload limits, secret rotation, version control.
2. **Reliability:** surface provider errors clearly (credit versus rate limit) with safe retry/back-off, store the error message class and a safe summary, DB-level locking, align dependency pins with the tested environment.
3. **Evidence:** complete the human-agreement study; rerun the consistency and injection experiments with telemetry and raw outputs; consider a lower evaluator temperature and measure the effect.
4. **Data governance:** automatic audited retention, encryption at rest, candidate data-request tooling.
5. **Cleanup:** remove the unused voice code and packages and the debug scripts after confirming nothing depends on them.
