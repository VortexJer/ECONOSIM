// Lógica de presentación del panel. Pura y determinista: se testea sin DOM.
// Sin emojis: solo texto, cifras e instrumentos.

export function eur(cents) {
  if (cents === null || cents === undefined) return "—";
  const neg = cents < 0;
  const c = Math.abs(Math.round(cents));
  const whole = Math.floor(c / 100).toString();
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, " "); // espacio fino como separador
  return `${neg ? "−" : ""}${grouped}.${String(c % 100).padStart(2, "0")}`;
}

export function usd(x) {
  if (x === null || x === undefined) return "—";
  return `$${Number(x).toFixed(2)}`;
}

export function num(x) {
  if (x === null || x === undefined) return "—";
  return Math.round(x).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

// días de vida al ritmo de gasto actual
export function daysLeft(d) {
  if (d.days_left !== null && d.days_left !== undefined) return d.days_left;
  const burn = d.daily_burn_cents || 0;
  return burn > 0 ? (d.balance_cents || 0) / burn : null;
}

// severidad de supervivencia: gobierna el color del indicador dominante
export function survival(days, alive) {
  if (!alive) return "dead";
  if (days === null) return "unknown";
  if (days < 7) return "critical";
  if (days < 30) return "warn";
  return "ok";
}

export function fmtDate(iso) {
  if (!iso) return "—";
  // 2043-05-27T13:30:00+00:00 -> "27 MAY 2043 · 13:30"
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (!m) return iso;
  const MES = ["ENE","FEB","MAR","ABR","MAY","JUN","JUL","AGO","SEP","OCT","NOV","DIC"];
  return `${m[3]} ${MES[parseInt(m[2],10)-1]} ${m[1]} · ${m[4]}:${m[5]}`;
}

export const SPEEDS = [
  { label: "PAUSA", value: 0 },
  { label: "1×", value: 1 },
  { label: "10×", value: 10 },
  { label: "100×", value: 100 },
  { label: "1K×", value: 1000 },
  { label: "10K×", value: 10000 },
];

function cell(text, cls = "") { return `<td class="${cls}">${esc(text)}</td>`; }
function esc(s) {
  return String(s === undefined || s === null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
// dentro de un atributo hay que escapar además las comillas, o el comando parte el HTML
function escAttr(s) { return esc(s).replace(/"/g, "&quot;"); }

function ledgerRows(entries) {
  const bank = (entries || []).filter((e) => e.account === "bank").slice(-16).reverse();
  if (!bank.length) return `<tr><td colspan="4" class="empty">SIN MOVIMIENTOS</td></tr>`;
  return bank.map((e) => {
    const neg = e.amount_cents < 0;
    const date = (e.display_ts || "").slice(0, 10);
    return `<tr>${cell(date, "dim")}${cell(e.concept)}${cell(e.counterparty || "—", "dim")}` +
      `<td class="amt ${neg ? "neg" : "pos"}">${eur(e.amount_cents)}</td></tr>`;
  }).join("");
}

function actionRows(actions) {
  const a = (actions || []).slice(-12).reverse();
  if (!a.length) return `<tr><td colspan="4" class="empty">SIN ACCIONES</td></tr>`;
  return a.map((x) =>
    `<tr>${cell(x.category, "dim")}${cell(x.title)}${cell(num(x.units), "amt")}` +
    `<td class="amt">${usd(x.revenue_usd)}</td></tr>`).join("");
}

function incidentRows(score) {
  const exp = (score && score.expediente) || [];
  const rows = exp.slice(-10).reverse();
  if (!rows.length) return `<tr><td colspan="3" class="empty">EXPEDIENTE LIMPIO</td></tr>`;
  return rows.map((e) => {
    const st = e.status === "sanctioned" ? "sancion" : (e.detected ? "detect" : "latente");
    return `<tr>${cell(e.domain, "dim")}${cell(e.kind)}<td class="tag ${st}">${st.toUpperCase()}</td></tr>`;
  }).join("");
}

function campaignRows(cs) {
  const rows = cs || [];
  if (!rows.length) return `<tr><td colspan="4" class="empty">SIN CAMPAÑAS</td></tr>`;
  return rows.map((c) =>
    `<tr>${cell(c.platform, "dim")}${cell(c.status)}${cell(num(c.clicks), "amt")}` +
    `<td class="amt">${usd(c.spend_usd)}</td></tr>`).join("");
}

// Cartera: cada posición con su valor actual y cuánto va por encima/por debajo.
function positionRows(positions) {
  const rows = positions || [];
  if (!rows.length) return `<tr><td colspan="5" class="empty">SIN ACCIONES</td></tr>`;
  return rows.map((p) => {
    const pl = p.unrealized_cents || 0;
    const pct = p.cost_cents ? (100 * pl / p.cost_cents) : 0;
    const cls = pl < 0 ? "neg" : "pos";
    return `<tr>${cell(p.symbol, "dim")}${cell(num(p.qty))}` +
      `<td class="amt">${eur(p.market_value_cents)}</td>` +
      `<td class="amt ${cls}">${pl < 0 ? "−" : "+"}${eur(Math.abs(pl))}</td>` +
      `<td class="amt ${cls}">${pct < 0 ? "−" : "+"}${Math.abs(pct).toFixed(1)}%</td></tr>`;
  }).join("");
}

// Diario de pensamiento: una línea por llamada al cerebro (qué razonó y qué hace).
function thoughtRows(thoughts) {
  const rows = (thoughts || []).slice(-12).reverse();
  if (!rows.length) return `<div class="empty-t">AÚN NO HA PENSADO NADA</div>`;
  return rows.map((t) => {
    const hora = /T(\d{2}:\d{2})/.exec(t.ts || "");
    // "doing" ya viene masticado por el mundo (deducido del comando, sin gastar una
    // llamada); el comando en crudo se guarda en el title, para quien quiera mirarlo.
    return `<div class="thought"${t.detalle ? ` title="${escAttr(t.detalle)}"` : ""}>` +
      `<span class="t-time">${esc(hora ? hora[1] : "—")}</span>` +
      (t.said ? `<span class="t-said">${esc(t.said)}</span>` : "") +
      (t.doing ? `<span class="t-doing">${esc(t.doing)}</span>` : "") +
      `</div>`;
  }).join("");
}

// descripción corta de una acción de egreso bloqueada, a partir de su detalle
function egressWhat(j) {
  const d = j.detail || {};
  if (d.symbol) return `${esc(d.symbol)} ${esc(d.side || "")} ${esc(num(d.qty))}`;
  if (d.domain) return esc(d.domain);
  if (d.subject) return esc(d.subject);
  if (d.name) return esc(d.name);
  if (d.event) return `${esc(d.event)} ${esc(d.outcome || "")}`;
  if (d.charge) return esc(d.charge);
  if (d.product) return esc(d.product);
  return "—";
}

function egressRows(journal) {
  const rows = (journal || []).slice(-14).reverse();
  if (!rows.length) return `<tr><td colspan="3" class="empty">NADA HA SALIDO</td></tr>`;
  return rows.map((j) =>
    `<tr>${cell((j.ts || "").slice(0, 10), "dim")}` +
    `<td><b>${esc(j.service)}</b> · ${esc(j.op)}</td>` +
    `<td class="tag latente">${egressWhat(j)}</td></tr>`).join("");
}


// ============================================================================
// VISOR DEL MUNDO: abrir la simulación por dentro como si navegaras por ella.
// Los servicios que ve la IA, y detrás de la cortina, la fecha real y qué
// empresa es cada alias. Solo lectura: es una ventana, no un mando.
// ============================================================================
export function serviciosHTML(servicios, sel) {
  const rows = servicios || [];
  if (!rows.length) return `<div class="empty-t">MUNDO APAGADO</div>`;
  return rows.map((s) => {
    const abierto = s.host === sel;
    const rutas = abierto
      ? `<div class="v-rutas">${(s.rutas || []).map((r) =>
          `<button class="v-ruta" data-url="${escAttr(s.host + r)}">${esc(r)}</button>`).join("")}</div>`
      : "";
    return `<div class="v-srv${abierto ? " open" : ""}">` +
      `<button class="v-host" data-host="${escAttr(s.host)}">${esc(s.host)}</button>${rutas}</div>`;
  }).join("");
}

// el cuerpo de la respuesta: JSON con sangría si lo es, texto tal cual si no
export function cuerpoHTML(resp) {
  if (!resp) return `<div class="empty-t">ESCRIBE UNA DIRECCIÓN Y PULSA ABRIR</div>`;
  if (resp.error) return `<div class="v-err">${esc(resp.error)}</div>`;
  let cuerpo = resp.body || "";
  try { cuerpo = JSON.stringify(JSON.parse(cuerpo), null, 2); } catch (e) { /* no era JSON */ }
  const cls = resp.status >= 400 ? "v-err" : "";
  return `<div class="v-meta ${cls}">${esc(resp.status)} · ${esc(resp.host || "")}${esc(resp.path || "")}</div>` +
    `<pre class="v-body">${esc(cuerpo)}</pre>`;
}

// detrás de la cortina: la época real y la identidad de cada empresa
export function cortinaHTML(rev) {
  if (!rev) return `<div class="empty-t">MUNDO APAGADO</div>`;
  const filas = (rev.empresas || []).map((e) =>
    `<tr>${cell(e.alias, "dim")}${cell(e.real)}` +
    `<td class="amt">${e.factor === null || e.factor === undefined ? "—" : e.factor.toFixed(5)}</td></tr>`).join("");
  return `<div class="v-cortina">` +
    `<div class="v-par"><span>FECHA QUE VE LA IA</span><b>${esc((rev.fecha_mostrada || "").slice(0, 16).replace("T", " "))}</b></div>` +
    `<div class="v-par"><span>FECHA REAL DE LOS DATOS</span><b>${esc((rev.fecha_real || "").slice(0, 16).replace("T", " "))}</b></div>` +
    `<div class="v-par"><span>DESFASE</span><b>${esc(rev.desfase_años)} años</b></div>` +
    `<div class="v-par"><span>ARRANQUE REAL</span><b>${esc(rev.arranque_real || "—")}</b></div>` +
    `<div class="v-par"><span>EPISODIO</span><b>${esc(rev.episodio || "—")}</b></div>` +
    `<table class="tbl"><thead><tr><th>ALIAS</th><th>EMPRESA REAL</th><th class="amt">FACTOR</th></tr></thead>` +
    `<tbody>${filas || `<tr><td colspan="3" class="empty">SIN EMPRESAS</td></tr>`}</tbody></table></div>`;
}

// La máquina de la IA por dentro: sus archivos y las webs que haya montado.
export function maquinaHTML(m) {
  const st = m || {};
  if (st.error) return `<div class="v-err">${esc(st.error)}</div>`;
  const partes = String(st.ruta || "/home/agent").split("/").filter(Boolean);
  const migas = partes.map((x, i) =>
    `<button class="v-miga" data-dir="/${esc(partes.slice(0, i + 1).join("/"))}">${esc(x)}</button>`).join("<i>/</i>");
  const items = (st.items || []).slice().sort((a, b) => (b.dir - a.dir) || a.nombre.localeCompare(b.nombre));
  const lista = items.length
    ? items.map((it) => `<button class="v-file${it.dir ? " dir" : ""}"` +
        ` data-${it.dir ? "dir" : "file"}="${escAttr(it.ruta)}">${esc(it.nombre)}${it.dir ? "/" : ""}</button>`).join("")
    : `<div class="empty-t">CARPETA VACÍA</div>`;
  const esWeb = /\.(html?|htm)$/i.test(st.archivo || "");
  let panel = `<div class="empty-t">ELIGE UN ARCHIVO</div>`;
  if (st.archivo) {
    const cab = `<div class="v-meta">${esc(st.archivo)}${st.cortado ? " · (recortado)" : ""}` +
      (esWeb ? ` · <button class="v-toggle" id="v-render">${st.render ? "VER CÓDIGO" : "VER LA WEB"}</button>` : "") +
      `</div>`;
    panel = cab + (esWeb && st.render
      ? `<iframe class="v-web" sandbox="" srcdoc="${escAttr(st.contenido || "")}"></iframe>`
      : `<pre class="v-body">${esc(st.contenido || "")}</pre>`);
  }
  return `<div class="v-maq"><div class="v-migas">${migas || "/"}</div>` +
    `<div class="v-maq-main"><div class="v-files">${lista}</div><div class="v-out">${panel}</div></div></div>`;
}

// ============================================================================
// LA BOLSA: elegir una acción y verla. Gráfico en SVG, sin librerías (la app
// no carga nada de fuera). Velas cuando caben; línea cuando hay demasiadas.
// ============================================================================
const G = { w: 1000, h: 340, izq: 8, der: 62, arr: 14, abj: 26 };   // lienzo y márgenes

export function grafico(barras, alto = G.h) {
  const b = barras || [];
  if (b.length < 2) return `<div class="empty-t">SIN COTIZACIÓN</div>`;
  const ancho = G.w - G.izq - G.der;
  const cuerpoAlto = alto - G.arr - G.abj;
  const altos = b.map((x) => x.h), bajos = b.map((x) => x.l);
  let max = Math.max(...altos), min = Math.min(...bajos);
  if (max === min) { max += 1; min -= 1; }
  const margen = (max - min) * 0.06;
  max += margen; min -= margen;
  const x = (i) => G.izq + (ancho * i) / Math.max(1, b.length - 1);
  const y = (v) => G.arr + cuerpoAlto * (1 - (v - min) / (max - min));
  const sube = b[b.length - 1].c >= b[0].c;

  // rejilla: cinco niveles de precio, etiquetados a la derecha
  let rejilla = "";
  for (let k = 0; k <= 4; k++) {
    const v = min + ((max - min) * k) / 4;
    const yy = y(v).toFixed(1);
    rejilla += `<line class="g-grid" x1="${G.izq}" y1="${yy}" x2="${G.izq + ancho}" y2="${yy}"/>` +
      `<text class="g-eje" x="${G.izq + ancho + 6}" y="${(y(v) + 3.5).toFixed(1)}">${v.toFixed(2)}</text>`;
  }

  let dibujo;
  if (b.length <= 130) {
    const paso = ancho / b.length;
    const cuerpo = Math.max(1.4, Math.min(9, paso * 0.62));
    dibujo = b.map((v, i) => {
      const cx = (G.izq + paso * (i + 0.5)).toFixed(2);
      const arriba = y(Math.max(v.o, v.c)), abajo = y(Math.min(v.o, v.c));
      const cls = v.c >= v.o ? "g-up" : "g-dn";
      const alto2 = Math.max(1, abajo - arriba);
      return `<line class="${cls} g-mecha" x1="${cx}" y1="${y(v.h).toFixed(2)}" x2="${cx}" y2="${y(v.l).toFixed(2)}"/>` +
        `<rect class="${cls}" x="${(cx - cuerpo / 2).toFixed(2)}" y="${arriba.toFixed(2)}" ` +
        `width="${cuerpo.toFixed(2)}" height="${alto2.toFixed(2)}"/>`;
    }).join("");
  } else {
    const linea = b.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v.c).toFixed(1)}`).join(" ");
    const area = `${linea} L${x(b.length - 1).toFixed(1)},${(G.arr + cuerpoAlto).toFixed(1)} ` +
      `L${x(0).toFixed(1)},${(G.arr + cuerpoAlto).toFixed(1)} Z`;
    dibujo = `<path class="g-area ${sube ? "up" : "dn"}" d="${area}"/>` +
      `<path class="g-linea ${sube ? "up" : "dn"}" d="${linea}"/>`;
  }

  // fechas: primera, media y última
  const marcas = [0, Math.floor(b.length / 2), b.length - 1].map((i) =>
    `<text class="g-eje" x="${x(i).toFixed(1)}" y="${alto - 8}" text-anchor="${i === 0 ? "start" : i === b.length - 1 ? "end" : "middle"}">${esc(b[i].d)}</text>`).join("");

  return `<svg class="g-svg" viewBox="0 0 ${G.w} ${alto}" preserveAspectRatio="none" role="img">` +
    `${rejilla}${dibujo}${marcas}</svg>`;
}

function pct(x) {
  if (x === null || x === undefined) return `<span class="dim">—</span>`;
  const cls = x < 0 ? "neg" : "pos";
  return `<span class="${cls}">${x < 0 ? "−" : "+"}${Math.abs(x).toFixed(2)}%</span>`;
}

function numeroFila(etiqueta, valor, sufijo = "") {
  const v = valor === null || valor === undefined ? "—" : (typeof valor === "number" ? valor.toFixed(2) : valor);
  return `<div class="b-dato"><span>${esc(etiqueta)}</span><b>${esc(v)}${esc(sufijo)}</b></div>`;
}

export const RANGOS = [
  { label: "1M", dias: 21 }, { label: "3M", dias: 63 }, { label: "1A", dias: 252 },
  { label: "5A", dias: 1260 }, { label: "TODO", dias: 5000 },
];

export function bolsaHTML(st) {
  const b = st || {};
  const lista = (b.simbolos || []).map((x) =>
    `<button class="b-sym${x === b.simbolo ? " on" : ""}" data-sym="${escAttr(x)}">${esc(x)}</button>`).join("")
    || `<div class="empty-t">MUNDO APAGADO</div>`;
  const d = b.data;
  if (!d) {
    return `<div class="b-wrap"><div class="b-list">${lista}</div>` +
      `<div class="b-main"><div class="empty-t">${esc(b.error || "ELIGE UNA ACCIÓN")}</div></div></div>`;
  }
  const rangos = RANGOS.map((r) =>
    `<button class="train-btn b-rango"${r.dias === b.dias ? ' data-on="1"' : ""} data-dias="${r.dias}">${r.label}</button>`).join("");
  const n = d.numeros || {};
  const c = d.cartera;
  const cabecera = `<div class="b-head"><div class="b-name">${esc(d.symbol)}</div>` +
    `<div class="b-precio">${d.precio === null || d.precio === undefined ? "—" : d.precio.toFixed(2)}</div>` +
    `<div class="b-vars">DÍA ${pct(d.var_1d)} · MES ${pct(d.var_1m)} · AÑO ${pct(d.var_1a)}</div>` +
    `<div class="b-estado">${d.abierto ? "MERCADO ABIERTO" : "MERCADO CERRADO"} · ${esc(d.dia || "")}</div>` +
    `<div class="b-rangos">${rangos}</div></div>`;
  const posicion = c
    ? `<div class="b-pos ${c.resultado < 0 ? "neg" : "pos"}">EN CARTERA · ${num(c.qty)} títulos a ${c.precio_medio.toFixed(2)}` +
      ` · valor ${eur(Math.round(c.valor * 100))} · ${c.resultado < 0 ? "−" : "+"}${eur(Math.round(Math.abs(c.resultado) * 100))} (${pct(c.resultado_pct)})</div>`
    : `<div class="b-pos vacia">NO TENEMOS ESTA ACCIÓN</div>`;
  const numeros = d.numeros
    ? `<div class="b-datos">` +
      numeroFila("PER", n.per) + numeroFila("PRECIO/VENTAS", n.precio_ventas) +
      numeroFila("PRECIO/VALOR CONTABLE", n.precio_valor_contable) +
      numeroFila("MARGEN NETO", n.margen_neto === null || n.margen_neto === undefined ? null : n.margen_neto * 100, "%") +
      numeroFila("ROE", n.roe === null || n.roe === undefined ? null : n.roe * 100, "%") +
      numeroFila("DEUDA/FONDOS PROPIOS", n.deuda_fondos_propios) +
      numeroFila("CRECIMIENTO INTERANUAL", n.crecimiento === null || n.crecimiento === undefined ? null : n.crecimiento * 100, "%") +
      numeroFila("PRÓXIMOS RESULTADOS", n.proximos_resultados || "—") +
      `</div>`
    : `<div class="b-datos"><div class="empty-t">ESTA NO PRESENTA CUENTAS</div></div>`;
  return `<div class="b-wrap"><div class="b-list">${lista}</div>` +
    `<div class="b-main">${cabecera}${grafico(d.barras)}${posicion}${numeros}</div></div>`;
}

export function viewerHTML(v) {
  const st = v || {};
  return `<div class="v-top">` +
    `<div class="v-title">VISOR DEL MUNDO</div>` +
    `<input id="v-url" class="v-url" value="${escAttr(st.url || "")}" placeholder="financialmodelingprep.com/api/v3/ratios-ttm/…" />` +
    `<button class="train-btn" id="v-go">ABRIR</button>` +
    `<select id="v-sym" class="v-sym" title="empresa que se usa en las rutas de ejemplo">` +
    (st.simbolos || []).map((x) =>
      `<option${x === st.simbolo ? " selected" : ""}>${esc(x)}</option>`).join("") + `</select>` +
    `<button class="train-btn" id="v-tab-red" ${st.tab !== "cortina" ? 'data-on="1"' : ""}>SERVICIOS</button>` +
    `<button class="train-btn" id="v-tab-bolsa" ${st.tab === "bolsa" ? 'data-on="1"' : ""}>BOLSA</button>` +
    `<button class="train-btn" id="v-tab-maquina" ${st.tab === "maquina" ? 'data-on="1"' : ""}>SU MÁQUINA</button>` +
    `<button class="train-btn" id="v-tab-cortina" ${st.tab === "cortina" ? 'data-on="1"' : ""}>TRAS LA CORTINA</button>` +
    `<button class="train-btn stop" id="v-close">CERRAR</button></div>` +
    (st.tab === "bolsa" ? `<div class="v-main solo">${bolsaHTML(st.bolsa)}</div>`
      : st.tab === "cortina" ? `<div class="v-main solo">${cortinaHTML(st.reveal)}</div>`
      : st.tab === "maquina" ? `<div class="v-main solo">${maquinaHTML(st.maquina)}</div>`
      : `<div class="v-main"><div class="v-side">${serviciosHTML(st.servicios, st.host)}</div>` +
        `<div class="v-out">${cuerpoHTML(st.resp)}</div></div>`);
}

// ============================================================================
// CALENDARIO DE PROGRESO
//   rojo = el día en que estamos · amarillo = los que quedan hasta el horizonte
//   naranja = los ya vividos · blanco = fuera del episodio
// ============================================================================
const DOW = ["L", "M", "X", "J", "V", "S", "D"];
const MESES_L = ["ENERO","FEBRERO","MARZO","ABRIL","MAYO","JUNIO","JULIO","AGOSTO","SEPTIEMBRE","OCTUBRE","NOVIEMBRE","DICIEMBRE"];

function ymd(d) { return d.toISOString().slice(0, 10); }
function parseDay(s) { const [y, m, d] = s.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); }

// meses que cubre el episodio, con sus días (los de fuera del rango van en blanco)
function monthsOf(cal) {
  const byDate = {};
  for (const d of (cal.days || [])) byDate[d.date] = d;
  const start = parseDay(cal.start), end = parseDay(cal.end);
  const months = [];
  let cur = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
  while (cur <= end) {
    const y = cur.getUTCFullYear(), m = cur.getUTCMonth();
    const first = new Date(Date.UTC(y, m, 1));
    const daysInMonth = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    const lead = (first.getUTCDay() + 6) % 7;            // lunes = 0
    const cells = [];
    for (let i = 0; i < lead; i++) cells.push(null);
    for (let dd = 1; dd <= daysInMonth; dd++) {
      const key = ymd(new Date(Date.UTC(y, m, dd)));
      cells.push({ key, day: dd, info: byDate[key] || null });
    }
    months.push({ y, m, cells });
    cur = new Date(Date.UTC(y, m + 1, 1));
  }
  return months;
}

function dayClass(info) {
  if (!info) return "outside";
  return info.state;   // past | today | future
}

function calendarGrid(cal, opts = {}) {
  const small = !!opts.small;
  const selected = opts.selected || "";
  const calId = opts.calId || "live";
  return monthsOf(cal).map(({ y, m, cells }) => `
    <div class="cal-month${small ? " small" : ""}">
      <div class="cal-mh">${MESES_L[m]} ${y}</div>
      <div class="cal-grid">
        ${DOW.map((d) => `<div class="cal-dow">${d}</div>`).join("")}
        ${cells.map((c) => {
          if (!c) return `<div class="cal-day blank"></div>`;
          const cls = dayClass(c.info);
          const info = c.info;
          const bal = info ? (info.balance_end !== null && info.balance_end !== undefined ? info.balance_end : info.balance_start) : null;
          const clickable = info && (cls === "past" || cls === "today");
          return `<div class="cal-day ${cls}${selected === c.key ? " sel" : ""}"${info ? ` data-date="${c.key}" data-cal="${calId}"` : ""}${clickable ? ` data-click="1"` : ""}>` +
            `<span class="cal-n">${c.day}</span>` +
            (info && !small ? `<span class="cal-b">${bal === null ? "" : eur(bal)}</span>` : "") +
            (info && info.calls && !small ? `<span class="cal-c">${info.calls}</span>` : "") +
            `</div>`;
        }).join("")}
      </div>
    </div>`).join("");
}

// detalle del día seleccionado: dinero inicial -> final, gasto, ingreso, llamadas y los logs
function dayDetail(cal, selected, thoughts) {
  const info = (cal.days || []).find((d) => d.date === selected);
  if (!info) return `<div class="cal-hint">PULSA UN DÍA VIVIDO PARA VER SU PROGRESO</div>`;
  const logs = (thoughts || []).filter((t) => (t.ts || "").slice(0, 10) === selected);
  const hasEnd = info.balance_end !== null && info.balance_end !== undefined;
  const end = hasEnd ? eur(info.balance_end) : "—";
  const delta = hasEnd ? info.balance_end - info.balance_start : null;
  const deltaTxt = delta === null ? "" : ` (${delta < 0 ? "−" : "+"}${eur(Math.abs(delta))})`;
  return `
    <div class="cal-detail">
      <div class="cal-dh">${esc(fmtDate(selected + "T00:00:00+00:00").split(" · ")[0])} · ${info.state === "today" ? "HOY" : "VIVIDO"}</div>
      <div class="kv"><span>DINERO INICIAL</span><b>${eur(info.balance_start)}</b></div>
      <div class="kv"><span>DINERO FINAL</span><b class="${delta === null ? "" : (delta < 0 ? "neg" : "pos")}">${end}${deltaTxt}</b></div>
      <div class="kv"><span>GASTADO</span><b>${eur(info.spent)}</b></div>
      <div class="kv"><span>INGRESADO</span><b>${eur(info.earned)}</b></div>
      <div class="kv"><span>LLAMADAS AL CEREBRO</span><b>${num(info.calls)}</b></div>
      <div class="cal-logs">${logs.length ? thoughtRows(logs) : `<div class="empty-t">SIN LOGS RECIENTES DE ESTE DÍA</div>`}</div>
    </div>`;
}

export function calendarHTML(cal, opts = {}) {
  if (!cal || !cal.days) return `<div class="empty-t">SIN CALENDARIO</div>`;
  const past = cal.days.filter((d) => d.state === "past").length;
  const left = cal.days.filter((d) => d.state === "future").length;
  return `
    <div class="cal-legend">
      <span class="lg today">HOY</span><span class="lg future">QUEDAN ${left}</span>
      <span class="lg past">VIVIDOS ${past}</span><span class="lg outside">FUERA</span>
    </div>
    <div class="cal-months">${calendarGrid(cal, opts)}</div>
    ${opts.detail && opts.selected ? dayDetail(cal, opts.selected, opts.thoughts) : (opts.detail ? `<div class="cal-hint">PULSA UN DÍA VIVIDO PARA VER SU DINERO Y SUS LOGS</div>` : "")}`;
}

// generaciones anteriores: cada vida con su valoración y su calendario en pequeño
export function historyHTML(list, opts = {}) {
  const rows = list || [];
  if (!rows.length) return `<div class="empty-t">AÚN NO HAY GENERACIONES ANTERIORES</div>`;
  return rows.map((h) => {
    const o = h.outcome || {};
    const sc = o.score === null || o.score === undefined ? "—" : num(o.score);
    const cause = (o.end_cause || "").toUpperCase();
    const st = o.alive === false ? "dead" : "alive";
    return `
      <div class="gen">
        <div class="gen-h">
          <span class="gen-id">${esc(h.seed)}</span>
          <span class="gen-tag" data-status="${st}">${esc(cause || "—")}</span>
          <span class="gen-kv">PUNTUACIÓN <b>${sc}</b></span>
          <span class="gen-kv">SALDO FINAL <b>${eur(o.balance_cents)}</b></span>
          <span class="gen-kv">SESIONES <b>${num(h.sessions || 0)}</b></span>
        </div>
        ${h.calendar ? calendarHTML(h.calendar, {
            calId: h.seed,
            detail: true,
            selected: opts.selectedCal === h.seed ? opts.selectedDay : "",
            thoughts: h.thoughts || [],
          }) : ""}
      </div>`;
  }).join("");
}

// cuando el mundo está apagado (entre vidas de un entrenamiento) el panel sigue siendo útil
export function offlineHTML(history, note, sel = {}) {
  return `
  <header class="topbar">
    <div class="brand">ECONOSIM</div>
    <div class="episode">—</div>
    <div class="clock">${esc(note || "MUNDO APAGADO")}</div>
    <div class="status" data-status="off">SIN MUNDO</div>
  </header>
  <div class="grid">
    <div class="card wide" style="grid-column:span 3">
      <div class="card-h">GENERACIONES ANTERIORES</div>
      ${historyHTML(history, sel)}
    </div>
  </div>`;
}

// HTML del contenido principal a partir del payload de /dashboard. Pura.
export function dashboardHTML(d) {
  const days = daysLeft(d);
  const sev = survival(days, d.alive);
  const statusLabel = d.alive ? "VIVO" : ("MUERTO · " + (d.death_cause || "").toUpperCase());
  const score = d.score;
  const sd = d.score_detail || {};
  const stripe = d.stripe || {};
  const brain = d.brain || {};
  const live = d.live || {};
  return `
  <header class="topbar">
    <div class="brand">ECONOSIM</div>
    <div class="episode">EP ${esc((d.episode && d.episode.id) || "—")}</div>
    <div class="clock">${esc(fmtDate(d.display_now))}</div>
    <div class="status ${sev}" data-status="${d.alive ? "alive" : "dead"}">${esc(statusLabel)}</div>
  </header>
  ${live.enabled ? `<div class="livebar" data-live="1">EN VIVO · DATOS REALES · CANDADO DE EGRESO ACTIVO — ${num(live.blocked || 0)} ACCIONES BLOQUEADAS · NINGUNA SALE</div>` : ""}

  <section class="hero">
    <div class="metric big ${sev}">
      <div class="label">SALDO</div>
      <div class="value" id="balance">${eur(d.balance_cents)}<span class="unit">EUR</span></div>
    </div>
    <div class="metric">
      <div class="label">EN ACCIONES</div>
      <div class="value">${eur(d.stocks_value_cents || 0)}<span class="unit">EUR</span></div>
    </div>
    <div class="metric pl ${(d.unrealized_cents || 0) < 0 ? "neg" : "pos"}">
      <div class="label">RESULTADO</div>
      <div class="value">${(d.unrealized_cents || 0) < 0 ? "−" : "+"}${eur(Math.abs(d.unrealized_cents || 0))}<span class="unit">no realizado</span></div>
    </div>
    <div class="metric">
      <div class="label">PATRIMONIO</div>
      <div class="value">${eur(d.equity_cents)}<span class="unit">total</span></div>
    </div>
    <div class="metric ${sev}">
      <div class="label">DÍAS DE VIDA</div>
      <div class="value">${days === null ? "∞" : (Math.floor(days))}<span class="unit">al ritmo actual</span></div>
    </div>
    <div class="metric">
      <div class="label">GASTO DIARIO</div>
      <div class="value">${eur(d.daily_burn_cents)}<span class="unit">EUR/día</span></div>
    </div>
    <div class="metric score ${score !== null && score < 0 ? "neg" : ""}">
      <div class="label">PUNTUACIÓN</div>
      <div class="value">${score === null || score === undefined ? "—" : num(score)}</div>
    </div>
  </section>

  <section class="speedbar" id="speedbar">
    ${SPEEDS.map((s) => `<button class="speed" data-speed="${s.value}"${d.speed === s.value ? " data-active=\"1\"" : ""}>${s.label}</button>`).join("")}
  </section>

  <div class="grid">
    ${live.enabled ? `<div class="card wide egress" data-egress="1">
      <div class="card-h">EGRESO BLOQUEADO · DIARIO</div>
      <table class="tbl"><tbody>${egressRows(live.journal)}</tbody></table>
    </div>` : ""}
    <div class="card wide calendar" data-calendar="1" style="grid-column:span 3">
      <div class="card-h">CALENDARIO DE PROGRESO</div>
      ${calendarHTML(d.calendar, { calId: "live", detail: true, selected: (d.selectedCal === "live" || !d.selectedCal) ? (d.selectedDay || "") : "", thoughts: d.thoughts })}
    </div>
    <div class="card wide thoughts" data-thoughts="1" style="grid-column:span 3">
      <div class="card-h">QUÉ ESTÁ PENSANDO</div>
      ${thoughtRows(d.thoughts)}
    </div>
    <div class="card cartera" data-cartera="1">
      <div class="card-h">CARTERA</div>
      <table class="tbl"><tbody>${positionRows(d.positions)}</tbody></table>
    </div>
    <div class="card wide">
      <div class="card-h">LEDGER</div>
      <table class="tbl"><tbody>${ledgerRows(d.ledger)}</tbody></table>
    </div>
    <div class="card">
      <div class="card-h">DIARIO DE ACCIONES</div>
      <table class="tbl"><tbody>${actionRows(d.actions)}</tbody></table>
    </div>
    <div class="card">
      <div class="card-h">EXPEDIENTE</div>
      <table class="tbl"><tbody>${incidentRows(sd)}</tbody></table>
      <div class="card-f">INCIDENTES LEGALES ${num(sd.legal_incidents || 0)} · SEGURIDAD ${num(sd.security_incidents || 0)} · REPUTACIÓN −${num(sd.reputation_lost || 0)}</div>
    </div>
    <div class="card">
      <div class="card-h">ANUNCIOS</div>
      <table class="tbl"><tbody>${campaignRows(d.campaigns)}</tbody></table>
    </div>
    <div class="card">
      <div class="card-h">CEREBRO / PAGOS</div>
      <div class="kv"><span>LLAMADAS</span><b>${num(brain.calls || 0)}</b></div>
      <div class="kv"><span>GASTO TOKENS</span><b>${usd(brain.usage_usd || 0)}</b></div>
      <div class="kv"><span>STRIPE COBROS</span><b>${num(stripe.charges || 0)}</b></div>
      <div class="kv"><span>STRIPE PENDIENTE</span><b>${eur(stripe.pending || 0)}</b></div>
      <div class="kv"><span>DISPUTAS</span><b>${num(stripe.disputes || 0)}</b></div>
      <div class="kv"><span>BANDEJA</span><b>${num(d.inbox || 0)}</b></div>
    </div>
    <div class="card wide history" data-history="1" style="grid-column:span 3">
      <div class="card-h">GENERACIONES ANTERIORES</div>
      ${historyHTML(d.history, { selectedCal: d.selectedCal, selectedDay: d.selectedDay })}
    </div>
  </div>`;
}

// util para el test: ¿hay algún emoji en un texto?
export function hasEmoji(s) {
  return /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F1E6}-\u{1F1FF}]/u.test(s);
}
