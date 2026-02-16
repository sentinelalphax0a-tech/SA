# Sentinel Alpha - Technical Status Brief

> Last updated: 2026-02-16
> 553 tests passing | 7 GitHub Actions workflows | Production since Jan 2026

---

## 1. Project Structure

```
SA/
├── .github/workflows/
│   ├── scan.yml                    # Market scanner (every 3h)
│   ├── tracker.yml                 # Alert tracker (every 6h)
│   ├── resolver.yml                # Market resolution (daily 08:00 UTC)
│   ├── check_resolutions.yml       # Resolution checks (daily 00:00 UTC)
│   ├── dashboard.yml               # Dashboard generation (hourly :15)
│   ├── weekly_report.yml           # Weekly report (Monday 08:00 UTC)
│   └── monthly_report.yml          # Monthly report (1st of month 08:00 UTC)
├── docs/
│   └── index.html                  # Generated dashboard (auto-committed)
├── scripts/
│   ├── backfill.py                 # DB backfill utility
│   ├── setup_db.py                 # DB setup
│   ├── test_blockchain.py          # Blockchain client manual test
│   ├── test_connection.py          # Supabase connection test
│   ├── test_news.py                # News checker manual test
│   ├── test_polymarket.py          # Polymarket API manual test
│   └── test_telegram.py            # Telegram bot manual test
├── src/
│   ├── __init__.py
│   ├── config.py                   # All constants, thresholds, filter defs (~645 lines)
│   ├── main.py                     # Orchestrator: scan pipeline (~1380 lines)
│   ├── analysis/
│   │   ├── arbitrage_filter.py     # N03/N04 hedging detection
│   │   ├── behavior_analyzer.py    # B01-B30, N09-N10 trade pattern analysis
│   │   ├── confluence_detector.py  # C01-C07 multi-wallet coordination + grouping
│   │   ├── market_analyzer.py      # M01-M05 market condition evaluation
│   │   ├── noise_filter.py         # N01-N08 false positive reduction
│   │   ├── reversion_checker.py    # B21 post-resolution scoring reversion
│   │   ├── scoring.py              # Score calculation, star assignment, validation
│   │   ├── sell_detector.py        # Individual/coordinated sell detection
│   │   ├── wallet_analyzer.py      # W01-W11, O01-O03, COORD04 wallet profiling
│   │   └── wallet_tracker.py       # WR01/SP01 win-rate & specialization tracking
│   ├── dashboard/
│   │   ├── dashboard_template.html # Single-page app template (Chart.js)
│   │   └── generate_dashboard.py   # Data enrichment + HTML generation
│   ├── database/
│   │   ├── models.py               # 15 dataclasses (~353 lines)
│   │   └── supabase_client.py      # 50+ CRUD methods (760 lines)
│   ├── publishing/
│   │   ├── chart_generator.py      # Chart image generation
│   │   ├── formatter.py            # 12+ message format templates
│   │   ├── telegram_bot.py         # Telegram publishing (multi-channel)
│   │   └── twitter_bot.py          # X/Twitter publishing (daily limits)
│   ├── reports/
│   │   ├── weekly.py               # TODO: NotImplementedError
│   │   └── monthly.py              # TODO: NotImplementedError
│   ├── scanner/
│   │   ├── blockchain_client.py    # Alchemy Polygon RPC (wallet age, funding, balance)
│   │   ├── news_checker.py         # Google News RSS feed checker
│   │   └── polymarket_client.py    # Gamma/CLOB/Data API client
│   ├── scripts/
│   │   └── check_resolutions.py    # Daily resolution + wallet tracking
│   └── tracking/
│       ├── alert_notifier.py       # Resolution notification sender
│       ├── alert_tracker.py        # Position update tracker
│       ├── resolver.py             # Market resolution engine
│       ├── run_resolver.py         # Resolver entry point
│       ├── run_tracker.py          # Tracker entry point
│       └── whale_monitor.py        # Sell watch + whale activity monitor
├── tests/                          # 25 test files, 553 tests
│   ├── test_main.py                # 50 tests
│   ├── test_scoring.py             # 49 tests
│   ├── test_formatter.py           # 66 tests
│   ├── test_behavior_analyzer.py   # 56 tests
│   ├── test_confluence.py          # 42 tests (+6 group_and_detect)
│   ├── test_dashboard.py           # 27 tests
│   ├── test_twitter_bot.py         # 26 tests
│   ├── test_resolver.py            # 26 tests
│   ├── test_sell_watch.py          # 23 tests
│   ├── test_telegram_bot.py        # 22 tests
│   ├── test_noise_filter.py        # 20 tests
│   ├── test_arbitrage_filter.py    # 16 tests
│   ├── test_market_analyzer.py     # 15 tests
│   ├── test_alert_notifier.py      # 13 tests
│   ├── test_market_analyzer_new.py # 12 tests
│   ├── test_alert_tracker.py       # 12 tests
│   ├── test_check_resolutions.py   # 10 tests
│   ├── test_noise_new.py           # 10 tests
│   ├── test_wallet_tracker.py      # 10 tests
│   ├── test_wallet_analyzer.py     # 9 tests
│   ├── test_behavior_new.py        # 9 tests
│   ├── test_whale_monitor.py       # 9 tests
│   ├── test_telegram_multichannel.py # 8 tests
│   ├── test_consolidation.py       # 7 tests
│   └── test_sell_detector.py       # 5 tests
└── requirements.txt
```

---

## 2. Module Status

