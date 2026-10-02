"""skills/pricebt-strategy-recipes/scripts/swap_pnl.py: every test from PNL_EXPLAIN_PLAN.md
sections 5.1 (toy definitions/plumbing) and 5.2 (toy backtest-level attribution), by the plan's own
test IDs (docstring tag on each test function).

Test worlds (plan's intro to section 5), built with `monkeypatch` so nothing leaks between tests
(the `isolation` autouse fixture in tests/conftest.py resets recorders -- EVAL_COUNTS/CSA_SEEN/
HOLES -- but never `_CCY_PARAMS`/`_zero_rate`, so `monkeypatch` is mandatory for those two):

- **Frozen world**: `monkeypatch.setitem(tr._CCY_PARAMS, "USD", (0.03, 0.0, 252))` -- constant z.
- **Shock world**: `monkeypatch.setattr(tr, "_zero_rate", <step function>)`.
- **Sloped world**: a tmp_path asset config using `tr.market_sloped` (PNL_EXPLAIN_PLAN.md 3.1 says
  the sloped world is reached through "a test-only config variant, written to tmp_path or
  fixtures" -- `_write_sloped_config` below), combined with the frozen-level monkeypatch so the
  curve's only time-dependence is the slope itself (static shape, real roll-down, plan section 3.1
  / 5.2 T-SLOPE-1/2).

Every expected number is derived from the toy's own closed-form formulas (tests/toylib/rates.py) or
an independent second-difference/exact-identity computation written directly in the test body --
never copied from a prior run's printed output. Two thresholds could not be met as the plan's
literal numbers (T-GAMMA-3's "6-10x doubling factor", T-ROLL's R2_TARGET >= 0.9999): both are
diagnosed and documented in docs/v2/DECISIONS_LOG.md (2026-09-29 entries), never silently loosened.
Since tr.gamma became the chain-rule second derivative (IR_STRICT_CONTRACT R3-3 B; the same formula as
toylib.irrisk.ir_gamma), T-GAMMA-3's doubling factor is the plan's own 6-10x again (DECISIONS_LOG
2026-10-01).
"""
from __future__ import annotations

import dataclasses
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pricebt-strategy-recipes" / "scripts"))
import swap_pnl  # noqa: E402

import toylib.rates as tr  # noqa: E402
from pricebt.backtests.actions import AddScaledTradeAction, AddTradeAction, ScalingActionType  # noqa: E402
from pricebt.backtests.backtest_objects import PnlAttribute, PnlDefinition  # noqa: E402
from pricebt.backtests.generic_engine import GenericEngine  # noqa: E402
from pricebt.backtests.strategy import Strategy  # noqa: E402
from pricebt.backtests.triggers import (  # noqa: E402
    DateTrigger,
    DateTriggerRequirements,
    PeriodicTrigger,
    PeriodicTriggerRequirements,
)
from pricebt.assets import load_asset  # noqa: E402
from pricebt.errors import ConfigError  # noqa: E402
from pricebt.instrument import IRSwap  # noqa: E402
from pricebt.risk import IRDelta, IRDeltaParallel, IRFwdRate, IRGammaParallel  # noqa: E402
from pricebt.session import PricebtSession  # noqa: E402

TOY_USD = str(ROOT / "tests" / "assets" / "toy_usd_irs.yaml")
TOY_EUR = str(ROOT / "tests" / "assets" / "toy_eur_irs.yaml")

# docs/v2/DECISIONS_LOG.md 2026-09-29: calibrated toy targets (plan section 5.6's procedure: 2x
# headroom over the observed value). Observed r2 = 0.999824 -> 1-r2 = 1.76e-4 -> 2x -> 3.52e-4 ->
# R2 >= 0.99965, rounded down slightly to 0.9996. Plan's literal R2_TARGET (0.9999) is not met --
# see the log entry for the diagnosis (inherent off-market moneyness residual, not a bug).
R2_TARGET_TOY_ROLL = 0.9996
RS_TARGET_TOY_ROLL = 2e-4  # tighter than the plan's RS_TARGET (1e-3); met with room to spare


# ============================================================================================ helpers


@pytest.fixture
def session():
    return PricebtSession.use(assets=[TOY_USD])


def _build_trade(market, pay_or_receive="Pay", tenor="10y", fixed_rate="ATM", notional=1_000_000.0):
    """A toylib trade object built directly (no PricebtSession/config), for the pure closed-form
    tests in section 5.1 that never touch the engine."""
    resolved = tr.resolve_swap(
        market, {"termination_date": tenor, "notional_amount": notional, "pay_or_receive": pay_or_receive, "fixed_rate": fixed_rate}
    )
    return tr.build_swap(market, resolved)


def _single_trade(bt):
    """The one instrument in a single-trade backtest's book, read off its first live date --
    avoids any ambiguity in `Instrument.__eq__` between the fresh `IRSwap` a test builds and the
    engine's resolved/renamed clone actually held (mirrors `explain_table`'s own iteration, which
    always reads instruments out of `bt.results`, never reconstructs them)."""
    first_date = min(d for d, r in bt.results.items() if len(r))
    return bt.results[first_date].portfolio.all_instruments[0]


def _run(swap, start, end, *, frequency="1b", trigger=None, pnl_def=None, extra_risks=(), cash=True, **kwargs):
    trig = trigger if trigger is not None else DateTrigger(DateTriggerRequirements([start]), AddTradeAction(swap))
    pnl_def = swap_pnl.swap_pnl_definition() if pnl_def is None else pnl_def
    risks = [*([swap_pnl.CashPaidToDate] if cash else []), *extra_risks]
    return GenericEngine().run_backtest(
        Strategy(None, trig),
        start=start,
        end=end,
        frequency=frequency,
        risks=risks,
        pnl_explain=pnl_def,
        show_progress=False,
        **kwargs,
    )


def _ledger_tie_out_holds(bt, table, rel=1e-6):
    rs = bt.result_summary
    lhs = table["actual_dpv"].sum()
    rhs = (rs["Total"].iloc[-1] - rs["Total"].iloc[0]) - (rs["Transaction Costs"].iloc[-1] - rs["Transaction Costs"].iloc[0])
    return lhs == pytest.approx(rhs, rel=rel)


