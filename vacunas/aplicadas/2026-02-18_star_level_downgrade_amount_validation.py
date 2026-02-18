"""
============================================================
VACUNA: star_level_downgrade_amount_validation
Fecha: 2026-02-18
Bug que corrige: Alertas creadas con el sistema antiguo de estrellas
  (STAR_THRESHOLDS basado solo en score) que no cumplen los requisitos
  de importe mínimo introducidos por STAR_VALIDATION:
    - 4★ requiere total_amount >= $5,000
    - 5★ requiere total_amount >= $10,000
  Las alertas afectadas obtuvieron su star_level cuando no existía este
  requisito y nunca fueron retroajustadas.
  Alertas corregidas:
    id=649  4★ → 3★  (amount=$4,182, score=163 cumple 3★)
    id=845  4★ → 3★  (amount=$4,426, score=158 cumple 3★)
    id=1066 4★ → 3★  (amount=$4,677, score=200 cumple 3★)
    id=1468 5★ → 4★  (amount=$6,893, score=248 cumple 4★ pero no $10k de 5★)
  id=578 excluido: amount=$4,999.99 — error de precisión flotante, no real.
Tablas afectadas: alerts
Filas estimadas a modificar: 4
Reversible: NO — recálculo in-place. Los valores originales se pierden.
Aplicada en producción: SI
Commit que introdujo el fix en código: N/A (bug de datos históricos)
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-18
  - Filas modificadas: 4
  - Observaciones: 649→3★, 845→3★, 1066→3★, 1468→4★. Verificación OK (0 errores).
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

# Correcciones a aplicar: {alert_id: (star_level_actual_esperado, nuevo_star_level)}
CORRECTIONS: dict[int, tuple[int, int]] = {
    649:  (4, 3),
    845:  (4, 3),
    1066: (4, 3),
    1468: (5, 4),
}


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Verifica que las alertas objetivo tienen el star_level incorrecto esperado.
    Detecta si alguna ya fue corregida (idempotencia).
    """
    logger.info("[DIAGNÓSTICO] Verificando alertas con star_level a corregir...")

    alert_ids = list(CORRECTIONS.keys())
    rows = (
        db.client.table("alerts")
        .select("id,star_level,score,total_amount")
        .in_("id", alert_ids)
        .execute()
        .data
    )

    afectadas = []
    for row in rows:
        aid = row["id"]
        star_actual = row.get("star_level")
        star_esperado_actual, star_nuevo = CORRECTIONS[aid]

        if star_actual == star_nuevo:
            logger.info(
                "  id=%d ya corregida (star_level=%d). Omitiendo.", aid, star_actual
            )
            continue

        if star_actual != star_esperado_actual:
            logger.warning(
                "  id=%d tiene star_level=%d pero se esperaba %d. Revisar manualmente.",
                aid, star_actual, star_esperado_actual,
            )
            continue

        logger.info(
            "  id=%d: star_level=%d | score=%d | total_amount=%.2f → corregir a %d★",
            aid,
            star_actual,
            row.get("score", 0),
            float(row.get("total_amount") or 0),
            star_nuevo,
        )
        row["_nuevo_star"] = star_nuevo
        afectadas.append(row)

    logger.info("[DIAGNÓSTICO] Alertas a corregir: %d de %d", len(afectadas), len(alert_ids))
    return afectadas


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """
    Baja el star_level de cada alerta al valor correcto según STAR_VALIDATION.
    El campo score NO se toca — el score es correcto; solo el star_level estaba inflado.
    """
    logger.info(
        "[CORRECCIÓN] %s modo. %d alertas.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas)
    )
    modificadas = 0

    for fila in filas:
        nuevo_star = fila["_nuevo_star"]
        if DRY_RUN:
            logger.info(
                "  DRY-RUN id=%d: star_level %d→%d",
                fila["id"], fila.get("star_level"), nuevo_star,
            )
        else:
            db.client.table("alerts").update(
                {"star_level": nuevo_star}
            ).eq("id", fila["id"]).execute()
            logger.info(
                "  ACTUALIZADO id=%d: star_level %d→%d",
                fila["id"], fila.get("star_level"), nuevo_star,
            )
            modificadas += 1

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se modificarían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas corregidas: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    """Confirma que las alertas tienen el star_level correcto tras la corrección."""
    logger.info("[VERIFICACIÓN] Comprobando star_levels corregidos...")

    alert_ids = list(CORRECTIONS.keys())
    rows = (
        db.client.table("alerts")
        .select("id,star_level,total_amount")
        .in_("id", alert_ids)
        .execute()
        .data
    )

    errores = 0
    for row in rows:
        aid = row["id"]
        _, star_esperado = CORRECTIONS[aid]
        star_actual = row.get("star_level")
        if star_actual != star_esperado:
            logger.warning(
                "  FALLO id=%d: star_level=%d, esperado=%d",
                aid, star_actual, star_esperado,
            )
            errores += 1
        else:
            logger.info("  OK id=%d: star_level=%d", aid, star_actual)

    if errores:
        logger.warning("[VERIFICACIÓN] %d alertas con star_level incorrecto.", errores)
    else:
        logger.info("[VERIFICACIÓN] OK — Todas las alertas corregidas correctamente.")


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient()

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron alertas a corregir. Nada que hacer.")
        return

    if not DRY_RUN:
        resp = input(
            f"\n¿Corregir star_level de {len(filas)} alertas en PRODUCCIÓN? [s/N] "
        ).strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, filas)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
