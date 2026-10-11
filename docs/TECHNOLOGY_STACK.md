



# Technology Stack

Versions are the ones installed in the project's virtual environments (read with `importlib.metadata` on this machine) and, where different, the pin in the manifest
(`backend/requirements.txt`, `frontend/requirements.txt`). "Transitive" means installed as a dependency of another package and not listed in a manifest.

| Technology | Version (verified) | Used in | Why it is used here | Reasonable alternative | Assessment / trade-off |
|---|---|---|---|---|---|
| Python | 3.14.7 (both venvs) | everything | One language for API, AI calls, reports, tests, tooling | Node.js/TypeScript | Fast to build; 3.14 is very new, so some packages may lag. No Python version is pinned in the repo. |
| FastAPI | 0.115.0 (pinned) | `backend/app/main.py`, `backend/app/api/*.py` | Typed routes, automatic validation and OpenAPI docs at `/docs` | Flask, Django REST Framework | Good fit for a JSON API with Pydantic models; routes are synchronous functions, which is fine at demo scale. |
| Starlette | 0.38.6 (transitive) | `TestClient` in the backend tests, CORS middleware | Provided by FastAPI | n/a | Tests use FastAPI's `TestClient`, so tests run without a network server. |
| Uvicorn | 0.54.0 installed; **manifest pins 0.29.0** | `backend/run.py` | ASGI server | Hypercorn, Gunicorn+uvicorn workers | Single process only. The version mismatch means a fresh install from the manifest gets a different version than the one tested here. |
| Pydantic | 2.13.5 (pinned) | `backend/app/schemas.py` | Request/response validation, report data model (`FeedbackReport`) | dataclasses + manual checks | Strict validation catches malformed LLM output indirectly through explicit checks; v2 syntax required. |
| pydantic-settings / python-dotenv | 2.3.0 / 1.0.1 (pinned) | `backend/app/config.py`, `backend/app/main.py` | Typed settings from `backend/.env` | plain `os.environ` | Missing keys fall back to defaults silently, so a missing `OPENAI_API_KEY` is only noticed when an AI call is made. |
| SQLAlchemy | 2.1.3 (pinned) | `backend/app/db/models.py`, `database.py`, all services | ORM, unique indexes, additive migrations in `init_db()` | SQLModel, raw `sqlite3` | Productive and portable; migrations are hand-written `ALTER TABLE ADD COLUMN`, not Alembic, so only additive changes are supported. |
| SQLite | bundled with Python | `backend/technical_interviewer.db` | Zero-setup local database | PostgreSQL | Perfect for a demo; one writer at a time, file-level access, no network access control. `.env.example` still lists a PostgreSQL `DATABASE_URL`, which the code ignores. |
| OpenAI Python SDK | 1.3.9 (pinned) | `services/llm_evaluation.py`, `follow_up_generator.py`, `cv_processor.py` | Chat completions for evaluation, follow-ups, CV questions, recommendation | Anthropic SDK, local models | Old SDK version: JSON mode is requested only for models that support it (`prompt_safety.supports_json_mode`). The configured model in `backend/.env` is `gpt-4`; the code default is `gpt-4o-mini`. |
| pypdf | 6.19.0 installed in both venvs; **backend manifest pins 5.0.1** | `services/cv_processor.py: extract_text_from_pdf` | Text extraction from uploaded CV PDFs | pdfminer.six, PyMuPDF | Pure Python; only works on text-based PDFs (scans need OCR). Version mismatch with the pin as above. |
| fpdf2 | 2.8.9 (pinned) | `services/report_export.py: render_pdf`, `sample_data/generate_synthetic_cvs.py` | Generate PDF reports and the synthetic CV PDFs without external binaries | ReportLab, WeasyPrint | Lightweight; needs a Unicode TTF (searched in `report_export.find_unicode_font`) for non-Latin text, otherwise falls back to Latin-1. |
| Streamlit | 1.65.0 installed (manifest: `>=1.28.0`) | `frontend/app.py`, `ui_theme.py`, `.streamlit/config.toml` | Fast Python-only UI; dark theme via config and injected CSS | React/Vite, Gradio | Quick to build; reruns the script on every interaction and session state is lost on refresh (mitigated with a `?resume=` query parameter). |
| requests | 2.34.2 (frontend, unpinned) | `frontend/app.py` | HTTP calls from the UI to the backend | httpx | Simple and sufficient. |
| streamlit-webrtc / av | 0.78.1 / 17.1.0 installed | `frontend/voice_mode_phase4.py`, `audio_utils.py` (not used by the text UI) | Left over from the voice phase | n/a | Declared in `frontend/requirements.txt` but unused by the text interface; removable. |
| pandas | 3.0.6 (transitive of Streamlit) | Streamlit tables | n/a | n/a | Not a direct dependency. |
| httpx | 0.27.2 in the backend venv (transitive of openai); not installed in the frontend venv | `evaluation/sample_interviews/drive_interviews.py` | HTTP client for the sample-interview driver | requests | Run the driver with the backend venv's Python. |
| unittest (standard library) | Python 3.14 | `backend/tests`, `frontend/tests`, `evaluation/test_*.py` | No extra test dependency | pytest | Plain and portable; tests are run with `python -m unittest discover`. |
| Streamlit `AppTest` | part of Streamlit 1.65.0 | `frontend/tests/test_ui_workflow.py` | Headless UI testing against a real test backend | Playwright/Selenium | Not a real browser: it does not check visual rendering. |
| Permutation test, variance | standard library (`statistics`, `random`) | `evaluation/run_experiments.py` | Bias and consistency statistics without SciPy | SciPy | Fine for small samples; implemented by hand and unit-tested. |
| python-docx | installed outside the project environments for the document build | `evaluation/make_client_documents.py` | Generate the client Word files | docx templates | **Not listed in any manifest**: install it (`pip install python-docx`) or set `PYTHON_DOCX_PATH`. |
| PyMuPDF (`pymupdf`) | installed outside the project environments | `evaluation/validate_client_documents.py` (optional, exact layout check) | Verify generated PDFs | pypdf | Optional; the validator falls back to an approximate pypdf check. |

Not used (so not claimed): Alembic, Docker, any authentication library, any task queue, Redis, any front-end build tool.
