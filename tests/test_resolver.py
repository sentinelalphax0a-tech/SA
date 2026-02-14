"""Tests for the market resolver module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

from src.tracking.resolver import MarketResolver


def _make_resolver():
    """Create a MarketResolver with mocked DB and Polymarket clients."""
    db = MagicMock()
    pm = MagicMock()
    resolver = MarketResolver(db=db, polymarket=pm)
    return resolver, db, pm


def _pending_alert(**overrides) -> dict:
    """Build a pending alert dict with sensible defaults."""
    base = {
        "id": 1,
        "market_id": "m1",
        "market_question": "Will X happen?",
        "direction": "YES",
        "odds_at_alert": 0.35,
        "outcome": "pending",
        "wallets": [
            {"address": "0xAAA", "total_amount": 5000},
        ],
        "timestamp": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
        "created_at": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
    }
    base.update(overrides)
    return base


class TestMarketResolver:

    def test_resolves_correct_yes_alert(self):
        """YES alert + YES outcome → correct."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(direction="YES", odds_at_alert=0.35),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "YES"}

        result = resolver.run()

        assert result["correct"] == 1
        assert result["incorrect"] == 0
        assert result["resolved"] == 1

        # Check alert was updated with correct outcome
        fields = db.update_alert_fields.call_args[0][1]
        assert fields["outcome"] == "correct"
        assert fields["odds_at_resolution"] == 1.0
        assert "resolved_at" in fields

    def test_resolves_correct_no_alert(self):
        """NO alert + NO outcome → correct."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(direction="NO", odds_at_alert=0.70),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "NO"}

        result = resolver.run()

        assert result["correct"] == 1
        assert result["incorrect"] == 0

        fields = db.update_alert_fields.call_args[0][1]
        assert fields["outcome"] == "correct"
        assert fields["odds_at_resolution"] == 1.0

    def test_resolves_incorrect_alert(self):
        """YES alert + NO outcome → incorrect."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(direction="YES", odds_at_alert=0.35),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "NO"}

        result = resolver.run()

        assert result["correct"] == 0
        assert result["incorrect"] == 1

        fields = db.update_alert_fields.call_args[0][1]
        assert fields["outcome"] == "incorrect"
        assert fields["odds_at_resolution"] == 0.0
        assert fields["actual_return"] == -100.0

    def test_skips_unresolved_market(self):
        """Unresolved market → alert stays pending, no updates."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [_pending_alert()]
        pm.get_market_resolution.return_value = {"resolved": False}

        result = resolver.run()

        assert result["resolved"] == 0
        db.update_alert_fields.assert_not_called()
        db.update_market_resolution.assert_not_called()

    def test_calculates_actual_return_correct_yes(self):
        """Correct YES: actual_return = ((1.0 - 0.35) / 0.35) * 100 = 185.71."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(direction="YES", odds_at_alert=0.35),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "YES"}

        resolver.run()

        fields = db.update_alert_fields.call_args[0][1]
        assert abs(fields["actual_return"] - 185.71) < 0.1

    def test_calculates_actual_return_correct_no(self):
        """Correct NO: odds_adj = 1 - 0.70 = 0.30, return = ((1.0 - 0.30) / 0.30) * 100 = 233.33."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(direction="NO", odds_at_alert=0.70),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "NO"}

        resolver.run()

        fields = db.update_alert_fields.call_args[0][1]
        assert abs(fields["actual_return"] - 233.33) < 0.1

    def test_calculates_actual_return_incorrect(self):
        """Incorrect → actual_return = -100."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(direction="YES", odds_at_alert=0.35),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "NO"}

        resolver.run()

        fields = db.update_alert_fields.call_args[0][1]
        assert fields["actual_return"] == -100.0

    def test_updates_wallet_win_rate(self):
        """Wallet stats updated for each wallet in the alert."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(
                wallets=[
                    {"address": "0xAAA", "total_amount": 5000},
                    {"address": "0xBBB", "total_amount": 3000},
                ],
            ),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "YES"}

        resolver.run()

        # Both wallets should get their stats updated
        assert db.update_wallet_stats.call_count == 2
        db.update_wallet_stats.assert_any_call("0xAAA", won=True)
        db.update_wallet_stats.assert_any_call("0xBBB", won=True)

    def test_updates_wallet_stats_on_loss(self):
        """On incorrect resolution, wallets are updated with won=False."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(direction="YES"),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "NO"}

        resolver.run()

        db.update_wallet_stats.assert_called_once_with("0xAAA", won=False)

    def test_updates_market_resolution(self):
        """Market table is updated with is_resolved=true and outcome."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [_pending_alert(market_id="m1")]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "YES"}

        resolver.run()

        db.update_market_resolution.assert_called_once_with("m1", "YES")

    def test_time_to_resolution_days(self):
        """time_to_resolution_days = (now - alert timestamp).days."""
        resolver, db, pm = _make_resolver()
        created = datetime.now(timezone.utc) - timedelta(days=14)
        db.get_alerts_pending.return_value = [
            _pending_alert(
                timestamp=created.isoformat(),
                created_at=created.isoformat(),
            ),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "YES"}

        resolver.run()

        fields = db.update_alert_fields.call_args[0][1]
        assert fields["time_to_resolution_days"] == 14

    def test_no_pending_alerts(self):
        """Empty pending list → returns zeros, no API calls."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = []

        result = resolver.run()

        assert result == {"resolved": 0, "correct": 0, "incorrect": 0}
        pm.get_market_resolution.assert_not_called()

    def test_multiple_alerts_same_market(self):
        """Multiple alerts on same market resolved in one API call."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(id=1, market_id="m1", direction="YES"),
            _pending_alert(id=2, market_id="m1", direction="NO"),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "YES"}

        result = resolver.run()

        # Only 1 API call for the market, but 2 alerts resolved
        assert pm.get_market_resolution.call_count == 1
        assert result["resolved"] == 2
        assert result["correct"] == 1   # YES alert
        assert result["incorrect"] == 1  # NO alert

    def test_exception_in_one_alert_doesnt_block_others(self):
        """If resolving one alert throws, the rest still get resolved."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(id=1, market_id="m1"),
            _pending_alert(id=2, market_id="m1"),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "YES"}

        # First update_alert_fields call raises, second succeeds
        db.update_alert_fields.side_effect = [
            Exception("DB timeout"),
            None,
        ]

        result = resolver.run()

        # One failed, one succeeded
        assert result["correct"] == 1

    def test_api_failure_for_market_skips_it(self):
        """API returning None for a market → skip all its alerts."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(id=1, market_id="m1"),
            _pending_alert(id=2, market_id="m2"),
        ]
        # m1 fails, m2 resolves
        pm.get_market_resolution.side_effect = [
            None,
            {"resolved": True, "outcome": "NO"},
        ]

        result = resolver.run()

        assert result["resolved"] == 1
        assert result["incorrect"] == 1  # m2 alert was YES, outcome NO

    def test_no_wallets_in_alert(self):
        """Alert with no wallets → resolves without error."""
        resolver, db, pm = _make_resolver()
        db.get_alerts_pending.return_value = [
            _pending_alert(wallets=None),
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "YES"}

        result = resolver.run()

        assert result["correct"] == 1
        db.update_wallet_stats.assert_not_called()
