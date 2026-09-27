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
from datetime import date as _date, datetime, time as _time
from typing import Any, Dict, Optional, Sequence, Tuple

import pandas as pd

from pricebt.assets.config import validate_resolved
from pricebt.assets.fx import FxConfig, FxEvaluator
from pricebt.assets.namespace import AssetNamespace
from pricebt.assets.registry import AssetRegistry
from pricebt.common import AggregationLevel
from pricebt.errors import ConfigError, MarketDataUnavailable, NotSupportedError, PricebtError
from pricebt.instrument import Instrument, instrument_identity
from pricebt.instrument import _freeze  # noqa: F401 -- reuse the one freeze rule (DESIGN.md 5.1 item 3)
from pricebt.markets import HistoricalPricingContext, PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import Price, RiskMeasureWithCurrencyParameter, RiskMeasureWithFiniteDifferenceParameter
from pricebt.risk.results import (
    FloatWithInfo,
    LazyFuture,
    MultipleRiskMeasureResult,
    PortfolioRiskResult,
    PricingFuture,
    RiskKey,
    SeriesWithInfo,
    _MultiMeasureFuture,  # noqa: F401 -- purpose-built for exactly this: a per-measure-lazy multi-measure future
    make_bucketed_frame,
)

__all__ = ["PricingService", "engine_calc", "engine_resolve"]

# Units a currency-parameterised measure may convert (the extensive, currency-denominated ones).
_CONVERTIBLE_UNITS = {"ccy", "ccy_per_bp", "ccy_per_bp2"}

