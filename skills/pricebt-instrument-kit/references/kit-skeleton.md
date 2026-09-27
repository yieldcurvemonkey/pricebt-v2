# Skeleton: `<lib>_adapter/conventions.py`, `swap.py` (the Kit) and the `Stack` in `__init__.py`

EXECUTED against `acmelib` (the fictional library of `skills/pricebt-wire-external-library/example/`), on top of the wrap skeleton of `pricebt-wrap-and-pricer`
(`skills/pricebt-wrap-and-pricer/references/pricer-skeleton.md`: `_compat.py`, `wrap.py`). What ran and what it printed (first checkpoint of a new adapter):

```
loads: swap ['carry', 'convexity', 'delta', 'delta_ladder', 'dv01', 'gamma', 'rate', 'roll', 'value']              # build_spec(..) in strict mode, default allow-list
value / rate / dv01 agree with the reference stack (both sides, two notionals)                                       # payer and receiver, 1e7 and 3.3e7, fixed rate 4.2
stub: NotImplementedError second derivative for the SAME +1bp move as dv01, per unit as built
unsupported -> [CFG-CONVENTION-UNSUPPORTED] convention day_count='thirty360' is in the vocabulary but this adapter does not support it: <LIB> has only ACT/360 and ACT/365 bases; supported: ['act360', 'act365f']
unknown key -> [CFG-CONVENTION] unknown convention keys ['spotlag'] for asset class 'swap'; the vocabulary is [...] (spotlag: did you mean 'spot_lag_days'?)
facade: usd_sofr_ois                                                                                                 # PricebtSession(market=..., stack=STACK).instrument_spec("swap", "USD")
```

The order of work it encodes: (1) the WHOLE default block loads before any body exists (`build_spec` validates every required name, every method target and its signature); (2) the factory
resolves dates, par and percent like the reference stack; (3) `value`, `rate`, `dv01` in pricebt's unit and sign on a FRESH swap; (4) every remaining stub is replaced one at a time under
the conformance kit (`pricebt-layers-and-ladder`, `pricebt-conformance-and-tieout`). The stubs (`gamma`, `delta_ladder`, `carry`, `roll`, `delta`, `convexity`, and the value of a SEASONED swap) raise
`NotImplementedError` naming what to do: they are the to-do list, and until you replace them two test cases of `references/kit-tests.md` stay red (executed on this skeleton: those two fail, every other case passes). EVERY line
that uses `lib.` or one of `acmelib`'s codes (`'A360'`, `'1y'`, `'MF'`) is yours to rewrite from `<LIB>`'s documentation; each carries a `# <LIB>` tag.

## `conventions.py`: honour or refuse, never default

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping

from pricebt.contracts.schema import AssetSchema, SchemaRegistry
from pricebt.errors import ConfigError

# pricebt token -> <LIB> code (<LIB>: read them from its documentation; acmelib says 'A360', '1y', 'MF')
_BASIS = {"act360": "A360", "act365f": "A365"}  # <LIB>
_FREQ = {"monthly": "1m", "quarterly": "3m", "semiannual": "6m", "annual": "1y"}  # <LIB>
_BDC = {"unadjusted": "U", "following": "F", "modified_following": "MF", "preceding": "P"}  # <LIB>

# EVERY key of the schema's vocabulary gets a decision: the values this adapter honours ('*' = whatever the schema accepts). A missing key is a KeyError at first use.
_SUPPORTED: Dict[str, Any] = {
    "calendar": "*", "spot_lag_days": "*", "payment_lag_days": "*", "day_count": tuple(_BASIS), "frequency": tuple(_FREQ), "business_day_convention": tuple(_BDC),
    "compounding": ("daily_compounded",), "stub": ("short_front",), "end_of_month": (False,), "fixing_lag_days": (1,), "time_accrual": ("lump",),
}
_REASON = {  # WHY it is refused: the message a user reads
    ("day_count", None): "<LIB> has only ACT/360 and ACT/365 bases",
    ("compounding", "daily_average"): "<LIB>'s overnight leg only compounds",
    ("time_accrual", "linear"): "<LIB> accrues in whole days: carry and roll happen at the date change",
}
_SCHEMA: List[AssetSchema] = []


