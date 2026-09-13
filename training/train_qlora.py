"""Entrena (QLoRA) el cerebro con las vidas que SÍ funcionaron.

Ajustado a una RTX 4050 de 6 GB: base en 4-bit (nf4), adaptadores LoRA, gradient
checkpointing, lote 1 con acumulación y secuencias cortas. No es RL: es SFT por rechazo
(imitas lo que puntuó alto). Es lo que cabe en un portátil, y es honesto decir que la
mejora será modesta con un 3B.

    python train_qlora.py --data data/sft.jsonl --out adapters/v1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_MODEL = "Qwen/Qwen2.5-3B-Instruct"      # el mismo que sirve Ollama como qwen3b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE / "data" / "sft.jsonl"))
    ap.add_argument("--out", default=str(HERE / "adapters" / "v1"))
    ap.add_argument("--base", default=BASE_MODEL)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--max-len", type=int, default=1024, help="bájalo si te quedas sin VRAM")
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16)
    a = ap.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                              DataCollatorForLanguageModeling, Trainer, TrainingArguments)

    if not torch.cuda.is_available():
        raise SystemExit("no hay GPU visible para torch; el entrenamiento en CPU no es viable aquí")
    print("GPU:", torch.cuda.get_device_name(0),
          f"({torch.cuda.get_device_properties(0).total_memory/1e9:.1f} GB)")

    rows = [json.loads(l) for l in Path(a.data).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        raise SystemExit(f"{a.data} está vacío: juega vidas y construye el dataset primero")
    print(f"{len(rows)} sesiones de entrenamiento")

    tok = AutoTokenizer.from_pretrained(a.base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    def render(r):
        # la plantilla de Qwen2.5 sabe pintar tool_calls y role=tool
        text = tok.apply_chat_template(r["messages"], tokenize=False, add_generation_prompt=False)
        return {"text": text}

    ds = Dataset.from_list([render(r) for r in rows])

    def tokenize(b):
        out = tok(b["text"], truncation=True, max_length=a.max_len)
        return out

    ds = ds.map(tokenize, batched=True, remove_columns=["text"])

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_use_double_quant=True,
                             bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(a.base, quantization_config=bnb, device_map={"": 0})
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model.config.use_cache = False
    lora = LoraConfig(r=a.rank, lora_alpha=a.rank * 2, lora_dropout=0.05, bias="none",
                      task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                      "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    args = TrainingArguments(
        output_dir=a.out, num_train_epochs=a.epochs,
        per_device_train_batch_size=1, gradient_accumulation_steps=8,
        gradient_checkpointing=True, learning_rate=a.lr, lr_scheduler_type="cosine",
        warmup_steps=5, logging_steps=5, save_strategy="epoch",
        bf16=True, optim="paged_adamw_8bit", report_to=[])
    trainer = Trainer(model=model, args=args, train_dataset=ds,
                      data_collator=DataCollatorForLanguageModeling(tok, mlm=False))
    trainer.train()
    model.save_pretrained(a.out)
    tok.save_pretrained(a.out)
    print(f"adaptador guardado en {a.out}")


if __name__ == "__main__":
    main()
