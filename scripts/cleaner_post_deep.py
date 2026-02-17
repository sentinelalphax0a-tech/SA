"""
Cleaner Post-Deep Scan — automatic cleanup of false alerts.

Runs after each deep scan.  Fully automatic, no user input.

Steps:
  1. Detect infrastructure senders (>100 wallets) and persist to
     ``detected_infrastructure`` table.
  2. Audit every pending alert that has C03d / C07 / C06 / C01 / C02
     filters, expand truncated sender addresses via wallet_funding,
     and remove filters whose sender is in the exclusion set.
     Recalculate score / star_level and update Supabase.
  3. Print a summary report.

Usage:
    python -m scripts.cleaner_post_deep
"""

import logging
import re
import sys
# import time  # only needed for paso 2B
from collections import defaultdict
from datetime import datetime, timezone

from src import config
from src.config import (
    KNOWN_BRIDGES,
    KNOWN_EXCHANGES,
    KNOWN_INFRASTRUCTURE,
    NEW_STAR_THRESHOLDS,
    OLD_TO_NEW_CATEGORY,
    SENDER_AUTO_EXCLUDE_MIN_WALLETS,
    STAR_VALIDATION,
)
from src.database.supabase_client import SupabaseClient
from src.scanner.blockchain_client import POLYMARKET_CONTRACTS
# from src.scanner.polymarket_client import PolymarketClient  # only needed for paso 2B

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PAGE_SIZE = 500

# Filters that can carry a sender/distributor address in their details
_SENDER_FILTERS = {"C03d", "C07", "C06"}
# All confluence filters eligible for removal
_ALL_C_FILTERS = {"C01", "C02", "C03d", "C06", "C07"}

# PASO 2B — Limpieza de filtros con datos de lookback incorrectos
# Este paso fue necesario una sola vez para corregir alertas generadas
# antes del fix de W04/W05/W09/B20/B23/B28/N06 (commit d9954ce).
# Los filtros ya están corregidos en producción.
# Descomentar solo si se necesita re-ejecutar por algún motivo.
#
# _LOOKBACK_FILTERS = {
#     "W04", "W05", "W09", "B20",
#     "B23a", "B23b", "B28a", "B28b",
#     "N06a", "N06b", "N06c",
# }
# _N06_FILTERS = {"N06a", "N06b", "N06c"}


# ── Helpers ───────────────────────────────────────────────────


def _build_config_exclusion_set() -> set[str]:
    """All addresses from static config lists, lowercased."""
    s: set[str] = set()
    for d in (KNOWN_INFRASTRUCTURE, KNOWN_EXCHANGES, KNOWN_BRIDGES):
        s |= {a.lower() for a in d}
    s |= {a.lower() for a in POLYMARKET_CONTRACTS}
    return s


def _extract_sender_prefix(details: str | None) -> str | None:
    """Extract the hex prefix from filter details text.

    Patterns:
        "sender=0xf70da978…, 3 wallets"
        "distributor=0x3a3bd7bb…, 4 wallets funded"
        "sender=0xf70da978…, amounts≈$118,562±30%"
    """
    if not details:
        return None
    m = re.search(r"(?:sender|distributor)=(0x[a-fA-F0-9]+)", details)
    return m.group(1).lower() if m else None


def _score_to_stars(score: int) -> int:
    for threshold, stars in NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0


def _get_categories(filters: list[dict]) -> set[str]:
    cats: set[str] = set()
    for f in filters:
        if f.get("points", 0) <= 0:
            continue
        cat = OLD_TO_NEW_CATEGORY.get(f.get("category", ""))
        if cat:
            cats.add(cat)
    return cats


