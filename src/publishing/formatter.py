"""
Alert Formatter — Builds publication text for X and Telegram.

Formats differ per platform:
  - X: No score shown, no filter details, max 280 chars.
  - Telegram: Includes score, more detail.
  - Telegram whale: Special whale entry format.
  - Resolution: Outcome follow-up for both platforms.
  - Opposing positions: Heads-up when both sides active.

NEVER expose filters, scoring methodology, or internal logic in public text.
Only show: market, odds, amount, wallets, stars.
"""

import logging

from src.database.models import Alert

logger = logging.getLogger(__name__)

# ── Max lengths ──────────────────────────────────────────────
_X_MAX_CHARS = 280


class AlertFormatter:
    """Formats alert data into publication-ready text."""

    # ── X (3+ stars) ─────────────────────────────────────────

    def format_x_alert(self, alert: Alert) -> str:
        """Format an alert for X/Twitter (star_level >= 3).

        Max 280 chars.  Never reveals filters or score.
        """
        stars = self._star_emoji(alert.star_level or 0)
        odds_change = self._odds_change_str(alert)

        lines = [
            "\U0001f50d SMART MONEY DETECTED \u2014 Sentinel Alpha",
            "",
            f'\U0001f4ca "{self._truncate_question(alert.market_question, 60)}"',
            f"\U0001f4c8 Odds: {odds_change}",
            f"\U0001f4b0 ${self._fmt_amount(alert.total_amount)} in {alert.direction} positions",
        ]

        if alert.confluence_count and alert.confluence_count > 1:
            lines.append(
                f"\U0001f45b {alert.confluence_count} coordinated wallets"
            )

        lines.extend([
            f"\u26a1 {stars}",
            "",
            "\u26a0\ufe0f Not financial advice. DYOR.",
            "#Polymarket #SmartMoney",
        ])

        text = "\n".join(lines)

        # Trim to 280 chars — cut from question if needed
        if len(text) > _X_MAX_CHARS:
            text = self._trim_to_limit(alert, stars, odds_change)

        return text

    # ── Telegram (2+ stars) ──────────────────────────────────

    def format_telegram_alert(self, alert: Alert) -> str:
        """Format an alert for Telegram (star_level >= 2).

        Includes score.  More detail than X.
        """
        stars = self._star_emoji(alert.star_level or 0)
        odds_change = self._odds_change_str(alert)

        lines = [
            "\U0001f50d SMART MONEY DETECTED",
            "",
            f'\U0001f4ca "{alert.market_question}"',
            f"\U0001f4c8 Odds: {odds_change}",
            f"\U0001f4b0 ${self._fmt_amount(alert.total_amount)} in {alert.direction}",
        ]

        if alert.confluence_count and alert.confluence_count > 1:
            lines.append(
                f"\U0001f45b {alert.confluence_count} coordinated wallets"
            )

        # Extra context line: time window + funding source
        context_parts = self._build_context_parts(alert)
        if context_parts:
            lines.append(" | ".join(context_parts))

        lines.extend([
            f"\u26a1 {stars} (Score: {alert.score})",
            "\u26a0\ufe0f DYOR",
        ])

        return "\n".join(lines)

    # ── Whale entry (B19, always Telegram) ───────────────────

    def format_whale_entry(self, alert: Alert) -> str:
        """Format a whale entry alert for Telegram (B19, always published)."""
        lines = [
            "\U0001f40b WHALE ENTRY \u2014 Sentinel Alpha",
            "",
            f'\U0001f4ca "{alert.market_question}"',
            f"\U0001f4b0 ${self._fmt_amount(alert.total_amount)} {alert.direction} (single tx)",
        ]

        if alert.odds_at_alert is not None and alert.price_impact is not None:
            old = alert.odds_at_alert
            new = old + alert.price_impact
            pct = alert.price_impact * 100
            lines.append(
                f"\U0001f4c8 Impact: {pct:+.1f}% ({old:.2f} \u2192 {new:.2f})"
            )

        # Wallet age if available
        wallet_age = self._extract_wallet_age(alert)
        if wallet_age is not None:
            lines.append(f"\U0001f45b {wallet_age} days old")

        lines.append("\u2139\ufe0f Monitoring.")

        return "\n".join(lines)

    # ── Resolution follow-up (X) ─────────────────────────────

    def format_x_resolution(self, alert: Alert) -> str:
        """Format a resolution follow-up for X/Twitter.

        Shows whether the alert's prediction was correct.
        Max 280 chars.
        """
        outcome = alert.outcome or "unknown"
        is_correct = self._is_prediction_correct(alert)
        emoji = "\u2705" if is_correct else "\u274c"
        label = "CORRECT" if is_correct else "INCORRECT"

        lines = [
            f"{emoji} ALERT RESOLVED: {label} \u2014 Sentinel Alpha",
            "",
            f'\U0001f4ca "{self._truncate_question(alert.market_question, 60)}"',
            f"\U0001f3af Outcome: {outcome.upper()}",
            f"\U0001f4b0 Alert was: {alert.direction}",
        ]

        if alert.star_level:
            lines.append(f"\u26a1 Was: {self._star_emoji(alert.star_level)}")

        lines.extend([
            "",
            "#Polymarket #SmartMoney",
        ])

        text = "\n".join(lines)
        if len(text) > _X_MAX_CHARS:
            text = text[:_X_MAX_CHARS - 1] + "\u2026"
        return text

    # ── Resolution follow-up (Telegram) ──────────────────────

    def format_telegram_resolution(self, alert: Alert) -> str:
        """Format a resolution follow-up for Telegram.

        More detail than X, includes score.
        """
        outcome = alert.outcome or "unknown"
        is_correct = self._is_prediction_correct(alert)
        emoji = "\u2705" if is_correct else "\u274c"
        label = "CORRECT" if is_correct else "INCORRECT"

        lines = [
            f"{emoji} ALERT RESOLVED: {label}",
            "",
            f'\U0001f4ca "{alert.market_question}"',
            f"\U0001f3af Outcome: {outcome.upper()}",
            f"\U0001f4b0 Alert was: ${self._fmt_amount(alert.total_amount)} in {alert.direction}",
        ]

        if alert.star_level:
            lines.append(f"\u26a1 Was: {self._star_emoji(alert.star_level)} (Score: {alert.score})")

        lines.append("\u26a0\ufe0f DYOR")

        return "\n".join(lines)

    # ── Opposing positions ───────────────────────────────────

    def format_opposing_positions(self, alert: Alert) -> str:
        """Format a notification when opposing smart money positions are detected.

        Published to Telegram only — informational, not an alpha signal.
        """
        lines = [
            "\u2694\ufe0f OPPOSING POSITIONS DETECTED",
            "",
            f'\U0001f4ca "{alert.market_question}"',
        ]

        if alert.opposite_positions:
            yes_count = 0
            no_count = 0
            yes_amount = 0.0
            no_amount = 0.0

            for pos in alert.opposite_positions:
                if pos.get("direction") == "YES":
                    yes_count += 1
                    yes_amount += pos.get("amount", 0)
                else:
                    no_count += 1
                    no_amount += pos.get("amount", 0)

            lines.append(
                f"\U0001f7e2 YES: {yes_count} wallets (${self._fmt_amount(yes_amount)})"
            )
            lines.append(
                f"\U0001f534 NO: {no_count} wallets (${self._fmt_amount(no_amount)})"
            )
        else:
            lines.append(
                f"\U0001f4b0 Smart money on both sides of this market"
            )

        if alert.odds_at_alert is not None:
            lines.append(f"\U0001f4c8 Current odds: {alert.odds_at_alert:.2f}")

        lines.extend([
            "",
            "\u26a0\ufe0f Conflicting signals \u2014 exercise caution.",
        ])

        return "\n".join(lines)

    # ── Backward-compatible aliases ──────────────────────────

    def format_x(self, alert: Alert) -> str:
        return self.format_x_alert(alert)

    def format_telegram(self, alert: Alert) -> str:
        return self.format_telegram_alert(alert)

    def format_telegram_whale(self, alert: Alert) -> str:
        return self.format_whale_entry(alert)

    # ── Private helpers ──────────────────────────────────────

    def _star_emoji(self, level: int) -> str:
        return "\u2b50" * level if level > 0 else "No stars"

    def _odds_change_str(self, alert: Alert) -> str:
        if alert.odds_at_alert is not None and alert.price_impact is not None:
            old = alert.odds_at_alert
            new = old + alert.price_impact
            pct = (alert.price_impact / old * 100) if old > 0 else 0
            return f"{old:.2f} \u2192 {new:.2f} ({pct:+.0f}%)"
        if alert.odds_at_alert is not None:
            return f"{alert.odds_at_alert:.2f}"
        return "N/A"

    def _fmt_amount(self, amount: float | None) -> str:
        if amount is None:
            return "0"
        return f"{amount:,.0f}"

    def _truncate_question(self, question: str | None, max_len: int) -> str:
        if not question:
            return "?"
        if len(question) <= max_len:
            return question
        return question[: max_len - 1] + "\u2026"

    def _build_context_parts(self, alert: Alert) -> list[str]:
        """Build extra context chips for Telegram (time window, funding)."""
        parts: list[str] = []

        # Time context from wallets data
        if alert.wallets and len(alert.wallets) > 0:
            first_wallet = alert.wallets[0]
            if first_wallet.get("time_span_hours"):
                hours = first_wallet["time_span_hours"]
                parts.append(f"\U0001f4c5 Last {hours}h")

        # Funding source
        if alert.confluence_type:
            ct = alert.confluence_type
            if "exchange" in ct.lower():
                parts.append("\U0001f3e6 Exchange-funded")
            elif "distribution" in ct.lower():
                parts.append("\U0001f3e6 Distribution network")
            elif "shared" in ct.lower() or "funding" in ct.lower():
                parts.append("\U0001f3e6 Shared funding")

        return parts

    def _extract_wallet_age(self, alert: Alert) -> int | None:
        """Extract wallet age from the first wallet in the alert."""
        if not alert.wallets:
            return None
        first = alert.wallets[0]
        age = first.get("wallet_age_days")
        if age is not None:
            return int(age)
        return None

    def _is_prediction_correct(self, alert: Alert) -> bool:
        """Check if the alert's direction matches the market outcome."""
        outcome = (alert.outcome or "").upper()
        direction = (alert.direction or "").upper()

        if outcome in ("YES", "NO"):
            return direction == outcome

        # "correct"/"incorrect" set by resolution checker
        return outcome == "CORRECT"

    def _trim_to_limit(self, alert: Alert, stars: str, odds_change: str) -> str:
        """Build a shorter X post if the full version exceeds 280 chars."""
        # Shorten question progressively
        for max_q in (45, 30, 20):
            lines = [
                "\U0001f50d SMART MONEY DETECTED \u2014 Sentinel Alpha",
                "",
                f'\U0001f4ca "{self._truncate_question(alert.market_question, max_q)}"',
                f"\U0001f4c8 Odds: {odds_change}",
                f"\U0001f4b0 ${self._fmt_amount(alert.total_amount)} in {alert.direction}",
            ]

            if alert.confluence_count and alert.confluence_count > 1:
                lines.append(
                    f"\U0001f45b {alert.confluence_count} wallets"
                )

            lines.extend([
                f"\u26a1 {stars}",
                "",
                "\u26a0\ufe0f DYOR",
                "#Polymarket #SmartMoney",
            ])

            text = "\n".join(lines)
            if len(text) <= _X_MAX_CHARS:
                return text

        # Last resort: hard truncate
        return text[:_X_MAX_CHARS - 1] + "\u2026"
