"""PricingService: the only place asset values are produced (DESIGN.md section 6).

Owns every cache in section 6.3, the section 4.3/4.4 evaluation rules (market/resolve/trade/
functions/attributes), the section 8.1 risk-measure -> function mapping (generic across every
asset -- no measure names beyond the two universal steps below are named here), the section 7
currency model, and the section 6.5 engine seams (`engine_calc`/`engine_resolve`, called by
`pricebt.markets._engine_calc`/`_engine_resolve`).

Per the import DAG (DESIGN.md section 3.2 item 5), this module sits after `markets`, `instrument`
and `markets.portfolio`, so it imports those at top level; `pricebt.session` sits after this module
in the DAG, so it is imported only inside `engine_calc`/`engine_resolve`'s bodies.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import date as _date, datetime, time as _time
from typing import Any, Dict, Optional, Sequence, Tuple

import pandas as pd

from pricebt.assets.config import validate_resolved
from pricebt.assets.fx import FxConfig, FxEvaluator
from pricebt.assets.namespace import AssetNamespace
from pricebt.assets.registry import AssetRegistry
from pricebt.common import AggregationLevel
from pricebt.errors import ConfigError, MarketDataUnavailable, NotSupportedError, PricebtError, UnsupportedMeasureError
from pricebt.instrument import Instrument, instrument_identity
from pricebt.instrument import _freeze  # noqa: F401 -- reuse the one freeze rule (DESIGN.md 5.1 item 3)
from pricebt.markets import HistoricalPricingContext, PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import Price, RiskMeasureWithCurrencyParameter, RiskMeasureWithFiniteDifferenceParameter, contracts
from pricebt.risk.results import (
    DataFrameWithInfo,
    FloatWithInfo,
    LazyFuture,
    MultipleRiskMeasureResult,
    PortfolioRiskResult,
    PricingFuture,
    RiskKey,
    SeriesWithInfo,
    _DeferredFuture,
    _historical_key,
    _MultiMeasureFuture,  # noqa: F401 -- purpose-built for exactly this: a per-measure-lazy multi-measure future
    _table_like,
    make_bucketed_frame,
    make_table_frame,
)

__all__ = ["PricingService", "engine_calc", "engine_resolve"]

# Units a currency-parameterised measure may convert (the extensive, currency-denominated ones).
_CONVERTIBLE_UNITS = {"ccy", "ccy_per_bp", "ccy_per_bp2"}

# pricebt DEV-I10: the measure parameters a config function may read, injected as pricebt_<name>
# (None when the measure does not set it). Every other parameter except aggregation_level and the
# currency is refused (DEV-I8).
_PASS_THROUGH_PARAMS = ("bump_size", "finite_difference_method", "local_curve", "scale_factor")

_NO_MARKET = object()  # cache sentinel: "evaluated, and there is none" (distinct from "not yet evaluated")


def _measure_params(risk) -> Tuple[Tuple[str, Any], ...]:
    """pricebt DEV-I10: the pass-through parameters `risk` sets, as a sorted `((name, value), ...)`
    tuple. It is part of every unit-value, portfolio-value and group key, so two bump sizes never
    share a cached value. A `FiniteDifferenceMethod` stays the enum (a `str`)."""
    p = risk.parameters
    return tuple(sorted((n, getattr(p, n)) for n in _PASS_THROUGH_PARAMS if getattr(p, n, None) is not None))


def _param_env(params: Tuple[Tuple[str, Any], ...]) -> Dict[str, Any]:
    """The four `pricebt_<parameter>` injected names, always present (None when not set), so an
    expression that reads one never raises NameError."""
    env: Dict[str, Any] = {f"pricebt_{n}": None for n in _PASS_THROUGH_PARAMS}
    env.update((f"pricebt_{n}", v) for n, v in params)
    return env


def _is_rows(raw) -> bool:
    """A `returns: buckets` value given as a list of per-row dicts (docs/v2/IR_RISK_DESIGN.md
    R2-14), as opposed to a `{mkt_point: value}` mapping or a list of `(point, value)` pairs, which
    `dict()` reads as before."""
    return isinstance(raw, (list, tuple)) and all(isinstance(r, Mapping) for r in raw)


def _bucket_total(raw) -> float:
    return sum(r["value"] for r in raw) if _is_rows(raw) else sum(raw.values())


def _unit_dict(spec, target_ccy: str) -> Dict[str, int]:
    """DESIGN.md section 4.2's units table, verbatim: a convertible (ccy*) unit reports the target
    currency; `number` is dimensionless (`{}`); every other unit (bp/pct/decimal) reports itself.
    Shared by the scalar, lazy and group_aggregate paths so all three agree."""
    if spec.unit in _CONVERTIBLE_UNITS:
        return {target_ccy: 1}
    if spec.unit == "number":
        return {}
    return {spec.unit: 1}


class PricingService:
    """DESIGN.md section 6.2. `registry`, `tz` and `eod_time` are plain attributes; the FX config
    is held privately (`fx` is the public method of section 6.2's surface, not an attribute)."""

    def __init__(self, registry: AssetRegistry, fx: Optional[FxConfig] = None, *, tz: str, eod_time: str):
        self.registry = registry
        self.tz = tz
        self.eod_time = eod_time
        self._fx_config = fx
        self._fx_evaluator: Optional[FxEvaluator] = FxEvaluator(fx) if fx is not None else None
        self._namespaces: Dict[str, AssetNamespace] = {}  # persists across reset() (section 4.4: once per process)
        self._market_cache: Dict[tuple, Any] = {}
        self._resolve_cache: Dict[tuple, dict] = {}
        self._trade_cache: Dict[tuple, Any] = {}
        self._unit_value_cache: Dict[tuple, Any] = {}
        self._portfolio_value_cache: Dict[tuple, Any] = {}
        self._attribute_cache: Dict[tuple, Any] = {}
        self._fx_cache: Dict[tuple, float] = {}

    def reset(self) -> None:
        # .clear() in place (not reassignment): any caller holding a reference to one of these
        # dicts (a test, e.g.) sees it emptied too.
        for cache in (
            self._market_cache,
            self._resolve_cache,
            self._trade_cache,
            self._unit_value_cache,
            self._portfolio_value_cache,
            self._attribute_cache,
            self._fx_cache,
        ):
            cache.clear()

    # ------------------------------------------------------------------------------ namespaces / injected vars

    def _ns(self, asset) -> AssetNamespace:
        ns = self._namespaces.get(asset.name)
        if ns is None:
            ns = self._namespaces[asset.name] = AssetNamespace(asset)
        return ns

    def _base_injected(self, d: _date, csa: Optional[str]) -> Dict[str, Any]:
        hh, mm = (int(x) for x in self.eod_time.split(":"))
        naive = datetime.combine(d, _time(hh, mm))
        return {
            "pricebt_date": d,
            "pricebt_timestamp": pd.Timestamp(naive, tz=self.tz),
            "pricebt_datetime": naive,
            "pricebt_csa": csa,
        }

    # ------------------------------------------------------------------------------ matching (section 5.3)

    def asset_for(self, inst: Instrument):
        cfg = self.registry.match(type(inst).__name__, inst.kwargs, inst.pricebt_asset)
        inst._matched_asset = cfg.name
        return cfg

    # ------------------------------------------------------------------------------ market (section 6.2/6.3)

    def _get_market_raw(self, asset, d: _date, csa: Optional[str]) -> Any:
        key = (asset.market_key, d, csa)
        if key in self._market_cache:
            return self._market_cache[key]
        evaluating = self.registry[self.registry.evaluating_asset(asset.market_key)]
        value = self._ns(evaluating).eval("market", **self._base_injected(d, csa))
        stored = value if value is not None else _NO_MARKET
        self._market_cache[key] = stored
        return stored

    def market(self, asset, d: _date, csa: Optional[str]) -> Any:
        value = self._get_market_raw(asset, d, csa)
        if value is _NO_MARKET:
            raise MarketDataUnavailable(asset.name, d, csa)
        return value

    def has_market(self, asset, d: _date, csa: Optional[str]) -> bool:
        return self._get_market_raw(asset, d, csa) is not _NO_MARKET

    # ------------------------------------------------------------------------------ resolve (section 6.2/6.3/4.4)

    def _kwargs_with_defaults(self, inst: Instrument, asset) -> Dict[str, Any]:
        present = inst.kwargs
        missing = {k: v for k, v in asset.defaults.items() if present.get(k) is None}
        if not missing:
            return present
        # Route through Instrument.clone() so default values get the SAME enum coercion as real
        # kwargs (DESIGN.md section 5.2) without pricing.py re-implementing that coercion table.
        return inst.clone(**missing).kwargs

    def resolve(self, inst: Instrument, d: _date, csa: Optional[str]) -> Instrument:
        if inst.resolved_terms is not None:
            return inst.clone()  # idempotent: same resolved_terms/resolution_key/resolution_csa/unresolved

        asset = self.asset_for(inst)
        kw = self._kwargs_with_defaults(inst, asset)
        key = (asset.name, _freeze(kw), d, csa)
        if key in self._resolve_cache:
            resolved = self._resolve_cache[key]
        else:
            if asset.resolve_expr is not None:
                mkt = self.market(asset, d, csa)
                injected = {
                    **self._base_injected(d, csa),
                    "pricebt_asset": asset.name,
                    "pricebt_currency": asset.currency,
                    "market": mkt,
                    "kwargs": dict(kw),
                }
                resolved = dict(self._ns(asset).eval("resolve", **injected))
            else:
                resolved = dict(kw)
            validate_resolved(resolved)
            self._resolve_cache[key] = resolved

        resolution_key = RiskKey(provider=None, date=d, market=None, params=None, scenario=None, risk_measure=None)
        pre = inst.clone()
        new_inst = inst.clone()
        new_inst._set_resolution(resolved, resolution_key, csa, pre)
        return new_inst

    # ------------------------------------------------------------------------------ trade (section 6.4)

    def _trade_for(self, asset, resolved_terms: dict, res_date: _date, res_csa: Optional[str], d: _date, csa: Optional[str]) -> Any:
        frozen = tuple(sorted(resolved_terms.items()))
        if asset.build_on == "resolve_date":
            key = (asset.name, frozen, res_date, res_csa)
            build_date, build_csa = res_date, res_csa
        else:
            key = (asset.name, frozen, d, csa)
            build_date, build_csa = d, csa
        if key in self._trade_cache:
            return self._trade_cache[key]
        if asset.trade_expr is not None:
            mkt = self.market(asset, build_date, build_csa)
            injected = {
                **self._base_injected(build_date, build_csa),
                "pricebt_asset": asset.name,
                "pricebt_currency": asset.currency,
                "market": mkt,
                "resolved": dict(resolved_terms),
            }
            if asset.resolve_expr is None:
                # DESIGN.md section 4.3: `kwargs` is injected into trade/functions evaluations only
                # when `resolve:` is absent. `resolved_terms` IS kwargs-after-defaults in that case
                # (resolve() sets `resolved = dict(kw)` verbatim), so this is a fresh copy of it.
                injected["kwargs"] = dict(resolved_terms)
            trade = self._ns(asset).eval("trade", **injected)
        else:
            trade = dict(resolved_terms)  # DESIGN.md section 4.2: trade.expr default is `resolved`
        self._trade_cache[key] = trade
        return trade

    # ------------------------------------------------------------------------------ unit evaluation (section 6.3)

    def _eval_unit_cached(self, asset, resolved_terms: dict, res_date: _date, res_csa: Optional[str], d: _date, csa: Optional[str], function: str, params: Tuple[Tuple[str, Any], ...] = ()) -> Any:
        """The RAW value of `function` for one unit of this asset's resolved terms (a float or a
        frame for a `functions:` entry, or whatever a `portfolio_functions:` entry returns when
        called with `trades=[trade], weights=[1.0]` -- DESIGN.md section 8.1 rule 6), with the
        measure `params` (`_measure_params`) injected. Cached; never scaled by quantity or FX here."""
        frozen = tuple(sorted(resolved_terms.items()))
        key = (asset.name, frozen, d, function, csa, params)  # pricebt DEV-I10: params in the key
        if asset.build_on == "resolve_date":
            key = key + (res_date, res_csa)
        if key in self._unit_value_cache:
            return self._unit_value_cache[key]
        trade = self._trade_for(asset, resolved_terms, res_date, res_csa, d, csa)
        mkt = self.market(asset, d, csa)
        base = {**self._base_injected(d, csa), **_param_env(params), "pricebt_asset": asset.name, "pricebt_currency": asset.currency, "market": mkt}
        if function in asset.functions:
            # DESIGN.md section 4.3: `resolved` is available in trade, functions and attributes --
            # NOT in portfolio_functions (the `else` branch below), which gets `trades`/`weights`.
            injected = {**base, "trade": trade, "resolved": dict(resolved_terms)}
            if asset.resolve_expr is None:
                # `kwargs` is injected into functions evaluations too when `resolve:` is absent
                # (same reasoning as `_trade_for` above).
                injected["kwargs"] = dict(resolved_terms)
        else:
            injected = {**base, "trades": [trade], "weights": [1.0]}
        raw = self._ns(asset).eval(function, **injected)
        self._unit_value_cache[key] = raw
        return raw

    def unit_value(self, inst: Instrument, d: _date, function: str, csa: Optional[str], *, params: Tuple[Tuple[str, Any], ...] = ()) -> float:
        resolved_inst = self.resolve(inst, d, csa)
        asset = self.asset_for(resolved_inst)
        rk = resolved_inst.resolution_key
        raw = self._eval_unit_cached(asset, resolved_inst.resolved_terms, rk.date, resolved_inst.resolution_csa, d, csa, function, params)
        return float(raw)

    # ------------------------------------------------------------------------------ group evaluation (section 6.3/8.2)

    def _portfolio_value_from_entries(self, asset, d: _date, function: str, csa: Optional[str], entries: Sequence[Tuple[dict, _date, Optional[str], float]], params: Tuple[Tuple[str, Any], ...] = ()) -> Any:
        spec = asset.functions.get(function)
        if spec is None:
            spec = asset.portfolio_functions.get(function)
        # pricebt (P6.2 finding 3): `weights` is what the expression sees as each trade's size, so it
        # must respect DESIGN.md section 5.4 the same way the single-instrument path does
        # (_eval_unit_cached always evaluates with weights=[1.0]; quantity_ is applied afterwards by
        # _scale_scalar/_scale_bucket, gated on scale_with_quantity). This path has no such
        # after-the-fact multiplication -- group_aggregate calls _scale_bucket with quantity_=1.0,
        # "weights already carry quantity_" -- so an intensive unit (scale_with_quantity=False) must
        # never see the real quantity_ here either, or it gets scaled twice over (once by being
        # baked into weights, and DESIGN says not at all).
        scale_with_quantity = spec is None or spec.scale_with_quantity
        key_entries = []
        for rt, res_date, res_csa, w in entries:
            frozen = tuple(sorted(rt.items()))
            kw = w if scale_with_quantity else 1.0
            key_entries.append((frozen, res_date, res_csa, kw) if asset.build_on == "resolve_date" else (frozen, kw))
        key = (asset.name, d, function, csa, tuple(key_entries), params)  # pricebt DEV-I10: params in the key
        if key in self._portfolio_value_cache:
            return self._portfolio_value_cache[key]
        trades = [self._trade_for(asset, rt, res_date, res_csa, d, csa) for rt, res_date, res_csa, _w in entries]
        weights = [w if scale_with_quantity else 1.0 for *_rest, w in entries]
        mkt = self.market(asset, d, csa)
        injected = {
            **self._base_injected(d, csa),
            **_param_env(params),
            "pricebt_asset": asset.name,
            "pricebt_currency": asset.currency,
            "market": mkt,
            "trades": trades,
            "weights": weights,
        }
        raw = self._ns(asset).eval(function, **injected)
        if spec is not None and spec.returns == "buckets":
            result = [dict(r) for r in raw] if _is_rows(raw) else dict(raw)
        else:
            result = float(raw)
        self._portfolio_value_cache[key] = result
        return result

    def portfolio_value(self, asset, d: _date, function: str, insts: Sequence[Instrument], csa: Optional[str], *, params: Tuple[Tuple[str, Any], ...] = ()) -> Any:
        entries = []
        for inst in insts:
            resolved_inst = self.resolve(inst, d, csa)
            rk = resolved_inst.resolution_key
            entries.append((resolved_inst.resolved_terms, rk.date, resolved_inst.resolution_csa, resolved_inst.quantity_))
        return self._portfolio_value_from_entries(asset, d, function, csa, entries, params)

    # ------------------------------------------------------------------------------ attribute (section 4.3)

    def attribute(self, inst: Instrument, field: str) -> Any:
        asset = self.asset_for(inst)
        resolved_terms = inst.resolved_terms
        frozen = tuple(sorted(resolved_terms.items()))
        rk = inst.resolution_key
        res_date, res_csa = rk.date, inst.resolution_csa
        # pricebt (P6.2 finding 4): the injected env below (pricebt_date/pricebt_timestamp/
        # pricebt_csa, and market when the expression reads it) depends on res_date/res_csa, not
        # just (asset, frozen resolved, field, quantity_) -- every sibling cache in this file
        # (_trade_for, _eval_unit_cached, _portfolio_value_from_entries, resolve) threads date/csa
        # through its key to match what it injects; this one didn't. Two instruments with the same
        # (asset, resolved_terms, quantity_) but resolved on different dates/csa -- an ordinary
        # occurrence when `resolve:` is absent, since resolved_terms is then just kwargs verbatim,
        # independent of the resolution date -- would otherwise collide and silently return the
        # first instrument's market/date/csa-dependent attribute value for the second.
        key = (asset.name, frozen, field, inst.quantity_, res_date, res_csa)
        if key in self._attribute_cache:
            return self._attribute_cache[key]
        code = asset.code(field)
        injected = {
            **self._base_injected(res_date, res_csa),
            "pricebt_asset": asset.name,
            "pricebt_currency": asset.currency,
            "resolved": dict(resolved_terms),
            "pricebt_quantity": inst.quantity_,
        }
        if "market" in code.co_names:
            injected["market"] = self.market(asset, res_date, res_csa)
        value = self._ns(asset).eval(field, **injected)
        self._attribute_cache[key] = value
        return value

    # ------------------------------------------------------------------------------ fx (section 4.5/7)

    def fx(self, from_ccy: str, to_ccy: str, d: _date) -> float:
        if from_ccy == to_ccy:
            return 1.0
        key = (from_ccy, to_ccy, d)
        if key in self._fx_cache:
            return self._fx_cache[key]
        if self._fx_evaluator is None:
            raise ConfigError(f"no FX config: cannot convert {from_ccy} to {to_ccy}")
        rate = self._fx_evaluator.rate(from_ccy, to_ccy, d)
        if rate is None or rate <= 0:
            raise MarketDataUnavailable(f"{from_ccy}{to_ccy}", d, None, "no FX rate")
        self._fx_cache[key] = rate
        return rate

    # ------------------------------------------------------------------------------ measure -> function (section 8.1)

    @staticmethod
    def _currency_field_name(risk) -> str:
        return "value" if isinstance(risk, RiskMeasureWithCurrencyParameter) else "currency"

    def _check_parameters(self, asset, risk, fname: str) -> None:
        params = risk.parameters
        if params is None:
            return
        currency_field = self._currency_field_name(risk)
        names = asset.code(fname).co_names
        for f in dataclasses.fields(params):
            if f.name in ("parameter_type", "aggregation_level", currency_field) or getattr(params, f.name) is None:
                continue
            if f.name in _PASS_THROUGH_PARAMS:
                # pricebt DEV-I10: gs sends these to its server; pricebt injects them as
                # pricebt_<name>, and a function supports one iff its expression names that
                # variable (top-level co_names: a nested scope cannot see injected names anyway).
                if f"pricebt_{f.name}" not in names:
                    raise NotSupportedError(f"asset {asset.name}: {risk!r} sets {f.name}; function {fname!r} does not reference pricebt_{f.name}")
                continue
            # pricebt DEV-I8 (narrowed by DEV-I10): gs honours mkt_marking_options server-side;
            # pricebt has no server, so it raises here instead (DESIGN.md section 8.1 rule 3a).
            raise NotSupportedError(f"asset {asset.name}: {risk!r} sets {f.name}; it is honoured GS server-side and pricebt cannot pass it to an asset config")

    @staticmethod
    def _raise_if_declared(asset, risk, forms: Sequence[str]) -> None:
        """pricebt DEV-I11: raise `UnsupportedMeasureError` if the measure (looked up like its
        mapping: its own name, then its `base_name`) declares the whole measure (`*`) or one of
        `forms` under `unsupported_measures:`. Called only once the requested mapping slot is known
        to be empty: a mapping always wins over a declaration (IR_RISK_DESIGN R2-9)."""
        for name in filter(None, (risk.name, risk.base_name)):
            declared = asset.unsupported_measures.get(name, {})
            for form in ("*", *forms):
                if form in declared:
                    raise UnsupportedMeasureError(asset.name, name, form, declared[form])

    def _scale_scalar(self, raw: float, spec, quantity_: float, func_ccy: str, target_ccy: str, d: _date) -> float:
        v = float(raw)
        # pricebt DEV-I7: gs's IRFwdRate is always percent; pricebt uses whatever unit the asset
        # function declares, and intensive units (this `scale_with_quantity` gate) are never
        # multiplied by quantity_ (DESIGN.md section 5.4).
        if spec.scale_with_quantity:
            v *= quantity_
        if target_ccy != func_ccy:
            v *= self.fx(func_ccy, target_ccy, d)
        return v

    def _scale_bucket(self, raw, spec, quantity_: float, func_ccy: str, target_ccy: str, d: _date):
        factor = 1.0
        if spec.scale_with_quantity:
            factor *= quantity_
        if target_ccy != func_ccy:
            factor *= self.fx(func_ccy, target_ccy, d)
        if _is_rows(raw):
            # IR_RISK_DESIGN R2-14: per-row buckets scale only each row's value (a row without one
            # is left for make_bucketed_frame to reject)
            return [dict(r, value=r["value"] * factor) if "value" in r else dict(r) for r in raw]
        if factor == 1.0:
            return dict(raw)
        return {k: v * factor for k, v in raw.items()}

    @staticmethod
    def _table_value(raw, asset, measure: str, fname: str, spec, quantity_: float, key: RiskKey, unit: dict):
        """A `returns: frame` function's per-unit table as this position's `DataFrameWithInfo`
        (IR_RISK_DESIGN R2-15): quantity scales only `scale_columns` (when the function scales
        with quantity); the frame is checked against the measure's required columns by name."""
        if isinstance(raw, (list, tuple)) and not raw:
            # no rows: still the measure's required columns (contracts.validate_frame needs them)
            raw = pd.DataFrame(columns=list(contracts.FRAME_COLUMNS.get(measure, ())))
        factor = quantity_ if spec.scale_with_quantity else 1.0
        try:
            frame = make_table_frame(raw, risk_key=key, unit=unit, scale_columns=spec.scale_columns, factor=factor)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"function {fname!r} (returns: frame): {exc}", asset=asset.name, key=f"functions.{fname}") from exc
        contracts.validate_frame(measure, frame)
        return frame

    def value(self, inst: Instrument, d: _date, risk, csa: Optional[str]):
        # step 1
        if risk.name == "ResolvedInstrumentValues":
            return self.resolve(inst, d, csa)
        # step 2
        if risk.name == "DollarPrice":
            risk = Price(currency="USD")

        asset = self.asset_for(inst)

        # pricebt DEV-I4: gs returns a small DataFrame for IRDelta(aggregation_level=Type); pricebt
        # returns FloatWithInfo for Type/Asset/Class and a bucketed DataFrameWithInfo for None/Point
        # (DESIGN.md section 8.1 rule 5).
        has_agg_level = isinstance(risk, RiskMeasureWithFiniteDifferenceParameter)
        agg = risk.aggregation_level if has_agg_level else None
        want_bucketed = has_agg_level and (agg is None or agg == AggregationLevel.Point)

        mname = risk.name
        mapping = asset.risk_measures.get(risk.name)
        if mapping is None and risk.base_name:
            mname = risk.base_name
            mapping = asset.risk_measures.get(risk.base_name)
        if mapping is None:
            # DESIGN.md section 8.1 rule 3, last step (IR_RISK_DESIGN R2-10): a preset or fallback
            # key the load-time contract counted toward this measure serves it too, in the form
            # this request needs (an aggregation-level scalar may also sum a bucketed slot, rule 5)
            mname = risk.base_name or risk.name
            forms = ("bucketed",) if want_bucketed else ("scalar", "bucketed") if has_agg_level else ("scalar", "frame", "bucketed")
            key = next((asset.provided_forms[(mname, f)] for f in forms if (mname, f) in asset.provided_forms), None)
            mapping = asset.risk_measures.get(key)

        # pricebt DEV-I11: a declared-unsupported measure/form raises UnsupportedMeasureError, but
        # only where the requested mapping slot is empty (a mapping wins, IR_RISK_DESIGN R2-9). An
        # aggregation-level measure asks for one form; a plain one names no form, so any
        # declaration of it counts when nothing is mapped.
        if mapping is None:
            self._raise_if_declared(asset, risk, ("bucketed",) if want_bucketed else ("scalar",) if has_agg_level else contracts.FORMS)
            raise ConfigError(f"asset {asset.name} has no mapping for risk measure {risk.name}; add it under risk_measures:", asset=asset.name)

        scalar_via_bucket_sum = False
        if want_bucketed:
            fname, is_bucketed = mapping.bucketed, True
            if fname is None:
                self._raise_if_declared(asset, risk, ("bucketed",))
                # IR_RISK_DESIGN R2-13: a bare finite-difference measure is the bucketed form; with
                # no bucketed slot, the scalar slot is the mapped one
                raise ConfigError(
                    f"asset {asset.name}: risk measure {risk.name} has no bucketed mapping; request {risk.name}(aggregation_level='Type') for the scalar form",
                    asset=asset.name,
                )
        else:
            fname, is_bucketed = mapping.scalar, False
            if fname is None:
                fname = mapping.bucketed
                if fname is None:
                    raise ConfigError(f"asset {asset.name}: risk measure {risk.name} has no scalar mapping", asset=asset.name)
                if has_agg_level:
                    # DESIGN.md section 8.1 rule 5's FIRST bullet: aggregation_level explicitly
                    # resolved to Type/Asset/Class (scalar form) but only a bucketed function is
                    # mapped -- sum the buckets into the scalar. A declared-unsupported scalar form
                    # wins over the sum (pricebt DEV-I11): the sum would produce exactly that form.
                    self._raise_if_declared(asset, risk, ("scalar",))
                    scalar_via_bucket_sum = True
                else:
                    # DESIGN.md section 8.1 rule 5's THIRD bullet: the measure has no
                    # aggregation_level concept at all, so a missing scalar mapping means SELECT
                    # the bucketed form (LazyFuture/DataFrameWithInfo) -- never sum it.
                    is_bucketed = True

        self._check_parameters(asset, risk, fname)
        params = _measure_params(risk)

        spec = asset.functions.get(fname)
        if spec is None:
            spec = asset.portfolio_functions[fname]
        # pricebt DEV-I5: gs defaults an unparameterised currency-bearing risk to USD (per the
        # IRDelta docstring); pricebt uses the function's own currency instead (decision 0.5).
        func_ccy = spec.currency or asset.currency
        risk_ccy = getattr(risk, "currency", None)
        target_ccy = func_ccy if risk_ccy in (None, "local") else risk_ccy
        if target_ccy != func_ccy and spec.returns == "frame":
            raise ConfigError(f"measure {risk.name} returns a frame (a table); it cannot be converted to {target_ccy}", asset=asset.name)
        if target_ccy != func_ccy and spec.unit not in _CONVERTIBLE_UNITS:
            raise ConfigError(f"measure {risk.name} has unit {spec.unit}; it cannot be converted to {target_ccy}", asset=asset.name)

        resolved_inst = self.resolve(inst, d, csa)
        rk = resolved_inst.resolution_key

        if is_bucketed:
            return self._lazy_value(inst, resolved_inst, asset, fname, spec, func_ccy, target_ccy, d, csa, risk, params)

        raw = self._eval_unit_cached(asset, resolved_inst.resolved_terms, rk.date, resolved_inst.resolution_csa, d, csa, fname, params)
        unit = _unit_dict(spec, target_ccy)
        key = RiskKey(provider=None, date=d, market=None, params=None, scenario=None, risk_measure=risk)
        if spec.returns == "frame":
            return self._table_value(raw, asset, mname, fname, spec, resolved_inst.quantity_, key, unit)
        raw_total = _bucket_total(raw) if scalar_via_bucket_sum else raw
        val = self._scale_scalar(raw_total, spec, resolved_inst.quantity_, func_ccy, target_ccy, d)
        return FloatWithInfo(val, risk_key=key, unit=unit)

    def _lazy_value(self, orig_inst: Instrument, resolved_inst: Instrument, asset, fname: str, spec, func_ccy: str, target_ccy: str, d: _date, csa: Optional[str], risk, params: Tuple[Tuple[str, Any], ...] = ()) -> LazyFuture:
        rk = resolved_inst.resolution_key
        res_date, res_csa = rk.date, resolved_inst.resolution_csa
        frozen_resolved = tuple(sorted(resolved_inst.resolved_terms.items()))
        # pricebt DEV-I10: params last, so two bump sizes never share a group (and group_key[2]
        # stays the date)
        group_key = (asset.name, asset.market_key, d, csa, fname, target_ccy, params)
        # pricebt: member carries res_date/res_csa and the originating risk measure too (beyond
        # DESIGN.md section 8.2's literal 3-tuple) so group_aggregate can build a resolve_date
        # asset's trades on the right market without re-deriving them from PricebtSession.current,
        # and so it can fill RiskKey.risk_measure (section 8.2: "pricebt fills date and
        # risk_measure") the same way the scalar path does. Every instrument in one group_key was
        # evaluated with the SAME `measures` tuple element (Portfolio.calc passes one shared risk
        # object to every child), so any one member's risk is the group's risk. The measure
        # params (pricebt DEV-I10) come last.
        member = (instrument_identity(orig_inst), frozen_resolved, resolved_inst.quantity_, res_date, res_csa, risk, params)
        service = self
        quantity_ = resolved_inst.quantity_
        resolved_terms = resolved_inst.resolved_terms

        def thunk():
            raw = service._eval_unit_cached(asset, resolved_terms, res_date, res_csa, d, csa, fname, params)
            scaled = service._scale_bucket(raw, spec, quantity_, func_ccy, target_ccy, d)
            key = RiskKey(provider=None, date=d, market=None, params=None, scenario=None, risk_measure=risk)
            return make_bucketed_frame(scaled, labels=spec.labels, risk_key=key, unit=_unit_dict(spec, target_ccy))

        return LazyFuture(thunk, group_key, member, service)

    def group_aggregate(self, group_key, members):
        asset_name, _market_key, d, csa, fname, target_ccy, params = group_key
        asset = self.registry[asset_name]
        spec = asset.functions.get(fname)
        if spec is None:
            spec = asset.portfolio_functions[fname]
        func_ccy = spec.currency or asset.currency
        entries = [(dict(frozen_resolved), res_date, res_csa, quantity_) for _identity, frozen_resolved, quantity_, res_date, res_csa, _risk, _params in members]
        raw = self._portfolio_value_from_entries(asset, d, fname, csa, entries, params)
        scaled = self._scale_bucket(raw, spec, 1.0, func_ccy, target_ccy, d)  # weights already carry quantity_
        risk = members[0][5]
        key = RiskKey(provider=None, date=d, market=None, params=None, scenario=None, risk_measure=risk)
        return make_bucketed_frame(scaled, labels=spec.labels, risk_key=key, unit=_unit_dict(spec, target_ccy))


