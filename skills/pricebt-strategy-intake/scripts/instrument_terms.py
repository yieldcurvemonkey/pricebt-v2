"""Per-class trade terms the strategy skills need (IRSwap, IRSwaption, Bond): which kwarg holds the
position's direction and which its size, the sign of a position's unit IRDelta, the opposite
position, the spec-kwarg check against the generated class signature, and the modelling notes a
report must carry. Shared by spec.py (validation) and recipes.py (build).

Signs follow the IR measure contract (src/pricebt/risk/contracts.py, IRDelta): the unit own-rate
delta is > 0 for a pay-fixed swap and a bought payer swaption, < 0 for a long bond. A swaption's
`pay_or_receive` is its OPTION TYPE (Pay = payer, Receive = receiver, Straddle); the position is
`buy_sell`, so the opposite of a bought payer is a SOLD payer, never a bought receiver (gs does the
same when it scales a swaption by -1: buy_sell flips, pay_or_receive stays).

    import sys; sys.path.insert(0, "skills/pricebt-strategy-intake/scripts")
    import instrument_terms as terms
    terms.risk_sign(IRSwaption(pay_or_receive="Receive", buy_sell="Sell", ...))   # +1

CLI (repository root): the problems, delta sign and opposite position of one spec instrument
    python skills/pricebt-strategy-intake/scripts/instrument_terms.py IRSwaption pay_or_receive=Pay buy_sell=Sell
"""
from __future__ import annotations

import argparse
import inspect
import sys
from typing import Any, Iterable, List

DIRECTION_KWARG = {"IRSwap": "pay_or_receive", "IRSwaption": "buy_sell", "Bond": "buy_sell"}
SIZE_KWARG = {"IRSwap": "notional_amount", "IRSwaption": "notional_amount", "Bond": "size"}
# kwargs a spec must state for the class: the config default would decide them invisibly
REQUIRED_KWARGS = {"IRSwaption": ("pay_or_receive", "buy_sell"), "Bond": ("buy_sell", "identifier")}
_OPPOSITE = {"pay": "Receive", "rec": "Pay", "receive": "Pay", "buy": "Sell", "sell": "Buy"}


def _word(v: Any) -> str:
    return str(getattr(v, "value", v)).split(".")[-1].strip().lower()


def build(cls_name: str, kwargs: dict, name: str = None):
    """`pricebt.instrument.<cls_name>(**kwargs, name=name)`; raises ValueError on a bad enum value."""
    from pricebt import instrument as _instrument_mod

    return getattr(_instrument_mod, cls_name)(**(kwargs or {}), name=name)


def kwarg_problems(cls_name: str, kwargs: dict, config_kwargs: Iterable[str] = ()) -> List[str]:
    """Spec kwargs that would build the wrong trade: a bad enum value (`buy_sell: Hold`), a name that
    is neither a field of the generated gs class nor a kwarg the spec's asset configs read (their
    `defaults:` keys, e.g. a bond config's repo terms `repo_term`, `repo_haircut`; gs accepts and
    ignores any other name, so a typo such as `expiry` silently falls back to the config default),
    and a missing required direction kwarg."""
    try:
        inst = build(cls_name, kwargs)
    except (TypeError, ValueError) as e:
        return [str(e)]
    from pricebt.instrument import GS_FIELDS

    out = []
    if cls_name in GS_FIELDS:  # a generated gs class: its signature is the field list
        params = inspect.signature(type(inst).__init__).parameters
        fields = {n for n, p in params.items() if p.kind is p.POSITIONAL_OR_KEYWORD} - {"self", "name"}
        extra = set(config_kwargs) - fields
        out += [f"{k!r} is not a {cls_name} field (gs would silently ignore it) nor a default of the spec's asset configs; fields: "
                f"{', '.join(sorted(fields))}" + (f"; config kwargs: {', '.join(sorted(extra))}" if extra else "")
                for k in inst.kwargs if k not in fields | extra]
    out += [f"{k} is required for {cls_name}: state it, the asset config's default is invisible in the spec"
            for k in REQUIRED_KWARGS.get(cls_name, ()) if inst.kwargs.get(k) is None]
    out += [f"{k} {inst.kwargs[k]!r} must be 0 in a backtest: the engine books only entry -Price and exit +Price, so an "
            "upfront amount paid after entry leaves Price without ever reaching cash (the entry cash already is the premium)"
            for k in ("premium", "fee") if inst.kwargs.get(k) not in (None, 0, 0.0)]
    return out


def risk_sign(inst) -> int:
    """Sign of the position's unit IRDelta scalar (currency per +1bp of its own rate), i.e. the sign
    an AddScaledTradeAction risk_measure scaling_level must carry so the trade keeps its direction.
    IRSwap: Pay +1, Receive -1. IRSwaption: (Buy +1, Sell -1) x (payer +1, receiver -1). Bond: Buy -1,
    Sell +1. Any other class: -1 if it has a Receive pay_or_receive, else +1. Raises ValueError for a
    Straddle (its delta sign depends on moneyness, ~0 at the money) or a swaption/bond whose
    direction is unstated."""
    cls, kw = type(inst).__name__, inst.kwargs
    if cls in ("IRSwaption", "Bond"):
        missing = [k for k in REQUIRED_KWARGS[cls] if k != "identifier" and kw.get(k) is None]
        if missing:
            raise ValueError(f"{cls} {inst.name or ''}: {', '.join(missing)} not set, so the delta sign is unknown")
        held = 1 if _word(kw["buy_sell"]) == "buy" else -1
        if cls == "Bond":
            return -held
        if _word(kw["pay_or_receive"]) == "straddle":
            raise ValueError(f"IRSwaption {inst.name or ''} is a Straddle: its delta is ~0 at the money and its sign "
                             "depends on moneyness, so it cannot be dv01-sized or signed; size it by notional or nav")
        return held * (-1 if _word(kw["pay_or_receive"]).startswith("rec") else 1)
    v = kw.get("pay_or_receive")
    return -1 if v is not None and _word(v).startswith("rec") else 1


