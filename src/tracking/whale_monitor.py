"""
Whale Monitor — Active monitoring of wallets from 4-5 star alerts.

Runs every 6 hours (after tracker + notifier) to detect:
  A. Full exit: wallet sold entire position
  B. Partial exit: wallet sold >30% of position
  C. Additional buy: wallet bought more in the same market
  D. New market entry: same wallet appeared in a different alert

Publishes formatted updates to Telegram.
"""

import logging
from datetime import datetime, timedelta, timezone

from dateutil import parser as dt_parser

logger = logging.getLogger(__name__)

PARTIAL_EXIT_THRESHOLD = 0.30  # 30% of position
LOOKBACK_MINUTES = 6 * 60  # 6 hours — matches tracker cycle


class WhaleMonitor:
    """Monitors wallets from 4-5 star alerts for new activity."""

    STAR_THRESHOLD = 4

    def __init__(self, db, polymarket, telegram) -> None:
        self.db = db
        self.pm = polymarket
        self.telegram = telegram

    def run(self) -> int:
        """Execute the full whale monitoring cycle.

        Returns the number of events detected and notified.
        """
        whale_alerts = self.db.get_high_star_alerts(min_stars=self.STAR_THRESHOLD)
        if not whale_alerts:
            logger.info("No high-star alerts to monitor")
            return 0

        events_sent = 0
        for alert in whale_alerts:
            wallets = alert.get("wallets") or []
            for wallet in wallets:
                address = wallet.get("address")
                if not address:
                    continue

                try:
                    activity = self._get_recent_activity(address, alert)
                    events = self._detect_events(wallet, activity, alert)

                    for event in events:
                        if self._already_notified(
                            alert["id"], event["type"], address
                        ):
                            continue
                        self._send_whale_update(alert, wallet, event)
                        self._log_notification(
                            alert["id"], event["type"], address, event
                        )
                        events_sent += 1
                except Exception as e:
                    logger.error(
                        "Whale monitor error for alert #%s wallet %s: %s",
                        alert.get("id"),
                        address[:10],
                        e,
                    )

        logger.info("Whale monitor: %d events sent", events_sent)
        return events_sent

    # ── Activity Fetching ────────────────────────────────────

    def _get_recent_activity(self, address: str, alert: dict) -> dict:
        """Fetch recent trading activity for a wallet in the alert's market."""
        market_id = alert.get("market_id", "")
        direction = (alert.get("direction") or "YES").upper()

        trades = self.pm.get_recent_trades(
            market_id=market_id,
            minutes=LOOKBACK_MINUTES,
        )

        wallet_trades = [t for t in trades if t.wallet_address == address]

        sell_trades = [t for t in wallet_trades if t.direction != direction]
        buy_trades = [t for t in wallet_trades if t.direction == direction]

        sell_amount = sum(t.amount for t in sell_trades)
        buy_amount = sum(t.amount for t in buy_trades)

        last_sell_price = None
        if sell_trades:
            last_sell = max(sell_trades, key=lambda t: t.timestamp)
            last_sell_price = last_sell.price

        last_buy_price = None
        if buy_trades:
            last_buy = max(buy_trades, key=lambda t: t.timestamp)
            last_buy_price = last_buy.price

        # Check for new market entries by this wallet
        new_market = self._check_new_markets(address, alert)

        return {
            "sell_amount": sell_amount,
            "buy_amount": buy_amount,
            "last_sell_price": last_sell_price,
            "last_buy_price": last_buy_price,
            "new_market": new_market,
        }

    def _check_new_markets(self, address: str, alert: dict) -> dict | None:
        """Check if this wallet appeared in a different recent alert."""
        alert_id = alert.get("id")
        alert_created = alert.get("created_at", "")

        try:
            recent = self.db.get_recent_alerts_with_wallet(
                wallet_address=address, hours=LOOKBACK_MINUTES // 60
            )
        except Exception:
            return None

        for other in recent:
            if other.get("id") == alert_id:
                continue
            if other.get("market_id") == alert.get("market_id"):
                continue

            # Find the wallet's amount in the other alert
            other_wallets = other.get("wallets") or []
            for w in other_wallets:
                if w.get("address") == address:
                    return {
                        "new_market_question": other.get("market_question", "?"),
                        "new_market_id": other.get("market_id", ""),
                        "new_direction": other.get("direction", "?"),
                        "new_amount": w.get("total_amount", 0),
                    }

        return None

    # ── Event Detection ──────────────────────────────────────

    def _detect_events(
        self, wallet: dict, activity: dict, alert: dict
    ) -> list[dict]:
        """Detect significant events from recent wallet activity."""
        events: list[dict] = []
        position_amount = wallet.get("total_amount", 0)

        if position_amount <= 0:
            return events

        sell_amount = activity.get("sell_amount", 0)
        buy_amount = activity.get("buy_amount", 0)

        # A. Full exit: sold >= 90% of position
        if sell_amount > 0 and sell_amount >= position_amount * 0.90:
            sell_price = activity.get("last_sell_price")
            entry_price = wallet.get(
                "avg_entry_price", alert.get("odds_at_alert")
            )
            direction = (alert.get("direction") or "YES").upper()
            sell_adj = self._direction_adjust(sell_price, direction) if sell_price else None
            entry_adj = self._direction_adjust(entry_price, direction) if entry_price else None
            pnl_pct = None
            if sell_adj and entry_adj and entry_adj > 0:
                pnl_pct = ((sell_adj - entry_adj) / entry_adj) * 100

            events.append({
                "type": "FULL_EXIT",
                "sell_price": sell_price,
                "entry_price": entry_price,
                "pnl_pct": pnl_pct,
                "amount_sold": sell_amount,
            })

        # B. Partial exit: sold > 30% but < 90%
        elif sell_amount > 0 and sell_amount >= position_amount * PARTIAL_EXIT_THRESHOLD:
            events.append({
                "type": "PARTIAL_EXIT",
                "amount_sold": sell_amount,
                "remaining": position_amount - sell_amount,
                "sell_price": activity.get("last_sell_price"),
                "pct_sold": (sell_amount / position_amount) * 100,
            })

        # C. Additional buy in the same market
        if buy_amount > 0:
            events.append({
                "type": "ADDITIONAL_BUY",
                "new_amount": buy_amount,
                "new_total": position_amount + buy_amount,
                "buy_price": activity.get("last_buy_price"),
            })

        # D. New market entry
        new_market = activity.get("new_market")
        if new_market:
            events.append({
                "type": "NEW_MARKET",
                "market_question": new_market.get("new_market_question", "?"),
                "market_id": new_market.get("new_market_id", ""),
                "direction": new_market.get("new_direction", "?"),
                "amount": new_market.get("new_amount", 0),
            })

        return events

    # ── Notification ─────────────────────────────────────────

    def _send_whale_update(
        self, alert: dict, wallet: dict, event: dict
    ) -> None:
        """Format and send a whale update to Telegram."""
        text = self._format_whale_update(alert, wallet, event)
        self.telegram.send_message(text, parse_mode="")

    def _format_whale_update(
        self, alert: dict, wallet: dict, event: dict
    ) -> str:
        """Build the whale update message text."""
        alert_id = alert.get("id", "?")
        question = alert.get("market_question", "?")
        star = alert.get("star_level") or 0
        direction = alert.get("direction") or "?"
        address = wallet.get("address", "")
        short_addr = (
            f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address
        )

        lines = [
            f"\U0001f40b WHALE UPDATE \u2014 ALERT #{alert_id}",
            "\u2501" * 32,
            f'\U0001f4cc "{question}"',
            f"\u2b50 {star}\u2605 | Direction: {direction}",
            "",
        ]

        etype = event["type"]

        if etype == "FULL_EXIT":
            sell_price = event.get("sell_price")
            entry_price = event.get("entry_price")
            pnl_pct = event.get("pnl_pct")
            amount = event.get("amount_sold", 0)

            lines.append(f"\U0001f534 FULL EXIT by {short_addr}")
            if sell_price is not None:
                lines.append(f"   Sold: ${amount:,.0f} @ {sell_price:.2f}")
            else:
                lines.append(f"   Sold: ${amount:,.0f}")
            if entry_price is not None:
                w_total = wallet.get("total_amount", amount)
                lines.append(f"   Entry: ${w_total:,.0f} @ {entry_price:.2f}")
            if pnl_pct is not None:
                pnl_dollar = amount * (pnl_pct / 100)
                if pnl_pct >= 0:
                    lines.append(f"   P&L: +${pnl_dollar:,.0f} (+{pnl_pct:.1f}%)")
                else:
                    lines.append(f"   P&L: -${abs(pnl_dollar):,.0f} ({pnl_pct:.1f}%)")

            held = self._held_duration(alert)
            if held:
                lines.append(f"   Held: {held}")
            lines.append(
                "   \u26a0\ufe0f Whale has exited \u2014 consider this resolved"
            )

        elif etype == "PARTIAL_EXIT":
            amount_sold = event.get("amount_sold", 0)
            remaining = event.get("remaining", 0)
            sell_price = event.get("sell_price")
            pct = event.get("pct_sold", 0)

            lines.append(f"\U0001f7e1 PARTIAL EXIT by {short_addr}")
            price_str = f" @ {sell_price:.2f}" if sell_price is not None else ""
            lines.append(
                f"   Sold: ${amount_sold:,.0f}{price_str} ({pct:.0f}% of position)"
            )
            lines.append(f"   Remaining: ${remaining:,.0f}")
            lines.append(
                "   \u26a0\ufe0f Whale taking profits but still holding"
            )

        elif etype == "ADDITIONAL_BUY":
            new_amount = event.get("new_amount", 0)
            new_total = event.get("new_total", 0)
            buy_price = event.get("buy_price")

            lines.append(f"\U0001f7e2 DOUBLED DOWN by {short_addr}")
            price_str = f" @ {buy_price:.2f}" if buy_price is not None else ""
            lines.append(f"   New buy: ${new_amount:,.0f}{price_str}")
            lines.append(f"   Total position: ${new_total:,.0f}")
            lines.append(
                "   \U0001f4aa Whale increasing conviction"
            )

        elif etype == "NEW_MARKET":
            new_q = event.get("market_question", "?")
            new_dir = event.get("direction", "?")
            new_amt = event.get("amount", 0)

            lines.append(f"\U0001f535 SAME WHALE NEW MARKET")
            lines.append(f"   {short_addr} also entered:")
            lines.append(f'   "{new_q}" \u2014 {new_dir}')
            lines.append(f"   Amount: ${new_amt:,.0f}")
            lines.append(
                f"   \U0001f50d Cross-reference with Alert #{alert_id}"
            )

        lines.append("")
        lines.append(
            f"\U0001f517 Wallet: https://polygonscan.com/address/{address}"
        )
        lines.append("\u2501" * 32)

        return "\n".join(lines)

    # ── Dedup & Logging ──────────────────────────────────────

    def _already_notified(
        self, alert_id: int, event_type: str, wallet_address: str
    ) -> bool:
        """Check if this specific event was already notified."""
        try:
            existing = self.db.get_whale_notifications(alert_id)
            for n in existing:
                if (
                    n.get("event_type") == event_type
                    and n.get("wallet_address") == wallet_address
                ):
                    return True
        except Exception:
            pass
        return False

    def _log_notification(
        self,
        alert_id: int,
        event_type: str,
        wallet_address: str,
        event: dict,
    ) -> None:
        """Record that a whale notification was sent."""
        try:
            self.db.log_whale_notification(
                alert_id=alert_id,
                event_type=event_type,
                wallet_address=wallet_address,
                details=event,
            )
        except Exception as e:
            logger.debug("Failed to log whale notification: %s", e)

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _direction_adjust(odds: float | None, direction: str) -> float | None:
        """YES -> odds, NO -> 1 - odds."""
        if odds is None:
            return None
        if direction == "NO":
            return 1.0 - odds
        return odds

    def _held_duration(self, alert: dict) -> str | None:
        """Calculate how long the position was held."""
        created = alert.get("created_at")
        if not created:
            return None
        try:
            if isinstance(created, str):
                dt = dt_parser.parse(created)
            else:
                dt = created
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - dt).days
            if days < 1:
                return "<1 day"
            return f"{days} day{'s' if days != 1 else ''}"
        except (TypeError, ValueError):
            return None