_SLOPED_YAML = """schema_version: 1
asset: toy_usd_irs_sloped_{suffix}
description: >
  Test-only sloped-world variant of toy_usd_irs (PNL_EXPLAIN_PLAN.md section 5 intro / 3.1).
  Not shipped: tmp_path only, written by _write_sloped_config in this test file.
instrument: IRSwap
match: {{notional_currency: USD}}
currency: USD
defaults:
  pay_or_receive: Receive
  termination_date: 10y
  notional_currency: USD
  fixed_rate: ATM
  notional_amount: 1.0e6
imports: |
  import dataclasses
  from datetime import timedelta
  import toylib.rates as tr
  import toylib.irrisk as tri
code: |
  _SLOPE = {slope}
  def _sloped_market(d, ccy, csa):
      return tr.market_sloped(d, ccy, csa, _SLOPE)
  def _theta_rolled(m, t):
      # a ROLLED (static-shape) curve one day later: the SAME z/slope numbers, ref_date advanced --
      # as opposed to tr.theta's TranslatedCurve (forward rates held fixed). PNL_EXPLAIN_PLAN.md
      # 2.2 note 2 / T-SLOPE-2: this double-counts real roll-down that tr.theta correctly excludes.
      rolled = dataclasses.replace(m, ref_date=m.ref_date + timedelta(days=1))
      return (tr.npv(rolled, t) - tr.npv(m, t)) * 365.0
market:
  expr: '_sloped_market(pricebt_date, "USD", pricebt_csa)'
  key: toy_usd_ois_sloped_{suffix}
resolve:
  expr: 'tr.resolve_swap(market, kwargs)'
trade:
  expr: 'tr.build_swap(market, resolved)'
  build_on: each_market
functions:
  npv:      {{expr: 'tr.npv(market, trade)', unit: ccy}}
  dv01:     {{expr: 'tr.pv01(market, trade)', unit: ccy_per_bp}}
  par_rate: {{expr: 'tr.par_rate(market, trade)', unit: bp}}
  gamma:             {{expr: 'tr.gamma(market, trade)', unit: ccy_per_bp2}}
  theta:             {{expr: 'tr.theta(market, trade)', unit: ccy}}
  theta_rolled:      {{expr: '_theta_rolled(market, trade)', unit: ccy}}
  year_fraction:     {{expr: 'tr.year_fraction(market)', unit: decimal}}
  cash_paid_to_date: {{expr: 'tr.cash_paid_to_date(market, trade)', unit: ccy}}
  # the rest of the strict IRSwap contract (IR_STRICT_CONTRACT R3-0: no declarations), from toylib.irrisk
  discount_delta:    {{expr: 'tri.discount_delta(market, trade)', unit: ccy_per_bp}}
  zero_per_bp:       {{expr: '0.0', unit: ccy_per_bp}}
  zero_per_bp2:      {{expr: '0.0', unit: ccy_per_bp2}}
  zero_vol:          {{expr: '0.0', unit: bp}}
  spot_rate:         {{expr: 'tri.spot_rate(market, trade)', unit: bp}}
  theta_1d:          {{expr: 'tri.theta_1d(market, trade)', unit: ccy}}
  expiry_in_years:   {{expr: 'tri.expiry_in_years(market, trade)', unit: decimal}}
  annuity:           {{expr: 'tri.annuity(market, trade)', unit: ccy}}
  cashflows:         {{expr: 'tri.empty_cashflows()', unit: ccy, returns: frame, scale_columns: [payment_amount]}}
  par_spread:        {{expr: 'tri.par_spread(market, trade)', unit: bp}}
  fair_premium:      {{expr: 'tri.fair_premium(market, trade)', unit: ccy}}
  forward_price:     {{expr: 'tri.forward_price(market, trade)', unit: ccy}}
  premium_cents:     {{expr: 'tri.premium_cents(market, trade)', unit: bp}}
  local_annuity:     {{expr: 'tri.local_annuity_in_cents(market, trade)', unit: decimal}}
  compounded_rate:   {{expr: 'tri.compounded_fixed_rate(market, trade)', unit: bp}}
  crif:              {{expr: 'tri.crif_ir_curve_pv01(market, trade)', unit: ccy, returns: frame, scale_columns: [Amount]}}
portfolio_functions:
  delta_ladder:
    expr: 'tr.delta_ladder(market, trades, weights, ("2Y", "5Y", "10Y", "30Y"))'
    unit: ccy_per_bp
    returns: buckets
  gamma_ladder:
    expr: 'tri.gamma_ladder(market, trades, weights, ("2Y", "5Y", "10Y", "30Y"))'
    unit: ccy_per_bp2
    returns: buckets
  vega_cube:
    expr: '{{}}'
    unit: ccy_per_bp
    returns: buckets
  pnl_explain:
    expr: 'tri.pnl_explain(market, market_to, trades, weights)'
    unit: ccy
    returns: buckets
attributes:
  effective_date:   'resolved["effective_date"]'
  termination_date: 'resolved["termination_date"]'
  notional_amount:  'abs(resolved["notional"]) * pricebt_quantity'
size_attribute: notional_amount
risk_measures:
  Price: npv
  IRDelta: {{scalar: dv01, bucketed: delta_ladder}}
  IRFwdRate: par_rate
  IRGammaParallel: gamma
  IRTheta: {theta_fn}
  YearFraction: year_fraction
  CashPaidToDate: cash_paid_to_date
  IRDiscountDeltaParallel: discount_delta
  IRGamma: {{bucketed: gamma_ladder}}
  IRVega: {{scalar: zero_per_bp, bucketed: vega_cube}}
  IRVanna: zero_per_bp2
  IRVolga: zero_per_bp2
  IRBasis: zero_per_bp
  IRXccyDelta: zero_per_bp
  IRSpotRate: spot_rate
  IRAnnualImpliedVol: zero_vol
  IRAnnualATMImpliedVol: zero_vol
  IRDailyImpliedVol: zero_vol
  Theta: theta_1d
  ExpiryInYears: expiry_in_years
  Annuity: annuity
  Cashflows: cashflows
  ParSpread: par_spread
  FairPremium: fair_premium
  ForwardPrice: forward_price
  PremiumCents: premium_cents
  LocalAnnuityInCents: local_annuity
  CompoundedFixedRate: compounded_rate
  CRIFIRCurve: crif
  PnlExplain: pnl_explain
"""

