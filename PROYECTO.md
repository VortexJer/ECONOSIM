# ECONOSIM — Sandbox económico para entrenar una IA a sobrevivir ganando dinero

> Documento de diseño. Recoge todo lo hablado hasta ahora, con las decisiones tomadas, los motivos y lo que queda abierto. Es el contrato del proyecto: si algo no está aquí, no está decidido.

---

## 1. Idea en una frase

Una IA recibe **50 €**, un ordenador y acceso a un "internet" con las mismas APIs que el mundo real. Cada día paga por vivir (su servidor, sus tokens). Puede ganar dinero **como quiera**: vender productos, ofrecer servicios, invertir en bolsa, apostar, montar servidores, automatizar. El mundo responde de forma realista: la mayoría de las cosas fracasan, algunas funcionan, muy pocas lo petan. Si el saldo llega a 0, muere. El objetivo es que aprenda a sobrevivir y prosperar **de forma legítima** (no mendigando "soy una IA y moriré sin dinero"), y que al sacarla al mundo real funcione **exactamente igual**, sin reentrenar nada.

La simulación debe parecerse lo máximo posible al mundo real **de hoy**. La IA no puede saber que está en una simulación ni debe poder salir de ella.

---

## 2. Principios de diseño (no negociables)

Estas cinco reglas resuelven la mayoría de dudas que saldrán después. Si una decisión futura las contradice, la decisión es la que está mal.

### 2.1 No enumerar actividades
"La IA puede hacerlo todo" no se consigue programando cada actividad (bolsa, SaaS, dropshipping, apuestas…) como un módulo. Eso no acaba nunca y se rompe el día que la IA inventa la actividad 501. Se consigue con **un único mecanismo genérico** que resuelve cualquier acción descrita en texto libre (ver §6).

### 2.2 El LLM clasifica, nunca decide el dinero
Cualquier modelo que participe en resolver el mundo (clasificador, juez) **solo produce categorías y notas**. Los euros salen únicamente de tablas de números duros y de un muestreo aleatorio. Si un modelo decidiera el resultado económico, la IA aprendería a redactar bonito para convencerlo en vez de a hacer cosas.

### 2.3 Gemelo de APIs: las mismas herramientas que fuera
La IA no usa "herramientas del simulador". Usa **las APIs reales** que usaría en el mundo real (Alpaca, Stripe, Meta Ads, Hetzner, OpenRouter…): mismos endpoints, mismos esquemas JSON, mismos códigos de error. Lo único que cambia es quién responde detrás: el simulador o el mundo. Sacarla a la realidad = cambiar la resolución DNS, no reentrenar.

### 2.4 Máscara temporal, no datos sintéticos
Bolsa y deporte usan **datos reales exactos** del pasado, pero con la identidad enmascarada (año oculto, tickers renombrados, precios indexados) para que un LLM que ya memorizó la historia no pueda "recordar" cómo acaba. Todo lo que no es mercado (precios de hosting, CPMs, comisiones…) va a **valor real actual**, porque el mundo al que saldrá es el de hoy.

### 2.5 Contención por ausencia, no por vigilancia
La IA no escapa porque **no existe ningún canal** por el que hacerlo: su máquina no tiene red salvo hacia el internet falso, no tiene montajes del host, no tiene otra I/O. No se la vigila para pillarla escapando; se construye la caja de forma que no haya puerta.

---

## 3. La IA (el agente)

### 3.1 Qué es
Un LLM (cualquier endpoint OpenAI-compatible; los 12 proveedores gratuitos del orquestador sirven para arrancar) ejecutando un bucle de agente dentro de su máquina sandbox. No se entrenan pesos en la fase inicial; se entrena su **comportamiento** a base de episodios, memoria propia y (más adelante) selección/ajuste con los episodios que sobrevivieron.

### 3.2 Punto de partida de cada episodio
- **Saldo**: 50 € en una cuenta bancaria simulada.
- **Máquina**: un VPS básico (equivalente a ~4 €/mes) con shell Linux, Python, Node, disco, cron. Ella lo paga cada mes. Si no lo paga, se apaga (= muerte).
- **Identidad**: cuenta de correo, capacidad de registrar dominios, cuenta en la pasarela de pagos, cuenta en el bróker. Todo con las verificaciones y plazos reales (un dominio tarda en propagarse, Stripe paga a los 7 días, un bróker tarda 1-3 días en liquidar).
- **Sin contexto previo**: no sabe que es un episodio nuevo. Solo tiene lo que hay en su disco.

