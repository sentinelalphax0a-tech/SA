"""
Audit False Confluence — identifies alerts inflated by infrastructure senders.

Scans pending alerts whose filters_triggered include C03d or C07, extracts
the sender address from the filter details, and checks whether that sender
belongs to KNOWN_INFRASTRUCTURE, KNOWN_EXCHANGES, KNOWN_BRIDGES, or
POLYMARKET_CONTRACTS.  If so, the alert is flagged as a *false confluence*
and a detailed report is printed.

**Read-only by default** — the script never modifies the DB unless the
operator explicitly confirms the recalculation prompt at the end.

Usage:
    python -m scripts.audit_false_confluence
"""

import logging
import math
import re
import sys
from collections import defaultdict

from src import config
from src.config import (
    KNOWN_BRIDGES,
    KNOWN_EXCHANGES,
    KNOWN_INFRASTRUCTURE,
    NEW_STAR_THRESHOLDS,
    OLD_TO_NEW_CATEGORY,
    STAR_VALIDATION,
)
from src.database.supabase_client import SupabaseClient
from src.scanner.blockchain_client import POLYMARKET_CONTRACTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Exclusion set (all lowercased) ────────────────────────────
EXCLUSION_SET: set[str] = set()
for _addr_dict in (KNOWN_INFRASTRUCTURE, KNOWN_EXCHANGES, KNOWN_BRIDGES):
    EXCLUSION_SET |= {a.lower() for a in _addr_dict}
EXCLUSION_SET |= {a.lower() for a in POLYMARKET_CONTRACTS}

# Build a prefix lookup (first 10 chars) for matching against
# the truncated addresses stored in filter details.
EXCLUSION_PREFIXES: dict[str, str] = {}  # prefix → full address
for addr in EXCLUSION_SET:
    prefix = addr[:10]
    EXCLUSION_PREFIXES[prefix] = addr

# Labels for human-readable output
_ALL_LABELS: dict[str, str] = {}
for _d in (KNOWN_INFRASTRUCTURE, KNOWN_EXCHANGES, KNOWN_BRIDGES):
    for _a, _lbl in _d.items():
        _ALL_LABELS[_a.lower()] = _lbl

# Filters that trigger the "false confluence" check
_TRIGGER_FILTERS = {"C03d", "C07"}

# Filters whose points are considered inflated when the alert is
# a confirmed false confluence.
_INFLATED_FILTERS = {"C01", "C02", "C03d", "C06", "C07"}

PAGE_SIZE = 500


# ── Helpers ───────────────────────────────────────────────────

def _extract_sender_prefix(details: str | None) -> str | None:
    """Extract the sender/distributor hex prefix from a filter's details string.

    Expected formats:
        "sender=0xf70da978…, 5 wallets"
        "distributor=0xf70da978…, 5 wallets funded"
        "sender=0xf70da978…, amounts≈$5,487±30%"
    """
    if not details:
        return None
    match = re.search(r"(?:sender|distributor)=(0x[a-fA-F0-9]+)", details)
    if match:
        return match.group(1).lower()
    return None


def _prefix_is_excluded(prefix: str) -> str | None:
    """If *prefix* matches an excluded address, return the full address."""
    return EXCLUSION_PREFIXES.get(prefix[:10])


def _score_to_stars(score: int) -> int:
    """Map final score → star level (0-5)."""
    for threshold, stars in NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0


def _get_categories(filters: list[dict]) -> set[str]:
    """Derive new scoring categories from a list of filter dicts."""
    cats: set[str] = set()
    for f in filters:
        pts = f.get("points", 0)
        if pts <= 0:
            continue
        cat = OLD_TO_NEW_CATEGORY.get(f.get("category", ""))
        if cat:
            cats.add(cat)
    return cats


def _validate_stars(
    star_level: int,
    categories: set[str],
    total_amount: float,
) -> int:
    """Downgrade star level if validation requirements are not met."""
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
    """Cap star level when N09 filters are present."""
    fids = {f.get("filter_id", "") for f in filters}
    if "N09a" in fids:
        return min(star_level, getattr(config, "OBVIOUS_BET_STAR_CAP_EXTREME", 2))
    if "N09b" in fids:
        return min(star_level, getattr(config, "OBVIOUS_BET_STAR_CAP_HIGH", 3))
    return star_level


