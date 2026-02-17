# SENTINEL ALPHA — Estado Actual v2

> Actualizado: 2026-02-17

---

## Arquitectura General

```
src/
├── main.py                        Orquestación principal del scan
├── config.py                      Constantes, umbrales, filtros (49 filtros)
├── analysis/
│   ├── scoring.py                 Motor de scoring (raw → multiplier → final → stars)
│   ├── wallet_analyzer.py         Filtros W01-W11, O01-O03
│   ├── behavior_analyzer.py       Filtros B01-B30, N01-N10
│   ├── confluence_detector.py     Filtros C01-C07 (4 capas aditivas)
│   ├── market_analyzer.py         Filtros M01-M05
│   ├── noise_filter.py            Noticias, bots, copy-trading
│   ├── arbitrage_filter.py        Detección de arbitraje
│   └── sell_detector.py           Monitoreo de ventas
├── scanner/
│   ├── polymarket_client.py       APIs Gamma + CLOB
│   ├── blockchain_client.py       Alchemy RPC (Polygon)
│   └── news_checker.py            Correlación de noticias
├── database/
│   ├── supabase_client.py         CRUD completo contra Supabase
│   └── models.py                  Dataclasses (Alert, Wallet, FilterResult, etc.)
├── publishing/
│   ├── telegram_bot.py            Multicanal (privado, VIP, público)
│   ├── twitter_bot.py             X/Twitter (max 10 tweets/día)
│   ├── formatter.py               Formato de mensajes
│   └── chart_generator.py         Gráficos
├── tracking/
│   ├── resolver.py                Resolución de mercados
│   ├── alert_tracker.py           Seguimiento de odds
│   ├── alert_notifier.py          Notificaciones de seguimiento
│   └── whale_monitor.py           Vigilancia de alertas 4-5 estrellas
├── dashboard/
│   └── generate_dashboard.py      HTML autocontenido → docs/index.html
└── reports/
    ├── weekly.py                  Resumen semanal
    └── monthly.py                 Resumen mensual
```

---

## Workflows (GitHub Actions)

| Workflow | Schedule | Propósito |
|----------|----------|-----------|
| `scan.yml` | Cada 3 horas | Scan principal de transacciones |
| `tracker.yml` | Cada 6 horas (01,07,13,19 UTC) | Tracking de odds, notificaciones, whale monitor |
| `resolver.yml` | Diario 08:00 UTC | Resolución de mercados, accuracy |
| `dashboard.yml` | Cada hora (:15) | Genera HTML dashboard, auto-commit |
| `publish_dashboard.yml` | Después de dashboard.yml | Copia index.html → repo público sentinel-dashboard |
| `weekly_report.yml` | Semanal | Compilación de stats |
| `monthly_report.yml` | Mensual | Leaderboards, rendimiento |
| `check_resolutions.yml` | Manual | Validación de resoluciones |

---

## Scripts

| Script | Propósito | Ejecución |
|--------|-----------|-----------|
| `audit_false_confluence.py` | Audita alertas con confluencia falsa por senders de infra. Informe + recálculo opcional | `python -m scripts.audit_false_confluence` |
| `cleaner_post_deep.py` | Limpieza automática post-deep-scan: detecta infra, limpia alertas, informe | `python -m scripts.cleaner_post_deep` |
| `setup_db.py` | Verifica que todas las tablas existen en Supabase | `python -m scripts.setup_db` |
| `backfill.py` | Backfill histórico (wallet ages, funding) | `python -m scripts.backfill` |
| `test_connection.py` | Test de conexión Supabase | `python -m scripts.test_connection` |
| `test_polymarket.py` | Test de APIs Polymarket | `python -m scripts.test_polymarket` |
| `test_blockchain.py` | Test de Alchemy RPC | `python -m scripts.test_blockchain` |
| `test_telegram.py` | Test de Telegram Bot | `python -m scripts.test_telegram` |
| `test_news.py` | Test de news checker | `python -m scripts.test_news` |

---

## Base de Datos (Supabase PostgreSQL)

### Tablas Principales

