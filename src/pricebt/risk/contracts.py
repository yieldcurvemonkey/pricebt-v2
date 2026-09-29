"""Per-instrument measure contracts (docs/v2/IR_RISK_DESIGN.md section 2; DEV-I11).

gs_quant answers every IR measure on every IR instrument (a swap's vega is 0) and silently returns
`UnsupportedValue` for what it cannot compute. pricebt has no server behind it, so an asset config
whose `instrument:` has a contract here must, for every measure and form of that contract, either
map it to a function with an allowed unit or declare it under `unsupported_measures:` with a
reason. `assets.config` calls `check` at load time; the pricing layer calls `validate_frame` on
frame results.

This module holds data and pure functions only. It never imports `pricebt.assets` or
`pricebt.instrument`: `assets.config` hands it a plain summary of the mappings (`MappedFunction`).
Measures are referred to by NAME; `base_measure` looks the objects up in `pricebt.risk` at call
time, so a name the catalogue does not (yet) export simply has no fallback.
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, NamedTuple, Optional, Tuple

import pandas as pd

import pricebt.risk as _risk  # the parent package: importing this module has already imported it
from pricebt.common import AggregationLevel
from pricebt.errors import ConfigError

FORMS = ("scalar", "bucketed", "frame")


@dataclass(frozen=True)
class MeasureRequirement:
    """One row of a contract: a gs measure name, its kind (a key of `KINDS`), the forms an asset
    must provide or declare, and the one-line contract semantics shown in errors and docs."""

    measure: str
    kind: str
    forms: Tuple[str, ...]
    doc: str


# kind -> (allowed units, must_be_intensive). R2-11: True = the function must be intensive
# (`scale_with_quantity: false`); False = no constraint either way. `table` kinds are frames: the
# unit is not checked, the columns are (FRAME_COLUMNS, FRAME_SCALE_COLUMNS).
KINDS: Dict[str, Tuple[Optional[frozenset], Optional[bool]]] = {
    "value": (frozenset({"ccy"}), False),
    "sens1": (frozenset({"ccy_per_bp"}), False),
    "sens2": (frozenset({"ccy_per_bp2"}), False),
    "theta": (frozenset({"ccy"}), False),
    "annuity": (frozenset({"ccy"}), False),
    "rate": (frozenset({"bp", "pct", "decimal"}), True),
    "vol": (frozenset({"bp", "pct", "decimal"}), True),
    "time": (frozenset({"decimal", "number"}), True),
    "prob": (frozenset({"decimal", "number"}), True),
    "table": (None, None),
}

FRAME_COLUMNS: Dict[str, Tuple[str, ...]] = {"Cashflows": ("payment_date", "payment_amount", "currency", "payment_type")}
FRAME_SCALE_COLUMNS: Dict[str, Tuple[str, ...]] = {"Cashflows": ("payment_amount",)}  # must scale with quantity


def _req(measure: str, kind: str, forms: str, doc: str) -> MeasureRequirement:
    return MeasureRequirement(measure, kind, tuple(forms.split()), doc)


# IR_RISK_DESIGN section 2.2 as amended by section 00 (R2-1..R2-8). Holder-signed, per unit trade
# (pricebt applies quantity). "s" = scalar form, "b" = bucketed form.
# pricebt DEV-I12 (own-rate IRDelta/IRGammaParallel/IRFwdRate), DEV-I15 (Theta per day, total
# return, own rate/vol fixed), DEV-I17 (ExpiryInYears for swaps/bonds): the semantics below.
_IR_BASE = (
    _req("Price", "value", "scalar",
         "PV in the function currency, holder-signed; it either drops each flow on its payment date (Cashflows then lists the flows still to drop) or never drops paid flows (total return: Cashflows is empty)."),
    _req("IRDelta", "sens1", "scalar bucketed",
         "s: TOTAL derivative of Price w.r.t. the own rate r (IRFwdRate) along the library's parallel curve shift, own-strike vol fixed: [PV(+h)-PV(-h)]/[r(+h)-r(-h)] per bp of r (a fixed-annuity pv01 is exact only at the money); "
         "b: curve ladder, ccy per +1bp at each pillar, labels.mkt_type IR. Pay-fixed swap > 0, payer swaption > 0, long bond < 0. IRDeltaParallel/IRDeltaLocalCcy resolve here (DEV-I12)."),
    _req("IRDiscountDeltaParallel", "sens1", "scalar",
         "PV change for a +1bp parallel shift of the discount curve only; not in general equal to the IRDelta scalar. IRDiscountDeltaParallelLocalCcy falls back here."),
    _req("IRGammaParallel", "sens2", "scalar",
         "chain-rule second derivative of Price w.r.t. the own rate r on the IRDelta bumps, per bp^2 of r: "
         "[n+ + n- - 2n0 - ((n+ - n-)/(r+ - r-))(r+ + r- - 2r0)] / ((r+ - r-)/2)^2; never d(pv01)/dr (half the gamma at the money). IRGammaParallelLocalCcy falls back here (DEV-I16)."),
    _req("IRGamma", "sens2", "bucketed",
         "diagonal gamma ladder, ccy per bp^2 at each pillar, a 6-column bucketed frame (DEV-I13: gs returns a 12-column cross-gamma frame)."),
    _req("IRVega", "sens1", "scalar bucketed",
         "s: PV change for +1bp of normal implied vol (IRAnnualImpliedVol); b: vol cube, mkt_point '<tail>;<expiry>' (e.g. '5Y;1Y'), labels.mkt_type IR VOL. "
         "Swaps/bonds: 0.0 / empty by convention (R2-8). IRVegaParallel/IRVegaLocalCcy resolve here."),
    _req("IRVanna", "sens2", "scalar",
         "d(IRDelta scalar)/d(sigma) per bp x bp (rate bp x normal-vol bp); swaps/bonds 0.0. Request as IRVanna(aggregation_level='Type') (FD measure, DEV-I9)."),
    _req("IRVolga", "sens2", "scalar",
         "second derivative of Price w.r.t. normal vol, per bp^2 of vol; swaps/bonds 0.0. Request as IRVolga(aggregation_level='Type') (DEV-I9)."),
    _req("IRBasis", "sens1", "scalar",
         "PV change for +1bp of the basis (projection-vs-discount) spread; single-curve libraries: 0.0. IRBasisParallel resolves here; or request IRBasis(aggregation_level='Type')."),
    _req("IRXccyDelta", "sens1", "scalar",
         "cross-currency basis delta; single-currency instruments: 0.0. IRXccyDeltaParallel resolves here; or request IRXccyDelta(aggregation_level='Type')."),
    _req("IRFwdRate", "rate", "scalar",
         "the own quoted rate: swap par rate, swaption forward rate of the underlying, bond yield to maturity (DEV-I12). Intensive; finite on every held date including the exit date (R2-7)."),
    _req("IRSpotRate", "rate", "scalar",
         "the par rate of the spot-starting equivalent (swap / swaption underlying with the same final date); bond: its yield. Intensive."),
    _req("IRAnnualImpliedVol", "vol", "scalar",
         "annualised NORMAL implied vol at the instrument's strike; swaps/bonds: 0.0 by convention (R2-8). Intensive."),
    _req("IRAnnualATMImpliedVol", "vol", "scalar",
         "ATM-forward normal vol for the same expiry and tail; swaps/bonds: 0.0 (R2-8). Intensive."),
    _req("IRDailyImpliedVol", "vol", "scalar",
         "IRAnnualImpliedVol / sqrt(252); swaps/bonds: 0.0 (R2-8). Intensive."),
    _req("Theta", "theta", "scalar",
         "one calendar day of carry holding the own IRFwdRate and IRAnnualImpliedVol fixed: Price(t+1d) + cashflows Price drops in (t, t+1d] - Price(t), ccy PER DAY "
         "(DEV-I15; curve translated DF(x)/DF(t+1d), never rolled). A per-year IRTheta = 365 x Theta: never map Theta to a per-year function."),
    _req("ExpiryInYears", "time", "scalar",
         "max(final_or_expiry - t, 0).days / 365 (calendar days, ACT/365F): a swaption's expiry, a swap's or bond's final date (DEV-I17). Intensive."),
    _req("Annuity", "annuity", "scalar",
         "PV of the fixed leg paying 1.0 per annum (1e4 x the fixed-leg pv01), ccy; bond: PV of 1.0 per annum on its schedule."),
    _req("Cashflows", "table", "frame",
         "the flows still included in Price that Price will drop on their payment date (payment_date > pricing date), holder-signed, one row each; empty for a total-return Price (R2-6). "
         "Required columns payment_date, payment_amount, currency, payment_type; returns: frame with scale_columns including payment_amount."),
)

CONTRACTS: Dict[str, Tuple[MeasureRequirement, ...]] = {
    "IRSwap": _IR_BASE,
    "IRSwaption": _IR_BASE + (
        _req("ProbabilityOfExercise", "prob", "scalar", "probability (0..1) of finishing in the money under the annuity measure. Intensive."),
    ),
    "Bond": _IR_BASE + (
        _req("LightningDV01", "sens1", "scalar", "yield DV01: Price change for +1bp of yield (= the IRDelta scalar for a bond)."),
        _req("LightningOAS", "rate", "scalar", "option-adjusted spread over the library's reference curve (a bullet bond: its Z-spread). Intensive."),
        _req("ParSpread", "rate", "scalar", "par asset-swap spread (or the library's par spread) in the declared unit. Intensive."),
    ),
}


class MappedFunction(NamedTuple):
    """What `check` needs to know about the function in one `risk_measures:` slot (built by
    `assets.config`, so this module never imports `pricebt.assets`)."""

    function: str
    unit: str
    scale_with_quantity: bool
    returns: str  # "scalar" | "frame" (functions:) or "scalar" | "buckets" (portfolio_functions:)
    scale_columns: Tuple[str, ...] = ()


class ContractCheck(NamedTuple):
    """`check`'s result: `problems` make the config invalid; `warnings` are stale declarations
    (R2-9: the mapping wins) and declarations the contract cannot count; `missing` are the
    `(measure, form)` pairs neither mapped nor declared, in contract order (feed them to
    `unsupported_block`)."""

    problems: List[str]
    warnings: List[str]
    missing: List[Tuple[str, str]]


def contract_for(instrument: str) -> Tuple[MeasureRequirement, ...]:
    """The contract of an instrument class name; `()` for classes without one (FXOption, EqOption,
    InflationSwap, Cash, FXForward, ConfigInstrument, ...), which keep the plain "Price is
    required" rule."""
    return CONTRACTS.get(instrument, ())


