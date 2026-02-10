# SENTINEL ALPHA — Guía de Implementación Paso a Paso

> Sigue estos pasos EN ORDEN. Cada paso incluye el comando exacto
> para Claude Code. No saltes pasos.
> Si algo falla, copia el error y pégaselo a Claude Code para que lo arregle.

---

## FASE 0: CUENTAS PENDIENTES (en el navegador)

Antes de tocar código, necesitas tener estas cuentas listas con sus keys.
Marca cada una cuando la tengas:

- [ ] **Supabase** — Crear proyecto, copiar URL + anon key + service_role key
- [ ] **Supabase SQL** — Ejecutar TODO el SQL del README.md (sección 5) en SQL Editor
- [ ] **Alchemy** — Crear app Polygon Mainnet, copiar API key
- [ ] **Telegram** — Crear bot con @BotFather, crear canal, añadir bot como admin, obtener chat_id
- [ ] **Twitter/X** — Solicitud de API enviada (puede tardar horas/días en aprobarse)

Cuando tengas las keys, crea tu archivo .env:

```bash
cd "/Users/alexg./Desktop/Pojetct S_A/SA"
cp .env.example .env
nano .env
# Rellena todas las keys reales
# Ctrl+X → Y → Enter para guardar
```

---

## FASE 1: VERIFICAR CONEXIONES (30 min)

### Paso 1.1 — Verificar Supabase

Abre Claude Code:
```bash
cd "/Users/alexg./Desktop/Pojetct S_A/SA"
source venv/bin/activate
claude
```

Dile:
```
Implementa src/database/supabase_client.py completamente. Necesito que:
1. Se conecte a Supabase usando las variables de .env
2. Tenga métodos para insertar y leer de todas las tablas: wallets, markets, alerts, wallet_funding, scans, weekly_reports, smart_money_leaderboard, system_config
3. Tenga un método get_system_config() que lea la tabla system_config
4. Tenga un método test_connection() que intente leer system_config y devuelva True/False

Después de implementarlo, crea un script rápido en scripts/test_connection.py
que haga:
1. Conectar a Supabase
2. Leer system_config
3. Insertar un wallet de prueba
4. Leerlo de vuelta
5. Borrarlo
6. Imprimir "✅ Supabase connection OK" si todo funciona

Ejecuta el script y dime si funciona.
```

Si falla: copia el error y pégaselo a Claude Code.

### Paso 1.2 — Verificar Alchemy (Polygon RPC)

Dile a Claude Code:
```
Implementa src/scanner/blockchain_client.py completamente. Necesito que:
1. Se conecte a Polygon via Alchemy usando la API key de .env
2. Tenga un método get_wallet_age(address) que devuelva la fecha de la primera transacción
3. Tenga un método get_funding_sources(address, max_hops=2) que trace de dónde vienen los fondos
4. Tenga un método is_known_exchange(address) que compare contra una lista hardcodeada de direcciones conocidas de Coinbase, Binance y Kraken en Polygon
5. Tenga un método get_first_tx_contracts(address) que devuelva los contratos con los que interactuó primero (para saber si su primera tx fue Polymarket)

Después crea scripts/test_blockchain.py que:
1. Conecte a Alchemy
2. Consulte la edad de una wallet conocida de Polymarket (busca una en polygonscan.com)
3. Imprima el resultado

Ejecuta y dime si funciona.
```

### Paso 1.3 — Verificar Polymarket API