def _schema() -> AssetSchema:
    if not _SCHEMA:
        _SCHEMA.append(SchemaRegistry.default().get("swap"))
    return _SCHEMA[0]


@dataclass(frozen=True)
class Settings:
    """The shared block translated into <LIB>'s vocabulary. `calendar` is still the SNAPSHOT's calendar name: the pricer maps it to the name <LIB> has it under."""

    calendar: str
    spot_lag: int
    basis: str
    freq: str
    bdc: str
    pay_lag: int


def swap_conventions(raw: Mapping[str, Any]) -> Settings:
    clean = _schema().check_conventions(raw, require_all=True)  # vocabulary: unknown key, wrong type, unknown token, missing key -> CFG-CONVENTION
    for key, v in clean.items():
        ok = _SUPPORTED[key]
        if ok != "*" and v not in ok:
            why = _REASON.get((key, v)) or _REASON.get((key, None)) or "not implemented by <LIB>"
            raise ConfigError(f"convention {key}={v!r} is in the vocabulary but this adapter does not support it: {why}; supported: {list(ok)}", code="CFG-CONVENTION-UNSUPPORTED")
    return Settings(clean["calendar"], clean["spot_lag_days"], _BASIS[clean["day_count"]], _FREQ[clean["frequency"]], _BDC[clean["business_day_convention"]], clean["payment_lag_days"])


#: a complete block for a US overnight-rate swap. `usd_fed` is the NAME of a calendar in the market snapshot (the provider owns the holidays).
USD_SOFR_OIS_CONVENTIONS: Mapping[str, Any] = {
    "calendar": "usd_fed", "spot_lag_days": 2, "day_count": "act360", "frequency": "annual", "business_day_convention": "modified_following", "payment_lag_days": 2,
    "compounding": "daily_compounded", "stub": "short_front", "end_of_month": False, "fixing_lag_days": 1, "time_accrual": "lump",
}
```

## `swap.py`: the class, the factory, the default block, the Kit

```python
from __future__ import annotations

import re
from typing import Any, Dict, Mapping

from pricebt.contracts.spec import Built, Kit
from pricebt.errors import ConfigError
from pricebt.pricable import Valuation

from . import _compat as C
from .conventions import swap_conventions
from .wrap import LibPricer

lib = C.lib  # <LIB>: the library module, re-exported by _compat
_TENOR = re.compile(r"\d+[A-Za-z]")
DEFAULT_TENORS = ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
RECEIVER_TO_PAYER = -1.0  # <LIB>: its numbers are the RECEIVER's; pricebt's are holder-signed, payer-positive


def _lib(p: Any) -> LibPricer:
    if not isinstance(p, LibPricer):
        raise ConfigError(f"the market pricer is a {type(p).__name__}, not a LibPricer: set `wrap` on the pricer role to lib_adapter:wrap", code="CFG-WRAP")
    return p


