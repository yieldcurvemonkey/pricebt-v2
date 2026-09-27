"""The event-driven engine. One timeline point = clock -> cash interest -> scheduled exits -> queued fills -> strategy step -> execution -> marks
(+ layers, measures) -> one recorded row -> one progress tick. Triggers/actions see only the read-only EngineView."""
from __future__ import annotations

import bisect
import copy
import math
import time as _time
from contextlib import nullcontext
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from ..contracts.binding import Env
from ..contracts.evaluate import evaluate_layer, evaluate_measure, evaluate_value, scalar_of, tenor_sort_key, vector_add, vector_of, vector_scale
from ..contracts.spec import TradeTemplate, layer_ref
from ..costs import CostContext, CostModel
from ..types import BASELINE_LAYERS
from ..errors import EngineInvariantError, MeasureError, MeasureNotBound, MissingDataError, NotSupportedError, PricebtError
from ..market import MarketData
from ..orders import CloseOrder, Instruction, OpenOrder, PositionMeta, PositionSelector, ResizeOrder
from ..pricable import MarkContext, Valuation, as_valuation
from ..pricer import Pricer
from ..timeutil import Calendar, TimeGrid, TimelineContext
from ..view import Scope
from .progress import ProgressBar, quiet_tqdm
from .state import POSITION_COLUMNS, EngineSettings, Position, RunRecord, term_columns


_BP_PER_UNIT = {"percent": 100.0, "decimal": 1e4, "bp": 1.0}
_NO_SNAPSHOT = object()


def _snapshot(state: Any) -> Any:
    """A copy of a position's state to compare against later; a state that cannot be copied gives `_NO_SNAPSHOT` (never equal: nothing peeked is reused for it)."""
    try:
        return copy.deepcopy(state)
    except Exception:
        return _NO_SNAPSHOT


def _unchanged(snap: Any, state: Any) -> bool:
    """True only when the state provably equals the snapshot (an unorderable or uncomparable value, e.g. an array, counts as changed)."""
    try:
        return snap is not _NO_SNAPSHOT and bool(snap == state)
    except Exception:
        return False


def _bp_per_unit(spec: Any) -> float:
    """Basis points per unit of the schema's `rate` measure: the baseline reads the unit the schema DECLARES, it does not assume percent."""
    ns = spec.schema.spec_of("rate")
    unit = getattr(ns, "unit", None)
    if unit not in _BP_PER_UNIT:
        raise MeasureError(f"the baseline decomposition needs the unit of `rate` to be one of {sorted(_BP_PER_UNIT)}; the schema of asset class {spec.asset_class!r} declares {unit!r}")
    return _BP_PER_UNIT[unit]


