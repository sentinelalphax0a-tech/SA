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
TELEGRAM_PRIVATE_CHANNEL_ID: str = os.getenv("TELEGRAM_PRIVATE_CHANNEL_ID", "")
TELEGRAM_PUBLIC_CHANNEL_ID: str = os.getenv("TELEGRAM_PUBLIC_CHANNEL_ID", "")
TELEGRAM_VIP_CHANNEL_ID: str = os.getenv("TELEGRAM_VIP_CHANNEL_ID", "")

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
SCAN_TIMEOUT_SECONDS: int = int(os.getenv("SCAN_TIMEOUT_SECONDS", "480"))  # 8 min
MARKET_TIMEOUT_SECONDS: int = int(os.getenv("MARKET_TIMEOUT_SECONDS", "45"))  # per-market
MAX_WALLETS_PER_MARKET: int = int(os.getenv("MAX_WALLETS_PER_MARKET", "10"))  # top N by volume
MARKET_MIN_VOLUME_24H: float = float(os.getenv("MARKET_MIN_VOLUME_24H", "1000"))
MARKET_SCAN_CAP: int = int(os.getenv("MARKET_SCAN_CAP", "100"))
CROSS_SCAN_DEDUP_HOURS: int = int(os.getenv("CROSS_SCAN_DEDUP_HOURS", "24"))

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
FILTER_W04 = {"id": "W04", "name": "Solo 1 mercado", "points": 10, "category": "wallet"}
FILTER_W05 = {"id": "W05", "name": "Solo 2-3 mercados", "points": 15, "category": "wallet"}
FILTER_W09 = {"id": "W09", "name": "Primera tx = Polymarket", "points": 5, "category": "wallet"}
FILTER_W11 = {"id": "W11", "name": "Balance redondo", "points": 3, "category": "wallet"}

# --- Origin filters (O) — 3 filters ---
FILTER_O01 = {"id": "O01", "name": "Origen exchange", "points": 5, "category": "origin"}
FILTER_O02 = {"id": "O02", "name": "Fondeo reciente", "points": 10, "category": "origin"}
FILTER_O03 = {"id": "O03", "name": "Fondeo muy reciente", "points": 5, "category": "origin"}

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
# Position sizing (B23a-b mutually exclusive)
FILTER_B23A = {"id": "B23a", "name": "Posición significativa", "points": 15, "category": "behavior"}
FILTER_B23B = {"id": "B23b", "name": "Posición dominante", "points": 30, "category": "behavior"}
# Odds conviction (B25a-c mutually exclusive)
FILTER_B25A = {"id": "B25a", "name": "Convicción extrema", "points": 25, "category": "behavior"}
FILTER_B25B = {"id": "B25b", "name": "Convicción alta", "points": 15, "category": "behavior"}
FILTER_B25C = {"id": "B25c", "name": "Convicción moderada", "points": 5, "category": "behavior"}
# Stealth accumulation (B26a-b mutually exclusive, replaces B18e)
FILTER_B26A = {"id": "B26a", "name": "Stealth whale", "points": 20, "category": "behavior"}
FILTER_B26B = {"id": "B26b", "name": "Low impact", "points": 10, "category": "behavior"}
# Diamond hands (B27a-b mutually exclusive, disabled until sell_detector covers pre-alert)
FILTER_B27A = {"id": "B27a", "name": "Diamond hands 48h", "points": 15, "category": "behavior"}
FILTER_B27B = {"id": "B27b", "name": "Diamond hands 72h+", "points": 20, "category": "behavior"}
# All-in (B28a-b mutually exclusive with B23)
FILTER_B28A = {"id": "B28a", "name": "All-in extremo", "points": 25, "category": "behavior"}
FILTER_B28B = {"id": "B28b", "name": "All-in fuerte", "points": 20, "category": "behavior"}
# First mover (B30a-c mutually exclusive, disabled until trades table exists)
FILTER_B30A = {"id": "B30a", "name": "First mover", "points": 20, "category": "behavior"}
FILTER_B30B = {"id": "B30b", "name": "Early mover top 3", "points": 10, "category": "behavior"}
FILTER_B30C = {"id": "B30c", "name": "Early mover top 5", "points": 5, "category": "behavior"}

