"""
============================================================
MIGRATION: add_alert_score_history
Fecha: 2026-03-03
Descripción:
  Crea la tabla alert_score_history para registrar cada vez
  que score o star_level de una alerta existente cambia.

  Cuándo se inserta una fila:
    - "cross_scan_dedup": nueva detección del mismo mercado <24h
      con score/star superior sobreescribe el existente.
    - "consolidation": nuevas wallets elevan el score de una
      alerta 4+★ ya publicada (star NO cambia en consolidación).

  Los campos *_initial de alerts siguen siendo el sello T0 inmutable.
  Esta tabla registra la evolución posterior al primer insert.

  Alertas históricas no tendrán filas aquí — solo nuevos eventos
  desde que la tabla existe.

Idempotente: sí — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
Ejecutar: python -m migrations.add_alert_score_history
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

_SQL = """
CREATE TABLE IF NOT EXISTS alert_score_history (
    id             BIGSERIAL PRIMARY KEY,
    alert_id       BIGINT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    old_star_level SMALLINT,
    new_star_level SMALLINT,
    old_score      INTEGER,
    new_score      INTEGER,
    change_reason  TEXT,
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_score_history_alert ON alert_score_history(alert_id);
CREATE INDEX IF NOT EXISTS idx_score_history_ts    ON alert_score_history(changed_at DESC);
"""

_VERIFY_TABLE = "alert_score_history"


def _table_exists(db: SupabaseClient, table: str) -> bool:
    try:
        db.client.table(table).select("id").limit(1).execute()
        return True
    except Exception:
        return False


def migrate(db: SupabaseClient) -> None:
    logger.info("Running migration: add_alert_score_history ...")
    try:
        db.client.rpc("exec_sql", {"sql": _SQL}).execute()
        logger.info("  Table and indexes created via exec_sql RPC.")
    except Exception as rpc_err:
        logger.warning("exec_sql RPC not available (%s).", rpc_err)
        logger.info("")
        logger.info("═" * 60)
        logger.info("Ejecuta este SQL en el Supabase SQL editor:")
        logger.info("")
        for line in _SQL.strip().splitlines():
            logger.info("  %s", line)
        logger.info("")
        logger.info("Supabase dashboard → SQL editor → New query → Run")
        logger.info("═" * 60)
        sys.exit(1)
    logger.info("Migration complete.")


def verify(db: SupabaseClient) -> None:
    exists = _table_exists(db, _VERIFY_TABLE)
    logger.info("Table %s → %s", _VERIFY_TABLE, "OK" if exists else "MISSING")
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
