"""
Confluence Detector — C filters (layered architecture).

4 additive layers:
  Layer 1 — Direction:
    C01: Basic confluence (3+ wallets same direction)  (+10)
    C02: Strong confluence (5+ wallets)                (+15)  — mutually exclusive with C01

  Layer 2 — Origin type (each fires independently, additive):
    C03a: Shared exchange origin   (2+ wallets from same exchange)  (+5)
    C03b: Shared bridge origin     (2+ wallets from same bridge)    (+20)
    C03c: Shared mixer origin      (2+ wallets from same mixer)     (+30)
    C03d: Same direct parent       (2+ wallets from same non-exchange/bridge/mixer sender)  (+30)

  Layer 3 — Bonus (additive, stacks):
    C05: Temporal funding (3+ funded from exchange < 4h, same dir)  (+10)
    C06: Similar funding amounts (±30%)                            (+10)

  Layer 4 — Distribution network:
    C07: 1 sender → 3+ wallets active in market  (+30)
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from src import config
from src.config import KNOWN_INFRASTRUCTURE
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
        # Cached exclusion set — populated once per scan via
        # refresh_excluded_senders(), then reused for every market.
        self._excluded_cache: set[str] | None = None

    # ── Sender exclusion ─────────────────────────────────────

    def refresh_excluded_senders(self) -> None:
        """Query DB for high-fanout senders and rebuild the exclusion cache.

        Call this **once** at the start of each scan cycle.  Individual
        :meth:`detect` calls will reuse the cached set without hitting
        the database again.
        """
        excluded = {a.lower() for a in POLYMARKET_CONTRACTS}
        excluded |= {a.lower() for a in KNOWN_INFRASTRUCTURE}

        # Auto-exclude any sender funding > N distinct wallets
        threshold = config.SENDER_AUTO_EXCLUDE_MIN_WALLETS
        try:
            high_fanout = self.db.get_high_fanout_senders(threshold)
            if high_fanout:
                auto = {a.lower() for a in high_fanout}
                new_auto = auto - excluded
                if new_auto:
                    logger.info(
                        "Auto-excluded %d high-fanout senders (>%d wallets): %s",
                        len(new_auto),
                        threshold,
                        ", ".join(s[:10] + "…" for s in sorted(new_auto)),
                    )
                excluded |= auto
        except Exception as exc:
            logger.warning("Failed to query high-fanout senders: %s", exc)

        self._excluded_cache = excluded

    def _build_default_excluded(self) -> set[str]:
        """Return the exclusion set, building it on first use if needed.

        Polymarket contracts and known infrastructure addresses (relay
        solvers, wrapped-collateral contracts, high-fanout routers) are
        excluded because they fund hundreds of unrelated wallets and
        generate false C03d / C07 confluences.

        Senders that fund >= SENDER_AUTO_EXCLUDE_MIN_WALLETS distinct
        wallets in wallet_funding are also excluded automatically.

        Exchanges, bridges, and mixers are intentionally kept because
        their shared use IS the confluence signal detected by Layer 2
        (C03a-c).
        """
        if self._excluded_cache is None:
            self.refresh_excluded_senders()
        return set(self._excluded_cache)  # return a copy

    # ── Main entry point ─────────────────────────────────────

    def detect(
        self,
        market_id: str,
        direction: str,
        wallets_with_scores: list[dict],
        excluded_senders: set[str] | None = None,
    ) -> list[FilterResult]:
        """Run all C filters for wallets active in a market.

        Layers are additive — each layer fires independently.

        Args:
            market_id: The market being evaluated.
            direction: Consensus direction to check ("YES" or "NO").
            wallets_with_scores: List of wallet dicts active in this market.
            excluded_senders: Senders to exclude (super-senders detected
                across markets).

        Returns:
            List of triggered FilterResult objects.
        """
        if not wallets_with_scores:
            self.last_senders_seen = set()
            return []

        results: list[FilterResult] = []

        # ── Layer 1: Direction confluence (C01/C02, mutually exclusive) ──
        results.extend(self._check_direction_confluence(direction, wallets_with_scores))

        # Fetch funding data from DB for all wallets
        funding_map = self._fetch_funding_map(wallets_with_scores)

        # Build raw sender → {funded wallet addresses} index
        raw_sender_to_wallets = self._build_sender_index(funding_map)

        # Expose raw senders for cross-market tracking
        self.last_senders_seen = set(raw_sender_to_wallets.keys())

        # Filter out infrastructure, Polymarket contracts, and super-senders
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

        # ── Layer 2: Origin type (C03a-d, additive) ─────────────
        results.extend(
            self._check_origin_layers(wallets_with_scores, sender_to_wallets, funding_map)
        )

        # ── Layer 3: Bonus (C05/C06, additive) ──────────────────
        results.extend(
            self._check_temporal_funding(direction, wallets_with_scores, funding_map)
        )
        results.extend(self._check_similar_amounts(sender_to_wallets, funding_map))

        # ── Layer 4: Distribution network (C07) ─────────────────
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

    # ── Layer 1: C01 / C02 — Direction confluence ─────────────

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

    # ── Layer 2: C03a-d — Origin type (additive) ──────────────

    def _check_origin_layers(
        self,
        wallets: list[dict],
        sender_to_wallets: dict[str, set[str]],
        funding_map: dict[str, list[dict]],
    ) -> list[FilterResult]:
        """C03a-d — Classify shared senders by type.

        Each origin type fires independently (additive, not mutually exclusive):
          C03a: shared exchange   (+5)
          C03b: shared bridge     (+20)
          C03c: shared mixer      (+30)
          C03d: same direct parent — non-exchange/bridge/mixer  (+30)
        """
        results: list[FilterResult] = []
        fired_types: set[str] = set()  # track which types already fired

        for sender, funded_addrs in sender_to_wallets.items():
            if len(funded_addrs) < config.FUNDING_CONFLUENCE_MIN_WALLETS:
                continue

            # Classify this sender
            sender_type = self._classify_sender(sender, funding_map)

            # Only fire each type once (strongest example wins)
            if sender_type in fired_types:
                continue

            detail = f"sender={sender[:10]}…, {len(funded_addrs)} wallets"

            if sender_type == "exchange":
                exchange_name = self._get_sender_label(sender, funding_map, "exchange_name")
                results.append(_fr(
                    config.FILTER_C03A,
                    f"{detail}, exchange={exchange_name}",
                ))
                fired_types.add("exchange")

            elif sender_type == "bridge":
                bridge_name = self._get_sender_label(sender, funding_map, "bridge_name")
                results.append(_fr(
                    config.FILTER_C03B,
                    f"{detail}, bridge={bridge_name}",
                ))
                fired_types.add("bridge")

            elif sender_type == "mixer":
                mixer_name = self._get_sender_label(sender, funding_map, "mixer_name")
                results.append(_fr(
                    config.FILTER_C03C,
                    f"{detail}, mixer={mixer_name}",
                ))
                fired_types.add("mixer")

            else:
                # Direct parent (padre directo) — unknown intermediary
                results.append(_fr(config.FILTER_C03D, detail))
                fired_types.add("padre")

        return results

    def _classify_sender(
        self, sender: str, funding_map: dict[str, list[dict]]
    ) -> str:
        """Classify a sender address by its type using funding row metadata."""
        for fundings in funding_map.values():
            for f in fundings:
                if f.get("sender_address") != sender:
                    continue
                if f.get("is_mixer"):
                    return "mixer"
                if f.get("is_bridge"):
                    return "bridge"
                if f.get("is_exchange"):
                    return "exchange"
        return "padre"

    @staticmethod
    def _get_sender_label(
        sender: str,
        funding_map: dict[str, list[dict]],
        label_key: str,
    ) -> str:
        """Extract a human-readable label for a sender from funding data."""
        for fundings in funding_map.values():
            for f in fundings:
                if f.get("sender_address") == sender:
                    label = f.get(label_key)
                    if label:
                        return label
        return sender[:10] + "…"

    # ── Layer 3: C05 — Temporal funding ───────────────────────

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

    # ── Layer 3: C06 — Similar funding amounts (bonus) ────────

    def _check_similar_amounts(
        self,
        sender_to_wallets: dict[str, set[str]],
        funding_map: dict[str, list[dict]],
    ) -> list[FilterResult]:
        """C06 — Wallets sharing a sender were funded with amounts ±30%."""
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

    # ── Layer 4: C07 — Distribution network ───────────────────

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

    # ── Union-find grouping ──────────────────────────────────

    @staticmethod
    def _union_find_groups(
        wallets: list[dict],
        sender_to_wallets: dict[str, set[str]],
    ) -> list[list[dict]]:
        """Group wallets by shared funding senders using union-find."""
        addr_to_idx = {w["address"]: i for i, w in enumerate(wallets)}
        parent = list(range(len(wallets)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Union wallets that share a sender
        for sender, funded_addrs in sender_to_wallets.items():
            idxs = [addr_to_idx[a] for a in funded_addrs if a in addr_to_idx]
            for i in range(1, len(idxs)):
                union(idxs[0], idxs[i])

        # Collect groups
        groups_map: dict[int, list[dict]] = defaultdict(list)
        for i, w in enumerate(wallets):
            groups_map[find(i)].append(w)

        return list(groups_map.values())

    # ── Group-aware entry point ────────────────────────────

    def group_and_detect(
        self,
        market_id: str,
        direction: str,
        wallets_with_scores: list[dict],
        excluded_senders: set[str] | None = None,
    ) -> list[tuple[list[dict], list[FilterResult]]]:
        """Group wallets by funding relationships and detect confluence per group."""
        if not wallets_with_scores:
            self.last_senders_seen = set()
            return []

        # Fetch funding & build indices (same as detect())
        funding_map = self._fetch_funding_map(wallets_with_scores)
        raw_sender_to_wallets = self._build_sender_index(funding_map)
        self.last_senders_seen = set(raw_sender_to_wallets.keys())

        all_excluded = self._build_default_excluded()
        if excluded_senders:
            all_excluded.update(excluded_senders)
        sender_to_wallets = {
            s: ws for s, ws in raw_sender_to_wallets.items()
            if s.lower() not in all_excluded
        }

        # Group wallets
        groups = self._union_find_groups(wallets_with_scores, sender_to_wallets)

        # Run C filters per group
        results: list[tuple[list[dict], list[FilterResult]]] = []
        for group_wallets in groups:
            group_addrs = {w["address"] for w in group_wallets}
            # Scope sender index to this group
            group_senders = {
                s: (ws & group_addrs) for s, ws in sender_to_wallets.items()
                if ws & group_addrs
            }
            group_funding = {a: funding_map[a] for a in group_addrs if a in funding_map}

            filters: list[FilterResult] = []
            filters.extend(self._check_direction_confluence(direction, group_wallets))
            filters.extend(self._check_origin_layers(group_wallets, group_senders, group_funding))
            filters.extend(self._check_temporal_funding(direction, group_wallets, group_funding))
            filters.extend(self._check_similar_amounts(group_senders, group_funding))
            filters.extend(self._check_distribution_network(group_wallets, group_senders))
            results.append((group_wallets, filters))

        return results

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
