"""
============================================================
MIGRATION: add_dca_price_fields
Fecha: 2026-03-03
Descripción:
  Añade dos columnas FLOAT (nullable) a la tabla alerts para
  trackear los precios de las compras adicionales (DCA):

  - avg_additional_buy_price: precio promedio ponderado por amount
    de todas las compras adicionales detectadas por whale_monitor.
    Se recalcula en cada ADDITIONAL_BUY event.
  - last_additional_buy_price: precio de la compra adicional más
    reciente (último ADDITIONAL_BUY event procesado).

  Nullable — alertas sin compras adicionales quedan NULL.
  Se escriben en whale_monitor._process_additional_buy().
  No afectan scoring ni star_level.

Idempotente: sí — ADD COLUMN IF NOT EXISTS.
Ejecutar: python -m migrations.add_dca_price_fields
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
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS avg_additional_buy_price FLOAT;",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_additional_buy_price FLOAT;",
]

_VERIFY_COLUMN = "avg_additional_buy_price"


def _column_exists(db: SupabaseClient, table: str, column: str) -> bool:
    try:
        db.client.table(table).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def migrate(db: SupabaseClient) -> None:
    logger.info("Running migration: add_dca_price_fields (%d columns) ...", len(_SQL_STATEMENTS))
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