class LibSwap:
    """One unit = one swap of `notional` (unsigned). `direction` +1 = payer of fixed (pricebt's sign). PLAIN DATA (the library contract + the direction): it deep-copies and pickles."""

    asset_class = "swap"

    def __init__(self, *, contract: Any, direction: int):
        if direction not in (1, -1):
            raise ConfigError(f"direction must be +1 (pay fixed) or -1 (receive fixed), got {direction!r}", code="SWAP")
        self.contract, self.direction = contract, int(direction)

    def _per_unit(self, x: float) -> float:
        return x * self.direction * self.contract.notional / lib.MM  # <LIB>: risk per ONE MILLION; a binding's `scale` is a constant and cannot read the notional

    def value(self, *, ctx: Any) -> Valuation:
        p = _lib(ctx.pricer)
        if C.to_date(self.contract.start) < p.reference_date:
            raise NotImplementedError("seasoned swap: needs published fixings and the cash sweep of [prev, ref) (see acme_adapter/swap.py: value, _paid_before)")
        with C.translate(p.ts), C.guard(p.reference_date):
            pv = lib.pv(self.contract, p.market())  # <LIB>: its pricer, in ITS sign and currency units
        k = self.direction * RECEIVER_TO_PAYER  # a Valuation cannot go through a binding's `sign`: the WHOLE conversion of pv (and cash) happens here
        return Valuation(ctx.ts, k * pv, 0.0, 0.0, {"reference_date": p.reference_date})

    def rate(self, *, ctx: Any) -> float:
        p = _lib(ctx.pricer)
        with C.translate(p.ts), C.guard(p.reference_date):
            return lib.par_rate(self.contract, p.market())  # <LIB>: a DECIMAL; the binding scales it to the schema's PERCENT (scale: 100)

    def dv01(self, *, ctx: Any, tenors=DEFAULT_TENORS) -> float:
        p = _lib(ctx.pricer)
        with C.translate(p.ts), C.guard(p.reference_date):
            return float(self._per_unit(lib.pv01(self.contract, p.market())))  # <LIB>: receiver's sign x direction; the binding's `sign: -1` finishes the flip

    def gamma(self, *, ctx: Any, tenors=DEFAULT_TENORS) -> float:
        raise NotImplementedError("second derivative for the SAME +1bp move as dv01, per unit as built")

    def delta_ladder(self, *, ctx: Any, tenors=DEFAULT_TENORS) -> Dict[str, float]:
        raise NotImplementedError("bucketed dollar delta keyed by tenor strings (<int><D|W|M|Y>); the values sum to dv01; a reducer converts the library's shape")

    def carry(self, *, ctx: Any) -> float:
        raise NotImplementedError("layer, ADR 005: carry = X_fwd - V0")

    def roll(self, *, ctx: Any) -> float:
        raise NotImplementedError("layer, ADR 005: roll = X_roll_act - X_fwd")

    def delta(self, *, ctx: Any) -> float:
        raise NotImplementedError("layer, ADR 005: delta = (V_base + cash - X_roll_act) + g.dz")

    def convexity(self, *, ctx: Any) -> float:
        raise NotImplementedError("layer, ADR 005: convexity = 1/2 dz'H dz")


def swap_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    """`(pricer, ts, *, terms, conventions) -> Built(obj, resolved_terms)`. Resolved HERE, at the fill pricer: `effective` (date | 'spot' | tenor), `maturity` (date | tenor),
    `fixed_rate` (PERCENT number | 'par'); returned as concrete `dt.date`s and a percent rate."""
    p, s = _lib(pricer), swap_conventions(conventions)
    rate, notional, direction = terms.get("fixed_rate", "par"), float(terms["notional"]), int(terms["direction"])  # `direction` is added by pricebt from `side`
    mat, eff = terms["maturity"], terms.get("effective", "spot")
    maturity = mat.strip().lower() if isinstance(mat, str) and _TENOR.fullmatch(mat.strip()) else C.lib_date(mat)  # <LIB>: lower-case tenors, its date spelling
    with C.translate(p.ts):
        cal = p.calendar(s.calendar)  # the SNAPSHOT's holidays, under the name <LIB> has them
        spot = lib.add_business_days(C.lib_date(p.reference_date), s.spot_lag, cal)  # <LIB>: reference date + spot lag business days
        if isinstance(eff, str) and eff.strip().lower() == "spot":
            effective = spot
        elif isinstance(eff, str) and _TENOR.fullmatch(eff.strip()):
            effective = lib.adjust(lib.add_tenor(spot, eff.strip().lower()), s.bdc, cal)  # <LIB>: a forward start: a tenor from spot, adjusted
        else:
            effective = lib.adjust(C.lib_date(eff), s.bdc, cal)  # <LIB>: an explicit date, adjusted
        kw = dict(calendar=cal, basis=s.basis, freq=s.freq, bdc=s.bdc, pay_lag=s.pay_lag, spot_lag=s.spot_lag)
        if isinstance(rate, str):  # 'par': strike at the par rate of THIS snapshot
            if rate.strip().lower() != "par":
                raise ConfigError(f"fixed_rate must be a number (PERCENT) or 'par', got {rate!r}", code="CFG-TERMS")
            with C.guard(p.reference_date):
                rate = lib.par_rate(lib.Swap(effective, maturity, notional, 0.0, **kw), p.market()) * 100.0  # <LIB>: decimal -> PERCENT
        contract = lib.Swap(effective, maturity, notional, float(rate) / 100.0, **kw)  # <LIB>: PERCENT -> its decimal
    return Built(LibSwap(contract=contract, direction=direction),
                 {"effective": C.to_date(contract.start), "maturity": C.to_date(contract.end), "fixed_rate": float(rate), "notional": notional})  # dt.date, never the library's strings


