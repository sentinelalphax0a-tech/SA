# ESTADO ACTUAL v2 — Sentinel Alpha

> Generado: 2026-02-15
> Commit base: `93ca97d` (main)
> Tests: 494 passing

---

## 1. Resumen de cambios vs versión anterior

### 1.1 Filtros modificados, añadidos o eliminados

| Filtro | Antes | Después | Tipo de cambio |
|--------|-------|---------|----------------|
| **B14** | Siempre evaluado | Suprimido si B19 disparó (`b19_fired=True`) | Mutual exclusion nueva |
| **B18a-d** | Disparaba con 1 trade | Requiere `≥2 trades` (single buys no son acumulación) | Condición añadida |
| **B18 bonus** | Sin bonus por trade count | +5 pts si 3-4 trades, +10 pts si 5+ trades | Bonus añadido |
| **B18e** | `Sin impacto en precio` (+15) | **ELIMINADO** — reemplazado por B26a/b | Eliminado → B26 |
| **B25a** | No existía | `Convicción extrema` (+25) — YES@<0.10 o NO eff>0.90 | Nuevo |
| **B25b** | No existía | `Convicción alta` (+15) — YES@<0.20 o NO eff>0.80 | Nuevo |
| **B25c** | No existía | `Convicción moderada` (+5) — YES@<0.35 o NO eff>0.20 | Nuevo |
| **B26a** | No existía | `Stealth whale` (+20) — move<1%, total>$5K | Nuevo (reemplaza B18e) |
| **B26b** | No existía | `Low impact` (+10) — move<3%, total>$3K | Nuevo |
| **B27a** | No existía | `Diamond hands 48h` (+15) — **DISABLED** | Nuevo (disabled) |
| **B27b** | No existía | `Diamond hands 72h+` (+20) — **DISABLED** | Nuevo (disabled) |
| **B28a** | No existía | `All-in extremo` (+25) — ratio≥90% | Nuevo (excl. B23) |
| **B28b** | No existía | `All-in fuerte` (+20) — ratio 70-90% | Nuevo (excl. B23) |
| **B30a** | No existía | `First mover` (+20) — **DISABLED** | Nuevo (disabled) |
| **B30b** | No existía | `Early mover top 3` (+10) — **DISABLED** | Nuevo (disabled) |
| **B30c** | No existía | `Early mover top 5` (+5) — **DISABLED** | Nuevo (disabled) |
| **C01** | `Confluencia 3+ wallets` (+10) | Mismo pero ahora Layer 1 del sistema por capas | Rediseñado |
| **C02** | `Confluencia 5+ wallets` (+15) | Mismo pero ahora Layer 1, mutuamente exclusivo con C01 | Rediseñado |
| **C03** | `Fondeo compartido` (genérico) | **ELIMINADO** → reemplazado por C03a/b/c/d | Eliminado → C03x |
| **C03a** | No existía | `Origen exchange compartido` (+5) — Layer 2 | Nuevo |
| **C03b** | No existía | `Origen bridge compartido` (+20) — Layer 2 | Nuevo |
| **C03c** | No existía | `Origen mixer compartido` (+30) — Layer 2 | Nuevo |
| **C03d** | No existía | `Mismo padre directo` (+30) — Layer 2 | Nuevo |
| **C04** | Existía como filtro independiente | **ELIMINADO** — absorbido por C03a-d + C05 | Eliminado |
| **C05** | `Fondeo temporal` (+10) | Mismo, ahora Layer 3 bonus aditivo | Rediseñado |
| **C06** | `Monto similar` (+10) | Mismo, ahora Layer 3 bonus aditivo | Rediseñado |
| **C07** | `Red de distribución` (+30) | Mismo, ahora Layer 4 independiente | Rediseñado |
| **N09a** | No existía | `Apuesta obvia extrema` (-40) — eff odds>0.90 | Nuevo (excl. B25) |
| **N09b** | No existía | `Apuesta obvia` (-25) — eff odds>0.85 | Nuevo (excl. B25) |
| **N10a** | No existía | `Horizonte lejano` (-10) — >30 días | Nuevo |
| **N10b** | No existía | `Horizonte muy lejano` (-20) — >60 días | Nuevo |
| **N10c** | No existía | `Horizonte extremo` (-30) — >90 días | Nuevo |
| **KNOWN_BRIDGES** | No existía | 7 bridges configurados en `config.py` | Nuevo |
| **Bybit** | No existía en KNOWN_EXCHANGES | Añadido: `0xf89d...eaa40` | Nuevo |
| **Telegram format** | Sin indicadores de dirección | `[YES]`/`[NO]` por wallet + `⚠️ [↕]` para dirección opuesta | Mejorado |
| **Funding threshold** | wallet_analyzer solo fetcheaba funding si basic_score≥30 | main.py fuerza fetch para TODOS los wallets cuando hay 3+ en misma dirección | Bug fix |