# --- Confluence filters (C) — Layer system ---
# Layer 1: Direction confluence (C01/C02 mutually exclusive)
FILTER_C01 = {"id": "C01", "name": "Confluencia básica", "points": 10, "category": "confluence"}
FILTER_C02 = {"id": "C02", "name": "Confluencia fuerte", "points": 15, "category": "confluence"}
# Layer 2: Origin type (C03a-d, additive, each fires independently)
FILTER_C03A = {"id": "C03a", "name": "Origen exchange compartido", "points": 5, "category": "confluence"}
FILTER_C03B = {"id": "C03b", "name": "Origen bridge compartido", "points": 20, "category": "confluence"}
FILTER_C03C = {"id": "C03c", "name": "Origen mixer compartido", "points": 30, "category": "confluence"}
FILTER_C03D = {"id": "C03d", "name": "Mismo padre directo", "points": 30, "category": "confluence"}
# Layer 3: Bonus (additive, stacks)
FILTER_C05 = {"id": "C05", "name": "Fondeo temporal", "points": 10, "category": "confluence"}
FILTER_C06 = {"id": "C06", "name": "Monto similar", "points": 10, "category": "confluence"}
# Layer 4: Distribution network
FILTER_C07 = {"id": "C07", "name": "Red de distribución", "points": 30, "category": "confluence"}

# --- Market filters (M) — 5 filters ---
FILTER_M01 = {"id": "M01", "name": "Volumen anómalo", "points": 15, "category": "market"}
FILTER_M02 = {"id": "M02", "name": "Odds estables rotos", "points": 20, "category": "market"}
FILTER_M03 = {"id": "M03", "name": "Baja liquidez", "points": 10, "category": "market"}
FILTER_M04A = {"id": "M04a", "name": "Concentración moderada", "points": 15, "category": "market"}
FILTER_M04B = {"id": "M04b", "name": "Concentración alta", "points": 25, "category": "market"}
FILTER_M05A = {"id": "M05a", "name": "Deadline <72h", "points": 10, "category": "market"}
FILTER_M05B = {"id": "M05b", "name": "Deadline <24h", "points": 15, "category": "market"}
FILTER_M05C = {"id": "M05c", "name": "Deadline <6h", "points": 25, "category": "market"}

# --- Coordination extra filter ---
FILTER_COORD04 = {"id": "COORD04", "name": "Fondeo via mixer", "points": 50, "category": "confluence"}

# --- Negative filters (N) — 8 filters ---
FILTER_N01 = {"id": "N01", "name": "Bot", "points": -40, "category": "negative"}
FILTER_N02 = {"id": "N02", "name": "Noticias", "points": -20, "category": "negative"}
FILTER_N03 = {"id": "N03", "name": "Arbitraje", "points": -100, "category": "negative"}
FILTER_N04 = {"id": "N04", "name": "Mercados opuestos", "points": 0, "category": "negative"}
FILTER_N05 = {"id": "N05", "name": "Copy-trading", "points": -25, "category": "negative"}
FILTER_N06A = {"id": "N06a", "name": "Degen leve", "points": -5, "category": "negative"}
FILTER_N06B = {"id": "N06b", "name": "Degen moderado", "points": -15, "category": "negative"}
FILTER_N06C = {"id": "N06c", "name": "Degen fuerte", "points": -30, "category": "negative"}
# Scalper/arb rápido (N07a-b mutually exclusive)
FILTER_N07A = {"id": "N07a", "name": "Scalper leve", "points": -20, "category": "negative"}
FILTER_N07B = {"id": "N07b", "name": "Scalper serial", "points": -40, "category": "negative"}
# Anti-bot evasion
FILTER_N08 = {"id": "N08", "name": "Anti-bot evasión", "points": 25, "category": "behavior"}
# Obvious bet (N09a-b mutually exclusive, opposite of B25)
FILTER_N09A = {"id": "N09a", "name": "Apuesta obvia extrema", "points": -40, "category": "negative"}
FILTER_N09B = {"id": "N09b", "name": "Apuesta obvia", "points": -25, "category": "negative"}
# Long-horizon discount (N10a-c mutually exclusive)
FILTER_N10A = {"id": "N10a", "name": "Horizonte lejano", "points": -10, "category": "negative"}
FILTER_N10B = {"id": "N10b", "name": "Horizonte muy lejano", "points": -20, "category": "negative"}
FILTER_N10C = {"id": "N10c", "name": "Horizonte extremo", "points": -30, "category": "negative"}

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
        FILTER_B20, FILTER_B23A, FILTER_B23B,
        FILTER_B25A, FILTER_B25B, FILTER_B25C,
        FILTER_B26A, FILTER_B26B,
        FILTER_B27A, FILTER_B27B,
        FILTER_B28A, FILTER_B28B,
        FILTER_B30A, FILTER_B30B, FILTER_B30C,
        FILTER_C01, FILTER_C02,
        FILTER_C03A, FILTER_C03B, FILTER_C03C, FILTER_C03D,
        FILTER_C05, FILTER_C06, FILTER_C07, FILTER_COORD04,
        FILTER_M01, FILTER_M02, FILTER_M03,
        FILTER_M04A, FILTER_M04B, FILTER_M05A, FILTER_M05B, FILTER_M05C,
        FILTER_N01, FILTER_N02, FILTER_N03, FILTER_N04, FILTER_N05,
        FILTER_N06A, FILTER_N06B, FILTER_N06C,
        FILTER_N07A, FILTER_N07B, FILTER_N08,
        FILTER_N09A, FILTER_N09B,
        FILTER_N10A, FILTER_N10B, FILTER_N10C,
    ]
}

