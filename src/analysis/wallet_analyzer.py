"""
Wallet Analyzer — W and O filters.

Evaluates wallet-level filters:
  W01-W03: Wallet age tiers
  W04-W05: Market count
  W09: First tx = Polymarket
  W11: Round balance
  O01: Exchange origin
  O02-O03: Funding recency
"""

import logging
from datetime import datetime, timedelta, timezone

from src import config
from src.database.models import Wallet, WalletFunding, FilterResult
from src.database.supabase_client import SupabaseClient
from src.scanner.blockchain_client import BlockchainClient

logger = logging.getLogger(__name__)


class WalletAnalyzer:
    """Evaluates wallet-level and origin filters."""

    def __init__(self, db: SupabaseClient, chain: BlockchainClient) -> None:
        self.db = db
        self.chain = chain

    def analyze(self, wallet: Wallet) -> list[FilterResult]:
        """Run all W and O filters on a wallet. Returns triggered filters."""
        results: list[FilterResult] = []
        results.extend(self._check_wallet_age(wallet))
        results.extend(self._check_market_count(wallet))
        results.extend(self._check_first_tx_pm(wallet))
        results.extend(self._check_round_balance(wallet))
        results.extend(self._check_origin(wallet))
        return results

    def _check_wallet_age(self, wallet: Wallet) -> list[FilterResult]:
        """W01/W02/W03 — Mutually exclusive age tiers."""
        if wallet.wallet_age_days is None:
            return []
        age = wallet.wallet_age_days
        if age < config.WALLET_AGE_VERY_NEW:
            return [FilterResult(**config.FILTER_W01)]
        if age < config.WALLET_AGE_NEW:
            return [FilterResult(**config.FILTER_W02)]
        if age < config.WALLET_AGE_RECENT:
            return [FilterResult(**config.FILTER_W03)]
        return []

    def _check_market_count(self, wallet: Wallet) -> list[FilterResult]:
        """W04/W05 — Mutually exclusive market count tiers."""
        if wallet.total_markets == 1:
            return [FilterResult(**config.FILTER_W04)]
        if wallet.total_markets <= 3:
            return [FilterResult(**config.FILTER_W05)]
        return []

    def _check_first_tx_pm(self, wallet: Wallet) -> list[FilterResult]:
        """W09 — First transaction is Polymarket."""
        if wallet.is_first_tx_pm:
            return [FilterResult(**config.FILTER_W09)]
        return []

    def _check_round_balance(self, wallet: Wallet) -> list[FilterResult]:
        """W11 — Balance is a round number ($5k/$10k/$50k ±1%)."""
        # TODO: Fetch live balance from chain client
        raise NotImplementedError

    def _check_origin(self, wallet: Wallet) -> list[FilterResult]:
        """O01/O02/O03 — Exchange origin and funding recency."""
        # TODO: Query wallet_funding table and evaluate
        raise NotImplementedError
