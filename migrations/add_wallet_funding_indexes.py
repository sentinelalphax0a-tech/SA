"""
============================================================
MIGRATION: add_wallet_funding_indexes
Fecha: 2026-02-24
Descripción:
  Añade índices a wallet_funding, alerts y wallet_positions
  para eliminar los sequential scans que causan timeouts (57014)
  en el deep scan de confluence detection.

  wallet_funding tenía 1.2M filas con CERO índices salvo el PK.
  Cada get_funding_sources() hacía un full sequential scan → 45s timeout.

  Índices críticos:
    1. wallet_funding(wallet_address)         ← CRÍTICO: 45s → <5ms
    2. wallet_funding(sender_address)         ← get_high_fanout_senders
    3. wallet_funding UNIQUE(wallet_address, sender_address, hop_level)
       ← EJECUTAR SOLO DESPUÉS de purge_wallet_funding_duplicates.py

  Índices secundarios (preventivos — tablas pequeñas ahora, crecerán):
    4. alerts(outcome)                        ← resolver pagination
    5. alerts(market_id, direction, created_at) ← cross-scan dedup
    6. wallet_positions(alert_id)             ← sell detector
    7. wallet_positions(current_status)       ← sell detector

  IMPORTANTE: exec_sql RPC no está disponible en este proyecto.
  Este script imprime el SQL a ejecutar manualmente en el
  Supabase SQL Editor (dashboard → SQL Editor → New query → Run).

Idempotente: sí — todos usan IF NOT EXISTS o CONCURRENTLY (no falla si ya existe).
Ejecutar: python -m migrations.add_wallet_funding_indexes
============================================================
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

# ── SQL statements ────────────────────────────────────────────────────────────

# FASE 1: Índices críticos (ejecutar ANTES de la purga)
_SQL_PHASE1 = [
    (
        "idx_wallet_funding_wallet_address",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wallet_funding_wallet_address
    ON wallet_funding (wallet_address);""",
        "CRÍTICO — convierte get_funding_sources() de 45s a <5ms",
    ),
    (
        "idx_wallet_funding_sender_address",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wallet_funding_sender_address
    ON wallet_funding (sender_address);""",
        "Para get_high_fanout_senders fallback scan",
    ),
]

# FASE 2: Unique constraint (ejecutar DESPUÉS de purge_wallet_funding_duplicates.py)
_SQL_PHASE2 = [
    (
        "idx_wallet_funding_unique",
        """CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_wallet_funding_unique
    ON wallet_funding (wallet_address, sender_address, hop_level);""",
        "DESPUÉS DE PURGA — evita duplicación futura en upserts",
    ),
]

# FASE 3: Índices secundarios en otras tablas
_SQL_PHASE3 = [
    (
        "idx_alerts_outcome",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_alerts_outcome
    ON alerts (outcome);""",
        "Resolver: pagination de alertas pending",
    ),
    (
        "idx_alerts_market_direction",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_alerts_market_direction
    ON alerts (market_id, direction, created_at DESC);""",
        "Cross-scan dedup: filtra por market_id + direction + created_at",
    ),
    (
        "idx_wallet_positions_alert_id",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wallet_positions_alert_id
    ON wallet_positions (alert_id);""",
        "Sell detector: lookup por alert_id",
    ),
    (
        "idx_wallet_positions_status",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wallet_positions_status
    ON wallet_positions (current_status);""",
        "Sell detector: filtra open positions",
    ),
]


def _print_sql_instructions(statements: list[tuple], phase: str) -> None:
    logger.info("═" * 65)
    logger.info("FASE %s — Ejecuta en Supabase SQL Editor:", phase)
    logger.info("  dashboard.supabase.com → SQL Editor → New query → Run")
    logger.info("═" * 65)
    logger.info("")
    for name, sql, description in statements:
        logger.info("-- %s", description)
        logger.info("%s", sql)
        logger.info("")
    logger.info("═" * 65)


def _try_exec_rpc(db: SupabaseClient, sql: str) -> bool:
    """Try to run SQL via exec_sql RPC. Returns True if successful."""
    try:
        db.client.rpc("exec_sql", {"sql": sql}).execute()
        return True
    except Exception:
        return False


def migrate(db: SupabaseClient) -> None:
    logger.info("=== add_wallet_funding_indexes ===")
    logger.info("")
    logger.info("CONTEXTO: wallet_funding tiene 1.2M filas con 0 índices.")
    logger.info("Cada get_funding_sources() hace sequential scan → 45s timeout (57014).")
    logger.info("")

    # Try exec_sql RPC for each statement
    all_via_rpc = True
    for phase_statements in [_SQL_PHASE1, _SQL_PHASE3]:
        for name, sql, description in phase_statements:
            if not _try_exec_rpc(db, sql):
                all_via_rpc = False
                break
        if not all_via_rpc:
            break

    if all_via_rpc:
        logger.info("OK: todos los índices de fase 1 y 3 creados via exec_sql RPC.")
        logger.info("")
        logger.info("PENDIENTE — Ejecutar DESPUÉS de purge_wallet_funding_duplicates.py:")
        _print_sql_instructions(_SQL_PHASE2, "2 (post-purga)")
        return

    # RPC not available — print all instructions
    logger.info("exec_sql RPC no disponible. Ejecuta el siguiente SQL MANUALMENTE.")
    logger.info("")
    logger.info("ORDEN RECOMENDADO:")
    logger.info("  1. Ejecuta FASE 1 ahora")
    logger.info("  2. Ejecuta: python -m migrations.purge_wallet_funding_duplicates")
    logger.info("  3. Ejecuta FASE 2 (unique index, solo después de purga)")
    logger.info("  4. Ejecuta FASE 3 (índices secundarios, cualquier momento)")
    logger.info("")

    _print_sql_instructions(_SQL_PHASE1, "1 (CRÍTICA — ejecutar primero)")
    logger.info("")
    _print_sql_instructions(_SQL_PHASE2, "2 (DESPUÉS de la purga)")
    logger.info("")
    _print_sql_instructions(_SQL_PHASE3, "3 (secundaria — ejecutar en cualquier momento)")

    sys.exit(1)


def verify(db: SupabaseClient) -> None:
    """Verify indexes exist by timing a get_funding_sources call."""
    import time

    logger.info("=== Verificación de índices ===")

    # Test wallet_funding query speed
    resp = db.client.table("wallet_funding").select("wallet_address").limit(1).execute()
    if not resp.data:
        logger.warning("wallet_funding está vacía — nada que verificar")
        return

    test_addr = resp.data[0]["wallet_address"]
    t0 = time.time()
    try:
        rows = db.get_funding_sources(test_addr)
        elapsed = time.time() - t0
        logger.info(
            "get_funding_sources(%s…): %d rows en %.3fs",
            test_addr[:10], len(rows), elapsed,
        )
        if elapsed < 1.0:
            logger.info("  ✓ Índice activo — query rápida")
        else:
            logger.warning("  ✗ Query lenta (%.1fs) — índice posiblemente ausente", elapsed)
    except Exception as e:
        logger.error("  ✗ get_funding_sources falló: %s", e)
        logger.error("  Índice idx_wallet_funding_wallet_address probablemente no existe aún.")
        sys.exit(1)


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)
    db = SupabaseClient()
    migrate(db)
    verify(db)


if __name__ == "__main__":
    main()
