"""Tests for the whale monitor module."""

from unittest.mock import MagicMock, call

from src.tracking.whale_monitor import WhaleMonitor, PARTIAL_EXIT_THRESHOLD


def _make_monitor():
    """Create a WhaleMonitor with mocked dependencies."""
    db = MagicMock()
    pm = MagicMock()
    telegram = MagicMock()
    monitor = WhaleMonitor(db=db, polymarket=pm, telegram=telegram)
    return monitor, db, pm, telegram


def _whale_alert(**overrides) -> dict:
    """Build a whale alert dict with sensible defaults (4+ stars)."""
    base = {
        "id": 1,
        "market_id": "m1",
        "market_question": "Will X happen?",
        "direction": "YES",
        "odds_at_alert": 0.35,
        "outcome": "pending",
        "star_level": 4,
        "score": 200,
        "total_amount": 10000.0,
        "wallets": [
            {
                "address": "0xAAAABBBBCCCCDDDD1111222233334444AABBCCDD",
                "total_amount": 10000,
                "avg_entry_price": 0.35,
                "trade_count": 3,
            }
        ],
        "created_at": "2026-02-10T12:00:00+00:00",
    }
    base.update(overrides)
    return base


class _FakeTrade:
    """Minimal trade object matching TradeEvent interface."""

    def __init__(self, wallet_address, direction, amount, price, timestamp="2026-02-14T10:00:00Z"):
        self.wallet_address = wallet_address
        self.direction = direction
        self.amount = amount
        self.price = price
        self.timestamp = timestamp


# ── Event Detection ──────────────────────────────────────


class TestDetectFullExit:
    def test_detects_full_exit(self):
        """Full exit detected when wallet sells >= 90% of position."""
        monitor, db, pm, telegram = _make_monitor()

        alert = _whale_alert()
        wallet = alert["wallets"][0]

        # Wallet sold 9500 out of 10000 (95%) → full exit
        activity = {
            "sell_amount": 9500,
            "buy_amount": 0,
            "last_sell_price": 0.55,
            "last_buy_price": None,
            "new_market": None,
        }

        events = monitor._detect_events(wallet, activity, alert)

        full_exits = [e for e in events if e["type"] == "FULL_EXIT"]
        assert len(full_exits) == 1
        assert full_exits[0]["amount_sold"] == 9500
        assert full_exits[0]["sell_price"] == 0.55


class TestDetectPartialExit:
    def test_detects_partial_exit_above_30pct(self):
        """Partial exit detected when wallet sells >30% but <90%."""
        monitor, db, pm, telegram = _make_monitor()

        alert = _whale_alert()
        wallet = alert["wallets"][0]

        # Sold 5000 out of 10000 (50%) → partial exit
        activity = {
            "sell_amount": 5000,
            "buy_amount": 0,
            "last_sell_price": 0.50,
            "last_buy_price": None,
            "new_market": None,
        }

        events = monitor._detect_events(wallet, activity, alert)

        partial = [e for e in events if e["type"] == "PARTIAL_EXIT"]
        assert len(partial) == 1
        assert partial[0]["amount_sold"] == 5000
        assert partial[0]["pct_sold"] == 50.0
        assert partial[0]["remaining"] == 5000

    def test_ignores_partial_exit_below_30pct(self):
        """No partial exit event when sell amount is < 30% of position."""
        monitor, db, pm, telegram = _make_monitor()

        alert = _whale_alert()
        wallet = alert["wallets"][0]

        # Sold 2000 out of 10000 (20%) → below threshold
        activity = {
            "sell_amount": 2000,
            "buy_amount": 0,
            "last_sell_price": 0.50,
            "last_buy_price": None,
            "new_market": None,
        }

        events = monitor._detect_events(wallet, activity, alert)

        partial = [e for e in events if e["type"] == "PARTIAL_EXIT"]
        assert len(partial) == 0
        full = [e for e in events if e["type"] == "FULL_EXIT"]
        assert len(full) == 0


class TestDetectAdditionalBuy:
    def test_detects_additional_buy(self):
        """Additional buy detected when wallet buys more in same market."""
        monitor, db, pm, telegram = _make_monitor()

        alert = _whale_alert()
        wallet = alert["wallets"][0]

        activity = {
            "sell_amount": 0,
            "buy_amount": 3000,
            "last_sell_price": None,
            "last_buy_price": 0.40,
            "new_market": None,
        }

        events = monitor._detect_events(wallet, activity, alert)

        buys = [e for e in events if e["type"] == "ADDITIONAL_BUY"]
        assert len(buys) == 1
        assert buys[0]["new_amount"] == 3000
        assert buys[0]["new_total"] == 13000  # 10000 + 3000
        assert buys[0]["buy_price"] == 0.40


