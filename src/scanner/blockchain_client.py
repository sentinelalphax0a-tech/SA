"""
Blockchain client via Alchemy (Polygon RPC).

Fetches on-chain data: wallet age, funding sources, token transfers.
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

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

# Rate-limit delay between Alchemy calls (seconds)
API_DELAY = 0.10
# Minimum transfer value to consider (skip dust)
MIN_TRANSFER_VALUE = 1.0
# Max pages to fetch per _fetch_transfers call
MAX_PAGES = 5


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

    # ── Internal helper ─────────────────────────────────────

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

            try:
                resp = self.w3.provider.make_request(
                    "alchemy_getAssetTransfers", [params]
                )
            except Exception as e:
                logger.error("alchemy_getAssetTransfers failed: %s", e)
                break

            result = resp.get("result") or {}
            transfers = result.get("transfers") or []
            all_transfers.extend(transfers)

            page_key = result.get("pageKey")
            if not page_key:
                break
            time.sleep(API_DELAY)

        return all_transfers

    # ── Public methods ──────────────────────────────────────

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
                raw = contract.functions.balanceOf(checksum).call()
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
