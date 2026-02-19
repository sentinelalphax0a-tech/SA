"""
============================================================
MIGRATION: add_merge_columns
Fecha: 2026-02-19
Descripción:
  Añade columnas de detección de merge a la tabla alerts:
    - merge_suspected BOOLEAN DEFAULT FALSE
    - merge_confirmed BOOLEAN DEFAULT FALSE
  Añade campo close_reason TEXT a wallet_positions:
    - Valores: 'sell_clob' | 'merge_suspected' | 'net_zero' | 'position_gone'
    - Label para ML, no diagnóstico definitivo.

Idempotente: sí — usa IF NOT EXISTS / ignora errores si la columna ya existe.
No reversible automáticamente, pero las columnas solo añaden nullable
booleans/text — eliminarlas con ALTER TABLE alerts DROP COLUMN es seguro.

Ejecutar: python -m migrations.add_merge_columns
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
    """Check if a column exists via a test SELECT (idempotency guard)."""
    try:
        db.client.table(table).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def migrate(db: SupabaseClient) -> None:
    """Apply all column additions idempotently."""

    # ── 1. alerts.merge_suspected ─────────────────────────────
    if _column_exists(db, "alerts", "merge_suspected"):
        logger.info("alerts.merge_suspected already exists — skipping")
    else:
        logger.info("Adding alerts.merge_suspected ...")
        db.client.rpc(
            "exec_sql",
            {"sql": "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS merge_suspected BOOLEAN DEFAULT FALSE;"},
        ).execute()
        logger.info("  OK")

    # ── 2. alerts.merge_confirmed ─────────────────────────────
    if _column_exists(db, "alerts", "merge_confirmed"):
        logger.info("alerts.merge_confirmed already exists — skipping")
    else:
        logger.info("Adding alerts.merge_confirmed ...")
        db.client.rpc(
            "exec_sql",
            {"sql": "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS merge_confirmed BOOLEAN DEFAULT FALSE;"},
        ).execute()
        logger.info("  OK")

    # ── 3. wallet_positions.close_reason ─────────────────────
    if _column_exists(db, "wallet_positions", "close_reason"):
        logger.info("wallet_positions.close_reason already exists — skipping")
    else:
        logger.info("Adding wallet_positions.close_reason ...")
        db.client.rpc(
            "exec_sql",
            {
                "sql": (
                    "ALTER TABLE wallet_positions "
                    "ADD COLUMN IF NOT EXISTS close_reason TEXT "
                    "CHECK (close_reason IN ('sell_clob','merge_suspected','net_zero','position_gone'));"
                )
            },
        ).execute()
        logger.info("  OK")

    logger.info("Migration complete.")


def verify(db: SupabaseClient) -> None:
    """Verify all expected columns exist."""
    checks = [
        ("alerts", "merge_suspected"),
        ("alerts", "merge_confirmed"),
        ("wallet_positions", "close_reason"),
    ]
    all_ok = True
    for table, col in checks:
        exists = _column_exists(db, table, col)
        status = "OK" if exists else "MISSING"
        logger.info("  %s.%s → %s", table, col, status)
        if not exists:
            all_ok = False
    if all_ok:
        logger.info("Verification PASSED — all columns present.")
    else:
        logger.error("Verification FAILED — some columns missing.")
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
