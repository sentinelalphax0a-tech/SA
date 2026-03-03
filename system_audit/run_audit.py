"""
Sentinel Alpha — Weekly System Audit
=====================================
Evaluates edge health by computing global, segmented, and rolling metrics
over the accuracy pool (dashboard-compatible filters).

Run:
    python -m system_audit.run_audit

Outputs:
    system_audit/snapshots/YYYY-MM-DD_audit.json
    system_audit/master_summary.csv  (one row appended per run)
"""

import csv
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# ── Path bootstrap (works as module or direct script) ─────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Directory layout ──────────────────────────────────────────────────────────
AUDIT_DIR     = Path(__file__).resolve().parent
SNAPSHOTS_DIR = AUDIT_DIR / "snapshots"
CSV_PATH      = AUDIT_DIR / "master_summary.csv"

# ── Constants ─────────────────────────────────────────────────────────────────
EFF_PRICE_BANDS = [
    ("0.60-0.70", 0.60, 0.70),
    ("0.70-0.80", 0.70, 0.80),
    ("0.80-0.90", 0.80, 0.90),
    ("0.90-1.00", 0.90, 1.01),
]
MIN_COMBO_N = 10  # minimum n for a combination segment to be reported


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_resolved_alerts(db: SupabaseClient) -> list[dict]:
    """Paginated fetch of all resolved alerts (bypasses PostgREST 1000-row cap)."""
    rows: list[dict] = []
    PAGE, offset = 1000, 0
    while True:
        batch = (
            db.client.table("alerts")
            .select(
                "id, market_id, star_level, score, created_at, resolved_at, "
                "outcome, direction, odds_at_alert, actual_return, realized_return, "
                "total_sold_pct, close_reason, merge_confirmed, filters_triggered"
            )
            .in_("outcome", ["correct", "incorrect"])
            .range(offset, offset + PAGE - 1)
            .execute()
        )
        data = batch.data or []
        rows.extend(data)
        if len(data) < PAGE:
            break
        offset += PAGE
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DASHBOARD-COMPATIBLE FILTERING
# ═══════════════════════════════════════════════════════════════════════════════

def _signal_sort_key(a: dict) -> tuple:
    """
    Identical to dashboard's _signal_sort_key.
    Higher tuple = better signal. Used to pick the winner per market_id.
    Priority: star_level > score > fewer negative points > later created_at.
    """
    filters = a.get("filters_triggered") or []
    neg_pts = sum(
        (f.get("points") or 0)
        for f in filters
        if (f.get("points") or 0) < 0
    )
    return (
        a.get("star_level") or 0,
        a.get("score") or 0,
        -neg_pts,
        a.get("created_at") or "",
    )


def _is_full_exit(a: dict) -> bool:
    """
    Mirrors dashboard: total_sold_pct >= 0.9 or close_reason == 'position_gone'.
    90% matches whale_monitor FULL_EXIT threshold.
    """
    total_sold = a.get("total_sold_pct") or 0
    cr = (a.get("close_reason") or "").lower()
    return total_sold >= 0.9 or cr == "position_gone"