_NO_MARKET = object()  # cache sentinel: "evaluated, and there is none" (distinct from "not yet evaluated")


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

    def _eval_unit_cached(self, asset, resolved_terms: dict, res_date: _date, res_csa: Optional[str], d: _date, csa: Optional[str], function: str) -> Any:
        """The RAW value of `function` for one unit of this asset's resolved terms (a float for a
        `functions:` entry, or whatever a `portfolio_functions:` entry returns when called with
        `trades=[trade], weights=[1.0]` -- DESIGN.md section 8.1 rule 6). Cached; never scaled by
        quantity or FX here."""
        frozen = tuple(sorted(resolved_terms.items()))
        key = (asset.name, frozen, d, function, csa)
        if asset.build_on == "resolve_date":
            key = key + (res_date, res_csa)
        if key in self._unit_value_cache:
            return self._unit_value_cache[key]
        trade = self._trade_for(asset, resolved_terms, res_date, res_csa, d, csa)
        mkt = self.market(asset, d, csa)
        base = {**self._base_injected(d, csa), "pricebt_asset": asset.name, "pricebt_currency": asset.currency, "market": mkt}
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

    def unit_value(self, inst: Instrument, d: _date, function: str, csa: Optional[str]) -> float:
        resolved_inst = self.resolve(inst, d, csa)
        asset = self.asset_for(resolved_inst)
        rk = resolved_inst.resolution_key
        raw = self._eval_unit_cached(asset, resolved_inst.resolved_terms, rk.date, resolved_inst.resolution_csa, d, csa, function)
        return float(raw)

    # ------------------------------------------------------------------------------ group evaluation (section 6.3/8.2)

    def _portfolio_value_from_entries(self, asset, d: _date, function: str, csa: Optional[str], entries: Sequence[Tuple[dict, _date, Optional[str], float]]) -> Any:
        key_entries = []
        for rt, res_date, res_csa, w in entries:
            frozen = tuple(sorted(rt.items()))
            key_entries.append((frozen, res_date, res_csa, w) if asset.build_on == "resolve_date" else (frozen, w))
        key = (asset.name, d, function, csa, tuple(key_entries))
        if key in self._portfolio_value_cache:
            return self._portfolio_value_cache[key]
        trades = [self._trade_for(asset, rt, res_date, res_csa, d, csa) for rt, res_date, res_csa, _w in entries]
        weights = [w for *_rest, w in entries]
        mkt = self.market(asset, d, csa)
        injected = {
            **self._base_injected(d, csa),
            "pricebt_asset": asset.name,
            "pricebt_currency": asset.currency,
            "market": mkt,
            "trades": trades,
            "weights": weights,
        }
        raw = self._ns(asset).eval(function, **injected)
        spec = asset.portfolio_functions.get(function)
        result = dict(raw) if spec is not None and spec.returns == "buckets" else float(raw)
        self._portfolio_value_cache[key] = result
        return result

    def portfolio_value(self, asset, d: _date, function: str, insts: Sequence[Instrument], csa: Optional[str]) -> Any:
        entries = []
        for inst in insts:
            resolved_inst = self.resolve(inst, d, csa)
            rk = resolved_inst.resolution_key
            entries.append((resolved_inst.resolved_terms, rk.date, resolved_inst.resolution_csa, resolved_inst.quantity_))
        return self._portfolio_value_from_entries(asset, d, function, csa, entries)

    # ------------------------------------------------------------------------------ attribute (section 4.3)

    def attribute(self, inst: Instrument, field: str) -> Any:
        asset = self.asset_for(inst)
        resolved_terms = inst.resolved_terms
        frozen = tuple(sorted(resolved_terms.items()))
        key = (asset.name, frozen, field, inst.quantity_)
        if key in self._attribute_cache:
            return self._attribute_cache[key]
        rk = inst.resolution_key
        res_date, res_csa = rk.date, inst.resolution_csa
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

    def _check_no_extra_parameters(self, asset, risk) -> None:
        params = risk.parameters
        if params is None:
            return
        currency_field = self._currency_field_name(risk)
        for f in dataclasses.fields(params):
            if f.name in ("parameter_type", "aggregation_level", currency_field):
                continue
            if getattr(params, f.name) is not None:
                # pricebt DEV-I8: gs honours extra measure parameters (e.g. bump_size) server-side;
                # pricebt has no server, so it raises here instead (DESIGN.md section 8.1 rule 3a).
                raise NotSupportedError(f"asset {asset.name}: {risk!r} sets {f.name}; pricebt passes only aggregation_level and currency to asset configs")

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

    def _scale_bucket(self, raw: dict, spec, quantity_: float, func_ccy: str, target_ccy: str, d: _date) -> dict:
        factor = 1.0
        if spec.scale_with_quantity:
            factor *= quantity_
        if target_ccy != func_ccy:
            factor *= self.fx(func_ccy, target_ccy, d)
        if factor == 1.0:
            return dict(raw)
        return {k: v * factor for k, v in raw.items()}

    def value(self, inst: Instrument, d: _date, risk, csa: Optional[str]):
        # step 1
        if risk.name == "ResolvedInstrumentValues":
            return self.resolve(inst, d, csa)
        # step 2
        if risk.name == "DollarPrice":
            risk = Price(currency="USD")

        asset = self.asset_for(inst)
        mapping = asset.risk_measures.get(risk.name)
        if mapping is None and risk.base_name:
            mapping = asset.risk_measures.get(risk.base_name)
        if mapping is None:
            raise ConfigError(f"asset {asset.name} has no mapping for risk measure {risk.name}; add it under risk_measures:", asset=asset.name)
        self._check_no_extra_parameters(asset, risk)

        # pricebt DEV-I4: gs returns a small DataFrame for IRDelta(aggregation_level=Type); pricebt
        # returns FloatWithInfo for Type/Asset/Class and a bucketed DataFrameWithInfo for None/Point
        # (DESIGN.md section 8.1 rule 5).
        has_agg_level = isinstance(risk, RiskMeasureWithFiniteDifferenceParameter)
        agg = risk.aggregation_level if has_agg_level else None
        want_bucketed = has_agg_level and (agg is None or agg == AggregationLevel.Point)

        scalar_via_bucket_sum = False
        if want_bucketed:
            fname, is_bucketed = mapping.bucketed, True
            if fname is None:
                raise ConfigError(f"asset {asset.name}: risk measure {risk.name} has no bucketed mapping", asset=asset.name)
        else:
            fname, is_bucketed = mapping.scalar, False
            if fname is None:
                fname = mapping.bucketed
                if fname is None:
                    raise ConfigError(f"asset {asset.name}: risk measure {risk.name} has no scalar mapping", asset=asset.name)
                if has_agg_level:
                    # DESIGN.md section 8.1 rule 5's FIRST bullet: aggregation_level explicitly
                    # resolved to Type/Asset/Class (scalar form) but only a bucketed function is
                    # mapped -- sum the buckets into the scalar.
                    scalar_via_bucket_sum = True
                else:
                    # DESIGN.md section 8.1 rule 5's THIRD bullet: the measure has no
                    # aggregation_level concept at all, so a missing scalar mapping means SELECT
                    # the bucketed form (LazyFuture/DataFrameWithInfo) -- never sum it.
                    is_bucketed = True

        spec = asset.functions.get(fname)
        if spec is None:
            spec = asset.portfolio_functions[fname]
        # pricebt DEV-I5: gs defaults an unparameterised currency-bearing risk to USD (per the
        # IRDelta docstring); pricebt uses the function's own currency instead (decision 0.5).
        func_ccy = spec.currency or asset.currency
        risk_ccy = getattr(risk, "currency", None)
        target_ccy = func_ccy if risk_ccy in (None, "local") else risk_ccy
        if target_ccy != func_ccy and spec.unit not in _CONVERTIBLE_UNITS:
            raise ConfigError(f"measure {risk.name} has unit {spec.unit}; it cannot be converted to {target_ccy}", asset=asset.name)

        resolved_inst = self.resolve(inst, d, csa)
        rk = resolved_inst.resolution_key

        if is_bucketed:
            return self._lazy_value(inst, resolved_inst, asset, fname, spec, func_ccy, target_ccy, d, csa, risk)

        raw = self._eval_unit_cached(asset, resolved_inst.resolved_terms, rk.date, resolved_inst.resolution_csa, d, csa, fname)
        raw_total = sum(raw.values()) if scalar_via_bucket_sum else raw
        val = self._scale_scalar(raw_total, spec, resolved_inst.quantity_, func_ccy, target_ccy, d)
        unit = _unit_dict(spec, target_ccy)
        key = RiskKey(provider=None, date=d, market=None, params=None, scenario=None, risk_measure=risk)
        return FloatWithInfo(val, risk_key=key, unit=unit)

    def _lazy_value(self, orig_inst: Instrument, resolved_inst: Instrument, asset, fname: str, spec, func_ccy: str, target_ccy: str, d: _date, csa: Optional[str], risk) -> LazyFuture:
        rk = resolved_inst.resolution_key
        res_date, res_csa = rk.date, resolved_inst.resolution_csa
        frozen_resolved = tuple(sorted(resolved_inst.resolved_terms.items()))
        group_key = (asset.name, asset.market_key, d, csa, fname, target_ccy)
        # pricebt: member carries res_date/res_csa and the originating risk measure too (beyond
        # DESIGN.md section 8.2's literal 3-tuple) so group_aggregate can build a resolve_date
        # asset's trades on the right market without re-deriving them from PricebtSession.current,
        # and so it can fill RiskKey.risk_measure (section 8.2: "pricebt fills date and
        # risk_measure") the same way the scalar path does. Every instrument in one group_key was
        # evaluated with the SAME `measures` tuple element (Portfolio.calc passes one shared risk
        # object to every child), so any one member's risk is the group's risk.
        member = (instrument_identity(orig_inst), frozen_resolved, resolved_inst.quantity_, res_date, res_csa, risk)
        service = self
        quantity_ = resolved_inst.quantity_
        resolved_terms = resolved_inst.resolved_terms

        def thunk():
            raw = service._eval_unit_cached(asset, resolved_terms, res_date, res_csa, d, csa, fname)
            scaled = service._scale_bucket(raw, spec, quantity_, func_ccy, target_ccy, d)
            key = RiskKey(provider=None, date=d, market=None, params=None, scenario=None, risk_measure=risk)
            return make_bucketed_frame(scaled, labels=spec.labels, risk_key=key, unit=_unit_dict(spec, target_ccy))

        return LazyFuture(thunk, group_key, member, service)

    def group_aggregate(self, group_key, members):
        asset_name, _market_key, d, csa, fname, target_ccy = group_key
        asset = self.registry[asset_name]
        spec = asset.functions.get(fname)
        if spec is None:
            spec = asset.portfolio_functions[fname]
        func_ccy = spec.currency or asset.currency
        entries = [(dict(frozen_resolved), res_date, res_csa, quantity_) for _identity, frozen_resolved, quantity_, res_date, res_csa, _risk in members]
        raw = self._portfolio_value_from_entries(asset, d, fname, csa, entries)
        scaled = self._scale_bucket(raw, spec, 1.0, func_ccy, target_ccy, d)  # weights already carry quantity_
        risk = members[0][5]
        key = RiskKey(provider=None, date=d, market=None, params=None, scenario=None, risk_measure=risk)
        return make_bucketed_frame(scaled, labels=spec.labels, risk_key=key, unit=_unit_dict(spec, target_ccy))


