"""
Sentinel Alpha — Main entry point.

Orchestrates the full scan cycle:
 1. Check system config (kill switch)
 2. Fetch active markets
 3. For each market: fetch trades, group by wallet, run analysis pipeline
 4. Score, check odds, generate and publish alerts
 5. Log scan results

Executable as: python -m src.main
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from src import config
from src.database.models import (
    Alert,
    AlertTracking,
    Market,
    Scan,
    Wallet,
    WalletPosition,
    TradeEvent,
    FilterResult,
    AccumulationWindow,
)
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
from src.analysis.sell_detector import SellDetector
from src.analysis.scoring import calculate_score
from src.analysis.arbitrage_filter import tokenize_for_dedup, jaccard
from src.publishing.twitter_bot import TwitterBot
from src.publishing.telegram_bot import TelegramBot
from src.publishing.formatter import AlertFormatter
from src.tracking.resolver import MarketResolver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentinel_alpha")


# ── Helpers ──────────────────────────────────────────────────


def _group_trades_by_wallet(
    trades: list[TradeEvent],
) -> dict[str, list[TradeEvent]]:
    """Group trades by wallet address."""
    groups: dict[str, list[TradeEvent]] = defaultdict(list)
    for t in trades:
        groups[t.wallet_address].append(t)
    return dict(groups)


def _dominant_direction(trades: list[TradeEvent]) -> str:
    """Determine the dominant direction by total amount."""
    yes_total = sum(t.amount for t in trades if t.direction == "YES")
    no_total = sum(t.amount for t in trades if t.direction == "NO")
    return "YES" if yes_total >= no_total else "NO"


def _filter_wallets_by_direction(
    wallets: list[dict],
    filter_sets: list[list[FilterResult]],
) -> tuple[str, list[dict], list[list[FilterResult]]]:
    """Filter wallets to only the dominant direction.

    Prevents mixing YES and NO wallets in the same alert.
    Direction is determined by total amount among analyzed wallets.
    Returns (direction, filtered_wallets, filtered_filter_sets).
    """
    yes_amt = sum(w["total_amount"] for w in wallets if w.get("direction") == "YES")
    no_amt = sum(w["total_amount"] for w in wallets if w.get("direction") == "NO")
    direction = "YES" if yes_amt >= no_amt else "NO"

    filtered = [
        (w, f) for w, f in zip(wallets, filter_sets)
        if w.get("direction") == direction
    ]
    filtered_wallets = [w for w, _ in filtered]
    filtered_filters = [f for _, f in filtered]
    return direction, filtered_wallets, filtered_filters


def _compute_accumulation(
    wallet_address: str,
    trades: list[TradeEvent],
    market_id: str,
) -> AccumulationWindow | None:
    """Build an AccumulationWindow from a wallet's trades in one market."""
    market_trades = [t for t in trades if t.market_id == market_id]
    if not market_trades:
        return None

    direction = _dominant_direction(market_trades)
    directional = [t for t in market_trades if t.direction == direction]
    if not directional:
        return None

    total = sum(t.amount for t in directional)
    sorted_t = sorted(directional, key=lambda t: t.timestamp)

    return AccumulationWindow(
        wallet_address=wallet_address,
        market_id=market_id,
        direction=direction,
        total_amount=total,
        trade_count=len(directional),
        first_trade=sorted_t[0].timestamp,
        last_trade=sorted_t[-1].timestamp,
        trades=sorted_t,
    )


def _has_whale_entry(filters: list[FilterResult]) -> bool:
    """Check if any B19 whale entry filter was triggered."""
    return any(f.filter_id in config.WHALE_ENTRY_FILTERS for f in filters)


def _is_in_odds_range(odds: float | None, score_final: int) -> bool:
    """Check if market odds are in the acceptable range.

    Extended range (up to 0.70) allowed if score >= 90.
    """
    if odds is None:
        return True  # allow through if unknown
    max_odds = config.ODDS_MAX
    if score_final >= config.ODDS_EXTENDED_MIN_SCORE:
        max_odds = config.ODDS_MAX_EXTENDED
    return config.ODDS_MIN <= odds <= max_odds


def _build_alert(
    market: Market,
    direction: str,
    scoring_result,
    wallet_data: list[dict],
    confluence_type: str | None = None,
    is_whale: bool = False,
) -> Alert:
    """Build an Alert object from scoring results."""
    total_amount = sum(w.get("total_amount", 0) for w in wallet_data)

    alert_type = config.ALERT_TYPE_WHALE_ENTRY if is_whale else config.ALERT_TYPE_ACCUMULATION
    if confluence_type and "distribution" in (confluence_type or "").lower():
        alert_type = config.ALERT_TYPE_DISTRIBUTION
    elif len(wallet_data) >= config.CONFLUENCE_BASIC_MIN_WALLETS:
        alert_type = config.ALERT_TYPE_CONFLUENCE

    # N12 in filters → merge_suspected flag
    merge_suspected = any(
        f.filter_id == "N12" for f in scoring_result.filters_triggered
    )

    return Alert(
        market_id=market.market_id,
        alert_type=alert_type,
        score=scoring_result.score_final,
        market_question=market.question,
        direction=direction,
        score_raw=scoring_result.score_raw,
        multiplier=scoring_result.multiplier,
        star_level=scoring_result.star_level,
        wallets=wallet_data,
        total_amount=total_amount,
        odds_at_alert=market.current_odds,
        confluence_count=len(wallet_data),
        confluence_type=confluence_type,
        filters_triggered=[asdict(f) for f in scoring_result.filters_triggered],
        merge_suspected=merge_suspected,
    )


def _deduplicate_alerts(
    alerts: list[tuple[Alert, bool]],
) -> list[tuple[Alert, bool]]:
    """Deduplicate alerts by market question similarity.

    Groups alerts whose market_question Jaccard similarity > 0.6,
    keeps the highest-score alert in each group, marks the rest as deduplicated.

    Args:
        alerts: List of (Alert, is_whale) tuples.

    Returns:
        Same list with deduplicated alerts marked (alert.deduplicated = True).
    """
    if len(alerts) <= 1:
        return alerts

    # Build similarity groups via union-find
    n = len(alerts)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Compare all pairs (strip dates/numbers for better dedup)
    tokens_cache: list[set[str]] = []
    for alert, _ in alerts:
        q = alert.market_question or ""
        tokens_cache.append(tokenize_for_dedup(q))

    for i in range(n):
        for j in range(i + 1, n):
            sim = jaccard(tokens_cache[i], tokens_cache[j])
            if sim > 0.6:
                union(i, j)

    # Group by root
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    # In each group, keep highest score, mark rest as deduplicated
    for indices in groups.values():
        if len(indices) <= 1:
            continue
        # Sort by score descending
        indices.sort(key=lambda i: alerts[i][0].score, reverse=True)
        for idx in indices[1:]:
            alerts[idx][0].deduplicated = True

    return alerts


