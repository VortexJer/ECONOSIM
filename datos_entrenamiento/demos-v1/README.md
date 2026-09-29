# demos-v1 — con qué se entrenó la IA

`demos.jsonl.gz`: **7.551 sesiones** del robot profesor, generadas con

```bash
PYTHONIOENCODING=utf-8 training/.venv/Scripts/python.exe -u training/demos.py --lives 1000
```

(semilla 11, `--noticias no`, 5.000 € por vida, 126 sesiones de bolsa ≈ 6 meses, arranques al azar
entre 2005-03 y 2021-06: **2022-2026 no aparece**). Cada línea es una sesión en el formato del agente
(`messages`: system / user / assistant con `tool_calls` / tool), con `vida` y `start`. Todo corre
contra los gemelos del mundo (Alpaca, FMP, Qonto, Hetzner) con fechas disfrazadas y nombres
inventados; cada comando que se ve se ejecutó de verdad y su salida es la respuesta exacta.

`demos_meta.jsonl`: una línea por vida (inicio, capital final, rentabilidad, índice, sesiones,
errores). Solo las vidas sin errores dan ejemplos: las 1.000 salieron con 0 errores.

El profesor en estas vidas: **~14 %/año de media** (el índice ~11 %), gana al índice en el 58 %,
22 % de vidas en pérdidas; mejor vida +60 %, peor −39,5 %. Ojo: universo de empresas que siguen hoy
en bolsa (sesgo de supervivencia, ver `training/RESULTADOS.md` §6).

La IA `modelos/qwen3b-demos-v1` se entrenó con el **trozo 1**: las primeras 2.200 sesiones
(`train_qlora.py --max-rows 2200 --val 0.03`, vidas 0-293). Los trozos siguientes empiezan en
`--desde-vida 294`.

Los datos de mercado, noticias y cuentas que usan los gemelos NO van aquí (licencias de Yahoo y
compañía): se regeneran con `scripts/fetch_market.py`, `fetch_news.py`, `fetch_pit.py` y
`training/noticias.py` (ver `training/SOBREMESA.md`).
