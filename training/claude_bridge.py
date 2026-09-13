"""Puente Claude -> sim: Claude como cerebro (profesor) con fallback al 7B local.

El gemelo de OpenRouter habla OpenAI-style (/v1/chat/completions con `tools`). Este
puente, en el host, traduce cada petición a la Messages API de Anthropic con el SDK
oficial y devuelve la respuesta en formato OpenAI. Si Claude falla (límite de tasa, cuota,
5xx, red), reenvía la MISMA petición al 7B local en Ollama: el agente no nota el cambio.

Requiere ANTHROPIC_API_KEY (en ../.env o en el entorno). Modelo por defecto:
claude-haiku-4-5 (barato). Arranque:
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
DEFAULT_MODEL = "claude-haiku-4-5"
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


class Bridge:
    def __init__(self, model: str, fallback_model: str):
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
        a = self.anthropic
        brain = "claude"
        try:
            data = await self.ask_claude(payload)
            self.stats["claude"] += 1
        except (a.RateLimitError, a.APIConnectionError, a.APITimeoutError) as e:
            brain = f"fallback ({type(e).__name__})"
        except a.APIStatusError as e:
            # cuota/facturación/servidor -> al 7B; una petición mal formada (400) también, para no parar al agente
            brain = f"fallback (HTTP {e.status_code})"
        except Exception as e:  # noqa
            brain = f"fallback ({type(e).__name__}: {str(e)[:80]})"
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
    a = ap.parse_args()
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("falta ANTHROPIC_API_KEY (ponla en .env): sin ella Claude no puede ser el cerebro")
    bridge = Bridge(a.model, a.fallback)
    app = web.Application(client_max_size=16 * 1024 ** 2)
    app.router.add_post("/v1/chat/completions", bridge.handle)
    app.router.add_get("/health", health)
    print(f"[bridge] Claude {a.model} como cerebro · fallback {a.fallback} · puerto {a.port}", flush=True)
    web.run_app(app, host="0.0.0.0", port=a.port, print=None)


if __name__ == "__main__":
    main()
