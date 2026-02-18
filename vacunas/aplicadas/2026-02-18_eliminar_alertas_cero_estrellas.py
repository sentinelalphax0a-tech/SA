"""
============================================================
VACUNA: eliminar_alertas_cero_estrellas
Fecha: 2026-02-18
Bug que corrige: Antes del commit de hoy, el sistema guardaba todas las alertas
  en DB independientemente de su star_level, incluyendo alertas con 0★ que nunca
  se publicarían ni aportarían valor. Esto inflaba la tabla alerts con ruido.
  El código ha sido corregido (gate 0★ en main.py) para no guardar nuevas
  alertas 0★. Esta vacuna limpia las 375 alertas históricas 0★ ya existentes.
Tablas afectadas: alert_tracking, wallet_positions, alerts
Filas estimadas a modificar: ~375 alertas (+ dependencias en FK tables)
Reversible: NO — eliminación permanente. Sin backup.
Aplicada en producción: SI
Commit que introdujo el fix en código: (este commit)
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-18
  - Filas eliminadas alerts: 375
  - Filas eliminadas alert_tracking: 46
  - Filas eliminadas wallet_positions: 43
  - Observaciones: Script ejecutado con DRY_RUN=True primero para confirmar
    el recuento. Luego ejecutado con DRY_RUN=False con confirmación manual.
    Verificación post-ejecución: 0 alertas 0★ restantes.
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

PAGE_SIZE = 500


def _get_zero_star_ids(db: SupabaseClient) -> list[int]:
    """Devuelve los IDs de todas las alertas con star_level = 0 o NULL."""
    ids: list[int] = []
    offset = 0

    while True:
        rows = (
            db.client.table("alerts")
            .select("id,star_level,score")
            .or_("star_level.eq.0,star_level.is.null")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        if not rows:
            break
        ids.extend(r["id"] for r in rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return ids


def diagnostico(db: SupabaseClient) -> list[int]:
    logger.info("[DIAGNÓSTICO] Buscando alertas con star_level = 0 o NULL...")
    ids = _get_zero_star_ids(db)
    logger.info("[DIAGNÓSTICO] Alertas 0★ encontradas: %d", len(ids))
    if ids:
        logger.info("  Primeros 10 IDs: %s", ids[:10])
    return ids


def correccion(db: SupabaseClient, alert_ids: list[int]) -> None:
    """Elimina las alertas 0★ y sus dependencias en FK tables."""
    if not alert_ids:
        logger.info("No hay alertas 0★ que eliminar.")
        return

    logger.info(
        "[CORRECCIÓN] %s modo. %d alertas a eliminar.",
        "DRY-RUN" if DRY_RUN else "LIVE",
        len(alert_ids),
    )

    # Procesar en batches para no saturar la API
    BATCH = 100
    total_tracking = 0
    total_positions = 0
    total_alerts = 0

    for i in range(0, len(alert_ids), BATCH):
        batch = alert_ids[i : i + BATCH]

        if DRY_RUN:
            # Contar dependencias sin borrar
            tracking = (
                db.client.table("alert_tracking")
                .select("id", count="exact")
                .in_("alert_id", batch)
                .execute()
            )
            positions = (
                db.client.table("wallet_positions")
                .select("id", count="exact")
                .in_("alert_id", batch)
                .execute()
            )
            total_tracking += tracking.count or 0
            total_positions += positions.count or 0
            total_alerts += len(batch)
        else:
            # 1. Borrar dependencias primero (FK)
            res_t = (
                db.client.table("alert_tracking")
                .delete()
                .in_("alert_id", batch)
                .execute()
            )
            res_p = (
                db.client.table("wallet_positions")
                .delete()
                .in_("alert_id", batch)
                .execute()
            )
            # 2. Borrar las alertas
            res_a = (
                db.client.table("alerts")
                .delete()
                .in_("id", batch)
                .execute()
            )
            total_tracking += len(res_t.data or [])
            total_positions += len(res_p.data or [])
            total_alerts += len(res_a.data or [])
            logger.info(
                "  Batch %d-%d: eliminadas %d alertas, %d tracking, %d positions",
                i, i + len(batch) - 1,
                len(res_a.data or []),
                len(res_t.data or []),
                len(res_p.data or []),
            )

    if DRY_RUN:
        logger.info(
            "[CORRECCIÓN] DRY-RUN. Se eliminarían: %d alertas, "
            "%d filas en alert_tracking, %d filas en wallet_positions",
            total_alerts, total_tracking, total_positions,
        )
    else:
        logger.info(
            "[CORRECCIÓN] Completado. Eliminadas: %d alertas, "
            "%d filas en alert_tracking, %d filas en wallet_positions",
            total_alerts, total_tracking, total_positions,
        )


def verificacion(db: SupabaseClient) -> None:
    logger.info("[VERIFICACIÓN] Comprobando que no quedan alertas 0★...")
    restantes = _get_zero_star_ids(db)
    if restantes:
        logger.warning("Aún quedan %d alertas 0★. IDs: %s", len(restantes), restantes[:20])
    else:
        logger.info("OK — No quedan alertas con star_level = 0 o NULL.")


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient()

    alert_ids = diagnostico(db)
    if not alert_ids:
        logger.info("No se encontraron alertas 0★. Nada que hacer.")
        return

    if not DRY_RUN:
        resp = input(
            f"\n¿Eliminar {len(alert_ids)} alertas 0★ y sus dependencias en PRODUCCIÓN? [s/N] "
        ).strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, alert_ids)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
