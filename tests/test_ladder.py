"""The required `delta_ladder` of the `swap` schema on the rateslib adapter (spec S4): the kit binds a plain `dict[str, float]` keyed by tenor with its exact keys, the
ladder maths (par-swap risk curve), vector measures through the engine, monthly ladder hedging end to end, and the config path. The sizing maths has its own core file
(`test_ladder_sizing.py`)."""
import datetime as dt

import pandas as pd
import pytest

pytest.importorskip("rateslib")
pytestmark = pytest.mark.adapter_rateslib

from conftest import assert_identity  # noqa: E402
from test_rl_common import (LAYERS, SCHEMAS, SWAP_CONV, build, ctx, engine, rl, rl_pricer, seeded_fixings, swap_template, synthetic, template,  # noqa: E402
                            time_context)

import pricebt.contrib.rateslib as RL  # noqa: E402
from pricebt import api  # noqa: E402
from pricebt.config import yamlio  # noqa: E402
from pricebt.config.loader import build as build_cfg  # noqa: E402
from pricebt.contracts.binding import Env  # noqa: E402
from pricebt.contracts.evaluate import evaluate_measure, is_vector, vector_add  # noqa: E402
from pricebt.contracts.spec import build_spec  # noqa: E402
from pricebt.contrib.rateslib import DEFAULT_TENORS, LadderModel  # noqa: E402
from pricebt.contrib.rateslib.conventions import swap_conventions  # noqa: E402
from pricebt.contrib.rateslib.ladder import build_par_swap_ladder, clean_tenors, ladder_model  # noqa: E402
from pricebt.errors import ConfigError, MeasureError  # noqa: E402
from pricebt.orders import CloseOrder, OpenOrder, PositionSelector  # noqa: E402
from pricebt.strategy import HedgeAction, PeriodicTrigger, PeriodicTriggerRequirements, PositionSpec, Strategy  # noqa: E402
from pricebt.testing.scripted import ScriptedStrategy  # noqa: E402
from pricebt.timeutil import TimeGrid  # noqa: E402

D = dt.datetime
TENORS = list(DEFAULT_TENORS)
FX = seeded_fixings()
CONV = swap_conventions(SWAP_CONV)


@pytest.fixture(scope="module")
def p1():
    return rl_pricer("2025-01-16", 0, 0)


@pytest.fixture(scope="module")
def mdp():
    return synthetic()


@pytest.fixture(scope="module")
def tctx():
    return time_context()


def swap(p, tenor="5Y", side="pay", notional=10e6, **kw):
    return build(p, maturity=tenor, side=side, notional=notional, **kw).obj


def tpl(name, tenor, side="pay", notional=1e7):
    return swap_template(layers=LAYERS, name=name, maturity=tenor.upper(), side=side, notional=notional)


def top(lad):
    return max(lad, key=lambda k: abs(lad[k]))


# ------------------------------------------------------------------ the contract: the swap schema requires the ladder and the kit binds it (S4)
def test_the_swap_schema_requires_a_delta_ladder_measure_returning_a_dict():
    s = SCHEMAS.get("swap")
    assert "delta_ladder" in s.required_names()["measure"] and is_vector(s.measures["delta_ladder"])
    assert not any(is_vector(m) for n, m in SCHEMAS.get("bond").measures.items()), "only the swap requires (and has) a ladder"


def test_a_swap_spec_without_a_delta_ladder_binding_is_refused_at_load():
    std = ("value", "dv01", "gamma", "rate", "carry", "roll", "delta", "convexity")  # the schema's names bound by hand to the adapter's methods, delta_ladder left out
    raw = {"asset_class": "swap", "factory": RL.swap.factory, "conventions": SWAP_CONV, "layers": list(LAYERS)}
    build_spec("s", {**raw, "bind": {k: RL.SWAP_BIND[k] for k in (*std, "delta_ladder")}}, schemas=SCHEMAS)  # twin: with the ladder it loads
    with pytest.raises(ConfigError, match="delta_ladder") as e:
        build_spec("s", {**raw, "bind": {k: RL.SWAP_BIND[k] for k in std}}, schemas=SCHEMAS)
    assert e.value.code == "CFG-REQUIRED-BINDING"


