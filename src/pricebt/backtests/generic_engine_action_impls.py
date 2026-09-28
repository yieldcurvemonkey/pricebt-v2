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
# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: DEV-E3, DEV-E5, DEV-E6,
# DEV-E9, DEV-E12, DEV-E13, DEV-E16, DEV-T11
#
# Every GenericEngine action handler (DESIGN.md section 9.3): OrderBasedActionImpl,
# AddTradeActionImpl, AddScaledTradeActionImpl, HedgeActionImpl, ExitTradeActionImpl,
# RebalanceActionImpl, AddWeightedTradeActionImpl, EarlyExitPositionLimitScaledActionImpl. Also
# holds `_roll_to_market_date` (DEV-E16) and `_trigger_info_for_date` (DEV-T11): both files need
# them, generic_engine.py imports the pricing/session stack downward of this module (never the
# reverse -- DESIGN.md section 3.2's DAG), so they live here rather than creating a cycle.
from __future__ import annotations

import datetime as dt
from abc import ABCMeta
from bisect import insort
from collections import namedtuple
from itertools import zip_longest
from typing import Collection, Iterable, Optional, Union

from ..errors import ConfigError, MarketDataUnavailable, NotSupportedError
from ..instrument import Instrument
from ..markets import HistoricalPricingContext, PricingContext
from ..markets.portfolio import Portfolio
from ..risk import ResolvedInstrumentValues, RiskMeasure
from ..risk.results import PortfolioRiskResult
from ..session import PricebtSession
from .action_handler import ActionHandler
from .actions import (
    Action,
    AddScaledTradeAction,
    AddScaledTradeActionInfo,
    AddTradeAction,
    AddTradeActionInfo,
    AddWeightedTradeAction,
    AddWeightedTradeActionInfo,
    ExitTradeAction,
    ExitTradeActionInfo,
    HedgeAction,
    HedgeActionInfo,
    RebalanceAction,
    RebalanceActionInfo,
    ScalingActionType,
)
from .backtest_objects import (
    BackTest,
    CashPayment,
    Hedge,
    ScalingPortfolio,
    TransactionCostEntry,
    WeightedScalingPortfolio,
    WeightedTrade,
)
from .backtest_utils import get_final_date, interpolate_signal, make_list


# pricebt DEV-T11: trigger_info reaching an impl's apply_action may be a `{date: info}` dict (Phase
# 3, keyed by date so a date whose trigger produced no info never shifts a later date's info -- gs's
# own positional `zip_longest` list has exactly that bug), a single info object (Phase 5, one date
# at a time), or None. Every impl normalises through this one helper instead of gs's per-class
# zip_longest/ti_by_state dance.
def _trigger_info_for_date(trigger_info, d: dt.date):
    if trigger_info is None:
        return None
    if isinstance(trigger_info, dict):
        return trigger_info.get(d)
    return trigger_info


# pricebt DEV-T11: gs's own public `apply_action(state, backtest, trigger_info)` signature also
# accepts a bare positional list of infos (`Iterable[XInfo]`, one per date in `state`/`dates` order)
# -- kept for MUST-2 API parity, for a caller that builds trigger_info itself rather than going
# through GenericEngine. `_trigger_info_for_date` alone cannot handle that shape (it only ever sees
# one date at a time, and a namedtuple info is itself a tuple, so "is it a list" is the only safe
# discriminator -- DESIGN.md section 11's own wording: "`dict(zip_longest(...))` is used only for a
# list"). Call this ONCE, up front, with the full ordered date list, before any per-date lookup.
def _normalize_trigger_info(trigger_info, dates: "list[dt.date]"):
    if isinstance(trigger_info, list):
        return dict(zip_longest(dates, trigger_info))
    return trigger_info


def _remap_next_schedule(info, kept_sorted: "list[dt.date]"):
    """pricebt DESIGN.md section 9.5 step 5 / DEV-T11: a `next_schedule` that fell on a date the
    missing-market policy dropped from the grid is remapped to the first KEPT grid date >= it, or
    to None if none remain <= the grid's end. Grid MEMBERSHIP only -- not a market-availability
    check (that is `_roll_to_market_date` below, for a different set of dates)."""
    if info is None or not hasattr(info, "next_schedule") or info.next_schedule is None:
        return info
    ns = info.next_schedule
    if ns in kept_sorted:
        return info
    for cand in kept_sorted:
        if cand >= ns:
            return info._replace(next_schedule=cand)
    return info._replace(next_schedule=None)


def remap_trigger_info(t_info, kept_sorted: "list[dt.date]"):
    """Apply `_remap_next_schedule` to every value of a TriggerInfo's info_dict. A no-op when there
    is no info_dict (the common case -- most triggers never carry one)."""
    if t_info is None or not t_info.info_dict:
        return t_info
    from .triggers import TriggerInfo

    return TriggerInfo(t_info.triggered, {k: _remap_next_schedule(v, kept_sorted) for k, v in t_info.info_dict.items()})


def _remove_tce(backtest: BackTest, tce: Optional[TransactionCostEntry]) -> None:
    """pricebt DEV-E3: a skipped hedge or skipped weighted-trade instrument (hedge_risk == 0, `d
    not in results`, zero total risk, zero scaling factor) still has its entry/exit
    TransactionCostEntry created and booked in gs (HedgeActionImpl/AddWeightedTradeActionImpl,
    below), because that booking happens before generic_engine.py's Phase 5.4 can know the hedge or
    instrument will be skipped. Remove it from `backtest.transaction_cost_entries` there instead of
    leaving it to silently contribute a cost for a position/cash payment that was never created."""
    if tce is None:
        return
    lst = backtest.transaction_cost_entries.get(tce.date)
    if lst and tce in lst:
        lst.remove(tce)


