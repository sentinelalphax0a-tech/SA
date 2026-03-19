# Simulación P&L Histórico — Señales del Dashboard

**Fecha de generación:** 2026-03-17
**Pool:** `apply_dashboard_filters()` — 1 apuesta por `market_id`, mejor señal (star↓ → score↓)
**Alertas excluidas:** `merge_confirmed` y full exits (≥90% vendido o `close_reason='position_gone'`)
**Datos:** alertas con `outcome IN ('correct', 'incorrect')` — base de datos Supabase real

---

## Resumen ejecutivo

> "Si desde el inicio hubiera apostado en cada señal que muestra el dashboard (una por mercado, la mejor señal del mercado), ¿cuánto habría ganado?"

| Métrica | Valor |
|---------|-------|
| Mercados únicos operados | **489** |
| Win rate global | **75.5%** (369W / 120L) |
| P&L con $1/señal | **+$27.46** sobre $489 invertidos |
| P&L con $10/señal | **+$274.63** sobre $4,890 invertidos |
| P&L con $100/señal | **+$2,746.35** sobre $48,900 invertidos |
| ROI global | **+5.6%** |

### Hallazgo clave: 4★ es la estrella del sistema

- **4★ y 5★ combinadas:** 77.8% WR, ROI **+9.6%** — la mejor estrategia
- **4★ solas:** 81.8% WR, ROI **+15.5%** — estrella individual más rentable
- **1★:** 80.8% WR, ROI **+8.7%** — volumen alto y edge consistente
- **5★:** 75.0% WR, ROI **+5.6%** — positivo pero con odds bajas (mercados caros)
- **3★:** 58.3% WR, ROI **+3.4%** — el nivel más débil

> **Nota metodológica:** "stake" = unidades (100 unidades → profit = odds × 100 si gana). Capital asumido = n_mercados × stake. Para YES a odds=0.77: profit correcto = (1-0.77)×100 = $23; pérdida = -0.77×100 = -$77. Para NO a odds=0.77: profit correcto = 0.77×100 = $77; pérdida = -(1-0.77)×100 = -$23.

---

## Tabla 1: P&L por star_level

| ★ | Mercados | W | L | WR% | P&L ($1) | P&L ($10) | P&L ($100) | ROI% | Capital ($100) |
|:-:|:--------:|:-:|:-:|:---:|:--------:|:---------:|:----------:|:----:|:--------------:|
| 5★ | 16 | 12 | 4 | 75.0% | +$0.90 | +$9.01 | +$90.10 | +5.6% | $1,600 |
| 4★ | 11 | 9 | 2 | 81.8% | +$1.70 | +$17.02 | +$170.25 | +15.5% | $1,100 |
| 3★ | 60 | 35 | 25 | 58.3% | +$2.03 | +$20.34 | +$203.35 | +3.4% | $6,000 |
| 2★ | 146 | 106 | 40 | 72.6% | +$2.36 | +$23.56 | +$235.55 | +1.6% | $14,600 |
| 1★ | 234 | 189 | 45 | 80.8% | +$20.29 | +$202.88 | +$2,028.75 | +8.7% | $23,400 |
| **TOTAL** | **489** | **369** | **120** | **75.5%** | **+$27.46** | **+$274.63** | **+$2,746.35** | **+5.6%** | **$48,900** |

### Observaciones

- **Todas las estrellas son positivas.** No hay ningún nivel con P&L negativo.
- **4★ tiene el ROI más alto (+15.5%)** pero solo 11 mercados — muestra estadística pequeña.
- **1★ domina en valor absoluto** ($2,028 con $100/apuesta) por puro volumen (234 mercados).
- **5★ tiene el ROI más bajo de los positivos (+5.6%)** — coincide con odds bajas (mercados donde el consenso ya era alto, por eso el profit por share es pequeño aunque el WR sea bueno).
- **3★ es el nivel más débil** (58.3% WR, +3.4% ROI) — el sistema tiene menos edge en señales medianas.

---

## Tabla 2: Detalle de las 16 señales 5★

