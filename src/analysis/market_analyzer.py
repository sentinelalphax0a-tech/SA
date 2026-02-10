"""
Market Analyzer — M filters.

Evaluates market-level conditions:
  M01: Anomalous volume (24h > 2x 7d average)
  M02: Stable odds broken (stable >48h, then move >10%)
  M03: Low liquidity (< $100k)
"""

import logging

from src import config
from src.database.models import Market, FilterResult

logger = logging.getLogger(__name__)


class MarketAnalyzer:
    """Evaluates market-level filters."""

    def analyze(self, market: Market) -> list[FilterResult]:
        """Run all M filters on a market. Returns triggered filters."""
        results: list[FilterResult] = []
        results.extend(self._check_volume_anomaly(market))
        results.extend(self._check_odds_stability_break(market))
        results.extend(self._check_low_liquidity(market))
        return results

    def _check_volume_anomaly(self, market: Market) -> list[FilterResult]:
        """M01 — 24h volume > 2x 7-day average."""
        if market.volume_7d_avg > 0 and (
            market.volume_24h > config.VOLUME_ANOMALY_MULTIPLIER * market.volume_7d_avg
        ):
            return [FilterResult(**config.FILTER_M01)]
        return []

    def _check_odds_stability_break(self, market: Market) -> list[FilterResult]:
        """M02 — Odds stable >48h then sudden move >10%."""
        # TODO: Requires odds history analysis
        raise NotImplementedError

    def _check_low_liquidity(self, market: Market) -> list[FilterResult]:
        """M03 — Liquidity below $100k."""
        if 0 < market.liquidity < config.LOW_LIQUIDITY_THRESHOLD:
            return [FilterResult(**config.FILTER_M03)]
        return []
