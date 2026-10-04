"""Automated spot checks for a finished pricebt BackTest: accounting identities, independent
repricing of trades and of the book, cash roll-forward, a P&L explain, and the frictions /
missing-market / determinism / limitations report. Run after every backtest, before any report.

In-process use (repository root as cwd, PYTHONPATH=src;tests):

    import sys; sys.path.insert(0, "skills/pricebt-spot-checks/scripts")
    import spot_check
    results = spot_check.run_spot_checks(backtest, rerun=lambda: run_it_again())
    print(spot_check.to_markdown(results))

CLI: python skills/pricebt-spot-checks/scripts/spot_check.py --demo   (toy monthly-roll backtest)
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional

import numpy as np
import pandas as pd

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
REL_TOL = 1e-6
ABS_TOL = 1e-4  # a hundredth of a cent: absorbs float noise on an ATM (PV ~ 0) entry


class CheckResult(NamedTuple):
    name: str
    status: str  # PASS | WARN | FAIL | INFO
    detail: str


def _close(a: float, b: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _fresh_pricing(session):
    """A PricingService with COLD caches on the same asset configs, so repricing re-evaluates the
    configs instead of reading back values the engine already cached."""
    from pricebt.assets.pricing import PricingService

    p = session.pricing
    return PricingService(session.registry, session.fx, tz=p.tz, eod_time=p.eod_time)


def _find(backtest, name, d):
    for inst in backtest.portfolio_dict.get(d, ()):
        if inst.name == name:
            return inst
    # A HedgeAction's ledger row names the scaled hedge Portfolio (gs parity: 'Scaled_<hedge name>_<date>'),
    # while portfolio_dict holds its instruments under their own names: return the booked Portfolio.
    from pricebt.markets.portfolio import Portfolio

    for cps in backtest.cash_payments.values():
        for cp in cps:
            if cp.trade.name == name and isinstance(cp.trade, Portfolio):
                return cp.trade
    return None


def _pv(pricing, obj, d, measure) -> float:
    """PV of one instrument, or the sum over a Portfolio's instruments (see _find)."""
    insts = getattr(obj, "all_instruments", None)
    return sum(float(pricing.value(i, d, measure, None)) for i in (insts if insts is not None else (obj,)))


def _payments_by_date(backtest) -> pd.Series:
    """Cash booked per date: the trades' cash payments plus, for a financed position (pricebt
    DEV-E22), the coupons and repo interest the engine booked as holding cash."""
    pays = {d: sum(sum(cp.cash_paid.values()) for cp in cps) for d, cps in backtest.cash_payments.items()}
    for d, day in getattr(backtest, "holding_cash", {}).items():
        if day:
            pays[d] = pays.get(d, 0.0) + sum(cash + financing for _ccy, cash, financing in day.values())
    return pd.Series(pays, dtype=float).sort_index()


# --- the checks. Each docstring names the mistake it catches. ------------------------------------


def check_ledger_identity(bt) -> CheckResult:
    """Total == price + Cumulative Cash + Transaction Costs on every row. Catches: a hand-built or
    post-processed result_summary, a wrong price_measure column (e.g. result_ccy run read in local
    ccy), a cost booked with the wrong sign."""
    rs = bt.result_summary
    expected = rs[bt.price_measure] + rs[bt.CUMULATIVE_CASH_COLUMN] + rs[bt.TRANSACTION_COSTS_COLUMN]
    scale = np.maximum(1.0, np.maximum(expected.abs(), rs[bt.TOTAL_COLUMN].abs()))
    bad = rs.index[((rs[bt.TOTAL_COLUMN] - expected).abs() > REL_TOL * scale).to_numpy()]
    if len(bad):
        return CheckResult("ledger identity", FAIL, f"{len(bad)}/{len(rs)} rows violate Total == Price + Cash + TC; first {bad[0]}")
    return CheckResult("ledger identity", PASS, f"{len(rs)} rows: Total == Price + Cumulative Cash + Transaction Costs")


def check_closed_trades(bt) -> CheckResult:
    """Trade PnL == Close Value + Open Value for every closed trade. Catches: a ledger that was
    edited/filtered, and exit cash booked to a different trade name than the entry."""
    led = bt.trade_ledger()
    closed = led[led["Status"] == "closed"] if len(led) else led
    bad = [n for n, r in closed.iterrows() if not _close(r["Trade PnL"], r["Close Value"] + r["Open Value"])]
    if bad:
        return CheckResult("closed-trade PnL", FAIL, f"{len(bad)} trades with Trade PnL != Close + Open, e.g. {bad[0]}")
    return CheckResult("closed-trade PnL", PASS, f"{len(closed)} closed trades: Trade PnL == Close Value + Open Value")


