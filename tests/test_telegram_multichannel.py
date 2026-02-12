"""Tests for multi-channel Telegram and sell notifications."""

from unittest.mock import patch, MagicMock

from src.database.models import Alert
from src.publishing.telegram_bot import TelegramBot
from src.publishing.formatter import AlertFormatter


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
    with patch("src.publishing.telegram_bot.config") as mock_cfg:
        mock_cfg.TELEGRAM_BOT_TOKEN = "fake-token"
        mock_cfg.TELEGRAM_CHANNEL_ID = "-100123"
        mock_cfg.TELEGRAM_PUBLIC_CHANNEL_ID = "-100456"
        mock_cfg.TELEGRAM_VIP_CHANNEL_ID = ""
        bot = TelegramBot()
    bot.send_message = MagicMock(return_value="42")
    bot.send_to_channel = MagicMock(return_value="99")
    return bot


# ── Multi-channel ───────────────────────────────────────


class TestPublishToPublic:
    def test_publishes_short_format_to_public(self):
        bot = _make_bot()
        alert = _alert(star_level=3)
        with patch("src.publishing.telegram_bot.config") as mock_cfg:
            mock_cfg.TELEGRAM_PUBLIC_CHANNEL_ID = "-100456"
            msg_id = bot.publish_to_public(alert)
        assert msg_id == "99"
        bot.send_to_channel.assert_called_once()
        # Should use the public channel ID
        args = bot.send_to_channel.call_args
        assert args[0][0] == "-100456"

    def test_public_no_channel_returns_none(self):
        bot = _make_bot()
        bot.send_to_channel = MagicMock()
        with patch("src.publishing.telegram_bot.config") as mock_cfg:
            mock_cfg.TELEGRAM_PUBLIC_CHANNEL_ID = ""
            result = bot.publish_to_public(_alert())
        assert result is None
        bot.send_to_channel.assert_not_called()


class TestSendToChannel:
    def test_sends_to_specific_channel(self):
        bot = _make_bot()
        bot.send_to_channel = TelegramBot.send_to_channel.__get__(bot)
        # Mock the HTTP call
        with patch("src.publishing.telegram_bot.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True, "result": {"message_id": 77}}
            mock_resp.raise_for_status = MagicMock()
            mock_req.post.return_value = mock_resp

            msg_id = bot.send_to_channel("-100999", "Test message")
            assert msg_id == "77"
            call_args = mock_req.post.call_args
            assert call_args[1]["json"]["chat_id"] == "-100999"

    def test_no_token_returns_none(self):
        with patch("src.publishing.telegram_bot.config") as mock_cfg:
            mock_cfg.TELEGRAM_BOT_TOKEN = ""
            mock_cfg.TELEGRAM_CHANNEL_ID = "-100123"
            mock_cfg.TELEGRAM_PUBLIC_CHANNEL_ID = ""
            mock_cfg.TELEGRAM_VIP_CHANNEL_ID = ""
            bot = TelegramBot()
        result = bot.send_to_channel("-100999", "test")
        assert result is None


# ── Sell notifications ──────────────────────────────────


class TestSellNotification:
    def test_individual_sell(self):
        bot = _make_bot()
        event = {
            "type": "individual",
            "market_id": "m1",
            "market_question": "Will X happen?",
            "wallets": [
                {
                    "address": "0x1234567890abcdef",
                    "sell_amount": 2000.0,
                    "original_amount": 5000.0,
                    "direction": "YES",
                }
            ],
        }
        msg_id = bot.publish_sell_notification(event)
        assert msg_id == "42"
        text = bot.send_message.call_args[0][0]
        assert "SELL DETECTED" in text
        assert "$2,000" in text

    def test_coordinated_sell(self):
        bot = _make_bot()
        event = {
            "type": "coordinated",
            "market_id": "m1",
            "market_question": "Will X happen?",
            "wallets": [
                {
                    "address": "0x1234567890abcdef",
                    "sell_amount": 2000.0,
                    "original_amount": 5000.0,
                },
                {
                    "address": "0xabcdef1234567890",
                    "sell_amount": 1500.0,
                    "original_amount": 3000.0,
                },
            ],
        }
        msg_id = bot.publish_sell_notification(event)
        assert msg_id == "42"
        text = bot.send_message.call_args[0][0]
        assert "COORDINATED SELL" in text
        assert "2 wallets" in text


# ── Formatter ──────────────────────────────────────────


class TestFormatterSell:
    def test_format_sell_notification(self):
        fmt = AlertFormatter()
        event = {
            "market_question": "Will BTC hit 100k?",
            "wallets": [
                {
                    "address": "0x1234567890abcdef1234",
                    "sell_amount": 3000.0,
                    "original_amount": 10000.0,
                    "direction": "YES",
                }
            ],
        }
        text = fmt.format_sell_notification(event)
        assert "SELL DETECTED" in text
        assert "BTC" in text
        assert "$3,000" in text

    def test_format_coordinated_sell(self):
        fmt = AlertFormatter()
        event = {
            "market_question": "Will ETH merge?",
            "wallets": [
                {"address": "0xabc123", "sell_amount": 1000.0, "original_amount": 5000.0},
                {"address": "0xdef456", "sell_amount": 2000.0, "original_amount": 4000.0},
            ],
        }
        text = fmt.format_coordinated_sell(event)
        assert "COORDINATED SELL" in text
        assert "2 wallets" in text
