"""Every archetype of skills/pricebt-strategy-recipes/scripts/recipes.py, built from a spec dict on
the toy assets and run through GenericEngine, with a behaviour check specific to the archetype plus
the invariants every run must satisfy (Total identity, costs booked, determinism)."""
from __future__ import annotations

import copy
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pricebt-strategy-recipes" / "scripts"))
import recipes  # noqa: E402

from pricebt.risk import IRDelta, Price  # noqa: E402

DV01 = IRDelta(aggregation_level="Type")
PAYER_10Y = {"class": "IRSwap", "kwargs": {"pay_or_receive": "Pay", "termination_date": "10y",
                                           "notional_currency": "USD", "notional_amount": 10_000_000, "fixed_rate": "ATM"}}
HEDGE_5Y = {"class": "IRSwap", "kwargs": {"pay_or_receive": "Receive", "termination_date": "5y",
                                          "notional_currency": "USD", "notional_amount": 10_000_000, "fixed_rate": "ATM"}}


def _deep_update(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict) and k != "instruments":
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def spec(archetype: str, **over) -> dict:
    s = {
        "name": f"toy-{archetype.replace('_', '-')}",
        "assets": ["tests/assets/toy_usd_irs.yaml"],
        "instruments": {"primary": copy.deepcopy(PAYER_10Y)},
        "dates": {"start": date(2024, 1, 2), "end": date(2024, 4, 2), "frequency": "1b"},
        "archetype": archetype,
        "signal": {"type": "none"},
        "rebalance": {"frequency": "1m", "trade_duration": "next schedule"},
        "sizing": {"method": "notional", "notional": None},
        "costs": {"model": "constant", "level": 10.0},
        "risks_to_report": ["Price", "IRDeltaParallel"],
    }
    return _deep_update(s, over)


def check_invariants(bt, cost_level_positive=True):
    rs = bt.result_summary
    expected = rs[bt.price_measure] + rs["Cumulative Cash"] + rs["Transaction Costs"]
    assert (rs["Total"] - expected).abs().max() < 1e-6
    if cost_level_positive:
        assert rs["Transaction Costs"].iloc[-1] < 0
        assert (rs["Transaction Costs"] <= 1e-12).all()
    return rs


def ledger_names(bt, prefix):
    return [n for n in bt.trade_ledger().index if n.startswith(prefix)]


# --------------------------------------------------------------------------------- periodic_roll


def test_periodic_roll_rolls_monthly_and_is_deterministic():
    s = spec("periodic_roll", dates={"end": date(2024, 6, 3)}, costs={"model": "notional_bp", "level": 0.5})
    bt, built = recipes.run(s)
    check_invariants(bt)
    ledger = bt.trade_ledger().sort_values("Open")
    assert all(re.fullmatch(r"Roll_primary_\d{4}-\d{2}-\d{2}", n) for n in ledger.index)
    opens = list(ledger["Open"])
    assert len(opens) == 6 and len({(d.year, d.month) for d in opens}) == 6  # one entry per month
    # 'next schedule': each roll closes on the next roll's entry date
    assert list(ledger["Close"])[:-1] == opens[1:]
    # notional_bp cost: 0.5bp x 10mm = 500 per side, entries and exits
    assert bt.result_summary["Transaction Costs"].iloc[-1] == pytest.approx(-500.0 * (6 + 5))

    bt2, _ = recipes.run(s)
    pd.testing.assert_frame_equal(bt.result_summary, bt2.result_summary)
    pd.testing.assert_frame_equal(bt.trade_ledger(), bt2.trade_ledger())
    assert "PeriodicTrigger" in recipes.describe(built)


def test_cash_accrual_and_initial_value_reach_the_strategy():
    s = spec("periodic_roll", dates={"end": date(2024, 2, 1)}, initial_value=1_000_000,
             financing={"cash_accrual_rate": 0.05})
    bt, built = recipes.run(s)
    check_invariants(bt)
    assert built.strategy.cash_accrual.rate == 0.05
    assert built.run_kwargs["initial_value"] == 1_000_000
    bt0, _ = recipes.run(spec("periodic_roll", dates={"end": date(2024, 2, 1)}, initial_value=1_000_000))
    gain = bt.result_summary["Cumulative Cash"].iloc[-1] - bt0.result_summary["Cumulative Cash"].iloc[-1]
    assert gain == pytest.approx(1_000_000 * ((1 + 0.05 / 365) ** 30 - 1), rel=1e-2)


