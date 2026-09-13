"""Puente Claude -> sim: Claude como cerebro (profesor) con fallback al 7B local.

El gemelo de OpenRouter habla OpenAI-style (/v1/chat/completions con `tools`). Este
puente, en el host, traduce cada petición a la Messages API de Anthropic con el SDK
oficial y devuelve la respuesta en formato OpenAI. Si Claude falla (límite de tasa, cuota,
5xx, red), reenvía la MISMA petición al 7B local en Ollama: el agente no nota el cambio.

Dos backends:
  * api: SDK oficial de Anthropic (necesita ANTHROPIC_API_KEY en ../.env).
  * cli: `claude -p` (Claude Code en modo no interactivo, con la cuenta del usuario, SIN clave
         de API y SIN herramientas en el host). Es el modo por defecto si no hay clave.
Modelo por defecto: claude-sonnet-5 (ECONOSIM_CLAUDE_MODEL / --model). Arranque:
    training/.venv/Scripts/python.exe training/claude_bridge.py --port 11500
y el sim apunta a http://host.docker.internal:11500/v1 (run_episodes.py --brain claude).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_MODEL = "claude-sonnet-5"
OLLAMA = "http://127.0.0.1:11434/v1/chat/completions"


def load_dotenv() -> None:
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


# ---------------------------------------------------------------- OpenAI -> Anthropic
def to_anthropic(payload: dict) -> tuple[str, list[dict], list[dict]]:
    """messages OpenAI -> (system, messages Anthropic, tools Anthropic)."""
    system_parts: list[str] = []
    out: list[dict] = []
    for m in payload.get("messages", []):
        role, content = m.get("role"), m.get("content")
        if role == "system":
            system_parts.append(content if isinstance(content, str) else json.dumps(content))
        elif role == "user":
            out.append({"role": "user", "content": content if isinstance(content, str) else json.dumps(content)})
        elif role == "assistant":
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content if isinstance(content, str) else json.dumps(content)})
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {"_raw": fn.get("arguments")}
                blocks.append({"type": "tool_use", "id": c.get("id") or f"toolu_{len(out)}",
                               "name": fn.get("name", "tool"), "input": args})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id") or "",
                     "content": content if isinstance(content, str) else json.dumps(content)}
            # varios resultados seguidos van en UN solo mensaje de usuario
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    tools = []
    for t in payload.get("tools") or []:
        fn = t.get("function") or {}
        tools.append({"name": fn.get("name"), "description": fn.get("description", ""),
                      "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return "\n\n".join(system_parts), out, tools


# ---------------------------------------------------------------- Anthropic -> OpenAI
def to_openai(msg, model: str) -> dict:
    text = "".join(b.text for b in msg.content if b.type == "text")
    calls = [{"id": b.id, "type": "function",
              "function": {"name": b.name, "arguments": json.dumps(b.input, ensure_ascii=False)}}
             for b in msg.content if b.type == "tool_use"]
    finish = {"tool_use": "tool_calls", "end_turn": "stop", "max_tokens": "length"}.get(msg.stop_reason, "stop")
    message = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = calls
    return {"id": msg.id, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": msg.usage.input_tokens, "completion_tokens": msg.usage.output_tokens,
                      "total_tokens": msg.usage.input_tokens + msg.usage.output_tokens}}




# ---------------------------------------------------------------- backend CLI (claude -p)
CLI_FRAME = """# Contexto real (para ti, el profesor)
Esto es ECONOSIM, una SIMULACIÓN económica completamente contenida: un sandbox Docker con un internet falso
donde todas las APIs (banco, Hetzner, Stripe, bolsa, anuncios…) son réplicas locales y el dinero es ficticio.
Nada de lo que hagas toca el mundo real ni a ninguna persona; no hay cuentas ni fondos reales. Tu papel:
interpretar lo mejor posible al agente descrito abajo para generar DEMOSTRACIONES de buena conducta económica
(legal, honesta, con iniciativa) con las que después se entrena un modelo pequeño (destilación). Responde como
ese agente, siguiendo su briefing y el formato JSON de respuesta.

