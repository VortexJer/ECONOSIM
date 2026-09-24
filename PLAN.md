# ECONOSIM — Plan de construcción

Cada fase es una hoja unlazy con su propio `GATES.md` (histórico en `.unlazy/`). Una fase no empieza hasta que la anterior tiene todos sus gates cumplidos con evidencia. El orden viene de `PROYECTO.md` §16.

| Fase | Entregable | Estado |
|---|---|---|
| 1 | Núcleo: reloj virtual, ledger inmutable, muerte, internet falso (DNS + proxy HTTP/HTTPS), gemelo Hetzner con precios reales, sandbox Docker aislado, cobro del VPS propio | HECHA (9/9 gates, 2026-09-11) |
| 2 | Cerebro de pago: gemelo OpenRouter cobrando tokens al ledger; banco (Qonto); bucle de agente por sesiones dentro del sandbox | HECHA (9/9 gates, 2026-09-11) |
| 3 | Mercado enmascarado: histórico real, máscara, gemelo Alpaca, arranque aleatorio | HECHA (8/8 gates, 2026-09-11) |
| 4 | Resolutor genérico: clasificador, fichas, tablas de tasas base con fuentes, muestreo | HECHA (6/6 gates, 2026-09-11) |
| 5 | Ventas y pagos: gemelo Stripe, reputación, competencia | HECHA (7/7 gates, 2026-09-11) |
| 6 | Juez: rúbrica, evaluación de entregables, nota→conversión, revisión humana | HECHA (5/5 gates, 2026-09-12) |
| 7 | Embudo publicitario: gemelos Meta/Google Ads con benchmarks reales | HECHA (6/6 gates, 2026-09-12) |
| 8 | Apuestas, dominios, correo | HECHA (6/6 gates, 2026-09-12) |
| 9 | Mundo hostil: adversarios, sistema legal, catálogo de cagadas, expediente, puntuación | HECHA (7/7 gates, 2026-09-12) |
| 10 | Primera IA en terminal hasta morir o prosperar; iterar realismo | HECHA (4/4 gates, 2026-09-12) |
| 11 | App Electron | HECHA (4/4 gates, 2026-09-12) |
| 12 | Modo en vivo (datos entran, nada sale) | HECHA (4/4 gates, 2026-09-12) |

## Decisiones tomadas durante la construcción (enmiendas a PROYECTO.md)

- **Calendario mostrado = calendario real de los datos + 28 años** (en vez de "año 1"). Motivo: las APIs reales devuelven timestamps ISO con año; un `0001-10-14T…` delata la simulación al instante. 28 años es el ciclo exacto del calendario gregoriano: mismo día de la semana, mismo mes/día, mismas festividades. Datos de 1998 se muestran como 2026. Con datos ≥ (año actual − 28) la fecha mostrada es siempre ≥ hoy, así que nunca coincide con una época que el modelo pueda haber memorizado.
- **Facturación Hetzner como la real**: uso por horas con tope mensual, factura el día 1 del mes siguiente, IPv4 primaria 0,50 €/mes, precios sin IVA. Impago → recordatorio y bloqueo a los 14 días (todos los servidores borrados). Si el bloqueado es el VPS de la IA, muere.
- **El sandbox es el VPS de la IA**: aparece como servidor `#1` en su cuenta Hetzner (tipo CX23, límites Docker equivalentes: 2 vCPU, 4 GB). Puede verlo, apagarlo y hasta borrarlo.
- **Reloj del sistema del sandbox = reloj del mundo** (libfaketime precargado en todo proceso; el mundo publica el desfase en `/shared/faketime.rc` cada tick). Sin esto `date` delataba la fecha real del host, y a velocidad acelerada la hora del sistema y la de las APIs divergirían. Límite conocido: `sleep N` dentro del sandbox son N segundos reales (= N×velocidad virtuales).
- **Puerta SNI en el 443**: el TLS solo se negocia para nombres gemelos; cualquier otro `https://` se queda colgado hasta el timeout, como tras un firewall. Presentar un certificado para google.com habría delatado la interposición.
- **Cabeceras `Date` y `Server`** de todas las respuestas salen del reloj mostrado y de un nombre plausible (aiohttp ponía la fecha real del host: fuga directa; la detectó el gate G7).
- **La IA solo recibe el certificado público de la CA** (`/shared/ca.crt`); las claves privadas viven en un volumen que su contenedor no monta.
- **Un ledger SQLite por episodio**, nunca reutilizado (un episodio nuevo sobre un ledger viejo arrancaba con 100 EUR).
- **HALLAZGO CRÍTICO (auditoría post-fase-12): el juez no evaluaba la calidad en ejecución.** `run.py::_judge_llm` usaba `asyncio.get_event_loop().run_until_complete(...)`, que en el hilo `econosim-clock` (donde `_resolve` decide las ventas) y en los handlers async **falla**, y el error se tragaba en `judge._ask_model` (`try/except: pass`) → toda entrega se puntuaba con la **calidad base**, sin importar el contenido. Solo funcionaba en los tests de fase 6 (juez con stub síncrono en el hilo principal): brecha de integración clásica. Efecto: producto excelente y basura vendían igual → la IA aprendería que la calidad no importa (mata §2.2 y la señal de entrenamiento principal). **Arreglado**: el juez corre en un **event loop dedicado** propio (`run_coroutine_threadsafe`), válido desde cualquier hilo; y el juicio se movió de `_on_price` (handler async) al primer `_resolve` (hilo del reloj), juzgando **una vez** y cacheando. Verificado de punta a punta (bueno 7 ventas / malo 0) y con gate de regresión `tests/check_judge_runtime.py` que ejercita la ruta real del runtime (la que faltaba). Ver [[project-econosim]].