def apply_dashboard_filters(rows: list[dict]) -> list[dict]:
    """
    Replicate the dashboard's accuracy pool exactly:
      1. Deduplicate by market_id — best _signal_sort_key wins.
      2. Exclude merge_confirmed.
      3. Exclude full exits (total_sold_pct >= 0.9 | position_gone).
    """
    # Step 1 — dedup: one signal per market_id
    dedup: dict[str, dict] = {}
    for a in rows:
        mid = a.get("market_id", "")
        if mid not in dedup or _signal_sort_key(a) > _signal_sort_key(dedup[mid]):
            dedup[mid] = a

    # Step 2 & 3 — exclusions
    return [
        a for a in dedup.values()
        if not a.get("merge_confirmed") and not _is_full_exit(a)
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. DERIVED FIELDS
# ═══════════════════════════════════════════════════════════════════════════════

def get_eff_price(a: dict) -> float:
    """Direction-adjusted odds: YES → odds_at_alert, NO → 1 - odds_at_alert."""
    odds = float(a.get("odds_at_alert") or 0)
    direction = (a.get("direction") or "YES").upper()
    return (1.0 - odds) if direction == "NO" else odds


def get_eff_price_band(ep: float) -> str | None:
    for label, lo, hi in EFF_PRICE_BANDS:
        if lo <= ep < hi:
            return label
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. METRICS ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def calc_metrics(pool: list[dict]) -> dict | None:
    """
    Compute standard metrics for a pool of resolved alerts.
    Returns None if pool is empty.
    """
    n = len(pool)
    if n == 0:
        return None

    wins   = [a for a in pool if a.get("outcome") == "correct"]
    losses = [a for a in pool if a.get("outcome") == "incorrect"]
    n_wins = len(wins)

    winrate = n_wins / n

    # actual_return: winner = positive %, loser = -100.0
    returns  = [float(a.get("actual_return") or 0) for a in pool]
    avg_ret  = float(np.mean(returns))

    # realized_return: only where populated (non-None)
    realized = [
        float(a["realized_return"])
        for a in pool
        if a.get("realized_return") is not None
    ]
    avg_realized = round(float(np.mean(realized)), 4) if realized else None

    # EV per trade: winrate * avg_win - (1 - winrate) * avg_loss
    win_rets  = [float(a.get("actual_return") or 0) for a in wins]
    loss_rets = [abs(float(a.get("actual_return") or 0)) for a in losses]
    avg_win  = float(np.mean(win_rets))  if win_rets  else 0.0
    avg_loss = float(np.mean(loss_rets)) if loss_rets else 0.0
    ev = round(winrate * avg_win - (1.0 - winrate) * avg_loss, 4)

    # Profit factor: sum(win returns) / sum(loss returns)
    sum_wins = sum(win_rets)
    sum_loss = sum(loss_rets)
    pf = round(sum_wins / sum_loss, 4) if sum_loss > 0 else None

    # Max consecutive losses (sorted chronologically by resolved_at)
    sorted_pool = sorted(
        pool,
        key=lambda a: a.get("resolved_at") or a.get("created_at") or "",
    )
    max_consec, cur = 0, 0
    for a in sorted_pool:
        if a.get("outcome") == "incorrect":
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 0

    # Simulated P&L: $100 stake per trade, actual_return is % return on stake
    # Win:  $100 × (actual_return / 100) = actual_return dollars
    # Loss: $100 × (-100 / 100)          = -$100
    pnl = round(sum(r / 100.0 * 100.0 for r in returns), 2)

    return {
        "n": n,
        "wins": n_wins,
        "losses": n - n_wins,
        "winrate": round(winrate, 4),
        "avg_return": round(avg_ret, 4),
        "avg_realized_return": avg_realized,
        "ev_per_trade": ev,
        "profit_factor": pf,
        "max_consecutive_losses": max_consec,
        "total_pnl_simulated": pnl,
    }


def calc_rolling(pool: list[dict], n: int) -> dict | None:
    """
    Metrics for the most recent N resolved alerts (by resolved_at).
    Returns condensed dict: {n, winrate, avg_return, ev}.
    """
    sorted_pool = sorted(
        pool,
        key=lambda a: a.get("resolved_at") or a.get("created_at") or "",
        reverse=True,
    )
    recent = sorted_pool[:n]
    m = calc_metrics(recent)
    if m is None:
        return None
    return {
        "n": m["n"],
        "winrate": m["winrate"],
        "avg_return": m["avg_return"],
        "ev": m["ev_per_trade"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SEGMENTED METRICS
# ═══════════════════════════════════════════════════════════════════════════════

def calc_by_eff_price(pool: list[dict]) -> dict:
    """Metrics per eff_price band."""
    result = {}
    for label, lo, hi in EFF_PRICE_BANDS:
        segment = [a for a in pool if lo <= get_eff_price(a) < hi]
        result[label] = calc_metrics(segment)
    return result


def calc_by_stars(pool: list[dict]) -> dict:
    """Metrics per star level (keys are strings '1'–'5')."""
    result = {}
    for star in range(1, 6):
        segment = [a for a in pool if (a.get("star_level") or 0) == star]
        result[str(star)] = calc_metrics(segment)
    return result


def calc_combinations(pool: list[dict]) -> dict:
    """
    Metrics for eff_price_band × star_level combinations.
    Only includes combinations with n >= MIN_COMBO_N.
    Key format: "0.70-0.80__3★"
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for a in pool:
        band = get_eff_price_band(get_eff_price(a))
        star = a.get("star_level") or 0
        if band:
            buckets[f"{band}__{star}★"].append(a)

    return {
        key: calc_metrics(segment)
        for key, segment in buckets.items()
        if len(segment) >= MIN_COMBO_N
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SNAPSHOT COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

def load_previous_snapshot(snapshots_dir: Path) -> dict | None:
    """Load the most recent *_audit.json, if any."""
    files = sorted(snapshots_dir.glob("*_audit.json"))
    if not files:
        return None
    with open(files[-1], "r", encoding="utf-8") as f:
        return json.load(f)


def _delta(new_val, old_val) -> float | None:
    if new_val is None or old_val is None:
        return None
    try:
        return round(float(new_val) - float(old_val), 4)
    except (TypeError, ValueError):
        return None


def calc_deltas(current: dict, previous: dict) -> dict:
    """
    Compares current global/segment metrics against the previous snapshot.
    Flags any segment whose winrate dropped > 5pp vs previous.
    """
    c_global = current.get("global") or {}
    p_global = previous.get("global") or {}

    global_deltas = {
        k: _delta(c_global.get(k), p_global.get(k))
        for k in (
            "winrate", "avg_return", "avg_realized_return",
            "ev_per_trade", "profit_factor",
            "total_pnl_simulated", "max_consecutive_losses",
        )
    }
    global_deltas["total_resolved"] = _delta(
        current.get("total_resolved"), previous.get("total_resolved")
    )

    # eff_price segment deltas
    flags: list[str] = []
    ep_deltas: dict[str, dict] = {}
    for band in [b[0] for b in EFF_PRICE_BANDS]:
        c_seg = (current.get("by_eff_price") or {}).get(band) or {}
        p_seg = (previous.get("by_eff_price") or {}).get(band) or {}
        wr_d  = _delta(c_seg.get("winrate"), p_seg.get("winrate"))
        ev_d  = _delta(c_seg.get("ev_per_trade"), p_seg.get("ev_per_trade"))
        ep_deltas[band] = {"winrate_delta": wr_d, "ev_delta": ev_d}
        if wr_d is not None and wr_d < -0.05:
            flags.append(f"eff_price {band}: winrate {wr_d*100:+.1f}pp")

    # star segment deltas
    star_deltas: dict[str, dict] = {}
    for star in range(1, 6):
        k = str(star)
        c_seg = (current.get("by_stars") or {}).get(k) or {}
        p_seg = (previous.get("by_stars") or {}).get(k) or {}
        wr_d  = _delta(c_seg.get("winrate"), p_seg.get("winrate"))
        ev_d  = _delta(c_seg.get("ev_per_trade"), p_seg.get("ev_per_trade"))
        star_deltas[k] = {"winrate_delta": wr_d, "ev_delta": ev_d}
        if wr_d is not None and wr_d < -0.05:
            flags.append(f"{star}★: winrate {wr_d*100:+.1f}pp")

    return {
        "previous_date": previous.get("fecha"),
        "global": global_deltas,
        "by_eff_price": ep_deltas,
        "by_stars": star_deltas,
        "flags": flags,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 7. PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

def save_snapshot(data: dict, snapshots_dir: Path) -> Path:
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = snapshots_dir / f"{data['fecha']}_audit.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


CSV_HEADERS = [
    "fecha", "total_resolved", "winrate", "avg_return", "ev",
    "profit_factor", "rolling_30_wr", "rolling_50_wr",
    "best_segment", "worst_segment",
]


def _best_worst_by_ev(by_eff_price: dict) -> tuple[str, str]:
    """Return (best, worst) segment labels by ev_per_trade (min n=5)."""
    scored = [
        (label, (m or {}).get("ev_per_trade"))
        for label, m in by_eff_price.items()
        if m and (m.get("n") or 0) >= 5 and m.get("ev_per_trade") is not None
    ]
    if not scored:
        return "", ""
    scored.sort(key=lambda x: x[1])
    return scored[-1][0], scored[0][0]


def append_csv(data: dict, csv_path: Path) -> None:
    write_header = not csv_path.exists()
    g   = data.get("global") or {}
    r   = data.get("rolling") or {}
    best, worst = _best_worst_by_ev(data.get("by_eff_price") or {})

    row = {
        "fecha":          data.get("fecha"),
        "total_resolved": data.get("total_resolved"),
        "winrate":        g.get("winrate"),
        "avg_return":     g.get("avg_return"),
        "ev":             g.get("ev_per_trade"),
        "profit_factor":  g.get("profit_factor"),
        "rolling_30_wr":  (r.get("last_30") or {}).get("winrate"),
        "rolling_50_wr":  (r.get("last_50") or {}).get("winrate"),
        "best_segment":   best,
        "worst_segment":  worst,
    }
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. CONSOLE OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def _pct(v, decimals=1) -> str:
    if v is None:
        return "N/A"
    return f"{float(v) * 100:.{decimals}f}%"


def _f(v, decimals=2, suffix="") -> str:
    if v is None:
        return "N/A"
    return f"{float(v):.{decimals}f}{suffix}"


def _compute_verdict(
    g: dict,
    r: dict,
    vs: dict | None,
) -> tuple[str, str]:
    """
    Simple verdict based on three signals:
      1. EV delta vs previous snapshot.
      2. Rolling-30 EV vs global EV (drift detection).
      3. Rolling-50 EV vs global EV.
      4. Segment degradation flags.
    """
    global_ev  = g.get("ev_per_trade")
    rolling_30 = (r.get("last_30") or {}).get("ev")
    rolling_50 = (r.get("last_50") or {}).get("ev")
    ev_delta   = ((vs or {}).get("global") or {}).get("ev_per_trade")
    flags      = (vs or {}).get("flags") or []

    deg, imp = 0, 0

    if ev_delta is not None:
        if ev_delta < -1.0:
            deg += 1
        elif ev_delta > 1.0:
            imp += 1

    if rolling_30 is not None and global_ev is not None:
        if rolling_30 < global_ev - 3.0:
            deg += 1
        elif rolling_30 > global_ev + 3.0:
            imp += 1

    if rolling_50 is not None and global_ev is not None:
        if rolling_50 < global_ev - 2.0:
            deg += 1
        elif rolling_50 > global_ev + 2.0:
            imp += 1

    if flags:
        deg += 1

    ev_str  = _f(global_ev, 2, "%")
    r30_str = _f(rolling_30, 2, "%")

    if deg >= 2:
        delta_str = f", delta EV {_f(ev_delta, 2, '%')}" if ev_delta is not None else ""
        return (
            "⚠️  POSIBLE DEGRADACIÓN",
            f"EV global={ev_str}, rolling_30={r30_str}{delta_str}. Revisar segmentos.",
        )
    if imp >= 2:
        return (
            "📈 MEJORANDO",
            f"EV reciente superior al histórico. Rolling_30 EV={r30_str} vs global {ev_str}.",
        )
    return (
        "✅ EDGE ESTABLE",
        f"Métricas dentro de rango histórico. EV global={ev_str}, rolling_30={r30_str}.",
    )


def print_summary(data: dict) -> None:
    g      = data.get("global") or {}
    r      = data.get("rolling") or {}
    vs     = data.get("vs_previous")
    by_ep  = data.get("by_eff_price") or {}
    by_st  = data.get("by_stars") or {}
    combos = data.get("combinations") or {}

    SEP = "═" * 62
    print(f"\n{SEP}")
    print(f"  SENTINEL ALPHA — AUDITORÍA SEMANAL  {data.get('fecha', '')}")
    print(SEP)

    # ── Global ───────────────────────────────────────────────────────────────
    print(f"\n  MÉTRICAS GLOBALES  (n = {data.get('total_resolved', 0)})")
    print(f"    Winrate              {_pct(g.get('winrate'))}")
    print(f"    Avg actual_return    {_f(g.get('avg_return'), 2, '%')}")
    print(f"    Avg realized_return  {_f(g.get('avg_realized_return'), 2, '%')}")
    print(f"    EV por trade         {_f(g.get('ev_per_trade'), 2, '%')}")
    print(f"    Profit factor        {_f(g.get('profit_factor'), 2, 'x')}")
    print(f"    Max consec. losses   {g.get('max_consecutive_losses', 'N/A')}")
    print(f"    PnL simulado ($100)  ${_f(g.get('total_pnl_simulated'), 0)}")

    # ── Rolling ───────────────────────────────────────────────────────────────
    r30 = r.get("last_30") or {}
    r50 = r.get("last_50") or {}
    print(f"\n  ROLLING (vs histórico {_pct(g.get('winrate'))} WR / {_f(g.get('ev_per_trade'), 2, '%')} EV)")
    print(f"    Últimos 30   WR={_pct(r30.get('winrate'))}  avg_ret={_f(r30.get('avg_return'), 2, '%')}  EV={_f(r30.get('ev'), 2, '%')}")
    print(f"    Últimos 50   WR={_pct(r50.get('winrate'))}  avg_ret={_f(r50.get('avg_return'), 2, '%')}  EV={_f(r50.get('ev'), 2, '%')}")

    # ── Segment table ─────────────────────────────────────────────────────────
    all_segs: list[tuple[str, dict]] = []
    for label, m in by_ep.items():
        if m and (m.get("n") or 0) >= 5:
            all_segs.append((f"eff_price {label}", m))
    for star, m in by_st.items():
        if m and (m.get("n") or 0) >= 5:
            all_segs.append((f"{star}★", m))
    for combo, m in combos.items():
        if m:
            all_segs.append((f"  combo {combo}", m))

    all_segs.sort(key=lambda x: x[1].get("ev_per_trade") or 0, reverse=True)

    print(f"\n  {'SEGMENTO':<32} {'N':>5}  {'WR':>7}  {'EV':>7}  {'PF':>6}")
    print(f"  {'─'*32} {'─'*5}  {'─'*7}  {'─'*7}  {'─'*6}")
    for label, m in all_segs:
        print(
            f"  {label:<32} {m['n']:>5}  "
            f"{_pct(m['winrate'], 1):>7}  "
            f"{_f(m['ev_per_trade'], 1, '%'):>7}  "
            f"{_f(m['profit_factor'], 2, 'x'):>6}"
        )

    # ── vs Previous ───────────────────────────────────────────────────────────
    if vs:
        print(f"\n  VS SNAPSHOT ANTERIOR  ({vs.get('previous_date', 'N/A')})")
        gd = vs.get("global") or {}

        def _dd(k, as_pp=False) -> str:
            v = gd.get(k)
            if v is None:
                return "N/A"
            return f"{v * 100:+.2f}pp" if as_pp else f"{v:+.4f}"

        print(f"    Winrate       {_dd('winrate', True)}")
        print(f"    EV per trade  {_dd('ev_per_trade')}")
        print(f"    PnL           {_dd('total_pnl_simulated')}")
        print(f"    N resueltas   {_dd('total_resolved')}")

        flags = vs.get("flags") or []
        if flags:
            print(f"\n  ⚠️  SEGMENTOS CON CAÍDA > 5pp:")
            for flag in flags:
                print(f"    • {flag}")
        else:
            print(f"    Sin caídas > 5pp en ningún segmento.")

    # ── Verdict ───────────────────────────────────────────────────────────────
    verdict, reason = _compute_verdict(g, r, vs)
    print(f"\n  VEREDICTO")
    print(f"    {verdict}")
    print(f"    {reason}")
    print(f"\n{SEP}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# 9. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("Starting audit — %s", today)

    db = SupabaseClient()

    logger.info("Fetching resolved alerts from Supabase...")
    raw = fetch_resolved_alerts(db)
    logger.info("  Total resolved (raw): %d", len(raw))

    pool = apply_dashboard_filters(raw)
    logger.info(
        "  After dashboard filters (dedup + excl. full_exit + merge_confirmed): %d",
        len(pool),
    )

    if not pool:
        logger.error("Empty pool after filters — nothing to audit.")
        return

    # ── Compute all metrics ───────────────────────────────────────────────────
    global_metrics = calc_metrics(pool)
    rolling = {
        "last_30": calc_rolling(pool, 30),
        "last_50": calc_rolling(pool, 50),
    }
    by_eff_price = calc_by_eff_price(pool)
    by_stars     = calc_by_stars(pool)
    combinations = calc_combinations(pool)

    # ── Compare with previous snapshot ───────────────────────────────────────
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    previous    = load_previous_snapshot(SNAPSHOTS_DIR)
    vs_previous = None

    if previous:
        current_for_delta = {
            "fecha":          today,
            "total_resolved": len(pool),
            "global":         global_metrics,
            "by_eff_price":   by_eff_price,
            "by_stars":       by_stars,
        }
        vs_previous = calc_deltas(current_for_delta, previous)
        logger.info("  Compared against snapshot: %s", previous.get("fecha"))
    else:
        logger.info("  No previous snapshot found — first run.")

    # ── Assemble snapshot ─────────────────────────────────────────────────────
    snapshot = {
        "fecha":          today,
        "total_resolved": len(pool),
        "global":         global_metrics,
        "rolling":        rolling,
        "by_eff_price":   by_eff_price,
        "by_stars":       by_stars,
        "combinations":   combinations,
        "vs_previous":    vs_previous,
    }

    # ── Persist ───────────────────────────────────────────────────────────────
    snap_path = save_snapshot(snapshot, SNAPSHOTS_DIR)
    logger.info("  Snapshot saved: %s", snap_path.name)

    append_csv(snapshot, CSV_PATH)
    logger.info("  master_summary.csv updated")

    # ── Print to stdout ───────────────────────────────────────────────────────
    print_summary(snapshot)


if __name__ == "__main__":
    main()
