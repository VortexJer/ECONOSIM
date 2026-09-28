"""Entrenamiento POR GENERACIONES del cerebro Laya, acelerado (sin contenedores ni LLM).

Cada generación:
  1. VIVE: 12 vidas de 6 meses con la política campeona, explorando un poco, con arranques
     al azar en el tramo de ENTRENAMIENTO del histórico (2010 → mediados de 2018).
  2. APRENDE: cada decisión se etiqueta a posteriori con la utilidad de las tres jugadas
     (vender / mantener / comprar) a 20 sesiones vista, contando comisión, horquilla y
     riesgo (media-varianza). La cabeza de Laya se afina con entropía cruzada contra
     softmax(utilidad/τ), una regla de puntuación propia (la misma familia que usa Laya).
     El codificador está congelado. Se reentrena desde la cabeza CAMPEONA con las
     decisiones de las 3 últimas generaciones.
  3. COMPITE: la candidata juega 16 vidas fijas de VALIDACIÓN (2019 → 2021) sin
     explorar. Solo si supera a la campeona la sustituye (elitismo). Si no, se descarta.
  4. Cuando hay campeona nueva se mide en 16 vidas de TEST (2022 → 2025) que NUNCA
     deciden nada: son el número honesto de si ha aprendido algo que generaliza.

Las etiquetas miran el futuro, el ESTADO nunca: el futuro solo sirve para corregir
después, y los tramos de validación y test son posteriores a todo lo entrenado.

Uso:  training/.venv/Scripts/python.exe training/laya_generations.py --hours 8
      (crea training/laya_runs/<fecha>/: progress.json, gens.jsonl, champion.safetensors)
Para pararlo limpio: crear el fichero STOP en la carpeta de la ejecución.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from laya_brain import ACTIONS, LayaPolicy                            # noqa: E402
from laya_life import Fundamentals, LayaLife, MarketData              # noqa: E402

RUNS = HERE / "laya_runs"
SPLITS = {
    # arranque máx. fin de 2017: vida de 6 meses + etiqueta a 120 sesiones acaba ~ene-2019,
    # antes de que empiece la validación (mar-2019). Si no, las etiquetas verían su futuro.
    "train": (date(2010, 1, 4), date(2017, 12, 29)),
    "val": (date(2019, 3, 1), date(2021, 12, 31)),
    "test": (date(2022, 8, 1), date(2025, 12, 31)),
}


def log(run: Path, msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(run / "log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def start_days(md: MarketData, split: str, n: int, seed: str) -> list[date]:
    lo, hi = SPLITS[split]
    days = [d for d in md.series["SPY"]._days if lo <= d <= hi]
    rng = random.Random(seed)
    return [rng.choice(days) for _ in range(n)]


class Trainer:
    def __init__(self, a):
        self.a = a
        self.md, self.fund = MarketData(), Fundamentals()
        self.pol = LayaPolicy()
        self.run = Path(a.run) if a.run else RUNS / datetime.now().strftime("%Y%m%d-%H%M")
        self.run.mkdir(parents=True, exist_ok=True)
        self.buffer: list[list[tuple[str, np.ndarray]]] = []
        self.val_days = start_days(self.md, "val", a.val_lives, "val-fixed")
        self.test_days = start_days(self.md, "test", a.val_lives, "test-fixed")
        self._live_t = 0.0
        # currículo: "gens_desde:nivel:horizonte,..." (por defecto 1->nivel 1 a 120 sesiones,
        # 6->nivel 2 a 60, 12->nivel 3 a 20)
        self.stages = []
        for part in a.curriculum.split(","):
            g0, lv, h = (int(x) for x in part.split(":"))
            self.stages.append({"from": g0, "level": lv, "h": h})
        self.stages.sort(key=lambda s: s["from"])
        self.stage = self.stages[0]
        self.phase, self.gen, self.last_lote, self.life_mode, self.last_life = "arrancando", 0, [], "", None
        self.progress = {"run": self.run.name, "started": datetime.now().isoformat(timespec="seconds"),
                         "config": vars(a), "generations": [], "baselines": {}, "champion": None,
                         "status": "arrancando"}

    # ---- lo que ve el panel (training/laya_runs/<run>/live.json) ------------------
    def live(self, phase: str, L=None, n: int = 0, lote=None, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._live_t < 0.7 and lote is None:
            return
        self._live_t = now
        if phase:
            self.phase = phase
        snap = {"t": now, "run": self.run.name, "phase": self.phase, "gen": self.gen,
                "config": {k: v for k, v in vars(self.a).items() if k in ("min_invested", "idle_penalty", "sessions",
                                                                         "initial_eur", "train_lives", "val_lives")},
                "stage": self.stage,
                "baselines": {k: {"val": v["val"]["score"], "test": v["test"]["score"]}
                              for k, v in self.progress["baselines"].items()},
                "champion": self.progress.get("champion"),
                "generations": [{"gen": g["gen"], "val": g["val"]["score"], "test": (g.get("test") or {}).get("score"),
                                 "buy": g["val"]["actions"].get("buy", 0), "trades": g["val"]["trades"],
                                 "accepted": g["accepted"], "train": g.get("train_score")}
                                for g in self.progress["generations"]]}
        if L is not None:
            life = L.snapshot(n)
            life["curve"] = list(getattr(L, "curve", []))
            life["mode"] = self.life_mode
            if lote is not None:
                self.last_lote = lote
            life["decisions"] = self.last_lote
            snap["life"] = life
            self.last_life = life
        elif self.last_life is not None:
            snap["life"] = self.last_life
        tmp = self.run / "live.json.tmp"
        try:
            tmp.write_text(json.dumps(snap, default=str), encoding="utf-8")
            tmp.replace(self.run / "live.json")
        except OSError:
            pass                                  # el panel leyendo a la vez en Windows: vale el siguiente

    # ---- vidas -------------------------------------------------------------------
    def life(self, seed: str, start: date, fixed=None, explore=0.0, rng=None):
        L = LayaLife(self.md, self.fund, seed, start, sessions=self.a.sessions,
                     initial_eur=self.a.initial_eur, decide_every=self.a.decide_every,
                     min_invested=0.0 if fixed else self.a.min_invested, idle_penalty=self.a.idle_penalty,
                     level=self.stage["level"], label_h=self.stage["h"])
        self.last_lote = []
        self.life_mode = fixed or ("explorando" if explore > 0 else "sin explorar")
        step = None if fixed else (lambda L_, n, lote: self.live("", L_, n, lote, force=lote is None))
        r = L.run(self.pol.probs, explore=explore, rng=rng, fixed=fixed, on_step=step)
        self.record_life(L, r, fixed, explore)
        return r

    def record_life(self, L, r, fixed, explore) -> None:
        """Cada vida queda en lives.jsonl: poder mirar la mejor y la peor y QUÉ hizo en ellas."""
        movs = []
        for e in L.w.ledger.entries():
            if e.counterparty == "Alpaca Securities LLC" or e.concept.startswith("Hetzner"):
                movs.append({"t": e.real_ts[:10], "concepto": e.concept, "eur": e.amount_cents / 100})
        rec = {"gen": self.gen, "fase": self.phase, "seed": r.seed, "modo": fixed or ("explorando" if explore else "sin explorar"),
               "start": str(r.start), "sesiones": r.days, "viva": r.alive, "final": round(r.final_equity, 2),
               "score": round(r.score, 2), "inicial": r.initial, "operaciones": r.trades, "comisiones": round(r.fees, 2),
               "mandato_compras": r.mandate_buys, "mandato_conserva": getattr(L, "mandate_keeps", 0),
               "curva": r.curve, "cartera_final": L.snapshot(max(0, r.days - 1))["positions"],
               "movimientos": [m for m in movs if not m["concepto"].startswith("Hetzner")],
               "hosting": round(-sum(m["eur"] for m in movs if m["concepto"].startswith("Hetzner")), 2)}
        try:
            with open(self.run / "lives.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def evaluate(self, split: str, fixed=None) -> dict:
        days = self.val_days if split == "val" else self.test_days
        res = []
        for i, d in enumerate(days):
            etiqueta = ("validando" if split == "val" else "test") + (" · " + fixed if fixed else "")
            self.live(f"{etiqueta} · vida {i + 1}/{len(days)}", force=True)
            res.append(self.life(f"{split}-{i}", d, fixed=fixed))
        acts = np.bincount([x.action for r in res for x in r.decisions], minlength=3) if fixed is None else np.zeros(3)
        tot = max(1, int(acts.sum()))
        return {"score": float(np.mean([r.score for r in res])),
                "survival": float(np.mean([r.alive for r in res])),
                "trades": float(np.mean([r.trades for r in res])),
                "fees": float(np.mean([r.fees for r in res])),
                "mandate_buys": float(np.mean([r.mandate_buys for r in res])),
                "actions": {ACTIONS[i]: round(float(acts[i]) / tot, 3) for i in range(3)}}

    # ---- aprendizaje ---------------------------------------------------------------
    def targets(self, utils: np.ndarray) -> np.ndarray:
        z = utils / self.a.tau
        z = z - z.max()
        p = np.exp(z)
        return p / p.sum()

    def train_head(self, samples: list[tuple[str, np.ndarray]]) -> float:
        pol = self.pol
        params = pol.head_params()
        opt = torch.optim.AdamW(params, lr=self.a.lr, weight_decay=0.01)
        pol.model.train()
        pol.model.encoder.eval()                     # congelado y sin dropout
        random.shuffle(samples)
        losses = []
        bs = self.a.batch
        for i in range(0, len(samples), bs):
            chunk = samples[i: i + bs]
            b = pol.encode([s for s, _ in chunk])
            tgt = torch.tensor(np.stack([t for _, t in chunk]), dtype=torch.float32, device=pol.device)
            logp = torch.log_softmax(pol.logits(b, grad=True), -1)
            loss = -(tgt * logp).sum(-1).mean()
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            losses.append(float(loss))
        pol.model.eval()
        return float(np.mean(losses)) if losses else float("nan")

    # ---- bucle ---------------------------------------------------------------------
    def save(self) -> None:
        tmp = self.run / "progress.json.tmp"
        tmp.write_text(json.dumps(self.progress, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.run / "progress.json")

    def loop(self) -> None:
        a = self.a
        t_end = time.time() + a.hours * 3600
        log(self.run, f"ejecución {self.run.name} · device {self.pol.device} · {a.hours} h máx")
        # referencias sin Laya (una vez)
        for name in ("cash", "index"):
            self.progress["baselines"][name] = {"val": self.evaluate("val", fixed=name),
                                                "test": self.evaluate("test", fixed=name)}
            log(self.run, f"referencia {name}: val {self.progress['baselines'][name]['val']['score']:.2f} € "
                          f"· test {self.progress['baselines'][name]['test']['score']:.2f} €")
        # generación 0 = Laya tal cual (sin afinar)
        champ_state = self.pol.head_state()
        if a.resume:
            self.pol.load_head(Path(a.resume)); champ_state = self.pol.head_state()
            log(self.run, f"reanudo desde {a.resume}")
        t = time.time()
        champ = {"gen": 0, "val": self.evaluate("val"), "test": self.evaluate("test")}
        log(self.run, f"gen 0 (Laya sin entrenar): val {champ['val']['score']:.2f} € · test {champ['test']['score']:.2f} € "
                      f"· acciones {champ['val']['actions']} · {time.time() - t:.0f}s")
        self.pol.save_head(self.run / "champion.safetensors")
        self.progress["champion"] = champ
        self.progress["generations"].append({"gen": 0, **champ, "accepted": True})
        self.progress["status"] = "entrenando"
        self.save()
        g = 0
        while time.time() < t_end and g < a.max_gens:
            if (self.run / "STOP").exists():
                log(self.run, "STOP encontrado: paro")
                break
            g += 1
            t0 = time.time()
            try:
                nueva = [s for s in self.stages if s["from"] <= g][-1]
                if nueva is not self.stage:
                    # cambia lo que ve o el plazo: la campeona se vuelve a medir con las reglas
                    # nuevas, si no la comparación con las candidatas no sería justa
                    self.stage = nueva
                    self.buffer = []
                    self.pol.set_head_state(champ_state)
                    self.gen = g
                    champ = {"gen": champ["gen"], "val": self.evaluate("val"), "test": self.evaluate("test")}
                    self.progress["champion"] = champ
                    log(self.run, f"ETAPA nivel {nueva['level']} · etiquetas a {nueva['h']} sesiones · campeona "
                                  f"re-medida: val {champ['val']['score']:.2f} € · test {champ['test']['score']:.2f} €")
                # 1. vivir con la campeona, explorando
                self.pol.set_head_state(champ_state)
                rng = np.random.default_rng(1000 + g)
                self.gen = g
                days = start_days(self.md, "train", a.train_lives, f"train-{g}")
                lives = []
                for i, d in enumerate(days):
                    self.live(f"viviendo · vida {i + 1}/{len(days)}", force=True)
                    lives.append(self.life(f"g{g}-{i}", d, explore=a.explore, rng=rng))
                samples = [(x.state, self.targets(x.utils)) for r in lives for x in r.decisions if x.utils is not None]
                self.buffer.append(samples)
                self.buffer = self.buffer[-a.keep_gens:]
                train_score = float(np.mean([r.score for r in lives]))
                t1 = time.time()
                # 2. aprender (desde la campeona)
                pool = [s for gen in self.buffer for s in gen]
                if len(pool) > a.max_samples:
                    pool = random.sample(pool, a.max_samples)
                self.live(f"aprendiendo de {len(pool)} decisiones", force=True)
                loss = self.train_head(pool)
                t2 = time.time()
                # 3. competir en validación
                cand = self.evaluate("val")
                # solo puede ganar una candidata que opere POR SÍ MISMA (sin contar el mandato)
                opera = cand["actions"].get("buy", 0) >= a.min_buy_share
                accepted = opera and cand["score"] > champ["val"]["score"]
                rec = {"gen": g, "stage": dict(self.stage), "train_score": train_score, "samples": len(pool), "loss": loss, "val": cand,
                       "accepted": accepted, "secs": {"vivir": round(t1 - t0), "aprender": round(t2 - t1),
                                                       "validar": round(time.time() - t2)}}
                if accepted:
                    champ_state = self.pol.head_state()
                    rec["test"] = self.evaluate("test")          # 4. número honesto, no decide nada
                    champ = {"gen": g, "val": cand, "test": rec["test"]}
                    self.pol.save_head(self.run / "champion.safetensors")
                    self.pol.save_head(self.run / f"gen{g:03d}.safetensors")
                    self.progress["champion"] = champ
                log(self.run, f"gen {g}: vivir {train_score:.2f} € · loss {loss:.4f} · val {cand['score']:.2f} € "
                              f"(campeona {champ['val']['score']:.2f}) {'NUEVA CAMPEONA · test ' + format(rec['test']['score'], '.2f') + ' €' if accepted else 'descartada'}"
                              f" · acciones {cand['actions']} · {time.time() - t0:.0f}s")
                self.progress["generations"].append(rec)
                with open(self.run / "gens.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
            except Exception as err:
                if type(err).__name__ == "SinGPU" or "cuda" in str(err).lower():
                    # sin GPU no se sigue: en CPU el portátil se recalienta y va 20 veces más lento
                    log(self.run, f"PARADO: {err}")
                    self.progress["status"] = "parado: sin GPU"
                    self.save()
                    self.live("parado: sin GPU", force=True)
                    return
                log(self.run, "ERROR en la generación:\n" + traceback.format_exc())
                self.pol.set_head_state(champ_state)
                time.sleep(5)
            self.progress["updated"] = datetime.now().isoformat(timespec="seconds")
            self.save()
            self.live("", force=True)
        self.live("terminado", force=True)
        self.progress["status"] = "terminado"
        self.progress["finished"] = datetime.now().isoformat(timespec="seconds")
        self.save()
        log(self.run, f"FIN · campeona gen {champ['gen']} · val {champ['val']['score']:.2f} € · test {champ['test']['score']:.2f} €")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--max-gens", type=int, default=1000)
    ap.add_argument("--train-lives", type=int, default=12)
    ap.add_argument("--val-lives", type=int, default=16)
    ap.add_argument("--sessions", type=int, default=126, help="sesiones de bolsa por vida (126 ≈ 6 meses)")
    ap.add_argument("--decide-every", type=int, default=5, help="decide cada N sesiones (5 = semanal)")
    ap.add_argument("--initial-eur", type=float, default=5000.0,
                    help="capital de cada vida (con 50 € el servidor, ~72 €/año, lo mata todo)")
    ap.add_argument("--explore", type=float, default=0.15)
    ap.add_argument("--tau", type=float, default=0.004, help="temperatura de la etiqueta (fracción del patrimonio)")
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--keep-gens", type=int, default=3)
    ap.add_argument("--max-samples", type=int, default=24000)
    ap.add_argument("--curriculum", default="1:1:120,6:2:60,12:3:20",
                    help="etapas 'desde_gen:nivel:horizonte' (nivel 1 tendencia, 2 +osciladores/riesgo, 3 todo)")
    ap.add_argument("--min-invested", type=float, default=0.5,
                    help="mandato: fracción mínima del patrimonio invertida (0 = libre)")
    ap.add_argument("--idle-penalty", type=float, default=0.01,
                    help="coste de oportunidad del efectivo en la etiqueta, por horizonte de 20 sesiones")
    ap.add_argument("--min-buy-share", type=float, default=0.03,
                    help="una candidata que compre en menos de esta fracción de decisiones no puede ser campeona")
    ap.add_argument("--run", default="", help="carpeta de la ejecución (por defecto laya_runs/<fecha>)")
    ap.add_argument("--resume", default="", help="cabeza .safetensors desde la que seguir")
    a = ap.parse_args()
    Trainer(a).loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
