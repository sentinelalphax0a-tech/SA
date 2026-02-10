"""
Sentinel Alpha — Configuration & Constants.

All thresholds, filter definitions, scoring multipliers,
odds ranges, and system-wide constants.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# EXTERNAL SERVICES
# ============================================================

# Supabase
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")

# Alchemy (Polygon RPC)
ALCHEMY_API_KEY: str = os.getenv("ALCHEMY_API_KEY", "")
ALCHEMY_ENDPOINT: str = os.getenv(
    "ALCHEMY_ENDPOINT",
    f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
)

# Twitter / X
TWITTER_API_KEY: str = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET: str = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN: str = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET: str = os.getenv("TWITTER_ACCESS_SECRET", "")

# Telegram
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "")

# ============================================================
# SCAN & INGESTION THRESHOLDS
# ============================================================

MIN_TX_AMOUNT: float = float(os.getenv("MIN_TX_AMOUNT", "100"))
MIN_ACCUMULATED_AMOUNT: float = float(os.getenv("MIN_ACCUMULATED_AMOUNT", "350"))
ACCUMULATION_WINDOW_HOURS: int = 72
ACCUMULATION_WINDOW_DAYS_7: int = 7
ACCUMULATION_WINDOW_DAYS_14: int = 14
SCAN_INTERVAL_MINUTES: int = int(os.getenv("SCAN_INTERVAL_MINUTES", "30"))
SCAN_LOOKBACK_MINUTES: int = 35  # slightly > interval to avoid gaps

# ============================================================
# ODDS RANGE
# ============================================================

ODDS_MIN: float = float(os.getenv("ODDS_MIN", "0.05"))
ODDS_MAX: float = float(os.getenv("ODDS_MAX", "0.55"))
ODDS_MAX_EXTENDED: float = float(os.getenv("ODDS_MAX_EXTENDED", "0.70"))
ODDS_EXTENDED_MIN_SCORE: int = 90  # score >= 90 to use extended range

# ============================================================
# PUBLISHING THRESHOLDS
# ============================================================

PUBLISH_SCORE_THRESHOLD_X: int = int(os.getenv("PUBLISH_SCORE_THRESHOLD_X", "70"))
PUBLISH_SCORE_THRESHOLD_TELEGRAM: int = int(
    os.getenv("PUBLISH_SCORE_THRESHOLD_TELEGRAM", "50")
)
MAX_TWEETS_PER_DAY: int = int(os.getenv("MAX_TWEETS_PER_DAY", "10"))

# ============================================================
# STAR LEVELS
# ============================================================

STAR_THRESHOLDS: list[tuple[int, int]] = [
    # (min_score, star_level) — evaluated top-down
    (120, 5),
    (90, 4),
    (70, 3),
    (50, 2),
    (30, 1),
    (0, 0),
]

# Where each star level publishes
STAR_PUBLISH_MAP: dict[int, dict[str, bool]] = {
    0: {"db": True, "web": False, "telegram": False, "x": False},
    1: {"db": True, "web": True, "telegram": False, "x": False},
    2: {"db": True, "web": True, "telegram": True, "x": False},
    3: {"db": True, "web": True, "telegram": True, "x": True},
    4: {"db": True, "web": True, "telegram": True, "x": True},
    5: {"db": True, "web": True, "telegram": True, "x": True},
}

# ============================================================
# FILTER DEFINITIONS — 42 filters
# Each filter: (id, name, points, category)
# ============================================================

# --- Wallet filters (W) — 7 filters ---
FILTER_W01 = {"id": "W01", "name": "Wallet muy nueva", "points": 25, "category": "wallet"}
FILTER_W02 = {"id": "W02", "name": "Wallet nueva", "points": 20, "category": "wallet"}
FILTER_W03 = {"id": "W03", "name": "Wallet reciente", "points": 15, "category": "wallet"}
FILTER_W04 = {"id": "W04", "name": "Solo 1 mercado", "points": 25, "category": "wallet"}
FILTER_W05 = {"id": "W05", "name": "Solo 2-3 mercados", "points": 15, "category": "wallet"}
FILTER_W09 = {"id": "W09", "name": "Primera tx = Polymarket", "points": 20, "category": "wallet"}
FILTER_W11 = {"id": "W11", "name": "Balance redondo", "points": 3, "category": "wallet"}

# --- Origin filters (O) — 3 filters ---
FILTER_O01 = {"id": "O01", "name": "Origen exchange", "points": 15, "category": "origin"}
FILTER_O02 = {"id": "O02", "name": "Fondeo reciente", "points": 10, "category": "origin"}
FILTER_O03 = {"id": "O03", "name": "Fondeo muy reciente", "points": 15, "category": "origin"}

# --- Behavior filters (B) — 14 filters ---
FILTER_B01 = {"id": "B01", "name": "Acumulación goteo", "points": 20, "category": "behavior"}
FILTER_B05 = {"id": "B05", "name": "Solo market orders", "points": 5, "category": "behavior"}
FILTER_B06 = {"id": "B06", "name": "Tamaño creciente", "points": 15, "category": "behavior"}
FILTER_B07 = {"id": "B07", "name": "Compra contra mercado", "points": 20, "category": "behavior"}
FILTER_B14 = {"id": "B14", "name": "Primera compra grande", "points": 15, "category": "behavior"}
FILTER_B16 = {"id": "B16", "name": "Acumulación rápida", "points": 20, "category": "behavior"}
FILTER_B17 = {"id": "B17", "name": "Horario bajo", "points": 10, "category": "behavior"}
# Accumulation tiers (B18a-d mutually exclusive, B18e bonus)
FILTER_B18A = {"id": "B18a", "name": "Acumulación moderada", "points": 15, "category": "behavior"}
FILTER_B18B = {"id": "B18b", "name": "Acumulación significativa", "points": 25, "category": "behavior"}
FILTER_B18C = {"id": "B18c", "name": "Acumulación fuerte", "points": 35, "category": "behavior"}
FILTER_B18D = {"id": "B18d", "name": "Acumulación muy fuerte", "points": 50, "category": "behavior"}
FILTER_B18E = {"id": "B18e", "name": "Sin impacto en precio", "points": 15, "category": "behavior"}
# Whale entries (B19a-c mutually exclusive, Telegram only)
FILTER_B19A = {"id": "B19a", "name": "Entrada grande", "points": 20, "category": "behavior"}
FILTER_B19B = {"id": "B19b", "name": "Entrada muy grande", "points": 30, "category": "behavior"}
FILTER_B19C = {"id": "B19c", "name": "Entrada masiva", "points": 40, "category": "behavior"}
# Old wallet new in PM
FILTER_B20 = {"id": "B20", "name": "Vieja nueva en PM", "points": 20, "category": "behavior"}

# --- Confluence filters (C) — 7 filters ---
FILTER_C01 = {"id": "C01", "name": "Confluencia básica", "points": 25, "category": "confluence"}
FILTER_C02 = {"id": "C02", "name": "Confluencia fuerte", "points": 40, "category": "confluence"}
FILTER_C03 = {"id": "C03", "name": "Mismo intermediario", "points": 35, "category": "confluence"}
FILTER_C04 = {"id": "C04", "name": "Mismo intermediario + misma dir", "points": 50, "category": "confluence"}
FILTER_C05 = {"id": "C05", "name": "Fondeo temporal", "points": 30, "category": "confluence"}
FILTER_C06 = {"id": "C06", "name": "Monto similar", "points": 15, "category": "confluence"}
FILTER_C07 = {"id": "C07", "name": "Red de distribución", "points": 60, "category": "confluence"}

# --- Market filters (M) — 3 filters ---
FILTER_M01 = {"id": "M01", "name": "Volumen anómalo", "points": 15, "category": "market"}
FILTER_M02 = {"id": "M02", "name": "Odds estables rotos", "points": 20, "category": "market"}
FILTER_M03 = {"id": "M03", "name": "Baja liquidez", "points": 10, "category": "market"}

# --- Negative filters (N) — 8 filters ---
FILTER_N01 = {"id": "N01", "name": "Bot", "points": -40, "category": "negative"}
FILTER_N02 = {"id": "N02", "name": "Noticias", "points": -20, "category": "negative"}
FILTER_N03 = {"id": "N03", "name": "Arbitraje", "points": -100, "category": "negative"}
FILTER_N04 = {"id": "N04", "name": "Mercados opuestos", "points": 0, "category": "negative"}
FILTER_N05 = {"id": "N05", "name": "Copy-trading", "points": -25, "category": "negative"}
FILTER_N06A = {"id": "N06a", "name": "Degen leve", "points": -5, "category": "negative"}
FILTER_N06B = {"id": "N06b", "name": "Degen moderado", "points": -15, "category": "negative"}
FILTER_N06C = {"id": "N06c", "name": "Degen fuerte", "points": -30, "category": "negative"}

# Master registry: id → filter dict
ALL_FILTERS: dict[str, dict] = {
    f["id"]: f
    for f in [
        FILTER_W01, FILTER_W02, FILTER_W03, FILTER_W04, FILTER_W05,
        FILTER_W09, FILTER_W11,
        FILTER_O01, FILTER_O02, FILTER_O03,
        FILTER_B01, FILTER_B05, FILTER_B06, FILTER_B07, FILTER_B14,
        FILTER_B16, FILTER_B17,
        FILTER_B18A, FILTER_B18B, FILTER_B18C, FILTER_B18D, FILTER_B18E,
        FILTER_B19A, FILTER_B19B, FILTER_B19C,
        FILTER_B20,
        FILTER_C01, FILTER_C02, FILTER_C03, FILTER_C04, FILTER_C05,
        FILTER_C06, FILTER_C07,
        FILTER_M01, FILTER_M02, FILTER_M03,
        FILTER_N01, FILTER_N02, FILTER_N03, FILTER_N04, FILTER_N05,
        FILTER_N06A, FILTER_N06B, FILTER_N06C,
    ]
}

# Mutually exclusive filter groups — only highest scoring fires
MUTUALLY_EXCLUSIVE_GROUPS: list[list[str]] = [
    ["W01", "W02", "W03"],       # wallet age tiers
    ["W04", "W05"],              # market count tiers
    ["B18a", "B18b", "B18c", "B18d"],  # accumulation tiers
    ["B19a", "B19b", "B19c"],    # whale entry tiers
    ["C01", "C02"],              # confluence direction tiers
    ["C03", "C04"],              # funding source tiers
    ["N06a", "N06b", "N06c"],    # degen tiers
]

# Filters that trigger Telegram-only whale alerts (bypass normal routing)
WHALE_ENTRY_FILTERS: set[str] = {"B19a", "B19b", "B19c"}

# ============================================================
# FILTER CONDITION THRESHOLDS
# ============================================================

# Wallet age thresholds (days)
WALLET_AGE_VERY_NEW: int = 7       # W01
WALLET_AGE_NEW: int = 14           # W02
WALLET_AGE_RECENT: int = 30        # W03

# Origin thresholds
FUNDING_RECENCY_DAYS: int = 7      # O02
FUNDING_VERY_RECENT_DAYS: int = 3  # O03
MAX_FUNDING_HOPS: int = 2          # O01: 1-2 hops

# Behavior thresholds
DRIP_MIN_BUYS: int = 5             # B01: 5+ buys in 24-72h
AGAINST_MARKET_ODDS: float = 0.20  # B07: odds < 0.20
FIRST_BIG_BUY_AMOUNT: float = 5000.0  # B14: > $5,000
RAPID_ACCUMULATION_COUNT: int = 3  # B16: 3+ in < 4h
RAPID_ACCUMULATION_HOURS: int = 4  # B16
LOW_ACTIVITY_HOUR_START: int = 2   # B17: 2 AM UTC
LOW_ACTIVITY_HOUR_END: int = 6     # B17: 6 AM UTC

# Accumulation tiers (B18)
ACCUM_MODERATE_MIN: float = 2000.0   # B18a
ACCUM_MODERATE_MAX: float = 3499.0
ACCUM_SIGNIFICANT_MIN: float = 3500.0  # B18b
ACCUM_SIGNIFICANT_MAX: float = 4999.0
ACCUM_STRONG_MIN: float = 5000.0     # B18c
ACCUM_STRONG_MAX: float = 9999.0
ACCUM_VERY_STRONG_MIN: float = 10000.0  # B18d
ACCUM_NO_IMPACT_MIN: float = 2000.0  # B18e: accum > $2k
ACCUM_NO_IMPACT_MAX_MOVE: float = 0.05  # B18e: odds move < 5%

# Whale entry tiers (B19)
WHALE_LARGE_MIN: float = 5000.0      # B19a
WHALE_LARGE_MAX: float = 9999.0
WHALE_VERY_LARGE_MIN: float = 10000.0  # B19b
WHALE_VERY_LARGE_MAX: float = 49999.0
WHALE_MASSIVE_MIN: float = 50000.0    # B19c

# Old wallet new in PM (B20)
OLD_WALLET_MIN_AGE_DAYS: int = 180    # wallet > 180 days
OLD_WALLET_PM_MAX_DAYS: int = 7       # PM activity < 7 days

# Round balance detection (W11)
ROUND_BALANCES: list[float] = [5000.0, 10000.0, 50000.0]
ROUND_BALANCE_TOLERANCE: float = 0.01  # ±1%

# Confluence thresholds
CONFLUENCE_BASIC_MIN_WALLETS: int = 3     # C01: 3+ wallets
CONFLUENCE_BASIC_WINDOW_HOURS: int = 48
CONFLUENCE_STRONG_MIN_WALLETS: int = 5    # C02: 5+ wallets
FUNDING_CONFLUENCE_MIN_WALLETS: int = 2   # C03: 2+ share sender
FUNDING_TEMPORAL_MIN_WALLETS: int = 3     # C05: 3+ funded < 4h
FUNDING_TEMPORAL_HOURS: int = 4
FUNDING_SIMILAR_AMOUNT_TOLERANCE: float = 0.30  # C06: ±30%
DISTRIBUTION_MIN_WALLETS: int = 3         # C07: 1 → 3+ wallets

# Market thresholds
VOLUME_ANOMALY_MULTIPLIER: float = 2.0    # M01: vol 24h > 2x avg 7d
ODDS_STABLE_HOURS: int = 48              # M02: stable > 48h
ODDS_BREAK_THRESHOLD: float = 0.10       # M02: move > 10%
LOW_LIQUIDITY_THRESHOLD: float = 100000.0  # M03: < $100k

# Negative thresholds
BOT_INTERVAL_STD_THRESHOLD: float = 1.0   # N01: std_dev ≈ 0
NEWS_LOOKBACK_HOURS: int = 24             # N02: 24h
COPY_TRADE_MIN_DELAY_MIN: int = 2         # N05: 2-10 min after whale
COPY_TRADE_MAX_DELAY_MIN: int = 10

# Degen thresholds
DEGEN_LIGHT_MAX: int = 2      # N06a: 1-2 non-political markets
DEGEN_MODERATE_MAX: int = 5   # N06b: 3-5
DEGEN_HEAVY_MIN: int = 6      # N06c: 6+

# Known exchanges for origin detection (O01) — Polygon hot wallets
KNOWN_EXCHANGES: dict[str, str] = {
    # Coinbase
    "0x1a1ec25dc08e98e5e93f1104b5e5cdd298707d31": "Coinbase",
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase",
    "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740": "Coinbase",
    # Binance
    "0xe7804c37c13166ff0b37f5ae0bb07a3aebb6e245": "Binance",
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance",
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    # Kraken
    "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0": "Kraken",
    # OKX
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
    # Crypto.com
    "0x6262998ced04146fa42253a5c0af90ca02dfd2a3": "Crypto.com",
    # Gate.io
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": "Gate.io",
}

# ============================================================
# SCORING MULTIPLIER PATTERNS — 6 patterns
# ============================================================

MULTIPLIER_PATTERNS: list[dict] = [
    {
        "id": "P1",
        "name": "Insider clásico",
        "multiplier": 1.3,
        "required": {"W01", "W09", "O03"},
        "any_of": {"C01", "C02", "C04", "C07"},
    },
    {
        "id": "P2",
        "name": "Coordinación",
        "multiplier": 1.2,
        "required": {"C02", "M01"},
        "none_of": {"N02"},
    },
    {
        "id": "P3",
        "name": "Urgencia",
        "multiplier": 1.15,
        "min_count_from": {"B16", "B05", "B17"},
        "min_count": 2,
    },
    {
        "id": "P4",
        "name": "Fragmentación silenciosa",
        "multiplier": 1.25,
        "any_of": {"B18c", "B18d"},
        "required": {"B18e", "W04"},
    },
    {
        "id": "P5",
        "name": "Acumulación silenciosa",
        "multiplier": 1.3,
        "required": {"B18d", "B18e", "M02"},
    },
    {
        "id": "P6",
        "name": "Red de distribución",
        "multiplier": 1.4,
        "required": {"C07"},
        "any_of": {"W01", "W02", "W03"},
    },
]

# ============================================================
# ALERT TYPES
# ============================================================

ALERT_TYPE_ACCUMULATION = "accumulation"
ALERT_TYPE_CONFLUENCE = "confluence"
ALERT_TYPE_WHALE_ENTRY = "whale_entry"
ALERT_TYPE_DISTRIBUTION = "distribution"

# ============================================================
# POLYMARKET API
# ============================================================

POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

# Market categories to scan
MARKET_CATEGORIES: list[str] = ["politics", "economics", "geopolitics"]
