"""A run of daily marks on REAL fixtures (curve rows, published SOFR fixings, the fixture calendar) through the QuantLib adapter, the way an engine would drive it:
value(prev, ts) each day, layers each day. No rateslib involved."""
import datetime as dt

import pytest

pytest.importorskip("QuantLib")
pytestmark = [pytest.mark.adapter_quantlib, pytest.mark.fixtures]

from quantlib_support import snapshots as S  # noqa: E402

from pricebt.contrib.quantlib import USD_SOFR_OIS_CONVENTIONS, swap, wrap  # noqa: E402
from pricebt.pricable import MarkContext  # noqa: E402

pytestmark.append(pytest.mark.skipif(not S.have_fixtures(), reason="data/fixtures is not present (exported from the maintainer's data infrastructure)"))


def marks(start, n):
    """The first n fixture business days from `start`: (date, pricer)."""
    cal = S.fixture_calendar("nyc")
    fx = S.fixture_fixings()
    out, d = [], start
    while len(out) < n:
        row = S.fixture_row(d) if S.is_bday(d, cal.holidays) else None
        if row is not None:
            snap = S.MarketSnapshot(ts=S.stamp(d), reference_date=d, curves={"sofr": S.curve_from_nodes(*row)}, fixings={"sofr": S.fixings_series(fx, d, "decimal")},
                                    calendars={"nyc": cal})
            out.append((d, wrap(S.pricer_of(snap))))
        d += dt.timedelta(days=1)
    return out


def test_a_run_of_daily_marks_reconciles_crosses_a_payment_date_without_a_jump_and_the_layers_explain_the_pnl():
    ms = marks(dt.date(2024, 4, 1), 110)  # a calm window that still crosses the receiver's first annual payment (2024-07-29)
    first_d, first_p = ms[0]
    cal = S.fixture_calendar("nyc")
    eff = first_d - dt.timedelta(days=250)
    while not S.is_bday(eff, cal.holidays):
        eff += dt.timedelta(days=1)
    book = {
        "recv5y": S.template(swap, "swap", USD_SOFR_OIS_CONVENTIONS, {"side": "receive", "effective": eff, "maturity": "5Y", "fixed_rate": 3.4, "notional": 1e7}).build(first_p, first_p.ts).obj,
        "pay10y": S.template(swap, "swap", USD_SOFR_OIS_CONVENTIONS, {"side": "pay", "maturity": "10Y", "notional": 1e7}).build(first_p, first_p.ts).obj,
    }
    for name, o in book.items():
        biggest_step = biggest_residual = 0.0
        cash_days = 0
        prev_v = o.value(ctx=MarkContext.standalone(first_p)) if o.effective <= first_d else None
        prev_p = first_p
        for d, p in ms[1:]:
            c = MarkContext.standalone(p, prev=prev_p)
            v = o.value(ctx=c)
            if prev_v is None:  # a forward-starting swap: the first mark after the effective date is the first one with a previous value
                prev_v = o.value(ctx=MarkContext.standalone(prev_p))
            total = v.pv - prev_v.pv + v.cash
            lay = o.decomposition(c)
            assert lay["carry"] + lay["roll"] + lay["delta"] + lay["convexity"] + lay["residual"] == pytest.approx(total, abs=1e-5), (name, d)
            risk = abs(o.dv01(ctx=MarkContext.standalone(prev_p)))
            assert abs(total) < 60 * risk, (name, d, total, risk)  # no daily move of 60bp: a missed sweep would show up here and below
            if v.cash != 0.0:
                cash_days += 1
                assert abs(v.pv + v.cash - prev_v.pv) < 0.1 * abs(v.cash), (name, d)  # the sweep day: no jump of coupon size (the flow left V and arrived as cash)
            biggest_step = max(biggest_step, abs(total))
            biggest_residual = max(biggest_residual, abs(lay["residual"]))
            assert o.dv01(ctx=c) == pytest.approx(o.dv01(ctx=MarkContext.standalone(p)), rel=1e-12)  # the cache does not change the number
            prev_v, prev_p = v, p
        assert biggest_residual < 60.0, (name, biggest_residual)  # third-order remainder of a daily move on 10mm
        if name == "recv5y":
            assert cash_days >= 1, "the receiver crosses its first annual payment inside the window"
