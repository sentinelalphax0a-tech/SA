"""
Entry point for the market resolver.

Usage:
    python -m src.tracking.run_resolver
"""

import logging

from src.tracking.resolver import MarketResolver
from src.database.supabase_client import SupabaseClient
from src.scanner.polymarket_client import PolymarketClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Market resolver starting...")
    db = SupabaseClient()
    pm = PolymarketClient()
    resolver = MarketResolver(db, pm)
    result = resolver.run()
    logger.info(
        "Market resolver finished — %d resolved (%d correct, %d incorrect)",
        result["resolved"], result["correct"], result["incorrect"],
    )


if __name__ == "__main__":
    main()
