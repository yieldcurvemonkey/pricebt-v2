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

START, END = date(2024, 1, 2), date(2024, 6, 28)
CORE = ["ledger identity", "closed-trade PnL", "trade repricing", "book repricing", "cash roll-forward", "missing market"]


def _run(costs=False, notional=1e7):
    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=notional, name="swap")
    tc = ScaledTransactionModel(IRDeltaParallel, 0.25) if costs else None
    action = AddTradeAction(swap, "1m", transaction_cost=tc) if costs else AddTradeAction(swap, "1m")
    trig = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=END), [action])
    return GenericEngine().run_backtest(Strategy(None, trig), start=START, end=END, frequency="1b",
                                        risks=[Price, IRDeltaParallel], show_progress=False)


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


def test_attribution_residual_info_pass_and_fail(session, monkeypatch):
    """check_pnl_attribution_generic: INFO with no PnlDefinition; PASS on the attributed toy
    swaption (and dispatched by run_spot_checks); FAIL when nothing is explained or a cell is NaN."""
    assert spot_check.check_pnl_attribution_generic(_run()).status == "INFO"
    sys.path.insert(0, str(ROOT / "skills" / "pricebt-pnl-attribution" / "scripts"))
    import attribution

    bt = attribution.demo_backtest("swaption", end=date(2024, 2, 29))
    res = _by_name(spot_check.run_spot_checks(bt, sample=2))["attribution residual"]
    assert res.status == "PASS" and "VegaPnL" in res.detail and "residual variance share" in res.detail
    table = bt.pnl_explain_table()
    unexplained = table.assign(residual_pnl=table["economic_pnl"])
    monkeypatch.setattr(BackTest, "pnl_explain_table", lambda self: unexplained)
    assert spot_check.check_pnl_attribution_generic(bt).status == "FAIL"
    poisoned = table.assign(PNL_theta=table["PNL_theta"].where(table.index != table.index[5]))
    monkeypatch.setattr(BackTest, "pnl_explain_table", lambda self: poisoned)
    nan = spot_check.check_pnl_attribution_generic(bt)
    assert nan.status == "FAIL" and "PNL_theta" in nan.detail
