"""Tests for the behavior analyzer (B filters)."""

from datetime import datetime, timedelta

from src.analysis.behavior_analyzer import BehaviorAnalyzer
from src.database.models import Wallet, AccumulationWindow, TradeEvent
from src import config


def _make_trade(
    amount: float = 500.0,
    price: float = 0.30,
    hours_ago: int = 0,
    is_market_order: bool = True,
) -> TradeEvent:
    return TradeEvent(
        wallet_address="0xabc",
        market_id="market1",
        direction="YES",
        amount=amount,
        price=price,
        timestamp=datetime.utcnow() - timedelta(hours=hours_ago),
        is_market_order=is_market_order,
    )


class TestMarketOrders:
    def test_all_market_orders(self):
        analyzer = BehaviorAnalyzer()
        trades = [_make_trade(is_market_order=True) for _ in range(3)]
        results = analyzer._check_market_orders(trades)
        assert len(results) == 1
        assert results[0].filter_id == "B05"

    def test_mixed_orders(self):
        analyzer = BehaviorAnalyzer()
        trades = [
            _make_trade(is_market_order=True),
            _make_trade(is_market_order=False),
        ]
        results = analyzer._check_market_orders(trades)
        assert len(results) == 0


class TestIncreasingSize:
    def test_increasing(self):
        analyzer = BehaviorAnalyzer()
        trades = [
            _make_trade(amount=100, hours_ago=3),
            _make_trade(amount=200, hours_ago=2),
            _make_trade(amount=300, hours_ago=1),
        ]
        results = analyzer._check_increasing_size(trades)
        assert len(results) == 1
        assert results[0].filter_id == "B06"

    def test_not_increasing(self):
        analyzer = BehaviorAnalyzer()
        trades = [
            _make_trade(amount=300, hours_ago=3),
            _make_trade(amount=100, hours_ago=2),
        ]
        results = analyzer._check_increasing_size(trades)
        assert len(results) == 0


class TestAgainstMarket:
    def test_low_odds(self):
        analyzer = BehaviorAnalyzer()
        trades = [_make_trade(price=0.08)]
        results = analyzer._check_against_market(trades)
        assert len(results) == 1
        assert results[0].filter_id == "B07"

    def test_normal_odds(self):
        analyzer = BehaviorAnalyzer()
        trades = [_make_trade(price=0.35)]
        results = analyzer._check_against_market(trades)
        assert len(results) == 0


class TestLowHours:
    def test_low_activity_hour(self):
        analyzer = BehaviorAnalyzer()
        trade = _make_trade()
        trade.timestamp = trade.timestamp.replace(hour=3)
        results = analyzer._check_low_hours([trade])
        assert len(results) == 1
        assert results[0].filter_id == "B17"


class TestAccumulationTiers:
    def test_moderate(self):
        analyzer = BehaviorAnalyzer()
        accum = AccumulationWindow(
            wallet_address="0xabc", market_id="m1", direction="YES",
            total_amount=2500.0, trade_count=3,
            first_trade=datetime.utcnow(), last_trade=datetime.utcnow(),
        )
        results = analyzer._check_accumulation_tiers(accum, odds_move=None)
        ids = {r.filter_id for r in results}
        assert "B18a" in ids
        # 3 trades → base + 5 bonus
        b18a = [r for r in results if r.filter_id == "B18a"][0]
        assert b18a.points == config.FILTER_B18A["points"] + 5

    def test_very_strong_with_no_impact(self):
        analyzer = BehaviorAnalyzer()
        accum = AccumulationWindow(
            wallet_address="0xabc", market_id="m1", direction="YES",
            total_amount=15000.0, trade_count=10,
            first_trade=datetime.utcnow(), last_trade=datetime.utcnow(),
        )
        results = analyzer._check_accumulation_tiers(accum, odds_move=0.02)
        ids = {r.filter_id for r in results}
        assert "B18d" in ids
        assert "B18e" in ids
        # 10 trades → base + 10 bonus
        b18d = [r for r in results if r.filter_id == "B18d"][0]
        assert b18d.points == config.FILTER_B18D["points"] + 10


class TestWhaleEntry:
    def test_massive(self):
        analyzer = BehaviorAnalyzer()
        trades = [_make_trade(amount=75000)]
        results = analyzer._check_whale_entry(trades)
        assert len(results) == 1
        assert results[0].filter_id == "B19c"

    def test_below_threshold(self):
        analyzer = BehaviorAnalyzer()
        trades = [_make_trade(amount=3000)]
        results = analyzer._check_whale_entry(trades)
        assert len(results) == 0


