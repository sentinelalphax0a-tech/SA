"""
============================================================
MIGRATION: add_realized_return
Fecha: 2026-03-03
Descripción:
  Añade columna realized_return (FLOAT, nullable) a la tabla alerts.

  Diferencia con actual_return:
    - actual_return: resultado binario del mercado (correcto=+X%, incorrecto=-100%)
    - realized_return: PnL real del whale, ponderando ventas CLOB + porción no vendida

  Nullable — alertas sin sell events tienen realized_return = actual_return
  (calculado en resolver.py al resolver). Alertas históricas ya resueltas
  quedan NULL hasta que se corra una vacuna de backfill si se requiere.

Idempotente: sí — ADD COLUMN IF NOT EXISTS.
Ejecutar: python -m migrations.add_realized_return
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

_SQL = "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS realized_return FLOAT;"
_VERIFY_COLUMN = "realized_return"


def _column_exists(db: SupabaseClient, table: str, column: str) -> bool:
    try:
        db.client.table(table).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def migrate(db: SupabaseClient) -> None:
    logger.info("Running migration: add_realized_return ...")
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
