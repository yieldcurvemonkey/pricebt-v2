"""gs_quant-style instrument constructors (`gs_quant.instrument` names) that produce TERMS on a registered instrument spec (spec F-2).

`IRSwap('Pay', '10y', 'USD')` does not build a library object: it validates the gs arguments, converts them to pricebt terms and returns an
`InstrumentTemplate` (a `TradeTemplate`) on the instrument spec the session registered for ("swap", "USD") - the adapter's factory builds the library object
AT TRADE TIME on whatever pricer the market returns, so `fixed_rate='ATMF'` (the token `par`) is struck on your curve on the trade date. This module imports
no library and knows no convention: which floating rate, calendar, day count or settlement a swap has is the registered spec's `conventions` block.

Arguments are checked before any session is consulted, each error naming the argument (`termination_date`, `effective_date`, `notional_amount`, `fixed_rate`, `fee`,
`name`): dates and tenors by the rules of the shipped `swap` schema (`AssetSchema.check_term`), numbers must be finite and not booleans. Only what needs the registered
spec (its existence, the gs values its stack accepts) waits for the session.

gs_quant behaviours kept here (C6): numeric `fixed_rate` values are DECIMALS (0.04 = 4%; pricebt terms are percent, so the constructor converts - the one unit
conversion that lives in the facade, F-2), 'ATMF' / 'ATM' / 'PAR' (optionally with a bp offset, 'ATMF+25') are par-relative, tenors ('10y', '1m') are relative
to the trade date and passed to the factory untouched, `name=` names the trade (trade_ledger, ExitTradeAction), a negative `notional_amount` flips the direction,
and `notional_amount` is exposed on the template (ScaledTransactionModel('notional_amount', ...)).

Scope: the swaps of the registered specs. Swaptions are out of scope for this release (`IRSwaption` raises NotSupportedError); every other gs instrument (FX,
equity, credit, inflation, ...) and `Bond` are stubs raising NotSupportedError: write an adapter or a pricable for them (DESIGN.md).
"""
from __future__ import annotations

import copy
import functools
import math
import re
from typing import Any, Dict, Mapping, Optional, Tuple

from .backtests.gs_fields import SWAP_GS_FIELDS
from .common import BuySell, Currency, OptionStyle, OptionType, PayReceive, check_currency
from .contracts.schema import AssetSchema, SchemaRegistry, is_number
from .contracts.spec import InstrumentSpec, TradeTemplate
from .errors import ConfigError, NotSupportedError
from .session import current_session

__all__ = [
    "IRSwap", "IRSwaption", "InstrumentTemplate", "PayReceive", "BuySell", "Currency", "OptionType", "OptionStyle",
    "FXForward", "FXOption", "FXBinary", "FXVolatilitySwap", "EqOption", "EqVarianceSwap", "EqForward", "IRCap", "IRFloor", "IRXccySwap",
    "IRBasisSwap", "IRFRA", "InflationSwap", "CDIndex", "CDIndexOption", "Bond",
]

DEFAULT_NOTIONAL = 1e7  # gs_quant's default notional_amount
PAR_SPREAD_KEY = "par_spread_bp"  # the standard `extras` key of the swap schema: a spread over par in bp when fixed_rate is `par`
_REL = re.compile(r"^\s*(ATMF|ATM|PAR)\s*(?:([+-])\s*(\d+(?:\.\d+)?)\s*(?:BPS?)?)?\s*$", re.I)



