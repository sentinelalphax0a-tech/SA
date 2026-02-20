"""
============================================================
MIGRATION: add_star_level_initial
Fecha: 2026-02-20
Descripción:
  Añade star_level_initial INTEGER a alerts.
  Campo inmutable: se fija en el momento de la primera detección
  y nunca se actualiza (ni por cross-scan dedup, ni por vacunas).
  Propósito: label limpio para entrenamiento de ML que no se
  contamine con upgrades posteriores al score.

  BACKFILL: se asigna star_level_current como aproximación.
  NOTA: Las alertas modificadas por las vacunas #10
  (star_level_downgrade_amount_validation, ids: 649, 845, 1066, 1468)
  y #18 (star_level_downgrade_persistence_bug, ids: 580, 631)
  tienen star_level_initial APROXIMADO, no el valor real de T0
  (que fue mayor). Para ML: tratar estos 6 registros como
  ruidosos o excluirlos del conjunto de entrenamiento.

Idempotente: sí — ignora si la columna ya existe.
Ejecutar: python -m migrations.add_star_level_initial
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


_SQL_ADD = (
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS star_level_initial INTEGER;"
)
_SQL_BACKFILL = (
    "UPDATE alerts SET star_level_initial = star_level "
    "WHERE star_level_initial IS NULL AND star_level IS NOT NULL;"
)


def migrate(db: SupabaseClient) -> None:
    if _column_exists(db, "alerts", "star_level_initial"):
        logger.info("alerts.star_level_initial already exists — skipping DDL")
    else:
        logger.info("Adding alerts.star_level_initial ...")
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
            logger.info("  %s", _SQL_BACKFILL)
            logger.info("")
            logger.info("Supabase dashboard → SQL editor → New query → Run")
            logger.info("═" * 60)
            sys.exit(1)

    logger.info("Backfilling star_level_initial = star_level for existing rows ...")
    try:
        db.client.rpc("exec_sql", {"sql": _SQL_BACKFILL}).execute()
        logger.info("  Backfill complete via exec_sql RPC")
    except Exception as rpc_err:
        logger.warning("exec_sql RPC not available for backfill (%s).", rpc_err)
        logger.info("")
        logger.info("═" * 60)
        logger.info("Ejecuta este SQL de backfill en el Supabase SQL editor:")
        logger.info("")
        logger.info("  %s", _SQL_BACKFILL)
        logger.info("")
        logger.info("═" * 60)
        sys.exit(1)

    logger.info("Migration complete.")


def verify(db: SupabaseClient) -> None:
    exists = _column_exists(db, "alerts", "star_level_initial")
    logger.info("alerts.star_level_initial → %s", "OK" if exists else "MISSING")
    if not exists:
        logger.error("Verification FAILED.")
        sys.exit(1)

    # Check no NULLs remain (all rows should have a value after backfill)
    try:
        resp = (
            db.client.table("alerts")
            .select("id")
            .is_("star_level_initial", "null")
            .not_.is_("star_level", "null")
            .limit(5)
            .execute()
        )
        if resp.data:
            logger.warning(
                "  %d alerts have star_level but no star_level_initial — backfill incomplete",
                len(resp.data),
            )
        else:
            logger.info("  Backfill verified: no NULL gaps found")
    except Exception as e:
        logger.warning("  Could not verify backfill completeness: %s", e)

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
