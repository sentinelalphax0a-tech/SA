# Auditoría de Cobertura de Campos en Base de Datos

**Fecha:** 2026-03-17
**Base de datos:** 13,795 alertas totales (3,249 resueltas · 10,521 pending · 25 otras)
**Objetivo:** Documentar qué campos no se guardan, por qué, y qué impacto tiene.
**Acción:** Solo documentación. Sin cambios en código ni DB.

---

## Resumen ejecutivo

| Severidad | Campos afectados | Impacto principal |
|:---------:|:----------------:|:-----------------|
| 🔴 Crítico | 4 campos | Datos que deberían existir y no existen nunca |
| 🟠 Grave | 5 campos | Baja cobertura histórica (añadidos tarde o API poco fiable) |
| 🟡 Sorpresa | 4 hallazgos | Comportamiento inesperado — más grave de lo aparente |
| 🔵 Por diseño | 3 campos | Baja cobertura justificada por lógica del sistema |

**El hallazgo más grave no relacionado con ML:** el 68.4% de todas las alertas está marcado como `is_secondary=True` — y un 84.5% de las alertas 0★ son secundarias. La base de datos es mayoritariamente ruido de re-detección.

---

## Cobertura real por campo (datos exactos)

| Campo | Todas | Resueltas | Pending | Estado |
|:------|------:|----------:|--------:|:------:|
| `market_category` | **0%** | **0%** | **0%** | 🔴 |
| `price_impact` | **0%** | **0%** | **0%** | 🔴 |
| `has_news = True` | **0%** | **0%** | **0%** | 🔴 |
| `news_summary` | **0%** | **0%** | **0%** | 🔴 |
| `additional_buys_count > 0` | **0%** | **0%** | **0%** | 🔴 |
| `close_reason` | 0.5% | 0.7% | 0.4% | 🟠 |
| `total_sold_pct > 0` | 0.5% | 0.6% | 0.4% | 🟠 |
| `confluence_type` | 2.5% | 2.1% | 2.7% | 🔵 |
| `hours_to_deadline` (>0) | 45.8% | 10.5% | 56.7% | 🟠 |
| `market_volume_24h_at_alert` (>0) | 50.7% | 15.2% | 61.8% | 🟠 |
| `market_liquidity_at_alert` (>0) | 50.7% | 15.2% | 61.8% | 🟠 |
| `score_initial` (>0) | 50.6% | 15.1% | 61.6% | 🟠 |
| `odds_at_alert_initial` (>0) | 50.7% | 15.2% | 61.8% | 🟠 |
| `wallets_count_initial >= 2` | 1.3% | 0.3% | 1.6% | 🔵 |
| `realized_return` | 100% resueltas | **100%** | 0% | 🟡 |
| `multi_signal = True` | 83.6% | 78.8% | 85.1% | 🔵 |
| `is_secondary = True` | **68.4%** | 61.5% | 70.4% | 🟡 |
| `merge_confirmed` | 0.1% | 0.2% | 0.1% | 🔵 |

---

## 🔴 Críticos — Campos con 0% de cobertura

### 1. `market_category` — 0% en 13,795 alertas

**Qué debería contener:** Categoría del mercado (política, deportes, cripto, cultura pop, etc.)

**Dónde se escribe:**
```python
# src/main.py — _build_alert()
market_category=market.category,
```

**Por qué es 0%:**
`market.category` viene del campo `category` de la API Gamma de Polymarket (`/events` endpoint). En práctica, este campo es **siempre null** en la respuesta de la API para los mercados que detecta el sistema. La API devuelve `category` en el objeto de evento (`/events`) pero el sistema consulta `/markets`, que no lo incluye. Existe un gap arquitectural: los mercados se obtienen por endpoint distinto al de eventos.

**Impacto:**
- Sin categoría, es imposible analizar edge por tipo de mercado (¿es el sistema mejor en cripto? ¿en política?)
- La variable más obvia para Model A de ML está completamente vacía
- El dashboard no puede filtrar por categoría

