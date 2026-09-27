"""zeta service internals: curve, schedule and swap arithmetic. Not public API; nothing from this module's dependencies is exposed to clients."""
import datetime as dt
import importlib
import math
import os
import sys

from ._check import ZetaError, bad


def _load():
    name = "pricebt.testing.refstack"
    try:
        return importlib.import_module(name)
    except ImportError:  # the service host lets an operator point at the arithmetic library
        sys.path.insert(0, os.environ.get("ZETA_ENGINE_SRC", "src"))  # default: `src` of the repository root (the documented working directory)
        return importlib.import_module(name)


_rs = _load()
PILLAR_MONTHS = (1, 3, 6, 12, 24, 36, 60, 84, 120, 180, 240, 360)
# zeta's fixed OIS conventions: spot lag 2 bd, ACT/360, annual, modified following, payment lag 2 bd, daily compounded, short front stub.
_CONV = _rs.SwapConv("zeta", 2, "act360", "annual", "modified_following", 2)
_DAY = dt.timedelta(days=1)


class View:
    """One (market, as_of, scenario) world: the curve rolled and shocked, the fixings, the calendar. Built once per request, shared by its trades."""

    def __init__(self, m, as_of, scenario):
        a, b = m["asof"], m["basis"]
        self.reference_date = self.ts = as_of
        self.market_asof = a
        self.cal = _rs.Calendar(m["holidays"], name=m["cal_name"])
        self.fixings = {d: v / 100.0 for d, v in m["fixings_bp"].items()}  # percent, as the arithmetic wants them
        self.keep, self._memo = [], {}
        c0 = _rs.RefCurve(a, [a] + [a + dt.timedelta(days=d) for d in m["days"]], [1.0] + [math.exp(-z * 1e-4 * d / b) for d, z in zip(m["days"], m["zeros"])])
        n = (as_of - a).days
        if n == 0:
            c = c0
        elif scenario.get("roll") == "TENOR":  # unchanged in tenor space: the same offsets from as_of carry the same discount factors
            c = _rs.RefCurve(as_of, [as_of] + [d + dt.timedelta(days=n) for d in c0.dates[1:]], [1.0] + list(c0.dfs[1:]))
        else:  # unchanged in date space: forwards are realised, discount factors re-based to as_of
            d1 = c0.df(as_of)
            pts = [(d, v / d1) for d, v in zip(c0.dates, c0.dfs) if d > as_of]
            c = _rs.RefCurve(as_of, [as_of] + [d for d, _ in pts], [1.0] + [v for _, v in pts])
        self.curve = self.shift(c, scenario.get("parallel_bp", 0.0), b)
        # fixings dated on/after asof that the market does not carry are implied from the market's own (unshocked) curve: the forward for that day
        if n:
            gap = [d for d in self.cal.business_days(a, as_of - _DAY) if d not in self.fixings]
            nxt = [self.cal.add_business_days(d, 1) for d in gap]
            f0 = c0.dfs_at([d.toordinal() for d in gap])
            f1 = c0.dfs_at([d.toordinal() for d in nxt])
            for d, e, x, y in zip(gap, nxt, f0, f1):
                self.fixings[d] = float((x / y - 1.0) * 360.0 / (e - d).days * 100.0)

    @staticmethod
    def shift(curve, bp, basis):
        if not bp:
            return curve
        ref = curve.reference_date
        return _rs.RefCurve(ref, curve.dates, [v * math.exp(-bp * 1e-4 * (d - ref).days / basis) for d, v in zip(curve.dates, curve.dfs)])

    # what the arithmetic library asks of a pricer
    def calendar(self, name):
        return self.cal

    def need_curve(self):
        return self.curve

    def memo(self, key, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]


def _missing_fixing(view, eff, i):
    ref = view.reference_date
    if eff < ref:
        for d in view.cal.business_days(view.cal.following(eff), view.cal.add_business_days(ref, -1)):
            if d not in view.fixings:
                raise ZetaError("Z530", f"trades[{i}]: no SOFR fixing dated {d.isoformat()} in the market (fixings before asof are required from the start date {eff.isoformat()} up to the day before as_of {ref.isoformat()})")