| Tabla | Propósito |
|-------|-----------|
| `alerts` | Alertas generadas (score, stars, filtros, outcome, wallets JSONB) |
| `wallets` | Catálogo de wallets detectadas (age, markets, win_rate, PnL) |
| `markets` | Mercados de Polymarket (odds, volumen, liquidez, resolución) |
| `wallet_funding` | Relaciones sender → wallet (hop, exchange, bridge, mixer) |
| `wallet_positions` | Posiciones abiertas por wallet/market (para sell tracking) |
| `alert_sell_events` | Eventos de venta (metadata, no afecta score) |
| `alert_tracking` | Seguimiento de odds post-alerta |
| `market_snapshots` | Histórico de odds/volumen/liquidez |
| `scans` | Log de cada ciclo de scan |
| `smart_money_leaderboard` | Ranking de wallets por win rate y PnL |
| `system_config` | Feature flags (scan_enabled, publish_x, publish_telegram) |
| `weekly_reports` | Reportes semanales |
| `wallet_categories` | Categorización de wallets (WR01, SP01) |
| `notification_log` | Log de notificaciones enviadas |
| `whale_notifications` | Notificaciones whale específicas |
| `detected_infrastructure` | Senders de infra detectados automáticamente (>100 wallets) |

---

## Sistema de Filtros (49 filtros)

### Wallet (W) — 7 filtros

| ID | Nombre | Pts | Condición |
|----|--------|-----|-----------|
| W01 | Wallet muy nueva | +25 | Edad < 7 días |
| W02 | Wallet nueva | +20 | Edad 7-14 días |
| W03 | Wallet reciente | +15 | Edad 14-30 días |
| W04 | Solo 1 mercado | +10 | Total markets = 1 |
| W05 | Solo 2-3 mercados | +15 | Total markets 2-3 |
| W09 | Primera tx = Polymarket | +5 | Primera tx on-chain fue a PM |
| W11 | Balance redondo | +3 | Balance ≈ $5K/$10K/$50K (±1%) |

### Origin (O) — 3 filtros

| ID | Nombre | Pts | Condición |
|----|--------|-----|-----------|
| O01 | Origen exchange | +5 | Fondeada desde exchange (1-2 hops) |
| O02 | Fondeo reciente | +10 | Fondeada hace 3-7 días |
| O03 | Fondeo muy reciente | +5 | Fondeada hace 0-3 días |

### Behavior (B) — 23 filtros activos

| ID | Nombre | Pts | Condición |
|----|--------|-----|-----------|
| B01 | Acumulación goteo | +20 | 5+ compras en 24-72h |
| B05 | Solo market orders | +5 | Todas las trades son market orders |
| B06 | Tamaño creciente | +15 | Montos de compra incrementales |
| B07 | Compra contra mercado | +20 | Compra con odds < 0.20 |
| B14 | Primera compra grande | +15 | Primera compra > $5,000 |
| B16 | Acumulación rápida | +20 | 3+ compras en < 4h |
| B17 | Horario bajo | +10 | Trades 2-6 AM UTC |
| B18a | Acumulación moderada | +15 | $2,000-$3,499 acumulados |
| B18b | Acumulación significativa | +25 | $3,500-$4,999 |
| B18c | Acumulación fuerte | +35 | $5,000-$9,999 |
| B18d | Acumulación muy fuerte | +50 | $10,000+ |
| B18e | Sin impacto en precio | +15 | Acum > $2K pero odds mueven < 5% |
| B19a | Entrada grande | +20 | Single buy $5K-$9,999 (whale routing) |
| B19b | Entrada muy grande | +30 | Single buy $10K-$49,999 |
| B19c | Entrada masiva | +40 | Single buy ≥ $50,000 |
| B20 | Vieja nueva en PM | +20 | Wallet > 180d, PM activity < 7d |
| B23a | Posición significativa | +15 | 20-50% del balance en este mercado |
| B23b | Posición dominante | +30 | >50% del balance |
| B25a | Convicción extrema | +25 | Odds < 0.10 |
| B25b | Convicción alta | +15 | Odds 0.10-0.20 |
| B25c | Convicción moderada | +5 | Odds 0.20-0.35 |
| B26a | Stealth whale | +20 | >$5K acum, price move < 1% |
| B26b | Low impact | +10 | >$3K acum, price move < 3% |
| B28a | All-in extremo | +25 | >90% del balance, ≥ $3.5K |
| B28b | All-in fuerte | +20 | 70-90% del balance, ≥ $3.5K |
| N08 | Anti-bot evasión | +25 | Amount CoV < 10% (counter-bot) |

> B27 (Diamond hands) y B30 (First mover) están **deshabilitados**.

### Confluence (C) — 4 capas aditivas

**Capa 1: Dirección** (mutuamente excluyentes)