# ====================================================================================== engine seams


def _instrument_calc_value(service: PricingService, inst: Instrument, measures: Tuple[Any, ...], d: _date, csa: Optional[str]):
    """One date's raw value(s) for a historical instrument result (`_historical_instrument_value`
    unwraps any LazyFuture in them)."""
    if len(measures) == 1:
        return service.value(inst, d, measures[0], csa)
    return MultipleRiskMeasureResult(inst, ((m, service.value(inst, d, m, csa)) for m in measures))


def _single_measure_future(service: PricingService, inst: Instrument, measure, d: _date, csa: Optional[str]) -> PricingFuture:
    value = service.value(inst, d, measure, csa)
    return value if isinstance(value, PricingFuture) else PricingFuture(value)


def _instrument_future(service: PricingService, inst: Instrument, measures: Tuple[Any, ...], d: _date, csa: Optional[str]) -> PricingFuture:
    if len(measures) == 1:
        return _single_measure_future(service, inst, measures[0], d, csa)
    return _MultiMeasureFuture({m: _single_measure_future(service, inst, m, d, csa) for m in measures}, inst)


def _with_fn(future: PricingFuture, fn) -> PricingFuture:
    """gs `Instrument.calc(fn=)`, also per leaf inside `Portfolio.calc`: `fn` applied to the
    evaluated value, an exception it raises stored in the future (gs instrument/core.py,
    `ret.set_exception`)."""
    return future if fn is None else _DeferredFuture(lambda: fn(future.result()))


