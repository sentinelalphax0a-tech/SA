"""
Telegram Bot — Publishes alerts to @SentinelAlphaChannel.

Handles bot auth and message sending. No rate limit on Telegram.
Publishes alerts with star_level >= 2 (score >= 50) and all whale entries (B19).
"""

import logging

import requests

from src import config

logger = logging.getLogger(__name__)


class TelegramBot:
    """Publishes alerts to a Telegram channel."""

    def __init__(self) -> None:
        self.token = config.TELEGRAM_BOT_TOKEN
        self.channel_id = config.TELEGRAM_CHANNEL_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def send_message(
        self, text: str, parse_mode: str = "HTML"
    ) -> str | None:
        """Send a text message to the Telegram channel.

        Returns the message_id as string, or None on failure.
        """
        if not self.token or not self.channel_id:
            logger.warning("Telegram credentials not configured, skipping")
            return None
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.channel_id,
                    "text": text,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("Telegram API error: %s", data.get("description"))
                return None
            msg_id = str(data["result"]["message_id"])
            logger.info("Telegram message sent: %s", msg_id)
            return msg_id
        except Exception as e:
            logger.error("send_message failed: %s", e)
            return None

    # Alias for backwards compatibility
    publish = send_message

    def send_photo(
        self, photo_path: str, caption: str = "", parse_mode: str = "HTML"
    ) -> str | None:
        """Send a photo with optional caption to the Telegram channel.

        Returns the message_id as string, or None on failure.
        """
        if not self.token or not self.channel_id:
            logger.warning("Telegram credentials not configured, skipping")
            return None
        try:
            with open(photo_path, "rb") as img:
                resp = requests.post(
                    f"{self.base_url}/sendPhoto",
                    data={
                        "chat_id": self.channel_id,
                        "caption": caption,
                        "parse_mode": parse_mode,
                    },
                    files={"photo": img},
                    timeout=30,
                )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("Telegram API error: %s", data.get("description"))
                return None
            msg_id = str(data["result"]["message_id"])
            logger.info("Telegram photo sent: %s", msg_id)
            return msg_id
        except Exception as e:
            logger.error("send_photo failed: %s", e)
            return None

    # Alias for backwards compatibility
    send_chart = send_photo

    def test_connection(self) -> bool:
        """Send a test message to verify bot + channel work."""
        msg_id = self.send_message(
            "\U0001f50d Sentinel Alpha \u2014 Connection test \u2705"
        )
        return msg_id is not None
