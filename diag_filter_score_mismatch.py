"""
diag_filter_score_mismatch.py — READ-ONLY diagnostic.

Finds alerts where the sum of filters_triggered.points does NOT match
score_raw.  This reveals alerts whose filters_triggered was frozen at the
original scan while score_raw was upgraded by a later cross-scan dedup
(Bug 4 — fixed 2026-02-21).

NOT a vacuna — makes zero writes.

Usage:
    python diag_filter_score_mismatch.py [--min-star N] [--csv]

Output columns:
    id | star_level | score_raw | filter_pts_sum | delta | outcome | created_at

──────────────────────────────────────────────────────────────────────
ML TRAINING SET EXCLUSIONS
──────────────────────────────────────────────────────────────────────
The following alert IDs have corrupted or unreliable labels and should
be excluded from any ML training set:

1. Bug 4 — filters_triggered frozen (score upgraded, filters not):
   Run detected on 2026-02-21. These 24 alerts have a score_raw that
   does not match their filters_triggered array because cross-scan dedup
   upgraded score/star without updating filters (Bug 4, pre-fix).
   Their feature vectors are inconsistent: the filter set shown does NOT
   explain the score.  Do NOT use filters_triggered as a feature for these.

   IDs: [2726, 1542, 1547, 2158, 1475, 3432, 2778, 1821, 2804, 2731,
          1410, 1972, 2001, 2452, 2051, 2000, 1749, 2013, 1597, 1652,
          2316, 1997, 2725]

2. Vacuna #10 — star_level_downgrade_amount_validation:
   star_level was forcibly reduced; star_level_initial is approximate.
   IDs: [649, 845, 1066, 1468]

3. Vacuna #18 — star_level_downgrade_persistence_bug:
   star_level was forcibly reduced; star_level_initial is approximate.
   IDs: [580, 631]

Combined exclusion list (33 IDs total):
   [2726, 1542, 1547, 2158, 1475, 3432, 2778, 1821, 2804, 2731,
    1410, 1972, 2001, 2452, 2051, 2000, 1749, 2013, 1597, 1652,
    2316, 1997, 2725, 649, 845, 1066, 1468, 580, 631]

   (The 3 negative-delta alerts — 716, 672, 713 — have filters_triggered
   with more points than score_raw, likely due to _validate_stars()
   penalties applied after filter scoring.  Their score_raw IS correct;
   only the filter sum is misleading.  Judgment call for ML team.)
──────────────────────────────────────────────────────────────────────
"""

import argparse
import csv
import os
import sys

TOLERANCE = 0          # points; exact match required (multiplier is applied separately)
PAGE = 1000            # rows per Supabase request


def _fetch_all(db) -> list[dict]:
    rows, offset = [], 0
    while True:
        batch = (
            db.client.table("alerts")
            .select("id, star_level, score_raw, multiplier, filters_triggered, outcome, created_at")
            .range(offset, offset + PAGE - 1)
            .execute()
        ).data or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def run(min_star: int = 0, emit_csv: bool = False) -> list[dict]:
    sys.path.insert(0, ".")
    from src.database.supabase_client import SupabaseClient
    db = SupabaseClient()

    print(f"Fetching all alerts from Supabase …")
    all_rows = _fetch_all(db)
    print(f"  {len(all_rows)} alerts fetched.")

    mismatches = []
    skipped_no_filters = 0
    skipped_no_score = 0

    for a in all_rows:
        star = a.get("star_level") or 0
        if star < min_star:
            continue

        score_raw = a.get("score_raw")
        filters = a.get("filters_triggered") or []

        if score_raw is None:
            skipped_no_score += 1
            continue
        if not filters:
            # alerts with no filters_triggered are either very old or corrupted
            skipped_no_filters += 1
            continue

        pts_sum = sum(f.get("points", 0) for f in filters)
        delta = score_raw - pts_sum   # positive = score_raw > filter sum (upgraded)

        if abs(delta) > TOLERANCE:
            mismatches.append({
                "id": a["id"],
                "star_level": star,
                "score_raw": score_raw,
                "filter_pts_sum": pts_sum,
                "delta": delta,
                "outcome": a.get("outcome", "?"),
                "created_at": (a.get("created_at") or "")[:10],
            })

    # Sort by delta descending (worst mismatch first)
    mismatches.sort(key=lambda x: x["delta"], reverse=True)

    print(f"\n{'='*70}")
    print(f"FILTER/SCORE MISMATCH DIAGNOSTIC  (min_star={min_star})")
    print(f"{'='*70}")
    print(f"  Total alerts checked:          {len(all_rows)}")
    print(f"  Skipped (no score_raw):        {skipped_no_score}")
    print(f"  Skipped (no filters):          {skipped_no_filters}")
    print(f"  Mismatches found:              {len(mismatches)}")
    print()

    if mismatches:
        header = f"{'ID':>6}  {'★':>2}  {'score_raw':>9}  {'filter_pts':>10}  {'delta':>7}  {'outcome':>10}  {'date':>10}"
        print(header)
        print("-" * len(header))
        for m in mismatches:
            print(
                f"{m['id']:>6}  {m['star_level']:>2}  "
                f"{m['score_raw']:>9}  {m['filter_pts_sum']:>10}  "
                f"{m['delta']:>+7}  {m['outcome']:>10}  {m['created_at']:>10}"
            )
        print()
        ids = [m["id"] for m in mismatches]
        print(f"Affected IDs ({len(ids)}):")
        print(f"  {ids}")

        # Star breakdown
        from collections import Counter
        star_dist = Counter(m["star_level"] for m in mismatches)
        print(f"\nBy star level:")
        for s in sorted(star_dist, reverse=True):
            print(f"  {s}★ : {star_dist[s]}")

        outcome_dist = Counter(m["outcome"] for m in mismatches)
        print(f"\nBy outcome:")
        for o, c in outcome_dist.most_common():
            print(f"  {o}: {c}")

    if emit_csv:
        path = "/tmp/filter_score_mismatches.csv"
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=mismatches[0].keys() if mismatches else [])
            writer.writeheader()
            writer.writerows(mismatches)
        print(f"\nCSV saved: {path}")

    return mismatches


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose filter/score_raw mismatches (read-only).")
    parser.add_argument("--min-star", type=int, default=0, help="Only check alerts with star_level >= N")
    parser.add_argument("--csv", action="store_true", help="Also save results to /tmp/filter_score_mismatches.csv")
    args = parser.parse_args()

    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        print("ERROR: SUPABASE_URL y SUPABASE_KEY deben estar definidas.", file=sys.stderr)
        sys.exit(1)

    mismatches = run(min_star=args.min_star, emit_csv=args.csv)
    sys.exit(0 if not mismatches else 1)


if __name__ == "__main__":
    main()
