"""Tests for the main orchestrator."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from src.database.models import (
    Alert,
    Market,
    TradeEvent,
    FilterResult,
    Wallet,
    AccumulationWindow,
    ScoringResult,
)
from src.main import (
    _group_trades_by_wallet,
    _dominant_direction,
    _filter_wallets_by_direction,
    _compute_accumulation,
    _has_whale_entry,
    _is_in_odds_range,
    _build_alert,
    _publish_alert,
    _analyze_wallet,
    _deduplicate_alerts,
    _filter_markets,
    run_scan,
)
from src import config


def _trade(
    wallet: str = "0xabc",
    market_id: str = "mkt1",
    direction: str = "YES",
    amount: float = 500.0,
    hours_ago: float = 0,
) -> TradeEvent:
    return TradeEvent(
        wallet_address=wallet,
        market_id=market_id,
        direction=direction,
        amount=amount,
        price=0.30,
        timestamp=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )


def _market(
    market_id: str = "mkt1",
    odds: float = 0.10,
    question: str = "Will X?",
) -> Market:
    return Market(
        market_id=market_id,
        question=question,
        current_odds=odds,
        volume_24h=50000,
        liquidity=200000,
    )


def _fr(fid: str, pts: int, cat: str = "test") -> FilterResult:
    return FilterResult(
        filter_id=fid, filter_name=fid, points=pts, category=cat,
    )


# ── _group_trades_by_wallet ──────────────────────────────────


class TestGroupTrades:
    def test_groups_correctly(self):
        trades = [
            _trade(wallet="0xa"), _trade(wallet="0xa"),
            _trade(wallet="0xb"),
        ]
        groups = _group_trades_by_wallet(trades)
        assert len(groups) == 2
        assert len(groups["0xa"]) == 2
        assert len(groups["0xb"]) == 1

    def test_empty(self):
        assert _group_trades_by_wallet([]) == {}


# ── _dominant_direction ──────────────────────────────────────


class TestDominantDirection:
    def test_yes_dominant(self):
        trades = [_trade(direction="YES", amount=5000), _trade(direction="NO", amount=1000)]
        assert _dominant_direction(trades) == "YES"

    def test_no_dominant(self):
        trades = [_trade(direction="YES", amount=1000), _trade(direction="NO", amount=8000)]
        assert _dominant_direction(trades) == "NO"

    def test_tie_goes_yes(self):
        trades = [_trade(direction="YES", amount=1000), _trade(direction="NO", amount=1000)]
        assert _dominant_direction(trades) == "YES"


# ── _compute_accumulation ────────────────────────────────────


class TestComputeAccumulation:
    def test_basic(self):
        trades = [
            _trade(wallet="0xa", market_id="m1", direction="YES", amount=200),
            _trade(wallet="0xa", market_id="m1", direction="YES", amount=300),
        ]
        accum = _compute_accumulation("0xa", trades, "m1")
        assert accum is not None
        assert accum.total_amount == 500
        assert accum.direction == "YES"
        assert accum.trade_count == 2

    def test_filters_by_market(self):
        trades = [
            _trade(wallet="0xa", market_id="m1", amount=500),
            _trade(wallet="0xa", market_id="m2", amount=9000),
        ]
        accum = _compute_accumulation("0xa", trades, "m1")
        assert accum.total_amount == 500

    def test_none_for_empty(self):
        assert _compute_accumulation("0xa", [], "m1") is None


# ── _has_whale_entry ─────────────────────────────────────────


class TestHasWhaleEntry:
    def test_true_b19a(self):
        assert _has_whale_entry([_fr("B19a", 20)]) is True

    def test_true_b19c(self):
        assert _has_whale_entry([_fr("B19c", 40)]) is True

    def test_false_no_whale(self):
        assert _has_whale_entry([_fr("W01", 25), _fr("B01", 20)]) is False

    def test_false_empty(self):
        assert _has_whale_entry([]) is False


# ── _is_in_odds_range ────────────────────────────────────────


class TestOddsRange:
    def test_in_range(self):
        assert _is_in_odds_range(0.10, 50) is True

    def test_below_min(self):
        assert _is_in_odds_range(0.03, 50) is False

    def test_above_max(self):
        assert _is_in_odds_range(0.60, 50) is False

    def test_extended_range_high_score(self):
        """Score >= 90 extends max to 0.70."""
        assert _is_in_odds_range(0.65, 95) is True

    def test_extended_still_capped(self):
        assert _is_in_odds_range(0.75, 95) is False

    def test_none_odds_allowed(self):
        assert _is_in_odds_range(None, 50) is True


# ── _build_alert ─────────────────────────────────────────────


class TestBuildAlert:
    def test_basic_alert(self):
        sr = ScoringResult(
            score_raw=50, multiplier=1.0, score_final=50,
            star_level=2, filters_triggered=[],
        )
        wallet_data = [{"address": "0xa", "total_amount": 5000}]
        alert = _build_alert(_market(), "YES", sr, wallet_data)
        assert alert.score == 50
        assert alert.direction == "YES"
        assert alert.total_amount == 5000
        assert alert.alert_type == config.ALERT_TYPE_ACCUMULATION

    def test_confluence_type(self):
        sr = ScoringResult(
            score_raw=80, multiplier=1.0, score_final=80,
            star_level=3, filters_triggered=[],
        )
        wallets = [{"address": f"0x{i}", "total_amount": 1000} for i in range(3)]
        alert = _build_alert(_market(), "YES", sr, wallets)
        assert alert.alert_type == config.ALERT_TYPE_CONFLUENCE

    def test_whale_type(self):
        sr = ScoringResult(
            score_raw=40, multiplier=1.0, score_final=40,
            star_level=1, filters_triggered=[],
        )
        alert = _build_alert(
            _market(), "YES", sr,
            [{"address": "0xa", "total_amount": 50000}],
            is_whale=True,
        )
        assert alert.alert_type == config.ALERT_TYPE_WHALE_ENTRY


# ── _publish_alert ───────────────────────────────────────────


class TestPublishAlert:
    def _make_alert(self, star: int, is_whale: bool = False) -> Alert:
        return Alert(
            market_id="m1", alert_type="accumulation",
            score=70, star_level=star,
            market_question="Q?", direction="YES",
            total_amount=5000,
        )

    def test_4_star_publishes_telegram_and_x(self):
        """4+ stars publish to Telegram and X."""
        alert = self._make_alert(star=4)
        db = MagicMock()
        twitter = MagicMock()
        twitter.publish_alert.return_value = "tw123"
        telegram = MagicMock()
        telegram.publish_alert.return_value = "tg456"
        counters = {"alerts_published_x": 0, "alerts_published_tg": 0}

        _publish_alert(
            alert=alert, alert_id=1, is_whale=False,
            db=db, twitter=twitter, telegram=telegram,
            counters=counters,
        )
        telegram.publish_alert.assert_called_once()
        twitter.publish_alert.assert_called_once()
        assert counters["alerts_published_tg"] == 1
        assert counters["alerts_published_x"] == 1

    def test_3_star_no_telegram_only_x(self):
        """3-star alerts go to X but NOT Telegram."""
        alert = self._make_alert(star=3)
        db = MagicMock()
        twitter = MagicMock()
        twitter.publish_alert.return_value = "tw1"
        telegram = MagicMock()
        counters = {"alerts_published_x": 0, "alerts_published_tg": 0}

        _publish_alert(
            alert=alert, alert_id=1, is_whale=False,
            db=db, twitter=twitter, telegram=telegram,
            counters=counters,
        )
        telegram.publish_alert.assert_not_called()
        twitter.publish_alert.assert_called_once()
        assert counters["alerts_published_tg"] == 0
        assert counters["alerts_published_x"] == 1

    def test_whale_4star_publishes_telegram(self):
        """Whale entry with 4+ stars publishes to Telegram."""
        alert = self._make_alert(star=4, is_whale=True)
        db = MagicMock()
        twitter = MagicMock()
        telegram = MagicMock()
        telegram.publish_whale_entry.return_value = "wh1"
        counters = {"alerts_published_x": 0, "alerts_published_tg": 0}

        _publish_alert(
            alert=alert, alert_id=1, is_whale=True,
            db=db, twitter=twitter, telegram=telegram,
            counters=counters,
        )
        telegram.publish_whale_entry.assert_called_once()
        assert counters["alerts_published_tg"] == 1

    def test_low_star_no_telegram(self):
        """1-3 star alerts are NOT published to Telegram."""
        for star in (1, 2, 3):
            alert = self._make_alert(star=star)
            db = MagicMock()
            twitter = MagicMock()
            telegram = MagicMock()
            counters = {"alerts_published_x": 0, "alerts_published_tg": 0}

            _publish_alert(
                alert=alert, alert_id=1, is_whale=False,
                db=db, twitter=twitter, telegram=telegram,
                counters=counters,
            )
            telegram.publish_alert.assert_not_called()
            assert counters["alerts_published_tg"] == 0


# ── run_scan ─────────────────────────────────────────────────


class TestRunScan:
    @patch("src.main.SupabaseClient")
    def test_scan_disabled_exits_early(self, mock_db_cls):
        db = MagicMock()
        db.is_scan_enabled.return_value = False
        mock_db_cls.return_value = db
        run_scan()
        db.insert_scan.assert_not_called()

    @patch("src.main.TelegramBot")
    @patch("src.main.TwitterBot")
    @patch("src.main.ConfluenceDetector")
    @patch("src.main.ArbitrageFilter")
    @patch("src.main.NoiseFilter")
    @patch("src.main.MarketAnalyzer")
    @patch("src.main.BehaviorAnalyzer")
    @patch("src.main.WalletAnalyzer")
    @patch("src.main.NewsChecker")
    @patch("src.main.BlockchainClient")
    @patch("src.main.PolymarketClient")
    @patch("src.main.SupabaseClient")
    def test_no_markets_exits_gracefully(
        self, mock_db_cls, mock_pm_cls, mock_chain_cls, mock_news_cls,
        mock_wa_cls, mock_ba_cls, mock_ma_cls, mock_nf_cls,
        mock_af_cls, mock_cd_cls, mock_tw_cls, mock_tg_cls,
    ):
        db = MagicMock()
        db.is_scan_enabled.return_value = True
        mock_db_cls.return_value = db

        pm = MagicMock()
        pm.get_active_markets.return_value = []
        mock_pm_cls.return_value = pm

        run_scan()
        db.insert_scan.assert_called_once()

    @patch("src.main.TelegramBot")
    @patch("src.main.TwitterBot")
    @patch("src.main.ConfluenceDetector")
    @patch("src.main.ArbitrageFilter")
    @patch("src.main.NoiseFilter")
    @patch("src.main.MarketAnalyzer")
    @patch("src.main.BehaviorAnalyzer")
    @patch("src.main.WalletAnalyzer")
    @patch("src.main.NewsChecker")
    @patch("src.main.BlockchainClient")
    @patch("src.main.PolymarketClient")
    @patch("src.main.SupabaseClient")
    def test_market_error_continues(
        self, mock_db_cls, mock_pm_cls, mock_chain_cls, mock_news_cls,
        mock_wa_cls, mock_ba_cls, mock_ma_cls, mock_nf_cls,
        mock_af_cls, mock_cd_cls, mock_tw_cls, mock_tg_cls,
    ):
        """An error in one market shouldn't stop the scan."""
        db = MagicMock()
        db.is_scan_enabled.return_value = True
        mock_db_cls.return_value = db

        pm = MagicMock()
        m1 = _market("m1")
        m2 = _market("m2")
        pm.get_active_markets.return_value = [m1, m2]
        # First market errors, second returns no trades
        pm.get_recent_trades.side_effect = [
            Exception("API error"),
            [],
        ]
        mock_pm_cls.return_value = pm

        run_scan()
        # Should still log the scan
        db.insert_scan.assert_called_once()


