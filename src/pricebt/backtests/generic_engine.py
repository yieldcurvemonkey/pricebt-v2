"""
Copyright 2019 Goldman Sachs.
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
"""
# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: DEV-E1, DEV-E2, DEV-E3,
# DEV-E4, DEV-E8, DEV-E11, DEV-E13, DEV-E14, DEV-E15, DEV-E16, DEV-T2, DEV-T4, DEV-T10, DEV-T11,
# DEV-T16, DEV-R1
#
# GenericEngine, GenericEngineActionFactory (DESIGN.md section 9.3): the integration point that
# calls every other backtests/ module. gs's server-only tracing (`Tracer`/`self._trace`) and
# `new_pricing_context()` (websocket batching/priority/market-data-location) are deleted (DESIGN.md
# section 9.1 step 3): pricebt has no server to batch requests to, so `run_backtest`'s ambient
# `csa_term` becomes one plain `PricingContext(csa_term=...)` wrapping the whole run instead.
from __future__ import annotations

import datetime as dt
import logging
import warnings
from collections import defaultdict
from functools import reduce
from typing import Iterable, Optional, Union

from ..common import Currency, ParameterisedRiskMeasure, RiskMeasure
from ..datetime.relative_date import RelativeDateSchedule
from ..errors import ConfigError, MarketDataUnavailable, PricebtError
from ..instrument import Instrument
from ..markets import HistoricalPricingContext, PricingContext
from ..markets.portfolio import Portfolio
from ..progress import ProgressBar
from ..risk import Price
from ..risk.results import PortfolioRiskResult
from ..session import PricebtSession
from .action_handler import ActionHandler, ActionHandlerBaseFactory
from .actions import (
    Action,
    AddScaledTradeAction,
    AddTradeAction,
    AddWeightedTradeAction,
    EarlyExitPositionLimitScaledAction,
    ExitAllPositionsAction,
    ExitTradeAction,
    HedgeAction,
    RebalanceAction,
)
from .backtest_engine import BacktestBaseEngine
from .backtest_objects import _BACKTEST_END, _RESULT_CCY, BackTest, CashPayment, PnlDefinition
from .backtest_utils import CalcType, clear_final_date_cache, get_final_date, make_list
from .generic_engine_action_impls import (
    AddScaledTradeActionImpl,
    AddTradeActionImpl,
    AddWeightedTradeActionImpl,
    EarlyExitPositionLimitScaledActionImpl,
    ExitTradeActionImpl,
    HedgeActionImpl,
    RebalanceActionImpl,
    _roll_to_market_date,
    remap_trigger_info,
)
from .strategy import Strategy
from .triggers import AggregateTriggerRequirements, NotTriggerRequirements, PeriodicTriggerRequirements

logger = logging.getLogger(__name__)


def raiser(ex):
    raise RuntimeError(ex)


# ---------------------------------------------------------------------------------------------
# pricebt helpers: DEV-T4 (Periodic _backtest_start), and DESIGN.md section 9.5's missing-market
# handling (steps 1-4 collect/check/apply/recompute; step 6's off-grid rolling lives in
# generic_engine_action_impls._roll_to_market_date, called from both files).
# ---------------------------------------------------------------------------------------------


def _walk_requirements(req):
    """Yield `req` and, for Aggregate/Not, every nested child requirement (DEV-T4/DEV-T10's
    "walking Aggregate/Not children")."""
    yield req
    if isinstance(req, AggregateTriggerRequirements):
        for child in req.triggers:
            yield from _walk_requirements(child)
    elif isinstance(req, NotTriggerRequirements):
        yield from _walk_requirements(req.trigger)


def _set_periodic_backtest_start(req, start: dt.date) -> None:
    """pricebt DEV-T4: set `_backtest_start` on every PeriodicTriggerRequirements with
    `start_date is None`, walking Aggregate/Not children. If the requirement already computed its
    schedule under a DIFFERENT anchor (e.g. before DESIGN.md section 9.5's missing-market drop
    moved the start), reset it so the next `get_trigger_times()`/`has_triggered()` call rebuilds
    the schedule from the new anchor."""
    for r in _walk_requirements(req):
        if isinstance(r, PeriodicTriggerRequirements) and r.start_date is None:
            if r._backtest_start != start:
                r._backtest_start = start
                r.reset()


def _flatten_instruments(value) -> "list[Instrument]":
    """A priceables-shaped value (an Instrument, a Portfolio, or a list of either) -> a flat list
    of Instrument leaves, for DESIGN.md section 9.5 step 1's tradable-asset collection."""
    out: "list[Instrument]" = []
    for item in make_list(value):
        if isinstance(item, Portfolio):
            out.extend(item.all_instruments)
        else:
            out.append(item)
    return out


def _collect_tradable(strategy: Strategy):
    """DESIGN.md section 9.5 step 1: the initial-portfolio instruments (list or dict form) and
    every action's priceables/priceable/dated_priceables, flattening Portfolios. Returns
    `(insts, hedge_pairs)`, where `hedge_pairs` is `[(instrument, action.csa_term), ...]` for every
    HedgeAction leg (step 2's extra hedge-csa check)."""
    insts: "list[Instrument]" = []
    hedge_pairs: "list[tuple[Instrument, Optional[str]]]" = []

    ip = strategy.initial_portfolio
    if isinstance(ip, dict):
        for v in ip.values():
            insts.extend(_flatten_instruments(v))
    else:
        insts.extend(_flatten_instruments(ip))

    for trigger in strategy.triggers:
        for action in trigger.actions:
            if isinstance(action, HedgeAction):
                legs = list(action.priceables.all_instruments)
                insts.extend(legs)
                hedge_pairs.extend((leg, action.csa_term) for leg in legs)
            elif isinstance(action, RebalanceAction):
                if action.priceable is not None:
                    insts.append(action.priceable)
            elif hasattr(action, "priceables"):
                insts.extend(_flatten_instruments(action.priceables))
                for v in (getattr(action, "dated_priceables", None) or {}).values():
                    insts.extend(_flatten_instruments(v))

    return insts, hedge_pairs