# Mutually exclusive filter groups — only highest scoring fires
MUTUALLY_EXCLUSIVE_GROUPS: list[list[str]] = [
    ["W01", "W02", "W03"],       # wallet age tiers
    ["W04", "W05"],              # market count tiers
    ["B18a", "B18b", "B18c", "B18d"],  # accumulation tiers
    ["B19a", "B19b", "B19c"],    # whale entry tiers
    ["B23a", "B23b"],            # position sizing tiers
    ["B25a", "B25b", "B25c"],   # odds conviction tiers
    ["B26a", "B26b"],           # stealth accumulation tiers
    ["B27a", "B27b"],           # diamond hands tiers
    ["B28a", "B28b"],           # all-in tiers (mut. excl. with B23)
    ["B30a", "B30b", "B30c"],  # first mover tiers
    ["C01", "C02"],              # confluence direction tiers
    ["M04a", "M04b"],            # volume concentration tiers
    ["M05a", "M05b", "M05c"],   # deadline proximity tiers
    ["N06a", "N06b", "N06c"],    # degen tiers
    ["N07a", "N07b"],            # scalper tiers
    ["N09a", "N09b"],            # obvious bet tiers
    ["N10a", "N10b", "N10c"],   # long-horizon discount tiers
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

# Odds conviction thresholds (B25)
CONVICTION_EXTREME_MAX: float = 0.10    # B25a: odds < 0.10
CONVICTION_HIGH_MAX: float = 0.20       # B25b: odds 0.10-0.20
CONVICTION_MODERATE_MAX: float = 0.35   # B25c: odds 0.20-0.35
# Stealth accumulation thresholds (B26)
STEALTH_WHALE_MOVE: float = 0.01        # B26a: price_move < 1%
STEALTH_WHALE_MIN: float = 5000.0       # B26a: total > $5k
STEALTH_LOW_IMPACT_MOVE: float = 0.03   # B26b: price_move < 3%
STEALTH_LOW_IMPACT_MIN: float = 3000.0  # B26b: total > $3k

# Diamond hands (B27) — disabled until sell_detector covers pre-alert wallets
ENABLE_B27: bool = False
DIAMOND_HANDS_SHORT_MIN_HOURS: int = 24   # B27a: held 24-48h
DIAMOND_HANDS_SHORT_MAX_HOURS: int = 48
DIAMOND_HANDS_SHORT_ODDS_MOVE: float = 0.05  # B27a: odds improved >5%
DIAMOND_HANDS_LONG_MIN_HOURS: int = 72    # B27b: held 72h+
DIAMOND_HANDS_LONG_ODDS_MOVE: float = 0.10   # B27b: odds improved >10%

# All-in (B28) — mutually exclusive with B23; B28 evaluated first
ALLIN_EXTREME_MIN: float = 0.90   # B28a: >90% of balance
ALLIN_STRONG_MIN: float = 0.70    # B28b: 70-90% of balance
ALLIN_MIN_AMOUNT: float = 3500.0  # B28: min $3.5K (below = pocket money, not insider)

# First mover (B30) — disabled until trades history table exists in Supabase
ENABLE_B30: bool = False
FIRST_MOVER_MIN_AMOUNT: float = 1000.0  # minimum $1K to count
FIRST_MOVER_LOOKBACK_HOURS: int = 24    # look back 24h for prior buyers

# Confluence thresholds
SENDER_MAX_MARKETS: int = 3               # Super-sender: exclude if funding wallets in >3 markets
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

# Volume concentration thresholds (M04)
VOLUME_CONCENTRATION_MODERATE: float = 0.60   # M04a: top 3 wallets > 60%
VOLUME_CONCENTRATION_HIGH: float = 0.80       # M04b: top 3 wallets > 80%

# Deadline proximity thresholds (M05) — hours until resolution
DEADLINE_72H: int = 72     # M05a
DEADLINE_24H: int = 24     # M05b
DEADLINE_6H: int = 6       # M05c

# Position sizing thresholds (B23)
POSITION_SIGNIFICANT_MIN: float = 0.20   # B23a: 20-50% of balance
POSITION_SIGNIFICANT_MAX: float = 0.50
POSITION_DOMINANT_MIN: float = 0.50      # B23b: >50% of balance

# Scalper thresholds (N07)
SCALPER_FLIP_HOURS: int = 2              # N07a: buy+sell in <2h
SCALPER_SERIAL_MIN_MARKETS: int = 3      # N07b: flip in 3+ markets

# Sell monitoring
SELL_COORDINATED_WINDOW_HOURS: int = 4   # 2+ wallets sell within 4h = coordinated
SELL_COORDINATED_MIN_WALLETS: int = 2

# Anti-bot evasion threshold (N08)
ANTI_BOT_AMOUNT_CV_MAX: float = 0.10     # N08: coefficient of variation of amounts < 10%

# Negative thresholds
BOT_INTERVAL_STD_THRESHOLD: float = 1.0   # N01: std_dev ≈ 0
NEWS_LOOKBACK_HOURS: int = 24             # N02: 24h
COPY_TRADE_MIN_DELAY_MIN: int = 2         # N05: 2-10 min after whale
COPY_TRADE_MAX_DELAY_MIN: int = 10

# Degen thresholds
DEGEN_LIGHT_MAX: int = 2      # N06a: 1-2 non-political markets
DEGEN_MODERATE_MAX: int = 5   # N06b: 3-5
DEGEN_HEAVY_MIN: int = 6      # N06c: 6+

# Obvious bet thresholds (N09) — opposite of B25
OBVIOUS_BET_EXTREME: float = 0.90   # N09a: odds > 0.90 in wallet's direction
OBVIOUS_BET_HIGH: float = 0.85      # N09b: odds > 0.85

# Long-horizon discount thresholds (N10) — days until resolution
LONG_HORIZON_EXTREME: int = 90      # N10c: > 90 days → -30
LONG_HORIZON_HIGH: int = 60         # N10b: > 60 days → -20
LONG_HORIZON_MODERATE: int = 30     # N10a: > 30 days → -10

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
    # Bybit
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": "Bybit",
}

