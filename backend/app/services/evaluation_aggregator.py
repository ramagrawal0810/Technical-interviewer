"""Aggregate per-question four-dimensional evaluations into one interview-level evaluation."""

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional

from app.schemas import DimensionEvaluation, OverallEvaluation

DIMENSION_KEYS = ("technical_skills", "domain_understanding", "problem_solving", "communication_clarity")
MIN_SCORE = 1
MAX_SCORE = 5


def _is_valid(evaluation: Optional[DimensionEvaluation]) -> bool:
    if evaluation is None:
        return False
    for key in DIMENSION_KEYS:
        score = getattr(evaluation, key).score
        if isinstance(score, bool) or not MIN_SCORE <= score <= MAX_SCORE:
            return False
    return True


def aggregate_overall_evaluation(
    evaluations: Iterable[Optional[DimensionEvaluation]],
    total_questions: int,
) -> Optional[OverallEvaluation]:
    """
    Mean of each dimension across questions with a valid 4D evaluation, rounded half-up to 1 decimal.

    Questions without a (valid) 4D evaluation are skipped. Returns None when no question has one.
    """
    valid = [e for e in evaluations if _is_valid(e)]
    if not valid:
        return None

    def mean(key: str) -> float:
        total = sum(getattr(e, key).score for e in valid)
        return float((Decimal(total) / Decimal(len(valid))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))

    return OverallEvaluation(
        **{key: mean(key) for key in DIMENSION_KEYS},
        evaluated_questions=len(valid),
        total_questions=total_questions,
    )
