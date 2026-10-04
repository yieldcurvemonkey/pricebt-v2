"""P&L attribution for a pricebt BackTest: pick the IR PnlDefinition that matches the loaded asset
configs (units read from their IRFwdRate / IRAnnualImpliedVol mappings), refuse a book that cannot
be attributed, and summarise and grade BackTest.pnl_explain_table() (totals, r2, residual shares,
worst date, the attribute a residual signature names).

In-process use (repository root as cwd, PYTHONPATH=src;tests):

    import sys; sys.path.insert(0, "skills/pricebt-pnl-attribution/scripts")
    import attribution
    definition = attribution.definition_for(session)            # or a list of configs / paths
    bt = GenericEngine().run_backtest(strategy, ..., pnl_explain=definition)
    table, cumulative = attribution.attribution_frames(bt)
    stats = attribution.explain_stats(table); print(attribution.grade(stats), attribution.grade_reason(stats))

CLI:
    python skills/pricebt-pnl-attribution/scripts/attribution.py --demo swaption      (or bond)
    python skills/pricebt-pnl-attribution/scripts/attribution.py --definition CONFIG [CONFIG ...] [--kind auto]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
# pnl_explain_table's fixed columns; financing_pnl is a financed position's repo interest (pricebt
# DEV-E22: the engine books it as holding cash), explained by definition (known cash, not a market move)
FIXED = ["actual_pnl", "cashflow_pnl", "financing_pnl", "economic_pnl", "explained_pnl", "residual_pnl"]
# the graded "unexplained" share = the worst of var(residual)/var(economic), 1 - r2 and
# |sum residual|/sum|economic| (a steady bias, e.g. a sign-flipped Theta, passes the variance share
# alone): <= WARN passes, <= FAIL warns. The names keep "RESIDUAL_SHARE" for the callers.
RESIDUAL_SHARE_WARN = 0.05
RESIDUAL_SHARE_FAIL = 0.25
# a residual signature: |corr(residual, attribute)| >= this and an implied scale _reading names
SIGNATURE_CORR = 0.9
# kind -> ir_pnl_definition flags (keyword flags given to definition_for override them)
_KIND_FLAGS = {
    "swaption": {},  # all six: delta, gamma, vega, vanna, volga, theta
    "bond": {"vega": False, "vanna": False, "volga": False},  # also the swap set (no vol)
    "ir": {},
}


# ------------------------------------------------------------------------------------ configs


def _configs(source=None, assets: Optional[Iterable[str]] = None) -> list:
    """AssetConfigs from a PricebtSession (default: the current one), an AssetRegistry, one
    AssetConfig, or a list of configs / YAML paths / dicts. `assets` keeps only those names (a
    session may hold configs the book never trades)."""
    from pricebt.assets.config import AssetConfig, load_asset

    if source is None:
        from pricebt.session import PricebtSession

        source = PricebtSession.current
        if source is None:
            raise ValueError("no PricebtSession: pass a session, a registry, configs or config paths")
    registry = getattr(source, "registry", source)
    if isinstance(registry, AssetConfig):
        configs = [registry]
    elif hasattr(registry, "names") and hasattr(registry, "__getitem__"):
        configs = [registry[n] for n in registry.names()]
    else:
        items = registry if isinstance(registry, (list, tuple)) else [registry]
        configs = [c if isinstance(c, AssetConfig) else load_asset(c) for c in items]
    if assets is not None:
        wanted = set(assets)
        missing = sorted(wanted - {c.name for c in configs})
        if missing:
            raise ValueError(f"assets {missing} are not among {[c.name for c in configs]}")
        configs = [c for c in configs if c.name in wanted]
    if not configs:
        raise ValueError("no asset configs to build a definition from")
    return configs


def _mapped_function(cfg, name: str):
    """The FunctionSpec serving the scalar form of measure `name` (through a preset key the
    contract counts, e.g. IRDeltaParallel for IRDelta), or None."""
    key = cfg.provided_forms.get((name, "scalar"), name)
    mapping = cfg.risk_measures.get(key)
    fn = mapping.scalar if mapping is not None else None
    if fn is None:
        return None
    return cfg.functions.get(fn) or cfg.portfolio_functions.get(fn)


def _names(measure) -> List[str]:
    return [n for n in (measure.name, getattr(measure, "base_name", None)) if n]


def _why_not(cfg, measure) -> Optional[str]:
    """None if `cfg` serves `measure`'s scalar form, else why not (its declared reason if any). A
    Bond, IRSwap or IRSwaption config maps every contract measure or does not load (R3-0, BOND_DESIGN
    4.1), so both answers come from a class without a contract (e.g. a ConfigInstrument), which may
    declare a measure under unsupported_measures, or from a measure outside the contracts."""
    names = _names(measure)
    if any(_mapped_function(cfg, n) is not None for n in names):
        return None
    for n in names:
        reasons = cfg.unsupported_measures.get(n, {})
        reason = reasons.get("scalar") or reasons.get("*")
        if reason:
            return f"declared unsupported: {reason}"
    return "not mapped"


def _level_unit(configs, measure: str) -> str:
    """The one unit every config declares for `measure`; mixed units make attribution meaningless."""
    units: Dict[str, List[str]] = {}
    for cfg in configs:
        units.setdefault(_mapped_function(cfg, measure).unit, []).append(cfg.name)
    if len(units) > 1:
        raise ValueError(
            f"{measure} is declared in different units across the book: {units}. One PnlDefinition scales"
            " every level by one factor, so convert in the asset configs until every asset uses one unit"
        )
    return next(iter(units))


def definition_for(source=None, kind: str = "auto", assets: Optional[Iterable[str]] = None, **flags):
    """The IR PnlDefinition for the assets in `source` (see _configs), with rate/vol units read from
    the configs' IRFwdRate / IRAnnualImpliedVol mappings (bp -> 1, pct -> 100, decimal -> 1e4).

    kind: 'swaption' = all six attributes (delta, gamma, vega, vanna, volga, theta); 'bond' =
    delta, gamma, theta (also right for swaps: gs names only, no vol); 'ir' = ir_pnl_definition's
    defaults (all six); 'auto' = 'swaption' if any config's instrument is IRSwaption, else 'bond'.
    Keyword flags (delta=, gamma=, vega=, vanna=, volga=, theta=) override the kind's.

    Raises ValueError when an asset does not serve a measure the definition reads (every held
    asset must answer every measure: pnl_explain calcs them all for every instrument; only a class
    without a contract, e.g. a ConfigInstrument, can still declare one unsupported, a bond, swap or
    swaption config with a gap does not load), or when a level is declared in different units on
    different assets."""
    from pricebt.backtests.backtest_objects import ir_pnl_definition

    if kind not in (*_KIND_FLAGS, "auto"):
        raise ValueError(f"kind must be one of {['auto', *_KIND_FLAGS]}, got {kind!r}")
    configs = _configs(source, assets)
    if kind == "auto":
        kind = "swaption" if any(c.instrument == "IRSwaption" for c in configs) else "bond"
    wanted = {**_KIND_FLAGS[kind], **flags}
    risks = dict.fromkeys(ir_pnl_definition(**wanted).get_risks())
    problems = [f"{c.name}: {m!r} {why}" for c in configs for m in risks if (why := _why_not(c, m))]
    if problems:
        raise ValueError(
            "every held asset must map every measure the definition reads (swaps and bonds map the vol"
            " greeks and vol levels to 0.0, IR_RISK_DESIGN R2-8; a declaration does not count):\n  " + "\n  ".join(problems)
        )
    uses = lambda *names: any(wanted.get(n, True) for n in names)  # noqa: E731
    rate_unit = _level_unit(configs, "IRFwdRate") if uses("delta", "gamma", "vanna") else "bp"
    vol_unit = _level_unit(configs, "IRAnnualImpliedVol") if uses("vega", "vanna", "volga") else "bp"
    return ir_pnl_definition(rate_unit, vol_unit, **wanted)


def describe(definition) -> pd.DataFrame:
    """One row per PnlAttribute: what it multiplies, how, and the unit it checks levels against."""
    return pd.DataFrame([{
        "attribute": a.attribute_name,
        "risk (t-1)": repr(a.attribute_metric),
        "level move": repr(a.market_data_metric),
        "cross level move": repr(a.cross_market_data_metric) if a.cross_market_data_metric is not None else "",
        "formula": "1/2 k R dm^2" if a.second_order else ("k R dm1 dm2" if a.cross_market_data_metric is not None else "k R dm"),
        "k (scaling_factor)": a.scaling_factor,
        "level unit": a.market_data_unit or "",
    } for a in definition.attributes])


# ------------------------------------------------------------------------------------ results


def _float(v) -> Optional[float]:
    return float(v) if v is not None and math.isfinite(float(v)) else None


def explain_stats(table: pd.DataFrame) -> Dict[str, Any]:
    """Summary of a pnl_explain_table():

    totals            column sums (the six fixed columns and every attribute; financing_pnl is the
                      repo interest a financed position paid, pricebt DEV-E22, and is part of both
                      economic_pnl and explained_pnl)
    r2                1 - sum(residual^2) / sum((economic - mean economic)^2): how much of the
                      step-to-step economic P&L the attribution explains, with no refit (stricter
                      than corr^2; a biased attribution scores below 1 even when correlated)
    residual_share    residual variance share var(residual) / var(economic): blind to a steady
                      bias (a sign-flipped Theta leaves it near 0)
    abs_residual_ratio  sum|residual| / sum|economic|: counts noise too, so it grades only next
                      to a signature (a tiny P&L, e.g. an option expiring worthless, inflates it)
    total_residual_ratio  |sum residual| / sum|economic|: the steady-bias share
    unexplained       the graded number: the worst of residual_share, 1 - r2, total_residual_ratio
    worst_date, worst_residual  the step with the largest |residual|
    residual_corr     corr(residual, attribute) per attribute: the one the residual co-moves with
                      is the first suspect (references/diagnosing-residuals.md)
    signatures        {attribute: implied scale} for each attribute with |corr| >= SIGNATURE_CORR
                      whose implied scale 1 + total residual / attribute total (what the attribute
                      must be multiplied by to absorb the residual) reads as a known error: about
                      -1 a sign flip, 2 half size, ~0 far too large, >= 20 far too small
    finite            False if any cell is NaN/inf: then every ratio is None (one NaN level
                      poisons every later cumulative value of pnl_explain())
    """
    attrs = [c for c in table.columns if c not in FIXED]
    values = table[FIXED + attrs].astype(float)
    finite = bool(np.isfinite(values.to_numpy()).all())
    stats: Dict[str, Any] = {
        "steps": len(table),
        "finite": finite,
        "totals": {c: _float(values[c].sum()) for c in FIXED + attrs},
        "r2": None, "residual_share": None, "abs_residual_ratio": None, "total_residual_ratio": None, "unexplained": None,
        "worst_date": None, "worst_residual": None, "residual_corr": {}, "signatures": {},
    }
    if not finite or not len(table):
        return stats
    econ, resid = values["economic_pnl"], values["residual_pnl"]
    ss_tot = float(((econ - econ.mean()) ** 2).sum())
    worst = resid.abs().idxmax()
    stats.update(
        r2=1.0 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else None,
        residual_share=_float(resid.var() / econ.var()) if len(table) > 1 and econ.var() > 0 else None,
        abs_residual_ratio=float(resid.abs().sum() / econ.abs().sum()) if econ.abs().sum() > 0 else None,
        total_residual_ratio=float(abs(resid.sum()) / econ.abs().sum()) if econ.abs().sum() > 0 else None,
        worst_date=worst, worst_residual=float(resid[worst]),
        residual_corr={a: _float(resid.corr(values[a])) if values[a].std() > 0 and resid.std() > 0 else None for a in attrs},
    )
    parts = [stats["residual_share"], stats["total_residual_ratio"], None if stats["r2"] is None else 1.0 - stats["r2"]]
    stats["unexplained"] = max((p for p in parts if p is not None), default=None)
    totals = stats["totals"]
    for a, c in stats["residual_corr"].items():
        if c is not None and abs(c) >= SIGNATURE_CORR and totals[a]:
            scale = 1.0 + totals["residual_pnl"] / totals[a]
            if _reading(scale):
                stats["signatures"][a] = scale
    return stats


def grade(stats: Dict[str, Any], warn: float = RESIDUAL_SHARE_WARN, fail: float = RESIDUAL_SHARE_FAIL) -> str:
    """FAIL on a non-finite table, on an unexplained share above `fail`, or when a residual
    signature names an attribute and the residual is material (the unexplained share or
    sum|residual|/sum|economic| above `warn`); WARN above `warn`; INFO when no share is defined
    (fewer than two steps, or no economic P&L); else PASS. See grade_reason for the words."""
    if not stats["finite"]:
        return "FAIL"
    score = stats["unexplained"]
    if score is None:
        return "INFO"
    if stats["signatures"] and _material(stats, warn):
        return "FAIL"
    return "PASS" if score <= warn else ("WARN" if score <= fail else "FAIL")


def _material(stats: Dict[str, Any], warn: float) -> bool:
    return max(stats["unexplained"], stats["abs_residual_ratio"] or 0.0) > warn


def _reading(scale: float) -> Optional[str]:
    if -1.25 <= scale <= -0.8:
        return "its sign is flipped"
    if 1.6 <= scale <= 2.5:
        return "it is about half its true size (on PNL_gamma: the half-gamma trap)"
    if abs(scale) < 0.1:
        return "it is far too large (per-year theta, a x100 or x1e4 unit?)"
    if abs(scale) >= 20:
        return "it is far too small (a level in pct or decimal declared bp, a greek per 1%?)"
    return None  # a size error with no known cause: the unexplained share still grades it


def grade_reason(stats: Dict[str, Any], warn: float = RESIDUAL_SHARE_WARN, fail: float = RESIDUAL_SHARE_FAIL) -> str:
    """One line: the grade's inputs and, when there is one, the attribute the residual names."""
    if not stats["finite"]:
        return "the table has NaN/inf (no statistics)"
    if stats["unexplained"] is None:
        return "no statistics: fewer than two steps, or no economic P&L"
    fmt = lambda v: "n/a" if v is None else f"{v:.2%}"  # noqa: E731
    text = (f"unexplained {fmt(stats['unexplained'])} (worst of residual variance share {fmt(stats['residual_share'])}, "
            f"1 - r2 {fmt(None if stats['r2'] is None else 1.0 - stats['r2'])}, |sum residual|/sum|economic| "
            f"{fmt(stats['total_residual_ratio'])}; PASS <= {warn:.0%}, FAIL > {fail:.0%}); sum|residual|/sum|economic| "
            f"{fmt(stats['abs_residual_ratio'])}")
    for a, scale in stats["signatures"].items():
        text += (f"; residual signature: {a} x {scale:.3g} would absorb the residual, so {_reading(scale)}"
                 + ("" if _material(stats, warn) else " (immaterial on this book)"))
    totals = stats["totals"]
    if totals.get("financing_pnl"):
        text += (f"; financed book: coupons {totals['cashflow_pnl']:,.2f} and repo interest {totals['financing_pnl']:,.2f} booked as cash by the engine"
                 " (pricebt DEV-E22; financing is explained by definition)")
    return text


