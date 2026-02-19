"""Tests for the sell detector module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.analysis.sell_detector import SellDetector
from src.database.models import TradeEvent


def _trade(
    wallet: str = "0xabc",
    direction: str = "NO",
    amount: float = 1000.0,
    minutes_ago: int = 10,
) -> TradeEvent:
    return TradeEvent(
        wallet_address=wallet,
        market_id="m1",
        direction=direction,
        amount=amount,
        price=0.30,
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


class TestSellDetector:
    def test_no_db_returns_empty(self):
        sd = SellDetector()
        assert sd.check_open_positions() == []

    def test_no_open_positions_returns_empty(self):
        db = MagicMock()
        db.get_open_positions.return_value = []
        sd = SellDetector(db_client=db)
        assert sd.check_open_positions() == []

    def test_individual_sell_detected(self):
        db = MagicMock()
        pm = MagicMock()

        db.get_open_positions.return_value = [
            {
                "wallet_address": "0xabc",
                "market_id": "m1",
                "direction": "YES",
                "total_amount": 5000.0,
            }
        ]
        db.get_market.return_value = {"question": "Will X happen?"}

        # Wallet sells (trades in opposite direction)
        pm.get_recent_trades.return_value = [
            _trade(wallet="0xabc", direction="NO", amount=2000.0),
        ]

        sd = SellDetector(db_client=db, polymarket_client=pm)
        events = sd.check_open_positions()

        assert len(events) == 1
        assert events[0]["type"] == "individual"
        assert events[0]["wallets"][0]["sell_amount"] == 2000.0
        db.update_position_sold.assert_called_once()

    def test_coordinated_sell_detected(self):
        db = MagicMock()
        pm = MagicMock()

        db.get_open_positions.return_value = [
            {
                "wallet_address": "0xabc",
                "market_id": "m1",
                "direction": "YES",
                "total_amount": 5000.0,
            },
            {
                "wallet_address": "0xdef",
                "market_id": "m1",
                "direction": "YES",
                "total_amount": 3000.0,
            },
        ]
        db.get_market.return_value = {"question": "Will X happen?"}

        now = datetime.now(timezone.utc)
        pm.get_recent_trades.return_value = [
            TradeEvent(
                wallet_address="0xabc", market_id="m1", direction="NO",
                amount=2000.0, price=0.30, timestamp=now - timedelta(minutes=10),
            ),
            TradeEvent(
                wallet_address="0xdef", market_id="m1", direction="NO",
                amount=1500.0, price=0.30, timestamp=now - timedelta(minutes=5),
            ),
        ]

        sd = SellDetector(db_client=db, polymarket_client=pm)
        events = sd.check_open_positions()

        assert len(events) == 1
        assert events[0]["type"] == "coordinated"
        assert len(events[0]["wallets"]) == 2

    def test_partial_sell_passes_original_amount(self):
        """update_position_sold receives original_amount so DB can classify partial vs full."""
        db = MagicMock()
        pm = MagicMock()

        db.get_open_positions.return_value = [
            {
                "wallet_address": "0xabc",
                "market_id": "m1",
                "direction": "YES",
                "total_amount": 5000.0,   # ← original position size
            }
        ]
        db.get_market.return_value = {"question": "Will X happen?"}

        # Wallet sells only 30% of position (partial)
        pm.get_recent_trades.return_value = [
            _trade(wallet="0xabc", direction="NO", amount=1500.0),
        ]

        sd = SellDetector(db_client=db, polymarket_client=pm)
        sd.check_open_positions()

        call_kwargs = db.update_position_sold.call_args
        assert call_kwargs is not None
        # original_amount must be passed so the DB method can apply the threshold
        assert call_kwargs.kwargs.get("original_amount") == 5000.0

    def test_no_sell_no_event(self):
        db = MagicMock()
        pm = MagicMock()

        db.get_open_positions.return_value = [
            {
                "wallet_address": "0xabc",
                "market_id": "m1",
                "direction": "YES",
                "total_amount": 5000.0,
            }
        ]

        # All trades are in same direction (not selling)
        pm.get_recent_trades.return_value = [
            _trade(wallet="0xabc", direction="YES", amount=500.0),
        ]

        sd = SellDetector(db_client=db, polymarket_client=pm)
        events = sd.check_open_positions()
        assert events == []


class TestPartialSoldThreshold:
    """Tests for the partial/full sell classification in update_position_sold."""

    def _call_update(self, sell_amount, original_amount):
        """Call update_position_sold and return the status that was written."""
        from unittest.mock import patch, MagicMock
        from src.database.supabase_client import SupabaseClient

        client = SupabaseClient.__new__(SupabaseClient)
        captured = {}

        mock_table = MagicMock()
        mock_table.update.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.execute.return_value = MagicMock()

        def capture_update(data):
            captured["status"] = data.get("current_status")
            return mock_table

        mock_table.update.side_effect = capture_update
        client.client = MagicMock()
        client.client.table.return_value = mock_table

        from datetime import datetime, timezone
        client.update_position_sold(
            wallet_address="0xabc",
            market_id="m1",
            sell_amount=sell_amount,
            sell_timestamp=datetime.now(timezone.utc),
            original_amount=original_amount,
        )
        return captured.get("status")

    def test_full_sell_above_threshold(self):
        # 100% sold → "sold"
        assert self._call_update(sell_amount=5000.0, original_amount=5000.0) == "sold"

    def test_full_sell_exactly_at_threshold(self):
        # 85% of original → "sold" (boundary inclusive)
        assert self._call_update(sell_amount=850.0, original_amount=1000.0) == "sold"

    def test_partial_sell_below_threshold(self):
        # 50% sold → "partial_sold"
        assert self._call_update(sell_amount=500.0, original_amount=1000.0) == "partial_sold"

    def test_small_partial_sell(self):
        # 10% sold → "partial_sold"
        assert self._call_update(sell_amount=100.0, original_amount=1000.0) == "partial_sold"

    def test_no_original_amount_defaults_to_sold(self):
        # original_amount=0 → unknown, assume full sell
        assert self._call_update(sell_amount=1000.0, original_amount=0.0) == "sold"