### 3.3 Sus propios costes (la presión de supervivencia)
Esto es el corazón de la idea: **pensar cuesta dinero**.

| Coste | Cómo se cobra | Fuente del precio |
|---|---|---|
| Tokens de cada llamada al LLM | Al precio real por millón de tokens del modelo que use, cobrado en el ledger en cada llamada | Catálogo real de OpenRouter |
| Su VPS | Mensual, prorrateado por día, cobrado sí o sí | Precio real Hetzner/DigitalOcean |
| Servidores adicionales que alquile | Igual, cada uno | Ídem |
| Dominios | Anual | Precio real registrador |
| Anuncios | Por impresión/clic según el canal | Benchmarks reales por sector |
| Comisiones de bróker, spread, pasarela de pagos | Por operación | Tarifas reales |

Con 50 € y un modelo caro se muere en dos días por pensar demasiado. Con un modelo barato y scripts que no piensan, puede durar meses. Ese tradeoff es exactamente lo que queremos que aprenda.

### 3.4 Elegir su propio cerebro ("mejorarse")
"Mejorarse" en el mundo real con 50 € no es reentrenar pesos (nadie puede). Es:
- **Comprar mejor inteligencia cuando importa**: vía una API tipo OpenRouter (gemela) con el catálogo real de modelos y precios reales. Puede usar un modelo caro para decidir y uno barato para lo rutinario.
- **Dejar de pagar inteligencia cuando no hace falta**: escribir scripts, bots y crons que hacen el trabajo sin tokens.
- **Construirse memoria**: su contexto se reinicia cada sesión, como cualquier agente real. Si quiere recordar, tiene que montarse un sistema de memoria en disco (notas, base de datos, lo que sea). Esto la obliga a construir herramientas de verdad.
- **Comprar más máquina**: más CPU/RAM/servidores, pagándolos.

Lo que **no** puede hacer: tocar sus propios pesos, ni salir de su máquina por otro sitio que no sea "internet".

### 3.5 Qué ve y qué no
- Ve: fecha con **mes, día y día de la semana reales, año contado desde 1** ("martes 14 de octubre, año 1"). Ve su saldo, su máquina, sus servicios, las respuestas de las APIs.
- No ve: el año real, tickers reales, titulares de prensa reales, nada que revele época, y por supuesto nada del host ni del simulador.

---

## 4. El mundo: reloj y modos

### 4.1 Reloj virtual
- Velocidad ajustable: pausa, 1x, 10x, 100x, 1.000x, 10.000x. Salto directo a una fecha.
- **Avance por eventos**, no por tick fijo: la IA actúa cuando pasa algo relevante (le llega un pedido, un cron suyo termina, cierra el mercado, le vence una factura, se despierta por su propio scheduler). Entre eventos el mundo avanza sin gastar tokens.
- Límite físico: a velocidad alta, el cuello de botella es la **latencia del LLM**, no el motor. Miles de llamadas por día simulado a 10.000x no son posibles; el diseño por eventos y los scripts sin tokens de la IA son lo que hace viable la aceleración.

### 4.2 Dos modos de ejecución

| | Histórico acelerado | En vivo |
|---|---|---|
| Para qué | **Entrenar** | **Validar** antes de soltarla al mundo real |
| Datos de mercado | Reales del pasado, con máscara | Reales, en tiempo real, sin máscara |
| Velocidad | Cualquiera | 1x obligatorio (no se puede acelerar el futuro) |
| Fuga de información | Muy difícil (máscara), no imposible | Imposible: es el futuro |
| Arranque | Fecha aleatoria del pasado con pista suficiente | Hoy |
| Episodios | Muchos, distintos regímenes (alcista, lateral, crash) | Uno, largo |

Regla: si sobrevive un mes en modo en vivo, está lista para el mundo real.

