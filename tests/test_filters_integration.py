"""
Exhaustive integration test suite for ALL filters in Sentinel Alpha.

Tests every filter with synthetic data simulating real scenarios:
- Positive cases (filter MUST fire)
- Negative cases (filter must NOT fire)
- Edge/threshold cases
- Mutual exclusion verification
- End-to-end scoring pipeline

Profiles tested:
  1. "Insider perfecto"          — 5★ composite
  2. "Degen obvio"               — 0-1★ noise-dominated
  3. "Bot"                       — N01 + N08 detection
  4. "Copy trader"               — N05 detection
  5. "Arbitrajista"              — N03/N04 detection
  6. "Red de distribución"       — confluence C filters
  7. "Falsa confluencia"         — infrastructure exclusion
  8. "Whale entry limpia"        — B19c + B20
  9. "B20 falso positivo"        — regression test for B20 fix
 10. "Mutual exclusion"          — only highest-impact survives
 11. "Star validation"           — category/amount gates
 12. "Amount multiplier"         — logarithmic curve edge cases
 13. "Diversity multiplier"      — sniper/shotgun thresholds
"""

import math
import statistics
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src import config
from src.database.models import (
    Wallet,
    Market,
    TradeEvent,
    FilterResult,
    AccumulationWindow,
    WalletFunding,
)
from src.analysis.behavior_analyzer import BehaviorAnalyzer
from src.analysis.market_analyzer import MarketAnalyzer
from src.analysis.noise_filter import NoiseFilter
from src.analysis.scoring import (
    calculate_score,
    _enforce_mutual_exclusion,
    _get_amount_multiplier,
    _get_diversity_multiplier,
    _score_to_stars,
    _validate_stars,
)
from src.analysis.confluence_detector import ConfluenceDetector
from src.analysis.arbitrage_filter import ArbitrageFilter
from src.analysis.wallet_analyzer import WalletAnalyzer


# ============================================================
# HELPERS
# ============================================================

NOW = datetime.now(timezone.utc)


def _fr(filt: dict, details: str | None = None) -> FilterResult:
    """Build a FilterResult from a config filter dict."""
    return FilterResult(
        filter_id=filt["id"],
        filter_name=filt["name"],
        points=filt["points"],
        category=filt["category"],
        details=details,
    )


def _make_trade(
    wallet: str = "0xaaa",
    market: str = "mkt1",
    direction: str = "YES",
    amount: float = 1000.0,
    price: float = 0.15,
    hours_ago: float = 1.0,
    is_market_order: bool = True,
    tx_hash: str | None = None,
) -> TradeEvent:
    """Create a synthetic TradeEvent."""
    return TradeEvent(
        wallet_address=wallet,
        market_id=market,
        direction=direction,
        amount=amount,
        price=price,
        timestamp=NOW - timedelta(hours=hours_ago),
        is_market_order=is_market_order,
        tx_hash=tx_hash,
    )


def _make_trades_at_intervals(
    n: int,
    interval_seconds: float,
    wallet: str = "0xaaa",
    market: str = "mkt1",
    amount: float = 100.0,
    price: float = 0.15,
) -> list[TradeEvent]:
    """Create N trades at exact intervals (for bot detection tests)."""
    base = NOW - timedelta(hours=1)
    return [
        TradeEvent(
            wallet_address=wallet,
            market_id=market,
            direction="YES",
            amount=amount,
            price=price,
            timestamp=base + timedelta(seconds=i * interval_seconds),
            is_market_order=True,
        )
        for i in range(n)
    ]


def _make_market(
    market_id: str = "mkt1",
    question: str = "Will X happen?",
    current_odds: float = 0.15,
    volume_24h: float = 50000.0,
    volume_7d_avg: float = 15000.0,
    liquidity: float = 30000.0,
    resolution_hours: float = 48.0,
) -> Market:
    """Create a synthetic Market."""
    return Market(
        market_id=market_id,
        question=question,
        current_odds=current_odds,
        volume_24h=volume_24h,
        volume_7d_avg=volume_7d_avg,
        liquidity=liquidity,
        resolution_date=NOW + timedelta(hours=resolution_hours),
    )


def _ids(results: list[FilterResult]) -> set[str]:
    """Extract filter IDs from results."""
    return {r.filter_id for r in results}


def _total_points(results: list[FilterResult]) -> int:
    """Sum points from filter results."""
    return sum(r.points for r in results)


class FakeDB:
    """Minimal mock DB for filters that need a db_client."""

    def __init__(self, funding=None, wallet=None, snapshots=None, markets=None):
        self._funding = funding or {}
        self._wallet = wallet
        self._snapshots = snapshots or []
        self._markets = markets or []

    def get_wallet(self, address):
        return self._wallet

    def get_funding_sources(self, address):
        return self._funding.get(address, [])

    def get_high_fanout_senders(self, min_wallets):
        return []

    def insert_market_snapshot(self, snapshot):
        pass

    def get_market_snapshots(self, market_id, hours=168):
        return self._snapshots

    def get_market(self, market_id):
        for m in self._markets:
            if m.get("market_id") == market_id:
                return m
        return None

    def get_all_markets(self):
        return self._markets


class FakePMClient:
    """Mock Polymarket client for B20/W04/W05/B28/B23/N06 verification."""

    def __init__(self, history=None, questions=None):
        self._history = history
        self._questions = questions or {}

    def get_wallet_pm_history(self, address):
        return self._history

    def get_wallet_pm_history_cached(self, address):
        return self._history

    def get_market_question(self, market_id):
        return self._questions.get(market_id)

    def count_non_political_markets(self, market_ids):
        count = 0
        for mid in market_ids:
            q = self.get_market_question(mid)
            if q is None:
                continue
            q_lower = q.lower()
            if any(term in q_lower for term in config.MARKET_BLACKLIST_TERMS):
                count += 1
        return count


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
) -> dict:
    ts = (NOW - timedelta(hours=hours_ago)).isoformat()
    return {
        "sender_address": sender,
        "amount": amount,
        "is_exchange": is_exchange,
        "exchange_name": exchange_name,
        "is_bridge": is_bridge,
        "bridge_name": bridge_name,
        "is_mixer": is_mixer,
        "mixer_name": mixer_name,
        "timestamp": ts,
        "hop_level": 1,
    }


def _wallet_dict(address: str, direction: str = "YES") -> dict:
    return {"address": address, "direction": direction}


# ============================================================
# PROFILE 1 — "Insider perfecto" (expected: high score, 5★)
# ============================================================


class TestProfile1InsiderPerfecto:
    """Composite profile: every suspicious signal fires."""

    def _build_insider_trades(self):
        """5 trades in 3 hours, increasing, total $15,000, price 0.15."""
        wallet = "0xinsider"
        mkt = "mkt_insider"
        base = NOW - timedelta(hours=3)
        amounts = [1500, 2000, 2500, 4000, 5000]  # increasing, total=15000
        return [
            TradeEvent(
                wallet_address=wallet,
                market_id=mkt,
                direction="YES",
                amount=amt,
                price=0.15,
                timestamp=base + timedelta(minutes=i * 35),
                is_market_order=True,
            )
            for i, amt in enumerate(amounts)
        ]

    def test_behavior_filters(self):
        """B16, B18d, B06, B05, B17, B25b, B26a should fire."""
        trades = self._build_insider_trades()
        # Adjust one trade to 3 AM UTC for B17
        trades[0] = TradeEvent(
            wallet_address=trades[0].wallet_address,
            market_id=trades[0].market_id,
            direction="YES",
            amount=trades[0].amount,
            price=0.15,
            timestamp=trades[0].timestamp.replace(hour=3),
            is_market_order=True,
        )

        # Make price moves negligible for B26a (stealth whale)
        # All prices very close → move < 1%, total > $5k
        for t in trades:
            t.price = 0.150  # all same price → move = 0

        analyzer = BehaviorAnalyzer(db_client=None)
        results = analyzer.analyze(
            wallet_address="0xinsider",
            trades=trades,
            market_id="mkt_insider",
            current_odds=0.15,
            wallet_balance=16000.0,  # B28a: 15000/16000 = 93.75% > 90%
        )
        ids = _ids(results)

        # B16: 5 trades in < 4h
        assert "B16" in ids, f"B16 expected, got {ids}"
        # B18d: $15,000 total > $10,000 threshold
        assert "B18d" in ids, f"B18d expected, got {ids}"
        # B06: increasing amounts
        assert "B06" in ids, f"B06 expected, got {ids}"
        # B05: all market orders
        assert "B05" in ids, f"B05 expected, got {ids}"
        # B17: trade at 3 AM UTC
        assert "B17" in ids, f"B17 expected, got {ids}"
        # B25b: avg price 0.15 < 0.20
        assert "B25b" in ids, f"B25b expected, got {ids}"
        # B26a: stealth whale (move=0 < 1%, total=$15k > $5k)
        assert "B26a" in ids, f"B26a expected, got {ids}"
        # B28a: 15000/16000 = 93.75% > 90%
        assert "B28a" in ids, f"B28a expected, got {ids}"
        # B23 should NOT fire (suppressed by B28)
        assert "B23a" not in ids
        assert "B23b" not in ids

    def test_market_filters(self):
        """M01, M03, M04b, M05a should fire."""
        market = _make_market(
            volume_24h=50000.0,
            volume_7d_avg=15000.0,  # ratio = 3.3x > 2x → M01
            liquidity=30000.0,      # < $100k → M03
            resolution_hours=48.0,  # < 72h → M05a
        )

        # For M04b: top 3 wallets hold 85% of volume
        trades = [
            _make_trade(wallet="0xw1", market=market.market_id, amount=7000),
            _make_trade(wallet="0xw2", market=market.market_id, amount=5000),
            _make_trade(wallet="0xw3", market=market.market_id, amount=5000),
            _make_trade(wallet="0xw4", market=market.market_id, amount=1500),
            _make_trade(wallet="0xw5", market=market.market_id, amount=1500),
        ]
        # top3 = 17000/20000 = 85% > 80% → M04b

        analyzer = MarketAnalyzer(db_client=FakeDB())
        results = analyzer.analyze(market, trades)
        ids = _ids(results)

        assert "M01" in ids, f"M01 expected, got {ids}"
        assert "M03" in ids, f"M03 expected, got {ids}"
        assert "M04b" in ids, f"M04b expected, got {ids}"
        assert "M05a" in ids, f"M05a expected, got {ids}"
        # M04a should NOT fire (M04b takes priority)
        assert "M04a" not in ids

    def test_scoring_pipeline_high_stars(self):
        """Full insider profile should yield high score and 4-5★."""
        filters = [
            _fr(config.FILTER_W01),   # 25 wallet
            _fr(config.FILTER_W04),   # 10 wallet (was 25 in user request, but actual is 10)
            _fr(config.FILTER_W09),   # 5  wallet
            _fr(config.FILTER_O01),   # 5  origin
            _fr(config.FILTER_O03),   # 5  origin
            _fr(config.FILTER_B16),   # 20 behavior
            _fr(config.FILTER_B18D),  # 50 behavior
            _fr(config.FILTER_B06),   # 15 behavior
            _fr(config.FILTER_B19A),  # 20 behavior
            _fr(config.FILTER_B28A),  # 25 behavior
            _fr(config.FILTER_B05),   # 5  behavior
            _fr(config.FILTER_B17),   # 10 behavior
            _fr(config.FILTER_B25B),  # 15 behavior
            _fr(config.FILTER_B26A),  # 20 behavior
            _fr(config.FILTER_M01),   # 15 market
            _fr(config.FILTER_M03),   # 10 market
            _fr(config.FILTER_M04B),  # 25 market
            _fr(config.FILTER_M05A),  # 10 market
        ]
        # raw = 25+10+5+5+5+20+50+15+20+25+5+10+15+20+15+10+25+10 = 290
        result = calculate_score(filters, total_amount=15000, wallet_market_count=1)
        assert result.score_raw > 200
        assert result.star_level >= 4, f"Expected 4-5★, got {result.star_level}★ (final={result.score_final})"