def test_the_kit_binds_the_ladder_with_its_exact_keys_and_the_dict_reducer():
    b = RL.SWAP_BIND["delta_ladder"]
    assert b["keys"] == TENORS and b["reduce"] == "dict_of_floats" and b["kwargs"]["tenors"] == TENORS
    spec = build_spec("s", {"factory": RL.swap, "conventions": SWAP_CONV}, schemas=SCHEMAS)
    assert spec.bindings["delta_ladder"].keys == tuple(TENORS)


def _mark(spec_terms, p, bind=None, allow=None):
    t = template(RL.swap, SWAP_CONV, spec_terms, bind=bind, allow=allow)
    obj = t.build(p, p.ts).obj
    return t, Env(pricer=p, ctx=ctx(p), instrument=obj, terms={})


def test_the_measure_returns_exactly_the_bound_tenors_in_tenor_order(p1):
    t, env = _mark({"side": "pay", "maturity": "10Y", "notional": 1e7}, p1)
    lad = evaluate_measure(t.spec, "delta_ladder", env)
    assert list(lad) == TENORS and all(isinstance(v, float) for v in lad.values())


def test_tenors_are_an_ordinary_bound_kwarg_and_the_keys_follow_them(p1):
    bind = {"delta_ladder": {"target": {"method": "delta_ladder"}, "kwargs": {"ctx": "@ctx", "tenors": ["2Y", "5Y", "10Y", "30Y"]}, "keys": ["30Y", "2Y", "10Y", "5Y"], "reduce": "dict_of_floats"}}
    t, env = _mark({"side": "pay", "maturity": "10Y", "notional": 1e7}, p1, bind)
    lad = evaluate_measure(t.spec, "delta_ladder", env)
    assert list(lad) == ["2Y", "5Y", "10Y", "30Y"], "pricebt orders the buckets by tenor whatever order `keys` are declared in"
    assert top(lad) == "10Y" and sum(lad.values()) == pytest.approx(env.instrument.dv01(ctx=ctx(p1)), rel=2e-2)


def test_keys_that_disagree_with_the_returned_ladder_fail_at_the_mark_naming_the_difference(p1):
    bind = {"delta_ladder": {"target": {"method": "delta_ladder"}, "kwargs": {"ctx": "@ctx", "tenors": ["2Y", "5Y", "10Y"]}, "keys": ["2Y", "5Y", "30Y"], "reduce": "dict_of_floats"}}
    t, env = _mark({"side": "pay", "maturity": "10Y", "notional": 1e7}, p1, bind)
    with pytest.raises(MeasureError, match=r"missing \['30Y'\]; extra \['10Y'\]"):
        evaluate_measure(t.spec, "delta_ladder", env)


def coarse_ladder(pricer, instrument, tenors, calls=[]):  # noqa: B006  (module-level recorder: the function is reached by a dotted path)
    """A USER ladder builder: the neutral equivalent of the old builder-function-in-config construct. Bound like any measure (S4), it gets exactly the declared arguments."""
    calls.append((type(pricer).__name__, tuple(tenors)))
    return ladder_model(pricer, CONV, tenors).delta(instrument._active)