Dile a Claude Code:
```
Implementa src/scanner/polymarket_client.py completamente. Necesito que:
1. Se conecte a la API CLOB de Polymarket (https://clob.polymarket.com)
2. Tenga un método get_active_markets(categories) que devuelva mercados activos filtrados por categoría
3. Tenga un método get_recent_trades(market_id, minutes=35) que devuelva trades recientes
4. Tenga un método get_market_odds(market_id) que devuelva las odds actuales
5. Tenga un método get_market_orderbook(market_id) para ver si hay limit orders
6. Filtre mercados con odds entre ODDS_MIN y ODDS_MAX de config.py

IMPORTANTE: Investiga primero la documentación de la API de Polymarket CLOB.
La base URL es https://clob.polymarket.com
Endpoints principales:
- GET /markets — lista de mercados
- GET /trades — trades recientes
- GET /book — orderbook

Después crea scripts/test_polymarket.py que:
1. Obtenga 5 mercados activos de política
2. Para cada uno, obtenga los últimos 35 min de trades
3. Imprima: nombre del mercado, odds, número de trades, volumen total

Ejecuta y dime qué mercados encuentra.
```

### Paso 1.4 — Verificar Telegram

Dile a Claude Code:
```
Implementa src/publishing/telegram_bot.py completamente usando requests
(NO python-telegram-bot). Necesito:
1. send_message(text) — enviar texto al canal
2. send_photo(photo_path, caption) — enviar imagen con caption
3. test_connection() — enviar "🔍 Sentinel Alpha — Connection test ✅" al canal

Crea scripts/test_telegram.py que ejecute test_connection().
Ejecuta y dime si llega el mensaje al canal.
```

### Paso 1.5 — Verificar Google News RSS

Dile a Claude Code:
```
Implementa src/scanner/news_checker.py completamente. Necesito:
1. check_news(keywords, hours=24) que busque en Google News RSS
   URL: https://news.google.com/rss/search?q={keywords}&hl=en-US&gl=US&ceid=US:en
2. Devuelva True si hay noticias en las últimas X horas, False si no
3. Devuelva también un resumen del titular más relevante

Crea scripts/test_news.py que busque noticias sobre "Trump president"
y sobre algo muy oscuro como "minister resign Andorra".
Ejecuta y muéstrame los resultados.
```

### Paso 1.6 — Primer commit

Dile a Claude Code:
```
Haz git add de todos los archivos nuevos y modificados.
Haz commit con el mensaje "feat: implement and verify all external connections"
Haz git push.
```

---

## FASE 2: IMPLEMENTAR ANÁLISIS (2-3 horas)

### Paso 2.1 — Wallet Analyzer

```
Implementa src/analysis/wallet_analyzer.py completamente.
Lee config.py para los umbrales y definiciones de filtros.

La clase WalletAnalyzer necesita:
- __init__(self, blockchain_client, db_client)
- analyze(self, wallet_address, trades) → list[FilterResult]

Debe evaluar TODOS estos filtros:
- W01: wallet < 7 días (usa blockchain_client.get_wallet_age)
- W02: 7-14 días
- W03: 14-30 días
- W04: solo 1 mercado en Polymarket
- W05: 2-3 mercados
- W09: primera tx fue en Polymarket (usa blockchain_client.get_first_tx_contracts)
- W11: recibió balance redondo ($5k, $10k, $50k ±1%)
- O01: fondos vienen de exchange conocido (usa blockchain_client.get_funding_sources)
- O02: fondeada hace < 7 días
- O03: fondeada hace < 3 días

Respeta las exclusiones mutuas: solo uno de W01/W02/W03, solo uno de W04/W05, solo uno de O02/O03.
Guarda los funding sources en la DB via db_client para uso posterior en confluence_detector.

Ejecuta los tests: pytest tests/test_wallet_analyzer.py -v
Arregla lo que falle.
```

### Paso 2.2 — Behavior Analyzer

