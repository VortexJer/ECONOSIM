# ECONOSIM

Sandbox económico para entrenar una IA a sobrevivir ganando dinero. Diseño completo en `PROYECTO.md`; plan y enmiendas en `PLAN.md`; contrato de la fase en curso en `GATES.md` (unlazy).

## Fase 1 (hecha): núcleo

- `econosim/clock.py` — reloj virtual con máscara temporal (+28 años) y cola de eventos.
- `econosim/ledger.py` — ledger SQLite append-only en céntimos.
- `econosim/world.py` — episodio: saldo, pagos, muerte, reloj en tiempo real acelerable.
- `econosim/twins/hetzner.py` — gemelo de la Hetzner Cloud API v1 con precios reales (`data/pricing/hetzner.json`, con fuente y fecha) y facturación real (horas con tope, IPv4, factura el día 1, bloqueo a los 14 días).
- `econosim/fakenet/` — internet falso: DNS que resuelve todo al proxy, proxy HTTP por Host, puerta SNI en 443, CA propia.
- `econosim/control.py` — API para el panel humano (nunca alcanzable desde el sandbox).
- `sandbox/` — Docker: `world` (simulador) + `agent` (el VPS de la IA, red interna sin salida, libfaketime).

## Fase 3 (hecha): mercado enmascarado

- `scripts/fetch_market.py` — descarga histórico real (Yahoo) a `data/market/`.
- `econosim/market/` — cargador, máscara por episodio (alias + indexado a 100), arranque aleatorio, calendario NYSE.
- `econosim/twins/alpaca.py` — gemelo de Alpaca (trading + market data) sobre los precios enmascarados.

## Fase 4 (hecha): resolutor genérico de acciones

- `data/pricing/base_rates.json` — tasas base por categoría, con fuente citada en cada número.
- `econosim/resolver/` — clasificador (texto → ficha), muestreo de cola pesada, motor que programa eventos en el ledger.

## Fase 5 (hecha): ventas y pagos

- `econosim/twins/stripe.py` — gemelo de Stripe (comisiones, payout diferido, reembolsos/disputas).
- `econosim/commerce/` — reputación y competencia por nicho.
- Las ventas del resolutor fluyen: resolutor → Stripe → payout al banco.

## Fase 6 (hecha): el juez

- `econosim/judge/` — rúbrica estricta, veredicto 0-10 sobre el entregable real, cola de revisión humana y calibración juez-vs-humano.

## Fase 7 (hecha): embudo publicitario

- `econosim/ads/` — embudo (euros→impresiones→clics→visitas, rendimientos decrecientes) y motor de campañas.
- `econosim/twins/meta_ads.py`, `google_ads.py` — gemelos de Meta y Google Ads con benchmarks reales.

## Fase 8 (hecha): apuestas, dominios y correo

- `econosim/twins/domains.py`, `email.py`, `betting.py` — registrador de dominios, correo (Resend) y casa de apuestas, con precios/márgenes reales.

## Fase 9 (hecha): mundo hostil

- `data/pricing/incidents.json` — catálogo de cagadas con fuente por entrada.
- `econosim/hostile/` — motor de incidentes, adversarios activos, expediente y puntuación del episodio (dinero − sanciones).

## Fase 10 (hecha): bucle económico completo

- `econosim/commerce/market_bridge.py` — cierra el bucle producto→ventas (Stripe product → resolutor → payout).
- Episodio de punta a punta verificado en proceso y en el sandbox real (11 gemelos, panel con puntuación).

## Fase 11 (hecha): app Electron (panel)

- `panel/` — app Electron que lee `GET /dashboard` del API de control: saldo, días de vida, reloj/velocidad, ledger, acciones, mercado, incidentes y puntuación. Acabado mono/hairline, sin emojis.
- Arrancar: `cd panel && npm install && npm start` (con el mundo corriendo y su control en :8080).

## Fase 12 (hecha): modo en vivo (datos reales entran, ninguna acción sale)

Ensayo general antes de la fase posterior (fuera de este proyecto) que apuntaría el DNS a las APIs reales. Se activa con `--live` (o `ECONOSIM_LIVE=1`):

- **Datos reales entran**: el mercado deja de enmascararse (símbolos, precios y fecha REALES; el reloj no se desplaza los +28 años).
- **Ninguna acción sale**: un candado de egreso (`world.live`) intercepta toda acción con efecto externo (orden, apuesta, correo, dominio, servidor, producto/cobro, anuncio): la acepta de forma benigna pero NO muta el mundo y la anota en el diario de egreso. Las lecturas pasan siempre. Con el modo apagado el candado es inerte.
- El panel muestra un banner **EN VIVO** y el **diario de egreso**.

```
python -m econosim.run --live --https 443 --dns 53 --ca-dir /ca --control 8080
```

## Probar

```
python tests/check_clock.py          # ... check_ledger, check_billing, check_death, check_hetzner, check_fakenet, check_nodates
python tests/check_sandbox.py        # necesita Docker Desktop en marcha; tarda ~2 min
node ~/.claude/skills/unlazy/scripts/gate-check.mjs --status GATES.md
```

## Levantar el sandbox a mano

```
cd sandbox
ECONOSIM_SPEED=60 docker compose up -d --build     # 1 minuto real = 1 hora virtual
curl http://127.0.0.1:8080/state                    # panel
docker compose exec agent bash -l                   # entrar en el VPS de la IA
```