# --------------------------------------------------------------------------------- mean_reversion


MR_SPEC = spec("mean_reversion", dates={"end": date(2025, 1, 2)},  # IRFwdRate: resolved through risk_measures (-> par_rate)
                signal={"type": "par_rate_zscore", "instrument": "primary", "measure": "IRFwdRate", "lookback": 30,
                        "params": {"z_entry": 2.0}},
                costs={"model": "dv01_bp", "level": 0.25})


def test_mean_reversion_trades_on_the_toy_year_and_never_closes():
    bt, built = recipes.run(MR_SPEC)
    check_invariants(bt)
    ledger = bt.trade_ledger()
    assert len(ledger) >= 2
    assert all(n.startswith("MeanRev_primary_") for n in ledger.index)
    assert ledger["Close"].isna().all()  # gs semantics: exits are offsetting trades held forever
    assert built.signal is not None and built.signal.attrs["unit"] == "bp"
    # dv01_bp: 0.25 x |dv01| of a 10mm 10y (~8k/bp) is ~2k per trade; each trade is charged once (never exited)
    tc = -bt.result_summary["Transaction Costs"].iloc[-1]
    assert 1_000 * len(ledger) < tc < 4_000 * len(ledger)


def test_signal_lag_shifts_the_series_and_moves_the_mean_reversion_trades():
    lagged = copy.deepcopy(MR_SPEC)
    lagged["signal"]["lag"] = 1
    bt0, built0 = recipes.run(MR_SPEC)
    bt1, built1 = recipes.run(lagged)
    check_invariants(bt1)
    assert MR_SPEC["signal"].get("lag") is None  # run() did not mutate the caller's dict
    pd.testing.assert_series_equal(built1.signal, built0.signal.shift(1).dropna(), check_names=False)
    assert list(bt1.trade_ledger()["Open"]) != list(bt0.trade_ledger()["Open"])
    assert any("lag" in n for n in built1.notes)


# --------------------------------------------------------------------------------- momentum


def test_momentum_uses_next_schedule_through_the_aggregate_and_no_lookahead():
    s = spec("momentum", dates={"end": date(2024, 7, 1)},
             signal={"type": "rate_momentum", "instrument": "primary", "measure": "par_rate", "lookback": 20,
                     "params": {"threshold_bp": 1.0}})
    bt, built = recipes.run(s)
    check_invariants(bt)
    ledger = bt.trade_ledger().sort_values("Open")
    ups, downs = ledger_names(bt, "MomUp_primary_"), ledger_names(bt, "MomDown_primary_opp_")
    assert ups and downs and len(ups) + len(downs) == len(ledger)
    # 'next schedule' reached the action through the ALL_OF aggregate: each closed trade exits on the next roll date
    closed = ledger[ledger["Status"] == "closed"]
    assert len(closed) >= 3
    for _, row in closed.iterrows():
        assert row["Close"] in set(ledger["Open"]) and row["Close"] > row["Open"]
    # direction from the change over the lookback, computed from data up to the trade date only
    raw = recipes.signal_series(s)
    for name, row in ledger.iterrows():
        hist = raw.loc[:row["Open"]]
        change = hist.iloc[-1] - hist.iloc[-21]
        assert built.signal[row["Open"]] == pytest.approx(change)
        assert (change > 1.0) if name.startswith("MomUp") else (change < -1.0)
    assert any("next_schedule" in n for n in built.notes)


# --------------------------------------------------------------------------------- curve_trade


