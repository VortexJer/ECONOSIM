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

// --- su máquina: los archivos de la IA y las webs que monta -----------------
import { maquinaHTML } from "../format.mjs";
const M = { ruta: "/home/agent", items: [
  { nombre: "sitio", dir: true, ruta: "/home/agent/sitio" },
  { nombre: "NOTES.md", dir: false, ruta: "/home/agent/NOTES.md" },
]};
const maq = maquinaHTML(M);
ok(maq.includes('data-dir="/home/agent/sitio"') && maq.includes("sitio/"), "las carpetas deben poder abrirse");
ok(maq.includes('data-file="/home/agent/NOTES.md"'), "los archivos deben poder leerse");
ok(maq.includes('data-dir="/home"') && maq.includes('data-dir="/home/agent"'), "faltan las migas de pan");
ok(maq.includes("ELIGE UN ARCHIVO"), "sin archivo elegido debería guiar");

const web = maquinaHTML({ ...M, archivo: "/home/agent/sitio/index.html",
                          contenido: "<h1>Mi \"tienda\"</h1>", render: true });
ok(web.includes("<iframe") && web.includes("sandbox=\"\""), "la web debe verse aislada, sin scripts");
ok(web.includes("&quot;tienda&quot;"), "las comillas del html romperían el srcdoc");
ok(web.includes("VER CÓDIGO"), "debe poder volverse al código");
const cod = maquinaHTML({ ...M, archivo: "/home/agent/sitio/index.html", contenido: "<h1>hola</h1>", render: false });
ok(cod.includes("&lt;h1&gt;") && !cod.includes("<iframe"), "en modo código no se renderiza");
ok(cod.includes("VER LA WEB"), "debe poder verse la web");
const txt = maquinaHTML({ ...M, archivo: "/home/agent/NOTES.md", contenido: "plan", render: false });
ok(!txt.includes("VER LA WEB"), "un .md no es una web");
ok(maquinaHTML({ error: "no hay mundo" }).includes("no hay mundo"), "el error no se ve");

const vm = viewerHTML({ tab: "maquina", maquina: M });
ok(vm.includes('id="v-tab-maquina" data-on="1"') && vm.includes("v-maq"), "la pestaña de la máquina no abre");

// --- la bolsa: elegir acción y ver el gráfico -------------------------------
import { bolsaHTML, grafico } from "../format.mjs";
const BARRAS = [];
for (let i = 0; i < 40; i++) {
  const base = 100 + i * 0.4;
  BARRAS.push({ d: `2046-0${1 + (i % 9)}-1${i % 9}`, o: base, h: base + 2, l: base - 2,
                c: base + (i % 3 === 0 ? -1 : 1), v: 1000 + i });
}
// pocas barras -> velas; muchas -> línea (dibujar 5000 velas no se ve ni se aguanta)
const velas = grafico(BARRAS);
ok(velas.includes("<svg") && velas.includes("<rect"), "con pocas barras deberían pintarse velas");
ok(velas.includes("g-up") && velas.includes("g-dn"), "las velas deben distinguir subida y bajada");
const muchas = [];
for (let i = 0; i < 400; i++) muchas.push({ d: "2046-01-01", o: 100, h: 101, l: 99, c: 100 + Math.sin(i) });
const linea = grafico(muchas);
ok(linea.includes("g-linea") && !linea.includes("<rect"), "con muchas barras debería ser una línea");
ok(grafico([]).includes("SIN COTIZACIÓN"), "sin datos debería decirlo");
// una acción plana no puede reventar el gráfico (dividir por cero)
const plana = grafico([{ d: "a", o: 5, h: 5, l: 5, c: 5 }, { d: "b", o: 5, h: 5, l: 5, c: 5 }]);
ok(plana.includes("<svg") && !plana.includes("NaN"), "una cotización plana rompe el gráfico");

const D = { symbol: "ACEON-57", precio: 116.4, dia: "2046-06-26", var_1d: 1.2, var_1m: -3.4, var_1a: 22.1,
  abierto: true, barras: BARRAS,
  cartera: { qty: 12, precio_medio: 100, valor: 1396.8, coste: 1200, resultado: 196.8, resultado_pct: 16.4 },
  numeros: { per: 21.4, precio_ventas: 3.2, precio_valor_contable: 5.1, margen_neto: 0.183, roe: 0.42,
             deuda_fondos_propios: 1.7, crecimiento: 0.061, proximos_resultados: "2046-07-24" } };
const bol = bolsaHTML({ simbolos: ["ACEON-57", "BELLUX-21"], simbolo: "ACEON-57", dias: 252, data: D });
ok(bol.includes('data-sym="BELLUX-21"') && bol.includes('class="b-sym on"'), "no se puede elegir la acción");
ok(bol.includes("116.40") && bol.includes("+1.20%") && bol.includes("−3.40%"), "faltan precio y variaciones");
ok(bol.includes("MERCADO ABIERTO"), "no dice si el mercado está abierto");
ok(bol.includes('data-dias="21"') && bol.includes('data-on="1" data-dias="252"'), "faltan los rangos del gráfico");
ok(bol.includes("<svg"), "falta el gráfico");
ok(bol.includes("EN CARTERA") && bol.includes("12 títulos") && bol.includes("+16.40%"), "no dice lo que tenemos");
ok(bol.includes("PER") && bol.includes("21.40") && bol.includes("18.30%") && bol.includes("2046-07-24"),
   "faltan los números de la empresa");
const vacio = bolsaHTML({ simbolos: ["A"], simbolo: "A", data: { ...D, cartera: null, numeros: null } });
ok(vacio.includes("NO TENEMOS ESTA ACCIÓN") && vacio.includes("NO PRESENTA CUENTAS"), "los vacíos mal puestos");
ok(bolsaHTML({ simbolos: [], simbolo: "", data: null }).includes("MUNDO APAGADO"), "sin mundo debería decirlo");
ok(bolsaHTML({ simbolos: ["A"], data: null, error: "no cotiza" }).includes("no cotiza"), "el error no se ve");
ok(!bol.includes("undefined") && !bol.includes("NaN"), "bolsa con valores rotos");
const vb = viewerHTML({ tab: "bolsa", bolsa: { simbolos: ["A"], simbolo: "A", dias: 252, data: D } });
ok(vb.includes('id="v-tab-bolsa" data-on="1"') && vb.includes("b-wrap"), "la pestaña de bolsa no abre");

if (fails) { console.log(`${fails} comprobaciones fallaron`); process.exit(1); }
console.log("VIEWER OK");
