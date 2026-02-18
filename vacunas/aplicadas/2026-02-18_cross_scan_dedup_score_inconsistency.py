"""
============================================================
VACUNA: cross_scan_dedup_score_inconsistency
Fecha: 2026-02-18
Bug que corrige: El mecanismo de deduplicación cross-scan (CROSS_SCAN_DEDUP_HOURS)
  actualizaba el campo `score` de una alerta existente con el score del nuevo scan,
  pero no actualizaba `score_raw` ni `star_level`. Esto dejaba alertas con campos
  inconsistentes: score ≠ round(score_raw × multiplier) y star_level incorrecto.
  Se detectaron 10 alertas afectadas (|score - score_raw * multiplier| > 5).
Tablas afectadas: alerts
Filas estimadas a modificar: 10
Reversible: NO — recálculo in-place. Los valores originales se pierden.
Aplicada en producción: SI
Commit que introdujo el fix en código: (este commit)
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-18
  - Filas modificadas: (completar tras ejecución)
  - Observaciones: El script recalcula star_level a partir del score actual
    (campo `score`) usando los umbrales NEW_STAR_THRESHOLDS de config.py.
    score_raw se actualiza a score/multiplier redondeado, donde multiplier
    se toma del campo existente.
    Las 10 alertas tienen score correcto (fue actualizado por cross-scan dedup);
    sólo star_level y score_raw necesitan corrección.
"""

import logging
import math
import os
import sys

from src import config
from src.analysis.scoring import SCORE_CAP
from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DRY_RUN = False

PAGE_SIZE = 500

# Tolerancia: consideramos inconsistente si la diferencia supera este umbral
INCONSISTENCY_THRESHOLD = 5


def _score_to_stars(score: int) -> int:
    for threshold, stars in config.NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0


def _expected_score_raw(score: int, multiplier: float) -> int:
    """Recalcula score_raw esperado a partir de score final y multiplier."""
    if not multiplier or multiplier == 0:
        return score
    return round(score / multiplier)


def diagnostico(db: SupabaseClient) -> list[dict]:
    """Detecta alertas donde score ≠ round(score_raw × multiplier) por encima del umbral."""
    logger.info("[DIAGNÓSTICO] Buscando alertas con score inconsistente...")

    candidatas = []
    offset = 0

    while True:
        rows = (
            db.client.table("alerts")
            .select("id,score,score_raw,multiplier,star_level")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        if not rows:
            break

        for row in rows:
            score = row.get("score") or 0
            score_raw = row.get("score_raw") or 0
            multiplier = row.get("multiplier") or 1.0
            star_level = row.get("star_level")

            # Calcular el score_final esperado desde score_raw × multiplier
            expected_score = min(round(score_raw * multiplier), SCORE_CAP)
            delta = abs(score - expected_score)

            # También detectar star_level inconsistente con el score actual
            expected_stars = _score_to_stars(score)
            stars_wrong = star_level != expected_stars

            if delta > INCONSISTENCY_THRESHOLD or stars_wrong:
                row["_delta"] = delta
                row["_expected_score"] = expected_score
                row["_expected_stars"] = expected_stars
                candidatas.append(row)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info(
        "[DIAGNÓSTICO] Alertas con inconsistencia en scoring: %d", len(candidatas)
    )
    for row in candidatas:
        logger.info(
            "  id=%s score=%d score_raw=%d multiplier=%.2f star=%s "
            "| esperado score=%d stars=%d delta=%d",
            row["id"],
            row.get("score", 0),
            row.get("score_raw", 0),
            row.get("multiplier", 1.0),
            row.get("star_level"),
            row["_expected_score"],
            row["_expected_stars"],
            row["_delta"],
        )
    return candidatas


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """
    Para cada alerta inconsistente:
      - Recalcula score_raw = round(score / multiplier)  [score es el valor correcto]
      - Recalcula star_level usando NEW_STAR_THRESHOLDS sobre el score actual
      - Actualiza la DB
    El campo `score` NO se toca — fue correctamente actualizado por el cross-scan dedup.
    """
    logger.info(
        "[CORRECCIÓN] %s modo. %d alertas.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas)
    )
    modificadas = 0

    for fila in filas:
        score = fila.get("score") or 0
        multiplier = fila.get("multiplier") or 1.0
        new_score_raw = _expected_score_raw(score, multiplier)
        new_stars = _score_to_stars(score)

        if DRY_RUN:
            logger.info(
                "  DRY-RUN id=%s: score_raw %d→%d | star_level %s→%d",
                fila["id"],
                fila.get("score_raw", 0),
                new_score_raw,
                fila.get("star_level"),
                new_stars,
            )
        else:
            db.client.table("alerts").update({
                "score_raw": new_score_raw,
                "star_level": new_stars,
            }).eq("id", fila["id"]).execute()
            modificadas += 1

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se modificarían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas corregidas: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    logger.info("[VERIFICACIÓN] Comprobando que no quedan inconsistencias...")
    restantes = diagnostico(db)
    if restantes:
        logger.warning("Aún quedan %d alertas inconsistentes.", len(restantes))
    else:
        logger.info("OK — No quedan alertas con scoring inconsistente.")


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient()

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron alertas con scoring inconsistente. Nada que hacer.")
        return

    if not DRY_RUN:
        resp = input(
            f"\n¿Corregir {len(filas)} alertas inconsistentes en PRODUCCIÓN? [s/N] "
        ).strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, filas)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
