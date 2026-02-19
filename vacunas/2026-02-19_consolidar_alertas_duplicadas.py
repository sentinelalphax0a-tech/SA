"""
============================================================
VACUNA: Consolidar alertas duplicadas por (market_id, direction)
Fecha: 2026-02-19
Bug que corrige:
  El mecanismo _try_consolidate() en main.py solo actúa sobre
  alertas nuevas durante cada scan (forward-only). Alertas
  acumuladas en distintos scans para el mismo mercado y dirección
  quedaron como filas independientes en la DB.

  Raíz secundaria: la vacuna 2026-02-19_reset_price_based_resolutions
  regresó 431 alertas a pending, muchas de ellas duplicadas del mismo
  mercado que ya existían en pending → se amplificó el problema.

  Diagnóstico previo (2026-02-19):
    Mercados con 2+ alertas 4+★ pending: 17
    Caso extremo: Khamenei → 7 alertas (6×5★, 1×4★)
    Total alertas a marcar is_secondary: ~40

Estrategia:
  Para cada grupo (market_id, direction) con 2+ alertas pending:
    - PRIMARY  = mayor star_level, luego mayor score, luego id más bajo
    - SECUNDARIAS = el resto → is_secondary=True

  No se borran filas. No se modifica score ni wallets.
  El dashboard ya filtra is_secondary en group_alerts_by_market().

  (Nota: la lógica de agrupación del dashboard — commit 6f6406d —
   ya ocultará las secundarias visualmente. Esta vacuna las marca
   explícitamente en DB para que _try_consolidate() también las
   reconozca en scans futuros.)

Tablas afectadas: alerts (campo is_secondary)
Filas estimadas: ~40 secundarias (verificar con DRY_RUN=True)
Reversible: SÍ — rollback vuelve is_secondary=False en las mismas filas
Aplicada en producción: NO
Commit asociado: 6f6406d (dashboard grouping)
============================================================

Resultado de ejecución:
  - Fecha ejecución: [YYYY-MM-DD HH:MM UTC]
  - Grupos procesados: [N mercados]
  - Filas marcadas is_secondary=True: [N]
  - Observaciones: [ninguna / detalles]
"""

import logging
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Configuración ──────────────────────────────────────────
# Cambiar a False solo después de revisar el diagnóstico DRY_RUN.
DRY_RUN = True

# Solo grupos con este mínimo de estrellas activan la consolidación.
# Alertas de 1-2★ se dejan como están (ruido aceptable).
MIN_STARS_TO_CONSOLIDATE = 3


# ── DB ─────────────────────────────────────────────────────
def get_db():
    from src.database.supabase_client import SupabaseClient
    return SupabaseClient()


# ── FASE 1: DIAGNÓSTICO ───────────────────────────────────
def diagnostico(db) -> list[dict]:
    """
    Encuentra grupos (market_id, direction) con 2+ alertas pending
    de star_level >= MIN_STARS_TO_CONSOLIDATE.

    Retorna lista de alertas SECUNDARIAS (las que se marcarían
    is_secondary=True), ordenadas por grupo para facilitar la revisión.
    """
    logger.info("[DIAGNÓSTICO] Buscando alertas pending duplicadas por mercado...")

    rows = (
        db.client.table("alerts")
        .select(
            "id,market_id,direction,star_level,score,market_question,"
            "created_at,is_secondary,wallets"
        )
        .eq("outcome", "pending")
        .gte("star_level", MIN_STARS_TO_CONSOLIDATE)
        .order("market_id")
        .execute()
        .data
    ) or []

    logger.info("[DIAGNÓSTICO] Total alertas pending %d+★: %d", MIN_STARS_TO_CONSOLIDATE, len(rows))

    # Group by (market_id, direction)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (r["market_id"], (r.get("direction") or "YES").upper())
        groups[key].append(r)

    # Keep only groups with 2+ alerts
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

    logger.info("[DIAGNÓSTICO] Mercados con duplicados: %d", len(dup_groups))

    secondaries: list[dict] = []

    for (market_id, direction), group in sorted(dup_groups.items()):
        # Sort: best first (highest star, then highest score, then lowest id)
        group.sort(
            key=lambda x: (x.get("star_level") or 0, x.get("score") or 0, -(x.get("id") or 0)),
            reverse=True,
        )
        primary = group[0]
        siblings = group[1:]

        q = (primary.get("market_question") or "")[:55]
        stars = [a.get("star_level") for a in group]
        logger.info(
            "  [%s] %s %s → %d alertas %s | PRIMARY=#%s",
            market_id[:12], direction, q, len(group), stars, primary["id"],
        )
        for s in siblings:
            already = s.get("is_secondary", False)
            logger.info(
                "      SECUNDARIA: #%s %d★ score=%s %s",
                s["id"],
                s.get("star_level") or 0,
                s.get("score") or 0,
                "(ya marcada)" if already else "(a marcar)",
            )
            if not already:
                secondaries.append(s)

    already_marked = sum(1 for r in rows if r.get("is_secondary"))
    logger.info(
        "[DIAGNÓSTICO] Secundarias a marcar: %d | Ya marcadas previamente: %d",
        len(secondaries), already_marked,
    )
    return secondaries