def test_curve_trade_legs_are_dv01_neutral_with_each_leg_at_target():
    target = 5_000.0
    s = spec("curve_trade",
             instruments={"primary": {"class": "IRSwap", "kwargs": {"pay_or_receive": "Receive", "termination_date": "2y",
                                                                   "notional_currency": "USD", "notional_amount": 10_000_000}},
                          "second": copy.deepcopy(PAYER_10Y)},
             sizing={"method": "dv01_target", "dv01_target": target})
    bt, _ = recipes.run(s)
    check_invariants(bt)
    ledger = bt.trade_ledger()
    entry_dates = sorted(set(ledger["Open"]))
    assert len(entry_dates) == 4  # 2024-01-02, 02-02, 03-04 and the end date 04-02
    for d in entry_dates:
        legs = {t.name: t for t in bt.portfolio_dict[d] if t.name.endswith(str(d))}
        assert len(legs) == 2
        dv01s = {}
        for name, t in legs.items():
            assert t.quantity_ > 0  # the signed scaling_level kept each leg's direction
            dv01s[name] = float(bt.results[d][DV01][name])
        front = next(v for k, v in dv01s.items() if k.startswith("Leg1_primary"))
        back = next(v for k, v in dv01s.items() if k.startswith("Leg2_second"))
        assert front == pytest.approx(-target, rel=1e-6)  # receiver: dv01 < 0
        assert back == pytest.approx(target, rel=1e-6)
        assert float(bt.results[d][DV01].aggregate()) == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------------------------- delta_hedged


def test_delta_hedged_book_dv01_is_zero_every_day():
    s = spec("delta_hedged", instruments={"primary": copy.deepcopy(PAYER_10Y), "hedge": copy.deepcopy(HEDGE_5Y)},
             dates={"end": date(2024, 1, 10)}, rebalance={"frequency": "1b"})
    bt, built = recipes.run(s)
    rs = check_invariants(bt)
    assert rs[DV01].abs().max() < 1e-2
    assert ledger_names(bt, "Enter_primary_")  # entered by the DateTrigger, so it pays costs too
    assert len(ledger_names(bt, "Scaled_")) == len(rs)  # one hedge per business day
    assert any("pre-hedge" in n for n in built.notes)


def test_delta_hedged_with_result_ccy_converts_and_still_hedges():
    eur = lambda k: {**k, "kwargs": {**k["kwargs"], "notional_currency": "EUR"}}  # noqa: E731
    s = spec("delta_hedged", assets=["tests/assets/toy_eur_irs.yaml"], fx="tests/assets/toy_fx.yaml", result_ccy="USD",
             instruments={"primary": eur(PAYER_10Y), "hedge": eur(HEDGE_5Y)},
             dates={"end": date(2024, 1, 5)}, rebalance={"frequency": "1b"}, risks_to_report=["Price"])
    bt, _ = recipes.run(s)
    rs = check_invariants(bt)
    dv01_usd = DV01(currency="USD")
    assert dv01_usd in rs.columns and rs[dv01_usd].abs().max() < 1e-2


# --------------------------------------------------------------------------------- risk_band


def test_risk_band_keeps_book_dv01_inside_the_band():
    band = 12_000.0
    s = spec("risk_band", instruments={"primary": copy.deepcopy(PAYER_10Y), "hedge": copy.deepcopy(HEDGE_5Y)},
             rebalance={"frequency": "1m", "trade_duration": None}, risk_limits={"max_abs_dv01": band})
    bt, _ = recipes.run(s)
    rs = check_invariants(bt)
    assert rs[DV01].abs().max() <= band
    assert ledger_names(bt, "Scaled_")  # the band was breached (2 x ~8k) and hedged at least once
    assert len(ledger_names(bt, "Add_primary_")) == 4


# --------------------------------------------------------------------------------- event


def test_event_trades_exactly_on_the_event_dates():
    events = [date(2024, 1, 31), date(2024, 3, 20)]
    s = spec("event", event_dates=events, rebalance={"trade_duration": "1w"})
    bt, _ = recipes.run(s)
    check_invariants(bt)
    ledger = bt.trade_ledger()
    assert sorted(ledger["Open"]) == events
    assert all(row["Close"] is not None and (row["Close"] - row["Open"]).days == 7 for _, row in ledger.iterrows())


def test_nav_sizing_spends_the_budget_on_a_swaption():
    s = spec("event", assets=["tests/assets/toy_usd_swaption.yaml"], event_dates=[date(2024, 2, 1)],
             instruments={"primary": {"class": "IRSwaption", "kwargs": {"pay_or_receive": "Pay", "buy_sell": "Buy",
                                                                        "expiration_date": "1y", "termination_date": "10y",
                                                                        "notional_currency": "USD", "strike": "ATM",
                                                                        "notional_amount": 1_000_000}}},
             rebalance={"trade_duration": None}, sizing={"method": "nav", "nav": 250_000.0},
             costs={"model": "none"}, risks_to_report=["Price"])
    bt, _ = recipes.run(s)
    check_invariants(bt, cost_level_positive=False)
    assert bt.trade_ledger().iloc[0]["Open Value"] == pytest.approx(-250_000.0)


