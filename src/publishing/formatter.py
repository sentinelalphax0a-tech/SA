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

from src import config
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

        if getattr(alert, "multi_signal", False) and getattr(alert, "secondary_count", 0) > 0:
            lines.append(
                f"\U0001f4e1 Multi-signal: {alert.secondary_count + 1} independent group(s)"
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

    # ── Telegram DETAILED (testing channel) ──────────────────

    def format_telegram_detailed(self, alert: Alert) -> str:
        """Format a detailed alert for the Telegram testing channel.

        Shows all triggered filters with points, multipliers, and top wallets.
        """
        stars = self._star_emoji(alert.star_level or 0)
        odds_str = (
            f"{self._direction_odds(alert.odds_at_alert, alert.direction):.2f}"
            if alert.odds_at_alert is not None else "N/A"
        )
        alert_num = alert.id or "?"

        lines = [
            f"\U0001f50d SENTINEL ALPHA \u2014 ALERT #{alert_num}",
            "\u2500" * 26,
            "",
            f'\U0001f4cc Market: "{alert.market_question}"',
            f"\U0001f4ca Direction: {alert.direction} | Odds: {odds_str}",
            f"\u2b50 Rating: {alert.star_level} stars | Score: {alert.score}",
            "",
        ]

        # Filters triggered
        if alert.filters_triggered:
            lines.append("\U0001f9e9 FILTERS TRIGGERED:")
            for f in alert.filters_triggered:
                fid = f.get("filter_id", "?")
                pts = f.get("points", 0)
                name = f.get("filter_name", "")
                details = f.get("details", "")
                detail_str = f" \u2014 {details}" if details else ""
                lines.append(f"  {fid}: {pts:+d} pts ({name}){detail_str}")
            lines.append("")

        # Multipliers
        lines.append("\U0001f4b0 MULTIPLIERS:")
        lines.append(f"  Amount: x{alert.multiplier:.2f} (${self._fmt_amount(alert.total_amount)} total)")
        # Extract sniper/shotgun info from scoring
        if alert.score_raw and alert.score_raw > 0 and alert.score:
            effective_mult = alert.score / alert.score_raw if alert.score_raw else 0
            lines.append(f"  Effective: x{effective_mult:.2f} (raw={alert.score_raw} \u2192 final={alert.score})")
        lines.append("")

        # Price impact (alert-level)
        if alert.odds_at_alert is not None and alert.price_impact is not None:
            old = self._direction_odds(alert.odds_at_alert, alert.direction)
            new = self._direction_odds(
                alert.odds_at_alert + alert.price_impact, alert.direction
            )
            pct = ((new - old) / old * 100) if old > 0 else 0
            lines.append(f"\U0001f4c8 Price Impact: {old:.2f} \u2192 {new:.2f} ({pct:+.1f}%)")
            lines.append("")

        # Top wallets (detailed)
        if alert.wallets:
            sorted_wallets = sorted(
                alert.wallets, key=lambda w: w.get("total_amount", 0), reverse=True
            )
            lines.append("\U0001f517 TOP WALLETS:")
            for w in sorted_wallets[:3]:
                lines.extend(self._format_wallet_detail(w, alert))
            lines.append("")

        # Market link
        slug = ""
        if alert.market_id:
            slug = alert.market_id
        lines.append(f"\U0001f517 https://polymarket.com/event/{slug}")
        lines.append("\u2500" * 26)

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
            old = self._direction_odds(alert.odds_at_alert, alert.direction)
            new = self._direction_odds(
                alert.odds_at_alert + alert.price_impact, alert.direction
            )
            pct = (new - old) * 100
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

    # ── Sell notifications ──────────────────────────────────

    def format_sell_notification(self, sell_event: dict) -> str:
        """Format an individual sell notification for Telegram with P&L."""
        wallet = sell_event["wallets"][0]
        addr = wallet.get("address", "?")
        short_addr = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr
        question = sell_event.get("market_question") or sell_event.get("market_id", "?")
        market_id = sell_event.get("market_id", "")
        direction = wallet.get("direction", "YES")

        lines = [
            "\U0001f534 SELL DETECTED \u2014 Sentinel Alpha",
            "\u2500" * 26,
            "",
            f'\U0001f4ca "{question}"',
            f"\U0001f4c8 Direction: {direction}",
        ]

        # Entry details
        entry_odds = wallet.get("entry_odds")
        original_amount = wallet.get("original_amount", 0)
        if entry_odds is not None:
            adj_entry = self._direction_odds(entry_odds, direction)
            lines.append(
                f"\U0001f4b0 Entry: ${self._fmt_amount(original_amount)} "
                f"@ {adj_entry:.2f} odds"
            )
        else:
            lines.append(f"\U0001f4b0 Entry: ${self._fmt_amount(original_amount)}")

        lines.append(f"\U0001f4b8 Sold: ${self._fmt_amount(wallet.get('sell_amount'))}")

        # Held duration
        held_str = self._held_duration(wallet.get("entry_date"))
        if held_str:
            lines.append(f"\U0001f4c5 Held: {held_str}")

        # P&L estimate
        pnl = self._calc_pnl(
            original_amount, entry_odds,
            sell_event.get("current_odds"), direction,
        )
        if pnl:
            lines.append("")
            lines.append("\U0001f4ca P&L Estimate:")
            lines.append(
                f"  Shares: {pnl['shares']:,.0f} "
                f"(${self._fmt_amount(original_amount)} / {pnl['entry_odds_adj']:.2f})"
            )
            lines.append(
                f"  Value: ${pnl['current_value']:,.0f} "
                f"(shares \u00d7 {pnl['current_odds_adj']:.2f})"
            )
            if pnl["pnl"] >= 0:
                lines.append(
                    f"  P&L: +${pnl['pnl']:,.0f} (+{pnl['pnl_pct']:.1f}%)"
                )
            else:
                lines.append(
                    f"  P&L: -${abs(pnl['pnl']):,.0f} ({pnl['pnl_pct']:.1f}%)"
                )

        lines.extend([
            "",
            f"\U0001f45b {short_addr}",
            f"   https://polygonscan.com/address/{addr}",
            "",
            f"\U0001f517 https://polymarket.com/event/{market_id}",
            "\u2500" * 26,
        ])

        return "\n".join(lines)

    def format_coordinated_sell(self, sell_event: dict) -> str:
        """Format a coordinated sell notification for Telegram."""
        wallets = sell_event.get("wallets", [])
        question = sell_event.get("market_question") or sell_event.get("market_id", "?")
        market_id = sell_event.get("market_id", "")
        total_sold = sum(w.get("sell_amount", 0) for w in wallets)
        direction = wallets[0].get("direction", "YES") if wallets else "YES"

        lines = [
            "\U0001f6a8 COORDINATED SELL \u2014 Sentinel Alpha",
            "\u2500" * 26,
            "",
            f'\U0001f4ca "{question}"',
            f"\U0001f4c8 Direction: {direction}",
            f"\U0001f45b {len(wallets)} wallets selling within {config.SELL_COORDINATED_WINDOW_HOURS}h",
            f"\U0001f4b8 Total sold: ${self._fmt_amount(total_sold)}",
            "",
        ]

        for w in wallets[:5]:
            addr = w.get("address", "?")
            short_addr = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr
            entry_odds = w.get("entry_odds")
            entry_str = ""
            if entry_odds is not None:
                adj_entry = self._direction_odds(entry_odds, w.get("direction"))
                entry_str = f" @ {adj_entry:.2f}"
            lines.append(
                f"  {short_addr}: sold ${self._fmt_amount(w.get('sell_amount'))} "
                f"of ${self._fmt_amount(w.get('original_amount'))}{entry_str}"
            )
            lines.append(f"  https://polygonscan.com/address/{addr}")

        lines.extend([
            "",
            f"\U0001f517 https://polymarket.com/event/{market_id}",
            "\u2500" * 26,
            "\u26a0\ufe0f Coordinated exit \u2014 exercise caution.",
        ])

        return "\n".join(lines)

    # ── Sell Watch (metadata-only, no star changes) ─────────

    def format_sell_watch(
        self, alert: dict, sell_event, held_str: str | None
    ) -> str:
        """Format a sell watch notification for Telegram.

        This is informational only — stars are never modified.
        """
        alert_id = alert.get("id", "?")
        star = alert.get("star_level") or 0
        question = alert.get("market_question", "?")
        direction = alert.get("direction") or "?"

        event_type = sell_event.event_type
        sell_amount = sell_event.sell_amount
        sell_pct = sell_event.sell_pct
        sell_price = sell_event.sell_price
        entry_price = sell_event.original_entry_price
        pnl_pct = sell_event.pnl_pct

        star_emoji = self._star_emoji(star)

        if event_type == "FULL_EXIT":
            event_label = "\U0001f534 FULL EXIT"
        else:
            event_label = "\U0001f7e1 PARTIAL EXIT"

        lines = [
            "\U0001f4ca SELL WATCH \u2014 Sentinel Alpha",
            "\u2501" * 32,
            f"\U0001f4cc Alert #{alert_id} ({star}\u2605)",
            f'\U0001f4ca "{question}"',
            f"\U0001f4c8 Direction: {direction}",
            "",
            f"{event_label}: ${sell_amount:,.0f} ({sell_pct * 100:.0f}% of position)",
        ]

        if sell_price is not None:
            lines.append(f"   Sell price: {sell_price:.2f}")

        if entry_price is not None:
            total_amount = alert.get("total_amount", sell_amount)
            lines.append(f"   Entry: ${total_amount:,.0f} @ {entry_price:.2f}")

        if pnl_pct is not None:
            if pnl_pct >= 0:
                lines.append(f"   P&L: +{pnl_pct:.1f}%")
            else:
                lines.append(f"   P&L: {pnl_pct:.1f}%")

        if held_str:
            lines.append(f"   Held: {held_str}")

        lines.append("")
        lines.append("\u2139\ufe0f Score preserved \u2014 informational update only.")
        lines.append("\u2501" * 32)

        return "\n".join(lines)

    # ── Alert Update (consolidation) ─────────────────────────

    def format_alert_update(
        self,
        original_alert: dict,
        new_wallets: list[dict],
        new_amount: float,
        update_count: int,
    ) -> str:
        """Format a consolidation update for Telegram."""
        alert_id = original_alert.get("id", "?")
        star = original_alert.get("star_level") or 0
        question = original_alert.get("market_question", "?")
        direction = original_alert.get("direction", "?")
        existing_wallets = original_alert.get("wallets") or []
        total_wallets = len(existing_wallets) + len(new_wallets)
        total_amount = (original_alert.get("total_amount") or 0) + new_amount
        n_new = len(new_wallets)
        wallet_word = "wallet" if n_new == 1 else "wallets"

        lines = [
            f"\U0001f504 UPDATE \u2014 Alert #{alert_id} ({star}\u2605)",
            "\u2501" * 30,
            f'\U0001f4cc "{question}" \u2192 {direction}',
            "",
            "\U0001f195 New activity:",
            f"  +{n_new} new {wallet_word} (total: {total_wallets} \u2192 {direction})",
            f"  +${new_amount:,.0f} added (total: ${total_amount:,.0f})",
            f"  Update #{update_count}",
            "",
        ]

        # Top new wallets (max 3)
        sorted_new = sorted(
            new_wallets, key=lambda w: w.get("total_amount", 0), reverse=True
        )
        lines.append("\U0001f517 Top new wallets:")
        for w in sorted_new[:3]:
            addr = w.get("address", "")
            short = (
                f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr
            )
            amt = w.get("total_amount", 0)
            odds = w.get("avg_entry_price")
            odds_str = f" @ {odds:.2f}" if odds is not None else ""
            w_dir = (w.get("direction") or direction).upper()
            is_opp = w_dir != direction.upper()
            if is_opp:
                lines.append(
                    f"  \u26a0\ufe0f {short} [{w_dir} \u2195] \u2014 ${amt:,.0f}{odds_str} "
                    f"(direcci\u00f3n opuesta)"
                )
            else:
                lines.append(f"  \U0001f4bc {short} [{w_dir}] \u2014 ${amt:,.0f}{odds_str}")
        lines.append("")

        lines.append("\U0001f4c8 Signal strength: Increasing \u2191")
        lines.append("\u2501" * 30)

        return "\n".join(lines)

    # ── Merge notification ───────────────────────────────────

    def format_merge_notification(self, alert: Alert, merge_detail: str | None = None) -> str:
        """Format a merge detection notification for Telegram.

        Sent independently of the main alert when merge_suspected=True
        and score >= MERGE_MIN_SCORE_NOTIFY. Uses 🔄 emoji.

        merge_detail: the N12 filter detail string (shares, dollars, window).
        """
        alert_id = alert.id or "?"
        question = alert.market_question or "?"
        market_id = alert.market_id or ""
        direction = alert.direction or "?"

        lines = [
            "\U0001f504 MERGE DETECTADO \u2014 Sentinel Alpha",
            "\u2500" * 26,
            "",
            f'\U0001f4ca "{question}"',
            f"\U0001f4c8 Direcci\u00f3n alerta: {direction}",
            f"\U0001f194 Alert #{alert_id} (score: {alert.score})",
            "",
            "\u26a0\ufe0f Wallet compr\u00f3 YES y NO del mismo mercado (CLOB arbitrage).",
            "   Posici\u00f3n neta pr\u00f3xima a cero \u2014 se\u00f1al de cautela, no confirmaci\u00f3n.",
        ]

        if merge_detail:
            lines.append("")
            lines.append(f"\U0001f9ee Detalle (shares/tokens, no d\u00f3lares):")
            lines.append(f"   {merge_detail}")

        lines.extend([
            "",
            "\U0001f517 https://polymarket.com/event/" + market_id,
            "\u2500" * 26,
            "\u2139\ufe0f merge_suspected marcado en DB. Score reducido -40pts (N12).",
        ])

        return "\n".join(lines)

    # ── Position gone notification ────────────────────────────

    def format_position_gone(self, sell_event: dict) -> str:
        """Format a 'position gone' notification for Telegram.

        Sent when net token position < 20% of original without explicit
        CLOB sells detected. Likely cause: CTF merge, transfer, or burn.
        These are invisible to the Data API by design.
        """
        question = sell_event.get("market_question") or sell_event.get("market_id", "?")
        market_id = sell_event.get("market_id", "")
        wallets = sell_event.get("wallets", [])
        direction = wallets[0].get("direction", "?") if wallets else "?"

        lines = [
            "\U0001f504 POSICI\u00d3N DESAPARECIDA \u2014 Sentinel Alpha",
            "\u2500" * 26,
            "",
            f'\U0001f4ca "{question}"',
            f"\U0001f4c8 Direcci\u00f3n: {direction}",
        ]

        for w in wallets[:3]:
            addr = w.get("address", "?")
            short = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr
            orig = w.get("original_amount", 0)
            rem = w.get("remaining_pct", 0)
            lines.append(f"   \U0001f45b {short}: ${orig:,.0f} original \u2192 ~{rem:.0f}% restante")

        close_reason = sell_event.get("close_reason", "position_gone")
        if close_reason == "merge_suspected":
            lines.extend([
                "",
                "\U0001f504 Causa probable: merge CLOB (compr\u00f3 direcci\u00f3n opuesta)",
            ])
        else:
            lines.extend([
                "",
                "\U0001f504 Sin ventas CLOB detectadas.",
                "   Posible: merge CTF (burn YES+NO), transfer o burn fuera del CLOB.",
                "   Nota: merges CTF son invisibles a la API por dise\u00f1o.",
            ])

        lines.extend([
            "",
            f"\U0001f517 https://polymarket.com/event/{market_id}",
            "\u2500" * 26,
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

    def _direction_odds(self, odds: float, direction: str | None) -> float:
        """Adjust odds for direction: YES → odds as-is, NO → 1 - odds."""
        if direction and direction.upper() == "NO":
            return 1.0 - odds
        return odds

    def _star_emoji(self, level: int) -> str:
        return "\u2b50" * level if level > 0 else "No stars"

    def _odds_change_str(self, alert: Alert) -> str:
        if alert.odds_at_alert is not None and alert.price_impact is not None:
            old = self._direction_odds(alert.odds_at_alert, alert.direction)
            new = self._direction_odds(
                alert.odds_at_alert + alert.price_impact, alert.direction
            )
            pct = ((new - old) / old * 100) if old > 0 else 0
            return f"{old:.2f} \u2192 {new:.2f} ({pct:+.0f}%)"
        if alert.odds_at_alert is not None:
            return f"{self._direction_odds(alert.odds_at_alert, alert.direction):.2f}"
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

    def _format_wallet_detail(self, w: dict, alert: Alert) -> list[str]:
        """Format a single wallet with trade details for detailed Telegram."""
        lines: list[str] = []
        addr = w.get("address", "?")
        short_addr = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr
        direction = w.get("direction") or alert.direction or "YES"
        total = w.get("total_amount", 0)
        num_trades = w.get("trade_count", 0)
        span = w.get("time_span_hours", 0)

        # Direction indicator: mark opposite-direction wallets
        alert_dir = (alert.direction or "YES").upper()
        wallet_dir = direction.upper()
        is_opposite = wallet_dir != alert_dir

        if is_opposite:
            lines.append(
                f"  \u26a0\ufe0f {short_addr} [{wallet_dir} \u2195] \u2014 "
                f"${total:,.0f} (direcci\u00f3n opuesta)"
            )
        else:
            lines.append(f"  \U0001f4bc {short_addr} [{wallet_dir}] \u2014 ${total:,.0f}")

        # Trade summary
        if span > 0:
            lines.append(f"     Trades: {num_trades} transactions in {span}h")
        else:
            lines.append(f"     Trades: {num_trades} transaction{'s' if num_trades != 1 else ''}")

        # Individual trades (max 5)
        # CLOB prices are the token price in the bought direction, no conversion needed
        trades = w.get("trades") or []
        for i, t in enumerate(trades[:5]):
            amt = t.get("amount", 0)
            price = t.get("price", 0)
            ts_str = self._format_trade_time(t.get("timestamp"))
            lines.append(
                f"     #{i+1}: ${amt:,.0f} @ {price:.2f} odds ({ts_str})"
            )
        if len(trades) > 5:
            lines.append(f"     ... and {len(trades) - 5} more trades")

        # Total + avg entry (CLOB price of the token bought, no conversion)
        avg_price = w.get("avg_entry_price")
        if avg_price is not None and avg_price > 0:
            lines.append(f"     Total: ${total:,.0f} | Avg entry: {avg_price:.2f}")
        else:
            lines.append(f"     Total: ${total:,.0f}")

        # Pattern detection from alert filters
        pattern = self._detect_trade_pattern(alert)
        if pattern:
            lines.append(f"     Pattern: {pattern}")

        return lines

    @staticmethod
    def _format_trade_time(ts) -> str:
        """Format a trade timestamp as HH:MM UTC."""
        if ts is None:
            return "?"
        try:
            if isinstance(ts, str):
                from dateutil import parser as dt_parser
                ts = dt_parser.parse(ts)
            return f"{ts.hour:02d}:{ts.minute:02d} UTC"
        except Exception:
            return "?"

    @staticmethod
    def _detect_trade_pattern(alert: Alert) -> str | None:
        """Detect trading pattern from triggered filters."""
        if not alert.filters_triggered:
            return None
        fids = {f.get("filter_id") for f in alert.filters_triggered}
        if fids & {"B19a", "B19b", "B19c"}:
            return "Single massive entry"
        if "B06" in fids:
            return "Escalating buys"
        if "B16" in fids:
            return "Rapid accumulation"
        if "B01" in fids:
            return "Drip accumulation"
        return None

    def _extract_wallet_age(self, alert: Alert) -> int | None:
        """Extract wallet age from the first wallet in the alert."""
        if not alert.wallets:
            return None
        first = alert.wallets[0]
        age = first.get("wallet_age_days")
        if age is not None:
            return int(age)
        return None

    def _held_duration(self, entry_date) -> str | None:
        """Calculate human-readable held duration from entry_date to now."""
        if entry_date is None:
            return None
        try:
            from datetime import datetime, timezone
            if isinstance(entry_date, str):
                from dateutil import parser as dt_parser
                entry_date = dt_parser.parse(entry_date)
            if entry_date.tzinfo is None:
                entry_date = entry_date.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - entry_date).days
            if days < 1:
                return "<1 day"
            return f"{days} day{'s' if days != 1 else ''}"
        except Exception:
            return None

    def _calc_pnl(
        self,
        original_amount: float | None,
        entry_odds: float | None,
        current_odds: float | None,
        direction: str | None,
    ) -> dict | None:
        """Calculate P&L estimate for a sell notification.

        Returns dict with shares, current_value, pnl, pnl_pct,
        entry_odds_adj, current_odds_adj. Returns None if data insufficient.
        """
        if not original_amount or not entry_odds or not current_odds:
            return None

        adj_entry = self._direction_odds(entry_odds, direction)
        adj_current = self._direction_odds(current_odds, direction)

        if adj_entry <= 0:
            return None

        shares = original_amount / adj_entry
        current_value = shares * adj_current
        pnl = current_value - original_amount
        pnl_pct = (pnl / original_amount * 100) if original_amount > 0 else 0

        return {
            "shares": shares,
            "current_value": current_value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "entry_odds_adj": adj_entry,
            "current_odds_adj": adj_current,
        }

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
