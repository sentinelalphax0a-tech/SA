"""
Supabase Client — Database operations.

Handles all CRUD operations against the Supabase PostgreSQL backend.
"""

import logging
from datetime import datetime, date
from dataclasses import asdict

from supabase import create_client, Client

from src import config
from src.database.models import (
    Wallet,
    Market,
    MarketSnapshot,
    Alert,
    WalletFunding,
    Scan,
    WeeklyReport,
    SmartMoneyLeaderboard,
    SystemConfig,
)

logger = logging.getLogger(__name__)


def _serialize(data: dict) -> dict:
    """Convert datetime/date values to ISO strings for JSON serialization."""
    out = {}
    for k, v in data.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


class SupabaseClient:
    """Wrapper around the Supabase Python client."""

    def __init__(self) -> None:
        self.client: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

    # ── Connection Test ─────────────────────────────────────

    def test_connection(self) -> bool:
        """Try to read system_config. Returns True if connection works."""
        try:
            self.client.table("system_config").select("*").limit(1).execute()
            return True
        except Exception as e:
            logger.error("Supabase connection test failed: %s", e)
            return False

    # ── System Config ──────────────────────────────────────

    def get_system_config(self) -> list[dict]:
        """Read all rows from system_config."""
        resp = self.client.table("system_config").select("*").execute()
        return resp.data or []

    def get_config(self, key: str) -> str | None:
        """Get a single value from system_config by key."""
        resp = (
            self.client.table("system_config")
            .select("value")
            .eq("key", key)
            .single()
            .execute()
        )
        return resp.data["value"] if resp.data else None

    def upsert_config(self, entry: SystemConfig) -> None:
        """Insert or update a system_config row."""
        data = _serialize(asdict(entry))
        self.client.table("system_config").upsert(data).execute()

    def is_scan_enabled(self) -> bool:
        """Check the scan_enabled kill switch."""
        return self.get_config("scan_enabled") == "true"

    def is_publish_x_enabled(self) -> bool:
        return self.get_config("publish_x") == "true"

    def is_publish_telegram_enabled(self) -> bool:
        return self.get_config("publish_telegram") == "true"

    # ── Wallets ────────────────────────────────────────────

    def get_wallet(self, address: str) -> dict | None:
        """Fetch a wallet by address."""
        resp = (
            self.client.table("wallets")
            .select("*")
            .eq("address", address)
            .single()
            .execute()
        )
        return resp.data

    def get_all_wallets(self, limit: int = 1000) -> list[dict]:
        """Fetch all wallets (paginated)."""
        resp = self.client.table("wallets").select("*").limit(limit).execute()
        return resp.data or []

    def upsert_wallet(self, wallet: Wallet) -> None:
        """Insert or update a wallet."""
        data = _serialize(asdict(wallet))
        self.client.table("wallets").upsert(data).execute()

    def insert_wallet(self, wallet: Wallet) -> None:
        """Insert a new wallet."""
        data = _serialize(asdict(wallet))
        self.client.table("wallets").insert(data).execute()

    def delete_wallet(self, address: str) -> None:
        """Delete a wallet by address."""
        self.client.table("wallets").delete().eq("address", address).execute()

    # ── Markets ────────────────────────────────────────────

    def get_market(self, market_id: str) -> dict | None:
        resp = (
            self.client.table("markets")
            .select("*")
            .eq("market_id", market_id)
            .single()
            .execute()
        )
        return resp.data

    def get_all_markets(self, limit: int = 1000) -> list[dict]:
        resp = self.client.table("markets").select("*").limit(limit).execute()
        return resp.data or []

    def upsert_market(self, market: Market) -> None:
        data = _serialize(asdict(market))
        self.client.table("markets").upsert(data).execute()

    def insert_market(self, market: Market) -> None:
        data = _serialize(asdict(market))
        self.client.table("markets").insert(data).execute()

    def delete_market(self, market_id: str) -> None:
        self.client.table("markets").delete().eq("market_id", market_id).execute()

    # ── Market Snapshots ─────────────────────────────────

    def insert_market_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Insert a point-in-time market snapshot for odds history."""
        data = _serialize(asdict(snapshot))
        data.pop("id", None)
        self.client.table("market_snapshots").insert(data).execute()

    def get_market_snapshots(
        self, market_id: str, hours: int = 72
    ) -> list[dict]:
        """Fetch recent snapshots for a market, ordered by timestamp asc."""
        from datetime import datetime, timedelta, timezone

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()
        resp = (
            self.client.table("market_snapshots")
            .select("*")
            .eq("market_id", market_id)
            .gte("timestamp", cutoff)
            .order("timestamp", desc=False)
            .execute()
        )
        return resp.data or []

    # ── Alerts ─────────────────────────────────────────────

    def insert_alert(self, alert: Alert) -> int | None:
        """Insert an alert and return its ID."""
        data = _serialize(asdict(alert))
        data.pop("id", None)
        resp = self.client.table("alerts").insert(data).execute()
        if resp.data:
            return resp.data[0].get("id")
        return None

    def get_alert(self, alert_id: int) -> dict | None:
        resp = (
            self.client.table("alerts")
            .select("*")
            .eq("id", alert_id)
            .single()
            .execute()
        )
        return resp.data

    def get_all_alerts(self, limit: int = 1000) -> list[dict]:
        resp = self.client.table("alerts").select("*").limit(limit).execute()
        return resp.data or []

    def update_alert_published(
        self, alert_id: int, platform: str, msg_id: str
    ) -> None:
        """Mark an alert as published on X or Telegram."""
        update = {}
        if platform == "x":
            update = {"published_x": True, "tweet_id": msg_id}
        elif platform == "telegram":
            update = {"published_telegram": True, "telegram_msg_id": msg_id}
        self.client.table("alerts").update(update).eq("id", alert_id).execute()

    def get_alerts_pending(self) -> list[dict]:
        """Get all alerts with outcome='pending'."""
        resp = (
            self.client.table("alerts")
            .select("*")
            .eq("outcome", "pending")
            .execute()
        )
        return resp.data or []

    def delete_alert(self, alert_id: int) -> None:
        self.client.table("alerts").delete().eq("id", alert_id).execute()

    # ── Wallet Funding ─────────────────────────────────────

    def insert_funding(self, funding: WalletFunding) -> None:
        data = _serialize(asdict(funding))
        data.pop("id", None)
        self.client.table("wallet_funding").insert(data).execute()

    def get_funding_sources(self, wallet_address: str) -> list[dict]:
        resp = (
            self.client.table("wallet_funding")
            .select("*")
            .eq("wallet_address", wallet_address)
            .execute()
        )
        return resp.data or []

    def get_all_funding(self, limit: int = 1000) -> list[dict]:
        resp = self.client.table("wallet_funding").select("*").limit(limit).execute()
        return resp.data or []

    def delete_funding(self, funding_id: int) -> None:
        self.client.table("wallet_funding").delete().eq("id", funding_id).execute()

    # ── Scans ──────────────────────────────────────────────

    def insert_scan(self, scan: Scan) -> None:
        data = _serialize(asdict(scan))
        data.pop("id", None)
        self.client.table("scans").insert(data).execute()

    def get_all_scans(self, limit: int = 100) -> list[dict]:
        resp = (
            self.client.table("scans")
            .select("*")
            .order("timestamp", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    def delete_scan(self, scan_id: int) -> None:
        self.client.table("scans").delete().eq("id", scan_id).execute()

    # ── Reports ────────────────────────────────────────────

    def insert_weekly_report(self, report: WeeklyReport) -> None:
        data = _serialize(asdict(report))
        data.pop("id", None)
        self.client.table("weekly_reports").insert(data).execute()

    def get_all_weekly_reports(self, limit: int = 100) -> list[dict]:
        resp = (
            self.client.table("weekly_reports")
            .select("*")
            .order("week_start", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    def delete_weekly_report(self, report_id: int) -> None:
        self.client.table("weekly_reports").delete().eq("id", report_id).execute()

    # ── Leaderboard ────────────────────────────────────────

    def upsert_leaderboard(self, entry: SmartMoneyLeaderboard) -> None:
        data = _serialize(asdict(entry))
        self.client.table("smart_money_leaderboard").upsert(data).execute()

    def get_all_leaderboard(self, limit: int = 100) -> list[dict]:
        resp = (
            self.client.table("smart_money_leaderboard")
            .select("*")
            .order("estimated_pnl", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    def delete_leaderboard_entry(self, address: str) -> None:
        self.client.table("smart_money_leaderboard").delete().eq(
            "address", address
        ).execute()

    # ── Utility ────────────────────────────────────────────

    def get_tweets_today_count(self) -> int:
        """Count alerts published to X today (for daily limit)."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        resp = (
            self.client.table("alerts")
            .select("id", count="exact")
            .eq("published_x", True)
            .gte("created_at", f"{today}T00:00:00")
            .execute()
        )
        return resp.count or 0
