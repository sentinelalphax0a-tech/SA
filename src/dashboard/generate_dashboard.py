"""
Sentinel Alpha -- Dashboard Generator.

Fetches alert data from Supabase, computes derived metrics,
and generates a self-contained HTML dashboard at docs/index.html.

Executable as: python -m src.dashboard.generate_dashboard
"""

import hashlib
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import config
from src.database.supabase_client import SupabaseClient

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "docs"
TEMPLATE_PATH = Path(__file__).parent / "dashboard_template.html"


# ── Data fetching ────────────────────────────────────────────


def fetch_data(db: SupabaseClient) -> dict:
    """Fetch all data needed for the dashboard from Supabase."""
    alerts_resp = (
        db.client.table("alerts")
        .select("*")
        .order("created_at", desc=True)
        .limit(5000)
        .execute()
    )
    markets_resp = db.client.table("markets").select("*").execute()
    scans_resp = (
        db.client.table("scans")
        .select("*")
        .order("timestamp", desc=True)
        .limit(1)
        .execute()
    )

    # Sell events (recent, for sell activity section)
    try:
        sell_events_resp = (
            db.client.table("alert_sell_events")
            .select("*")
            .order("detected_at", desc=True)
            .limit(50)
            .execute()
        )
        sell_events = sell_events_resp.data or []
    except Exception:
        sell_events = []

    markets_list = markets_resp.data or []
    return {
        "alerts": alerts_resp.data or [],
        "markets": {m["market_id"]: m for m in markets_list},
        "last_scan": (scans_resp.data or [{}])[0] if scans_resp.data else {},
        "sell_events": sell_events,
    }


# ── Direction helpers ────────────────────────────────────────


def _direction_adjust(odds: float, direction: str) -> float:
    """Adjust odds for direction: YES -> odds, NO -> 1 - odds."""
    if direction and direction.upper() == "NO":
        return 1.0 - odds
    return odds


