"""Tests for the confluence detector (C filters — layered architecture)."""

from datetime import datetime, timedelta, timezone

from src.analysis.confluence_detector import ConfluenceDetector, _amounts_similar


# ── Fake DB ──────────────────────────────────────────────────

SENDER_A = "0xaaaa000000000000000000000000000000000001"
SENDER_B = "0xbbbb000000000000000000000000000000000002"
SENDER_EXCHANGE = "0xeeee000000000000000000000000000000000003"
SENDER_BRIDGE = "0xdddd000000000000000000000000000000000004"
SENDER_MIXER = "0xcccc000000000000000000000000000000000005"


class FakeDB:
    """In-memory mock of SupabaseClient for funding queries."""

    def __init__(self, funding_rows: dict[str, list[dict]] | None = None):
        # {wallet_address: [funding_row, ...]}
        self._funding = funding_rows or {}

    def get_funding_sources(self, wallet_address: str) -> list[dict]:
        return self._funding.get(wallet_address, [])

    def get_high_fanout_senders(self, min_wallets: int) -> list[str]:
        return []


def _wallet(address: str, direction: str = "YES") -> dict:
    return {"address": address, "direction": direction}


def _funding_row(
    sender: str,
    amount: float = 1000.0,
    is_exchange: bool = False,
    exchange_name: str | None = None,
    is_bridge: bool = False,
    bridge_name: str | None = None,
    is_mixer: bool = False,
    mixer_name: str | None = None,
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
        "is_bridge": is_bridge,
        "bridge_name": bridge_name,
        "is_mixer": is_mixer,
        "mixer_name": mixer_name,
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


# ── Layer 1: C01 / C02 — Direction confluence ────────────────


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

    def test_opposite_direction_not_counted(self):
        """3 wallets NO + 1 wallet YES → C01 counts 3 (NO group), not 4."""
        detector = ConfluenceDetector(FakeDB())
        wallets = [
            _wallet("0x01", "NO"),
            _wallet("0x02", "NO"),
            _wallet("0x03", "NO"),
            _wallet("0x04", "YES"),  # opposite — must NOT be counted
        ]
        results = detector._check_direction_confluence("NO", wallets)
        assert len(results) == 1
        assert results[0].filter_id == "C01"
        assert "3 wallets" in results[0].details


# ── Layer 1 tests (user-requested) ───────────────────────────


class TestCapa1:
    def test_capa1_solo_direccion(self):
        """3 wallets same direction, no funding → only C01 (+10)."""
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB())
        results = detector.detect("mkt1", "YES", wallets)
        ids = {r.filter_id for r in results}
        assert ids == {"C01"}
        assert results[0].points == 10

    def test_capa1_fuerte(self):
        """5 wallets same direction → C02 (+15) instead of C01."""
        wallets = [_wallet(f"0x{i:040x}", "YES") for i in range(5)]
        detector = ConfluenceDetector(FakeDB())
        results = detector.detect("mkt1", "YES", wallets)
        ids = {r.filter_id for r in results}
        assert "C02" in ids
        assert "C01" not in ids
        c02 = [r for r in results if r.filter_id == "C02"][0]
        assert c02.points == 15


# ── Layer 2: C03a-d — Origin type ────────────────────────────


class TestCapa2Exchange:
    def test_capa2_exchange(self):
        """2 wallets funded from same exchange → C03a (+5)."""
        funding = {
            "0x01": [_funding_row(SENDER_EXCHANGE, is_exchange=True, exchange_name="Coinbase")],
            "0x02": [_funding_row(SENDER_EXCHANGE, is_exchange=True, exchange_name="Coinbase")],
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "YES")]
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_origin_layers(wallets, sender_idx, funding)
        assert len(results) == 1
        assert results[0].filter_id == "C03a"
        assert results[0].points == 5
        assert "Coinbase" in results[0].details


class TestCapa2Bridge:
    def test_capa2_bridge(self):
        """2 wallets funded from same bridge → C03b (+20)."""
        funding = {
            "0x01": [_funding_row(SENDER_BRIDGE, is_bridge=True, bridge_name="Hop Protocol")],
            "0x02": [_funding_row(SENDER_BRIDGE, is_bridge=True, bridge_name="Hop Protocol")],
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "YES")]
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_origin_layers(wallets, sender_idx, funding)
        assert len(results) == 1
        assert results[0].filter_id == "C03b"
        assert results[0].points == 20
        assert "Hop Protocol" in results[0].details