SLOPE = 0.001


def _sloped_session(tmp_path, suffix, theta_fn="theta"):
    path = tmp_path / f"toy_usd_irs_sloped_{suffix}.yaml"
    path.write_text(_SLOPED_YAML.format(slope=SLOPE, theta_fn=theta_fn, suffix=suffix))
    return PricebtSession.use(assets=[str(path)])


# ============================================================================================ 5.1: toy definitions and plumbing


# T-GAMMA-1 known answers (ATM payer, 2024-01-03, chain-rule gamma): REGRESSION PINS, not independent
# known answers -- owner B's tr.gamma values recorded when it gained the chain term, which only freeze
# today's toy output (the independent check is the second difference in the test body); the
# no-chain-term formula gave -0.03613 / -0.73785 / -4.13576
T_GAMMA_1_ATM_PAYER = {"2y": -0.05437, "10y": -0.81656, "30y": -4.30509}


@pytest.mark.parametrize("tenor", ["2y", "10y", "30y"])
def test_t_gamma_1_matches_independent_second_difference_atm_payer_and_receiver(tenor):
    """T-GAMMA-1: an independently-written chain-rule second derivative (h=1bp zero-rate bumps, n =
    npv, p = par in bp): G = [n+ + n- - 2n0 - ((n+ - n-)/(p+ - p-))(p+ + p- - 2p0)] / ((p+ - p-)/2)^2,
    agrees with tr.gamma to 1e-9 relative, for both directions; payer < 0; receiver = -payer.
    Non-vacuity: the formula without the chain term and the half gamma d(pv01)/dpar each miss tr.gamma
    by a clear margin, so neither could pass this test.
    Mutation: drop the chain term in tr.gamma -> the 1e-9 match and the known answer fail."""
    market = tr.market(date(2024, 1, 3), "USD")
    payer = _build_trade(market, "Pay", tenor=tenor)
    receiver = _build_trade(market, "Receive", tenor=tenor)
    h = 1e-4
    up = dataclasses.replace(market, zero_rate=market.zero_rate + h)
    down = dataclasses.replace(market, zero_rate=market.zero_rate - h)

    def points(trade):
        n = (tr.npv(up, trade), tr.npv(down, trade), tr.npv(market, trade))
        p = (tr.par_rate(up, trade), tr.par_rate(down, trade), tr.par_rate(market, trade))
        return n, p

    def independent_gamma(trade):
        (n_up, n_down, n_mid), (p_up, p_down, p_mid) = points(trade)
        slope = (n_up - n_down) / (p_up - p_down)
        return (n_up + n_down - 2.0 * n_mid - slope * (p_up + p_down - 2.0 * p_mid)) / ((p_up - p_down) / 2.0) ** 2

    def no_chain_term(trade):
        (n_up, n_down, n_mid), (p_up, p_down, _p_mid) = points(trade)
        return (n_up + n_down - 2.0 * n_mid) / ((p_up - p_down) / 2.0) ** 2

    def half_gamma(trade):
        return (tr.pv01(up, trade) - tr.pv01(down, trade)) / (tr.par_rate(up, trade) - tr.par_rate(down, trade))

    g_payer, g_receiver = tr.gamma(market, payer), tr.gamma(market, receiver)
    assert g_payer == pytest.approx(independent_gamma(payer), rel=1e-9)
    assert g_receiver == pytest.approx(independent_gamma(receiver), rel=1e-9)
    assert g_payer == pytest.approx(T_GAMMA_1_ATM_PAYER[tenor], rel=2e-4)
    assert g_payer < 0
    assert g_receiver == pytest.approx(-g_payer, rel=1e-9)
    assert abs(no_chain_term(payer) / g_payer - 1.0) > 0.02   # 4% at 30y, 10% at 10y, 34% at 2y
    assert abs(half_gamma(payer) / g_payer - 1.0) > 0.4      # ~0.5


def test_t_gamma_2_half_gamma_trap_documented_at_30y():
    """T-GAMMA-2 (documents the trap; no mutation -- it IS the trap): d(pv01)/dpar is half the gamma.
    docs/v2/DECISIONS_LOG.md 2026-09-28 (T1-A) found the ratio tenor-dependent against the old
    no-chain-term tr.gamma; against the chain-rule tr.gamma it is 0.5 at the money at every tenor
    (DECISIONS_LOG 2026-10-01). Re-verified here independently of those entries' numbers."""
    market = tr.market(date(2024, 1, 3), "USD")
    trade = _build_trade(market, "Pay", tenor="30y")
    h = 1e-4
    up = dataclasses.replace(market, zero_rate=market.zero_rate + h)
    down = dataclasses.replace(market, zero_rate=market.zero_rate - h)
    dv01_up, dv01_down = tr.pv01(up, trade), tr.pv01(down, trade)
    par_up, par_down = tr.par_rate(up, trade), tr.par_rate(down, trade)
    half_gamma = (dv01_up - dv01_down) / (par_up - par_down)
    gamma = tr.gamma(market, trade)
    ratio = half_gamma / gamma
    assert 0.45 <= ratio <= 0.55, ratio