| ID | Nombre | Pts | Condición |
|----|--------|-----|-----------|
| C01 | Confluencia básica | +10 | 3+ wallets misma dirección |
| C02 | Confluencia fuerte | +15 | 5+ wallets misma dirección |

**Capa 2: Origen de fondeo** (aditivos, cada tipo dispara independiente)

| ID | Nombre | Pts | Condición |
|----|--------|-----|-----------|
| C03a | Origen exchange compartido | +5 | 2+ wallets mismo exchange |
| C03b | Origen bridge compartido | +20 | 2+ wallets mismo bridge |
| C03c | Origen mixer compartido | +30 | 2+ wallets mismo mixer |
| C03d | Mismo padre directo | +30 | 2+ wallets mismo sender no-infra |

**Capa 3: Bonus** (aditivos)

| ID | Nombre | Pts | Condición |
|----|--------|-----|-----------|
| C05 | Fondeo temporal | +10 | 3+ fondeadas desde exchange < 4h |
| C06 | Monto similar | +10 | Montos de funding ±30% similares |

**Capa 4: Red de distribución**

| ID | Nombre | Pts | Condición |
|----|--------|-----|-----------|
| C07 | Red de distribución | +30 | 1 sender → 3+ wallets activas |
| COORD04 | Fondeo via mixer | +50 | Fondeada vía Tornado Cash / Railgun |

### Market (M) — 6 filtros

| ID | Nombre | Pts | Condición |
|----|--------|-----|-----------|
| M01 | Volumen anómalo | +15 | Vol 24h > 2x promedio 7d |
| M02 | Odds estables rotos | +20 | Estables >48h, luego movimiento >10% |
| M03 | Baja liquidez | +10 | Liquidez < $100K |
| M04a | Concentración moderada | +15 | Top 3 wallets > 60% del volumen |
| M04b | Concentración alta | +25 | Top 3 wallets > 80% del volumen |
| M05a | Deadline <72h | +10 | Resolución < 72 horas |
| M05b | Deadline <24h | +15 | Resolución < 24 horas |
| M05c | Deadline <6h | +25 | Resolución < 6 horas |

### Negative (N) — 15 filtros

| ID | Nombre | Pts | Condición |
|----|--------|-----|-----------|
| N01 | Bot | -40 | Intervalos regulares |
| N02 | Noticias | -20 | Noticias relacionadas en 24h |
| N03 | Arbitraje | -100 | Posiciones opuestas (hard kill) |
| N04 | Mercados opuestos | 0 | Logging only |
| N05 | Copy-trading | -25 | Trades 2-10 min después de otra wallet |
| N06a | Degen leve | -5 | 1-2 mercados no-políticos |
| N06b | Degen moderado | -15 | 3-5 mercados no-políticos |
| N06c | Degen fuerte | -30 | 6+ mercados no-políticos |
| N07a | Scalper leve | -20 | Buy+sell en <2h |
| N07b | Scalper serial | -40 | Flip en 3+ mercados |
| N09a | Apuesta obvia extrema | -40 | Odds > 0.90 → cap 2 estrellas |
| N09b | Apuesta obvia | -25 | Odds > 0.85 → cap 3 estrellas |
| N10a | Horizonte lejano | -10 | Resolución > 30 días |
| N10b | Horizonte muy lejano | -20 | Resolución > 60 días |
| N10c | Horizonte extremo | -30 | Resolución > 90 días |

### Grupos mutuamente excluyentes (17 grupos)

Dentro de cada grupo solo sobrevive el filtro con mayor `|points|`:

- W01/W02/W03, W04/W05
- B18a/B18b/B18c/B18d, B19a/B19b/B19c
- B23a/B23b, B25a/B25b/B25c, B26a/B26b
- B27a/B27b, B28a/B28b (también mut. excl. con B23)
- B30a/B30b/B30c, C01/C02
- M04a/M04b, M05a/M05b/M05c
- N06a/N06b/N06c, N07a/N07b, N09a/N09b
- N10a/N10b/N10c

---

## Sistema de Scoring

### Pipeline de cálculo

```
1. Exclusión mutua → solo el más fuerte por grupo
2. score_raw = sum(puntos de filtros sobrevivientes)  [floor 0]
3. Amount multiplier = 0.18 × ln(total_usd) - 0.37   [clamped 0.3 – 2.0]
4. Diversity multiplier:
     ≤3 mercados  → 1.2x (sniper)
     4-9 mercados → 1.0x
     10-19        → 0.7x (shotgun)
     ≥20          → 0.5x (super shotgun)
5. multiplier_final = amount × diversity
6. score_final = min(400, round(score_raw × multiplier_final))
7. star_level = lookup(score_final)
8. Validación de estrellas (requisitos mínimos)
9. Cap por N09 (obvious bet)
```

