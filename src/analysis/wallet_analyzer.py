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

# How long a cached wallet_age_days / is_first_tx_pm stays valid (days).
# is_first_tx_pm is immutable so could be infinite, but 7d keeps the cache warm
# while ensuring age estimates stay within ±7 days of reality.
_WALLET_CACHE_TTL_DAYS = 7

# How long wallet_funding DB rows are considered fresh for Phase 2.
# Funding sources rarely change — 14 days balances freshness vs Alchemy savings.
_FUNDING_CACHE_TTL_DAYS = 14


def _db_rows_to_wallet_funding(rows: list[dict]) -> list[WalletFunding]:
    """Convert wallet_funding DB rows to WalletFunding dataclass objects."""
    result = []
    for r in rows:
        ts = None
        if r.get("timestamp"):
            try:
                ts = _parse_dt(r["timestamp"])
            except Exception:
                pass
        result.append(WalletFunding(
            wallet_address=r["wallet_address"],
            sender_address=r["sender_address"],
            id=r.get("id"),
            amount=r.get("amount"),
            timestamp=ts,
            hop_level=r.get("hop_level", 1),
            is_exchange=bool(r.get("is_exchange", False)),
            exchange_name=r.get("exchange_name"),
            is_bridge=bool(r.get("is_bridge", False)),
            bridge_name=r.get("bridge_name"),
            is_mixer=bool(r.get("is_mixer", False)),
            mixer_name=r.get("mixer_name"),
        ))
    return result


def _parse_dt(value: str | datetime) -> datetime:
    """Return a timezone-aware datetime from a Supabase timestamp string or datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


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

    def __init__(
        self,
        db: SupabaseClient,
        chain: BlockchainClient,
        pm_client=None,
        max_hops: int = config.MAX_FUNDING_HOPS,
    ) -> None:
        self.db = db
        self.chain = chain
        self.pm_client = pm_client
        self.max_hops = max_hops

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
        # --- Phase 1: DB cache lookup (avoids Alchemy for known wallets) ---
        # wallet_age_days and is_first_tx_pm are already persisted by upsert_wallet()
        # at the end of every analyze() call.  Re-reading them here skips the
        # two most expensive Alchemy calls (3 getAssetTransfers = 450 CU) for
        # wallets already analysed within the last _WALLET_CACHE_TTL_DAYS days.
        cached_age: int | None = None          # set iff DB hit + age not NULL
        cached_first_pm: bool | None = None    # set iff DB hit (immutable flag)
        try:
            row = self.db.get_wallet(wallet_address)
            if row:
                updated_raw = row.get("updated_at")
                if updated_raw:
                    updated_at = _parse_dt(updated_raw)
                    days_since_update = (datetime.now(timezone.utc) - updated_at).days
                    if days_since_update < _WALLET_CACHE_TTL_DAYS:
                        # Age: compensate for elapsed days since last DB write
                        db_age = row.get("wallet_age_days")
                        if db_age is not None:
                            cached_age = int(db_age) + days_since_update
                        # is_first_tx_pm is immutable — always trust the cached value
                        cached_first_pm = bool(row.get("is_first_tx_pm", False))
        except Exception as e:
            logger.debug("wallet cache lookup failed for %s: %s", wallet_address[:10], e)

        # --- Phase 1: Cheap on-chain checks (skip if cached) ---
        age_days = (
            cached_age
            if cached_age is not None
            else self.chain.get_wallet_age_days(wallet_address)
        )
        is_first_pm = (
            cached_first_pm
            if cached_first_pm is not None
            else self.chain.is_first_tx_polymarket(wallet_address)
        )
        balance = self.chain.get_balance(wallet_address)  # always live — balances change

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
            funding = self._get_funding_with_db_cache(wallet_address)
            results.extend(self._check_origin(wallet, funding))
            results.extend(self._check_mixer_funding(funding))

        # --- Persist wallet to DB ---
        try:
            self.db.upsert_wallet(wallet)
        except Exception as e:
            logger.error("upsert_wallet failed for %s: %s", wallet_address, e)

        return results

    # ── Funding cache (DB-first, Alchemy fallback) ─────────

    def _get_funding_with_db_cache(self, wallet_address: str) -> list[WalletFunding]:
        """Return funding sources for a wallet, using DB cache when fresh.

        Checks wallet_funding DB first. If rows exist and the most recent
        created_at is within _FUNDING_CACHE_TTL_DAYS (14d), returns those rows
        as WalletFunding objects without calling Alchemy.

        Falls back to Alchemy (chain.get_funding_sources) on cache miss,
        stale data, or DB errors. Persists Alchemy results to DB.
        """
        try:
            db_rows = self.db.get_funding_sources(wallet_address)
            if db_rows:
                most_recent = max(
                    (_parse_dt(r["created_at"]) for r in db_rows if r.get("created_at")),
                    default=None,
                )
                if most_recent is not None:
                    age_days = (datetime.now(timezone.utc) - most_recent).days
                    if age_days < _FUNDING_CACHE_TTL_DAYS:
                        logger.info(
                            "Funding cache HIT for %s (%d rows, age %dd)",
                            wallet_address[:10], len(db_rows), age_days,
                        )
                        return _db_rows_to_wallet_funding(db_rows)
        except Exception as e:
            logger.debug("Funding DB cache lookup failed for %s: %s", wallet_address[:10], e)

        # Cache miss or stale — fetch from Alchemy
        logger.info(
            "Funding cache MISS for %s — fetching from Alchemy",
            wallet_address[:10],
        )
        funding = self.chain.get_funding_sources(wallet_address, max_hops=self.max_hops)
        if funding:
            try:
                self.db.insert_funding_batch(funding)
            except Exception as e:
                logger.debug("insert_funding_batch failed: %s", e)
        return funding

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
            # Check real PM history before firing W04
            if self.pm_client is not None:
                try:
                    history = self.pm_client.get_wallet_pm_history_cached(wallet.address)
                    if history is not None:
                        real_markets = history.get("distinct_markets", 0)
                        if real_markets > config.W04_SUPPRESS_MARKETS:
                            logger.info(
                                "W04 suppressed for %s: real distinct_markets=%d",
                                wallet.address[:10], real_markets,
                            )
                            return []
                except Exception as e:
                    logger.debug("W04 PM history check failed for %s: %s", wallet.address[:10], e)
            return [_fr(config.FILTER_W04)]
        if 2 <= wallet.total_markets <= 3:
            # Check real PM history before firing W05
            if self.pm_client is not None:
                try:
                    history = self.pm_client.get_wallet_pm_history_cached(wallet.address)
                    if history is not None:
                        real_markets = history.get("distinct_markets", 0)
                        if real_markets > config.W05_SUPPRESS_MARKETS:
                            logger.info(
                                "W05 suppressed for %s: real distinct_markets=%d",
                                wallet.address[:10], real_markets,
                            )
                            return []
                except Exception as e:
                    logger.debug("W05 PM history check failed for %s: %s", wallet.address[:10], e)
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
                    wallet.address, max_hops=self.max_hops
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
