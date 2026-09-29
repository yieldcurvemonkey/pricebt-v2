"""Snapshot gs_quant 1.5.4's public API shape into tests/data/gs_api_1_5_4.json and
tests/data/gs_instruments_1_5_4.json (IMPLEMENTATION_PLAN.md P1.6; DESIGN.md section 12.3).

RUN ONLY WITH THE BASE PYTHON (gs_quant 1.5.4 is the pinned reference version, DESIGN section 3.3):

    C:\\Users\\chris\\anaconda3\\python.exe tools\\gs_api_snapshot.py

Never run this with the stir env's python (gs_quant 1.4.26 there -- not the reference version).

This tool only *introspects* already-importable gs_quant classes/functions/enums via
`inspect.signature`, `dataclasses.fields` and enum iteration. It never constructs a GsSession and
never touches the network: no pricebt module or test imports gs_quant, so this snapshot is the only
place gs_quant is ever imported in this repo (DESIGN section 0.1).

Output 1: tests/data/gs_api_1_5_4.json
----------------------------------------
{
  "gs_quant_version": "1.5.4",
  "generated_date": "YYYY-MM-DD",
  "symbols": {
    "<qualified name>": <descriptor>,
    ...
  },
  "requested_not_found": ["<qualified name>", ...]   # e.g. 2.1.17-only risk measures absent from 1.5.4
}

A descriptor is one of:
  {"kind": "enum", "members": [[name, value_repr], ...]}
  {"kind": "risk_measure", "class": <RiskMeasure subclass name>, "name": ..., "measure_type": ...,
   "asset_class": str(obj.asset_class), "unit": str(obj.unit)}   # "None" when unset
  {"kind": "class" | "dataclass", "signature": [[name, param_kind, default_repr], ...],
   "fields": [[name, init, default_repr], ...],   # dataclass only
   "methods": [...], "properties": [...],
   "method_signatures": {method_name: [[name, param_kind, default_repr], ...], ...}}
  {"kind": "function", "signature": [[name, param_kind, default_repr], ...]}

`signature` lists constructor/function parameters in declaration order, `self`/`cls` dropped,
`default_repr` is `repr(default)` or the literal string "NODEFAULT" (no default at all -- a
required `inspect.Parameter` or a dataclass field with neither `default` nor `default_factory`)
or, for a dataclass field only, "FACTORY" (the field has a `default_factory`, whose callable has
no meaningful `repr()`). Note `signature` renders that same case differently: CPython's generated
`__init__` puts its own sentinel there, whose `repr()` is the unquoted text `<factory>` -- a
different spelling of the same "has a default_factory" fact, harmless but not the same string.

The in-scope symbols (DESIGN section 12.3, IMPLEMENTATION_PLAN.md P1.6):
  - every public class/function of gs_quant.backtests.{strategy, triggers, actions, data_sources,
    backtest_objects, backtest_utils, generic_engine, core} (name not starting with "_", and
    actually defined in that module rather than merely imported into it);
  - the 8 generated instrument classes (gs_quant.instrument.{IRSwap, IRSwaption, FXOption,
    FXForward, EqOption, InflationSwap, Cash, Bond});
  - every gs_quant.risk measure instance and preset of gs 2.1.17 (IR_RISK_DESIGN.md section 1;
    the 2.1.17-only ones are absent from 1.5.4 and land in "requested_not_found", not "symbols");
  - the gs_quant.risk functions aggregate_risk, aggregate_results, subtract_risk, sort_risk,
    combine_risk_key and classes PnlExplain, PnlExplainClose, PnlExplainLive, PnlPredictLive
    (IR_RISK_DESIGN.md R2-23);
  - the gs_quant.common enums;
  - gs_quant.markets.{PricingContext, HistoricalPricingContext} and
    gs_quant.markets.portfolio.Portfolio;
  - the gs_quant.datetime functions;
  - gs_quant.session.GsSession.use and gs_quant.session.Environment.

Output 2: tests/data/gs_instruments_1_5_4.json
-----------------------------------------------
{
  "gs_quant_version": "1.5.4",
  "generated_date": "YYYY-MM-DD",
  "dataclasses": {
    "<class name>": {
      "asset_class": <repr of the class-level default>,
      "type_": <repr of the class-level default>,
      "fields": [[name, init, default_repr, annotation_str], ...]   # ALL fields, declaration order
    },
    ...   # every dataclass in gs_quant.target.instrument (110 in 1.5.4), feeding gen_gs_fields.py
  }
}
"""
from __future__ import annotations