def base_measure(key: str) -> Tuple[str, Optional[str]]:
    """`(base, form)` for a `risk_measures:` key (R2-10). A preset or fallback name in
    `pricebt.risk` (one with a `base_name`, e.g. IRDeltaParallel) counts toward its base measure:
    `form` is "scalar" when the preset always asks for the scalar form (a finite-difference
    measure with aggregation_level other than Point), else None (each mapping slot counts as its
    own form, e.g. IRDeltaLocalCcy, IRGammaParallelLocalCcy). Any other key maps to itself."""
    obj = getattr(_risk, key, None)
    base = getattr(obj, "base_name", None) if isinstance(obj, _risk.RiskMeasure) else None
    if not base:
        return key, None
    agg = getattr(obj.parameters, "aggregation_level", None)
    return base, ("scalar" if agg is not None and agg != AggregationLevel.Point else None)


def _slot_problems(req: MeasureRequirement, key: str, form: str, fn: MappedFunction) -> List[str]:
    where = f"{req.measure} ({form}{'' if key == req.measure else f', via {key}'})"
    units, intensive = KINDS[req.kind]
    if req.kind == "table":
        if fn.returns != "frame":
            return [f"{where}: function {fn.function!r} returns {fn.returns!r}; this measure is a table: map a functions: entry with `returns: frame`"]
        out = []
        missing_cols = [c for c in FRAME_SCALE_COLUMNS.get(req.measure, ()) if c not in fn.scale_columns]
        if missing_cols:
            out.append(f"{where}: function {fn.function!r} scale_columns {list(fn.scale_columns)} must include {missing_cols}")
        if FRAME_SCALE_COLUMNS.get(req.measure) and not fn.scale_with_quantity:
            out.append(f"{where}: function {fn.function!r} has scale_with_quantity false; its amounts must scale with the position")
        return out
    if fn.returns == "frame":
        return [f"{where}: function {fn.function!r} returns a frame; this measure needs a number"]
    out = []
    if fn.unit not in units:
        out.append(f"{where}: function {fn.function!r} has unit {fn.unit!r}; allowed {sorted(units)}")
    if intensive and fn.scale_with_quantity:
        out.append(f"{where}: function {fn.function!r} scales with quantity; a {req.kind} level must be intensive (set scale_with_quantity: false)")
    return out


