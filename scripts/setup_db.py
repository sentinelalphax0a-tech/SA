"""
Database Setup Script.

Verifies Supabase connection and checks that all required tables exist.
Run this after executing the SQL schema in Supabase SQL Editor.
"""

import sys
import logging

from src.database.supabase_client import SupabaseClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REQUIRED_TABLES = [
    "wallets",
    "markets",
    "alerts",
    "wallet_funding",
    "scans",
    "weekly_reports",
    "smart_money_leaderboard",
    "system_config",
]


def verify_tables() -> bool:
    """Check that all required tables exist and are accessible."""
    db = SupabaseClient()
    all_ok = True

    for table in REQUIRED_TABLES:
        try:
            resp = db.client.table(table).select("*", count="exact").limit(0).execute()
            logger.info(f"  ✓ {table} (rows: {resp.count})")
        except Exception as e:
            logger.error(f"  ✗ {table}: {e}")
            all_ok = False

    return all_ok


def main() -> None:
    logger.info("Sentinel Alpha — Database verification")
    logger.info("=" * 40)

    if verify_tables():
        logger.info("\nAll tables verified successfully.")
    else:
        logger.error("\nSome tables are missing. Run the SQL schema in Supabase.")
        sys.exit(1)


if __name__ == "__main__":
    main()
