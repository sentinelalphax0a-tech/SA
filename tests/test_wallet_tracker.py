"""Tests for wallet tracker (WR01 win rate + SP01 specialization)."""

from unittest.mock import MagicMock, call

from src.analysis.wallet_tracker import WalletTracker


class TestWR01WinRate:
    def test_no_db_no_crash(self):
        wt = WalletTracker()
        wt.update_win_rate("0xabc", True)  # should not raise

    def test_first_win(self):
        db = MagicMock()
        db.get_wallet_category.return_value = None

        wt = WalletTracker(db_client=db)
        wt.update_win_rate("0xabc", True, total_amount=5000.0)

        db.upsert_wallet_category.assert_called_once()
        cat = db.upsert_wallet_category.call_args[0][0]
        assert cat.markets_resolved == 1
        assert cat.markets_won == 1
        assert cat.win_rate == 1.0
        assert cat.total_tracked == 5000.0

    def test_first_loss(self):
        db = MagicMock()
        db.get_wallet_category.return_value = None

        wt = WalletTracker(db_client=db)
        wt.update_win_rate("0xabc", False, total_amount=3000.0)

        cat = db.upsert_wallet_category.call_args[0][0]
        assert cat.markets_resolved == 1
        assert cat.markets_won == 0
        assert cat.win_rate == 0.0

    def test_increments_existing(self):
        db = MagicMock()
        db.get_wallet_category.return_value = {
            "category": "unknown",
            "win_rate": 0.5,
            "markets_resolved": 4,
            "markets_won": 2,
            "total_tracked": 10000.0,
            "specialty_tags": None,
        }

        wt = WalletTracker(db_client=db)
        wt.update_win_rate("0xabc", True, total_amount=2000.0)

        cat = db.upsert_wallet_category.call_args[0][0]
        assert cat.markets_resolved == 5
        assert cat.markets_won == 3
        assert cat.win_rate == 3 / 5
        assert cat.total_tracked == 12000.0

    def test_auto_categorize_smart_money(self):
        db = MagicMock()
        db.get_wallet_category.return_value = {
            "category": "unknown",
            "win_rate": 0.8,
            "markets_resolved": 4,
            "markets_won": 4,
            "total_tracked": 20000.0,
            "specialty_tags": None,
        }

        wt = WalletTracker(db_client=db)
        wt.update_win_rate("0xabc", False, total_amount=1000.0)
        # Now: resolved=5, won=4, rate=0.8 → smart_money

        cat = db.upsert_wallet_category.call_args[0][0]
        assert cat.category == "smart_money"

    def test_auto_categorize_degen(self):
        db = MagicMock()
        db.get_wallet_category.return_value = {
            "category": "unknown",
            "win_rate": 0.25,
            "markets_resolved": 4,
            "markets_won": 1,
            "total_tracked": 5000.0,
            "specialty_tags": None,
        }

        wt = WalletTracker(db_client=db)
        wt.update_win_rate("0xabc", False, total_amount=500.0)
        # Now: resolved=5, won=1, rate=0.2 → degen

        cat = db.upsert_wallet_category.call_args[0][0]
        assert cat.category == "degen"


class TestSP01Specialization:
    def test_no_db_no_crash(self):
        wt = WalletTracker()
        wt.update_specialization("0xabc", "Politics")  # should not raise

    def test_no_category_no_crash(self):
        wt = WalletTracker()
        wt.update_specialization("0xabc", None)

    def test_tracks_categories(self):
        db = MagicMock()
        db.get_wallet_category.return_value = None

        wt = WalletTracker(db_client=db)
        wt.update_specialization("0xabc", "Politics")

        cat = db.upsert_wallet_category.call_args[0][0]
        assert "politics" in cat.specialty_tags

    def test_accumulates_tags(self):
        db = MagicMock()
        db.get_wallet_category.return_value = {
            "category": "unknown",
            "win_rate": None,
            "markets_resolved": 0,
            "markets_won": 0,
            "total_tracked": 0.0,
            "specialty_tags": ["politics", "politics"],
        }

        wt = WalletTracker(db_client=db)
        wt.update_specialization("0xabc", "Politics")

        cat = db.upsert_wallet_category.call_args[0][0]
        # Should now have 3x "politics" → specialist
        assert cat.specialty_tags.count("politics") == 3
