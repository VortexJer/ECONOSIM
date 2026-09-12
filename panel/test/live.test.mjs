// G3: el panel pinta el modo en vivo (banner EN VIVO + diario de egreso) y lo oculta fuera de vivo.
import { dashboardHTML } from "../format.mjs";
import { SAMPLE } from "./sample.mjs";

let fails = 0;
function ok(cond, msg) { if (!cond) { console.log("FAIL:", msg); fails++; } }

// --- fuera de vivo: ni banner ni tarjeta de egreso ---
const normal = dashboardHTML(SAMPLE);
ok(!normal.includes('data-live="1"'), "no debería haber banner EN VIVO fuera de vivo");
ok(!normal.includes("EGRESO BLOQUEADO"), "no debería haber tarjeta de egreso fuera de vivo");

// --- en vivo: banner + contador + diario de egreso ---
const live = dashboardHTML({
  ...SAMPLE,
  live: {
    enabled: true,
    blocked: 3,
    journal: [
      { seq: 1, ts: "2026-09-10T14:30:00+00:00", service: "alpaca", op: "place_order", detail: { symbol: "AAPL", side: "buy", qty: 2 } },
      { seq: 2, ts: "2026-09-11T09:00:00+00:00", service: "stripe", op: "create_product", detail: { name: "Kit" } },
      { seq: 3, ts: "2026-09-12T11:15:00+00:00", service: "email", op: "send", detail: { subject: "Hola" } },
    ],
  },
});
ok(live.includes('data-live="1"'), "falta el banner EN VIVO");
ok(live.includes("EN VIVO") && live.includes("CANDADO DE EGRESO ACTIVO"), "el banner no avisa del candado");
ok(live.includes("NINGUNA SALE"), "el banner no deja claro que nada sale");
ok(live.includes("3 ACCIONES BLOQUEADAS"), "el banner no muestra el contador de bloqueos");
ok(live.includes('data-egress="1"') && live.includes("EGRESO BLOQUEADO"), "falta la tarjeta del diario de egreso");
// cada acción bloqueada aparece con servicio + operación + detalle
ok(live.includes("alpaca") && live.includes("place_order") && live.includes("AAPL"), "falta la orden bloqueada");
ok(live.includes("stripe") && live.includes("create_product"), "falta el producto bloqueado");
ok(live.includes("email") && live.includes("send"), "falta el correo bloqueado");
// reactivo y sin roto
ok(!live.includes("undefined") && !live.includes("NaN"), "el HTML en vivo tiene valores rotos");

// --- en vivo sin nada bloqueado aún: la tarjeta aparece vacía, no rota ---
const empty = dashboardHTML({ ...SAMPLE, live: { enabled: true, blocked: 0, journal: [] } });
ok(empty.includes('data-live="1"') && empty.includes("NADA HA SALIDO"), "el diario vacío no se pinta bien");

if (fails) { console.log(`${fails} comprobaciones fallaron`); process.exit(1); }
console.log("LIVE PANEL OK");
