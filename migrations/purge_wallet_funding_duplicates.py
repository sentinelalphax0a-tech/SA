"""
============================================================
MIGRATION: purge_wallet_funding_duplicates
Fecha: 2026-02-24
Descripción:
  Elimina filas duplicadas de wallet_funding conservando solo
  la más reciente (id máximo) por cada triplete único
  (wallet_address, sender_address, hop_level).

  Contexto:
    insert_funding_batch() hacía upsert sin on_conflict → cada
    deep scan re-insertaba todas las relaciones de funding.
    Resultado: 1.2M filas donde ~58k son registros únicos reales.
    Factor de duplicación: ~20x.

  Este script requiere que idx_wallet_funding_wallet_address
  exista ANTES de ejecutarse. Sin el índice, get_funding_sources()
  haría sequential scan de 1.2M filas → timeout.

  Algoritmo:
    Por cada wallet en la tabla wallets (3877 wallets):
      1. get_funding_sources(addr) → rápido con índice (<5ms)
      2. Agrupar por (sender_address, hop_level) client-side
      3. Conservar el id máximo de cada grupo
      4. DELETE WHERE id IN (ids a eliminar)
    Deletes en batches de 10,000 IDs para evitar timeouts.

  Snapshot pre-purga: 1,201,122 filas.
  Resultado esperado: ~50,000-80,000 filas únicas.

Idempotente: sí — segunda ejecución no borra nada (ya no hay duplicados).
Ejecutar DESPUÉS de: migrations.add_wallet_funding_indexes (fase 1)
Ejecutar ANTES de:  CREATE UNIQUE INDEX idx_wallet_funding_unique
Ejecutar: python -m migrations.purge_wallet_funding_duplicates
============================================================
"""

import logging
import os
import sys
import time

from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

_DELETE_BATCH_SIZE = 10_000
_WALLET_PAGE_SIZE = 1_000


def _verify_index_exists(db: SupabaseClient) -> bool:
    """Check that the wallet_address index is active by timing a query."""
    resp = db.client.table("wallet_funding").select("wallet_address").limit(1).execute()
    if not resp.data:
        logger.info("wallet_funding está vacía — nada que purgar")
        return False

    test_addr = resp.data[0]["wallet_address"]
    t0 = time.time()
    try:
        rows = db.get_funding_sources(test_addr)
        elapsed = time.time() - t0
        if elapsed > 5.0:
            logger.error(
                "get_funding_sources tardó %.1fs — índice wallet_address NO existe.",
                elapsed,
            )
            logger.error("Ejecuta primero: python -m migrations.add_wallet_funding_indexes")
            logger.error("Y crea los índices de FASE 1 en el SQL Editor.")
            return False
        logger.info(
            "Índice confirmado: get_funding_sources en %.3fs (%d rows para %s…)",
            elapsed, len(rows), test_addr[:10],
        )
        return True
    except Exception as e:
        logger.error("get_funding_sources falló — índice probablemente ausente: %s", e)
        return False


def _get_all_wallet_addresses(db: SupabaseClient) -> list[str]:
    """Paginate wallets table to get all distinct addresses."""
    addresses = []
    offset = 0
    while True:
        resp = (
            db.client.table("wallets")
            .select("address")
            .range(offset, offset + _WALLET_PAGE_SIZE - 1)
            .execute()
        )
        batch = resp.data or []
        addresses.extend(r["address"] for r in batch)
        if len(batch) < _WALLET_PAGE_SIZE:
            break
        offset += _WALLET_PAGE_SIZE
    return addresses


def _find_duplicate_ids(rows: list[dict]) -> list[int]:
    """
    Given all wallet_funding rows for a single wallet, return the IDs
    to DELETE (all duplicates, keeping the highest id per unique
    (sender_address, hop_level) pair).
    """
    # Group by (sender_address, hop_level) → keep max id
    best: dict[tuple, int] = {}
    for row in rows:
        key = (row["sender_address"], row["hop_level"])
        row_id = row["id"]
        if key not in best or row_id > best[key]:
            best[key] = row_id

    keep_ids = set(best.values())
    return [row["id"] for row in rows if row["id"] not in keep_ids]


def _delete_in_batches(db: SupabaseClient, ids: list[int]) -> int:
    """DELETE wallet_funding rows by id in batches. Returns count deleted."""
    deleted = 0
    for i in range(0, len(ids), _DELETE_BATCH_SIZE):
        batch = ids[i : i + _DELETE_BATCH_SIZE]
        db.client.table("wallet_funding").delete().in_("id", batch).execute()
        deleted += len(batch)
    return deleted


