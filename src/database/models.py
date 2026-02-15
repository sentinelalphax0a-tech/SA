"""
Sentinel Alpha — Data Models.

Dataclasses matching the Supabase schema for type-safe data handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any


# ============================================================
# WALLET
# ============================================================

@dataclass
class Wallet:
    address: str
    first_seen: datetime = field(default_factory=datetime.utcnow)
    wallet_age_days: int | None = None
    category: str = "unknown"
    total_markets: int = 0
    total_markets_pm: int = 0
    non_pm_markets: int = 0
    markets_won: int = 0
    markets_lost: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    times_detected: int = 0
    last_activity: datetime | None = None
    origin_exchange: str | None = None
    funding_sources: list[dict[str, Any]] | None = None
    is_first_tx_pm: bool = False
    is_blacklisted: bool = False
    blacklist_reason: str | None = None
    degen_score: int = 0
    notes: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================
# MARKET
# ============================================================

@dataclass
class Market:
    market_id: str
    question: str
    slug: str | None = None
    category: str | None = None
    current_odds: float | None = None
    volume_24h: float = 0.0
    volume_7d_avg: float = 0.0
    liquidity: float = 0.0
    resolution_date: datetime | None = None
    is_resolved: bool = False
    outcome: str | None = None
    is_active: bool = True
    opposite_market: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================
# ALERT
# ============================================================

@dataclass
class Alert:
    market_id: str
    alert_type: str
    score: int
    id: int | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    market_question: str | None = None
    direction: str | None = None
    score_raw: int | None = None
    multiplier: float = 1.0
    star_level: int | None = None
    wallets: list[dict[str, Any]] | None = None
    total_amount: float | None = None
    odds_at_alert: float | None = None
    price_impact: float | None = None
    confluence_count: int = 1
    confluence_type: str | None = None
    has_news: bool = False
    news_summary: str | None = None
    is_arbitrage: bool = False
    opposite_positions: list[dict[str, Any]] | None = None
    filters_triggered: list[dict[str, Any]] | None = None
    published_x: bool = False
    published_telegram: bool = False
    tweet_id: str | None = None
    telegram_msg_id: str | None = None
    deduplicated: bool = False
    outcome: str = "pending"
    resolved_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================
# WALLET FUNDING
# ============================================================

@dataclass
class WalletFunding:
    wallet_address: str
    sender_address: str
    id: int | None = None
    amount: float | None = None
    timestamp: datetime | None = None
    hop_level: int = 1
    is_exchange: bool = False
    exchange_name: str | None = None
    is_bridge: bool = False
    bridge_name: str | None = None
    is_mixer: bool = False
    mixer_name: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================
# SCAN LOG
# ============================================================

@dataclass
class Scan:
    id: int | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    markets_scanned: int = 0
    transactions_analyzed: int = 0
    wallets_analyzed: int = 0
    alerts_generated: int = 0
    alerts_published_x: int = 0
    alerts_published_tg: int = 0
    duration_seconds: float | None = None
    errors: str | None = None
    status: str = "success"


# ============================================================
# WEEKLY REPORT
# ============================================================

@dataclass
class WeeklyReport:
    week_start: date
    week_end: date
    id: int | None = None
    total_alerts: int = 0
    alerts_by_stars: dict[str, int] | None = None
    alerts_correct: int = 0
    alerts_incorrect: int = 0
    alerts_pending: int = 0
    accuracy_rate: float | None = None
    accuracy_by_stars: dict[str, float] | None = None
    top_markets: list[dict[str, Any]] | None = None
    top_wallets: list[dict[str, Any]] | None = None
    chart_url: str | None = None
    tweet_id: str | None = None
    telegram_msg_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================
# SMART MONEY LEADERBOARD
# ============================================================

@dataclass
class SmartMoneyLeaderboard:
    address: str
    markets_entered: int = 0
    markets_won: int = 0
    markets_lost: int = 0
    win_rate: float = 0.0
    total_invested: float = 0.0
    estimated_pnl: float = 0.0
    avg_entry_odds: float | None = None
    best_trade: dict[str, Any] | None = None
    last_seen: datetime | None = None
    updated_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================
# SYSTEM CONFIG
# ============================================================

@dataclass
class SystemConfig:
    key: str
    value: str
    updated_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================
# RUNTIME / INTERMEDIATE MODELS (not persisted directly)
# ============================================================

@dataclass
class FilterResult:
    """Result of evaluating a single filter against a wallet/market."""
    filter_id: str
    filter_name: str
    points: int
    category: str
    details: str | None = None


@dataclass
class ScoringResult:
    """Final scoring output for an alert candidate."""
    score_raw: int
    multiplier: float
    score_final: int
    star_level: int
    filters_triggered: list[FilterResult] = field(default_factory=list)
    multiplier_pattern: str | None = None


@dataclass
class TradeEvent:
    """A single trade from the Polymarket CLOB API."""
    wallet_address: str
    market_id: str
    direction: str  # "YES" or "NO"
    amount: float
    price: float
    timestamp: datetime
    is_market_order: bool = False
    tx_hash: str | None = None


@dataclass
class AccumulationWindow:
    """Aggregated accumulation data for a wallet in a market."""
    wallet_address: str
    market_id: str
    direction: str
    total_amount: float
    trade_count: int
    first_trade: datetime
    last_trade: datetime
    trades: list[TradeEvent] = field(default_factory=list)


@dataclass
class MarketSnapshot:
    """Point-in-time snapshot of a market's odds/volume/liquidity."""
    market_id: str
    odds: float
    volume_24h: float = 0.0
    liquidity: float = 0.0
    id: int | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class FundingLink:
    """A funding connection between wallets."""
    sender: str
    funded_wallets: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0
    time_spread_hours: float = 0.0
    similar_amounts: bool = False
    is_distribution: bool = False


# ============================================================
# ALERT TRACKING (resolution tracking)
# ============================================================

@dataclass
class AlertTracking:
    """Tracks an alert's outcome for resolution checks."""
    alert_id: int
    market_id: str
    direction: str
    odds_at_alert: float
    id: int | None = None
    current_odds: float | None = None
    outcome: str = "pending"  # pending | correct | incorrect
    resolved_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================
# WALLET POSITION (sell monitoring)
# ============================================================

@dataclass
class WalletPosition:
    """Tracks a wallet's open position in a market."""
    wallet_address: str
    market_id: str
    direction: str
    total_amount: float
    entry_odds: float
    id: int | None = None
    current_status: str = "open"  # open | sold | partial_sold
    sell_amount: float = 0.0
    sell_timestamp: datetime | None = None
    alert_id: int | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================
# WALLET CATEGORY (win rate + specialization tracking)
# ============================================================

@dataclass
class WalletCategory:
    """Tracks wallet performance and specialization."""
    wallet_address: str
    category: str = "unknown"  # unknown | smart_money | whale | scalper | bot | degen
    win_rate: float | None = None
    markets_resolved: int = 0
    markets_won: int = 0
    specialty_tags: list[str] | None = None
    total_tracked: float = 0.0
    updated_at: datetime = field(default_factory=datetime.utcnow)
