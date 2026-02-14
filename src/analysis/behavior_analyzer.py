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
  B19a-c: Whale entry tiers (mutually exclusive, whale_entry alert)
  B20: Old wallet, new in Polymarket
"""

import logging
from datetime import datetime, timedelta, timezone

from dateutil import parser as dt_parser

from src import config
from src.database.models import (
    Wallet,
    AccumulationWindow,
    TradeEvent,
    FilterResult,
)

logger = logging.getLogger(__name__)


def ensure_datetime(val) -> datetime:
    """Convert a value to a timezone-aware UTC datetime.

    Handles: datetime (naive → UTC), str (parse → UTC), passthrough for aware.
    """
    if isinstance(val, str):
        val = dt_parser.parse(val)
    if not isinstance(val, datetime):
        raise TypeError(f"Cannot convert {type(val)} to datetime")
    if val.tzinfo is None:
        val = val.replace(tzinfo=timezone.utc)
    return val


def _fr(filt: dict, details: str | None = None) -> FilterResult:
    """Build a FilterResult from a config filter dict."""
    return FilterResult(
        filter_id=filt["id"],
        filter_name=filt["name"],
        points=filt["points"],
        category=filt["category"],
        details=details,
    )


class BehaviorAnalyzer:
    """Evaluates behavior filters on a wallet's trading activity."""

    def __init__(self, db_client=None) -> None:
        self.db = db_client

    # ── Main entry point ─────────────────────────────────────

    def analyze(
        self,
        wallet_address: str,
        trades: list[TradeEvent],
        market_id: str,
        current_odds: float | None = None,
        wallet_balance: float | None = None,
    ) -> list[FilterResult]:
        """Run all B filters for a wallet's trades in a specific market.

        Args:
            wallet_address: Polygon address being analyzed.
            trades: All recent trades (may include other wallets/markets).
            market_id: The market to focus on.
            current_odds: Current YES price for odds-move calculation.
            wallet_balance: USDC balance of the wallet (for B23).

        Returns:
            List of triggered FilterResult objects.
        """
        # Filter to relevant trades for this wallet + market
        relevant = [
            t for t in trades
            if t.wallet_address == wallet_address and t.market_id == market_id
        ]
        if not relevant:
            return []

        relevant.sort(key=lambda t: t.timestamp)

        # Build accumulation window from trades
        accum = self._build_accumulation(relevant)

        # Calculate odds move for B18e (first trade price → current price)
        odds_move = None
        if current_odds is not None and relevant:
            odds_move = current_odds - relevant[0].price

        # Fetch wallet from DB for B20
        wallet = self._get_wallet(wallet_address)

        results: list[FilterResult] = []
        results.extend(self._check_drip(relevant))
        results.extend(self._check_market_orders(relevant))
        results.extend(self._check_increasing_size(relevant))
        results.extend(self._check_against_market(relevant))
        results.extend(self._check_first_big_buy(relevant))
        results.extend(self._check_rapid_accumulation(relevant))
        results.extend(self._check_low_hours(relevant))
        results.extend(self._check_accumulation_tiers(accum, odds_move))
        results.extend(self._check_whale_entry(relevant))
        results.extend(self._check_position_sizing(accum, wallet_balance))
        if wallet is not None:
            results.extend(self._check_old_wallet_new_pm(wallet))

        return results

    # ── Helpers ───────────────────────────────────────────────

    def _build_accumulation(self, trades: list[TradeEvent]) -> AccumulationWindow:
        """Build an AccumulationWindow from a sorted list of trades."""
        total = sum(t.amount for t in trades)
        direction = trades[0].direction if trades else "YES"
        return AccumulationWindow(
            wallet_address=trades[0].wallet_address,
            market_id=trades[0].market_id,
            direction=direction,
            total_amount=total,
            trade_count=len(trades),
            first_trade=trades[0].timestamp,
            last_trade=trades[-1].timestamp,
            trades=trades,
        )

    def _get_wallet(self, wallet_address: str) -> Wallet | None:
        """Look up wallet from DB for B20 check."""
        if self.db is None:
            return None
        try:
            data = self.db.get_wallet(wallet_address)
            if data is None:
                return None
            if isinstance(data, Wallet):
                return data
            if isinstance(data, dict):
                # Remove keys that don't match Wallet fields gracefully
                return Wallet(**{
                    k: v for k, v in data.items()
                    if k in Wallet.__dataclass_fields__
                })
        except Exception as e:
            logger.debug("get_wallet failed for %s: %s", wallet_address, e)
        return None

    # ── B01 — Drip accumulation (5+ buys in 24-72h) ─────────

    def _check_drip(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B01 — 5+ buys spread across a 24-72h window."""
        if len(trades) < config.DRIP_MIN_BUYS:
            return []

        sorted_trades = sorted(trades, key=lambda t: t.timestamp)
        window_72h = timedelta(hours=config.ACCUMULATION_WINDOW_HOURS)
        window_24h = timedelta(hours=24)

        for i, anchor in enumerate(sorted_trades):
            # Collect trades within 72h after this anchor
            window_trades = [
                t for t in sorted_trades[i:]
                if t.timestamp - anchor.timestamp <= window_72h
            ]
            if len(window_trades) >= config.DRIP_MIN_BUYS:
                spread = window_trades[-1].timestamp - window_trades[0].timestamp
                # Must span at least 24h (drip, not burst)
                if spread >= window_24h:
                    return [_fr(
                        config.FILTER_B01,
                        f"{len(window_trades)} buys over {spread.total_seconds()/3600:.0f}h",
                    )]

        return []

    # ── B05 — Market orders only ─────────────────────────────

    def _check_market_orders(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B05 — All trades are market orders (no limit orders)."""
        if not trades:
            return []
        if all(t.is_market_order for t in trades):
            return [_fr(config.FILTER_B05)]
        return []

    # ── B06 — Increasing trade sizes ─────────────────────────

    def _check_increasing_size(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B06 — Each buy is larger than the previous one."""
        if len(trades) < 2:
            return []
        sorted_trades = sorted(trades, key=lambda t: t.timestamp)
        if all(
            sorted_trades[i].amount < sorted_trades[i + 1].amount
            for i in range(len(sorted_trades) - 1)
        ):
            return [_fr(config.FILTER_B06)]
        return []

    # ── B07 — Buying against market ──────────────────────────

    def _check_against_market(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B07 — Buying at odds < 0.20."""
        for t in trades:
            if t.price < config.AGAINST_MARKET_ODDS:
                return [_fr(config.FILTER_B07, f"price={t.price:.2f}")]
        return []

    # ── B14 — First big buy ──────────────────────────────────

    def _check_first_big_buy(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B14 — First buy in PM > $5,000."""
        if not trades:
            return []
        first = min(trades, key=lambda t: t.timestamp)
        if first.amount >= config.FIRST_BIG_BUY_AMOUNT:
            return [_fr(config.FILTER_B14, f"first_buy=${first.amount:,.0f}")]
        return []

    # ── B16 — Rapid accumulation ─────────────────────────────

    def _check_rapid_accumulation(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B16 — 3+ trades within 4 hours."""
        if len(trades) < config.RAPID_ACCUMULATION_COUNT:
            return []

        sorted_trades = sorted(trades, key=lambda t: t.timestamp)
        window = timedelta(hours=config.RAPID_ACCUMULATION_HOURS)

        for i in range(len(sorted_trades)):
            count = 1
            for j in range(i + 1, len(sorted_trades)):
                if sorted_trades[j].timestamp - sorted_trades[i].timestamp <= window:
                    count += 1
                else:
                    break
            if count >= config.RAPID_ACCUMULATION_COUNT:
                return [_fr(
                    config.FILTER_B16,
                    f"{count} trades in <{config.RAPID_ACCUMULATION_HOURS}h",
                )]

        return []

    # ── B17 — Low activity hours ─────────────────────────────

    def _check_low_hours(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B17 — Trading during 2-6 AM UTC."""
        for t in trades:
            hour = t.timestamp.hour
            if config.LOW_ACTIVITY_HOUR_START <= hour < config.LOW_ACTIVITY_HOUR_END:
                return [_fr(config.FILTER_B17, f"hour={hour} UTC")]
        return []

    # ── B18a-d + B18e — Accumulation tiers ───────────────────

    def _check_accumulation_tiers(
        self, accum: AccumulationWindow, odds_move: float | None
    ) -> list[FilterResult]:
        """B18a-d (mutually exclusive) + B18e bonus.

        Tiers:
          B18d: $10,000+ in 14 days
          B18c: $5,000-$9,999 in 14 days
          B18b: $3,500-$4,999 in 7 days
          B18a: $2,000-$3,499 in 7 days

        B18e bonus: accumulation > $2k with < 5% price move.
        """
        results: list[FilterResult] = []
        amount = accum.total_amount

        # Mutually exclusive tiers — pick the highest applicable
        if amount >= config.ACCUM_VERY_STRONG_MIN:
            results.append(_fr(config.FILTER_B18D, f"accum=${amount:,.0f}"))
        elif amount >= config.ACCUM_STRONG_MIN:
            results.append(_fr(config.FILTER_B18C, f"accum=${amount:,.0f}"))
        elif amount >= config.ACCUM_SIGNIFICANT_MIN:
            results.append(_fr(config.FILTER_B18B, f"accum=${amount:,.0f}"))
        elif amount >= config.ACCUM_MODERATE_MIN:
            results.append(_fr(config.FILTER_B18A, f"accum=${amount:,.0f}"))

        # B18e bonus: accumulation > $2k with < 5% price move
        if (
            amount >= config.ACCUM_NO_IMPACT_MIN
            and odds_move is not None
            and abs(odds_move) < config.ACCUM_NO_IMPACT_MAX_MOVE
        ):
            results.append(_fr(config.FILTER_B18E, f"odds_move={odds_move:.2%}"))

        return results

    # ── B19a-c — Whale entry ─────────────────────────────────

    def _check_whale_entry(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """B19a-c — Large single-tx entries (whale_entry alert). Mutually exclusive."""
        max_single = max((t.amount for t in trades), default=0)
        if max_single >= config.WHALE_MASSIVE_MIN:
            return [_fr(config.FILTER_B19C, f"single_tx=${max_single:,.0f}")]
        if max_single >= config.WHALE_VERY_LARGE_MIN:
            return [_fr(config.FILTER_B19B, f"single_tx=${max_single:,.0f}")]
        if max_single >= config.WHALE_LARGE_MIN:
            return [_fr(config.FILTER_B19A, f"single_tx=${max_single:,.0f}")]
        return []

    # ── B23 — Position sizing intelligence ───────────────────

    def _check_position_sizing(
        self, accum: AccumulationWindow, wallet_balance: float | None
    ) -> list[FilterResult]:
        """B23a/b — Position is a significant portion of wallet balance.

        B23b (>50%) takes priority over B23a (20-50%). Mutually exclusive.
        Guards: skip when wallet_balance or accum amount < $50 (avoids
        absurd ratios from dust balances or tiny positions).
        """
        if wallet_balance is None or wallet_balance < 50:
            return []
        if accum.total_amount < 50:
            return []

        position_ratio = accum.total_amount / wallet_balance

        # Cap: ratio > 10 (1000%) means wallet_balance is post-trade
        # residual, not representative of total assets. Skip.
        if position_ratio > 10.0:
            return []

        if position_ratio >= config.POSITION_DOMINANT_MIN:
            return [_fr(
                config.FILTER_B23B,
                f"position={position_ratio:.0%} of ${wallet_balance:,.0f}",
            )]
        if position_ratio >= config.POSITION_SIGNIFICANT_MIN:
            return [_fr(
                config.FILTER_B23A,
                f"position={position_ratio:.0%} of ${wallet_balance:,.0f}",
            )]
        return []

    # ── B20 — Old wallet, new in Polymarket ──────────────────

    def _check_old_wallet_new_pm(self, wallet: Wallet) -> list[FilterResult]:
        """B20 — Wallet > 180 days old but first Polymarket activity < 7 days."""
        if wallet.wallet_age_days is None:
            return []
        if wallet.wallet_age_days <= config.OLD_WALLET_MIN_AGE_DAYS:
            return []

        # Use first_seen as proxy for when we first detected PM activity
        now = datetime.now(timezone.utc)
        try:
            first_seen = ensure_datetime(wallet.first_seen)
        except (TypeError, ValueError):
            return []
        pm_days = (now - first_seen).days

        if pm_days < config.OLD_WALLET_PM_MAX_DAYS:
            return [_fr(
                config.FILTER_B20,
                f"wallet_age={wallet.wallet_age_days}d, pm_activity={pm_days}d",
            )]
        return []