def test_a_user_supplied_ladder_function_is_bound_like_any_measure(p1):
    bind = {"delta_ladder": {"target": {"function": "test_ladder:coarse_ladder"}, "kwargs": {"pricer": "@pricer", "instrument": "@instrument", "tenors": ["2Y", "5Y", "10Y"]},
                             "keys": ["2Y", "5Y", "10Y"], "reduce": "dict_of_floats"}}
    t, env = _mark({"side": "pay", "maturity": "5Y", "notional": 1e7}, p1, bind, allow=["pricebt", "test_ladder"])
    coarse_ladder.__defaults__[0].clear()
    lad = evaluate_measure(t.spec, "delta_ladder", env)
    assert coarse_ladder.__defaults__[0] == [("WrappedCurvePricer", ("2Y", "5Y", "10Y"))] and list(lad) == ["2Y", "5Y", "10Y"] and top(lad) == "5Y"
    assert lad == env.instrument.delta_ladder(ctx=ctx(p1), tenors=["2Y", "5Y", "10Y"])


# ------------------------------------------------------------------ ladder maths
def test_ladder_of_a_pillar_swap_sits_in_its_own_bucket_and_sums_to_dv01(p1):
    sw = swap(p1, "5Y")
    lad = sw.delta_ladder(ctx=ctx(p1))
    assert list(lad) == TENORS and isinstance(lad, dict)
    dv01 = float(sw.dv01(ctx=ctx(p1)))
    assert top(lad) == "5Y" and lad["5Y"] / sum(abs(v) for v in lad.values()) > 0.999
    assert sum(lad.values()) == pytest.approx(dv01, rel=2e-2)
    assert lad["5Y"] > 0, "a payer gains when rates rise: positive ladder"
    rec = swap(p1, "5Y", side="receive").delta_ladder(ctx=ctx(p1))
    assert max(abs(rec[k] + lad[k]) for k in lad) < 1e-6 * max(abs(v) for v in lad.values()), "receiver mirrors payer"


def test_off_pillar_swap_splits_between_neighbouring_pillars(p1):
    lad = swap(p1, "4Y").delta_ladder(ctx=ctx(p1))
    assert (abs(lad["3Y"]) + abs(lad["5Y"])) / sum(abs(v) for v in lad.values()) > 0.98 and lad["3Y"] > 0 and lad["5Y"] > 0
    assert lad["3Y"] + lad["5Y"] == pytest.approx(sum(lad.values()), rel=1e-3), "interpolation leaks < 0.1% into other pillars"


def test_ladder_notional_scaling_and_bucket_sum_vs_parallel_bump(p1):
    a, b = (swap(p1, "7Y", notional=n).delta_ladder(ctx=ctx(p1)) for n in (1e7, 3e7))
    assert max(abs(b[k] / 3 - a[k]) for k in a) < 1e-6 * max(abs(v) for v in a.values())
    sw = swap(p1, "10Y")
    up, dn = rl_pricer("2025-01-16", 1, 1), rl_pricer("2025-01-16", -1, -1)
    v = lambda p: sw.value(ctx=ctx(p)).pv  # noqa: E731
    parallel = (v(up) - v(dn)) / 2.0  # ccy per bp from a +-1bp parallel par-curve shift (par_curve tilts are in percent/100)
    assert sum(sw.delta_ladder(ctx=ctx(p1)).values()) == pytest.approx(parallel, rel=2e-2)


def test_risk_curve_is_calibrated_to_the_dense_curve_and_memoised(p1):
    m = ladder_model(p1, CONV, TENORS)
    assert isinstance(m, LadderModel) and m.buckets == tuple(TENORS) and m.solver.result["status"] == "SUCCESS"
    assert ladder_model(p1, CONV, TENORS) is m, "one calibration per snapshot, conventions and tenor set (memoised on the pricer)"
    assert ladder_model(p1, CONV, ["2Y", "10Y"]) is not m and ladder_model(p1, CONV, TENORS, 1.0) is not m
    cal = p1.calendar("nyc")
    eff = cal.add_bus_days(D(2025, 1, 16), 2, True)
    for t in ("2Y", "10Y"):
        on_risk = float(rl.IRS(effective=eff, termination=t.lower(), spec="usd_irs", calendar=cal).rate(curves=m.curve))
        assert on_risk == pytest.approx(build(p1, maturity=t, fixed_rate="par").terms["fixed_rate"], abs=1e-4), "the pillar swaps reprice at the dense curve's par rates"


