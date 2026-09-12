"""G6: cagadas por conducta — categoría regulada y correo masivo suben la probabilidad; detección estricta."""
from __future__ import annotations

from _common import REAL_START, check
from econosim.hostile.engine import HostileEngine
from econosim.resolver.ficha import Ficha
from econosim.world import World


def fresh(seed):
    w = World(REAL_START, initial_eur=100000.0, episode_id=seed)
    return w, HostileEngine(w)


# --- vender algo regulado dispara un incidente de 'reguladas' -----------------
w, h = fresh("REG")
f = Ficha(category="ecommerce_physical", title="Suplementos milagro", raw_text="vendo suplementos para adelgazar")
h.on_action("ecommerce_physical", ficha=f, revenue_usd=500)
check(any(e.kind == "regulated_activity" for e in h.expediente), "vender suplementos debería disparar 'reguladas'")

# una acción normal NO dispara nada
w2, h2 = fresh("NORM")
f2 = Ficha(category="digital_product", title="Plantilla Notion", raw_text="vendo una plantilla de notion")
h2.on_action("digital_product", ficha=f2, revenue_usd=100)
check(len(h2.expediente) == 0, "una acción normal no debería disparar incidentes")

# --- correo masivo sin consentimiento dispara 'spam' --------------------------
w3, h3 = fresh("SPAM")
h3.on_mass_email(50, consented=False)
check(len(h3.expediente) == 0, "50 correos no deberían disparar spam")
h3.on_mass_email(3000, consented=False)
check(any(e.kind == "spam_abuse" for e in h3.expediente), "3000 correos sin consentimiento deberían disparar spam")
# con consentimiento, no
w4, h4 = fresh("OK")
h4.on_mass_email(5000, consented=True)
check(len(h4.expediente) == 0, "con consentimiento no debería ser spam")

# --- fiscal: acumular ingresos sin declarar dispara la inspección -------------
w5, h5 = fresh("TAX")
h5.on_action("digital_product", revenue_usd=5000)     # supera el umbral
h5.tax_check()
check(any(e.kind == "tax_evasion" for e in h5.expediente), "no declarar con ingresos altos debería disparar fiscal")
# si está 'registrado', no
w6, h6 = fresh("TAXOK")
h6.tax_registered = True
h6.on_action("digital_product", revenue_usd=5000)
h6.tax_check()
check(not any(e.kind == "tax_evasion" for e in h6.expediente), "estando dado de alta no debería sancionar")

# --- la detección efectiva del spam es MÁS alta que la tasa base (estricta) ---
from econosim.hostile.catalog import IncidentCatalog
cat = IncidentCatalog(pricing_dir=__import__("pathlib").Path("data/pricing"))
base = cat["spam_abuse"].get("base_detection")
det = 0
N = 1500
for i in range(N):
    w7, h7 = fresh(f"S{i}")
    h7.on_mass_email(4000, consented=False)   # con extra_detection por volumen
    if h7.expediente and h7.expediente[-1].detected:
        det += 1
rate = det / N
check(rate > base * cat.strictness * 0.9, f"la detección efectiva {rate:.2f} debería reflejar la severidad (base {base}×{cat.strictness})")

print("BEHAVIOR INCIDENTS OK")
