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
# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: DEV-T4, DEV-T5, DEV-T6,
# DEV-T7, DEV-T8, DEV-T9, DEV-T10, DEV-T12
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Iterable, Optional, Union

from ..base import static_field
from ..common import RiskMeasure
from ..data import Dataset
from ..datetime.relative_date import RelativeDateSchedule
from ..risk.transform import Transformer
from .actions import (
    Action,
    AddScaledTradeAction,
    AddScaledTradeActionInfo,
    AddTradeAction,
    AddTradeActionInfo,
    HedgeAction,
    HedgeActionInfo,
)
from .backtest_objects import BackTest, PredefinedAssetBacktest
from .backtest_utils import CalcType, make_list
from .data_sources import DataSource


class TriggerDirection(Enum):
    ABOVE = 1
    BELOW = 2
    EQUAL = 3


class AggType(Enum):
    ALL_OF = 1
    ANY_OF = 2


@dataclass
class TriggerRequirements:
    __sub_classes: ClassVar[list[type]] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        TriggerRequirements.__sub_classes.append(cls)

    @staticmethod
    def sub_classes():
        return tuple(TriggerRequirements.__sub_classes)

    def get_trigger_times(self):
        return []

    @property
    def calc_type(self):
        return CalcType.simple

    # pricebt DEV-T10: base no-op; overridden by stateful subclasses (Periodic/MeanReversion/Event/Aggregate/Not)
    def reset(self) -> None:
        """pricebt addition (DEV-T10): gs's stateful trigger requirements (MeanReversion's
        current_position, Periodic's memoised trigger_dates, Event's memoised trigger_dates) live on
        the instance and are never reset, so re-running the same Strategy object twice starts the
        second run wherever the first one left off. GenericEngine.run_backtest (P3.5) calls this on
        every trigger requirement at the start of each run. No-op by default; overridden by the
        stateful subclasses below, and walked into their children by Aggregate/Not."""
        pass


@dataclass
class TriggerInfo(object):
    triggered: bool
    info_dict: Optional[dict] = None

    def __eq__(self, other):
        return self.triggered is other

    def __bool__(self):
        return self.triggered


def check_barrier(direction, test_value, trigger_level) -> TriggerInfo:
    if direction == TriggerDirection.ABOVE:
        if test_value > trigger_level:
            return TriggerInfo(True)
    elif direction == TriggerDirection.BELOW:
        if test_value < trigger_level:
            return TriggerInfo(True)
    else:
        if test_value == trigger_level:
            return TriggerInfo(True)
    return TriggerInfo(False)


@dataclass
class PeriodicTriggerRequirements(TriggerRequirements):
    start_date: Optional[dt.date] = None
    end_date: Optional[dt.date] = None
    frequency: Optional[str] = None
    calendar: Optional[Iterable[dt.date]] = None
    trigger_dates = []
    # pricebt DEV-T4: not a gs field -- a plain instance/class attribute, like trigger_dates above,
    # so the positional constructor signature is unchanged. gs treats start_date=None as "today"
    # (RelativeDateSchedule's own default, via the ambient PricingContext); pricebt has no ambient
    # "today" for a historical backtest, so start_date=None instead means the backtest start.
    # GenericEngine.run_backtest (P3.5) sets this to the grid's first date, after the §9.5
    # missing-market drop, before the first get_trigger_times() call.
    _backtest_start = None
    class_type: str = static_field('periodic_trigger_requirements')

    def get_trigger_times(self) -> [dt.date]:
        if not self.trigger_dates:
            # pricebt DEV-T4: see the _backtest_start comment above.
            start = self.start_date if self.start_date is not None else self._backtest_start
            # pricebt DEV-T15: frequency is lower-cased before dispatch -- 2.1.17 behaviour, kept
            # verbatim (not a pricebt change; 1.5.4's RelativeDate was case-sensitive, e.g. '1M'
            # meant "next 1st Monday", an unrelated rule -- see pricebt.datetime.relative_date).
            self.trigger_dates = RelativeDateSchedule(self.frequency.lower(), start, self.end_date).apply_rule(
                holiday_calendar=self.calendar
            )
        return self.trigger_dates

    def has_triggered(self, state: dt.date, backtest: BackTest = None) -> TriggerInfo:
        if not self.trigger_dates:
            self.get_trigger_times()
        if state in self.trigger_dates:
            next_state = None
            if self.trigger_dates.index(state) != len(self.trigger_dates) - 1:
                next_state = self.trigger_dates[self.trigger_dates.index(state) + 1]
            return TriggerInfo(
                True,
                {
                    AddTradeAction: AddTradeActionInfo(scaling=None, next_schedule=next_state),
                    AddScaledTradeAction: AddScaledTradeActionInfo(next_schedule=next_state),
                    HedgeAction: HedgeActionInfo(next_schedule=next_state),
                },
            )
        return TriggerInfo(False)

    def reset(self) -> None:
        # pricebt DEV-T10: forget the memoised schedule so the next get_trigger_times() call
        # rebuilds it (picking up a new _backtest_start on a fresh run).
        self.trigger_dates = []