def try_build(entry: Any):
    """A spec `instruments.<k>` entry -> its instrument, or None when it cannot be built (the spec
    validator reports why elsewhere)."""
    try:
        return build(entry["class"], entry.get("kwargs") or {})
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def opposite_positions(first: dict, second: dict) -> bool:
    """Whether two spec instrument entries are opposite positions: unit IRDelta signs differ (so each
    leg can be sized to +/- the same dv01). Straddles have no delta sign: opposite buy_sell. True when
    either entry cannot be built (reported elsewhere)."""
    a, b = try_build(first), try_build(second)
    if a is None or b is None:
        return True
    try:
        return risk_sign(a) != risk_sign(b)
    except ValueError:
        sides = {_word(i.kwargs.get("buy_sell")) for i in (a, b)}
        return sides == {"buy", "sell"}


def dv01_sizing_problems(entries: dict) -> List[str]:
    """sizing.method dv01_target signs each sized leg by risk_sign: every non-hedge instrument needs one."""
    out = []
    for key, entry in entries.items():
        inst = try_build(entry) if key != "hedge" and isinstance(entry, dict) else None
        if inst is None:
            continue
        try:
            risk_sign(inst)
        except ValueError as e:
            out.append(f"sizing.dv01_target: instruments.{key}: {e}")
    return out


def opposite(inst, name: str):
    """The opposite position: the class's direction kwarg flipped (IRSwap pay_or_receive; IRSwaption
    and Bond buy_sell, the option type unchanged), else quantity_ negated."""
    key = DIRECTION_KWARG.get(type(inst).__name__, "pay_or_receive")
    v = inst.kwargs.get(key)
    if v is None:
        return inst.clone(name=name, quantity_=-inst.quantity_)
    return inst.clone(name=name, **{key: _OPPOSITE[_word(v)]})


def notes(insts: dict, trade_duration: Any = None, hedge_key: str = "hedge") -> List[str]:
    """Class-specific modelling notes for built.notes (one line each; the report must state them)."""
    out, classes = [], {k: type(v).__name__ for k, v in insts.items()}
    for key, inst in insts.items():
        cls, kw = classes[key], inst.kwargs
        if cls == "IRSwaption":
            out.append(f"{key}: IRSwaption {_word(kw.get('buy_sell'))} {_word(kw.get('pay_or_receive'))}: buy_sell is the "
                       "position, pay_or_receive the option type; the premium is the entry cash -Price, so kwargs "
                       "premium must stay 0/unset (a premium paid after entry leaves Price without ever reaching cash)")
            if str(trade_duration) == "expiration_date":
                out.append(f"{key}: exits on expiration_date book the library's Price on that date (intrinsic, or the "
                           "exercised underlying's PV for physical settlement); no swap position is carried past expiry")
        if cls == "Bond":
            repo = ", ".join(f"{k} {kw[k]!r}" for k in sorted(kw) if k.startswith("repo")) or "the asset config's defaults"
            out.append(f"{key}: Bond {_word(kw.get('buy_sell'))} {kw.get('identifier')!r} size {kw.get('size')}: financed in repo "
                       f"({repo}); the engine books its coupons and the change of FinancingToDate as cash on every mark (pricebt "
                       "DEV-E22), so Total = Price change + coupons - repo interest; BackTest.pnl_explain_table() shows them as "
                       "cashflow_pnl and financing_pnl")
    if hedge_key in insts and len(set(classes.values())) > 1:
        out.append("mixed instrument types: own-rate deltas (swap par rate, swaption forward, bond yield) are summed "
                   "across types by HedgeAction / triggers / sizing -- approximate (DEV-I12 additivity caveat), exact "
                   "only for a hedge of the same type")
    elif "second" in insts and classes.get("primary") != classes.get("second"):
        out.append(f"legs {classes['primary']} vs {classes['second']}: each is sized against its own-rate delta "
                   "(bond: per bp of yield; swap: per bp of par rate), so the book is neutral to a parallel move of both "
                   "rates, not to their spread -- the spread is the trade (DEV-I12)")
    return out


def main(argv=None) -> int:
    import yaml

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cls", help="instrument class, e.g. IRSwaption")
    ap.add_argument("kwargs", nargs="*", help="key=value spec kwargs (values read as YAML scalars)")
    args = ap.parse_args(argv)
    kwargs = {k: yaml.safe_load(v) for k, _, v in (kv.partition("=") for kv in args.kwargs)}
    problems = kwarg_problems(args.cls, kwargs)
    for p in problems:
        print(f"PROBLEM: {p}")
    inst = try_build({"class": args.cls, "kwargs": kwargs})
    if inst is not None:
        try:
            print(f"unit IRDelta sign: {risk_sign(inst):+d}")
        except ValueError as e:
            print(f"unit IRDelta sign: none ({e})")
        opp = opposite(inst, "opposite")
        print(f"opposite: {type(opp).__name__}({opp.kwargs}, quantity_={opp.quantity_})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
