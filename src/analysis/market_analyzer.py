"""
Market Analyzer — M filters.

Evaluates market-level conditions:
  M01: Anomalous volume (24h > 2x 7d average)
  M02: Stable odds broken (stable >48h, then move >10%)
  M03: Low liquidity (< $100k)
  M04: Volume concentration (top 3 wallets >60%/>80%)
  M05: Deadline proximity (<72h/<24h/<6h to resolution)

M01 and M02 require historical data. During the first days of operation
(no history yet), these filters gracefully return empty — data accumulates
with each scan via market_snapshots.
"""

import logging
from datetime import datetime, timedelta, timezone

from src import config
from src.database.models import Market, MarketSnapshot, TradeEvent, FilterResult

logger = logging.getLogger(__name__)


def _fr(filt: dict, details: str | None = None) -> FilterResult:
    """Build a FilterResult from a config filter dict."""
    return FilterResult(
        filter_id=filt["id"],
        filter_name=filt["name"],
        points=filt["points"],
        category=filt["category"],
        details=details,
    )


class MarketAnalyzer:
    """Evaluates market-level filters."""

    def __init__(self, db_client=None, polymarket_client=None) -> None:
        self.db = db_client
        self.pm = polymarket_client

    # ── Main entry point ─────────────────────────────────────

    def analyze(
        self,
        market: Market,
        trades: list[TradeEvent] | None = None,
    ) -> list[FilterResult]:
        """Run all M filters on a market.

        Also saves a snapshot of current odds/volume/liquidity
        so future scans have historical data for M01 and M02.

        Args:
            market: Market object with current data.
            trades: Recent trades in this market (for M04 concentration).

        Returns:
            List of triggered FilterResult objects.
        """
        # Save snapshot for future analysis
        self._save_snapshot(market)

        results: list[FilterResult] = []
        results.extend(self._check_volume_anomaly(market))
        results.extend(self._check_odds_stability_break(market))
        results.extend(self._check_low_liquidity(market))
        results.extend(self._check_volume_concentration(trades))
        results.extend(self._check_deadline_proximity(market))
        return results

    # ── Snapshot persistence ─────────────────────────────────

    def _save_snapshot(self, market: Market) -> None:
        """Persist current market state as a snapshot for history."""
        if self.db is None:
            return
        if market.current_odds is None:
            return
        try:
            snapshot = MarketSnapshot(
                market_id=market.market_id,
                odds=market.current_odds,
                volume_24h=market.volume_24h,
                liquidity=market.liquidity,
            )
            self.db.insert_market_snapshot(snapshot)
        except Exception as e:
            logger.debug("insert_market_snapshot failed: %s", e)

    # ── M01 — Anomalous volume ───────────────────────────────

    def _check_volume_anomaly(self, market: Market) -> list[FilterResult]:
        """M01 — 24h volume > 2x 7-day average.

        Uses Market.volume_7d_avg if available (populated by pipeline).
        Falls back to computing avg from snapshots in DB.
        If no historical data exists yet, returns empty.
        """
        vol_24h = market.volume_24h
        avg_7d = market.volume_7d_avg

        # If Market object doesn't have a 7d avg, try computing from snapshots
        if avg_7d <= 0 and self.db is not None:
            avg_7d = self._compute_avg_volume_from_snapshots(market.market_id)

        if avg_7d <= 0:
            return []

        ratio = vol_24h / avg_7d
        if ratio > config.VOLUME_ANOMALY_MULTIPLIER:
            return [_fr(
                config.FILTER_M01,
                f"vol_24h=${vol_24h:,.0f}, avg_7d=${avg_7d:,.0f}, ratio={ratio:.1f}x",
            )]
        return []

    def _compute_avg_volume_from_snapshots(self, market_id: str) -> float:
        """Compute 7-day average daily volume from snapshots."""
        try:
            snapshots = self.db.get_market_snapshots(market_id, hours=168)  # 7 days
        except Exception as e:
            logger.debug("get_market_snapshots failed: %s", e)
            return 0.0

        if not snapshots:
            return 0.0

        # Average the volume_24h values across snapshots
        volumes = [s.get("volume_24h", 0) for s in snapshots if s.get("volume_24h", 0) > 0]
        if not volumes:
            return 0.0

        return sum(volumes) / len(volumes)

    # ── M02 — Stable odds broken ─────────────────────────────

    def _check_odds_stability_break(self, market: Market) -> list[FilterResult]:
        """M02 — Odds stable >48h then sudden move >10%.

        Requires at least 48h of snapshot history. If insufficient
        data exists (early days), returns empty.
        """
        if market.current_odds is None:
            return []

        snapshots = self._get_odds_history(market.market_id)
        if not snapshots:
            return []

        # Need enough snapshots spanning at least 48h
        timestamps = []
        for s in snapshots:
            ts = s.get("timestamp")
            if ts is None:
                continue
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
            timestamps.append(ts)

        if len(timestamps) < 2:
            return []

        # Ensure timezone-aware
        first_ts = timestamps[0]
        last_ts = timestamps[-1]
        if first_ts.tzinfo is None:
            first_ts = first_ts.replace(tzinfo=timezone.utc)
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)

        span_hours = (last_ts - first_ts).total_seconds() / 3600
        if span_hours < config.ODDS_STABLE_HOURS:
            return []

        # Extract odds values
        odds_values = [s.get("odds", 0) for s in snapshots if s.get("odds") is not None]
        if len(odds_values) < 2:
            return []

        # Check stability: all historical odds (except last) are within a tight range
        historical = odds_values[:-1]
        current = market.current_odds

        odds_min = min(historical)
        odds_max = max(historical)

        # "Stable" means the historical range was tight (< threshold)
        historical_range = odds_max - odds_min
        if historical_range > config.ODDS_BREAK_THRESHOLD:
            return []  # wasn't stable

        # Check if current odds broke away from the stable range
        midpoint = (odds_min + odds_max) / 2
        move = abs(current - midpoint)

        if move > config.ODDS_BREAK_THRESHOLD:
            return [_fr(
                config.FILTER_M02,
                f"stable={midpoint:.2f}±{historical_range:.2f}, now={current:.2f}, move={move:.2f}",
            )]

        return []

    def _get_odds_history(self, market_id: str) -> list[dict]:
        """Fetch odds history from DB snapshots."""
        if self.db is None:
            return []
        try:
            return self.db.get_market_snapshots(
                market_id, hours=config.ODDS_STABLE_HOURS + 24
            )
        except Exception as e:
            logger.debug("get_market_snapshots failed: %s", e)
            return []

    # ── M03 — Low liquidity ──────────────────────────────────

    def _check_low_liquidity(self, market: Market) -> list[FilterResult]:
        """M03 — Liquidity below $100k."""
        if 0 < market.liquidity < config.LOW_LIQUIDITY_THRESHOLD:
            return [_fr(
                config.FILTER_M03,
                f"liquidity=${market.liquidity:,.0f}",
            )]
        return []

    # ── M04 — Volume concentration ────────────────────────

    def _check_volume_concentration(
        self, trades: list[TradeEvent] | None
    ) -> list[FilterResult]:
        """M04 — Top 3 wallets account for >60%/>80% of volume.

        Mutually exclusive: M04b (high) takes priority over M04a (moderate).
        """
        if not trades:
            return []

        # Sum volume per wallet
        wallet_volume: dict[str, float] = {}
        for t in trades:
            wallet_volume[t.wallet_address] = (
                wallet_volume.get(t.wallet_address, 0.0) + t.amount
            )

        if not wallet_volume:
            return []

        total_volume = sum(wallet_volume.values())
        if total_volume <= 0:
            return []

        # Top 3 by volume
        top3_volume = sum(
            sorted(wallet_volume.values(), reverse=True)[:3]
        )
        concentration = top3_volume / total_volume

        if concentration >= config.VOLUME_CONCENTRATION_HIGH:
            return [_fr(
                config.FILTER_M04B,
                f"top3={concentration:.0%} of ${total_volume:,.0f}",
            )]
        if concentration >= config.VOLUME_CONCENTRATION_MODERATE:
            return [_fr(
                config.FILTER_M04A,
                f"top3={concentration:.0%} of ${total_volume:,.0f}",
            )]
        return []

    # ── M05 — Deadline proximity ──────────────────────────

    def _check_deadline_proximity(self, market: Market) -> list[FilterResult]:
        """M05 — Market resolves within <6h/<24h/<72h.

        Mutually exclusive: M05c (<6h) > M05b (<24h) > M05a (<72h).
        """
        if market.resolution_date is None:
            return []

        now = datetime.now(timezone.utc)
        res_date = market.resolution_date
        if res_date.tzinfo is None:
            res_date = res_date.replace(tzinfo=timezone.utc)

        hours_left = (res_date - now).total_seconds() / 3600
        if hours_left < 0:
            return []  # already passed

        if hours_left < config.DEADLINE_6H:
            return [_fr(
                config.FILTER_M05C,
                f"resolves in {hours_left:.1f}h",
            )]
        if hours_left < config.DEADLINE_24H:
            return [_fr(
                config.FILTER_M05B,
                f"resolves in {hours_left:.1f}h",
            )]
        if hours_left < config.DEADLINE_72H:
            return [_fr(
                config.FILTER_M05A,
                f"resolves in {hours_left:.1f}h",
            )]
        return []