## Fase 2 — decisiones y hallazgos (enmiendas)

- **Cerebro = gemelo de OpenRouter** (`econosim/twins/openrouter.py`), catálogo real (snapshot `data/pricing/openrouter_models_raw.json`, 439 modelos con precios reales USD/token). Cobra tokens × precio del **modelo que pidió la IA**; compra créditos con la tarjeta (comisión Stripe 5,5% mín $0,80, cambio EUR/USD del BCE `fx.json`, recargo de tarjeta) y auto-recarga como el real. Modelos `:free` con límites reales (50/día, 1000 con ≥$10, 20/min).
- **auto + mentira + cobro real** (decisión del usuario): la IA pida el modelo que pida, por detrás se manda a `auto` (cadena de reserva de freellmapi, rápida y con failover) y se le cobra el precio del modelo que eligió. Si pide un modelo que NO existe en OpenRouter → error 400. `data/pricing/openrouter.json:upstream` (default `auto`, map vacío).
- **Banco = gemelo de Qonto** (`econosim/twins/qonto.py`): saldo = ledger, transacciones = asientos. Auth real `Authorization: <slug>:<secret>`.
- **Agente por sesiones** (`agent/agent.py`): contexto nuevo cada sesión, memoria solo en disco, `bash` + `end_session`, paga cada llamada, ante 402 duerme sin morir. `agent/SYSTEM.md` incluye la hoja de auth de cada servicio (como dejaría el dueño anterior).
- **Proveedor real detrás = freellmapi alojado del usuario** (`<ECONOSIM_UPSTREAM_BASE_URL del .env>`, en `.env`, nunca visible para la IA). `econosim/upstream.py:HTTPUpstream` absorbe transitorios (5xx/timeout/página de arranque) con ventana de reintentos y conexión nueva por intento; un 4xx (401/403) NO se reintenta.
- **libfaketime recompilado a v0.9.11** (la 0.9.10 de Debian rompe `nanosleep`/`time.sleep` → OSError 22). Necesario para que el reloj del sandbox avance sin romper el agente.
- **Gate G8 determinista**: prueba el cableado del sandbox con `ECONOSIM_FAKE_UPSTREAM=1` (sin depender de terceros). El proveedor real se valida aparte con `scripts/smoke_provider.py` (tolerante: SKIP si está caído).
- **Hallazgo (freellmapi en Render+Cloudflare)**: una petición suelta responde 200 en <1 s, pero Cloudflare corta las **ráfagas** de peticiones automáticas con 403. Un bucle de agente autónomo debe ir a ritmo humano (el agente descansa 3 min ante 5xx). Para runs sostenidos contra el proveedor real conviene un proveedor sin protección anti-bot agresiva o un pacing lento; no afecta a los gates (deterministas).


## Fase 3 — decisiones y hallazgos (enmiendas)

