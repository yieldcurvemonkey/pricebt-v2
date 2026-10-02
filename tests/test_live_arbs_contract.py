"""Live ARBS tests for the strict IRSwap contract (docs/v2/IR_STRICT_CONTRACT.md R3-3 "C").

Same lazy-import safety pattern as tests/test_live_arbs.py -- read that file's module docstring first;
it applies here unchanged. Every name imported at MODULE level below is a plain `pricebt.*`,
`check_asset` (pricebt-only at module level) or stdlib/pandas import: none touches ARBS or rateslib.
ARBS is reached only lazily, inside `pricebt.assets.namespace.AssetNamespace.eval()`, the first time an
expression of `usd_sofr_ois_interest_rate_swap` is evaluated -- which happens only inside a test
FUNCTION body below, and `tests/conftest.py` skips every `live_arbs` item unless PRICEBT_LIVE_ARBS=1.
Where a test needs rateslib itself (the independent Hessian), it takes the module the config's own
lazy import already registered (`sys.modules["rateslib"]`) and the market wrapper class by reflection
(`type(m)`), as tests/test_live_arbs_pnl.py does: never a fresh import.

Dates: 2024, plus 2025-01 for the coupon and maturity steps, all well inside `_LAST_SAFE`
(2026-08-20). Every number printed here (`-s`) is recorded in docs/v2/LIVE_ARBS_REPORT.md
"Strict contract (R3)".
"""
from __future__ import annotations

import contextlib
import io
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.backtest_objects import ir_pnl_definition
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.markets import CloseMarket
from pricebt.risk import (
    Annuity, Cashflows, CompoundedFixedRate, CRIFIRCurve, ExpiryInYears, FairPremium, ForwardPrice, IRDelta,
    IRDiscountDeltaParallel, IRFwdRate, IRGamma, IRGammaParallel, IRSpotRate, LocalAnnuityInCents, ParSpread,
    PnlExplain, PremiumCents, Price, Theta, contracts,
)
from pricebt.session import PricebtSession

pytestmark = pytest.mark.live_arbs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "pricebt-verify-asset-config" / "scripts"))
import check_asset  # noqa: E402 -- pricebt-only at module level, never imports a pricing library itself

ASSET_PATH = "configs/assets/usd_sofr_ois_interest_rate_swap.yaml"
ASSET_NAME = "usd_sofr_ois_interest_rate_swap"
N = 1_000_000
D_TRADE = date(2024, 1, 3)
SIMM_TENORS = ("2w", "1m", "3m", "6m", "1y", "2y", "3y", "5y", "10y", "15y", "20y", "30y")
_DV01 = IRDelta(aggregation_level="Type")


def _session() -> PricebtSession:
    return PricebtSession.use(assets=[ASSET_PATH])


def _swap(tenor="10y", por="Pay", fixed_rate="ATM", quantity=1.0) -> IRSwap:
    return IRSwap(pay_or_receive=por, termination_date=tenor, notional_currency="USD", notional_amount=N,
                  fixed_rate=fixed_rate, quantity_=quantity)


def _v(session, inst, d, measure) -> float:
    return float(session.pricing.value(inst, d, measure, None))


def _frame(session, inst, d, measure) -> pd.DataFrame:
    got = session.pricing.value(inst, d, measure, None)
    return pd.DataFrame(got.result() if hasattr(got, "result") else got)


def _rebuild_on(m, terms):
    """The same swap on market `m` from PUBLIC wrapper methods and the resolved terms (no config helper)."""
    return m.build_irswap(effective_date=pd.Timestamp(terms["effective_date"]).to_pydatetime(),
                          maturity_date=pd.Timestamp(terms["termination_date"]).to_pydatetime(),
                          fixed_rate=terms["fixed_rate"], notional=terms["notional"])


# ======================================================================================= R3-1 coverage