def test_duplicate_tenors_are_rejected(p1):
    with pytest.raises(ConfigError, match="duplicate maturities"):
        build_par_swap_ladder(p1, CONV, ("5Y", "60M"))
    with pytest.raises(ConfigError, match="increasing") as e:
        build_par_swap_ladder(p1, CONV, ("10Y", "2Y", "5Y"))  # pillars must ascend: rateslib itself would fail on unsorted nodes with an unrelated ValueError
    assert e.value.code == "LADDER"
    with pytest.raises(ConfigError, match="duplicates"):
        clean_tenors(["5Y", "5y"])
    with pytest.raises(ConfigError, match="at least one"):
        clean_tenors([])


# ------------------------------------------------------------------ vector measures through the engine
def test_vector_measures_aggregate_positions_and_pending_orders_and_match_scalar_sum(tctx, mdp):
    grid = TimeGrid.daily("2024-03-04", "2024-03-15", "1b", tctx)
    a, b = tpl("a5", "5y", "pay", 1e7), tpl("b10", "10y", "receive", 2e7)
    seen = {}

    def probe(ts, view, submit):
        if ts == grid[1]:
            seen["book"] = view.measure_vector("delta_ladder")
            seen["scalar"] = view.measure("delta_ladder")
            seen["dv01"] = view.measure("dv01")
            seen["unit_a"] = view.measure_of_vector(a, "delta_ladder")
            submit(OpenOrder(a, 2.0))
            seen["pending_open"] = view.measure_vector("delta_ladder", include_pending=True)
            submit(CloseOrder(PositionSelector(templates=("b10",))))
            seen["pending_close"] = view.measure_vector("delta_ladder", include_pending=True)
            with pytest.raises(MeasureError, match="not a vector"):
                view.measure_vector("dv01")
            seen["by_scope"] = view.measure_vector("delta_ladder", PositionSelector(templates=("a5",)))
            seen["empty"] = view.measure_vector("delta_ladder", PositionSelector(templates=("nothing",)))

    strat = ScriptedStrategy({grid[0]: [OpenOrder(a, 1.0), OpenOrder(b, 1.0)]}, fn=probe)
    rec = engine(grid, strat, mdp, cadence="each", vector_measures=("delta_ladder",), measures=("delta_ladder", "dv01")).run()
    assert rec.errors.empty
    book = seen["book"]
    assert isinstance(book, dict) and sum(book.values()) == pytest.approx(seen["scalar"], rel=1e-12)
    assert sum(book.values()) == pytest.approx(seen["dv01"], rel=3e-2)
    grown = vector_add(book, seen["unit_a"], 2.0)
    assert max(abs(seen["pending_open"][k] - grown[k]) for k in grown) < 1e-6
    assert max(abs(v) for v in seen["pending_close"].values()) < max(abs(v) for v in seen["pending_open"].values())
    assert top(seen["by_scope"]) == "5Y" and seen["empty"] == {}
    v = rec.vectors["delta_ladder"]
    assert list(v.columns) == TENORS and len(v) == len(rec.equity) and v.index.equals(rec.equity.index)
    assert (v.sum(axis=1) - rec.equity["measure_delta_ladder"]).abs().max() < 1e-9
    assert v.iloc[0].abs().sum() > 0 and v.iloc[0].sum() == pytest.approx(rec.equity["measure_delta_ladder"].iloc[0], abs=1e-9)
    assert_identity(rec)