def _instrument_result(ctx: PricingContext, future: PricingFuture, fn):
    """gs `Instrument.calc`: the future inside an entered (or async) context, else its value -- so
    `PricingContext.current = ...; inst.calc(measure)` gives the value, never a LazyFuture."""
    future = _with_fn(future, fn)
    return future if ctx.is_entered or ctx.is_async else future.result()


def _calc_portfolio_one_date(service: PricingService, portfolio: Portfolio, measures: Tuple[Any, ...], d: _date, csa: Optional[str], fn=None) -> PortfolioRiskResult:
    futures = []
    for child in portfolio.priceables:
        if isinstance(child, Portfolio):
            futures.append(PricingFuture(_calc_portfolio_one_date(service, child, measures, d, csa, fn)))
        else:
            futures.append(_with_fn(_instrument_future(service, child, measures, d, csa), fn))
    return PortfolioRiskResult(portfolio.clone(), measures, futures)


def _unwrap(v):
    return v.result() if isinstance(v, LazyFuture) else v


def _date_indexed(by_date: dict, rep):
    """One measure's per-date values as one historical result: a `SeriesWithInfo` indexed by date
    carrying `rep`'s (the first date's raw value's) unit/risk_key; a bucketed result is one
    `DataFrameWithInfo` indexed by `date` (gs `compose`, IR_RISK_DESIGN section 5 item 3); a table
    (`pricebt_table`, IR_RISK_DESIGN R2-15) is one table with a `date` column prepended to each
    date's rows, concatenated in date order."""
    first = next(iter(by_date.values()), None)
    if isinstance(first, pd.DataFrame) and not getattr(first, "pricebt_table", False):
        # checked on the values: `rep` is still the unevaluated LazyFuture of a bucketed measure
        return DataFrameWithInfo.compose(by_date.values())
    if getattr(rep, "pricebt_table", False):
        # pricebt DEV-R11: gs has no table measures; a historical table is one table, date column first
        frames = []
        for d, table in by_date.items():
            frame = pd.DataFrame(table, copy=True)
            frame.insert(0, "date", d)
            frames.append(frame)
        # dates with no rows add nothing (and concatenating empty frames trips a pandas warning)
        frames = [f for f in frames if len(f)] or frames
        # the priced dates mark it historical (never its column names) and keep a date with no rows
        return _table_like(pd.concat(frames, ignore_index=True), rep, _historical_key(rep.risk_key), tuple(by_date))
    # a historical key has no date (gs `historical_risk_key`), as `FloatWithInfo.compose` gives
    return SeriesWithInfo(pd.Series(by_date), unit=getattr(rep, "unit", None), risk_key=_historical_key(getattr(rep, "risk_key", None)))


