"""The engine-computed baseline decomposition (spec L3): a Taylor attribution from three measures the instrument already binds - `dv01` (the dollar delta),
`gamma` and `rate` (its own market rate) - so two stacks get an identical, definition-controlled decomposition and a difference can only be in the measures.

    tay_delta      = dv01 at the start of the interval x the change of `rate` in bp
    tay_convexity  = 1/2 x gamma at the start of the interval x (the change of `rate` in bp) squared
    tay_unexplained = interval P&L - tay_delta - tay_convexity        (the baseline's own remainder; `unexplained` stays the library layers')
"""
import pandas as pd
import pytest

from pricebt.engine import Engine, EngineSettings
from pricebt.market import MarketData
from pricebt.orders import CloseOrder, OpenOrder
from pricebt.pricable import MarkContext
from pricebt.results.errors import ResultError
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyForward, ToyFuture, ToyMDP, ToyOption
from pricebt.timeutil import Clock, TimeContext, TimeGrid
from toy_helpers import toy_tpl

pytestmark = pytest.mark.core

D = lambda s: pd.Timestamp(f"{s} 17:00", tz="America/New_York")  # noqa: E731


def run(strategy, mdp=None, **settings):
    grid = TimeGrid.daily("2024-01-02", "2024-02-29", "1b", TimeContext())
    mdp = mdp or ToyMDP()
    settings.setdefault("show_progress", False)
    rec = Engine(grid, MarketData({"primary": mdp}, Clock()), strategy, EngineSettings(**settings)).run()
    return rec, mdp


def fwd(**kw):
    return toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, **kw})


def opt():
    return toy_tpl(name="opt", target=ToyOption, kwargs={"strike": 4.0, "expiry": D("2024-04-30"), "notional": 1e4}, resolved=("expiry",))


OPEN, CLOSE = D("2024-01-10"), D("2024-02-07")


def test_a_linear_instrument_is_explained_by_tay_delta_alone():
    rec, _ = run(ScriptedStrategy({OPEN: [OpenOrder(fwd(), quantity=3.0)], CLOSE: [CloseOrder()]}), baseline=True, cadence="each")
    lay = rec.layers_by_position.set_index("layer")["pnl"]
    assert lay["tay_unexplained"] == pytest.approx(0.0, abs=1e-9) and lay["tay_convexity"] == 0.0
    r0, r1 = ToyMDP().get_pricer(OPEN).rate, ToyMDP().get_pricer(CLOSE).rate
    assert lay["tay_delta"] == pytest.approx(3.0 * 10.0 * (r1 - r0), rel=1e-12), "q * dv01 (10 * 0.01 per bp) * the move in bp (100 * dr)"


def test_the_baseline_leaves_the_library_layers_and_their_unexplained_alone():
    strat = lambda: ScriptedStrategy({OPEN: [OpenOrder(fwd(carry_bp_per_day=0.5), quantity=3.0)], CLOSE: [CloseOrder()]})  # noqa: E731
    with_b, _ = run(strat(), baseline=True, layers=("carry", "delta"), cadence="each")
    without, _ = run(strat(), layers=("carry", "delta"), cadence="each")
    a = with_b.layers_by_position.set_index("layer")["pnl"]
    b = without.layers_by_position.set_index("layer")["pnl"]
    for name in ("carry", "delta", "convexity", "unexplained"):
        assert a.get(name, 0.0) == pytest.approx(b.get(name, 0.0), abs=1e-12), name
    assert {"tay_delta", "tay_convexity", "tay_unexplained"} <= set(a.index) and not {"tay_delta"} & set(b.index)
    assert a["tay_unexplained"] == pytest.approx(a["carry"], rel=1e-10), "what the Taylor terms miss here is exactly the carry cash"