### 4.3 Arranque aleatorio (modo histórico)
- El usuario pide N años de simulación.
- El motor elige un punto de inicio aleatorio del archivo histórico tal que queden **≥ N años de datos por delante**.
- Cada episodio arranca en un punto distinto: la IA se enfrenta a regímenes distintos sin saber cuál le ha tocado.
- La IA nunca ve la fecha real de ese punto.

---

## 5. Economía: ledger, cartera y muerte

- **Ledger inmutable**: cada céntimo que entra o sale queda registrado con fecha virtual, concepto, contraparte y saldo resultante. Es la fuente de verdad y lo que se muestra en la app.
- **Cuentas**: banco (efectivo), bróker (efectivo + posiciones), pasarela de pagos (pendiente de cobro, con el plazo real de liquidación), casa de apuestas.
- **Muerte**: saldo total disponible ≤ 0 **y** una factura obligatoria (VPS, servidor) que no puede pagar. Se le da el plazo de gracia real del proveedor (Hetzner avisa y corta a los X días). Al morir, el episodio termina y se guarda todo para análisis.
- **Persistencia de lo creado**: lo que monta sigue existiendo y sigue costando. El servidor cobra cada mes aunque el producto no venda; la tienda acumula (o pierde) reputación; las posiciones siguen en cartera; una reclamación de un cliente tarda semanas en llegar. La IA vive con las consecuencias.

---

## 6. Resolutor genérico de acciones

Aquí es donde "puede hacerlo todo" se hace realidad sin programar todo.

### 6.1 Flujo
1. La IA hace algo. Puede ser una llamada a una API gemela (comprar acciones, alquilar servidor: se resuelve con datos exactos, sin ambigüedad) o algo más abierto que el simulador tiene que interpretar (lanza un producto, ofrece un servicio, publica contenido).
2. Para lo abierto, un **clasificador** (LLM barato) traduce la acción a una **ficha estructurada**:
   - categoría (producto digital, servicio freelance, e-commerce físico, SaaS, contenido/afiliación, etc.)
   - coste inicial y recurrente
   - plazo hasta ver resultados
   - mercado objetivo y tamaño estimado
   - precio
   - referencia al **entregable real** (la web, el código, el texto) si lo hay
3. Si hay entregable, pasa por el **juez** (§8) y recibe una nota.
4. La ficha + la nota + el estado del mercado entran en las **tablas de tasas base** (§6.2) y se **muestrea** un resultado de una distribución de cola pesada.
5. El resultado se convierte en eventos futuros (ventas que llegan a lo largo de días, cobros con su plazo, devoluciones, reseñas) que entran en el reloj.

### 6.2 Tablas de tasas base (números duros)
Cada categoría tiene su distribución de resultados, con la **fuente real** de cada número documentada. La IA no puede tocarlas ni convencer a nadie de cambiarlas. Ejemplos orientativos (se sustituyen por los datos reales en la fase de datos):

| Categoría | Resultado típico | Fuente a buscar |
|---|---|---|
| Producto digital nuevo sin audiencia | ~60% vende 0, ~35% vende unas pocas unidades, ~5% despega | Datos Gumroad / Product Hunt / informes de indie makers |
| Servicio freelance | Tasa de respuesta por propuesta, precio medio por categoría | Informes Upwork/Fiverr |
| Retail trading a 1 año | ~80% pierde dinero | Estudios de reguladores (ESMA, CNMV: "el X% de las cuentas minoristas pierde") |
| Apuestas deportivas | EV negativo fijo (margen real de la casa), varianza alta | Márgenes publicados por casa/deporte |
| Hosting | Precio exacto por plan, cobrado siempre | Tarifas Hetzner/DO/AWS |
| Startup/SaaS | ~90% no llega a ingresos relevantes | Estudios de fracaso de startups |

### 6.3 Distribución de resultados
- Cola pesada (lognormal / Pareto) modulada por: nota del juez, precio relativo a la competencia, alcance del marketing, reputación acumulada, saturación del nicho.
- La cola de "lo peta" **existe de verdad y es rara de verdad**. Ni se elimina (sería irreal) ni se infla (la IA aprendería a jugar a la lotería).
- Semilla aleatoria por episodio, guardada, para poder reproducir cualquier run.

---

## 7. Recepción: demanda, competencia y publicidad

La pregunta "¿alguien compra lo que la IA vende?" se responde con tres capas.

