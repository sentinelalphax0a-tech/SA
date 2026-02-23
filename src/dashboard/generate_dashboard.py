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
    # Paginate alerts to bypass the PostgREST server-side max_rows=1000 cap.
    # .limit(N) alone is silently overridden by the server; .range() forces
    # explicit offsets and retrieves every row regardless of max_rows setting.
    all_alerts: list[dict] = []
    _PAGE = 1000
    _offset = 0
    while True:
        _batch = (
            db.client.table("alerts")
            .select("*")
            .order("created_at", desc=True)
            .range(_offset, _offset + _PAGE - 1)
            .execute()
        )
        _rows = _batch.data or []
        all_alerts.extend(_rows)
        if len(_rows) < _PAGE:
            break
        _offset += _PAGE
    markets_resp = db.client.table("markets").select("*").execute()
    scans_resp = (
        db.client.table("scans")
        .select("*")
        .order("timestamp", desc=True)
        .limit(1)
        .execute()
    )

    # Sell events — two separate queries with different scopes:
    # 1. Recent (7 days) → "Recent Sell Activity" section in dashboard
    try:
        sell_events = db.get_recent_sell_events(hours=168)
    except Exception:
        sell_events = []

    # 2. All time → per-alert timeline in detail rows (no temporal filter)
    try:
        all_sell_events_resp = (
            db.client.table("alert_sell_events")
            .select("*")
            .order("detected_at", desc=False)  # chronological for timeline
            .execute()
        )
        all_sell_events = all_sell_events_resp.data or []
    except Exception:
        all_sell_events = []

    # Fetch sold positions for hold durations + merge/position_gone close reasons
    try:
        sold_resp = (
            db.client.table("wallet_positions")
            .select("alert_id, hold_duration_hours, close_reason, wallet_address, sell_timestamp")
            .neq("current_status", "open")
            .execute()
        )
        # alert_id → minimum hold_duration_hours (earliest seller wins)
        hold_durations: dict[int, float] = {}
        # alert_id → list of position closure events with close_reason (for timeline)
        position_closures: dict[int, list[dict]] = {}
        for p in (sold_resp.data or []):
            aid = p.get("alert_id")
            if not aid:
                continue
            h = p.get("hold_duration_hours")
            if h is not None:
                if aid not in hold_durations or h < hold_durations[aid]:
                    hold_durations[aid] = h
            # Collect non-CLOB closure reasons for the timeline
            reason = p.get("close_reason")
            if reason and reason != "sell_clob":
                position_closures.setdefault(aid, []).append({
                    "wallet_address": p.get("wallet_address"),
                    "close_reason": reason,
                    "sell_timestamp": p.get("sell_timestamp"),
                })
    except Exception:
        hold_durations = {}
        position_closures = {}

    markets_list = markets_resp.data or []
    return {
        "alerts": all_alerts,
        "markets": {m["market_id"]: m for m in markets_list},
        "last_scan": (scans_resp.data or [{}])[0] if scans_resp.data else {},
        "sell_events": sell_events,
        "all_sell_events": all_sell_events,
        "hold_durations": hold_durations,
        "position_closures": position_closures,
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


def enrich_alerts(
    alerts: list[dict],
    markets: dict,
    hold_durations: dict | None = None,
    all_sell_events: list[dict] | None = None,
    position_closures: dict | None = None,
) -> list[dict]:
    """Add derived display fields to each alert."""
    now = datetime.now(timezone.utc)

    # Build index of all sell events by alert_id for per-alert timeline.
    # Uses all_sell_events (no temporal filter) so the timeline shows full history.
    sell_by_alert: dict[int, list[dict]] = {}
    for se in (all_sell_events or []):
        aid = se.get("alert_id")
        if aid:
            sell_by_alert.setdefault(aid, []).append(se)

    # Build (market_id, direction) → max star_level index for opposite-signal detection.
    # Covers all alerts (pending + resolved) so the badge shows even if the opposite
    # signal has a different outcome.
    _mkt_dir_star: dict[tuple, int] = {}
    for _a in alerts:
        _k = (_a.get("market_id", ""), (_a.get("direction") or "YES").upper())
        _star = _a.get("star_level") or 0
        if _star > _mkt_dir_star.get(_k, -1):
            _mkt_dir_star[_k] = _star

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
        close_reason = (a.get("close_reason") or "").lower()
        a["has_sells"] = total_sold > 0
        if total_sold > 0:
            a["sold_pct_display"] = f"{total_sold * 100:.0f}% sold"
        else:
            a["sold_pct_display"] = None

        # exit_label: unified exit status combining total_sold_pct + close_reason.
        # Priority: non-CLOB close_reason wins over pct-based label.
        if close_reason == "merge_suspected":
            a["exit_label"] = "\U0001f500 Merge Suspected"
        elif close_reason == "merge_confirmed":
            a["exit_label"] = "\U0001f500 Merge Confirmed"
        elif close_reason == "position_gone":
            a["exit_label"] = "\U0001f47b Position Gone"
        elif close_reason == "net_zero":
            a["exit_label"] = "\u26a0\ufe0f Net Zero"
        elif total_sold >= 1.0 or close_reason == "sell_clob":
            a["exit_label"] = "\U0001f4c9 Full Exit"
        elif total_sold > 0:
            a["exit_label"] = f"\U0001f4c9 Partial Exit ({total_sold * 100:.0f}%)"
        else:
            a["exit_label"] = None

        # Hold indicator (resolved alerts only)
        alert_outcome = a.get("outcome", "pending")
        if alert_outcome in ("correct", "incorrect"):
            if total_sold > 0:
                a["hold_label"] = "sold-early"
                hold_h = (hold_durations or {}).get(a.get("id"))
                if hold_h is not None:
                    a["hold_display"] = f"Sold after {round(hold_h, 1)}h"
                else:
                    a["hold_display"] = "Sold early"
            else:
                a["hold_label"] = "held"
                a["hold_display"] = "Held to resolution"
        else:
            a["hold_label"] = None
            a["hold_display"] = None

        # Per-alert sell history for timeline panel (all-time, no temporal filter)
        a["sell_events_history"] = sell_by_alert.get(a.get("id"), [])
        # Per-alert position closures (merge/position_gone events, not visible in CLOB)
        a["position_closures_history"] = (position_closures or {}).get(a.get("id"), [])

        # Opposite-signal badge: is there a signal in the opposite direction?
        opp_dir = "NO" if direction == "YES" else "YES"
        opp_key = (a.get("market_id", ""), opp_dir)
        opp_star = _mkt_dir_star.get(opp_key)
        a["opposite_signal"] = (
            {"direction": opp_dir, "star_level": opp_star}
            if opp_star is not None
            else None
        )

        # Stale badge: pending alert whose market resolution date passed >48h ago.
        # Uses market_resolution_date (already computed above from markets table).
        if a.get("outcome") == "pending" and res_date is not None:
            a["is_stale"] = (now - res_date).total_seconds() > 48 * 3600
        else:
            a["is_stale"] = False

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
    # Merge counters
    merge_confirmed_count = sum(1 for a in alerts if a.get("merge_confirmed"))
    merge_suspected_count = sum(
        1 for a in alerts if a.get("merge_suspected") and not a.get("merge_confirmed")
    )

    # Deduplicate resolved_list by (market_id, direction): 1 signal per pair.
    # Takes the highest star_level per group. Used for all accuracy / history
    # calculations so that multiple scans of the same market count as ONE signal.
    # Volume metrics (total_alerts, resolved count, by_star["count"]) use the raw list.
    _dedup_seen: dict[tuple, dict] = {}
    for _a in resolved_list:
        _k = (_a.get("market_id", ""), (_a.get("direction") or "YES").upper())
        if _k not in _dedup_seen or (_a.get("star_level") or 0) > (_dedup_seen[_k].get("star_level") or 0):
            _dedup_seen[_k] = _a
    dedup_resolved = list(_dedup_seen.values())

    # Count map: how many raw resolved alerts exist per (market_id, direction).
    # Used to populate siblings_count in resolution_history.
    _mid_dir_counts: dict[tuple, int] = {}
    for _a in resolved_list:
        _k = (_a.get("market_id", ""), (_a.get("direction") or "YES").upper())
        _mid_dir_counts[_k] = _mid_dir_counts.get(_k, 0) + 1

    # Exclude merge_confirmed from accuracy/P&L — not real trading signals
    stats_resolved = [a for a in dedup_resolved if not a.get("merge_confirmed")]
    correct_3plus = sum(
        1
        for a in stats_resolved
        if a.get("outcome") == "correct" and (a.get("star_level") or 0) >= 3
    )
    total_3plus = sum(
        1 for a in stats_resolved if (a.get("star_level") or 0) >= 3
    )
    accuracy_3plus = (
        round((correct_3plus / total_3plus) * 100, 1) if total_3plus > 0 else None
    )

    # Accuracy split: held-to-resolution vs sold-early (3+ stars, merge_confirmed excl.)
    held_3plus = [
        a for a in stats_resolved
        if (a.get("star_level") or 0) >= 3 and not (a.get("total_sold_pct") or 0)
    ]
    sold_early_3plus = [
        a for a in stats_resolved
        if (a.get("star_level") or 0) >= 3 and (a.get("total_sold_pct") or 0) > 0
    ]
    correct_held = sum(1 for a in held_3plus if a.get("outcome") == "correct")
    correct_sold = sum(1 for a in sold_early_3plus if a.get("outcome") == "correct")
    accuracy_3plus_held = (
        round((correct_held / len(held_3plus)) * 100, 1) if held_3plus else None
    )
    accuracy_3plus_sold_early = (
        round((correct_sold / len(sold_early_3plus)) * 100, 1) if sold_early_3plus else None
    )

    # By star level.
    # count/pending = raw volume (all alerts detected at this star level).
    # correct/incorrect/accuracy/avg_return = dedup_resolved so each unique
    # signal is counted once, same as the headline accuracy_3plus.
    by_star = {}
    for star in range(1, 6):
        star_alerts = [a for a in alerts if (a.get("star_level") or 0) == star]
        star_pending = sum(1 for a in star_alerts if a.get("outcome") == "pending")
        # Accuracy stats from deduplicated resolved signals
        star_stats = [
            a for a in dedup_resolved
            if (a.get("star_level") or 0) == star and not a.get("merge_confirmed")
        ]
        star_correct = sum(1 for a in star_stats if a.get("outcome") == "correct")
        star_incorrect = len(star_stats) - star_correct
        denom = star_correct + star_incorrect
        returns = [
            a.get("actual_return")
            for a in star_stats
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
    for a in stats_resolved:
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
            for a in stats_resolved
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
    for a in stats_resolved:
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

    # Resolution history (last 50 unique signals, most recent first).
    # Uses dedup_resolved so the same market+direction appears at most once.
    # siblings_count = how many additional raw alerts shared this signal.
    resolution_history = []
    for a in sorted(
        dedup_resolved,
        key=lambda x: x.get("resolved_at") or x.get("created_at") or "",
        reverse=True,
    )[:50]:
        dt = _parse_dt(a.get("resolved_at"))
        _k = (a.get("market_id", ""), (a.get("direction") or "YES").upper())
        resolution_history.append(
            {
                "date": dt.strftime("%d %b %Y") if dt else "",
                "market_question": a.get("market_question", ""),
                "direction": a.get("direction"),
                "outcome": a.get("outcome"),
                "actual_return": a.get("actual_return"),
                "star_level": a.get("star_level"),
                "siblings_count": _mid_dir_counts.get(_k, 1) - 1,
            }
        )

    # Sell Watch stats — exclude secondaries so we count unique signals only
    alerts_with_sells = sum(
        1 for a in alerts
        if (a.get("total_sold_pct") or 0) > 0 and not a.get("is_secondary")
    )

    return {
        "total_alerts": total,
        "active_alerts": active,
        "resolved_alerts": resolved,
        "accuracy_3plus": accuracy_3plus,
        "alerts_with_sells": alerts_with_sells,
        "merge_confirmed_count": merge_confirmed_count,
        "merge_suspected_count": merge_suspected_count,
        "accuracy_3plus_held": accuracy_3plus_held,
        "accuracy_3plus_sold_early": accuracy_3plus_sold_early,
        "by_star": by_star,
        "alerts_per_day": alerts_per_day,
        "accuracy_over_time": accuracy_over_time,
        "cumulative_pnl": cumulative_pnl,
        "filter_distribution": filter_distribution,
        "closing_soon": closing_soon,
        "resolution_history": resolution_history,
    }


# ── Alert grouping ───────────────────────────────────────────


def _group_by_market_dir(alerts: list[dict]) -> list[dict]:
    """Group a list of alerts by (market_id, direction), highest star as primary.

    Shared by both pending and resolved grouping — same sibling logic everywhere.
    Caller is responsible for the final sort order.
    """
    from collections import defaultdict

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for a in alerts:
        key = (a.get("market_id", ""), (a.get("direction") or "YES").upper())
        groups[key].append(a)

    result: list[dict] = []
    for key, group in groups.items():
        group.sort(
            key=lambda x: (x.get("star_level") or 0, x.get("score") or 0),
            reverse=True,
        )
        primary = group[0]
        siblings = group[1:]

        by_star: dict[str, int] = {}
        for s in siblings:
            star = s.get("star_level") or 0
            if star >= 3:
                by_star[str(star)] = by_star.get(str(star), 0) + 1

        primary["siblings_count"] = len(siblings)
        primary["siblings_by_star"] = by_star
        primary["siblings"] = siblings
        result.append(primary)

    return result


def group_alerts_by_market(alerts: list[dict]) -> list[dict]:
    """Group alerts by (market_id, direction), one row per pair.

    YES and NO signals on the same market are kept separate — they are
    independent trading signals.

    Both pending AND resolved alerts are grouped so the table never shows
    the same signal multiple times regardless of outcome. The alert with
    the highest star_level (then score) is the primary row; the rest
    collapse into primary["siblings"], primary["siblings_count"], and
    primary["siblings_by_star"] (only stars >= 3 are counted in the badge).
    """
    pending: list[dict] = []
    resolved: list[dict] = []
    for a in alerts:
        if a.get("outcome", "pending") == "pending":
            pending.append(a)
        else:
            resolved.append(a)

    # Group pending — highest star first, then most recent
    grouped_pending = _group_by_market_dir(pending)
    grouped_pending.sort(
        key=lambda x: (x.get("star_level") or 0, x.get("created_at") or ""),
        reverse=True,
    )

    # Group resolved — most recently resolved first
    grouped_resolved = _group_by_market_dir(resolved)
    grouped_resolved.sort(
        key=lambda x: x.get("resolved_at") or x.get("created_at") or "",
        reverse=True,
    )

    return grouped_pending + grouped_resolved


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

    enriched = enrich_alerts(
        data["alerts"],
        data["markets"],
        data.get("hold_durations"),
        data.get("all_sell_events"),
        data.get("position_closures"),
    )
    enriched = group_alerts_by_market(enriched)
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