def test_an_option_gets_delta_and_convexity_from_its_start_of_interval_greeks():
    rec, mdp = run(ScriptedStrategy({OPEN: [OpenOrder(opt(), quantity=2.0)], CLOSE: [CloseOrder()]}), baseline=True, cadence="each")
    lay = rec.layers_by_position.set_index("layer")["pnl"]
    # hand: sum over the daily intervals of q * dv01(t0) * 100 dr  and  q * gamma(t0)/2 * (100 dr)^2, with the option's own closed-form greeks
    template = opt()
    pts = [t for t in rec.equity.index if OPEN <= t <= CLOSE]
    tay_d = tay_c = 0.0
    for a, b in zip(pts[:-1], pts[1:]):
        pa, pb = mdp.get_pricer(a), mdp.get_pricer(b)
        obj = template.build(pa, a).obj
        c = MarkContext.standalone(pa, ts=a)
        dbp = (pb.rate - pa.rate) * 100.0
        tay_d += 2.0 * obj.dv01(c) * dbp
        tay_c += 0.5 * 2.0 * obj.convexity(c) * dbp ** 2
    assert lay["tay_delta"] == pytest.approx(tay_d, rel=1e-10) and lay["tay_convexity"] == pytest.approx(tay_c, rel=1e-10)
    assert lay["tay_convexity"] != 0.0


def test_a_multi_point_cadence_uses_the_measures_of_the_previous_flush_not_of_the_previous_point():
    strat = lambda: ScriptedStrategy({OPEN: [OpenOrder(opt(), quantity=1.0)], CLOSE: [CloseOrder()]})  # noqa: E731
    every, mdp = run(strat(), baseline=True, cadence="each")
    coarse, _ = run(strat(), baseline=True, cadence="every_n:5")
    a = every.layers_by_position.set_index("layer")["pnl"]
    b = coarse.layers_by_position.set_index("layer")["pnl"]
    assert b["tay_delta"] != pytest.approx(a["tay_delta"], rel=1e-6), "a coarser cadence is a coarser Taylor expansion"
    total = coarse.positions["pnl"].iloc[0]
    assert b["tay_delta"] + b["tay_convexity"] + b["tay_unexplained"] == pytest.approx(total, abs=1e-6), "the baseline's three rows always sum to the position's P&L"


def test_baseline_rows_are_audited_and_named_in_the_manifest():
    rec, _ = run(ScriptedStrategy({OPEN: [OpenOrder(fwd(), quantity=3.0)], CLOSE: [CloseOrder()]}), baseline=True, cadence="each", audit=True)
    assert {"tay_delta", "tay_convexity", "tay_unexplained"} <= set(rec.audit["layers"]["layer"])
    refs = rec.manifest["instruments"]["fwd"]["layers"]
    assert refs["tay_delta"] == "baseline.tay_delta@1" and refs["tay_convexity"] == "baseline.tay_convexity@1" and refs["tay_unexplained"] == "baseline.tay_unexplained@1"


def test_an_instrument_that_does_not_bind_rate_dv01_and_gamma_is_reported_with_the_missing_name_and_gets_no_baseline_rows():
    """CHANGED in doubt cycle 3 (E2; it pinned an abort under the default on_error=raise): the baseline never changes whether a run completes, so the refusal is ONE recorded
    error naming the missing measure and the position trades without baseline rows. The config loader still refuses such an instrument at load (CFG-BASELINE)."""
    fut = toy_tpl(name="fut", target=ToyFuture, kwargs={"multiplier": 1.0})
    rec, _ = run(ScriptedStrategy({OPEN: [OpenOrder(fut)], CLOSE: [CloseOrder()]}), baseline=True, cadence="each")
    assert len(rec.trades) == 2 and list(rec.errors["where"]) == ["baseline"] and "rate" in rec.errors["error"].iloc[0]
    assert not [n for n in rec.layers_by_position["layer"].unique() if n.startswith("tay_")]


def test_the_baseline_alone_flushes_even_when_no_library_layer_is_requested():
    rec, _ = run(ScriptedStrategy({OPEN: [OpenOrder(fwd(), quantity=1.0)]}), baseline=True)
    assert "layer_tay_delta" in rec.equity.columns and rec.equity["layer_tay_delta"].abs().max() > 0


def test_the_baseline_is_off_by_default():
    rec, _ = run(ScriptedStrategy({OPEN: [OpenOrder(fwd(), quantity=1.0)]}), layers=("delta",))
    assert not [c for c in rec.equity.columns if "tay_" in c]


def test_config_switch_and_round_trip():
    from pricebt import api

    cfg = {
        "name": "b", "backtest": {"tz": "America/New_York", "grid": {"start": "2024-01-02", "end": "2024-02-29", "freq": "1b"}, "progress": {"show": False}, "attribution": {"baseline": True}},
        "market": {"mdps": {"rates": {"type": "toy"}}, "pricers": {"primary": {"mdp": "rates"}}},
        "instruments": {"fwd": {"asset_class": "toy", "factory": "pricebt.testing.toys:forward", "conventions": {"strike": 4.0, "notional": 10.0}}},
        "strategy": {"triggers": [{"type": "periodic", "frequency": "2w", "actions": [{"type": "add_trade", "priceables": "fwd", "trade_duration": "1w"}]}]},
    }
    res = api.run(cfg)
    assert res.record.settings.baseline is True and "layer_tay_delta" in res.equity.columns and res.reconcile().ok


