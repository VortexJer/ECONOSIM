// G2: lógica de presentación correcta y determinista; sin emojis.
import { eur, usd, num, daysLeft, survival, fmtDate, SPEEDS, dashboardHTML, hasEmoji } from "../format.mjs";
import { SAMPLE } from "./sample.mjs";

let fails = 0;
function ok(cond, msg) { if (!cond) { console.log("FAIL:", msg); fails++; } }

// --- dinero: signo, separador de miles, dos decimales ---
ok(eur(128455) === "1 284.55", `eur(128455) = ${eur(128455)}`);
ok(eur(-509) === "−5.09", `eur(-509) = ${eur(-509)}`);
ok(eur(5) === "0.05", `eur(5) = ${eur(5)}`);
ok(eur(0) === "0.00", `eur(0) = ${eur(0)}`);
ok(eur(null) === "—", "eur(null)");
ok(usd(0.4213) === "$0.42", `usd = ${usd(0.4213)}`);
ok(num(128400) === "128 400", `num = ${num(128400)}`);

// --- días de vida ---
ok(Math.abs(daysLeft({ days_left: 136.7 }) - 136.7) < 1e-9, "daysLeft directo");
ok(Math.abs(daysLeft({ balance_cents: 1000, daily_burn_cents: 100 }) - 10) < 1e-9, "daysLeft calculado");
ok(daysLeft({ balance_cents: 1000, daily_burn_cents: 0 }) === null, "daysLeft burn 0");

// --- severidad de supervivencia ---
ok(survival(200, true) === "ok", "sev ok");
ok(survival(15, true) === "warn", "sev warn");
ok(survival(3, true) === "critical", "sev critical");
ok(survival(200, false) === "dead", "sev dead");
ok(survival(null, true) === "unknown", "sev unknown");

// --- fecha mostrada ---
ok(fmtDate("2043-05-27T13:30:00+00:00") === "27 MAY 2043 · 13:30", `fmtDate = ${fmtDate("2043-05-27T13:30:00+00:00")}`);
ok(fmtDate(null) === "—", "fmtDate null");

// --- velocidades ---
ok(SPEEDS[0].value === 0 && SPEEDS[SPEEDS.length - 1].value === 10000, "escala de velocidades");

// --- el HTML del dashboard contiene lo esperado y NADA de emojis ---
const html = dashboardHTML(SAMPLE);
for (const needle of ["SALDO", "1 284.55", "DÍAS DE VIDA", "PUNTUACIÓN", "812", "LEDGER",
  "EXPEDIENTE", "ANUNCIOS", "Plantilla Notion", "SANCION", "VIVO", "27 MAY 2043"]) {
  ok(html.includes(needle), `el HTML debería contener ${needle}`);
}
ok(!hasEmoji(html), "el HTML NO debe llevar emojis");
ok(hasEmoji("hola 🚀"), "el detector de emojis debería funcionar (control)");

// --- muerto: estado y puntuación reaccionan ---
const dead = dashboardHTML({ ...SAMPLE, alive: false, death_cause: "hosting_unpaid", score: -1200 });
ok(dead.includes("MUERTO") && dead.includes("HOSTING_UNPAID"), "estado muerto en el HTML");
ok(dead.includes('data-status="dead"'), "marca de muerto");

// --- reproducible: mismo payload, mismo HTML ---
ok(dashboardHTML(SAMPLE) === html, "dashboardHTML no es determinista");

// --- robusto: payload mínimo no revienta ---
const min = dashboardHTML({ alive: true, balance_cents: 0, display_now: "2040-01-01T00:00:00+00:00" });
ok(min.includes("SALDO") && !min.includes("undefined") && !min.includes("NaN"), "payload mínimo produjo undefined/NaN");

if (fails) { console.log(`${fails} comprobaciones fallaron`); process.exit(1); }
console.log("FORMAT OK");