def _roll_to_market_date(inst: Instrument, d: dt.date, csa: Optional[str], backtest: BackTest, reason: str) -> dt.date:
    """pricebt DEV-E16 (DESIGN.md section 9.5 step 6): gs prices every weekday server-side, so a
    computed exit/final date always has a market. pricebt's `missing_market='drop'` grid policy can
    leave a date with none. Every date from `get_final_date` (or the initial-portfolio duration
    logic) that is `<= strategy_end_date` (`backtest.states[-1]`) and has no market for `inst`'s
    asset is mapped to the first KEPT grid date after it (`backtest.states`, which IS the post-drop
    grid); a date that already has a market, or one beyond the backtest end (never booked anyway),
    is returned unchanged. Under `missing_market='raise'`, such a date raises immediately instead.
    Each real remap is recorded on `backtest.missing_market_moves` as `(trade name, original date,
    used date)`.
    """
    states = backtest.states
    strategy_end_date = states[-1]
    if d == dt.date.max or d > strategy_end_date:
        return d
    session = PricebtSession.current
    asset = session.pricing.asset_for(inst)
    if session.pricing.has_market(asset, d, csa):
        return d
    if session.missing_market == "raise":
        raise MarketDataUnavailable(asset.name, d, csa, reason=reason)
    for cand in sorted(states):
        if cand > d and session.pricing.has_market(asset, cand, csa):
            move = (inst.name, d, cand)
            # the same (inst, exit date) can be rolled more than once for the same exit -- e.g.
            # AddScaledTradeActionImpl._nav_scale_orders rolls it once for NAV unwind pricing, and
            # apply_action rolls it again (independently) for the exit CashPayment/TCE. Record each
            # real remap once.
            if move not in backtest.missing_market_moves:
                backtest.missing_market_moves.append(move)
            return cand
    # No kept date has a market either (e.g. every remaining date is itself a hole): leave the
    # date as computed. If it is ever actually priced, PricingService raises MarketDataUnavailable
    # on its own -- this is not silently swallowed, just not pre-empted here.
    return d


def _stamp_create_date(inst: Instrument, create_date: dt.date) -> None:
    """pricebt DEV-E5: `position_meta` is set to `(action_name, priceable_name, None)` at
    action-construction time (actions.py's `_rename_priceables`/`RebalanceAction.__post_init__`) and
    carried unchanged through `clone()`/`resolve()`. Wherever an impl appends a date suffix to a
    renamed priceable's name (rules R2/R3/R7 and the AddWeightedTradeAction equivalent), fill in
    that same date as the third element, so ExitTradeAction(priceable_names=...) can later match
    on `(priceable_name, create_date)` instead of gs's crash-prone `name.split('_')` parsing."""
    meta = inst.position_meta
    if meta is not None:
        inst.position_meta = (meta[0], meta[1], create_date)


def _single_hedge_leg(trade: Union[Instrument, Portfolio]) -> Optional[Instrument]:
    """pricebt DEV-E9 shared helper: a hedge `trade` is always a Portfolio (HedgeAction wraps a
    lone instrument, actions.py __post_init__). Return its one leaf when there is exactly one, for
    the trade_duration attribute lookups and DEV-E16 rolling below that gs performs on the
    Portfolio itself -- which has no such gs-instrument attributes and always falls through to
    `get_final_date`'s tenor-string branch."""
    if isinstance(trade, Portfolio):
        leaves = trade.all_instruments
        return leaves[0] if len(leaves) == 1 else None
    return trade


# Action Implementations
class OrderBasedActionImpl(ActionHandler, metaclass=ABCMeta):
    def __init__(self, action: Action):
        self._order_valuations = [ResolvedInstrumentValues]
        super().__init__(action)

    def get_base_orders_for_states(self, states: Collection[dt.date], **kwargs):
        orders = {}
        dated_priceables = getattr(self.action, "dated_priceables", {}) or {}
        for s in states:
            active_portfolio = dated_priceables.get(s) or self.action.priceables
            with PricingContext(pricing_date=s):
                orders[s] = Portfolio(active_portfolio).calc(tuple(self._order_valuations))
        return orders

    def get_instrument_final_date(self, inst: Instrument, order_date: dt.date, info: namedtuple):
        return get_final_date(inst, order_date, self.action.trade_duration, self.action.holiday_calendar, info)


