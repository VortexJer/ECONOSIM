# Resultados del entrenamiento por generaciones (25/09/2026)

Cada ejecución vive en `training/laya_runs/<nombre>/` con su `log.txt` (una línea por generación),
`progress.json` (parámetros y campeonas), `gens.jsonl`, las vidas de la campeona (`lives.jsonl`: cada
compra y venta) y la campeona (`champion_mlp.pt`). La caché de estados (`training/laya_cache/`) no se
sube: se regenera sola.

**Cómo leer las cifras.** Vidas de 6 meses que empiezan con 50 € y pagan el servidor (~6 €/mes), así
que casi todo acaba por debajo de 50 €. Lo que se compara es contra las referencias en las MISMAS vidas:
quedarse en efectivo (13,62 € val / 13,17 € test) y todo al índice (15,59 € / 15,31 €).
Validación = 2019-2021 (elige la campeona); test = 2022-2025 (no decide nada).

## 1. Laya supervisado (`laya_generations.py`) — descartado

| Ejecución | Qué pasó |
|---|---|
| `20260925-0808` | El mandato del 50 % no compraba: Laya sin entrenar vota "vender/fuera" a todo y eso bloqueaba el mandato. Arreglado y relanzado. |
| `20260925-0815` | Relanzada para guardar cada vida (`lives.jsonl`). |
| `20260925-0820` | Parada por el usuario: "esto no es entrenar por generaciones". Tenía razón: una candidata por generación y etiquetas retrospectivas. |

## 2. Estrategias evolutivas (`laya_es.py`, OpenAI ES: población 30 en espejo, rangos, Adam)

Simulador rápido **idéntico al mundo real al céntimo** (comprobado en cada campeona: mismas operaciones,
mismo saldo), 8 ms por vida frente a ~1 s.

| Ejecución | Cerebro · parámetros | Resultado | Lección |
|---|---|---|---|
| `es-…0829`, `num-…0832` | — | Descartadas: rejilla sin alinear (caché 5× más grande). | Arranques en la rejilla de 5 sesiones. |
| `es-…0834` | Laya · sigma 0,05 | Las 30 variantes daban lo mismo (13,45 €), 0 % de compras propias. | El ruido no movía ninguna decisión. |
| `es-…0859` | Laya · sigma 0,5 | Campeona gen 0 · val 13,92 € · test 15,41 €, 0 % compras propias. | **Laya descartada**: sesgo "fuera" que la evolución no mueve. |
| `num-…0834` | Red numérica 38→64→3 · mandato 50 % | Campeona gen 156 · val 19,03 € · test 19,34 €. | Gana 30/30 a sus notas barajadas, pero IC sin mercado ≈ 0: solo aprendió **beta** (comprar lo más arriesgado en años alcistas). Sobreajusta desde gen ~160. |
| `num-ic-…0842` | + premio por IC sin mercado ×30 | Gen 36 · val 17,38 € · test 20,33 €. | El premio no generaliza (IC val → 0). |
| `num-crisis-…0848` | + entrena desde 2005 (crisis 2008), sin mandato | Gen 12 · val 15,79 € · test 19,50 €. | Igual: sin señal fuera de muestra. |
| `factor-…0859` | **C: inversor de factores** (16 pesos) | Gen 71 · val 18,86 € · test 14,86 €. | Primer cerebro con IC sin mercado > 0 (+0,05), estable, no memoriza. |
| `factor-crisis-…0907` | C + crisis 2008 | Gen 185 · val 19,16 € · test 14,21 €. | Vida a vida frente al índice: 10/24 val, 12/24 test → **moneda al aire**. |
| `factor-full-…0914` | C siempre invertido (mandato 95 %) | Gen 1 · val 17,14 € · test 15,20 €. | Estancado: le quita esperar. |
| `factor-u97-…0928`, `factor-u97-consist-…0933` | C con 97 empresas (· aptitud de consistencia) | Gen 0 · val 17,18 € · test 15,33 €. | Superadas por el walk-forward. |