class Engine:
    def __init__(self, grid: TimeGrid, market: MarketData, strategy: Any, settings: Optional[EngineSettings] = None, *, signals: Any = None):
        self.grid = grid
        self.market = market
        self.strategy = strategy
        self.settings = settings or EngineSettings()
        self.signals = signals
        self.view = _View(self)
        self._peek_view = _View(self, peek=True)  # the ONLY view a signal store is ever given: a signal evaluated from any call path can therefore only peek
        self._reset()

    # ------------------------------------------------------------------ state
    def _reset(self) -> None:
        self.market.clock.reset()
        self.market.reset()
        self._points: List[pd.Timestamp] = []
        self._i = -1
        self._t: Optional[pd.Timestamp] = None
        self._prev_t: Optional[pd.Timestamp] = None
        self._open: Dict[str, Position] = {}
        self._all: List[Position] = []
        self._seq = 0
        self.cash_account = 0.0
        self.tcost_account = 0.0
        self.interest_cum = 0.0
        self._pending: List[Instruction] = []
        self._queued: List[Tuple[str, Any]] = []
        self._rows: List[Dict[str, Any]] = []
        self._vec_rows: Dict[str, List[Tuple[pd.Timestamp, Optional[Dict[str, float]]]]] = {n: [] for n in self.settings.vector_measures}
        self._trades: List[Dict[str, Any]] = []
        self._audit_marks: List[Dict[str, Any]] = []
        self._audit_layers: List[Dict[str, Any]] = []
        self.market.audit = [] if self.settings.audit else None
        self._orders: List[Dict[str, Any]] = []
        self._errors: List[Dict[str, Any]] = []
        self._events: List[Dict[str, Any]] = []
        self._layer_totals: Dict[str, float] = {}
        self._pending_n = {"layers": 0, "baseline": 0}  # points counted toward the next `every_n` flush: by the library layers, and by the baseline while no library layer is held
        self._last_equity = self.settings.initial_capital
        self._bar: Optional[ProgressBar] = None

    # ------------------------------------------------------------------ run
    def run(self) -> RunRecord:
        self._reset()
        s = self.settings
        t0 = _time.perf_counter()
        tctx = TimelineContext(self.grid.start, self.grid.end, self.grid.ctx, self.grid.days)
        run = self.strategy.start(tctx) if hasattr(self.strategy, "start") else self.strategy
        extras = tctx.normalise(run.timeline_extras())
        self._run = run
        self._end = self.grid.end
        self._base_points = list(self.grid.points)
        self._points = sorted(set(self.grid.points) | set(extras))
        guard = quiet_tqdm() if s.quiet_foreign_bars else nullcontext()
        with guard, ProgressBar(len(self._points), desc=s.progress_desc, show=s.show_progress) as bar:
            self._bar = bar
            self._i = 0
            while self._i < len(self._points):
                self._step(self._points[self._i])
                bar.update(1, equity=self._last_equity)
                self._i += 1
        self._i = len(self._points) - 1
        return self._finalize(_time.perf_counter() - t0)

    def _add_point(self, ts: pd.Timestamp) -> None:
        if self.settings.exit_policy == "next_grid":
            j = bisect.bisect_left(self._base_points, ts)
            if j >= len(self._base_points):
                return
            ts = self._base_points[j]
        elif self.settings.exit_policy != "own_time":
            raise NotSupportedError(f"unknown exit_policy {self.settings.exit_policy!r}")
        if self._t is not None and ts <= self._t:
            return
        if ts > self._end:
            return
        k = bisect.bisect_left(self._points, ts)
        if k < len(self._points) and self._points[k] == ts:
            return
        self._points.insert(k, ts)
        if self._bar is not None:
            self._bar.add_total(1)

    # ------------------------------------------------------------------ one point
    def _step(self, t: pd.Timestamp) -> None:
        s = self.settings
        self.market.clock.advance(t)
        self._prev_t, self._t = self._t, t
        if self._prev_t is not None:
            keep = self._prev_t
            self.market.evict_before(keep)
            accrual = s.cash_accrual if s.cash_accrual is not None else getattr(self.strategy, "cash_accrual", None)
            if accrual is not None and self.cash_account != 0.0:
                intr = accrual.interest(self.cash_account, self._prev_t, t)
                self.cash_account += intr
                self.interest_cum += intr
        # 2. scheduled exits
        for pos in [p for p in self._open.values() if p.final_ts is not None and p.final_ts <= t]:
            self._guarded(lambda p=pos: self._close(p, t, "scheduled", None, None), where="scheduled_exit", pos=pos)
        # 3. queued fills (fill_lag >= 1)
        if self._queued:
            self._book_queued(t)
        # signals then strategy. Signals only OBSERVE: the store is handed the peeking view, so a book signal peeks at the positions whichever path evaluates it (here, lazily from the
        # strategy step, or at record time): no mark is booked, `view.cash` and the P&L a trigger sees are untouched; a data gap under a signal is a recorded error exactly as under a mark
        if self.signals is not None:
            self._guarded(lambda: self.signals.observe(t, self._peek_view), where="signals")
        self._run.step(t, self.view, self._submit)
        # 5. execution
        pending, self._pending = self._pending, []
        last = self._i == len(self._points) - 1
        for order in pending:
            self._guarded(lambda o=order: self._execute(o, t, last), where="execute")
        # 6. marks, layers, measures
        for pos in list(self._open.values()):
            self._guarded(lambda p=pos: self._mark(p, t), where="mark", pos=pos)
        self._maybe_layers(t)
        # 7. record
        self._record(t)

    def _guarded(self, fn: Callable[[], Any], *, where: str, pos: Optional[Position] = None) -> Any:
        if self.settings.on_error == "raise":
            return fn()
        try:
            return fn()
        except PricebtError as e:
            self._errors.append({"ts": self._t, "where": where, "position": pos.id if pos else None, "error": f"{type(e).__name__}: {e}"})
        return None

    # ------------------------------------------------------------------ submission / execution
    def _submit(self, order: Instruction) -> None:
        self._orders.append(_order_row(self._t, order))
        if isinstance(order, OpenOrder) and order.immediate:
            self._execute(order, self._t, False, immediate=True)
        else:
            self._pending.append(order)

    def _execute(self, order: Instruction, t: pd.Timestamp, last: bool, immediate: bool = False) -> None:
        lag = 0 if immediate else self.settings.fill_lag
        if lag >= 1:
            if last:
                self._events.append({"ts": t, "kind": "unfilled_order_at_end", "detail": _order_row(t, order)})
                return
            if self.settings.fill_price == "next":
                self._queued.append(("order", order))  # executed as a lag-0 order at the next point's pricer
            else:
                self._queue(order, t)
            return
        if isinstance(order, OpenOrder):
            pos = self._make_position(order, t)
            self._book_open(pos, t)
        elif isinstance(order, CloseOrder):
            for pos in self._select(order.selector):
                self._close(pos, t, order.reason, order.cost_model, None)
        elif isinstance(order, ResizeOrder):
            pos = self._open.get(order.position_id)
            if pos is None:
                raise EngineInvariantError(f"ResizeOrder targets unknown/closed position {order.position_id!r}")
            self._resize(pos, t, order.delta_quantity, order.cost_model, None)
        else:
            raise EngineInvariantError(f"unknown instruction {type(order).__name__}")

    def _select(self, sel: PositionSelector) -> List[Position]:
        return [p for p in self._open.values() if p.booked_point < self._i and sel.matches(p)] if self._i >= 0 else []

    # ---- lag >= 1: price now, book at the next point
    def _queue(self, order: Instruction, t: pd.Timestamp) -> None:
        if isinstance(order, OpenOrder):
            pos = self._make_position(order, t)
            self._queued.append(("open", (pos, self._entry_cost(pos, t))))
        elif isinstance(order, CloseOrder):
            for pos in self._select(order.selector):
                v = self._mark(pos, t)
                self._flush_layers(pos, t)
                self._queued.append(("close", (pos.id, v.pv, order.reason, order.cost_model)))
        elif isinstance(order, ResizeOrder):
            pos = self._open.get(order.position_id)
            if pos is None:
                raise EngineInvariantError(f"ResizeOrder targets unknown/closed position {order.position_id!r}")
            v = self._mark(pos, t)
            self._flush_layers(pos, t)
            self._queued.append(("resize", (pos.id, order.delta_quantity, v.pv, order.cost_model)))

    def _book_queued(self, t: pd.Timestamp) -> None:
        q, self._queued = self._queued, []
        for kind, payload in q:
            if kind == "order":
                self._guarded(lambda o=payload: self._execute(o, t, False, immediate=True), where="execute")
            elif kind == "open":
                pos, cost = payload
                self._book_open(pos, t, cost=cost)
            elif kind == "close":
                pid, pv, reason, cm = payload
                pos = self._open.get(pid)
                if pos is not None:
                    self._close(pos, t, reason, cm, pv)
            else:
                pid, dq, pv, cm = payload
                pos = self._open.get(pid)
                if pos is not None:
                    self._resize(pos, t, dq, cm, pv)

    # ------------------------------------------------------------------ positions
    def _roles(self, template: TradeTemplate, t: pd.Timestamp) -> Dict[str, Pricer]:
        return {role: self.market.pricer(t, name) for role, name in template.roles().items()}

    def _make_position(self, order: OpenOrder, t: pd.Timestamp) -> Position:
        template = order.template
        if not isinstance(template, TradeTemplate):
            raise EngineInvariantError(f"an OpenOrder needs a TradeTemplate (instrument spec + terms), got {type(template).__name__}; wrap a ready-made object with TradeTemplate.wrap")
        pricers = self._roles(template, t)
        built = template.build(pricers["primary"], t)
        meta = order.meta if order.meta.template else PositionMeta(**{**asdict(order.meta), "template": template.name})
        tags = tuple(dict.fromkeys((*order.tags, *meta.tags)))
        pos = Position(
            id="", pricable=built.obj, template=template, quantity=float(order.quantity), entry_quantity=float(order.quantity), entry_ts=t,
            entry_pricer=pricers["primary"], entry_pv=0.0, final_ts=order.final_ts, tags=tags, meta=meta, booked_point=self._i,
            terms=dict(built.terms), cost_entry=order.cost_entry, cost_exit=order.cost_exit if order.cost_exit is not None else order.cost_entry,
        )
        ctx = MarkContext(t, pricers["primary"], pricers, None, None, t, pricers["primary"], pos.state, {}, template.spec.params)
        v = self._call_value(pos, ctx, t)
        if self.settings.baseline:
            self._baseline_open(pos, ctx)
        pos.entry_pv = pos.last_pv = v.pv
        pos.last_ts, pos.last_pricer, pos.last_val = t, pricers["primary"], v
        pos.layer_prev_ts, pos.layer_prev_pricer = t, pricers["primary"]
        pos._ctx = ctx  # type: ignore[attr-defined]
        return pos

    def _entry_cost(self, pos: Position, t: pd.Timestamp) -> float:
        if pos.cost_entry is None:
            return 0.0
        return float(pos.cost_entry.cost(CostContext(t, pos.quantity, lambda n, p=pos: self._unit_measure(p, n))))

    def _book_open(self, pos: Position, t: pd.Timestamp, cost: Optional[float] = None) -> None:
        self._seq += 1
        pos.id = f"P{self._seq:06d}"
        pos.booked_point = self._i
        if pos.baseline_error:  # the baseline could not be read at entry (recorded now: the position has its id)
            self._errors.append({"ts": self._t, "where": "baseline", "position": pos.id, "error": pos.baseline_error})
        cash = -pos.quantity * pos.entry_pv
        self.cash_account += cash
        pos.trade_cash += cash
        c = self._entry_cost(pos, t) if cost is None else cost
        self.tcost_account -= c
        pos.tcost += c
        self._open[pos.id] = pos
        self._all.append(pos)
        if pos.final_ts is not None:
            self._add_point(pos.final_ts)
        self._trades.append({"ts": t, "position": pos.id, "kind": "open", "quantity": pos.quantity, "pv": pos.entry_pv, "cash": cash, "tcost": c, "action": pos.meta.action, "template": pos.meta.template, "reason": pos.meta.kind,
                             "instrument": pos.template.spec.name, **term_columns(pos.terms)})
        if self.settings.audit and pos.last_ts == t:  # the entry valuation is the first audited mark when it was made at THIS point (lag 0); a decision-point valuation (lag >= 1) is not: the mark at the booking point is
            self._audit_mark(pos, pos._ctx, pos.last_val, t)  # type: ignore[attr-defined]

    def _close(self, pos: Position, t: pd.Timestamp, reason: str, cost_model: Optional[CostModel], price: Optional[float]) -> None:
        if price is None:
            px = self._mark(pos, t).pv
            self._flush_layers(pos, t)
        else:  # fill_lag>=1: valued and layer-flushed at the decision point; the interval after it belongs to the successor position
            px = price
        q = pos.quantity
        cash = q * px
        self.cash_account += cash
        pos.trade_cash += cash
        cm = cost_model if cost_model is not None else pos.cost_exit
        c = float(cm.cost(CostContext(t, q, lambda n, p=pos: self._unit_measure(p, n)))) if cm is not None else 0.0
        self.tcost_account -= c
        pos.tcost += c
        pos.quantity = 0.0
        pos.status = "closed"
        pos.exit_ts, pos.exit_pv, pos.exit_reason = t, px, reason
        self._open.pop(pos.id, None)
        self._trades.append({"ts": t, "position": pos.id, "kind": "close", "quantity": -q, "pv": px, "cash": cash, "tcost": c, "action": pos.meta.action, "template": pos.meta.template, "reason": reason,
                             "instrument": pos.template.spec.name, **term_columns(pos.terms)})

    def _resize(self, pos: Position, t: pd.Timestamp, dq: float, cost_model: Optional[CostModel], price: Optional[float]) -> None:
        if price is None:
            px = self._mark(pos, t).pv
            self._flush_layers(pos, t)
        else:
            px = price
        cash = -dq * px
        self.cash_account += cash
        pos.trade_cash += cash
        pos.quantity += dq
        cm = cost_model if cost_model is not None else pos.cost_entry
        c = float(cm.cost(CostContext(t, dq, lambda n, p=pos: self._unit_measure(p, n)))) if cm is not None else 0.0
        self.tcost_account -= c
        pos.tcost += c
        if abs(pos.quantity) < 1e-12:
            pos.quantity = 0.0
            pos.status = "closed"
            pos.exit_ts, pos.exit_pv, pos.exit_reason = t, px, "resize_to_zero"
            self._open.pop(pos.id, None)
        self._trades.append({"ts": t, "position": pos.id, "kind": "resize", "quantity": dq, "pv": px, "cash": cash, "tcost": c, "action": pos.meta.action, "template": pos.meta.template, "reason": "resize",
                             "instrument": pos.template.spec.name, **term_columns(pos.terms)})

    # ------------------------------------------------------------------ marking
    def _env(self, pos: Position, ctx: MarkContext) -> Env:
        return Env(pricer=ctx.pricer, ctx=ctx, instrument=pos.pricable, terms=pos.terms, state=pos.state)

    def _call_value(self, pos: Position, ctx: MarkContext, t: pd.Timestamp) -> Valuation:
        raw = evaluate_value(pos.template.spec, self._env(pos, ctx))
        v = as_valuation(raw, t)
        if not (math.isfinite(v.pv) and math.isfinite(v.cash) and math.isfinite(v.financing)):
            raise EngineInvariantError(f"non-finite valuation for {pos.id or pos.meta.template} at {t}: {v}")
        return v

    def _peek_ctx(self, pos: Position, t: pd.Timestamp) -> MarkContext:
        """The mark context of `pos` at `t` WITHOUT marking it: nothing is booked and no position field changes. The next real mark at the same point reuses its pricers and, while
        the position's state is unchanged, the measures this peek cached (the state as it was when the peek began is kept to tell)."""
        hit = getattr(pos, "_peek", None)
        if hit is not None and hit[0].ts == t:
            return hit[0]
        pricers = self._roles(pos.template, t)
        ctx = MarkContext(t, pricers["primary"], pricers, pos.last_ts, pos.last_pricer, pos.entry_ts, pos.entry_pricer, pos.state, {}, pos.template.spec.params)
        pos._peek = (ctx, _snapshot(pos.state))  # type: ignore[attr-defined]
        return ctx

    def _mark(self, pos: Position, t: pd.Timestamp) -> Valuation:
        """Idempotent per (position, t): book the interval flows on the quantity held over [prev, t) and update the mark."""
        if pos.last_ts == t and pos.last_val is not None:
            return pos.last_val
        peeked = getattr(pos, "_peek", None)
        if peeked is not None and peeked[0].ts == t:  # a signal looked at this position at this point: same pricers, and its measures are already cached
            pctx, snap = peeked
            ctx = MarkContext(t, pctx.pricer, pctx.pricers, pos.last_ts, pos.last_pricer, pos.entry_ts, pos.entry_pricer, pos.state, {}, pos.template.spec.params)
            pos._peek = None  # type: ignore[attr-defined]
        else:
            pctx = None
            pricers = self._roles(pos.template, t)
            ctx = MarkContext(t, pricers["primary"], pricers, pos.last_ts, pos.last_pricer, pos.entry_ts, pos.entry_pricer, pos.state, {}, pos.template.spec.params)
        v = self._call_value(pos, ctx, t)  # `value` always runs on a fresh cache, exactly as when nothing peeked
        if pctx is not None and _unchanged(snap, pos.state):  # a measure read AFTER `value` wrote the state can differ from one a peek read before it: reuse the peeked ones only if `value` left the state alone
            for k, x in pctx.cache.items():
                ctx.cache.setdefault(k, x)
        q = pos.quantity
        step = (v.pv - pos.last_pv) + v.cash + v.financing
        flows = q * (v.cash + v.financing)
        self.cash_account += flows
        pos.flow_cash += q * v.cash
        pos.fin_cash += q * v.financing
        pos.interval_pnl += q * step
        pos.last_pv, pos.last_ts, pos.last_pricer, pos.last_val = v.pv, t, ctx.pricer, v
        pos._ctx = ctx  # type: ignore[attr-defined]
        if self.settings.audit:
            self._audit_mark(pos, ctx, v, t)
        return v

    def _audit_marks_frame(self) -> pd.DataFrame:
        """One row per (position, point); a scalar measure is one column, a vector measure one column per bucket in its own order (`measure_<name>.<bucket>`), a measure that no
        position could give is a NaN column of its own name."""
        head = ["ts", "position", "quantity", "pv", "cash", "financing"]
        seen = dict.fromkeys(k for row in self._audit_marks for k in row if k not in head)
        tail = [f"measure_{n}" for n in self.settings.audit_measures if not any(k == f"measure_{n}" or k.startswith(f"measure_{n}.") for k in seen)]
        return pd.DataFrame(self._audit_marks, columns=[*head, *seen, *tail])

    def _audit_mark(self, pos: Position, ctx: MarkContext, v: Valuation, t: pd.Timestamp) -> None:
        row: Dict[str, Any] = {"ts": t, "position": pos.id, "quantity": pos.quantity, "pv": v.pv, "cash": v.cash, "financing": v.financing}
        for name in self.settings.audit_measures:
            try:
                raw = evaluate_measure(pos.template.spec, name, self._env(pos, ctx), cache=ctx.cache)
                if isinstance(raw, Mapping):  # a vector measure (the delta ladder): one column per bucket, so the buckets are compared, not their sum
                    for bucket, x in raw.items():
                        row[f"measure_{name}.{bucket}"] = float(x)
                else:
                    row[f"measure_{name}"] = scalar_of(raw)
            except MeasureNotBound:
                row[f"measure_{name}"] = float("nan")  # the instrument does not bind it: the one silent NaN cell
            except Exception as e:  # the audit only OBSERVES: a measure that is bound but FAILS (non-finite, a bad ladder key, a library error) is a NaN cell and one recorded error, never an abort or a half-booked position
                row[f"measure_{name}"] = float("nan")
                self._errors.append({"ts": t, "where": "audit", "position": pos.id or None, "error": f"{type(e).__name__}: {e}"})
        self._audit_marks.append(row)

    # ------------------------------------------------------------------ layers
    def _layers_for(self, pos: Position) -> Tuple[str, ...]:
        """Instrument-level layers are mandatory (bound at load); engine-wide layers apply only where the instrument's spec binds them."""
        spec = pos.template.spec
        common = tuple(n for n in self.settings.layers if n in spec.bindings)
        return tuple(dict.fromkeys((*common, *spec.layers)))

    def _cadence_due(self, t: pd.Timestamp, counter: str) -> bool:
        """Is a flush due at this point? `every_n` counts points on `counter` ('layers' or 'baseline', see `_maybe_layers`); `each` and `eod` need no counter."""
        c = self.settings.cadence
        last = self._i >= len(self._points) - 1
        if c == "each":
            return True
        if c == "eod":
            return last or self._points[self._i + 1].date() != t.date()
        if c.startswith("every_n:"):
            self._pending_n[counter] += 1
            k = int(c.split(":", 1)[1])
            if self._pending_n[counter] >= k or last:
                self._pending_n[counter] = 0
                return True
            return False
        raise NotSupportedError(f"unknown attribution cadence {c!r}")

    def _baseline_active(self, pos: Position) -> bool:
        """The baseline still books rows for `pos`: it has not failed, or it failed after it began (from then on the whole interval P&L is `tay_unexplained`, so its rows keep adding up)."""
        return self.settings.baseline and (not pos.baseline_off or "tay_unexplained" in pos.layer_pnl)

    def _maybe_layers(self, t: pd.Timestamp) -> None:
        """Flush the attribution layers of every held position when the cadence says so. The baseline never moves WHEN a library layer flushes: the `every_n` counter of the library
        layers advances at exactly the points the run without the baseline would advance it (a library-layer position is held); only when none is held does the baseline count on
        a counter of its own, and a library flush flushes the baseline too."""
        held = list(self._open.values())
        lib = any(self._layers_for(p) for p in held)
        if not (lib or any(self._baseline_active(p) for p in held)):
            return
        if not self._cadence_due(t, "layers" if lib else "baseline"):
            return
        self._pending_n["baseline"] = 0
        for pos in held:
            self._guarded(lambda p=pos: self._flush_layers(p, t), where="layers", pos=pos)

    def _baseline_open(self, pos: Position, ctx: MarkContext) -> None:
        """The baseline state at entry. The baseline only OBSERVES: an instrument that cannot give it is reported once and traded without baseline rows."""
        try:
            pos.baseline_prev = self._baseline_state(pos, ctx)
        except Exception as e:
            self._baseline_failed(pos, e)

    def _baseline_failed(self, pos: Position, e: Exception) -> None:
        """`attribution.baseline` never changes whether the run completes: whatever the baseline fails on (an unbound measure, a NaN rate past maturity, a raw library error) is ONE
        recorded error for the position under every `on_error` mode, and the position gets no further Taylor terms. A position with no id yet (it is not booked) is recorded when it is."""
        pos.baseline_off = True
        pos.baseline_prev = None
        msg = f"{type(e).__name__}: {e}"
        if pos.id:
            self._errors.append({"ts": self._t, "where": "baseline", "position": pos.id, "error": msg})
        else:
            pos.baseline_error = msg

    def _baseline_state(self, pos: Position, ctx: MarkContext) -> Tuple[float, float, float]:
        """(dv01, gamma, rate) per unit at `ctx`: the three bound measures the baseline decomposition is built from (an unbound one is a MeasureError naming it)."""
        env = self._env(pos, ctx)
        got = [scalar_of(evaluate_measure(pos.template.spec, n, env, cache=ctx.cache)) for n in ("dv01", "gamma", "rate")]
        return got[0], got[1], got[2]

    def _baseline_rows(self, pos: Position, t: pd.Timestamp, ctx: MarkContext, q: float) -> None:
        """tay_delta = dv01(start) * d(rate) in bp; tay_convexity = 1/2 gamma(start) * d(rate)^2; tay_unexplained = interval P&L minus both. The end state starts the next interval."""
        if not pos.baseline_off:
            try:
                dv0, g0, r0 = pos.baseline_prev if pos.baseline_prev is not None else self._baseline_state(pos, ctx)
                state = self._baseline_state(pos, ctx)
                dbp = (state[2] - r0) * _bp_per_unit(pos.template.spec)
            except Exception as e:  # the baseline only OBSERVES
                self._baseline_failed(pos, e)
        if pos.baseline_off:  # the baseline cannot read this interval (now, or since an earlier one): it explains none of it, so all of it is the baseline's own remainder
            self._book_layer(pos, t, "tay_unexplained", pos.interval_pnl / q if q else 0.0, pos.interval_pnl)
            return
        units = {"tay_delta": dv0 * dbp, "tay_convexity": 0.5 * g0 * dbp * dbp}
        explained = 0.0
        for name, val in units.items():
            amt = q * val
            explained += amt
            self._book_layer(pos, t, name, val, amt)
        unexpl = pos.interval_pnl - explained
        self._book_layer(pos, t, "tay_unexplained", unexpl / q if q else 0.0, unexpl)
        pos.baseline_prev = state

    def _book_layer(self, pos: Position, t: pd.Timestamp, name: str, unit: float, amount: float) -> None:
        if self.settings.audit:
            self._audit_layers.append({"ts": t, "position": pos.id, "layer": name, "unit": unit, "amount": amount})
        pos.layer_pnl[name] = pos.layer_pnl.get(name, 0.0) + amount
        self._layer_totals[name] = self._layer_totals.get(name, 0.0) + amount

    def _flush_layers(self, pos: Position, t: pd.Timestamp) -> None:
        names = self._layers_for(pos)
        base = self._baseline_active(pos)
        if not (names or base) or pos.last_ts != t or pos.layer_prev_ts is None or pos.layer_prev_ts == t:
            return
        ctx = MarkContext(t, pos.last_pricer, self._roles(pos.template, t), pos.layer_prev_ts, pos.layer_prev_pricer, pos.entry_ts, pos.entry_pricer, pos.state, {}, pos.template.spec.params)
        env = self._env(pos, ctx)
        q = pos.quantity
        explained = 0.0
        for name in names:
            val = evaluate_layer(pos.template.spec, name, env)
            amt = q * val
            explained += amt
            self._book_layer(pos, t, name, val, amt)
        if names:
            unexpl = pos.interval_pnl - explained
            if self.settings.strict_layers and abs(unexpl) > self.settings.layer_tol * max(1.0, abs(pos.interval_pnl)):
                raise EngineInvariantError(f"{pos.id}: unexplained {unexpl:.6g} exceeds tolerance on interval P&L {pos.interval_pnl:.6g} at {t}")
            self._book_layer(pos, t, "unexplained", unexpl / q if q else 0.0, unexpl)
        if base:
            self._baseline_rows(pos, t, ctx, q)
        pos.interval_pnl = 0.0
        pos.layer_prev_ts, pos.layer_prev_pricer = t, pos.last_pricer

    # ------------------------------------------------------------------ measures
    def _unit_raw(self, pos: Position, name: str, peek: bool = False) -> Any:
        """The per-unit measure of a held position at the current point (a float, or a dict[str, float] for a vector measure). `peek` (the view a signal is given) reads it at this
        point's market WITHOUT marking; the strategy's own request (peek False) marks the position, as it always did."""
        if pos.last_ts != self._t and pos.status == "open" and self._t is not None:
            if peek:
                ctx = self._peek_ctx(pos, self._t)
                return evaluate_measure(pos.template.spec, name, self._env(pos, ctx), cache=ctx.cache)
            self._mark(pos, self._t)
        ctx = getattr(pos, "_ctx", None)
        if ctx is None:
            raise EngineInvariantError(f"position {pos.id} has no mark context")
        return evaluate_measure(pos.template.spec, name, self._env(pos, ctx), cache=ctx.cache)

    def _unit_measure(self, pos: Position, name: str, peek: bool = False) -> float:
        return scalar_of(self._unit_raw(pos, name, peek))

    def _unit_vector(self, pos: Position, name: str, peek: bool = False) -> Dict[str, float]:
        return vector_of(self._unit_raw(pos, name, peek), name)

    # ------------------------------------------------------------------ recording
    def _signal_value(self, name: str) -> float:
        """The recorded value of a signal at this point; NaN while it has none (a windowed signal before its first emission)."""
        if self.signals is None:
            raise NotSupportedError(f"record_signals names {name!r} but this engine has no signal store")
        try:
            return float(self.signals.value(name, self._t))
        except MissingDataError:
            return float("nan")
        except PricebtError:  # a data gap under a recorded signal: the observe step already recorded it (on_error != raise); the column is NaN, the run goes on
            if self.settings.on_error == "raise":
                raise
            return float("nan")

    def _record(self, t: pd.Timestamp) -> None:
        opens = list(self._open.values())
        pos_val = sum(p.quantity * p.last_pv for p in opens)
        equity = self.settings.initial_capital + self.cash_account + self.tcost_account + pos_val
        prev = self._last_equity
        row: Dict[str, Any] = {
            "ts": t, "equity": equity, "cash": self.cash_account, "tcost": self.tcost_account, "positions_value": pos_val, "n_positions": len(opens),
            "step_pnl": equity - prev, "interest_cum": self.interest_cum,
            "financing_cum": sum(p.fin_cash for p in self._all), "flows_cum": sum(p.flow_cash for p in self._all),
        }
        for k, v in self._layer_totals.items():
            row[f"layer_{k}"] = v
        for name in self.settings.measures:
            got = self._guarded(lambda n=name: self.view.measure(n, missing="zero"), where="measure")
            row[f"measure_{name}"] = float("nan") if got is None else got
        for name in self.settings.record_signals:
            row[f"signal_{name}"] = self._signal_value(name)
        for name in self.settings.vector_measures:
            vec = self._guarded(lambda n=name: self.view.measure_vector(n, missing="zero"), where="measure_vector")
            self._vec_rows[name].append((t, vec))  # a failed measurement is None (recorded as NaN), never an empty ladder that would read as a flat book
        self._rows.append(row)
        self._last_equity = equity

    def _finalize(self, elapsed: float) -> RunRecord:
        eq = pd.DataFrame(self._rows).set_index("ts") if self._rows else pd.DataFrame()
        layer_cols = sorted({c for c in eq.columns if c.startswith("layer_")}) if len(eq) else []
        if layer_cols:
            eq[layer_cols] = eq[layer_cols].ffill().fillna(0.0)
        pos = pd.DataFrame([p.summary() for p in self._all], columns=None if self._all else POSITION_COLUMNS)
        lay = pd.DataFrame([{"position": p.id, "layer": k, "pnl": v} for p in self._all for k, v in p.layer_pnl.items()], columns=["position", "layer", "pnl"])
        base = BASELINE_LAYERS if self.settings.baseline else ()
        specs = {p.template.spec.name: (p.template.spec, (*self._layers_for(p), *base)) for p in self._all}
        manifest = {
            "instruments": {n: {"asset_class": sp.asset_class, "conventions_digest": sp.conventions_digest, "conventions": dict(sp.conventions), "factory": sp.factory_path,
                                "roles": sorted(set(sp.roles().values())), "layers": {ln: layer_ref(sp, ln) for ln in lays}} for n, (sp, lays) in specs.items()},
            "name": self.settings.name, "n_points": len(self._points), "n_positions": len(self._all), "elapsed_seconds": elapsed,
            "fetches": self.market.n_fetches, "memo_hits": self.market.n_hits, "fill_lag": self.settings.fill_lag,
            "settings": {k: (str(v) if not isinstance(v, (int, float, str, bool, tuple, type(None))) else v) for k, v in asdict(self.settings).items()},
            "start": str(self._points[0]) if self._points else None, "end": str(self._points[-1]) if self._points else None,
        }
        vectors: Dict[str, pd.DataFrame] = {}
        for name, rows in self._vec_rows.items():
            if rows:
                cols = sorted({c for _, v in rows if v is not None for c in v}, key=tenor_sort_key)
                nan_row = [float("nan")] * len(cols)
                data = [nan_row if v is None else [v.get(c, 0.0) for c in cols] for _, v in rows]  # an absent bucket of a valid row is 0; a failed row is NaN
                vectors[name] = pd.DataFrame(data, index=pd.DatetimeIndex([t for t, _ in rows], name="ts"), columns=cols)
            else:
                vectors[name] = pd.DataFrame()
        audit: Dict[str, pd.DataFrame] = {}
        if self.settings.audit:
            audit = {"inputs": pd.DataFrame(self.market.audit, columns=["ts", "role", "digest", "stamp"]),
                     "marks": self._audit_marks_frame(),
                     "layers": pd.DataFrame(self._audit_layers, columns=["ts", "position", "layer", "unit", "amount"])}
        return RunRecord(
            settings=self.settings, equity=eq, positions=pos, trades=pd.DataFrame(self._trades), orders=pd.DataFrame(self._orders),
            errors=pd.DataFrame(self._errors, columns=["ts", "where", "position", "error"]), events=pd.DataFrame(self._events), layers_by_position=lay, manifest=manifest,
            vectors=vectors, audit=audit,
        )


