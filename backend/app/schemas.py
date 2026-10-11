"""Pydantic schemas for API request/response validation."""

import logging
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, field_validator

logger = logging.getLogger(__name__)


# ===== Candidate Schemas =====

class CandidateResponse(BaseModel):
    """Schema for candidate API response."""

    id: int
    name: str
    email: str
    skills: str
    experience_years: int
    resume_summary: str
    created_at: datetime
    has_cv: bool = False  # a saved CV is on file (its text is not returned here)
    cv_file_name: Optional[str] = None
    cv_updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CandidateCvResponse(BaseModel):
    """Result of saving a CV to a candidate."""

    candidate_id: int
    has_cv: bool
    cv_file_name: Optional[str] = None
    cv_updated_at: Optional[datetime] = None
    cv_text_length: int = 0


class CandidateCreate(BaseModel):
    """Schema for creating a new candidate."""

    name: str
    email: str
    skills: str
    experience_years: int = 0
    resume_summary: str


# ===== JobDescription Schemas =====

class JobDescriptionResponse(BaseModel):
    """Schema for job description API response."""

    id: int
    title: str
    description: str
    required_skills: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobDescriptionCreate(BaseModel):
    """Schema for creating a new job description."""

    title: str
    description: str
    required_skills: str


# ===== Question Schemas =====

class QuestionResponse(BaseModel):
    """Schema for question API response."""

    id: int
    question: str
    category: str
    difficulty: str
    job_type: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class QuestionCreate(BaseModel):
    """Schema for creating a new question."""

    question: str
    category: str
    difficulty: str
    job_type: str
    is_active: bool = True


# ===== Interview Schemas =====

class InterviewResponse(BaseModel):
    """Schema for interview API response."""

    id: int
    candidate_id: int
    job_description_id: int
    mode: str  # question_bank, cv_driven, or hybrid
    status: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    abandoned_at: Optional[datetime] = None  # set when the candidate ended the interview without finishing it
    cv_text: Optional[str] = None
    consent_status: Optional[str] = None  # "granted", "declined" or None (no decision recorded)
    consent_recorded_at: Optional[datetime] = None
    cv_available: bool = False  # the interview has a CV, or its candidate has a saved one that will be used
    # Derived, not stored: Scheduled / In Progress / Completed / Reviewed
    display_status: Optional[str] = None
    job_title: Optional[str] = None
    candidate_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class InterviewCreate(BaseModel):
    """Schema for creating a new interview."""

    candidate_id: int
    job_description_id: int
    mode: str = "question_bank"  # question_bank, cv_driven, or hybrid
    cv_text: Optional[str] = None  # Optional CV text for cv_driven or hybrid modes
    status: str = "not_started"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class InterviewQuestionBrief(BaseModel):
    """Brief schema for interview question (used in interview start response)."""

    id: int
    question_id: int
    question_order: int

    model_config = ConfigDict(from_attributes=True)


class InterviewStartResponse(BaseModel):
    """Schema for the response when starting an interview."""

    id: int
    candidate_id: int
    job_description_id: int
    mode: str  # question_bank, cv_driven, or hybrid
    status: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    current_question: InterviewQuestionBrief  # First question for adaptive flow
    current_index: int = 0
    total_questions: int = 5
    main_question_number: int = 1
    follow_up_number: int = 0
    max_follow_ups: int = 3
    # Legacy field for backward compatibility
    questions: list[InterviewQuestionBrief] = []

    model_config = ConfigDict(from_attributes=True)


class InterviewCompletionResponse(BaseModel):
    """Schema for the response when completing an interview."""

    id: int
    candidate_id: int
    job_description_id: int
    status: str
    created_at: datetime
    started_at: datetime
    completed_at: datetime

    overall_score: int
    evaluation_count: int
    average_confidence: float

    recommendation: str
    recommendation_reason: str
    recommendation_confidence: Optional[str] = None  # High, Medium, Low

    model_config = ConfigDict(from_attributes=True)


# ===== InterviewQuestion Schemas =====

class InterviewQuestionResponse(BaseModel):
    """Schema for interview question API response."""

    id: int
    interview_id: int
    question_id: int
    question_order: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InterviewQuestionCreate(BaseModel):
    """Schema for creating a new interview question."""

    interview_id: int
    question_id: int
    question_order: int


# ===== Answer Schemas =====

class AnswerResponse(BaseModel):
    """Schema for answer API response."""

    id: int
    interview_question_id: int
    answer_text: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnswerCreate(BaseModel):
    """Schema for creating a new answer."""

    interview_question_id: int
    answer_text: str


class AnswerSubmission(BaseModel):
    """Schema for submitting an answer during an interview."""

    answer_text: str


# ===== Evaluation Schemas =====