def check_trade_repricing(bt, pricing, sample, seed) -> CheckResult:
    """Open Value == -PV(open date) and Close Value == +PV(close date), repriced with cold caches
    on a deterministic sample of trades. Catches: wrong entry/exit sign, quantity_ not applied,
    exit priced on the wrong date (holding window), a trade priced with the wrong asset config."""
    led = bt.trade_ledger()
    names = sorted(n for n, r in led.iterrows() if r["Long Short"] != 0) if len(led) else []
    if not names:
        return CheckResult("trade repricing", INFO, "no trades to reprice")
    picked = random.Random(seed).sample(names, min(sample, len(names)))
    errors, done = [], 0
    for name in picked:
        row = led.loc[name]
        inst = _find(bt, name, row["Open"])
        if inst is None:
            errors.append(f"{name}: not in portfolio_dict[{row['Open']}]")
            continue
        pv_open = _pv(pricing, inst, row["Open"], bt.price_measure)
        if not _close(row["Open Value"], -pv_open):
            errors.append(f"{name}: Open Value {row['Open Value']:.6g} != -PV {-pv_open:.6g}")
        if row["Status"] == "closed":
            pv_close = _pv(pricing, inst, row["Close"], bt.price_measure)
            if not _close(row["Close Value"], pv_close):
                errors.append(f"{name}: Close Value {row['Close Value']:.6g} != +PV {pv_close:.6g} on {row['Close']}")
        done += 1
    if errors:
        return CheckResult("trade repricing", FAIL, "; ".join(errors[:5]))
    return CheckResult("trade repricing", PASS, f"{done} sampled trades: entry == -PV(open), exit == +PV(close) (cold-cache reprice)")


def check_book_repricing(bt, pricing, sample, seed) -> CheckResult:
    """Sum of held positions' PV == result_summary[price_measure] on sampled grid dates. Catches:
    positions missing from / left in the book (holding window), stale ffilled PV (gs DEV-R1),
    aggregation across currencies without result_ccy."""
    rs = bt.result_summary
    held = [d for d in bt.states if len(bt.portfolio_dict.get(d, ())) and d in rs.index]
    if not held:
        return CheckResult("book repricing", INFO, "no grid date holds positions")
    picked = sorted(random.Random(seed).sample(held, min(sample, len(held))))
    bad = []
    for d in picked:
        pv = sum(float(pricing.value(i, d, bt.price_measure, None)) for i in bt.portfolio_dict[d])
        if not _close(pv, rs.at[d, bt.price_measure]):
            bad.append(f"{d}: repriced {pv:.6g} vs reported {rs.at[d, bt.price_measure]:.6g}")
    if bad:
        return CheckResult("book repricing", FAIL, "; ".join(bad[:5]))
    return CheckResult("book repricing", PASS, f"{len(picked)} sampled dates: sum of position PVs == reported {bt.price_measure}")


def check_cash_rollforward(bt) -> CheckResult:
    """Cumulative Cash moves only on cash-payment dates (and, for a financed position, its holding-cash
    dates, pricebt DEV-E22) and equals initial cash + the running sum of payments. Catches: cash
    booked on the wrong date, double-booked exits, an accrual model that is on when you thought it
    was off."""
    if getattr(bt.strategy, "cash_accrual", None) is not None:
        return CheckResult("cash roll-forward", INFO, "cash accrual on: cash moves every day, roll-forward check skipped")
    rs = bt.result_summary
    cash = rs[bt.CUMULATIVE_CASH_COLUMN].astype(float)
    pay = _payments_by_date(bt)
    pay = pay[pay.index <= rs.index[-1]]
    moved = cash.index[(cash.diff().abs() > ABS_TOL).to_numpy()]
    stray = [d for d in moved[1:] if d not in pay.index]
    first = rs.index[0]
    initial = cash.iloc[0] - pay[pay.index <= first].sum()
    expected = initial + pay.reindex(cash.index.union(pay.index), fill_value=0.0).cumsum().reindex(cash.index)
    bad = cash.index[[not _close(a, b) for a, b in zip(cash, expected)]]
    if stray or len(bad):
        return CheckResult(
            "cash roll-forward", FAIL,
            f"cash moved on {len(stray)} non-payment dates{(' e.g. ' + str(stray[0])) if stray else ''}; "
            f"{len(bad)} rows != initial + cumsum(payments){(' e.g. ' + str(bad[0])) if len(bad) else ''}",
        )
    return CheckResult("cash roll-forward", PASS, f"cash == {initial:.6g} + cumsum of {len(pay)} payment dates; no stray moves")


