"""
============================================================
MIGRATION: add_ml_snapshot_fields
Fecha: 2026-02-25
Descripción:
  Añade 11 columnas de snapshot ML a la tabla alerts.

  Estos campos capturan el estado T0 de la alerta en el
  momento de la primera detección y son inmutables —
  nunca se sobreescriben por cross-scan dedup ni vacunas.
  Permiten entrenar modelos con el estado real en T0 en lugar
  del estado mutado tras upgrades/consolidaciones.

  Todos los campos son nullable — alertas históricas quedan
  sin valores y eso es aceptable para el training set.

Idempotente: sí — usa ADD COLUMN IF NOT EXISTS.
Ejecutar: python -m migrations.add_ml_snapshot_fields
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

_SQL_STATEMENTS = [
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS scan_mode TEXT;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS score_initial INTEGER;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS score_raw_initial INTEGER;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS odds_at_alert_initial FLOAT;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS total_amount_initial FLOAT;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS filters_triggered_initial JSONB;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS market_category TEXT;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS market_volume_24h_at_alert FLOAT;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS market_liquidity_at_alert FLOAT;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS hours_to_deadline FLOAT;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS wallets_count_initial INTEGER;",
]

# Spot-check column used in verify()
_VERIFY_COLUMN = "scan_mode"


def _column_exists(db: SupabaseClient, table: str, column: str) -> bool:
    try:
        db.client.table(table).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def migrate(db: SupabaseClient) -> None:
    logger.info("Running migration: add_ml_snapshot_fields (%d columns) ...", len(_SQL_STATEMENTS))
    try:
        for sql in _SQL_STATEMENTS:
            db.client.rpc("exec_sql", {"sql": sql}).execute()
        logger.info("  All %d columns added via exec_sql RPC.", len(_SQL_STATEMENTS))
    except Exception as rpc_err:
        logger.warning("exec_sql RPC not available (%s).", rpc_err)
        logger.info("")
        logger.info("═" * 60)
        logger.info("Ejecuta este SQL en el Supabase SQL editor:")
        logger.info("")
        for sql in _SQL_STATEMENTS:
            logger.info("  %s", sql)
        logger.info("")
        logger.info("Supabase dashboard → SQL editor → New query → Run")
        logger.info("═" * 60)
        sys.exit(1)

    logger.info("Migration complete.")


def verify(db: SupabaseClient) -> None:
    exists = _column_exists(db, "alerts", _VERIFY_COLUMN)
    logger.info("alerts.%s → %s", _VERIFY_COLUMN, "OK" if exists else "MISSING")
    if not exists:
        logger.error("Verification FAILED.")
        sys.exit(1)
    logger.info("Verification PASSED.")


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)
    db = SupabaseClient()
    migrate(db)
    verify(db)


if __name__ == "__main__":
    main()
