"""Human and machine output of a tie-out (spec X4): a markdown or HTML report and parquet tables, per level: the worst absolute and relative difference of each
quantity, WHERE it occurs, the tolerance, the verdict and the attribution of a failure (input, expected definition difference, structure, exceedance), then the
largest offenders."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Union


from .compare import Row, TieoutReport

_LEVEL_TITLES = {
    "L0": "L0 inputs: snapshot digests, resolved terms, conventions",
    "L1": "L1 marks: pv, cash, financing per position per point",
    "L2": "L2 measures: dv01, gamma, rate ... per position per point",
    "L3": "L3 layers: by schema name, invariants, baseline decomposition",
    "L4": "L4 portfolio: equity, trade ledger, summary statistics",
}


def _fmt(x: float) -> str:
    return "0" if x == 0 else f"{x:.3e}"


def _verdict(r: Row) -> str:
    return {"exact": "exact", "noise": "within tolerance", "expected": "expected definition difference", "info": "info",
            "exceeds": "EXCEEDS", "input": "explained by inputs", "structure": "STRUCTURE"}.get(r.status, r.status)


def _not_identity(bindings: Mapping[str, Any]) -> List[str]:
    """`instrument.name (scale 2.0)` for every binding that converts the library's number (scale, offset or sign is not the identity)."""
    out = []
    for inst, names in bindings.items():
        for name, b in names.items():
            flags = ", ".join(f"{k} {b[k]}" for k, ident in (("scale", 1.0), ("offset", 0.0), ("sign", 1.0)) if b.get(k, ident) != ident)
            if flags:
                out.append(f"{inst}.{name} ({flags})")
    return out


def _literals(b: Mapping[str, Any]) -> str:
    """The literal library arguments a binding passes (everything in `kwargs` that is not an `@` reference) and the ladder `keys` it declares, shortened."""
    lit = {k: v for k, v in dict(b.get("kwargs") or {}).items() if not (isinstance(v, str) and v.startswith("@"))}
    text = ", ".join([*(f"{k}={json.dumps(v, default=str)}" for k, v in lit.items()), *([f"keys={json.dumps(b['keys'])}"] if b.get("keys") else [])])
    return (text if len(text) <= 100 else text[:97] + "...").replace("|", "/")


def _stacks_section(d: Mapping[str, Any]) -> List[str]:
    """Markdown of what each stack of the pair owns: factory, wrap and every binding, with the unit conversions flagged (none of it is in the base hash)."""
    stacks = list(d["factories"])
    flagged = {s: _not_identity(d["bindings"][s]) for s in stacks}
    out = ["## Stacks: what each stack owns (factory, wrap, bindings)", "",
           "Bindings that are not the identity (a unit conversion the stack applies to the library's number): " + ("; ".join(f"{s}: {', '.join(v)}" for s, v in flagged.items() if v) or "none"), "",
           "| stack | instrument | factory |", "|---|---|---|"]
    out += [f"| {s} | {i} | {f} |" for s in stacks for i, f in d["factories"][s].items()]
    out += ["", "| stack | pricer role | wrap |", "|---|---|---|"]
    out += [f"| {s} | {r} | {w or '(none)'} |" for s in stacks for r, w in d["wraps"][s].items()]
    out += ["", "| stack | instrument | schema name | target | reduce | scale | offset | sign | literal kwargs | flag |", "|---|---|---|---|---|---:|---:|---:|---|---|"]
    for s in stacks:
        for inst, names in d["bindings"][s].items():
            for n, b in names.items():
                flag = ", ".join(f"{k} {b[k]}" for k, ident in (("scale", 1.0), ("offset", 0.0), ("sign", 1.0)) if b.get(k, ident) != ident)
                out.append(f"| {s} | {inst} | {n} | {b['target']} | {b.get('reduce') or ''} | {b['scale']:g} | {b['offset']:g} | {b['sign']:g} | {_literals(b)} | {'NOT IDENTITY: ' + flag if flag else ''} |")
    return out + [""]


def stack_summary(header: Mapping[str, Any]) -> List[str]:
    """One compact line per stack (for the command line): its factories, its wraps and its bindings that are not the identity."""
    out = []
    for s, factories in header.get("factories", {}).items():
        wraps = header.get("wraps", {}).get(s, {})
        flagged = _not_identity(header.get("bindings", {}).get(s, {}))
        out.append(f"stack {s}: factory " + ", ".join(f"{i}={f}" for i, f in factories.items()) + "; wrap " + (", ".join(f"{r}={w or 'none'}" for r, w in wraps.items()) or "none")
                   + "; bindings not identity: " + ("; ".join(flagged) or "none"))
    return out


