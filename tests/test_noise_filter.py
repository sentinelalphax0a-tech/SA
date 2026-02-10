"""Tests for the noise filter (N filters)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from src.analysis.noise_filter import NoiseFilter
from src.database.models import Wallet, TradeEvent
from src import config


def _make_trade(hours_ago: int = 0) -> TradeEvent:
    return TradeEvent(
        wallet_address="0xabc",
        market_id="m1",
        direction="YES",
        amount=500.0,
        price=0.30,
        timestamp=datetime.utcnow() - timedelta(hours=hours_ago),
    )


class TestBotDetection:
    def test_regular_intervals_detected(self):
        news = MagicMock()
        nf = NoiseFilter(news)
        # Trades at perfectly regular 60s intervals
        base = datetime.utcnow()
        trades = [
            TradeEvent(
                wallet_address="0xabc", market_id="m1", direction="YES",
                amount=100, price=0.3,
                timestamp=base + timedelta(seconds=60 * i),
            )
            for i in range(5)
        ]
        results = nf._check_bot(trades)
        assert len(results) == 1
        assert results[0].filter_id == "N01"

    def test_irregular_intervals_not_detected(self):
        news = MagicMock()
        nf = NoiseFilter(news)
        base = datetime.utcnow()
        trades = [
            TradeEvent(
                wallet_address="0xabc", market_id="m1", direction="YES",
                amount=100, price=0.3, timestamp=base,
            ),
            TradeEvent(
                wallet_address="0xabc", market_id="m1", direction="YES",
                amount=100, price=0.3,
                timestamp=base + timedelta(seconds=120),
            ),
            TradeEvent(
                wallet_address="0xabc", market_id="m1", direction="YES",
                amount=100, price=0.3,
                timestamp=base + timedelta(seconds=500),
            ),
        ]
        results = nf._check_bot(trades)
        assert len(results) == 0


class TestDegenFilter:
    def test_degen_heavy(self):
        news = MagicMock()
        nf = NoiseFilter(news)
        wallet = Wallet(address="0xabc", non_pm_markets=8)
        results = nf._check_degen(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "N06c"

    def test_no_degen(self):
        news = MagicMock()
        nf = NoiseFilter(news)
        wallet = Wallet(address="0xabc", non_pm_markets=0)
        results = nf._check_degen(wallet)
        assert len(results) == 0