def _swap(view, t, rate_pct, i, unit=False):
    cal, ref = view.cal, view.reference_date
    kind, v = t["start"]
    eff = cal.add_business_days(ref, v) if kind == "spot" else cal.adjust(v, "modified_following")
    kind, v = t["end"]
    if kind == "date" and v <= eff:
        raise bad(f"trades[{i}].end", f"end {v.isoformat()} is not after the start {eff.isoformat()}")
    try:
        sched = _rs.build_schedule(eff, f"{v}M" if kind == "months" else v, cal, freq_months=12, bdc="modified_following", pay_lag=2, basis=360.0)
        s = _rs.RefSwap(effective=eff, schedule=sched, sign=t["sign"], notional=1.0 if unit else t["mm"] * 1e6, fixed_rate=rate_pct, conv=_CONV)
    except Exception as e:
        raise bad(f"trades[{i}]", "cannot build a schedule from this start and end") from None
    view.keep.append(s)  # the arithmetic caches per object identity: keep every swap alive for the life of the view
    return s


def evaluate(view, t, measures, i):
    """One trade -> {"resolved", "paid_on_as_of", "measures"}. Currency amounts are holder-signed: a PAY trade gains when rates rise."""
    ref, curve = view.reference_date, view.curve
    probe = _swap(view, t, 0.0, i, unit=True)
    _missing_fixing(view, probe.effective, i)
    rate = t["rate"]
    if rate == "PAR":
        if probe.matured(ref):
            raise bad(f"trades[{i}].fixed_rate_bp", "'PAR' is undefined: the trade has no flow paid on or after as_of")
        rate_pct = probe.par_rate(view, curve, ref)
    else:
        rate_pct = rate / 100.0
    s = _swap(view, t, rate_pct, i)
    live = not s.matured(ref)
    amounts = s._amounts(view, curve, ref)
    on_day = s._p == ref.toordinal()
    paid_today = float(amounts[on_day].sum()) if live else 0.0
    npv_incl = s.npv(view, curve, ref)  # includes the flow paid on as_of; the differences below cancel it
    out = {}
    need = set(measures)
    if "NPV" in need:
        out["NPV"] = npv_incl - paid_today
    if "CASH_TO_DATE" in need:
        out["CASH_TO_DATE"] = float(amounts[(s._p > view.market_asof.toordinal()) & (s._p <= ref.toordinal())].sum())
    if "PAR_BP" in need:
        out["PAR_BP"] = s.par_rate(view, curve, ref) * 100.0 if live else None
    if need & {"RISK_10BP", "CONVEXITY_10BP", "LADDER_10BP"}:
        if live:
            rc = _rs._risk_curves(view, _CONV, [f"{m}M" for m in PILLAR_MONTHS])
            up, dn, base = (s.npv(view, c, ref) for c in (rc.parallel_up, rc.parallel_dn, rc.base))
            out["RISK_10BP"] = 10.0 * (up - dn) / 2.0
            out["CONVEXITY_10BP"] = 100.0 * (up + dn - 2.0 * base)
            out["LADDER_10BP"] = [{"pillar_months": m, "risk": 10.0 * (s.npv(view, d, ref) - s.npv(view, u, ref))} for m, u, d in zip(PILLAR_MONTHS, rc.ups, rc.dns)]
        else:
            out["RISK_10BP"] = out["CONVEXITY_10BP"] = 0.0
            out["LADDER_10BP"] = [{"pillar_months": m, "risk": 0.0} for m in PILLAR_MONTHS]
    return {"index": i, "resolved": {"start": s.effective.isoformat(), "end": s.maturity.isoformat(), "fixed_rate_bp": rate_pct * 100.0, "notional_mm": t["mm"]},
            "paid_on_as_of": paid_today, "measures": {k: out[k] for k in measures if k in out}}