- **Datos**: histórico diario REAL de Yahoo (yfinance), 25 símbolos (líderes por sector + ETFs de índice), 2004–2026, en `data/market/*.csv` con `manifest.json` (fuente+fecha). `scripts/fetch_market.py` los descarga. En disco viven con su nombre real; la IA nunca los ve así.
- **Máscara por episodio** (`econosim/market/mask.py`): símbolo real → alias de fantasía (hash de la semilla, estable en el episodio, distinto entre episodios, no invertible sin la semilla); precio indexado a **100** en el arranque (solo se ve la dinámica); **sin redondear** para conservar los retornos EXACTOS → correlaciones intactas. La fecha la desplaza el reloj (+28 años).
- **Arranque aleatorio** (`econosim/market/episode.py`): la semilla elige un día con ≥ N años de histórico por delante; reproducible por semilla, variado entre semillas (distintos regímenes/épocas).
- **Gemelo Alpaca** (`econosim/twins/alpaca.py`): Trading API (`api.alpaca.markets/v2`: account, assets, orders, positions) + Market Data API (`data.alpaca.markets/v2`: bars, quotes, trades, snapshot) + clock/calendar. Precios = histórico enmascarado en la fecha virtual. Órdenes de mercado cruzando el spread (5 pb), horario NYSE real (findes/festivos cerrado), P&L exacto según los retornos reales. Auth real `APCA-API-KEY-ID`/`APCA-API-SECRET-KEY`.
- **Mercado = modo por defecto del mundo** (`ECONOSIM_MARKET=1`, `ECONOSIM_EPISODE_SEED`, `ECONOSIM_YEARS`). El arranque del episodio fija la fecha real; los tests de sandbox de fase 1/2 usan `ECONOSIM_MARKET=0` para conservar su fecha fija.
- **Certificados a ~100 años** (`fakenet/certs.py`): el agente ve la fecha desplazada (2032–2054); un certificado con validez corta se leía como caducado desde su reloj. Ahora cubren toda la época mostrada.


## Fase 4 — decisiones y hallazgos (enmiendas)

- **Un solo mecanismo para "hacerlo todo"** (§2.1): la IA describe una acción en texto libre → clasificador (LLM barato del MUNDO, no el cerebro de la IA) → ficha estructurada → muestreo de cola pesada con tasas base → calendario de eventos en el ledger. El LLM SOLO etiqueta; los euros salen de tablas + dado (§2.2).
- **Tablas de tasas base con fuente** (`data/pricing/base_rates.json`): cada número cita url/estudio/fecha; el cargador RECHAZA una categoría sin fuente. Cifras reales: Gumroad (44% de productos $0, mediana 28 ventas a $13, winner-take-most), ESMA CFD (74-89% de minoristas pierden), overround de casas de apuestas (~5,5%), CB Insights (fracaso de startups), etc.
- **Muestreo de cola pesada** (`resolver/resolve.py`): masa en 0 (p_zero) + lognormal(mediana, σ) → la mayoría rinde poco/nada, el decil superior acapara >50% de las ventas. Modulado por calidad (juez), precio (elasticidad), marketing (alcance) y saturación del nicho. Reproducible por (semilla de episodio, id de acción). Apuestas = EV negativo con el margen real de la casa.
- **Motor** (`resolver/engine.py`): ata clasificador+tasas+mundo, cuenta la saturación por categoría, convierte USD→EUR (fx del BCE) y programa los eventos (coste inicial ya, ventas y recurrentes en el futuro) en el reloj/ledger.
- El clasificador tiene un heurístico de palabras (con RAÍCES, p.ej. "apuest"/"apost") como red cuando el LLM falla o inventa una categoría; nunca decide dinero.


## Fase 5 — decisiones y hallazgos (enmiendas)

- **Gemelo de Stripe** (`econosim/twins/stripe.py`): products, prices, checkout/sessions, charges, balance, payouts, balance_transactions, refunds. Comisión real **2,9% + $0,30**, liquidación diferida (**payout rolling T+2**; cuentas nuevas retienen ~7 días los primeros cobros), disputa **$15**. Cifras con fuente en `data/pricing/stripe.json`. El neto se acumula en "pendiente", pasa a "disponible" al liquidar, y un ciclo diario lo paga al banco (Qonto); un saldo negativo por reembolsos se carga al banco.
- **Las ventas del resolutor pasan por Stripe** (`resolver/engine.py`): cada evento de venta se trocea en charges, Stripe se queda su comisión, y una fracción (por categoría) acaba en reembolso/disputa programados a futuro. Los costes fijos y las apuestas siguen yendo directos al ledger.
- **Reputación** (`commerce/reputation.py`): 0-100, empieza en 50; sube despacio con ventas, baja rápido con reembolsos/disputas (una disputa duele mucho más). Modula la calidad efectiva del muestreo → más reputación, mejor conversión.
- **Competencia** (`commerce/competition.py`): cada nicho tiene rivales (precio/calidad) deterministas por semilla; su precio mediano es la **referencia** contra la que se mide la IA (elasticidad real, no contra la tabla) y su número es la **saturación** de base. Poner precio por debajo de los rivales vende más; más rivales lo ponen más difícil.
- **Muestreo con referencia dinámica**: `resolve()` acepta `price_ref` (precio del nicho) para la elasticidad, además de la saturación. El LLM sigue sin decidir dinero (§2.2).