def _mapped_slots(reqs: Mapping[str, MeasureRequirement], mapped: Mapping[str, Mapping[str, Optional[MappedFunction]]]):
    """Every mapped slot as `(key, base measure, requirement or None, form, function, counts)`;
    `counts` is False for a preset's bucketed slot, which never serves the base's bucketed form."""
    for key, slots in mapped.items():
        base, preset_form = base_measure(key)
        req = reqs.get(base)
        for slot in ("scalar", "bucketed"):
            fn = slots.get(slot)
            if fn is None:
                continue
            if slot == "bucketed":
                form = "bucketed"
            elif req is not None:
                # the scalar slot is an attempt at the contract's own shape (a frame for a table),
                # so a shape mismatch is one problem, not also a "missing" one
                form = "frame" if req.kind == "table" else "scalar"
            else:
                form = "frame" if fn.returns == "frame" else "scalar"
            yield key, base, req, form, fn, not (slot == "bucketed" and preset_form == "scalar")


def provided_forms(instrument: str, mapped: Mapping[str, Mapping[str, Optional[MappedFunction]]]) -> Dict[Tuple[str, str], str]:
    """`{(base measure, form): the risk_measures key whose slot provides it}` (the first such key in
    mapping order). What `check` counts toward a contract row, and what the pricing layer serves a
    request by when neither the measure's name nor its `base_name` is a key (R2-10: e.g. an
    `IRDeltaParallel` key serves `IRDelta(aggregation_level='Type')`, an `IRGammaParallelLocalCcy`
    key serves `IRGammaParallel`), so a contract row that loads is a request that prices."""
    out: Dict[Tuple[str, str], str] = {}
    for key, base, _req, form, _fn, counts in _mapped_slots({r.measure: r for r in contract_for(instrument)}, mapped):
        if counts:
            out.setdefault((base, form), key)
    return out


