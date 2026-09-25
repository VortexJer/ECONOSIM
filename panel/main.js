// Proceso principal de Electron: ventana que carga el panel.
// El panel LEE el mundo (y controla el reloj) por el API de control en localhost, y
// puede LANZAR el entrenamiento en este PC (training/pipeline.py) mostrando su log.
const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const CONTROL = process.env.ECONOSIM_CONTROL || "http://127.0.0.1:8080";

// Raíz del proyecto: junto a panel/ en desarrollo; en el .exe empaquetado, la
// carpeta del proyecto se localiza subiendo desde dist/win-unpacked.
function projectRoot() {
  const candidates = [
    path.resolve(__dirname, ".."),                 // panel/ -> proyecto
    path.resolve(process.resourcesPath || "", "..", "..", ".."),   // dist/win-unpacked -> panel -> proyecto
    path.resolve(process.execPath, "..", "..", "..", ".."),
  ];
  for (const c of candidates) {
    if (fs.existsSync(path.join(c, "training", "pipeline.py"))) return c;
  }
  return candidates[0];
}

let win = null;
let trainProc = null;
let trainLog = [];

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    backgroundColor: "#0a0b0d",
    title: "ECONOSIM · Panel",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.webContents.on("did-finish-load", () => {
    win.webContents.executeJavaScript(`window.ECONOSIM_CONTROL = ${JSON.stringify(CONTROL)};`);
  });
  win.loadFile(path.join(__dirname, "index.html"));
}

function sendLog(line) {
  trainLog.push(line);
  if (trainLog.length > 400) trainLog = trainLog.slice(-300);
  if (win && !win.isDestroyed()) win.webContents.send("train:log", line);
}