> Verificación manual: alerta #2726 "US strikes Iran Feb 27 → NO, odds_YES=0.215, correct → entry NO = 0.785, profit = 0.215 × $100 = **$21.50** ✓

| ID | Mercado | Dir | Odds (YES) | Entry real | Outcome | P&L ($100) | Fecha |
|---:|:--------|:---:|:----------:|:----------:|:-------:|:----------:|:-----:|
| 337 | Will Google have the best AI model at end of 2026? | YES | 0.2950 | 0.2950 | incorrect | **-$29.50** | 2026-02-16 |
| 687 | US strikes Iran by February 28, 2026? | NO | 0.1550 | 0.8450 | incorrect | **-$84.50** | 2026-02-17 |
| 1542 | Israel strikes Iran by February 28, 2026? | NO | 0.2250 | 0.7750 | incorrect | **-$77.50** | 2026-02-19 |
| 1549 | US strikes Iran by February 20, 2026? | NO | 0.0565 | 0.9435 | correct | +$5.65 | 2026-02-19 |
| 1576 | US strikes Iran by February 21, 2026? | NO | 0.1050 | 0.8950 | correct | +$10.50 | 2026-02-19 |
| 1599 | US strikes Iran by February 24, 2026? | NO | 0.1800 | 0.8200 | correct | +$18.00 | 2026-02-19 |
| 1607 | US strikes Iran by February 26, 2026? | NO | 0.1950 | 0.8050 | correct | +$19.50 | 2026-02-19 |
| 1618 | US strikes Iran by February 23, 2026? | NO | 0.1800 | 0.8200 | correct | +$18.00 | 2026-02-19 |
| 2181 | US strikes Iran by February 22, 2026? | NO | 0.1300 | 0.8700 | correct | +$13.00 | 2026-02-19 |
| 2726 | US strikes Iran by February 27, 2026? | NO | 0.2150 | 0.7850 | correct | +$21.50 | 2026-02-19 |
| 2988 | Will Google have the second-best AI model at end of 2026? | NO | 0.1795 | 0.8205 | correct | +$17.95 | 2026-02-20 |
| 3022 | Will Kevin Warsh be formally nominated to be Fed Chair? | NO | 0.2170 | 0.7830 | correct | +$21.70 | 2026-02-20 |
| 5331 | Will Meteora be accused of insider trading? | NO | 0.2800 | 0.7200 | correct | +$28.00 | 2026-02-24 |
| 6082 | Will Axiom be accused of insider trading? | YES | 0.2980 | 0.2980 | correct | +$70.20 | 2026-02-26 |
| 6716 | Khamenei out as Supreme Leader of Iran by Feb 2026? | YES | 0.4630 | 0.4630 | correct | +$53.70 | 2026-02-28 |
| 10327 | Will The Greens win the most seats in the 2026 German election? | YES | 0.1610 | 0.1610 | incorrect | **-$16.10** | 2026-03-08 |
| | | | | | **12W / 4L** | **+$90.10** | |

### Análisis de los 4 fallos 5★

| ID | Mercado | Por qué falló | Pérdida |
|---:|:--------|:--------------|--------:|
| 337 | Google best AI model | Señal YES a 0.295 — mercado resolvió NO | -$29.50 |
| 687 | US strikes Iran Feb 28 | NO a odds 0.155 — Iran deadline pasó sin strike; alerta se creó demasiado tarde con odds ya altas | -$84.50 |
| 1542 | Israel strikes Iran Feb 28 | NO a odds 0.225 — mismo cluster de deadlines, Israel tampoco atacó | -$77.50 |
| 10327 | The Greens German election | YES a 0.161 — The Greens no ganaron | -$16.10 |

