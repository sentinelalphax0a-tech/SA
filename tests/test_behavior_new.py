"""Tests for B23 position sizing filter."""

from datetime import datetime, timezone

from src.analysis.behavior_analyzer import BehaviorAnalyzer
from src.database.models import TradeEvent, AccumulationWindow


def _trade(amount: float = 500.0, **kw) -> TradeEvent:
    defaults = dict(
        wallet_address="0xabc",
        market_id="m1",
        direction="YES",
        amount=amount,
        price=0.30,
        timestamp=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    return TradeEvent(**defaults)


def _accum(total_amount: float) -> AccumulationWindow:
    now = datetime.now(timezone.utc)
    return AccumulationWindow(
        wallet_address="0xabc",
        market_id="m1",
        direction="YES",
        total_amount=total_amount,
        trade_count=1,
        first_trade=now,
        last_trade=now,
    )


class TestB23PositionSizing:
    def test_no_balance_returns_empty(self):
        ba = BehaviorAnalyzer()
        result = ba._check_position_sizing(_accum(1000.0), wallet_balance=None)
        assert result == []

    def test_zero_balance_returns_empty(self):
        ba = BehaviorAnalyzer()
        result = ba._check_position_sizing(_accum(1000.0), wallet_balance=0.0)
        assert result == []

    def test_below_threshold_returns_empty(self):
        ba = BehaviorAnalyzer()
        # 1000 / 10000 = 10% < 20% threshold
        result = ba._check_position_sizing(_accum(1000.0), wallet_balance=10000.0)
        assert result == []

    def test_significant_position(self):
        ba = BehaviorAnalyzer()
        # 3000 / 10000 = 30% → B23a (20-50%)
        result = ba._check_position_sizing(_accum(3000.0), wallet_balance=10000.0)
        assert len(result) == 1
        assert result[0].filter_id == "B23a"
        assert result[0].points == 15

    def test_dominant_position(self):
        ba = BehaviorAnalyzer()
        # 7000 / 10000 = 70% → B23b (>50%)
        result = ba._check_position_sizing(_accum(7000.0), wallet_balance=10000.0)
        assert len(result) == 1
        assert result[0].filter_id == "B23b"
        assert result[0].points == 30

    def test_exact_50_percent_is_dominant(self):
        ba = BehaviorAnalyzer()
        # 5000 / 10000 = 50% → B23b (>= 50%)
        result = ba._check_position_sizing(_accum(5000.0), wallet_balance=10000.0)
        assert len(result) == 1
        assert result[0].filter_id == "B23b"

    def test_exact_20_percent_is_significant(self):
        ba = BehaviorAnalyzer()
        # 2000 / 10000 = 20% → B23a
        result = ba._check_position_sizing(_accum(2000.0), wallet_balance=10000.0)
        assert len(result) == 1
        assert result[0].filter_id == "B23a"


class TestB23InAnalyze:
    def test_analyze_passes_wallet_balance(self):
        """$7K / $10K = 70% → B28b fires (all-in), suppressing B23."""
        ba = BehaviorAnalyzer()
        trades = [_trade(7000.0)]
        results = ba.analyze(
            wallet_address="0xabc",
            trades=trades,
            market_id="m1",
            current_odds=0.30,
            wallet_balance=10000.0,
        )
        filter_ids = {f.filter_id for f in results}
        assert "B28b" in filter_ids
        assert "B23b" not in filter_ids  # B28 suppresses B23

    def test_analyze_without_balance_no_b23(self):
        ba = BehaviorAnalyzer()
        trades = [_trade(7000.0)]
        results = ba.analyze(
            wallet_address="0xabc",
            trades=trades,
            market_id="m1",
            current_odds=0.30,
        )
        filter_ids = {f.filter_id for f in results}
        assert "B23a" not in filter_ids
        assert "B23b" not in filter_ids