def test_every_contract_measure_is_mapped_and_evaluates_finite_on_two_dates():
    session = _session()
    cfg = session.registry[ASSET_NAME]
    reqs = contracts.CONTRACTS["IRSwap"]
    assert len(reqs) == 27, f"expected the 19 base + 8 R3-1 rows, got {len(reqs)}"
    missing = [(r.measure, f) for r in reqs for f in r.forms if (r.measure, f) not in cfg.provided_forms]
    assert not missing and not cfg.unsupported_measures, (missing, cfg.unsupported_measures)

    payer = session.pricing.resolve(_swap(), D_TRADE, None)
    for d in (date(2024, 5, 20), date(2024, 11, 4)):
        shown = []
        for r in reqs:
            if r.measure == "PnlExplain":
                f = _frame(session, payer, d, PnlExplain(CloseMarket(date=d + timedelta(days=1))))
                assert list(f["mkt_type"]) == ["IR"] and np.isfinite(f["value"].to_numpy(float)).all()
                continue
            measure = getattr(__import__("pricebt.risk", fromlist=[r.measure]), r.measure)
            if "scalar" in r.forms:
                scalar = measure(aggregation_level="Type") if r.measure in ("IRDelta", "IRVega", "IRVanna", "IRVolga", "IRBasis", "IRXccyDelta") else measure
                x = _v(session, payer, d, scalar)
                assert math.isfinite(x), f"{r.measure} on {d}: {x}"
                shown.append(f"{r.measure}={x:.6g}")
            if "bucketed" in r.forms:
                f = _frame(session, payer, d, measure)
                assert np.isfinite(f["value"].to_numpy(float)).all() if len(f) else True
            if "frame" in r.forms:
                f = _frame(session, payer, d, measure)
                assert set(contracts.FRAME_COLUMNS[r.measure]) <= set(f.columns), (r.measure, list(f.columns))
                assert len(f) > 0, f"{r.measure} empty on {d} for a live swap"
        print(f"R3 coverage {d}: " + " ".join(shown))


# ======================================================================================= identities


