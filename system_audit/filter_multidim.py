"""
Sentinel Alpha — Multi-Dimensional Filter Analysis
===================================================
Cruza los filtros con dimensiones extra: eff_price, star_level,
confluence_count (nº de wallets que activaron la alerta) y
wallets_count (nº de wallets en el array del alert).

Busca "golden zones": segmentos donde 2+ dimensiones coinciden
con WR ≥ 85% y n ≥ 10.

Run:
    python -m system_audit.filter_multidim
"""

import sys
from collections import defaultdict
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
from system_audit.filter_combos import get_positive_filter_ids

# ── Parámetros ────────────────────────────────────────────────────────────────
MIN_N        = 8    # mínimo de alertas para reportar un segmento
MIN_WR_SHOW  = 0.75 # solo mostrar segmentos con WR ≥ 75%
GOLDEN_N     = 10   # mínimo n para "golden zone"
GOLDEN_WR    = 0.85 # WR mínimo para "golden zone"

# Filtros de interés (top performers de filter_combos)
TOP_FILTERS = ["B28a", "B05", "B16", "B18d", "B26b", "M03", "B17", "B20"]

# Bandas de eff_price
PRICE_BANDS = [
    ("0.50–0.60", 0.50, 0.60),
    ("0.60–0.70", 0.60, 0.70),
    ("0.70–0.80", 0.70, 0.80),
    ("0.80–0.90", 0.80, 0.90),
    ("0.90–1.00", 0.90, 1.01),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def price_band(ep: float) -> str:
    for label, lo, hi in PRICE_BANDS:
        if lo <= ep < hi:
            return label
    return "other"


def wallets_bucket(n: int) -> str:
    if n == 1:
        return "1w"
    if n == 2:
        return "2w"
    if n == 3:
        return "3w"
    return "4w+"


def confluence_bucket(n: int) -> str:
    if n == 1:
        return "conf=1"
    if n == 2:
        return "conf=2"
    return "conf=3+"


def star_label(s) -> str:
    try:
        return f"{int(s)}★"
    except (TypeError, ValueError):
        return "?★"


def _wr_row(label: str, n: int, wins: int, width: int = 30) -> str:
    wr = wins / n
    bar_filled = round(wr * 20)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)
    return (
        f"  {label:<{width}} {n:>5}  "
        f"{wr*100:>6.1f}%  "
        f"{wins:>4}/{n-wins:<4}  {bar}"
    )


def _section(title: str) -> None:
    SEP = "─" * 70
    print(f"\n  {title}")
    print(f"  {SEP}")


def _print_table(rows: list[dict], label_key: str, label_width: int = 30) -> None:
    if not rows:
        print("  (ningún segmento cumple los criterios)")
        return
    print(f"  {'Segmento':<{label_width}} {'N':>5}  {'WR':>7}  {'W/L':>9}  Bar")
    print(f"  {'─'*label_width} {'─'*5}  {'─'*7}  {'─'*9}  {'─'*20}")
    for r in rows:
        print(_wr_row(r[label_key], r["n"], r["wins"], label_width))


# ── Análisis por dimensión simple ─────────────────────────────────────────────

def analyze_by_dimension(pool: list[dict], dim_fn) -> list[dict]:
    """Agrupa el pool por dim_fn(alert) → str y calcula WR."""
    wins_count  = defaultdict(int)
    total_count = defaultdict(int)
    for alert in pool:
        key    = dim_fn(alert)
        is_win = alert.get("outcome") == "correct"
        total_count[key] += 1
        if is_win:
            wins_count[key] += 1
    rows = []
    for key, n in total_count.items():
        if n < MIN_N:
            continue
        w = wins_count[key]
        rows.append({"segment": key, "n": n, "wins": w, "wr": w / n})
    rows.sort(key=lambda x: (x["wr"], x["n"]), reverse=True)
    return rows


# ── Análisis cruzado: filtro × dimensión ──────────────────────────────────────

def analyze_filter_x_dim(pool: list[dict], dim_fn, filter_ids: list[str]) -> list[dict]:
    """
    Para cada filtro en filter_ids, analiza WR dentro de cada bucket de dim_fn.
    Devuelve filas (filtro, bucket, n, wins, wr).
    """
    # key = (filter_id, dim_bucket)
    wins_count  = defaultdict(int)
    total_count = defaultdict(int)
    for alert in pool:
        fids   = get_positive_filter_ids(alert)
        bucket = dim_fn(alert)
        is_win = alert.get("outcome") == "correct"
        for fid in filter_ids:
            if fid in fids:
                total_count[(fid, bucket)] += 1
                if is_win:
                    wins_count[(fid, bucket)] += 1
    rows = []
    for (fid, bucket), n in total_count.items():
        if n < MIN_N:
            continue
        w  = wins_count[(fid, bucket)]
        wr = w / n
        if wr < MIN_WR_SHOW:
            continue
        rows.append({
            "segment": f"{fid} | {bucket}",
            "filter":  fid,
            "bucket":  bucket,
            "n":       n,
            "wins":    w,
            "wr":      wr,
        })
    rows.sort(key=lambda x: (x["wr"], x["n"]), reverse=True)
    return rows


# ── Golden zones: 3 dimensiones combinadas ────────────────────────────────────

