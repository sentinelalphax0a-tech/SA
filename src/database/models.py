"""
Sentinel Alpha — Data Models.

Dataclasses matching the Supabase schema for type-safe data handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from typing import Any


# ============================================================
# WALLET
# ============================================================

@dataclass
class Wallet:
    address: str
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================
# ALERT
# ============================================================

@dataclass
class Alert:
    market_id: str
    alert_type: str
    score: int
    id: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    market_question: str | None = None
    direction: str | None = None
    score_raw: int | None = None
    multiplier: float = 1.0
    star_level: int | None = None
    # Immutable: star level at first detection (ML training label).
    # Set once on insert and never updated by cross-scan dedup or vacunas.
    star_level_initial: int | None = None
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
    # ── ML snapshot (T0 state — set once at insert, never updated by dedup) ──
    scan_mode: str | None = None                              # "quick" or "deep"
    score_initial: int | None = None                          # score at first detection
    score_raw_initial: int | None = None                      # raw score at first detection
    odds_at_alert_initial: float | None = None                # market odds at first detection
    total_amount_initial: float | None = None                 # total buy amount at first detection
    filters_triggered_initial: list[dict[str, Any]] | None = None  # filters at first detection
    market_category: str | None = None                        # market category at first detection
    market_volume_24h_at_alert: float | None = None           # 24h volume at first detection
    market_liquidity_at_alert: float | None = None            # liquidity at first detection
    hours_to_deadline: float | None = None                    # hours until resolution at first detection
    wallets_count_initial: int | None = None                  # number of wallets at first detection
    outcome: str = "pending"
    resolved_at: datetime | None = None
    total_sold_pct: float = 0.0                  # cumulative % sold across all wallets
    last_sell_detected_at: datetime | None = None
    # ── Multi-signal grouping ──
    multi_signal: bool = False              # True when 2+ independent groups bet same direction
    is_secondary: bool = False              # True for non-primary alerts in a group
    alert_group_id: str | None = None       # Shared UUID across alerts from same market scan
    secondary_count: int = 0                # Number of secondary alerts (set on primary)
    # ── Merge detection ──
    # merge_suspected: wallet bought both YES and NO of same market (CLOB arbitrage).
    # Comparison is in tokens/shares (not dollars). Does NOT capture CTF-layer merges
    # (those are invisible to the CLOB API by design). Label for ML, not final diagnosis.
    merge_suspected: bool = False
    merge_confirmed: bool = False           # set by check_merge_resolution() when net<$500
    scoring_version: str | None = None     # "v1"=mixed-direction bug, "v2"=fixed
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================
# SCAN LOG
# ============================================================

@dataclass
class Scan:
    id: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================
# SYSTEM CONFIG
# ============================================================

@dataclass
class SystemConfig:
    key: str
    value: str
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    # close_reason: ML label for how the position closed. Not a definitive diagnosis.
    # Values: 'sell_clob' | 'merge_suspected' | 'net_zero' | 'position_gone'
    # 'position_gone' captures CTF merges, transfers, burns — any exit not visible in CLOB.
    close_reason: str | None = None
    # hold_duration_hours: hours between alert creation and sell detection.
    # Populated by SellDetector on CLOB sell. Tracking only — no scoring impact.
    hold_duration_hours: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================
# SELL EVENT (metadata-only position exit tracking)
# ============================================================

@dataclass
class SellEvent:
    """Records a sell detected for a monitored alert wallet."""
    alert_id: int
    wallet_address: str
    sell_amount: float
    sell_pct: float                         # fraction of original position sold
    event_type: str                         # "FULL_EXIT" or "PARTIAL_EXIT"
    id: int | None = None
    sell_price: float | None = None
    original_entry_price: float | None = None
    position_remaining_pct: float = 0.0
    pnl_pct: float | None = None
    held_hours: float | None = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sell_timestamp: datetime | None = None


# ============================================================
# BOT TRADES (bot execution tracking)
# ============================================================

@dataclass
class BotTrade:
    """Records a bot order (paper or live) linked to an alert.

    paper_trade=True  → shadow / simulation mode (default, safe)
    paper_trade=False → live order placed on Polymarket

    Status lifecycle: open → closed_win | closed_loss | cancelled | expired
    """
    alert_id: int
    market_id: str
    direction: str                # "YES" or "NO"
    entry_odds: float
    stake: float
    paper_trade: bool = True      # always default to shadow mode
    id: int | None = None
    status: str = "open"          # open | closed_win | closed_loss | cancelled | expired
    pnl: float | None = None
    pnl_pct: float | None = None
    exit_odds: float | None = None
    exit_reason: str | None = None  # market_resolved | manual | stop_loss | take_profit
    polymarket_order_id: str | None = None
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None
    notes: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
