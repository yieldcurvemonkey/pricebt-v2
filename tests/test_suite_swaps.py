"""Smoke tests of the SWAP strategy suite (configs/suite/s1*, s2*, s3*, s4*, s08*, s11*, s12*) on REAL fixture curves.

The suite is library-neutral: ONE instrument spec `usd_sofr_ois` (side, maturity and notional are TERMS of each action), the market data comes from the test-support
curve store (`support.curves:CurveStore`, plain snapshots) and the pricing library is a STACK OVERLAY (`configs/adapters/<stack>_swap.yaml`). Every config is run end to
end through `pricebt.api.build(config, sets=..., stack=...)` (exactly the CLI path) on a short window, and the controls / invariants of the suite are asserted: strict
reconcile, always-flat is exactly 0, payer/receiver mirror, notional scaling, cost monotonicity, fill_lag invariance of buy-and-hold, steepener sign against independently
computed par rates, DV01 neutrality, z-score entries reproduced from par rates, minute-grid carry/roll = 0 intraday, and the first rows of the recorded pre-refactor
results reproduced. The full-window runs and report tables are produced by tools/run_suite_swaps.py; tasks/suite_reproduction.md compares them with the recorded results.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from suite_common import CFG as DEFAULT_CFG, EOD, RECORDED, ROOT, build_config, needs_fixtures, needs_recorded, require_lib, STACKS, ny

CFG = Path(os.environ.get("PRICEBT_SWAP_SUITE_CFG", DEFAULT_CFG))  # override = mutation testing of the configs
NO_LAYERS = "backtest.attribution.layers=[]"
SWAP_CONFIGS = ("s1_swap_carry_eod", "s1m_swap_pay_mirror", "s1f_swap_carry_full_history", "s2_steepener_2s10s", "s3_fly_2s5s10s", "s4_meanrev_2s10s",
                "s08_swap_intraday_min", "s08b_swap_minute_hold_week", "s11_buy_hold_10y", "s12_always_flat")
ACTION_KEYS = ("priceables", "priceable")


def eod(start: str, end: str, mdp: str = "sofr") -> list:
    """Short EOD window; the provider only indexes a month around it (fast)."""
    lo = (pd.Timestamp(start) - pd.Timedelta(days=10)).date()
    hi = (pd.Timestamp(end) + pd.Timedelta(days=10)).date()
    return [f"backtest.grid.start={start}", f"backtest.grid.end={end}", f"market.mdps.{mdp}.kwargs.window=[{lo}, {hi}]"]


def run(name: str, sets=(), stack: str = "rateslib"):
    require_lib(stack)
    return build_config(f"{name}.yaml", [*sets, "outputs.dir=null"], stack=stack, cfg_dir=CFG).run()


def healthy(r) -> None:
    rep = r.reconcile(strict=True)  # raises ReconcileError on any failed identity
    assert rep.ok and r.n_errors == 0
    assert np.isfinite(r.equity.select_dtypes("number").to_numpy()).all()


def gross(r) -> pd.Series:
    return r.equity["equity"] - r.equity["tcost"]


def par_rates(stamps, tenors=("2Y", "10Y")) -> pd.DataFrame:
    """Independent of the engine, the strategy layer AND the adapter: raw rateslib on the store curve's node table at each stamp, spot par rates in bp."""
    from tools.swap_suite_support import par_panel

    return par_panel(stamps, tenors) * 100.0


def load_yaml(name: str) -> dict:
    from pricebt import api

    return api.load(CFG / f"{name}.yaml")


