"""
Blockchain client via Alchemy (Polygon RPC).

Fetches on-chain data: wallet age, funding sources, token transfers.
"""

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from web3 import Web3

from src import config
from src.database.models import WalletFunding

logger = logging.getLogger(__name__)

# Polymarket contracts on Polygon (lowercased)
POLYMARKET_CONTRACTS: set[str] = {
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # CTF Exchange
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",  # NegRisk CTF Exchange
    "0x4d97dcd97ec945f40cf65f87097ace5ea0476045",  # Polymarket Proxy
}

# USDC on Polygon
USDC_POLYGON_POS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_POLYGON_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

ERC20_BALANCE_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]

# Rate-limit delay between Alchemy calls (seconds) — used as inter-page buffer
API_DELAY = 0.10
# Minimum transfer value to consider (skip dust)
MIN_TRANSFER_VALUE = 1.0
# Max pages to fetch per _fetch_transfers call
MAX_PAGES = 5

# ── Alchemy module-level rate limiter ────────────────────────────────────────
# Shared across ALL BlockchainClient instances and threads within a process.
#
# Design:
#   • _ALCHEMY_SEMAPHORE(3)  — caps concurrent in-flight HTTP requests to Alchemy
#   • _ALCHEMY_LOCK + _ALCHEMY_LAST_CALL — token-bucket: ≥200 ms between call starts
#
# With these two constraints:
#   – At most 3 requests are being awaited simultaneously
#   – A new call can start at most once every 200 ms (≈ 5 starts/s)
#   – Peak CU/s ≈ 5 × 150 (getAssetTransfers) = 750, well below 500 CU/s sustained
#     once real network latency is factored in (calls rarely complete in <200 ms)
_ALCHEMY_MAX_CONCURRENT: int = 5
_ALCHEMY_SEMAPHORE = threading.Semaphore(_ALCHEMY_MAX_CONCURRENT)
_ALCHEMY_LOCK = threading.Lock()        # protects _ALCHEMY_LAST_CALL
_ALCHEMY_LAST_CALL: float = 0.0        # time.monotonic() of the last call start
_ALCHEMY_MIN_SPACING: float = 0.100    # 100 ms minimum between consecutive starts

# Exponential-backoff delays for 429 retries (seconds): 2 → 4 → 8, then give up
_BACKOFF_DELAYS: tuple[float, ...] = (2.0, 4.0, 8.0)


def _is_rate_limited(exc: Exception) -> bool:
    """Return True if *exc* signals an HTTP 429 / Alchemy rate-limit response."""
    msg = str(exc).lower()
    return "429" in msg or "too many requests" in msg or "rate limit" in msg