def test_result_exposes_vectors_and_parquet_round_trips_them(tctx, mdp, tmp_path):
    pytest.importorskip("pyarrow")
    from pricebt.results import BacktestResult
    from pricebt.results.io import from_parquet, to_parquet

    grid = TimeGrid.daily("2024-03-04", "2024-03-12", "1b", tctx)
    rec = engine(grid, ScriptedStrategy({grid[0]: [OpenOrder(tpl("a5", "5y"), 1.0)]}), mdp, cadence="each", vector_measures=("delta_ladder",)).run()
    res = BacktestResult.from_record(rec)
    assert list(res.vectors) == ["delta_ladder"]
    d = to_parquet(res, tmp_path / "r")
    back = from_parquet(d)
    pd.testing.assert_frame_equal(back.vectors["delta_ladder"], res.vectors["delta_ladder"], check_freq=False)
    assert back.record.settings.vector_measures == ("delta_ladder",)


# ------------------------------------------------------------------ ladder hedge end to end
def hedged(mdp, tctx, mode="resize", **hedge_kw):
    grid = TimeGrid.daily("2024-03-04", "2024-06-28", "1b", tctx)
    book = [PositionSpec(tpl("book10", "10y", "pay", 2e7), 1.0), PositionSpec(tpl("book4", "4y", "receive", 1.5e7), 1.0)]
    hedges = [tpl("h2y", "2y", notional=1e6), tpl("h3y", "3y", notional=1e6), tpl("h5y", "5y", notional=1e6), tpl("h7y", "7y", notional=1e6), tpl("h10y", "10y", notional=1e6)]
    act = HedgeAction("delta_ladder", hedges, hedge_kw.pop("trade_duration", None), "lh", mode=mode, **hedge_kw)
    strat = Strategy(book, [PeriodicTrigger(PeriodicTriggerRequirements(start_date=dt.date(2024, 3, 4), frequency="1m"), act)])
    return engine(grid, strat, mdp, vector_measures=("delta_ladder",), measures=("dv01",), cadence="eod").run()


def test_monthly_ladder_hedge_flattens_every_bucket_after_each_rebalance(tctx, mdp):
    rec = hedged(mdp, tctx)
    assert rec.errors.empty
    assert_identity(rec)
    ev = rec.events[rec.events.kind == "ladder_hedge"]
    assert len(ev) >= 3
    d0, later = ev.detail.iloc[0], list(ev.detail.iloc[1:])
    assert d0["l1_after"] < 1e-4 * d0["l1_before"], "spot-start book on the hedge pillars: exact fit"
    assert all(e["l1_after"] < 0.15 * e["l1_before"] for e in later), "aged swaps leak a little into the unhedged front-end buckets (3M-1Y): small least-squares residual"
    v = rec.vectors["delta_ladder"]
    at_rebalance = v.loc[list(ev.ts)].abs().sum(axis=1)
    assert at_rebalance.max() < 0.1 * v.abs().sum(axis=1).max(), "book ladder is nearly flat at every rebalance point, aged hedge positions included"
    assert at_rebalance.iloc[0] < 1.0, "exact fit at the first firing (of ~2e4 ccy/bp before)"
    assert v.abs().sum(axis=1).iloc[1:].max() > 1.0, "and drifts between rebalances (curve moves + ageing), which is what the monthly rebalance corrects"
    assert [e["l1_before"] > 1.0 for e in ev.detail][1:] == [True] * (len(ev) - 1), "each later rebalance finds a drifted ladder and trades it back"
    hedges = rec.positions[rec.positions.action == "lh"]
    assert {"h3y", "h5y", "h10y"} <= set(hedges.template) and set(hedges.template) <= {"h2y", "h3y", "h5y", "h7y", "h10y"}
    assert len(hedges[hedges.status == "open"]) <= 5, "resize mode keeps one live position per leg"