# Known bridge contracts on Polygon (lowercased)
KNOWN_BRIDGES: dict[str, str] = {
    # Polygon PoS Bridge
    "0xa0c68c638235ee32657e8f720a23cec1bfc6492a": "Polygon PoS Bridge",
    # Polygon Plasma Bridge
    "0x401f6c983ea34274ec46f84d70b31c151321188b": "Polygon Plasma Bridge",
    # Multichain (Anyswap)
    "0x4f3aff3a747fcade12598081e80c6605a8be192f": "Multichain",
    # Hop Protocol
    "0x25d8039bb044dc227f741a9e381ca4ceae2e6ae8": "Hop Protocol",
    # Across Protocol
    "0x69b5c72837769ef1e7c164abc6515dcff217f920": "Across Protocol",
    # Stargate (LayerZero)
    "0x45a01e4e04f14f7a4a6702c74187c5f6222033cd": "Stargate",
    "0x2f6f07cdcf3588944bf4c42ac74ff24bf56e7590": "Stargate",
}

# ============================================================
# SCORING — NEW SYSTEM (replaces old multiplier patterns)
# ============================================================

# Map old filter categories → new scoring categories
OLD_TO_NEW_CATEGORY: dict[str, str] = {
    "wallet": "ACCUMULATION",
    "origin": "ACCUMULATION",
    "behavior": "COORDINATION",
    "confluence": "COORDINATION",
    "market": "TIMING",
    "negative": "MARKET",
}

