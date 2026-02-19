"""
============================================================
VACUNA: odds_boundary_pending
Fecha: 2026-02-19
Bug que corrige: 1.276 alertas con outcome='pending' donde
  odds_max=1.0 o odds_min=1.0 (precio direction-adjusted en
  certeza absoluta). El resolver normal requiere que la API de
  Polymarket devuelva closed=True + winner flag, pero para estos
  mercados la API aún devuelve active=True aunque el precio ya
  resolvió. El alert_tracker actualizó odds_max/min correctamente
  pero el resolver no puede actuar sin confirmación API.
  Fix en código: _resolve_by_price() en resolver.py (este commit).
  Esta vacuna resuelve retroactivamente el histórico acumulado.
  Lógica: odds direction-adjusted = 1.0 → dirección ganó → correct.
    direction=YES + odds_max=1.0 → YES ganó → correct
    direction=NO  + odds_max=1.0 → NO-adj=1.0 → YES_price=0 → NO ganó → correct
  outcome = "correct" en todos los casos (odds=1.0 = dirección ganó).
  actual_return = ((1.0 - odds_at_alert_adj) / odds_at_alert_adj) × 100
  resolved_at = odds_max_date (o odds_min_date, o now si no disponible)
Tablas afectadas: alerts
Filas estimadas a modificar: ~638 alertas únicas (1.276 registros en
  health_check incluyen duplicados por alert_id con ambos campos a 1.0)
Reversible: NO — actualización in-place. Valores originales se pierden.
Aplicada en producción: SI
Commit con fix de código: este commit
============================================================

Resultado de ejecución:
  - Fecha ejecución: 2026-02-19
  - Filas modificadas: 436
  - Observaciones: 436 alertas → todas 'correct'. actual_return avg=+32.5%
    (min=+5.3%, max=+122.2%). Verificación OK (0 restantes).
"""

import logging
import os
import sys
from datetime import datetime, timezone

from dateutil import parser as dt_parser

from src import config
from src.database.supabase_client import SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DRY_RUN = False

PAGE_SIZE = 500


def _parse_ts(val) -> datetime | None:
    if val is None:
        return None
    try:
        if isinstance(val, str):
            val = dt_parser.parse(val)
        if isinstance(val, datetime):
            return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    except (TypeError, ValueError):
        pass
    return None


