"""G2: máscara — alias estable/único, indexado a 100, retornos y correlaciones intactos, sin fugas."""
from __future__ import annotations

from datetime import date

from _common import check
from econosim.market.data import MarketData
from econosim.market.mask import EpisodeMask, INDEX_BASE

md = MarketData()
syms = md.symbols
start = md.series[syms[0]].bars[200].day        # un día cualquiera con futuro

m1 = EpisodeMask(md, seed="ep-alpha", start_day=start)
m2 = EpisodeMask(md, seed="ep-beta", start_day=start)

# --- alias: estable dentro del episodio, biyectivo, distinto entre episodios ----------
check(len(set(m1.alias_of.values())) == len(syms), "alias no únicos")
for sym in syms:
    a = m1.to_alias(sym)
    check(m1.to_alias(sym) == a, "alias no estable en la misma máscara")
    check(m1.to_real(a) == sym, "alias no invertible")
    check(sym not in a, f"el alias {a} contiene el símbolo real {sym}")
distintos = sum(1 for sym in syms if m1.to_alias(sym) != m2.to_alias(sym))
check(distintos >= len(syms) * 0.8, f"demasiados alias iguales entre episodios: {len(syms)-distintos}")
# reproducible: misma semilla, mismos alias
check(EpisodeMask(md, "ep-alpha", start).alias_of == m1.alias_of, "no reproducible con la misma semilla")

# --- indexado: 100 en el arranque, y conserva los retornos exactos --------------------
for sym in syms[:6]:
    s = md.series[sym]
    base = s.asof(start)
    check(abs(m1.index_price(sym, base.close) - INDEX_BASE) < 1e-6, f"{sym}: no vale 100 en el arranque")
    # retornos diarios del enmascarado == retornos reales
    later = [b for b in s.bars if b.day > start][:250]
    for prev, cur in zip([base] + later[:-1], later):
        r_real = cur.close / prev.close - 1
        r_mask = m1.index_price(sym, cur.close) / m1.index_price(sym, prev.close) - 1
        check(abs(r_real - r_mask) < 1e-6, f"{sym}: retorno alterado por la máscara")

# --- correlación entre dos símbolos: idéntica antes y después de enmascarar -----------
def rets(seq):
    return [seq[i] / seq[i - 1] - 1 for i in range(1, len(seq))]


def corr(a, b):
    n = len(a); ma = sum(a) / n; mb = sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a) ** 0.5
    vb = sum((y - mb) ** 2 for y in b) ** 0.5
    return cov / (va * vb)


common = sorted(set(md.series[syms[0]]._days) & set(md.series[syms[1]]._days))
common = [d for d in common if d >= start][:400]
ra_real = rets([md.series[syms[0]].on(d).close for d in common])
rb_real = rets([md.series[syms[1]].on(d).close for d in common])
ra_mask = rets([m1.index_price(syms[0], md.series[syms[0]].on(d).close) for d in common])
rb_mask = rets([m1.index_price(syms[1], md.series[syms[1]].on(d).close) for d in common])
check(abs(corr(ra_real, rb_real) - corr(ra_mask, rb_mask)) < 1e-9, "la máscara cambió la correlación")

# --- barra enmascarada: OHLC coherente e indexado ------------------------------------
b = md.series[syms[0]].on(common[5])
mb = m1.mask_bar(syms[0], b)
check(mb.low <= mb.open <= mb.high and mb.low <= mb.close <= mb.high and mb.volume == b.volume, "barra enmascarada incoherente")
check(abs(mb.close - m1.index_price(syms[0], b.close)) < 1e-6, "cierre enmascarado != index_price")

# --- sin fugas: ni símbolo real ni año real en los alias -----------------------------
real_years = {str(md.series[s].first_day.year) for s in syms} | {str(md.series[s].last_day.year) for s in syms}
for a in m1.aliases:
    check(not any(sym in a for sym in syms), f"alias {a} filtra símbolo real")
    check(not any(y in a for y in real_years), f"alias {a} filtra un año real")

print("MASK OK")
