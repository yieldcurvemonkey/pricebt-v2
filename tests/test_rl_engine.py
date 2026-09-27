"""Integration: swaps and bonds of the rateslib adapter through the real Engine on the library-free synthetic market (`Binding(wrap=...)`, the way a config's
`market.pricers.primary.wrap` builds it), and on the real fixtures through the test-support providers when present."""
import copy
import dataclasses
import datetime as dt
import logging
import time

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("rateslib")
pytestmark = pytest.mark.adapter_rateslib

from conftest import assert_identity  # noqa: E402
from support.known import EOD, MIN, needs_fixtures  # noqa: E402
from test_rl_common import BOND_CONV, LAYERS, SWAP_CONV, ctx, engine, eod, synthetic, template, time_context  # noqa: E402

import pricebt.contrib.rateslib as RL  # noqa: E402
from pricebt.orders import OpenOrder  # noqa: E402
from pricebt.testing.scripted import ScriptedStrategy  # noqa: E402
from pricebt.timeutil import TimeGrid  # noqa: E402

log = logging.getLogger("pricebt.rl.timing")
TZ = "America/New_York"


@pytest.fixture(scope="module")
def mdp():
    return synthetic()


@pytest.fixture(scope="module")
def tctx():
    return time_context()


def swap_tpl(name="swap", conv=SWAP_CONV, **kw):
    """A 2y spot-start payer of 10mm at par unless `kw` say otherwise (terms of the shipped swap kit)."""
    return template(RL.swap, conv, {"side": "pay", "maturity": "2Y", "notional": 1e7, **kw}, name, layers=LAYERS)


def bond_tpl(name="bond", conv=BOND_CONV, **kw):
    repo = kw.pop("repo", {"gc_rate": 4.3})
    return template(RL.bond, conv, {"side": "buy", "security": "SYN032032", "notional": 1e7, "extras": {"repo": repo}, **kw}, name, layers=LAYERS)


def run(grid, mdp, opens, entry=2, layers=True, **settings):
    """opens: [(template, quantity)] all opened at grid[entry]; layers=False strips each template's layer list."""
    t_in = grid[entry]
    opens = [(t if layers else _no_layers(t), q) for t, q in opens]
    strat = ScriptedStrategy({t_in: [OpenOrder(t, q) for t, q in opens]})
    settings.setdefault("cadence", "each")
    return engine(grid, strat, mdp, **settings).run()


def _no_layers(t):
    n = copy.copy(t)
    n.spec = dataclasses.replace(t.spec, layers=())
    return n


def healthy(rec):
    assert rec.errors.empty, rec.errors
    assert_identity(rec)
    assert np.isfinite(rec.equity.select_dtypes("number").to_numpy()).all()


def step_layer(rec, name):
    return rec.equity[f"layer_{name}"].diff().fillna(rec.equity[f"layer_{name}"].iloc[0])


# ------------------------------------------------------------------ swaps
def test_swap_payer_receiver_mirror_layers_reconcile_and_delta_explains_the_moves(tctx, mdp):
    grid = TimeGrid.daily("2024-03-04", "2024-05-03", "1b", tctx)
    assert len(grid) >= 30
    pay = run(grid, mdp, [(swap_tpl(), +1.0)])
    rec = run(grid, mdp, [(swap_tpl(), -1.0)])
    healthy(pay), healthy(rec)
    assert (pay.equity["equity"] + rec.equity["equity"]).abs().max() < 1e-6
    for name in (*LAYERS, "unexplained"):
        assert (pay.equity[f"layer_{name}"] + rec.equity[f"layer_{name}"]).abs().max() < 1e-6, name
    step = pay.equity["step_pnl"].iloc[3:]
    resid = step - sum(step_layer(pay, n).iloc[3:] for n in LAYERS)
    assert step.abs().sum() > 5e4 and resid.abs().sum() < 1e-3 * step.abs().sum(), "engine `unexplained` is a rounding-level remainder"
    delta = step_layer(pay, "delta").iloc[3:]
    r2 = 1 - ((step - delta) ** 2).sum() / ((step - step.mean()) ** 2).sum()
    assert r2 > 0.99, r2
    assert pay.layers_by_position.query("layer == 'unexplained'").pnl.abs().iloc[0] < 1.0


