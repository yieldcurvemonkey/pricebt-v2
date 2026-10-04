"""skills/pricebt-connect-pricing-library: the fictional Meridian SDK, its asset config (the whole strict
IRSwap contract, docs/v2/IR_STRICT_CONTRACT.md), and the three deliberate-mistake configs (the mistake
tests assert the WRONG behaviour, so the example stays honest); and the three contract templates
(references/config-template*.yaml): each maps the whole measure contract of `pricebt.risk.contracts`,
loads blank, fails loudly when priced unfilled, answers a gap with the paste-ready mapping skeleton
(every contract class is strict: IRSwap, IRSwaption, Bond), and -- with its library primitives filled by
the toy library -- prices every contract measure like the toy reference config, which proves its recipes
(for the bond: settlement-date Price, drop-date Cashflows, the repo financing contract)."""
from __future__ import annotations

import math
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "skills" / "pricebt-connect-pricing-library" / "example"
if str(EXAMPLE) not in sys.path:
    sys.path.insert(0, str(EXAMPLE))

import meridian_sdk as mdn  # noqa: E402
from pricebt import risk  # noqa: E402
from pricebt.assets.config import load_asset  # noqa: E402
from pricebt.backtests.actions import AddTradeAction  # noqa: E402
from pricebt.backtests.generic_engine import GenericEngine  # noqa: E402
from pricebt.backtests.strategy import Strategy  # noqa: E402
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements  # noqa: E402
from pricebt.errors import AssetEvaluationError, ConfigError, NotSupportedError  # noqa: E402
from pricebt.instrument import Bond, IRSwap, IRSwaption  # noqa: E402
from pricebt.markets import CloseMarket, PricingContext  # noqa: E402
from pricebt.markets.portfolio import Portfolio  # noqa: E402
from pricebt.risk import IRDelta, Price, contracts  # noqa: E402
from pricebt.session import PricebtSession  # noqa: E402

CONFIG = EXAMPLE / "meridian_usd_irs.yaml"
MISTAKES = EXAMPLE / "mistakes"
D1 = date(2024, 1, 2)   # Tuesday, a Meridian business day
D2 = date(2024, 4, 2)   # three months later
N = 10_000_000


def _swap(por="Pay", term="10y", fixed_rate="ATM", notional=N, **kw):
    return IRSwap(por, term, "USD", notional, fixed_rate=fixed_rate, **kw)


def _vals(cfg, inst, d):
    """(npv, dv01, par_rate) for `inst` resolved on D1 and priced on d, under config `cfg`."""
    s = PricebtSession.use(assets=[cfg])
    r = s.pricing.resolve(inst, D1, None)
    return tuple(s.pricing.unit_value(r, d, f, None) for f in ("npv", "dv01", "par_rate"))


# ------------------------------------------------------------------ SDK sanity
def test_sdk_market_raises_on_weekend_holiday_gap_and_outside_history():
    c = mdn.connect()
    with pytest.raises(mdn.MarketClosed):
        c.market("2024-01-06", "USD.SOFR")        # Saturday
    for hol in ("2024-01-15", "2024-05-27", "2024-07-04", "2024-12-25"):
        with pytest.raises(mdn.MarketClosed):
            c.market(hol, "USD.SOFR")
    for missing in ("2019-12-31", mdn.DEFAULT_GAPS[0]):
        with pytest.raises(mdn.NoData):
            c.market(missing, "USD.SOFR")
    assert mdn.connect(gaps=()).market(mdn.DEFAULT_GAPS[0], "USD.SOFR").as_of == mdn.DEFAULT_GAPS[0]


def test_sdk_receiver_positive_dv01_buckets_sum_and_batching():
    c = mdn.connect()
    m = c.market("2024-01-02", "USD.SOFR")
    start = c.spot_date(m)
    end = c.maturity(m, start, "10Y")
    pay, rec = (c.swap("USD", start, end, 3.0, 1e6, side) for side in ("PAY", "RECEIVE"))
    before = c.calls
    rows = c.price([pay, rec], m, ["PV", "DV01", "BUCKET_DV01"])
    assert c.calls == before + 1                   # one batch = one remote call
    assert rows[1]["DV01"] > 0 > rows[0]["DV01"]   # receiver-positive
    for r in rows:
        assert sum(r["BUCKET_DV01"].values()) == pytest.approx(r["DV01"], rel=1e-6)
        assert set(r["BUCKET_DV01"]) == {"USD.SOFR:1Y", "USD.SOFR:2Y", "USD.SOFR:5Y", "USD.SOFR:10Y", "USD.SOFR:30Y"}
    # DV01 is the PV change for a 1bp DECREASE: check against a finite difference (first order)
    down = mdn.MarketHandle(c, date(2024, 1, 2), "USD.SOFR", tuple(z - 1e-4 for z in m._zeros))
    fd = c.price([rec], down, ["PV"])[0]["PV"] - rows[1]["PV"]
    assert fd == pytest.approx(rows[1]["DV01"], rel=2e-3)


# ------------------------------------------------------------------ the config
def test_config_loads_without_touching_the_sdk():
    cfg = load_asset(CONFIG)
    assert cfg.name == "meridian_usd_irs"


def test_atm_payer_is_at_par_payer_positive_dv01_and_par_rate_in_bp():
    npv, dv01, par = _vals(CONFIG, _swap(), D1)
    assert abs(npv) < 1e-6 * N
    assert dv01 > 0                                   # payer gains when rates rise
    assert 100.0 < par < 1000.0                       # bp, not percent or decimal
    s = PricebtSession.current
    r = s.pricing.resolve(_swap(), D1, None)
    assert r.resolved_terms["fixed_rate"] * 1e4 == pytest.approx(par)   # decimal strike == ATM
    # 'ATM+25' offsets are bp: +25bp strike, payer PV negative by ~25 * dv01 (zero-rate DV01 vs annuity: a few %)
    r25 = s.pricing.resolve(_swap(fixed_rate="ATM+25"), D1, None)
    assert r25.resolved_terms["fixed_rate"] - r.resolved_terms["fixed_rate"] == pytest.approx(25e-4)
    assert s.pricing.unit_value(r25, D1, "npv", None) == pytest.approx(-25 * dv01, rel=0.05)
    # receiver is the mirror image
    rr = s.pricing.resolve(_swap("Receive"), D1, None)
    assert s.pricing.unit_value(rr, D1, "dv01", None) == pytest.approx(-dv01)


def test_seasoned_trade_keeps_its_resolved_maturity():
    s = PricebtSession.use(assets=[CONFIG])
    r = s.pricing.resolve(_swap(), D1, None)
    assert r.resolved_terms["termination_date"] == date(2034, 1, 4)   # spot(2024-01-02) + 10Y
    assert s.pricing.resolve(r, D2, None).resolved_terms == r.resolved_terms
    assert s.pricing.unit_value(r, D2, "npv", None) != pytest.approx(0.0, abs=1.0)   # it has P&L now