def check(instrument: str, mapped: Mapping[str, Mapping[str, Optional[MappedFunction]]], unsupported: Mapping[str, Mapping[str, str]]) -> ContractCheck:
    """Check an asset's mappings against its instrument's contract (IR_RISK_DESIGN section 2.1).

    `mapped` is `{risk_measures key: {"scalar": MappedFunction | None, "bucketed": ... | None}}`;
    `unsupported` is `{measure: {form or "*": reason}}` (`AssetConfig.unsupported_measures`; a bare
    reason string, the YAML shape, also means "*"). A form is satisfied iff a mapping slot
    provides it (`provided_forms`: preset keys count toward their base, `base_measure`) or it is
    declared. A frame is a scalar-slot function with `returns: frame`. Every mapped slot of a
    contract measure is also checked for unit, intensivity and shape; measure names outside the
    contract are unrestricted. Declarations that cannot mean what they say (a preset name, a form
    outside the contract row, an unknown name) are warnings.
    """
    reqs = {r.measure: r for r in contract_for(instrument)}
    unsupported = {m: ({"*": v} if isinstance(v, str) else v) for m, v in unsupported.items()}
    problems: List[str] = []
    for key, _base, req, form, fn, _counts in _mapped_slots(reqs, mapped):
        if req is not None:
            problems += _slot_problems(req, key, form, fn)
    provided = provided_forms(instrument, mapped)

    missing: List[Tuple[str, str]] = []
    for req in reqs.values():
        declared = unsupported.get(req.measure, {})
        gaps = [f for f in req.forms if (req.measure, f) not in provided and "*" not in declared and f not in declared]
        if gaps:
            missing += [(req.measure, f) for f in gaps]
            problems.append(f"{req.measure} ({', '.join(gaps)}): neither mapped nor declared under unsupported_measures -- {req.doc}")

    # pricebt DEV-I11 (R2-9): a mapping wins over a stale declaration, with a warning
    warnings: List[str] = []
    for measure, declared in unsupported.items():
        for form in declared:
            hits = sorted(f"{f} via {k}" if k != measure else f for (m, f), k in provided.items() if m == measure and form in ("*", f))
            if hits:
                shown = "every form" if form == "*" else form
                warnings.append(f"{measure} is declared unsupported ({shown}) but mapped ({', '.join(hits)}); the mapping is used -- remove or narrow the stale declaration")

    # declarations the contract cannot count: a form outside the row, a preset key, an unknown name
    if reqs:
        for measure, declared in unsupported.items():
            req, base = reqs.get(measure), base_measure(measure)[0]
            if req is not None:
                outside = sorted(f for f in declared if f != "*" and f not in req.forms)
                if outside:
                    warnings.append(f"{measure} declares {outside} unsupported but its {instrument} contract row has only {list(req.forms)}; remove the extra form(s)")
            elif base != measure:
                warnings.append(f"{measure} is a preset or fallback of {base}; declare {base} instead (the contract counts only {base}, and a request for {measure} falls back to {base}'s mapping or declaration)")
            elif not isinstance(getattr(_risk, measure, None), _risk.RiskMeasure):
                close = difflib.get_close_matches(measure, list(reqs), n=1)
                hint = f" (did you mean {close[0]!r}?)" if close else ""
                warnings.append(f"{measure} is neither in the {instrument} contract nor a pricebt.risk measure{hint}; if misspelt it declares nothing (it only affects a request named exactly {measure})")
    return ContractCheck(problems, warnings, missing)


