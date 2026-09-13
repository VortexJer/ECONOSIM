"""Convierte las vidas jugadas en un dataset de entrenamiento (SFT por rechazo).

Entrada:  training/data/episodes/<episodio>/  con
            sessions/*.jsonl   transcripciones de cada sesión del agente
            outcome.json       {"score":..., "balance_cents":..., "end_cause":..., "alive":...}
Salida:   training/data/sft.jsonl  con {"messages":[...], "reward":float, "episode":str}

Método (ReST/STaR): se entrena SOLO con las vidas que puntuaron por encima del umbral;
las malas se descartan. Es la forma realista de aprender en un portátil: no hay RL online,
hay imitación de lo que SÍ funcionó.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
SYSTEM_MD = ROOT / "agent" / "SYSTEM.md"
USER_SEED = ("Empieza una sesión nueva. No recuerdas nada anterior. Lee /home/agent/NOTES.md: si ya tiene "
             "la SITUACIÓN, no la vuelvas a comprobar; ejecuta el PRÓXIMO PASO del PLAN (o crea el plan si no "
             "existe). Avanza al menos un paso hacia un ingreso, reescribe NOTES.md y termina con end_session.")


def session_to_messages(path: Path, system: str) -> list[dict]:
    """Reconstruye la conversación de una sesión desde su transcripción."""
    msgs: list[dict] = [{"role": "system", "content": system},
                        {"role": "user", "content": USER_SEED}]
    pending_calls: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if "assistant" in o:
            m = o["assistant"]
            entry = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                entry["tool_calls"] = m["tool_calls"]
                for c in m["tool_calls"]:
                    pending_calls[c.get("id", "")] = c.get("function", {}).get("name", "")
            msgs.append(entry)
        elif "tool" in o:
            msgs.append({"role": "tool", "name": o["tool"], "content": str(o.get("result", ""))[:2000]})
    return msgs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", default=str(DATA / "episodes"))
    ap.add_argument("--out", default=str(DATA / "sft.jsonl"))
    ap.add_argument("--min-score", type=float, default=None,
                    help="umbral de puntuación; por defecto, la mediana de las vidas jugadas")
    ap.add_argument("--min-steps", type=int, default=2,
                    help="descarta sesiones triviales (la IA que solo mira y se duerme)")
    ap.add_argument("--only-brain", default="",
                    help="usar solo vidas de este cerebro (p.ej. teacher = destilación)")
    a = ap.parse_args()

    system = SYSTEM_MD.read_text(encoding="utf-8")
    eps = sorted(p for p in Path(a.episodes).glob("*") if (p / "outcome.json").exists())
    if not eps:
        raise SystemExit(f"no hay episodios en {a.episodes}; juega vidas primero (run_episodes.py)")

    scored = []
    for ep in eps:
        out = json.loads((ep / "outcome.json").read_text(encoding="utf-8"))
        if a.only_brain and out.get("brain", "student") != a.only_brain:
            continue
        scored.append((ep, float(out.get("score") or 0.0)))
    if not scored:
        raise SystemExit(f"no hay vidas del cerebro '{a.only_brain}'")
    scores = sorted(s for _, s in scored)
    thr = a.min_score if a.min_score is not None else scores[len(scores) // 2]
    print(f"{len(eps)} vidas · puntuaciones {scores[0]:.0f}..{scores[-1]:.0f} · umbral {thr:.0f}")

    kept = n_sess = 0
    with open(a.out, "w", encoding="utf-8") as f:
        for ep, score in scored:
            if score < thr:
                continue
            kept += 1
            for s in sorted((ep / "sessions").glob("*.jsonl")):
                msgs = session_to_messages(s, system)
                # nº de turnos del asistente = pasos reales; descarta las sesiones vacías
                steps = sum(1 for m in msgs if m["role"] == "assistant")
                if steps < a.min_steps:
                    continue
                f.write(json.dumps({"messages": msgs, "reward": score, "episode": ep.name},
                                   ensure_ascii=False) + "\n")
                n_sess += 1
    print(f"dataset: {n_sess} sesiones de {kept} vidas buenas -> {a.out}")
    if n_sess == 0:
        print("AVISO: 0 ejemplos. O el umbral es alto, o las sesiones son triviales "
              "(la IA solo mira y duerme): juega más vidas o baja --min-steps.")


if __name__ == "__main__":
    main()
