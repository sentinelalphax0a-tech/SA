"""
Backfill Script.

Backfills historical data for wallets and markets that were
detected before funding source tracing was implemented.
"""

import logging

from src.database.supabase_client import SupabaseClient
from src.scanner.blockchain_client import BlockchainClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def backfill_wallet_funding() -> None:
    """Backfill funding sources for wallets missing funding data."""
    db = SupabaseClient()
    chain = BlockchainClient()

    # TODO: Query wallets without funding_sources, trace and save
    raise NotImplementedError


def backfill_wallet_ages() -> None:
    """Backfill wallet_age_days for wallets where it is NULL."""
    db = SupabaseClient()
    chain = BlockchainClient()

    # TODO: Query wallets with NULL wallet_age_days, fetch and update
    raise NotImplementedError


if __name__ == "__main__":
    logger.info("Sentinel Alpha — Backfill")
    backfill_wallet_funding()
    backfill_wallet_ages()