### Curva de amount multiplier

| Monto | Multiplier |
|-------|-----------|
| $100 | 0.46x |
| $500 | 0.75x |
| $1,000 | 0.87x |
| $5,000 | 1.17x |
| $10,000 | 1.29x |
| $50,000 | 1.57x |
| $100,000 | 1.70x |

### Umbrales de estrellas

| Score | Estrellas | DB | Web | Telegram | X |
|-------|-----------|----|----|----------|---|
| ≥ 220 | 5 | si | si | si | si |
| 150-219 | 4 | si | si | si | si |
| 100-149 | 3 | si | si | si | si |
| 70-99 | 2 | si | si | si | no |
| 40-69 | 1 | si | si | no | no |
| 0-39 | 0 | si | no | no | no |

### Validación de estrellas

| Estrellas | Requisito |
|-----------|-----------|
| 3 | Min 2 categorías |
| 4 | Min 2 categorías + ≥$5,000 |
| 5 | Min 3 categorías + ≥$10,000 + COORDINATION obligatorio |

### Cap por apuesta obvia

- N09a (odds > 0.90) → max 2 estrellas
- N09b (odds > 0.85) → max 3 estrellas

---

## Exclusión de Infraestructura

### Listas estáticas (config.py)

**KNOWN_EXCHANGES** (11 direcciones):
Coinbase (3), Binance (3), Kraken, OKX, Crypto.com, Gate.io, Bybit

**KNOWN_BRIDGES** (7 direcciones):
Polygon PoS, Polygon Plasma, Multichain, Hop Protocol, Across Protocol, Stargate (2)

**KNOWN_INFRASTRUCTURE** (3 direcciones):
| Dirección | Etiqueta |
|-----------|----------|
| `0xf70da97812cb96acdf810712aa562db8dfa3dbef` | Relay Solver (Polygon) |
| `0x3a3bd7bb9528e159577f7c2e685cc81a765002e2` | Polymarket Wrapped Collateral |
| `0xc288480574783bd7615170660d71753378159c47` | Bridge/router Polygon |

**POLYMARKET_CONTRACTS** (3 direcciones):
CTF Exchange, NegRisk CTF Exchange, Polymarket Proxy

**MIXER_ADDRESSES** (7 direcciones):
Tornado Cash (5), Railgun (2)

### Umbral automático

`SENDER_AUTO_EXCLUDE_MIN_WALLETS = 100`

Cualquier sender que financie ≥100 wallets distintas en `wallet_funding` se excluye automáticamente de C03d/C07. Se cachea una vez al inicio del scan via `refresh_excluded_senders()`.

### Flujo de exclusión en confluence_detector

```
ConfluenceDetector.__init__()
  └── _excluded_cache = None

refresh_excluded_senders()  ← llamado 1x al inicio del scan
  ├── POLYMARKET_CONTRACTS (hardcoded)
  ├── KNOWN_INFRASTRUCTURE (hardcoded)
  ├── get_high_fanout_senders(100)  ← query DB
  └── _excluded_cache = combined set

_build_default_excluded()  ← llamado por detect() en cada mercado
  └── return copy of _excluded_cache (lazy init si no se llamó refresh)
```

### Cleaner post-deep-scan

`scripts/cleaner_post_deep.py` — ejecutado después de cada deep scan:

1. Detecta senders de infra nuevos (>100 wallets) → persiste en `detected_infrastructure`
2. Audita alertas pending con C03d/C07/C06/C01/C02, expande prefijos truncados, elimina filtros falsos
3. Recalcula score/stars y actualiza en Supabase
4. Idempotente: segunda ejecución → 0 cambios

---

## Multi-Signal Grouping

Cuando múltiples wallets apuestan en el mismo mercado:

1. Se agrupan por sender compartido (union-find)
2. Cada grupo genera alerta independiente con su propio score
3. Si hay 2+ grupos: `multi_signal=True`
4. Grupo de mayor score → primary (se publica)
5. Resto → secondary (solo DB, `is_secondary=True`)
6. `alert_group_id` compartido (UUID) para tracking

---

## Consolidación de Alertas