def _recalculate(
    remaining_filters: list[dict],
    multiplier: float,
    total_amount: float,
) -> tuple[int, int, int]:
    """Recalculate score_raw, score_final, and star_level from remaining filters.

    Uses the alert's original multiplier (which already accounts for
    amount and diversity) so the only variable is the raw points.
    """
    score_raw = max(0, sum(f.get("points", 0) for f in remaining_filters))
    score_final = min(400, round(score_raw * multiplier))
    categories = _get_categories(remaining_filters)
    star_level = _score_to_stars(score_final)
    star_level = _validate_stars(star_level, categories, total_amount)
    star_level = _apply_obvious_bet_cap(star_level, remaining_filters)
    return score_raw, score_final, star_level


# ── Pagination helper ─────────────────────────────────────────

def _fetch_pending_alerts(db: SupabaseClient) -> list[dict]:
    """Fetch ALL pending alerts using pagination."""
    all_alerts: list[dict] = []
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
        all_alerts.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_alerts


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    db = SupabaseClient()
    logger.info("Conectando a Supabase…")
    db.test_connection()

    # 1. Fetch all pending alerts
    logger.info("Descargando alertas pendientes (paginado, %d por página)…", PAGE_SIZE)
    all_pending = _fetch_pending_alerts(db)
    logger.info("Total alertas pendientes: %d", len(all_pending))

    if not all_pending:
        print("\nNo hay alertas pendientes. Nada que auditar.")
        return

    # 2. Filter to alerts that have C03d or C07 in filters_triggered
    candidates: list[dict] = []
    for alert in all_pending:
        filters = alert.get("filters_triggered") or []
        fids = {f.get("filter_id", "") for f in filters}
        if fids & _TRIGGER_FILTERS:
            candidates.append(alert)

    logger.info(
        "Alertas con C03d/C07: %d de %d pendientes",
        len(candidates),
        len(all_pending),
    )

    if not candidates:
        print("\nNinguna alerta pendiente tiene C03d o C07. Nada que auditar.")
        return

    # 3. For each candidate, extract senders and cross-check
    false_confluences: list[dict] = []  # enriched alert dicts

    for alert in candidates:
        filters = alert.get("filters_triggered") or []
        matched_senders: dict[str, str] = {}  # full_addr → label

        for f in filters:
            fid = f.get("filter_id", "")
            if fid not in _TRIGGER_FILTERS:
                continue
            prefix = _extract_sender_prefix(f.get("details"))
            if prefix is None:
                continue
            full_addr = _prefix_is_excluded(prefix)
            if full_addr:
                label = _ALL_LABELS.get(full_addr, full_addr)
                matched_senders[full_addr] = label

        if not matched_senders:
            continue

        # Calculate inflated points
        inflated_points = 0
        inflated_filter_ids: list[str] = []
        remaining_filters: list[dict] = []

        for f in filters:
            fid = f.get("filter_id", "")
            if fid in _INFLATED_FILTERS:
                inflated_points += f.get("points", 0)
                inflated_filter_ids.append(fid)
            else:
                remaining_filters.append(f)

        # Recalculate without inflated filters
        multiplier = alert.get("multiplier", 1.0)
        total_amount = alert.get("total_amount", 0.0) or 0.0
        new_raw, new_final, new_stars = _recalculate(
            remaining_filters, multiplier, total_amount,
        )

        old_stars = alert.get("star_level", 0) or 0
        star_change = old_stars != new_stars

        false_confluences.append({
            "alert": alert,
            "matched_senders": matched_senders,
            "inflated_points": inflated_points,
            "inflated_filter_ids": inflated_filter_ids,
            "remaining_filters": remaining_filters,
            "new_score_raw": new_raw,
            "new_score_final": new_final,
            "new_star_level": new_stars,
            "star_changed": star_change,
        })

    # ── Report ────────────────────────────────────────────────

    print("\n" + "=" * 70)
    print("  INFORME DE AUDITORÍA — CONFLUENCIAS FALSAS")
    print("=" * 70)
    print(f"\n  Total alertas pendientes:          {len(all_pending)}")
    print(f"  Alertas con C03d/C07:              {len(candidates)}")
    print(f"  Alertas con confluencia falsa:     {len(false_confluences)}")

    if not false_confluences:
        print("\n  ✓ No se detectaron confluencias falsas.")
        print("=" * 70)
        return

    star_downgrades = [fc for fc in false_confluences if fc["star_changed"]]
    print(f"  Alertas que bajarían de estrellas:  {len(star_downgrades)}")

    print("\n" + "-" * 70)
    print("  DETALLE POR ALERTA")
    print("-" * 70)

    for fc in false_confluences:
        alert = fc["alert"]
        aid = alert.get("id", "?")
        mkt = alert.get("market_question") or alert.get("market_id", "?")
        if isinstance(mkt, str) and len(mkt) > 60:
            mkt = mkt[:57] + "…"

        old_score = alert.get("score", 0) or 0
        old_raw = alert.get("score_raw", 0) or 0
        old_stars = alert.get("star_level", 0) or 0

        print(f"\n  Alert #{aid}")
        print(f"    Mercado:  {mkt}")
        print(f"    Senders falsos:")
        for addr, label in fc["matched_senders"].items():
            print(f"      • {addr[:10]}… → {label}")
        print(f"    Filtros inflados:  {', '.join(fc['inflated_filter_ids'])}  "
              f"(+{fc['inflated_points']} pts)")
        print(f"    Score actual:      {old_score} (raw {old_raw}) — "
              f"{'★' * old_stars}{'☆' * (5 - old_stars)}")
        print(f"    Score estimado:    {fc['new_score_final']} "
              f"(raw {fc['new_score_raw']}) — "
              f"{'★' * fc['new_star_level']}{'☆' * (5 - fc['new_star_level'])}")
        if fc["star_changed"]:
            print(f"    ⚠ BAJARÍA de {old_stars}★ a {fc['new_star_level']}★")

    # Summary table
    print("\n" + "-" * 70)
    print("  RESUMEN")
    print("-" * 70)
    total_inflated = sum(fc["inflated_points"] for fc in false_confluences)
    print(f"  Puntos inflados totales:   {total_inflated}")
    print(f"  Promedio por alerta:       {total_inflated / len(false_confluences):.1f}")
    if star_downgrades:
        print(f"\n  Alertas que bajarían de nivel:")
        for fc in star_downgrades:
            a = fc["alert"]
            print(f"    Alert #{a.get('id', '?')}: "
                  f"{a.get('star_level', 0)}★ → {fc['new_star_level']}★")

    print("\n" + "=" * 70)

    # ── Optional DB update ────────────────────────────────────

    try:
        answer = input(
            "\n¿Quieres recalcular los scores afectados? (s/n): "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAbortado.")
        return

    if answer != "s":
        print("No se modificó la base de datos.")
        return

    print(f"\nActualizando {len(false_confluences)} alertas…")
    updated = 0
    errors = 0

    for fc in false_confluences:
        alert = fc["alert"]
        aid = alert.get("id")
        if aid is None:
            continue

        new_filters = fc["remaining_filters"]

        try:
            db.update_alert_fields(aid, {
                "score_raw": fc["new_score_raw"],
                "score": fc["new_score_final"],
                "star_level": fc["new_star_level"],
                "filters_triggered": new_filters,
            })
            updated += 1
            logger.info("Alert #%s actualizada: score %d → %d, stars %d → %d",
                        aid,
                        alert.get("score", 0),
                        fc["new_score_final"],
                        alert.get("star_level", 0),
                        fc["new_star_level"])
        except Exception as exc:
            errors += 1
            logger.error("Error actualizando alert #%s: %s", aid, exc)

    print(f"\n✓ {updated} alertas actualizadas, {errors} errores.")


if __name__ == "__main__":
    main()
