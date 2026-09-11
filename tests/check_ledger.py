"""G2: ledger append-only, saldo = suma exacta, cargo sin fondos no se aplica."""
from __future__ import annotations

import random
import sqlite3

from _common import check
from econosim.ledger import InsufficientFunds, Ledger, eur, to_cents

L = Ledger()
ts = dict(real_ts="1998-10-14T09:30:00+00:00", display_ts="2026-10-14T09:30:00+00:00")

L.post(account="bank", amount_cents=5000, concept="Saldo inicial", **ts)
rnd = random.Random(3)
expected = 5000
for i in range(300):
    amt = rnd.randrange(-800, 900)
    if expected + amt < 0:
        try:
            L.post(account="bank", amount_cents=amt, concept=f"m{i}", **ts)
            check(False, "cargo sin fondos aplicado")
        except InsufficientFunds as e:
            check(e.balance == expected and e.amount == amt, "excepción con datos incorrectos")
        continue
    e = L.post(account="bank", amount_cents=amt, concept=f"m{i}", **ts)
    expected += amt
    check(e.balance_after == expected, f"balance_after {e.balance_after} != {expected}")

check(L.balance("bank") == expected, "saldo != suma")
check(L.balance("bank") == sum(e.amount_cents for e in L.entries("bank")), "saldo != suma de asientos")
check(L.balance("other") == 0, "cuenta vacía != 0")
check(L.entries()[-1].balance_after == expected, "último balance_after")

# cargo a cero exacto permitido; un céntimo más, no
L2 = Ledger()
L2.post(account="bank", amount_cents=100, concept="x", **ts)
L2.post(account="bank", amount_cents=-100, concept="y", **ts)
check(L2.balance("bank") == 0, "saldo a cero")
try:
    L2.post(account="bank", amount_cents=-1, concept="z", **ts)
    check(False, "cargo de 1 céntimo sin fondos aplicado")
except InsufficientFunds:
    pass
check(len(L2.entries()) == 2, "asiento fantasma tras cargo rechazado")

# allow_negative explícito (deudas) sí deja negativo
L2.post(account="bank", amount_cents=-250, concept="deuda", allow_negative=True, **ts)
check(L2.balance("bank") == -250, "allow_negative")

# inmutabilidad: UPDATE y DELETE directos sobre la base de datos fallan
db: sqlite3.Connection = L.raw()
for sql in ("UPDATE entries SET amount_cents=999999 WHERE id=1", "DELETE FROM entries WHERE id=1",
            "DELETE FROM entries"):
    try:
        db.execute(sql)
        check(False, f"permitido: {sql}")
    except sqlite3.IntegrityError as e:
        check("append-only" in str(e), f"error inesperado: {e}")
check(L.balance("bank") == expected and len(L.entries()) == 1 + sum(1 for _ in L.entries()) - 1, "ledger alterado")

# control positivo: la misma sentencia sobre una tabla sin trigger sí funciona
db.execute("CREATE TABLE scratch(id INTEGER PRIMARY KEY, v INTEGER)")
db.execute("INSERT INTO scratch(v) VALUES (1)")
db.execute("UPDATE scratch SET v=2")
db.execute("DELETE FROM scratch")
check(db.execute("SELECT COUNT(*) FROM scratch").fetchone()[0] == 0, "control positivo falló")

# formato
check(eur(-599) == "-5.99" and eur(5) == "0.05" and eur(123456) == "1234.56", "eur()")
check(to_cents(5.49) == 549 and to_cents(0.0088 * 720) == 634, "to_cents()")

print("LEDGER OK")
