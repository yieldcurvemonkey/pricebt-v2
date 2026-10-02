"""skills/pricebt-connect-pricing-library: the fictional Meridian SDK, its asset config (the whole strict
IRSwap contract, docs/v2/IR_STRICT_CONTRACT.md), and the three deliberate-mistake configs (the mistake
tests assert the WRONG behaviour, so the example stays honest); and the three contract templates
(references/config-template*.yaml): each maps the whole measure contract of `pricebt.risk.contracts`,
loads blank, fails loudly when priced unfilled, answers a gap with the paste-ready mapping skeleton
(IRSwap, IRSwaption) or declaration block (Bond), and -- with its library primitives filled by the toy
library -- prices every contract measure like the toy reference config, which proves its recipes."""
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
    assert crif["Amount"].sum() == pytest.approx(s.pricing.value(r, D2, IRDelta, None).result()["value"].sum(), rel=1e-12)


def test_one_vendor_call_per_trade_and_date_and_a_strict_matrix(tmp_path):
    s = PricebtSession.use(assets=[CONFIG])
    r = s.pricing.resolve(_swap(fixed_rate="ATM+25"), D1, None)
    client = s.pricing.market(s.pricing.asset_for(r), D1, None).client
    s.pricing.value(r, D1, Price, None)
    before = client.calls
    for m in (IRDelta(aggregation_level="Type"), risk.Annuity, risk.IRSpotRate, risk.IRFwdRate, risk.IRDiscountDeltaParallel):
        s.pricing.value(r, D1, m, None)
    assert client.calls == before                          # all from the one memoised batch
    sys.path.insert(0, str(ROOT / "skills" / "pricebt-risk-measures" / "scripts"))
    import measures

    assert measures.main(["matrix", "--strict", str(CONFIG)]) == 0
    rows = {(x["measure"], x["form"]): x for x in measures.capability_matrix(CONFIG)["rows"]}
    assert {x["status"] for x in rows.values()} == {measures.MAPPED}           # the whole strict contract, no declaration
    assert {m for m, _f in rows} == {r.measure for r in contracts.contract_for("IRSwap")}
    # --strict still refuses a declaration every library can avoid -- on a Bond, the one class that may declare
    bond = yaml.safe_load((ROOT / "tests" / "assets" / "toy_usd_bond.yaml").read_text(encoding="utf-8"))
    del bond["risk_measures"]["IRVanna"]
    bond["unsupported_measures"] = {"IRVanna": "no vol bump in this library"}
    path = tmp_path / "bond_declares_a_zero.yaml"
    path.write_text(yaml.safe_dump(bond, sort_keys=False), encoding="utf-8")
    assert measures.main(["matrix", str(path)]) == 0 and measures.main(["matrix", "--strict", str(path)]) == 1


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
def lib_pv(m, t): return tr.npv(m, t)
def lib_own_rate(m, t): return tr._par_rate(m, t.effective_date, t.termination_date)
def lib_spot_rate(m, t): return tri.spot_rate(m, t) / 1e4
def lib_shift(m, h): return tri.bumped(m, h)
def lib_shift_discount(m, h): return tri.bumped(m, h)
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
def lib_pv(m, leg): return ts._value(m.curve, m.sigma, leg)
def lib_fwd_rate(m, leg): return ts._fwd(m.curve, leg)
def lib_normal_vol(m, leg): return ts.annual_vol(m, leg) / 1e4
def lib_atm_normal_vol(m, leg): return ts.atm_vol(m, leg) / 1e4
def lib_prob_exercise(m, leg): return ts.prob_exercise(m, leg)
def lib_annuity(m, leg): return ts.annuity(m, leg)
def lib_spot_rate(m, leg): return ts.spot_rate(m, leg) / 1e4
def lib_shift(m, h): return _NS(curve=tri.bumped(m.curve, h), sigma=m.sigma)
def lib_shift_discount(m, h): return _NS(curve=tri.bumped(m.curve, h), sigma=m.sigma)
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
def lib_pv(m, t): return tb.npv(m, t)
def lib_yield(m, t): return tb._yield(m.curve, m.spread, t)
def lib_pv_at_yield(t, y, d): return sum(a * _math.exp(-y * (p - d).days / 365.0) for p, a, *_ in tb._flows(t, d))
def lib_shift_discount(m, h): return _NS(curve=tri.bumped(m.curve, h), spread=m.spread)
def lib_annuity(m, t): return tb.annuity(m, t)
def lib_cashflows(m, t): return tb.cashflows(m, t).to_dict("records")
def lib_oas(m, t): return m.spread
def lib_par_spread(m, t): return m.spread
def lib_shift_pillar(m, pillar, h): return _NS(curve=_KeyRateCurve(m.curve, pillar, h), spread=m.spread)
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
    # 2024-05-14: both toy bonds pay a coupon on 05-15, inside Theta's one-day window
    "Bond": ("config-template-bond.yaml", "toy_usd_bond.yaml", _TOY_BOND, date(2024, 5, 14), (
        lambda: Bond(buy_sell="Buy", identifier="TOY 4.25 2034-11-15", size=1e6, settlement_currency="USD"),
        lambda: Bond(buy_sell="Sell", identifier="TOY 3.5 2027-05-15", size=5e5, settlement_currency="USD"),
    )),
}
# (instrument, measure) -> rel tolerance where the template's recipe is a different (equally valid) method
# than the toy's: a finite-difference vega/DV01 vs an analytic one, yield bumps vs curve bumps on the bond
TOL = {("IRSwaption", "IRVega"): 2e-5, ("Bond", "LightningDV01"): 1e-5, ("Bond", "IRDelta"): 1e-7, ("Bond", "IRGammaParallel"): 1e-6}


def _template_raw(instrument):
    return yaml.safe_load((TEMPLATES / CASES[instrument][0]).read_text(encoding="utf-8"))


def _filled(instrument):
    raw = _template_raw(instrument)
    raw["code"] += CASES[instrument][2]
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
    return out


def _compare(instrument, got, ref):
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
        elif measure == "IRDelta":   # key-rate ladder: sums to the parallel (discount-only, single curve) delta,
            assert tuple(v["mkt_point"]) == TOY_PILLARS   # up to an option's third-order finite-difference terms
            assert v["value"].sum() == pytest.approx(got[("IRDiscountDeltaParallel", "scalar")], rel=1e-4, abs=1e-9)
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


def test_bond_template_declaration_path_pastes_back():
    """Bond keeps map-or-declare: map only Price, and the load error carries the paste-ready block for
    the rest; pasting it loads."""
    raw, missing, err = _price_only("Bond")
    block = contracts.unsupported_block("Bond", missing)
    assert block in err
    raw["unsupported_measures"] = yaml.safe_load(block)["unsupported_measures"]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = load_asset(raw)
    assert set(cfg.unsupported_measures) == {r.measure for r in contracts.contract_for("Bond")} - {"Price"}


@pytest.mark.parametrize("instrument", ["IRSwap", "IRSwaption"])
def test_strict_template_gap_gets_the_mapping_skeleton_never_a_declaration_block(instrument):
    """IRSwap/IRSwaption (IR_STRICT_CONTRACT R3-0): map only Price, and the load error lists every gap
    and ends with the paste-ready mapping skeleton, not an unsupported_measures block. The skeleton's
    stub expressions do not compile, so pasting it unchanged still does not load; and declaring the
    gaps instead is itself refused."""
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
    declared["unsupported_measures"] = yaml.safe_load(contracts.unsupported_block(instrument, missing))["unsupported_measures"]
    with pytest.raises(ConfigError, match="unsupported_measures cannot satisfy them"):
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
