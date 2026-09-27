"""Running ONE base config under N stacks (spec X1) and comparing the runs. A stack is an overlay (or several) that may set only an instrument's `factory`, its `bind`
block and a pricer role's `wrap`; everything else is the shared base, and equal `base_hash` values prove it. Before any real result is trusted the harness
re-runs the reference stack and demands EXACTLY ZERO difference at every level (self-test X5a); the mutated-convention and known-answer self-tests (X5b, X5c) are
in `tests/test_tieout_selftest.py` and `tests/test_refstack.py`.

    result = run_tieout("base.yaml", {"rateslib": ["stacks/rateslib.yaml"], "quantlib": ["stacks/quantlib.yaml"]}, out="results/tieout")
    print(result.reports["rateslib_vs_quantlib"].frame())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .. import api
from ..config import loader
from ..config.loader import Built
from .compare import TieoutError, TieoutReport, compare
from .report import save
from .tolerances import Tolerances, declared_tolerances, merge_declarations

Source = Union[str, Path, Mapping[str, Any]]
DEFAULT_AUDIT_MEASURES: Tuple[str, ...] = ("dv01", "gamma", "rate")


@dataclass
class TieoutResult:
    reports: Dict[str, TieoutReport]
    results: Dict[str, Any]
    builts: Dict[str, Built]
    selftest: Optional[TieoutReport]
    header: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.reports.values())


def disclose_stack(built: Built) -> Dict[str, Any]:
    """What one stack OWNS (spec X1: `factory`, `bind`, `wrap`), in plain data: `factories` instrument -> factory path, `wraps` pricer role -> wrap path (None: none), `bindings` see
    `disclose_bindings`. The report prints it: none of it is in `base_digest`, so it is the only place a stack's own choices show."""
    pricers = (built.cfg.get("market") or {}).get("pricers") or {}
    return {"factories": {n: spec.factory_path for n, spec in sorted(built.instruments.items())},
            "wraps": {r: (d or {}).get("wrap") for r, d in sorted(pricers.items())}, "bindings": disclose_bindings(built)}


def market_roles(built: Built) -> Dict[str, List[str]]:
    """instrument -> the market roles it reads (a snapshot difference in another role cannot explain its pv difference)."""
    return {n: sorted(set(spec.roles().values())) for n, spec in built.instruments.items()}


def disclose_bindings(built: Built) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """What each stack bound, in plain data: `instrument -> schema name -> {target, args, kwargs (with their literals), scale, offset, sign}`. The overlay owns `bind`, and a binding can
    carry a unit conversion or a literal library argument; those are invisible to `base_digest`, so the report SHOWS them instead (spec X1: a difference must be attributable)."""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for iname, spec in sorted(built.instruments.items()):
        out[iname] = {n: {"target": f"{b.kind}:{b.target}", "args": list(b.args), "kwargs": dict(b.kwargs), "scale": b.scale, "offset": b.offset, "sign": b.sign, "reduce": b.reduce, "keys": list(b.keys)}
                      for n, b in sorted(spec.bindings.items())}
    return out


def _prepared(base: Source, sets: Sequence[str], audit_measures: Sequence[str], baseline: bool) -> Tuple[Dict[str, Any], Path]:
    cfg = api.load(base, sets=sets)
    bt = cfg.setdefault("backtest", {})
    bt["audit"] = {"measures": list(audit_measures)}
    if baseline:
        bt["attribution"] = {**dict(bt.get("attribution") or {}), "baseline": True}
    return cfg, (Path(base).resolve().parent if not isinstance(base, Mapping) else Path("."))


def run_stack(base: Source, overlays: Sequence[Source] = (), *, sets: Sequence[str] = (), audit_measures: Sequence[str] = DEFAULT_AUDIT_MEASURES, baseline: bool = True) -> Tuple[Built, Any]:
    """Build and run `base` under the given overlays with the audit trail on; returns (Built, BacktestResult)."""
    cfg, base_dir = _prepared(base, sets, audit_measures, baseline)
    built = loader.build(cfg, base_dir=base_dir, stack=[api.load(s) for s in overlays])
    return built, built.run()


def run_tieout(base: Source, stacks: Mapping[str, Sequence[Source]], *, sets: Sequence[str] = (), tolerances: Optional[Mapping[str, Any]] = None,
               audit_measures: Sequence[str] = DEFAULT_AUDIT_MEASURES, baseline: bool = True, reference: Optional[str] = None, selftest: bool = True,
               out: Optional[Union[str, Path]] = None, top: int = 10) -> TieoutResult:
    """Run `base` under every stack, prove the base is shared (X1), self-test the harness on the reference stack (X5a) and compare every other stack with the reference.

    `tolerances` overrides the placeholders of spec Appendix B and the base config's own `tieout.tolerances` (same shape; both are validated by `validate_declaration`): an explicit key wins at every
    specificity for what it names, i.e. it removes every config-declared key it covers (`merge_declarations`). `out` receives markdown, HTML and parquet per pair."""
    if len(stacks) < 2:
        raise TieoutError("a tie-out needs at least two stacks")
    declared = declared_tolerances(api.load(base, sets=sets))  # `tieout.tolerances` of the base config (X3), validated like the loader does; an explicit argument wins over what it covers
    ref = reference or next(iter(stacks))
    if ref not in stacks:
        raise TieoutError(f"reference {ref!r} is not one of the stacks {sorted(stacks)}")
    tol = Tolerances(merge_declarations(declared, dict(tolerances or {})))
    builts: Dict[str, Built] = {}
    results: Dict[str, Any] = {}
    for name, overlays in stacks.items():
        builts[name], results[name] = run_stack(base, overlays, sets=sets, audit_measures=audit_measures, baseline=baseline)
    hashes = {n: b.base_hash for n, b in builts.items()}
    if len(set(hashes.values())) != 1:
        raise TieoutError(f"the stacks do not share one base config (X1): base hashes {hashes}")
    st: Optional[TieoutReport] = None
    if selftest:
        _, again = run_stack(base, stacks[ref], sets=sets, audit_measures=audit_measures, baseline=baseline)
        st = compare(results[ref], again, names=(ref, f"{ref}_again"), tolerances=tol, top=top)
        bad = [r for r in st.rows if r.status not in ("exact", "info")]
        if bad:
            raise TieoutError("harness self-test failed: the same stack run twice differs at " + "; ".join(f"{r.level}.{r.quantity} ({r.status}, max abs {r.max_abs:.3g})" for r in bad[:5]))
    roles = market_roles(builts[ref])
    reports = {f"{ref}_vs_{n}": compare(results[ref], results[n], names=(ref, n), tolerances=tol, top=top, roles=roles) for n in stacks if n != ref}
    disclosed = {n: disclose_stack(b) for n, b in builts.items()}
    for key, rep in reports.items():
        pair = (rep.names[0], rep.names[1])
        rep.disclosure = {part: {n: disclosed[n][part] for n in pair} for part in ("factories", "wraps", "bindings")}
    header = {"reference": ref, "stacks": list(stacks), "base_hash": next(iter(hashes.values())), "config_hash": {n: b.config_hash for n, b in builts.items()},
              "selftest": "passed" if st is not None else "skipped", "audit_measures": list(audit_measures), "baseline": baseline,
              **{part: {n: d[part] for n, d in disclosed.items()} for part in ("bindings", "factories", "wraps")}}
    if out is not None:
        save(reports, out, header=header)
    return TieoutResult(reports, results, builts, st, header)