def test_ladder_sums_to_dv01_and_is_one_vendor_call_for_the_book():
    """The zero-pillar ladder sums to the curve DV01 (the `dv01` function), not to the own-rate IRDelta
    scalar: the two differ by dr/ds (R2-2), about 1.03 on this curve."""
    s = PricebtSession.use(assets=[CONFIG])
    a = _swap(term="5y", name="a", quantity_=2.0)
    b = _swap("Receive", term="10y", name="b", quantity_=0.5)
    c = _swap(term="30y", name="c")
    scalar = sum(x.quantity_ * s.pricing.unit_value(s.pricing.resolve(x, D1, None), D1, "dv01", None) for x in (a, b, c))
    own = sum(float(s.pricing.value(x, D1, IRDelta(aggregation_level="Type"), None)) for x in (a, b, c))
    assert 1.01 < scalar / own < 1.06
    asset = s.pricing.asset_for(a)
    client = s.pricing.market(asset, D1, None).client    # the config's own module-level client
    before = client.calls
    with PricingContext(D1):
        ladder = Portfolio([a, b, c]).calc(IRDelta).aggregate()
    assert client.calls - before == 1                   # resolves are cached; the ladder is ONE batched call
    assert set(ladder["mkt_point"]) <= {"1Y", "2Y", "5Y", "10Y", "30Y"}
    assert ladder["value"].sum() == pytest.approx(scalar, rel=1e-6)


def test_three_month_backtest_total_identity():
    PricebtSession.use(assets=[CONFIG])
    swap = _swap(notional=1e4, name="swap")
    end = date(2024, 4, 2)
    trigger = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=end), AddTradeAction(swap, "1m"))
    with pytest.warns(UserWarning, match="dropped"):   # MLK day, Presidents day, the NoData gap
        bt = GenericEngine().run_backtest(Strategy(None, trigger), start=D1, end=end, frequency="1b", show_progress=False)
    assert date(2024, 1, 15) in bt.missing_market_dates and date(2024, 3, 14) in bt.missing_market_dates
    summ = bt.result_summary
    expected = summ[Price] + summ["Cumulative Cash"] + summ["Transaction Costs"]
    pd.testing.assert_series_equal(summ["Total"], expected, check_names=False)
    assert len(bt.trade_ledger()) >= 3
    assert summ["Total"].abs().max() > 0.0


# ------------------------------------------------------------------ the contract from first-order vendor codes
def _bumped_pv_and_par(spec, d, h):
    """The SDK spec's PV and PAR_PCT on the market of d with every pillar zero shifted by h (test-only:
    the config cannot do this -- the SDK takes no scenario request)."""
    c = mdn.connect()
    m = c.market(d.isoformat(), "USD.SOFR")
    shifted = mdn.MarketHandle(c, d, "USD.SOFR", tuple(z + h for z in m._zeros))
    row = c.price([spec], shifted, ["PV", "PAR_PCT"])[0]
    return row["PV"], row["PAR_PCT"] * 100.0   # PAR_PCT -> bp


@pytest.mark.parametrize("por,fixed_rate", [("Pay", "ATM+50"), ("Receive", "ATM-40"), ("Pay", "ATM")])
def test_derived_measures_match_a_bump_and_reprice(por, fixed_rate):
    """IRDelta (own-rate, from DV01 at three strikes), Annuity (-dPV/dK), IRSpotRate, ExpiryInYears and
    the R2-8 zeros, against an independent zero-shift reprice of the SDK."""
    s = PricebtSession.use(assets=[CONFIG])
    r = s.pricing.resolve(_swap(por, fixed_rate=fixed_rate), D1, None)
    val = lambda m: float(s.pricing.value(r, D1, m, None))  # noqa: E731
    delta = val(IRDelta(aggregation_level="Type"))
    t = r.resolved_terms
    spec = mdn.connect().swap("USD", str(t["effective_date"]), str(t["termination_date"]), fixed_rate_pct=t["fixed_rate"] * 100.0,
                              notional=abs(t["notional"]), direction="PAY" if t["notional"] > 0 else "RECEIVE")
    (pu, ru), (pd_, rd) = _bumped_pv_and_par(spec, D1, 1e-4), _bumped_pv_and_par(spec, D1, -1e-4)
    assert delta == pytest.approx((pu - pd_) / (ru - rd), rel=1e-4)            # the TOTAL own-rate derivative
    assert (delta > 0) == (por == "Pay")
    annuity = val(risk.Annuity)
    assert (annuity > 0) == (por == "Pay")                                     # payer-positive signed notional
    if fixed_rate == "ATM":
        assert delta == pytest.approx(annuity * 1e-4, rel=1e-9)                # at the money: own-rate delta = annuity pv01
        assert abs(val(risk.IRDiscountDeltaParallel)) < 0.01 * delta            # discount-only, forwards held: ~0 at the money
        assert val(risk.IRSpotRate) == pytest.approx(val(risk.IRFwdRate), rel=1e-12)   # spot-starting: the same swap
    assert val(risk.ExpiryInYears) == pytest.approx((r.resolved_terms["termination_date"] - D1).days / 365)
    for m in (risk.IRVega(aggregation_level="Type"), risk.IRVanna(aggregation_level="Type"), risk.IRAnnualImpliedVol, risk.IRBasis(aggregation_level="Type")):
        assert val(m) == 0.0


def _spec_of(r):
    t = r.resolved_terms
    return mdn.connect().swap("USD", str(t["effective_date"]), str(t["termination_date"]), fixed_rate_pct=t["fixed_rate"] * 100.0,
                              notional=abs(t["notional"]), direction="PAY" if t["notional"] > 0 else "RECEIVE")