### 1.2 Exclusiones mutuas actualizadas

| Grupo | Filtros | Enforcement |
|-------|---------|-------------|
| B14 ↔ B19 | B14 suprimido si B19 disparó | `behavior_analyzer.py:132-134` |
| B28 ↔ B23 | B28 evaluado primero; si dispara, B23 no se evalúa | `behavior_analyzer.py:137-140` |
| B25 ↔ N09 | B25 evaluado primero; si dispara, N09 no se evalúa | `behavior_analyzer.py:142-146` |
| Scoring safety net | 17 grupos en `MUTUALLY_EXCLUSIVE_GROUPS` | `scoring.py:85-108` |

---

## 2. Todos los filtros implementados

### 2.1 Wallet filters (W) — 7 filtros

| ID | Nombre | Puntos | Condición | Notas |
|----|--------|--------|-----------|-------|
| W01 | Wallet muy nueva | +25 | age < 7 días | Mutex W01/W02/W03 |
| W02 | Wallet nueva | +20 | age < 14 días | |
| W03 | Wallet reciente | +15 | age < 30 días | |
| W04 | Solo 1 mercado | +10 | total_markets == 1 | Mutex W04/W05 |
| W05 | Solo 2-3 mercados | +15 | 2 ≤ total_markets ≤ 3 | |
| W09 | Primera tx = Polymarket | +5 | is_first_tx_pm | |
| W11 | Balance redondo | +3 | balance ≈ $5K/$10K/$50K (±1%) | |

### 2.2 Origin filters (O) — 3 filtros

| ID | Nombre | Puntos | Condición | Notas |
|----|--------|--------|-----------|-------|
| O01 | Origen exchange | +5 | Funding desde exchange conocido | 1-2 hops |
| O02 | Fondeo reciente | +10 | Funding < 7 días | Mutex O02/O03 |
| O03 | Fondeo muy reciente | +5 | Funding < 3 días | |

### 2.3 Behavior filters (B) — 30 filtros (24 activos, 5 disabled, 1 eliminado)

| ID | Nombre | Puntos | Condición | Notas |
|----|--------|--------|-----------|-------|
| B01 | Acumulación goteo | +20 | 5+ buys en 24-72h | Spread ≥ 24h |
| B05 | Solo market orders | +5 | Todos los trades son market orders | |
| B06 | Tamaño creciente | +15 | Cada buy > anterior | ≥ 2 trades |
| B07 | Compra contra mercado | +20 | price < 0.20 | |
| B14 | Primera compra grande | +15 | first buy ≥ $5K | **Suprimido si B19 disparó** |
| B16 | Acumulación rápida | +20 | 3+ trades en < 4h | |
| B17 | Horario bajo | +10 | 2-6 AM UTC | |
| B18a | Acumulación moderada | +15 | $2K-$3.5K, ≥2 trades | Mutex B18a-d; +5/+10 bonus |
| B18b | Acumulación significativa | +25 | $3.5K-$5K, ≥2 trades | |
| B18c | Acumulación fuerte | +35 | $5K-$10K, ≥2 trades | |
| B18d | Acumulación muy fuerte | +50 | ≥$10K, ≥2 trades | |
| ~~B18e~~ | ~~Sin impacto en precio~~ | ~~+15~~ | — | **ELIMINADO → B26** |
| B19a | Entrada grande | +20 | single tx ≥ $5K | Mutex B19a-c |
| B19b | Entrada muy grande | +30 | single tx ≥ $10K | |
| B19c | Entrada masiva | +40 | single tx ≥ $50K | Telegram-only whale alert |
| B20 | Vieja nueva en PM | +20 | age > 180d, PM < 7d | |
| B23a | Posición significativa | +15 | ratio 20-50% del balance | Mutex B23a/b; **suprimido si B28 disparó** |
| B23b | Posición dominante | +30 | ratio ≥ 50% del balance | |
| B25a | Convicción extrema | +25 | YES@<0.10 o NO eff>0.90 | Mutex B25a-c; **excluye N09** |
| B25b | Convicción alta | +15 | YES@<0.20 o NO eff>0.80 | |
| B25c | Convicción moderada | +5 | YES@<0.35 o NO eff 0.20-0.80 | |
| B26a | Stealth whale | +20 | move <1%, total >$5K, ≥2 trades | Mutex B26a/b |
| B26b | Low impact | +10 | move <3%, total >$3K, ≥2 trades | |
| B27a | Diamond hands 48h | +15 | 24-48h hold, odds +5% | **DISABLED** (ENABLE_B27=False) |
| B27b | Diamond hands 72h+ | +20 | 72h+ hold, odds +10% | **DISABLED** |
| B28a | All-in extremo | +25 | ratio ≥ 90% del balance | Mutex B28a/b; **suprime B23** |
| B28b | All-in fuerte | +20 | ratio 70-90% del balance | |
| B30a | First mover | +20 | 1er wallet >$1K en dirección | **DISABLED** (ENABLE_B30=False) |
| B30b | Early mover top 3 | +10 | Entre los 3 primeros | **DISABLED** |
| B30c | Early mover top 5 | +5 | Entre los 5 primeros | **DISABLED** |

