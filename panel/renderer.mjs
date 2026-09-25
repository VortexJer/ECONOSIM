// Renderer: sondea el API de control y pinta el panel. Sin lógica de negocio (va en format.mjs).
// Además maneja el dock de ENTRENAMIENTO (fuera de #app, para que el re-pintado no lo borre).
import { dashboardHTML, offlineHTML, viewerHTML } from "./format.mjs";
import { layaHTML, LAYA_CSS } from "./laya.mjs";
const LAYA = window.econosimLaya;     // entrenamiento del cerebro Laya en vivo (solo en la app)
let laya = null;                      // ejecución que se está mirando (live.json + vidas)
let layaRuns = [];                    // todas las ejecuciones activas
let layaSel = "";                     // cuál se mira ("" = la más reciente con generaciones)
let layaLife = -1;                    // vida de la campeona pulsada (-1 = la de la instantánea)
{ const st = document.createElement("style"); st.textContent = LAYA_CSS; document.head.appendChild(st); }
const MAQ = window.econosimMaquina;   // puente a la máquina de la IA (solo en la app)

const BASE = (window.ECONOSIM_CONTROL || "http://127.0.0.1:8080").replace(/\/$/, "");
const app = document.getElementById("app");
const conn = document.getElementById("conn");
let lastOk = 0;
let training = false;
let lastStage = "";
let history = [];          // generaciones anteriores (vidas ya jugadas), vía IPC
let selectedCal = "";      // calendario pulsado ("live" o la semilla de una generación)
let selectedDay = "";      // día pulsado
let lastData = null;

async function poll() {
  try {
    const r = await fetch(BASE + "/dashboard", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    lastData = data;
    app.innerHTML = dashboardHTML({ ...data, history, selectedCal, selectedDay });
    wireSpeed();
    lastOk = Date.now();
    conn.textContent = "";
  } catch (e) {
    // Sin mundo en marcha: si hay (o hubo) un entrenamiento de Laya, se enseña eso.
    if (laya) { app.innerHTML = layaHTML(laya.data, layaOpts()); conn.textContent = ""; return; }
    // entre vida y vida del entrenamiento el mundo está apagado a propósito: el panel
    // sigue enseñando las generaciones anteriores y el estado del entrenamiento
    const note = training ? "MUNDO APAGADO ENTRE VIDAS · " + (lastStage || "entrenando") : "SIN CONEXIÓN CON EL MUNDO";
    app.innerHTML = offlineHTML(history, note, { selectedCal, selectedDay });
    conn.textContent = training ? "" : "SIN CONEXIÓN CON EL MUNDO · " + BASE;
  }
}

// re-pinta la vista actual (vivo u offline) conservando la selección de día
function rerender() {
  if (lastData && Date.now() - lastOk < 4000) {
    app.innerHTML = dashboardHTML({ ...lastData, history, selectedCal, selectedDay });
    wireSpeed();
  } else if (laya) {
    app.innerHTML = layaHTML(laya.data, layaOpts());
  } else {
    const note = training ? "MUNDO APAGADO ENTRE VIDAS · " + (lastStage || "entrenando") : "SIN CONEXIÓN CON EL MUNDO";
    app.innerHTML = offlineHTML(history, note, { selectedCal, selectedDay });
  }
}

function layaOpts() {
  return { age: laya.age, runs: layaRuns.map((r) => r.name), sel: laya.name, lives: laya.lives || [], lifeSel: layaLife };
}

async function refreshLaya() {
  if (!LAYA) return;
  try {
    const r = await LAYA.live();
    if (r && r.ok) {
      layaRuns = r.runs;
      const conGen = r.runs.find((x) => (x.data.generations || []).length > 1) || r.runs[0];
      laya = r.runs.find((x) => x.name === layaSel) || conGen;
    }
  } catch (e) { /* sin entrenamiento */ }
}

// pestañas de ejecución y vidas de la campeona: delegación en #app (sobrevive al re-pintado)
app.addEventListener("click", (e) => {
  const tab = e.target.closest && e.target.closest("[data-run]");
  if (tab) { layaSel = tab.getAttribute("data-run"); layaLife = -1; refreshLaya().then(rerender); return; }
  const vida = e.target.closest && e.target.closest("[data-life]");
  if (vida) { const i = Number(vida.getAttribute("data-life")); layaLife = layaLife === i ? -1 : i; rerender(); }
});

// Delegación: un único manejador en #app capta el clic en cualquier día clicable,
// del calendario vivo o de una generación anterior. Sobrevive a los re-pintados del poll
// y funciona aunque no haya mundo.
app.addEventListener("click", (e) => {
  const day = e.target.closest && e.target.closest('.cal-day[data-click="1"]');
  if (!day) return;
  const cal = day.getAttribute("data-cal") || "live";
  const date = day.getAttribute("data-date") || "";
  if (selectedCal === cal && selectedDay === date) { selectedCal = ""; selectedDay = ""; }  // volver a pulsar = cerrar
  else { selectedCal = cal; selectedDay = date; }
  rerender();
});

// generaciones anteriores: se leen del disco (Electron) cada 15 s
async function refreshHistory() {
  try { if (window.econosimHistory) history = await window.econosimHistory.list(); } catch (e) { /* sin historial */ }
}

function wireSpeed() {
  const bar = document.getElementById("speedbar");
  if (!bar) return;
  bar.querySelectorAll(".speed").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const speed = Number(btn.getAttribute("data-speed"));
      bar.querySelectorAll(".speed").forEach((b) => b.removeAttribute("data-active"));
      btn.setAttribute("data-active", "1");
      try {
        await fetch(BASE + "/speed", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ speed }),
        });
      } catch (e) { /* el poll reflejará el estado real */ }
    });
  });
}

