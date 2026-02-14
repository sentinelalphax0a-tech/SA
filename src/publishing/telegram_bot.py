"""
Telegram Bot — Publishes alerts to @SentinelAlphaChannel.

Handles bot auth and message sending with rate limiting.
Publishes alerts with star_level >= 2 (score >= 50) and all whale entries (B19).
"""

import logging
import os
import time

import requests

from src import config
from src.database.models import Alert
from src.publishing.formatter import AlertFormatter

logger = logging.getLogger(__name__)

_SEND_DELAY = 1.5  # seconds between consecutive sends


class TelegramBot:
    """Publishes alerts to a Telegram channel."""

    def __init__(self) -> None:
        self.token = config.TELEGRAM_BOT_TOKEN
        self.channel_id = config.TELEGRAM_CHANNEL_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.formatter = AlertFormatter()
        self._last_send_time: float = 0.0

    def _rate_limit_delay(self) -> None:
        """Wait if needed to enforce minimum delay between sends."""
        elapsed = time.monotonic() - self._last_send_time
        if self._last_send_time > 0 and elapsed < _SEND_DELAY:
            time.sleep(_SEND_DELAY - elapsed)

    def send_message(
        self, text: str, parse_mode: str = "HTML"
    ) -> str | None:
        """Send a text message to the Telegram channel.

        Returns the message_id as string, or None on failure.
        Enforces 1.5s delay between sends and retries once on 429.
        """
        if not self.token or not self.channel_id:
            logger.warning("Telegram credentials not configured, skipping")
            return None

        self._rate_limit_delay()

        # Visual separator between consecutive messages
        text = "\n" + text

        payload = {
            "chat_id": self.channel_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        url = f"{self.base_url}/sendMessage"

        try:
            resp = requests.post(url, json=payload, timeout=10)

            # 429 retry: wait 5s, try once more
            if resp.status_code == 429:
                logger.warning("Telegram rate-limited (429), retrying in 5s")
                time.sleep(5)
                resp = requests.post(url, json=payload, timeout=10)

            self._last_send_time = time.monotonic()
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

        self._rate_limit_delay()

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
            self._last_send_time = time.monotonic()
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
        """Publish a smart money alert to Telegram.

        Testing phase: publishes ALL star levels (including 1-star) using
        detailed format (filters + multipliers).  For public launch,
        restore the star >= 2 gate and switch to format_telegram_alert.

        Returns:
            message_id on success, None otherwise.
        """
        text = self.formatter.format_telegram_detailed(alert)
        return self.send_message(text, parse_mode="")

    def publish_whale_entry(self, alert: Alert) -> str | None:
        """Publish a whale entry alert to Telegram (B19, always published).

        Testing phase: uses detailed format (same as regular alerts) so we
        can evaluate filter quality.  For public launch, switch back to
        format_whale_entry.

        Returns:
            message_id on success, None otherwise.
        """
        text = self.formatter.format_telegram_detailed(alert)
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

    # ── Sell notifications ──────────────────────────────────

    def publish_sell_notification(self, sell_event: dict) -> str | None:
        """Publish an individual sell notification to Telegram.

        Returns:
            message_id on success, None otherwise.
        """
        if sell_event.get("type") == "coordinated":
            text = self.formatter.format_coordinated_sell(sell_event)
        else:
            text = self.formatter.format_sell_notification(sell_event)
        return self.send_message(text, parse_mode="")

    # ── Multi-channel ──────────────────────────────────────

    def send_to_channel(
        self, channel_id: str, text: str, parse_mode: str = "HTML"
    ) -> str | None:
        """Send a message to a specific Telegram channel.

        Returns the message_id as string, or None on failure.
        """
        if not self.token or not channel_id:
            return None

        self._rate_limit_delay()

        text = "\n" + text

        payload = {
            "chat_id": channel_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        url = f"{self.base_url}/sendMessage"

        try:
            resp = requests.post(url, json=payload, timeout=10)

            if resp.status_code == 429:
                logger.warning("Telegram rate-limited (429), retrying in 5s")
                time.sleep(5)
                resp = requests.post(url, json=payload, timeout=10)

            self._last_send_time = time.monotonic()
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("Telegram API error: %s", data.get("description"))
                return None
            msg_id = str(data["result"]["message_id"])
            logger.info("Telegram message sent to %s: %s", channel_id, msg_id)
            return msg_id
        except Exception as e:
            logger.error("send_to_channel failed: %s", e)
            return None

    def publish_to_public(self, alert: Alert) -> str | None:
        """Publish a short-format alert to the public channel (stars >= 3).

        Uses format_telegram_alert (no filter details, no scoring internals).
        """
        public_id = config.TELEGRAM_PUBLIC_CHANNEL_ID
        if not public_id:
            return None
        text = self.formatter.format_telegram_alert(alert)
        return self.send_to_channel(public_id, text, parse_mode="")

    # ── Diagnostics ───────────────────────────────────────────

    def test_connection(self) -> bool:
        """Send a test message to verify bot + channel work."""
        msg_id = self.send_message(
            "\U0001f50d Sentinel Alpha \u2014 Connection test \u2705"
        )
        return msg_id is not None
