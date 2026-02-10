"""
Sentinel Alpha — Main entry point.

Orchestrates the full scan cycle:
1. Check system config (kill switch)
2. Fetch active markets
3. Fetch recent trades, filter by MIN_TX_AMOUNT
4. Group by wallet, calculate accumulation windows
5. Run filters (W, O, B, N) per wallet
6. Detect confluence (C) per market
7. Score and apply multipliers
8. Check odds range
9. Generate alerts → DB, Telegram, X
10. Log scan results
"""

import logging
import time
from datetime import datetime

from src import config
from src.database.supabase_client import SupabaseClient
from src.scanner.polymarket_client import PolymarketClient
from src.scanner.blockchain_client import BlockchainClient
from src.scanner.news_checker import NewsChecker
from src.analysis.wallet_analyzer import WalletAnalyzer
from src.analysis.behavior_analyzer import BehaviorAnalyzer
from src.analysis.confluence_detector import ConfluenceDetector
from src.analysis.market_analyzer import MarketAnalyzer
from src.analysis.noise_filter import NoiseFilter
from src.analysis.arbitrage_filter import ArbitrageFilter
from src.analysis.scoring import calculate_score
from src.publishing.twitter_bot import TwitterBot
from src.publishing.telegram_bot import TelegramBot
from src.publishing.formatter import AlertFormatter
from src.database.models import Scan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentinel_alpha")


def run_scan() -> None:
    """Execute a single scan cycle."""
    start_time = time.time()
    db = SupabaseClient()

    # Step 1: Check kill switch
    if not db.is_scan_enabled():
        logger.info("Scan disabled via system_config. Exiting.")
        return

    scan = Scan(timestamp=datetime.utcnow())
    logger.info("=== Sentinel Alpha scan started ===")

    try:
        pm_client = PolymarketClient()
        chain_client = BlockchainClient()
        news = NewsChecker()
        wallet_analyzer = WalletAnalyzer(db, chain_client)
        behavior_analyzer = BehaviorAnalyzer()
        confluence_detector = ConfluenceDetector(db)
        market_analyzer = MarketAnalyzer()
        noise_filter = NoiseFilter(news)
        arb_filter = ArbitrageFilter()
        formatter = AlertFormatter()
        twitter = TwitterBot()
        telegram = TelegramBot()

        # Step 2: Fetch active markets
        markets = pm_client.get_active_markets()
        scan.markets_scanned = len(markets)
        logger.info(f"Fetched {len(markets)} active markets")

        # Step 3: Fetch recent trades
        trades = pm_client.get_recent_trades(
            minutes=config.SCAN_LOOKBACK_MINUTES,
            min_amount=config.MIN_TX_AMOUNT,
        )
        scan.transactions_analyzed = len(trades)
        logger.info(f"Fetched {len(trades)} trades above ${config.MIN_TX_AMOUNT}")

        # Step 4-8: Analysis pipeline
        # TODO: Implement full pipeline in Phase 2+

        scan.duration_seconds = time.time() - start_time
        scan.status = "success"
        logger.info(
            f"=== Scan complete: {scan.alerts_generated} alerts "
            f"in {scan.duration_seconds:.1f}s ==="
        )

    except Exception as e:
        scan.duration_seconds = time.time() - start_time
        scan.status = "error"
        scan.errors = str(e)
        logger.error(f"Scan failed: {e}", exc_info=True)

    finally:
        db.insert_scan(scan)


if __name__ == "__main__":
    run_scan()
