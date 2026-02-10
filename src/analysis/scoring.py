"""
Scoring Engine.

Calculates final score from triggered filters and applies multiplier patterns.
42 filters | 6 multiplier patterns | Star levels 0-5.
"""

import logging

from src import config
from src.database.models import FilterResult, ScoringResult

logger = logging.getLogger(__name__)


def calculate_score(filters_triggered: list[FilterResult]) -> ScoringResult:
    """
    Calculate final score with multipliers.

    1. Sum raw points (floor at 0).
    2. Check all 6 multiplier patterns, apply the highest match.
    3. Compute final score = raw * multiplier.
    4. Map to star level.
    """
    score_raw = max(0, sum(f.points for f in filters_triggered))

    ids = {f.filter_id for f in filters_triggered}
    multiplier = 1.0
    matched_pattern: str | None = None

    for pattern in config.MULTIPLIER_PATTERNS:
        if _matches_pattern(ids, pattern):
            if pattern["multiplier"] > multiplier:
                multiplier = pattern["multiplier"]
                matched_pattern = pattern["id"]

    score_final = int(score_raw * multiplier)
    star_level = _score_to_stars(score_final)

    return ScoringResult(
        score_raw=score_raw,
        multiplier=multiplier,
        score_final=score_final,
        star_level=star_level,
        filters_triggered=filters_triggered,
        multiplier_pattern=matched_pattern,
    )


def _matches_pattern(ids: set[str], pattern: dict) -> bool:
    """Check if a set of filter IDs matches a multiplier pattern."""
    # "required": all must be present
    required = pattern.get("required", set())
    if required and not required.issubset(ids):
        return False

    # "any_of": at least one must be present
    any_of = pattern.get("any_of", set())
    if any_of and not ids.intersection(any_of):
        return False

    # "none_of": none must be present
    none_of = pattern.get("none_of", set())
    if none_of and ids.intersection(none_of):
        return False

    # "min_count_from": N or more from the set must be present
    min_count_from = pattern.get("min_count_from", set())
    min_count = pattern.get("min_count", 0)
    if min_count_from and len(ids.intersection(min_count_from)) < min_count:
        return False

    return True


def _score_to_stars(score: int) -> int:
    """Map a final score to a star level (0-5)."""
    for threshold, stars in config.STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0