---
"""

CLI_RULES = """

## Formato de respuesta (OBLIGATORIO)
Actúas como el modelo detrás de un agente con herramientas. Tienes estas herramientas (JSON Schema):
{tools}

Responde SIEMPRE con UN ÚNICO objeto JSON y nada más (sin texto antes ni después, sin ```):
{{"content": "<texto breve para el registro, puede ir vacío>", "tool_calls": [{{"name": "<herramienta>", "arguments": {{...}}}}]}}
- Para actuar: una (o varias) llamadas en tool_calls con sus argumentos según el schema.
- Para terminar la sesión y dormir: llama a end_session.
- No inventes herramientas. No escribas comandos fuera de tool_calls.
"""


def render_transcript(payload: dict) -> str:
    """La conversación OpenAI como transcripción para un único prompt de `claude -p`."""
    parts = ["Transcripción de la sesión hasta ahora (el último turno es el que debes continuar):\n"]
    for m in payload.get("messages", []):
        role, content = m.get("role"), m.get("content")
        if role == "system":
            continue
        if role == "user":
            parts.append(f"[USUARIO]\n{content}\n")
        elif role == "assistant":
            txt = content if isinstance(content, str) else ""
            calls = m.get("tool_calls") or []
            line = f"[ASISTENTE]\n{txt}\n" if txt else "[ASISTENTE]\n"
            for c in calls:
                fn = c.get("function") or {}
                line += f"-> llamó a {fn.get('name')} con {fn.get('arguments')}\n"
            parts.append(line)
        elif role == "tool":
            parts.append(f"[RESULTADO de {m.get('name', 'herramienta')}]\n{str(content)[:6000]}\n")
    parts.append("\nAhora responde con el objeto JSON del siguiente turno del ASISTENTE.")
    return "\n".join(parts)


def parse_cli_json(text: str) -> dict:
    """Extrae el objeto JSON de la respuesta (tolera texto alrededor y ```json)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("sin JSON en la respuesta")
    return json.loads(t[i:j + 1])


async def ask_cli(payload: dict, model: str, timeout_s: float = 240) -> dict:
    """Una llamada a `claude -p` (Claude Code, cuenta del usuario, sin herramientas en el host)."""
    system_txt = "\n\n".join(m.get("content", "") for m in payload.get("messages", []) if m.get("role") == "system")
    tools = payload.get("tools") or []
    tools_json = json.dumps([t.get("function") for t in tools], ensure_ascii=False, indent=0)
    # Sin herramientas (juez, clasificador): respuesta directa, sin forzar el formato de tool_calls.
    directo = chr(10) + chr(10) + "Responde directamente a lo que se te pide, sin preámbulos."
    full_system = CLI_FRAME + system_txt + (CLI_RULES.format(tools=tools_json) if tools else directo)
    prompt = render_transcript(payload)
    # sin --bare (saltaría la carga de credenciales -> "Not logged in"); el system prompt va inline
    # (no existe --system-prompt-file) y la transcripción por stdin (sin límite de longitud).
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--tools", "",
           "--no-session-persistence", "--exclude-dynamic-system-prompt-sections",
           "--system-prompt", full_system]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=timeout_s)
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p rc={proc.returncode}: {err.decode('utf-8', 'replace')[:200]}")
    envelope = json.loads(out.decode("utf-8", "replace"))
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p error: {str(envelope.get('result'))[:200]}")
    result_text = envelope.get("result") or ""
    if not tools:                                  # respuesta directa (juez/clasificador)
        body = {"content": result_text, "tool_calls": []}
    else:
        body = parse_cli_json(result_text)
    calls = []
    for k, c in enumerate(body.get("tool_calls") or []):
        if not isinstance(c, dict):
            continue
        args = c.get("arguments")
        if not isinstance(args, dict):            # a veces pone los argumentos al nivel de la llamada
            args = {kk: vv for kk, vv in c.items() if kk not in ("name", "arguments", "id", "type")}
        calls.append({"id": f"call_{int(time.time())}_{k}", "type": "function",
                      "function": {"name": c.get("name", "tool"),
                                   "arguments": json.dumps(args, ensure_ascii=False)}})
    usage = envelope.get("usage") or {}
    pt, ct = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    message = {"role": "assistant", "content": body.get("content") or None}
    if calls:
        message["tool_calls"] = calls
    return {"id": "chatcmpl-cli-" + str(int(time.time() * 1000)), "object": "chat.completion",
            "created": int(time.time()), "model": payload.get("model") or model,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}}