class TestCapa2Mixer:
    def test_capa2_mixer(self):
        """2 wallets funded from same mixer → C03c (+30)."""
        funding = {
            "0x01": [_funding_row(SENDER_MIXER, is_mixer=True, mixer_name="Tornado Cash")],
            "0x02": [_funding_row(SENDER_MIXER, is_mixer=True, mixer_name="Tornado Cash")],
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "YES")]
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_origin_layers(wallets, sender_idx, funding)
        assert len(results) == 1
        assert results[0].filter_id == "C03c"
        assert results[0].points == 30
        assert "Tornado Cash" in results[0].details


class TestCapa2Padre:
    def test_capa2_padre(self):
        """2 wallets funded from same unknown sender → C03d (+30)."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_A)],
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "YES")]
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_origin_layers(wallets, sender_idx, funding)
        assert len(results) == 1
        assert results[0].filter_id == "C03d"
        assert results[0].points == 30

    def test_no_trigger_no_shared_sender(self):
        """Each wallet has a different sender → no trigger."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_B)],
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "YES")]
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_origin_layers(wallets, sender_idx, funding)
        assert len(results) == 0


class TestCapa2MultipleTypes:
    def test_exchange_and_padre_both_fire(self):
        """Exchange sender + padre sender → C03a + C03d (additive)."""
        funding = {
            "0x01": [_funding_row(SENDER_EXCHANGE, is_exchange=True, exchange_name="Binance")],
            "0x02": [_funding_row(SENDER_EXCHANGE, is_exchange=True, exchange_name="Binance")],
            "0x03": [_funding_row(SENDER_A)],
            "0x04": [_funding_row(SENDER_A)],
        }
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
            _wallet("0x04", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_origin_layers(wallets, sender_idx, funding)
        ids = {r.filter_id for r in results}
        assert "C03a" in ids
        assert "C03d" in ids
        assert len(results) == 2

    def test_mixer_priority_over_exchange(self):
        """Mixer classification takes priority: is_mixer=True + is_exchange=True → mixer."""
        funding = {
            "0x01": [_funding_row(SENDER_MIXER, is_mixer=True, mixer_name="Tornado Cash",
                                  is_exchange=True, exchange_name="Coinbase")],
            "0x02": [_funding_row(SENDER_MIXER, is_mixer=True, mixer_name="Tornado Cash",
                                  is_exchange=True, exchange_name="Coinbase")],
        }
        wallets = [_wallet("0x01", "YES"), _wallet("0x02", "YES")]
        detector = ConfluenceDetector(FakeDB(funding))
        sender_idx = detector._build_sender_index(funding)
        results = detector._check_origin_layers(wallets, sender_idx, funding)
        assert len(results) == 1
        assert results[0].filter_id == "C03c"  # mixer, not exchange


# ── Layer 3: C05 — Temporal funding ──────────────────────────


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


# ── Layer 3: C06 — Similar funding amounts ───────────────────


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


# ── Layer 4: C07 — Distribution network ──────────────────────


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
        assert results[0].points == 30

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


# ── Full detect flow — integration tests ────────────────────


class TestCapasCompletas:
    def test_capas_completas(self):
        """3 wallets, same direction, shared padre, similar amounts → all layers fire."""
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

        # Layer 1: direction
        assert "C01" in ids

        # Layer 2: padre directo (SENDER_A is not exchange/bridge/mixer)
        assert "C03d" in ids
        # Old C03/C04 should NOT appear
        assert "C03" not in ids
        assert "C04" not in ids

        # Layer 3: similar amounts
        assert "C06" in ids

        # Layer 4: distribution (3 wallets from 1 sender)
        assert "C07" in ids

        # Verify new point values
        points = {r.filter_id: r.points for r in results}
        assert points["C01"] == 10
        assert points["C03d"] == 30
        assert points["C06"] == 10
        assert points["C07"] == 30

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

    def test_exchange_and_padre_layers_additive(self):
        """Exchange sender + padre sender in same market → C03a + C03d both fire."""
        funding = {
            "0x01": [_funding_row(SENDER_EXCHANGE, is_exchange=True, exchange_name="Coinbase")],
            "0x02": [_funding_row(SENDER_EXCHANGE, is_exchange=True, exchange_name="Coinbase")],
            "0x03": [_funding_row(SENDER_A)],
            "0x04": [_funding_row(SENDER_A)],
            "0x05": [_funding_row(SENDER_A)],
        }
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
            _wallet("0x04", "YES"),
            _wallet("0x05", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector.detect("mkt1", "YES", wallets)
        ids = {r.filter_id for r in results}

        assert "C02" in ids    # 5 wallets
        assert "C03a" in ids   # exchange origin
        assert "C03d" in ids   # padre directo
        assert "C07" in ids    # 3+ wallets from SENDER_A


class TestNoduplicarCoord04:
    def test_coord04_and_c03c_can_coexist(self):
        """COORD04 (per-wallet mixer detection) and C03c (confluence mixer)
        are different concepts and can BOTH fire on the same alert.
        COORD04 fires in wallet_analyzer, C03c fires in confluence_detector.
        They should NOT suppress each other.
        """
        # C03c fires when 2+ wallets share a mixer sender
        funding = {
            "0x01": [_funding_row(SENDER_MIXER, is_mixer=True, mixer_name="Tornado Cash")],
            "0x02": [_funding_row(SENDER_MIXER, is_mixer=True, mixer_name="Tornado Cash")],
            "0x03": [_funding_row(SENDER_MIXER, is_mixer=True, mixer_name="Tornado Cash")],
        }
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector.detect("mkt1", "YES", wallets)
        ids = {r.filter_id for r in results}

        assert "C03c" in ids   # confluence mixer detection
        assert "C01" in ids    # 3 wallets same direction
        # COORD04 would fire separately in wallet_analyzer — not tested here
        # but importantly, C03c does NOT check or suppress COORD04


# ── Sender exclusion ────────────────────────────────────────


class TestSenderExclusion:
    def test_polymarket_contracts_excluded(self):
        """Polymarket contracts should be excluded from sender analysis."""
        detector = ConfluenceDetector(FakeDB())
        excluded = detector._build_default_excluded()
        from src.scanner.blockchain_client import POLYMARKET_CONTRACTS
        for contract in POLYMARKET_CONTRACTS:
            assert contract.lower() in excluded

    def test_exchanges_not_excluded(self):
        """Exchanges should NOT be excluded — their shared use is the C03a signal."""
        detector = ConfluenceDetector(FakeDB())
        excluded = detector._build_default_excluded()
        from src import config
        for exchange_addr in config.KNOWN_EXCHANGES:
            assert exchange_addr.lower() not in excluded

    def test_known_infrastructure_excluded(self):
        """Known infrastructure addresses should be excluded from sender analysis."""
        detector = ConfluenceDetector(FakeDB())
        excluded = detector._build_default_excluded()
        from src.config import KNOWN_INFRASTRUCTURE
        for addr in KNOWN_INFRASTRUCTURE:
            assert addr.lower() in excluded


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


# ── group_and_detect ───────────────────────────────────────


class TestGroupAndDetect:
    def test_solo_wallets_each_own_group(self):
        """2 wallets with different senders → 2 groups."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_B)],
        }
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector.group_and_detect("mkt1", "YES", [
            _wallet("0x01", "YES"), _wallet("0x02", "YES"),
        ])
        assert len(results) == 2
        # Each group has 1 wallet
        sizes = sorted(len(g) for g, _ in results)
        assert sizes == [1, 1]

    def test_shared_sender_merges(self):
        """2 wallets sharing a sender → 1 group."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_A)],
        }
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector.group_and_detect("mkt1", "YES", [
            _wallet("0x01", "YES"), _wallet("0x02", "YES"),
        ])
        assert len(results) == 1
        assert len(results[0][0]) == 2

    def test_mixed_groups(self):
        """3 wallets: A+B share sender, C solo → 2 groups."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_A)],
            "0x03": [_funding_row(SENDER_B)],
        }
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector.group_and_detect("mkt1", "YES", [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
        ])
        assert len(results) == 2
        sizes = sorted(len(g) for g, _ in results)
        assert sizes == [1, 2]

    def test_c_filters_scoped_per_group(self):
        """C01 should count only wallets within the group, not globally."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_A)],
            "0x03": [_funding_row(SENDER_A)],
            "0x04": [_funding_row(SENDER_B)],
        }
        wallets = [
            _wallet("0x01", "YES"),
            _wallet("0x02", "YES"),
            _wallet("0x03", "YES"),
            _wallet("0x04", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding))
        results = detector.group_and_detect("mkt1", "YES", wallets)
        assert len(results) == 2

        # Find the group with 3 wallets — should have C01
        for group_wallets, filters in results:
            ids = {f.filter_id for f in filters}
            if len(group_wallets) == 3:
                assert "C01" in ids
            elif len(group_wallets) == 1:
                assert "C01" not in ids

    def test_empty_wallets(self):
        """Empty wallets → empty result."""
        detector = ConfluenceDetector(FakeDB())
        results = detector.group_and_detect("mkt1", "YES", [])
        assert results == []

    def test_last_senders_seen_populated(self):
        """last_senders_seen should be set after group_and_detect."""
        funding = {
            "0x01": [_funding_row(SENDER_A)],
            "0x02": [_funding_row(SENDER_B)],
        }
        detector = ConfluenceDetector(FakeDB(funding))
        detector.group_and_detect("mkt1", "YES", [
            _wallet("0x01", "YES"), _wallet("0x02", "YES"),
        ])
        assert SENDER_A in detector.last_senders_seen
        assert SENDER_B in detector.last_senders_seen