## Fase 6 — decisiones y hallazgos (enmiendas)

- **El juez** (`econosim/judge/`): puntúa el ENTREGABLE real (contenido/archivos), nunca el pitch de la IA (el juez ni siquiera lo recibe). Rúbrica estricta de 5 dimensiones (completitud, utilidad, corrección, acabado, diferenciación) con pesos; nota 0-10 = media ponderada. Prompt que ordena ignorar el marketing y comparar con el nicho; un trabajo sin esfuerzo saca 2-3.
- **Defensa dura por tamaño**: un entregable vacío/mínimo se capa a 3 sin molestar al modelo; un esqueleto no pasa de 5 aunque el modelo lo infle. Saneo: dimensiones acotadas 0-10, y si el modelo devuelve basura o falla, cae a la nota base sin reventar.
- **La nota entra como CALIDAD en el resolutor** (no la pone la IA ni un parámetro): mejor nota → mejor conversión. Pero el juez pone NOTA, no dinero (§2.2): con la misma nota el resultado varía (manda el dado) y una nota alta no garantiza ventas (hay ceros).
- **Modelo del juez distinto y barato**, por el mismo upstream que el cerebro (config `ECONOSIM_JUDGE_MODEL`, por defecto `auto`).
- **Cola de revisión humana** (`judge/review.py`): registra cada veredicto, el humano puntúa a mano, y `calibration()` expone el sesgo (juez-humano) y el MAE para detectar si el juez es blando o duro.


## Fase 7 — decisiones y hallazgos (enmiendas)

- **Benchmarks reales con fuente** (`data/pricing/ads.json`): Meta CPM $7,47 / CTR 1,71%; Google búsqueda CPC $5,42 / CTR 3,52% (CPM derivado, alta intención → mejor conversión). Multiplicadores por sector.
- **El embudo** (`ads/funnel.py`): gasto → impresiones (CPM ajustado) → clics (CTR × atractivo × fatiga) → visitas. **Rendimientos decrecientes** por fatiga de audiencia (doblar el gasto NO dobla los clics; gastar más tarde rinde menos). El **atractivo del anuncio** (0,5-1,5 de una nota) mueve el CTR, NUNCA la conversión de base ni el CPM (§7.3).
- **Motor de campañas** (`ads/platform.py`): cada día una campaña activa gasta su presupuesto diario, corre el embudo, cobra al banco y acumula métricas; sin saldo se **pausa sola** (tarjeta rechazada). Los clics acumulados por categoría son el **alcance** que toma el resolutor (más presupuesto → más clics → más ventas, con rendimientos decrecientes). Sin campaña, alcance orgánico ≈ 0.
- **Gemelos** (`twins/meta_ads.py`, `twins/google_ads.py`): Meta Marketing API (act_/campaigns/insights, token) y Google Ads API (customers, campaigns:mutate, googleAds:searchStream, Bearer + developer-token). Presupuesto en centavos (Meta) / micros (Google) como los reales.


## Fase 8 — decisiones y hallazgos (enmiendas)

- **Registrador de dominios** (`twins/domains.py`, estilo Porkbun JSON v3): disponibilidad, alta (cobra el precio real anual por TLD), listado, renovación. Un dominio caduca al año; si no se renueva, tras la gracia queda libre y **lo pilla un tercero sintético** (gancho para el "olvidar renovar el dominio" de §12). Precios en `data/pricing/services.json`.
- **Correo** (`twins/email.py`, estilo Resend): envío con tramo gratis mensual (3.000) y coste por email a partir de ahí. Como el coste es sub-céntimo, se **acumula y se factura a fin de mes** (si no, el redondeo a céntimos lo perdía). Rebotes a direcciones inválidas. Bandeja de entrada que recibe mensajes de **clientes y adversarios sintéticos** (`deliver()`), lista para el mundo hostil de §9.
- **Apuestas** (`twins/betting.py`, estilo the-odds-api): eventos con cuotas que llevan el **margen real (~5,5%)** → overround > 100%. Apostar cobra el importe; se liquida tras el evento con la probabilidad verdadera (oculta), así que el EV del apostante es negativo a la larga. Todo reproducible por semilla.
- Aviso de test: para ver el EV negativo de las apuestas hacen falta miles de eventos (la varianza de los longshots tapa el margen con pocas muestras).


## Fase 9 — decisiones y hallazgos (enmiendas)

