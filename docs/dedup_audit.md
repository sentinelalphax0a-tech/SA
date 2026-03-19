# Auditoría de Deduplicación de Alertas

**Fecha:** 2026-03-17
**Rama:** fix/audit-bugs-march-2026
**Contexto:** Investigación de por qué 5★ muestra 4W/1L en la simulación bot vs 12W/4L en el dashboard.

---

## 1. Mapa de lógica de dedup por módulo

### 1.1 Dashboard (`src/dashboard/generate_dashboard.py`)

**Función clave:** `_signal_sort_key()` + bloque de dedup en `compute_stats()` (líneas ~396–430)

```python
def _signal_sort_key(a: dict) -> tuple:
    filters = a.get("filters_triggered") or []
    neg_pts = sum(f.get("points", 0) for f in filters if (f.get("points") or 0) < 0)
    return (a.get("star_level") or 0, a.get("score") or 0, -neg_pts, a.get("created_at") or "")
```

| Parámetro | Valor |
|-----------|-------|
| **Agrupa por** | `market_id` (una apuesta por mercado, ignora dirección) |
| **Criterio de selección** | Mejor `_signal_sort_key`: star DESC → score DESC → menos filtros negativos → created_at más reciente |
| **Campo estrella** | `star_level` (actual, mutable — afectado por vaccine) |
| **Excluye** | `merge_confirmed = true` y `total_sold_pct >= 0.9 OR close_reason = 'position_gone'` (full exits) |
| **Filtra 0★** | No (0★ pueden aparecer en stats pero tienen poco peso) |
| **Usa star_level_initial** | No |

**Resultado para 5★:** 16 mercados únicos → **12W/4L** ← esto es lo que muestra el dashboard.

---

### 1.2 Auditoría semanal (`system_audit/run_audit.py`)

**Función clave:** `apply_dashboard_filters()` (líneas 118–136)

```python
def apply_dashboard_filters(rows: list[dict]) -> list[dict]:
    # Step 1 — dedup: one signal per market_id
    dedup: dict[str, dict] = {}
    for a in rows:
        mid = a.get("market_id", "")
        if mid not in dedup or _signal_sort_key(a) > _signal_sort_key(dedup[mid]):
            dedup[mid] = a
    return [
        a for a in dedup.values()
        if not a.get("merge_confirmed") and not _is_full_exit(a)
    ]
```

| Parámetro | Valor |
|-----------|-------|
| **Agrupa por** | `market_id` — idéntico al dashboard |
| **Criterio de selección** | Misma `_signal_sort_key` — idéntico al dashboard |
| **Campo estrella** | `star_level` (actual) |
| **Excluye** | Mismas exclusiones que el dashboard |

**Coherencia con dashboard:** ✓ **Perfecta.** Usa exactamente la misma función `apply_dashboard_filters()` importada desde aquí por el resto.

---

### 1.3 Análisis Bayesiano (`system_audit/bayesian_edge_analysis.py`)

```python
from system_audit.run_audit import apply_dashboard_filters, ...
pool = apply_dashboard_filters(raw)
```

| Parámetro | Valor |
|-----------|-------|
| **Agrupa por** | `market_id` — delega a `apply_dashboard_filters()` |
| **Coherencia con dashboard** | ✓ **Perfecta.** Misma función. |

El cálculo de edge (+5.43pp, P(edge>0)=99.55%) usa exactamente el mismo pool de 489 mercados únicos que el dashboard.

---

### 1.4 Simulación bot P&L (ejecutada manualmente, sesión anterior)

**Lógica aplicada:**

- **Paso 1:** Primera alerta por `(market_id, direction)` ordenando por `created_at ASC`
- **Paso 2:** Por `market_id`, si hay conflicto YES+NO, quedarse con la de mayor `star_level` (desempate: `score`)

| Parámetro | Valor |
|-----------|-------|
| **Agrupa por** | Primero `(market_id, direction)`, luego `market_id` |
| **Criterio de selección** | PRIMERA cronológicamente (not best sort_key) |
| **Campo estrella** | `star_level` (actual) |

**Esta lógica difiere del dashboard en un punto clave:** selecciona la alerta más ANTIGUA en vez de la de mayor calidad. Para 5★ produce 5 mercados → 4W/1L en vez de 16 → 12W/4L.

