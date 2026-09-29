"""skills/pricebt-spot-checks/scripts/spot_check.py: every core check passes on a correct toy
backtest, and each one FAILs (or WARNs) when the thing it guards is tampered with."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.backtest_objects import BackTest, ScaledTransactionModel
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.data import measure_series
from pricebt.instrument import IRSwap
from pricebt.risk import IRDeltaParallel, Price
from pricebt.session import PricebtSession

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pricebt-spot-checks" / "scripts"))
import spot_check  # noqa: E402

sys.path.insert(0, str(ROOT / "skills" / "pricebt-strategy-recipes" / "scripts"))
import swap_pnl  # noqa: E402

START, END = date(2024, 1, 2), date(2024, 6, 28)
CORE = ["ledger identity", "closed-trade PnL", "trade repricing", "book repricing", "cash roll-forward", "missing market"]


def _run(costs=False, notional=1e7, pnl_explain=None):
    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=notional, name="swap")
    tc = ScaledTransactionModel(IRDeltaParallel, 0.25) if costs else None
    action = AddTradeAction(swap, "1m", transaction_cost=tc) if costs else AddTradeAction(swap, "1m")
    trig = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=END), [action])
    risks = [Price, IRDeltaParallel, swap_pnl.CashPaidToDate] if pnl_explain is not None else [Price, IRDeltaParallel]
    return GenericEngine().run_backtest(Strategy(None, trig), start=START, end=END, frequency="1b",
                                        risks=risks, pnl_explain=pnl_explain, show_progress=False)


@pytest.fixture
def session():
    return PricebtSession.use(assets=[ROOT / "tests" / "assets" / "toy_usd_irs.yaml"])


def _by_name(results):
    return {r.name: r for r in results}


@pytest.mark.parametrize("costs", [False, True])
def test_core_checks_pass_on_the_toy(session, costs):
    bt = _run(costs)
    results = spot_check.run_spot_checks(bt, session=session, sample=10, rerun=lambda: _run(costs))
    res = _by_name(results)
    for name in CORE + ["determinism"]:
        assert res[name].status == "PASS", res[name]
    assert res["transaction costs"].status == ("PASS" if costs else "WARN")
    assert "5 closed trades" in res["closed-trade PnL"].detail
    assert "1 of 6 trades still open" in res["open at end"].detail
    assert sum(r.name == "known limitation" for r in results) == 2
    md = spot_check.to_markdown(results)
    assert md.startswith("| Check | Status | Detail |") and "coupons paid between marks" in md


def test_identity_check_fails_on_a_tampered_result_summary(session, monkeypatch):
    bt = _run()
    tampered = bt.result_summary.copy()
    tampered.iloc[10, tampered.columns.get_loc("Total")] += 1000.0
    monkeypatch.setattr(BackTest, "result_summary", property(lambda self: tampered))
    assert spot_check.check_ledger_identity(bt).status == "FAIL"


def test_book_repricing_fails_when_the_reported_price_is_wrong(session, monkeypatch):
    bt = _run()
    tampered = bt.result_summary.copy()
    tampered[Price] = tampered[Price] * 1.01
    monkeypatch.setattr(BackTest, "result_summary", property(lambda self: tampered))
    pricing = spot_check._fresh_pricing(session)
    assert spot_check.check_book_repricing(bt, pricing, sample=50, seed=0).status == "FAIL"


def test_trade_repricing_and_cash_rollforward_fail_on_a_tampered_exit_payment(session):
    bt = _run()
    exit_date = sorted(bt.cash_payments)[1]
    cp = next(c for c in bt.cash_payments[exit_date] if c.direction == 1)
    cp.cash_paid["USD"] *= 1.01  # ledger Close Value no longer equals +PV(close); cash_dict is untouched
    res = _by_name(spot_check.run_spot_checks(bt, session=session, sample=10))
    assert res["trade repricing"].status == "FAIL"
    assert res["cash roll-forward"].status == "FAIL"


def test_determinism_fails_when_the_rerun_differs(session):
    bt = _run()
    assert spot_check.check_determinism(bt, lambda: _run(notional=2e7)).status == "FAIL"
    assert spot_check.check_determinism(bt, None).status == "INFO"


def test_pnl_explain_high_correlation_and_warn_on_a_flipped_rate(session):
    bt = _run()
    rate = measure_series(IRSwap(termination_date="10y", notional_currency="USD"), "par_rate", START, END)
    good = spot_check.check_pnl_explain(bt, IRDeltaParallel, rate)
    assert good.status == "INFO" and "corr" in good.detail
    corr = float(good.detail.split("= ")[1].split(",")[0])
    assert corr > 0.9
    assert spot_check.check_pnl_explain(bt, IRDeltaParallel, -rate).status == "WARN"  # directional payer book


def test_pnl_attribution_info_when_explain_not_enabled():
    assert spot_check.check_pnl_attribution(None).status == "INFO"


@pytest.mark.parametrize(
    "share,expected",
    [(1e-6, "PASS"), (5e-3, "WARN"), (2e-2, "FAIL")],
    ids=["inside-target", "5x-target-inside-10x", "20x-target"],
)
def test_pnl_attribution_status_thresholds(share, expected):
    result = spot_check.check_pnl_attribution({"residual_share": share}, target=1e-3)
    assert result.status == expected, result


def test_pnl_attribution_wired_into_run_spot_checks_with_real_stats(session):
    bt = _run(pnl_explain=swap_pnl.swap_pnl_definition())
    stats = swap_pnl.explain_stats(swap_pnl.explain_table(bt))
    expected_status = spot_check.check_pnl_attribution(stats).status
    # this is the T-ROLL shape (near-ATM monthly roll): plan section 5.6 says this book lands
    # residual_share <= RS_TARGET (observed ~1.8e-4 against a 1e-3 target), so the default-target
    # path (target=None -> swap_pnl.RS_TARGET) must reach PASS here, not just "some real status".
    assert expected_status == "PASS", stats

    results = spot_check.run_spot_checks(bt, session=session, sample=10, pnl_stats=stats)
    res = _by_name(results)
    assert res["P&L attribution"].status == expected_status
    assert "residual_share" in res["P&L attribution"].detail

    # regression: every pre-existing check still runs, unchanged shape
    for name in CORE + ["determinism", "P&L explain", "open at end"]:
        assert name in res
    assert sum(r.name == "known limitation" for r in results) == 2


def test_run_spot_checks_without_pnl_stats_is_backward_compatible(session):
    """Today's callers don't know about `pnl_stats` and never pass it -- the new check must not
    break or change behaviour for them."""
    bt = _run()
    results = spot_check.run_spot_checks(bt, session=session, sample=10)
    res = _by_name(results)
    assert res["P&L attribution"].status == "INFO"
    assert "not enabled" in res["P&L attribution"].detail


def test_missing_market_warns_above_two_percent(session):
    bt = _run()
    bt.missing_market_dates = [START + timedelta(days=i) for i in range(10)]
    assert spot_check.check_missing_market(bt).status == "WARN"


def test_nan_cost_inside_the_window_fails(session):
    bt = _run(costs=True)
    first = min(bt.transaction_costs)
    bt.transaction_costs = {**bt.transaction_costs, first: float("nan")}
    assert spot_check.check_frictions(bt)[0].status == "FAIL"


def test_demo_cli_prints_a_table(capsys):
    spot_check._demo()
    out = capsys.readouterr().out
    assert "| ledger identity | **PASS** |" in out and "| determinism | **PASS** |" in out
