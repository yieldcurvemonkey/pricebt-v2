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
import swap_pnl  # noqa: E402

from pricebt.backtests.backtest_objects import PnlDefinition  # noqa: E402
from pricebt.errors import ConfigError  # noqa: E402
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


MR_SPEC = spec("mean_reversion", dates={"end": date(2025, 1, 2)},
                signal={"type": "par_rate_zscore", "instrument": "primary", "measure": "par_rate", "lookback": 30,
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


# --------------------------------------------------------------------------------- pnl_explain (T3-B)
# PNL_EXPLAIN_PLAN.md section 7: recipes.build() wires swap_pnl.swap_pnl_definition() into
# run_kwargs when spec.pnl_explain.enabled and the primary is an IRSwap; recipes.run() then hands
# it straight to GenericEngine.run_backtest(pnl_explain=...) (src/pricebt untouched either way).


def test_pnl_explain_disabled_by_default_has_no_run_kwarg():
    built = recipes.build(spec("periodic_roll"))
    assert "pnl_explain" not in built.run_kwargs  # absent, not explicitly None (recipes.py's own choice)
    assert "P&L explain not enabled" in built.notes


def test_pnl_explain_enabled_builds_a_real_definition_with_gamma_and_carry_toggles():
    full = recipes.build(spec("periodic_roll", pnl_explain={"enabled": True, "gamma": True, "carry": True, "cash": False}))
    pnl_def = full.run_kwargs["pnl_explain"]
    assert isinstance(pnl_def, PnlDefinition)
    assert {a.attribute_name for a in pnl_def.attributes} == {"PNL_delta", "PNL_gamma", "PNL_carry"}
    assert len(pnl_def.attributes) == 3
    # describe() must summarise the PnlDefinition, not dump its full dataclass repr (recipes.py's
    # own _short()/describe() -- see the "risks" column it already gets the same treatment)
    assert "PnlDefinition(PNL_delta, PNL_gamma, PNL_carry)" in recipes.describe(full)

    delta_only = recipes.build(spec("periodic_roll",
                                    pnl_explain={"enabled": True, "gamma": False, "carry": False, "cash": False}))
    pnl_def2 = delta_only.run_kwargs["pnl_explain"]
    assert {a.attribute_name for a in pnl_def2.attributes} == {"PNL_delta"}
    assert len(pnl_def2.attributes) == 1

    gamma_no_carry = recipes.build(spec("periodic_roll",
                                        pnl_explain={"enabled": True, "gamma": True, "carry": False, "cash": False}))
    assert len(gamma_no_carry.run_kwargs["pnl_explain"].attributes) == 2
    assert any("P&L explain: swap_pnl_definition(gamma=True, carry=True)" in n for n in full.notes)


def test_pnl_explain_cash_auto_adds_cash_paid_to_date_when_the_config_maps_it():
    # tests/assets/toy_usd_irs.yaml maps CashPaidToDate (PNL_EXPLAIN_PLAN.md section 3.2).
    built = recipes.build(spec("periodic_roll", pnl_explain={"enabled": True, "gamma": True, "carry": True, "cash": "auto"}))
    assert swap_pnl.CashPaidToDate in built.run_kwargs["risks"]
    assert any("cash column included" in n for n in built.notes)


def test_pnl_explain_cash_auto_skips_and_does_not_crash_when_the_config_does_not_map_it():
    # tests/assets/toy_eur_irs.yaml has no gamma/theta/year_fraction/cash_paid_to_date functions at
    # all (PNL_EXPLAIN_PLAN.md section 3.2: it is the "explain not supported" negative fixture) --
    # gamma/carry are turned off here so build() only exercises the cash lookup this test targets.
    eur_primary = {**PAYER_10Y, "kwargs": {**PAYER_10Y["kwargs"], "notional_currency": "EUR"}}
    s = spec("periodic_roll", assets=["tests/assets/toy_eur_irs.yaml"], instruments={"primary": eur_primary},
             pnl_explain={"enabled": True, "gamma": False, "carry": False, "cash": "auto"})
    built = recipes.build(s)  # must not raise
    assert swap_pnl.CashPaidToDate not in built.run_kwargs["risks"]
    assert any("cash column not requested/not mapped" in n for n in built.notes)


def test_pnl_explain_cash_true_forces_the_risk_even_when_unmapped_and_run_fails_cleanly():
    # DECISIONS_LOG.md: cash: true is an explicit request, so recipes.py always honours it (adds
    # CashPaidToDate to risks) rather than silently downgrading it to "not mapped" the way `auto`
    # does. If the config truly has no mapping, pricebt's own PricingService already raises a clear
    # ConfigError naming the missing measure (src/pricebt/assets/pricing.py) the moment the engine
    # tries to price it -- recipes.py does not need to duplicate that check.
    eur_primary = {**PAYER_10Y, "kwargs": {**PAYER_10Y["kwargs"], "notional_currency": "EUR"}}
    s = spec("periodic_roll", assets=["tests/assets/toy_eur_irs.yaml"], instruments={"primary": eur_primary},
             dates={"end": date(2024, 2, 1)},
             pnl_explain={"enabled": True, "gamma": False, "carry": False, "cash": True})
    built = recipes.build(s)  # building never raises: it only decides what to ask for
    assert swap_pnl.CashPaidToDate in built.run_kwargs["risks"]
    with pytest.raises(ConfigError, match="CashPaidToDate"):
        recipes.run(s)


def test_pnl_explain_enabled_with_a_non_irswap_primary_is_skipped_not_crashed():
    s = spec("event", assets=["tests/assets/toy_usd_swaption.yaml"], event_dates=[date(2024, 2, 1)],
             instruments={"primary": {"class": "IRSwaption", "kwargs": {"pay_or_receive": "Pay", "buy_sell": "Buy",
                                                                        "expiration_date": "1y", "termination_date": "10y",
                                                                        "notional_currency": "USD", "strike": "ATM",
                                                                        "notional_amount": 1_000_000}}},
             rebalance={"trade_duration": None}, risks_to_report=["Price"],
             pnl_explain={"enabled": True, "gamma": True, "carry": True, "cash": False})
    built = recipes.build(s)  # must not raise
    assert "pnl_explain" not in built.run_kwargs
    assert any("primary is not IRSwap (got 'IRSwaption')" in n for n in built.notes)


def test_pnl_explain_actual_run_produces_a_working_pnl_explain_on_the_backtest():
    s = spec("periodic_roll", dates={"end": date(2024, 4, 2)},
             pnl_explain={"enabled": True, "gamma": True, "carry": True, "cash": "auto"})
    bt, built = recipes.run(s)
    assert swap_pnl.CashPaidToDate in built.run_kwargs["risks"]
    raw = bt.pnl_explain()
    assert set(raw) == {"PNL_delta", "PNL_gamma", "PNL_carry"}

    s2 = spec("periodic_roll", dates={"end": date(2024, 4, 2)},
              pnl_explain={"enabled": True, "gamma": False, "carry": True, "cash": "auto"})
    bt2, _ = recipes.run(s2)
    raw2 = bt2.pnl_explain()
    assert set(raw2) == {"PNL_delta", "PNL_carry"}


# --------------------------------------------------------------------------------- errors


def test_invalid_and_custom_specs_are_refused():
    with pytest.raises(ValueError, match="archetype"):
        recipes.build(spec("carry"))
    with pytest.raises(NotImplementedError, match="archetype-catalogue"):
        recipes.build(spec("custom"))


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
