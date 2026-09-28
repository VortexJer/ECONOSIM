# Entrenar la IA en el sobremesa (encargo para Claude Code)

Contexto: ECONOSIM entrena a una IA inversora por IMITACIÓN. `training/demos.py` graba sesiones de
un robot que invierte bien (núcleo 75 % fondo del índice + satélite momentum), jugando contra los
gemelos reales del mundo; `training/train_qlora.py` entrena Qwen2.5-3B-Instruct (QLoRA) con ellas.
En el portátil (RTX 4050, 6 GB) iba a ~12 s por sesión: se mueve aquí. Lee `training/RESULTADOS.md`
(secciones 6-8) y `PLAN.md` fase 14 si necesitas más contexto. Todo en Windows + Git Bash/PowerShell.

## Pasos

1. **Máquina.** `nvidia-smi` (GPU y VRAM). Hace falta Python 3.13 y git. Clona
   `https://github.com/VortexJer/ECONOSIM` en el Escritorio como `econosim`.
2. **Entorno** (en `econosim/training/.venv`, que es la ruta que usan los scripts):
   `python -m venv training/.venv` y dentro:
   `pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124` (usa la rueda CUDA
   que corresponda a esta GPU) y luego
   `pip install transformers==5.17.0 peft==0.20.0 bitsandbytes==0.50.2 accelerate==1.15.0 pandas pyarrow aiohttp yfinance numpy safetensors gguf sentencepiece`.
   Comprueba `torch.cuda.is_available()`.
3. **Datos** (no están en git; se regeneran, desde la raíz `econosim/`, con `PYTHONIOENCODING=utf-8`):
   - `training/.venv/Scripts/python.exe scripts/fetch_market.py` → `data/market/` (precios 2004-hoy, yfinance).
   - `training/.venv/Scripts/python.exe scripts/fetch_news.py` → `data/news/headlines.parquet`
     (streaming de 5,7 GB de FNSPID, ~10 min).
   - `training/.venv/Scripts/python.exe training/noticias.py` → `data/news/scored.parquet` (FinBERT en GPU).
   - `training/.venv/Scripts/python.exe -u training/demos.py --lives 1000` → `training/data/demos.jsonl`
     (~7.500 sesiones, CPU, ~30-40 min). Debe acabar con errores 0 en casi todas las vidas.
4. **Prueba de velocidad** (desde `econosim/training/`):
   `.venv/Scripts/python.exe -u train_qlora.py --data data/demos.jsonl --out adapters/prueba --val 0 --accum 1 --max-steps 6`
   Anota los s/it. En el portátil eran ~10-12 s por sesión.
5. **Entrenamiento real**: todas las sesiones si cabe en una noche; si no, `--max-rows` para que
   dure ≤ 8 h. Ejemplo:
   `.venv/Scripts/python.exe -u train_qlora.py --data data/demos.jsonl --out adapters/demos-v1 --val 0.03 --epochs 1 > data/train_v1.log 2>&1`
   Con más VRAM (≥ 12 GB) puedes subir `--rank 32`; no cambies el modelo base (3B) sin avisar.
6. **Al terminar**: apunta en `training/RESULTADOS.md` (sección nueva "9. Imitación demos-v1"):
   GPU, sesiones, s/sesión, pérdida de entrenamiento y la de validación (vidas apartadas). Commit y
   push. El adaptador (`training/adapters/`) NO va a git (está en .gitignore): déjalo en disco.

## Cosas que ya se aprendieron (no las deshagas)

- En `train_qlora.py`: sin `prepare_model_for_kbit_training` (subía a fp32 la tabla de palabras,
  +1,2 GB); `use_gqa_in_sdpa=False` (con `enable_gqa` PyTorch 2.6 sin flash-attention cae al núcleo
  MATH, memoria cuadrática); pérdida solo en tokens del asistente y cabeza de vocabulario a trozos.
- Si la memoria de GPU se llena y Windows desborda a la RAM compartida, todo va 40 veces más lento:
  mejor bajar tamaño que desbordar.
- En Git Bash, no metas `\n` en heredocs de comandos (un filtro los altera): escribe scripts a fichero.