def test_t_gamma_3_taylor_order_on_an_instant_shock():
    """T-GAMMA-3: ATM 10y, same-date synthetic shocks of 25/50/100bp. With-gamma residual <= 10% of
    delta-only residual at every size (the plan's own bound), and doubling the shock multiplies the
    with-gamma residual by 6-10x (third order: the plan's own window). docs/v2/DECISIONS_LOG.md
    2026-09-29 had lowered the window to [2.5, 5.0] because the no-chain-term tr.gamma left a
    second-order error; with the chain-rule tr.gamma the doubling is ~7.97 / ~7.93 (DECISIONS_LOG
    2026-10-01 records the window restored).
    Mutation: use half gamma -> the 10% bound fails; drop the chain term in tr.gamma -> the 6-10x
    window fails (~3.5x).
    """
    market = tr.market(date(2024, 1, 3), "USD")
    trade = _build_trade(market, "Pay", tenor="10y")
    dv01 = tr.pv01(market, trade)
    gamma = tr.gamma(market, trade)
    par0 = tr.par_rate(market, trade)
    npv0 = tr.npv(market, trade)

    with_gamma_residuals = []
    for bp in (25, 50, 100):
        shocked = dataclasses.replace(market, zero_rate=market.zero_rate + bp * 1e-4)
        dpv = tr.npv(shocked, trade) - npv0
        dpar = tr.par_rate(shocked, trade) - par0
        pnl_delta = dv01 * dpar
        pnl_gamma = 0.5 * gamma * dpar * dpar
        resid_delta_only = dpv - pnl_delta
        resid_with_gamma = dpv - pnl_delta - pnl_gamma
        assert abs(resid_with_gamma) <= 0.10 * abs(resid_delta_only), bp
        with_gamma_residuals.append(resid_with_gamma)

    doubling_25_to_50 = abs(with_gamma_residuals[1] / with_gamma_residuals[0])
    doubling_50_to_100 = abs(with_gamma_residuals[2] / with_gamma_residuals[1])
    assert 6.0 <= doubling_25_to_50 <= 10.0
    assert 6.0 <= doubling_50_to_100 <= 10.0


def test_t_theta_1_frozen_off_market_theta_matches_uniform_df_scaling(monkeypatch):
    """T-THETA-1: frozen world, off-market payer (K=2% vs z=3%). Under a flat frozen curve every
    discount factor scales by the SAME constant c=exp(z/365) under a 1-day translation, so
    theta == npv*(c-1)*365 exactly. An ATM trade (npv0~=0) has theta ~= 0.
    Mutation: per-day theta (drop *365).
    """
    monkeypatch.setitem(tr._CCY_PARAMS, "USD", (0.03, 0.0, 252))
    market = tr.market(date(2024, 1, 2), "USD")
    trade = _build_trade(market, "Pay", fixed_rate=0.02)
    npv0 = tr.npv(market, trade)
    theta = tr.theta(market, trade)
    z = 0.03
    expected = npv0 * (math.exp(z / 365.0) - 1.0) * 365.0
    assert theta == pytest.approx(expected, rel=1e-9)

    atm_trade = _build_trade(market, "Pay", fixed_rate="ATM")
    assert tr.theta(market, atm_trade) == pytest.approx(0.0, abs=1e-6)


def test_t_theta_2_frozen_world_par_invariant_over_a_year(monkeypatch):
    """T-THETA-2: frozen world, par(t) == par(t0) to 1e-9bp over a year of marks (every discount
    factor scales by the same constant as the market's ref_date advances, so the par ratio is
    unchanged). n/a mutation."""
    monkeypatch.setitem(tr._CCY_PARAMS, "USD", (0.03, 0.0, 252))
    d0 = date(2024, 1, 2)
    m0 = tr.market(d0, "USD")
    trade = _build_trade(m0, "Pay", fixed_rate=0.02)
    par0 = tr.par_rate(m0, trade)
    for offset in (30, 90, 182, 365):
        m = tr.market(d0 + timedelta(days=offset), "USD")
        assert tr.par_rate(m, trade) == pytest.approx(par0, abs=1e-9)


def test_t_yf_intensive_friday_to_monday_and_notional_invariant(session):
    """T-YF: Delta year_fraction over a Fri->Mon weekend is 3/365 (spans weekends automatically);
    identical for a x3-quantity instrument (intensive -- unit: decimal never scales with
    quantity_, per DESIGN.md 4.2, unlike an extensive unit: number would).
    Mutation: declare unit: number -> the x3 instrument would differ (verified separately: a
    tmp_path config with year_fraction's unit flipped to 'number' makes the second assertion fail).
    """
    d_fri, d_mon = date(2024, 1, 5), date(2024, 1, 8)
    assert d_fri.weekday() == 4 and d_mon.weekday() == 0
    m_fri, m_mon = tr.market(d_fri, "USD"), tr.market(d_mon, "USD")
    assert tr.year_fraction(m_mon) - tr.year_fraction(m_fri) == pytest.approx(3.0 / 365.0, rel=1e-12)

    swap1 = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, name="q1")
    swap3 = swap1.clone(quantity_=3.0)
    v1 = float(session.pricing.value(swap1, d_fri, swap_pnl.YearFraction, None))
    v3 = float(session.pricing.value(swap3, d_fri, swap_pnl.YearFraction, None))
    assert v1 == pytest.approx(v3, rel=1e-12)
    assert v1 == pytest.approx((d_fri - date(2000, 1, 1)).days / 365.0, rel=1e-12)


def test_t_cash_toy_cash_paid_to_date_is_always_zero():
    """T-CASH: the toy never pays coupons out of PV (_annuity/npv keep them), so
    cash_paid_to_date == 0 exactly, for both directions. n/a mutation."""
    market = tr.market(date(2024, 6, 3), "USD")
    for por in ("Pay", "Receive"):
        assert tr.cash_paid_to_date(market, _build_trade(market, por)) == 0.0


