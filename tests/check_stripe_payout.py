"""G2: comisión retenida, neto pendiente, y payout al banco SOLO tras el plazo de liquidación."""
from __future__ import annotations

from datetime import datetime, timedelta

from _common import REAL_START, check
from econosim.ledger import to_cents
from econosim.twins.stripe import StripeTwin, COUNTERPARTY
from econosim.world import World

w = World(REAL_START, initial_eur=100.0)
st = StripeTwin(w, secret_key="sk")
fx = w.load_pricing("fx")["usd_per_eur"]
cfg = w.load_pricing("stripe")
hold = cfg["payout"]["new_account_hold_days"]     # cuentas nuevas: 7 días

bank0 = w.balance()
ch = st.record_sale(100.0, "venta")
net = ch["net"]
check(st.pending_cents == net and st.available_cents == 0, "el neto debe estar pendiente")

# antes del plazo de retención: nada llega al banco
w.advance(timedelta(days=hold - 1))
check(w.balance() == bank0, "llegó dinero antes del plazo de liquidación")
check(st.available_cents == 0 and st.pending_cents == net, "se liquidó antes de tiempo")

# tras el plazo: el neto pasa a disponible y el ciclo de payout lo manda al banco
w.advance(timedelta(days=3))
payouts = st.payouts
check(len(payouts) >= 1 and payouts[-1]["status"] == "paid", "no hubo payout tras el plazo")
expected_eur = to_cents((net / 100.0) / fx)
bank_in = [e for e in w.ledger.entries() if e.counterparty == COUNTERPARTY and e.amount_cents > 0]
check(len(bank_in) >= 1 and bank_in[-1].amount_cents == expected_eur, f"payout al banco {bank_in[-1].amount_cents if bank_in else None} != {expected_eur}")
check(w.balance() == bank0 + expected_eur, "saldo del banco tras payout")
check(st.available_cents == 0, "debería haber quedado a 0 tras el payout")

# --- una venta ya con cuenta 'antigua' liquida más rápido (rolling T+2) -------
for i in range(cfg["payout"]["new_account_charges_threshold"]):
    st.record_sale(10.0, "relleno")     # supera el umbral de cuenta nueva
w.advance(timedelta(days=20))            # deja que todo liquide
before = w.balance()
ch2 = st.record_sale(50.0, "venta rápida")
w.advance(timedelta(days=cfg["payout"]["rolling_days"]))    # T+2
w.advance(timedelta(days=1))             # ciclo de payout
check(w.balance() > before, "la venta con cuenta antigua debería liquidar en T+2")

# --- la comisión total retenida coincide con la tabla -------------------------
# sobre una venta de $100: 2.9% + $0.30 = $3.20
check(ch["fee"] == to_cents(100 * 0.029) + to_cents(0.30), "comisión != 2.9% + $0.30")

print("PAYOUT OK")
