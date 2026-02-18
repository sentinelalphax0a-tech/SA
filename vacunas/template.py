"""
============================================================
VACUNA: [nombre_descriptivo]
Fecha: [YYYY-MM-DD]
Bug que corrige: [descripción del bug]
Tablas afectadas: [alerts, market_history, etc.]
Filas estimadas a modificar: [N]
Reversible: [SI/NO] — si SI, incluir lógica de rollback abajo
Aplicada en producción: NO
Commit que introdujo el fix en código: [hash]
============================================================

Resultado de ejecución:
  - Fecha ejecución: [YYYY-MM-DD HH:MM UTC]
  - Filas modificadas: [N]
  - Observaciones: [ninguna / detalles]
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

# ── Configuración ─────────────────────────────────────────
# Cambiar a False solo después de revisar el diagnóstico.
DRY_RUN = True

PAGE_SIZE = 500


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    SELECT que muestra el problema ANTES de corregir.
    Debe ser idempotente y solo leer.
    Retorna la lista de registros afectados.
    """
    logger.info("[DIAGNÓSTICO] Buscando registros afectados...")

    # TODO: implementar query de diagnóstico
    # Ejemplo:
    # rows = db.client.table("alerts").select("*").eq("campo", "valor").execute().data
    # return rows
    raise NotImplementedError("Implementar diagnóstico")


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """
    Aplica la corrección a los registros encontrados en diagnóstico().
    En DRY_RUN=True solo imprime lo que haría, sin modificar la DB.
    Retorna el número de filas modificadas.
    """
    logger.info("[CORRECCIÓN] %s modo. %d filas a procesar.", "DRY-RUN" if DRY_RUN else "LIVE", len(filas))
    modificadas = 0

    for fila in filas:
        # TODO: calcular el valor correcto y construir el update
        nuevo_valor = None  # reemplazar con lógica real

        if DRY_RUN:
            logger.info("  DRY-RUN: actualizaría id=%s → %s", fila.get("id"), nuevo_valor)
        else:
            # db.client.table("alerts").update({...}).eq("id", fila["id"]).execute()
            modificadas += 1
            logger.info("  ACTUALIZADO: id=%s", fila.get("id"))

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN completado. Filas que se modificarían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Filas modificadas: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    """
    SELECT que confirma que el problema fue corregido.
    Debe retornar 0 filas si la corrección fue exitosa.
    """
    logger.info("[VERIFICACIÓN] Comprobando que no quedan registros afectados...")

    # TODO: ejecutar la misma query que diagnóstico() y verificar que retorna 0
    raise NotImplementedError("Implementar verificación")


# ── ROLLBACK (solo si Reversible=SI) ─────────────────────
def rollback(db: SupabaseClient) -> None:
    """
    Revertir los cambios aplicados por correccion().
    Solo implementar si Reversible=SI en la cabecera.
    """
    raise NotImplementedError("Este script no tiene rollback definido")


# ── Entry point ───────────────────────────────────────────
def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas en el entorno.")
        sys.exit(1)

    db = SupabaseClient(supabase_url, supabase_key)

    # 1. Diagnóstico
    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron filas afectadas. Nada que corregir.")
        return

    logger.info("Filas afectadas encontradas: %d", len(filas))

    # 2. Confirmación antes de corregir
    if not DRY_RUN:
        resp = input(f"\n¿Aplicar corrección sobre {len(filas)} filas en PRODUCCIÓN? [s/N] ").strip().lower()
        if resp != "s":
            logger.info("Operación cancelada por el operador.")
            sys.exit(0)

    # 3. Corrección
    correccion(db, filas)

    # 4. Verificación
    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
