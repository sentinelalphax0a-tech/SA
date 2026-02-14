# ESTADO ACTUAL — Sentinel Alpha

> Generado: 2026-02-14 | Basado en el código fuente actual

---

## 1. Filtros Implementados (55 filtros)

### 1.1 Wallet Filters (W) — 7 filtros

| ID | Nombre | Puntos | Qué detecta | Umbrales exactos |
|----|--------|--------|-------------|------------------|
| W01 | Wallet muy nueva | +25 | Wallet creada hace muy pocos días | `wallet_age < 7 días` |
| W02 | Wallet nueva | +20 | Wallet creada recientemente | `7 ≤ wallet_age < 14 días` |
| W03 | Wallet reciente | +15 | Wallet con poca antigüedad | `14 ≤ wallet_age < 30 días` |
| W04 | Solo 1 mercado | +10 | Wallet que solo opera en 1 mercado | `total_markets == 1` |
| W05 | Solo 2-3 mercados | +15 | Wallet con actividad en pocos mercados | `2 ≤ total_markets ≤ 3` |
| W09 | Primera tx = Polymarket | +5 | La primera transacción de la wallet fue en Polymarket | `is_first_tx_polymarket == True` |
| W11 | Balance redondo | +3 | Balance cercano a cifra redonda ($5k/$10k/$50k) | `balance ∈ {$5,000, $10,000, $50,000} ±1%` |

**Exclusión mutua:** W01/W02/W03 (se queda el de mayor puntaje); W04/W05.

**Implementación:** `src/analysis/wallet_analyzer.py` — Dos fases: fase 1 (checks baratos: edad, mercados, primera tx, balance) y fase 2 (funding, solo si fase 1 ≥ 30 pts).

---

### 1.2 Origin Filters (O) — 3 filtros

| ID | Nombre | Puntos | Qué detecta | Umbrales exactos |
|----|--------|--------|-------------|------------------|
| O01 | Origen exchange | +5 | Wallet fondeada desde un exchange conocido (Coinbase, Binance, Kraken, OKX, Crypto.com, Gate.io) | Cualquier hop (max 2 hops), `is_exchange == True` |
| O02 | Fondeo reciente | +10 | Wallet fondeada recientemente | `3 ≤ días_desde_fondeo < 7` |
| O03 | Fondeo muy reciente | +5 | Wallet fondeada hace muy poco | `días_desde_fondeo < 3` |

**Exclusión mutua:** O02/O03 (se queda el de mayor puntaje).

**Implementación:** `src/analysis/wallet_analyzer.py` — Solo se ejecuta si los checks básicos de fase 1 suman ≥ 30 puntos (`_MIN_BASIC_SCORE_FOR_FUNDING`). Usa `chain.get_funding_sources(address, max_hops=2)`.

---

### 1.3 Behavior Filters (B) — 18 filtros

| ID | Nombre | Puntos | Qué detecta | Umbrales exactos |
|----|--------|--------|-------------|------------------|
| B01 | Acumulación goteo | +20 | Patrón de compra gradual tipo "drip" | `≥5 compras en ventana 24-72h` (mínimo 24h de spread) |
| B05 | Solo market orders | +5 | Todas las operaciones son market orders (no límite) | `all(t.is_market_order for t in trades)` |
| B06 | Tamaño creciente | +15 | Cada compra sucesiva es mayor que la anterior | `trades[i].amount < trades[i+1].amount` para todo i |
| B07 | Compra contra mercado | +20 | Compra en odds muy bajos (contra el consenso) | `price < 0.20` |
| B14 | Primera compra grande | +15 | Primera operación en Polymarket es grande | `primera_compra ≥ $5,000` |
| B16 | Acumulación rápida | +20 | Múltiples compras concentradas en poco tiempo | `≥3 trades en < 4 horas` |
| B17 | Horario bajo | +10 | Trading en horas de baja actividad | `2:00 ≤ hora UTC < 6:00` |
| B18a | Acumulación moderada | +15 | Acumulación en rango moderado | `$2,000 ≤ total < $3,500` |
| B18b | Acumulación significativa | +25 | Acumulación significativa | `$3,500 ≤ total < $5,000` |
| B18c | Acumulación fuerte | +35 | Acumulación fuerte | `$5,000 ≤ total < $10,000` |
| B18d | Acumulación muy fuerte | +50 | Acumulación muy grande | `total ≥ $10,000` |
| B18e | Sin impacto en precio | +15 | Acumulación sin mover el precio (stealth) | `total ≥ $2,000 AND |odds_move| < 5%` |
| B19a | Entrada grande | +20 | Whale entry: transacción individual grande | `$5,000 ≤ single_tx < $10,000` |
| B19b | Entrada muy grande | +30 | Whale entry: transacción muy grande | `$10,000 ≤ single_tx < $50,000` |
| B19c | Entrada masiva | +40 | Whale entry: transacción masiva | `single_tx ≥ $50,000` |
| B20 | Vieja nueva en PM | +20 | Wallet antigua que empieza a operar en Polymarket | `wallet_age > 180 días AND actividad_pm < 7 días` |
| B23a | Posición significativa | +15 | Posición es parte significativa del balance | `20% ≤ position_ratio < 50%` (min $50 balance y posición) |
| B23b | Posición dominante | +30 | Posición es parte dominante del balance | `position_ratio ≥ 50%` (cap: ratio > 10x se ignora) |