def _validate_stars(star_level: int, categories: set[str],
                    total_amount: float) -> int:
    while star_level >= 3:
        reqs = STAR_VALIDATION.get(star_level)
        if reqs is None:
            break
        if len(categories) < reqs.get("min_categories", 0):
            star_level -= 1
            continue
        if total_amount < reqs.get("min_amount", 0):
            star_level -= 1
            continue
        if reqs.get("requires_coord", False) and "COORDINATION" not in categories:
            star_level -= 1
            continue
        break
    return star_level


def _apply_obvious_bet_cap(star_level: int, filters: list[dict]) -> int:
    fids = {f.get("filter_id", "") for f in filters}
    if "N09a" in fids:
        return min(star_level, getattr(config, "OBVIOUS_BET_STAR_CAP_EXTREME", 2))
    if "N09b" in fids:
        return min(star_level, getattr(config, "OBVIOUS_BET_STAR_CAP_HIGH", 3))
    return star_level


def _recalculate(remaining: list[dict], multiplier: float,
                 total_amount: float) -> tuple[int, int, int]:
    """Return (score_raw, score_final, star_level) from surviving filters."""
    score_raw = max(0, sum(f.get("points", 0) for f in remaining))
    score_final = min(400, round(score_raw * multiplier))
    cats = _get_categories(remaining)
    stars = _score_to_stars(score_final)
    stars = _validate_stars(stars, cats, total_amount)
    stars = _apply_obvious_bet_cap(stars, remaining)
    return score_raw, score_final, stars


# ── Pagination helpers ────────────────────────────────────────


