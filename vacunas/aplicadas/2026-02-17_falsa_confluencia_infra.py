"""
============================================================
VACUNA: falsa_confluencia_infra
Fecha: 2026-02-17
Bug que corrige: Relay Solver (Polygon), Polymarket Wrapped Collateral y routers
  genéricos de Polymarket financiaban a cientos de wallets sin relación entre sí.
  El sistema detectaba estos senders como señales de coordinación (C03d "mismo padre
  directo", C07 "red de distribución"), generando confluencias completamente falsas.
Tablas afectadas: alerts (filters_triggered, score_raw, score_final, star_level)
Filas estimadas a modificar: alertas con C03d/C07 de senders de infraestructura
Reversible: Parcial — se puede restaurar filters_triggered original desde backup en 'notes'
Aplicada en producción: SI
Commit que introdujo el fix en código: c8e57dc
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-17
  - Filas modificadas: ver log
  - Observaciones: La lógica de este script es equivalente a cleaner_post_deep.py
    pero limitada a la corrección retroactiva puntual del bug histórico.
    cleaner_post_deep.py corre automáticamente después de cada deep scan para
    prevenir recurrencia futura. Este script es la corrección one-shot del pasado.
    Los senders de infra también se añadieron a KNOWN_INFRASTRUCTURE en config.py.
"""

import json
import logging
import os
import re
import sys
from collections import defaultdict

from src import config
from src.config import (
    KNOWN_BRIDGES,
    KNOWN_EXCHANGES,
    KNOWN_INFRASTRUCTURE,
    NEW_STAR_THRESHOLDS,
    SENDER_AUTO_EXCLUDE_MIN_WALLETS,
    STAR_VALIDATION,
)
from src.database.supabase_client import SupabaseClient

try:
    from src.scanner.blockchain_client import POLYMARKET_CONTRACTS
except ImportError:
    POLYMARKET_CONTRACTS: list[str] = []

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DRY_RUN = False

PAGE_SIZE = 500

# Filtros de confluencia que pueden llevar sender en su campo 'details'
CONFLUENCE_SENDER_FILTERS = {"C03d", "C07", "C06"}
# Todos los filtros de confluencia elegibles para eliminación
ALL_CONFLUENCE_FILTERS = {"C01", "C02", "C03a", "C03b", "C03c", "C03d", "C05", "C06", "C07"}

# Construir conjunto de exclusión
EXCLUSION_SET: set[str] = set()
for _d in (KNOWN_INFRASTRUCTURE, KNOWN_EXCHANGES, KNOWN_BRIDGES):
    EXCLUSION_SET |= {a.lower() for a in _d}
EXCLUSION_SET |= {a.lower() for a in POLYMARKET_CONTRACTS}

# Prefijos de las primeras 10 chars para matching con addresses truncadas en 'details'
EXCLUSION_PREFIXES: dict[str, str] = {}
for _addr in EXCLUSION_SET:
    prefix = _addr[:10]
    if prefix not in EXCLUSION_PREFIXES:
        EXCLUSION_PREFIXES[prefix] = _addr


def _parse_filters(raw) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return raw


def _extract_sender_prefix(details: str | None) -> str | None:
    if not details:
        return None
    m = re.search(r"(sender|distributor|exchange|bridge)=([0-9a-fA-Fx]{6,})", details)
    if m:
        return m.group(2)[:10].lower()
    return None


def _is_infra_sender(filter_dict: dict) -> bool:
    fid = filter_dict.get("filter_id", "")
    if fid not in CONFLUENCE_SENDER_FILTERS:
        return False
    prefix = _extract_sender_prefix(filter_dict.get("details"))
    if prefix and prefix in EXCLUSION_PREFIXES:
        return True
    return False


def _score_to_stars(score: int) -> int:
    for threshold, stars in NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0


def diagnostico(db: SupabaseClient) -> list[dict]:
    """Detecta alertas con filtros de confluencia disparados por senders de infra."""
    logger.info("[DIAGNÓSTICO] Buscando alertas con falsa confluencia por infra...")

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
            infra_filters = [f for f in filters if _is_infra_sender(f)]
            if infra_filters:
                row["_infra_filters"] = infra_filters
                afectadas.append(row)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Alertas con falsa confluencia por infra: %d", len(afectadas))
    for row in afectadas[:5]:
        infra = [f.get("filter_id") for f in row["_infra_filters"]]
        logger.info("  id=%s infra_filters=%s score_raw=%d stars=%s",
                    row["id"], infra, row.get("score_raw", 0), row.get("star_level"))

    return afectadas


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """
    Para cada alerta afectada:
      1. Elimina los filtros de confluencia disparados por senders de infra.
      2. También elimina todos los filtros de confluencia si quedan sin C01/C02
         (los layers superiores son inválidos sin base de dirección).
      3. Recalcula score_raw, score_final y star_level.
      4. Persiste el filters_triggered original en 'notes' como backup.
    """
    logger.info("[CORRECCIÓN] %s modo. %d alertas.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas))
    modificadas = 0

    me_groups = config.MUTUALLY_EXCLUSIVE_GROUPS

    for fila in filas:
        filters_orig = _parse_filters(fila.get("filters_triggered"))
        infra_ids = {f.get("filter_id") for f in fila["_infra_filters"]}

        # Eliminar filtros de infra
        filters_clean = [f for f in filters_orig if f.get("filter_id") not in infra_ids]

        # Si después del limpiado no quedan C01/C02, eliminar toda la confluencia
        remaining_ids = {f.get("filter_id") for f in filters_clean}
        if not (remaining_ids & {"C01", "C02"}):
            filters_clean = [f for f in filters_clean if f.get("filter_id") not in ALL_CONFLUENCE_FILTERS]

        # Recalcular score con mutual exclusion
        used: dict = {}
        non_group: list[dict] = []
        for f in filters_clean:
            fid = f.get("filter_id", "")
            group_found = False
            for group in me_groups:
                if fid in group:
                    key = frozenset(group)
                    if key not in used or abs(f.get("points", 0)) > abs(used[key].get("points", 0)):
                        used[key] = f
                    group_found = True
                    break
            if not group_found:
                non_group.append(f)

        final_filters = list(used.values()) + non_group
        nuevo_score_raw = sum(f.get("points", 0) for f in final_filters)
        nuevo_score_final = min(nuevo_score_raw, config.SCORE_CAP)
        nuevas_stars = _score_to_stars(nuevo_score_final)

        backup = f"[vacuna 2026-02-17] filters_orig={json.dumps([f.get('filter_id') for f in filters_orig])}"

        if DRY_RUN:
            logger.info(
                "  DRY-RUN id=%s: removería %s → score %d→%d stars %s→%d",
                fila["id"], infra_ids, fila.get("score_raw", 0), nuevo_score_raw,
                fila.get("star_level"), nuevas_stars,
            )
        else:
            db.client.table("alerts").update({
                "filters_triggered": json.dumps(final_filters),
                "score_raw": nuevo_score_raw,
                "score_final": nuevo_score_final,
                "star_level": nuevas_stars,
                "notes": backup,
            }).eq("id", fila["id"]).execute()
            modificadas += 1

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se modificarían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas corregidas: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    logger.info("[VERIFICACIÓN] Comprobando que no quedan falsas confluencias de infra...")
    restantes = diagnostico(db)
    if restantes:
        logger.warning("Aún quedan %d alertas con falsa confluencia. Revisar.", len(restantes))
    else:
        logger.info("OK — No quedan alertas con confluencia de senders de infra.")


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient(supabase_url, supabase_key)

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron alertas con falsa confluencia de infra.")
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
