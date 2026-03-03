# Sentinel Alpha

> **CONFIDENCIAL — USO PERSONAL**
> Sistema privado de investigación. No compartir ni distribuir.
> Última actualización: marzo 2026

---

## Índice

1. [Qué es Sentinel Alpha](#1-qué-es-sentinel-alpha)
2. [Evolución del proyecto](#2-evolución-del-proyecto)
3. [Cómo funciona — flujo del scan](#3-cómo-funciona--flujo-del-scan)
4. [Estructura del proyecto](#4-estructura-del-proyecto)
5. [Configuración y variables de comportamiento](#5-configuración-y-variables-de-comportamiento)
6. [Comandos](#6-comandos)
7. [Base de datos](#7-base-de-datos)
8. [CI/CD — GitHub Actions](#8-cicd--github-actions)
9. [Costes](#9-costes)
10. [Estado actual y pendientes](#10-estado-actual-y-pendientes)

---

## 1. Qué es Sentinel Alpha

Sentinel Alpha es un **detector de insider trading en Polymarket**. Identifica wallets con probable información privilegiada analizando sus transacciones on-chain en Polygon: edad de wallet, origen de fondos, patrones de acumulación y coordinación entre wallets.

**Stack:** Python 3.11+, Supabase (PostgreSQL), Alchemy RPC (Polygon), Polymarket CLOB/Gamma/Data APIs, Telegram, Twitter/X, GitHub Actions.

**Arquitectura:** Quick scans automáticos cada 3h via GitHub Actions (lookback 35min, top 100 mercados) + deep scans manuales locales (lookback 24h, sin límite de mercados). Todas las alertas se almacenan en Supabase para tracking y validación de precisión histórica.

---

## 2. Evolución del proyecto

### Línea de tiempo

| Fecha | Hito |
|-------|------|
| **10 Feb 2026** | MVP funcional — ~15 filtros básicos, scans manuales de 35+ minutos, sin deduplicación entre scans consecutivos. Primera alerta real: "US strikes Iran". |
| **13 Feb 2026** | Primer éxito confirmado: cluster de government shutdown. Múltiples alertas 5★ detectaron smart money apostando NO en wallets coordinadas con fondeo compartido. Todas acertaron. |
| **1ª semana** | Descubierto y corregido triple-counting bug (B14+B18d+B19b inflaba artificialmente el score). Refactor a grupos mutuamente excluyentes en el pipeline de scoring. |
| **Feb 2026** | Cross-scan dedup (Jaccard), consolidación 4★+, dual-mode quick/deep, 12 campos ML snapshot inmutables (T0). El sistema pasa de MVP a producción en ~3 semanas. |
| **Mar 2026** | 55+ filtros activos, 910 tests automatizados, pipeline ML completo. Backfill de 2,447 alertas históricas. Auditoría y corrección de integridad de datos: sell totals, sell_timestamp real, realized_return, DCA tracking, odds_at_resolution_raw, alert_score_history. |

### De MVP a producción en 3 semanas

El sistema arrancó el 10 de febrero con una arquitectura básica: 15 filtros sin categorías, scoring plano sin multiplicadores de monto ni diversidad, scans manuales de 35+ minutos, y sin sistema de deduplicación entre scans consecutivos. La primera semana reveló el triple-counting bug — tres filtros del grupo B disparaban simultáneamente y sus puntos se sumaban en lugar de excluirse mutuamente, inflando scores artificialmente. Corregirlo requirió refactorizar el sistema de grupos mutuamente excluyentes.

En las semanas siguientes se implementaron cross-scan dedup (Jaccard sobre wallet addresses + ventana 24h), consolidación de alertas 4★+ (fusión en lugar de duplicación), el modo dual quick/deep, y los 12 campos T0 inmutables para el training set de ML. Para marzo el sistema tiene 55+ filtros en 6 categorías, 910 tests automatizados, y un historial validado de 2,447 resoluciones.

---

## 3. Cómo funciona — flujo del scan

```
┌─────────────────────────────────────────────────────┐
│  python -m src.main  [--mode quick|deep]            │
└────────────────────┬────────────────────────────────┘
                     │
           ┌─────────▼──────────┐
           │  Kill switch check  │  system_config: scan_enabled
           └─────────┬──────────┘
                     │
           ┌─────────▼──────────┐
           │  Fetch markets      │  Gamma API → filtrar por categoría,
           │  (PolymarketClient) │  volumen, odds (0.05–0.55/0.85),
           └─────────┬──────────┘  blacklist de términos
                     │  (100 mercados quick / todos en deep)
           ┌─────────▼──────────┐
           │  Por cada mercado  │  ←── asyncio paralelo en deep (5 concurrent)
           │  fetch trades CLOB │      secuencial con timeout 8min en quick
           └─────────┬──────────┘
                     │
           ┌─────────▼──────────────────────────────────────┐
           │  Por cada wallet (top 10 por volumen)           │
           │                                                  │
           │  Fase 1 — Filtros básicos (sin Alchemy)         │
           │    W: wallet age, market count, first_tx_pm     │
           │    O: exchange origin (lookup DB cache)         │
           │    B: behavior patterns (timing, sizing, etc.)  │
           │    M: market anomalies (vol, odds, deadline)    │
           │    N: negative filters (bot, news, arb, degen)  │
           │    C: cross-wallet confluence (direction, BFS)  │
           │                                                  │
           │  Score básico ≥ 30 → Fase 2 (Alchemy)          │
           │    O01-O03: on-chain funding sources (BFS 2-3h) │
           │    C03-C07: funding confluence entre wallets    │
           │    N12: merge detection (CLOB arb)              │
           └─────────┬──────────────────────────────────────┘
                     │
           ┌─────────▼──────────┐
           │  calculate_score()  │  raw_score × amount_mult × diversity_mult
           │  star assignment    │  → 1★(40) 2★(70) 3★(100) 4★(150) 5★(220)
           └─────────┬──────────┘
                     │
           ┌─────────▼──────────┐
           │  Cross-scan dedup   │  Jaccard sobre wallets + 24h window
           │  + consolidation    │  4★+ se consolidan en vez de duplicar
           └─────────┬──────────┘
                     │
           ┌─────────▼──────────┐
           │  Publish & store    │  0★→DB, 1★→web, 2★→Telegram, 3-5★→X+TG
           └─────────────────────┘
```

### Las 6 familias de filtros

| Familia | Código | Activos | Propósito |
|---------|--------|---------|-----------|
| Wallet | W01–W11 | 7 | Edad, n° mercados, primera tx Polymarket, balance redondo |
| Origin | O01–O03 | 3 | Fondeo desde exchange/bridge/mixer on-chain |
| Behavior | B01–B30 | ~20 activos* | Acumulación, timing, sizing, convicción de odds |
| Confluence | C01–C07, COORD04 | 9 | Múltiples wallets coordinadas, mismo origen funding |
| Market | M01–M05c | 8 | Volumen anómalo, odds rotas, concentración, deadline |
| Negative | N01–N12 | 17 | Bot, noticias, arbitraje, degen, scalper, merge CTF |

*B27 (diamond hands) y B30 (first mover) están desactivados — ver §10.

### Sistema de scoring

```
score_raw  = Σ(puntos de filtros disparados)   # grupos mutuamente excluyentes
           × amount_multiplier                  # 0.5×–1.5× según monto total
           × diversity_multiplier               # 0.5×–1.2× sniper/shotgun

star_level asignado por score final:
  ≥220 → 5★  |  ≥150 → 4★  |  ≥100 → 3★  |  ≥70 → 2★  |  ≥40 → 1★  |  <40 → 0★

Star validation (puede bajar el nivel):
  3★: requiere ≥2 categorías de filtro
  4★: ≥2 categorías + monto ≥ $5,000
  5★: ≥3 categorías + monto ≥ $10,000 + al menos un filtro de coordinación
```

### Los 6 perfiles de insider (P1–P6, legacy)

| ID | Nombre | Multiplicador | Señal clave |
|----|--------|---------------|-------------|
| P1 | Insider clásico | ×1.3 | W01+W09+O03 + confluencia |
| P2 | Coordinación | ×1.2 | C02+M01 sin noticias |
| P3 | Urgencia | ×1.15 | 2+ de {B16, B05, B17} |
| P4 | Fragmentación silenciosa | ×1.25 | B18c/d + B18e + W04 |
| P5 | Acumulación silenciosa | ×1.30 | B18d + B18e + M02 |
| P6 | Red de distribución | ×1.40 | C07 + wallet nueva |

> **Nota:** P1–P6 son legacy. El sistema activo usa el scoring por categorías + validación de estrellas.

### Cross-scan dedup y resolución

- **Dedup intra-scan:** Jaccard (≥0.5) sobre conjunto de wallet addresses en la misma ventana de 24h. Previene alertar dos veces el mismo grupo.
- **Consolidación:** Alertas 4★+ pendientes del mismo mercado/dirección se fusionan: se suman wallets y monto.
- **Historial de score:** Cada upgrade de `score` o `star_level` (vía cross-scan dedup o consolidación) inserta una fila en `alert_score_history` con los valores anteriores/nuevos, el motivo del cambio (`cross_scan_dedup` / `consolidation`) y timestamp. Los campos `*_initial` siguen siendo el sello T0 inmutable.
- **Resolución:** `run_resolver.py` comprueba la API CLOB diariamente. Si el mercado resolvió YES/NO, actualiza `outcome=correct/incorrect` en todas las alertas pendientes.

---

## 4. Estructura del proyecto

```
SA/
├── src/
│   ├── main.py                 # Punto de entrada principal (quick + deep scan)
│   ├── config.py               # TODOS los umbrales, filtros y constantes
│   ├── analysis/
│   │   ├── wallet_analyzer.py  # Filtros W, O (+ cache DB wallet_funding OPT-1)
│   │   ├── behavior_analyzer.py # Filtros B (comportamiento, sizing, timing)
│   │   ├── confluence_detector.py # Filtros C (coordinación multi-wallet)
│   │   ├── market_analyzer.py  # Filtros M (anomalías de mercado)
│   │   ├── noise_filter.py     # Filtros N negativos (bot, degen, scalper, N09, N10)
│   │   ├── arbitrage_filter.py # N03, N12 merge detection + Jaccard dedup
│   │   ├── scoring.py          # calculate_score() — pipeline completo
│   │   ├── reversion_checker.py # B21 post-resolution price reversion
│   │   ├── sell_detector.py    # Detección de salidas (sell watch)
│   │   └── wallet_tracker.py   # Seguimiento de posiciones abiertas
│   ├── scanner/
│   │   ├── polymarket_client.py # Gamma/CLOB/Data API — mercados y trades
│   │   ├── blockchain_client.py # Alchemy RPC — wallet age, funding, balance
│   │   └── news_checker.py     # RSS feed check para filtro N02
│   ├── database/
│   │   ├── models.py           # Dataclasses (Alert, Wallet, Market, etc.)
│   │   └── supabase_client.py  # SupabaseClient — todo el CRUD contra Supabase
│   ├── publishing/
│   │   ├── formatter.py        # AlertFormatter — textos para Telegram/X
│   │   ├── telegram_bot.py     # TelegramBot — envío multicanal
│   │   └── twitter_bot.py      # TwitterBot — Tweepy v2
│   ├── tracking/
│   │   ├── resolver.py         # MarketResolver — resuelve alertas pendientes
│   │   ├── run_resolver.py     # Entry point para resolver.yml
│   │   ├── alert_tracker.py    # AlertTracker — price tracking de alertas activas
│   │   ├── run_tracker.py      # Entry point para tracker.yml
│   │   ├── alert_notifier.py   # Notificaciones de price move post-alert
│   │   └── whale_monitor.py    # Monitor de alertas 4-5★ activas
│   ├── reports/
│   │   ├── weekly.py           # Reporte semanal (Lunes 08:00 UTC)
│   │   └── monthly.py          # Reporte mensual (día 1, 08:00 UTC)
│   ├── dashboard/
│   │   └── generate_dashboard.py # Genera docs/index.html (HTML estático)
│   └── scripts/
│       └── check_resolutions.py  # Chequeo diario de resoluciones (00:00 UTC)
├── migrations/                 # Scripts one-shot de esquema y mantenimiento DB
├── vacunas/                    # Scripts de corrección/backfill (aplicadas/ = ejecutadas)
├── tests/                      # pytest — 33 archivos, 910 tests
├── docs/
│   └── index.html              # Dashboard HTML generado (no editar a mano)
├── .github/workflows/          # 8 GitHub Actions workflows
├── requirements.txt
├── .env                        # Variables de entorno (NO versionar)
└── README.md
```

---

## 5. Configuración y variables de comportamiento

### Variables de entorno (`.env` + GitHub Secrets)

| Variable | Requerida | Uso |
|----------|-----------|-----|
| `SUPABASE_URL` | ✅ | URL del proyecto Supabase |
| `SUPABASE_KEY` | ✅ | Service role key (acceso total) |
| `SUPABASE_ANON_KEY` | — | Anon key (actualmente sin uso activo) |
| `ALCHEMY_API_KEY` | ✅ | API key para Polygon RPC (Alchemy) |
| `ALCHEMY_ENDPOINT` | — | URL completa RPC (se construye auto desde API_KEY) |
| `TELEGRAM_BOT_TOKEN` | ✅ | Token del bot de Telegram |
| `TELEGRAM_CHANNEL_ID` | ✅ | Canal principal de alertas (en GH Actions: `TELEGRAM_CHAT_ID`) |
| `TELEGRAM_PRIVATE_CHANNEL_ID` | — | Canal privado adicional |
| `TELEGRAM_PUBLIC_CHANNEL_ID` | — | Canal público (si aplica) |
| `TELEGRAM_VIP_CHANNEL_ID` | — | Canal VIP (si aplica) |
| `TWITTER_API_KEY` | — | Twitter/X OAuth 1.0a key |
| `TWITTER_API_SECRET` | — | Twitter/X OAuth 1.0a secret |
| `TWITTER_ACCESS_TOKEN` | — | Twitter/X access token |
| `TWITTER_ACCESS_SECRET` | — | Twitter/X access secret |
| `DASHBOARD_ACCESS_KEY` | — | Clave de acceso al dashboard HTML |

> **Nota:** GitHub Actions usa el nombre `TELEGRAM_CHAT_ID` como secret, que se mapea a `TELEGRAM_CHANNEL_ID` en el workflow.

### Variables de comportamiento — tabla completa

| Constante | Archivo | Valor actual | Qué controla | Rango recomendado |
|-----------|---------|-------------|--------------|-------------------|
| `_ALCHEMY_MIN_SPACING` | `blockchain_client.py:65` | `0.100` s | Mínimo entre llamadas consecutivas a Alchemy | 0.05–0.20 |
| `_ALCHEMY_MAX_CONCURRENT` | `blockchain_client.py:61` | `5` | Máximo requests simultáneos a Alchemy | 3–8 |
| `_DEEP_SEMAPHORE_SIZE` | `main.py:440` | `5` | Mercados procesados en paralelo en modo deep | 3–10 |
| `_WALLET_CACHE_TTL_DAYS` | `wallet_analyzer.py:26` | `7` días | Validez del cache de wallet_age en DB | 3–14 |
| `_FUNDING_CACHE_TTL_DAYS` | `wallet_analyzer.py:30` | `14` días | TTL del cache wallet_funding en DB (OPT-1) | 7–30 |
| `_MIN_BASIC_SCORE_FOR_FUNDING` | `wallet_analyzer.py:97` | `30` | Score mínimo para activar Fase 2 (Alchemy) | 20–50 |
| `MAX_WALLETS_PER_MARKET` | `config.py:55` | `10` | Top N wallets por mercado analizados | 5–20 |
| `SCAN_LOOKBACK_MINUTES` | `config.py:52` | `35` | Ventana quick scan (ligeramente > 30min para no dejar gaps) | 35–45 |
| `MARKET_MIN_VOLUME_24H` | `config.py:56` | `$1,000` | Volumen mínimo para quick scan | 500–5000 |
| `MARKET_SCAN_CAP` | `config.py:57` | `100` | Máximo mercados en quick scan (límite timeout GH Actions) | 80–150 |
| `SCAN_TIMEOUT_SECONDS` | `config.py:53` | `480` (8 min) | Timeout global quick scan | 420–540 |
| `MARKET_TIMEOUT_SECONDS` | `config.py:54` | `45` | Timeout por mercado | 30–60 |
| `ODDS_MIN` | `config.py:64` | `0.05` | Precio mínimo para alertas | 0.03–0.10 |
| `ODDS_MAX` | `config.py:65` | `0.55` | Precio máximo quick scan | 0.50–0.65 |
| `ODDS_MAX_EXTENDED` | `config.py:66` | `0.70` | Precio máximo si score ≥ 90 | 0.65–0.80 |
| `ENABLE_B27` | `config.py:339` | `False` | Diamond hands filter — **desactivado** | — |
| `ENABLE_B30` | `config.py:352` | `False` | First mover filter — **desactivado** | — |
| `ALLIN_MIN_AMOUNT` | `config.py:349` | `$3,500` | Monto mínimo para disparar B28 (all-in) | 2000–5000 |
| `PUBLISH_SCORE_THRESHOLD_X` | `config.py:73` | `70` | Score mínimo para publicar en X | 60–90 |
| `PUBLISH_SCORE_THRESHOLD_TELEGRAM` | `config.py:75` | `50` | Score mínimo para Telegram | 40–70 |
| `MAX_TWEETS_PER_DAY` | `config.py:77` | `10` | Límite diario de tweets | 5–15 |
| `CROSS_SCAN_DEDUP_HOURS` | `config.py:58` | `24` | Ventana de deduplicación cross-scan | 12–48 |
| `SENDER_AUTO_EXCLUDE_MIN_WALLETS` | `config.py:491` | `100` | Auto-excluir senders de infraestructura que fondean >N wallets | 50–200 |

### Umbrales de estrellas (sistema activo)

```python
# src/config.py — NEW_STAR_THRESHOLDS
(220, 5★)  (150, 4★)  (100, 3★)  (70, 2★)  (40, 1★)
```

### Multiplicadores de monto (sobre score_raw)

| Monto total | Multiplicador |
|-------------|---------------|
| ≥ $50,000 | ×1.5 |
| ≥ $20,000 | ×1.3 |
| ≥ $10,000 | ×1.2 |
| ≥ $5,000 | ×1.1 |
| ≥ $1,000 | ×1.0 |
| ≥ $500 | ×0.8 |
| < $500 | ×0.5 |

### Multiplicadores de diversidad de wallet

| Condición | Multiplicador |
|-----------|---------------|
| ≤3 mercados distintos (sniper) | ×1.2 |
| 4–10 mercados | ×1.0 |
| 11–20 mercados (shotgun) | ×0.7 |
| >20 mercados (super shotgun) | ×0.5 |

### Canales de publicación por estrella

| Nivel | DB | Dashboard web | Telegram | Twitter/X |
|-------|----|---------------|----------|-----------|
| 0★ | ✅ | ❌ | ❌ | ❌ |
| 1★ | ✅ | ✅ | ❌ | ❌ |
| 2★ | ✅ | ✅ | ✅ | ❌ |
| 3★–5★ | ✅ | ✅ | ✅ | ✅ |

---

## 6. Comandos

```bash
# Activar entorno virtual
source venv/bin/activate

# ── Scans ──────────────────────────────────────────────────────────────
# Quick scan (modo por defecto — mismo que GitHub Actions)
python -m src.main

# Deep scan (24h lookback, sin límite de mercados, asyncio paralelo)
python -m src.main --mode deep

# ── Dashboard ──────────────────────────────────────────────────────────
# Generar docs/index.html localmente
python -m src.dashboard.generate_dashboard

# ── Tracking y resolución ──────────────────────────────────────────────
# Resolver alertas pendientes manualmente
python -m src.tracking.run_resolver

# Ejecutar tracker + notifier + whale monitor manualmente
python -m src.tracking.run_tracker

# Check diario de resoluciones (actualiza outcomes en DB)
python -m src.scripts.check_resolutions

# ── Tests ──────────────────────────────────────────────────────────────
pytest                         # todos los tests
pytest tests/test_scoring.py   # test específico
pytest -x -v                   # stop on first failure, verbose

# ── Migrations (one-shot, ejecutar una sola vez) ──────────────────────
python -m migrations.add_wallet_funding_indexes
python -m migrations.add_ml_snapshot_fields
python -m migrations.add_star_level_initial
python -m migrations.add_hold_duration
python -m migrations.add_close_reason
python -m migrations.add_merge_columns
python -m migrations.reconcile_sell_fields
python -m migrations.purge_wallet_funding_duplicates
python -m migrations.add_realized_return
python -m migrations.add_additional_buy_fields
python -m migrations.add_odds_at_resolution_raw
python -m migrations.add_alert_score_history
python -m migrations.add_dca_price_fields

# ── Scripts de diagnóstico ─────────────────────────────────────────────
python diag_filter_score_mismatch.py     # detecta alertas con score inconsistente
python validate_post_scan.py             # valida integridad post-scan
python scripts/diag_price_resolutions.py # diagnóstico de resoluciones
python scripts/audit_false_confluence.py # audita falsas confluencias
python health_check.py                   # check general de salud del sistema
```

---

## 7. Base de datos

Supabase PostgreSQL. Todas las tablas tienen RLS deshabilitado (acceso via service key).

| Tabla | Propósito principal |
|-------|---------------------|
| `alerts` | Registro central de alertas (score, filtros, wallets, outcome, ML snapshot) |
| `wallets` | Historial de wallets detectadas (age, win_rate, stats) |
| `markets` | Mercados conocidos con metadatos y resolución |
| `market_snapshots` | Historial de odds por mercado (puntos en el tiempo) |
| `wallet_funding` | Fuentes de fondeo on-chain (cache OPT-1, TTL 14d) |
| `wallet_positions` | Posiciones abiertas por wallet+mercado |
| `wallet_categories` | Categorización de wallets (insider/degen/unknown) |
| `alert_tracking` | Seguimiento post-alerta de price moves |
| `alert_sell_events` | Eventos de salida detectados (sell watch) |
| `alert_score_history` | Changelog de cambios de score/star_level (old/new values, change_reason, timestamp) |
| `scans` | Log de cada ejecución de scan (métricas, errores) |
| `weekly_reports` | Reportes semanales generados |
| `smart_money_leaderboard` | Ranking de wallets por PnL estimado |
| `system_config` | Kill switches y config en tiempo real (scan_enabled, publish_x, etc.) |
| `notification_log` | Log de notificaciones enviadas por alerta |
| `whale_notifications` | Eventos de whale monitor (4-5★) |
| `bot_trades` | Registro de operaciones ejecutadas por el bot de trading |

### Campos ML snapshot en `alerts` (T0 — inmutables post-insert)

Los 12 campos `*_initial` capturan el estado exacto en el momento de la primera detección. Nunca se modifican por dedup, consolidación ni ninguna operación posterior:

`scan_mode`, `score_initial`, `score_raw_initial`, `odds_at_alert_initial`, `total_amount_initial`, `filters_triggered_initial`, `market_category`, `market_volume_24h_at_alert`, `market_liquidity_at_alert`, `hours_to_deadline`, `wallets_count_initial`, `star_level_initial`

### Campos de tracking post-detección en `alerts`

Campos que se actualizan durante la vida de la alerta (no inmutables):

| Campo | Tipo | Cuándo se escribe |
|-------|------|-------------------|
| `actual_return` | FLOAT | Al resolver: retorno binario del mercado — `((1−odds)/odds)×100` para correctas, `−100` para incorrectas |
| `realized_return` | FLOAT | Al resolver: PnL real del whale — ventas CLOB ponderadas por `sell_pct × pnl_pct` + porción no vendida × `actual_return` |
| `odds_at_resolution_raw` | FLOAT | Al resolver: precio YES real del último `market_snapshot` antes de resolución (no binario 0/1) |
| `odds_max` / `odds_min` | FLOAT | Actualizado en cada ciclo de tracker; congelado con el último snapshot al resolver |
| `additional_buys_count` | INT | Whale monitor: número de compras adicionales (DCA) detectadas post-alerta |
| `additional_buys_amount` | FLOAT | Whale monitor: monto total acumulado de DCAs en USD |
| `avg_additional_buy_price` | FLOAT | Whale monitor: precio promedio ponderado por amount de todos los DCAs |
| `last_additional_buy_price` | FLOAT | Whale monitor: precio de la compra adicional más reciente |
| `total_sold_pct` | FLOAT | Sell detector: porcentaje neto de la posición que el whale ha vendido |
| `close_reason` | TEXT | Sell detector / whale monitor: motivo de cierre (`sell_clob`, `net_zero`, `merge_suspected`, etc.) |

### Índices relevantes

```sql
-- wallet_funding (creados por migration)
idx_wallet_funding_wallet_address
idx_wallet_funding_sender_address
idx_wallet_funding_created_at

-- alerts (búsquedas frecuentes por outcome y market)
idx_alerts_outcome
idx_alerts_market_id

-- alert_score_history
idx_score_history_alert
idx_score_history_ts
```

---

## 8. CI/CD — GitHub Actions

8 workflows en `.github/workflows/`:

| Workflow | Trigger | Timeout | Qué hace |
|----------|---------|---------|----------|
| `scan.yml` | Cron `0 */3 * * *` (cada 3h) | 10 min | Quick scan principal → genera alertas → publica Telegram/X |
| `dashboard.yml` | Cron `15 * * * *` (cada hora, offset +15min) | 5 min | Genera `docs/index.html` y hace commit al repo |
| `publish_dashboard.yml` | Tras `dashboard.yml` exitoso | 3 min | Copia `index.html` al repo público `sentinel-dashboard` (GitHub Pages) |
| `resolver.yml` | Cron `0 8 * * *` (diario 08:00 UTC) | 10 min | Resuelve mercados cerrados, actualiza outcomes en alertas |
| `tracker.yml` | Cron `0 1,7,13,19 * * *` (4x/día) | 10 min | Tracker de precio + notifier + whale monitor para 4-5★ |
| `check_resolutions.yml` | Cron `0 0 * * *` (diario 00:00 UTC) | — | Check adicional de resoluciones + B21 reversion scoring |
| `weekly_report.yml` | Cron `0 8 * * 1` (lunes 08:00 UTC) | 10 min | Genera y publica reporte semanal vía Telegram/X |
| `monthly_report.yml` | Cron `0 8 1 * *` (día 1 08:00 UTC) | 10 min | Genera y publica reporte mensual |

Todos tienen `workflow_dispatch` para ejecución manual.

**Dashboard:** `generate_dashboard.py` → commit a `docs/index.html` → `publish_dashboard.yml` → push a repo público `sentinelalphax0a-tech/sentinel-dashboard` → GitHub Pages.

---

## 9. Costes

| Servicio | Coste | Notas |
|----------|-------|-------|
| Alchemy | ~$30/mes | Quick scans en GH Actions consumen ~1–2M CUs/mes. Deep scans locales: ~50k–100k CUs cada uno. Cargo por volumen de API. |
| Supabase | Gratis (free tier) | 500 MB incluidos. Con 6,800+ alertas + wallet_funding, actualmente <100 MB. Margen amplio. |
| GitHub Actions | Gratis | 2,000 min/mes en free tier. Quick scan: ~3 min × 8/día × 30 días ≈ 720 min/mes. |
| Telegram Bot | Gratis | Sin límite relevante para uso personal |
| Twitter/X API | Gratis (free tier) | Límite: 1,500 tweets/mes. Con cap de 10/día → <310/mes |
| Polymarket APIs | Gratis | APIs públicas sin autenticación |

**Migración planificada:** Despliegue en mini PC local con PostgreSQL propio eliminará la dependencia de Supabase, reducirá los costes de Alchemy (scans locales sin pasar por GH Actions) y permitirá scans cada 10 minutos.

---

## 10. Estado actual y pendientes

### Métricas actuales (mar 2026)

- **Alertas totales generadas:** 6,800+
- **Alertas resueltas:** 2,447
- **Accuracy global (3+★):** 66.7%
- **Win rate zona óptima:** 80.7% (alertas con precio efectivo ≥ 0.70)
- **EV por operación:** +6.8% del stake (edge real confirmado, Z=+4.7σ)
- **Edge real concentrado en:** precio efectivo 0.70–0.90 (NO tokens con ROI +11–20%)
- **Rango sin edge:** precio efectivo 0.60–0.70 (EV −8.6%) — candidato a filtrar

### Casos de éxito documentados

| Evento | Señal detectada | Resultado |
|--------|----------------|-----------|
| **Government shutdown (13 Feb)** | Múltiples alertas 5★: wallets coordinadas apostando NO con fondeo compartido desde el mismo intermediario, entradas sincronizadas. | ✅ Correcto — smart money tenía información. |
| **Supreme Court — tariffs** | Wallet de 1,441 días de antigüedad (4 años sin actividad en Polymarket) que deposita $41,569 de golpe apostando NO. Patrón clásico de wallet dormida activada por información privilegiada. | ✅ Correcto |
| **Iran strikes (10 Feb)** | Wallets nuevas con compras coordinadas. El filtro N02 (news checker) restó puntos al detectar cobertura pública previa — reduciendo el score a alerta menor en lugar de 5★. | Caso de estudio: validó que el sistema penaliza correctamente cuando hay noticias públicas simultáneas. |

### Roadmap

| Período | Objetivo |
|---------|----------|
| **Marzo 2026** | Data collection y auditorías semanales de calidad de señal. Preparación del training set para ML. Migración planificada a mini PC local. |
| **Abril 2026** | Bot de trading en shadow mode (simula operaciones sin ejecutar real). Validación de estrategia de sizing y gestión de riesgo. Transición a live con capital semilla (~$1,000). |
| **Mayo–Julio 2026** | ML training con 3–4 meses de datos limpios. Dos modelos para Polymarket: (1) meta-labeling para filtrar false positives, (2) predictor de calidad de señal. Un modelo para Forex que correlacione eventos de prediction markets con movimientos FX en divisas relacionadas. |
| **Julio 2026+** | Integración del bot con capa ML: señales heurísticas + score ML → decisión de ejecución. Dashboard con ventanas de ML, análisis de feature importance, hosting local via Cloudflare tunnel. |
| **2027** | Escalado: licenciamiento a fondos cuantitativos o modelo de suscripción para traders externos. |

### Filtros activos

- **W (Wallet):** 7 activos (W01, W02, W03, W04, W05, W09, W11)
- **O (Origin):** 3 activos (O01, O02, O03)
- **B (Behavior):** ~20 activos — B27 y B30 desactivados (ver abajo)
- **C (Confluence):** 9 activos (C01, C02, C03a–d, C05, C06, C07, COORD04)
- **M (Market):** 8 activos (M01, M02, M03, M04a/b, M05a/b/c)
- **N (Negative):** 17 activos (N01–N12, con sub-tiers)

### Filtros desactivados

| Filtro | Razón |
|--------|-------|
| `B27` (diamond hands) | `sell_detector` solo cubre sells post-alerta, no pre-alert. Activar causaría falsos positivos al no tener historial completo de posiciones. |
| `B30` (first mover) | Requiere tabla de historial de trades en Supabase que no existe. |

### Implementaciones recientes

| Feature | Estado |
|---------|--------|
| **OPT-1 — Funding DB cache** | ✅ `wallet_funding` rows se cachean 14 días. Reduce llamadas Alchemy ~80% en wallets recurrentes. |
| **ML snapshot fields** | ✅ 12 campos `*_initial` congelados en T0. Base para training set futuro. Backfill ejecutado sobre historial completo. |
| **Multi-signal grouping** | ✅ `multi_signal` / `is_secondary` / `alert_group_id` permiten agrupar señales del mismo mercado-scan. |
| **Merge detection (N12)** | ✅ Detección de CLOB arbitrage en tokens/shares (no dólares). |
| **Sell Watch** | ✅ Monitoreo de salidas en alertas 3★+, notificación Telegram en 4-5★. `sell_timestamp` escribe el timestamp real del trade CLOB. |
| **Integridad sell totals** | ✅ `_reconcile_sell_totals` solo sube `total_sold_pct`, nunca degrada. Umbral de full exit alineado al 90% consistente con whale_monitor. |
| **realized_return** | ✅ PnL real del whale: ventas CLOB ponderadas (`sell_pct × pnl_pct`) + porción no vendida × `actual_return`. Backfill ejecutado (2,447 alertas). |
| **DCA tracking** | ✅ `additional_buys_count`, `additional_buys_amount`, `avg_additional_buy_price`, `last_additional_buy_price` — precio promedio ponderado de compras adicionales post-alerta. |
| **odds_at_resolution_raw** | ✅ Precio YES real del mercado al resolver (no binario). Capturado del último `market_snapshot` disponible. |
| **odds_max/min freeze** | ✅ Al resolver, `odds_max`/`odds_min` se actualizan con el último snapshot antes de que el mercado muestre precios binarios. |
| **alert_score_history** | ✅ Changelog completo de cambios de score/star_level: old/new values, `change_reason`, timestamp. Alimentado desde cross-scan dedup y consolidación. |
| **B30 (first mover)** | ⏳ Pendiente tabla `trades_history` en Supabase. |
| **B27 (diamond hands)** | ⏳ Pendiente cobertura pre-alert en sell_detector. |
| **Filtro precio 0.60–0.70** | ⏳ Candidato a agregar N13 (descuento por precio medio-alto sin convicción clara). |
| **ML model** | ⏳ Training set completo (2,447 alertas resueltas + campos T0). Siguiente paso: feature engineering + primer modelo de clasificación. |
