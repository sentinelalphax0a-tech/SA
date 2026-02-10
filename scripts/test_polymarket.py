"""
Quick Polymarket API test.

Steps:
1. Fetch active political markets
2. Print top 5 with odds and volume
3. For each, fetch last 35 min of trades
4. Print trade count and total volume
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.scanner.polymarket_client import PolymarketClient


def main() -> None:
    pm = PolymarketClient()

    # --- Step 1: Fetch active markets (politics only) ---
    print("1. Fetching active political markets...")
    markets = pm.get_active_markets(categories=["politics"])
    print(f"   Found {len(markets)} markets with odds in range\n")

    if not markets:
        print("   No markets found. Check API or category filters.")
        sys.exit(1)

    # --- Step 2: Show top 5 by volume ---
    top5 = sorted(markets, key=lambda m: m.volume_24h, reverse=True)[:5]

    for i, m in enumerate(top5, 1):
        print(f"   [{i}] {m.question[:75]}")
        print(f"       Odds(YES): {m.current_odds:.3f}  |  Vol 24h: ${m.volume_24h:,.0f}  |  Liq: ${m.liquidity:,.0f}")

        # --- Step 3: Fetch recent trades ---
        trades = pm.get_recent_trades(m.market_id, minutes=35, min_amount=50.0)
        total_vol = sum(t.amount for t in trades)
        print(f"       Trades (35 min, >$50): {len(trades)}  |  Vol: ${total_vol:,.0f}")

        # Show a few sample trades
        for t in trades[:3]:
            print(f"         {t.direction} ${t.amount:,.2f} @ {t.price:.3f} by {t.wallet_address[:12]}...")
        print()

    # --- Step 4: Orderbook for top market ---
    top = top5[0]
    print(f"2. Orderbook for: {top.question[:60]}...")
    book = pm.get_market_orderbook(top.market_id)
    if book:
        n_bids = len(book["bids"])
        n_asks = len(book["asks"])
        print(f"   Bids: {n_bids} levels  |  Asks: {n_asks} levels")
        if book["bids"]:
            best_bid = book["bids"][-1]
            print(f"   Best bid: {best_bid['price']} x {best_bid['size']}")
        if book["asks"]:
            best_ask = book["asks"][0]
            print(f"   Best ask: {best_ask['price']} x {best_ask['size']}")
    else:
        print("   Could not fetch orderbook")

    # --- Step 5: Live odds via CLOB midpoint ---
    print(f"\n3. Live odds (CLOB midpoint) for: {top.question[:60]}...")
    odds = pm.get_market_odds(top.market_id)
    if odds:
        print(f"   YES: {odds.get('yes', 'N/A')}  |  NO: {odds.get('no', 'N/A')}")
    else:
        print("   Could not fetch odds")

    print("\n\u2705 Polymarket client test complete")


if __name__ == "__main__":
    main()