def test_t_def_structure_and_scaling():
    """T-DEF: scalar-form delta (IRDeltaParallel, not IRDelta); gamma second_order; scaling
    1/100/1e4 for bp/pct/decimal, gamma squared; carry always scaling_factor=1.0 (theta is already
    ccy/yr, year_fraction already years -- section 2's formula has no unit conversion for carry);
    gamma=False/carry=False omit the attribute."""
    d = swap_pnl.swap_pnl_definition()
    assert [a.attribute_name for a in d.attributes] == ["PNL_delta", "PNL_gamma", "PNL_carry"]
    delta, gamma, carry = d.attributes

    assert delta.attribute_metric is IRDeltaParallel
    assert delta.market_data_metric is IRFwdRate
    assert delta.second_order is False
    assert delta.scaling_factor == 1.0

    assert gamma.attribute_metric is IRGammaParallel
    assert gamma.market_data_metric is IRFwdRate
    assert gamma.second_order is True
    assert gamma.scaling_factor == 1.0

    assert carry.attribute_metric is swap_pnl.IRTheta
    assert carry.market_data_metric is swap_pnl.YearFraction
    assert carry.second_order is False
    assert carry.scaling_factor == 1.0

    for rate_unit, scale in (("bp", 1.0), ("pct", 100.0), ("decimal", 1e4)):
        dd = swap_pnl.swap_pnl_definition(rate_unit=rate_unit)
        assert dd.attributes[0].scaling_factor == scale
        assert dd.attributes[1].scaling_factor == scale * scale
        assert dd.attributes[2].scaling_factor == 1.0  # carry never moves with rate_unit

    delta_only = swap_pnl.swap_pnl_definition(gamma=False, carry=False)
    assert [a.attribute_name for a in delta_only.attributes] == ["PNL_delta"]
    no_carry = swap_pnl.swap_pnl_definition(carry=False)
    assert [a.attribute_name for a in no_carry.attributes] == ["PNL_delta", "PNL_gamma"]

    with pytest.raises(ConfigError):
        swap_pnl.swap_pnl_definition(rate_unit="bogus")


def test_t_def_bare_irdelta_gives_a_clear_error(session):
    """T-DEF mutation: substituting bare IRDelta (bucketed) for IRDeltaParallel in
    attribute_metric makes bt.pnl_explain() crash inside the frozen engine (a pandas "truth value
    of a DataFrame is ambiguous" error); explain_table wraps that in a clear ConfigError naming the
    real cause instead of leaking the opaque pandas one."""
    bad = PnlDefinition(
        attributes=[PnlAttribute(attribute_name="PNL_delta", attribute_metric=IRDelta, market_data_metric=IRFwdRate, scaling_factor=1.0, second_order=False)]
    )
    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, name="bad")
    bt = _run(swap, date(2024, 1, 2), date(2024, 2, 2), pnl_def=bad)
    with pytest.raises(ConfigError, match="IRDeltaParallel"):
        swap_pnl.explain_table(bt)


def test_rate_unit_for_reads_the_configs_declared_unit_and_falls_back_to_bp():
    """Not a plan-numbered test: rate_unit_for isn't itself in section 5.1/5.2's tables, but the
    plan (section 3.3) asks for it, so it gets its own direct check of every branch: a mapped
    scalar function's own declared unit (not just 'bp' by coincidence -- SimpleNamespace stand-ins
    let this use a unit the toy configs don't happen to declare); no mapping at all; a
    bucketed-only mapping (swap_pnl_definition's rate_measure is always used in scalar form, so
    that does not count as "mapped" here either); and a sanity check against the real toy config.
    """
    from types import SimpleNamespace

    from pricebt.assets import load_asset

    def cfg(risk_measures, functions):
        return SimpleNamespace(risk_measures=risk_measures, functions=functions)

    mapped_pct = cfg({"IRFwdRate": SimpleNamespace(scalar="par_rate", bucketed=None)}, {"par_rate": SimpleNamespace(unit="pct")})
    assert swap_pnl.rate_unit_for(mapped_pct) == "pct"

    no_mapping = cfg({}, {})
    assert swap_pnl.rate_unit_for(no_mapping) == "bp"

    bucketed_only = cfg({"IRFwdRate": SimpleNamespace(scalar=None, bucketed="par_ladder")}, {"par_ladder": SimpleNamespace(unit="decimal")})
    assert swap_pnl.rate_unit_for(bucketed_only) == "bp"

    assert swap_pnl.rate_unit_for(load_asset(TOY_USD)) == "bp"


_MINIMAL_IRSWAP = """schema_version: 1
asset: toy_usd_irs_minimal
description: Test-only minimal IRSwap (T-MISSING), never shipped.
instrument: IRSwap
match: {notional_currency: USD}
currency: USD
imports: |
  import toylib.rates as tr
market:
  expr: 'tr.market(pricebt_date, "USD", pricebt_csa)'
  key: toy_usd_ois_minimal
resolve:
  expr: 'tr.resolve_swap(market, kwargs)'
trade:
  expr: 'tr.build_swap(market, resolved)'
functions:
  npv:      {expr: 'tr.npv(market, trade)', unit: ccy}
  dv01:     {expr: 'tr.pv01(market, trade)', unit: ccy_per_bp}
  par_rate: {expr: 'tr.par_rate(market, trade)', unit: bp}
risk_measures:
  Price: npv
  IRDelta: dv01
  IRFwdRate: par_rate
"""


def test_t_missing_a_minimal_irswap_config_without_gamma_or_theta_fails_to_load():
    """T-MISSING: an IRSwap config mapping only npv/dv01/par_rate cannot exist any more
    (IR_STRICT_CONTRACT R3-0): load_asset raises one ConfigError naming every gap -- gamma and theta
    among them -- and declaring them under unsupported_measures does not rescue it. On toy_eur_irs
    (the whole strict contract, but none of the swap recipe's custom IRTheta/YearFraction) the full
    definition raises a clean ConfigError naming the missing custom measure -- `cash=False` so it is
    about the definition's own measures, not CashPaidToDate; gamma=False, carry=False (delta only)
    works."""
    import yaml

    raw = yaml.safe_load(_MINIMAL_IRSWAP)
    with pytest.raises(ConfigError) as err:
        load_asset(raw)
    for name in ("IRGammaParallel", "Theta", "IRGamma (bucketed)"):
        assert f"{name}" in str(err.value), name
    assert "require a mapping for every contract measure" in str(err.value)
    raw["unsupported_measures"] = {"IRGammaParallel": "no gamma call", "Theta": "no carry call"}
    with pytest.raises(ConfigError, match="unsupported_measures declares IRGammaParallel"):
        load_asset(raw)

    PricebtSession.use(assets=[TOY_EUR])
    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="EUR", notional_amount=1_000_000, name="eur")
    with pytest.raises(ConfigError, match=r"no mapping for risk measure (IRTheta|YearFraction)"):
        _run(swap, date(2024, 1, 2), date(2024, 2, 2), cash=False)

    swap_delta_only = swap.clone(name="eur_delta_only")
    bt = _run(swap_delta_only, date(2024, 1, 2), date(2024, 2, 2), pnl_def=swap_pnl.swap_pnl_definition(gamma=False, carry=False), cash=False)
    table = swap_pnl.explain_table(bt, cash=False)
    assert np.isfinite(table.to_numpy(dtype=float)).all()
    assert (table["PNL_gamma"] == 0.0).all() and (table["PNL_carry"] == 0.0).all()