| Module | File | Status | Lines | Notes |
|--------|------|--------|-------|-------|
| **Orchestrator** | `src/main.py` | Production | ~1380 | Per-group scoring + multi-signal |
| **Config** | `src/config.py` | Production | ~645 | SCAN_PROFILES + FUNDING_GROUPING |
| **Wallet Analyzer** | `src/analysis/wallet_analyzer.py` | Production | ~300 | W/O/COORD04 filters |
| **Behavior Analyzer** | `src/analysis/behavior_analyzer.py` | Production | ~600 | 31 B/N filters |
| **Market Analyzer** | `src/analysis/market_analyzer.py` | Production | ~200 | M01-M05 filters |
| **Noise Filter** | `src/analysis/noise_filter.py` | Production | ~250 | N01-N08 filters |
| **Arbitrage Filter** | `src/analysis/arbitrage_filter.py` | Production | ~200 | N03 kills alert (-100) |
| **Confluence Detector** | `src/analysis/confluence_detector.py` | Production | ~530 | C01-C07 + group_and_detect |
| **Scoring Engine** | `src/analysis/scoring.py` | Production | ~200 | Log multiplier + star validation |
| **Sell Detector** | `src/analysis/sell_detector.py` | Production | ~200 | Position exit detection |
| **Wallet Tracker** | `src/analysis/wallet_tracker.py` | Production | ~150 | WR01/SP01 win-rate |
| **Reversion Checker** | `src/analysis/reversion_checker.py` | Production | ~100 | B21 post-resolution |
| **Polymarket Client** | `src/scanner/polymarket_client.py` | Production | ~450 | Gamma + CLOB + Data APIs |
| **Blockchain Client** | `src/scanner/blockchain_client.py` | Production | ~400 | Alchemy Polygon RPC |
| **News Checker** | `src/scanner/news_checker.py` | Production | ~80 | Google News RSS |
| **Supabase Client** | `src/database/supabase_client.py` | Production | ~760 | 50+ CRUD methods |
| **Models** | `src/database/models.py` | Production | ~353 | 15 dataclasses (Alert +4 fields) |
| **Telegram Bot** | `src/publishing/telegram_bot.py` | Production | ~250 | Multi-channel support |
| **Twitter Bot** | `src/publishing/twitter_bot.py` | Production | ~200 | Daily limit, OAuth 1.0a |
| **Formatter** | `src/publishing/formatter.py` | Production | ~610 | 12+ formats + multi-signal line |
| **Dashboard Generator** | `src/dashboard/generate_dashboard.py` | Production | ~400 | Self-contained HTML |
| **Dashboard Template** | `src/dashboard/dashboard_template.html` | Production | ~2000 | Chart.js SPA |
| **Resolver** | `src/tracking/resolver.py` | Production | ~200 | Market resolution engine |
| **Alert Tracker** | `src/tracking/alert_tracker.py` | Production | ~200 | Position tracking |
| **Alert Notifier** | `src/tracking/alert_notifier.py` | Production | ~200 | Resolution notifications |
| **Whale Monitor** | `src/tracking/whale_monitor.py` | Production | ~400 | Sell Watch + whale events |
| **Check Resolutions** | `src/scripts/check_resolutions.py` | Production | ~150 | Daily resolution script |
| **Weekly Report** | `src/reports/weekly.py` | TODO | ~20 | NotImplementedError |
| **Monthly Report** | `src/reports/monthly.py` | TODO | ~20 | NotImplementedError |

---

## 3. Execution Flow

### 3.1 Scan Pipeline (`src/main.py` — `run_scan()`)

```
Step 1: INITIALIZE
  ├── Check kill switch (system_config.scan_enabled)
  ├── Initialize all services (PM client, blockchain, analyzers, bots)
  └── Read SCAN_PROFILE (quick or deep)

Step 2: FETCH MARKETS
  ├── pm_client.get_active_markets(categories)
  ├── _filter_markets(markets, mode)
  │   ├── Volume filter (quick: $1000, deep: $200)
  │   ├── Odds range filter (quick: 0.05-0.55, deep: 0.05-0.85)
  │   ├── Category filter (quick: politics/economics/geopolitics, deep: +science)
  │   ├── Blacklist filter (50+ terms + deep extras)
  │   └── Cap (quick: 100 markets, deep: unlimited)
  └── Sort by volume DESC

Step 3-7: PROCESS MARKETS
  ├── [QUICK MODE] Sequential loop with 8-min global timeout
  │   ├── Build excluded_senders from cross-market tracking
  │   ├── _process_market() for each market → list[(Alert, is_whale)]
  │   └── Update sender_market_count per market
  │
  └── [DEEP MODE] asyncio parallel (5 concurrent, 1s batch pause)
      ├── _process_markets_deep() with retry on 429
      ├── No global timeout, no sender tracking
      └── Progress log every 25 markets

  _process_market() per market → list[(Alert, is_whale)]:
    ├── 3. Fetch recent trades (lookback: 35min quick, 24h deep)
    ├── 4a. Group trades by wallet
    ├── 4b. Sort wallets by volume DESC, take top N
    ├── 4c. For each wallet:
    │   ├── 4d. Check accumulation (min $100)
    │   ├── 4e-i. Wallet analysis (W01-W11, O01-O03, COORD04)
    │   ├── 4e-ii. Behavior analysis (B01-B30, N09-N10)
    │   ├── 4e-iii. Noise filter (N01, N02, N05-N08)
    │   ├── 4e-iv. Arbitrage filter (N03, N04)
    │   └── 4f. Collect wallet data + filters
    ├── 4f. Filter wallets by dominant direction
    ├── 5a. Market analysis (M01-M05)
    ├── 5b. GROUP WALLETS BY FUNDING RELATIONSHIPS (NEW)
    │   ├── Fetch funding for 2+ wallets (FUNDING_GROUPING_MIN_WALLETS)
    │   ├── group_and_detect(): union-find grouping by shared senders
    │   │   ├── Build sender → wallets index
    │   │   ├── Union wallets sharing a sender (C03a-d, C07 relationships)
    │   │   └── Return list of wallet groups
    │   └── Run C01-C07 filters SCOPED per group (not globally)
    ├── 5c. SCORE EACH GROUP INDEPENDENTLY (NEW)
    │   ├── Per group: best wallet filters + market + group's C filters
    │   ├── calculate_score() per group
    │   ├── Odds range check per group
    │   └── Build Alert per group
    ├── 6. MULTI-SIGNAL CLASSIFICATION (NEW)
    │   ├── Sort alert candidates by score DESC
    │   ├── First = primary (published), rest = secondary (DB only)
    │   ├── Set alert_group_id (shared UUID)
    │   └── multi_signal=True if 2+ independent groups
    └── Return list[(Alert, is_whale)] — one per group

Step 7b: DEDUPLICATE
  └── Jaccard similarity on market questions (>=0.60 threshold)
      → Keep highest-scoring alert per similar group

Step 8: SAVE + PUBLISH (per alert)
  ├── 8a-pre. Secondary alerts: insert to DB but skip publish (NEW)
  ├── 8a. Within-scan dedup: insert but skip publish
  ├── 8b. Cross-scan dedup: update existing alert if same market+direction in 24h
  ├── 8b2. Consolidation: merge into existing 4+★ alert
  └── 8c. New alert:
      ├── Insert to DB
      ├── Insert AlertTracking + WalletPositions
      ├── Telegram (2+★ testing, 3+★ public) — includes multi-signal line
      └── Twitter (3+★, daily limit)

Step 8b: SELL MONITORING (skip in dry-run)
  └── SellDetector.check_open_positions()
      → Telegram notifications for sells

Step 9: LOG SCAN RECORD
  └── Insert Scan row with counters
```

