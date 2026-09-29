"""Tests for tests/toylib (IMPLEMENTATION_PLAN.md P1.5). Calls toylib directly -- pricebt.assets
and pricebt.instrument do not fully exist yet in this phase."""
from __future__ import annotations

import math
from datetime import date

import pytest
from dateutil.relativedelta import relativedelta

import toylib.rates as tr
import toylib.swaption as ts

_D = date(2024, 6, 3)  # a Monday, well inside the toy world (no holiday calendar to dodge)


def _swap_kwargs(**over):
    kw = dict(pay_or_receive="Pay", termination_date="10y", notional_amount=1e6, fixed_rate="ATM")
    kw.update(over)
    return kw


def _swaption_kwargs(**over):
    kw = dict(pay_or_receive="Pay", buy_sell="Buy", expiration_date="1y", termination_date="10y",
              notional_amount=1e6, strike="ATM")
    kw.update(over)
    return kw


def test_toylib_is_importable_as_a_top_level_package():
    # the CRITICAL requirement of IMPLEMENTATION_PLAN.md P1.5: shared module identity with configs
    import toylib.rates as tr2
    assert tr2 is tr


def test_par_swap_npv_is_approximately_zero():
    m = tr.market(_D, "USD")
    resolved = tr.resolve_swap(m, _swap_kwargs())
    trade = tr.build_swap(m, resolved)
    assert abs(tr.npv(m, trade)) < abs(resolved["notional"]) * 1e-9


def test_pv01_is_positive_for_a_payer():
    m = tr.market(_D, "USD")
    resolved = tr.resolve_swap(m, _swap_kwargs(pay_or_receive="Pay"))
    trade = tr.build_swap(m, resolved)
    assert tr.pv01(m, trade) > 0.0


def test_ladder_of_a_single_trade_sums_to_its_pv01():
    m = tr.market(_D, "USD")
    trade = tr.build_swap(m, tr.resolve_swap(m, _swap_kwargs()))
    ladder = tr.delta_ladder(m, [trade], [1.0], ("2Y", "5Y", "10Y", "30Y"))
    assert sum(ladder.values()) == pytest.approx(tr.pv01(m, trade), rel=1e-9)


def test_ladder_is_linear_in_weights():
    # two trades in DIFFERENT pillars (5Y, 10Y), each weight checked against its own pv01 * weight
    # -- this is what actually exercises the weight scaling (additivity alone doesn't, since a
    # missing "* w" is invisible when every trade lands in its own bucket).
    m = tr.market(_D, "USD")
    t1 = tr.build_swap(m, tr.resolve_swap(m, _swap_kwargs(termination_date="5y")))
    t2 = tr.build_swap(m, tr.resolve_swap(m, _swap_kwargs(termination_date="10y")))
    tenors = ("2Y", "5Y", "10Y", "30Y")
    w1, w2 = 2.5, -0.7
    ladder = tr.delta_ladder(m, [t1, t2], [w1, w2], tenors)
    assert ladder["5Y"] == pytest.approx(tr.pv01(m, t1) * w1, rel=1e-9)
    assert ladder["10Y"] == pytest.approx(tr.pv01(m, t2) * w2, rel=1e-9)
    assert ladder["2Y"] == 0.0 and ladder["30Y"] == 0.0
    doubled = tr.delta_ladder(m, [t1], [2 * w1], tenors)
    assert doubled["5Y"] == pytest.approx(2 * ladder["5Y"], rel=1e-9)


def test_rate_path_is_deterministic():
    assert tr.market(_D, "USD").zero_rate == tr.market(_D, "USD").zero_rate


def test_zero_rate_matches_the_closed_form_sinusoid():
    # hand-computes r(d) = base + amp*sin(2*pi*n/period) from _CCY_PARAMS and business_day_index
    # independently of _zero_rate, so a wrong shape (e.g. sin -> cos) fails this test.
    base, amp, period = tr._CCY_PARAMS["USD"]
    n = tr.business_day_index(_D)
    expected = base + amp * math.sin(2 * math.pi * n / period)
    assert tr.market(_D, "USD").zero_rate == pytest.approx(expected, rel=1e-12)


