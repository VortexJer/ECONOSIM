"""API de control para el humano/panel. Nunca alcanzable desde el sandbox.

Se enlaza a la interfaz de fuera (no a la red interna) y exige un token si se
configura. Con ECONOSIM_DEBUG=1 expone también la fecha real y permite
saltar en el tiempo (tests).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from aiohttp import web

from .world import World


def _n(x, nd: int = 3):
    """Redondeo tolerante: los ratios pueden no existir y eso no es un fallo."""
    return None if x is None else round(x, nd)


def control_app(world: World, token: str = "", debug: bool = False) -> web.Application:
    app = web.Application()

    @web.middleware
    async def auth(request: web.Request, handler):
        if token and request.headers.get("X-Econosim-Token") != token:
            return web.json_response({"error": "forbidden"}, status=403)
        return await handler(request)

    app.middlewares.append(auth)

    async def state(_):
        return web.json_response(world.state(include_real=debug))

    async def speed(req):
        body = await req.json()
        world.speed = float(body.get("speed", 0))
        return web.json_response({"speed": world.speed})

    async def advance(req):
        if not debug:
            return web.json_response({"error": "debug only"}, status=403)
        body = await req.json()
        ran = world.advance(timedelta(seconds=float(body.get("seconds", 0))))
        return web.json_response({"events_run": ran, **world.state(include_real=True)})

    async def ledger(_):
        return web.json_response([e.__dict__ for e in world.ledger.entries()])

    async def hetzner(_):
        h = world.twins.get("hetzner")
        if h is None:
            return web.json_response({})
        return web.json_response({
            "locked": h.locked,
            "servers": [{"id": s.id, "name": s.name, "type": s.stype["name"], "status": s.status,
                         "created": h.clock.display_iso(s.created_real)} for s in h.servers.values()],
            "invoices": [{k: v for k, v in i.items() if k != "issued_real"} for i in h.invoices],
        })

    async def openrouter(_):
        o = world.twins.get("openrouter")
        if o is None:
            return web.json_response({})
        return web.json_response({"calls": o.calls, "credits_usd": round(o.credits_usd, 6),
                                  "usage_usd": round(o.usage_usd, 6), "purchased_usd": o.purchased_usd,
                                  "purchases": o.purchases,
                                  "generations": list(o.generations.values())[-20:]})

    async def fakenet(_):
        fn = world.twins.get("fakenet")
        return web.json_response({"hosts": fn.hosts(), "served": fn.served, "dropped": fn.dropped} if fn else {})

    def _daily_burn_cents() -> int:
        """Estimación del gasto diario: VPS prorrateado + presupuestos de anuncios activos."""
        burn = 0
        h = world.twins.get("hetzner")
        if h is not None:
            for s in h.servers.values():
                if not s.deleted:
                    burn += int(round(s.stype["monthly"] * 100 / 30 / _fx()))
        for name in ("meta_ads", "google_ads"):
            m = world.twins.get(name)
            if m is not None:
                for c in m.mgr.campaigns.values():
                    if c.status == "ACTIVE":
                        burn += int(round(c.daily_budget_usd * 100 / _fx()))
        return max(1, burn)

    def _fx() -> float:
        try:
            return world.load_pricing("fx")["usd_per_eur"]
        except Exception:
            return 1.0


    def _calendar() -> dict:
        """Resumen por día del episodio: saldo al empezar y al acabar el día, gasto,
        ingreso y llamadas al cerebro. Desde el arranque hasta el horizonte (o hoy)."""
        from datetime import date as _date, timedelta as _td
        start = _date.fromisoformat(world.episode.display_start[:10])
        today = world.clock.display_now.date()
        end_iso = world.clock.display_iso(world.sim_end_real) if world.sim_end_real else None
        end = _date.fromisoformat(end_iso[:10]) if end_iso else today
        if (end - start).days > 400:
            end = start + _td(days=400)
        bank = [e for e in world.ledger.entries() if e.account == "bank"]
        o = world.twins.get("openrouter")
        by_day = getattr(o, "calls_by_day", {}) if o is not None else {}
        days = []
        bal = world.episode.initial_cents
        i = 0
        d = start
        while d <= end:
            key = d.isoformat()
            start_bal = bal
            spent = earned = 0
            while i < len(bank) and bank[i].display_ts[:10] <= key:
                e = bank[i]
                if e.display_ts[:10] == key:
                    if e.amount_cents < 0:
                        spent += -e.amount_cents
                    elif e.concept != "Saldo inicial":
                        earned += e.amount_cents
                bal = e.balance_after
                i += 1
            state = "today" if d == today else ("past" if d < today else "future")
            days.append({"date": key, "state": state, "balance_start": start_bal,
                         "balance_end": bal if d <= today else None,
                         "spent": spent, "earned": earned, "calls": by_day.get(key, 0)})
            d += _td(days=1)
        return {"start": start.isoformat(), "end": end.isoformat(), "today": today.isoformat(),
                "days": days}

    async def dashboard(_):
        st = world.state(include_real=debug)
        burn = _daily_burn_cents()
        days_left = world.balance() / burn if burn > 0 else None
        alp = world.twins.get("alpaca")
        positions = []
        equity_cents = world.balance()
        stocks_value_cents = unrealized_cents = 0
        if alp is not None:
            from .ledger import to_cents
            equity_cents = alp.equity_cents()
            for pos in alp.positions.values():
                if pos.qty <= 0:
                    continue
                try:
                    j = alp._position_json(pos)          # lo que ve la IA
                    # …y en céntimos, para el panel humano: valor, coste y resultado
                    px = alp._masked_price(pos.alias)
                    mv = to_cents(pos.qty * px) if px is not None else 0
                    cost = to_cents(pos.qty * pos.avg)
                    j["market_value_cents"] = mv
                    j["cost_cents"] = cost
                    j["unrealized_cents"] = mv - cost
                    stocks_value_cents += mv
                    unrealized_cents += mv - cost
                    positions.append(j)
                except Exception:
                    pass
        stripe = world.twins.get("stripe")
        stripe_info = {}
        if stripe is not None:
            stripe_info = {"available": stripe.available_cents, "pending": stripe.pending_cents,
                           "charges": stripe.charge_count, "payouts": len(stripe.payouts),
                           "disputes": len(stripe.disputes), "refunds": len(stripe.refunds)}
        campaigns = []
        for name in ("meta_ads", "google_ads"):
            m = world.twins.get(name)
            if m is not None:
                for c in m.mgr.campaigns.values():
                    campaigns.append({"platform": m.mgr.platform, "name": c.name, "status": c.status,
                                      "daily_budget_usd": c.daily_budget_usd, "spend_usd": round(c.spend_usd, 2),
                                      "impressions": c.impressions, "clicks": int(round(c.clicks))})
        mb = world.twins.get("market_bridge")
        listings = mb.summary() if mb is not None else []
        resolver = world.twins.get("resolver")
        actions = resolver.actions[-30:] if resolver is not None else []
        hostile = world.twins.get("hostile")
        score = hostile.score() if hostile is not None else {}
        o = world.twins.get("openrouter")
        brain = {"calls": o.calls, "usage_usd": round(o.usage_usd, 6),
                 "credits_usd": round(o.credits_usd, 4)} if o is not None else {}
        # diario de pensamiento: una frase por llamada (qué razonó y qué hace)
        thoughts = o.thoughts[-25:] if o is not None else []
        h = world.twins.get("hetzner")
        servers = [{"name": s.name, "type": s.stype["name"], "status": s.status}
                   for s in h.servers.values() if not s.deleted] if h is not None else []
        email = world.twins.get("email")
        inbox = len(email.inbox) if email is not None else 0
        ledger = [e.__dict__ for e in world.ledger.entries()][-60:]
        return web.json_response({
            **st,
            "equity_cents": equity_cents,
            "daily_burn_cents": burn,
            "days_left": round(days_left, 1) if days_left is not None else None,
            "score": score.get("score"),
            "score_detail": score,
            "positions": positions,
            "stocks_value_cents": stocks_value_cents,     # valor de mercado de la cartera, en euros
            "unrealized_cents": unrealized_cents,         # cuánto va por encima/por debajo
            "stripe": stripe_info,
            "campaigns": campaigns,
            "listings": listings,
            "actions": actions,
            "brain": brain,
            "thoughts": thoughts,
            "calendar": _calendar(),
            "servers": servers,
            "inbox": inbox,
            "ledger": ledger,
        })

    # ------------------------------------------------------------------
    # VISOR DEL MUNDO (solo para el humano; el sandbox no alcanza esta API).
    # Permite abrir los servicios de la simulación como los ve la IA y levantar
    # la cortina: qué fecha real hay detrás y qué empresa es cada alias.
    # ------------------------------------------------------------------
    def _servicios() -> list[dict]:
        """Los servicios montados, con una ruta de ejemplo para empezar a mirar."""
        fn = world.twins.get("fakenet")
        hosts = fn.hosts() if fn else []
        ejemplos = {
            "api.alpaca.markets": ["/v2/account", "/v2/positions", "/v2/orders", "/v2/clock", "/v2/assets"],
            "data.alpaca.markets": ["/v2/stocks/{sym}/snapshot", "/v2/stocks/{sym}/bars?timeframe=1Day&limit=30"],
            "financialmodelingprep.com": [
                "/api/v3/financial-statement-symbol-lists",
                "/api/v3/profile/{sym}", "/api/v3/income-statement/{sym}?period=quarter&limit=5",
                "/api/v3/balance-sheet-statement/{sym}?limit=2", "/api/v3/cash-flow-statement/{sym}?period=quarter&limit=4",
                "/api/v3/ratios-ttm/{sym}", "/api/v3/key-metrics-ttm/{sym}",
                "/api/v3/historical/earning_calendar/{sym}?limit=8", "/api/v3/earning_calendar?days=45",
                "/api/v3/analyst-estimates/{sym}", "/api/v4/price-target-consensus?symbol={sym}"],
            "api.hetzner.cloud": ["/v1/servers", "/v1/server_types", "/v1/pricing"],
            "api.stripe.com": ["/v1/balance", "/v1/products", "/v1/charges", "/v1/payouts"],
            "thirdparty.qonto.com": ["/v2/organization", "/v2/transactions"],
            "openrouter.ai": ["/api/v1/models", "/api/v1/credits"],
            "api.resend.com": ["/inbox"],
            "api.porkbun.com": ["/api/json/v3/pricing/get"],
            "graph.facebook.com": ["/v22.0/act_1/campaigns"],
            "api.the-odds-api.com": ["/v4/sports"],
        }
        return [{"host": h, "rutas": ejemplos.get(h, ["/"])} for h in hosts]

    def _credenciales(host: str) -> tuple[dict, dict]:
        """Cabeceras y parámetros con los que la IA firma en cada servicio."""
        t = world.twins
        if host.endswith("alpaca.markets") and "alpaca" in t:
            a = t["alpaca"]
            return {"APCA-API-KEY-ID": a.key_id, "APCA-API-SECRET-KEY": a.secret}, {}
        if host == "financialmodelingprep.com" and "fundamentals" in t:
            return {}, {"apikey": t["fundamentals"].api_key}
        if host == "api.hetzner.cloud" and "hetzner" in t:
            return {"Authorization": f"Bearer {t['hetzner'].token}"}, {}
        if host == "api.stripe.com" and "stripe" in t:
            return {"Authorization": f"Bearer {t['stripe'].secret_key}"}, {}
        if host == "thirdparty.qonto.com" and "qonto" in t:
            return {"Authorization": t["qonto"].auth_header}, {}
        if host == "openrouter.ai" and "openrouter" in t:
            return {"Authorization": f"Bearer {t['openrouter'].api_key}"}, {}
        return {}, {}

    def _mask():
        a = world.twins.get("alpaca")
        return getattr(a, "mask", None) if a is not None else None

    async def world_services(_):
        m = _mask()
        return web.json_response({"servicios": _servicios(),
                                  "simbolos": sorted(m.aliases) if m is not None else []})

    async def world_get(req):
        """GET a un servicio del mundo, firmado como lo haría la IA. Solo lectura:
        aquí no se puede comprar, vender ni gastar; esto es una ventana, no un mando."""
        crudo = req.query.get("url", "").strip()
        if not crudo:
            return web.json_response({"error": "falta url"}, status=400)
        if "://" not in crudo:
            crudo = "http://" + crudo
        from urllib.parse import urlsplit
        u = urlsplit(crudo)
        fn = world.twins.get("fakenet")
        if fn is None or u.hostname not in fn.hosts():
            return web.json_response({"error": f"ese servicio no existe en este mundo: {u.hostname}"}, status=404)
        cab, params = _credenciales(u.hostname)
        ruta = u.path + (("?" + u.query) if u.query else "")
        import aiohttp
        destino = f"http://127.0.0.1:{fn.bound_ports()[0]}{ruta}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(destino, headers={**cab, "Host": u.hostname},
                                 params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    cuerpo = await r.text()
                    return web.json_response({"status": r.status, "host": u.hostname, "path": ruta,
                                              "content_type": r.headers.get("Content-Type", ""),
                                              "body": cuerpo[:200000]})
        except Exception as e:
            return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=502)

    async def world_reveal(_):
        """Detrás de la cortina: la fecha REAL, el desfase y qué empresa es cada alias.
        Nunca sale de aquí: el sandbox no tiene ruta a esta API."""
        m = _mask()
        empresas = []
        if m is not None:
            for alias in sorted(m.aliases):
                real = m.to_real(alias)
                empresas.append({"alias": alias, "real": real,
                                 "factor": round(m.factor(real), 8) if real else None})
        return web.json_response({
            "fecha_mostrada": world.clock.display_iso(),
            "fecha_real": world.clock.real_now().isoformat(),
            "desfase_años": world.clock.offset_years,
            "episodio": world.episode.id,
            "arranque_real": m.start_day.isoformat() if m is not None else None,
            "semilla": getattr(m, "seed", None),
            "empresas": empresas,
        })

    async def world_stock(req):
        """Todo lo de UNA acción en una sola llamada: cotización, histórico para el
        gráfico, lo que tenemos de ella y sus números. Lo que enseñaría un bróker."""
        m = _mask()
        alp = world.twins.get("alpaca")
        if m is None or alp is None:
            return web.json_response({"error": "este mundo no tiene bolsa"}, status=404)
        alias = (req.query.get("symbol") or "").upper()
        if m.to_real(alias) is None:
            return web.json_response({"error": f"no cotiza nada llamado {alias}"}, status=404)
        try:
            dias = max(5, min(int(req.query.get("days", "180")), 5000))
        except ValueError:
            dias = 180
        real = m.to_real(alias)
        serie = alp.data.series[real]
        hoy = alp._session_asof()
        pasado = [b for b in serie.bars if hoy is not None and b.day <= hoy]
        barras = [{"d": alp.clock.display(datetime.combine(b.day, datetime.min.time())).date().isoformat(),
                   "o": round(m.index_price(real, b.open), 4), "h": round(m.index_price(real, b.high), 4),
                   "l": round(m.index_price(real, b.low), 4), "c": round(m.index_price(real, b.close), 4),
                   "v": b.volume} for b in pasado[-dias:]]

        def variacion(n: int):
            """Cuánto se ha movido en las últimas n sesiones, en %."""
            if len(pasado) <= n:
                return None
            antes = pasado[-1 - n].close
            return round(100 * (pasado[-1].close / antes - 1), 2) if antes else None

        pos = alp.positions.get(alias)
        cartera = None
        if pos is not None and pos.qty > 0:
            px = alp._masked_price(alias)
            valor = pos.qty * px if px is not None else 0.0
            coste = pos.qty * pos.avg
            cartera = {"qty": pos.qty, "precio_medio": round(pos.avg, 4), "valor": round(valor, 2),
                       "coste": round(coste, 2), "resultado": round(valor - coste, 2),
                       "resultado_pct": round(100 * (valor / coste - 1), 2) if coste else 0.0}

        numeros = None
        f = world.twins.get("fundamentals")
        if f is not None:
            c = f._facts(alias)
            if c is not None:
                dia = f._hoy()
                v = f._valoracion(alias, c, dia)
                q = c.periodos(dia, "revenue", (80, 100), 8)
                crec = (q[0].val / q[4].val - 1) if len(q) >= 5 and q[4].val else None
                prox = c.proxima_publicacion(dia)
                numeros = {"per": _n(v["per"]), "precio_ventas": _n(v["precio_ventas"]),
                           "precio_valor_contable": _n(v["precio_valor_contable"]),
                           "margen_neto": _n(v["margen_neto"], 4), "roe": _n(v["roe"], 4),
                           "deuda_fondos_propios": _n(v["deuda_fondos_propios"]),
                           "crecimiento": _n(crec, 4), "base": v["base"],
                           "capitalizacion": _n(v["capitalizacion"], 0),
                           "proximos_resultados": f._dia(prox)}
        return web.json_response({
            "symbol": alias, "precio": barras[-1]["c"] if barras else None,
            "dia": barras[-1]["d"] if barras else None,
            "var_1d": variacion(1), "var_1m": variacion(21), "var_1a": variacion(252),
            "barras": barras, "cartera": cartera, "numeros": numeros,
            "abierto": alp.cal.is_open(world.clock.real_now()),
        })

    app.router.add_get("/world/stock", world_stock)
    app.router.add_get("/world/services", world_services)
    app.router.add_get("/world/get", world_get)
    app.router.add_get("/world/reveal", world_reveal)
    app.router.add_get("/dashboard", dashboard)
    app.router.add_get("/state", state)
    app.router.add_post("/speed", speed)
    app.router.add_post("/advance", advance)
    app.router.add_get("/ledger", ledger)
    app.router.add_get("/hetzner", hetzner)
    app.router.add_get("/fakenet", fakenet)
    async def hostile(_):
        hh = world.twins.get("hostile")
        return web.json_response(hh.score() if hh else {})

    app.router.add_get("/openrouter", openrouter)
    app.router.add_get("/hostile", hostile)
    return app


def debug_enabled() -> bool:
    return os.environ.get("ECONOSIM_DEBUG", "") not in ("", "0", "false")