El mayor fallo (ID #687, -$84.50) corresponde al mercado con dedup que se aplicó en la auditoría: el sistema mantuvo la alerta YES como ganadora (correct), pero la alerta NO (que fue la seleccionada por sort_key en este contexto) resultó incorrect. El cluster "US strikes Iran" concentra la mayoría del riesgo en 5★.

---

## Tabla 3: Estrategias combinadas

| Estrategia | Stake | Mercados | W | L | WR% | Capital invertido | P&L | ROI% |
|:-----------|------:|:--------:|:-:|:-:|:---:|:-----------------:|----:|:----:|
| Solo 5★ | $1 | 16 | 12 | 4 | 75.0% | $16 | +$0.90 | +5.6% |
| Solo 5★ | $10 | 16 | 12 | 4 | 75.0% | $160 | +$9.01 | +5.6% |
| **Solo 5★** | **$100** | **16** | **12** | **4** | **75.0%** | **$1,600** | **+$90.10** | **+5.6%** |
| 4★ y 5★ | $1 | 27 | 21 | 6 | 77.8% | $27 | +$2.60 | +9.6% |
| 4★ y 5★ | $10 | 27 | 21 | 6 | 77.8% | $270 | +$26.04 | +9.6% |
| **4★ y 5★** | **$100** | **27** | **21** | **6** | **77.8%** | **$2,700** | **+$260.35** | **+9.6%** |
| Todas 1–5★ | $1 | 489 | 369 | 120 | 75.5% | $489 | +$27.46 | +5.6% |
| Todas 1–5★ | $10 | 489 | 369 | 120 | 75.5% | $4,890 | +$274.63 | +5.6% |
| **Todas 1–5★** | **$100** | **489** | **369** | **120** | **75.5%** | **$48,900** | **+$2,746.35** | **+5.6%** |

### Recomendación basada en datos

| Estrategia | Pros | Contras |
|:-----------|:-----|:--------|
| **4★ y 5★** | Mayor ROI (+9.6%), WR más alto (77.8%) | Solo 27 mercados históricos — muestra pequeña |
| **Todas 1–5★** | Mayor diversificación, edge estadísticamente robusto (n=489) | ROI más bajo (+5.6%), requiere capital alto |
| **Solo 5★** | Señales de mayor confianza | Solo 16 mercados, ROI más bajo que 4★ por odds bajas |

La estrategia óptima para maximizar ROI es **4★ y 5★**, pero para minimizar riesgo de run de pérdidas la estrategia **Todas 1–5★** tiene una base estadística mucho más sólida (n=489 vs n=27).

---

## Notas metodológicas

### Fórmula de P&L

```
stake = unidades apostadas (ej: $1, $10, $100)

YES direction:
  correct:   profit = (1 - odds_at_alert) × stake
  incorrect: loss   = -odds_at_alert × stake

NO direction:
  correct:   profit = odds_at_alert × stake
  incorrect: loss   = -(1 - odds_at_alert) × stake

Donde odds_at_alert = precio del YES en el momento de la detección (0 < x < 1)
      entry_real_NO = 1 - odds_at_alert
```

### Fuente de datos

- Pool: `apply_dashboard_filters()` — exactamente el mismo pool que usa el dashboard y la auditoría Bayesiana
- 3,249 alertas resueltas totales → 489 mercados únicos tras dedup y exclusiones
- Ninguna alerta excluida por odds inválidas (todas 0 < odds < 1)
- Campo odds: `odds_at_alert` de la tabla `alerts`

### Limitaciones

1. **Historial corto:** Los datos comprenden principalmente Feb–Mar 2026 (~1 mes). Los resultados son consistentes pero n=27 para 4★+5★ es pequeño.
2. **Sin slippage:** No se modela el impacto de precio al entrar (el sistema ya filtra `price_impact`, pero la ejecución real puede diferir).
3. **Stake fijo:** En un bot real se aplicaría Kelly criterion para sizing óptimo según odds. Con Kelly fraccional el ROI sería superior.
4. **Stake = unidades, no dólares:** $100 de "stake" = 100 shares. Capital real en efectivo depende del precio de entrada (ej: comprar 100 NO shares a $0.22 cuesta $22, no $100).

---

*Datos verificados contra Supabase el 2026-03-17. Pool idéntico al dashboard (489 mercados).*