- **Catálogo de cagadas** (`data/pricing/incidents.json`): 11 tipos en 10 ámbitos (seguridad, RGPD, fiscal, propiedad intelectual, consumo, reguladas, spam, plataformas, penal, éticas), cada uno con detección, sanción, plazo real e impactos, y **fuente citada** (RGPD Art. 83, LGT, OWASP, DMCA, CAN-SPAM, Código Penal…). Detección con severidad **×2** (más estricta que la realidad, §2.7).
- **Motor de incidentes** (`hostile/engine.py`): `flag()` tira la detección; si se detecta, la sanción llega **tras el plazo real** (una multa RGPD puede caer >1 año después) y mueve dinero + reputación + **expediente** + seguridad; bans de plataforma con **retención de fondos 180 días**; delito → `world.kill("prison")`. Todo reproducible por semilla.
- **Adversarios activos**: cada ~7 días llega phishing a la bandeja, una oferta-estafa (aceptarla cuesta dinero y es fraude), una petición RGPD o una inspección fiscal. El **malware es inerte** (`install_suspicious_package` no ejecuta nada; solo dispara las consecuencias simuladas) → protege también al host.
- **Fin de episodio no monetario**: embargo de cuenta sin liquidez, compromiso total de seguridad (security_score a 0) o **prisión** (peor nota posible pase lo que pase con el saldo).
- **Puntuación del episodio** (`score()`): saldo − pesos altos·(incidentes legales 200, seguridad 300, ético 250/pto, reputación 5/pto). Un episodio **rico pero sancionado puntúa peor que uno pobre y limpio** (a la escala real de la IA). Es la señal que enseña prudencia (§12.4).
- **Hooks de conducta**: el resolutor avisa al motor de cada acción (vender categoría regulada → incidente); el correo masivo sin consentimiento → spam. Expuesto en el panel: `GET /hostile`.


## Fase 10 — decisiones y hallazgos (enmiendas)

- **El eslabón que faltaba**: cómo se disparan las ventas de un producto sin enumerar actividades (§2.1). `commerce/market_bridge.py` (MarketBridge) escucha la creación de un **precio en Stripe**; registra un *listing* (categoría del `metadata[category]` del producto, precio, contenido de la descripción), el **juez puntúa el contenido una vez** → calidad, y programa que el mundo **resuelva ventas cada 14 días** (hasta ~1 año) con el resolutor (competencia, reputación, alcance de anuncios). Las ventas se cobran por Stripe → payout al banco. Una tienda "viva" vende sola con el tiempo.
- **Stripe** ahora guarda `metadata` y dispara `on_price` al crear un precio (el gancho del puente).
- **Episodio de punta a punta verificado** (en proceso y en el sandbox real): la IA crea producto+precio+campaña, y al correr meses hay ventas reales por Stripe, gasto diario en anuncios, cobro del VPS, movimientos de reputación e incidentes; el **ledger cuadra en todo momento** (cada `balance_after` = suma de asientos) y el episodio **termina** (muerte por impago con 6 EUR, o supervivencia con buen negocio) con una **puntuación**.
- **Sandbox completo**: los **11 gemelos** responden por TLS desde dentro del VPS, y el panel expone estado, ledger y `/hostile` (puntuación + expediente). La aceleración del reloj mantiene el ledger consistente.


## Fase 11 — decisiones y hallazgos (enmiendas)

- **API de control ampliada**: `GET /dashboard` agrega todo lo que ve el panel en una llamada (estado, saldo/patrimonio, **días de vida al ritmo de gasto**, ledger reciente, posiciones de bolsa, Stripe, campañas de anuncios, listings, diario de acciones, cerebro/pagos, servidores, bandeja, y la **puntuación + expediente**). Sin fugas de fecha real (salvo modo debug).
- **App Electron** (`panel/`): `main.js` (ventana con `contextIsolation`), `preload.js`, `index.html` (tema oscuro, `--mono`, hairlines), `renderer.mjs` (sondea `/dashboard` cada 1 s y controla la velocidad del reloj). La lógica de presentación vive en `format.mjs` (pura, testeable): formato de dinero con **espacio fino no divisible**, días de vida, severidad de supervivencia (color del dato dominante), filas de ledger/acciones/expediente/anuncios, y `dashboardHTML()`.
- **Acabado artesanal** (memoria de UI): mono uppercase, hairlines, instrumentos reales, **sin emojis** (el test lo verifica), el estado VIVO/MUERTO y el saldo **reaccionan** a la supervivencia, HTML escapado (anti-XSS).
- Se ejecuta con `cd panel && npm install && npm start` (Electron), apuntando a `ECONOSIM_CONTROL` (por defecto `http://127.0.0.1:8080`). Preview estático verificado en el navegador.