def _fetch_all_pending(db: SupabaseClient) -> list[dict]:
    """Paginate all pending alerts."""
    out: list[dict] = []
    offset = 0
    while True:
        resp = (
            db.client.table("alerts")
            .select("*")
            .eq("outcome", "pending")
            .order("id")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = resp.data or []
        out.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return out


def _fetch_sender_counts(db: SupabaseClient) -> dict[str, int]:
    """Paginate wallet_funding and return {sender → distinct wallet count}."""
    sender_wallets: dict[str, set[str]] = {}
    offset = 0
    while True:
        resp = (
            db.client.table("wallet_funding")
            .select("sender_address,wallet_address")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        rows = resp.data or []
        for r in rows:
            sa = r.get("sender_address")
            wa = r.get("wallet_address")
            if sa and wa:
                if sa not in sender_wallets:
                    sender_wallets[sa] = set()
                sender_wallets[sa].add(wa)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return {s: len(ws) for s, ws in sender_wallets.items()}


def _build_prefix_to_full(sender_counts: dict[str, int]) -> dict[str, str]:
    """Map 10-char hex prefix → full address, preferring highest wallet count
    when collisions occur."""
    prefix_map: dict[str, str] = {}
    for addr in sorted(sender_counts, key=lambda a: sender_counts[a], reverse=True):
        prefix = addr[:10].lower()
        if prefix not in prefix_map:
            prefix_map[prefix] = addr.lower()
    return prefix_map


# ── Step 1: Detect infrastructure senders ─────────────────────


def step1_detect_senders(
    db: SupabaseClient,
    sender_counts: dict[str, int],
    config_excluded: set[str],
) -> tuple[set[str], list[dict]]:
    """Detect new infra senders and persist to detected_infrastructure.

    Returns (all_excluded, new_senders_info).
    """
    threshold = SENDER_AUTO_EXCLUDE_MIN_WALLETS
    high_fanout = {
        s.lower(): c for s, c in sender_counts.items() if c >= threshold
    }

    # Separate known vs new
    new_senders: list[dict] = []
    for addr, count in high_fanout.items():
        if addr not in config_excluded:
            new_senders.append({"sender_address": addr, "wallet_count": count})

    # Upsert new senders to detected_infrastructure (table may not exist yet)
    now = datetime.now(timezone.utc).isoformat()
    table_ok = True
    for info in new_senders:
        if not table_ok:
            break
        try:
            db.client.table("detected_infrastructure").upsert(
                {
                    "sender_address": info["sender_address"],
                    "wallet_count": info["wallet_count"],
                    "detected_at": now,
                    "added_to_config": False,
                },
                on_conflict="sender_address",
            ).execute()
        except Exception as exc:
            err_msg = str(exc)
            if "PGRST205" in err_msg or "could not find" in err_msg.lower():
                logger.warning(
                    "Table detected_infrastructure does not exist — "
                    "skipping persistence.  Create it with:\n"
                    "  CREATE TABLE detected_infrastructure (\n"
                    "    id SERIAL PRIMARY KEY,\n"
                    "    sender_address TEXT UNIQUE NOT NULL,\n"
                    "    wallet_count INTEGER NOT NULL,\n"
                    "    detected_at TIMESTAMPTZ DEFAULT NOW(),\n"
                    "    added_to_config BOOLEAN DEFAULT FALSE,\n"
                    "    notes TEXT\n"
                    "  );"
                )
                table_ok = False
            else:
                logger.warning(
                    "Failed to upsert detected_infrastructure for %s: %s",
                    info["sender_address"][:12], exc,
                )

    all_excluded = config_excluded | {s["sender_address"] for s in new_senders}
    return all_excluded, new_senders


# ── Step 2: Audit & clean alerts ──────────────────────────────


def step2_clean_alerts(
    db: SupabaseClient,
    all_excluded: set[str],
    prefix_to_full: dict[str, str],
) -> tuple[list[dict], int]:
    """Audit pending alerts and clean false confluence filters.

    Returns (modifications, total_reviewed).
    """
    alerts = _fetch_all_pending(db)
    modifications: list[dict] = []

    for alert in alerts:
        filters = alert.get("filters_triggered") or []
        fids_present = {f.get("filter_id", "") for f in filters}

        # Skip alerts without any C filter
        if not fids_present & _ALL_C_FILTERS:
            continue

        # --- Identify which sender-bearing filters are false ---
        false_filter_ids: set[str] = set()

        for f in filters:
            fid = f.get("filter_id", "")
            if fid not in _SENDER_FILTERS:
                continue
            prefix = _extract_sender_prefix(f.get("details"))
            if prefix is None:
                continue
            # Expand prefix → full address
            full = prefix_to_full.get(prefix[:10])
            if full is None:
                logger.debug(
                    "Cannot expand prefix %s for alert #%s filter %s — skipping",
                    prefix, alert.get("id"), fid,
                )
                continue
            if full in all_excluded:
                false_filter_ids.add(fid)

        if not false_filter_ids:
            continue

        # --- Decide which C01/C02 to remove ---
        # If C03d or C07 was false ⇒ the "shared sender" signal is gone.
        # C01/C02 count wallets from these senders, so remove them too.
        if false_filter_ids & {"C03d", "C07"}:
            for fid in ("C01", "C02"):
                if fid in fids_present:
                    false_filter_ids.add(fid)

        # C06 already handled above (it has sender in details)

        # --- Build surviving filter list ---
        removed_filters: list[dict] = []
        remaining_filters: list[dict] = []
        for f in filters:
            if f.get("filter_id", "") in false_filter_ids:
                removed_filters.append(f)
            else:
                remaining_filters.append(f)

        if not removed_filters:
            continue

        # --- Recalculate ---
        multiplier = alert.get("multiplier", 1.0)
        total_amount = alert.get("total_amount", 0.0) or 0.0
        new_raw, new_final, new_stars = _recalculate(
            remaining_filters, multiplier, total_amount,
        )

        old_score = alert.get("score", 0) or 0
        old_raw = alert.get("score_raw", 0) or 0
        old_stars = alert.get("star_level", 0) or 0

        # Only update if something actually changed
        if new_final == old_score and new_stars == old_stars:
            continue

        # --- Update DB ---
        aid = alert.get("id")
        try:
            db.update_alert_fields(aid, {
                "score": new_final,
                "score_raw": new_raw,
                "star_level": new_stars,
                "filters_triggered": remaining_filters,
            })
        except Exception as exc:
            logger.error("Failed to update alert #%s: %s", aid, exc)
            continue

        modifications.append({
            "alert_id": aid,
            "market_question": alert.get("market_question", "?"),
            "old_score": old_score,
            "new_score": new_final,
            "old_stars": old_stars,
            "new_stars": new_stars,
            "removed": [
                f"{f.get('filter_id')}({f.get('points', 0):+d})"
                for f in removed_filters
            ],
        })

    return modifications, len(alerts)


# ── Step 2B helpers (commented out — one-time use) ────────────
#
# PASO 2B — Limpieza de filtros con datos de lookback incorrectos
# Este paso fue necesario una sola vez para corregir alertas generadas
# antes del fix de W04/W05/W09/B20/B23/B28/N06 (commit d9954ce).
# Los filtros ya están corregidos en producción.
# Descomentar solo si se necesita re-ejecutar por algún motivo.
#
# def _extract_balance_from_details(
#     filters: list[dict], filter_id: str,
# ) -> float | None:
#     """Extract wallet balance from B28/B23 filter details."""
#     for f in filters:
#         if f.get("filter_id") != filter_id:
#             continue
#         details = f.get("details", "")
#         m = re.search(r"\$([0-9,]+)", details)
#         if m:
#             try:
#                 return float(m.group(1).replace(",", ""))
#             except ValueError:
#                 pass
#     return None
#
#
# def _determine_n06_tier(non_pm_count: int) -> str | None:
#     """Return the correct N06 filter_id for a given non-political market count."""
#     if non_pm_count >= config.DEGEN_HEAVY_MIN:
#         return "N06c"
#     if non_pm_count >= config.DEGEN_LIGHT_MAX + 1:
#         return "N06b"
#     if non_pm_count >= 1:
#         return "N06a"
#     return None
#
#
# def _build_n06_filter(filter_id: str, non_pm_count: int) -> dict:
#     """Build a filter dict for a given N06 tier."""
#     filt_map = {
#         "N06a": config.FILTER_N06A,
#         "N06b": config.FILTER_N06B,
#         "N06c": config.FILTER_N06C,
#     }
#     filt = filt_map[filter_id]
#     return {
#         "filter_id": filt["id"],
#         "filter_name": filt["name"],
#         "points": filt["points"],
#         "category": filt["category"],
#         "details": f"non_pm_markets={non_pm_count} (real)",
#     }
#
#
# def step2b_clean_lookback_filters(
#     db: SupabaseClient,
#     pm_client: PolymarketClient,
# ) -> tuple[list[dict], int]:
#     """Clean filters that depend on lookback data using real PM history."""
#     alerts = _fetch_all_pending(db)
#     modifications: list[dict] = []
#     seen_wallets: set[str] = set()
#
#     for alert in alerts:
#         filters = alert.get("filters_triggered") or []
#         fids_present = {f.get("filter_id", "") for f in filters}
#
#         if not fids_present & _LOOKBACK_FILTERS:
#             continue
#
#         wallets = alert.get("wallets") or []
#         if not wallets:
#             continue
#         primary = max(wallets, key=lambda w: w.get("total_amount", 0))
#         wallet_address = primary.get("address")
#         if not wallet_address:
#             continue
#
#         addr_lower = wallet_address.lower()
#         if addr_lower not in seen_wallets:
#             seen_wallets.add(addr_lower)
#             time.sleep(0.1)
#
#         try:
#             history = pm_client.get_wallet_pm_history_cached(wallet_address)
#         except Exception as e:
#             logger.warning(
#                 "PM history failed for %s (alert #%s): %s",
#                 wallet_address[:10], alert.get("id"), e,
#             )
#             continue
#         if history is None:
#             continue
#
#         real_markets = history.get("distinct_markets", 0)
#         total_volume = history.get("total_volume", 0)
#         market_ids = history.get("market_ids", [])
#
#         remove_ids: set[str] = set()
#         add_filters: list[dict] = []
#
#         if "W04" in fids_present and real_markets > config.W04_SUPPRESS_MARKETS:
#             remove_ids.add("W04")
#         if "W05" in fids_present and real_markets > config.W05_SUPPRESS_MARKETS:
#             remove_ids.add("W05")
#         if "W09" in fids_present and real_markets > config.W04_SUPPRESS_MARKETS:
#             remove_ids.add("W09")
#         if "B20" in fids_present and real_markets > config.OLD_WALLET_PM_MIN_MARKETS:
#             remove_ids.add("B20")
#
#         for b28 in ("B28a", "B28b"):
#             if b28 in fids_present:
#                 balance = _extract_balance_from_details(filters, b28)
#                 if (balance and balance > 0
#                         and total_volume > balance * config.ALLIN_VOLUME_SUPPRESS_RATIO):
#                     remove_ids.add(b28)
#
#         for b23 in ("B23a", "B23b"):
#             if b23 in fids_present:
#                 balance = _extract_balance_from_details(filters, b23)
#                 if (balance and balance > 0
#                         and total_volume > balance * config.ALLIN_VOLUME_SUPPRESS_RATIO):
#                     remove_ids.add(b23)
#
#         n06_present = fids_present & _N06_FILTERS
#         if n06_present and market_ids:
#             for mid in market_ids:
#                 if mid not in pm_client._market_question_cache:
#                     time.sleep(0.1)
#             real_non_pm = pm_client.count_non_political_markets(market_ids)
#             correct_tier = _determine_n06_tier(real_non_pm)
#             if correct_tier is None:
#                 remove_ids |= n06_present
#             elif correct_tier not in n06_present:
#                 remove_ids |= n06_present
#                 add_filters.append(_build_n06_filter(correct_tier, real_non_pm))
#
#         if not remove_ids:
#             continue
#
#         removed_filters: list[dict] = []
#         remaining_filters: list[dict] = []
#         for f in filters:
#             if f.get("filter_id", "") in remove_ids:
#                 removed_filters.append(f)
#             else:
#                 remaining_filters.append(f)
#         remaining_filters.extend(add_filters)
#
#         if not removed_filters and not add_filters:
#             continue
#
#         multiplier = alert.get("multiplier", 1.0)
#         total_amount = alert.get("total_amount", 0.0) or 0.0
#         new_raw, new_final, new_stars = _recalculate(
#             remaining_filters, multiplier, total_amount,
#         )
#
#         old_score = alert.get("score", 0) or 0
#         old_stars = alert.get("star_level", 0) or 0
#
#         if new_final == old_score and new_stars == old_stars and not add_filters:
#             continue
#
#         aid = alert.get("id")
#         try:
#             db.update_alert_fields(aid, {
#                 "score": new_final,
#                 "score_raw": new_raw,
#                 "star_level": new_stars,
#                 "filters_triggered": remaining_filters,
#             })
#         except Exception as exc:
#             logger.error("Failed to update alert #%s: %s", aid, exc)
#             continue
#
#         removed_desc = [
#             f"{f.get('filter_id')}({f.get('points', 0):+d})"
#             for f in removed_filters
#         ]
#         added_desc = [
#             f"+{f.get('filter_id')}({f.get('points', 0):+d})"
#             for f in add_filters
#         ]
#
#         modifications.append({
#             "alert_id": aid,
#             "market_question": alert.get("market_question", "?"),
#             "old_score": old_score,
#             "new_score": new_final,
#             "old_stars": old_stars,
#             "new_stars": new_stars,
#             "removed": removed_desc,
#             "added": added_desc,
#             "wallet": wallet_address[:12],
#             "real_markets": real_markets,
#             "total_volume": total_volume,
#         })
#
#     return modifications, len(alerts)


# ── Step 3: Report ────────────────────────────────────────────


def step3_report(
    config_count: int,
    new_senders: list[dict],
    total_reviewed: int,
    modifications: list[dict],
) -> None:
    print()
    print("=" * 60)
    print("  CLEANER POST-DEEP SCAN")
    print("=" * 60)

    # ── Paso 1: Senders ──
    print()
    print(f"  Senders excluidos (config): {config_count}")
    print(f"  Senders nuevos detectados (>{SENDER_AUTO_EXCLUDE_MIN_WALLETS} wallets): "
          f"{len(new_senders)}")
    for s in new_senders:
        addr = s["sender_address"]
        print(f"    - {addr[:12]}… ({s['wallet_count']} wallets) "
              f"→ guardado en DB")

    # ── Paso 2: Confluencia ──
    print()
    print(f"  Alertas revisadas (confluencia): {total_reviewed}")
    print(f"  Alertas modificadas (confluencia): {len(modifications)}")

    warnings: list[str] = []

    if modifications:
        print()
        for m in modifications:
            mkt = m["market_question"] or "?"
            if isinstance(mkt, str) and len(mkt) > 50:
                mkt = mkt[:47] + "…"
            print(f"  Alert #{m['alert_id']}: {mkt}")
            print(f"    Score: {m['old_score']} → {m['new_score']} | "
                  f"Stars: {m['old_stars']}★ → {m['new_stars']}★")
            print(f"    Filtros eliminados: {', '.join(m['removed'])}")
            if m.get("new_stars", 1) == 0 or m.get("new_score", 40) < 40:
                warnings.append(
                    f"  ⚠️  Alert #{m['alert_id']} bajó a {m['new_stars']}★ "
                    f"(score {m['new_score']}) — considerar eliminar"
                )

    if not modifications:
        print()
        print("  Sin alertas afectadas.")

    if warnings:
        print()
        for w in warnings:
            print(w)

    print()
    print("=" * 60)


# ── Main ──────────────────────────────────────────────────────


def main() -> None:
    db = SupabaseClient()
    logger.info("Conectando a Supabase…")
    db.test_connection()

    # Step 1 — Detect infrastructure senders
    logger.info("Paso 1: Detectando senders de infraestructura…")
    sender_counts = _fetch_sender_counts(db)
    logger.info("Total senders en wallet_funding: %d", len(sender_counts))

    config_excluded = _build_config_exclusion_set()
    all_excluded, new_senders = step1_detect_senders(
        db, sender_counts, config_excluded,
    )
    logger.info(
        "Senders conocidos: %d | Nuevos detectados: %d",
        len(config_excluded), len(new_senders),
    )

    # Build prefix → full address map from all senders in wallet_funding
    prefix_to_full = _build_prefix_to_full(sender_counts)

    # Step 2 — Audit & clean confluence alerts
    logger.info("Paso 2: Auditando alertas pendientes (confluencia)…")
    modifications, total_reviewed = step2_clean_alerts(
        db, all_excluded, prefix_to_full,
    )
    logger.info(
        "Revisadas: %d | Modificadas: %d",
        total_reviewed, len(modifications),
    )

    # Step 2B — desactivado (ya ejecutado, filtros corregidos en producción)
    # Descomentar solo si se necesita re-ejecutar:
    # from src.scanner.polymarket_client import PolymarketClient
    # logger.info("Paso 2B: Limpiando filtros con datos de lookback…")
    # pm_client = PolymarketClient()
    # lookback_mods, lookback_reviewed = step2b_clean_lookback_filters(
    #     db, pm_client,
    # )
    # logger.info(
    #     "Revisadas: %d | Corregidas: %d",
    #     lookback_reviewed, len(lookback_mods),
    # )

    # Step 3 — Report
    step3_report(
        config_count=len(config_excluded),
        new_senders=new_senders,
        total_reviewed=total_reviewed,
        modifications=modifications,
    )


if __name__ == "__main__":
    main()