class AddTradeActionImpl(OrderBasedActionImpl):
    def __init__(self, action: AddTradeAction):
        super().__init__(action)

    def _raise_order(
        self,
        state: Union[dt.date, Iterable[dt.date]],
        trigger_info: Optional[Union[dict, AddTradeActionInfo]] = None,
    ):
        state_list = make_list(state)
        # pricebt DEV-T11: turn a bare list of infos into a {date: info} dict up front (a single
        # info or a dict/None pass through unchanged) -- see _normalize_trigger_info above -- then
        # build the per-date dict gs's own `ti_by_state` also builds and passes as `trigger_infos`
        # (matching gs generic_engine_action_impls.py:94's `get_base_orders_for_states(state_list,
        # trigger_infos=ti_by_state)`; the base OrderBasedActionImpl ignores the kwarg, but a
        # subclass override may need it, exactly as for AddScaledTradeActionImpl below).
        trigger_info = _normalize_trigger_info(trigger_info, state_list)
        ti_by_state = {d: _trigger_info_for_date(trigger_info, d) for d in state_list}
        orders = self.get_base_orders_for_states(state_list, trigger_infos=ti_by_state)
        final_orders = {}
        for d, p in orders.items():
            new_insts = [t.clone(name=f"{t.name}_{d}") for t in p.result()]
            for ni in new_insts:
                _stamp_create_date(ni, d)  # pricebt DEV-E5
            new_port = Portfolio(new_insts)
            ti = ti_by_state[d]
            final_orders[d] = (new_port.scale(None if ti is None else ti.scaling, in_place=False), ti)

        return final_orders

    def apply_action(
        self,
        state: Union[dt.date, Iterable[dt.date]],
        backtest: BackTest,
        trigger_info: Optional[Union[dict, AddTradeActionInfo]] = None,
    ):
        orders = self._raise_order(state, trigger_info)

        current_tc_entries = []
        # record entry and unwind cashflows
        for create_date, (portfolio, info) in orders.items():
            for inst in portfolio.all_instruments:
                tc_enter = TransactionCostEntry(create_date, inst, self.action.transaction_cost)
                current_tc_entries.append(tc_enter)
                backtest.cash_payments[create_date].append(
                    CashPayment(inst, effective_date=create_date, direction=-1, transaction_cost_entry=tc_enter)
                )
                backtest.transaction_cost_entries[create_date].append(tc_enter)
                final_date = self.get_instrument_final_date(inst, create_date, info)
                # pricebt DEV-E16: see the helper's docstring above.
                final_date = _roll_to_market_date(inst, final_date, PricingContext.current.csa_term, backtest, "exit date")
                tc_exit = TransactionCostEntry(final_date, inst, self.action.transaction_cost_exit)
                current_tc_entries.append(tc_exit)
                backtest.cash_payments[final_date].append(
                    CashPayment(inst, effective_date=final_date, transaction_cost_entry=tc_exit)
                )
                backtest.transaction_cost_entries[final_date].append(tc_exit)
                backtest_states = (s for s in backtest.states if final_date > s >= create_date)
                for s in backtest_states:
                    backtest.portfolio_dict[s].append(inst)

        if any(tce.no_of_risk_calcs > 0 for tce in current_tc_entries):
            backtest.calc_calls += 1
        for tce in current_tc_entries:
            backtest.calculations += tce.no_of_risk_calcs
            tce.calculate_unit_cost()

        return backtest