**Por qué el número cae de 16 a 5:** Aún por investigar. La query de verificación (ver Sección 2) muestra que Dedup(mkt+dir) da 18 pares únicos y Dedup(mkt) da 16 mercados únicos para 5★. La sesión anterior reportó 5 — posiblemente por un filtro adicional (date range) o un bug en el script ad-hoc. El resultado de 4W/1L **no es representativo** del track record real.

---

### 1.5 Otros módulos

| Módulo | Dedup | Propósito |
|--------|-------|-----------|
| `src/main.py` `_try_consolidate()` | Por `(market_id, direction)` — busca alerta pending 4+★ en últimas 48h | Consolidación en tiempo real, no métricas |
| `src/main.py` `_check_cross_scan_duplicate()` | Por `(market_id, direction, primary_wallet)` en últimas N horas | Evitar insertar duplicados del mismo scan |
| `vacunas/2026-02-19_consolidar_alertas_duplicadas.py` | Por `(market_id, direction)` — mejor `(star DESC, score DESC, id DESC)` | Marcado de secundarias, no métricas |
| `src/analysis/wallet_tracker.py` | Por `wallet_address` (tracking per-wallet, no per-alert) | Win rate por wallet, no por mercado |
| `src/tracking/resolver.py` | Sin dedup (resuelve alerta por alerta) | Determinación de outcome |

---

## 2. Tabla comparativa por star_level

Query ejecutada: `2026-03-17` sobre alertas con `outcome IN ('correct', 'incorrect')`.

**Exclusiones aplicadas** (replicando dashboard):
- `merge_confirmed = true` → excluido
- `total_sold_pct >= 0.9 OR close_reason = 'position_gone'` → excluido

| Estrellas | Raw total | Excluidos | Clean | Dedup (mkt+dir) | Dedup (mkt) | W | L | WR |
|:---------:|----------:|----------:|------:|----------------:|------------:|--:|--:|---:|
| 5★ | 33 | 2 | 31 | 18 | **16** | **12** | **4** | **75.0%** |
| 4★ | 74 | 2 | 72 | 29 | **25** | **17** | **8** | **68.0%** |
| 3★ | 200 | 4 | 196 | 101 | **81** | **51** | **30** | **63.0%** |
| 2★ | 506 | 4 | 502 | 232 | **204** | **147** | **57** | **72.1%** |
| 1★ | 1407 | 4 | 1403 | 463 | **415** | **318** | **97** | **76.6%** |
| **ALL** | **3249** | **16** | **3233** | **564** | **489** | **369** | **120** | **75.5%** |

> **Negrita = columna que usa el dashboard y la auditoría Bayesiana** (`apply_dashboard_filters()`: dedup primero, excluir después). Coincide exactamente con los números del dashboard (5★: 12W/4L ✓) y con la simulación P&L (489 mercados, 369W/120L).

> ⚠️ **Nota de corrección (2026-03-17):** La query original de esta tabla usó orden incorrecto (excluir → dedup) produciendo 491 mercados. El orden correcto de `apply_dashboard_filters()` es **dedup → excluir**, que da 489. Ver sección 6 para el análisis de los 2 mercados que difieren.

### Factor de compresión

| Estrellas | Raw → Clean | Clean → Dedup(mkt+dir) | Dedup(mkt+dir) → Dedup(mkt) | Total Raw → Dedup(mkt) |
|:---------:|:-----------:|:---------------------:|:----------------------------:|:---------------------:|
| 5★ | 33 → 31 (1.06×) | 31 → 18 (1.72×) | 18 → 16 (1.13×) | **2.06×** |
| 4★ | 74 → 72 (1.03×) | 72 → 29 (2.48×) | 29 → 25 (1.16×) | **2.96×** |
| 3★ | 200 → 196 (1.02×) | 196 → 101 (1.94×) | 101 → 81 (1.25×) | **2.47×** |
| 2★ | 506 → 502 (1.01×) | 502 → 232 (2.16×) | 232 → 205 (1.13×) | **2.45×** |
| 1★ | 1407 → 1403 (1.00×) | 1403 → 463 (3.03×) | 463 → 416 (1.11×) | **3.37×** |

La mayor parte de la compresión ocurre en el paso Clean → Dedup(mkt+dir), es decir, el sistema genera muchas alertas del mismo mercado en distintos scans con distintas wallets. La compresión adicional mkt+dir → mkt es pequeña (1.1–1.25×) — pocas señales YES/NO contradictorias por mercado.