# New star thresholds (evaluated top-down)
NEW_STAR_THRESHOLDS: list[tuple[int, int]] = [
    (220, 5),
    (150, 4),
    (100, 3),
    (70, 2),
    (40, 1),
]

# Amount-based multiplier (applied to raw score before star assignment)
AMOUNT_MULTIPLIERS: list[tuple[float, float]] = [
    (50_000, 1.5),
    (20_000, 1.3),
    (10_000, 1.2),
    (5_000, 1.1),
    (1_000, 1.0),
    (500, 0.8),
    (0, 0.5),
]

# Sniper vs Shotgun — wallet market diversity multiplier
DIVERSITY_SNIPER_MAX_MARKETS: int = 3       # <=3 markets → sniper (x1.2)
DIVERSITY_SNIPER_MULTIPLIER: float = 1.2
DIVERSITY_SHOTGUN_MIN_MARKETS: int = 10     # >10 markets → shotgun (x0.7)
DIVERSITY_SHOTGUN_MULTIPLIER: float = 0.7
DIVERSITY_SUPER_SHOTGUN_MIN: int = 20       # >20 markets → super shotgun (x0.5)
DIVERSITY_SUPER_SHOTGUN_MULTIPLIER: float = 0.5

# Star validation — stars may be downgraded if requirements not met
STAR_VALIDATION: dict[int, dict] = {
    3: {"min_categories": 2},
    4: {"min_categories": 2, "min_amount": 5_000},
    5: {"min_categories": 3, "min_amount": 10_000, "requires_coord": True},
}

# Known mixer/privacy protocol addresses on Polygon (lowercased)
MIXER_ADDRESSES: dict[str, str] = {
    "0x1e34a77868e19a6647b3f7c4f1c4175d6ce5986f": "Tornado Cash (Polygon)",
    "0xba214c1c1928a32bffe790263e38b4af9bfcd659": "Tornado Cash (Polygon)",
    "0xdf231d99ff8b6c6cbf44e02362e760185973813d": "Tornado Cash (Polygon)",
    "0xaf4c0b70b2ea9fb7487c7cbb37ada259579fe040": "Tornado Cash (Polygon)",
    "0x94a1b5cdb22c43faab4abeb5c74999895464ddba": "Tornado Cash (Polygon)",
    "0xee9fb4f12d819bbab533e2b3866cdb9490f77ab2": "Railgun (Polygon)",
    "0x19ffac1a86f13b85cc09e42570d95a7b1e031012": "Railgun (Polygon)",
}

# ============================================================
# LEGACY MULTIPLIER PATTERNS (kept for reference, no longer used by scoring)
# ============================================================

MULTIPLIER_PATTERNS: list[dict] = [
    {
        "id": "P1",
        "name": "Insider clásico",
        "multiplier": 1.3,
        "required": {"W01", "W09", "O03"},
        "any_of": {"C01", "C02", "C03d", "C07"},
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

# Only scan markets in these categories (case-sensitive as returned by API)
MARKET_RELEVANT_CATEGORIES: set[str] = {
    "Politics", "Economics", "Corporate", "Crypto", "Geopolitics",
    "politics", "economics", "corporate", "crypto", "geopolitics",
}

# Blacklisted terms — markets whose question contains any of these are skipped
MARKET_BLACKLIST_TERMS: list[str] = [
    "tweet", "twitter", "follower", "subscriber", "tiktok", "instagram",
    "youtube", "stream", "movie", "album", "grammy", "oscar", "emmy",
    "nfl", "nba", "mlb", "nhl", "premier league", "champions league",
    "la liga", "serie a", "bundesliga", "world cup",
    "weather", "temperature", "rain", "hurricane",
    "dating", "married", "baby", "divorce",
    "mrbeast", "kardashian", "celebrity",
    # Daily/binary speculation markets (not insider trading)
    "up or down", "daily", "12pm et", "4pm et",
    "over/under", "above or below",
    # Counting/metrics markets
    "how many", "number of", "count of",
    # Crypto price speculation (low-liquidity, easy to dominate, not insider trading)
    "will bitcoin reach", "will bitcoin dip",
    "will ethereum reach", "will ethereum dip",
    "will solana reach", "will solana dip",
    "will xrp reach", "will xrp dip",
    "price of bitcoin", "price of ethereum",
    "price of solana", "price of xrp",
    "btc reach", "eth reach", "sol reach",
]