## 3. Walk-forward caótico (`--walk-forward`)

Bloques de 6 meses 2005-2021 en orden aleatorio: aprende en uno y se le juzga en otro **nunca visto**
y no pegado (purga y embargo, López de Prado); 40 de 97 empresas al azar por vida; examen final
2022-2026 una sola vez.

| Ejecución | Cerebro | Bloques nuevos ganados | Examen final 2022-2026 |
|---|---|---|---|
| `factor-wf-…0939` | C | 10/19 | 5/9 (−1,64 · +0,63 · −1,85 · +1,41 · +2,41 · +6,35 · +8,34 · −4,81 · −2,19 €) |
| `factor-wf-fix-…0949` / `fix2-…0950` | C con la tendencia y el refugio **arreglados** (antes leían una columna vacía) | 9/22 | 4/9 (máx. −2,40 €: bandazos más pequeños) |
| `hibrido-wf-…1003` | **Bot + IA** (núcleo 75 % S&P 500 + satélite momentum; la IA solo corrige) | 10/22 | 3/9, todos a céntimos del índice. La IA no cambió nada: nada que añadir. |

## 4. Bots de reglas fijas (`bots.py`, sin IA: los 43 bloques 2005-2026 son prueba limpia)

`bots-…1000`:

| Bot | Gana al índice | Examen 2022-26 | Peor bloque |
|---|---|---|---|
| **Momentum top 3** (Jegadeesh-Titman) | **27/43** | 4/9 | −7,72 € |
| Momentum con filtro de tendencia | 25/43 | 4/9 | −8,14 € |
| **Núcleo 75 % + momentum 2** | 25/43 | 5/9 | **−1,19 €** |
| Cruce de medias | 19/43 | 4/9 | −9,21 € |
| Reversión RSI | 15/43 | 4/9 | −9,97 € |
| Tendencia del índice (Faber) | 2/43 | 0/9 | −9,46 € (se lo comen las comisiones) |

`bots-…0946` es anterior al arreglo de `mom_vsSMA200` (4 bots daban 0 operaciones).

## 5. % anual con TODOS los gastos (`anual.py`, 43 semestres encadenados, 21,3 años)

S&P 500 puro sin gastos: +10,8 %/año.

| Estrategia | 50 € | 5.000 € |
|---|---|---|
| Índice | −92,8 % | +9,6 % |
| Núcleo 75 % + momentum 2 | −92,6 % | +13,5 % |
| Momentum top 3 | −91,5 % | +24,1 % (inflado por **sesgo de supervivencia**: son las empresas que HOY están en el S&P 100) |
| Efectivo | −92,7 % | −1,5 % |

Con 50 € el servidor (~72 €/año) hace imposible sobrevivir, se invierta como se invierta.

## 6. Sesgo de supervivencia medido (`sesgo.py`, 28/09/2026)

Composición histórica del S&P 500 (fja05680/sp500) + precios de miembros actuales y retirados
(Johnbrick123/sp500-data: Yahoo + Tiingo), bajados con `scripts/fetch_pit.py`. Mismo bot de momentum
12-1 sobre las 100 más negociadas de: HOY = las que están hoy en el índice; ENTONCES = las que
estaban ese día. Gastos 0,10 %/operación; SPY sin gastos.

| Periodo · bot | HOY | ENTONCES | SPY | Sesgo |
|---|---|---|---|---|
| 2005-2026 · top 3 | +26,3 %/año (27/43) | +16,5 %/año (25/43) | +10,9 % | 9,9 pts |
| 2005-2026 · top 5 | +24,2 % (27/43) | +13,7 % (25/43) | +10,9 % | 10,5 pts |
| 2005-2026 · top 10 | +21,2 % (27/43) | +12,9 % (25/43) | +10,9 % | 8,3 pts |
| 2013-2026 · top 3 | +45,1 % (17/27) | +25,7 % (16/27) | +15,0 % | 19,4 pts |
| 2013-2026 · top 10 | +32,2 % (17/27) | +20,7 % (15/27) | +15,0 % | 11,5 pts |

