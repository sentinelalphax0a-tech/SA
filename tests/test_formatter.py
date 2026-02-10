"""Tests for the alert formatter."""

from src.publishing.formatter import AlertFormatter, _X_MAX_CHARS
from src.database.models import Alert


def _alert(**overrides) -> Alert:
    """Build a default Alert with sensible defaults, easy to override."""
    defaults = dict(
        market_id="m1",
        alert_type="accumulation",
        score=75,
        market_question="Will X resign?",
        direction="YES",
        star_level=3,
        total_amount=47200.0,
        odds_at_alert=0.08,
        price_impact=0.06,
        confluence_count=3,
    )
    defaults.update(overrides)
    return Alert(**defaults)


# ── format_x_alert ───────────────────────────────────────────


class TestFormatXAlert:
    def test_contains_required_fields(self):
        fmt = AlertFormatter()
        text = fmt.format_x_alert(_alert())
        assert "SMART MONEY DETECTED" in text
        assert "Sentinel Alpha" in text
        assert "Will X resign?" in text
        assert "$47,200" in text
        assert "YES positions" in text
        assert "3 coordinated wallets" in text
        assert "#Polymarket" in text

    def test_no_score_shown(self):
        """X format must NEVER reveal the score."""
        fmt = AlertFormatter()
        text = fmt.format_x_alert(_alert(score=99))
        assert "Score" not in text
        assert "99" not in text

    def test_no_filter_details(self):
        """X format must NEVER reveal filter IDs."""
        fmt = AlertFormatter()
        alert = _alert(filters_triggered=[{"id": "W01", "points": 25}])
        text = fmt.format_x_alert(alert)
        assert "W01" not in text
        assert "filter" not in text.lower()

    def test_stars_shown(self):
        fmt = AlertFormatter()
        text = fmt.format_x_alert(_alert(star_level=4))
        assert "\u2b50\u2b50\u2b50\u2b50" in text

    def test_max_280_chars(self):
        fmt = AlertFormatter()
        long_q = "Will this extremely long market question about something " * 5
        text = fmt.format_x_alert(_alert(market_question=long_q))
        assert len(text) <= _X_MAX_CHARS

    def test_no_confluence_line_when_single_wallet(self):
        fmt = AlertFormatter()
        text = fmt.format_x_alert(_alert(confluence_count=1))
        assert "coordinated" not in text
        assert "wallets" not in text

    def test_odds_no_impact(self):
        fmt = AlertFormatter()
        text = fmt.format_x_alert(_alert(price_impact=None))
        assert "0.08" in text
        assert "\u2192" not in text

    def test_odds_na(self):
        fmt = AlertFormatter()
        text = fmt.format_x_alert(_alert(odds_at_alert=None, price_impact=None))
        assert "N/A" in text

    def test_backward_compat_format_x(self):
        fmt = AlertFormatter()
        assert fmt.format_x(_alert()) == fmt.format_x_alert(_alert())


# ── format_telegram_alert ────────────────────────────────────


class TestFormatTelegramAlert:
    def test_contains_score(self):
        """Telegram format MUST include the score."""
        fmt = AlertFormatter()
        text = fmt.format_telegram_alert(_alert(score=75))
        assert "Score: 75" in text

    def test_contains_required_fields(self):
        fmt = AlertFormatter()
        text = fmt.format_telegram_alert(_alert())
        assert "SMART MONEY DETECTED" in text
        assert "Will X resign?" in text
        assert "$47,200" in text
        assert "3 coordinated wallets" in text

    def test_no_filter_details(self):
        fmt = AlertFormatter()
        alert = _alert(filters_triggered=[{"id": "B01", "points": 20}])
        text = fmt.format_telegram_alert(alert)
        assert "B01" not in text

    def test_context_exchange_funded(self):
        fmt = AlertFormatter()
        text = fmt.format_telegram_alert(_alert(confluence_type="exchange_funded"))
        assert "Exchange-funded" in text

    def test_context_distribution(self):
        fmt = AlertFormatter()
        text = fmt.format_telegram_alert(_alert(confluence_type="distribution_network"))
        assert "Distribution network" in text

    def test_context_shared_funding(self):
        fmt = AlertFormatter()
        text = fmt.format_telegram_alert(_alert(confluence_type="shared_funding"))
        assert "Shared funding" in text

    def test_context_time_span(self):
        fmt = AlertFormatter()
        wallets = [{"address": "0x1", "time_span_hours": 48}]
        text = fmt.format_telegram_alert(_alert(wallets=wallets))
        assert "Last 48h" in text

    def test_backward_compat_format_telegram(self):
        fmt = AlertFormatter()
        assert fmt.format_telegram(_alert()) == fmt.format_telegram_alert(_alert())


# ── format_whale_entry ───────────────────────────────────────


class TestFormatWhaleEntry:
    def test_whale_header(self):
        fmt = AlertFormatter()
        text = fmt.format_whale_entry(_alert(alert_type="whale_entry", total_amount=25000))
        assert "WHALE ENTRY" in text
        assert "Sentinel Alpha" in text
        assert "$25,000" in text
        assert "single tx" in text

    def test_impact_shown(self):
        fmt = AlertFormatter()
        text = fmt.format_whale_entry(
            _alert(odds_at_alert=0.12, price_impact=0.032)
        )
        assert "Impact:" in text
        assert "+3.2%" in text
        assert "0.12" in text

    def test_wallet_age_shown(self):
        fmt = AlertFormatter()
        wallets = [{"address": "0x1", "wallet_age_days": 45}]
        text = fmt.format_whale_entry(_alert(wallets=wallets))
        assert "45 days old" in text

    def test_no_age_when_missing(self):
        fmt = AlertFormatter()
        text = fmt.format_whale_entry(_alert(wallets=None))
        assert "days old" not in text

    def test_monitoring_footer(self):
        fmt = AlertFormatter()
        text = fmt.format_whale_entry(_alert())
        assert "Monitoring" in text

    def test_no_score_shown(self):
        fmt = AlertFormatter()
        text = fmt.format_whale_entry(_alert(score=90))
        assert "Score" not in text
        assert "90" not in text

    def test_backward_compat_format_telegram_whale(self):
        fmt = AlertFormatter()
        assert fmt.format_telegram_whale(_alert()) == fmt.format_whale_entry(_alert())


