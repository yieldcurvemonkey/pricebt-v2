import math

import numpy as np
import pandas as pd
import pytest

from conftest import make_engine
from pricebt.engine.state import RunRecord
from pricebt.errors import PricebtError
from pricebt.orders import OpenOrder, PositionMeta
from pricebt.results import BacktestResult, ResultError
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyMDP
from test_results_common import D, forward_run, fwd, make_grid, rich_run, run, synth_record, zero

pytestmark = pytest.mark.core

SPY = 365.25 * 5.0 / 7.0


def rate(ts):
    return ToyMDP().get_pricer(ts).rate


def test_closed_trade_log_matches_closed_form():
    res = forward_run()
    t_in, t_out = D("2024-01-10"), D("2024-02-07")
    price = 3.0 * 10.0 * (rate(t_out) - rate(t_in))
    income = 3.0 * 10.0 * 0.5 * 0.01 * 28.0
    (row,) = res.closed_trades().to_dict("records")
    assert row["pnl_price"] == pytest.approx(price, abs=1e-9)
    assert row["pnl_income"] == pytest.approx(income, abs=1e-9)  # carry realised during the hold, booked as cash
    assert row["pnl_gross"] == pytest.approx(price + income, abs=1e-9)
    assert row["tcost"] == pytest.approx(3.0) and row["pnl_net"] == pytest.approx(price + income - 3.0, abs=1e-9)
    assert res.equity["equity"].iloc[-1] == pytest.approx(row["pnl_net"], abs=1e-9)
    assert row["exit_reason"] == "scheduled" and row["side"] == "long" and row["tags"] == "macro,core"
    assert row["hold_days"] == 28.0 and row["hold_seconds"] == 28 * 86400.0
    grid = make_grid().points
    assert row["hold_points"] == int(((grid > t_in) & (grid <= t_out)).sum()) == 20
    assert row["entry_pv"] == pytest.approx(10.0 * (rate(t_in) - 4.0)) and row["exit_pv"] == pytest.approx(10.0 * (rate(t_out) - 4.0))


def test_hold_points_counts_timeline_points_including_off_grid_exit():
    off = pd.Timestamp("2024-01-20 12:00", tz="America/New_York")
    res = run({D("2024-01-10"): [OpenOrder(fwd(), final_ts=off)]})
    grid = make_grid().points
    expected = int(((grid > D("2024-01-10")) & (grid <= off)).sum()) + 1  # the off-grid exit point itself
    assert res.closed_trades().iloc[0]["hold_points"] == expected
    assert res.closed_trades().iloc[0]["hold_days"] == pytest.approx((off - D("2024-01-10")).total_seconds() / 86400.0)


def test_financing_is_part_of_realised_income():
    res = run({D("2024-01-10"): [OpenOrder(zero(), quantity=10.0, final_ts=D("2024-02-07"))]})
    row = res.closed_trades().iloc[0]
    assert row["pnl_income"] < 0 and row["pnl_income"] == pytest.approx(res.equity["financing_cum"].iloc[-1])
    assert row["pnl_net"] == pytest.approx(res.equity["equity"].iloc[-1], abs=1e-9)
    assert row["pnl_price"] + row["pnl_income"] == pytest.approx(row["pnl_gross"])


def test_open_positions_and_status_split():
    res = rich_run()
    assert set(res.closed_trades().index) == {"P000001"}
    assert set(res.open_positions().index) == {"P000002", "P000003"}
    open_row = res.open_positions().loc["P000002"]
    assert open_row["hold_points"] == int((make_grid().points > D("2024-01-10")).sum())  # up to the last point
    assert pd.isna(open_row["exit_ts"]) and open_row["exit_reason"] == ""


def test_result_summary_gs_identity_and_columns():
    res = rich_run()
    s = res.result_summary
    assert list(s.columns) == ["Price", "Cumulative Cash", "Transaction Costs", "Total"]
    assert (s["Total"] - (s["Price"] + s["Cumulative Cash"] + s["Transaction Costs"])).abs().max() < 1e-9
    assert (s["Total"] == res.equity["equity"]).all() and (s["Transaction Costs"] <= 0).all()


def test_trade_ledger_gs_vocabulary_and_values():
    res = forward_run()
    tl = res.trade_ledger()
    assert list(tl.columns) == ["Open", "Close", "Open Value", "Close Value", "Long Short", "Status", "Trade PnL"]
    row, pos = tl.iloc[0], res.positions.iloc[0]
    assert row["Open Value"] == pytest.approx(-3.0 * pos["entry_pv"]) and row["Close Value"] == pytest.approx(3.0 * pos["exit_pv"])
    assert row["Long Short"] == "Long" and row["Status"] == "closed" and row["Trade PnL"] == pytest.approx(pos["pnl_gross"])
    rr = rich_run().trade_ledger()
    assert set(rr["Status"]) == {"closed", "open"}
    o = rr.loc["P000002"]
    assert o["Close Value"] == pytest.approx(rich_run().equity["positions_value"].iloc[-1] - rr.loc["P000003", "Close Value"])  # open marks sum to positions_value


def test_short_side_and_sign_conventions():
    res = run({D("2024-01-10"): [OpenOrder(fwd(notional=2.0), quantity=-1.0, final_ts=D("2024-01-31"))]})
    row = res.closed_trades().iloc[0]
    assert row["side"] == "short" and res.trade_ledger().iloc[0]["Long Short"] == "Short"
    assert row["pnl_price"] == pytest.approx(-2.0 * (rate(D("2024-01-31")) - rate(D("2024-01-10"))), abs=1e-9)