def check_pnl_explain(bt, risk, rate_measure) -> CheckResult:
    """Daily dTotal vs risk(t-1) * d(rate). Catches: a risk sign convention flipped vs the P&L
    (dv01 quoted for a receiver), P&L driven by something other than the rate you think (roll,
    carry, a config bug), rates in % instead of bp. INFO/WARN only: carry and convexity are real."""
    if risk is None or rate_measure is None:
        return CheckResult("P&L explain", INFO, "skipped: pass risk=<scalar ccy/bp column> and rate_measure=<bp series>")
    rs = bt.result_summary
    d_total = rs[bt.TOTAL_COLUMN].astype(float).diff()
    rate = pd.Series(rate_measure).reindex(rs.index).ffill().astype(float)
    explain = rs[risk].astype(float).shift(1) * rate.diff()
    df = pd.DataFrame({"pnl": d_total, "explain": explain}).dropna()
    df = df[df["explain"] != 0]
    if len(df) < 3 or df["pnl"].std() == 0:
        return CheckResult("P&L explain", INFO, f"too few usable days ({len(df)})")
    corr = float(df["pnl"].corr(df["explain"]))
    resid_share = float((df["pnl"] - df["explain"]).var() / df["pnl"].var())
    r = rs[risk].astype(float)
    r = r[r.abs() > 1e-9]
    directional = len(r) > 0 and (np.sign(r).nunique() == 1)
    detail = f"corr(dTotal, risk[t-1]*drate) = {corr:.2f}, residual variance share = {resid_share:.0%} over {len(df)} days"
    if directional and corr < 0.5:
        return CheckResult("P&L explain", WARN, detail + " (directional book: expected corr >= 0.5; check risk sign/units)")
    return CheckResult("P&L explain", INFO, detail)


def _swap_pnl_rs_target() -> float:
    """swap_pnl.py's `RS_TARGET` (PNL_EXPLAIN_PLAN.md section 5.6), reached with the SAME
    cross-skill sys.path pattern check_asset.py already uses for the same module (plan section
    3.4, T1-C) -- try the plain import first (already on sys.path in most invocation contexts,
    e.g. this file's own tests), else add skills/pricebt-strategy-recipes/scripts and retry."""
    try:
        import swap_pnl
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pricebt-strategy-recipes" / "scripts"))
        import swap_pnl
    return swap_pnl.RS_TARGET


def check_pnl_attribution(pnl_stats: Optional[dict], target: Optional[float] = None) -> CheckResult:
    """PNL_EXPLAIN_PLAN.md section 7: is `swap_pnl.py`'s delta/gamma/carry attribution
    (`explain_stats()["residual_share"] = var(residual) / var(economic)`) explaining this book's
    P&L well enough to trust? PASS if `residual_share <= target`, WARN if `<= 10*target`, FAIL
    otherwise. INFO -- not a crash, not a silent skip -- when `pnl_stats` is None: P&L explain was
    not enabled for this run (spec `pnl_explain.enabled: false`), so there is nothing to check.

    `target` defaults to `swap_pnl.RS_TARGET`, the plan's own near-ATM-roll number (section 5.6),
    the one general default this check and the tearsheet's "P&L attribution" section both fall
    back to (plan section 7) so a future reader isn't confused by two different numbers meaning the
    same thing. That default is a near-ATM calibration: an off-market or directional book
    legitimately carries a larger residual (section 2.7 -- moneyness is a real, first-order cost,
    not a bug), so WARN/FAIL here is a prompt to check moneyness/cash/gamma units
    (pricebt-adversarial-review checklist), not proof the attribution is broken.
    """
    if pnl_stats is None:
        return CheckResult("P&L attribution", INFO, "pnl_explain not enabled for this run (spec pnl_explain.enabled: false): nothing to check")
    if target is None:
        target = _swap_pnl_rs_target()
    share = float(pnl_stats["residual_share"])
    detail = f"residual_share = {share:.2%} of economic P&L variance (target <= {target:.2%}, warn <= {10 * target:.2%})"
    if share <= target:
        return CheckResult("P&L attribution", PASS, detail)
    detail += "; check moneyness (off-market residual is real, section 2.7), coupon cash and gamma units before trusting the attribution"
    return CheckResult("P&L attribution", WARN if share <= 10 * target else FAIL, detail)


