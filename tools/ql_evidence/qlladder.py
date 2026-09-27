"""Prototype QuantLib par-swap ladder: PiecewiseLogLinearDiscount bootstrapped from OISRateHelpers at the pillar tenors, +-0.5bp central bumps.

The bootstrap objects (relative-date helpers, the piecewise curve) are DESTROYED before returning: they observe Settings.evaluationDate and would throw on
the next date change. What survives is plain data: one static ql.DiscountCurve per scenario.
"""
from qlswap import *  # noqa


EOM = False


def _static(curve):
    ds = list(curve.dates())
    c = ql.DiscountCurve(ds, [curve.discount(d) for d in ds], ql.Actual360())
    c.enableExtrapolation()
    return c


class QLLadder:
    def __init__(self, nodes, cal, tenors, pillar="custom", lag=2, bump_bp=0.5):
        self.cal, self.tenors, self.bump = cal, [t.upper() for t in tenors], bump_bp * 1e-4
        self.ref = nodes[0][0]
        dense = ql_curve(nodes)
        dh = ql.YieldTermStructureHandle(dense)
        didx = make_index(cal, dh)
        self.spot = pd_(cal.advance(qd(self.ref), 2, ql.Days))
        self.pars, self.terms = [], []
        for t in self.tenors:
            sw = make_ois2(self.spot, t, 1, 1e6, 4.0, cal, didx)
            sw.setPricingEngine(ql.DiscountingSwapEngine(dh, True))
            self.pars.append(sw.fairRate())
            self.terms.append(pd_(sw.maturityDate()))
        quotes = [ql.SimpleQuote(p) for p in self.pars]
        hidx = make_index(cal, ql.YieldTermStructureHandle())
        helpers = []
        for t, q, term in zip(self.tenors, quotes, self.terms):
            kw = dict(pillar=ql.Pillar.CustomDate, customPillarDate=qd(term)) if pillar == "custom" else {}
            if EOM is not None:
                kw["endOfMonth"] = EOM
            helpers.append(ql.OISRateHelper(2, ql.Period(t), ql.QuoteHandle(q), hidx, ql.YieldTermStructureHandle(), False, lag, ql.ModifiedFollowing, ql.Annual, cal,
                                            ql.Period(0, ql.Days), 0.0, fixedCalendar=cal, overnightCalendar=cal, **kw))
        curve = ql.PiecewiseLogLinearDiscount(qd(self.ref), helpers, ql.Actual360())
        curve.enableExtrapolation()
        self.base = _static(curve)
        self.resid = max(abs(h.impliedQuote() - h.quote().value()) * 1e4 for h in helpers)
        self.up, self.dn = [], []
        for q, p in zip(quotes, self.pars):
            q.setValue(p + self.bump)
            self.up.append(_static(curve))
            q.setValue(p - self.bump)
            self.dn.append(_static(curve))
            q.setValue(p)
        for q, p in zip(quotes, self.pars):
            q.setValue(p + self.bump)
        self.par_up = _static(curve)
        for q, p in zip(quotes, self.pars):
            q.setValue(p - self.bump)
        self.par_dn = _static(curve)
        for q, p in zip(quotes, self.pars):
            q.setValue(p)
        del curve, helpers, quotes, hidx, didx, dh, dense  # eval-date observers must not outlive the guarded call

    def node_dates(self):
        return [pd_(d) for d in self.base.dates()]

    def ladder(self, make_swap):
        """make_swap(handle) -> priced swap. ccy per +1bp of each pillar par rate."""
        h = ql.RelinkableYieldTermStructureHandle(self.base)
        sw = make_swap(h)
        out = []
        for up, dn in zip(self.up, self.dn):
            h.linkTo(up)
            v_up = sw.NPV()
            h.linkTo(dn)
            v_dn = sw.NPV()
            out.append((v_up - v_dn) / (2 * self.bump) * 1e-4)
        return out

    def parallel(self, make_swap):
        """ccy per +1bp of ALL pillar par rates together (central difference)."""
        h = ql.RelinkableYieldTermStructureHandle(self.base)
        sw = make_swap(h)
        h.linkTo(self.par_up)
        up = sw.NPV()
        h.linkTo(self.par_dn)
        dn = sw.NPV()
        return (up - dn) / (2 * self.bump) * 1e-4