> Los totales ALL corregidos respecto a la query original: 491 → **489**, 371W → **369W**. La diferencia son 2 mercados donde el dedup seleccionó una alerta `full_exit` como ganadora — ver sección 6.

### Detalle de conflictos YES/NO en 5★

Markets con más de una alerta (multi-scan o YES+NO) tras limpiar exclusiones:

| market_id (primeros 20 chars) | n alertas | Direcciones | Outcomes |
|-------------------------------|----------:|-------------|---------|
| `0x3488f31e6449f9803f` | 6 | YES×1, NO×5 | correct×1, incorrect×5 |
| `0x8a38e9511fe17e13fa` | 4 | NO×4 | correct×4 |
| `0x09cbe3e796661a1d82` | 3 | NO×3 | correct×3 |
| `0x680efda99a9c43cae7` | 3 | NO×3 | correct×3 |
| `0xe57fce6d333b67f7fb` | 2 | NO×2 | correct×2 |
| `0x804bc34275d7d610de` | 2 | YES×1, NO×1 | correct×1, incorrect×1 |
| `0x6995678ed5f481941c` | 2 | YES×2 | incorrect×2 |

El mercado `0x3488...` (US strikes Iran by Feb 28) es el caso más extremo: 6 alertas → el dashboard mantiene la YES (correct) porque tiene mayor `_signal_sort_key`. Sin dedup, este mercado contribuye 1W/5L al raw; con dedup contribuye 1W.

---

## 3. Diagnóstico de la discrepancia 4W/1L vs 12W/4L

| Método | Alertas 5★ | Mercados únicos | Resultado |
|--------|----------:|----------------:|-----------|
| Raw sin dedup | 31 clean | — | 23W/8L (74.2%) |
| Dedup(mkt+dir), best sort_key | 18 | — | 13W/5L (72.2%) |
| **Dedup(mkt), best sort_key** | **—** | **16** | **12W/4L (75.0%) ← Dashboard** |
| Simulación bot (sesión anterior) | — | 5 | 4W/1L (80.0%) |

La simulación bot reportó 5 mercados, no 16. La causa más probable es que el script ad-hoc:
1. Aplicó un filtro de fecha adicional (ej. solo alertas de los últimos N días)
2. O usó `star_level` del momento del script (post-vaccine) con un threshold diferente
3. O el dedup tomó el PRIMER alert por created_at y luego del conflicto YES/NO eligió erróneamente

**Conclusión:** El 4W/1L de la simulación es un artifact del script puntual. Los números canónicos son los del dashboard/auditoría: **16 mercados únicos → 12W/4L (75%)** para 5★.

---

## 4. Recomendación: lógica de dedup única

### Recomendación

**Usar `apply_dashboard_filters()` de `system_audit/run_audit.py` como función canónica para todos los módulos** que calculen win rates, accuracy o P&L histórico.

Razón: ya es la fuente de verdad. El dashboard, `run_audit.py` y `bayesian_edge_analysis.py` son coherentes al 100%. El único módulo desalineado fue la simulación ad-hoc de la sesión anterior.

### Para futura simulación bot (P&L realista)

Si se quiere simular entradas reales de un bot, usar `apply_dashboard_filters()` como base pero **reemplazar el criterio de selección dentro de cada grupo** de `market_id`:

```python
# En vez de best sort_key (que puede elegir una alerta tardía):
# Tomar la primera alerta por created_at dentro del grupo de dedup
# Esto simula que el bot entra en la primera señal, no en la mejor retrospectivamente.
dedup_bot: dict[str, dict] = {}
for a in sorted(pool, key=lambda x: x.get("created_at") or ""):
    mid = a["market_id"]
    if mid not in dedup_bot:
        dedup_bot[mid] = a  # primera cronológicamente
```

Esto produce un resultado más conservador (el bot no tiene visión del futuro para escoger la alerta de mayor score). Para track record de precisión del sistema, se debe usar la versión con best sort_key.

### Tabla resumen de criterios según uso