### 2.4 Confluence filters (C) — 8 filtros (sistema por capas)

| ID | Nombre | Puntos | Layer | Condición | Notas |
|----|--------|--------|-------|-----------|-------|
| C01 | Confluencia básica | +10 | 1 Direction | 3+ wallets misma dirección | Mutex C01/C02 |
| C02 | Confluencia fuerte | +15 | 1 Direction | 5+ wallets misma dirección | |
| C03a | Origen exchange compartido | +5 | 2 Origin | 2+ wallets desde mismo exchange | Aditivo |
| C03b | Origen bridge compartido | +20 | 2 Origin | 2+ wallets desde mismo bridge | Aditivo |
| C03c | Origen mixer compartido | +30 | 2 Origin | 2+ wallets desde mismo mixer | Aditivo |
| C03d | Mismo padre directo | +30 | 2 Origin | 2+ wallets desde mismo sender (no exch/bridge/mixer) | Aditivo |
| C05 | Fondeo temporal | +10 | 3 Bonus | 3+ funded desde exchange < 4h, misma dir | Aditivo |
| C06 | Monto similar | +10 | 3 Bonus | Wallets del mismo sender con montos ±30% | Aditivo |
| C07 | Red de distribución | +30 | 4 Distribution | 1 sender → 3+ wallets activas | |

### 2.5 Coordination extra — 1 filtro

| ID | Nombre | Puntos | Condición | Notas |
|----|--------|--------|-----------|-------|
| COORD04 | Fondeo via mixer | +50 | Funding desde Tornado Cash / Railgun | wallet_analyzer.py |

### 2.6 Market filters (M) — 8 filtros

| ID | Nombre | Puntos | Condición | Notas |
|----|--------|--------|-----------|-------|
| M01 | Volumen anómalo | +15 | vol_24h > 2x avg_7d | Necesita historial |
| M02 | Odds estables rotos | +20 | Stable >48h, move >10% | Necesita historial |
| M03 | Baja liquidez | +10 | liquidity < $100K | |
| M04a | Concentración moderada | +15 | Top 3 wallets > 60% vol | Mutex M04a/b |
| M04b | Concentración alta | +25 | Top 3 wallets > 80% vol | |
| M05a | Deadline <72h | +10 | resolution < 72h | Mutex M05a/b/c |
| M05b | Deadline <24h | +15 | resolution < 24h | |
| M05c | Deadline <6h | +25 | resolution < 6h | |

### 2.7 Negative filters (N) — 16 filtros

| ID | Nombre | Puntos | Condición | Notas |
|----|--------|--------|-----------|-------|
| N01 | Bot | -40 | interval std_dev < 1s | ≥3 trades |
| N02 | Noticias | -20 | Google News RSS match | |
| N03 | Arbitraje | -100 | YES en mercado A + NO en equivalente | **Kill alert** |
| N04 | Mercados opuestos | 0 | Misma dirección en mercados opuestos | Flag only |
| N05 | Copy-trading | -25 | Trade 2-10 min después de whale | |
| N06a | Degen leve | -5 | 1-2 non-political markets | Mutex N06a-c |
| N06b | Degen moderado | -15 | 3-5 non-political markets | |
| N06c | Degen fuerte | -30 | 6+ non-political markets | |
| N07a | Scalper leve | -20 | buy+sell <2h mismo mercado | Mutex N07a/b |
| N07b | Scalper serial | -40 | flips en 3+ mercados | |
| N08 | Anti-bot evasión | +25 | Timing irregular, montos uniformes (CV<10%) | Solo si N01 no disparó; cat: behavior |
| N09a | Apuesta obvia extrema | -40 | Effective odds >0.90 en dirección wallet | Mutex N09a/b; **excluido si B25 disparó** |
| N09b | Apuesta obvia | -25 | Effective odds >0.85 | |
| N10a | Horizonte lejano | -10 | Resolution >30 días | Mutex N10a-c |
| N10b | Horizonte muy lejano | -20 | Resolution >60 días | |
| N10c | Horizonte extremo | -30 | Resolution >90 días | |

---

## 3. Tabla resumen completa

