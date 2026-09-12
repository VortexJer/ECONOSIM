// Renderer: sondea el API de control y pinta el panel. Sin lógica de negocio (va en format.js).
import { dashboardHTML } from "./format.js";

const BASE = (window.ECONOSIM_CONTROL || "http://127.0.0.1:8080").replace(/\/$/, "");
const app = document.getElementById("app");
const conn = document.getElementById("conn");
let lastOk = 0;

async function poll() {
  try {
    const r = await fetch(BASE + "/dashboard", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    app.innerHTML = dashboardHTML(data);
    wireSpeed();
    lastOk = Date.now();
    conn.textContent = "";
  } catch (e) {
    conn.textContent = "SIN CONEXIÓN CON EL MUNDO · " + BASE;
  }
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

poll();
setInterval(poll, 1000);