def test_seasoned_swap_cash_is_swept_when_the_flow_leaves_the_mark(tctx, mdp):
    grid = TimeGrid.daily("2024-03-04", "2024-03-29", "1b", tctx)
    tpl = swap_tpl(effective=dt.date(2023, 3, 8), maturity="3Y", fixed_rate=4.10)
    rec = run(grid, mdp, [(tpl, 1.0)], entry=0)
    healthy(rec)
    p13 = RL.wrap(mdp.get_pricer(eod("2024-03-13")))
    ref = tpl.build(p13, p13.ts).obj
    assert ref.termination == dt.date(2026, 3, 9)
    pay_day = [p for p in ref._pay_dates if p.year == 2024][0]
    assert pay_day == dt.datetime(2024, 3, 12)
    flows = rec.equity["flows_cum"].diff().fillna(0.0)
    swept = flows[flows != 0.0]
    assert list(swept.index) == [pd.Timestamp("2024-03-13 17:00", tz=TZ)], "swept the day AFTER the payment date (inside V(D), outside V(D+1))"
    ref._prep(p13)
    tab = ref._flows(p13.curve)
    assert swept.iloc[0] == pytest.approx(float(tab[tab.Payment == pay_day].Cashflow.sum()), abs=1e-6)
    step = rec.equity["step_pnl"]
    assert step.abs().max() < 0.25 * abs(swept.iloc[0]) or step.abs().max() < 1.5e5, "no equity jump across the payment date"
    assert abs(step.loc["2024-03-13 17:00"]) < 3.0 * step.drop(step.index[0]).drop(pd.Timestamp("2024-03-13 17:00", tz=TZ)).abs().median() + 2e4


def test_swap_positions_open_during_the_run_and_forward_start_are_par_at_entry(tctx, mdp):
    grid = TimeGrid.daily("2024-03-04", "2024-04-05", "1b", tctx)
    rec = run(grid, mdp, [(swap_tpl(maturity="5Y", effective="3M"), 1.0)], layers=False)
    healthy(rec)
    assert abs(rec.equity["equity"].iloc[2]) < 1e-3 * 1e7 * 1e-2, "par at inception: entry mark ~ 0 on a 10mm notional"
    assert rec.positions.iloc[0]["entry_pv"] == pytest.approx(0.0, abs=1e3)


# ------------------------------------------------------------------ bonds
def test_bond_long_short_mirror_financing_coupon_and_layers(tctx, mdp):
    grid = TimeGrid.daily("2024-07-15", "2024-09-06", "1b", tctx)
    assert len(grid) >= 30
    lo = run(grid, mdp, [(bond_tpl(), +1.0)])
    sh = run(grid, mdp, [(bond_tpl(), -1.0)])
    healthy(lo), healthy(sh)
    assert (lo.equity["equity"] + sh.equity["equity"]).abs().max() < 1e-6
    for name in (*LAYERS, "unexplained"):
        assert (lo.equity[f"layer_{name}"] + sh.equity[f"layer_{name}"]).abs().max() < 1e-6, name
    assert lo.equity["financing_cum"].iloc[-1] < 0 < sh.equity["financing_cum"].iloc[-1]
    flows = lo.equity["flows_cum"].diff().fillna(0.0)
    swept = flows[flows != 0.0]
    assert list(swept.index) == [pd.Timestamp("2024-08-16 17:00", tz=TZ)] and swept.iloc[0] == pytest.approx(1e7 * 0.04125 / 2)
    step = lo.equity["step_pnl"].iloc[3:]
    layer_sum = sum(step_layer(lo, n).iloc[3:] for n in LAYERS)
    assert (step - layer_sum).abs().sum() < 2e-3 * step.abs().sum() + 200.0
    delta = step_layer(lo, "delta").iloc[3:]
    assert 1 - ((step - delta - step_layer(lo, "carry").iloc[3:]) ** 2).sum() / ((step - step.mean()) ** 2).sum() > 0.999