def _check_missing_market(session, insts, hedge_pairs, dates, run_csa) -> "list[tuple[str, dt.date]]":
    """DESIGN.md section 9.5 step 2: for each grid date and each tradable asset, check
    `pricing.has_market`; for a HedgeAction's asset, also check under its own `csa_term`. Returns
    the sorted, de-duplicated list of (asset name, date) pairs with no market."""
    missing: "set[tuple[str, dt.date]]" = set()
    checked: "set[tuple[str, dt.date, Optional[str]]]" = set()

    def check(inst, csa):
        asset = session.pricing.asset_for(inst)
        for d in dates:
            key = (asset.name, d, csa)
            if key in checked:
                continue
            checked.add(key)
            if not session.pricing.has_market(asset, d, csa):
                missing.add((asset.name, d))

    for inst in insts:
        check(inst, run_csa)
    for inst, csa in hedge_pairs:
        check(inst, csa)

    return sorted(missing)


def _apply_missing_market_policy(
    session, strategy: Strategy, dates: "list[dt.date]", start: dt.date, end: dt.date, run_csa
):
    """DESIGN.md section 9.5 steps 1-4 / decision 0.4. Returns `(dates, start, end, dropped)`,
    recomputed after a 'drop' (`dropped` is `[]` when nothing was), or raises under 'raise' or when
    every grid date would be dropped either way."""
    insts, hedge_pairs = _collect_tradable(strategy)
    missing = _check_missing_market(session, insts, hedge_pairs, dates, run_csa)
    if not missing:
        return dates, start, end, []

    missing_dates = sorted({d for _a, d in missing})
    policy = session.missing_market

    if policy == "raise":
        shown = ", ".join(f"({a}, {d})" for a, d in missing[:10])
        raise MarketDataUnavailable(
            missing[0][0], missing[0][1], run_csa, reason=f"{len(missing)} missing (asset, date) pairs: {shown}"
        )

    kept = sorted(set(dates) - set(missing_dates))
    if not kept:
        first_asset, _first_date = missing[0]
        raise MarketDataUnavailable(
            first_asset, dates[0], run_csa, reason=f"no market data on any of the {len(dates)} grid dates"
        )

    warnings.warn(
        f"pricebt: dropped {len(missing_dates)} dates with no market data "
        f"({missing_dates[0]}..{missing_dates[-1]}); see backtest.missing_market_dates"
    )
    return kept, kept[0], kept[-1], missing_dates


def _remap_dict_initial_portfolio(session, initial_portfolio: dict, kept_dates: "list[dt.date]", end: dt.date) -> dict:
    """DESIGN.md section 9.5 step 4's last bullet: a dict-form initial-portfolio key with no
    market for its instrument(s) moves to the first KEPT grid date >= that key; the entry is
    dropped if there is no such date <= `end`."""
    remapped: dict = {}
    kept_sorted = sorted(kept_dates)
    for key, value in initial_portfolio.items():
        target = key
        for inst in _flatten_instruments(value):
            asset = session.pricing.asset_for(inst)
            if not session.pricing.has_market(asset, target, None):
                moved = next((d for d in kept_sorted if d >= key), None)
                target = moved if moved is not None and moved <= end else None
            if target is None:
                break
        if target is not None:
            remapped.setdefault(target, [])
            remapped[target] = make_list(remapped[target]) + make_list(value)
    return remapped


class GenericEngineActionFactory(ActionHandlerBaseFactory):
    def __init__(self, action_impl_map=None):
        self.action_impl_map = {
            AddTradeAction: AddTradeActionImpl,
            HedgeAction: HedgeActionImpl,
            ExitTradeAction: ExitTradeActionImpl,
            ExitAllPositionsAction: ExitTradeActionImpl,
            RebalanceAction: RebalanceActionImpl,
            AddScaledTradeAction: AddScaledTradeActionImpl,
            AddWeightedTradeAction: AddWeightedTradeActionImpl,
            EarlyExitPositionLimitScaledAction: EarlyExitPositionLimitScaledActionImpl,
        } | (action_impl_map or {})

    def get_action_handler(self, action: Action) -> ActionHandler:
        if type(action) in self.action_impl_map:
            return self.action_impl_map[type(action)](action)
        raise RuntimeError(f"Action {type(action)} not supported by engine")


