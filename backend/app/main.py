"""FastAPI application entry point."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from backend directory (works regardless of cwd)
backend_dir = Path(__file__).parent.parent
env_file = backend_dir / ".env"
load_dotenv(dotenv_path=str(env_file), override=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.db.database import init_db
from app.db import models  # noqa: F401 - Imported to register models with Base
from app.api import candidates, jobs, questions, interviews, interview_questions, answers, evaluations, recommendations, audio, usage, human_review, interviewer_access, data_policy as data_policy_api, reports

app = FastAPI(
    title="Technical Interviewer API",
    description="AI-powered technical interview system",
    version="0.1.0",
)

# Add CORS middleware to allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",  # Streamlit
        "http://localhost:5173",  # Vite dev server (legacy React)
        "http://localhost:3000",  # Standard React dev
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    """Initialize the database when the application starts."""
    init_db()


# Include API routers
app.include_router(candidates.router)
app.include_router(jobs.router)
app.include_router(questions.router)
app.include_router(interviews.router)
app.include_router(interview_questions.router)
app.include_router(answers.router)
app.include_router(evaluations.router)
app.include_router(recommendations.router)
app.include_router(audio.router)
app.include_router(usage.router)
app.include_router(usage.dashboard_router)
app.include_router(human_review.router)
app.include_router(interviewer_access.router)
app.include_router(data_policy_api.router)
app.include_router(reports.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "environment": settings.environment,
        "debug": settings.debug,
    }


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Technical Interviewer API",
        "version": "0.1.0",
        "docs": "/docs",
    }


# Placeholder for future routes
# from app.api import routes
# app.include_router(routes.router)