@pytest.mark.parametrize("por,fixed_rate", [("Pay", "ATM+50"), ("Receive", "ATM-40")])
def test_scenario_measures_match_independent_reprices(por, fixed_rate):
    """IRGammaParallel (chain rule on the +-1bp scenarios), Theta (forwards held: every DF divides by
    DF(t+1d), so PV scales by 1/DF(t+1d) with no flow in the day), the IRGamma diagonal, and
    PnlExplain, each against a computation written here from the SDK's raw prices."""
    s = PricebtSession.use(assets=[CONFIG])
    r = s.pricing.resolve(_swap(por, fixed_rate=fixed_rate), D1, None)
    val = lambda m, d=D2: float(s.pricing.value(r, d, m, None))  # noqa: E731
    spec = _spec_of(r)
    (pu, ru), (pd_, rd), (p0, r0) = _bumped_pv_and_par(spec, D2, 1e-4), _bumped_pv_and_par(spec, D2, -1e-4), _bumped_pv_and_par(spec, D2, 0.0)
    delta = (pu - pd_) / (ru - rd)
    gamma = (pu + pd_ - 2 * p0 - delta * (ru + rd - 2 * r0)) / ((ru - rd) / 2) ** 2
    assert val(risk.IRGammaParallel) == pytest.approx(gamma, rel=1e-9)
    assert (gamma < 0) == (por == "Pay")                                         # a payer swap is short convexity in its own rate
    no_chain = (pu + pd_ - 2 * p0) / ((ru - rd) / 2) ** 2
    assert abs(no_chain / gamma - 1) > 1e-3                                       # the chain term matters here: the check is not vacuous
    c = mdn.connect()
    m = c.market(D2.isoformat(), "USD.SOFR")
    df1 = c.discount_factors(m, [(D2 + timedelta(days=1)).isoformat()])[0]
    assert val(risk.Theta) == pytest.approx(p0 * (1 / df1 - 1), rel=1e-9)        # no flow on D2 + 1d
    ladder = s.pricing.value(r, D2, risk.IRGamma, None).result()
    assert dict(zip(ladder["mkt_point"], ladder["value"]))["10Y"] == pytest.approx(
        sum(c.price([spec], c.scenario(m, pillar_shifts_bp={"USD.SOFR:10Y": h}), ["PV"])[0]["PV"] for h in (1.0, -1.0)) - 2 * p0, rel=1e-9)
    with PricingContext(D1):
        rows = r.calc(risk.PnlExplain(CloseMarket(date=D2))).result()
        base = float(r.calc(Price).result())
    with PricingContext(D1, market=CloseMarket(date=D2)):
        moved = float(r.calc(Price).result())                                     # D2's curve seen from D1: no time passes
    assert list(rows["mkt_type"]) == ["IR", "CROSSES"] and rows["value"].sum() == pytest.approx(moved - base, rel=1e-12)


@pytest.mark.parametrize("por,fixed_rate", [("Pay", "ATM+50"), ("Receive", "ATM-40")])
def test_strict_contract_identities_on_meridian(por, fixed_rate):
    """The R3-1 measures from first principles: Cashflows discount back to Price exactly (every flow
    Price includes is listed, holder-signed), ForwardPrice x DF(end) and FairPremium x DF(spot) give
    Price, PremiumCents and LocalAnnuityInCents are per |notional|, ParSpread = K - IRFwdRate (both
    directions), CompoundedFixedRate = K (annual leg), sum(CRIF Amount) = the IRDelta ladder."""
    s = PricebtSession.use(assets=[CONFIG])
    r = s.pricing.resolve(_swap(por, fixed_rate=fixed_rate), D1, None)
    val = lambda m: float(s.pricing.value(r, D2, m, None))  # noqa: E731
    t, n = r.resolved_terms, abs(r.resolved_terms["notional"])
    c = mdn.connect()
    m = c.market(D2.isoformat(), "USD.SOFR")
    price = val(Price)
    flows = s.pricing.value(r, D2, risk.Cashflows, None)
    assert (flows["payment_date"] > D2).all() and set(flows["payment_type"]) == {"Fixed", "Float"}
    dfs = c.discount_factors(m, [p.isoformat() for p in flows["payment_date"]])
    assert sum(a * df for a, df in zip(flows["payment_amount"], dfs)) == pytest.approx(price, rel=1e-9)
    # IRDiscountDeltaParallel: the same flows (forwards held) rediscounted on +-1bp pillar zeros built here
    shifted = [mdn.MarketHandle(c, D2, "USD.SOFR", tuple(z + h for z in m._zeros)) for h in (1e-4, -1e-4)]
    up, dn = ([x._df_and_grad(p)[0] for p in flows["payment_date"]] for x in shifted)
    assert val(risk.IRDiscountDeltaParallel) == pytest.approx(sum(a * (u - v) for a, u, v in zip(flows["payment_amount"], up, dn)) / 2.0, rel=1e-9)
    df_spot, df_end = c.discount_factors(m, [c.spot_date(m), t["termination_date"].isoformat()])
    assert val(risk.ForwardPrice) * df_end == pytest.approx(price, rel=1e-12)
    assert val(risk.FairPremium) * df_spot == pytest.approx(price, rel=1e-12)
    assert val(risk.PremiumCents) == pytest.approx(price / n * 1e4, rel=1e-12)
    assert val(risk.LocalAnnuityInCents) == pytest.approx(val(risk.Annuity) / n, rel=1e-12)
    assert (val(risk.LocalAnnuityInCents) > 0) == (por == "Pay")
    assert val(risk.ParSpread) == pytest.approx(t["fixed_rate"] * 1e4 - val(risk.IRFwdRate), rel=1e-12)
    other = s.pricing.resolve(_swap("Receive" if por == "Pay" else "Pay", fixed_rate=t["fixed_rate"]), D1, None)
    assert float(s.pricing.value(other, D2, risk.ParSpread, None)) == pytest.approx(val(risk.ParSpread), rel=1e-12)
    assert val(risk.CompoundedFixedRate) == pytest.approx(t["fixed_rate"] * 1e4, rel=1e-12)
    crif = s.pricing.value(r, D2, risk.CRIFIRCurve, None)
    assert set(crif["Label1"]) <= set(contracts.SIMM_IR_TENORS) and set(crif["RiskType"]) == {"Risk_IRCurve"}
    assert set(crif["Label2"]) == {"OIS"} and set(crif["Bucket"]) == {"1"}   # SIMM sub-curve name ('SOFR' is not one)
    assert crif["Amount"].sum() == pytest.approx(s.pricing.value(r, D2, IRDelta, None).result()["value"].sum(), rel=1e-12)


def test_one_vendor_call_per_trade_and_date_and_a_strict_matrix(tmp_path):
    s = PricebtSession.use(assets=[CONFIG])
    r = s.pricing.resolve(_swap(fixed_rate="ATM+25"), D1, None)
    client = s.pricing.market(s.pricing.asset_for(r), D1, None).client
    s.pricing.value(r, D1, Price, None)
    before = client.calls
    for m in (IRDelta(aggregation_level="Type"), risk.Annuity, risk.IRSpotRate, risk.IRFwdRate):
        s.pricing.value(r, D1, m, None)
    assert client.calls == before                          # all from the one memoised batch
    # IRDiscountDeltaParallel rediscounts the batch's CASHFLOWS on the +-1bp scenarios (forwards held), so it
    # is not in the batch: two scenario() calls and two discount_factors() calls
    s.pricing.value(r, D1, risk.IRDiscountDeltaParallel, None)
    assert client.calls == before + 4
    sys.path.insert(0, str(ROOT / "skills" / "pricebt-risk-measures" / "scripts"))
    import measures

    assert measures.main(["matrix", "--strict", str(CONFIG)]) == 0
    rows = {(x["measure"], x["form"]): x for x in measures.capability_matrix(CONFIG)["rows"]}
    assert {x["status"] for x in rows.values()} == {measures.MAPPED}           # the whole strict contract, no declaration
    assert {m for m, _f in rows} == {r.measure for r in contracts.contract_for("IRSwap")}
    # a Bond is strict too (BOND_DESIGN 4.1): a declaration satisfies nothing, with or without --strict
    bond = yaml.safe_load((ROOT / "tests" / "assets" / "toy_usd_bond.yaml").read_text(encoding="utf-8"))
    del bond["risk_measures"]["IRVanna"]
    bond["unsupported_measures"] = {"IRVanna": "no vol bump in this library"}
    path = tmp_path / "bond_declares_a_zero.yaml"
    path.write_text(yaml.safe_dump(bond, sort_keys=False), encoding="utf-8")
    assert measures.main(["matrix", str(path)]) == 1 and measures.main(["matrix", "--strict", str(path)]) == 1