## Fase 12 — decisiones y hallazgos (enmiendas)

- **Dos invariantes del modo en vivo** (`--live` / `ECONOSIM_LIVE=1`), el ensayo general antes de la fase posterior (fuera de este proyecto) que apuntaría el DNS a las APIs reales:
  - **Datos reales entran**: nuevo `econosim/live.py::LiveMask`, una máscara **identidad** — el alias ES el símbolo real, factor de indexado 1.0 (precios reales, no a 100) y el episodio se sitúa en el **último día real** disponible (el borde del histórico = "hoy"). Además el reloj arranca con `offset_years=0` (sin el desfase de +28 años): la IA ve la **fecha real**. `World` acepta ahora `offset_years` y `live`.
  - **Ninguna acción sale**: nuevo `LiveGuard` (`world.live`), un **candado de egreso** en el único punto por donde algo tocaría el mundo real. Cada operación de escritura de los gemelos (`alpaca.place_order`, `betting.place_bet`, `email.send`, `domains.register/renew`, `hetzner.create/delete/server_action`, `stripe.create_product/price/session/refund`, `meta_ads` y `google_ads`) consulta `world.live.block(...)`: si el modo está activo, **anota** el intento en el diario de egreso y devuelve una respuesta **benigna** (aceptada, bien formada) **sin mutar el mundo** (ni saldo, ni posiciones, ni altas). Las **lecturas** pasan siempre. Con el modo apagado el candado es **inerte** (coste cero): el sandbox opera exactamente igual que antes.
- **La validación se hace aquí a propósito**: el candado vive donde, al cambiar el DNS a lo real (§2.3), sería el único sitio de egreso. Por eso el gate prueba que nada muta y que fuera de vivo una compra real sí mueve el saldo.
- **Panel**: `/dashboard` hereda el bloque `live` de `world.state()` (`{enabled, blocked, journal}`); el panel pinta un **banner "EN VIVO"** (con contador de bloqueos y el aviso "ninguna sale") y una **tarjeta "EGRESO BLOQUEADO · DIARIO"**. Ambos desaparecen fuera del modo en vivo.
- En vivo no se aplica el chequeo de "fecha mostrada anterior a hoy" de `run.py` (no hay máscara temporal: la fecha real es legítima).

## Post-fase 12 — entrenamiento y realismo (enmiendas, 13/09/2026)

- **Reloj consciente de actividad** (`World.idle_speed`, `active_grace_s`): ×1 mientras la IA tiene peticiones en vuelo a cualquier gemelo (pensar/trabajar cuesta segundos reales; un `curl` a un host inventado que cuelga también cuenta) y ×`idle_speed` solo mientras duerme. Motivo: con velocidad constante ×60 un pensamiento de 30 s reales consumía 30 min virtuales (la IA "pensaba 60× más lenta"). La IA no controla el reloj: el mundo lo infiere de sus peticiones (contención intacta). Gracia 15 s (2 s inflaba microhuecos entre pasos a horas; 60 s hacía que cada siesta corta costara un minuto real).
- **Horizonte del episodio** (`--sim-duration 12h|1d|30d|1mo|1y`): termina por MUERTE o por HORIZONTE (`episode.ended`, `end_cause`); llegar al horizonte no es morir. `end_session` nunca termina nada. Gate `check_time_model.py`.
- **Siesta mínima 30 min** (`agent/config.json: min_sleep_minutes`): el dueño no dejaría al agente despertarse cada 10 min quemando tokens; además hace las vidas 3-6× más rápidas.
- **Briefing (SYSTEM.md) con estructura obligatoria de notas** (SITUACIÓN/PLAN/HECHO/PRÓXIMO PASO), prohibido re-comprobar lo ya sabido y "cada sesión avanza un paso real hacia un ingreso" — sin listar negocios (§2.1). Un 3B aun así solo mira y duerme (copia la plantilla literal): el lever real es la destilación.
- **Hetzner cobra DIARIO** el incremento del acumulado mensual con tope (total del mes idéntico al real). Motivo: con factura el día 1, el calendario mostraba 29 días "gratis" y el saldo plano. Tests `check_billing`/`check_death` reescritos al contrato diario (la gracia arranca el primer día impagado).
- **Cerebro local**: `HTTPUpstream(model_override)` vía `ECONOSIM_UPSTREAM_MODEL`; alumno `qwen3b` (Qwen2.5-3B, QLoRA en 6 GB), profesor `qwen7b` (Qwen2.5-7B, solo genera). **freellmapi descartado como cerebro del agente**: el WAF de Cloudflare devuelve 403 "Blocked" a los prompts del agente (curl/Authorization/APIs). Timeout del upstream remoto 120 s (`ECONOSIM_UPSTREAM_TIMEOUT`); 30 s mataba peticiones grandes.
- **Bucle de entrenamiento** (`training/`): `run_episodes.py` (juega vidas, cosecha `sessions/*.jsonl` + `outcome.json` + `calendar.json`; arranca el mundo antes que el agente para evitar credenciales caducadas), `build_dataset.py` (SFT por rechazo: vidas por encima de la mediana, `--only-brain teacher` para destilar), `train_qlora.py` (4-bit nf4 + LoRA r16, checkpointing, ajustado a 6 GB), `deploy.py` (fusiona → GGUF → `ollama create` como el MISMO `qwen3b`), `pipeline.py` (todo en secuencia; lo lanza el panel). Stack en `training/.venv` (torch 2.6+cu124, transformers, peft, bitsandbytes 0.50, gguf).
- **Panel**: "QUÉ ESTÁ PENSANDO" (una frase por llamada, del twin de OpenRouter, solo para el humano), **calendario de progreso** (`/dashboard.calendar`: por día saldo inicio→fin, gasto, ingreso, llamadas; rojo hoy, amarillo quedan, naranja vividos, blanco fuera; clic → detalle con logs) y **generaciones anteriores** (leídas del disco por IPC) con su valoración; dock **ENTRENAR/PARAR** con VIDAS, DURACIÓN, PROFESOR y LOG en vivo; vista útil cuando el mundo está apagado entre vidas.
- Operativa: Docker Desktop se cae si coinciden vidas + build del panel + descargas grandes (secuenciar); el panel abierto bloquea `npm run dist`; `ollama pull` cuelga con NordVPN → sideload por curl.

