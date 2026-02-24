"""Tests for wallet DB cache in WalletAnalyzer.analyze().

Verifies that wallet_age_days and is_first_tx_pm are read from Supabase
when fresh (< 7 days), skipping Alchemy calls, and that Alchemy is called
when the cache is missing, stale, or the wallet is new.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.analysis.wallet_analyzer import WalletAnalyzer, _WALLET_CACHE_TTL_DAYS, _parse_dt
from src.database.models import TradeEvent


# ── Helpers ──────────────────────────────────────────────────────────────────

_ADDR = "0xAbCdEf1234567890AbCdEf1234567890AbCdEf12"


def _make_trade() -> TradeEvent:
    return TradeEvent(
        wallet_address=_ADDR,
        market_id="market-1",
        direction="YES",
        amount=5000.0,
        price=0.40,
        timestamp=datetime.now(timezone.utc),
    )


def _ts(days_ago: int) -> str:
    """ISO timestamp string N days in the past (timezone-aware)."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _make_analyzer(db_row: dict | None) -> tuple[WalletAnalyzer, MagicMock, MagicMock]:
    """Return (analyzer, db_mock, chain_mock).

    db.get_wallet() returns *db_row*.
    chain mocks return safe defaults so analyze() completes without errors.
    """
    db = MagicMock()
    db.get_wallet.return_value = db_row
    db.get_funding_sources.return_value = []

    chain = MagicMock()
    chain.get_wallet_age_days.return_value = 30
    chain.is_first_tx_polymarket.return_value = False
    chain.get_balance.return_value = 0.0
    chain.get_funding_sources.return_value = []

    analyzer = WalletAnalyzer(db, chain)
    return analyzer, db, chain


# ── _parse_dt ─────────────────────────────────────────────────────────────────


class TestParseDt:
    def test_parses_Z_suffix(self):
        dt = _parse_dt("2026-02-20T10:00:00Z")
        assert dt.tzinfo is not None
        assert dt.year == 2026

    def test_parses_offset_suffix(self):
        dt = _parse_dt("2026-02-20T10:00:00+00:00")
        assert dt.tzinfo is not None

    def test_naive_datetime_becomes_utc(self):
        naive = datetime(2026, 2, 20, 10, 0, 0)
        dt = _parse_dt(naive)
        assert dt.tzinfo is not None

    def test_aware_datetime_passthrough(self):
        aware = datetime(2026, 2, 20, 10, 0, 0, tzinfo=timezone.utc)
        assert _parse_dt(aware) is aware


# ── Cache hit (fresh record) ──────────────────────────────────────────────────


class TestCacheHitFresh:
    """When DB row is fresh (< TTL days), Alchemy age+first_tx are skipped."""

    def _fresh_row(self, age_days: int = 20, first_pm: bool = True, db_age_days_ago: int = 2):
        return {
            "wallet_age_days": age_days,
            "is_first_tx_pm": first_pm,
            "updated_at": _ts(db_age_days_ago),
        }

    def test_chain_age_not_called_when_cached(self):
        row = self._fresh_row(age_days=10, db_age_days_ago=3)
        analyzer, db, chain = _make_analyzer(row)

        analyzer.analyze(_ADDR, [_make_trade()])

        chain.get_wallet_age_days.assert_not_called()

    def test_chain_first_pm_not_called_when_cached(self):
        row = self._fresh_row(age_days=10, db_age_days_ago=3)
        analyzer, db, chain = _make_analyzer(row)

        analyzer.analyze(_ADDR, [_make_trade()])

        chain.is_first_tx_polymarket.assert_not_called()

    def test_balance_always_called_live(self):
        """get_balance is never cached — must always hit Alchemy."""
        row = self._fresh_row(age_days=10, db_age_days_ago=3)
        analyzer, db, chain = _make_analyzer(row)

        analyzer.analyze(_ADDR, [_make_trade()])

        chain.get_balance.assert_called_once()

    def test_age_compensation_applied(self):
        """Cached age is incremented by the number of days since last DB write."""
        db_age = 10
        days_since_update = 3
        row = {
            "wallet_age_days": db_age,
            "is_first_tx_pm": False,
            "updated_at": _ts(days_since_update),
        }
        analyzer, db, chain = _make_analyzer(row)

        # Intercept the Wallet object passed to upsert_wallet to read the age used
        captured = {}
        original_upsert = analyzer.db.upsert_wallet

        def capture_upsert(wallet):
            captured["age"] = wallet.wallet_age_days

        analyzer.db.upsert_wallet.side_effect = capture_upsert

        analyzer.analyze(_ADDR, [_make_trade()])

        assert captured["age"] == db_age + days_since_update

    def test_cached_first_pm_true_propagates(self):
        """is_first_tx_pm=True from cache triggers W09 filter."""
        row = self._fresh_row(age_days=5, first_pm=True, db_age_days_ago=1)
        analyzer, db, chain = _make_analyzer(row)

        results = analyzer.analyze(_ADDR, [_make_trade()])

        filter_ids = [r.filter_id for r in results]
        assert "W09" in filter_ids
        chain.is_first_tx_polymarket.assert_not_called()

    def test_cached_first_pm_false_no_w09(self):
        """is_first_tx_pm=False from cache does not trigger W09."""
        row = self._fresh_row(age_days=5, first_pm=False, db_age_days_ago=1)
        analyzer, db, chain = _make_analyzer(row)

        results = analyzer.analyze(_ADDR, [_make_trade()])

        filter_ids = [r.filter_id for r in results]
        assert "W09" not in filter_ids
        chain.is_first_tx_polymarket.assert_not_called()