# ── format_x_resolution ─────────────────────────────────────


class TestFormatXResolution:
    def test_correct_resolution(self):
        fmt = AlertFormatter()
        text = fmt.format_x_resolution(
            _alert(outcome="YES", direction="YES", star_level=4)
        )
        assert "CORRECT" in text
        assert "\u2705" in text
        assert "ALERT RESOLVED" in text
        assert "Outcome: YES" in text
        assert "Alert was: YES" in text
        assert "#Polymarket" in text

    def test_incorrect_resolution(self):
        fmt = AlertFormatter()
        text = fmt.format_x_resolution(
            _alert(outcome="NO", direction="YES")
        )
        assert "INCORRECT" in text
        assert "\u274c" in text

    def test_no_score_in_x_resolution(self):
        fmt = AlertFormatter()
        text = fmt.format_x_resolution(_alert(outcome="YES", direction="YES"))
        assert "Score" not in text

    def test_stars_shown_if_present(self):
        fmt = AlertFormatter()
        text = fmt.format_x_resolution(
            _alert(outcome="YES", direction="YES", star_level=3)
        )
        assert "Was:" in text
        assert "\u2b50\u2b50\u2b50" in text

    def test_max_280_chars(self):
        fmt = AlertFormatter()
        long_q = "Will this very long question about an event happen? " * 5
        text = fmt.format_x_resolution(
            _alert(outcome="YES", direction="YES", market_question=long_q)
        )
        assert len(text) <= _X_MAX_CHARS


# ── format_telegram_resolution ───────────────────────────────


class TestFormatTelegramResolution:
    def test_correct_with_score(self):
        fmt = AlertFormatter()
        text = fmt.format_telegram_resolution(
            _alert(outcome="YES", direction="YES", score=85, star_level=4)
        )
        assert "CORRECT" in text
        assert "\u2705" in text
        assert "Score: 85" in text
        assert "Outcome: YES" in text

    def test_incorrect(self):
        fmt = AlertFormatter()
        text = fmt.format_telegram_resolution(
            _alert(outcome="NO", direction="YES")
        )
        assert "INCORRECT" in text
        assert "\u274c" in text

    def test_amount_shown(self):
        fmt = AlertFormatter()
        text = fmt.format_telegram_resolution(
            _alert(outcome="YES", direction="YES", total_amount=50000)
        )
        assert "$50,000" in text

    def test_no_filter_details(self):
        fmt = AlertFormatter()
        alert = _alert(
            outcome="YES", direction="YES",
            filters_triggered=[{"id": "W01", "points": 25}],
        )
        text = fmt.format_telegram_resolution(alert)
        assert "W01" not in text


# ── format_opposing_positions ────────────────────────────────


class TestFormatOpposingPositions:
    def test_with_positions_data(self):
        fmt = AlertFormatter()
        alert = _alert(
            opposite_positions=[
                {"direction": "YES", "amount": 30000, "wallet": "0xa"},
                {"direction": "YES", "amount": 20000, "wallet": "0xb"},
                {"direction": "NO", "amount": 45000, "wallet": "0xc"},
            ],
            odds_at_alert=0.55,
        )
        text = fmt.format_opposing_positions(alert)
        assert "OPPOSING POSITIONS DETECTED" in text
        assert "Will X resign?" in text
        assert "YES: 2 wallets ($50,000)" in text
        assert "NO: 1 wallets ($45,000)" in text
        assert "Current odds: 0.55" in text
        assert "Conflicting signals" in text

    def test_without_positions_data(self):
        fmt = AlertFormatter()
        text = fmt.format_opposing_positions(_alert(opposite_positions=None))
        assert "OPPOSING POSITIONS DETECTED" in text
        assert "both sides" in text

    def test_no_score_shown(self):
        fmt = AlertFormatter()
        text = fmt.format_opposing_positions(_alert(opposite_positions=None))
        assert "Score" not in text

    def test_caution_footer(self):
        fmt = AlertFormatter()
        text = fmt.format_opposing_positions(_alert(opposite_positions=None))
        assert "caution" in text


# ── Security: no internal data leaks ─────────────────────────


class TestNoLeaks:
    """Ensure NO format ever reveals filters, methodology, or internal IDs."""

    def _all_formats(self, alert: Alert) -> list[str]:
        fmt = AlertFormatter()
        return [
            fmt.format_x_alert(alert),
            fmt.format_telegram_alert(alert),
            fmt.format_whale_entry(alert),
            fmt.format_x_resolution(alert),
            fmt.format_telegram_resolution(alert),
            fmt.format_opposing_positions(alert),
        ]

    def test_no_filter_ids_in_any_format(self):
        alert = _alert(
            outcome="YES",
            filters_triggered=[
                {"id": "W01", "points": 25},
                {"id": "B16", "points": 20},
                {"id": "C04", "points": 40},
            ],
        )
        for text in self._all_formats(alert):
            assert "W01" not in text
            assert "B16" not in text
            assert "C04" not in text

    def test_no_multiplier_in_public(self):
        alert = _alert(outcome="YES", multiplier=1.4)
        for text in self._all_formats(alert):
            assert "multiplier" not in text.lower()
            assert "1.4" not in text