### 3.2 Tracker Pipeline (`src/tracking/run_tracker.py` — every 6h)

```
1. AlertTracker: Update positions for pending alerts
2. AlertNotifier: Send resolution notifications
3. WhaleMonitor: Check 3-5★ alerts for:
   ├── Full exit (>90% sold)
   ├── Partial exit (30-90%)
   ├── Additional buys
   └── New market entries by same wallet
```

### 3.3 Resolver Pipeline (`src/tracking/resolver.py` — daily 08:00 UTC)

```
1. Fetch all pending alerts
2. For each unique market: check Polymarket CLOB for resolution
3. If resolved:
   ├── Set outcome (correct/incorrect)
   ├── Calculate P&L (actual_return %)
   ├── Update wallet win/loss stats
   └── Send resolution notification
```

### 3.4 Dashboard Pipeline (`src/dashboard/generate_dashboard.py` — hourly)

```
1. Fetch 5000 alerts + all markets + last scan + 50 sell events
2. Enrich alerts: entry_price, odds_change, PnL, time fields, row_class
3. Compute stats: accuracy, by-star breakdown, charts data, filter distribution
4. Inject JSON into HTML template → self-contained SPA
5. Auto-commit to docs/index.html
```

---

## 4. Filter Catalog (50+ filters)

### Wallet Filters (category: `wallet` → ACCUMULATION)

| ID | Name | Points | Condition |
|----|------|--------|-----------|
| W01 | Wallet muy nueva | +25 | Age < 7 days |
| W02 | Wallet nueva | +20 | Age 7-14 days |
| W03 | Wallet reciente | +15 | Age 14-30 days |
| W04 | Solo 1 mercado | +10 | Exactly 1 market on Polymarket |
| W05 | Solo 2-3 mercados | +15 | 2-3 markets total |
| W09 | Primera tx = Polymarket | +5 | First on-chain contract = PM |
| W11 | Balance redondo | +3 | Balance ~$5k/$10k/$50k (+-1%) |

### Origin Filters (category: `origin` → ACCUMULATION)

| ID | Name | Points | Condition |
|----|------|--------|-----------|
| O01 | Origen exchange | +5 | Funded from known exchange (Coinbase, Binance, etc.) |
| O02 | Fondeo reciente | +10 | Funded 3-7 days ago |
| O03 | Fondeo muy reciente | +5 | Funded < 3 days ago |
| COORD04 | Fondeo via mixer | +50 | Funded from Tornado Cash or Railgun |

### Behavior Filters (category: `behavior` → COORDINATION)