def find_golden_zones(pool: list[dict]) -> list[dict]:
    """
    Busca segmentos con: filtro + price_band + star_level
    que tengan WR ≥ GOLDEN_WR y n ≥ GOLDEN_N.
    """
    wins_count  = defaultdict(int)
    total_count = defaultdict(int)
    for alert in pool:
        fids    = get_positive_filter_ids(alert)
        pb      = price_band(get_eff_price(alert))
        sl      = star_label(alert.get("star_level"))
        cc      = confluence_bucket(alert.get("confluence_count") or 1)
        is_win  = alert.get("outcome") == "correct"
        for fid in fids:
            for key in [
                (fid, pb, sl),
                (fid, pb, cc),
                (fid, sl, cc),
            ]:
                total_count[key] += 1
                if is_win:
                    wins_count[key] += 1

    rows = []
    for key, n in total_count.items():
        if n < GOLDEN_N:
            continue
        w  = wins_count[key]
        wr = w / n
        if wr < GOLDEN_WR:
            continue
        rows.append({
            "segment": " | ".join(key),
            "dims":    key,
            "n":       n,
            "wins":    w,
            "wr":      wr,
        })
    rows.sort(key=lambda x: (x["wr"], x["n"]), reverse=True)
    return rows[:40]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    db  = SupabaseClient()
    raw = fetch_resolved_alerts(db)
    print(f"\n  Raw resolved: {len(raw)}")
    pool = apply_dashboard_filters(raw)
    print(f"  Pool tras filtros dashboard: {len(pool)}")
    wins_total = sum(1 for a in pool if a.get("outcome") == "correct")
    print(f"  Wins: {wins_total}  Losses: {len(pool)-wins_total}  "
          f"WR global: {wins_total/len(pool)*100:.1f}%")

    SEP = "═" * 72
    print(f"\n{SEP}")
    print(f"  SENTINEL ALPHA — ANÁLISIS MULTI-DIMENSIONAL")
    print(SEP)

    # ── 1. WR por star_level ──────────────────────────────────────────────────
    _section("1. WIN RATE POR STAR LEVEL")
    stars = analyze_by_dimension(pool, lambda a: star_label(a.get("star_level")))
    _print_table(stars, "segment", 10)

    # ── 2. WR por banda de eff_price ─────────────────────────────────────────
    _section("2. WIN RATE POR BANDA DE EFF_PRICE")
    bands = analyze_by_dimension(pool, lambda a: price_band(get_eff_price(a)))
    _print_table(bands, "segment", 12)

    # ── 3. WR por confluence_count ────────────────────────────────────────────
    _section("3. WIN RATE POR CONFLUENCE_COUNT (nº wallets que activaron)")
    confs = analyze_by_dimension(
        pool,
        lambda a: confluence_bucket(a.get("confluence_count") or 1)
    )
    _print_table(confs, "segment", 10)

    # ── 4. WR por wallets_count ───────────────────────────────────────────────
    _section("4. WIN RATE POR WALLETS_COUNT (nº wallets en el array)")
    wals = analyze_by_dimension(
        pool,
        lambda a: wallets_bucket(len(a.get("wallets") or []))
    )
    _print_table(wals, "segment", 10)

    # ── 5. Filtro × eff_price band ────────────────────────────────────────────
    _section(f"5. TOP FILTROS × EFF_PRICE BAND  (n≥{MIN_N}, WR≥{MIN_WR_SHOW*100:.0f}%)")
    fx_price = analyze_filter_x_dim(
        pool,
        lambda a: price_band(get_eff_price(a)),
        TOP_FILTERS,
    )
    _print_table(fx_price, "segment", 22)

    # ── 6. Filtro × star_level ────────────────────────────────────────────────
    _section(f"6. TOP FILTROS × STAR LEVEL  (n≥{MIN_N}, WR≥{MIN_WR_SHOW*100:.0f}%)")
    fx_star = analyze_filter_x_dim(
        pool,
        lambda a: star_label(a.get("star_level")),
        TOP_FILTERS,
    )
    _print_table(fx_star, "segment", 18)

    # ── 7. Filtro × confluence_count ──────────────────────────────────────────
    _section(f"7. TOP FILTROS × CONFLUENCE_COUNT  (n≥{MIN_N}, WR≥{MIN_WR_SHOW*100:.0f}%)")
    fx_conf = analyze_filter_x_dim(
        pool,
        lambda a: confluence_bucket(a.get("confluence_count") or 1),
        TOP_FILTERS,
    )
    _print_table(fx_conf, "segment", 20)

    # ── 8. Golden zones: filtro × price × star ────────────────────────────────
    _section(
        f"8. GOLDEN ZONES (filtro × price × star/conf)  "
        f"n≥{GOLDEN_N}, WR≥{GOLDEN_WR*100:.0f}%"
    )
    golden = find_golden_zones(pool)
    if not golden:
        print("  (ninguna zona dorada encontrada con los criterios actuales)")
    else:
        print(f"  {'Zona (filtro | dim1 | dim2)':<38} {'N':>5}  {'WR':>7}  {'W/L':>9}")
        print(f"  {'─'*38} {'─'*5}  {'─'*7}  {'─'*9}")
        for r in golden:
            wr = r["wr"]
            w  = r["wins"]
            n  = r["n"]
            print(
                f"  {r['segment']:<38} {n:>5}  "
                f"{wr*100:>6.1f}%  "
                f"{w:>4}/{n-w:<4}"
            )

    print(f"\n{SEP}\n")


if __name__ == "__main__":
    main()