// --- entrenamiento: lanzar / parar / estado --------------------------------
// Fase 14: el cerebro es Laya y se entrena por generaciones (training/laya_generations.py);
// los profesores LLM (overnight.py) quedan fuera del panel. Corre hasta que se pulse PARAR.
ipcMain.handle("train:start", (_e, opts) => {
  if (trainProc) return { ok: false, error: "ya hay un entrenamiento en marcha" };
  const root = projectRoot();
  // la evolución (estrategias evolutivas) es el entrenamiento por generaciones de verdad
  const script = path.join(root, "training", "laya_es.py");
  if (!fs.existsSync(script)) return { ok: false, error: "no encuentro training/laya_es.py en " + root };
  const venvPy = path.join(root, "training", ".venv", "Scripts", "python.exe");
  const py = fs.existsSync(venvPy) ? venvPy : "python";
  const n = (v, d) => (Number.isFinite(Number(v)) && Number(v) >= 0 ? String(Number(v)) : String(d));
  const args = ["-u", script, "--lives", n(opts.lives, 16), "--min-invested", n(opts.minInvested, 0.5)];
  if (opts.brain === "num") args.push("--brain", "num");
  trainLog = [];
  trainProc = spawn(py, args, { cwd: path.join(root, "training"), windowsHide: true,
                                env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" } });
  sendLog(`[panel] lanzado: python ${args.map((a) => path.basename(a)).join(" ")}`);
  const onData = (buf) => buf.toString("utf-8").split(/\r?\n/).filter(Boolean).forEach(sendLog);
  trainProc.stdout.on("data", onData);
  trainProc.stderr.on("data", onData);
  trainProc.on("close", (code) => {
    sendLog(`[panel] terminado (código ${code})`);
    trainProc = null;
    if (win && !win.isDestroyed()) win.webContents.send("train:done", { code });
  });
  return { ok: true };
});

ipcMain.handle("train:stop", () => {
  if (!trainProc) return { ok: false };
  try { spawn("taskkill", ["/pid", String(trainProc.pid), "/T", "/F"], { windowsHide: true }); } catch (e) { trainProc.kill(); }
  sendLog("[panel] detenido por el usuario");
  return { ok: true };
});

// --- la máquina de la IA: mirar lo que ha construido dentro de su servidor ---
// Solo lectura, y solo dentro del contenedor del agente. Es la otra mitad de "ver
// el mundo": no basta con los servicios, hay que poder abrir las webs que monta.
const CONTENEDOR = "econosim-agent";
function docker(args, limiteBytes = 400000) {
  return new Promise((resolve) => {
    const p = spawn("docker", args, { windowsHide: true });
    let out = "", err = "", cortado = false;
    p.stdout.on("data", (b) => {
      if (out.length < limiteBytes) out += b.toString("utf-8"); else cortado = true;
    });
    p.stderr.on("data", (b) => { err += b.toString("utf-8"); });
    p.on("error", (e) => resolve({ ok: false, error: String(e.message || e) }));
    p.on("close", (code) => resolve(code === 0
      ? { ok: true, out, cortado }
      : { ok: false, error: (err || out || "").trim().slice(0, 300) || ("docker salió con " + code) }));
    setTimeout(() => { try { p.kill(); } catch (e) {} }, 15000);
  });
}

// rutas permitidas: su casa y lo que sirva por web. Nada de husmear el sistema.
const chr0 = String.fromCharCode(0);
const lineas = (t) => String(t).split(String.fromCharCode(10))
  .map((x) => x.replace(String.fromCharCode(13), "")).filter(Boolean);
const RAICES = ["/home/agent", "/var/www", "/srv", "/opt/app", "/etc/nginx"];
function rutaValida(ruta) {
  const r = String(ruta || "").split("\\").join("/");
  if (r.includes("..") || r.indexOf(chr0) >= 0) return null;
  return RAICES.some((base) => r === base || r.startsWith(base + "/")) ? r : null;
}

ipcMain.handle("agent:ls", async (_e, ruta) => {
  const r = rutaValida(ruta || "/home/agent");
  if (!r) return { ok: false, error: "ruta fuera de la máquina de la IA" };
  // -p marca los directorios con / al final; así el panel sabe qué se puede abrir
  const res = await docker(["exec", CONTENEDOR, "sh", "-c", `ls -Ap1 -- ${JSON.stringify(r)} 2>&1`]);
  if (!res.ok) return res;
  const items = lineas(res.out).filter(Boolean).map((n) => {
    const dir = n.endsWith("/");
    const nombre = dir ? n.slice(0, -1) : n;
    return { nombre, dir, ruta: (r === "/" ? "" : r) + "/" + nombre };
  });
  return { ok: true, ruta: r, items };
});

ipcMain.handle("agent:read", async (_e, ruta) => {
  const r = rutaValida(ruta);
  if (!r) return { ok: false, error: "ruta fuera de la máquina de la IA" };
  const res = await docker(["exec", CONTENEDOR, "sh", "-c", `head -c 400000 -- ${JSON.stringify(r)}`]);
  if (!res.ok) return res;
  return { ok: true, ruta: r, contenido: res.out, cortado: !!res.cortado };
});

// --- probar freellm directamente (sin mundo): usa las credenciales del .env del proyecto ---
function readDotenv() {
  const out = {};
  try {
    const f = path.join(projectRoot(), ".env");
    if (fs.existsSync(f)) {
      for (const line of fs.readFileSync(f, "utf-8").split(/\r?\n/)) {
        const t = line.trim();
        if (t && !t.startsWith("#") && t.includes("=")) {
          const i = t.indexOf("=");
          out[t.slice(0, i).trim()] = t.slice(i + 1).trim();
        }
      }
    }
  } catch (e) { /* sin .env */ }
  return out;
}

ipcMain.handle("freellm:ask", async (_e, prompt) => {
  const env = readDotenv();
  const base = (env.ECONOSIM_UPSTREAM_BASE_URL || "").replace(/\/$/, "");
  const key = env.ECONOSIM_UPSTREAM_API_KEY || "";
  if (!base || !key) return { ok: false, error: "faltan ECONOSIM_UPSTREAM_BASE_URL / API_KEY en .env" };
  const t0 = Date.now();
  try {
    const r = await fetch(base + "/chat/completions", {
      method: "POST",
      headers: { "Authorization": "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ model: "auto", messages: [{ role: "user", content: String(prompt || "").slice(0, 6000) }], max_tokens: 500 }),
      signal: AbortSignal.timeout(90000),
    });
    const text = await r.text();
    const ms = Date.now() - t0;
    if (!r.ok) {
      const why = /blocked/i.test(text) ? "Bloqueado por Cloudflare (WAF)" : text.slice(0, 220);
      return { ok: false, status: r.status, error: why, ms };
    }
    let content = "";
    try { content = JSON.parse(text).choices[0].message.content || ""; }
    catch (e) { content = text.slice(0, 800); }
    return { ok: true, content, ms };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e).slice(0, 200), ms: Date.now() - t0 };
  }
});

ipcMain.handle("train:status", () => ({ running: !!trainProc, log: trainLog.slice(-120) }));

// --- cerebro Laya en vivo: el live.json de la ejecución más reciente -------------
// Devuelve las ejecuciones con live.json (las 4 más recientes, sin las DESCARTADAS) y, de
// cada una, las vidas de su campeona actual (lives.jsonl: curva y cada compra/venta).
ipcMain.handle("laya:live", () => {
  const dir = path.join(projectRoot(), "training", "laya_runs");
  if (!fs.existsSync(dir)) return { ok: false };
  const runs = [];
  for (const name of fs.readdirSync(dir)) {
    const f = path.join(dir, name, "live.json");
    if (!fs.existsSync(f) || fs.existsSync(path.join(dir, name, "DESCARTADA.txt"))) continue;
    runs.push({ name, f, m: fs.statSync(f).mtimeMs });
  }
  runs.sort((a, b) => b.m - a.m);
  const out = [];
  for (const r of runs.slice(0, 4)) {
    try {
      const data = JSON.parse(fs.readFileSync(r.f, "utf-8"));
      let lives = [];
      const lf = path.join(dir, r.name, "lives.jsonl");
      if (fs.existsSync(lf)) {
        const rows = fs.readFileSync(lf, "utf-8").split(/\r?\n/).filter(Boolean).map((l) => {
          try { return JSON.parse(l); } catch (e) { return null; }
        }).filter(Boolean);
        const g = Math.max(...rows.map((x) => x.gen ?? 0));
        lives = rows.filter((x) => (x.gen ?? 0) === g);
      }
      out.push({ name: r.name, data, age: (Date.now() - r.m) / 1000, lives });
    } catch (e) { /* leyendo mientras se escribe: vale el siguiente sondeo */ }
  }
  return out.length ? { ok: true, runs: out } : { ok: false };
});

// --- generaciones anteriores: vidas ya jugadas, leídas del disco ------------
ipcMain.handle("history:list", () => {
  const root = projectRoot();
  const dir = path.join(root, "training", "data", "episodes");
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const name of fs.readdirSync(dir)) {
    const ep = path.join(dir, name);
    const oc = path.join(ep, "outcome.json");
    if (!fs.existsSync(oc)) continue;
    try {
      const outcome = JSON.parse(fs.readFileSync(oc, "utf-8"));
      const cp = path.join(ep, "calendar.json");
      const calendar = fs.existsSync(cp) ? JSON.parse(fs.readFileSync(cp, "utf-8")) : null;
      const tp = path.join(ep, "thoughts.json");
      const thoughts = fs.existsSync(tp) ? JSON.parse(fs.readFileSync(tp, "utf-8")) : [];
      let sessions = 0;
      const sd = path.join(ep, "sessions");
      if (fs.existsSync(sd)) sessions = fs.readdirSync(sd).filter((f) => f.endsWith(".jsonl")).length;
      out.push({ seed: name, outcome, calendar, thoughts, sessions, mtime: fs.statSync(oc).mtimeMs });
    } catch (e) { /* vida corrupta: se ignora */ }
  }
  out.sort((a, b) => b.mtime - a.mtime);
  return out.slice(0, 24);
});

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