def _historical_instrument_value(service: PricingService, inst: Instrument, measures: Tuple[Any, ...], dates, csa: Optional[str]):
    # pricebt: historical per-instrument values are materialised eagerly (unlike the single-date
    # path, where a bucketed measure stays a LazyFuture for group aggregation) -- a date-indexed
    # SeriesWithInfo has no per-date grouping partner to defer to, and the engine's own daily loop
    # drives single-date PricingContexts, where laziness actually matters (DESIGN.md section 8.2).
    per_date = {d: _instrument_calc_value(service, inst, measures, d, csa) for d in dates}
    # pre-existing bug fix (out of P3.5's own scope, but confirmed blocking -- found wiring
    # GenericEngine's HedgeActionImpl, whose `p.results[d][p.risk]` reads a historical
    # per-instrument result's `.unit` after indexing by date): a bare `pd.Series({date: FloatWithInfo,
    # ...})` silently downcasts every FloatWithInfo element to a plain float64 (pandas cannot hold a
    # float subclass in a float64-backed array), so `.unit`/`.risk_key` were lost the moment the
    # per-date values were put in a Series -- SeriesWithInfo carries them on the SERIES itself
    # (`_metadata`, risk/results.py), not per element, and the constructor call here never passed
    # them. Grab them from one representative value (constant across dates for the same instrument
    # and measure) and pass them through explicitly. A table measure becomes one date-stacked
    # table instead (`_date_indexed`).
    if len(measures) == 1:
        rep = next(iter(per_date.values()), None)
        return _date_indexed({d: _unwrap(per_date[d]) for d in dates}, rep)
    result = MultipleRiskMeasureResult(inst, ())
    rep_multi = next(iter(per_date.values()), None)
    for m in measures:
        rep = rep_multi[m] if rep_multi is not None else None
        result[m] = _date_indexed({d: _unwrap(per_date[d][m]) for d in dates}, rep)
    return result