def _contract_values(s, r, d):
    """{(measure, form): value} for every form of every IRSwap contract row on d (frames as DataFrames)."""
    out = {}
    with PricingContext(d):
        for measure, form, request in _requests("IRSwap", d):
            v = r.calc(request).result()
            out[(measure, form)] = float(v) if form == "scalar" else pd.DataFrame(v)
    return out


def _forward_bp(m, start, end):
    """Independent: the simple forward over [start, end] on Meridian market handle m, bp."""
    df_s, df_e = (m._df_and_grad(x)[0] for x in (start, end))
    return (df_s / df_e - 1.0) / ((end - start).days / 365.0) * 1e4


@pytest.mark.parametrize("d", [date(2025, 1, 7), date(2025, 1, 16)])
def test_dead_swap_levels_continue_and_sensitivities_are_zero(d):
    """R2-7 on Meridian: a 1y payer resolved 2024-01-02 matures 2025-01-06 (spot 01-04 + 1y = a Saturday,
    rolled), after which the SDK's PAR_PCT is NaN (no annuity left). Its unadjusted schedule ends with a
    stub, 2025-01-04 -> 01-06. Every contract measure stays finite; IRFwdRate is that final period's par,
    which IS the live PAR_PCT while only that period is left (no jump at death), so ParSpread = K - it;
    every sensitivity is exactly 0 and the frames are empty."""
    s = PricebtSession.use(assets=[CONFIG])
    r = s.pricing.resolve(_swap(term="1y"), D1, None)
    t = r.resolved_terms
    assert t["termination_date"] == date(2025, 1, 6)
    got = _contract_values(s, r, d)
    for (measure, form), v in got.items():
        if form == "scalar":
            assert math.isfinite(v), measure
        else:
            assert np.isfinite(v.select_dtypes("number").to_numpy()).all(), measure
    c, stub = mdn.connect(), (date(2025, 1, 4), t["termination_date"])
    assert got[("IRFwdRate", "scalar")] == pytest.approx(_forward_bp(c.market(d.isoformat(), "USD.SOFR"), *stub), rel=1e-12)
    # continuity: the last live state (only the stub left) is a weekend -- value Friday's curve from Sunday
    sunday = c.scenario(c.market("2025-01-03", "USD.SOFR"), valuation_date="2025-01-05", hold="FORWARDS")
    live = c.price([_spec_of(r)], sunday, ["PAR_PCT"])[0]["PAR_PCT"] * 100.0
    assert live == pytest.approx(_forward_bp(sunday, *stub), rel=1e-12)
    assert got[("ParSpread", "scalar")] == pytest.approx(t["fixed_rate"] * 1e4 - got[("IRFwdRate", "scalar")], rel=1e-12)
    for m in ("Price", "IRDelta", "IRDiscountDeltaParallel", "IRGammaParallel", "Theta", "Annuity", "LocalAnnuityInCents",
              "PremiumCents", "FairPremium", "ForwardPrice", "ExpiryInYears", "IRVega", "IRVanna", "IRVolga", "IRBasis", "IRXccyDelta"):
        assert got[(m, "scalar")] == 0.0, m
    for m in ("IRDelta", "IRGamma"):
        assert (got[(m, "bucketed")]["value"] == 0.0).all(), m
    assert got[("Cashflows", "frame")].empty and got[("CRIFIRCurve", "frame")].empty
    assert 0.0 < got[("IRSpotRate", "scalar")] < 1000.0   # the overnight forward at spot, in bp


@pytest.mark.parametrize("resolve_on,priced,n", [
    (D1, date(2025, 1, 3), 3),                 # coupon 2025-01-04 (Saturday): the Friday step spans the weekend
    (date(2024, 2, 1), date(2025, 2, 4), 1),   # spot 2024-02-05: coupon 2025-02-05 (Wednesday), a one-day step
])
@pytest.mark.parametrize("por", ["Pay", "Receive"])
def test_theta_holds_the_own_par_across_a_coupon_counting_the_roll_once(resolve_on, priced, n, por):
    """DEV-I15 on Meridian: across a coupon the remaining swap's par jumps (the paid period leaves the
    schedule). Theta holds the own par fixed by taking that jump out at the translated annuity pv01, and
    divides the jump term by n = calendar days to the next business day, so Theta x n (what P&L explain
    books over the step) removes it exactly once. Recomputed here from the SDK's raw prices."""
    s = PricebtSession.use(assets=[CONFIG])
    r = s.pricing.resolve(_swap(por, fixed_rate="ATM+30"), resolve_on, None)
    c, spec = mdn.connect(), _spec_of(r)
    m = c.market(priced.isoformat(), "USD.SOFR")
    tomorrow = (priced + timedelta(days=1)).isoformat()
    later = c.scenario(m, valuation_date=tomorrow, hold="FORWARDS")
    row0 = c.price([spec], m, ["PV", "PAR_PCT", "CASHFLOWS"])[0]
    row1 = c.price([spec], later, ["PV", "PAR_PCT"])[0]
    cash = sum(f["amount"] if f["direction"] == "RECEIVE" else -f["amount"] for f in row0["CASHFLOWS"] if f["date"] == tomorrow)
    assert cash != 0.0                                                        # the coupon IS in the one-day window
    moved = lambda bp: c.swap("USD", spec.start, spec.end, spec.fixed_rate_pct + bp / 100.0, spec.notional, spec.direction)  # noqa: E731
    pv01 = (c.price([moved(-1)], later, ["PV"])[0]["PV"] - c.price([moved(1)], later, ["PV"])[0]["PV"]) / 2.0
    jump = (row1["PAR_PCT"] - row0["PAR_PCT"]) * 100.0
    assert abs(jump) > 1.0                                                    # material: the check is not vacuous
    raw = row1["PV"] + cash - row0["PV"]
    theta = float(s.pricing.value(r, priced, risk.Theta, None))
    assert theta == pytest.approx(raw - pv01 * jump / n, rel=1e-9)
    assert abs(theta - raw) > 100.0                                           # the held-par term matters here


