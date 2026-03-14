"""
Test Bug 4 Fix — distinct_markets diversity multiplier
=======================================================
Tests A, B, C, D para verificar el fix antes de commit.

Uso: python system_audit/test_bug4_diversity.py
"""

import math
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(".env")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config
from src.scanner.polymarket_client import PolymarketClient
from src.database.supabase_client import SupabaseClient

SCORE_CAP = 400
THROTTLE = 0.2  # seconds between API calls

# ── Helpers ────────────────────────────────────────────────────────────────────

def get_diversity_mult(n: int | None) -> float:
    if n is None:
        return 1.0
    if n >= config.DIVERSITY_SUPER_SHOTGUN_MIN:
        return config.DIVERSITY_SUPER_SHOTGUN_MULTIPLIER
    if n >= config.DIVERSITY_SHOTGUN_MIN_MARKETS:
        return config.DIVERSITY_SHOTGUN_MULTIPLIER
    if n <= config.DIVERSITY_SNIPER_MAX_MARKETS:
        return config.DIVERSITY_SNIPER_MULTIPLIER
    return 1.0

def diversity_label(n: int | None) -> str:
    if n is None:
        return "neutral (n/a)"
    if n >= config.DIVERSITY_SUPER_SHOTGUN_MIN:
        return "super-shotgun"
    if n >= config.DIVERSITY_SHOTGUN_MIN_MARKETS:
        return "shotgun"
    if n <= config.DIVERSITY_SNIPER_MAX_MARKETS:
        return "sniper"
    return "neutral"

def score_to_stars(score: int) -> int:
    for threshold, stars in config.NEW_STAR_THRESHOLDS:
        if score >= threshold:
            return stars
    return 0

def get_amount_mult(total_amount: float) -> float:
    if total_amount <= 0:
        return 0.3
    raw = 0.18 * math.log(total_amount) - 0.37
    return round(max(0.3, min(2.0, raw)), 2)

# ── Test A — BTC sniper wallet (espera ~2 mercados → 1.2×) ────────────────────

def test_a(pm: PolymarketClient) -> None:
    print("\n" + "="*65)
    print("TEST A — BTC sniper wallet (esperado: ~2 mercados, mult 1.2×)")
    print("="*65)
    wallet = "0x6c3b8555aa6bef7ed8197d6e1cf8aae262fa1f54"
    h = pm.get_wallet_pm_history_cached(wallet)
    if h is None:
        print(f"  ERROR: no data from API para {wallet[:12]}...")
        return
    n = h.get("distinct_markets", 0)
    mult = get_diversity_mult(n)
    label = diversity_label(n)
    print(f"  Wallet:            {wallet[:14]}...")
    print(f"  distinct_markets:  {n}")
    print(f"  trade_count:       {h.get('trade_count')}")
    print(f"  total_volume:      ${h.get('total_volume', 0):.0f}")
    print(f"  Multiplicador:     {mult}× ({label})")
    ok = "✓ PASS" if mult == 1.2 else "✗ FAIL (esperado 1.2×)"
    print(f"  Resultado:         {ok}")

# ── Test B — Super-shotgun wallets (esperado: mult 0.5×) ──────────────────────

def test_b(pm: PolymarketClient) -> None:
    print("\n" + "="*65)
    print("TEST B — Super-shotgun wallets (esperado: ≥20 mercados, mult 0.5×)")
    print("="*65)
    wallets = [
        ("0xe8dd7741ccb12350957ec71e9ee332e0d1e6ec86", "≈47 mercados"),
        ("0xe7387473b067235436884d16799777cf279edf65", "≈38 mercados"),
        ("0xf9009e251a87b8b96fb184591da93546af96cd0c", "≈32 mercados"),
    ]
    for addr, expected in wallets:
        time.sleep(THROTTLE)
        h = pm.get_wallet_pm_history_cached(addr)
        if h is None:
            print(f"  {addr[:14]}... → ERROR: no data")
            continue
        n = h.get("distinct_markets", 0)
        mult = get_diversity_mult(n)
        label = diversity_label(n)
        ok = "✓" if mult == 0.5 else ("~ neutral/shotgun" if mult < 1.2 else "✗ FAIL")
        print(f"  {addr[:14]}... | markets={n:>3}  mult={mult}× ({label})  {ok}")
        print(f"             expected: {expected}")

# ── Test C — 5★ pending alerts: simular impacto del fix ───────────────────────

