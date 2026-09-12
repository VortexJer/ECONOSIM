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

// HTML del contenido principal a partir del payload de /dashboard. Pura.
export function dashboardHTML(d) {
  const days = daysLeft(d);
  const sev = survival(days, d.alive);
  const statusLabel = d.alive ? "VIVO" : ("MUERTO · " + (d.death_cause || "").toUpperCase());
  const score = d.score;
  const sd = d.score_detail || {};
  const stripe = d.stripe || {};
  const brain = d.brain || {};
  return `
  <header class="topbar">
    <div class="brand">ECONOSIM</div>
    <div class="episode">EP ${esc((d.episode && d.episode.id) || "—")}</div>
    <div class="clock">${esc(fmtDate(d.display_now))}</div>
    <div class="status ${sev}" data-status="${d.alive ? "alive" : "dead"}">${esc(statusLabel)}</div>
  </header>

  <section class="hero">
    <div class="metric big ${sev}">
      <div class="label">SALDO</div>
      <div class="value" id="balance">${eur(d.balance_cents)}<span class="unit">EUR</span></div>
    </div>
    <div class="metric">
      <div class="label">PATRIMONIO</div>
      <div class="value">${eur(d.equity_cents)}<span class="unit">EUR</span></div>
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
  </div>`;
}

// util para el test: ¿hay algún emoji en un texto?
export function hasEmoji(s) {
  return /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F1E6}-\u{1F1FF}]/u.test(s);
}
