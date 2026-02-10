"""Tests for the confluence detector (C filters)."""

from datetime import datetime, timedelta, timezone

from src.analysis.confluence_detector import ConfluenceDetector, _amounts_similar


# ── Fake DB ──────────────────────────────────────────────────

SENDER_A = "0xaaaa000000000000000000000000000000000001"
SENDER_B = "0xbbbb000000000000000000000000000000000002"


class FakeDB:
    """In-memory mock of SupabaseClient for funding queries."""

    def __init__(self, funding_rows: dict[str, list[dict]] | None = None):
        # {wallet_address: [funding_row, ...]}
        self._funding = funding_rows or {}

    def get_funding_sources(self, wallet_address: str) -> list[dict]:
        return self._funding.get(wallet_address, [])


def _wallet(address: str, direction: str = "YES") -> dict:
    return {"address": address, "direction": direction}


def _funding_row(
    sender: str,
    amount: float = 1000.0,
    is_exchange: bool = False,
    exchange_name: str | None = None,
    hours_ago: int = 12,
    hop_level: int = 1,
) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {
        "wallet_address": "filled_by_key",
        "sender_address": sender,
        "amount": amount,
        "is_exchange": is_exchange,
        "exchange_name": exchange_name,
        "timestamp": ts,
        "hop_level": hop_level,
    }


# ── Basic init ───────────────────────────────────────────────


class TestConfluenceDetector:
    def test_init(self):
        db = FakeDB()
        detector = ConfluenceDetector(db)
        assert detector.db is db

    def test_empty_wallets_returns_empty(self):
        detector = ConfluenceDetector(FakeDB())
        results = detector.detect("mkt1", "YES", [])
        assert results == []


# ── C01 / C02 — Direction confluence ────────────────────────


class TestDirectionConfluence:
    def test_c01_three_wallets_same_direction(self):
        detector = ConfluenceDetector(FakeDB())
        wallets = [_wallet(f"0x{i:040x}", "YES") for i in range(3)]
        results = detector._check_direction_confluence("YES", wallets)
        assert len(results) == 1
        assert results[0].filter_id == "C01"

    def test_c02_five_wallets_same_direction(self):
        detector = ConfluenceDetector(FakeDB())
        wallets = [_wallet(f"0x{i:040x}", "YES") for i in range(5)]
        results = detector._check_direction_confluence("YES", wallets)
        assert len(results) == 1
        assert results[0].filter_id == "C02"

    def test_c02_replaces_c01_mutually_exclusive(self):
        """C02 fires instead of C01 when 5+ wallets."""
        detector = ConfluenceDetector(FakeDB())
        wallets = [_wallet(f"0x{i:040x}", "YES") for i in range(7)]
        results = detector._check_direction_confluence("YES", wallets)
        assert len(results) == 1
        assert results[0].filter_id == "C02"

    def test_no_trigger_two_wallets(self):
        detector = ConfluenceDetector(FakeDB())
        wallets = [_wallet(f"0x{i:040x}", "YES") for i in range(2)]
        results = detector._check_direction_confluence("YES", wallets)
        assert len(results) == 0

    def test_only_counts_matching_direction(self):
        detector = ConfluenceDetector(FakeDB())
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "NO"),
            _wallet("0x04", "NO"),
        ]
        results = detector._check_direction_confluence("YES", wallets)
        assert len(results) == 0  # only 2 YES


# ── C03 / C04 — Shared funding source ───────────────────────