def test_ladder_hedge_reduces_pnl_variance_versus_no_hedge_and_dv01_hedge_leaves_curve_risk(tctx, mdp):
    grid = TimeGrid.daily("2024-03-04", "2024-06-28", "1b", tctx)
    book = [PositionSpec(tpl("book9", "9y", "pay", 3e7), 1.0), PositionSpec(tpl("book4", "4y", "receive", 4e7), 1.0)]

    def run(actions):
        trig = [PeriodicTrigger(PeriodicTriggerRequirements(start_date=dt.date(2024, 3, 4), frequency="1m"), a) for a in actions]
        return engine(grid, Strategy(book, trig), mdp, vector_measures=("delta_ladder",), measures=("dv01",), cadence="eod").run()

    none = run([])
    lad = run([HedgeAction("delta_ladder", [tpl("h2y", "2y", notional=1e6), tpl("h3y", "3y", notional=1e6), tpl("h5y", "5y", notional=1e6), tpl("h7y", "7y", notional=1e6),
                                            tpl("h10y", "10y", notional=1e6)], None, "lh", mode="resize")])
    dv = run([HedgeAction("dv01", tpl("h5y", "5y", notional=1e6), None, "dh", mode="resize")])
    std = lambda r: float(r.equity["step_pnl"].iloc[1:].std())  # noqa: E731
    resid = lambda r: float(r.vectors["delta_ladder"].abs().sum(axis=1).iloc[1:].mean())  # noqa: E731
    assert std(lad) < 0.05 * std(none) and std(lad) < 0.2 * std(dv), "ladder hedge kills the daily P&L variance of an off-pillar curve book; a one-leg parallel hedge does not"
    assert resid(lad) < 0.05 * resid(none) and resid(lad) < 0.1 * resid(dv), "a parallel-dv01 hedge nets dv01 but leaves a 2s10s ladder; the ladder hedge removes it"


def test_ladder_hedge_add_mode_static_and_guards(tctx, mdp):
    rec = hedged(mdp, tctx, mode="add", trade_duration="1m")
    assert rec.errors.empty and (rec.positions.action == "lh").sum() >= 8
    with pytest.raises(Exception, match="weights must be 1"):
        from pricebt.strategy import Leg

        HedgeAction("delta_ladder", [Leg(tpl("h2y", "2y"), 2.0)], None, "x")
    with pytest.raises(Exception, match="single-leg"):
        HedgeAction("dv01", [tpl("h2y", "2y"), tpl("h5y", "5y")], None, "x", mode="resize")
    with pytest.raises(Exception, match="target must be 0"):
        h = HedgeAction("delta_ladder", [tpl("h2y", "2y")], None, "x", target=5.0)
        h.apply(pd.Timestamp("2024-03-04", tz="America/New_York"), type("V", (), {"pending_orders": (), "measure_vector": lambda *a, **k: {}})(), None)


# ------------------------------------------------------------------ config path end to end
CFG = """
name: ladder_cfg
backtest:
  tz: America/New_York
  grid: {start: 2024-03-01, end: 2024-04-30, freq: 1b}
  progress: {show: false}
  attribution: {layers: [carry, roll, delta, convexity], cadence: eod}
  measures: [dv01]
  vector_measures: [delta_ladder]
market:
  mdps:
    rates: {type: "pricebt.testing.synthetic:SyntheticMarket", kwargs: {start: 2024-01-02, end: 2024-12-31, seed: 7, calendar_name: nyc}}
  pricers:
    primary: {mdp: rates, wrap: "pricebt.contrib.rateslib:wrap"}
instruments:
  swap:
    factory: "pricebt.contrib.rateslib:swap"
    layers: [carry, roll, delta, convexity]
    bind:
      # the ladder is a bound measure like any other: the requested tenors are an ordinary kwarg, `keys` says exactly what comes back
      delta_ladder: {target: {method: delta_ladder}, kwargs: {ctx: "@ctx", tenors: [2Y, 5Y, 10Y, 30Y]}, keys: [2Y, 5Y, 10Y, 30Y], reduce: dict_of_floats}
trades:
  book10y: {instrument: swap, terms: {side: pay, maturity: 10Y, notional: 2e7}}
  h2y: {instrument: swap, terms: {side: pay, maturity: 2Y, notional: 1e6}}
  h10y: {instrument: swap, terms: {side: pay, maturity: 10Y, notional: 1e6}}
  h30y: {instrument: swap, terms: {side: pay, maturity: 30Y, notional: 1e6}}
strategy:
  initial_portfolio: [{template: book10y, quantity: 1}]
  triggers:
    - type: periodic
      frequency: 1m
      actions:
        - {type: hedge, name: lh, risk: delta_ladder, priceables: [h2y, h10y, h30y], mode: resize}
"""


