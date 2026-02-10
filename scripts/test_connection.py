"""
Quick Supabase connection test.

Steps:
1. Connect to Supabase
2. Read system_config
3. Insert a test wallet
4. Read it back
5. Delete it
6. Print result
"""

import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database.supabase_client import SupabaseClient
from src.database.models import Wallet

TEST_ADDRESS = "0x_TEST_CONNECTION_DELETE_ME"


def main() -> None:
    print("1. Connecting to Supabase...")
    db = SupabaseClient()

    # --- Step 1: test_connection ---
    ok = db.test_connection()
    if not ok:
        print("FAIL: Could not connect to Supabase")
        sys.exit(1)
    print("   Connection OK")

    # --- Step 2: read system_config ---
    print("2. Reading system_config...")
    configs = db.get_system_config()
    print(f"   Got {len(configs)} config rows")

    # --- Step 3: insert test wallet ---
    print("3. Inserting test wallet...")
    test_wallet = Wallet(address=TEST_ADDRESS, category="test")
    db.upsert_wallet(test_wallet)
    print(f"   Inserted wallet {TEST_ADDRESS}")

    # --- Step 4: read it back ---
    print("4. Reading test wallet back...")
    row = db.get_wallet(TEST_ADDRESS)
    if not row:
        print("FAIL: Could not read back test wallet")
        sys.exit(1)
    assert row["address"] == TEST_ADDRESS
    print(f"   Read back: address={row['address']}, category={row['category']}")

    # --- Step 5: delete it ---
    print("5. Deleting test wallet...")
    db.delete_wallet(TEST_ADDRESS)
    print("   Deleted")

    print("\n\u2705 Supabase connection OK")


if __name__ == "__main__":
    main()