def attribution_frames(bt) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """(pnl_explain_table(), its cumulative attribute + residual + economic columns) for charts.
    The cumulative attribute columns equal pnl_explain()'s cumulative values."""
    table = bt.pnl_explain_table()
    if table is None:
        raise ValueError("the backtest has no PnlDefinition: run_backtest(..., pnl_explain=definition_for(session))")
    attrs = [c for c in table.columns if c not in FIXED]
    return table, table[attrs + ["residual_pnl", "economic_pnl"]].cumsum()


# ------------------------------------------------------------------------------------ demo / CLI


def demo_backtest(kind: str = "swaption", end: Optional[dt.date] = None, risks=None, **config_edits):
    """A toy book (tests/assets, PYTHONPATH must include tests): 'swaption' = a long 1y10y ATM payer
    held daily from 2024-01-02; 'bond' = a long financed toy bond held from 2024-05-06 across its
    2024-05-15 coupon (dropped from Price on 2024-05-14, T+1): its asset maps FinancingToDate, so
    the engine books the coupon and the repo interest as holding cash (pricebt DEV-E22) and the
    table shows them as cashflow_pnl and financing_pnl, with no Cashflows among the risks."""
    from pricebt.backtests.actions import AddTradeAction
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.backtests.strategy import Strategy
    from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements
    from pricebt.instrument import Bond, IRSwaption
    from pricebt.session import PricebtSession

    if kind == "swaption":
        config, start = REPO_ROOT / "tests/assets/toy_usd_swaption.yaml", dt.date(2024, 1, 2)
        inst = IRSwaption("Pay", "10y", "USD", notional_amount=1e6, expiration_date="1y", strike="ATM", buy_sell="Buy", name="payer")
        end, risks = end or dt.date(2024, 3, 28), risks or []
    elif kind == "bond":
        config, start = REPO_ROOT / "tests/assets/toy_usd_bond.yaml", dt.date(2024, 5, 6)
        inst = Bond(identifier="TOY 4.25 2034-11-15", size=1e6, buy_sell="Buy", settlement_currency="USD", name="bond")
        end, risks = end or dt.date(2024, 6, 28), risks or []
    else:
        raise ValueError(f"kind must be 'swaption' or 'bond', got {kind!r}")
    if config_edits:  # in-memory edits, e.g. functions={"vega": {...}}: each named entry is replaced whole
        import yaml

        raw = yaml.safe_load(config.read_text(encoding="utf-8"))
        for key, value in config_edits.items():
            raw[key] = {**raw[key], **value} if isinstance(value, dict) else value
        config = raw
    session = PricebtSession.use(assets=[config])
    trigger = DateTrigger(DateTriggerRequirements(dates=[start]), [AddTradeAction(inst, name="Add")])
    return GenericEngine().run_backtest(Strategy(None, trigger), start=start, end=end, frequency="1b", risks=risks,
                                        pnl_explain=definition_for(session), show_progress=False)


def _json(v):
    return v.isoformat() if isinstance(v, (dt.date, pd.Timestamp)) else str(v)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo", choices=["swaption", "bond"], help="attribute a toy backtest and print the stats")
    ap.add_argument("--definition", nargs="+", metavar="CONFIG", help="asset config YAMLs: print the definition they get")
    ap.add_argument("--kind", default="auto", choices=["auto", *_KIND_FLAGS])
    args = ap.parse_args(argv)
    if args.definition:
        try:
            definition = definition_for(args.definition, kind=args.kind)
        except ValueError as exc:  # the gap list: every unmapped / declared measure, or mixed units
            print(f"cannot attribute this book: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        table = describe(definition)
        print(table.to_string(index=False))
        return table
    if args.demo:
        table, _ = attribution_frames(demo_backtest(args.demo))
        stats = explain_stats(table)
        print(table.head(10).to_string())
        print(json.dumps({**stats, "grade": grade(stats), "grade_reason": grade_reason(stats)}, indent=2, default=_json))
        return stats
    ap.print_help()
    return None


if __name__ == "__main__":
    main()