def purge(db: SupabaseClient) -> dict:
    """Run the full deduplication purge. Returns stats dict."""
    # Pre-flight: verify index exists
    if not _verify_index_exists(db):
        sys.exit(1)

    # Pre-purge count
    pre_resp = db.client.table("wallet_funding").select("*", count="exact").limit(1).execute()
    pre_count = pre_resp.count
    logger.info("")
    logger.info("=== PURGA DE DUPLICADOS wallet_funding ===")
    logger.info("Filas antes: %d", pre_count)
    logger.info("")

    # Get all wallet addresses to process
    logger.info("Obteniendo lista de wallets...")
    addresses = _get_all_wallet_addresses(db)
    logger.info("Wallets a procesar: %d", len(addresses))
    logger.info("")

    total_deleted = 0
    total_kept = 0
    wallets_with_dupes = 0
    t_start = time.time()

    for i, addr in enumerate(addresses, 1):
        # Fetch all funding rows for this wallet (fast with index)
        try:
            rows = db.get_funding_sources(addr)
        except Exception as e:
            logger.warning("get_funding_sources falló para %s…: %s", addr[:10], e)
            continue

        if not rows:
            continue

        # Find duplicates
        ids_to_delete = _find_duplicate_ids(rows)
        unique_count = len(rows) - len(ids_to_delete)
        total_kept += unique_count

        if ids_to_delete:
            wallets_with_dupes += 1
            deleted = _delete_in_batches(db, ids_to_delete)
            total_deleted += deleted

        # Progress every 100 wallets
        if i % 100 == 0 or i == len(addresses):
            elapsed = time.time() - t_start
            rate = total_deleted / elapsed if elapsed > 0 else 0
            logger.info(
                "  [%d/%d] %d wallets con dupes | %d borradas | %d retenidas | %.0f filas/s",
                i, len(addresses),
                wallets_with_dupes, total_deleted, total_kept, rate,
            )

    # Post-purge count
    post_resp = db.client.table("wallet_funding").select("*", count="exact").limit(1).execute()
    post_count = post_resp.count

    logger.info("")
    logger.info("=== RESULTADO ===")
    logger.info("Filas antes:    %d", pre_count)
    logger.info("Filas después:  %d", post_count)
    logger.info("Filas borradas: %d", pre_count - post_count)
    logger.info("Reducción:      %.1f%%", (1 - post_count / pre_count) * 100 if pre_count > 0 else 0)
    logger.info("Tiempo total:   %.1fs", time.time() - t_start)
    logger.info("")

    return {
        "pre_count": pre_count,
        "post_count": post_count,
        "deleted": pre_count - post_count,
        "wallets_processed": len(addresses),
        "wallets_with_dupes": wallets_with_dupes,
    }


def verify(db: SupabaseClient, sample_addr: str | None = None) -> None:
    """Verify purge result with count and a spot-check on a real wallet."""
    logger.info("=== Verificación post-purga ===")

    resp = db.client.table("wallet_funding").select("*", count="exact").limit(1).execute()
    logger.info("Filas actuales: %d", resp.count)

    if resp.count > 200_000:
        logger.warning(
            "Quedan %d filas — más de las ~60-80k esperadas. "
            "Revisa si hay wallets en wallet_funding no registradas en wallets.",
            resp.count,
        )

    # Spot-check: a real wallet should have data
    if sample_addr:
        rows = db.get_funding_sources(sample_addr)
        logger.info(
            "Spot-check %s…: %d rows (esperado: 1 por cada par sender/hop único)",
            sample_addr[:10], len(rows),
        )
        if rows:
            keys = {(r["sender_address"], r["hop_level"]) for r in rows}
            if len(keys) == len(rows):
                logger.info("  ✓ Sin duplicados en este wallet")
            else:
                logger.warning("  ✗ Aún hay duplicados para este wallet (%d rows, %d keys únicos)", len(rows), len(keys))


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient()
    stats = purge(db)

    # Spot-check con la primera wallet que tiene datos
    resp = db.client.table("wallet_funding").select("wallet_address").limit(1).execute()
    sample = resp.data[0]["wallet_address"] if resp.data else None
    verify(db, sample_addr=sample)

    logger.info("")
    logger.info("SIGUIENTE PASO:")
    logger.info("  Ejecuta en Supabase SQL Editor:")
    logger.info("")
    logger.info("  CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_wallet_funding_unique")
    logger.info("  ON wallet_funding (wallet_address, sender_address, hop_level);")
    logger.info("")
    logger.info("  Luego el código de insert_funding_batch ya usa on_conflict.")


if __name__ == "__main__":
    main()
