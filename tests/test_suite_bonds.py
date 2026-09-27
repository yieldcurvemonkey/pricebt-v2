"""Fast smoke tests of the US Treasury / cross-product strategy suite on REAL fixtures (configs/suite/s05-s09*, ctrl_s10b, ctrl_s12b).

The suite is library-neutral: ONE bond instrument spec `ust` (the bond token, the side and the financing are TERMS of each action; s07 adds the swap spec `usd_sofr_ois`),
the market data comes from the test-support quote panels (`support.ust_eod:UstEod`, `support.ust_minute:UstMinute`; plain snapshots) and the pricing library is a STACK OVERLAY
(`configs/adapters/<stack>_bond.yaml`, plus `<stack>_swap.yaml` for s07). Each config runs end to end on a small window (EOD: 2018-05-01 .. 2018-07/08; minute: the 2026-03-09
session) and must reconcile strictly; the controls, the long/short mirror and the notional / DV01 scaling invariants are asserted exactly; what the suite needs from the adapter (the
`gc_rate: pricer` repo proxy, the `bond_ytm` / `bond_clean` / `repo_fixing` lookups, a quote on a coupon date, a missing quote raising) is unit-tested on known answers; the first rows
of the recorded pre-refactor results are reproduced. The providers' own rules (bad-tick filter, partial days, on-the-run ranking) are tested in tests/test_support_ust.py.
Full-window runs: tools/run_suite_bonds.py; tasks/suite_reproduction.md compares them with the recorded results.
"""
import datetime as dt
import functools
import importlib.util

import numpy as np
import pandas as pd
import pytest

from suite_common import CFG, FIXTURES, RECORDED, ROOT, STACKS, build_config, needs_fixtures, needs_recorded, ny, require_lib

EOD_CONFIGS = ("s05_ust_ct10_financed.yaml", "s06_ust_2s10s_steepener.yaml", "s07_swap_spread.yaml", "ctrl_s10b_ust_buy_and_hold.yaml", "ctrl_s12b_ust_always_flat.yaml")
ALL_CONFIGS = (*EOD_CONFIGS, "s09_ust_intraday_mr.yaml")
SMALL = ("backtest.grid.end=2018-07-31", "market.mdps.ust.kwargs.window=[2018-04-01, 2018-09-28]")
TERMS0 = "strategy.triggers.0.actions.0.priceables.terms"
COST0 = "strategy.triggers.0.actions.0.transaction_cost.scaling_level"


def real(fn):
    """A test that runs a real-data backtest through the rateslib stack: the partition markers, and a labelled skip without the fixtures (spec G6)."""
    for m in (needs_fixtures, pytest.mark.adapter_rateslib, pytest.mark.fixtures):
        fn = m(fn)
    return fn


def kinds_of(config: str):
    return ("swap", "bond") if config.startswith("s07") else ("bond",)


@functools.lru_cache(maxsize=None)
def run(config: str, *sets: str):
    require_lib("rateslib")
    return build_config(config, ["outputs.dir=null", *sets], kinds=kinds_of(config), stack="rateslib").run()


def healthy(res) -> None:
    assert res.n_errors == 0, res.errors
    res.reconcile(strict=True)
    assert np.isfinite(res.equity.select_dtypes("number").to_numpy()).all()