def walk_actions(node):
    """Every action mapping of a config's trigger tree (aggregate triggers nest their own)."""
    if isinstance(node, dict):
        if "type" in node and any(k in node for k in ACTION_KEYS + ("scaling_type", "measure")) and "instrument" not in node:
            yield node
        for v in node.values():
            yield from walk_actions(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_actions(v)


def trade_specs(cfg: dict):
    """(action, trade mapping) of every trade an action of the config names."""
    for a in walk_actions(cfg.get("strategy", {})):
        for k in ACTION_KEYS:
            if k in a:
                yield a, a[k]


# ------------------------------------------------------------------ the migrated configs: one instrument spec plus terms (no fixtures, no library)
@pytest.mark.core
@pytest.mark.parametrize("name", SWAP_CONFIGS)
def test_a_swap_suite_config_is_one_instrument_spec_plus_per_action_terms(name):
    cfg = load_yaml(name)
    from pricebt.config.loader import TOP_KEYS

    assert set(cfg["instruments"]) == {"usd_sofr_ois"}, "no per-direction or per-tenor instruments (spec T1, SC6)"
    assert set(cfg) <= TOP_KEYS and "trades" not in cfg, "no key of the old config grammar, no pre-baked trade: the trades are the terms of the actions"
    spec = cfg["instruments"]["usd_sofr_ois"]
    assert spec["asset_class"] == "swap" and "bind" not in spec, "the kit ships the bindings; a config writes none unless it overrides"
    n = 0
    for _, trade in trade_specs(cfg):
        n += 1
        assert trade["instrument"] == "usd_sofr_ois" and set(trade["terms"]) >= {"side", "maturity", "notional"}, trade
        assert trade["terms"]["side"] in ("pay", "receive") and trade["terms"]["fixed_rate"] == "par"
    assert n >= 1
    assert cfg["outputs"]["dir"].replace("\\", "/").endswith(f"results_new/{name}"), "the suite never writes over the recorded results/"


@pytest.mark.core
def test_the_one_spec_plus_terms_check_is_not_vacuous():
    """Twin of the check above: a config with a second (per-tenor) instrument, an old-grammar key, or a trade that names another instrument or lacks a term is refused by the same predicates."""
    import copy

    from pricebt.config.loader import TOP_KEYS

    cfg = load_yaml("s2_steepener_2s10s")
    two = copy.deepcopy(cfg)
    two["instruments"]["usd_sofr_ois_10y"] = copy.deepcopy(two["instruments"]["usd_sofr_ois"])
    assert set(two["instruments"]) != {"usd_sofr_ois"}
    assert not set({**cfg, "instrument": {}}) <= TOP_KEYS, "a key outside the grammar (a typo, or an old one) is seen"
    bad = copy.deepcopy(cfg)
    next(iter(trade_specs(bad)))[1]["terms"].pop("side")
    assert any(not set(t["terms"]) >= {"side", "maturity", "notional"} for _, t in trade_specs(bad))


@pytest.mark.core
def test_the_suite_conventions_block_is_one_shared_block_naming_the_snapshot_calendar():
    base = load_yaml("_swap_base")["instruments"]["usd_sofr_ois"]
    assert base["conventions"]["calendar"] == "usd_fed"
    for name in ("s1_swap_carry_eod", "s2_steepener_2s10s", "s08_swap_intraday_min", "s07_swap_spread"):
        assert load_yaml(name)["instruments"]["usd_sofr_ois"]["conventions"] == base["conventions"], name


@pytest.mark.core
@pytest.mark.parametrize("name", [*SWAP_CONFIGS, "s07_swap_spread"])
def test_every_stack_overlay_leaves_the_base_digest_unchanged_and_sets_only_factory_and_wrap(name):
    """Spec X1: the three stacks share one base (conventions, terms, strategy, grid, signals, costs are identical); the harness-side check is `base_digest`."""
    from pricebt import api
    from pricebt.config.stacks import apply_stack, base_digest, validate_overlay

    cfg = load_yaml(name)
    kinds = ("swap", "bond") if name == "s07_swap_spread" else ("swap",)
    digests = set()
    for stack in STACKS:
        out = cfg
        for f in build_overlays(stack, kinds):
            overlay = api.load(f)
            validate_overlay(overlay)
            assert set(overlay) == {"instruments", "market"} and all(set(v) == {"factory"} for v in overlay["instruments"].values()), f
            out = apply_stack(out, overlay)
        digests.add(base_digest(out))
        assert out["instruments"]["usd_sofr_ois"]["conventions"] == cfg["instruments"]["usd_sofr_ois"]["conventions"]
    assert digests == {base_digest(cfg)}


def build_overlays(stack, kinds):
    from suite_common import overlays

    return overlays(stack, *kinds)


@pytest.mark.core
def test_the_stack_overlay_check_is_not_vacuous_a_convention_change_moves_the_digest():
    """Non-vacuity of the digest test above: an overlay that touched the shared conventions would be rejected, and a changed convention changes the digest."""
    import copy

    from pricebt.config.stacks import base_digest, validate_overlay
    from pricebt.errors import ConfigError

    cfg = load_yaml("s1_swap_carry_eod")
    other = copy.deepcopy(cfg)
    other["instruments"]["usd_sofr_ois"]["conventions"]["day_count"] = "act365f"
    assert base_digest(other) != base_digest(cfg)
    with pytest.raises(ConfigError):
        validate_overlay({"instruments": {"usd_sofr_ois": {"conventions": {"day_count": "act365f"}}}})


@pytest.mark.fixtures
@needs_fixtures
def test_the_suite_calendar_is_a_copy_of_the_calendar_the_curve_store_serves():
    """`backtest.calendar` cannot name the fixture file (its name is a banned token in shipped configs), so the suite carries a copy; it must not drift."""
    import json

    from support.common import calendar_data

    mine = json.loads((CFG / "calendars" / "usd_fed.json").read_text(encoding="utf8"))
    served = calendar_data("nyc")
    assert tuple(pd.Timestamp(h).date() for h in mine["holidays"]) == served.holidays and mine["weekmask"] == served.weekmask


@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_the_independent_par_panel_reproduces_the_par_rates_computed_by_the_data_infrastructure():
    """Known answers (support.known.ARBS_PAR_10Y): the raw-rateslib panel used by the tests below returns the 10Y par of two store rows."""
    require_lib("rateslib")
    from support.known import ARBS_PAR_10Y

    for stamp in ("2026-08-05 17:00", "2024-06-14 17:00"):
        got = par_rates([ny(stamp)], ("10Y",)).iloc[0]["10Y"] / 100.0
        assert got == pytest.approx(ARBS_PAR_10Y[(EOD, stamp)], abs=1e-8), stamp
    two = par_rates([ny("2024-06-14 17:00")], ("2Y", "10Y")).iloc[0]
    assert two["2Y"] != two["10Y"], "not a constant"


# ------------------------------------------------------------------ every config builds, under every stack
@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@pytest.mark.adapter_quantlib
@needs_fixtures
@pytest.mark.parametrize("name", SWAP_CONFIGS)
def test_every_swap_suite_config_builds_under_every_stack_with_one_base(name):
    pytest.importorskip("pyarrow")
    mdp = "sofr_min" if name.startswith("s08") else "sofr"
    sets = [f"market.mdps.{mdp}.kwargs.window=[2026-03-01, 2026-03-31]"] if name.startswith("s08") else eod("2024-01-02", "2024-01-31")
    seen = {}
    for stack in STACKS:
        try:
            require_lib(stack)
        except pytest.skip.Exception:
            continue
        b = build_config(f"{name}.yaml", sets, stack=stack, cfg_dir=CFG)
        assert len(b.grid) > 10 and b.strategy.triggers and set(b.instruments) == {"usd_sofr_ois"}
        assert b.outputs.get("dir", "").replace("\\", "/").endswith(f"results_new/{name}")
        seen[stack] = b.base_hash
    assert seen and len(set(seen.values())) == 1, seen


# ------------------------------------------------------------------ S12 / S11 controls
@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s12_always_flat_is_exactly_zero():
    r = run("s12_always_flat", eod("2024-01-02", "2024-03-28"))
    healthy(r)
    assert (r.equity["equity"].to_numpy() == 0.0).all()
    assert r.trades.empty and r.positions.empty and r.orders.empty and r.record.layers_by_position.empty


@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s11_buy_and_hold_terminal_equity_is_fill_lag_invariant():
    w = [*eod("2022-01-03", "2022-02-15"), NO_LAYERS]
    r0, r1 = run("s11_buy_hold_10y", w), run("s11_buy_hold_10y", [*w, "backtest.fill_lag=1"])
    healthy(r0)
    healthy(r1)
    assert len(r0.positions) == len(r1.positions) == 1
    assert r1.pnl.iloc[-1] == pytest.approx(r0.pnl.iloc[-1], abs=1e-6)
    assert abs(r0.pnl.iloc[-1]) > 1e4, "the control is not vacuous: the swap moved"


# ------------------------------------------------------------------ S1: mirror, scaling
@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s1_receiver_payer_mirror_and_notional_scaling_on_real_curves():
    w = eod("2024-01-02", "2024-02-15")
    rec, pay = run("s1_swap_carry_eod", w), run("s1m_swap_pay_mirror", w)
    x2 = run("s1_swap_carry_eod", [*w, "strategy.triggers.0.actions.0.priceables.terms.notional=2e7", NO_LAYERS])
    assert len(rec.positions) == 2, "the window contains one monthly roll"
    for r in (rec, pay, x2):
        healthy(r)
    scale = float(gross(rec).abs().max())
    assert scale > 1e4
    assert float((gross(rec) + gross(pay)).abs().max()) <= 1e-9 * scale
    for c in rec.layers.columns:
        assert float((rec.layers[c] + pay.layers[c]).abs().max()) <= 1e-9 * scale, c
    assert float((x2.pnl - 2.0 * rec.pnl).abs().max()) <= 1e-9 * scale
    assert float(rec.equity["measure_dv01"].iloc[0]) < -7000 and float(pay.equity["measure_dv01"].iloc[0]) > 7000, "receiver -dv01, payer +dv01"
    att = rec.attribution("layer")["pnl"]
    assert abs(att["unexplained"]) <= 0.02 * sum(abs(att[k]) for k in ("carry", "roll", "delta", "convexity"))


@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s1_monthly_rolls_never_overlap_and_the_1m_duration_trap_is_detected():
    """Schedule = base date + k months (01-02, 02-02, 03-02 -> Mon 03-04, 04-02); a `1m` exit from the ROLLED 03-04 entry lands on 04-04,
    so `trade_duration: 1m` holds two swaps on 04-02..04-03. The suite uses `next schedule`."""
    w = [*eod("2024-01-02", "2024-04-15"), NO_LAYERS]
    ok = run("s1_swap_carry_eod", w)
    trap = run("s1_swap_carry_eod", [*w, "strategy.triggers.0.actions.0.trade_duration=1m"])
    healthy(ok)
    healthy(trap)
    assert int(ok.equity["n_positions"].max()) == 1
    assert int(trap.equity["n_positions"].max()) == 2, "control: the check can see an overlap"


@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s1f_full_history_config_runs_on_its_earliest_window():
    """Early 2018: 26-node curves and the first months of SOFR fixings (2018-04-02); swaps become seasoned inside the window."""
    r = run("s1f_swap_carry_full_history", [*eod("2018-06-01", "2018-07-20"), NO_LAYERS])
    healthy(r)
    assert len(r.positions) == 2 and abs(float(r.pnl.iloc[-1])) > 1e3


# ------------------------------------------------------------------ S2: steepener sign, neutrality, costs
@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s2_is_a_dv01_neutral_steepener_against_independent_par_rates():
    r = run("s2_steepener_2s10s", [*eod("2024-01-02", "2024-03-28"), NO_LAYERS])
    healthy(r)
    eq = r.equity
    core = r.positions[r.positions["kind"] == "scaled"]
    entries = pd.DatetimeIndex(core["entry_ts"]).unique()
    assert len(entries) == 3 and float(eq.loc[entries, "measure_dv01"].abs().max()) < 1e-6, "legs sized to +/-10,000 at the same pricer"
    assert (core["entry_quantity"] > 0).all() and set(core["template"]) == {"pay10y", "recv2y"}, "pay 10Y and receive 2Y (never the reverse)"
    reb = r.positions[r.positions["kind"] == "rebalance"]
    if len(reb):
        assert float(eq.loc[pd.DatetimeIndex(reb["entry_ts"]).unique(), "measure_dv01"].abs().max()) < 1e-6, "rebalance restores dv01 neutrality"
    pr = par_rates(list(eq.index))
    slope = (pr["10Y"] - pr["2Y"]).diff()
    y = (r.step_pnl - eq["tcost"].diff().fillna(eq["tcost"])).iloc[1:]
    X = np.column_stack([np.ones(len(y)), slope.iloc[1:].to_numpy()])
    beta = np.linalg.lstsq(X, y.to_numpy(), rcond=None)[0][1]
    assert 8500.0 <= beta <= 11500.0, f"P&L per bp of 2s10s steepening = {beta:,.0f}; negative would be a flattener"


@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s2_transaction_costs_are_monotone_and_leave_gross_unchanged():
    base = [*eod("2024-01-02", "2024-02-29"), "backtest.attribution.layers=[]"]
    pre = ("strategy.triggers.0.actions.0", "strategy.triggers.0.actions.1", "strategy.triggers.1.actions.0")
    rs = [run("s2_steepener_2s10s", [*base, *(f"{p}.transaction_cost.scaling_level={lvl}" for p in pre)]) for lvl in (0.0, 0.1, 0.2)]
    for r in rs:
        healthy(r)
    g0 = gross(rs[0])
    assert all(float((gross(r) - g0).abs().max()) <= 1e-6 for r in rs)
    net = [float(r.pnl.iloc[-1]) for r in rs]
    assert net[0] > net[1] > net[2]
    tc = [-float(r.equity["tcost"].iloc[-1]) for r in rs]
    assert tc[0] == 0.0 and tc[2] == pytest.approx(2.0 * tc[1], rel=1e-9)


# ------------------------------------------------------------------ S3: fly neutrality
@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s3_fly_is_dv01_neutral_and_belly_weighted_at_entry():
    r = run("s3_fly_2s5s10s", [*eod("2024-01-02", "2024-02-15"), NO_LAYERS])
    healthy(r)
    entries = pd.DatetimeIndex(r.positions["entry_ts"]).unique()
    assert float(r.equity.loc[entries, "measure_dv01"].abs().max()) < 1e-6
    first = r.positions[r.positions["entry_ts"] == entries[0]].set_index("template")
    assert set(first.index) == {"pay5y", "recv2y", "recv10y"} and (first["entry_quantity"] > 0).all()


# ------------------------------------------------------------------ S4: mean reversion reproduced from raw par rates
@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s4_entries_reproduce_a_pandas_zscore_of_par_rates_without_lookahead():
    n, zb = 10, 1.0
    sets = [*eod("2024-01-02", "2024-06-28"), "backtest.attribution.layers=[]", f"strategy.triggers.0.rolling_mean_window={n}",
            f"strategy.triggers.0.rolling_std_window={n}", f"strategy.triggers.0.z_score_bound={zb}"]
    r = run("s4_meanrev_2s10s", sets)
    healthy(r)
    pr = par_rates(list(r.equity.index))
    x = (pr["10Y"] - pr["2Y"]).to_numpy()
    prior_mean = pd.Series(x).shift(1).rolling(n).mean().to_numpy()
    prior_std = pd.Series(x).shift(1).rolling(n).std(ddof=1).to_numpy()
    z = (x - prior_mean) / prior_std
    p = r.positions[r.positions["action"] == "mr_pay10y"]
    assert len(p) >= 2
    for t, q in zip(p["entry_ts"], p["entry_quantity"]):
        k = r.equity.index.get_loc(t)
        assert abs(z[k]) > zb, (t, z[k])
        assert np.sign(q) == -np.sign(z[k]), "high 2s10s z -> flattener (steepener package scaled by -1)"
    legs = r.positions.groupby("entry_ts")["template"].apply(set)
    assert (legs == {"pay10y", "recv2y"}).all()


# ------------------------------------------------------------------ S8: minute grid machinery
@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s08_minute_trade_on_the_dst_day_opens_0930_edt_and_has_no_intraday_carry():
    sets = ["backtest.grid.start=2026-03-09", "backtest.grid.end=2026-03-09", "market.mdps.sofr_min.kwargs.window=[2026-03-09, 2026-03-09]",
            "strategy.triggers.0.dates=[2026-03-09 09:30:00]", "strategy.triggers.2.dates=[2026-03-09 16:00:00]"]
    r = run("s08_swap_intraday_min", sets)
    healthy(r)
    (p,) = r.positions.to_dict("records")
    assert pd.Timestamp(p["entry_ts"]).tz_convert("UTC") == pd.Timestamp("2026-03-09 13:30", tz="UTC"), "09:30 EDT on the first session after the change"
    assert p["exit_reason"] in ("stop_loss", "time_exit_1600")
    eq = r.equity
    assert (eq["layer_carry"] == 0.0).all() and (eq["layer_roll"] == 0.0).all()
    held = eq.loc[eq["n_positions"] > 0, "positions_value"]
    assert held.nunique() > 0.5 * len(held), "real minute curves move the mark"
    if p["exit_reason"] == "stop_loss":
        k = eq.index.get_loc(p["exit_ts"])
        assert eq["equity"].iloc[k - 1] < -15000.0 <= eq["equity"].iloc[k - 2], "the stop reads the previous minute's recorded P&L"


@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
def test_s08b_minute_hold_carry_and_roll_lump_only_at_the_reference_date_flip():
    sets = ["backtest.grid.start=2026-03-09", "backtest.grid.end=2026-03-10", "market.mdps.sofr_min.kwargs.window=[2026-03-09, 2026-03-10]"]
    r = run("s08b_swap_minute_hold_week", sets)
    healthy(r)
    eq = r.equity
    days = pd.DatetimeIndex(eq.index).tz_convert("America/New_York").normalize()
    flip = int(np.flatnonzero(days != days[0])[0])  # first minute of 03-10: the reference date moves from 03-09 to 03-10
    for k in ("carry", "roll"):
        step = eq[f"layer_{k}"].diff().fillna(eq[f"layer_{k}"]).to_numpy()
        assert np.abs(np.delete(step, flip)).max() <= 1e-6, k
    lump = abs(eq["layer_carry"].iloc[flip] - eq["layer_carry"].iloc[flip - 1]) + abs(eq["layer_roll"].iloc[flip] - eq["layer_roll"].iloc[flip - 1])
    assert lump > 1.0, "one day of carry + roll-down arrives on the first interval after the flip"
    att = r.attribution("layer")["pnl"]
    assert abs(att["unexplained"]) <= 1e-3 * abs(att["delta"])


# ------------------------------------------------------------------ the recorded results (spec 9.5, SC6): the first rows of the migrated runs equal the recorded ones
def recorded_prefix(name: str, n_rows: int):
    from pricebt.results import BacktestResult

    return BacktestResult.from_parquet(RECORDED / name).equity.iloc[:n_rows]


@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
@needs_recorded
@pytest.mark.parametrize("name,end", [("s11_buy_hold_10y", "2022-03-31"), ("s1_swap_carry_eod", "2022-04-29"), ("s3_fly_2s5s10s", "2022-03-31")])
def test_short_windows_reproduce_the_recorded_pre_refactor_results(name, end):
    """Same trades, same gross equity, cash, marks and layers as the recorded run (tools/run_suite_swaps.py before the refactor) over its first rows: tolerance 1e-9 relative to
    max|equity| (the measured difference is exactly 0.0: the migrated stack marks the same curve rows with the same rateslib calls). The ONE difference is the definition of a STARTED
    swap's dv01: the recorded figure is the analytic fixed-leg PV01 (it keeps the accrued coupon), the adapter's is the sum of its delta ladder (the parallel par-rate dv01, which agrees
    with a +-1bp par bump to 3e-7). It moves `measure_dv01` and the cost of every EXIT (a cost scaled by |dv01|), never an entry; see tasks/suite_reproduction.md."""
    if not (RECORDED / name / "equity.parquet").is_file():
        pytest.skip(f"no recorded result for {name}")
    r = run(name, eod("2022-01-03", end))
    healthy(r)
    old = recorded_prefix(name, len(r.equity))
    assert list(r.equity.index) == list(old.index), "the same grid"
    scale = float(old["equity"].abs().max())
    for c in ("cash", "positions_value", "layer_carry", "layer_roll", "layer_delta", "layer_convexity", "layer_unexplained"):
        assert float((r.equity[c] - old[c]).abs().max()) <= 1e-9 * scale, c
    assert float(((r.equity["equity"] - r.equity["tcost"]) - (old["equity"] - old["tcost"])).abs().max()) <= 1e-9 * scale, "gross equity"
    assert (r.equity["n_positions"] == old["n_positions"]).all()
    old_trades = pd.read_parquet(RECORDED / name / "trades.parquet")
    assert len(r.trades) == int((pd.to_datetime(old_trades["ts"]) <= r.equity.index[-1]).sum()), "the same trades"
    assert float((r.equity["tcost"] - old["tcost"]).abs().max()) <= 0.05 * float(old["tcost"].abs().max()) + 1e-9, "only the exit costs move, and by the dv01 definition"
    assert float((r.equity["measure_dv01"] - old["measure_dv01"]).abs().iloc[:3].max()) <= 1e-6 * float(old["measure_dv01"].abs().max()), "an unstarted swap: identical dv01"


def _max_rel(a: pd.Series, b: pd.Series, scale: float) -> float:
    return float((a - b).abs().max()) / scale


@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@pytest.mark.adapter_quantlib
@needs_fixtures
def test_the_swap_suite_gives_the_same_ledger_and_pnl_under_every_stack():
    """S1 on a window with one roll, under rateslib, QuantLib and the dependency-free reference stack (only the factory and the wrap differ): the same trades, and equity
    and layers that agree far inside the tie-out placeholders. Measured on this window: equity within 6e-6 currency (1.7e-11 of max|equity|) for both other stacks; layers
    within 2e-8 (carry, roll), and 1e-5 for the delta / convexity / unexplained of QuantLib (a bump-and-revalue delta: 0.12 currency on 3.7e5)."""
    w = eod("2024-01-02", "2024-02-15")
    res = {s: run("s1_swap_carry_eod", w, stack=s) for s in STACKS}
    ref = res["rateslib"]
    for r in res.values():
        healthy(r)
    scale = float(ref.equity["equity"].abs().max())
    key = ["ts", "kind", "action", "template", "reason"]
    for s in ("quantlib", "refstack"):
        r = res[s]
        assert r.trades[key].astype(str).to_numpy().tolist() == ref.trades[key].astype(str).to_numpy().tolist(), s
        for c in ("equity", "cash", "tcost", "positions_value"):
            assert _max_rel(r.equity[c], ref.equity[c], scale) <= 1e-8, (s, c)
        for c in ("layer_carry", "layer_roll", "layer_delta", "layer_convexity", "layer_unexplained"):
            assert _max_rel(r.equity[c], ref.equity[c], scale) <= 1e-5, (s, c)
    # non-vacuity: the same comparison sees a genuinely different run (the payer mirrors the receiver, so its equity is the negative of it)
    pay = run("s1m_swap_pay_mirror", w, stack="rateslib")
    assert _max_rel(pay.equity["equity"], ref.equity["equity"], scale) > 0.5


# ------------------------------------------------------------------ bisecting the one known difference: the dv01 of a started swap
@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@needs_fixtures
@needs_recorded
def test_with_the_recorded_dv01_definition_s2_reproduces_the_recorded_run_exactly_and_without_it_the_rebalances_differ():
    """s2 re-neutralises when the book's net dv01 leaves a band, so the definition of a STARTED swap's dv01 decides WHEN it trades. Bound back to the recorded definition (the
    analytic fixed-leg PV01: `configs/adapters/rateslib_swap_recorded_dv01.yaml`) the migrated run equals the recorded one in every equity column, the layers and the ledger
    (measured: exactly 0.0 over the whole recorded window, tasks/suite_reproduction.md); with the adapter's dv01 (the parallel par-rate delta, the sum of the ladder) it rebalances
    on different days. Non-vacuity: the two definitions are compared on the same window."""
    from pricebt import api
    from pricebt.results import BacktestResult

    name = "s2_steepener_2s10s"
    if not (RECORDED / name / "equity.parquet").is_file():
        pytest.skip(f"no recorded result for {name}")
    sets = [*eod("2022-01-03", "2022-03-31"), "outputs.dir=null", "backtest.progress.show=false"]
    require_lib("rateslib")
    rec = api.build(CFG / f"{name}.yaml", sets=[*sets, "registry.allow=[support, tools.swap_suite_support]"], stack=[ROOT / "configs" / "adapters" / "rateslib_swap_recorded_dv01.yaml"]).run()
    healthy(rec)
    old = BacktestResult.from_parquet(RECORDED / name)
    oe = old.equity.iloc[: len(rec.equity)]
    scale = float(oe["equity"].abs().max())
    assert list(rec.equity.index) == list(oe.index)
    for c in oe.columns:
        assert float((rec.equity[c] - oe[c]).abs().max()) <= 1e-9 * max(scale, 1.0), c
    old_ledger = old.trades[pd.to_datetime(old.trades["ts"]) <= rec.equity.index[-1]]
    key = ["ts", "position", "kind", "action", "template"]
    assert rec.trades[key].astype(str).to_numpy().tolist() == old_ledger[key].astype(str).to_numpy().tolist()
    assert (rec.trades["kind"] == "open").sum() >= 8 and (rec.positions["kind"] == "rebalance").sum() >= 2, "the window exercises the rebalance trigger"
    now = run(name, eod("2022-01-03", "2022-03-31"))
    assert sorted(set(now.positions.loc[now.positions["kind"] == "rebalance", "entry_ts"])) != sorted(set(rec.positions.loc[rec.positions["kind"] == "rebalance", "entry_ts"]))
    assert float((now.equity["measure_dv01"] - rec.equity["measure_dv01"]).abs().max()) > 10.0, "the definitions differ on a started swap"


# ------------------------------------------------------------------ the synthetic config (no fixtures): a run behind the "migrated" claim
def run_synthetic(stack: str):
    from pricebt import api
    from suite_common import overlays

    return api.build(ROOT / "configs" / "synthetic_swap_carry.yaml", sets=["backtest.progress.show=false", "outputs.dir=null"], stack=overlays(stack, "swap")).run()


@pytest.mark.adapter_rateslib
def test_the_synthetic_swap_config_runs_and_the_reference_stack_agrees_with_rateslib():
    """`configs/synthetic_swap_carry.yaml` (the library-free synthetic market, one instrument spec, a monthly rolled receiver): a real run under rateslib and under the reference stack."""
    pytest.importorskip("rateslib")
    a, b = run_synthetic("rateslib"), run_synthetic("refstack")
    healthy(a)
    healthy(b)
    assert len(a.positions) >= 5 and a.trades[["ts", "kind", "action"]].astype(str).equals(b.trades[["ts", "kind", "action"]].astype(str))
    scale = float(a.equity["equity"].abs().max())
    assert scale > 1e4 and float((a.equity["equity"] - b.equity["equity"]).abs().max()) <= 1e-8 * scale


@pytest.mark.adapter_quantlib
def test_the_synthetic_swap_config_runs_under_quantlib():
    pytest.importorskip("QuantLib")
    healthy(run_synthetic("quantlib"))
