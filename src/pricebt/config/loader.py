"""Config -> object graph. Every component is `{type: <registry-name|package.module:Attr>, ...kwargs}`; errors carry the YAML path.

Top-level keys: backtest, market, instruments, trades, params, signals, strategy, registry, outputs, meta, doc.

`instruments:` define WHAT an instrument is (asset class, factory, conventions, bindings); an action supplies the TERMS of each trade
(`{instrument: usd_sofr_ois, terms: {side: receive, maturity: 10Y, notional: 1e7}}`). Nothing here imports a library: a dotted path in the config
(`factory`, `wrap`, `registry.import`, `type`) is the only thing that does, and only under `registry.allow`.
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import inspect
import json
import math
import numbers
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from .. import registries as R
from ..contracts.evaluate import is_vector
from ..contracts.schema import SchemaRegistry
from ..contracts.spec import InstrumentSpec, TradeTemplate, build_spec
from ..costs import CashAccrualModel
from ..errors import ConfigError, PricebtError
from ..engine import Engine, EngineSettings
from ..market import Binding, MarketData
from ..pricer import TimeMapping
from ..registry import DEFAULT_ALLOW, Registry, resolve_dotted
from ..timeutil import Calendar, Clock, TimeContext, TimeGrid
from .stacks import apply_stack, base_digest

TOP_KEYS = {"backtest", "market", "instruments", "trades", "params", "signals", "strategy", "registry", "outputs", "meta", "name", "doc", "extends", "tieout"}
TIEOUT_KEYS = {"tolerances"}
ATTRIBUTION_KEYS = {"layers", "cadence", "strict", "tol", "baseline"}
_CADENCE = re.compile(r"(each|eod|every_n:[1-9][0-9]*)")
TRADE_KEYS = {"instrument", "terms", "name", "legs"}
LEG_KEYS = {"terms", "name"}
BACKTEST_KEYS = {"name", "tz", "calendar", "session", "date_policy", "grid", "initial_capital", "fill_lag", "fill_price", "exit_policy", "on_error", "progress", "attribution", "measures", "vector_measures", "record_signals", "cash_accrual", "audit"}
MARKET_KEYS = {"mdps", "pricers"}
MDP_KEYS = {"type", "kwargs", "time", "request"}
PRICER_KEYS = {"mdp", "request", "wrap"}

_KIND_OF_PARAM = {
    "actions": "action", "action": "action", "triggers": "trigger", "trigger": "trigger", "data_source": "signal", "source": "signal", "signal": "signal", "signals": "signal", "inputs": "signal",
    "transaction_cost": "cost", "transaction_cost_exit": "cost", "cost": "cost", "cost_entry": "cost", "cost_exit": "cost",
    "priceables": "template", "priceable": "template", "template": "template", "hedge": "template", "item": "template", "cash_accrual": "cash",
}


_TIME_KEYS = {"start_time", "end_time", "time", "times"}
_HHMM = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def _to_times(v: Any, path: str) -> Any:
    """'09:30' / '16:00:00' -> datetime.time (also inside lists); anything else passes through."""
    if isinstance(v, str) and _HHMM.match(v):
        parts = [int(x) for x in v.split(":")]
        return dt.time(*parts)
    if isinstance(v, list):
        return [_to_times(x, f"{path}[{i}]") for i, x in enumerate(v)]
    return v


_PATH_KEYS = {"root", "path", "file", "dir", "directory", "fixings_path", "calendars_root", "prices", "reference", "quotes"}


def _suggest(bad: str, allowed: Sequence[str]) -> str:
    m = difflib.get_close_matches(bad, list(allowed), n=1)
    return f" (did you mean {m[0]!r}?)" if m else f" (allowed: {sorted(allowed)})"


def _check_keys(d: Mapping[str, Any], allowed: set, path: str) -> None:
    for k in d:
        if k not in allowed:
            raise ConfigError(f"unknown key {k!r}{_suggest(k, sorted(allowed))}", path=f"{path}.{k}" if path else k, code="CFG-UNKNOWN-KEY")


_ON_ERROR = ("raise", "record", "skip")
_FILL_PRICE = ("decision", "next")
_EXIT_POLICY = ("own_time", "next_grid")
PROGRESS_KEYS = {"show", "desc"}
_MAPPING_BLOCKS = ("backtest", "market", "instruments", "trades", "params", "signals", "strategy", "registry", "outputs")


def _number(v: Any, path: str, what: str, *, integer: bool = False, minimum: Optional[float] = None) -> Any:
    """A finite real number (an integer when `integer`), never a bool: a wrong type is a load error with its path, not a ValueError from float() three calls later."""
    ok = not isinstance(v, bool) and isinstance(v, numbers.Integral if integer else numbers.Real) and math.isfinite(v) and (minimum is None or v >= minimum)
    if not ok:
        raise ConfigError(f"{path} must be {what}, got {v!r}", path=path, code="CFG-TYPE")
    return v


def _bound_extra(sp: InstrumentSpec, kind: str) -> Tuple[str, ...]:
    """What the kit binds beyond the schema (an adapter's own measures and layers): known names for the config, like the schema's."""
    return tuple(n for n, k in sp.extra.items() if k == kind and n in sp.bindings)


def _flag(v: Any, path: str) -> bool:
    if not isinstance(v, bool):
        raise ConfigError(f"{path} must be true or false, got {v!r} (a string such as \"false\" would read as true)", path=path, code="CFG-TYPE")
    return v


def _choice(v: Any, allowed: Sequence[str], path: str) -> str:
    if not isinstance(v, str) or v not in allowed:
        close = difflib.get_close_matches(str(v), list(allowed), n=1)
        raise ConfigError(f"{path} must be one of {list(allowed)}, got {v!r}" + (f"; did you mean {close[0]!r}?" if close else ""), path=path, code="CFG-TYPE")
    return v


def _dur(x: Any) -> Optional[pd.Timedelta]:
    if x is None:
        return None
    return pd.Timedelta(x)


def _time(x: Any) -> Optional[dt.time]:
    if x is None or isinstance(x, dt.time):
        return x
    h, m = str(x).split(":")[:2]
    return dt.time(int(h), int(m))


@dataclass
class Built:
    """Everything needed to run; `engine()` builds a fresh Engine, `run()` returns a BacktestResult."""

    cfg: Dict[str, Any]
    config_hash: str
    grid: TimeGrid
    market: MarketData
    strategy: Any
    settings: EngineSettings
    signals: Any
    instruments: Dict[str, InstrumentSpec]
    outputs: Dict[str, Any] = field(default_factory=dict)
    base_hash: str = ""

    def engine(self) -> Engine:
        from ..strategy.signals import SignalStore

        signals = SignalStore.for_strategy(self.strategy, list(self.signals.values())) if self.signals is not None else None
        if signals is None and getattr(self.strategy, "required_signals", None) and self.strategy.required_signals():
            signals = SignalStore.for_strategy(self.strategy)
        return Engine(self.grid, self.market, self.strategy, self.settings, signals=signals)

    def run(self) -> Any:
        from ..results import BacktestResult

        record = self.engine().run()
        return BacktestResult.from_record(record, config_hash=self.config_hash)


class _Builder:
    def __init__(self, cfg: Mapping[str, Any], base_dir: Path):
        self.cfg = cfg
        self.base = base_dir
        self.allow: Tuple[str, ...] = tuple(dict.fromkeys((*DEFAULT_ALLOW, *(cfg.get("registry", {}) or {}).get("allow", ()))))
        self.instruments: Dict[str, InstrumentSpec] = {}
        self.trades: Dict[str, Any] = {}
        self.params: Dict[str, Any] = dict(cfg.get("params") or {})
        self.signals: Dict[str, Any] = {}
        self.schemas = SchemaRegistry.default()
        for f in (cfg.get("registry", {}) or {}).get("schemas", ()):
            self.schemas.register_file(self.base / f)

    # ------------------------------------------------------------------ helpers
    def resolve(self, reg: Registry, name: str, path: str) -> Any:
        try:
            if ":" not in name and "." in name and name.split(".")[0] in reg.names():
                head, _, rest = name.partition(".")
                obj: Any = reg.resolve(head)
                for part in rest.split("."):
                    obj = getattr(obj, part)
                return obj
            return reg.resolve(name, extra_allow=self.allow)
        except PricebtError as e:
            raise ConfigError(str(e), path=path, code=getattr(e, "code", "CFG-UNKNOWN-TYPE")) from e
        except AttributeError as e:
            raise ConfigError(f"cannot resolve {name!r}: {e}", path=path, code="CFG-UNKNOWN-TYPE") from e

    def component(self, kind: str, spec: Any, path: str) -> Any:
        reg = {"action": R.ACTIONS, "trigger": R.TRIGGERS, "signal": R.SIGNALS, "cost": R.COSTS, "cash": R.CASH_ACCRUAL}[kind]
        if not isinstance(spec, Mapping) or "type" not in spec:
            raise ConfigError(f"a {kind} must be a mapping with a `type:` key", path=path, code="CFG-TYPE")
        spec = dict(spec)
        typ = spec.pop("type")
        cls = self.resolve(reg, typ, f"{path}.type")
        return self.instantiate(cls, spec, path, kind=kind)

    def instantiate(self, cls: Any, kwargs: Dict[str, Any], path: str, kind: str = "") -> Any:
        built: Dict[str, Any] = {}
        for k, v in kwargs.items():
            built[k] = self.convert(k, v, f"{path}.{k}", kind)
        try:
            sig = inspect.signature(cls)
            if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                for k in built:
                    if k not in sig.parameters:
                        raise ConfigError(f"unknown key {k!r} for {getattr(cls, '__name__', cls)}{_suggest(k, list(sig.parameters))}", path=f"{path}.{k}", code="CFG-UNKNOWN-KEY")
        except (TypeError, ValueError):
            pass
        try:
            return cls(**built)
        except ConfigError:
            raise
        except TypeError as e:
            raise ConfigError(f"cannot construct {getattr(cls, '__name__', cls)}: {e}", path=path, code="CFG-TYPE") from e
        except PricebtError as e:
            raise ConfigError(str(e), path=path, code=getattr(e, "code", "CFG-VALUE")) from e

    def dotted(self, v: Mapping[str, Any], path: str) -> Any:
        """`{$ref: "pkg.mod:Attr"}` -> the object itself (class/callable); `{$call: "pkg.mod:Attr", kwargs: {...}}` -> Attr(**kwargs). Allow-listed."""
        key = "$call" if "$call" in v else "$ref"
        extra = set(v) - {key, "kwargs"}
        if extra or (key == "$ref" and "kwargs" in v):
            raise ConfigError(f"unexpected keys {sorted(extra) or ['kwargs']} next to {key} (use $call for kwargs)", path=path, code="CFG-UNKNOWN-KEY")
        try:
            obj = resolve_dotted(str(v[key]), allow=self.allow)
        except PricebtError as e:
            raise ConfigError(str(e), path=f"{path}.{key}", code=getattr(e, "code", "CFG-IMPORT")) from e
        if key == "$ref":
            return obj
        kwargs = {k: self.deep_dotted(x, f"{path}.kwargs.{k}") for k, x in (v.get("kwargs") or {}).items()}
        try:
            return obj(**kwargs)
        except ConfigError:
            raise
        except Exception as e:  # noqa: BLE001 - a foreign constructor may raise anything; report it with the config path
            raise ConfigError(f"{v[key]}(**kwargs) failed: {type(e).__name__}: {e}", path=path, code="CFG-CALL") from e

    def deep_dotted(self, v: Any, path: str) -> Any:
        """Resolve `$ref`/`$call` forms anywhere inside a value (dicts and lists are walked; everything else is untouched)."""
        if isinstance(v, Mapping):
            if "$ref" in v or "$call" in v:
                return self.dotted(v, path)
            return {k: self.deep_dotted(x, f"{path}.{k}") for k, x in v.items()}
        if isinstance(v, list):
            return [self.deep_dotted(x, f"{path}[{i}]") for i, x in enumerate(v)]
        return v

    def convert(self, key: str, v: Any, path: str, owner: str = "") -> Any:
        if isinstance(v, Mapping) and ("$ref" in v or "$call" in v):
            return self.dotted(v, path)
        kind = _KIND_OF_PARAM.get(key)
        if kind is None:
            if owner in ("trigger", "action") and key in _TIME_KEYS:
                return _to_times(v, path)
            if key in _PATH_KEYS and isinstance(v, str) and not Path(v).is_absolute() and (self.base / v).exists():
                return str((self.base / v).resolve())  # relative paths resolve against the config file's directory
            return self.coerce_enum(key, v, path, owner)
        if isinstance(v, list):
            return [self.convert_one(kind, x, f"{path}[{i}]") for i, x in enumerate(v)]
        return self.convert_one(kind, v, path)

    def convert_one(self, kind: str, v: Any, path: str) -> Any:
        if kind == "template":
            return self.template_ref(v, path)
        if kind == "signal" and isinstance(v, str):
            if v not in self.signals:
                raise ConfigError(f"unknown signal {v!r}{_suggest(v, list(self.signals))}", path=path, code="CFG-REF")
            return self.signals[v]
        if kind in ("trigger", "action", "signal", "cost", "cash") and isinstance(v, Mapping):
            if kind == "trigger" and "type" not in v and len(v) == 1:  # gs style {trigger_requirements: ...}
                return self.convert("trigger_requirements", v, path)
            return self.component(kind, v, path)
        return v

    def coerce_enum(self, key: str, v: Any, path: str, owner: str = "") -> Any:
        from ..strategy import AggType, ScalingActionType, TriggerDirection

        all_enums = {"direction": ("trigger", TriggerDirection), "aggregate_type": ("trigger", AggType), "scaling_type": ("action", ScalingActionType)}
        enums = {k: e for k, (want, e) in all_enums.items() if owner == want}  # a cost's `scaling_type` is a plain string
        if key in enums and isinstance(v, str):
            try:
                return enums[key][v.upper()] if v.upper() in enums[key].__members__ else enums[key](v.lower())
            except (KeyError, ValueError) as e:
                raise ConfigError(f"{v!r} is not one of {sorted(enums[key].__members__)}", path=path, code="CFG-ENUM") from e
        if key == "trigger_requirements" and isinstance(v, Mapping) and "type" in v:
            spec = dict(v)
            typ = spec.pop("type")
            cls = self.resolve(R.TRIGGERS, typ if typ.endswith("requirements") else f"{typ}_requirements", f"{path}.type")
            return self.instantiate(cls, spec, path, kind="trigger")
        if isinstance(v, list):
            return [self.coerce_enum(key, x, f"{path}[{i}]", owner) for i, x in enumerate(v)]
        return v

    def subst_params(self, v: Any, path: str) -> Any:
        """Replace `@param.<name>` (anywhere in a value) by the top-level `params:` entry: a scanned parameter needs no edit to any instrument."""
        if isinstance(v, str) and v.startswith("@param."):
            key = v[len("@param."):]
            if key not in self.params:
                raise ConfigError(f"unknown parameter {key!r}{_suggest(key, list(self.params))}", path=path, code="CFG-REF")
            return self.params[key]
        if isinstance(v, Mapping):
            return {k: self.subst_params(x, f"{path}.{k}") for k, x in v.items()}
        if isinstance(v, list):
            return [self.subst_params(x, f"{path}[{i}]") for i, x in enumerate(v)]
        return v

    def make_template(self, instrument: str, terms: Mapping[str, Any], name: str, path: str) -> TradeTemplate:
        spec = self.instruments.get(instrument)
        if spec is None:
            raise ConfigError(f"unknown instrument {instrument!r}{_suggest(instrument, list(self.instruments))}", path=f"{path}.instrument", code="CFG-REF")
        try:
            return TradeTemplate(name, spec, self.subst_params(dict(terms), f"{path}.terms"), allow_refs=True)
        except ConfigError as e:
            raise ConfigError(str(e), path=f"{path}.terms", code=e.code) from e

    def trade(self, d: Mapping[str, Any], path: str, name: Optional[str] = None) -> Any:
        """`{instrument, terms, name}` -> a TradeTemplate; with `legs: [{name, terms}, ...]` -> a list of them (shared `terms` under each leg's own)."""
        _check_keys(d, TRADE_KEYS, path)
        if "instrument" not in d:
            raise ConfigError("a trade needs `instrument:` (the name of an entry of `instruments:`)", path=path, code="CFG-REQUIRED")
        inst, shared = str(d["instrument"]), dict(d.get("terms") or {})
        if "legs" in d:
            legs = d["legs"]
            if not isinstance(legs, list) or not legs:
                raise ConfigError("`legs` must be a non-empty list of {name, terms}", path=f"{path}.legs", code="CFG-TYPE")
            out = []
            for i, leg in enumerate(legs):
                _check_keys(leg, LEG_KEYS, f"{path}.legs[{i}]")
                out.append(self.make_template(inst, {**shared, **dict(leg.get("terms") or {})}, str(leg.get("name") or f"{inst}_{i}"), f"{path}.legs[{i}]"))
            return out
        return self.make_template(inst, shared, str(name or d.get("name") or inst), path)

    def template_ref(self, v: Any, path: str) -> Any:
        if isinstance(v, str):
            if v in self.trades:
                return self.trades[v]
            if v in self.instruments:
                return self.make_template(v, {}, v, path)  # an instrument that needs no terms (a toy), by name
            raise ConfigError(f"unknown trade or instrument {v!r}{_suggest(v, list(self.trades) + list(self.instruments))}", path=path, code="CFG-REF")
        if isinstance(v, Mapping):
            if "template" in v and "instrument" not in v:
                return self.position_spec(v, path)
            return self.trade(v, path)
        if isinstance(v, list):
            out: List[Any] = []
            for i, x in enumerate(v):
                r = self.template_ref(x, f"{path}[{i}]")
                out.extend(r if isinstance(r, list) else [r])
            return out
        return v

    def position_spec(self, v: Mapping[str, Any], path: str) -> Any:
        from ..strategy import PositionSpec

        _check_keys(v, {"template", "quantity", "tags", "name"}, path)
        return PositionSpec(self.template_ref(v["template"], f"{path}.template"), quantity=float(v.get("quantity", 1.0)), name=v.get("name"), tags=tuple(v.get("tags", ())))

    # ------------------------------------------------------------------ sections
    def instrument(self, name: str, d: Mapping[str, Any]) -> InstrumentSpec:
        return build_spec(name, d, schemas=self.schemas, allow=self.allow)

    def time_context(self, bt: Mapping[str, Any]) -> TimeContext:
        cal_cfg = bt.get("calendar")
        if cal_cfg in (None, "weekends"):
            cal = Calendar.weekends_only()
        elif isinstance(cal_cfg, str):
            cal = Calendar.from_json(self.base / cal_cfg)
        elif isinstance(cal_cfg, Mapping) and "file" in cal_cfg:
            cal = Calendar.from_json(self.base / cal_cfg["file"])
        elif isinstance(cal_cfg, Mapping):
            cal = Calendar(cal_cfg.get("holidays", ()), cal_cfg.get("weekmask", (1, 1, 1, 1, 1, 0, 0)))
        else:
            raise ConfigError("calendar must be 'weekends', a path, or {file|holidays,weekmask}", path="backtest.calendar", code="CFG-TYPE")
        sess = bt.get("session", {}) or {}
        return TimeContext(tz=bt.get("tz", "America/New_York"), calendar=cal, date_policy=bt.get("date_policy", "close"),
                           session_open=_time(sess.get("open")) or dt.time(8, 0), session_close=_time(sess.get("close")) or dt.time(17, 0))

    def mdp(self, d: Mapping[str, Any], path: str) -> Any:
        _check_keys(d, MDP_KEYS, path)
        if "type" not in d:
            raise ConfigError("an mdp needs a `type:`", path=path, code="CFG-REQUIRED")
        cls = self.resolve(R.MDPS, d["type"], f"{path}.type")
        kwargs = dict(d.get("kwargs", {}) or {})
        if "time" in d:
            t = d["time"] or {}
            try:
                params = inspect.signature(cls).parameters
            except (TypeError, ValueError):
                params = {}
            var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
            if "time" in params:  # provider parses its own time block (dict or TimeMapping)
                kwargs["time"] = dict(t)
            elif "mapping" in params or var_kw:
                mode = t.get("mode", "asof")
                if mode == "asof" and "max_staleness" not in t and not t.get("unbounded_ok", False):
                    raise ConfigError("an asof time mapping needs max_staleness (or unbounded_ok: true)", path=f"{path}.time", code="CFG-REQUIRED")
                kwargs["mapping"] = TimeMapping(mode=mode, max_staleness=_dur(t.get("max_staleness")), eod_visible_at=_time(t.get("eod_visible_at")),
                                                unbounded_ok=bool(t.get("unbounded_ok", False)), tz=t.get("tz", "America/New_York"))
            else:
                raise ConfigError(f"{d['type']} does not accept a time mapping", path=f"{path}.time", code="CFG-UNKNOWN-KEY")
        return self.instantiate(cls, kwargs, f"{path}.kwargs")

    def market(self, cfg: Mapping[str, Any]) -> Tuple[MarketData, Dict[str, Any]]:
        m = cfg.get("market")
        if not m:
            raise ConfigError("`market:` with at least one mdp is required", path="market", code="CFG-REQUIRED")
        _check_keys(m, MARKET_KEYS, "market")
        mdps = {n: self.mdp(d, f"market.mdps.{n}") for n, d in (m.get("mdps") or {}).items()}
        if not mdps:
            raise ConfigError("market.mdps is empty", path="market.mdps", code="CFG-REQUIRED")
        bindings: Dict[str, Binding] = {}
        pr = m.get("pricers")
        if pr:
            for role, d in pr.items():
                _check_keys(d, PRICER_KEYS, f"market.pricers.{role}")
                if d.get("mdp") not in mdps:
                    raise ConfigError(f"unknown mdp {d.get('mdp')!r}{_suggest(str(d.get('mdp')), list(mdps))}", path=f"market.pricers.{role}.mdp", code="CFG-REF")
                wrap = None
                if d.get("wrap") is not None:
                    try:
                        wrap = resolve_dotted(str(d["wrap"]), allow=self.allow)
                    except PricebtError as e:
                        raise ConfigError(str(e), path=f"market.pricers.{role}.wrap", code=getattr(e, "code", "CFG-IMPORT")) from e
                    if not callable(wrap):
                        raise ConfigError(f"wrap {d['wrap']!r} must be a callable wrap(pricer) -> pricer", path=f"market.pricers.{role}.wrap", code="CFG-TYPE")
                bindings[role] = Binding(mdps[d["mdp"]], dict(d.get("request", {}) or {}), wrap)
        else:
            for n, mdp in mdps.items():
                bindings[n] = Binding(mdp, dict((m["mdps"][n].get("request") or {})))
            if "primary" not in bindings and len(bindings) == 1:
                bindings["primary"] = next(iter(bindings.values()))
        for n in mdps:  # an instrument may also name an mdp directly as a role
            bindings.setdefault(n, Binding(mdps[n], dict((m["mdps"][n].get("request") or {}))))
        return MarketData(bindings, Clock()), mdps

    def grid(self, bt: Mapping[str, Any], tctx: TimeContext, market: MarketData) -> TimeGrid:
        g = bt.get("grid")
        if not g:
            raise ConfigError("backtest.grid is required", path="backtest.grid", code="CFG-REQUIRED")
        g = dict(g)
        if g.get("from_mdp"):
            role = g["from_mdp"] if isinstance(g["from_mdp"], str) else "primary"
            pts = market.available_timestamps(tctx.at(g["start"], time=dt.time(0, 0)) if "start" in g else pd.Timestamp("1900-01-01", tz=tctx.tz), tctx.at(g["end"], time=dt.time(23, 59)) if "end" in g else pd.Timestamp("2200-01-01", tz=tctx.tz), role)
            grid = TimeGrid(pts, tctx)
            if g.get("every"):
                keep, last = [], None
                for p in grid.points:
                    b = p.floor(pd.Timedelta(g["every"]))
                    if b != last:
                        keep.append(p)
                        last = b
                grid = TimeGrid(keep, tctx)
            if g.get("business_only", True):
                grid = TimeGrid([p for p in grid.points if tctx.calendar.is_business_day(p.date())], tctx)
            if g.get("session_only", False):
                grid = TimeGrid([p for p in grid.points if tctx.in_session(p)], tctx)
            if len(grid) == 0:
                raise ConfigError("the MDP reported no timestamps in the requested window", path="backtest.grid", code="CFG-GRID")
            return grid
        return TimeGrid.from_config(g, tctx)

    def _names(self, path: str, value: Any) -> Tuple[str, ...]:
        """A list of names (a bare string is a mistake: it would be read letter by letter)."""
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):  # (a bare string is not a list)
            raise ConfigError(f"{path} must be a list of names, got {value!r}", path=path, code="CFG-TYPE")
        return tuple(value)

    def _known(self, path: str, names: Sequence[str], universe: Mapping[str, Any], what: str) -> None:
        """Every name must exist for at least one defined instrument (a typo is otherwise a silent zero or NaN column)."""
        if not self.instruments:
            return
        unknown = [n for n in names if n not in universe]
        if unknown:
            close = {n: difflib.get_close_matches(n, list(universe), n=1) for n in unknown}
            raise ConfigError(f"{path}: {what} {unknown} not defined by any instrument (known: {sorted(universe)})" + "".join(f"; {n!r}: did you mean {c[0]!r}?" for n, c in close.items() if c),
                              path=path, code="CFG-REF")

    def settings(self, bt: Mapping[str, Any], cash_model: Optional[CashAccrualModel]) -> EngineSettings:
        att = bt.get("attribution", {}) or {}
        if not isinstance(att, Mapping):
            raise ConfigError("backtest.attribution must be a mapping", path="backtest.attribution", code="CFG-TYPE")
        _check_keys(att, ATTRIBUTION_KEYS, "backtest.attribution")
        for k in ("baseline", "strict"):
            if k in att and not isinstance(att[k], bool):
                raise ConfigError(f"backtest.attribution.{k} must be true or false, got {att[k]!r} (a string such as \"false\" would read as true)", path=f"backtest.attribution.{k}", code="CFG-TYPE")
        cadence = str(att.get("cadence", "eod"))
        if not _CADENCE.fullmatch(cadence):
            raise ConfigError(f"backtest.attribution.cadence {cadence!r}: use 'each', 'eod' or 'every_n:<k>' with k >= 1", path="backtest.attribution.cadence", code="CFG-TYPE")
        layers = self._names("backtest.attribution.layers", att.get("layers"))
        measures = self._names("backtest.measures", bt.get("measures"))
        vectors = self._names("backtest.vector_measures", bt.get("vector_measures"))
        specs = list(self.instruments.values())
        scalar_names = {n: 1 for sp in specs for n in (*(n for n in sp.schema.measures if n in sp.bindings or sp.schema.measures[n].derived), *_bound_extra(sp, "measure"))}
        vector_names = {n: 1 for sp in specs for n, m in sp.schema.measures.items() if is_vector(m) and n in sp.bindings}
        layer_names = {n: 1 for sp in specs for n in (*(k for k in sp.schema.layers if k in sp.bindings), *sp.layers, *_bound_extra(sp, "layer"))}
        self._known("backtest.attribution.layers", layers, layer_names, "layers")
        self._known("backtest.measures", measures, {**scalar_names, **vector_names}, "measures")
        self._known("backtest.vector_measures", vectors, vector_names, "vector measures")
        if att.get("baseline"):
            for sp in specs:
                missing = [n for n in ("dv01", "gamma", "rate") if n not in sp.bindings and not (n in sp.schema.measures and sp.schema.measures[n].derived)]
                if missing:
                    raise ConfigError(f"attribution.baseline needs the instrument {sp.name!r} to bind {missing} (the engine builds tay_delta and tay_convexity from dv01, gamma and rate)", path="backtest.attribution.baseline", code="CFG-BASELINE")
        prog = bt.get("progress", {})
        if not isinstance(prog, Mapping):
            raise ConfigError(f"backtest.progress must be a mapping {{show: true|false, desc: text}}, got {prog!r}", path="backtest.progress", code="CFG-TYPE")
        _check_keys(prog, PROGRESS_KEYS, "backtest.progress")
        show = _flag(prog.get("show", True), "backtest.progress.show")
        desc = prog.get("desc", "BACKTESTING...")
        if not isinstance(desc, str):
            raise ConfigError(f"backtest.progress.desc must be text, got {desc!r}", path="backtest.progress.desc", code="CFG-TYPE")
        audit_cfg = bt.get("audit")
        if audit_cfg is not None and not isinstance(audit_cfg, (bool, Mapping)):
            raise ConfigError("backtest.audit must be true/false or {measures: [...]}", path="backtest.audit", code="CFG-TYPE")
        audit_map = dict(audit_cfg) if isinstance(audit_cfg, Mapping) else {}
        bad_audit = sorted(set(audit_map) - {"measures"})
        if bad_audit:
            raise ConfigError(f"unknown backtest.audit keys {bad_audit}; allowed ['measures']", path="backtest.audit", code="CFG-UNKNOWN-KEY")
        audit_measures = self._names("backtest.audit.measures", audit_map.get("measures"))
        self._known("backtest.audit.measures", audit_measures, {**scalar_names, **vector_names}, "measures")
        recorded = self._names("backtest.record_signals", bt.get("record_signals"))
        unknown = [n for n in recorded if n not in self.signals]
        if unknown:
            raise ConfigError(f"record_signals names {unknown}, which are not in `signals:` (defined: {sorted(self.signals)})", path="backtest.record_signals", code="CFG-REF")
        return EngineSettings(
            name=bt.get("name", self.cfg.get("name", "backtest")), initial_capital=float(_number(bt.get("initial_capital", 0.0), "backtest.initial_capital", "a finite number")),
            fill_lag=int(_number(bt.get("fill_lag", 0), "backtest.fill_lag", "a whole number of grid points >= 0", integer=True, minimum=0)),
            on_error=_choice(bt.get("on_error", "raise"), _ON_ERROR, "backtest.on_error"), layers=layers, cadence=cadence, strict_layers=bool(att.get("strict", False)),
            layer_tol=float(_number(att.get("tol", 1e-6), "backtest.attribution.tol", "a finite number >= 0", minimum=0.0)), measures=measures, vector_measures=vectors,
            record_signals=recorded, baseline=bool(att.get("baseline", False)), audit=audit_cfg is not None and audit_cfg is not False,  # `audit: {}` is ON with the default measures
            audit_measures=audit_measures, show_progress=show,
            progress_desc=desc, cash_accrual=cash_model, exit_policy=_choice(bt.get("exit_policy", "own_time"), _EXIT_POLICY, "backtest.exit_policy"),
            fill_price=_choice(bt.get("fill_price", "decision"), _FILL_PRICE, "backtest.fill_price"),
        )

    def strategy(self, cfg: Mapping[str, Any]) -> Any:
        from ..strategy import Strategy

        s = cfg.get("strategy") or {}
        _check_keys(s, {"initial_portfolio", "triggers", "cash_accrual", "name", "eval_mode"}, "strategy")
        ip = s.get("initial_portfolio")
        if isinstance(ip, Mapping):
            ip = {k: self.template_ref(v, f"strategy.initial_portfolio.{k}") for k, v in ip.items()}
        elif ip is not None:
            ip = self.template_ref(ip, "strategy.initial_portfolio")
        trig = [self.convert_one("trigger", t, f"strategy.triggers[{i}]") for i, t in enumerate(s.get("triggers") or [])]
        cash = self.component("cash", s["cash_accrual"], "strategy.cash_accrual") if s.get("cash_accrual") else None
        kw: Dict[str, Any] = {"name": s.get("name", self.cfg.get("name", "strategy"))}
        if "eval_mode" in s:
            kw["eval_mode"] = s["eval_mode"]
        return Strategy(ip, trig, cash, **kw)


def _check_tieout(tie: Any) -> None:
    """`tieout: {tolerances: {<level>.<quantity>[.<asset class>]: <number | {rel, floor, expected}>}}` (spec X3): validated here, used by `pricebt tieout`."""
    if tie is None:
        return
    if not isinstance(tie, Mapping):
        raise ConfigError("tieout must be a mapping {tolerances: {...}}", path="tieout", code="CFG-TYPE")
    _check_keys(tie, TIEOUT_KEYS, "tieout")
    from ..tieout.tolerances import validate_declaration

    validate_declaration(tie.get("tolerances") or {}, "tieout.tolerances")  # the harness's own check: keys, numbers, {rel, floor, expected, strict, reason}; an invalid one is a ConfigError


def _outputs(cfg: Mapping[str, Any], base: Path) -> Dict[str, Any]:
    out = dict(cfg.get("outputs") or {})
    if isinstance(out.get("dir"), str) and not Path(out["dir"]).is_absolute():
        out["dir"] = str((base / out["dir"]).resolve())  # relative to the config file, like every other path
    return out


def config_hash(cfg: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode("utf8")).hexdigest()[:16]


def build(cfg: Mapping[str, Any], *, base_dir: Optional[Path] = None, stack: Sequence[Mapping[str, Any]] = ()) -> Built:
    """Validate and construct everything (nothing runs). `cfg` is a plain dict (see yamlio.load_file); each `stack` overlay (factory / bind / wrap only)
    is applied in order, and `Built.base_hash` is the digest of everything the stacks do NOT own."""
    import pricebt.strategy  # noqa: F401  (registers triggers/actions/signals)

    for overlay in stack:
        cfg = apply_stack(cfg, overlay)
    _check_keys(cfg, TOP_KEYS, "")
    for key in _MAPPING_BLOCKS:
        if cfg.get(key) is not None and not isinstance(cfg[key], Mapping):
            raise ConfigError(f"{key} must be a mapping, got {type(cfg[key]).__name__}", path=key, code="CFG-TYPE")
    _check_tieout(cfg.get("tieout"))
    b = _Builder(cfg, Path(base_dir or "."))
    _import_registered_extras(cfg, b.allow)
    for i, st in enumerate((cfg.get("registry", {}) or {}).get("setup", ()) or ()):  # e.g. install a guard before any foreign code runs
        if set(st) - {"call", "kwargs"} or "call" not in st:
            raise ConfigError("a setup step is {call: 'pkg.mod:fn', kwargs: {...}}", path=f"registry.setup[{i}]", code="CFG-UNKNOWN-KEY")
        b.dotted({"$call": st["call"], "kwargs": st.get("kwargs", {})}, f"registry.setup[{i}]")
    bt = cfg.get("backtest") or {}
    _check_keys(bt, BACKTEST_KEYS, "backtest")
    tctx = b.time_context(bt)
    market, _ = b.market(cfg)
    for name, d in (cfg.get("instruments") or {}).items():
        b.instruments[name] = b.instrument(name, d)
    for name, d in (cfg.get("trades") or {}).items():
        b.trades[name] = b.trade(d, f"trades.{name}", name=name)
    for name, d in (cfg.get("signals") or {}).items():
        sig = b.component("signal", d, f"signals.{name}")
        if getattr(sig, "name", None) in (None, ""):
            try:
                sig.name = name
            except (AttributeError, TypeError):
                pass
        elif sig.name != name:
            raise ConfigError(f"the signal under the key {name!r} is named {sig.name!r} by its own definition: the engine records and resolves signals by that name, so use one name (drop `name:` or make it {name!r})",
                              path=f"signals.{name}", code="CFG-SIGNAL-NAME")
        b.signals[name] = sig
    grid = b.grid(bt, tctx, market)
    cash = b.component("cash", bt["cash_accrual"], "backtest.cash_accrual") if bt.get("cash_accrual") else None
    settings = b.settings(bt, cash)
    strategy = b.strategy(cfg)
    problems = strategy.validate() if hasattr(strategy, "validate") else []
    if problems:
        raise ConfigError("; ".join(problems), path="strategy", code="CFG-STRATEGY")
    return Built(dict(cfg), config_hash(cfg), grid, market, strategy, settings, b.signals or None, b.instruments, _outputs(cfg, b.base), base_digest(cfg))


def _import_registered_extras(cfg: Mapping[str, Any], allow: Sequence[str]) -> None:
    """Side-effect imports a config asks for by name: `registry: {import: [package.module, ...]}` (allow-listed prefixes only). Nothing is ever imported
    because of TEXT elsewhere in the config: an adapter is imported only through a dotted path (`factory`, `wrap`, `type`, `import`)."""
    import importlib

    for mod in (cfg.get("registry", {}) or {}).get("import", ()) or ():
        if not any(mod == a or mod.startswith(a + ".") for a in allow):
            raise ConfigError(f"module {mod!r} is not under an allowed prefix {list(allow)}", path="registry.import", code="CFG-ALLOW")
        try:
            importlib.import_module(mod)
        except ImportError as e:
            raise ConfigError(f"cannot import plugin {mod!r}: {e}", path="registry.import", code="CFG-IMPORT") from e