```
Implementa src/analysis/behavior_analyzer.py completamente.

La clase BehaviorAnalyzer necesita:
- __init__(self, db_client)
- analyze(self, wallet_address, trades, market_id, current_odds) → list[FilterResult]

Filtros a implementar:
- B01: 5+ compras en mismo mercado en 24-72h
- B05: solo market orders (no limit)
- B06: tamaño creciente (cada compra > anterior)
- B07: compra contra mercado (odds < 0.20)
- B14: primera compra en PM > $5,000
- B16: 3+ compras en < 4h
- B17: trade entre 2-6 AM UTC
- B18a: $2,000-$3,499 acumulados en 7 días
- B18b: $3,500-$4,999 en 7 días
- B18c: $5,000-$9,999 en 14 días
- B18d: $10,000+ en 14 días
- B18e: acumulación > $2k sin mover precio > 5% (bonus, se suma)
- B19a: entrada $5k-$9,999 de golpe
- B19b: $10k-$49,999
- B19c: $50k+
- B20: wallet > 180 días pero primera actividad PM < 7 días

B18a-d son mutuamente excluyentes. B19a-c mutuamente excluyentes.
B18e es bonus que se suma a cualquier B18.
B19 marca la alerta como "whale_entry" para publicar siempre en Telegram.

Para calcular acumulación necesitas agrupar trades por wallet+market en ventanas de 72h, 7d, 14d.
Para B18e necesitas comparar odds al inicio vs odds al final del periodo de acumulación.

Ejecuta: pytest tests/test_behavior_analyzer.py -v
```

### Paso 2.3 — Market Analyzer

```
Implementa src/analysis/market_analyzer.py completamente.

Filtros:
- M01: volumen 24h > 2x media 7 días (usa datos de la DB o de Polymarket API)
- M02: odds estables > 48h y luego movimiento > 10% (necesita histórico en DB)
- M03: liquidez < $100k

Para M01 y M02 necesitamos histórico. Si no hay suficiente histórico en la DB
todavía (primeros días), el filtro simplemente no se activa (devuelve lista vacía).
Los datos se irán acumulando con cada scan.

Ejecuta tests si los hay, o crea un test básico.
```

### Paso 2.4 — Confluence Detector

```
Implementa src/analysis/confluence_detector.py completamente.
Este es el módulo MÁS IMPORTANTE del proyecto.

Necesita:
- __init__(self, db_client)
- detect(self, market_id, direction, wallets_with_scores) → list[FilterResult]

Filtros:
- C01: 3+ wallets misma dirección en 48h → +25
- C02: 5+ wallets → +40 (mutuamente excluyente con C01)
- C03: 2+ wallets comparten sender en wallet_funding (salto 1-2) → +35
- C04: C03 + apuestan en misma dirección → +50 (mutuamente excluyente con C03)
- C05: 3+ wallets fondeadas desde exchange en < 4 horas + misma dirección → +30
- C06: wallets fondeadas con montos ±30% entre sí → +15 (bonus)
- C07: 1 wallet envía fondos a 3+ wallets que apuestan en PM → +60

Para C03/C04/C05/C06/C07 usa la tabla wallet_funding que se llenó en wallet_analyzer.

Lógica de C07 (red de distribución):
1. Consultar wallet_funding para las wallets activas en este mercado
2. Agrupar por sender_address
3. Si un sender fondeó a 3+ wallets activas → C07 activado
4. Ese sender es la "wallet distribuidora"

Ejecuta: pytest tests/test_confluence.py -v
```

### Paso 2.5 — Noise Filter

```
Implementa src/analysis/noise_filter.py completamente.

Filtros:
- N01: bot (std_dev de intervalos entre trades ≈ 0, umbral < 1 segundo) → -40
- N02: noticias públicas (usa news_checker.check_news con keywords del mercado) → -20
- N05: copy-trading (compra 2-10 min después de whale conocido en mismo mercado) → -25
- N06a: 1-2 mercados no políticos → -5
- N06b: 3-5 mercados variados → -15
- N06c: 6+ mercados de todo tipo → -30

N06a/b/c mutuamente excluyentes. Para determinar "degen score" necesitas el historial
de la wallet en Polymarket: en cuántos mercados participa y de qué categorías son.
```

### Paso 2.6 — Arbitrage Filter