class TestFundingConfluence:
    def test_c04_same_sender_same_direction(self):
        """2 wallets share sender AND bet same direction → C04."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_A)],
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "YES")]
        detector = ConfluenceDetector(FakeDB(funding))

        results = detector._check_funding_confluence(
            "YES", wallets,
            detector._build_sender_index(funding),
            funding,
        )
        assert len(results) == 1
        assert results[0].filter_id == "C04"

    def test_c03_same_sender_different_direction(self):
        """2 wallets share sender but bet different directions → C03."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_A)],
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "NO")]
        detector = ConfluenceDetector(FakeDB(funding))

        results = detector._check_funding_confluence(
            "YES", wallets,
            detector._build_sender_index(funding),
            funding,
        )
        assert len(results) == 1
        assert results[0].filter_id == "C03"

    def test_no_trigger_no_shared_sender(self):
        """Each wallet has a different sender → no trigger."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_B)],
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "YES")]
        detector = ConfluenceDetector(FakeDB(funding))

        results = detector._check_funding_confluence(
            "YES", wallets,
            detector._build_sender_index(funding),
            funding,
        )
        assert len(results) == 0


# ── C05 — Temporal funding ──────────────────────────────────


class TestTemporalFunding:
    def test_c05_three_exchange_funded_within_4h(self):
        """3 wallets funded from exchanges within 4h, same direction."""
        funding = {
            "0x01": [_funding_row(SENDER_A, is_exchange=True, exchange_name="Coinbase", hours_ago=2)],
            "0x02": [_funding_row(SENDER_B, is_exchange=True, exchange_name="Binance", hours_ago=3)],
            "0x03": [_funding_row(SENDER_A, is_exchange=True, exchange_name="Coinbase", hours_ago=4)],
        }
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector._check_temporal_funding("YES", wallets, funding)
        assert len(results) == 1
        assert results[0].filter_id == "C05"

    def test_no_trigger_spread_too_wide(self):
        """Wallets funded > 4h apart → no trigger."""
        funding = {
            "0x01": [_funding_row(SENDER_A, is_exchange=True, hours_ago=1)],
            "0x02": [_funding_row(SENDER_B, is_exchange=True, hours_ago=10)],
            "0x03": [_funding_row(SENDER_A, is_exchange=True, hours_ago=20)],
        }
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector._check_temporal_funding("YES", wallets, funding)
        assert len(results) == 0

    def test_no_trigger_different_direction(self):
        """Exchange-funded wallets betting different directions → no trigger."""
        funding = {
            "0x01": [_funding_row(SENDER_A, is_exchange=True, hours_ago=1)],
            "0x02": [_funding_row(SENDER_B, is_exchange=True, hours_ago=2)],
            "0x03": [_funding_row(SENDER_A, is_exchange=True, hours_ago=3)],
        }
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "NO"),   # wrong direction
            _wallet("0x03", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector._check_temporal_funding("YES", wallets, funding)
        assert len(results) == 0  # only 2 YES wallets with exchange funding

    def test_no_trigger_not_exchange(self):
        """Funded from non-exchange wallets → no trigger."""
        funding = {
            "0x01": [_funding_row(SENDER_A, is_exchange=False, hours_ago=1)],
            "0x02": [_funding_row(SENDER_B, is_exchange=False, hours_ago=2)],
            "0x03": [_funding_row(SENDER_A, is_exchange=False, hours_ago=3)],
        }
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector._check_temporal_funding("YES", wallets, funding)
        assert len(results) == 0


# ── C06 — Similar funding amounts ───────────────────────────


class TestSimilarAmounts:
    def test_c06_similar_amounts(self):
        """Wallets funded with amounts ±30% from same sender."""
        funding = {
            "0x01": [_funding_row(SENDER_A, amount=1000)],
            "0x02": [_funding_row(SENDER_A, amount=1100)],
        }
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_similar_amounts(sender_idx, funding)
        assert len(results) == 1
        assert results[0].filter_id == "C06"

    def test_no_trigger_very_different_amounts(self):
        """Amounts differ by more than 30% → no trigger."""
        funding = {
            "0x01": [_funding_row(SENDER_A, amount=1000)],
            "0x02": [_funding_row(SENDER_A, amount=5000)],
        }
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_similar_amounts(sender_idx, funding)
        assert len(results) == 0

    def test_amounts_similar_helper(self):
        assert _amounts_similar([1000, 1100, 1200], 0.30) is True
        assert _amounts_similar([1000, 5000], 0.30) is False
        assert _amounts_similar([1000], 0.30) is False
        assert _amounts_similar([], 0.30) is False


# ── C07 — Distribution network ──────────────────────────────


class TestDistributionNetwork:
    def test_c07_one_sender_three_active_wallets(self):
        """Single sender funded 3+ wallets active in the market."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_A)],
            "0x03": [_funding_row(SENDER_A)],
        }
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_distribution_network(wallets, sender_idx)
        assert len(results) == 1
        assert results[0].filter_id == "C07"
        assert results[0].points == 60

    def test_no_trigger_only_two_from_same_sender(self):
        """Only 2 wallets from same sender → below threshold."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_A)],
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "YES")]
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_distribution_network(wallets, sender_idx)
        assert len(results) == 0

    def test_c07_ignores_inactive_wallets(self):
        """Sender funded 4 wallets but only 2 are active in this market."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_A)],
            "0x03": [_funding_row(SENDER_A)],  # funded but not in wallets list
            "0x04": [_funding_row(SENDER_A)],  # funded but not in wallets list
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "YES")]
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_distribution_network(wallets, sender_idx)
        assert len(results) == 0  # only 2 active, need 3


# ── Full detect flow ────────────────────────────────────────


class TestDetectIntegration:
    def test_full_detect_c01_and_c04_and_c06(self):
        """3 wallets, same direction, shared sender, similar amounts."""
        funding = {
            "0x01": [_funding_row(SENDER_A, amount=1000)],
            "0x02": [_funding_row(SENDER_A, amount=1050)],
            "0x03": [_funding_row(SENDER_A, amount=980)],
        }
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector.detect("mkt1", "YES", wallets)
        ids = {r.filter_id for r in results}

        assert "C01" in ids       # 3 wallets same direction
        assert "C04" in ids       # shared sender + same direction
        assert "C06" in ids       # similar amounts
        assert "C07" in ids       # 1 sender → 3 active wallets
        assert "C03" not in ids   # replaced by C04

    def test_full_detect_no_funding_data(self):
        """3 wallets same direction but no funding data → only C01."""
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB())
        results = detector.detect("mkt1", "YES", wallets)
        ids = {r.filter_id for r in results}
        assert ids == {"C01"}

    def test_full_detect_strong_confluence(self):
        """5 wallets → C02 instead of C01."""
        wallets = [_wallet(f"0x{i:040x}", "YES") for i in range(5)]
        detector = ConfluenceDetector(FakeDB())
        results = detector.detect("mkt1", "YES", wallets)
        ids = {r.filter_id for r in results}
        assert "C02" in ids
        assert "C01" not in ids


# ── detect_funding_links ─────────────────────────────────────


class TestDetectFundingLinks:
    def test_returns_link_for_shared_sender(self):
        funding = {
            "0x01": [_funding_row(SENDER_A, amount=500, hours_ago=2)],
            "0x02": [_funding_row(SENDER_A, amount=520, hours_ago=3)],
        }
        detector = ConfluenceDetector(FakeDB(funding))
        links = detector.detect_funding_links(["0x01", "0x02"])
        assert len(links) == 1
        assert links[0].sender == SENDER_A
        assert links[0].count == 2
        assert links[0].similar_amounts is True

    def test_no_link_for_different_senders(self):
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_B)],
        }
        detector = ConfluenceDetector(FakeDB(funding))
        links = detector.detect_funding_links(["0x01", "0x02"])
        assert len(links) == 0