def _historical_portfolio_result(service: PricingService, portfolio: Portfolio, measures: Tuple[Any, ...], dates, csa: Optional[str], fn=None) -> PortfolioRiskResult:
    futures = []
    for child in portfolio.priceables:
        if isinstance(child, Portfolio):
            futures.append(PricingFuture(_historical_portfolio_result(service, child, measures, dates, csa, fn)))
        else:
            futures.append(_with_fn(PricingFuture(_historical_instrument_value(service, child, measures, dates, csa)), fn))
    return PortfolioRiskResult(portfolio.clone(), measures, futures)


def _current_service() -> PricingService:
    from pricebt.session import PricebtSession  # import DAG: session sits after assets.pricing

    session = PricebtSession.current
    if session is None:
        raise PricebtError("no PricebtSession: call PricebtSession.use(assets=[...]) first")
    return session.pricing


def engine_calc(priceable, measures, fn=None):
    service = _current_service()
    ctx = PricingContext.current
    measures_t = tuple(measures) if isinstance(measures, (list, tuple)) else (measures,)

    if isinstance(ctx, HistoricalPricingContext):
        dates, csa = ctx.date_range, ctx.csa_term
        if isinstance(priceable, Portfolio):
            return _historical_portfolio_result(service, priceable, measures_t, dates, csa, fn)
        return _instrument_result(ctx, PricingFuture(_historical_instrument_value(service, priceable, measures_t, dates, csa)), fn)

    d, csa = ctx.pricing_date, ctx.csa_term
    if isinstance(priceable, Portfolio):
        return _calc_portfolio_one_date(service, priceable, measures_t, d, csa, fn)
    return _instrument_result(ctx, _instrument_future(service, priceable, measures_t, d, csa), fn)


