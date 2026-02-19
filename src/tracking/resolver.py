"""
Market Resolver — Checks resolved markets and scores alerts.

Runs periodically to:
  1. Find pending alerts whose markets have resolved.
  2. Compare alert direction vs market outcome (correct/incorrect).
  3. Calculate actual_return, odds_at_resolution, time_to_resolution_days.
  4. Update wallet win/loss stats.
"""

import logging
from datetime import datetime, timezone

from dateutil import parser as dt_parser

logger = logging.getLogger(__name__)


class MarketResolver:
    """Resolves pending alerts by checking market outcomes."""

    def __init__(self, db, polymarket) -> None:
        self.db = db
        self.pm = polymarket

    def run(self) -> dict:
        """Execute the full resolution cycle.

        Returns dict with counts: {"resolved", "correct", "incorrect"}.
        """
        # 1. Get pending alerts
        pending = self.db.get_alerts_pending()
        if not pending:
            logger.info("No pending alerts to resolve")
            return {"resolved": 0, "correct": 0, "incorrect": 0}

        # 2. Extract unique market_ids
        market_ids = {a["market_id"] for a in pending if a.get("market_id")}

        # 3. Check resolution for each market
        resolved_markets: dict[str, str] = {}
        for mid in market_ids:
            try:
                resolution = self.pm.get_market_resolution(mid)
                if not resolution:
                    continue
                if not resolution.get("resolved"):
                    continue
                outcome = resolution.get("outcome")
                if not outcome:
                    continue

                resolved_markets[mid] = outcome

                # Update markets table
                self.db.update_market_resolution(mid, outcome)
            except Exception as e:
                logger.error("Failed to check resolution for %s: %s", mid, e)

        if not resolved_markets:
            logger.info("No markets resolved this cycle — trying price-based fallback")
        else:
            pass  # continue to step 4

        # 4. Resolve matching alerts (API-confirmed)
        n_correct = 0
        n_incorrect = 0

        for alert in pending:
            mid = alert.get("market_id")
            if mid not in resolved_markets:
                continue

            try:
                is_correct = self._resolve_alert(alert, resolved_markets[mid])
                if is_correct:
                    n_correct += 1
                else:
                    n_incorrect += 1
            except Exception as e:
                logger.error(
                    "Failed to resolve alert #%s: %s", alert.get("id"), e,
                )

        # 5. Price-based fallback: resolve remaining pending alerts where
        #    odds_max=1.0 or odds_min=1.0 (direction-adjusted price hit
        #    certainty). These are markets that resolved but Polymarket API
        #    still returns active=True, so the normal resolver skips them.
        n_price = self._resolve_by_price()

        n_total = n_correct + n_incorrect + n_price
        logger.info(
            "Resolved %d correct, %d incorrect (API) + %d (price-based) = %d total",
            n_correct, n_incorrect, n_price, n_total,
        )

        return {
            "resolved": n_total,
            "correct": n_correct + n_price,  # price-based are always correct
            "incorrect": n_incorrect,
        }

    def _resolve_alert(
        self,
        alert: dict,
        market_outcome: str,
        resolved_at: datetime | None = None,
    ) -> bool:
        """Resolve a single alert. Returns True if correct.

        Args:
            alert: Alert dict from DB.
            market_outcome: "YES" or "NO" — the market's winning outcome.
            resolved_at: Override resolved_at timestamp (e.g. odds_max_date
                for price-based resolution). Defaults to now.
        """
        alert_id = alert.get("id")
        direction = (alert.get("direction") or "YES").upper()
        outcome_upper = market_outcome.upper()

        # a/b/c. Compare direction with market outcome
        is_correct = direction == outcome_upper
        alert_outcome = "correct" if is_correct else "incorrect"

        # d. Calculate actual_return (direction-adjusted)
        odds_at_alert = alert.get("odds_at_alert") or 0
        odds_adj = self._direction_adjust(odds_at_alert, direction)

        if is_correct and odds_adj > 0:
            actual_return = round(((1.0 - odds_adj) / odds_adj) * 100, 2)
        else:
            actual_return = -100.0

        # e. odds_at_resolution
        odds_at_resolution = 1.0 if is_correct else 0.0

        # f. time_to_resolution_days
        now = resolved_at or datetime.now(timezone.utc)
        alert_ts = self._parse_timestamp(
            alert.get("timestamp") or alert.get("created_at")
        )
        time_to_resolution = (now - alert_ts).days if alert_ts else None

        # g. Update alert in Supabase
        fields: dict = {
            "outcome": alert_outcome,
            "resolved_at": now.isoformat(),
            "odds_at_resolution": odds_at_resolution,
            "actual_return": actual_return,
        }
        if time_to_resolution is not None:
            fields["time_to_resolution_days"] = time_to_resolution

        self.db.update_alert_fields(alert_id, fields)

        # h. Update wallet stats
        wallets = alert.get("wallets") or []
        for w in wallets:
            addr = w.get("address")
            if addr:
                try:
                    self.db.update_wallet_stats(addr, won=is_correct)
                except Exception as e:
                    logger.debug("Failed to update wallet stats for %s: %s", addr, e)

        # i. Log
        q_short = (alert.get("market_question") or "")[:40]
        logger.info(
            "Resolved alert #%s: %s | %s", alert_id, alert_outcome, q_short,
        )

        return is_correct

    def _resolve_by_price(self) -> int:
        """Price-based fallback: resolve pending alerts where odds_max=1.0
        or odds_min=1.0 (direction-adjusted price reached certainty).

        When the direction-adjusted price of a token hits 1.0, the market
        has resolved in favour of that direction with absolute certainty.
        The normal resolver skips these because Polymarket API sometimes
        still returns active=True for officially unprocessed markets.

        Only fires after the API-based pass so it only touches alerts that
        the normal resolver didn't handle.

        Returns the number of alerts resolved.
        """
        try:
            candidates = (
                self.db.client.table("alerts")
                .select(
                    "id,direction,odds_at_alert,odds_max,odds_max_date,"
                    "odds_min,odds_min_date,wallets,market_question,"
                    "created_at,timestamp"
                )
                .eq("outcome", "pending")
                .or_("odds_max.eq.1,odds_min.eq.1")
                .execute()
                .data
            ) or []
        except Exception as e:
            logger.warning("Price-based resolution query failed: %s", e)
            return 0

        if not candidates:
            return 0

        logger.info(
            "[price-resolver] %d pending alerts with odds=1.0 found", len(candidates)
        )

        resolved = 0
        for alert in candidates:
            try:
                direction = (alert.get("direction") or "YES").upper()

                # Determine resolved_at: prefer the date when odds first hit 1.0
                if alert.get("odds_max") == 1.0 and alert.get("odds_max_date"):
                    resolved_at = self._parse_timestamp(alert["odds_max_date"])
                elif alert.get("odds_min") == 1.0 and alert.get("odds_min_date"):
                    resolved_at = self._parse_timestamp(alert["odds_min_date"])
                else:
                    resolved_at = None

                # market_outcome == direction because direction-adjusted odds
                # reached 1.0 → direction won
                self._resolve_alert(alert, direction, resolved_at=resolved_at)
                resolved += 1
                logger.info(
                    "[price-resolver] Resolved alert #%s as correct"
                    " (direction=%s, odds_max=%.4f)",
                    alert["id"],
                    direction,
                    alert.get("odds_max") or 0,
                )
            except Exception as e:
                logger.error(
                    "[price-resolver] Failed for alert #%s: %s", alert.get("id"), e
                )

        return resolved

    @staticmethod
    def _direction_adjust(odds: float, direction: str) -> float:
        """Adjust odds for direction: YES → odds, NO → 1 - odds."""
        if direction.upper() == "NO":
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
