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
# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: DEV-E5, DEV-E7, DEV-E10,
# DEV-T16
#
# Every gs action class, `ScalingActionType`, the `*ActionInfo` namedtuples and `default_transaction_
# cost()` (DESIGN.md section 9.3): AddTradeAction, AddScaledTradeAction, AddWeightedTradeAction,
# EnterPositionQuantityScaledAction, ExitPositionAction, ExitTradeAction, ExitAllPositionsAction,
# HedgeAction, RebalanceAction, EarlyExitPositionLimitScaledAction. `EnterPositionQuantityScaledAction`
# and `ExitPositionAction` are real classes here (API parity) even though GenericEngine (P3.5) has no
# handler for either -- that is a real, intentional gs gap this port preserves, not a bug.
from __future__ import annotations

import datetime as dt
import warnings
from collections import namedtuple
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, ClassVar, Iterable, Optional, TypeVar, Union

from ..base import Priceable, static_field
from ..common import RiskMeasure
from ..instrument import Instrument
from ..markets.portfolio import Portfolio
from ..risk.transform import Transformer
from .backtest_objects import ConstantTransactionModel, TransactionModel
from .backtest_utils import CalcType, CustomDuration, make_list
from .core import BacktestTradingQuantityType

action_count = 1


Duration = Union[str, dt.date, dt.timedelta, CustomDuration]


def default_transaction_cost():
    return ConstantTransactionModel(0)


class ScalingActionType(Enum):
    risk_measure = 'risk_measure'
    size = 'size'
    NAV = 'NAV'


@dataclass
class Action(object):
    _needs_scaling = False
    _calc_type = CalcType.simple
    _risk = None
    _transaction_cost = ConstantTransactionModel(0)
    _transaction_cost_exit = None
    name = None
    __sub_classes: ClassVar[list[type]] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        Action.__sub_classes.append(cls)

    @staticmethod
    def sub_classes():
        return tuple(Action.__sub_classes)

    def __post_init__(self):
        self.set_name(self.name)

    @property
    def calc_type(self):
        return self._calc_type

    @property
    def risk(self):
        return self._risk

    def set_name(self, name: str):
        global action_count
        if self.name is None:
            self.name = 'Action{}'.format(action_count)
            action_count += 1

    @property
    def transaction_cost(self):
        return self._transaction_cost

    @transaction_cost.setter
    def transaction_cost(self, value):
        self._transaction_cost = value

    @property
    def transaction_cost_exit(self):
        return self._transaction_cost_exit

    @transaction_cost_exit.setter
    def transaction_cost_exit(self, value):
        self._transaction_cost_exit = value


TAction = TypeVar('TAction', bound='Action')


def _rename_priceables(action_name: str, priceables: Iterable, set_meta: bool = True) -> list:
    """pricebt addition (not in gs -- shared body for rule R1, research/02 section 2.4): for
    `i, p in enumerate(priceables)`, `p.name is None` -> clone named `f'{action_name}_Priceable{i}'`;
    `p.name.startswith(action_name)` -> keep `p` as-is (note: this branch does not clone, so it
    mutates the caller's original priceable object in place when `set_meta=True` -- gs's own
    equivalent branch, actions.py:445, has the same no-clone behaviour but no `position_meta` field
    to mutate); else -> clone named `f'{action_name}_{p.name}'`.
    Every action with a `priceables`/`priceable` field that gs renames this way
    (AddTradeAction, AddScaledTradeAction, AddWeightedTradeAction, EnterPositionQuantityScaledAction,
    HedgeAction) calls this from its own `__post_init__`, so each keeps its own real gs method body
    with only the loop factored out.

    Also sets `position_meta` on every returned priceable when `set_meta` (the default) -- see the
    DEV-E5 comment on that line below, which replaces gs's crash-prone `name.split('_')[-2]` parsing
    (DESIGN.md section 11 DEV-E5) with something generic_engine_action_impls.py (P3.5) can match on
    safely. HedgeAction passes `set_meta=False`: see the DEV-E5 comment at its call site.
    """
    named = []
    for i, p in enumerate(priceables):
        original_name = p.name if p.name is not None else f'Priceable{i}'
        if p.name is None:
            new_p = p.clone(name=f'{action_name}_Priceable{i}')
        elif p.name.startswith(action_name):
            new_p = p
        else:
            new_p = p.clone(name=f'{action_name}_{p.name}')
        if set_meta:
            # pricebt DEV-E5: (action_name, original priceable name or f'Priceable{i}', None) -- the
            # third element (create date) is filled in by generic_engine_action_impls.py (P3.5)
            # wherever it appends a date suffix to a name. `original_name` is what a caller would
            # pass to ExitTradeAction(priceable_names=...) to match this priceable later.
            new_p.position_meta = (action_name, original_name, None)
        named.append(new_p)
    return named


