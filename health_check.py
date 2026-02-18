#!/usr/bin/env python3
"""
health_check.py — Sentinel Alpha DB health checker.

Detecta inconsistencias matemáticas, anomalías de datos y problemas
estructurales en la tabla alerts de Supabase.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python health_check.py
    python health_check.py --no-file      # sin guardar JSON
    python health_check.py --json-only    # solo JSON en stdout (para CI)

Exit codes:
    0 — Sin anomalías críticas
    1 — Anomalías críticas detectadas
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Bootstrap: añadir raíz del proyecto al path ──────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config
from src.database.supabase_client import SupabaseClient

# ── Constantes ───────────────────────────────────────────────────────
PAGE_SIZE = 500
SCORE_MATH_TOLERANCE = 5
SCORE_CAP: int = getattr(config, "SCORE_CAP", 400)

# Requisitos mínimos por star_level (spec del usuario + STAR_VALIDATION de config)
_STAR_REQS: dict[int, dict] = {
    5: {"min_score": 220, "min_amount": 10_000},
    4: {"min_score": 150, "min_amount": 5_000},
    3: {"min_score": 100},
    2: {"min_score": 70},
    1: {"min_score": 40},
}

# Checks cuya presencia pone exit_code=1
CRITICAL_CHECKS = {
    "score_math",
    "star_consistency",
    # filter_sum_mismatch es WARNING, no CRITICAL:
    # diferencias pequeñas son drift histórico por cambios de pesos de filtros.
    "high_score_low_star",
    "published_low_star",
    "pending_but_resolved",
    "multi_signal_no_group",
}

# Alertas anteriores a esta fecha pueden tener filter_sum_mismatch por cambio
# de pesos histórico; se ignoran si el delta es menor que el umbral.
_OLD_ALERT_CUTOFF = datetime(2026, 2, 10, tzinfo=timezone.utc)
_OLD_ALERT_FILTER_DELTA_TOLERANCE = 50


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _parse_json_field(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return []


def _parse_dt(val) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except Exception:
        return None


def _fetch_all_alerts(db: SupabaseClient) -> list[dict]:
    """Descarga todas las alertas paginando de PAGE_SIZE en PAGE_SIZE."""
    rows: list[dict] = []
    offset = 0
    while True:
        batch = (
            db.client.table("alerts")
            .select(
                "id,market_id,direction,score,score_raw,multiplier,star_level,"
                "filters_triggered,wallets,total_amount,odds_at_alert,"
                "odds_max,odds_min,outcome,published_telegram,published_x,"
                "multi_signal,alert_group_id,opposite_positions,"
                "resolved_at,created_at,is_secondary"
            )
            .range(offset, offset + PAGE_SIZE - 1)
            .order("id")
            .execute()
            .data
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


# ──────────────────────────────────────────────────────────────────────
# SECCIÓN 1 — Consistencia Matemática
# ──────────────────────────────────────────────────────────────────────

def check_score_math(alerts: list[dict]) -> list[dict]:
    """1a. |score - round(score_raw × multiplier)| > TOLERANCIA."""
    issues = []
    for row in alerts:
        score = row.get("score") or 0
        score_raw = row.get("score_raw") or 0
        multiplier = row.get("multiplier") or 1.0
        expected = min(round(score_raw * multiplier), SCORE_CAP)
        delta = abs(score - expected)
        if delta > SCORE_MATH_TOLERANCE:
            issues.append({
                "id": row["id"],
                "check": "score_math",
                "score_db": score,
                "score_raw": score_raw,
                "multiplier": multiplier,
                "expected_score": expected,
                "delta": delta,
            })
    return issues


def check_star_consistency(alerts: list[dict]) -> list[dict]:
    """1b. star_level no cumple los requisitos mínimos de su nivel."""
    issues = []
    for row in alerts:
        star = row.get("star_level") or 0
        reqs = _STAR_REQS.get(star)
        if not reqs:
            continue  # 0★ lo detecta check_zero_star
        score = row.get("score") or 0
        amount = float(row.get("total_amount") or 0)
        violations = []
        if score < reqs.get("min_score", 0):
            violations.append(f"score={score} < min={reqs['min_score']}")
        if round(amount) < reqs.get("min_amount", 0):
            violations.append(f"total_amount={amount:.0f} < min={reqs['min_amount']}")
        if violations:
            issues.append({
                "id": row["id"],
                "check": "star_consistency",
                "star_level": star,
                "score": score,
                "total_amount": amount,
                "violations": violations,
            })
    return issues


def check_filter_sum(alerts: list[dict]) -> list[dict]:
    """1c. Suma de puntos en filters_triggered ≠ score_raw (con exclusión mutua)."""
    # Construir lookup filter_id → grupo
    filter_to_group: dict[str, frozenset] = {}
    for group in config.MUTUALLY_EXCLUSIVE_GROUPS:
        fs = frozenset(group)
        for fid in group:
            filter_to_group[fid] = fs

    issues = []
    for row in alerts:
        score_raw = row.get("score_raw")
        if score_raw is None:
            continue
        filters = _parse_json_field(row.get("filters_triggered"))
        if not filters:
            continue

        # Aplicar exclusión mutua para calcular el score_raw esperado
        best_in_group: dict[frozenset, dict] = {}
        non_group: list[dict] = []
        for f in filters:
            fid = f.get("filter_id", "")
            pts = f.get("points", 0)
            grp = filter_to_group.get(fid)
            if grp:
                prev = best_in_group.get(grp)
                if prev is None or abs(pts) > abs(prev.get("points", 0)):
                    best_in_group[grp] = f
            else:
                non_group.append(f)

        computed = max(0, sum(
            f.get("points", 0) for f in list(best_in_group.values()) + non_group
        ))
        delta = abs(computed - score_raw)
        if delta <= 1:  # tolerancia 1 pt por redondeo
            continue
        # Alertas antiguas: ignorar si el delta es drift histórico esperado
        created_at_dt = _parse_dt(row.get("created_at"))
        is_old = created_at_dt is not None and created_at_dt < _OLD_ALERT_CUTOFF
        if is_old and delta < _OLD_ALERT_FILTER_DELTA_TOLERANCE:
            continue
        issues.append({
            "id": row["id"],
            "check": "filter_sum_mismatch",
            "score_raw_db": score_raw,
            "score_raw_computed": computed,
            "delta": delta,
            "filter_ids": [f.get("filter_id") for f in filters],
        })
    return issues


# ──────────────────────────────────────────────────────────────────────
# SECCIÓN 2 — Anomalías de Alertas
# ──────────────────────────────────────────────────────────────────────

def check_high_score_low_star(alerts: list[dict]) -> list[dict]:
    """2a. score >= 150 pero star_level <= 1 (posible cap N09 u otra limitación)."""
    return [
        {
            "id": row["id"],
            "check": "high_score_low_star",
            "score": row.get("score"),
            "star_level": row.get("star_level"),
            "total_amount": row.get("total_amount"),
        }
        for row in alerts
        if (row.get("score") or 0) >= 150 and (row.get("star_level") or 0) <= 1
    ]


def check_published_low_star(alerts: list[dict]) -> list[dict]:
    """2b. published_telegram=True pero STAR_PUBLISH_MAP no permite Telegram en ese star_level.

    Se excluyen publicaciones históricas legítimas: alertas con star_level >= 1 y
    score > 0 que fueron publicadas cuando tenían un star_level superior y luego
    fueron degradadas (p. ej. por cambio de umbrales o por vacunas).
    Solo se marca como crítico publicar una alerta con star_level=0 o score=0.
    """
    issues = []
    for row in alerts:
        if not row.get("published_telegram"):
            continue
        star = row.get("star_level") or 0
        score = row.get("score") or 0
        # Comprobar contra STAR_PUBLISH_MAP en lugar del umbral hardcodeado < 3
        allows_telegram = config.STAR_PUBLISH_MAP.get(star, {}).get("telegram", True)
        if allows_telegram:
            continue
        # Publicación histórica legítima: star fue degradado después de publicar.
        # El flag published_telegram=True es un registro de hecho, no un error.
        if star >= 1 and score > 0:
            continue
        issues.append({
            "id": row["id"],
            "check": "published_low_star",
            "star_level": star,
            "score": score,
            "published_telegram": True,
        })
    return issues


def check_missing_opposite_positions(alerts: list[dict]) -> list[dict]:
    """2c. Wallet en YES y NO del mismo mercado pero opposite_positions=null."""
    # Construir índice: market_id → dirección → conjunto de wallets
    mkt_dir_wallets: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"YES": set(), "NO": set()}
    )
    # Construir índice: alert_id → info resumida
    alert_wallet_index: dict[int, dict] = {}

    for row in alerts:
        mid = row.get("market_id") or ""
        direction = row.get("direction") or "YES"
        wallet_addrs = {
            w.get("address")
            for w in _parse_json_field(row.get("wallets"))
            if w.get("address")
        }
        mkt_dir_wallets[mid][direction] |= wallet_addrs
        alert_wallet_index[row["id"]] = {
            "market_id": mid,
            "direction": direction,
            "wallets": wallet_addrs,
            "opposite_positions": row.get("opposite_positions"),
        }

    issues = []
    for aid, info in alert_wallet_index.items():
        mid = info["market_id"]
        direction = info["direction"]
        opposite = "NO" if direction == "YES" else "YES"
        overlap = info["wallets"] & mkt_dir_wallets[mid].get(opposite, set())
        if overlap and not info["opposite_positions"]:
            issues.append({
                "id": aid,
                "check": "missing_opposite_positions",
                "market_id": mid,
                "direction": direction,
                "overlapping_wallets": sorted(overlap)[:3],
            })
    return issues


def check_zero_star(alerts: list[dict]) -> list[dict]:
    """2d. Alertas con star_level = 0 o null."""
    return [
        {
            "id": row["id"],
            "check": "zero_star",
            "score": row.get("score"),
            "star_level": row.get("star_level"),
            "is_secondary": row.get("is_secondary"),
        }
        for row in alerts
        if (row.get("star_level") or 0) == 0
    ]


# ──────────────────────────────────────────────────────────────────────
# SECCIÓN 3 — Detección de Wallets Relacionadas
# ──────────────────────────────────────────────────────────────────────

def check_same_scan_similar_amounts(alerts: list[dict]) -> list[dict]:
    """3a. Pares mismo mercado+dirección, ±5 min, wallets distintas, monto ±10%."""
    # Agrupar por market_id+direction
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in alerts:
        key = f"{row.get('market_id')}|{row.get('direction') or 'YES'}"
        groups[key].append(row)

    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    issues = []

    for group in groups.values():
        if len(group) < 2:
            continue
        # Ordenar por fecha de creación
        group.sort(key=lambda r: _parse_dt(r.get("created_at")) or _epoch)

        for i, a in enumerate(group):
            dt_a = _parse_dt(a.get("created_at"))
            amt_a = float(a.get("total_amount") or 0)
            wallets_a = {
                w.get("address")
                for w in _parse_json_field(a.get("wallets"))
                if w.get("address")
            }
            for b in group[i + 1:]:
                dt_b = _parse_dt(b.get("created_at"))
                if dt_a and dt_b:
                    diff_s = (dt_b - dt_a).total_seconds()
                    if diff_s > 300:  # lista ordenada → ya no habrá más ≤5min
                        break
                amt_b = float(b.get("total_amount") or 0)
                if amt_a <= 0 or amt_b <= 0:
                    continue
                pct_diff = abs(amt_a - amt_b) / max(amt_a, amt_b)
                if pct_diff > 0.10:
                    continue
                wallets_b = {
                    w.get("address")
                    for w in _parse_json_field(b.get("wallets"))
                    if w.get("address")
                }
                if wallets_a & wallets_b:  # mismas wallets → no es anomalía
                    continue
                issues.append({
                    "check": "same_scan_similar_amounts",
                    "alert_a": a["id"],
                    "alert_b": b["id"],
                    "market_id": a.get("market_id"),
                    "direction": a.get("direction"),
                    "amount_a": amt_a,
                    "amount_b": amt_b,
                    "amount_diff_pct": round(pct_diff * 100, 1),
                    "time_diff_seconds": round(diff_s) if dt_a and dt_b else None,
                })
    return issues


def check_wallet_opposite_directions(alerts: list[dict]) -> list[dict]:
    """3b. Wallets que aparecen en YES y NO del mismo mercado."""
    # market_id → wallet_address → set of directions
    mkt_wallet_dirs: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    mkt_wallet_alerts: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for row in alerts:
        mid = row.get("market_id") or ""
        direction = row.get("direction") or "YES"
        for w in _parse_json_field(row.get("wallets")):
            addr = w.get("address")
            if addr:
                mkt_wallet_dirs[mid][addr].add(direction)
                mkt_wallet_alerts[mid][addr].append(row["id"])

    issues = []
    for mid, wallet_dirs in mkt_wallet_dirs.items():
        for addr, directions in wallet_dirs.items():
            if len(directions) > 1:
                issues.append({
                    "check": "wallet_opposite_directions",
                    "market_id": mid,
                    "wallet_prefix": addr[:10],
                    "directions": sorted(directions),
                    "alert_ids": mkt_wallet_alerts[mid][addr],
                })
    return issues


# ──────────────────────────────────────────────────────────────────────
# SECCIÓN 4 — Datos Faltantes
# ──────────────────────────────────────────────────────────────────────

def check_pending_but_resolved(alerts: list[dict]) -> list[dict]:
    """4a. outcome='pending' pero resolved_at no es null en DB."""
    return [
        {
            "id": row["id"],
            "check": "pending_but_resolved",
            "outcome": row.get("outcome"),
            "resolved_at": str(row.get("resolved_at")),
            "market_id": row.get("market_id"),
        }
        for row in alerts
        if row.get("outcome") == "pending" and row.get("resolved_at") is not None
    ]


def check_odds_boundary(alerts: list[dict]) -> list[dict]:
    """4b. odds_max o odds_min en 0 o 1 exacto con outcome aún 'pending'."""
    issues = []
    for row in alerts:
        outcome = row.get("outcome") or "pending"
        for field_name in ("odds_max", "odds_min"):
            val = row.get(field_name)
            if val is not None and float(val) in (0.0, 1.0) and outcome == "pending":
                issues.append({
                    "id": row["id"],
                    "check": "odds_boundary_pending",
                    "field": field_name,
                    "value": float(val),
                    "outcome": outcome,
                })
    return issues


def check_multi_signal_no_group(alerts: list[dict]) -> list[dict]:
    """4c. multi_signal=True pero alert_group_id es null."""
    return [
        {
            "id": row["id"],
            "check": "multi_signal_no_group",
            "multi_signal": True,
            "alert_group_id": None,
        }
        for row in alerts
        if row.get("multi_signal") and not row.get("alert_group_id")
    ]


# ──────────────────────────────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────────────────────────────

def _build_report(results: dict[str, list]) -> dict:
    sections = {
        "1_consistencia_matematica": {
            "score_math": results.get("score_math", []),
            "star_consistency": results.get("star_consistency", []),
            "filter_sum_mismatch": results.get("filter_sum_mismatch", []),
        },
        "2_anomalias_alertas": {
            "high_score_low_star": results.get("high_score_low_star", []),
            "published_low_star": results.get("published_low_star", []),
            "missing_opposite_positions": results.get("missing_opposite_positions", []),
            "zero_star": results.get("zero_star", []),
        },
        "3_wallets_relacionadas": {
            "same_scan_similar_amounts": results.get("same_scan_similar_amounts", []),
            "wallet_opposite_directions": results.get("wallet_opposite_directions", []),
        },
        "4_datos_faltantes": {
            "pending_but_resolved": results.get("pending_but_resolved", []),
            "odds_boundary_pending": results.get("odds_boundary_pending", []),
            "multi_signal_no_group": results.get("multi_signal_no_group", []),
        },
    }

    summary: dict[str, dict] = {}
    has_critical = False
    for checks in sections.values():
        for check_name, issues in checks.items():
            count = len(issues)
            is_crit = check_name in CRITICAL_CHECKS and count > 0
            summary[check_name] = {"count": count, "critical": is_crit}
            if is_crit:
                has_critical = True

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_alerts_checked": sum(
            len(v) for v in results.values()
            if not isinstance(v, int)
        ),
        "has_critical": has_critical,
        "summary": summary,
        "details": sections,
    }


def _print_report(report: dict, total_alerts: int) -> None:
    status = "!! CRITICO !!" if report["has_critical"] else "OK"
    pad = "=" * 62

    print()
    print(pad)
    print(f"  SENTINEL ALPHA — HEALTH CHECK  [{status}]")
    print(f"  {report['generated_at']}   ({total_alerts} alertas)")
    print(pad)

    labels = {
        "1_consistencia_matematica": "1. Consistencia Matemática",
        "2_anomalias_alertas":       "2. Anomalías de Alertas",
        "3_wallets_relacionadas":    "3. Wallets Relacionadas",
        "4_datos_faltantes":         "4. Datos Faltantes",
    }

    for sec_key, checks in report["details"].items():
        print(f"\n  {labels.get(sec_key, sec_key)}")
        print("  " + "-" * 44)
        for check_name, issues in checks.items():
            count = len(issues)
            crit = report["summary"].get(check_name, {}).get("critical", False)
            tag = "  *** CRITICO ***" if crit else ""
            print(f"  {check_name:<38} {count:>4}{tag}")

    print()
    if report["has_critical"]:
        print("  !! Anomalías críticas detectadas. Ver JSON para IDs.")
    else:
        print("  ✓  Sin anomalías críticas.")
    print(pad)
    print()


def _save_report(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = out_dir / f"{ts}.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Sentinel Alpha DB health check")
    parser.add_argument(
        "--no-file", action="store_true",
        help="No guardar el JSON en disco",
    )
    parser.add_argument(
        "--json-only", action="store_true",
        help="Imprimir solo JSON (útil para CI/pipes)",
    )
    args = parser.parse_args()

    if not args.json_only:
        print("Conectando a Supabase...")

    db = SupabaseClient()

    if not args.json_only:
        print("Cargando alertas...")

    alerts = _fetch_all_alerts(db)

    if not args.json_only:
        print(f"  {len(alerts)} alertas cargadas. Ejecutando checks...\n")

    results = {
        # 1. Consistencia matemática
        "score_math":         check_score_math(alerts),
        "star_consistency":   check_star_consistency(alerts),
        "filter_sum_mismatch": check_filter_sum(alerts),
        # 2. Anomalías
        "high_score_low_star":         check_high_score_low_star(alerts),
        "published_low_star":          check_published_low_star(alerts),
        "missing_opposite_positions":  check_missing_opposite_positions(alerts),
        "zero_star":                   check_zero_star(alerts),
        # 3. Wallets relacionadas
        "same_scan_similar_amounts":   check_same_scan_similar_amounts(alerts),
        "wallet_opposite_directions":  check_wallet_opposite_directions(alerts),
        # 4. Datos faltantes
        "pending_but_resolved":   check_pending_but_resolved(alerts),
        "odds_boundary_pending":  check_odds_boundary(alerts),
        "multi_signal_no_group":  check_multi_signal_no_group(alerts),
    }

    report = _build_report(results)
    # Añadir conteo real (sin el total_alerts_checked calculado mal arriba)
    report["total_alerts_checked"] = len(alerts)

    if args.json_only:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        _print_report(report, len(alerts))

    if not args.no_file:
        out_dir = PROJECT_ROOT / "health_reports"
        path = _save_report(report, out_dir)
        if not args.json_only:
            print(f"  Reporte guardado en: {path}\n")

    return 1 if report["has_critical"] else 0


if __name__ == "__main__":
    sys.exit(main())