@dataclass
class IntradayTriggerRequirements(TriggerRequirements):
    start_time: Optional[dt.time] = None
    end_time: Optional[dt.time] = None
    frequency: Optional[float] = None
    class_type: str = static_field('intraday_trigger_requirements')

    def __post_init__(self):
        # generate all the trigger times
        self._trigger_times = []
        time = self.start_time
        while time <= self.end_time:
            self._trigger_times.append(time)
            next_time = (dt.datetime.combine(dt.date.today(), time) + dt.timedelta(minutes=self.frequency)).time()
            if next_time <= time:
                # pricebt DEV-T9: gs loops forever when adding frequency wraps past midnight (e.g.
                # start=23:00, end=23:59, frequency=30: 23:00, 23:30, 00:00, 00:30, ... .time() never
                # exceeds end_time because it wrapped back to a small value). Stop at the wrap.
                break
            time = next_time

    def get_trigger_times(self):
        return self._trigger_times

    def has_triggered(self, state: Union[dt.date, dt.datetime], backtest: BackTest = None) -> TriggerInfo:
        return TriggerInfo(state.time() in self._trigger_times)


@dataclass
class MktTriggerRequirements(TriggerRequirements):
    data_source: DataSource = None
    trigger_level: float = None
    direction: TriggerDirection = None
    class_type: str = static_field('mkt_trigger_requirements')

    def has_triggered(self, state: dt.date, backtest: BackTest = None) -> TriggerInfo:
        data_value = self.data_source.get_data(state)
        try:
            triggered = check_barrier(self.direction, data_value, self.trigger_level)
        except TypeError:
            raise RuntimeError(f'unable to determine trigger state on {str(state)}, data value was {data_value}')
        return triggered


@dataclass
class RiskTriggerRequirements(TriggerRequirements):
    risk: RiskMeasure = None
    trigger_level: float = None
    direction: TriggerDirection = None
    risk_transformation: Optional[Transformer] = None
    class_type: str = static_field('risk_trigger_requirements')

    def has_triggered(self, state: dt.date, backtest: BackTest = None) -> TriggerInfo:
        if state not in backtest.results:
            return TriggerInfo(False)
        if self.risk_transformation is None:
            risk_value = backtest.results[state][self.risk].aggregate()
        else:
            risk_value = (
                backtest.results[state][self.risk]
                .transform(risk_transformation=self.risk_transformation)
                .aggregate(allow_mismatch_risk_keys=True)
            )
        return check_barrier(self.direction, risk_value, self.trigger_level)

    @property
    def calc_type(self):
        return CalcType.path_dependent