class DimensionScore(BaseModel):
    """Single dimension evaluation (1-5 scale)."""

    score: int  # 1-5
    justification: str
    quote: str  # Direct quote from candidate answer (evidence)

    model_config = ConfigDict(from_attributes=True)


class DimensionEvaluation(BaseModel):
    """Four-dimensional technical evaluation."""

    technical_skills: DimensionScore
    domain_understanding: DimensionScore
    problem_solving: DimensionScore
    communication_clarity: DimensionScore

    model_config = ConfigDict(from_attributes=True)


class EvaluationResponse(BaseModel):
    """Schema for evaluation API response."""

    id: int
    answer_id: int

    # New: Four-dimensional evaluation (parsed from JSON)
    dimensions: Optional[DimensionEvaluation] = None

    # Legacy: Single score (0-10, calculated from dimensions)
    score: int
    strengths: str
    weaknesses: str
    feedback: str
    confidence: float

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator('dimensions', mode='before')
    @classmethod
    def parse_dimensions_json(cls, v):
        """Deserialize dimensions JSON string to DimensionEvaluation object.

        Handles both cases:
        - v is already a DimensionEvaluation dict/object (new data) → pass through
        - v is a JSON string (existing data from database) → parse and return object
        - v is None → pass through
        """
        if v is None:
            return None

        # If already a dict or DimensionEvaluation object, return as-is
        if isinstance(v, dict):
            return v
        if isinstance(v, DimensionEvaluation):
            return v

        # If it's a JSON string, deserialize it
        if isinstance(v, str):
            if not v.strip():
                return None
            try:
                import json
                dimensions_data = json.loads(v)
                return dimensions_data
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to parse dimensions JSON: {str(e)}, value: {v[:100]}")
                return None

        return v


class EvaluationCreate(BaseModel):
    """Schema for creating a new evaluation."""

    answer_id: int

    # Accept either new or legacy format
    dimensions: Optional[DimensionEvaluation] = None
    score: Optional[int] = None
    strengths: Optional[str] = None
    weaknesses: Optional[str] = None
    feedback: Optional[str] = None
    confidence: Optional[float] = None


# ===== Recommendation Schemas =====

class RecommendationResponse(BaseModel):
    """Schema for recommendation API response."""

    id: int
    interview_id: int
    recommendation: str  # Strong Hire, Hire, Hold, No Hire
    confidence: Optional[str] = None  # High, Medium, Low
    reason: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RecommendationCreate(BaseModel):
    """Schema for creating a new recommendation."""

    interview_id: int
    recommendation: str  # Strong Hire, Hire, Hold, No Hire
    confidence: Optional[str] = None  # High, Medium, Low
    reason: str


# ===== Adaptive Interview Schemas (Phase 5) =====

class NextQuestionResponse(BaseModel):
    """Schema for response when fetching next adaptive question."""

    id: int  # interview_question_id
    question_id: int
    question: Optional[str]  # Full question text
    current_index: int
    total_questions: int = 5
    is_follow_up: bool = False
    follow_up_reason: Optional[str] = None
    difficulty_adjusted: Optional[str] = None  # "increased", "same", "decreased"
    is_complete: bool = False  # True when the 5 main questions (and any probing) are done
    # `current_index` counts every asked question (follow-ups included); these describe the interview's
    # real progress. `total_questions` is the number of MAIN questions; follow-ups do not count toward it.
    main_question_number: int = 1  # 1-based main question this belongs to
    follow_up_number: int = 0  # 0 for a main question, 1..max_follow_ups for a follow-up
    max_follow_ups: int = 3

    model_config = ConfigDict(from_attributes=True)


# ===== Consent Schemas =====

class ConsentRequest(BaseModel):
    granted: bool
    notice_version: str  # the version of the notice the candidate was shown


class ConsentResponse(BaseModel):
    interview_id: int
    status: str
    consent_status: str
    consent_recorded_at: datetime
    consent_notice_version: str


# ===== Interview Session (resume) and Progress (interviewer) Schemas =====

class SessionStep(BaseModel):
    """One asked question in an interview, as persisted."""
    interview_question_id: int
    question_id: int
    order: int
    question: str
    is_follow_up: bool
    main_question_number: int
    follow_up_number: int  # 0 for a main question
    answer_id: Optional[int] = None
    answer: Optional[str] = None
    state: str  # evaluated / submitted / pending / unanswered


