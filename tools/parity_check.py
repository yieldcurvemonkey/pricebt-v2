"""Parity check between two pricebt set-ups of the same instrument (e.g. an asset config before and
after its market moved into a market provider): every function of the asset config, on several
dates, must give the same value. Used by docs/v2/MARKET_PROVIDER_REFACTOR_PROMPT.md.

Copy this file next to your own configs, edit the three blocks marked EDIT, and run it from the
repository root (PYTHONPATH=src;tests). Unedited it compares the toy swap config with itself.
Exit code 0 = identical. An error counts as a value: both set-ups must fail the same way, and the
script names what the OLD one fails on. tests/test_parity_check_tool.py runs it on known answers.
"""
from __future__ import annotations

import math
import sys
from datetime import date

import pandas as pd

from pricebt import risk as risk_module
from pricebt.instrument import IRSwap
from pricebt.session import PricebtSession

# EDIT 1: the two set-ups (keyword arguments of PricebtSession)
OLD = dict(assets=["tests/assets/toy_usd_irs.yaml"])
NEW = dict(assets=["tests/assets/toy_usd_irs.yaml"])

# EDIT 2: business dates with market data; consecutive pairs are also used as (from, to) for PnlExplain
DATES = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 6, 3)]

# EDIT 3: the instruments to compare, as (label, factory for the OLD set-up, factory for the NEW one)
CASES = [
    ("payer 10y ATM", lambda: IRSwap("Pay", "10y", "USD", 1e6), lambda: IRSwap("Pay", "10y", "USD", 1e6)),
    ("receiver 5y off-market", lambda: IRSwap("Receive", "5y", "USD", 5e6, fixed_rate=0.03), lambda: IRSwap("Receive", "5y", "USD", 5e6, fixed_rate=0.03)),
]

REL_TOL = 1e-12
IGNORED_RESOLVED_KEYS: set = set()  # resolved terms the NEW set-up adds on purpose (e.g. {"curve"})


def _plain(v):
    """A value as plain comparable data."""
    if isinstance(v, pd.DataFrame):
        return [{k: _plain(x) for k, x in row.items()} for row in pd.DataFrame(v).to_dict("records")]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return v


def _call(fn):
    try:
        return _plain(fn())
    except Exception as exc:  # an error is a value too: both set-ups must fail the same way
        return f"ERROR {type(exc).__name__}"


def _names_target(asset, name) -> bool:
    def names(code):
        out = set(code.co_names)
        for const in code.co_consts:
            if hasattr(const, "co_names"):
                out |= names(const)
        return out

    return bool({"market_to", "pricebt_to_date"} & names(asset.code(name)))


def snapshot(session_kwargs, make, d, d_to):
    """{function or measure name: value} for one instrument resolved and priced on `d`."""
    with PricebtSession(**session_kwargs) as session:
        service = session.pricing
        resolved = service.resolve(make(), d, None)  # resolved on d, then priced on d and on d_to
        asset = service.asset_for(resolved)
        out = {"resolved": {k: _plain(v) for k, v in resolved.resolved_terms.items() if k not in IGNORED_RESOLVED_KEYS}}
        for on, tag in ((d, ""), (d_to, "@next")):
            for name, spec in asset.functions.items():
                if spec.returns != "frame":
                    out[f"functions.{name}{tag}"] = _call(lambda: service.unit_value(resolved, on, name, None))
            for name in asset.portfolio_functions:
                params = (("to_date", d_to),) if _names_target(asset, name) else ()
                if params and on == d_to:
                    continue  # a relative function explains from d to d_to only
                out[f"portfolio_functions.{name}{tag}"] = _call(lambda: service.portfolio_value(asset, on, name, [resolved], None, params=params))
            for measure, mapping in asset.risk_measures.items():
                spec = asset.functions.get(mapping.scalar) if mapping.scalar else None
                if spec is not None and spec.returns == "frame":
                    out[f"frame.{measure}{tag}"] = _call(lambda: service.value(resolved, on, getattr(risk_module, measure), None))
        return out


def differences(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'OLD' if k in a else 'NEW'}")
            else:
                out += differences(a[k], b[k], f"{path}/{k}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [x for i, (u, v) in enumerate(zip(a, b)) for x in differences(u, v, f"{path}[{i}]")]
    if isinstance(a, float) and isinstance(b, float):
        same = (math.isnan(a) and math.isnan(b)) or math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-9)
        return [] if same else [f"{path}: OLD {a!r} != NEW {b!r}"]
    return [] if a == b else [f"{path}: OLD {a!r} != NEW {b!r}"]


def main() -> int:
    diffs, compared = [], 0
    for label, make_old, make_new in CASES:
        for d, d_to in zip(DATES, DATES[1:]):
            old, new = snapshot(OLD, make_old, d, d_to), snapshot(NEW, make_new, d, d_to)
            compared += len(old)
            diffs += [f"[{label} {d}] {x}" for x in differences(old, new)]
            errors = sorted(k for k, v in old.items() if isinstance(v, str) and v.startswith("ERROR"))
            if errors:
                print(f"note [{label} {d}]: OLD raises for {errors} (compared as errors)")
    print(f"{compared} values compared, {len(diffs)} difference(s)")
    for line in diffs[:40]:
        print("  " + line)
    return 1 if diffs or not compared else 0


if __name__ == "__main__":
    sys.exit(main())
