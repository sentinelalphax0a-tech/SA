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
from src.database.models import Wallet, WalletFunding, FilterResult, TradeEvent
from src.database.supabase_client import SupabaseClient
from src.scanner.blockchain_client import BlockchainClient

logger = logging.getLogger(__name__)


def _fr(filt: dict, details: str | None = None) -> FilterResult:
    """Build a FilterResult from a config filter dict."""
    return FilterResult(
        filter_id=filt["id"],
        filter_name=filt["name"],
        points=filt["points"],
        category=filt["category"],
        details=details,
    )


class WalletAnalyzer:
    """Evaluates wallet-level and origin filters."""

    def __init__(self, db: SupabaseClient, chain: BlockchainClient) -> None:
        self.db = db
        self.chain = chain

    # Minimum points from basic (non-funding) checks before we spend
    # API calls on funding source analysis.
    _MIN_BASIC_SCORE_FOR_FUNDING = 30

    def analyze(
        self, wallet_address: str, trades: list[TradeEvent]
    ) -> list[FilterResult]:
        """Run all W and O filters for a wallet.

        Two-phase approach to reduce API calls:
          Phase 1: Cheap checks (age, market count, first_tx, balance).
          Phase 2: Expensive funding check — only if phase 1 score >= 30.

        Args:
            wallet_address: Polygon address to analyze.
            trades: Recent trades made by this wallet.
        """
        # --- Phase 1: Cheap on-chain checks ---
        age_days = self.chain.get_wallet_age_days(wallet_address)
        is_first_pm = self.chain.is_first_tx_polymarket(wallet_address)
        balance = self.chain.get_balance(wallet_address)

        market_ids = {t.market_id for t in trades}

        wallet = Wallet(
            address=wallet_address,
            wallet_age_days=age_days,
            total_markets=len(market_ids),
            is_first_tx_pm=is_first_pm,
        )

        results: list[FilterResult] = []
        results.extend(self._check_wallet_age(wallet))
        results.extend(self._check_market_count(wallet))
        results.extend(self._check_first_tx_pm(wallet))
        results.extend(self._check_round_balance(wallet, balance))

        # --- Phase 2: Expensive funding check (only if basic score warrants it) ---
        basic_score = sum(f.points for f in results)
        funding: list[WalletFunding] = []

        if basic_score >= self._MIN_BASIC_SCORE_FOR_FUNDING:
            funding = self.chain.get_funding_sources(
                wallet_address, max_hops=config.MAX_FUNDING_HOPS
            )
            if funding:
                try:
                    self.db.insert_funding_batch(funding)
                except Exception as e:
                    logger.debug("insert_funding_batch failed: %s", e)

            results.extend(self._check_origin(wallet, funding))
            results.extend(self._check_mixer_funding(funding))

        # --- Persist wallet to DB ---
        try:
            self.db.upsert_wallet(wallet)
        except Exception as e:
            logger.error("upsert_wallet failed for %s: %s", wallet_address, e)

        return results

    # ── W01 / W02 / W03 — Wallet age (mutually exclusive) ──

    def _check_wallet_age(self, wallet: Wallet) -> list[FilterResult]:
        if wallet.wallet_age_days is None:
            return []
        age = wallet.wallet_age_days
        if age < config.WALLET_AGE_VERY_NEW:
            return [_fr(config.FILTER_W01, f"age={age}d")]
        if age < config.WALLET_AGE_NEW:
            return [_fr(config.FILTER_W02, f"age={age}d")]
        if age < config.WALLET_AGE_RECENT:
            return [_fr(config.FILTER_W03, f"age={age}d")]
        return []

    # ── W04 / W05 — Market count (mutually exclusive) ──────

    def _check_market_count(self, wallet: Wallet) -> list[FilterResult]:
        if wallet.total_markets == 1:
            return [_fr(config.FILTER_W04)]
        if 2 <= wallet.total_markets <= 3:
            return [_fr(config.FILTER_W05, f"markets={wallet.total_markets}")]
        return []

    # ── W09 — First tx = Polymarket ────────────────────────

    def _check_first_tx_pm(self, wallet: Wallet) -> list[FilterResult]:
        if wallet.is_first_tx_pm:
            return [_fr(config.FILTER_W09)]
        return []

    # ── W11 — Round balance ($5k / $10k / $50k ±1%) ───────

    def _check_round_balance(
        self, wallet: Wallet, balance: float | None = None
    ) -> list[FilterResult]:
        if balance is None:
            try:
                balance = self.chain.get_balance(wallet.address)
            except Exception:
                return []

        if balance <= 0:
            return []

        for target in config.ROUND_BALANCES:
            low = target * (1 - config.ROUND_BALANCE_TOLERANCE)
            high = target * (1 + config.ROUND_BALANCE_TOLERANCE)
            if low <= balance <= high:
                return [_fr(config.FILTER_W11, f"balance=${balance:,.0f}≈${target:,.0f}")]
        return []

    # ── COORD04 — Mixer/privacy protocol funding ──────────

    def _check_mixer_funding(
        self, funding: list[WalletFunding] | None
    ) -> list[FilterResult]:
        """COORD04 — Funded from Tornado Cash / Railgun."""
        if not funding:
            return []
        for f in funding:
            sender = f.sender_address.lower()
            mixer_name = config.MIXER_ADDRESSES.get(sender)
            if mixer_name:
                return [_fr(
                    config.FILTER_COORD04,
                    f"mixer={mixer_name} hop={f.hop_level}",
                )]
        return []

    # ── O01 / O02 / O03 — Origin & funding recency ────────

    def _check_origin(
        self, wallet: Wallet, funding: list[WalletFunding] | None = None
    ) -> list[FilterResult]:
        if funding is None:
            try:
                funding = self.chain.get_funding_sources(
                    wallet.address, max_hops=config.MAX_FUNDING_HOPS
                )
            except Exception:
                return []

        if not funding:
            return []

        results: list[FilterResult] = []
        now = datetime.now(timezone.utc)

        # O01: funded from a known exchange (any hop)
        for f in funding:
            if f.is_exchange:
                results.append(
                    _fr(config.FILTER_O01, f"exchange={f.exchange_name} hop={f.hop_level}")
                )
                break  # one is enough

        # O02 / O03: funding recency (mutually exclusive — take highest)
        most_recent_ts: datetime | None = None
        for f in funding:
            if f.timestamp is not None:
                ts = f.timestamp
                # Ensure timezone-aware
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if most_recent_ts is None or ts > most_recent_ts:
                    most_recent_ts = ts

        if most_recent_ts is not None:
            days_ago = (now - most_recent_ts).days
            if days_ago < config.FUNDING_VERY_RECENT_DAYS:
                results.append(_fr(config.FILTER_O03, f"funded {days_ago}d ago"))
            elif days_ago < config.FUNDING_RECENCY_DAYS:
                results.append(_fr(config.FILTER_O02, f"funded {days_ago}d ago"))

        return results
