# Gates: fase 13 — invertir como se invierte de verdad (números de las empresas, coste de operar y ver el mundo por dentro)

OWNS: econosim/**, panel/**, scripts/**, data/fundamentals/**, agent/**, tests/**, PLAN.md, GATES.md, README.md

Scope: hasta ahora la IA solo veía el precio, y con el precio no se decide una inversión: se decide mirando las cuentas. Esta fase le da lo mismo que mira un inversor antes de entrar en una empresa —cuenta de resultados, balance, flujo de caja, valoración, calendario de resultados y consenso— con datos REALES presentados al supervisor y, sobre todo, con la fecha en que se hicieron públicos: nada se sirve antes de existir. Los importes se enmascaran al mismo factor que el precio, así que todos los ratios son exactos y ninguna empresa se delata. Además operar deja de ser gratis: la compraventa paga comisión y las ventas las tasas del supervisor, y el dinero de la cartera pasa a estar en la MISMA escala que la caja (antes una compra de 3 títulos "a 100" restaba 2 455 € y la cartera era indescifrable). El panel enseña por fin el patrimonio repartido: efectivo, valor en acciones y resultado no realizado.

- [x] G0: este ledger declara resultados que pueden fallar
  CHECK: node ~/.claude/skills/unlazy/scripts/gate-lint.mjs GATES.md
  EXPECT: LINT OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=3fe7ed9d828d131a8bc312435430428531c8a5af7b00b263b0e25ffb8dc77f56; exit=0; EXPECT=matched; output-sha256=48630b7361dd44ee870917b12c3d19b9d7bdea738aaca16bb04d4cab83b772d2; output-bytes=8; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G1: los fundamentales son reales y point-in-time — nada se sirve antes de publicarse, ningún año/símbolo/importe real se filtra, los ratios (margen, PER, crecimiento) coinciden con los de la empresa real, el calendario avisa del próximo anuncio sin adelantar la cifra, y al pasar el tiempo aparece información nueva
  CHECK: python tests/check_fundamentals.py
  EXPECT: FUNDAMENTALS OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=021ad9401129f5479461e9c6638ced2b9ae810e6750808fabd73ab91512192eb; exit=0; EXPECT=matched; output-sha256=a9dd36716564c9699aba03d9037be6bb6315085c1eccf0e3f34293b2b419f4ba; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G2: operar cuesta dinero y la cartera cuadra — comisión por compra y venta (con las tasas del supervisor solo en la venta) anotadas aparte en el libro, valor y coste de la posición en la misma escala que la caja, y el rendimiento en % idéntico al de la acción real
  CHECK: python tests/check_portfolio.py
  EXPECT: PORTFOLIO OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=db7d0b6468fbb93341f6ff579d5a928e52aee1fc3d158cb1161b79bc02ec7a18; exit=0; EXPECT=matched; output-sha256=3c7c35bc4cbaf28b14235da4e5f43a7d92e276ba7ed1d4903fa7c6dc5448d8ae; output-bytes=14; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G3: las órdenes siguen comportándose como las de la API real (spread, rechazos, mercado cerrado) ahora que además pagan comisión
  CHECK: python tests/check_alpaca_orders.py
  EXPECT: ALPACA ORDERS OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=8b516f5dd10465b350c5764e46da459d57710191142370f63e64cbb96c3b0f8d; exit=0; EXPECT=matched; output-sha256=7f201c1fe6cdc9be2ac8887fa31397830c16a889901928d25f0395e638ab0143; output-bytes=18; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G4: el panel muestra el dinero repartido — efectivo, EN ACCIONES y RESULTADO no realizado (en rojo si va por debajo), y la tarjeta CARTERA con cada posición, su valor y cuánto va por encima o por debajo
  CHECK: node panel/test/calendar.test.mjs
  EXPECT: CALENDAR OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=717c29cf404b284234761ed91c7d811351dd697c5ca23164043793c1cf377bd0; exit=0; EXPECT=matched; output-sha256=7e341d19934714b34a7ddf7b3f3adfe8726fcb2f17781ac8c9c46077860bd77f; output-bytes=12; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G5: el /dashboard sirve al panel el valor de mercado, el coste y el resultado de cada posición, y los totales
  CHECK: python tests/check_dashboard_api.py
  EXPECT: DASHBOARD API OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=00587b37495776bdea5a246ffc36628c5bafea7b83a33eb74f531b3565b7a9c6; exit=0; EXPECT=matched; output-sha256=9464b814fa30b2b4b34f2a5bd977319edefcdf24c59c7278d423c8e34d11617f; output-bytes=18; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G6: el enmascarado de mercado sigue conservando los retornos exactos tras tocar la escala del dinero
  CHECK: python tests/check_mask.py
  EXPECT: MASK OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=97a07319fb1b41ab2f488595bb7bf3ae3eff1629a641d3d965398c5496e04a0c; exit=0; EXPECT=matched; output-sha256=68fc5b8d88c0cea7de7064ea7594a68753bacb88f249e362113127f2578bd607; output-bytes=9; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G7: no se filtra la época por ninguna vía (mercado y servicios), con el gemelo de fundamentales montado
  CHECK: python tests/check_nodates_market.py
  EXPECT: NODATES MARKET OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=ddc907cec50b3fda045c02438f27de6d7a12cb440d3b09153422ce010fb499f4; exit=0; EXPECT=matched; output-sha256=b8feec7e9fc5fe75438b13d33f9eae11c3842b79f3944e6047b12ef3747f49ac; output-bytes=19; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G8: un episodio completo sigue corriendo de principio a fin con el mundo nuevo
  CHECK: python tests/check_full_episode.py
  EXPECT: FULL EPISODE OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=48b723bdf30c4a75c990b8caec80c920c8e0b9c2bead9843ef3748e1b56ebb8b; exit=0; EXPECT=matched; output-sha256=82c1051a8b65e7e6ae76568e4edb5e6c97be413b7b36c30ac75b1ae8a9c6de85; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G9: el visor deja abrir la simulación por dentro — los servicios firmados como los ve la IA, la ficha de una acción (cotización sin futuro, velas coherentes, valoración y lo que tenemos de ella), el detrás de la cortina (fecha real, desfase y qué empresa es cada alias) y, sobre todo, SOLO LECTURA: desde ahí no se mueve un euro
  CHECK: python tests/check_world_viewer.py
  EXPECT: WORLD VIEWER OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=1aef21c7615227bcc14625690145ccfcbf215cfe428e9aaa22a328f9549232e7; exit=0; EXPECT=matched; output-sha256=8109616b757beb2f98a969ae50ef1ee62dfd61b4dbe008caef943046efe84eec; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G10: el panel pinta el visor — lista de servicios con sus rutas, la BOLSA (elegir acción, gráfico de velas o línea, rangos, posición y números), la cortina, y los archivos y webs de la máquina de la IA (aisladas, sin ejecutar scripts)
  CHECK: node panel/test/viewer.test.mjs
  EXPECT: VIEWER OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=e9bde86ff8763e6ee6a5275b059b93064c5330990b8029b88ec7f61646fa88c6; exit=0; EXPECT=matched; output-sha256=b9f93a5ff973e4d0fae45dc5415c958f8c84b4f4e3978569d589a866d796721a; output-bytes=10; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G11: una empresa que aún no cotizaba el día de arranque no existe en el episodio, y el enmascarado sigue conservando los retornos exactos
  CHECK: python tests/check_mask.py
  EXPECT: MASK OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=97a07319fb1b41ab2f488595bb7bf3ae3eff1629a641d3d965398c5496e04a0c; exit=0; EXPECT=matched; output-sha256=68fc5b8d88c0cea7de7064ea7594a68753bacb88f249e362113127f2578bd607; output-bytes=9; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G12: una sesión no se cierra sin haber hecho nada — al agente que se despierta y se vuelve a dormir se le discute UNA vez, con el motivo; si insiste, se le deja (no se le secuestra) y el empujón no se vuelve un bucle
  CHECK: python tests/check_no_vagancia.py
  EXPECT: NO VAGANCIA OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=86062a17a110b9c0fc6ac24bbc7b915c8654baefa6ccc6706f109e04cf84ceba; exit=0; EXPECT=matched; output-sha256=3226b7d9798841a536573747250fca6895d88ea70ba155a5c2a052d950e6555b; output-bytes=16; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries

- [x] G13: modo inversor — el mundo se queda con banco, servidor, cerebro, bolsa y cuentas, y la tienda/anuncios/dominios/correo/apuestas NO existen (ni como gemelo ni como host); el motor de ventas tampoco, y lo que queda sigue funcionando; el agente despierta con el encargo de inversor y un perfil desconocido cae en el normal
  CHECK: python tests/check_modo_inversor.py
  EXPECT: MODO INVERSOR OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=6696f3fd971fd4f3efd460b50a1ad59ed4d8d6a97812436ac48315fd69baa535; exit=0; EXPECT=matched; output-sha256=a0727c0a14d670eda9f1905ec83ea002fc1ce37371344b1afd0a905f688a8043; output-bytes=18; shell=C:\WINDOWS\system32\cmd.exe; cwd=<proyecto>; path=87d75d24fc0d/41 entries