# ── FASE 2: CORRECCIÓN ────────────────────────────────────
def correccion(db, filas: list[dict]) -> int:
    """Marca las alertas secundarias con is_secondary=True."""
    logger.info(
        "[CORRECCIÓN] %s modo. %d alertas a marcar como is_secondary=True.",
        "DRY-RUN" if DRY_RUN else "LIVE", len(filas),
    )

    modificadas = 0
    for fila in filas:
        alert_id = fila["id"]
        q = (fila.get("market_question") or "")[:45]
        if DRY_RUN:
            logger.info(
                "  DRY-RUN: #%s %d★ %s — %s",
                alert_id, fila.get("star_level") or 0, fila.get("direction"), q,
            )
        else:
            db.client.table("alerts").update(
                {"is_secondary": True}
            ).eq("id", alert_id).execute()
            modificadas += 1
            logger.info("  MARCADA: #%s → is_secondary=True", alert_id)

    if DRY_RUN:
        logger.info("[CORRECCIÓN] DRY-RUN: %d alertas se marcarían.", len(filas))
    else:
        logger.info("[CORRECCIÓN] %d alertas marcadas is_secondary=True.", modificadas)

    return modificadas if not DRY_RUN else 0


# ── FASE 3: VERIFICACIÓN ──────────────────────────────────
def verificacion(db, n_expected: int) -> None:
    """Comprueba que no quedan grupos con 2+ primarias pending."""
    logger.info("[VERIFICACIÓN] Comprobando estado final...")

    rows = (
        db.client.table("alerts")
        .select("market_id,direction,star_level")
        .eq("outcome", "pending")
        .eq("is_secondary", False)
        .gte("star_level", MIN_STARS_TO_CONSOLIDATE)
        .execute()
        .data
    ) or []

    groups: dict[tuple, int] = defaultdict(int)
    for r in rows:
        key = (r["market_id"], (r.get("direction") or "YES").upper())
        groups[key] += 1

    remaining_dup = {k: v for k, v in groups.items() if v > 1}
    if remaining_dup:
        logger.warning(
            "[VERIFICACIÓN] Aún hay %d grupos con 2+ primarias pending. Revisar.",
            len(remaining_dup),
        )
        for k, v in list(remaining_dup.items())[:5]:
            logger.warning("  %s %s → %d alertas", k[0][:12], k[1], v)
    else:
        logger.info(
            "[VERIFICACIÓN] OK — ningún grupo tiene más de 1 primaria pending."
        )

    marked = (
        db.client.table("alerts")
        .select("id")
        .eq("is_secondary", True)
        .execute()
        .data
    ) or []
    logger.info("[VERIFICACIÓN] Total alertas is_secondary=True en DB: %d", len(marked))


# ── ROLLBACK ──────────────────────────────────────────────
def rollback(db, backup: list[dict]) -> None:
    """Revierte is_secondary=True → False para las alertas del backup."""
    logger.warning("[ROLLBACK] Revirtiendo %d alertas a is_secondary=False...", len(backup))
    for row in backup:
        db.client.table("alerts").update(
            {"is_secondary": False}
        ).eq("id", row["id"]).execute()
        logger.info("  REVERTIDA: #%s", row["id"])
    logger.warning("[ROLLBACK] Completado.")


# ── Entry point ───────────────────────────────────────────
def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar en el entorno.")
        sys.exit(1)

    db = get_db()

    # 1. Diagnóstico
    to_mark = diagnostico(db)

    print("\n" + "=" * 60)
    print(f"Alertas a marcar is_secondary=True:  {len(to_mark)}")
    print(f"DRY_RUN: {DRY_RUN}")
    print("=" * 60 + "\n")

    if not to_mark:
        logger.info("No hay duplicados que consolidar. Saliendo.")
        return

    # 2. Confirmación antes de ejecutar en LIVE
    if not DRY_RUN:
        resp = input(
            f"\n¿Marcar {len(to_mark)} alertas como is_secondary=True en PRODUCCIÓN? [s/N] "
        ).strip().lower()
        if resp != "s":
            logger.info("Operación cancelada.")
            sys.exit(0)

    # 3. Corrección
    correccion(db, to_mark)

    # 4. Verificación (solo en LIVE)
    if not DRY_RUN:
        verificacion(db, len(to_mark))


if __name__ == "__main__":
    main()