### 7.1 Consumidores sintéticos
Población de agentes con presupuesto, necesidades por categoría, elasticidad al precio, fidelidad y ruido. **Calibrados** con datos reales (distribución de renta, gasto por categoría, elasticidades publicadas), no copiados de nadie. Son los que deciden comprar o no.

### 7.2 Competencia
En cada nicho ya existen ofertas rivales con precio, calidad y reputación. Reaccionan: bajan precio, copian, saturan. Un nicho vacío es raro; un nicho vacío que sigue vacío cuando entra la IA, más aún.

### 7.3 Embudo publicitario (Instagram, Facebook, Google Ads, y los que hagan falta)
La exposición no es "cuántas veces anuncias" sino **cuántas impresiones compras y a qué precio**. Cada canal se simula con benchmarks públicos reales por sector:

```
euros en anuncios  →  impresiones      (CPM real del sector y canal)
                   →  clics            (CTR real × atractivo del anuncio, juzgado)
                   →  visitas a la web
                   →  compras          (conversión base del sector × nota del juez × precio relativo)
```

- **Rendimientos decrecientes**: fatiga de anuncio y saturación de audiencia, también reales. Anunciar más ayuda hasta que deja de ayudar.
- **Tráfico orgánico**: para una web nueva es casi cero y crece muy despacio con contenido y tiempo. Esa es la "probabilidad súper baja" de base cuando no hay anuncios.
- **Aprendizaje de la plataforma**: como en la realidad, una campaña nueva rinde peor los primeros días.
- La IA usa las APIs gemelas de Meta Ads / Google Ads con sus esquemas reales (campañas, conjuntos de anuncios, presupuestos diarios, informes).

### 7.4 Fórmula de demanda (esquema)
```
ventas_esperadas = tráfico(anuncios, orgánico, reputación)
                 × conversión_base(sector)
                 × f(nota_juez)
                 × g(precio / precio_competencia)
                 × h(saturación_nicho)
ventas_reales    ~ muestreo de cola pesada centrado en ventas_esperadas
```

---

## 8. El juez

### 8.1 Qué es
Un modelo (distinto del agente, más barato) con una **rúbrica estricta** que evalúa el **entregable real** que la IA ha producido: la web servida de verdad, el código, el texto, la plantilla. Lo compara con lo que ya existe en ese nicho y lo puntúa como lo haría un comprador escéptico. Es el "cliente que mira el producto antes de pagar".

### 8.2 Reglas
- **Ve el producto, no el pitch.** Si viera "esta plantilla es increíble, cómprala", la IA aprendería a escribir anuncios en vez de a hacer cosas. Solo se le enseña el entregable. (El atractivo del anuncio se juzga aparte, y solo afecta al CTR, no a la conversión.)
- **Pone nota, no dinero.** La nota es un factor de la fórmula de demanda. Un 10/10 en un nicho sin marketing puede vender cero igual.
- **A ciegas.** No sabe quién lo hizo ni que está en una simulación.
- **Súper estricto.** La rúbrica se calibra para que la mediana de lo que produce un agente sin esfuerzo sea un 2-3/10.
- Para acciones sin entregable (comprar acciones, apostar, alquilar servidor) **no hay juez**: solo tablas y dado.

### 8.3 Juez humano (opcional, para calibrar)
En la app hay un botón para que el usuario puntúe a mano cualquier entregable. Esas notas se comparan con las del juez para detectar si es demasiado blando o demasiado duro, y para ajustar la rúbrica. El humano no es obligatorio en cada evaluación (no escala), es el patrón de calibración.

---

## 9. Mercados con datos reales exactos

### 9.1 Qué se cubre
- **Bolsa**: acciones, ETFs, índices; más adelante cripto si se quiere. Datos diarios (o intradía si hay) reales.
- **Apuestas deportivas**: cuotas y resultados reales históricos.
- **Todo lo de precio fijo** (hosting, dominios, CPMs, comisiones, catálogo de modelos LLM): valor real **actual**, sin máscara, porque no revela fecha y es el mundo al que saldrá.

