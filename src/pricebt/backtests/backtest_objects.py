"""gs_quant `backtests.backtest_objects` names: transaction-cost models, cash accrual models, PnL-explain definitions and the `Backtest`
result object returned by `GenericEngine.run_backtest`.

Transaction models keep gs's field names (`ConstantTransactionModel(cost)`, `ScaledTransactionModel(scaling_type, scaling_level)`,
`AggregateTransactionModel(transaction_models, aggregate_type)`) and convert to pricebt cost models (`pricebt.costs`) when an action is built
(`to_cost_model`). A gs `cost` field cannot live on a pricebt `CostModel` (whose `cost()` is a method), hence the conversion.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from ..accrual import wall_days
from ..costs import AggregateCost, CashAccrualModel, ConstantCost, CostModel, ScaledCost
from ..errors import NotSupportedError, StrategyError
from ..risk import RiskMeasure, as_risk_measure

__all__ = [
    "TransactionAggType", "TransactionModel", "ConstantTransactionModel", "ScaledTransactionModel", "AggregateTransactionModel", "to_cost_model",
    "CashAccrualModel", "ConstantCashAccrualModel", "DataCashAccrualModel", "OisFixingCashAccrualModel", "PnlAttribute", "PnlDefinition",
    "ResultSummary", "Backtest", "BackTest", "BaseBacktest",
]


# ============================================================================ transaction costs
_GS_SIZE_MEASURES = {"notional_amount": "notional"}  # gs attribute name -> the schema measure name


class TransactionAggType(Enum):
    SUM = "sum"
    MAX = "max"
    MIN = "min"


@dataclass(frozen=True)
class TransactionModel:
    """Base of the gs transaction models. `to_pricebt()` returns the equivalent pricebt CostModel."""

    def to_pricebt(self) -> CostModel:
        raise NotImplementedError


@dataclass(frozen=True)
class ConstantTransactionModel(TransactionModel):
    """A fixed cash amount per instrument traded (entry or exit), not scaled by size. pricebt: ConstantCost(cost_per_leg=cost)."""

    cost: Union[float, int] = 0

    def to_pricebt(self) -> CostModel:
        return ConstantCost(cost_per_leg=float(self.cost))

    def get_unit_cost(self, state: Any = None, info: Any = None, instrument: Any = None) -> float:
        return float(self.cost)


@dataclass(frozen=True)
class ScaledTransactionModel(TransactionModel):
    """scaling_level x |size metric x quantity|. `scaling_type` is a gs instrument attribute name ('notional_amount', the gs default: the schema measure
    `notional`), any measure name of the instrument, or a RiskMeasure (e.g. `Price`: cost = level x |premium|). pricebt: ScaledCost(<measure>, level)."""

    scaling_type: Union[str, RiskMeasure] = "notional_amount"
    scaling_level: Union[float, int] = 0.0001

    def to_pricebt(self) -> CostModel:
        st = self.scaling_type
        if isinstance(st, RiskMeasure):
            if st.vector:
                raise NotSupportedError(f"ScaledTransactionModel: {st!r} is a bucketed (vector) risk; scale by a scalar measure such as IRDeltaParallel")
            return ScaledCost(scaling_type=f"measure:{st.measure}", scaling_level=float(self.scaling_level))
        if not isinstance(st, str):
            raise StrategyError(f"ScaledTransactionModel.scaling_type must be an attribute name or a RiskMeasure, got {type(st).__name__}")
        return ScaledCost(scaling_type=_GS_SIZE_MEASURES.get(st, st), scaling_level=float(self.scaling_level))


@dataclass(frozen=True)
class AggregateTransactionModel(TransactionModel):
    """Combine models: SUM (default), MAX or MIN of their costs. pricebt: AggregateCost(models, 'sum'|'max'|'min')."""

    transaction_models: Tuple[TransactionModel, ...] = ()
    aggregate_type: TransactionAggType = TransactionAggType.SUM

    def __post_init__(self) -> None:
        object.__setattr__(self, "transaction_models", tuple(self.transaction_models))
        if not isinstance(self.aggregate_type, TransactionAggType):
            object.__setattr__(self, "aggregate_type", TransactionAggType(str(getattr(self.aggregate_type, "value", self.aggregate_type)).lower()))

    def to_pricebt(self) -> CostModel:
        return AggregateCost(models=tuple(to_cost_model(m) for m in self.transaction_models), aggregate=self.aggregate_type.value)


def to_cost_model(x: Any) -> Optional[CostModel]:
    """gs TransactionModel -> pricebt CostModel; pricebt CostModel and None pass through."""
    if x is None or isinstance(x, CostModel):
        return x
    if isinstance(x, TransactionModel):
        return x.to_pricebt()
    raise StrategyError(f"transaction_cost must be a gs TransactionModel, a pricebt CostModel or None, got {type(x).__name__}")


# ============================================================================ cash accrual
_wall_days = wall_days  # gs counts calendar days (`(date1 - date0).days`): the wall-clock day count of `accrual.wall_days`


@dataclass(frozen=True)
class ConstantCashAccrualModel(CashAccrualModel):
    """gs: cash grows by (1 + rate/365)**days (annual=True) or (1 + rate)**days (annual=False); rate is a decimal; days are wall-clock calendar days."""

    rate: float = 0.0
    annual: bool = True

    def interest(self, balance: float, t0: pd.Timestamp, t1: pd.Timestamp) -> float:
        days = _wall_days(t0, t1)
        r = self.rate / 365.0 if self.annual else self.rate
        return balance * ((1.0 + r) ** days - 1.0)


@dataclass(frozen=True)
class DataCashAccrualModel(CashAccrualModel):
    """gs: the accrual rate (decimal) is read from `data_source` at the START of each interval (no look-ahead)."""

    data_source: Any = None
    annual: bool = True

    def interest(self, balance: float, t0: pd.Timestamp, t1: pd.Timestamp) -> float:
        if self.data_source is None:
            raise StrategyError("DataCashAccrualModel needs a data_source")
        get = getattr(self.data_source, "get", None) or getattr(self.data_source, "get_data")
        rate = float(get(t0))
        days = _wall_days(t0, t1)
        r = rate / 365.0 if self.annual else rate
        return balance * ((1.0 + r) ** days - 1.0)


class OisFixingCashAccrualModel(CashAccrualModel):
    """Stub: gs priced an OIS swap on GS servers to get fixings."""

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotSupportedError("OisFixingCashAccrualModel fetches OIS fixings from GS; use DataCashAccrualModel(GenericDataSource(<your fixings, decimal>)) "
                                "or ConstantCashAccrualModel(rate)")

    def interest(self, balance: float, t0: pd.Timestamp, t1: pd.Timestamp) -> float:  # pragma: no cover - never constructed
        raise NotSupportedError("OisFixingCashAccrualModel")


# ============================================================================ pnl explain (accepted, not run)
@dataclass
class PnlAttribute:
    attribute_name: str
    attribute_metric: Any
    market_data_metric: Any
    scaling_factor: float
    second_order: bool = False


@dataclass
class PnlDefinition:
    """Accepted so gs code imports; `run_backtest(pnl_explain=...)` raises NotSupportedError: use pricebt P&L layers (EngineSettings.layers,
    BacktestResult.attribution())."""

    attributes: Iterable[PnlAttribute] = ()


# ============================================================================ result
PRICE_COLUMN = "Price"
CUMULATIVE_CASH_COLUMN = "Cumulative Cash"
TRANSACTION_COSTS_COLUMN = "Transaction Costs"
TOTAL_COLUMN = "Total"
LEDGER_COLUMNS = ("Open", "Close", "Open Value", "Close Value", "Long Short", "Status", "Trade PnL")


class ResultSummary(pd.DataFrame):
    """The gs `result_summary` frame. Selecting with a price RiskMeasure (`Price`, `DollarPrice`, whose value is 'pv') returns the 'Price'
    column; other RiskMeasure columns are labelled by the RiskMeasure itself (so `df[IRDeltaParallel]` and `df['dv01']` both work). The
    pre-2021 gs column 'Cash' (per-point cash change) is derived on request from 'Cumulative Cash'."""

    @property
    def _constructor(self) -> type:
        return ResultSummary

    def _gs_key(self, k: Any) -> Any:
        return k.label_in(self) if isinstance(k, RiskMeasure) else k

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str) and not isinstance(key, RiskMeasure) and key == "Cash" and "Cash" not in self.columns and CUMULATIVE_CASH_COLUMN in self.columns:
            cc = super().__getitem__(CUMULATIVE_CASH_COLUMN)
            return cc.diff().fillna(cc.iloc[0] if len(cc) else 0.0).rename("Cash")
        if isinstance(key, list):
            key = [self._gs_key(k) for k in key]
        else:
            key = self._gs_key(key)
        return super().__getitem__(key)


class BaseBacktest:
    pass


class Backtest(BaseBacktest):
    """What `GenericEngine.run_backtest` returns: gs_quant's `BackTest` surface over a pricebt `BacktestResult` (`.result`).

    `result_summary` (Price / risk columns / Cumulative Cash / Transaction Costs / Total), `trade_ledger()`, `summary_stats()` follow
    gs_quant's column names, labels and formulas; everything else pricebt records is on `.result` (positions, trades, reconcile(), tearsheet...).
    """

    CUMULATIVE_CASH_COLUMN = CUMULATIVE_CASH_COLUMN
    TRANSACTION_COSTS_COLUMN = TRANSACTION_COSTS_COLUMN
    TOTAL_COLUMN = TOTAL_COLUMN

    def __init__(self, strategy: Any, states: Sequence[Any], risks: Sequence[Any], price_measure: Any, result: Any, holiday_calendar: Any = None,
                 pnl_explain_def: Any = None):
        self.strategy = strategy
        self.states = list(states)
        self.risks = list(risks)
        self.price_measure = price_measure
        self.holiday_calendar = holiday_calendar
        self.pnl_explain_def = pnl_explain_def
        self.result = result
        self._summary: Optional[ResultSummary] = None
        self._ledger: Optional[pd.DataFrame] = None

    # ---- plumbing
    @property
    def record(self) -> Any:
        return self.result.record

    @property
    def daily(self) -> bool:
        """True when the run has at most one point per local date (then results are indexed by dt.date, like gs)."""
        idx = self.result.equity.index
        return len({t.date() for t in idx}) == len(idx)

    def _key(self, ts: Any) -> Any:
        if ts is None or pd.isna(ts):
            return None
        return pd.Timestamp(ts).tz_convert(self.result.equity.index.tz).date() if self.daily else pd.Timestamp(ts).tz_convert(self.result.equity.index.tz)

    def _index(self) -> pd.Index:
        idx = self.result.equity.index
        return pd.Index([t.date() for t in idx]) if self.daily else idx

    # ---- gs views
    @property
    def result_summary(self) -> ResultSummary:
        """Price (MTM of live positions) | one column per extra risk | Cumulative Cash | Transaction Costs (cumulative, <= 0) | Total.
        Total == Price + Cumulative Cash + Transaction Costs on every row."""
        if self._summary is None:
            eq = self.result.equity
            idx = self._index()
            cols: Dict[Any, Any] = {PRICE_COLUMN: eq["positions_value"].to_numpy(dtype=float)}
            vectors = getattr(self.result, "vectors", None) or {}
            for r in self.risks:
                rm = as_risk_measure(r)
                if rm.is_price:
                    continue
                name = rm.measure
                if rm.vector:
                    vec = vectors.get(name)
                    if vec is None:
                        raise NotSupportedError(f"vector risk {rm!r} was not recorded (EngineSettings.vector_measures)")
                    vec = vec.reindex(eq.index).fillna(0.0) if len(vec) else pd.DataFrame(index=eq.index)
                    cols[r] = pd.Series([vec.loc[t] for t in eq.index], index=idx, dtype=object).to_numpy()
                else:
                    cols[r] = eq[f"measure_{name}"].to_numpy(dtype=float)
            cols[CUMULATIVE_CASH_COLUMN] = (self.result.initial_capital + eq["cash"]).to_numpy(dtype=float)
            cols[TRANSACTION_COSTS_COLUMN] = eq["tcost"].to_numpy(dtype=float)
            cols[TOTAL_COLUMN] = eq["equity"].to_numpy(dtype=float)
            self._summary = ResultSummary(cols, index=idx)
        return self._summary.copy()

    @property
    def risk_summary(self) -> pd.DataFrame:
        """The recorded risk columns only (gs: risks with 0 on dates without instruments)."""
        rs = self.result_summary
        keep = [c for c in rs.columns if c not in (CUMULATIVE_CASH_COLUMN, TRANSACTION_COSTS_COLUMN, TOTAL_COLUMN)]
        return pd.DataFrame(rs[keep])

    def ladder(self, risk: Any) -> pd.DataFrame:
        """A recorded vector risk (e.g. `IRDelta`) as a frame: one row per point, one column per bucket."""
        rm = as_risk_measure(risk)
        vec = (getattr(self.result, "vectors", None) or {}).get(rm.measure)
        if vec is None:
            raise NotSupportedError(f"{rm!r} was not recorded: pass it in run_backtest(risks=[...])")
        out = vec.reindex(self.result.equity.index).fillna(0.0)
        out.index = self._index()
        return out

    def trade_name(self, positions: Optional[pd.DataFrame] = None) -> pd.Series:
        """gs trade names '<Action>_<Instrument>_<date>' (initial holdings: '<Instrument>_<date>'), one per pricebt position id."""
        p = self.result.positions if positions is None else positions
        names: List[str] = []
        seen: Dict[str, int] = {}
        for pid, r in p.iterrows():
            k = self._key(r["entry_ts"])
            stamp = k.isoformat() if isinstance(k, dt.date) and not isinstance(k, dt.datetime) else pd.Timestamp(k).strftime("%Y-%m-%dT%H%M%S")
            base = f"{r['template']}_{stamp}" if r["kind"] == "initial" else f"{r['action']}_{r['template']}_{stamp}"
            if base in seen:
                base = f"{base}_{pid}"
            seen[base] = 1
            names.append(base)
        return pd.Series(names, index=p.index, dtype=object)

    def trade_ledger(self) -> pd.DataFrame:
        """gs columns: Open, Close, Open Value, Close Value, Long Short, Status, Trade PnL; indexed by trade name.

        Open Value = cash paid at entry (-quantity x entry price); Close Value = all cash received afterwards (exit proceeds, resizes, coupons,
        option cash settlement, financing); Trade PnL = Open + Close Value for closed trades (gross of transaction costs, like gs), None while
        open (Close None, Close Value 0 - the mark is in result_summary's Price). Long Short = sign of the quantity held (+1 / -1). A two-leg
        Bundle is two rows (one per leg)."""
        if self._ledger is None:
            p = self.result.positions
            if p.empty:
                self._ledger = pd.DataFrame(columns=list(LEDGER_COLUMNS))
            else:
                closed = p["status"] == "closed"
                open_value = -p["entry_quantity"].astype(float) * p["entry_pv"].astype(float)
                close_value = np.where(closed, p["pnl_gross"].astype(float) - open_value, 0.0)
                led = pd.DataFrame(
                    {
                        "Open": [self._key(t) for t in p["entry_ts"]],
                        "Close": [self._key(t) if c else None for t, c in zip(p["exit_ts"], closed)],
                        "Open Value": open_value.to_numpy(dtype=float),
                        "Close Value": close_value.astype(float),
                        "Long Short": np.where(p["entry_quantity"].astype(float) >= 0, 1, -1),
                        "Status": np.where(closed, "closed", "open"),
                        "Trade PnL": np.where(closed, p["pnl_gross"].astype(float), np.nan),
                    },
                    index=pd.Index(self.trade_name(p).to_numpy(), name=None),
                )
                self._ledger = led.sort_index()
        return self._ledger.copy()

    def summary_stats(self, annualisation_factor: int = 252) -> pd.Series:
        """gs_quant `BackTest.summary_stats`: same labels and formulas, computed on result_summary['Total'] (point-to-point changes)."""
        summary = self.result_summary
        if summary.empty:
            return pd.Series(dtype=float)
        total = summary[TOTAL_COLUMN].astype(float)
        daily_pnl = total.diff().dropna()
        start_date = total.index[0]
        end_date = total.index[-1]
        duration_days = (end_date - start_date).days
        num_periods = len(daily_pnl)
        total_pnl = total.iloc[-1]
        total_tc = summary[TRANSACTION_COSTS_COLUMN].iloc[-1] if TRANSACTION_COSTS_COLUMN in summary else 0
        try:
            num_trades = len(self.trade_ledger())
        except Exception:  # noqa: BLE001 - gs semantics: a ledger failure reports NaN trades
            num_trades = np.nan
        avg_daily = daily_pnl.mean()
        std_daily = daily_pnl.std()
        ann_return = avg_daily * annualisation_factor
        ann_vol = std_daily * np.sqrt(annualisation_factor)
        sharpe = ann_return / ann_vol if ann_vol != 0 else np.nan
        downside = daily_pnl[daily_pnl < 0]
        downside_std = np.sqrt((downside ** 2).mean()) if len(downside) > 0 else 0.0
        ann_downside = downside_std * np.sqrt(annualisation_factor)
        sortino = ann_return / ann_downside if ann_downside != 0 else np.nan
        running_max = total.cummax()
        drawdown = total - running_max
        max_drawdown = drawdown.min()
        calmar = ann_return / abs(max_drawdown) if max_drawdown != 0 else np.nan
        current_drawdown = drawdown.iloc[-1]
        peak_pnl = running_max.iloc[-1]
        in_drawdown = drawdown < 0
        if in_drawdown.any():
            dd_groups = (~in_drawdown).cumsum()
            dd_durations = in_drawdown.groupby(dd_groups).apply(lambda g: (g.index[-1] - g.index[0]).days if g.any() else 0)
            max_dd_duration = dd_durations.max()
        else:
            max_dd_duration = 0
        best_day = daily_pnl.max()
        worst_day = daily_pnl.min()
        pct_positive = (daily_pnl > 0).sum() / num_periods * 100 if num_periods > 0 else np.nan
        skewness = daily_pnl.skew()
        kurtosis = daily_pnl.kurtosis()
        return pd.Series(
            {
                "Start Date": start_date,
                "End Date": end_date,
                "Duration (days)": duration_days,
                "Total PnL": total_pnl,
                "Total Transaction Costs": total_tc,
                "Total Trades": num_trades,
                "Peak PnL": peak_pnl,
                "Annualised Return": ann_return,
                "Annualised Volatility": ann_vol,
                "Sharpe Ratio": sharpe,
                "Sortino Ratio": sortino,
                "Max Drawdown": max_drawdown,
                "Max Drawdown Duration (days)": max_dd_duration,
                "Calmar Ratio": calmar,
                "Current Drawdown": current_drawdown,
                "Average Daily PnL": avg_daily,
                "Daily PnL Std Dev": std_daily,
                "Best Day": best_day,
                "Worst Day": worst_day,
                "% Positive Days": pct_positive,
                "Skewness": skewness,
                "Kurtosis": kurtosis,
            }
        )

    def pnl_explain(self) -> None:
        """gs returns None without a PnlDefinition; pricebt never runs one (see PnlDefinition)."""
        return None

    def __repr__(self) -> str:
        e = self.result.equity
        return f"Backtest({len(e)} points {self.states[0] if self.states else ''} -> {self.states[-1] if self.states else ''}, total={float(e['equity'].iloc[-1]):,.2f})"


BackTest = Backtest  # gs_quant's class name