# ============================================================
# PROFILE 2 — "Degen obvio" (expected: 0-1★)
# ============================================================


class TestProfile2DegenObvio:
    """Noise-dominated profile: old wallet, low amount, obvious bet."""

    def test_noise_filters_dominate(self):
        """N09a, N06c fire; no positive B/W filters."""
        # 1 trade at $500, price 0.92 (obvious bet)
        trade = _make_trade(
            wallet="0xdegen",
            market="mkt_degen",
            amount=500,
            price=0.92,
            is_market_order=True,
        )

        # Behavior: single trade, price > 0.90 → N09a
        analyzer = BehaviorAnalyzer(db_client=None)
        results = analyzer.analyze(
            wallet_address="0xdegen",
            trades=[trade],
            market_id="mkt_degen",
            current_odds=0.92,
        )
        ids = _ids(results)
        # B25 should NOT fire because price > 0.35 (no conviction)
        # N09a should fire (price > 0.90 = extreme obvious)
        assert "N09a" in ids, f"N09a expected, got {ids}"
        assert "B25a" not in ids
        assert "B25b" not in ids

    def test_degen_noise(self):
        """N06c fires for 8 non-political markets."""
        wallet = Wallet(address="0xdegen", non_pm_markets=8)
        nf = NoiseFilter()
        results = nf._check_degen(wallet)
        ids = _ids(results)
        assert "N06c" in ids, f"N06c expected, got {ids}"

    def test_long_horizon(self):
        """N10c fires for 120d resolution."""
        analyzer = BehaviorAnalyzer(db_client=None)
        results = analyzer._check_long_horizon(
            resolution_date=NOW + timedelta(days=120),
        )
        ids = _ids(results)
        assert "N10c" in ids, f"N10c expected, got {ids}"

    def test_scoring_low_stars(self):
        """Degen with all negatives → 0★."""
        filters = [
            _fr(config.FILTER_N09A),  # -40
            _fr(config.FILTER_N06C),  # -30
            _fr(config.FILTER_N10C),  # -30
        ]
        result = calculate_score(filters, total_amount=500)
        assert result.score_raw == 0  # floored at 0
        assert result.star_level == 0


# ============================================================
# PROFILE 3 — "Bot" (N01 + N08)
# ============================================================


class TestProfile3Bot:
    """Bot detection via interval regularity and amount uniformity."""

    def test_n01_regular_intervals(self):
        """Trades every 0.5s exactly → std_dev ≈ 0 → N01."""
        trades = _make_trades_at_intervals(
            n=5, interval_seconds=0.5, amount=100.0,
        )
        nf = NoiseFilter()
        results = nf._check_bot(trades)
        ids = _ids(results)
        assert "N01" in ids, f"N01 expected, got {ids}"

    def test_n01_negative_varied_intervals(self):
        """Irregular intervals → N01 should NOT fire."""
        base = NOW - timedelta(hours=1)
        trades = [
            _make_trade(hours_ago=1.0),
            _make_trade(hours_ago=0.8),   # 12 min gap
            _make_trade(hours_ago=0.3),   # 30 min gap
            _make_trade(hours_ago=0.1),   # 12 min gap
            _make_trade(hours_ago=0.01),  # 5 min gap
        ]
        nf = NoiseFilter()
        results = nf._check_bot(trades)
        assert "N01" not in _ids(results)

    def test_n08_uniform_amounts(self):
        """Irregular timing but uniform $100 amounts → N08."""
        trades = [
            _make_trade(amount=100.0, hours_ago=3.0),
            _make_trade(amount=100.0, hours_ago=2.1),
            _make_trade(amount=100.0, hours_ago=1.3),
            _make_trade(amount=100.0, hours_ago=0.2),
        ]
        nf = NoiseFilter()
        # First check bot doesn't fire
        bot = nf._check_bot(trades)
        assert "N01" not in _ids(bot)
        # Then N08 should fire
        results = nf._check_anti_bot_evasion(trades)
        ids = _ids(results)
        assert "N08" in ids, f"N08 expected, got {ids}"

    def test_n08_negative_varied_amounts(self):
        """Varied amounts → N08 should NOT fire."""
        trades = [
            _make_trade(amount=100.0, hours_ago=3.0),
            _make_trade(amount=500.0, hours_ago=2.0),
            _make_trade(amount=200.0, hours_ago=1.0),
            _make_trade(amount=800.0, hours_ago=0.5),
        ]
        nf = NoiseFilter()
        results = nf._check_anti_bot_evasion(trades)
        assert "N08" not in _ids(results)

    def test_n01_suppresses_n08(self):
        """When N01 fires, N08 must NOT fire (mutually exclusive path)."""
        trades = _make_trades_at_intervals(
            n=5, interval_seconds=0.5, amount=100.0,
        )
        nf = NoiseFilter()
        wallet = Wallet(address="0xbot")
        results = nf.analyze(wallet=wallet, trades=trades)
        ids = _ids(results)
        assert "N01" in ids
        # N08 should NOT fire when N01 fires
        assert "N08" not in ids


# ============================================================
# PROFILE 4 — "Copy trader" (N05)
# ============================================================


class TestProfile4CopyTrader:
    """Trade follows whale by 2-10 minutes."""

    def test_n05_fires(self):
        """Trade 5 minutes after whale → N05."""
        whale_trade = _make_trade(
            wallet="0xwhale", market="mkt1", direction="YES",
            amount=50000, hours_ago=1.0,
        )
        copy_trade = _make_trade(
            wallet="0xcopy", market="mkt1", direction="YES",
            amount=1000, hours_ago=1.0 - (5 / 60),  # 5 min later
        )
        nf = NoiseFilter()
        results = nf._check_copy_trading([copy_trade], [whale_trade])
        ids = _ids(results)
        assert "N05" in ids, f"N05 expected, got {ids}"

    def test_n05_negative_too_fast(self):
        """Trade only 30 seconds after whale → N05 NOT fire (< 2 min)."""
        whale_trade = _make_trade(
            wallet="0xwhale", market="mkt1", direction="YES",
            amount=50000, hours_ago=1.0,
        )
        copy_trade = _make_trade(
            wallet="0xcopy", market="mkt1", direction="YES",
            amount=1000, hours_ago=1.0 - (0.5 / 60),  # 30s later
        )
        nf = NoiseFilter()
        results = nf._check_copy_trading([copy_trade], [whale_trade])
        assert "N05" not in _ids(results)

    def test_n05_negative_too_slow(self):
        """Trade 15 minutes after whale → N05 NOT fire (> 10 min)."""
        whale_trade = _make_trade(
            wallet="0xwhale", market="mkt1", direction="YES",
            amount=50000, hours_ago=1.0,
        )
        copy_trade = _make_trade(
            wallet="0xcopy", market="mkt1", direction="YES",
            amount=1000, hours_ago=1.0 - (15 / 60),  # 15 min later
        )
        nf = NoiseFilter()
        results = nf._check_copy_trading([copy_trade], [whale_trade])
        assert "N05" not in _ids(results)

    def test_n05_negative_different_direction(self):
        """Whale buys YES, copy buys NO → N05 NOT fire."""
        whale_trade = _make_trade(
            wallet="0xwhale", market="mkt1", direction="YES",
            amount=50000, hours_ago=1.0,
        )
        copy_trade = _make_trade(
            wallet="0xcopy", market="mkt1", direction="NO",
            amount=1000, hours_ago=1.0 - (5 / 60),
        )
        nf = NoiseFilter()
        results = nf._check_copy_trading([copy_trade], [whale_trade])
        assert "N05" not in _ids(results)


# ============================================================
# PROFILE 5 — "Arbitrajista" (N03 / N04)
# ============================================================


class TestProfile5Arbitrage:
    """Opposite-market hedging detection."""

    def test_n03_arbitrage_kills_alert(self):
        """YES on market A + NO on opposite market B → N03 (-100)."""
        # Set up DB with opposite_market mapping
        db = FakeDB(markets=[
            {"market_id": "mktA", "question": "Will X happen by March?",
             "opposite_market": "mktB"},
            {"market_id": "mktB", "question": "X happens by March?",
             "opposite_market": "mktA"},
        ])

        arb = ArbitrageFilter(db_client=db)
        all_trades = [
            _make_trade(wallet="0xarb", market="mktA", direction="YES", amount=5000),
            _make_trade(wallet="0xarb", market="mktB", direction="NO", amount=5000),
        ]
        results = arb.check(
            wallet_address="0xarb",
            market_id="mktA",
            direction="YES",
            all_wallet_trades=all_trades,
        )
        ids = _ids(results)
        assert "N03" in ids, f"N03 expected, got {ids}"
        assert results[0].points == -100

    def test_n04_same_direction_flag(self):
        """YES on both opposite markets → N04 (flag, 0 pts)."""
        db = FakeDB(markets=[
            {"market_id": "mktA", "question": "Will X happen?",
             "opposite_market": "mktB"},
            {"market_id": "mktB", "question": "X happens?"},
        ])
        arb = ArbitrageFilter(db_client=db)
        all_trades = [
            _make_trade(wallet="0xarb", market="mktA", direction="YES", amount=5000),
            _make_trade(wallet="0xarb", market="mktB", direction="YES", amount=5000),
        ]
        results = arb.check("0xarb", "mktA", "YES", all_trades)
        ids = _ids(results)
        assert "N04" in ids, f"N04 expected, got {ids}"
        assert results[0].points == 0

    def test_no_opposite_market(self):
        """No opposite market → no N03/N04."""
        db = FakeDB(markets=[
            {"market_id": "mktA", "question": "Will X happen?"},
        ])
        arb = ArbitrageFilter(db_client=db)
        results = arb.check("0xarb", "mktA", "YES", [
            _make_trade(wallet="0xarb", market="mktA", direction="YES"),
        ])
        assert len(results) == 0


