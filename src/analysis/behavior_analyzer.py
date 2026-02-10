"""
Behavior Analyzer — B filters.

Evaluates behavior-level filters:
  B01: Drip accumulation (5+ buys in 24-72h)
  B05: Market orders only
  B06: Increasing trade sizes
  B07: Buying against market (odds < 0.20)
  B14: First big buy (> $5k)
  B16: Rapid accumulation (3+ in < 4h)
  B17: Low activity hours (2-6 AM UTC)
  B18a-d: Progressive accumulation tiers (mutually exclusive)
  B18e: No price impact bonus
  B19a-c: Whale entry tiers (mutually exclusive, Telegram only)
  B20: Old wallet, new in Polymarket
"""

import logging
from datetime import datetime, timedelta, timezone

from src import config
from src.database.models import (
    Wallet,
    AccumulationWindow,
    TradeEvent,
    FilterResult,
)

logger = logging.getLogger(__name__)


class BehaviorAnalyzer:
    """Evaluates behavior filters on a wallet's trading activity."""

    def analyze(
        self,
        wallet: Wallet,
        accumulation: AccumulationWindow,
        odds_move: float | None = None,
    ) -> list[FilterResult]:
        """Run all B filters. Returns triggered filters."""
        results: list[FilterResult] = []
        trades = accumulation.trades

        results.extend(self._check_drip(trades))
        results.extend(self._check_market_orders(trades))
        results.extend(self._check_increasing_size(trades))
        results.extend(self._check_against_market(trades))
        results.extend(self._check_first_big_buy(trades))
        results.extend(self._check_rapid_accumulation(trades))
        results.extend(self._check_low_hours(trades))
        results.extend(self._check_accumulation_tiers(accumulation, odds_move))
        results.extend(self._check_whale_entry(trades))
        results.extend(self._check_old_wallet_new_pm(wallet))

        return results

    def _check_drip(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B01 — 5+ buys in 24-72h window."""
        # TODO: Implement
        raise NotImplementedError

    def _check_market_orders(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B05 — All trades are market orders (no limit orders)."""
        if not trades:
            return []
        if all(t.is_market_order for t in trades):
            return [FilterResult(**config.FILTER_B05)]
        return []

    def _check_increasing_size(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B06 — Each buy is larger than the previous one."""
        if len(trades) < 2:
            return []
        sorted_trades = sorted(trades, key=lambda t: t.timestamp)
        if all(
            sorted_trades[i].amount < sorted_trades[i + 1].amount
            for i in range(len(sorted_trades) - 1)
        ):
            return [FilterResult(**config.FILTER_B06)]
        return []

    def _check_against_market(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B07 — Buying at odds < 0.20."""
        for t in trades:
            if t.price < config.AGAINST_MARKET_ODDS:
                return [FilterResult(**config.FILTER_B07)]
        return []

    def _check_first_big_buy(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B14 — First buy is > $5,000."""
        if not trades:
            return []
        first = min(trades, key=lambda t: t.timestamp)
        if first.amount >= config.FIRST_BIG_BUY_AMOUNT:
            return [FilterResult(**config.FILTER_B14)]
        return []

    def _check_rapid_accumulation(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B16 — 3+ trades within 4 hours."""
        # TODO: Implement sliding window check
        raise NotImplementedError

    def _check_low_hours(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B17 — Trading during 2-6 AM UTC."""
        for t in trades:
            hour = t.timestamp.hour
            if config.LOW_ACTIVITY_HOUR_START <= hour < config.LOW_ACTIVITY_HOUR_END:
                return [FilterResult(**config.FILTER_B17)]
        return []

    def _check_accumulation_tiers(
        self, accum: AccumulationWindow, odds_move: float | None
    ) -> list[FilterResult]:
        """B18a-d (mutually exclusive) + B18e bonus."""
        results: list[FilterResult] = []
        amount = accum.total_amount

        # Mutually exclusive tiers — pick the highest applicable
        if amount >= config.ACCUM_VERY_STRONG_MIN:
            results.append(FilterResult(**config.FILTER_B18D))
        elif amount >= config.ACCUM_STRONG_MIN:
            results.append(FilterResult(**config.FILTER_B18C))
        elif amount >= config.ACCUM_SIGNIFICANT_MIN:
            results.append(FilterResult(**config.FILTER_B18B))
        elif amount >= config.ACCUM_MODERATE_MIN:
            results.append(FilterResult(**config.FILTER_B18A))

        # B18e bonus: accumulation > $2k with < 5% price move
        if (
            amount >= config.ACCUM_NO_IMPACT_MIN
            and odds_move is not None
            and abs(odds_move) < config.ACCUM_NO_IMPACT_MAX_MOVE
        ):
            results.append(FilterResult(**config.FILTER_B18E))

        return results

    def _check_whale_entry(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B19a-c — Large single-tx entries (Telegram only). Mutually exclusive."""
        max_single = max((t.amount for t in trades), default=0)
        if max_single >= config.WHALE_MASSIVE_MIN:
            return [FilterResult(**config.FILTER_B19C)]
        if max_single >= config.WHALE_VERY_LARGE_MIN:
            return [FilterResult(**config.FILTER_B19B)]
        if max_single >= config.WHALE_LARGE_MIN:
            return [FilterResult(**config.FILTER_B19A)]
        return []

    def _check_old_wallet_new_pm(self, wallet: Wallet) -> list[FilterResult]:
        """B20 — Wallet > 180 days old but Polymarket activity < 7 days."""
        # TODO: Implement with wallet age + PM first activity check
        raise NotImplementedError
