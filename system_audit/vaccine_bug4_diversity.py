"""
VACUNA Bug 4 — distinct_markets siempre=1
==========================================
Corrige todas las alertas pending cuyos scores fueron calculados con
diversity_multiplier=1.2× (sniper) cuando el wallet en realidad opera
en múltiples mercados.

Modos:
  python system_audit/vaccine_bug4_diversity.py            → dry-run
  python system_audit/vaccine_bug4_diversity.py --apply    → aplica cambios en DB

Dry-run: imprime resumen + guarda vaccine_bug4_results.json
Apply:   lee vaccine_bug4_results.json y aplica UPDATEs

Throttling: BATCH_SIZE alertas, BATCH_PAUSE segundos entre batches.
"""

import json
import math
import os
import sys
import time
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(".env")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config
from src.scanner.polymarket_client import PolymarketClient
from src.database.supabase_client import SupabaseClient

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

RESULTS_JSON = os.path.join(os.path.dirname(__file__), "vaccine_bug4_results.json")
BATCH_SIZE   = 50
BATCH_PAUSE  = 2.0    # seconds between batches
API_THROTTLE = 0.15   # seconds between per-wallet API calls
SCORE_CAP    = 400

# ── Scoring helpers ────────────────────────────────────────────────────────────

def get_diversity_mult(n: int | None) -> float:
    if n is None:
        return 1.0
    if n >= config.DIVERSITY_SUPER_SHOTGUN_MIN:
        return config.DIVERSITY_SUPER_SHOTGUN_MULTIPLIER
    if n >= config.DIVERSITY_SHOTGUN_MIN_MARKETS:
        return config.DIVERSITY_SHOTGUN_MULTIPLIER
    if n <= config.DIVERSITY_SNIPER_MAX_MARKETS:
        return config.DIVERSITY_SNIPER_MULTIPLIER
    return 1.0

def diversity_label(n: int) -> str:
    if n >= config.DIVERSITY_SUPER_SHOTGUN_MIN:
        return "super-shotgun"
    if n >= config.DIVERSITY_SHOTGUN_MIN_MARKETS:
        return "shotgun"
    if n <= config.DIVERSITY_SNIPER_MAX_MARKETS:
        return "sniper"
    return "neutral"

def score_to_stars(score: int) -> int:
    for threshold, stars in config.NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0

# ── Dry-run: fetch real distinct_markets and compute new scores ────────────────