# --------------------------------------------------------------------------------- stop-loss overlay


def test_stop_loss_overlay_exits_all_positions():
    stop = 1_000_000.0
    s = spec("periodic_roll", dates={"start": date(2024, 5, 1), "end": date(2024, 9, 3)},
             rebalance={"trade_duration": None}, risk_limits={"stop_loss_mtm": stop})
    bt, built = recipes.run(s)
    rs = check_invariants(bt)
    ledger = bt.trade_ledger()
    closed = ledger[ledger["Status"] == "closed"]
    assert len(closed) >= 1  # trade_duration None: only the stop can close anything
    stop_day = min(closed["Close"])
    assert rs.loc[:stop_day, Price].iloc[:-1].min() >= -stop  # no earlier breach went unnoticed
    assert rs.loc[stop_day, Price] == pytest.approx(0.0, abs=1e-6)  # flat after the exit (DEV-R1)
    assert any("APPROXIMATION" in n for n in built.notes)


# --------------------------------------------------------------------------------- errors


def test_invalid_and_custom_specs_are_refused():
    with pytest.raises(ValueError, match="archetype"):
        recipes.build(spec("carry"))
    with pytest.raises(NotImplementedError, match="archetype-catalogue"):
        recipes.build(spec("custom"))


# --------------------------------------------------------------------------------- swaptions and bonds

EXAMPLES = ROOT / "skills" / "pricebt-strategy-recipes" / "example"
_OPT = {"expiration_date": "1y", "termination_date": "10y", "notional_currency": "USD", "notional_amount": 1e6, "strike": "ATM"}
_BOND = {"identifier": "TOY 4.25 2034-11-15", "size": 1e6, "settlement_currency": "USD"}


def _w(v):
    return str(getattr(v, "value", v))


@pytest.mark.parametrize("cls, kwargs, sign, opposite", [
    ("IRSwap", {"pay_or_receive": "Pay"}, 1, {"pay_or_receive": "Rec"}),
    ("IRSwap", {"pay_or_receive": "Receive"}, -1, {"pay_or_receive": "Pay"}),
    ("IRSwaption", {"pay_or_receive": "Pay", "buy_sell": "Buy", **_OPT}, 1, {"pay_or_receive": "Pay", "buy_sell": "Sell"}),
    ("IRSwaption", {"pay_or_receive": "Pay", "buy_sell": "Sell", **_OPT}, -1, {"pay_or_receive": "Pay", "buy_sell": "Buy"}),
    ("IRSwaption", {"pay_or_receive": "Receive", "buy_sell": "Buy", **_OPT}, -1, {"pay_or_receive": "Rec", "buy_sell": "Sell"}),
    ("IRSwaption", {"pay_or_receive": "Receive", "buy_sell": "Sell", **_OPT}, 1, {"pay_or_receive": "Rec", "buy_sell": "Buy"}),
    ("Bond", {"buy_sell": "Buy", **_BOND}, -1, {"buy_sell": "Sell"}),
    ("Bond", {"buy_sell": "Sell", **_BOND}, 1, {"buy_sell": "Buy"}),
])
def test_direction_follows_the_class_position_kwarg(cls, kwargs, sign, opposite):
    """R13 finding 9: a swaption's position is buy_sell (pay_or_receive is the option type), a bond's is
    buy_sell; direction_sign is the sign of the unit IRDelta under the contract (long bond < 0)."""
    inst = recipes.make_instrument("primary", {"class": cls, "kwargs": kwargs})
    assert recipes.direction_sign(inst) == sign
    opp = recipes.flipped(inst, "primary_opp")
    assert {k: _w(opp.kwargs[k]) for k in opposite} == opposite and opp.quantity_ == inst.quantity_
    assert recipes.direction_sign(opp) == -sign