def cfg(**edits):
    c = yamlio.loads(CFG)
    c["instruments"]["swap"]["conventions"] = dict(RL.USD_SOFR_OIS_CONVENTIONS)  # the shared block, as the adapter ships it (calendar `nyc`: the market's calendar name)
    for k, v in edits.items():
        yamlio.set_path(c, k.replace("__", "."), v)
    return c


def test_config_errors_in_the_ladder_binding_carry_the_yaml_path_and_a_suggestion():
    with pytest.raises(ConfigError, match="did you mean") as e:
        build_cfg(cfg(instruments__swap__bind__delta_ladder__target={"method": "delta_laddder"}))
    assert e.value.code == "CFG-BINDING" and "instruments.swap.bind.delta_ladder" in str(e.value)


def test_config_inline_tenors_are_accepted():
    build_cfg(cfg(instruments__swap__bind__delta_ladder__kwargs={"ctx": "@ctx", "tenors": ["5Y", "10Y"]}, instruments__swap__bind__delta_ladder__keys=["5Y", "10Y"]))


def test_config_driven_ladder_hedge_runs_records_vectors_and_reconciles():
    res = api.run(cfg())
    assert res.n_errors == 0 and res.reconcile().ok
    v = res.vectors["delta_ladder"]
    assert list(v.columns) == ["2Y", "5Y", "10Y", "30Y"]
    assert v.abs().sum(axis=1).iloc[0] > 0
    assert v.abs().sum(axis=1).iloc[0] < 1e-6, "the first monthly firing hedges the initial book at the same point"
    assert (res.positions.action == "lh").any()


# ------------------------------------------------------------------ dv01 of an aged swap
def test_dv01_of_a_started_swap_is_the_market_dv01_not_the_full_coupon_pv01():
    px = rl_pricer("2024-10-16", 0, 0, FX)
    aged = build(px, effective=dt.date(2024, 1, 8), maturity=dt.date(2026, 1, 8), notional=1e7, fixed_rate=4.2).obj  # a 2Y swap, 9 months in: 1.2Y left, first coupon 80% accrued
    c = ctx(px)
    market_dv01 = aged.dv01(ctx=c)
    aged._prep(px)
    analytic = float(aged._active.analytic_delta(curves=px.curve, leg=1))
    assert market_dv01 == pytest.approx(sum(aged.delta_ladder(ctx=c).values()), rel=1e-12), "started swap: dv01 IS the ladder sum"
    for tenors in (["2Y", "5Y", "10Y"], ["1Y", "2Y", "3Y"]):
        assert aged.dv01(ctx=c, tenors=tenors) == pytest.approx(sum(aged.delta_ladder(ctx=c, tenors=tenors).values()), rel=1e-12), "... over the tenors the binding declares"
    assert aged.dv01(ctx=c, tenors=["1Y", "2Y", "3Y"]) != pytest.approx(market_dv01, rel=1e-6)
    assert analytic > 1.3 * market_dv01, "the analytic fixed-leg PV01 still carries the (nearly fully accrued) first coupon"
    fresh = swap(px, "2Y")
    assert fresh.dv01(ctx=ctx(px)) == pytest.approx(sum(fresh.delta_ladder(ctx=ctx(px)).values()), rel=1e-3), "spot-start: analytic == ladder sum"