class AddScaledTradeActionImpl(OrderBasedActionImpl):
    def __init__(self, action: AddScaledTradeAction):
        super().__init__(action)
        self._scaling_level_signal = (
            interpolate_signal(self.action.scaling_level) if isinstance(self.action.scaling_level, dict) else None
        )

    @staticmethod
    def __portfolio_scaling_for_available_cash(
        portfolio, available_cash, cur_day, unscaled_prices_by_day, unscaled_entry_tces_by_day
    ) -> float:
        fixed_tcs = 0
        scaling_based_tcs = 0
        for inst in portfolio:
            insed_fixed_tc, inst_scaling_tc = unscaled_entry_tces_by_day[cur_day][inst].get_cost_by_component()
            fixed_tcs += insed_fixed_tc
            scaling_based_tcs += inst_scaling_tc
        first_scale_factor = (available_cash - fixed_tcs) / (
            unscaled_prices_by_day[cur_day].aggregate() + scaling_based_tcs
        )
        if first_scale_factor == 0:
            return 0
        fixed_tcs = 0
        scaling_based_tcs = 0
        for inst in portfolio:
            unscaled_entry_tces_by_day[cur_day][inst].additional_scaling = first_scale_factor
            insed_fixed_tc, inst_scaling_tc = unscaled_entry_tces_by_day[cur_day][inst].get_cost_by_component()
            fixed_tcs += insed_fixed_tc
            scaling_based_tcs += inst_scaling_tc
        second_scale_factor = max(available_cash - fixed_tcs, 0) / (
            unscaled_prices_by_day[cur_day].aggregate() * first_scale_factor + scaling_based_tcs
        )
        return first_scale_factor * second_scale_factor

    def _nav_scale_orders(self, orders, price_measure, trigger_infos, backtest: BackTest):
        sorted_order_days = sorted(make_list(orders.keys()))
        final_days_orders = {}
        unscaled_entry_tces_by_day = {}
        unscaled_unwind_tces_by_day = {}
        for create_date, portfolio in orders.items():
            info = trigger_infos.get(create_date)
            unscaled_entry_tces_by_day.setdefault(create_date, {})
            for inst in portfolio.all_instruments:
                tc_enter = TransactionCostEntry(create_date, inst, self.action.transaction_cost)
                unscaled_entry_tces_by_day[create_date][inst] = tc_enter
                d = self.get_instrument_final_date(inst, create_date, info)
                # pricebt DEV-E16 (DESIGN.md section 9.5 step 6, which explicitly lists "NAV unwind
                # pricing"): roll a hole exit date forward here too, or `unscaled_unwind_prices_by_day`
                # below prices at a date with no market for `inst`'s asset, and this dict's key would
                # disagree with apply_action's own (correctly rolled) `final_date` for the same exit.
                d = _roll_to_market_date(inst, d, PricingContext.current.csa_term, backtest, "NAV unwind date")
                tc_exit = TransactionCostEntry(d, inst, self.action.transaction_cost_exit)
                unscaled_unwind_tces_by_day.setdefault(d, {})[inst] = tc_exit
                if d not in final_days_orders:
                    final_days_orders[d] = []
                final_days_orders[d].append(inst)

        unscaled_prices_by_day = {}
        unscaled_unwind_prices_by_day = {}
        for day, portfolio in orders.items():
            with PricingContext(pricing_date=day):
                unscaled_prices_by_day[day] = portfolio.calc(price_measure)
        for unwind_day, unwind_instruments in final_days_orders.items():
            # pricebt DEV-E12: compare against this run's own end date (backtest.states[-1]),
            # not dt.date.today() -- one of the three dt.date.today() guards this task replaces
            # (NAV unwind pricing; the other two are the hedge/weighted-trade exit payment guards
            # below and in HedgeActionImpl/AddWeightedTradeActionImpl above).
            if unwind_day <= backtest.states[-1]:
                with PricingContext(pricing_date=unwind_day):
                    unscaled_unwind_prices_by_day[unwind_day] = Portfolio(unwind_instruments).calc(price_measure)
        for day, inst_tce_map in unscaled_entry_tces_by_day.items():
            for inst, tce in inst_tce_map.items():
                tce.calculate_unit_cost()
        for day, inst_tce_map in unscaled_unwind_tces_by_day.items():
            for inst, tce in inst_tce_map.items():
                tce.calculate_unit_cost()

        available_cash = self.action.scaling_level
        scaling_factors_by_inst = {}
        scaling_factors_by_day = {}
        for idx, cur_day in enumerate(sorted_order_days):
            portfolio = orders[cur_day]
            scale_factor = self.__portfolio_scaling_for_available_cash(
                portfolio, available_cash, cur_day, unscaled_prices_by_day, unscaled_entry_tces_by_day
            )
            scaling_factors_by_day[cur_day] = scale_factor
            for inst in portfolio:
                scaling_factors_by_inst[inst] = scale_factor

            available_cash = 0

            if idx + 1 < len(sorted_order_days):
                next_day = sorted_order_days[idx + 1]
            else:
                break

            for d, p in final_days_orders.items():
                if cur_day < d <= next_day:
                    for inst in p:
                        available_cash += unscaled_unwind_prices_by_day[d][inst] * scaling_factors_by_inst[inst]
                        tce = unscaled_unwind_tces_by_day[d][inst]
                        tce.additional_scaling = scaling_factors_by_inst[inst]
                        available_cash -= unscaled_unwind_tces_by_day[d][inst].get_final_cost()
            available_cash = max(available_cash, 0)

        for day in sorted_order_days:
            if scaling_factors_by_day[day] == 0:
                del orders[day]
            else:
                orders[day].scale(scaling_factors_by_day[day])

    def _scaling_level_for_date(self, d: dt.date) -> float:
        if self._scaling_level_signal is not None:
            if d in self._scaling_level_signal:
                return self._scaling_level_signal[d]
            return 0
        else:
            return self.action.scaling_level

    def _scale_order(self, orders, daily_risk, price_measure, trigger_infos, backtest: BackTest):
        if self.action.scaling_type == ScalingActionType.size:
            for day, portfolio in orders.items():
                portfolio.scale(self._scaling_level_for_date(day))
        elif self.action.scaling_type == ScalingActionType.NAV:
            self._nav_scale_orders(orders, price_measure, trigger_infos, backtest)
        elif self.action.scaling_type == ScalingActionType.risk_measure:
            for day, portfolio in orders.items():
                scaling_factor = self._scaling_level_for_date(day) / daily_risk[day]
                portfolio.scale(scaling_factor)
        else:
            raise RuntimeError(f"Scaling Type {self.action.scaling_type} not supported by engine")

    def _raise_order(
        self,
        state_list: Collection[dt.date],
        price_measure: RiskMeasure,
        trigger_infos: dict,
        backtest: BackTest,
    ):
        if self.action.scaling_type == ScalingActionType.risk_measure:
            self._order_valuations.append(self.action.scaling_risk)
        # gs (generic_engine_action_impls.py:288) passes trigger_infos through here so a subclass's
        # own get_base_orders_for_states (e.g. EarlyExitPositionLimitScaledActionImpl below, which
        # needs per-instrument exit dates for max_concurrent_pos accounting) can use it; the base
        # OrderBasedActionImpl.get_base_orders_for_states ignores the kwarg, so this is a no-op here.
        orders = self.get_base_orders_for_states(state_list, trigger_infos=trigger_infos)

        final_orders = {}
        for d, res in orders.items():
            new_port = []
            dated_priceables = getattr(self.action, "dated_priceables", {}) or {}
            instruments = dated_priceables.get(d) or self.action.priceables
            for inst in instruments:
                new_inst = res[inst]
                if len(self._order_valuations) > 1:
                    new_inst = new_inst[ResolvedInstrumentValues]
                new_inst.name = f"{new_inst.name}_{d}"
                _stamp_create_date(new_inst, d)  # pricebt DEV-E5
                new_port.append(new_inst)
            final_orders[d] = Portfolio(new_port)
        daily_risk = (
            {d: res[self.action.scaling_risk].aggregate() for d, res in orders.items()}
            if self.action.scaling_type == ScalingActionType.risk_measure
            else None
        )

        self._scale_order(final_orders, daily_risk, price_measure, trigger_infos, backtest)

        return final_orders

    def apply_action(
        self,
        state: Union[dt.date, Iterable[dt.date]],
        backtest: BackTest,
        trigger_info: Optional[Union[dict, AddScaledTradeActionInfo]] = None,
    ):
        state_list = make_list(state)
        # pricebt DEV-T11: normalise a bare list of infos to a {date: info} dict first (a single
        # info or a dict/None pass through unchanged), THEN build the per-date dict every impl uses.
        trigger_info = _normalize_trigger_info(trigger_info, state_list)
        trigger_infos = {d: _trigger_info_for_date(trigger_info, d) for d in state_list}
        orders = self._raise_order(state_list, backtest.price_measure, trigger_infos, backtest)

        current_tc_entries = []
        for create_date, portfolio in orders.items():
            info = trigger_infos.get(create_date)
            for inst in portfolio.all_instruments:
                tc_enter = TransactionCostEntry(create_date, inst, self.action.transaction_cost)
                current_tc_entries.append(tc_enter)
                backtest.cash_payments[create_date].append(
                    CashPayment(inst, effective_date=create_date, direction=-1, transaction_cost_entry=tc_enter)
                )
                backtest.transaction_cost_entries[create_date].append(tc_enter)
                final_date = self.get_instrument_final_date(inst, create_date, info)
                # pricebt DEV-E16: see the helper's docstring above.
                final_date = _roll_to_market_date(inst, final_date, PricingContext.current.csa_term, backtest, "exit date")
                tc_exit = TransactionCostEntry(final_date, inst, self.action.transaction_cost_exit)
                current_tc_entries.append(tc_exit)
                backtest.cash_payments[final_date].append(
                    CashPayment(inst, effective_date=final_date, transaction_cost_entry=tc_exit)
                )
                backtest.transaction_cost_entries[final_date].append(tc_exit)
                backtest_states = (s for s in backtest.states if final_date > s >= create_date)
                for s in backtest_states:
                    backtest.portfolio_dict[s].append(inst)

        if any(tce.no_of_risk_calcs > 0 for tce in current_tc_entries):
            backtest.calc_calls += 1
        for tce in current_tc_entries:
            backtest.calculations += tce.no_of_risk_calcs
            tce.calculate_unit_cost()

        return backtest


