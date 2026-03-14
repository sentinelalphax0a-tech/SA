"""
VACUNA v3 — Apply: Aplica los resultados del dry-run a la DB.
=============================================================
DESTRUCTIVO — solo ejecutar después de revisar vaccine_dry_run_results.json.

Acciones:
  1. Para cada alerta con cambio de score/star: UPDATE score, star_level,
     total_amount, filters_triggered_initial, updated_at.
  2. Para alertas invalidadas (all-sells / no-data): UPDATE outcome='invalidated'.
  3. Alerta #7372 (false_confluence): SKIP — requiere revisión manual.

Fuente: system_audit/vaccine_dry_run_results.json
"""

import os
import sys
import json
import time
import re
import math
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(".env")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase import create_client
from src import config

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

DRY_RUN_JSON = os.path.join(os.path.dirname(__file__), "vaccine_dry_run_results.json")

# ── B18 / B28 config (same as dry-run) ────────────────────────────────────────
B18_TIERS = [
    ("B18d", "Acumulación muy fuerte",   config.ACCUM_VERY_STRONG_MIN,   None),
    ("B18c", "Acumulación fuerte",        config.ACCUM_STRONG_MIN,        config.ACCUM_VERY_STRONG_MIN),
    ("B18b", "Acumulación significativa", config.ACCUM_SIGNIFICANT_MIN,   config.ACCUM_STRONG_MIN),
    ("B18a", "Acumulación moderada",      config.ACCUM_MODERATE_MIN,      config.ACCUM_SIGNIFICANT_MIN),
]
B18_POINTS = {
    "B18d": config.FILTER_B18D["points"],
    "B18c": config.FILTER_B18C["points"],
    "B18b": config.FILTER_B18B["points"],
    "B18a": config.FILTER_B18A["points"],
}
ALLIN_EXTREME = config.ALLIN_EXTREME_MIN
ALLIN_STRONG  = config.ALLIN_STRONG_MIN
ALLIN_MIN_AMT = config.ALLIN_MIN_AMOUNT

REBUILD_IDS = {"B18a", "B18b", "B18c", "B18d", "B28a", "B28b"}


def parse_wallet_balance_from_b28(details: str) -> float | None:
    m = re.search(r"of \$([0-9,]+)", details or "")
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def b18_tier_for_amount(total: float) -> tuple[str, str, int] | None:
    for fid, name, lo, hi in B18_TIERS:
        if total >= lo and (hi is None or total < hi):
            return (fid, name, B18_POINTS[fid])
    return None


def b28_tier_for_ratio(ratio: float, total: float) -> tuple[str, str, int] | None:
    if total < ALLIN_MIN_AMT:
        return None
    if ratio >= ALLIN_EXTREME:
        return ("B28a", "All-in extremo", config.FILTER_B28A["points"])
    if ratio >= ALLIN_STRONG:
        return ("B28b", "All-in fuerte", config.FILTER_B28B["points"])
    return None


def rebuild_filters(
    stored_filters: list[dict],
    new_total: float,
    new_direction: str,
    added_ids: list[str],
    removed_ids: list[str],
) -> list[dict]:
    """
    Rebuild the filter list with corrected B18/B28.
    Returns the new list of filter dicts (serializable for DB).
    """
    wallet_balance: float | None = None

    # Pass 1: extract wallet balance from stored B28, collect non-rebuild filters
    base: list[dict] = []
    for f in stored_filters:
        fid = f.get("filter_id", "")
        if fid in REBUILD_IDS:
            if fid.startswith("B28") and wallet_balance is None:
                wallet_balance = parse_wallet_balance_from_b28(f.get("details", ""))
            continue  # drop all B18/B28 — will re-add below
        base.append(f)

    # Pass 2: re-add correct B18
    b18 = b18_tier_for_amount(new_total)
    if b18:
        fid, name, pts = b18
        base.append({
            "filter_id":   fid,
            "filter_name": name,
            "points":      pts,
            "category":    "behavior",
            "details":     f"accum=${new_total:,.0f}",
        })

    # Pass 3: re-add correct B28 if wallet balance known
    if wallet_balance and wallet_balance > 0 and new_total > 0:
        ratio = new_total / wallet_balance
        b28 = b28_tier_for_ratio(ratio, new_total)
        if b28:
            fid, name, pts = b28
            base.append({
                "filter_id":   fid,
                "filter_name": name,
                "points":      pts,
                "category":    "behavior",
                "details":     f"all-in {ratio*100:.0f}% of ${wallet_balance:,.0f}",
            })

    return base