| filter_id | name | points | category | mutex group | status |
|-----------|------|--------|----------|-------------|--------|
| W01 | Wallet muy nueva | +25 | wallet | W01/W02/W03 | Activo |
| W02 | Wallet nueva | +20 | wallet | W01/W02/W03 | Activo |
| W03 | Wallet reciente | +15 | wallet | W01/W02/W03 | Activo |
| W04 | Solo 1 mercado | +10 | wallet | W04/W05 | Activo |
| W05 | Solo 2-3 mercados | +15 | wallet | W04/W05 | Activo |
| W09 | Primera tx = Polymarket | +5 | wallet | — | Activo |
| W11 | Balance redondo | +3 | wallet | — | Activo |
| O01 | Origen exchange | +5 | origin | — | Activo |
| O02 | Fondeo reciente | +10 | origin | O02/O03* | Activo |
| O03 | Fondeo muy reciente | +5 | origin | O02/O03* | Activo |
| B01 | Acumulación goteo | +20 | behavior | — | Activo |
| B05 | Solo market orders | +5 | behavior | — | Activo |
| B06 | Tamaño creciente | +15 | behavior | — | Activo |
| B07 | Compra contra mercado | +20 | behavior | — | Activo |
| B14 | Primera compra grande | +15 | behavior | B14↔B19 | Activo |
| B16 | Acumulación rápida | +20 | behavior | — | Activo |
| B17 | Horario bajo | +10 | behavior | — | Activo |
| B18a | Acumulación moderada | +15 | behavior | B18a/B18b/B18c/B18d | Activo |
| B18b | Acumulación significativa | +25 | behavior | B18a/B18b/B18c/B18d | Activo |
| B18c | Acumulación fuerte | +35 | behavior | B18a/B18b/B18c/B18d | Activo |
| B18d | Acumulación muy fuerte | +50 | behavior | B18a/B18b/B18c/B18d | Activo |
| B18e | Sin impacto en precio | +15 | behavior | — | **Eliminado** |
| B19a | Entrada grande | +20 | behavior | B19a/B19b/B19c | Activo |
| B19b | Entrada muy grande | +30 | behavior | B19a/B19b/B19c | Activo |
| B19c | Entrada masiva | +40 | behavior | B19a/B19b/B19c | Activo |
| B20 | Vieja nueva en PM | +20 | behavior | — | Activo |
| B23a | Posición significativa | +15 | behavior | B23a/B23b; B28↔B23 | Activo |
| B23b | Posición dominante | +30 | behavior | B23a/B23b; B28↔B23 | Activo |
| B25a | Convicción extrema | +25 | behavior | B25a/B25b/B25c; B25↔N09 | Activo |
| B25b | Convicción alta | +15 | behavior | B25a/B25b/B25c; B25↔N09 | Activo |
| B25c | Convicción moderada | +5 | behavior | B25a/B25b/B25c; B25↔N09 | Activo |
| B26a | Stealth whale | +20 | behavior | B26a/B26b | Activo |
| B26b | Low impact | +10 | behavior | B26a/B26b | Activo |
| B27a | Diamond hands 48h | +15 | behavior | B27a/B27b | **Disabled** |
| B27b | Diamond hands 72h+ | +20 | behavior | B27a/B27b | **Disabled** |
| B28a | All-in extremo | +25 | behavior | B28a/B28b; B28↔B23 | Activo |
| B28b | All-in fuerte | +20 | behavior | B28a/B28b; B28↔B23 | Activo |
| B30a | First mover | +20 | behavior | B30a/B30b/B30c | **Disabled** |
| B30b | Early mover top 3 | +10 | behavior | B30a/B30b/B30c | **Disabled** |
| B30c | Early mover top 5 | +5 | behavior | B30a/B30b/B30c | **Disabled** |
| C01 | Confluencia básica | +10 | confluence | C01/C02 | Activo |
| C02 | Confluencia fuerte | +15 | confluence | C01/C02 | Activo |
| C03a | Origen exchange compartido | +5 | confluence | — (aditivo) | Activo |
| C03b | Origen bridge compartido | +20 | confluence | — (aditivo) | Activo |
| C03c | Origen mixer compartido | +30 | confluence | — (aditivo) | Activo |
| C03d | Mismo padre directo | +30 | confluence | — (aditivo) | Activo |
| C05 | Fondeo temporal | +10 | confluence | — (aditivo) | Activo |
| C06 | Monto similar | +10 | confluence | — (aditivo) | Activo |
| C07 | Red de distribución | +30 | confluence | — | Activo |
| COORD04 | Fondeo via mixer | +50 | confluence | — | Activo |
| M01 | Volumen anómalo | +15 | market | — | Activo |
| M02 | Odds estables rotos | +20 | market | — | Activo |
| M03 | Baja liquidez | +10 | market | — | Activo |
| M04a | Concentración moderada | +15 | market | M04a/M04b | Activo |
| M04b | Concentración alta | +25 | market | M04a/M04b | Activo |
| M05a | Deadline <72h | +10 | market | M05a/M05b/M05c | Activo |
| M05b | Deadline <24h | +15 | market | M05a/M05b/M05c | Activo |
| M05c | Deadline <6h | +25 | market | M05a/M05b/M05c | Activo |
| N01 | Bot | -40 | negative | — | Activo |
| N02 | Noticias | -20 | negative | — | Activo |
| N03 | Arbitraje | -100 | negative | — | Activo |
| N04 | Mercados opuestos | 0 | negative | — | Activo |
| N05 | Copy-trading | -25 | negative | — | Activo |
| N06a | Degen leve | -5 | negative | N06a/N06b/N06c | Activo |
| N06b | Degen moderado | -15 | negative | N06a/N06b/N06c | Activo |
| N06c | Degen fuerte | -30 | negative | N06a/N06b/N06c | Activo |
| N07a | Scalper leve | -20 | negative | N07a/N07b | Activo |
| N07b | Scalper serial | -40 | negative | N07a/N07b | Activo |
| N08 | Anti-bot evasión | +25 | behavior | — | Activo |
| N09a | Apuesta obvia extrema | -40 | negative | N09a/N09b; B25↔N09 | Activo |
| N09b | Apuesta obvia | -25 | negative | N09a/N09b; B25↔N09 | Activo |
| N10a | Horizonte lejano | -10 | negative | N10a/N10b/N10c | Activo |
| N10b | Horizonte muy lejano | -20 | negative | N10a/N10b/N10c | Activo |
| N10c | Horizonte extremo | -30 | negative | N10a/N10b/N10c | Activo |

