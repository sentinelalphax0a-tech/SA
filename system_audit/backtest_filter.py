"""
Backtest Filter Changes — Simulates scoring changes on resolved alerts.

Fetches all resolved alerts from Supabase, applies a hypothetical filter
change (remove or modify points), recomputes score + star, and reports the
impact on win-rates and EV.

Usage:
    # Simulate removing B07 entirely
    python -m system_audit.backtest_filter --remove B07

    # Simulate converting B07 from +20 to -15
    python -m system_audit.backtest_filter --modify B07=-15

    # Combine: remove B07 and boost B25a
    python -m system_audit.backtest_filter --remove B07 --modify B25a=30

    # Verbose: print every alert that changes star level
    python -m system_audit.backtest_filter --remove B07 --verbose

    # Run all 3 B07 scenarios at once
    python -m system_audit.backtest_filter --b07-scenarios
"""

import argparse
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from src import config
from src.analysis.scoring import (
    calculate_score,
    _get_amount_multiplier,
    _get_diversity_multiplier,
    _score_to_stars,
    _validate_stars,
    _apply_obvious_bet_cap,
    _enforce_mutual_exclusion,
    _get_categories,
)
from src.database.models import FilterResult
from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_resolved_alerts(db: SupabaseClient) -> list[dict]:
    """Paginated fetch of all resolved alerts with all fields needed for backtest."""
    rows: list[dict] = []
    PAGE, offset = 1000, 0
    while True:
        batch = (
            db.client.table("alerts")
            .select(
                "id, market_id, star_level, score, score_raw, multiplier, "
                "created_at, resolved_at, outcome, direction, odds_at_alert, "
                "actual_return, total_amount, wallets, "
                "filters_triggered, filters_triggered_initial"
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
    logger.info("Fetched %d resolved alerts", len(rows))
    return rows


# ── Filter manipulation ───────────────────────────────────────────────────────

def _dict_to_filter_result(d: dict) -> FilterResult:
    return FilterResult(
        filter_id=d.get("filter_id", ""),
        filter_name=d.get("filter_name", ""),
        points=int(d.get("points", 0)),
        category=d.get("category", ""),
        details=d.get("details"),
    )


def apply_changes(
    filters: list[FilterResult],
    remove: set[str],
    modify: dict[str, int],
) -> list[FilterResult]:
    """Return a new filter list with the requested changes applied."""
    result = []
    for f in filters:
        if f.filter_id in remove:
            continue
        if f.filter_id in modify:
            result.append(FilterResult(
                filter_id=f.filter_id,
                filter_name=f.filter_name,
                points=modify[f.filter_id],
                category=f.category,
                details=f.details,
            ))
        else:
            result.append(f)
    return result


def rescore(
    filters: list[FilterResult],
    total_amount: float,
    wallet_market_count: int | None,
) -> tuple[int, float, int, int]:
    """Recalculate (score_raw, multiplier, score_final, star_level)."""
    result = calculate_score(filters, total_amount, wallet_market_count)
    return (
        result.score_raw,
        result.multiplier,
        result.score_final,
        result.star_level,
    )


# ── Per-alert simulation ──────────────────────────────────────────────────────

def simulate_alert(
    alert: dict,
    remove: set[str],
    modify: dict[str, int],
) -> dict:
    """
    Returns a dict with original and simulated scoring for one alert.
    Uses filters_triggered_initial if available (T0 snapshot), else filters_triggered.
    """
    # Prefer T0 snapshot for backtest fidelity
    raw_filters = alert.get("filters_triggered_initial") or alert.get("filters_triggered") or []
    filters_orig = [_dict_to_filter_result(f) for f in raw_filters]

    total_amount = float(alert.get("total_amount") or 0)

    # Extract wallet_market_count from wallets[0].distinct_markets if present
    wallet_market_count: int | None = None
    wallets = alert.get("wallets") or []
    if wallets and isinstance(wallets, list) and len(wallets) > 0:
        dm = wallets[0].get("distinct_markets")
        if dm is not None:
            try:
                wallet_market_count = int(dm)
            except (ValueError, TypeError):
                pass

    # Original score (recomputed from stored filters for consistency)
    orig_raw, orig_mult, orig_final, orig_stars = rescore(
        filters_orig, total_amount, wallet_market_count
    )

    # Simulated
    filters_sim = apply_changes(filters_orig, remove, modify)
    sim_raw, sim_mult, sim_final, sim_stars = rescore(
        filters_sim, total_amount, wallet_market_count
    )

    # B07 presence in original filters
    has_b07 = any(f.filter_id == "B07" for f in filters_orig)

    return {
        "id": alert.get("id"),
        "outcome": alert.get("outcome"),
        "orig_stars": orig_stars,
        "orig_final": orig_final,
        "orig_raw": orig_raw,
        "sim_stars": sim_stars,
        "sim_final": sim_final,
        "sim_raw": sim_raw,
        "star_changed": orig_stars != sim_stars,
        "has_b07": has_b07,
        "total_amount": total_amount,
        "odds_at_alert": alert.get("odds_at_alert"),
        "actual_return": alert.get("actual_return"),
    }


# ── Metrics computation ───────────────────────────────────────────────────────

def compute_metrics(sims: list[dict], key: str = "orig") -> dict:
    """
    Compute win-rate and EV metrics from simulation results.
    key = "orig" or "sim"
    Returns: {global_wr, by_star: {star: wr}, counts: {star: n}, ev}
    """
    stars_key = f"{key}_stars"

    by_star: dict[int, list[str]] = defaultdict(list)
    all_outcomes = []
    returns = []

    for s in sims:
        star = s[stars_key]
        if star == 0:
            continue
        outcome = s["outcome"]
        by_star[star].append(outcome)
        all_outcomes.append(outcome)
        ret = s.get("actual_return")
        if ret is not None:
            returns.append(float(ret))

    def wr(outcomes: list[str]) -> float | None:
        if not outcomes:
            return None
        return sum(1 for o in outcomes if o == "correct") / len(outcomes) * 100

    global_wr = wr(all_outcomes)
    ev = sum(returns) / len(returns) if returns else None

    return {
        "global_wr": global_wr,
        "by_star": {s: wr(v) for s, v in sorted(by_star.items())},
        "counts": {s: len(v) for s, v in sorted(by_star.items())},
        "ev": ev,
        "total": len(all_outcomes),
    }


def b07_stats(sims: list[dict]) -> dict:
    """Stats specifically about B07 presence vs outcome."""
    with_b07 = [s for s in sims if s["has_b07"] and s["orig_stars"] > 0]
    correct = [s for s in with_b07 if s["outcome"] == "correct"]
    incorrect = [s for s in with_b07 if s["outcome"] == "incorrect"]
    total = len(with_b07)
    return {
        "total": total,
        "correct": len(correct),
        "incorrect": len(incorrect),
        "correct_ratio": len(correct) / total * 100 if total else None,
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def _fmt_wr(wr: float | None) -> str:
    return f"{wr:.1f}%" if wr is not None else "n/a"

def _fmt_ev(ev: float | None) -> str:
    return f"{ev:+.2f}%" if ev is not None else "n/a"

def _fmt_delta(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return ""
    return f"({b - a:+.1f}pp)"


def print_report(
    scenario_name: str,
    sims: list[dict],
    verbose: bool = False,
) -> None:
    orig = compute_metrics(sims, "orig")
    sim  = compute_metrics(sims, "sim")
    b07  = b07_stats(sims)

    # Stars present in either original or simulated
    all_stars = sorted(set(orig["by_star"]) | set(sim["by_star"]))

    changed = [s for s in sims if s["star_changed"] and s["orig_stars"] > 0]
    upgrades   = [s for s in changed if s["sim_stars"] > s["orig_stars"]]
    downgrades = [s for s in changed if s["sim_stars"] < s["orig_stars"]]
    zeroed     = [s for s in sims if s["orig_stars"] > 0 and s["sim_stars"] == 0]

    total_orig = orig["total"]

    print()
    print(f"{'═'*60}")
    print(f"  BACKTEST: {scenario_name}")
    print(f"{'═'*60}")
    print(f"  Alerts analyzed: {len(sims)}  |  publishable (≥1★): {total_orig}")
    print()

    print("  ORIGINAL:")
    print(f"    Global win rate : {_fmt_wr(orig['global_wr'])}  |  EV: {_fmt_ev(orig['ev'])}")
    wr_line  = "  ".join(f"{s}★={_fmt_wr(orig['by_star'].get(s))}" for s in all_stars)
    cnt_line = "  ".join(f"{s}★={orig['counts'].get(s, 0)}" for s in all_stars)
    print(f"    Win rate by ★   : {wr_line}")
    print(f"    Count by ★      : {cnt_line}")
    print()

    print(f"  SIMULATED ({scenario_name}):")
    print(f"    Global win rate : {_fmt_wr(sim['global_wr'])}  "
          f"{_fmt_delta(orig['global_wr'], sim['global_wr'])}  "
          f"|  EV: {_fmt_ev(sim['ev'])} {_fmt_delta(orig['ev'], sim['ev'])}")
    wr_line2  = "  ".join(f"{s}★={_fmt_wr(sim['by_star'].get(s))}" for s in all_stars)
    cnt_line2 = "  ".join(f"{s}★={sim['counts'].get(s, 0)}" for s in all_stars)
    print(f"    Win rate by ★   : {wr_line2}")
    print(f"    Count by ★      : {cnt_line2}")
    print()

    print("  CHANGES:")
    print(f"    Star changed    : {len(changed)} ({len(changed)/len(sims)*100:.1f}%)")
    print(f"    Upgrades        : {len(upgrades)}")
    print(f"    Downgrades      : {len(downgrades)}")
    print(f"    Zeroed (→0★)    : {len(zeroed)}")
    if changed:
        print(f"    Win rate change by ★:")
        for s in all_stars:
            o_wr = orig["by_star"].get(s)
            s_wr = sim["by_star"].get(s)
            if o_wr is not None or s_wr is not None:
                print(f"      {s}★: {_fmt_wr(o_wr)} → {_fmt_wr(s_wr)} {_fmt_delta(o_wr, s_wr)}")
    print()

    print("  B07 STATS (original scoring):")
    if b07["total"] > 0:
        base_wr = orig["global_wr"] or 0
        print(f"    B07 present in  : {b07['total']} alerts")
        print(f"    Correct         : {b07['correct']} ({b07['correct_ratio']:.1f}%)")
        print(f"    Incorrect       : {b07['incorrect']}")
        b07_ratio = b07["correct_ratio"] or 0
        signal = "NOISE/HARMFUL" if b07_ratio < base_wr else "ADDS SIGNAL"
        print(f"    vs baseline {_fmt_wr(orig['global_wr'])}: → B07 is {signal}")
    else:
        print("    B07 not present in any resolved alert")
    print()

    if verbose and changed:
        print("  VERBOSE — alerts with star change:")
        for s in sorted(changed, key=lambda x: x["id"]):
            print(f"    id={s['id']}  {s['orig_stars']}★→{s['sim_stars']}★  "
                  f"{s['outcome']}  amount=${s['total_amount']:,.0f}  "
                  f"score={s['orig_final']}→{s['sim_final']}")
        print()

    print(f"{'═'*60}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backtest filter changes on resolved alerts")
    p.add_argument(
        "--remove", action="append", metavar="FILTER_ID", default=[],
        help="Remove a filter entirely (e.g. --remove B07). Repeatable.",
    )
    p.add_argument(
        "--modify", action="append", metavar="FILTER_ID=POINTS", default=[],
        help="Change a filter's points (e.g. --modify B07=-15). Repeatable.",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Print every alert that changes star level.",
    )
    p.add_argument(
        "--b07-scenarios", action="store_true",
        help="Run all 3 B07 scenarios: remove, -15, -10.",
    )
    return p.parse_args()


def parse_modify(specs: list[str]) -> dict[str, int]:
    result = {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"--modify must be FILTER_ID=POINTS, got: {spec!r}")
        fid, pts = spec.split("=", 1)
        result[fid.strip().upper()] = int(pts.strip())
    return result


def main() -> None:
    args = parse_args()

    db = SupabaseClient()
    alerts = fetch_resolved_alerts(db)

    if not alerts:
        print("No resolved alerts found.")
        return

    if args.b07_scenarios:
        scenarios = [
            ("Remove B07",      {"B07"},   {}),
            ("B07 → -15pts",    set(),     {"B07": -15}),
            ("B07 → -10pts",    set(),     {"B07": -10}),
        ]
        for name, rem, mod in scenarios:
            sims = [simulate_alert(a, rem, mod) for a in alerts]
            print_report(name, sims, verbose=args.verbose)
        return

    remove = {r.strip().upper() for r in args.remove}
    modify = parse_modify(args.modify)

    if not remove and not modify:
        print("No changes specified. Use --remove, --modify, or --b07-scenarios.")
        return

    parts = []
    if remove:
        parts.append("Remove " + ", ".join(sorted(remove)))
    if modify:
        parts.append(", ".join(f"{k}→{v:+d}" for k, v in sorted(modify.items())))
    scenario_name = " | ".join(parts)

    sims = [simulate_alert(a, remove, modify) for a in alerts]
    print_report(scenario_name, sims, verbose=args.verbose)


if __name__ == "__main__":
    main()