def _get_primary_wallet(alert: Alert) -> str | None:
    """Return the address of the wallet with the highest total_amount."""
    if not alert.wallets:
        return None
    best = max(alert.wallets, key=lambda w: w.get("total_amount", 0))
    return best.get("address")


def _check_cross_scan_duplicate(
    alert: Alert, db: SupabaseClient
) -> dict | None:
    """Check if a cross-scan duplicate exists in the DB.

    Looks for an alert with the same market_id, direction, and primary
    wallet address created within the last CROSS_SCAN_DEDUP_HOURS hours.

    Returns the existing alert dict if a duplicate is found, None otherwise.
    """
    primary_wallet = _get_primary_wallet(alert)
    if not primary_wallet:
        return None

    recent = db.get_recent_alerts_for_market(
        market_id=alert.market_id,
        direction=alert.direction or "YES",
        hours=config.CROSS_SCAN_DEDUP_HOURS,
    )

    for existing in recent:
        existing_wallets = existing.get("wallets") or []
        if not existing_wallets:
            continue
        existing_primary = max(
            existing_wallets,
            key=lambda w: w.get("total_amount", 0),
        )
        if existing_primary.get("address") == primary_wallet:
            return existing

    return None


def _find_new_wallets(
    incoming_wallets: list[dict],
    existing_wallets: list[dict],
) -> list[dict]:
    """Find wallets in incoming that don't exist in existing (by address)."""
    existing_addresses = {w.get("address") for w in existing_wallets}
    return [
        w for w in incoming_wallets
        if w.get("address") not in existing_addresses
    ]


def _try_consolidate(
    alert: Alert,
    db: SupabaseClient,
    telegram: TelegramBot,
) -> bool:
    """Try to consolidate alert into an existing high-star alert.

    Returns True if consolidated (caller should skip insert), False otherwise.
    """
    existing = db.get_existing_high_star_alert(
        market_id=alert.market_id,
        direction=alert.direction or "YES",
    )
    if existing is None:
        return False

    new_wallets = _find_new_wallets(
        alert.wallets or [], existing.get("wallets") or []
    )
    if not new_wallets:
        return True  # Same wallets, skip without update message

    new_amount = sum(w.get("total_amount", 0) for w in new_wallets)
    new_score = (
        alert.score
        if alert.score > (existing.get("score") or 0)
        else None
    )

    db.update_alert_consolidation(
        alert_id=existing["id"],
        new_wallets=new_wallets,
        new_amount=new_amount,
        new_score=new_score,
    )

    update_count = (existing.get("updated_count") or 0) + 1
    formatter = AlertFormatter()
    msg = formatter.format_alert_update(
        original_alert=existing,
        new_wallets=new_wallets,
        new_amount=new_amount,
        update_count=update_count,
    )
    telegram.send_message(msg, parse_mode="")

    return True


# ── Main scan ────────────────────────────────────────────────


def _filter_markets(
    markets: list[Market],
    *,
    mode: str = "quick",
    resolved_ids: set[str] | None = None,
) -> list[Market]:
    """Filter, sort, and cap markets for scanning.

    Uses SCAN_PROFILES[mode] for volume, odds, categories, blacklist, and cap.
    resolved_ids: set of market_ids already marked resolved in our DB.
        Markets in this set are skipped so the scanner never creates fresh
        pending alerts for markets we have already closed.
    Default mode="quick" preserves current behavior.
    """
    profile = config.SCAN_PROFILES[mode]
    min_volume = profile["min_volume"]
    odds_min = profile["odds_min"]
    odds_max = profile["odds_max"]
    max_markets = profile["max_markets"]
    relevant_cats = profile["relevant_categories"]
    blacklist = config.MARKET_BLACKLIST_TERMS + profile["extra_blacklist"]

    filtered = []
    for m in markets:
        # Skip markets already resolved in our DB
        if resolved_ids and m.market_id in resolved_ids:
            continue
        # Volume filter
        if (m.volume_24h or 0) < min_volume:
            continue
        # Odds filter
        odds = m.current_odds
        if odds is not None and not (odds_min <= odds <= odds_max):
            continue
        # Category filter
        if m.category and m.category not in relevant_cats:
            continue
        # Blacklist filter
        if m.question:
            q_lower = m.question.lower()
            if any(term in q_lower for term in blacklist):
                continue
        filtered.append(m)

    # Sort by volume descending, cap
    filtered.sort(key=lambda m: m.volume_24h or 0, reverse=True)
    if max_markets is not None:
        return filtered[:max_markets]
    return filtered


# ── Deep mode: parallel market processing ────────────────────


_DEEP_SEMAPHORE_SIZE = 5
_DEEP_BATCH_PAUSE = 1.0        # seconds between batches
_DEEP_RETRY_DELAYS = [5, 15]   # exponential backoff for 429s (2 retries, then skip)


def _is_rate_limited(exc: Exception) -> bool:
    """Check if an exception is an HTTP 429 rate-limit error."""
    import requests
    if isinstance(exc, requests.exceptions.HTTPError):
        return exc.response is not None and exc.response.status_code == 429
    return "429" in str(exc) or "Too Many Requests" in str(exc)


async def _process_markets_deep(
    markets,
    *,
    pm_client,
    wallet_analyzer,
    behavior_analyzer,
    market_analyzer,
    noise_filter,
    arb_filter,
    confluence_detector,
    db,
    counters,
    chain_client,
    lookback_minutes: int,
) -> tuple[list[tuple[Alert, bool]], list[str]]:
    """Process all markets with rate-limited parallelism.

    - 5 concurrent workers (Semaphore)
    - 1s pause between batches
    - Retry with exponential backoff on 429 errors
    Returns (collected_alerts, errors).
    """
    collected: list[tuple[Alert, bool]] = []
    errors: list[str] = []
    alert_count = 0
    rate_limited = 0

    def _call_process_market(market):
        return _process_market(
            market=market,
            pm_client=pm_client,
            wallet_analyzer=wallet_analyzer,
            behavior_analyzer=behavior_analyzer,
            market_analyzer=market_analyzer,
            noise_filter=noise_filter,
            arb_filter=arb_filter,
            confluence_detector=confluence_detector,
            db=db,
            counters=counters,
            excluded_senders=set(),
            chain_client=chain_client,
            lookback_minutes=lookback_minutes,
        )

    async def process_one(market):
        nonlocal alert_count, rate_limited
        last_exc = None
        max_attempts = len(_DEEP_RETRY_DELAYS) + 1
        for attempt in range(max_attempts):
            try:
                result = await asyncio.to_thread(
                    _call_process_market, market,
                )
                if result:
                    collected.extend(result)
                    alert_count += len(result)
                return  # success
            except Exception as e:
                if _is_rate_limited(e) and attempt < len(_DEEP_RETRY_DELAYS):
                    delay = _DEEP_RETRY_DELAYS[attempt]
                    logger.warning(
                        "429 rate limit on %s — retry %d/%d in %ds",
                        market.market_id[:12], attempt + 1,
                        len(_DEEP_RETRY_DELAYS), delay,
                    )
                    await asyncio.sleep(delay)
                    last_exc = e
                    continue
                last_exc = e
                break

        # Exhausted retries or non-429 error
        if _is_rate_limited(last_exc):
            rate_limited += 1
            logger.warning(
                "Rate limited — skipping market %s (%s)",
                market.market_id[:12],
                market.question[:40] if market.question else "",
            )
        else:
            errors.append(f"Market {market.market_id[:12]}: {last_exc}")

    # Process in batches with pause between them
    batch_size = _DEEP_SEMAPHORE_SIZE
    processed = 0
    for i in range(0, len(markets), batch_size):
        batch = markets[i : i + batch_size]
        await asyncio.gather(*(process_one(m) for m in batch))
        processed += len(batch)
        if processed % 25 == 0 or i + batch_size >= len(markets):
            logger.info(
                "Deep scan progress: %d/%d markets, %d alerts, %d rate_limited",
                processed, len(markets), alert_count, rate_limited,
            )
        # Pause between batches to avoid 429s
        if i + batch_size < len(markets):
            await asyncio.sleep(_DEEP_BATCH_PAUSE)

    ok_count = processed - rate_limited
    logger.info(
        "Deep scan complete: %d/%d markets OK, %d rate_limited, %d alerts found",
        ok_count, len(markets), rate_limited, alert_count,
    )
    return collected, errors