def check_missing_market(bt) -> CheckResult:
    """Dates dropped / exits rolled for missing market data. Catches: a data hole silently thinning
    the grid (and the daily-P&L statistics), exits booked a day or more late."""
    dropped, moves = list(bt.missing_market_dates), list(bt.missing_market_moves)
    grid = len(list(bt.states)) + len(dropped)
    share = len(dropped) / grid if grid else 0.0
    detail = f"{len(dropped)} dropped grid dates ({share:.1%}), {len(moves)} rolled exits"
    if dropped:
        detail += f"; dropped {dropped[:5]}{' ...' if len(dropped) > 5 else ''}"
    if moves:
        detail += f"; moves {moves[:3]}{' ...' if len(moves) > 3 else ''}"
    return CheckResult("missing market", WARN if share > 0.02 else (INFO if dropped or moves else PASS), detail)


def check_frictions(bt) -> List[CheckResult]:
    """Costs and financing as modelled. Catches: a frictionless backtest reported as tradable."""
    end = list(bt.states)[-1]
    tc = [float(v) for d, v in bt.transaction_costs.items() if d <= end]  # an exit TCE after the end is never booked
    out = []
    if any(math.isnan(v) for v in tc):
        out.append(CheckResult("transaction costs", FAIL, "NaN cost inside the backtest window: Total is NaN from that date"))
    elif not tc or all(v == 0 for v in tc):
        out.append(CheckResult("transaction costs", WARN, "all zero: costs not modelled (default ConstantTransactionModel(0))"))
    else:
        out.append(CheckResult("transaction costs", PASS, f"total {sum(tc):,.2f} over {len(tc)} cost dates in the window"))
    accrual = getattr(bt.strategy, "cash_accrual", None)
    out.append(CheckResult("cash accrual", INFO, f"on: {accrual!r}" if accrual is not None else "off: cash earns nothing"))
    return out


def check_determinism(bt, rerun) -> CheckResult:
    """Rerun and compare result_summary exactly. Catches: wall-clock dependence, unseeded randomness,
    state leaking between runs (a trigger or cache not reset)."""
    if rerun is None:
        return CheckResult("determinism", INFO, "skipped: pass rerun=<callable returning a fresh BackTest>")
    other = rerun()
    try:
        pd.testing.assert_frame_equal(bt.result_summary, other.result_summary, check_exact=True)
    except AssertionError as e:
        return CheckResult("determinism", FAIL, f"rerun differs: {str(e).splitlines()[0]}")
    return CheckResult("determinism", PASS, "rerun result_summary identical")


def check_open_positions(bt) -> CheckResult:
    """Trades still open at the end. Catches: mean-reversion 'exits' that are really offsetting
    trades held forever (gross book grows while net risk looks small)."""
    led = bt.trade_ledger()
    open_ = led[led["Status"] == "open"] if len(led) else led
    last = list(bt.states)[-1]
    gross = 0.0
    for name in open_.index:
        inst = _find(bt, name, last)
        try:
            gross += abs(float(getattr(inst, "notional_amount")))
        except Exception:  # no size attribute on this asset: report the count only
            gross = float("nan")
    return CheckResult("open at end", INFO, f"{len(open_)} of {len(led)} trades still open on {last}; gross notional {gross:,.0f}")


def _attribution():
    """skills/pricebt-pnl-attribution/scripts/attribution.py (one source for the stats and bands)."""
    import sys
    from pathlib import Path

    path = str(Path(__file__).resolve().parents[2] / "pricebt-pnl-attribution" / "scripts")
    if path not in sys.path:
        sys.path.insert(0, path)
    import attribution

    return attribution


