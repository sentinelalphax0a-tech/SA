"""
============================================================
VACUNA: precios_direccion_no
Fecha: 2026-02-15
Bug que corrige: Se implementó inversión de precio (1 - price) para trades en
  dirección NO asumiendo que el CLOB retornaba el precio del token YES. La asunción
  era incorrecta: el CLOB retorna el precio del token comprado (NO token para
  compras NO). La "corrección" invertía precios ya correctos. Filtros B07 y B25
  evaluaban el complemento del precio real para trades NO.
Tablas afectadas: alerts (filters_triggered con B07/B25 disparados sobre trades NO)
Filas estimadas a modificar: alertas con direction=NO y B07 o B25 activos
Reversible: NO — el recálculo es la versión correcta
Aplicada en producción: SI
Commits: d14876d (fix inicial, incorrecto) → 76341d7 (revert correcto)
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-15
  - Filas modificadas: ver log
  - Observaciones: El segundo commit (76341d7) revierte la inversión. Este script
    identifica alertas generadas en la ventana entre ambos commits donde B07/B25
    pudieron haber evaluado el precio invertido. El recálculo no es posible sin
    los precios originales de los trades; el script solo marca las alertas como
    "requiere revisión".
"""

import logging
import os
import sys
from datetime import datetime, timezone

from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DRY_RUN = True

# Ventana donde el bug de inversión estaba activo
BUG_START = datetime(2026, 2, 15, 0, 0, 0, tzinfo=timezone.utc)   # commit d14876d
BUG_END   = datetime(2026, 2, 15, 23, 59, 59, tzinfo=timezone.utc) # commit 76341d7

# Filtros potencialmente afectados por precio incorrecto en dirección NO
AFFECTED_FILTERS = {"B07", "B25"}

PAGE_SIZE = 500


def _parse_filters(filters_triggered) -> list[dict]:
    import json
    if not filters_triggered:
        return []
    if isinstance(filters_triggered, str):
        try:
            return json.loads(filters_triggered)
        except Exception:
            return []
    return filters_triggered


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Detecta alertas con direction=NO y B07 o B25 disparados,
    generadas durante la ventana del bug (entre los dos commits del 2026-02-15).
    """
    logger.info("[DIAGNÓSTICO] Buscando alertas NO-direction con B07/B25 en ventana del bug...")

    candidatas = []
    offset = 0

    while True:
        rows = (
            db.client.table("alerts")
            .select("id, direction, score_raw, star_level, filters_triggered, created_at")
            .eq("direction", "NO")
            .gte("created_at", BUG_START.isoformat())
            .lte("created_at", BUG_END.isoformat())
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        if not rows:
            break

        for row in rows:
            filters = _parse_filters(row.get("filters_triggered"))
            ids = {f.get("filter_id", "") for f in filters}
            if ids & AFFECTED_FILTERS:
                candidatas.append(row)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Alertas NO-direction con B07/B25 en ventana bug: %d", len(candidatas))
    for row in candidatas[:10]:
        filters = _parse_filters(row.get("filters_triggered"))
        ids = {f.get("filter_id", "") for f in filters}
        activos = ids & AFFECTED_FILTERS
        logger.info("  id=%s created=%s filtros_afectados=%s score_raw=%s",
                    row["id"], row.get("created_at", "?")[:19],
                    activos, row.get("score_raw"))

    return candidatas


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """
    Marca las alertas afectadas con una nota de auditoría.
    El recálculo exacto no es posible sin los precios originales del CLOB
    (no almacenados en DB). Las alertas se marcan para revisión manual.
    """
    logger.info("[CORRECCIÓN] %s modo. %d alertas a marcar.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas))
    modificadas = 0

    for fila in filas:
        nota = "[vacuna 2026-02-15] B07/B25 posiblemente evaluados con precio invertido (bug dirección NO ventana d14876d→76341d7). Score puede estar +/-15 pts off."

        if DRY_RUN:
            logger.info("  DRY-RUN id=%s: añadiría nota de auditoría", fila["id"])
        else:
            db.client.table("alerts").update({
                "notes": nota,
            }).eq("id", fila["id"]).execute()
            modificadas += 1
            logger.info("  MARCADO id=%s", fila["id"])

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se marcarían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas marcadas para revisión: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    """
    Confirma que todas las alertas detectadas tienen la nota de auditoría.
    """
    logger.info("[VERIFICACIÓN] Comprobando alertas marcadas...")
    filas_post = diagnostico(db)

    sin_nota = [f for f in filas_post if not (f.get("notes") or "").startswith("[vacuna 2026-02-15]")]
    if sin_nota:
        logger.warning("%d alertas aún sin nota de auditoría.", len(sin_nota))
    else:
        logger.info("OK — Todas las alertas afectadas tienen nota de auditoría.")


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient(supabase_url, supabase_key)

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron alertas afectadas en la ventana del bug.")
        return

    if not DRY_RUN:
        resp = input(f"\n¿Marcar {len(filas)} alertas con nota de auditoría? [s/N] ").strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, filas)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
