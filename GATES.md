# Gates: fase 12 — modo en vivo (datos reales entran, ninguna acción sale)

OWNS: econosim/**, panel/**, tests/**, PLAN.md, GATES.md, README.md

Scope: el ensayo general antes de la fase posterior (fuera de este proyecto) que apuntaría el DNS a las APIs reales. En vivo el mundo deja de enmascararse: el mercado sirve símbolos, precios y fechas REALES, y el reloj no se desplaza. A cambio se cierra un candado de egreso: toda acción con efecto externo (colocar una orden, apostar, enviar correo, registrar un dominio, aprovisionar un servidor, crear producto/cobro, gastar en anuncios) se acepta de forma benigna pero NO muta el mundo y queda anotada en un diario de egreso. Las lecturas pasan siempre. El panel avisa con un banner "EN VIVO" y muestra el diario de egreso. Fuera del modo en vivo todo queda exactamente como antes (candado inerte).

- [ ] G0: este ledger declara resultados que pueden fallar
  CHECK: node ~/.claude/skills/unlazy/scripts/gate-lint.mjs GATES.md
  EXPECT: LINT OK

- [ ] G1: en vivo entran datos REALES (símbolos, precios y fecha sin máscara ni desfase) y NINGUNA acción sale — orden, apuesta, correo, dominio, servidor, producto/sesión y anuncio se aceptan benignos sin mutar el mundo y quedan en el diario de egreso; con el modo apagado el candado es inerte (una compra real mueve el saldo)
  CHECK: python tests/check_live_mode.py
  EXPECT: LIVE MODE OK

- [ ] G2: el /dashboard expone el bloque del modo en vivo (activo, contador de bloqueos y diario de egreso con servicio/operación/detalle) coherente con el mundo; fuera de vivo lo marca apagado
  CHECK: python tests/check_live_dashboard.py
  EXPECT: LIVE DASHBOARD OK

- [ ] G3: el panel pinta el banner EN VIVO (con contador y aviso de que nada sale) y la tarjeta del diario de egreso con cada acción bloqueada; fuera de vivo no aparece ninguno de los dos
  CHECK: node panel/test/live.test.mjs
  EXPECT: LIVE PANEL OK