@pytest.mark.parametrize("d", [date(2024, 5, 20), date(2024, 11, 4)])
def test_r3_identities_payer_receiver_and_quantity(d):
    session = _session()
    asset = session.registry[ASSET_NAME]
    m = session.pricing.market(asset, d, None)
    pay = session.pricing.resolve(_swap(), D_TRADE, None)
    rec = session.pricing.resolve(_swap(por="Receive"), D_TRADE, None)
    terms = pay.resolved_terms
    K_bp, term = terms["fixed_rate"] * 1e4, pd.Timestamp(terms["termination_date"]).to_pydatetime()
    df_spot, df_term = float(m.handle()[m.spot_date()]), float(m.handle()[term])

    vals = {}
    for side, inst in (("pay", pay), ("rec", rec)):
        g = {k: _v(session, inst, d, mm) for k, mm in (("price", Price), ("dv01", _DV01), ("par", IRFwdRate), ("annuity", Annuity),
                                                         ("cents", PremiumCents), ("lac", LocalAnnuityInCents), ("fwd", ForwardPrice),
                                                         ("fair", FairPremium), ("spread", ParSpread), ("cfr", CompoundedFixedRate),
                                                         ("expiry", ExpiryInYears), ("spot", IRSpotRate))}
        assert g["cents"] == pytest.approx(g["price"] / N * 1e4, rel=1e-9, abs=1e-9)
        assert g["lac"] == pytest.approx(g["annuity"] / N, rel=1e-9)
        assert g["annuity"] == pytest.approx(1e4 * g["dv01"], rel=1e-12)
        assert g["fwd"] * df_term == pytest.approx(g["price"], rel=1e-9, abs=1e-6)
        assert g["fair"] * df_spot == pytest.approx(g["price"], rel=1e-9, abs=1e-6)
        assert abs(g["spread"] - (K_bp - g["par"])) < 0.5, f"{side} ParSpread {g['spread']} vs K - par {K_bp - g['par']}"
        assert g["cfr"] == pytest.approx(K_bp, rel=1e-12)   # usd_irs fixed leg is annual: (1 + K)^1 - 1 = K
        assert g["expiry"] == pytest.approx((term.date() - d).days / 365.0, abs=1e-12)
        assert abs(g["spot"] - g["par"]) < 100.0   # a seasoned swap's spot-starting equivalent: same curve, nearby rate
        vals[side] = g
    assert vals["pay"]["annuity"] > 0 > vals["rec"]["annuity"]
    for k in ("price", "annuity", "cents", "lac", "fwd", "fair"):
        assert vals["rec"][k] == pytest.approx(-vals["pay"][k], rel=1e-9, abs=1e-9), k
    for k in ("spread", "cfr", "par", "spot", "expiry"):
        assert vals["rec"][k] == pytest.approx(vals["pay"][k], rel=1e-9, abs=1e-9), k

    crif, ladder = _frame(session, pay, d, CRIFIRCurve), _frame(session, pay, d, IRDelta)
    assert crif["Amount"].sum() == pytest.approx(ladder["value"].sum(), rel=1e-9)
    assert set(crif["RiskType"]) == {"Risk_IRCurve"} and set(crif["Qualifier"]) == {"USD"} and set(crif["Bucket"]) == {"1"}
    assert set(crif["Label2"]) == {"OIS"} and set(crif["AmountCurrency"]) == {"USD"} and set(crif["Label1"]) <= set(SIMM_TENORS)

    scaled = session.pricing.resolve(_swap(quantity=2.5), D_TRADE, None)
    for mm, k in ((Price, "price"), (Annuity, "annuity"), (FairPremium, "fair"), (ForwardPrice, "fwd")):
        assert _v(session, scaled, d, mm) == pytest.approx(2.5 * vals["pay"][k], rel=1e-9, abs=1e-6), k
    for mm, k in ((PremiumCents, "cents"), (LocalAnnuityInCents, "lac"), (ParSpread, "spread"), (CompoundedFixedRate, "cfr")):
        assert _v(session, scaled, d, mm) == pytest.approx(vals["pay"][k], rel=1e-9, abs=1e-9), k
    assert _frame(session, scaled, d, CRIFIRCurve)["Amount"].sum() == pytest.approx(2.5 * crif["Amount"].sum(), rel=1e-9)

    p = vals["pay"]
    print(f"R3 identities {d} payer: price={p['price']:.4f} PremiumCents={p['cents']:.6f}bp Annuity={p['annuity']:.2f} "
          f"LocalAnnuityInCents={p['lac']:.6f} ForwardPrice={p['fwd']:.4f} (DF(term)={df_term:.6f}) FairPremium={p['fair']:.4f} "
          f"(DF(spot)={df_spot:.8f}) ParSpread={p['spread']:.6f}bp K-par={K_bp - p['par']:.6f}bp CompoundedFixedRate={p['cfr']:.6f}bp "
          f"IRSpotRate={p['spot']:.4f}bp IRFwdRate={p['par']:.4f}bp ExpiryInYears={p['expiry']:.6f} sumCRIF={crif['Amount'].sum():.6f}")


def test_fresh_spot_swap_spot_rate_equals_own_par_on_its_trade_date():
    session = _session()
    d = date(2024, 5, 20)
    fresh = session.pricing.resolve(_swap(), d, None)
    spot, par = _v(session, fresh, d, IRSpotRate), _v(session, fresh, d, IRFwdRate)
    print(f"R3 IRSpotRate fresh {d}: spot={spot:.8f} par={par:.8f}")
    assert spot == pytest.approx(par, abs=1e-8)


# ======================================================================================= Cashflows