@dataclass
class AggregateTriggerRequirements(TriggerRequirements):
    triggers: Iterable[TriggerRequirements] = None
    aggregate_type: AggType = AggType.ALL_OF
    class_type: str = static_field('aggregate_trigger_requirements')

    def __setattr__(self, key, value):
        if key == 'triggers':
            if value is None:
                # pricebt DEV-T8: gs's `all([isinstance(v, Trigger) for v in value])` iterates
                # `None` when triggers is left at its own default (`AggregateTriggerRequirements()`
                # with no args), raising an unhelpful `TypeError: 'NoneType' object is not
                # iterable` at construction. A clear message instead.
                raise ValueError('triggers required')
            if all(isinstance(v, Trigger) for v in value):
                value = tuple(v.trigger_requirements for v in value)
        super().__setattr__(key, value)

    def has_triggered(self, state: dt.date, backtest: BackTest = None) -> TriggerInfo:
        info_dict = {}
        if self.aggregate_type == AggType.ALL_OF:
            for trigger in self.triggers:
                t_info = trigger.has_triggered(state, backtest)
                if not t_info:
                    return TriggerInfo(False)
                else:
                    if t_info.info_dict:
                        info_dict.update(t_info.info_dict)
            return TriggerInfo(True, info_dict)
        elif self.aggregate_type == AggType.ANY_OF:
            triggered = False
            for trigger in self.triggers:
                t_info = trigger.has_triggered(state, backtest)
                if t_info:
                    triggered = True
                    if t_info.info_dict:
                        info_dict.update(t_info.info_dict)
            return TriggerInfo(True, info_dict) if triggered else TriggerInfo(False)
        else:
            raise RuntimeError(f'Unrecognised aggregation type: {self.aggregate_type}')

    @property
    def calc_type(self):
        seen_types = set()
        for trigger in self.triggers:
            seen_types.add(trigger.calc_type)

        if CalcType.path_dependent in seen_types:
            return CalcType.path_dependent
        elif CalcType.semi_path_dependent in seen_types:
            return CalcType.semi_path_dependent
        else:
            return CalcType.simple

    def get_trigger_times(self):
        # pricebt DEV-T7: gs returns the base [] here (never overridden), so a DateTrigger/
        # PeriodicTrigger nested inside an Aggregate never adds its dates to the engine's pricing
        # grid, and an EventTriggerRequirements inside one never even has get_trigger_times()
        # called, so it can never fire (research/01 section 6.5.5). Union the children's times
        # instead; extra evaluation dates are harmless for ALL_OF (has_triggered still
        # short-circuits on the first falsy child).
        times = set()
        for trigger in self.triggers:
            times.update(trigger.get_trigger_times())
        return sorted(times)

    def reset(self) -> None:
        # pricebt DEV-T10: walk every child so nested stateful requirements reset too.
        for trigger in self.triggers:
            trigger.reset()


@dataclass
class NotTriggerRequirements(TriggerRequirements):
    trigger: TriggerRequirements = None
    class_type: str = static_field('not_trigger_requirements')

    def __setattr__(self, key, value):
        # pricebt DEV-T6: gs's `super().__setattr__` call is indented INSIDE the
        # `if key == 'trigger':` block, so setting any other attribute is silently dropped -- the
        # instance's __dict__ never gets that key at all. A real, documented gs bug (research/01
        # section 6.5.6), not ported. Unwrap a Trigger into its trigger_requirements when the key
        # is 'trigger'; every attribute is actually set via the unconditional super() call below.
        if key == 'trigger' and isinstance(value, Trigger):
            value = value.trigger_requirements
        super().__setattr__(key, value)

    def has_triggered(self, state: dt.date, backtest: BackTest = None) -> TriggerInfo:
        t_info = self.trigger.has_triggered(state, backtest)
        if t_info:
            return TriggerInfo(False)
        else:
            return TriggerInfo(True)

    @property
    def calc_type(self):
        # pricebt DEV-T5: gs hard-codes `simple` here (never overridden), so a NotTrigger wrapping
        # a path_dependent child (e.g. RiskTriggerRequirements) runs in the pre-pass while
        # backtest.results is still empty; the child then returns False on every date, so the Not
        # fires on EVERY date. Inherit the child's calc_type instead.
        return self.trigger.calc_type

    def get_trigger_times(self):
        # pricebt DEV-T7: gs returns the base [] here too; use the child's times (the "union of the
        # children's times" reduces to one child for Not).
        return sorted(self.trigger.get_trigger_times())

    def reset(self) -> None:
        # pricebt DEV-T10: walk the single child.
        self.trigger.reset()


