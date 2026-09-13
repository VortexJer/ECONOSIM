"""G5: gemelo Qonto — auth org:secret, saldo = ledger, transacciones = asientos."""
from __future__ import annotations

from datetime import datetime, timedelta

import requests

from _common import LiveApp, check, make_world
from econosim.clock import UTC
from econosim.twins.qonto import QontoTwin

w, h = make_world(initial_eur=50)
q = QontoTwin(w, secret_key="s3cr3t")
H = {"Authorization": q.auth_header}

with LiveApp(q.app()) as net:
    U = net.url
    check(requests.get(U("/v2/organization")).status_code == 401, "sin auth")
    check(requests.get(U("/v2/organization"), headers={"Authorization": "vps-labs-1234:wrong"}).status_code == 401, "secreto malo")
    check(requests.get(U("/v2/organization"), headers={"Authorization": "Bearer s3cr3t"}).status_code == 401, "formato de auth")

    org = requests.get(U("/v2/organization"), headers=H).json()["organization"]
    acc = org["bank_accounts"][0]
    check(acc["balance_cents"] == 5000 and acc["balance"] == 50.0 and acc["currency"] == "EUR" and acc["status"] == "active", acc)
    check(len(acc["iban"]) == 24 and acc["iban"].startswith("ES") and acc["bic"], "IBAN/BIC")

    # movimientos: los cargos DIARIOS de Hetzner de un mes y un ingreso
    w.advance_to(datetime(1998, 11, 1, 0, 0, 1, tzinfo=UTC))       # cruza un mes de cobros diarios
    w.receive(1234, "Venta plantilla", "Stripe Payments Europe", ref="po_1")
    debits = [e for e in w.ledger.entries() if e.amount_cents < 0]
    check(len(debits) >= 10, f"preparación: esperaba cobros diarios, hay {len(debits)}")
    check(w.balance() == 5000 - sum(-e.amount_cents for e in debits) + 1234, "preparación")

    acc = requests.get(U("/v2/organization"), headers=H).json()["organization"]["bank_accounts"][0]
    check(acc["balance_cents"] == w.balance(), f"saldo API {acc['balance_cents']} != ledger {w.balance()}")

    txs = requests.get(U("/v2/transactions"), headers=H, params={"slug": q.account_slug, "iban": q.iban}).json()
    entries = w.ledger.entries("bank")
    check(txs["meta"]["total_count"] == len(entries) == 2 + len(debits), txs["meta"])
    sides = [t["side"] for t in txs["transactions"]]
    check(sides[0] == "credit" and sides[-1] == "credit" and all(s == "debit" for s in sides[1:-1]), sides)  # más reciente primero
    newest = txs["transactions"][0]
    check(newest["amount_cents"] == 1234 and newest["label"] == "Stripe Payments Europe" and newest["reference"] == "po_1"
          and newest["status"] == "completed" and newest["currency"] == "EUR", newest)
    debit = txs["transactions"][1]
    last_debit = debits[-1]
    check(debit["amount_cents"] == -last_debit.amount_cents and debit["label"] == "Hetzner Online GmbH"
          and debit["operation_type"] == "card" and debit["settled_at"] == last_debit.display_ts, debit)
    check(all("1998" not in t["settled_at"] and t["settled_at"].startswith("2026-") for t in txs["transactions"]), "fechas")
    check(sum(t["amount_cents"] * (1 if t["side"] == "credit" else -1) for t in txs["transactions"]) == w.balance(), "suma de movimientos")

    only_debits = requests.get(U("/v2/transactions"), headers=H, params={"side": "debit"}).json()["transactions"]
    check(len(only_debits) == len(debits) and all(t["side"] == "debit" for t in only_debits), "filtro side")
    page = requests.get(U("/v2/transactions"), headers=H, params={"per_page": 2, "current_page": 2}).json()
    total = len(entries)
    import math
    check(len(page["transactions"]) == 2 and page["meta"]["prev_page"] == 1 and page["meta"]["next_page"] == 3
          and page["meta"]["total_pages"] == math.ceil(total / 2) and page["meta"]["total_count"] == total, page["meta"])
    one = requests.get(U(f"/v2/transactions/{newest['transaction_id']}"), headers=H).json()["transaction"]
    check(one == newest, "transacción individual")
    check(requests.get(U("/v2/transactions/nope"), headers=H).status_code == 404, "404")

print("QONTO OK")
