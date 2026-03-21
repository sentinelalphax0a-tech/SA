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

        Architecture:
        1. get_pending_market_ids() — lightweight paginated query that
           returns only the market_id column, so the resolver knows which
           markets to check against the CLOB API even when total pending
           alerts exceed the PostgREST 1000-row cap.
        2. For each resolved market, get_pending_alerts_for_market() fetches
           that market's pending alerts specifically.  A single market never
           has more than a few dozen pending alerts, so this is always safe
           without pagination.
        3. Each alert is resolved individually (outcome/return vary by
           direction and odds_at_alert, so per-alert logic is required).

        This replaces the old approach of fetching ALL pending alerts in one
        call and iterating — which was silently capped at 1000 rows and caused
        2172 alerts (68% of pending) to be invisible to the resolver.
        """
        # 1. Get distinct market_ids that have pending alerts (paginated, lightweight)
        pending_market_ids = self.db.get_pending_market_ids()
        if not pending_market_ids:
            logger.info("No pending alerts to resolve")
            return {"resolved": 0, "correct": 0, "incorrect": 0}

        logger.info("Found %d distinct markets with pending alerts", len(pending_market_ids))

        # 2. Check resolution for each market via CLOB API
        resolved_markets: dict[str, str] = {}
        for mid in pending_market_ids:
            try:
                resolution = self.pm.get_market_resolution(mid)
                if not resolution:
                    continue
                if not resolution.get("resolved"):
                    continue
                outcome = resolution.get("outcome")
                if not outcome:
                    continue
                if outcome not in ("YES", "NO"):
                    logger.warning(
                        "Market %s has non-standard outcome '%s' — resolving as-is",
                        mid, outcome,
                    )

                resolved_markets[mid] = outcome

                # Update markets table
                self.db.update_market_resolution(mid, outcome)
            except Exception as e:
                logger.error("Failed to check resolution for %s: %s", mid, e)

        if not resolved_markets:
            logger.info("No markets resolved this cycle")
            return {"resolved": 0, "correct": 0, "incorrect": 0}

        logger.info("%d markets resolved this cycle", len(resolved_markets))

        # 3. For each resolved market, fetch its pending alerts and resolve them.
        #    Using a per-market targeted query guarantees ALL pending alerts for
        #    that market are resolved — not just the ones that happened to land
        #    within the first 1000 rows of a full-table scan.
        n_correct = 0
        n_incorrect = 0

        for mid, market_outcome in resolved_markets.items():
            pending_for_market = self.db.get_pending_alerts_for_market(mid)
            if not pending_for_market:
                continue
            logger.debug(
                "Resolving %d pending alerts for market %s (outcome=%s)",
                len(pending_for_market), mid[:16], market_outcome,
            )
            for alert in pending_for_market:
                try:
                    is_correct = self._resolve_alert(alert, market_outcome)
                    if is_correct:
                        n_correct += 1
                    else:
                        n_incorrect += 1
                except Exception as e:
                    logger.error(
                        "Failed to resolve alert #%s: %s", alert.get("id"), e,
                    )

        n_total = n_correct + n_incorrect
        logger.info(
            "Resolved %d correct, %d incorrect = %d total",
            n_correct, n_incorrect, n_total,
        )

        return {
            "resolved": n_total,
            "correct": n_correct,
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

        # a/b/c. Compare direction with market outcome.
        # For categorical directions (not YES/NO), the CLOB sub-market resolves
        # YES if that category won, NO if it lost.
        if direction not in ("YES", "NO"):
            is_correct = outcome_upper == "YES"
        else:
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

        # g. Last snapshot: freeze odds_max/odds_min with final pre-resolution price
        #    and capture odds_at_resolution_raw (raw YES price, non-binary).
        #    Uses market_snapshots because the CLOB already shows binary odds
        #    by the time the resolver runs.
        odds_at_resolution_raw = None
        peak_updates: dict = {}
        try:
            snapshot = self.db.get_last_snapshot_for_market(alert.get("market_id", ""))
            if snapshot:
                raw_yes_price = snapshot.get("odds")
                if raw_yes_price is not None:
                    odds_at_resolution_raw = raw_yes_price
                    odds_actual = self._direction_adjust(raw_yes_price, direction)

                    odds_max = alert.get("odds_max")
                    if odds_max is None or odds_actual > odds_max:
                        peak_updates["odds_max"] = round(odds_actual, 4)
                        peak_updates["odds_max_date"] = now.isoformat()

                    odds_min = alert.get("odds_min")
                    if odds_min is None or odds_actual < odds_min:
                        peak_updates["odds_min"] = round(odds_actual, 4)
                        peak_updates["odds_min_date"] = now.isoformat()
        except Exception as e:
            logger.warning(
                "Could not fetch last snapshot for alert #%s: %s", alert_id, e
            )

        # h. Calculate realized_return: CLOB-weighted PnL for sold portion + market outcome for unsold
        try:
            sell_events = self.db.get_sell_events_for_alert(alert_id)
            total_sold_pct = alert.get("total_sold_pct") or 0.0
            realized_return = self._calc_realized_return(actual_return, total_sold_pct, sell_events)
        except Exception as e:
            logger.warning("Could not calculate realized_return for alert #%s: %s", alert_id, e)
            realized_return = actual_return

        # i. Update alert in Supabase
        fields: dict = {
            "outcome": alert_outcome,
            "resolved_at": now.isoformat(),
            "odds_at_resolution": odds_at_resolution,
            "odds_at_resolution_raw": odds_at_resolution_raw,
            "actual_return": actual_return,
            "realized_return": realized_return,
            **peak_updates,
        }
        if time_to_resolution is not None:
            fields["time_to_resolution_days"] = time_to_resolution

        self.db.update_alert_fields(alert_id, fields)

        # j. Update wallet stats
        wallets = alert.get("wallets") or []
        for w in wallets:
            addr = w.get("address")
            if addr:
                try:
                    self.db.update_wallet_stats(addr, won=is_correct)
                except Exception as e:
                    logger.debug("Failed to update wallet stats for %s: %s", addr, e)

        # k. Log
        q_short = (alert.get("market_question") or "")[:40]
        logger.info(
            "Resolved alert #%s: %s | %s", alert_id, alert_outcome, q_short,
        )

        return is_correct

    @staticmethod
    def _calc_realized_return(
        actual_return: float,
        total_sold_pct: float,
        sell_events: list[dict],
    ) -> float:
        """Calculate realized return combining CLOB sell PnL with market outcome.

        For the sold portion: weighted average pnl_pct from sell events.
        For the unsold portion (1 - total_sold_pct): actual_return (market outcome).

        Formula:
            weighted_pnl_sold = sum(sell_pct_i * pnl_pct_i) / sum(sell_pct_i)
            realized_return = sold_frac * weighted_pnl_sold + unsold_frac * actual_return

        Falls back to actual_return if no sell events or no pnl_pct data.
        """
        if not sell_events or total_sold_pct <= 0:
            return actual_return

        valid = [
            (se.get("sell_pct") or 0.0, se["pnl_pct"])
            for se in sell_events
            if se.get("pnl_pct") is not None and (se.get("sell_pct") or 0) > 0
        ]
        if not valid:
            return actual_return

        total_wt = sum(sp for sp, _ in valid)
        if total_wt <= 0:
            return actual_return

        weighted_pnl_sold = sum(sp * pp for sp, pp in valid) / total_wt

        sold_frac = min(1.0, total_sold_pct)
        unsold_frac = max(0.0, 1.0 - sold_frac)

        return round(sold_frac * weighted_pnl_sold + unsold_frac * actual_return, 2)

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
