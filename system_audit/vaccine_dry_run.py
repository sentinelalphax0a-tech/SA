"""
VACUNA v3 — Dry-Run: Re-scan de alertas contaminadas
======================================================
DRY-RUN ONLY — no modifica la DB.

Bugs corregidos:
  Bug ROOT: direction siempre = "NO" para mercados multi-outcome
  Bug 2:    SELLs contados como BUYs en total_amount / avg_entry

Alcance:
  - Bug 2 (SELLs): ~95 alertas (criterio: trade @ <65% avg, >$100)
  - Bug ROOT (multi-outcome): 3 mercados confirmados (~135 alertas)

Metodología:
  1. Para cada wallet en cada alerta: fetch activity?user= para ese market
  2. Aplicar Bug ROOT fix: direction = outcome string real
  3. Aplicar Bug 2 fix:    solo trades con side == "BUY"
  4. Recomputar total_amount, avg_entry, trade_count
  5. Reconstruir filter list: B18x y B28x actualizados, resto igual
  6. Re-run calculate_score()
  7. Comparar con score original → informe
"""

import os
import re
import sys
import time
import math
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv(".env")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase import create_client
from src import config
from src.analysis.scoring import calculate_score
from src.database.models import FilterResult

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
DATA_API = "https://data-api.polymarket.com"
THROTTLE   = 0.12   # seconds between API calls
MAX_RETRIES = 1

# B18 thresholds (from config)
B18_TIERS = [
    ("B18d", "Acumulación muy fuerte",  config.ACCUM_VERY_STRONG_MIN,   None),
    ("B18c", "Acumulación fuerte",       config.ACCUM_STRONG_MIN,         config.ACCUM_VERY_STRONG_MIN),
    ("B18b", "Acumulación significativa",config.ACCUM_SIGNIFICANT_MIN,    config.ACCUM_STRONG_MIN),
    ("B18a", "Acumulación moderada",     config.ACCUM_MODERATE_MIN,       config.ACCUM_SIGNIFICANT_MIN),
]

# Current config B18 points
B18_POINTS = {
    "B18d": config.FILTER_B18D["points"],
    "B18c": config.FILTER_B18C["points"],
    "B18b": config.FILTER_B18B["points"],
    "B18a": config.FILTER_B18A["points"],
}

# B28 thresholds
ALLIN_EXTREME = config.ALLIN_EXTREME_MIN   # 0.90
ALLIN_STRONG  = config.ALLIN_STRONG_MIN    # 0.70
ALLIN_MIN_AMT = config.ALLIN_MIN_AMOUNT    # 3500.0


# ── Multi-outcome market IDs confirmed ────────────────────────────────────────
MULTI_OUTCOME_MARKETS = {
    "0xecd961f60dad9a8f4f25f717bc6771e09cddf3077657aafc67a6a528c92aad55": ("$60k", "$80k"),
    "0x6f01b7736bea0faed4087b73e2ab3592446cef68ea9eef8d314b2484f682228a": ("US", "Israel"),
    "0xf30a0d0e1df8776638d4a5b32fced8b63a52e5a3a2c8d3d5cee5da5c1f5b949": ("Nothing", "Something"),
}


# ── Direction logic (Bug ROOT fix) ────────────────────────────────────────────
def direction_from_outcome(outcome: str) -> str:
    if outcome in ("Yes", "YES"):
        return "YES"
    if outcome in ("No", "NO"):
        return "NO"
    return outcome  # categorical: "$60k", "US", "Nothing", etc.