class GenericEngine(BacktestBaseEngine):
    def __init__(self, action_impl_map=None, price_measure=Price):
        self.action_impl_map = {} if action_impl_map is None else action_impl_map
        self.price_measure = price_measure

    def get_action_handler(self, action: Action) -> ActionHandler:
        handler_factory = GenericEngineActionFactory(self.action_impl_map)
        return handler_factory.get_action_handler(action)

    def supports_strategy(self, strategy):
        all_actions = reduce(lambda x, y: x + y, (map(lambda x: x.actions, strategy.triggers)))
        try:
            for x in all_actions:
                self.get_action_handler(x)
        except RuntimeError:
            return False
        return True

    def run_backtest(
        self,
        strategy: Strategy,
        start: Optional[dt.date] = None,
        end: Optional[dt.date] = None,
        frequency: Optional[str] = "1m",
        states: Optional[Iterable[dt.date]] = None,
        risks: Optional[Iterable[RiskMeasure]] = None,
        show_progress: bool = True,
        csa_term: Optional[str] = None,
        visible_to_gs: bool = False,
        initial_value: float = 0,
        result_ccy: Optional[Union[str, Currency]] = None,
        holiday_calendar: Optional[str] = None,
        market_data_location: Optional[str] = None,
        is_batch: bool = True,
        calc_risk_at_trade_exits: bool = False,
        pnl_explain: Optional[PnlDefinition] = None,
    ):
        """
        run the backtest following the triggers and actions defined in the strategy.  If states are entered run on
        those dates otherwise build a schedule from the start, end, frequency
        using pricebt.datetime.relative_date.RelativeDateSchedule
        :param strategy: the strategy object
        :param start: a datetime
        :param end: a datetime
        :param frequency: str, default '1m'
        :param states: a list of dates will override the start, end, freq if provided
        :param risks: risks to run
        :param show_progress: boolean default true
        :param csa_term: the csa term to use
        :param visible_to_gs: accepted for signature parity; not used (no GS server, DESIGN.md MUST-1)
        :param initial_value: initial cash value of strategy defaults to 0
        :param result_ccy: ccy of all risks, pvs and cash
        :param holiday_calendar for date maths - list of dates
        :param market_data_location: accepted for signature parity; not used
        :param is_batch: accepted for signature parity; not used
        :param calc_risk_at_trade_exits: separate results for requested risk measures on tradable exit dates;
                                         not to be included in main results but useful for PnL decomposition
        :param pnl_explain: a Pnl Definition object which defines the risk attribution and mkt data for a pnl explain
        :return: a backtest object containing the portfolios on each day and results which show all risks on all days

        """
        logger.info(f"Starting Backtest: Building Date Schedule - {dt.datetime.now()}")

        # pricebt DEV-T16: run_backtest's own holiday_calendar, normalised to a tuple here (a list
        # is unhashable and crashes get_final_date's cache key).
        if isinstance(holiday_calendar, list):
            holiday_calendar = tuple(holiday_calendar)

        # pricebt: gs's ambient csa_term flows through new_pricing_context() (a server-batching
        # wrapper this port has no use for, DESIGN.md section 9.1 step 3). Here it is simply the
        # ambient PricingContext for the whole run (DESIGN.md section 9.4's csa_term handling).
        with PricingContext(csa_term=csa_term):
            return self.__run(
                strategy,
                start,
                end,
                frequency,
                states,
                risks,
                initial_value,
                result_ccy,
                holiday_calendar,
                calc_risk_at_trade_exits,
                pnl_explain,
                show_progress,
            )

    def __run(
        self,
        strategy,
        start,
        end,
        frequency,
        states,
        risks,
        initial_value,
        result_ccy,
        holiday_calendar,
        calc_risk_at_trade_exits,
        pnl_explain,
        show_progress,
    ):
        """
        Run the backtest strategy using the ambient pricing context
        """
        session = PricebtSession.current
        if session is None:
            raise PricebtError("no PricebtSession: call PricebtSession.use(assets=[...]) first")

        # pricebt DEV-T2: gs's get_final_date cache is a module global that otherwise leaks across
        # backtest runs (and across tests). Clear it, and the pricing caches, at the start of every run.
        clear_final_date_cache()
        session.pricing.reset()

        # pricebt DEV-T10: reset every trigger requirement (walks Aggregate/Not children
        # internally), so re-running the same Strategy object starts fresh every time.
        for trigger in strategy.triggers:
            trigger.trigger_requirements.reset()

        # The ambient run csa (run_backtest's own csa_term, or inherited): __run executes entirely
        # inside run_backtest's `with PricingContext(csa_term=csa_term):`.
        run_csa = PricingContext.current.csa_term

        # --- Phase 1: date grid -----------------------------------------------------------------
        strategy_pricing_dates = (
            RelativeDateSchedule(frequency.lower(), start, end).apply_rule(holiday_calendar=holiday_calendar)
            if states is None
            else states
        )
        strategy_pricing_dates.sort()

        strategy_start_date = strategy_pricing_dates[0]
        strategy_end_date = strategy_pricing_dates[-1]

        # pricebt DEV-T4: set _backtest_start on every Periodic requirement with start_date is
        # None, BEFORE the get_trigger_times() loop below.
        for trigger in strategy.triggers:
            _set_periodic_backtest_start(trigger.trigger_requirements, strategy_start_date)

        for trigger in strategy.triggers:
            strategy_pricing_dates += [
                t for t in trigger.get_trigger_times() if strategy_start_date <= t <= strategy_end_date
            ]

        strategy_pricing_dates = sorted(set(strategy_pricing_dates))

        # pricebt DESIGN.md section 9.5 steps 1-4 (decision 0.4): drop or raise on grid dates with
        # no market for a tradable asset, then recompute start/end and re-anchor _backtest_start.
        strategy_pricing_dates, strategy_start_date, strategy_end_date, dropped_dates = _apply_missing_market_policy(
            session, strategy, strategy_pricing_dates, strategy_start_date, strategy_end_date, run_csa
        )
        for trigger in strategy.triggers:
            _set_periodic_backtest_start(trigger.trigger_requirements, strategy_start_date)

        if isinstance(strategy.initial_portfolio, dict):
            strategy.initial_portfolio = _remap_dict_initial_portfolio(
                session, strategy.initial_portfolio, strategy_pricing_dates, strategy_end_date
            )

        if pnl_explain is not None:
            calc_risk_at_trade_exits = True
            pnl_risks = pnl_explain.get_risks()
        else:
            pnl_risks = []
        # pricebt DEV-E14: ordered de-duplication (user risks, then strategy.risks, then pnl
        # risks, then the price measure), not gs's `set(...)`, whose column order is non-deterministic.
        risks = list(dict.fromkeys([*make_list(risks), *strategy.risks, *pnl_risks, self.price_measure]))
        if result_ccy is not None:
            # pricebt DEV-E15: gs's ONLY multi-currency mechanism is server-side result_ccy
            # conversion. Here the same rewrite (every risk becomes r(currency=result_ccy)) instead
            # drives REAL conversion through PricingService.fx (P2.2, the asset's FX config) --
            # otherwise the gs errors below apply unchanged (DESIGN.md section 7 point 4's hint
            # lives on BackTest.result_summary, backtest_objects.py).
            risks = [
                (
                    r(currency=result_ccy)
                    if isinstance(r, ParameterisedRiskMeasure)
                    else raiser(f"Unparameterised risk: {r}")
                )
                for r in risks
            ]

        if result_ccy is not None:
            if isinstance(self.price_measure, ParameterisedRiskMeasure):
                price_risk = self.price_measure(currency=result_ccy)
            else:
                raiser(f"Unparameterised price measure: {self.price_measure}")
        else:
            price_risk = self.price_measure

        backtest = BackTest(strategy, strategy_pricing_dates, risks, price_risk, holiday_calendar, pnl_explain)
        backtest.missing_market_dates = dropped_dates

        token = _BACKTEST_END.set(strategy_end_date)
        # pricebt DEV-E18: see backtest_objects.py -- ScaledTransactionModel.get_unit_cost reads
        # this to rewrite a RiskMeasure scaling_type to result_ccy, mirroring the `risks` rewrite
        # above for the primary risks list.
        ccy_token = _RESULT_CCY.set(result_ccy)
        try:
            logger.info("Resolving initial portfolio")
            self._resolve_initial_portfolio(
                strategy.initial_portfolio, backtest, strategy_start_date, strategy_pricing_dates, holiday_calendar
            )

            logger.info("Building simple and semi-deterministic triggers and actions")
            self._build_simple_and_semi_triggers_and_actions(strategy, backtest, strategy_pricing_dates)

            logger.info(f"Filtering strategy calculations to run from {strategy_start_date} to {strategy_end_date}")
            backtest.portfolio_dict = defaultdict(
                Portfolio,
                {
                    k: backtest.portfolio_dict[k]
                    for k in backtest.portfolio_dict
                    if strategy_start_date <= k <= strategy_end_date
                },
            )
            backtest.hedges = defaultdict(
                list, {k: backtest.hedges[k] for k in backtest.hedges if strategy_start_date <= k <= strategy_end_date}
            )
            backtest.weighted_trades = defaultdict(
                list,
                {
                    k: backtest.weighted_trades[k]
                    for k in backtest.weighted_trades
                    if strategy_start_date <= k <= strategy_end_date
                },
            )

            with ProgressBar(total=0, desc="pricebt", show=show_progress) as bar:
                logger.info("Pricing simple and semi-deterministic triggers and actions")
                bar.add_total(len(backtest.portfolio_dict))
                self._price_semi_det_triggers(backtest, risks, bar)

                logger.info(
                    "Scaling semi-determ triggers and actions and calculating path dependent triggers and actions"
                )
                bar.add_total(len(strategy_pricing_dates))
                for d in strategy_pricing_dates:
                    self._process_triggers_and_actions_for_date(d, strategy, backtest, risks)
                    bar.update(1)

                self._calc_new_trades(backtest, risks)

                cash_walk_dates = sorted(set(strategy_pricing_dates + list(backtest.cash_payments.keys())))
                bar.add_total(len(cash_walk_dates))
                self._handle_cash(
                    backtest,
                    risks,
                    price_risk,
                    strategy_pricing_dates,
                    strategy_end_date,
                    initial_value,
                    calc_risk_at_trade_exits,
                    strategy.cash_accrual,
                    bar,
                )

            backtest.transaction_costs = {
                d: -sum(tce.get_final_cost() for tce in tce_list)
                for d, tce_list in backtest.transaction_cost_entries.items()
            }

            logger.info(f"Finished Backtest:- {dt.datetime.now()}")
            return backtest
        finally:
            _BACKTEST_END.reset(token)
            _RESULT_CCY.reset(ccy_token)

    def _resolve_initial_portfolio(
        self, initial_portfolio, backtest, strategy_start_date, strategy_pricing_dates, holiday_calendar, duration=None
    ):
        if isinstance(initial_portfolio, dict):
            sorted_dates = sorted(initial_portfolio.keys())
            for i, d in enumerate(sorted_dates):
                portfolio = make_list(initial_portfolio[d])
                end_date = sorted_dates[i + 1] if i + 1 < len(sorted_dates) else strategy_pricing_dates[-1]
                self._resolve_initial_portfolio(
                    portfolio, backtest, d, strategy_pricing_dates, holiday_calendar, end_date
                )
        else:
            if len(initial_portfolio):
                renamed_port = []
                for index in range(len(initial_portfolio)):
                    old_name = initial_portfolio[index].name
                    renamed_inst = initial_portfolio[index].clone(
                        name=f"{old_name}_{strategy_start_date.strftime('%Y-%m-%d')}"
                    )
                    renamed_port.append(renamed_inst)
                    entry_payment = CashPayment(renamed_inst, effective_date=strategy_start_date, direction=-1)
                    backtest.cash_payments[strategy_start_date].append(entry_payment)
                    final_date = get_final_date(renamed_inst, strategy_start_date, duration, holiday_calendar)
                    # pricebt DEV-E16: roll an off-grid final date with no market to the first kept
                    # grid date after it (DESIGN.md section 9.5 step 6).
                    final_date = _roll_to_market_date(
                        renamed_inst, final_date, PricingContext.current.csa_term, backtest, "initial portfolio"
                    )
                    # pricebt DEV-E4: gs books the exit payment against the ORIGINAL un-renamed,
                    # unresolved instrument (`initial_portfolio[index]`), adding a spurious ledger
                    # row (the server re-resolves it fresh on the exit date, e.g. as a brand-new
                    # 'ATM' swap). Use `renamed_inst` instead -- the SAME object as the entry
                    # payment's trade -- so that `init_port.resolve()` below (which mutates it in
                    # place) resolves both payments' trade to the one real position.
                    exit_payment = CashPayment(renamed_inst, effective_date=final_date)
                    backtest.cash_payments[final_date].append(exit_payment)
                init_port = Portfolio(renamed_port)
                with PricingContext(strategy_start_date):
                    init_port.resolve()
                for d in strategy_pricing_dates:
                    if duration is None or (
                        d >= strategy_start_date and (d < duration or duration == strategy_pricing_dates[-1])
                    ):
                        backtest.portfolio_dict[d].append(init_port.instruments)

    def _build_simple_and_semi_triggers_and_actions(self, strategy, backtest, strategy_pricing_dates):
        for trigger in strategy.triggers:
            if trigger.calc_type != CalcType.path_dependent:
                triggered_dates = []
                # pricebt DEV-T11: dict[ActionType, dict[date, info]] instead of gs's parallel list
                # (defaultdict(list)), which Phase 5's zip_longest re-aligns positionally -- a date
                # whose trigger produced no info_dict entry there silently shifts every later
                # date's info by one. Keying by date removes the alignment problem entirely.
                trigger_infos: dict = defaultdict(dict)
                for d in strategy_pricing_dates:
                    t_info = remap_trigger_info(trigger.has_triggered(d, backtest), backtest.states)
                    if t_info:
                        triggered_dates.append(d)
                        if t_info.info_dict:
                            for k, v in t_info.info_dict.items():
                                trigger_infos[k][d] = v

                for action in trigger.actions:
                    if action.calc_type != CalcType.path_dependent:
                        trigger_info = None
                        if type(action) in trigger_infos:
                            trigger_info = trigger_infos[type(action)]
                        else:
                            for mapped_action_type, action_trigger_info in trigger_infos.items():
                                if isinstance(action, mapped_action_type):
                                    trigger_info = action_trigger_info
                                    break
                        self.get_action_handler(action).apply_action(triggered_dates, backtest, trigger_info)

    def _price_semi_det_triggers(self, backtest, risks, bar):
        # gs parity (generic_engine.py:410): one calc_calls tick per PricingContext-batch method,
        # not per calc() -- restores calc_calls/calculations bookkeeping dropped during the port.
        backtest.calc_calls += 1
        for day, portfolio in backtest.portfolio_dict.items():
            if isinstance(day, dt.date):
                with PricingContext(day):
                    backtest.calculations += len(portfolio) * len(risks)
                    backtest.add_results(day, portfolio.calc(tuple(risks)))
            bar.update(1)

        # semi path dependent initial calc for hedges
        for _, hedge_list in backtest.hedges.items():
            scaling_list = [h.scaling_portfolio for h in hedge_list]
            for p in scaling_list:
                with HistoricalPricingContext(dates=p.dates):
                    backtest.calculations += len(risks) * len(p.dates)
                    port = p.trade if isinstance(p.trade, Portfolio) else Portfolio([p.trade])
                    p.results = port.calc(tuple(risks))

        # semi path dependent initial calc for weighted trades
        for _, weighted_trade_list in backtest.weighted_trades.items():
            for wt in weighted_trade_list:
                sp = wt.scaling_portfolio
                with HistoricalPricingContext(dates=sp.dates):
                    backtest.calculations += len(risks) * len(sp.dates) * len(sp.trades)
                    sp.results = sp.trades.calc(tuple(risks))

    @staticmethod
    def __ensure_risk_results(dates, backtest: BackTest, risks):
        port_by_date = {}
        for d in dates:
            port = []
            # pricebt DEV-E8: `backtest.results[d]` on the defaultdict silently creates a `[]`
            # entry -- `.get(d)` avoids that.
            results_d = backtest.results.get(d)
            for t in backtest.portfolio_dict[d]:
                if not results_d or t.name not in results_d.portfolio:
                    port.append(t)
            if len(port):
                port_by_date[d] = port

        if len(port_by_date):
            results_by_date = {}
            for d, port in port_by_date.items():
                with PricingContext(pricing_date=d):
                    results_by_date[d] = Portfolio(port).calc(tuple(risks))

            for d, results in results_by_date.items():
                backtest.add_results(d, results)

    def _process_triggers_and_actions_for_date(self, d, strategy, backtest: BackTest, risks):
        logger.debug(f"{d}: Processing triggers and actions")

        for trigger in strategy.triggers:
            if trigger.calc_type == CalcType.path_dependent:
                # pricebt DEV-E1: ensure the day's risk results BEFORE evaluating a path-dependent
                # trigger, not after -- gs evaluates has_triggered on stale/absent results[d].
                self.__ensure_risk_results([d], backtest, risks)
                # pricebt DEV-T11 (DESIGN §9.5 step 5): remap_trigger_info() wraps gs's bare
                # has_triggered() call (gs generic_engine.py:463) to roll a dropped grid date's
                # next_schedule forward -- see _remap_next_schedule in
                # generic_engine_action_impls.py.
                t_info = remap_trigger_info(trigger.has_triggered(d, backtest), backtest.states)
                if t_info:
                    for action in trigger.actions:
                        # gs (generic_engine.py:469) also calls this per action, after
                        # has_triggered -- ensure() is idempotent, but an earlier path-dependent
                        # action on this same trigger/date (e.g. RebalanceActionImpl) can add a new
                        # position that a later action in this loop needs fresh results for.
                        self.__ensure_risk_results([d], backtest, risks)
                        # pricebt DEV-T11: gs (generic_engine.py:460, 466-467) accumulates every
                        # fired path-dependent trigger's info for this date into one shared
                        # `defaultdict(list)` across the whole `for trigger in strategy.triggers`
                        # loop, so two different triggers producing infos for the same action type
                        # get merged -- a bug. `t_info` here is computed fresh per trigger, just
                        # above, and never accumulated across triggers, so this pulls only THIS
                        # trigger's info for the action's type.
                        trigger_info = t_info.info_dict.get(type(action)) if t_info.info_dict else None
                        self.get_action_handler(action).apply_action(d, backtest, trigger_info)
            else:
                # pricebt DEV-E2: evaluate this (simple/semi) trigger ONCE per date, not once per
                # path-dependent action -- gs calls has_triggered again for every such action.
                has_path_dependent_action = any(a.calc_type == CalcType.path_dependent for a in trigger.actions)
                # pricebt DEV-T11 (DESIGN §9.5 step 5): remap_trigger_info() wraps gs's bare
                # has_triggered() call (gs generic_engine.py:475) -- see the other call site above
                # and _remap_next_schedule in generic_engine_action_impls.py.
                t_info_cache = (
                    remap_trigger_info(trigger.has_triggered(d, backtest), backtest.states)
                    if has_path_dependent_action
                    else None
                )
                for action in trigger.actions:
                    if action.calc_type == CalcType.path_dependent:
                        if t_info_cache:
                            trigger_info = (
                                t_info_cache.info_dict.get(type(action)) if t_info_cache.info_dict else None
                            )
                            self.__ensure_risk_results([d], backtest, risks)
                            self.get_action_handler(action).apply_action(d, backtest, trigger_info)

        # explicit check needed because backtest.hedges is a defaultdict that gets populated on access below
        if d not in backtest.hedges and d not in backtest.weighted_trades:
            return
        for hedge in backtest.hedges[d]:
            sp = hedge.scaling_portfolio
            if sp.results is None:
                with HistoricalPricingContext(dates=sp.dates):
                    # gs parity (generic_engine.py:487): no matching calc_calls tick here -- gs
                    # only bumps calc_calls in the three PricingContext-batch methods.
                    backtest.calculations += len(risks) * len(sp.dates)
                    port_sp = sp.trade if isinstance(sp.trade, Portfolio) else Portfolio([sp.trade])
                    sp.results = port_sp.calc(tuple(risks))

        if backtest.hedges[d]:
            self.__ensure_risk_results([d], backtest, risks)
            # pricebt DEV-E8: `.get` avoids the defaultdict autoviv; the `d not in backtest.results`
            # check below is still safe as `not in` (a `dict.__contains__` never autovivifies), kept
            # exactly as gs has it.
            if d not in backtest.results:
                # pricebt DEV-E3 (both files): none of today's hedges will ever be sized -- remove
                # their already-booked entry/exit TCEs (created in HedgeActionImpl) instead of
                # leaving them to silently contribute a cost for a hedge that never happened.
                for hedge in backtest.hedges[d]:
                    _remove_tce(backtest, hedge.entry_payment.transaction_cost_entry)
                    if hedge.exit_payment is not None:
                        _remove_tce(backtest, hedge.exit_payment.transaction_cost_entry)
                return
            for hedge in backtest.hedges[d]:
                p = hedge.scaling_portfolio
                current_risk = (
                    backtest.results[d][p.risk]
                    .transform(risk_transformation=p.risk_transformation)
                    .aggregate(allow_mismatch_risk_keys=True)
                )
                hedge_risk = p.results[d][p.risk].transform(risk_transformation=p.risk_transformation).aggregate()
                if hedge_risk == 0:
                    # pricebt DEV-E3: see above.
                    _remove_tce(backtest, hedge.entry_payment.transaction_cost_entry)
                    if hedge.exit_payment is not None:
                        _remove_tce(backtest, hedge.exit_payment.transaction_cost_entry)
                    continue
                if current_risk.unit != hedge_risk.unit:
                    raise RuntimeError("cannot hedge in a different currency")
                scaling_factor = current_risk / hedge_risk * hedge.scaling_portfolio.risk_percentage / 100
                hedge.entry_payment.transaction_cost_entry.additional_scaling = scaling_factor
                if hedge.exit_payment is not None:
                    hedge.exit_payment.transaction_cost_entry.additional_scaling = scaling_factor
                if isinstance(p.trade, Portfolio):
                    scaled_portfolio_position = _deepcopy(p.trade)
                    scaled_portfolio_position.name = f"Scaled_{scaled_portfolio_position.name}"
                    for instrument in scaled_portfolio_position.all_instruments:
                        instrument.name = f"Scaled_{instrument.name}"

                    scale_direction = -1
                    scaled_portfolio_position.scale(scaling_factor * scale_direction)

                    for day in p.dates:
                        backtest.portfolio_dict[day] += _deepcopy(scaled_portfolio_position)

                    hedge.entry_payment.trade = _deepcopy(scaled_portfolio_position)
                    if hedge.exit_payment is not None:
                        hedge.exit_payment.trade = _deepcopy(scaled_portfolio_position)
                else:
                    raise RuntimeError("Hedge trade instrument must be a Portfolio")

                backtest.cash_payments[hedge.entry_payment.effective_date].append(hedge.entry_payment)
                if hedge.exit_payment is not None:
                    backtest.cash_payments[hedge.exit_payment.effective_date].append(hedge.exit_payment)

        if d in backtest.weighted_trades and backtest.weighted_trades[d]:
            for weighted_trade in backtest.weighted_trades[d]:
                sp = weighted_trade.scaling_portfolio
                if sp.results is None:
                    with HistoricalPricingContext(dates=sp.dates):
                        # gs parity (generic_engine.py:551): see the hedge site above.
                        backtest.calculations += len(risks) * len(sp.dates) * len(sp.trades)
                        sp.results = sp.trades.calc(tuple(risks))

                instrument_risks = {}
                for inst in sp.trades.all_instruments:
                    try:
                        inst_risk = sp.results[d][sp.risk][inst.name]
                        if hasattr(inst_risk, "aggregate"):
                            inst_risk = inst_risk.aggregate()
                        instrument_risks[inst.name] = abs(inst_risk) if inst_risk != 0 else 0
                    except (KeyError, ValueError):
                        instrument_risks[inst.name] = 0

                num_instruments = len(sp.trades.all_instruments)
                if num_instruments == 0:
                    continue

                total_portfolio_risk = sum(instrument_risks.values())
                if total_portfolio_risk == 0:
                    # pricebt DEV-E3 (both files): none of this weighted trade's instruments will
                    # ever be sized -- remove every one of their already-booked TCEs.
                    for idx, _inst in enumerate(sp.trades.all_instruments):
                        _remove_tce(backtest, weighted_trade.entry_payments[idx].transaction_cost_entry)
                        if weighted_trade.exit_payments[idx] is not None:
                            _remove_tce(backtest, weighted_trade.exit_payments[idx].transaction_cost_entry)
                    continue

                scaling_factors = {}
                for inst_name, inst_risk in instrument_risks.items():
                    if inst_risk != 0:
                        weight = inst_risk / total_portfolio_risk
                        scaling_factors[inst_name] = weight * sp.total_size
                    else:
                        scaling_factors[inst_name] = 0

                for idx, inst in enumerate(sp.trades.all_instruments):
                    scaling_factor = scaling_factors.get(inst.name, 0)
                    if scaling_factor == 0:
                        # pricebt DEV-E3: see above.
                        _remove_tce(backtest, weighted_trade.entry_payments[idx].transaction_cost_entry)
                        if weighted_trade.exit_payments[idx] is not None:
                            _remove_tce(backtest, weighted_trade.exit_payments[idx].transaction_cost_entry)
                        continue

                    # pricebt (DESIGN.md sections 5.4/9.4, DEV-I1): gs's own comment for this
                    # algorithm is explicit -- "Scaling factor = notional (since unit notional
                    # instruments)": it assumes a UNIT-notional instrument, so scaling_factor (a
                    # risk-weighted share of total_size) directly becomes the new notional under
                    # gs's field-editing scale(). pricebt instruments carry their own real declared
                    # size and scale by a quantity_ MULTIPLIER (DEV-I1), so the target size must be
                    # divided by the instrument's own unit size (its asset's size_attribute) to get
                    # that multiplier -- exactly the RebalanceActionImpl translation, applied here.
                    asset = inst.asset_config
                    if asset.size_attribute is None:
                        raise ConfigError(
                            f"AddWeightedTradeAction needs asset {asset.name!r} to declare size_attribute",
                            asset=asset.name,
                        )
                    unit_size = getattr(inst, asset.size_attribute)
                    quantity_multiplier = 0 if unit_size == 0 else scaling_factor / unit_size

                    scaled_inst = _deepcopy(inst)
                    scaled_inst.name = f"Weighted_{inst.name}"
                    scaled_inst.scale(quantity_multiplier)

                    entry_payment = weighted_trade.entry_payments[idx]
                    entry_payment.transaction_cost_entry.additional_scaling = quantity_multiplier
                    if weighted_trade.exit_payments[idx] is not None:
                        weighted_trade.exit_payments[idx].transaction_cost_entry.additional_scaling = quantity_multiplier

                    for day in sp.dates:
                        backtest.portfolio_dict[day].append(_deepcopy(scaled_inst))

                    entry_payment.trade = _deepcopy(scaled_inst)
                    if weighted_trade.exit_payments[idx] is not None:
                        weighted_trade.exit_payments[idx].trade = _deepcopy(scaled_inst)

                    backtest.cash_payments[entry_payment.effective_date].append(entry_payment)
                    if weighted_trade.exit_payments[idx] is not None:
                        backtest.cash_payments[weighted_trade.exit_payments[idx].effective_date].append(
                            weighted_trade.exit_payments[idx]
                        )

    def _calc_new_trades(self, backtest, risks):
        logger.info("Calculating and scaling newly added portfolio positions")
        # gs parity (generic_engine.py:622): see _price_semi_det_triggers.
        backtest.calc_calls += 1
        leaves_by_date = {}
        for day, portfolio in backtest.portfolio_dict.items():
            if not portfolio:
                continue
            # pricebt DEV-E8: `.get` avoids the defaultdict autoviv on a day nothing has priced yet.
            results_for_date = backtest.results.get(day)

            trades_for_date = results_for_date.portfolio if isinstance(results_for_date, PortfolioRiskResult) else []
            leaves = []
            for leaf in portfolio:
                if leaf.name not in trades_for_date:
                    logger.debug(f"{day}: new portfolio position {leaf.name} scheduled for calculation")
                    leaves.append(leaf)

            if len(leaves):
                with PricingContext(pricing_date=day):
                    leaves_by_date[day] = Portfolio(leaves).calc(tuple(risks))
                    backtest.calculations += len(leaves) * len(risks)

        logger.info("Processing results for newly added portfolio positions")
        for day, leaves in leaves_by_date.items():
            backtest.add_results(day, leaves)

    @staticmethod
    def _handle_cash(
        backtest,
        risks,
        price_risk,
        strategy_pricing_dates,
        strategy_end_date,
        initial_value,
        calc_risk_at_trade_exits,
        cash_accrual,
        bar,
    ):
        logger.info("Calculating prices for cash payments")
        # gs parity (generic_engine.py:679): see _price_semi_det_triggers.
        backtest.calc_calls += 1
        cash_results = {}
        cash_trades_by_date = defaultdict(list)
        exited_cash_trades_by_date = defaultdict(list)
        for _, cash_payments in backtest.cash_payments.items():
            for cp in cash_payments:
                trades = cp.trade.all_instruments if isinstance(cp.trade, Portfolio) else [cp.trade]
                for trade in trades:
                    if cp.effective_date and cp.effective_date <= strategy_end_date:
                        if cp.effective_date not in backtest.results or trade not in backtest.results[cp.effective_date]:
                            cash_trades_by_date[cp.effective_date].append(trade)
                            if calc_risk_at_trade_exits and cp.direction == 1:
                                exited_cash_trades_by_date[cp.effective_date].append(trade)

        for cash_date, trades in cash_trades_by_date.items():
            with PricingContext(cash_date):
                # gs parity (generic_engine.py:682): per cash_date, not multiplied by trade count --
                # this is gs's own literal (arguably undercounting) increment, ported unchanged.
                backtest.calculations += len(risks)
                cash_results[cash_date] = Portfolio(trades).calc(price_risk)
                if calc_risk_at_trade_exits and cash_date in exited_cash_trades_by_date:
                    expiring_trades = exited_cash_trades_by_date[cash_date]
                    backtest.trade_exit_risk_results[cash_date] = Portfolio(expiring_trades).calc(risks)

        # handle cash
        current_value = None
        grid_first = strategy_pricing_dates[0]
        if initial_value != 0:
            # pricebt DEV-E11 (DESIGN.md section 7 point 7): gs seeds initial_value only from the
            # FIRST cash-payment date it happens to process, under that payment's own currency, and
            # never seeds it at all if there are no payments. Seed it explicitly on D[0], before the
            # walk, in ccy0 (result_ccy, else PricebtSession.reporting_currency, else the single
            # currency of the pre-scanned tradable assets).
            ccy0 = _initial_value_ccy(backtest)
            backtest.cash_dict[grid_first] = {ccy0: initial_value}
            current_value = (backtest.cash_dict[grid_first], grid_first)
        for d in sorted(set(strategy_pricing_dates + list(backtest.cash_payments.keys()))):
            if d <= strategy_end_date:
                # pricebt DEV-R1: on a non-grid date that is NOT flat (a continuing position with a
                # final date beyond `d` is still held), also price those continuing positions for
                # every requested risk into results[d], so the row shows their true PV/risk instead
                # of an ffilled stale one (BackTest._flat_dates, backtest_objects.py).
                if d not in strategy_pricing_dates and d not in backtest.results:
                    prior_grid = [g for g in strategy_pricing_dates if g < d]
                    if prior_grid:
                        continuing = [
                            t
                            for t in backtest.portfolio_dict.get(prior_grid[-1], ())
                            if _final_date_of(t, backtest) is None or _final_date_of(t, backtest) > d
                        ]
                        if continuing:
                            with PricingContext(d):
                                backtest.add_results(d, Portfolio(continuing).calc(tuple(risks)))

                if current_value is not None:
                    backtest.cash_dict[d] = (
                        current_value[0] if cash_accrual is None else cash_accrual.get_accrued_value(current_value, d)
                    )
                if d in backtest.cash_payments:
                    for cp in backtest.cash_payments[d]:
                        trades = cp.trade.all_instruments if isinstance(cp.trade, Portfolio) else [cp.trade]
                        for trade in trades:
                            value = cash_results.get(cp.effective_date, {}).get(price_risk, {}).get(trade.name, {})
                            try:
                                value = (
                                    backtest.results[cp.effective_date][price_risk][trade.name]
                                    if value == {}
                                    else value
                                )
                            except (KeyError, ValueError):
                                raise RuntimeError(
                                    f"failed to get cash value for {trade.name} on "
                                    f"{cp.effective_date} received value of {value}"
                                )
                            if not isinstance(value, float):
                                raise RuntimeError(
                                    f"failed to get cash value for {trade.name} on "
                                    f"{cp.effective_date} received value of {value}"
                                )
                            # pricebt DEV-E13: gs maps the value's unit key through
                            # map_ccy_name_to_ccy (a long-name -> ISO lookup; an unmapped key
                            # yields None). pricebt's FloatWithInfo.unit key is already the ISO code.
                            ccy = next(iter(value.unit))
                            if d not in backtest.cash_dict:
                                # pricebt DEV-E11: the ported gs line is `backtest.cash_dict[d] =
                                # {ccy: initial_value}` -- initial_value is now seeded exactly once,
                                # above, so every date's first-seen currency here starts at 0.0.
                                backtest.cash_dict[d] = {ccy: 0.0}
                            if ccy not in backtest.cash_dict[d]:
                                backtest.cash_dict[d][ccy] = 0

                            cp.cash_paid[ccy] += value * cp.direction

                        for ccy, cash_paid in cp.cash_paid.items():
                            backtest.cash_dict[d][ccy] += cash_paid

                    current_value = backtest.cash_dict[d], d

                current_value = _deepcopy(current_value)
            bar.update(1)


