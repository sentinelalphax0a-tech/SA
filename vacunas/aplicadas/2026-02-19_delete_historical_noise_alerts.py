"""
============================================================
VACUNA: delete_historical_noise_alerts
Fecha: 2026-02-19
Bug que corrige: 4 alertas con daño histórico de persistencia que
  generan ruido en el dataset para ML. Sus filters_triggered están
  incompletos (score_raw >> computed) y no pueden ser reconstruidos.
  Eliminar es preferible a mantenerlas con labels potencialmente
  incorrectos.
  Alertas a eliminar:
    id=340  star=5  score=400  delta=14   computed>score_raw (filtros neg faltantes, score inflado)
    id=580  star=4  score=264  delta=216  MISSING_POSITIVE (261 vs 45 computed)
    id=631  star=2  score=280  delta=162  MISSING_POSITIVE (212 vs 50 computed)
    id=649  star=3  score=163  delta=56   MISSING_POSITIVE (141 vs 85 computed)
  Orden de borrado (cascada FK manual):
    1. whale_notifications  WHERE alert_id IN (...)
    2. alert_sell_events    WHERE alert_id IN (...)
    3. notification_log     WHERE alert_id IN (...)
    4. wallet_positions     WHERE alert_id IN (...)
    5. alert_tracking       WHERE alert_id IN (...)
    6. alerts               WHERE id IN (...)
Tablas afectadas: whale_notifications, alert_sell_events,
  notification_log, wallet_positions, alert_tracking, alerts
Filas estimadas a eliminar: 4 alertas + dependencias FK
Reversible: NO — eliminación permanente.
Aplicada en producción: SI
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-19
  - Filas eliminadas: 17 (4 alerts + 9 wallet_positions + 4 alert_tracking)
  - Observaciones: cascada FK completa. Verificación OK (0 restantes).
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

DRY_RUN = False

# Alertas a eliminar — daño histórico de persistencia de filters_triggered
TARGET_IDS: list[int] = [340, 580, 631, 649]

# Tablas con FK a alerts, en orden de borrado correcto
FK_TABLES: list[str] = [
    "whale_notifications",
    "alert_sell_events",
    "notification_log",
    "wallet_positions",
    "alert_tracking",
]


def diagnostico(db: SupabaseClient) -> dict:
    """
    Verifica que las alertas objetivo existen con los campos esperados
    y cuenta cuántos registros FK se eliminarían en cada tabla.
    """
    logger.info("[DIAGNÓSTICO] Verificando alertas objetivo y dependencias FK...")

    # Verificar alertas objetivo
    rows = (
        db.client.table("alerts")
        .select("id,star_level,score,score_raw,created_at,market_question")
        .in_("id", TARGET_IDS)
        .execute()
        .data
    ) or []

    found_ids = [r["id"] for r in rows]
    missing = [i for i in TARGET_IDS if i not in found_ids]
    if missing:
        logger.warning("  Alertas no encontradas en DB: %s", missing)

    logger.info("[DIAGNÓSTICO] Alertas objetivo encontradas: %d/%d", len(rows), len(TARGET_IDS))
    for row in rows:
        logger.info(
            "  id=%d  star=%d  score=%d  score_raw=%d  created=%s  q=%.50s",
            row["id"],
            row.get("star_level") or 0,
            row.get("score") or 0,
            row.get("score_raw") or 0,
            (row.get("created_at") or "")[:10],
            row.get("market_question") or "",
        )

    # Contar dependencias FK
    fk_counts: dict[str, int] = {}
    for table in FK_TABLES:
        try:
            result = (
                db.client.table(table)
                .select("alert_id", count="exact")
                .in_("alert_id", TARGET_IDS)
                .execute()
            )
            n = getattr(result, "count", None)
            if n is None:
                n = len(result.data or [])
            fk_counts[table] = n
            if n > 0:
                logger.info("  %s: %d registros a eliminar", table, n)
            else:
                logger.info("  %s: 0 registros (sin dependencias)", table)
        except Exception as e:
            logger.warning("  %s: error al consultar — %s", table, e)
            fk_counts[table] = -1

    return {"alerts": rows, "fk_counts": fk_counts}


def correccion(db: SupabaseClient, diagnostico_result: dict) -> dict:
    """
    Elimina en cascada: primero FKs, luego alertas.
    """
    alerts = diagnostico_result["alerts"]
    if not alerts:
        logger.info("[CORRECCIÓN] Sin alertas a eliminar.")
        return {}

    ids_to_delete = [r["id"] for r in alerts]
    logger.info(
        "[CORRECCIÓN] %s modo. Eliminando ids=%s",
        "DRY-RUN" if DRY_RUN else "LIVE", ids_to_delete,
    )

    deleted: dict[str, int] = {}

    if DRY_RUN:
        for table in FK_TABLES:
            n = diagnostico_result["fk_counts"].get(table, 0)
            logger.info("  DRY-RUN: %s → %d registros que se eliminarían", table, n)
            deleted[table] = n
        logger.info("  DRY-RUN: alerts → %d registros que se eliminarían", len(ids_to_delete))
        deleted["alerts"] = len(ids_to_delete)
        logger.info(
            "[CORRECCIÓN] DRY-RUN. Total filas que se eliminarían: %d",
            sum(deleted.values()),
        )
        return deleted

    # LIVE: borrar en orden correcto
    for table in FK_TABLES:
        try:
            result = (
                db.client.table(table)
                .delete()
                .in_("alert_id", ids_to_delete)
                .execute()
            )
            n = len(result.data or [])
            deleted[table] = n
            logger.info("  ELIMINADO %s: %d registros", table, n)
        except Exception as e:
            logger.error("  ERROR eliminando %s: %s", table, e)
            deleted[table] = -1

    # Por último, eliminar las alertas
    try:
        result = (
            db.client.table("alerts")
            .delete()
            .in_("id", ids_to_delete)
            .execute()
        )
        n = len(result.data or [])
        deleted["alerts"] = n
        logger.info("  ELIMINADO alerts: %d registros", n)
    except Exception as e:
        logger.error("  ERROR eliminando alerts: %s", e)
        deleted["alerts"] = -1

    total = sum(v for v in deleted.values() if v >= 0)
    logger.info("[CORRECCIÓN] Completado. Total filas eliminadas: %d", total)
    return deleted


def verificacion(db: SupabaseClient) -> None:
    logger.info("[VERIFICACIÓN] Comprobando que las alertas fueron eliminadas...")
    result = (
        db.client.table("alerts")
        .select("id")
        .in_("id", TARGET_IDS)
        .execute()
        .data
    ) or []
    if result:
        remaining = [r["id"] for r in result]
        logger.warning("  Aún existen alertas: %s", remaining)
    else:
        logger.info("  OK — Todas las alertas objetivo eliminadas.")


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient()

    result = diagnostico(db)
    if not result["alerts"]:
        logger.info("No se encontraron alertas objetivo. Nada que hacer.")
        return

    if not DRY_RUN:
        ids = [r["id"] for r in result["alerts"]]
        resp = input(
            f"\n¿Eliminar PERMANENTEMENTE las alertas {ids} y sus FK en PRODUCCIÓN? [s/N] "
        ).strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, result)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