# ====================================================================================== engine seams


def _instrument_calc_value(service: PricingService, inst: Instrument, measures: Tuple[Any, ...], d: _date, csa: Optional[str]):
    """The raw computed value(s) for a standalone `Instrument.calc()` -- unwrapped; the caller
    applies context-based future-wrapping."""
    if len(measures) == 1:
        return service.value(inst, d, measures[0], csa)
    return MultipleRiskMeasureResult((m, service.value(inst, d, m, csa)) for m in measures)


def _single_measure_future(service: PricingService, inst: Instrument, measure, d: _date, csa: Optional[str]) -> PricingFuture:
    value = service.value(inst, d, measure, csa)
    return value if isinstance(value, PricingFuture) else PricingFuture(value)


def _instrument_future(service: PricingService, inst: Instrument, measures: Tuple[Any, ...], d: _date, csa: Optional[str]) -> PricingFuture:
    if len(measures) == 1:
        return _single_measure_future(service, inst, measures[0], d, csa)
    return _MultiMeasureFuture({m: _single_measure_future(service, inst, m, d, csa) for m in measures})


def _calc_portfolio_one_date(service: PricingService, portfolio: Portfolio, measures: Tuple[Any, ...], d: _date, csa: Optional[str]) -> PortfolioRiskResult:
    futures = []
    for child in portfolio.priceables:
        if isinstance(child, Portfolio):
            futures.append(PricingFuture(_calc_portfolio_one_date(service, child, measures, d, csa)))
        else:
            futures.append(_instrument_future(service, child, measures, d, csa))
    return PortfolioRiskResult(portfolio.clone(), measures, futures)