@dataclass
class AddTradeAction(Action):
    """
    create an action which adds a trade when triggered.  The trades are resolved on the trigger date (state) and
    last until the trade_duration if specified or for all future dates if not.
    :param priceables: a priceable or a list of pricables.
    :param trade_duration: an instrument attribute eg. 'expiration_date' or a date or a tenor or timedelta
                           if left as None the
                           trade will be added for all future dates
                           can also specify 'next schedule' in order to exit at the next periodic trigger date
    :param name: optional additional name to the priceable name
    :param transaction_cost: optional a cash amount paid for each transaction
    :param transaction_cost_exit: optionally specify a different model for exits; defaults to entry cost if None
    """

    priceables: Union[Instrument, Iterable[Instrument]] = None
    trade_duration: Duration = None
    name: str = None
    transaction_cost: TransactionModel = field(default_factory=default_transaction_cost)
    transaction_cost_exit: Optional[TransactionModel] = None
    holiday_calendar: Iterable[dt.date] = None
    class_type: str = static_field('add_trade_action')

    def __post_init__(self):
        super().__post_init__()
        self._dated_priceables = {}
        # pricebt DEV-T16: a list holiday_calendar is unhashable, which crashes
        # backtest_utils.get_final_date's cache key; normalise to a tuple here so the SAME value
        # (list or tuple, given to the action) flows into that cache key without a TypeError.
        if isinstance(self.holiday_calendar, list):
            self.holiday_calendar = tuple(self.holiday_calendar)
        self.priceables = _rename_priceables(self.name, make_list(self.priceables))
        if self.transaction_cost is None:
            self.transaction_cost = ConstantTransactionModel(0)
        if self.transaction_cost_exit is None:
            self.transaction_cost_exit = self.transaction_cost

    def set_dated_priceables(self, state, priceables):
        self._dated_priceables[state] = make_list(priceables)

    @property
    def dated_priceables(self):
        return self._dated_priceables


AddTradeActionInfo = namedtuple('AddTradeActionInfo', ['scaling', 'next_schedule'])
HedgeActionInfo = namedtuple('HedgeActionInfo', 'next_schedule')
ExitTradeActionInfo = namedtuple('ExitTradeActionInfo', 'not_applicable')
RebalanceActionInfo = namedtuple('RebalanceActionInfo', 'not_applicable')
AddScaledTradeActionInfo = namedtuple('AddScaledActionInfo', 'next_schedule')
AddWeightedTradeActionInfo = namedtuple('AddWeightedActionInfo', 'next_schedule')