# ── _analyze_wallet ──────────────────────────────────────────


class TestAnalyzeWallet:
    def test_skips_below_threshold(self):
        """Wallet with < $350 accumulated → None."""
        trades = [_trade(amount=100)]
        result = _analyze_wallet(
            wallet_address="0xa",
            wallet_trades=trades,
            all_trades=trades,
            market=_market(),
            wallet_analyzer=MagicMock(analyze=MagicMock(return_value=[])),
            behavior_analyzer=MagicMock(analyze=MagicMock(return_value=[])),
            noise_filter=MagicMock(analyze=MagicMock(return_value=[])),
            arb_filter=MagicMock(check=MagicMock(return_value=[])),
            db=MagicMock(get_wallet=MagicMock(return_value=None)),
        )
        assert result is None

    def test_passes_above_threshold(self):
        """Wallet with > $350 accumulated → analyzed."""
        trades = [_trade(amount=500)]
        wa = MagicMock()
        wa.analyze.return_value = [_fr("W01", 25)]
        ba = MagicMock()
        ba.analyze.return_value = [_fr("B05", 5)]
        nf = MagicMock()
        nf.analyze.return_value = []
        af = MagicMock()
        af.check.return_value = []
        db = MagicMock()
        db.get_wallet.return_value = None

        result = _analyze_wallet(
            wallet_address="0xa",
            wallet_trades=trades,
            all_trades=trades,
            market=_market(),
            wallet_analyzer=wa,
            behavior_analyzer=ba,
            noise_filter=nf,
            arb_filter=af,
            db=db,
        )
        assert result is not None
        wallet_data, filters = result
        assert wallet_data["address"] == "0xa"
        assert wallet_data["total_amount"] == 500
        assert len(filters) == 2

    def test_n03_kills_wallet(self):
        """N03 arbitrage → wallet discarded."""
        trades = [_trade(amount=5000)]
        af = MagicMock()
        af.check.return_value = [_fr("N03", -100, "negative")]

        result = _analyze_wallet(
            wallet_address="0xa",
            wallet_trades=trades,
            all_trades=trades,
            market=_market(),
            wallet_analyzer=MagicMock(analyze=MagicMock(return_value=[])),
            behavior_analyzer=MagicMock(analyze=MagicMock(return_value=[])),
            noise_filter=MagicMock(analyze=MagicMock(return_value=[])),
            arb_filter=af,
            db=MagicMock(get_wallet=MagicMock(return_value=None)),
        )
        assert result is None

    def test_analyzer_error_doesnt_crash(self):
        """Error in one analyzer shouldn't crash the whole wallet analysis."""
        trades = [_trade(amount=1000)]
        wa = MagicMock()
        wa.analyze.side_effect = Exception("chain error")
        ba = MagicMock()
        ba.analyze.return_value = [_fr("B05", 5)]

        result = _analyze_wallet(
            wallet_address="0xa",
            wallet_trades=trades,
            all_trades=trades,
            market=_market(),
            wallet_analyzer=wa,
            behavior_analyzer=ba,
            noise_filter=MagicMock(analyze=MagicMock(return_value=[])),
            arb_filter=MagicMock(check=MagicMock(return_value=[])),
            db=MagicMock(get_wallet=MagicMock(return_value=None)),
        )
        # Should still return with the B filter
        assert result is not None
        _, filters = result
        assert len(filters) == 1
        assert filters[0].filter_id == "B05"


