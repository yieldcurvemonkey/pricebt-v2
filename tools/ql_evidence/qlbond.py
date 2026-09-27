"""Prototype QuantLib fixed-rate UST construction for the Phase 1 probes (becomes src/pricebt/contrib/quantlib/bond.py in Phase 2)."""
from common import *  # noqa

DC_COUPON = None


def make_bond(issue: dt.date, maturity: dt.date, coupon_pct: float, face: float = 100.0, eom: bool = True, settlement_days: int = 0):
    """Semi-annual fixed bond, ACT/ACT ICMA, Unadjusted dates, short FRONT stub (Backward from maturity), calendar = NullCalendar (modifier none)."""
    cal = ql.NullCalendar()
    sched = ql.Schedule(qd(issue), qd(maturity), ql.Period(ql.Semiannual), cal, ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.Backward, eom)
    dc = ql.ActualActual(ql.ActualActual.ISMA, sched)
    b = ql.FixedRateBond(settlement_days, face, sched, [coupon_pct / 100.0], dc, ql.Unadjusted, 100.0, qd(issue))
    return b, dc


YIELD_DC = ql.ActualActual(ql.ActualActual.ISMA)  # placeholder; the yield day counter is taken from the bond's own


def dirty_from_yield(b, dc, y_pct, settle, comp=ql.SimpleThenCompounded):
    return b.dirtyPrice(y_pct / 100.0, dc, comp, ql.Semiannual, qd(settle))


def clean_from_yield(b, dc, y_pct, settle, comp=ql.SimpleThenCompounded):
    return b.cleanPrice(y_pct / 100.0, dc, comp, ql.Semiannual, qd(settle))


def yield_from_clean(b, dc, clean, settle, comp=ql.SimpleThenCompounded):
    return b.bondYield(ql.BondPrice(clean, ql.BondPrice.Clean), dc, comp, ql.Semiannual, qd(settle), 1e-14, 200, 0.04) * 100.0
