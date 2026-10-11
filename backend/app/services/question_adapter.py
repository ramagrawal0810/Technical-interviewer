"""Analyze evaluation and recommend adaptive question strategy."""

import logging
import math
from typing import Optional, Sequence, TypedDict

from app.db.models import Evaluation

logger = logging.getLogger(__name__)

# ---- Interview policy ----
MAIN_QUESTION_COUNT = 5        # main questions per interview; follow-ups do not count toward it
MAX_FOLLOW_UPS = 3             # HARD limit of probing follow-ups per main question
CV_MODES = ("cv_driven", "hybrid")

LOW_SCORE_THRESHOLD = 3        # 0-10 evaluation score at/below which an answer is weak enough to probe
STRONG_SCORE = 8               # a main question averaging this or more moves difficulty up
WEAK_SCORE = 4                 # ... this or less moves it down
PASSING_SCORE = 6              # halfway levels round up only when the candidate is doing at least this well

FOLLOW_UP_REASONS = ("vague", "incomplete", "contradicts_cv", "incorrect")
DIFFICULTY_LEVELS = ("easy", "medium", "hard")
# Baseline per main question: Easy, Easy->Medium, Medium, Medium->Hard, Hard (0=easy, 1=medium, 2=hard)
DIFFICULTY_LADDER = (0.0, 0.5, 1.0, 1.5, 2.0)


class AdaptationRecommendation(TypedDict):
    """Recommended next question strategy based on evaluation."""
    recommended_difficulty: str  # easy, medium, hard
    should_follow_up: bool
    follow_up_reason: Optional[str]
    weakness_to_probe: Optional[str]
    topic: Optional[str]


def analyze_evaluation_and_recommend(evaluation: Evaluation) -> AdaptationRecommendation:
    """
    Analyze candidate's evaluation to recommend next question strategy.

    Args:
        evaluation: Evaluation object with score, strengths, weaknesses, feedback

    Returns:
        AdaptationRecommendation with difficulty, follow-up decision, and topics to probe
    """
    score = evaluation.score
    weaknesses = evaluation.weaknesses or ""
    feedback = evaluation.feedback or ""

    logger.info(f"Analyzing evaluation. Score: {score}, Weaknesses: {weaknesses[:100]}...")

    # Determine difficulty progression
    if score <= 3:
        recommended_difficulty = "easy"
        should_follow_up = True
        follow_up_reason = "low_score"
    elif score <= 5:
        recommended_difficulty = "medium"
        should_follow_up = True
        follow_up_reason = "incomplete_answer"
    elif score <= 7:
        recommended_difficulty = "medium"
        should_follow_up = False
        follow_up_reason = None
    elif score <= 9:
        recommended_difficulty = "hard"
        should_follow_up = False
        follow_up_reason = None
    else:  # score == 10
        recommended_difficulty = "hard"
        should_follow_up = False
        follow_up_reason = None

    # Extract weakness/topic from evaluation text
    weakness_to_probe = None
    if weaknesses and len(weaknesses.strip()) > 0:
        # Use first weakness as probe topic
        lines = weaknesses.split(";")
        if lines:
            weakness_to_probe = lines[0].strip()

    # Try to identify the main topic from feedback
    topic = None
    if feedback and len(feedback.strip()) > 0:
        lines = feedback.split(".")
        if lines:
            topic = lines[0].strip()

    result = AdaptationRecommendation(
        recommended_difficulty=recommended_difficulty,
        should_follow_up=should_follow_up,
        follow_up_reason=follow_up_reason,
        weakness_to_probe=weakness_to_probe,
        topic=topic,
    )

    logger.info(
        f"Adaptation recommendation: difficulty={recommended_difficulty}, "
        f"follow_up={should_follow_up}, reason={follow_up_reason}"
    )

    return result


class FollowUpDecision(TypedDict):
    """Whether to probe the last answer before moving on, and why."""
    should_follow_up: bool
    reason: Optional[str]               # one of FOLLOW_UP_REASONS (or legacy low_score/incomplete_answer)
    missing_information: Optional[str]
    capped: bool                        # True when probing was wanted but the per-question limit stopped it


def decide_follow_up(evaluation: Evaluation, follow_up_count: int, mode: str) -> FollowUpDecision:
    """
    Decide whether to ask a probing follow-up after the candidate's last answer.

    Probe when the evaluator flagged the answer as vague, incomplete or incorrect, when it contradicts
    the CV (cv_driven/hybrid only), or when the score is too low to move on. Never probe once
    `follow_up_count` (follow-ups already asked for the CURRENT main question) reaches MAX_FOLLOW_UPS.

    Evaluations created before the follow-up decision existed (needs_follow_up is NULL) use the
    original score-based rule.
    """
    missing = evaluation.missing_information or None

    if evaluation.needs_follow_up is None:
        legacy = analyze_evaluation_and_recommend(evaluation)
        wanted, reason = legacy["should_follow_up"], legacy["follow_up_reason"]
    elif evaluation.contradicts_cv and mode in CV_MODES:
        wanted, reason = True, "contradicts_cv"
    elif evaluation.needs_follow_up:
        wanted, reason = True, evaluation.follow_up_reason or "incomplete"
    elif evaluation.score <= LOW_SCORE_THRESHOLD:
        wanted, reason = True, "incorrect"
    else:
        wanted, reason = False, None

    if wanted and follow_up_count >= MAX_FOLLOW_UPS:
        logger.info(f"Follow-up wanted ({reason}) but the limit of {MAX_FOLLOW_UPS} was reached; moving on")
        return FollowUpDecision(should_follow_up=False, reason=None, missing_information=missing, capped=True)

    return FollowUpDecision(should_follow_up=wanted, reason=reason if wanted else None,
                            missing_information=missing, capped=False)


def target_main_difficulty(main_number: int, previous_main_scores: Sequence[float]) -> str:
    """
    Target difficulty for the next MAIN question (follow-ups never change it).

    Baseline ladder by main question number: easy, easy->medium, medium, medium->hard, hard.
    The previous main question's score (mean over it and its follow-ups) adapts it: strong answers
    move up half a level, weak answers move down half a level. A halfway level rounds up only when
    the candidate is doing at least passably, otherwise it stays at the lower level.
    """
    index = min(max(main_number, 1), len(DIFFICULTY_LADDER)) - 1
    level = DIFFICULTY_LADDER[index]
    last = previous_main_scores[-1] if previous_main_scores else None

    if last is not None:
        if last >= STRONG_SCORE:
            level += 0.5
        elif last <= WEAK_SCORE:
            level -= 0.5
    level = min(max(level, 0.0), 2.0)

    if level == int(level):
        return DIFFICULTY_LEVELS[int(level)]
    rounds_up = last is not None and last >= PASSING_SCORE
    return DIFFICULTY_LEVELS[math.ceil(level) if rounds_up else math.floor(level)]