def test_t_ccy_result_ccy_raises_unparameterised(session):
    """T-CCY: swap_pnl_definition's measures (besides the rate_measure itself, plain RiskMeasures)
    cannot be rewritten to r(currency=...), so run_backtest(result_ccy=...) raises gs's own clean
    RuntimeError("Unparameterised risk: ...") before any pricing happens."""
    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, name="ccy")
    with pytest.raises(RuntimeError, match="Unparameterised risk"):
        _run(swap, date(2024, 1, 2), date(2024, 2, 2), result_ccy="EUR")


def test_t_recon_single_off_market_trade_residual_matches_exact_split(session):
    """T-RECON: a single off-market payer, held most of a year then exited (exercising
    explain_table's exit-result lookup), daily. residual == exact_split's three terms minus the
    attribution pieces, to 1e-8 relative (economic == exact_split.total on the toy, cash always 0).
    Mutation: break explain_table's exit-result lookup.
    """
    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, fixed_rate="ATM+75", name="recon")
    start, end = date(2024, 1, 2), date(2025, 1, 2)
    trig = DateTrigger(DateTriggerRequirements([start]), AddTradeAction(swap, "9m"))
    bt = _run(swap, start, end, trigger=trig)
    ledger = bt.trade_ledger()
    assert len(ledger) == 1 and ledger.iloc[0]["Close"] is not None  # a real exit happened

    table = swap_pnl.explain_table(bt)
    split = swap_pnl.exact_split(bt)
    recon = split["total"] - table["PNL_delta"] - table["PNL_gamma"] - table["PNL_carry"]
    assert np.allclose(table["residual"].to_numpy(float), recon.to_numpy(float), rtol=1e-8, atol=1e-6)
    assert np.allclose(table["economic"].to_numpy(float), split["total"].to_numpy(float), rtol=1e-8, atol=1e-6)


# ============================================================================================ 5.2: toy backtest-level attribution


def test_t_frozen_delta_and_gamma_vanish_carry_explains_economic(monkeypatch, session):
    """T-FROZEN: frozen world, off-market payer held a year, daily. |PNL_delta| <= |dv01|*1e-9 and
    |PNL_gamma| ~= 0 (Delta par == 0 algebraically, floating-point only in practice); per-step
    |residual| <= 2*|npv(t-1)|*(z*k/365)^2 (k = calendar days in the step); carry accounts for
    nearly all of economic.
    Mutation: per-day theta; carry attribute on the wrong measure.
    """
    monkeypatch.setitem(tr._CCY_PARAMS, "USD", (0.03, 0.0, 252))
    start, end = date(2024, 1, 2), date(2025, 1, 2)
    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, fixed_rate=0.02, name="frozen")
    bt = _run(swap, start, end, extra_risks=[IRDeltaParallel])
    table = swap_pnl.explain_table(bt)

    dv01_series = bt.result_summary[IRDeltaParallel].astype(float)
    assert table["PNL_delta"].abs().max() <= 1e-9 * dv01_series.abs().max()
    assert table["PNL_gamma"].abs().max() < 1e-6

    inst = _single_trade(bt)
    all_dates = sorted(bt.results)
    z = 0.03
    for i in range(1, len(all_dates)):
        cur, prev = all_dates[i], all_dates[i - 1]
        k = (cur - prev).days
        npv_prev = float(bt.results[prev][inst][bt.price_measure])
        bound = 2.0 * abs(npv_prev) * (z * k / 365.0) ** 2 + 1e-9
        assert abs(table.loc[cur, "residual"]) <= bound, cur

    assert (table["PNL_carry"] - table["economic"]).abs().max() < 1e-3 * table["economic"].abs().max()


def test_t_shock_100bp_jump_gamma_beats_delta_only(monkeypatch, session):
    """T-SHOCK: shock world (tr._zero_rate step function, +100bp from a given date), a payer
    entered ATM the business day right before the jump (so the jump step is a clean shock, not
    contaminated by prior seasoning/moneyness drift -- verified empirically: a trade seasoned for
    months before the jump shows a much bigger, moneyness-driven residual that the gamma term can
    even make WORSE, which is exactly section 2.7's own point about off-market residuals, not a
    bug -- so this test isolates the shock itself, matching "ATM 10y payer" in the plan's own
    description). On the jump step: |residual| <= 1% of |PNL_delta|; delta+gamma beats delta-only
    by more than 10x.
    Mutation: half gamma.
    """
    shock_date = date(2024, 6, 3)
    entry_date = date(2024, 5, 31)

    base_zero_rate = tr._zero_rate

    def stepped(d, ccy):
        z = base_zero_rate(d, ccy)
        return z + 0.01 if ccy == "USD" and d >= shock_date else z

    monkeypatch.setattr(tr, "_zero_rate", stepped)

    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, fixed_rate="ATM", name="shock")
    bt = _run(swap, entry_date, date(2024, 7, 1))
    table = swap_pnl.explain_table(bt)

    pnl_delta = table.loc[shock_date, "PNL_delta"]
    delta_only_resid = table.loc[shock_date, "economic"] - pnl_delta
    with_gamma_resid = table.loc[shock_date, "residual"]

    assert abs(with_gamma_resid) <= 0.01 * abs(pnl_delta)
    assert abs(delta_only_resid) > 10.0 * abs(with_gamma_resid)


