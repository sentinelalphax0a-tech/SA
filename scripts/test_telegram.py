"""
Quick Telegram bot connection test.

Sends a test message to the configured channel.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.publishing.telegram_bot import TelegramBot


def main() -> None:
    print("Sending test message to Telegram channel...")
    bot = TelegramBot()
    ok = bot.test_connection()
    if ok:
        print("\u2705 Telegram message sent successfully")
    else:
        print("FAIL: Could not send Telegram message")
        print("Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID in .env")
        sys.exit(1)


if __name__ == "__main__":
    main()
