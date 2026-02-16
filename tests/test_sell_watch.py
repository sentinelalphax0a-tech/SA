"""Tests for the Sell Watch — Metadata-only position exit tracking.

Covers: detection, persistence, format, thresholds, config.
Stars are NEVER modified — sells are separate metadata.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from src import config
from src.database.models import SellEvent
from src.publishing.formatter import AlertFormatter
from src.tracking.whale_monitor import WhaleMonitor


# ── Helpers ──────────────────────────────────────────────


def _make_monitor(formatter=None):
    """Create a WhaleMonitor with mocked dependencies."""
    db = MagicMock()
    pm = MagicMock()
    telegram = MagicMock()
    fmt = formatter or MagicMock()
    monitor = WhaleMonitor(db=db, polymarket=pm, telegram=telegram, formatter=fmt)
    return monitor, db, pm, telegram, fmt


def _whale_alert(**overrides) -> dict:
    """Build an alert dict with sensible defaults."""
    base = {
        "id": 123,
        "market_id": "m1",
        "market_question": "Will X happen?",
        "direction": "YES",
        "odds_at_alert": 0.32,
        "outcome": "pending",
        "star_level": 5,
        "score": 250,
        "total_amount": 20000.0,
        "total_sold_pct": 0,
        "wallets": [
            {
                "address": "0xAAAABBBBCCCCDDDD1111222233334444AABBCCDD",
                "total_amount": 17000,
                "avg_entry_price": 0.32,
                "trade_count": 5,
            }
        ],
        "created_at": "2026-02-14T12:00:00+00:00",
    }
    base.update(overrides)
    return base


# ── TestSellEventCreation ────────────────────────────────


class TestSellEventCreation:
    def test_full_exit_creates_sell_event(self):
        """FULL_EXIT produces a SellEvent with sell_pct >= 0.90."""
        monitor, db, pm, telegram, fmt = _make_monitor()
        db.get_whale_notifications.return_value = []

        alert = _whale_alert()
        wallet = alert["wallets"][0]
        event = {
            "type": "FULL_EXIT",
            "amount_sold": 17000,
            "sell_price": 0.45,
            "entry_price": 0.32,
            "pnl_pct": 40.6,
        }

        monitor._process_sell_event(alert, wallet, event)

        db.insert_sell_event.assert_called_once()
        sell_event = db.insert_sell_event.call_args[0][0]
        assert isinstance(sell_event, SellEvent)
        assert sell_event.sell_pct >= 0.90
        assert sell_event.event_type == "FULL_EXIT"

    def test_partial_exit_creates_sell_event(self):
        """PARTIAL_EXIT produces SellEvent with correct sell_pct."""
        monitor, db, pm, telegram, fmt = _make_monitor()
        db.get_whale_notifications.return_value = []

        alert = _whale_alert()
        wallet = alert["wallets"][0]
        event = {
            "type": "PARTIAL_EXIT",
            "amount_sold": 8500,
            "sell_price": 0.50,
            "pct_sold": 50.0,  # 50% of position
        }

        monitor._process_sell_event(alert, wallet, event)

        db.insert_sell_event.assert_called_once()
        sell_event = db.insert_sell_event.call_args[0][0]
        assert sell_event.sell_pct == 0.50
        assert sell_event.event_type == "PARTIAL_EXIT"

    def test_below_minimum_skipped(self):
        """sell_pct < 0.20 → no SellEvent created."""
        monitor, db, pm, telegram, fmt = _make_monitor()
        db.get_whale_notifications.return_value = []

        alert = _whale_alert()
        wallet = alert["wallets"][0]
        event = {
            "type": "PARTIAL_EXIT",
            "amount_sold": 1700,
            "sell_price": 0.50,
            "pct_sold": 10.0,  # 10% — below 20% threshold
        }

        monitor._process_sell_event(alert, wallet, event)

        db.insert_sell_event.assert_not_called()

    def test_sell_event_fields(self):
        """All fields populated correctly on the SellEvent."""
        monitor, db, pm, telegram, fmt = _make_monitor()
        db.get_whale_notifications.return_value = []

        alert = _whale_alert()
        wallet = alert["wallets"][0]
        event = {
            "type": "FULL_EXIT",
            "amount_sold": 17000,
            "sell_price": 0.45,
            "entry_price": 0.32,
            "pnl_pct": 40.6,
        }

        monitor._process_sell_event(alert, wallet, event)

        sell_event = db.insert_sell_event.call_args[0][0]
        assert sell_event.alert_id == 123
        assert sell_event.wallet_address == "0xAAAABBBBCCCCDDDD1111222233334444AABBCCDD"
        assert sell_event.sell_amount == 17000
        assert sell_event.sell_price == 0.45
        assert sell_event.original_entry_price == 0.32
        assert sell_event.pnl_pct == 40.6
        assert sell_event.held_hours is not None

    def test_cumulative_sold_pct(self):
        """Two partial sells accumulate on alert's total_sold_pct."""
        monitor, db, pm, telegram, fmt = _make_monitor()
        db.get_whale_notifications.return_value = []

        # First sell: 30%
        alert = _whale_alert(total_sold_pct=0)
        wallet = alert["wallets"][0]
        event1 = {
            "type": "PARTIAL_EXIT",
            "amount_sold": 5100,
            "sell_price": 0.40,
            "pct_sold": 30.0,
        }
        monitor._process_sell_event(alert, wallet, event1)

        first_call_pct = db.update_alert_sell_metadata.call_args[0][1]
        assert abs(first_call_pct - 0.30) < 0.01

        # Second sell: 40% (total should be 0.70)
        alert2 = _whale_alert(total_sold_pct=0.30)
        event2 = {
            "type": "PARTIAL_EXIT",
            "amount_sold": 6800,
            "sell_price": 0.45,
            "pct_sold": 40.0,
        }
        monitor._process_sell_event(alert2, wallet, event2)

        second_call_pct = db.update_alert_sell_metadata.call_args[0][1]
        assert abs(second_call_pct - 0.70) < 0.01