// --- entrenamiento -----------------------------------------------------------
const T = window.econosimTrain;
const $ = (id) => document.getElementById(id);
const logBox = $("trainlog");

function setRunning(on) {
  training = on;
  $("train-start").disabled = on;
  $("train-stop").disabled = !on;
  $("train-status").setAttribute("data-running", on ? "1" : "0");
  if (!on && !lastStage.startsWith("fin")) { /* mantiene el último estado */ }
}

function addLog(line) {
  const div = document.createElement("div");
  const m = /^\[([^\]]+)\]\s?(.*)$/.exec(line);
  const stage = m ? m[1] : "";
  const text = m ? m[2] : line;
  if (stage === "error") div.className = "er";
  else if (stage === "fin") div.className = "ok";
  else if (["inicio", "vidas", "dataset", "entrenar", "desplegar"].includes(stage) || /^\d\d:\d\d:\d\d$/.test(stage)) div.className = "st";
  div.textContent = (stage ? stage.toUpperCase() + " · " : "") + text;
  logBox.appendChild(div);
  while (logBox.children.length > 300) logBox.removeChild(logBox.firstChild);
  logBox.scrollTop = logBox.scrollHeight;
  // estado corto en la barra: la última línea de etapa
  if (stage && stage !== "panel") {
    lastStage = stage + " · " + text.slice(0, 90);
    $("train-status").textContent = lastStage;
  }
}

if (T) {
  T.onLog(addLog);
  T.onDone(({ code }) => {
    setRunning(false);
    $("train-status").textContent = code === 0
      ? "ENTRENAMIENTO TERMINADO · la campeona está en training/laya_runs"
      : "PARADO (código " + code + ") · la campeona guardada sigue en training/laya_runs";
  });
  $("train-start").addEventListener("click", async () => {
    const lives = Number($("train-lives").value) || 12;
    const minInvested = Number($("train-mandato").value);
    const brain = $("train-num").checked ? "num" : "laya";
    logBox.setAttribute("data-open", "1");
    const r = await T.start({ lives, minInvested, brain });
    if (!r.ok) { addLog("[error] " + r.error); return; }
    setRunning(true);
    $("train-status").textContent = "arrancando…";
  });
  $("train-stop").addEventListener("click", async () => { await T.stop(); });
  $("train-toggle").addEventListener("click", () => {
    logBox.setAttribute("data-open", logBox.getAttribute("data-open") === "1" ? "0" : "1");
  });
  // si el panel se abre con un entrenamiento ya en marcha, recupera su estado
  T.status().then((s) => { if (s && s.running) { setRunning(true); (s.log || []).forEach(addLog); logBox.setAttribute("data-open", "1"); } });
} else {
  $("train-status").textContent = "ENTRENAMIENTO SOLO DISPONIBLE EN LA APP ELECTRON";
  $("train-start").disabled = true;
}

