"""zeta request and market validation (internal). Every failure is a ZetaError carrying a Z-code; the client turns it into an ERROR response."""
import datetime as dt
import math
import numbers
import re

MEASURES = ("NPV", "RISK_10BP", "CONVEXITY_10BP", "PAR_BP", "LADDER_10BP", "CASH_TO_DATE")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


class ZetaError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def bad(path, msg):
    return ZetaError("Z101", f"{path}: {msg}")


def iso(v, path):
    if not (isinstance(v, str) and _ISO.fullmatch(v)):
        raise bad(path, f"expected an ISO date string 'YYYY-MM-DD', got {v!r}")
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise bad(path, f"{v!r} is not a calendar date") from None


def num(v, path):
    if isinstance(v, bool) or not isinstance(v, numbers.Real) or not math.isfinite(v):
        raise bad(path, f"expected a finite number, got {v!r}")
    return float(v)


def fields(d, allowed, path, required):
    if not isinstance(d, dict):
        raise bad(path, f"expected an object, got {type(d).__name__}")
    extra = sorted(str(k) for k in d if k not in allowed)
    if extra:
        raise bad(path, f"unknown field(s) {extra}; allowed: {list(allowed)}")
    missing = [k for k in required if k not in d]
    if missing:
        raise bad(path, f"missing required field(s) {missing}")


def parse_market(m):
    """The stored market dict -> plain values. Runs at the first price() on a market, never at upload."""
    fields(m, ("asof", "calendar", "curve", "fixings"), "market", ("asof", "curve"))
    asof = iso(m["asof"], "market.asof")
    cal = m.get("calendar", {"name": "WEEKENDS", "holidays": []})
    fields(cal, ("name", "holidays"), "market.calendar", ("name", "holidays"))
    if not isinstance(cal["name"], str) or not isinstance(cal["holidays"], list):
        raise bad("market.calendar", "name must be a string and holidays a list of ISO dates")
    hol = frozenset(iso(h, f"market.calendar.holidays[{i}]") for i, h in enumerate(cal["holidays"]))
    c = m["curve"]
    fields(c, ("name", "day_count", "nodes"), "market.curve", ("name", "day_count", "nodes"))
    if c["name"] != "SOFR":
        raise bad("market.curve.name", f"only 'SOFR' is supported, got {c['name']!r}")
    if c["day_count"] not in ("ACT/360", "ACT/365"):
        raise bad("market.curve.day_count", f"expected 'ACT/360' or 'ACT/365', got {c['day_count']!r}")
    nodes = c["nodes"]
    if not isinstance(nodes, list) or not nodes:
        raise bad("market.curve.nodes", "expected a non-empty list of [days, zero_rate_bp] pairs")
    days, zeros = [], []
    for i, n in enumerate(nodes):
        p = f"market.curve.nodes[{i}]"
        if not isinstance(n, (list, tuple)) or len(n) != 2:
            raise bad(p, "expected [days, zero_rate_bp]")
        if isinstance(n[0], bool) or not isinstance(n[0], numbers.Integral):
            raise bad(p, f"days must be a whole number of ACT days from asof, got {n[0]!r}")
        if n[0] < 1:
            raise bad(p, f"days must be >= 1 (the rate at day 0 is implicit), got {n[0]}")
        days.append(int(n[0]))
        zeros.append(num(n[1], p + "[1]"))
    for i in range(1, len(days)):
        if days[i] <= days[i - 1]:
            raise ZetaError("Z301", f"curve nodes must be strictly ascending in days: node {i} (day {days[i]}) does not follow node {i - 1} (day {days[i - 1]})")
    fx = m.get("fixings", {})
    fields(fx, ("SOFR",), "market.fixings", ())
    fixings = {}
    for k, v in (fx.get("SOFR") or {}).items():
        fixings[iso(k, "market.fixings.SOFR key")] = num(v, f"market.fixings.SOFR[{k}]")
    return {"asof": asof, "holidays": hol, "cal_name": cal["name"], "basis": 360.0 if c["day_count"] == "ACT/360" else 365.0,
            "days": days, "zeros": zeros, "fixings_bp": fixings}


def parse_scenario(s):
    if s is None:
        return {}
    if not isinstance(s, dict) or len(s) != 1:
        raise bad("scenario", "expected exactly one of {'parallel_bp': x} or {'roll': 'TENOR'}")
    (k, v), = s.items()
    if k == "parallel_bp":
        return {k: num(v, "scenario.parallel_bp")}
    if k == "roll" and v == "TENOR":
        return {k: v}
    raise bad("scenario", f"expected {{'parallel_bp': x}} or {{'roll': 'TENOR'}}, got {s!r}")


def _tenor(end, path):
    if len(end) != 1:
        raise bad(path, "a tenor is exactly one of {'tenor_years': n} or {'tenor_months': n}")
    (k, v), = end.items()
    if k in ("tenor_years", "tenor_months"):
        if isinstance(v, bool) or not isinstance(v, numbers.Real) or not math.isfinite(v):
            raise bad(f"{path}.{k}", f"expected a number, got {v!r}")
        if v != int(v):
            raise ZetaError("Z204", f"{path}.{k}: {v!r} is not a whole number; only whole months and years are supported")
        n = int(v) * (12 if k == "tenor_years" else 1)
        if not 1 <= n <= 1200:
            raise bad(f"{path}.{k}", f"tenor must be between 1 month and 100 years, got {v}")
        return n
    if k.startswith("tenor_"):
        raise ZetaError("Z204", f"{path}.{k}: unsupported tenor unit; only whole months (tenor_months) and years (tenor_years)")
    raise bad(path, f"unknown field {k!r}")


def parse_trade(t, i):
    p = f"trades[{i}]"
    names = ("product", "leg_fixed", "notional_mm", "start", "end", "fixed_rate_bp")
    fields(t, names, p, names)
    if t["product"] != "OIS":
        raise bad(p + ".product", f"only 'OIS' is supported, got {t['product']!r}")
    if t["leg_fixed"] not in ("PAY", "RECEIVE"):
        raise bad(p + ".leg_fixed", f"expected 'PAY' or 'RECEIVE' (upper case), got {t['leg_fixed']!r}")
    mm = num(t["notional_mm"], p + ".notional_mm")
    if mm <= 0:
        raise bad(p + ".notional_mm", f"notional is unsigned and must be > 0 (direction is leg_fixed), got {mm}")
    s = t["start"]
    if isinstance(s, dict):
        fields(s, ("spot_lag",), p + ".start", ("spot_lag",))
        lag = s["spot_lag"]
        if isinstance(lag, bool) or not isinstance(lag, numbers.Integral) or not 0 <= lag <= 10:
            raise bad(p + ".start.spot_lag", f"expected a whole number of business days 0..10, got {lag!r}")
        start = ("spot", int(lag))
    else:
        start = ("date", iso(s, p + ".start"))
    e = t["end"]
    end = ("months", _tenor(e, p + ".end")) if isinstance(e, dict) else ("date", iso(e, p + ".end"))
    r = t["fixed_rate_bp"]
    if isinstance(r, str) and r != "PAR":
        raise bad(p + ".fixed_rate_bp", f"expected a number of bp or the token 'PAR' (upper case), got {r!r}")
    rate = "PAR" if isinstance(r, str) else num(r, p + ".fixed_rate_bp")
    return {"sign": 1 if t["leg_fixed"] == "PAY" else -1, "mm": mm, "start": start, "end": end, "rate": rate}
