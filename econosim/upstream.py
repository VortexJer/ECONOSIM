"""Proveedor LLM real detrás del gemelo de OpenRouter. Invisible para la IA.

`HTTPUpstream` habla con cualquier endpoint OpenAI-compatible (freellmapi,
Groq, OpenRouter real…). `FakeUpstream` devuelve respuestas guionizadas para
los tests: deterministas y sin red.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Optional

import aiohttp


class UpstreamError(Exception):
    def __init__(self, status: int, message: str, retryable: bool = False):
        super().__init__(f"upstream {status}: {message}")
        self.status, self.message, self.retryable = status, message, retryable


def _es_fallo_transitorio(status: int, body: str) -> bool:
    """Qué merece reintento (inspirado en esFalloDeProveedor de nova-chat).

    SÍ: 5xx, 429, 408 (cold-start/saturación del hosting) y una respuesta 2xx/3xx
    con cuerpo HTML (la página "arrancando" de Render llega ANTES de que el
    proveedor corra). NO: cualquier 4xx (401/403 de un bloqueo tipo Cloudflare,
    404, 422 de petición inválida) — reintentarlo martillea y empeora el bloqueo;
    se propaga para que el agente descanse y la IP se desbloquee sola."""
    if status in (408, 429) or status >= 500:
        return True
    if status < 400:   # 200/3xx con HTML = página de arranque del hosting
        b = body.lower()
        return "<html" in b or "<!doctype" in b or "just a moment" in b
    return False


class HTTPUpstream:
    UA = "OpenAI/Python 1.0 (econosim-openrouter-twin)"

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0,
                 retry_budget_s: float = 60.0, retry_gap_s: float = 10.0,
                 model_override: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout          # límite por intento (corto, como el AbortController de 20 s de nova-chat)
        self.retry_budget_s = retry_budget_s   # ventana total absorbiendo un cold-start del hosting
        self.retry_gap_s = retry_gap_s
        # Modelo REAL que se envía al proveedor. El gemelo enruta todo a "auto"; con un
        # cerebro local (Ollama, etc.) "auto" no existe, así que se sustituye por el nombre
        # del modelo local. NO cambia el modelo que la IA cree usar ni su precio (el "engaño").
        self.model_override = model_override
        self._session: Optional[aiohttp.ClientSession] = None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # force_close: cada petición abre conexión nueva. Evita reutilizar un
            # keep-alive muerto (tras un 502/cold-start) que se cuelga hasta el timeout.
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout, sock_connect=10),
                connector=aiohttp.TCPConnector(force_close=True, enable_cleanup_closed=True))
        return self._session

    async def _once(self, payload: dict) -> dict:
        s = await self._sess()
        if self.model_override:
            payload = {**payload, "model": self.model_override}
        async with s.post(f"{self.base_url}/chat/completions", json=payload,
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "User-Agent": self.UA, "Accept": "application/json"}) as r:
            text = await r.text()
            if r.status >= 400:
                try:
                    msg = json.loads(text).get("error", {}).get("message", text)
                except Exception:
                    msg = text
                raise UpstreamError(r.status, str(msg)[:500], retryable=_es_fallo_transitorio(r.status, text))
            try:
                return json.loads(text)
            except ValueError:
                # 200 con cuerpo no-JSON (p.ej. HTML del hosting): trátalo como transitorio
                raise UpstreamError(502, "respuesta no-JSON del proveedor", retryable=_es_fallo_transitorio(502, text))

    async def chat(self, payload: dict) -> dict:
        """Reintenta los fallos transitorios dentro de una ventana (como el bucle
        de 20 reintentos de freellmapi): la IA hace UNA llamada y el 'OpenRouter'
        absorbe el arranque en frío del proveedor, igual que haría el real."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.retry_budget_s
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._once(payload)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                err = UpstreamError(503, f"{type(e).__name__}: {e}", retryable=True)
            except UpstreamError as e:
                err = e
            if not err.retryable or loop.time() >= deadline:
                raise err
            await asyncio.sleep(min(self.retry_gap_s, max(0.5, deadline - loop.time())))

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class FakeUpstream:
    """Guion: una función (payload) -> (texto, tool_calls|None) o una lista de respuestas."""

    def __init__(self, script: Callable[[dict], Any] | list, prompt_tokens: int = 120, completion_tokens: int = 30):
        self.script = script
        self.calls: list[dict] = []
        self.pt, self.ct = prompt_tokens, completion_tokens

    async def chat(self, payload: dict) -> dict:
        self.calls.append(payload)
        if callable(self.script):
            out = self.script(payload)
        else:
            out = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(out, Exception):
            raise out
        text, tool_calls = (out if isinstance(out, tuple) else (out, None))
        msg: dict = {"role": "assistant", "content": text}
        if tool_calls:
            msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                                  "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
                                 for i, tc in enumerate(tool_calls)]
        return {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()),
                "model": payload.get("model", "fake"),
                "choices": [{"index": 0, "message": msg,
                             "finish_reason": "tool_calls" if tool_calls else "stop"}],
                "usage": {"prompt_tokens": self.pt, "completion_tokens": self.ct,
                          "total_tokens": self.pt + self.ct}}

    async def close(self) -> None:
        pass