class InterviewSessionResponse(BaseModel):
    """Everything a client needs to restore an in-progress interview exactly where it stopped."""
    interview_id: int
    status: str
    display_status: str
    mode: str
    candidate_id: int
    job_description_id: int
    candidate_name: Optional[str] = None
    job_title: Optional[str] = None
    total_questions: int
    max_follow_ups: int
    consent_status: Optional[str] = None  # the candidate must have granted consent to continue
    main_question_number: int
    follow_up_number: int
    main_questions_answered: int
    # answer: show `pending`. evaluate: last answer saved, run its evaluation first.
    # next_question: ask the engine what comes next. complete: nothing left to resume.
    next_action: str
    last_answer_id: Optional[int] = None
    pending: Optional[SessionStep] = None
    steps: list[SessionStep]


class ProgressStep(BaseModel):
    """One asked question with whatever has been persisted about it; nothing is inferred or invented."""
    interview_question_id: int
    order: int
    question: str
    is_follow_up: bool
    follow_up_number: int
    state: str  # evaluated / submitted / pending / unanswered
    asked_at: Optional[datetime] = None
    answer: Optional[str] = None
    answered_at: Optional[datetime] = None
    evaluation: Optional[DimensionEvaluation] = None  # 4D, when the evaluation has it
    score: Optional[int] = None  # 0-10 evaluation score, when evaluated
    follow_up_reason: Optional[str] = None
    missing_information: Optional[str] = None
    evaluated_at: Optional[datetime] = None


class ProgressMainQuestion(BaseModel):
    main_question_number: int
    question: ProgressStep
    follow_ups: list[ProgressStep]
    follow_up_count: int
    max_follow_ups: int


class InterviewProgressResponse(BaseModel):
    """Read-only view of an interview's persisted progress (viewing it never changes the interview)."""
    interview: Dict[str, Any]
    total_questions: int
    max_follow_ups: int
    main_questions_asked: int
    main_questions_answered: int
    follow_ups_asked: int
    answers_submitted: int
    answers_evaluated: int
    pending_question: Optional[ProgressStep] = None
    last_activity_at: Optional[datetime] = None
    main_questions: list[ProgressMainQuestion]


# ===== Feedback Report Schemas =====

class ReportEvidence(BaseModel):
    question: str  # e.g. "Question 2, follow-up 1"
    score: int  # 1-5
    justification: str
    quote: Optional[str] = None  # verbatim text from the candidate's answer, or None
    quote_note: Optional[str] = None


class ReportDimension(BaseModel):
    key: str
    label: str
    average: Optional[float] = None  # mean 1-5 across evaluated answers; None if no answer has 4D data
    evaluated_answers: int
    evidence: list[ReportEvidence]
    note: Optional[str] = None


class ReportPoint(BaseModel):
    text: str
    source: Optional[str] = None  # which question this comes from
    quote: Optional[str] = None


class ReportQADimension(BaseModel):
    label: str
    score: int
    justification: str
    quote: Optional[str] = None


class ReportQA(BaseModel):
    label: str
    is_follow_up: bool
    question: str
    answer: Optional[str] = None
    evaluation_status: str  # evaluated / not_evaluated / no_answer
    score: Optional[int] = None  # 0-10
    dimensions: Optional[list[ReportQADimension]] = None
    follow_up_reason: Optional[str] = None
    missing_information: Optional[str] = None
    note: Optional[str] = None


class ReportHumanReview(BaseModel):
    present: bool
    decision: Optional[str] = None
    decision_type: Optional[str] = None
    interviewer_id: Optional[str] = None
    interviewer_name: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    override_reason: Optional[str] = None
    ai_recommendation_at_review: Optional[str] = None
    ai_confidence_at_review: Optional[str] = None
    note: Optional[str] = None


class ReportSecurityFinding(BaseModel):
    source: str  # "CV" or "Answer to Question 3"
    categories: list[str]
    excerpt: Optional[str] = None
    explanation: Optional[str] = None


class ReportSecurity(BaseModel):
    status: str  # clean / suspected / failed / unknown
    summary: str
    findings: list[ReportSecurityFinding]


class ReportConsent(BaseModel):
    status: Optional[str] = None
    recorded_at: Optional[datetime] = None
    notice_version: Optional[str] = None


class FeedbackReport(BaseModel):
    """Interviewer-facing feedback report assembled from stored data (no LLM call, no costs, no credentials)."""
    generated_at: datetime
    interview_id: int
    candidate_name: str
    job_title: str
    mode: str
    completed_at: Optional[datetime] = None
    # Partial (abandoned) interviews: the report is evidence-only and carries no full-interview recommendation
    is_partial: bool = False
    interview_status: str = "completed"
    abandoned_at: Optional[datetime] = None
    partial_label: Optional[str] = None
    answered_count: int = 0
    main_questions_asked: int = 0
    ai_recommendation_note: Optional[str] = None
    consent: ReportConsent
    overall_score: Optional[int] = None  # 0-10
    ai_recommendation: Optional[str] = None  # advisory; the human decision is reported separately
    ai_confidence: Optional[str] = None
    overall_assessment: Optional[str] = None
    evaluation_coverage: str
    strengths: list[ReportPoint]
    strengths_note: Optional[str] = None
    gaps: list[ReportPoint]
    gaps_note: Optional[str] = None
    dimensions: list[ReportDimension]
    next_round_areas: list[ReportPoint]
    next_round_note: Optional[str] = None
    transcript: list[ReportQA]
    human_review: ReportHumanReview
    security: ReportSecurity


