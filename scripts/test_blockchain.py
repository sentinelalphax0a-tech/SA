"""
Quick Alchemy / Polygon blockchain test.

Steps:
1. Connect to Alchemy
2. Query wallet age for a known Polymarket wallet
3. Print results
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.scanner.blockchain_client import BlockchainClient

# Active Polymarket trader (found via CTF Exchange recent transfers)
TEST_WALLET = "0xf24780aab4338e6162545b35fa3cae13d662e269"


def main() -> None:
    print("1. Connecting to Alchemy (Polygon)...")
    client = BlockchainClient()
    if not client.w3.is_connected():
        print("FAIL: Could not connect to Alchemy RPC")
        sys.exit(1)
    chain_id = client.w3.eth.chain_id
    print(f"   Connected. Chain ID: {chain_id}")

    print(f"\n2. Querying wallet age for {TEST_WALLET[:12]}...")
    age = client.get_wallet_age_days(TEST_WALLET)
    if age is None:
        print("   WARNING: Could not determine wallet age")
    else:
        print(f"   Wallet age: {age} days")

    first_tx = client.get_first_transaction_timestamp(TEST_WALLET)
    print(f"   First tx: {first_tx}")

    print(f"\n3. Checking first tx contracts...")
    contracts = client.get_first_tx_contracts(TEST_WALLET)
    for c in contracts[:5]:
        print(f"   -> {c}")

    is_pm = client.is_first_tx_polymarket(TEST_WALLET)
    print(f"   First tx is Polymarket: {is_pm}")

    print(f"\n4. Getting USDC balance...")
    balance = client.get_balance(TEST_WALLET)
    print(f"   USDC balance: ${balance:,.2f}")

    print("\n\u2705 Blockchain client test complete")


if __name__ == "__main__":
    main()