import dataclasses
import datetime
import enum
import inspect
import json
import sys
from pathlib import Path

import gs_quant

if gs_quant.__version__ != "1.5.4":
    print(f"gs_api_snapshot.py: expected gs_quant 1.5.4, found {gs_quant.__version__} "
          f"(wrong python -- see DESIGN.md section 3.3)", file=sys.stderr)
    raise SystemExit(1)

import gs_quant.backtests.strategy
import gs_quant.backtests.triggers
import gs_quant.backtests.actions
import gs_quant.backtests.data_sources
import gs_quant.backtests.backtest_objects
import gs_quant.backtests.backtest_utils
import gs_quant.backtests.generic_engine
import gs_quant.backtests.core
import gs_quant.instrument
import gs_quant.target.instrument
import gs_quant.risk
import gs_quant.common
import gs_quant.markets
import gs_quant.markets.portfolio
import gs_quant.datetime
from gs_quant.session import Environment, GsSession

ROOT = Path(__file__).resolve().parents[1]
OUT_API = ROOT / "tests" / "data" / "gs_api_1_5_4.json"
OUT_INSTRUMENTS = ROOT / "tests" / "data" / "gs_instruments_1_5_4.json"

BACKTEST_MODULE_NAMES = ["strategy", "triggers", "actions", "data_sources", "backtest_objects",
                          "backtest_utils", "generic_engine", "core"]
INSTRUMENT_CLASS_NAMES = ["IRSwap", "IRSwaption", "FXOption", "FXForward", "EqOption",
                           "InflationSwap", "Cash", "Bond"]
# Every measure instance and preset gs_quant.risk exposes in 2.1.17 (117 instances + 7 presets,
# IR_RISK_DESIGN.md section 1.1; the whole catalogue pricebt.risk ports), sorted. The six 2.1.17-only
# ones (EqForwardSpot, FXDeltaHedgeLocalCcy, FXDeltaLocalCcy, FXGammaLocalCcy, FXThetaLocalCcy,
# FXVegaLocalCcy) are absent from 1.5.4 and land in "requested_not_found".
RISK_MEASURE_NAMES = [
    "Annuity", "BaseCPI", "CDATMSpread", "CDDelta", "CDFwdSpread", "CDGamma", "CDIForward",
    "CDIIndexDelta", "CDIIndexVega", "CDIOptionPremium", "CDIOptionPremiumFlatFwd",
    "CDIOptionPremiumFlatVol", "CDISpot", "CDISpreadDV01", "CDIUpfrontPrice",
    "CDImpliedVolatility", "CDIndexVega", "CDTheta", "CDVega", "CRIFIRCurve", "Cashflows",
    "CommodDelta", "CommodImpliedVol", "CommodTheta", "CommodVega", "CompoundedFixedRate", "Cross",
    "CrossMultiplier", "Description", "DollarPrice", "EqAnnualImpliedVol", "EqDelta",
    "EqForwardSpot", "EqGamma", "EqSpot", "EqTheta", "EqVega", "ExpiryInYears",
    "FX25DeltaButterflyVolatility", "FX25DeltaRiskReversalVolatility", "FXAnnualATMImpliedVol",
    "FXAnnualImpliedVol", "FXBlackScholes", "FXBlackScholesPct", "FXCalcDelta",
    "FXCalcDeltaNoPremAdj", "FXDelta", "FXDeltaHedge", "FXDeltaHedgeLocalCcy", "FXDeltaLocalCcy",
    "FXDiscountFactorOver", "FXDiscountFactorUnder", "FXFwd", "FXGamma", "FXGammaLocalCcy",
    "FXImpliedCorrelation", "FXPoints", "FXPremium", "FXPremiumPct", "FXPremiumPctFlatFwd",
    "FXQuotedDelta", "FXQuotedDeltaNoPremAdj", "FXQuotedVega", "FXQuotedVegaBps", "FXSpot",
    "FXSpotVal", "FXStrikePts", "FXThetaLocalCcy", "FXVega", "FXVegaLocalCcy", "FairPremium",
    "FairPremiumInPercent", "FairPrice", "FairVarStrike", "FairVolStrike", "ForwardPrice",
    "IRAnnualATMImpliedVol", "IRAnnualImpliedVol", "IRBasis", "IRBasisParallel",
    "IRDailyImpliedVol", "IRDelta", "IRDeltaLocalCcy", "IRDeltaParallel",
    "IRDiscountDeltaParallel", "IRDiscountDeltaParallelLocalCcy", "IRFwdRate", "IRGamma",
    "IRGammaParallel", "IRGammaParallelLocalCcy", "IRSpotRate", "IRVanna", "IRVega",
    "IRVegaLocalCcy", "IRVegaParallel", "IRVolga", "IRXccyDelta", "IRXccyDeltaParallel",
    "InflDeltaParallelLocalCcyInBps", "InflMaturityCPI", "Infl_CompPeriod", "InflationDelta",
    "InflationDeltaParallel", "LightningDV01", "LightningOAS", "LocalAnnuityInCents", "Market",
    "MarketData", "MarketDataAssets", "NonUSDOisDomRate", "OisFXSprExSpkRate", "OisFXSprRate",
    "ParSpread", "PremiumCents", "PremiumSummary", "Price", "PricePips", "ProbabilityOfExercise",
    "RFRFXRate", "RFRFXSprExSpkRate", "RFRFXSprRate", "ResolvedInstrumentValues", "Theta",
    "USDOisDomRate",
]
# IR_RISK_DESIGN.md R2-23: the gs_quant.risk helper functions (gs_quant.risk.core, re-exported;
# pricebt.risk.core lands in Phase B, section 1.8) and the PnlExplain measure classes (Phase E,
# section 8), described like any other class/function.
RISK_FUNCTION_AND_CLASS_NAMES = [
    "aggregate_risk", "aggregate_results", "subtract_risk", "sort_risk", "combine_risk_key",
    "PnlExplain", "PnlExplainClose", "PnlExplainLive", "PnlPredictLive",
]


