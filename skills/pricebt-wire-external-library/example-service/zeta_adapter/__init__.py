"""zeta_adapter: the pricebt adapter for zeta, a rates-pricing SERVICE (ZETA_DOCS.md).

    instruments: {usd_sofr_ois: {asset_class: swap, factory: "zeta_adapter:swap", conventions: {...}}}     # a Kit: factory + default bindings
    market:      {pricers: {primary: {mdp: ..., wrap: "zeta_adapter:wrap"}}}
    registry:    {allow: [zeta_adapter]}     # in the BASE config: dotted paths outside `pricebt` are imported only under an allowed prefix

zeta does NOT serve market data (it prices only from a market you upload), so there is no provider here: the base config's provider (the library-free synthetic market, or a recorded store) feeds
every stack, and `wrap` uploads each snapshot to the service ONCE.

Structure = one adapter package: `_compat` (the only module that imports zeta: client, upload counter, error translation), `wrap` (snapshot -> zeta market, uploaded once), `conventions` (shared
vocabulary -> what zeta fixes), `swap` (the instrument, the factory, the default bindings, the ladder reducer, the layers), and the `STACK` the Python facade takes.
"""
from __future__ import annotations

from pricebt.contracts.spec import Stack

from . import _compat
from .conventions import SWAP_KEYS, USD_SOFR_OIS_CONVENTIONS, swap_conventions
from .swap import ZETA_TENORS, SWAP_BIND, ZetaSwap, ladder_to_tenor_dict, swap
from .wrap import ZetaPricer, wrap

STACK = Stack(
    name="zeta", wrap=wrap,
    instruments={"usd_sofr_ois": {"factory": swap, "conventions": dict(USD_SOFR_OIS_CONVENTIONS)}},
    defaults={"swap:USD": "usd_sofr_ois"},
    accepts_gs={"floating_rate_option": ("USD-SOFR", "USD-SOFR-COMPOUND", "USD-SOFR-OIS", "SOFR")},
)

__all__ = ["swap", "wrap", "STACK", "ZetaPricer", "ZetaSwap", "SWAP_BIND", "ZETA_TENORS", "USD_SOFR_OIS_CONVENTIONS", "SWAP_KEYS", "swap_conventions", "ladder_to_tenor_dict"]
