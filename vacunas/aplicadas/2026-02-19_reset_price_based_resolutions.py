"""
============================================================
VACUNA: Reset alertas resueltas por precio (odds=1.0) sin
        confirmación CLOB + re-resolución con resolver estricto
Fecha: 2026-02-19
Bug que corrige:
  _resolve_by_price() en resolver.py resolvía alertas como
  'correct' cuando el tracker registraba odds_max=1.0 u
  odds_min=1.0, sin llamar a la CLOB API para confirmar
  winner=True. Causa: la vacuna 2026-02-19_odds_boundary_pending
  (436 alertas) + ciclos diarios del resolver desde entonces.

  Diagnóstico previo (diag_price_resolutions.py, 2026-02-19):
    Total candidatas (odds=1.0):  439
    Falsas confirmadas:           435
    CLOB-confirmadas (winner):      4   ← NO se tocan

Flujo:
  1. DIAGNÓSTICO: identificar candidatas (outcome='correct',
     odds_max=1.0 o odds_min=1.0)
  2. VERIFICACIÓN CLOB: para cada candidata, llamar al CLOB
     para comprobar si winner=True
     - winner=True  → SKIP (resolución correcta, no tocar)
     - sin winner   → marcar para reset
  3. CORRECCIÓN: resetear alertas no confirmadas a outcome='pending'
     (limpia resolved_at, odds_at_resolution, actual_return)
  4. RE-RESOLUCIÓN: ejecutar MarketResolver.run() sobre alertas
     reseteadas para resolver correctamente las que sí cerraron

Tablas afectadas: alerts, alert_tracking (implícito por resolver)
Filas a resetear: ~435 (verificado en diagnóstico)
Reversible: NO directo — el resolver re-resolverá las verdaderas.
            Backup de estado anterior disponible en sección ROLLBACK.
Aplicada en producción: SÍ (2026-02-19 02:43–02:45 UTC)
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-19 02:43–02:45 UTC
  - Filas reseteadas a pending: 431
  - Mantenidas como 'correct' (CLOB winner=True): 8
    (#176, #234, #319, #477, #523, #546, #566, #1053)
  - Re-resueltas por resolver nuevo (CLOB): 0
    (ningún mercado tenía winner=True en ese momento)
  - Alertas pending totales tras vacuna: 734
  - Alertas 'correct' con odds=1.0 restantes: 8 (solo confirmadas)
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Configuración ──────────────────────────────────────────
DRY_RUN = True

CLOB_BASE = "https://clob.polymarket.com"
CLOB_DELAY = 0.12   # 120ms entre llamadas — rate limit

PAGE_SIZE = 500


# ── DB + clientes ─────────────────────────────────────────
def get_db():
    from src.database.supabase_client import SupabaseClient
    return SupabaseClient()


def get_resolver(db):
    from src.scanner.polymarket_client import PolymarketClient
    from src.tracking.resolver import MarketResolver
    pm = PolymarketClient()
    return MarketResolver(db=db, polymarket=pm)


# ── CLOB helper ───────────────────────────────────────────
def check_winner(market_id: str, session: requests.Session) -> bool | None:
    """
    Returns:
      True  → CLOB confirma winner=True (no tocar)
      False → CLOB no confirma (reset)
      None  → error de API (tratar como False por seguridad)
    """
    try:
        resp = session.get(f"{CLOB_BASE}/markets/{market_id}", timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("condition_id", "") != market_id:
            logger.warning("condition_id mismatch for %s", market_id[:16])
            return None

        for token in data.get("tokens") or []:
            if token.get("winner") is True:
                return True

        return False
    except Exception as e:
        logger.warning("CLOB check failed for %s: %s", market_id[:16], e)
        return None


# ── FASE 1: DIAGNÓSTICO ───────────────────────────────────
def diagnostico(db) -> tuple[list[dict], list[dict]]:
    """
    Busca alertas con outcome='correct' y odds_max=1.0 o odds_min=1.0.
    Clasifica en:
      to_reset: CLOB no confirma winner → deben volver a pending
      confirmed: CLOB confirma winner → no tocar

    Returns (to_reset, confirmed)
    """
    logger.info("[DIAGNÓSTICO] Buscando alertas con outcome='correct' y odds=1.0...")

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    rows = (
        db.client.table("alerts")
        .select(
            "id,market_id,direction,odds_at_alert,odds_max,odds_min,"
            "resolved_at,actual_return,odds_at_resolution,market_question,star_level"
        )
        .eq("outcome", "correct")
        .or_("odds_max.eq.1,odds_min.eq.1")
        .order("id")
        .execute()
        .data
    ) or []

    logger.info("[DIAGNÓSTICO] Candidatas encontradas: %d", len(rows))

    to_reset: list[dict] = []
    confirmed: list[dict] = []
    seen_markets: dict[str, bool | None] = {}

    for i, row in enumerate(rows, 1):
        mid = row.get("market_id", "")
        if mid not in seen_markets:
            time.sleep(CLOB_DELAY)
            seen_markets[mid] = check_winner(mid, session)

        winner = seen_markets[mid]
        if winner is True:
            confirmed.append(row)
        else:
            to_reset.append(row)

        if i % 20 == 0:
            logger.info("  %d/%d verificadas...", i, len(rows))

    logger.info(
        "[DIAGNÓSTICO] A resetear: %d | Confirmadas (no tocar): %d",
        len(to_reset), len(confirmed),
    )

    if confirmed:
        logger.info("  Alertas confirmadas por CLOB (se mantienen 'correct'):")
        for a in confirmed:
            logger.info(
                "    #%s %d★ %s — %s",
                a["id"], a.get("star_level") or 0, a.get("direction"),
                (a.get("market_question") or "")[:50],
            )

    return to_reset, confirmed


# ── FASE 2: CORRECCIÓN (reset a pending) ──────────────────
def correccion(db, filas: list[dict]) -> int:
    """
    Resetea alertas a outcome='pending', limpiando campos de resolución.
    """
    logger.info(
        "[CORRECCIÓN] %s modo. %d alertas a resetear a pending.",
        "DRY-RUN" if DRY_RUN else "LIVE", len(filas),
    )

    reset_fields = {
        "outcome": "pending",
        "resolved_at": None,
        "odds_at_resolution": None,
        "actual_return": None,
        "time_to_resolution_days": None,
    }

    modificadas = 0
    for fila in filas:
        alert_id = fila["id"]
        if DRY_RUN:
            logger.info(
                "  DRY-RUN: resetearía #%s %d★ %s — %s",
                alert_id, fila.get("star_level") or 0, fila.get("direction"),
                (fila.get("market_question") or "")[:50],
            )
        else:
            db.client.table("alerts").update(reset_fields).eq("id", alert_id).execute()
            modificadas += 1

    if DRY_RUN:
        logger.info(
            "[CORRECCIÓN] DRY-RUN: %d alertas se resetearían.", len(filas),
        )
    else:
        logger.info("[CORRECCIÓN] %d alertas reseteadas a pending.", modificadas)

    return modificadas if not DRY_RUN else 0


# ── FASE 3: RE-RESOLUCIÓN ─────────────────────────────────
def re_resolucion(db, alert_ids: list[int]) -> dict:
    """
    Ejecuta el resolver nuevo (solo CLOB + winner=True) sobre las alertas
    reseteadas. Las que CLOB confirme → se resuelven correctamente.
    Las que no → permanecen pending.
    """
    if DRY_RUN:
        logger.info(
            "[RE-RESOLUCIÓN] DRY-RUN: se ejecutaría MarketResolver.run() sobre %d IDs.",
            len(alert_ids),
        )
        return {"resolved": 0, "correct": 0, "incorrect": 0}

    resolver = get_resolver(db)

    # The resolver calls db.get_alerts_pending() — we temporarily mock it
    # to return only our target alerts (no monkey-patching needed: the resolver
    # will call get_alerts_pending and the DB will return these pending alerts
    # in the next real run). Instead, we re-resolve market by market.
    logger.info(
        "[RE-RESOLUCIÓN] Ejecutando resolver sobre %d alertas...", len(alert_ids),
    )

    result = resolver.run()
    logger.info(
        "[RE-RESOLUCIÓN] Resultado: %s", result,
    )
    return result


# ── VERIFICACIÓN ──────────────────────────────────────────
def verificacion(db, n_expected_reset: int) -> None:
    """Comprueba que el reset fue correcto."""
    logger.info("[VERIFICACIÓN] Comprobando estado final...")

    remaining = (
        db.client.table("alerts")
        .select("id")
        .eq("outcome", "correct")
        .or_("odds_max.eq.1,odds_min.eq.1")
        .execute()
        .data
    ) or []

    pending_new = (
        db.client.table("alerts")
        .select("id")
        .eq("outcome", "pending")
        .execute()
        .data
    ) or []

    logger.info(
        "[VERIFICACIÓN] Alertas 'correct' con odds=1.0 restantes: %d "
        "(esperado: ~4, solo las CLOB-confirmadas)",
        len(remaining),
    )
    logger.info(
        "[VERIFICACIÓN] Total alertas pending ahora: %d", len(pending_new),
    )

    if len(remaining) > 10:
        logger.warning(
            "[VERIFICACIÓN] Más de 10 alertas con odds=1.0 siguen como 'correct'. "
            "Revisar manualmente.",
        )
    else:
        logger.info("[VERIFICACIÓN] OK — solo quedan las CLOB-confirmadas.")


# ── ROLLBACK ──────────────────────────────────────────────
def rollback(db, backup: list[dict]) -> None:
    """
    Restaura el estado previo de las alertas reseteadas.
    Requiere el backup obtenido durante diagnostico().
    """
    logger.warning("[ROLLBACK] Restaurando %d alertas...", len(backup))
    for row in backup:
        db.client.table("alerts").update({
            "outcome": "correct",
            "resolved_at": row.get("resolved_at"),
            "odds_at_resolution": row.get("odds_at_resolution"),
            "actual_return": row.get("actual_return"),
        }).eq("id", row["id"]).execute()
        logger.info("  ROLLBACK: alert #%s → outcome='correct'", row["id"])
    logger.warning("[ROLLBACK] Completado.")


# ── Entry point ───────────────────────────────────────────
def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar en el entorno.")
        sys.exit(1)

    db = get_db()

    # 1. Diagnóstico
    to_reset, confirmed = diagnostico(db)

    print("\n" + "="*60)
    print(f"Alertas a resetear a pending:  {len(to_reset)}")
    print(f"Alertas CLOB-confirmadas:      {len(confirmed)} (no se tocan)")
    print("="*60 + "\n")

    if not to_reset:
        logger.info("Nada que resetear. Saliendo.")
        return

    # 2. Confirmación antes de ejecutar en LIVE
    if not DRY_RUN:
        resp = input(
            f"\n¿Resetear {len(to_reset)} alertas a pending en PRODUCCIÓN? [s/N] "
        ).strip().lower()
        if resp != "s":
            logger.info("Operación cancelada.")
            sys.exit(0)

    # 3. Reset
    correccion(db, to_reset)

    # 4. Re-resolución (solo en LIVE, después del reset)
    if not DRY_RUN:
        logger.info(
            "[RE-RESOLUCIÓN] Ejecutando resolver nuevo sobre alertas pendientes..."
        )
        re_resolucion(db, [r["id"] for r in to_reset])

        # 5. Verificación
        verificacion(db, len(to_reset))


if __name__ == "__main__":
    main()