# ── Cache miss scenarios ──────────────────────────────────────────────────────


class TestCacheMiss:
    """Alchemy is called when there is no usable cache entry."""

    def test_no_db_row_calls_alchemy(self):
        analyzer, db, chain = _make_analyzer(db_row=None)

        analyzer.analyze(_ADDR, [_make_trade()])

        chain.get_wallet_age_days.assert_called_once_with(_ADDR)
        chain.is_first_tx_polymarket.assert_called_once_with(_ADDR)

    def test_stale_row_calls_alchemy(self):
        """Row older than TTL → treat as cache miss."""
        row = {
            "wallet_age_days": 10,
            "is_first_tx_pm": False,
            "updated_at": _ts(_WALLET_CACHE_TTL_DAYS + 1),  # one day past TTL
        }
        analyzer, db, chain = _make_analyzer(row)

        analyzer.analyze(_ADDR, [_make_trade()])

        chain.get_wallet_age_days.assert_called_once()
        chain.is_first_tx_polymarket.assert_called_once()

    def test_null_age_in_db_calls_alchemy_for_age_only(self):
        """wallet_age_days NULL in DB: still cache first_pm but fetch age from Alchemy."""
        row = {
            "wallet_age_days": None,      # age not yet populated
            "is_first_tx_pm": True,       # immutable — can still be cached
            "updated_at": _ts(1),         # fresh
        }
        analyzer, db, chain = _make_analyzer(row)

        results = analyzer.analyze(_ADDR, [_make_trade()])

        # Age must come from Alchemy (NULL in DB)
        chain.get_wallet_age_days.assert_called_once()
        # first_pm must come from cache (not Alchemy)
        chain.is_first_tx_polymarket.assert_not_called()

    def test_missing_updated_at_calls_alchemy(self):
        """Row with no updated_at field is treated as a cache miss."""
        row = {
            "wallet_age_days": 20,
            "is_first_tx_pm": False,
            # updated_at intentionally absent
        }
        analyzer, db, chain = _make_analyzer(row)

        analyzer.analyze(_ADDR, [_make_trade()])

        chain.get_wallet_age_days.assert_called_once()
        chain.is_first_tx_polymarket.assert_called_once()

    def test_db_exception_falls_back_to_alchemy(self):
        """If get_wallet() raises, silently fall back to Alchemy."""
        db = MagicMock()
        db.get_wallet.side_effect = Exception("connection error")
        db.get_funding_sources.return_value = []

        chain = MagicMock()
        chain.get_wallet_age_days.return_value = 30
        chain.is_first_tx_polymarket.return_value = False
        chain.get_balance.return_value = 0.0
        chain.get_funding_sources.return_value = []

        analyzer = WalletAnalyzer(db, chain)
        # Must not raise
        results = analyzer.analyze(_ADDR, [_make_trade()])

        chain.get_wallet_age_days.assert_called_once()
        chain.is_first_tx_polymarket.assert_called_once()


# ── Boundary: exactly at TTL ──────────────────────────────────────────────────


class TestCacheTTLBoundary:
    def test_exactly_at_ttl_is_stale(self):
        """A row exactly TTL days old is treated as stale (strict <)."""
        row = {
            "wallet_age_days": 10,
            "is_first_tx_pm": False,
            "updated_at": _ts(_WALLET_CACHE_TTL_DAYS),  # exactly 7 days
        }
        analyzer, db, chain = _make_analyzer(row)

        analyzer.analyze(_ADDR, [_make_trade()])

        chain.get_wallet_age_days.assert_called_once()

    def test_one_day_before_ttl_is_fresh(self):
        """A row TTL-1 days old is still fresh."""
        row = {
            "wallet_age_days": 10,
            "is_first_tx_pm": False,
            "updated_at": _ts(_WALLET_CACHE_TTL_DAYS - 1),
        }
        analyzer, db, chain = _make_analyzer(row)

        analyzer.analyze(_ADDR, [_make_trade()])

        chain.get_wallet_age_days.assert_not_called()
