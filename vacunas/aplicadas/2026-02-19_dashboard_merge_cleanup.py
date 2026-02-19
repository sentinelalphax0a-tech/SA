"""
============================================================
VACUNA: Dashboard cleanup — marcar is_secondary alertas con N12 y star_level<=1
Fecha: 2026-02-19
Bug que corrige: Alertas que fueron penalizadas por N12 (merge sospechoso,
  -40 pts) y cayeron a star_level<=1 siguen apareciendo en el dashboard como
  alertas primarias. Deben marcarse is_secondary=True para que el dashboard
  las oculte del feed principal (ya existe el gate 0★ en main.py para futuros
  casos, pero no es retroactivo).

  Criterio de selección:
    - outcome = 'pending'
    - star_level <= 1
    - filters_triggered contiene un filtro con filter_id = 'N12'
    - is_secondary = False (no marcado aún)

Tablas afectadas: alerts
Filas estimadas a modificar: <20 (solo alertas recientes con N12)
Reversible: SI — is_secondary=False es el estado previo
Aplicada en producción: NO
Commit que introdujo el fix en código: (ver git log FILTER_N12)
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-19 02:03 UTC
  - Filas modificadas: 0
  - Observaciones: 0 filas. N12 no existía en alertas históricas —
    no hay candidatos. Futuros casos quedan cubiertos por el gate 0★ en main.py.
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
DRY_RUN = True

PAGE_SIZE = 500


def _has_n12(filters_triggered) -> bool:
    """Check if filters_triggered list contains an N12 filter."""
    if not filters_triggered:
        return False
    return any(
        f.get("filter_id") == "N12"
        for f in filters_triggered
        if isinstance(f, dict)
    )


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Busca alertas pending con star_level<=1 que tienen N12 en filters_triggered
    y aún no están marcadas is_secondary=True.
    """
    logger.info("[DIAGNÓSTICO] Buscando alertas con N12 y star_level<=1...")

    affected = []
    offset = 0

    while True:
        rows = (
            db.client.table("alerts")
            .select("id,star_level,is_secondary,filters_triggered,market_question,direction,score")
            .eq("outcome", "pending")
            .eq("is_secondary", False)
            .lte("star_level", 1)
            .not_.is_("filters_triggered", "null")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        ) or []

        if not rows:
            break

        for row in rows:
            if _has_n12(row.get("filters_triggered")):
                logger.info(
                    "  Afectada: alert #%s | %d★ | score=%s | %s %s",
                    row["id"],
                    row.get("star_level") or 0,
                    row.get("score", "?"),
                    row.get("direction", "?"),
                    (row.get("market_question") or "?")[:50],
                )
                affected.append(row)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info(
        "[DIAGNÓSTICO] Alertas con N12 y star_level<=1 (is_secondary=False): %d",
        len(affected),
    )
    return affected


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """Marca is_secondary=True en alertas afectadas."""
    logger.info(
        "[CORRECCIÓN] %s modo. %d filas a procesar.",
        "DRY-RUN" if DRY_RUN else "LIVE", len(filas),
    )
    modificadas = 0

    for fila in filas:
        alert_id = fila["id"]
        if DRY_RUN:
            logger.info(
                "  DRY-RUN: actualizaría alert #%s → is_secondary=True "
                "(star_level=%s, score=%s)",
                alert_id, fila.get("star_level"), fila.get("score"),
            )
        else:
            db.client.table("alerts").update(
                {"is_secondary": True}
            ).eq("id", alert_id).execute()
            modificadas += 1
            logger.info("  ACTUALIZADO: alert #%s is_secondary=True", alert_id)

    if DRY_RUN:
        logger.info(
            "[CORRECCIÓN] DRY-RUN completado. Filas que se modificarían: %d", len(filas)
        )
    else:
        logger.info("[CORRECCIÓN] Completado. Filas modificadas: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    """
    Confirma que no quedan alertas pending con N12, star_level<=1 e is_secondary=False.
    """
    logger.info("[VERIFICACIÓN] Comprobando que no quedan alertas afectadas...")

    rows = (
        db.client.table("alerts")
        .select("id,star_level,is_secondary,filters_triggered")
        .eq("outcome", "pending")
        .eq("is_secondary", False)
        .lte("star_level", 1)
        .not_.is_("filters_triggered", "null")
        .execute()
        .data
    ) or []

    remaining = [r for r in rows if _has_n12(r.get("filters_triggered"))]
    if remaining:
        logger.warning(
            "[VERIFICACIÓN] Aún quedan %d alertas afectadas. Revisar.", len(remaining)
        )
    else:
        logger.info("[VERIFICACIÓN] OK — ninguna alerta afectada restante.")


# ── ROLLBACK ──────────────────────────────────────────────
def rollback(db: SupabaseClient, alert_ids: list[int]) -> None:
    """Revertir: marcar is_secondary=False en las alertas modificadas."""
    for alert_id in alert_ids:
        db.client.table("alerts").update(
            {"is_secondary": False}
        ).eq("id", alert_id).execute()
        logger.info("  ROLLBACK: alert #%s → is_secondary=False", alert_id)


# ── Entry point ───────────────────────────────────────────
def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas en el entorno.")
        sys.exit(1)

    db = SupabaseClient()

    # 1. Diagnóstico
    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron filas afectadas. Nada que corregir.")
        return

    logger.info("Filas afectadas encontradas: %d", len(filas))

    # 2. Confirmación
    if not DRY_RUN:
        resp = input(
            f"\n¿Marcar is_secondary=True en {len(filas)} alertas en PRODUCCIÓN? [s/N] "
        ).strip().lower()
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
