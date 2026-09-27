"""`pricebt run|validate|describe cfg.yaml [--set a.b=c ...]` and `pricebt tieout cfg.yaml --stack NAME=overlay.yaml ...` (python -m pricebt)."""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

from ..errors import ConfigError


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pricebt", description="Config-driven backtester")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("run", "build and run a backtest"), ("validate", "load and build everything, run nothing"), ("describe", "print the resolved object graph summary")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("config")
        s.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE", help="override a config value (dotted path, YAML literal)")
        s.add_argument("--stack", dest="stacks", action="append", default=[], metavar="OVERLAY.yaml", help="a stack overlay: sets only instruments.<n>.factory/.bind and market.pricers.<r>.wrap")
        if name == "run":
            s.add_argument("--out", default=None, help="output directory (parquet + manifest [+ tearsheet])")
            s.add_argument("--tearsheet", action="store_true")
            s.add_argument("--no-progress", action="store_true")
            s.add_argument("--dry-run", action="store_true", help="build the graph and fetch the first pricer only")
    t = sub.add_parser("tieout", help="run ONE base config under several stacks and compare them level by level (spec X1-X6)")
    t.add_argument("config")
    t.add_argument("--stack", dest="stacks", action="append", required=True, metavar="NAME=OVERLAY.yaml[,OVERLAY.yaml]", help="a named stack (an overlay of factory/bind/wrap); repeat for each stack")
    t.add_argument("--reference", default=None, help="the stack the others are compared with (default: the first)")
    t.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE", help="override a base config value for every stack")
    t.add_argument("--out", default=None, help="directory for the markdown/HTML report and the parquet detail")
    t.add_argument("--no-selftest", action="store_true", help="skip the harness self-test (X5a); a result without it is not to be trusted")
    t.add_argument("--top", type=int, default=10, help="offenders listed per row")
    return p


def _named_stacks(specs: Sequence[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for s in specs:
        name, sep, files = s.partition("=")
        if not sep or not name.strip() or not files.strip():
            raise ConfigError(f"--stack {s!r}: expected NAME=OVERLAY.yaml[,OVERLAY.yaml]", code="CLI")
        name = name.strip()
        if name in out:
            raise ConfigError(f"--stack {name!r} is given twice", code="CLI")
        out[name] = [f.strip() for f in files.split(",") if f.strip()]
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .. import api

    args = _parser().parse_args(argv)
    sets: List[str] = list(args.sets)
    if getattr(args, "no_progress", False):
        sets.append("backtest.progress.show=false")
    try:
        if args.cmd == "tieout":
            from ..tieout import run_tieout, stack_summary, to_markdown

            res = run_tieout(args.config, _named_stacks(args.stacks), sets=args.sets, reference=args.reference, selftest=not args.no_selftest, out=args.out, top=args.top)
            for name, rep in res.reports.items():
                print(to_markdown(rep, title=name))
            for line in stack_summary(res.header):  # what each stack owns (factory, wrap, unit-converting bindings): the full tables are in the markdown above
                print(line)
            print(f"harness self-test: {res.header['selftest']}")
            print("TIE-OUT " + ("PASSED" if res.passed else "FAILED: " + "; ".join(f"{n}: {r.level}.{r.quantity}" for n, rep in res.reports.items() for r in rep.failures())))
            return 0 if res.passed else 1
        if args.cmd == "validate":
            api.build(args.config, sets=sets, stack=args.stacks)
            print("OK")
            return 0
        if args.cmd == "describe":
            b = api.build(args.config, sets=sets, stack=args.stacks)
            print(f"config_hash: {b.config_hash}\nbase_hash: {b.base_hash}\ngrid: {len(b.grid)} points {b.grid.start} .. {b.grid.end}\nroles: {sorted(b.market.bindings)}\n"
                  f"instruments: {sorted(b.instruments)}\nsignals: {sorted(b.signals or {})}\ntriggers: {len(b.strategy.triggers)}\nsettings: {b.settings}")
            return 0
        if args.dry_run:
            b = api.build(args.config, sets=sets, stack=args.stacks)
            b.market.clock.advance(b.grid.start)
            p = b.market.pricer(b.grid.start)
            print(f"OK: first pricer {type(p).__name__} at {p.ts} (reference_date {p.reference_date})")
            return 0
        res = api.run(args.config, sets=sets, out=args.out, tearsheet=args.tearsheet, stack=args.stacks)
        print(res.summary_stats().to_string())
        return 0
    except Exception as e:  # any failure is the documented error status 2, never the interpreter's 1 (which is "TIE-OUT FAILED")
        print(f"error: {e}" if isinstance(e, ConfigError) else f"error: [{getattr(e, 'code', None) or type(e).__name__}] {e}", file=sys.stderr)  # a ConfigError's text already starts with its code
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