def test_summary_stats_against_independent_computation():
    res = rich_run()
    s = res.summary_stats()
    step = res.equity["equity"].diff().fillna(res.equity["equity"].iloc[0]).to_numpy()  # independent of the engine's step_pnl column
    assert s["periods_per_year"] == pytest.approx(SPY)
    assert s["sharpe"] == pytest.approx(step.mean() / step.std(ddof=1) * math.sqrt(SPY), rel=1e-9)
    assert s["total_pnl"] == pytest.approx(res.equity["equity"].iloc[-1])
    assert s["max_drawdown"] == pytest.approx((np.cumsum(step) - np.maximum.accumulate(np.maximum(np.cumsum(step), 0.0))).min())
    assert s["n_trades"] == 3 and s["n_closed"] == 1 and s["n_open"] == 2 and s["n_resizes"] == 1 and s["n_fills"] == 5
    assert s["total_tcost"] == pytest.approx(-0.5) and s["n_errors"] == 0
    assert 0.0 < s["time_in_market"] <= 1.0


def test_summary_stats_nav_basis_when_capital_given():
    res = rich_run(initial_capital=1_000.0)
    s = res.summary_stats()
    assert s["basis"] == "nav" and s["total_pnl"] == pytest.approx(res.pnl.iloc[-1])
    r = res.equity["equity"].pct_change().fillna(res.equity["equity"].iloc[0] / 1_000.0 - 1.0).to_numpy()
    assert s["mean"] == pytest.approx(r.mean())


def test_gs_labels_and_invalid_label_mode():
    res = forward_run()
    gs = res.summary_stats(labels="gs")
    for k in ("Total PnL", "Sharpe Ratio", "Sortino Ratio", "Max Drawdown", "Annualised Return", "Total Transaction Costs", "VaR 95%", "CVaR 95%", "Total Trades"):
        assert k in gs.index
    assert gs["Total Transaction Costs"] == -3.0
    with pytest.raises(ResultError):
        res.summary_stats(labels="nope")


def test_flat_strategy_result_is_well_formed():
    eng, _, _ = make_engine(make_grid(), ScriptedStrategy())
    res = BacktestResult(eng.run())
    assert res.positions.empty and res.closed_trades().empty and res.trade_ledger().empty and res.open_positions().empty
    s = res.summary_stats()
    assert s["total_pnl"] == 0.0 and math.isnan(s["sharpe"]) and s["n_trades"] == 0 and math.isnan(s["trade_hit_rate"])
    assert res.reconcile().ok
    for by in ("layer", "component", "position", "template", "tag", "day"):
        assert res.attribution(by)["net_total" if by not in ("layer", "component") else "pnl"].abs().sum() == 0.0


def test_errors_accessor_under_record_policy():
    bad = D("2024-01-16")
    res = run({D("2024-01-10"): [OpenOrder(fwd())]}, mdp=ToyMDP(fail_at=[bad]), on_error="record")
    assert res.n_errors >= 1 and (res.errors["ts"] == bad).any()
    assert res.summary_stats()["n_errors"] == res.n_errors
    rep = res.reconcile()
    assert rep.ok and not rep["errors"].passed and rep["errors"].severity == "warn"


def test_manifest_grid_and_hash_and_repr():
    res = forward_run()
    m = res.manifest
    assert m["config_hash"] == "abc123" and m["grid"]["n_points"] == len(make_grid()) and m["n_points"] == len(make_grid())
    assert res.grid_info["median_step_seconds"] in (86400.0,) and res.grid_info["points_per_session"] == 1.0
    assert "backtest" in repr(res) and res.name == "backtest"
    assert res.manifest is not res.record.manifest  # a copy


def test_constructor_rejects_empty_and_naive_equity():
    rec = synth_record([1.0, 2.0])
    with pytest.raises(ResultError):
        BacktestResult(RunRecord(**{**rec.__dict__, "equity": pd.DataFrame()}))
    naive = rec.equity.copy()
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(ResultError):
        BacktestResult(RunRecord(**{**rec.__dict__, "equity": naive}))
    assert issubclass(ResultError, PricebtError)


def test_pnl_step_and_measure_accessors():
    res = rich_run(measures=("dv01",))
    assert res.pnl.iloc[-1] == pytest.approx(res.equity["equity"].iloc[-1])
    assert list(res.measures.columns) == ["dv01"] and res.measures["dv01"].iloc[-1] != 0.0
    assert (res.step_pnl.cumsum() - res.pnl).abs().max() < 1e-9
    assert "roll" in res.layers.columns and "unexplained" in res.layers.columns
    assert set(res.layers_by_position.columns) >= {"roll", "delta", "convexity", "unexplained"}


def test_lag_one_result_positions_and_ledger():
    res = run({D("2024-01-10"): [OpenOrder(fwd(notional=1.0), final_ts=D("2024-02-07"))]}, fill_lag=1)
    row = res.closed_trades().iloc[0]
    assert row["pnl_net"] == pytest.approx(res.equity["equity"].iloc[-1], abs=1e-9)
    assert row["entry_pv"] == pytest.approx(rate(D("2024-01-10")) - 4.0)  # priced at the decision point


def test_metadata_columns_come_from_position_meta():
    res = run({D("2024-01-10"): [OpenOrder(fwd(), meta=PositionMeta(action="my_action", kind="hedge"), tags=("x",))]})
    row = res.positions.iloc[0]
    assert (row["action"], row["kind"], row["tags"], row["template"]) == ("my_action", "hedge", "x", "fwd")
