"""Tests for the noise filter (N filters)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.analysis.noise_filter import NoiseFilter
from src.database.models import Wallet, TradeEvent
from src import config


def _make_trade(
    hours_ago: int = 0,
    market_id: str = "m1",
    direction: str = "YES",
    wallet_address: str = "0xabc",
) -> TradeEvent:
    return TradeEvent(
        wallet_address=wallet_address,
        market_id=market_id,
        direction=direction,
        amount=500.0,
        price=0.30,
        timestamp=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )


# ── N01 — Bot detection ─────────────────────────────────────


class TestBotDetection:
    def test_regular_intervals_detected(self):
        nf = NoiseFilter()
        base = datetime.now(timezone.utc)
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
        assert results[0].points == -40

    def test_irregular_intervals_not_detected(self):
        nf = NoiseFilter()
        base = datetime.now(timezone.utc)
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

    def test_too_few_trades(self):
        nf = NoiseFilter()
        trades = [_make_trade(), _make_trade()]
        results = nf._check_bot(trades)
        assert len(results) == 0


# ── N02 — News detection ────────────────────────────────────


class TestNewsFilter:
    def test_n02_news_found(self):
        news = MagicMock()
        news.check_news.return_value = (True, "Breaking: event happened")
        nf = NoiseFilter(news_checker=news)
        results = nf._check_news("Will event happen?")
        assert len(results) == 1
        assert results[0].filter_id == "N02"
        assert results[0].points == -20
        assert "Breaking" in results[0].details

    def test_n02_no_news(self):
        news = MagicMock()
        news.check_news.return_value = (False, None)
        nf = NoiseFilter(news_checker=news)
        results = nf._check_news("Will obscure event happen?")
        assert len(results) == 0

    def test_n02_no_question(self):
        nf = NoiseFilter(news_checker=MagicMock())
        results = nf._check_news(None)
        assert len(results) == 0

    def test_n02_no_news_checker(self):
        nf = NoiseFilter()
        results = nf._check_news("Some question?")
        assert len(results) == 0


# ── N05 — Copy-trading ──────────────────────────────────────


class TestCopyTrading:
    def test_n05_copy_within_window(self):
        """Trade 5 minutes after whale in same market+direction → N05."""
        nf = NoiseFilter()
        base = datetime.now(timezone.utc)

        whale_trade = TradeEvent(
            wallet_address="0xwhale", market_id="m1", direction="YES",
            amount=50000, price=0.30, timestamp=base,
        )
        copy_trade = TradeEvent(
            wallet_address="0xcopy", market_id="m1", direction="YES",
            amount=500, price=0.31,
            timestamp=base + timedelta(minutes=5),
        )

        results = nf._check_copy_trading([copy_trade], [whale_trade])
        assert len(results) == 1
        assert results[0].filter_id == "N05"
        assert results[0].points == -25

    def test_n05_no_trigger_too_fast(self):
        """Trade < 2 min after whale → not copy-trading."""
        nf = NoiseFilter()
        base = datetime.now(timezone.utc)

        whale_trade = TradeEvent(
            wallet_address="0xwhale", market_id="m1", direction="YES",
            amount=50000, price=0.30, timestamp=base,
        )
        fast_trade = TradeEvent(
            wallet_address="0xfast", market_id="m1", direction="YES",
            amount=500, price=0.31,
            timestamp=base + timedelta(seconds=30),
        )

        results = nf._check_copy_trading([fast_trade], [whale_trade])
        assert len(results) == 0

    def test_n05_no_trigger_too_slow(self):
        """Trade > 10 min after whale → not copy-trading."""
        nf = NoiseFilter()
        base = datetime.now(timezone.utc)

        whale_trade = TradeEvent(
            wallet_address="0xwhale", market_id="m1", direction="YES",
            amount=50000, price=0.30, timestamp=base,
        )
        slow_trade = TradeEvent(
            wallet_address="0xslow", market_id="m1", direction="YES",
            amount=500, price=0.31,
            timestamp=base + timedelta(minutes=15),
        )

        results = nf._check_copy_trading([slow_trade], [whale_trade])
        assert len(results) == 0

    def test_n05_different_direction_no_trigger(self):
        """Trade in opposite direction → not copy-trading."""
        nf = NoiseFilter()
        base = datetime.now(timezone.utc)

        whale_trade = TradeEvent(
            wallet_address="0xwhale", market_id="m1", direction="YES",
            amount=50000, price=0.30, timestamp=base,
        )
        opposite_trade = TradeEvent(
            wallet_address="0xcopy", market_id="m1", direction="NO",
            amount=500, price=0.70,
            timestamp=base + timedelta(minutes=5),
        )

        results = nf._check_copy_trading([opposite_trade], [whale_trade])
        assert len(results) == 0

    def test_n05_no_whale_trades(self):
        nf = NoiseFilter()
        results = nf._check_copy_trading([_make_trade()], None)
        assert len(results) == 0

    def test_n05_same_wallet_ignored(self):
        """Whale shouldn't be flagged as copying themselves."""
        nf = NoiseFilter()
        base = datetime.now(timezone.utc)

        whale_trade = TradeEvent(
            wallet_address="0xwhale", market_id="m1", direction="YES",
            amount=50000, price=0.30, timestamp=base,
        )
        same_whale = TradeEvent(
            wallet_address="0xwhale", market_id="m1", direction="YES",
            amount=10000, price=0.31,
            timestamp=base + timedelta(minutes=5),
        )

        results = nf._check_copy_trading([same_whale], [whale_trade])
        assert len(results) == 0