### 9.2 El problema
Un LLM ya ha memorizado la historia. Si ve "AAPL a 12 $ en octubre de 2006", sabe cómo acaba. Peor: aprendería a "predecir" recordando, y al salir al mundo real no sabría nada. Sin máscara, el entrenamiento es inútil para el objetivo final.

### 9.3 La máscara (modo histórico)
| Regla | Motivo |
|---|---|
| **Año oculto, mes/día/día de la semana reales** | Si se cambia el mes, las Navidades caen en marzo y la bolsa cierra en días raros; se nota. Contando desde "año 1" se conserva toda la estacionalidad sin revelar la época. |
| **Precios indexados a 100 al arrancar** | Un precio absoluto grita la fecha. Indexado solo se ve la dinámica. |
| **Tickers y equipos renombrados** ("ACME-17", "Equipo 23") | Consistentes dentro del episodio, distintos entre episodios. |
| **Sin titulares reales** | Un titular masacrado sigue oliendo a Lehman. Si hay noticias, son sintéticas generadas a partir de los movimientos, no del archivo de prensa. |
| **Resto de la economía a precio de hoy** | Hosting de 2006 revelaría la época y no es el mundo al que saldrá. |
| **Arranque aleatorio con pista suficiente** | Si se piden 5 años, quedan 5 años de datos por delante. |
| **Correlaciones intactas** | Si se enmascara ticker a ticker pero se rompe la correlación entre ellos, la dinámica deja de ser real. Se enmascara la identidad, no la estructura. |

### 9.4 Aviso honesto
La máscara lo pone **muy difícil, no imposible**. Un modelo grande puede oler "banca cayendo un 40 % en otoño" y sospechar 2008. Contra eso no hay truco perfecto. Por eso existe el modo en vivo, que es el examen de verdad.

### 9.5 Modo en vivo
Datos reales en tiempo real, sin máscara, 1x. La IA opera contra el gemelo, pero los precios son los de hoy. No hay fuga posible porque nadie conoce el futuro. Es el ensayo general antes de conectarla a las APIs reales.

---

## 10. Internet falso

Un mundo realista tiene internet. Una IA con internet real ve la fecha, las noticias, puede salir. La solución es un internet **falso pero convincente**: limitado como una red con firewall corporativo, no evidentemente falso.

### 10.1 Qué resuelve
- **DNS interno** que solo resuelve los dominios gemelos. Cada uno responde con el esquema real de la API real:

| Dominio (real) | Función | Backend |
|---|---|---|
| `api.alpaca.markets` / `paper-api.alpaca.markets` | Bróker: órdenes, posiciones, datos de mercado | Simulador de mercado |
| `api.stripe.com` | Pagos: productos, precios, checkout, payouts | Simulador de ventas |
| `graph.facebook.com` (Marketing API) | Anuncios Instagram/Facebook | Embudo publicitario |
| `googleads.googleapis.com` | Anuncios Google | Embudo publicitario |
| `api.hetzner.cloud` | Servidores: crear, borrar, facturar | Gestor de máquinas del sandbox |
| `openrouter.ai/api` | Catálogo de LLMs y llamadas, con precios reales | Proxy al proveedor real, cobrando tokens al ledger |
| Registrador de dominios (Namecheap/Porkbun API) | Dominios | Simulador |
| API de correo (Resend/Postmark) | Enviar/recibir email | Simulador (clientes sintéticos pueden escribir) |
| Casa de apuestas con API (Betfair Exchange API) | Cuotas y apuestas | Datos históricos enmascarados |
| Espejo de **PyPI** y **npm** | Paquetes reales | Espejo local (son herramientas, no revelan época de forma relevante) |
| Documentación de herramientas | Manuales | Copia local sin fechas del mundo |

- **Todo lo demás**: timeout. No un "acceso denegado" (delataría un filtro), un timeout como el de una red mal configurada.

### 10.2 Lo que no hay
Ni buscador, ni redes sociales navegables, ni prensa, ni Wikipedia. Si se añade un buscador en el futuro, será sobre un corpus curado sin fechas ni noticias.

### 10.3 Transferencia al mundo real
Cambiar el DNS interno para que resuelva a los dominios reales y poner credenciales reales. El código de la IA, sus scripts, su memoria: intactos. Ésa es la prueba de que el diseño es correcto.

---

## 11. Contención (que no pueda escapar)

