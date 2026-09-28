"""GenericEngine scenario: the 040300 monthly swap-roll notebook variant (DESIGN.md section 12.4
row `test_engine_periodic_roll.py`; research/05 section 7.3). A USD 10y ATM payer swap entered
monthly via PeriodicTrigger + AddTradeAction('1m'), independently verified against PricingService
-- never against the engine's own bookkeeping.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.risk import Price
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _swap():
    return IRSwap("Pay", "10y", "USD", 10_000_000, fixed_rate="ATM", name="10y")


def _trigger(end_date):
    return PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1m", end_date=end_date),
        actions=[AddTradeAction(_swap(), "1m")],
    )


def test_monthly_roll_ledger_entry_is_atm_zero_and_exit_matches_independent_pv():
    _session()
    start, end = date(2024, 1, 2), date(2024, 4, 2)
    strategy = Strategy(initial_portfolio=None, triggers=[_trigger(end)])
    bt = GenericEngine().run_backtest(strategy, start=start, end=end, frequency="1b", show_progress=False)

    ledger = bt.trade_ledger()
    assert len(ledger) >= 3  # roughly one entry per month over a 3-month window
    session = PricebtSession.current
    closed = 0
    for name, row in ledger.iterrows():
        create_date = row["Open"]
        # Hand fact, not a magic number: resolve() pins fixed_rate to THAT day's own par rate for
        # 'ATM', so npv = notional * (float_pv - par_rate * annuity) = 0 by the definition of par.
        assert row["Open Value"] == pytest.approx(0.0, abs=1e-6)
        if row["Status"] == "closed":
            closed += 1
            resolved = session.pricing.resolve(_swap(), create_date, None)
            expected_exit_pv = session.pricing.value(resolved, row["Close"], Price, None)
            assert row["Close Value"] == pytest.approx(float(expected_exit_pv))
            assert row["Trade PnL"] == pytest.approx(row["Open Value"] + row["Close Value"])
    # Not every roll's ~1-month exit necessarily falls at-or-before `end` (the last one or two
    # don't), so this checks the shape rather than an exact count: at least one roll has closed,
    # the very last-created roll is still open, and every closed row's exit independently matched
    # PricingService above.
    assert 1 <= closed < len(ledger)
    last_created = ledger.sort_values("Open").iloc[-1]
    assert last_created["Status"] != "closed"


def test_result_summary_price_column_is_the_currently_held_swaps_pv():
    _session()
    d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
    strategy = Strategy(initial_portfolio=None, triggers=[_trigger(d2)])
    bt = GenericEngine().run_backtest(strategy, states=[d1, d2], show_progress=False)

    session = PricebtSession.current
    resolved = session.pricing.resolve(_swap(), d1, None)  # the one swap entered on d1, held on d2 too
    for d in (d1, d2):
        expected_pv = float(session.pricing.value(resolved, d, Price, None))
        assert bt.result_summary.loc[d, Price] == pytest.approx(expected_pv)


def test_an_off_grid_exit_still_appears_as_a_cash_only_row_in_result_summary():
    """research/05 section 7.3: a swap's monthly exit can fall on a date the main pricing grid
    does not otherwise visit; it must still show up in result_summary as a cash-only row
    (DEV-R1's non-flat/flat handling), not silently disappear. Forced deterministically with a
    SPARSE explicit `states=` (two widely-spaced dates), rather than hoping a '1b' schedule happens
    to skip a business-day-rolled '1m' exit."""
    _session()
    d0 = date(2024, 1, 2)
    later = date(2024, 3, 4)  # well past d0's ~1-month exit, and deliberately not that exit date
    # off-ATM on purpose: a nonzero entry PV means an incorrect forward-fill of the stale d0 value
    # (rather than DEV-R1's correct zero) would be a DIFFERENT, detectable number on exit_date.
    off_atm_swap = IRSwap("Pay", "10y", "USD", 10_000_000, fixed_rate="ATM+50", name="10y")
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1m", end_date=d0),
        actions=[AddTradeAction(off_atm_swap, "1m")],
    )
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    bt = GenericEngine().run_backtest(strategy, states=[d0, later], show_progress=False)

    ledger = bt.trade_ledger()
    assert len(ledger) == 1
    exit_date = ledger.iloc[0]["Close"]
    assert exit_date not in (d0, later)  # genuinely off the explicit two-date grid
    assert exit_date in bt.result_summary.index  # yet still a row

    session = PricebtSession.current
    resolved = session.pricing.resolve(off_atm_swap, d0, None)
    entry_pv = float(session.pricing.value(resolved, d0, Price, None))
    assert entry_pv != pytest.approx(0.0)  # confirms this scenario is NOT the degenerate ATM=0 case
    row = bt.result_summary.loc[exit_date]
    # The trade's holding window is create_date <= s < final_date, so on exit_date (== final_date)
    # nothing is held: DEV-R1 makes this a FLAT row (Price forced to 0), never d0's stale entry_pv.
    assert row[Price] == pytest.approx(0.0)
    expected_exit_pv = session.pricing.value(resolved, exit_date, Price, None)
    # Cumulative cash accumulates entry (-entry_pv) then exit (+expected_exit_pv).
    assert row["Cumulative Cash"] == pytest.approx(-entry_pv + float(expected_exit_pv))
