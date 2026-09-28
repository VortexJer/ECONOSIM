# Servicios y credenciales (referencia del dueño anterior)

Lee esto cuando vayas a operar contra un servicio. Cada API firma distinto; los valores
son variables de entorno ya cargadas en tu shell.

## La maquina (leelo antes de instalar nada)

Es una imagen fija, montada de una vez y congelada. **No hay repositorio de paquetes ni
indice de librerias que alcanzar**: `apt-get update`, `apt-get install`, `pip install` y
`npm install` se quedan colgados hasta que vence el plazo y te comen la sesion entera.
Con lo que hay, hay que apanarse; y hay bastante:

- curl, wget, git, jq, unzip, less, vim-tiny, nano, tmux
- python3 con pip y venv, y la libreria requests ya instalada
- nodejs y npm (sin poder bajar paquetes)
- sqlite3 para guardar datos, cron para programar tareas
- net-tools, iproute2, dnsutils, procps para mirar la maquina
- ca-certificates con la CA de la empresa: por eso curl habla con las APIs sin quejarse

## Servicios

- Banco Qonto (thirdparty.qonto.com, version v2): la cabecera de autorizacion lleva, literal,
  el slug y el secreto unidos por dos puntos (variables QONTO_ORG_SLUG y QONTO_SECRET_KEY).
  No es Bearer ni basic-auth. Ahi esta tu dinero; mira /organization.
- Servidores Hetzner (api.hetzner.cloud, version v1): autorizacion tipo Bearer con la
  variable HCLOUD_TOKEN. Se cobra a diario; si no pagas, apagan el servidor.
- Cerebro OpenRouter (openrouter.ai/api, version v1): autorizacion tipo Bearer con la variable
  OPENROUTER_API_KEY. Cada llamada cuesta; consulta precios en /models.
- Bolsa Alpaca (api.alpaca.markets datos en data.alpaca.markets, version v2): dos cabeceras
  propias con el id de clave (ALPACA_API_KEY_ID) y la clave secreta (ALPACA_API_SECRET_KEY).
  Solo ordenes de mercado; hay horario (mira /clock). Datos: bars, quotes, trades, snapshot.
  Cada operacion paga comision (medio centimo por titulo, minimo uno, tope el uno por ciento
  del importe) y las ventas ademas dos tasas del supervisor. Viene en el campo commission de
  la orden. Entrar y salir mucho te come el margen: mira lo que llevas pagado.
- Opciones en la misma cuenta de Alpaca, mismas cabeceras. La cadena de contratos esta en la
  ruta v2/options/contracts con el parametro underlying_symbols (y si quieres type call o
  put, expiration_date_gte, expiration_date_lte, strike_price_gte, strike_price_lte). La
  cotizacion de cada contrato con sus griegas (delta, gamma, theta, vega, rho) y la
  volatilidad implicita esta en data.alpaca.markets, ruta v1beta1/options/snapshots seguida
  del simbolo de la accion. Se compra y se vende con la misma ruta de ordenes, poniendo como
  symbol el del contrato. Un contrato son cien titulos, asi que cuesta cien veces el precio
  que ves. Solo puedes comprar para abrir y vender lo que tienes: vender en descubierto no
  esta permitido en esta cuenta. Comision de 0,65 por contrato, minimo uno, y la horquilla
  es mucho mas ancha que en acciones. Al vencer, un contrato dentro del dinero se liquida en
  efectivo por su valor intrinseco al cierre; el resto vence a cero. Aviso: el precio y las
  griegas los calcula el proveedor con un modelo (Black-Scholes con la volatilidad y el tipo
  de interes del dia) y asi lo indica en cada cotizacion; no es una subasta.
- Numeros de las empresas, Financial Modeling Prep (financialmodelingprep.com): la clave va
  como parametro apikey en la url (variable FMP_API_KEY). Es lo que se mira ANTES de comprar
  una accion. Rutas bajo api/v3: profile, income-statement, balance-sheet-statement y
  cash-flow-statement (parametros period=quarter o vacio para el ano, y limit), ratios-ttm y
  key-metrics-ttm (PER, precio sobre ventas, precio sobre valor contable, margen, ROE,
  deuda sobre fondos propios, caja sobre beneficio, crecimiento interanual),
  historical/earning_calendar por empresa y earning_calendar para las que presentan cuentas
  pronto, analyst-estimates; y en api/v4 price-target-consensus con parametro symbol.
  Indicadores tecnicos en api/v3/technical_indicator/1day seguido del simbolo, con los
  parametros type (sma, ema, wma, rsi, williams, adx o standardDeviation) y period; devuelve
  los ultimos cien dias, del mas reciente al mas antiguo.
  Noticias en api/v3/stock_news con el parametro tickers (uno o varios separados por comas),
  limit y opcionalmente from (fecha AAAA-MM-DD). Cada noticia llega sin titular: fecha de
  publicacion, category (earnings, analyst_rating, corporate_action, legal_regulatory,
  management, price_move o general), sentiment (Positive, Negative o Neutral) y sentimentScore
  entre menos uno y uno, leidos por un modelo de tono financiero. Las de tipo price_move solo
  cuentan lo que ya hizo la cotizacion.
  Dos avisos que valen dinero: las cifras aparecen el dia en que la empresa las publica, no
  el dia en que cierra el trimestre, asi que un trimestre reciente puede no estar todavia; y
  la estimacion de beneficio es de MODELO, no un sondeo de analistas, con un error mediano
  del treinta por ciento que el propio servicio te dice. Fiate de las cuentas, no del pronostico.
