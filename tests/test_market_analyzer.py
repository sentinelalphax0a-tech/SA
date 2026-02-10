"""Tests for the market analyzer (M filters)."""

from datetime import datetime, timedelta, timezone

from src.analysis.market_analyzer import MarketAnalyzer
from src.database.models import Market


def _make_market(
    volume_24h: float = 50000.0,
    volume_7d_avg: float = 50000.0,
    liquidity: float = 200000.0,
    current_odds: float = 0.35,
) -> Market:
    return Market(
        market_id="mkt_test_001",
        question="Test market?",
        volume_24h=volume_24h,
        volume_7d_avg=volume_7d_avg,
        liquidity=liquidity,
        current_odds=current_odds,
    )


# ── M01 — Volume anomaly ────────────────────────────────────


class TestVolumeAnomaly:
    def test_triggers_when_volume_above_2x(self):
        analyzer = MarketAnalyzer()
        market = _make_market(volume_24h=120000, volume_7d_avg=50000)
        results = analyzer._check_volume_anomaly(market)
        assert len(results) == 1
        assert results[0].filter_id == "M01"

    def test_no_trigger_at_2x_exactly(self):
        analyzer = MarketAnalyzer()
        market = _make_market(volume_24h=100000, volume_7d_avg=50000)
        results = analyzer._check_volume_anomaly(market)
        assert len(results) == 0  # 2.0x is not > 2.0x

    def test_no_trigger_normal_volume(self):
        analyzer = MarketAnalyzer()
        market = _make_market(volume_24h=40000, volume_7d_avg=50000)
        results = analyzer._check_volume_anomaly(market)
        assert len(results) == 0

    def test_no_trigger_when_no_avg(self):
        """If no historical average, M01 should not fire."""
        analyzer = MarketAnalyzer()
        market = _make_market(volume_24h=120000, volume_7d_avg=0)
        results = analyzer._check_volume_anomaly(market)
        assert len(results) == 0


# ── M02 — Stable odds broken ────────────────────────────────


class TestOddsStabilityBreak:
    def test_no_trigger_without_db(self):
        """Without DB (no history), M02 should return empty."""
        analyzer = MarketAnalyzer()
        market = _make_market(current_odds=0.50)
        results = analyzer._check_odds_stability_break(market)
        assert len(results) == 0

    def test_no_trigger_without_current_odds(self):
        analyzer = MarketAnalyzer()
        market = _make_market()
        market.current_odds = None
        results = analyzer._check_odds_stability_break(market)
        assert len(results) == 0

    def test_triggers_with_stable_then_break(self):
        """Simulate stable odds for 48+h then a large move."""
        now = datetime.now(timezone.utc)

        # Build fake snapshots: stable at 0.30 for 50 hours
        snapshots = []
        for h in range(50, 0, -6):
            snapshots.append({
                "market_id": "mkt_test_001",
                "odds": 0.30,
                "timestamp": (now - timedelta(hours=h)).isoformat(),
            })

        class FakeDB:
            def get_market_snapshots(self, market_id, hours=72):
                return snapshots
            def insert_market_snapshot(self, snapshot):
                pass

        analyzer = MarketAnalyzer(db_client=FakeDB())
        # Current odds jumped to 0.50 (move of 0.20 from stable 0.30)
        market = _make_market(current_odds=0.50)
        results = analyzer._check_odds_stability_break(market)
        assert len(results) == 1
        assert results[0].filter_id == "M02"

    def test_no_trigger_when_odds_were_unstable(self):
        """If odds varied a lot historically, M02 should not fire."""
        now = datetime.now(timezone.utc)

        # Odds vary widely: 0.20, 0.40, 0.20, 0.40 ...
        snapshots = []
        for i, h in enumerate(range(50, 0, -6)):
            odds = 0.20 if i % 2 == 0 else 0.40
            snapshots.append({
                "market_id": "mkt_test_001",
                "odds": odds,
                "timestamp": (now - timedelta(hours=h)).isoformat(),
            })

        class FakeDB:
            def get_market_snapshots(self, market_id, hours=72):
                return snapshots
            def insert_market_snapshot(self, snapshot):
                pass

        analyzer = MarketAnalyzer(db_client=FakeDB())
        market = _make_market(current_odds=0.60)
        results = analyzer._check_odds_stability_break(market)
        assert len(results) == 0  # wasn't stable

    def test_no_trigger_small_move(self):
        """Stable odds + small move should not trigger."""
        now = datetime.now(timezone.utc)

        snapshots = []
        for h in range(50, 0, -6):
            snapshots.append({
                "market_id": "mkt_test_001",
                "odds": 0.30,
                "timestamp": (now - timedelta(hours=h)).isoformat(),
            })

        class FakeDB:
            def get_market_snapshots(self, market_id, hours=72):
                return snapshots
            def insert_market_snapshot(self, snapshot):
                pass

        analyzer = MarketAnalyzer(db_client=FakeDB())
        # Small move: 0.30 → 0.35 (5%, below 10% threshold)
        market = _make_market(current_odds=0.35)
        results = analyzer._check_odds_stability_break(market)
        assert len(results) == 0

    def test_no_trigger_insufficient_history(self):
        """Less than 48h of snapshots should not trigger."""
        now = datetime.now(timezone.utc)

        # Only 24h of data
        snapshots = [
            {
                "market_id": "mkt_test_001",
                "odds": 0.30,
                "timestamp": (now - timedelta(hours=24)).isoformat(),
            },
            {
                "market_id": "mkt_test_001",
                "odds": 0.30,
                "timestamp": now.isoformat(),
            },
        ]

        class FakeDB:
            def get_market_snapshots(self, market_id, hours=72):
                return snapshots
            def insert_market_snapshot(self, snapshot):
                pass

        analyzer = MarketAnalyzer(db_client=FakeDB())
        market = _make_market(current_odds=0.60)
        results = analyzer._check_odds_stability_break(market)
        assert len(results) == 0


# ── M03 — Low liquidity ─────────────────────────────────────


class TestLowLiquidity:
    def test_triggers_below_threshold(self):
        analyzer = MarketAnalyzer()
        market = _make_market(liquidity=50000)
        results = analyzer._check_low_liquidity(market)
        assert len(results) == 1
        assert results[0].filter_id == "M03"

    def test_no_trigger_above_threshold(self):
        analyzer = MarketAnalyzer()
        market = _make_market(liquidity=200000)
        results = analyzer._check_low_liquidity(market)
        assert len(results) == 0

    def test_no_trigger_zero_liquidity(self):
        """Zero liquidity means no data — don't flag it."""
        analyzer = MarketAnalyzer()
        market = _make_market(liquidity=0)
        results = analyzer._check_low_liquidity(market)
        assert len(results) == 0


# ── Full analyze flow ────────────────────────────────────────


class TestAnalyzeIntegration:
    def test_analyze_no_db(self):
        """Without DB, only M01 and M03 can fire (from Market data)."""
        analyzer = MarketAnalyzer()
        market = _make_market(
            volume_24h=200000, volume_7d_avg=50000,
            liquidity=80000, current_odds=0.40,
        )
        results = analyzer.analyze(market)
        ids = {r.filter_id for r in results}
        assert "M01" in ids  # 200k vs 50k avg = 4x
        assert "M03" in ids  # 80k < 100k

    def test_analyze_nothing_triggers(self):
        analyzer = MarketAnalyzer()
        market = _make_market(
            volume_24h=50000, volume_7d_avg=50000,
            liquidity=200000, current_odds=0.35,
        )
        results = analyzer.analyze(market)
        assert len(results) == 0