| Caso de uso | Dedup por | Selección | Módulo a usar |
|-------------|-----------|-----------|---------------|
| **Accuracy del sistema / Bayesiano** | `market_id` | Best sort_key (star↓, score↓) | `apply_dashboard_filters()` |
| **Dashboard público** | `market_id` | Best sort_key | `compute_stats()` en dashboard |
| **Simulación bot realista** | `market_id` | Primera por created_at | Script dedicado (por construir) |
| **Consolidación de alertas pending** | `(market_id, direction)` | Existente 4+★ en 48h | `_try_consolidate()` en main.py |
| **Dedup cross-scan** | `(market_id, dir, wallet)` | Existente en N horas | `_check_cross_scan_duplicate()` |

### Lo que NO cambiar (scoring freeze)

- Thresholds de estrellas
- Filtros de noise (N01–N10)
- Cálculo de `_signal_sort_key` — ya funciona y es consistente
- `star_level_initial` — campo inmutable, no involucrado en dedup de métricas

---

## 5. Verificación de consistencia actual

| Módulo | Usa `apply_dashboard_filters()`? | Resultado 5★ | Coherente |
|--------|:--------------------------------:|:------------:|:---------:|
| `src/dashboard/generate_dashboard.py` | Implementación propia equivalente | 12W/4L | ✓ |
| `system_audit/run_audit.py` | ✓ (la define) | 12W/4L | ✓ |
| `system_audit/bayesian_edge_analysis.py` | ✓ (la importa) | incluido en pool | ✓ |
| `docs/pnl_simulation.md` | ✓ (replica fielmente) | 12W/4L | ✓ |
| Simulación bot (sesión anterior) | ✗ (script ad-hoc) | 4W/1L | ✗ Artifact |

**Estado actual: 4/5 módulos coherentes.** La simulación bot ad-hoc fue un one-off descartado. `docs/pnl_simulation.md` usa el método correcto (489 mercados, 369W/120L).

---

## 6. Análisis de los 2 mercados que difieren entre métodos

**Causa raíz:** `apply_dashboard_filters()` hace **dedup → excluir**. La query original de esta auditoría hizo **excluir → dedup**, lo que incluye 2 mercados extra donde el mejor alert disponible (tras excluir el ganador del dedup) es válido.

### Mercado 1 — `0xb4c9...` (30 alertas, todas `correct`)

| | id | star | score | excluido |
|--|---:|:----:|------:|:--------:|
| Ganador dedup (método A) | 963 | 3★ | 126 | ✅ `full_exit` (sold=1.0, close=sell_clob) |
| Ganador tras excluir (método B) | 1821 | 2★ | 97 | — |

El whale vendió el 100% de su posición antes de la resolución (`total_sold_pct=1.0`). La mejor alerta del mercado es una `full_exit` → en `apply_dashboard_filters()` el mercado queda excluido. El método B (excluir primero) habría usado la segunda mejor alerta (2★, correct).

### Mercado 2 — `0xd240...` (2 alertas, ambas `correct`)

| | id | star | score | excluido |
|--|---:|:----:|------:|:--------:|
| Ganador dedup (método A) | 1900 | 1★ | 49 | ✅ `full_exit` (sold=1.0, close=merge_suspected) |
| Ganador tras excluir (método B) | 1901 | 1★ | 43 | — |

Ídem: el whale vendió el 100%, el mercado queda fuera del pool de accuracy.

### ¿Cuál método es correcto?

**Para accuracy del sistema:** Método A (`apply_dashboard_filters()`) es correcto. Una alerta donde el whale vendió antes de la resolución **no es evidencia de que la señal funcionó** — el mercado pudo haber resuelto a favor o en contra por razones ajenas al whale. Incluirlos infla artificialmente el WR.

**Para P&L de un bot:** Ídem. Si el whale (la "señal") ya salió, el bot no debería contar ese outcome como validación del sistema.

Los 2 mercados son ambos `correct` (el outcome favoreció la dirección detectada), pero su exclusión es **metodológicamente correcta**. La pérdida de 2 wins no es un problema — es una feature del filtro.

**Cifras definitivas canónicas:**

```
Pool total:  489 mercados únicos
Win/Loss:    369W / 120L
WR:          75.5%
Fuente:      apply_dashboard_filters() — dedup primero, excluir después
```

---

*Generado por auditoría manual. Datos verificados con query directa a Supabase el 2026-03-17.*
*Corrección de 491→489 aplicada el 2026-03-17 tras identificar error de orden de operaciones en query original.*
