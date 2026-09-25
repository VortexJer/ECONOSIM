// Vista CEREBRO LAYA: lo que está haciendo el entrenamiento por generaciones, en vivo.
// Lee training/laya_runs/<run>/live.json (vía IPC). Pura: datos -> HTML, sin efectos.

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
const e2 = (x) => (x == null || !isFinite(x) ? "—" : Number(x).toFixed(2));
const pc = (x, d = 0) => (x == null || !isFinite(x) ? "—" : (x * 100).toFixed(d) + "%");
const signed = (x) => (x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "") + Number(x).toFixed(2));
const ACT = { buy: "COMPRA", hold: "MANTIENE", sell: "VENDE" };

// curva de patrimonio de la vida en curso, con la línea de salida
function curva(curve, initial, W = 1000, H = 220) {
  if (!curve || curve.length < 2) return `<div class="empty-t">ESPERANDO LA PRIMERA SEMANA DE ESTA VIDA</div>`;
  const pad = { l: 8, r: 58, t: 12, b: 20 };
  const lo = Math.min(...curve, initial) * 0.97, hi = Math.max(...curve, initial) * 1.03;
  const x = (i) => pad.l + (i / (curve.length - 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / (hi - lo || 1)) * (H - pad.t - pad.b);
  const pts = curve.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up = curve[curve.length - 1] >= initial;
  const area = `${pad.l},${H - pad.b} ${pts} ${x(curve.length - 1).toFixed(1)},${H - pad.b}`;
  const ticks = [lo, (lo + hi) / 2, hi].map((v) =>
    `<line class="g-grid" x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}"/>` +
    `<text class="g-eje" x="${W - pad.r + 6}" y="${y(v) + 3}">${v.toFixed(0)}</text>`).join("");
  return `<svg class="g-svg" style="height:${H}px" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${ticks}
    <line x1="${pad.l}" x2="${W - pad.r}" y1="${y(initial)}" y2="${y(initial)}" stroke="#e0a340" stroke-dasharray="4 4" stroke-width="1"/>
    <text class="g-eje" x="${W - pad.r + 6}" y="${y(initial) + 3}" fill="#e0a340">SALIDA</text>
    <polygon class="g-area ${up ? "up" : "dn"}" points="${area}"/>
    <polyline class="g-linea ${up ? "up" : "dn"}" points="${pts}"/>
  </svg>`;
}

// marcador de generaciones: validación por generación frente a las referencias
function marcador(gens, base, W = 1000, H = 150) {
  if (!gens || !gens.length) return `<div class="empty-t">SIN GENERACIONES TODAVÍA</div>`;
  const vals = gens.map((g) => g.val);
  const refs = [base.cash && base.cash.val, base.index && base.index.val].filter((v) => v != null);
  const lo = Math.min(...vals, ...refs) * 0.95, hi = Math.max(...vals, ...refs) * 1.05;
  const pad = { l: 8, r: 70, t: 10, b: 16 };
  const n = Math.max(gens.length - 1, 1);
  const x = (i) => pad.l + (i / n) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / (hi - lo || 1)) * (H - pad.t - pad.b);
  const ref = (v, name, color) => v == null ? "" :
    `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="${color}" stroke-dasharray="3 5" stroke-width="1"/>` +
    `<text class="g-eje" x="${W - pad.r + 6}" y="${y(v) + 3}" fill="${color}">${name} ${v.toFixed(2)}</text>`;
  // línea escalonada de la campeona
  let best = -Infinity;
  const champ = gens.map((g, i) => { if (g.accepted) best = g.val; return `${x(i).toFixed(1)},${y(best).toFixed(1)}`; }).join(" ");
  const dots = gens.map((g, i) =>
    `<circle cx="${x(i).toFixed(1)}" cy="${y(g.val).toFixed(1)}" r="${g.accepted ? 4 : 2.5}" fill="${g.accepted ? "#e0a340" : "#6b7480"}"><title>gen ${g.gen}: ${g.val.toFixed(2)} €</title></circle>`).join("");
  return `<svg class="g-svg" style="height:${H}px" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${ref(base.cash && base.cash.val, "EFECTIVO", "#6b7480")}
    ${ref(base.index && base.index.val, "ÍNDICE", "#58a6ff")}
    <polyline fill="none" stroke="#e0a340" stroke-width="1.4" points="${champ}"/>
    ${dots}
  </svg>`;
}