class HedgeActionImpl(OrderBasedActionImpl):
    def __init__(self, action: HedgeAction):
        super().__init__(action)

    def get_base_orders_for_states(self, states: Collection[dt.date], **kwargs):
        with HistoricalPricingContext(dates=states, csa_term=self.action.csa_term):
            f = Portfolio(self.action.priceable).resolve(in_place=False)
        return f.result()

    def get_instrument_final_date(self, inst, order_date: dt.date, info: namedtuple):
        # pricebt DEV-E9: gs looks trade_duration attributes (e.g. 'termination_date') up on the
        # hedge Portfolio itself, which has no such attribute, so the lookup always falls through
        # to get_final_date's tenor-string branch. When the hedge has exactly one leg, look the
        # attribute up on that instrument instead (research/02 section 6.1 Q17).
        target = _single_hedge_leg(inst) or inst
        return get_final_date(target, order_date, self.action.trade_duration, self.action.holiday_calendar, info)

    def apply_action(
        self,
        state: Union[dt.date, Iterable[dt.date]],
        backtest: BackTest,
        trigger_info: Optional[Union[dict, HedgeActionInfo]] = None,
    ):
        state_list = make_list(state)
        # pricebt DEV-T11: normalise a bare list of infos first -- see _normalize_trigger_info above.
        trigger_info = _normalize_trigger_info(trigger_info, state_list)
        trigger_infos = {d: _trigger_info_for_date(trigger_info, d) for d in state_list}
        backtest.calc_calls += 1
        backtest.calculations += len(state_list)
        orders = self.get_base_orders_for_states(state_list, trigger_infos=trigger_infos)

        current_tc_entries = []
        for create_date, portfolio in orders.items():
            info = trigger_infos.get(create_date)
            hedge_trade = portfolio.priceables[0]
            hedge_trade.name = f"{hedge_trade.name}_{create_date.strftime('%Y-%m-%d')}"
            if isinstance(hedge_trade, Portfolio):
                for instrument in hedge_trade.all_instruments:
                    instrument.name = f"{hedge_trade.name}_{instrument.name}"
            final_date = self.get_instrument_final_date(hedge_trade, create_date, info)
            # pricebt DEV-E16: rolled against the HEDGE'S OWN csa (DESIGN.md section 9.5 step 2's
            # "for a HedgeAction's asset, also call has_market(a, d, action.csa_term)"), and only
            # when the hedge has exactly one leg -- see _single_hedge_leg above; a multi-leg hedge's
            # exit date is left as computed (a documented scope limit, not a silent skip: it still
            # raises naturally through PricingService if actually priced on a missing date).
            single_leg = _single_hedge_leg(hedge_trade)
            if single_leg is not None:
                final_date = _roll_to_market_date(single_leg, final_date, self.action.csa_term, backtest, "exit date")
            active_dates = [s for s in backtest.states if create_date <= s < final_date]

            if len(active_dates):
                scaling_portfolio = ScalingPortfolio(
                    trade=hedge_trade,
                    dates=active_dates,
                    risk=self.action.risk,
                    csa_term=self.action.csa_term,
                    risk_transformation=self.action.risk_transformation,
                    risk_percentage=self.action.risk_percentage,
                )
                # pricebt DEV-E3 (both files): this entry/exit TCE pair is booked here
                # unconditionally, but generic_engine.py's Phase 5.4 may later decide the hedge
                # never gets sized (hedge_risk == 0, or no risk result for `d` at all) -- it removes
                # these same TCE objects from backtest.transaction_cost_entries in that case.
                tc_enter = TransactionCostEntry(create_date, hedge_trade, self.action.transaction_cost)
                current_tc_entries.append(tc_enter)
                entry_payment = CashPayment(
                    trade=hedge_trade, effective_date=create_date, direction=-1, transaction_cost_entry=tc_enter
                )
                backtest.transaction_cost_entries[create_date].append(tc_enter)
                tc_exit = TransactionCostEntry(final_date, hedge_trade, self.action.transaction_cost_exit)
                current_tc_entries.append(tc_exit)
                # pricebt DEV-E12: gs compares against dt.date.today() (whatever day the backtest
                # happens to be RUN on); compare against this run's own end date instead.
                backtest_end = backtest.states[-1]
                exit_payment = (
                    CashPayment(trade=hedge_trade, effective_date=final_date, transaction_cost_entry=tc_exit)
                    if final_date <= backtest_end
                    else None
                )
                backtest.transaction_cost_entries[final_date].append(tc_exit)
                hedge = Hedge(
                    scaling_portfolio=scaling_portfolio, entry_payment=entry_payment, exit_payment=exit_payment
                )
                backtest.hedges[create_date].append(hedge)

        if any(tce.no_of_risk_calcs > 0 for tce in current_tc_entries):
            backtest.calc_calls += 1
        for tce in current_tc_entries:
            backtest.calculations += tce.no_of_risk_calcs
            tce.calculate_unit_cost()

        return backtest


