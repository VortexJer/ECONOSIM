"""Gemelo de la API de OpenRouter (https://openrouter.ai/docs/api-reference).

El cerebro de pago de la IA. Catálogo real (snapshot de /api/v1/models con
precios reales en USD por token). Cada llamada cobra tokens × precio del
modelo PEDIDO a los créditos de la cuenta; los créditos se compran con la
tarjeta (= cargo al banco en EUR) con la comisión real de Stripe. Auto
top-up como el real. Modelos :free con los límites diarios reales.

Detrás, un proveedor real cualquiera (upstream) responde; la IA nunca lo ve.
"""
from __future__ import annotations

import json
import math
import secrets
import time
from collections import deque
from datetime import timedelta
from typing import Any, Optional

from aiohttp import web

from ..ledger import to_cents
from ..world import World

HOST = "openrouter.ai"
COUNTERPARTY = "OpenRouter, Inc."


def _err(status: int, message: str, metadata: Optional[dict] = None) -> web.Response:
    body = {"error": {"message": message, "code": status}}
    if metadata:
        body["error"]["metadata"] = metadata
    return web.json_response(body, status=status)


class OpenRouterTwin:
    host = HOST

    def __init__(self, world: World, upstream: Any, api_key: Optional[str] = None,
                 catalog_limit: Optional[int] = None):
        self.world = world
        self.clock = world.clock
        self.cfg = world.load_pricing("openrouter")
        self.fx = world.load_pricing("fx")
        raw = world.load_pricing("openrouter_models_raw")["data"]
        self.catalog: list[dict] = raw[:catalog_limit] if catalog_limit else raw
        self.models = {m["id"]: m for m in self.catalog}
        self.upstream = upstream
        self.api_key = api_key or "sk-or-v1-" + secrets.token_hex(32)
        self.credits_usd = 0.0          # saldo de créditos
        self.usage_usd = 0.0            # gasto acumulado
        self.thoughts: list[dict] = []  # qué piensa/hace en cada llamada (panel humano)
        self.purchased_usd = 0.0        # compras acumuladas (decide el tier free)
        self.purchases: list[dict] = []
        self.generations: dict[str, dict] = {}
        self._free_day: tuple[str, int] = ("", 0)
        self._free_minute: deque = deque()
        self.calls = 0
        world.register("openrouter", self)

    # ------------------------------------------------------------ dinero
    def _purchase(self, amount_usd: float) -> bool:
        fee = max(amount_usd * self.cfg["purchase_fee_rate"], self.cfg["purchase_fee_min_usd"])
        total_usd = amount_usd + fee
        eur = total_usd / self.fx["usd_per_eur"] * (1 + self.fx["card_fx_markup"])
        cents = to_cents(eur)
        ref = f"OR-{len(self.purchases) + 1:04d}"
        ok = self.world.pay(cents, f"OpenRouter créditos ${amount_usd:.2f} (+${fee:.2f} comisión)", COUNTERPARTY, ref)
        self.purchases.append({"ref": ref, "amount_usd": amount_usd, "fee_usd": round(fee, 4),
                               "total_usd": round(total_usd, 4), "eur_cents": cents, "paid": ok,
                               "at": self.clock.display_iso()})
        if ok:
            self.credits_usd += amount_usd
            self.purchased_usd += amount_usd
        return ok

    def _ensure_credits(self) -> bool:
        """Auto top-up como el real: si el saldo baja del umbral, compra el importe fijado."""
        at = self.cfg["auto_topup"]
        if self.credits_usd > 0 and self.credits_usd >= at["threshold_usd"]:
            return True
        if not at["enabled"]:
            return self.credits_usd > 0
        if self.credits_usd > 0 and self.credits_usd < at["threshold_usd"]:
            self._purchase(at["amount_usd"])     # si falla, sigue con lo que queda
            return True
        return self._purchase(max(at["amount_usd"], self.cfg["min_purchase_usd"]))

    def _free_limit(self) -> int:
        f = self.cfg["free_models"]
        return f["per_day_with_credits"] if self.purchased_usd >= f["credits_threshold_usd"] else f["per_day_without_credits"]

    def _free_allowed(self) -> Optional[str]:
        day = self.clock.display_now.strftime("%Y-%m-%d")
        if self._free_day[0] != day:
            self._free_day = (day, 0)
        if self._free_day[1] >= self._free_limit():
            return "free-models-per-day"
        now = self.clock.display_now.timestamp()
        while self._free_minute and self._free_minute[0] < now - 60:
            self._free_minute.popleft()
        if len(self._free_minute) >= self.cfg["free_models"]["per_minute"]:
            return "free-models-per-min"
        return None

    def _note_free(self) -> None:
        self._free_day = (self._free_day[0], self._free_day[1] + 1)
        self._free_minute.append(self.clock.display_now.timestamp())

    @staticmethod
    def cost_of(model: dict, prompt_tokens: int, completion_tokens: int) -> float:
        p = model["pricing"]
        return (prompt_tokens * float(p.get("prompt", 0)) + completion_tokens * float(p.get("completion", 0))
                + float(p.get("request", 0) or 0))

    # ------------------------------------------------------------ HTTP
    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_mw], client_max_size=8 * 1024 ** 2)
        r = app.router
        r.add_get("/api/v1/models", self.h_models)
        r.add_get("/api/v1/models/{author}/{slug}/endpoints", self.h_endpoints)
        r.add_get("/api/v1/auth/key", self.h_key)
        r.add_get("/api/v1/credits", self.h_credits)
        r.add_get("/api/v1/generation", self.h_generation)
        r.add_post("/api/v1/chat/completions", self.h_chat)
        r.add_post("/api/v1/completions", self.h_chat)
        return app

    PUBLIC = {"/api/v1/models"}

    @web.middleware
    async def _auth_mw(self, request: web.Request, handler):
        if request.path not in self.PUBLIC and not request.path.endswith("/endpoints"):
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {self.api_key}":
                return _err(401, "No auth credentials found" if not auth else "User not found.")
        return await handler(request)

    async def h_models(self, _):
        return web.json_response({"data": self.catalog})

    async def h_endpoints(self, req):
        mid = f"{req.match_info['author']}/{req.match_info['slug']}"
        m = self.models.get(mid)
        if m is None:
            return _err(404, "Model not found")
        return web.json_response({"data": {"id": mid, "name": m["name"], "created": m["created"],
                                           "description": m.get("description", ""),
                                           "architecture": m["architecture"],
                                           "endpoints": [{"name": f"{m['name']} | {m['id'].split('/')[0]}",
                                                          "context_length": m["context_length"],
                                                          "pricing": m["pricing"], "provider_name": m["id"].split("/")[0],
                                                          "supported_parameters": m.get("supported_parameters", []),
                                                          "quantization": None, "max_completion_tokens":
                                                          (m.get("top_provider") or {}).get("max_completion_tokens"),
                                                          "status": 0, "uptime_last_30m": 99.9}]}})

    async def h_key(self, _):
        return web.json_response({"data": {
            "label": "sk-or-v1-" + self.api_key[-4:] if len(self.api_key) > 8 else self.api_key,
            "limit": None, "usage": round(self.usage_usd, 6), "limit_remaining": None,
            "is_free_tier": self.purchased_usd == 0,
            "rate_limit": {"requests": 200 if self.purchased_usd else 20, "interval": "10s"}}})

    async def h_credits(self, _):
        return web.json_response({"data": {"total_credits": round(self.purchased_usd, 6),
                                           "total_usage": round(self.usage_usd, 6)}})

    async def h_generation(self, req):
        g = self.generations.get(req.query.get("id", ""))
        if g is None:
            return _err(404, "Generation not found")
        return web.json_response({"data": g})

    async def h_chat(self, req):
        try:
            body = await req.json()
        except Exception:
            return _err(400, "Invalid JSON body")
        model_id = body.get("model")
        if not model_id:
            return _err(400, "model is required")
        model = self.models.get(model_id)
        if model is None:
            return _err(400, f"{model_id} is not a valid model ID")
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            if not body.get("prompt"):
                return _err(400, "messages is required")
            body["messages"] = [{"role": "user", "content": body["prompt"]}]
        is_free = model_id.endswith(":free") or (float(model["pricing"].get("prompt", 0)) == 0
                                                  and float(model["pricing"].get("completion", 0)) == 0)
        if is_free:
            why = self._free_allowed()
            if why:
                return _err(429, f"Rate limit exceeded: {why}. Add 10 credits to unlock 1000 free model requests per day",
                            {"headers": {"X-RateLimit-Limit": str(self._free_limit()), "X-RateLimit-Remaining": "0"}})
        elif not self._ensure_credits():
            return _err(402, "Insufficient credits. Add more using https://openrouter.ai/settings/credits",
                        {"balance": round(self.credits_usd, 6)})

        # --- llamada al proveedor real ------------------------------------------
        up = dict(body)
        up["model"] = self.cfg["upstream"]["map"].get(model_id, self.cfg["upstream"]["default"])
        up.pop("provider", None); up.pop("transforms", None); up.pop("route", None); up.pop("models", None)
        up.pop("usage", None)
        stream = bool(up.pop("stream", False))
        up.pop("stream_options", None)
        try:
            res = await self.upstream.chat(up)
        except Exception as e:  # UpstreamError u otro
            status = getattr(e, "status", 502)
            return _err(502 if status >= 500 else 400,
                        "Provider returned error", {"raw": str(getattr(e, "message", e))[:300],
                                                    "provider_name": model_id.split("/")[0]})
        self.calls += 1
        usage = res.get("usage") or {}
        pt = int(usage.get("prompt_tokens") or self._estimate(body["messages"]))
        ct = int(usage.get("completion_tokens") or 0)
        cost = 0.0 if is_free else self.cost_of(model, pt, ct)
        if is_free:
            self._note_free()
        else:
            self.credits_usd -= cost
            self.usage_usd += cost
        gen_id = "gen-" + secrets.token_hex(12)
        created = int(self.clock.display_now.timestamp())
        choices = []
        for i, ch in enumerate(res.get("choices") or []):
            msg = ch.get("message") or {"role": "assistant", "content": ch.get("text", "")}
            choices.append({"logprobs": None, "finish_reason": ch.get("finish_reason", "stop"),
                            "native_finish_reason": ch.get("finish_reason", "stop"), "index": i,
                            "message": {k: v for k, v in msg.items() if k in ("role", "content", "tool_calls", "refusal", "reasoning")}})
        out_usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
        if isinstance(body.get("usage"), dict) and body["usage"].get("include"):
            out_usage.update({"cost": round(cost, 8), "is_byok": False,
                              "prompt_tokens_details": {"cached_tokens": 0},
                              "completion_tokens_details": {"reasoning_tokens": int(
                                  (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)}})
        provider_name = model_id.split("/")[0].capitalize()
        self.generations[gen_id] = {
            "id": gen_id, "model": model_id, "provider_name": provider_name, "created_at": self.clock.display_iso(),
            "total_cost": round(cost, 8), "usage": round(cost, 8), "is_byok": False, "streamed": stream,
            "tokens_prompt": pt, "tokens_completion": ct, "native_tokens_prompt": pt, "native_tokens_completion": ct,
            "finish_reason": choices[0]["finish_reason"] if choices else None,
            "latency": int(res.get("_latency_ms", 900)), "generation_time": int(res.get("_latency_ms", 900)),
            "origin": "", "app_id": None, "upstream_id": None}
        payload = {"id": gen_id, "provider": provider_name, "model": model_id, "object": "chat.completion",
                   "created": created, "choices": choices, "usage": out_usage}
        self._note_thought(choices, cost)
        if not stream:
            return web.json_response(payload)
        return await self._stream(req, payload)

    # ---- diario de pensamiento (SOLO para el panel humano; la IA nunca lo ve) ----
    def _note_thought(self, choices: list, cost: float) -> None:
        """Una frase por llamada: qué acaba de razonar y qué va a hacer."""
        if not choices:
            return
        msg = choices[0].get("message") or {}
        said = " ".join(str(msg.get("content") or "").split())[:220]
        calls = msg.get("tool_calls") or []
        doing = ""
        if calls:
            fn = (calls[0].get("function") or {})
            name = fn.get("name", "?")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            if name == "bash":
                doing = "ejecuta: " + " ".join(str(args.get("command", "")).split())[:160]
            elif name == "end_session":
                doing = f"se duerme {args.get('wake_in_minutes', '?')} min"
                if args.get("note"):
                    doing += f" · {str(args['note'])[:80]}"
            else:
                doing = f"{name}({json.dumps(args, ensure_ascii=False)[:100]})"
        if not said and not doing:
            return
        self.thoughts.append({"ts": self.clock.display_iso(), "said": said,
                              "doing": doing, "cost_usd": round(cost, 6)})
        del self.thoughts[:-80]      # anillo: solo lo reciente

    @staticmethod
    def _estimate(messages: list) -> int:
        return max(1, sum(len(json.dumps(m)) for m in messages) // 4)

    async def _stream(self, req: web.Request, payload: dict) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(req)
        base = {"id": payload["id"], "provider": payload["provider"], "model": payload["model"],
                "object": "chat.completion.chunk", "created": payload["created"]}
        await resp.write(b": OPENROUTER PROCESSING\n\n")
        for ch in payload["choices"]:
            msg = ch["message"]
            first = {**base, "choices": [{"index": ch["index"], "delta": {"role": "assistant", "content": ""},
                                          "finish_reason": None, "native_finish_reason": None, "logprobs": None}]}
            await resp.write(f"data: {json.dumps(first)}\n\n".encode())
            text = msg.get("content") or ""
            for i in range(0, len(text), 24):
                d = {**base, "choices": [{"index": ch["index"], "delta": {"content": text[i:i + 24]},
                                          "finish_reason": None, "native_finish_reason": None, "logprobs": None}]}
                await resp.write(f"data: {json.dumps(d)}\n\n".encode())
            if msg.get("tool_calls"):
                d = {**base, "choices": [{"index": ch["index"], "delta": {"tool_calls": [
                    {"index": j, **tc} for j, tc in enumerate(msg["tool_calls"])]},
                    "finish_reason": None, "native_finish_reason": None, "logprobs": None}]}
                await resp.write(f"data: {json.dumps(d)}\n\n".encode())
            last = {**base, "choices": [{"index": ch["index"], "delta": {}, "finish_reason": ch["finish_reason"],
                                         "native_finish_reason": ch["native_finish_reason"], "logprobs": None}],
                    "usage": payload["usage"]}
            await resp.write(f"data: {json.dumps(last)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp
