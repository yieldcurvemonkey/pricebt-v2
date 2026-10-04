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
# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: DEV-R1, DEV-R2, DEV-R4,
# DEV-R11, DEV-E12, DEV-E14, DEV-E15, DEV-E19, DEV-E20, DEV-E21, DEV-E22; pricebt additions
# BackTest.pnl_explain_table, ir_pnl_definition, swaption_pnl_definition, bond_pnl_definition
#
# BackTest (+ pnl_bps, missing_market_dates/missing_market_moves -- pricebt-only additions, no DEV
# marker: they are new API, not a deviation from existing gs behaviour), ScalingPortfolio,
# WeightedScalingPortfolio, the transaction-cost models, TransactionCostEntry, CashPayment, Hedge,
# WeightedTrade, the cash accrual models, PnlAttribute/PnlDefinition, fx_pnl_definition() (ported
# verbatim) and the _BACKTEST_END ContextVar (DEV-E12). PredefinedAssetBacktest and
# OisFixingCashAccrualModel are stubs (DESIGN.md section 2.3 / section 9.3): PredefinedAssetEngine
# and the GS OIS-fixing API are out of scope for pricebt v2; triggers.py (P3.4) imports
# PredefinedAssetBacktest by name, so it must exist even though constructing it raises.
from __future__ import annotations

import datetime as dt
import functools
from abc import ABC
from collections import defaultdict
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, ClassVar, Iterable, Optional, TypeVar, Union

import numpy as np
import pandas as pd

from ..base import field_metadata, static_field
from ..common import AggregationLevel, ParameterisedRiskMeasure, RiskMeasure
from ..errors import NotSupportedError
from ..instrument import Instrument
from ..markets import PricingContext
from ..markets.portfolio import Portfolio
from ..risk import (
    Cashflows,
    ErrorValue,
    ExpiryInYears,
    FXAnnualImpliedVol,
    FXDeltaLocalCcy,
    FXGammaLocalCcy,
    FXSpot,
    FXVegaLocalCcy,
    IRAnnualImpliedVol,
    IRDeltaParallel,
    IRFwdRate,
    IRGammaParallel,
    IRVanna,
    IRVegaParallel,
    IRVolga,
    Theta,
)
from ..risk.results import FloatWithInfo, PortfolioRiskResult, PricingFuture, _is_table
from ..risk.transform import Transformer
from .backtest_utils import make_list
from .data_sources import DataSource


# pricebt addition (not in gs; DESIGN.md section 9.4 / DEV-E12): the engine's run_backtest sets this
# to the run's last backtest state (backtest.states[-1]) for the duration of the run, and resets it
# to None in a `finally`. ScaledTransactionModel.get_unit_cost reads it below, in place of gs's
# `state > dt.date.today()` guard, so a historical backtest's own transaction-cost pricing no longer
# depends on the wall-clock date it happens to be run on. Setting/resetting it is GenericEngine's
# job (P3.5); this file only defines it and reads it.
_BACKTEST_END: "ContextVar[Optional[dt.date]]" = ContextVar('_BACKTEST_END', default=None)

# pricebt DEV-E18: same pattern as _BACKTEST_END above. run_backtest's result_ccy rewrite
# (generic_engine.py) converts the run's `risks` list and price measure to `r(currency=result_ccy)`
# but has no way to reach into an action's own `transaction_cost`/`transaction_cost_exit` models --
# a ScaledTransactionModel(scaling_type=<RiskMeasure>) is a plain dataclass with no run context.
# Without this, its scaling_type is priced in whatever currency the (unparameterised) measure
# defaults to (decision 0.5's function/asset currency), never result_ccy, so under a multi-currency
# run the booked Transaction Costs can be silently in a different currency than Price/Cumulative
# Cash while Total sums all three as if they matched. GenericEngine sets/resets this for the run's
# duration (P3.5, alongside _BACKTEST_END); ScaledTransactionModel.get_unit_cost reads it below.
_RESULT_CCY: "ContextVar[Optional[str]]" = ContextVar('_RESULT_CCY', default=None)


class BaseBacktest(ABC):
    pass


TBaseBacktest = TypeVar('TBaseBacktest', bound='BaseBacktest')


class TransactionAggType(Enum):
    SUM = 'sum'
    MAX = 'max'
    MIN = 'min'


@dataclass
class PnlAttribute:
    attribute_name: str
    attribute_metric: RiskMeasure
    market_data_metric: RiskMeasure
    scaling_factor: float
    second_order: bool = False
    # pricebt DEV-E19: appended (positional gs calls unchanged). When set, the step P&L is
    # scaling_factor * risk(t-1) * dm1 * dm2 (no 1/2; e.g. vanna), m2 read exactly like m1.
    cross_market_data_metric: Optional[RiskMeasure] = None
    # pricebt DEV-E21: appended. When set, every level read for this attribute must be a value whose
    # unit is {unit: 1}, else pnl_explain raises (a wrong unit scales the P&L by 1e4 silently).
    market_data_unit: Optional[str] = None
    cross_market_data_unit: Optional[str] = None

    def __post_init__(self):
        if self.second_order and self.cross_market_data_metric is not None:
            raise ValueError(f"{self.attribute_name}: second_order cannot be combined with cross_market_data_metric")

    def get_risks(self):
        # pricebt DEV-E19: the cross metric too; None metrics are left out
        risks = (self.attribute_metric, self.market_data_metric, self.cross_market_data_metric)
        return [r for r in risks if r is not None]


@dataclass
class PnlDefinition:
    attributes: Iterable[PnlAttribute]

    def get_risks(self):
        return [risk for attribute in self.attributes for risk in attribute.get_risks()]


def _level(attribute: PnlAttribute, results, measure: RiskMeasure, unit: Optional[str], instrument):
    """`results[measure]`, checked against the attribute's declared unit (pricebt DEV-E21)."""
    value = results[measure]
    if unit is not None and isinstance(value, FloatWithInfo) and value.unit != {unit: 1}:
        raise ValueError(
            f"{attribute.attribute_name}: {measure} on {getattr(instrument, 'name', instrument)} has unit"
            f" {value.unit}; the definition expects {unit}"
        )
    return value


