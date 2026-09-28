"""skills/pricebt-connect-pricing-library: the fictional Meridian SDK, its asset config, and the three
deliberate-mistake configs. The mistake tests assert the WRONG behaviour, so the example stays honest."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "skills" / "pricebt-connect-pricing-library" / "example"
if str(EXAMPLE) not in sys.path:
    sys.path.insert(0, str(EXAMPLE))

import meridian_sdk as mdn  # noqa: E402
from pricebt.assets.config import load_asset  # noqa: E402
from pricebt.backtests.actions import AddTradeAction  # noqa: E402
from pricebt.backtests.generic_engine import GenericEngine  # noqa: E402
from pricebt.backtests.strategy import Strategy  # noqa: E402
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements  # noqa: E402
from pricebt.instrument import IRSwap  # noqa: E402
from pricebt.markets import PricingContext  # noqa: E402
from pricebt.markets.portfolio import Portfolio  # noqa: E402
from pricebt.risk import IRDelta, Price  # noqa: E402
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


def test_blank_template_loads():
    template = EXAMPLE.parent / "references" / "config-template.yaml"
    assert load_asset(template).name == "TODO_asset_name"


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
    s = PricebtSession.use(assets=[CONFIG])
    a = _swap(term="5y", name="a", quantity_=2.0)
    b = _swap("Receive", term="10y", name="b", quantity_=0.5)
    c = _swap(term="30y", name="c")
    scalar = sum(float(s.pricing.value(x, D1, IRDelta(aggregation_level="Type"), None)) for x in (a, b, c))
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
    assert ladder_sum == pytest.approx(-bad[1])   # ladder and scalar disagree in sign


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
