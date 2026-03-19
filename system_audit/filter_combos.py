"""
Sentinel Alpha — Filter Combination Analysis
=============================================
Encuentra combinaciones de filtros (pares y triples) con win rate inusualmente alto.
Trabaja sobre el mismo accuracy pool que run_audit (filtros dashboard aplicados).

Run:
    python -m system_audit.filter_combos
"""

import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from src.database.supabase_client import SupabaseClient
from system_audit.run_audit import (
    apply_dashboard_filters,
    fetch_resolved_alerts,
    get_eff_price,
)

# ── Parámetros ────────────────────────────────────────────────────────────────
MIN_N        = 10   # mínimo de alertas para reportar una combinación
TOP_N        = 30   # top N combinaciones a mostrar
MIN_WR_SHOW  = 0.70 # solo mostrar combinaciones con WR >= 70%


# ── Extracción de filtros positivos ───────────────────────────────────────────

def get_positive_filter_ids(alert: dict) -> frozenset[str]:
    """Devuelve el conjunto de IDs de filtros con puntos positivos."""
    filters = alert.get("filters_triggered") or []
    return frozenset(
        f["filter_id"]
        for f in filters
        if isinstance(f, dict)
        and f.get("filter_id")
        and (f.get("points") or 0) > 0
    )


# ── Análisis de combinaciones ─────────────────────────────────────────────────

def analyze_combos(pool: list[dict], combo_size: int) -> list[dict]:
    """
    Para cada combinación de `combo_size` filtros que aparezca juntos,
    calcula n, wins, WR. Devuelve lista ordenada por WR desc.
    """
    wins_count   = defaultdict(int)
    total_count  = defaultdict(int)

    for alert in pool:
        fids     = get_positive_filter_ids(alert)
        outcome  = alert.get("outcome")
        is_win   = outcome == "correct"

        for combo in combinations(sorted(fids), combo_size):
            total_count[combo] += 1
            if is_win:
                wins_count[combo] += 1

    results = []
    for combo, n in total_count.items():
        if n < MIN_N:
            continue
        w  = wins_count[combo]
        wr = w / n
        if wr < MIN_WR_SHOW:
            continue
        results.append({
            "combo":  list(combo),
            "n":      n,
            "wins":   w,
            "losses": n - w,
            "wr":     round(wr, 4),
        })

    results.sort(key=lambda x: (x["wr"], x["n"]), reverse=True)
    return results[:TOP_N]


# ── Análisis de filtros individuales ─────────────────────────────────────────

def analyze_singles(pool: list[dict]) -> list[dict]:
    wins_count  = defaultdict(int)
    total_count = defaultdict(int)

    for alert in pool:
        fids    = get_positive_filter_ids(alert)
        is_win  = alert.get("outcome") == "correct"
        for fid in fids:
            total_count[fid] += 1
            if is_win:
                wins_count[fid] += 1

    results = []
    for fid, n in total_count.items():
        if n < MIN_N:
            continue
        w  = wins_count[fid]
        wr = w / n
        results.append({
            "filter": fid,
            "n":      n,
            "wins":   w,
            "losses": n - w,
            "wr":     round(wr, 4),
        })

    results.sort(key=lambda x: (x["wr"], x["n"]), reverse=True)
    return results


# ── Salida ────────────────────────────────────────────────────────────────────

def _bar(wr: float, width: int = 20) -> str:
    filled = round(wr * width)
    return "█" * filled + "░" * (width - filled)


def print_singles(singles: list[dict]) -> None:
    SEP = "─" * 62
    print(f"\n  FILTROS INDIVIDUALES  (n ≥ {MIN_N})")
    print(f"  {SEP}")
    print(f"  {'Filtro':<10} {'N':>5}  {'WR':>7}  {'W/L':>9}  Bar")
    print(f"  {'─'*10} {'─'*5}  {'─'*7}  {'─'*9}  {'─'*20}")
    for r in singles:
        bar = _bar(r["wr"])
        print(
            f"  {r['filter']:<10} {r['n']:>5}  "
            f"{r['wr']*100:>6.1f}%  "
            f"{r['wins']:>4}/{r['losses']:<4}  {bar}"
        )


def print_combos(combos: list[dict], size: int) -> None:
    label = "PARES" if size == 2 else "TRIPLES"
    SEP   = "─" * 62
    print(f"\n  COMBINACIONES DE {label}  (n ≥ {MIN_N}, WR ≥ {MIN_WR_SHOW*100:.0f}%)")
    print(f"  {SEP}")
    if not combos:
        print(f"  (ninguna combinación cumple los criterios)")
        return
    print(f"  {'Combinación':<30} {'N':>5}  {'WR':>7}  {'W/L':>9}")
    print(f"  {'─'*30} {'─'*5}  {'─'*7}  {'─'*9}")
    for r in combos:
        combo_str = " + ".join(r["combo"])
        print(
            f"  {combo_str:<30} {r['n']:>5}  "
            f"{r['wr']*100:>6.1f}%  "
            f"{r['wins']:>4}/{r['losses']:<4}"
        )


def main() -> None:
    db  = SupabaseClient()
    raw = fetch_resolved_alerts(db)
    print(f"\n  Raw resolved: {len(raw)}")

    pool = apply_dashboard_filters(raw)
    print(f"  Pool tras filtros dashboard: {len(pool)}")
    print(f"  Wins: {sum(1 for a in pool if a.get('outcome')=='correct')}  "
          f"Losses: {sum(1 for a in pool if a.get('outcome')=='incorrect')}")

    SEP = "═" * 66
    print(f"\n{SEP}")
    print(f"  SENTINEL ALPHA — ANÁLISIS DE COMBINACIONES DE FILTROS")
    print(SEP)

    singles = analyze_singles(pool)
    print_singles(singles)

    pairs   = analyze_combos(pool, 2)
    print_combos(pairs, 2)

    triples = analyze_combos(pool, 3)
    print_combos(triples, 3)

    print(f"\n{SEP}\n")


if __name__ == "__main__":
    main()