function probBar(p) {
  const [s, h, b] = p || [0, 0, 0];
  return `<div class="lp-bar" title="vende ${pc(s)} · mantiene ${pc(h)} · compra ${pc(b)}">
    <i class="lp-s" style="width:${(s * 100).toFixed(1)}%"></i><i class="lp-h" style="width:${(h * 100).toFixed(1)}%"></i><i class="lp-b" style="width:${(b * 100).toFixed(1)}%"></i></div>`;
}

function decisiones(lote) {
  if (!lote || !lote.length) return `<div class="empty-t">ESPERANDO A QUE LAYA DECIDA</div>`;
  const orden = { buy: 0, sell: 1, hold: 2 };
  const vistas = new Set();
  const filas = [...lote].sort((a, b) => (a.mandate - b.mandate) || (orden[a.act] - orden[b.act]) || (b.p[2] - a.p[2]))
    .filter((d) => { const k = d.alias + (d.mandate ? "m" : ""); if (vistas.has(k)) return false; vistas.add(k); return true; });
  return `<table class="tbl lp-tbl">${filas.map((d) => `<tr>
      <td>${esc(d.alias)}</td>
      <td class="lp-act lp-${d.act}">${d.mandate ? "COMPRA · MANDATO" : ACT[d.act] || d.act}</td>
      <td class="lp-cell">${probBar(d.p)}</td>
      <td class="amt dim">${pc(d.p[2])}</td></tr>`).join("")}</table>`;
}

function cartera(pos) {
  if (!pos || !pos.length) return `<table class="tbl"><tr><td class="empty">SIN POSICIONES</td></tr></table>`;
  return `<table class="tbl">${pos.map((p) => `<tr><td>${esc(p.alias)}</td>
    <td class="amt">${e2(p.value)} €</td>
    <td class="amt ${p.pl >= 0 ? "pos" : "neg"}">${(p.pl >= 0 ? "+" : "") + (p.pl * 100).toFixed(1)}%</td>
    <td class="dim amt">${esc(p.since || "")}</td></tr>`).join("")}</table>`;
}

function tablaGen(gens) {
  const ult = [...(gens || [])].reverse().slice(0, 14);
  return `<table class="tbl"><tr class="dim"><td>GEN</td><td class="amt">VALID.</td><td class="amt">TEST</td><td class="amt">COMPRA</td><td class="amt">OPS/VIDA</td><td></td></tr>
    ${ult.map((g) => `<tr><td>${g.gen}</td><td class="amt">${e2(g.val)}</td><td class="amt dim">${g.test == null ? "" : e2(g.test)}</td>
      <td class="amt">${pc(g.buy, 1)}</td><td class="amt">${g.trades == null ? "—" : g.trades.toFixed(1)}</td>
      <td class="tag ${g.accepted ? "lp-champ" : "dim"}">${g.gen === 0 ? "BASE" : g.accepted ? "CAMPEONA" : "DESCARTADA"}</td></tr>`).join("")}</table>`;
}