# ------------------------------------------------------------------------------ the template
class InstrumentTemplate(TradeTemplate):
    """A TradeTemplate built by a gs-style constructor: `inputs` are the gs arguments as given, `gs_name` the gs `name=`, `notional_amount` the unsigned
    notional and `instrument_type` the gs class name."""

    def __init__(self, name: str, spec: InstrumentSpec, terms: Mapping[str, Any], *, instrument_type: str, gs_name: Optional[str], notional_amount: float,
                 inputs: Mapping[str, Any]):
        super().__init__(name, spec, terms)
        self.instrument_type = instrument_type
        self.gs_name = gs_name
        self.notional_amount = float(notional_amount)
        self.inputs: Dict[str, Any] = dict(inputs)

    def clone(self, **changes: Any) -> "InstrumentTemplate":
        """gs `Instrument.clone(**changes)`: the same constructor with some arguments replaced (e.g. `clone(name='x')`), on the session's current spec."""
        return _CONSTRUCTORS[self.instrument_type](**{**copy.deepcopy(self.inputs), **changes})

    def _derived(self, name: str, changes: Mapping[str, Any]) -> "InstrumentTemplate":
        """`with_terms` / `renamed`: the terms change, so the gs arguments (`inputs`, `notional_amount`) are brought in step, or `clone()` would rebuild the OLD trade."""
        new = super()._derived(name, changes)
        new.notional_amount = float(new.terms["notional"])
        new.inputs = _inputs_after(self.inputs, self.terms, new.terms)  # a new dict: `copy.copy` shares the old one
        return new

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={_show(v)}" for k, v in self.inputs.items() if v is not None)
        return f"{self.instrument_type}({args})" + (f" [{self.name}]" if self.name != self.gs_name else "")


def _show(v: Any) -> str:
    return repr(getattr(v, "value", v))


def _rate_input(terms: Mapping[str, Any]) -> Any:
    """The gs `fixed_rate` argument for these terms: 'ATMF' / 'ATMF+25' for par (and its spread in bp), a decimal for a number. (A spread means something only next to
    `par`; a gs argument cannot carry any other `extras` key.)"""
    if terms["fixed_rate"] != "par":
        return terms["fixed_rate"] / 100.0
    off = (terms.get("extras") or {}).get(PAR_SPREAD_KEY, 0.0)
    return "ATMF" if not off else "ATMF" + f"{off:+.10f}".rstrip("0").rstrip(".")


def _inputs_after(inputs: Mapping[str, Any], old: Mapping[str, Any], new: Mapping[str, Any]) -> Dict[str, Any]:
    """`inputs` with the gs arguments of every term that changed rewritten from the new terms (the ones that did not change keep the value the user wrote)."""
    out = dict(inputs)
    if (new["side"], new["notional"]) != (old["side"], old["notional"]):
        out["pay_or_receive"], out["notional_amount"] = (PayReceive.Pay if new["side"] == "pay" else PayReceive.Receive), new["notional"]
    for term, arg in (("maturity", "termination_date"), ("effective", "effective_date")):
        if new.get(term) != old.get(term):
            out[arg] = new.get(term)
    if (new["fixed_rate"], new.get("extras")) != (old["fixed_rate"], old.get("extras")):
        out["fixed_rate"] = _rate_input(new)
    return out


# ------------------------------------------------------------------------------ argument parsing
def _number(x: Any, what: str) -> float:
    """A finite real number for the gs argument `what` (numpy scalars and numeric strings included). A bool is not one (True would be read as 1.0)."""
    v = x
    if isinstance(x, str):
        try:
            v = float(x)
        except ValueError:
            pass
    if not is_number(v) or not math.isfinite(float(v)):
        raise ConfigError(f"{what}={x!r}: must be a finite number" + (" (a boolean is not a number)" if isinstance(x, bool) or type(x).__name__ == "bool_" else ""), code="INSTRUMENT")
    return float(v)


@functools.lru_cache(maxsize=1)
def _swap_schema() -> AssetSchema:
    """The shipped neutral `swap` schema, read once: it says what a date, a tenor or a size looks like whatever spec (or none yet) is registered."""
    return SchemaRegistry.default().get("swap")


def _date_arg(arg: str, term: str, value: Any) -> None:
    """Reject a `termination_date` / `effective_date` that is not a date, an ISO date string, a tenor or one of the schema's tokens (`spot`) - before a session is needed."""
    try:
        _swap_schema().check_term(term, value)
    except ConfigError as e:
        tokens = list(_swap_schema().terms[term].tokens)
        raise ConfigError(f"IRSwap {arg}={value!r}: use a date, an ISO date string ('2034-01-02') or a tenor like '10y'" + (f" (or one of {tokens})" if tokens else ""),
                          code="INSTRUMENT") from e