**Solución propuesta:**
Hacer un lookup adicional al endpoint `/events?market={condition_id}` en el momento de crear la alerta, o enriquecer la tabla `markets` con `event_id` y hacer join con la tabla de eventos.

---

### 2. `price_impact` — 0% en 13,795 alertas

**Qué debería contener:** El movimiento de precio causado por las compras detectadas (en pp). Ej: si YES estaba a 0.30 y tras las compras quedó a 0.38, `price_impact = +8pp`.

**Dónde se escribe:** **Ningún sitio.** No existe ninguna línea de código que escriba este campo.

```python
# src/database/models.py line 89
price_impact: float | None = None   # ← inicializado, NUNCA modificado
```

El campo existe en el modelo y los formatters lo leen:
```python
# src/publishing/formatter.py
if alert.price_impact is not None:   # ← branch nunca se ejecuta
    ...
```

**Por qué es 0%:**
El campo fue añadido al schema como feature futura pero nunca se implementó el cálculo. Requeriría capturar el precio del mercado **antes** de las compras detectadas y compararlo con el precio **después**, lo que implica guardar la serie temporal de precios o comparar `odds_before_window` vs `odds_at_alert`.

**Impacto:**
- El formatter tiene texto preparado para mostrarlo pero nunca aparece
- Es el indicador más directo de "impacto real de las ballenas en el mercado"
- Sin él, no se puede distinguir entre una compra que movió el precio (señal fuerte) y una que no lo hizo (ruido)

**Solución propuesta:**
Durante el scan, guardar el precio del mercado al inicio del `lookback_window` y comparar con `odds_at_alert`. La diferencia normalizada es el `price_impact`.

---

### 3. `has_news` / `news_summary` — 0% aunque N02 detecta en el 15% de alertas

**Qué debería contener:** `has_news = True` cuando el filtro N02 (noticias) se activa. `news_summary` debería tener el titular de la noticia detectada.

**El bug exacto:**

El filtro N02 se activa correctamente y aparece en `filters_triggered`:
```json
{
  "filter_id": "N02",
  "filter_name": "Noticias",
  "points": -20,
  "category": "negative",
  "details": "Prediction Market Trends: Fed Chair, Oscars, and 2028 Election"
}
```

Pero el campo `has_news` del objeto Alert **nunca se actualiza**:

```python
# src/analysis/noise_filter.py — NoiseFilter._check_news()
has_news, summary = self.news.check_news(market_question)
if has_news:
    return [_fr(config.FILTER_N02, summary)]  # ← devuelve FilterResult
    # ¡Nunca actualiza alert.has_news ni alert.news_summary!
```

```python
# src/main.py — _build_alert()
# No lee has_news de los FilterResult. Solo copia la lista de FilterResult a filters_triggered.
```

**Datos reales:**
- N02 presente en `filters_triggered`: **~15% de alertas** (75/500 en muestra)
- `has_news = True`: **0 alertas en toda la DB**
- `news_summary` con contenido: **0 alertas**

**Impacto:**
- El detector de noticias funciona pero sus resultados son invisibles como campo de primer nivel
- El resumen de la noticia se guarda correctamente en `filters_triggered[].details` pero no en `news_summary`
- Para recuperar si hubo noticias hay que parsear el JSON de `filters_triggered` — posible pero ineficiente
- Para ML: `has_news` como feature binaria es trivial de usar pero está siempre vacía

**Solución propuesta (2 líneas de código):**
```python
# En _build_alert() o post-filter, después de tener filters_triggered:
n02 = next((f for f in filters_triggered if f.filter_id == "N02"), None)
if n02:
    alert.has_news = True
    alert.news_summary = n02.details
```

---

### 4. `additional_buys_count` — 0% aunque whale_monitor tiene lógica de detección

**Qué debería contener:** Número de compras adicionales que el wallet hizo en el mismo mercado después de la alerta inicial. Indicador de convicción y DCA.