def _parse_dt(val) -> datetime | None:
    """Parse an ISO string or datetime into a tz-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    if isinstance(val, str):
        try:
            from dateutil import parser as dt_parser

            dt = dt_parser.parse(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, ImportError):
            return None
    return None


def _time_ago(dt_val: datetime | None) -> str:
    """Human-readable time since a datetime."""
    if dt_val is None:
        return ""
    now = datetime.now(timezone.utc)
    delta = now - dt_val
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{secs / 3600:.1f}h ago"
    return f"{int(secs // 86400)}d ago"


def _time_left(resolution_date: datetime | None) -> str:
    """Human-readable time until a resolution date."""
    if resolution_date is None:
        return ""
    now = datetime.now(timezone.utc)
    delta = resolution_date - now
    secs = delta.total_seconds()
    if secs <= 0:
        return "Closed"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    if secs < 86400:
        return f"{secs / 3600:.1f}h"
    days = int(secs // 86400)
    hours = int((secs % 86400) // 3600)
    if hours > 0:
        return f"{days}d {hours}h"
    return f"{days}d"


# ── Enrichment ───────────────────────────────────────────────


def enrich_alerts(alerts: list[dict], markets: dict) -> list[dict]:
    """Add derived display fields to each alert."""
    now = datetime.now(timezone.utc)
    enriched = []

    for alert in alerts:
        a = dict(alert)  # shallow copy
        market = markets.get(a.get("market_id", "")) or {}
        direction = (a.get("direction") or "YES").upper()

        # Entry price: use wallet's avg_entry_price (direction-accurate),
        # fallback to odds_at_alert for backwards compat
        wallets = a.get("wallets") or []
        if wallets and wallets[0].get("avg_entry_price") is not None:
            a["entry_price"] = wallets[0]["avg_entry_price"]
        else:
            a["entry_price"] = a.get("odds_at_alert")

        # Current odds from market
        market_odds = market.get("current_odds")
        a["current_odds"] = market_odds

        # Odds change %
        entry = a.get("odds_at_alert")
        if entry and market_odds and entry > 0:
            entry_adj = _direction_adjust(entry, direction)
            current_adj = _direction_adjust(market_odds, direction)
            if entry_adj > 0:
                a["odds_change_pct"] = round(
                    ((current_adj - entry_adj) / entry_adj) * 100, 1
                )
            else:
                a["odds_change_pct"] = None
        else:
            a["odds_change_pct"] = None

        # Dollar P&L
        actual_return = a.get("actual_return")
        total_amount = a.get("total_amount") or 0
        if actual_return is not None:
            a["dollar_pnl"] = round(total_amount * (actual_return / 100), 2)
        elif a["odds_change_pct"] is not None and total_amount > 0:
            a["dollar_pnl"] = round(
                total_amount * (a["odds_change_pct"] / 100), 2
            )
        else:
            a["dollar_pnl"] = None

        # Time fields
        res_date = _parse_dt(market.get("resolution_date"))
        a["market_resolution_date"] = (
            res_date.isoformat() if res_date else None
        )
        a["time_left"] = _time_left(res_date)

        created = _parse_dt(a.get("created_at"))
        a["time_ago"] = _time_ago(created)

        # Row class
        outcome = a.get("outcome", "pending")
        if outcome == "correct":
            a["row_class"] = "correct"
        elif outcome == "incorrect":
            a["row_class"] = "incorrect"
        elif res_date and (res_date - now).total_seconds() < 86400 and (
            res_date - now
        ).total_seconds() > 0:
            a["row_class"] = "closing-soon"
        elif a["odds_change_pct"] is not None and a["odds_change_pct"] > 0:
            a["row_class"] = "winning"
        elif a["odds_change_pct"] is not None and a["odds_change_pct"] < 0:
            a["row_class"] = "losing"
        else:
            a["row_class"] = "neutral"

        # Polymarket URL
        slug = market.get("slug") or a.get("market_id", "")
        a["polymarket_url"] = f"https://polymarket.com/event/{slug}"

        # Sell Watch metadata
        total_sold = a.get("total_sold_pct") or 0
        a["has_sells"] = total_sold > 0
        if total_sold > 0:
            a["sold_pct_display"] = f"{total_sold * 100:.0f}% sold"
        else:
            a["sold_pct_display"] = None

        enriched.append(a)

    return enriched


# ── Statistics computation ───────────────────────────────────


def compute_stats(alerts: list[dict], markets: dict) -> dict:
    """Compute derived metrics from raw alert data."""
    now = datetime.now(timezone.utc)
    total = len(alerts)
    active = sum(1 for a in alerts if a.get("outcome") == "pending")
    resolved_list = [
        a for a in alerts if a.get("outcome") in ("correct", "incorrect")
    ]
    resolved = len(resolved_list)
    correct_3plus = sum(
        1
        for a in resolved_list
        if a.get("outcome") == "correct" and (a.get("star_level") or 0) >= 3
    )
    total_3plus = sum(
        1 for a in resolved_list if (a.get("star_level") or 0) >= 3
    )
    accuracy_3plus = (
        round((correct_3plus / total_3plus) * 100, 1) if total_3plus > 0 else None
    )

    # By star level
    by_star = {}
    for star in range(1, 6):
        star_alerts = [a for a in alerts if (a.get("star_level") or 0) == star]
        star_resolved = [
            a
            for a in star_alerts
            if a.get("outcome") in ("correct", "incorrect")
        ]
        star_correct = sum(
            1 for a in star_resolved if a.get("outcome") == "correct"
        )
        star_incorrect = len(star_resolved) - star_correct
        star_pending = sum(
            1 for a in star_alerts if a.get("outcome") == "pending"
        )
        denom = star_correct + star_incorrect
        returns = [
            a.get("actual_return")
            for a in star_resolved
            if a.get("actual_return") is not None
        ]
        by_star[str(star)] = {
            "count": len(star_alerts),
            "correct": star_correct,
            "incorrect": star_incorrect,
            "pending": star_pending,
            "accuracy": round((star_correct / denom) * 100, 1) if denom > 0 else None,
            "avg_return": (
                round(sum(returns) / len(returns), 1) if returns else None
            ),
        }

    # Alerts per day (last 30 days)
    alerts_per_day = []
    day_groups: dict[str, dict] = defaultdict(lambda: {"count": 0, "by_star": defaultdict(int)})
    for a in alerts:
        dt = _parse_dt(a.get("created_at"))
        if dt and (now - dt).days <= 30:
            day_str = dt.strftime("%Y-%m-%d")
            day_groups[day_str]["count"] += 1
            star = a.get("star_level") or 0
            day_groups[day_str]["by_star"][str(star)] += 1
    for day in sorted(day_groups.keys()):
        g = day_groups[day]
        alerts_per_day.append(
            {"date": day, "count": g["count"], "by_star": dict(g["by_star"])}
        )

    # Accuracy over time (weekly buckets, 3+ star only)
    accuracy_over_time = []
    week_groups: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0})
    for a in resolved_list:
        if (a.get("star_level") or 0) < 3:
            continue
        dt = _parse_dt(a.get("resolved_at") or a.get("created_at"))
        if dt:
            week_start = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
            week_groups[week_start]["total"] += 1
            if a.get("outcome") == "correct":
                week_groups[week_start]["correct"] += 1
    for week in sorted(week_groups.keys()):
        g = week_groups[week]
        accuracy_over_time.append(
            {
                "date": week,
                "accuracy": round((g["correct"] / g["total"]) * 100, 1)
                if g["total"] > 0
                else 0,
                "sample_size": g["total"],
            }
        )

    # Cumulative P&L ($100 per alert on 3+ star)
    cumulative_pnl = []
    pnl_alerts = sorted(
        [
            a
            for a in resolved_list
            if (a.get("star_level") or 0) >= 3
            and a.get("actual_return") is not None
        ],
        key=lambda a: a.get("resolved_at") or a.get("created_at") or "",
    )
    running = 0.0
    for a in pnl_alerts:
        ret = a["actual_return"]
        running += 100.0 * (ret / 100.0)
        dt = _parse_dt(a.get("resolved_at") or a.get("created_at"))
        cumulative_pnl.append(
            {
                "date": dt.strftime("%Y-%m-%d") if dt else "",
                "pnl": round(running, 2),
            }
        )

    # Filter distribution (correct vs incorrect)
    filter_correct: dict[str, int] = defaultdict(int)
    filter_incorrect: dict[str, int] = defaultdict(int)
    filter_names: dict[str, str] = {}
    for a in resolved_list:
        filters = a.get("filters_triggered") or []
        bucket = (
            filter_correct if a.get("outcome") == "correct" else filter_incorrect
        )
        for f in filters:
            fid = f.get("filter_id", "")
            bucket[fid] += 1
            if fid not in filter_names:
                filter_names[fid] = f.get("filter_name", fid)
    all_filter_ids = set(filter_correct.keys()) | set(filter_incorrect.keys())
    filter_distribution = sorted(
        [
            {
                "filter_id": fid,
                "name": filter_names.get(fid, fid),
                "correct": filter_correct.get(fid, 0),
                "incorrect": filter_incorrect.get(fid, 0),
            }
            for fid in all_filter_ids
        ],
        key=lambda x: x["correct"] + x["incorrect"],
        reverse=True,
    )[:20]

    # Closing soon (pending alerts with market resolution < 7 days)
    closing_soon = []
    for a in alerts:
        if a.get("outcome") != "pending":
            continue
        market = markets.get(a.get("market_id", "")) or {}
        res_date = _parse_dt(market.get("resolution_date"))
        if res_date and 0 < (res_date - now).total_seconds() < 7 * 86400:
            closing_soon.append(
                {
                    "alert_id": a.get("id"),
                    "market_question": a.get("market_question", ""),
                    "star_level": a.get("star_level"),
                    "direction": a.get("direction"),
                    "odds_at_alert": a.get("odds_at_alert"),
                    "current_odds": market.get("current_odds"),
                    "time_left": _time_left(res_date),
                    "time_left_seconds": (res_date - now).total_seconds(),
                }
            )
    closing_soon.sort(key=lambda x: x.get("time_left_seconds", 0))

    # Resolution history (last 50)
    resolution_history = []
    for a in sorted(
        resolved_list,
        key=lambda x: x.get("resolved_at") or x.get("created_at") or "",
        reverse=True,
    )[:50]:
        dt = _parse_dt(a.get("resolved_at"))
        resolution_history.append(
            {
                "date": dt.strftime("%d %b %Y") if dt else "",
                "market_question": a.get("market_question", ""),
                "direction": a.get("direction"),
                "outcome": a.get("outcome"),
                "actual_return": a.get("actual_return"),
                "star_level": a.get("star_level"),
            }
        )

    # Sell Watch stats
    alerts_with_sells = sum(
        1 for a in alerts if (a.get("total_sold_pct") or 0) > 0
    )

    return {
        "total_alerts": total,
        "active_alerts": active,
        "resolved_alerts": resolved,
        "accuracy_3plus": accuracy_3plus,
        "alerts_with_sells": alerts_with_sells,
        "by_star": by_star,
        "alerts_per_day": alerts_per_day,
        "accuracy_over_time": accuracy_over_time,
        "cumulative_pnl": cumulative_pnl,
        "filter_distribution": filter_distribution,
        "closing_soon": closing_soon,
        "resolution_history": resolution_history,
    }


# ── HTML generation ──────────────────────────────────────────


def build_html(
    alerts_json: str,
    stats_json: str,
    last_updated: str,
    template: str,
    access_key_hash: str = "",
) -> str:
    """Inject data into the HTML template."""
    html = template.replace("/* __ALERTS_DATA__ */", alerts_json)
    html = html.replace("/* __STATS_DATA__ */", stats_json)
    html = html.replace("/* __GENERATED_AT__ */", last_updated)
    html = html.replace("/* __ACCESS_KEY_HASH__ */", access_key_hash)
    return html


# ── Main entry point ─────────────────────────────────────────


def generate(output_dir: Path | None = None) -> Path:
    """Generate the dashboard HTML file.

    Returns the path to the generated file.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    db = SupabaseClient()
    data = fetch_data(db)
    print(f"Fetched {len(data['alerts'])} alerts, {len(data['markets'])} markets")
    logger.info("Fetched %d alerts, %d markets", len(data["alerts"]), len(data["markets"]))

    enriched = enrich_alerts(data["alerts"], data["markets"])
    stats = compute_stats(data["alerts"], data["markets"])
    stats["last_scan"] = data["last_scan"]
    stats["sell_events"] = data.get("sell_events", [])

    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    alerts_json = json.dumps(enriched, default=str)
    stats_json = json.dumps(stats, default=str)

    raw_key = os.environ.get("DASHBOARD_ACCESS_KEY", "")
    access_key_hash = hashlib.sha256(raw_key.encode()).hexdigest() if raw_key else ""

    html = build_html(alerts_json, stats_json, last_updated, template, access_key_hash=access_key_hash)

    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    output_path = out / "index.html"
    output_path.write_text(html, encoding="utf-8")

    logger.info("Dashboard generated: %s (%d alerts)", output_path, len(enriched))
    return output_path


if __name__ == "__main__":
    generate()