def test_theta_on_the_last_live_day_is_the_final_flow_less_price():
    """A 1y payer resolved 2024-01-04 (spot 01-08) ends Wednesday 2025-01-08. On 2025-01-07 it is alive and
    the translated market (valued from 01-08) is already dead: Theta takes the carried par there (no NaN)
    and, with nothing left to carry, is exactly the final flows less today's Price."""
    s = PricebtSession.use(assets=[CONFIG])
    r = s.pricing.resolve(_swap(term="1y"), date(2024, 1, 4), None)
    assert r.resolved_terms["termination_date"] == date(2025, 1, 8)
    d = date(2025, 1, 7)
    c = mdn.connect()
    row = c.price([_spec_of(r)], c.market(d.isoformat(), "USD.SOFR"), ["PV", "CASHFLOWS"])[0]
    cash = sum(f["amount"] if f["direction"] == "RECEIVE" else -f["amount"] for f in row["CASHFLOWS"] if f["date"] == "2025-01-08")
    assert cash != 0.0 and row["PV"] != 0.0
    assert float(s.pricing.value(r, d, risk.Theta, None)) == pytest.approx(cash - row["PV"], rel=1e-9)


def test_close_market_override_values_dfs_from_the_pricing_date():
    """Under a CloseMarket override every function values the override's curve from the PRICING date, as
    Price does: ForwardPrice x DF(end) and FairPremium x DF(spot) give Price on D2's zero curve seen from
    D1 (DFs computed here from D2's raw pillar zeros), never on D2's own dates."""
    s = PricebtSession.use(assets=[CONFIG])
    r = s.pricing.resolve(_swap(fixed_rate="ATM+50"), D1, None)
    t = r.resolved_terms
    c = mdn.connect()
    seen = mdn.MarketHandle(c, D1, "USD.SOFR", c.market(D2.isoformat(), "USD.SOFR")._zeros)   # test-only: D2's zeros from D1
    df_spot, df_end = (seen._df_and_grad(x)[0] for x in (date.fromisoformat(c.spot_date(seen)), t["termination_date"]))
    with PricingContext(D1, market=CloseMarket(date=D2)):
        price, fwd, fair = (float(r.calc(m).result()) for m in (Price, risk.ForwardPrice, risk.FairPremium))
    with PricingContext(D1):
        own = float(r.calc(Price).result())
    assert abs(price - own) > 1e3                                             # the override moved Price: not vacuous
    assert fwd * df_end == pytest.approx(price, rel=1e-12)
    assert fair * df_spot == pytest.approx(price, rel=1e-12)


# ------------------------------------------------------------------ deliberate mistakes
@pytest.mark.parametrize("name", ["dv01_sign_not_flipped", "par_rate_in_percent", "maturity_not_pinned"])
def test_mistake_configs_load(name):
    assert load_asset(MISTAKES / f"{name}.yaml").name.startswith("meridian_usd_irs_mistake_")


def test_mistake_dv01_sign_not_flipped_gives_negative_payer_dv01():
    good = _vals(CONFIG, _swap(), D1)
    bad = _vals(MISTAKES / "dv01_sign_not_flipped.yaml", _swap(), D1)
    assert bad[1] < 0 and bad[1] == pytest.approx(-good[1])
    with PricingContext(D1):
        ladder_sum = Portfolio([_swap(name="p")]).calc(IRDelta).aggregate()["value"].sum()
        scalar = float(_swap(name="p").calc(IRDelta(aggregation_level="Type")).result())
    assert ladder_sum == pytest.approx(bad[1])    # the ladder is receiver-positive too: hedges trade the wrong way
    assert scalar > 0 > ladder_sum                # the ratio-derived own-rate scalar keeps its sign: ladder and scalar disagree


def test_mistake_par_rate_in_percent_is_100x_too_small():
    good = _vals(CONFIG, _swap(), D1)
    bad = _vals(MISTAKES / "par_rate_in_percent.yaml", _swap(), D1)
    assert bad[2] == pytest.approx(good[2] / 100.0)
    s = PricebtSession.current
    strike_bp = s.pricing.resolve(_swap(), D1, None).resolved_terms["fixed_rate"] * 1e4
    assert strike_bp == pytest.approx(100.0 * bad[2])   # par_rate != the ATM strike it was struck at


def test_mistake_maturity_not_pinned_lets_the_swap_drift():
    bad_cfg = MISTAKES / "maturity_not_pinned.yaml"
    s = PricebtSession.use(assets=[bad_cfg])
    r = s.pricing.resolve(_swap(), D1, None)
    assert r.resolved_terms["termination_date"] == "10Y"      # a literal string, not a date
    bad_d1, bad_d2 = (s.pricing.unit_value(r, d, "dv01", None) for d in (D1, D2))

    good = PricebtSession.use(assets=[CONFIG])
    gr = good.pricing.resolve(_swap(), D1, None)
    assert good.pricing.unit_value(gr, D1, "dv01", None) == pytest.approx(bad_d1)   # identical on the trade date
    # On D2 the bad swap is priced as maturing spot(D2) + 10Y: exactly an explicit-dates swap with that maturity.
    c = mdn.connect()
    m2 = c.market(D2.isoformat(), "USD.SOFR")
    drifted_end = date.fromisoformat(c.maturity(m2, c.spot_date(m2), "10Y"))
    assert drifted_end > gr.resolved_terms["termination_date"]
    explicit = _swap(fixed_rate=gr.resolved_terms["fixed_rate"], term=drifted_end,
                     effective_date=gr.resolved_terms["effective_date"])
    ex = good.pricing.resolve(explicit, D1, None)
    assert good.pricing.unit_value(ex, D2, "dv01", None) == pytest.approx(bad_d2, rel=1e-12)
    assert good.pricing.unit_value(gr, D2, "dv01", None) != pytest.approx(bad_d2, rel=1e-4)


# ------------------------------------------------------------------ contract templates (swap, swaption, bond)
TEMPLATES = EXAMPLE.parent / "references"
TOY_ASSETS = ROOT / "tests" / "assets"
TOY_PILLARS = ("2Y", "5Y", "10Y", "30Y")

# Toy implementations of the templates' library primitives, appended to their `code:` (a later `def`
# replaces the TODO stub; the templates' recipes run unchanged). The key-rate bump adds h times a hat
# around one pillar to the flat toy curve (the hats sum to 1), so bumping every pillar is a parallel bump.
_TOY_KEY_RATE = '''
import math as _math
from types import SimpleNamespace as _NS
import toylib.rates as tr
import toylib.irrisk as tri
_PILLARS = ("2Y", "5Y", "10Y", "30Y")


class _KeyRateCurve:
    def __init__(self, base, pillar, h):
        self.base, self.i, self.h = base, _PILLARS.index(pillar), h
        self.ref_date, self.ccy, self.csa = base.ref_date, base.ccy, base.csa

    def _w(self, t):
        ys = [tr._tenor_years(p) for p in _PILLARS]
        if t <= ys[0]:
            return float(self.i == 0)
        if t >= ys[-1]:
            return float(self.i == len(ys) - 1)
        k = max(j for j in range(len(ys) - 1) if ys[j] <= t)
        a = (t - ys[k]) / (ys[k + 1] - ys[k])
        return {k: 1.0 - a, k + 1: a}.get(self.i, 0.0)

    def discount_factor(self, d):
        t = (d - self.ref_date).days / 365.0
        return self.base.discount_factor(d) * _math.exp(-self.h * self._w(t) * t)


def lib_pillars(m):
    return _PILLARS
'''

