# Traspaso al portátil (29/09/2026): dónde se quedó el sobremesa y qué sigue

Léelo entero antes de tocar nada, junto con `training/SOBREMESA.md` (sobre todo **"Reglas de los
ejemplos simulados"**, no negociables) y `training/RESULTADOS.md` §9-12. El dueño no sabe jerga
técnica: explícale todo en castellano sencillo y pregunta antes de decisiones que sean suyas.

## Qué se hizo en el sobremesa (todo está en git, commits del 29/09)

1. **Encargo de SOBREMESA.md cumplido** hasta el entrenamiento: datos regenerados, 1.000 vidas de
   demostración (7.551 sesiones, 0 errores), trozo 1 entrenado (RTX 3050 8 GB, 8 h, validación
   **0,0109**). Resultados en `RESULTADOS.md` §9.
2. **La IA entrenada está en git**: `modelos/qwen3b-demos-v1/` (adaptador LoRA, 120 MB por
   **Git LFS**: haz `git lfs install` y `git lfs pull`). Datos con los que se entrenó:
   `datos_entrenamiento/demos-v1/` (README con cómo se generaron).
3. **La IA juega en el simulador** con `training/evalua_ia.py` (§10): calca al profesor al céntimo en
   2022 (no visto) y hace una vida de 6 meses con 76 sesiones y 0 errores. Mismo resultado que el
   profesor: no elige mejor que la receta, la imita.
4. **Investigación "agresiva"** pedida por el dueño (`training/agresivo/`, §11-12): optimizar pesos
   para máxima rentabilidad encontró suerte (examen 2022-26: −25 %/año); las reglas publicadas de
   los grandes (tendencia multimercado, multifactor, control de volatilidad) igualan al índice por
   unidad de riesgo. **2022-2026 ya se usó una vez como examen: está gastado.**
5. Herramientas del sobremesa en `training/sobremesa/`: panel web del entrenamiento y visor de vida
   (`panel/servidor.py`, http://127.0.0.1:8765, `/vida`), vigilante, lanzador que impide suspender
   Windows y las comprobaciones unlazy (`verify.py`, `GATES.md`).

## Decisiones PENDIENTES del dueño (pregúntale; no las tomes tú)

- **¿Profesor nuevo que use herramientas de inversor profesional?** Choca con la regla 3 (la receta
  es la medida). El dueño pidió más riesgo, operar más y que la IA "sirva de algo más que el bot";
  antes de cambiar `demos.py` o la receta hay que preguntarle.
- **¿Se quedan los 16 fondos nuevos del mundo?** El commit "Mercado: fondos de bonos, oro, materias
  primas…" añadió a `econosim/market/mask.py` EFA, EEM, TLT, IEF, SHY, LQD, TIP, GLD, SLV, DBC, USO,
  VNQ, UUP, EFZ, EUM, TBF. `MarketData` carga TODO `data/market/*.csv`, así que si se bajan sus CSV
  cambian las listas de fondos que ven las demostraciones (regla 5). Revertible con `git revert`.
- **¿Trozos 2-4 del entrenamiento?** Recomendación: no; la imitación ya está al tope (§9).
- **¿Escritorio cuantitativo para la IA?** Idea diseñada, no construida: un gemelo nuevo con
  tendencia por mercado, ranking por factores, riesgo de cartera, calculadora de tamaños por
  volatilidad objetivo y un probador de ideas solo con datos hasta "hoy". Cambia lo que ve la IA
  (SERVICIOS.md): preguntar primero.

## Tareas que sí puedes hacer sin preguntar

- Reproducir y verificar: `git lfs pull`, `cd training && python deploy.py --adapter
  ../modelos/qwen3b-demos-v1 --name qwen3b-demos-v1` y `python evalua_ia.py --fecha 2022-01-03
  --sesiones 1` (debe calcar al profesor: SPY 3.647 €, NVDA 624 €, GOOGL 622 €).
- Arreglar debilidades medidas de la IA SIN tocar la receta, si el dueño está de acuerdo: calcula
  mal cuánto dormir hasta la apertura (repite 3.095 min memorizados).

## Trampas ya pagadas (no las repitas)

- **Fusionar el LoRA sobre la base original la estropea**: se entrenó sobre nf4. `deploy.py` ya
  fusiona sobre la base cuantizada y vuelta a bf16. Si ves a la IA inventar campos del bróker o
  creer el mercado cerrado, es esto.
- **Conversor GGUF**: el `convert_hf_to_gguf.py` de llama.cpp `master` ya no funciona suelto (pide
  el paquete `conversion`). Usa la etiqueta **b8639** (último en un solo fichero) con su `gguf-py`
  en `PYTHONPATH`. `deploy.py` aún descarga el de master: cámbialo o pásale el de b8639.
- **Ollama 0.33** no importa Qwen2 desde safetensors (`unsupported MLX architecture`), ni con
  `--quantize`: siempre GGUF + `ollama create` con `FROM <gguf>`.
- **Evalúa con temperatura 0**: con 0,3 un token desviado en el script de ranking (1.900
  caracteres) tuerce todo.
- **`train_qlora.py`**: el log de pérdida sale ×8 (el Trainer suma la acumulación; la validación va
  por sesión); ya no revienta en el eval (`(loss, {})`); guarda cada 50 pasos.
- **La GPU se comparte con la pantalla**: con la VRAM justa, un navegador con animaciones o con
  aceleración gráfica frena el entrenamiento hasta 2x. Mira paneles en Edge `--disable-gpu`, sin
  animaciones continuas. En el portátil (6 GB) es aún más crítico.
- **PowerShell 5.1**: `0x80000001` es un Int32 negativo; para `SetThreadExecutionState` usa
  `[uint32]2147483649` o no impide la suspensión. Y `*>` retiene el log hasta el final: redirige con
  `cmd /c "... > log 2>&1"`.
- **evalua_ia**: los ficheros del agente (ranking.py, NOTES.md) viven toda la vida; si acortas el
  sueño (`--factor-sueno`), hazlo en días enteros o despertará siempre antes de abrir la bolsa.
- **Búsquedas de estrategias**: más pruebas = más suerte disfrazada. Siempre periodo de búsqueda,
  de elección y examen separados, listón de estrategias al azar y Sharpe deflactado
  (`agresivo/solidez.py`, `agresivo/clones.py`).
- El repositorio se movió a `https://github.com/VortexJer/ECONOSIM.git` (el viejo redirige).

## Datos que NO están en git (se regeneran; ver SOBREMESA.md paso 3)

`data/market` (fetch_market.py; para los fondos nuevos: `fetch_market.py EFA EEM TLT IEF SHY LQD TIP
GLD SLV DBC USO VNQ UUP EFZ EUM TBF`), `data/news` (fetch_news.py + training/noticias.py),
`data/pit/prices.parquet` (fetch_pit.py, ~150 MB), `training/data/agresivo/panel.npz`
(`python -m agresivo.datos` desde training/; `datos.py` tiene la ruta de prices.parquet del
sobremesa en `PIT_PRECIOS`: cámbiala a `data/pit/prices.parquet`).
