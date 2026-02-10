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