# ── N06 — Degen tiers ───────────────────────────────────────


class TestDegenFilter:
    def test_degen_heavy(self):
        nf = NoiseFilter()
        wallet = Wallet(address="0xabc", non_pm_markets=8)
        results = nf._check_degen(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "N06c"
        assert results[0].points == -30

    def test_degen_moderate(self):
        nf = NoiseFilter()
        wallet = Wallet(address="0xabc", non_pm_markets=4)
        results = nf._check_degen(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "N06b"
        assert results[0].points == -15

    def test_degen_light(self):
        nf = NoiseFilter()
        wallet = Wallet(address="0xabc", non_pm_markets=2)
        results = nf._check_degen(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "N06a"
        assert results[0].points == -5

    def test_no_degen(self):
        nf = NoiseFilter()
        wallet = Wallet(address="0xabc", non_pm_markets=0)
        results = nf._check_degen(wallet)
        assert len(results) == 0

    def test_mutually_exclusive(self):
        """Only the highest applicable tier should fire."""
        nf = NoiseFilter()
        wallet = Wallet(address="0xabc", non_pm_markets=6)
        results = nf._check_degen(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "N06c"  # not N06a or N06b


# ── Full analyze flow ────────────────────────────────────────


class TestAnalyzeIntegration:
    def test_analyze_bot_and_degen(self):
        """Bot pattern + degen wallet → both filters fire."""
        nf = NoiseFilter()
        base = datetime.now(timezone.utc)
        trades = [
            TradeEvent(
                wallet_address="0xbot", market_id="m1", direction="YES",
                amount=100, price=0.3,
                timestamp=base + timedelta(seconds=60 * i),
            )
            for i in range(5)
        ]
        wallet = Wallet(address="0xbot", non_pm_markets=8)
        results = nf.analyze(wallet, trades)
        ids = {r.filter_id for r in results}
        assert "N01" in ids
        assert "N06c" in ids

    def test_analyze_clean_wallet(self):
        """Normal wallet, no noise → empty results."""
        nf = NoiseFilter()
        base = datetime.now(timezone.utc)
        trades = [
            TradeEvent(
                wallet_address="0xclean", market_id="m1", direction="YES",
                amount=100, price=0.3, timestamp=base,
            ),
            TradeEvent(
                wallet_address="0xclean", market_id="m1", direction="YES",
                amount=200, price=0.3,
                timestamp=base + timedelta(minutes=47),
            ),
            TradeEvent(
                wallet_address="0xclean", market_id="m1", direction="YES",
                amount=300, price=0.3,
                timestamp=base + timedelta(hours=3),
            ),
        ]
        wallet = Wallet(address="0xclean", non_pm_markets=0)
        results = nf.analyze(wallet, trades)
        assert len(results) == 0