| ID | Name | Points | Condition | Notes |
|----|------|--------|-----------|-------|
| B01 | Acumulacion goteo | +20 | 5+ buys in 24-72h window | Drip accumulation |
| B05 | Solo market orders | +5 | All trades are market orders | |
| B06 | Tamano creciente | +15 | Each buy > previous buy | Escalation |
| B07 | Compra contra mercado | +20 | Any trade @ price < 0.20 | Contrarian |
| B14 | Primera compra grande | +15 | First buy >= $5,000 | Suppressed if B19 fires |
| B16 | Acumulacion rapida | +20 | 3+ trades within 4 hours | |
| B17 | Horario bajo | +10 | Trade 2-6 AM UTC | Off-hours |
| B18a | Acumulacion moderada | +15 | $2,000-$3,499 total | +5 if 3-4 trades, +10 if 5+ |
| B18b | Acumulacion significativa | +25 | $3,500-$4,999 total | Trade bonus applies |
| B18c | Acumulacion fuerte | +35 | $5,000-$9,999 total | Trade bonus applies |
| B18d | Acumulacion muy fuerte | +50 | >= $10,000 total | Highest tier |
| B19a | Entrada grande | +20 | Single trade >= $5,000 | Whale entry |
| B19b | Entrada muy grande | +30 | Single trade >= $10,000 | Suppresses B14 |
| B19c | Entrada masiva | +40 | Single trade >= $50,000 | Suppresses B14 |
| B20 | Vieja nueva en PM | +20 | Wallet >180d old, PM < 7d | Old account new to PM |
| B23a | Posicion significativa | +15 | 20-50% of wallet balance | Requires >=2 trades, >$50 |
| B23b | Posicion dominante | +30 | >50% of wallet balance | Suppressed if B28 fires |
| B25a | Conviccion extrema | +25 | Avg price < 0.10 | |
| B25b | Conviccion alta | +15 | Avg price 0.10-0.20 | |
| B25c | Conviccion moderada | +5 | Avg price 0.20-0.35 | |
| B26a | Stealth whale | +20 | Price move <1% AND total >= $5k | |
| B26b | Low impact | +10 | Price move <3% AND total >= $3k | |
| B27a | Diamond hands 48h | +15 | Held 24-48h, no sells, +5% | DISABLED |
| B27b | Diamond hands 72h+ | +20 | Held 72h+, no sells, +10% | DISABLED |
| B28a | All-in extremo | +25 | >90% balance, >= $3,500 | Mutual excl. with B23 |
| B28b | All-in fuerte | +20 | 70-90% balance, >= $3,500 | Mutual excl. with B23 |
| B30a | First mover | +20 | First wallet to buy >= $1k | DISABLED |
| B30b | Early mover top 3 | +10 | Among first 3 >$1k buyers | DISABLED |
| B30c | Early mover top 5 | +5 | Among first 5 >$1k buyers | DISABLED |

### Confluence Filters (category: `confluence` → COORDINATION)

| ID | Name | Points | Condition |
|----|------|--------|-----------|
| C01 | Confluencia basica | +10 | 3+ wallets same direction (scoped per group) |
| C02 | Confluencia fuerte | +15 | 5+ wallets same direction (replaces C01, scoped per group) |
| C03a | Origen exchange compartido | +5 | 2+ wallets from same exchange |
| C03b | Origen bridge compartido | +20 | 2+ wallets from same bridge |
| C03c | Origen mixer compartido | +30 | 2+ wallets from same mixer |
| C03d | Mismo padre directo | +30 | 2+ wallets from same unknown sender |
| C05 | Fondeo temporal | +10 | 3+ wallets funded from exchange within 4h |
| C06 | Monto similar | +10 | Same sender funded wallets with amounts +-30% |
| C07 | Red de distribucion | +30 | 1 sender → 3+ wallets active in market |

### Market Filters (category: `market` → TIMING)

| ID | Name | Points | Condition |
|----|------|--------|-----------|
| M01 | Volumen anomalo | +15 | 24h volume > 2x 7d average |
| M02 | Odds estables rotos | +20 | Stable >48h then move >10% |
| M03 | Baja liquidez | +10 | Liquidity < $100,000 |
| M04a | Concentracion moderada | +15 | Top 3 wallets > 60% volume |
| M04b | Concentracion alta | +25 | Top 3 wallets > 80% volume |
| M05a | Deadline <72h | +10 | Resolution in 24-72 hours |
| M05b | Deadline <24h | +15 | Resolution in 6-24 hours |
| M05c | Deadline <6h | +25 | Resolution in <6 hours |

### Negative Filters (category: `negative`)

| ID | Name | Points | Condition | Impact |
|----|------|--------|-----------|--------|
| N01 | Bot | -40 | Interval std_dev < 1s | |
| N02 | Noticias | -20 | Google News coverage in 24h | |
| N03 | Arbitraje | -100 | YES + NO on equivalent markets | KILLS ALERT |
| N04 | Mercados opuestos | 0 | Same direction both markets | Flag only |
| N05 | Copy-trading | -25 | Trade 2-10 min after whale | |
| N06a | Degen leve | -5 | 1-2 non-political markets | |
| N06b | Degen moderado | -15 | 3-5 non-political markets | |
| N06c | Degen fuerte | -30 | 6+ non-political markets | |
| N07a | Scalper leve | -20 | Buy+sell same market <2h | |
| N07b | Scalper serial | -40 | Flips in 3+ markets | |
| N08 | Anti-bot evasion | -25 | Irregular intervals + uniform amounts | |
| N09a | Apuesta obvia extrema | -40 | Avg price > 0.90 | Star cap: 2 |
| N09b | Apuesta obvia | -25 | Avg price > 0.85 | Star cap: 3 |
| N10a | Horizonte lejano | -10 | Resolution > 30 days | |
| N10b | Horizonte muy lejano | -20 | Resolution > 60 days | |
| N10c | Horizonte extremo | -30 | Resolution > 90 days | |

---

## 5. Scoring System

### 5.1 Mutual Exclusion Groups

Before scoring, only the highest-scoring filter per group is kept:

| Group | Filters | Rule |
|-------|---------|------|
| wallet_age | W01, W02, W03 | Keep highest |
| wallet_markets | W04, W05 | Keep highest |
| funding_recency | O02, O03 | Keep highest |
| accumulation | B18a, B18b, B18c, B18d | Keep highest |
| whale_entry | B19a, B19b, B19c | Keep highest |
| position | B23a, B23b, B28a, B28b | Keep highest (B28 suppresses B23) |
| conviction | B25a, B25b, B25c | Keep highest |
| stealth | B26a, B26b | Keep highest |
| confluence | C01, C02 | Keep highest |
| concentration | M04a, M04b | Keep highest |
| deadline | M05a, M05b, M05c | Keep highest |
| degen | N06a, N06b, N06c | Keep highest |
| scalper | N07a, N07b | Keep highest |
| obvious_bet | N09a, N09b | Keep highest |
| long_horizon | N10a, N10b, N10c | Keep highest |

### 5.2 Score Calculation