def test_bond_pnl_is_smooth_through_the_coupon_date(tctx):
    grid = TimeGrid.daily("2024-08-12", "2024-08-20", "1b", tctx)
    flat = synthetic(daily_bp=(0.0, 0.0, 0.0))
    rec = run(grid, flat, [(bond_tpl(repo={}), 1.0)], entry=0, layers=False)
    healthy(rec)
    assert (rec.equity["step_pnl"].iloc[1:].abs() < 4000.0).all(), rec.equity["step_pnl"]  # accrual (3.2k over a weekend), never the 206k coupon


# ------------------------------------------------------------------ intraday
def test_intraday_marks_carry_only_at_the_date_flip_and_financing_runs_on_seconds(tctx):
    m = synthetic("2024-03-01", "2024-03-29", seed=5, intraday_bp=1.0)
    grid = TimeGrid.intraday("2024-03-06", "2024-03-07", "30min", tctx)
    rec = run(grid, m, [(swap_tpl(), 1.0), (bond_tpl(), 1.0)], entry=1)
    healthy(rec)
    carry = step_layer(rec, "carry")
    idx = list(carry.index)
    same_day = [t for a, t in zip(idx[1:], idx[2:]) if a.date() == t.date()]
    swap_only = run(grid, m, [(swap_tpl(), 1.0)], entry=1)
    sc = step_layer(swap_only, "carry")
    assert all(abs(sc.loc[t]) < 1e-8 for t in same_day if t in sc.index and t > grid[1]), "curves have a date-only clock: no intraday carry"
    flip = [t for t in sc.index if t.date() == dt.date(2024, 3, 7)][0]
    assert abs(sc.loc[flip] + step_layer(swap_only, "roll").loc[flip]) > 1.0, "the day's time P&L lumps at the reference-date flip"
    fin = rec.equity["financing_cum"].diff().iloc[2:]
    secs = pd.Series(rec.equity.index).diff().dt.total_seconds().to_numpy()[2:]
    ratio = (fin.to_numpy() / secs)
    assert ratio.std() / abs(ratio.mean()) < 0.02, "financing per second is constant across unequal steps"


# ------------------------------------------------------------------ cost and hygiene
def test_run_is_quiet_and_timings_are_bounded(tctx, mdp, capsys):
    grid = TimeGrid.daily("2024-03-04", "2024-04-30", "1b", tctx)
    opens = [(swap_tpl(), 1.0), (bond_tpl(), 1.0)]
    t = time.perf_counter()
    plain = run(grid, mdp, opens, layers=False)
    t_plain = time.perf_counter() - t
    t = time.perf_counter()
    full = run(grid, mdp, opens)
    t_layers = time.perf_counter() - t
    out = capsys.readouterr()
    assert "SUCCESS" not in out.out and out.out == "" and "it/s" not in out.err
    n = len(grid)
    log.info("2 positions x %d daily marks: no layers %.1f ms/mark, carry/roll/delta/convexity %.1f ms/mark", n, 1e3 * t_plain / n, 1e3 * t_layers / n)
    assert t_plain / n < 0.25 and t_layers / n < 1.0
    assert plain.errors.empty and full.errors.empty


