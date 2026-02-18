"""
============================================================
VACUNA: rate_limiting_deep_scan
Fecha: 2026-02-16
Bug que corrige: El modo deep scan con asyncio puro generaba errores 429
  (Too Many Requests) de las APIs de Polymarket al procesar muchos mercados
  concurrentes. Los errores eran silenciosos o propagados, dejando mercados
  sin analizar y scans marcados como completos cuando no lo eran.
Tablas afectadas: scans (registros con status=error o incomplete)
Filas estimadas a modificar: scans fallidos antes del fix
Reversible: NO aplica — los scans incompletos no se pueden recuperar retroactivamente.
  Este script solo marca los scans afectados para contexto estadístico.
Aplicada en producción: SI
Commit que introdujo el fix en código: 5713198
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-16
  - Filas modificadas: scans en ventana afectada marcados con nota
  - Observaciones: Los datos de trades de los scans afectados no son recuperables.
    El sistema de deduplicación de 24h en scans posteriores capturó la mayoría
    de las señales perdidas. Scans marcados como 'deep' con status='error'
    en la ventana pre-fix son los únicos potencialmente incompletos.
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

# Fecha del fix de rate limiting
FIX_DATE = datetime(2026, 2, 16, tzinfo=timezone.utc)

# El deep scan empezó a ser problemático desde que se añadió asyncio paralelo
# (estimado: inicio de enero 2026)
DEEP_SCAN_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

PAGE_SIZE = 200


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Detecta scans de tipo 'deep' con status='error' o duración anómala
    ejecutados antes del fix de rate limiting.
    """
    logger.info("[DIAGNÓSTICO] Buscando deep scans fallidos antes del fix...")

    afectados = []
    offset = 0

    while True:
        # Scans con error explícito
        rows = (
            db.client.table("scans")
            .select("id, scan_type, status, started_at, markets_scanned, error_message")
            .eq("scan_type", "deep")
            .eq("status", "error")
            .gte("started_at", DEEP_SCAN_START.isoformat())
            .lt("started_at", FIX_DATE.isoformat())
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        if not rows:
            break
        afectados.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Deep scans con error en ventana pre-fix: %d", len(afectados))

    # Scans 'completados' pero con pocos mercados (posible 429 silencioso)
    offset = 0
    sospechosos = []

    while True:
        rows = (
            db.client.table("scans")
            .select("id, scan_type, status, started_at, markets_scanned")
            .eq("scan_type", "deep")
            .eq("status", "completed")
            .lt("markets_scanned", 100)  # deep scan normal analiza 450+ mercados
            .gte("started_at", DEEP_SCAN_START.isoformat())
            .lt("started_at", FIX_DATE.isoformat())
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        if not rows:
            break
        sospechosos.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Deep scans 'completados' con <100 mercados (sospechosos): %d", len(sospechosos))

    total = afectados + sospechosos
    logger.info("[DIAGNÓSTICO] Total de scans afectados/sospechosos: %d", len(total))
    return total


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """
    Marca los scans afectados con una nota indicando que pueden estar incompletos
    debido al bug de rate limiting. No modifica status ni datos de análisis.
    """
    logger.info("[CORRECCIÓN] %s modo. %d scans a marcar.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas))
    modificadas = 0

    for scan in filas:
        nota = (
            f"[vacuna 2026-02-16] Scan posiblemente incompleto por rate limiting (429 pre-fix 5713198). "
            f"status_original={scan.get('status')} markets_scanned={scan.get('markets_scanned')}."
        )

        if DRY_RUN:
            logger.info(
                "  DRY-RUN id=%s type=%s status=%s markets=%s",
                scan["id"], scan.get("scan_type"), scan.get("status"), scan.get("markets_scanned"),
            )
        else:
            db.client.table("scans").update({
                "error_message": nota,
            }).eq("id", scan["id"]).execute()
            modificadas += 1

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Scans que se marcarían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Scans marcados: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    """
    Confirma que no hay deep scans recientes (post-fix) con status=error
    por rate limiting.
    """
    logger.info("[VERIFICACIÓN] Comprobando scans post-fix...")

    rows = (
        db.client.table("scans")
        .select("id, status, error_message, started_at")
        .eq("scan_type", "deep")
        .eq("status", "error")
        .gte("started_at", FIX_DATE.isoformat())
        .execute()
        .data
    )

    rate_limit_errors = [
        r for r in rows
        if "429" in (r.get("error_message") or "") or "rate" in (r.get("error_message") or "").lower()
    ]

    if rate_limit_errors:
        logger.warning("Hay %d scans post-fix con errores de rate limiting. El fix puede no ser efectivo.", len(rate_limit_errors))
    else:
        logger.info("OK — No hay errores de rate limiting en scans post-fix.")


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient(supabase_url, supabase_key)

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron scans afectados.")
        return

    if not DRY_RUN:
        resp = input(f"\n¿Marcar {len(filas)} scans con nota de auditoría? [s/N] ").strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, filas)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
