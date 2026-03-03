"""
============================================================
MIGRATION: add_odds_at_resolution_raw
Fecha: 2026-03-03
Descripción:
  Añade columna odds_at_resolution_raw (FLOAT, nullable) a la tabla alerts.

  Diferencia con odds_at_resolution:
    - odds_at_resolution: binario (1.0 si correcto, 0.0 si incorrecto)
    - odds_at_resolution_raw: precio YES real del mercado en el momento
      de resolución, obtenido del último market_snapshot disponible.
      Indica timing — 0.97 vs 0.65 al resolver tiene valor de ML.

  Nullable — se escribe en resolver.py al resolver cada alerta.
  Alertas históricas ya resueltas quedan NULL.

Idempotente: sí — ADD COLUMN IF NOT EXISTS.
Ejecutar: python -m migrations.add_odds_at_resolution_raw
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

_SQL = "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS odds_at_resolution_raw FLOAT;"
_VERIFY_COLUMN = "odds_at_resolution_raw"


def _column_exists(db: SupabaseClient, table: str, column: str) -> bool:
    try:
        db.client.table(table).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def migrate(db: SupabaseClient) -> None:
    logger.info("Running migration: add_odds_at_resolution_raw ...")
    try:
        db.client.rpc("exec_sql", {"sql": _SQL}).execute()
        logger.info("  Column added via exec_sql RPC.")
    except Exception as rpc_err:
        logger.warning("exec_sql RPC not available (%s).", rpc_err)
        logger.info("")
        logger.info("═" * 60)
        logger.info("Ejecuta este SQL en el Supabase SQL editor:")
        logger.info("")
        logger.info("  %s", _SQL)
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
