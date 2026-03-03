"""
============================================================
VACUNA: backfill_new_fields
Fecha: 2026-03-03
Descripción:
  Backfill one-time de los campos añadidos en los Fixes 2 y 4
  de la sesión 2026-03-03.

  CAMPOS BACKFILLADOS:
    1. realized_return — Fórmula Fix 2: PnL ponderado de ventas CLOB
       + porción no vendida al precio del mercado. Para alertas sin
       sell events: realized_return = actual_return.

    2. odds_at_resolution_raw — Fix 4: YES price real del mercado
       en el momento de resolución, desde market_snapshots dentro
       de una ventana ±1h de resolved_at.

  CAMPOS NO BACKFILLADOS (con documentación):
    - sell_timestamp (alert_sell_events): quedan NULL para registros
      anteriores a 2026-03-03. No se rellena con detected_at para
      no contaminar el dataset ML con timestamps aproximados.
      CUTOFF: registros con detected_at < 2026-03-03 son imprecisos.

    - additional_buys_count / additional_buys_amount: la información
      solo fue enviada a Telegram y no fue persistida en DB. No hay
      fuente de datos para reconstruirla.
      CUTOFF: alertas anteriores a 2026-03-03 tienen 0/0.0 por defecto.

Tablas afectadas: alerts
Filas estimadas a modificar: variable (ver diagnóstico)
Reversible: SI — realized_return y odds_at_resolution_raw eran NULL,
  se pueden resetear a NULL con un UPDATE simple.
Aplicada en producción: NO
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-03-03 16:00 UTC
  - Part 1 (realized_return): 2447 filas modificadas (todas sin sell events → realized=actual_return)
  - Part 2 (odds_at_resolution_raw): 3 filas modificadas (alertas #319, #1053, #5213)
  - Verificación Part 1: OK — 0 alertas con NULL pendientes
  - Verificación Part 2: 2444 alertas con NULL esperado (sin snapshots históricos previos a 2026-03-03)
  - Observaciones: ninguna
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Configuración ─────────────────────────────────────────
# Cambiar a False solo después de revisar el diagnóstico y confirmar los samples.
DRY_RUN = True

PAGE_SIZE = 500
SAMPLE_SIZE = 5      # alertas a mostrar en el preview del dry-run
SNAPSHOT_WINDOW_H = 1  # horas de ventana para buscar snapshot cercano a resolved_at


# ══════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════

def _calc_realized_return(
    actual_return: float,
    total_sold_pct: float,
    sell_events: list[dict],
) -> float:
    """Misma fórmula que MarketResolver._calc_realized_return (Fix 2)."""
    if not sell_events or total_sold_pct <= 0:
        return actual_return

    valid = [
        (se.get("sell_pct") or 0.0, se["pnl_pct"])
        for se in sell_events
        if se.get("pnl_pct") is not None and (se.get("sell_pct") or 0) > 0
    ]
    if not valid:
        return actual_return

    total_wt = sum(sp for sp, _ in valid)
    if total_wt <= 0:
        return actual_return

    weighted_pnl_sold = sum(sp * pp for sp, pp in valid) / total_wt
    sold_frac = min(1.0, total_sold_pct)
    unsold_frac = max(0.0, 1.0 - sold_frac)
    return round(sold_frac * weighted_pnl_sold + unsold_frac * actual_return, 2)


def _parse_ts(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _get_snapshot_near_ts(
    db: SupabaseClient,
    market_id: str,
    resolved_at_str: str,
    window_hours: int = SNAPSHOT_WINDOW_H,
) -> dict | None:
    """Devuelve el snapshot de market_snapshots más cercano a resolved_at
    dentro de una ventana ±window_hours. None si no hay ninguno."""
    resolved_at = _parse_ts(resolved_at_str)
    if not resolved_at:
        return None

    window = timedelta(hours=window_hours)
    lo = (resolved_at - window).isoformat()
    hi = (resolved_at + window).isoformat()

    try:
        resp = (
            db.client.table("market_snapshots")
            .select("odds, timestamp")
            .eq("market_id", market_id)
            .gte("timestamp", lo)
            .lte("timestamp", hi)
            .execute()
        )
        rows = resp.data or []
    except Exception as e:
        logger.warning("  Snapshot query error for %s: %s", market_id[:16], e)
        return None

    if not rows:
        return None

    # Elegir el más cercano en el tiempo
    def _abs_diff(row: dict) -> float:
        ts = _parse_ts(row.get("timestamp"))
        if ts is None:
            return float("inf")
        return abs((ts - resolved_at).total_seconds())

    return min(rows, key=_abs_diff)


def _paginate_resolved_alerts(db: SupabaseClient, extra_filters: list) -> list[dict]:
    """Pagina alertas resueltas aplicando filtros adicionales."""
    rows: list[dict] = []
    offset = 0
    while True:
        q = (
            db.client.table("alerts")
            .select(
                "id, market_id, direction, actual_return, total_sold_pct, "
                "outcome, market_question, star_level, resolved_at, odds_at_alert"
            )
            .in_("outcome", ["correct", "incorrect"])
        )
        for f in extra_filters:
            q = f(q)
        batch = q.range(offset, offset + PAGE_SIZE - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


# ══════════════════════════════════════════════════════════
# PART 1 — realized_return
# ══════════════════════════════════════════════════════════

def diagnostico_realized_return(db: SupabaseClient) -> list[dict]:
    """Lee alertas resueltas con realized_return=NULL y calcula el valor propuesto."""
    logger.info("═" * 60)
    logger.info("PART 1: realized_return backfill")
    logger.info("═" * 60)

    alerts = _paginate_resolved_alerts(
        db,
        extra_filters=[
            lambda q: q.is_("realized_return", "null"),
            lambda q: q.not_.is_("actual_return", "null"),
        ],
    )
    logger.info("Alertas resueltas con realized_return=NULL: %d", len(alerts))

    if not alerts:
        return []

    results: list[dict] = []
    with_sell_events = 0

    for i, alert in enumerate(alerts):
        alert_id = alert["id"]
        actual_return = alert.get("actual_return") or 0.0
        total_sold_pct = alert.get("total_sold_pct") or 0.0

        try:
            sell_events = db.get_sell_events_for_alert(alert_id)
        except Exception as e:
            logger.warning("  No se pudo leer sell events para #%s: %s", alert_id, e)
            sell_events = []

        realized = _calc_realized_return(actual_return, total_sold_pct, sell_events)
        n_se = len(sell_events)
        if n_se > 0:
            with_sell_events += 1

        results.append({
            "id": alert_id,
            "market_question": (alert.get("market_question") or "?")[:50],
            "star_level": alert.get("star_level"),
            "outcome": alert.get("outcome"),
            "actual_return": actual_return,
            "total_sold_pct": total_sold_pct,
            "sell_events_count": n_se,
            "realized_return": realized,
        })

        if i < SAMPLE_SIZE:
            diff_label = f" (Δ={realized - actual_return:+.1f}%)" if n_se > 0 else " (sin sells, =actual)"
            logger.info(
                "  [%d/%d] #%s [%s★ %s] actual=%.1f%% → realized=%.2f%%%s",
                i + 1, min(SAMPLE_SIZE, len(alerts)),
                alert_id,
                alert.get("star_level", "?"),
                alert.get("outcome", "?"),
                actual_return,
                realized,
                diff_label,
            )

    logger.info("")
    logger.info("  Total a escribir   : %d", len(results))
    logger.info("  Sin sell events    : %d  (realized = actual_return)", len(results) - with_sell_events)
    logger.info("  Con sell events    : %d  (PnL real distinto al del mercado)", with_sell_events)
    logger.info("")

    return results


def correccion_realized_return(db: SupabaseClient, rows: list[dict]) -> int:
    """Escribe realized_return. El filtro IS NULL en el UPDATE garantiza
    que no se sobreescribe ningún valor existente."""
    logger.info(
        "[CORRECCIÓN] realized_return — %s — %d filas",
        "DRY-RUN" if DRY_RUN else "LIVE", len(rows),
    )
    modificadas = 0

    for row in rows:
        if DRY_RUN:
            if modificadas < SAMPLE_SIZE:
                logger.info(
                    "  DRY-RUN: id=%s → realized_return=%.2f (actual_return=%.2f)",
                    row["id"], row["realized_return"], row["actual_return"],
                )
            modificadas += 1
        else:
            try:
                db.client.table("alerts").update(
                    {"realized_return": row["realized_return"]}
                ).eq("id", row["id"]).is_("realized_return", "null").execute()
                modificadas += 1
            except Exception as e:
                logger.error("  ERROR id=%s: %s", row["id"], e)

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Filas que se modificarían: %d", len(rows))
    else:
        logger.info("[CORRECCIÓN] Completado. Filas modificadas: %d", modificadas)

    return modificadas


def verificacion_realized_return(db: SupabaseClient) -> None:
    remaining = _paginate_resolved_alerts(
        db,
        extra_filters=[
            lambda q: q.is_("realized_return", "null"),
            lambda q: q.not_.is_("actual_return", "null"),
        ],
    )
    if remaining:
        logger.warning(
            "[VERIFICACIÓN] realized_return: quedan %d alertas con NULL. "
            "Revisar errores arriba.",
            len(remaining),
        )
    else:
        logger.info("[VERIFICACIÓN] realized_return: OK — 0 alertas con NULL pendientes.")


# ══════════════════════════════════════════════════════════
# PART 2 — odds_at_resolution_raw
# ══════════════════════════════════════════════════════════

def diagnostico_odds_raw(db: SupabaseClient) -> list[dict]:
    """Lee alertas resueltas con odds_at_resolution_raw=NULL y
    busca el snapshot más cercano en market_snapshots (±1h)."""
    logger.info("═" * 60)
    logger.info("PART 2: odds_at_resolution_raw backfill")
    logger.info("═" * 60)

    alerts = _paginate_resolved_alerts(
        db,
        extra_filters=[
            lambda q: q.is_("odds_at_resolution_raw", "null"),
            lambda q: q.not_.is_("resolved_at", "null"),
        ],
    )
    logger.info("Alertas resueltas con odds_at_resolution_raw=NULL: %d", len(alerts))

    if not alerts:
        return []

    results: list[dict] = []
    found = 0
    not_found = 0

    for i, alert in enumerate(alerts):
        alert_id = alert["id"]
        market_id = alert.get("market_id", "")
        resolved_at_str = alert.get("resolved_at")

        snapshot = _get_snapshot_near_ts(db, market_id, resolved_at_str)

        if snapshot:
            raw_odds = snapshot.get("odds")
            found += 1
        else:
            raw_odds = None
            not_found += 1

        results.append({
            "id": alert_id,
            "market_question": (alert.get("market_question") or "?")[:50],
            "star_level": alert.get("star_level"),
            "outcome": alert.get("outcome"),
            "resolved_at": resolved_at_str,
            "odds_at_resolution_raw": raw_odds,
            "snapshot_ts": snapshot.get("timestamp") if snapshot else None,
        })

        if i < SAMPLE_SIZE:
            if raw_odds is not None:
                logger.info(
                    "  [%d/%d] #%s [%s★ %s] resolved_at=%s → odds_raw=%.3f (snapshot=%s)",
                    i + 1, min(SAMPLE_SIZE, len(alerts)),
                    alert_id,
                    alert.get("star_level", "?"),
                    alert.get("outcome", "?"),
                    (resolved_at_str or "?")[:19],
                    raw_odds,
                    (snapshot.get("timestamp") or "?")[:19] if snapshot else "—",
                )
            else:
                logger.info(
                    "  [%d/%d] #%s [%s★ %s] resolved_at=%s → sin snapshot (dejará NULL)",
                    i + 1, min(SAMPLE_SIZE, len(alerts)),
                    alert_id,
                    alert.get("star_level", "?"),
                    alert.get("outcome", "?"),
                    (resolved_at_str or "?")[:19],
                )

    # Only write rows where snapshot was found
    writable = [r for r in results if r["odds_at_resolution_raw"] is not None]

    logger.info("")
    logger.info("  Total candidatas   : %d", len(results))
    logger.info("  Con snapshot ±1h   : %d  (se escribirá odds_at_resolution_raw)", found)
    logger.info("  Sin snapshot ±1h   : %d  (quedarán NULL)", not_found)
    logger.info("")

    return writable


def correccion_odds_raw(db: SupabaseClient, rows: list[dict]) -> int:
    """Escribe odds_at_resolution_raw. El filtro IS NULL en el UPDATE
    garantiza que no se sobreescribe ningún valor existente."""
    logger.info(
        "[CORRECCIÓN] odds_at_resolution_raw — %s — %d filas",
        "DRY-RUN" if DRY_RUN else "LIVE", len(rows),
    )
    modificadas = 0

    for row in rows:
        if DRY_RUN:
            if modificadas < SAMPLE_SIZE:
                logger.info(
                    "  DRY-RUN: id=%s → odds_at_resolution_raw=%.4f",
                    row["id"], row["odds_at_resolution_raw"],
                )
            modificadas += 1
        else:
            try:
                db.client.table("alerts").update(
                    {"odds_at_resolution_raw": row["odds_at_resolution_raw"]}
                ).eq("id", row["id"]).is_("odds_at_resolution_raw", "null").execute()
                modificadas += 1
            except Exception as e:
                logger.error("  ERROR id=%s: %s", row["id"], e)

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Filas que se modificarían: %d", len(rows))
    else:
        logger.info("[CORRECCIÓN] Completado. Filas modificadas: %d", modificadas)

    return modificadas


def verificacion_odds_raw(db: SupabaseClient) -> None:
    # After correction, count remaining NULLs where resolved_at exists and
    # market_snapshots might have data (hard to verify fully post-hoc, so just count)
    remaining = _paginate_resolved_alerts(
        db,
        extra_filters=[
            lambda q: q.is_("odds_at_resolution_raw", "null"),
            lambda q: q.not_.is_("resolved_at", "null"),
        ],
    )
    logger.info(
        "[VERIFICACIÓN] odds_at_resolution_raw: %d alertas aún con NULL "
        "(esperado = alertas sin snapshot en market_snapshots ±1h).",
        len(remaining),
    )


# ══════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════

def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient()

    # ── Diagnóstico (siempre read-only) ─────────────────
    rows_rr = diagnostico_realized_return(db)
    rows_odds = diagnostico_odds_raw(db)

    # ── Resumen final ────────────────────────────────────
    logger.info("═" * 60)
    logger.info("RESUMEN DIAGNÓSTICO:")
    logger.info("  Part 1 (realized_return)       : %d alertas a actualizar", len(rows_rr))
    logger.info("  Part 2 (odds_at_resolution_raw): %d alertas a actualizar", len(rows_odds))
    logger.info("")
    logger.info("  NO backfillado — sell_timestamp      : NULL para registros < %s", "2026-03-03")
    logger.info("  NO backfillado — additional_buys_*   : 0/0.0 para alertas < %s (info perdida)", "2026-03-03")
    logger.info("═" * 60)

    if DRY_RUN:
        logger.info("")
        logger.info("DRY_RUN=True — no se escribió nada.")
        logger.info("Revisa el output. Si todo es correcto, cambia DRY_RUN=False y re-ejecuta.")
        return

    # ── Confirmación antes de escribir ──────────────────
    if not rows_rr and not rows_odds:
        logger.info("Nada que escribir.")
        return

    resp = input(
        f"\n¿Aplicar backfill sobre "
        f"{len(rows_rr)} (realized_return) + {len(rows_odds)} (odds_raw) "
        f"filas en PRODUCCIÓN? [s/N] "
    ).strip().lower()
    if resp != "s":
        logger.info("Operación cancelada.")
        sys.exit(0)

    # ── Correcciones ─────────────────────────────────────
    correccion_realized_return(db, rows_rr)
    correccion_odds_raw(db, rows_odds)

    # ── Verificación ─────────────────────────────────────
    verificacion_realized_return(db)
    verificacion_odds_raw(db)

    logger.info("")
    logger.info("Backfill completado.")
    logger.info(
        "Mueve este archivo a vacunas/aplicadas/ y registra los resultados en el header."
    )


if __name__ == "__main__":
    main()