def run_scan(
    mode: str = "quick",
    dry_run: bool = False,
    lookback_override: int | None = None,
    post_scan_check: bool = False,
) -> None:
    """Execute a single scan cycle.

    Args:
        mode: "quick" (default, current behavior) or "deep" (all markets, no
              global timeout, 10x parallel).
        dry_run: If True, run full pipeline but skip all DB writes and
                 Telegram/X publishing. Logs what would have been done.
        lookback_override: If set, override the profile's lookback_minutes.
        post_scan_check: If True, run net position check after scan to detect
                         CTF merges, transfers, burns (invisible to CLOB API).
                         Skipped automatically in GitHub Actions.
                         Also runs automatically when mode="deep" (local only).
    """
    # Suppress noisy HTTP client logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    start_time = time.time()
    scan_deadline = start_time + config.SCAN_TIMEOUT_SECONDS
    errors: list[str] = []

    # ── Step 1: Connect services ─────────────────────────────
    db = SupabaseClient()

    if not db.is_scan_enabled():
        logger.info("Scan disabled via system_config. Exiting.")
        return

    scan = Scan(timestamp=datetime.now(timezone.utc))
    logger.info(
        "=== Sentinel Alpha scan started === mode=%s dry_run=%s",
        mode, dry_run,
    )

    try:
        pm_client = PolymarketClient()
        chain_client = BlockchainClient()
        news = NewsChecker()
        _max_hops = config.SCAN_PROFILES[mode].get("max_funding_hops", config.MAX_FUNDING_HOPS)
        wallet_analyzer = WalletAnalyzer(db, chain_client, pm_client=pm_client, max_hops=_max_hops)
        behavior_analyzer = BehaviorAnalyzer(db_client=db, pm_client=pm_client)
        market_analyzer = MarketAnalyzer(db_client=db, polymarket_client=pm_client)
        noise_filter = NoiseFilter(news_checker=news, db_client=db)
        arb_filter = ArbitrageFilter(db_client=db)
        confluence_detector = ConfluenceDetector(db_client=db)
        confluence_detector.refresh_excluded_senders()
        twitter = TwitterBot()
        telegram = TelegramBot()
    except Exception as e:
        logger.error("Failed to initialize services: %s", e, exc_info=True)
        scan.duration_seconds = time.time() - start_time
        scan.status = "error"
        scan.errors = f"Init failed: {e}"
        db.insert_scan(scan)
        return

    try:
        # ── Step 2: Fetch active markets ─────────────────────
        profile = config.SCAN_PROFILES[mode]
        lookback_minutes = lookback_override or profile["lookback_minutes"]
        raw_markets = pm_client.get_active_markets(
            categories=profile["categories"],
        )
        # Load resolved market_ids from our DB once and pass to filter.
        # This prevents creating new pending alerts for markets we already closed,
        # guarding against the window where the Gamma API still shows them as active.
        try:
            resolved_ids = db.get_resolved_market_ids()
        except Exception as _e:
            logger.warning("Could not load resolved market IDs, proceeding without guard: %s", _e)
            resolved_ids = set()
        markets = _filter_markets(raw_markets, mode=mode, resolved_ids=resolved_ids)
        scan.markets_scanned = len(markets)
        logger.info(
            "Fetched %d markets (%d after filtering, %d resolved skipped), lookback=%dmin",
            len(raw_markets), len(markets), len(resolved_ids), lookback_minutes,
        )

        if not markets:
            logger.warning("No active markets found, ending scan")
            scan.duration_seconds = time.time() - start_time
            scan.status = "success"
            return

        counters = {
            "total_trades": 0,
            "wallets_analyzed": 0,
            "alerts_generated": 0,
            "alerts_published_x": 0,
            "alerts_published_tg": 0,
            "alerts_deduplicated": 0,
            "alerts_cross_scan_dedup": 0,
            "alerts_consolidated": 0,
        }
        collected_alerts: list[tuple[Alert, bool]] = []

        # Cross-market super-sender tracking:
        # {sender_address: set(market_ids)} — senders funding wallets in
        # too many unrelated markets are exchanges/routers, not insiders.
        sender_market_count: dict[str, set[str]] = defaultdict(set)

        # ── Step 3-7: Per-market pipeline (collect phase) ──────
        markets_processed = 0

        if mode == "deep":
            # Deep mode: 10x parallel, no global timeout, no sender tracking
            collected_alerts, deep_errors = asyncio.run(
                _process_markets_deep(
                    markets,
                    pm_client=pm_client,
                    wallet_analyzer=wallet_analyzer,
                    behavior_analyzer=behavior_analyzer,
                    market_analyzer=market_analyzer,
                    noise_filter=noise_filter,
                    arb_filter=arb_filter,
                    confluence_detector=confluence_detector,
                    db=db,
                    counters=counters,
                    chain_client=chain_client,
                    lookback_minutes=lookback_minutes,
                )
            )
            errors.extend(deep_errors)
            markets_processed = len(markets)

        else:
            # Quick mode: sequential, with global timeout + sender tracking
            for market in markets:
                # Timeout check (quick mode only)
                if time.time() > scan_deadline:
                    logger.warning(
                        "Scan timeout (%ds) reached after %d/%d markets",
                        config.SCAN_TIMEOUT_SECONDS, markets_processed, len(markets),
                    )
                    break
                markets_processed += 1
                t_mkt = time.time()
                try:
                    # Build excluded senders: those appearing in >SENDER_MAX_MARKETS markets
                    excluded_senders = {
                        s for s, mkts in sender_market_count.items()
                        if len(mkts) > config.SENDER_MAX_MARKETS
                    }

                    result = _process_market(
                        market=market,
                        pm_client=pm_client,
                        wallet_analyzer=wallet_analyzer,
                        behavior_analyzer=behavior_analyzer,
                        market_analyzer=market_analyzer,
                        noise_filter=noise_filter,
                        arb_filter=arb_filter,
                        confluence_detector=confluence_detector,
                        db=db,
                        counters=counters,
                        excluded_senders=excluded_senders,
                        chain_client=chain_client,
                        lookback_minutes=lookback_minutes,
                    )

                    # Update sender_market_count with senders seen in this market
                    for sender in confluence_detector.last_senders_seen:
                        sender_market_count[sender].add(market.market_id)

                    if result:
                        collected_alerts.extend(result)
                    logger.info(
                        "Market %d/%d done in %.1fs: %s",
                        markets_processed, len(markets),
                        time.time() - t_mkt,
                        market.question[:50],
                    )

                except Exception as e:
                    msg = f"Market {market.market_id[:12]}: {e}"
                    logger.error("Error processing market: %s", msg, exc_info=True)
                    errors.append(msg)
                    continue

        # ── Step 7b: Deduplicate ───────────────────────────────
        collected_alerts = _deduplicate_alerts(collected_alerts)
        dedup_count = sum(1 for a, _ in collected_alerts if a.deduplicated)
        counters["alerts_deduplicated"] = dedup_count

        # ── Step 8: Save + Publish ─────────────────────────────
        for alert, is_whale in collected_alerts:
            try:
                # 8a-pre. Secondary alerts: insert to DB but don't publish
                if alert.is_secondary:
                    if not dry_run:
                        if alert.star_level_initial is None:
                            alert.star_level_initial = alert.star_level
                        alert_id = db.insert_alert(alert)
                        alert.id = alert_id
                    else:
                        logger.info(
                            "[DRY-RUN] Would insert secondary alert: %s",
                            alert.market_question[:50] if alert.market_question else "",
                        )
                    counters["alerts_generated"] += 1
                    continue

                # 0★ gate: discard alerts with no star rating
                if (alert.star_level or 0) < 1:
                    logger.debug(
                        "Skipping 0★ alert for %s %s",
                        alert.direction,
                        alert.market_id[:12],
                    )
                    continue

                # 8a. Within-scan dedup: still insert, but skip publish
                if alert.deduplicated:
                    if dry_run:
                        logger.info(
                            "[DRY-RUN] Would insert deduplicated alert: %s",
                            alert.market_question[:50] if alert.market_question else alert.market_id[:12],
                        )
                    else:
                        if alert.star_level_initial is None:
                            alert.star_level_initial = alert.star_level
                        alert_id = db.insert_alert(alert)
                        alert.id = alert_id
                    counters["alerts_generated"] += 1
                    logger.debug(
                        "Skipping publish for within-scan deduplicated alert",
                    )
                    continue

                # 8b. Cross-scan dedup: check for existing alert in last 24h
                existing_alert = _check_cross_scan_duplicate(alert, db) if not dry_run else None
                if existing_alert is not None:
                    existing_id = existing_alert["id"]
                    existing_score = existing_alert.get("score") or 0
                    existing_star = existing_alert.get("star_level") or 0
                    existing_amount = existing_alert.get("total_amount") or 0.0
                    existing_wallets = existing_alert.get("wallets") or []

                    update_fields: dict = {
                        "odds_at_alert": alert.odds_at_alert,
                    }

                    # Wallet merge: additive only — never replace existing wallets.
                    # TODO: wallet amount updates for existing wallets not tracked —
                    # would require per-wallet position comparison.
                    new_wallets = _find_new_wallets(alert.wallets or [], existing_wallets)
                    if new_wallets:
                        update_fields["wallets"] = existing_wallets + new_wallets
                        # Accumulate new wallet amounts on top of existing total
                        new_wallets_amount = sum(
                            w.get("total_amount", 0) for w in new_wallets
                        )
                        update_fields["total_amount"] = existing_amount + new_wallets_amount
                    elif (alert.total_amount or 0) > existing_amount:
                        # No new wallets, but existing wallet increased position
                        update_fields["total_amount"] = alert.total_amount

                    # Score and star: only upgrade, never downgrade a published alert
                    if (alert.score or 0) > existing_score:
                        update_fields["score"] = alert.score
                        update_fields["score_raw"] = alert.score_raw
                        update_fields["multiplier"] = alert.multiplier
                        # filters_triggered: always paired with score — the scan
                        # that generated the higher score also generated the
                        # richer filter set. Replace together or not at all.
                        update_fields["filters_triggered"] = [
                            {"filter_id": f.filter_id,
                             "filter_name": f.filter_name,
                             "points": f.points,
                             "category": f.category,
                             "details": f.details}
                            for f in (alert.filters_triggered or [])
                        ]
                        # Guard: star only moves strictly up, never sideways or down
                        if (alert.star_level or 0) > existing_star:
                            update_fields["star_level"] = alert.star_level

                    db.update_alert_fields(existing_id, update_fields)
                    alert.deduplicated = True
                    counters["alerts_cross_scan_dedup"] += 1
                    logger.info(
                        "Cross-scan dedup: updated existing alert #%d for %s %s (%s)",
                        existing_id,
                        alert.direction,
                        alert.market_id[:12],
                        alert.market_question[:40] if alert.market_question else "",
                    )
                    continue

                # 8b2. High-star consolidation: merge into existing 4+★ alert
                if not dry_run and _try_consolidate(alert, db, telegram):
                    counters["alerts_consolidated"] += 1
                    logger.info(
                        "Consolidated alert for %s %s into existing high-star alert",
                        alert.direction,
                        alert.market_id[:12],
                    )
                    continue

                # 8c. New alert: insert + publish
                if dry_run:
                    logger.info(
                        "[DRY-RUN] Would insert alert: %s %d★ %dpts",
                        alert.market_question[:50] if alert.market_question else alert.market_id[:12],
                        alert.star_level or 0,
                        alert.score or 0,
                    )
                    _publish_alert(
                        alert=alert,
                        alert_id=None,
                        is_whale=is_whale,
                        db=db,
                        twitter=twitter,
                        telegram=telegram,
                        counters=counters,
                        dry_run=True,
                    )
                else:
                    # Fix star_level_initial at T0 — immutable ML label
                    if alert.star_level_initial is None:
                        alert.star_level_initial = alert.star_level
                    alert_id = db.insert_alert(alert)
                    alert.id = alert_id
                    counters["alerts_generated"] += 1
                    _publish_alert(
                        alert=alert,
                        alert_id=alert_id,
                        is_whale=is_whale,
                        db=db,
                        twitter=twitter,
                        telegram=telegram,
                        counters=counters,
                        dry_run=False,
                    )
                    # Merge notification (independent of main alert publication)
                    if alert.merge_suspected and (alert.score or 0) >= config.MERGE_MIN_SCORE_NOTIFY:
                        try:
                            merge_detail = next(
                                (
                                    f.get("details", "")
                                    for f in (alert.filters_triggered or [])
                                    if f.get("filter_id") == "N12"
                                ),
                                None,
                            )
                            msg = AlertFormatter().format_merge_notification(alert, merge_detail)
                            telegram.send_message(msg)
                            logger.info(
                                "Merge notification sent for alert #%s", alert_id
                            )
                        except Exception as e:
                            logger.debug("Merge notification failed for alert #%s: %s", alert_id, e)
            except Exception as e:
                logger.error("Failed to save/publish alert: %s", e)

        # ── Step 8b: Sell monitoring ──────────────────────────
        if not dry_run:
            try:
                sell_detector = SellDetector(db_client=db, polymarket_client=pm_client)
                formatter_inst = AlertFormatter()

                # CLOB-visible sells
                sell_events = sell_detector.check_open_positions()
                for event in sell_events:
                    try:
                        telegram.publish_sell_notification(event)
                    except Exception as e:
                        logger.debug("Sell notification failed: %s", e)
                if sell_events:
                    logger.info("Sell monitoring: %d CLOB sell events detected", len(sell_events))

                # Post-scan net position check (detects CTF merges, transfers, burns).
                # Runs when explicitly requested OR automatically in deep mode (local, no CI guard).
                if post_scan_check or mode == "deep":
                    try:
                        net_events = sell_detector.check_net_positions()
                        for event in net_events:
                            try:
                                msg = formatter_inst.format_position_gone(event)
                                telegram.send_message(msg, parse_mode="")
                            except Exception as e:
                                logger.debug("Position gone notification failed: %s", e)
                        if net_events:
                            logger.info(
                                "Post-scan net check: %d position events detected",
                                len(net_events),
                            )
                    except Exception as e:
                        logger.error("Post-scan net position check failed: %s", e)

                # Merge resolution (always runs, capped at 20 alerts)
                try:
                    merge_result = sell_detector.check_merge_resolution()
                    logger.info("Merge resolution check: %s", merge_result)
                except Exception as e:
                    logger.debug("check_merge_resolution failed: %s", e)

                # Deep-scan only: re-verify all pending alerts against CLOB API.
                # Resolves any markets that have settled since the last daily
                # resolver.yml run without waiting for the next scheduled cycle.
                if mode == "deep":
                    try:
                        resolver = MarketResolver(db=db, polymarket=pm_client)
                        resolve_result = resolver.run()
                        logger.info("Deep scan resolver: %s", resolve_result)
                    except Exception as e:
                        logger.error("Deep scan resolver failed: %s", e)

                    try:
                        _backfill_hold_durations(db)
                    except Exception as e:
                        logger.error("hold_duration backfill failed: %s", e)

                    try:
                        _reconcile_sell_totals(db)
                    except Exception as e:
                        logger.error("sell totals reconciliation failed: %s", e)

            except Exception as e:
                logger.error("Sell monitoring failed: %s", e)
        else:
            logger.info("[DRY-RUN] Skipping sell monitoring")

        # ── Step 9: Record scan ──────────────────────────────
        scan.markets_scanned = markets_processed
        scan.transactions_analyzed = counters["total_trades"]
        scan.wallets_analyzed = counters["wallets_analyzed"]
        scan.alerts_generated = counters["alerts_generated"]
        scan.alerts_published_x = counters["alerts_published_x"]
        scan.alerts_published_tg = counters["alerts_published_tg"]
        scan.duration_seconds = time.time() - start_time
        scan.status = "success" if not errors else "partial"
        scan.errors = "; ".join(errors[:10]) if errors else None

    except Exception as e:
        logger.error("Scan failed: %s", e, exc_info=True)
        scan.duration_seconds = time.time() - start_time
        scan.status = "error"
        scan.errors = str(e)[:500]

    finally:
        if not dry_run:
            try:
                db.insert_scan(scan)
            except Exception as e:
                logger.error("Failed to save scan record: %s", e)
        else:
            logger.info("[DRY-RUN] Scan complete — not recording to database")

    _log_scan_summary(scan)


