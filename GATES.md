# Gates: fase 14 — cerebro Laya, caja cuantitativa (indicadores y griegas) y solo inversión

OWNS: econosim/**, agent/**, desactivado/**, scripts/**, training/**, tests/**, data/rates/**, PLAN.md, GATES.md

Scope: los profesores LLM salen del bucle de entrenamiento y entra Laya (modelo de decisión de una pasada, ModernBERT ~400M, Apache-2.0), que no escribe comandos: decide VENDER / MANTENER / COMPRAR por acción sobre un estado de texto, y un "cuerpo" determinista ejecuta contra los MISMOS gemelos (libro, Hetzner, bróker con comisión). Se le da la caja completa del inversor: indicadores técnicos (medias, RSI, MACD, Bollinger, ATR, ADX, Williams, volatilidad, drawdown, Sharpe, beta) y opciones con griegas (delta, gamma, theta, vega, rho) por Black-Scholes con tipo del Tesoro y VIX REALES de cada día, también por API para la IA con LLM (endpoints reales de Alpaca y FMP). El mundo queda en SOLO INVERSIÓN por defecto: lo demás está desactivado, no borrado, y la IA no puede leer que existe. El entrenamiento es por generaciones con elitismo en validación y un tramo de test que nunca decide.

- [ ] G0: este ledger declara resultados que pueden fallar
  CHECK: node ~/.claude/skills/unlazy/scripts/gate-lint.mjs GATES.md
  EXPECT: LINT OK

- [ ] G1: la caja cuantitativa da los números correctos y no mira el futuro — cada indicador contra una implementación independiente (pandas/numpy) sobre histórico real, Black-Scholes contra el valor de libro y la paridad put-call, las cinco griegas contra diferencias finitas, tipo y VIX reales, y el endpoint technical_indicator de FMP no sirve barras futuras ni cambia una fecha pasada al consultarla más tarde
  CHECK: python tests/check_quant.py
  EXPECT: QUANT OK

- [ ] G2: opciones como en la API real de Alpaca — contratos OCC con fechas del calendario mostrado, sin año ni símbolo real; instantáneas con griegas y volatilidad implícita declaradas como estimación de modelo; compra al ask con 0,65/contrato; venta en descubierto rechazada; cierre al bid; mercado cerrado; liquidación al intrínseco al vencer; y en vivo nada se ejecuta
  CHECK: python tests/check_options.py
  EXPECT: OPTIONS OK

- [ ] G3: las acciones siguen como antes tras sacar la lógica de la orden a place_order (spread, rechazos, comisión, cartera en la escala de la caja)
  CHECK: python tests/check_alpaca_orders.py && python tests/check_portfolio.py && python tests/check_live_mode.py
  EXPECT: LIVE MODE OK

- [ ] G4: solo inversión por defecto y la IA no sabe que existe lo demás — ningún fichero de agent/ (lo que monta /opt/agent) nombra tienda, anuncios, dominios, correo o apuestas; el catálogo cuenta opciones e indicadores; el modo completo sigue guardado en desactivado/ y el conmutador va y vuelve sin alterar nada; el mundo arranca en solo inversión sin pedirlo
  CHECK: python tests/check_modo_inversor.py
  EXPECT: MODO INVERSOR OK

- [ ] G5: la vida de Laya es honesta — cada orden se decide en una sesión ANTERIOR a la que se ejecuta, el estado no lleva símbolo ni año real y cabe en la ventana del modelo, el dinero pasa por el libro real (Hetzner, comisiones) y el índice con tesorería no muere de impago
  CHECK: training/.venv/Scripts/python.exe tests/check_laya_life.py
  EXPECT: LAYA LIFE OK

- [ ] G6: Laya aprende algo — la campeona es de una generación entrenada (>= 1) que ganó a Laya sin entrenar en validación, opera de verdad (compra en más del 2 % de las decisiones) y acaba con más dinero que quedarse en efectivo
  CHECK: python tests/check_laya_learning.py
  EXPECT: LAYA LEARNING OK

- [ ] G7: el entrenamiento por generaciones queda corriendo sin límite de hora hasta que el usuario pida pararlo (progreso escrito hace menos de 45 min)
  CHECK: python tests/check_laya_learning.py --running
  EXPECT: LAYA RUNNING OK
