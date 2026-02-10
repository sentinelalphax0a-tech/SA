"""
Alert Formatter — Builds publication text for X and Telegram.

Formats differ per platform:
  - X: No score shown, no filter details, max 280 chars.
  - Telegram: Includes score, more detail.
  - Telegram whale: Special whale entry format.
"""

import logging

from src.database.models import Alert

logger = logging.getLogger(__name__)


class AlertFormatter:
    """Formats alert data into publication-ready text."""

    def format_x(self, alert: Alert) -> str:
        """Format an alert for X/Twitter (star_level >= 3)."""
        stars = self._star_emoji(alert.star_level or 0)
        odds_change = self._odds_change_str(alert)

        lines = [
            "\U0001f50d SMART MONEY DETECTED \u2014 Sentinel Alpha",
            "",
            f'\U0001f4ca "{alert.market_question}"',
            f"\U0001f4c8 Odds: {odds_change}",
            f"\U0001f4b0 ${alert.total_amount:,.0f} in {alert.direction} positions",
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

        return "\n".join(lines)

    def format_telegram(self, alert: Alert) -> str:
        """Format an alert for Telegram (star_level >= 2)."""
        stars = self._star_emoji(alert.star_level or 0)
        odds_change = self._odds_change_str(alert)

        lines = [
            "\U0001f50d SMART MONEY DETECTED",
            "",
            f'\U0001f4ca "{alert.market_question}"',
            f"\U0001f4c8 Odds: {odds_change}",
            f"\U0001f4b0 ${alert.total_amount:,.0f} in {alert.direction}",
        ]

        if alert.confluence_count and alert.confluence_count > 1:
            lines.append(
                f"\U0001f45b {alert.confluence_count} coordinated wallets"
            )

        lines.extend([
            f"\u26a1 {stars} (Score: {alert.score})",
            "\u26a0\ufe0f DYOR",
        ])

        return "\n".join(lines)

    def format_telegram_whale(self, alert: Alert) -> str:
        """Format a whale entry alert for Telegram (B19, always published)."""
        lines = [
            "\U0001f40b WHALE ENTRY \u2014 Sentinel Alpha",
            "",
            f'\U0001f4ca "{alert.market_question}"',
            f"\U0001f4b0 ${alert.total_amount:,.0f} {alert.direction} (single tx)",
        ]

        if alert.price_impact is not None:
            lines.append(
                f"\U0001f4c8 Impact: {alert.price_impact:+.1%}"
            )

        lines.extend([
            "\u2139\ufe0f Monitoring.",
        ])

        return "\n".join(lines)

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
