"""Tests for new M04 and M05 market filters."""

from datetime import datetime, timedelta, timezone

from src.analysis.market_analyzer import MarketAnalyzer
from src.database.models import Market, TradeEvent


def _market(**overrides) -> Market:
    defaults = dict(
        market_id="m1",
        question="Will X happen?",
        volume_24h=50000.0,
        liquidity=200000.0,
    )
    defaults.update(overrides)
    return Market(**defaults)


def _trade(wallet: str, amount: float, **kw) -> TradeEvent:
    defaults = dict(
        wallet_address=wallet,
        market_id="m1",
        direction="YES",
        amount=amount,
        price=0.30,
        timestamp=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    return TradeEvent(**defaults)


class TestM04VolumeConcentration:
    def test_no_trades_returns_empty(self):
        ma = MarketAnalyzer()
        m = _market()
        result = ma._check_volume_concentration(None)
        assert result == []
        result2 = ma._check_volume_concentration([])
        assert result2 == []

    def test_below_threshold_returns_empty(self):
        ma = MarketAnalyzer()
        # 10 wallets with equal amounts → top 3 = 30%, below 60%
        trades = [_trade(f"w{i}", 100.0) for i in range(10)]
        result = ma._check_volume_concentration(trades)
        assert result == []

    def test_moderate_concentration(self):
        ma = MarketAnalyzer()
        # 3 big wallets + 2 small → top3 = 3000/4000 = 75%
        trades = [
            _trade("big1", 1000.0),
            _trade("big2", 1000.0),
            _trade("big3", 1000.0),
            _trade("small1", 500.0),
            _trade("small2", 500.0),
        ]
        result = ma._check_volume_concentration(trades)
        assert len(result) == 1
        assert result[0].filter_id == "M04a"
        assert result[0].points == 15

    def test_high_concentration(self):
        ma = MarketAnalyzer()
        # 3 big wallets + 1 tiny → top3 = 3000/3100 = 96.8%
        trades = [
            _trade("big1", 1000.0),
            _trade("big2", 1000.0),
            _trade("big3", 1000.0),
            _trade("small1", 100.0),
        ]
        result = ma._check_volume_concentration(trades)
        assert len(result) == 1
        assert result[0].filter_id == "M04b"
        assert result[0].points == 25

    def test_single_wallet_is_high(self):
        ma = MarketAnalyzer()
        trades = [_trade("w1", 5000.0)]
        result = ma._check_volume_concentration(trades)
        assert len(result) == 1
        assert result[0].filter_id == "M04b"


class TestM05DeadlineProximity:
    def test_no_resolution_date(self):
        ma = MarketAnalyzer()
        m = _market(resolution_date=None)
        result = ma._check_deadline_proximity(m)
        assert result == []

    def test_far_away_no_trigger(self):
        ma = MarketAnalyzer()
        m = _market(resolution_date=datetime.now(timezone.utc) + timedelta(days=30))
        result = ma._check_deadline_proximity(m)
        assert result == []

    def test_within_72h(self):
        ma = MarketAnalyzer()
        m = _market(resolution_date=datetime.now(timezone.utc) + timedelta(hours=48))
        result = ma._check_deadline_proximity(m)
        assert len(result) == 1
        assert result[0].filter_id == "M05a"
        assert result[0].points == 10

    def test_within_24h(self):
        ma = MarketAnalyzer()
        m = _market(resolution_date=datetime.now(timezone.utc) + timedelta(hours=12))
        result = ma._check_deadline_proximity(m)
        assert len(result) == 1
        assert result[0].filter_id == "M05b"
        assert result[0].points == 15

    def test_within_6h(self):
        ma = MarketAnalyzer()
        m = _market(resolution_date=datetime.now(timezone.utc) + timedelta(hours=3))
        result = ma._check_deadline_proximity(m)
        assert len(result) == 1
        assert result[0].filter_id == "M05c"
        assert result[0].points == 25

    def test_past_deadline_no_trigger(self):
        ma = MarketAnalyzer()
        m = _market(resolution_date=datetime.now(timezone.utc) - timedelta(hours=1))
        result = ma._check_deadline_proximity(m)
        assert result == []


class TestM04M05Integration:
    def test_analyze_includes_new_filters(self):
        """analyze() returns both M04 and M05 when applicable."""
        ma = MarketAnalyzer()
        m = _market(
            resolution_date=datetime.now(timezone.utc) + timedelta(hours=3),
        )
        trades = [
            _trade("big1", 5000.0),
            _trade("small1", 100.0),
        ]
        results = ma.analyze(m, trades=trades)
        filter_ids = {f.filter_id for f in results}
        # Should have M04b (concentration) and M05c (deadline <6h)
        assert "M04b" in filter_ids
        assert "M05c" in filter_ids
