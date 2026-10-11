"""Role-aware question selection for different interview modes.

The interview's Job Role is a HARD constraint: Question.job_type is matched to JobDescription.title
(case-insensitively), so a Frontend interview can only ever receive Frontend questions. Questions
generated for one interview (CV / hybrid / follow-up) carry source_interview_id and are never
offered to another interview.
"""

import logging
import random
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Question, Interview, JobDescription

logger = logging.getLogger(__name__)

DIFFICULTY_LEVELS = ("easy", "medium", "hard")
FOLLOW_UP_CATEGORY = "follow_up"
GENERATED_CATEGORIES = ("cv_generated", "hybrid_generated", FOLLOW_UP_CATEGORY)
CV_QUESTION_CATEGORIES = ("cv_generated", "hybrid_generated")


def normalize_difficulty(value: Optional[str]) -> Optional[str]:
    """'Medium' / ' hard ' -> 'medium' / 'hard'; None if it is not a known level."""
    level = (value or "").strip().lower()
    return level if level in DIFFICULTY_LEVELS else None


def difficulty_rank(value: Optional[str]) -> int:
    """0 easy, 1 medium, 2 hard. Unknown values rank as medium."""
    level = normalize_difficulty(value)
    return DIFFICULTY_LEVELS.index(level) if level else 1


def _role_filter(role_title: str):
    return func.lower(func.trim(Question.job_type)) == role_title.strip().lower()


def get_role_title(db: Session, job_description_id: Optional[int]) -> Optional[str]:
    if job_description_id is None:
        return None
    job = db.query(JobDescription).filter(JobDescription.id == job_description_id).first()
    return job.title if job else None


def select_question_bank_questions(db: Session, job_description_id: Optional[int], count: int = 5) -> list[Question]:
    """
    Shared question-bank questions for ONE role, easiest first.

    Returns [] when there is no role context: a question is never offered without knowing the role.
    """
    role_title = get_role_title(db, job_description_id)
    if not role_title:
        logger.warning(f"No role found for job description {job_description_id}; refusing to select questions")
        return []

    questions = db.query(Question).filter(
        Question.is_active == True,
        Question.source_interview_id.is_(None),
        ~Question.category.in_(GENERATED_CATEGORIES),
        _role_filter(role_title),
    ).order_by(Question.id).all()

    questions.sort(key=lambda q: difficulty_rank(q.difficulty))  # stable: keeps id order within a level
    selected = questions[:count]

    if not selected:
        logger.warning(f"No active question-bank questions found for role '{role_title}'")
    else:
        logger.info(f"Selected {len(selected)} question bank questions for role '{role_title}'")
    return selected


def _levels_by_closeness(target: str) -> list[str]:
    """Difficulty levels ordered by distance from the target (ties prefer the easier level)."""
    target_rank = difficulty_rank(target)
    return sorted(DIFFICULTY_LEVELS, key=lambda level: (abs(DIFFICULTY_LEVELS.index(level) - target_rank),
                                                         DIFFICULTY_LEVELS.index(level)))


def _pools_for_mode(db: Session, interview: Interview, role_title: str, exclude_ids: Iterable[int]) -> list[list[Question]]:
    """
    Candidate pools in priority order. Every pool is already role-filtered, excludes questions
    already used, and never contains follow-ups or another interview's generated questions.
    """
    exclude = list(exclude_ids)
    base = db.query(Question).filter(Question.is_active == True, _role_filter(role_title))
    if exclude:
        base = base.filter(~Question.id.in_(exclude))

    bank = base.filter(
        Question.source_interview_id.is_(None), ~Question.category.in_(GENERATED_CATEGORIES)
    ).order_by(Question.id).all()

    def owned(categories) -> list[Question]:
        return base.filter(
            Question.source_interview_id == interview.id, Question.category.in_(categories)
        ).order_by(Question.id).all()

    if interview.mode == "cv_driven":
        # CV-grounded questions first; the role's bank only once those are used up
        return [owned(("cv_generated",)), bank]
    if interview.mode == "hybrid":
        # Alternate between the two sources (CV question first) so a hybrid interview always mixes both, instead of
        # leaving it to chance whether the random pick ever lands on a CV question.
        cv_pool = owned(CV_QUESTION_CATEGORIES)
        used_cv = db.query(Question.id).filter(
            Question.id.in_(exclude), Question.source_interview_id == interview.id,
            Question.category.in_(CV_QUESTION_CATEGORIES)).count() if exclude else 0
        used_bank = db.query(Question.id).filter(
            Question.id.in_(exclude), Question.source_interview_id.is_(None),
            ~Question.category.in_(GENERATED_CATEGORIES), _role_filter(role_title)).count() if exclude else 0
        return [cv_pool, bank] if used_cv <= used_bank else [bank, cv_pool]
    return [bank]  # question_bank


def _choose(pool: list[Question], difficulty: str, asked_categories: set, rng: random.Random) -> Optional[Question]:
    """Nearest available difficulty; within it prefer a topic not asked yet, then pick at random."""
    if not pool:
        return None
    for level in _levels_by_closeness(difficulty):
        at_level = [q for q in pool if normalize_difficulty(q.difficulty) == level]
        if at_level:
            fresh_topic = [q for q in at_level if q.category not in asked_categories]
            return rng.choice(fresh_topic or at_level)
    # Only questions with an unrecognised difficulty remain
    return rng.choice(pool)


def select_question_bank_adaptive(
    db: Session,
    interview_id: int,
    difficulty: str,
    exclude_question_ids: list[int],
    mode: str = "question_bank",
    *,
    asked_categories: Iterable[str] = (),
    rng: Optional[random.Random] = None,
) -> Optional[Question]:
    """
    Select the next MAIN question for an interview.

    selected role -> role-specific pool for the interview mode -> exclude questions already asked
    -> nearest difficulty to `difficulty` -> prefer a topic not yet covered -> pick one.

    Args:
        db: Database session
        interview_id: Interview to select for (provides the role and mode)
        difficulty: Target difficulty level (easy, medium, hard; case-insensitive)
        exclude_question_ids: Question IDs already used in this interview
        mode: Interview mode (question_bank, cv_driven, hybrid); the interview's own mode is used
        asked_categories: Topics (categories) already covered, to vary the interview
        rng: Random source (injectable for deterministic tests)

    Returns:
        A Question from the interview's role, or None if no suitable unused question exists.
        Never a question from a different role.
    """
    rng = rng or random.Random()
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        logger.error(f"Interview {interview_id} not found")
        return None

    role_title = get_role_title(db, interview.job_description_id)
    if not role_title:
        logger.error(f"Interview {interview_id} has no resolvable job role; refusing to select a question")
        return None

    logger.info(
        f"Selecting main question for interview {interview_id}: role='{role_title}', mode={interview.mode}, "
        f"difficulty={difficulty}, exclude={len(exclude_question_ids)} questions"
    )

    asked = set(asked_categories)
    for pool in _pools_for_mode(db, interview, role_title, exclude_question_ids):
        question = _choose(pool, difficulty, asked, rng)
        if question:
            logger.info(
                f"Selected question {question.id} (role='{question.job_type}', category={question.category}, "
                f"difficulty={question.difficulty})"
            )
            return question

    logger.warning(f"No unused questions left for role '{role_title}' in interview {interview_id}")
    return None