def _backfill_hold_durations(db: SupabaseClient) -> int:
    """Backfill hold_duration_hours for sold positions that are missing it.

    Calculates hold_duration_hours = (sell_timestamp - created_at) / 3600
    for every wallet_position where:
      - current_status != 'open'   (position was exited)
      - hold_duration_hours IS NULL (not yet calculated)
      - sell_timestamp IS NOT NULL  (sell was recorded)

    Returns the number of positions updated.
    """
    try:
        rows = (
            db.client.table("wallet_positions")
            .select("id,created_at,sell_timestamp")
            .neq("current_status", "open")
            .is_("hold_duration_hours", "null")
            .not_.is_("sell_timestamp", "null")
            .execute()
            .data
        ) or []
    except Exception as e:
        logger.error("_backfill_hold_durations: query failed: %s", e)
        return 0

    if not rows:
        logger.debug("_backfill_hold_durations: nothing to backfill")
        return 0

    from dateutil import parser as dt_parser

    updated = 0
    for row in rows:
        try:
            anchor = dt_parser.parse(row["created_at"])
            sell_ts = dt_parser.parse(row["sell_timestamp"])
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            if sell_ts.tzinfo is None:
                sell_ts = sell_ts.replace(tzinfo=timezone.utc)
            hold_h = round(max(0.0, (sell_ts - anchor).total_seconds() / 3600), 2)
            db.client.table("wallet_positions").update(
                {"hold_duration_hours": hold_h}
            ).eq("id", row["id"]).execute()
            updated += 1
        except Exception as e:
            logger.debug(
                "_backfill_hold_durations: failed for position #%s: %s",
                row.get("id"), e,
            )

    logger.info("_backfill_hold_durations: updated %d positions", updated)
    return updated