def _method(name: str, **extra: Any) -> Dict[str, Any]:
    return {"target": {"method": name}, "kwargs": {"ctx": "@ctx", **extra}}


SWAP_BIND: Dict[str, Any] = {  # EVERY required name of the schema: strict mode refuses a Kit that leaves one out (`pricebt` never resolves a name by a same-named method)
    "value": _method("value"),  # a Valuation: `sign`/`scale` cannot apply to it
    "dv01": {**_method("dv01", tenors=list(DEFAULT_TENORS)), "sign": RECEIVER_TO_PAYER},
    "gamma": {**_method("gamma", tenors=list(DEFAULT_TENORS)), "sign": RECEIVER_TO_PAYER},
    "rate": {**_method("rate"), "scale": 100.0},  # <LIB>'s decimals -> the schema's percent
    "delta_ladder": {**_method("delta_ladder", tenors=list(DEFAULT_TENORS)), "keys": list(DEFAULT_TENORS), "reduce": "dict_of_floats", "sign": RECEIVER_TO_PAYER},
    "carry": {**_method("carry"), "sign": RECEIVER_TO_PAYER},
    "roll": {**_method("roll"), "sign": RECEIVER_TO_PAYER},
    "delta": {**_method("delta"), "sign": RECEIVER_TO_PAYER},
    "convexity": {**_method("convexity"), "sign": RECEIVER_TO_PAYER},
}

swap = Kit(factory=swap_factory, asset_class="swap", default_bind=SWAP_BIND, cls=LibSwap, doc="USD SOFR-style overnight-indexed swap on <LIB>.")
```

## `__init__.py`: the exports and the `Stack`

```python
from pricebt.contracts.spec import Stack

from .conventions import USD_SOFR_OIS_CONVENTIONS, swap_conventions
from .swap import DEFAULT_TENORS, RECEIVER_TO_PAYER, SWAP_BIND, LibSwap, swap
from .wrap import LibPricer, wrap

STACK = Stack(
    name="lib", wrap=wrap,
    instruments={"usd_sofr_ois": {"factory": swap, "conventions": dict(USD_SOFR_OIS_CONVENTIONS)}},
    defaults={"swap:USD": "usd_sofr_ois"},
    accepts_gs={"floating_rate_option": ("USD-SOFR", "USD-SOFR-COMPOUND", "USD-SOFR-OIS", "SOFR")},  # gs values the shipped block's conventions already imply
)

__all__ = ["swap", "wrap", "STACK", "LibPricer", "LibSwap", "SWAP_BIND", "DEFAULT_TENORS", "RECEIVER_TO_PAYER", "USD_SOFR_OIS_CONVENTIONS", "swap_conventions"]
```

## What this skeleton deliberately leaves out (read `acme_adapter/swap.py` for each)

* Seasoned swaps: fixings, the cash swept in `[prev_ts, ts)`, `started`/`expired`, the analytic `dv01` before the start and the ladder sum after it (the tie-out passes at 1e-4 only if the
  definition equals the reference stack's).
* `extras` (`par_spread_bp`), maturity tenors in days, a ladder reducer for the library's shape (`register_reducer`), the four layers as functions of `(instrument, ctx)`, extension measures
  in `Kit.extra`. Each is in the worked example with its test.
* `Kit.extra` names must not collide with a schema name; see `references/facade-and-config.md`.
