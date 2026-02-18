"""
============================================================
VACUNA: resolver_clob_api
Fecha: 2026-02-15
Bug que corrige: El endpoint GET /markets de la Gamma API ignora el parámetro
  conditionId y siempre retorna el primer mercado de su DB (mercado Biden 2020).
  El resolver comparaba outcomes contra ese mercado incorrecto.
  94 alertas marcadas como correct/incorrect con datos del mercado equivocado.
Tablas afectadas: alerts
Filas estimadas a modificar: 94
Reversible: SI — se guarda el outcome original antes de sobrescribir
Aplicada en producción: SI
Commit que introdujo el fix en código: 2d1c56a
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-15
  - Filas modificadas: 94
  - Observaciones: Se reseteó outcome a NULL en las 94 alertas afectadas.
    El resolver (ya con CLOB API) las re-procesó en el siguiente run diario
    (resolver.yml 08:00 UTC 2026-02-16).
"""

import logging
import os
import sys
from datetime import datetime, timezone

from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DRY_RUN = True

# El mercado Biden 2020 que la Gamma API retornaba erróneamente
BOGUS_MARKET_CONDITION_ID = "0x4d6b5d5b6a7d8e9f0a1b2c3d4e5f6a7b"  # placeholder

# Periodo de ejecución del resolver roto (antes del fix 2026-02-15)
# Alertas resueltas ANTES de esta fecha pueden estar afectadas
FIX_DATE = datetime(2026, 2, 15, tzinfo=timezone.utc)

PAGE_SIZE = 500


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Detecta alertas cuyo outcome fue asignado por el resolver roto.
    Criterio: outcome != NULL y resolved_at < FIX_DATE.
    (El resolver roto operó desde el inicio hasta el fix del 2026-02-15.)
    """
    logger.info("[DIAGNÓSTICO] Buscando alertas resueltas antes del fix CLOB API...")

    afectadas = []
    offset = 0

    while True:
        rows = (
            db.client.table("alerts")
            .select("id, market_id, condition_id, outcome, resolved_at, star_level")
            .not_.is_("outcome", "null")
            .lt("resolved_at", FIX_DATE.isoformat())
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        if not rows:
            break
        afectadas.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Alertas con outcome potencialmente incorrecto: %d", len(afectadas))
    for row in afectadas[:5]:
        logger.info("  id=%s market=%s outcome=%s resolved_at=%s",
                    row["id"], row.get("market_id", "?")[:20],
                    row.get("outcome"), row.get("resolved_at"))
    if len(afectadas) > 5:
        logger.info("  ... y %d más.", len(afectadas) - 5)

    return afectadas


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """
    Resetea outcome → NULL y resolved_at → NULL para que el resolver
    (ya con CLOB API) las re-procese en el siguiente run.
    Guarda el outcome original en el campo 'notes' de la alerta como backup.
    """
    logger.info("[CORRECCIÓN] %s modo. %d alertas a resetear.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas))
    modificadas = 0

    for fila in filas:
        backup_note = f"[vacuna 2026-02-15] outcome_original={fila.get('outcome')} resolved_at_original={fila.get('resolved_at')}"

        if DRY_RUN:
            logger.info("  DRY-RUN id=%s: outcome %s → NULL", fila["id"], fila.get("outcome"))
        else:
            db.client.table("alerts").update({
                "outcome": None,
                "resolved_at": None,
                "notes": backup_note,
            }).eq("id", fila["id"]).execute()
            modificadas += 1
            logger.info("  RESETEADO id=%s (backup: %s)", fila["id"], backup_note)

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se resetearían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas reseteadas: %d", modificadas)
        logger.info("  El resolver (CLOB API) las re-procesará en el próximo run de resolver.yml.")

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    """Confirma que las alertas afectadas tienen outcome=NULL (pendientes de re-resolver)."""
    logger.info("[VERIFICACIÓN] Comprobando que las alertas fueron reseteadas...")

    rows = (
        db.client.table("alerts")
        .select("id, outcome")
        .not_.is_("outcome", "null")
        .lt("resolved_at", FIX_DATE.isoformat())
        .execute()
        .data
    )

    if rows:
        logger.warning("Aún quedan %d alertas con outcome != NULL antes del fix. Revisar.", len(rows))
    else:
        logger.info("OK — Todas las alertas pre-fix tienen outcome=NULL. Listas para re-resolver.")


def rollback(db: SupabaseClient, filas: list[dict]) -> None:
    """
    Restaura el outcome original desde el campo 'notes'.
    Solo usar si el resolver vuelve a fallar tras la corrección.
    """
    logger.info("[ROLLBACK] Restaurando outcomes originales desde campo 'notes'...")
    for fila in filas:
        # El backup está en 'notes' como: "[vacuna 2026-02-15] outcome_original=X ..."
        notes = fila.get("notes", "")
        if "outcome_original=" in notes:
            original = notes.split("outcome_original=")[1].split(" ")[0]
            db.client.table("alerts").update({
                "outcome": original,
                "notes": None,
            }).eq("id", fila["id"]).execute()
            logger.info("  REVERTIDO id=%s → outcome=%s", fila["id"], original)


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient(supabase_url, supabase_key)

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron alertas afectadas.")
        return

    if not DRY_RUN:
        resp = input(f"\n¿Resetear outcome de {len(filas)} alertas en PRODUCCIÓN? [s/N] ").strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, filas)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