# ── _deduplicate_alerts ─────────────────────────────────────


def _alert(question: str = "Will X?", score: int = 50) -> Alert:
    return Alert(
        market_id="m1", alert_type="accumulation",
        score=score, market_question=question,
        direction="YES", star_level=2, total_amount=5000,
    )


class TestDeduplicateAlerts:
    def test_empty(self):
        assert _deduplicate_alerts([]) == []

    def test_single_alert(self):
        alerts = [(_alert(), False)]
        result = _deduplicate_alerts(alerts)
        assert len(result) == 1
        assert result[0][0].deduplicated is False

    def test_different_questions_no_dedup(self):
        alerts = [
            (_alert("Will Bitcoin hit 100k?", score=80), False),
            (_alert("Will the president resign?", score=60), False),
        ]
        result = _deduplicate_alerts(alerts)
        assert not any(a.deduplicated for a, _ in result)

    def test_similar_questions_dedup(self):
        alerts = [
            (_alert("Will Trump win the 2025 election?", score=80), False),
            (_alert("Will Trump win the 2025 presidential election?", score=60), False),
        ]
        result = _deduplicate_alerts(alerts)
        # Higher score stays, lower gets deduplicated
        deduped = [a for a, _ in result if a.deduplicated]
        kept = [a for a, _ in result if not a.deduplicated]
        assert len(deduped) == 1
        assert deduped[0].score == 60
        assert len(kept) == 1
        assert kept[0].score == 80

    def test_three_similar_only_best_kept(self):
        alerts = [
            (_alert("Will Trump win election 2025?", score=50), False),
            (_alert("Will Trump win the 2025 election?", score=90), False),
            (_alert("Will Trump win 2025 presidential election?", score=70), False),
        ]
        result = _deduplicate_alerts(alerts)
        kept = [a for a, _ in result if not a.deduplicated]
        assert len(kept) == 1
        assert kept[0].score == 90