def _attribute_step_pnl(attribute: PnlAttribute, held) -> float:
    """One attribute's P&L over one step of BackTest._explain_steps: gs's per-instrument formula,
    verbatim, plus the DEV-E19 cross term. A zero risk never reads a level (gs)."""
    metric_pnl = 0.0
    for prev_date_inst, prev_results, cur_results in held:
        prev_date_risk = prev_results[attribute.attribute_metric]
        if prev_date_risk == 0:
            continue
        unit = attribute.market_data_unit
        prev_date_mkt_data = _level(attribute, prev_results, attribute.market_data_metric, unit, prev_date_inst)
        cur_date_mkt_data = _level(attribute, cur_results(), attribute.market_data_metric, unit, prev_date_inst)
        if attribute.second_order:
            metric_pnl += (
                0.5
                * attribute.scaling_factor
                * prev_date_risk
                * (cur_date_mkt_data - prev_date_mkt_data)
                * (cur_date_mkt_data - prev_date_mkt_data)
            )
        elif attribute.cross_market_data_metric is not None:
            # pricebt DEV-E19: the cross metric is read like the first one (exit results on an exit date)
            cross, cross_unit = attribute.cross_market_data_metric, attribute.cross_market_data_unit
            prev_date_cross = _level(attribute, prev_results, cross, cross_unit, prev_date_inst)
            cur_date_cross = _level(attribute, cur_results(), cross, cross_unit, prev_date_inst)
            metric_pnl += (
                attribute.scaling_factor
                * prev_date_risk
                * (cur_date_mkt_data - prev_date_mkt_data)
                * (cur_date_cross - prev_date_cross)
            )
        else:
            metric_pnl += attribute.scaling_factor * prev_date_risk * (cur_date_mkt_data - prev_date_mkt_data)
    return metric_pnl


def _cash_due(table, prev_date: dt.date, cur_date: dt.date):
    """The rows of a Cashflows table with prev_date < payment_date <= cur_date."""
    if not len(table):
        return table
    paid = pd.to_datetime(table["payment_date"])
    return table.loc[(paid > pd.Timestamp(prev_date)) & (paid <= pd.Timestamp(cur_date))]


