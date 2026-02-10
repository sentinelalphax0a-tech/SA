"""Tests for the Telegram bot."""

import os
import tempfile
from unittest.mock import patch, MagicMock

from src.database.models import Alert
from src.publishing.telegram_bot import TelegramBot


def _alert(**overrides) -> Alert:
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


def _make_bot() -> TelegramBot:
    """Create a TelegramBot with mocked send primitives."""
    with patch("src.publishing.telegram_bot.config") as mock_cfg:
        mock_cfg.TELEGRAM_BOT_TOKEN = "fake-token"
        mock_cfg.TELEGRAM_CHANNEL_ID = "-100123"
        bot = TelegramBot()
    bot.send_message = MagicMock(return_value="42")
    bot.send_photo = MagicMock(return_value="43")
    return bot


# ── publish_alert ────────────────────────────────────────────


class TestPublishAlert:
    def test_publishes_2_star(self):
        bot = _make_bot()
        msg_id = bot.publish_alert(_alert(star_level=2))
        assert msg_id == "42"
        bot.send_message.assert_called_once()

    def test_publishes_5_star(self):
        bot = _make_bot()
        msg_id = bot.publish_alert(_alert(star_level=5))
        assert msg_id == "42"

    def test_skips_below_2_stars(self):
        bot = _make_bot()
        msg_id = bot.publish_alert(_alert(star_level=1))
        assert msg_id is None
        bot.send_message.assert_not_called()

    def test_skips_0_stars(self):
        bot = _make_bot()
        msg_id = bot.publish_alert(_alert(star_level=0))
        assert msg_id is None

    def test_text_contains_market_and_score(self):
        bot = _make_bot()
        bot.publish_alert(_alert(market_question="Will BTC moon?", score=88))
        text = bot.send_message.call_args[0][0]
        assert "Will BTC moon?" in text
        assert "Score: 88" in text
        assert "SMART MONEY DETECTED" in text

    def test_text_has_no_filter_ids(self):
        bot = _make_bot()
        alert = _alert(
            star_level=3,
            filters_triggered=[{"id": "W01", "points": 25}],
        )
        bot.publish_alert(alert)
        text = bot.send_message.call_args[0][0]
        assert "W01" not in text

    def test_uses_empty_parse_mode(self):
        """Alert text has emojis, not HTML — parse_mode should be empty."""
        bot = _make_bot()
        bot.publish_alert(_alert(star_level=3))
        _, kwargs = bot.send_message.call_args
        assert kwargs.get("parse_mode") == ""


# ── publish_whale_entry ──────────────────────────────────────


class TestPublishWhaleEntry:
    def test_always_publishes(self):
        """B19 whale entries are always published, regardless of stars."""
        bot = _make_bot()
        msg_id = bot.publish_whale_entry(_alert(star_level=0))
        assert msg_id == "42"
        bot.send_message.assert_called_once()

    def test_text_contains_whale(self):
        bot = _make_bot()
        bot.publish_whale_entry(_alert(total_amount=50000))
        text = bot.send_message.call_args[0][0]
        assert "WHALE ENTRY" in text
        assert "$50,000" in text

    def test_no_score_in_whale(self):
        bot = _make_bot()
        bot.publish_whale_entry(_alert(score=90))
        text = bot.send_message.call_args[0][0]
        assert "Score" not in text


# ── publish_resolution ───────────────────────────────────────


class TestPublishResolution:
    def test_publishes_correct(self):
        bot = _make_bot()
        msg_id = bot.publish_resolution(_alert(outcome="YES", direction="YES"))
        assert msg_id == "42"
        bot.send_message.assert_called_once()

    def test_text_contains_resolved(self):
        bot = _make_bot()
        bot.publish_resolution(_alert(outcome="NO", direction="YES"))
        text = bot.send_message.call_args[0][0]
        assert "ALERT RESOLVED" in text
        assert "INCORRECT" in text

    def test_text_contains_score(self):
        bot = _make_bot()
        bot.publish_resolution(
            _alert(outcome="YES", direction="YES", score=85, star_level=4)
        )
        text = bot.send_message.call_args[0][0]
        assert "Score: 85" in text


# ── publish_report ───────────────────────────────────────────


class TestPublishReport:
    def test_text_only(self):
        bot = _make_bot()
        msg_id = bot.publish_report("Weekly report text")
        assert msg_id == "42"
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][0]
        assert "Weekly report text" in text

    def test_text_only_when_no_chart(self):
        bot = _make_bot()
        msg_id = bot.publish_report("Report", chart_path=None)
        assert msg_id == "42"
        bot.send_message.assert_called_once()
        bot.send_photo.assert_not_called()

    def test_text_only_when_chart_missing(self):
        bot = _make_bot()
        msg_id = bot.publish_report("Report", chart_path="/nonexistent/chart.png")
        assert msg_id == "42"
        bot.send_message.assert_called_once()
        bot.send_photo.assert_not_called()

    def test_photo_when_chart_exists(self):
        bot = _make_bot()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"fake png")
            chart_path = f.name
        try:
            msg_id = bot.publish_report("Report with chart", chart_path=chart_path)
            assert msg_id == "43"
            bot.send_photo.assert_called_once_with(chart_path, caption="Report with chart")
            bot.send_message.assert_not_called()
        finally:
            os.unlink(chart_path)

    def test_uses_html_parse_mode(self):
        bot = _make_bot()
        bot.publish_report("<b>Bold report</b>")
        _, kwargs = bot.send_message.call_args
        assert kwargs.get("parse_mode") == "HTML"


# ── No credentials ───────────────────────────────────────────


class TestNoCredentials:
    def test_no_token_no_crash(self):
        with patch("src.publishing.telegram_bot.config") as mock_cfg:
            mock_cfg.TELEGRAM_BOT_TOKEN = ""
            mock_cfg.TELEGRAM_CHANNEL_ID = "-100123"
            bot = TelegramBot()
        result = bot.send_message("test")
        assert result is None

    def test_no_channel_no_crash(self):
        with patch("src.publishing.telegram_bot.config") as mock_cfg:
            mock_cfg.TELEGRAM_BOT_TOKEN = "token"
            mock_cfg.TELEGRAM_CHANNEL_ID = ""
            bot = TelegramBot()
        result = bot.send_message("test")
        assert result is None


# ── No leaks ─────────────────────────────────────────────────


class TestNoLeaks:
    def test_no_filter_ids_in_any_method(self):
        bot = _make_bot()
        alert = _alert(
            outcome="YES",
            star_level=3,
            filters_triggered=[
                {"id": "W01", "points": 25},
                {"id": "C07", "points": 60},
            ],
        )
        bot.publish_alert(alert)
        bot.publish_whale_entry(alert)
        bot.publish_resolution(alert)

        for call in bot.send_message.call_args_list:
            text = call[0][0]
            assert "W01" not in text
            assert "C07" not in text

    def test_no_multiplier_in_any_method(self):
        bot = _make_bot()
        alert = _alert(outcome="YES", star_level=3, multiplier=1.4)
        bot.publish_alert(alert)
        bot.publish_whale_entry(alert)
        bot.publish_resolution(alert)

        for call in bot.send_message.call_args_list:
            text = call[0][0]
            assert "multiplier" not in text.lower()
            assert "1.4" not in text
