"""Tests for the alert consolidation system."""

from unittest.mock import MagicMock

from src.database.models import Alert
from src.main import _try_consolidate, _find_new_wallets
from src.publishing.formatter import AlertFormatter


def _mock_alert(**overrides) -> Alert:
    """Build an Alert with sensible defaults."""
    kwargs = {
        "market_id": "m1",
        "alert_type": "accumulation",
        "score": 150,
        "market_question": "Will X happen?",
        "direction": "YES",
        "star_level": 5,
        "wallets": [
            {"address": "0xNEW1111", "total_amount": 5000, "avg_entry_price": 0.35},
        ],
        "total_amount": 5000,
        "odds_at_alert": 0.35,
        "confluence_count": 1,
    }
    kwargs.update(overrides)
    return Alert(**kwargs)


def _existing_alert(**overrides) -> dict:
    """Build an existing alert dict (as returned from DB)."""
    base = {
        "id": 42,
        "market_id": "m1",
        "market_question": "Will X happen?",
        "direction": "YES",
        "star_level": 5,
        "score": 120,
        "total_amount": 10000,
        "wallets": [
            {"address": "0xAAAA1111", "total_amount": 10000, "avg_entry_price": 0.30},
        ],
        "confluence_count": 1,
        "updated_count": 0,
        "outcome": "pending",
    }
    base.update(overrides)
    return base


# ── Consolidation Logic ─────────────────────────────────────


class TestConsolidation:
    def test_consolidates_same_market_same_direction(self):
        """New wallets merge into existing 4+* alert for same market+direction."""
        db = MagicMock()
        telegram = MagicMock()

        existing = _existing_alert()
        db.get_existing_high_star_alert.return_value = existing

        alert = _mock_alert(
            wallets=[
                {"address": "0xNEW2222", "total_amount": 3000, "avg_entry_price": 0.40},
            ],
            total_amount=3000,
            score=100,
        )

        result = _try_consolidate(alert, db, telegram)

        assert result is True
        db.update_alert_consolidation.assert_called_once()
        call_kwargs = db.update_alert_consolidation.call_args[1]
        assert call_kwargs["alert_id"] == 42
        assert len(call_kwargs["new_wallets"]) == 1
        assert call_kwargs["new_wallets"][0]["address"] == "0xNEW2222"
        assert call_kwargs["new_amount"] == 3000
        # Score 100 < existing 120 → no score update
        assert call_kwargs["new_score"] is None
        telegram.send_message.assert_called_once()

    def test_does_not_consolidate_different_direction(self):
        """No consolidation when no existing alert matches (different direction)."""
        db = MagicMock()
        telegram = MagicMock()

        # DB returns None (no matching high-star alert for this direction)
        db.get_existing_high_star_alert.return_value = None

        alert = _mock_alert(direction="NO")

        result = _try_consolidate(alert, db, telegram)

        assert result is False
        db.update_alert_consolidation.assert_not_called()
        telegram.send_message.assert_not_called()

    def test_does_not_consolidate_low_star(self):
        """No consolidation when no existing 4+* alert found."""
        db = MagicMock()
        telegram = MagicMock()

        # No high-star alert exists
        db.get_existing_high_star_alert.return_value = None

        alert = _mock_alert(star_level=3)

        result = _try_consolidate(alert, db, telegram)

        assert result is False
        db.update_alert_consolidation.assert_not_called()

    def test_skips_update_if_no_new_wallets(self):
        """No update message when all wallets already exist in the original."""
        db = MagicMock()
        telegram = MagicMock()

        existing = _existing_alert(
            wallets=[{"address": "0xSAME1111", "total_amount": 10000}],
        )
        db.get_existing_high_star_alert.return_value = existing

        # Incoming alert has the same wallet address
        alert = _mock_alert(
            wallets=[{"address": "0xSAME1111", "total_amount": 10000}],
        )

        result = _try_consolidate(alert, db, telegram)

        assert result is True  # Consolidated (skipped silently)
        db.update_alert_consolidation.assert_not_called()
        telegram.send_message.assert_not_called()

    def test_updates_score_if_higher(self):
        """Score is updated when the new alert has a higher score."""
        db = MagicMock()
        telegram = MagicMock()

        existing = _existing_alert(score=120)
        db.get_existing_high_star_alert.return_value = existing

        alert = _mock_alert(
            wallets=[{"address": "0xNEW3333", "total_amount": 8000}],
            score=200,
        )

        result = _try_consolidate(alert, db, telegram)

        assert result is True
        call_kwargs = db.update_alert_consolidation.call_args[1]
        assert call_kwargs["new_score"] == 200


# ── Update Format ───────────────────────────────────────────


class TestUpdateFormat:
    def test_publishes_update_format(self):
        """Update message contains all expected fields."""
        formatter = AlertFormatter()

        original = _existing_alert(star_level=5, total_amount=10000)
        new_wallets = [
            {
                "address": "0xNEW111122223333444455556666AABBCCDD",
                "total_amount": 5000,
                "avg_entry_price": 0.40,
            },
        ]

        msg = formatter.format_alert_update(
            original_alert=original,
            new_wallets=new_wallets,
            new_amount=5000,
            update_count=1,
        )

        assert "UPDATE" in msg
        assert "#42" in msg
        assert "5\u2605" in msg
        assert "Will X happen?" in msg
        assert "+1 new wallet" in msg  # singular
        assert "$5,000" in msg
        assert "$15,000" in msg  # total: 10000 + 5000
        assert "0xNEW1...CCDD" in msg
        assert "Signal strength" in msg

    def test_update_format_multiple_wallets(self):
        """Update message uses plural for multiple new wallets."""
        formatter = AlertFormatter()

        original = _existing_alert()
        new_wallets = [
            {"address": "0xWALLET_A_LONG_ADDRESS_HERE_1234", "total_amount": 3000},
            {"address": "0xWALLET_B_LONG_ADDRESS_HERE_5678", "total_amount": 2000},
        ]

        msg = formatter.format_alert_update(
            original_alert=original,
            new_wallets=new_wallets,
            new_amount=5000,
            update_count=2,
        )

        assert "+2 new wallets" in msg  # plural
        assert "Update #2" in msg
