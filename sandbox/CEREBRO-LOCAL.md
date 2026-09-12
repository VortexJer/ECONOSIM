# Cerebro local (sin tocar la API compartida)

El sim puede pensar con un modelo LOCAL en vez de freellmapi. El gemelo de OpenRouter
enruta TODO al modelo local; la IA sigue creyendo que usa el modelo que pide y se le
cobra su precio (el "engaño" intacto).

## Modelo: qwen3b (Qwen2.5-3B-Instruct, tool-calling OK, entra en 6 GB VRAM)

`ollama pull` se cuelga con NordVPN Threat Protection activo, así que se hace *sideload*:

```bash
# 1) bajar el GGUF por curl (esquiva el filtro de NordVPN que rompe 'ollama pull')
curl -sL -o qwen2.5-3b-instruct-q4_k_m.gguf \
  https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
# 2) importarlo (Modelfile con FROM ./<gguf> + PARAMETER num_ctx 8192)
ollama create qwen3b -f Modelfile.qwen3b
# 3) comprobar tool-calling: debe emitir tool_calls (bash)
```

## Correr el sim con el cerebro local

- **En proceso** (host → Ollama en localhost): exportar y lanzar `python -m econosim.run`:
  ```
  ECONOSIM_FAKE_UPSTREAM=0
  ECONOSIM_UPSTREAM_BASE_URL=http://localhost:11434/v1
  ECONOSIM_UPSTREAM_API_KEY=ollama
  ECONOSIM_UPSTREAM_MODEL=qwen3b
  ```
- **Docker** (agente completo con todos los servicios): usar el override, con Ollama en 0.0.0.0
  para que el contenedor lo alcance:
  ```
  setx OLLAMA_HOST 0.0.0.0:11434   # y reiniciar Ollama
  cd sandbox
  docker compose -f docker-compose.yml -f docker-compose.local.yml up --build
  ```

`ECONOSIM_UPSTREAM_MODEL` sale de `run.py` → `HTTPUpstream(model_override=...)`: sustituye el
`"auto"` por el modelo local SOLO en el envío real; el precio y el modelo que la IA cree usar
no cambian.

Verificado: la IA piensa con `qwen3b`, cree usar `gpt-oss-120b`, y paga créditos de su banco
(saldo 50 € → 44,91 € tras autorecargar). Ninguna llamada sale a freellmapi.
