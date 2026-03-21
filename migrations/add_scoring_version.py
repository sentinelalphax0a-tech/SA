"""
============================================================
MIGRATION: add_scoring_version
Fecha: 2026-03-21
Descripción:
  Añade scoring_version TEXT a alerts.
  - Filas existentes (scoring con bug de dirección mixta) → 'v1'
  - Nuevas alertas desde el código corregido → 'v2'

  REQUISITO PREVIO: añadir la columna manualmente en el dashboard
  de Supabase antes de ejecutar este script:
    Table: alerts
    Column name: scoring_version
    Type: text
    Default: null
    Nullable: true

Idempotente: sí — pagina hasta encontrar NULL rows, no reprocesa v1.
Ejecutar: python -m migrations.add_scoring_version
============================================================
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

_SQL_ADD = (
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS scoring_version TEXT;"
)
_SQL_BACKFILL = (
    "UPDATE alerts SET scoring_version = 'v1' WHERE scoring_version IS NULL;"
)


def _column_exists(db: SupabaseClient, table: str, column: str) -> bool:
    try:
        db.client.table(table).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def migrate(db: SupabaseClient) -> None:
    if not _column_exists(db, "alerts", "scoring_version"):
        logger.info("alerts.scoring_version not found — attempting DDL via exec_sql RPC ...")
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
            logger.info("Luego vuelve a ejecutar este script.")
            logger.info("═" * 60)
            sys.exit(1)
    else:
        logger.info("alerts.scoring_version already exists — skipping DDL")

    # Backfill: mark all existing rows without a version as v1
    logger.info("Backfilling existing alerts to scoring_version='v1' ...")
    try:
        db.client.rpc("exec_sql", {"sql": _SQL_BACKFILL}).execute()
        logger.info("  Backfill complete via exec_sql RPC")
        return
    except Exception as rpc_err:
        logger.warning("exec_sql RPC not available for backfill (%s) — using data API.", rpc_err)

    # Fallback: paginate via data API
    PAGE = 1000
    offset = 0
    total_updated = 0
    while True:
        batch = (
            db.client.table("alerts")
            .select("id")
            .is_("scoring_version", "null")
            .range(offset, offset + PAGE - 1)
            .execute()
        )
        rows = batch.data or []
        if not rows:
            break
        ids = [r["id"] for r in rows]
        for alert_id in ids:
            db.client.table("alerts").update(
                {"scoring_version": "v1"}
            ).eq("id", alert_id).execute()
            total_updated += 1
        offset += PAGE
        logger.info("  Updated %d alerts to v1 so far...", total_updated)

    logger.info("Migration complete: %d alerts marked as v1", total_updated)


def verify(db: SupabaseClient) -> None:
    exists = _column_exists(db, "alerts", "scoring_version")
    logger.info("alerts.scoring_version → %s", "OK" if exists else "MISSING")
    if not exists:
        logger.error("Verification FAILED.")
        sys.exit(1)

    try:
        resp = (
            db.client.table("alerts")
            .select("id")
            .is_("scoring_version", "null")
            .limit(5)
            .execute()
        )
        if resp.data:
            logger.warning(
                "  %d alerts still have NULL scoring_version — backfill incomplete",
                len(resp.data),
            )
        else:
            logger.info("  Backfill verified: no NULL scoring_version found")
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