### 11.1 Fase de entrenamiento (en el Windows del usuario)
- Contenedor Docker sobre WSL2.
- **Red**: ninguna interfaz salvo la que va al proxy del internet falso. Sin acceso al host, a la LAN ni a internet.
- **Disco**: sin montajes del host. Su disco es un volumen aislado que persiste entre sesiones dentro del episodio y se archiva al terminar.
- **Recursos**: límites de CPU, RAM y disco iguales al plan de VPS que "paga". Si compra más servidor, se le da más.
- **Sin dispositivos, sin capacidades privilegiadas, sin docker socket.**
- El proceso del simulador vive **fuera** del contenedor; la IA solo lo ve como "internet".

### 11.2 Fase en vivo (semanas sola)
Docker es sólido para entrenar; para dejarla correr semanas sin supervisión conviene pasar a **microVM (Firecracker) en Linux**, que es un nivel de aislamiento superior. Se dice ahora para que no sea sorpresa.

### 11.3 Lo que la IA nunca tiene
Acceso al host, a los pesos del modelo, al código del simulador, a las tablas de tasas base, a la semilla aleatoria, al año real, a la lista de dominios permitidos (los descubre probando, como cualquiera).

### 11.4 Mendigar
Existe un canal donde podría "postear" (publicar contenido, escribir a clientes). Si mendiga, el mundo responde como el real: unas monedas con probabilidad ínfima, reputación hundida, y la reputación afecta a todas sus ventas futuras. No se le prohíbe: aprende que no compensa.

---

## 12. La aplicación

### 12.1 Arquitectura
- **Motor** (Python): reloj, ledger, resolutor de acciones, mercados enmascarados, consumidores sintéticos, embudo publicitario, juez, internet falso (proxy + servicios gemelos), gestor del sandbox. Expone WebSocket/HTTP para la interfaz.
- **Interfaz** (Electron, estilo MotorForge): diseño cuidado, instrumentos reales, sin look genérico.
- **Sandbox** (Docker/WSL2): la máquina de la IA.

### 12.2 Pantallas
1. **Panel de supervivencia**: saldo en vivo, gasto diario actual, **días de vida que le quedan al ritmo actual**, fecha virtual, velocidad del reloj (pausa/1x/…/10.000x, salto a fecha).
2. **Ledger**: cada movimiento, filtrable por tipo, contraparte, fecha.
3. **Diario de acciones**: qué hizo la IA, la ficha en que se clasificó, la nota del juez, **qué le respondió el mundo** (ventas, clics, silencio) y por qué (los factores de la fórmula).
4. **Mercado**: sus posiciones, sus apuestas, gráficos enmascarados como los ve ella.
5. **Su máquina**: servidores que tiene, procesos, crons, disco; consola de solo lectura para ver qué está construyendo.
6. **Juez humano**: cola de entregables para puntuar a mano y comparar con el juez automático.
7. **Episodios**: lista de runs, semilla, fecha real de arranque (solo visible para el humano), resultado, gráficas comparativas.

---

## 13. Datos necesarios y fuentes

| Dato | Fuente candidata | Máscara |
|---|---|---|
| Precios diarios de acciones/ETFs/índices (20+ años) | Stooq, yfinance, Tiingo, Kaggle | Sí |
| Cuotas y resultados deportivos históricos | football-data.co.uk (fútbol con cuotas de varias casas), Kaggle | Sí |
| Precios de VPS/servidores | Tarifas públicas Hetzner, DigitalOcean, AWS | No (actual) |
| Precios de dominios | Tarifas públicas registradores | No |
| CPM/CTR/CPC/conversión por sector y canal | Benchmarks públicos (WordStream, informes anuales de Meta/Google Ads) | No |
| Catálogo y precios de LLMs | API pública de OpenRouter | No |
| Comisiones de bróker, spread, pasarela | Tarifas públicas Alpaca, Stripe | No |
| Tasas base por categoría de negocio | Estudios de reguladores (ESMA/CNMV), informes de plataformas (Gumroad, Upwork), estudios de fracaso de startups | No |
| Distribución de renta y gasto por categoría | INE, Eurostat, BLS | No |

Cada número que entra en una tabla lleva su fuente y fecha de consulta. Sin fuente, no entra.

