"""SQLAlchemy ORM models for the Technical Interviewer application."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Candidate(Base):
    """Candidate model - represents a person being interviewed."""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    skills: Mapped[str] = mapped_column(Text, nullable=False)
    experience_years: Mapped[int] = mapped_column(Integer, default=0)
    resume_summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # The candidate's saved CV (extracted text; the PDF itself is not kept). New interviews snapshot it into interviews.cv_text.
    cv_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cv_file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cv_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    @property
    def has_cv(self) -> bool:
        return bool((self.cv_text or "").strip())


class JobDescription(Base):
    """JobDescription model - represents a job posting."""

    __tablename__ = "job_descriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required_skills: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Question(Base):
    """Question model - represents an interview question."""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(50), nullable=False)
    job_type: Mapped[str] = mapped_column(String(255), nullable=False)  # matches JobDescription.title
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set for questions generated for ONE interview (cv_generated / hybrid_generated / follow_up);
    # NULL for the shared question bank. Stops generated questions leaking between interviews.
    source_interview_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("interviews.id"), nullable=True, index=True)


class Interview(Base):
    """Interview model - represents an interview session between a candidate and job."""

    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
    job_description_id: Mapped[int] = mapped_column(Integer, ForeignKey("job_descriptions.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="not_started")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Set once when the candidate ends an in-progress interview for good (status "abandoned"); NULL otherwise.
    abandoned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    mode: Mapped[str] = mapped_column(String(50), default="question_bank", nullable=False)
    cv_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Candidate consent to the data-handling notice. NULL = no decision recorded (e.g. interviews that predate consent).
    consent_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # "granted" or "declined"
    consent_recorded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    consent_notice_version: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)


class InterviewQuestion(Base):
    """InterviewQuestion model - represents a question asked during an interview."""

    __tablename__ = "interview_questions"
    # One question per position: concurrent or repeated "next question" requests cannot create duplicates
    __table_args__ = (Index("uq_interview_questions_order", "interview_id", "question_order", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    interview_id: Mapped[int] = mapped_column(Integer, ForeignKey("interviews.id"), nullable=False, index=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("questions.id"), nullable=False, index=True)
    question_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Answer(Base):
    """Answer model - represents a candidate's answer to an interview question."""

    __tablename__ = "answers"
    # At most one answer per asked question
    __table_args__ = (Index("uq_answers_interview_question", "interview_question_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    interview_question_id: Mapped[int] = mapped_column(Integer, ForeignKey("interview_questions.id"), nullable=False, index=True)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Evaluation(Base):
    """Evaluation model - represents an evaluation of a candidate's answer."""

    __tablename__ = "evaluations"
    # At most one evaluation per answer
    __table_args__ = (Index("uq_evaluations_answer", "answer_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    answer_id: Mapped[int] = mapped_column(Integer, ForeignKey("answers.id"), nullable=False, index=True)

    # Four-dimensional evaluation (JSON with technical_skills, domain_understanding, problem_solving, communication_clarity)
    # Each dimension has: score (1-5), justification (str), quote (str from candidate answer)
    dimensions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Legacy score (calculated from dimensions: int((avg(4 dims) - 1) * 2.5))
    # Range: 0-10. Kept for backward compatibility with adaptive questioning.
    score: Mapped[int] = mapped_column(Integer, nullable=False)

    # Legacy text fields (kept for backward compatibility)
    strengths: Mapped[str] = mapped_column(Text, nullable=False)
    weaknesses: Mapped[str] = mapped_column(Text, nullable=False)
    feedback: Mapped[str] = mapped_column(Text, nullable=False)

    # Legacy confidence (kept for backward compatibility, matches High/Medium/Low)
    # High=0.9, Medium=0.7, Low=0.5
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Adaptive follow-up decision made by the evaluator. NULL on evaluations created before this existed,
    # in which case the engine falls back to the score-based rule.
    needs_follow_up: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    follow_up_reason: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # vague/incomplete/contradicts_cv/incorrect
    missing_information: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contradicts_cv: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Recommendation(Base):
    """Recommendation model - represents a hiring recommendation for an interview."""

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    interview_id: Mapped[int] = mapped_column(Integer, ForeignKey("interviews.id"), nullable=False, index=True)
    recommendation: Mapped[str] = mapped_column(String(50), nullable=False)  # Strong Hire, Hire, Hold, No Hire
    confidence: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # High, Medium, Low
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ContentSecurityCheck(Base):
    """Result of the prompt-injection check on one piece of candidate-supplied content (the CV or one answer)."""

    __tablename__ = "content_security_checks"
    __table_args__ = (Index("uq_security_check_source", "interview_id", "source", "source_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    interview_id: Mapped[int] = mapped_column(Integer, ForeignKey("interviews.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # "cv" or "answer"
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # answer id; 0 for the CV
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # "clean", "suspected" or "failed"
    categories: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list of matched categories
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # short snippet of the flagged text
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LLMUsageLog(Base):
    """LLM/API usage tracking for interviews."""

    __tablename__ = "llm_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    interview_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("interviews.id"), nullable=True, index=True)
    question_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    operation: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # "openai"
    model: Mapped[str] = mapped_column(String(50), nullable=False)  # "gpt-4o-mini", "tts-1", "whisper-1"
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # "success", "error"
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class HumanReview(Base):
    """Human interviewer review and final decision on an interview."""

    __tablename__ = "human_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    interview_id: Mapped[int] = mapped_column(Integer, ForeignKey("interviews.id"), nullable=False, unique=True, index=True)

    # Interviewer Identity (Demo: hardcoded, ready for auth later)
    interviewer_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    interviewer_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # AI Recommendation Snapshot at Review Time
    ai_recommendation: Mapped[str] = mapped_column(String(50), nullable=False)  # Strong Hire, Hire, Hold, No Hire
    ai_confidence: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # High, Medium, Low
    ai_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Human Decision
    decision: Mapped[str] = mapped_column(String(50), nullable=False)  # Strong Hire, Hire, Hold, No Hire
    decision_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # ACCEPT, OVERRIDE
    override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Only if decision_type = "OVERRIDE"

    # Audit Trail
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
