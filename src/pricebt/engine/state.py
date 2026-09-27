"""Engine state objects: Position, EngineSettings, RunRecord."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd

from ..contracts.spec import TradeTemplate
from ..costs import CashAccrualModel, CostModel
from ..orders import PositionMeta
from ..pricable import Valuation
from ..pricer import Pricer


POSITION_COLUMNS = [
    "id", "template", "instrument", "action", "kind", "tags", "entry_ts", "exit_ts", "exit_reason", "entry_quantity", "quantity", "entry_pv", "exit_pv",
    "status", "trade_cash", "flow_cash", "financing", "tcost", "pnl",
]


def term_columns(terms: Mapping[str, Any]) -> Dict[str, Any]:
    """The RESOLVED terms of a position as flat ledger columns `term_<name>` (a mapping-valued term such as `extras` is skipped: it is opaque)."""
    return {f"term_{k}": v for k, v in terms.items() if not isinstance(v, Mapping)}


@dataclass
class Position:
    """One held instrument. Quantity is signed units of the template as written; per-unit values come from the bound callables."""

    id: str
    pricable: Any
    template: TradeTemplate
    quantity: float
    entry_quantity: float
    entry_ts: pd.Timestamp
    entry_pricer: Pricer
    entry_pv: float
    final_ts: Optional[pd.Timestamp]
    tags: Tuple[str, ...]
    meta: PositionMeta
    booked_point: int
    terms: Dict[str, Any] = field(default_factory=dict)  # the terms the factory RESOLVED (concrete dates, resolved rate, direction)
    cost_entry: Optional[CostModel] = None
    cost_exit: Optional[CostModel] = None
    status: str = "open"
    state: Dict[str, Any] = field(default_factory=dict)
    last_ts: Optional[pd.Timestamp] = None
    last_pricer: Optional[Pricer] = None
    last_pv: float = 0.0
    last_val: Optional[Valuation] = None
    baseline_off: bool = False  # the baseline measures could not be evaluated for this position (recorded once, under every on_error mode); no Taylor terms from then on (the rest of its P&L is `tay_unexplained`)
    baseline_error: str = ""  # the baseline failure at entry, kept until the position is booked and has an id to be recorded under
    baseline_prev: Optional[Tuple[float, float, float]] = None  # (dv01, gamma, rate) per unit at the start of the current attribution interval (settings.baseline)
    # ledger (currency)
    trade_cash: float = 0.0
    flow_cash: float = 0.0
    fin_cash: float = 0.0
    tcost: float = 0.0
    # attribution
    interval_pnl: float = 0.0
    layer_prev_ts: Optional[pd.Timestamp] = None
    layer_prev_pricer: Optional[Pricer] = None
    layer_pnl: Dict[str, float] = field(default_factory=dict)
    # exit
    exit_ts: Optional[pd.Timestamp] = None
    exit_pv: Optional[float] = None
    exit_reason: str = ""

    @property
    def value(self) -> float:
        return self.quantity * self.last_pv

    @property
    def pnl(self) -> float:
        """Net P&L to date in currency: value + trade cash + realised flows + financing - costs."""
        return self.quantity * self.last_pv + self.trade_cash + self.flow_cash + self.fin_cash - self.tcost

    def summary(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "template": self.meta.template,
            "instrument": self.template.spec.name,
            "action": self.meta.action,
            "kind": self.meta.kind,
            "tags": ",".join(self.tags),
            "entry_ts": self.entry_ts,
            "exit_ts": self.exit_ts,
            "exit_reason": self.exit_reason,
            "entry_quantity": self.entry_quantity,
            "quantity": self.quantity,
            "entry_pv": self.entry_pv,
            "exit_pv": self.exit_pv,
            "status": self.status,
            "trade_cash": self.trade_cash,
            "flow_cash": self.flow_cash,
            "financing": self.fin_cash,
            "tcost": self.tcost,
            "pnl": self.pnl,
        }
        d.update(term_columns(self.terms))
        for k, v in self.layer_pnl.items():
            d[f"layer_{k}"] = v
        return d


@dataclass(frozen=True)
class EngineSettings:
    name: str = "backtest"
    initial_capital: float = 0.0
    fill_lag: int = 0
    on_error: str = "raise"  # raise | skip | record (skip and record both continue; both log an ErrorRecord)
    layers: Tuple[str, ...] = ()
    cadence: str = "eod"  # each | eod | every_n:<k>
    strict_layers: bool = False
    layer_tol: float = 1e-6
    measures: Tuple[str, ...] = ()  # portfolio-level standard measures recorded every point (a vector measure is recorded as its sum over buckets)
    vector_measures: Tuple[str, ...] = ()  # portfolio-level VECTOR measures (e.g. delta_ladder) recorded every point as one row of buckets
    baseline: bool = False  # the engine's Taylor attribution rows tay_delta / tay_convexity / tay_unexplained (spec L3): needs the instrument's bound dv01, gamma and rate
    audit: bool = False  # record what the tie-out compares (spec D5, X2): the snapshot digest of every fetched input, per-position marks and layer amounts
    audit_measures: Tuple[str, ...] = ()  # per-unit scalar measures recorded in the audit marks (NaN where an instrument does not bind them)
    record_signals: Tuple[str, ...] = ()  # names of signals recorded every point as `signal_<name>` columns (NaN where the signal has no value yet)
    show_progress: bool = True
    progress_desc: str = "BACKTESTING..."
    cash_accrual: Optional[CashAccrualModel] = None
    quiet_foreign_bars: bool = True
    retain_pricers: int = 2
    exit_policy: str = "own_time"  # own_time: a scheduled exit adds its own timeline point | next_grid: snap to the next base-grid point (data-driven grids)
    fill_price: str = "decision"  # fill_lag>=1: 'decision' = price locked at the decision point | 'next' = re-priced at the booking point


@dataclass
class RunRecord:
    """Raw output of one Engine.run(); the results package wraps it."""

    settings: EngineSettings
    equity: pd.DataFrame
    positions: pd.DataFrame
    trades: pd.DataFrame
    orders: pd.DataFrame
    errors: pd.DataFrame
    events: pd.DataFrame
    layers_by_position: pd.DataFrame
    manifest: Dict[str, Any] = field(default_factory=dict)
    vectors: Dict[str, pd.DataFrame] = field(default_factory=dict)  # vector_measures: name -> frame (index ts, columns buckets)
    audit: Dict[str, pd.DataFrame] = field(default_factory=dict)  # settings.audit: 'inputs' (ts, role, digest, stamp), 'marks' (per position and point), 'layers' (per position, flush, layer)
