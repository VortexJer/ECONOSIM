# qwen3b-demos-v1 — la IA imitadora del profesor

Adaptador LoRA (rango 16, α 32, todas las proyecciones) sobre **Qwen/Qwen2.5-3B-Instruct**, entrenado
con QLoRA (base en 4 bits nf4) para imitar al robot profesor de `training/demos.py`
(núcleo 75 % en el fondo del índice + 2 empresas por momentum 12-1, revisión mensual).

| | |
|---|---|
| Datos | trozo 1 de `datos_entrenamiento/demos-v1`: 2.141 sesiones de entreno + 59 de validación (8 vidas apartadas) |
| Máquina | RTX 3050 8 GB, 8,0 h (268 pasos de 8 sesiones) |
| Validación | 0,0109 de pérdida por token del asistente (~99 % de tokens del profesor) |
| Resultado en el simulador | calca al profesor en 2022 (no visto); vida de 6 meses con 76 sesiones y 0 errores |

Detalle en `training/RESULTADOS.md` §9-10. El fichero `.safetensors` va por **Git LFS**
(`git lfs install` antes de clonar, o `git lfs pull` después).

## Usarla

**Tal como se entrenó** (transformers + peft, GPU con ≥ 6 GB): base en 4 bits nf4 + este adaptador
(ver `training/diagnostico_ia.py`).

**En Ollama** (lo que usa `training/evalua_ia.py`, ~47 tokens/s en una RTX 3050):

```bash
cd training
python deploy.py --adapter ../modelos/qwen3b-demos-v1 --name qwen3b-demos-v1
```

`deploy.py` fusiona el adaptador sobre la base **cuantizada en 4 bits y vuelta a bf16** (fusionarlo
sobre la base original la estropea: se desvía del profesor desde la primera frase). Para el GGUF
hace falta el conversor de llama.cpp: el de `master` ya no funciona suelto; la etiqueta **b8639**
es la última con `convert_hf_to_gguf.py` en un solo fichero (junto a su `gguf-py`). Con Ollama 0.33,
`ollama create` con `FROM <gguf>` (importar safetensors de Qwen2 directamente no está soportado).

Genera sin azar (temperatura 0): con 0,3 un solo token desviado en el script de ranking torcía
el programa entero.

## Lo que sabe y lo que no

Sabe operar las herramientas del agente (bróker, datos, cuentas, noticias) siguiendo la receta del
profesor, en fechas y con nombres que nunca vio. **No** elige mejor que la receta (la imita), no ha
visto instrucciones del dueño ni imprevistos, y para "dormir hasta la apertura" repite un número
memorizado (3.095 min) en vez de calcularlo.
