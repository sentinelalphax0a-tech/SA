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
    Resultado: 1.2M filas donde el factor de duplicación varía
    por wallet (algunos wallets tienen 20x, otros 1.1x).

  v3 Algoritmo (read-all-first, sin interferencia cursor/delete):
    1. Leer TODA la tabla en memoria (paginando por id ASC — PK,
       sin deletes concurrentes durante la lectura).
    2. En un solo pase cliente, agrupar por
       (wallet_address, sender_address, hop_level) y conservar
       el id máximo de cada grupo.
    3. Eliminar todos los ids duplicados en batches de 500
       (límite URL de httpx).

  Ventajas sobre v1/v2:
    - Cubre TODOS los wallet_address (incluidos intermedios hop-2)
    - Sin bugs de cursor/flush interaction
    - Garantizado idempotente: si no hay duplicados, no borra nada

Idempotente: sí — segunda ejecución no borra nada.
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

_DELETE_URL_BATCH = 500   # max ids per single DELETE HTTP request (URL length limit)
_PAGE_SIZE = 1_000


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
            return False
        logger.info(
            "Índice confirmado: get_funding_sources en %.3fs (%d rows para %s…)",
            elapsed, len(rows), test_addr[:10],
        )
        return True
    except Exception as e:
        logger.error("get_funding_sources falló — índice probablemente ausente: %s", e)
        return False


def _read_all_rows(db: SupabaseClient) -> list[dict]:
    """
    Read ALL rows from wallet_funding into memory.

    Pages through the table in ascending id order (cursor on PK id).
    Selects only the 4 fields needed for deduplication.
    No deletes happen during this read — safe for full accumulation.
    """
    all_rows: list[dict] = []
    last_id: int = 0
    page_num = 0

    while True:
        resp = (
            db.client.table("wallet_funding")
            .select("id,wallet_address,sender_address,hop_level")
            .gt("id", last_id)
            .order("id")
            .limit(_PAGE_SIZE)
            .execute()
        )
        page = resp.data or []
        page_num += 1
        if not page:
            break
        all_rows.extend(page)
        last_id = page[-1]["id"]

        if page_num % 100 == 0:
            logger.info("  [página %d] %d rows leídas...", page_num, len(all_rows))

    logger.info("  Total leído: %d rows en %d páginas", len(all_rows), page_num)
    return all_rows


def _delete_in_batches(db: SupabaseClient, ids: list[int]) -> int:
    """DELETE wallet_funding rows by id, splitting into URL-safe chunks."""
    deleted = 0
    for i in range(0, len(ids), _DELETE_URL_BATCH):
        batch = ids[i : i + _DELETE_URL_BATCH]
        db.client.table("wallet_funding").delete().in_("id", batch).execute()
        deleted += len(batch)
        if (i // _DELETE_URL_BATCH) % 20 == 0 and i > 0:
            logger.info("  ... %d/%d eliminados", deleted, len(ids))
    return deleted


def purge(db: SupabaseClient) -> dict:
    """
    Run the full deduplication purge (v3 — read-all-first strategy).

    Reads the entire wallet_funding table into memory first, then identifies
    all duplicate ids in a single O(n) client-side pass, then deletes them.
    No concurrent reads/deletes — guaranteed to find all duplicates in one run.

    Returns stats dict.
    """
    if not _verify_index_exists(db):
        sys.exit(1)

    pre_resp = db.client.table("wallet_funding").select("*", count="exact").limit(1).execute()
    pre_count = pre_resp.count
    logger.info("")
    logger.info("=== PURGA DE DUPLICADOS wallet_funding (v3 — read-all-first) ===")
    logger.info("Filas antes: %d", pre_count)
    logger.info("")

    t_start = time.time()

    # Step 1: Read ALL rows into memory (ordered by PK id, no concurrent deletes)
    logger.info("Paso 1/3: Leyendo tabla completa en memoria...")
    all_rows = _read_all_rows(db)
    t_read = time.time() - t_start
    logger.info("  Completado en %.1fs", t_read)
    logger.info("")

    if not all_rows:
        logger.info("Tabla vacía — nada que purgar.")
        return {"pre_count": pre_count, "post_count": pre_count, "deleted": 0,
                "wallets_processed": 0, "wallets_with_dupes": 0}

    # Step 2: Find all duplicate ids in one O(n) client-side pass
    logger.info("Paso 2/3: Identificando duplicados...")
    best: dict[tuple, int] = {}
    for row in all_rows:
        key = (row["wallet_address"], row["sender_address"], row["hop_level"])
        row_id = row["id"]
        if key not in best or row_id > best[key]:
            best[key] = row_id

    unique_count = len(best)
    keep_ids = set(best.values())
    delete_ids = [row["id"] for row in all_rows if row["id"] not in keep_ids]
    wallets_processed = len({row["wallet_address"] for row in all_rows})
    wallets_with_dupes = len({row["wallet_address"] for row in all_rows
                               if row["id"] not in keep_ids})

    logger.info("  Filas totales:  %d", len(all_rows))
    logger.info("  Claves únicas:  %d", unique_count)
    logger.info("  A eliminar:     %d", len(delete_ids))
    logger.info("  Wallets únicos: %d", wallets_processed)
    logger.info("  Con dupes:      %d", wallets_with_dupes)
    logger.info("")

    if not delete_ids:
        post_count = pre_count
        logger.info("Sin duplicados — tabla ya limpia.")
        logger.info("Filas actuales: %d", post_count)
        return {"pre_count": pre_count, "post_count": post_count, "deleted": 0,
                "wallets_processed": wallets_processed, "wallets_with_dupes": 0}

    # Step 3: Delete all duplicates in URL-safe batches
    logger.info("Paso 3/3: Eliminando %d duplicados en batches de %d...",
                len(delete_ids), _DELETE_URL_BATCH)
    total_deleted = _delete_in_batches(db, delete_ids)
    t_total = time.time() - t_start

    post_resp = db.client.table("wallet_funding").select("*", count="exact").limit(1).execute()
    post_count = post_resp.count

    logger.info("")
    logger.info("=== RESULTADO ===")
    logger.info("Filas antes:    %d", pre_count)
    logger.info("Filas después:  %d", post_count)
    logger.info("Filas borradas: %d", pre_count - post_count)
    logger.info("Reducción:      %.1f%%",
                (1 - post_count / pre_count) * 100 if pre_count > 0 else 0)
    logger.info("Wallets únicos: %d", wallets_processed)
    logger.info("Con dupes:      %d", wallets_with_dupes)
    logger.info("Tiempo total:   %.1fs", t_total)
    logger.info("")

    return {
        "pre_count": pre_count,
        "post_count": post_count,
        "deleted": pre_count - post_count,
        "wallets_processed": wallets_processed,
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
            "Puede haber wallets con muchos senders únicos.",
            resp.count,
        )
    else:
        logger.info("  ✓ Dentro del rango esperado")

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
                logger.warning(
                    "  ✗ Aún hay duplicados (%d rows, %d keys únicos)",
                    len(rows), len(keys),
                )


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient()
    stats = purge(db)

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
