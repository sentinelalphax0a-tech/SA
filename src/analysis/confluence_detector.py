"""
Confluence Detector — C filters.

Detects coordination patterns across multiple wallets:
  C01: Basic confluence (3+ wallets, same direction, 48h)
  C02: Strong confluence (5+ wallets)  — mutually exclusive with C01
  C03: Same funding intermediary (2+ wallets share sender)
  C04: C03 + same direction            — mutually exclusive with C03
  C05: Temporal funding (3+ funded from exchange < 4h + same direction)
  C06: Similar funding amounts (±30%)  — bonus, stacks
  C07: Distribution network (1 sender → 3+ wallets active in PM)
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from src import config
from src.database.models import FilterResult, FundingLink
from src.scanner.blockchain_client import POLYMARKET_CONTRACTS

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


class ConfluenceDetector:
    """Detects multi-wallet coordination patterns per market.

    ``wallets_with_scores`` passed to :meth:`detect` is a list of dicts,
    each containing at minimum::

        {
            "address":   str,           # wallet address
            "direction": str,           # "YES" or "NO"
        }

    Optional keys used when available::

        "score":        int,            # wallet score
        "total_amount": float,          # total traded
    """

    def __init__(self, db_client) -> None:
        self.db = db_client
        # Senders seen in the last detect() call — used by main.py for
        # cross-market super-sender tracking.
        self.last_senders_seen: set[str] = set()

    # ── Sender exclusion ─────────────────────────────────────

    @staticmethod
    def _build_default_excluded() -> set[str]:
        """Addresses that should ALWAYS be excluded from sender analysis.

        Includes Polymarket contracts and known exchanges — these are not
        insider intermediaries.
        """
        excluded = {a.lower() for a in POLYMARKET_CONTRACTS}
        excluded.update(a.lower() for a in config.KNOWN_EXCHANGES)
        return excluded

    # ── Main entry point ─────────────────────────────────────

    def detect(
        self,
        market_id: str,
        direction: str,
        wallets_with_scores: list[dict],
        excluded_senders: set[str] | None = None,
    ) -> list[FilterResult]:
        """Run all C filters for wallets active in a market.

        Args:
            market_id: The market being evaluated.
            direction: Consensus direction to check ("YES" or "NO").
            wallets_with_scores: List of wallet dicts active in this market.
            excluded_senders: Senders to exclude from C03/C04/C06/C07
                (super-senders detected across markets).

        Returns:
            List of triggered FilterResult objects.
        """
        if not wallets_with_scores:
            self.last_senders_seen = set()
            return []

        results: list[FilterResult] = []

        # C01 / C02 — direction confluence (mutually exclusive)
        results.extend(self._check_direction_confluence(direction, wallets_with_scores))

        # Fetch funding data from DB for all wallets
        funding_map = self._fetch_funding_map(wallets_with_scores)

        # Build raw sender → {funded wallet addresses} index
        raw_sender_to_wallets = self._build_sender_index(funding_map)

        # Expose raw senders for cross-market tracking
        self.last_senders_seen = set(raw_sender_to_wallets.keys())

        # Filter out Polymarket contracts, known exchanges, and super-senders
        all_excluded = self._build_default_excluded()
        if excluded_senders:
            all_excluded.update(excluded_senders)

        sender_to_wallets = {
            s: ws for s, ws in raw_sender_to_wallets.items()
            if s.lower() not in all_excluded
        }

        if sender_to_wallets != raw_sender_to_wallets:
            removed = len(raw_sender_to_wallets) - len(sender_to_wallets)
            logger.debug("Excluded %d senders from confluence analysis", removed)

        # C03 / C04 — shared funding source (mutually exclusive)
        results.extend(
            self._check_funding_confluence(direction, wallets_with_scores, sender_to_wallets, funding_map)
        )

        # C05 — temporal funding (exchange, < 4h, same direction)
        results.extend(
            self._check_temporal_funding(direction, wallets_with_scores, funding_map)
        )

        # C06 — similar funding amounts (bonus, stacks)
        results.extend(self._check_similar_amounts(sender_to_wallets, funding_map))

        # C07 — distribution network (1 → 3+)
        results.extend(self._check_distribution_network(wallets_with_scores, sender_to_wallets))

        return results

    # ── Public helper ────────────────────────────────────────

    def detect_funding_links(
        self, wallet_addresses: list[str]
    ) -> list[FundingLink]:
        """Cross-reference funding sources for a set of wallets.

        Builds a sender → [funded wallets] map and returns FundingLink
        objects where the sender funds 2+ wallets in the set.
        """
        # Build a minimal wallets_with_scores list for the internal helper
        wallets = [{"address": a, "direction": "unknown"} for a in wallet_addresses]
        funding_map = self._fetch_funding_map(wallets)
        sender_to_wallets = self._build_sender_index(funding_map)

        links: list[FundingLink] = []
        for sender, funded_addrs in sender_to_wallets.items():
            if len(funded_addrs) < 2:
                continue

            # Collect funding details
            funded_details = []
            amounts: list[float] = []
            timestamps: list[datetime] = []
            for addr in funded_addrs:
                for f in funding_map.get(addr, []):
                    if f.get("sender_address") == sender:
                        funded_details.append({"address": addr, "amount": f.get("amount")})
                        if f.get("amount") is not None:
                            amounts.append(f["amount"])
                        ts = self._parse_ts(f.get("timestamp"))
                        if ts is not None:
                            timestamps.append(ts)

            # Compute spread
            spread_hours = 0.0
            if len(timestamps) >= 2:
                spread_hours = (max(timestamps) - min(timestamps)).total_seconds() / 3600

            # Check similar amounts
            similar = _amounts_similar(amounts, config.FUNDING_SIMILAR_AMOUNT_TOLERANCE)

            links.append(FundingLink(
                sender=sender,
                funded_wallets=funded_details,
                count=len(funded_addrs),
                time_spread_hours=spread_hours,
                similar_amounts=similar,
                is_distribution=len(funded_addrs) >= config.DISTRIBUTION_MIN_WALLETS,
            ))

        return links

    # ── Internal: data fetching ──────────────────────────────

    def _fetch_funding_map(self, wallets: list[dict]) -> dict[str, list[dict]]:
        """Fetch funding sources for all wallets.

        Returns:
            {wallet_address: [funding_rows_from_db]}
        """
        funding_map: dict[str, list[dict]] = {}
        for w in wallets:
            addr = w["address"]
            try:
                rows = self.db.get_funding_sources(addr)
                if rows:
                    funding_map[addr] = rows
            except Exception as e:
                logger.debug("get_funding_sources failed for %s: %s", addr, e)
        return funding_map

    def _build_sender_index(
        self, funding_map: dict[str, list[dict]]
    ) -> dict[str, set[str]]:
        """Build sender_address → {funded wallet addresses} index."""
        sender_to_wallets: dict[str, set[str]] = defaultdict(set)
        for wallet_addr, fundings in funding_map.items():
            for f in fundings:
                sender = f.get("sender_address")
                if sender:
                    sender_to_wallets[sender].add(wallet_addr)
        return dict(sender_to_wallets)

    # ── C01 / C02 — Direction confluence ─────────────────────

    def _check_direction_confluence(
        self, direction: str, wallets: list[dict]
    ) -> list[FilterResult]:
        """C01/C02 — Multiple wallets betting same direction (mutually exclusive)."""
        same_dir = [w for w in wallets if w.get("direction") == direction]
        count = len(same_dir)

        if count >= config.CONFLUENCE_STRONG_MIN_WALLETS:
            return [_fr(config.FILTER_C02, f"{count} wallets → {direction}")]
        if count >= config.CONFLUENCE_BASIC_MIN_WALLETS:
            return [_fr(config.FILTER_C01, f"{count} wallets → {direction}")]
        return []

    # ── C03 / C04 — Shared funding source ────────────────────

    def _check_funding_confluence(
        self,
        direction: str,
        wallets: list[dict],
        sender_to_wallets: dict[str, set[str]],
        funding_map: dict[str, list[dict]],
    ) -> list[FilterResult]:
        """C03/C04 — 2+ wallets share a funding sender (mutually exclusive).

        C04 upgrades C03 when all shared-sender wallets bet in the same direction.
        """
        wallet_dir = {w["address"]: w.get("direction") for w in wallets}

        for sender, funded_addrs in sender_to_wallets.items():
            if len(funded_addrs) < config.FUNDING_CONFLUENCE_MIN_WALLETS:
                continue

            # Check if all funded wallets bet the same direction
            all_same_dir = all(
                wallet_dir.get(a) == direction
                for a in funded_addrs
                if a in wallet_dir
            )

            if all_same_dir:
                return [_fr(
                    config.FILTER_C04,
                    f"sender={sender[:10]}…, {len(funded_addrs)} wallets, dir={direction}",
                )]
            else:
                return [_fr(
                    config.FILTER_C03,
                    f"sender={sender[:10]}…, {len(funded_addrs)} wallets",
                )]

        return []

    # ── C05 — Temporal funding ───────────────────────────────

    def _check_temporal_funding(
        self,
        direction: str,
        wallets: list[dict],
        funding_map: dict[str, list[dict]],
    ) -> list[FilterResult]:
        """C05 — 3+ wallets funded from exchange within 4h, same direction."""
        wallet_dir = {w["address"]: w.get("direction") for w in wallets}
        window = timedelta(hours=config.FUNDING_TEMPORAL_HOURS)

        # Collect exchange-funded wallets with timestamps
        exchange_funded: list[tuple[str, datetime]] = []
        for wallet_addr, fundings in funding_map.items():
            if wallet_dir.get(wallet_addr) != direction:
                continue
            for f in fundings:
                if not f.get("is_exchange"):
                    continue
                ts = self._parse_ts(f.get("timestamp"))
                if ts is not None:
                    exchange_funded.append((wallet_addr, ts))
                    break  # one exchange funding per wallet is enough

        if len(exchange_funded) < config.FUNDING_TEMPORAL_MIN_WALLETS:
            return []

        # Sort by timestamp and check sliding window
        exchange_funded.sort(key=lambda x: x[1])

        for i in range(len(exchange_funded)):
            cluster_addrs = {exchange_funded[i][0]}
            for j in range(i + 1, len(exchange_funded)):
                if exchange_funded[j][1] - exchange_funded[i][1] <= window:
                    cluster_addrs.add(exchange_funded[j][0])
                else:
                    break
            if len(cluster_addrs) >= config.FUNDING_TEMPORAL_MIN_WALLETS:
                return [_fr(
                    config.FILTER_C05,
                    f"{len(cluster_addrs)} wallets funded <{config.FUNDING_TEMPORAL_HOURS}h, dir={direction}",
                )]

        return []

    # ── C06 — Similar funding amounts (bonus) ────────────────

    def _check_similar_amounts(
        self,
        sender_to_wallets: dict[str, set[str]],
        funding_map: dict[str, list[dict]],
    ) -> list[FilterResult]:
        """C06 — Wallets sharing a sender were funded with amounts ±30%.

        This is a bonus filter that stacks on top of C03/C04/C07.
        """
        for sender, funded_addrs in sender_to_wallets.items():
            if len(funded_addrs) < config.FUNDING_CONFLUENCE_MIN_WALLETS:
                continue

            # Collect the funding amounts from this sender
            amounts: list[float] = []
            for addr in funded_addrs:
                for f in funding_map.get(addr, []):
                    if f.get("sender_address") == sender and f.get("amount") is not None:
                        amounts.append(f["amount"])

            if len(amounts) < 2:
                continue

            if _amounts_similar(amounts, config.FUNDING_SIMILAR_AMOUNT_TOLERANCE):
                median = sorted(amounts)[len(amounts) // 2]
                return [_fr(
                    config.FILTER_C06,
                    f"sender={sender[:10]}…, amounts≈${median:,.0f}±30%",
                )]

        return []

    # ── C07 — Distribution network ───────────────────────────

    def _check_distribution_network(
        self,
        wallets: list[dict],
        sender_to_wallets: dict[str, set[str]],
    ) -> list[FilterResult]:
        """C07 — One sender funded 3+ wallets active in this market."""
        active_addrs = {w["address"] for w in wallets}

        for sender, funded_addrs in sender_to_wallets.items():
            active_funded = funded_addrs & active_addrs
            if len(active_funded) >= config.DISTRIBUTION_MIN_WALLETS:
                return [_fr(
                    config.FILTER_C07,
                    f"distributor={sender[:10]}…, {len(active_funded)} wallets funded",
                )]

        return []

    # ── Timestamp helper ─────────────────────────────────────

    @staticmethod
    def _parse_ts(raw) -> datetime | None:
        """Parse a timestamp from a DB row (str or datetime)."""
        if raw is None:
            return None
        if isinstance(raw, datetime):
            if raw.tzinfo is None:
                return raw.replace(tzinfo=timezone.utc)
            return raw
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                return None
        return None


# ── Module-level helpers ─────────────────────────────────────


def _amounts_similar(amounts: list[float], tolerance: float) -> bool:
    """Check if all amounts are within ±tolerance of the median."""
    if len(amounts) < 2:
        return False
    median = sorted(amounts)[len(amounts) // 2]
    if median <= 0:
        return False
    return all(
        abs(a - median) / median <= tolerance
        for a in amounts
    )