class ExitTradeActionImpl(ActionHandler):
    def __init__(self, action: ExitTradeAction):
        super().__init__(action)

    def apply_action(
        self,
        state: Union[dt.date, Iterable[dt.date]],
        backtest: BackTest,
        trigger_info: Optional[Union[dict, ExitTradeActionInfo]] = None,
    ):
        for s in make_list(state):
            trades_to_remove = []
            if self.action.priceable_names is None:
                current_trade_names = [i.name for i in list(backtest.portfolio_dict[s].all_instruments)]

            fut_dates = list(filter(lambda d: d >= s and type(d) is dt.date, backtest.states))
            for port_date in fut_dates:
                res_fut = []
                res_futures = []
                pos_fut = list(backtest.portfolio_dict[port_date].all_instruments)
                # pricebt DEV-E8: `backtest.results[port_date]` on the defaultdict silently creates
                # a `[]` entry when `port_date` has never been priced -- `.get` avoids that.
                results_at_port_date = backtest.results.get(port_date)
                if results_at_port_date:
                    res_fut = list(results_at_port_date.portfolio.all_instruments)
                    res_futures = list(results_at_port_date.futures)

                # pricebt DEV-E5: gs expects tradable names as <ActionName>_<TradeName>_<TradeDate>
                # and matches by splitting on '_' (`x.name.split('_')[-2] in priceable_names`), which
                # breaks on an underscore in the user's own priceable name and crashes outright on a
                # held hedge leg (whose name has no trailing date token to strptime). Match on the
                # structured `position_meta = (action_name, priceable_name, create_date)` set at
                # construction (actions.py) instead: a position with no meta (a hedge leg) is never
                # matched by name.
                if self.action.priceable_names:

                    def _matches(x, _s=s, _names=self.action.priceable_names):
                        meta = getattr(x, "position_meta", None)
                        return meta is not None and meta[1] in _names and meta[2] is not None and meta[2] <= _s

                    port_indexes_to_remove = [i for i, x in enumerate(pos_fut) if _matches(x)]
                    result_indexes_to_remove = [i for i, x in enumerate(res_fut) if _matches(x)]
                else:
                    port_indexes_to_remove = [i for i, x in enumerate(pos_fut) if x.name in current_trade_names]
                    result_indexes_to_remove = [i for i, x in enumerate(res_fut) if x.name in current_trade_names]

                for index in sorted(port_indexes_to_remove, reverse=True):
                    if pos_fut[index].name not in trades_to_remove:
                        trades_to_remove.append(pos_fut[index])
                    del pos_fut[index]
                for index in sorted(result_indexes_to_remove, reverse=True):
                    del res_fut[index]
                    del res_futures[index]
                backtest.portfolio_dict[port_date] = Portfolio(tuple(pos_fut))
                if result_indexes_to_remove:
                    backtest.set_results(
                        port_date, PortfolioRiskResult(Portfolio(res_fut), results_at_port_date.risk_measures, res_futures)
                    )

            for cp_date, cp_list in list(backtest.cash_payments.items()):
                if cp_date > s:
                    indexes_to_remove = [
                        i for i, cp in enumerate(cp_list) if cp.trade.name in [x.name for x in trades_to_remove]
                    ]
                    for index in sorted(indexes_to_remove, reverse=True):
                        cp = cp_list[index]
                        prev_pos = [i for i, x in enumerate(backtest.cash_payments[s]) if cp.trade.name == x.trade.name]
                        if prev_pos:
                            backtest.cash_payments[s][prev_pos[0]].direction += cp.direction
                        else:
                            cp.effective_date = s
                            backtest.cash_payments[s].append(cp)
                        backtest.transaction_cost_entries[s].append(cp.transaction_cost_entry)
                        backtest.transaction_cost_entries[cp_date].remove(cp.transaction_cost_entry)
                        cp.transaction_cost_entry.date = s
                        del backtest.cash_payments[cp_date][index]

                    if not backtest.cash_payments[cp_date]:
                        del backtest.cash_payments[cp_date]

            for trade in trades_to_remove:
                if trade.name not in [x.trade.name for x in backtest.cash_payments[s]]:
                    # pricebt DEV-I3: gs compares by `to_dict()` set-membership -- a dict is
                    # unhashable, a latent crash. Compare by `instrument_identity()` instead
                    # (already the P2.1 fix; this call site is the one gs bug report names).
                    from ..instrument import instrument_identity

                    trade_instruments = (
                        set(instrument_identity(t) for t in trade.all_instruments)
                        if isinstance(trade, Portfolio)
                        else {instrument_identity(trade)}
                    )
                    trade_tce = [
                        tce
                        for tce in backtest.transaction_cost_entries[s]
                        if set(instrument_identity(i) for i in tce.all_instruments) == trade_instruments
                    ]
                    tce = trade_tce[0] if trade_tce else None
                    backtest.cash_payments[s].append(CashPayment(trade, effective_date=s, transaction_cost_entry=tce))

        return backtest