def _resolve_instrument(service: PricingService, inst: Instrument, in_place: bool, ctx: PricingContext, is_historical: bool):
    if in_place:
        if is_historical:
            raise RuntimeError("Cannot resolve in place under a HistoricalPricingContext")
        if inst.resolved_terms is not None:
            return None  # already resolved: a no-op, never re-derived (DESIGN.md section 6.2 rule 1)
        pre = inst.clone()
        resolved_clone = service.resolve(inst, ctx.pricing_date, ctx.csa_term)
        inst._set_resolution(resolved_clone.resolved_terms, resolved_clone.resolution_key, resolved_clone.resolution_csa, pre)
        return None
    if is_historical:
        return {d: service.resolve(inst, d, ctx.csa_term) for d in ctx.date_range}
    return service.resolve(inst, ctx.pricing_date, ctx.csa_term)


def _resolve_portfolio_one_date(service: PricingService, portfolio: Portfolio, d: _date, csa: Optional[str]) -> Portfolio:
    children = []
    for child in portfolio.priceables:
        if isinstance(child, Portfolio):
            children.append(_resolve_portfolio_one_date(service, child, d, csa))
        else:
            children.append(service.resolve(child, d, csa))
    return Portfolio(children, name=portfolio.name)


def _resolve_portfolio(service: PricingService, portfolio: Portfolio, in_place: bool, ctx: PricingContext, is_historical: bool):
    if in_place:
        for child in portfolio.priceables:
            if isinstance(child, Portfolio):
                _resolve_portfolio(service, child, True, ctx, is_historical)
            else:
                _resolve_instrument(service, child, True, ctx, is_historical)
        return None
    if is_historical:
        return {d: _resolve_portfolio_one_date(service, portfolio, d, ctx.csa_term) for d in ctx.date_range}
    return _resolve_portfolio_one_date(service, portfolio, ctx.pricing_date, ctx.csa_term)


def engine_resolve(priceable, in_place: bool):
    service = _current_service()
    ctx = PricingContext.current
    is_historical = isinstance(ctx, HistoricalPricingContext)

    if isinstance(priceable, Portfolio):
        result = _resolve_portfolio(service, priceable, in_place, ctx, is_historical)
    else:
        result = _resolve_instrument(service, priceable, in_place, ctx, is_historical)

    if in_place:
        return PricingFuture(None) if (ctx.is_entered or ctx.is_async) else None
    if ctx.is_entered or ctx.is_async:
        return result if isinstance(result, PricingFuture) else PricingFuture(result)
    return result