# ── _filter_markets ──────────────────────────────────────────


class TestFilterMarkets:
    def test_filters_low_volume(self):
        markets = [
            Market(market_id="m1", question="Q?", volume_24h=500, current_odds=0.10),
            Market(market_id="m2", question="Q?", volume_24h=5000, current_odds=0.10),
        ]
        result = _filter_markets(markets)
        assert len(result) == 1
        assert result[0].market_id == "m2"

    def test_filters_bad_odds(self):
        markets = [
            Market(market_id="m1", question="Q?", volume_24h=5000, current_odds=0.80),
            Market(market_id="m2", question="Q?", volume_24h=5000, current_odds=0.10),
        ]
        result = _filter_markets(markets)
        assert len(result) == 1
        assert result[0].market_id == "m2"

    def test_filters_blacklisted_terms(self):
        markets = [
            Market(market_id="m1", question="Will Musk tweet about X?", volume_24h=5000, current_odds=0.10),
            Market(market_id="m2", question="Will Congress pass the bill?", volume_24h=5000, current_odds=0.10),
        ]
        result = _filter_markets(markets)
        assert len(result) == 1
        assert result[0].market_id == "m2"

    def test_blacklists_btc_price_market(self):
        markets = [
            Market(market_id="m1", question="Will Bitcoin reach $100k by June?", volume_24h=5000, current_odds=0.10),
            Market(market_id="m2", question="Will Ethereum dip below $2000?", volume_24h=5000, current_odds=0.10),
            Market(market_id="m3", question="What is the price of Bitcoin on Dec 31?", volume_24h=5000, current_odds=0.10),
            Market(market_id="m4", question="Will BTC reach $150k?", volume_24h=5000, current_odds=0.10),
        ]
        result = _filter_markets(markets)
        assert len(result) == 0

    def test_does_not_blacklist_btc_etf_market(self):
        markets = [
            Market(market_id="m1", question="Will Bitcoin ETF be approved by SEC?", volume_24h=5000, current_odds=0.10),
            Market(market_id="m2", question="Will Congress regulate Ethereum staking?", volume_24h=5000, current_odds=0.10),
        ]
        result = _filter_markets(markets)
        assert len(result) == 2

    def test_blacklist_case_insensitive(self):
        markets = [
            Market(market_id="m1", question="WILL BITCOIN REACH $200k?", volume_24h=5000, current_odds=0.10),
            Market(market_id="m2", question="will bitcoin reach $50k?", volume_24h=5000, current_odds=0.10),
            Market(market_id="m3", question="Will Bitcoin Reach $75k?", volume_24h=5000, current_odds=0.10),
        ]
        result = _filter_markets(markets)
        assert len(result) == 0

    def test_sorted_by_volume(self):
        markets = [
            Market(market_id="m1", question="Q?", volume_24h=1000, current_odds=0.10),
            Market(market_id="m2", question="Q?", volume_24h=50000, current_odds=0.10),
            Market(market_id="m3", question="Q?", volume_24h=10000, current_odds=0.10),
        ]
        result = _filter_markets(markets)
        assert result[0].market_id == "m2"
        assert result[1].market_id == "m3"
        assert result[2].market_id == "m1"

    def test_caps_at_limit(self):
        markets = [
            Market(market_id=f"m{i}", question="Q?", volume_24h=5000, current_odds=0.10)
            for i in range(150)
        ]
        result = _filter_markets(markets)
        assert len(result) == config.MARKET_SCAN_CAP