def test_c(pm: PolymarketClient, db: SupabaseClient) -> None:
    print("\n" + "="*65)
    print("TEST C — Impacto en alertas 5★ pending (EL MÁS IMPORTANTE)")
    print("="*65)

    alerts = db.get_high_star_alerts(min_stars=5)
    if not alerts:
        print("  No hay alertas 5★ pending.")
        return

    print(f"  Alertas 5★ pending encontradas: {len(alerts)}\n")

    stay_5 = 0
    drop_to_4 = 0
    drop_lower = 0
    rows = []

    for alert in alerts:
        alert_id = alert.get("id")
        score_raw = alert.get("score_raw") or 0
        stored_mult = alert.get("multiplier") or 1.2
        stored_score = alert.get("score_final") or round(score_raw * stored_mult)
        wallets_json = alert.get("wallets") or []
        market_q = (alert.get("market_question") or "")[:45]

        # Extraer wallet addresses del JSON
        addresses = [w["address"] for w in wallets_json if isinstance(w, dict) and "address" in w]
        if not addresses:
            # Puede estar como campo top-level
            if alert.get("wallet_address"):
                addresses = [alert["wallet_address"]]

        # Fetch real distinct_markets para cada wallet, tomar el max
        max_distinct = 1  # fallback (bug behavior)
        for addr in addresses:
            time.sleep(THROTTLE)
            h = pm.get_wallet_pm_history_cached(addr)
            if h and h.get("distinct_markets"):
                max_distinct = max(max_distinct, h["distinct_markets"])

        # Recalcular:
        # stored_mult = amount_mult × 1.2  (bug: siempre sniper)
        # amount_mult = stored_mult / 1.2
        amount_mult = round(stored_mult / 1.2, 4)
        new_div_mult = get_diversity_mult(max_distinct)
        new_mult = round(amount_mult * new_div_mult, 2)
        new_score = min(SCORE_CAP, round(score_raw * new_mult))
        new_stars = score_to_stars(new_score)

        change = ""
        if new_stars == 5:
            stay_5 += 1
            change = "mantiene 5★"
        elif new_stars == 4:
            drop_to_4 += 1
            change = "baja a 4★  ←"
        else:
            drop_lower += 1
            change = f"baja a {new_stars}★  ←←"

        wallet_str = addresses[0][:12] + "..." if addresses else "?"
        rows.append((alert_id, market_q, wallet_str, max_distinct, stored_mult, new_mult,
                     stored_score, new_score, change))

    # Imprimir tabla
    print(f"  {'ID':>5}  {'Mercado':<45}  {'Wallet':<15}  "
          f"{'Mkts':>4}  {'MultOld':>7}  {'MultNew':>7}  "
          f"{'ScOld':>6}  {'ScNew':>6}  Resultado")
    print("  " + "-"*135)
    for (aid, mq, wa, dm, om, nm, os_, ns, ch) in rows:
        print(f"  {aid:>5}  {mq:<45}  {wa:<15}  "
              f"{dm:>4}  {om:>7.2f}  {nm:>7.2f}  "
              f"{os_:>6}  {ns:>6}  {ch}")

    print(f"\n  RESUMEN:")
    print(f"    Mantienen 5★:  {stay_5}/{len(alerts)}")
    print(f"    Bajan a 4★:    {drop_to_4}/{len(alerts)}")
    print(f"    Bajan más:     {drop_lower}/{len(alerts)}")

# ── Test D — Regresión sniper: confirmar que ≤3 mercados → 1.2× ──────────────

def test_d(pm: PolymarketClient) -> None:
    print("\n" + "="*65)
    print("TEST D — Regresión sniper (≤3 mercados deben seguir con 1.2×)")
    print("="*65)
    # Reutilizamos la wallet del Test A (esperado: sniper)
    wallets_sniper = [
        "0x6c3b8555aa6bef7ed8197d6e1cf8aae262fa1f54",  # BTC sniper (Test A)
    ]
    all_ok = True
    for addr in wallets_sniper:
        h = pm.get_wallet_pm_history_cached(addr)
        if h is None:
            print(f"  {addr[:14]}... → no data (cache miss)")
            continue
        n = h.get("distinct_markets", 0)
        mult = get_diversity_mult(n)
        ok = mult == 1.2
        if not ok:
            all_ok = False
        status = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {addr[:14]}... | markets={n}  mult={mult}×  {status}")

    if all_ok:
        print("\n  Todas las wallets sniper siguen con 1.2×. Regresión OK.")
    else:
        print("\n  ✗ REGRESIÓN DETECTADA — revisar fix.")

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Bug 4 — Verification Tests")
    print("distinct_markets fix: real PM history vs always=1")

    pm = PolymarketClient()
    db = SupabaseClient()

    test_a(pm)
    test_b(pm)
    test_c(pm, db)
    test_d(pm)

    print("\n" + "="*65)
    print("Tests completados.")

if __name__ == "__main__":
    main()