def check_pnl_attribution_generic(bt, warn: Optional[float] = None, fail: Optional[float] = None) -> CheckResult:
    """Greeks attribution vs the economic P&L it explains, from bt.pnl_explain_table() (any
    PnlDefinition: ir/swaption/bond, fx, custom), graded by attribution.grade: PASS when the
    unexplained share (the worst of the residual variance share, sum|residual|/sum|economic| and
    1 - r2) is <= warn, WARN up to fail, FAIL above it, on any NaN, or above warn with a residual
    signature naming an attribute; INFO without a definition. Catches a material greek error: a
    wrong sign or unit, per-year theta, coupons with no Cashflows, NaN levels. A small term (half
    gamma on a book with little gamma P&L) can stay under warn: the detail then names it as
    immaterial. Diagnose with skills/pricebt-pnl-attribution/references/diagnosing-residuals.md."""
    name = "attribution residual"
    if getattr(bt, "pnl_explain_def", None) is None:
        return CheckResult(name, INFO, "no PnlDefinition: run_backtest(..., pnl_explain=attribution.definition_for(session)) to attribute P&L")
    att = _attribution()
    table = bt.pnl_explain_table()
    stats = att.explain_stats(table)
    status = att.grade(stats, warn if warn is not None else att.RESIDUAL_SHARE_WARN, fail if fail is not None else att.RESIDUAL_SHARE_FAIL)
    if not stats["finite"]:
        bad = table.columns[~np.isfinite(table.astype(float)).all()].tolist()
        return CheckResult(name, status, f"NaN/inf in pnl_explain_table columns {bad}: one NaN level poisons every later cumulative value")
    totals = ", ".join(f"{k} {v:,.0f}" for k, v in stats["totals"].items() if k not in ("actual_pnl", "explained_pnl"))
    detail = f"{att.grade_reason(stats)} over {stats['steps']} steps; totals {totals}"
    if stats["worst_date"] is not None:
        detail += f"; worst residual {stats['worst_residual']:,.2f} on {stats['worst_date']}"
    return CheckResult(name, status, detail)


def check_holding_cash(bt, pricing, sample, seed) -> CheckResult:
    """pricebt DEV-E22: every holding-cash booking of a sampled financed position, recomputed with
    cold caches from its asset config: the Cashflows rows its previous mark p listed with
    p < payment_date <= d (each converted at its payment date's FX when the run has a result_ccy),
    plus FinancingToDate(d) - FinancingToDate(p) (at d's FX). With the ledger identity and the cash roll-forward this is
    "Total change = Price change + coupons + financing" for the financed book. Catches: a coupon
    booked on the wrong date or twice, a financing leg booked from the wrong mark, a booking that
    does not match what the config says the position paid."""
    from pricebt.risk import Cashflows, FinancingToDate

    booked = {d: day for d, day in getattr(bt, "holding_cash", {}).items() if day}
    if not booked:
        return CheckResult("holding cash", INFO, "no financed position (no asset maps FinancingToDate): coupons and financing are not booked as cash (gs parity)")
    positions = sorted({inst for day in booked.values() for inst in day}, key=lambda i: i.name)  # the engine keys them by instrument
    picked = random.Random(seed).sample(positions, min(sample, len(positions)))
    bad, count, coupons, financing = [], 0, 0.0, 0.0
    for inst in picked:
        marks = sorted(d for d, insts in bt.portfolio_dict.items() if any(i.name == inst.name for i in insts))
        local = pricing.asset_for(inst).currency
        for d in sorted(x for x, day in booked.items() if inst in day):
            prev = [m for m in marks if m < d]
            if not prev:
                bad.append(f"{inst.name} on {d}: booked with no earlier mark")
                continue
            p = prev[-1]
            ccy, cash, fin = booked[d][inst]
            fx = (lambda x: 1.0) if ccy == local else (lambda x: pricing.fx(local, ccy, x))  # noqa: E731
            frame = pricing.value(inst, p, Cashflows, None) if pricing.maps(inst, Cashflows) else None
            due = 0.0 if frame is None or not len(frame) else float(sum(
                a * fx(pd.Timestamp(x).date()) for x, a in zip(frame["payment_date"], frame["payment_amount"]) if p < pd.Timestamp(x).date() <= d))
            want_fin = (float(pricing.value(inst, d, FinancingToDate, None)) - float(pricing.value(inst, p, FinancingToDate, None))) * fx(d)
            if not (_close(cash, due) and _close(fin, want_fin)):
                bad.append(f"{inst.name} on {d}: booked coupons {cash:,.4f}, financing {fin:,.4f} vs the config's {due:,.4f}, {want_fin:,.4f} (marked {p})")
            count, coupons, financing = count + 1, coupons + cash, financing + fin
    if bad:
        return CheckResult("holding cash", FAIL, "; ".join(bad[:4]))
    return CheckResult("holding cash", PASS, f"{count} bookings of {len(picked)} of {len(positions)} financed positions (cold-cache reprice): coupons {coupons:,.2f}, financing {financing:,.2f} == the configs' Cashflows dropped + FinancingToDate change")