**Dónde se escribe:**
```python
# src/tracking/whale_monitor.py — _process_additional_buy()
current_count = alert.get("additional_buys_count") or 0
new_count = current_count + 1
fields["additional_buys_count"] = new_count
self.db.update_alert_fields(alert_id, fields)
```

**Por qué es 0%:**

Investigación de la lógica de `whale_monitor`:
- El monitor detecta "additional buys" cuando una wallet ya alertada vuelve a comprar en el mismo mercado
- Pero el campo **nunca se incrementa en práctica** porque la condición para activar `_process_additional_buy()` requiere que el trade sea:
  1. Posterior a `alert.created_at`
  2. Del mismo wallet address
  3. En el mismo mercado y dirección
  4. Por encima de `MIN_ADDITIONAL_BUY_AMOUNT` (threshold)

En los datos observados: **0 alertas** con additional_buys_count > 0 sobre 13,795. Esto puede ser porque:
- El threshold es demasiado alto y los DCA reales no lo superan
- El lookback del whale_monitor con Bug 5 (ventana fija de 6h) perdía compras adicionales anteriores — el fix de Bug 5 debería mejorar esto prospectivamente
- Los wallets detectados raramente DCA en el mismo mercado (compran una vez y mantienen)

**Impacto:**
- Feature de convicción completamente vacía
- DCA indicator era potencialmente el mejor predictor de "smart money" a largo plazo

---

## 🟠 Graves — Baja cobertura histórica

### 5. `hours_to_deadline`, `market_volume_24h_at_alert`, `market_liquidity_at_alert`

**Patrón de cobertura:**

| Campo | Resueltas (antiguas) | Pending (recientes) | Diferencia |
|:------|:-------------------:|:-------------------:|:----------:|
| `hours_to_deadline` | 10.5% | 56.7% | **+46pp** |
| `market_volume_24h_at_alert` | 15.2% | 61.8% | **+47pp** |
| `market_liquidity_at_alert` | 15.2% | 61.8% | **+47pp** |

**Por qué bajos en resueltas:**
Estos campos fueron añadidos al `_build_alert()` aproximadamente en commit de finales de 2025 / enero 2026. Las ~2,700 alertas resueltas previas a esa fecha no tienen estos datos porque ya existían cuando se añadió el campo.

**Por qué no al 100% incluso en pending:**
La API Gamma de Polymarket devuelve `endDate`, `volume24hr`, y `liquidityNum` como campos opcionales. Para mercados sin resolución explícita (mercados abiertos indefinidamente) o mercados con escasa actividad, estos campos llegan como `null` o `0`.

**Impacto para ML:**
- Para las 3,249 alertas resueltas (el único dataset etiquetado), solo el 15% tiene estas features
- Entrenar un modelo sobre el 15% con estos campos y el 85% sin ellos requiere imputación
- La brecha temporal (alertas recientes tienen más features) puede introducir data leakage si no se hace split temporal correcto

---

### 6. `close_reason` y `total_sold_pct` — 0.5% cobertura

**Valores actuales en la DB:**

| Valor | Cantidad | % del total |
|:------|:--------:|:-----------:|
| `NULL` | 13,724 | **99.5%** |
| `sell_clob` | 67 | 0.5% |
| `merge_suspected` | 4 | 0.0% |

**`total_sold_pct > 0`: 66 alertas (0.5%)** — todas tienen `close_reason = sell_clob`.

**Por qué es 0.5% y no más:**

Este número revela el impacto real del Bug 5 (sell detection windows). Con el bug:
- `check_open_positions()` usaba ventana fija de 35 minutos
- `check_net_positions()` fallaba cuando `buys_shares = 0` (posiciones más antiguas de 48h)
- Resultado: el sell detector era prácticamente ciego para el 99%+ de las posiciones

El fix de Bug 5 (2026-03-17, esta rama) debería elevar este número significativamente en las próximas semanas.

