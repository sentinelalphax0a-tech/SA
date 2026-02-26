"""
============================================================
MIGRATION: create_bot_trades
Fecha: 2026-02-26
Descripción:
  Crea la tabla bot_trades y añade campos de bot a alerts.

  bot_trades registra cada orden ejecutada (o simulada) por
  el bot. Tiene dos modos de operación:
    - paper_trade = TRUE  → shadow mode (simulación, sin dinero real)
    - paper_trade = FALSE → live mode (orden real en Polymarket)

  La relación es:
    alerts (1) ←──→ (N) bot_trades
  Un alert puede generar varias órdenes (reentradas, partial fills).
  bot_trades.alert_id es FK obligatoria; alerts.bot_trade_id apunta
  a la orden principal (la primera) para trazabilidad rápida.

  Flujo de estados de bot_trades.status:
    open → closed_win | closed_loss | cancelled | expired

Campos añadidos a alerts:
    bot_executed        BOOLEAN  — True si el bot ya ejecutó este alert
    bot_trade_id        BIGINT   — FK a bot_trades (la orden primaria)
    bot_stake           FLOAT    — stake en USD de la orden primaria
    bot_executed_at     TIMESTAMPTZ

Idempotente: sí — usa CREATE TABLE IF NOT EXISTS y ADD COLUMN IF NOT EXISTS.
Ejecutar: python -m migrations.create_bot_trades
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

# ── Step 1: Create bot_trades table ──────────────────────────────────────────
# alert_id is FK to alerts(id). alerts.bot_trade_id (step 2) closes the loop.
# CHECK constraints are enforced at DB level — safe defaults for the bot.
_SQL_CREATE_BOT_TRADES = """
CREATE TABLE IF NOT EXISTS bot_trades (
    id                  BIGSERIAL PRIMARY KEY,

    -- Link back to the alert that triggered this trade
    alert_id            BIGINT NOT NULL REFERENCES alerts(id) ON DELETE RESTRICT,

    -- Shadow mode: TRUE = paper trade (no real money), FALSE = live order
    -- Defaults to TRUE — the bot must explicitly opt in to live trading.
    paper_trade         BOOLEAN NOT NULL DEFAULT TRUE,

    -- Market context (denormalized for query convenience)
    market_id           TEXT NOT NULL,
    direction           TEXT NOT NULL CHECK (direction IN ('YES', 'NO')),

    -- Trade parameters
    entry_odds          FLOAT NOT NULL CHECK (entry_odds > 0 AND entry_odds < 1),
    stake               FLOAT NOT NULL CHECK (stake > 0),

    -- Lifecycle
    status              TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'closed_win', 'closed_loss',
                                              'cancelled', 'expired')),

    -- Resolution (NULL while open)
    pnl                 FLOAT,           -- absolute PnL in USD
    pnl_pct             FLOAT,           -- PnL as % of stake
    exit_odds           FLOAT,           -- odds at position close
    exit_reason         TEXT,            -- 'market_resolved' | 'manual' | 'stop_loss' | 'take_profit'

    -- Polymarket execution metadata (live mode only)
    polymarket_order_id TEXT,            -- order ID from Polymarket CLOB API

    -- Timestamps
    executed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Optional notes / debug info
    notes               TEXT
);
""".strip()

# ── Step 2: Indexes on bot_trades ─────────────────────────────────────────────
_SQL_INDEXES = [
    # Fast lookup by alert
    "CREATE INDEX IF NOT EXISTS bot_trades_alert_id_idx ON bot_trades(alert_id);",
    # Dashboard: all open positions (partial index — only open rows)
    "CREATE INDEX IF NOT EXISTS bot_trades_open_idx ON bot_trades(executed_at DESC) WHERE status = 'open';",
    # Filter paper vs live
    "CREATE INDEX IF NOT EXISTS bot_trades_paper_trade_idx ON bot_trades(paper_trade, status);",
    # Chronological queries
    "CREATE INDEX IF NOT EXISTS bot_trades_executed_at_idx ON bot_trades(executed_at DESC);",
]

# ── Step 3: Add bot tracking fields to alerts ─────────────────────────────────
# bot_trade_id references bot_trades(id). This FK is added AFTER bot_trades
# exists to avoid circular dependency issues during table creation.
_SQL_ALTER_ALERTS = [
    # Whether this alert has been acted on by the bot
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS bot_executed BOOLEAN DEFAULT FALSE;",
    # FK to bot_trades — the primary (first) order for this alert
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS bot_trade_id BIGINT REFERENCES bot_trades(id) ON DELETE SET NULL;",
    # Convenience copy of stake for reporting without joining bot_trades
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS bot_stake FLOAT;",
    # Timestamp of first bot execution
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS bot_executed_at TIMESTAMPTZ;",
]

# All SQL in execution order
_ALL_SQL: list[tuple[str, str]] = (
    [("create_bot_trades_table", _SQL_CREATE_BOT_TRADES)]
    + [("index", s) for s in _SQL_INDEXES]
    + [("alter_alerts", s) for s in _SQL_ALTER_ALERTS]
)


def _column_exists(db: SupabaseClient, table: str, column: str) -> bool:
    try:
        db.client.table(table).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def _table_exists(db: SupabaseClient, table: str) -> bool:
    try:
        db.client.table(table).select("id").limit(1).execute()
        return True
    except Exception:
        return False


def migrate(db: SupabaseClient) -> None:
    logger.info("Running migration: create_bot_trades (%d statements) ...", len(_ALL_SQL))
    try:
        for label, sql in _ALL_SQL:
            logger.info("  [%s] ...", label)
            db.client.rpc("exec_sql", {"sql": sql}).execute()
        logger.info("  All statements executed via exec_sql RPC.")
    except Exception as rpc_err:
        logger.warning("exec_sql RPC not available (%s).", rpc_err)
        logger.info("")
        logger.info("═" * 60)
        logger.info("Ejecuta este SQL en el Supabase SQL editor:")
        logger.info("")
        logger.info("-- ① Crear tabla bot_trades")
        logger.info("%s", _SQL_CREATE_BOT_TRADES)
        logger.info("")
        logger.info("-- ② Indexes")
        for s in _SQL_INDEXES:
            logger.info("%s", s)
        logger.info("")
        logger.info("-- ③ Añadir campos a alerts")
        for s in _SQL_ALTER_ALERTS:
            logger.info("%s", s)
        logger.info("")
        logger.info("Supabase dashboard → SQL editor → New query → Run")
        logger.info("═" * 60)
        sys.exit(1)

    logger.info("Migration complete.")


def verify(db: SupabaseClient) -> None:
    ok = True

    # Table exists?
    if _table_exists(db, "bot_trades"):
        logger.info("bot_trades table         → OK")
    else:
        logger.error("bot_trades table         → MISSING")
        ok = False

    # Alerts columns
    for col in ("bot_executed", "bot_trade_id", "bot_stake", "bot_executed_at"):
        exists = _column_exists(db, "alerts", col)
        logger.info("alerts.%-20s → %s", col, "OK" if exists else "MISSING")
        if not exists:
            ok = False

    if not ok:
        logger.error("Verification FAILED — run migration first.")
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