> **Nota sobre B27/B30**: Estructura implementada completa pero deshabilitada via feature flags.
> - B27 necesita: `sell_detector` que cubra wallets pre-alerta (actualmente solo post-alerta).
> - B30 necesita: tabla `trades` históricos en Supabase (actualmente solo ventana de 35 min del scan).

> **Nota sobre O02/O03**: Mutuamente exclusivos en la lógica de `wallet_analyzer.py` (O03 tiene prioridad), pero NO listados en `MUTUALLY_EXCLUSIVE_GROUPS` porque el analyzer ya lo garantiza.

---

## 4. Sistema de confluencia por capas

### 4.1 Arquitectura

El sistema de confluencia fue completamente rediseñado de filtros independientes a un sistema de **4 capas aditivas**:

```
Layer 1: Direction (C01/C02)     — ¿Cuántos wallets van en la misma dirección?
    ↓
Layer 2: Origin (C03a/b/c/d)    — ¿Comparten origen de fondeo?
    ↓
Layer 3: Bonus (C05, C06)       — ¿Fondeo temporal coordinado? ¿Montos similares?
    ↓
Layer 4: Distribution (C07)     — ¿Un sender → 3+ wallets?
```

**Reglas clave:**
- Layer 1: C01 y C02 son mutuamente exclusivos (solo el más alto).
- Layer 2: C03a-d son **aditivos** — pueden disparar múltiples tipos simultáneamente. Cada tipo dispara una sola vez (el primer ejemplo con 2+ wallets gana).
- Layer 3: C05 y C06 son **bonus aditivos** — se suman independientemente.
- Layer 4: C07 dispara independientemente si 1 sender → 3+ wallets activas.
- **Todas las capas se suman** al score final.

### 4.2 Sender exclusion

Solo se excluyen de la análisis de senders:
- **Contratos de Polymarket** (`POLYMARKET_CONTRACTS` de `blockchain_client.py`)
- **Super-senders**: senders que fondean wallets en >3 mercados diferentes (trackeo cross-market en `main.py`)

**No se excluyen** exchanges, bridges ni mixers — su uso compartido ES la señal de confluencia que detectan C03a-c.

### 4.3 Fix del threshold de funding

**Problema**: `wallet_analyzer.py` solo hacía fetch de funding sources cuando el `basic_score` del wallet era ≥30 puntos. Esto significaba que la mayoría de wallets en un grupo de confluencia no tenían datos de funding en la DB, haciendo que C03-C07 no pudieran disparar.

**Fix** (`main.py:722-741`): Cuando hay 3+ wallets en la misma dirección (condición mínima para confluencia), `main.py` fuerza el fetch de funding para TODOS los wallets del grupo antes de llamar a `confluence_detector.detect()`:

```python
if same_dir_count >= config.CONFLUENCE_BASIC_MIN_WALLETS and chain_client is not None:
    for w in wallets_for_confluence:
        addr = w["address"]
        existing = db.get_funding_sources(addr)
        if not existing:
            funding = chain_client.get_funding_sources(addr, max_hops=config.MAX_FUNDING_HOPS)
            if funding:
                db.insert_funding_batch(funding)
```

### 4.4 Ejemplo de scoring por capas

Escenario: 5 wallets compran YES, 3 fondeadas desde Binance en ventana de 2h, con montos similares, y 1 sender directo fondea 3 de ellas.

| Capa | Filtro | Puntos | Detalle |
|------|--------|--------|---------|
| Layer 1 | C02 | +15 | 5 wallets → YES |
| Layer 2 | C03a | +5 | 3 wallets desde Binance |
| Layer 2 | C03d | +30 | 1 sender → 3 wallets |
| Layer 3 | C05 | +10 | 3 funded <4h |
| Layer 3 | C06 | +10 | Montos ±30% |
| Layer 4 | C07 | +30 | 1 sender → 3 wallets activas |
| **Total confluencia** | | **+100** | |

