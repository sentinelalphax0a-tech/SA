"""Tests for the alert notifier module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

from src.tracking.alert_notifier import (
    AlertNotifier,
    CLOSING_SOON_HOURS,
    MAX_ODDS_UPDATES_PER_CYCLE,
    ODDS_CHANGE_THRESHOLD_PCT,
    SPAM_COOLDOWN_HOURS,
)


def _make_notifier():
    """Create an AlertNotifier with mocked DB and Telegram."""
    db = MagicMock()
    telegram = MagicMock()
    notifier = AlertNotifier(db=db, telegram=telegram)
    return notifier, db, telegram


def _pending_alert(**overrides) -> dict:
    """Build a pending alert dict with sensible defaults."""
    base = {
        "id": 1,
        "market_id": "m1",
        "market_question": "Will X happen?",
        "direction": "YES",
        "odds_at_alert": 0.35,
        "outcome": "pending",
        "star_level": 3,
        "score": 100,
        "total_amount": 5000.0,
        "odds_max": None,
        "potential_return_max": None,
        "odds_max_date": None,
        "wallets": [{"address": "0xAAAABBBBCCCCDDDD", "total_amount": 5000}],
        "timestamp": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "created_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "resolved_at": None,
        "actual_return": None,
    }
    base.update(overrides)
    return base


def _market(
    current_odds: float = 0.50,
    resolution_date: datetime | None = None,
) -> dict:
    """Build a market dict."""
    return {
        "market_id": "m1",
        "question": "Will X happen?",
        "current_odds": current_odds,
        "resolution_date": (
            resolution_date.isoformat()
            if resolution_date
            else (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        ),
    }


# ── Closing Soon ─────────────────────────────────────────


class TestClosingSoon:
    def test_closing_soon_filters_24h(self):
        """Only alerts with <24h to resolution are included."""
        notifier, db, telegram = _make_notifier()

        soon = datetime.now(timezone.utc) + timedelta(hours=6)
        far = datetime.now(timezone.utc) + timedelta(days=3)

        alert_soon = _pending_alert(id=1, market_id="m1")
        alert_far = _pending_alert(id=2, market_id="m2")

        db.get_alerts_pending.return_value = [alert_soon, alert_far]
        db.get_recently_resolved.return_value = []
        db.get_notification_log.return_value = None

        # Market for m1 resolves in 6h, m2 in 3 days
        def get_market(mid):
            if mid == "m1":
                return _market(resolution_date=soon)
            return _market(resolution_date=far)

        db.get_market.side_effect = get_market

        counts = notifier.run()

        # closing_soon should have found 1 alert
        assert counts["closing_soon"] == 1
        telegram.send_message.assert_called()
        msg = telegram.send_message.call_args_list[0][0][0]
        assert "CLOSING SOON" in msg
        assert "Will X happen?" in msg

    def test_no_closing_soon_when_none_close(self):
        """If no alerts are closing soon, no message is sent."""
        notifier, db, telegram = _make_notifier()

        far = datetime.now(timezone.utc) + timedelta(days=10)
        db.get_alerts_pending.return_value = [_pending_alert()]
        db.get_recently_resolved.return_value = []
        db.get_notification_log.return_value = None
        db.get_market.return_value = _market(resolution_date=far)

        counts = notifier.run()

        assert counts["closing_soon"] == 0


# ── Odds Updates ─────────────────────────────────────────


class TestOddsUpdate:
    def test_odds_update_only_3plus_stars(self):
        """Odds updates are only sent for 3+ star alerts."""
        notifier, db, telegram = _make_notifier()

        # 2-star alert with huge odds move — should NOT trigger
        alert_2star = _pending_alert(id=1, star_level=2, odds_at_alert=0.30)
        # 3-star alert with big move — SHOULD trigger
        alert_3star = _pending_alert(id=2, star_level=3, odds_at_alert=0.30, market_id="m2")

        db.get_alerts_pending.return_value = [alert_2star, alert_3star]
        db.get_recently_resolved.return_value = []
        db.get_notification_log.return_value = None

        far = datetime.now(timezone.utc) + timedelta(days=10)

        def get_market(mid):
            if mid == "m2":
                return _market(current_odds=0.55, resolution_date=far)
            return _market(current_odds=0.55, resolution_date=far)

        db.get_market.side_effect = get_market

        counts = notifier.run()

        # Only the 3-star alert should produce an odds update
        assert counts["odds_updates"] == 1
        db.log_notification.assert_called_once_with(2, "odds_update")

    def test_odds_update_threshold_15pct(self):
        """Odds updates only fire when change exceeds 15%."""
        notifier, db, telegram = _make_notifier()

        # 10% move — below threshold
        alert_small = _pending_alert(id=1, odds_at_alert=0.50, market_id="m1")
        # 25% move — above threshold
        alert_big = _pending_alert(id=2, odds_at_alert=0.40, market_id="m2")

        db.get_alerts_pending.return_value = [alert_small, alert_big]
        db.get_recently_resolved.return_value = []
        db.get_notification_log.return_value = None

        far = datetime.now(timezone.utc) + timedelta(days=10)

        def get_market(mid):
            if mid == "m1":
                # 0.50 → 0.55 = 10% move
                return _market(current_odds=0.55, resolution_date=far)
            # 0.40 → 0.52 = 30% move
            return _market(current_odds=0.52, resolution_date=far)

        db.get_market.side_effect = get_market

        counts = notifier.run()

        assert counts["odds_updates"] == 1
        db.log_notification.assert_called_once_with(2, "odds_update")

    def test_max_5_odds_updates_per_cycle(self):
        """No more than 5 odds updates per cycle."""
        notifier, db, telegram = _make_notifier()

        # 8 alerts, all with big moves
        alerts = [
            _pending_alert(
                id=i, star_level=4, odds_at_alert=0.30, market_id=f"m{i}"
            )
            for i in range(1, 9)
        ]

        db.get_alerts_pending.return_value = alerts
        db.get_recently_resolved.return_value = []
        db.get_notification_log.return_value = None

        far = datetime.now(timezone.utc) + timedelta(days=10)
        # All markets: 0.30 → 0.60 = 100% move
        db.get_market.return_value = _market(
            current_odds=0.60, resolution_date=far
        )

        counts = notifier.run()

        assert counts["odds_updates"] == MAX_ODDS_UPDATES_PER_CYCLE
        assert db.log_notification.call_count == MAX_ODDS_UPDATES_PER_CYCLE

    def test_odds_update_direction_no(self):
        """Direction NO correctly inverts odds for change calculation."""
        notifier, db, telegram = _make_notifier()

        # NO alert: entry YES=0.70 → NO adj = 0.30
        # Current YES=0.50 → NO adj = 0.50
        # Change = (0.50 - 0.30) / 0.30 = 66.7% → above threshold
        alert = _pending_alert(
            id=1, direction="NO", odds_at_alert=0.70, star_level=4
        )

        db.get_alerts_pending.return_value = [alert]
        db.get_recently_resolved.return_value = []
        db.get_notification_log.return_value = None

        far = datetime.now(timezone.utc) + timedelta(days=10)
        db.get_market.return_value = _market(
            current_odds=0.50, resolution_date=far
        )

        counts = notifier.run()

        assert counts["odds_updates"] == 1


# ── Spam Control ─────────────────────────────────────────


class TestSpamControl:
    def test_spam_control_12h(self):
        """Alerts notified within last 12h are skipped."""
        notifier, db, telegram = _make_notifier()

        alert = _pending_alert(id=1, odds_at_alert=0.30, star_level=4)

        db.get_alerts_pending.return_value = [alert]
        db.get_recently_resolved.return_value = []

        # Notification was sent 3 hours ago — within cooldown
        recent = datetime.now(timezone.utc) - timedelta(hours=3)
        db.get_notification_log.return_value = {
            "alert_id": 1,
            "last_notified_at": recent.isoformat(),
        }

        far = datetime.now(timezone.utc) + timedelta(days=10)
        db.get_market.return_value = _market(
            current_odds=0.60, resolution_date=far
        )

        counts = notifier.run()

        assert counts["odds_updates"] == 0
        db.log_notification.assert_not_called()

    def test_spam_control_allows_after_cooldown(self):
        """Alerts notified >12h ago are allowed again."""
        notifier, db, telegram = _make_notifier()

        alert = _pending_alert(id=1, odds_at_alert=0.30, star_level=4)

        db.get_alerts_pending.return_value = [alert]
        db.get_recently_resolved.return_value = []

        # Notification was sent 15 hours ago — past cooldown
        old = datetime.now(timezone.utc) - timedelta(hours=15)
        db.get_notification_log.return_value = {
            "alert_id": 1,
            "last_notified_at": old.isoformat(),
        }

        far = datetime.now(timezone.utc) + timedelta(days=10)
        db.get_market.return_value = _market(
            current_odds=0.60, resolution_date=far
        )

        counts = notifier.run()

        assert counts["odds_updates"] == 1
        db.log_notification.assert_called_once()


# ── Resolution Summary ───────────────────────────────────


class TestResolutionSummary:
    def test_resolution_summary_correct(self):
        """Correct resolution shows checkmark and return."""
        notifier, db, telegram = _make_notifier()

        resolved = _pending_alert(
            id=1,
            outcome="correct",
            actual_return=38.9,
            time_to_resolution_days=3,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )

        db.get_alerts_pending.return_value = []
        db.get_recently_resolved.return_value = [resolved]

        # Mock all-time query
        all_time_resp = MagicMock()
        all_time_resp.data = [
            {"outcome": "correct", "star_level": 3},
            {"outcome": "incorrect", "star_level": 4},
        ]
        table_mock = MagicMock()
        table_mock.select.return_value = table_mock
        table_mock.neq.return_value = table_mock
        table_mock.gte.return_value = table_mock
        table_mock.execute.return_value = all_time_resp
        db.client.table.return_value = table_mock

        far = datetime.now(timezone.utc) + timedelta(days=10)
        db.get_market.return_value = _market(resolution_date=far)

        counts = notifier.run()

        assert counts["resolutions"] == 1
        msg = telegram.send_message.call_args_list[0][0][0]
        assert "CORRECT" in msg
        assert "+38.9%" in msg
        assert "Session: 1/1" in msg

    def test_resolution_summary_incorrect(self):
        """Incorrect resolution shows X mark."""
        notifier, db, telegram = _make_notifier()

        resolved = _pending_alert(
            id=1,
            outcome="incorrect",
            actual_return=-100.0,
            time_to_resolution_days=5,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )

        db.get_alerts_pending.return_value = []
        db.get_recently_resolved.return_value = [resolved]

        all_time_resp = MagicMock()
        all_time_resp.data = []
        table_mock = MagicMock()
        table_mock.select.return_value = table_mock
        table_mock.neq.return_value = table_mock
        table_mock.gte.return_value = table_mock
        table_mock.execute.return_value = all_time_resp
        db.client.table.return_value = table_mock

        far = datetime.now(timezone.utc) + timedelta(days=10)
        db.get_market.return_value = _market(resolution_date=far)

        counts = notifier.run()

        assert counts["resolutions"] == 1
        msg = telegram.send_message.call_args_list[0][0][0]
        assert "INCORRECT" in msg
        assert "-100.0%" in msg

    def test_resolution_summary_mixed(self):
        """Mixed resolutions show accurate session stats."""
        notifier, db, telegram = _make_notifier()

        resolved = [
            _pending_alert(id=1, outcome="correct", actual_return=50.0,
                          resolved_at=datetime.now(timezone.utc).isoformat()),
            _pending_alert(id=2, outcome="incorrect", actual_return=-100.0,
                          resolved_at=datetime.now(timezone.utc).isoformat()),
        ]

        db.get_alerts_pending.return_value = []
        db.get_recently_resolved.return_value = resolved

        all_time_resp = MagicMock()
        all_time_resp.data = []
        table_mock = MagicMock()
        table_mock.select.return_value = table_mock
        table_mock.neq.return_value = table_mock
        table_mock.gte.return_value = table_mock
        table_mock.execute.return_value = all_time_resp
        db.client.table.return_value = table_mock

        far = datetime.now(timezone.utc) + timedelta(days=10)
        db.get_market.return_value = _market(resolution_date=far)

        counts = notifier.run()

        assert counts["resolutions"] == 2
        msg = telegram.send_message.call_args_list[0][0][0]
        assert "Session: 1/2 correct (50%)" in msg

    def test_no_resolution_summary_when_none_resolved(self):
        """No resolution message if nothing resolved recently."""
        notifier, db, telegram = _make_notifier()

        db.get_alerts_pending.return_value = []
        db.get_recently_resolved.return_value = []

        counts = notifier.run()

        assert counts["resolutions"] == 0
        telegram.send_message.assert_not_called()


# ── Missing Market ──────────────────────────────────────


class TestMissingMarket:
    def test_hours_to_resolution_missing_market(self):
        """Market not found in DB → _hours_to_resolution returns None."""
        notifier, db, telegram = _make_notifier()

        db.get_market.return_value = None

        alert = _pending_alert(market_id="nonexistent_market")
        result = notifier._hours_to_resolution(alert)

        assert result is None