@dataclass
class AddScaledTradeAction(Action):
    """
    create an action which adds a trade when triggered.  The trade is scaled by a measure or trade property.
    The trades are resolved on the trigger date (state) and last until the trade_duration if specified or for
    all future dates if not.
    :param priceables: a priceable or a list of pricables.
    :param trade_duration: an instrument attribute eg. 'expiration_date' or a date or a tenor or timedelta
                           if left as None the
                           trade will be added for all future dates
                           can also specify 'next schedule' in order to exit at the next periodic trigger date
    :param name: optional additional name to the priceable name
    :param scaling_type: the type of scaling we are doing
    :param scaling_risk: if the scaling type is a measure then this is the definition of the measure
    :param scaling_level: the level of scaling to be done
    :param transaction_cost: optional a cash amount paid for each transaction
    :param transaction_cost_exit: optionally specify a different model for exits; defaults to entry cost if None
    """

    priceables: Union[Priceable, Iterable[Priceable]] = None
    trade_duration: Duration = None
    name: str = None
    scaling_type: ScalingActionType = ScalingActionType.size
    scaling_risk: RiskMeasure = None
    scaling_level: Union[float, dict[dt.date, Union[float, int]]] = 1
    transaction_cost: TransactionModel = field(default_factory=default_transaction_cost)
    transaction_cost_exit: Optional[TransactionModel] = None
    holiday_calendar: Iterable[dt.date] = None
    dated_priceables: dict[dt.date, Priceable] = None
    class_type: str = static_field('add_scaled_trade_action')

    def __post_init__(self):
        super().__post_init__()
        # pricebt DEV-T16: see AddTradeAction above.
        if isinstance(self.holiday_calendar, list):
            self.holiday_calendar = tuple(self.holiday_calendar)
        self.priceables = _rename_priceables(self.name, make_list(self.priceables))
        if self.transaction_cost_exit is None:
            self.transaction_cost_exit = self.transaction_cost


@dataclass
class AddWeightedTradeAction(Action):
    """
    create an action which adds trades when triggered.  The trades are weighted by a measure.
    The trades are resolved on the trigger date (state) and last until the trade_duration if specified or for
    all future dates if not.
    :param priceables: a portfolio.
    :param trade_duration: an instrument attribute eg. 'expiration_date' or a date or a tenor or timedelta
                           if left as None the
                           trades will be added for all future dates
                           can also specify 'next schedule' in order to exit at the next periodic trigger date
    :param name: optional additional name to the priceable name
    :param scaling_risk: if the scaling type is a measure then this is the definition of the measure
    :param total_size: the total notional that we are scaling to
    :param transaction_cost: optional a cash amount paid for each transaction
    :param transaction_cost_exit: optionally specify a different model for exits; defaults to entry cost if None
    :param holiday_calendar: optional an iterable list of holiday dates
    """

    priceables: Portfolio = None
    trade_duration: Duration = None
    name: str = None
    scaling_risk: RiskMeasure = None
    total_size: float = 100000.0
    transaction_cost: TransactionModel = field(default_factory=default_transaction_cost)
    transaction_cost_exit: Optional[TransactionModel] = None
    holiday_calendar: Iterable[dt.date] = None
    class_type: str = static_field('add_weighted_trade_action')

    def __post_init__(self):
        super().__post_init__()
        self._calc_type = CalcType.semi_path_dependent
        # pricebt DEV-T16: see AddTradeAction above.
        if isinstance(self.holiday_calendar, list):
            self.holiday_calendar = tuple(self.holiday_calendar)
        self.priceables = _rename_priceables(self.name, make_list(self.priceables))
        if self.transaction_cost_exit is None:
            self.transaction_cost_exit = self.transaction_cost


@dataclass
class EnterPositionQuantityScaledAction(Action):
    """
    create an action which enters trades when triggered.  The trades are executed with specified quantity and
    last until the trade_duration if specified, or for all future dates if not.
    :param priceables: a priceable or a list of pricables.
    :param trade_duration: an instrument attribute eg. 'expiration_date' or a date or a tenor if left as None the
                           trade will be added for all future dates
    :param name: optional additional name to the priceable name
    :param trade_quantity: the amount, in units of trade_quantity_type to be traded
    :param trade_quantity_type: the quantity type used to scale trade. eg. quantity for units, notional for
                                underlier notional
    :param transaction_cost: optional a cash amount paid for each transaction
    :param transaction_cost_exit: optionally specify a different model for exits; defaults to entry cost if None
    """

    priceables: Union[Priceable, Iterable[Priceable]] = None
    trade_duration: Duration = None
    name: str = None
    trade_quantity: Union[float, dict[dt.date, Union[float, int]]] = 1
    trade_quantity_type: BacktestTradingQuantityType = BacktestTradingQuantityType.quantity
    transaction_cost: TransactionModel = field(default_factory=default_transaction_cost)
    transaction_cost_exit: Optional[TransactionModel] = None
    class_type: str = static_field('enter_position_quantity_scaled_action')

    def __post_init__(self):
        super().__post_init__()
        self.priceables = _rename_priceables(self.name, make_list(self.priceables))
        if self.transaction_cost_exit is None:
            self.transaction_cost_exit = self.transaction_cost