@dataclass
class BackTest(BaseBacktest):
    CUMULATIVE_CASH_COLUMN: ClassVar[str] = "Cumulative Cash"
    TRANSACTION_COSTS_COLUMN: ClassVar[str] = "Transaction Costs"
    TOTAL_COLUMN: ClassVar[str] = "Total"

    strategy: object
    states: Iterable
    risks: Iterable[RiskMeasure]
    price_measure: RiskMeasure
    holiday_calendar: Iterable[dt.date] = None
    pnl_explain_def: Optional[PnlDefinition] = None

    def __post_init__(self):
        self._portfolio_dict = defaultdict(Portfolio)  # portfolio by state
        self._cash_dict = {}  # cash by state
        self._hedges = defaultdict(list)  # list of Hedge by date
        self._weighted_trades = defaultdict(list)  # list of WeightedTrade by date
        self._cash_payments = defaultdict(list)  # list of cash payments (entry, unwind)
        self._transaction_costs = defaultdict(int)  # list of transaction costs by date
        self._transaction_cost_entries = defaultdict(list)  # entries tracking transaction costs by state
        self.strategy = deepcopy(self.strategy)  # the strategy definition
        self._results = defaultdict(list)
        self._trade_exit_risk_results = defaultdict(list)
        self.risks = make_list(self.risks)  # list of risks to calculate
        self._risk_summary_dict = None  # Summary dict shared between output views, only initialized once
        self._calc_calls = 0
        self._calculations = 0
        # pricebt additions (DESIGN.md section 9.5; not in gs): populated by GenericEngine's
        # missing_market='drop' grid-date policy and its off-grid valuation-date rolling (P3.5).
        # Empty when nothing was dropped/rolled.
        self.missing_market_dates = []
        self.missing_market_moves = []
        # pricebt DEV-E22: {date: {position name: (currency, cashflow, financing)}}, the holding cash
        # GenericEngine booked on that date for each financed position (empty without one)
        self.holding_cash = defaultdict(dict)

    @property
    def cash_dict(self):
        return self._cash_dict

    @property
    def portfolio_dict(self):
        return self._portfolio_dict

    @portfolio_dict.setter
    def portfolio_dict(self, portfolio_dict):
        self._portfolio_dict = portfolio_dict

    @property
    def cash_payments(self):
        return self._cash_payments

    @cash_payments.setter
    def cash_payments(self, cash_payments):
        self._cash_payments = cash_payments

    @property
    def transaction_costs(self):
        return self._transaction_costs

    @transaction_costs.setter
    def transaction_costs(self, transaction_costs):
        self._transaction_costs = transaction_costs

    @property
    def transaction_cost_entries(self):
        return self._transaction_cost_entries

    @property
    def hedges(self):
        return self._hedges

    @hedges.setter
    def hedges(self, hedges):
        self._hedges = hedges

    @property
    def weighted_trades(self):
        return self._weighted_trades

    @weighted_trades.setter
    def weighted_trades(self, weighted_trades):
        self._weighted_trades = weighted_trades

    @property
    def results(self):
        return self._results

    def set_results(self, date, results):
        self._results[date] = results

    def add_results(self, date, results, replace=False):
        if date in self._results and len(self._results[date]) and not replace:
            self._results[date] += results
        else:
            self._results[date] = results

    @property
    def trade_exit_risk_results(self):
        return self._trade_exit_risk_results

    @property
    def calc_calls(self):
        return self._calc_calls

    @calc_calls.setter
    def calc_calls(self, calc_calls):
        self._calc_calls = calc_calls

    @property
    def calculations(self):
        return self._calculations

    @calculations.setter
    def calculations(self, calculations):
        self._calculations = calculations

    # pricebt DEV-R1/DEV-R4: a new helper (not in gs) that get_risk_summary_df uses below to zero
    # a flat date instead of ffilling it (R03 section 4.4 quirk Q1's fix).
    def _flat_dates(self) -> set:
        """The set of dates on which nothing is held, per DESIGN.md section 11's DEV-R1 row. A
        grid date (a member of `self.states`) is flat iff `portfolio_dict[d]` holds no
        instruments. A non-grid date (an off-grid cash or transaction-cost date, e.g. an exit
        priced between two grid dates) is flat iff GenericEngine's `_handle_cash` (P3.5) has not
        booked a priced, non-empty result for it -- when it prices a continuing position into
        `results[d]` for such a date, that date is not flat and shows its own real values instead."""
        grid = set(self.states)
        flat = {d for d in grid if len(self._portfolio_dict.get(d, ())) == 0}
        # pricebt DEV-R1: bounded to self.states[-1] so an unclosed position's placeholder exit
        # (trade_duration=None -> dt.date.max, never priced) is not mistaken for a flat date --
        # that placeholder is not a real off-grid pricing date and gs's own zero_on_empty_dates
        # block (which risk_summary relies on) never produces a row for it either.
        off_grid_candidates = {
            d for d in (set(self._cash_dict) | set(self._transaction_costs)) - grid if d <= self.states[-1]
        }
        for d in off_grid_candidates:
            if not len(self._results.get(d, ())):
                flat.add(d)
        return flat

    def get_risk_summary_df(self, zero_on_empty_dates=False):
        # pricebt DEV-R2: gs builds summary_dict once into self._risk_summary_dict and never
        # invalidates it, so a later change to self.results has no effect on an already-cached
        # view. pricebt recomputes summary_dict on every call instead.
        if not self._results:
            return pd.DataFrame(columns=self.risks)
        dates_with_results = list(filter(lambda x: len(x[1]), self._results.items()))
        summary_dict = defaultdict(dict)
        # pricebt DEV-R11: a table measure (its results carry the `pricebt_table` marker; never
        # found by name) has no summary cell, so it is left out here and in every fill below.
        table_risks = set()
        for date, results in dates_with_results:
            for risk in results.risk_measures:
                if risk in table_risks:
                    continue
                try:
                    value = results[risk].aggregate(True, True)
                except TypeError:
                    value = ErrorValue(None, error='Could not aggregate risk results')
                if _is_table(value):
                    table_risks.add(risk)
                    continue
                summary_dict[date][risk] = value
        risks = [r for r in self.risks if r not in table_risks]
        self._risk_summary_dict = summary_dict
        zero_risk_sd_copy = summary_dict.copy()
        # pricebt DEV-R1/DEV-R4: a flat date (see _flat_dates) gets 0 for every risk -- including a
        # bucketed one -- instead of being left absent, which result_summary's later ffill() would
        # otherwise fill in with the previous date's stale PV/ladder (R03 section 4.4 quirk Q1).
        # Unlike gs's own zero_on_empty_dates block below, this runs unconditionally, because it is
        # what fixes result_summary (which calls this with zero_on_empty_dates=False).
        for flat_date in self._flat_dates() - set(zero_risk_sd_copy):
            for risk in risks:
                zero_risk_sd_copy[flat_date][risk] = 0
        if zero_on_empty_dates:
            for cash_only_date in set(self._cash_dict.keys()).difference(zero_risk_sd_copy.keys()):
                for risk in risks:
                    zero_risk_sd_copy[cash_only_date][risk] = 0
        result = pd.DataFrame(zero_risk_sd_copy).T.sort_index()
        # pricebt DEV-E14: reindex columns to self.risks (an ordered list, DESIGN.md section 8.3)
        # instead of gs's arbitrary column order (whatever pd.DataFrame(dict_of_dicts) happens to
        # produce from the per-date risk keys it was built from). pricebt DEV-R11: tables left out.
        result = result.reindex(columns=risks)
        return result

    @property
    def result_summary(self):
        """
        Get a dataframe showing the PV and other risks and cash on each day in the backtest
        """
        summary = self.get_risk_summary_df()
        cash_summary = defaultdict(dict)
        for date, results in self._cash_dict.items():
            for ccy, value in results.items():
                cash_summary[f'Cumulative Cash {ccy}'][date] = value
        if len(cash_summary) > 1:
            # pricebt DEV-E15: gs's raw message gives no indication of the fix; pricebt appends a
            # hint naming the parameter that resolves it (DESIGN.md section 7 point 4).
            raise RuntimeError(
                'Cannot aggregate cash in multiple currencies'
                ' — pass result_ccy=... (pricebt converts with your FX config)'
            )
        elif len(cash_summary) == 1:
            cash = pd.concat(
                [pd.Series(cash_dict, name=self.CUMULATIVE_CASH_COLUMN) for name, cash_dict in cash_summary.items()],
                axis=1,
                sort=True,
            )
        else:
            cash = pd.DataFrame(columns=[self.CUMULATIVE_CASH_COLUMN])
        transaction_costs = pd.Series(self.transaction_costs, name=self.TRANSACTION_COSTS_COLUMN)
        transaction_costs = transaction_costs.sort_index().cumsum()
        df = pd.concat([summary, cash, transaction_costs], axis=1, sort=True).ffill().fillna(0)
        df[self.TOTAL_COLUMN] = (
            df[self.price_measure] + df[self.CUMULATIVE_CASH_COLUMN] + df[self.TRANSACTION_COSTS_COLUMN]
        )
        if df.empty:
            result = df
        else:
            result = df.loc[: self.states[-1]]
        return result

    @property
    def risk_summary(self):
        """
        Get a dataframe showing the risks in the backtest with zero values for days with no instruments held
        """
        return self.get_risk_summary_df(zero_on_empty_dates=True)

    def trade_ledger(self):
        # this is a ledger of each instrument when it was entered and when it was closed out.  The cash associated
        # with the entry and exit are used in the open value and close value and PnL calc.  If the PnL is None it
        # means the instrument is still live and therefore will show up in the PV
        ledger = {}
        names = []
        for date in sorted(self.cash_payments.keys()):
            cash_list = self.cash_payments[date]
            for cash in cash_list:
                if cash.direction == 0:
                    ledger[cash.trade.name] = {
                        'Open': date,
                        'Close': date,
                        'Open Value': 0,
                        'Close Value': 0,
                        'Long Short': cash.direction,
                        'Status': 'closed',
                        'Trade PnL': 0,
                    }
                elif cash.trade.name in names:
                    if len(cash.cash_paid) > 0:
                        ledger[cash.trade.name]['Close'] = date
                        ledger[cash.trade.name]['Close Value'] += sum(cash.cash_paid.values())
                        open_value = ledger[cash.trade.name]['Open Value']
                        ledger[cash.trade.name]['Trade PnL'] = ledger[cash.trade.name]['Close Value'] + open_value
                        ledger[cash.trade.name]['Status'] = 'closed'
                else:
                    names.append(cash.trade.name)
                    ledger[cash.trade.name] = {
                        'Open': date,
                        'Close': None,
                        'Open Value': sum(cash.cash_paid.values()),
                        'Close Value': 0,
                        'Long Short': cash.direction,
                        'Status': 'open',
                        'Trade PnL': None,
                    }
        return pd.DataFrame(ledger).T.sort_index()

    def strategy_as_time_series(self):
        """
        Get a dataframe indexed by strategy dates and instruments present on the respective dates
        For each tradable, displays calculated risk measures, cash payment amount and ccy (if any) and static data.
        """
        # Construct a table of cash payments for each date and concat them in a single table of all cash payments
        cp_table = pd.concat(
            [pd.concat([cp.to_frame() for cp in date_payments]) for _, date_payments in self.cash_payments.items()]
        )
        cp_table = cp_table.set_index(['Pricing Date', 'Instrument Name']).sort_index()
        cp_table.columns = pd.MultiIndex.from_product([['Cash Payments'], cp_table.columns])

        # pricebt DEV-R11: the `value` pivot leaves table measures out
        risk_measure_dict = {
            date: risk_res.to_frame(values='value', index='instrument_name', columns='risk_measure').assign(
                pricing_date=[date] * len(risk_res)
            )
            for date, risk_res in self.results.items()
        }
        risk_measure_table = pd.concat(risk_measure_dict.values())
        risk_measure_table = risk_measure_table.reset_index()
        risk_measure_table = risk_measure_table.rename(
            columns={'pricing_date': 'Pricing Date', 'instrument_name': 'Instrument Name'}
        )
        risk_measure_table = risk_measure_table.set_index(['Pricing Date', 'Instrument Name'])
        risk_measure_table.columns = pd.MultiIndex.from_product(
            [['Risk Measures'], [str(col) for col in risk_measure_table.columns]]
        )

        risk_and_cp_joined = risk_measure_table.join(cp_table, how='outer')

        static_inst_info = pd.concat([info.portfolio.to_frame() for info in self.results.values()])
        static_inst_info = static_inst_info.rename(columns={'name': 'Instrument Name'})
        static_inst_info = static_inst_info.set_index(['Instrument Name'])
        static_inst_info = static_inst_info[~static_inst_info.index.duplicated(keep='first')]
        static_inst_info.columns = pd.MultiIndex.from_product([['Static Instrument Data'], static_inst_info.columns])

        result = static_inst_info.join(risk_and_cp_joined, how='outer')

        return result.sort_index()

    def pnl_explain(self):
        """
        Get a dictionary of risk attributions which explain the pnl
        """
        if self.pnl_explain_def is None:
            return None

        attributes = list(self.pnl_explain_def.attributes)
        results = [{} for _ in attributes]
        cum_totals = [0.0 for _ in attributes]
        for _, cur_date, held in self._explain_steps():
            for i, attribute in enumerate(attributes):
                if held is not None:
                    cum_totals[i] += _attribute_step_pnl(attribute, held)
                results[i][cur_date] = cum_totals[i]
        pnl_explain_results = {}
        for attribute, result in zip(attributes, results):
            pnl_explain_results[attribute.attribute_name] = result
        return pnl_explain_results

    def _explain_steps(self):
        """The step iterator pnl_explain() and pnl_explain_table() share (IR_RISK_DESIGN R2-17; gs's
        loop, factored out). Yields (prev_date, cur_date, held) per step over the union of result
        and exit-result dates; `held` is None when prev_date has no results (gs skips the step),
        else one (instrument, its results on prev_date, cur) per instrument of results[prev_date],
        where `cur()` gives its results on cur_date -- from results[cur_date] while still held,
        else from trade_exit_risk_results[cur_date] -- looked up only when called (as gs, which
        reads it only for a non-zero risk) and at most once per step.

        pricebt DEV-E20: an exit on an off-grid date while other positions continue attributes
        correctly, because DEV-R1 prices the continuing positions into results[that date]; gs has
        no result there and raises KeyError (R15 S3) or drops the interval (S6)."""
        risk_results = self.results
        exit_risk_results = self.trade_exit_risk_results
        dates = sorted(set(risk_results.keys()).union(exit_risk_results.keys()))

        # pricebt DEV-E20: after an off-grid exit a continuing position is found in results[cur_date]
        # (DEV-R1 priced it there), the exiting one in the exit results; gs does not find the
        # continuing one (KeyError, R15 S3)
        def results_on(cur_date, inst):
            if cur_date in risk_results and inst in risk_results[cur_date].portfolio:
                return risk_results[cur_date][inst]
            return exit_risk_results[cur_date][inst]

        for idx in range(1, len(dates)):
            cur_date = dates[idx]
            prev_date = dates[idx - 1]
            if prev_date not in risk_results:
                yield prev_date, cur_date, None
                continue
            prev = risk_results[prev_date]
            yield prev_date, cur_date, [
                (inst, prev[inst], functools.cache(functools.partial(results_on, cur_date, inst)))
                for inst in prev.portfolio.all_instruments
            ]

    def pnl_explain_table(self) -> Optional[pd.DataFrame]:
        """pricebt addition (IR_RISK_DESIGN section 7.3, R2-17): pnl_explain() per step, next to the
        P&L it explains. Indexed by pnl_explain()'s step dates, over the same held set (the
        instruments of results[t-1], valued at t from results[t] or the exit results). None without
        a PnlDefinition.

        Columns: `actual_pnl` (the sum of price_measure(t) - price_measure(t-1) over the held
        book), `cashflow_pnl` (the `payment_amount`s of the Cashflows held at t-1 paid in (t-1, t];
        0.0 when Cashflows is not among the risks; for a financed position, the coupons the engine
        booked as holding cash on t, pricebt DEV-E22), `financing_pnl` (the change of a financed
        position's FinancingToDate the engine booked on t; 0.0 for every other position),
        `economic_pnl` (actual + cashflow + financing), one column per attribute (its P&L over the
        step, so the column's cumsum is exactly pnl_explain()'s cumulative value), `explained_pnl`
        (the sum of the attributes plus financing_pnl: financing is known cash, not a market move)
        and `residual_pnl` (economic - explained). For a book of financed positions with no
        transaction costs, economic_pnl sums to the change in result_summary's Total.

        Raises ValueError when two attributes share a name or one is named like a fixed column, and
        when a step would add amounts in different units (the held book's price_measure units and
        its due Cashflows' `currency`), as result_summary does for different units on one date."""
        if self.pnl_explain_def is None:
            return None
        attributes = list(self.pnl_explain_def.attributes)
        names = [attribute.attribute_name for attribute in attributes]
        fixed = ['actual_pnl', 'cashflow_pnl', 'financing_pnl', 'economic_pnl', 'explained_pnl', 'residual_pnl']
        clashes = sorted({name for name in names if names.count(name) > 1 or name in fixed})
        if clashes:
            raise ValueError(f"PnlAttribute names must be unique and not one of {fixed}; got {clashes}")
        by_name = dict(zip(names, attributes))
        cashflows = Cashflows if Cashflows in self.risks else None
        rows, index = [], []
        for prev_date, cur_date, held in self._explain_steps():
            actual = cash = financing = 0.0
            steps = dict.fromkeys(by_name, 0.0)
            if held is not None:
                units, currencies = set(), set()
                booked = self.holding_cash.get(cur_date, {})
                for inst, prev_results, cur_results in held:
                    prev, cur = prev_results[self.price_measure], cur_results()[self.price_measure]
                    units.update(getattr(prev, 'unit', None) or (), getattr(cur, 'unit', None) or ())
                    actual += float(cur) - float(prev)
                    if inst.name in booked:
                        # pricebt DEV-E22: what the engine booked for this financed position
                        ccy, coupons, funding = booked[inst.name]
                        cash += coupons
                        financing += funding
                        currencies.add(ccy)
                    elif cashflows is not None:
                        due = _cash_due(prev_results[cashflows], prev_date, cur_date)
                        if len(due):
                            cash += float(due['payment_amount'].sum())
                            currencies.update(due['currency'])
                if len(units | currencies) > 1:
                    raise ValueError(
                        f"Cannot aggregate results with different units on {cur_date}: {self.price_measure}"
                        f" in {sorted(units)}, Cashflows paid in {sorted(currencies)}"
                    )
                steps = {name: float(_attribute_step_pnl(a, held)) for name, a in by_name.items()}
            rows.append([actual, cash, financing, actual + cash + financing, *steps.values()])
            index.append(cur_date)
        table = pd.DataFrame(rows, index=index, columns=['actual_pnl', 'cashflow_pnl', 'financing_pnl', 'economic_pnl', *by_name])
        table['explained_pnl'] = table[list(by_name)].sum(axis=1) + table['financing_pnl']
        table['residual_pnl'] = table['economic_pnl'] - table['explained_pnl']
        return table

    def pnl_bps(self, risk: RiskMeasure, min_abs_risk: float = 1e-9) -> pd.DataFrame:
        """P&L expressed in bp of rate move: each row's P&L divided by the PREVIOUS row's risk
        (currency per bp). Pure pricebt addition (DESIGN.md section 8.4); no equivalent in gs, so
        no DEV marker -- this is new API, not a deviation from existing gs behaviour.

        Example: backtest.pnl_bps(IRDeltaParallel).

        Columns: 'PnL' (Total.diff()), 'Risk' (result_summary[risk].shift(1)), 'PnL (bps)',
        'Cumulative PnL (bps)'.

        'PnL (bps)' = PnL / Risk when |Risk| >= min_abs_risk, else NaN; cumulative = running sum
        treating NaN as 0.

        If `risk` is not a column and self.price_measure carries a currency parameter (a
        result_ccy run), risk(currency=that) is tried.

        Raises ValueError(f"{risk} is not a column of result_summary; add it to
        run_backtest(risks=[...])") if still absent, and ValueError(f"{risk} is not a scalar
        measure; use e.g. IRDeltaParallel") if non-zero cells are not scalars.
        """
        summary = self.result_summary
        if risk not in summary.columns:
            currency = getattr(self.price_measure, 'currency', None)
            if currency is not None and isinstance(risk, ParameterisedRiskMeasure):
                retried = risk(currency=currency)
                if retried in summary.columns:
                    risk = retried
        if risk not in summary.columns:
            # pricebt DEV-R11: a table measure is never a result_summary column; say so
            results = [r for r in self._results.values() if len(r) and risk in r.risk_measures]
            if results and _is_table(results[0][risk].aggregate(True, True)):
                raise ValueError(
                    f"{risk} is a table measure (one table per position); pnl_bps needs a scalar measure"
                    " such as IRDeltaParallel"
                )
            raise ValueError(f"{risk} is not a column of result_summary; add it to run_backtest(risks=[...])")

        risk_col = summary[risk]
        if any(isinstance(v, pd.DataFrame) for v in risk_col):
            raise ValueError(f"{risk} is not a scalar measure; use e.g. IRDeltaParallel")

        total = summary[self.TOTAL_COLUMN]
        pnl = total.diff()
        risk_shifted = risk_col.shift(1).astype(float)
        safe_risk = risk_shifted.where(risk_shifted.abs() >= min_abs_risk)
        pnl_bps_series = pnl.astype(float) / safe_risk
        cumulative = pnl_bps_series.fillna(0).cumsum()
        return pd.DataFrame(
            {
                'PnL': pnl,
                'Risk': risk_shifted,
                'PnL (bps)': pnl_bps_series,
                'Cumulative PnL (bps)': cumulative,
            }
        )

    def summary_stats(self, annualisation_factor: int = 252) -> pd.Series:
        """
        Compute summary statistics for the backtest useful for evaluating and comparing strategies.

        Returns a pandas Series with the following metrics:

        - **Total PnL**: final value of the Total performance series
        - **Total Transaction Costs**: cumulative transaction costs (always negative or zero)
        - **Total Trades**: number of trades entered during the backtest
        - **Start Date / End Date**: backtest date range
        - **Duration (days)**: calendar days in the backtest
        - **Annualised Return**: total return annualised assuming the given annualisation_factor
        - **Annualised Volatility**: standard deviation of daily P&L changes, annualised
        - **Sharpe Ratio**: annualised return / annualised volatility (assumes zero risk-free rate)
        - **Sortino Ratio**: annualised return / annualised downside deviation
        - **Max Drawdown**: largest peak-to-trough decline in the Total series
        - **Max Drawdown Duration (days)**: longest period (calendar days) spent in drawdown
        - **Calmar Ratio**: annualised return / |max drawdown|
        - **Average Daily PnL**: mean of daily PnL changes
        - **Daily PnL Std Dev**: standard deviation of daily PnL changes
        - **Best Day**: largest single-day gain
        - **Worst Day**: largest single-day loss
        - **% Positive Days**: proportion of days with positive PnL change
        - **Skewness**: skewness of daily PnL changes
        - **Kurtosis**: excess kurtosis of daily PnL changes
        - **Peak PnL**: highest Total value reached
        - **Current Drawdown**: drawdown at the end of the backtest

        :param annualisation_factor: number of business days per year (default 252)
        :return: pandas Series of summary statistics
        """
        summary = self.result_summary
        if summary.empty:
            return pd.Series(dtype=float)

        total = summary[self.TOTAL_COLUMN]
        daily_pnl = total.diff().dropna()

        # Basic info
        start_date = total.index[0]
        end_date = total.index[-1]
        duration_days = (end_date - start_date).days
        num_periods = len(daily_pnl)

        # Total PnL and transaction costs
        total_pnl = total.iloc[-1]
        total_tc = summary[self.TRANSACTION_COSTS_COLUMN].iloc[-1] if self.TRANSACTION_COSTS_COLUMN in summary else 0

        # Trade count
        try:
            ledger = self.trade_ledger()
            num_trades = len(ledger)
        except Exception:
            num_trades = np.nan

        # Annualised return and volatility
        avg_daily = daily_pnl.mean()
        std_daily = daily_pnl.std()
        ann_return = avg_daily * annualisation_factor
        ann_vol = std_daily * np.sqrt(annualisation_factor)

        # Sharpe ratio (excess return over zero risk-free rate)
        sharpe = ann_return / ann_vol if ann_vol != 0 else np.nan

        # Sortino ratio (downside deviation)
        downside = daily_pnl[daily_pnl < 0]
        downside_std = np.sqrt((downside**2).mean()) if len(downside) > 0 else 0.0
        ann_downside = downside_std * np.sqrt(annualisation_factor)
        sortino = ann_return / ann_downside if ann_downside != 0 else np.nan

        # Drawdown analysis
        running_max = total.cummax()
        drawdown = total - running_max
        max_drawdown = drawdown.min()

        # Calmar ratio
        calmar = ann_return / abs(max_drawdown) if max_drawdown != 0 else np.nan

        # Current drawdown
        current_drawdown = drawdown.iloc[-1]

        # Peak PnL
        peak_pnl = running_max.iloc[-1]

        # Max drawdown duration (longest streak below the running max)
        in_drawdown = drawdown < 0
        if in_drawdown.any():
            dd_groups = (~in_drawdown).cumsum()
            dd_durations = in_drawdown.groupby(dd_groups).apply(
                lambda g: (g.index[-1] - g.index[0]).days if g.any() else 0
            )
            max_dd_duration = dd_durations.max()
        else:
            max_dd_duration = 0

        # Best / worst day
        best_day = daily_pnl.max()
        worst_day = daily_pnl.min()

        # Percentage of positive days
        pct_positive = (daily_pnl > 0).sum() / num_periods * 100 if num_periods > 0 else np.nan

        # Higher moments
        skewness = daily_pnl.skew()
        kurtosis = daily_pnl.kurtosis()  # excess kurtosis

        stats = pd.Series(
            {
                'Start Date': start_date,
                'End Date': end_date,
                'Duration (days)': duration_days,
                'Total PnL': total_pnl,
                'Total Transaction Costs': total_tc,
                'Total Trades': num_trades,
                'Peak PnL': peak_pnl,
                'Annualised Return': ann_return,
                'Annualised Volatility': ann_vol,
                'Sharpe Ratio': sharpe,
                'Sortino Ratio': sortino,
                'Max Drawdown': max_drawdown,
                'Max Drawdown Duration (days)': max_dd_duration,
                'Calmar Ratio': calmar,
                'Current Drawdown': current_drawdown,
                'Average Daily PnL': avg_daily,
                'Daily PnL Std Dev': std_daily,
                'Best Day': best_day,
                'Worst Day': worst_day,
                '% Positive Days': pct_positive,
                'Skewness': skewness,
                'Kurtosis': kurtosis,
            }
        )

        return stats


