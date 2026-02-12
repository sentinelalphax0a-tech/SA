"""Tests for the arbitrage filter (N03/N04)."""

from datetime import datetime, timezone

from src.analysis.arbitrage_filter import (
    ArbitrageFilter,
    _dominant_direction,
    tokenize,
    jaccard,
)
from src.database.models import TradeEvent


# ── Fake DB ──────────────────────────────────────────────────


class FakeDB:
    """In-memory mock of SupabaseClient for market queries."""

    def __init__(
        self,
        markets: dict[str, dict] | None = None,
        all_markets: list[dict] | None = None,
    ):
        self._markets = markets or {}
        self._all_markets = all_markets

    def get_market(self, market_id: str) -> dict | None:
        return self._markets.get(market_id)

    def get_all_markets(self) -> list[dict]:
        if self._all_markets is not None:
            return self._all_markets
        return list(self._markets.values())


def _trade(
    wallet: str = "0xabc",
    market_id: str = "mktA",
    direction: str = "YES",
    amount: float = 1000.0,
) -> TradeEvent:
    return TradeEvent(
        wallet_address=wallet,
        market_id=market_id,
        direction=direction,
        amount=amount,
        price=0.30,
        timestamp=datetime.now(timezone.utc),
    )


# ── N03 — Arbitrage (opposite directions) ───────────────────


class TestArbitrageN03:
    def test_n03_yes_on_a_no_on_b(self):
        """YES on market A, NO on opposite market B → N03 (-100)."""
        db = FakeDB(markets={
            "mktA": {"market_id": "mktA", "question": "Will X?", "opposite_market": "mktB"},
            "mktB": {"market_id": "mktB", "question": "Will not X?", "opposite_market": "mktA"},
        })
        af = ArbitrageFilter(db_client=db)

        trades = [
            _trade(wallet="0xabc", market_id="mktA", direction="YES", amount=5000),
            _trade(wallet="0xabc", market_id="mktB", direction="NO", amount=5000),
        ]

        results = af.check("0xabc", "mktA", "YES", trades)
        assert len(results) == 1
        assert results[0].filter_id == "N03"
        assert results[0].points == -100

    def test_n03_no_on_a_yes_on_b(self):
        """NO on market A, YES on opposite market B → N03."""
        db = FakeDB(markets={
            "mktA": {"market_id": "mktA", "question": "Will X?", "opposite_market": "mktB"},
            "mktB": {"market_id": "mktB", "question": "Will not X?"},
        })
        af = ArbitrageFilter(db_client=db)

        trades = [
            _trade(wallet="0xabc", market_id="mktA", direction="NO", amount=5000),
            _trade(wallet="0xabc", market_id="mktB", direction="YES", amount=5000),
        ]

        results = af.check("0xabc", "mktA", "NO", trades)
        assert len(results) == 1
        assert results[0].filter_id == "N03"

    def test_n03_dominant_direction_by_amount(self):
        """Dominant direction is determined by total amount, not trade count."""
        db = FakeDB(markets={
            "mktA": {"market_id": "mktA", "question": "Q?", "opposite_market": "mktB"},
            "mktB": {"market_id": "mktB", "question": "Q opposite?"},
        })
        af = ArbitrageFilter(db_client=db)

        # 3 small YES trades + 1 large NO trade on opposite
        trades = [
            _trade(wallet="0xabc", market_id="mktA", direction="YES", amount=5000),
            _trade(wallet="0xabc", market_id="mktB", direction="YES", amount=100),
            _trade(wallet="0xabc", market_id="mktB", direction="YES", amount=100),
            _trade(wallet="0xabc", market_id="mktB", direction="NO", amount=10000),
        ]

        results = af.check("0xabc", "mktA", "YES", trades)
        assert len(results) == 1
        assert results[0].filter_id == "N03"  # YES vs dominant NO


# ── N04 — Same direction on opposite markets ────────────────


class TestOppositeN04:
    def test_n04_same_direction_both_markets(self):
        """YES on both market A and opposite B → N04 (flag, 0 pts)."""
        db = FakeDB(markets={
            "mktA": {"market_id": "mktA", "question": "Will X?", "opposite_market": "mktB"},
            "mktB": {"market_id": "mktB", "question": "Will not X?"},
        })
        af = ArbitrageFilter(db_client=db)

        trades = [
            _trade(wallet="0xabc", market_id="mktA", direction="YES", amount=5000),
            _trade(wallet="0xabc", market_id="mktB", direction="YES", amount=5000),
        ]

        results = af.check("0xabc", "mktA", "YES", trades)
        assert len(results) == 1
        assert results[0].filter_id == "N04"
        assert results[0].points == 0


# ── No opposite market / no trades ──────────────────────────