def _initial_value_ccy(backtest: BackTest) -> str:
    """DESIGN.md section 7 point 7 / DEV-E11: result_ccy, else PricebtSession.reporting_currency,
    else the single currency of the pre-scanned tradable assets (raising if ambiguous)."""
    price_measure = backtest.price_measure
    result_ccy = getattr(price_measure, "currency", None)
    if result_ccy is not None:
        return result_ccy
    session = PricebtSession.current
    if session.reporting_currency is not None:
        return session.reporting_currency
    insts, _hedge_pairs = _collect_tradable(backtest.strategy)
    currencies = {session.pricing.asset_for(i).currency for i in insts}
    if len(currencies) == 1:
        return next(iter(currencies))
    raise ValueError(
        "initial_value needs result_ccy or PricebtSession(reporting_currency=...) for a multi-currency strategy"
    )


def _final_date_of(inst: Instrument, backtest: BackTest) -> Optional[dt.date]:
    """DEV-R1's flatness test needs each held position's final (exit) date. It is not stored on the
    instrument itself; recover it from the matching exit CashPayment (direction != -1) booked for
    this instrument's name -- the same bookkeeping every action impl already produces.
    """
    for date_payments in backtest.cash_payments.values():
        for cp in date_payments:
            if cp.direction == -1:
                continue
            trade = cp.trade
            if getattr(trade, "name", None) == inst.name:
                return cp.effective_date
            # pricebt DEV-R1: a hedge leg's exit cp.trade is the scaled WRAPPER Portfolio (gs's own
            # "adds leaves, not the portfolio": portfolio_dict holds the leaves, the exit CashPayment
            # keeps the wrapper) -- a leaf's own name never equals the wrapper's, so also match it
            # against the wrapper's own leaves.
            if isinstance(trade, Portfolio) and any(leaf.name == inst.name for leaf in trade.all_instruments):
                return cp.effective_date
    return None


def _deepcopy(x):
    import copy

    return copy.deepcopy(x)


def _remove_tce(backtest, tce):
    if tce is None:
        return
    lst = backtest.transaction_cost_entries.get(tce.date)
    if lst and tce in lst:
        lst.remove(tce)