class ScalingPortfolio:
    def __init__(
        self, trade, dates, risk, csa_term=None, risk_transformation: Transformer = None, risk_percentage: float = 100
    ):
        self.trade = trade
        self.dates = dates
        self.risk = risk
        self.csa_term = csa_term
        self.risk_transformation = risk_transformation
        self.risk_percentage = risk_percentage
        self.results = None


@dataclass(frozen=True)
class TransactionModel:
    def get_unit_cost(self, state, info, instrument) -> float:
        pass


@dataclass(frozen=True)
class ConstantTransactionModel(TransactionModel):
    cost: Union[float, int] = 0
    class_type: str = static_field('constant_transaction_model')

    def get_unit_cost(self, state, info, instrument) -> float:
        return self.cost


@dataclass(frozen=True)
class ScaledTransactionModel(TransactionModel):
    scaling_type: Union[str, RiskMeasure] = 'notional_amount'
    scaling_level: Union[float, int] = 0.0001
    class_type: str = static_field('scaled_transaction_model')

    def get_unit_cost(self, state, info, instrument) -> Union[float, PricingFuture]:
        if isinstance(self.scaling_type, str):
            try:
                return getattr(instrument, self.scaling_type)
            except AttributeError:
                raise RuntimeError(f'{self.scaling_type} not recognised for instrument {instrument.type_}')
        # pricebt DEV-E12: gs compares against dt.date.today() (real wall-clock "now"), which makes
        # a historical backtest's own transaction-cost pricing depend on when it happens to be RUN.
        # run_backtest (P3.5) sets _BACKTEST_END to this run's last state for the run's duration
        # (reset to None in a finally); outside an active run this falls back to gs's own guard.
        backtest_end = _BACKTEST_END.get()
        cutoff = backtest_end if backtest_end is not None else dt.date.today()
        if state > cutoff:
            return np.nan
        # pricebt DEV-E18: rewrite to result_ccy exactly like generic_engine.py's `risks` list does,
        # so a currency-bearing scaling_type prices (and, via PricingService, FX-converts) into the
        # same currency as Price/Cumulative Cash instead of silently staying in its own. A plain
        # (non-currency-capable) RiskMeasure is left as-is, same as the 'notional_amount'-style
        # string scaling_type above -- undocumented currency, but no worse than before this fix.
        scaling_type = self.scaling_type
        result_ccy = _RESULT_CCY.get()
        if result_ccy is not None and isinstance(scaling_type, ParameterisedRiskMeasure):
            scaling_type = scaling_type(currency=result_ccy)
        with PricingContext(state):
            risk = instrument.calc(scaling_type)
        return risk


