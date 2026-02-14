"""
Entry point for the alert tracker + notifier.

Usage:
    python -m src.tracking.run_tracker
"""

import logging

from src.tracking.alert_tracker import AlertTracker
from src.tracking.alert_notifier import AlertNotifier
from src.tracking.whale_monitor import WhaleMonitor
from src.database.supabase_client import SupabaseClient
from src.scanner.polymarket_client import PolymarketClient
from src.publishing.telegram_bot import TelegramBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    db = SupabaseClient()
    pm = PolymarketClient()
    telegram = TelegramBot()

    # 1. Track odds on pending alerts
    logger.info("Alert tracker starting...")
    tracker = AlertTracker(db, pm)
    tracked = tracker.run()
    logger.info("Alert tracker finished — %d alerts tracked", tracked)

    # 2. Send follow-up notifications
    logger.info("Alert notifier starting...")
    notifier = AlertNotifier(db, telegram)
    counts = notifier.run()
    logger.info(
        "Alert notifier finished — closing=%d, odds=%d, resolutions=%d",
        counts["closing_soon"], counts["odds_updates"], counts["resolutions"],
    )

    # 3. Monitor whale wallets on 4-5★ alerts
    logger.info("Whale monitor starting...")
    whale_monitor = WhaleMonitor(db, pm, telegram)
    whale_events = whale_monitor.run()
    logger.info("Whale monitor finished — %d events sent", whale_events)


if __name__ == "__main__":
    main()
