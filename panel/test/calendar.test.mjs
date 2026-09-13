// Calendario de progreso: colores por estado, detalle del día, generaciones anteriores, vista sin mundo.
import { dashboardHTML, calendarHTML, historyHTML, offlineHTML } from "../format.mjs";
import { SAMPLE } from "./sample.mjs";
let fails = 0;
function ok(c, m) { if (!c) { console.log("FAIL:", m); fails++; } }

const cal = { start: "2046-04-18", end: "2046-04-24", today: "2046-04-20", days: [
  { date: "2046-04-18", state: "past", balance_start: 5000, balance_end: 4491, spent: 509, earned: 0, calls: 7 },
  { date: "2046-04-19", state: "past", balance_start: 4491, balance_end: 4475, spent: 16, earned: 0, calls: 3 },
  { date: "2046-04-20", state: "today", balance_start: 4475, balance_end: 4475, spent: 0, earned: 0, calls: 1 },
  { date: "2046-04-21", state: "future", balance_start: 4475, balance_end: null, spent: 0, earned: 0, calls: 0 },
  { date: "2046-04-22", state: "future", balance_start: 4475, balance_end: null, spent: 0, earned: 0, calls: 0 },
  { date: "2046-04-23", state: "future", balance_start: 4475, balance_end: null, spent: 0, earned: 0, calls: 0 },
  { date: "2046-04-24", state: "future", balance_start: 4475, balance_end: null, spent: 0, earned: 0, calls: 0 },
]};
const h = calendarHTML(cal, { detail: true, selected: "" });
ok(h.includes("ABRIL 2046"), "falta el mes");
ok((h.match(/cal-day past/g) || []).length === 2, "2 días vividos (naranja)");
ok((h.match(/cal-day today/g) || []).length === 1, "1 día de hoy (rojo)");
ok((h.match(/cal-day future/g) || []).length === 4, "4 días que quedan (amarillo)");
ok((h.match(/cal-day outside/g) || []).length >= 20, "el resto del mes en blanco (fuera)");
ok(h.includes("QUEDAN 4") && h.includes("VIVIDOS 2"), "leyenda con conteos");
ok(h.includes("PULSA UN DÍA VIVIDO"), "pista de detalle sin selección");
// detalle del día: dinero inicial -> final, gasto, llamadas y logs de ese día
const det = calendarHTML(cal, { detail: true, selected: "2046-04-18",
  thoughts: [{ ts: "2046-04-18T13:31:00+00:00", said: "Reviso el banco.", doing: "ejecuta: curl qonto" },
             { ts: "2046-04-19T09:00:00+00:00", said: "Otro día.", doing: "" }] });
ok(det.includes("DINERO INICIAL") && det.includes("50.00") && det.includes("44.91"), "detalle con dinero inicial/final");
ok(det.includes("−5.09"), "delta del día");
ok(det.includes("LLAMADAS AL CEREBRO") && det.includes(">7<"), "llamadas del día");
ok(det.includes("Reviso el banco") && !det.includes("Otro día"), "solo los logs de ese día");
ok(det.includes("cal-day past sel"), "el día pulsado queda marcado");
// generaciones anteriores con valoración y mini calendario
const hist = historyHTML([{ seed: "gen2-000", sessions: 12, calendar: cal,
  outcome: { score: 44.75, balance_cents: 4475, alive: true, end_cause: "horizon" } }]);
ok(hist.includes("gen2-000") && hist.includes("HORIZON") && hist.includes("PUNTUACIÓN"), "cabecera de la generación");
ok(hist.includes("44.75") && hist.includes("cal-months"), "valoración y calendario de la generación");
ok(historyHTML([]).includes("AÚN NO HAY GENERACIONES"), "sin generaciones");
// el dashboard integra calendario + historial
const full = dashboardHTML({ ...SAMPLE, calendar: cal, history: [] });
ok(full.includes("CALENDARIO DE PROGRESO") && full.includes("GENERACIONES ANTERIORES"), "secciones en el dashboard");
// vista con el mundo apagado: sigue mostrando las generaciones
const off = offlineHTML([{ seed: "x", sessions: 1, calendar: null, outcome: { score: 1, balance_cents: 100, alive: false, end_cause: "death" } }], "MUNDO APAGADO ENTRE VIDAS");
ok(off.includes("SIN MUNDO") && off.includes("MUNDO APAGADO ENTRE VIDAS") && off.includes("DEATH"), "vista sin mundo con historial");
ok(!full.includes("undefined") && !full.includes("NaN") && !off.includes("undefined"), "sin valores rotos");

