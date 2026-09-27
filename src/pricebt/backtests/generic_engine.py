"""gs_quant `GenericEngine` over the pricebt engine. The market comes from `PricebtSession.use(market=...)` (or `GenericEngine(market=...)`).

`run_backtest` keeps gs's signature. What each parameter does here:

| parameter                | pricebt behaviour                                                                                                   |
|--------------------------|---------------------------------------------------------------------------------------------------------------------|
| strategy                 | a `pricebt.backtests.strategy.Strategy` (pricebt Strategy)                                                          |
| start / end / frequency  | `TimeGrid.daily(start, end, frequency)` with gs RelativeDate rules ('1b','1w','1m','3m','1y'; intraday '15min','1h') on the session calendar |
| states                   | explicit dates / datetimes (override start/end/frequency)                                                           |
| risks                    | recorded every point: scalar measures via EngineSettings.measures, ladders via EngineSettings.vector_measures; the strategy's own risks (hedge / trigger measures) are added, like gs |
| show_progress            | one tqdm bar                                                                                                        |
| initial_value            | EngineSettings.initial_capital (part of Cumulative Cash and Total)                                                  |
| result_ccy               | None / 'USD' only (NotSupportedError otherwise)                                                                    |
| holiday_calendar         | extra holidays added to the session calendar                                                                        |
| csa_term, market_data_location | forwarded to your market as request keys ('csa_term', 'market_data_location'); providers may use or ignore them |
| visible_to_gs, is_batch  | accepted and ignored (GS request plumbing)                                                                          |
| calc_risk_at_trade_exits | False only (True raises NotSupportedError: use P&L layers / BacktestResult.attribution())                          |
| pnl_explain              | None only (a PnlDefinition raises NotSupportedError: use P&L layers)                                                |
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from ..common import check_currency
from ..engine import Engine, EngineSettings
from ..errors import ConfigError, NotSupportedError
from ..results import BacktestResult
from ..risk import Price, RiskMeasure, as_risk_measure
from ..session import PricebtSession, current_session
from ..strategy.actions import Action
from ..strategy.signals import SignalStore
from ..timeutil import TimeContext, TimeGrid, parse_tenor
from ..types import as_list
from .backtest_objects import Backtest

__all__ = ["GenericEngine"]


def _as_state(x: Any, ctx: TimeContext) -> pd.Timestamp:
    """A date -> that date's daily time; a naive midnight datetime/Timestamp is a date (gs states are dates); other datetimes are localised."""
    if isinstance(x, (pd.Timestamp, dt.datetime)):
        ts = pd.Timestamp(x)
        if ts.tzinfo is None and (ts.hour, ts.minute, ts.second, ts.microsecond) == (0, 0, 0, 0):
            return ctx.at(ts.date())
        return ctx.localize(ts)
    return ctx.at(x)