@dataclass
class ExitPositionAction(Action):
    name: str = None
    class_type: str = 'exit_position_action'


@dataclass
class ExitTradeAction(Action):
    priceable_names: Union[str, Iterable[str]] = None
    name: str = None
    transaction_cost: TransactionModel = field(default_factory=default_transaction_cost)
    class_type: str = static_field('exit_trade_action')

    def __post_init__(self):
        super().__post_init__()
        self.priceables_names = make_list(self.priceable_names)
        # pricebt DEV-E10: gs's real field (`priceable_names`) is left exactly as given, so a bare
        # string is later substring-matched by the `in` checks in generic_engine_action_impls.py's
        # exit-matching logic (a str is iterable of its own characters). gs also sets a typo'd,
        # never-read attribute `priceables_names` above (kept for exact parity with gs's own
        # __post_init__, though nothing reads it -- research/02 section 2.3). Normalise the real
        # field too, so a bare string (or any other non-None iterable) becomes a list -- but leave
        # `None` as `None`: ExitTradeActionImpl.apply_action (P3.5,
        # generic_engine_action_impls.py:440,453) branches on `is None` ("no names given -> exit
        # every trade currently held") vs. truthiness ("only these named trades"), and
        # ExitAllPositionsAction() relies on that `is None` sentinel to mean "exit everything".
        # Turning `None` into `[]` would make both branches false and crash with
        # UnboundLocalError('current_trade_names') the first time anyone calls
        # ExitAllPositionsAction() with no args.
        if self.priceable_names is not None:
            self.priceable_names = make_list(self.priceable_names)


@dataclass
class ExitAllPositionsAction(ExitTradeAction):
    """
    Fully exit all held positions
    """

    class_type: str = static_field('exit_all_positions_action')

    def __post_init__(self):
        super().__post_init__()
        self._calc_type = CalcType.path_dependent