def _reconcile_sell_totals(db: SupabaseClient) -> int:
    """Reconcile alerts.total_sold_pct against alert_sell_events.

    For each alert_id present in alert_sell_events, recalculates the correct
    total_sold_pct as min(1.0, sum(sell_pct)) and updates the alerts row if
    the stored value differs by more than a float rounding tolerance.

    This corrects silent write failures from whale_monitor and any other
    desync between the two tables.

    Returns the number of alerts updated.
    """
    try:
        events = (
            db.client.table("alert_sell_events")
            .select("alert_id,sell_pct")
            .execute()
            .data
        ) or []
    except Exception as e:
        logger.error("_reconcile_sell_totals: query alert_sell_events failed: %s", e)
        return 0

    if not events:
        logger.debug("_reconcile_sell_totals: no sell events found")
        return 0

    # Aggregate sell_pct per alert_id
    from collections import defaultdict as _dd
    sums: dict[int, float] = _dd(float)
    for ev in events:
        aid = ev.get("alert_id")
        pct = ev.get("sell_pct") or 0.0
        if aid is not None:
            sums[aid] += pct

    alert_ids = list(sums.keys())

    # Fetch current total_sold_pct for those alerts
    try:
        rows = (
            db.client.table("alerts")
            .select("id,total_sold_pct,is_secondary")
            .in_("id", alert_ids)
            .execute()
            .data
        ) or []
    except Exception as e:
        logger.error("_reconcile_sell_totals: query alerts failed: %s", e)
        return 0

    current: dict[int, float] = {r["id"]: (r.get("total_sold_pct") or 0.0) for r in rows}

    updated = 0
    for alert_id, raw_sum in sums.items():
        correct = round(min(1.0, raw_sum), 6)
        stored = round(current.get(alert_id, 0.0), 6)
        if abs(correct - stored) < 1e-5:
            continue
        try:
            db.client.table("alerts").update(
                {"total_sold_pct": correct}
            ).eq("id", alert_id).execute()
            logger.info(
                "_reconcile_sell_totals: alert #%d %.4f → %.4f",
                alert_id, stored, correct,
            )
            updated += 1
        except Exception as e:
            logger.error(
                "_reconcile_sell_totals: failed to update alert #%d: %s",
                alert_id, e,
            )

    logger.info("_reconcile_sell_totals: updated %d alerts", updated)
    return updated


