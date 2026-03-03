# Sentinel Alpha
### Sistema de inteligencia on-chain para mercados de predicción

---

## Qué es

Sentinel Alpha es un sistema automatizado de inteligencia que detecta indicios de insider trading en Polymarket, la mayor plataforma mundial de mercados de predicción con dinero real. Monitorea en tiempo real el comportamiento de miles de wallets on-chain, identificando patrones de trading que sugieren acceso a información privilegiada: quién entra antes de que las noticias sean públicas, cuánto apuesta, y si lo hace coordinado con otros actores.

El sistema lleva operando de forma continua desde febrero de 2026. Ha procesado más de 6.800 señales y acumula 2.447 resoluciones con resultado verificable.

---

## El problema

Polymarket mueve más de $2.000 millones de volumen mensual en mercados sobre eventos políticos, económicos y geopolíticos. Su premisa es que los mercados agregan información eficientemente — pero esta premisa se rompe cuando algunos actores operan con información que el resto del mercado no tiene.

No existe regulación efectiva ni herramientas públicas que detecten este comportamiento de forma sistemática. Los competidores existentes (Polysights, Whalescreen) ofrecen filtros básicos de actividad sin profundidad analítica ni validación estadística. El resultado: el insider trading en prediction markets pasa desapercibido, el edge no se cuantifica, y los participantes ordinarios no tienen forma de detectarlo.

---

## Cómo funciona

Cada tres horas, Sentinel Alpha analiza automáticamente los mercados más activos de Polymarket y evalúa el comportamiento de cada wallet con actividad significativa. El proceso combina análisis on-chain de la blockchain Polygon con datos de trades del mercado de predicción.

Cada wallet pasa por **más de 55 señales heurísticas** organizadas en 6 categorías independientes:

- **Señales de wallet** — antigüedad, historial de trading, comportamiento en mercados anteriores
- **Patrones de comportamiento** — tamaño de posición, timing de entrada, nivel de convicción
- **Trazabilidad de fondos** — origen on-chain del capital (exchanges, bridges, wallets intermediarias)
- **Anomalías de mercado** — volumen inusual, concentración de liquidez, proximidad a resolución
- **Coordinación entre wallets** — fondeo compartido, entradas sincronizadas de múltiples cuentas
- **Filtros negativos** — exclusión automática de bots, arbitrajistas y traders de ruido conocidos

Las señales se combinan en una puntuación compuesta. Las alertas se clasifican en una **escala de 1 a 5 estrellas** donde los niveles más altos requieren evidencia convergente de múltiples categorías independientes simultáneamente. Una alerta 5★ no es simplemente un score alto — requiere que al menos tres familias de señal apunten en la misma dirección al mismo tiempo, con posición mínima verificable.

Cuando un mercado resuelve, cada alerta se evalúa automáticamente. Ese historial de outcomes retroalimenta el conjunto de validación y, en el futuro, el training set de ML. Todo el pipeline funciona **24/7 sin intervención humana**, con 910 tests automatizados que cubren escenarios reales de insider trading y falsos positivos.

---

## Resultados

El sistema ha generado **más de 6.800 señales** desde su lanzamiento. De las **2.447 ya resueltas**:

| Métrica | Valor |
|---------|-------|
| Accuracy global (alertas 3★ o superior) | **66.7%** |
| Win rate en zona de alta convicción (precio efectivo ≥ 0.70) | **80.7%** |
| Edge estadístico sobre probabilidad implícita del mercado | **Z = +4.7σ** |

El segmento de alta convicción — donde el mercado subestima significativamente la probabilidad del evento — es donde la asimetría informacional genera el mayor retorno. Un win rate del 80.7% con Z = +4.7σ no es ruido estadístico: es evidencia de que el sistema identifica información real.

**Casos documentados:**

**Government shutdown (13 febrero 2026):** Múltiples alertas 5★ detectaron wallets coordinadas apostando NO con fondeo compartido desde el mismo intermediario, entradas sincronizadas horas antes de que la resolución fuera evidente para el mercado. Todas correctas.

**Supreme Court — tariffs:** Wallet con 4 años de antigüedad que nunca había usado Polymarket deposita $41.569 en una sola operación apostando en contra del consenso. Patrón clásico de capital institucional activado por información específica. Correcta.

**Iran strikes (10 febrero 2026):** Caso de estudio de falso positivo gestionado correctamente. Wallets nuevas coordinadas, pero el sistema penalizó automáticamente al detectar cobertura mediática pública simultánea — reduciendo el score a alerta menor en lugar de 5★. Validó el mecanismo de discriminación entre insider trading real y reacción a noticias públicas.

---

## Evolución

| Período | Estado |
|---------|--------|
| **Febrero 2026** | MVP con 15 filtros, scans manuales de 35+ minutos. Primera alerta real el día del lanzamiento. En 3 semanas: deduplicación entre scans, modo automático, 12 variables de training data frozen en T0. |
| **Marzo 2026** | 55+ filtros activos, 910 tests automatizados, automatización completa 24/7, pipeline de datos para ML listo. 2.447 alertas resueltas en el training set. |
| **Abril 2026** | Bot de trading en shadow mode → live con capital semilla. |
| **Mayo–Julio 2026** | ML training: clasificador para filtrar falsos positivos + predictor de calidad de señal. Modelo Forex que correlaciona prediction markets con movimientos de divisas. |
| **2027** | Escalado a clientes externos o licenciamiento institucional. |

---

## Modelo de negocio

**Fase 1 — Trading propio**
Operar con capital propio usando las señales del sistema. Las alertas de alta convicción (precio efectivo ≥ 0.70) ofrecen un edge demostrado de +6.8% sobre la probabilidad implícita del mercado.

**Fase 2 — Suscripciones**
El feed de alertas validado — 66.7% de accuracy global, 80.7% en zona óptima, edge estadísticamente significativo — es un producto de señal comercialmente viable para participantes activos en mercados de predicción.

**Fase 3 — Licenciamiento institucional**
Acceso al modelo ML entrenado o a la infraestructura completa para fondos cuantitativos interesados en señales de prediction markets como leading indicators de eventos macroeconómicos.

La infraestructura actual opera con un coste fijo de ~$30/mes y está diseñada para escalar sin coste marginal relevante. La migración planificada a hardware propio eliminará prácticamente todos los costes variables.

---

## Por qué es diferente

| Aspecto | Sentinel Alpha | Competidores |
|---------|---------------|--------------|
| Profundidad de análisis | 55+ señales en 6 categorías, trazabilidad on-chain de varios saltos | Filtros básicos de actividad de wallets |
| Validación estadística | 2.447 outcomes verificados, Z = +4.7σ | Sin validación publicada |
| Diseño para ML | 12 variables T0 frozen en cada alerta desde el primer día | Datos no estructurados para ML |
| Coste operativo | ~$30/mes, migrando a coste mínimo con hardware propio | Modelos de suscripción |
| Propiedad intelectual | Código privado, lógica no replicable públicamente | Herramientas abiertas o cerradas sin validación |

---

## Stack técnico

**Python · PostgreSQL · GitHub Actions · Machine Learning** (en desarrollo)

---

*Documento confidencial — uso restringido*