**`close_reason` esperados que deberían existir pero no existen:**
- `position_gone` — posición desapareció sin venta detectada
- `merge_confirmed` — merge/arb arbitrage (solo 13 alertas con `merge_confirmed=True` en toda la DB)

**Impacto:**
- Sin `total_sold_pct`, el dashboard no puede identificar "exits anticipados" (el whale salió antes de la resolución)
- `realized_return` no puede diferenciarse de `actual_return` porque no hay datos de ventas parciales
- El accuracy pool de alertas donde el whale vendió temprano (señal de que él mismo perdió la confianza) se mezcla con el de alertas donde mantuvo hasta resolución

---

### 7. `score_initial` / `odds_at_alert_initial` — 50% cobertura, pero con 91% de drift

**Score drift analizado:**
```
score != score_initial:       457/500 alertas muestreadas (91.4%)
star_level != star_level_initial: 211/500 alertas muestreadas (42.2%)
```

**Muestra de drift:**
```
id=11765  star 1★ → 0★   score 46 → 19
id=13937  star 2★ → 0★   score 82 → 34
id=9394   star 1★ → 0★   score 62 → 36
```

**Por qué drifta tanto:**
- La vacuna Bug 4 (distinct_markets) modificó score/star_level de 8,676 alertas
- El resolver actualiza scores post-resolución
- `_try_consolidate()` puede subir el score cuando nuevas wallets se unen a una alerta

**Impacto:**
Los campos `_initial` existen exactamente para preservar el estado T0. El alto drift confirma que son necesarios y que el score actual no refleja la calidad de la señal en el momento de detección. Para ML supervisado, usar `score_initial` (el estado en el momento de la señal) es más correcto que `score` (el estado actual, post-vaccine).

---

## 🟡 Sorpresas — Hallazgos inesperados

### 8. 68.4% de todas las alertas son `is_secondary = True`

**Distribución por star_level:**

| ★ | Primary | Secondary | % Secondary |
|:-:|:-------:|:---------:|:-----------:|
| 5★ | 36 | 12 | 25.0% |
| 4★ | 86 | 83 | 49.1% |
| 3★ | 276 | 189 | 40.6% |
| 2★ | 704 | 396 | 36.0% |
| 1★ | 1,581 | 1,225 | 43.6% |
| **0★** | **1,682** | **7,525** | **81.7%** |
| **TOTAL** | **4,365** | **9,430** | **68.4%** |

**Qué significa:** Una alerta es secundaria cuando ya existe otra alerta para el mismo `(market_id, direction)` en la DB. El sistema crea la alerta nueva pero la marca como secundaria.

**El problema:** De 13,795 alertas en DB, **9,430 son duplicados del mismo mercado+dirección**. El 81.7% de las alertas 0★ son secundarias. La base de datos está dominada por re-detecciones del mismo movimiento.

**¿Por qué se guardan?**
Cada scan re-detecta wallets que ya alertaron. `_check_cross_scan_duplicate()` filtra duplicados dentro de N horas, pero si pasa el tiempo, el mismo movimiento se re-detecta y genera una nueva alerta secundaria.

**Impacto:**
- El storage crece principalmente por secundarias (68% del volumen)
- Cualquier análisis raw sin filtrar `is_secondary` sobrestima señales por 3.1×
- Las 7,525 alertas 0★ secundarias no tienen ningún valor analítico
- El dedup de `apply_dashboard_filters()` es esencial — sin él los números son 3× más de lo real

---

### 9. `realized_return` = `actual_return` en el 100% de los casos

**Datos:**
```
Alertas resueltas con realized_return: 3,249/3,249 (100%)
```

**Muestra:**
```
id=2653  actual=21.2%  realized=21.2%  ← idénticos
id=3884  actual=119.8% realized=119.8% ← idénticos
```

**Qué debería ser `realized_return`:**
El retorno **real** del whale, considerando que puede haber vendido antes de la resolución. Si vendió el 50% a 0.60 (ganó) y el otro 50% resolvió a 0 (perdió), el realized_return debería reflejar esa media ponderada — no el binario de `actual_return`.