```python
# 1. Raw score = sum(filter.points for each surviving filter), floor at 0
score_raw = max(0, sum(f.points for f in filters))

# 2. Amount multiplier (logarithmic)
amount_mult = clamp(0.18 * ln(total_amount) - 0.37, min=0.3, max=2.0)

# 3. Diversity multiplier (wallet market count)
#    <= 3 markets: x1.2 (sniper/focused)
#    4-9 markets:  x1.0 (normal)
#    10-19 markets: x0.7 (shotgun)
#    >= 20 markets: x0.5 (super shotgun)
#    None:          x1.0

# 4. Final score
multiplier = amount_mult * diversity_mult
score_final = round(score_raw * multiplier)  # capped at 400
```

### 5.3 Star Thresholds

| Stars | Min Final Score |
|-------|----------------|
| 5 | 220 |
| 4 | 150 |
| 3 | 100 |
| 2 | 70 |
| 1 | 40 |
| 0 | < 40 |

### 5.4 Star Validation Requirements

Stars can be downgraded if requirements are not met:

| Stars | Min Categories | Min Amount | Requires COORDINATION |
|-------|---------------|------------|----------------------|
| 3 | 2 | - | No |
| 4 | 2 | $5,000 | No |
| 5 | 3 | $10,000 | Yes |

Categories are derived from filter categories:
- `wallet`, `origin` → ACCUMULATION
- `behavior`, `confluence` → COORDINATION
- `market` → TIMING

### 5.5 N09 Star Cap

| Filter | Max Stars |
|--------|-----------|
| N09a (odds > 0.90) | 2 |
| N09b (odds > 0.85) | 3 |

---

## 6. Deduplication, Grouping & Multi-Signal Logic

### 6.1 Wallet Grouping by Funding Relationships (NEW)

Before scoring, wallets in a market are grouped by shared funding relationships using union-find. Two wallets are in the same group if they share at least one funding sender (C03a-d, C07 relationships).

```
Input: 5 wallets in market
  ├── Wallet A ← funded by Sender X
  ├── Wallet B ← funded by Sender X    → Group 1 (A, B, C — linked via Sender X)
  ├── Wallet C ← funded by Sender X
  ├── Wallet D ← funded by Sender Y    → Group 2 (D — solo)
  └── Wallet E ← funded by Sender Z    → Group 3 (E — solo)

Output: 3 groups, each scored independently
```

**Key behaviors:**
- C filters (C01-C07) are scoped per group, not globally
- Each group generates its own Alert with its best wallet's W/B/O filters + M filters + group's C filters
- Funding fetch triggers at `FUNDING_GROUPING_MIN_WALLETS` (2), lower than `CONFLUENCE_BASIC_MIN_WALLETS` (3)

### 6.2 Multi-Signal Classification (NEW)

After grouping and scoring, alerts from the same market are classified:

| Field | Primary Alert | Secondary Alert(s) |
|-------|--------------|-------------------|
| `is_secondary` | `False` | `True` |
| `multi_signal` | `True` (if 2+ groups) | `True` |
| `alert_group_id` | Shared UUID | Same shared UUID |
| `secondary_count` | N-1 (number of secondaries) | 0 |

**Publishing rules:**
- Primary alert (highest score): published normally (Telegram + Twitter based on star level)
- Secondary alerts: inserted to DB only, never published
- Multi-signal line added to Telegram format: "Multi-signal: N independent group(s)"

### 6.3 Within-Scan Dedup (Step 7b)

After all markets are processed, alerts with similar market questions are grouped using Jaccard similarity:

```python
similarity = |tokens_A & tokens_B| / |tokens_A | tokens_B|
threshold = 0.60
```

Tokenization strips stop words and month names. Within each similar group, only the highest-scoring alert is published; others are inserted but marked `deduplicated=True`.

### 6.4 Cross-Scan Dedup (Step 8b)

Before inserting a new alert, the system checks for existing alerts in the last 24h for the same `(market_id, direction)`. If found, the existing alert's fields (odds, amount, score, wallets) are updated instead of creating a duplicate.

### 6.5 High-Star Consolidation (Step 8b2)

If a 4+★ alert already exists for the same `(market_id, direction)` in the last 48h, new wallets are merged into the existing alert via `db.update_alert_consolidation()`. The consolidation count increments, and a Telegram update is sent.

### 6.6 Super-Sender Exclusion (Quick Mode Only)

