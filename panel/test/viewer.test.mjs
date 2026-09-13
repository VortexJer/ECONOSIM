// Visor del mundo: que se pueda navegar la simulación por dentro y que se entienda.
import { viewerHTML, serviciosHTML, cuerpoHTML, cortinaHTML } from "../format.mjs";

let fails = 0;
const ok = (c, m) => { if (!c) { console.log("FAIL:", m); fails++; } };

const SERVICIOS = [
  { host: "financialmodelingprep.com", rutas: ["/api/v3/ratios-ttm/{sym}", "/api/v3/earning_calendar?days=45"] },
  { host: "api.alpaca.markets", rutas: ["/v2/account", "/v2/positions"] },
];

// --- lista de servicios: plegada, y desplegada al elegir uno ---------------
const cerrado = serviciosHTML(SERVICIOS, "");
ok(cerrado.includes("financialmodelingprep.com") && cerrado.includes("api.alpaca.markets"), "faltan servicios");
ok(!cerrado.includes("ratios-ttm"), "las rutas no deberían verse sin abrir el servicio");
const abierto = serviciosHTML(SERVICIOS, "api.alpaca.markets");
ok(abierto.includes('data-url="api.alpaca.markets/v2/account"'), "la ruta no lleva su url");
ok(!abierto.includes("ratios-ttm"), "se abrió el servicio equivocado");
ok(serviciosHTML([], "").includes("MUNDO APAGADO"), "sin mundo debería decirlo");

// --- el cuerpo: JSON con sangría, texto tal cual, errores en rojo ----------
const j = cuerpoHTML({ status: 200, host: "api.alpaca.markets", path: "/v2/account", body: '[{"a":1,"b":[2,3]}]' });
ok(j.includes("200 · api.alpaca.markets/v2/account"), "falta la cabecera de la respuesta");
ok(j.includes('"a": 1'), "el JSON debería venir con sangría");
ok(cuerpoHTML({ status: 404, host: "x", path: "/y", body: "no" }).includes("v-err"), "un 404 debería marcarse");
ok(cuerpoHTML({ error: "ese servicio no existe" }).includes("ese servicio no existe"), "el error no se ve");
ok(cuerpoHTML(null).includes("ESCRIBE UNA DIRECCIÓN"), "sin respuesta debería guiar");
ok(cuerpoHTML({ status: 200, body: "<b>hola</b>" }).includes("&lt;b&gt;"), "HTML de la respuesta sin escapar");

// --- tras la cortina: la época real y la identidad de cada empresa ---------
const rev = cortinaHTML({ fecha_mostrada: "2046-06-26T13:30:00+00:00", fecha_real: "2018-06-26T13:30:00+00:00",
  desfase_años: 28, arranque_real: "2018-05-01", episodio: "7d93d01c",
  empresas: [{ alias: "BELLUX-21", real: "KO", factor: 0.2278 }] });
ok(rev.includes("2046-06-26 13:30") && rev.includes("2018-06-26 13:30"), "faltan las dos fechas");
ok(rev.includes("28 años") && rev.includes("7d93d01c"), "falta el desfase o el episodio");
ok(rev.includes("BELLUX-21") && rev.includes("KO") && rev.includes("0.22780"), "no traduce la empresa");
ok(cortinaHTML(null).includes("MUNDO APAGADO"), "sin mundo debería decirlo");

// --- la pantalla entera -----------------------------------------------------
const v = viewerHTML({ tab: "red", url: "api.alpaca.markets/v2/account", servicios: SERVICIOS,
                       simbolos: ["BELLUX-21", "ACEON-57"], simbolo: "ACEON-57",
                       resp: { status: 200, host: "api.alpaca.markets", path: "/v2/account", body: "{}" } });
ok(v.includes("VISOR DEL MUNDO") && v.includes('id="v-url"') && v.includes('id="v-go"'), "falta la barra de dirección");
ok(v.includes('value="api.alpaca.markets/v2/account"'), "la dirección no se conserva");
ok(v.includes('<option selected>ACEON-57</option>'), "el selector de empresa no recuerda la elegida");
ok(v.includes('id="v-tab-red" data-on="1"'), "no se marca la pestaña activa");
ok(v.includes('id="v-close"'), "sin botón de cerrar no se sale");
const vc = viewerHTML({ tab: "cortina", reveal: null });
ok(vc.includes('id="v-tab-cortina" data-on="1"') && vc.includes("v-main solo"), "la pestaña de la cortina no cambia la vista");
ok(!vc.includes("v-side"), "en la cortina no pinta la lista de servicios");

// comillas de una dirección con parámetros: no pueden romper el atributo
const vq = viewerHTML({ tab: "red", url: 'x.com/a?q="1"', servicios: [], simbolos: [] });
ok(vq.includes("&quot;1&quot;"), "la dirección con comillas rompería el input");
ok(!v.includes("undefined") && !v.includes("NaN"), "visor con valores rotos");

if (fails) { console.log(`${fails} comprobaciones fallaron`); process.exit(1); }
console.log("VIEWER OK");
