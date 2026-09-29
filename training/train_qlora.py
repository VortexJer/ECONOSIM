"""Entrena (QLoRA) el cerebro por imitación: demostraciones (demos.py) o vidas que funcionaron.

Ajustado a una RTX 4050 de 6 GB: base en 4-bit (nf4), adaptadores LoRA, gradient
checkpointing, lote 1 con acumulación. Tres cosas que importan:

  * Aprende SOLO de lo que escribe el asistente (pensamiento + llamadas a herramientas). El
    encargo, las notas y las respuestas de los servicios son contexto, no se imitan: si no,
    la mitad del esfuerzo se iría en memorizar JSON de Alpaca.
  * La plantilla de Qwen2.5 recibe las MISMAS herramientas que ve el agente (agent.TOOLS) y
    los argumentos como objeto, igual que en inferencia.
  * La pérdida se calcula solo en las posiciones del asistente (la cabeza de vocabulario, de
    151k salidas, no se aplica a las ~5.000 posiciones restantes): es lo que deja entrenar
    sesiones de 8k tokens en 6 GB.

    python train_qlora.py --data data/demos.jsonl --out adapters/demos-v1

A trozos (cada uno sigue lo aprendido en el anterior; al acabar, cada trozo imprime cómo lanzar el siguiente):

    python train_qlora.py --max-rows 2200 --out adapters/demos-v1
    python train_qlora.py --desde-vida 294 --init-adapter adapters/demos-v1 --max-rows 2200 --out adapters/demos-v1b
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent"))
BASE_MODEL = "Qwen/Qwen2.5-3B-Instruct"      # el mismo que sirve Ollama como qwen3b
INICIO, FIN = "<|im_start|>assistant\n", "<|im_end|>"


def render(tok, tools, r) -> str:
    msgs = []
    for m in r["messages"]:
        m = dict(m)
        if m.get("tool_calls"):
            m["tool_calls"] = [{**c, "function": {**c["function"], "arguments":
                                json.loads(c["function"]["arguments"]) if isinstance(c["function"]["arguments"], str)
                                else c["function"]["arguments"]}} for c in m["tool_calls"]]
        msgs.append(m)
    return tok.apply_chat_template(msgs, tools=tools, tokenize=False, add_generation_prompt=False)


def etiquetar(tok, texto: str, max_len: int):
    """input_ids + labels: -100 salvo en los tramos del asistente (incluido su <|im_end|>)."""
    enc = tok(texto, return_offsets_mapping=True, add_special_tokens=False)
    tramos, i = [], 0
    while (j := texto.find(INICIO, i)) >= 0:
        k = texto.find(FIN, j + len(INICIO))
        k = len(texto) if k < 0 else k + len(FIN)
        tramos.append((j + len(INICIO), k))
        i = k
    labels = []
    for tid, (a, b) in zip(enc.input_ids, enc.offset_mapping):
        dentro = any(s <= a < e for s, e in tramos)
        labels.append(tid if dentro else -100)
    ids = enc.input_ids
    if len(ids) > max_len:                       # no debería pasar (las demos rondan 4-8k): se corta el final
        ids, labels = ids[:max_len], labels[:max_len]
    return ids, labels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE / "data" / "demos.jsonl"))
    ap.add_argument("--out", default=str(HERE / "adapters" / "demos-v1"))
    ap.add_argument("--base", default=BASE_MODEL)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--max-len", type=int, default=8192)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--accum", type=int, default=8, help="sesiones por paso de optimizador")
    ap.add_argument("--max-steps", type=int, default=-1, help="para pruebas de velocidad")
    ap.add_argument("--val", type=float, default=0.05, help="fracción de VIDAS apartadas para medir")
    ap.add_argument("--max-rows", type=int, default=0, help="0 = todas")
    ap.add_argument("--desde-vida", type=int, default=0,
                    help="empieza en esta vida (entrenar a trozos: el trozo anterior dice por cuál seguir)")
    ap.add_argument("--init-adapter", default="", help="adaptador de un trozo anterior del que seguir aprendiendo")
    a = ap.parse_args()

    import torch
    import torch.nn.functional as F
    import transformers.integrations.sdpa_attention as sdpa
    from torch.utils.checkpoint import checkpoint
    # Sin máscara, transformers pide a SDPA `enable_gqa`; en PyTorch 2.6 sin flash-attention (Windows)
    # eso solo lo hace el núcleo MATH, que monta la matriz de atención entera (memoria cuadrática).
    # Repitiendo las cabezas K/V se usa el núcleo eficiente: memoria lineal (medido: 8k tokens caben).
    sdpa.use_gqa_in_sdpa = lambda *a, **k: False
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer, TrainingArguments
    from agent import TOOLS

    if not torch.cuda.is_available():
        raise SystemExit("no hay GPU visible para torch; el entrenamiento en CPU no es viable aquí")
    print("GPU:", torch.cuda.get_device_name(0), f"({torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB)")

    rows = [json.loads(l) for l in Path(a.data).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        raise SystemExit(f"{a.data} está vacío: genera demostraciones o juega vidas primero")
    if a.desde_vida:
        rows = [r for r in rows if r.get("vida", -1) >= a.desde_vida]
    if a.max_rows:
        rows = rows[: a.max_rows]
    # el siguiente trozo empieza en una vida nueva: una vida partida podría entrenar en un trozo y medir en otro
    siguiente = max((r["vida"] for r in rows if "vida" in r), default=-1) + 1
    vidas = sorted({r.get("vida", r.get("episode", i)) for i, r in enumerate(rows)}, key=str)
    random.Random(0).shuffle(vidas)
    val_vidas = set(vidas[: max(1, int(len(vidas) * a.val))]) if a.val > 0 else set()
    tok = AutoTokenizer.from_pretrained(a.base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    def prep(rs):
        out = []
        for r in rs:
            ids, lab = etiquetar(tok, render(tok, TOOLS, r), a.max_len)
            if any(x != -100 for x in lab):
                out.append({"input_ids": ids, "labels": lab})
        return out
    tren = prep([r for i, r in enumerate(rows) if r.get("vida", r.get("episode", i)) not in val_vidas])
    val = prep([r for i, r in enumerate(rows) if r.get("vida", r.get("episode", i)) in val_vidas])
    largos = sorted(len(x["input_ids"]) for x in tren)
    print(f"{len(tren)} sesiones de entrenamiento, {len(val)} de validación (vidas apartadas: {len(val_vidas)}) · "
          f"tokens mediana {largos[len(largos) // 2]}, máx {largos[-1]} · "
          f"{sum(sum(l != -100 for l in x['labels']) for x in tren) / len(tren):.0f} tokens del asistente por sesión")

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                             bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(a.base, quantization_config=bnb, device_map={"": 0},
                                                 attn_implementation="sdpa")
    # NO prepare_model_for_kbit_training: pasa a fp32 las capas sin cuantizar y la tabla de palabras
    # (151k x 2048, compartida con la cabeza) sola ocupa ~1,2 GB más: con 6 GB desbordaba a la RAM
    # del sistema y cada paso tardaba minutos. Se deja en bf16 y se activa a mano lo necesario.
    for prm in model.parameters():
        prm.requires_grad_(False)
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    model.config.use_cache = False
    if a.init_adapter:                                 # sigue desde el trozo anterior (su rango manda, no --rank)
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, a.init_adapter, is_trainable=True)
    else:
        lora = LoraConfig(r=a.rank, lora_alpha=a.rank * 2, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
                          target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
        model = get_peft_model(model, lora)
    model.print_trainable_parameters()
    causal = model.base_model.model                    # Qwen2ForCausalLM con los LoRA dentro

    def collate(b):
        x = b[0]                                       # lote 1: sin relleno
        ids = torch.tensor([x["input_ids"]])
        return {"input_ids": ids, "labels": torch.tensor([x["labels"]]), "attention_mask": torch.ones_like(ids)}

    class Imitacion(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, **_):
            labels = inputs.pop("labels")
            # sin attention_mask (lote 1, sin relleno): así SDPA usa el núcleo causal eficiente; con máscara
            # explícita montaba la matriz de atención entera (~3 GB a 7k tokens)
            h = causal.model(input_ids=inputs["input_ids"]).last_hidden_state
            y = labels[:, 1:]
            sel = y != -100
            hs, ys = h[:, :-1][sel], y[sel]
            # la cabeza de 151k palabras, a trozos y recalculada en la vuelta atrás: guardar sus
            # probabilidades de golpe (~1.200 posiciones x 151k x fp32) eran ~2 GB fijos
            total = hs.new_zeros((), dtype=torch.float32)
            for i in range(0, len(ys), 256):
                total = total + checkpoint(
                    lambda hh, yy: F.cross_entropy(causal.lm_head(hh).float(), yy, reduction="sum"),
                    hs[i:i + 256], ys[i:i + 256], use_reentrant=False)
            loss = total / len(ys)
            # en el examen el Trainer pide (loss, outputs) y hace outputs[1:] si no es un dict: con None
            # reventaba al llegar al primer eval (paso 100). Un dict vacío = "no hay logits", solo la pérdida.
            return (loss, {}) if return_outputs else loss

    args = TrainingArguments(
        output_dir=a.out, num_train_epochs=a.epochs, per_device_train_batch_size=1, per_device_eval_batch_size=1,
        gradient_accumulation_steps=a.accum, max_steps=a.max_steps, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False}, learning_rate=a.lr, lr_scheduler_type="cosine",
        warmup_steps=10, logging_steps=1 if a.max_steps > 0 else 5, save_strategy="steps", save_steps=50, save_total_limit=2,
        eval_strategy="steps" if val else "no", eval_steps=100, bf16=True, optim="paged_adamw_8bit",
        report_to=[], remove_unused_columns=False, dataloader_num_workers=0)
    trainer = Imitacion(model=model, args=args, train_dataset=tren, eval_dataset=val or None, data_collator=collate)
    trainer.train()
    if val:
        print("pérdida final en vidas apartadas:", trainer.evaluate().get("eval_loss"))
    model.save_pretrained(a.out)
    tok.save_pretrained(a.out)
    print(f"adaptador guardado en {a.out}")
    print(f"siguiente trozo: --desde-vida {siguiente} --init-adapter {a.out}")


if __name__ == "__main__":
    main()
