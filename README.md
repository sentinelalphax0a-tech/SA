# SENTINEL ALPHA — Polymarket Hunter

> Documento técnico y referencia completa del proyecto.
> Confidencial. No distribuir.
> Última actualización: Febrero 2026

---

## Índice

1. [Visión General](#1-visión-general)
2. [Stack Tecnológico](#2-stack-tecnológico)
3. [Cuentas y Servicios](#3-cuentas-y-servicios)
4. [Estructura del Proyecto](#4-estructura-del-proyecto)
5. [Base de Datos — Esquema Supabase](#5-base-de-datos--esquema-supabase)
6. [Sistema de Filtros y Scoring](#6-sistema-de-filtros-y-scoring)
7. [Lógica de Detección](#7-lógica-de-detección)
8. [Sistema de Publicación (X + Telegram)](#8-sistema-de-publicación-x--telegram)
9. [GitHub Actions — Workflows](#9-github-actions--workflows)
10. [Web Pública — GitHub Pages](#10-web-pública--github-pages)
11. [Reportes Semanales y Mensuales](#11-reportes-semanales-y-mensuales)
12. [Configuración del Sistema (Flags / Kill Switch)](#12-configuración-del-sistema)
13. [Roadmap de Desarrollo](#13-roadmap-de-desarrollo)

---

## 1. Visión General

### Qué es

Sentinel Alpha es un sistema automatizado de detección de smart money en Polymarket. Identifica señales de dinero informado: wallets que acumulan posiciones progresivamente, confluencia de múltiples wallets conectadas por fuentes de fondeo comunes, volúmenes anómalos sin justificación pública, y patrones de distribución de fondos desde wallets intermediarias.

### Qué NO es

- No es un sistema de trading automatizado (no compra ni vende).
- No es un detector forense con ML (eso viene en fases posteriores).
- No compite con Polysights en velocidad, compite en calidad de señal.

### Marca pública

- **X:** @SentinelAlpha (o variante elegida)
- **Display name:** Sentinel Alpha | Polymarket Hunter
- **Telegram:** Canal público @SentinelAlphaChannel

### Diferencia con la competencia

| Característica | Sentinel Alpha | Insider Finder (Polysights) | Unusual Whales |
|---|---|---|---|
| Scoring progresivo por acumulación | ✅ | ❌ | ❌ |
| Confluencia por fuente de fondeo | ✅ | ❌ | ❌ |
| Detección de red de distribución | ✅ | ❌ | ❌ |
| Filtro anti-degen | ✅ | ❌ | ❌ |
| Micro-acumulación escalonada | ✅ | ❌ | ❌ |
| Detección mercados opuestos | ✅ | ❌ | ❌ |
| Anti copy-trading | ✅ | ❌ | ❌ |
| Canal Telegram | ✅ | ❌ | ❌ |

---

## 2. Stack Tecnológico

### Core

- **Lenguaje:** Python 3.11+
- **Ejecución:** GitHub Actions (cron cada 30 min)
- **Base de datos:** Supabase (PostgreSQL)
- **Web:** GitHub Pages (HTML + JS + Chart.js)

### APIs externas

| Servicio | Uso | Free Tier | API Key |
|---|---|---|---|
| Polymarket CLOB API | Mercados, órdenes, trades | Sin límite | No |
| Polygon RPC (Alchemy) | Datos on-chain | 300M compute units/mes | Sí |
| X/Twitter API | Publicar alertas | 1,500 tweets/mes | Sí |
| Telegram Bot API | Publicar alertas | Sin límite | Sí (token) |
| Google News RSS | Verificar noticias | Sin límite | No |

### Librerías Python

```
requests
tweepy
supabase
web3
matplotlib
python-dotenv
feedparser
pytest
black
```

---

## 3. Cuentas y Servicios

### Variables de entorno (.env)

```env
# === SUPABASE ===
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=service_role_key
SUPABASE_ANON_KEY=anon_key

# === ALCHEMY ===
ALCHEMY_API_KEY=xxxxx
ALCHEMY_ENDPOINT=https://polygon-mainnet.g.alchemy.com/v2/xxxxx

# === TWITTER/X ===
TWITTER_API_KEY=xxxxx
TWITTER_API_SECRET=xxxxx
TWITTER_ACCESS_TOKEN=xxxxx
TWITTER_ACCESS_SECRET=xxxxx

# === TELEGRAM ===
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHANNEL_ID=-1001234567890

# === CONFIGURACIÓN ===
MIN_TX_AMOUNT=100
MIN_ACCUMULATED_AMOUNT=350
ACCUMULATION_WINDOW_HOURS=72
SCAN_INTERVAL_MINUTES=30
PUBLISH_SCORE_THRESHOLD_X=70
PUBLISH_SCORE_THRESHOLD_TELEGRAM=50
ODDS_MIN=0.05
ODDS_MAX=0.55
ODDS_MAX_EXTENDED=0.70
MAX_TWEETS_PER_DAY=10
```

### GitHub Secrets (repo privado)

Mismos valores, en: Settings → Secrets and variables → Actions

### Repositorios

- **sentinel-alpha** (PRIVADO): Todo el código y lógica.
- **sentinel-alpha-web** (PÚBLICO): Solo dashboard web. Se crea en Fase 5.

---

## 4. Estructura del Proyecto

```
SA/
├── .github/
│   └── workflows/
│       ├── scan.yml
│       ├── weekly_report.yml
│       └── monthly_report.yml
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── main.py
│   ├── scanner/
│   │   ├── __init__.py
│   │   ├── polymarket_client.py
│   │   ├── blockchain_client.py
│   │   └── news_checker.py
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── wallet_analyzer.py
│   │   ├── behavior_analyzer.py
│   │   ├── confluence_detector.py
│   │   ├── market_analyzer.py
│   │   ├── noise_filter.py
│   │   ├── arbitrage_filter.py
│   │   └── scoring.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── supabase_client.py
│   │   └── models.py
│   ├── publishing/
│   │   ├── __init__.py
│   │   ├── twitter_bot.py
│   │   ├── telegram_bot.py
│   │   ├── formatter.py
│   │   └── chart_generator.py
│   └── reports/
│       ├── __init__.py
│       ├── weekly.py
│       └── monthly.py
├── tests/
│   ├── __init__.py
│   ├── test_wallet_analyzer.py
│   ├── test_behavior_analyzer.py
│   ├── test_confluence.py
│   ├── test_scoring.py
│   ├── test_noise_filter.py
│   └── test_formatter.py
├── scripts/
│   ├── setup_db.py
│   └── backfill.py
├── .env.example
├── .env
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 5. Base de Datos — Esquema Supabase

Ejecutar en Supabase SQL Editor:

```sql
-- =============================================
-- SENTINEL ALPHA — ESQUEMA COMPLETO
-- =============================================

CREATE TABLE wallets (
    address          TEXT PRIMARY KEY,
    first_seen       TIMESTAMP NOT NULL DEFAULT NOW(),
    wallet_age_days  INTEGER,
    category         TEXT DEFAULT 'unknown',
    total_markets    INTEGER DEFAULT 0,
    total_markets_pm INTEGER DEFAULT 0,
    non_pm_markets   INTEGER DEFAULT 0,
    markets_won      INTEGER DEFAULT 0,
    markets_lost     INTEGER DEFAULT 0,
    win_rate         REAL DEFAULT 0.0,
    total_pnl        REAL DEFAULT 0.0,
    times_detected   INTEGER DEFAULT 0,
    last_activity    TIMESTAMP,
    origin_exchange  TEXT,
    funding_sources  JSONB,
    is_first_tx_pm   BOOLEAN DEFAULT FALSE,
    is_blacklisted   BOOLEAN DEFAULT FALSE,
    blacklist_reason TEXT,
    degen_score      INTEGER DEFAULT 0,
    notes            TEXT,
    created_at       TIMESTAMP DEFAULT NOW(),
    updated_at       TIMESTAMP DEFAULT NOW()
);

CREATE TABLE markets (
    market_id       TEXT PRIMARY KEY,
    slug            TEXT,
    question        TEXT NOT NULL,
    category        TEXT,
    current_odds    REAL,
    volume_24h      REAL DEFAULT 0.0,
    volume_7d_avg   REAL DEFAULT 0.0,
    liquidity       REAL DEFAULT 0.0,
    resolution_date TIMESTAMP,
    is_resolved     BOOLEAN DEFAULT FALSE,
    outcome         TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    opposite_market TEXT,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE alerts (
    id                 SERIAL PRIMARY KEY,
    timestamp          TIMESTAMP NOT NULL DEFAULT NOW(),
    market_id          TEXT REFERENCES markets(market_id),
    market_question    TEXT,
    alert_type         TEXT NOT NULL,
    direction          TEXT,
    score_raw          INTEGER,
    multiplier         REAL DEFAULT 1.0,
    score              INTEGER NOT NULL,
    star_level         INTEGER,
    wallets            JSONB,
    total_amount       REAL,
    odds_at_alert      REAL,
    price_impact       REAL,
    confluence_count   INTEGER DEFAULT 1,
    confluence_type    TEXT,
    has_news           BOOLEAN DEFAULT FALSE,
    news_summary       TEXT,
    is_arbitrage       BOOLEAN DEFAULT FALSE,
    opposite_positions JSONB,
    filters_triggered  JSONB,
    published_x        BOOLEAN DEFAULT FALSE,
    published_telegram BOOLEAN DEFAULT FALSE,
    tweet_id           TEXT,
    telegram_msg_id    TEXT,
    outcome            TEXT DEFAULT 'pending',
    resolved_at        TIMESTAMP,
    created_at         TIMESTAMP DEFAULT NOW()
);

CREATE TABLE wallet_funding (
    id              SERIAL PRIMARY KEY,
    wallet_address  TEXT REFERENCES wallets(address),
    sender_address  TEXT NOT NULL,
    amount          REAL,
    timestamp       TIMESTAMP,
    hop_level       INTEGER DEFAULT 1,
    is_exchange     BOOLEAN DEFAULT FALSE,
    exchange_name   TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE scans (
    id                    SERIAL PRIMARY KEY,
    timestamp             TIMESTAMP NOT NULL DEFAULT NOW(),
    markets_scanned       INTEGER DEFAULT 0,
    transactions_analyzed INTEGER DEFAULT 0,
    wallets_analyzed      INTEGER DEFAULT 0,
    alerts_generated      INTEGER DEFAULT 0,
    alerts_published_x    INTEGER DEFAULT 0,
    alerts_published_tg   INTEGER DEFAULT 0,
    duration_seconds      REAL,
    errors                TEXT,
    status                TEXT DEFAULT 'success'
);

CREATE TABLE weekly_reports (
    id                SERIAL PRIMARY KEY,
    week_start        DATE NOT NULL,
    week_end          DATE NOT NULL,
    total_alerts      INTEGER DEFAULT 0,
    alerts_by_stars   JSONB,
    alerts_correct    INTEGER DEFAULT 0,
    alerts_incorrect  INTEGER DEFAULT 0,
    alerts_pending    INTEGER DEFAULT 0,
    accuracy_rate     REAL,
    accuracy_by_stars JSONB,
    top_markets       JSONB,
    top_wallets       JSONB,
    chart_url         TEXT,
    tweet_id          TEXT,
    telegram_msg_id   TEXT,
    created_at        TIMESTAMP DEFAULT NOW()
);

CREATE TABLE smart_money_leaderboard (
    address         TEXT PRIMARY KEY REFERENCES wallets(address),
    markets_entered INTEGER DEFAULT 0,
    markets_won     INTEGER DEFAULT 0,
    markets_lost    INTEGER DEFAULT 0,
    win_rate        REAL DEFAULT 0.0,
    total_invested  REAL DEFAULT 0.0,
    estimated_pnl   REAL DEFAULT 0.0,
    avg_entry_odds  REAL,
    best_trade      JSONB,
    last_seen       TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE system_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMP DEFAULT NOW()
);

INSERT INTO system_config (key, value) VALUES
    ('system_status', 'active'),
    ('maintenance_message', 'System under maintenance. Back soon.'),
    ('countdown_date', ''),
    ('countdown_message', 'Something bigger is coming.'),
    ('publish_x', 'true'),
    ('publish_telegram', 'true'),
    ('scan_enabled', 'true');

-- INDEXES
CREATE INDEX idx_alerts_timestamp ON alerts(timestamp DESC);
CREATE INDEX idx_alerts_market ON alerts(market_id);
CREATE INDEX idx_alerts_score ON alerts(score DESC);
CREATE INDEX idx_alerts_star_level ON alerts(star_level);
CREATE INDEX idx_alerts_outcome ON alerts(outcome);
CREATE INDEX idx_alerts_published_x ON alerts(published_x);
CREATE INDEX idx_alerts_published_tg ON alerts(published_telegram);
CREATE INDEX idx_wallets_category ON wallets(category);
CREATE INDEX idx_wallets_last_activity ON wallets(last_activity DESC);
CREATE INDEX idx_wallets_blacklisted ON wallets(is_blacklisted);
CREATE INDEX idx_markets_active ON markets(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_scans_timestamp ON scans(timestamp DESC);
CREATE INDEX idx_funding_wallet ON wallet_funding(wallet_address);
CREATE INDEX idx_funding_sender ON wallet_funding(sender_address);
CREATE INDEX idx_funding_timestamp ON wallet_funding(timestamp DESC);

-- ROW LEVEL SECURITY
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE markets ENABLE ROW LEVEL SECURITY;
ALTER TABLE wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE wallet_funding ENABLE ROW LEVEL SECURITY;
ALTER TABLE weekly_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE smart_money_leaderboard ENABLE ROW LEVEL SECURITY;
ALTER TABLE scans ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_config ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read alerts" ON alerts FOR SELECT USING (true);
CREATE POLICY "Public read markets" ON markets FOR SELECT USING (true);
CREATE POLICY "Public read reports" ON weekly_reports FOR SELECT USING (true);
CREATE POLICY "Public read leaderboard" ON smart_money_leaderboard FOR SELECT USING (true);
CREATE POLICY "Public read wallets" ON wallets FOR SELECT USING (true);
CREATE POLICY "Public read scans" ON scans FOR SELECT USING (true);
CREATE POLICY "Public read config" ON system_config FOR SELECT USING (true);
```

---

## 6. Sistema de Filtros y Scoring

### 6.1 Umbrales de Ingesta

```python
MIN_TX_AMOUNT = 100
MIN_ACCUMULATED_AMOUNT = 350
ACCUMULATION_WINDOW_HOURS = 72
ACCUMULATION_WINDOW_DAYS_7 = 7
ACCUMULATION_WINDOW_DAYS_14 = 14
```

### 6.2 Filtros de Wallet (W) — 7 filtros

| ID | Filtro | Condición | Pts |
|---|---|---|---|
| W01 | Wallet muy nueva | < 7 días | +25 |
| W02 | Wallet nueva | 7-14 días | +20 |
| W03 | Wallet reciente | 14-30 días | +15 |
| W04 | Solo 1 mercado | total_markets == 1 | +25 |
| W05 | Solo 2-3 mercados | total_markets <= 3 | +15 |
| W09 | Primera tx = Polymarket | solo PM | +20 |
| W11 | Balance redondo | $5k/$10k/$50k ±1% | +3 |

### 6.3 Filtros de Origen (O) — 3 filtros

| ID | Filtro | Condición | Pts |
|---|---|---|---|
| O01 | Origen exchange | Coinbase/Binance/Kraken 1-2 saltos | +15 |
| O02 | Fondeo reciente | < 7 días | +10 |
| O03 | Fondeo muy reciente | < 3 días | +15 |

### 6.4 Filtros de Comportamiento (B) — 14 filtros

**Básicos:**

| ID | Filtro | Condición | Pts |
|---|---|---|---|
| B01 | Acumulación goteo | 5+ compras 24-72h | +20 |
| B05 | Solo market orders | no limit orders | +5 |
| B06 | Tamaño creciente | cada compra > anterior | +15 |
| B07 | Compra contra mercado | odds < 0.20 | +20 |
| B14 | Primera compra grande | > $5,000 | +15 |
| B16 | Acumulación rápida | 3+ en < 4h | +20 |
| B17 | Horario bajo | 2-6 AM UTC | +10 |

**Acumulación progresiva (B18a-d mutuamente excluyentes, B18e bonus):**

| ID | Filtro | Condición | Pts |
|---|---|---|---|
| B18a | Acumulación moderada | $2,000-$3,499 en 7d | +15 |
| B18b | Acumulación significativa | $3,500-$4,999 en 7d | +25 |
| B18c | Acumulación fuerte | $5,000-$9,999 en 14d | +35 |
| B18d | Acumulación muy fuerte | $10,000+ en 14d | +50 |
| B18e | Sin impacto en precio | acum >$2k, odds mov <5% | +15 bonus |

**Entradas grandes (solo Telegram, mutuamente excluyentes):**

| ID | Filtro | Condición | Pts | Canal |
|---|---|---|---|---|
| B19a | Entrada grande | $5k-$9,999 de golpe | +20 | Solo TG |
| B19b | Entrada muy grande | $10k-$49,999 | +30 | Solo TG |
| B19c | Entrada masiva | $50,000+ | +40 | Solo TG |

**Wallet vieja nueva en PM:**

| ID | Filtro | Condición | Pts |
|---|---|---|---|
| B20 | Vieja nueva en PM | wallet >180d, PM <7d | +20 |

### 6.5 Filtros de Confluencia (C) — 7 filtros

**Por dirección (mutuamente excluyentes):**

| ID | Filtro | Condición | Pts |
|---|---|---|---|
| C01 | Confluencia básica | 3+ wallets misma dir, 48h | +25 |
| C02 | Confluencia fuerte | 5+ wallets | +40 |

**Por fuente de fondeo (mutuamente excluyentes C03/C04):**

| ID | Filtro | Condición | Pts |
|---|---|---|---|
| C03 | Mismo intermediario | 2+ wallets comparten sender salto 1-2 | +35 |
| C04 | Mismo intermediario + misma dir | C03 + apuestan igual | +50 |

**Temporal y distribución:**

| ID | Filtro | Condición | Pts |
|---|---|---|---|
| C05 | Fondeo temporal | 3+ wallets fondeadas <4h + misma dir | +30 |
| C06 | Monto similar | montos ±30% | +15 bonus |
| C07 | Red de distribución | 1 wallet → 3+ wallets que apuestan en PM | +60 |

### 6.6 Filtros de Mercado (M) — 3 filtros

| ID | Filtro | Condición | Pts |
|---|---|---|---|
| M01 | Volumen anómalo | vol 24h > 2x media 7d | +15 |
| M02 | Odds estables rotos | estable >48h + mov >10% | +20 |
| M03 | Baja liquidez | < $100k | +10 |

### 6.7 Filtros Negativos (N) — 8 filtros

| ID | Filtro | Condición | Pts |
|---|---|---|---|
| N01 | Bot | std_dev intervalos ≈ 0 | -40 |
| N02 | Noticias | Google News RSS 24h | -20 |
| N03 | Arbitraje | YES+NO mercados equiv | -100 |
| N04 | Mercados opuestos | posiciones inversas | 0 (marcar) |
| N05 | Copy-trading | 2-10 min después de whale | -25 |
| N06a | Degen leve | 1-2 mercados no políticos | -5 |
| N06b | Degen moderado | 3-5 variados | -15 |
| N06c | Degen fuerte | 6+ de todo tipo | -30 |

### 6.8 Rango de Odds

```python
ODDS_MIN = 0.05
ODDS_MAX = 0.55
ODDS_MAX_EXTENDED = 0.70  # Solo si score >= 90
```

### 6.9 Score y Multiplicadores

```python
def calculate_score(filters_triggered):
    score_raw = max(0, sum(f['points'] for f in filters_triggered))
    multiplier = 1.0
    ids = {f['id'] for f in filters_triggered}

    # P1: Insider clásico (x1.3)
    if {'W01','W09','O03'}.issubset(ids) and ids.intersection({'C01','C02','C04','C07'}):
        multiplier = 1.3

    # P2: Coordinación (x1.2)
    if {'C02','M01'}.issubset(ids) and 'N02' not in ids:
        multiplier = max(multiplier, 1.2)

    # P3: Urgencia (x1.15)
    if len({'B16','B05','B17'}.intersection(ids)) >= 2:
        multiplier = max(multiplier, 1.15)

    # P4: Fragmentación silenciosa (x1.25)
    if ids.intersection({'B18c','B18d'}) and 'B18e' in ids and 'W04' in ids:
        multiplier = max(multiplier, 1.25)

    # P5: Acumulación silenciosa (x1.3)
    if 'B18d' in ids and 'B18e' in ids and 'M02' in ids:
        multiplier = max(multiplier, 1.3)

    # P6: Red de distribución (x1.4) — EL MÁS FUERTE
    if 'C07' in ids and ids.intersection({'W01','W02','W03'}):
        multiplier = max(multiplier, 1.4)

    score_final = int(score_raw * multiplier)

    if score_final >= 120: star = 5
    elif score_final >= 90: star = 4
    elif score_final >= 70: star = 3
    elif score_final >= 50: star = 2
    elif score_final >= 30: star = 1
    else: star = 0

    return score_raw, multiplier, score_final, star
```

### 6.10 Niveles de Alerta

| ⭐ | Score | DB | Web | TG | X |
|---|---|---|---|---|---|
| 0 | 0-29 | ✅ | ❌ | ❌ | ❌ |
| 1 | 30-49 | ✅ | ✅ | ❌ | ❌ |
| 2 | 50-69 | ✅ | ✅ | ✅ | ❌ |
| 3 | 70-89 | ✅ | ✅ | ✅ | ✅ |
| 4 | 90-119 | ✅ | ✅ | ✅ | ✅+ |
| 5 | 120+ | ✅ | ✅ | ✅ | ✅🚨 |

B19 whale entries → siempre Telegram.

### 6.11 Totales: 42 filtros | 6 multiplicadores

---

## 7. Lógica de Detección

### Flujo (cada 30 min)

```
1. CHECK system_config → scan_enabled?
2. GET active markets (política, economía, geopolítica)
3. GET trades last 35 min, filter tx < $100
4. GROUP by wallet, calc accumulated per wallet+market (72h/7d/14d)
5. IF accumulated > $350 → ANALYZE:
   ├─ W filters (wallet age, markets, first tx)
   ├─ O filters (origin, funding recency)
   │   └─ SAVE funding sources to wallet_funding table
   ├─ B filters (behavior, accumulation tiers, whale entry)
   ├─ N filters (bot, news, degen, copy-trading)
   └─ Individual score
6. CONFLUENCE per market:
   ├─ Direction: group by market+direction (C01/C02)
   ├─ Funding: cross wallet_funding senders (C03/C04)
   ├─ Temporal: compare funding timestamps (C05)
   ├─ Amounts: compare funding amounts (C06)
   ├─ Distribution: find 1 sender → 3+ active wallets (C07)
   └─ Recalculate with multipliers
7. CHECK odds range
8. GENERATE alerts → DB, TG, X
9. LOG scan
```

### Confluencia por fondeo (pseudocódigo)

```python
def detect_funding_confluence(wallets_in_market):
    funding_map = {}  # sender → [wallets funded]
    for wallet in wallets_in_market:
        sources = db.get_funding_sources(wallet)
        for s in sources:
            funding_map.setdefault(s.sender, []).append({
                'wallet': wallet, 'amount': s.amount, 'time': s.timestamp
            })

    results = []
    for sender, funded in funding_map.items():
        if len(funded) >= 2:
            times = [f['time'] for f in funded]
            amounts = [f['amount'] for f in funded]
            avg = sum(amounts)/len(amounts)
            results.append({
                'sender': sender,
                'count': len(funded),
                'time_spread_h': (max(times)-min(times)).seconds/3600,
                'similar_amounts': all(abs(a-avg)/avg < 0.3 for a in amounts),
                'is_distribution': len(funded) >= 3
            })
    return results
```

---

## 8. Sistema de Publicación

### X (3+ estrellas, no revela filtros)

```
🔍 SMART MONEY DETECTED — Sentinel Alpha

📊 "¿Dimite el ministro X?"
📈 Odds: 0.08 → 0.14 (+75%)
💰 $47,200 in YES positions
👛 3 coordinated wallets
⚡ ⭐⭐⭐

⚠️ Not financial advice. DYOR.
#Polymarket #SmartMoney
```

### Telegram (2+ estrellas, incluye score)

```
🔍 SMART MONEY DETECTED

📊 "¿Dimite el ministro X?"
📈 Odds: 0.08 → 0.14 (+75%)
💰 $47,200 in YES
👛 3 coordinated wallets
📅 Last 48h | 🏦 Exchange-funded
⚡ ⭐⭐⭐ (Score: 75)
⚠️ DYOR
```

### Telegram whale (B19, siempre)

```
🐋 WHALE ENTRY — Sentinel Alpha

📊 "Will X resign?"
💰 $25,000 YES (single tx)
📈 Impact: +3.2% (0.12 → 0.15)
👛 45 days old
ℹ️ Monitoring.
```

### Rules: X max 10/day. TG unlimited. B19 always TG.

---

## 9-13. Workflows, Web, Reports, Config, Roadmap

(See sections 9-13 in previous versions — unchanged)

### GitHub Actions: scan.yml (*/30 * * * *), weekly (0 8 * * 1), monthly (0 8 1 * *)
### Web: GitHub Pages from public repo, reads Supabase anon key
### Reports: weekly + monthly with Chart.js graphs, by star level
### Config: system_config table controls everything from Supabase dashboard
### Roadmap: 5 phases over 14 days

---

*Sentinel Alpha — Polymarket Hunter*
*42 filtros | 6 multiplicadores | Confluencia por fondeo*
*Confidencial.*