def test_cashflows_flow_leaves_the_frame_on_exactly_the_step_npv_drops_it():
    """10y payer resolved 2024-01-03, first coupon Payment 2025-01-08 (ARBS npv keeps it ON that date
    and drops it the day after): Cashflows lists it with payment_date = Payment + 1d through 2025-01-08
    and no longer on 2025-01-09, the step on which npv drops and cash_paid_to_date gains it. The 1y
    payer's final coupon (same Payment date, fully fixed) must reconcile to the cent."""
    session = _session()
    asset = session.registry[ASSET_NAME]
    for tenor in ("10y", "1y"):
        inst = session.pricing.resolve(_swap(tenor), D_TRADE, None)
        m_pay = session.pricing.market(asset, date(2025, 1, 8), None)
        cf = _rebuild_on(m_pay, inst.resolved_terms).cashflows(curves=m_pay.handle())
        pays = cf["Payment"].apply(lambda p: p.date())
        first_pay = min(pays)
        assert first_pay == date(2025, 1, 8), f"test setup: first Payment {first_pay}"
        hand = float(cf.loc[pays == first_pay, "Cashflow"].sum())   # both legs, holder-signed

        t_before, t0, t1 = date(2025, 1, 7), first_pay, date(2025, 1, 9)
        f0, f1 = _frame(session, inst, t0, Cashflows), _frame(session, inst, t1, Cashflows)
        due0 = f0[[(p > t0) and (p <= t1) for p in f0["payment_date"]]]
        assert set(due0["payment_date"]) == {first_pay + timedelta(days=1)}
        flow = float(due0["payment_amount"].sum())
        assert flow == pytest.approx(hand, rel=1e-9)
        assert not any(p <= t1 for p in f1["payment_date"]), "the paid coupon is still listed after npv dropped it"
        assert len(f0) - len(f1) == len(due0)

        npv_before, npv0, npv1 = (_v(session, inst, x, Price) for x in (t_before, t0, t1))
        cash0 = float(session.pricing.unit_value(inst, t0, "cash_paid_to_date", None))
        cash1 = float(session.pricing.unit_value(inst, t1, "cash_paid_to_date", None))
        print(f"R3 Cashflows {tenor}: flow={flow:.4f} (hand both-leg Cashflow {hand:.4f}) npv {t_before}={npv_before:.4f} "
              f"{t0}={npv0:.4f} {t1}={npv1:.4f}; dnpv(t0->t1)+flow={npv1 - npv0 + flow:.4f}; cash jump={cash1 - cash0:.4f}; "
              f"rows {len(f0)}->{len(f1)}")
        assert cash1 - cash0 == pytest.approx(flow, rel=1e-9)
        assert abs(npv0 - npv_before) < 0.1 * abs(flow), "npv dropped the coupon ON its payment date (expected the day after)"
        assert abs(npv1 - npv0 + flow) < 0.1 * abs(flow), "npv did not drop the listed flow on the step after Payment"
        if tenor == "1y":   # final coupon, every fixing known and Payment-day DF = 1: exact
            assert npv1 == 0.0 and npv0 == pytest.approx(flow, rel=1e-9)


# ======================================================================================= PnlExplain


def test_pnl_explain_ir_row_equals_the_npv_change_and_scales():
    session = _session()
    d1, d2 = date(2024, 5, 20), date(2024, 6, 3)
    pay = session.pricing.resolve(_swap(), D_TRADE, None)
    explain = PnlExplain(CloseMarket(date=d2))
    rows = _frame(session, pay, d1, explain)
    dnpv = _v(session, pay, d2, Price) - _v(session, pay, d1, Price)
    print(f"R3 PnlExplain {d1}->{d2}: rows={rows[['mkt_type', 'value']].to_dict('records')} dnpv={dnpv:.6f}")
    assert list(rows["mkt_type"]) == ["IR"]
    assert float(rows["value"].iloc[0]) == pytest.approx(dnpv, rel=1e-9)
    scaled = session.pricing.resolve(_swap(quantity=2.5), D_TRADE, None)
    assert float(_frame(session, scaled, d1, explain)["value"].iloc[0]) == pytest.approx(2.5 * dnpv, rel=1e-9)
    rec = session.pricing.resolve(_swap(por="Receive"), D_TRADE, None)
    assert float(_frame(session, rec, d1, explain)["value"].iloc[0]) == pytest.approx(-dnpv, rel=1e-9)
    # across the coupon: an ARBS market carries its own date, so the row includes the coupon npv drops
    c1, c2 = date(2025, 1, 8), date(2025, 1, 9)
    across = float(_frame(session, pay, c1, PnlExplain(CloseMarket(date=c2)))["value"].iloc[0])
    print(f"R3 PnlExplain across the coupon {c1}->{c2}: IR={across:.4f}")
    assert across == pytest.approx(_v(session, pay, c2, Price) - _v(session, pay, c1, Price), rel=1e-9)


