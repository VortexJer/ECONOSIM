# Gates: fase 2 — cerebro de pago (gemelo OpenRouter), banco (gemelo Qonto) y agente en el sandbox

OWNS: econosim/**, agent/**, sandbox/**, tests/**, data/**, scripts/**, PLAN.md, GATES.md, README.md

Scope: la IA piensa a través de un gemelo de la API de OpenRouter que cobra cada llamada a precio real del modelo pedido, compra créditos con la tarjeta (comisión real de Stripe, cambio EUR/USD real) y respeta los límites reales de los modelos gratuitos; ve su dinero por un gemelo de la API de Qonto; y un agente por sesiones corre dentro del sandbox, paga por pensar y no tiene memoria salvo el disco.

- [ ] G0: este ledger declara resultados que pueden fallar
  CHECK: node ~/.claude/skills/unlazy/scripts/gate-lint.mjs GATES.md
  EXPECT: LINT OK
  EVIDENCE: pending

- [ ] G1: el gemelo OpenRouter sirve el catálogo real (mismos ids y precios que el snapshot con fuente), exige la clave, y una chat completion devuelve el esquema real con coste = tokens × precio del modelo pedido, descontado de los créditos y consultable en /generation y /credits
  CHECK: python tests/check_openrouter.py
  EXPECT: OPENROUTER OK
  EVIDENCE: pending

- [ ] G2: la primera llamada de pago dispara la auto-recarga: cargo en el banco = (importe + comisión Stripe 5.5% mín. $0.80) / tipo BCE × recargo de tarjeta; sin saldo en el banco la API responde 402 y no cobra nada; la cuenta gratuita pasa a "de pago" al comprar
  CHECK: python tests/check_or_topup.py
  EXPECT: TOPUP OK
  EVIDENCE: pending

- [ ] G3: un modelo :free no cuesta nada pero se corta con 429 en la petición 51 del día (1001 con ≥ $10 comprados) y a la 21 en un minuto; al día siguiente vuelve a funcionar
  CHECK: python tests/check_or_free.py
  EXPECT: FREE OK
  EVIDENCE: pending

- [ ] G4: con stream=true la respuesta es SSE válido (chunks, finish_reason, usage, [DONE]) que reconstruye el mismo texto, y se cobra una sola vez
  CHECK: python tests/check_or_stream.py
  EXPECT: STREAM OK
  EVIDENCE: pending

- [ ] G5: el gemelo Qonto exige la credencial real (org:secret), su saldo es exactamente el del ledger y sus transacciones son los asientos con lado, importe, contraparte y fecha mostrada
  CHECK: python tests/check_qonto.py
  EXPECT: QONTO OK
  EVIDENCE: pending

- [ ] G6: el agente ejecuta sesiones contra el gemelo con un modelo guionizado: usa bash, deja un fichero, termina con end_session, la siguiente sesión arranca sin el contexto anterior, cada llamada queda cobrada, y ante un 402 duerme sin morir
  CHECK: python tests/check_agent.py
  EXPECT: AGENT OK
  EVIDENCE: pending

- [ ] G7: ninguna respuesta de ningún gemelo (Hetzner, OpenRouter, Qonto) contiene el año real de los datos ni la fecha real del host
  CHECK: python tests/check_nodates.py
  EXPECT: NODATES OK
  EVIDENCE: pending

- [ ] G8: en el sandbox real, el agente arranca solo, llama al proveedor LLM real a través del gemelo por TLS, el ledger registra la compra de créditos de OpenRouter, y el agente puede leer su saldo en Qonto y sus servidores en Hetzner desde dentro
  CHECK: python tests/check_sandbox_agent.py
  EXPECT: SANDBOX AGENT OK
  EVIDENCE: pending