Cuando llega una nueva alerta para un mercado+dirección existente con 4-5 estrellas:

1. Busca alerta existente 4-5 estrellas en últimas 48h
2. Merge: añade wallets, actualiza `total_amount`, `confluence_count`
3. Actualiza score si el nuevo es mayor
4. Envía mensaje de actualización a Telegram (no nueva alerta)
5. No inserta alerta duplicada

---

## Sell Detection

Monitoreo de posiciones (no afecta score, solo metadata):

- Mínimo 3 estrellas para monitorear
- Ignora exits < 20% de la posición
- Cooldown 6h entre eventos de venta por alerta
- Notificación Telegram solo para 4-5 estrellas
- Registra: `sell_amount`, `sell_pct`, `held_hours`, `pnl_pct`

---

## Resolución de Alertas

Workflow diario (08:00 UTC):

1. Fetch alertas con `outcome='pending'`
2. Consulta resolución del mercado via Polymarket API
3. Compara dirección de la alerta vs. outcome real
4. Marca `outcome` = "correct" / "incorrect"
5. Actualiza `wallets.markets_won/lost`, `win_rate`
6. Actualiza `smart_money_leaderboard`
7. Publica resolución en Telegram/X (opcional)

---

## Dashboard

- Generado cada hora por `dashboard.yml`
- Datos: últimas 5000 alertas + markets + scans + sell events
- Output: `docs/index.html` (HTML autocontenido)
- Publicado automáticamente en repo público `sentinel-dashboard` via `publish_dashboard.yml`
- URL pública: `https://sentinelalphax0a-tech.github.io/sentinel-dashboard/`

---

## Configuración Clave

### Scan

| Parámetro | Valor | Descripción |
|-----------|-------|-------------|
| `MIN_TX_AMOUNT` | $100 | Mínimo por trade |
| `MIN_ACCUMULATED_AMOUNT` | $350 | Mínimo acumulado por wallet/mercado |
| `ACCUMULATION_WINDOW_HOURS` | 72 | Ventana de acumulación |
| `SCAN_INTERVAL_MINUTES` | 30 | Intervalo entre scans |
| `SCAN_LOOKBACK_MINUTES` | 35 | Ventana de trades |
| `SCAN_TIMEOUT_SECONDS` | 480 | Timeout global (8 min) |
| `MAX_WALLETS_PER_MARKET` | 10 | Top N wallets por volumen |
| `MARKET_SCAN_CAP` | 100 | Max mercados por ciclo (quick) |
| `CROSS_SCAN_DEDUP_HOURS` | 24 | Ventana de deduplicación |

### Odds

| Parámetro | Valor | Aplica cuando |
|-----------|-------|---------------|
| `ODDS_MIN` | 0.05 | Siempre |
| `ODDS_MAX` | 0.55 | Score < 90 |
| `ODDS_MAX_EXTENDED` | 0.70 | Score ≥ 90 |

### Publicación

| Canal | Mínimo estrellas |
|-------|------------------|
| DB | 0 |
| Web dashboard | 1 |
| Telegram privado | 2 |
| Telegram VIP | 4 (+ whale entries) |
| Telegram público | 3 |
| X/Twitter | 3 (max 10/día) |

---

## Tests

**554 tests** organizados en 25 archivos de test:

| Área | Tests |
|------|-------|
| Confluence detector | 44 tests (capas, exclusiones, grouping) |
| Scoring engine | 48 tests (stars, multipliers, validation) |
| Behavior analyzer | 80+ tests (B filters, mutual exclusion) |
| Main scan | 50+ tests (orchestration, publish routing) |
| Noise/Arbitrage | 30+ tests |
| Telegram/Twitter | 40+ tests |
| Sell detection | 30+ tests |
| Otros | Tracker, resolver, formatter, dashboard |

Todos pasan: `554 passed, 0 failed`.

---

## Stack Técnico

- **Lenguaje**: Python 3.11+
- **Base de datos**: Supabase (PostgreSQL)
- **Blockchain**: Alchemy RPC (Polygon)
- **APIs**: Polymarket Gamma + CLOB, News aggregators
- **Publicación**: Telegram Bot API, X/Twitter API (Tweepy)
- **Orquestación**: GitHub Actions (cron)
- **Dashboard**: HTML autocontenido (auto-commit cada hora)
- **Repo principal**: `sentinelalphax0a-tech/SA` (privado)
- **Dashboard público**: `sentinelalphax0a-tech/sentinel-dashboard`