```
Implementa src/analysis/arbitrage_filter.py completamente.

- N03: wallet tiene YES en un mercado y NO en el equivalente → -100 (kill alert)
- N04: posiciones en mercados opuestos (mapeo manual en markets.opposite_market) → 0 puntos pero marcar

Para N03 necesitas cruzar las posiciones de la wallet en mercados relacionados.
Si la tabla markets tiene opposite_market relleno, usar eso.
Si no, intentar detectar por similitud de nombre (básico).
```

### Paso 2.7 — Scoring Engine

```
Revisa src/analysis/scoring.py y asegúrate de que:
1. Suma correctamente todos los filtros
2. Respeta las exclusiones mutuas (no suma W01 y W02 a la vez)
3. Aplica el multiplicador más alto que aplique de los 6 perfiles
4. Calcula estrellas correctamente según los rangos del config.py
5. Devuelve: score_raw, multiplier, score_final, star_level

Ejecuta TODOS los tests: pytest tests/ -v
Arregla todo lo que falle.
```

### Paso 2.8 — Commit

```
Haz git add, commit "feat: implement all 42 filters and scoring engine", push.
```

---

## FASE 3: IMPLEMENTAR PUBLICACIÓN (1-2 horas)

### Paso 3.1 — Formatter

```
Implementa src/publishing/formatter.py completamente.

Necesita métodos:
- format_x_alert(alert: Alert) → str  (máx 280 chars, sin filtros, con emojis)
- format_telegram_alert(alert: Alert) → str (más detalle, incluye score)
- format_whale_entry(alert: Alert) → str (formato especial para B19)
- format_x_resolution(alert: Alert) → str
- format_telegram_resolution(alert: Alert) → str
- format_opposing_positions(alert: Alert) → str

Los formatos están definidos en el README.md sección 8.
NUNCA revelar filtros, scoring ni metodología en los formatos públicos.
Solo mostrar: mercado, odds, cantidad, wallets, estrellas.
Telegram puede incluir score numérico.
```

### Paso 3.2 — Twitter Bot

```
Implementa src/publishing/twitter_bot.py completamente.

Necesita:
- __init__(self) — conectar con tweepy usando keys de .env
- publish_alert(self, alert: Alert) → tweet_id
- publish_resolution(self, alert: Alert) → tweet_id
- get_tweets_today(self) → int (para respetar límite de 10/día)
- can_publish(self) → bool

Si la API de Twitter aún no está aprobada, que el bot logee
"Twitter API not available, skipping" sin crashear.
```

### Paso 3.3 — Telegram Bot (ya debería estar del paso 1.4)

```
Revisa src/publishing/telegram_bot.py y añade:
- publish_alert(self, alert: Alert) → message_id
- publish_whale_entry(self, alert: Alert) → message_id
- publish_resolution(self, alert: Alert) → message_id
- publish_report(self, text: str, chart_path: str = None) → message_id

Usa formatter.py para generar el texto.
```

### Paso 3.4 — Commit

```
Haz git add, commit "feat: implement publishing (X + Telegram + formatter)", push.
```

---

## FASE 4: IMPLEMENTAR ORQUESTADOR (1-2 horas)

### Paso 4.1 — Main.py

