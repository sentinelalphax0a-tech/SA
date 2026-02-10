"""Tests for the wallet analyzer (W and O filters)."""

from unittest.mock import MagicMock

from src.analysis.wallet_analyzer import WalletAnalyzer
from src.database.models import Wallet
from src import config


def _make_analyzer() -> WalletAnalyzer:
    db = MagicMock()
    chain = MagicMock()
    return WalletAnalyzer(db, chain)


class TestWalletAge:
    def test_very_new_wallet(self):
        analyzer = _make_analyzer()
        wallet = Wallet(address="0xabc", wallet_age_days=3)
        results = analyzer._check_wallet_age(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "W01"

    def test_new_wallet(self):
        analyzer = _make_analyzer()
        wallet = Wallet(address="0xabc", wallet_age_days=10)
        results = analyzer._check_wallet_age(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "W02"

    def test_recent_wallet(self):
        analyzer = _make_analyzer()
        wallet = Wallet(address="0xabc", wallet_age_days=20)
        results = analyzer._check_wallet_age(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "W03"

    def test_old_wallet_no_filter(self):
        analyzer = _make_analyzer()
        wallet = Wallet(address="0xabc", wallet_age_days=60)
        results = analyzer._check_wallet_age(wallet)
        assert len(results) == 0


class TestMarketCount:
    def test_single_market(self):
        analyzer = _make_analyzer()
        wallet = Wallet(address="0xabc", total_markets=1)
        results = analyzer._check_market_count(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "W04"

    def test_few_markets(self):
        analyzer = _make_analyzer()
        wallet = Wallet(address="0xabc", total_markets=3)
        results = analyzer._check_market_count(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "W05"

    def test_many_markets_no_filter(self):
        analyzer = _make_analyzer()
        wallet = Wallet(address="0xabc", total_markets=10)
        results = analyzer._check_market_count(wallet)
        assert len(results) == 0


class TestFirstTxPM:
    def test_first_tx_is_pm(self):
        analyzer = _make_analyzer()
        wallet = Wallet(address="0xabc", is_first_tx_pm=True)
        results = analyzer._check_first_tx_pm(wallet)
        assert len(results) == 1
        assert results[0].filter_id == "W09"

    def test_first_tx_not_pm(self):
        analyzer = _make_analyzer()
        wallet = Wallet(address="0xabc", is_first_tx_pm=False)
        results = analyzer._check_first_tx_pm(wallet)
        assert len(results) == 0