def _direction_adjust(odds: float, direction: str) -> float:
    return (1.0 - odds) if direction.upper() == "NO" else odds


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Encuentra alertas pending con odds_max=1.0 o odds_min=1.0.
    Para cada una calcula el outcome y actual_return que se aplicaría.
    """
    logger.info(
        "[DIAGNÓSTICO] Buscando alertas pending con odds_max=1.0 o odds_min=1.0..."
    )

    candidates = (
        db.client.table("alerts")
        .select(
            "id,direction,odds_at_alert,odds_max,odds_max_date,"
            "odds_min,odds_min_date,created_at,market_question,star_level"
        )
        .eq("outcome", "pending")
        .or_("odds_max.eq.1,odds_min.eq.1")
        .execute()
        .data
    ) or []

    logger.info("[DIAGNÓSTICO] Candidatas encontradas: %d", len(candidates))

    afectadas = []
    for row in candidates:
        direction = (row.get("direction") or "YES").upper()
        odds_at_alert = row.get("odds_at_alert") or 0
        odds_adj = _direction_adjust(odds_at_alert, direction)

        # Siempre correct: odds direction-adjusted en 1.0 = dirección ganó
        outcome = "correct"
        odds_at_resolution = 1.0

        actual_return = (
            round(((1.0 - odds_adj) / odds_adj) * 100, 2)
            if odds_adj > 0 else 0.0
        )

        # resolved_at: usar la fecha en que los odds llegaron a 1.0
        if row.get("odds_max") == 1.0 and row.get("odds_max_date"):
            resolved_at = _parse_ts(row["odds_max_date"])
        elif row.get("odds_min") == 1.0 and row.get("odds_min_date"):
            resolved_at = _parse_ts(row["odds_min_date"])
        else:
            resolved_at = datetime.now(timezone.utc)

        created_at = _parse_ts(row.get("created_at"))
        time_to_resolution = (
            (resolved_at - created_at).days
            if resolved_at and created_at else None
        )

        row["_outcome"] = outcome
        row["_actual_return"] = actual_return
        row["_resolved_at"] = resolved_at
        row["_time_to_resolution"] = time_to_resolution
        row["_odds_at_resolution"] = odds_at_resolution
        afectadas.append(row)

    # Estadísticas del dry-run
    if afectadas:
        returns = [r["_actual_return"] for r in afectadas]
        avg_ret = round(sum(returns) / len(returns), 1)
        min_ret = round(min(returns), 1)
        max_ret = round(max(returns), 1)
        logger.info(
            "[DIAGNÓSTICO] Resumen: %d alertas → todas 'correct' | "
            "actual_return avg=%.1f%% min=%.1f%% max=%.1f%%",
            len(afectadas), avg_ret, min_ret, max_ret,
        )
        # Mostrar muestra representativa
        for row in afectadas[:10]:
            logger.info(
                "  id=%s  dir=%s  star=%s  odds_at_alert=%.4f  "
                "actual_return=%.1f%%  resolved_at=%s  q=%.40s",
                row["id"],
                row.get("direction"),
                row.get("star_level"),
                row.get("odds_at_alert") or 0,
                row["_actual_return"],
                row["_resolved_at"].date() if row["_resolved_at"] else "?",
                row.get("market_question") or "",
            )
        if len(afectadas) > 10:
            logger.info("  ... +%d más", len(afectadas) - 10)

    return afectadas


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    logger.info(
        "[CORRECCIÓN] %s modo. %d alertas.",
        "DRY-RUN" if DRY_RUN else "LIVE", len(filas),
    )
    modificadas = 0

    for fila in filas:
        fields = {
            "outcome": fila["_outcome"],
            "resolved_at": fila["_resolved_at"].isoformat() if fila["_resolved_at"] else None,
            "odds_at_resolution": fila["_odds_at_resolution"],
            "actual_return": fila["_actual_return"],
        }
        if fila["_time_to_resolution"] is not None:
            fields["time_to_resolution_days"] = fila["_time_to_resolution"]

        if DRY_RUN:
            if modificadas < 5:
                logger.info(
                    "  DRY-RUN id=%s: outcome=%s actual_return=%.1f%% "
                    "resolved_at=%s time_to_res=%s días",
                    fila["id"], fields["outcome"], fields["actual_return"],
                    fields.get("resolved_at", "?")[:10] if fields.get("resolved_at") else "?",
                    fields.get("time_to_resolution_days", "?"),
                )
        else:
            db.client.table("alerts").update(fields).eq("id", fila["id"]).execute()
            modificadas += 1

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN. Alertas que se modificarían: %d", len(filas))
    else:
        logger.info("[CORRECCIÓN] Completado. Alertas corregidas: %d", modificadas)

    return modificadas if not DRY_RUN else len(filas)


def verificacion(db: SupabaseClient) -> None:
    logger.info("[VERIFICACIÓN] Comprobando que no quedan odds_boundary_pending...")
    restantes = (
        db.client.table("alerts")
        .select("id", count="exact")
        .eq("outcome", "pending")
        .or_("odds_max.eq.1,odds_min.eq.1")
        .execute()
    )
    n = getattr(restantes, "count", None) or len(restantes.data or [])
    if n:
        logger.warning("Aún quedan %d alertas con odds=1.0 y outcome=pending.", n)
    else:
        logger.info("OK — No quedan alertas con odds=1.0 y outcome=pending.")


def main() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar definidas.")
        sys.exit(1)

    db = SupabaseClient()

    filas = diagnostico(db)
    if not filas:
        logger.info("No se encontraron alertas afectadas. Nada que hacer.")
        return

    if not DRY_RUN:
        resp = input(
            f"\n¿Resolver {len(filas)} alertas como 'correct' en PRODUCCIÓN? [s/N] "
        ).strip().lower()
        if resp != "s":
            logger.info("Cancelado.")
            sys.exit(0)

    correccion(db, filas)

    if not DRY_RUN:
        verificacion(db)


if __name__ == "__main__":
    main()