def test_pv01_matches_a_closed_form_two_period_annuity():
    # hand-computes a 2y annuity as two exp(-r*t) terms with an explicit ACT/365 day count,
    # independently of _annuity, so a wrong day-count divisor (e.g. 365 -> 360) fails this test.
    m = tr.market(_D, "USD")
    trade = tr.build_swap(m, tr.resolve_swap(m, _swap_kwargs(termination_date="2y")))
    cpn1 = trade.effective_date + relativedelta(years=1)
    cpn2 = trade.effective_date + relativedelta(years=2)

    def df(d):
        return math.exp(-m.zero_rate * ((d - m.ref_date).days / 365.0))

    expected_annuity = (
        df(cpn1) * (cpn1 - trade.effective_date).days / 365.0 + df(cpn2) * (cpn2 - cpn1).days / 365.0
    )
    expected_pv01 = trade.notional * expected_annuity * 1e-4
    assert tr.pv01(m, trade) == pytest.approx(expected_pv01, rel=1e-9)


def test_holes_return_none(monkeypatch):
    monkeypatch.setattr(tr, "HOLES", {_D})
    assert tr.market(_D, "USD") is None


def test_swaption_rejects_an_unpriceable_pay_or_receive_at_resolve_time():
    # DESIGN §13.3: resolve MUST reject a pay_or_receive it does not price -- before pricing, not
    # inside npv()/vega() (a late fail via tr._sign would still raise, but only at price time).
    # (Straddle prices since IR_RISK_DESIGN Phase C: payer + receiver.)
    m = ts.market(_D, "USD")
    with pytest.raises(ValueError):
        ts.resolve_swaption(m, _swaption_kwargs(pay_or_receive="Sideways"))


def test_swaption_put_call_parity_at_atm():
    m = ts.market(_D, "USD")
    payer = ts.resolve_swaption(m, _swaption_kwargs(pay_or_receive="Pay"))
    receiver = ts.resolve_swaption(m, _swaption_kwargs(pay_or_receive="Receive"))
    assert ts.npv(m, payer) == pytest.approx(ts.npv(m, receiver), abs=1e-6)


def test_swaption_npv_on_its_own_expiration_date_is_intrinsic_value():
    # DESIGN.md section 13: AddTradeAction(option, 'expiration_date') prices the option ON its
    # expiry (T == 0), where the Bachelier d = (F-K)/(sigma*sqrt(T)) would divide by zero.
    m = ts.market(_D, "USD")
    resolved = ts.resolve_swaption(m, _swaption_kwargs(pay_or_receive="Pay", expiration_date=_D))
    assert resolved["expiration_date"] == _D
    F = tr._par_rate(m.curve, resolved["expiration_date"], resolved["termination_date"])
    ann = tr._annuity(m.curve, resolved["expiration_date"], resolved["termination_date"])
    expected = resolved["notional"] * ann * max(F - resolved["strike"], 0.0)
    assert ts.npv(m, resolved) == pytest.approx(expected, abs=1e-6)
    assert ts.vega(m, resolved) == 0.0


def test_swaption_sell_npv_is_negative_buy_npv():
    m = ts.market(_D, "USD")
    buy = ts.resolve_swaption(m, _swaption_kwargs(buy_sell="Buy"))
    sell = ts.resolve_swaption(m, _swaption_kwargs(buy_sell="Sell"))
    assert ts.npv(m, sell) == pytest.approx(-ts.npv(m, buy), abs=1e-6)


def test_recorders_track_evaluations_and_reset():
    m = tr.market(_D, "USD")  # isolation fixture already reset EVAL_COUNTS/CSA_SEEN for this test
    tr.resolve_swap(m, _swap_kwargs())
    assert tr.EVAL_COUNTS["market"] == 1
    assert tr.EVAL_COUNTS["resolve"] == 1
    assert tr.CSA_SEEN == [("market", _D, None), ("resolve", _D, None)]
    tr.reset_recorders()
    assert not tr.EVAL_COUNTS
    assert tr.CSA_SEEN == []
    assert tr.HOLES == set()
