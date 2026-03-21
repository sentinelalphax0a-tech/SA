"""Tests for OPT-1: wallet_funding DB cache in Phase 2 of WalletAnalyzer.

Verifies that _get_funding_with_db_cache():
  1. Returns DB data and skips Alchemy when rows are fresh (<14d).
  2. Falls through to Alchemy when DB is empty (cache miss).
  3. Falls through to Alchemy when DB data is stale (>=14d).
  4. Falls through to Alchemy gracefully when DB lookup raises.
  5. Persists Alchemy results to DB on cache miss.
  6. Does NOT call insert_funding_batch on a cache hit.
  7. _db_rows_to_wallet_funding converts DB dicts correctly.
  8. analyze() end-to-end: chain.get_funding_sources NOT called on fresh cache hit.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.analysis.wallet_analyzer import (
    WalletAnalyzer,
    _db_rows_to_wallet_funding,
    _FUNDING_CACHE_TTL_DAYS,
)
from src.database.models import WalletFunding, TradeEvent


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_analyzer() -> WalletAnalyzer:
    db = MagicMock()
    chain = MagicMock()
    # Defaults: no wallet cache, balance=0, age=None, first_pm=False
    db.get_wallet.return_value = None
    chain.get_wallet_age_days.return_value = None
    chain.is_first_tx_polymarket.return_value = False
    chain.get_balance.return_value = 0.0
    return WalletAnalyzer(db, chain)


def _fresh_db_row(wallet: str = "0xAAA", sender: str = "0xBBB", age_days: int = 3) -> dict:
    """A single wallet_funding DB row created `age_days` ago."""
    created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {
        "id": 1,
        "wallet_address": wallet.lower(),
        "sender_address": sender.lower(),
        "amount": 1000.0,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "hop_level": 1,
        "is_exchange": False,
        "exchange_name": None,
        "is_bridge": False,
        "bridge_name": None,
        "is_mixer": False,
        "mixer_name": None,
        "created_at": created_at.isoformat(),
    }


def _stale_db_row(**kwargs) -> dict:
    """A DB row created _FUNDING_CACHE_TTL_DAYS + 1 days ago (stale)."""
    return _fresh_db_row(age_days=_FUNDING_CACHE_TTL_DAYS + 1, **kwargs)


def _make_wallet_funding_from_chain() -> list[WalletFunding]:
    return [WalletFunding(
        wallet_address="0xaaa",
        sender_address="0xccc",
        amount=500.0,
        hop_level=1,
    )]


def _make_trade(wallet: str = "0xAAA") -> TradeEvent:
    return TradeEvent(
        wallet_address=wallet,
        market_id="mkt-1",
        direction="YES",
        amount=5000.0,
        price=0.5,
        timestamp=datetime.now(timezone.utc),
    )


# ── Unit tests for _get_funding_with_db_cache ─────────────────────────────

class TestFundingDbCacheHit:
    """Fresh DB data → Alchemy not called."""

    def setup_method(self):
        self.az = _make_analyzer()
        self.row = _fresh_db_row(age_days=0)
        self.az.db.get_funding_sources.return_value = [self.row]

    def test_chain_not_called_on_hit(self):
        self.az._get_funding_with_db_cache("0xAAA")
        self.az.chain.get_funding_sources.assert_not_called()

    def test_returns_converted_funding(self):
        result = self.az._get_funding_with_db_cache("0xAAA")
        assert len(result) == 1
        assert isinstance(result[0], WalletFunding)
        assert result[0].sender_address == "0xbbb"
        assert result[0].amount == 1000.0
        assert result[0].hop_level == 1

    def test_insert_funding_batch_not_called_on_hit(self):
        self.az._get_funding_with_db_cache("0xAAA")
        self.az.db.insert_funding_batch.assert_not_called()

    def test_hit_near_ttl_boundary(self):
        """A row created exactly TTL-1 days ago is still a hit."""
        self.az.db.get_funding_sources.return_value = [
            _fresh_db_row(age_days=_FUNDING_CACHE_TTL_DAYS - 1)
        ]
        result = self.az._get_funding_with_db_cache("0xAAA")
        self.az.chain.get_funding_sources.assert_not_called()
        assert len(result) == 1

    def test_multiple_rows_all_returned(self):
        rows = [_fresh_db_row(sender=f"0xS{i}") for i in range(5)]
        self.az.db.get_funding_sources.return_value = rows
        result = self.az._get_funding_with_db_cache("0xAAA")
        assert len(result) == 5
        self.az.chain.get_funding_sources.assert_not_called()


class TestFundingDbCacheMiss:
    """Empty DB → Alchemy called, result persisted."""

    def setup_method(self):
        self.az = _make_analyzer()
        self.az.db.get_funding_sources.return_value = []
        self.chain_result = _make_wallet_funding_from_chain()
        self.az.chain.get_funding_sources.return_value = self.chain_result

    def test_chain_called_on_miss(self):
        self.az._get_funding_with_db_cache("0xAAA")
        self.az.chain.get_funding_sources.assert_called_once()
        call_kwargs = self.az.chain.get_funding_sources.call_args.kwargs
        assert call_kwargs.get("max_hops") == self.az.max_hops
        assert callable(call_kwargs.get("db_funding_lookup"))

    def test_result_persisted_to_db(self):
        self.az._get_funding_with_db_cache("0xAAA")
        self.az.db.insert_funding_batch.assert_called_once_with(self.chain_result)

    def test_returns_chain_result(self):
        result = self.az._get_funding_with_db_cache("0xAAA")
        assert result == self.chain_result

    def test_no_persist_when_chain_returns_empty(self):
        self.az.chain.get_funding_sources.return_value = []
        self.az._get_funding_with_db_cache("0xAAA")
        self.az.db.insert_funding_batch.assert_not_called()


class TestFundingDbCacheStale:
    """Stale DB data (>=14d) → falls through to Alchemy."""

    def setup_method(self):
        self.az = _make_analyzer()
        self.az.db.get_funding_sources.return_value = [_stale_db_row()]
        self.chain_result = _make_wallet_funding_from_chain()
        self.az.chain.get_funding_sources.return_value = self.chain_result

    def test_chain_called_on_stale(self):
        self.az._get_funding_with_db_cache("0xAAA")
        self.az.chain.get_funding_sources.assert_called_once()

    def test_stale_result_NOT_returned(self):
        result = self.az._get_funding_with_db_cache("0xAAA")
        # Should return fresh chain result, not the stale 0xBBB row
        assert result == self.chain_result

    def test_stale_then_alchemy_persisted(self):
        self.az._get_funding_with_db_cache("0xAAA")
        self.az.db.insert_funding_batch.assert_called_once_with(self.chain_result)


class TestFundingDbCacheError:
    """DB lookup raises → falls back to Alchemy gracefully."""

    def setup_method(self):
        self.az = _make_analyzer()
        self.az.db.get_funding_sources.side_effect = Exception("DB timeout")
        self.chain_result = _make_wallet_funding_from_chain()
        self.az.chain.get_funding_sources.return_value = self.chain_result

    def test_chain_called_on_db_error(self):
        self.az._get_funding_with_db_cache("0xAAA")
        self.az.chain.get_funding_sources.assert_called_once()

    def test_returns_chain_result_on_db_error(self):
        result = self.az._get_funding_with_db_cache("0xAAA")
        assert result == self.chain_result

    def test_no_exception_propagated(self):
        # Should not raise even if DB fails
        result = self.az._get_funding_with_db_cache("0xAAA")
        assert isinstance(result, list)


# ── Unit tests for _db_rows_to_wallet_funding ─────────────────────────────

class TestDbRowsToWalletFunding:
    def test_basic_conversion(self):
        row = _fresh_db_row()
        result = _db_rows_to_wallet_funding([row])
        assert len(result) == 1
        wf = result[0]
        assert isinstance(wf, WalletFunding)
        assert wf.wallet_address == "0xaaa"
        assert wf.sender_address == "0xbbb"
        assert wf.amount == 1000.0
        assert wf.hop_level == 1
        assert wf.is_exchange is False
        assert wf.exchange_name is None
        assert wf.id == 1

    def test_timestamp_parsed(self):
        row = _fresh_db_row()
        result = _db_rows_to_wallet_funding([row])
        assert result[0].timestamp is not None
        assert result[0].timestamp.tzinfo is not None

    def test_empty_input(self):
        assert _db_rows_to_wallet_funding([]) == []

    def test_exchange_row(self):
        row = _fresh_db_row()
        row["is_exchange"] = True
        row["exchange_name"] = "Binance"
        result = _db_rows_to_wallet_funding([row])
        assert result[0].is_exchange is True
        assert result[0].exchange_name == "Binance"

    def test_mixer_row(self):
        row = _fresh_db_row()
        row["is_mixer"] = True
        row["mixer_name"] = "Tornado Cash"
        result = _db_rows_to_wallet_funding([row])
        assert result[0].is_mixer is True
        assert result[0].mixer_name == "Tornado Cash"

    def test_missing_timestamp_is_none(self):
        row = _fresh_db_row()
        row["timestamp"] = None
        result = _db_rows_to_wallet_funding([row])
        assert result[0].timestamp is None

    def test_multiple_rows(self):
        rows = [_fresh_db_row(sender=f"0xS{i}") for i in range(3)]
        result = _db_rows_to_wallet_funding(rows)
        assert len(result) == 3


# ── End-to-end: analyze() with cache hit ──────────────────────────────────

class TestAnalyzeFundingCacheIntegration:
    """analyze() end-to-end: Alchemy not called when DB cache is fresh."""

    def _setup_high_score_wallet(self, az: WalletAnalyzer, wallet: str = "0xAAA"):
        """Configure chain mocks so Phase 1 basic_score >= 30."""
        # W01 (very new wallet) = 25pts + W09 (first_tx_pm) = 5pts → 30pts total
        az.chain.get_wallet_age_days.return_value = 3   # W01 = 25pts
        az.chain.is_first_tx_polymarket.return_value = True  # W09 = 5pts
        az.chain.get_balance.return_value = 0.0
        az.db.get_wallet.return_value = None  # no wallet cache

    def test_chain_funding_not_called_on_db_cache_hit(self):
        """Main assertion: with fresh DB data, chain.get_funding_sources is never called."""
        az = _make_analyzer()
        self._setup_high_score_wallet(az)

        # DB has fresh funding data for this wallet
        az.db.get_funding_sources.return_value = [_fresh_db_row(age_days=1)]
        az.db.upsert_wallet.return_value = None

        trade = _make_trade("0xAAA")
        az.analyze("0xAAA", [trade])

        az.chain.get_funding_sources.assert_not_called()

    def test_chain_funding_called_on_db_cache_miss(self):
        """When DB has no funding data, chain.get_funding_sources is called."""
        az = _make_analyzer()
        self._setup_high_score_wallet(az)

        az.db.get_funding_sources.return_value = []
        az.chain.get_funding_sources.return_value = []
        az.db.upsert_wallet.return_value = None

        trade = _make_trade("0xAAA")
        az.analyze("0xAAA", [trade])

        az.chain.get_funding_sources.assert_called_once()

    def test_cache_hit_still_evaluates_origin_filters(self):
        """Filters O01/O02/O03 still fire even when data comes from DB cache."""
        az = _make_analyzer()
        self._setup_high_score_wallet(az)
        az.db.upsert_wallet.return_value = None

        # DB row from a known exchange
        row = _fresh_db_row(age_days=2)
        row["is_exchange"] = True
        row["exchange_name"] = "Binance"
        az.db.get_funding_sources.return_value = [row]

        trade = _make_trade("0xAAA")
        results = az.analyze("0xAAA", [trade])

        filter_ids = [r.filter_id for r in results]
        assert "O01" in filter_ids  # exchange origin filter should fire
        az.chain.get_funding_sources.assert_not_called()

    def test_score_below_30_skips_funding_entirely(self):
        """With basic_score < 30, neither DB nor Alchemy is consulted for funding."""
        az = _make_analyzer()
        # No W filters → basic_score = 0 < 30
        az.chain.get_wallet_age_days.return_value = 365   # old wallet → no W filter
        az.chain.is_first_tx_polymarket.return_value = False
        az.chain.get_balance.return_value = 0.0
        az.db.get_wallet.return_value = None
        az.db.upsert_wallet.return_value = None

        trade = _make_trade("0xAAA")
        az.analyze("0xAAA", [trade])

        # get_funding_sources should not be called at all (DB or chain)
        az.db.get_funding_sources.assert_not_called()
        az.chain.get_funding_sources.assert_not_called()


# ── OPT-A: BFS db_funding_lookup callback (blockchain_client) ─────────────

class TestBFSDbFundingLookup:
    """
    Verify that BlockchainClient.get_funding_sources() respects db_funding_lookup:
      1. Cache hit → _fetch_transfers NOT called for that address
      2. Cache miss → _fetch_transfers IS called
      3. Results are equivalent with/without cache
    """

    def _make_client(self) -> "BlockchainClient":
        from src.scanner.blockchain_client import BlockchainClient
        from unittest.mock import patch, MagicMock
        with patch("src.scanner.blockchain_client.Web3") as mock_w3:
            mock_w3.is_address.return_value = True
            mock_w3.return_value.is_connected.return_value = True
            client = BlockchainClient.__new__(BlockchainClient)
            client._age_cache = {}
            client._first_pm_cache = {}
            client._balance_cache = {}
            client._funding_cache = {}
            client._calls_ok = 0
            client._rl_hits = 0
            client._calls_failed = 0
        return client

    def _make_funding(self, wallet: str, sender: str, hop: int = 1) -> "WalletFunding":
        return WalletFunding(
            wallet_address=wallet.lower(),
            sender_address=sender.lower(),
            amount=500.0,
            hop_level=hop,
            is_exchange=False,
        )

    def test_cache_hit_skips_fetch_transfers(self):
        """When db_funding_lookup returns data for every BFS node, _fetch_transfers is NOT called."""
        from unittest.mock import MagicMock, patch
        client = self._make_client()

        # max_hops=1 → sender of hop-1 is NOT enqueued for hop-2 (hop < max_hops fails)
        # So only "0xabc" is processed and it hits the cache → _fetch_transfers never called.
        hop1_data = [self._make_funding("0xabc", "0xsender1", hop=1)]

        def lookup(addr: str):
            if addr == "0xabc":
                return hop1_data
            return None

        with patch.object(client, "_fetch_transfers") as mock_fetch:
            with patch("src.scanner.blockchain_client.Web3") as mock_w3:
                mock_w3.is_address.return_value = True
                result = client.get_funding_sources(
                    "0xabc", max_hops=1, db_funding_lookup=lookup
                )

        mock_fetch.assert_not_called()
        assert len(result) == 1
        assert result[0].sender_address == "0xsender1"

    def test_cache_miss_calls_fetch_transfers(self):
        """When db_funding_lookup returns None, _fetch_transfers IS called."""
        from unittest.mock import MagicMock, patch
        import time as _time
        client = self._make_client()

        def lookup(addr: str):
            return None  # always miss

        with patch.object(client, "_fetch_transfers", return_value=[]) as mock_fetch:
            with patch("src.scanner.blockchain_client.Web3") as mock_w3:
                mock_w3.is_address.return_value = True
                with patch("src.scanner.blockchain_client.time") as mock_time:
                    client.get_funding_sources(
                        "0xabc", max_hops=1, db_funding_lookup=lookup
                    )

        mock_fetch.assert_called_once()

    def test_no_lookup_still_calls_fetch_transfers(self):
        """Without db_funding_lookup, _fetch_transfers is called as before."""
        from unittest.mock import patch
        client = self._make_client()

        with patch.object(client, "_fetch_transfers", return_value=[]) as mock_fetch:
            with patch("src.scanner.blockchain_client.Web3") as mock_w3:
                mock_w3.is_address.return_value = True
                with patch("src.scanner.blockchain_client.time"):
                    client.get_funding_sources("0xabc", max_hops=1)

        mock_fetch.assert_called_once()

    def test_cache_hit_enqueues_non_exchange_senders(self):
        """BFS continues to hop 2 for non-exchange senders found via cache."""
        from unittest.mock import patch
        client = self._make_client()

        hop1_data = [self._make_funding("0xabc", "0xintermediate", hop=1)]
        hop2_data = [self._make_funding("0xintermediate", "0xroot", hop=2)]

        def lookup(addr: str):
            if addr == "0xabc":
                return hop1_data
            if addr == "0xintermediate":
                return hop2_data
            return None

        with patch.object(client, "_fetch_transfers", return_value=[]) as mock_fetch:
            with patch("src.scanner.blockchain_client.Web3") as mock_w3:
                mock_w3.is_address.return_value = True
                result = client.get_funding_sources(
                    "0xabc", max_hops=2, db_funding_lookup=lookup
                )

        mock_fetch.assert_not_called()
        senders = {f.sender_address for f in result}
        assert "0xintermediate" in senders
        assert "0xroot" in senders

    def test_exchange_sender_not_enqueued(self):
        """Exchange senders found via cache do NOT trigger further BFS hops."""
        from unittest.mock import patch
        client = self._make_client()

        exchange_funding = [WalletFunding(
            wallet_address="0xabc",
            sender_address="0xbinance",
            amount=1000.0,
            hop_level=1,
            is_exchange=True,
            exchange_name="Binance",
        )]

        calls: list[str] = []

        def lookup(addr: str):
            calls.append(addr)
            if addr == "0xabc":
                return exchange_funding
            return None

        with patch.object(client, "_fetch_transfers", return_value=[]):
            with patch("src.scanner.blockchain_client.Web3") as mock_w3:
                mock_w3.is_address.return_value = True
                client.get_funding_sources("0xabc", max_hops=2, db_funding_lookup=lookup)

        # Exchange sender (0xbinance) should NOT be added to the BFS queue
        assert "0xbinance" not in calls


# ── OPT-A: wallet_analyzer passes db_funding_lookup to chain ──────────────

class TestWalletAnalyzerPassesDbLookup:
    """Verify _get_funding_with_db_cache passes db_funding_lookup to chain on miss."""

    def test_db_lookup_passed_on_cache_miss(self):
        """On cache miss, chain.get_funding_sources receives a callable db_funding_lookup."""
        az = _make_analyzer()
        az.db.get_funding_sources.return_value = []  # miss
        az.chain.get_funding_sources.return_value = []

        az._get_funding_with_db_cache("0xAAA")

        call_kwargs = az.chain.get_funding_sources.call_args.kwargs
        assert "db_funding_lookup" in call_kwargs
        assert callable(call_kwargs["db_funding_lookup"])

    def test_db_lookup_callback_returns_none_on_empty(self):
        """The callback returns None when DB has no rows for an address."""
        az = _make_analyzer()
        az.db.get_funding_sources.return_value = []  # miss
        az.chain.get_funding_sources.return_value = []

        az._get_funding_with_db_cache("0xAAA")

        callback = az.chain.get_funding_sources.call_args.kwargs["db_funding_lookup"]
        az.db.get_funding_sources.return_value = []  # still empty for intermediate addr
        result = callback("0xintermediate")
        assert result is None

    def test_db_lookup_callback_returns_funding_on_fresh_hit(self):
        """The callback returns WalletFunding list when DB has fresh rows."""
        az = _make_analyzer()
        az.db.get_funding_sources.return_value = []  # initial miss for root
        az.chain.get_funding_sources.return_value = []

        az._get_funding_with_db_cache("0xAAA")

        callback = az.chain.get_funding_sources.call_args.kwargs["db_funding_lookup"]
        # Now simulate fresh DB data for an intermediate address
        az.db.get_funding_sources.return_value = [_fresh_db_row(
            wallet="0xintermediate", sender="0xroot", age_days=1
        )]
        result = callback("0xintermediate")
        assert result is not None
        assert len(result) == 1
        assert result[0].sender_address == "0xroot"

    def test_db_lookup_callback_returns_none_on_stale(self):
        """The callback returns None when DB data is stale."""
        az = _make_analyzer()
        az.db.get_funding_sources.return_value = []
        az.chain.get_funding_sources.return_value = []

        az._get_funding_with_db_cache("0xAAA")

        callback = az.chain.get_funding_sources.call_args.kwargs["db_funding_lookup"]
        az.db.get_funding_sources.return_value = [_stale_db_row(
            wallet="0xintermediate", sender="0xroot"
        )]
        result = callback("0xintermediate")
        assert result is None
