# Sentinel Alpha

> **CONFIDENCIAL — USO PERSONAL**
> Sistema privado de investigación. No compartir ni distribuir.
> Última actualización: febrero 2026

---

## Índice

1. [Propósito y contexto](#1-propósito-y-contexto)
2. [Arquitectura dual-mode](#2-arquitectura-dual-mode)
3. [Stack técnico](#3-stack-técnico)
4. [Estructura de archivos](#4-estructura-de-archivos)
5. [Flujo de scan paso a paso](#5-flujo-de-scan-paso-a-paso)
6. [Sistema de filtros](#6-sistema-de-filtros)
   - 6.1 [W — Wallet (7 filtros)](#61-w--wallet-7-filtros)
   - 6.2 [O — Origen (3 filtros + COORD04)](#62-o--origen-3-filtros--coord04)
   - 6.3 [B — Comportamiento (32 filtros definidos)](#63-b--comportamiento-32-filtros-definidos)
   - 6.4 [C — Confluencia (9 filtros, 4 capas)](#64-c--confluencia-9-filtros-4-capas)
   - 6.5 [M — Mercado (8 filtros)](#65-m--mercado-8-filtros)
   - 6.6 [N — Negativos (16 filtros)](#66-n--negativos-16-filtros)
   - 6.7 [Grupos mutuamente excluyentes](#67-grupos-mutuamente-excluyentes)
7. [Sistema de scoring](#7-sistema-de-scoring)
   - 7.1 [Pipeline de cálculo](#71-pipeline-de-cálculo)
   - 7.2 [Multiplicador por monto (logarítmico)](#72-multiplicador-por-monto-logarítmico)
   - 7.3 [Multiplicador por diversidad (sniper/shotgun)](#73-multiplicador-por-diversidad-snipershotgun)
   - 7.4 [Umbrales de estrellas y validación](#74-umbrales-de-estrellas-y-validación)
   - 7.5 [Multiplicadores heredados P1-P6 (legacy)](#75-multiplicadores-heredados-p1-p6-legacy)
8. [6 Perfiles de insider](#8-6-perfiles-de-insider)
9. [Sistema de alertas](#9-sistema-de-alertas)
   - 9.1 [Agrupación y multi-signal](#91-agrupación-y-multi-signal)
   - 9.2 [Deduplicación](#92-deduplicación)
   - 9.3 [Publicación por canal](#93-publicación-por-canal)
   - 9.4 [Ciclo de vida de una alerta](#94-ciclo-de-vida-de-una-alerta)
10. [Sell Watch — monitoreo de salidas](#10-sell-watch--monitoreo-de-salidas)
11. [Exclusión de infraestructura](#11-exclusión-de-infraestructura)
12. [GitHub Actions — 8 workflows](#12-github-actions--8-workflows)
13. [Base de datos Supabase](#13-base-de-datos-supabase)
14. [Scripts de utilidad](#14-scripts-de-utilidad)
15. [Bugs críticos resueltos](#15-bugs-críticos-resueltos)
16. [Decisiones técnicas y lecciones aprendidas](#16-decisiones-técnicas-y-lecciones-aprendidas)
17. [Tests](#17-tests)
18. [Roadmap](#18-roadmap)
19. [Resumen del sistema](#19-resumen-del-sistema)

---

## 1. Propósito y contexto

Sentinel Alpha es un sistema de **detección de smart money en Polymarket**. Su objetivo es identificar wallets que probablemente tienen información privilegiada sobre el desenlace de mercados de predicción políticos, geopolíticos y económicos.

**Qué hace:**
- Monitorea transacciones en Polymarket cada 3 horas (modo sentinel automatizado)
- Analiza el comportamiento de wallets: edad, origen de fondos, patrones de acumulación, coordinación entre wallets
- Genera alertas con un score de convicción (0–400 puntos) y nivel de estrellas (0★–5★)
- Envía alertas por Telegram a un canal/grupo privado personal
- Almacena todas las alertas en Supabase para análisis histórico y validación de precisión

**Qué NO es:**
- No es un servicio comercial ni tiene tiers de pago
- No tiene canales públicos de pago
- Es una herramienta de investigación personal y ventaja informacional propia

**Hipótesis central:** Las wallets con información privilegiada exhiben patrones detectables — son nuevas en Polymarket, vienen fondeadas recientemente de exchanges conocidos, apuestan a odds desfavorables con alta convicción, y a veces coordinan entre ellas con funding del mismo origen.

---

## 2. Arquitectura dual-mode

El sistema opera en dos modos completamente distintos:

### Modo "sentinel" (quick)
- **Ejecución:** GitHub Actions, cron cada 3 horas, sin coste
- **Lookback:** 35 minutos (ligeramente superior al intervalo para no dejar gaps)
- **Mercados:** Top 100 por volumen, categorías politics/economics/geopolitics
- **Mínimo de volumen:** $1,000 en 24h
- **Odds:** 0.05–0.55 (extendido a 0.70 si score ≥ 90)
- **Procesamiento:** Secuencial con timeout global de 8 minutos
- **Constraint principal:** Timeout de GitHub Actions — no se pueden analizar más de ~100 mercados

### Modo "deep scan" (deep)
- **Ejecución:** Local, sin límites de tiempo
- **Lookback:** 24 horas completas
- **Mercados:** Sin cap (450+ mercados en ~4 minutos)
- **Mínimo de volumen:** $200 en 24h
- **Odds:** 0.05–0.85 (rango ampliado)
- **Categorías adicionales:** Science & Tech
- **Procesamiento:** asyncio paralelo, 5 mercados concurrentes, pausa 1s entre batches
- **Uso:** Después de períodos de inactividad o cuando se sospecha actividad relevante reciente

```python
# Perfiles en config.py
SCAN_PROFILES = {
    "quick": {
        "min_volume": 1000,
        "odds_max": 0.55,
        "max_markets": 100,
        "lookback_minutes": 35,
    },
    "deep": {
        "min_volume": 200,
        "odds_max": 0.85,
        "max_markets": None,      # sin cap
        "lookback_minutes": 1440, # 24h
    },
}
```

**Por qué dual-mode y no solo GitHub Actions:** El CI tiene un timeout máximo de 10 minutos por job. Analizar 450+ mercados con 24h de lookback requiere consultas a 3 APIs diferentes (Gamma, CLOB, Alchemy) — imposible en ese tiempo. El modo deep se ejecuta localmente cuando se necesita.

---

## 3. Stack técnico

| Componente | Tecnología | Coste |
|---|---|---|
| Lenguaje | Python 3.11 + asyncio | — |
| Orquestación | GitHub Actions (cron) | $0 |
| Base de datos | Supabase PostgreSQL | $0 (plan free) |
| Blockchain RPC | Alchemy (Polygon) | $0 (plan free) |
| Notificaciones | Telegram Bot API | $0 |
| APIs de mercado | Polymarket Gamma + CLOB + Data API | $0 (sin auth) |
| Dashboard | HTML autocontenido en GitHub Pages | $0 |

**Librerías clave:**
- `supabase` — cliente Supabase
- `web3` — interacción con Polygon RPC
- `aiohttp` — requests async para deep scan
- `feedparser` — noticias Google News RSS
- `tweepy` — Twitter/X API (OAuth 1.0a)
- `tenacity` — retry logic con backoff exponencial

---

## 4. Estructura de archivos

```
SA/
├── .github/workflows/
│   ├── scan.yml                    # Scan principal (cada 3h)
│   ├── tracker.yml                 # Tracking de odds (cada 6h)
│   ├── resolver.yml                # Resolución de mercados (diario 08:00 UTC)
│   ├── check_resolutions.yml       # Validación de resoluciones (diario 00:00 UTC)
│   ├── dashboard.yml               # Generación del dashboard (cada hora :15)
│   ├── publish_dashboard.yml       # Publica index.html al repo público
│   ├── weekly_report.yml           # Reporte semanal (lunes 08:00 UTC)
│   └── monthly_report.yml          # Reporte mensual (día 1 08:00 UTC)
├── docs/
│   └── index.html                  # Dashboard HTML autocontenido (auto-commit)
├── scripts/
│   ├── audit_false_confluence.py   # Audita alertas con confluencia falsa de infra
│   ├── backfill.py                 # Backfill histórico (wallet ages, funding)
│   ├── cleaner_post_deep.py        # Limpieza post-deep-scan (infra, recálculo)
│   ├── setup_db.py                 # Verifica tablas en Supabase
│   ├── test_blockchain.py          # Test manual de Alchemy RPC
│   ├── test_connection.py          # Test de conexión Supabase
│   ├── test_news.py                # Test del news checker
│   ├── test_polymarket.py          # Test de APIs Polymarket
│   └── test_telegram.py            # Test del bot de Telegram
├── src/
│   ├── config.py                   # Constantes, umbrales, definición de filtros (~665 líneas)
│   ├── main.py                     # Orquestador principal (~1380 líneas)
│   ├── analysis/
│   │   ├── arbitrage_filter.py     # Filtro N03/N04 — detección de arbitraje
│   │   ├── behavior_analyzer.py    # Filtros B01–B30, N09–N10 (~840 líneas)
│   │   ├── confluence_detector.py  # Filtros C01–C07, agrupación union-find (~600 líneas)
│   │   ├── market_analyzer.py      # Filtros M01–M05 (~310 líneas)
│   │   ├── noise_filter.py         # Filtros N01–N08 (~270 líneas)
│   │   ├── reversion_checker.py    # Reversion post-resolución
│   │   ├── scoring.py              # Motor de scoring + estrellas (~230 líneas)
│   │   ├── sell_detector.py        # Detección de ventas de posición
│   │   ├── wallet_analyzer.py      # Filtros W01–W11, O01–O03, COORD04 (~250 líneas)
│   │   └── wallet_tracker.py       # Win-rate y especialización de wallets
│   ├── database/
│   │   ├── models.py               # 15 dataclasses (Alert, Wallet, FilterResult…)
│   │   └── supabase_client.py      # 50+ métodos CRUD (~800 líneas)
│   ├── dashboard/
│   │   ├── dashboard_template.html # SPA template (Chart.js)
│   │   └── generate_dashboard.py   # Generación del dashboard
│   ├── publishing/
│   │   ├── chart_generator.py      # Generación de imágenes
│   │   ├── formatter.py            # 12+ templates de mensajes
│   │   ├── telegram_bot.py         # Publicación multicanal Telegram
│   │   └── twitter_bot.py          # Publicación X/Twitter (límite 10/día)
│   ├── reports/
│   │   ├── weekly.py               # TODO: pendiente implementar
│   │   └── monthly.py              # TODO: pendiente implementar
│   ├── scanner/
│   │   ├── blockchain_client.py    # Alchemy Polygon RPC (age, funding, balance)
│   │   ├── news_checker.py         # Google News RSS (N02)
│   │   └── polymarket_client.py    # Gamma + CLOB + Data API (~450 líneas)
│   ├── scripts/
│   │   └── check_resolutions.py    # Script diario de resolución
│   └── tracking/
│       ├── alert_notifier.py       # Notificaciones de resolución
│       ├── alert_tracker.py        # Seguimiento de odds post-alerta
│       ├── resolver.py             # Motor de resolución de mercados
│       ├── run_resolver.py         # Entry point del resolver
│       ├── run_tracker.py          # Entry point del tracker
│       └── whale_monitor.py        # Monitor de ventas y actividad whale
├── tests/                          # 26 archivos de test, 737 tests totales
├── requirements.txt
└── README.md
```

---

## 5. Flujo de scan paso a paso

```
PASO 1 — INICIALIZACIÓN
  ├── Kill switch: system_config.scan_enabled == "true"
  ├── Inicializar todos los servicios (PM client, Alchemy, analizadores, bots)
  └── Leer SCAN_PROFILE: "quick" (GitHub Actions) o "deep" (local)

PASO 2 — OBTENER MERCADOS
  ├── pm_client.get_active_markets(categories)
  ├── Filtros de mercado:
  │   ├── Volumen mínimo (quick: $1K, deep: $200)
  │   ├── Odds range (quick: 0.05–0.55, deep: 0.05–0.85)
  │   ├── Categorías válidas (quick: politics/economics/geopolitics, deep: +science)
  │   ├── Blacklist: 50+ términos (deportes, celebridades, crypto precios, etc.)
  │   └── Cap: quick: 100 mercados, deep: sin límite
  └── Ordenar por volumen DESC

PASO 3-7 — PROCESAR MERCADOS
  ├── [QUICK] Secuencial con timeout global 8 min
  └── [DEEP] asyncio paralelo: 5 concurrentes, pausa 1s entre batches

  Por mercado (_process_market):
  ├── 3. Fetch trades (lookback: 35min quick, 24h deep)
  ├── 4. Agrupar trades por wallet, ordenar por volumen, top N wallets
  ├── 5. Por cada wallet:
  │   ├── Verificar mínimo acumulado ($350)
  │   ├── WalletAnalyzer → filtros W + O + COORD04
  │   ├── BehaviorAnalyzer → filtros B + N09 + N10
  │   ├── NoiseFilter → filtros N01/N02/N05-N08
  │   └── ArbitrageFilter → N03/N04
  ├── 6. Filtrar por dirección dominante
  ├── 7. MarketAnalyzer → filtros M01-M05
  ├── 8. Agrupar wallets por funding compartido (union-find)
  ├── 9. ConfluenceDetector.group_and_detect() → C01-C07 por grupo
  └── 10. calculate_score() por grupo → Alert con score/stars

PASO 7b — DEDUPLICACIÓN WITHIN-SCAN
  └── Similitud Jaccard (umbral 0.60) entre preguntas de mercado
      → Por grupo similar, solo el de mayor score se publica

PASO 8 — GUARDAR + PUBLICAR
  ├── Alertas secundarias → solo DB (is_secondary=True)
  ├── Cross-scan dedup: actualizar alerta existente si mismo mercado+dirección en 24h
  ├── Consolidación: merge en alerta 4+★ existente en últimas 48h
  └── Nueva alerta:
      ├── INSERT en Supabase
      ├── INSERT AlertTracking + WalletPositions
      ├── Telegram (2+★ canal testing, 3+★ canal público)
      └── Twitter (3+★, máximo 10/día)

PASO 8b — SELL MONITORING
  └── SellDetector.check_open_positions() → notificaciones de venta

PASO 9 — LOG
  └── INSERT Scan row con contadores (markets_scanned, alerts_generated, duración)
```

---

## 6. Sistema de filtros

El sistema tiene **76 filtros definidos** (70 activos, 6 deshabilitados o retirados) organizados en 6 categorías. Cada filtro tiene un ID único, nombre, puntos (positivos o negativos) y categoría que determina cómo contribuye al scoring.

**Regla de exclusión mutua:** Dentro de cada grupo, solo sobrevive el filtro con mayor `|points|`. El scoring engine lo garantiza como safety net, aunque los analyzers ya lo aplican individualmente.

---

### 6.1 W — Wallet (7 filtros)

Evalúan la antigüedad, exposición a mercados y características de la wallet.

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| W01 | Wallet muy nueva | +25 | `wallet_age_days < 7` | Mut. excl. con W02, W03 |
| W02 | Wallet nueva | +20 | `7 ≤ wallet_age_days < 14` | Mut. excl. con W01, W03 |
| W03 | Wallet reciente | +15 | `14 ≤ wallet_age_days < 30` | Mut. excl. con W01, W02 |
| W04 | Solo 1 mercado | +10 | Exactamente 1 mercado en ventana de scan. Suprimido si `real_distinct_markets > 3` (Data API) | Mut. excl. con W05 |
| W05 | Solo 2-3 mercados | +15 | 2-3 mercados en ventana. Suprimido si `real_distinct_markets > 5` | Mut. excl. con W04 |
| W09 | Primera tx = Polymarket | +5 | El primer contrato llamado en Polygon fue un contrato de PM. Suprimido si historial real PM > 3 mercados | — |
| W11 | Balance redondo | +3 | Balance USDC ≈ $5K / $10K / $50K (tolerancia ±1%) | — |

**Nota sobre W04/W05/W09:** Hasta febrero 2026 usaban solo la ventana de 35 min del scan para juzgar el historial. Esto causaba falsos positivos masivos. La corrección consulta el historial real via Data API de Polymarket con caché por scan.

---

### 6.2 O — Origen (3 filtros + COORD04)

Evalúan de dónde vienen los fondos de la wallet.

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| O01 | Origen exchange | +5 | Wallet fondeada desde Coinbase/Binance/Kraken/OKX/Crypto.com/Gate.io/Bybit en 1-2 hops | — |
| O02 | Fondeo reciente | +10 | Fondeo más reciente hace 3-7 días | Mut. excl. con O03 |
| O03 | Fondeo muy reciente | +5 | Fondeo más reciente hace < 3 días | Mut. excl. con O02 |
| COORD04 | Fondeo via mixer | +50 | Fondeada desde Tornado Cash o Railgun (Polygon) | Máxima señal de ocultamiento |

**Fase 2 (lazy):** Los filtros O solo se calculan si la fase 1 (W básicos) acumula ≥30 puntos. Evita llamadas innecesarias a Alchemy RPC.

---

### 6.3 B — Comportamiento (32 filtros definidos)

Evalúan los patrones de trading dentro del mercado bajo análisis.

#### B01-B17: Patrones básicos

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| B01 | Acumulación goteo | +20 | ≥5 compras en ventana de 24-72h (spread ≥24h para que no sea burst) | — |
| B05 | Solo market orders | +5 | Todas las trades son market orders (sin limit orders) | — |
| B06 | Tamaño creciente | +15 | Cada compra > la anterior (escalada de convicción) | — |
| B07 | Compra contra mercado | +20 | Al menos una trade con precio CLOB < 0.20. El CLOB retorna el precio del token comprado, por tanto precio bajo = apuesta contraria independientemente de la dirección | — |
| B14 | Primera compra grande | +15 | Primera compra ≥ $5,000 | **Suprimido si B19 disparó** |
| B16 | Acumulación rápida | +20 | ≥3 trades en ventana de < 4 horas | — |
| B17 | Horario bajo | +10 | Al menos una trade entre las 2:00–6:00 AM UTC | — |

#### B18a-d: Tiers de acumulación total (mutuamente excluyentes, requieren ≥2 trades)

| ID | Nombre | Pts base | Rango total acumulado | Bonus por trades |
|----|--------|----------|----------------------|-----------------|
| B18a | Acumulación moderada | +15 | $2,000–$3,499 | +5 si 3-4 trades; +10 si ≥5 |
| B18b | Acumulación significativa | +25 | $3,500–$4,999 | +5 si 3-4 trades; +10 si ≥5 |
| B18c | Acumulación fuerte | +35 | $5,000–$9,999 | +5 si 3-4 trades; +10 si ≥5 |
| B18d | Acumulación muy fuerte | +50 | ≥ $10,000 | +5 si 3-4 trades; +10 si ≥5 |

**Regla crítica:** Requieren `trade_count ≥ 2`. Una sola compra grande dispara B19 (whale entry), no B18.

**B18e (retirado):** Existía "Sin impacto en precio" (+15) que fue reemplazado por B26a/B26b. Sigue definido en config.py pero ya no se activa en el código.

#### B19a-c: Whale entries (mutuamente excluyentes, bypass a Telegram directo)

| ID | Nombre | Pts | Rango de single transaction | Notas |
|----|--------|-----|---------------------------|-------|
| B19a | Entrada grande | +20 | Single tx ≥$5,000 y <$10,000 | Mut. excl. con B19b/c |
| B19b | Entrada muy grande | +30 | Single tx ≥$10,000 y <$50,000 | Mut. excl. con B19a/c |
| B19c | Entrada masiva | +40 | Single tx ≥ $50,000 | Mut. excl. con B19a/b |

Cuando se activa cualquier B19, se envía una alerta especial `whale_entry` por Telegram independientemente del score total.

#### B20: Wallet veterana nueva en Polymarket

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| B20 | Vieja nueva en PM | +20 | `wallet_age_days > 180` Y primera actividad en PM detectada < 7 días. Suprimido si `real_distinct_markets > 3` | — |

#### B23a-b: Tamaño de posición vs balance (mutuamente excluyentes con B28)

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| B23a | Posición significativa | +15 | Posición = 20-50% del balance USDC | Mut. excl. con B23b; **suprimido si B28 disparó** |
| B23b | Posición dominante | +30 | Posición > 50% del balance USDC | Mut. excl. con B23a; **suprimido si B28 disparó** |

Guards: Solo aplica si `wallet_balance ≥ $50` y `total_amount ≥ $50`. Ratio > 10× se suprime (balance es residuo post-trade). También suprimido si volumen histórico PM > 3× balance USDC (wallet con capital no visible).

#### B25a-c: Convicción por odds (mutuamente excluyentes, opuesto de N09)

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| B25a | Convicción extrema | +25 | Precio promedio de trades < 0.10 | Mut. excl. con B25b/c y con N09 |
| B25b | Convicción alta | +15 | Precio promedio 0.10–0.20 | Mut. excl. con B25a/c y con N09 |
| B25c | Convicción moderada | +5 | Precio promedio 0.20–0.35 | Mut. excl. con B25a/b y con N09 |

Si B25 dispara, N09 no puede disparar en la misma wallet/mercado (son señales opuestas).

#### B26a-b: Acumulación stealth (mutuamente excluyentes, reemplazan B18e)

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| B26a | Stealth whale | +20 | Total acumulado ≥$5K Y price_move entre primera y última trade < 1% | Requiere ≥2 trades |
| B26b | Low impact | +10 | Total acumulado ≥$3K Y price_move < 3% | Requiere ≥2 trades |

Detectan wallets que acumulan posiciones grandes sin mover el mercado.

#### B27a-b: Diamond hands — DESHABILITADO (`ENABLE_B27 = False`)

| ID | Nombre | Pts | Condición | Estado |
|----|--------|-----|-----------|--------|
| B27a | Diamond hands 48h | +15 | Mantuvo 24-48h sin vender, odds +5% | DESHABILITADO |
| B27b | Diamond hands 72h+ | +20 | Mantuvo 72h+ sin vender, odds +10% | DESHABILITADO |

Razón: Requiere datos de ventas pre-alerta que sell_detector aún no cubre.

#### B28a-b: All-in (mutuamente excluyentes, suprimen B23)

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| B28a | All-in extremo | +25 | Posición > 90% del balance USDC y `total_amount ≥ $3,500` | Mut. excl. con B28b; suprime B23 |
| B28b | All-in fuerte | +20 | Posición 70-90% del balance y `total_amount ≥ $3,500` | Mut. excl. con B28a; suprime B23 |

Floor de $3,500 para evitar que wallets con pocket money parezcan all-in. Suprimido si volumen PM > 3× balance USDC.

#### B30a-c: First mover — DESHABILITADO (`ENABLE_B30 = False`)

| ID | Nombre | Pts | Estado |
|----|--------|-----|--------|
| B30a | First mover | +20 | DESHABILITADO |
| B30b | Early mover top 3 | +10 | DESHABILITADO |
| B30c | Early mover top 5 | +5 | DESHABILITADO |

Razón: La ventana de 35 min no cubre suficiente historial. Se activará cuando exista tabla de trades históricos en Supabase.

#### N08: Anti-bot evasión (positivo, categoría behavior)

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| N08 | Anti-bot evasión | **+25** | Intervalos irregulares (N01 no dispara) PERO montos de trade muy uniformes (CoV < 10%). Indica wallet que evita detección variando timing pero con importes fijos | Solo dispara cuando N01 no disparó |

N08 tiene categoría "behavior" → contribuye a COORDINATION en scoring. Es positivo (+25), no una penalización.

---

### 6.4 C — Confluencia (9 filtros, 4 capas)

Detectan coordinación entre múltiples wallets. Se evalúan **por grupo** (union-find por funding), no globalmente.

#### Capa 1: Dirección (mutuamente excluyentes)

| ID | Nombre | Pts | Condición exacta |
|----|--------|-----|-----------------|
| C01 | Confluencia básica | +10 | ≥3 wallets del mismo grupo apostando en la misma dirección |
| C02 | Confluencia fuerte | +15 | ≥5 wallets del mismo grupo en la misma dirección |

#### Capa 2: Origen de fondeo (aditivos — cada tipo dispara independientemente)

| ID | Nombre | Pts | Condición exacta |
|----|--------|-----|-----------------|
| C03a | Origen exchange compartido | +5 | ≥2 wallets fondeadas desde el mismo exchange |
| C03b | Origen bridge compartido | +20 | ≥2 wallets fondeadas desde el mismo bridge |
| C03c | Origen mixer compartido | +30 | ≥2 wallets fondeadas desde el mismo mixer |
| C03d | Mismo padre directo | +30 | ≥2 wallets fondeadas desde el mismo sender no-infra, no-exchange, no-bridge, no-mixer |

Los 4 subtipos de C03 pueden dispararse simultáneamente si existen distintos tipos de sender compartido.

#### Capa 3: Bonus (aditivos, apilan con capas 1 y 2)

| ID | Nombre | Pts | Condición exacta |
|----|--------|-----|-----------------|
| C05 | Fondeo temporal | +10 | ≥3 wallets fondeadas desde exchange en ventana < 4h, misma dirección |
| C06 | Monto similar | +10 | El sender común financió las wallets con montos dentro de ±30% del mediano |

#### Capa 4: Red de distribución

| ID | Nombre | Pts | Condición exacta |
|----|--------|-----|-----------------|
| C07 | Red de distribución | +30 | 1 sender → ≥3 wallets activas en este mercado específico |

**Corrección crítica de confluencia por odds:** Wallets apostando en la misma dirección pero a odds muy diferentes NO se agrupan como coordinadas. La agrupación usa union-find por relaciones de funding, no por dirección. Esto evita que wallets independientes que coinciden en la dirección mayoritaria se traten como coordinadas.

---

### 6.5 M — Mercado (8 filtros)

Evalúan las condiciones del mercado en el momento del análisis.

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| M01 | Volumen anómalo | +15 | Volumen 24h > 2× promedio 7 días. Requiere historial en `market_snapshots` | Graceful: vacío si sin historial |
| M02 | Odds estables rotos | +20 | Odds estables >48h (range histórico < 10%) luego movimiento actual > 10% del punto medio | Requiere ≥48h snapshots |
| M03 | Baja liquidez | +10 | Liquidez actual < $100,000 | — |
| M04a | Concentración moderada | +15 | Top 3 wallets > 60% del volumen total del mercado | Mut. excl. con M04b |
| M04b | Concentración alta | +25 | Top 3 wallets > 80% del volumen total del mercado | Mut. excl. con M04a |
| M05a | Deadline <72h | +10 | Mercado resuelve en 24-72 horas | Mut. excl. con M05b/c |
| M05b | Deadline <24h | +15 | Mercado resuelve en 6-24 horas | Mut. excl. con M05a/c |
| M05c | Deadline <6h | +25 | Mercado resuelve en < 6 horas | Mut. excl. con M05a/b |

---

### 6.6 N — Negativos (16 filtros)

Detectan ruido y penalizan el score. Implementados en `NoiseFilter` (N01-N08) y `BehaviorAnalyzer` (N09-N10).

| ID | Nombre | Pts | Condición exacta | Notas |
|----|--------|-----|-----------------|-------|
| N01 | Bot | -40 | Desviación estándar de intervalos entre trades < 1 segundo | — |
| N02 | Noticias | -20 | Noticias relacionadas en Google News en últimas 24h | — |
| N03 | Arbitraje | -100 | Posiciones YES+NO en mercados equivalentes | Elimina la alerta efectivamente |
| N04 | Mercados opuestos | 0 | Mismo patrón pero sin arbitraje claro | Solo logging |
| N05 | Copy-trading | -25 | Trade 2-10 minutos después de una trade whale conocida en el mismo mercado y dirección | — |
| N06a | Degen leve | -5 | 1-2 mercados no políticos en historial de la wallet | Mut. excl. con N06b/c |
| N06b | Degen moderado | -15 | 3-5 mercados no políticos | Mut. excl. con N06a/c |
| N06c | Degen fuerte | -30 | ≥6 mercados no políticos | Mut. excl. con N06a/b |
| N07a | Scalper leve | -20 | Compra y vende en el mismo mercado en < 2 horas | Mut. excl. con N07b |
| N07b | Scalper serial | -40 | Flip en ≥3 mercados distintos en < 2h cada uno | Mut. excl. con N07a |
| N08 | Anti-bot evasión | **+25** | Ver sección B — es positivo | Solo si N01 no disparó |
| N09a | Apuesta obvia extrema | -40 | Precio promedio > 0.90 (apuesta CON el consenso extremo) | Cap: máx 2★; mut. excl. con N09b y con B25 |
| N09b | Apuesta obvia | -25 | Precio promedio > 0.85 | Cap: máx 3★; mut. excl. con N09a y con B25 |
| N10a | Horizonte lejano | -10 | Mercado resuelve en > 30 días | Mut. excl. con N10b/c |
| N10b | Horizonte muy lejano | -20 | Mercado resuelve en > 60 días | Mut. excl. con N10a/c |
| N10c | Horizonte extremo | -30 | Mercado resuelve en > 90 días | Mut. excl. con N10a/b |

---

### 6.7 Grupos mutuamente excluyentes

El motor de scoring aplica esta tabla antes de sumar. Por cada grupo, solo sobrevive el de mayor `abs(points)`.

| Grupo | Filtros incluidos |
|-------|-----------------|
| Edad de wallet | W01, W02, W03 |
| Mercados de wallet | W04, W05 |
| Recencia de fondeo | O02, O03 |
| Acumulación tiers | B18a, B18b, B18c, B18d |
| Whale entry tiers | B19a, B19b, B19c |
| Posición vs balance | B23a, B23b |
| Convicción por odds | B25a, B25b, B25c |
| Stealth tiers | B26a, B26b |
| Diamond hands | B27a, B27b (ambos deshabilitados) |
| All-in tiers | B28a, B28b |
| First mover tiers | B30a, B30b, B30c (todos deshabilitados) |
| Confluencia dirección | C01, C02 |
| Concentración volumen | M04a, M04b |
| Deadline proximity | M05a, M05b, M05c |
| Degen tiers | N06a, N06b, N06c |
| Scalper tiers | N07a, N07b |
| Obvious bet tiers | N09a, N09b |
| Long-horizon discount | N10a, N10b, N10c |

**Exclusiones entre filtros (no grupos formales):**
- B14 suprimido si cualquier B19 dispara
- B23 suprimido si cualquier B28 dispara
- N09 suprimido si cualquier B25 dispara (y viceversa)
- N08 suprimido si N01 dispara

---

## 7. Sistema de scoring

### 7.1 Pipeline de cálculo

```python
# Pseudocódigo del flujo completo (scoring.py + behavior_analyzer.py)

# PASO 1: Exclusión mutua
filtros = _enforce_mutual_exclusion(filters_triggered)
# Solo el de mayor |points| sobrevive por grupo

# PASO 2: Score raw
score_raw = max(0, sum(f.points for f in filtros))

# PASO 3: Categorías disparadas (para validación posterior)
# wallet, origin   → ACCUMULATION
# behavior, confluence → COORDINATION
# market           → TIMING

# PASO 4: Multiplicador por monto (logarítmico)
# Fórmula: 0.18 * ln(total_usd) - 0.37, clampado a [0.3, 2.0]
amount_mult = clamp(0.18 * ln(total_amount) - 0.37, 0.3, 2.0)

# PASO 5: Multiplicador por diversidad (sniper/shotgun)
# wallet_market_count = mercados distintos en los que opera la wallet
if wallet_market_count <= 3:
    diversity_mult = 1.2   # sniper
elif wallet_market_count >= 20:
    diversity_mult = 0.5   # super shotgun
elif wallet_market_count >= 10:
    diversity_mult = 0.7   # shotgun
else:
    diversity_mult = 1.0

# PASO 6: Multiplicador final combinado
multiplier = round(amount_mult * diversity_mult, 2)

# PASO 7: Score final (con techo)
SCORE_CAP = 400
score_final = min(SCORE_CAP, round(score_raw * multiplier))

# PASO 8: Lookup de estrellas (NEW_STAR_THRESHOLDS)
star_level = lookup_stars(score_final)
# ≥220→5, ≥150→4, ≥100→3, ≥70→2, ≥40→1, <40→0

# PASO 9: Validación de estrellas (puede bajar el nivel)
while star_level >= 3:
    reqs = STAR_VALIDATION[star_level]
    if len(categories) < reqs["min_categories"]:
        star_level -= 1; continue
    if total_amount < reqs.get("min_amount", 0):
        star_level -= 1; continue
    if reqs.get("requires_coord") and "COORDINATION" not in categories:
        star_level -= 1; continue
    break  # todos los requisitos cumplidos

# PASO 10: Cap por N09 (apuesta obvia)
if "N09a" in filter_ids: star_level = min(star_level, 2)
if "N09b" in filter_ids: star_level = min(star_level, 3)
```

### 7.2 Multiplicador por monto (logarítmico)

Fórmula: `0.18 × ln(total_usd) - 0.37`, clamped a [0.3, 2.0]

| Monto total | Multiplicador |
|-------------|-------------|
| < $0 / vacío | 0.30× (mínimo) |
| $100 | 0.46× |
| $500 | 0.75× |
| $1,000 | 0.87× |
| $5,000 | 1.17× |
| $10,000 | 1.29× |
| $50,000 | 1.57× |
| $100,000 | 1.70× |

La curva logarítmica reemplaza los tiers discretos anteriores para una progresión más suave sin saltos bruscos.

### 7.3 Multiplicador por diversidad (sniper/shotgun)

Premia la especialización: un insider con información real suele apostarlo todo en un solo mercado.

| Mercados distintos (72h) | Tipo | Multiplicador |
|---|---|---|
| ≤ 3 | Sniper | × 1.2 |
| 4–9 | Normal | × 1.0 |
| 10–19 | Shotgun | × 0.7 |
| ≥ 20 | Super shotgun | × 0.5 |
| None | Default | × 1.0 |

### 7.4 Umbrales de estrellas y validación

**Umbrales (NEW_STAR_THRESHOLDS):**

| Score final | Estrellas | DB | Dashboard web | Telegram | Twitter/X |
|---|---|---|---|---|---|
| ≥ 220 | 5★ | ✓ | ✓ | ✓ | ✓ |
| 150–219 | 4★ | ✓ | ✓ | ✓ | ✓ |
| 100–149 | 3★ | ✓ | ✓ | ✓ | ✓ |
| 70–99 | 2★ | ✓ | ✓ | ✓ | ✗ |
| 40–69 | 1★ | ✓ | ✓ | ✗ | ✗ |
| < 40 | 0★ | ✓ | ✗ | ✗ | ✗ |

**Validación de estrellas (puede bajar el nivel si no se cumplen requisitos):**

| Estrellas | Min. categorías distintas | Min. monto | COORDINATION requerida |
|---|---|---|---|
| 3★ | 2 | — | No |
| 4★ | 2 | $5,000 | No |
| 5★ | 3 | $10,000 | Sí |

**Cap por apuesta obvia (N09):**
- N09a (precio > 0.90): máximo 2★
- N09b (precio > 0.85): máximo 3★

**Odds range ampliado:** Alertas con score_final ≥ 90 pueden superar el límite normal de 0.55 hasta 0.70 (odds extendidos). Permite detectar insiders en mercados donde el evento ya es probable pero aún no está al máximo.

**Ejemplos reales (alertas de ataque a Irán, feb 2026):**
- Alerta 1: score_raw 140, total $8,500 → mult 1.28× → score_final **179** → 4★
- Alerta 2: score_raw 170, total $12,000 → mult 1.30× → score_final **221** → 5★

### 7.5 Multiplicadores heredados P1-P6 (legacy)

Estos patrones existieron como multiplicadores discretos en una versión anterior. **Ya no se usan en producción.** El sistema actual usa el multiplicador logarítmico continuo + diversidad. Se mantienen en `config.py` (sección `MULTIPLIER_PATTERNS`) solo como referencia histórica.

| ID | Nombre | Factor | Condición original |
|----|--------|--------|-------------------|
| P1 | Insider clásico | ×1.30 | W01+W09+O03 + cualquiera de {C01, C02, C03d, C07} |
| P2 | Coordinación limpia | ×1.20 | C02+M01 sin N02 |
| P3 | Urgencia | ×1.15 | ≥2 de {B16, B05, B17} |
| P4 | Fragmentación silenciosa | ×1.25 | Cualquiera de {B18c, B18d} + B18e + W04 |
| P5 | Acumulación silenciosa | ×1.30 | B18d + B18e + M02 |
| P6 | Red de distribución | ×1.40 | C07 + wallet nueva (W01/W02/W03) |

---

## 8. 6 Perfiles de insider

Patrones conceptuales que el sistema detecta. No son clases explícitas en el código — emergen de combinaciones de filtros.

### Perfil 1: Red de Distribución
**Descripción:** Una wallet "madre" distribuye fondos a múltiples wallets "hijas" que luego apuestan coordinadamente en el mismo mercado. El más difícil de ocultar porque deja rastro en la blockchain.

**Filtros clave:** C07 (+30, 1 sender → ≥3 wallets activas) + C03d (+30, mismo padre directo) + C01/C02 (confluencia de dirección). Si el funding pasó por Tornado Cash: COORD04 (+50).

**Señal de alta calidad:** Combina 4 categorías → validación de 5★ posible.

---

### Perfil 2: Hormiguitas
**Descripción:** Micro-acumulación escalonada muy lenta. La wallet va construyendo posición con compras pequeñas y frecuentes durante 24-72h, sin llamar la atención en ninguna trade individual.

**Filtros clave:** B01 (+20, ≥5 compras en 24-72h) + B06 (+15, tamaño creciente) + B18a-d (según total acumulado) + B26b (low impact si el precio no se mueve).

---

### Perfil 3: Stealth Whale
**Descripción:** Ballena que acumula una posición grande sin mover el precio del mercado. Accede a liquidez profunda o fragmenta cuidadosamente para no revelar sus intenciones.

**Filtros clave:** B18d (+50, ≥$10K acumulado) + B26a (+20, price_move < 1%) — anteriormente B18e. El score_raw puede ser alto con solo estos dos: 70 pts × mult por monto → 5★ con suficiente cantidad.

---

### Perfil 4: Explosivo
**Descripción:** Entrada única masiva en una sola transacción. No hay patrón de acumulación — todo o nada. Puede indicar certeza extrema o información con ventana temporal muy estrecha.

**Filtros clave:** B19c (+40, single tx ≥$50K) → trigger automático de alerta `whale_entry` en Telegram. Con B25 (convicción por odds) y B23/B28 (posición significativa del balance) el score puede superar 5★.

---

### Perfil 5: Francotirador
**Descripción:** Apuesta con alta convicción a odds muy desfavorables. Compra cuando el mercado no cree en el outcome — altos retornos potenciales si tiene razón, alta pérdida si se equivoca. El hecho de arriesgar a 0.08 odds indica información privilegiada o convicción extrema.

**Filtros clave:** B07 (+20, odds < 0.20) + B25a (+25, avg price < 0.10) + B23b/B28a (posición significativa del balance). A odds muy bajos, tanto B07 como B25a pueden co-existir.

---

### Perfil 6: Lobo Solitario
**Descripción:** Wallet nueva o reciente en Polymarket cuya primera (o casi primera) acción en el mercado es apostar una cantidad significativa. No hay historial de comportamiento degen — parece alguien que creó la wallet específicamente para esta apuesta.

**Filtros clave:** W01/W02/W03 (wallet nueva/reciente) + W09 (primera tx fue PM) + W04 (solo este mercado) + B14 (primera compra ≥$5K) o B19 (entrada whale) + B20 si la wallet es antigua pero nueva en PM.

---

## 9. Sistema de alertas

### 9.1 Agrupación y multi-signal

Antes de calcular el score, las wallets activas en un mercado se agrupan por relaciones de funding usando **union-find**:

```
Input: 5 wallets en el mercado
  Wallet A ← Sender X
  Wallet B ← Sender X    ] Grupo 1 (A, B, C — vinculadas por Sender X)
  Wallet C ← Sender X
  Wallet D ← Sender Y    → Grupo 2 (D — sola)
  Wallet E ← Sender Z    → Grupo 3 (E — sola)

Output: 3 grupos independientes, cada uno puntuado por separado
→ 3 alertas generadas (1 primaria + 2 secundarias si hay 2+ grupos)
```

Cada grupo genera su propio `Alert` con los filtros W/B/O del mejor wallet del grupo + filtros M del mercado + filtros C **scoped al grupo**.

**Multi-signal:** Si hay ≥2 grupos en el mismo mercado:
- El grupo de mayor score → **alerta primaria** (se publica)
- Los demás → **alertas secundarias** (solo DB, `is_secondary=True`)
- Ambos comparten `alert_group_id` (UUID) y `multi_signal=True`
- La alerta primaria incluye línea: "Multi-signal: N grupo(s) independientes"

### 9.2 Deduplicación

**Within-scan (Jaccard):** Las alertas del mismo scan con preguntas similares (similitud Jaccard ≥ 0.60 sobre tokens normalizados) se consolidan: solo el de mayor score se publica.

**Cross-scan (24h):** Si ya existe una alerta para el mismo `(market_id, direction)` en las últimas 24 horas, se actualiza la existente (odds, amount, score, wallets) en lugar de crear una nueva.

**Consolidación 4+★ (48h):** Si existe una alerta de 4-5★ para el mismo mercado+dirección en las últimas 48h, las nuevas wallets se mergean en ella. La alerta existente recibe las nuevas wallets + update de total_amount y confluence_count. Se envía mensaje de actualización a Telegram (no nueva alerta).

### 9.3 Publicación por canal

| Canal | Mínimo | Formato | Notas |
|-------|--------|---------|-------|
| DB (Supabase) | 0★ | — | Siempre se persiste |
| Dashboard web | 1★ | — | HTML auto-commit |
| Telegram (canal testing) | 1★ | `format_telegram_detailed` | Con IDs de filtros y puntos — canal privado de debug |
| Telegram (canal principal) | 2★ | `format_telegram_alert` | Con score, sin IDs de filtros |
| Telegram (canal público) | 3★ | `format_telegram_alert` | Sin score ni filtros |
| Telegram whale entry (B19) | Siempre | `format_whale_entry` | Bypass de stars — formato especial |
| Twitter/X | 3★ | `format_x_alert` (280 chars) | Máximo 10/día |

**Feature flags en `system_config`:**
- `scan_enabled` — kill switch maestro
- `publish_x` — activa/desactiva Twitter
- `publish_telegram` — activa/desactiva Telegram

### 9.4 Ciclo de vida de una alerta

```
CREADA (outcome='pending')
  ├── AlertTracking registrada (market_id, direction, odds_at_alert)
  ├── WalletPositions creadas (una por wallet de la alerta)
  │
  ├── [cada 6h] Tracker:
  │   └── Actualiza odds actuales para seguimiento
  │
  ├── [cada 6h] WhaleMonitor:
  │   ├── Full exit (>90% vendido) → notificación Telegram (4-5★)
  │   ├── Partial exit (30-90%)    → notificación Telegram (4-5★)
  │   ├── Additional buy           → notificación Telegram (3-5★)
  │   └── Nueva entrada misma wallet en otro mercado → Telegram (3-5★)
  │
  ├── [diario 08:00 UTC] Resolver:
  │   ├── Verifica si el mercado resolvió via CLOB API
  │   ├── outcome='correct' si la dirección acertó
  │   └── outcome='incorrect' si falló
  │
  └── RESUELTA
      ├── Wallet stats actualizados (markets_won/lost, win_rate)
      ├── Smart money leaderboard actualizado
      └── Notificación de resolución en Telegram/Twitter (opcional)
```

---

## 10. Sell Watch — monitoreo de salidas

Sistema de tracking de posiciones que **NO afecta el score** de alertas existentes — solo registra metadata de salidas para análisis posterior.

**Configuración:**
- `SELL_WATCH_MIN_STARS = 3` — solo monitorea alertas con ≥3★
- `SELL_WATCH_MIN_SELL_PCT = 0.20` — ignora ventas < 20% de la posición
- `SELL_WATCH_COOLDOWN_HOURS = 6` — máximo 1 evento de venta por alerta por 6h
- `SELL_WATCH_NOTIFY_MIN_STARS = 4` — Telegram solo para 4-5★

**Datos registrados en `alert_sell_events`:**

| Campo | Descripción |
|-------|-------------|
| `sell_amount` | Monto vendido en USD |
| `sell_pct` | Fracción de la posición original vendida |
| `event_type` | "FULL_EXIT" (≥90%) o "PARTIAL_EXIT" (20-90%) |
| `sell_price` | Precio al que se vendió el token |
| `original_entry_price` | Precio de entrada de la alerta |
| `pnl_pct` | Rendimiento estimado (%) |
| `held_hours` | Horas desde la alerta hasta la venta |

---

## 11. Exclusión de infraestructura

La detección de confluencia (C03d/C07) requiere identificar senders que son insiders reales, no contratos de infraestructura que financian a miles de wallets sin relación.

### Listas estáticas (`config.py`)

**KNOWN_EXCHANGES (11 direcciones):** Coinbase (3), Binance (3), Kraken, OKX, Crypto.com, Gate.io, Bybit — mantenidos para C03a pero excluidos de C03d/C07.

**KNOWN_BRIDGES (7 direcciones):** Polygon PoS Bridge, Polygon Plasma Bridge, Multichain, Hop Protocol, Across Protocol, Stargate (2) — mantenidos para C03b.

**KNOWN_INFRASTRUCTURE (3 direcciones):**

| Dirección | Descripción |
|-----------|-------------|
| `0xf70da97812cb96acdf810712aa562db8dfa3dbef` | Relay Solver (Polygon) |
| `0x3a3bd7bb9528e159577f7c2e685cc81a765002e2` | Polymarket Wrapped Collateral |
| `0xc288480574783bd7615170660d71753378159c47` | Bridge/router Polygon |

**MIXER_ADDRESSES (7 direcciones):** Tornado Cash en Polygon (5), Railgun en Polygon (2) — usados para COORD04 y C03c.

### Umbral automático

`SENDER_AUTO_EXCLUDE_MIN_WALLETS = 100` — cualquier sender que financie ≥100 wallets distintas en `wallet_funding` es excluido automáticamente de C03d/C07.

### Flujo de exclusión

```
Inicio del scan: refresh_excluded_senders()  [llamado 1× por scan]
  ├── POLYMARKET_CONTRACTS (hardcoded)
  ├── KNOWN_INFRASTRUCTURE (hardcoded)
  └── DB.get_high_fanout_senders(100) → _excluded_cache

Por mercado: _build_default_excluded()
  └── Devuelve copia de _excluded_cache (lazy init si refresh no se llamó)

detect() / group_and_detect():
  ├── Recibe excluded_senders adicionales (super-senders cross-market, modo quick)
  └── Filtra sender_to_wallets excluyendo todo el conjunto combinado
```

---

## 12. GitHub Actions — 8 workflows

| Workflow | Cron | Timeout | Entry point | Propósito |
|----------|------|---------|-------------|-----------|
| `scan.yml` | `0 */3 * * *` | 10 min | `python -m src.main` | Scan principal de mercados |
| `tracker.yml` | `0 1,7,13,19 * * *` | 10 min | `python -m src.tracking.run_tracker` | Tracking odds, whale monitor, sell watch |
| `resolver.yml` | `0 8 * * *` | 10 min | `python -m src.tracking.run_resolver` | Resolución de alertas pendientes |
| `check_resolutions.yml` | `0 0 * * *` | — | `python -m src.scripts.check_resolutions` | Validación adicional de resoluciones |
| `dashboard.yml` | `15 * * * *` | 5 min | `python -m src.dashboard.generate_dashboard` | Genera `docs/index.html` (auto-commit) |
| `publish_dashboard.yml` | Después de `dashboard.yml` | — | — | Copia `index.html` al repo público |
| `weekly_report.yml` | `0 8 * * 1` | 10 min | `python -m src.reports.weekly` | Reporte semanal (TODO: no implementado) |
| `monthly_report.yml` | `0 8 1 * *` | 10 min | `python -m src.reports.monthly` | Reporte mensual (TODO: no implementado) |

**Variables de entorno requeridas (GitHub Secrets):**

| Secret | Workflows que lo usan |
|--------|----------------------|
| `SUPABASE_URL`, `SUPABASE_KEY` | Todos |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` (= chat ID) | scan, tracker, resolver, check_resolutions, dashboard |
| `TELEGRAM_PRIVATE_CHANNEL_ID` | check_resolutions |
| `ALCHEMY_API_KEY`, `ALCHEMY_ENDPOINT` | scan |
| `TWITTER_API_KEY/SECRET`, `TWITTER_ACCESS_TOKEN/SECRET` | scan, weekly, monthly |

---

## 13. Base de datos Supabase

PostgreSQL (Supabase, plan free). 16 tablas.

### Tablas completas

| Tabla | Modelo Python | Propósito |
|-------|--------------|-----------|
| `alerts` | `Alert` | Alertas generadas. Campos clave: `score` (final), `score_raw`, `multiplier`, `star_level`, `filters_triggered` (JSONB), `wallets` (JSONB array), `outcome` (pending/correct/incorrect), `multi_signal`, `is_secondary`, `alert_group_id`, `secondary_count`, `total_sold_pct` |
| `wallets` | `Wallet` | Catálogo de wallets detectadas. Campos: `wallet_age_days`, `total_markets`, `win_rate`, `total_pnl`, `times_detected`, `origin_exchange`, `is_first_tx_pm`, `is_blacklisted`, `degen_score` |
| `markets` | `Market` | Mercados de Polymarket. Campos: `current_odds`, `volume_24h`, `volume_7d_avg`, `liquidity`, `resolution_date`, `is_resolved`, `outcome` |
| `wallet_funding` | `WalletFunding` | Traza de funding: `sender_address`, `hop_level` (1-2), `is_exchange`, `exchange_name`, `is_bridge`, `bridge_name`, `is_mixer`, `mixer_name` |
| `market_snapshots` | `MarketSnapshot` | Histórico de `odds`, `volume_24h`, `liquidity` por mercado. Alimenta M01 y M02 |
| `alert_tracking` | `AlertTracking` | Seguimiento de odds post-alerta. `outcome`: pending/correct/incorrect |
| `wallet_positions` | `WalletPosition` | Posiciones abiertas por wallet/mercado. `current_status`: open/sold/partial_sold |
| `alert_sell_events` | `SellEvent` | Eventos de venta detectados: `sell_pct`, `event_type`, `pnl_pct`, `held_hours` |
| `scans` | `Scan` | Log de cada ciclo: `markets_scanned`, `alerts_generated`, `duration_seconds`, `status` |
| `smart_money_leaderboard` | `SmartMoneyLeaderboard` | Ranking de wallets por `win_rate` y `estimated_pnl` |
| `wallet_categories` | `WalletCategory` | Clasificación: unknown/smart_money/whale/scalper/bot/degen |
| `notification_log` | — | Deduplicación de notificaciones enviadas por alerta |
| `whale_notifications` | — | Log de notificaciones whale específicas |
| `weekly_reports` | `WeeklyReport` | Reportes semanales (pendiente implementación) |
| `system_config` | `SystemConfig` | Feature flags: `scan_enabled`, `publish_x`, `publish_telegram` |
| `detected_infrastructure` | — | Senders de infra detectados automáticamente (>100 wallets) |

### Migración multi-signal (añadida en producción)

```sql
ALTER TABLE alerts
  ADD COLUMN IF NOT EXISTS multi_signal BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_secondary BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS alert_group_id TEXT,
  ADD COLUMN IF NOT EXISTS secondary_count INTEGER DEFAULT 0;
```

### APIs usadas

| Endpoint | Base URL | Propósito | Notas |
|----------|----------|-----------|-------|
| `GET /events` | `gamma-api.polymarket.com` | Descubrimiento de mercados activos | 0.1s delay |
| `GET /markets` | `gamma-api.polymarket.com` | Metadata de mercados | 0.1s delay |
| `GET /markets/{conditionId}` | `clob.polymarket.com` | Estado de resolución | **Usar CLOB, NO Gamma** |
| `GET /midpoint` | `clob.polymarket.com` | Odds actuales | 0.05s delay |
| `GET /trades` | `data-api.polymarket.com` | Historial de trades | 500/página, 5 páginas max |
| `GET /activity` | `data-api.polymarket.com` | Historial real de wallet en PM | Para supresión W04/W05/B20 |
| `alchemy_getAssetTransfers` | Alchemy Polygon | Traza de funding, edad de wallet | — |
| `eth_call (balanceOf)` | Alchemy Polygon | Balance USDC | — |

---

## 14. Scripts de utilidad

| Script | Comando | Propósito |
|--------|---------|-----------|
| `setup_db.py` | `python -m scripts.setup_db` | Verifica que las tablas requeridas existen en Supabase |
| `cleaner_post_deep.py` | `python -m scripts.cleaner_post_deep` | Post-proceso del deep scan: detecta infra nueva, audita alertas con C03d/C07 falsos, recalcula scores. Es idempotente |
| `audit_false_confluence.py` | `python -m scripts.audit_false_confluence` | Informe de alertas con confluencia potencialmente falsa + recálculo opcional |
| `backfill.py` | `python -m scripts.backfill` | Backfill histórico de wallet ages y funding |
| `test_connection.py` | `python -m scripts.test_connection` | Test de conexión a Supabase |
| `test_polymarket.py` | `python -m scripts.test_polymarket` | Test de APIs Polymarket |
| `test_blockchain.py` | `python -m scripts.test_blockchain` | Test de Alchemy RPC |
| `test_telegram.py` | `python -m scripts.test_telegram` | Test del bot de Telegram |
| `test_news.py` | `python -m scripts.test_news` | Test del news checker |

**Cleaner post-deep:** Detecta senders de infra nuevos (>100 wallets) y los persiste en `detected_infrastructure`. Expande los prefijos truncados de addresses para match exacto en `wallet_funding`. Segundo paso recalcula scores y actualiza en Supabase.

---

## 15. Bugs críticos resueltos

### Bug 1 — Triple-counting en transacciones grandes

| | |
|---|---|
| **Descripción** | B14 (Primera compra grande, +15), B18d (Acumulación muy fuerte, +50), y B19b (Entrada muy grande, +30) podían dispararse simultáneamente cuando una wallet hacía una sola compra grande (>$10K). Los tres señalaban el mismo evento desde distintos ángulos. |
| **Impacto** | Score inflado hasta +95 pts extra. Alertas de 5★ donde debería haber 3-4★. |
| **Solución** | (1) B14 suprimido si cualquier B19 disparó. (2) B18 requiere `trade_count ≥ 2` — un single buy no es "acumulación". (3) B23 suprimido si B28 dispara. Motor de scoring garantiza exclusión mutua vía `MUTUALLY_EXCLUSIVE_GROUPS`. |
| **Commit** | `34954bd feat: B14/B18/B19 anti-triple-counting` |
| **Estado** | Resuelto |

### Bug 2 — Market resolver con endpoint roto (94 alertas falsamente resueltas)

| | |
|---|---|
| **Descripción** | El endpoint `GET /markets` de la Gamma API ignora el parámetro `conditionId` y siempre retorna el primer mercado de su base de datos (un mercado de Biden de 2020). El sistema comparaba outcomes contra el mercado equivocado. |
| **Impacto** | 94 alertas marcadas como "correct" o "incorrect" basándose en datos del mercado Biden 2020. Estadísticas de precisión completamente inválidas. |
| **Solución** | Switch al CLOB `GET /markets/{condition_id}`. Salvaguardas adicionales: verificar que el `condition_id` retornado coincide con el consultado; saltar si `end_date` es futuro; usar `tokens[].winner` como fuente primaria; fallback a `price > 0.9 / < 0.1` solo si sin winner flag. |
| **Commit** | `2d1c56a fix: resolver uses CLOB API instead of broken Gamma API` |
| **Estado** | Resuelto |

### Bug 3 — Falsos positivos por dependencia de ventana corta (342 alertas corregidas)

| | |
|---|---|
| **Descripción** | Los filtros W04, W05, W09, B20, B23, B28, y N06 usaban únicamente la ventana de 35 minutos del scan para juzgar el historial de una wallet. Una wallet con 50 mercados en PM podía disparar W04 simplemente por no aparecer en los últimos 35 minutos. |
| **Impacto** | Falsos positivos sistemáticos en wallets con historial real en PM. Score 20-30 pts más alto del real. En corrección retroactiva post-fix: **342 alertas** corregidas en producción. |
| **Solución** | Consulta al Data API de Polymarket (`/activity`) para obtener historial real antes de disparar cada filtro. Umbrales: W04 suprimido si `real_distinct_markets > 3`, W05 si `> 5`, B20/W09 si `> 3`. Caché por scan (1 fetch por wallet). Fail-safe: si API falla, el filtro se dispara de todas formas. |
| **Commit** | `b11c119 fix: suppress lookback-dependent false positives` |
| **Estado** | Resuelto. Corrección retroactiva ejecutada una vez y comentada en cleaner para evitar re-ejecución. |

### Bug 4 — Falsa confluencia por senders de infraestructura

| | |
|---|---|
| **Descripción** | Relay Solver (Polygon), Polymarket Wrapped Collateral, y routers genéricos financiaban a cientos de wallets sin relación. El sistema los detectaba como C03d ("mismo padre directo") y C07 ("red de distribución"), generando señales de coordinación completamente falsas. |
| **Impacto** | Alertas de confluencia en mercados donde múltiples wallets simplemente habían pasado por el mismo contrato de infraestructura de Polymarket. |
| **Solución** | (1) Lista estática `KNOWN_INFRASTRUCTURE` en config.py. (2) Exclusión automática para senders con ≥100 wallets distintas (`SENDER_AUTO_EXCLUDE_MIN_WALLETS = 100`). (3) Scripts `audit_false_confluence.py` y `cleaner_post_deep.py` para corrección retroactiva. |
| **Commit** | `c8e57dc feat: exclude infrastructure senders from confluence detection` |
| **Estado** | Resuelto |

### Bug 5 — Manejo de precios en dirección NO (idas y vuelta)

| | |
|---|---|
| **Descripción** | Para trades en dirección NO, se implementó inversión de precio (`1 - price`) asumiendo que el CLOB retornaba el precio del token YES. Esta asunción era incorrecta — el CLOB retorna el precio del token comprado (NO token para compras NO). La "corrección" invertía los precios correctos, haciendo que B07 y B25 funcionaran al revés para trades NO. |
| **Impacto** | Filtros B07 y B25 evaluaban el complemento del precio real para direcciones NO — falsos positivos y negativos en ambas direcciones. |
| **Solución** | Revertir la inversión. El CLOB ya retorna el precio correcto del token comprado. No se necesita conversión. |
| **Commits** | `d14876d fix: correct NO direction odds handling` → `76341d7 fix: revert NO direction price conversion` |
| **Estado** | Resuelto |

### Bug 6 — Rate limiting en deep scan

| | |
|---|---|
| **Descripción** | El modo deep con asyncio puro y muchos mercados concurrentes generaba errores 429 (Too Many Requests) de las APIs de Polymarket, causando pérdida de datos y scans incompletos. |
| **Impacto** | Deep scans fallaban parcialmente con errores HTTP 429 silenciosos o propagados. Mercados sin analizar. |
| **Solución** | Procesamiento en batches de 5 mercados, pausa de 1 segundo entre batches, retry con backoff exponencial via `tenacity`. |
| **Commit** | `5713198 fix: deep scan rate limiting — batch processing with retry backoff` |
| **Estado** | Resuelto |

### Bug 7 — Dashboard: precio de entrada incorrecto + estrellas sobre score_raw

| | |
|---|---|
| **Descripción** | El dashboard mostraba el precio de entrada del primer trade disponible (no del wallet principal de la alerta). Además calculaba las estrellas del dashboard a partir de `score_raw` en lugar de `score_final` (post-multiplicador). |
| **Impacto** | Datos de dashboard incorrectos: precios de entrada y niveles de estrella erróneos para alertas con multiplicador significativo. |
| **Solución** | Tomar precio de entrada del primer trade del wallet principal de la alerta. Usar `score_final` para lookup de estrellas en el dashboard. |
| **Commit** | `3b800a6 fix: dashboard entry price from wallet + verify stars use final score` |
| **Estado** | Resuelto |

---

## 16. Decisiones técnicas y lecciones aprendidas

### Pump-and-dump: de penalización a notificación

**Contexto:** Se intentó detectar y penalizar el "pump-and-dump" — wallets que compran para subir el precio y luego venden.

**Aprendizaje:** Los insiders legítimos también venden cuando tienen ganancias. Una wallet que acumula durante días y vende cuando el precio ha subido está haciendo exactamente lo que haría un insider real tomando profits. Penalizarlo como comportamiento negativo eliminaba señales válidas.

**Decisión:** Eliminar la penalización por ventas. El sistema ahora *monitorea* las ventas (Sell Watch) como metadata adicional, sin afectar el score de alertas ya generadas.

---

### Confluencia requiere validación de dirección y agrupación por funding

**Contexto:** Inicialmente la confluencia se calculaba sobre todas las wallets del mercado en la misma dirección, independientemente de sus relaciones de funding.

**Problema 1:** Dos wallets apostando YES a 0.30 y NO a 0.70 no están coordinadas — están en lados opuestos. Agruparlas por "misma dirección" era incorrecto.

**Problema 2:** Wallets de grupos de funding distintos que coinciden en la misma dirección no son evidencia de coordinación — pueden ser señales independientes (y de hecho más valiosas como multi-signal).

**Decisión:** (1) Agrupar por relaciones de funding (union-find), no por dirección. (2) Filtrar por dirección dentro de cada grupo. (3) Múltiples grupos independientes = multi-signal, no confluencia.

---

### Scoring basado en convicción, no solo en dirección

**Aprendizaje:** Un insider que apuesta a 0.08 odds tiene información mucho más valiosa que uno que apuesta a 0.45. El sistema recompensa esto con B25 (convicción extrema, +25) y B07 (compra contra mercado, +20). El multiplicador de diversidad complementa esto premiando wallets especializadas (sniper ×1.2) sobre shotguns dispersadas.

---

### Por qué dual-mode en vez de solo GitHub Actions

**Constraint real:** GitHub Actions limita jobs a 10 minutos. Analizar 450+ mercados con lookback de 24h requiere consultas a 3 APIs distintas por mercado — imposible en ese tiempo.

**Solución práctica:** El modo quick (GitHub Actions) cubre monitoreo continuo automatizado. El modo deep (local, asyncio paralelo) permite cobertura completa cuando se necesita — por ejemplo, tras un evento geopolítico significativo.

---

### Por qué Polymarket CLOB y no solo Gamma para resolución

**Aprendizaje crítico (bug 2):** El endpoint `GET /markets` de Gamma ignora el parámetro `conditionId` — este es un bug conocido de la API. Usarlo para resolución de mercados retorna datos del mercado incorrecto.

**Decisión:** Gamma solo para descubrimiento de mercados (lista de activos). CLOB para resolución y trading data. Data API para historial de wallets individuales.

---

### Por qué ventana de lookback variable vs. fija

**Aprendizaje:** Una ventana de 35 minutos es suficiente para el monitoreo continuo (no se pierden trades entre scans), pero absolutamente insuficiente para juzgar el historial de una wallet. La misma ventana no puede usarse para ambos propósitos.

**Decisión:** Ventana de 35 min SOLO para detectar trades nuevos. Para juzgar historial de wallet (W04/W05/B20/N06), consultar el Data API de Polymarket con historial real.

---

## 17. Tests

**Total actual: 737 tests en 26 archivos** (verificado con `pytest --collect-only -q`)

| Archivo | Tests aprox. | Cobertura principal |
|---------|-------------|-------------------|
| `test_filters_integration.py` | 180+ | Integración: false positives por lookback, supresión Data API, mutual exclusion end-to-end |
| `test_main.py` | 50 | Orquestación, dedup, publish routing, multi-signal |
| `test_formatter.py` | 66 | 12+ formatos de mensaje Telegram/X |
| `test_behavior_analyzer.py` | 56 | Filtros B01-B30, N09-N10, exclusiones mutuas |
| `test_scoring.py` | 49 | Stars, multiplicadores, validación, N09 cap |
| `test_confluence.py` | 48 | Capas C01-C07, exclusiones de infra, group_and_detect |
| `test_dashboard.py` | 27 | Generación del dashboard HTML |
| `test_twitter_bot.py` | 26 | Publicación en X/Twitter |
| `test_resolver.py` | 26 | Resolución de mercados, CLOB API, salvaguardas |
| `test_sell_watch.py` | 23 | Sell Watch, eventos de venta |
| `test_telegram_bot.py` | 22 | Publicación Telegram multicanal |
| `test_noise_filter.py` | 20 | N01-N08, detección de bots |
| `test_arbitrage_filter.py` | 16 | N03/N04 detección de arbitraje |
| `test_market_analyzer.py` | 15 | M01-M05 |
| `test_alert_notifier.py` | 13 | Notificaciones de resolución |
| `test_market_analyzer_new.py` | 12 | M04/M05 nuevos |
| `test_alert_tracker.py` | 12 | Tracking de odds |
| `test_noise_new.py` | 10 | N07/N08 |
| `test_check_resolutions.py` | 10 | Script de resoluciones |
| `test_wallet_tracker.py` | 10 | Win-rate tracking |
| `test_wallet_analyzer.py` | 9 | W01-W11, O01-O03 |
| `test_behavior_new.py` | 9 | B25-B30, N09-N10 |
| `test_whale_monitor.py` | 9 | Monitor whale |
| `test_telegram_multichannel.py` | 8 | Routing multicanal Telegram |
| `test_consolidation.py` | 7 | Consolidación de alertas |
| `test_sell_detector.py` | 5 | Detección individual de ventas |

**Ejecutar todos los tests:**
```bash
source venv/bin/activate
python -m pytest tests/ -v
```

**Ejecutar tests de una categoría:**
```bash
python -m pytest tests/test_behavior_analyzer.py tests/test_filters_integration.py -v
```

---

## 18. Roadmap

### Fase actual — Feb/Mar 2026: Refinamiento y validación

El sistema está en producción desde enero 2026. Prioridades:
- Detectar y corregir bugs en filtros existentes (7 bugs críticos ya resueltos en feb 2026)
- Afinar scoring: reducir false positives, mejorar precisión de detección
- Acumular track record: publicar alertas y verificar si el mercado resolvió en la dirección apostada

**Métrica de éxito:** Porcentaje de alertas ≥3★ con outcome="correct" > 60% en muestra estadísticamente significativa (>100 alertas resueltas).

---

### Hasta Mayo 2026: Validación con datos reales

- Continuar refinando filtros hasta comportamiento estable
- Acumular suficientes alertas resueltas (>100 con ≥2★) para evaluar precisión por tier de estrellas
- No añadir filtros nuevos hasta validar los existentes
- Activar **B27** (Diamond hands) cuando sell_detector cubra posiciones pre-alerta
- Activar **B30** (First mover) cuando exista tabla de trades históricos en Supabase
- Implementar reportes semanales y mensuales (actualmente stub `NotImplementedError`)

---

### Post-Mayo 2026: Groundwork ML

Con filtros validados y track record suficiente:
- **Scraping histórico** de Polymarket desde 2023/2024 (mercados ya resueltos con outcome conocido)
- **Aplicar filtros heurísticos** actuales sobre datos históricos para labelear casos
- **Etiquetar casos reales** de insider trading como training data positivo
- Las 3 tablas de Supabase para ML ya están preparadas: `alert_tracking` (outcome conocido), `smart_money_leaderboard` (win rate acumulado), `wallet_categories` (clasificación con datos históricos)

---

### Largo plazo (2027+): Componente ML

Con 1-2 años de datos históricos etiquetados:
- Los heurísticos actuales (scores de filtros) se convierten en **features del modelo ML**
- Añadir features adicionales: correlación temporal con noticias, análisis de red de wallets relacionadas, timing vs. deadline de resolución
- Los modelos ML predicen probabilidad de insider trading como complemento del sistema heurístico
- Infraestructura ya lista en Supabase para almacenar y servir features

---

## 19. Resumen del sistema

| Métrica | Valor |
|---------|-------|
| Filtros definidos en config.py | 76 |
| Filtros activos en producción | 70 (6 deshabilitados: B18e retirado; B27a-b y B30a-c pendientes) |
| Tests automatizados | **737** (26 archivos) |
| Workflows de GitHub Actions | 8 |
| Tablas en Supabase | 16 |
| Mercados en modo quick | ~100 (top por volumen 24h) |
| Mercados en modo deep | 450+ (sin cap, ~4 min) |
| Frecuencia de scan automatizado | Cada 3 horas |
| Lookback quick | 35 minutos |
| Lookback deep | 24 horas |
| Coste operacional mensual | $0 (todos los servicios en plan free) |
| En producción desde | Enero 2026 |
| Bugs críticos resueltos | 7 |
| Alertas corregidas retroactivamente | 342 (fix falsos positivos feb 2026) + 94 (fix resolver feb 2026) |
| Score máximo posible | 400 pts (techo definido en `SCORE_CAP`) |
| Umbral mínimo para publicación Telegram | 2★ (score_final ≥ 70) |
| Umbral mínimo para publicación Twitter | 3★ (score_final ≥ 100) |