```
Implementa src/main.py completamente. Este es el orquestador que ejecuta
el ciclo completo de escaneo.

Flujo:
1. Cargar config y conectar a todos los servicios
2. Leer system_config → si scan_enabled != 'true', exit
3. Obtener mercados activos de Polymarket (política, economía, geopolítica)
4. Para cada mercado:
   a. Obtener trades últimos 35 min
   b. Filtrar tx < $100
   c. Agrupar por wallet
   d. Calcular acumulado por wallet+mercado en ventanas 72h, 7d, 14d
   e. Si acumulado > $350 → analizar wallet:
      - Ejecutar wallet_analyzer.analyze()
      - Ejecutar behavior_analyzer.analyze()
      - Ejecutar noise_filter.analyze()
      - Ejecutar arbitrage_filter.analyze()
      - Si N03 activado (-100) → descartar wallet
5. Para cada mercado con wallets analizadas:
   a. Ejecutar market_analyzer.analyze()
   b. Ejecutar confluence_detector.detect()
   c. Ejecutar scoring.calculate_score() con todos los filtros
6. Verificar odds range
7. Generar alertas:
   - Guardar todas en DB
   - 2+ estrellas → Telegram
   - 3+ estrellas → X (si can_publish)
   - B19 → Telegram whale entry
8. Registrar scan en DB

Manejo de errores: si algo falla, loggear y continuar con el siguiente
mercado/wallet. No parar todo el scan por un error individual.
Guardar errores en tabla scans.

El script debe ser ejecutable como: python -m src.main
```

### Paso 4.2 — Test manual

```
Ejecuta python -m src.main y dime qué pasa.
Debería:
1. Conectar a Supabase ✅
2. Leer system_config ✅
3. Obtener mercados de Polymarket
4. Obtener trades
5. Analizar wallets (si hay alguna que pase el umbral)
6. Generar alertas (si hay alguna)
7. Registrar scan en DB

Si no genera alertas es normal (puede no haber actividad).
Lo importante es que no crashee y que registre el scan.
```

### Paso 4.3 — Commit

```
Haz git add, commit "feat: implement main orchestrator", push.
```

---

## FASE 5: REPORTES Y GRÁFICAS (1-2 horas)

### Paso 5.1 — Chart Generator

```
Implementa src/publishing/chart_generator.py.
Genera gráficas con matplotlib:
1. alerts_by_day(alerts, output_path) — barras por día, coloreadas por estrellas
2. accuracy_over_time(reports, output_path) — línea de accuracy acumulada
3. star_distribution(alerts, output_path) — pie chart de distribución de estrellas
Guarda como PNG en /tmp/ para enviar por Telegram.
```

### Paso 5.2 — Weekly Report

```
Implementa src/reports/weekly.py completamente.
1. Consultar alertas de la última semana desde Supabase
2. Calcular: total, por estrellas, correctas, incorrectas, pendientes
3. Calcular accuracy por nivel de estrellas
4. Generar gráfica con chart_generator
5. Publicar en X (texto) y Telegram (texto + imagen)
6. Guardar reporte en tabla weekly_reports

Ejecutable como: python -m src.reports.weekly
```

### Paso 5.3 — Monthly Report

```
Implementa src/reports/monthly.py. Igual que weekly pero para el mes completo.
Añade: leaderboard de wallets, PnL estimado, evolución semanal.
Ejecutable como: python -m src.reports.monthly
```

### Paso 5.4 — Commit

```
Haz git add, commit "feat: implement reports and chart generation", push.
```

---

## FASE 6: GITHUB ACTIONS Y LANZAMIENTO (30 min)

### Paso 6.1 — Verificar workflows

```
Lee los 3 archivos de .github/workflows/ y verifica que:
1. scan.yml ejecuta python -m src.main
2. weekly_report.yml ejecuta python -m src.reports.weekly
3. monthly_report.yml ejecuta python -m src.reports.monthly
4. Todos pasan las env vars correctas desde secrets
```

### Paso 6.2 — Configurar GitHub Secrets

En el navegador: GitHub → repo sentinel-alpha → Settings → Secrets → Actions.
Añadir TODOS estos secrets (copia los valores de tu .env):

```
SUPABASE_URL
SUPABASE_KEY
ALCHEMY_API_KEY
TWITTER_API_KEY
TWITTER_API_SECRET
TWITTER_ACCESS_TOKEN
TWITTER_ACCESS_SECRET
TELEGRAM_BOT_TOKEN
TELEGRAM_CHANNEL_ID
```

### Paso 6.3 — Test manual del workflow

