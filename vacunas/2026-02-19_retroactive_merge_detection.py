"""
============================================================
VACUNA: Detección retroactiva de merges CLOB
Fecha: 2026-02-19
Bug que corrige: El sistema no detectaba cuando un wallet compraba YES y NO
  del mismo mercado (CLOB arbitrage). Alertas 677/678 son el caso concreto.
  A partir de hoy el filtro N12 lo detecta en tiempo real. Esta vacuna
  intenta encontrar casos históricos en los datos existentes.

LIMITACIÓN IMPORTANTE: No hay tabla de trades raw en Supabase.
  Solo disponemos del campo `alerts.wallets` (trades de la dirección alertada).
  No podemos reconstruir los trades de la dirección opuesta. Por tanto:
  - NO podemos detectar merges donde el wallet compró YES en dirección opuesta
    a la alerta (esos trades nunca se guardaron).
  - SÍ podemos marcar alertas donde alerts.wallets contenga trades de AMBAS
    direcciones (caso raro — solo ocurre si hay bug en el pipeline).
  - El valor real de esta vacuna es DOCUMENTAL: confirmar la limitación y
    dejar el campo merge_suspected=False en todos los históricos.

Tablas afectadas: alerts
Filas estimadas a modificar: 0-5 (casos con wallets de dirección mixta)
Reversible: SI — merge_suspected=False es el estado previo (por defecto)
Aplicada en producción: NO
Commit que introdujo el fix en código: (ver git log FILTER_N12 / _check_merge)
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
DRY_RUN = True

PAGE_SIZE = 500

# Thresholds (mirror config.py)
MERGE_WINDOW_HOURS = 12
MERGE_NET_THRESHOLD = 0.15   # net_shares < 15% of larger side → merge
MERGE_MIN_SHARES = 1000.0    # minimum shares on smaller side to qualify


def _calc_shares(trades_in_direction: list[dict]) -> float:
    """Sum shares (amount/price) for a list of trade dicts."""
    total = 0.0
    for t in trades_in_direction:
        price = t.get("price", 0)
        amount = t.get("amount", 0)
        if price > 0 and amount > 0:
            total += amount / price
    return total


def diagnostico(db: SupabaseClient) -> list[dict]:
    """
    Busca alertas históricas donde alerts.wallets contiene trades de DIRECCIÓN
    MIXTA para el mismo wallet (YES y NO). Esto solo ocurre en casos excepcionales.

    NOTA: Esta búsqueda tiene alcance muy limitado por falta de trades raw.
    Ver LIMITACIÓN en la cabecera.
    """
    logger.info("[DIAGNÓSTICO] Buscando alertas históricas con wallets de dirección mixta...")
    logger.warning(
        "LIMITACIÓN: Solo podemos analizar trades guardados en alerts.wallets. "
        "No hay tabla de trades raw. Los merges donde el wallet compró la dirección "
        "OPUESTA a la alerta no son detectables retroactivamente."
    )

    affected = []
    offset = 0

    while True:
        rows = (
            db.client.table("alerts")
            .select("id,market_id,direction,wallets,merge_suspected")
            .eq("outcome", "pending")
            .eq("merge_suspected", False)
            .not_.is_("wallets", "null")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        ) or []

        if not rows:
            break

        for row in rows:
            wallets = row.get("wallets") or []
            alert_direction = row.get("direction", "YES")

            # For each wallet in this alert, check if it has BOTH YES and NO trades
            for w in wallets:
                trades = w.get("trades") or []
                if not trades:
                    continue

                # trades in alerts.wallets only have the alert direction; skip if no direction field
                # (Most historical alerts don't have per-trade direction in the wallets blob)
                yes_trades = [t for t in trades if t.get("direction") == "YES"]
                no_trades = [t for t in trades if t.get("direction") == "NO"]

                if not yes_trades or not no_trades:
                    continue  # No mixed-direction trades for this wallet — expected

                # Check shares threshold
                shares_yes = _calc_shares(yes_trades)
                shares_no = _calc_shares(no_trades)
                lado_mayor = max(shares_yes, shares_no)
                lado_menor = min(shares_yes, shares_no)

                if lado_menor < MERGE_MIN_SHARES:
                    continue

                net_shares = abs(shares_yes - shares_no)
                if net_shares >= lado_mayor * MERGE_NET_THRESHOLD:
                    continue

                logger.info(
                    "  Merge candidato: alert #%s (market=%s dir=%s) "
                    "wallet=%s YES=%.0f shares NO=%.0f shares",
                    row["id"], row.get("market_id", "?")[:12],
                    alert_direction,
                    w.get("address", "?")[:10],
                    shares_yes, shares_no,
                )
                affected.append({
                    "id": row["id"],
                    "market_id": row.get("market_id"),
                    "direction": alert_direction,
                    "wallet_address": w.get("address"),
                    "shares_yes": shares_yes,
                    "shares_no": shares_no,
                })
                break  # One wallet match per alert is enough

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info("[DIAGNÓSTICO] Alertas con merge CLOB detectado: %d", len(affected))
    if not affected:
        logger.info(
            "Resultado esperado: 0 alertas afectadas. "
            "Los merges históricos no son detectables sin tabla de trades raw."
        )
    return affected


def correccion(db: SupabaseClient, filas: list[dict]) -> int:
    """Marca merge_suspected=True en alertas con merges CLOB detectados."""
    logger.info(
        "[CORRECCIÓN] %s modo. %d filas a procesar.",
        "DRY-RUN" if DRY_RUN else "LIVE", len(filas),
    )
    modificadas = 0

    for fila in filas:
        alert_id = fila["id"]
        if DRY_RUN:
            logger.info(
                "  DRY-RUN: actualizaría alert #%s → merge_suspected=True "
                "(YES=%.0f shares, NO=%.0f shares)",
                alert_id, fila["shares_yes"], fila["shares_no"],
            )
        else:
            db.client.table("alerts").update(
                {"merge_suspected": True}
            ).eq("id", alert_id).execute()
            modificadas += 1
            logger.info("  ACTUALIZADO: alert #%s merge_suspected=True", alert_id)

    if DRY_RUN:
        logger.info(
            "[CORRECCIÓN] DRY-RUN completado. Filas que se modificarían: %d", len(filas)
        )
    else:
        logger.info("[CORRECCIÓN] Completado. Filas modificadas: %d", modificadas)

    return modificadas


def verificacion(db: SupabaseClient) -> None:
    """Confirma que las alertas modificadas tienen merge_suspected=True."""
    logger.info("[VERIFICACIÓN] Comprobando merge_suspected en alertas tratadas...")
    rows = (
        db.client.table("alerts")
        .select("id,merge_suspected")
        .eq("merge_suspected", True)
        .execute()
        .data
    ) or []
    logger.info("  Alertas con merge_suspected=True en DB: %d", len(rows))


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
        logger.info(
            "NOTA: Esto es el resultado esperado. Sin tabla de trades raw no podemos "
            "detectar merges históricos. El filtro N12 en tiempo real cubrirá los "
            "casos futuros desde la fecha de este commit."
        )
        return

    logger.info("Filas afectadas encontradas: %d", len(filas))

    # 2. Confirmación
    if not DRY_RUN:
        resp = input(
            f"\n¿Aplicar corrección sobre {len(filas)} filas en PRODUCCIÓN? [s/N] "
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