class TestNoTrigger:
    def test_no_opposite_market_in_db(self):
        """No opposite_market set and no similarity match → empty."""
        db = FakeDB(markets={
            "mktA": {"market_id": "mktA", "question": "Will X?", "opposite_market": None},
        })
        af = ArbitrageFilter(db_client=db)
        trades = [_trade(wallet="0xabc", market_id="mktA", direction="YES")]
        results = af.check("0xabc", "mktA", "YES", trades)
        assert len(results) == 0

    def test_no_trades_on_opposite(self):
        """Opposite market exists but wallet has no trades there → empty."""
        db = FakeDB(markets={
            "mktA": {"market_id": "mktA", "question": "Q?", "opposite_market": "mktB"},
            "mktB": {"market_id": "mktB", "question": "Q opposite?"},
        })
        af = ArbitrageFilter(db_client=db)

        trades = [_trade(wallet="0xabc", market_id="mktA", direction="YES")]
        results = af.check("0xabc", "mktA", "YES", trades)
        assert len(results) == 0

    def test_no_db_no_crash(self):
        """No DB client at all → gracefully returns empty."""
        af = ArbitrageFilter()
        trades = [_trade()]
        results = af.check("0xabc", "mktA", "YES", trades)
        assert len(results) == 0

    def test_other_wallet_trades_ignored(self):
        """Only trades from the specified wallet are checked."""
        db = FakeDB(markets={
            "mktA": {"market_id": "mktA", "question": "Q?", "opposite_market": "mktB"},
            "mktB": {"market_id": "mktB", "question": "Q opp?"},
        })
        af = ArbitrageFilter(db_client=db)

        trades = [
            _trade(wallet="0xabc", market_id="mktA", direction="YES"),
            _trade(wallet="0xother", market_id="mktB", direction="NO"),  # different wallet
        ]
        results = af.check("0xabc", "mktA", "YES", trades)
        assert len(results) == 0


# ── Similarity fallback ─────────────────────────────────────


class TestSimilarityFallback:
    def test_similarity_finds_opposite(self):
        """When no explicit mapping, similarity detects related market."""
        all_markets = [
            {"market_id": "mktA", "question": "Will Trump win the 2024 election?"},
            {"market_id": "mktB", "question": "Will Trump lose the 2024 election?"},
            {"market_id": "mktC", "question": "Will Bitcoin reach $100k?"},
        ]
        db = FakeDB(
            markets={m["market_id"]: m for m in all_markets},
            all_markets=all_markets,
        )
        af = ArbitrageFilter(db_client=db)

        trades = [
            _trade(wallet="0xabc", market_id="mktA", direction="YES"),
            _trade(wallet="0xabc", market_id="mktB", direction="NO"),
        ]

        results = af.check("0xabc", "mktA", "YES", trades)
        assert len(results) == 1
        assert results[0].filter_id == "N03"

    def test_no_similarity_match_unrelated(self):
        """Totally different questions → no match."""
        all_markets = [
            {"market_id": "mktA", "question": "Will Trump win the election?"},
            {"market_id": "mktB", "question": "Will Bitcoin reach 100k by December?"},
        ]
        db = FakeDB(
            markets={m["market_id"]: m for m in all_markets},
            all_markets=all_markets,
        )
        af = ArbitrageFilter(db_client=db)
        trades = [_trade(wallet="0xabc", market_id="mktA", direction="YES")]
        results = af.check("0xabc", "mktA", "YES", trades)
        assert len(results) == 0


# ── Helper unit tests ────────────────────────────────────────


class TestHelpers:
    def test_dominant_direction_yes(self):
        trades = [
            _trade(direction="YES", amount=5000),
            _trade(direction="NO", amount=2000),
        ]
        assert _dominant_direction(trades) == "YES"

    def test_dominant_direction_no(self):
        trades = [
            _trade(direction="YES", amount=1000),
            _trade(direction="NO", amount=8000),
        ]
        assert _dominant_direction(trades) == "NO"

    def test_dominant_direction_tie_goes_yes(self):
        trades = [
            _trade(direction="YES", amount=5000),
            _trade(direction="NO", amount=5000),
        ]
        assert _dominant_direction(trades) == "YES"

    def testtokenize(self):
        tokens = tokenize("Will Trump win the 2024 election?")
        assert "trump" in tokens
        assert "2024" in tokens
        assert "election" in tokens
        assert "will" not in tokens  # stop word
        assert "the" not in tokens  # stop word

    def test_jaccard_identical(self):
        a = {"trump", "win", "election"}
        assert jaccard(a, a) == 1.0

    def test_jaccard_disjoint(self):
        a = {"trump", "win"}
        b = {"bitcoin", "price"}
        assert jaccard(a, b) == 0.0

    def test_jaccard_partial(self):
        a = {"trump", "win", "election", "2024"}
        b = {"trump", "lose", "election", "2024"}
        score = jaccard(a, b)
        assert 0.5 < score < 1.0  # 3 shared out of 5 unique = 0.6
