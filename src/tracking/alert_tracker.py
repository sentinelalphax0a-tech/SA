"""
Alert Tracker — Post-alert odds monitoring.

Runs every 6 hours to update odds metrics on pending alerts:
  - odds_max / odds_max_date / days_to_max / potential_return_max
  - odds_min / odds_min_date

Direction-adjusted: YES alerts track YES price, NO alerts track (1 - YES price).
"""

import logging
from datetime import datetime, timezone

from dateutil import parser as dt_parser

logger = logging.getLogger(__name__)


class AlertTracker:
    """Monitors pending alerts and updates their odds metrics."""

    def __init__(self, db, polymarket) -> None:
        self.db = db
        self.pm = polymarket

    def run(self) -> int:
        """Execute the full tracking cycle.

        Returns the number of alerts successfully tracked.
        """
        pending = self.db.get_alerts_pending()
        if not pending:
            logger.info("No pending alerts to track")
            return 0

        tracked = 0
        for alert in pending:
            try:
                if self._track_alert(alert):
                    tracked += 1
            except Exception as e:
                logger.error(
                    "Failed to track alert #%s: %s", alert.get("id"), e,
                )

        logger.info("Tracked %d pending alerts", tracked)
        return tracked

    def _track_alert(self, alert: dict) -> bool:
        """Track a single alert. Returns True if updated."""
        alert_id = alert.get("id")
        market_id = alert.get("market_id")
        direction = alert.get("direction", "YES")
        odds_at_alert = alert.get("odds_at_alert")

        if not market_id:
            return False

        # Get current YES odds from Polymarket
        current_odds = self._get_current_odds(market_id)
        if current_odds is None:
            return False

        # Direction-adjust current odds
        odds_actual = self._direction_adjust(current_odds, direction)

        # Direction-adjust odds_at_alert for potential_return calc
        odds_at_alert_adj = (
            self._direction_adjust(odds_at_alert, direction)
            if odds_at_alert is not None
            else None
        )

        now = datetime.now(timezone.utc)
        updates: dict = {}

        # ── Check odds_max ────────────────────────────────────
        odds_max = alert.get("odds_max")
        if odds_max is None or odds_actual > odds_max:
            updates["odds_max"] = round(odds_actual, 4)
            updates["odds_max_date"] = now.isoformat()

            # days_to_max from alert creation
            alert_ts = self._parse_timestamp(
                alert.get("timestamp") or alert.get("created_at")
            )
            if alert_ts:
                updates["days_to_max"] = (now - alert_ts).days

            # potential_return_max
            if odds_at_alert_adj and odds_at_alert_adj > 0:
                updates["potential_return_max"] = round(
                    ((odds_actual - odds_at_alert_adj) / odds_at_alert_adj)
                    * 100,
                    2,
                )

        # ── Check odds_min ────────────────────────────────────
        odds_min = alert.get("odds_min")
        if odds_min is None or odds_actual < odds_min:
            updates["odds_min"] = round(odds_actual, 4)
            updates["odds_min_date"] = now.isoformat()

        # ── Persist ───────────────────────────────────────────
        if updates:
            self.db.update_alert_fields(alert_id, updates)

        # ── Log ───────────────────────────────────────────────
        question = alert.get("market_question", "") or ""
        q_short = question[:40]
        odds_max_display = updates.get("odds_max") or odds_max or 0
        logger.info(
            "Tracked alert #%s: %s | odds: %.2f → %.2f (max: %.2f)",
            alert_id,
            q_short,
            odds_at_alert_adj or 0,
            odds_actual,
            odds_max_display,
        )

        return True

    def _get_current_odds(self, market_id: str) -> float | None:
        """Get current YES odds for a market from Polymarket."""
        try:
            market = self.pm.get_market_info(market_id)
            if market and market.current_odds is not None:
                return market.current_odds
        except Exception as e:
            logger.debug("Failed to get odds for %s: %s", market_id, e)
        return None

    @staticmethod
    def _direction_adjust(odds: float, direction: str | None) -> float:
        """Adjust odds for direction: YES → odds, NO → 1 - odds."""
        if direction and direction.upper() == "NO":
            return 1.0 - odds
        return odds

    @staticmethod
    def _parse_timestamp(val) -> datetime | None:
        """Parse a timestamp value into a timezone-aware datetime."""
        if val is None:
            return None
        try:
            if isinstance(val, str):
                val = dt_parser.parse(val)
            if isinstance(val, datetime):
                if val.tzinfo is None:
                    val = val.replace(tzinfo=timezone.utc)
                return val
        except (TypeError, ValueError):
            pass
        return None