# ===== LLM Usage Schemas =====

class OperationUsageStats(BaseModel):
    """Usage statistics for a specific LLM operation."""
    calls: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0


class AudioUsageStats(BaseModel):
    """Usage statistics for audio operations."""
    tts_calls: int = 0
    tts_latency_ms: int = 0
    stt_calls: int = 0
    stt_latency_ms: int = 0


class UsageSummaryResponse(BaseModel):
    """Schema for LLM usage summary for an interview."""
    interview_id: int
    total_llm_calls: int
    total_latency_ms: int
    tokens: Dict[str, int]
    by_operation: Dict[str, OperationUsageStats]
    audio: AudioUsageStats
    # Added for the usage dashboard (LLM operations only; token counts are provider-reported)
    success_calls: int = 0
    error_calls: int = 0
    success_rate: Optional[float] = None  # 0.0-1.0, None when there are no calls
    average_latency_ms: Optional[float] = None
    estimated_cost_usd: float = 0.0  # estimate: actual tokens x configured price
    unpriced_calls: int = 0  # calls whose model has no configured price (excluded from the cost)

    model_config = ConfigDict(from_attributes=True)


class UsageMetrics(BaseModel):
    """Headline LLM usage metrics for a set of usage logs."""
    total_llm_calls: int
    success_calls: int
    error_calls: int
    success_rate: Optional[float] = None
    average_latency_ms: Optional[float] = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    unpriced_calls: int


class UsageLogRow(BaseModel):
    """One LLMUsageLog row, enriched with display-friendly interview context."""
    id: int
    created_at: datetime  # UTC
    interview_id: Optional[int] = None
    candidate_name: Optional[str] = None
    job_title: Optional[str] = None
    operation: str
    operation_label: str  # display label derived from `operation` (there is no agent_name column)
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: int
    status: str
    estimated_cost_usd: Optional[float] = None


class UsageOperationOption(BaseModel):
    value: str
    label: str


class UsageDashboardResponse(BaseModel):
    """Filtered LLM usage: headline metrics over all matches, plus the latest matching rows."""
    summary: UsageMetrics
    logs: list[UsageLogRow]
    total_logs: int  # all matching rows (logs is capped by `limit`)
    available_operations: list[UsageOperationOption]


# ===== Interviewer Access (demo gate) =====

class PasscodeRequest(BaseModel):
    passcode: str


class PasscodeResponse(BaseModel):
    authorized: bool


# ===== Human Review Schemas =====

class HumanReviewCreate(BaseModel):
    """Schema for creating a human review (ACCEPT or OVERRIDE)."""
    decision: str  # Strong Hire, Hire, Hold, No Hire
    override_reason: Optional[str] = None  # Required if OVERRIDE, else null


class HumanReviewResponse(BaseModel):
    """Schema for human review API response."""
    id: int
    interview_id: int
    interviewer_id: str
    interviewer_name: str
    ai_recommendation: str
    ai_confidence: Optional[str]
    ai_reasoning: Optional[str]
    decision: str
    decision_type: str  # ACCEPT or OVERRIDE
    override_reason: Optional[str]
    created_at: datetime
    reviewed_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class QuestionAnswerEvaluation(BaseModel):
    """Single Q&A pair with evaluation."""
    order: int
    question: str
    answer: str
    evaluation: Optional[DimensionEvaluation] = None
    is_follow_up: bool = False


class OverallEvaluation(BaseModel):
    """Interview-level 4D evaluation: per-dimension mean (1-5, one decimal) across evaluated questions."""
    technical_skills: float
    domain_understanding: float
    problem_solving: float
    communication_clarity: float
    evaluated_questions: int
    total_questions: int


class InterviewReviewData(BaseModel):
    """Complete data needed for human review of an interview."""
    interview: Dict[str, Any]  # id, candidate_name, candidate_email, job_title, mode, status, created_at, completed_at
    display_status: Optional[str] = None  # Completed or Reviewed
    cv_text: Optional[str]
    questions_answers_evaluations: list[QuestionAnswerEvaluation]
    overall_evaluation: Optional[OverallEvaluation] = None  # None when no question has a valid 4D evaluation
    ai_assessment: Dict[str, Any]  # overall_score, recommendation, confidence, reason
    human_review: Optional[HumanReviewResponse] = None

    model_config = ConfigDict(from_attributes=True)