class RebalanceActionImpl(ActionHandler):
    def __init__(self, action: RebalanceAction):
        super().__init__(action)

    def apply_action(
        self,
        state: Union[dt.date, Iterable[dt.date]],
        backtest: BackTest,
        trigger_info: Optional[Union[dict, RebalanceActionInfo]] = None,
    ):
        new_size = self.action.method(state, backtest, trigger_info)
        current_size = 0
        for trade in backtest.portfolio_dict[state]:
            if self.action.priceable.name.split("_")[-1] in trade.name:
                current_size += getattr(trade, self.action.size_parameter)
        # if we are already at the required size then do nothing.
        if new_size - current_size == 0:
            return backtest

        # pricebt (DESIGN.md section 9.4): gs clones the priceable with `size_parameter` set to the
        # DELTA directly, because a gs instrument scales by editing its own size field in place.
        # pricebt instruments have no size fields to edit -- position size is the `quantity_`
        # multiplier (DEV-I1) -- so the delta is expressed as a quantity_ on a fresh unit clone.
        if not isinstance(self.action.size_parameter, str):
            raise NotSupportedError(
                "RebalanceAction.size_parameter must name the asset's size_attribute; a numeric "
                "size_parameter is GS-server-only"
            )
        asset = self.action.priceable.asset_config
        if self.action.size_parameter != asset.size_attribute:
            raise ConfigError(
                f"RebalanceAction.size_parameter {self.action.size_parameter!r} must equal asset "
                f"{asset.name!r}'s size_attribute {asset.size_attribute!r}",
                asset=asset.name,
            )
        unit_size = getattr(self.action.priceable.clone(quantity_=1.0), self.action.size_parameter)
        if unit_size == 0:
            raise ValueError(f"{self.action.size_parameter} of one unit of {self.action.priceable.name} is 0")
        pos = self.action.priceable.clone(
            quantity_=(new_size - current_size) / unit_size, name=f"{self.action.priceable.name}_{state}"
        )
        _stamp_create_date(pos, state)  # pricebt DEV-E5

        current_tc_entries = []
        tc_enter = TransactionCostEntry(state, pos, self.action.transaction_cost)
        current_tc_entries.append(tc_enter)
        backtest.cash_payments[state].append(
            CashPayment(pos, effective_date=state, direction=-1, transaction_cost_entry=tc_enter)
        )
        backtest.transaction_cost_entries[state].append(tc_enter)
        unwind_payment = None
        cash_payment_dates = backtest.cash_payments.keys()
        for d in reversed(sorted(cash_payment_dates)):
            for cp in backtest.cash_payments[d]:
                if self.action.priceable.name.split("_")[-1] in cp.trade.name and cp.direction == 1:
                    tc_exit = TransactionCostEntry(d, pos, self.action.transaction_cost_exit)
                    current_tc_entries.append(tc_exit)
                    unwind_payment = CashPayment(pos, effective_date=d, transaction_cost_entry=tc_exit)
                    backtest.cash_payments[d].append(unwind_payment)
                    # pricebt DEV-E6: gs appends the Python BUILTIN `exit` here (`append(exit)`, a
                    # bare-word typo for `tc_exit`), which crashes later at `get_final_cost()` with
                    # AttributeError the first time this rebalanced position is actually costed.
                    backtest.transaction_cost_entries[d].append(tc_exit)
                    break
            if unwind_payment:
                break

        if unwind_payment is None:
            raise ValueError("Found no final cash payment to rebalance for trade.")

        for s in backtest.states:
            if unwind_payment.effective_date > s >= state:
                backtest.portfolio_dict[s].append(pos)

        if any(tce.no_of_risk_calcs > 0 for tce in current_tc_entries):
            backtest.calc_calls += 1
        for tce in current_tc_entries:
            backtest.calculations += tce.no_of_risk_calcs
            tce.calculate_unit_cost()

        return backtest


