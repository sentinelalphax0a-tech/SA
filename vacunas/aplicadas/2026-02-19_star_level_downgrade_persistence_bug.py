"""
============================================================
VACUNA: star_level_downgrade_persistence_bug
Fecha: 2026-02-19
Bug que corrige: Alertas históricas donde filters_triggered solo
  persistía un subconjunto de los filtros (en general los de mercado),
  omitiendo wallet/origin/behavior/confluence.
  El score_raw fue calculado correctamente con todos los filtros en el
  momento de creación, pero STAR_VALIDATION sobre los datos persistidos
  falla los requisitos de min_categories y requires_coord, resultando
  en un star_level inflado respecto a lo que pueden acreditar los datos
  almacenados.
  Criterio: computed_score_raw (suma signed con exclusión mutua de
  filters_triggered) << score_raw almacenado por más de DELTA_THRESHOLD.
  Esto indica filtros positivos ausentes — no puede ocurrir si la
  persistencia estuviera completa.
  Corrección: recalcula star_level usando SOLO filters_triggered
  persistidos + score final + total_amount. El score_raw y score NO se
  modifican (fueron calculados correctamente en su momento).
  Alertas identificadas: 580 (5★→4★), 631 (5★→2★), 649 (3★→sin cambio)
Tablas afectadas: alerts
Filas estimadas a modificar: 2 (580, 631); 649 ya estaba corregida
Reversible: NO — recálculo in-place. Los valores originales se pierden.
Aplicada en producción: SI
Commit que introdujo el fix de persistencia: pre-2026-02-16
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-19
  - Filas modificadas: 2
  - Observaciones: id=580 5★→4★ (COORDINATION+TIMING, $12k, score=264).
    id=631 5★→2★ (solo TIMING, $25k, falla min_categories=2 para 3★).
    Verificación OK (0 restantes).
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

# Delta mínimo: computed_score_raw (desde filters_triggered) << score_raw almacenado
# Indica filtros positivos ausentes de filters_triggered.
DELTA_THRESHOLD = 10  # conservador: cualquier discrepancia > 10 pts


# ── Lógica de cálculo (misma que health_check.check_filter_sum) ─────────────

_filter_to_group: dict[str, frozenset] = {}
for _group in config.MUTUALLY_EXCLUSIVE_GROUPS:
    _fs = frozenset(_group)
    for _fid in _group:
        _filter_to_group[_fid] = _fs


def _computed_score_raw(filters: list[dict]) -> int:
    """Suma signed de puntos con exclusión mutua — replica health_check."""
    best_in_group: dict[frozenset, dict] = {}
    non_group: list[dict] = []
    for f in filters:
        fid = f.get("filter_id", "")
        grp = _filter_to_group.get(fid)
        if grp:
            prev = best_in_group.get(grp)
            if prev is None or abs(f.get("points", 0)) > abs(prev.get("points", 0)):
                best_in_group[grp] = f
        else:
            non_group.append(f)
    return max(0, sum(f.get("points", 0) for f in list(best_in_group.values()) + non_group))


# ── Lógica de recálculo de star (replica scoring.py) ────────────────────────

def _get_categories(filters: list[dict]) -> set[str]:
    """Solo filtros con points > 0 — replica scoring._get_categories()."""
    cats: set[str] = set()
    for f in filters:
        if f.get("points", 0) <= 0:
            continue
        new_cat = config.OLD_TO_NEW_CATEGORY.get(f.get("category", ""))
        if new_cat:
            cats.add(new_cat)
    return cats


def _score_to_stars(score: int) -> int:
    for threshold, stars in config.NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0


def _validate_stars(star_level: int, categories: set[str], total_amount: float) -> int:
    while star_level >= 3:
        reqs = config.STAR_VALIDATION.get(star_level)
        if reqs is None:
            break
        if len(categories) < reqs.get("min_categories", 0):
            star_level -= 1
            continue
        if total_amount < reqs.get("min_amount", 0):
            star_level -= 1
            continue
        if reqs.get("requires_coord", False) and "COORDINATION" not in categories:
            star_level -= 1
            continue
        break
    return star_level


def _apply_n09_cap(star_level: int, filters: list[dict]) -> int:
    ids = {f.get("filter_id", "") for f in filters}
    if "N09a" in ids:
        return min(star_level, config.OBVIOUS_BET_STAR_CAP_EXTREME)
    if "N09b" in ids:
        return min(star_level, config.OBVIOUS_BET_STAR_CAP_HIGH)
    return star_level


def _recalc_star(score: int, total_amount: float, filters: list[dict]) -> int:
    categories = _get_categories(filters)
    base_star = _score_to_stars(score)
    star = _validate_stars(base_star, categories, total_amount)
    star = _apply_n09_cap(star, filters)
    return star


# ── Fases ────────────────────────────────────────────────────────────────────

def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Detecta alertas donde computed_score_raw (desde filters_triggered persistidos)
    es significativamente menor que score_raw almacenado. Esto indica que faltan
    filtros positivos en filters_triggered (bug de persistencia).
    Solo flags alertas donde el star_level recalculado < star_level actual.
    """
    logger.info(
        "[DIAGNÓSTICO] Buscando alertas con star_level inflado por bug de persistencia "
        "(computed_score_raw << score_raw, delta > %d)...",
        DELTA_THRESHOLD,
    )

    afectadas = []
    offset = 0

    while True:
        rows = (
            db.client.table("alerts")
            .select("id,star_level,score,score_raw,total_amount,filters_triggered,created_at")
            .gte("star_level", 3)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        if not rows:
            break

        for row in rows:
            filters = row.get("filters_triggered") or []
            if not filters:
                continue

            score_raw = row.get("score_raw") or 0
            score = row.get("score") or 0
            total_amount = float(row.get("total_amount") or 0)
            star_level = row.get("star_level") or 0

            comp = _computed_score_raw(filters)

            # Solo el caso de filtros positivos ausentes (comp < score_raw)
            # La dirección opuesta (comp > score_raw) son filtros negativos ausentes
            # o drift de valores — no infla star_level.
            if score_raw - comp <= DELTA_THRESHOLD:
                continue

            new_star = _recalc_star(score, total_amount, filters)

            if new_star >= star_level:
                continue  # Sin inflación o ya correcto

            categories = _get_categories(filters)
            row["_new_star"] = new_star
            row["_computed"] = comp
            row["_delta"] = score_raw - comp
            row["_categories"] = categories
            row["_filter_ids"] = [f.get("filter_id") for f in filters]
            afectadas.append(row)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Alertas con star_level inflado: %d", len(afectadas))
    for row in afectadas:
        logger.info(
            "  id=%s  star=%d→%d | score=%d score_raw=%d computed=%d delta=%d"
            " | categories=%s | amount=%.0f | filters=%s",
            row["id"],
            row["star_level"],
            row["_new_star"],
            row.get("score", 0),
            row.get("score_raw", 0),
            row["_computed"],
            row["_delta"],
            row["_categories"],
            float(row.get("total_amount") or 0),
            row["_filter_ids"],
        )
    return afectadas


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    logger.info(
        "[CORRECCIÓN] %s modo. %d alertas.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas)
    )
    modificadas = 0

    for fila in filas:
        nuevo_star = fila["_new_star"]
        if DRY_RUN:
            logger.info(
                "  DRY-RUN id=%s: star_level %d→%d"
                " (score=%d categories=%s amount=%.0f)",
                fila["id"],
                fila["star_level"],
                nuevo_star,
                fila.get("score", 0),
                fila["_categories"],
                float(fila.get("total_amount") or 0),
            )
        else:
            db.client.table("alerts").update(
                {"star_level": nuevo_star}
            ).eq("id", fila["id"]).execute()
            logger.info(
                "  ACTUALIZADO id=%s: star_level %d→%d",
                fila["id"], fila["star_level"], nuevo_star,
            )
            modificadas += 1

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se modificarían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas corregidas: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    logger.info("[VERIFICACIÓN] Comprobando que no quedan alertas con star_level inflado...")
    restantes = diagnostico(db)
    if restantes:
        logger.warning("Aún quedan %d alertas con star_level inflado.", len(restantes))
    else:
        logger.info(
            "OK — No quedan alertas con star_level inflado por bug de persistencia."
        )


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient()

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron alertas afectadas. Nada que hacer.")
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