// --- probar freellm ---------------------------------------------------------
const FL = window.econosimFreellm;
if (FL) {
  const flAsk = async () => {
    const q = ($("fl-input").value || "").trim();
    if (!q) return;
    const out = $("fl-out"); const st = $("fl-status");
    st.textContent = "preguntando…"; st.removeAttribute("data-ok");
    out.setAttribute("data-open", "1"); out.textContent = "";
    const r = await FL.ask(q);
    if (r.ok) {
      st.textContent = `OK · ${r.ms} ms`; st.setAttribute("data-ok", "1");
      out.textContent = r.content;
    } else {
      st.textContent = "FALLO" + (r.status ? ` (${r.status})` : "") + (r.ms ? ` · ${r.ms} ms` : "");
      st.setAttribute("data-ok", "0");
      out.textContent = r.error || "sin detalle";
    }
  };
  $("fl-send").addEventListener("click", flAsk);
  $("fl-input").addEventListener("keydown", (e) => { if (e.key === "Enter") flAsk(); });
} else {
  $("fl-send").disabled = true;
  $("fl-status").textContent = "SOLO EN LA APP";
}

// ---------------------------------------------------------------------------
// VISOR DEL MUNDO: navegar la simulación por dentro (servicios y cortina).
// Todo pasa por el API de control, que solo hace GET: desde aquí no se opera.
// ---------------------------------------------------------------------------
const vista = { abierto: false, tab: "red", url: "", host: "", servicios: [], simbolos: [], simbolo: "",
                resp: null, reveal: null, maquina: { ruta: "/home/agent", items: [] },
                bolsa: { simbolos: [], simbolo: "", dias: 252, data: null } };
const viewer = document.getElementById("viewer");

function pintaVisor() {
  viewer.hidden = !vista.abierto;
  if (!vista.abierto) return;
  viewer.innerHTML = viewerHTML(vista);
}

async function traeJSON(ruta) {
  const r = await fetch(BASE + ruta, { cache: "no-store" });
  if (!r.ok && r.status !== 404 && r.status !== 400 && r.status !== 502) throw new Error("HTTP " + r.status);
  return r.json();
}

async function abrirVisor() {
  vista.abierto = true;
  pintaVisor();
  try {
    const d = await traeJSON("/world/services");
    vista.servicios = d.servicios || [];
    vista.simbolos = d.simbolos || [];
    if (!vista.simbolo || !vista.simbolos.includes(vista.simbolo)) vista.simbolo = vista.simbolos[0] || "";
  } catch (e) { vista.servicios = []; vista.simbolos = []; }
  pintaVisor();
}

async function abrirUrl(url) {
  vista.url = url;
  vista.resp = { status: "…", host: "", path: " abriendo…", body: "" };
  pintaVisor();
  try {
    vista.resp = await traeJSON("/world/get?url=" + encodeURIComponent(url));
  } catch (e) {
    vista.resp = { error: String(e.message || e) };
  }
  pintaVisor();
}

async function verCarpeta(ruta) {
  if (!MAQ) { vista.maquina = { error: "esto solo funciona dentro de la app" }; pintaVisor(); return; }
  const r = await MAQ.ls(ruta);
  vista.maquina = r.ok
    ? { ruta: r.ruta, items: r.items, archivo: "", contenido: "", render: false }
    : { ruta, items: [], error: r.error || "no se pudo mirar la carpeta (¿hay mundo en marcha?)" };
  pintaVisor();
}