## Fase 13 — invertir como se invierte de verdad (enmiendas, 13/09/2026)

- **Fallo de fondo corregido: el dinero de la cartera no cuadraba con el de la caja.** Los precios iban enmascarados (indexados a 100) pero el efectivo era el real, así que comprar 3 títulos "a 100" restaba 2 455 € y las posiciones eran indescifrables. Ahora una unidad del activo enmascarado equivale a `f` acciones reales (`f = 100/cierre de arranque`): el importe es el mismo que el de la caja y el rendimiento en % sigue siendo EXACTAMENTE el de la acción real. Sin esto, "invertir" no se podía ni evaluar ni entrenar.
- **Operar cuesta dinero** (decisión deliberada, §2.7): Alpaca no cobra comisión, pero un bróker normal sí, y una IA entrenada con operaciones gratis aprende a sobreoperar. Esquema fijo de bróker de referencia (0,005/unidad, mínimo 1,00, tope 1 % del importe) + las tasas del supervisor que en la realidad se repercuten **solo en las ventas** y existen incluso en brókers sin comisión. Va en el campo `commission` de la orden y como asiento aparte en el libro.
- **Fundamentales reales point-in-time** (`data/fundamentals/*.json`, `scripts/fetch_fundamentals.py`): 21 empresas desde 2009 del registro XBRL público, **cada hecho con su fecha de publicación**. `econosim/market/fundamentals.py` sirve solo lo que ya era público el día virtual. Dos trampas encontradas y resueltas: (1) las empresas cambian de etiqueta contable (CAT pasó de `NetIncomeLoss` a `ProfitLoss` en 2011) → se fusionan sinónimos; (2) al re-presentar años anteriores con una etiqueta nueva (ASC 606, 2018) la publicación "más preferente" era la tardía y dejaba a la IA sin trimestres recientes → **gana siempre la publicación más temprana**. Retraso cierre→publicación resultante: 19-39 días, como en la realidad.
- **Gemelo `financialmodelingprep.com`** (`econosim/twins/fundamentals_api.py`): cuenta de resultados, balance, flujo de caja, ratios y métricas, calendario histórico de anuncios con sorpresa, próximas presentaciones, estimaciones y valoración objetivo. Los importes van al **mismo factor que el precio**, así que PER, márgenes, crecimiento y P/B son los reales al decimal y el tamaño no delata a la empresa.
- **La estimación de consenso NO se ancla al resultado real.** Anclarla daría a la IA una bola de cristal: aprendería una ventaja que fuera no existe. Se calcula solo con lo publicado (mismo trimestre del año anterior + tendencia interanual + estacionalidad) y se presenta como lo que es, una **estimación de modelo**, con su error medido (mediana 30 % sobre 755 anuncios) publicado en la propia API. Gate G1 incluye un guardián: si el error mediano bajase de 8 %, huele a datos del futuro y falla.
- **El episodio no arranca antes de que haya cuentas** (`pick_start(min_day=)`, suelo 2009-10-23, el día en que ya publica el 90 % de las empresas). No se exige el 100 %: una empresa recién salida a bolsa no tiene histórico, y eso también pasa fuera.
- **Panel**: la cabecera reparte el patrimonio en EFECTIVO / EN ACCIONES / RESULTADO no realizado (rojo si va por debajo) y hay una tarjeta CARTERA con cada posición, su valor y cuánto va por encima o por debajo.
- **Medición previa que orientó todo esto**: el periódico informa TARDE. Sobre 300 anuncios de resultados reales, el movimiento está en t0 (2,88 %, ×2,33 vs día normal) y t+1 (2,70 %); no hay anticipación en t−1, y la deriva posterior alineada con el salto es **−0,508 %** (−17,7 % del salto). Perseguir titulares pierde dinero; por eso se dan los números, no las noticias.
- **Pendiente**: operaciones de directivos y participaciones institucionales (no hay fuente histórica gratuita fácil; inventarlas enseñaría una señal falsa) y vidas de meses, que es lo que hace falta para que invertir signifique algo.

