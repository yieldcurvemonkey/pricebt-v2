"""QuantLib adapter: `wrap(SnapshotPricer)`, the `swap` and `bond` kits, the shared conventions vocabulary. Needs the optional extra `QuantLib`.

    instruments:
      usd_sofr_ois: {factory: "pricebt.contrib.quantlib:swap", conventions: {...}}      # a Kit: factory + default bindings
      ust:          {factory: "pricebt.contrib.quantlib:bond", conventions: {...}}
    market: {pricers: {primary: {mdp: ..., wrap: "pricebt.contrib.quantlib:wrap"}}}

    PricebtSession.use(market=<provider>, stack="pricebt.contrib.quantlib:STACK")        # the gs_quant-style facade (IRSwap('Pay', '10y', 'USD'))
"""
from __future__ import annotations

from ...contracts.spec import Stack
from . import _compat
from .bond import BOND_BIND, QLBond, bond
from .conventions import BOND_KEYS, SWAP_KEYS, UST_CONVENTIONS, USD_SOFR_OIS_CONVENTIONS
from .swap import DEFAULT_TENORS, SWAP_BIND, QLSwap, swap
from .wrap import QLPricer, wrap

STACK = Stack(
    name="quantlib", wrap=wrap,
    instruments={"usd_sofr_ois": {"factory": swap, "conventions": dict(USD_SOFR_OIS_CONVENTIONS)}, "ust": {"factory": bond, "conventions": dict(UST_CONVENTIONS)}},
    defaults={"swap:USD": "usd_sofr_ois", "bond:USD": "ust"},
    accepts_gs={"floating_rate_option": ("USD-SOFR", "USD-SOFR-COMPOUND", "USD-SOFR-OIS", "SOFR")},
)

__all__ = ["swap", "bond", "wrap", "STACK", "QLPricer", "QLSwap", "QLBond", "SWAP_BIND", "BOND_BIND", "DEFAULT_TENORS", "USD_SOFR_OIS_CONVENTIONS", "UST_CONVENTIONS", "SWAP_KEYS", "BOND_KEYS"]
