// G3: proyecto Electron válido y el renderer pinta el payload de ejemplo sin errores.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { dashboardHTML } from "../format.mjs";
import { SAMPLE } from "./sample.mjs";

const DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
let fails = 0;
function ok(cond, msg) { if (!cond) { console.log("FAIL:", msg); fails++; } }

// --- proyecto Electron bien formado ---
const pkg = JSON.parse(readFileSync(join(DIR, "package.json"), "utf-8"));
ok(pkg.main === "main.js", "package.json: main debería ser main.js");
ok(pkg.scripts && pkg.scripts.start === "electron .", "package.json: script start");
ok(pkg.devDependencies && pkg.devDependencies.electron, "electron no está en devDependencies");
for (const f of ["main.js", "preload.js", "index.html", "renderer.mjs", "format.mjs"]) {
  ok(pkg.build.files.includes(f), `falta ${f} en build.files`);
  readFileSync(join(DIR, f), "utf-8");   // existe y se lee
}

// --- main.js: crea ventana y carga index.html ---
const main = readFileSync(join(DIR, "main.js"), "utf-8");
ok(main.includes("BrowserWindow") && main.includes("loadFile") && main.includes("index.html"), "main.js no crea la ventana");
ok(main.includes("contextIsolation: true") && main.includes("nodeIntegration: false"), "main.js sin aislamiento seguro");

// --- index.html enlaza el renderer y define el tema oscuro ---
const html = readFileSync(join(DIR, "index.html"), "utf-8");
ok(html.includes('src="renderer.mjs"') && html.includes('id="app"'), "index.html no enlaza el renderer");
ok(html.includes("--bg:#0a0b0d") && html.includes("var(--mono)"), "index.html sin tema oscuro/mono");

// --- el renderer usa dashboardHTML y sondea /dashboard ---
const rnd = readFileSync(join(DIR, "renderer.mjs"), "utf-8");
ok(rnd.includes("dashboardHTML") && rnd.includes("/dashboard") && rnd.includes("/speed"), "renderer no usa el API");

// --- render de un payload completo: contiene TODAS las secciones esperadas ---
const out = dashboardHTML(SAMPLE);
for (const section of ["SALDO", "PATRIMONIO", "DÍAS DE VIDA", "GASTO DIARIO", "PUNTUACIÓN",
  "LEDGER", "DIARIO DE ACCIONES", "EXPEDIENTE", "ANUNCIOS", "CEREBRO"]) {
  ok(out.includes(section), `falta la sección ${section}`);
}
// datos concretos del payload presentes
ok(out.includes("Plantilla Notion") && out.includes("Logo a medida"), "faltan las acciones");
ok(out.includes("rgpd_violation") && out.includes("SANCION"), "falta el incidente sancionado");
ok(out.includes("512"), "faltan las llamadas del cerebro");
// nada roto
ok(!out.includes("undefined") && !out.includes("NaN") && !out.includes("[object Object]"), "el HTML tiene valores rotos");
// controles de velocidad con el activo marcado (speed=100)
ok(out.includes('data-speed="100"') && out.includes('data-active="1"'), "no marca la velocidad activa");

// --- muerto: el HTML reacciona ---
const dead = dashboardHTML({ ...SAMPLE, alive: false, death_cause: "prison", score: -1000000000 });
ok(dead.includes('data-status="dead"') && dead.includes("PRISON"), "no reacciona a la muerte");

// --- HTML escapado: un título con < no rompe el marcado ---
const evil = dashboardHTML({ ...SAMPLE, actions: [{ category: "x", title: "<script>alert(1)</script>", units: 1, revenue_usd: 1 }] });
ok(!evil.includes("<script>alert(1)</script>") && evil.includes("&lt;script&gt;"), "no escapa el HTML (XSS)");

if (fails) { console.log(`${fails} comprobaciones fallaron`); process.exit(1); }
console.log("RENDER OK");
