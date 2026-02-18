"""
============================================================
VACUNA: falsos_positivos_ventana_corta
Fecha: 2026-02-17
Bug que corrige: Los filtros W04, W05, W09, B20, B23, B28 y N06 usaban únicamente
  la ventana de 35 minutos del scan para juzgar el historial de una wallet.
  Una wallet con 50 mercados en Polymarket podía disparar W04 ("solo 1 mercado")
  simplemente por no haber operado en los últimos 35 minutos.
  Score 20-30 pts más alto del real. 342 alertas corregidas retroactivamente.
Tablas afectadas: alerts
Filas estimadas a modificar: 342 (confirmado en ejecución)
Reversible: NO — recálculo in-place con datos del Data API de PM.
Aplicada en producción: SI
Commit que introdujo el fix en código: b11c119
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-17
  - Filas modificadas: 342
  - Observaciones: El script consultó el Data API de Polymarket (/activity) para
    cada wallet con W04/W05/W09/B20/B23/B28/N06 activos, y suprimió los filtros
    donde el historial real superaba el umbral de supresión configurado.
    Umbrales aplicados: W04 si real_distinct_markets>3, W05 si >5, B20/W09 si >3.
    Caché por wallet para minimizar llamadas a la API.
    El script fue ejecutado una vez y está comentado en cleaner_post_deep.py
    para evitar re-ejecución accidental.
"""

import json
import logging
import os
import sys
import time
from typing import Optional

from src import config
from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DRY_RUN = False

# Filtros que dependen de la ventana de lookback y pueden ser falsos positivos
LOOKBACK_DEPENDENT_FILTERS = {"W04", "W05", "W09", "B20", "B23", "B28", "N06"}

# Umbrales de supresión (replicando config.py)
W04_SUPPRESS_MARKETS = getattr(config, "W04_SUPPRESS_MARKETS", 3)
W05_SUPPRESS_MARKETS = getattr(config, "W05_SUPPRESS_MARKETS", 5)

PAGE_SIZE = 200

# Caché para evitar llamadas repetidas a la API por la misma wallet
_pm_history_cache: dict[str, Optional[dict]] = {}


def _get_pm_history(wallet_address: str, pm_client=None) -> Optional[dict]:
    """Consulta el historial real de PM para una wallet."""
    if wallet_address in _pm_history_cache:
        return _pm_history_cache[wallet_address]

    if pm_client is None:
        # Sin cliente PM disponible, no se puede verificar
        _pm_history_cache[wallet_address] = None
        return None

    try:
        history = pm_client.get_wallet_pm_history_cached(wallet_address)
        _pm_history_cache[wallet_address] = history
        time.sleep(0.1)  # rate limiting preventivo
        return history
    except Exception as e:
        logger.debug("Error obteniendo historial PM para %s: %s", wallet_address[:10], e)
        _pm_history_cache[wallet_address] = None
        return None


def _should_suppress_filter(filter_id: str, real_markets: int) -> bool:
    """Determina si un filtro debe suprimirse dado el historial real de la wallet."""
    if filter_id == "W04":
        return real_markets > W04_SUPPRESS_MARKETS
    if filter_id == "W05":
        return real_markets > W05_SUPPRESS_MARKETS
    if filter_id in ("W09", "B20", "B23", "B28", "N06"):
        return real_markets > W04_SUPPRESS_MARKETS  # mismo umbral que W04
    return False


def _parse_filters(raw) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return raw


