"""
============================================================
MIGRATION: reconcile_sell_fields
Fecha: 2026-02-23
Descripción:
  One-shot reconciliación de los dos campos de venta en alerts:
    - total_sold_pct  (escrito por whale_monitor via alert_sell_events)
    - close_reason    (escrito por check_net_positions)

  Las dos rutas son independientes y no se coordinaban, creando dos
  grupos de alertas anómalas:

  GRUPO A (18 alertas): total_sold_pct > 0 pero close_reason = NULL
    → Origen garantizado: whale_monitor detecta solo CLOB trades.
    → Fix: SET close_reason = 'sell_clob'

  GRUPO B (variable): close_reason set pero total_sold_pct = 0
    → check_net_positions() detectó la salida vía net shares pero
      no registró el importe en dollars.
    → Fix: derivar total_sold_pct desde alert_sell_events (si existen)
      o desde wallet_positions.sell_amount/total_amount.
    → Si no hay datos: dejar total_sold_pct = 0 (conocemos el tipo de
      cierre pero no el importe).

Idempotente: sí — comprueba estado actual antes de escribir.
Ejecutar: python -m migrations.reconcile_sell_fields
============================================================
"""

import logging
import os
import sys

from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _fetch_all_alerts(db: SupabaseClient) -> list[dict]:
    rows = []
    PAGE = 1000
    offset = 0
    while True:
        batch = (
            db.client.table("alerts")
            .select("id,total_sold_pct,close_reason,outcome")
            .range(offset, offset + PAGE - 1)
            .execute()
        ).data or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def reconcile(db: SupabaseClient) -> dict:
    alerts = _fetch_all_alerts(db)
    logger.info("Fetched %d alerts", len(alerts))

    # ── GRUPO A: sold_pct > 0 but close_reason = NULL ─────────────────────
    group_a = [
        a for a in alerts
        if (a.get("total_sold_pct") or 0) > 0 and not a.get("close_reason")
    ]
    logger.info("Grupo A (sold_pct>0, close_reason=NULL): %d alerts", len(group_a))

    a_fixed = 0
    for alert in group_a:
        try:
            db.client.table("alerts").update(
                {"close_reason": "sell_clob"}
            ).eq("id", alert["id"]).execute()
            logger.info("  Grupo A: alert #%d → close_reason='sell_clob'", alert["id"])
            a_fixed += 1
        except Exception as e:
            logger.error("  Grupo A: failed for alert #%d: %s", alert["id"], e)

    # ── GRUPO B: close_reason set but total_sold_pct = 0 ──────────────────
    group_b = [
        a for a in alerts
        if a.get("close_reason") and not (a.get("total_sold_pct") or 0)
    ]
    logger.info("Grupo B (close_reason set, sold_pct=0): %d alerts", len(group_b))

    # Load sell events (for derivation from alert_sell_events.sum(sell_pct))
    se_resp = (
        db.client.table("alert_sell_events")
        .select("alert_id,sell_pct")
        .execute()
    )
    se_by_alert: dict[int, float] = {}
    for ev in (se_resp.data or []):
        aid = ev.get("alert_id")
        if aid is not None:
            se_by_alert[aid] = se_by_alert.get(aid, 0.0) + (ev.get("sell_pct") or 0.0)

    b_fixed = 0
    b_skipped = 0
    for alert in group_b:
        aid = alert["id"]

        # Try 1: derive from alert_sell_events (most accurate)
        if aid in se_by_alert:
            derived = round(min(1.0, se_by_alert[aid]), 6)
            try:
                db.client.table("alerts").update(
                    {"total_sold_pct": derived}
                ).eq("id", aid).execute()
                logger.info(
                    "  Grupo B #%d: total_sold_pct=%.4f (from alert_sell_events)", aid, derived
                )
                b_fixed += 1
            except Exception as e:
                logger.error("  Grupo B #%d: failed to update from sell_events: %s", aid, e)
            continue

        # Try 2: derive from wallet_positions.sell_amount / total_amount
        wp_resp = (
            db.client.table("wallet_positions")
            .select("sell_amount,total_amount,current_status")
            .eq("alert_id", aid)
            .execute()
        )
        wp_rows = wp_resp.data or []
        total_pos = sum(p.get("total_amount") or 0 for p in wp_rows)
        total_sold = sum(p.get("sell_amount") or 0 for p in wp_rows if (p.get("sell_amount") or 0) > 0)

        if total_pos > 0 and total_sold > 0:
            derived = round(min(1.0, total_sold / total_pos), 6)
            try:
                db.client.table("alerts").update(
                    {"total_sold_pct": derived}
                ).eq("id", aid).execute()
                logger.info(
                    "  Grupo B #%d: total_sold_pct=%.4f (from wallet_positions)", aid, derived
                )
                b_fixed += 1
            except Exception as e:
                logger.error("  Grupo B #%d: failed to update from wallet_positions: %s", aid, e)
            continue

        # No data available: log and skip
        logger.info(
            "  Grupo B #%d: no dollar amount recoverable — leaving total_sold_pct=0 "
            "(close_reason=%s already set)", aid, alert.get("close_reason")
        )
        b_skipped += 1

    return {
        "grupo_a_fixed": a_fixed,
        "grupo_b_fixed": b_fixed,
        "grupo_b_skipped": b_skipped,
    }


def verify(db: SupabaseClient) -> None:
    alerts = _fetch_all_alerts(db)

    remaining_a = [
        a for a in alerts
        if (a.get("total_sold_pct") or 0) > 0 and not a.get("close_reason")
    ]
    remaining_b_fixable = []
    for a in alerts:
        if a.get("close_reason") and not (a.get("total_sold_pct") or 0):
            remaining_b_fixable.append(a["id"])

    if remaining_a:
        logger.warning(
            "VERIFY: %d Grupo A alerts still missing close_reason: %s",
            len(remaining_a), [a["id"] for a in remaining_a]
        )
    else:
        logger.info("VERIFY: Grupo A — OK (0 alerts with sold_pct>0 and no close_reason)")

    logger.info(
        "VERIFY: Grupo B — %d alerts have close_reason but sold_pct=0 "
        "(no dollar data recoverable — expected)", len(remaining_b_fixable)
    )


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)
    db = SupabaseClient()
    result = reconcile(db)
    logger.info(
        "Reconciliation complete: Grupo A fixed=%d | Grupo B fixed=%d skipped=%d",
        result["grupo_a_fixed"], result["grupo_b_fixed"], result["grupo_b_skipped"],
    )
    verify(db)


if __name__ == "__main__":
    main()
