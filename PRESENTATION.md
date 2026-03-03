# Sentinel Alpha
### Sistema de detección de insider trading en mercados de predicción

---

## Qué es

Sentinel Alpha es un sistema automatizado que detecta indicios de insider trading en Polymarket, la mayor plataforma mundial de mercados de predicción con dinero real. Identifica wallets y grupos coordinados que parecen operar con información privilegiada: entran en posiciones con odds favorables antes de que ocurran eventos relevantes, y salen con beneficio cuando el mercado resuelve.

El sistema lleva funcionando de forma continua desde 2025, ha procesado más de 6.800 alertas y acumula un historial validado de 2.447 resoluciones con resultado medible.

---

## El problema

Los mercados de predicción funcionan bajo la premisa de que los participantes agregan información de forma eficiente. Pero un patrón recurrente rompe esa premisa: ciertas wallets acumulan posiciones grandes a odds favorables horas o días antes de que noticias relevantes se hagan públicas — ataques geopolíticos, decisiones gubernamentales, resultados electorales — y cierran con beneficio cuando el evento se confirma.

Este comportamiento replica el insider trading clásico de los mercados financieros, trasladado a un entorno descentralizado y pseudoanónimo. Los actores involucrados no son especuladores con suerte: exhiben patrones sistemáticos — cuentas nuevas fondeadas desde el mismo origen, entradas coordinadas entre múltiples wallets, tamaños de posición inusuales respecto a su historial — que distinguen información privilegiada de ruido.

El reto: detectar a estos actores automáticamente, en tiempo real, a partir de datos de transacciones on-chain crudos, sobre miles de mercados activos simultáneamente.

---

## Cómo funciona

Cada tres horas, Sentinel Alpha analiza los mercados de predicción más activos y evalúa cada wallet con actividad significativa. Cada wallet pasa por **más de 55 filtros heurísticos** organizados en 6 categorías independientes:

- **Señales de wallet** — antigüedad, historial, origen de la cuenta
- **Patrones de comportamiento** — sizing de posición, timing de entrada, señales de convicción
- **Origen de fondos** — trazabilidad on-chain del fondeo de la wallet
- **Anomalías de mercado** — volumen inusual, concentración de liquidez, proximidad a deadline
- **Coordinación entre wallets** — fondeo compartido, entradas sincronizadas entre múltiples cuentas
- **Filtros negativos** — exclusión automática de bots conocidos, arbitrajistas y traders de ruido

Cada señal contribuye puntos a una puntuación compuesta. Las alertas se clasifican en una **escala de 1 a 5 estrellas**, donde los niveles más altos exigen no solo una puntuación mayor sino también validación independiente en múltiples categorías de señal y tamaños mínimos de posición. Una alerta de 4★ o 5★ requiere evidencia simultánea de al menos dos categorías independientes: el sistema está diseñado para que los falsos positivos se concentren en los niveles bajos, no en los altos.

Cuando un mercado resuelve, cada alerta se evalúa automáticamente. Ese historial de outcomes alimenta el conjunto de validación.

Todo el pipeline funciona **24/7 sin intervención humana**, con notificaciones en tiempo real vía Telegram y un dashboard web actualizado cada hora.

---

## Resultados

El sistema ha generado **más de 6.800 alertas** desde su despliegue. De las **2.447 ya resueltas**:

| Métrica | Valor |
|---------|-------|
| Accuracy global (alertas 3★ o superior) | **66.7%** |
| Win rate en segmento de alta convicción (odds < 0.30) | **85–90%** |
| Tests automatizados en el codebase | **910** |

El segmento de odds bajas (< 0.30) es donde la asimetría informacional genera mayor retorno esperado: el mercado pricea el evento al 30% o menos, pero la wallet entra con convicción. Que el win rate en ese segmento alcance el 85–90% no es ruido estadístico.

**Casos reales detectados:**

- **Ataques de Irán sobre Israel** — posiciones acumuladas en múltiples wallets coordinadas horas antes del anuncio público
- **Government shutdown de EE.UU.** — entradas coordinadas a odds favorables antes de que la resolución fuera evidente para el mercado general

---

## Infraestructura

El sistema opera con **coste operativo cero** usando niveles gratuitos de servicios públicos:

- Automatización completa vía **GitHub Actions** — 8 workflows: scans periódicos, tracking de resoluciones, reportes semanales/mensuales, actualización del dashboard
- Alertas en tiempo real por **Telegram** a canales privados
- **Dashboard web** público actualizado en tiempo real (GitHub Pages)
- Historial completo y datos de validación almacenados en **PostgreSQL**

No hay servidores propios, no hay costes de infraestructura fijos.

---

## Roadmap

**Fase 1 — Mayor frecuencia de análisis**
Despliegue en un mini PC local para ejecutar scans cada 10 minutos en lugar de cada 3 horas. Las señales más rápidas (wallets que entran en los 30 minutos previos a un evento) solo son capturables con esa granularidad.

**Fase 2 — Capa de Machine Learning**
El sistema fue diseñado desde el inicio como generador de datos de entrenamiento. Con 2.447 alertas resueltas y más de 55 variables capturadas en el momento de la detección (antes de conocer el outcome), el siguiente paso es entrenar un clasificador para reducir falsos positivos y priorizar automáticamente las señales de mayor edge.

**Fase 3 — Monetización**
Modelo de suscripción para acceso a señales curadas. El feed de alertas 3★+, validado al 66.7% de accuracy con edge documentado en el segmento de baja probabilidad, es un producto de señal comercialmente viable para participantes en mercados de predicción.

---

## Stack técnico

**Python · PostgreSQL · GitHub Actions**

---

*Documento confidencial — uso restringido*