def _unwrap(v):
    return v.result() if isinstance(v, LazyFuture) else v


def _historical_instrument_value(service: PricingService, inst: Instrument, measures: Tuple[Any, ...], dates, csa: Optional[str]):
    # pricebt: historical per-instrument values are materialised eagerly (unlike the single-date
    # path, where a bucketed measure stays a LazyFuture for group aggregation) -- a date-indexed
    # SeriesWithInfo has no per-date grouping partner to defer to, and the engine's own daily loop
    # drives single-date PricingContexts, where laziness actually matters (DESIGN.md section 8.2).
    per_date = {d: _instrument_calc_value(service, inst, measures, d, csa) for d in dates}
    if len(measures) == 1:
        return SeriesWithInfo(pd.Series({d: _unwrap(per_date[d]) for d in dates}))
    return MultipleRiskMeasureResult((m, SeriesWithInfo(pd.Series({d: _unwrap(per_date[d][m]) for d in dates}))) for m in measures)


def _historical_portfolio_result(service: PricingService, portfolio: Portfolio, measures: Tuple[Any, ...], dates, csa: Optional[str]) -> PortfolioRiskResult:
    futures = []
    for child in portfolio.priceables:
        if isinstance(child, Portfolio):
            futures.append(PricingFuture(_historical_portfolio_result(service, child, measures, dates, csa)))
        else:
            futures.append(PricingFuture(_historical_instrument_value(service, child, measures, dates, csa)))
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
        dates, csa = ctx.dates, ctx.csa_term
        if isinstance(priceable, Portfolio):
            result = _historical_portfolio_result(service, priceable, measures_t, dates, csa)
            return fn(result) if fn is not None else result
        result = _historical_instrument_value(service, priceable, measures_t, dates, csa)
        if fn is not None:
            result = fn(result)
        if ctx.is_entered or ctx.is_async:
            return result if isinstance(result, PricingFuture) else PricingFuture(result)
        return result

    d, csa = ctx.pricing_date, ctx.csa_term
    if isinstance(priceable, Portfolio):
        result = _calc_portfolio_one_date(service, priceable, measures_t, d, csa)
        return fn(result) if fn is not None else result
    result = _instrument_calc_value(service, priceable, measures_t, d, csa)
    if fn is not None:
        result = fn(result)
    if ctx.is_entered or ctx.is_async:
        return result if isinstance(result, PricingFuture) else PricingFuture(result)
    return result


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
        return {d: service.resolve(inst, d, ctx.csa_term) for d in ctx.dates}
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
        return {d: _resolve_portfolio_one_date(service, portfolio, d, ctx.csa_term) for d in ctx.dates}
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
