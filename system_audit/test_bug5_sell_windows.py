"""
Test Bug 5 Fix — Sell detection con ventanas dinámicas
=======================================================
Tests A, B, C, D del plan.

Uso: python system_audit/test_bug5_sell_windows.py
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(".env")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dateutil import parser as dt_parser

from src import config
from src.scanner.polymarket_client import PolymarketClient
from src.database.supabase_client import SupabaseClient
from src.analysis.sell_detector import SellDetector

THROTTLE = 0.2

# ── Helpers ────────────────────────────────────────────────────────────────────

def minutes_since(ts_str: str | None) -> int | None:
    if not ts_str:
        return None
    try:
        anchor = dt_parser.parse(ts_str)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        return int((datetime.now(timezone.utc) - anchor).total_seconds() / 60)
    except Exception:
        return None

def lookback_for_position(pos: dict) -> int:
    """Replica el cálculo del fix para mostrar qué ventana se usaría."""
    elapsed = minutes_since(pos.get("created_at"))
    if elapsed is None:
        return config.SCAN_LOOKBACK_MINUTES
    return min(max(config.SCAN_LOOKBACK_MINUTES, elapsed + 60), 43_200)

# ── Test A — caso forense: alerta #13765 ──────────────────────────────────────

def test_a(pm: PolymarketClient, db: SupabaseClient) -> None:
    print("\n" + "="*65)
    print("TEST A — Caso forense: alerta #13765")
    print("="*65)
    ALERT_ID = 13765
    try:
        row = (
            db.client.table("alerts")
            .select("id,market_id,direction,wallets,created_at,outcome,star_level")
            .eq("id", ALERT_ID)
            .single()
            .execute()
            .data
        )
    except Exception as e:
        print(f"  ERROR fetching alert #{ALERT_ID}: {e}")
        return

    if not row:
        print(f"  Alert #{ALERT_ID} not found.")
        return

    wallets = row.get("wallets") or []
    market_id = row.get("market_id", "")
    direction = row.get("direction", "YES")
    created_at = row.get("created_at", "")
    elapsed = minutes_since(created_at)
    lookback_old = config.SCAN_LOOKBACK_MINUTES
    lookback_new = min(max(lookback_old, (elapsed or 0) + 60), 43_200)

    print(f"  Alert #{ALERT_ID} | {row.get('star_level')}★ | outcome={row.get('outcome')}")
    print(f"  Market: {market_id[:16]}... | Direction: {direction}")
    print(f"  Created: {created_at[:19]} ({elapsed//60 if elapsed else '?'}h ago)")
    print(f"  Ventana OLD: {lookback_old} min ({lookback_old//60}h)")
    print(f"  Ventana NEW: {lookback_new} min ({lookback_new//60:.0f}h)")
    print()

    for w in wallets:
        addr = w.get("address", "")
        if not addr:
            continue
        original_amount = w.get("total_amount", 0)
        avg_entry = w.get("avg_entry_price", 0)

        print(f"  Wallet: {addr[:14]}... | amount=${original_amount:.0f} | entry={avg_entry:.3f}")

        # Fetch with NEW window
        time.sleep(THROTTLE)
        trades_new = pm.get_recent_trades(market_id=market_id, minutes=lookback_new, min_amount=0)
        wallet_trades = [t for t in trades_new if t.wallet_address == addr]
        sells = [t for t in wallet_trades if t.direction != direction]
        buys  = [t for t in wallet_trades if t.direction == direction]

        print(f"    Trades in {lookback_new//60:.0f}h window: {len(wallet_trades)} total "
              f"({len(buys)} buys, {len(sells)} sells)")

        if sells:
            sell_amt = sum(t.amount for t in sells)
            sell_ts  = max(t.timestamp for t in sells)
            pct = (sell_amt / original_amount * 100) if original_amount > 0 else 0
            print(f"    SELLS detected: ${sell_amt:.0f} ({pct:.0f}% of position)")
            print(f"    Last sell at: {sell_ts.strftime('%Y-%m-%d %H:%M')} UTC")
            status = "FULL_EXIT" if pct >= 90 else ("PARTIAL_EXIT" if pct >= 30 else "SMALL_SELL")
            print(f"    → Status: {status}")
            result = "✓ FIX FUNCIONA — sell detectado con ventana expandida"
        else:
            print(f"    No sells found in window. Wallet may still be holding.")
            result = "~ No sells (wallet holding or market data unavailable)"
        print(f"    {result}")

# ── Test B — alertas zombie (las más antiguas) ────────────────────────────────

def test_b(db: SupabaseClient) -> None:
    print("\n" + "="*65)
    print("TEST B — Ventanas que se usarían para las posiciones abiertas")
    print("="*65)
    open_positions = db.get_open_positions()
    if not open_positions:
        print("  No hay posiciones abiertas.")
        return

    print(f"  Total posiciones abiertas: {len(open_positions)}")

    # Agrupar por mercado y calcular ventana
    from collections import defaultdict
    by_market: dict = defaultdict(list)
    for pos in open_positions:
        by_market[pos["market_id"]].append(pos)

    rows = []
    for mid, positions in by_market.items():
        oldest_created = None
        for pos in positions:
            raw_ts = pos.get("created_at")
            if raw_ts:
                try:
                    anchor = dt_parser.parse(raw_ts)
                    if anchor.tzinfo is None:
                        anchor = anchor.replace(tzinfo=timezone.utc)
                    if oldest_created is None or anchor < oldest_created:
                        oldest_created = anchor
                except Exception:
                    pass
        if oldest_created:
            elapsed = int((datetime.now(timezone.utc) - oldest_created).total_seconds() / 60)
            lookback_old = config.SCAN_LOOKBACK_MINUTES
            lookback_new = min(max(lookback_old, elapsed + 60), 43_200)
            rows.append((mid, len(positions), elapsed, lookback_old, lookback_new))

    # Sort by elapsed (oldest first)
    rows.sort(key=lambda x: x[2], reverse=True)

    # Show top 15 oldest
    print(f"\n  Top 15 mercados con posiciones más antiguas:")
    print(f"  {'Market':>16}  {'Pos':>3}  {'Age':>8}  {'OldWin':>8}  {'NewWin':>8}  {'Gain':>8}")
    print("  " + "-"*60)
    for (mid, n, elapsed, lo, ln) in rows[:15]:
        age_str = f"{elapsed//1440}d{(elapsed%1440)//60}h" if elapsed >= 1440 else f"{elapsed//60}h{elapsed%60}m"
        gain = ln - lo
        print(f"  {mid[:16]}  {n:>3}  {age_str:>8}  {lo:>6}min  {ln:>6}min  +{gain}min")

    # How many would have buys_shares=0 with old window?
    would_fail_old = sum(1 for (_, _, elapsed, lo, _) in rows if elapsed > lo)
    print(f"\n  Posiciones que fallarían con ventana vieja (elapsed > 35min): {would_fail_old}/{len(rows)} mercados")
    print(f"  → Con fix: todas cubiertss hasta {43_200//60} días")

# ── Test C — alerta activa con holding real ───────────────────────────────────

def test_c(pm: PolymarketClient, db: SupabaseClient) -> None:
    print("\n" + "="*65)
    print("TEST C — Alertas 4★+ recientes: verificar holding vs exit")
    print("="*65)

    # Get recent 4★+ pending alerts (last 48h)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    try:
        rows = (
            db.client.table("alerts")
            .select("id,market_id,direction,wallets,created_at,star_level")
            .eq("outcome", "pending")
            .gte("star_level", 4)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
            .data or []
        )
    except Exception as e:
        print(f"  ERROR: {e}")
        return

    if not rows:
        print("  No hay alertas 4★+ en las últimas 48h.")
        return

    print(f"  Evaluando {len(rows)} alertas 4★+ recientes:")
    for alert in rows:
        aid = alert["id"]
        market_id = alert.get("market_id", "")
        direction = alert.get("direction", "YES")
        wallets = alert.get("wallets") or []
        created_at = alert.get("created_at", "")
        elapsed = minutes_since(created_at)
        lookback = min(max(config.SCAN_LOOKBACK_MINUTES, (elapsed or 0) + 60), 43_200)

        print(f"\n  Alert #{aid} | {alert.get('star_level')}★ | {created_at[:16]} "
              f"({elapsed//60 if elapsed else '?'}h ago)")

        for w in (wallets[:2]):  # max 2 wallets per alert
            addr = w.get("address", "")
            if not addr:
                continue
            original_amount = w.get("total_amount", 0)

            time.sleep(THROTTLE)
            trades = pm.get_recent_trades(market_id=market_id, minutes=lookback, min_amount=0)
            wt = [t for t in trades if t.wallet_address == addr]
            sells = [t for t in wt if t.direction != direction]
            buys  = [t for t in wt if t.direction == direction]

            sell_amt = sum(t.amount for t in sells)
            pct_sold = (sell_amt / original_amount * 100) if original_amount > 0 else 0

            if pct_sold >= 90:
                status = "FULL_EXIT ⚠️"
            elif pct_sold >= 30:
                status = "PARTIAL_EXIT"
            elif sell_amt > 0:
                status = "SMALL_SELL"
            else:
                status = "HOLDING ✓"

            print(f"    {addr[:14]}... | buys={len(buys)} sells={len(sells)} "
                  f"sold={pct_sold:.0f}% | {status}")

# ── Test D — conteo de API calls ──────────────────────────────────────────────

def test_d(db: SupabaseClient) -> None:
    print("\n" + "="*65)
    print("TEST D — Rate limit: estimación de API calls para scan completo")
    print("="*65)

    open_positions = db.get_open_positions()
    if not open_positions:
        print("  No hay posiciones abiertas.")
        return

    from collections import defaultdict
    by_market: dict = defaultdict(list)
    for pos in open_positions:
        by_market[pos["market_id"]].append(pos)

    total_markets = len(by_market)

    # Para check_open_positions: 1 request/mercado (puede ser hasta 5 páginas)
    calls_check_open = total_markets

    # Para check_net_positions: también 1 batch fetch/mercado
    positions_capped = min(len(open_positions), config.SELL_POST_SCAN_MAX_ALERTS)
    by_market_capped: dict = defaultdict(list)
    for pos in list(open_positions)[:positions_capped]:
        by_market_capped[pos["market_id"]].append(pos)
    calls_check_net = len(by_market_capped)

    # Máximo páginas por mercado: 5 (ya hardcodeado en get_recent_trades)
    max_pages_per_market = 5
    api_calls_max_open = calls_check_open * max_pages_per_market
    api_calls_max_net  = calls_check_net  * max_pages_per_market

    print(f"  Posiciones abiertas totales:  {len(open_positions)}")
    print(f"  Mercados únicos:              {total_markets}")
    print()
    print(f"  check_open_positions() por scan:")
    print(f"    Requests base (1/mercado):  {calls_check_open}")
    print(f"    Requests max (5 págs/mkt):  {api_calls_max_open}")
    print(f"    Tiempo est. (0.05s/req):    {api_calls_max_open * 0.05:.1f}s max")
    print()
    print(f"  check_net_positions() por deep scan (cap {config.SELL_POST_SCAN_MAX_ALERTS} pos):")
    print(f"    Requests base (1/mercado):  {calls_check_net}")
    print(f"    Requests max (5 págs/mkt):  {api_calls_max_net}")
    print(f"    Tiempo est. (0.05s/req):    {api_calls_max_net * 0.05:.1f}s max")
    print()
    print(f"  Rate limit Polymarket Data API: ~100 req/min → "
          f"{'OK ✓' if api_calls_max_open < 100 else 'PUEDE NECESITAR THROTTLE ⚠️'}")
    print(f"  Nota: en práctica mucho menos (mayoría de mercados = 1 página)")

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Bug 5 — Sell Detection Windows Fix — Verification Tests")

    pm = PolymarketClient()
    db = SupabaseClient()

    test_a(pm, db)
    test_b(db)
    test_c(pm, db)
    test_d(db)

    print("\n" + "="*65)
    print("Tests completados.")

if __name__ == "__main__":
    main()