**Exclusión mutua:** B18a/B18b/B18c/B18d; B19a/B19b/B19c; B23a/B23b.

**Nota:** B18e es un bonus que se acumula sobre B18a-d. B19a-c activan alertas tipo `whale_entry`.

**Implementación:** `src/analysis/behavior_analyzer.py`

---

### 1.4 Confluence Filters (C) — 8 filtros

| ID | Nombre | Puntos | Qué detecta | Umbrales exactos |
|----|--------|--------|-------------|------------------|
| C01 | Confluencia básica | +25 | Múltiples wallets apuestan en la misma dirección | `≥3 wallets, misma dirección, ventana 48h` |
| C02 | Confluencia fuerte | +40 | Muchas wallets coordinadas | `≥5 wallets, misma dirección` |
| C03 | Mismo intermediario | +35 | 2+ wallets comparten fuente de fondeo | `≥2 wallets fondeadas por el mismo sender` |
| C04 | Mismo intermediario + misma dirección | +50 | Fondeo compartido + misma apuesta | `C03 + todas apuestan en la misma dirección` |
| C05 | Fondeo temporal | +30 | Fondeo coordinado desde exchange en ventana corta | `≥3 wallets fondeadas desde exchange en < 4h, misma dirección` |
| C06 | Monto similar | +15 | Wallets fondeadas con montos similares (bonus) | `montos de fondeo ±30% de la mediana` (stacks con C03/C04/C07) |
| C07 | Red de distribución | +60 | Un distribuidor fondea múltiples wallets activas | `1 sender → ≥3 wallets activas en el mercado` |
| COORD04 | Fondeo vía mixer | +50 | Fondeo a través de Tornado Cash o Railgun | `sender_address ∈ MIXER_ADDRESSES` (7 direcciones conocidas en Polygon) |

**Exclusión mutua:** C01/C02; C03/C04.

**Nota:** El sistema excluye "super-senders" (senders que fondean wallets en >3 mercados distintos) para evitar falsos positivos de exchanges/routers. Se excluyen también contratos de Polymarket y exchanges conocidos.

**Implementación:** `src/analysis/confluence_detector.py`

---

### 1.5 Market Filters (M) — 8 filtros

| ID | Nombre | Puntos | Qué detecta | Umbrales exactos |
|----|--------|--------|-------------|------------------|
| M01 | Volumen anómalo | +15 | Volumen 24h inusualmente alto respecto al promedio | `vol_24h > 2.0x promedio_7d` |
| M02 | Odds estables rotos | +20 | Odds que estaban estables de repente se mueven | `estable >48h (rango < 10%), luego movimiento > 10%` |
| M03 | Baja liquidez | +10 | Mercado con poca liquidez | `0 < liquidity < $100,000` |
| M04a | Concentración moderada | +15 | Top 3 wallets dominan el volumen | `top 3 wallets > 60% del volumen total` |
| M04b | Concentración alta | +25 | Top 3 wallets dominan fuertemente | `top 3 wallets > 80% del volumen total` |
| M05a | Deadline <72h | +10 | Mercado cierra pronto | `6h < tiempo_restante ≤ 72h` |
| M05b | Deadline <24h | +15 | Mercado cierra muy pronto | `6h < tiempo_restante ≤ 24h` |
| M05c | Deadline <6h | +25 | Mercado cierra inminentemente | `tiempo_restante ≤ 6h` |