---

## 5. Flujo completo del scan

### 5.1 Entrada: `run_scan()` → `main.py:353`

```
1. Conectar servicios (DB, Polymarket, Blockchain, News, Twitter, Telegram)
2. Fetch mercados activos → filtrar (volumen, odds, categoría, blacklist) → cap 100
3. Para cada mercado:
   └── _process_market()
       ├── 4a. Fetch trades (últimos 35 min, min $100)
       ├── 4c. Agrupar por wallet → top 10 por volumen
       ├── Para cada wallet:
       │   └── _analyze_wallet()
       │       ├── 4d. Compute accumulation (min $350)
       │       ├── 4e-i.  WalletAnalyzer → W01-W11, O01-O03, COORD04
       │       ├── 4e-ii. BehaviorAnalyzer → B + N09 + N10
       │       │          ├── B01, B05, B06, B07, B16, B17
       │       │          ├── B18a-d (≥2 trades + bonus)
       │       │          ├── B19a-c → B14 (suprimido si B19)
       │       │          ├── B28a/b → B23a/b (suprimido si B28)
       │       │          ├── B25a-c → N09a/b (suprimido si B25)
       │       │          ├── B26a/b, B20
       │       │          ├── B27 (disabled), B30 (disabled)
       │       │          └── N10a-c (usa resolution_date)
       │       ├── 4e-iii. NoiseFilter → N01, N02, N05, N06, N07, N08
       │       └── 4e-iv.  ArbitrageFilter → N03 (kill) / N04
       │
       ├── 5a. MarketAnalyzer → M01-M05
       ├── 5b. Confluence funding fix + ConfluenceDetector → C01-C07
       ├── 5c. Score = best_wallet_filters + market + confluence
       │        └── calculate_score() → mutual exclusion → amount mult → diversity mult → star validation
       ├── 6. Odds range check (0.05-0.55, extended 0.70 si score≥90)
       └── 7. Build Alert
4. Deduplicación intra-scan (Jaccard >0.6)
5. Para cada alerta:
   ├── Cross-scan dedup (mismo wallet+mercado+dirección en 24h)
   ├── Consolidación high-star (merge en alerta 4+★ existente)
   └── Insert + Publish
       ├── 4+★ → Telegram (privado + público) + X
       └── 3★ → X solamente
6. Sell monitoring (SellDetector → check_open_positions)
7. Log scan record
```

### 5.2 Parámetros pasados a `behavior_analyzer.analyze()`

```python
behavior_analyzer.analyze(
    wallet_address=wallet_address,
    trades=wallet_trades,         # trades del wallet en este mercado
    market_id=market.market_id,
    current_odds=market.current_odds,
    wallet_balance=wallet_balance,  # chain_client.get_balance()
    resolution_date=market.resolution_date,  # para N10
)
```

### 5.3 Scoring: solo el mejor wallet

El score NO suma los filtros de todos los wallets. Usa el **mejor wallet** (mayor raw score) + market + confluence:

```python
all_filters = best_wallet_filters + market_filters + confluence_filters
scoring_result = calculate_score(all_filters, total_amount, wallet_market_count)
```

Esto evita inflación artificial del score por tener muchos wallets mediocres.

---

## 6. Sistema de scoring

### 6.1 Categorías (v2)

Los filtros se mapean a 4 categorías de scoring:

| Categoría original | Categoría scoring |
|--------------------|-------------------|
| wallet, origin | ACCUMULATION |
| behavior, confluence | COORDINATION |
| market | TIMING |
| negative | MARKET |

### 6.2 Mutual exclusion (scoring engine)

El scoring engine aplica `_enforce_mutual_exclusion()` como **safety net**. Itera los 17 grupos de `MUTUALLY_EXCLUSIVE_GROUPS` y mantiene solo el filtro con mayor `abs(points)` en cada grupo:

```python
MUTUALLY_EXCLUSIVE_GROUPS = [
    ["W01", "W02", "W03"],
    ["W04", "W05"],
    ["B18a", "B18b", "B18c", "B18d"],
    ["B19a", "B19b", "B19c"],
    ["B23a", "B23b"],
    ["B25a", "B25b", "B25c"],
    ["B26a", "B26b"],
    ["B27a", "B27b"],
    ["B28a", "B28b"],
    ["B30a", "B30b", "B30c"],
    ["C01", "C02"],
    ["M04a", "M04b"],
    ["M05a", "M05b", "M05c"],
    ["N06a", "N06b", "N06c"],
    ["N07a", "N07b"],
    ["N09a", "N09b"],
    ["N10a", "N10b", "N10c"],
]
```

### 6.3 Amount multiplier (logarítmico)

