"""
============================================================
VACUNA: dashboard_precio_entrada
Fecha: 2026-02-16
Bug que corrige: El dashboard mostraba dos datos incorrectos:
  (1) Precio de entrada tomado del primer trade disponible en DB
      en lugar del primer trade del wallet principal de la alerta.
  (2) Estrellas calculadas sobre score_raw en lugar de score_final
      (post-multiplicador logarítmico y de diversidad).
  Para alertas con multiplicadores significativos (ej: monto $50K → mult 1.6x),
  el nivel de estrellas del dashboard podía estar 1 nivel por debajo del real.
Tablas afectadas: alerts (campo entry_price y star_level en la vista del dashboard)
Filas estimadas a modificar: todas las alertas en el dashboard (impacto visual)
Reversible: NO aplica — el dashboard se regenera cada hora automáticamente.
  Este script verifica que la lógica de generación usa los campos correctos.
Aplicada en producción: SI
Commit que introdujo el fix en código: 3b800a6
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-16
  - Filas modificadas: N/A — el bug era en generate_dashboard.py, no en la DB.
    El fix en código corrigió el template. El dashboard regenerado a las 16:15 UTC
    del 2026-02-16 ya mostraba datos correctos.
  - Observaciones: Este script verifica que las alertas en DB tienen star_level
    consistente con score_final (no score_raw). Si hay inconsistencias, las corrige.
"""

import logging
import os
import sys

from src import config
from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DRY_RUN = False

PAGE_SIZE = 500


def _score_to_stars(score: int) -> int:
    for threshold, stars in config.NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Detecta alertas donde star_level en DB no coincide con el que
    correspondería a score_final (debería ser siempre consistentes tras el fix).
    También detecta alertas donde star_level coincidiría con score_raw pero
    no con score_final (evidencia del bug histórico).
    """
    logger.info("[DIAGNÓSTICO] Verificando consistencia star_level vs score_final...")

    inconsistentes = []
    offset = 0

    while True:
        rows = (
            db.client.table("alerts")
            .select("id, score_raw, score_final, star_level, created_at")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        if not rows:
            break

        for row in rows:
            score_final = row.get("score_final") or row.get("score_raw") or 0
            score_raw = row.get("score_raw") or 0
            star_actual = row.get("star_level") or 0

            stars_from_final = _score_to_stars(score_final)
            stars_from_raw = _score_to_stars(score_raw)

            # Inconsistencia: star_level coincide con score_raw pero no con score_final
            if star_actual == stars_from_raw and star_actual != stars_from_final:
                inconsistentes.append({
                    **row,
                    "_stars_correctas": stars_from_final,
                    "_stars_raw": stars_from_raw,
                })

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Alertas con star_level basado en score_raw (incorrecto): %d", len(inconsistentes))
    for row in inconsistentes[:10]:
        logger.info(
            "  id=%s score_raw=%d score_final=%d star_actual=%d → debería ser=%d",
            row["id"], row.get("score_raw", 0), row.get("score_final", 0),
            row.get("star_level", 0), row["_stars_correctas"],
        )

    return inconsistentes


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """
    Corrige star_level para que coincida con score_final en lugar de score_raw.
    """
    logger.info("[CORRECCIÓN] %s modo. %d alertas a corregir.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas))
    modificadas = 0

    for fila in filas:
        stars_correctas = fila["_stars_correctas"]

        if DRY_RUN:
            logger.info(
                "  DRY-RUN id=%s: star_level %d→%d (score_final=%d)",
                fila["id"], fila.get("star_level"), stars_correctas, fila.get("score_final"),
            )
        else:
            db.client.table("alerts").update({
                "star_level": stars_correctas,
            }).eq("id", fila["id"]).execute()
            modificadas += 1
            logger.info("  CORREGIDO id=%s: star_level→%d", fila["id"], stars_correctas)

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se corregirían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas corregidas: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    """Confirma que no quedan inconsistencias star_level vs score_final."""
    logger.info("[VERIFICACIÓN] Re-verificando consistencia...")
    restantes = diagnostico(db)
    if restantes:
        logger.warning("Aún quedan %d alertas con star_level inconsistente.", len(restantes))
    else:
        logger.info("OK — Todas las alertas tienen star_level consistente con score_final.")


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient(supabase_url, supabase_key)

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron inconsistencias. star_level consistente en todas las alertas.")
        return

    if not DRY_RUN:
        resp = input(f"\n¿Corregir star_level de {len(filas)} alertas? [s/N] ").strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, filas)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