**Exclusión mutua:** M04a/M04b; M05a/M05b/M05c.

**Nota:** M01 y M02 requieren datos históricos (market_snapshots). En los primeros días sin historial, estos filtros no disparan. Los snapshots se guardan automáticamente en cada scan.

**Implementación:** `src/analysis/market_analyzer.py`

---

### 1.6 Negative/Noise Filters (N) — 11 filtros

| ID | Nombre | Puntos | Qué detecta | Umbrales exactos |
|----|--------|--------|-------------|------------------|
| N01 | Bot | -40 | Intervalos regulares tipo bot | `std_dev de intervalos entre trades < 1.0s` (mínimo 3 trades) |
| N02 | Noticias | -20 | Ya hay noticias públicas sobre el tema | Google News RSS check, `lookback 24h` |
| N03 | Arbitraje | -100 | Wallet hace hedging entre mercados equivalentes | `YES en mercado A + NO en mercado equivalente` (kill: descarta wallet) |
| N04 | Mercados opuestos | 0 | Posición en mercado opuesto, misma dirección | `mismo sentido en ambos mercados` (flag informativo, 0 pts) |
| N05 | Copy-trading | -25 | Compra justo después de un whale conocido | `2 ≤ delay ≤ 10 minutos después de whale trade, mismo mercado+dirección` |
| N06a | Degen leve | -5 | Actividad en mercados no-políticos (leve) | `1-2 mercados no-políticos` |
| N06b | Degen moderado | -15 | Actividad en mercados no-políticos (moderada) | `3-5 mercados no-políticos` |
| N06c | Degen fuerte | -30 | Actividad en mercados no-políticos (fuerte) | `≥6 mercados no-políticos` |
| N07a | Scalper leve | -20 | Buy+sell en mismo mercado en poco tiempo | `compra + venta en < 2h, mismo mercado` |
| N07b | Scalper serial | -40 | Flips en múltiples mercados | `flips en ≥3 mercados distintos` |
| N08 | Anti-bot evasión | +25 | Intervalos irregulares pero montos uniformes (evasión de N01) | `CV de montos < 0.10, ≥4 trades, solo si N01 NO disparó` |

**Exclusión mutua:** N06a/N06b/N06c; N07a/N07b.

**Nota:** N03 es un "kill filter" — si se activa, la wallet completa se descarta del análisis. N08 es positivo (+25) porque detecta evasión deliberada de detección de bots, lo cual es señal de insider. N08 tiene categoría `behavior` (no `negative`).

**Implementación:** `src/analysis/noise_filter.py` (N01, N02, N05, N06, N07, N08), `src/analysis/arbitrage_filter.py` (N03, N04).

---

## 2. Filtros No Implementados

Los siguientes IDs del diseño original no tienen implementación:

| IDs faltantes | Categoría | Razón |
|--------------|-----------|-------|
| W06, W07, W08, W10 | Wallet | Consolidados en los 7 filtros W existentes. W06-W08 (patrones de reputación) y W10 (Polymarket history) se cubrieron parcialmente con W09 y B20 |
| B02, B03, B04 | Behavior | Fusionados en B01 (drip) y B16 (rápido). Los patrones intermedios no añadían valor diferencial |
| B08-B13, B15 | Behavior | B08-B10 (patrones de timing avanzados) cubiertos por B17. B11-B13 (patrones de spread) no eran fiables con datos CLOB. B15 (segundo mercado) cubierto por B20 |
| B21, B22 | Behavior | B21 (market making activity) y B22 (portfolio analysis) descartados por complejidad vs. valor |
| O04-O08 | Origin | O04 (multiple exchange hops) cubierto por COORD04 (mixer). O05-O08 (patrones de fondeo avanzados) cubiertos por C03-C07 |

**Total:** 55 filtros implementados de ~70 diseñados originalmente. Los filtros eliminados fueron consolidados en filtros existentes o descartados por baja relación señal/ruido.

---

## 3. Tabla Resumen

