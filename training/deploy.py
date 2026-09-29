"""Cierra el bucle: adaptador entrenado -> modelo servible como cerebro del sim.

    python deploy.py --adapter adapters/v1 --name qwen3b-v1

Pasos: fusiona el LoRA en la base, convierte a GGUF (script de llama.cpp) y lo registra
en Ollama con ese nombre. Después basta con apuntar el sim al modelo nuevo:
    ECONOSIM_UPSTREAM_MODEL=qwen3b-v1   (en sandbox/docker-compose.local.yml)
y volver a jugar vidas: eso es una iteración completa del entrenamiento.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONVERT_URL = ("https://raw.githubusercontent.com/ggml-org/llama.cpp/master/convert_hf_to_gguf.py")
BASE_MODEL = "Qwen/Qwen2.5-3B-Instruct"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=str(HERE / "adapters" / "v1"))
    ap.add_argument("--name", default="qwen3b-v1", help="nombre del modelo en Ollama")
    ap.add_argument("--base", default=BASE_MODEL)
    ap.add_argument("--outtype", default="q8_0", help="cuantización del GGUF (q8_0, f16...)")
    a = ap.parse_args()

    merged = HERE / "merged" / a.name
    gguf = HERE / "gguf" / f"{a.name}.gguf"
    gguf.parent.mkdir(parents=True, exist_ok=True)

    # 1) fusionar el adaptador en la base
    if not merged.exists():
        print("fusionando el adaptador en la base…")
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        tok = AutoTokenizer.from_pretrained(a.base)
        # El LoRA se entrenó sobre la base en 4 bits (nf4, train_qlora.py): hay que fusionarlo sobre ESOS pesos
        # (cuantizados y vueltos a bf16), no sobre la base original. Medido el 29/09: fusionado sobre la base
        # original, la primera sesión ya se desviaba del profesor; con la entrenada tal cual, la calcaba.
        bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                                 bnb_4bit_compute_dtype=torch.bfloat16)
        model = AutoModelForCausalLM.from_pretrained(a.base, quantization_config=bnb, device_map={"": 0})
        model = model.dequantize().to(torch.bfloat16)
        model = PeftModel.from_pretrained(model, a.adapter)
        model = model.merge_and_unload()
        merged.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(merged, safe_serialization=True)
        tok.save_pretrained(merged)
        print("  fusionado en", merged)

    # 2) convertir a GGUF con el script de llama.cpp
    conv = HERE / "convert_hf_to_gguf.py"
    if not conv.exists():
        print("descargando el conversor de llama.cpp…")
        urllib.request.urlretrieve(CONVERT_URL, conv)
    if not gguf.exists():
        print("convirtiendo a GGUF…")
        r = subprocess.run([sys.executable, str(conv), str(merged),
                            "--outfile", str(gguf), "--outtype", a.outtype])
        if r.returncode != 0:
            raise SystemExit("falló la conversión a GGUF (¿falta 'pip install gguf sentencepiece'?)")

    # 3) registrarlo en Ollama
    mf = HERE / f"Modelfile.{a.name}"
    mf.write_text(f"FROM {gguf.as_posix()}\nPARAMETER temperature 0.3\nPARAMETER num_ctx 12288\n",
                  encoding="utf-8")
    r = subprocess.run(["ollama", "create", a.name, "-f", str(mf)])
    if r.returncode != 0:
        raise SystemExit("falló 'ollama create'")
    print(f"\nlisto: modelo '{a.name}' servible en Ollama.")
    print(f"Apunta el sim a él con ECONOSIM_UPSTREAM_MODEL={a.name} y juega vidas otra vez.")


if __name__ == "__main__":
    main()
