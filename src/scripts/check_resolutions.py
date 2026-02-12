"""
Daily Resolution Checker — checks if tracked alerts have resolved.

Runs daily (via GitHub Actions or cron). For each pending alert_tracking:
  1. Check if the market has resolved via Polymarket API
  2. If resolved:
     a. Update alert_tracking.outcome (correct/incorrect)
     b. Apply B21 price reversion scoring
     c. Update wallet_categories via WR01/SP01
     d. Publish resolution to Telegram

Executable as: python -m src.scripts.check_resolutions
"""

import logging
import time
from datetime import datetime, timezone

from src.database.supabase_client import SupabaseClient
from src.database.models import AlertTracking
from src.scanner.polymarket_client import PolymarketClient
from src.analysis.reversion_checker import check_reversion
from src.analysis.wallet_tracker import WalletTracker
from src.publishing.telegram_bot import TelegramBot
from src.database.models import Alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("check_resolutions")


def run_resolutions() -> None:
    """Check all pending alert trackings for resolution."""
    logger.info("=== Resolution check started ===")

    db = SupabaseClient()
    pm = PolymarketClient()
    tracker = WalletTracker(db_client=db)
    telegram = TelegramBot()

    try:
        pending = db.get_pending_alert_trackings()
    except Exception as e:
        logger.error("Failed to fetch pending alert trackings: %s", e)
        return

    logger.info("Found %d pending alert trackings", len(pending))

    resolved_count = 0
    correct_count = 0

    for tracking in pending:
        alert_id = tracking.get("alert_id")
        market_id = tracking.get("market_id")
        direction = tracking.get("direction")
        odds_at_alert = tracking.get("odds_at_alert")

        if not market_id or not direction:
            continue

        try:
            market = pm.get_market_info(market_id)
        except Exception as e:
            logger.debug("get_market_info failed for %s: %s", market_id, e)
            continue

        if market is None or not market.is_resolved:
            continue

        # Market is resolved
        outcome = market.outcome  # "Yes" or "No" from API
        if outcome is None:
            continue

        is_correct = direction.upper() == outcome.upper()
        outcome_label = "correct" if is_correct else "incorrect"
        now = datetime.now(timezone.utc)

        # 1. Update alert_tracking
        try:
            db.update_alert_tracking_outcome(alert_id, outcome_label, now)
        except Exception as e:
            logger.error("update_alert_tracking_outcome failed for %d: %s", alert_id, e)

        # 2. Apply B21 reversion scoring
        current_odds = market.current_odds
        reversion_result = check_reversion(direction, odds_at_alert, current_odds)
        if reversion_result:
            logger.info(
                "B21 for alert #%d: %s (%+d pts)",
                alert_id, reversion_result.filter_name, reversion_result.points,
            )

        # 3. Update wallet categories (WR01 + SP01)
        try:
            alert_data = db.get_alert(alert_id)
            if alert_data:
                wallets = alert_data.get("wallets") or []
                total_amount = alert_data.get("total_amount", 0)
                for w in wallets:
                    addr = w.get("address")
                    if addr:
                        tracker.update_win_rate(addr, is_correct, total_amount)
                        tracker.update_specialization(addr, market.category)
        except Exception as e:
            logger.error("Wallet tracking failed for alert #%d: %s", alert_id, e)

        # 4. Publish resolution to Telegram
        try:
            alert_data = db.get_alert(alert_id)
            if alert_data:
                alert_obj = Alert(
                    market_id=market_id,
                    alert_type=alert_data.get("alert_type", "accumulation"),
                    score=alert_data.get("score", 0),
                    market_question=alert_data.get("market_question"),
                    direction=direction,
                    star_level=alert_data.get("star_level"),
                    total_amount=alert_data.get("total_amount"),
                    outcome=outcome_label.upper(),
                )
                telegram.publish_resolution(alert_obj)
        except Exception as e:
            logger.error("Resolution publish failed for alert #%d: %s", alert_id, e)

        resolved_count += 1
        if is_correct:
            correct_count += 1

        logger.info(
            "Alert #%d resolved: %s (%s predicted %s, outcome %s)",
            alert_id, outcome_label, direction, direction, outcome,
        )

        time.sleep(0.1)  # small delay between API calls

    logger.info(
        "=== Resolution check complete: %d resolved (%d correct, %d incorrect) ===",
        resolved_count, correct_count, resolved_count - correct_count,
    )


if __name__ == "__main__":
    run_resolutions()