@dataclass
class DateTriggerRequirements(TriggerRequirements):
    dates: Iterable[Union[dt.datetime, dt.date]] = None
    entire_day: bool = False
    class_type: str = static_field('date_trigger_requirements')
    dates_from_datetimes = []

    def __post_init__(self):
        self.dates_from_datetimes = (
            [d.date() if isinstance(d, dt.datetime) else d for d in self.dates] if self.entire_day else None
        )

    def has_triggered(self, state: Union[dt.date, dt.datetime], backtest: BackTest = None) -> TriggerInfo:
        if self.entire_day:
            dates = sorted(self.dates_from_datetimes)
            if isinstance(state, dt.datetime):
                state = state.date()
        else:
            dates = sorted(self.dates)
        if state in dates:
            next_state = None
            if dates.index(state) < len(dates) - 1:
                next_state = dates[dates.index(state) + 1]
            return TriggerInfo(
                True,
                {
                    AddTradeAction: AddTradeActionInfo(scaling=None, next_schedule=next_state),
                    AddScaledTradeAction: AddScaledTradeActionInfo(next_schedule=next_state),
                    HedgeAction: HedgeActionInfo(next_schedule=next_state),
                },
            )
        return TriggerInfo(False)

    def get_trigger_times(self):
        return self.dates_from_datetimes or self.dates


@dataclass
class PortfolioTriggerRequirements(TriggerRequirements):
    data_source: str = None
    trigger_level: float = None
    direction: TriggerDirection = None
    class_type: str = static_field('portfolio_trigger_requirements')

    def has_triggered(self, state: dt.date, backtest: BackTest = None) -> TriggerInfo:
        if self.data_source == 'len':
            value = len(backtest.portfolio_dict)
            if self.direction == TriggerDirection.ABOVE:
                if value > self.trigger_level:
                    return TriggerInfo(True)
            elif self.direction == TriggerDirection.BELOW:
                if value < self.trigger_level:
                    return TriggerInfo(True)
            else:
                if value == self.trigger_level:
                    return TriggerInfo(True)
        return TriggerInfo(False)


@dataclass
class MeanReversionTriggerRequirements(TriggerRequirements):
    data_source: DataSource = None
    z_score_bound: float = None
    rolling_mean_window: int = None
    rolling_std_window: int = None
    current_position = 0
    class_type: str = static_field('mean_reversion_trigger_requirements')

    def has_triggered(self, state: dt.date, backtest: BackTest = None) -> TriggerInfo:
        rolling_mean = self.data_source.get_data_range(state, self.rolling_mean_window).mean()
        rolling_std = self.data_source.get_data_range(state, self.rolling_std_window).std()
        current_price = self.data_source.get_data(state)
        if self.current_position == 0:
            if abs((current_price - rolling_mean) / rolling_std) > self.z_score_bound:
                if current_price > rolling_mean:
                    self.current_position = -1
                    return TriggerInfo(True, {AddTradeAction: AddTradeActionInfo(scaling=-1, next_schedule=None)})
                else:
                    self.current_position = 1
                    return TriggerInfo(True, {AddTradeAction: AddTradeActionInfo(scaling=1, next_schedule=None)})
        elif self.current_position == 1:
            if current_price > rolling_mean:
                self.current_position = 0
                return TriggerInfo(True, {AddTradeAction: AddTradeActionInfo(scaling=-1, next_schedule=None)})
        elif self.current_position == -1:
            if current_price < rolling_mean:
                self.current_position = 0
                return TriggerInfo(True, {AddTradeAction: AddTradeActionInfo(scaling=1, next_schedule=None)})
        else:
            raise RuntimeWarning(f'unexpected current position: {self.current_position}')
        return TriggerInfo(False)

    def reset(self) -> None:
        # pricebt DEV-T10: current_position is a class attribute shadowed on the instance the first
        # time it changes (see has_triggered above) and gs never resets it, so re-running the same
        # Strategy starts the second run wherever the first one left the position. Reset to flat.
        self.current_position = 0