async function verArchivo(ruta) {
  if (!MAQ) return;
  const r = await MAQ.read(ruta);
  const m = vista.maquina || {};
  vista.maquina = r.ok
    ? { ...m, archivo: r.ruta, contenido: r.contenido, cortado: r.cortado, render: /\.html?$/i.test(r.ruta) }
    : { ...m, archivo: ruta, contenido: "", error: r.error };
  pintaVisor();
}

async function verBolsa(sym, dias) {
  const b = vista.bolsa;
  b.simbolos = vista.simbolos;
  if (sym) b.simbolo = sym;
  if (dias) b.dias = dias;
  if (!b.simbolo) b.simbolo = b.simbolos[0] || "";
  if (!b.simbolo) { b.error = "este mundo no tiene bolsa"; pintaVisor(); return; }
  pintaVisor();
  try {
    const d = await traeJSON(`/world/stock?symbol=${encodeURIComponent(b.simbolo)}&days=${b.dias}`);
    if (d.error) { b.data = null; b.error = d.error; } else { b.data = d; b.error = ""; }
  } catch (e) { b.data = null; b.error = String(e.message || e); }
  pintaVisor();
}

document.getElementById("v-open").addEventListener("click", abrirVisor);

viewer.addEventListener("click", async (e) => {
  const t = e.target;
  if (!t || !t.closest) return;
  if (t.id === "v-close") { vista.abierto = false; pintaVisor(); return; }
  if (t.id === "v-go") { abrirUrl(document.getElementById("v-url").value.trim()); return; }
  if (t.id === "v-tab-red") { vista.tab = "red"; pintaVisor(); return; }
  if (t.id === "v-tab-bolsa") { vista.tab = "bolsa"; verBolsa(); return; }
  const sym = t.closest(".b-sym");
  if (sym) { verBolsa(sym.getAttribute("data-sym")); return; }
  const rango = t.closest(".b-rango");
  if (rango) { verBolsa(null, parseInt(rango.getAttribute("data-dias"), 10)); return; }
  if (t.id === "v-tab-maquina") {
    vista.tab = "maquina"; pintaVisor();
    verCarpeta((vista.maquina && vista.maquina.ruta) || "/home/agent");
    return;
  }
  if (t.id === "v-render") { vista.maquina.render = !vista.maquina.render; pintaVisor(); return; }
  const dir = t.closest(".v-file.dir") || t.closest(".v-miga");
  if (dir) { verCarpeta(dir.getAttribute("data-dir") || dir.getAttribute("data-file")); return; }
  const arch = t.closest(".v-file");
  if (arch && arch.getAttribute("data-file")) { verArchivo(arch.getAttribute("data-file")); return; }
  if (t.id === "v-tab-cortina") {
    vista.tab = "cortina";
    pintaVisor();
    try { vista.reveal = await traeJSON("/world/reveal"); } catch (err) { vista.reveal = null; }
    pintaVisor();
    return;
  }
  const host = t.closest(".v-host");
  if (host) { vista.host = vista.host === host.getAttribute("data-host") ? "" : host.getAttribute("data-host"); pintaVisor(); return; }
  const ruta = t.closest(".v-ruta");
  if (ruta) {
    let url = ruta.getAttribute("data-url");
    if (url.includes("{sym}")) url = url.replaceAll("{sym}", vista.simbolo || "");
    abrirUrl(url);
  }
});

viewer.addEventListener("change", (e) => {
  if (e.target && e.target.id === "v-sym") vista.simbolo = e.target.value;
});

viewer.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target && e.target.id === "v-url") abrirUrl(e.target.value.trim());
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && vista.abierto) { vista.abierto = false; pintaVisor(); } });

refreshHistory().then(refreshLaya).then(poll);
setInterval(poll, 1000);
setInterval(refreshLaya, 1500);
setInterval(refreshHistory, 15000);
