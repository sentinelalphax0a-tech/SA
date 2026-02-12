"""Tests for N07 scalper and N08 anti-bot evasion filters."""

from datetime import datetime, timedelta, timezone

from src.analysis.noise_filter import NoiseFilter
from src.database.models import Wallet, TradeEvent


def _trade(
    direction: str = "YES",
    amount: float = 500.0,
    minutes_ago: int = 0,
    market_id: str = "m1",
    wallet: str = "0xabc",
) -> TradeEvent:
    return TradeEvent(
        wallet_address=wallet,
        market_id=market_id,
        direction=direction,
        amount=amount,
        price=0.30,
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


def _wallet(**kw) -> Wallet:
    defaults = dict(address="0xabc", non_pm_markets=0)
    defaults.update(kw)
    return Wallet(**defaults)


class TestN07Scalper:
    def test_no_flip_returns_empty(self):
        nf = NoiseFilter()
        trades = [
            _trade("YES", minutes_ago=10),
            _trade("YES", minutes_ago=5),
        ]
        result = nf._check_scalper(trades)
        assert result == []

    def test_single_flip_triggers_n07a(self):
        nf = NoiseFilter()
        trades = [
            _trade("YES", minutes_ago=60),
            _trade("NO", minutes_ago=30),  # sell within 2h
        ]
        result = nf._check_scalper(trades)
        assert len(result) == 1
        assert result[0].filter_id == "N07a"
        assert result[0].points == -20

    def test_flip_outside_window_no_trigger(self):
        nf = NoiseFilter()
        trades = [
            _trade("YES", minutes_ago=200),
            _trade("NO", minutes_ago=5),  # >2h gap
        ]
        result = nf._check_scalper(trades)
        assert result == []

    def test_serial_scalper_triggers_n07b(self):
        nf = NoiseFilter()
        trades_m1 = [
            _trade("YES", minutes_ago=60, market_id="m1"),
            _trade("NO", minutes_ago=50, market_id="m1"),
        ]
        all_trades = [
            _trade("YES", minutes_ago=60, market_id="m1"),
            _trade("NO", minutes_ago=50, market_id="m1"),
            _trade("YES", minutes_ago=40, market_id="m2"),
            _trade("NO", minutes_ago=35, market_id="m2"),
            _trade("YES", minutes_ago=30, market_id="m3"),
            _trade("NO", minutes_ago=25, market_id="m3"),
        ]
        result = nf._check_scalper(trades_m1, all_wallet_trades=all_trades)
        assert len(result) == 1
        assert result[0].filter_id == "N07b"
        assert result[0].points == -40

    def test_single_trade_returns_empty(self):
        nf = NoiseFilter()
        result = nf._check_scalper([_trade()])
        assert result == []


class TestN08AntiBotEvasion:
    def test_too_few_trades_returns_empty(self):
        nf = NoiseFilter()
        trades = [_trade(amount=100.0) for _ in range(3)]
        result = nf._check_anti_bot_evasion(trades)
        assert result == []

    def test_uniform_amounts_triggers(self):
        nf = NoiseFilter()
        # All trades are $500 exactly → cv = 0
        trades = [_trade(amount=500.0) for _ in range(5)]
        result = nf._check_anti_bot_evasion(trades)
        assert len(result) == 1
        assert result[0].filter_id == "N08"
        assert result[0].points == 25

    def test_varied_amounts_no_trigger(self):
        nf = NoiseFilter()
        # Varied amounts → cv > 0.10
        trades = [
            _trade(amount=100.0),
            _trade(amount=500.0),
            _trade(amount=200.0),
            _trade(amount=800.0),
            _trade(amount=50.0),
        ]
        result = nf._check_anti_bot_evasion(trades)
        assert result == []

    def test_nearly_uniform_triggers(self):
        nf = NoiseFilter()
        # Nearly uniform: 500, 502, 498, 501 → very low cv
        trades = [
            _trade(amount=500.0),
            _trade(amount=502.0),
            _trade(amount=498.0),
            _trade(amount=501.0),
        ]
        result = nf._check_anti_bot_evasion(trades)
        assert len(result) == 1
        assert result[0].filter_id == "N08"


class TestN08NotWithN01:
    def test_n08_skipped_when_bot_detected(self):
        """N08 should NOT fire when N01 also fires."""
        nf = NoiseFilter()
        wallet = _wallet()
        # Regular intervals (N01 triggers) + uniform amounts (N08 would trigger)
        base = datetime.now(timezone.utc)
        trades = [
            TradeEvent(
                wallet_address="0xabc", market_id="m1", direction="YES",
                amount=500.0, price=0.30,
                timestamp=base + timedelta(seconds=i * 10),
            )
            for i in range(5)
        ]
        results = nf.analyze(wallet, trades)
        filter_ids = {f.filter_id for f in results}
        # N01 should fire, N08 should not
        assert "N01" in filter_ids
        assert "N08" not in filter_ids