En GitHub → Actions → Sentinel Alpha Scan → Run workflow (botón manual).
Espera a que termine. Revisa los logs. Si falla, copia el error y arréglalo.

### Paso 6.4 — Publicar primer tweet pinneado

```
Crea scripts/first_tweet.py que publique el tweet pinneado:

"In the last year, anonymous wallets have made millions
betting on political events on Polymarket — hours before
the news broke.

The data is public. Nobody was watching.

Now someone is.

🔍 Sentinel Alpha is live.

Automated smart money detection.
Public track record. No opinions, just data.

📩 Telegram: t.me/SentinelAlphaChannel

#Polymarket #SmartMoney #Crypto"

Y el mismo mensaje al canal de Telegram.
Ejecuta el script.
Luego ve a X y pinnea ese tweet manualmente.
```

### Paso 6.5 — Commit final

```
Haz git add, commit "feat: launch ready — all systems operational", push.
```

---

## FASE 7: WEB PÚBLICA (cuando quieras, no urgente)

### Paso 7.1 — Crear repo público

```bash
# Fuera de Claude Code, en terminal normal
cd "/Users/alexg./Desktop/Pojetct S_A"
mkdir sentinel-alpha-web
cd sentinel-alpha-web
git init
gh repo create sentinel-alpha-web --public --source=. --push
```

### Paso 7.2 — Dashboard

Dile a Claude Code (o Claude Web):
```
Crea un dashboard web en HTML + Tailwind CSS + Chart.js + Supabase JS.
Archivos: index.html, alerts.html, leaderboard.html, css/style.css,
js/config.js, js/dashboard.js, js/charts.js.

config.js solo tiene SUPABASE_URL y SUPABASE_ANON_KEY (lectura).
El dashboard lee system_config: si status=maintenance muestra mensaje,
si status=countdown muestra cuenta atrás, si status=active muestra dashboard.

Página principal: últimas alertas, accuracy, contador.
Alerts: tabla filtrable por estrellas y fecha.
Leaderboard: top smart money wallets.
```

### Paso 7.3 — GitHub Pages

GitHub → sentinel-alpha-web → Settings → Pages → Source: main → root → Save.

---

## RESUMEN DE FASES

| Fase | Qué | Tiempo estimado |
|---|---|---|
| 0 | Cuentas y .env | 30 min |
| 1 | Verificar conexiones | 30 min |
| 2 | Implementar análisis (42 filtros) | 2-3 horas |
| 3 | Implementar publicación | 1-2 horas |
| 4 | Orquestador main.py | 1-2 horas |
| 5 | Reportes y gráficas | 1-2 horas |
| 6 | GitHub Actions + lanzamiento | 30 min |
| 7 | Web pública | 1-2 horas (no urgente) |

**Total estimado: 8-12 horas de trabajo**

---

## TIPS PARA CLAUDE CODE

- Si Claude Code se confunde, dile: "Lee el README.md y el archivo X antes de editarlo."
- Si algo falla, copia el error completo y pégaselo.
- Si quieres ver qué hay en un archivo: "Muéstrame el contenido de src/config.py"
- Si quieres que ejecute algo: "Ejecuta pytest tests/ -v y dime qué pasa"
- Si quieres reanudar sesión: `claude --resume`
- Cada vez que termines una fase, haz commit y push.
- Si Claude Code intenta hacer algo raro, dile "Para. Lee el README.md primero."

---

## SI LA API DE TWITTER NO ESTÁ APROBADA

No te preocupes. El sistema funciona sin Twitter. Las alertas van a:
1. Base de datos (siempre)
2. Telegram (desde 2 estrellas)
3. Web pública (desde 1 estrella)

Twitter es un canal extra. Cuando te aprueben la API, solo tienes que
rellenar las keys en .env y GitHub Secrets y ya publicará automáticamente.
El código ya maneja el caso de "Twitter no disponible" sin crashear.

---

*Sigue estos pasos en orden y tendrás Sentinel Alpha operativo.*
