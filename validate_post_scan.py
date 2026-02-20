"""
validate_post_scan.py — Post-deep-scan regression checker.

Reads /tmp/pre_scan_snapshot.json (captured before a deep scan),
queries the same alert IDs in Supabase, and reports any field that
decreased (star_level, score, total_amount) or any backfill gap
(star_level_initial IS NULL).

Usage:
    python validate_post_scan.py [--snapshot /path/to/snapshot.json]

Exit codes:
    0 — all checks passed (no regressions)
    1 — regressions detected or snapshot not found
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOT = "/tmp/pre_scan_snapshot.json"
BATCH_SIZE = 50  # max IDs per Supabase query to stay within URL limits


def load_snapshot(path: str) -> dict:
    if not os.path.exists(path):
        logger.error("Snapshot not found: %s", path)
        logger.error("Run a pre-scan first (it's generated automatically before deep scans).")
        sys.exit(1)
    with open(path) as f:
        data = json.load(f)
    return data


def fetch_current(db, alert_ids: list[int]) -> dict[int, dict]:
    """Fetch current values for given alert IDs from Supabase, in batches."""
    results: dict[int, dict] = {}
    for i in range(0, len(alert_ids), BATCH_SIZE):
        batch = alert_ids[i : i + BATCH_SIZE]
        resp = (
            db.client.table("alerts")
            .select("id, star_level, star_level_initial, score, total_amount, wallets")
            .in_("id", batch)
            .execute()
        )
        for row in resp.data or []:
            wallets = row.get("wallets") or []
            row["wallet_count"] = len(wallets)
            results[row["id"]] = row
    return results


def run_validation(snapshot_path: str) -> bool:
    """
    Run all checks. Returns True if all pass, False if any regression found.
    """
    from src.database.supabase_client import SupabaseClient

    snapshot = load_snapshot(snapshot_path)
    generated_at = snapshot.get("generated_at", "unknown")
    pre_alerts: list[dict] = snapshot.get("alerts", [])

    if not pre_alerts:
        logger.warning("Snapshot is empty — nothing to compare.")
        return True

    logger.info("Snapshot: %s (%d alerts)", generated_at, len(pre_alerts))
    logger.info("Fetching current values from Supabase ...")

    db = SupabaseClient()
    alert_ids = [a["id"] for a in pre_alerts]
    current = fetch_current(db, alert_ids)

    logger.info("Fetched %d / %d alerts from DB.", len(current), len(pre_alerts))

    # ── Comparison ─────────────────────────────────────────────────────────
    regressions: list[str] = []
    warnings: list[str] = []

    for pre in pre_alerts:
        aid = pre["id"]
        now = current.get(aid)

        if now is None:
            warnings.append(f"  Alert id={aid} no longer exists in DB (deleted?)")
            continue

        # 1. star_level must not decrease
        pre_star = pre.get("star_level") or 0
        now_star = now.get("star_level") or 0
        if now_star < pre_star:
            regressions.append(
                f"  ❌ STAR DOWNGRADE  id={aid}: {pre_star}★ → {now_star}★"
            )

        # 2. score must not decrease
        pre_score = pre.get("score") or 0
        now_score = now.get("score") or 0
        if now_score < pre_score:
            regressions.append(
                f"  ❌ SCORE DROP      id={aid}: {pre_score} → {now_score}"
            )

        # 3. total_amount must not decrease (additive wallet merge guarantees this)
        pre_amt = pre.get("total_amount") or 0.0
        now_amt = now.get("total_amount") or 0.0
        if now_amt < pre_amt - 0.01:  # 1 cent tolerance for float rounding
            regressions.append(
                f"  ❌ AMOUNT DROP     id={aid}: ${pre_amt:,.2f} → ${now_amt:,.2f}"
            )

        # 4. wallet_count must not decrease
        pre_wc = pre.get("wallet_count") or 0
        now_wc = now.get("wallet_count") or 0
        if now_wc < pre_wc:
            regressions.append(
                f"  ❌ WALLET LOSS     id={aid}: {pre_wc} wallets → {now_wc} wallets"
            )

        # 5. star_level_initial must be set (backfill check)
        if now.get("star_level_initial") is None and now.get("star_level") is not None:
            warnings.append(
                f"  ⚠️  NULL initial    id={aid}: star_level={now_star} but star_level_initial=NULL"
            )

    # ── star_level upgrades (expected good behavior) ────────────────────
    upgrades: list[str] = []
    for pre in pre_alerts:
        aid = pre["id"]
        now = current.get(aid)
        if now is None:
            continue
        pre_star = pre.get("star_level") or 0
        now_star = now.get("star_level") or 0
        if now_star > pre_star:
            upgrades.append(
                f"  ✅ STAR UPGRADE    id={aid}: {pre_star}★ → {now_star}★ (expected)"
            )

    # ── Summary ─────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("POST-SCAN VALIDATION REPORT")
    print(f"Snapshot:  {generated_at}")
    print(f"Compared:  {datetime.now(timezone.utc).isoformat()}")
    print(f"Alerts:    {len(pre_alerts)} pre-scan / {len(current)} found in DB")
    print("=" * 60)

    if upgrades:
        print(f"\nUpgrades ({len(upgrades)}):")
        for u in upgrades:
            print(u)

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(w)

    if regressions:
        print(f"\nREGRESSIONS DETECTED ({len(regressions)}):")
        for r in regressions:
            print(r)
        print()
        print("ACTION REQUIRED — investigate cross-scan dedup logic.")
        return False
    else:
        print(f"\n✅ No regressions detected across {len(pre_alerts)} alerts.")
        if warnings:
            print("   (see warnings above — some may require SQL backfill)")
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate post-deep-scan alert integrity.")
    parser.add_argument(
        "--snapshot",
        default=DEFAULT_SNAPSHOT,
        help=f"Path to pre-scan snapshot JSON (default: {DEFAULT_SNAPSHOT})",
    )
    args = parser.parse_args()

    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    passed = run_validation(args.snapshot)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
