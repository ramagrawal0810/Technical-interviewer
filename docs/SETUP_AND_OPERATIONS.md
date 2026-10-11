# Setup and Operations (Windows, PowerShell)

Commands were taken from the repository's scripts and verified on the development machine. Paths are relative to the repository root unless stated.
Do not paste real keys or passcodes into documents or chat; they live only in `backend/.env`, which is git-ignored.

## Requirements
- Windows with PowerShell and **Python 3.14** (both project virtual environments report 3.14.7; no version is pinned in the repository).
- No Node.js: the frontend is Streamlit. An OpenAI API key with credit is needed for interviews; the automated tests do not need one.

## 1. Backend
```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # then edit backend\.env (never overwrite an existing .env)
```
Variables in `backend/.env` (names from `.env.example`): `OPENAI_API_KEY`, `OPENAI_MODEL` (code default `gpt-4o-mini`; the example shows `gpt-4`),
`INTERVIEWER_PASSCODE` (interviewer gate; access is refused if empty), `DATA_RETENTION_DAYS` (policy value, default 180), `SECRET_KEY`, `DEBUG`, `ENVIRONMENT`, `LOG_LEVEL`,
`VOICE_PROVIDER` (voice is unused). `DATABASE_URL` in the example is ignored: the app always uses `sqlite:///technical_interviewer.db`.

Note: `requirements.txt` pins `uvicorn==0.29.0` and `pypdf==5.0.1`, while the tested environment has newer versions installed (0.54.0 and 6.19.0). A fresh install follows the pins.

## 2. Database (initialise and seed)
```powershell
cd backend
python seed_database.py
```
`seed_database()` runs `init_db()` (creates missing tables, adds missing columns; additive only) and then inserts any missing rows: 3 job descriptions, 10 candidates, 41 questions and
the 10 synthetic CVs. It is safe to repeat and never overwrites a CV a candidate already has. The database file is `backend/technical_interviewer.db`, resolved **relative to the
directory the backend is started from**: always start the backend from `backend/`, otherwise a second, empty database is created elsewhere.

## 3. Start the backend
```powershell
cd backend
.\venv\Scripts\Activate.ps1
python run.py          # uvicorn on 0.0.0.0:8000 with auto-reload
```
Health check: <http://localhost:8000/health> returns `{"status": "ok", ...}`. API docs: <http://localhost:8000/docs>.
`run.py` binds to all network interfaces; see [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) before running on a shared network.

## 4. Frontend
```powershell
cd frontend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py   # http://localhost:8501
```
`BACKEND_URL` (environment variable, default `http://localhost:8000`) points the UI at the backend.

## 5. Tests and validation
```powershell
cd backend;   .\venv\Scripts\python.exe -m unittest discover -s tests -t .      # API, engine, reports, security, consent, CVs (304 tests at the last run)
cd frontend;  .\venv\Scripts\python.exe -m unittest discover -s tests -t .      # Streamlit AppTest against a real throw-away backend (88 tests)
.\backend\venv\Scripts\python.exe -m unittest discover -s evaluation -p "test_evaluation_tools.py"
.\backend\venv\Scripts\python.exe -m unittest discover -s evaluation/sample_interviews -p "test_driver.py"
```
The backend and UI tests use temporary databases and fake LLM clients: they make no paid call and do not touch `backend/technical_interviewer.db`.
Documentation and report checks (free, no LLM):
```powershell
.\backend\venv\Scripts\python.exe docs\check_docs.py                         # link and path check of the maintained docs
.\backend\venv\Scripts\python.exe evaluation\build_client_report.py          # regenerate the client report and evidence index (reads the database read-only; re-saves report snapshots if the backend is running)
.\backend\venv\Scripts\python.exe evaluation\make_client_documents.py        # Word and PDF (needs python-docx; set PYTHON_DOCX_PATH if installed elsewhere)
.\backend\venv\Scripts\python.exe evaluation\validate_client_documents.py
```

## 6. Operations
- **Reports and usage:** available in the interviewer panel; also `GET /api/interviews/{id}/report(.md|.pdf)` and `GET /api/interviews/{id}/usage`.
- **Retention cleanup (manual, dry run by default):** `cd backend; python retention_cleanup.py` lists completed/abandoned interviews older than `DATA_RETENTION_DAYS`; add `--execute` and type `DELETE` to delete. Never run automatically.
- **Backups:** copy `backend/technical_interviewer.db` while the backend is stopped, or use SQLite's backup API. Existing backups are in `backend/db_backups/`.
- **Real-model experiments cost money** (see [TESTING_AND_EVALUATION.md](TESTING_AND_EVALUATION.md)); do not rerun them casually.

## Troubleshooting (project-specific)
| Symptom | Cause | Fix |
|---|---|---|
| Evaluation fails with HTTP 500 "Error evaluating answer with AI service"; usage log shows `RateLimitError` | The provider rejected the call: out of credit/quota or a rate limit. The API hides the real message. | Read the backend console line `Error evaluating answer with OpenAI: RateLimitError: Error code: 429 ...`, check billing, then retry; the step is safe to repeat. |
| "Consent has not been recorded for this interview" (403) | The candidate has not accepted the notice (also for interviews that predate consent) | Candidate accepts in the UI; consent cannot be skipped on the backend. |
| CV-Driven/Hybrid start says a CV is required | Candidate has no saved CV and none was uploaded | Upload a PDF in the schedule form; run `seed_database.py` to attach the sample CVs. |
| Empty candidate list or missing tables | Backend started from another folder, so a different `technical_interviewer.db` was used | Start from `backend/`, then run `seed_database.py`. |
| Cannot move/delete the database file | The backend (or its reload worker) holds it open | Stop all `uvicorn` processes first. |
| UI lost its state after refresh | Streamlit session state is per browser session | In-progress interviews reopen via `?resume=<id>`; abandoned ones cannot be resumed. |
| `ModuleNotFoundError: docx` when building client documents | python-docx is not in any manifest | `pip install python-docx` or set `PYTHON_DOCX_PATH`. |
| UI tests are slow | Each class starts a real backend subprocess | Expected (about 5 minutes for the suite). |
