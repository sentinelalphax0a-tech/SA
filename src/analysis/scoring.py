"""
Scoring Engine — v2.

Calculates final score from triggered filters using the new 4-category system.

Categories (mapped from old filter categories):
  ACCUMULATION  ← wallet, origin
  COORDINATION  ← behavior, confluence
  TIMING        ← market
  MARKET        ← negative

Steps:
  1. Map filters to new categories via OLD_TO_NEW_CATEGORY.
  2. Enforce mutual exclusion (keep highest-impact per group).
  3. Sum raw points (floor at 0).
  4. Apply amount-based multiplier.
  5. Compute final score = raw * multiplier.
  6. Assign star level from NEW_STAR_THRESHOLDS.
  7. Validate stars (category diversity, amount, coordination requirements).
"""

import logging
import math

from src import config
from src.database.models import FilterResult, ScoringResult

logger = logging.getLogger(__name__)

# Hard cap on final score to prevent runaway values
SCORE_CAP = 400


def calculate_score(
    filters_triggered: list[FilterResult],
    total_amount: float = 0.0,
    wallet_market_count: int | None = None,
) -> ScoringResult:
    """Calculate final score with mutual exclusion and amount multiplier.

    Args:
        filters_triggered: All filter results from analyzers.
        total_amount: Total accumulated amount in USD for amount multiplier.
        wallet_market_count: Distinct markets traded by wallet in 72h (sniper/shotgun).

    Returns:
        ScoringResult with score_raw, multiplier, score_final, star_level.
    """
    # 1. Enforce mutual exclusion — keep only highest-impact per group
    filters = _enforce_mutual_exclusion(filters_triggered)

    # 2. Sum raw points (floor at 0)
    score_raw = max(0, sum(f.points for f in filters))

    # 3. Get new categories triggered
    categories = _get_categories(filters)

    # 4. Amount-based multiplier
    multiplier = _get_amount_multiplier(total_amount)

    # 4b. Diversity multiplier (sniper vs shotgun)
    diversity_mult = _get_diversity_multiplier(wallet_market_count)
    multiplier = round(multiplier * diversity_mult, 2)

    # 5. Final score (capped)
    score_final = min(SCORE_CAP, round(score_raw * multiplier))

    # 6. Star level from new thresholds
    star_level = _score_to_stars(score_final)

    # 7. Validate stars (may downgrade)
    star_level = _validate_stars(
        star_level, categories, total_amount, filters,
    )

    return ScoringResult(
        score_raw=score_raw,
        multiplier=multiplier,
        score_final=score_final,
        star_level=star_level,
        filters_triggered=filters,
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


def _get_categories(filters: list[FilterResult]) -> set[str]:
    """Get the set of new scoring categories triggered by positive filters."""
    cats: set[str] = set()
    for f in filters:
        if f.points <= 0:
            continue
        new_cat = config.OLD_TO_NEW_CATEGORY.get(f.category)
        if new_cat:
            cats.add(new_cat)
    return cats


def _get_amount_multiplier(total_amount: float) -> float:
    """Get the amount-based multiplier using a logarithmic curve.

    Formula: 0.18 * ln(total_usd) - 0.37, clamped to [0.3, 2.0].
    This replaces the stepped thresholds for a smoother progression.

    Examples:
        $100  → 0.46
        $500  → 0.75
        $1000 → 0.87
        $5000 → 1.17
        $10K  → 1.29
        $50K  → 1.57
        $100K → 1.70
    """
    if total_amount <= 0:
        return 0.3
    raw = 0.18 * math.log(total_amount) - 0.37
    return round(max(0.3, min(2.0, raw)), 2)


def _get_diversity_multiplier(wallet_market_count: int | None) -> float:
    """Get the sniper/shotgun diversity multiplier.

    <=3 markets → sniper (x1.2, focused = higher quality signal)
    >10 markets → shotgun (x0.7, spread too thin)
    >20 markets → super shotgun (x0.5, likely noise)
    """
    if wallet_market_count is None:
        return 1.0
    if wallet_market_count >= config.DIVERSITY_SUPER_SHOTGUN_MIN:
        return config.DIVERSITY_SUPER_SHOTGUN_MULTIPLIER
    if wallet_market_count >= config.DIVERSITY_SHOTGUN_MIN_MARKETS:
        return config.DIVERSITY_SHOTGUN_MULTIPLIER
    if wallet_market_count <= config.DIVERSITY_SNIPER_MAX_MARKETS:
        return config.DIVERSITY_SNIPER_MULTIPLIER
    return 1.0


def _score_to_stars(score: int) -> int:
    """Map a final score to a star level (0-5) using new thresholds."""
    for threshold, stars in config.NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0


def _validate_stars(
    star_level: int,
    categories: set[str],
    total_amount: float,
    filters: list[FilterResult],
) -> int:
    """Validate and potentially downgrade star level.

    Stars 3-5 have requirements that must be met. If not, the star
    is downgraded until the requirements are satisfied (or reaches 2).
    """
    while star_level >= 3:
        reqs = config.STAR_VALIDATION.get(star_level)
        if reqs is None:
            break

        # Check min_categories
        min_cats = reqs.get("min_categories", 0)
        if len(categories) < min_cats:
            star_level -= 1
            continue

        # Check min_amount
        min_amount = reqs.get("min_amount", 0)
        if total_amount < min_amount:
            star_level -= 1
            continue

        # Check requires_coord
        if reqs.get("requires_coord", False):
            has_coord = "COORDINATION" in categories
            if not has_coord:
                star_level -= 1
                continue

        # All requirements met
        break

    return star_level