@dataclass(frozen=True)
class AggregateTransactionModel(TransactionModel):
    transaction_models: tuple = tuple()
    aggregate_type: TransactionAggType = field(default=TransactionAggType.SUM, metadata=field_metadata)

    def get_unit_cost(self, state, info, instrument) -> float:
        if not self.transaction_models:
            return 0
        if self.aggregate_type == TransactionAggType.SUM:
            return sum(model.get_unit_cost(state, info, instrument) for model in self.transaction_models)
        elif self.aggregate_type == TransactionAggType.MAX:
            return max(model.get_unit_cost(state, info, instrument) for model in self.transaction_models)
        elif self.aggregate_type == TransactionAggType.MIN:
            return min(model.get_unit_cost(state, info, instrument) for model in self.transaction_models)
        else:
            raise RuntimeError(f'unrecognised aggregation type:{str(self.aggregation_type)}')


class TransactionCostEntry:
    """
    Stateful wrapper around TransactionModel used in the Generic Engine.
    Used to link costs to CashPayments, which can be scaled throughout the backtest (e.g. hedges), and
    to resolve risk-based costs under the same PricingContext for efficiency.
    """

    def __init__(self, date: dt.date, instrument: Instrument, transaction_model: TransactionModel):
        self._date = date
        self._instrument = instrument
        self._transaction_model = transaction_model
        self._unit_cost_by_model_by_inst = {}
        self._additional_scaling = 1

    @property
    def all_instruments(self) -> "tuple[Instrument, ...]":
        return self._instrument.all_instruments if isinstance(self._instrument, Portfolio) else (self._instrument,)

    @property
    def all_transaction_models(self):
        return (
            self._transaction_model.transaction_models
            if isinstance(self._transaction_model, AggregateTransactionModel)
            else (self._transaction_model,)
        )

    @property
    def cost_aggregation_func(self) -> Callable:
        if isinstance(self._transaction_model, AggregateTransactionModel):
            if self._transaction_model.aggregate_type is TransactionAggType.SUM:
                return sum
            if self._transaction_model.aggregate_type is TransactionAggType.MAX:
                return max
            if self._transaction_model.aggregate_type is TransactionAggType.MIN:
                return min
        return sum

    @property
    def additional_scaling(self):
        return self._additional_scaling

    @additional_scaling.setter
    def additional_scaling(self, value: float):
        self._additional_scaling = value

    @property
    def date(self):
        return self._date

    @date.setter
    def date(self, value: dt.date):
        self._date = value

    @property
    def no_of_risk_calcs(self) -> int:
        return len(
            [
                m
                for m in self.all_transaction_models
                if isinstance(m, ScaledTransactionModel) and isinstance(m.scaling_type, RiskMeasure)
            ]
        )

    def calculate_unit_cost(self):
        for m in self.all_transaction_models:
            self._unit_cost_by_model_by_inst[m] = {}
            for i in self.all_instruments:
                self._unit_cost_by_model_by_inst[m][i] = m.get_unit_cost(self._date, None, i)

    @staticmethod
    def __resolved_cost(cost: Union[float, PricingFuture]) -> float:
        if isinstance(cost, PortfolioRiskResult):
            return cost.aggregate()
        elif isinstance(cost, PricingFuture):
            return cost.result()
        return cost

    def get_final_cost(self):
        final_costs = []
        for m in self.all_transaction_models:
            # charges may net out for portfolios
            cost = sum(self.__resolved_cost(self._unit_cost_by_model_by_inst[m][i]) for i in self.all_instruments)
            if isinstance(m, ScaledTransactionModel):
                cost = m.scaling_level * abs(cost * self._additional_scaling)
            final_costs.append(cost)
        return self.cost_aggregation_func(final_costs) if final_costs else 0

    def get_cost_by_component(self) -> "tuple[float, float]":
        fixed_costs = []
        scaled_costs = []
        for m in self.all_transaction_models:
            # charges may net out for portfolios
            cost = sum(self.__resolved_cost(self._unit_cost_by_model_by_inst[m][i]) for i in self.all_instruments)
            if isinstance(m, ScaledTransactionModel):
                cost = abs(cost * m.scaling_level * self._additional_scaling)
                scaled_costs.append(cost)
            else:
                fixed_costs.append(cost)
        fixed_cost = self.cost_aggregation_func(fixed_costs) if fixed_costs else None
        scaled_cost = self.cost_aggregation_func(scaled_costs) if scaled_costs else None
        if scaled_cost is None:
            return fixed_cost, 0
        elif fixed_cost is None:
            return 0, scaled_cost

        if self.cost_aggregation_func is sum:
            return fixed_cost, scaled_cost
        else:
            # min or max
            if self.cost_aggregation_func([fixed_cost, scaled_cost]) == fixed_cost:
                return fixed_cost, 0
            elif self.cost_aggregation_func([fixed_cost, scaled_cost]) == scaled_cost:
                return 0, scaled_cost
            else:
                raise ValueError(f"Unable to split cost for aggregation function {self.cost_aggregation_func}")


