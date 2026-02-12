"""
Reversion Checker — B21 retroactive scoring.

Applied during daily resolution checks, NOT during live scanning.

B21 adjusts an alert's score based on price movement after the alert:
  - Price reverted toward alert direction (5-15% move): +10 pts
  - Price strongly reverted (>15% move): +20 pts
  - Price continued moving away (>10% move away): -15 pts
"""

import logging

from src.database.models import FilterResult

logger = logging.getLogger(__name__)

# Reversion thresholds
REVERSION_MODERATE_MIN: float = 0.05   # 5% move toward alert direction
REVERSION_STRONG_MIN: float = 0.15     # 15% strong reversion
AWAY_MIN: float = 0.10                 # 10% move against alert direction

# Points
REVERSION_MODERATE_PTS: int = 10
REVERSION_STRONG_PTS: int = 20
AWAY_PTS: int = -15


def check_reversion(
    direction: str,
    odds_at_alert: float,
    current_odds: float | None,
) -> FilterResult | None:
    """Check if price has reverted toward or away from the alert direction.

    Args:
        direction: Alert direction ("YES" or "NO").
        odds_at_alert: Odds at the time the alert was created.
        current_odds: Current market odds (or odds at resolution).

    Returns:
        FilterResult with B21 scoring, or None if no significant move.
    """
    if current_odds is None or odds_at_alert is None:
        return None

    # For YES direction: price going UP is favorable (reversion toward YES)
    # For NO direction: price going DOWN is favorable (reversion toward NO)
    if direction == "YES":
        move = current_odds - odds_at_alert  # positive = favorable
    else:
        move = odds_at_alert - current_odds  # positive = favorable

    if move >= REVERSION_STRONG_MIN:
        return FilterResult(
            filter_id="B21",
            filter_name="Reversión fuerte",
            points=REVERSION_STRONG_PTS,
            category="behavior",
            details=f"move={move:+.2f} toward {direction}",
        )
    if move >= REVERSION_MODERATE_MIN:
        return FilterResult(
            filter_id="B21",
            filter_name="Reversión moderada",
            points=REVERSION_MODERATE_PTS,
            category="behavior",
            details=f"move={move:+.2f} toward {direction}",
        )
    if move <= -AWAY_MIN:
        return FilterResult(
            filter_id="B21",
            filter_name="Precio en contra",
            points=AWAY_PTS,
            category="behavior",
            details=f"move={move:+.2f} against {direction}",
        )

    return None
