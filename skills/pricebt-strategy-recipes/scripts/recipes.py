"""Turn a validated pricebt strategy spec into a runnable gs-style backtest.

Every archetype maps to plain pricebt.backtests constructs (the gs_quant.backtests API), catalogued in
skills/pricebt-strategy-recipes/references/archetype-catalogue.md.

In-process:
    import sys; sys.path.insert(0, "skills/pricebt-strategy-recipes/scripts")
    import recipes
    backtest, built = recipes.run("my_spec.yaml")      # or recipes.build(spec) then run it yourself
    print(recipes.describe(built))

Each archetype builder (periodic_roll, mean_reversion, momentum, curve_trade, delta_hedged,
risk_band, event) is also a plain function taking instruments and keyword arguments; it returns
Parts(triggers, signal, notes) to put into Strategy(None, parts.triggers).

CLI (repository root):
    python skills/pricebt-strategy-recipes/scripts/recipes.py describe SPEC.yaml
    python skills/pricebt-strategy-recipes/scripts/recipes.py run SPEC.yaml [--out result_summary.csv]
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pricebt-strategy-intake" / "scripts"))
import spec as specmod  # noqa: E402

from pricebt import instrument as _instrument_mod  # noqa: E402
from pricebt.backtests.actions import (  # noqa: E402
    Action,
    AddScaledTradeAction,
    AddTradeAction,
    ExitAllPositionsAction,
    HedgeAction,
    ScalingActionType,
)
from pricebt.backtests.backtest_objects import (  # noqa: E402
    ConstantCashAccrualModel,
    ConstantTransactionModel,
    ScaledTransactionModel,
    TransactionModel,
)
from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy  # noqa: E402
from pricebt.backtests.generic_engine import GenericEngine  # noqa: E402
from pricebt.backtests.strategy import Strategy  # noqa: E402
from pricebt.backtests.triggers import (  # noqa: E402
    AggregateTrigger,
    AggregateTriggerRequirements,
    AggType,
    DateTrigger,
    DateTriggerRequirements,
    MeanReversionTrigger,
    MeanReversionTriggerRequirements,
    MktTrigger,
    MktTriggerRequirements,
    PeriodicTrigger,
    PeriodicTriggerRequirements,
    RiskTriggerRequirements,
    StrategyRiskTrigger,
    Trigger,
    TriggerDirection,
)
from pricebt.common import ParameterisedRiskMeasure, RiskMeasure  # noqa: E402
from pricebt.data import measure_series  # noqa: E402
from pricebt.instrument import Instrument  # noqa: E402
from pricebt.risk import IRDelta, Price  # noqa: E402
from pricebt.session import PricebtSession  # noqa: E402

BOOK_DV01 = IRDelta(aggregation_level="Type")  # scalar book dv01, currency per +1bp (payer > 0 on the shipped configs)
NEXT = "next schedule"


@dataclass
class Parts:
    """What an archetype builder returns: the triggers, the series its trigger reads (if any) and
    the modelling choices it made."""

    triggers: list
    signal: Optional[pd.Series] = None
    notes: list = field(default_factory=list)


@dataclass
class Built:
    strategy: Strategy
    run_kwargs: dict
    signal: Optional[pd.Series]
    notes: list
    spec: Optional[dict] = None


# ------------------------------------------------------------------------------------ helpers


def make_instrument(key: str, entry: dict, notional: Optional[float] = None) -> Instrument:
    """`instruments.<key>` of a spec -> an unresolved pricebt instrument named `key`."""
    cls = getattr(_instrument_mod, entry["class"])
    kwargs = dict(entry.get("kwargs") or {})
    if notional is not None and "notional_amount" in kwargs:
        kwargs["notional_amount"] = notional
    return cls(**kwargs, name=key)


def direction_sign(inst: Instrument) -> int:
    """-1 for a Receive-fixed instrument, else +1: the sign of its unit dv01 under the payer > 0
    convention, i.e. the sign a risk_measure scaling_level must carry so the leg keeps its direction."""
    v = inst.kwargs.get("pay_or_receive")
    return -1 if v is not None and str(getattr(v, "value", v)).lower().startswith("rec") else 1


def flipped(inst: Instrument, name: str) -> Instrument:
    """The opposite position: pay_or_receive swapped when the instrument has one, else quantity_ negated."""
    v = inst.kwargs.get("pay_or_receive")
    if v is None:
        return inst.clone(name=name, quantity_=-inst.quantity_)
    return inst.clone(name=name, pay_or_receive="Pay" if direction_sign(inst) < 0 else "Receive")


def transaction_model(model: str = "none", level: float = 0.0, dv01: RiskMeasure = BOOK_DV01) -> TransactionModel:
    if model == "none":
        return ConstantTransactionModel(0)
    if model == "constant":
        return ConstantTransactionModel(level)
    if model == "notional_bp":
        return ScaledTransactionModel("notional_amount", level * 1e-4)
    if model == "dv01_bp":
        return ScaledTransactionModel(dv01, level)
    raise ValueError(f"unknown costs.model {model!r}")


def with_ccy(measure: RiskMeasure, result_ccy: Optional[str]) -> RiskMeasure:
    """run_backtest(result_ccy=X) rewrites its risks to r(currency=X), but triggers and hedges index
    results by the measure they were given, so they must be given the rewritten one."""
    if result_ccy and isinstance(measure, ParameterisedRiskMeasure):
        return measure(currency=result_ccy)
    return measure


def entry_action(inst: Instrument, name: str, trade_duration: Any = None, sizing: Optional[dict] = None,
                 transaction_cost: Optional[TransactionModel] = None, sizing_risk: RiskMeasure = BOOK_DV01) -> Action:
    """AddTradeAction for sizing notional (or None); AddScaledTradeAction for dv01_target (risk_measure,
    level signed by the leg's direction) or nav (ScalingActionType.NAV)."""
    tc = transaction_cost or ConstantTransactionModel(0)
    method = (sizing or {}).get("method", "notional")
    if method == "notional":
        return AddTradeAction(inst, trade_duration, name=name, transaction_cost=tc)
    if method == "dv01_target":
        return AddScaledTradeAction(inst, trade_duration, name=name, scaling_type=ScalingActionType.risk_measure,
                                    scaling_risk=sizing_risk, scaling_level=direction_sign(inst) * abs(sizing["dv01_target"]),
                                    transaction_cost=tc)
    if method == "nav":
        return AddScaledTradeAction(inst, trade_duration, name=name, scaling_type=ScalingActionType.NAV,
                                    scaling_level=sizing["nav"], transaction_cost=tc)
    raise ValueError(f"unknown sizing.method {method!r}")


def _periodic(frequency: str, end, start=None) -> PeriodicTriggerRequirements:
    return PeriodicTriggerRequirements(start_date=start, end_date=end, frequency=frequency)


def _sizing_note(sizing: Optional[dict]) -> list:
    method = (sizing or {}).get("method", "notional")
    if method == "dv01_target":
        return [f"size: AddScaledTradeAction(scaling_type=risk_measure, scaling_risk=IRDelta(Type)); "
                f"scaling_level = +/-{abs(sizing['dv01_target'])} signed by each leg's direction (scale = level / unit dv01)"]
    if method == "nav":
        return [f"size: AddScaledTradeAction(scaling_type=NAV, scaling_level={sizing['nav']}): scale = cash / entry PV; "
                "meaningless for par (ATM) swaps whose entry PV is ~0 -- use it for premium instruments"]
    return []


# ------------------------------------------------------------------------------------ archetypes


def periodic_roll(primary: Instrument, *, frequency: str = "1m", end=None, trade_duration: Any = NEXT,
                  sizing: Optional[dict] = None, transaction_cost: Optional[TransactionModel] = None,
                  sizing_risk: RiskMeasure = BOOK_DV01) -> Parts:
    """Carry / roll-down harvest: enter a fresh `primary` every `frequency`, hold for `trade_duration`."""
    action = entry_action(primary, "Roll", trade_duration, sizing, transaction_cost, sizing_risk)
    return Parts([PeriodicTrigger(_periodic(frequency, end), action)],
                 notes=[f"PeriodicTrigger({frequency}) + {type(action).__name__}(primary, {trade_duration!r})",
                        *_sizing_note(sizing)])


def mean_reversion(primary: Instrument, series: pd.Series, *, z_entry: float = 2.0, lookback: int = 30,
                   transaction_cost: Optional[TransactionModel] = None) -> Parts:
    """gs 040304: fade |z| > z_entry of `series` vs its rolling mean/std (window excludes today)."""
    ds = GenericDataSource(series, MissingDataStrategy.fill_forward)
    req = MeanReversionTriggerRequirements(ds, z_entry, lookback, lookback)
    action = AddTradeAction(primary, None, name="MeanRev", transaction_cost=transaction_cost or ConstantTransactionModel(0))
    return Parts([MeanReversionTrigger(req, action)], signal=series, notes=[
        f"MeanReversionTrigger(z_score_bound={z_entry}, rolling windows={lookback}) + AddTradeAction(primary)",
        "gs semantics: signal and execution at the same close; entry scaling is -1 when the signal is above its mean "
        "(sell primary), +1 below; the 'exit' when the signal crosses back through the mean is an OFFSETTING trade, "
        "and both trades are held forever (never closed in the trade ledger)",
        "rolling window = the `lookback` observations strictly before the trigger date (sample std, ddof=1)",
    ])


def momentum(primary: Instrument, series: pd.Series, *, lookback: int = 20, threshold_bp: float = 0.0,
             frequency: str = "1m", end=None, trade_duration: Any = NEXT, sizing: Optional[dict] = None,
             transaction_cost: Optional[TransactionModel] = None, sizing_risk: RiskMeasure = BOOK_DV01) -> Parts:
    """Trend following: on each `frequency` date hold `primary` if the signal rose by more than
    threshold_bp over `lookback` observations, its opposite if it fell by more, nothing otherwise."""
    change = series.diff(lookback).dropna()  # value on d uses only observations <= d
    ds = GenericDataSource(change, MissingDataStrategy.fill_forward)
    triggers = []
    for direction, level, inst, name in (
        (TriggerDirection.ABOVE, threshold_bp, primary, "MomUp"),
        (TriggerDirection.BELOW, -threshold_bp, flipped(primary, f"{primary.name}_opp"), "MomDown"),
    ):
        agg = AggregateTriggerRequirements(
            [_periodic(frequency, end), MktTriggerRequirements(ds, level, direction)], AggType.ALL_OF)
        triggers.append(AggregateTrigger(agg, entry_action(inst, name, trade_duration, sizing, transaction_cost, sizing_risk)))
    return Parts(triggers, signal=change, notes=[
        f"momentum signal = {lookback}-observation change of the measure (uses data up to the trigger date only)",
        f"AggregateTrigger(ALL_OF[PeriodicTriggerRequirements({frequency}), MktTriggerRequirements(change, "
        f"+{threshold_bp}, ABOVE)]) -> primary; the BELOW -{threshold_bp} twin -> the opposite position",
        "the Periodic child is first, so its info_dict (next_schedule) reaches the action through the aggregate: "
        f"trade_duration {trade_duration!r} works",
        "gs semantics: the signal and the trade use the same close", *_sizing_note(sizing),
    ])


def curve_trade(first: Instrument, second: Instrument, *, frequency: str = "1m", end=None,
                trade_duration: Any = NEXT, sizing: Optional[dict] = None,
                transaction_cost: Optional[TransactionModel] = None, sizing_risk: RiskMeasure = BOOK_DV01) -> Parts:
    """Two opposite legs (e.g. a 2s10s steepener: receive 2y, pay 10y), each sized to the same |dv01|
    so the book is dv01-neutral on every entry date."""
    notes = []
    if (sizing or {}).get("method") != "dv01_target":
        notes.append("sizing is not dv01_target: legs traded at their notionals, so the book is NOT dv01-neutral")
    triggers = [PeriodicTrigger(_periodic(frequency, end), [
        entry_action(first, "Leg1", trade_duration, sizing, transaction_cost, sizing_risk),
        entry_action(second, "Leg2", trade_duration, sizing, transaction_cost, sizing_risk)])]
    return Parts(triggers, notes=[
        f"PeriodicTrigger({frequency}) + one action per leg, trade_duration {trade_duration!r}", *_sizing_note(sizing),
        "dv01-neutral only on entry dates: the legs' dv01 drift apart between rolls", *notes])


def delta_hedged(primary: Instrument, hedge: Instrument, *, start, end=None, frequency: str = "1b",
                 risk: RiskMeasure = BOOK_DV01, hedge_duration: Any = None, sizing: Optional[dict] = None,
                 transaction_cost: Optional[TransactionModel] = None, sizing_risk: RiskMeasure = BOOK_DV01) -> Parts:
    """Hold `primary` from `start`; every `frequency` date hedge the book's `risk` with `hedge`."""
    enter = DateTrigger(DateTriggerRequirements([start]),
                        entry_action(primary, "Enter", None, sizing, transaction_cost, sizing_risk))
    hedger = PeriodicTrigger(_periodic(frequency, end),
                             HedgeAction(risk, hedge, hedge_duration, name="Hedge",
                                         transaction_cost=transaction_cost or ConstantTransactionModel(0)))
    return Parts([enter, hedger], notes=[
        "entry: DateTrigger([start]) + entry action (not Strategy.initial_portfolio, which books no transaction "
        "cost and cannot be dv01-sized)",
        f"hedge: PeriodicTrigger({frequency}) + HedgeAction({risk!r}, hedge, trade_duration={hedge_duration!r})",
        "hedges with trade_duration None accumulate (each date trades only the residual); 'next schedule' would "
        "instead re-trade the whole hedge every period and pay its costs each time",
        "gs semantics: every hedge placed on a date is sized against the same pre-hedge book risk",
        *_sizing_note(sizing)])


def risk_band(primary: Instrument, hedge: Instrument, *, max_abs_dv01: float, frequency: str = "1m", end=None,
              trade_duration: Any = None, risk: RiskMeasure = BOOK_DV01, risk_percentage: float = 100,
              sizing: Optional[dict] = None, transaction_cost: Optional[TransactionModel] = None,
              sizing_risk: RiskMeasure = BOOK_DV01) -> Parts:
    """Add `primary` every `frequency`; whenever the book's |risk| leaves [-max, +max], hedge
    `risk_percentage`% of it with `hedge` on the same date."""
    tc = transaction_cost or ConstantTransactionModel(0)
    triggers = [PeriodicTrigger(_periodic(frequency, end), entry_action(primary, "Add", trade_duration, sizing, tc, sizing_risk))]
    for level, direction, name in ((max_abs_dv01, TriggerDirection.ABOVE, "BandHi"), (-max_abs_dv01, TriggerDirection.BELOW, "BandLo")):
        triggers.append(StrategyRiskTrigger(RiskTriggerRequirements(risk, level, direction),
                                            HedgeAction(risk, hedge, None, name=name, transaction_cost=tc,
                                                        risk_percentage=risk_percentage)))
    notes = [f"PeriodicTrigger({frequency}) + entry action(primary, {trade_duration!r})",
             f"StrategyRiskTrigger(RiskTriggerRequirements({risk!r}, +/-{max_abs_dv01}, ABOVE/BELOW)) -> "
             f"HedgeAction(risk_percentage={risk_percentage}), hedges held to the end",
             "the trigger reads the book risk at the close after that day's scheduled trades are on (DEV-E1)"]
    if risk_percentage < 100:
        notes.append(f"risk_percentage {risk_percentage} < 100: the post-hedge book can still exceed the band "
                     f"when |risk| > max / (1 - {risk_percentage}/100)")
    return Parts(triggers, notes=notes + _sizing_note(sizing))


def event(primary: Instrument, dates: Iterable, *, trade_duration: Any = None, sizing: Optional[dict] = None,
          transaction_cost: Optional[TransactionModel] = None, sizing_risk: RiskMeasure = BOOK_DV01) -> Parts:
    """Enter `primary` on each of `dates` (e.g. central-bank meetings), hold for `trade_duration`."""
    dates = sorted(dates)
    action = entry_action(primary, "Event", trade_duration, sizing, transaction_cost, sizing_risk)
    return Parts([DateTrigger(DateTriggerRequirements(dates), action)], notes=[
        f"DateTrigger({len(dates)} dates) + {type(action).__name__}(primary, {trade_duration!r}); "
        "'next schedule' means the next event date", *_sizing_note(sizing)])


def stop_loss_overlay(stop_loss_mtm: float, *, price_measure: RiskMeasure = Price) -> Parts:
    """Exit every open position when the book's PV falls below -stop_loss_mtm."""
    trig = StrategyRiskTrigger(RiskTriggerRequirements(price_measure, -abs(stop_loss_mtm), TriggerDirection.BELOW),
                               ExitAllPositionsAction(name="StopLoss"))
    return Parts([trig], notes=[
        f"stop-loss overlay: StrategyRiskTrigger(RiskTriggerRequirements(Price, -{abs(stop_loss_mtm)}, BELOW)) -> "
        "ExitAllPositionsAction",
        "APPROXIMATION: it reads the PV of the positions open that day, not realised P&L (gs has no P&L trigger); "
        "later entry triggers still fire, so the strategy can re-enter after a stop"])


# ------------------------------------------------------------------------------------ spec -> backtest


def use_session(spec: dict) -> PricebtSession:
    fx = spec.get("fx")
    return PricebtSession.use(assets=[str(specmod.resolve_path(a)) for a in spec["assets"]],
                              fx=str(specmod.resolve_path(fx)) if fx else None)


def _prepare(spec: Union[dict, str, Path]) -> dict:
    spec, _ = specmod.apply_defaults(spec)
    errors = specmod.validate_spec(spec)
    if errors:
        raise ValueError("invalid spec:\n  " + "\n  ".join(errors))
    return spec


def signal_series(spec: Union[dict, str, Path]) -> Optional[pd.Series]:
    """The raw measure series the signal is built from: `signal.measure` of a fresh, unresolved copy
    of `signal.instrument` on every `dates.frequency` date (pricebt.data.measure_series). Needs a
    PricebtSession (build() sets one). None when signal.type is none."""
    spec = _prepare(spec)
    sig, d = spec["signal"], spec["dates"]
    if sig["type"] == "none":
        return None
    entry = spec["instruments"][sig["instrument"]]
    fresh = getattr(_instrument_mod, entry["class"])(**(entry.get("kwargs") or {}))
    return measure_series(fresh, sig["measure"], d["start"], d["end"], frequency=d["frequency"],
                          holiday_calendar=d.get("holiday_calendar") or None)


def build(spec: Union[dict, str, Path]) -> Built:
    """Defaults + validation, then the archetype's strategy and the GenericEngine.run_backtest kwargs.
    Registers the spec's assets in a new PricebtSession (needed to price a signal)."""
    spec = _prepare(spec)
    use_session(spec)
    arch, d, sz = spec["archetype"], spec["dates"], spec["sizing"]
    if arch == "custom":
        raise NotImplementedError("archetype custom: compose triggers/actions directly, following "
                                  "skills/pricebt-strategy-recipes/references/archetype-catalogue.md")
    ccy = spec.get("result_ccy")
    dv01 = with_ccy(BOOK_DV01, ccy)
    notional = sz.get("notional") if sz["method"] == "notional" else None
    insts = {k: make_instrument(k, v, notional) for k, v in spec["instruments"].items()}
    primary = insts["primary"]
    tc = transaction_model(spec["costs"]["model"], spec["costs"].get("level") or 0.0, dv01)
    common = dict(sizing=sz, transaction_cost=tc, sizing_risk=dv01)
    reb, sig, rl = spec["rebalance"], spec["signal"], spec["risk_limits"]
    notes = []
    if notional is not None:
        notes.append(f"sizing notional: notional_amount = {notional} on every instrument that sets it")

    series = None
    if arch in ("mean_reversion", "momentum"):
        series = signal_series(spec)
        lag = sig.get("lag") or 0
        if lag:
            series = series.shift(lag).dropna()
            notes.append(f"signal.lag {lag}: the signal series is shifted by {lag} observations (robustness run)")

    if arch == "periodic_roll":
        parts = periodic_roll(primary, frequency=reb["frequency"], end=d["end"], trade_duration=reb["trade_duration"], **common)
    elif arch == "mean_reversion":
        parts = mean_reversion(primary, series, z_entry=sig["params"]["z_entry"], lookback=sig["lookback"],
                               transaction_cost=tc)
    elif arch == "momentum":
        threshold = sig["params"].get("threshold_bp")
        if threshold is None:
            threshold = 0.0
            notes.append("signal.params.threshold_bp not set: 0.0 used (any rise -> primary, any fall -> opposite)")
        parts = momentum(primary, series, lookback=sig["lookback"], threshold_bp=threshold,
                         frequency=reb["frequency"], end=d["end"], trade_duration=reb["trade_duration"], **common)
    elif arch == "curve_trade":
        parts = curve_trade(primary, insts["second"], frequency=reb["frequency"], end=d["end"],
                            trade_duration=reb["trade_duration"], **common)
    elif arch == "delta_hedged":
        parts = delta_hedged(primary, insts["hedge"], start=d["start"], end=d["end"], frequency=reb["frequency"],
                             risk=dv01, **common)
    elif arch == "risk_band":
        pct = rl.get("hedge_risk_percentage", 100)  # optional, not in the template: % of the book risk each hedge removes
        parts = risk_band(primary, insts["hedge"], max_abs_dv01=rl["max_abs_dv01"], frequency=reb["frequency"],
                          end=d["end"], trade_duration=reb["trade_duration"], risk=dv01, risk_percentage=pct, **common)
    elif arch == "event":
        parts = event(primary, spec["event_dates"], trade_duration=reb["trade_duration"], **common)
    else:  # validate_spec rejects anything else
        raise ValueError(arch)

    triggers = list(parts.triggers)
    notes += parts.notes
    if rl.get("stop_loss_mtm"):
        overlay = stop_loss_overlay(rl["stop_loss_mtm"], price_measure=with_ccy(Price, ccy))
        triggers += overlay.triggers
        notes += overlay.notes
    for k in ("max_positions", "max_drawdown"):
        if rl.get(k):
            notes.append(f"risk_limits.{k} is not enforced by this recipe (report/review only)")

    rate = spec["financing"].get("cash_accrual_rate") or 0.0
    strategy = Strategy(None, triggers, cash_accrual=ConstantCashAccrualModel(rate) if rate else None)
    if rate:
        notes.append(f"cash accrues at {rate} a year (ConstantCashAccrualModel, ACT/365 daily compounding)")
    notes.append(f"costs: {spec['costs']['model']} level {spec['costs'].get('level')} per side "
                 f"({type(tc).__name__}); applied to entries, exits and hedges")

    risks = [specmod.parse_risk(r) for r in spec.get("risks_to_report") or []]
    if sz["method"] == "dv01_target" or arch in ("curve_trade", "delta_hedged", "risk_band"):
        risks.append(BOOK_DV01)  # the sizing measure is not added to the results automatically
    # run_backtest de-duplicates its risks BEFORE the result_ccy rewrite, so a bare measure whose
    # currency form a trigger/hedge already carries (strategy.risks) would appear twice: drop it here.
    risks = [r for r in dict.fromkeys(risks) if with_ccy(r, ccy) not in strategy.risks or with_ccy(r, ccy) == r]
    run_kwargs = dict(start=d["start"], end=d["end"], frequency=d["frequency"], risks=risks,
                      holiday_calendar=d.get("holiday_calendar") or None, result_ccy=ccy,
                      initial_value=spec.get("initial_value") or 0, show_progress=False)
    return Built(strategy, run_kwargs, parts.signal, notes, spec)


def run(spec: Union[dict, str, Path]) -> "tuple[Any, Built]":
    """build(spec), then GenericEngine().run_backtest(built.strategy, **built.run_kwargs)."""
    built = build(spec)  # registers the spec's assets: PricebtSession.use(assets=..., fx=...)
    return GenericEngine().run_backtest(built.strategy, **built.run_kwargs), built


# ------------------------------------------------------------------------------------ describe


def _short(v: Any) -> str:
    if isinstance(v, Instrument):
        return f"{type(v).__name__}({v.name})"
    if isinstance(v, (pd.Series, GenericDataSource)):
        return type(v).__name__
    if hasattr(v, "priceables") and not isinstance(v, Action):  # Portfolio
        return "[" + ", ".join(_short(p) for p in v.priceables) + "]"
    if isinstance(v, (list, tuple)):
        return f"[{len(v)} items]" if len(v) > 4 else "[" + ", ".join(_short(x) for x in v) + "]"
    return v.name if isinstance(v, Enum) else repr(v)


def _fields(obj: Any, keep: Iterable[str]) -> str:
    return ", ".join(f"{f.name}={_short(getattr(obj, f.name))}" for f in dataclasses.fields(obj)
                     if f.name in keep and getattr(obj, f.name, None) is not None)


_REQ_FIELDS = ("frequency", "start_date", "end_date", "dates", "trigger_level", "direction", "risk", "z_score_bound",
               "rolling_mean_window", "aggregate_type")
_ACTION_FIELDS = ("priceables", "trade_duration", "scaling_type", "scaling_risk", "scaling_level", "risk",
                  "risk_percentage", "transaction_cost")


def _req(req: Any) -> str:
    if isinstance(req, AggregateTriggerRequirements):
        return f"AggregateTriggerRequirements({req.aggregate_type.name}: " + "; ".join(_req(t) for t in req.triggers) + ")"
    return f"{type(req).__name__}({_fields(req, _REQ_FIELDS)})"


def describe(built: Built) -> str:
    """A plain-text list of exactly which gs constructs the strategy uses (for the report)."""
    s = built.strategy
    lines = ["Strategy(initial_portfolio=None, triggers=[...]"
             + (f", cash_accrual={s.cash_accrual!r})" if s.cash_accrual else ")")]
    for t in s.triggers:
        lines.append(f"- {type(t).__name__}: {_req(t.trigger_requirements)}")
        for a in t.actions:
            lines.append(f"    -> {type(a).__name__}(name={a.name}, {_fields(a, _ACTION_FIELDS)})")
    rk = {k: (_short(v) if k == "risks" else v) for k, v in built.run_kwargs.items()}
    lines.append(f"GenericEngine().run_backtest(strategy, {', '.join(f'{k}={v}' for k, v in rk.items())})")
    if built.signal is not None:
        lines.append(f"signal: {len(built.signal)} points, {built.signal.index[0]}..{built.signal.index[-1]}")
    lines.append("modelling notes:")
    lines += [f"- {n}" for n in built.notes]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("describe", "run"))
    ap.add_argument("spec")
    ap.add_argument("--out", help="run: write result_summary to this CSV")
    args = ap.parse_args(argv)
    if args.cmd == "describe":
        print(describe(build(args.spec)))
        return 0
    bt, built = run(args.spec)
    print(describe(built))
    print(bt.result_summary.tail())
    if args.out:
        bt.result_summary.to_csv(args.out)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
