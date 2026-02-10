"""
Noise Filter — N filters (negative scoring).

Detects noise and subtracts from score:
  N01: Bot detection (interval std_dev ≈ 0)
  N02: News already public (Google News RSS)
  N04: Opposite market positions (flag only)
  N05: Copy-trading (2-10 min after whale)
  N06a-c: Degen tiers (non-political market activity)
"""

import logging
import statistics
from datetime import timedelta

from src import config
from src.database.models import Wallet, TradeEvent, FilterResult
from src.scanner.news_checker import NewsChecker

logger = logging.getLogger(__name__)


class NoiseFilter:
    """Applies negative filters to reduce false positives."""

    def __init__(self, news_checker: NewsChecker) -> None:
        self.news = news_checker

    def analyze(
        self,
        wallet: Wallet,
        trades: list[TradeEvent],
        market_question: str | None = None,
    ) -> list[FilterResult]:
        """Run all N filters (except N03 arbitrage). Returns triggered filters."""
        results: list[FilterResult] = []
        results.extend(self._check_bot(trades))
        results.extend(self._check_news(market_question))
        results.extend(self._check_opposite_markets(wallet))
        results.extend(self._check_copy_trading(trades))
        results.extend(self._check_degen(wallet))
        return results

    def _check_bot(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """N01 — Bot-like regular intervals (std_dev of intervals ≈ 0)."""
        if len(trades) < 3:
            return []
        sorted_trades = sorted(trades, key=lambda t: t.timestamp)
        intervals = [
            (sorted_trades[i + 1].timestamp - sorted_trades[i].timestamp).total_seconds()
            for i in range(len(sorted_trades) - 1)
        ]
        if len(intervals) >= 2:
            std = statistics.stdev(intervals)
            if std < config.BOT_INTERVAL_STD_THRESHOLD:
                return [FilterResult(**config.FILTER_N01)]
        return []

    def _check_news(self, market_question: str | None) -> list[FilterResult]:
        """N02 — Recent public news about the market topic."""
        if not market_question:
            return []
        has_news, summary = self.news.has_recent_news(market_question)
        if has_news:
            result = FilterResult(**config.FILTER_N02, details=summary)
            return [result]
        return []

    def _check_opposite_markets(self, wallet: Wallet) -> list[FilterResult]:
        """N04 — Wallet holds positions in opposite markets (flag, 0 pts)."""
        # TODO: Check for inverse positions across linked markets
        raise NotImplementedError

    def _check_copy_trading(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """N05 — Trade follows a known whale by 2-10 minutes."""
        # TODO: Implement whale trade comparison
        raise NotImplementedError

    def _check_degen(self, wallet: Wallet) -> list[FilterResult]:
        """N06a/b/c — Non-political market activity indicates degen behavior."""
        non_pm = wallet.non_pm_markets
        if non_pm >= config.DEGEN_HEAVY_MIN:
            return [FilterResult(**config.FILTER_N06C)]
        if non_pm > config.DEGEN_LIGHT_MAX:
            return [FilterResult(**config.FILTER_N06B)]
        if non_pm >= 1:
            return [FilterResult(**config.FILTER_N06A)]
        return []