def test_a_straddle_has_no_delta_sign_and_flips_buy_sell():
    straddle = recipes.make_instrument("p", {"class": "IRSwaption", "kwargs": {"pay_or_receive": "Straddle", "buy_sell": "Sell", **_OPT}})
    with pytest.raises(ValueError, match="Straddle"):
        recipes.direction_sign(straddle)
    assert _w(recipes.flipped(straddle, "q").kwargs["buy_sell"]) == "Buy"
    bond = recipes.make_instrument("b", {"class": "Bond", "kwargs": {"buy_sell": "Buy", **_BOND}}, notional=5e6)
    assert bond.kwargs["size"] == 5e6  # sizing.notional overrides the class's size kwarg


def test_momentum_on_a_swaption_trades_the_sold_payer_not_a_bought_receiver():
    """U30 acceptance: the _opp leg has the opposite buy_sell and the SAME pay_or_receive; the signal
    is the config-driven level IRFwdRate (the swaption's forward), in bp."""
    s = spec("momentum", assets=["tests/assets/toy_usd_swaption.yaml"], dates={"end": date(2024, 5, 1)},
             instruments={"primary": {"class": "IRSwaption", "kwargs": {"pay_or_receive": "Pay", "buy_sell": "Buy", **_OPT}}},
             signal={"type": "rate_momentum", "instrument": "primary", "measure": "IRFwdRate", "lookback": 20,
                     "params": {"threshold_bp": 1.0}}, costs={"model": "none"}, risks_to_report=["Price"])
    bt, built = recipes.run(s)
    check_invariants(bt, cost_level_positive=False)
    assert built.signal is not None and len(built.signal) > 20
    up, down = (t.actions[0].priceables[0] for t in built.strategy.triggers)
    assert (_w(up.kwargs["buy_sell"]), _w(up.kwargs["pay_or_receive"])) == ("Buy", "Pay")
    assert (_w(down.kwargs["buy_sell"]), _w(down.kwargs["pay_or_receive"])) == ("Sell", "Pay")
    assert len(bt.trade_ledger()) > 0


def test_swaption_expiry_roll_exits_each_option_on_its_expiration_date():
    bt, built = recipes.run(EXAMPLES / "toy_swaption_expiry_roll.yaml")
    check_invariants(bt)
    ledger = bt.trade_ledger()
    closed = ledger[ledger["Status"] == "closed"]
    assert len(closed) == 5
    for name, row in closed.iterrows():
        trade = next(t for t in bt.portfolio_dict[row["Open"]] if t.name == name)
        assert row["Close"] == trade.expiration_date  # 'expiration_date' read from the config attribute
        assert row["Close Value"] >= 0  # a bought option is never a liability at expiry
    assert (closed["Close Value"] == 0).any()  # some expired out of the money: the premium is the loss
    assert any("expiration_date" in n for n in built.notes) and any("buy_sell is the position" in n for n in built.notes)


def test_short_straddle_delta_hedged_is_delta_flat_short_vega_long_theta():
    from pricebt.risk import IRDeltaParallel, IRVegaParallel, Theta

    bt, built = recipes.run(EXAMPLES / "toy_short_straddle_delta_hedged.yaml")
    rs = check_invariants(bt)
    assert built.strategy.triggers[1].actions[0].risk == IRDeltaParallel
    assert rs[IRDeltaParallel].abs().max() < 1e-2  # hedged on IRDeltaParallel every business day
    assert (rs[IRVegaParallel] < 0).all()  # short vol: the swap hedges carry no vega
    assert (rs[Theta] > 0).all()  # short options collect carry
    assert len(ledger_names(bt, "Scaled_hedge_")) == len(rs)
    assert any("DEV-I12" in n for n in built.notes)


def test_bond_carry_roll_is_long_duration_and_its_coupon_shows_only_in_the_explain_table():
    from pricebt.backtests.backtest_objects import bond_pnl_definition
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.risk import IRDeltaParallel

    built = recipes.build(EXAMPLES / "toy_bond_carry_roll.yaml")
    bt = GenericEngine().run_backtest(built.strategy, **built.run_kwargs, pnl_explain=bond_pnl_definition())
    rs = check_invariants(bt)
    ledger = bt.trade_ledger().sort_values("Open")
    assert list(ledger["Close"])[:-1] == list(ledger["Open"])[1:]  # re-entered on every roll date
    assert (rs[IRDeltaParallel] < 0).all()  # a long bond loses when its yield rises
    table = bt.pnl_explain_table()
    coupon = table.loc[table["cashflow_pnl"] != 0, "cashflow_pnl"]
    assert list(coupon.index) == [date(2024, 5, 15)] and coupon.iloc[0] == pytest.approx(10e6 * 0.0425 / 2)
    step = table.loc[date(2024, 5, 15)]
    assert step["actual_pnl"] < 0 < step["economic_pnl"]  # Total drops by the coupon the engine never books
    assert abs(step["residual_pnl"]) < 1e-3 * coupon.iloc[0]
    assert any("NOT booked" in n for n in built.notes)