class TestDetectNewMarket:
    def test_detects_new_market_entry(self):
        """New market entry detected when same wallet appears in another alert."""
        monitor, db, pm, telegram = _make_monitor()

        alert = _whale_alert()
        wallet = alert["wallets"][0]

        activity = {
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

        events = monitor._detect_events(wallet, activity, alert)

        new_market = [e for e in events if e["type"] == "NEW_MARKET"]
        assert len(new_market) == 1
        assert new_market[0]["market_question"] == "Will Y happen?"
        assert new_market[0]["direction"] == "NO"
        assert new_market[0]["amount"] == 8000


# ── Star Filtering ───────────────────────────────────────


class TestStarFiltering:
    def test_only_monitors_4plus_stars(self):
        """WhaleMonitor only processes alerts returned by get_high_star_alerts."""
        monitor, db, pm, telegram = _make_monitor()

        # DB returns only 4+ star alerts (the monitor asks for min_stars=4)
        alert_4star = _whale_alert(id=1, star_level=4)
        alert_5star = _whale_alert(id=2, star_level=5)
        db.get_high_star_alerts.return_value = [alert_4star, alert_5star]
        db.get_whale_notifications.return_value = []

        # No recent activity — just testing that both alerts are processed
        pm.get_recent_trades.return_value = []
        db.get_recent_alerts_with_wallet.return_value = []

        monitor.run()

        # Verify get_high_star_alerts was called with min_stars=4
        db.get_high_star_alerts.assert_called_once_with(min_stars=4)


# ── Deduplication ────────────────────────────────────────


class TestDeduplication:
    def test_no_duplicate_notifications(self):
        """Already-notified events are not sent again."""
        monitor, db, pm, telegram = _make_monitor()

        alert = _whale_alert()
        address = alert["wallets"][0]["address"]

        db.get_high_star_alerts.return_value = [alert]

        # Simulate existing notification for ADDITIONAL_BUY
        db.get_whale_notifications.return_value = [
            {"event_type": "ADDITIONAL_BUY", "wallet_address": address},
        ]
        db.get_recent_alerts_with_wallet.return_value = []

        # Trades that would trigger an additional buy event
        pm.get_recent_trades.return_value = [
            _FakeTrade(
                wallet_address=address,
                direction="YES",
                amount=5000,
                price=0.40,
            ),
        ]

        result = monitor.run()

        # Event was detected but already notified → no message sent
        assert result == 0
        telegram.send_message.assert_not_called()


# ── Message Formatting ───────────────────────────────────


class TestFormatting:
    def test_formats_whale_update_full_exit(self):
        """Full exit message contains expected markers."""
        monitor, db, pm, telegram = _make_monitor()

        alert = _whale_alert()
        wallet = alert["wallets"][0]
        event = {
            "type": "FULL_EXIT",
            "sell_price": 0.55,
            "entry_price": 0.35,
            "pnl_pct": 57.1,
            "amount_sold": 10000,
        }

        msg = monitor._format_whale_update(alert, wallet, event)

        assert "WHALE UPDATE" in msg
        assert "FULL EXIT" in msg
        assert "Will X happen?" in msg
        assert "$10,000" in msg
        assert "0.55" in msg
        assert "+57.1%" in msg
        assert "exited" in msg.lower()
        # Wallet link present
        assert "polygonscan.com" in msg

    def test_formats_whale_update_partial_exit(self):
        """Partial exit message contains expected markers."""
        monitor, db, pm, telegram = _make_monitor()

        alert = _whale_alert()
        wallet = alert["wallets"][0]
        event = {
            "type": "PARTIAL_EXIT",
            "amount_sold": 5000,
            "remaining": 5000,
            "sell_price": 0.50,
            "pct_sold": 50.0,
        }

        msg = monitor._format_whale_update(alert, wallet, event)

        assert "WHALE UPDATE" in msg
        assert "PARTIAL EXIT" in msg
        assert "$5,000" in msg
        assert "50%" in msg
        assert "Remaining: $5,000" in msg
        assert "taking profits" in msg.lower()
