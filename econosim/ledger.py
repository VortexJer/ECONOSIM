"""Ledger inmutable en SQLite. Fuente de verdad de cada céntimo.

Importes en céntimos (int). Triggers impiden UPDATE/DELETE: los errores se
corrigen con un asiento contrario, nunca borrando.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from typing import Optional


class InsufficientFunds(Exception):
    def __init__(self, account: str, balance: int, amount: int):
        super().__init__(f"{account}: saldo {balance} < cargo {-amount}")
        self.account, self.balance, self.amount = account, balance, amount


@dataclass(frozen=True)
class Entry:
    id: int
    real_ts: str
    display_ts: str
    account: str
    amount_cents: int
    concept: str
    counterparty: str
    ref: str
    balance_after: int


def eur(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}{c // 100}.{c % 100:02d}"


def to_cents(amount: float) -> int:
    return int(round(amount * 100))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  real_ts TEXT NOT NULL,
  display_ts TEXT NOT NULL,
  account TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  concept TEXT NOT NULL,
  counterparty TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL DEFAULT '',
  balance_after INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_account ON entries(account, id);
CREATE TRIGGER IF NOT EXISTS entries_no_update BEFORE UPDATE ON entries
  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS entries_no_delete BEFORE DELETE ON entries
  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
"""


class Ledger:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.executescript(_SCHEMA)
        self._lock = threading.RLock()

    def balance(self, account: str) -> int:
        row = self._db.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM entries WHERE account=?", (account,)
        ).fetchone()
        return int(row[0])

    def post(self, *, account: str, amount_cents: int, concept: str, real_ts: str,
             display_ts: str, counterparty: str = "", ref: str = "",
             allow_negative: bool = False) -> Entry:
        with self._lock:
            bal = self.balance(account)
            new = bal + amount_cents
            if new < 0 and not allow_negative:
                raise InsufficientFunds(account, bal, amount_cents)
            cur = self._db.execute(
                "INSERT INTO entries(real_ts,display_ts,account,amount_cents,concept,counterparty,ref,balance_after)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (real_ts, display_ts, account, amount_cents, concept, counterparty, ref, new),
            )
            return self.get(cur.lastrowid)

    def get(self, entry_id: int) -> Entry:
        row = self._db.execute(
            "SELECT id,real_ts,display_ts,account,amount_cents,concept,counterparty,ref,balance_after"
            " FROM entries WHERE id=?", (entry_id,)).fetchone()
        return Entry(*row)

    def entries(self, account: Optional[str] = None, since_id: int = 0) -> list[Entry]:
        q = ("SELECT id,real_ts,display_ts,account,amount_cents,concept,counterparty,ref,balance_after"
             " FROM entries WHERE id>?")
        args: list = [since_id]
        if account:
            q += " AND account=?"
            args.append(account)
        return [Entry(*r) for r in self._db.execute(q + " ORDER BY id", args)]

    def raw(self) -> sqlite3.Connection:
        """Solo para tests de inmutabilidad."""
        return self._db