# ── _filter_wallets_by_direction ─────────────────────────────


def _wallet(direction: str = "YES", amount: float = 5000.0) -> dict:
    """Helper to create a wallet dict for direction filtering tests."""
    return {"address": "0xabc", "direction": direction, "total_amount": amount}


class TestFilterWalletsByDirection:
    def test_no_mixed_directions(self):
        """3 wallets NO + 1 wallet YES → only 3 NO wallets kept."""
        wallets = [
            _wallet("NO", 5000), _wallet("NO", 3000), _wallet("NO", 4000),
            _wallet("YES", 2000),
        ]
        filters = [[_fr("B01", 10)] for _ in wallets]
        direction, kept, kept_f = _filter_wallets_by_direction(wallets, filters)
        assert direction == "NO"
        assert len(kept) == 3
        assert all(w["direction"] == "NO" for w in kept)

    def test_single_opposite_wallet(self):
        """1 YES wallet when dominant is NO → 0 wallets remain."""
        wallets = [_wallet("YES", 2000)]
        filters = [[_fr("B01", 10)]]
        # Only YES wallet, but if we force direction to NO externally...
        # Actually with 1 YES wallet, direction = YES (dominant)
        direction, kept, kept_f = _filter_wallets_by_direction(wallets, filters)
        assert direction == "YES"
        assert len(kept) == 1

    def test_single_opposite_excluded(self):
        """2 NO wallets ($8K) + 1 YES wallet ($2K) → YES excluded."""
        wallets = [_wallet("NO", 5000), _wallet("NO", 3000), _wallet("YES", 2000)]
        filters = [[_fr("B01", 10)] for _ in wallets]
        direction, kept, kept_f = _filter_wallets_by_direction(wallets, filters)
        assert direction == "NO"
        assert len(kept) == 2
        assert all(w["direction"] == "NO" for w in kept)

    def test_confluence_count_filtered(self):
        """4 wallets total (3 NO + 1 YES) → confluence_count = 3."""
        wallets = [
            _wallet("NO", 5000), _wallet("NO", 3000), _wallet("NO", 4000),
            _wallet("YES", 2000),
        ]
        filters = [[_fr("B01", 10)] for _ in wallets]
        direction, kept, kept_f = _filter_wallets_by_direction(wallets, filters)
        # confluence_count in _build_alert = len(wallet_data)
        assert len(kept) == 3

    def test_filter_sets_aligned(self):
        """Filter sets stay aligned with their wallets after filtering."""
        wallets = [_wallet("NO", 5000), _wallet("YES", 2000), _wallet("NO", 3000)]
        f1 = [_fr("B01", 10)]
        f2 = [_fr("B07", 15)]
        f3 = [_fr("B25a", 25)]
        filters = [f1, f2, f3]
        direction, kept, kept_f = _filter_wallets_by_direction(wallets, filters)
        assert direction == "NO"
        assert len(kept) == 2
        assert len(kept_f) == 2
        # f1 (B01) and f3 (B25a) should be kept, f2 (B07) excluded
        assert kept_f[0][0].filter_id == "B01"
        assert kept_f[1][0].filter_id == "B25a"
