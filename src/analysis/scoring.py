"""
Scoring Engine.

Calculates final score from triggered filters and applies multiplier patterns.
42 filters | 6 multiplier patterns | Star levels 0-5.

Steps:
  1. Enforce mutual exclusion (keep highest-impact per group).
  2. Sum raw points (floor at 0).
  3. Check all 6 multiplier patterns, apply the highest match.
  4. Compute final score = raw * multiplier.
  5. Map to star level.
"""

import logging

from src import config
from src.database.models import FilterResult, ScoringResult

logger = logging.getLogger(__name__)


def calculate_score(filters_triggered: list[FilterResult]) -> ScoringResult:
    """Calculate final score with mutual exclusion and multipliers.

    Returns:
        ScoringResult with score_raw, multiplier, score_final, star_level.
    """
    # 1. Enforce mutual exclusion — keep only highest-impact per group
    filters = _enforce_mutual_exclusion(filters_triggered)

    # 2. Sum raw points (floor at 0)
    score_raw = max(0, sum(f.points for f in filters))

    # 3. Check multiplier patterns — pick the highest matching multiplier
    ids = {f.filter_id for f in filters}
    multiplier = 1.0
    matched_pattern: str | None = None

    for pattern in config.MULTIPLIER_PATTERNS:
        if _matches_pattern(ids, pattern):
            if pattern["multiplier"] > multiplier:
                multiplier = pattern["multiplier"]
                matched_pattern = pattern["id"]

    # 4. Final score
    score_final = int(score_raw * multiplier)

    # 5. Star level
    star_level = _score_to_stars(score_final)

    return ScoringResult(
        score_raw=score_raw,
        multiplier=multiplier,
        score_final=score_final,
        star_level=star_level,
        filters_triggered=filters,
        multiplier_pattern=matched_pattern,
    )


def _enforce_mutual_exclusion(
    filters: list[FilterResult],
) -> list[FilterResult]:
    """Remove lower-impact filters from mutually exclusive groups.

    Within each group, only the filter with the highest ``abs(points)``
    is kept.  This is a safety net — individual analyzers already enforce
    exclusion, but the scoring engine guarantees it.
    """
    ids_to_remove: set[str] = set()

    for group in config.MUTUALLY_EXCLUSIVE_GROUPS:
        group_set = set(group)
        group_filters = [f for f in filters if f.filter_id in group_set]
        if len(group_filters) <= 1:
            continue
        # Keep the one with highest absolute impact
        group_filters.sort(key=lambda f: abs(f.points), reverse=True)
        for f in group_filters[1:]:
            ids_to_remove.add(f.filter_id)

    if not ids_to_remove:
        return filters
    return [f for f in filters if f.filter_id not in ids_to_remove]


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