def test_t_slope_1_static_slope_residual_tiny_carry_nonzero_delta_picks_up_roll(tmp_path, monkeypatch):
    """T-SLOPE-1: sloped world (frozen level + a fixed nonzero slope -> real, deterministic
    roll-down), ATM payer 1y, daily, a year. residual per step stays tiny (<=0.03, ~2x the observed
    max ~0.0126 per section 5.6's headroom rule -- this is the correct-theta world); carry
    != 0; delta picks up the roll-down (nonzero, and negative for this slope's sign -- a positive
    slope means the curve seen from a later ref_date implies a lower average rate to any fixed
    maturity, i.e. the payer's par drifts down, which is a delta loss for a payer). n/a mutation
    (T-SLOPE-2 below is this scenario's own mutation test).
    """
    monkeypatch.setitem(tr._CCY_PARAMS, "USD", (0.03, 0.0, 252))
    _sloped_session(tmp_path, "s1", theta_fn="theta")
    start, end = date(2024, 1, 2), date(2025, 1, 2)
    swap = IRSwap(pay_or_receive="Pay", termination_date="1y", notional_currency="USD", notional_amount=1_000_000, fixed_rate="ATM", name="sloped")
    bt = _run(swap, start, end)
    table = swap_pnl.explain_table(bt)

    assert table["residual"].abs().max() <= 0.03
    assert abs(table["PNL_carry"].sum()) > 1.0
    assert table["PNL_delta"].sum() < 0.0


def test_t_slope_2_rolled_theta_double_counts_roll_down_correct_theta_does_not(tmp_path, monkeypatch):
    """T-SLOPE-2 (double-count non-vacuity, itself the mutation test for section 2.2): same sloped
    world, but theta computed on a ROLLED (static-shape) curve instead of the TRANSLATED one.
    Sigma(residual) ~= -Sigma(delta_term) within 5% (every Delta par IS roll-down here, and
    exact_split's delta_term = pv01(t-1)*Delta par is the independent measure of it); the correct
    (translated) theta does NOT show this drift.
    """
    monkeypatch.setitem(tr._CCY_PARAMS, "USD", (0.03, 0.0, 252))
    start, end = date(2024, 1, 2), date(2025, 1, 2)

    def run_world(theta_fn, suffix):
        _sloped_session(tmp_path, suffix, theta_fn=theta_fn)
        swap = IRSwap(pay_or_receive="Pay", termination_date="1y", notional_currency="USD", notional_amount=1_000_000, fixed_rate="ATM", name=f"sloped_{suffix}")
        bt = _run(swap, start, end)
        return swap_pnl.explain_table(bt), swap_pnl.exact_split(bt)

    table_correct, split_correct = run_world("theta", "correct")
    table_rolled, _ = run_world("theta_rolled", "rolled")

    roll_down = split_correct["delta_term"].sum()
    ratio = table_rolled["residual"].sum() / (-roll_down)
    assert 0.95 <= ratio <= 1.05

    assert abs(table_correct["residual"].sum()) < 0.05 * abs(roll_down)


def test_t_monthly_gamma_material_and_improves_residual_on_biggest_steps(session):
    """T-MONTHLY: normal (non-frozen) toy world, monthly marks, a single ATM-at-entry 10y payer
    (~50bp/month moves). On the 3 biggest delta-only-residual steps: gamma > 1% of delta, and
    with-gamma residual < delta-only residual. Window chosen short enough (6 months) that the
    trade's own moneyness drift does not yet dominate/flip the comparison on any of those 3 steps
    (a longer window does flip it on at least one step -- section 2.7's own documented moneyness
    effect, verified while building this test, not asserted here since T-MONTHLY is specifically
    about the gamma-vs-delta-only comparison).
    Mutation: half gamma.
    """
    start, end = date(2024, 1, 2), date(2024, 7, 2)
    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, fixed_rate="ATM", name="monthly")
    trig = DateTrigger(DateTriggerRequirements([start]), AddTradeAction(swap))
    bt = _run(swap, start, end, frequency="1m", trigger=trig)
    table = swap_pnl.explain_table(bt)

    delta_only_resid = table["economic"] - table["PNL_delta"]
    biggest = delta_only_resid.abs().sort_values(ascending=False).index[:3]
    assert len(biggest) == 3
    for d in biggest:
        assert abs(table.loc[d, "PNL_gamma"]) > 0.01 * abs(table.loc[d, "PNL_delta"]), d
        assert abs(table.loc[d, "residual"]) < abs(delta_only_resid[d]), d


def _roll_backtest():
    start, end = date(2024, 1, 2), date(2024, 12, 31)
    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, fixed_rate="ATM", name="roll")
    trig = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=end), AddTradeAction(swap, "1m"))
    return _run(swap, start, end, trigger=trig)


def test_t_roll_periodic_monthly_roll_r2_and_residual_share(session):
    """T-ROLL: momentum/periodic-roll strategy (monthly new ATM 10y, trade_duration 1m), daily,
    2024. r2 >= R2_TARGET_TOY_ROLL and residual_share <= RS_TARGET_TOY_ROLL (calibrated per
    docs/v2/DECISIONS_LOG.md 2026-09-29 -- the plan's literal R2_TARGET is inherently missed by
    this monthly-roll world's off-market moneyness residual). Exits and entries both handled: 11
    closed rolls plus the final still-open one.
    Mutation: break the exit-results branch in explain_table.
    """
    bt = _roll_backtest()
    table = swap_pnl.explain_table(bt)
    stats = swap_pnl.explain_stats(table)
    assert stats["r2"] >= R2_TARGET_TOY_ROLL
    assert stats["residual_share"] <= RS_TARGET_TOY_ROLL
    assert len(bt.trade_ledger()) == 12
    assert bt.trade_ledger()["Close"].isna().sum() == 1


def test_t_ledger_the_t_roll_run_ties_out_independently_of_attribution(session):
    """T-LEDGER: the T-ROLL run's ledger tie-out: Sigma(actual_dpv) ==
    (Total[-1]-Total[0]) - (costs[-1]-costs[0]), no cash accrual, to 1e-6 relative -- independent of
    the delta/gamma/carry attribution entirely (uses only explain_table's actual_dpv column).
    Mutation: an off-by-one step in explain_table.
    """
    bt = _roll_backtest()
    table = swap_pnl.explain_table(bt)
    assert _ledger_tie_out_holds(bt, table)
    assert (table["cash"] == 0.0).all()