In sequential mode, the system tracks `{sender_address: set(market_ids)}`. Senders funding wallets in > `SENDER_MAX_MARKETS` markets are excluded from confluence analysis (they're exchanges/routers, not insiders). Deep mode skips this tracking since parallel processing prevents incremental tracking.

---

## 7. Database Schema (Supabase PostgreSQL)

### Tables & Models

| Table | Model | Purpose |
|-------|-------|---------|
| `wallets` | `Wallet` | Wallet profiles (age, markets, win rate, origin) |
| `markets` | `Market` | Market metadata (odds, volume, resolution) |
| `alerts` | `Alert` | Generated alerts (score, filters, wallets, outcome, multi-signal) |
| `wallet_funding` | `WalletFunding` | Funding chain traces (sender, hops, exchange/bridge/mixer) |
| `scans` | `Scan` | Scan execution logs (counters, duration, errors) |
| `weekly_reports` | `WeeklyReport` | Weekly performance aggregates |
| `smart_money_leaderboard` | `SmartMoneyLeaderboard` | Wallet performance rankings |
| `alert_tracking` | `AlertTracking` | Pending alert resolution tracking |
| `wallet_positions` | `WalletPosition` | Open positions for sell monitoring |
| `alert_sell_events` | `SellEvent` | Detected sell events (exit type, PnL) |
| `wallet_categories` | `WalletCategory` | Wallet specialization (smart_money, whale, degen, bot) |
| `notification_log` | - | Dedup log for notifications |
| `whale_notifications` | - | Whale monitor notification log |
| `market_snapshots` | `MarketSnapshot` | Historical odds/volume snapshots |
| `system_config` | `SystemConfig` | Kill switches and runtime flags |

### Alert Model Fields (NEW multi-signal fields)

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `multi_signal` | `bool` | `False` | True when 2+ independent groups bet same direction |
| `is_secondary` | `bool` | `False` | True for non-primary alerts in a group |
| `alert_group_id` | `str | None` | `None` | Shared UUID across alerts from same market scan |
| `secondary_count` | `int` | `0` | Number of secondary alerts (set on primary) |

### Supabase Migration (run once)

```sql
ALTER TABLE alerts
  ADD COLUMN IF NOT EXISTS multi_signal BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_secondary BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS alert_group_id TEXT,
  ADD COLUMN IF NOT EXISTS secondary_count INTEGER DEFAULT 0;
```

### Key system_config Flags

| Key | Purpose |
|-----|---------|
| `scan_enabled` | Master kill switch for scans |
| `publish_x` | Enable/disable Twitter publishing |
| `publish_telegram` | Enable/disable Telegram publishing |

---

## 8. APIs Used

### Polymarket APIs

| Endpoint | Base URL | Purpose | Rate Limit |
|----------|----------|---------|------------|
| `GET /events` | `gamma-api.polymarket.com` | Market discovery | 0.1s delay |
| `GET /markets` | `gamma-api.polymarket.com` | Market metadata | 0.1s delay |
| `GET /markets/{id}` | `clob.polymarket.com` | Resolution status | 0.05s delay |
| `GET /midpoint` | `clob.polymarket.com` | Current odds | 0.05s delay |
| `GET /book` | `clob.polymarket.com` | Orderbook | 0.05s delay |
| `GET /trades` | `data-api.polymarket.com` | Trade history | Paginated, 500/page, 5 max |

### Alchemy (Polygon RPC)

| Method | Purpose |
|--------|---------|
| `alchemy_getAssetTransfers` | Token transfers (funding traces, wallet age) |
| `eth_call` (balanceOf) | USDC balance (PoS + Native) |

### Other APIs

| Service | Purpose |
|---------|---------|
| Google News RSS | N02 news detection (24h lookback) |
| Telegram Bot API | Alert publishing (multi-channel) |
| Twitter API (OAuth 1.0a) | Alert publishing (daily limit) |
| Supabase REST API | Database CRUD |

### Known On-Chain Addresses

**Polymarket Contracts:**
- `0x4bfb...982e` — CTF Exchange
- `0xc5d5...f80a` — NegRisk CTF
- `0x4d97...6045` — Proxy

**Exchanges:** Coinbase, Binance, Kraken, OKX, Crypto.com, Gate.io, Bybit

**Bridges:** Polygon PoS, Plasma, Multichain, Hop Protocol, Across Protocol, Stargate

**Mixers:** Tornado Cash (Polygon), Railgun (Polygon)

---

## 9. GitHub Actions Workflows

| Workflow | Schedule | Timeout | Python | Entry Point |
|----------|----------|---------|--------|-------------|
| **scan.yml** | `0 */3 * * *` (every 3h) | 10 min | 3.11 | `python -m src.main` |
| **tracker.yml** | `0 1,7,13,19 * * *` (every 6h) | 10 min | 3.13 | `python -m src.tracking.run_tracker` |
| **resolver.yml** | `0 8 * * *` (daily 08:00) | 10 min | 3.13 | `python -m src.tracking.run_resolver` |
| **check_resolutions.yml** | `0 0 * * *` (daily 00:00) | - | 3.12 | `python -m src.scripts.check_resolutions` |
| **dashboard.yml** | `15 * * * *` (hourly :15) | 5 min | 3.11 | `python -m src.dashboard.generate_dashboard` |
| **weekly_report.yml** | `0 8 * * 1` (Mon 08:00) | 10 min | 3.11 | `python -m src.reports.weekly` |
| **monthly_report.yml** | `0 8 1 * *` (1st 08:00) | 10 min | 3.11 | `python -m src.reports.monthly` |

### Environment Variables (Secrets)

| Variable | Used By |
|----------|---------|
| `SUPABASE_URL` | All workflows |
| `SUPABASE_KEY` | All workflows |
| `TELEGRAM_BOT_TOKEN` | scan, tracker, resolver, check_resolutions, dashboard |
| `TELEGRAM_CHANNEL_ID` | scan, tracker, resolver, check_resolutions, dashboard |
| `TELEGRAM_PRIVATE_CHANNEL_ID` | check_resolutions |
| `ALCHEMY_API_KEY` | scan |
| `ALCHEMY_ENDPOINT` | scan |
| `TWITTER_CONSUMER_KEY` | scan, weekly, monthly |
| `TWITTER_CONSUMER_SECRET` | scan, weekly, monthly |
| `TWITTER_ACCESS_TOKEN` | scan, weekly, monthly |
| `TWITTER_ACCESS_TOKEN_SECRET` | scan, weekly, monthly |

---

## 10. Alert System

### Publishing Thresholds

| Platform | Min Stars | Format | Notes |
|----------|-----------|--------|-------|
| Telegram (testing channel) | 1★+ | `format_telegram_detailed` | Full filter breakdown |
| Telegram (main channel) | 2★+ | `format_telegram_alert` | Score visible, multi-signal line |
| Telegram (public channel) | 3★+ | `format_telegram_alert` | No filter IDs |
| Twitter/X | 3★+ | `format_x_alert` (280 chars) | No score, no filters |
| Whale entry (B19) | Always | `format_whale_entry` | Telegram only |

### Alert Lifecycle

```
Created (pending) → Tracked (AlertTracking + WalletPositions)
  ├── Multi-signal: primary published, secondaries stored in DB only (NEW)
  ├── Sell detected → SellEvent recorded, alert.total_sold_pct updated
  │   └── Telegram notification if 4-5★
  ├── Market resolves → outcome = correct/incorrect
  │   ├── actual_return % calculated
  │   ├── Wallet stats updated (win/loss)
  │   └── Resolution notification (Telegram + Twitter)
  └── Consolidated → New wallets merged into existing 4+★ alert
      └── Update notification (Telegram)
```

### Message Formats Summary

| Format | Platform | Use Case | Shows Score | Shows Filters |
|--------|----------|----------|-------------|---------------|
| `format_x_alert` | Twitter | 3+★ alert | No | No |
| `format_x_resolution` | Twitter | Any resolution | No | No |
| `format_telegram_alert` | Telegram | 2+★ alert | Yes | No |
| `format_telegram_detailed` | Telegram | Testing | Yes | Yes (IDs + points) |
| `format_whale_entry` | Telegram | B19 whale entry | No | No |
| `format_telegram_resolution` | Telegram | Resolution | Yes | No |
| `format_sell_notification` | Telegram | Individual sell | N/A | No |
| `format_coordinated_sell` | Telegram | Coordinated sell | N/A | No |
| `format_sell_watch` | Telegram | Sell event metadata | N/A | No |
| `format_alert_update` | Telegram | Consolidation | No | No |
| `format_opposing_positions` | Telegram | Conflicting signals | No | No |

---

## 11. Tracking & Resolution

### Alert Tracker (every 6h)

Updates alert positions with current odds from Polymarket. Feeds data to the whale monitor and notifier.

### Whale Monitor (every 6h, after tracker)

Monitors 3-5★ alerts for position changes:

| Event | Threshold | Notification |
|-------|-----------|-------------|
| Full Exit | >= 90% position sold | Telegram (4-5★) |
| Partial Exit | 30-90% sold | Telegram (4-5★) |
| Additional Buy | Any new buy | Telegram (3-5★) |
| New Market Entry | Same wallet in another alert | Telegram (3-5★) |

### Sell Watch System

Metadata-only tracking of position exits. Stars and scores are NEVER modified by sells.

| Config | Value |
|--------|-------|
| `SELL_WATCH_MIN_STARS` | 3 |
| `SELL_WATCH_MIN_SELL_PCT` | 0.20 (20%) |
| `SELL_WATCH_COOLDOWN_HOURS` | 6 |
| `SELL_WATCH_NOTIFY_MIN_STARS` | 4 |

### Market Resolver (daily 08:00 UTC)

```python
# P&L Calculation
if correct and odds_adj > 0:
    actual_return = ((1.0 - odds_adj) / odds_adj) * 100  # % profit
else:
    actual_return = -100.0  # total loss
```

### Check Resolutions (daily 00:00 UTC)

Supplements the resolver with:
- B21 reversion scoring
- WR01/SP01 wallet category tracking
- Private channel notifications

---

## 12. Reporting

### Dashboard (Production)

Self-contained HTML SPA at `docs/index.html`, auto-generated hourly:

| Section | Content |
|---------|---------|
| Stats Grid | Total/Active/Resolved/Accuracy/Sells/Last Scan |
| Star Summary | Per-star performance cards (count, accuracy, avg return) |
| Alert Table | Sortable/filterable table with expandable detail rows |
| Accuracy Over Time | Line chart (weekly, 3+★ only) |
| Alerts Per Day | Stacked bar chart (30 days, by star level) |
| Cumulative P&L | Line chart ($100/alert, 3+★) |
| Top Filters | Horizontal bar (correct vs incorrect, top 20) |
| Closing Soon | Pending alerts resolving within 7 days |
| Recent Sells | Latest 20 sell events |
| Resolution History | Timeline of last 30 resolved alerts |

### Weekly Report (TODO)

`src/reports/weekly.py` raises `NotImplementedError`. Workflow exists but won't run.

### Monthly Report (TODO)

`src/reports/monthly.py` raises `NotImplementedError`. Workflow exists but won't run.

---

## 13. Known Bugs & Limitations

### Bugs

1. **Weekly/Monthly Reports**: `NotImplementedError` — workflows will crash silently.

### Limitations

1. **Deep mode sender tracking**: Cross-market super-sender exclusion is skipped in deep mode (parallel processing prevents incremental tracking). Dedup at Step 7b partially compensates.

2. **Deep mode race conditions**: Shared `counters` dict accessed from multiple threads — minor races on diagnostic counters (total_trades, wallets_analyzed) acceptable since they only affect scan summary logs.

3. **Rate limits in deep mode**: Polymarket Data API throttles at ~275+ concurrent requests. Mitigated with 5-concurrent-worker limit, 1s batch pause, and retry with exponential backoff (5s, 15s, then skip).

4. **Google News RSS**: N02 news check depends on Google News RSS availability. Feed outages cause false negatives (no news detected → no penalty applied).

5. **CLOB midpoint fallback**: If CLOB `/midpoint` fails, falls back to Gamma `outcomePrices` which may be stale.

6. **Disabled filters**: B27a/b (Diamond hands) and B30a/b/c (First mover) are disabled via config flags. No timeline for re-enabling.

7. **Sell detection lookback**: Sell detector uses same lookback as scan (35min quick). Short sells between scan cycles may be missed.

8. **Dashboard P&L assumption**: Cumulative P&L chart assumes $100 per alert, which doesn't reflect actual position sizes.

9. **Resolution timing**: Resolver runs once daily (08:00 UTC). Markets resolving between runs have delayed outcome recording.

10. **Multi-signal secondary alerts**: Secondary alerts are stored in DB but not published. Dashboard and resolution tracking treat them equally — no separate accuracy tracking per primary vs secondary yet.

---

## 14. Scan Modes & CLI

### Quick Mode (default — GitHub Actions)

```bash
python -m src.main
```

| Parameter | Value |
|-----------|-------|
| Markets | 100 cap, sorted by volume |
| Volume min | $1,000 |
| Odds range | 0.05 - 0.55 |
| Categories | politics, economics, geopolitics |
| Lookback | 35 minutes |
| Timeout | 8 minutes global |
| Processing | Sequential |
| Sender tracking | Yes (cross-market exclusion) |

### Deep Mode (local machine)

```bash
python -m src.main --mode deep
python -m src.main --mode deep --dry-run
python -m src.main --mode deep --lookback 720
```

| Parameter | Value |
|-----------|-------|
| Markets | No cap (all qualifying) |
| Volume min | $200 |
| Odds range | 0.05 - 0.85 |
| Categories | politics, economics, geopolitics, science |
| Lookback | 1440 minutes (24h) |
| Timeout | None |
| Processing | 5 concurrent workers, 1s batch pause |
| Sender tracking | Skipped |
| Extra blacklist | sports terms (super bowl, ufc, etc.) |

### CLI Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--mode` | `quick\|deep` | `quick` | Scan mode |
| `--dry-run` | flag | `False` | Skip all DB writes and publishing |
| `--lookback` | int (minutes) | profile default | Override trade lookback window |

### Dry-Run Behavior

When `--dry-run` is active:
- All DB writes skipped (alerts, scans, positions, tracking)
- Telegram/Twitter publishing skipped
- Sell monitoring skipped
- Full pipeline runs otherwise (fetch, analyze, score)
- Logs `[DRY-RUN] Would insert/send...` messages

---

## 15. Real Example: Alert Lifecycle (with Multi-Signal)

```
=== Scan at 2026-02-16T09:00:00Z (quick mode) ===

1. MARKET DISCOVERY
   Polymarket API returns 847 events
   After filtering: 92 markets (vol > $1K, odds 5-55%, politics/economics/geo)

2. MARKET #23: "Will Trump sign executive order on TikTok?"
   Current odds: 0.28 (YES), Volume 24h: $45,000

3. FETCH TRADES (last 35 min)
   Found 18 trades, 5 unique wallets

4. PER-WALLET ANALYSIS
   Wallet A (0x7a3f): $8,200 YES — W01+W04+B16+B18c = 130 pts
   Wallet B (0x1b2c): $3,800 YES — W02+B18b = 45 pts
   Wallet C (0x9d4e): $2,500 YES — W03+B18a = 30 pts
   Wallet D (0xab12): $4,100 YES — W01+B18b = 45 pts
   Wallet E (0xfe89): $1,200 YES — B18a = 15 pts

5. WALLET GROUPING (NEW)
   Funding fetch: all 5 wallets (>= FUNDING_GROUPING_MIN_WALLETS=2)
   ├── A, B, C ← all funded by Sender X → Group 1
   ├── D ← funded by Sender Y → Group 2
   └── E ← funded by Sender Z → Group 3

6. PER-GROUP CONFLUENCE (C filters scoped per group)
   Group 1 (A,B,C): C01 (+10, 3 wallets), C03d (+30, shared parent), C07 (+30)
   Group 2 (D):     No C filters (solo wallet)
   Group 3 (E):     No C filters (solo wallet)

7. PER-GROUP SCORING
   Group 1: best=A (130) + M01(15) + M05a(10) + C01(10) + C03d(30) + C07(30) = 225 raw
     → amount_mult=1.25, diversity=1.2 → final=337 → 5★ → validate: downgrade to 4★
   Group 2: best=D (45) + M01(15) + M05a(10) = 70 raw
     → amount_mult=1.08, diversity=1.2 → final=91 → 2★
   Group 3: best=E (15) + M01(15) + M05a(10) = 40 raw
     → amount_mult=0.80, diversity=1.0 → final=32 → 0★

8. MULTI-SIGNAL CLASSIFICATION
   ├── Group 1: PRIMARY (score=337, 4★) — published
   ├── Group 2: SECONDARY (score=91, 2★) — DB only
   └── Group 3: SECONDARY (score=32, 0★) — DB only
   alert_group_id = "a1b2c3d4-..." (shared UUID)
   multi_signal = True (3 independent groups)
   secondary_count = 2 (on primary alert)

9. PUBLISH (primary alert only)
   ├── DB: Alert #247 inserted (primary, multi_signal=True, secondary_count=2)
   ├── DB: Alert #248 inserted (secondary, is_secondary=True)
   ├── DB: Alert #249 inserted (secondary, is_secondary=True)
   ├── AlertTracking created for #247
   ├── WalletPositions created (3 wallets from Group 1)
   ├── Telegram: "SMART MONEY DETECTED — 4★ — YES on TikTok...
   │              📡 Multi-signal: 3 independent group(s)"
   └── Twitter: "SMART MONEY DETECTED — TikTok — YES $14.5K 4★"

10. TRACKING (6h later)
    ├── WhaleMonitor: No new activity
    └── Odds moved: 0.28 → 0.35 (+25%)

11. RESOLUTION (2 days later)
    ├── Market resolves: YES
    ├── is_correct = (YES == YES) = True
    ├── actual_return = ((1.0 - 0.27) / 0.27) * 100 = 270.4%
    ├── Wallet stats: markets_won += 1
    └── Notification: "✅ CORRECT — 4★ — TikTok — 270.4% return"

12. DASHBOARD (next hourly refresh)
    └── Alert #247 shown as "correct", row_class="correct", +$270 P&L
```