class CashPayment:
    def __init__(
        self, trade, effective_date=None, direction=1, transaction_cost_entry: Optional[TransactionCostEntry] = None
    ):
        self.trade = trade
        self.effective_date = effective_date
        self.direction = direction
        self.cash_paid = defaultdict(float)
        self.transaction_cost_entry = transaction_cost_entry

    def to_frame(self):
        df = pd.DataFrame(self.cash_paid.items(), columns=['Cash Ccy', 'Cash Amount'])
        df['Instrument Name'] = self.trade.name
        df['Pricing Date'] = self.effective_date
        return df


class Hedge:
    def __init__(
        self, scaling_portfolio: ScalingPortfolio, entry_payment: CashPayment, exit_payment: Optional[CashPayment]
    ):
        self.scaling_portfolio = scaling_portfolio
        self.entry_payment = entry_payment
        self.exit_payment = exit_payment


class WeightedScalingPortfolio:
    """
    Similar to ScalingPortfolio but for weighted trade actions where each instrument
    in the portfolio is scaled to have equal risk contribution.
    """

    def __init__(
        self,
        trades: Portfolio,
        dates: list,
        risk: RiskMeasure,
        total_size: float,
        csa_term=None,
    ):
        self.trades = trades  # Portfolio of instruments to be weighted
        self.dates = dates
        self.risk = risk  # The risk measure used for weighting
        self.total_size = total_size  # Total notional to distribute equally by risk
        self.csa_term = csa_term
        self.results = None  # Will store risk calculation results