@dataclass
class TradeCountTriggerRequirements(TriggerRequirements):
    trade_count: float = None
    direction: TriggerDirection = None
    class_type: str = static_field('trade_count_requirements')

    def has_triggered(self, state: dt.date, backtest: BackTest = None) -> TriggerInfo:
        value = len(backtest.portfolio_dict.get(state, []))
        if self.direction == TriggerDirection.ABOVE:
            if value > self.trade_count:
                return TriggerInfo(True)
        elif self.direction == TriggerDirection.BELOW:
            if value < self.trade_count:
                return TriggerInfo(True)
        else:
            if value == self.trade_count:
                return TriggerInfo(True)
        return TriggerInfo(False)

    @property
    def calc_type(self):
        return CalcType.path_dependent


@dataclass
class EventTriggerRequirements(TriggerRequirements):
    event_name: str = None
    offset_days: int = 0
    # Calendar filters. All optional so historical single-arg callers still
    # work, but strongly recommended: shared eventNames like 'CPI' or
    # 'Interest Rate Decision' repeat across regions/sources, and an
    # unfiltered scan of MACRO_EVENTS_CALENDAR from 2000-01-01 times out.
    country: Optional[str] = None
    currency: Optional[str] = None
    source: Optional[str] = None
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None
    data_source: DataSource = None
    class_type: str = static_field('event_requirements')
    trigger_dates = []

    def __post_init__(self):
        if self.data_source is None:
            # pricebt DEV-T12: gs falls back to a GS-server-side MACRO_EVENTS_CALENDAR lookup, a
            # dependency pricebt does not have (MUST-1). Require a user-supplied data_source
            # instead of silently reaching for a server pricebt cannot have.
            raise ValueError('data_source required')

    def get_trigger_times(self) -> [dt.date]:
        if not self.trigger_dates:
            kwargs = {'eventName': self.event_name}
            if self.country:
                kwargs['country'] = self.country
            if self.currency:
                kwargs['currency'] = self.currency
            if self.source:
                kwargs['source'] = [self.source] if isinstance(self.source, str) else self.source
            # Window bounds go through get_data's positional (start, end) via
            # the GsDataSource wrapper. When either bound is set we pass both
            # so the dataset query is fully constrained.
            if self.start is not None or self.end is not None:
                kwargs['start'] = self.start
                kwargs['end'] = self.end
            self.trigger_dates = [
                d.date() + dt.timedelta(days=self.offset_days) for d in self.data_source.get_data(None, **kwargs).index
            ]
        return self.trigger_dates

    def has_triggered(self, state: dt.date, backtest: BackTest = None) -> TriggerInfo:
        dates = sorted(self.trigger_dates)
        if state in dates:
            next_state = None
            if dates.index(state) < len(dates) - 1:
                next_state = dates[dates.index(state) + 1]
            return TriggerInfo(
                True,
                {
                    AddTradeAction: AddTradeActionInfo(scaling=None, next_schedule=next_state),
                    AddScaledTradeAction: AddScaledTradeActionInfo(next_schedule=next_state),
                    HedgeAction: HedgeActionInfo(next_schedule=next_state),
                },
            )
        return TriggerInfo(False)

    def reset(self) -> None:
        # pricebt DEV-T10: clear the memo so a reused Strategy re-queries its data_source instead
        # of replaying the previous run's event dates.
        self.trigger_dates = []

    @staticmethod
    def list_events(currency: str, start=Optional[dt.datetime], end=Optional[dt.datetime], **kwargs):
        kwargs['currency'] = currency
        dataset = Dataset('MACRO_EVENTS_CALENDAR')
        return dataset.get_data(start, end, **kwargs)['eventName'].unique()