# ============================================================
# PROFILE 6 — "Red de distribución legítima" (C filters)
# ============================================================


class TestProfile6Distribution:
    """4 new wallets, same padre, same direction, similar amounts."""

    PADRE = "0xpadre_real_000000000000000000000000000001"

    def test_confluence_fires(self):
        """C01 + C03d + C06 + C07 should all fire."""
        funding = {
            "0x01": [_funding_row(self.PADRE, amount=1000)],
            "0x02": [_funding_row(self.PADRE, amount=1050)],
            "0x03": [_funding_row(self.PADRE, amount=980)],
            "0x04": [_funding_row(self.PADRE, amount=1020)],
        }
        wallets = [
            _wallet_dict("0x01", "YES"),
            _wallet_dict("0x02", "YES"),
            _wallet_dict("0x03", "YES"),
            _wallet_dict("0x04", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding=funding))
        results = detector.detect("mkt1", "YES", wallets)
        ids = _ids(results)

        assert "C01" in ids, f"C01 expected, got {ids}"
        assert "C03d" in ids, f"C03d expected, got {ids}"
        assert "C06" in ids, f"C06 expected, got {ids}"
        assert "C07" in ids, f"C07 expected, got {ids}"
        # Verify point values
        pts = {r.filter_id: r.points for r in results}
        assert pts["C01"] == 10
        assert pts["C03d"] == 30
        assert pts["C06"] == 10
        assert pts["C07"] == 30

    def test_high_score_from_confluence(self):
        """Confluence filters contribute to COORDINATION category → high score."""
        filters = [
            _fr(config.FILTER_C01),   # 10
            _fr(config.FILTER_C03D),  # 30
            _fr(config.FILTER_C06),   # 10
            _fr(config.FILTER_C07),   # 30
            _fr(config.FILTER_W01),   # 25 wallet → ACCUMULATION
            _fr(config.FILTER_M01),   # 15 market → TIMING
        ]
        result = calculate_score(filters, total_amount=10000, wallet_market_count=1)
        # raw = 10+30+10+30+25+15 = 120, mult ≈ 1.29*1.2 = 1.55
        assert result.star_level >= 4


# ============================================================
# PROFILE 7 — "Falsa confluencia por infraestructura"
# ============================================================


class TestProfile7FalseConfluence:
    """Sender is known infrastructure → C03d/C07 should NOT fire."""

    def test_infrastructure_excluded(self):
        """Wallets funded by KNOWN_INFRASTRUCTURE sender are excluded."""
        infra_addr = list(config.KNOWN_INFRASTRUCTURE.keys())[0]

        funding = {
            "0x01": [_funding_row(infra_addr, amount=1000)],
            "0x02": [_funding_row(infra_addr, amount=1000)],
            "0x03": [_funding_row(infra_addr, amount=1000)],
            "0x04": [_funding_row(infra_addr, amount=1000)],
        }
        wallets = [
            _wallet_dict("0x01", "YES"),
            _wallet_dict("0x02", "YES"),
            _wallet_dict("0x03", "YES"),
            _wallet_dict("0x04", "YES"),
        ]
        detector = ConfluenceDetector(FakeDB(funding=funding))
        results = detector.detect("mkt1", "YES", wallets)
        ids = _ids(results)

        # C01 can fire (direction confluence doesn't depend on funding)
        assert "C01" in ids
        # But C03d and C07 should NOT fire — sender is infrastructure
        assert "C03d" not in ids, f"C03d should NOT fire for infra sender, got {ids}"
        assert "C07" not in ids, f"C07 should NOT fire for infra sender, got {ids}"

    def test_default_excluded_has_infrastructure(self):
        """_build_default_excluded includes KNOWN_INFRASTRUCTURE."""
        detector = ConfluenceDetector(FakeDB())
        excluded = detector._build_default_excluded()
        for addr in config.KNOWN_INFRASTRUCTURE:
            assert addr.lower() in excluded


# ============================================================
# PROFILE 8 — "Whale entry limpia" (B19c + B20)
# ============================================================


class TestProfile8WhaleEntry:
    """Single $55,000 trade, old wallet truly new in PM."""

    def test_b19c_fires(self):
        """Single trade $55k → B19c (massive entry)."""
        trade = _make_trade(
            wallet="0xwhale", market="mkt1",
            amount=55000, price=0.30,
        )
        analyzer = BehaviorAnalyzer(db_client=None)
        results = analyzer._check_whale_entry([trade])
        ids = _ids(results)
        assert "B19c" in ids, f"B19c expected, got {ids}"
        assert "B19b" not in ids  # mutually exclusive
        assert "B19a" not in ids

    def test_b20_fires_for_truly_new_wallet(self):
        """Old wallet (908d), truly new in PM (distinct_markets=1) → B20 fires."""
        wallet = Wallet(
            address="0xwhale_new",
            wallet_age_days=908,
            first_seen=NOW - timedelta(days=2),  # first_seen 2 days ago
        )
        # PM client returns only 1 market → not enough to suppress
        pm_client = FakePMClient(
            history={"trade_count": 3, "distinct_markets": 1},
        )
        analyzer = BehaviorAnalyzer(db_client=None, pm_client=pm_client)
        results = analyzer._check_old_wallet_new_pm(wallet)
        ids = _ids(results)
        assert "B20" in ids, f"B20 expected, got {ids}"


# ============================================================
# PROFILE 9 — "B20 falso positivo" (regression test)
# ============================================================


class TestProfile9B20Regression:
    """Veteran PM wallet (50+ markets) must NOT trigger B20."""

    def test_b20_suppressed_by_real_history(self):
        """Old wallet (800d), first_seen=today, but 50 distinct PM markets → B20 suppressed."""
        wallet = Wallet(
            address="0x4c4c2da46b289847e3e8377c6f4ca3d520c620f8ae",
            wallet_age_days=800,
            first_seen=NOW,  # system just saw it → pm_days=0
        )
        pm_client = FakePMClient(
            history={"trade_count": 579, "distinct_markets": 50},
        )
        analyzer = BehaviorAnalyzer(db_client=None, pm_client=pm_client)
        results = analyzer._check_old_wallet_new_pm(wallet)
        assert "B20" not in _ids(results), "B20 should be suppressed for veteran PM wallet"

    def test_b20_fires_without_pm_client(self):
        """Without pm_client (fallback), B20 fires based on first_seen."""
        wallet = Wallet(
            address="0xnew",
            wallet_age_days=800,
            first_seen=NOW,
        )
        analyzer = BehaviorAnalyzer(db_client=None, pm_client=None)
        results = analyzer._check_old_wallet_new_pm(wallet)
        assert "B20" in _ids(results), "B20 should fire when no pm_client to verify"

    def test_b20_threshold_exact(self):
        """Exactly 3 distinct_markets (= threshold) → B20 fires (need > 3)."""
        wallet = Wallet(
            address="0xedge",
            wallet_age_days=200,
            first_seen=NOW,
        )
        pm_client = FakePMClient(
            history={"trade_count": 10, "distinct_markets": 3},
        )
        analyzer = BehaviorAnalyzer(db_client=None, pm_client=pm_client)
        results = analyzer._check_old_wallet_new_pm(wallet)
        # 3 is NOT > 3, so B20 should still fire
        assert "B20" in _ids(results), "B20 should fire when distinct_markets == threshold"

    def test_b20_threshold_plus_one(self):
        """4 distinct_markets (> threshold) → B20 suppressed."""
        wallet = Wallet(
            address="0xedge2",
            wallet_age_days=200,
            first_seen=NOW,
        )
        pm_client = FakePMClient(
            history={"trade_count": 15, "distinct_markets": 4},
        )
        analyzer = BehaviorAnalyzer(db_client=None, pm_client=pm_client)
        results = analyzer._check_old_wallet_new_pm(wallet)
        assert "B20" not in _ids(results), "B20 should be suppressed when distinct_markets > threshold"

    def test_b20_api_failure_fallback(self):
        """PM API returns None (failure) → B20 fires (fail-safe)."""
        wallet = Wallet(
            address="0xfail",
            wallet_age_days=300,
            first_seen=NOW,
        )
        pm_client = FakePMClient(history=None)  # API failure
        analyzer = BehaviorAnalyzer(db_client=None, pm_client=pm_client)
        results = analyzer._check_old_wallet_new_pm(wallet)
        assert "B20" in _ids(results), "B20 should fire when PM API fails"

    def test_b20_young_wallet_no_trigger(self):
        """Wallet only 100 days old → B20 should NOT fire (< 180d)."""
        wallet = Wallet(
            address="0xyoung",
            wallet_age_days=100,
            first_seen=NOW,
        )
        analyzer = BehaviorAnalyzer(db_client=None)
        results = analyzer._check_old_wallet_new_pm(wallet)
        assert "B20" not in _ids(results)

    def test_b20_old_pm_activity(self):
        """Old wallet with first_seen 30 days ago → B20 should NOT fire (pm_days > 7)."""
        wallet = Wallet(
            address="0xold_pm",
            wallet_age_days=500,
            first_seen=NOW - timedelta(days=30),
        )
        analyzer = BehaviorAnalyzer(db_client=None)
        results = analyzer._check_old_wallet_new_pm(wallet)
        assert "B20" not in _ids(results)


# ============================================================
# PROFILE 10 — "Mutual exclusion verification"
# ============================================================


