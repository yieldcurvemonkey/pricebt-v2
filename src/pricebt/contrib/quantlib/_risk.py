"""The par-swap risk curves behind `delta_ladder`, `dv01` (started swaps) and `gamma`.

One risk curve per snapshot and pillar set: a QuantLib `PiecewiseLogLinearDiscount` bootstrapped from OIS rate helpers at the pillar tenors, quoted at the par rates of the dense
curve (so each pillar par swap reprices at the dense par rate), with a node AT THE TERMINATION of each pillar swap (`Pillar.CustomDate`; the default is the last payment date,
two business days later). Central +-0.5bp bumps of each quote give the ladder scenarios; +-1bp of all quotes give the parallel scenarios used for `gamma`.

The bootstrap objects observe the evaluation date, so they are built inside a `guard`, in a frame that ends before it exits; what survives is plain `ql.DiscountCurve`s.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Tuple

from ...errors import ConfigError
from . import _compat as C
from . import _ois, _schedule
from .conventions import SwapConv

ql = C.ql

DEFAULT_TENORS: Tuple[str, ...] = ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
LADDER_BUMP = 0.5e-4   # decimal, per pillar, central
PARALLEL_BUMP = 1.0e-4  # decimal, all pillars, central (gamma)


@dataclass(frozen=True)
class RiskCurves:
    tenors: Tuple[str, ...]
    terms: Tuple[dt.date, ...]
    pars: Tuple[float, ...]  # decimals
    base: "ql.DiscountCurve"
    ups: Tuple["ql.DiscountCurve", ...]
    dns: Tuple["ql.DiscountCurve", ...]
    parallel_up: "ql.DiscountCurve"
    parallel_dn: "ql.DiscountCurve"


def clean_tenors(tenors) -> Tuple[str, ...]:
    out = tuple(str(t).strip().upper() for t in tenors)
    if not out:
        raise ConfigError("a delta ladder needs at least one tenor", code="LADDER")
    for t in out:
        _schedule.tenor_months(t)
    if len(set(out)) != len(out):
        raise ConfigError(f"ladder tenors {list(out)} contain duplicates", code="LADDER")
    return out


def _static(curve: "ql.YieldTermStructure") -> "ql.DiscountCurve":
    ds = list(curve.dates())
    c = ql.DiscountCurve(ds, [curve.discount(d) for d in ds], ql.Actual360())
    c.enableExtrapolation()
    return c


def _bootstrap(pricer, conv: SwapConv, tenors: Tuple[str, ...]) -> RiskCurves:
    """All the QuantLib objects that observe the evaluation date live in THIS frame: they die when it returns, before the caller's guard restores the date."""
    ref = pricer.reference_date
    cal = pricer.calendar(conv.calendar)
    dense = pricer.ql_curve()
    dh = ql.YieldTermStructureHandle(dense)
    didx = C.make_index(cal, dh)
    spot = C.pd_(cal.advance(C.qd(ref), conv.spot_lag_days, ql.Days))
    pars, terms = [], []
    for t in tenors:
        sw = _ois.make_swap(cal, didx, conv, _schedule.from_tenor(spot, t, conv.freq_months), 1, 1e6, 4.0)
        sw.setPricingEngine(_ois.engine(dh, ref))
        pars.append(sw.fairRate())
        terms.append(C.pd_(sw.maturityDate()))
    if len(set(terms)) != len(terms):
        raise ConfigError(f"ladder tenors {list(tenors)} map to duplicate maturities; use distinct, increasing tenors", code="LADDER")
    quotes = [ql.SimpleQuote(p) for p in pars]
    hidx = C.make_index(cal, ql.YieldTermStructureHandle())
    freq = _ois.FREQ[conv.frequency]
    bdc = _ois.BDC[conv.business_day_convention]
    helpers = [
        ql.OISRateHelper(
            conv.spot_lag_days, ql.Period(t), ql.QuoteHandle(q), hidx, ql.YieldTermStructureHandle(), False, conv.payment_lag_days, bdc, freq, cal, ql.Period(0, ql.Days), 0.0,
            pillar=ql.Pillar.CustomDate, customPillarDate=C.qd(term), endOfMonth=False, fixedPaymentFrequency=freq, fixedCalendar=cal, overnightCalendar=cal, convention=bdc,
        )
        for t, q, term in zip(tenors, quotes, terms)
    ]
    curve = ql.PiecewiseLogLinearDiscount(C.qd(ref), helpers, ql.Actual360())
    curve.enableExtrapolation()
    base = _static(curve)
    ups, dns = [], []
    for q, p in zip(quotes, pars):
        q.setValue(p + LADDER_BUMP)
        ups.append(_static(curve))
        q.setValue(p - LADDER_BUMP)
        dns.append(_static(curve))
        q.setValue(p)
    for q, p in zip(quotes, pars):
        q.setValue(p + PARALLEL_BUMP)
    par_up = _static(curve)
    for q, p in zip(quotes, pars):
        q.setValue(p - PARALLEL_BUMP)
    par_dn = _static(curve)
    return RiskCurves(tenors, tuple(terms), tuple(pars), base, tuple(ups), tuple(dns), par_up, par_dn)


def build_risk_curves(pricer, conv: SwapConv, tenors: Tuple[str, ...]) -> RiskCurves:
    with C.guard(pricer.reference_date):
        return _bootstrap(pricer, conv, tenors)