def test_t_scale_quantity_2_5_scales_every_column(session):
    """T-SCALE: `AddScaledTradeAction(quantity 2.5)` scales every explain_table column by exactly
    2.5x, to 1e-9.
    Mutation: year_fraction declared extensive (unit: number) -- verified separately with a
    tmp_path config variant; PNL_carry would then scale by 2.5^2 instead of 2.5.
    """
    start, end = date(2024, 1, 2), date(2024, 4, 2)
    swap1 = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, fixed_rate="ATM+40", name="unit")
    swap25 = swap1.clone(name="scaled")
    trig1 = DateTrigger(DateTriggerRequirements([start]), AddTradeAction(swap1))
    trig25 = DateTrigger(
        DateTriggerRequirements([start]), AddScaledTradeAction(swap25, scaling_type=ScalingActionType.size, scaling_level=2.5)
    )
    bt1 = _run(swap1, start, end, trigger=trig1)
    bt25 = _run(swap25, start, end, trigger=trig25)
    t1, t25 = swap_pnl.explain_table(bt1), swap_pnl.explain_table(bt25)
    for col in ("actual_dpv", "cash", "economic", "PNL_delta", "PNL_gamma", "PNL_carry", "explained", "residual"):
        assert np.allclose(t25[col].to_numpy(float), 2.5 * t1[col].to_numpy(float), rtol=1e-9, atol=1e-9), col


def test_t_sym_payer_vs_receiver_negate_every_column(session):
    """T-SYM: the same run, payer vs receiver -- every explain_table column negates exactly
    ('ATM+40' resolves to the same K for both directions, since par_rate never reads
    pay_or_receive; every toy function is linear in the signed notional)."""
    start, end = date(2024, 1, 2), date(2024, 6, 3)
    payer = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, fixed_rate="ATM+40", name="payer")
    receiver = payer.clone(pay_or_receive="Receive", name="receiver")
    bt_p, bt_r = _run(payer, start, end), _run(receiver, start, end)
    tp, tr_ = swap_pnl.explain_table(bt_p), swap_pnl.explain_table(bt_r)
    for col in ("actual_dpv", "cash", "economic", "PNL_delta", "PNL_gamma", "PNL_carry", "explained", "residual"):
        assert np.allclose(tr_[col].to_numpy(float), -tp[col].to_numpy(float), rtol=1e-9, atol=1e-9), col


def test_t_holes_missing_market_gap_carries_correctly_no_nan(session):
    """T-HOLES: a toy HOLES date inside the held window (missing_market='drop', pricebt's default).
    The carry step spanning the gap equals theta(t-1)*(k/365) with k the real calendar gap (year
    fraction spans holes automatically, section 2.3); no NaN anywhere; ledger tie-out still holds.
    n/a mutation.
    """
    hole = date(2024, 2, 6)
    assert hole.weekday() < 5
    tr.HOLES.add(hole)  # the isolation autouse fixture clears HOLES after this test

    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, fixed_rate="ATM+30", name="holes")
    bt = _run(swap, date(2024, 1, 2), date(2024, 4, 2), extra_risks=[swap_pnl.IRTheta])
    assert hole in bt.missing_market_dates
    assert hole not in bt.results

    table = swap_pnl.explain_table(bt)
    assert np.isfinite(table.to_numpy(dtype=float)).all()

    inst = _single_trade(bt)
    dates = sorted(bt.results)
    prev_d, cur_d = next((dates[i - 1], dates[i]) for i in range(1, len(dates)) if dates[i - 1] < hole < dates[i])
    k = (cur_d - prev_d).days
    assert k > 1  # the gap really spans the dropped date
    theta_prev = float(bt.results[prev_d][inst][swap_pnl.IRTheta])
    assert table.loc[cur_d, "PNL_carry"] == pytest.approx(theta_prev * (k / 365.0), rel=1e-9)

    assert _ledger_tie_out_holds(bt, table)


def test_t_nan_no_nan_anywhere_in_the_roll_table(session):
    """T-NAN: a guard used by every backtest test here -- np.isfinite on the whole explain_table."""
    bt = _roll_backtest()
    table = swap_pnl.explain_table(bt)
    assert np.isfinite(table.to_numpy(dtype=float)).all()


def test_t_consist_cumulative_sums_match_pnl_explain_dicts_exactly(session):
    """T-CONSIST: explain_table's per-step attribution columns cumsum back to bt.pnl_explain()'s
    own cumulative dicts, to a tight (not bitwise -- cumsum() and pnl_explain's running total
    accumulate in different orders/operations) tolerance."""
    bt = _roll_backtest()
    table = swap_pnl.explain_table(bt)
    raw = bt.pnl_explain()
    for name in ("PNL_delta", "PNL_gamma", "PNL_carry"):
        cum_from_table = table[name].cumsum()
        cum_raw = pd.Series(raw[name], dtype=float).reindex(table.index)
        assert np.allclose(cum_from_table.to_numpy(float), cum_raw.to_numpy(float), rtol=1e-12, atol=1e-9), name


# ============================================================================================ explain_stats sanity (not its own plan ID; exercised via T-ROLL above too)


def test_explain_stats_shape_and_worst_residual_date(session):
    """Not a plan-numbered test: a direct shape/sanity check on explain_stats, since T-ROLL only
    exercises r2/residual_share."""
    bt = _roll_backtest()
    table = swap_pnl.explain_table(bt)
    stats = swap_pnl.explain_stats(table)
    assert set(stats) == {
        "totals",
        "r2",
        "residual_share",
        "abs_residual_total",
        "abs_economic_total",
        "abs_residual_ratio",
        "worst_residual_date",
        "worst_residual",
    }
    assert stats["worst_residual_date"] in table.index
    assert stats["worst_residual"] == pytest.approx(table.loc[stats["worst_residual_date"], "residual"])
    assert stats["totals"]["economic"] == pytest.approx(table["economic"].sum())