def _log_scan_summary(scan: Scan) -> None:
    """Log a formatted scan summary."""
    duration = scan.duration_seconds or 0
    mins, secs = divmod(int(duration), 60)
    logger.info(
        "\n=== SCAN COMPLETE ===\n"
        "Markets scanned: %d\n"
        "Wallets analyzed: %d\n"
        "Alerts generated: %d\n"
        "  Published TG: %d | Published X: %d\n"
        "Duration: %dm %ds | Status: %s",
        scan.markets_scanned,
        scan.wallets_analyzed,
        scan.alerts_generated,
        scan.alerts_published_tg, scan.alerts_published_x,
        mins, secs,
        scan.status,
    )


def _process_market(
    *,
    market: Market,
    pm_client: PolymarketClient,
    wallet_analyzer: WalletAnalyzer,
    behavior_analyzer: BehaviorAnalyzer,
    market_analyzer: MarketAnalyzer,
    noise_filter: NoiseFilter,
    arb_filter: ArbitrageFilter,
    confluence_detector: ConfluenceDetector,
    db: SupabaseClient,
    counters: dict,
    excluded_senders: set[str] | None = None,
    chain_client: BlockchainClient | None = None,
    lookback_minutes: int | None = None,
) -> list[tuple[Alert, bool]]:
    """Process a single market through the full analysis pipeline.

    Returns list of (alert, is_whale) tuples (one per independent group).
    """
    t_market_start = time.time()
    logger.debug("Processing market: %s", market.question[:60])

    # ── 4a. Fetch trades ─────────────────────────────────────
    trades = pm_client.get_recent_trades(
        market_id=market.market_id,
        minutes=lookback_minutes or config.SCAN_LOOKBACK_MINUTES,
        min_amount=config.MIN_TX_AMOUNT,
    )
    counters["total_trades"] += len(trades)

    if not trades:
        logger.debug("  No trades in %s (%.1fs)", market.market_id[:12], time.time() - t_market_start)
        return []

    logger.debug("  %d trades in %s", len(trades), market.market_id[:12])

    # ── 4c. Group by wallet, keep top N by volume ───────────
    wallet_groups = _group_trades_by_wallet(trades)

    # Sort wallets by total trade volume, keep only top N
    sorted_wallets = sorted(
        wallet_groups.items(),
        key=lambda item: sum(t.amount for t in item[1]),
        reverse=True,
    )
    top_wallets = sorted_wallets[: config.MAX_WALLETS_PER_MARKET]
    if len(sorted_wallets) > config.MAX_WALLETS_PER_MARKET:
        logger.debug(
            "  Capped wallets: %d → %d (top by volume)",
            len(sorted_wallets), len(top_wallets),
        )

    # Per-market timeout
    market_deadline = t_market_start + config.MARKET_TIMEOUT_SECONDS

    # ── Per-wallet analysis ──────────────────────────────────
    analyzed_wallets: list[dict] = []
    wallet_filter_sets: list[list[FilterResult]] = []

    for wallet_address, wallet_trades in top_wallets:
        # Check per-market timeout
        if time.time() > market_deadline:
            logger.warning(
                "  Market timeout (%ds) for %s after %d wallets",
                config.MARKET_TIMEOUT_SECONDS,
                market.market_id[:12],
                len(analyzed_wallets),
            )
            break

        try:
            result = _analyze_wallet(
                wallet_address=wallet_address,
                wallet_trades=wallet_trades,
                all_trades=trades,
                market=market,
                wallet_analyzer=wallet_analyzer,
                behavior_analyzer=behavior_analyzer,
                noise_filter=noise_filter,
                arb_filter=arb_filter,
                db=db,
                chain_client=chain_client,
                pm_client=pm_client,
            )
            if result is None:
                continue

            wallet_data, wallet_filters = result
            analyzed_wallets.append(wallet_data)
            wallet_filter_sets.append(wallet_filters)
            counters["wallets_analyzed"] += 1

        except Exception as e:
            logger.error(
                "Error analyzing wallet %s in market %s: %s",
                wallet_address[:10], market.market_id[:12], e,
            )
            continue

    if not analyzed_wallets:
        return []

    # ── 4f. Filter wallets by dominant direction ───────────────
    direction, analyzed_wallets, wallet_filter_sets = _filter_wallets_by_direction(
        analyzed_wallets, wallet_filter_sets,
    )
    if not analyzed_wallets:
        return []

    # ── 5a. Market-level analysis ────────────────────────────
    market_filters: list[FilterResult] = []
    try:
        market_filters = market_analyzer.analyze(market, trades=trades)
    except Exception as e:
        logger.error("Market analyzer failed for %s: %s", market.market_id[:12], e)

    # ── 5b. Group wallets & detect confluence per group ─────
    # `direction` was already set in step 4f (from analyzed wallets).
    import uuid as _uuid

    wallets_for_confluence = [
        {"address": w["address"], "direction": w.get("direction", direction)}
        for w in analyzed_wallets
    ]

    groups_with_filters: list[tuple[list[dict], list[FilterResult]]]

    try:
        # Ensure funding is fetched for wallets when 2+ share a direction.
        same_dir_count = sum(
            1 for w in wallets_for_confluence if w["direction"] == direction
        )
        if same_dir_count >= config.FUNDING_GROUPING_MIN_WALLETS and chain_client is not None:
            for w in wallets_for_confluence:
                addr = w["address"]
                existing = db.get_funding_sources(addr)
                if not existing:
                    try:
                        funding = chain_client.get_funding_sources(
                            addr, max_hops=profile.get("max_funding_hops", config.MAX_FUNDING_HOPS)
                        )
                        if funding:
                            db.insert_funding_batch(funding)
                    except Exception as e:
                        logger.debug("Confluence funding fetch for %s: %s", addr[:10], e)

        groups_with_filters = confluence_detector.group_and_detect(
            market_id=market.market_id,
            direction=direction,
            wallets_with_scores=wallets_for_confluence,
            excluded_senders=excluded_senders,
        )
    except Exception as e:
        logger.error("Confluence detection failed for %s: %s", market.market_id[:12], e)
        # Fallback: each wallet is its own group, no C filters
        groups_with_filters = [([w_dict], []) for w_dict in wallets_for_confluence]

    # Build address → (wallet_data, filters) lookup
    wallet_lookup: dict[str, tuple[dict, list[FilterResult]]] = {}
    for wd, wf in zip(analyzed_wallets, wallet_filter_sets):
        wallet_lookup[wd["address"]] = (wd, wf)

    # ── 5c. Score each group independently ────────────────────
    alert_group_id = str(_uuid.uuid4())
    alert_candidates: list[tuple[Alert, bool, int]] = []  # (alert, is_whale, score)

    for group_wallets, confluence_filters in groups_with_filters:
        # Match group addresses to analyzed wallets
        group_analyzed: list[dict] = []
        group_filter_sets: list[list[FilterResult]] = []
        for gw in group_wallets:
            addr = gw["address"]
            if addr in wallet_lookup:
                wd, wf = wallet_lookup[addr]
                group_analyzed.append(wd)
                group_filter_sets.append(wf)

        if not group_analyzed and not market_filters and not confluence_filters:
            continue

        # Best wallet in this group
        best_wallet_filters: list[FilterResult] = []
        best_wallet_score = -1
        for wf in group_filter_sets:
            raw = sum(f.points for f in wf)
            if raw > best_wallet_score:
                best_wallet_score = raw
                best_wallet_filters = wf

        all_filters = best_wallet_filters + market_filters + confluence_filters
        if not all_filters:
            continue

        group_amount = sum(w.get("total_amount", 0) for w in group_analyzed)
        max_distinct = max((w.get("distinct_markets", 0) for w in group_analyzed), default=0)

        scoring_result = calculate_score(
            all_filters,
            total_amount=group_amount,
            wallet_market_count=max_distinct or None,
        )

        # ── Detailed filter log for debugging ────────────────
        filter_lines = []
        for f in scoring_result.filters_triggered:
            detail = f" — {f.details}" if f.details else ""
            filter_lines.append(f"  {f.filter_id}: {f.points:+d} pts ({f.filter_name}){detail}")
        logger.info(
            "Score breakdown for %s (group %d/%d) | score=%d (raw=%d × %.2f):\n%s",
            market.question[:50],
            len(alert_candidates) + 1,
            len(groups_with_filters),
            scoring_result.score_final,
            scoring_result.score_raw,
            scoring_result.multiplier,
            "\n".join(filter_lines) if filter_lines else "  (no filters)",
        )

        # Odds range check
        if not _is_in_odds_range(market.current_odds, scoring_result.score_final):
            continue

        is_whale = _has_whale_entry(scoring_result.filters_triggered)

        # Determine confluence type
        confluence_type = None
        for f in confluence_filters:
            if f.filter_id == "C07":
                confluence_type = "distribution_network"
                break
            elif f.filter_id in ("C03a", "C03b", "C03c", "C03d"):
                confluence_type = "shared_funding"
            elif f.filter_id == "C05" and not confluence_type:
                confluence_type = "exchange_funded"

        alert = _build_alert(
            market=market,
            direction=direction,
            scoring_result=scoring_result,
            wallet_data=group_analyzed,
            confluence_type=confluence_type,
            is_whale=is_whale,
        )
        alert.alert_group_id = alert_group_id
        alert_candidates.append((alert, is_whale, scoring_result.score_final))

    if not alert_candidates:
        return []

    # Sort by score descending — first is primary
    alert_candidates.sort(key=lambda x: x[2], reverse=True)

    # Mark primary/secondary
    multi_signal = len(alert_candidates) > 1
    results: list[tuple[Alert, bool]] = []
    for i, (alert, is_whale, _score) in enumerate(alert_candidates):
        alert.multi_signal = multi_signal
        if i == 0:
            alert.is_secondary = False
            alert.secondary_count = len(alert_candidates) - 1
        else:
            alert.is_secondary = True
            alert.secondary_count = 0
        results.append((alert, is_whale))

    t_market_elapsed = time.time() - t_market_start
    primary = results[0][0]
    logger.info(
        "Alert candidate: %s %s | %d stars | score=%d | %s | groups=%d (%.1fs)",
        direction,
        market.question[:40],
        primary.star_level or 0,
        primary.score,
        primary.alert_type,
        len(results),
        t_market_elapsed,
    )

    return results