# ------------------------------------------------------------------ real fixtures
@pytest.mark.fixtures
@needs_fixtures
def test_real_curves_seasoned_swap_and_ust_run_through_the_engine():
    pytest.importorskip("pyarrow")
    from support.common import BOND_CALENDAR, SWAP_CALENDAR
    from support.curves import CurveStore
    from support.ust_eod import UstEod

    win = ("2025-01-02", "2025-02-28")
    mdp = UstEod(curve_mdp=CurveStore(EOD, window=win), window=win)  # the test-support provider: funding curve + fixings + FedInvest quotes, as ONE snapshot
    ts = mdp.available_timestamps(pd.Timestamp("2025-01-02", tz=TZ), pd.Timestamp("2025-02-28 23:59", tz=TZ))
    assert len(ts) >= 30
    ct10 = mdp.get_pricer(ts[0]).snapshot.quotes.aliases["CT10"]
    ctx_ = time_context()
    grid = TimeGrid([ctx_.at(t.date(), time=dt.time(17, 0)) for t in ts], ctx_)
    sw, bd = {**SWAP_CONV, "calendar": SWAP_CALENDAR}, {**BOND_CONV, "calendar": BOND_CALENDAR}  # the provider's own calendar names
    seasoned = swap_tpl(conv=sw, effective=dt.date(2024, 2, 8), maturity="5Y", notional=25e6, fixed_rate=4.10)
    tpls = [(seasoned, 1.0), (swap_tpl("recv10y", sw, side="receive", maturity="10Y", notional=10e6), 1.0), (bond_tpl(conv=bd, security=ct10, notional=10e6), 1.0)]
    rec = run(grid, mdp, tpls, entry=1, cadence="each")
    healthy(rec)
    step = rec.equity["step_pnl"].iloc[3:]
    tot = sum(step_layer(rec, n).iloc[3:] for n in LAYERS)
    assert step.abs().sum() > 1e5
    assert (step - tot).abs().sum() < 0.01 * step.abs().sum(), "unexplained is <1% of gross daily P&L on real curves"
    assert rec.layers_by_position.query("layer == 'unexplained'").pnl.abs().max() < 2000.0
    assert rec.equity["flows_cum"].abs().max() > 0, "the seasoned swap pays a coupon inside the window"


@pytest.mark.fixtures
@needs_fixtures
def test_real_minute_curves_run_a_two_day_intraday_backtest():
    pytest.importorskip("pyarrow")
    from support.curves import CurveStore

    mdp = CurveStore(MIN, window=("2026-08-04", "2026-08-05"))
    ctx_ = time_context(session_open=dt.time(9, 30), session_close=dt.time(16, 0))
    grid = TimeGrid.intraday("2026-08-04", "2026-08-05", "30min", ctx_)
    assert len(grid) >= 26
    sw = {**SWAP_CONV, "calendar": "usd_fed"}
    seasoned = swap_tpl(conv=sw, effective=dt.date(2026, 1, 8), maturity="5Y", notional=25e6, fixed_rate=3.9)
    fresh = swap_tpl("fresh", sw, maturity="7Y", notional=10e6)
    rec = run(grid, mdp, [(seasoned, 1.0), (fresh, -1.0)], entry=1)
    healthy(rec)
    step = rec.equity["step_pnl"].iloc[3:]
    tot = sum(step_layer(rec, n).iloc[3:] for n in LAYERS)
    assert step.abs().sum() > 1e3 and (step - tot).abs().sum() < 0.02 * step.abs().sum() + 5.0
    carry = step_layer(rec, "carry")
    idx = list(carry.index)
    intraday = [t for a, t in zip(idx[1:], idx[2:]) if a.date() == t.date()]
    assert all(abs(carry.loc[t]) < 1e-6 for t in intraday), "minute curves carry a date-only clock"
    assert rec.manifest["fetches"] >= len(grid) - 1


def test_engine_measures_match_direct_calls_of_the_instrument(tctx, mdp):
    grid = TimeGrid.daily("2024-03-04", "2024-04-05", "1b", tctx)
    entry = RL.wrap(mdp.get_pricer(grid[2]))
    last, prev = RL.wrap(mdp.get_pricer(grid[-1])), RL.wrap(mdp.get_pricer(grid[-2]))
    for tpl, qty, meas in [(swap_tpl(), 2.0, ("dv01", "gamma")), (bond_tpl(), -1.0, ("dv01", "gamma", "ytm"))]:
        rec = run(grid, mdp, [(tpl, qty)], layers=False, measures=meas)
        healthy(rec)
        obj = tpl.build(entry, grid[2]).obj
        c = ctx(last, prev)
        for m in meas:
            direct = getattr(obj, m)(ctx=c)
            assert rec.equity[f"measure_{m}"].iloc[-1] == pytest.approx(qty * direct, rel=1e-9), (type(obj).__name__, m)
        assert (rec.equity[f"measure_{meas[0]}"].iloc[:2] == 0.0).all() and rec.equity[f"measure_{meas[0]}"].iloc[3] != 0.0
