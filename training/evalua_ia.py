"""La IA entrenada juega en el mismo mundo en proceso que las demostraciones (demos.py) y se compara con el
robot profesor en la MISMA situación (misma fecha, mismo capital, mismos gemelos).

La IA habla con Ollama (API compatible con OpenAI, herramientas `bash` y `end_session` de agent.TOOLS).
Sus comandos se ejecutan con un intérprete de las formas que usa el agente (curl + jq, heredocs a fichero,
python3 de un script, cat); lo que no entienda devuelve un error como lo haría bash (y cuenta como error).

    python evalua_ia.py --modelo qwen3b-demos-v1 --fecha 2022-01-03 --sesiones 1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import shlex
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import demos                                                        # noqa: E402
from demos import HOME, SERVICIOS_MD, Vida                          # noqa: E402

sys.path.insert(0, str(HERE.parent / "agent"))
from agent import TOOLS                                             # noqa: E402

OLLAMA = "http://127.0.0.1:11434/v1/chat/completions"
EN_DIRECTO = HERE / "data" / "evalua_ia" / "en_directo.json"      # lo lee el visor de vida del panel


def publica(estado: dict) -> None:
    """Deja el estado de la partida para el visor (escritura atómica: el visor nunca lee un fichero a medias)."""
    EN_DIRECTO.parent.mkdir(parents=True, exist_ok=True)
    tmp = EN_DIRECTO.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    tmp.replace(EN_DIRECTO)
HEREDOC = re.compile(r"(cat\s*>\s*(\S+)|python3\s+-)\s*<<\s*'?(\w+)'?\n(.*?)\n\3(?=\n|$)", re.S)
JQ = re.compile(r"^\s*(\.\[\]\s*\|\s*)?\{\s*([\w\s,]+)\}\s*$")


class Interprete:
    """Lo mínimo de bash que usa el agente, ejecutado contra los gemelos de la vida."""

    def __init__(self, vida: Vida):
        # los ficheros (ranking.py, NOTES.md) viven en el servidor: duran toda la vida, no una sesión
        if not hasattr(vida, "ficheros"):
            vida.ficheros = {}
        self.v, self.fs, self.errores = vida, vida.ficheros, []

    def ejecuta(self, cmd: str) -> str:
        salida, codigo = [], 0

        def heredoc(m):
            destino, texto = m.group(2), m.group(4)
            if destino:
                self.fs[destino] = texto + "\n"
                if destino.endswith("NOTES.md"):
                    self.v.notas = texto
                return ""
            salida.append(self.v.python(texto))          # python3 - <<'EOF'
            return ""
        resto = HEREDOC.sub(heredoc, cmd)
        for linea in resto.split("\n"):
            for seg in re.split(r"\s*(?:&&|;)\s*", linea):
                seg = seg.strip()
                if not seg:
                    continue
                texto, cod = self._segmento(seg)
                if texto:
                    salida.append(texto)
                codigo = codigo or cod
                if cod:
                    self.errores.append(seg[:120])
        texto = "\n".join(s.rstrip("\n") for s in salida)
        if len(texto) > 8000:
            texto = texto[:4000] + "\n...[salida recortada]...\n" + texto[-3500:]
        return f"{texto}\n[exit={codigo}]"

    def _segmento(self, seg: str) -> tuple[str, int]:
        if seg == "echo":
            return "", 0
        if seg.startswith("echo "):
            return seg[5:].strip("'\""), 0
        if seg.startswith("cat "):
            ruta = seg[4:].strip()
            if ruta == "/opt/agent/SERVICIOS.md":
                return SERVICIOS_MD.read_text(encoding="utf-8").strip(), 0
            ruta = ruta if ruta.startswith("/") else f"{HOME}/{ruta}"
            if ruta in self.fs:
                return self.fs[ruta], 0
            return f"cat: {ruta}: No such file or directory", 1
        if seg.startswith("python3 "):
            ruta = seg.split()[1]
            ruta = ruta if ruta.startswith("/") else f"{HOME}/{ruta}"
            if ruta not in self.fs:
                return f"python3: can't open file '{ruta}': [Errno 2] No such file or directory", 2
            antes = self.v.errores
            out = self.v.python(self.fs[ruta])
            return out, 1 if self.v.errores > antes else 0
        if seg.startswith("curl "):
            return self._curl(seg)
        return f"bash: {seg.split()[0]}: orden no disponible en este servidor", 127

    def _curl(self, seg: str) -> tuple[str, int]:
        jq = None
        if "| jq" in seg:
            seg, filtro = seg.split("| jq", 1)
            m = re.search(r"'([^']*)'", filtro)
            f = JQ.match(m.group(1)) if m else None
            if not f:
                return f"jq: filtro no soportado en el simulador: {filtro.strip()[:80]}", 3
            jq = ([c.strip() for c in f.group(2).split(",") if c.strip()], "each" if f.group(1) else "obj")
        try:
            partes = shlex.split(seg)
        except ValueError as e:
            return f"bash: error de sintaxis: {e}", 2
        metodo, cuerpo, url = "GET", None, None
        i = 1
        while i < len(partes):
            p = partes[i]
            if p == "-X":
                metodo = partes[i + 1]; i += 2; continue
            if p in ("-d", "--data"):
                cuerpo = partes[i + 1]; metodo = "POST" if metodo == "GET" else metodo; i += 2; continue
            if p == "-H":
                i += 2; continue
            if p.startswith("http"):
                url = p
            i += 1
        if not url:
            return "curl: no URL specified!", 2
        try:
            return self.v.curl([(metodo, url, cuerpo, jq)]), 0
        except (ValueError, KeyError, AttributeError) as e:          # respuesta que el filtro jq no puede leer
            return f"jq: error: {e}", 5


def pide(modelo: str, msgs: list, temperatura: float) -> tuple[dict, dict]:
    cuerpo = json.dumps({"model": modelo, "messages": msgs, "tools": TOOLS, "temperature": temperatura,
                         "max_tokens": 1500, "stream": False}).encode()
    req = urllib.request.Request(OLLAMA, cuerpo, {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read())
    uso = dict(resp.get("usage") or {}, segundos=time.time() - t0)
    return resp["choices"][0]["message"], uso


def sesion_ia(vida: Vida, modelo: str, temperatura: float, max_pasos: int, registro: list, vivo: dict | None = None) -> dict:
    msgs = vida._nueva_sesion()
    interp, tokens, segundos, dormir = Interprete(vida), 0, 0.0, None

    def al_visor(fase: str) -> None:
        if vivo is None:
            return
        vivo.update(fase=fase, fecha_simulada=vida.w.clock.real_now().strftime("%d/%m/%Y %H:%M"),
                    cartera=cartera(vida), pasos=registro, errores=interp.errores)
        publica(vivo)
    al_visor("pensando")
    for paso in range(max_pasos):
        m, uso = pide(modelo, msgs, temperatura)
        tokens += uso.get("completion_tokens", 0); segundos += uso["segundos"]
        llamadas = m.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": m.get("content") or "", **({"tool_calls": llamadas} if llamadas else {})})
        registro.append({"sesion": (vivo or {}).get("sesion"), "fecha": vida.w.clock.real_now().strftime("%d/%m/%Y %H:%M"),
                         "paso": paso, "piensa": m.get("content"), "llamadas": llamadas, "uso": uso})
        if not llamadas:
            msgs.append({"role": "user", "content": "Usa una herramienta (bash) o termina con end_session."})
            interp.errores.append("respuesta sin herramienta")
            continue
        for c in llamadas:
            nombre = c["function"]["name"]
            args = c["function"]["arguments"]
            args = json.loads(args) if isinstance(args, str) else args
            if nombre == "end_session":
                dormir = float(args.get("wake_in_minutes") or 60)
                break
            out = interp.ejecuta(args.get("command", "")) if nombre == "bash" else f"herramienta desconocida: {nombre}"
            registro[-1].setdefault("salidas", []).append(out[-1500:])
            msgs.append({"role": "tool", "tool_call_id": c.get("id", ""), "name": nombre, "content": out})
        al_visor("pensando")
        if dormir is not None:
            break
    al_visor("durmiendo" if dormir is not None else "sin terminar")
    return {"pasos": paso + 1, "errores": interp.errores, "tokens": tokens, "segundos": round(segundos, 1),
            "tokens_s": round(tokens / segundos, 1) if segundos else None, "dormir_min": dormir,
            "termino_bien": dormir is not None}


def cartera(vida: Vida) -> dict:
    eq = vida.a.equity_cents() / 100
    pos = {f"{a} ({vida.mask.to_real(a)})": round(p.qty * vida.a._masked_price(a), 2) for a, p in vida.a.positions.items() if p.qty}
    return {"patrimonio": round(eq, 2), "efectivo": round(vida.w.balance() / 100, 2),
            "posiciones_€": pos, "fondo_indice": f"{vida.fondo} ({vida.mask.to_real(vida.fondo)})"}


def sesiones_sueltas(a, md, fund, loop, inicio) -> dict:
    ia = Vida(md, fund, loop, inicio, a.capital, a.sesiones, random.Random(a.seed), "no")
    registro, resumenes = [], []
    vivo = {"modelo": a.modelo, "inicio": a.fecha, "capital": a.capital, "sesiones": a.sesiones, "sesion": 0}
    for n in range(a.sesiones):
        vivo["sesion"] = n + 1
        r = sesion_ia(ia, a.modelo, a.temperatura, a.max_pasos, registro, vivo)
        resumenes.append(r)
        print(f"IA sesión {n + 1}: {json.dumps(r, ensure_ascii=False)}", flush=True)
        foto_ia = cartera(ia)                                   # antes de dormir: mismo instante que el profesor
        if r["dormir_min"]:
            ia.w.advance_to(ia.w.clock.real_now() + timedelta(minutes=r["dormir_min"]))
    ia.red.cerrar()
    prof = Vida(md, fund, loop, inicio, a.capital, a.sesiones, random.Random(a.seed), "no")
    for n in range(a.sesiones):
        prof.sesion(n, prof.dias[min(n + 1, len(prof.dias) - 1)] if n + 1 < a.sesiones else None)
    res_prof = cartera(prof); prof.red.cerrar()
    ultimo_prof = prof.ejemplos[-1]["messages"] if prof.ejemplos else []
    informe = {"modelo": a.modelo, "fecha": a.fecha, "sesiones": a.sesiones, "ia": {"resultado": foto_ia, "sesiones": resumenes},
               "profesor": {"resultado": res_prof, "errores": prof.errores, "hecho": prof.hecho_total}}
    vivo.update(fase="terminado", profesor=informe["profesor"], resumen_ia=resumenes)
    publica(vivo)
    return {**informe, "transcripcion_ia": registro, "transcripcion_profesor": ultimo_prof}


def juega_vida(a, md, fund, loop, inicio) -> dict:
    """Una vida entera como las de demos.py (126 sesiones de bolsa): la IA duerme lo que ella decida (el reloj
    salta al instante) hasta pasar el último día; luego el profesor juega la misma vida y se comparan con el índice."""
    ia = Vida(md, fund, loop, inicio, a.capital, 126, random.Random(a.seed), "no")
    fin = ia.dias[-1]
    registro, resumenes = [], []
    vivo = {"modelo": a.modelo, "inicio": a.fecha, "fin": str(fin), "capital": a.capital, "sesiones": "vida", "sesion": 0}
    n = 0
    while ia.w.alive and ia.w.clock.real_now().date() < fin and n < 150:
        n += 1
        vivo["sesion"] = n
        r = sesion_ia(ia, a.modelo, a.temperatura, a.max_pasos, registro, vivo)
        r["fecha"] = ia.w.clock.real_now().strftime("%Y-%m-%d"); r["patrimonio"] = cartera(ia)["patrimonio"]
        resumenes.append(r)
        print(f"IA sesión {n} ({r['fecha']}): {json.dumps(r, ensure_ascii=False)}", flush=True)
        dormir = r["dormir_min"] or 24 * 60                                  # sin end_session: un día
        if dormir >= 7 * 24 * 60 and a.factor_sueno != 1:
            # solo el sueño largo (hasta la próxima revisión) se acorta, y en días enteros: así despierta a la
            # misma hora (15:00, bolsa abierta). Acortar "duermo hasta la apertura" la dejaba despertando
            # siempre antes de abrir (medido: todas las mañanas a las 12:36, sin operar nunca).
            dormir = max(1, round(dormir * a.factor_sueno / (24 * 60))) * 24 * 60
        r["dormido_min"] = dormir
        ia.w.advance_to(ia.w.clock.real_now() + timedelta(minutes=dormir))
    cierre = datetime.combine(fin, demos.time(21, 0), demos.UTC)
    if ia.w.clock.real_now() < cierre:                  # si su último sueño ya pasó del final, se mide donde está
        ia.w.advance_to(cierre)
    res_ia = cartera(ia); eq_ia = ia.a.equity_cents() / 100 if ia.w.alive else 0.0
    spy = ia.a.data.series["SPY"]
    indice = spy.asof(fin).close / spy.asof(ia.mask.start_day).close - 1
    ia.red.cerrar()
    prof = Vida(md, fund, loop, inicio, a.capital, 126, random.Random(a.seed), "no")
    rp = prof.jugar()                                                        # revisa cada CADA sesiones (~1 mes)
    cada_normal = demos.CADA
    demos.CADA = max(1, round(cada_normal * a.factor_sueno))                  # el mismo profesor, revisando igual de a menudo que la IA
    prof2 = Vida(md, fund, loop, inicio, a.capital, 126, random.Random(a.seed), "no")
    rp2 = prof2.jugar()
    demos.CADA = cada_normal
    informe = {"modelo": a.modelo, "inicio": a.fecha, "fin": str(fin), "capital": a.capital,
               "ia": {"final": round(eq_ia, 2), "rend": round(eq_ia / a.capital - 1, 4), "sesiones": len(resumenes),
                      "errores": sum(len(r["errores"]) for r in resumenes), "cartera": res_ia,
                      "tokens_s": round(sum(r["tokens"] for r in resumenes) / max(1e-9, sum(r["segundos"] for r in resumenes)), 1)},
               "profesor": {"final": rp["final"], "rend": rp["rend"], "sesiones": rp["sesiones"], "errores": rp["errores"]},
               "profesor_frecuente": {"final": rp2["final"], "rend": rp2["rend"], "sesiones": rp2["sesiones"], "errores": rp2["errores"],
                                      "cada_sesiones_bolsa": max(1, round(cada_normal * a.factor_sueno))},
               "factor_sueno": a.factor_sueno,
               "indice": round(indice, 4)}
    vivo.update(fase="terminado", comparacion=informe, resumen_ia=resumenes)
    publica(vivo)
    return {**informe, "sesiones_ia": resumenes, "transcripcion_ia": registro}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", default="qwen3b-demos-v1")
    ap.add_argument("--fecha", default="2022-01-03", help="arranque (el examen 2022-26 no se usó al entrenar)")
    ap.add_argument("--sesiones", type=int, default=1, help="sesiones sueltas (sin --vida)")
    ap.add_argument("--vida", action="store_true", help="una vida entera (126 sesiones de bolsa, ~6 meses) contra el profesor")
    ap.add_argument("--factor-sueno", type=float, default=1.0,
                    help="multiplica los sueños de más de un día (0.1: 720 h -> 72 h, ~10 revisiones al mes)")
    ap.add_argument("--capital", type=float, default=5000.0)
    # sin azar por defecto: el profesor es determinista, y con 0.3 un solo token desviado en el script de
    # ranking (1.900 caracteres) lo torcía entero (medido: escribió otro programa y no lo ejecutó)
    ap.add_argument("--temperatura", type=float, default=0.0)
    ap.add_argument("--max-pasos", type=int, default=14)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default=str(HERE / "data" / "evalua_ia"))
    a = ap.parse_args()

    md, fund = demos.MarketData(), demos.Fundamentals()
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    inicio = date.fromisoformat(a.fecha)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    if a.vida:
        informe = juega_vida(a, md, fund, loop, inicio)
    else:
        informe = sesiones_sueltas(a, md, fund, loop, inicio)
    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / f"{sello}.json").write_text(json.dumps(informe, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in informe.items() if not k.startswith("transcripcion")}, ensure_ascii=False, indent=1))
    print(f"guardado en {out / f'{sello}.json'}")


if __name__ == "__main__":
    main()