def _default_repr(default) -> str:
    if default is inspect.Signature.empty or default is dataclasses.MISSING:
        return "NODEFAULT"
    return repr(default)


def _field_default_repr(field: dataclasses.Field) -> str:
    """Like `_default_repr`, but for a dataclass field: a `default_factory` field has no usable
    `repr()` at all (it's a callable, e.g. `list`) and must be reported distinctly from a truly
    required field, which `_default_repr(field.default)` alone can't tell apart -- both hit
    `dataclasses.MISSING` on `.default`."""
    if field.default_factory is not dataclasses.MISSING:
        return "FACTORY"
    return _default_repr(field.default)


def _sig_list(obj, strip_self: bool) -> list:
    """`obj` is a class (signature = its constructor, self already excluded by inspect.signature)
    or a function/bound method (self/cls stripped by hand when `strip_self`)."""
    sig = inspect.signature(obj)
    params = list(sig.parameters.values())
    if strip_self and params and params[0].name in ("self", "cls"):
        params = params[1:]
    return [[p.name, str(p.kind), _default_repr(p.default)] for p in params]


def _public_methods_and_properties(cls) -> tuple:
    """Public method/property names, plus each method's own parameter signature (self/cls
    stripped) so a specific method (e.g. GenericEngine.run_backtest) can be checked exactly."""
    methods, properties, method_signatures = [], [], {}
    for name in dir(cls):
        if name.startswith("_"):
            continue
        try:
            member = inspect.getattr_static(cls, name)
        except AttributeError:
            continue
        if isinstance(member, property):
            properties.append(name)
        elif isinstance(member, (classmethod, staticmethod)):
            methods.append(name)
            method_signatures[name] = _sig_list(member.__func__, strip_self=isinstance(member, classmethod))
        elif inspect.isfunction(member):
            methods.append(name)
            method_signatures[name] = _sig_list(member, strip_self=True)
    return sorted(methods), sorted(properties), method_signatures


def _describe(obj) -> dict:
    if isinstance(obj, type):
        if issubclass(obj, enum.Enum):
            return {"kind": "enum", "members": [[m.name, _default_repr(m.value)] for m in obj]}
        entry = {"kind": "dataclass" if dataclasses.is_dataclass(obj) else "class",
                 "signature": _sig_list(obj, strip_self=False)}
        if dataclasses.is_dataclass(obj):
            entry["fields"] = [[f.name, f.init, _field_default_repr(f)]
                                for f in dataclasses.fields(obj)]
        entry["methods"], entry["properties"], entry["method_signatures"] = _public_methods_and_properties(obj)
        return entry
    if dataclasses.is_dataclass(obj):  # a module-level RiskMeasure instance
        cls = type(obj)
        return {"kind": "risk_measure", "class": cls.__name__,
                "name": getattr(obj, "name", None),
                "measure_type": str(getattr(obj, "measure_type", None)),
                "asset_class": str(getattr(obj, "asset_class", None)),
                "unit": str(getattr(obj, "unit", None))}
    return {"kind": "function", "signature": _sig_list(obj, strip_self=True)}