def _rate_or_relative(x: Any, what: str) -> Tuple[Optional[float], float]:
    """-> (numeric rate in PERCENT or None for par, offset in bp). gs numbers are decimals; 'ATMF+25' is par + 25bp."""
    if x is None:
        return None, 0.0
    if isinstance(x, str):
        m = _REL.match(x)
        if not m:
            raise ConfigError(f"{what} {x!r}: use 'ATMF', 'ATM', 'ATMF+25' (bp) or a decimal rate (0.04 = 4%)", code="INSTRUMENT")
        off = float(m.group(3) or 0.0)
        return None, (-off if m.group(2) == "-" else off)
    v = _number(x, what)
    if abs(v) >= 1.0:
        raise ConfigError(f"{what}={v}: rates are decimals as in gs_quant (0.04 = 4%)", code="INSTRUMENT")
    return v * 100.0, 0.0


def _check_gs_kwargs(ctor: str, given: Mapping[str, Any], known: Tuple[str, ...], accepts: Mapping[str, Any], *, session: bool = True) -> None:
    unknown = sorted(k for k in given if k not in known)
    if unknown:
        raise TypeError(f"{ctor}() got unexpected keyword arguments {unknown}")
    bad = []
    for k, v in given.items():
        if v is None or (k == "fee" and _number(v, f"{ctor} fee") == 0.0):
            continue
        if str(getattr(v, "value", v)).lower() in {str(a).lower() for a in accepts.get(k, ())}:
            continue
        bad.append(k)
    if bad and not session:
        raise NotSupportedError(f"{ctor}: {sorted(bad)} are gs fields whose values only a session's stack can accept (`Stack.accepts_gs`) and there is no session: create one "
                                "first, PricebtSession.use(market=..., stack=<an adapter's Stack>); a value the stack does not list is refused there")
    if bad:
        raise NotSupportedError(f"{ctor}: {sorted(bad)} are not terms of the registered instrument spec: its `conventions` block defines them. Change the convention "
                                "on the spec (PricebtSession(instruments={...})) or write an adapter for the variant")


def _notional(x: Any) -> Tuple[float, float]:
    """(unsigned notional, sign); gs lets a negative notional flip the direction."""
    n = DEFAULT_NOTIONAL if x is None else _number(x, "IRSwap notional_amount")
    if n == 0.0:
        raise ConfigError("notional_amount must be non-zero", code="INSTRUMENT")
    return abs(n), (1.0 if n > 0 else -1.0)


def _accepted_gs_values(ccy: str) -> Mapping[str, Any]:
    """The gs values accepted for the ("swap", ccy) spec in force: its stack's `accepts_gs` unless the session substituted a spec of its own (see `accepted_gs`)."""
    s = current_session()
    return s.accepted_gs("swap", ccy) if s is not None else {}


def _session_spec(asset_class: str, ccy: str, what: str) -> InstrumentSpec:
    s = current_session()
    if s is None:
        raise ConfigError(f"{what} needs an instrument spec for {asset_class}:{ccy}: create a session first, once - PricebtSession.use(market=..., stack=<an adapter's Stack>)",
                          code="SESSION")
    return s.instrument_spec(asset_class, ccy)