_TOY_SWAP = _TOY_KEY_RATE + '''
def lib_market(d): return tr.market(d, "USD", None)
def lib_spot_date(m): return m.ref_date
def lib_add_tenor(m, start, tenor): return tr._pin_date(start, tenor)
def lib_par_rate(m, start, end): return tr._par_rate(m, start, end)
def lib_swap(m, r): return tr.build_swap(m, r)
class _DiscountOnly:   # the discount curve shifted, the projection forwards held (IRDiscountDeltaParallel)
    def __init__(self, m, h): self.disc, self.fwd = tri.bumped(m, h), m
def lib_pv(m, t): return tri.npv_two_curve(m.disc, m.fwd, t) if isinstance(m, _DiscountOnly) else tr.npv(m, t)
def lib_own_rate(m, t): return tr._par_rate(m, t.effective_date, t.termination_date)
def lib_spot_rate(m, t): return tri.spot_rate(m, t) / 1e4
def lib_shift(m, h): return tri.bumped(m, h)
def lib_shift_discount(m, h): return _DiscountOnly(m, h)
def lib_translate(m, days): return tri._TranslatedCurve(m, days)
def lib_annuity(m, t): return tri.annuity(m, t)
def lib_cashflows(m, t): return []
def lib_shift_pillar(m, pillar, h): return _KeyRateCurve(m, pillar, h)
def lib_discount_factor(m, d): return m.discount_factor(d)
def lib_fixed_frequency(t): return 1
def lib_at(m, d): return tri.at(m, d)
'''

_TOY_SWAPTION = _TOY_KEY_RATE + '''
import toylib.swaption as ts
def lib_market(d): return ts.market(d, "USD", None)
def lib_add_tenor(m, start, tenor): return tr._pin_date(start, tenor)
def lib_fwd_par_rate(m, start, end): return tr._par_rate(m.curve, start, end)
def lib_swaption(m, r, is_payer): return dict(r, pay_or_receive="Pay" if is_payer else "Receive")
def lib_pv(m, leg): return ts._value(m.curve, m.sigma, leg, fwd=getattr(m, "fwd", None))   # fwd: projection held
def lib_fwd_rate(m, leg): return ts._fwd(m.curve, leg)
def lib_normal_vol(m, leg): return ts.annual_vol(m, leg) / 1e4
def lib_atm_normal_vol(m, leg): return ts.atm_vol(m, leg) / 1e4
def lib_prob_exercise(m, leg): return ts.prob_exercise(m, leg)
def lib_annuity(m, leg): return ts.annuity(m, leg)
def lib_spot_rate(m, leg): return ts.spot_rate(m, leg) / 1e4
def lib_shift(m, h): return _NS(curve=tri.bumped(m.curve, h), sigma=m.sigma)
def lib_shift_discount(m, h): return _NS(curve=tri.bumped(m.curve, h), sigma=m.sigma, fwd=m.curve)
def lib_vol_shift(m, h): return _NS(curve=m.curve, sigma=m.sigma + h)
def lib_translate(m, days): return _NS(curve=tri._TranslatedCurve(m.curve, days), sigma=m.sigma)
def lib_cashflows(m, leg): return []
def lib_shift_pillar(m, pillar, h): return _NS(curve=_KeyRateCurve(m.curve, pillar, h), sigma=m.sigma)
def lib_discount_factor(m, d): return m.curve.discount_factor(d)
def lib_premium_date(m, r): return m.curve.ref_date
def lib_fixed_frequency(r): return 1
def lib_at(m, d): return _NS(curve=tri.at(m.curve, d), sigma=m.sigma)
def lib_with_vols(m, m_vols): return _NS(curve=m.curve, sigma=m_vols.sigma)
'''

_TOY_BOND = _TOY_KEY_RATE + '''
import toylib.bond as tb
def lib_market(d): return tb.market(d, "USD", None)
def lib_bond_static(m, identifier, identifier_type):
    coupon, maturity, frequency = tb.BONDS[identifier]
    return {"coupon": coupon, "maturity": maturity, "frequency": frequency}
def lib_bond(m, r): return dict(r)
def lib_settlement_date(d): return tb.settle(d)
def lib_next_business_day(d): return tb.next_weekday(d)
def lib_flows(t, after): return tb._flows(t, after)
def lib_pv(m, t): return tb._pv(m.curve, m.spread, t)
def lib_at(m, d): return _NS(curve=tri.at(m.curve, d), spread=m.spread, repo=m.repo)
def lib_with_marks(m, marks): return _NS(curve=m.curve, spread=marks.spread, repo=m.repo)
def lib_yield(m, t): return tb._yield(m.curve, m.spread, t)
def lib_pv_at_yield(t, y, x): return sum(a * _math.exp(-y * (p - x).days / 365.0) for p, a, *_ in tb._flows(t, x))
def lib_accrued(t, x): return tb._accrued_at(t, x)
def lib_pv_rolled(m, t, x):   # each flow at today's DF for its time to payment from x, spread held
    c, r = m.curve, m.curve.ref_date
    return sum(a * c.discount_factor(r + (p - x)) / c.discount_factor(r) * _math.exp(-m.spread * (p - x).days / 365.0) for p, a, *_ in tb._flows(t, x))
def lib_shift_discount(m, h): return _NS(curve=tri.bumped(m.curve, h), spread=m.spread, repo=m.repo)
def lib_annuity(m, t): return tb.annuity(m, t)
def lib_oas(m, t): return m.spread
def lib_par_spread(m, t): return m.spread
def lib_repo_rate(d, identifier): return tb.gc(d) - tb.SPECIALS.get(identifier, 0.0)
def lib_term_repo_rate(d, identifier): return tb.gc(d) - tb.SPECIALS.get(identifier, 0.0)   # the toy locks the trade date's rate
def lib_shift_pillar(m, pillar, h): return _NS(curve=_KeyRateCurve(m.curve, pillar, h), spread=m.spread, repo=m.repo)
'''

