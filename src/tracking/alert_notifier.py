"""
Alert Notifier — Sends follow-up Telegram notifications on active alerts.

Runs every 6 hours (after the tracker) to send:
  1. Closing soon: alerts whose markets resolve in <24h
  2. Odds updates: significant moves (>15%) on 3+ star alerts
  3. Resolution summaries: markets resolved in the last 6h

Spam control:
  - Max 1 notification per alert every 12h (odds updates only)
  - Max 5 odds update notifications per cycle
  - Closing soon and resolution summaries always send
"""

import logging
from datetime import datetime, timedelta, timezone

from dateutil import parser as dt_parser

logger = logging.getLogger(__name__)

MAX_ODDS_UPDATES_PER_CYCLE = 5
ODDS_CHANGE_THRESHOLD_PCT = 15.0
SPAM_COOLDOWN_HOURS = 12
CLOSING_SOON_HOURS = 24


class AlertNotifier:
    """Sends follow-up Telegram notifications about active alerts."""

    def __init__(self, db, telegram) -> None:
        self.db = db
        self.telegram = telegram

    def run(self) -> dict:
        """Execute the full notification cycle.

        Returns dict with counts of notifications sent by type.
        """
        counts = {
            "closing_soon": 0,
            "odds_updates": 0,
            "resolutions": 0,
        }

        pending = self.db.get_alerts_pending()

        # 1. Closing soon (<24h to resolution)
        closing = [a for a in pending if self._hours_to_resolution(a) is not None
                   and 0 < self._hours_to_resolution(a) < CLOSING_SOON_HOURS]
        if closing:
            try:
                self._send_closing_soon(closing)
                counts["closing_soon"] = len(closing)
            except Exception as e:
                logger.error("Failed to send closing soon: %s", e)

        # 2. Odds updates (>15% move, 3+ stars, spam-controlled)
        odds_sent = 0
        for alert in pending:
            if odds_sent >= MAX_ODDS_UPDATES_PER_CYCLE:
                break
            if (alert.get("star_level") or 0) < 3:
                continue
            pct_move = self._calc_odds_change(alert)
            if pct_move is None or abs(pct_move) <= ODDS_CHANGE_THRESHOLD_PCT:
                continue
            if self._is_recently_notified(alert.get("id")):
                continue
            try:
                self._send_odds_update(alert, pct_move)
                self.db.log_notification(alert["id"], "odds_update")
                odds_sent += 1
            except Exception as e:
                logger.error(
                    "Failed to send odds update for alert #%s: %s",
                    alert.get("id"), e,
                )
        counts["odds_updates"] = odds_sent

        # 3. Recently resolved (last 6h)
        try:
            recent_resolved = self.db.get_recently_resolved(hours=6)
            if recent_resolved:
                self._send_resolution_summary(recent_resolved)
                counts["resolutions"] = len(recent_resolved)
        except Exception as e:
            logger.error("Failed to send resolution summary: %s", e)

        logger.info(
            "Notifier done: %d closing, %d odds updates, %d resolutions",
            counts["closing_soon"], counts["odds_updates"], counts["resolutions"],
        )
        return counts

    # ── Closing Soon ─────────────────────────────────────────

    def _send_closing_soon(self, alerts: list[dict]) -> None:
        """Send a summary of alerts closing in <24h."""
        lines = [
            "\u23f0 MARKETS CLOSING SOON",
            "\u2501" * 32,
            "",
        ]

        for a in sorted(alerts, key=lambda x: self._hours_to_resolution(x) or 999):
            hours = self._hours_to_resolution(a) or 0
            icon = self._pnl_icon(a)
            question = a.get("market_question", "?")
            star = a.get("star_level") or 0
            score = a.get("score") or 0
            direction = a.get("direction") or "?"

            entry_adj = self._adjusted_entry(a)
            current_adj = self._adjusted_current(a)
            change_str = self._change_str(entry_adj, current_adj)

            pnl_str = self._estimated_pnl_str(a, entry_adj, current_adj)

            lines.append(f"{icon} {question}")
            lines.append(
                f"   \u2b50 {star}\u2605 (score {score}) | Direction: {direction}"
            )
            if entry_adj is not None and current_adj is not None:
                lines.append(
                    f"   Entry: {entry_adj:.2f} \u2192 Current: {current_adj:.2f} ({change_str})"
                )
            if pnl_str:
                lines.append(f"   \U0001f4b0 Estimated P&L: {pnl_str}")
            lines.append(f"   \u23f3 Resolves in {hours:.1f}h")
            lines.append("")

        lines.append("\u2501" * 32)
        lines.append("\U0001f534 = winning | \U0001f7e2 = losing | \u26aa = flat")

        self.telegram.send_message("\n".join(lines), parse_mode="")

    # ── Odds Update ──────────────────────────────────────────

    def _send_odds_update(self, alert: dict, pct_change: float) -> None:
        """Send individual odds movement notification."""
        alert_id = alert.get("id", "?")
        question = alert.get("market_question", "?")
        star = alert.get("star_level") or 0
        direction = alert.get("direction") or "?"
        market_id = alert.get("market_id", "")

        entry_adj = self._adjusted_entry(alert)
        current_adj = self._adjusted_current(alert)
        odds_max = alert.get("odds_max")
        odds_max_date = alert.get("odds_max_date", "")

        icon = "\U0001f4c8" if pct_change > 0 else "\U0001f4c9"

        lines = [
            f"{icon} ODDS MOVEMENT \u2014 ALERT #{alert_id}",
            "\u2501" * 32,
            f'\U0001f4cc "{question}"',
            f"\u2b50 {star}\u2605 | Direction: {direction}",
            "",
        ]

        if entry_adj is not None and current_adj is not None:
            lines.append(
                f"Entry: {entry_adj:.2f} \u2192 Now: {current_adj:.2f} ({pct_change:+.1f}%)"
            )

        if odds_max is not None:
            max_date_str = ""
            if odds_max_date:
                dt = self._parse_timestamp(odds_max_date)
                if dt:
                    max_date_str = f" on {dt.strftime('%d %b')}"
            lines.append(f"Peak: {odds_max:.2f}{max_date_str}")

        pnl_str = self._estimated_pnl_str(alert, entry_adj, current_adj)
        if pnl_str:
            lines.append(f"\n\U0001f4b0 If sold now: {pnl_str}")

        lines.append(f"\U0001f517 https://polymarket.com/event/{market_id}")
        lines.append("\u2501" * 32)

        self.telegram.send_message("\n".join(lines), parse_mode="")

    # ── Resolution Summary ───────────────────────────────────

    def _send_resolution_summary(self, resolved: list[dict]) -> None:
        """Send summary of recently resolved markets."""
        lines = [
            "\U0001f3c1 MARKETS RESOLVED",
            "\u2501" * 32,
            "",
        ]

        session_correct = 0
        session_total = 0

        for a in resolved:
            outcome = a.get("outcome", "")
            is_correct = outcome == "correct"
            icon = "\u2705" if is_correct else "\u274c"
            label = "CORRECT" if is_correct else "INCORRECT"
            question = a.get("market_question", "?")
            star = a.get("star_level") or 0
            direction = a.get("direction") or "?"

            entry_adj = self._adjusted_entry(a)
            actual_return = a.get("actual_return")
            time_held = a.get("time_to_resolution_days")

            session_total += 1
            if is_correct:
                session_correct += 1

            lines.append(f"{icon} {label} \u2014 {question}")
            lines.append(
                f"   \u2b50 {star}\u2605 | {direction}"
                + (f" @ {entry_adj:.2f}" if entry_adj is not None else "")
            )

            parts = []
            if actual_return is not None:
                parts.append(
                    f"Return: {actual_return:+.1f}%"
                )
            if time_held is not None:
                parts.append(f"Time held: {time_held} days")
            if parts:
                lines.append(f"   {' | '.join(parts)}")

            # Top whale wallet P&L for correct alerts
            if is_correct and a.get("wallets"):
                top_w = max(a["wallets"], key=lambda w: w.get("total_amount", 0))
                addr = top_w.get("address", "")
                short = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr
                w_amount = top_w.get("total_amount", 0)
                if entry_adj and entry_adj > 0 and actual_return is not None:
                    w_profit = w_amount * (actual_return / 100)
                    w_value = w_amount + w_profit
                    lines.append(
                        f"   \U0001f40b {short}: ${w_amount:,.0f} \u2192 ${w_value:,.0f} ({w_profit:+,.0f})"
                    )

            lines.append("")

        # Session stats
        if session_total > 0:
            lines.append(
                f"\U0001f4ca Session: {session_correct}/{session_total} correct "
                f"({session_correct / session_total * 100:.0f}%)"
            )

        # All-time 3+ star accuracy
        try:
            all_time = self._all_time_accuracy_3plus()
            if all_time:
                lines.append(
                    f"\U0001f4ca All-time 3+\u2605: {all_time}"
                )
        except Exception:
            pass

        lines.append("\u2501" * 32)

        self.telegram.send_message("\n".join(lines), parse_mode="")

    # ── Helpers ──────────────────────────────────────────────

    def _hours_to_resolution(self, alert: dict) -> float | None:
        """Calculate hours until the market resolves."""
        market = self.db.get_market(alert.get("market_id", ""))
        if not market:
            return None
        res_date = self._parse_timestamp(market.get("resolution_date"))
        if res_date is None:
            return None
        now = datetime.now(timezone.utc)
        delta = (res_date - now).total_seconds() / 3600
        return delta

    def _calc_odds_change(self, alert: dict) -> float | None:
        """Calculate % odds change from entry, direction-adjusted."""
        entry = alert.get("odds_at_alert")
        if entry is None:
            return None

        market = self.db.get_market(alert.get("market_id", ""))
        if not market or market.get("current_odds") is None:
            return None

        current = market["current_odds"]
        direction = (alert.get("direction") or "YES").upper()

        entry_adj = self._direction_adjust(entry, direction)
        current_adj = self._direction_adjust(current, direction)

        if entry_adj <= 0:
            return None
        return ((current_adj - entry_adj) / entry_adj) * 100

    def _is_recently_notified(self, alert_id: int | None) -> bool:
        """Check if this alert was notified within the spam cooldown."""
        if alert_id is None:
            return False
        log = self.db.get_notification_log(alert_id)
        if not log:
            return False
        last_ts = self._parse_timestamp(log.get("last_notified_at"))
        if last_ts is None:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(hours=SPAM_COOLDOWN_HOURS)
        return last_ts > cutoff

    def _adjusted_entry(self, alert: dict) -> float | None:
        """Get direction-adjusted entry odds."""
        entry = alert.get("odds_at_alert")
        if entry is None:
            return None
        direction = (alert.get("direction") or "YES").upper()
        return self._direction_adjust(entry, direction)

    def _adjusted_current(self, alert: dict) -> float | None:
        """Get direction-adjusted current odds from market data."""
        market = self.db.get_market(alert.get("market_id", ""))
        if not market or market.get("current_odds") is None:
            return None
        direction = (alert.get("direction") or "YES").upper()
        return self._direction_adjust(market["current_odds"], direction)

    @staticmethod
    def _direction_adjust(odds: float, direction: str) -> float:
        """YES -> odds, NO -> 1 - odds."""
        if direction == "NO":
            return 1.0 - odds
        return odds

    @staticmethod
    def _pnl_icon(alert: dict) -> str:
        """Return icon based on odds movement direction."""
        change = alert.get("potential_return_max")
        if change is not None and change > 0:
            return "\U0001f534"  # winning (red circle = our position is gaining)
        if change is not None and change < 0:
            return "\U0001f7e2"  # losing
        return "\u26aa"  # flat

    @staticmethod
    def _change_str(entry: float | None, current: float | None) -> str:
        """Format change percentage string."""
        if entry is None or current is None or entry <= 0:
            return "--"
        pct = ((current - entry) / entry) * 100
        return f"{pct:+.1f}%"

    def _estimated_pnl_str(
        self, alert: dict, entry_adj: float | None, current_adj: float | None,
    ) -> str:
        """Build estimated P&L string."""
        total = alert.get("total_amount") or 0
        if not total or entry_adj is None or current_adj is None or entry_adj <= 0:
            return ""
        pct = ((current_adj - entry_adj) / entry_adj) * 100
        dollar = total * (pct / 100)
        if dollar >= 0:
            return f"+${dollar:,.0f} ({pct:+.1f}%)"
        return f"-${abs(dollar):,.0f} ({pct:+.1f}%)"

    def _all_time_accuracy_3plus(self) -> str | None:
        """Calculate all-time accuracy for 3+ star resolved alerts."""
        try:
            all_alerts = self.db.client.table("alerts").select(
                "outcome,star_level"
            ).neq("outcome", "pending").gte("star_level", 3).execute()
            rows = all_alerts.data or []
            if not rows:
                return None
            correct = sum(1 for r in rows if r.get("outcome") == "correct")
            total = len(rows)
            return f"{correct}/{total} correct ({correct / total * 100:.1f}%)"
        except Exception:
            return None

    @staticmethod
    def _parse_timestamp(val) -> datetime | None:
        """Parse an ISO string or datetime into tz-aware datetime."""
        if val is None:
            return None
        try:
            if isinstance(val, str):
                val = dt_parser.parse(val)
            if isinstance(val, datetime):
                if val.tzinfo is None:
                    val = val.replace(tzinfo=timezone.utc)
                return val
        except (TypeError, ValueError):
            pass
        return None