def _score_to_stars(score: int) -> int:
    for threshold, stars in config.NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Detecta alertas con alguno de los filtros lookback-dependientes activos.
    """
    logger.info("[DIAGNÓSTICO] Buscando alertas con filtros lookback-dependientes...")

    candidatas = []
    offset = 0

    while True:
        rows = (
            db.client.table("alerts")
            .select("id, wallet_address, score_raw, score_final, star_level, filters_triggered")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        if not rows:
            break

        for row in rows:
            filters = _parse_filters(row.get("filters_triggered"))
            active_lookback = [
                f for f in filters
                if f.get("filter_id") in LOOKBACK_DEPENDENT_FILTERS
            ]
            if active_lookback:
                row["_lookback_filters"] = active_lookback
                candidatas.append(row)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Alertas con filtros lookback-dependientes: %d", len(candidatas))
    return candidatas


def correccion(db: SupabaseClient, filas: list[dict], pm_client=None) -> int:
    """
    Para cada alerta con filtros lookback-dependientes:
      1. Consulta el historial real de PM para la wallet.
      2. Suprime los filtros que superan el umbral de supresión.
      3. Recalcula score y stars.
      4. Actualiza la DB.
    """
    logger.info("[CORRECCIÓN] %s modo. %d alertas.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas))
    modificadas = 0
    suprimidas_total = 0

    for fila in filas:
        wallet = fila.get("wallet_address", "")
        filters_orig = _parse_filters(fila.get("filters_triggered"))

        history = _get_pm_history(wallet, pm_client)
        real_markets = history.get("distinct_markets", 0) if history else 0

        # Determinar qué filtros suprimir
        a_suprimir = set()
        for f in fila["_lookback_filters"]:
            fid = f.get("filter_id", "")
            if history is not None and _should_suppress_filter(fid, real_markets):
                a_suprimir.add(fid)

        if not a_suprimir:
            continue  # historial no justifica supresión

        suprimidas_total += len(a_suprimir)

        # Recalcular score sin los filtros suprimidos
        filters_clean = [f for f in filters_orig if f.get("filter_id") not in a_suprimir]
        nuevo_score_raw = sum(f.get("points", 0) for f in filters_clean)
        nuevo_score_final = min(nuevo_score_raw, config.SCORE_CAP)
        nuevas_stars = _score_to_stars(nuevo_score_final)

        if DRY_RUN:
            logger.info(
                "  DRY-RUN id=%s wallet=%s real_mkts=%d suprime=%s score %d→%d stars %s→%d",
                fila["id"], wallet[:10], real_markets, a_suprimir,
                fila.get("score_raw", 0), nuevo_score_raw,
                fila.get("star_level"), nuevas_stars,
            )
        else:
            db.client.table("alerts").update({
                "filters_triggered": json.dumps(filters_clean),
                "score_raw": nuevo_score_raw,
                "score_final": nuevo_score_final,
                "star_level": nuevas_stars,
                "notes": (
                    f"[vacuna 2026-02-17] suprimidos={list(a_suprimir)} "
                    f"real_distinct_markets={real_markets}"
                ),
            }).eq("id", fila["id"]).execute()
            modificadas += 1

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se modificarían: %d con %d filtros suprimidos",
                    sum(1 for f in filas if _get_pm_history(f.get("wallet_address", "")) is not None),
                    suprimidas_total)
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas corregidas: %d, filtros suprimidos: %d",
                    modificadas, suprimidas_total)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    """
    Confirma la distribución de star_level post-corrección.
    Solo informativo — no hay un estado 'correcto' verificable sin los datos de PM.
    """
    logger.info("[VERIFICACIÓN] Distribución de star_level post-corrección...")

    rows = (
        db.client.table("alerts")
        .select("star_level")
        .execute()
        .data
    )

    from collections import Counter
    dist = Counter(r.get("star_level", 0) for r in rows)
    for stars in sorted(dist):
        logger.info("  %d★: %d alertas", stars, dist[stars])


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient(supabase_url, supabase_key)

    # Intentar inicializar pm_client para verificar historial real
    pm_client = None
    try:
        from src.scanner.polymarket_client import PolymarketClient
        pm_client = PolymarketClient()
        logger.info("PolymarketClient inicializado — se consultará el Data API.")
    except Exception as e:
        logger.warning("No se pudo inicializar PolymarketClient: %s. Sin verificación de historial real.", e)

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron alertas con filtros lookback-dependientes.")
        return

    if not DRY_RUN:
        if pm_client is None:
            logger.error("Se requiere PolymarketClient para aplicar correcciones. Abortar.")
            sys.exit(1)
        resp = input(f"\n¿Corregir hasta {len(filas)} alertas en PRODUCCIÓN? [s/N] ").strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, filas, pm_client=pm_client)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