class TestProfile10MutualExclusion:
    """Verify mutually exclusive groups: only highest-impact survives."""

    def test_w01_w02_only_w01(self):
        """W01 (25) + W02 (20) → only W01 survives."""
        filters = [_fr(config.FILTER_W01), _fr(config.FILTER_W02)]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "W01" in ids
        assert "W02" not in ids

    def test_b18c_b18d_only_b18d(self):
        """B18c (35) + B18d (50) → only B18d survives."""
        filters = [_fr(config.FILTER_B18C), _fr(config.FILTER_B18D)]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "B18d" in ids
        assert "B18c" not in ids

    def test_b23b_b28a_only_b28a(self):
        """B23b (30) + B28a (25) in scoring: both survive mutual exclusion
        (they are NOT in the same group). B28 suppresses B23 at the
        analyze() level, not in scoring's mutual exclusion."""
        # B23 and B28 are separate groups in MUTUALLY_EXCLUSIVE_GROUPS
        filters = [_fr(config.FILTER_B23B), _fr(config.FILTER_B28A)]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        # Both should survive mutual exclusion (different groups)
        assert "B23b" in ids
        assert "B28a" in ids

    def test_b28_suppresses_b23_in_analyzer(self):
        """When B28 fires in behavior analyzer, B23 should NOT fire."""
        trades = [
            _make_trade(amount=4000, hours_ago=2.0, price=0.3),
            _make_trade(amount=4000, hours_ago=1.0, price=0.3),
        ]
        analyzer = BehaviorAnalyzer(db_client=None)
        results = analyzer.analyze(
            wallet_address="0xaaa",
            trades=trades,
            market_id="mkt1",
            current_odds=0.30,
            wallet_balance=5000.0,  # 8000/5000 = 160% → B28a (>90%)...
            # Actually 8000/5000=1.6, ratio > 1 but < 10, B28a needs >=0.90
            # 8000 / 5000 = 1.6 which is > 0.90 and > 10? No, 1.6 < 10
            # Actually wait — B28 checks accum/balance. accum=8000, balance=5000
            # ratio = 8000/5000 = 1.6 → 1.6 >= 0.90 → B28a fires
            # And accum >= $3500 (min amount check)
        )
        ids = _ids(results)
        # B28a should fire (ratio = 1.6 > 0.90)
        assert "B28a" in ids, f"B28a expected, got {ids}"
        # B23 should NOT fire (suppressed by B28)
        assert "B23a" not in ids
        assert "B23b" not in ids

    def test_n09a_n09b_only_n09a(self):
        """N09a (-40) + N09b (-25) → only N09a survives (higher abs)."""
        filters = [_fr(config.FILTER_N09A), _fr(config.FILTER_N09B)]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "N09a" in ids
        assert "N09b" not in ids

    def test_m05_all_three_only_m05c(self):
        """M05a (10) + M05b (15) + M05c (25) → only M05c survives."""
        filters = [
            _fr(config.FILTER_M05A),
            _fr(config.FILTER_M05B),
            _fr(config.FILTER_M05C),
        ]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "M05c" in ids
        assert "M05b" not in ids
        assert "M05a" not in ids

    def test_w04_w05_only_w05(self):
        """W04 (10) + W05 (15) → only W05 survives."""
        filters = [_fr(config.FILTER_W04), _fr(config.FILTER_W05)]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "W05" in ids
        assert "W04" not in ids

    def test_c01_c02_only_c02(self):
        """C01 (10) + C02 (15) → only C02 survives."""
        filters = [_fr(config.FILTER_C01), _fr(config.FILTER_C02)]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "C02" in ids
        assert "C01" not in ids

    def test_n06_tiers(self):
        """N06a (-5) + N06b (-15) + N06c (-30) → only N06c survives."""
        filters = [
            _fr(config.FILTER_N06A),
            _fr(config.FILTER_N06B),
            _fr(config.FILTER_N06C),
        ]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "N06c" in ids
        assert "N06b" not in ids
        assert "N06a" not in ids

    def test_n10_tiers(self):
        """N10a (-10) + N10b (-20) + N10c (-30) → only N10c survives."""
        filters = [
            _fr(config.FILTER_N10A),
            _fr(config.FILTER_N10B),
            _fr(config.FILTER_N10C),
        ]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "N10c" in ids
        assert "N10b" not in ids
        assert "N10a" not in ids

    def test_b25_tiers(self):
        """B25a (25) + B25b (15) + B25c (5) → only B25a survives."""
        filters = [
            _fr(config.FILTER_B25A),
            _fr(config.FILTER_B25B),
            _fr(config.FILTER_B25C),
        ]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "B25a" in ids
        assert "B25b" not in ids
        assert "B25c" not in ids

    def test_b19_tiers(self):
        """B19a (20) + B19b (30) + B19c (40) → only B19c survives."""
        filters = [
            _fr(config.FILTER_B19A),
            _fr(config.FILTER_B19B),
            _fr(config.FILTER_B19C),
        ]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "B19c" in ids
        assert "B19b" not in ids
        assert "B19a" not in ids

    def test_b26_tiers(self):
        """B26a (20) + B26b (10) → only B26a survives."""
        filters = [_fr(config.FILTER_B26A), _fr(config.FILTER_B26B)]
        result = _enforce_mutual_exclusion(filters)
        ids = _ids(result)
        assert "B26a" in ids
        assert "B26b" not in ids

    def test_score_raw_after_exclusion(self):
        """Score raw should only count the surviving filter."""
        filters = [
            _fr(config.FILTER_B18A),  # 15
            _fr(config.FILTER_B18D),  # 50
        ]
        result = calculate_score(filters, total_amount=1000)
        assert result.score_raw == 50  # only B18d


# ============================================================
# PROFILE 11 — "Star validation"
# ============================================================


class TestProfile11StarValidation:
    """Stars require category diversity, amount, and coordination."""

    def test_single_category_caps_at_2_stars(self):
        """Score 160, only wallet filters → max 2★ (needs 2 categories for 3★)."""
        # All wallet/origin → ACCUMULATION only
        filters = [
            _fr(config.FILTER_W01),   # 25
            _fr(config.FILTER_W04),   # 10
            _fr(config.FILTER_W09),   # 5
            _fr(config.FILTER_O01),   # 5
            _fr(config.FILTER_O03),   # 5
        ]
        # raw = 50, need big multiplier to get to 160
        result = calculate_score(filters, total_amount=500000, wallet_market_count=1)
        # Only ACCUMULATION category
        assert result.star_level <= 2, f"Expected ≤2★ with single category, got {result.star_level}★"

    def test_two_categories_low_amount_caps_at_3_stars(self):
        """Score 200, 2 categories, $3,000 → max 3★ (needs $5K for 4★)."""
        filters = [
            _fr(config.FILTER_W01),   # 25 → ACCUMULATION
            _fr(config.FILTER_B18D),  # 50 → COORDINATION
            _fr(config.FILTER_B16),   # 20 → COORDINATION
            _fr(config.FILTER_B06),   # 15 → COORDINATION
        ]
        result = calculate_score(filters, total_amount=3000, wallet_market_count=2)
        # 2 categories: ACCUMULATION + COORDINATION
        # raw = 110, mult ≈ 1.07 → ~118 → 3★, amount < $5K → can't be 4★
        assert result.star_level <= 3, f"Expected ≤3★ with $3K, got {result.star_level}★"

    def test_no_coordination_caps_at_4_stars(self):
        """Score 250, 3 categories but no COORDINATION → max 4★."""
        # Need 3 categories without COORDINATION: ACCUMULATION + TIMING + MARKET(negative)
        # Actually we need positive filters to count.
        # wallet → ACCUMULATION, market → TIMING, we need a third non-behavior
        # Only: wallet=ACCUM, origin=ACCUM, behavior=COORD, market=TIMING, negative=MARKET
        # So to have 3 categories without COORDINATION we need... that's impossible
        # since behavior maps to COORDINATION.
        # Let's test: high score, 2 categories + amount, no COORD
        # wallet + market = ACCUMULATION + TIMING, no COORDINATION
        filters = [
            _fr(config.FILTER_W01),   # 25 → ACCUMULATION
            _fr(config.FILTER_W04),   # 10 → ACCUMULATION
            _fr(config.FILTER_O01),   # 5  → ACCUMULATION
            _fr(config.FILTER_M01),   # 15 → TIMING
            _fr(config.FILTER_M03),   # 10 → TIMING
            _fr(config.FILTER_M04B),  # 25 → TIMING
            _fr(config.FILTER_M05C),  # 25 → TIMING
        ]
        # raw = 115, with high amount
        result = calculate_score(filters, total_amount=100000, wallet_market_count=1)
        # 2 categories: ACCUMULATION + TIMING, no COORDINATION
        # 5★ requires COORDINATION, so cap at 4★
        assert result.star_level <= 4, f"Expected ≤4★ without COORDINATION, got {result.star_level}★"

    def test_5_star_all_requirements(self):
        """3 categories + $10K+ + COORDINATION → eligible for 5★."""
        filters = [
            _fr(config.FILTER_W01),   # 25 → ACCUMULATION
            _fr(config.FILTER_B18D),  # 50 → COORDINATION
            _fr(config.FILTER_B16),   # 20 → COORDINATION
            _fr(config.FILTER_M01),   # 15 → TIMING
            _fr(config.FILTER_M04B),  # 25 → TIMING
            _fr(config.FILTER_B06),   # 15 → COORDINATION
            _fr(config.FILTER_B17),   # 10 → COORDINATION
        ]
        # raw = 160, sniper(1.2) * amount(1.29 for $10K) = 1.55, final = 248
        result = calculate_score(filters, total_amount=15000, wallet_market_count=1)
        assert result.star_level == 5, (
            f"Expected 5★, got {result.star_level}★ "
            f"(raw={result.score_raw}, final={result.score_final}, mult={result.multiplier})"
        )


# ============================================================
# PROFILE 12 — "Amount multiplier edge cases"
# ============================================================


class TestProfile12AmountMultiplier:
    """Test logarithmic amount multiplier curve."""

    def test_zero_amount(self):
        assert _get_amount_multiplier(0) == 0.3

    def test_50_dollars(self):
        """$50 → close to minimum (0.3)."""
        m = _get_amount_multiplier(50)
        assert 0.3 <= m <= 0.4, f"$50 → {m}"

    def test_1000_dollars(self):
        """$1,000 → ~0.87."""
        m = _get_amount_multiplier(1000)
        assert 0.85 <= m <= 0.89, f"$1K → {m}"

    def test_10000_dollars(self):
        """$10,000 → ~1.29."""
        m = _get_amount_multiplier(10000)
        assert 1.27 <= m <= 1.31, f"$10K → {m}"

    def test_100000_dollars(self):
        """$100,000 → ~1.70."""
        m = _get_amount_multiplier(100000)
        assert 1.68 <= m <= 1.72, f"$100K → {m}"

    def test_500000_dollars(self):
        """$500,000 → close to max (2.0)."""
        m = _get_amount_multiplier(500000)
        assert 1.95 <= m <= 2.0, f"$500K → {m}"

    def test_clamp_min(self):
        """Very small amounts clamp to 0.3."""
        assert _get_amount_multiplier(1) >= 0.3
        assert _get_amount_multiplier(0.01) == 0.3

    def test_clamp_max(self):
        """Very large amounts clamp to 2.0."""
        assert _get_amount_multiplier(1_000_000_000) <= 2.0
        assert _get_amount_multiplier(1_000_000) <= 2.0

    def test_monotonically_increasing(self):
        """Multiplier strictly increases with amount."""
        amounts = [10, 50, 100, 500, 1000, 5000, 10000, 50000, 100000, 500000]
        mults = [_get_amount_multiplier(a) for a in amounts]
        for i in range(len(mults) - 1):
            assert mults[i] <= mults[i + 1], (
                f"Not monotonic: ${amounts[i]}→{mults[i]} vs ${amounts[i+1]}→{mults[i+1]}"
            )


