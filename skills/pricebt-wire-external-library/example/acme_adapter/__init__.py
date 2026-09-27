"""acme_adapter: the pricebt adapter for acmelib (the worked example of wiring an external library into pricebt).

    instruments: {usd_sofr_ois: {asset_class: swap, factory: "acme_adapter:swap", conventions: {...}}}     # a Kit: factory + default bindings
    market:      {pricers: {primary: {mdp: ..., wrap: "acme_adapter:wrap"}}}
    registry:    {allow: [acme_adapter]}     # in the BASE config: dotted paths outside `pricebt` are imported only under an allowed prefix

Structure = one adapter package: `_compat` (the only module that touches acmelib's globals and exceptions), `wrap` (snapshot -> acmelib market), `conventions` (shared
vocabulary -> acmelib codes), `swap` (the instrument, the factory, the default bindings, the layers), and the `STACK` the Python facade takes.
"""
from __future__ import annotations

from pricebt.contracts.spec import Stack

from . import _compat
from .conventions import SWAP_KEYS, USD_SOFR_OIS_CONVENTIONS, swap_conventions
from .swap import DEFAULT_TENORS, RECEIVER_TO_PAYER, SWAP_BIND, AcmeSwap, ladder_to_tenor_dict, swap
from .wrap import AcmePricer, wrap

STACK = Stack(
    name="acme", wrap=wrap,
    instruments={"usd_sofr_ois": {"factory": swap, "conventions": dict(USD_SOFR_OIS_CONVENTIONS)}},
    defaults={"swap:USD": "usd_sofr_ois"},
    accepts_gs={"floating_rate_option": ("USD-SOFR", "USD-SOFR-COMPOUND", "USD-SOFR-OIS", "SOFR")},
)

__all__ = ["swap", "wrap", "STACK", "AcmePricer", "AcmeSwap", "SWAP_BIND", "DEFAULT_TENORS", "RECEIVER_TO_PAYER", "USD_SOFR_OIS_CONVENTIONS", "SWAP_KEYS", "swap_conventions",
           "ladder_to_tenor_dict"]
