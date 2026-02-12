"""
Noise Filter — N filters (negative scoring).

Detects noise and subtracts from score:
  N01: Bot detection (interval std_dev < 1s)
  N02: News already public (Google News RSS)
  N05: Copy-trading (2-10 min after whale)
  N06a-c: Degen tiers (non-political market activity, mutually exclusive)
  N07a-b: Scalper/arb rápido (buy+sell <2h, mutually exclusive)
  N08: Anti-bot evasion (irregular timing but uniform amounts)
"""

import logging
import statistics
from datetime import timedelta

from src import config
from src.database.models import Wallet, TradeEvent, FilterResult

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


class NoiseFilter:
    """Applies negative filters to reduce false positives."""

    def __init__(self, news_checker=None, db_client=None) -> None:
        self.news = news_checker
        self.db = db_client

    # ── Main entry point ─────────────────────────────────────

    def analyze(
        self,
        wallet: Wallet,
        trades: list[TradeEvent],
        market_question: str | None = None,
        whale_trades: list[TradeEvent] | None = None,
        all_wallet_trades: list[TradeEvent] | None = None,
    ) -> list[FilterResult]:
        """Run all N filters (except N03 arbitrage).

        Args:
            wallet: Wallet being evaluated.
            trades: This wallet's trades in the current market.
            market_question: Market question text for news lookup.
            whale_trades: Recent trades by known whales in this market
                          (for copy-trading detection).
            all_wallet_trades: All trades by this wallet across all markets
                               (for N07 scalper detection).

        Returns:
            List of triggered FilterResult objects (all negative points).
        """
        results: list[FilterResult] = []
        bot_triggered = self._check_bot(trades)
        results.extend(bot_triggered)
        results.extend(self._check_news(market_question))
        results.extend(self._check_copy_trading(trades, whale_trades))
        results.extend(self._check_degen(wallet))
        results.extend(self._check_scalper(trades, all_wallet_trades))
        # N08 only fires when N01 (bot) did not
        if not bot_triggered:
            results.extend(self._check_anti_bot_evasion(trades))
        return results

    # ── N01 — Bot detection ──────────────────────────────────

    def _check_bot(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """N01 — Bot-like regular intervals (std_dev of intervals < 1s)."""
        if len(trades) < 3:
            return []

        sorted_trades = sorted(trades, key=lambda t: t.timestamp)
        intervals = [
            (sorted_trades[i + 1].timestamp - sorted_trades[i].timestamp).total_seconds()
            for i in range(len(sorted_trades) - 1)
        ]

        if len(intervals) < 2:
            return []

        std = statistics.stdev(intervals)
        if std < config.BOT_INTERVAL_STD_THRESHOLD:
            mean_interval = statistics.mean(intervals)
            return [_fr(
                config.FILTER_N01,
                f"std={std:.2f}s, mean_interval={mean_interval:.1f}s, n={len(trades)}",
            )]
        return []

    # ── N02 — News already public ────────────────────────────

    def _check_news(self, market_question: str | None) -> list[FilterResult]:
        """N02 — Recent public news about the market topic."""
        if not market_question:
            return []
        if self.news is None:
            return []

        try:
            has_news, summary = self.news.check_news(market_question)
        except Exception as e:
            logger.debug("news check failed: %s", e)
            return []

        if has_news:
            return [_fr(config.FILTER_N02, summary)]
        return []

    # ── N05 — Copy-trading ───────────────────────────────────

    def _check_copy_trading(
        self,
        trades: list[TradeEvent],
        whale_trades: list[TradeEvent] | None = None,
    ) -> list[FilterResult]:
        """N05 — Trade follows a known whale by 2-10 minutes.

        Compares each of the wallet's trades against whale trades
        in the same market and direction. If any trade lands in the
        2-10 minute window after a whale trade, N05 triggers.
        """
        if not trades or not whale_trades:
            return []

        min_delay = timedelta(minutes=config.COPY_TRADE_MIN_DELAY_MIN)
        max_delay = timedelta(minutes=config.COPY_TRADE_MAX_DELAY_MIN)

        # Index whale trades by (market_id, direction) for fast lookup
        whale_by_market: dict[tuple[str, str], list[TradeEvent]] = {}
        for wt in whale_trades:
            key = (wt.market_id, wt.direction)
            whale_by_market.setdefault(key, []).append(wt)

        for trade in trades:
            key = (trade.market_id, trade.direction)
            whales = whale_by_market.get(key)
            if not whales:
                continue

            for wt in whales:
                # Skip if same wallet (whale trading with themselves)
                if wt.wallet_address == trade.wallet_address:
                    continue
                delay = trade.timestamp - wt.timestamp
                if min_delay <= delay <= max_delay:
                    delay_min = delay.total_seconds() / 60
                    return [_fr(
                        config.FILTER_N05,
                        f"copied {wt.wallet_address[:10]}… after {delay_min:.1f}min",
                    )]

        return []

    # ── N06a/b/c — Degen tiers ───────────────────────────────

    def _check_degen(self, wallet: Wallet) -> list[FilterResult]:
        """N06a/b/c — Non-political market activity (mutually exclusive).

        Uses wallet.non_pm_markets:
          N06c: 6+ markets  → -30
          N06b: 3-5 markets → -15
          N06a: 1-2 markets → -5
        """
        non_pm = wallet.non_pm_markets

        if non_pm >= config.DEGEN_HEAVY_MIN:
            return [_fr(config.FILTER_N06C, f"non_pm_markets={non_pm}")]
        if non_pm > config.DEGEN_LIGHT_MAX:
            return [_fr(config.FILTER_N06B, f"non_pm_markets={non_pm}")]
        if non_pm >= 1:
            return [_fr(config.FILTER_N06A, f"non_pm_markets={non_pm}")]
        return []

    # ── N07a/b — Scalper/arb rápido ───────────────────────

    def _check_scalper(
        self,
        trades: list[TradeEvent],
        all_wallet_trades: list[TradeEvent] | None = None,
    ) -> list[FilterResult]:
        """N07a/b — Wallet buys and sells same market within <2h.

        N07a: single market flip → -20
        N07b: flips in 3+ markets → -40 (mutually exclusive, takes priority)
        """
        if not trades or len(trades) < 2:
            return []

        flip_window = timedelta(hours=config.SCALPER_FLIP_HOURS)

        def _has_flip(market_trades: list[TradeEvent]) -> bool:
            """Check if there's a buy-then-sell (or sell-then-buy) within window."""
            sorted_t = sorted(market_trades, key=lambda t: t.timestamp)
            for i, t1 in enumerate(sorted_t):
                for t2 in sorted_t[i + 1:]:
                    if t2.timestamp - t1.timestamp > flip_window:
                        break
                    if t1.direction != t2.direction:
                        return True
            return False

        # Check if there's a flip in the current market
        current_flip = _has_flip(trades)

        # If we have all_wallet_trades, check across markets for serial scalping
        if all_wallet_trades and len(all_wallet_trades) > len(trades):
            markets_with_flips: set[str] = set()
            by_market: dict[str, list[TradeEvent]] = {}
            for t in all_wallet_trades:
                by_market.setdefault(t.market_id, []).append(t)

            for mid, mtrades in by_market.items():
                if len(mtrades) >= 2 and _has_flip(mtrades):
                    markets_with_flips.add(mid)

            if len(markets_with_flips) >= config.SCALPER_SERIAL_MIN_MARKETS:
                return [_fr(
                    config.FILTER_N07B,
                    f"flips in {len(markets_with_flips)} markets",
                )]

        if current_flip:
            return [_fr(config.FILTER_N07A, "buy+sell <2h same market")]

        return []

    # ── N08 — Anti-bot evasion ─────────────────────────────

    def _check_anti_bot_evasion(
        self, trades: list[TradeEvent]
    ) -> list[FilterResult]:
        """N08 — Irregular intervals but very uniform trade amounts.

        Detects wallets that intentionally vary timing to bypass N01 bot
        detection, but still use suspiciously uniform notional amounts.
        Only fires when N01 did NOT fire.
        """
        if len(trades) < 4:
            return []

        amounts = [t.amount for t in trades]
        mean_amt = statistics.mean(amounts)
        if mean_amt <= 0:
            return []

        std_amt = statistics.stdev(amounts)
        cv = std_amt / mean_amt  # coefficient of variation

        if cv < config.ANTI_BOT_AMOUNT_CV_MAX:
            return [_fr(
                config.FILTER_N08,
                f"amount_cv={cv:.3f}, mean=${mean_amt:,.0f}, n={len(trades)}",
            )]
        return []