# ======================================================================================= recorded comparisons


def _hessian(m, terms, tenors=("2Y", "5Y", "10Y", "30Y")):
    """Independent par-pillar cross-gamma (a re-implementation of the risk-curve calibration with public
    methods; never the config's _risk_model/_on_risk): (labels, matrix) in USD per bp^2."""
    rl = sys.modules["rateslib"]
    dense, ref, spot = m.handle(), m.reference_date(), m.spot_date()
    nodes, pars = {ref: 1.0}, []
    for b in tenors:
        p = m.build_irswap(effective_date=spot, tenor=b, notional=N)
        pars.append(float(m.fair_rate(p)))
        nodes[m.maturity_date(p)] = float(dense[m.maturity_date(p)])
    risk = rl.Curve(nodes=dict(sorted(nodes.items())), convention=dense.meta.convention, calendar=dense.meta.calendar,
                    interpolation="log_linear", id=f"r3-risk-{ref:%Y%m%d}")
    meta = dict(m.meta()); meta["id"] = risk.id
    rh = type(m)(rl_curve_id=risk.id, rl_curve_handle=risk, fixings=m.index(), meta_data=meta)
    insts = [rh.build_irswap(effective_date=spot, tenor=b, fixed_rate=k, notional=N) for b, k in zip(tenors, pars)]
    with contextlib.redirect_stdout(io.StringIO()):
        sv = rl.Solver(curves=[risk], instruments=insts, s=[k * 100.0 for k in pars], instrument_labels=list(tenors),
                       id=risk.id, func_tol=1e-8, conv_tol=1e-10)
    g = rl.Portfolio([_rebuild_on(rh, terms)]).gamma(solver=sv)
    return list(g.index.get_level_values(-1)), g.to_numpy(dtype=float)


@pytest.mark.parametrize("trade_date, d", [(date(2024, 1, 3), date(2024, 1, 3)), (date(2024, 5, 20), date(2024, 5, 20)),
                                           (date(2024, 1, 3), date(2024, 11, 4))])
def test_recorded_gamma_ladder_discount_delta_and_theta(trade_date, d):
    session = _session()
    asset = session.registry[ASSET_NAME]
    m = session.pricing.market(asset, d, None)
    pay = session.pricing.resolve(_swap(), trade_date, None)
    terms = pay.resolved_terms
    dv01, gpar = _v(session, pay, d, _DV01), _v(session, pay, d, IRGammaParallel)
    ladder = _frame(session, pay, d, IRGamma).set_index("mkt_point")["value"]

    labels, H = _hessian(m, terms)
    diag = dict(zip(labels, np.diag(H)))
    for b, x in ladder.items():   # the config's ladder is exactly the solver Hessian's diagonal
        assert x == pytest.approx(diag[b], rel=1e-9, abs=1e-12), b
    print(f"R3 IRGamma {d} (traded {trade_date}): diag={ {b: round(float(x), 6) for b, x in ladder.items()} } diag_sum={ladder.sum():.6f} "
          f"IRGammaParallel={gpar:.6f} ratio diag/parallel={ladder.sum() / gpar:.4f} full-Hessian sum={H.sum():.6f} ratio={H.sum() / gpar:.4f}")
    assert ladder.sum() < 0 and gpar < 0
    if trade_date == d:   # fresh 10y: its maturity IS the 10Y pillar, so the AD Hessian's total = the chain-rule FD gamma
        assert H.sum() == pytest.approx(gpar, rel=0.01), "chain-rule IRGammaParallel disagrees with the solver Hessian total"

    dd = _v(session, pay, d, IRDiscountDeltaParallel)
    h, rt = m.handle(), _rebuild_on(m, terms)
    up, dn = h.shift(1.0), h.shift(-1.0)
    proj = (float(rt.npv(curves=[up, h])) - float(rt.npv(curves=[dn, h]))) / 2.0
    full = (float(rt.npv(curves=up)) - float(rt.npv(curves=dn))) / 2.0
    npv = _v(session, pay, d, Price)
    print(f"R3 IRDiscountDeltaParallel {d}: {dd:.6f} vs dv01 {dv01:.4f} (ratio {dd / dv01:.5f}); projection-only {proj:.4f}; "
          f"discount+projection {dd + proj:.4f} vs full parallel {full:.4f}; npv {npv:.2f}")
    assert dd + proj == pytest.approx(full, rel=1e-3)
    assert abs(dd) < 0.1 * abs(dv01)

    theta, irtheta = _v(session, pay, d, Theta), float(session.pricing.unit_value(pay, d, "theta", None))
    print(f"R3 Theta {d}: Theta={theta:.6f}/day Theta*365={theta * 365:.4f} IRTheta={irtheta:.4f}")
    assert theta * 365.0 == pytest.approx(irtheta, rel=1e-12, abs=1e-9)