**Por qué son idénticos:**
El resolver escribe `realized_return = actual_return` directamente:
```python
# src/tracking/resolver.py
realized_return = actual_return  # simplified when no sell events
```
La lógica de ponderación con sell events existe como código pero no se ejecuta porque `total_sold_pct` casi nunca tiene datos (0.6% de alertas), así que siempre cae al path simplificado.

**Impacto:**
`realized_return` como campo separado no aporta valor actualmente — es idéntico a `actual_return`. Cuando el sell detector esté funcionando correctamente (post-Bug 5 fix), estos dos campos deberían comenzar a divergir.

---

### 10. `score` drifta en el 91.4% de las alertas post-creación

Ya cubierto en sección de `score_initial`. El hallazgo clave: el score que ve el usuario en el dashboard para una alerta resuelta no es el score que tenía cuando se generó la señal. Para track record honesto, `score_initial` es el campo correcto.

---

## 🔵 Por diseño — Baja cobertura justificada

### 11. `confluence_type` — 2.5%

Solo se popula cuando múltiples wallets con infraestructura compartida compran el mismo mercado. Require: 2+ wallets + análisis de funding origin (exchange, mixer, bridge). Extremadamente raro en un mercado normal. Funciona como está diseñado.

### 12. `wallets_count_initial >= 2` — 1.3%

El 98.7% de las alertas son de un solo wallet. La confluencia multi-wallet es genuinamente rara. Funciona como está diseñado.

### 13. `merge_confirmed` — 0.1%

Solo 13 alertas en toda la DB. Los merges de CTF (mercados que comparten liquidez) son raros y requieren confirmación manual. Funciona como está diseñado.

---

## Mapa de problemas por componente del sistema

### `src/main.py` — Scanner principal

| Problema | Campo | Tipo |
|:---------|:------|:----:|
| `market.category` viene null de la API | `market_category` | API gap |
| `has_news` y `news_summary` nunca se propagan desde FilterResult | `has_news`, `news_summary` | Bug de integración |
| `price_impact` nunca calculado | `price_impact` | Feature no implementada |
| Campos `_initial` añadidos tarde (no disponibles para alertas antiguas) | `score_initial`, `odds_at_alert_initial`, etc. | Deuda histórica |

### `src/tracking/whale_monitor.py` — Monitor de ballenas

| Problema | Campo | Tipo |
|:---------|:------|:----:|
| Bug 5 (ventana fija 35min) impedía detectar sells | `total_sold_pct`, `close_reason` | Bug — corregido 2026-03-17 |
| `additional_buys_count` nunca se incrementa en práctica | `additional_buys_count` | Threshold/lógica |
| `realized_return` no se calcula con sell data | `realized_return` | Feature incompleta |

### `src/analysis/noise_filter.py` — Filtros de ruido

| Problema | Campo | Tipo |
|:---------|:------|:----:|
| N02 devuelve FilterResult pero no actualiza alert.has_news | `has_news`, `news_summary` | Bug de integración |

### API Gamma de Polymarket — Fuente de datos externa

| Problema | Campo | Tipo |
|:---------|:------|:----:|
| `category` siempre null en `/markets` endpoint | `market_category` | API limitación |
| `endDate`, `volume24hr`, `liquidityNum` opcionales | `hours_to_deadline`, volumen, liquidez | API comportamiento |
| `outcomePrices` missing para algunos tipos de mercado | `odds_at_alert_initial` | API comportamiento |

---

## Impacto en ML (resumen práctico)

Si se intentara entrenar un modelo sobre el dataset histórico actual:

