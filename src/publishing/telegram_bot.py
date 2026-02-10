"""
Telegram Bot — Publishes alerts to @SentinelAlphaChannel.

Handles bot auth and message sending. No rate limit on Telegram.
Publishes alerts with star_level >= 2 (score >= 50) and all whale entries (B19).
"""

import logging
import os

import requests

from src import config
from src.database.models import Alert
from src.publishing.formatter import AlertFormatter

logger = logging.getLogger(__name__)


class TelegramBot:
    """Publishes alerts to a Telegram channel."""

    def __init__(self) -> None:
        self.token = config.TELEGRAM_BOT_TOKEN
        self.channel_id = config.TELEGRAM_CHANNEL_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.formatter = AlertFormatter()

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

    # ── High-level publish methods ────────────────────────────

    def publish_alert(self, alert: Alert) -> str | None:
        """Publish a smart money alert to Telegram (star_level >= 2).

        Returns:
            message_id on success, None otherwise.
        """
        star = alert.star_level or 0
        if star < 2:
            logger.debug("Skipping TG publish: star_level %d < 2", star)
            return None

        text = self.formatter.format_telegram_alert(alert)
        return self.send_message(text, parse_mode="")

    def publish_whale_entry(self, alert: Alert) -> str | None:
        """Publish a whale entry alert to Telegram (B19, always published).

        Returns:
            message_id on success, None otherwise.
        """
        text = self.formatter.format_whale_entry(alert)
        return self.send_message(text, parse_mode="")

    def publish_resolution(self, alert: Alert) -> str | None:
        """Publish a resolution follow-up to Telegram.

        Returns:
            message_id on success, None otherwise.
        """
        text = self.formatter.format_telegram_resolution(alert)
        return self.send_message(text, parse_mode="")

    def publish_report(
        self, text: str, chart_path: str | None = None,
    ) -> str | None:
        """Publish a weekly/monthly report to Telegram.

        If chart_path is provided and the file exists, sends it as a photo
        with the text as caption.  Otherwise sends text only.

        Returns:
            message_id on success, None otherwise.
        """
        if chart_path and os.path.isfile(chart_path):
            return self.send_photo(chart_path, caption=text)
        return self.send_message(text, parse_mode="HTML")

    # ── Diagnostics ───────────────────────────────────────────

    def test_connection(self) -> bool:
        """Send a test message to verify bot + channel work."""
        msg_id = self.send_message(
            "\U0001f50d Sentinel Alpha \u2014 Connection test \u2705"
        )
        return msg_id is not None
