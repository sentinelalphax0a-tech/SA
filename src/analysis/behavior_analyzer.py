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
  B19a-c: Whale entry tiers (mutually exclusive, whale_entry alert)
  B20: Old wallet, new in Polymarket
  B25a-c: Odds conviction (against consensus, mutually exclusive)
  B26a-b: Stealth accumulation (replaces B18e, mutually exclusive)
  B27a-b: Diamond hands — hold without selling (Option A: scan trades + is_market_order)
  B28a-b: All-in — extreme position ratio (mut. excl. with B23)
  B30a-c: First mover — first to buy in direction (24h CLOB lookback, fallback scan)
  N09a-c: Obvious bet — with consensus at elevated odds (negative, excl. with B25)
  N10a-c: Long-horizon discount — market resolution > 30 days (negative)
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

    def __init__(self, db_client=None, pm_client=None) -> None:
        self.db = db_client
        self.pm_client = pm_client

    # ── Main entry point ─────────────────────────────────────

    def analyze(
        self,
        wallet_address: str,
        trades: list[TradeEvent],
        market_id: str,
        current_odds: float | None = None,
        wallet_balance: float | None = None,
        all_trades: list[TradeEvent] | None = None,
        resolution_date: datetime | None = None,
    ) -> list[FilterResult]:
        # NOTE: `trades` (wallet_trades) already contains both YES and NO trades
        # for this wallet because get_recent_trades() fetches the full market and
        # _group_trades_by_wallet() groups without direction filtering.
        # Merge check uses this mixed list directly — zero extra API calls.
        """Run all B and N filters for a wallet's trades in a specific market.

        Args:
            wallet_address: Polygon address being analyzed.
            trades: All recent trades (may include other wallets/markets).
            market_id: The market to focus on.
            current_odds: Current YES price for odds-move calculation.
            wallet_balance: USDC balance of the wallet (for B23/B28).
            all_trades: All trades for the market (all wallets), for B30.
            resolution_date: Market resolution date for N10.

        Returns:
            List of triggered FilterResult objects.
        """
        # Filter to relevant trades for this wallet + market (ALL directions)
        relevant = [
            t for t in trades
            if t.wallet_address == wallet_address and t.market_id == market_id
        ]
        if not relevant:
            return []

        relevant.sort(key=lambda t: t.timestamp)

        # ── N12 — Merge detection (run first, before accumulation filters) ──
        # Must run before _build_accumulation so merge signal is captured even
        # if the accumulation total would be inflated by mixed-direction trades.
        results_pre: list[FilterResult] = self._check_merge(relevant)
        if results_pre:
            # Return early only with N12 — all other filters still run below
            pass  # merge result appended to final results after

        # Build accumulation window from trades
        accum = self._build_accumulation(relevant)

        # Calculate odds move (first trade price → current price)
        odds_move = None
        if current_odds is not None and relevant:
            odds_move = current_odds - relevant[0].price

        # Fetch wallet from DB for B20
        wallet = self._get_wallet(wallet_address)

        results: list[FilterResult] = []
        results.extend(results_pre)  # N12 merge (if detected)
        results.extend(self._check_drip(relevant))
        results.extend(self._check_market_orders(relevant))
        results.extend(self._check_increasing_size(relevant))
        results.extend(self._check_against_market(relevant))
        results.extend(self._check_rapid_accumulation(relevant))
        results.extend(self._check_low_hours(relevant))

        # Ordered: B18 → B19 → B14 (B14 suppressed if B19 fired)
        results.extend(self._check_accumulation_tiers(accum, odds_move))

        b19_results = self._check_whale_entry(relevant)
        results.extend(b19_results)

        results.extend(self._check_first_big_buy(
            relevant, b19_fired=len(b19_results) > 0,
        ))

        # B28 (all-in) evaluated first; if it fires, B23 is suppressed
        b28_results = self._check_all_in(accum, wallet_balance)
        results.extend(b28_results)
        if not b28_results:
            results.extend(self._check_position_sizing(accum, wallet_balance))

        # B25 (odds conviction) and N09 (obvious bet) are mutually exclusive
        b25_results = self._check_odds_conviction(relevant)
        results.extend(b25_results)
        if not b25_results:
            results.extend(self._check_obvious_bet(relevant, current_odds))

        results.extend(self._check_stealth_accumulation(relevant))
        results.extend(self._check_diamond_hands(
            wallet_address, market_id, relevant, current_odds,
        ))
        results.extend(self._check_first_mover(
            wallet_address, market_id, relevant, all_trades,
        ))
        results.extend(self._check_long_horizon(resolution_date))
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
        """B07 — Buying at CLOB price < 0.20 (contrarian bet).

        CLOB returns the price of the token bought:
          YES trade → YES token price
          NO trade  → NO token price
        Low price = contrarian regardless of direction.
        """
        for t in trades:
            if t.price < config.AGAINST_MARKET_ODDS:
                return [_fr(config.FILTER_B07, f"price={t.price:.2f}")]
        return []

    # ── B14 — First big buy ──────────────────────────────────

    def _check_first_big_buy(
        self, trades: list[TradeEvent], b19_fired: bool = False,
    ) -> list[FilterResult]:
        """B14 — First buy in PM > $5,000.

        RULE 3: Suppressed if B19 already fired (redundant signal).
        """
        if b19_fired:
            return []
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

    # ── B18a-d — Accumulation tiers ──────────────────────────

    def _check_accumulation_tiers(
        self, accum: AccumulationWindow, odds_move: float | None
    ) -> list[FilterResult]:
        """B18a-d (mutually exclusive, require ≥2 trades).

        Tiers (≥2 trades required — single buys are not accumulation):
          B18d: $10,000+
          B18c: $5,000-$9,999
          B18b: $3,500-$4,999
          B18a: $2,000-$3,499

        Trade count bonus: 3-4 trades → +5, 5+ trades → +10.
        Note: B18e (no price impact) replaced by B26 (stealth accumulation).
        """
        results: list[FilterResult] = []
        amount = accum.total_amount

        # RULE 1: accumulation requires ≥2 trades
        if accum.trade_count < 2:
            return results

        # RULE 2: trade count bonus
        if accum.trade_count >= 5:
            trade_bonus = 10
        elif accum.trade_count >= 3:
            trade_bonus = 5
        else:
            trade_bonus = 0

        # Mutually exclusive tiers — pick the highest applicable
        detail = f"accum=${amount:,.0f}, trades={accum.trade_count}"
        if amount >= config.ACCUM_VERY_STRONG_MIN:
            fr = _fr(config.FILTER_B18D, detail)
            fr.points += trade_bonus
            results.append(fr)
        elif amount >= config.ACCUM_STRONG_MIN:
            fr = _fr(config.FILTER_B18C, detail)
            fr.points += trade_bonus
            results.append(fr)
        elif amount >= config.ACCUM_SIGNIFICANT_MIN:
            fr = _fr(config.FILTER_B18B, detail)
            fr.points += trade_bonus
            results.append(fr)
        elif amount >= config.ACCUM_MODERATE_MIN:
            fr = _fr(config.FILTER_B18A, detail)
            fr.points += trade_bonus
            results.append(fr)

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

        Suppressed when real PM total_volume >> USDC balance, indicating
        the wallet has significant capital beyond its liquid balance.
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

        result: list[FilterResult] = []
        if position_ratio >= config.POSITION_DOMINANT_MIN:
            result = [_fr(
                config.FILTER_B23B,
                f"position={position_ratio:.0%} of ${wallet_balance:,.0f}",
            )]
        elif position_ratio >= config.POSITION_SIGNIFICANT_MIN:
            result = [_fr(
                config.FILTER_B23A,
                f"position={position_ratio:.0%} of ${wallet_balance:,.0f}",
            )]

        if not result:
            return []

        # Suppress if real PM volume >> USDC balance
        if self.pm_client is not None and wallet_balance > 0:
            try:
                history = self.pm_client.get_wallet_pm_history_cached(accum.wallet_address)
                if history is not None:
                    total_volume = history.get("total_volume", 0)
                    if total_volume > wallet_balance * config.ALLIN_VOLUME_SUPPRESS_RATIO:
                        logger.info(
                            "B23 suppressed for %s: PM volume=$%s >> balance=$%s",
                            accum.wallet_address[:10],
                            f"{total_volume:,.0f}",
                            f"{wallet_balance:,.0f}",
                        )
                        return []
            except Exception as e:
                logger.debug("B23 PM volume check failed for %s: %s", accum.wallet_address[:10], e)

        return result

    # ── B25a-c — Odds conviction ─────────────────────────────

    def _check_odds_conviction(
        self, trades: list[TradeEvent]
    ) -> list[FilterResult]:
        """B25a-c — Conviction scoring for bets against market consensus.

        CLOB returns the price of the token bought:
          YES trade → YES token price
          NO trade  → NO token price
        Low price = contrarian regardless of direction.
          < 0.10 → B25a (extreme conviction)
          < 0.20 → B25b (high conviction)
          < 0.35 → B25c (moderate conviction)
        """
        if not trades:
            return []

        direction = trades[0].direction
        avg_price = sum(t.price for t in trades) / len(trades)

        tag = f"{direction}@{avg_price:.2f}"

        if avg_price < config.CONVICTION_EXTREME_MAX:
            return [_fr(config.FILTER_B25A, tag)]
        if avg_price < config.CONVICTION_HIGH_MAX:
            return [_fr(config.FILTER_B25B, tag)]
        if avg_price < config.CONVICTION_MODERATE_MAX:
            return [_fr(config.FILTER_B25C, tag)]
        return []

    # ── B26a-b — Stealth accumulation ──────────────────────

    def _check_stealth_accumulation(
        self, trades: list[TradeEvent]
    ) -> list[FilterResult]:
        """B26a-b — Gradual accumulation with minimal price impact.

        Replaces B18e (binary) with a tiered system.
        Requires ≥2 trades — single buys use B19 instead.

        Tiers:
          B26a: price_move < 1% AND total > $5k  (+20)
          B26b: price_move < 3% AND total > $3k  (+10)
        """
        if len(trades) < 2:
            return []

        sorted_trades = sorted(trades, key=lambda t: t.timestamp)
        price_move = abs(sorted_trades[-1].price - sorted_trades[0].price)
        total = sum(t.amount for t in trades)

        if price_move < config.STEALTH_WHALE_MOVE and total >= config.STEALTH_WHALE_MIN:
            return [_fr(
                config.FILTER_B26A,
                f"move={price_move:.3f}, total=${total:,.0f}",
            )]
        if price_move < config.STEALTH_LOW_IMPACT_MOVE and total >= config.STEALTH_LOW_IMPACT_MIN:
            return [_fr(
                config.FILTER_B26B,
                f"move={price_move:.3f}, total=${total:,.0f}",
            )]
        return []

    # ── B27a-b — Diamond hands (hold without selling) ────────

    def _check_diamond_hands(
        self,
        wallet_address: str,
        market_id: str,
        trades: list[TradeEvent],
        current_odds: float | None,
    ) -> list[FilterResult]:
        """B27a-b — Wallet held position without selling despite favorable odds.

        Option A: uses scan trades only — no wallet_positions DB query.
          - Primary direction = trades[0].direction.
          - Sell detection: any trade with is_market_order=False (CLOB side=SELL)
            in the primary direction means the wallet has partially or fully exited.
          - Entry time/price = earliest BUY trade in primary direction.
          - Hold duration limited by scan window; fires reliably in deep-scan mode
            (24h lookback). B27a (24-48h) candidates visible within that window.

        Tiers:
          B27b: held 72h+, odds improved >10%  (+20)
          B27a: held 24-48h, odds improved >5%  (+15)
        """
        if not config.ENABLE_B27:
            return []

        if not trades or current_odds is None:
            return []

        direction = trades[0].direction

        # Filter to primary direction
        dir_trades = [t for t in trades if t.direction == direction]
        if not dir_trades:
            return []

        # Any CLOB sell (is_market_order=False ↔ side=SELL) → wallet has exited
        if any(not t.is_market_order for t in dir_trades):
            return []

        # Entry = earliest BUY trade in this direction
        entry = min(dir_trades, key=lambda t: t.timestamp)
        hold_hours = (datetime.now(timezone.utc) - entry.timestamp).total_seconds() / 3600

        # Odds improvement (direction-adjusted):
        #   YES: token price should rise → improvement = current_odds - entry.price
        #   NO:  YES price should fall   → improvement = entry.price - current_odds
        if direction == "YES":
            odds_improvement = current_odds - entry.price
        else:
            odds_improvement = entry.price - current_odds

        # B27b: 72h+, >10% improvement
        if (hold_hours >= config.DIAMOND_HANDS_LONG_MIN_HOURS
                and odds_improvement > config.DIAMOND_HANDS_LONG_ODDS_MOVE):
            return [_fr(
                config.FILTER_B27B,
                f"held {hold_hours:.0f}h, odds +{odds_improvement:.2f}",
            )]

        # B27a: 24-48h, >5% improvement
        if (config.DIAMOND_HANDS_SHORT_MIN_HOURS <= hold_hours
                <= config.DIAMOND_HANDS_SHORT_MAX_HOURS
                and odds_improvement > config.DIAMOND_HANDS_SHORT_ODDS_MOVE):
            return [_fr(
                config.FILTER_B27A,
                f"held {hold_hours:.0f}h, odds +{odds_improvement:.2f}",
            )]

        return []

    # ── B28a-b — All-in (extreme position ratio) ──────────

    def _check_all_in(
        self, accum: AccumulationWindow, wallet_balance: float | None
    ) -> list[FilterResult]:
        """B28a-b — Position is an extreme portion of wallet balance.

        Mutually exclusive with B23 — if B28 fires, B23 is suppressed.
        Uses same guards as B23 (skip dust balances/positions, cap ratio).

        Suppressed when real PM total_volume >> USDC balance, indicating
        the wallet has significant capital beyond its liquid balance.

        Tiers:
          B28a: ratio > 90%  (+25)
          B28b: ratio 70-90% (+20)
        """
        if wallet_balance is None or wallet_balance < 50:
            return []
        if accum.total_amount < 50:
            return []
        if accum.total_amount < config.ALLIN_MIN_AMOUNT:
            return []

        position_ratio = accum.total_amount / wallet_balance

        # Cap: ratio > 10 means balance is post-trade residual
        if position_ratio > 10.0:
            return []

        result: list[FilterResult] = []
        if position_ratio >= config.ALLIN_EXTREME_MIN:
            result = [_fr(
                config.FILTER_B28A,
                f"all-in {position_ratio:.0%} of ${wallet_balance:,.0f}",
            )]
        elif position_ratio >= config.ALLIN_STRONG_MIN:
            result = [_fr(
                config.FILTER_B28B,
                f"all-in {position_ratio:.0%} of ${wallet_balance:,.0f}",
            )]

        if not result:
            return []

        # Suppress if real PM volume >> USDC balance
        if self.pm_client is not None and wallet_balance > 0:
            try:
                history = self.pm_client.get_wallet_pm_history_cached(accum.wallet_address)
                if history is not None:
                    total_volume = history.get("total_volume", 0)
                    if total_volume > wallet_balance * config.ALLIN_VOLUME_SUPPRESS_RATIO:
                        logger.info(
                            "B28 suppressed for %s: PM volume=$%.0f >> balance=$%.0f",
                            accum.wallet_address[:10], total_volume, wallet_balance,
                        )
                        return []
            except Exception as e:
                logger.debug("B28 PM volume check failed for %s: %s", accum.wallet_address[:10], e)

        return result

    # ── B30a-c — First mover ──────────────────────────────

    def _check_first_mover(
        self,
        wallet_address: str,
        market_id: str,
        trades: list[TradeEvent],
        all_trades: list[TradeEvent] | None,
    ) -> list[FilterResult]:
        """B30a-c — Wallet was among the first to buy in this direction.

        Uses 24h lookback via CLOB Data API when pm_client is available.
        Falls back to all_trades (scan window) if the API call fails or
        pm_client is None.

        Tiers:
          B30a: first wallet to buy >$1K in direction  (+20)
          B30b: among first 3                          (+10)
          B30c: among first 5                          (+5)
        """
        if not config.ENABLE_B30:
            return []

        if not trades:
            return []

        direction = trades[0].direction
        wallet_addr_lower = wallet_address.lower()

        # ── Try 24h CLOB fetch; fall back to scan window ──────────────────────
        reference_trades: list[TradeEvent] | None = all_trades
        if self.pm_client is not None:
            try:
                reference_trades = self.pm_client.get_recent_trades(
                    market_id=market_id,
                    minutes=config.FIRST_MOVER_LOOKBACK_HOURS * 60,
                    min_amount=config.FIRST_MOVER_MIN_AMOUNT,
                )
            except Exception as e:
                logger.debug("B30: 24h CLOB fetch failed, using scan window: %s", e)
                reference_trades = all_trades

        if not reference_trades:
            return []

        # Collect trades in same direction with amount >= $1K, chronological
        same_dir_trades = sorted(
            [
                t for t in reference_trades
                if t.market_id == market_id
                and t.direction == direction
                and t.amount >= config.FIRST_MOVER_MIN_AMOUNT
            ],
            key=lambda t: t.timestamp,
        )

        if not same_dir_trades:
            return []

        # Unique wallets in chronological order of first appearance
        seen: set[str] = set()
        ordered_wallets: list[str] = []
        for t in same_dir_trades:
            addr = t.wallet_address.lower()
            if addr not in seen:
                seen.add(addr)
                ordered_wallets.append(addr)

        if wallet_addr_lower not in seen:
            return []

        position = ordered_wallets.index(wallet_addr_lower) + 1  # 1-based

        detail = f"position #{position} of {len(ordered_wallets)} wallets"
        if position == 1:
            return [_fr(config.FILTER_B30A, detail)]
        if position <= 3:
            return [_fr(config.FILTER_B30B, detail)]
        if position <= 5:
            return [_fr(config.FILTER_B30C, detail)]
        return []

    # ── N09a-b — Obvious bet (with consensus at extreme odds) ─

    def _check_obvious_bet(
        self,
        trades: list[TradeEvent],
        current_odds: float | None,
    ) -> list[FilterResult]:
        """N09a-c — Wallet bets WITH the consensus at elevated odds.

        Opposite of B25 (which rewards contrarian bets). If B25 fired,
        N09 must NOT fire — enforced by caller in analyze().

        CLOB returns the price of the token bought:
          YES trade → YES token price
          NO trade  → NO token price
        High price = with consensus = obvious bet.
          > 0.90 → N09a (extreme obvious, -40, max 2★)
          > 0.85 → N09b (obvious, -25, max 2★)
          > 0.80 → N09c (moderate obvious, -20, max 2★)
        """
        if not trades:
            return []

        direction = trades[0].direction
        avg_price = sum(t.price for t in trades) / len(trades)

        detail = f"{direction}@{avg_price:.2f}"
        if avg_price > config.OBVIOUS_BET_EXTREME:
            return [_fr(config.FILTER_N09A, detail)]
        if avg_price > config.OBVIOUS_BET_HIGH:
            return [_fr(config.FILTER_N09B, detail)]
        if avg_price > config.OBVIOUS_BET_MODERATE:
            return [_fr(config.FILTER_N09C, detail)]
        return []

    # ── N10a-c — Long-horizon discount ──────────────────────

    def _check_long_horizon(
        self, resolution_date: datetime | None
    ) -> list[FilterResult]:
        """N10a-c — Market resolves too far in the future.

        An insider acts days/hours before an event, not months.
        Betting 3+ months before resolution = speculation, not insider info.

        Tiers:
          N10c: > 90 days  (-30)
          N10b: > 60 days  (-20)
          N10a: > 30 days  (-10)
          ≤ 30 days or no date → +0
        """
        if resolution_date is None:
            return []

        now = datetime.now(timezone.utc)

        # Ensure resolution_date is timezone-aware
        if resolution_date.tzinfo is None:
            resolution_date = resolution_date.replace(tzinfo=timezone.utc)

        days_to_resolution = (resolution_date - now).days
        if days_to_resolution <= 0:
            return []

        detail = f"resolution in {days_to_resolution}d"
        if days_to_resolution > config.LONG_HORIZON_EXTREME:
            return [_fr(config.FILTER_N10C, detail)]
        if days_to_resolution > config.LONG_HORIZON_HIGH:
            return [_fr(config.FILTER_N10B, detail)]
        if days_to_resolution > config.LONG_HORIZON_MODERATE:
            return [_fr(config.FILTER_N10A, detail)]
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

        if pm_days >= config.OLD_WALLET_PM_MAX_DAYS:
            return []

        # ── Verify against real Polymarket history ──────────────
        # first_seen only tracks when *our system* first saw the wallet,
        # not when the wallet actually started trading on PM.  Query the
        # Data API for the wallet's real trade history to avoid false
        # positives on veteran PM wallets we haven't seen before.
        if self.pm_client is not None:
            try:
                history = self.pm_client.get_wallet_pm_history_cached(wallet.address)
                if history is not None:
                    real_markets = history.get("distinct_markets", 0)
                    if real_markets > config.OLD_WALLET_PM_MIN_MARKETS:
                        logger.info(
                            "B20 suppressed for %s: real PM activity = %d distinct markets",
                            wallet.address[:10], real_markets,
                        )
                        return []
            except Exception as e:
                logger.debug("B20 PM history check failed for %s: %s", wallet.address[:10], e)

        return [_fr(
            config.FILTER_B20,
            f"wallet_age={wallet.wallet_age_days}d, pm_activity={pm_days}d",
        )]

    # ── N12 — Merge detection ─────────────────────────────────

    def _check_merge(self, trades: list[TradeEvent]) -> list[FilterResult]:
        """N12 — Wallet bought both YES and NO of the same market (CLOB arbitrage).

        Merge detection compares TOKEN SHARES, not dollars.
        Rationale: at odds 0.90/0.10, buying $90 YES and $10 NO costs $100
        but produces 100 YES shares and 100 NO shares — a perfect hedge.
        The dollar amounts look asymmetric; the shares are identical.

        Formula:
            shares_YES = sum(amount / price for YES trades)
            shares_NO  = sum(amount / price for NO trades)
            net_shares = abs(shares_YES - shares_NO)
            lado_mayor = max(shares_YES, shares_NO)

        Criteria (ALL must hold):
            - Wallet has trades in both YES and NO
            - First YES trade and first NO trade within MERGE_WINDOW_HOURS (12h)
            - net_shares < MERGE_NET_THRESHOLD (15%) of lado_mayor
            - Smaller side > MERGE_MIN_SHARES (1000 shares) — ignore dust

        Limitation: only detects CLOB-visible merges within the current
        scan's lookback window. CTF-layer merges (burning YES+NO pairs
        via the CTF contract) are invisible to this API.
        """
        yes_trades = [t for t in trades if t.direction == "YES"]
        no_trades = [t for t in trades if t.direction == "NO"]

        if not yes_trades or not no_trades:
            return []

        # Time window: first YES and first NO within MERGE_WINDOW_HOURS
        first_yes = min(t.timestamp for t in yes_trades)
        first_no = min(t.timestamp for t in no_trades)
        time_spread = abs((first_yes - first_no).total_seconds()) / 3600
        if time_spread > config.MERGE_WINDOW_HOURS:
            return []

        # Share calculation (tokens, not dollars)
        shares_yes = sum(
            t.amount / t.price for t in yes_trades if t.price > 0
        )
        shares_no = sum(
            t.amount / t.price for t in no_trades if t.price > 0
        )

        lado_mayor = max(shares_yes, shares_no)
        lado_menor = min(shares_yes, shares_no)

        if lado_menor <= 0:
            return []

        # Ignore dust positions
        if lado_menor < config.MERGE_MIN_SHARES:
            return []

        net_shares = abs(shares_yes - shares_no)
        if net_shares >= lado_mayor * config.MERGE_NET_THRESHOLD:
            return []

        # Dollar totals for the detail string (readability only — NOT the criterion)
        usd_yes = sum(t.amount for t in yes_trades)
        usd_no = sum(t.amount for t in no_trades)

        detail = (
            f"YES={shares_yes:,.0f} shares (${usd_yes:,.0f}), "
            f"NO={shares_no:,.0f} shares (${usd_no:,.0f}), "
            f"net={net_shares:,.0f} shares ({net_shares / lado_mayor * 100:.1f}% desequilibrio), "
            f"ventana={time_spread:.1f}h"
        )
        logger.info(
            "[N12] Merge suspected for %s: %s",
            (trades[0].wallet_address if trades else "?")[:10],
            detail,
        )
        return [_fr(config.FILTER_N12, detail)]
