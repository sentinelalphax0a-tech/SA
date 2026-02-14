"""Tests for the alert tracker module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

from src.tracking.alert_tracker import AlertTracker
from src.database.models import Market


def _make_tracker():
    """Create an AlertTracker with mocked DB and Polymarket clients."""
    db = MagicMock()
    pm = MagicMock()
    tracker = AlertTracker(db=db, polymarket=pm)
    return tracker, db, pm


def _pending_alert(**overrides) -> dict:
    """Build a pending alert dict with sensible defaults."""
    base = {
        "id": 1,
        "market_id": "m1",
        "market_question": "Will X happen?",
        "direction": "YES",
        "odds_at_alert": 0.35,
        "outcome": "pending",
        "odds_max": None,
        "odds_max_date": None,
        "days_to_max": None,
        "potential_return_max": None,
        "odds_min": None,
        "odds_min_date": None,
        "timestamp": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
        "created_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
    }
    base.update(overrides)
    return base


def _market(odds: float) -> Market:
    """Build a Market with given YES odds."""
    return Market(market_id="m1", question="Will X happen?", current_odds=odds)


class TestAlertTracker:

    def test_updates_odds_max_when_higher(self):
        """odds_actual > existing odds_max → update odds_max."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = [
            _pending_alert(odds_max=0.40, odds_min=0.30),
        ]
        pm.get_market_info.return_value = _market(0.55)

        tracked = tracker.run()

        assert tracked == 1
        db.update_alert_fields.assert_called_once()
        fields = db.update_alert_fields.call_args[0][1]
        assert fields["odds_max"] == 0.55
        assert "odds_max_date" in fields
        assert "odds_min" not in fields  # 0.55 > 0.30 → min untouched

    def test_does_not_update_odds_max_when_lower(self):
        """odds_actual < existing odds_max → don't update odds_max."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = [
            _pending_alert(odds_max=0.60, odds_min=0.30),
        ]
        pm.get_market_info.return_value = _market(0.50)

        tracker.run()

        # Neither max nor min changed, so no DB update
        db.update_alert_fields.assert_not_called()

    def test_updates_odds_min_when_lower(self):
        """odds_actual < existing odds_min → update."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = [
            _pending_alert(odds_max=0.60, odds_min=0.30),
        ]
        pm.get_market_info.return_value = _market(0.20)

        tracker.run()

        fields = db.update_alert_fields.call_args[0][1]
        assert fields["odds_min"] == 0.20
        assert "odds_min_date" in fields

    def test_does_not_update_odds_min_when_higher(self):
        """odds_actual > existing odds_min → don't update odds_min."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = [
            _pending_alert(odds_max=0.60, odds_min=0.30),
        ]
        # Current = 0.40 → between min(0.30) and max(0.60)
        pm.get_market_info.return_value = _market(0.40)

        tracker.run()

        # No field changed
        db.update_alert_fields.assert_not_called()

    def test_handles_null_odds_max(self):
        """First tracking run: odds_max is None → set it."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = [
            _pending_alert(odds_max=None, odds_min=None),
        ]
        pm.get_market_info.return_value = _market(0.42)

        tracker.run()

        fields = db.update_alert_fields.call_args[0][1]
        assert fields["odds_max"] == 0.42
        assert fields["odds_min"] == 0.42
        assert "odds_max_date" in fields
        assert "odds_min_date" in fields
        assert "days_to_max" in fields
        # potential_return = ((0.42 - 0.35) / 0.35) * 100 = 20.0
        assert abs(fields["potential_return_max"] - 20.0) < 0.1

    def test_skips_resolved_market(self):
        """get_market_info returns None → skip (market resolved/gone)."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = [_pending_alert()]
        pm.get_market_info.return_value = None

        tracked = tracker.run()

        assert tracked == 0
        db.update_alert_fields.assert_not_called()

    def test_direction_no_inverts_odds(self):
        """NO direction: odds_actual = 1 - YES price."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = [
            _pending_alert(
                direction="NO",
                odds_at_alert=0.30,  # YES price at alert
                odds_max=None,
                odds_min=None,
            ),
        ]
        # Current YES price = 0.20 → NO price = 0.80
        pm.get_market_info.return_value = _market(0.20)

        tracker.run()

        fields = db.update_alert_fields.call_args[0][1]
        # NO odds = 1 - 0.20 = 0.80
        assert fields["odds_max"] == 0.80
        assert fields["odds_min"] == 0.80
        # odds_at_alert_adj = 1 - 0.30 = 0.70
        # potential_return = ((0.80 - 0.70) / 0.70) * 100 = 14.29
        assert abs(fields["potential_return_max"] - 14.29) < 0.1

    def test_no_pending_alerts(self):
        """Empty pending list → returns 0, no updates."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = []

        tracked = tracker.run()

        assert tracked == 0
        pm.get_market_info.assert_not_called()
        db.update_alert_fields.assert_not_called()

    def test_multiple_alerts_tracked(self):
        """Multiple pending alerts → all tracked independently."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = [
            _pending_alert(id=1, market_id="m1", odds_max=None),
            _pending_alert(id=2, market_id="m2", odds_max=None),
        ]
        pm.get_market_info.side_effect = [
            _market(0.40),  # for m1
            _market(0.50),  # for m2
        ]

        tracked = tracker.run()

        assert tracked == 2
        assert db.update_alert_fields.call_count == 2

    def test_days_to_max_calculated(self):
        """days_to_max = (now - alert timestamp).days."""
        tracker, db, pm = _make_tracker()
        created = datetime.now(timezone.utc) - timedelta(days=10)
        db.get_alerts_pending.return_value = [
            _pending_alert(
                odds_max=None,
                timestamp=created.isoformat(),
                created_at=created.isoformat(),
            ),
        ]
        pm.get_market_info.return_value = _market(0.50)

        tracker.run()

        fields = db.update_alert_fields.call_args[0][1]
        assert fields["days_to_max"] == 10

    def test_potential_return_max_formula(self):
        """potential_return_max = ((odds_max - odds_at_alert_adj) / odds_at_alert_adj) * 100."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = [
            _pending_alert(
                direction="YES",
                odds_at_alert=0.25,
                odds_max=None,
            ),
        ]
        pm.get_market_info.return_value = _market(0.75)

        tracker.run()

        fields = db.update_alert_fields.call_args[0][1]
        # ((0.75 - 0.25) / 0.25) * 100 = 200.0
        assert fields["potential_return_max"] == 200.0

    def test_exception_in_one_alert_doesnt_block_others(self):
        """If one alert throws, the rest still get tracked."""
        tracker, db, pm = _make_tracker()
        db.get_alerts_pending.return_value = [
            _pending_alert(id=1, market_id="m1", odds_max=None),
            _pending_alert(id=2, market_id="m2", odds_max=None),
        ]
        # First call raises, second succeeds
        pm.get_market_info.side_effect = [
            Exception("API timeout"),
            _market(0.50),
        ]

        tracked = tracker.run()

        assert tracked == 1
        db.update_alert_fields.assert_called_once()
