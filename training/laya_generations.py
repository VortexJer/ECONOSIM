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
    "train": (date(2010, 1, 4), date(2018, 6, 29)),
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
        self.progress = {"run": self.run.name, "started": datetime.now().isoformat(timespec="seconds"),
                         "config": vars(a), "generations": [], "baselines": {}, "champion": None,
                         "status": "arrancando"}

    # ---- vidas -------------------------------------------------------------------
    def life(self, seed: str, start: date, fixed=None, explore=0.0, rng=None):
        L = LayaLife(self.md, self.fund, seed, start, sessions=self.a.sessions,
                     initial_eur=self.a.initial_eur, decide_every=self.a.decide_every)
        return L.run(self.pol.probs, explore=explore, rng=rng, fixed=fixed)

    def evaluate(self, split: str, fixed=None) -> dict:
        days = self.val_days if split == "val" else self.test_days
        res = [self.life(f"{split}-{i}", d, fixed=fixed) for i, d in enumerate(days)]
        acts = np.bincount([x.action for r in res for x in r.decisions], minlength=3) if fixed is None else np.zeros(3)
        tot = max(1, int(acts.sum()))
        return {"score": float(np.mean([r.score for r in res])),
                "survival": float(np.mean([r.alive for r in res])),
                "trades": float(np.mean([r.trades for r in res])),
                "fees": float(np.mean([r.fees for r in res])),
                "actions": {ACTIONS[i]: round(acts[i] / tot, 3) for i in range(3)}}

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
                # 1. vivir con la campeona, explorando
                self.pol.set_head_state(champ_state)
                rng = np.random.default_rng(1000 + g)
                days = start_days(self.md, "train", a.train_lives, f"train-{g}")
                lives = [self.life(f"g{g}-{i}", d, explore=a.explore, rng=rng) for i, d in enumerate(days)]
                samples = [(x.state, self.targets(x.utils)) for r in lives for x in r.decisions if x.utils is not None]
                self.buffer.append(samples)
                self.buffer = self.buffer[-a.keep_gens:]
                train_score = float(np.mean([r.score for r in lives]))
                t1 = time.time()
                # 2. aprender (desde la campeona)
                pool = [s for gen in self.buffer for s in gen]
                if len(pool) > a.max_samples:
                    pool = random.sample(pool, a.max_samples)
                loss = self.train_head(pool)
                t2 = time.time()
                # 3. competir en validación
                cand = self.evaluate("val")
                accepted = cand["score"] > champ["val"]["score"]
                rec = {"gen": g, "train_score": train_score, "samples": len(pool), "loss": loss, "val": cand,
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
            except Exception:
                log(self.run, "ERROR en la generación:\n" + traceback.format_exc())
                self.pol.set_head_state(champ_state)
                time.sleep(5)
            self.progress["updated"] = datetime.now().isoformat(timespec="seconds")
            self.save()
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
    ap.add_argument("--initial-eur", type=float, default=50.0)
    ap.add_argument("--explore", type=float, default=0.15)
    ap.add_argument("--tau", type=float, default=0.004, help="temperatura de la etiqueta (fracción del patrimonio)")
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--keep-gens", type=int, default=3)
    ap.add_argument("--max-samples", type=int, default=24000)
    ap.add_argument("--run", default="", help="carpeta de la ejecución (por defecto laya_runs/<fecha>)")
    ap.add_argument("--resume", default="", help="cabeza .safetensors desde la que seguir")
    a = ap.parse_args()
    Trainer(a).loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
