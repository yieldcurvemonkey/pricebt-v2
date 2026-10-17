"""Scenario grids and transform checks for a pricebt asset config (docs/v2/SCENARIOS.md).

In-process:

    import sys; sys.path.insert(0, "skills/pricebt-scenarios/scripts")
    import scenario_grid as sg
    sg.payoff_grid(straddle, range(-100, 101, 25), roll_to=expiry)       # terminal payoffs, one row per shock
    sg.grid_2d(book, rates_bp=[-50, 0, 50], vols_bp=[-10, 0, 10])         # rate x vol stress of the book total
    sg.roll_ladder(swap, ['0b', '22b', '44b'], realise_fwd=False)        # carry: value rolled forward in time
    sg.check_transforms(swaption)                                         # known-answer checks of the config's transforms

CLI (repository root, PYTHONPATH=src;tests):

    python skills/pricebt-scenarios/scripts/scenario_grid.py check --asset tests/assets/toy_usd_swaption.yaml \
        --instrument "IRSwaption('Straddle', '10y', 'USD', expiration_date='1y')" --date 2024-03-04
    python skills/pricebt-scenarios/scripts/scenario_grid.py payoff --asset tests/assets/toy_usd_swaption.yaml \
        --instrument "IRSwaption('Straddle', '10y', 'USD', expiration_date='1y')" --date 2024-03-04 --roll-to expiration_date --shocks=-100:100:25

Everything goes through pricebt's public API (instruments, `PricingContext`, the scenario classes);
the shocks use gs's units: rates and normal vols in decimals inside `MarketDataShock`, the grids'
arguments in bp.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Iterable, List, Optional, Sequence

import pandas as pd

from pricebt.markets import PricingContext
from pricebt.risk import (
    CompositeScenario,
    IRDelta,
    IRFwdRate,
    IRVegaParallel,
    MarketDataPattern,
    MarketDataShock,
    MarketDataShockBasedScenario,
    MarketDataShockType,
    MultiScenario,
    Price,
    RollFwd,
)
from pricebt.risk.results import PortfolioRiskResult, PricingFuture


def rate_shock(bp: float, asset: Optional[str] = None, name: Optional[str] = None) -> MarketDataShockBasedScenario:
    """An absolute `'IR'` shock of `bp` basis points (gs units: `bp / 1e4`)."""
    return MarketDataShockBasedScenario({MarketDataPattern("IR", asset): MarketDataShock(MarketDataShockType.Absolute, bp / 1e4)}, name=name or f"{bp:+g}bp")


def vol_shock(bp: float, asset: Optional[str] = None, name: Optional[str] = None) -> MarketDataShockBasedScenario:
    """An absolute `'IR Vol'` shock of `bp` basis points of normal vol."""
    return MarketDataShockBasedScenario({MarketDataPattern("IR Vol", asset): MarketDataShock(MarketDataShockType.Absolute, bp / 1e4)}, name=name or f"vol {bp:+g}bp")


def _members_frame(result, members, labels) -> pd.DataFrame:
    """A per-member result (instrument or portfolio) as rows = `labels`, columns = leaves + total."""
    if isinstance(result, PortfolioRiskResult):
        leaves = result.to_frame()  # rows: instruments, columns: members
        total = result.aggregate()
        out = leaves.T.copy()
        out.index = labels
        out["total"] = [float(total[m]) for m in members]
        return out
    return pd.DataFrame({"total": [float(result[m]) for m in members]}, index=labels)


def payoff_grid(priceable, shocks_bp: Iterable[float], roll_to=None, realise_fwd: bool = True, asset: Optional[str] = None, measure=Price) -> pd.DataFrame:
    """`measure` of `priceable` under each rate shock, after `RollFwd(roll_to)` when given (the
    terminal payoff when `roll_to` is the expiry). Resolve `priceable` first, or each member
    re-strikes on its own shocked curve. Rows: the shock in bp; columns: each leaf of a Portfolio
    and `total`."""
    shocks = list(shocks_bp)
    members = [rate_shock(bp, asset) for bp in shocks]
    outer = [RollFwd(date=roll_to, realise_fwd=realise_fwd)] if roll_to is not None else []
    with CompositeScenario(outer) if outer else _Null(), MultiScenario(members):
        result = priceable.calc(measure)
    return _members_frame(_value(result), members, pd.Index(shocks, name="shock_bp"))


def grid_2d(priceable, rates_bp: Sequence[float], vols_bp: Sequence[float], roll_to=None, asset: Optional[str] = None, measure=Price) -> pd.DataFrame:
    """The total `measure` under every (rate, vol) shock pair: one MultiScenario of composites
    (docs/v2/SCENARIOS.md section 3). Rows: rate shock bp; columns: vol shock bp."""
    pairs = [(r, v) for r in rates_bp for v in vols_bp]
    members = [CompositeScenario([rate_shock(r, asset), vol_shock(v, asset)], name=f"{r:+g}bp / vol {v:+g}bp") for r, v in pairs]
    outer = [RollFwd(date=roll_to)] if roll_to is not None else []
    with CompositeScenario(outer) if outer else _Null(), MultiScenario(members):
        result = _value(priceable.calc(measure))
    totals = result.aggregate() if isinstance(result, PortfolioRiskResult) else result
    out = pd.DataFrame(index=pd.Index(list(rates_bp), name="rate_bp"), columns=pd.Index(list(vols_bp), name="vol_bp"), dtype=float)
    for (r, v), m in zip(pairs, members):
        out.loc[r, v] = float(totals[m])
    return out


def roll_ladder(priceable, rolls: Iterable, realise_fwd: bool = True, measure=Price) -> pd.DataFrame:
    """`measure` of `priceable` rolled forward by each of `rolls` (dates or relative rules such as
    '22b', '3m'): the gs RollFwd carry notebooks (01_rollfwd_shock), as one MultiScenario. Rows:
    the roll; columns: each leaf of a Portfolio and `total`."""
    rolls = list(rolls)
    members = [RollFwd(date=r, realise_fwd=realise_fwd, name=f"roll {r}") for r in rolls]
    with MultiScenario(members):
        result = _value(priceable.calc(measure))
    return _members_frame(result, members, pd.Index(rolls, name="roll"))


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _value(x):
    """A value, from a future when pricing ran inside an entered PricingContext."""
    return x.result() if isinstance(x, PricingFuture) else x


def _maps(inst, measure) -> bool:
    from pricebt.session import PricebtSession

    try:
        return PricebtSession.current.pricing.maps(inst, measure)
    except Exception:  # noqa: BLE001 - an unmatched instrument just skips the check
        return False


def check_transforms(inst, asset: Optional[str] = None, rel: float = 1e-6) -> List[dict]:
    """Known-answer checks of an asset config's scenario transforms on `inst` (resolved first):
    each a dict `{check, ok, detail}`; a check the config cannot run (no mapping) is `ok=None`.
    The same identities tests/test_scenarios.py proves on the toy library."""
    out: List[dict] = []

    def record(check, fn):
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 - a failing transform is a failed check
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        out.append({"check": check, "ok": ok, "detail": detail})

    if inst.resolved_terms is None:
        inst.resolve()
    base = float(_value(inst.calc(Price)))

    def zero_shock():
        with rate_shock(0.0, asset):
            v = float(_value(inst.calc(Price)))
        return abs(v - base) <= rel * max(1.0, abs(base)), f"base {base:.6g}, zero shock {v:.6g}"

    def roll_to_today():
        with RollFwd(date=PricingContext.current.pricing_date):
            v = float(_value(inst.calc(Price)))
        return abs(v - base) <= rel * max(1.0, abs(base)), f"base {base:.6g}, roll to the pricing date {v:.6g}"

    def other_currency():
        other = "EUR" if (asset or "USD") != "EUR" else "USD"
        with rate_shock(50.0, other):
            v = float(_value(inst.calc(Price)))
        return abs(v - base) <= rel * max(1.0, abs(base)), f"an {other} shock moved the price to {v:.6g}" if abs(v - base) > rel * max(1.0, abs(base)) else "unchanged"

    def moves():
        with rate_shock(10.0, asset):
            v = float(_value(inst.calc(Price)))
        return abs(v - base) > rel * max(1.0, abs(base)), f"+10bp: {v - base:+.6g}"

    def delta_equivalent():
        if not (_maps(inst, IRFwdRate) and _maps(inst, IRDelta)):
            return None, "IRFwdRate/IRDelta not mapped"
        with rate_shock(1.0, asset):
            up, f_up = float(_value(inst.calc(Price))), float(_value(inst.calc(IRFwdRate)))
        with rate_shock(-1.0, asset):
            dn, f_dn = float(_value(inst.calc(Price))), float(_value(inst.calc(IRFwdRate)))
        delta = float(_value(inst.calc(IRDelta(aggregation_level="Type"))))
        by_shocks = (up - dn) / (f_up - f_dn)
        return abs(by_shocks - delta) <= 1e-3 * max(1.0, abs(delta)), f"shocks {by_shocks:.6g} per bp of the own rate, IRDelta {delta:.6g}"

    def vega_equivalent():
        if not _maps(inst, IRVegaParallel):
            return None, "IRVegaParallel not mapped"
        vega = float(_value(inst.calc(IRVegaParallel)))
        if vega == 0.0:
            return None, "zero vega (no vol exposure)"
        with vol_shock(1.0, asset):
            up = float(_value(inst.calc(Price)))
        with vol_shock(-1.0, asset):
            dn = float(_value(inst.calc(Price)))
        by_shocks = (up - dn) / 2
        return abs(by_shocks - vega) <= 1e-3 * max(1.0, abs(vega)), f"shocks {by_shocks:.6g}, IRVegaParallel {vega:.6g}"

    def composite_is_nested():
        a, b = rate_shock(10.0, asset), vol_shock(5.0, asset)
        with a, b:
            nested = float(_value(inst.calc(Price)))
        with CompositeScenario([a, b]):
            explicit = float(_value(inst.calc(Price)))
        return nested == explicit, f"nested {nested:.6g}, composite {explicit:.6g}"

    record("a zero rate shock equals the base price", zero_shock)
    record("a +10bp rate shock moves the price", moves)
    record("a roll to the pricing date changes nothing", roll_to_today)
    record("another currency's shock changes nothing", other_currency)
    record("(P(+1bp) - P(-1bp)) / dF equals IRDelta (gs 010204)", delta_equivalent)
    record("(P(+1bp vol) - P(-1bp vol)) / 2 equals IRVegaParallel (gs 010205)", vega_equivalent)
    record("with a, b: equals CompositeScenario([a, b])", composite_is_nested)
    return out


def _shocks(spec: str) -> List[float]:
    lo, hi, step = (float(x) for x in spec.split(":"))
    n = int(round((hi - lo) / step))
    return [lo + i * step for i in range(n + 1)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    from pricebt import instrument as instruments
    from pricebt.session import PricebtSession

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=("check", "payoff"))
    p.add_argument("--asset", action="append", required=True, help="asset config path (repeatable)")
    p.add_argument("--instrument", required=True, help="a pricebt.instrument expression, e.g. \"IRSwaption('Straddle', '10y', 'USD', expiration_date='1y')\"")
    p.add_argument("--date", required=True, type=date.fromisoformat)
    p.add_argument("--roll-to", help="payoff: a date, a relative rule ('1y'), or the name of a resolved term (e.g. expiration_date)")
    p.add_argument("--shocks", default="-100:100:25", help="payoff: lo:hi:step in bp (write --shocks=-100:100:25, a leading minus needs the =)")
    args = p.parse_args(argv)

    PricebtSession.use(assets=args.asset)
    PricingContext.current = PricingContext(args.date)
    inst = eval(args.instrument, {n: getattr(instruments, n) for n in instruments.__all__})  # noqa: S307 - a local CLI argument
    inst.resolve()
    if args.command == "check":
        rows = check_transforms(inst)
        print(pd.DataFrame(rows).to_string(index=False))
        return 0 if all(r["ok"] is not False for r in rows) else 1
    roll_to = args.roll_to
    if roll_to in (inst.resolved_terms or {}):
        roll_to = inst.resolved_terms[roll_to]
    print(payoff_grid(inst, _shocks(args.shocks), roll_to=roll_to).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
