"""
Sell Detector — monitors open positions for sell activity.

Notification-only system (no scoring impact):
  - Individual sell: single wallet reduces/exits position
  - Coordinated sell: 2+ wallets sell same market within 4h
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from src import config
from src.database.models import TradeEvent

logger = logging.getLogger(__name__)


class SellDetector:
    """Detects sell activity on tracked positions."""

    def __init__(self, db_client=None, polymarket_client=None) -> None:
        self.db = db_client
        self.pm = polymarket_client

    def check_open_positions(self) -> list[dict]:
        """Check all open positions for sell activity.

        Returns a list of sell events:
            {
                "type": "individual" | "coordinated",
                "market_id": str,
                "market_question": str | None,
                "wallets": [{"address": str, "sell_amount": float, ...}],
                "timestamp": datetime,
            }
        """
        if self.db is None:
            return []

        try:
            open_positions = self.db.get_open_positions()
        except Exception as e:
            logger.error("Failed to fetch open positions: %s", e)
            return []

        if not open_positions:
            return []

        # Group positions by market
        by_market: dict[str, list[dict]] = defaultdict(list)
        for pos in open_positions:
            by_market[pos["market_id"]].append(pos)

        sell_events: list[dict] = []

        for market_id, positions in by_market.items():
            market_sells = self._check_market_sells(market_id, positions)
            sell_events.extend(market_sells)

        return sell_events

    def _check_market_sells(
        self, market_id: str, positions: list[dict]
    ) -> list[dict]:
        """Check if any tracked wallets have sold in a specific market."""
        if self.pm is None:
            return []

        # Fetch recent trades for this market
        try:
            trades = self.pm.get_recent_trades(
                market_id=market_id,
                minutes=config.SCAN_LOOKBACK_MINUTES,
                min_amount=config.MIN_TX_AMOUNT,
            )
        except Exception as e:
            logger.debug("Failed to fetch trades for %s: %s", market_id, e)
            return []

        if not trades:
            return []

        # Check each position for opposing trades (sells)
        sell_wallets: list[dict] = []
        for pos in positions:
            wallet_addr = pos["wallet_address"]
            direction = pos["direction"]

            # Find trades by this wallet in opposite direction (= selling)
            sell_trades = [
                t for t in trades
                if t.wallet_address == wallet_addr and t.direction != direction
            ]
            if not sell_trades:
                continue

            sell_amount = sum(t.amount for t in sell_trades)
            sell_ts = max(t.timestamp for t in sell_trades)

            sell_wallets.append({
                "address": wallet_addr,
                "sell_amount": sell_amount,
                "original_amount": pos.get("total_amount", 0),
                "direction": direction,
                "timestamp": sell_ts,
            })

            # Update position in DB
            try:
                self.db.update_position_sold(
                    wallet_address=wallet_addr,
                    market_id=market_id,
                    sell_amount=sell_amount,
                    sell_timestamp=sell_ts,
                )
            except Exception as e:
                logger.debug("Failed to update position sold: %s", e)

        if not sell_wallets:
            return []

        # Get market question for notification
        market_question = None
        try:
            market_data = self.db.get_market(market_id)
            if market_data:
                market_question = market_data.get("question")
        except Exception:
            pass

        # Check if coordinated (2+ sells within window)
        events: list[dict] = []
        if len(sell_wallets) >= config.SELL_COORDINATED_MIN_WALLETS:
            # Check temporal proximity
            timestamps = [w["timestamp"] for w in sell_wallets]
            window = timedelta(hours=config.SELL_COORDINATED_WINDOW_HOURS)
            if max(timestamps) - min(timestamps) <= window:
                events.append({
                    "type": "coordinated",
                    "market_id": market_id,
                    "market_question": market_question,
                    "wallets": sell_wallets,
                    "timestamp": max(timestamps),
                })
                return events

        # Individual sells
        for sw in sell_wallets:
            events.append({
                "type": "individual",
                "market_id": market_id,
                "market_question": market_question,
                "wallets": [sw],
                "timestamp": sw["timestamp"],
            })

        return events