class WeightedTrade:
    """
    Represents a weighted trade entry containing the scaling portfolio and cash payments.
    Similar to Hedge but for weighted trade actions.
    """

    def __init__(
        self,
        scaling_portfolio: WeightedScalingPortfolio,
        entry_payments: list,  # List of CashPayment for each instrument
        exit_payments: list,  # List of CashPayment for each instrument (or None)
    ):
        self.scaling_portfolio = scaling_portfolio
        self.entry_payments = entry_payments
        self.exit_payments = exit_payments


class PredefinedAssetBacktest(BaseBacktest):
    """gs's PredefinedAssetEngine result object: trade_ledger (FIFO long/short order matching),
    mark_to_market, get_level, get_costs, get_orders_for_date. Out of scope for pricebt v2
    (DESIGN.md section 2.3): pricebt ports GenericEngine, not PredefinedAssetEngine. The name
    exists only because triggers.py (P3.4) imports it by name; constructing it raises."""

    def __init__(self, *args, **kwargs):
        raise NotSupportedError("PredefinedAssetBacktest is GS-server-only / out of scope in pricebt v2; use GenericEngine")


@dataclass
class CashAccrualModel:
    class_type: str = static_field('cash_accrual_model')

    def get_accrued_value(self, current_value, to_state) -> dict:
        pass