def test_bond_against_a_payer_swap_keeps_both_directions_and_nets_to_zero_dv01():
    bt, built = recipes.run(EXAMPLES / "toy_bond_asset_swap.yaml")
    check_invariants(bt)
    entry_dates = sorted(set(bt.trade_ledger()["Open"]))
    assert len(entry_dates) == 4
    for d in entry_dates:
        legs = {t.name: t for t in bt.portfolio_dict[d] if t.name.endswith(str(d))}
        bond, swap = (next(t for n, t in legs.items() if n.startswith(p)) for p in ("Leg1_primary", "Leg2_second"))
        assert bond.quantity_ > 0 and swap.quantity_ > 0  # Buy stays a long bond, Pay stays a payer
        assert float(bt.results[d][DV01][bond.name]) == pytest.approx(-5000.0, rel=1e-6)
        assert float(bt.results[d][DV01][swap.name]) == pytest.approx(5000.0, rel=1e-6)
        assert float(bt.results[d][DV01].aggregate()) == pytest.approx(0.0, abs=1e-6)
    assert any("the spread is the trade" in n for n in built.notes)


# --------------------------------------------------------------------------------- determinism, every archetype

_TWO_LEGS = {"primary": {"class": "IRSwap", "kwargs": {"pay_or_receive": "Receive", "termination_date": "2y",
                                                      "notional_currency": "USD", "notional_amount": 10_000_000}},
             "second": PAYER_10Y}
_HEDGED = {"primary": PAYER_10Y, "hedge": HEDGE_5Y}
_SIGNAL = {"instrument": "primary", "measure": "par_rate", "lookback": 20, "params": {"z_entry": 1.5, "threshold_bp": 1.0}}
DETERMINISM_SPECS = {
    "periodic_roll": spec("periodic_roll"),
    "mean_reversion": MR_SPEC,
    "momentum": spec("momentum", signal={"type": "rate_momentum", **_SIGNAL}),
    "curve_trade": spec("curve_trade", instruments=_TWO_LEGS, sizing={"method": "dv01_target", "dv01_target": 5000.0}),
    "delta_hedged": spec("delta_hedged", instruments=_HEDGED, dates={"end": date(2024, 1, 10)}, rebalance={"frequency": "1b"}),
    "risk_band": spec("risk_band", instruments=_HEDGED, rebalance={"trade_duration": None}, risk_limits={"max_abs_dv01": 12_000.0}),
    "event": spec("event", event_dates=[date(2024, 1, 31), date(2024, 3, 20)], rebalance={"trade_duration": "1w"}),
    "stop_loss": spec("periodic_roll", dates={"start": date(2024, 5, 1), "end": date(2024, 9, 3)},
                      rebalance={"trade_duration": None}, risk_limits={"stop_loss_mtm": 1_000_000.0}),
    **{p.stem: recipes.specmod.load_spec(p) for p in sorted(EXAMPLES.glob("*.yaml"))},  # the swaption and bond recipes
}


@pytest.mark.parametrize("name", DETERMINISM_SPECS)
def test_every_archetype_is_deterministic_and_keeps_the_invariants(name):
    s = DETERMINISM_SPECS[name]
    before = copy.deepcopy(s)
    bt1, _ = recipes.run(s)
    bt2, _ = recipes.run(s)
    assert s == before  # build/run never mutate the caller's spec
    check_invariants(bt1)
    assert len(bt1.trade_ledger()) > 0
    pd.testing.assert_frame_equal(bt1.result_summary, bt2.result_summary)
    pd.testing.assert_frame_equal(bt1.trade_ledger(), bt2.trade_ledger())