# ── API helpers ───────────────────────────────────────────────────────────────
def fetch_activity(wallet: str, limit: int = 500) -> list[dict]:
    """Fetch wallet activity from Polymarket Data API."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.get(
                f"{DATA_API}/activity",
                params={"user": wallet, "limit": limit},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json() or []
            if r.status_code == 429:
                time.sleep(2.0)
                continue
        except Exception:
            time.sleep(0.5)
    return []


def match_stored_trade_to_api(stored: dict, api_trades: list[dict]) -> dict | None:
    """
    Match a stored DB trade (price + amount + timestamp) to an API activity record.
    Returns the API record or None.
    """
    s_price  = round(float(stored.get("price", 0)), 4)
    s_amount = float(stored.get("amount", 0))
    s_ts_raw = stored.get("timestamp", "")

    # Parse stored timestamp
    if isinstance(s_ts_raw, str):
        try:
            s_ts = datetime.fromisoformat(s_ts_raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            s_ts = 0.0
    else:
        s_ts = float(s_ts_raw) if s_ts_raw else 0.0

    best: dict | None = None
    best_score = float("inf")

    for t in api_trades:
        a_price    = round(float(t.get("price", 0)), 4)
        a_usdc     = float(t.get("usdcSize", 0) or 0)
        a_notional = a_usdc or float(t.get("size", 0)) * float(t.get("price", 0))
        a_ts       = float(t.get("timestamp", 0))

        price_diff  = abs(a_price - s_price)
        amount_diff = abs(a_notional - s_amount) / max(s_amount, 1)
        ts_diff     = abs(a_ts - s_ts)

        # Accept if price within 0.5%, amount within 5%, timestamp within 300s
        if price_diff < 0.005 and amount_diff < 0.05 and ts_diff < 300:
            score = price_diff + amount_diff * 0.1 + ts_diff * 1e-6
            if score < best_score:
                best_score = score
                best = t

    return best


def get_corrected_wallet(
    wallet_addr: str,
    condition_id: str,
    stored_trades: list[dict],
) -> dict:
    """
    Fetch API activity for this wallet+market, match to stored trades,
    apply Bug ROOT + Bug 2 fixes.

    Returns dict with:
      direction, total_amount, avg_entry, trade_count,
      corrected_trades (list), unmatched_count, api_total_trades
    """
    time.sleep(THROTTLE)
    api_acts = fetch_activity(wallet_addr, limit=500)

    # Filter to this market and TRADE type
    mkt_acts = [
        t for t in api_acts
        if t.get("conditionId") == condition_id
        and t.get("type", "TRADE") == "TRADE"
    ]

    if not mkt_acts and not stored_trades:
        return _empty_wallet_result(status="no_data")

    corrected_trades = []
    unmatched = 0

    for stored in stored_trades:
        matched = match_stored_trade_to_api(stored, mkt_acts)
        if matched:
            outcome = matched.get("outcome", "")
            side    = matched.get("side", "BUY")
            corrected_trades.append({
                "price":     float(stored.get("price", 0)),
                "amount":    float(stored.get("amount", 0)),
                "side":      side,
                "outcome":   outcome,
                "direction": direction_from_outcome(outcome),
                "timestamp": stored.get("timestamp", ""),
                "matched":   True,
            })
        else:
            unmatched += 1
            # Can't determine side from API — conservative: treat as BUY
            corrected_trades.append({
                "price":     float(stored.get("price", 0)),
                "amount":    float(stored.get("amount", 0)),
                "side":      "BUY",    # conservative
                "outcome":   "",
                "direction": "BUY_UNKNOWN",
                "timestamp": stored.get("timestamp", ""),
                "matched":   False,
            })

    # Bug 2: only BUY-side trades
    buys = [t for t in corrected_trades if t["side"] == "BUY"]

    if not buys:
        return _empty_wallet_result(status="all_sells")

    # Bug ROOT: dominant direction by total BUY amount
    totals: dict[str, float] = {}
    for t in buys:
        d = t["direction"]
        totals[d] = totals.get(d, 0.0) + t["amount"]

    dominant_dir = max(totals, key=totals.get)

    # If dominant_dir is "BUY_UNKNOWN" (all unmatched), we can't correct direction
    directional = [t for t in buys if t["direction"] == dominant_dir]
    if not directional:
        directional = buys

    total_amount = sum(t["amount"] for t in directional)
    if total_amount > 0:
        avg_entry = sum(t["price"] * t["amount"] for t in directional) / total_amount
    else:
        avg_entry = 0.0

    return {
        "direction":       dominant_dir,
        "total_amount":    round(total_amount, 4),
        "avg_entry":       round(avg_entry, 6),
        "trade_count":     len(directional),
        "corrected_trades": directional,
        "unmatched_count": unmatched,
        "api_total_trades": len(mkt_acts),
        "status":          "ok" if unmatched == 0 else "partial_match",
    }


def _empty_wallet_result(status: str) -> dict:
    return {
        "direction": "UNKNOWN",
        "total_amount": 0.0,
        "avg_entry": 0.0,
        "trade_count": 0,
        "corrected_trades": [],
        "unmatched_count": 0,
        "api_total_trades": 0,
        "status": status,
    }


# ── Filter reconstruction ─────────────────────────────────────────────────────
def parse_wallet_balance_from_b28(details: str) -> float | None:
    """Extract wallet balance from B28 detail string 'all-in X% of $Y'."""
    m = re.search(r"of \$([0-9,]+)", details or "")
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def b18_tier_for_amount(total: float) -> tuple[str, str, int] | None:
    """Return (filter_id, name, points) for the applicable B18 tier, or None."""
    for fid, name, lo, hi in B18_TIERS:
        if total >= lo and (hi is None or total < hi):
            return (fid, name, B18_POINTS[fid])
    return None


def b28_tier_for_ratio(ratio: float, total: float) -> tuple[str, str, int] | None:
    """Return (filter_id, name, points) for B28 tier, or None."""
    if total < ALLIN_MIN_AMT:
        return None
    if ratio >= ALLIN_EXTREME:
        return ("B28a", "All-in extremo", config.FILTER_B28A["points"])
    if ratio >= ALLIN_STRONG:
        return ("B28b", "All-in fuerte", config.FILTER_B28B["points"])
    return None


def rebuild_filters(
    stored_filters: list[dict],
    corrected_total: float,
    wallet_balance: float | None,
    corrected_trade_count: int,
) -> tuple[list[FilterResult], list[str], list[str]]:
    """
    Rebuild the filter list with corrected B18/B28.
    Returns (new_filter_results, added_filter_ids, removed_filter_ids).
    """
    REBUILD_IDS = {"B18a", "B18b", "B18c", "B18d", "B28a", "B28b"}

    added   = []
    removed = []

    # Collect non-B18/B28 filters as-is
    base_filters: list[FilterResult] = []
    wallet_balance_from_stored = wallet_balance

    for f in stored_filters:
        fid = f.get("filter_id", "")
        if fid in REBUILD_IDS:
            removed.append(fid)
            # Extract wallet balance from B28 if not already known
            if fid.startswith("B28") and wallet_balance_from_stored is None:
                wallet_balance_from_stored = parse_wallet_balance_from_b28(
                    f.get("details", "")
                )
            continue
        base_filters.append(FilterResult(
            filter_id   = fid,
            filter_name = f.get("filter_name", ""),
            points      = f.get("points", 0),
            category    = f.get("category", ""),
            details     = f.get("details"),
        ))

    # Re-add B18 based on corrected total
    b18 = b18_tier_for_amount(corrected_total)
    if b18:
        fid, name, pts = b18
        base_filters.append(FilterResult(
            filter_id=fid, filter_name=name, points=pts,
            category="behavior",
            details=f"accum=${corrected_total:,.0f}, trades={corrected_trade_count}",
        ))
        added.append(fid)

    # Re-add B28 if wallet balance known
    if wallet_balance_from_stored and wallet_balance_from_stored > 0:
        ratio = corrected_total / wallet_balance_from_stored
        b28 = b28_tier_for_ratio(ratio, corrected_total)
        if b28:
            fid, name, pts = b28
            base_filters.append(FilterResult(
                filter_id=fid, filter_name=name, points=pts,
                category="behavior",
                details=f"all-in {ratio*100:.0f}% of ${wallet_balance_from_stored:,.0f}",
            ))
            added.append(fid)

    return base_filters, added, removed


# ── Main dry-run logic ────────────────────────────────────────────────────────
@dataclass
class WalletResult:
    addr: str
    stored_dir: str
    stored_total: float
    corrected_dir: str
    corrected_total: float
    corrected_avg_entry: float
    corrected_trade_count: int
    status: str  # ok | partial_match | no_data | all_sells


@dataclass
class AlertResult:
    alert_id: int
    market_question: str
    market_id: str
    stored_star: int
    stored_score: int
    stored_raw: int
    stored_mult: float
    stored_total: float
    new_star: int
    new_score: int
    new_raw: int
    new_mult: float
    new_total: float
    new_direction: str
    added_filters: list[str]
    removed_filters: list[str]
    wallet_results: list[WalletResult]
    flags: list[str]   # "false_confluence", "invalidated", "no_data", etc.


def run_dry_run() -> list[AlertResult]:
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])

    # ── Step 1: Collect all contaminated alert IDs ─────────────────────────
    print("Fetching all pending alerts...", flush=True)
    PAGE = 1000
    all_alerts: list[dict] = []
    offset = 0
    while True:
        rows = (
            sb.table("alerts")
            .select(
                "id,market_question,market_id,direction,total_amount,"
                "score,score_raw,multiplier,star_level,outcome,wallets,"
                "filters_triggered_initial"
            )
            .eq("outcome", "pending")
            .range(offset, offset + PAGE - 1)
            .execute()
            .data or []
        )
        all_alerts.extend(rows)
        if len(rows) < PAGE:
            break
        offset += PAGE
        time.sleep(0.05)

    print(f"Total pending alerts: {len(all_alerts)}")

    # Bug 2: sell-as-buy candidates
    sell_contaminated: set[int] = set()
    for a in all_alerts:
        for w in (a.get("wallets") or []):
            trades = w.get("trades") or []
            if len(trades) < 2:
                continue
            for i, t in enumerate(trades):
                tp = float(t.get("price", 0))
                ta = float(t.get("amount", 0))
                other_p = [float(trades[j].get("price", 0)) for j in range(len(trades)) if j != i]
                if not other_p:
                    continue
                other_avg = sum(other_p) / len(other_p)
                if other_avg > 0 and tp < other_avg * 0.65 and ta > 100:
                    sell_contaminated.add(a["id"])
                    break

    # Bug ROOT: multi-outcome market alerts
    multi_contaminated: set[int] = set()
    mid_map: dict[int, str] = {}  # alert_id -> market_id
    for a in all_alerts:
        mid = a.get("market_id", "")
        if mid in MULTI_OUTCOME_MARKETS:
            multi_contaminated.add(a["id"])
            mid_map[a["id"]] = mid

    all_contaminated = sell_contaminated | multi_contaminated
    alert_map = {a["id"]: a for a in all_alerts if a["id"] in all_contaminated}

    print(f"Bug 2 (sell): {len(sell_contaminated)} alerts")
    print(f"Bug ROOT (multi-outcome): {len(multi_contaminated)} alerts")
    print(f"Total unique: {len(all_contaminated)} alerts")
    print(f"Starting dry-run...\n", flush=True)

    results: list[AlertResult] = []
    done = 0

    for aid, alert in sorted(alert_map.items()):
        done += 1
        mid = alert.get("market_id", "")
        wallets_data = alert.get("wallets") or []
        stored_filters = alert.get("filters_triggered_initial") or []
        stored_star  = alert.get("star_level", 0)
        stored_score = alert.get("score", 0)
        stored_raw   = alert.get("score_raw", 0)
        stored_mult  = float(alert.get("multiplier", 1.0))
        stored_total = float(alert.get("total_amount", 0))
        stored_dir   = alert.get("direction", "")
        distinct_mkt = None  # will try to get from stored wallets

        if done % 20 == 0 or done == 1:
            print(f"  [{done}/{len(all_contaminated)}] processing alert #{aid}...", flush=True)

        wallet_results: list[WalletResult] = []
        flags: list[str] = []

        # Extract distinct_markets from first wallet if available
        for wdata in wallets_data:
            dm = wdata.get("distinct_markets")
            if dm is not None:
                distinct_mkt = int(dm)
                break

        # ── Process each wallet ────────────────────────────────────────────
        for wdata in wallets_data:
            addr = wdata.get("wallet_address") or wdata.get("address", "")
            if not addr:
                continue
            stored_w_dir   = wdata.get("direction", "")
            stored_w_total = float(wdata.get("total_amount", 0))
            stored_trades  = wdata.get("trades") or []

            corrected = get_corrected_wallet(addr, mid, stored_trades)

            wallet_results.append(WalletResult(
                addr              = addr,
                stored_dir        = stored_w_dir,
                stored_total      = stored_w_total,
                corrected_dir     = corrected["direction"],
                corrected_total   = corrected["total_amount"],
                corrected_avg_entry = corrected["avg_entry"],
                corrected_trade_count = corrected["trade_count"],
                status            = corrected["status"],
            ))

        # ── Check for false confluence in multi-outcome markets ────────────
        if mid in MULTI_OUTCOME_MARKETS:
            directions_in_alert = {
                wr.corrected_dir for wr in wallet_results
                if wr.corrected_dir not in ("UNKNOWN", "BUY_UNKNOWN", "")
            }
            # Binary directions are fine; multiple non-binary directions = false confluence
            std_dirs = {"YES", "NO"}
            non_std = directions_in_alert - std_dirs
            if len(non_std) > 1:
                flags.append("false_confluence")

        # ── Determine dominant direction for the alert ─────────────────────
        dir_totals: dict[str, float] = {}
        for wr in wallet_results:
            d = wr.corrected_dir
            if d not in ("UNKNOWN", "BUY_UNKNOWN"):
                dir_totals[d] = dir_totals.get(d, 0.0) + wr.corrected_total

        new_direction = max(dir_totals, key=dir_totals.get) if dir_totals else stored_dir

        # Filter wallets to dominant direction (mirrors _filter_wallets_by_direction)
        dominant_wallets = [
            wr for wr in wallet_results
            if wr.corrected_dir == new_direction or wr.corrected_dir in ("UNKNOWN", "BUY_UNKNOWN")
        ]

        new_total = sum(wr.corrected_total for wr in dominant_wallets)

        # Handle no-data case
        if new_total == 0:
            flags.append("no_data" if all(wr.status == "no_data" for wr in wallet_results) else "all_sells_or_no_data")

        # ── Rebuild filter list ─────────────────────────────────────────────
        # Use the wallet with the max stored B28 balance (most common: single wallet)
        wallet_balance = None
        for f in stored_filters:
            if f.get("filter_id", "").startswith("B28"):
                wallet_balance = parse_wallet_balance_from_b28(f.get("details", ""))
                if wallet_balance:
                    break

        max_trade_count = max((wr.corrected_trade_count for wr in dominant_wallets), default=0)

        new_filter_list, added, removed = rebuild_filters(
            stored_filters,
            new_total,
            wallet_balance,
            max_trade_count,
        )

        # ── Re-run scoring ─────────────────────────────────────────────────
        if new_total > 0:
            score_result = calculate_score(
                filters_triggered=new_filter_list,
                total_amount=new_total,
                wallet_market_count=distinct_mkt,
            )
            new_star  = score_result.star_level
            new_score = score_result.score_final
            new_raw   = score_result.score_raw
            new_mult  = score_result.multiplier
        else:
            # No usable data → treat as invalidated
            new_star = new_score = new_raw = 0
            new_mult = 0.0
            flags.append("invalidated")

        results.append(AlertResult(
            alert_id       = aid,
            market_question= alert.get("market_question", ""),
            market_id      = mid,
            stored_star    = stored_star,
            stored_score   = stored_score,
            stored_raw     = stored_raw,
            stored_mult    = stored_mult,
            stored_total   = stored_total,
            new_star       = new_star,
            new_score      = new_score,
            new_raw        = new_raw,
            new_mult       = new_mult,
            new_total      = round(new_total, 2),
            new_direction  = new_direction,
            added_filters  = added,
            removed_filters= removed,
            wallet_results = wallet_results,
            flags          = flags,
        ))

    return results


# ── Report ────────────────────────────────────────────────────────────────────
def print_report(results: list[AlertResult]) -> None:
    from collections import Counter

    changed     = [r for r in results if r.new_star != r.stored_star or abs(r.new_score - r.stored_score) >= 5]
    no_change   = [r for r in results if r not in changed and "invalidated" not in r.flags and "no_data" not in r.flags]
    invalidated = [r for r in results if "invalidated" in r.flags or "no_data" in r.flags]
    false_conf  = [r for r in results if "false_confluence" in r.flags]

    star_transitions: Counter = Counter()
    for r in changed:
        star_transitions[f"{r.stored_star}★→{r.new_star}★"] += 1

    print("\n" + "=" * 72)
    print("VACUNA v3 — DRY-RUN REPORT")
    print("=" * 72)
    print(f"Alertas analizadas: {len(results)}")
    print(f"  Con cambio de estrellas o score: {len(changed)}")
    print(f"  Sin cambio material: {len(no_change)}")
    print(f"  Invalidadas (no data / all-sells): {len(invalidated)}")
    print(f"  Falsa confluencia (multi-outcome): {len(false_conf)}")

    print("\n── TRANSICIONES DE ESTRELLAS ──────────────────────────────────────")
    for transition, cnt in sorted(star_transitions.items(), reverse=True):
        print(f"  {transition}: {cnt}")

    print("\n── DETALLE: ALERTAS CON CAMBIO DE STAR ────────────────────────────")
    for r in sorted(changed, key=lambda x: (-x.stored_star, -(x.stored_score - x.new_score))):
        delta_score = r.new_score - r.stored_score
        bug_type = []
        if r.alert_id in {r2.alert_id for r2 in results if "false_confluence" in r2.flags}:
            bug_type.append("ROOT+FC")
        elif r.market_id in MULTI_OUTCOME_MARKETS:
            bug_type.append("ROOT")
        if any("sell" in f.lower() for f in r.flags) or r.new_total < r.stored_total * 0.95:
            bug_type.append("BUG2")
        bug_label = "+".join(bug_type) if bug_type else "BUG2"

        print(f"\n  #{r.alert_id:>6} [{r.stored_star}★→{r.new_star}★]  "
              f"{r.stored_score}→{r.new_score} ({delta_score:+d}pts)  "
              f"[{bug_label}]")
        print(f"           total: ${r.stored_total:,.0f} → ${r.new_total:,.0f}  "
              f"new_dir: {r.new_direction}")
        print(f"           removed: {r.removed_filters}  added: {r.added_filters}")
        print(f"           {r.market_question[:60]}")
        for wr in r.wallet_results:
            if abs(wr.corrected_total - wr.stored_total) > 50 or wr.corrected_dir != wr.stored_dir:
                print(f"           wallet {wr.addr[:10]}...  "
                      f"${wr.stored_total:,.0f}@{wr.stored_dir} → "
                      f"${wr.corrected_total:,.0f}@{wr.corrected_dir}  [{wr.status}]")

    print("\n── FALSA CONFLUENCIA (multi-outcome wallets opuestos mezclados) ────")
    for r in false_conf:
        dirs = {wr.corrected_dir for wr in r.wallet_results if wr.corrected_dir not in ("UNKNOWN","BUY_UNKNOWN")}
        print(f"  #{r.alert_id:>6} [{r.stored_star}★]  dirs={dirs}  "
              f"total=${r.stored_total:,.0f}  {r.market_question[:55]}")

    print("\n── INVALIDADAS (no data / all-sells) ───────────────────────────────")
    for r in sorted(invalidated, key=lambda x: -x.stored_star):
        print(f"  #{r.alert_id:>6} [{r.stored_star}★]  flags={r.flags}  "
              f"total=${r.stored_total:,.0f}  {r.market_question[:55]}")

    print("\n── ESTADÍSTICAS FINALES ────────────────────────────────────────────")
    print(f"  Alertas en total: {len(results)}")
    by_star_delta = Counter()
    for r in results:
        by_star_delta[f"{r.stored_star}★→{r.new_star}★"] += 1
    for k, v in sorted(by_star_delta.items()):
        print(f"  {k}: {v}")

    # Save JSON for the apply step
    output = [
        {
            "alert_id":       r.alert_id,
            "stored_star":    r.stored_star,
            "new_star":       r.new_star,
            "stored_score":   r.stored_score,
            "new_score":      r.new_score,
            "stored_total":   r.stored_total,
            "new_total":      r.new_total,
            "new_direction":  r.new_direction,
            "flags":          r.flags,
            "added_filters":  r.added_filters,
            "removed_filters":r.removed_filters,
        }
        for r in results
    ]
    out_path = os.path.join(os.path.dirname(__file__), "vaccine_dry_run_results.json")
    with open(out_path, "w") as fh:
        json.dump(output, fh, indent=2)
    print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
    results = run_dry_run()
    print_report(results)