def test_discount_delta_sign_off_market_payer():
    """A payer struck 50bp below par is worth > 0, so discounting harder lowers it: discount delta < 0.
    (Swapping rateslib's curves=[forecast, discount] order would return the projection delta, ~ +dv01.)"""
    session = _session()
    d = date(2024, 5, 20)
    pay = session.pricing.resolve(_swap(fixed_rate="ATM-50"), d, None)
    npv, dd, dv01 = _v(session, pay, d, Price), _v(session, pay, d, IRDiscountDeltaParallel), _v(session, pay, d, _DV01)
    print(f"R3 IRDiscountDeltaParallel ATM-50 payer {d}: npv={npv:.2f} discount_delta={dd:.4f} dv01={dv01:.4f}")
    assert npv > 0 and dd < 0 and abs(dd) < 0.1 * dv01


def test_theta_on_a_coupon_day_holds_the_own_par_fixed():
    """2025-01-08 (the 10y payer's first Payment): the paid period leaves the remaining swap, so its par
    jumps; Theta (own par fixed) takes that jump out at the translated pv01, spread over the n calendar
    days to the next business day (DEV-I15; n = 1 on both dates here). Recorded with the T2 formula
    (translated npv + coupon - npv, par NOT held) computed independently from public methods; on a
    normal day the two agree."""
    session = _session()
    asset = session.registry[ASSET_NAME]
    pay = session.pricing.resolve(_swap(), D_TRADE, None)
    for d in (date(2025, 1, 7), date(2025, 1, 8)):
        m = session.pricing.market(asset, d, None)
        nxt = pd.Timestamp(d + timedelta(days=1)).to_pydatetime()
        trans = m.handle().translate(nxt)
        fixings = m.index().copy()
        fixings.loc[pd.Timestamp(d)] = float(m.handle().rate(pd.Timestamp(d).to_pydatetime(), nxt))   # the one-day gap (A-THETA)
        meta = dict(m.meta()); meta["id"] = trans.id
        rh = type(m)(rl_curve_id=trans.id, rl_curve_handle=trans, fixings=fixings.sort_index(), meta_data=meta)
        tt, t0 = _rebuild_on(rh, pay.resolved_terms), _rebuild_on(m, pay.resolved_terms)
        cf = t0.cashflows(curves=m.handle())
        coupon = float(cf.loc[cf["Payment"].apply(lambda p: p.date()) == d, "Cashflow"].sum())
        old = float(rh.npv(tt)) + coupon - float(m.npv(t0))
        dpar = (float(rh.fair_rate(tt)) - float(m.fair_rate(t0))) * 1e4
        n = (pd.Timestamp(m.calendar_advance(m.reference_date(), "1b")).date() - d).days
        expected = old - float(rh.pv01(tt)) * dpar / n
        theta = _v(session, pay, d, Theta)
        print(f"R3 Theta {d}: config={theta:.4f}/day; T2 formula (par not held)={old:.4f}; translated par move={dpar:.6f}bp; coupon today={coupon:.4f}")
        assert theta == pytest.approx(expected, rel=1e-9, abs=1e-6)
    assert abs(dpar) > 1.0 and abs(theta - old) > 1000.0, "test setup: 2025-01-08 should be the coupon day with a par jump"