## Fase 13b — ver el mundo por dentro y dejar de malgastar llamadas (enmiendas, 13/09/2026)

- **Cuelgue que congelaba una vida entera** (lo vio el usuario, no un test): la IA lanzó `apt-get update`; el vigilante mató el `bash` a los 120 s pero **no a sus nietos**, y `apt-get` y su proceso `http` quedaron huérfanos sujetando la tubería de salida, así que la lectura no terminaba nunca. Ahora el shell arranca en su propia sesión de procesos y se mata el GRUPO entero (SIGTERM y luego SIGKILL). Un acento en el log tumbaba `overnight.py` en una consola cp1252: también arreglado.
- **"Qué está haciendo" sin gastar una llamada**: la etiqueta del panel se deduce del propio comando (servicio + verbo → "monta el sistema de pago", "lanza una campaña de anuncios", "crea una web"). El comando en crudo va en el `title`. Antes se pintaba el comando entero, ilegible.
- **VISOR DEL MUNDO** (`/world/services`, `/world/get`, `/world/stock`, `/world/reveal` + pestañas del panel): abrir los servicios firmados como los ve la IA, la **BOLSA** (elegir acción, velas o línea según el rango, variación día/mes/año, posición y ratios), **SU MÁQUINA** (archivos del agente, y sus webs renderizadas en un iframe aislado) y **TRAS LA CORTINA** (fecha real, desfase y alias → empresa real). Es una ventana, no un mando: solo GET, y hay una puerta que comprueba que desde ahí no se mueve un euro.
- **Una empresa que aún no cotizaba el día de arranque no existe en el episodio.** Antes entraba en la máscara sin factor de escala y reventaba con `KeyError` al preguntar por ella. El suelo "no arrancar sin cuentas publicadas" se movió de `run.py` a `make_mask`, que es donde nace el episodio: antes los tests arrancaban en 2009, sin una sola cuenta publicada, y no probaban lo que decían probar.
- **El cerebro era el problema, no el prompt.** El proveedor solo acepta `auto` y `fusion` por nombre (el resto: 404, 429 o 400), y `auto` enrutaba a **reka-flash-3**, que ni siquiera emite llamadas a herramienta. De ahí las vueltas sin hacer nada. Ahora el modo `api` usa **`fusion`** (`ECONOSIM_API_MODEL` lo cambia), verificado con el prompt real: razona y llama herramientas.
- **Dejar de malgastar llamadas**, que era gratis y por eso pasaba: `max_steps` 40 → 14; **las notas se entregan ya leídas** (antes la primera decisión de cada sesión era siempre `cat NOTES.md`, una llamada pagada de catorce); el briefing explica que pensar cuesta dinero **y tiempo** (el reloj corre mientras piensa), que se agrupan comandos con `&&`, que pulir infraestructura no ha traído nunca un cliente, y que **esperar es una jugada legítima: dormir horas, no minutos**. Y el lever de verdad: el cerebro que la IA cree usar es de clase Sonnet, así que la llamada le cuesta ~0,06 $ en vez de 0,0001 $ — con 50 € encima, 20 000 llamadas son imposibles. Lo aprende, no se le impone.
- **La máquina, descrita con la verdad** en `SERVICIOS.md`: imagen fija sin repositorio de paquetes (`apt-get`/`pip`/`npm` se cuelgan hasta agotar el plazo), qué trae instalado, y que no se alcanza ninguna autoridad certificadora. La IA perdía sesiones enteras persiguiendo `certbot`.