# ── Main apply logic ───────────────────────────────────────────────────────────

def apply_vaccine():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
    now_iso = datetime.now(timezone.utc).isoformat()

    with open(DRY_RUN_JSON) as f:
        results = json.load(f)

    print(f"Loaded {len(results)} results from dry-run JSON.")

    # Categorize
    to_invalidate: list[dict] = []
    to_update: list[dict] = []
    to_skip: list[dict] = []

    for r in results:
        if "false_confluence" in r["flags"]:
            to_skip.append(r)
            continue
        if "invalidated" in r["flags"] or "all_sells_or_no_data" in r["flags"]:
            to_invalidate.append(r)
            continue
        # Update if score or star changed materially
        if (r["new_star"] != r["stored_star"]
                or abs(r["new_score"] - r["stored_score"]) >= 5
                or abs(r["new_total"] - r["stored_total"]) > 50):
            to_update.append(r)

    print(f"  To UPDATE (score/star change): {len(to_update)}")
    print(f"  To INVALIDATE (all-sells/no-data): {len(to_invalidate)}")
    print(f"  SKIP (false confluence): {len(to_skip)} "
          f"→ {[r['alert_id'] for r in to_skip]}")
    print()

    # ── Step 1: Fetch current filters for alerts to update ─────────────────
    update_ids = [r["alert_id"] for r in to_update]
    filters_by_id: dict[int, list[dict]] = {}

    if update_ids:
        print(f"Fetching current filters for {len(update_ids)} alerts...")
        PAGE = 500
        for i in range(0, len(update_ids), PAGE):
            batch = update_ids[i:i + PAGE]
            rows = (
                sb.table("alerts")
                .select("id,filters_triggered_initial")
                .in_("id", batch)
                .execute()
                .data or []
            )
            for row in rows:
                filters_by_id[row["id"]] = row.get("filters_triggered_initial") or []
            time.sleep(0.05)

    # ── Step 2: Apply UPDATEs ──────────────────────────────────────────────
    updated_ok = 0
    updated_err = 0

    for r in to_update:
        aid = r["alert_id"]
        stored_filters = filters_by_id.get(aid, [])

        new_filters = rebuild_filters(
            stored_filters=stored_filters,
            new_total=r["new_total"],
            new_direction=r["new_direction"],
            added_ids=r.get("added_filters", []),
            removed_ids=r.get("removed_filters", []),
        )

        try:
            sb.table("alerts").update({
                "score":                     r["new_score"],
                "star_level":                r["new_star"],
                "total_amount":              r["new_total"],
                "filters_triggered_initial": new_filters,
                "last_updated_at":           now_iso,
            }).eq("id", aid).execute()
            updated_ok += 1
            print(f"  UPDATED  #{aid:>6}  {r['stored_star']}★→{r['new_star']}★  "
                  f"score {r['stored_score']}→{r['new_score']}  "
                  f"total ${r['stored_total']:,.0f}→${r['new_total']:,.0f}")
        except Exception as e:
            updated_err += 1
            print(f"  ERROR    #{aid:>6}  {e}")
        time.sleep(0.05)

    # ── Step 3: Invalidate all-sells / no-data alerts ─────────────────────
    invalidated_ok = 0
    invalidated_err = 0

    for r in to_invalidate:
        aid = r["alert_id"]
        try:
            sb.table("alerts").update({
                "outcome":         "invalidated",
                "score":           0,
                "star_level":      0,
                "last_updated_at": now_iso,
            }).eq("id", aid).execute()
            invalidated_ok += 1
            print(f"  INVALID  #{aid:>6}  [{r['stored_star']}★]  "
                  f"flags={r['flags']}  total=${r['stored_total']:,.0f}")
        except Exception as e:
            invalidated_err += 1
            print(f"  ERROR    #{aid:>6}  {e}")
        time.sleep(0.05)

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("VACUNA v3 — APPLY COMPLETE")
    print("=" * 60)
    print(f"  Updated:     {updated_ok} ok, {updated_err} errors")
    print(f"  Invalidated: {invalidated_ok} ok, {invalidated_err} errors")
    print(f"  Skipped:     {len(to_skip)} (false confluence — manual review needed)")
    if to_skip:
        for r in to_skip:
            print(f"    #{r['alert_id']} [{r['stored_star']}★] — review manually")
    print()
    print(f"  Timestamp: {now_iso}")


if __name__ == "__main__":
    apply_vaccine()