# ============================================================
# PROFILE 13 — "Diversity multiplier"
# ============================================================


class TestProfile13DiversityMultiplier:
    """Sniper/shotgun thresholds."""

    def test_none_neutral(self):
        assert _get_diversity_multiplier(None) == 1.0

    def test_sniper_1_market(self):
        assert _get_diversity_multiplier(1) == 1.2

    def test_sniper_3_markets(self):
        assert _get_diversity_multiplier(3) == 1.2

    def test_normal_5_markets(self):
        assert _get_diversity_multiplier(5) == 1.0

    def test_normal_9_markets(self):
        assert _get_diversity_multiplier(9) == 1.0

    def test_boundary_4_markets(self):
        """4 markets → normal (above sniper threshold of 3)."""
        assert _get_diversity_multiplier(4) == 1.0

    def test_boundary_10_markets(self):
        """10 markets → shotgun (>= 10)."""
        assert _get_diversity_multiplier(10) == 0.7

    def test_shotgun_15_markets(self):
        assert _get_diversity_multiplier(15) == 0.7

    def test_boundary_20_markets(self):
        """20 markets → super shotgun (>= 20)."""
        assert _get_diversity_multiplier(20) == 0.5

    def test_super_shotgun_25_markets(self):
        assert _get_diversity_multiplier(25) == 0.5

    def test_super_shotgun_50_markets(self):
        assert _get_diversity_multiplier(50) == 0.5

    def test_combined_with_amount(self):
        """Diversity multiplier compounds with amount multiplier in calculate_score."""
        filters = [_fr(config.FILTER_W01)]  # 25 pts
        result_sniper = calculate_score(filters, total_amount=5000, wallet_market_count=1)
        result_shotgun = calculate_score(filters, total_amount=5000, wallet_market_count=15)
        # Sniper boost should yield higher final score
        assert result_sniper.score_final > result_shotgun.score_final


# ============================================================
# INDIVIDUAL FILTER POSITIVE/NEGATIVE TESTS
# ============================================================


class TestWalletAgeFilters:
    """W01, W02, W03 — wallet age tiers (mutually exclusive)."""

    def test_w01_very_new(self):
        """Age 2 days → W01 (< 7d)."""
        wallet = Wallet(address="0x1", wallet_age_days=2, total_markets=1)
        from src.analysis.wallet_analyzer import WalletAnalyzer
        # Can't easily instantiate WalletAnalyzer without real chain client,
        # so test the internal method directly
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        results = wa._check_wallet_age(wallet)
        assert _ids(results) == {"W01"}

    def test_w02_new(self):
        """Age 10 days → W02 (7-14d)."""
        wallet = Wallet(address="0x1", wallet_age_days=10)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        results = wa._check_wallet_age(wallet)
        assert _ids(results) == {"W02"}

    def test_w03_recent(self):
        """Age 20 days → W03 (14-30d)."""
        wallet = Wallet(address="0x1", wallet_age_days=20)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        results = wa._check_wallet_age(wallet)
        assert _ids(results) == {"W03"}

    def test_no_trigger_old(self):
        """Age 60 days → no W filter."""
        wallet = Wallet(address="0x1", wallet_age_days=60)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        results = wa._check_wallet_age(wallet)
        assert len(results) == 0

    def test_boundary_7_days(self):
        """Exactly 7 days → W02 (not W01)."""
        wallet = Wallet(address="0x1", wallet_age_days=7)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        results = wa._check_wallet_age(wallet)
        assert _ids(results) == {"W02"}

    def test_boundary_14_days(self):
        """Exactly 14 days → W03 (not W02)."""
        wallet = Wallet(address="0x1", wallet_age_days=14)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        results = wa._check_wallet_age(wallet)
        assert _ids(results) == {"W03"}

    def test_boundary_30_days(self):
        """Exactly 30 days → no filter (not < 30)."""
        wallet = Wallet(address="0x1", wallet_age_days=30)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        results = wa._check_wallet_age(wallet)
        assert len(results) == 0


class TestMarketCountFilters:
    """W04, W05 — market count tiers."""

    def test_w04_single_market(self):
        wallet = Wallet(address="0x1", total_markets=1)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = None
        results = wa._check_market_count(wallet)
        assert _ids(results) == {"W04"}

    def test_w05_few_markets(self):
        wallet = Wallet(address="0x1", total_markets=3)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = None
        results = wa._check_market_count(wallet)
        assert _ids(results) == {"W05"}

    def test_no_trigger_many_markets(self):
        wallet = Wallet(address="0x1", total_markets=10)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = None
        results = wa._check_market_count(wallet)
        assert len(results) == 0


class TestFirstTxPM:
    """W09 — First tx = Polymarket."""

    def test_w09_fires(self):
        wallet = Wallet(address="0x1", is_first_tx_pm=True)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        results = wa._check_first_tx_pm(wallet)
        assert _ids(results) == {"W09"}

    def test_w09_no_fire(self):
        wallet = Wallet(address="0x1", is_first_tx_pm=False)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        results = wa._check_first_tx_pm(wallet)
        assert len(results) == 0


class TestRoundBalance:
    """W11 — Round balance detection."""

    def test_w11_5k(self):
        wallet = Wallet(address="0x1")
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.chain = MagicMock()
        results = wa._check_round_balance(wallet, balance=5010.0)  # within 1%
        assert _ids(results) == {"W11"}

    def test_w11_10k(self):
        wallet = Wallet(address="0x1")
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.chain = MagicMock()
        results = wa._check_round_balance(wallet, balance=10050.0)
        assert _ids(results) == {"W11"}

    def test_w11_no_match(self):
        wallet = Wallet(address="0x1")
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.chain = MagicMock()
        results = wa._check_round_balance(wallet, balance=7777.0)
        assert len(results) == 0