# instrument class -> (template, toy reference config, toy primitives, pricing date, two instruments)
CASES = {
    "IRSwap": ("config-template.yaml", "toy_usd_irs_full.yaml", _TOY_SWAP, date(2024, 1, 2), (
        lambda: IRSwap("Pay", "10y", "USD", 1e6, fixed_rate="ATM+25"),
        lambda: IRSwap("Receive", "5y", "USD", 2e6, fixed_rate=0.03, effective_date="1y"),
    )),
    "IRSwaption": ("config-template-swaption.yaml", "toy_usd_swaption.yaml", _TOY_SWAPTION, date(2024, 1, 2), (
        lambda: IRSwaption(pay_or_receive="Pay", buy_sell="Buy", expiration_date="1y", termination_date="10y",
                           notional_currency="USD", strike="A+25", notional_amount=1e6),
        lambda: IRSwaption(pay_or_receive="Straddle", buy_sell="Sell", expiration_date="2y", termination_date="5y",
                           notional_currency="USD", strike="ATM", notional_amount=3e6),
    )),
    # 2024-05-13: both toy bonds pay a coupon on 05-15 (Wednesday), dropped from Price on 05-14 (T+1), inside
    # Theta's step to the next business day; the second is a short on a term repo with a 5% haircut
    "Bond": ("config-template-bond.yaml", "toy_usd_bond.yaml", _TOY_BOND, date(2024, 5, 13), (
        lambda: Bond(buy_sell="Buy", identifier="TOY 4.25 2034-11-15", size=1e6, settlement_currency="USD"),
        lambda: Bond(buy_sell="Sell", identifier="TOY 3.5 2027-05-15", size=5e5, settlement_currency="USD", repo_term="term", repo_haircut=0.05),
    )),
}
# (instrument, measure) -> rel tolerance where the template's recipe is a different (equally valid) method
# than the toy's: a finite-difference vega/DV01 vs an analytic one, yield bumps vs curve bumps on the bond
TOL = {("IRSwaption", "IRVega"): 2e-5, ("Bond", "LightningDV01"): 1e-5, ("Bond", "IRDelta"): 1e-7, ("Bond", "IRGammaParallel"): 1e-6,
       ("Bond", "ModifiedDuration"): 1e-6, ("Bond", "Convexity"): 1e-6}


def _template_raw(instrument):
    return yaml.safe_load((TEMPLATES / CASES[instrument][0]).read_text(encoding="utf-8"))


PARALLEL = ("parallel_delta", "scalar")   # test-only: the whole-curve parallel delta the key-rate ladder must sum to


def _filled(instrument):
    raw = _template_raw(instrument)
    raw["code"] += CASES[instrument][2]
    if instrument != "Bond":   # an unmapped extra function, priced by _values: the template's own +-H parallel bump
        raw["functions"]["parallel_delta"] = {"expr": "(pv(lib_shift(market, H), trade) - pv(lib_shift(market, -H), trade)) / 2.0", "unit": "ccy_per_bp"}
    return raw


def _requests(instrument, d):
    """(measure, form, request) for every form of every row of the instrument's contract; PnlExplain
    explains d to a week later."""
    for req in contracts.contract_for(instrument):
        obj = getattr(risk, req.measure)
        is_fd = isinstance(obj, risk.RiskMeasureWithFiniteDifferenceParameter)
        for form in req.forms:
            if req.measure == "PnlExplain":
                yield req.measure, form, obj(CloseMarket(date=d + timedelta(days=7)))
            else:
                yield req.measure, form, obj(aggregation_level="Type") if form == "scalar" and is_fd else obj


def _values(cfg, inst, d, resolve_on=None):
    s = PricebtSession.use(assets=[cfg])
    r = s.pricing.resolve(inst, resolve_on or d, None)
    out = {}
    with PricingContext(d):
        for measure, form, request in _requests(type(inst).__name__, d):
            v = r.calc(request).result()
            out[(measure, form)] = float(v) if form == "scalar" else pd.DataFrame(v)
    if isinstance(cfg, dict) and PARALLEL[0] in cfg["functions"]:
        out[PARALLEL] = s.pricing.unit_value(r, d, PARALLEL[0], None)
    return out


def _compare(instrument, got, ref):
    parallel = got.pop(PARALLEL, None)
    assert got.keys() == ref.keys()
    for (measure, form), v in got.items():
        r = ref[(measure, form)]
        if form == "scalar":
            assert math.isfinite(v), measure
            assert v == pytest.approx(r, rel=TOL.get((instrument, measure), 1e-9), abs=1e-9), measure
        elif measure == "CRIFIRCurve":  # the template's CRIF is its key-rate ladder (the toy's: nearest pillar): the identity
            assert list(v.columns) == list(contracts.FRAME_COLUMNS["CRIFIRCurve"]) and set(v["Label1"]) <= set(contracts.SIMM_IR_TENORS)
            assert v["Amount"].sum() == pytest.approx(got[("IRDelta", "bucketed")]["value"].sum(), rel=1e-12, abs=1e-9)
        elif measure == "PnlExplain":   # the same rows by risk factor as the toy's full revaluation
            assert dict(zip(v["mkt_type"], v["value"])) == pytest.approx(dict(zip(r["mkt_type"], r["value"])), rel=1e-9, abs=1e-9)
        elif form == "frame":
            assert len(v) == len(r) and v["payment_amount"].sum() == pytest.approx(r["payment_amount"].sum())
            if instrument == "Bond":  # the drop dates (BOND_DESIGN 4.5): the trade date whose settlement reaches each flow
                assert list(pd.to_datetime(v["payment_date"])) == list(pd.to_datetime(r["payment_date"]))
        elif measure == "IRDelta":   # key-rate ladder (hats summing to 1): sums to the PARALLEL whole-curve delta, up to
            assert tuple(v["mkt_point"]) == TOY_PILLARS   # an option's third-order finite-difference terms. Not
            # IRDiscountDeltaParallel (it holds the forwards: about 0 at the money) except on a bond, whose fixed flows
            # project nothing; not the own-rate IRDelta scalar (dr/ds apart, R2-2), which the toy's ladder sums to
            want = got[("IRDiscountDeltaParallel", "scalar")] if parallel is None else parallel
            assert v["value"].sum() == pytest.approx(want, rel=1e-4, abs=1e-9)
        elif measure == "IRGamma":
            assert tuple(v["mkt_point"]) == TOY_PILLARS and np.isfinite(v["value"]).all()
        else:                        # IRVega cube: same '<tail>;<expiry>' points and values as the toy's
            assert dict(zip(v["mkt_point"], v["value"])) == pytest.approx(dict(zip(r["mkt_point"], r["value"])), rel=TOL.get((instrument, "IRVega"), 1e-9), abs=1e-9)