class _Standin:
    """Position-shaped stand-in for a pending OpenOrder so PositionSelector.matches applies to it."""

    def __init__(self, o: OpenOrder):
        self.id = ""
        self.tags = tuple(dict.fromkeys((*o.tags, *o.meta.tags)))
        self.meta = o.meta if o.meta.template else PositionMeta(**{**asdict(o.meta), "template": getattr(o.template, "name", "")})


def _order_matches(sel: PositionSelector, o: OpenOrder) -> bool:
    return sel.matches(_Standin(o))


def _order_row(t: Optional[pd.Timestamp], o: Instruction) -> Dict[str, Any]:
    if isinstance(o, OpenOrder):
        return {"ts": t, "type": "open", "action": o.meta.action, "template": getattr(o.template, "name", type(o.template).__name__), "quantity": o.quantity, "final_ts": o.final_ts}
    if isinstance(o, CloseOrder):
        return {"ts": t, "type": "close", "action": o.reason, "template": "", "quantity": 0.0, "final_ts": None}
    return {"ts": t, "type": "resize", "action": o.reason, "template": o.position_id, "quantity": o.delta_quantity, "final_ts": None}


class _View:
    """Concrete EngineView (read-only surface for strategy components). `peek=True` is the view the signal store is given: asking it for a measure never marks a position."""

    def __init__(self, eng: Engine, *, peek: bool = False):
        self._e = eng
        self._peek = peek

    @property
    def now(self) -> Optional[pd.Timestamp]:
        return self._e._t

    # ---- static-ish
    @property
    def calendar(self) -> Calendar:
        return self._e.grid.ctx.calendar

    @property
    def start(self) -> pd.Timestamp:
        return self._e.grid.start

    @property
    def end(self) -> pd.Timestamp:
        return self._e.grid.end

    @property
    def equity(self) -> float:
        return self._e._last_equity

    @property
    def cash(self) -> float:
        return self._e.cash_account

    @property
    def pending_orders(self) -> Tuple[Instruction, ...]:
        return tuple(self._e._pending)

    # ---- positions
    def _sel(self, scope: Scope) -> PositionSelector:
        if isinstance(scope, PositionSelector):
            return scope
        if scope in ("portfolio", "all"):
            return PositionSelector()
        if isinstance(scope, str) and scope.startswith("tag:"):
            return PositionSelector(tags=(scope[4:],))
        if isinstance(scope, str) and scope.startswith("action:"):
            return PositionSelector(actions=(scope[7:],))
        raise NotSupportedError(f"unknown scope {scope!r}")

    def positions(self, scope: Scope = "portfolio", *, include_pending: bool = False) -> Tuple[Position, ...]:
        sel = self._sel(scope)
        return tuple(p for p in self._e._open.values() if sel.matches(p))

    def n_positions(self, scope: Scope = "portfolio", *, include_pending: bool = False) -> int:
        n = len(self.positions(scope))
        if include_pending:
            sel = self._sel(scope)
            for o in self._e._pending:
                if isinstance(o, OpenOrder) and _order_matches(sel, o):
                    n += 1
                elif isinstance(o, CloseOrder):
                    n -= len(self._e._select(o.selector))
        return n

    # ---- measures
    def measure(self, name: str, scope: Scope = "portfolio", *, include_pending: bool = False, missing: str = "raise") -> float:
        """Quantity-weighted sum of a per-unit measure (a vector measure counts as its sum over buckets). missing='zero': positions whose pricable
        lacks the measure contribute 0 (used for recording)."""
        return self._aggregate(name, scope, include_pending, missing, vector=False)

    def measure_vector(self, name: str, scope: Scope = "portfolio", *, include_pending: bool = False, missing: str = "raise") -> Dict[str, float]:
        """Quantity-weighted sum of a per-unit VECTOR measure (e.g. delta_ladder): a plain dict over the union of the positions' buckets."""
        return self._aggregate(name, scope, include_pending, missing, vector=True)

    def _aggregate(self, name: str, scope: Scope, include_pending: bool, missing: str, *, vector: bool) -> Any:
        e = self._e
        raw_unit = e._unit_vector if vector else e._unit_measure
        unit = lambda p, n: raw_unit(p, n, self._peek)  # noqa: E731
        of = self.measure_of_vector if vector else self.measure_of
        tot: Any = {} if vector else 0.0

        def acc(q: float, x: Any) -> None:
            nonlocal tot
            tot = vector_add(tot, x, q) if vector else tot + q * x

        for p in self.positions(scope):
            try:
                acc(p.quantity, unit(p, name))
            except MeasureNotBound:  # only "the instrument does not bind it" counts as 0: a binding that FAILS must not read as a zero exposure
                if missing != "zero":
                    raise
        if include_pending:
            sel = self._sel(scope)
            for o in e._pending:
                if isinstance(o, OpenOrder) and _order_matches(sel, o):
                    acc(1.0, of(o.template, name, o.quantity))
                elif isinstance(o, CloseOrder):
                    for p in e._select(o.selector):
                        if sel.matches(p):
                            acc(-p.quantity, unit(p, name))
                elif isinstance(o, ResizeOrder):
                    p = e._open.get(o.position_id)
                    if p is not None and sel.matches(p):
                        acc(o.delta_quantity, unit(p, name))
        return tot

    def build(self, template: TradeTemplate, *, request: Optional[Any] = None) -> Any:
        """Build an instrument from a template at `now` (for measures and duration resolution): returns `Built(obj, resolved_terms)`."""
        return template.build(self.pricer(self.now, template.spec.pricer, request), self.now)

    def measure_of(self, template: TradeTemplate, name: str, quantity: float = 1.0) -> float:
        """Per-unit measure of an unbooked template at `now`, times quantity."""
        return quantity * scalar_of(self._measure_of_raw(template, name))

    def measure_of_vector(self, template: TradeTemplate, name: str, quantity: float = 1.0) -> Dict[str, float]:
        """Per-unit VECTOR measure of an unbooked template at `now`, times quantity."""
        return vector_scale(vector_of(self._measure_of_raw(template, name), name), quantity)

    def _measure_of_raw(self, template: TradeTemplate, name: str) -> Any:
        e = self._e
        pricers = e._roles(template, self.now)
        built = template.build(pricers["primary"], self.now)
        ctx = MarkContext(self.now, pricers["primary"], pricers, None, None, self.now, pricers["primary"], {}, {}, template.spec.params)
        env = Env(pricer=ctx.pricer, ctx=ctx, instrument=built.obj, terms=built.terms, state={})
        return evaluate_measure(template.spec, name, env, cache=ctx.cache)

    # ---- market / signals
    def pricer(self, ts: Optional[pd.Timestamp] = None, role: str = "primary", request: Optional[Any] = None) -> Pricer:
        return self._e.market.pricer(ts or self.now, role, request)

    def signal(self, source: Any, ts: Optional[pd.Timestamp] = None) -> float:
        if self._e.signals is None:
            raise NotSupportedError("this engine has no signal store")
        return self._e.signals.value(source, ts)

    def signal_window(self, source: Any, n: int, *, inclusive: bool = False) -> np.ndarray:
        if self._e.signals is None:
            raise NotSupportedError("this engine has no signal store")
        return self._e.signals.window(source, n, inclusive=inclusive)

    # ---- accounting
    def layer_pnl(self, layer: str, scope: Scope = "portfolio") -> float:
        sel = self._sel(scope)
        return sum(p.layer_pnl.get(layer, 0.0) for p in self._e._all if sel.matches(p))

    def action_cash(self, action: str) -> float:
        return sum(p.trade_cash - p.tcost for p in self._e._all if p.meta.action == action)

    def event(self, kind: str, **detail: Any) -> None:
        self._e._events.append({"ts": self.now, "kind": kind, "detail": detail})
