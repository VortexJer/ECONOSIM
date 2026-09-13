# Qué está corriendo esta noche

`overnight.py` (proceso desacoplado, sobrevive a la sesión de Claude) hace iteraciones de:
1. 3 vidas × 2 días con el **PROFESOR = Sonnet** (vía `claude -p`, tu Claude Code, sin clave de API; si falla o toca el límite de uso, cae al 7B local `qwen7b` sin que el agente lo note — se ve en `data/claude_bridge.log`)
2. dataset con TODAS las vidas de profesor acumuladas → **QLoRA del alumno** (Qwen2.5-3B, 4-bit, RTX 4050)
3. fusión → GGUF → `ollama create qwen3b` (el sim piensa con la versión nueva, mismo nombre)
4. 1 vida de **evaluación** con el alumno recién entrenado (`evalN-000`) → se ve en "GENERACIONES ANTERIORES" del panel

Vigila Docker Desktop y Ollama y los relanza si se caen.

## Dónde mirar
- Log en vivo: `training/data/overnight.log`
- Vidas y puntuaciones: `training/data/episodes/<semilla>/outcome.json` (+ `calendar.json`, `sessions/`)
  - `it<fecha>-NNN` = vidas del profesor · `evalN-000` = el alumno tras la iteración N · `haiku-demo` = la demo de Haiku
- Panel (`ECONOSIM Panel` del Escritorio): mientras hay una vida en curso se ve en vivo; entre vidas muestra las generaciones anteriores.
- Adaptadores entrenados: `training/adapters/<fecha>/`

## Cómo parar
Crear un fichero vacío `training/data/STOP` (para al acabar la iteración en curso), o cerrar el proceso `python overnight.py`.

## Cómo es el profesor
`training/claude_bridge.py` traduce las peticiones del agente a Claude. Backend `cli` = `claude -p --model sonnet --tools ""` (tu cuenta de Claude Code, sin herramientas en el host, con un contexto veraz de que es una simulación — sin él Claude Code rehúsa el papel). Si algún día pones `ANTHROPIC_API_KEY` en `.env`, usa el SDK oficial automáticamente. freellmapi NO sirve: su WAF bloquea el prompt del agente (403).