LIMITATIONS = [
    "coupons paid between marks are booked as cash only for positions whose asset maps FinancingToDate (pricebt DEV-E22: every Bond"
    " config, with its repo financing); for every other asset (swaps, swaptions) they are not (gs parity): carry-heavy P&L is understated",
    "signal and execution on the same close: no next-day fill, no slippage beyond the cost model",
]


def run_spot_checks(
    backtest,
    session=None,
    sample: int = 5,
    seed: int = 0,
    rerun: Optional[Callable] = None,
    rate_measure: Optional[pd.Series] = None,
    risk=None,
    pnl_stats: Optional[dict] = None,
) -> List[CheckResult]:
    """Run every automated check. `session` defaults to PricebtSession.current (it must hold the
    asset configs the backtest used). `rerun` is a zero-argument callable returning a fresh
    BackTest. `risk` is a scalar ccy/bp result_summary column (e.g. IRDeltaParallel) and
    `rate_measure` a bp series (e.g. measure_series(..., <the function mapped to IRFwdRate>, ...) times
    its unit's factor to bp, as the SKILL shows) for the P&L explain.
    `pnl_stats` is `swap_pnl.explain_stats(...)`'s dict (PNL_EXPLAIN_PLAN.md section 7); omitted or
    None (the default, so every existing caller keeps working unchanged) means P&L explain was not
    enabled for this run and the "P&L attribution" check reports INFO."""
    from pricebt.session import PricebtSession

    session = session or PricebtSession.current
    pricing = _fresh_pricing(session)
    steps = [
        ("ledger identity", lambda: check_ledger_identity(backtest)),
        ("closed-trade PnL", lambda: check_closed_trades(backtest)),
        ("trade repricing", lambda: check_trade_repricing(backtest, pricing, sample, seed)),
        ("book repricing", lambda: check_book_repricing(backtest, pricing, sample, seed)),
        ("cash roll-forward", lambda: check_cash_rollforward(backtest)),
        ("holding cash", lambda: check_holding_cash(backtest, pricing, sample, seed)),
        ("P&L explain", lambda: check_pnl_explain(backtest, risk, rate_measure)),
        ("attribution residual", lambda: check_pnl_attribution_generic(backtest)),
        ("P&L attribution", lambda: check_pnl_attribution(pnl_stats)),
        ("missing market", lambda: check_missing_market(backtest)),
        ("frictions", lambda: check_frictions(backtest)),
        ("determinism", lambda: check_determinism(backtest, rerun)),
        ("open at end", lambda: check_open_positions(backtest)),
    ]
    results: List[CheckResult] = []
    for name, step in steps:
        try:
            r = step()
        except Exception as e:  # a crashing check must not hide the others; it is itself a finding
            r = CheckResult(name, FAIL, f"check crashed: {type(e).__name__}: {e}")
        results.extend(r if isinstance(r, list) else [r])
    results.extend(CheckResult("known limitation", INFO, text) for text in LIMITATIONS)
    return results


def to_markdown(results: List[CheckResult]) -> str:
    lines = ["| Check | Status | Detail |", "|---|---|---|"]
    lines += [f"| {r.name} | **{r.status}** | {r.detail.replace('|', '/')} |" for r in results]
    return "\n".join(lines)


def _demo():
    from datetime import date

    from pricebt.backtests.actions import AddTradeAction
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.backtests.strategy import Strategy
    from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
    from pricebt.instrument import IRSwap
    from pricebt.risk import IRDeltaParallel, Price
    from pricebt.session import PricebtSession

    PricebtSession.use(assets=["tests/assets/toy_usd_irs.yaml"])
    start, end = date(2024, 1, 2), date(2024, 6, 28)

    def run():
        swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1e7, name="swap")
        trig = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=end), [AddTradeAction(swap, "1m")])
        return GenericEngine().run_backtest(Strategy(None, trig), start=start, end=end, frequency="1b",
                                            risks=[Price, IRDeltaParallel], show_progress=False)

    print(to_markdown(run_spot_checks(run(), rerun=run)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo", action="store_true", help="run the checks on a toy monthly-roll USD swap backtest")
    if ap.parse_args().demo:
        _demo()
    else:
        ap.print_help()
