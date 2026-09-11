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
    def __init__(self, status: int, message: str):
        super().__init__(f"upstream {status}: {message}")
        self.status, self.message = status, message


class HTTPUpstream:
    def __init__(self, base_url: str, api_key: str, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        return self._session

    async def chat(self, payload: dict) -> dict:
        s = await self._sess()
        try:
            async with s.post(f"{self.base_url}/chat/completions", json=payload,
                              headers={"Authorization": f"Bearer {self.api_key}"}) as r:
                text = await r.text()
                if r.status >= 400:
                    try:
                        msg = json.loads(text).get("error", {}).get("message", text)
                    except Exception:
                        msg = text
                    raise UpstreamError(r.status, str(msg)[:500])
                return json.loads(text)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise UpstreamError(503, f"{type(e).__name__}: {e}") from e

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