| filter_id | name | points | category | mutex_group | status |
|-----------|------|--------|----------|-------------|--------|
| W01 | Wallet muy nueva | +25 | wallet | W01/W02/W03 | Implementado |
| W02 | Wallet nueva | +20 | wallet | W01/W02/W03 | Implementado |
| W03 | Wallet reciente | +15 | wallet | W01/W02/W03 | Implementado |
| W04 | Solo 1 mercado | +10 | wallet | W04/W05 | Implementado |
| W05 | Solo 2-3 mercados | +15 | wallet | W04/W05 | Implementado |
| W09 | Primera tx = Polymarket | +5 | wallet | — | Implementado |
| W11 | Balance redondo | +3 | wallet | — | Implementado |
| O01 | Origen exchange | +5 | origin | — | Implementado |
| O02 | Fondeo reciente | +10 | origin | O02/O03 | Implementado |
| O03 | Fondeo muy reciente | +5 | origin | O02/O03 | Implementado |
| B01 | Acumulación goteo | +20 | behavior | — | Implementado |
| B05 | Solo market orders | +5 | behavior | — | Implementado |
| B06 | Tamaño creciente | +15 | behavior | — | Implementado |
| B07 | Compra contra mercado | +20 | behavior | — | Implementado |
| B14 | Primera compra grande | +15 | behavior | — | Implementado |
| B16 | Acumulación rápida | +20 | behavior | — | Implementado |
| B17 | Horario bajo | +10 | behavior | — | Implementado |
| B18a | Acumulación moderada | +15 | behavior | B18a/b/c/d | Implementado |
| B18b | Acumulación significativa | +25 | behavior | B18a/b/c/d | Implementado |
| B18c | Acumulación fuerte | +35 | behavior | B18a/b/c/d | Implementado |
| B18d | Acumulación muy fuerte | +50 | behavior | B18a/b/c/d | Implementado |
| B18e | Sin impacto en precio | +15 | behavior | — (bonus) | Implementado |
| B19a | Entrada grande | +20 | behavior | B19a/b/c | Implementado |
| B19b | Entrada muy grande | +30 | behavior | B19a/b/c | Implementado |
| B19c | Entrada masiva | +40 | behavior | B19a/b/c | Implementado |
| B20 | Vieja nueva en PM | +20 | behavior | — | Implementado |
| B23a | Posición significativa | +15 | behavior | B23a/B23b | Implementado |
| B23b | Posición dominante | +30 | behavior | B23a/B23b | Implementado |
| C01 | Confluencia básica | +25 | confluence | C01/C02 | Implementado |
| C02 | Confluencia fuerte | +40 | confluence | C01/C02 | Implementado |
| C03 | Mismo intermediario | +35 | confluence | C03/C04 | Implementado |
| C04 | Mismo intermediario + dir | +50 | confluence | C03/C04 | Implementado |
| C05 | Fondeo temporal | +30 | confluence | — | Implementado |
| C06 | Monto similar | +15 | confluence | — (bonus) | Implementado |
| C07 | Red de distribución | +60 | confluence | — | Implementado |
| COORD04 | Fondeo vía mixer | +50 | confluence | — | Implementado |
| M01 | Volumen anómalo | +15 | market | — | Implementado |
| M02 | Odds estables rotos | +20 | market | — | Implementado |
| M03 | Baja liquidez | +10 | market | — | Implementado |
| M04a | Concentración moderada | +15 | market | M04a/M04b | Implementado |
| M04b | Concentración alta | +25 | market | M04a/M04b | Implementado |
| M05a | Deadline <72h | +10 | market | M05a/b/c | Implementado |
| M05b | Deadline <24h | +15 | market | M05a/b/c | Implementado |
| M05c | Deadline <6h | +25 | market | M05a/b/c | Implementado |
| N01 | Bot | -40 | negative | — | Implementado |
| N02 | Noticias | -20 | negative | — | Implementado |
| N03 | Arbitraje | -100 | negative | — | Implementado |
| N04 | Mercados opuestos | 0 | negative | — | Implementado |
| N05 | Copy-trading | -25 | negative | — | Implementado |
| N06a | Degen leve | -5 | negative | N06a/b/c | Implementado |
| N06b | Degen moderado | -15 | negative | N06a/b/c | Implementado |
| N06c | Degen fuerte | -30 | negative | N06a/b/c | Implementado |
| N07a | Scalper leve | -20 | negative | N07a/N07b | Implementado |
| N07b | Scalper serial | -40 | negative | N07a/N07b | Implementado |
| N08 | Anti-bot evasión | +25 | behavior | — | Implementado |

---

## 4. Flujo Completo del Scan

Entry point: `python -m src.main` → `run_scan()`