# ── TestSellEventPersistence ─────────────────────────────


class TestSellEventPersistence:
    def test_insert_sell_event(self):
        """db.insert_sell_event called with correct SellEvent."""
        monitor, db, pm, telegram, fmt = _make_monitor()
        db.get_whale_notifications.return_value = []

        alert = _whale_alert()
        wallet = alert["wallets"][0]
        event = {
            "type": "FULL_EXIT",
            "amount_sold": 17000,
            "sell_price": 0.45,
            "entry_price": 0.32,
            "pnl_pct": 40.6,
        }

        monitor._process_sell_event(alert, wallet, event)

        db.insert_sell_event.assert_called_once()
        arg = db.insert_sell_event.call_args[0][0]
        assert isinstance(arg, SellEvent)
        assert arg.alert_id == 123

    def test_alert_metadata_updated(self):
        """db.update_alert_sell_metadata called with accumulated pct."""
        monitor, db, pm, telegram, fmt = _make_monitor()
        db.get_whale_notifications.return_value = []

        alert = _whale_alert(total_sold_pct=0.10)
        wallet = alert["wallets"][0]
        event = {
            "type": "PARTIAL_EXIT",
            "amount_sold": 5100,
            "sell_price": 0.40,
            "pct_sold": 30.0,
        }

        monitor._process_sell_event(alert, wallet, event)

        db.update_alert_sell_metadata.assert_called_once()
        call_args = db.update_alert_sell_metadata.call_args[0]
        assert call_args[0] == 123  # alert_id
        assert abs(call_args[1] - 0.40) < 0.01  # 0.10 + 0.30

    def test_star_level_not_modified(self):
        """star_level is NEVER in any update call."""
        monitor, db, pm, telegram, fmt = _make_monitor()
        db.get_whale_notifications.return_value = []

        alert = _whale_alert(star_level=5)
        wallet = alert["wallets"][0]
        event = {
            "type": "FULL_EXIT",
            "amount_sold": 17000,
            "sell_price": 0.45,
            "entry_price": 0.32,
            "pnl_pct": 40.6,
        }

        monitor._process_sell_event(alert, wallet, event)

        # Check all calls to the db mock — none should include star_level
        for method_call in db.method_calls:
            name, args, kwargs = method_call
            if name == "update_alert_sell_metadata":
                # This method only takes alert_id and total_sold_pct
                assert "star_level" not in str(args)
                assert "star_level" not in str(kwargs)
            # Check update_alert_fields is never called with star_level
            if name == "update_alert_fields":
                fields = args[1] if len(args) > 1 else kwargs.get("fields", {})
                assert "star_level" not in fields

    def test_cooldown_prevents_duplicate(self):
        """Second sell within cooldown period is skipped."""
        monitor, db, pm, telegram, fmt = _make_monitor()

        # First call: no existing notifications
        db.get_whale_notifications.return_value = []

        alert = _whale_alert()
        wallet = alert["wallets"][0]
        event = {
            "type": "FULL_EXIT",
            "amount_sold": 17000,
            "sell_price": 0.45,
            "entry_price": 0.32,
            "pnl_pct": 40.6,
        }

        monitor._process_sell_event(alert, wallet, event)
        assert db.insert_sell_event.call_count == 1

        # Second call: SELL_EVENT already logged
        db.get_whale_notifications.return_value = [
            {
                "event_type": "SELL_EVENT",
                "wallet_address": wallet["address"],
            }
        ]

        monitor._process_sell_event(alert, wallet, event)
        # insert_sell_event should NOT be called again
        assert db.insert_sell_event.call_count == 1