def _coupon_step(swap, start, end):
    """(t0, t1, table row, Theta(t0)) of the single coupon step of a short ir_pnl_definition run with Cashflows."""
    bt = GenericEngine().run_backtest(Strategy(None, DateTrigger(DateTriggerRequirements([start]), AddTradeAction(swap))),
                                      start=start, end=end, frequency="1b", risks=[Cashflows],
                                      pnl_explain=ir_pnl_definition(vega=False, vanna=False, volga=False), show_progress=False)
    table = bt.pnl_explain_table()
    steps = list(table.index[table["cashflow_pnl"].abs() > 0])
    assert len(steps) == 1, f"test setup: expected one coupon step, got {steps}"
    t1 = steps[0]
    t0 = max(d for d in bt.results if d < t1)
    inst = next(iter(bt.results[min(bt.results)].portfolio.all_instruments))
    return t0, t1, table.loc[t1], _v(_session(), inst, t0, Theta)


def test_coupon_step_over_a_weekend_counts_the_own_par_jump_once():
    """Theta x step calendar days is PNL_theta (backtest_objects ir_pnl_definition). The own-par jump Theta takes out
    on a payment date is one-off, so it is spread over the calendar days to the next business day (DEV-I15): a
    Friday coupon step (3 days) must explain as well as a midweek one. Unspread, PNL_theta counted it 3x: residual
    -23,359.06 on 2024-03-15 -> 03-18, 170% of the 13,714.56 coupon (measured; LIVE_ARBS_REPORT.md).
    1-day: the D_TRADE 10y payer (first Payment Wed 2025-01-08). Weekend: a 10y payer effective 2023-03-13 (first
    Payment Fri 2024-03-15), struck ATM on 2024-03-12. The 1-day ratio (the -a x dpar limit of an own-par factor,
    decision 2) sets the bound."""
    session = _session()
    terms = session.pricing.resolve(_swap(), D_TRADE, None).resolved_terms
    wed = IRSwap(pay_or_receive="Pay", effective_date=terms["effective_date"], termination_date=terms["termination_date"],
                 fixed_rate=terms["fixed_rate"], notional_currency="USD", notional_amount=N, name="coupon_wed")
    fri = IRSwap(pay_or_receive="Pay", effective_date=date(2023, 3, 13), termination_date="10y", notional_currency="USD",
                 notional_amount=N, fixed_rate="ATM", name="coupon_fri")
    ratio = {}
    for label, swap, start, end, days in (("1-day", wed, date(2025, 1, 3), date(2025, 1, 14), 1),
                                          ("weekend", fri, date(2024, 3, 12), date(2024, 3, 21), 3)):
        t0, t1, row, theta0 = _coupon_step(swap, start, end)
        assert (t1 - t0).days == days, f"test setup: {label} coupon step {t0} -> {t1}"
        coupon, resid = float(row["cashflow_pnl"]), float(row["residual_pnl"])
        ratio[label] = abs(resid) / abs(coupon)
        print(f"R3 coupon step {label} {t0}->{t1}: coupon={coupon:.4f} Theta(t0)={theta0:.4f}/day PNL_theta={row['PNL_theta']:.4f} "
              f"PNL_delta={row['PNL_delta']:.4f} residual={resid:.4f} ({ratio[label]:.2%} of the coupon)")
        assert row["PNL_theta"] == pytest.approx(days * theta0, rel=1e-9)
    assert ratio["1-day"] < 0.15, ratio            # measured 8.68%
    assert ratio["weekend"] < 2.0 * ratio["1-day"], ratio   # measured 9.95%; jump counted 3x: 170%


