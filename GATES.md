# Gates: fase 13 — invertir como se invierte de verdad (números de las empresas + coste de operar)

OWNS: econosim/**, panel/**, scripts/**, data/fundamentals/**, agent/**, tests/**, PLAN.md, GATES.md, README.md

Scope: hasta ahora la IA solo veía el precio, y con el precio no se decide una inversión: se decide mirando las cuentas. Esta fase le da lo mismo que mira un inversor antes de entrar en una empresa —cuenta de resultados, balance, flujo de caja, valoración, calendario de resultados y consenso— con datos REALES presentados al supervisor y, sobre todo, con la fecha en que se hicieron públicos: nada se sirve antes de existir. Los importes se enmascaran al mismo factor que el precio, así que todos los ratios son exactos y ninguna empresa se delata. Además operar deja de ser gratis: la compraventa paga comisión y las ventas las tasas del supervisor, y el dinero de la cartera pasa a estar en la MISMA escala que la caja (antes una compra de 3 títulos "a 100" restaba 2 455 € y la cartera era indescifrable). El panel enseña por fin el patrimonio repartido: efectivo, valor en acciones y resultado no realizado.

- [ ] G0: este ledger declara resultados que pueden fallar
  CHECK: node ~/.claude/skills/unlazy/scripts/gate-lint.mjs GATES.md
  EXPECT: LINT OK

- [ ] G1: los fundamentales son reales y point-in-time — nada se sirve antes de publicarse, ningún año/símbolo/importe real se filtra, los ratios (margen, PER, crecimiento) coinciden con los de la empresa real, el calendario avisa del próximo anuncio sin adelantar la cifra, y al pasar el tiempo aparece información nueva
  CHECK: python tests/check_fundamentals.py
  EXPECT: FUNDAMENTALS OK

- [ ] G2: operar cuesta dinero y la cartera cuadra — comisión por compra y venta (con las tasas del supervisor solo en la venta) anotadas aparte en el libro, valor y coste de la posición en la misma escala que la caja, y el rendimiento en % idéntico al de la acción real
  CHECK: python tests/check_portfolio.py
  EXPECT: PORTFOLIO OK

- [ ] G3: las órdenes siguen comportándose como las de la API real (spread, rechazos, mercado cerrado) ahora que además pagan comisión
  CHECK: python tests/check_alpaca_orders.py
  EXPECT: ALPACA ORDERS OK

- [ ] G4: el panel muestra el dinero repartido — efectivo, EN ACCIONES y RESULTADO no realizado (en rojo si va por debajo), y la tarjeta CARTERA con cada posición, su valor y cuánto va por encima o por debajo
  CHECK: node panel/test/calendar.test.mjs
  EXPECT: CALENDAR OK

- [ ] G5: el /dashboard sirve al panel el valor de mercado, el coste y el resultado de cada posición, y los totales
  CHECK: python tests/check_dashboard_api.py
  EXPECT: DASHBOARD API OK

- [ ] G6: el enmascarado de mercado sigue conservando los retornos exactos tras tocar la escala del dinero
  CHECK: python tests/check_mask.py
  EXPECT: MASK OK

- [ ] G7: no se filtra la época por ninguna vía (mercado y servicios), con el gemelo de fundamentales montado
  CHECK: python tests/check_nodates_market.py
  EXPECT: NODATES MARKET OK

- [ ] G8: un episodio completo sigue corriendo de principio a fin con el mundo nuevo
  CHECK: python tests/check_full_episode.py
  EXPECT: FULL EPISODE OK
