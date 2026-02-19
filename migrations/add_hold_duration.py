"""
============================================================
MIGRATION: add_hold_duration
Fecha: 2026-02-19
Descripción:
  Añade hold_duration_hours FLOAT a wallet_positions.
  Registra cuántas horas aguantó la wallet su posición
  desde la creación de la alerta hasta que se detectó
  la venta (sell_clob). Solo tracking — no afecta scoring.

Idempotente: sí — ignora si la columna ya existe.
Ejecutar: python -m migrations.add_hold_duration
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


def _column_exists(db: SupabaseClient, table: str, column: str) -> bool:
    try:
        db.client.table(table).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def migrate(db: SupabaseClient) -> None:
    if _column_exists(db, "wallet_positions", "hold_duration_hours"):
        logger.info("wallet_positions.hold_duration_hours already exists — skipping")
    else:
        logger.info("Adding wallet_positions.hold_duration_hours ...")
        db.client.rpc(
            "exec_sql",
            {"sql": "ALTER TABLE wallet_positions ADD COLUMN IF NOT EXISTS hold_duration_hours FLOAT;"},
        ).execute()
        logger.info("  OK")

    logger.info("Migration complete.")


def verify(db: SupabaseClient) -> None:
    exists = _column_exists(db, "wallet_positions", "hold_duration_hours")
    logger.info("wallet_positions.hold_duration_hours → %s", "OK" if exists else "MISSING")
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