# ======================================================================================= dead-trade par (R2-7)


def test_dead_par_keeps_pnl_explain_table_residual_small_across_maturity():
    """The dead-trade IRFwdRate decision (LIVE_ARBS_REPORT.md "Strict contract (R3)"): the final period's
    par, continuous with the last live value. 1y payer 2024-01-03 -> 2025-01-31, ir_pnl_definition with
    Cashflows: the last-alive -> maturity step and the coupon-drop step must both explain to ~0. Under
    the old dead convention (par = fixed_rate) the maturity step's PNL_delta is dv01 x (K - par) ~ -PV,
    a residual of ~4,519 USD here (measured: see the report)."""
    session = _session()
    swap = _swap("1y")
    start, end = D_TRADE, date(2025, 1, 31)
    bt = GenericEngine().run_backtest(Strategy(None, DateTrigger(DateTriggerRequirements([start]), AddTradeAction(swap))),
                                      start=start, end=end, frequency="1b", risks=[Cashflows, IRFwdRate],
                                      pnl_explain=ir_pnl_definition(vega=False, vanna=False, volga=False), show_progress=False)
    table = bt.pnl_explain_table()
    assert np.isfinite(table.to_numpy(dtype=float)).all()
    inst = next(iter(bt.results[min(bt.results)].portfolio.all_instruments))
    maturity = pd.Timestamp(inst.resolved_terms["termination_date"]).date()
    alive = [d for d in sorted(bt.results) if d < maturity]
    t_a, t_m = alive[-1], min(d for d in table.index if d >= maturity)
    pv_a = float(bt.results[t_a][inst][bt.price_measure])
    par_a, par_m = (float(bt.results[x][inst][IRFwdRate]) for x in (t_a, t_m))
    drop = table.index[table["cashflow_pnl"].abs() > 0]
    assert len(drop) == 1, f"expected one coupon step, got {list(drop)}"
    step = table.loc[drop[0]]
    stats = (f"t_a={t_a} t_m={t_m} PV(t_a)={pv_a:.4f} par {par_a:.6f}->{par_m:.6f}bp; maturity step "
             f"{table.loc[t_m, ['economic_pnl', 'PNL_delta', 'PNL_theta', 'residual_pnl']].round(6).to_dict()}; "
             f"drop step {drop[0]}: actual={step['actual_pnl']:.4f} cash={step['cashflow_pnl']:.4f} residual={step['residual_pnl']:.6f}; "
             f"run sum|residual|={table['residual_pnl'].abs().sum():.4f} max={table['residual_pnl'].abs().max():.4f}")
    print("R3 dead par: " + stats)
    assert abs(par_m - par_a) < 0.5, "IRFwdRate jumps at maturity (R2-7: continuous with the last live value)"
    assert abs(table.loc[t_m, "residual_pnl"]) < 1e-3 * abs(pv_a)
    assert abs(step["residual_pnl"]) < 1e-6 * N and step["cashflow_pnl"] == pytest.approx(-step["actual_pnl"], rel=1e-9)
    for d in sorted(bt.results)[-5:]:   # levels stay finite after the final coupon has paid (R2-7)
        if inst in bt.results[d].portfolio:
            assert math.isfinite(float(bt.results[d][inst][IRFwdRate]))


# ======================================================================================= checker


def test_check_asset_no_fail_and_every_contract_row_passes():
    results = check_asset.run_checks(ASSET_PATH, dates=[date(2024, 1, 3), date(2024, 2, 5)])
    by = {r.name: r for r in results}
    print(check_asset.to_markdown(results))
    fails = [(r.name, r.detail) for r in results if r.status == check_asset.FAIL]
    assert not fails, fails
    contract_rows = [n for n in by if n.startswith("contract[")]
    assert len(contract_rows) == 27 and all(by[n].status == check_asset.PASS for n in contract_rows), \
        [(n, by[n].status) for n in contract_rows]
    assert "ir_cashflow_drop" in by and by["ir_cashflow_drop"].status != check_asset.SKIP