def _analyze_wallet(
    *,
    wallet_address: str,
    wallet_trades: list[TradeEvent],
    all_trades: list[TradeEvent],
    market: Market,
    wallet_analyzer: WalletAnalyzer,
    behavior_analyzer: BehaviorAnalyzer,
    noise_filter: NoiseFilter,
    arb_filter: ArbitrageFilter,
    db: SupabaseClient,
    chain_client: BlockchainClient | None = None,
    pm_client: PolymarketClient | None = None,
) -> tuple[dict, list[FilterResult]] | None:
    """Analyze a single wallet. Returns (wallet_data, filters) or None to skip."""
    t_wallet_start = time.time()

    # ── 4d. Accumulation check ───────────────────────────────
    accum = _compute_accumulation(
        wallet_address, wallet_trades, market.market_id,
    )
    if accum is None or accum.total_amount < config.MIN_ACCUMULATED_AMOUNT:
        return None

    direction = accum.direction
    filters: list[FilterResult] = []

    # ── 4e-i. Wallet analyzer (W + O filters) ────────────────
    t0 = time.time()
    try:
        w_filters = wallet_analyzer.analyze(wallet_address, wallet_trades)
        filters.extend(w_filters)
    except Exception as e:
        logger.error("wallet_analyzer failed for %s: %s", wallet_address[:10], e)
    t_wallet_analysis = time.time() - t0

    # ── 4e-ii. Behavior analyzer (B filters) ─────────────────
    t0 = time.time()
    # Fetch wallet balance for B23 position sizing
    wallet_balance = None
    if chain_client is not None:
        try:
            wallet_balance = chain_client.get_balance(wallet_address)
        except Exception as e:
            logger.debug("get_balance failed for %s: %s", wallet_address[:10], e)
    try:
        b_filters = behavior_analyzer.analyze(
            wallet_address=wallet_address,
            trades=wallet_trades,
            market_id=market.market_id,
            current_odds=market.current_odds,
            wallet_balance=wallet_balance,
            resolution_date=market.resolution_date,
        )
        filters.extend(b_filters)
    except Exception as e:
        logger.error("behavior_analyzer failed for %s: %s", wallet_address[:10], e)
    t_behavior = time.time() - t0

    # ── 4e-iii. Noise filter (N01, N02, N05, N06) ────────────
    t0 = time.time()
    try:
        # Build a minimal Wallet for noise filter
        wallet_data_db = db.get_wallet(wallet_address)
        non_pm = 0
        if wallet_data_db:
            non_pm = wallet_data_db.get("non_pm_markets", 0)

        # Populate non_pm_markets from real PM history if DB has 0
        if non_pm == 0 and pm_client is not None:
            try:
                history = pm_client.get_wallet_pm_history_cached(wallet_address)
                if history and history.get("market_ids"):
                    non_pm = pm_client.count_non_political_markets(history["market_ids"])
            except Exception as e:
                logger.debug("N06 PM history check failed for %s: %s", wallet_address[:10], e)

        wallet_obj = Wallet(address=wallet_address, non_pm_markets=non_pm)

        n_filters = noise_filter.analyze(
            wallet=wallet_obj,
            trades=wallet_trades,
            market_question=market.question,
        )
        filters.extend(n_filters)
    except Exception as e:
        logger.error("noise_filter failed for %s: %s", wallet_address[:10], e)
    t_noise = time.time() - t0

    # ── 4e-iv. Arbitrage filter (N03, N04) ────────────────────
    t0 = time.time()
    try:
        arb_results = arb_filter.check(
            wallet_address=wallet_address,
            market_id=market.market_id,
            direction=direction,
            all_wallet_trades=wallet_trades,
        )
        filters.extend(arb_results)

        # If N03 triggered (-100 kill), discard this wallet
        if any(f.filter_id == "N03" for f in arb_results):
            logger.info(
                "  N03 arbitrage kill: wallet %s in %s",
                wallet_address[:10], market.market_id[:12],
            )
            return None
    except Exception as e:
        logger.error("arb_filter failed for %s: %s", wallet_address[:10], e)
    t_arb = time.time() - t0

    t_wallet_total = time.time() - t_wallet_start
    logger.debug(
        "  Wallet %s timing: total=%.1fs (chain=%.1fs, behavior=%.1fs, noise=%.1fs, arb=%.1fs)",
        wallet_address[:10], t_wallet_total,
        t_wallet_analysis, t_behavior, t_noise, t_arb,
    )

    # ── Build wallet data dict for alert ─────────────────────
    span_hours = (accum.last_trade - accum.first_trade).total_seconds() / 3600
    # Distinct markets this wallet traded in (for sniper/shotgun scoring)
    distinct_markets = len({t.market_id for t in all_trades if t.wallet_address == wallet_address})

    # Individual trade details for detailed Telegram format
    # CLOB returns the price of the token bought (YES or NO), no conversion needed.
    trade_details = []
    total_weighted_price = 0.0
    for t in accum.trades:
        trade_details.append({
            "amount": t.amount,
            "price": t.price,
            "timestamp": t.timestamp.isoformat(),
        })
        total_weighted_price += t.price * t.amount

    avg_entry = (
        total_weighted_price / accum.total_amount
        if accum.total_amount > 0
        else 0.0
    )

    wallet_info = {
        "address": wallet_address,
        "direction": direction,
        "total_amount": accum.total_amount,
        "trade_count": accum.trade_count,
        "time_span_hours": round(span_hours, 2),
        "distinct_markets": distinct_markets,
        "trades": trade_details,
        "avg_entry_price": round(avg_entry, 4),
        "first_trade_time": accum.first_trade.isoformat(),
        "last_trade_time": accum.last_trade.isoformat(),
    }

    return wallet_info, filters