@dataclass
class HedgeAction(Action):
    """
    create an action which adds a hedge trade when triggered.  This trade will be scaled to hedge the risk
    specified.  The trades are resolved on the trigger date (state) and
    last until the trade_duration if specified or for all future dates if not.
    :param risk: a risk measure which should be hedged
    :param priceables: a priceable or a list of pricables these should have sensitivity to the risk.
    :param trade_duration: an instrument attribute eg. 'expiration_date' or a date or a tenor or timedelta
                           if left as None the
                           trade will be added for all future dates
                           can also specify 'next schedule' in order to exit at the next periodic trigger date
    :param name: optional additional name to the priceable name
    :param transaction_cost: optional a cash amount paid for each transaction
    :param transaction_cost_exit: optionally specify a different model for exits; defaults to entry cost if None
    :param risk_transformation: optional a Transformer which will be applied to the raw risk numbers before hedging
    :param holiday_calendar: optional an iterable list of holiday dates
    :param risk_percentage: proportion of risk to hedge expressed as a percentage. Default is 100%
    """

    risk: RiskMeasure = None
    priceables: Optional[Priceable] = None
    trade_duration: Duration = None
    name: str = None
    csa_term: str = None
    scaling_parameter: str = 'notional_amount'
    transaction_cost: TransactionModel = field(default_factory=default_transaction_cost)
    transaction_cost_exit: Optional[TransactionModel] = None
    risk_transformation: Transformer = None
    holiday_calendar: Iterable[dt.date] = None
    risk_percentage: float = 100
    class_type: str = static_field('hedge_action')

    def __post_init__(self):
        super().__post_init__()
        self._calc_type = CalcType.semi_path_dependent
        portfolio = (
            self.priceables
            if isinstance(self.priceables, Portfolio)
            else Portfolio(self.priceables.clone(name=None), name=self.priceables.name)
            if isinstance(self.priceables, Priceable)
            else None
        )

        # pricebt DEV-E7: gs's `if not Portfolio:` tests the CLASS object itself, which is never
        # falsy, so the guard never fires; a `priceables` that is neither a Portfolio nor a
        # Priceable then crashes later, at `enumerate(None)`, with a bare TypeError. Fixed to the
        # check gs clearly intended.
        if portfolio is None:
            raise RuntimeError('hedge action only accepts one trade or one portfolio')

        # pricebt DEV-T16: see AddTradeAction above.
        if isinstance(self.holiday_calendar, list):
            self.holiday_calendar = tuple(self.holiday_calendar)

        # pricebt DEV-E5: set_meta=False -- a hedge leg's `position_meta` stays the
        # `Instrument.__init__` default of None. R4's hedge rename (research/02 section 2.4) embeds
        # the create date into the *wrapping portfolio's* name at execution time
        # (generic_engine.py, P3.5), never into this leg's own position_meta[2] the way R2/R3 do for
        # ordinary trades, so a hedge leg's position_meta third element would stay None forever.
        # ExitTradeAction(priceable_names=...) must therefore never match a hedge leg by name --
        # DESIGN.md section 11 DEV-E5: "Positions with no meta (hedges) are never matched by name."
        named_priceables = _rename_priceables(self.name, portfolio, set_meta=False)
        named_priceable = Portfolio(named_priceables, name=portfolio.name)

        self.priceables = named_priceable
        if self.transaction_cost_exit is None:
            self.transaction_cost_exit = self.transaction_cost

        if self.scaling_parameter != 'notional_amount':
            warnings.warn(
                'HedgeAction.scaling_parameter is deprecated. It is no longer used and will be removed '
                'in a future release',
                DeprecationWarning,
                stacklevel=2,
            )

    @property
    def priceable(self):
        return self.priceables


@dataclass
class RebalanceAction(Action):
    priceable: Priceable = None
    size_parameter: Union[str, float] = None
    method: Callable = None
    transaction_cost: TransactionModel = field(default_factory=default_transaction_cost)
    transaction_cost_exit: Optional[TransactionModel] = None
    name: str = None

    def __post_init__(self):
        super().__post_init__()
        self._calc_type = CalcType.path_dependent
        if self.priceable.unresolved is None:
            raise ValueError("Please specify a resolved priceable to rebalance.")
        if self.priceable is not None:
            original_name = self.priceable.name if self.priceable.name is not None else 'Priceable0'
            if self.priceable.name is None:
                self.priceable = self.priceable.clone(name=f'{self.name}_Priceable0')
            else:
                self.priceable = self.priceable.clone(name=f'{self.name}_{self.priceable.name}')
            # pricebt DEV-E5: RebalanceAction's own construction-time rename above always renames
            # (no `startswith` exception, unlike rule R1's _rename_priceables above -- this rename
            # has no rule id of its own; R7 in research/02 section 2.4 is a different, later,
            # execution-time rename in generic_engine.py, owned by P3.5), so set position_meta
            # directly here.
            self.priceable.position_meta = (self.name, original_name, None)
        if self.transaction_cost_exit is None:
            self.transaction_cost_exit = self.transaction_cost


@dataclass
class EarlyExitPositionLimitScaledAction(AddScaledTradeAction):
    """
    Subclass of AddScaledTradeAction that holds information on early exits to precompute its trade final dates.
    It also holds a limit on the number of positions that can be held at the time and filters orders accordingly.

    :param early_exits: list of dates when the action portfolio is exit early
    :param max_concurrent_pos: maximum number of positions that can be held a time - a portfolio is added as a whole
    """

    early_exits: Optional[Iterable[dt.date]] = None
    max_concurrent_pos: Optional[int] = None
    class_type: str = static_field('early_exit_position_limit_scaled_action')