@pytest.mark.parametrize("instrument", CASES)
def test_template_loads_blank_and_maps_every_contract_measure(instrument):
    """Completeness against src/pricebt/risk/contracts.py itself: a new contract row fails this test."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = load_asset(TEMPLATES / CASES[instrument][0])
    assert (cfg.name, cfg.instrument, cfg.unsupported_measures) == ("TODO_asset_name", instrument, {})
    need = {(r.measure, f) for r in contracts.contract_for(instrument) for f in r.forms}
    assert need <= set(cfg.provided_forms)


@pytest.mark.parametrize("instrument", CASES)
def test_blank_template_fails_loudly_at_the_first_todo(instrument):
    s = PricebtSession.use(assets=[TEMPLATES / CASES[instrument][0]])
    with pytest.raises(AssetEvaluationError, match=r"NotImplementedError: TODO .*lib_market"):
        s.pricing.resolve(CASES[instrument][4][0](), CASES[instrument][3], None)


def _price_only(instrument):
    raw = _template_raw(instrument)
    raw["risk_measures"] = {"Price": raw["risk_measures"]["Price"]}
    with pytest.raises(ConfigError) as exc:
        load_asset(raw)
    missing = [(r.measure, f) for r in contracts.contract_for(instrument) if r.measure != "Price" for f in r.forms]
    return raw, missing, str(exc.value)


@pytest.mark.parametrize("instrument", ["IRSwap", "IRSwaption", "Bond"])
def test_strict_template_gap_gets_the_mapping_skeleton_never_a_declaration_block(instrument):
    """Every contract class is strict (IR_STRICT_CONTRACT R3-0, BOND_DESIGN 4.1): map only Price, and the
    load error lists every gap and ends with the paste-ready mapping skeleton, not an unsupported_measures
    block. The skeleton's stub expressions do not compile, so pasting it unchanged still does not load; and
    declaring the gaps instead is itself refused."""
    raw, missing, err = _price_only(instrument)
    skeleton = contracts.mapping_skeleton(instrument, missing)
    assert skeleton in err and "unsupported_measures:" not in err
    assert all(m in err for m, _f in missing)
    pasted = yaml.safe_load(skeleton)
    raw["functions"].update(pasted["functions"])
    raw["portfolio_functions"].update(pasted.get("portfolio_functions", {}))
    raw["risk_measures"].update(pasted["risk_measures"])
    with pytest.raises(ConfigError):
        load_asset(raw)
    declared = _price_only(instrument)[0]
    declared["unsupported_measures"] = {m: "TODO: no library call" for m, _f in missing}
    with pytest.raises(ConfigError, match="Bond/IRSwap/IRSwaption configs must map every contract measure; unsupported_measures cannot satisfy them"):
        load_asset(declared)


@pytest.mark.parametrize("instrument,variant", [(i, k) for i in CASES for k in (0, 1)])
def test_template_filled_with_the_toy_library_matches_the_toy_config_on_every_measure(instrument, variant):
    _t, ref_cfg, _p, d, instruments = CASES[instrument]
    got = _values(_filled(instrument), instruments[variant](), d)
    ref = _values(TOY_ASSETS / ref_cfg, instruments[variant](), d)
    _compare(instrument, got, ref)


def test_swaption_template_after_expiry_matches_the_toy_and_has_no_vol_risk():
    """R2-7: a physically settled swaption past expiry keeps finite levels and has exactly no vol risk."""
    def inst():
        return IRSwaption(pay_or_receive="Pay", buy_sell="Buy", expiration_date="1m", termination_date="5y",
                          notional_currency="USD", strike="ATM", notional_amount=1e6)
    later = date(2024, 3, 4)
    got = _values(_filled("IRSwaption"), inst(), later, resolve_on=date(2024, 1, 2))
    ref = _values(TOY_ASSETS / "toy_usd_swaption.yaml", inst(), later, resolve_on=date(2024, 1, 2))
    _compare("IRSwaption", got, ref)
    assert [got[(m, "scalar")] for m in ("IRVega", "IRVanna", "IRVolga")] == [0.0, 0.0, 0.0]


def test_template_bump_size_and_method_reach_the_recipe_and_unreferenced_parameters_raise():
    """pricebt DEV-I10: the delta expression names pricebt_bump_size and pricebt_finite_difference_method."""
    s = PricebtSession.use(assets=[_filled("IRSwap")])
    r = s.pricing.resolve(CASES["IRSwap"][4][0](), date(2024, 1, 2), None)

    def val(**params):
        return float(s.pricing.value(r, date(2024, 1, 2), IRDelta(aggregation_level="Type", **params), None))

    base = val()
    assert val(bump_size=1.0) == base                        # the default is a centred 1bp bump
    for params in ({"bump_size": 10.0}, {"finite_difference_method": "Up"}):
        assert val(**params) != base and val(**params) == pytest.approx(base, rel=1e-2)
    with pytest.raises(NotSupportedError, match="does not reference pricebt_scale_factor"):
        val(scale_factor=2.0)


def test_swaption_template_bachelier_inversion_recovers_the_normal_vol():
    import toylib.swaption as ts

    raw, ns = _template_raw("IRSwaption"), {}
    exec(raw["imports"] + raw["code"], ns)
    for F, K, sigma, T, payer in [(0.04, 0.04, 0.008, 1.0, True), (0.04, 0.045, 0.006, 0.5, True), (0.035, 0.03, 0.01, 2.0, False)]:
        assert ns["bachelier_implied_vol"](ts._unit_price(F, K, sigma, T, payer), F, K, T, payer) == pytest.approx(sigma, rel=1e-9)


@pytest.mark.parametrize("variant", [0, 1])
def test_bond_template_seasoned_position_matches_the_toy_including_financing(variant):
    """A position traded 2024-04-25 (Thursday: it settles on Friday, so the first repo step spans the
    weekend) priced on Friday 2024-05-10 (Theta's step to Monday is 3 calendar days): FinancingToDate,
    Carry, RollDown, ForwardPrice and Theta of a seasoned position (overnight GC-special and a term repo)
    match the toy config, and the financing is not trivially 0."""
    _t, ref_cfg, _p, _d, instruments = CASES["Bond"]
    traded, d = date(2024, 4, 25), date(2024, 5, 10)
    got = _values(_filled("Bond"), instruments[variant](), d, resolve_on=traded)
    ref = _values(TOY_ASSETS / ref_cfg, instruments[variant](), d, resolve_on=traded)
    _compare("Bond", got, ref)
    assert abs(got[("FinancingToDate", "scalar")]) > 100.0 and (got[("FinancingToDate", "scalar")] > 0) == (variant == 1)  # the short receives


def test_bond_template_term_repo_comes_from_its_own_primitive():
    """repo_term: term locks lib_term_repo_rate's quote on the trade date, not the overnight fixing:
    with a term rate 25bp over the overnight one, RepoRate of a term position is that quote on every
    date, and an overnight position still reads the day's fixing."""
    raw = _filled("Bond")
    raw["code"] += "\ndef lib_term_repo_rate(d, identifier): return tb.gc(d) - tb.SPECIALS.get(identifier, 0.0) + 0.0025\n"
    traded, d = date(2024, 4, 25), date(2024, 5, 10)
    term = Bond(buy_sell="Buy", identifier="TOY 4.25 2034-11-15", size=1e6, settlement_currency="USD", repo_term="term")
    over = Bond(buy_sell="Buy", identifier="TOY 4.25 2034-11-15", size=1e6, settlement_currency="USD", repo_term="overnight")
    import toylib.bond as tb

    want_term = (tb.gc(traded) - tb.SPECIALS["TOY 4.25 2034-11-15"] + 0.0025) * 1e4
    assert _values(raw, term, d, resolve_on=traded)[("RepoRate", "scalar")] == pytest.approx(want_term, rel=1e-12)
    assert _values(raw, over, d, resolve_on=traded)[("RepoRate", "scalar")] == pytest.approx((tb.gc(d) - tb.SPECIALS["TOY 4.25 2034-11-15"]) * 1e4, rel=1e-12)