Fórmula: `0.18 * ln(total_usd) - 0.37`, clamped `[0.3, 2.0]`

| Amount | Multiplier |
|--------|-----------|
| $100 | 0.46 |
| $500 | 0.75 |
| $1,000 | 0.87 |
| $5,000 | 1.17 |
| $10,000 | 1.29 |
| $50,000 | 1.57 |
| $100,000 | 1.70 |

### 6.4 Diversity multiplier (sniper vs shotgun)

| Condición | Multiplier | Lógica |
|-----------|-----------|--------|
| ≤3 mercados | x1.2 | Sniper — enfocado = señal de calidad |
| 4-10 mercados | x1.0 | Normal |
| >10 mercados | x0.7 | Shotgun — disperso |
| >20 mercados | x0.5 | Super shotgun — probablemente ruido |

Ambos multiplicadores se combinan: `multiplier = amount_mult * diversity_mult`

### 6.5 Star levels

| Score final | Estrellas |
|------------|-----------|
| ≥220 | 5★ |
| ≥150 | 4★ |
| ≥100 | 3★ |
| ≥70 | 2★ |
| ≥40 | 1★ |
| <40 | 0★ |

### 6.6 Star validation (downgrade)

| Star | Requisitos | Si no cumple |
|------|-----------|--------------|
| 3★ | 2+ categorías | Downgrade a 2★ |
| 4★ | 2+ categorías + $5K min | Downgrade a 3★ → re-evalúa |
| 5★ | 3+ categorías + $10K min + COORDINATION | Downgrade a 4★ → re-evalúa |

Score cap: **400** puntos máximo.

---

## 7. Configuración actual

### 7.1 Exchanges conocidos (`KNOWN_EXCHANGES`)

| Dirección | Exchange |
|-----------|----------|
| `0x1a1e...d31` | Coinbase |
| `0x5038...3da` | Coinbase |
| `0xddfa...740` | Coinbase |
| `0xe780...245` | Binance |
| `0xf977...ec` | Binance |
| `0x28c6...d60` | Binance |
| `0x267b...dc0` | Kraken |
| `0x6cc5...a7b` | OKX |
| `0x6262...a3` | Crypto.com |
| `0x0d07...fe` | Gate.io |
| `0xf89d...a40` | **Bybit** (nuevo) |

### 7.2 Bridges conocidos (`KNOWN_BRIDGES`) — NUEVO

| Dirección | Bridge |
|-----------|--------|
| `0xa0c6...92a` | Polygon PoS Bridge |
| `0x401f...8b` | Polygon Plasma Bridge |
| `0x4f3a...92f` | Multichain (Anyswap) |
| `0x25d8...ae8` | Hop Protocol |
| `0x69b5...920` | Across Protocol |
| `0x45a0...cd` | Stargate (LayerZero) |
| `0x2f6f...590` | Stargate (LayerZero) |

### 7.3 Mixers conocidos (`MIXER_ADDRESSES`)

| Dirección | Protocolo |
|-----------|-----------|
| `0x1e34...86f` | Tornado Cash (Polygon) |
| `0xba21...659` | Tornado Cash (Polygon) |
| `0xdf23...3d` | Tornado Cash (Polygon) |
| `0xaf4c...040` | Tornado Cash (Polygon) |
| `0x94a1...ba` | Tornado Cash (Polygon) |
| `0xee9f...2` | Railgun (Polygon) |
| `0x19ff...012` | Railgun (Polygon) |

### 7.4 Feature flags

| Flag | Valor | Filtros afectados | Dato que falta |
|------|-------|-------------------|----------------|
| `ENABLE_B27` | `False` | B27a, B27b | sell_detector cubriendo wallets pre-alerta |
| `ENABLE_B30` | `False` | B30a, B30b, B30c | Tabla `trades` históricos en Supabase |

### 7.5 Umbrales de scan e ingesta

| Parámetro | Valor | Descripción |
|-----------|-------|-------------|
| `MIN_TX_AMOUNT` | $100 | Mínimo por transacción |
| `MIN_ACCUMULATED_AMOUNT` | $350 | Mínimo acumulado para analizar wallet |
| `ACCUMULATION_WINDOW_HOURS` | 72h | Ventana de acumulación |
| `SCAN_INTERVAL_MINUTES` | 30 | Frecuencia del scan |
| `SCAN_LOOKBACK_MINUTES` | 35 | Ventana de trades (>30 para evitar gaps) |
| `SCAN_TIMEOUT_SECONDS` | 480 | 8 min máximo por scan |
| `MARKET_TIMEOUT_SECONDS` | 45 | Por mercado |
| `MAX_WALLETS_PER_MARKET` | 10 | Top N por volumen |
| `MARKET_MIN_VOLUME_24H` | $1,000 | Vol mínimo 24h |
| `MARKET_SCAN_CAP` | 100 | Max mercados por scan |
| `CROSS_SCAN_DEDUP_HOURS` | 24h | Ventana de dedup cross-scan |
| `ODDS_MIN` | 0.05 | Mínimo odds permitido |
| `ODDS_MAX` | 0.55 | Máximo odds normal |
| `ODDS_MAX_EXTENDED` | 0.70 | Máximo odds si score ≥ 90 |