### Paso 1: Conexión de servicios
- Inicializa: `SupabaseClient`, `PolymarketClient`, `BlockchainClient`, `NewsChecker`
- Inicializa analizadores: `WalletAnalyzer`, `BehaviorAnalyzer`, `MarketAnalyzer`, `NoiseFilter`, `ArbitrageFilter`, `ConfluenceDetector`
- Inicializa publishers: `TwitterBot`, `TelegramBot`
- Verifica kill switch: `db.is_scan_enabled()` (tabla `system_config`)

### Paso 2: Fetch de mercados activos
- Llama a `PolymarketClient.get_active_markets()` (Gamma API)
- Categorías: `["politics", "economics", "geopolitics"]`
- Filtros de mercado (`_filter_markets`):
  - `volume_24h ≥ $1,000`
  - `0.05 ≤ odds ≤ 0.55`
  - Categoría en `MARKET_RELEVANT_CATEGORIES` (Politics, Economics, Corporate, Crypto, Geopolitics)
  - No contiene términos blacklisted (deportes, celebridades, clima, crypto prices, etc.)
- Ordena por volumen desc, cap a `100 mercados`

### Paso 3-7: Pipeline por mercado

Para cada mercado (con timeout de 45s por mercado, 480s global):

#### 3a. Fetch trades
- `PolymarketClient.get_recent_trades(market_id, minutes=35, min_amount=$100)`
- Fuente: CLOB API de Polymarket

#### 3b. Agrupar por wallet
- Agrupa trades por `wallet_address`
- Ordena wallets por volumen total desc
- Mantiene top 10 wallets (`MAX_WALLETS_PER_MARKET`)

#### 3c. Análisis por wallet

Para cada wallet (de las top 10):

1. **Accumulation check:** Calcula `AccumulationWindow` — si `total_amount < $350`, descarta wallet
2. **Wallet Analyzer (W+O):** Ejecuta filtros W01-W11, O01-O03
3. **Behavior Analyzer (B):** Ejecuta filtros B01-B23b
4. **Noise Filter (N):** Ejecuta N01, N02, N05, N06a-c, N07a-b, N08
5. **Arbitrage Filter (N03/N04):** Si N03 dispara (-100), descarta wallet completa

#### 3d. Market-level analysis
- Ejecuta filtros M01-M05c sobre el mercado
- Guarda snapshot para histórico (M01/M02 futuro)

#### 3e. Confluence detection
- Ejecuta filtros C01-C07, COORD04
- Excluye super-senders (>3 mercados), contratos Polymarket, exchanges conocidos

#### 3f. Scoring
- Combina: **mejor wallet** (por raw score) + filtros de mercado + filtros de confluencia
- NO suma todas las wallets (evita inflación)
- Motor de scoring (`scoring.py`):
  1. Exclusión mutua (mantiene mayor puntaje por grupo)
  2. Suma raw points (floor 0)
  3. Multiplier logarítmico por monto
  4. Multiplier diversidad (sniper/shotgun)
  5. `score_final = min(400, raw * multiplier)`
  6. Asigna estrellas por umbral
  7. Valida estrellas (puede downgrade)

#### 3g. Odds range check
- Rango normal: `0.05 ≤ odds ≤ 0.55`
- Rango extendido (si score ≥ 90): `0.05 ≤ odds ≤ 0.70`
- Si odds fuera de rango, descarta alerta

#### 3h. Genera candidato de alerta

### Paso 7b: Deduplicación intra-scan
- Compara `market_question` de todas las alertas candidatas
- Usa Jaccard similarity (tokenización sin stop words ni fechas/meses)
- Umbral: `similarity > 0.6` → agrupa, mantiene la de mayor score
- Las duplicadas se insertan en DB pero NO se publican

### Paso 8: Save + Publish

Para cada alerta:

1. **8a. Within-scan dedup:** Si `deduplicated == True` → inserta en DB, skip publish
2. **8b. Cross-scan dedup:** Busca alerta existente con mismo `market_id + direction + primary_wallet` en últimas 24h → actualiza campos, skip publish
3. **8b2. High-star consolidation:** Busca alerta existente 4+★ en mismo mercado/dirección (últimas 48h) → merge nuevas wallets, actualiza totales, envía mensaje UPDATE a Telegram
4. **8c. Nueva alerta:** Inserta en DB + publica según estrellas:
   - `4+★` → Telegram (canal privado + público) + X/Twitter
   - `3★` → Solo X/Twitter
   - `1-2★` → Solo DB + dashboard
   - `0★` → Solo DB