@functools.lru_cache(maxsize=None)
def tool():
    """tools/run_suite_bonds.py as a module (its independent re-computations and checks are reused here)."""
    import sys

    spec = importlib.util.spec_from_file_location("run_suite_bonds", ROOT / "tools" / "run_suite_bonds.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


def load_yaml(config: str) -> dict:
    from pricebt import api

    return api.load(CFG / config)


def trades_of(node):
    """Every trade mapping (`priceables` / `priceable` of an action, and the `template` of a `measure` signal) of a config tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("priceables", "priceable", "template") and isinstance(v, dict) and "instrument" in v:
                yield v
            else:
                yield from trades_of(v)
    elif isinstance(node, list):
        for v in node:
            yield from trades_of(v)


# ============================================================================ the migrated configs (no fixtures, no library)
@pytest.mark.core
@pytest.mark.parametrize("config", ALL_CONFIGS)
def test_a_bond_suite_config_is_one_instrument_spec_per_asset_class_plus_per_action_terms(config):
    cfg = load_yaml(config)
    want = {"ust", "usd_sofr_ois"} if config.startswith("s07") else {"ust"}
    from pricebt.config.loader import TOP_KEYS

    assert set(cfg["instruments"]) == want, "no per-bond or per-direction instruments (spec T1, SC6)"
    assert set(cfg) <= TOP_KEYS and "trades" not in cfg, "no key of the old config grammar, no pre-baked trade: the trades are the terms of the actions"
    assert cfg["instruments"]["ust"]["asset_class"] == "bond" and "bind" not in cfg["instruments"]["ust"]
    seen = list(trades_of({k: cfg[k] for k in ("strategy", "signals") if k in cfg}))
    assert seen
    for t in seen:
        if t["instrument"] == "ust":
            assert t["terms"]["side"] in ("buy", "sell") and t["terms"]["security"] in ("CT2", "CT10") and t["terms"]["notional"] == 1e7, t
        else:
            assert t["instrument"] == "usd_sofr_ois" and t["terms"]["side"] == "pay" and t["terms"]["maturity"] == "10Y"
    assert cfg["outputs"]["dir"].replace("\\", "/").endswith(f"results_new/{config[:-5]}"), "the suite never writes over the recorded results/"


@pytest.mark.core
@pytest.mark.parametrize("config", ALL_CONFIGS)
def test_a_cost_is_a_plain_scaled_cost_and_a_financed_leg_carries_its_repo_in_the_terms(config):
    """The workaround factories of the deleted stores (`scaled_cost`, `bond_ytm_signal`) are gone: `{type: scaled, ...}` is plain YAML and the repo proxy is a per-trade term."""
    cfg = load_yaml(config)
    costs = list(walk(cfg, "transaction_cost"))
    for c in costs:
        assert c["type"] == "scaled" and set(c) == {"type", "scaling_type", "scaling_level"}, c
    assert costs or config.startswith("ctrl_s10b"), "every strategy but the no-cost buy-and-hold control pays a cost"
    for t in trades_of({"strategy": cfg["strategy"]}):
        if t["instrument"] == "ust":
            assert t["terms"]["extras"] == {"repo": {"gc_rate": "pricer"}}, "financed at the last published overnight fixing"


def walk(node, key):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            else:
                yield from walk(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v, key)


@pytest.mark.core
def test_a_plain_scaled_cost_builds_from_yaml_with_the_known_answer():
    from pricebt import registries
    from pricebt.costs import CostContext, ScaledCost

    cfg = load_yaml("s05_ust_ct10_financed.yaml")
    cost = cfg["strategy"]["triggers"][0]["actions"][0]["transaction_cost"]
    assert cost == {"type": "scaled", "scaling_type": "notional", "scaling_level": 1.5625e-4}
    c = registries.COSTS.resolve(cost["type"])(**{k: v for k, v in cost.items() if k != "type"})  # what the loader does with the mapping
    assert c == ScaledCost("notional", 1.5625e-4)
    assert c.cost(CostContext(ny("2024-01-02 17:00"), -2.0, lambda name: {"notional": 1e7}[name])) == pytest.approx(3125.0), "1/64 point per side on 2 x 10mm face"


@pytest.mark.core
def test_the_s09_signal_chain_is_plain_yaml_derived_signals_with_named_inputs():
    """z = clip(zscore(ytm, 30), -10, 10) and ytm_sd = rolling_std(ytm, 30) over the yield of a fresh CT10 (the `rate` measure), no Python factory."""
    sig = load_yaml("s09_ust_intraday_mr.yaml")["signals"]
    assert sig["ytm"]["type"] == "measure" and sig["ytm"]["measure"] == "rate" and sig["ytm"]["template"]["terms"]["security"] == "CT10"
    assert sig["ytm_z"] == {"type": "derived", "op": "zscore", "inputs": ["ytm"], "window": 30}
    assert sig["z"] == {"type": "derived", "op": "clip", "inputs": ["ytm_z"], "lo": -10.0, "hi": 10.0}
    assert sig["ytm_sd"] == {"type": "derived", "op": "rolling_std", "inputs": ["ytm"], "window": 30}


@pytest.mark.core
@pytest.mark.parametrize("config", ALL_CONFIGS)
def test_every_stack_overlay_leaves_the_base_digest_unchanged_and_sets_only_factory_and_wrap(config):
    """Spec X1: the stacks share one base (conventions, terms, strategy, grid, signals, costs); the overlays touch instruments.<n>.factory and market.pricers.<r>.wrap only."""
    from pricebt import api
    from pricebt.config.stacks import apply_stack, base_digest, validate_overlay
    from suite_common import overlays

    cfg = load_yaml(config)
    digests = set()
    for stack in STACKS:
        out = cfg
        for f in overlays(stack, *kinds_of(config)):
            ov = api.load(f)
            validate_overlay(ov)
            assert all(set(v) == {"factory"} for v in ov["instruments"].values()) and all(set(r) == {"wrap"} for r in ov["market"]["pricers"].values()), f
            out = apply_stack(out, ov)
        digests.add(base_digest(out))
        assert out["instruments"]["ust"]["conventions"] == cfg["instruments"]["ust"]["conventions"] and out["instruments"]["ust"]["conventions"]["calendar"] == "us_govt"
    assert digests == {base_digest(cfg)}


@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@pytest.mark.adapter_quantlib
@needs_fixtures
@pytest.mark.parametrize("config", ALL_CONFIGS)
def test_every_suite_config_builds_under_every_stack_with_one_base(config):
    pytest.importorskip("pyarrow")
    sets = ["backtest.progress.show=false"]
    seen = {}
    for stack in STACKS:
        try:
            require_lib(stack)
        except pytest.skip.Exception:
            continue
        b = build_config(config, sets, kinds=kinds_of(config), stack=stack)
        assert len(b.grid) > 0 and set(b.instruments) >= {"ust"} and b.strategy.triggers
        seen[stack] = b.base_hash
    assert seen and len(set(seen.values())) == 1, seen


# ============================================================================ configs end to end
@real
def test_s05_financed_ct10_roll_reconciles_and_matches_an_independent_recomputation():
    res = run("s05_ust_ct10_financed.yaml", *SMALL)
    healthy(res)
    pos = res.positions
    assert list(pos["template"].unique()) == ["ct10"] and (pos["entry_quantity"] == 1.0).all() and len(pos) == 3
    held = res.equity["n_positions"] > 0
    assert (res.equity.loc[held, "measure_dv01"] < -7000).all(), "a long 10mm CT10 has dv01 ~ -8.5k USD/bp"
    assert (res.equity["financing_cum"].diff().iloc[2:] < 0).all(), "the long pays GC repo every step"
    att = res.attribution("layer")["pnl"]
    layers = sum(abs(att[k]) for k in ("carry", "roll", "delta", "convexity"))
    assert abs(att["unexplained"]) < 1e-3 * layers
    ind = tool().independent_s5(res, 3)
    assert ind["max_abs_diff_per_position"] < 0.05 and ind["entry_pv_max_abs_diff"] < 0.05, ind["table"]


@real
def test_s06_steepener_is_long_2y_short_10y_and_dv01_neutral_at_rolls():
    res = run("s06_ust_2s10s_steepener.yaml", *SMALL)
    healthy(res)
    pos = res.positions
    assert (pos.loc[pos["template"] == "ct2", "entry_quantity"] > 0).all() and (pos.loc[pos["template"] == "ct10", "action"].isin(["short_10y", "rehedge_10y"])).all()
    assert (pos.loc[pos["action"] == "short_10y", "entry_quantity"] < 0).all(), "the 10Y leg is SHORT: the book profits when the curve steepens"
    rolls = sorted(set(res.trades.loc[res.trades["action"] == "long_2y", "ts"]))
    assert len(rolls) == 3
    assert res.equity.loc[rolls, "measure_dv01"].abs().max() < 1e-6, "net dv01 is exactly 0 right after each roll (same pricer)"
    assert (res.equity["measure_dv01"].abs() < 2000).all(), "drift between weekly checks stays small next to the 10k legs"
    hedges = res.trades[(res.trades["action"] == "rehedge_10y") & (res.trades["kind"] == "open")]
    assert res.equity.loc[sorted(set(hedges["ts"])), "measure_dv01"].abs().max() < 1e-6 if len(hedges) else True


@real
def test_s07_swap_spread_uses_two_pricer_roles_and_is_dv01_neutral_at_rolls():
    res = run("s07_swap_spread.yaml", *SMALL, "market.mdps.sofr.kwargs.window=[2018-04-01, 2018-09-28]")
    healthy(res)
    pos = res.positions
    assert set(pos["template"]) == {"ct10", "pay10y"}
    assert (pos.loc[pos["template"] == "pay10y", "entry_quantity"] > 0).all(), "a payer swap (dv01 > 0) hedges the long bond (dv01 < 0)"
    rolls = sorted(set(res.trades.loc[res.trades["action"] == "long_ct10", "ts"]))
    assert res.equity.loc[rolls, "measure_dv01"].abs().max() < 1e-6
    assert (pos.loc[pos["template"] == "pay10y", "entry_pv"].abs() < 1.0).all(), "the swap is struck at par on its own (curve) pricer"


@real
def test_s09_intraday_session_is_flat_by_1600_and_finances_on_seconds():
    res = run("s09_ust_intraday_mr.yaml", "backtest.grid.start=2026-03-09", "backtest.grid.end=2026-03-09")
    healthy(res)
    ck = tool().s09_checks(res)
    assert ck["grid_weekend_points"] == 0 and ck["grid_time_range"] == ["09:00:00", "16:00:00"] and ck["n_sessions"] == 1
    assert ck["n_positions"] >= 1 and ck["all_flat_by_1600_same_day"] and ck["n_open_at_end"] == 0
    assert ck["financing_seconds_max_rel_err"] < 1e-9, "financing = -MV * SOFR * seconds / (360 * 86400) on every held minute"
    assert ck["intraday_carry_minus_financing_max_abs"] < 1e-8, "accrued interest does not move inside a date (carry = financing only)"
    assert ck["share_minute_steps_mark_moved"] > 0.3, "real minute yields move the mark"


@real
def test_ctrl_s10b_sweeps_the_coupon_once_and_the_mark_is_smooth_through_it():
    res = run("ctrl_s10b_ust_buy_and_hold.yaml", "backtest.grid.end=2018-08-31", "market.mdps.ust.kwargs.window=[2018-04-01, 2018-09-28]")
    healthy(res)
    ck = tool().coupon_check(res)
    assert ck["n_coupon_flows"] == 1 and ck["amounts"] == [137500.0], ck  # 2.75% Feb-2028 note, 10mm face, coupon 2018-08-15
    assert ck["rows"][0]["ts"].startswith("2018-08-16"), "swept on the first point after the payment date"
    assert ck["max_abs_carry_on_coupon_steps"] < 5 * max(ck["median_abs_carry_step"], 1.0), "no coupon-sized jump in the carry layer"
    eq = res.equity
    market = sum(eq[f"layer_{k}"].diff() for k in ("delta", "convexity", "roll"))
    ex_market = (eq["step_pnl"] - market).loc["2018-08-14":"2018-08-17"]
    assert ex_market.abs().max() < 5000.0, ex_market  # ~1 day of accrual net of repo; the pre-fix mark gave +/-137.5k on 08-15/08-16


@real
def test_ctrl_s12b_always_flat_is_exactly_zero():
    res = run("ctrl_s12b_ust_always_flat.yaml", *SMALL)
    eq = res.equity
    assert (eq["equity"] == 0.0).all() and (eq["cash"] == 0.0).all() and (eq["tcost"] == 0.0).all() and (eq["positions_value"] == 0.0).all()
    assert len(res.trades) == 0 and len(res.orders) == 0 and len(res.positions) == 0 and res.n_errors == 0
    res.reconcile(strict=True)


@real
def test_long_short_mirror_sums_to_zero_with_financing_on_real_data():
    a = run("s05_ust_ct10_financed.yaml", *SMALL, f"{COST0}=0.0")
    b = run("s05_ust_ct10_financed.yaml", *SMALL, f"{COST0}=0.0", f"{TERMS0}.side=sell")
    healthy(a)
    healthy(b)
    assert a.equity["financing_cum"].iloc[-1] < 0 < b.equity["financing_cum"].iloc[-1]
    assert (a.equity["equity"] + b.equity["equity"]).abs().max() < 1e-6 * a.equity["equity"].abs().max()
    assert (a.equity["financing_cum"] + b.equity["financing_cum"]).abs().max() < 1e-6


@real
def test_notional_and_dv01_target_doubling_double_the_pnl_exactly():
    base = run("s05_ust_ct10_financed.yaml", *SMALL)
    big = run("s05_ust_ct10_financed.yaml", *SMALL, f"{TERMS0}.notional=2e7")
    assert (big.equity["equity"] - 2 * base.equity["equity"]).abs().max() < 1e-6 * base.equity["equity"].abs().max()
    assert big.equity["tcost"].iloc[-1] == pytest.approx(2 * base.equity["tcost"].iloc[-1], rel=1e-12)
    s6 = run("s06_ust_2s10s_steepener.yaml", *SMALL)
    s6x2 = run("s06_ust_2s10s_steepener.yaml", *SMALL, "strategy.triggers.0.actions.1.scaling_level=-20000", "strategy.triggers.1.band=600")
    assert (s6x2.equity["equity"] - 2 * s6.equity["equity"]).abs().max() < 1e-6 * s6.equity["equity"].abs().max()


@real
def test_a_roll_on_a_zero_price_day_raises_instead_of_marking_zero():
    from pricebt.errors import StaleSnapshot

    sets = ("backtest.grid.start=2026-06-01", "backtest.grid.end=2026-07-31", "market.mdps.ust.kwargs.window=[2026-05-01, 2026-08-21]",
            "strategy.triggers.0.start_date=2026-06-09")
    built = build_config("s05_ust_ct10_financed.yaml", ["outputs.dir=null", *sets], kinds=("bond",))
    assert not any(t.date() == dt.date(2026, 7, 9) for t in built.grid), "the zero-price day is not a grid point"
    with pytest.raises(StaleSnapshot):
        built.run()  # ... but the monthly roll lands on it: the pricer refuses (asof 12h), no zero-price mark
    rec = run("s05_ust_ct10_financed.yaml", *sets, "backtest.on_error=record", "backtest.measures=[]")
    assert rec.n_errors > 0 and set(rec.errors["where"]) >= {"scheduled_exit"}
    assert all("2026-07-09" in str(t) for t in rec.errors["ts"])


# ============================================================================ what the suite needs from the adapter and the providers (known answers)
def rateslib_bond(p, security="CT10", side="buy", notional=1e7, repo=None):
    """The rateslib bond kit's factory on a wrapped snapshot pricer: the object a position holds (`RLBond`)."""
    from pricebt.contrib import rateslib as RL

    terms = {"side": side, "direction": 1 if side == "buy" else -1, "security": security, "notional": notional}
    if repo is not None:
        terms["extras"] = {"repo": repo}
    return RL.bond.factory(p, p.ts, terms=terms, conventions={**RL.UST_CONVENTIONS, "calendar": "us_govt"}).obj


@pytest.fixture(scope="module")
def fi_small():
    pytest.importorskip("rateslib")
    from support.ust_eod import UstEod

    return UstEod(window=("2018-05-01", "2018-06-30"), fixings="auto")


@real
def test_gc_rate_pricer_reads_the_last_published_sofr_fixing(fi_small):
    from pricebt.contrib.rateslib import RLBond, wrap
    from pricebt.errors import ConfigError
    from pricebt.pricable import MarkContext
    from support.common import load_fixings

    p0, p1 = wrap(fi_small.get_pricer(ny("2018-06-04 17:00"))), wrap(fi_small.get_pricer(ny("2018-06-05 17:00")))
    fx = load_fixings(FIXTURES / "fixings" / "USD-SOFR-1D.parquet")
    want = float(fx[fx.index < pd.Timestamp("2018-06-04")].iloc[-1])
    assert p0.lookup("repo_fixing") == want and fx.index[fx.index < pd.Timestamp("2018-06-04")][-1] == pd.Timestamp("2018-06-01")
    b = rateslib_bond(p0, repo={"gc_rate": "pricer"})
    assert b.repo_rate(p0) == want
    older = fx[fx.index < pd.Timestamp("2018-06-05")]
    assert b.repo_rate(p1) == float(older.iloc[-1]) == pytest.approx(1.80, abs=1e-12), "06-05 uses the 06-04 fixing (1.80)"
    assert b.repo_rate(p1) != float(older.iloc[-2]), "... not the 06-01 fixing (1.81)"
    v = b.value(MarkContext.standalone(p1, prev=p0))
    assert v.financing == pytest.approx(-1e7 / 100 * b.mark(p0)["dirty"] * want / 100 / 360, rel=1e-12)
    with pytest.raises(ConfigError, match="gc_rate"):
        RLBond(b.ref, 1, 1e7, repo={"gc_rate": "sofr"})


@real
def test_gc_rate_pricer_raises_without_fixings_or_when_they_are_stale(tmp_path, fi_small):
    from pricebt.contrib.rateslib import wrap
    from pricebt.errors import ConfigError, MarketDataUnavailable
    from support.ust_eod import UstEod

    bare = UstEod(window=("2018-06-01", "2018-06-08"))
    p = wrap(bare.get_pricer(ny("2018-06-05 17:00")))
    b = rateslib_bond(p, repo={"gc_rate": "pricer"})
    with pytest.raises(MarketDataUnavailable, match="no published fixings"):
        b.repo_rate(p)
    fx = pd.read_parquet(FIXTURES / "fixings" / "USD-SOFR-1D.parquet")
    fx[pd.to_datetime(fx["date"]) <= "2018-05-15"].to_parquet(tmp_path / "old.parquet")
    stale = UstEod(window=("2018-06-01", "2018-06-08"), fixings=tmp_path / "old.parquet")
    with pytest.raises(MarketDataUnavailable, match="stale"):
        b.repo_rate(wrap(stale.get_pricer(ny("2018-06-05 17:00"))))
    with pytest.raises(ConfigError, match="not both"):
        UstEod(window=("2018-06-01", "2018-06-08"), fixings="auto", curve_mdp=bare)


@real
def test_bond_lookups_agree_with_the_rlbond_mark(fi_small):
    from pricebt.contrib.rateslib import wrap
    from support.ust_minute import UstMinute

    p = wrap(fi_small.get_pricer(ny("2018-06-05 17:00")))
    b = rateslib_bond(p)
    m = b.mark(p)
    assert p.lookup("bond_ytm", bond="CT10") == pytest.approx(m["ytm"], abs=1e-12)
    assert p.lookup("bond_ytm", bond=b.cusip) == pytest.approx(m["ytm"], abs=1e-12)
    assert p.lookup("bond_clean", bond="CT10") == p.bond_quote(b.cusip).clean
    q = wrap(UstMinute().get_pricer(ny("2026-03-09 10:00")))
    assert q.lookup("bond_ytm", bond="CT10") == q.bond_quote("91282CPZ8").ytm


@real
def test_a_position_whose_cusip_has_no_quote_raises_instead_of_marking_zero():
    """The 5 partial 2023 panels are dropped by the provider (tests/test_support_ust.py); on a panel that keeps such a day, a held bond without a quote is an error, never 0."""
    from pricebt.contrib.rateslib import wrap
    from pricebt.errors import MarketDataUnavailable
    from pricebt.pricable import MarkContext
    from support.ust_eod import UstEod

    p = wrap(UstEod(window=("2023-07-25", "2023-08-08")).get_pricer(ny("2023-08-01 17:00")))
    assert p.bond("CT10").cusip == "91282CHC8"
    b = rateslib_bond(p)
    with pytest.raises(MarketDataUnavailable, match="no quote for 91282CHC8"):
        b.value(MarkContext.standalone(p))


@real
def test_the_partial_2023_days_are_not_grid_points_of_the_suite_panel():
    """The suite's `partial_day_min_missing: 10` and the monthly-roll `calendar:` exceptions name the same five days (spec of the panel: tests/test_support_ust.py)."""
    from support.known import FEDINVEST_PARTIAL_DAYS_2023
    from support.ust_eod import UstEod

    cfg = load_yaml("_ust_base_eod.yaml")
    assert cfg["market"]["mdps"]["ust"]["kwargs"]["partial_day_min_missing"] == 10
    days = {t.date() for t in UstEod(window=("2023-01-01", "2023-12-31"), partial_day_min_missing=10).available_timestamps(ny("2023-01-01"), ny("2023-12-31 23:00"))}
    assert not days & {dt.date.fromisoformat(x) for x in FEDINVEST_PARTIAL_DAYS_2023}
    for name in ("s05_ust_ct10_financed.yaml", "s06_ust_2s10s_steepener.yaml", "s07_swap_spread.yaml", "ctrl_s12b_ust_always_flat.yaml"):
        assert [str(x) for x in load_yaml(name)["strategy"]["triggers"][-1 if name.startswith("ctrl_s12b") else 0]["calendar"]] == list(FEDINVEST_PARTIAL_DAYS_2023), name


@real
def test_clean_quote_on_a_coupon_date_marks_with_zero_accrued_and_a_continuous_yield():
    """rateslib 2.7.1 (ex_div=0) prices the dirty value at a coupon-date settlement EXCLUDING that coupon but reports the FULL coupon as
    accrued; RLBond must use accrued 0 there, else ytm(clean) is ~18bp off and V(D) double counts the coupon."""
    from pricebt.contrib.rateslib import wrap
    from pricebt.contrib.rateslib.bond import market_clean
    from support.ust_eod import UstEod

    f = UstEod(window=("2018-08-01", "2018-08-31"))
    ps = {d: wrap(f.get_pricer(ny(f"2018-08-{d} 17:00"))) for d in (14, 15, 16)}
    b = rateslib_bond(ps[14], security="9128283W8")  # T 2 3/4 Feb-28, coupon 2018-08-15
    m = {d: b.mark(p) for d, p in ps.items()}
    assert market_clean(ps[15]), "the quote panel declares market-convention clean prices"
    assert ps[15].bond_quote(b.cusip).clean == 99.0 and m[15]["accrued"] == 0.0 and m[15]["dirty"] == pytest.approx(99.0, abs=1e-9)
    assert abs(m[15]["ytm"] - m[14]["ytm"]) < 0.04 and abs(m[16]["ytm"] - m[15]["ytm"]) < 0.04, {d: x["ytm"] for d, x in m.items()}
    assert b.pv_of(ps[15], m[15]) == pytest.approx(1e7 / 100 * 99.0 + 137500.0, abs=1e-3), "V(D) = clean value + the coupon paid on D, once"
    assert ps[15].lookup("bond_ytm", bond=b.cusip) == pytest.approx(m[15]["ytm"], abs=1e-12)


# ============================================================================ the recorded results (spec 9.5, SC6)
@real
@needs_recorded
@pytest.mark.parametrize("name,end", [("s05_ust_ct10_financed", "2018-08-31"), ("ctrl_s10b_ust_buy_and_hold", "2018-08-31")])
def test_short_windows_reproduce_the_recorded_pre_refactor_results(name, end):
    """Same trades, same equity, financing and layers as the recorded run over its first rows (tolerance 1e-9 relative to max|equity|, see tasks/suite_reproduction.md)."""
    from pricebt.results import BacktestResult

    if not (RECORDED / name / "equity.parquet").is_file():
        pytest.skip(f"no recorded result for {name}")
    r = run(f"{name}.yaml", f"backtest.grid.end={end}", "market.mdps.ust.kwargs.window=[2018-04-01, 2018-10-15]")
    healthy(r)
    old = BacktestResult.from_parquet(RECORDED / name).equity.iloc[: len(r.equity)]
    assert list(r.equity.index) == list(old.index), "the same grid"
    scale = float(old["equity"].abs().max())
    for c in ("equity", "cash", "tcost", "positions_value", "step_pnl", "financing_cum", "flows_cum", "measure_dv01", "layer_carry", "layer_roll", "layer_delta", "layer_convexity", "layer_unexplained"):
        assert float((r.equity[c] - old[c]).abs().max()) <= 1e-9 * scale, c


# ============================================================================ one base config, three stacks (spec X1, SC8)
@pytest.mark.fixtures
@pytest.mark.adapter_rateslib
@pytest.mark.adapter_quantlib
@needs_fixtures
def test_the_bond_suite_gives_the_same_ledger_and_pnl_under_every_stack():
    """S5 (financed CT10 rolled monthly) under rateslib, QuantLib and the dependency-free reference stack: the same trades and the same equity, financing and layers.
    Measured on this window: equity within 7e-6 currency (4e-11 of max|equity|), financing within 2e-8, layers within 4e-5."""
    res = {}
    for stack in STACKS:
        require_lib(stack)
        res[stack] = build_config("s05_ust_ct10_financed.yaml", ["outputs.dir=null", *SMALL], kinds=("bond",), stack=stack).run()
        healthy(res[stack])
    ref = res["rateslib"]
    scale = float(ref.equity["equity"].abs().max())
    key = ["ts", "kind", "action", "template", "reason"]
    for s in ("quantlib", "refstack"):
        r = res[s]
        assert r.trades[key].astype(str).to_numpy().tolist() == ref.trades[key].astype(str).to_numpy().tolist(), s
        for c in ("equity", "cash", "tcost", "positions_value", "financing_cum", "flows_cum"):
            assert float((r.equity[c] - ref.equity[c]).abs().max()) <= 1e-8 * scale, (s, c)
        for c in ("layer_carry", "layer_roll", "layer_delta", "layer_convexity", "layer_unexplained"):
            assert float((r.equity[c] - ref.equity[c]).abs().max()) <= 1e-6 * scale, (s, c)
    short = run("s05_ust_ct10_financed.yaml", *SMALL, f"{TERMS0}.side=sell")  # non-vacuity: a different run (the short mirror) is far outside the tolerance
    assert float((short.equity["equity"] - ref.equity["equity"]).abs().max()) > 0.5 * scale