@dataclass
class Trigger:
    trigger_requirements: Optional[TriggerRequirements] = None
    actions: Union[Action, Iterable[Action]] = None
    __sub_classes: ClassVar[list[type]] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        Trigger.__sub_classes.append(cls)

    @staticmethod
    def sub_classes():
        return tuple(Trigger.__sub_classes)

    def __post_init__(self):
        self.actions = make_list(self.actions)

    def has_triggered(self, state: dt.date, backtest: BackTest = None) -> TriggerInfo:
        """
        implemented by sub classes
        :param state:
        :param backtest:
        :return:
            TriggerInfo containing a bool indication of whether the trigger has triggered and optionally info
            in the form of a dictionary of action type to object info understood by that action.
        """
        return self.trigger_requirements.has_triggered(state, backtest)

    def get_trigger_times(self):
        return self.trigger_requirements.get_trigger_times()

    @property
    def calc_type(self):
        return self.trigger_requirements.calc_type

    @property
    def risks(self):
        return [x.risk for x in make_list(self.actions) if x.risk is not None]


@dataclass
class PeriodicTrigger(Trigger):
    trigger_requirements: PeriodicTriggerRequirements = None
    _trigger_dates = None
    class_type: str = static_field('periodic_trigger')


@dataclass
class IntradayPeriodicTrigger(Trigger):
    trigger_requirements: IntradayTriggerRequirements = None
    class_type: str = static_field('intraday_periodic_trigger')


@dataclass
class MktTrigger(Trigger):
    trigger_requirements: MktTriggerRequirements = None
    class_type: str = static_field('mkt_trigger')


@dataclass
class StrategyRiskTrigger(Trigger):
    trigger_requirements: RiskTriggerRequirements = None
    class_type: str = static_field('strategy_risk_trigger')

    @property
    def risks(self):
        return [x.risk for x in make_list(self.actions) if x.risk is not None] + [self.trigger_requirements.risk]


@dataclass
class AggregateTrigger(Trigger):
    trigger_requirements: AggregateTriggerRequirements = None
    class_type: str = static_field('aggregate_trigger')


@dataclass
class NotTrigger(Trigger):
    trigger_requirements: NotTriggerRequirements = None
    class_type: str = static_field('not_trigger')


@dataclass
class DateTrigger(Trigger):
    trigger_requirements: DateTriggerRequirements = None
    class_type: str = static_field('date_trigger')


@dataclass
class PortfolioTrigger(Trigger):
    trigger_requirements: PortfolioTriggerRequirements = None
    class_type: str = static_field('portfolio_trigger')


@dataclass
class MeanReversionTrigger(Trigger):
    trigger_requirements: MeanReversionTriggerRequirements = None
    class_type: str = static_field('mean_reversion_trigger')


@dataclass
class TradeCountTrigger(Trigger):
    trigger_requirements: TradeCountTriggerRequirements = None
    class_type: str = static_field('trade_count_trigger')


@dataclass
class EventTrigger(Trigger):
    trigger_requirements: EventTriggerRequirements = None
    class_type: str = static_field('event_trigger')


@dataclass
class OrdersGeneratorTrigger(Trigger):
    """Base class for triggers used with the PredefinedAssetEngine."""

    def __post_init__(self):
        if not self.actions:
            self.actions = [Action()]
        super().__post_init__()

    def get_trigger_times(self) -> list:
        """
        Returns the set of times when orders can be generated e.g. every 30 min
        :return: list
        """
        raise RuntimeError('get_trigger_times must be implemented by subclass')

    def generate_orders(self, state: dt.datetime, backtest: PredefinedAssetBacktest = None) -> list:
        """
        Returns the orders generated at state
        :param state: the time when orders are generated
        :param backtest: the backtest, used to access the holdings and orders generated so far
        :return: list
        """
        raise RuntimeError('generate_orders must be implemented by subclass')

    def has_triggered(self, state: dt.datetime, backtest: PredefinedAssetBacktest = None) -> TriggerInfo:
        """
        Calls generate_orders if state is among the trigger times
        :param state: the time of the trigger
        :param backtest: the backtest, used to access the holdings and orders generated so far
        :return: list
        """
        if state.time() not in self.get_trigger_times():
            return TriggerInfo(False)
        else:
            orders = self.generate_orders(state, backtest)
            return TriggerInfo(True, {type(a): orders for a in self.actions}) if len(orders) else TriggerInfo(False)