### Paso 8b: Sell monitoring
- `SellDetector.check_open_positions()` — revisa posiciones abiertas
- Detecta ventas individuales o coordinadas (2+ wallets en <4h)
- Envía notificación a Telegram (no afecta scoring)

### Paso 9: Registro del scan
- Guarda `Scan` en tabla `scans` con métricas:
  - `markets_scanned`, `wallets_analyzed`, `alerts_generated`
  - `alerts_published_x`, `alerts_published_tg`
  - `duration_seconds`, `status`, `errors`

---

## 5. Umbrales de Scoring

### 5.1 Score → Estrellas (NEW_STAR_THRESHOLDS)

| Score mínimo | Estrellas |
|-------------|-----------|
| 220 | 5★ |
| 150 | 4★ |
| 100 | 3★ |
| 70 | 2★ |
| 40 | 1★ |
| <40 | 0★ |

Score máximo (cap): **400**

### 5.2 Multiplicador por monto (logarítmico)

Fórmula: `multiplier = 0.18 * ln(total_usd) - 0.37`, clamped a `[0.3, 2.0]`

| Monto total | Multiplicador aprox. |
|-------------|---------------------|
| $100 | 0.46 |
| $500 | 0.75 |
| $1,000 | 0.87 |
| $5,000 | 1.17 |
| $10,000 | 1.29 |
| $50,000 | 1.57 |
| $100,000 | 1.70 |

### 5.3 Multiplicador de diversidad (sniper vs. shotgun)

| Mercados distintos | Tipo | Multiplicador |
|-------------------|------|---------------|
| ≤3 | Sniper (enfocado) | x1.2 |
| 4-9 | Normal | x1.0 |
| 10-19 | Shotgun (disperso) | x0.7 |
| ≥20 | Super shotgun | x0.5 |

### 5.4 Validación de estrellas (puede downgrade)

| Estrellas | Requisitos mínimos |
|-----------|-------------------|
| 3★ | ≥2 categorías scoring |
| 4★ | ≥2 categorías + monto ≥ $5,000 |
| 5★ | ≥3 categorías + monto ≥ $10,000 + requiere COORDINATION |

**Categorías de scoring (4):**
- **ACCUMULATION** ← wallet + origin filters
- **COORDINATION** ← behavior + confluence filters
- **TIMING** ← market filters
- **MARKET** ← negative filters

Si los requisitos no se cumplen, la estrella baja progresivamente hasta que se cumplen (o llega a 2★).

### 5.5 Publicación por estrellas

| Estrellas | DB | Dashboard | Telegram | X/Twitter |
|-----------|-----|-----------|----------|-----------|
| 0★ | Si | No | No | No |
| 1★ | Si | Si | No | No |
| 2★ | Si | Si | No | No |
| 3★ | Si | Si | No | Si |
| 4★ | Si | Si | Si | Si |
| 5★ | Si | Si | Si | Si |

**Nota:** La publicación en Telegram está gateada en `main.py` a `star >= 4` (código), independientemente de la tabla `STAR_PUBLISH_MAP` en config.

---

## 6. Configuración Actual

### 6.1 Scan & Ingestión

| Parámetro | Valor | Variable |
|-----------|-------|----------|
| Frecuencia de scan | Cada 3 horas | `cron: 0 */3 * * *` (scan.yml) |
| Lookback por scan | 35 minutos | `SCAN_LOOKBACK_MINUTES = 35` |
| Timeout global | 480s (8 min) | `SCAN_TIMEOUT_SECONDS = 480` |
| Timeout por mercado | 45s | `MARKET_TIMEOUT_SECONDS = 45` |
| Min tx amount | $100 | `MIN_TX_AMOUNT = 100` |
| Min acumulado | $350 | `MIN_ACCUMULATED_AMOUNT = 350` |
| Ventana acumulación | 72h | `ACCUMULATION_WINDOW_HOURS = 72` |
| Max wallets/mercado | 10 | `MAX_WALLETS_PER_MARKET = 10` |
| Cap mercados/scan | 100 | `MARKET_SCAN_CAP = 100` |
| Cross-scan dedup | 24h | `CROSS_SCAN_DEDUP_HOURS = 24` |

### 6.2 Rango de Odds

| Parámetro | Valor |
|-----------|-------|
| Odds mínimo | 0.05 (5%) |
| Odds máximo | 0.55 (55%) |
| Odds máximo extendido | 0.70 (70%) — si score ≥ 90 |

### 6.3 Filtros de mercado