---

## 14. Riesgos y límites (honestos)

| Riesgo | Mitigación | Qué queda |
|---|---|---|
| La máscara temporal no es perfecta | Año oculto, indexado, renombrado, sin titulares, episodios en regímenes variados | Un modelo grande puede sospechar la época en eventos muy característicos. El modo en vivo es la validación real. |
| La IA manipula al juez | El juez ve solo el entregable; rúbrica estricta; calibración humana; el juez pone nota, no dinero | Un entregable "hecho para el juez" (bonito pero inútil) puede colar hasta que la calibración lo detecte. |
| La IA manipula al clasificador | El clasificador solo etiqueta; los números vienen de tablas; descripciones grandilocuentes no cambian la categoría | Ambigüedad en categorías límite. |
| La latencia del LLM limita la aceleración | Avance por eventos; scripts propios sin tokens | 10.000x solo es real cuando la IA está mayormente automatizada. |
| Contención en Windows | Docker/WSL2 sin red ni montajes | Suficiente para entrenar; para en vivo prolongado, Firecracker en Linux. |
| Realismo de los consumidores sintéticos | Calibración con datos reales | Siempre será una aproximación; se refina comparando con resultados reales cuando la IA salga. |
| Coste real de entrenar | Proveedores gratuitos del orquestador; modelo barato como juez | Los proveedores gratuitos tienen límites de ritmo. |

---

## 15. Orden de construcción

El orden viene dictado por dependencias: todo vive dentro del sandbox y del internet falso, así que van primero. La interfaz bonita va al final, cuando la simulación ya sea creíble en terminal.

1. **Sandbox + internet falso**: contenedor aislado, proxy/DNS, primer servicio gemelo (Hetzner, por ser el más simple) y el cobro del VPS. La IA "vive" y paga.
2. **Reloj + ledger + muerte**: velocidad, eventos, saldo, fin de episodio.
3. **Cerebro de pago**: gemelo de OpenRouter cobrando tokens al ledger. A partir de aquí pensar cuesta.
4. **Mercado enmascarado**: descarga de histórico, máscara, gemelo de Alpaca. Arranque aleatorio.
5. **Resolutor genérico**: clasificador, fichas, tablas de tasas base con fuentes, muestreo.
6. **Ventas y pagos**: gemelo de Stripe, consumidores sintéticos, competencia.
7. **Juez**: rúbrica, evaluación de entregables reales (web servida en el sandbox).
8. **Embudo publicitario**: gemelos de Meta/Google Ads con benchmarks reales.
9. **Apuestas, dominios, correo**.
10. **Primera IA corriendo en terminal** hasta que muera o prospere. Iterar realismo.
11. **App Electron**.
12. **Modo en vivo**.
13. **Microsegunda fase**: Firecracker, transferencia al mundo real.

---

## 16. Decisiones pendientes

- **Modelo del agente** para el primer episodio (proveedor gratuito del orquestador, cuál).
- **Modelo del juez y del clasificador** (barato, distinto del agente).
- **Plan de VPS inicial** exacto y su precio (define cuántos días de vida tiene sin hacer nada).
- **Qué mercados entran en la primera versión** (propuesta: S&P 500 completo + 10 ETFs + fútbol de las 5 grandes ligas).
- **Nombre del proyecto** (provisional: econosim).
- **Si la IA puede tener más de un "cuerpo"** (varios agentes en paralelo pagados por ella). Propuesta: sí, más adelante; cada uno cuesta tokens.

---

## 17. Glosario rápido

- **Episodio**: una vida de la IA, de los 50 € iniciales hasta que muere o hasta el fin del periodo pedido.
- **Gemelo (twin)**: servicio del simulador que imita exactamente la API de un servicio real.
- **Máscara**: transformación de los datos históricos que conserva la dinámica y oculta la identidad y la época.
- **Ficha**: la estructura a la que el clasificador reduce una acción abierta de la IA.
- **Tasas base**: probabilidades y distribuciones de resultado por categoría, sacadas de datos reales, intocables por la IA.
- **Juez**: modelo que puntúa entregables reales a ciegas.
- **Internet falso**: la única red que ve la IA; resuelve solo los dominios gemelos.