export function layaHTML(L, opts = {}) {
  const life = L.life || {};
  const ini = life.initial || (L.config && L.config.initial_eur) || 50;
  const eq = life.equity, cash = life.cash;
  const res = eq != null ? eq - ini : null;
  const ch = L.champion || {};
  const base = L.baselines || {};
  const edad = opts.age != null ? Math.round(opts.age) : null;
  const vivo = edad != null && edad < 90;
  const cfg = L.config || {};
  return `
  <header class="topbar">
    <div class="brand">ECONOSIM</div>
    <div class="episode">CEREBRO LAYA · ${esc(L.run || "")}</div>
    <div class="clock">${esc((L.phase || "").toUpperCase())}${life.date ? " · " + esc(life.date) : ""}</div>
    <div class="status" data-status="${vivo ? "alive" : "off"}">${vivo ? "GEN " + (L.gen ?? 0) : "PARADO" + (edad != null ? " · HACE " + Math.round(edad / 60) + " MIN" : "")}</div>
  </header>
  <div class="hero">
    <div class="metric big"><div class="label">PATRIMONIO · ESTA VIDA</div><div class="value">${e2(eq)}<span class="unit">€</span></div></div>
    <div class="metric"><div class="label">EFECTIVO</div><div class="value">${e2(cash)}</div></div>
    <div class="metric"><div class="label">INVERTIDO</div><div class="value">${eq != null ? e2(eq - cash) : "—"}</div></div>
    <div class="metric pl ${res == null ? "" : res >= 0 ? "pos" : "neg"}"><div class="label">RESULTADO</div><div class="value">${signed(res)}</div></div>
    <div class="metric"><div class="label">OPERACIONES</div><div class="value">${life.trades ?? "—"}<span class="unit">${life.mandate_buys ? life.mandate_buys + " MAND." : ""}</span></div></div>
    <div class="metric"><div class="label">COMISIONES</div><div class="value">${e2(life.fees)}</div></div>
    <div class="metric"><div class="label">SESIÓN</div><div class="value">${life.day ?? "—"}<span class="unit">/ ${life.total ?? "—"}</span></div></div>
  </div>
  <div class="grid">
    <div class="card wide">
      <div class="card-h">PATRIMONIO DE ESTA VIDA · ${esc((life.mode || "").toUpperCase())} · ARRANQUE ${esc(life.start || "—")}${life.alive === false ? " · MUERTA" : ""}</div>
      ${curva(life.curve, ini)}
      <div class="card-f">EL SERVIDOR COBRA CADA DÍA · LÍNEA DISCONTINUA = CON LO QUE EMPEZÓ · MANDATO ${pc(cfg.min_invested)} INVERTIDO · PENALIZACIÓN DEL EFECTIVO ${pc(cfg.idle_penalty, 1)} / 20 SESIONES</div>
    </div>
    <div class="card">
      <div class="card-h">CARTERA</div>
      ${cartera(life.positions)}
    </div>
    <div class="card wide">
      <div class="card-h">DECISIONES DE LAYA · ÚLTIMA SEMANA · <span class="lp-key"><i class="lp-s"></i>VENDE <i class="lp-h"></i>MANTIENE <i class="lp-b"></i>COMPRA</span></div>
      <div class="lp-scroll">${decisiones(life.decisions)}</div>
    </div>
    <div class="card">
      <div class="card-h">CAMPEONA</div>
      <div class="kv"><span>GENERACIÓN</span><b>${ch.gen ?? "—"}</b></div>
      <div class="kv"><span>VALIDACIÓN (ELIGE)</span><b>${e2(ch.val && ch.val.score)} €</b></div>
      <div class="kv"><span>TEST (NO ELIGE)</span><b>${e2(ch.test && ch.test.score)} €</b></div>
      <div class="kv"><span>EFECTIVO · VALID / TEST</span><b>${e2(base.cash && base.cash.val)} / ${e2(base.cash && base.cash.test)}</b></div>
      <div class="kv"><span>ÍNDICE · VALID / TEST</span><b>${e2(base.index && base.index.val)} / ${e2(base.index && base.index.test)}</b></div>
      <div class="kv"><span>COMPRA POR SÍ MISMA</span><b>${pc(ch.val && ch.val.actions && ch.val.actions.buy, 1)}</b></div>
      <div class="card-f">€ MEDIOS AL ACABAR CADA VIDA DE ${cfg.sessions || 126} SESIONES, EMPEZANDO CON ${ini} €</div>
    </div>
    <div class="card wide">
      <div class="card-h">GENERACIONES · VALIDACIÓN POR GENERACIÓN · ESCALÓN = CAMPEONA</div>
      ${marcador(L.generations, base)}
    </div>
    <div class="card">
      <div class="card-h">HISTORIAL</div>
      ${tablaGen(L.generations)}
    </div>
  </div>`;
}

export const LAYA_CSS = `
  .lp-bar{display:flex;height:9px;width:100%;min-width:160px;background:#14171c}
  .lp-bar i,.lp-key i{display:block;height:100%}
  .lp-key i{display:inline-block;width:9px;height:9px;margin:0 5px 0 10px;vertical-align:-1px}
  .lp-s{background:#f85149} .lp-h{background:#3a404a} .lp-b{background:#3fb950}
  .lp-tbl td{padding:4px 8px 4px 0}
  .lp-cell{width:55%}
  .lp-act{font-size:10px;letter-spacing:.14em}
  .lp-act.lp-buy{color:#3fb950} .lp-act.lp-sell{color:#f85149} .lp-act.lp-hold{color:#6b7480}
  .lp-scroll{max-height:320px;overflow:auto}
  .tag.lp-champ{color:#e0a340}
`;