def run_dry_run(pm: PolymarketClient, db: SupabaseClient) -> list[dict]:
    """Fetch all pending alerts, compute corrected scores, return result list."""
    print("Fetching all pending alerts...")
    alerts = db.get_alerts_pending()
    print(f"  Total pending: {len(alerts)}")

    results = []
    errors  = 0
    total_alerts = len(alerts)

    for batch_start in range(0, total_alerts, BATCH_SIZE):
        batch = alerts[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = math.ceil(total_alerts / BATCH_SIZE)
        print(f"\n  Batch {batch_num}/{total_batches} "
              f"(alerts {batch_start+1}–{min(batch_start+BATCH_SIZE, total_alerts)})...")

        for alert in batch:
            aid          = alert.get("id")
            score_raw    = alert.get("score_raw") or 0
            stored_mult  = alert.get("multiplier") or 1.2
            stored_score = alert.get("score") or 0
            stored_star  = alert.get("star_level") or 0
            wallets_json = alert.get("wallets") or []

            # ── Extract wallet addresses ──────────────────────────────────────
            addresses = [
                w["address"] for w in wallets_json
                if isinstance(w, dict) and "address" in w
            ]
            if not addresses:
                # Fallback: no wallets in JSON → can't correct, skip
                results.append({
                    "alert_id":     aid,
                    "stored_star":  stored_star,
                    "stored_score": stored_score,
                    "stored_mult":  stored_mult,
                    "new_star":     stored_star,
                    "new_score":    stored_score,
                    "new_mult":     stored_mult,
                    "max_distinct": 1,
                    "old_distinct": 1,
                    "changed":      False,
                    "skip_reason":  "no_wallets",
                    "wallets_updated": [],
                })
                continue

            # ── Fetch real distinct_markets per wallet ────────────────────────
            updated_wallets = []
            max_distinct    = 1   # fallback = bug behavior

            for w in wallets_json:
                if not isinstance(w, dict):
                    updated_wallets.append(w)
                    continue

                addr = w.get("address", "")
                old_dm = w.get("distinct_markets", 1)

                if addr:
                    time.sleep(API_THROTTLE)
                    h = pm.get_wallet_pm_history_cached(addr)
                    if h is not None:
                        real_dm = h.get("distinct_markets") or 1
                    else:
                        real_dm = old_dm  # keep existing if API fails

                    max_distinct = max(max_distinct, real_dm)
                    updated_wallets.append({**w, "distinct_markets": real_dm})
                else:
                    updated_wallets.append(w)

            # ── Recalculate score ─────────────────────────────────────────────
            # stored_mult = amount_mult × 1.2 (bug: diversity always = sniper)
            amount_mult  = round(stored_mult / 1.2, 6)
            new_div_mult = get_diversity_mult(max_distinct)
            new_mult     = round(amount_mult * new_div_mult, 2)
            new_score    = min(SCORE_CAP, round(score_raw * new_mult))
            new_star     = score_to_stars(new_score)

            # Opción A: el fix solo puede BAJAR multiplicadores, nunca subirlos.
            # Cap ambos para evitar upgrades por artefactos del recálculo
            # (e.g., score inconsistente con stored_star por _validate_stars histórico).
            new_score = min(new_score, stored_score)
            new_star  = min(new_star, stored_star)

            changed = (new_star != stored_star) or (abs(new_score - stored_score) >= 5)

            results.append({
                "alert_id":        aid,
                "stored_star":     stored_star,
                "stored_score":    stored_score,
                "stored_mult":     stored_mult,
                "new_star":        new_star,
                "new_score":       new_score,
                "new_mult":        new_mult,
                "max_distinct":    max_distinct,
                "old_distinct":    1,           # was always 1 due to bug
                "changed":         changed,
                "skip_reason":     None,
                "wallets_updated": updated_wallets,
            })

        if batch_start + BATCH_SIZE < total_alerts:
            print(f"  Pausing {BATCH_PAUSE}s between batches...")
            time.sleep(BATCH_PAUSE)

    return results


# ── Print dry-run summary ──────────────────────────────────────────────────────

def print_summary(results: list[dict]) -> None:
    changed   = [r for r in results if r["changed"]]
    unchanged = [r for r in results if not r["changed"]]
    skipped   = [r for r in results if r.get("skip_reason")]

    # Star transition matrix
    transitions: dict[tuple[int, int], int] = {}
    for r in changed:
        key = (r["stored_star"], r["new_star"])
        transitions[key] = transitions.get(key, 0) + 1

    print("\n" + "="*65)
    print("VACUNA Bug 4 — DRY-RUN SUMMARY")
    print("="*65)
    print(f"  Total pending:   {len(results)}")
    print(f"  Cambian:         {len(changed)}")
    print(f"  Sin cambio:      {len(unchanged)}")
    print(f"  Sin wallets:     {len(skipped)}")

    print(f"\n  Transiciones de estrellas:")
    for (old, new), count in sorted(transitions.items(), reverse=True):
        direction = "↓" if new < old else ("↑" if new > old else "=")
        print(f"    {old}★ → {new}★  {direction}  ×{count}")

    # Per-star breakdown
    star_counts_old: dict[int, int] = {}
    star_counts_new: dict[int, int] = {}
    for r in results:
        star_counts_old[r["stored_star"]] = star_counts_old.get(r["stored_star"], 0) + 1
        star_counts_new[r["new_star"]]    = star_counts_new.get(r["new_star"], 0) + 1

    print(f"\n  Distribución antes → después:")
    all_stars = sorted(set(list(star_counts_old.keys()) + list(star_counts_new.keys())), reverse=True)
    for s in all_stars:
        old_n = star_counts_old.get(s, 0)
        new_n = star_counts_new.get(s, 0)
        diff  = new_n - old_n
        diff_str = f" ({'+' if diff >= 0 else ''}{diff})" if diff != 0 else ""
        print(f"    {s}★:  {old_n:>4} → {new_n:>4}{diff_str}")

    # Show changed alerts detail (sorted by star drop severity)
    print(f"\n  Detalle de las {len(changed)} alertas que cambian:")
    print(f"  {'ID':>6}  {'Mkts':>4}  {'MultOld':>7}  {'MultNew':>7}  "
          f"{'ScOld':>6}  {'ScNew':>6}  {'Stars':>8}")
    print("  " + "-"*62)
    for r in sorted(changed, key=lambda x: (x["stored_star"] - x["new_star"]), reverse=True):
        print(f"  {r['alert_id']:>6}  {r['max_distinct']:>4}  "
              f"{r['stored_mult']:>7.2f}  {r['new_mult']:>7.2f}  "
              f"{r['stored_score']:>6}  {r['new_score']:>6}  "
              f"{r['stored_star']}★→{r['new_star']}★")


# ── Apply: write changes to DB ─────────────────────────────────────────────────

def run_apply(db: SupabaseClient) -> None:
    if not os.path.exists(RESULTS_JSON):
        print(f"ERROR: {RESULTS_JSON} not found. Run dry-run first.")
        sys.exit(1)

    with open(RESULTS_JSON) as f:
        results = json.load(f)

    to_update = [r for r in results if r.get("changed") and not r.get("skip_reason")]
    print(f"Loaded {len(results)} results. Applying {len(to_update)} changes...")

    now_iso   = datetime.now(timezone.utc).isoformat()
    ok_count  = 0
    err_count = 0

    for batch_start in range(0, len(to_update), BATCH_SIZE):
        batch = to_update[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = math.ceil(len(to_update) / BATCH_SIZE)
        print(f"\n  Applying batch {batch_num}/{total_batches}...")

        for r in batch:
            aid         = r["alert_id"]
            old_star    = r["stored_star"]
            new_star    = r["new_star"]
            old_score   = r["stored_score"]
            new_score   = r["new_score"]
            new_mult    = r["new_mult"]
            old_dm      = r["old_distinct"]
            new_dm      = r["max_distinct"]
            new_wallets = r["wallets_updated"]

            change_reason = (
                f"♻️ VACUNA bug4: distinct_markets {old_dm}→{new_dm}, "
                f"mult {r['stored_mult']:.2f}×→{new_mult:.2f}×"
            )

            try:
                db.update_alert_fields(aid, {
                    "score":           new_score,
                    "star_level":      new_star,
                    "multiplier":      new_mult,
                    "wallets":         new_wallets,
                    "last_updated_at": now_iso,
                })
                db.log_score_history(
                    alert_id=aid,
                    old_star_level=old_star,
                    new_star_level=new_star,
                    old_score=old_score,
                    new_score=new_score,
                    change_reason=change_reason,
                )
                ok_count += 1
                print(f"  UPDATED #{aid:>6}  mkts={new_dm:>3}  "
                      f"mult {r['stored_mult']:.2f}×→{new_mult:.2f}×  "
                      f"score {old_score}→{new_score}  {old_star}★→{new_star}★")
            except Exception as e:
                err_count += 1
                print(f"  ERROR   #{aid:>6}  {e}")

            time.sleep(0.05)

        if batch_start + BATCH_SIZE < len(to_update):
            print(f"  Pausing {BATCH_PAUSE}s...")
            time.sleep(BATCH_PAUSE)

    print("\n" + "="*65)
    print("VACUNA Bug 4 — APPLY COMPLETE")
    print("="*65)
    print(f"  Updated OK:  {ok_count}")
    print(f"  Errors:      {err_count}")
    print(f"  Unchanged:   {len(results) - len(to_update)}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    apply_mode = "--apply" in sys.argv

    if apply_mode:
        print("VACUNA Bug 4 — APPLY MODE")
        db = SupabaseClient()
        run_apply(db)
    else:
        print("VACUNA Bug 4 — DRY-RUN MODE (no DB changes)")
        print("Run with --apply to commit changes.\n")
        pm = PolymarketClient()
        db = SupabaseClient()
        results = run_dry_run(pm, db)
        print_summary(results)
        with open(RESULTS_JSON, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n  Resultados guardados en {RESULTS_JSON}")
        print("  Para aplicar: python system_audit/vaccine_bug4_diversity.py --apply")


if __name__ == "__main__":
    main()