**Cobertura:** en 2005 solo 288 de 495 miembros tienen precios (faltan Lehman, Bear Stearns,
Countrywide, Wachovia…); desde 2013 pasa del 78 % y en 2024 es del 98 %. Por eso las cifras
ENTONCES antes de 2013 siguen infladas (lo que falta son sobre todo empresas muertas) y el sesgo
medido es una COTA INFERIOR. El tramo fiable es 2013-2026.

Lectura: la mitad de la "ventaja" del momentum era sesgo. Lo que queda (+5 a +10 pts/año sobre el SPY
en 2013-2026) es real pero gana solo ~6 de cada 10 semestres, con semestres de −17 a −35 pts frente
al índice, y coincide con la era de las megatecnológicas: no hay garantía de que se repita.

## 7. Bots con capital realista (5.000 €, `bots-20260928-2014`)

Desde el 28/09 las vidas de entrenamiento y los bots arrancan con 5.000 € (`--initial-eur`,
`--capital`; 50 € sigue disponible). Universo aún con sesgo (97 empresas de hoy):

| Bot | Gana al índice | Examen 2022-26 | Peor bloque | %/año invertido |
|---|---|---|---|---|
| Momentum top 3 | 30/43 | 6/9 | −1.039 € | 24,2 % |
| Momentum con filtro | 27/43 | 5/9 | −1.241 € | 24,6 % |
| Núcleo 75 % + momentum 2 | 26/43 | 5/9 | **−251 €** | 15,2 % |
| Cruce de medias | 23/43 | 4/9 | −1.348 € | 14,3 % |
| Reversión RSI | 10/43 | 1/9 | −1.882 € | 8,3 % |
| Tendencia del índice | 5/43 | 1/9 | −1.427 € | 10,7 % |

Índice en el simulador: 11,3 %/año; S&P 500 puro: 10,8 %/año. Con 5.000 € las comisiones ya no se
comen a los bots de pocas operaciones, pero el orden no cambia.

## 8. Noticias leídas por FinBERT (`estudio_noticias.py`, 28/09/2026)

1.018.181 titulares de 735 empresas del S&P 500 (FNSPID, `scripts/fetch_news.py`; cubre
2009-02 → 2020-06), leídos una vez por FinBERT (`training/noticias.py`: tono + tipo; las de
"solo día" cuentan desde el día siguiente). 135 meses, 55.676 empresa-mes, 70 % con alguna noticia.

| Prueba | Resultado |
|---|---|
| IC mensual tono neto (30 d) → exceso del mes siguiente | **−0,001 (t = −0,15)**, positivo el 50 % de los meses |
| Netas buenas / empate / netas malas → mes siguiente vs SPY | −0,10 / −0,05 / −0,02 pts |
| Veto del robot: top 8 momentum con noticias netas malas vs resto | +0,11 vs −0,01 pts/mes (t = 0,14) |

Lectura: a un mes vista el tono de las noticias no anticipa nada; cuando sale el titular el
precio ya lo ha descontado. El robot de las demostraciones las CONSULTA pero no decide con ellas
(`--noticias no`). La IA tiene la herramienta en el mundo (`/api/v3/stock_news`: tipo y tono,
sin titular, que delataría empresa y época).

## Conclusiones

1. Ninguna IA bate al índice de forma fiable fuera de muestra; lo que parecía ventaja era beta o suerte.
2. El momentum clásico es lo único con ventaja histórica, irregular y en parte por sesgo de supervivencia.
3. Núcleo + satélite da la constancia pedida a cambio de quedarse cerca del índice.
4. Sesgo medido (28/09): con la composición histórica el momentum pierde ~10 pts/año; en 2013-2026,
   con datos fiables, le queda ventaja sobre el SPY pero irregular. Capital realista (5.000 €) aplicado.
5. Pendiente: datos que faltan (precios de empresas muertas antes de 2013; más fuentes de datos).
