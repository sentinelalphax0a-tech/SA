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


# ── format_telegram_detailed (trade details) ─────────────────


def _wallet_with_trades(**overrides) -> dict:
    """Build a wallet dict with trade details for testing."""
    base = {
        "address": "0xABCDEF1234567890ABCDEF1234567890ABCDEF12",
        "direction": "YES",
        "total_amount": 15000,
        "trade_count": 3,
        "time_span_hours": 2.5,
        "distinct_markets": 1,
        "trades": [
            {"amount": 5000, "price": 0.35, "timestamp": "2025-06-01T10:00:00+00:00"},
            {"amount": 6000, "price": 0.37, "timestamp": "2025-06-01T11:00:00+00:00"},
            {"amount": 4000, "price": 0.40, "timestamp": "2025-06-01T12:30:00+00:00"},
        ],
        "avg_entry_price": 0.3713,
        "first_trade_time": "2025-06-01T10:00:00+00:00",
        "last_trade_time": "2025-06-01T12:30:00+00:00",
    }
    base.update(overrides)
    return base


class TestFormatTelegramDetailed:
    """Tests for the detailed Telegram format with per-wallet trade details."""

    def test_basic_structure(self):
        """Detailed format includes header, market, score, filters, wallets."""
        fmt = AlertFormatter()
        alert = _alert(
            id=42,
            filters_triggered=[
                {"filter_id": "B01", "points": 20, "filter_name": "Drip Buy", "details": "3 txs"},
            ],
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        assert "SENTINEL ALPHA" in text
        assert "ALERT #42" in text
        assert "Will X resign?" in text
        assert "Score: 75" in text
        assert "3 stars" in text
        assert "FILTERS TRIGGERED" in text
        assert "B01: +20 pts (Drip Buy)" in text
        assert "TOP WALLETS" in text
        assert "polymarket.com" in text

    def test_wallet_address_truncated(self):
        """Wallet address is shown as 0xABCD...EF12 format."""
        fmt = AlertFormatter()
        alert = _alert(wallets=[_wallet_with_trades()])
        text = fmt.format_telegram_detailed(alert)
        assert "0xABCD...EF12" in text
        # Full address should NOT appear
        assert "0xABCDEF1234567890ABCDEF1234567890ABCDEF12" not in text

    def test_trade_details_shown(self):
        """Individual trades appear with amount, price, and time."""
        fmt = AlertFormatter()
        alert = _alert(wallets=[_wallet_with_trades()])
        text = fmt.format_telegram_detailed(alert)
        assert "#1: $5,000 @ 0.35 odds" in text
        assert "#2: $6,000 @ 0.37 odds" in text
        assert "#3: $4,000 @ 0.40 odds" in text
        assert "10:00 UTC" in text
        assert "11:00 UTC" in text
        assert "12:30 UTC" in text

    def test_trade_count_and_span(self):
        """Shows trade count and time span."""
        fmt = AlertFormatter()
        alert = _alert(wallets=[_wallet_with_trades()])
        text = fmt.format_telegram_detailed(alert)
        assert "3 transactions in 2.5h" in text

    def test_total_and_avg_entry_price(self):
        """Shows total amount and avg entry price."""
        fmt = AlertFormatter()
        alert = _alert(wallets=[_wallet_with_trades()])
        text = fmt.format_telegram_detailed(alert)
        assert "Total: $15,000" in text
        assert "Avg entry: 0.37" in text

    def test_max_5_trades_per_wallet(self):
        """Only first 5 trades shown, rest summarized."""
        fmt = AlertFormatter()
        trades = [
            {"amount": 1000 * (i + 1), "price": 0.30 + i * 0.02, "timestamp": f"2025-06-01T{10+i}:00:00+00:00"}
            for i in range(8)
        ]
        wallet = _wallet_with_trades(trades=trades, trade_count=8)
        alert = _alert(wallets=[wallet])
        text = fmt.format_telegram_detailed(alert)
        assert "#1:" in text
        assert "#5:" in text
        assert "#6:" not in text
        assert "... and 3 more trades" in text

    def test_max_3_wallets(self):
        """Only top 3 wallets shown by total_amount."""
        fmt = AlertFormatter()
        wallets = [
            _wallet_with_trades(address=f"0x{'A' * 38}{i:02d}", total_amount=10000 * (5 - i))
            for i in range(5)
        ]
        alert = _alert(wallets=wallets)
        text = fmt.format_telegram_detailed(alert)
        # Top 3 wallets (amounts: 50000, 40000, 30000)
        assert "$50,000" in text
        assert "$40,000" in text
        assert "$30,000" in text
        # 4th and 5th wallets should NOT appear
        assert "$20,000" not in text
        assert "$10,000" not in text

    def test_wallets_sorted_by_amount(self):
        """Wallets are sorted by total_amount descending."""
        fmt = AlertFormatter()
        wallets = [
            _wallet_with_trades(address="0x" + "B" * 40, total_amount=8000, trades=[], trade_count=1, time_span_hours=0),
            _wallet_with_trades(address="0x" + "A" * 40, total_amount=25000, trades=[], trade_count=2, time_span_hours=0),
        ]
        alert = _alert(wallets=wallets)
        text = fmt.format_telegram_detailed(alert)
        # Larger wallet should appear first in the Total lines
        pos_25k = text.index("Total: $25,000")
        pos_8k = text.index("Total: $8,000")
        assert pos_25k < pos_8k

    def test_direction_no_odds_display(self):
        """For NO direction, market odds are direction-adjusted but trade prices are NOT."""
        fmt = AlertFormatter()
        wallet = _wallet_with_trades(
            direction="NO",
            trades=[
                {"amount": 10000, "price": 0.65, "timestamp": "2025-06-01T10:00:00+00:00"},
            ],
            avg_entry_price=0.65,
            trade_count=1,
        )
        alert = _alert(
            direction="NO",
            odds_at_alert=0.30,  # YES price → NO display = 0.70
            wallets=[wallet],
        )
        text = fmt.format_telegram_detailed(alert)
        # Market odds line: direction-adjusted (1 - 0.30 = 0.70)
        assert "Odds: 0.70" in text
        # Trade price: NOT direction-adjusted (already NO price from API)
        assert "@ 0.65 odds" in text
        # Avg entry: NOT direction-adjusted
        assert "Avg entry: 0.65" in text

    def test_direction_yes_trade_prices_unchanged(self):
        """For YES direction, trade prices displayed as-is."""
        fmt = AlertFormatter()
        wallet = _wallet_with_trades(
            direction="YES",
            trades=[
                {"amount": 8000, "price": 0.42, "timestamp": "2025-06-01T10:00:00+00:00"},
            ],
            avg_entry_price=0.42,
            trade_count=1,
        )
        alert = _alert(
            direction="YES",
            odds_at_alert=0.42,
            wallets=[wallet],
        )
        text = fmt.format_telegram_detailed(alert)
        assert "@ 0.42 odds" in text
        assert "Avg entry: 0.42" in text

    def test_price_impact_section(self):
        """Price impact is shown with direction adjustment."""
        fmt = AlertFormatter()
        alert = _alert(
            odds_at_alert=0.40,
            price_impact=0.05,
            direction="YES",
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        # 0.40 → 0.45 (+12.5%)
        assert "Price Impact" in text
        assert "0.40" in text
        assert "0.45" in text
        assert "+12.5%" in text

    def test_price_impact_no_direction(self):
        """Price impact adjusts for NO direction."""
        fmt = AlertFormatter()
        alert = _alert(
            odds_at_alert=0.30,     # YES price
            price_impact=0.05,      # YES price moved +5
            direction="NO",
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        # NO odds: old = 1-0.30 = 0.70, new = 1-0.35 = 0.65
        # pct = (0.65 - 0.70) / 0.70 * 100 = -7.1%
        assert "0.70" in text
        assert "0.65" in text
        assert "-7.1%" in text

    def test_no_price_impact_when_missing(self):
        """No price impact section when data missing."""
        fmt = AlertFormatter()
        alert = _alert(
            price_impact=None,
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        assert "Price Impact" not in text

    def test_pattern_whale_entry(self):
        """Detects B19 whale entry pattern."""
        fmt = AlertFormatter()
        alert = _alert(
            filters_triggered=[
                {"filter_id": "B19a", "points": 50, "filter_name": "Whale Entry"},
            ],
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        assert "Pattern: Single massive entry" in text

    def test_pattern_escalating_buys(self):
        """Detects B06 escalating buys pattern."""
        fmt = AlertFormatter()
        alert = _alert(
            filters_triggered=[
                {"filter_id": "B06", "points": 20, "filter_name": "Escalating"},
            ],
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        assert "Pattern: Escalating buys" in text

    def test_pattern_rapid_accumulation(self):
        """Detects B16 rapid accumulation pattern."""
        fmt = AlertFormatter()
        alert = _alert(
            filters_triggered=[
                {"filter_id": "B16", "points": 25, "filter_name": "Rapid"},
            ],
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        assert "Pattern: Rapid accumulation" in text

    def test_pattern_drip_accumulation(self):
        """Detects B01 drip accumulation pattern."""
        fmt = AlertFormatter()
        alert = _alert(
            filters_triggered=[
                {"filter_id": "B01", "points": 20, "filter_name": "Drip Buy"},
            ],
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        assert "Pattern: Drip accumulation" in text

    def test_no_pattern_when_no_filters(self):
        """No pattern label when no matching filters."""
        fmt = AlertFormatter()
        alert = _alert(
            filters_triggered=[
                {"filter_id": "C04", "points": 40, "filter_name": "Confluence"},
            ],
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        assert "Pattern:" not in text

    def test_graceful_with_old_wallet_format(self):
        """Handles wallets without trade details (backward compat)."""
        fmt = AlertFormatter()
        old_wallet = {
            "address": "0xABCDEF1234567890ABCDEF1234567890ABCDEF12",
            "direction": "YES",
            "total_amount": 10000,
            "trade_count": 5,
            "time_span_hours": 3,
        }
        alert = _alert(wallets=[old_wallet])
        text = fmt.format_telegram_detailed(alert)
        # Should still show wallet info
        assert "0xABCD...EF12" in text
        assert "$10,000" in text
        assert "5 transactions in 3h" in text
        # No trades section, no avg entry
        assert "#1:" not in text

    def test_graceful_with_empty_wallets(self):
        """Handles empty wallets list."""
        fmt = AlertFormatter()
        alert = _alert(wallets=[])
        text = fmt.format_telegram_detailed(alert)
        assert "TOP WALLETS" not in text

    def test_graceful_with_none_wallets(self):
        """Handles None wallets."""
        fmt = AlertFormatter()
        alert = _alert(wallets=None)
        text = fmt.format_telegram_detailed(alert)
        assert "TOP WALLETS" not in text

    def test_single_trade_no_plural(self):
        """Single trade shows 'transaction' not 'transactions'."""
        fmt = AlertFormatter()
        wallet = _wallet_with_trades(
            trade_count=1,
            time_span_hours=0,
            trades=[{"amount": 5000, "price": 0.35, "timestamp": "2025-06-01T10:00:00+00:00"}],
        )
        alert = _alert(wallets=[wallet])
        text = fmt.format_telegram_detailed(alert)
        assert "1 transaction" in text
        assert "1 transactions" not in text

    def test_multiplier_section_shown(self):
        """Multiplier section shows amount and effective multiplier."""
        fmt = AlertFormatter()
        alert = _alert(
            total_amount=47200,
            score_raw=50,
            score=75,
            multiplier=1.5,
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        assert "MULTIPLIERS" in text
        assert "$47,200 total" in text
        assert "Effective:" in text

    def test_filters_section_with_details(self):
        """Filters show ID, points, name, and details."""
        fmt = AlertFormatter()
        alert = _alert(
            filters_triggered=[
                {"filter_id": "B01", "points": 20, "filter_name": "Drip Buy", "details": "5 txs in 2h"},
                {"filter_id": "C04", "points": 40, "filter_name": "Confluence", "details": ""},
            ],
            wallets=[_wallet_with_trades()],
        )
        text = fmt.format_telegram_detailed(alert)
        assert "B01: +20 pts (Drip Buy) \u2014 5 txs in 2h" in text
        assert "C04: +40 pts (Confluence)" in text
        # Empty details should not show " — "
        lines = text.split("\n")
        c04_line = [l for l in lines if "C04:" in l][0]
        assert "\u2014" not in c04_line