| Parámetro | Valor |
|-----------|-------|
| Volumen mínimo 24h | $1,000 |
| Categorías aceptadas | Politics, Economics, Corporate, Crypto, Geopolitics |
| Términos blacklisted | tweet, twitter, nfl, nba, weather, dating, crypto prices, etc. (50+ términos) |

### 6.4 GitHub Actions (Workflows)

| Workflow | Frecuencia | Entry point |
|----------|-----------|-------------|
| `scan.yml` | `0 */3 * * *` (cada 3h) | `python -m src.main` |
| `tracker.yml` | `0 1,7,13,19 * * *` (4x/día) | `python -m src.tracking.run_tracker` |
| `resolver.yml` | `0 8 * * *` (diario 8am UTC) | `python -m src.tracking.run_resolver` |
| `dashboard.yml` | `15 * * * *` (cada hora, min 15) | `python -m src.dashboard.generate_dashboard` |
| `monthly_report.yml` | `0 8 1 * *` (1ro de cada mes) | `python -m src.reports.monthly` |
| `weekly_report.yml` | Lunes 8am UTC | `python -m src.reports.weekly` |
| `check_resolutions.yml` | `0 */6 * * *` (cada 6h) | `python -m src.tracking.check_resolutions` |

### 6.5 Exchanges conocidos (O01)

| Exchange | Direcciones Polygon |
|----------|-------------------|
| Coinbase | 3 hot wallets |
| Binance | 3 hot wallets |
| Kraken | 1 hot wallet |
| OKX | 1 hot wallet |
| Crypto.com | 1 hot wallet |
| Gate.io | 1 hot wallet |

### 6.6 Mixer addresses (COORD04)

| Protocolo | Direcciones |
|-----------|------------|
| Tornado Cash (Polygon) | 5 direcciones |
| Railgun (Polygon) | 2 direcciones |

### 6.7 Servicios externos

| Servicio | Uso |
|----------|-----|
| Supabase | Base de datos (alerts, markets, wallets, scans, etc.) |
| Alchemy | Polygon RPC (edad wallet, balance, funding sources) |
| Polymarket Gamma API | Fetch mercados activos |
| Polymarket CLOB API | Fetch trades recientes |
| Google News RSS | N02 (noticias públicas) |
| Twitter/X API | Publicación de alertas 3+★ |
| Telegram Bot API | Publicación de alertas 4+★, sells, updates |

---

## 7. Sistemas Auxiliares

### 7.1 Consolidación de alertas
- Evita publicar múltiples alertas para el mismo mercado
- Si existe alerta 4+★ del mismo mercado/dirección en últimas 48h:
  - Merge nuevas wallets al JSON existente
  - Actualiza `total_amount`, `confluence_count`, `updated_count`
  - Envía mensaje UPDATE a Telegram
- Implementado en `_try_consolidate()` (`main.py`)

### 7.2 Sell monitoring
- Detecta ventas en posiciones previamente alertadas
- Tipos: individual (1 wallet) y coordinada (2+ wallets en <4h)
- Solo notificación, no afecta scoring
- Implementado en `src/analysis/sell_detector.py`

### 7.3 Whale monitor
- Monitorea wallets de alertas 4+★ para actividad posterior
- Detecta: FULL_EXIT (≥90%), PARTIAL_EXIT (≥30%), ADDITIONAL_BUY, NEW_MARKET
- Se ejecuta en `run_tracker` (4x/día)
- Implementado en `src/tracking/whale_monitor.py`

### 7.4 Dashboard
- HTML estático generado en `docs/index.html`
- Servido via GitHub Pages
- Incluye: tabla de alertas, gráficos (accuracy, P&L, filtros), filtros interactivos
- Regenerado cada hora via GitHub Actions
- Implementado en `src/dashboard/generate_dashboard.py`

### 7.5 Deduplicación multinivel

| Nivel | Dónde | Mecanismo | Resultado |
|-------|-------|-----------|-----------|
| 1. Intra-scan | `_deduplicate_alerts()` | Jaccard similarity >0.6 en `market_question` | Inserta en DB, no publica |
| 2. Cross-scan | `_check_cross_scan_duplicate()` | Mismo market_id + direction + primary wallet en 24h | Actualiza existente, no publica |
| 3. High-star consolidation | `_try_consolidate()` | Alerta 4+★ existente, mismo mercado/dirección en 48h | Merge wallets, envía UPDATE |