---

## 8. Sistemas auxiliares

### 8.1 Publicación

| Plataforma | Condición | Formato |
|------------|-----------|---------|
| X (Twitter) | 3+★ | `format_x_alert` — max 280 chars, sin score/filtros |
| Telegram privado | 4+★ | `format_telegram_detailed` — filtros, multipliers, wallets con `[YES]`/`[NO]` |
| Telegram público | 4+★ | `format_telegram_alert` — score visible, sin filtros |
| Telegram whale | B19 trigger | `format_whale_entry` — siempre se publica |

**Indicadores de dirección en Telegram**:
- Wallet misma dirección: `💼 0xabc1...ef23 [YES] — $5,000`
- Wallet dirección opuesta: `⚠️ 0xabc1...ef23 [NO ↕] — $3,000 (dirección opuesta)`
- Ajuste de odds por dirección: YES → odds as-is, NO → `1.0 - odds`

### 8.2 Sell monitoring

- `SellDetector` verifica `wallet_positions` abiertas cada scan
- Detecta ventas individuales y coordinadas (2+ wallets vendiendo en <4h)
- Publica notificaciones con P&L estimado a Telegram
- Formato: `format_sell_notification` (individual), `format_coordinated_sell` (coordinado)

### 8.3 Alert tracking & resolution

- `AlertTracking` insertado en DB para cada alerta publicada
- Permite seguimiento de `odds_at_alert` vs odds final
- Resolution checker (externo) marca alertas como `correct`/`incorrect`

### 8.4 Deduplicación

1. **Intra-scan**: Jaccard similarity >0.6 entre questions (con `tokenize_for_dedup` que strip fechas/números). Mantiene el de mayor score.
2. **Cross-scan**: Mismo `market_id` + `direction` + wallet principal en últimas 24h → actualiza el existente.
3. **Consolidación**: Si existe alerta 4+★ para el mismo mercado/dirección, merge wallets nuevos en lugar de crear alerta nueva. Envía update a Telegram.

### 8.5 Super-sender tracking

`main.py` mantiene un dict `sender_market_count` que trackea qué senders fondean wallets en qué mercados. Senders que aparecen en >3 mercados se excluyen de la confluencia (son routers/exchanges, no insiders).

### 8.6 Categorías de mercado relevantes

```python
MARKET_RELEVANT_CATEGORIES = {
    "Politics", "Economics", "Corporate", "Crypto", "Geopolitics",
    "politics", "economics", "corporate", "crypto", "geopolitics",
}
```

### 8.7 Blacklist de mercados

Mercados cuya pregunta contiene alguno de estos términos son excluidos:
- Entretenimiento: tweet, twitter, youtube, grammy, oscar, emmy...
- Deportes: nfl, nba, mlb, premier league, champions league...
- Clima: weather, temperature, hurricane...
- Celebridades: mrbeast, kardashian, celebrity...
- Especulación diaria: up or down, daily, over/under...
- Crypto price: will bitcoin reach, price of ethereum...

---

## Resumen de conteo

Conteo por prefijo de ID (74 en `ALL_FILTERS`):

| Prefijo | Activos | Disabled | Eliminados | Total en registry |
|---------|---------|----------|------------|-------------------|
| Wallet (W) | 7 | 0 | 0 | 7 |
| Origin (O) | 3 | 0 | 0 | 3 |
| Behavior (B) | 24 | 5 | 1 | 30 |
| Confluence (C) | 9 | 0 | 0 | 9 |
| Coordination (COORD) | 1 | 0 | 0 | 1 |
| Market (M) | 8 | 0 | 0 | 8 |
| Negative (N) | 16 | 0 | 0 | 16 |
| **Total** | **68** | **5** | **1** | **74** |

Conteo por `category` en el registro:

| category | Count | Filtros |
|----------|-------|---------|
| wallet | 7 | W01-W11 |
| origin | 3 | O01-O03 |
| behavior | 31 | B01-B30c + N08 |
| confluence | 10 | C01-C07 + COORD04 |
| market | 8 | M01-M05c |
| negative | 15 | N01-N10c (sin N08) |

> **Nota**: N08 (Anti-bot evasión) tiene `category: "behavior"` y puntos positivos (+25) a pesar de su prefijo N. Esto es intencional: detecta un patrón que intenta evadir el detector de bots, lo cual es una señal positiva de insider.
>
> **Nota**: B18e permanece en `ALL_FILTERS` y `MUTUALLY_EXCLUSIVE_GROUPS` pero ningún analyzer lo genera. Su código fue reemplazado por B26a/b.
