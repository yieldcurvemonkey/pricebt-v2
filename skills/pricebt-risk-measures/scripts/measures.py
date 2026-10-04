"""Measure-contract helpers for skills/pricebt-risk-measures.

Three jobs, all without touching your pricing library:

  contract <IRSwap|IRSwaption|Bond>   the contract table (pricebt.risk.contracts): every measure and
                                      form an asset of that class must map (every class with a
                                      contract is strict: docs/v2/IR_STRICT_CONTRACT.md,
                                      docs/v2/BOND_DESIGN.md), its kind, allowed units and one-line
                                      semantics
  matrix <config.yaml>                audit a config against its class's contract, row by row:
                                      MAPPED (function, unit) / MISSING (with a hint where every
                                      library can supply it), plus the load-time problems and
                                      warnings; exit 1 if anything is MISSING or a problem. A
                                      declaration never satisfies a row: the row stays MISSING (its
                                      reason shown) and the declaration itself is a problem.
                                      --strict is accepted and changes nothing (every contract class
                                      is strict now).
  block <config.yaml>                 what to paste for what is MISSING: the mapping skeleton
                                      (contracts.mapping_skeleton: functions: / portfolio_functions: /
                                      risk_measures: stubs whose expressions you write -- they do not
                                      compile until you do)

    python skills/pricebt-risk-measures/scripts/measures.py contract IRSwaption
    python skills/pricebt-risk-measures/scripts/measures.py matrix tests/assets/toy_usd_irs.yaml

In-process:

    import sys; sys.path.insert(0, "skills/pricebt-risk-measures/scripts")
    import measures
    m = measures.capability_matrix("tests/assets/toy_usd_swaption.yaml")

Why not `load_asset`: a config with a contract gap fails `load_asset` with one ConfigError, and this
tool exists to diagnose exactly that config. It parses the YAML with pricebt's own loader and asks
`pricebt.risk.contracts` the same questions `load_asset` asks. The config's `imports:` and `code:`
are never executed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from pricebt.assets import yamlio
from pricebt.assets.config import EXTENSIVE_UNITS
from pricebt.risk import contracts

MAPPED, MISSING = "MAPPED", "MISSING"


def contract_rows(instrument: str) -> List[Dict[str, Any]]:
    """One dict per contract measure: measure, kind, forms, units (allowed), intensive, doc.
    Raises ValueError for a class without a contract."""
    reqs = contracts.contract_for(instrument)
    if not reqs:
        raise ValueError(f"no measure contract for {instrument!r}; classes with one: {sorted(contracts.CONTRACTS)} (any other class only needs Price)")
    rows = []
    for r in reqs:
        units, intensive = contracts.KINDS[r.kind]
        rows.append({"measure": r.measure, "kind": r.kind, "forms": list(r.forms), "units": sorted(units) if units else ["frame"], "intensive": bool(intensive), "doc": r.doc})
    return rows


def contract_markdown(instrument: str) -> str:
    rule = (f"every row must be MAPPED: a {'/'.join(sorted(contracts.STRICT_CLASSES))} config cannot declare a contract measure"
            " (docs/v2/IR_STRICT_CONTRACT.md, docs/v2/BOND_DESIGN.md)")
    lines = [
        f"contract: {instrument} ({len(contracts.contract_for(instrument))} measures; holder-signed, per unit trade, pricebt applies quantity)",
        f"rule: {rule}",
        "",
        "| measure | kind | forms | allowed units | intensive | semantics |",
        "|---|---|---|---|---|---|",
    ]
    for r in contract_rows(instrument):
        lines.append(f"| {r['measure']} | {r['kind']} | {', '.join(r['forms'])} | {', '.join(r['units'])} | {'yes' if r['intensive'] else '-'} | {r['doc'].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------ matrix


def _raw(config: Union[str, Path, Mapping[str, Any]]) -> Mapping[str, Any]:
    raw = config if isinstance(config, Mapping) else yamlio.load_file(Path(config))
    if not isinstance(raw, Mapping) or not raw.get("instrument"):
        raise ValueError(f"{config}: not an asset config (no instrument:)")
    return raw


def _function(raw: Mapping[str, Any], name: str) -> Tuple[Optional[str], Optional[contracts.MappedFunction]]:
    """(slot kind, summary) of a function name, as assets.config classifies it: a functions: entry
    is scalar; a portfolio_functions: entry is scalar or bucketed by its own returns. (None, None)
    if the name is neither."""
    for section, portfolio in (("functions", False), ("portfolio_functions", True)):
        spec = (raw.get(section) or {}).get(name)
        if isinstance(spec, Mapping):
            unit = spec.get("unit")
            swq = spec.get("scale_with_quantity")
            returns = spec.get("returns", "scalar")
            fn = contracts.MappedFunction(name, unit, unit in EXTENSIVE_UNITS if swq is None else bool(swq), returns, tuple(spec.get("scale_columns") or ()))
            return ("bucketed" if portfolio and returns == "buckets" else "scalar"), fn
    return None, None


def _mapped(raw: Mapping[str, Any]) -> Tuple[Dict[str, Dict[str, Optional[contracts.MappedFunction]]], List[str]]:
    """The `{key: {"scalar": MappedFunction | None, "bucketed": ...}}` summary load_asset builds,
    plus a problem per reference to a missing function or a slot of the wrong kind."""
    mapped, problems = {}, []
    for key, value in (raw.get("risk_measures") or {}).items():
        slots: Dict[str, Optional[contracts.MappedFunction]] = {"scalar": None, "bucketed": None}
        wanted = value.items() if isinstance(value, Mapping) else [(None, value)]
        for slot, fname in wanted:
            kind, fn = _function(raw, fname) if isinstance(fname, str) else (None, None)
            if slot not in (None, "scalar", "bucketed"):
                problems.append(f"risk_measures.{key}: unknown key {slot!r} (use scalar and/or bucketed)")
            elif fn is None:
                problems.append(f"risk_measures.{key}: references unknown function {fname!r}")
            elif slot not in (None, kind):
                problems.append(f"risk_measures.{key}.{slot}: {fname!r} is a {kind} function")
            else:
                slots[kind] = fn
        mapped[key] = slots
    return mapped, problems


def _declared(raw: Mapping[str, Any]) -> Dict[str, Dict[str, str]]:
    out = {}
    for measure, value in (raw.get("unsupported_measures") or {}).items():
        out[measure] = {"*": value} if not isinstance(value, Mapping) else dict(value)
    return out


# how any library computes the strict-contract additions (docs/v2/IR_STRICT_CONTRACT.md R3-1)
_SWAP_HINTS = {
    "ParSpread": "K - IRFwdRate in bp when both legs share one curve and schedule; else the floating-leg spread solving PV = 0",
    "FairPremium": "Price / DF(premium settlement date: spot, or the premium payment date); no spot lag: Price",
    "ForwardPrice": "Price / DF(the date ExpiryInYears counts to); Price on or after it",
    "PremiumCents": "Price / |notional_amount| x 1e4, unit bp, intensive",
    "LocalAnnuityInCents": "Annuity / |notional_amount|, unit decimal, intensive",
    "CompoundedFixedRate": "(1 + K/f)^f - 1 from the resolved fixed rate/strike (annual leg: K), intensive",
    "CRIFIRCurve": "the trade's IRDelta ladder (weights [1.0]) as CRIF rows: Risk_IRCurve, currency, Bucket '1', SIMM tenor, sub-curve, Amount",
    "PnlExplain": "a buckets portfolio function reading market_to: Price(market_to) - Price(market) on the pricing date, row mkt_type IR",
}


# the Bond texts (docs/v2/BOND_DESIGN.md section 3): what any bond library (price-from-yield, accrued,
# settlement calendar, a repo rate) gives; skills/pricebt-connect-pricing-library/references/
# config-template-bond.yaml has a recipe for each
_BOND_HINTS = {
    "FairPremium": "Price itself (a bond's Price is already the settlement-date value)",
    "ForwardPrice": "Price x (1 + RepoRate x tau(s, H)) - coupons paid in (s, H] x (1 + RepoRate x tau(c, H)), H = settlement + 1 month",
    "PremiumCents": "Price / |face| x 100, unit pct (the dirty price per 100, negative for a short), intensive",
    "LocalAnnuityInCents": "Annuity / |face|, unit decimal, intensive",
    "CompoundedFixedRate": "(1 + c/f)^f - 1 from the resolved coupon c and frequency f, intensive",
    "CRIFIRCurve": "the trade's IRDelta ladder (weights [1.0]) as CRIF rows: Risk_IRCurve, currency, Bucket '1', SIMM tenor, sub-curve, Amount",
    "PnlExplain": "a buckets portfolio function reading market_to: Price(market_to) - Price(market) on the pricing date, rows IR (curve) and CREDIT (spread)",
    "CleanPrice": "DirtyPrice - 100 x AccruedInterest / face (signed face), unit pct, intensive",
    "DirtyPrice": "100 x Price / face (signed face), unit pct, intensive",
    "AccruedInterest": "the coupon accrued to the standard settlement date (your library's accrued at settlement) x face / 100, holder-signed",
    "ModifiedDuration": "-1e4 x IRDelta / Price in years, or your library's modified duration at the yield IRFwdRate quotes",
    "Convexity": "1e8 x IRGammaParallel / Price in years^2, or your library's convexity at that yield",
    "DaysToSettlement": "(settlement date of the pricing date - pricing date).days, unit number, scale_with_quantity: false",
    "RepoRate": "the overnight GC or special fixing of the pricing date (or the term rate pinned in resolve), in a rate unit",
    "RepoHaircut": "a resolve kwarg or a data lookup: the fraction of the settlement value not financed, decimal",
    "FinancingToDate": "-(1 - haircut) x Price(trade date, pinned in resolve) x sum of daily repo fixings / 360 from settle(trade date) to settle(t)",
    "Carry": "(Price - AccruedInterest) - (ForwardPrice - the accrued at H)",
    "RollDown": "the clean value at H on the curve rolled down (time to maturity held, spread held) minus the clean value now",
}


def _hint(instrument: str, measure: str, provided: Mapping[Tuple[str, str], str]) -> str:
    """A nudge for a measure that is missing but that every library can supply."""
    if measure in contracts.ZERO_BY_CONVENTION.get(instrument, {}):
        return f"0.0 by convention ({contracts.ZERO_BY_CONVENTION[instrument][measure]}): map a '0.0' function"
    hints = _BOND_HINTS if instrument == "Bond" else _SWAP_HINTS
    if measure in hints:
        return hints[measure]
    if measure == "ExpiryInYears":
        return "pure date arithmetic on the resolved terms: max(final_or_expiry - t, 0).days / 365"
    if measure == "IRDailyImpliedVol" and ("IRAnnualImpliedVol", "scalar") in provided:
        return "the mapped IRAnnualImpliedVol / sqrt(252)"
    if measure in ("IRBasis", "IRXccyDelta"):
        return "0.0 when your model is single-curve / single-currency (contract text)"
    if measure == "Annuity" and instrument == "IRSwap":
        return "any library that prices a swap at a given fixed rate: -[PV(K+1bp) - PV(K-1bp)] / 2e-4 (payer > 0)"
    if measure == "IRSpotRate" and instrument == "IRSwap":
        return "any library with a par rate: the par rate of a spot-starting probe swap to the same final date"
    return ""


def capability_matrix(config: Union[str, Path, Mapping[str, Any]]) -> Dict[str, Any]:
    """Audit one asset config (a path or an in-memory mapping) against its class's contract.

    Returns {asset, instrument, rows, problems, warnings, outside, missing}: `rows` has one dict per
    contract (measure, form) with status MAPPED / MISSING and its function, unit, `via` (the
    risk_measures key when it is a preset, e.g. IRDeltaParallel), reason and hint (a declared row
    stays MISSING, its reason shown, and the declaration is one of the `problems`); `problems` would
    stop load_asset (wrong unit, non-intensive level, unknown function, a declared contract measure,
    ...); `warnings` are load warnings; `outside` lists risk_measures keys the contract does not
    restrict (custom names); `missing` feeds contracts.mapping_skeleton. A class without a contract
    gives no rows."""
    raw = _raw(config)
    instrument = raw["instrument"]
    mapped, problems = _mapped(raw)
    declared = _declared(raw)
    check = contracts.check(instrument, mapped, declared)
    provided = contracts.provided_forms(instrument, mapped)
    missing_measures = {m for m, _f in check.missing}
    problems += [p for p in check.problems if not (p.split(" (", 1)[0] in missing_measures and "require a mapping for every contract measure" in p)]
    rows = []
    for req in contracts.contract_for(instrument):
        units, _intensive = contracts.KINDS[req.kind]
        for form in req.forms:
            row = {"measure": req.measure, "form": form, "kind": req.kind, "units": sorted(units) if units else ["frame"], "status": MISSING, "function": None, "unit": None, "via": None, "reason": None, "hint": ""}
            key = provided.get((req.measure, form))
            reason = declared.get(req.measure, {}).get(form) or declared.get(req.measure, {}).get("*")
            if key is not None:
                fn = mapped[key]["bucketed" if form == "bucketed" else "scalar"]
                row.update(status=MAPPED, function=fn.function, unit=fn.unit, via=None if key == req.measure else key)
            elif reason is not None:
                row.update(reason=f"declared, which cannot satisfy an {instrument} row: {reason}")   # stays MISSING
            if row["status"] != MAPPED:
                row["hint"] = _hint(instrument, req.measure, provided)
            rows.append(row)
    contract_names = {r.measure for r in contracts.contract_for(instrument)}
    outside = sorted(k for k in mapped if contracts.base_measure(k)[0] not in contract_names)
    return {"asset": raw.get("asset"), "instrument": instrument, "rows": rows, "problems": problems, "warnings": list(check.warnings), "outside": outside, "missing": list(check.missing)}


def matrix_ok(matrix: Mapping[str, Any], strict: bool = False) -> bool:
    """True iff nothing is MISSING and there is no problem. `strict` is kept for callers and changes
    nothing: every class with a contract is strict, so a declaration never satisfies a row."""
    return not matrix["problems"] and all(r["status"] == MAPPED for r in matrix["rows"])


def matrix_markdown(matrix: Mapping[str, Any]) -> str:
    lines = [f"matrix: asset {matrix['asset']} ({matrix['instrument']})"]
    if not matrix["rows"]:
        lines.append(f"{matrix['instrument']} has no measure contract: only Price is required.")
    else:
        lines += ["", "| measure | form | kind | allowed units | status | detail | hint |", "|---|---|---|---|---|---|---|"]
        for r in matrix["rows"]:
            if r["status"] == MAPPED:
                detail = f"{r['function']} ({r['unit']})" + (f" via {r['via']}" if r["via"] else "")
            else:
                detail = (r["reason"] or "").replace("|", "/")
            lines.append(f"| {r['measure']} | {r['form']} | {r['kind']} | {', '.join(r['units'])} | {r['status']} | {detail} | {r['hint']} |")
        counts = {s: sum(r["status"] == s for r in matrix["rows"]) for s in (MAPPED, MISSING)}
        lines += ["", " ".join(f"{k}={v}" for k, v in counts.items()) + f" PROBLEMS={len(matrix['problems'])}"]
    for title, items in (("problems (load_asset would fail)", matrix["problems"]), ("warnings (load_asset warns)", matrix["warnings"])):
        if items:
            lines += [f"{title}:"] + [f"  - {p}" for p in items]
    if matrix["outside"]:
        lines.append(f"outside the contract (loaded as-is, no unit checks): {', '.join(matrix['outside'])}")
    if any(r["status"] == MISSING for r in matrix["rows"]):
        lines.append(f"next: map each MISSING row (no declarations for {'/'.join(sorted(contracts.STRICT_CLASSES))}): `measures.py block <config>` prints the paste-ready mapping skeleton")
    return "\n".join(lines) + "\n"


def missing_block(config: Union[str, Path, Mapping[str, Any]]) -> str:
    """What to paste for every MISSING (measure, form), or "" if nothing is missing:
    `contracts.mapping_skeleton` (stubs to merge into functions: / portfolio_functions: /
    risk_measures:, each expression contracts.SKELETON_EXPR, which does not compile until written)."""
    matrix = capability_matrix(config)
    return contracts.mapping_skeleton(matrix["instrument"], matrix["missing"]) if matrix["missing"] else ""


# ------------------------------------------------------------------------------------ CLI


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="pricebt measure contracts: print, audit a config, or draft its declarations.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("contract", help="print the contract of an instrument class").add_argument("instrument")
    mx = sub.add_parser("matrix", help="audit an asset config against its contract (exit 1 on MISSING/problems)")
    mx.add_argument("config")
    mx.add_argument("--strict", action="store_true", help="accepted for old scripts; changes nothing (every contract class is strict)")
    b = sub.add_parser("block", help="print the mapping skeleton for what the config is missing")
    b.add_argument("config")
    b.add_argument("--reason", default=None, help="ignored (a declaration cannot satisfy a contract row); accepted for old scripts")
    args = ap.parse_args(argv)

    if args.cmd == "contract":
        try:
            print(contract_markdown(args.instrument), end="")
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0
    if args.cmd == "matrix":
        m = capability_matrix(args.config)
        print(matrix_markdown(m), end="")
        return 0 if matrix_ok(m, strict=args.strict) else 1
    block = missing_block(args.config)
    if not block:
        print("# nothing missing: every contract measure and form is mapped")
        return 0
    print("note: merge each section into the config's own functions: / portfolio_functions: / risk_measures: (a second key is a duplicate-key error),"
          " then write every expr (the stubs do not compile) and pick the unit", file=sys.stderr)
    print(block, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