// --- generaciones anteriores: día clicable con su calendario de origen y logs de ese día ---
const gen = { seed: "gen2-000", sessions: 12, calendar: cal,
  thoughts: [{ ts: "2046-04-18T13:31:00+00:00", said: "Reviso banco.", doing: "ejecuta: curl qonto" },
             { ts: "2046-04-19T10:00:00+00:00", said: "Creo producto.", doing: "ejecuta: stripe products" }],
  outcome: { score: 44.75, balance_cents: 4475, alive: true, end_cause: "horizon" } };
// sin selección: la generación es clicable y pide pulsar
const h0 = historyHTML([gen], {});
ok(h0.includes('data-cal="gen2-000"') && h0.includes('data-click="1"'), "los días de la generación no son clicables");
ok(h0.includes("PULSA UN DÍA"), "falta la pista de detalle en la generación");
// con un día de ESA generación seleccionado: sale su dinero y SUS logs de ese día
const h1 = historyHTML([gen], { selectedCal: "gen2-000", selectedDay: "2046-04-18" });
ok(h1.includes("DINERO INICIAL") && h1.includes("cal-day past sel"), "no marca ni detalla el día de la generación");
ok(h1.includes("Reviso banco") && !h1.includes("Creo producto"), "no filtra los logs del día correcto");
// la vista SIN MUNDO también permite pulsar días (el bug reportado)
const off2 = offlineHTML([gen], "MUNDO APAGADO", { selectedCal: "gen2-000", selectedDay: "2046-04-19" });
ok(off2.includes('data-cal="gen2-000"') && off2.includes('data-click="1"'), "sin mundo, los días no son clicables");
ok(off2.includes("Creo producto") && off2.includes("DINERO INICIAL"), "sin mundo, no muestra el detalle del día");
ok(!off2.includes("undefined") && !off2.includes("NaN"), "vista sin mundo con valores rotos");

// --- cartera: dinero + valor en acciones + resultado (por encima / por debajo) ---
const cart = dashboardHTML({ ...SAMPLE, stocks_value_cents: 128000, unrealized_cents: -4550,
  positions: [
    { symbol: "QUAION-46", qty: 12, market_value_cents: 98000, cost_cents: 105000, unrealized_cents: -7000 },
    { symbol: "BELCOR-71", qty: 5, market_value_cents: 30000, cost_cents: 27550, unrealized_cents: 2450 },
  ]});
ok(cart.includes("EN ACCIONES") && cart.includes("1 280.00"), "falta el valor en acciones");
ok(cart.includes("RESULTADO") && cart.includes("−45.50"), "falta el resultado total (negativo)");
ok(cart.includes('metric pl neg'), "el resultado negativo no se marca en rojo");
ok(cart.includes("CARTERA") && cart.includes("QUAION-46") && cart.includes("BELCOR-71"), "falta la tabla de posiciones");
ok(cart.includes("−70.00") && cart.includes("+24.50"), "faltan los resultados por posición");
ok(cart.includes("−6.7%") && cart.includes("+8.9%"), "faltan los porcentajes por posición");
const sinAcc = dashboardHTML({ ...SAMPLE, positions: [], stocks_value_cents: 0, unrealized_cents: 0 });
ok(sinAcc.includes("SIN ACCIONES") && sinAcc.includes('metric pl pos'), "sin acciones no se pinta bien");
ok(!cart.includes("undefined") && !cart.includes("NaN"), "cartera con valores rotos");

if (fails) { console.log(`${fails} comprobaciones fallaron`); process.exit(1); }
console.log("CALENDAR OK");

// --- qué está haciendo: etiqueta deducida del comando, comando crudo en el title ---
const pens = dashboardHTML({ ...SAMPLE, thoughts: [
  { ts: "2046-06-26T10:12:00+00:00", said: "", doing: "monta el sistema de pago",
    detalle: 'curl -s -X POST https://api.stripe.com/v1/products -d name="X"' },
]});
ok(pens.includes("monta el sistema de pago"), "no se pinta la etiqueta de la acción");
ok(pens.includes('title="curl -s -X POST'), "el comando crudo debería quedar en el title");
ok(pens.includes("name=&quot;X&quot;"), "las comillas del comando romperían el atributo title");