# ── Anti triple-counting tests (B14/B18/B19) ─────────────


class TestAntiTripleCounting:
    """Rules: B18 requires ≥2 trades, B18 trade bonus, B14 suppressed by B19."""

    def test_no_triple_counting(self):
        """1 trade of $34,000 must NOT activate B14+B18+B19 simultaneously.
        Expected: only B19b (+30).
        """
        analyzer = BehaviorAnalyzer()
        trades = [_make_trade(amount=34000, hours_ago=1)]
        results = analyzer.analyze("0xabc", trades, "market1")
        ids = {r.filter_id for r in results}
        # B19b fires ($34k single tx, ≥$10k)
        assert "B19b" in ids
        # B18 must NOT fire (1 trade, need ≥2)
        assert not any(fid.startswith("B18") for fid in ids)
        # B14 must NOT fire (B19 suppresses it)
        assert "B14" not in ids

    def test_b18_single_trade_blocked(self):
        """1 trade of $10,000 → B18 does not fire (needs ≥2 trades)."""
        analyzer = BehaviorAnalyzer()
        accum = AccumulationWindow(
            wallet_address="0xabc", market_id="m1", direction="YES",
            total_amount=10000.0, trade_count=1,
            first_trade=datetime.utcnow(), last_trade=datetime.utcnow(),
        )
        results = analyzer._check_accumulation_tiers(accum, odds_move=None)
        assert len(results) == 0

    def test_two_trades_b18_base_points(self):
        """2 trades → B18d at base points, no bonus."""
        analyzer = BehaviorAnalyzer()
        accum = AccumulationWindow(
            wallet_address="0xabc", market_id="m1", direction="YES",
            total_amount=34000.0, trade_count=2,
            first_trade=datetime.utcnow(), last_trade=datetime.utcnow(),
        )
        results = analyzer._check_accumulation_tiers(accum, odds_move=None)
        assert len(results) == 1
        assert results[0].filter_id == "B18d"
        assert results[0].points == config.FILTER_B18D["points"]  # base, no bonus

    def test_three_trades_b18_bonus_5(self):
        """3 trades → base + 5 bonus."""
        analyzer = BehaviorAnalyzer()
        accum = AccumulationWindow(
            wallet_address="0xabc", market_id="m1", direction="YES",
            total_amount=2500.0, trade_count=3,
            first_trade=datetime.utcnow(), last_trade=datetime.utcnow(),
        )
        results = analyzer._check_accumulation_tiers(accum, odds_move=None)
        b18a = [r for r in results if r.filter_id == "B18a"][0]
        assert b18a.points == config.FILTER_B18A["points"] + 5

    def test_five_trades_b18_bonus_10(self):
        """5 trades → base + 10 bonus."""
        analyzer = BehaviorAnalyzer()
        accum = AccumulationWindow(
            wallet_address="0xabc", market_id="m1", direction="YES",
            total_amount=15000.0, trade_count=5,
            first_trade=datetime.utcnow(), last_trade=datetime.utcnow(),
        )
        results = analyzer._check_accumulation_tiers(accum, odds_move=None)
        b18d = [r for r in results if r.filter_id == "B18d"][0]
        assert b18d.points == config.FILTER_B18D["points"] + 10  # 50 + 10 = 60

    def test_b14_suppressed_when_b19_fires(self):
        """B14 does not fire when B19 already triggered."""
        analyzer = BehaviorAnalyzer()
        trades = [_make_trade(amount=6000)]
        result = analyzer._check_first_big_buy(trades, b19_fired=True)
        assert len(result) == 0

    def test_b14_fires_when_b19_absent(self):
        """B14 fires normally when B19 didn't fire."""
        analyzer = BehaviorAnalyzer()
        trades = [_make_trade(amount=6000)]
        result = analyzer._check_first_big_buy(trades, b19_fired=False)
        assert len(result) == 1
        assert result[0].filter_id == "B14"

    def test_two_trades_full_flow(self):
        """2 trades of $17,000 → B18d (+50) + B19b (+30), no B14."""
        analyzer = BehaviorAnalyzer()
        trades = [
            _make_trade(amount=17000, hours_ago=2),
            _make_trade(amount=17000, hours_ago=1),
        ]
        results = analyzer.analyze("0xabc", trades, "market1")
        ids = {r.filter_id for r in results}
        points = {r.filter_id: r.points for r in results}
        assert "B18d" in ids
        assert points["B18d"] == config.FILTER_B18D["points"]  # 50, 2 trades = no bonus
        assert "B19b" in ids
        assert "B14" not in ids
