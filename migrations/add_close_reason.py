"""
============================================================
MIGRATION: add_close_reason
Fecha: 2026-02-20
Descripción:
  Añade close_reason TEXT a alerts.
  Este campo es escrito por SellDetector.check_net_positions()
  para registrar cómo se cerró una posición (ML label).

  Valores posibles:
    'sell_clob'       — venta explícita en el CLOB
    'merge_suspected' — merge CTF vía compra del lado opuesto
    'net_zero'        — posición neta cero (sin actividad CLOB visible)
    'position_gone'   — posición desapareció (CTF burn, transfer, etc.)

  NOTA: La columna ya existe en wallet_positions.close_reason.
  Este campo en alerts es un resumen a nivel de alerta
  (la razón de cierre predominante de las wallets monitoreadas).

  Sin este campo la columna:
    - update_alert_fields(id, {"close_reason": ...}) falla silenciosamente
    - El timeline del dashboard no puede mostrar merge/position_gone
    - El formatter.py no puede recuperar el close_reason de la alerta

Idempotente: sí — ignora si la columna ya existe.
Ejecutar: python -m migrations.add_close_reason
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


_SQL_ADD = "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS close_reason TEXT;"

# No backfill needed: historical alerts did not have this written (silent fail).
# New alerts will populate it going forward.


def migrate(db: SupabaseClient) -> None:
    if _column_exists(db, "alerts", "close_reason"):
        logger.info("alerts.close_reason already exists — skipping DDL")
        return

    logger.info("Adding alerts.close_reason ...")
    try:
        db.client.rpc("exec_sql", {"sql": _SQL_ADD}).execute()
        logger.info("  Column added via exec_sql RPC")
    except Exception as rpc_err:
        logger.warning("exec_sql RPC not available (%s).", rpc_err)
        logger.info("")
        logger.info("═" * 60)
        logger.info("Ejecuta este SQL en el Supabase SQL editor:")
        logger.info("")
        logger.info("  %s", _SQL_ADD)
        logger.info("")
        logger.info("Supabase dashboard → SQL editor → New query → Run")
        logger.info("═" * 60)
        sys.exit(1)

    logger.info("Migration complete.")


def verify(db: SupabaseClient) -> None:
    exists = _column_exists(db, "alerts", "close_reason")
    logger.info("alerts.close_reason → %s", "OK" if exists else "MISSING")
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