def _publish_alert(
    *,
    alert: Alert,
    alert_id: int | None,
    is_whale: bool,
    db: SupabaseClient,
    twitter: TwitterBot,
    telegram: TelegramBot,
    counters: dict,
    dry_run: bool = False,
) -> None:
    """Publish an alert to Telegram and/or X."""
    star = alert.star_level or 0
    q = alert.market_question[:50] if alert.market_question else alert.market_id[:12]

    if dry_run:
        if star >= 4:
            whale_tag = " (whale)" if is_whale else ""
            logger.info(
                "[DRY-RUN] Would send Telegram%s: %d★ %s %s",
                whale_tag, star, alert.direction or "?", q,
            )
        if star >= 3:
            logger.info("[DRY-RUN] Would tweet: %d★ %s %s", star, alert.direction or "?", q)
        return

    # ── Insert AlertTracking for resolution tracking ──────
    if alert_id:
        try:
            tracking = AlertTracking(
                alert_id=alert_id,
                market_id=alert.market_id,
                direction=alert.direction or "YES",
                odds_at_alert=alert.odds_at_alert or 0.0,
            )
            db.upsert_alert_tracking(tracking)
        except Exception as e:
            logger.debug("upsert_alert_tracking failed: %s", e)

    # ── Insert WalletPositions for sell monitoring ─────────
    if alert_id and alert.wallets:
        for w in alert.wallets:
            try:
                pos = WalletPosition(
                    wallet_address=w.get("address", ""),
                    market_id=alert.market_id,
                    direction=alert.direction or "YES",
                    total_amount=w.get("total_amount", 0.0),
                    entry_odds=alert.odds_at_alert or 0.0,
                    alert_id=alert_id,
                )
                db.upsert_wallet_position(pos)
            except Exception as e:
                logger.debug("upsert_wallet_position failed: %s", e)

    # ── Publish to Telegram (4+ stars only) ──────────────────
    if star >= 4:
        if is_whale:
            try:
                msg_id = telegram.publish_whale_entry(alert)
                if msg_id and alert_id:
                    db.update_alert_published(alert_id, "telegram", msg_id)
                    counters["alerts_published_tg"] += 1
            except Exception as e:
                logger.error("Telegram whale publish failed: %s", e)
        else:
            try:
                msg_id = telegram.publish_alert(alert)
                if msg_id and alert_id:
                    db.update_alert_published(alert_id, "telegram", msg_id)
                    counters["alerts_published_tg"] += 1
            except Exception as e:
                logger.error("Telegram publish failed: %s", e)

        # Public Telegram channel
        try:
            telegram.publish_to_public(alert)
        except Exception as e:
            logger.debug("Public Telegram publish failed: %s", e)

    # 3+ stars → X (if can_publish)
    if star >= 3:
        try:
            tweet_id = twitter.publish_alert(alert)
            if tweet_id and alert_id:
                db.update_alert_published(alert_id, "x", tweet_id)
                counters["alerts_published_x"] += 1
        except Exception as e:
            logger.error("Twitter publish failed: %s", e)


# ── Entry point ──────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sentinel Alpha scanner")
    parser.add_argument(
        "--mode",
        choices=["quick", "deep"],
        default="quick",
        help="quick = current behavior (100 markets, 8min timeout); "
             "deep = all markets, no timeout, 10x parallel",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full pipeline but don't publish to Telegram/X or write alerts to Supabase",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=None,
        metavar="MINUTES",
        help="Override trade lookback window (default: 35min quick, 1440min deep)",
    )
    parser.add_argument(
        "--post-scan-check",
        action="store_true",
        help="Run net position check after scan to detect CTF merges, transfers, and burns "
             "(invisible to CLOB API). Adds ~30-60s. Skipped automatically in GitHub Actions.",
    )
    args = parser.parse_args()
    run_scan(
        mode=args.mode,
        dry_run=args.dry_run,
        lookback_override=args.lookback,
        post_scan_check=args.post_scan_check,
    )