| Feature | Usable para ML | Nota |
|:--------|:--------------:|:-----|
| `odds_at_alert` | ✅ 100% | Feature más predictiva disponible |
| `direction` | ✅ 100% | **29pp diferencia de WR YES vs NO** |
| `score`, `multiplier`, `score_raw` | ✅ 100% | Usar `score_initial` para T0 honesto |
| `confluence_count` | ✅ 100% | confluence=2 → 89.5% WR |
| `distinct_markets` (wallet0) | ✅ 100% | Post-vaccine, datos correctos |
| `star_level_initial` | ✅ 100% | Preferir a `star_level` para training |
| `filters_triggered` (patrón) | ✅ 100% | Requiere encoding (one-hot de filter_ids) |
| `has_news` | ❌ 0% | Bug — siempre False (usar N02 en filters) |
| `market_category` | ❌ 0% | Nunca disponible |
| `price_impact` | ❌ 0% | Nunca implementado |
| `hours_to_deadline` | ⚠️ 45% total / 10% resueltas | Imputar o ignorar para training |
| `market_volume_24h_at_alert` | ⚠️ 50% total / 15% resueltas | Ídem |
| `total_sold_pct`, `close_reason` | ⚠️ 0.5% | Post-Bug 5 fix mejorará prospectivamente |
| `additional_buys_count` | ❌ 0% | Nunca incrementado |
| `realized_return` | ❌ usar `actual_return` | Son idénticos actualmente |

**Features proxy disponibles hoy** (extraíbles de campos existentes al 100%):
- `has_news_proxy` = `any(f["filter_id"] == "N02" for f in filters_triggered)`
- `odds_band` = `floor(odds_at_alert / 0.10) * 0.10` (binning)
- `filter_count_positive` = cuenta de filtros con points > 0
- `filter_count_negative` = cuenta de filtros con points < 0
- `is_no_direction` = `direction == "NO"`
- `score_drift` = `score - score_initial` (cuando ambos disponibles)

---

## Priorización de correcciones propuestas

> **Nota:** Ninguna de estas correcciones modifica el scoring, los filtros, ni los thresholds actuales. Son puramente mejoras de captura de datos.

| Prioridad | Campo | Corrección | Dificultad | Impacto ML |
|:---------:|:------|:-----------|:----------:|:----------:|
| 1 | `has_news` / `news_summary` | 2 líneas: leer N02 de FilterResult en `_build_alert()` | 🟢 Fácil | Alto |
| 2 | `market_category` | Lookup adicional a `/events?market=X` al crear alerta | 🟡 Media | Alto |
| 3 | `price_impact` | Guardar odds pre-window y calcular delta en `_build_alert()` | 🟡 Media | Alto |
| 4 | `hours_to_deadline` fallback | Si `endDate` null, intentar parsear question text | 🟡 Media | Medio |
| 5 | `close_reason` + `total_sold_pct` | Bug 5 fix aplicado — monitorear si sube en próximas semanas | ✅ Hecho | Medio |
| 6 | `additional_buys_count` | Revisar threshold y lookback del whale_monitor | 🟡 Media | Bajo |
| 7 | `realized_return` real | Requiere sell events funcionando (depende de #5) | 🔴 Difícil | Medio |
| 8 | `market_volume/liquidity` | Alternativa: enriquecer desde CLOB API | 🔴 Difícil | Bajo |

---

## Datos de referencia

```
Total alertas en DB:       13,795
  Pending:                 10,521 (76.3%)
  Resueltas:                3,249 (23.6%)
  Otras:                       25 (0.2%)

Alertas secundarias:        9,430 (68.4%)
  → Solo 4,365 son señales únicas primarias

Score drifta post-creación:  91.4% de alertas
Star_level drifta:           42.2% de alertas

Campos 100% vacíos:          market_category, price_impact, has_news,
                             news_summary, additional_buys_count

Sell detector pre-Bug5:      Solo 66/13,795 alertas con total_sold_pct>0
Sell detector post-Bug5:     Pendiente de medir (fix aplicado 2026-03-17)
```

---

*Auditoría generada el 2026-03-17. Datos verificados con queries directas a Supabase.*
*Ver también: `docs/dedup_audit.md` (lógica de deduplicación), `docs/pnl_simulation.md` (simulación P&L)*