# ------------------------------------------------------------------------------ constructors
def IRSwap(pay_or_receive: Any = None, termination_date: Any = None, notional_currency: Any = None, notional_amount: Any = None, effective_date: Any = None,
           *, fixed_rate: Any = "ATMF", name: Optional[str] = None, **gs_kwargs: Any) -> InstrumentTemplate:
    """gs `IRSwap(pay_or_receive, termination_date, notional_currency, notional_amount, effective_date, ..., fixed_rate, name)` as terms on the session's
    ("swap", notional_currency) spec.

    pay_or_receive: Pay (pay fixed, +dv01; the default) / Receive. termination_date: tenor from the effective date ('10y') or a date. effective_date: None
    (the spec's spot), a forward tenor ('1y') or a date. fixed_rate: 'ATMF' (par on the trade date), 'ATMF+10' (bp over par) or a decimal (0.04 = 4%).
    notional_amount: default 1e7; negative flips the direction."""
    ccy = check_currency(notional_currency, "IRSwap")
    _check_gs_kwargs("IRSwap", gs_kwargs, SWAP_GS_FIELDS, _accepted_gs_values(ccy), session=current_session() is not None)
    pr = PayReceive.coerce(pay_or_receive if pay_or_receive is not None else PayReceive.Pay)
    if pr is PayReceive.Straddle:
        raise ConfigError("IRSwap: pay_or_receive must be Pay or Receive", code="INSTRUMENT")
    if name is not None and not isinstance(name, str):
        raise ConfigError(f"IRSwap name={name!r}: the trade name must be a string", code="INSTRUMENT")
    if termination_date is None:
        raise ConfigError("IRSwap needs termination_date (a tenor like '10y' or a date)", code="INSTRUMENT")
    _date_arg("termination_date", "maturity", termination_date)
    if effective_date is not None:
        _date_arg("effective_date", "effective", effective_date)
    notional, sgn = _notional(notional_amount)
    side_sign = (1.0 if pr.is_pay else -1.0) * sgn
    rate, off = _rate_or_relative(fixed_rate, "IRSwap fixed_rate")
    spec = _session_spec("swap", ccy, "IRSwap")  # last: every argument problem is reported without needing a session
    terms: Dict[str, Any] = {"side": "pay" if side_sign > 0 else "receive", "maturity": termination_date, "notional": notional, "fixed_rate": "par" if rate is None else rate}
    if effective_date is not None:
        terms["effective"] = effective_date
    if off:
        terms["extras"] = {PAR_SPREAD_KEY: off}
    inputs = {"pay_or_receive": pay_or_receive, "termination_date": termination_date, "notional_currency": notional_currency, "notional_amount": notional_amount,
              "effective_date": effective_date, "fixed_rate": fixed_rate, "name": name, **gs_kwargs}
    return InstrumentTemplate(name or "IRSwap", spec, terms, instrument_type="IRSwap", gs_name=name, notional_amount=notional, inputs=inputs)


def IRSwaption(*args: Any, **kwargs: Any) -> Any:
    raise NotSupportedError("IRSwaption is not supported in this release: swaptions and volatility are out of scope until the later options workflow "
                            "(linear swaps and bonds only). Use IRSwap, or write your own instrument spec")


_CONSTRUCTORS = {"IRSwap": IRSwap}


# ------------------------------------------------------------------------------ stubs (GS-priced instruments)
def _stub(cls_name: str, hint: str) -> type:
    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        raise NotSupportedError(f"{cls_name} is priced by GS services in gs_quant and has no shipped pricebt instrument. {hint}")

    return type(cls_name, (), {"__init__": __init__, "__doc__": f"Stub: {cls_name} raises NotSupportedError ({hint})"})


_FX_HINT = "Write an FX pricable (value(ctx), fx_delta/fx_vega measures) over a market that holds FX spot/forwards/vols, then use it with GenericEngine."
_EQ_HINT = "Write an equity pricable (value(ctx), eq_delta/eq_vega measures) over a market that holds spot/vols, then use it with GenericEngine."
_IR_HINT = "Only IRSwap ships; register an instrument spec (an adapter factory plus bindings) for other rates products (DESIGN.md)."

FXForward = _stub("FXForward", _FX_HINT)
FXOption = _stub("FXOption", _FX_HINT)
FXBinary = _stub("FXBinary", _FX_HINT)
FXVolatilitySwap = _stub("FXVolatilitySwap", _FX_HINT)
EqOption = _stub("EqOption", _EQ_HINT)
EqVarianceSwap = _stub("EqVarianceSwap", _EQ_HINT)
EqForward = _stub("EqForward", _EQ_HINT)
IRCap = _stub("IRCap", _IR_HINT)
IRFloor = _stub("IRFloor", _IR_HINT)
IRXccySwap = _stub("IRXccySwap", _IR_HINT)
IRBasisSwap = _stub("IRBasisSwap", _IR_HINT)
IRFRA = _stub("IRFRA", _IR_HINT)
InflationSwap = _stub("InflationSwap", _IR_HINT)
CDIndex = _stub("CDIndex", "Credit instruments are not shipped; write an adapter or a pricable.")
CDIndexOption = _stub("CDIndexOption", "Credit instruments are not shipped; write an adapter or a pricable.")
Bond = _stub("Bond", "Trade a bond as terms on a registered bond spec: add_trade(instrument=<bond spec>, terms={side, security, notional}).")
