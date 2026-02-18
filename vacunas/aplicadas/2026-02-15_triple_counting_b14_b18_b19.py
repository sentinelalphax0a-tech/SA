"""
============================================================
VACUNA: triple_counting_b14_b18_b19
Fecha: 2026-02-15
Bug que corrige: B14 (Primera compra grande, +15), B18d (Acumulación muy fuerte, +50)
  y B19b (Entrada muy grande, +30) podían dispararse simultáneamente ante una única
  compra >$10K, contando el mismo evento desde tres ángulos distintos.
  Score inflado hasta +95 pts. Alertas de 5★ donde debería haber 3-4★.
Tablas afectadas: alerts
Filas estimadas a modificar: desconocido (todas las alertas con B14+B18d+B19b simultáneos)
Reversible: NO — recálculo in-place. Los valores originales no se guardan.
Aplicada en producción: SI
Commit que introdujo el fix en código: 34954bd
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-15
  - Filas modificadas: ver log de ejecución
  - Observaciones: El fix en código ya previene recurrencia. Este script corrigió
    alertas históricas que ya existían en DB con el triple-counting.
    B14 suprimido si cualquier B19 disparó. B18 requiere trade_count>=2.
"""

import json
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

DRY_RUN = True

# Los tres filtros que formaban el triple-counting
TRIPLE_FILTERS = {"B14", "B18d", "B19b"}

# Grupos mutuamente excluyentes relevantes para recálculo
# (simplificado — el motor de scoring aplica la lista completa de config.py)
ME_GROUPS = config.MUTUALLY_EXCLUSIVE_GROUPS

PAGE_SIZE = 500


def _parse_filters(filters_triggered: list | str | None) -> list[dict]:
    if not filters_triggered:
        return []
    if isinstance(filters_triggered, str):
        try:
            return json.loads(filters_triggered)
        except Exception:
            return []
    return filters_triggered


def _filter_ids(filters: list[dict]) -> set[str]:
    return {f.get("filter_id", "") for f in filters}


def _has_triple_counting(filters: list[dict]) -> bool:
    ids = _filter_ids(filters)
    return TRIPLE_FILTERS.issubset(ids)


def diagnostico(db: SupabaseClient) -> list[dict]:
    """Detecta alertas con B14, B18d y B19b disparados simultáneamente."""
    logger.info("[DIAGNÓSTICO] Buscando alertas con triple-counting B14+B18d+B19b...")

    afectadas = []
    offset = 0

    while True:
        rows = (
            db.client.table("alerts")
            .select("id, score_raw, score_final, star_level, filters_triggered")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        if not rows:
            break

        for row in rows:
            filters = _parse_filters(row.get("filters_triggered"))
            if _has_triple_counting(filters):
                afectadas.append(row)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Alertas con triple-counting: %d", len(afectadas))
    return afectadas


def _recalcular_score(filters: list[dict]) -> tuple[int, int]:
    """
    Recalcula score_raw y score_final aplicando las reglas post-fix:
      - Si algún B19x disparó → eliminar B14.
      - B18 solo cuenta si había trade_count >= 2 (no recuperable desde DB;
        se asume que si B19b disparó era una compra única → eliminar B18d también).
    Devuelve (nuevo_score_raw, nuevo_score_final).
    """
    b19_fired = any(f.get("filter_id", "").startswith("B19") for f in filters)

    cleaned = []
    for f in filters:
        fid = f.get("filter_id", "")
        if b19_fired and fid == "B14":
            continue  # suprimido por B19
        if b19_fired and fid == "B18d":
            continue  # single buy no es acumulación
        cleaned.append(f)

    # Aplicar exclusión mutua (tomar el de mayor |points| por grupo)
    used: dict[str, dict] = {}  # group_key → filter con mayor |points|
    non_group: list[dict] = []

    for f in cleaned:
        fid = f.get("filter_id", "")
        group_found = False
        for group in ME_GROUPS:
            if fid in group:
                key = frozenset(group)
                if key not in used or abs(f.get("points", 0)) > abs(used[key].get("points", 0)):
                    used[key] = f
                group_found = True
                break
        if not group_found:
            non_group.append(f)

    final_filters = list(used.values()) + non_group
    score_raw = sum(f.get("points", 0) for f in final_filters)

    # Multiplicadores no se recalculan aquí (no tenemos monto/diversidad desde DB)
    # Se usa score_raw como aproximación conservadora.
    score_final = min(score_raw, config.SCORE_CAP)

    return score_raw, score_final


def _score_to_stars(score: int) -> int:
    for threshold, stars in config.NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    logger.info("[CORRECCIÓN] %s modo. %d alertas a procesar.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas))
    modificadas = 0

    for fila in filas:
        filters = _parse_filters(fila.get("filters_triggered"))
        nuevo_score_raw, nuevo_score_final = _recalcular_score(filters)
        nuevas_stars = _score_to_stars(nuevo_score_final)

        delta = fila.get("score_raw", 0) - nuevo_score_raw

        if DRY_RUN:
            logger.info(
                "  DRY-RUN id=%s: score_raw %d→%d (Δ%d), stars %s→%d",
                fila["id"], fila.get("score_raw", 0), nuevo_score_raw, -delta,
                fila.get("star_level"), nuevas_stars,
            )
        else:
            db.client.table("alerts").update({
                "score_raw": nuevo_score_raw,
                "score_final": nuevo_score_final,
                "star_level": nuevas_stars,
            }).eq("id", fila["id"]).execute()
            modificadas += 1
            logger.info("  ACTUALIZADO id=%s: score_raw→%d, stars→%d", fila["id"], nuevo_score_raw, nuevas_stars)

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se modificarían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas modificadas: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    logger.info("[VERIFICACIÓN] Comprobando que no quedan alertas con triple-counting...")
    restantes = diagnostico(db)
    if restantes:
        logger.warning("Aún quedan %d alertas con triple-counting. Revisar.", len(restantes))
    else:
        logger.info("OK — No quedan alertas con triple-counting B14+B18d+B19b.")


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient(supabase_url, supabase_key)

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron alertas afectadas.")
        return

    if not DRY_RUN:
        resp = input(f"\n¿Corregir {len(filas)} alertas en PRODUCCIÓN? [s/N] ").strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, filas)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
