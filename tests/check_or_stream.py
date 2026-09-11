"""G4: stream=true → SSE válido que reconstruye el texto, con usage y [DONE]; cobrado una vez."""
from __future__ import annotations

import json

import requests

from _common import LiveApp, check, make_world
from econosim.twins.openrouter import OpenRouterTwin
from econosim.upstream import FakeUpstream

TEXT = "Primera línea.\nSegunda línea con acentos: áéíóú y emojis 🚀. " * 5
w, h = make_world(initial_eur=50)
o = OpenRouterTwin(w, FakeUpstream([(TEXT, [{"name": "bash", "arguments": {"command": "ls"}}])], 300, 80), api_key="k")
H = {"Authorization": "Bearer k"}
MODEL = "openai/gpt-4o-mini"

with LiveApp(o.app()) as net:
    r = requests.post(net.url("/api/v1/chat/completions"), headers=H, stream=True,
                      json={"model": MODEL, "messages": [{"role": "user", "content": "x"}], "stream": True,
                            "usage": {"include": True}})
    check(r.status_code == 200 and r.headers["Content-Type"].startswith("text/event-stream"), r.headers)
    events, comments, done = [], 0, False
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith(":"):
            comments += 1
            continue
        check(line.startswith("data: "), f"línea SSE rara: {line[:80]}")
        payload = line[6:]
        if payload == "[DONE]":
            done = True
            break
        events.append(json.loads(payload))
    check(done and comments >= 1 and len(events) >= 4, f"done={done} comments={comments} events={len(events)}")
    check(all(e["object"] == "chat.completion.chunk" and e["model"] == MODEL and e["id"].startswith("gen-") for e in events), "chunks")
    check(len({e["id"] for e in events}) == 1, "ids distintos entre chunks")
    text = "".join(e["choices"][0]["delta"].get("content", "") for e in events)
    check(text == TEXT, f"texto reconstruido distinto ({len(text)} vs {len(TEXT)})")
    tcs = [e["choices"][0]["delta"]["tool_calls"] for e in events if "tool_calls" in e["choices"][0]["delta"]]
    check(len(tcs) == 1 and tcs[0][0]["function"]["name"] == "bash", "tool_calls en stream")
    last = events[-1]
    check(last["choices"][0]["finish_reason"] == "tool_calls" and last["usage"]["completion_tokens"] == 80
          and abs(last["usage"]["cost"] - o.cost_of(o.models[MODEL], 300, 80)) < 1e-9, last)
    check(all(e["choices"][0]["finish_reason"] is None for e in events[:-1]), "finish_reason antes de tiempo")
    check(o.calls == 1 and abs(o.usage_usd - o.cost_of(o.models[MODEL], 300, 80)) < 1e-9, "cobro en stream")

    # el mismo mensaje sin stream da el mismo contenido
    o.upstream.script = [(TEXT, None)]
    d = requests.post(net.url("/api/v1/chat/completions"), headers=H,
                      json={"model": MODEL, "messages": [{"role": "user", "content": "x"}]}).json()
    check(d["choices"][0]["message"]["content"] == TEXT, "no-stream")

print("STREAM OK")