def _module_symbols(mod) -> dict:
    """Every public class/function actually defined in `mod` (not merely imported into it)."""
    out = {}
    for name, obj in inspect.getmembers(mod):
        if name.startswith("_"):
            continue
        if not (inspect.isclass(obj) or inspect.isfunction(obj)):
            continue
        if getattr(obj, "__module__", None) != mod.__name__:
            continue
        out[f"{mod.__name__}.{name}"] = _describe(obj)
    return out


def build_api_snapshot() -> dict:
    symbols = {}
    for modname in BACKTEST_MODULE_NAMES:
        symbols.update(_module_symbols(getattr(gs_quant.backtests, modname)))

    for name in INSTRUMENT_CLASS_NAMES:
        symbols[f"gs_quant.instrument.{name}"] = _describe(getattr(gs_quant.instrument, name))

    requested_not_found = []
    for name in RISK_MEASURE_NAMES:
        obj = getattr(gs_quant.risk, name, None)
        if obj is None:
            requested_not_found.append(f"gs_quant.risk.{name}")
        else:
            symbols[f"gs_quant.risk.{name}"] = _describe(obj)
    for name in RISK_FUNCTION_AND_CLASS_NAMES:
        symbols[f"gs_quant.risk.{name}"] = _describe(getattr(gs_quant.risk, name))

    for name, obj in inspect.getmembers(gs_quant.common):
        if name.startswith("_"):
            continue
        if isinstance(obj, type) and issubclass(obj, enum.Enum) and obj.__module__.startswith("gs_quant"):
            symbols[f"gs_quant.common.{name}"] = _describe(obj)

    for name in ("PricingContext", "HistoricalPricingContext"):
        symbols[f"gs_quant.markets.{name}"] = _describe(getattr(gs_quant.markets, name))
    symbols["gs_quant.markets.portfolio.Portfolio"] = _describe(gs_quant.markets.portfolio.Portfolio)

    for name, obj in inspect.getmembers(gs_quant.datetime):
        if name.startswith("_"):
            continue
        if inspect.isfunction(obj) and getattr(obj, "__module__", "").startswith("gs_quant.datetime"):
            symbols[f"gs_quant.datetime.{name}"] = _describe(obj)

    symbols["gs_quant.session.GsSession.use"] = _describe(GsSession.use)
    symbols["gs_quant.session.Environment"] = _describe(Environment)

    return {
        "gs_quant_version": gs_quant.__version__,
        "generated_date": datetime.date.today().isoformat(),
        "symbols": symbols,
        "requested_not_found": requested_not_found,
    }


def build_instrument_snapshot() -> dict:
    dataclasses_out = {}
    for name, cls in inspect.getmembers(gs_quant.target.instrument):
        if not (isinstance(cls, type) and dataclasses.is_dataclass(cls)):
            continue
        if cls.__module__ != gs_quant.target.instrument.__name__:
            continue
        fields = [[f.name, f.init, _field_default_repr(f), str(f.type)]
                  for f in dataclasses.fields(cls)]
        dataclasses_out[name] = {
            "asset_class": repr(getattr(cls, "asset_class", None)),
            "type_": repr(getattr(cls, "type_", None)),
            "fields": fields,
        }
    return {
        "gs_quant_version": gs_quant.__version__,
        "generated_date": datetime.date.today().isoformat(),
        "dataclasses": dataclasses_out,
    }


def main() -> None:
    api_doc = build_api_snapshot()
    instr_doc = build_instrument_snapshot()

    OUT_API.parent.mkdir(parents=True, exist_ok=True)
    OUT_API.write_text(json.dumps(api_doc, indent=2, sort_keys=True) + "\n", encoding="utf8")
    OUT_INSTRUMENTS.write_text(json.dumps(instr_doc, indent=2, sort_keys=True) + "\n", encoding="utf8")

    print(f"wrote {OUT_API} ({len(api_doc['symbols'])} symbols, "
          f"{len(api_doc['requested_not_found'])} requested-not-found)")
    print(f"wrote {OUT_INSTRUMENTS} ({len(instr_doc['dataclasses'])} dataclasses)")


if __name__ == "__main__":
    main()