class GenericEngine:
    """gs `GenericEngine(action_impl_map=None, price_measure=Price)` plus `market=` / `session=` (else the PricebtSession default)."""

    def __init__(self, action_impl_map: Any = None, price_measure: Any = Price, *, market: Any = None, session: Optional[PricebtSession] = None,
                 **session_kw: Any):
        if action_impl_map:
            raise NotSupportedError("action_impl_map (custom gs action handlers) is not supported: write a pricebt CustomAction or subclass an action")
        if as_risk_measure(price_measure).measure != "pv":
            raise NotSupportedError(f"price_measure must be Price/DollarPrice ('pv'), got {price_measure!r}")
        if session is not None and market is not None:
            raise ConfigError("give either market= or session=, not both", code="SESSION")
        if market is None and session_kw:
            raise ConfigError(f"{sorted(session_kw)} need market= (or configure them on PricebtSession.use)", code="SESSION")
        self.price_measure = price_measure
        self.action_impl_map: Dict[Any, Any] = {}
        self._session = session if session is not None else (PricebtSession(market, **session_kw) if market is not None else None)

    @property
    def session(self) -> PricebtSession:
        s = self._session or current_session()
        if s is None:
            raise ConfigError("no market: call PricebtSession.use(market=<MarketDataProvider>, calendar=...) once, or GenericEngine(market=...)", code="SESSION")
        return s

    def supports_strategy(self, strategy: Any) -> bool:
        return all(isinstance(a, Action) for t in getattr(strategy, "triggers", ()) for a in t.actions)

    # ------------------------------------------------------------------ run
    def run_backtest(
        self,
        strategy: Any,
        start: Optional[Any] = None,
        end: Optional[Any] = None,
        frequency: Optional[str] = "1m",
        states: Optional[Iterable[Any]] = None,
        risks: Optional[Iterable[Any]] = None,
        show_progress: bool = True,
        csa_term: Optional[str] = None,
        visible_to_gs: bool = False,
        initial_value: float = 0,
        result_ccy: Optional[Any] = None,
        holiday_calendar: Optional[Iterable[Any]] = None,
        market_data_location: Optional[str] = None,
        is_batch: bool = True,
        calc_risk_at_trade_exits: bool = False,
        pnl_explain: Optional[Any] = None,
    ) -> Backtest:
        """Run `strategy` over the grid built from (start, end, frequency) or `states`; returns a gs-style `Backtest` (see module docstring)."""
        if calc_risk_at_trade_exits:
            raise NotSupportedError("calc_risk_at_trade_exits: pricebt attributes P&L with layers instead (EngineSettings.layers, BacktestResult.attribution())")
        if pnl_explain is not None:
            raise NotSupportedError("pnl_explain: pricebt attributes P&L with layers instead (EngineSettings.layers, BacktestResult.attribution())")
        session = self.session
        if result_ccy is not None:
            check_currency(result_ccy, "run_backtest(result_ccy=...)", session=session)
        ctx = session.time_context(holiday_calendar)
        grid = self._grid(ctx, start, end, frequency, states)
        scalars, vectors, labels = self._risks(risks, strategy)
        request = {k: v for k, v in (("csa_term", csa_term), ("market_data_location", market_data_location)) if v is not None}
        settings = self._settings(session, strategy, initial_value, show_progress, scalars, vectors)
        signals = SignalStore.for_strategy(strategy) if hasattr(strategy, "required_signals") and strategy.required_signals() else None
        record = Engine(grid, session.market_data(request), strategy, settings, signals=signals).run()
        result = BacktestResult(record)
        bt = Backtest(strategy, [], labels, self.price_measure, result, holiday_calendar=holiday_calendar)
        bt.states = list(bt.result_summary.index)
        return bt

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _grid(ctx: TimeContext, start: Any, end: Any, frequency: Optional[str], states: Optional[Iterable[Any]]) -> TimeGrid:
        if states is not None:
            pts = [_as_state(s, ctx) for s in states]
            if not pts:
                raise ConfigError("states is empty", code="GRID")
            return TimeGrid(pts, ctx)
        if start is None or end is None:
            raise ConfigError("run_backtest needs start and end (or states)", code="GRID")
        freq = str(frequency or "1m").lower()
        _, unit = parse_tenor(freq)
        grid = TimeGrid.intraday(start, end, freq, ctx) if unit in ("min", "h", "s") else TimeGrid.daily(start, end, freq, ctx)
        if len(grid) == 0:
            raise ConfigError(f"no business days between {start} and {end} at frequency {frequency!r}", code="GRID")
        return grid

    @staticmethod
    def _risks(risks: Any, strategy: Any) -> Tuple[Tuple[str, ...], Tuple[str, ...], List[Any]]:
        """(scalar measure names, vector measure names, result_summary labels). Requested risks first, then the strategy's own (gs adds
        strategy.risks too); deduplicated by pricebt measure name; the price measure is the 'Price' column."""
        seen: Dict[str, Any] = {}
        for r in [*as_list(risks), *getattr(strategy, "risks", ())]:
            rm = as_risk_measure(r)
            if rm.is_price or rm.measure in seen:
                continue
            seen[rm.measure] = r if isinstance(r, RiskMeasure) else rm
        labels = [Price, *seen.values()]
        scal = tuple(n for n, r in seen.items() if not as_risk_measure(r).vector)
        vec = tuple(n for n, r in seen.items() if as_risk_measure(r).vector)
        return scal, vec, labels

    @staticmethod
    def _settings(session: PricebtSession, strategy: Any, initial_value: float, show_progress: bool, scalars: Tuple[str, ...],
                  vectors: Tuple[str, ...]) -> EngineSettings:
        names = {f.name for f in dataclasses.fields(EngineSettings)}
        kw: Dict[str, Any] = {"name": getattr(strategy, "name", "backtest"), "initial_capital": float(initial_value), "show_progress": bool(show_progress),
                              "measures": scalars, "vector_measures": vectors}
        kw.update(session.settings)
        unknown = sorted(k for k in session.settings if k not in names)
        if unknown:
            raise ConfigError(f"unknown EngineSettings fields in the session settings: {unknown}", code="SESSION")
        return EngineSettings(**{k: v for k, v in kw.items() if k in names})