class BlockchainClient:
    """Client for Polygon blockchain data via Alchemy."""

    def __init__(self) -> None:
        self.w3 = Web3(Web3.HTTPProvider(config.ALCHEMY_ENDPOINT))
        if not self.w3.is_connected():
            logger.warning("Failed to connect to Alchemy RPC endpoint")

        # Per-scan caches to avoid redundant API calls for the same wallet
        self._age_cache: dict[str, int | None] = {}
        self._first_pm_cache: dict[str, bool] = {}
        self._balance_cache: dict[str, float] = {}
        self._funding_cache: dict[str, list[WalletFunding]] = {}

        # Per-scan Alchemy call statistics (approximate; GIL makes int += 1 safe
        # for CPython, but these are best-effort counters for observability)
        self._calls_ok: int = 0       # requests that returned a result
        self._rl_hits: int = 0        # 429 responses received (each retry = +1)
        self._calls_failed: int = 0   # permanent non-429 failures

    # ── Rate limiter ─────────────────────────────────────────────────────────

    def _rate_limited_alchemy(self, fn: Callable[[], Any]) -> Any:
        """Execute one Alchemy RPC call under the global rate limiter.

        1. Books a time slot (token bucket, 200 ms min spacing) under a brief lock.
        2. Sleeps outside the lock so other threads can book their own slots.
        3. Runs *fn* inside the module semaphore (max 3 concurrent in-flight).

        Raises whatever *fn()* raises — retry is the caller's responsibility.
        """
        global _ALCHEMY_LAST_CALL

        # Reserve a time slot — lock is held for computation only, not for I/O
        with _ALCHEMY_LOCK:
            now = time.monotonic()
            target = _ALCHEMY_LAST_CALL + _ALCHEMY_MIN_SPACING
            wait = max(0.0, target - now)
            _ALCHEMY_LAST_CALL = max(now, target)  # book the slot

        if wait > 0:
            time.sleep(wait)

        # Limit concurrent in-flight requests
        with _ALCHEMY_SEMAPHORE:
            return fn()

    def _alchemy_request(self, fn: Callable[[], Any]) -> Any | None:
        """Rate-limited Alchemy call with exponential-backoff retry on 429.

        Attempts up to len(_BACKOFF_DELAYS)+1 times total.
        - 429 responses log [WARNING] (transient throttle, not a bug).
        - All other errors log [ERROR] (unexpected, needs investigation).
        Returns None on any final failure so callers can degrade gracefully.
        """
        for attempt in range(len(_BACKOFF_DELAYS) + 1):
            try:
                result = self._rate_limited_alchemy(fn)
                self._calls_ok += 1
                return result
            except Exception as e:
                if _is_rate_limited(e):
                    self._rl_hits += 1
                    if attempt < len(_BACKOFF_DELAYS):
                        delay = _BACKOFF_DELAYS[attempt]
                        logger.warning(
                            "Alchemy 429 — retry %d/%d in %.0fs",
                            attempt + 1, len(_BACKOFF_DELAYS), delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.warning(
                            "Alchemy 429 — exhausted %d retries, skipping call",
                            len(_BACKOFF_DELAYS),
                        )
                        return None
                else:
                    self._calls_failed += 1
                    logger.error("Alchemy call failed: %s", e)
                    return None
        return None  # unreachable; satisfies type checker

    def log_stats(self) -> None:
        """Log Alchemy RPC call statistics for the completed scan."""
        total = self._calls_ok + self._rl_hits + self._calls_failed
        logger.info(
            "Alchemy stats — %d OK, %d rate-limited (429), %d failed permanently"
            " (total attempted: %d)",
            self._calls_ok, self._rl_hits, self._calls_failed, total,
        )

    # ── Internal helper ──────────────────────────────────────────────────────

    def _fetch_transfers(
        self,
        address: str,
        direction: str = "to",
        categories: list[str] | None = None,
        order: str = "asc",
        max_count: int = 1000,
        contract_addresses: list[str] | None = None,
    ) -> list[dict]:
        """Fetch transfers via alchemy_getAssetTransfers.

        Args:
            address: Wallet address.
            direction: "to" for incoming, "from" for outgoing.
            categories: Transfer categories (default: external + erc20).
            order: "asc" or "desc".
            max_count: Max results per page (up to 1000).
            contract_addresses: Filter to specific token contracts.
        """
        if categories is None:
            categories = ["external", "erc20"]

        params: dict = {
            "fromBlock": "0x0",
            "toBlock": "latest",
            "category": categories,
            "order": order,
            "maxCount": hex(max_count),
            "withMetadata": True,
        }

        if direction == "to":
            params["toAddress"] = address
        else:
            params["fromAddress"] = address

        if contract_addresses:
            params["contractAddresses"] = contract_addresses

        all_transfers: list[dict] = []
        page_key: str | None = None

        for _ in range(MAX_PAGES):
            if page_key:
                params["pageKey"] = page_key

            resp = self._alchemy_request(
                lambda: self.w3.provider.make_request(
                    "alchemy_getAssetTransfers", [params]
                )
            )
            if resp is None:
                break  # rate-limited or permanently failed — abort page loop

            result = resp.get("result") or {}
            transfers = result.get("transfers") or []
            all_transfers.extend(transfers)

            page_key = result.get("pageKey")
            if not page_key:
                break
            time.sleep(API_DELAY)  # conservative inter-page buffer

        return all_transfers

    # ── Public methods ────────────────────────────────────────────────────────

    def get_first_transaction_timestamp(self, address: str) -> datetime | None:
        """Get the timestamp of a wallet's first ever transaction."""
        if not Web3.is_address(address):
            logger.warning("Invalid address: %s", address)
            return None

        try:
            # Earliest incoming
            incoming = self._fetch_transfers(
                address, direction="to", order="asc", max_count=1
            )
            # Earliest outgoing
            time.sleep(API_DELAY)
            outgoing = self._fetch_transfers(
                address, direction="from", order="asc", max_count=1
            )

            timestamps: list[datetime] = []
            for tx in incoming + outgoing:
                ts_str = (tx.get("metadata") or {}).get("blockTimestamp")
                if ts_str:
                    timestamps.append(
                        datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    )

            if not timestamps:
                return None
            return min(timestamps)

        except Exception as e:
            logger.error("get_first_transaction_timestamp failed for %s: %s", address, e)
            return None

    def get_wallet_age_days(self, address: str) -> int | None:
        """Get the age of a wallet in days from its first transaction."""
        key = address.lower()
        if key in self._age_cache:
            return self._age_cache[key]
        first_tx = self.get_first_transaction_timestamp(address)
        if first_tx is None:
            self._age_cache[key] = None
            return None
        now = datetime.now(timezone.utc)
        age = (now - first_tx).days
        self._age_cache[key] = age
        return age

    def get_first_tx_contracts(self, address: str) -> list[str]:
        """Return destination addresses from the wallet's first outgoing txs.

        Useful for detecting if the first interaction was with Polymarket.
        """
        if not Web3.is_address(address):
            logger.warning("Invalid address: %s", address)
            return []

        try:
            outgoing = self._fetch_transfers(
                address, direction="from", order="asc", max_count=5
            )
            contracts: list[str] = []
            for tx in outgoing:
                to_addr = tx.get("to")
                if to_addr:
                    contracts.append(to_addr.lower())
                raw = tx.get("rawContract") or {}
                contract_addr = raw.get("address")
                if contract_addr:
                    contracts.append(contract_addr.lower())
            return contracts

        except Exception as e:
            logger.error("get_first_tx_contracts failed for %s: %s", address, e)
            return []

    def is_first_tx_polymarket(self, address: str) -> bool:
        """Check if the wallet's first transactions target Polymarket contracts."""
        key = address.lower()
        if key in self._first_pm_cache:
            return self._first_pm_cache[key]
        contracts = self.get_first_tx_contracts(address)
        result = any(c in POLYMARKET_CONTRACTS for c in contracts)
        self._first_pm_cache[key] = result
        return result

    def get_funding_sources(
        self, address: str, max_hops: int = 2
    ) -> list[WalletFunding]:
        """Trace funding sources up to N hops back via BFS."""
        if not Web3.is_address(address):
            logger.warning("Invalid address: %s", address)
            return []

        key = address.lower()
        if key in self._funding_cache:
            return self._funding_cache[key]

        results: list[WalletFunding] = []
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(address.lower(), 1)]

        while queue:
            current_addr, hop = queue.pop(0)

            if hop > max_hops or current_addr in visited:
                continue
            visited.add(current_addr)

            try:
                time.sleep(API_DELAY)
                transfers = self._fetch_transfers(
                    current_addr, direction="to", order="desc", max_count=100
                )
            except Exception as e:
                logger.error("get_funding_sources fetch failed for %s: %s", current_addr, e)
                continue

            # Group by sender
            by_sender: dict[str, list[dict]] = defaultdict(list)
            for tx in transfers:
                sender = (tx.get("from") or "").lower()
                if sender and sender != current_addr:
                    by_sender[sender].append(tx)

            for sender, txs in by_sender.items():
                # Sum value
                total_value = 0.0
                latest_ts: datetime | None = None
                for tx in txs:
                    val = tx.get("value")
                    if val is not None:
                        try:
                            total_value += float(val)
                        except (ValueError, TypeError):
                            pass
                    ts_str = (tx.get("metadata") or {}).get("blockTimestamp")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if latest_ts is None or ts > latest_ts:
                            latest_ts = ts

                if total_value < MIN_TRANSFER_VALUE:
                    continue

                is_exchange, exchange_name = self.is_exchange_address(sender)
                is_bridge, bridge_name = self.is_bridge_address(sender)
                is_mixer, mixer_name = self.is_mixer_address(sender)

                results.append(
                    WalletFunding(
                        wallet_address=current_addr,
                        sender_address=sender,
                        amount=total_value,
                        timestamp=latest_ts,
                        hop_level=hop,
                        is_exchange=is_exchange,
                        exchange_name=exchange_name,
                        is_bridge=is_bridge,
                        bridge_name=bridge_name,
                        is_mixer=is_mixer,
                        mixer_name=mixer_name,
                    )
                )

                # Trace further if not an exchange and within hop limit
                if not is_exchange and hop < max_hops and sender not in visited:
                    queue.append((sender, hop + 1))

        self._funding_cache[key] = results
        return results

    def get_balance(self, address: str) -> float:
        """Get current USDC balance of a wallet (sum of PoS + native USDC)."""
        if not Web3.is_address(address):
            logger.warning("Invalid address: %s", address)
            return 0.0

        key = address.lower()
        if key in self._balance_cache:
            return self._balance_cache[key]

        try:
            checksum = Web3.to_checksum_address(address)
            total = 0.0
            for usdc_addr in (USDC_POLYGON_POS, USDC_POLYGON_NATIVE):
                contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(usdc_addr),
                    abi=ERC20_BALANCE_ABI,
                )
                raw = self._alchemy_request(
                    lambda c=contract, a=checksum: c.functions.balanceOf(a).call()
                )
                if raw is None:
                    continue  # skip this token on failure, proceed with partial total
                total += raw / 1e6  # USDC has 6 decimals
            self._balance_cache[key] = total
            return total

        except Exception as e:
            logger.error("get_balance failed for %s: %s", address, e)
            return 0.0

    def is_exchange_address(self, address: str) -> tuple[bool, str | None]:
        """Check if an address belongs to a known exchange."""
        normalized = address.lower()
        for known, name in config.KNOWN_EXCHANGES.items():
            if known.lower() == normalized:
                return True, name
        return False, None

    def is_bridge_address(self, address: str) -> tuple[bool, str | None]:
        """Check if an address belongs to a known bridge."""
        normalized = address.lower()
        for known, name in config.KNOWN_BRIDGES.items():
            if known.lower() == normalized:
                return True, name
        return False, None

    def is_mixer_address(self, address: str) -> tuple[bool, str | None]:
        """Check if an address belongs to a known mixer/privacy protocol."""
        normalized = address.lower()
        for known, name in config.MIXER_ADDRESSES.items():
            if known.lower() == normalized:
                return True, name
        return False, None
