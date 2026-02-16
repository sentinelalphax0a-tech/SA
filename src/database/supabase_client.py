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
    AlertTracking,
    WalletFunding,
    WalletPosition,
    WalletCategory,
    SellEvent,
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

    def update_wallet_stats(self, address: str, won: bool) -> None:
        """Increment markets_won or markets_lost and recalculate win_rate."""
        wallet = self.get_wallet(address)
        if not wallet:
            return

        markets_won = wallet.get("markets_won", 0)
        markets_lost = wallet.get("markets_lost", 0)

        if won:
            markets_won += 1
        else:
            markets_lost += 1

        total = markets_won + markets_lost
        win_rate = markets_won / total if total > 0 else 0.0

        self.client.table("wallets").update({
            "markets_won": markets_won,
            "markets_lost": markets_lost,
            "win_rate": round(win_rate, 4),
        }).eq("address", address).execute()

    # ── Markets ────────────────────────────────────────────

    def get_market(self, market_id: str) -> dict | None:
        resp = (
            self.client.table("markets")
            .select("*")
            .eq("market_id", market_id)
            .maybe_single()
            .execute()
        )
        if resp is None or resp.data is None:
            return None
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

    def update_market_resolution(self, market_id: str, outcome: str) -> None:
        """Mark a market as resolved with the given outcome (YES/NO)."""
        self.client.table("markets").update({
            "is_resolved": True,
            "outcome": outcome,
        }).eq("market_id", market_id).execute()

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
        data.pop("deduplicated", None)  # in-memory only, not in DB schema
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

    def update_alert_fields(self, alert_id: int, fields: dict) -> None:
        """Update specific fields on an existing alert row."""
        if not fields:
            return
        self.client.table("alerts").update(fields).eq("id", alert_id).execute()

    def get_alerts_pending(self) -> list[dict]:
        """Get all alerts with outcome='pending'."""
        resp = (
            self.client.table("alerts")
            .select("*")
            .eq("outcome", "pending")
            .execute()
        )
        return resp.data or []

    def get_recent_alerts_for_market(
        self, market_id: str, direction: str, hours: int = 24
    ) -> list[dict]:
        """Fetch recent alerts for a market+direction within the last N hours.

        Used for cross-scan deduplication. Returns raw dicts including
        the JSONB `wallets` field for client-side primary wallet matching.
        """
        from datetime import datetime, timedelta, timezone

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()
        resp = (
            self.client.table("alerts")
            .select("id,market_id,direction,wallets,odds_at_alert,total_amount,score")
            .eq("market_id", market_id)
            .eq("direction", direction)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
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

    def insert_funding_batch(
        self, fundings: list[WalletFunding], batch_size: int = 100
    ) -> int:
        """Insert multiple funding records in batches using upsert.

        Returns the number of records successfully inserted.
        """
        if not fundings:
            return 0

        rows = []
        for f in fundings:
            d = _serialize(asdict(f))
            d.pop("id", None)
            rows.append(d)

        inserted = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            try:
                self.client.table("wallet_funding").upsert(batch).execute()
                inserted += len(batch)
            except Exception as e:
                logger.warning("Batch funding insert failed (%d rows), falling back: %s", len(batch), e)
                for row in batch:
                    try:
                        self.client.table("wallet_funding").insert(row).execute()
                        inserted += 1
                    except Exception:
                        pass  # duplicate or other error
        return inserted

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

    # ── Alert Tracking ────────────────────────────────────

    def upsert_alert_tracking(self, tracking: AlertTracking) -> None:
        """Insert or update an alert tracking record."""
        data = _serialize(asdict(tracking))
        data.pop("id", None)
        self.client.table("alert_tracking").upsert(data).execute()

    def get_pending_alert_trackings(self) -> list[dict]:
        """Get all alert_tracking rows with outcome='pending'."""
        resp = (
            self.client.table("alert_tracking")
            .select("*")
            .eq("outcome", "pending")
            .execute()
        )
        return resp.data or []

    def update_alert_tracking_outcome(
        self, alert_id: int, outcome: str, resolved_at: datetime | None = None
    ) -> None:
        """Update the outcome of an alert tracking record."""
        update: dict = {"outcome": outcome}
        if resolved_at:
            update["resolved_at"] = resolved_at.isoformat()
        self.client.table("alert_tracking").update(update).eq(
            "alert_id", alert_id
        ).execute()

    # ── Wallet Positions ──────────────────────────────────

    def upsert_wallet_position(self, pos: WalletPosition) -> None:
        """Insert or update a wallet position."""
        data = _serialize(asdict(pos))
        data.pop("id", None)
        self.client.table("wallet_positions").upsert(data).execute()

    def get_open_positions(
        self,
        market_id: str | None = None,
        wallet_address: str | None = None,
    ) -> list[dict]:
        """Get open positions, optionally filtered by market or wallet."""
        query = (
            self.client.table("wallet_positions")
            .select("*")
            .eq("current_status", "open")
        )
        if market_id:
            query = query.eq("market_id", market_id)
        if wallet_address:
            query = query.eq("wallet_address", wallet_address)
        resp = query.execute()
        return resp.data or []

    def update_position_sold(
        self,
        wallet_address: str,
        market_id: str,
        sell_amount: float,
        sell_timestamp: datetime,
    ) -> None:
        """Mark a position as sold or partially sold."""
        status = "sold" if sell_amount > 0 else "partial_sold"
        self.client.table("wallet_positions").update({
            "current_status": status,
            "sell_amount": sell_amount,
            "sell_timestamp": sell_timestamp.isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("wallet_address", wallet_address).eq(
            "market_id", market_id
        ).execute()

    # ── Wallet Categories ─────────────────────────────────

    def upsert_wallet_category(self, cat: WalletCategory) -> None:
        """Insert or update a wallet category record."""
        data = _serialize(asdict(cat))
        self.client.table("wallet_categories").upsert(data).execute()

    def get_wallet_category(self, wallet_address: str) -> dict | None:
        """Fetch a wallet category record."""
        try:
            resp = (
                self.client.table("wallet_categories")
                .select("*")
                .eq("wallet_address", wallet_address)
                .single()
                .execute()
            )
            return resp.data
        except Exception:
            return None

    def get_wallet_categories_by_type(self, category: str) -> list[dict]:
        """Fetch all wallet category records of a given type."""
        resp = (
            self.client.table("wallet_categories")
            .select("*")
            .eq("category", category)
            .execute()
        )
        return resp.data or []

    # ── Notification Log ──────────────────────────────────

    def get_recently_resolved(self, hours: int = 6) -> list[dict]:
        """Get alerts resolved within the last N hours."""
        from datetime import timedelta, timezone

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()
        resp = (
            self.client.table("alerts")
            .select("*")
            .neq("outcome", "pending")
            .gte("resolved_at", cutoff)
            .order("resolved_at", desc=True)
            .execute()
        )
        return resp.data or []

    def get_notification_log(self, alert_id: int) -> dict | None:
        """Get the notification log entry for an alert."""
        try:
            resp = (
                self.client.table("notification_log")
                .select("*")
                .eq("alert_id", alert_id)
                .order("last_notified_at", desc=True)
                .limit(1)
                .execute()
            )
            return resp.data[0] if resp.data else None
        except Exception:
            return None

    def log_notification(self, alert_id: int, notification_type: str) -> None:
        """Record that a notification was sent for an alert."""
        from datetime import timezone

        now = datetime.now(timezone.utc).isoformat()
        self.client.table("notification_log").upsert({
            "alert_id": alert_id,
            "notification_type": notification_type,
            "last_notified_at": now,
        }).execute()

    # ── Alert Consolidation ─────────────────────────────────

    def get_existing_high_star_alert(
        self,
        market_id: str,
        direction: str,
        min_stars: int = 4,
        hours: int = 48,
    ) -> dict | None:
        """Find an existing pending high-star alert for consolidation."""
        from datetime import timedelta, timezone

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()
        resp = (
            self.client.table("alerts")
            .select("*")
            .eq("market_id", market_id)
            .eq("direction", direction)
            .eq("outcome", "pending")
            .gte("star_level", min_stars)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def update_alert_consolidation(
        self,
        alert_id: int,
        new_wallets: list[dict],
        new_amount: float,
        new_score: int | None = None,
    ) -> None:
        """Merge new wallets into an existing alert (high-star consolidation)."""
        from datetime import timezone

        alert = self.get_alert(alert_id)
        if not alert:
            return

        existing_wallets = alert.get("wallets") or []
        merged_wallets = existing_wallets + new_wallets

        fields = {
            "wallets": merged_wallets,
            "total_amount": (alert.get("total_amount") or 0) + new_amount,
            "confluence_count": len(merged_wallets),
            "updated_count": (alert.get("updated_count") or 0) + 1,
            "last_updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if new_score is not None:
            fields["score"] = new_score

        self.client.table("alerts").update(fields).eq("id", alert_id).execute()

    # ── Whale Monitor ─────────────────────────────────────

    def get_high_star_alerts(self, min_stars: int = 4) -> list[dict]:
        """Get pending alerts with star_level >= min_stars."""
        resp = (
            self.client.table("alerts")
            .select("*")
            .eq("outcome", "pending")
            .gte("star_level", min_stars)
            .execute()
        )
        return resp.data or []

    def get_whale_notifications(self, alert_id: int) -> list[dict]:
        """Get whale notification log entries for an alert."""
        try:
            resp = (
                self.client.table("whale_notifications")
                .select("*")
                .eq("alert_id", alert_id)
                .execute()
            )
            return resp.data or []
        except Exception:
            return []

    def log_whale_notification(
        self,
        alert_id: int,
        event_type: str,
        wallet_address: str,
        details: dict,
    ) -> None:
        """Record that a whale notification was sent."""
        from datetime import timezone

        now = datetime.now(timezone.utc).isoformat()
        self.client.table("whale_notifications").insert({
            "alert_id": alert_id,
            "event_type": event_type,
            "wallet_address": wallet_address,
            "details": details,
            "created_at": now,
        }).execute()

    def get_recent_alerts_with_wallet(
        self, wallet_address: str, hours: int = 6
    ) -> list[dict]:
        """Get recent alerts that contain a specific wallet address.

        Searches the JSONB wallets column for matching addresses.
        """
        from datetime import timedelta, timezone

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()
        resp = (
            self.client.table("alerts")
            .select("*")
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .execute()
        )
        rows = resp.data or []
        # Client-side filter: Supabase JSONB containment queries are limited,
        # so we filter the wallets array in Python.
        result = []
        for row in rows:
            wallets = row.get("wallets") or []
            for w in wallets:
                if w.get("address") == wallet_address:
                    result.append(row)
                    break
        return result

    # ── Sell Events (Sell Watch) ─────────────────────────

    def insert_sell_event(self, event: SellEvent) -> int | None:
        """Insert a sell event into alert_sell_events. Returns the row ID."""
        data = _serialize(asdict(event))
        data.pop("id", None)
        try:
            resp = self.client.table("alert_sell_events").insert(data).execute()
            if resp.data:
                return resp.data[0].get("id")
        except Exception as e:
            logger.error("Failed to insert sell event for alert #%s: %s", event.alert_id, e)
        return None

    def get_sell_events_for_alert(self, alert_id: int) -> list[dict]:
        """Fetch all sell events for a given alert."""
        resp = (
            self.client.table("alert_sell_events")
            .select("*")
            .eq("alert_id", alert_id)
            .order("detected_at", desc=True)
            .execute()
        )
        return resp.data or []

    def get_recent_sell_events(self, hours: int = 168) -> list[dict]:
        """Fetch recent sell events (default 7 days) for dashboard."""
        from datetime import timedelta, timezone

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()
        resp = (
            self.client.table("alert_sell_events")
            .select("*")
            .gte("detected_at", cutoff)
            .order("detected_at", desc=True)
            .limit(50)
            .execute()
        )
        return resp.data or []

    def update_alert_sell_metadata(
        self, alert_id: int, total_sold_pct: float
    ) -> None:
        """Update sell metadata on the alert row. Does NOT touch star_level."""
        from datetime import timezone

        now = datetime.now(timezone.utc).isoformat()
        self.client.table("alerts").update({
            "total_sold_pct": total_sold_pct,
            "last_sell_detected_at": now,
        }).eq("id", alert_id).execute()

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