class AddWeightedTradeActionImpl(OrderBasedActionImpl):
    def __init__(self, action: AddWeightedTradeAction):
        super().__init__(action)

    def get_base_orders_for_states(self, states: Collection[dt.date], **kwargs):
        with HistoricalPricingContext(dates=states):
            f = Portfolio(self.action.priceables).resolve(in_place=False)
        return f.result()

    def apply_action(
        self,
        state: Union[dt.date, Iterable[dt.date]],
        backtest: BackTest,
        trigger_info: Optional[Union[dict, AddWeightedTradeActionInfo]] = None,
    ):
        state_list = make_list(state)
        # pricebt DEV-T11: normalise a bare list of infos first -- see _normalize_trigger_info above.
        trigger_info = _normalize_trigger_info(trigger_info, state_list)
        trigger_infos = {d: _trigger_info_for_date(trigger_info, d) for d in state_list}
        backtest.calc_calls += 1
        backtest.calculations += len(state_list)
        orders = self.get_base_orders_for_states(state_list, trigger_infos=trigger_infos)

        current_tc_entries = []
        for create_date, portfolio in orders.items():
            info = trigger_infos.get(create_date)
            instruments = portfolio.priceables
            if not instruments:
                continue

            renamed_instruments = []
            for inst in instruments:
                renamed_inst = inst.clone(name=f"{inst.name}_{create_date.strftime('%Y-%m-%d')}")
                _stamp_create_date(renamed_inst, create_date)  # pricebt DEV-E5
                renamed_instruments.append(renamed_inst)

            weighted_portfolio = Portfolio(renamed_instruments)
            final_date = self.get_instrument_final_date(renamed_instruments[0], create_date, info)
            # pricebt DEV-E16: rolled off the SAME representative instrument gs uses for the whole
            # weighted portfolio's final date.
            final_date = _roll_to_market_date(
                renamed_instruments[0], final_date, PricingContext.current.csa_term, backtest, "exit date"
            )
            active_dates = [s for s in backtest.states if create_date <= s < final_date]

            if len(active_dates):
                scaling_portfolio = WeightedScalingPortfolio(
                    trades=weighted_portfolio,
                    dates=active_dates,
                    risk=self.action.scaling_risk,
                    total_size=self.action.total_size,
                )

                entry_payments = []
                exit_payments = []
                for inst in renamed_instruments:
                    # pricebt DEV-E3 (both files): see the identical note in HedgeActionImpl above
                    # -- generic_engine.py's Phase 5.4 removes these TCEs if this instrument is
                    # later skipped (zero total portfolio risk, or a zero scaling factor).
                    tc_enter = TransactionCostEntry(create_date, inst, self.action.transaction_cost)
                    current_tc_entries.append(tc_enter)
                    entry_payment = CashPayment(
                        trade=inst, effective_date=create_date, direction=-1, transaction_cost_entry=tc_enter
                    )
                    entry_payments.append(entry_payment)
                    backtest.transaction_cost_entries[create_date].append(tc_enter)

                    tc_exit = TransactionCostEntry(final_date, inst, self.action.transaction_cost_exit)
                    current_tc_entries.append(tc_exit)
                    # pricebt DEV-E12: compare against this run's own end date, not dt.date.today().
                    backtest_end = backtest.states[-1]
                    exit_payment = (
                        CashPayment(trade=inst, effective_date=final_date, transaction_cost_entry=tc_exit)
                        if final_date <= backtest_end
                        else None
                    )
                    exit_payments.append(exit_payment)
                    backtest.transaction_cost_entries[final_date].append(tc_exit)

                weighted_trade = WeightedTrade(
                    scaling_portfolio=scaling_portfolio,
                    entry_payments=entry_payments,
                    exit_payments=exit_payments,
                )
                backtest.weighted_trades[create_date].append(weighted_trade)

        if any(tce.no_of_risk_calcs > 0 for tce in current_tc_entries):
            backtest.calc_calls += 1
        for tce in current_tc_entries:
            backtest.calculations += tce.no_of_risk_calcs
            tce.calculate_unit_cost()

        return backtest


class EarlyExitPositionLimitScaledActionImpl(AddScaledTradeActionImpl):
    def get_base_orders_for_states(self, states: Collection[dt.date], **kwargs):
        trigger_infos = kwargs.get("trigger_infos")
        orders = {}
        dated_priceables = getattr(self.action, "dated_priceables", {}) or {}
        for s in states:
            active_portfolio = dated_priceables.get(s) or self.action.priceables
            with PricingContext(pricing_date=s):
                orders[s] = Portfolio(active_portfolio).calc(tuple(self._order_valuations))
        cur_no_pos = 0
        future_exits = []
        for s in sorted(states):
            num_exit_pos = len([d for d in future_exits if d <= s])
            cur_no_pos -= num_exit_pos
            future_exits = future_exits[num_exit_pos:]
            if (
                self.action.max_concurrent_pos is not None
                and cur_no_pos + len(orders[s].portfolio) > self.action.max_concurrent_pos
            ):
                del orders[s]
                continue
            for inst in orders[s].portfolio:
                resolved_inst = orders[s][inst]
                if len(self._order_valuations) > 1:
                    resolved_inst = resolved_inst[ResolvedInstrumentValues]
                info = trigger_infos.get(s) if trigger_infos else None
                inst_exit_date = self.get_instrument_final_date(resolved_inst, s, info)
                insort(future_exits, inst_exit_date)
                cur_no_pos += 1
        return orders

    def get_instrument_final_date(self, inst: Instrument, order_date: dt.date, info: namedtuple):
        final_date = get_final_date(inst, order_date, self.action.trade_duration, self.action.holiday_calendar, info)
        if self.action.early_exits is None:
            return final_date
        future_exits = [d for d in sorted(self.action.early_exits) if d > order_date]
        if future_exits:
            final_date = min(final_date, next(iter(future_exits)))
        return final_date

    # No `_raise_order` override here, matching gs: it inherits AddScaledTradeActionImpl's, which
    # already passes `trigger_infos=trigger_infos` into `get_base_orders_for_states` above. Every
    # date kept by that method's max_concurrent_pos filter keeps its WHOLE `orders[d]` (the filter
    # deletes an entire over-limit date, never a single instrument within a kept date), so every
    # `inst in instruments` the shared `_raise_order` looks up is always present in `res`.