@dataclass
class ConstantCashAccrualModel(CashAccrualModel):
    rate: float = 0
    annual: bool = True
    class_type: str = static_field('cash_accrual_model')

    def get_accrued_value(self, current_value, to_state) -> dict:
        new_value = {}
        from_state = current_value[1]
        days = (to_state - from_state).days
        for currency, value in current_value[0].items():
            new_value[currency] = value * (1 + (self.rate / (365 if self.annual else 1))) ** days

        return new_value


@dataclass
class DataCashAccrualModel(CashAccrualModel):
    data_source: DataSource = field(default=None)
    annual: bool = True
    class_type: str = static_field('cash_accrual_model')

    def get_accrued_value(self, current_value, to_state) -> dict:
        new_value = {}
        from_state = current_value[1]
        days = (to_state - from_state).days
        rate = self.data_source.get_data(from_state)
        for currency, value in current_value[0].items():
            new_value[currency] = value * (1 + (rate / (365 if self.annual else 1))) ** days
        return new_value


class OisFixingCashAccrualModel(CashAccrualModel):
    """gs's OIS-fixing-driven cash accrual model: it prices an IRSwap Cashflows request against the
    GS pricing API to build its own rate series, then delegates to DataCashAccrualModel. Out of
    scope for pricebt v2 (DESIGN.md section 2.3): pricebt has no GS server. Use
    DataCashAccrualModel fed from an asset or market expression instead."""

    def __init__(self, *args, **kwargs):
        raise NotSupportedError(
            "OisFixingCashAccrualModel is GS-server-only / out of scope in pricebt v2; use DataCashAccrualModel"
        )


def fx_pnl_definition() -> PnlDefinition:
    """PnlDefinition for FX options that matches BasicBacktestRequest PnL measures.

    Produces cumulative PNL_delta, PNL_gamma, and VegaPnL series equivalent to
    the hardcoded formulas in GenericEngineBasicBacktestRunner.
    """
    return PnlDefinition(
        attributes=[
            PnlAttribute(
                attribute_name='PNL_delta',
                attribute_metric=FXDeltaLocalCcy,
                market_data_metric=FXSpot,
                scaling_factor=1.0,
                second_order=False,
            ),
            PnlAttribute(
                attribute_name='PNL_gamma',
                attribute_metric=FXGammaLocalCcy,
                market_data_metric=FXSpot,
                scaling_factor=1.0,
                second_order=True,
            ),
            PnlAttribute(
                attribute_name='VegaPnL',
                attribute_metric=FXVegaLocalCcy,
                market_data_metric=FXAnnualImpliedVol,
                scaling_factor=100.0,
                second_order=False,
            ),
        ]
    )


# pricebt addition (IR_RISK_DESIGN section 7.2): IR definitions next to fx_pnl_definition. Every
# sensitivity is per bp (bp^2) of its level (DEV-I12 contract), so a level quoted in `unit` needs
# the factor below (squared for a second-order attribute, applied once, as gs's formula does).
_UNIT_FACTORS = {"bp": 1.0, "pct": 100.0, "decimal": 1e4}


def _unit_factor(name: str, unit: str) -> float:
    if unit not in _UNIT_FACTORS:
        raise ValueError(f"{name} must be one of {sorted(_UNIT_FACTORS)}, got {unit!r}")
    return _UNIT_FACTORS[unit]


def ir_pnl_definition(
    rate_unit: str = 'bp',
    vol_unit: str = 'bp',
    *,
    delta: bool = True,
    gamma: bool = True,
    vega: bool = True,
    vanna: bool = True,
    volga: bool = True,
    theta: bool = True,
) -> PnlDefinition:
    """PnlDefinition for interest-rate instruments on the IR measure contract (IR_RISK_DESIGN
    section 2.2), with gs measure names only. `rate_unit`/`vol_unit` ('bp', 'pct' or 'decimal')
    are the units the assets' IRFwdRate and IRAnnualImpliedVol are declared in: they set each
    scaling factor, and pnl_explain checks every level read against them (DEV-E21).

    PNL_delta = IRDeltaParallel x dIRFwdRate; PNL_gamma = 1/2 IRGammaParallel x dIRFwdRate^2;
    VegaPnL = IRVegaParallel x dIRAnnualImpliedVol; PNL_vanna = IRVanna x dIRFwdRate x
    dIRAnnualImpliedVol (DEV-E19); PNL_volga = 1/2 IRVolga x dIRAnnualImpliedVol^2; PNL_theta =
    Theta (per calendar day, DEV-I15) x dExpiryInYears x -365. The keyword flags leave attributes
    out, e.g. ir_pnl_definition(vega=False, vanna=False, volga=False) for swaps.
    """
    f_r, f_v = _unit_factor('rate_unit', rate_unit), _unit_factor('vol_unit', vol_unit)
    attributes = [
        (delta, PnlAttribute('PNL_delta', IRDeltaParallel, IRFwdRate, f_r, market_data_unit=rate_unit)),
        (
            gamma,
            PnlAttribute(
                'PNL_gamma', IRGammaParallel, IRFwdRate, f_r * f_r, second_order=True, market_data_unit=rate_unit
            ),
        ),
        (vega, PnlAttribute('VegaPnL', IRVegaParallel, IRAnnualImpliedVol, f_v, market_data_unit=vol_unit)),
        (
            vanna,
            PnlAttribute(
                'PNL_vanna',
                IRVanna(aggregation_level=AggregationLevel.Type),
                IRFwdRate,
                f_r * f_v,
                cross_market_data_metric=IRAnnualImpliedVol,
                market_data_unit=rate_unit,
                cross_market_data_unit=vol_unit,
            ),
        ),
        (
            volga,
            PnlAttribute(
                'PNL_volga',
                IRVolga(aggregation_level=AggregationLevel.Type),
                IRAnnualImpliedVol,
                f_v * f_v,
                second_order=True,
                market_data_unit=vol_unit,
            ),
        ),
        (theta, PnlAttribute('PNL_theta', Theta, ExpiryInYears, -365.0)),
    ]
    return PnlDefinition(attributes=[attribute for wanted, attribute in attributes if wanted])


def swaption_pnl_definition(rate_unit: str = 'bp', vol_unit: str = 'bp') -> PnlDefinition:
    """ir_pnl_definition with all six attributes (delta, gamma, vega, vanna, volga, theta)."""
    return ir_pnl_definition(rate_unit, vol_unit)


def bond_pnl_definition(rate_unit: str = 'bp') -> PnlDefinition:
    """ir_pnl_definition with delta, gamma and theta against the bond's own yield (IRFwdRate,
    DEV-I12). Coupons are not attributed: pnl_explain_table's cashflow_pnl shows them."""
    return ir_pnl_definition(rate_unit, vega=False, vanna=False, volga=False)
