"""¿La IA se estropeó al pasarla a Ollama? Misma primera sesión, tres cerebros, generación sin azar:
  1) base en 4 bits + adaptador (exactamente como se entrenó)
  2) Ollama (fusionado + GGUF q8)
y se compara con lo que escribió el profesor. Uso: python diagnostico_ia.py"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "agent"))
import demos                                                    # noqa: E402
from agent import TOOLS                                         # noqa: E402
from train_qlora import render                                  # noqa: E402

md, fund = demos.MarketData(), demos.Fundamentals()
loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
vida = demos.Vida(md, fund, loop, date(2022, 1, 3), 5000.0, 1, random.Random(11), "no")
msgs = vida._nueva_sesion()
profe = demos.Vida(md, fund, loop, date(2022, 1, 3), 5000.0, 1, random.Random(11), "no")
profe.sesion(0, None)
esperado = profe.ejemplos[0]["messages"][2]
print("PROFESOR:", esperado["content"][:200], "|", esperado["tool_calls"][0]["function"]["arguments"][:160])

# 2) Ollama, sin azar
cuerpo = json.dumps({"model": "qwen3b-demos-v1", "messages": msgs, "tools": TOOLS, "temperature": 0, "max_tokens": 300,
                     "stream": False}).encode()
r = json.loads(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/v1/chat/completions", cuerpo,
                                                             {"Content-Type": "application/json"}), timeout=600).read())
m = r["choices"][0]["message"]
print("\nOLLAMA:", (m.get("content") or "")[:200], "|", json.dumps(m.get("tool_calls"), ensure_ascii=False)[:300])

# 1) como se entrenó: base 4 bits + adaptador, mismo render que el entrenamiento
import torch                                                    # noqa: E402
from peft import PeftModel                                      # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig  # noqa: E402
tok = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-3B-Instruct")
bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                         bnb_4bit_compute_dtype=torch.bfloat16)
base = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-3B-Instruct", quantization_config=bnb, device_map={"": 0})
model = PeftModel.from_pretrained(base, str(HERE / "adapters" / "demos-v1")).eval()
texto = render(tok, TOOLS, {"messages": msgs}) + "<|im_start|>assistant\n"
ids = tok(texto, return_tensors="pt", add_special_tokens=False).input_ids.to(0)
with torch.no_grad():
    out = model.generate(ids, max_new_tokens=220, do_sample=False)
print("\nENTRENADA (4 bits + adaptador):", tok.decode(out[0, ids.shape[1]:], skip_special_tokens=False)[:600])

# ¿y el prompt que ve Ollama es el mismo? longitud en tokens de lo que renderiza el entrenamiento
print("\ntokens del prompt (render del entrenamiento):", ids.shape[1], "| Ollama prompt_tokens:", r.get("usage", {}).get("prompt_tokens"))
vida.red.cerrar(); profe.red.cerrar()
