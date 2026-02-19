"""
Sell Detector — monitors open positions for sell activity.

Notification-only system (no scoring impact):
  - Individual sell: single wallet reduces/exits position
  - Coordinated sell: 2+ wallets sell same market within 4h
  - Position gone: net position < 20% without explicit CLOB sell
    (captures CTF merges, transfers, burns — invisible to CLOB API)
  - Merge suspected: opposite-direction CLOB trades detected

Net position formula:
    buys_shares = sum(amount / price for direction buys)
    sells_shares = sum(amount / price for opposite-direction trades)
    merges_estimated = min(buys_shares, opp_buy_shares)  ← conservative proxy
    net_shares = buys_shares - sells_shares - merges_estimated

close_reason values (ML labels, not definitive diagnoses):
    'sell_clob'       — explicit CLOB sell detected
    'merge_suspected' — opposite-direction CLOB trades found
    'net_zero'        — net position ≈ 0 with sells
    'position_gone'   — position disappeared without CLOB explanation
"""

import logging
import os
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

            # Calculate hold duration: alert creation → sell detection.
            # Primary: pos["created_at"] (set when WalletPosition was inserted).
            # Fallback: fetch alert.created_at if position row pre-dates this field.
            hold_hours: float | None = None
            try:
                from dateutil import parser as dt_parser

                raw_ts = pos.get("created_at")
                if raw_ts is None and pos.get("alert_id") and self.db is not None:
                    # Lazy fallback: one DB call per sell (sells are rare)
                    alert_row = (
                        self.db.client.table("alerts")
                        .select("created_at")
                        .eq("id", pos["alert_id"])
                        .single()
                        .execute()
                    )
                    raw_ts = (alert_row.data or {}).get("created_at")
                if raw_ts is not None:
                    anchor = dt_parser.parse(raw_ts) if isinstance(raw_ts, str) else raw_ts
                    if anchor.tzinfo is None:
                        anchor = anchor.replace(tzinfo=timezone.utc)
                    hold_hours = (sell_ts - anchor).total_seconds() / 3600
            except Exception as exc:
                logger.debug("hold_duration calc failed for %s: %s", wallet_addr[:10], exc)

            sell_wallets.append({
                "address": wallet_addr,
                "sell_amount": sell_amount,
                "original_amount": pos.get("total_amount", 0),
                "direction": direction,
                "timestamp": sell_ts,
                "entry_odds": pos.get("entry_odds"),
                "alert_id": pos.get("alert_id"),
                "entry_date": pos.get("created_at"),
            })

            # Update position in DB
            try:
                self.db.update_position_sold(
                    wallet_address=wallet_addr,
                    market_id=market_id,
                    sell_amount=sell_amount,
                    sell_timestamp=sell_ts,
                    hold_duration_hours=hold_hours,
                    original_amount=pos.get("total_amount", 0.0),
                )
            except Exception as e:
                logger.debug("Failed to update position sold: %s", e)

        if not sell_wallets:
            return []

        # Get market question and current odds for notification
        market_question = None
        current_odds = None
        try:
            market_data = self.db.get_market(market_id)
            if market_data:
                market_question = market_data.get("question")
                current_odds = market_data.get("current_odds")
        except Exception:
            pass

        # Enrich sells with close_reason and update DB
        for sw in sell_wallets:
            sw.setdefault("close_reason", "sell_clob")

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
                    "current_odds": current_odds,
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
                "current_odds": current_odds,
                "wallets": [sw],
                "timestamp": sw["timestamp"],
            })

        return events

    # ── Net position check (post-scan) ────────────────────────

    def check_net_positions(
        self,
        lookback_minutes: int | None = None,
    ) -> list[dict]:
        """Check net position for all pending alerts with open positions.

        Detects exits that don't appear as explicit CLOB sells:
          - CTF merge (burn YES+NO via the CTF contract — invisible by design)
          - Transfer out
          - Token burn

        Net position formula (in shares/tokens, not dollars):
            buys_shares  = sum(amount/price) for direction trades
            sells_shares = sum(amount/price) for opposite-direction trades
            merges_estimated = min(buys_shares, opp_buy_shares)  ← conservative proxy
            net_shares = buys_shares - sells_shares - merges_estimated

        Only runs when NOT in GitHub Actions (performance guard for CI).
        Use --post-scan-check flag or deep scan to trigger.

        Returns list of sell events with close_reason set.
        """
        if os.environ.get("GITHUB_ACTIONS") == "true":
            logger.debug("Skipping check_net_positions in GitHub Actions")
            return []

        if self.db is None or self.pm is None:
            return []

        lback = lookback_minutes or (config.SELL_POST_SCAN_LOOKBACK_HOURS * 60)

        try:
            open_positions = self.db.get_open_positions()
        except Exception as e:
            logger.error("check_net_positions: failed to fetch positions: %s", e)
            return []

        if not open_positions:
            return []

        # Cap to avoid long runtimes
        open_positions = open_positions[:config.SELL_POST_SCAN_MAX_ALERTS]

        # Group by market for batch trade fetch
        by_market: dict[str, list[dict]] = defaultdict(list)
        for pos in open_positions:
            by_market[pos["market_id"]].append(pos)

        events: list[dict] = []

        for market_id, positions in by_market.items():
            try:
                trades = self.pm.get_recent_trades(
                    market_id=market_id,
                    minutes=lback,
                    min_amount=0,  # include dust for net position accuracy
                )
            except Exception as e:
                logger.debug("check_net_positions: trades fetch failed %s: %s", market_id, e)
                continue

            market_question = None
            try:
                mkt = self.db.get_market(market_id)
                if mkt:
                    market_question = mkt.get("question")
            except Exception:
                pass

            for pos in positions:
                event = self._check_net_position(pos, trades, market_id, market_question)
                if event:
                    events.append(event)

        return events

    def _check_net_position(
        self,
        pos: dict,
        trades: list[TradeEvent],
        market_id: str,
        market_question: str | None,
    ) -> dict | None:
        """Check a single position's net token balance.

        Returns a sell event dict if the position appears to have exited,
        or None if position is still substantially open.
        """
        wallet_addr = pos["wallet_address"]
        direction = pos["direction"]
        original_amount = pos.get("total_amount", 0)
        entry_odds = pos.get("entry_odds") or 0

        if entry_odds <= 0 or original_amount <= 0:
            return None

        # Wallet's trades in this market
        wallet_trades = [t for t in trades if t.wallet_address == wallet_addr]

        dir_trades = [t for t in wallet_trades if t.direction == direction]
        opp_trades = [t for t in wallet_trades if t.direction != direction]

        # Use is_market_order (= side=="BUY" from CLOB API) to separate:
        #   dir buys  = acquiring the alerted direction token (side=BUY, same direction)
        #   dir sells = explicitly closing the position (side=SELL, same direction)
        #   opp buys  = buying the opposite token = merge/hedge (side=BUY, opp direction)
        dir_buys  = [t for t in dir_trades if t.is_market_order]
        dir_sells = [t for t in dir_trades if not t.is_market_order]
        opp_buys  = [t for t in opp_trades if t.is_market_order]

        buys_shares = sum(t.amount / t.price for t in dir_buys  if t.price > 0)
        sells_shares = sum(t.amount / t.price for t in dir_sells if t.price > 0)
        opp_buy_shares = sum(t.amount / t.price for t in opp_buys  if t.price > 0)

        if buys_shares <= 0:
            return None  # no buys in window — can't assess

        # Conservative merge proxy: if wallet bought opposite tokens,
        # min(buys, opp_buys) shares are likely neutralized via CTF merge
        merges_estimated = min(buys_shares, opp_buy_shares)

        net_shares = max(0.0, buys_shares - sells_shares - merges_estimated)
        remaining_pct = (net_shares / buys_shares * 100) if buys_shares > 0 else 100.0

        # Determine close_reason
        if sells_shares > 0 and opp_buy_shares > 0:
            close_reason = "merge_suspected"   # explicit sell + hedge = CLOB arbitrage
        elif sells_shares > 0:
            close_reason = "sell_clob"         # explicit CLOB sell, no hedge
        elif opp_buy_shares > 0:
            close_reason = "merge_suspected"   # only opposite buys = merge proxy
        elif net_shares < buys_shares * 0.05:
            close_reason = "position_gone"     # disappeared without any CLOB activity
        else:
            close_reason = "sell_clob"         # fallback (should not reach here)

        now = datetime.now(timezone.utc)

        if remaining_pct < config.SELL_NET_TOTAL_THRESHOLD * 100:
            event_type = "position_gone" if close_reason == "position_gone" else "net_exit_total"
            level = "SALIDA_TOTAL"
        elif remaining_pct < config.SELL_NET_PARTIAL_THRESHOLD * 100:
            event_type = "net_exit_partial"
            level = "SALIDA_PARCIAL"
        else:
            return None  # position still substantially open

        logger.info(
            "Net position check %s %s: %.0f%% remaining — %s (%s)",
            wallet_addr[:10], market_id[:12], remaining_pct, level, close_reason,
        )

        # Update position in DB
        try:
            self.db.update_alert_fields(
                pos["alert_id"],
                {"close_reason": close_reason} if pos.get("alert_id") else {},
            )
        except Exception as e:
            logger.debug("Failed to update close_reason: %s", e)

        return {
            "type": event_type,
            "close_reason": close_reason,
            "market_id": market_id,
            "market_question": market_question,
            "wallets": [{
                "address": wallet_addr,
                "direction": direction,
                "original_amount": original_amount,
                "remaining_pct": remaining_pct,
                "entry_odds": entry_odds,
                "alert_id": pos.get("alert_id"),
            }],
            "timestamp": now,
        }

    # ── Merge resolution verification ─────────────────────────

    def check_merge_resolution(self) -> dict:
        """Verify merge_suspected alerts from the last 48h.

        For each alert with merge_suspected=True:
          - Fetch current open positions
          - If net_position < $500 equivalent → mark merge_confirmed=True

        Capped at 20 most recent merge_suspected alerts to limit overhead
        (target: < 10 seconds added to scan cycle).

        Returns dict with counts.
        """
        if self.db is None or self.pm is None:
            return {"checked": 0, "confirmed": 0}

        try:
            rows = (
                self.db.client.table("alerts")
                .select("id,market_id,direction,odds_at_alert,wallets,merge_confirmed")
                .eq("outcome", "pending")
                .eq("merge_suspected", True)
                .eq("merge_confirmed", False)
                .order("created_at", desc=True)
                .limit(20)
                .execute()
                .data
            ) or []
        except Exception as e:
            logger.warning("check_merge_resolution: query failed: %s", e)
            return {"checked": 0, "confirmed": 0}

        confirmed = 0
        for row in rows:
            try:
                alert_id = row["id"]
                market_id = row["market_id"]
                direction = row.get("direction", "YES")
                wallets = row.get("wallets") or []

                # Fetch recent trades for the market
                trades = self.pm.get_recent_trades(
                    market_id=market_id,
                    minutes=config.SELL_POST_SCAN_LOOKBACK_HOURS * 60,
                    min_amount=0,
                )

                # Check each wallet's net position in dollar terms
                total_net_usd = 0.0
                for w in wallets:
                    addr = w.get("address", "")
                    if not addr:
                        continue
                    w_trades = [t for t in trades if t.wallet_address == addr]
                    dir_trades = [t for t in w_trades if t.direction == direction]
                    opp_trades = [t for t in w_trades if t.direction != direction]
                    net_usd = sum(t.amount for t in dir_trades) - sum(t.amount for t in opp_trades)
                    total_net_usd += max(0.0, net_usd)

                if total_net_usd < 500:
                    self.db.update_alert_fields(alert_id, {"merge_confirmed": True})
                    confirmed += 1
                    logger.info(
                        "merge_confirmed=True for alert #%s (net_usd=%.0f)", alert_id, total_net_usd
                    )
            except Exception as e:
                logger.debug("check_merge_resolution: error for alert #%s: %s", row.get("id"), e)

        logger.info(
            "check_merge_resolution: checked=%d confirmed=%d", len(rows), confirmed
        )
        return {"checked": len(rows), "confirmed": confirmed}
