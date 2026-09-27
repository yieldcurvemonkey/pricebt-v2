"""zeta.Client: the whole public surface. Every service error is a response dict; only programming errors (a closed client) raise."""
import copy
import datetime as dt

from . import _engine
from ._check import MEASURES, ZetaError, bad, fields, iso, parse_market, parse_scenario, parse_trade

UPLOAD_MS, REQUEST_MS, TRADE_MS = 120, 25, 3  # deterministic simulated latency: a market upload is expensive, a trade in a batch is cheap


class Client:
    def __init__(self):
        self._markets, self._n, self._closed = {}, 0, False
        self.stats = {"requests": 0, "markets_uploaded": 0, "trade_evaluations": 0, "errors": 0, "latency_ms": 0}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def upload_market(self, market):
        """Store a copy of `market` on the service and return its id. Nothing is validated here: a bad market shows up as an error on the first price()."""
        if self._closed:
            raise RuntimeError("zeta client is closed")
        self.stats["markets_uploaded"] += 1
        self.stats["latency_ms"] += UPLOAD_MS
        self._n += 1
        mid = f"mkt-{self._n:04d}"
        self._markets[mid] = {"raw": copy.deepcopy(market), "parsed": None, "error": None}
        return mid

    def close(self):
        """Drop every stored market; later price() calls answer Z412."""
        self._markets.clear()
        self._closed = True

    def price(self, request):
        s = self.stats
        s["requests"] += 1
        try:
            out = self._price(request)
        except ZetaError as e:
            out = {"status": "ERROR", "code": e.code, "message": e.message}
        except Exception as e:  # a bug in the service, never a client mistake
            kind = type(e).__name__ if type(e).__module__ == "builtins" else "engine"
            out = {"status": "ERROR", "code": "Z500", "message": f"internal error ({kind})"}
        n = len(out.get("results", ()))
        ms = REQUEST_MS + TRADE_MS * n
        s["trade_evaluations"] += n
        s["latency_ms"] += ms
        s["errors"] += out["status"] != "OK"
        out["latency_ms"] = ms
        return out

    def _market(self, mid):
        rec = self._markets.get(mid)
        if rec is None:
            raise ZetaError("Z412", f"unknown market_id {mid!r}" + (" (the client is closed)" if self._closed else ""))
        if rec["parsed"] is None and rec["error"] is None:
            try:
                rec["parsed"] = parse_market(rec["raw"])
            except ZetaError as e:
                rec["error"] = e
        if rec["error"]:
            raise rec["error"]
        return rec["parsed"]

    def _price(self, req):
        fields(req, ("market_id", "as_of", "scenario", "trades", "measures"), "request", ("market_id", "as_of", "trades", "measures"))
        if not isinstance(req["market_id"], str):
            raise bad("request.market_id", f"expected a string, got {req['market_id']!r}")
        m = self._market(req["market_id"])
        as_of = iso(req["as_of"], "request.as_of")
        last = m["asof"] + dt.timedelta(days=m["days"][-1])
        if not m["asof"] <= as_of < last:
            raise bad("request.as_of", f"{as_of.isoformat()} must be on or after the market asof {m['asof'].isoformat()} and before the last curve node {last.isoformat()}")
        scenario = parse_scenario(req.get("scenario"))
        ms = req["measures"]
        if not isinstance(ms, list) or not ms or any(x not in MEASURES for x in ms):
            raise bad("request.measures", f"expected a non-empty list drawn from {list(MEASURES)}, got {ms!r}")
        if not isinstance(req["trades"], list) or not req["trades"]:
            raise bad("request.trades", "expected a non-empty list of trades")
        trades = [parse_trade(t, i) for i, t in enumerate(req["trades"])]
        view = _engine.View(m, as_of, scenario)
        return {"status": "OK", "market_id": req["market_id"], "as_of": as_of.isoformat(), "results": [_engine.evaluate(view, t, ms, i) for i, t in enumerate(trades)]}