def to_markdown(rep: TieoutReport, *, title: str = "") -> str:
    a, b = rep.names
    out: List[str] = [f"# Tie-out: {title or f'{a} vs {b}'}", "", f"**Verdict: {'PASS' if rep.passed else 'FAIL'}** ({len(rep.rows)} quantities, {len(rep.failures())} failing)  ",
                      f"reference `{a}`, other `{b}`; {rep.header.get('n_points')} points, {rep.header.get('n_positions')} positions", ""]
    for level in ("L0", "L1", "L2", "L3", "L4"):
        rows = rep.level_rows(level)
        if not rows:
            continue
        out += [f"## {_LEVEL_TITLES[level]}", "", "| quantity | asset class | n | max abs | max rel | tolerance (rel / floor) | verdict | worst at | note |", "|---|---|---:|---:|---:|---|---|---|---|"]
        for r in rows:
            out.append(f"| {r.quantity} | {r.asset_class} | {r.n} | {_fmt(r.max_abs)} | {_fmt(r.max_rel)} | {_fmt(r.tol_rel)} / {_fmt(r.tol_floor)} | {_verdict(r)} | {r.where} | {r.note} |")
        out.append("")
    if rep.stack_stats:
        out += ["## Unexplained share of the library layers (reported, per stack and asset class)", "", "| stack | asset class | unexplained share | explained P&L (abs sum) |", "|---|---|---:|---:|"]
        out += [f"| {s['stack']} | {s['asset_class']} | {s['unexplained_share']:.3e} | {s['pnl_explained_abs']:,.2f} |" for s in rep.stack_stats]
        out.append("")
    if rep.offenders:
        out += ["## Largest offenders (by relative difference)", "", "| level | quantity | asset class | position | ts | a | b | abs diff | rel diff |", "|---|---|---|---|---|---:|---:|---:|---:|"]
        out += [f"| {o.level} | {o.quantity} | {o.asset_class} | {o.position} | {o.ts} | {o.a:.10g} | {o.b:.10g} | {_fmt(o.diff)} | {_fmt(o.rel)} |" for o in rep.offenders[:20]]
        out.append("")
    if rep.disclosure:
        out += _stacks_section(rep.disclosure)
    out += ["## Tolerances used", "", "| key | rel | floor | floor unit | expected | reason |", "|---|---:|---:|---|---|---|"]
    out += [f"| {k} | {_fmt(t['rel'])} | {_fmt(t['floor'])} | {t.get('unit', '')} | {t['expected']} | {str(t.get('reason', '')).replace('|', '/')} |" for k, t in rep.tolerances.items()]
    return "\n".join(out) + "\n"


def to_html(rep: TieoutReport, *, title: str = "") -> str:
    def cell(x: Any) -> str:
        return html.escape(str(x))

    css = "body{font:14px system-ui,sans-serif;margin:24px}table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #ccc;padding:3px 8px}th{background:#f3f3f3}.bad{background:#fdd}.ok{background:#efe}"
    body = [f"<h1>Tie-out: {cell(title or ' vs '.join(rep.names))}</h1>", f"<p><b>Verdict: {'PASS' if rep.passed else 'FAIL'}</b></p>"]
    for level in ("L0", "L1", "L2", "L3", "L4"):
        rows = rep.level_rows(level)
        if not rows:
            continue
        body.append(f"<h2>{cell(_LEVEL_TITLES[level])}</h2><table><tr><th>quantity</th><th>asset class</th><th>n</th><th>max abs</th><th>max rel</th><th>tol</th><th>verdict</th><th>worst at</th><th>note</th></tr>")
        for r in rows:
            body.append(f"<tr class={'ok' if r.passed else 'bad'}><td>{cell(r.quantity)}</td><td>{cell(r.asset_class)}</td><td>{r.n}</td><td>{_fmt(r.max_abs)}</td><td>{_fmt(r.max_rel)}</td>"
                        f"<td>{_fmt(r.tol_rel)}</td><td>{cell(_verdict(r))}</td><td>{cell(r.where)}</td><td>{cell(r.note)}</td></tr>")
        body.append("</table>")
    return f"<!doctype html><meta charset='utf-8'><title>Tie-out</title><style>{css}</style>" + "".join(body)


def save(reports: Mapping[str, TieoutReport], directory: Union[str, Path], *, header: Mapping[str, Any] = ()) -> Path:
    """Write `<name>.summary.parquet`, `<name>.offenders.parquet`, `<name>.md`, `<name>.html` per report and `tieout.json` (verdicts and header) into `directory`."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    verdicts: Dict[str, Any] = {}
    for name, rep in reports.items():
        rep.frame().to_parquet(d / f"{name}.summary.parquet", index=False)
        rep.offenders_frame().astype({"ts": str}).to_parquet(d / f"{name}.offenders.parquet", index=False)
        (d / f"{name}.md").write_text(to_markdown(rep, title=name), encoding="utf8")
        (d / f"{name}.html").write_text(to_html(rep, title=name), encoding="utf8")
        verdicts[name] = {"passed": rep.passed, "failures": [f"{r.level}.{r.quantity}[{r.asset_class}] {r.status}" for r in rep.failures()], "header": rep.header, "disclosure": rep.disclosure}
    (d / "tieout.json").write_text(json.dumps({"header": dict(header), "reports": verdicts}, indent=2, default=str), encoding="utf8")
    return d