# ------------------------------------------------------------------ the result's attribution tables (a run with BOTH the library layers and the baseline)
def _both():
    from pricebt.results import BacktestResult

    rec, _ = run(ScriptedStrategy({OPEN: [OpenOrder(opt(), quantity=3.0)], CLOSE: [CloseOrder()]}), baseline=True, layers=("carry", "delta", "convexity"), cadence="each")
    return BacktestResult.from_record(rec)


def test_attribution_by_layer_does_not_count_the_baseline_rows_as_library_layers():
    res = _both()
    t = res.attribution(by="layer")
    assert not [n for n in t.index if n.startswith("tay_")], list(t.index)
    modelled = [n for n in t.index if n not in ("unexplained", "transactions", "cash_interest", "total")]
    assert "delta" in modelled and "convexity" in modelled, modelled
    lib = t.loc[modelled, "pnl"].sum()
    assert lib + t.loc["unexplained", "pnl"] == pytest.approx(t.loc["total", "pnl"], abs=1e-9), "the library layers plus their own unexplained ARE the total"
    assert abs(t.loc["unexplained", "pnl"]) < abs(t.loc["total", "pnl"]), "and `unexplained` is the small remainder, not minus the total"
    assert t["share"].drop("total").sum() == pytest.approx(1.0, abs=1e-9)


def test_the_baseline_has_its_own_attribution_view_that_also_sums_to_the_total():
    res = _both()
    b = res.attribution(by="baseline")
    assert list(b.index[:3]) == ["tay_delta", "tay_convexity", "tay_unexplained"] and b.index[-1] == "total"
    assert b["pnl"].drop("total").sum() == pytest.approx(b.loc["total", "pnl"], abs=1e-9)
    assert b.loc["total", "pnl"] == pytest.approx(res.attribution(by="layer").loc["total", "pnl"], abs=1e-9), "two decompositions of the same P&L"
    assert b["share"].drop("total").sum() == pytest.approx(1.0, abs=1e-9)


def test_the_position_and_day_tables_also_leave_the_baseline_out_of_the_library_residual():
    res = _both()
    pos = res.attribution(by="position")
    assert not [c for c in pos.columns if c.startswith("tay_")]
    day = res.attribution(by="day")
    assert not [c for c in day.columns if c.startswith("tay_")]
    assert day["net_total"].sum() == pytest.approx(res.attribution(by="layer").loc["total", "pnl"], abs=1e-9)


def test_a_run_without_the_baseline_has_no_baseline_view_and_says_how_to_get_one():
    rec, _ = run(ScriptedStrategy({OPEN: [OpenOrder(fwd(), quantity=3.0)], CLOSE: [CloseOrder()]}), layers=("delta",), cadence="each")
    from pricebt.results import BacktestResult

    with pytest.raises(ResultError, match="baseline"):
        BacktestResult.from_record(rec).attribution(by="baseline")


def test_the_baseline_table_carries_the_transaction_costs_and_the_cash_interest_of_a_rich_run():
    from test_results_common import rich_run

    res = rich_run(baseline=True)
    b, lay = res.attribution(by="baseline"), res.attribution(by="layer")
    assert res.positions["tcost"].sum() > 0 and res.equity["interest_cum"].iloc[-1] != 0.0, "the scenario has costs and interest"
    assert b.loc["transactions", "pnl"] == pytest.approx(-res.positions["tcost"].sum(), abs=1e-12)
    assert b.loc["cash_interest", "pnl"] == pytest.approx(res.equity["interest_cum"].iloc[-1], abs=1e-12)
    assert b.loc["total", "pnl"] == pytest.approx(lay.loc["total", "pnl"], abs=1e-9)
    per_layer = res.record.layers_by_position.query("layer == 'tay_delta'").groupby("position")["pnl"].sum()
    assert len(per_layer) >= 2 and b.loc["tay_delta", "pnl"] == pytest.approx(per_layer.sum(), abs=1e-9), "several positions: summed, not averaged"