# ── TestSellWatchNotification ────────────────────────────


class TestSellWatchNotification:
    def test_telegram_sent_for_4_star(self):
        """4★ alert sell → telegram.send_message called."""
        monitor, db, pm, telegram, fmt = _make_monitor()
        db.get_whale_notifications.return_value = []
        fmt.format_sell_watch.return_value = "sell watch message"

        alert = _whale_alert(star_level=4)
        wallet = alert["wallets"][0]
        event = {
            "type": "FULL_EXIT",
            "amount_sold": 17000,
            "sell_price": 0.45,
            "entry_price": 0.32,
            "pnl_pct": 40.6,
        }

        monitor._process_sell_event(alert, wallet, event)

        telegram.send_message.assert_called_once()
        fmt.format_sell_watch.assert_called_once()

    def test_telegram_not_sent_for_3_star(self):
        """3★ alert sell → telegram NOT called (below SELL_WATCH_NOTIFY_MIN_STARS)."""
        monitor, db, pm, telegram, fmt = _make_monitor()
        db.get_whale_notifications.return_value = []

        alert = _whale_alert(star_level=3)
        wallet = alert["wallets"][0]
        event = {
            "type": "FULL_EXIT",
            "amount_sold": 17000,
            "sell_price": 0.45,
            "entry_price": 0.32,
            "pnl_pct": 40.6,
        }

        monitor._process_sell_event(alert, wallet, event)

        # Sell event is persisted
        db.insert_sell_event.assert_called_once()
        # But no Telegram notification
        telegram.send_message.assert_not_called()

    def test_format_contains_score_preserved(self):
        """Output contains 'Score preserved'."""
        formatter = AlertFormatter()
        alert = _whale_alert()
        sell_event = SellEvent(
            alert_id=123,
            wallet_address="0xAAAA",
            sell_amount=17000,
            sell_pct=0.85,
            event_type="FULL_EXIT",
            sell_price=0.45,
            original_entry_price=0.32,
            pnl_pct=40.6,
        )

        output = formatter.format_sell_watch(alert, sell_event, "14h")
        assert "Score preserved" in output

    def test_format_contains_pnl_and_held(self):
        """Output contains P&L and held duration."""
        formatter = AlertFormatter()
        alert = _whale_alert()
        sell_event = SellEvent(
            alert_id=123,
            wallet_address="0xAAAA",
            sell_amount=17000,
            sell_pct=0.85,
            event_type="FULL_EXIT",
            sell_price=0.45,
            original_entry_price=0.32,
            pnl_pct=40.6,
        )

        output = formatter.format_sell_watch(alert, sell_event, "14h")
        assert "40.6%" in output
        assert "14h" in output


# ── TestFormatSellWatch ──────────────────────────────────


class TestFormatSellWatch:
    def _make_sell_event(self, **overrides):
        base = dict(
            alert_id=123,
            wallet_address="0xAAAABBBBCCCCDDDD1111222233334444AABBCCDD",
            sell_amount=17000,
            sell_pct=0.85,
            event_type="FULL_EXIT",
            sell_price=0.45,
            original_entry_price=0.32,
            pnl_pct=40.6,
        )
        base.update(overrides)
        return SellEvent(**base)

    def test_full_exit_label(self):
        """'FULL EXIT' in output."""
        formatter = AlertFormatter()
        alert = _whale_alert()
        sell_event = self._make_sell_event(event_type="FULL_EXIT")

        output = formatter.format_sell_watch(alert, sell_event, "14h")
        assert "FULL EXIT" in output

    def test_partial_exit_with_pct(self):
        """'PARTIAL EXIT' and percentage in output."""
        formatter = AlertFormatter()
        alert = _whale_alert()
        sell_event = self._make_sell_event(
            event_type="PARTIAL_EXIT",
            sell_pct=0.50,
            sell_amount=8500,
        )

        output = formatter.format_sell_watch(alert, sell_event, "6h")
        assert "PARTIAL EXIT" in output
        assert "50%" in output

    def test_alert_id_and_question(self):
        """Alert ID and market question present."""
        formatter = AlertFormatter()
        alert = _whale_alert()
        sell_event = self._make_sell_event()

        output = formatter.format_sell_watch(alert, sell_event, None)
        assert "#123" in output
        assert "Will X happen?" in output

    def test_star_emoji_shown(self):
        """Star level shown in output."""
        formatter = AlertFormatter()
        alert = _whale_alert(star_level=5)
        sell_event = self._make_sell_event()

        output = formatter.format_sell_watch(alert, sell_event, None)
        assert "5\u2605" in output

    def test_entry_and_sell_price(self):
        """Entry and sell prices formatted correctly."""
        formatter = AlertFormatter()
        alert = _whale_alert()
        sell_event = self._make_sell_event(
            sell_price=0.45,
            original_entry_price=0.32,
        )

        output = formatter.format_sell_watch(alert, sell_event, None)
        assert "0.45" in output
        assert "0.32" in output