def unsupported_block(instrument: str, missing: Iterable[Tuple[str, str]], reason: str = "TODO: why your library cannot compute this") -> str:
    """Paste-ready YAML declaring every `(measure, form)` in `missing` unsupported: a measure whose
    every contract form is missing gets one reason, otherwise a `{form: reason}` mapping. Measures
    follow the contract's order. Replace each reason with an honest, specific one (the checker
    skill flags reasons that still start with TODO, R2-12)."""
    by_measure: Dict[str, List[str]] = {}
    for measure, form in missing:
        by_measure.setdefault(measure, []).append(form)
    order = [r.measure for r in contract_for(instrument)]
    forms_of = {r.measure: r.forms for r in contract_for(instrument)}
    quoted = json.dumps(reason)  # a JSON string is a valid double-quoted YAML scalar
    lines = ["unsupported_measures:"]
    for measure in sorted(by_measure, key=lambda m: (order.index(m) if m in order else len(order), m)):
        forms = by_measure[measure]
        if measure in forms_of and set(forms) == set(forms_of[measure]):
            lines.append(f"  {measure}: {quoted}")
        else:
            lines.append(f"  {measure}: {{{', '.join(f'{f}: {quoted}' for f in forms)}}}")
    return "\n".join(lines) + "\n"


def validate_frame(measure_name: str, frame: pd.DataFrame) -> None:
    """Raise `ConfigError` unless `frame` is a DataFrame with every required column of
    `measure_name` (FRAME_COLUMNS), even when it has no rows: consumers index those columns. A
    measure with no required columns only has to be a DataFrame."""
    if not isinstance(frame, pd.DataFrame):
        raise ConfigError(f"{measure_name}: a frame measure must produce a DataFrame, got {type(frame).__name__}")
    required = FRAME_COLUMNS.get(measure_name, ())
    absent = [c for c in required if c not in frame.columns]
    if absent:
        raise ConfigError(f"{measure_name}: frame is missing required column(s) {absent}; required {list(required)} (return pd.DataFrame(columns=[...]) when there are no rows)")