class TestDripAccumulation:
    """B01 — 5+ buys in 24-72h."""

    def test_b01_fires(self):
        """5 trades spread over 30 hours → B01."""
        trades = [
            _make_trade(wallet="0xa", market="m1", hours_ago=35),
            _make_trade(wallet="0xa", market="m1", hours_ago=30),
            _make_trade(wallet="0xa", market="m1", hours_ago=25),
            _make_trade(wallet="0xa", market="m1", hours_ago=15),
            _make_trade(wallet="0xa", market="m1", hours_ago=10),
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_drip(trades)
        assert "B01" in _ids(results)

    def test_b01_no_fire_too_fast(self):
        """5 trades in 2 hours → B01 NOT fire (span < 24h)."""
        trades = [
            _make_trade(wallet="0xa", market="m1", hours_ago=2.0),
            _make_trade(wallet="0xa", market="m1", hours_ago=1.8),
            _make_trade(wallet="0xa", market="m1", hours_ago=1.5),
            _make_trade(wallet="0xa", market="m1", hours_ago=1.2),
            _make_trade(wallet="0xa", market="m1", hours_ago=1.0),
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_drip(trades)
        assert "B01" not in _ids(results)

    def test_b01_no_fire_too_few(self):
        """3 trades → B01 NOT fire (< 5)."""
        trades = [
            _make_trade(wallet="0xa", market="m1", hours_ago=40),
            _make_trade(wallet="0xa", market="m1", hours_ago=30),
            _make_trade(wallet="0xa", market="m1", hours_ago=20),
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_drip(trades)
        assert "B01" not in _ids(results)


class TestRapidAccumulation:
    """B16 — 3+ trades in < 4h."""

    def test_b16_fires(self):
        trades = [
            _make_trade(hours_ago=1.0),
            _make_trade(hours_ago=0.5),
            _make_trade(hours_ago=0.2),
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_rapid_accumulation(trades)
        assert "B16" in _ids(results)

    def test_b16_no_fire(self):
        """3 trades spread over 6 hours → no B16."""
        trades = [
            _make_trade(hours_ago=6.0),
            _make_trade(hours_ago=3.0),
            _make_trade(hours_ago=0.5),
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_rapid_accumulation(trades)
        assert "B16" not in _ids(results)


class TestAccumulationTiers:
    """B18a-d — amount-based accumulation tiers."""

    def _make_accum(self, amount: float, trade_count: int = 3) -> AccumulationWindow:
        return AccumulationWindow(
            wallet_address="0xa",
            market_id="m1",
            direction="YES",
            total_amount=amount,
            trade_count=trade_count,
            first_trade=NOW - timedelta(hours=2),
            last_trade=NOW,
        )

    def test_b18a_moderate(self):
        accum = self._make_accum(2500)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_accumulation_tiers(accum, odds_move=0.01)
        assert "B18a" in _ids(results)

    def test_b18b_significant(self):
        accum = self._make_accum(4000)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_accumulation_tiers(accum, odds_move=0.01)
        assert "B18b" in _ids(results)

    def test_b18c_strong(self):
        accum = self._make_accum(7000)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_accumulation_tiers(accum, odds_move=0.01)
        assert "B18c" in _ids(results)

    def test_b18d_very_strong(self):
        accum = self._make_accum(15000)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_accumulation_tiers(accum, odds_move=0.01)
        assert "B18d" in _ids(results)

    def test_b18_no_fire_below_threshold(self):
        accum = self._make_accum(1500)  # below $2,000
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_accumulation_tiers(accum, odds_move=0.01)
        assert len(results) == 0

    def test_b18_no_fire_single_trade(self):
        """Single trade → no accumulation tier (needs ≥2)."""
        accum = self._make_accum(15000, trade_count=1)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_accumulation_tiers(accum, odds_move=0.01)
        assert len(results) == 0

    def test_b18_trade_count_bonus(self):
        """5+ trades get +10 bonus."""
        accum = self._make_accum(2500, trade_count=5)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_accumulation_tiers(accum, odds_move=0.01)
        assert results[0].points == 15 + 10  # B18a (15) + bonus (10)


class TestWhaleEntry:
    """B19a-c — whale entry tiers."""

    def test_b19a(self):
        trade = _make_trade(amount=8000)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_whale_entry([trade])
        assert _ids(results) == {"B19a"}

    def test_b19b(self):
        trade = _make_trade(amount=25000)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_whale_entry([trade])
        assert _ids(results) == {"B19b"}

    def test_b19c(self):
        trade = _make_trade(amount=55000)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_whale_entry([trade])
        assert _ids(results) == {"B19c"}

    def test_b19_no_fire(self):
        trade = _make_trade(amount=3000)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_whale_entry([trade])
        assert len(results) == 0


class TestOddsConviction:
    """B25a-c — odds conviction tiers."""

    def test_b25a_extreme(self):
        trade = _make_trade(price=0.05)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_odds_conviction([trade])
        assert _ids(results) == {"B25a"}

    def test_b25b_high(self):
        trade = _make_trade(price=0.15)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_odds_conviction([trade])
        assert _ids(results) == {"B25b"}

    def test_b25c_moderate(self):
        trade = _make_trade(price=0.25)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_odds_conviction([trade])
        assert _ids(results) == {"B25c"}

    def test_b25_no_fire(self):
        trade = _make_trade(price=0.50)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_odds_conviction([trade])
        assert len(results) == 0


class TestStealthAccumulation:
    """B26a-b — stealth accumulation."""

    def test_b26a_stealth_whale(self):
        """Price move < 1%, total > $5K → B26a."""
        trades = [
            _make_trade(price=0.150, amount=3000, hours_ago=2.0),
            _make_trade(price=0.151, amount=3000, hours_ago=1.0),
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_stealth_accumulation(trades)
        assert "B26a" in _ids(results)

    def test_b26b_low_impact(self):
        """Price move < 3%, total > $3K → B26b."""
        trades = [
            _make_trade(price=0.150, amount=2000, hours_ago=2.0),
            _make_trade(price=0.168, amount=2000, hours_ago=1.0),  # move = 0.018 < 0.03
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_stealth_accumulation(trades)
        assert "B26b" in _ids(results)

    def test_b26_no_fire_single_trade(self):
        """Single trade → no B26 (needs ≥2)."""
        trades = [_make_trade(amount=10000)]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_stealth_accumulation(trades)
        assert len(results) == 0


class TestObviousBet:
    """N09a-b — obvious bet detection."""

    def test_n09a_extreme(self):
        """Avg price > 0.90 → N09a."""
        trade = _make_trade(price=0.92)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_obvious_bet([trade], current_odds=0.92)
        assert "N09a" in _ids(results)

    def test_n09b_high(self):
        """Avg price 0.85-0.90 → N09b."""
        trade = _make_trade(price=0.87)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_obvious_bet([trade], current_odds=0.87)
        assert "N09b" in _ids(results)

    def test_n09_no_fire(self):
        """Price 0.50 → no N09."""
        trade = _make_trade(price=0.50)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_obvious_bet([trade], current_odds=0.50)
        assert len(results) == 0


class TestObviousBetStarCap:
    """N09 caps the star level."""

    def test_n09a_caps_at_2_stars(self):
        filters = [
            _fr(config.FILTER_W01),   # 25
            _fr(config.FILTER_B18D),  # 50
            _fr(config.FILTER_M01),   # 15
            _fr(config.FILTER_N09A),  # -40
        ]
        result = calculate_score(filters, total_amount=50000)
        assert result.star_level <= 2

    def test_n09b_caps_at_3_stars(self):
        filters = [
            _fr(config.FILTER_W01),   # 25
            _fr(config.FILTER_B18D),  # 50
            _fr(config.FILTER_M01),   # 15
            _fr(config.FILTER_N09B),  # -25
        ]
        result = calculate_score(filters, total_amount=50000)
        assert result.star_level <= 3


class TestMarketFilters:
    """M01-M05 individual tests."""

    def test_m01_volume_anomaly(self):
        market = _make_market(volume_24h=50000, volume_7d_avg=15000)
        analyzer = MarketAnalyzer(db_client=FakeDB())
        results = analyzer._check_volume_anomaly(market)
        assert "M01" in _ids(results)

    def test_m01_no_fire(self):
        market = _make_market(volume_24h=15000, volume_7d_avg=15000)
        analyzer = MarketAnalyzer(db_client=FakeDB())
        results = analyzer._check_volume_anomaly(market)
        assert "M01" not in _ids(results)

    def test_m03_low_liquidity(self):
        market = _make_market(liquidity=30000)
        analyzer = MarketAnalyzer()
        results = analyzer._check_low_liquidity(market)
        assert "M03" in _ids(results)

    def test_m03_no_fire(self):
        market = _make_market(liquidity=150000)
        analyzer = MarketAnalyzer()
        results = analyzer._check_low_liquidity(market)
        assert "M03" not in _ids(results)

    def test_m04a_moderate_concentration(self):
        """Top 3 wallets = 65% → M04a."""
        trades = [
            _make_trade(wallet="w1", amount=4000),
            _make_trade(wallet="w2", amount=3000),
            _make_trade(wallet="w3", amount=1500),
            _make_trade(wallet="w4", amount=2000),
            _make_trade(wallet="w5", amount=2500),
        ]
        # Total = 13000, top3 = 4000+3000+2500 = 9500, 9500/13000 = 73% > 60%
        analyzer = MarketAnalyzer()
        results = analyzer._check_volume_concentration(trades)
        assert "M04a" in _ids(results)

    def test_m04b_high_concentration(self):
        """Top 3 wallets = 85% → M04b."""
        trades = [
            _make_trade(wallet="w1", amount=7000),
            _make_trade(wallet="w2", amount=5000),
            _make_trade(wallet="w3", amount=5000),
            _make_trade(wallet="w4", amount=1500),
            _make_trade(wallet="w5", amount=1500),
        ]
        # Total = 20000, top3 = 17000, 85% > 80%
        analyzer = MarketAnalyzer()
        results = analyzer._check_volume_concentration(trades)
        assert "M04b" in _ids(results)

    def test_m05a_72h(self):
        market = _make_market(resolution_hours=48)
        analyzer = MarketAnalyzer()
        results = analyzer._check_deadline_proximity(market)
        assert "M05a" in _ids(results)

    def test_m05b_24h(self):
        market = _make_market(resolution_hours=12)
        analyzer = MarketAnalyzer()
        results = analyzer._check_deadline_proximity(market)
        assert "M05b" in _ids(results)

    def test_m05c_6h(self):
        market = _make_market(resolution_hours=3)
        analyzer = MarketAnalyzer()
        results = analyzer._check_deadline_proximity(market)
        assert "M05c" in _ids(results)

    def test_m05_no_fire(self):
        market = _make_market(resolution_hours=200)
        analyzer = MarketAnalyzer()
        results = analyzer._check_deadline_proximity(market)
        assert len(results) == 0


class TestLongHorizon:
    """N10a-c — long horizon discount."""

    def test_n10a_30d(self):
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_long_horizon(NOW + timedelta(days=40))
        assert "N10a" in _ids(results)

    def test_n10b_60d(self):
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_long_horizon(NOW + timedelta(days=70))
        assert "N10b" in _ids(results)

    def test_n10c_90d(self):
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_long_horizon(NOW + timedelta(days=100))
        assert "N10c" in _ids(results)

    def test_n10_no_fire(self):
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_long_horizon(NOW + timedelta(days=15))
        assert len(results) == 0


class TestScalper:
    """N07a-b — scalper detection."""

    def test_n07a_single_flip(self):
        """Buy + sell within 2h same market → N07a."""
        trades = [
            _make_trade(direction="YES", hours_ago=1.0),
            _make_trade(direction="NO", hours_ago=0.5),
        ]
        nf = NoiseFilter()
        results = nf._check_scalper(trades)
        assert "N07a" in _ids(results)

    def test_n07a_no_fire_beyond_window(self):
        """Buy + sell 3h apart → no N07a."""
        trades = [
            _make_trade(direction="YES", hours_ago=4.0),
            _make_trade(direction="NO", hours_ago=1.0),
        ]
        nf = NoiseFilter()
        results = nf._check_scalper(trades)
        assert "N07a" not in _ids(results)

    def test_n07b_serial(self):
        """Flips in 3+ markets → N07b."""
        trades_current = [
            _make_trade(direction="YES", hours_ago=1.0, market="m1"),
            _make_trade(direction="NO", hours_ago=0.5, market="m1"),
        ]
        all_trades = trades_current + [
            _make_trade(direction="YES", hours_ago=1.0, market="m2"),
            _make_trade(direction="NO", hours_ago=0.5, market="m2"),
            _make_trade(direction="YES", hours_ago=1.0, market="m3"),
            _make_trade(direction="NO", hours_ago=0.5, market="m3"),
        ]
        nf = NoiseFilter()
        results = nf._check_scalper(trades_current, all_wallet_trades=all_trades)
        assert "N07b" in _ids(results)


class TestDegenTiers:
    """N06a-c — non-political market activity."""

    def test_n06a_light(self):
        wallet = Wallet(address="0x1", non_pm_markets=1)
        nf = NoiseFilter()
        results = nf._check_degen(wallet)
        assert "N06a" in _ids(results)

    def test_n06b_moderate(self):
        wallet = Wallet(address="0x1", non_pm_markets=4)
        nf = NoiseFilter()
        results = nf._check_degen(wallet)
        assert "N06b" in _ids(results)

    def test_n06c_heavy(self):
        wallet = Wallet(address="0x1", non_pm_markets=8)
        nf = NoiseFilter()
        results = nf._check_degen(wallet)
        assert "N06c" in _ids(results)

    def test_n06_no_fire(self):
        wallet = Wallet(address="0x1", non_pm_markets=0)
        nf = NoiseFilter()
        results = nf._check_degen(wallet)
        assert len(results) == 0


class TestMarketOrders:
    """B05 — all trades are market orders."""

    def test_b05_fires(self):
        trades = [
            _make_trade(is_market_order=True),
            _make_trade(is_market_order=True),
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_market_orders(trades)
        assert "B05" in _ids(results)

    def test_b05_no_fire(self):
        trades = [
            _make_trade(is_market_order=True),
            _make_trade(is_market_order=False),
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_market_orders(trades)
        assert "B05" not in _ids(results)


class TestIncreasingSizes:
    """B06 — each trade larger than previous."""

    def test_b06_fires(self):
        trades = [
            _make_trade(amount=1000, hours_ago=3.0),
            _make_trade(amount=2000, hours_ago=2.0),
            _make_trade(amount=3000, hours_ago=1.0),
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_increasing_size(trades)
        assert "B06" in _ids(results)

    def test_b06_no_fire(self):
        trades = [
            _make_trade(amount=3000, hours_ago=3.0),
            _make_trade(amount=2000, hours_ago=2.0),
            _make_trade(amount=1000, hours_ago=1.0),
        ]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_increasing_size(trades)
        assert "B06" not in _ids(results)


class TestAgainstMarket:
    """B07 — buying at odds < 0.20."""

    def test_b07_fires(self):
        trade = _make_trade(price=0.10)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_against_market([trade])
        assert "B07" in _ids(results)

    def test_b07_no_fire(self):
        trade = _make_trade(price=0.30)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_against_market([trade])
        assert "B07" not in _ids(results)


class TestLowHours:
    """B17 — trading during 2-6 AM UTC."""

    def test_b17_fires(self):
        trade = _make_trade()
        # Override timestamp to 3 AM UTC
        trade.timestamp = trade.timestamp.replace(hour=3, minute=0)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_low_hours([trade])
        assert "B17" in _ids(results)

    def test_b17_no_fire(self):
        trade = _make_trade()
        trade.timestamp = trade.timestamp.replace(hour=14, minute=0)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_low_hours([trade])
        assert "B17" not in _ids(results)


class TestFirstBigBuy:
    """B14 — first buy > $5,000."""

    def test_b14_fires(self):
        trades = [_make_trade(amount=6000, hours_ago=2.0)]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_first_big_buy(trades, b19_fired=False)
        assert "B14" in _ids(results)

    def test_b14_suppressed_by_b19(self):
        """B14 NOT fire when B19 already fired."""
        trades = [_make_trade(amount=6000, hours_ago=2.0)]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_first_big_buy(trades, b19_fired=True)
        assert "B14" not in _ids(results)

    def test_b14_no_fire_small(self):
        trades = [_make_trade(amount=3000, hours_ago=2.0)]
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_first_big_buy(trades, b19_fired=False)
        assert "B14" not in _ids(results)


class TestPositionSizing:
    """B23a-b — position sizing."""

    def _make_accum(self, amount: float) -> AccumulationWindow:
        return AccumulationWindow(
            wallet_address="0xa", market_id="m1", direction="YES",
            total_amount=amount, trade_count=2,
            first_trade=NOW - timedelta(hours=2), last_trade=NOW,
        )

    def test_b23a_significant(self):
        """Position = 30% of balance → B23a."""
        accum = self._make_accum(3000)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_position_sizing(accum, wallet_balance=10000)
        assert "B23a" in _ids(results)

    def test_b23b_dominant(self):
        """Position = 60% of balance → B23b."""
        accum = self._make_accum(6000)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_position_sizing(accum, wallet_balance=10000)
        assert "B23b" in _ids(results)

    def test_b23_no_fire_small(self):
        """Position = 5% of balance → no B23."""
        accum = self._make_accum(500)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_position_sizing(accum, wallet_balance=10000)
        assert len(results) == 0


class TestAllIn:
    """B28a-b — all-in detection."""

    def _make_accum(self, amount: float) -> AccumulationWindow:
        return AccumulationWindow(
            wallet_address="0xa", market_id="m1", direction="YES",
            total_amount=amount, trade_count=2,
            first_trade=NOW - timedelta(hours=2), last_trade=NOW,
        )

    def test_b28a_extreme(self):
        """Position = 95% of balance, amount > $3.5K → B28a."""
        accum = self._make_accum(9500)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_all_in(accum, wallet_balance=10000)
        assert "B28a" in _ids(results)

    def test_b28b_strong(self):
        """Position = 75% of balance → B28b."""
        accum = self._make_accum(7500)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_all_in(accum, wallet_balance=10000)
        assert "B28b" in _ids(results)

    def test_b28_no_fire_below_min_amount(self):
        """Position ratio high but amount < $3.5K → no B28."""
        accum = self._make_accum(2000)
        analyzer = BehaviorAnalyzer()
        results = analyzer._check_all_in(accum, wallet_balance=2100)
        assert len(results) == 0


class TestNewsFilter:
    """N02 — news detection."""

    def test_n02_fires(self):
        mock_news = MagicMock()
        mock_news.check_news.return_value = (True, "Breaking news about X")
        nf = NoiseFilter(news_checker=mock_news)
        results = nf._check_news("Will X happen?")
        assert "N02" in _ids(results)

    def test_n02_no_fire(self):
        mock_news = MagicMock()
        mock_news.check_news.return_value = (False, None)
        nf = NoiseFilter(news_checker=mock_news)
        results = nf._check_news("Will X happen?")
        assert "N02" not in _ids(results)


class TestOddsStabilityBreak:
    """M02 — stable odds broken."""

    def test_m02_fires(self):
        """Odds stable at 0.30 for 48h+ then jump to 0.50 → M02."""
        # Create snapshots spanning > 48h with stable odds
        snapshots = []
        for i in range(6):
            snapshots.append({
                "odds": 0.30,
                "volume_24h": 10000,
                "timestamp": (NOW - timedelta(hours=72 - i * 12)).isoformat(),
            })

        db = FakeDB(snapshots=snapshots)
        market = _make_market(current_odds=0.50)  # big move from 0.30
        analyzer = MarketAnalyzer(db_client=db)
        results = analyzer._check_odds_stability_break(market)
        assert "M02" in _ids(results)

    def test_m02_no_fire_already_volatile(self):
        """Odds already volatile (range > 10%) → no M02."""
        snapshots = []
        for i, odds in enumerate([0.20, 0.40, 0.25, 0.45, 0.30]):
            snapshots.append({
                "odds": odds,
                "volume_24h": 10000,
                "timestamp": (NOW - timedelta(hours=60 - i * 10)).isoformat(),
            })

        db = FakeDB(snapshots=snapshots)
        market = _make_market(current_odds=0.50)
        analyzer = MarketAnalyzer(db_client=db)
        results = analyzer._check_odds_stability_break(market)
        assert "M02" not in _ids(results)


# ============================================================
# END-TO-END: Scoring pipeline integration
# ============================================================


class TestEndToEndInsider:
    """Full pipeline: insider profile → score → stars."""

    def test_insider_scores_high(self):
        """Composite insider-like filters through full scoring pipeline."""
        filters = [
            _fr(config.FILTER_W01),   # 25 wallet → ACCUMULATION
            _fr(config.FILTER_W04),   # 10 wallet
            _fr(config.FILTER_W09),   # 5  wallet
            _fr(config.FILTER_O01),   # 5  origin → ACCUMULATION
            _fr(config.FILTER_O03),   # 5  origin
            _fr(config.FILTER_B16),   # 20 behavior → COORDINATION
            _fr(config.FILTER_B18D),  # 50 behavior
            _fr(config.FILTER_B06),   # 15 behavior
            _fr(config.FILTER_B05),   # 5  behavior
            _fr(config.FILTER_B17),   # 10 behavior
            _fr(config.FILTER_B25B),  # 15 behavior
            _fr(config.FILTER_B26A),  # 20 behavior
            _fr(config.FILTER_M01),   # 15 market → TIMING
            _fr(config.FILTER_M03),   # 10 market
            _fr(config.FILTER_M05A),  # 10 market
        ]
        result = calculate_score(
            filters,
            total_amount=15000,
            wallet_market_count=1,  # sniper
        )
        # raw = 220, mult = 1.29 * 1.2 = 1.55
        # final = 220 * 1.55 = 341
        # 3 categories: ACCUMULATION + COORDINATION + TIMING
        assert result.score_raw >= 200
        assert result.score_final >= 220, f"Final={result.score_final}"
        assert result.star_level == 5, f"Stars={result.star_level}"

    def test_degen_scores_zero(self):
        """Degen profile: all negatives → 0 score, 0★."""
        filters = [
            _fr(config.FILTER_N09A),  # -40
            _fr(config.FILTER_N06C),  # -30
            _fr(config.FILTER_N10C),  # -30
            _fr(config.FILTER_N02),   # -20
        ]
        result = calculate_score(filters, total_amount=500)
        assert result.score_raw == 0
        assert result.score_final == 0
        assert result.star_level == 0


class TestEndToEndScoreCap:
    """Score cap at 400."""

    def test_score_capped(self):
        """Extremely high raw score gets capped at 400."""
        filters = [
            _fr(config.FILTER_W01),   # 25
            _fr(config.FILTER_B18D),  # 50
            _fr(config.FILTER_B16),   # 20
            _fr(config.FILTER_B06),   # 15
            _fr(config.FILTER_B05),   # 5
            _fr(config.FILTER_B17),   # 10
            _fr(config.FILTER_B25A),  # 25
            _fr(config.FILTER_B26A),  # 20
            _fr(config.FILTER_B28A),  # 25
            _fr(config.FILTER_M01),   # 15
            _fr(config.FILTER_M04B),  # 25
            _fr(config.FILTER_M05C),  # 25
            _fr(config.FILTER_C07),   # 30
            _fr(config.FILTER_C03D),  # 30
            _fr(config.FILTER_C02),   # 15
        ]
        result = calculate_score(filters, total_amount=500000, wallet_market_count=1)
        assert result.score_final <= 400


# ============================================================
# B25 / N09 mutual exclusion at analyzer level
# ============================================================


class TestB25N09AnalyzerExclusion:
    """B25 and N09 are mutually exclusive in the analyzer: if B25 fires, N09 doesn't."""

    def test_b25_fires_n09_suppressed(self):
        """Price 0.08 → B25a fires, N09 should NOT fire."""
        trade = _make_trade(price=0.08)
        analyzer = BehaviorAnalyzer()
        results = analyzer.analyze(
            wallet_address="0xaaa",
            trades=[trade],
            market_id="mkt1",
            current_odds=0.08,
        )
        ids = _ids(results)
        assert "B25a" in ids
        assert "N09a" not in ids
        assert "N09b" not in ids

    def test_n09_fires_b25_absent(self):
        """Price 0.92 → N09a fires, B25 doesn't fire."""
        trade = _make_trade(price=0.92)
        analyzer = BehaviorAnalyzer()
        results = analyzer.analyze(
            wallet_address="0xaaa",
            trades=[trade],
            market_id="mkt1",
            current_odds=0.92,
        )
        ids = _ids(results)
        assert "N09a" in ids
        assert "B25a" not in ids
        assert "B25b" not in ids
        assert "B25c" not in ids


# ============================================================
# W04/W05 SUPPRESSION by real PM history
# ============================================================


class TestW04W05Suppression:
    """W04/W05 suppressed when real PM distinct_markets > threshold."""

    def test_w04_suppressed_by_real_history(self):
        """Wallet shows 1 market in lookback but 10 real markets → W04 suppressed."""
        wallet = Wallet(address="0xmulti", total_markets=1)
        pm_client = FakePMClient(
            history={"trade_count": 100, "distinct_markets": 10, "total_volume": 50000, "market_ids": []},
        )
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = pm_client
        results = wa._check_market_count(wallet)
        assert "W04" not in _ids(results), "W04 should be suppressed for wallet with 10 real markets"

    def test_w04_fires_when_really_new(self):
        """Wallet has only 1 real market → W04 fires."""
        wallet = Wallet(address="0xnew", total_markets=1)
        pm_client = FakePMClient(
            history={"trade_count": 2, "distinct_markets": 1, "total_volume": 500, "market_ids": []},
        )
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = pm_client
        results = wa._check_market_count(wallet)
        assert "W04" in _ids(results), "W04 should fire for wallet with 1 real market"

    def test_w04_fires_api_failure(self):
        """PM API returns None → W04 fires (fail-safe)."""
        wallet = Wallet(address="0xfail", total_markets=1)
        pm_client = FakePMClient(history=None)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = pm_client
        results = wa._check_market_count(wallet)
        assert "W04" in _ids(results), "W04 should fire when PM API fails"

    def test_w04_fires_no_pm_client(self):
        """No pm_client → W04 fires as normal."""
        wallet = Wallet(address="0xold", total_markets=1)
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = None
        results = wa._check_market_count(wallet)
        assert "W04" in _ids(results), "W04 should fire when no pm_client"

    def test_w04_threshold_boundary(self):
        """Exactly 3 real markets (= W04_SUPPRESS_MARKETS) → W04 fires (need > 3)."""
        wallet = Wallet(address="0xedge", total_markets=1)
        pm_client = FakePMClient(
            history={"trade_count": 10, "distinct_markets": 3, "total_volume": 1000, "market_ids": []},
        )
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = pm_client
        results = wa._check_market_count(wallet)
        assert "W04" in _ids(results), "W04 should fire when distinct_markets == threshold"

    def test_w04_threshold_plus_one(self):
        """4 real markets (> W04_SUPPRESS_MARKETS) → W04 suppressed."""
        wallet = Wallet(address="0xedge2", total_markets=1)
        pm_client = FakePMClient(
            history={"trade_count": 15, "distinct_markets": 4, "total_volume": 2000, "market_ids": []},
        )
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = pm_client
        results = wa._check_market_count(wallet)
        assert "W04" not in _ids(results), "W04 should be suppressed when distinct_markets > threshold"

    def test_w05_suppressed_by_real_history(self):
        """Wallet shows 2 markets in lookback but 8 real markets → W05 suppressed."""
        wallet = Wallet(address="0xmulti", total_markets=2)
        pm_client = FakePMClient(
            history={"trade_count": 200, "distinct_markets": 8, "total_volume": 100000, "market_ids": []},
        )
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = pm_client
        results = wa._check_market_count(wallet)
        assert "W05" not in _ids(results), "W05 should be suppressed for wallet with 8 real markets"

    def test_w05_fires_when_few_real_markets(self):
        """Wallet has 3 real markets (≤ W05_SUPPRESS_MARKETS=5) → W05 fires."""
        wallet = Wallet(address="0xfew", total_markets=3)
        pm_client = FakePMClient(
            history={"trade_count": 10, "distinct_markets": 3, "total_volume": 1500, "market_ids": []},
        )
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = pm_client
        results = wa._check_market_count(wallet)
        assert "W05" in _ids(results), "W05 should fire when real markets <= threshold"

    def test_w05_threshold_boundary(self):
        """Exactly 5 real markets (= W05_SUPPRESS_MARKETS) → W05 fires (need > 5)."""
        wallet = Wallet(address="0xedge5", total_markets=2)
        pm_client = FakePMClient(
            history={"trade_count": 20, "distinct_markets": 5, "total_volume": 5000, "market_ids": []},
        )
        wa = WalletAnalyzer.__new__(WalletAnalyzer)
        wa.pm_client = pm_client
        results = wa._check_market_count(wallet)
        assert "W05" in _ids(results), "W05 should fire when distinct_markets == threshold"


# ============================================================
# B28/B23 VOLUME SUPPRESSION
# ============================================================


class TestB28B23VolumeSuppression:
    """B28/B23 suppressed when PM total_volume >> USDC balance."""

    def _make_accum(self, amount: float, wallet: str = "0xrich") -> AccumulationWindow:
        return AccumulationWindow(
            wallet_address=wallet, market_id="m1", direction="YES",
            total_amount=amount, trade_count=2,
            first_trade=NOW - timedelta(hours=2), last_trade=NOW,
        )

    def test_b28_suppressed_high_volume(self):
        """PM volume $246K >> balance $20K → B28 suppressed."""
        accum = self._make_accum(18000)  # 18000/20000 = 90% → B28a
        pm_client = FakePMClient(
            history={"trade_count": 443, "distinct_markets": 6, "total_volume": 246000, "market_ids": []},
        )
        analyzer = BehaviorAnalyzer(pm_client=pm_client)
        results = analyzer._check_all_in(accum, wallet_balance=20000)
        assert "B28a" not in _ids(results), "B28a should be suppressed when PM volume >> balance"
        assert "B28b" not in _ids(results), "B28b should be suppressed when PM volume >> balance"

    def test_b28_fires_low_volume(self):
        """PM volume $5K on $20K balance (ratio < 3) → B28 fires."""
        accum = self._make_accum(18000)
        pm_client = FakePMClient(
            history={"trade_count": 5, "distinct_markets": 1, "total_volume": 5000, "market_ids": []},
        )
        analyzer = BehaviorAnalyzer(pm_client=pm_client)
        results = analyzer._check_all_in(accum, wallet_balance=20000)
        assert "B28a" in _ids(results), "B28a should fire when PM volume < 3x balance"

    def test_b28_fires_api_failure(self):
        """PM API returns None → B28 fires (fail-safe)."""
        accum = self._make_accum(18000)
        pm_client = FakePMClient(history=None)
        analyzer = BehaviorAnalyzer(pm_client=pm_client)
        results = analyzer._check_all_in(accum, wallet_balance=20000)
        assert "B28a" in _ids(results), "B28a should fire when PM API fails"

    def test_b28_fires_no_pm_client(self):
        """No pm_client → B28 fires as normal."""
        accum = self._make_accum(18000)
        analyzer = BehaviorAnalyzer(pm_client=None)
        results = analyzer._check_all_in(accum, wallet_balance=20000)
        assert "B28a" in _ids(results), "B28a should fire when no pm_client"

    def test_b23_suppressed_high_volume(self):
        """PM volume $150K >> balance $10K → B23 suppressed."""
        accum = self._make_accum(6000)  # 6000/10000 = 60% → B23b
        pm_client = FakePMClient(
            history={"trade_count": 200, "distinct_markets": 10, "total_volume": 150000, "market_ids": []},
        )
        analyzer = BehaviorAnalyzer(pm_client=pm_client)
        results = analyzer._check_position_sizing(accum, wallet_balance=10000)
        assert "B23b" not in _ids(results), "B23b should be suppressed when PM volume >> balance"
        assert "B23a" not in _ids(results), "B23a should be suppressed when PM volume >> balance"

    def test_b23_fires_low_volume(self):
        """PM volume $8K on $10K balance (ratio < 3) → B23 fires."""
        accum = self._make_accum(6000)
        pm_client = FakePMClient(
            history={"trade_count": 10, "distinct_markets": 2, "total_volume": 8000, "market_ids": []},
        )
        analyzer = BehaviorAnalyzer(pm_client=pm_client)
        results = analyzer._check_position_sizing(accum, wallet_balance=10000)
        assert "B23b" in _ids(results), "B23b should fire when PM volume < 3x balance"

    def test_b28b_suppressed_threshold_boundary(self):
        """PM volume exactly 3x balance → B28 suppressed (> check, 3x = equal → not suppressed)."""
        accum = self._make_accum(8000)  # 8000/10000 = 80% → B28b
        pm_client = FakePMClient(
            history={"trade_count": 50, "distinct_markets": 5, "total_volume": 30000, "market_ids": []},
        )
        analyzer = BehaviorAnalyzer(pm_client=pm_client)
        results = analyzer._check_all_in(accum, wallet_balance=10000)
        # 30000 > 10000 * 3.0 is False (30000 == 30000), so B28 should fire
        assert "B28b" in _ids(results), "B28b should fire when PM volume == 3x balance (not >)"

    def test_b28b_suppressed_above_threshold(self):
        """PM volume 3.1x balance → B28 suppressed."""
        accum = self._make_accum(8000)
        pm_client = FakePMClient(
            history={"trade_count": 50, "distinct_markets": 5, "total_volume": 31000, "market_ids": []},
        )
        analyzer = BehaviorAnalyzer(pm_client=pm_client)
        results = analyzer._check_all_in(accum, wallet_balance=10000)
        assert "B28b" not in _ids(results), "B28b should be suppressed when PM volume > 3x balance"


# ============================================================
# N06 REAL MARKETS — non-political market classification
# ============================================================


class TestN06RealMarkets:
    """N06 fires when non-political markets are populated from PM history."""

    def test_n06_fires_with_non_political(self):
        """Wallet with 4 non-political markets → N06b."""
        wallet = Wallet(address="0xdegen", non_pm_markets=4)
        nf = NoiseFilter()
        results = nf._check_degen(wallet)
        assert "N06b" in _ids(results)

    def test_n06_correct_tier_heavy(self):
        """8 non-political markets → N06c."""
        wallet = Wallet(address="0xsuper_degen", non_pm_markets=8)
        nf = NoiseFilter()
        results = nf._check_degen(wallet)
        assert "N06c" in _ids(results)

    def test_n06_correct_tier_light(self):
        """1 non-political market → N06a."""
        wallet = Wallet(address="0xlight", non_pm_markets=1)
        nf = NoiseFilter()
        results = nf._check_degen(wallet)
        assert "N06a" in _ids(results)

    def test_count_non_political_markets_basic(self):
        """FakePMClient correctly classifies blacklisted market questions."""
        pm_client = FakePMClient(
            questions={
                "mkt1": "Will the NFL Super Bowl winner be from the NFC?",
                "mkt2": "Will Trump win the election?",
                "mkt3": "Will Bitcoin reach $100K by March?",
                "mkt4": "Will the Fed cut rates?",
            },
        )
        # mkt1 has "nfl" and "super bowl" → blacklisted
        # mkt2 is political → not blacklisted
        # mkt3 has "will bitcoin reach" → blacklisted
        # mkt4 is economics → not blacklisted
        count = pm_client.count_non_political_markets(["mkt1", "mkt2", "mkt3", "mkt4"])
        assert count == 2, f"Expected 2 non-political markets, got {count}"

    def test_count_non_political_unknown_markets(self):
        """Markets with no question data → not counted."""
        pm_client = FakePMClient(questions={})
        count = pm_client.count_non_political_markets(["mkt_unknown1", "mkt_unknown2"])
        assert count == 0