# ── TestWhaleMonitorThreshold ────────────────────────────


class TestWhaleMonitorThreshold:
    def test_monitors_3_star_alerts(self):
        """STAR_THRESHOLD == 3."""
        assert WhaleMonitor.STAR_THRESHOLD == 3

    def test_sell_event_alongside_whale_update(self):
        """Both whale update and sell event fire for FULL_EXIT."""
        monitor, db, pm, telegram, fmt = _make_monitor()

        alert = _whale_alert(star_level=5)
        db.get_high_star_alerts.return_value = [alert]
        db.get_whale_notifications.return_value = []
        db.get_recent_alerts_with_wallet.return_value = []
        fmt.format_sell_watch.return_value = "sell watch msg"

        address = alert["wallets"][0]["address"]

        # Simulate a full exit via trades
        class FakeTrade:
            def __init__(self, wa, d, a, p, t):
                self.wallet_address = wa
                self.direction = d
                self.amount = a
                self.price = p
                self.timestamp = t

        # Sell all: 17000 in opposite direction (NO when alert is YES)
        pm.get_recent_trades.return_value = [
            FakeTrade(address, "NO", 17000, 0.55, "2026-02-14T10:00:00Z"),
        ]

        result = monitor.run()

        # Whale update (send_message) called for the FULL_EXIT event
        assert telegram.send_message.call_count >= 1
        # Sell event also persisted
        db.insert_sell_event.assert_called_once()

    def test_existing_whale_events_unchanged(self):
        """ADDITIONAL_BUY and NEW_MARKET still work as before."""
        monitor, db, pm, telegram, fmt = _make_monitor()

        alert = _whale_alert(star_level=4)
        wallet = alert["wallets"][0]

        # ADDITIONAL_BUY event
        activity = {
            "sell_amount": 0,
            "buy_amount": 5000,
            "last_sell_price": None,
            "last_buy_price": 0.40,
            "new_market": None,
        }
        events = monitor._detect_events(wallet, activity, alert)

        buys = [e for e in events if e["type"] == "ADDITIONAL_BUY"]
        assert len(buys) == 1
        assert buys[0]["new_amount"] == 5000

        # NEW_MARKET event
        activity2 = {
            "sell_amount": 0,
            "buy_amount": 0,
            "last_sell_price": None,
            "last_buy_price": None,
            "new_market": {
                "new_market_question": "Will Y happen?",
                "new_market_id": "m2",
                "new_direction": "NO",
                "new_amount": 8000,
            },
        }
        events2 = monitor._detect_events(wallet, activity2, alert)

        new_markets = [e for e in events2 if e["type"] == "NEW_MARKET"]
        assert len(new_markets) == 1


# ── TestConfig ───────────────────────────────────────────


class TestConfig:
    def test_sell_watch_constants_exist(self):
        """All SELL_WATCH_* constants accessible."""
        assert hasattr(config, "SELL_WATCH_MIN_STARS")
        assert hasattr(config, "SELL_WATCH_MIN_SELL_PCT")
        assert hasattr(config, "SELL_WATCH_COOLDOWN_HOURS")
        assert hasattr(config, "SELL_WATCH_NOTIFY_MIN_STARS")

    def test_sell_watch_defaults(self):
        """Verify default values."""
        assert config.SELL_WATCH_MIN_STARS == 3
        assert config.SELL_WATCH_MIN_SELL_PCT == 0.20
        assert config.SELL_WATCH_COOLDOWN_HOURS == 6
        assert config.SELL_WATCH_NOTIFY_MIN_STARS == 4
