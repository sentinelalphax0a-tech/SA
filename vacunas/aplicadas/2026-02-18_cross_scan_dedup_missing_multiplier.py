"""
============================================================
VACUNA: cross_scan_dedup_missing_multiplier
Fecha: 2026-02-18
Bug que corrige: El bloque de upgrade del cross-scan dedup actualizaba
  score, score_raw y star_level, pero NO actualizaba el campo multiplier.
  Si entre dos scans el total_amount de la wallet cambió, el nuevo scan
  produce un multiplier distinto. El score se actualiza al nuevo valor
  correcto (score_raw × nuevo_multiplier), pero el campo multiplier queda
  congelado en el valor original, rompiendo la invariante:
    score == round(score_raw × multiplier)
  Fix en código: añadir "multiplier": alert.multiplier al bloque de upgrade
  en src/main.py (commit de este mismo día).
  Alerta afectada conocida: id=689
    score=221, score_raw=115, multiplier=1.81 (debería ser ≈1.92)
    expected_score = round(115 × 1.81) = 208 ≠ 221  delta=13
Tablas afectadas: alerts
Filas estimadas a modificar: 1 confirmada + potencialmente más (scan completo)
Reversible: NO — recálculo in-place. Los valores originales se pierden.
Aplicada en producción: SI
Commit que introdujo el fix en código: (este commit)
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-18
  - Filas modificadas: 1
  - Observaciones: id=689 multiplier 1.8100→1.9217. Verificación OK (0 restantes).
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

# Tolerancia: mismo umbral que vacuna anterior de scoring
INCONSISTENCY_THRESHOLD = 5


def _score_to_stars(score: int) -> int:
    for threshold, stars in config.NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Detecta alertas donde score ≠ round(score_raw × multiplier) por encima
    del umbral — mismo criterio que la vacuna anterior, para capturar cualquier
    alerta afectada por este bug (no solo id=689).
    """
    logger.info("[DIAGNÓSTICO] Buscando alertas con score ≠ round(score_raw × multiplier)...")

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

            expected_score = min(round(score_raw * multiplier), SCORE_CAP)
            delta = abs(score - expected_score)

            if delta > INCONSISTENCY_THRESHOLD:
                row["_delta"] = delta
                row["_expected_score"] = expected_score
                candidatas.append(row)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Alertas con inconsistencia score/multiplier: %d", len(candidatas))
    for row in candidatas:
        logger.info(
            "  id=%s score=%d score_raw=%d multiplier=%.4f | esperado=%d delta=%d",
            row["id"],
            row.get("score", 0),
            row.get("score_raw", 0),
            row.get("multiplier", 1.0),
            row["_expected_score"],
            row["_delta"],
        )
    return candidatas


def _recalc_multiplier(score: int, score_raw: int) -> float:
    """
    Recalcula el multiplier correcto a partir del score final y score_raw.
    Invierte la fórmula: multiplier = score / score_raw.
    Retorna 1.0 si score_raw es 0 para evitar división por cero.
    """
    if not score_raw:
        return 1.0
    return round(score / score_raw, 4)


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """
    Para cada alerta inconsistente recalcula multiplier = score / score_raw.
    El campo score es el valor correcto (fue actualizado por cross-scan dedup).
    El campo score_raw también es correcto (fue actualizado junto con score).
    Solo el multiplier quedó desactualizado — eso es lo que se corrige aquí.
    """
    logger.info(
        "[CORRECCIÓN] %s modo. %d alertas.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas)
    )
    modificadas = 0

    for fila in filas:
        score = fila.get("score") or 0
        score_raw = fila.get("score_raw") or 0
        old_multiplier = fila.get("multiplier") or 1.0
        new_multiplier = _recalc_multiplier(score, score_raw)

        if DRY_RUN:
            logger.info(
                "  DRY-RUN id=%s: multiplier %.4f→%.4f (score=%d score_raw=%d)",
                fila["id"], old_multiplier, new_multiplier, score, score_raw,
            )
        else:
            db.client.table("alerts").update(
                {"multiplier": new_multiplier}
            ).eq("id", fila["id"]).execute()
            logger.info(
                "  ACTUALIZADO id=%s: multiplier %.4f→%.4f",
                fila["id"], old_multiplier, new_multiplier,
            )
            modificadas += 1

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se modificarían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas corregidas: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    logger.info("[VERIFICACIÓN] Comprobando que no quedan inconsistencias score/multiplier...")
    restantes = diagnostico(db)
    if restantes:
        logger.warning("Aún quedan %d alertas inconsistentes.", len(restantes))
    else:
        logger.info("OK — No quedan alertas con score ≠ round(score_raw × multiplier).")


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
            f"\n¿Corregir multiplier de {len(filas)} alertas en PRODUCCIÓN? [s/N] "
        ).strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, filas)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