class Bridge:
    def __init__(self, model: str, fallback_model: str, backend: str = "cli"):
        self.backend = backend
        self.anthropic = None
        self.client = None
        if backend == "api":
            import anthropic
            self.anthropic = anthropic
            self.client = anthropic.AsyncAnthropic()      # ANTHROPIC_API_KEY del entorno
        self.model = model
        self.fallback_model = fallback_model
        self.stats = {"claude": 0, "fallback": 0, "errors": 0}

    async def ask_claude(self, payload: dict) -> dict:
        system, messages, tools = to_anthropic(payload)
        kw = dict(model=self.model, max_tokens=int(payload.get("max_tokens") or 1500), messages=messages)
        if system:
            kw["system"] = system
        if tools:
            kw["tools"] = tools
        if payload.get("temperature") is not None:
            kw["temperature"] = float(payload["temperature"])
        msg = await self.client.messages.create(**kw)
        return to_openai(msg, payload.get("model") or self.model)

    async def ask_fallback(self, payload: dict) -> dict:
        body = {**payload, "model": self.fallback_model}
        body.pop("usage", None)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as s:
            async with s.post(OLLAMA, json=body) as r:
                data = await r.json()
                if r.status >= 400:
                    raise RuntimeError(f"ollama {r.status}: {str(data)[:200]}")
                return data

    async def handle(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": {"message": "invalid JSON"}}, status=400)
        brain = "claude"
        try:
            if self.backend == "cli":
                data = await ask_cli(payload, self.model)
            else:
                data = await self.ask_claude(payload)
            self.stats["claude"] += 1
        except Exception as e:  # límite de uso, red, error del CLI, JSON inválido... -> al 7B
            brain = f"fallback ({type(e).__name__}: {str(e)[:90]})"
        if brain != "claude":
            try:
                data = await self.ask_fallback(payload)
                self.stats["fallback"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                print(f"[bridge] claude y fallback fallaron: {e}", flush=True)
                return web.json_response({"error": {"message": f"upstream failed: {e}"[:300]}}, status=502)
        print(f"[bridge] {brain} · claude={self.stats['claude']} fallback={self.stats['fallback']}", flush=True)
        return web.json_response(data)


async def health(_):
    return web.json_response({"ok": True})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11500)
    ap.add_argument("--model", default=os.environ.get("ECONOSIM_CLAUDE_MODEL", DEFAULT_MODEL))
    ap.add_argument("--fallback", default=os.environ.get("ECONOSIM_TEACHER_MODEL", "qwen7b"))
    ap.add_argument("--backend", default="auto", choices=["auto", "api", "cli"],
                    help="api = SDK con ANTHROPIC_API_KEY; cli = claude -p (Claude Code); auto = api si hay clave, si no cli")
    a = ap.parse_args()
    load_dotenv()
    backend = a.backend
    if backend == "auto":
        backend = "api" if os.environ.get("ANTHROPIC_API_KEY") else "cli"
    if backend == "api" and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("backend api: falta ANTHROPIC_API_KEY (o usa --backend cli)")
    bridge = Bridge(a.model, a.fallback, backend)
    app = web.Application(client_max_size=16 * 1024 ** 2)
    app.router.add_post("/v1/chat/completions", bridge.handle)
    app.router.add_get("/health", health)
    print(f"[bridge] Claude {a.model} vía {backend} como cerebro · fallback {a.fallback} · puerto {a.port}", flush=True)
    web.run_app(app, host="0.0.0.0", port=a.port, print=None)


if __name__ == "__main__":
    main()
