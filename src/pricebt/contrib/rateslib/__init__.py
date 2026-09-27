"""rateslib adapter: `wrap(SnapshotPricer)`, the `swap` and `bond` kits, the shared conventions blocks and a `STACK` for the gs_quant-style facade. Needs the optional extra
`rateslib`.

    instruments:
      usd_sofr_ois: {factory: "pricebt.contrib.rateslib:swap", conventions: {...}}      # a Kit: factory + default bindings
      ust:          {factory: "pricebt.contrib.rateslib:bond", conventions: {...}}
    market: {pricers: {primary: {mdp: ..., wrap: "pricebt.contrib.rateslib:wrap"}}}

    PricebtSession.use(market=<provider>, stack="pricebt.contrib.rateslib:STACK")        # the gs_quant-style facade (IRSwap('Pay', '10y', 'USD'))

Importing this package registers the library's built-in NYC holidays as the named calendar `nyc` for sessions (`PricebtSession(calendar='nyc')`); the instruments themselves
always use the calendar of the market SNAPSHOT.
"""
from __future__ import annotations

from ...contracts.spec import Stack
from ...registries import CALENDARS
from . import _compat
from .bond import BOND_BIND, RLBond, bond
from .conventions import UST_CONVENTIONS, USD_SOFR_OIS_CONVENTIONS, BondConv, SwapConv
from .ladder import DEFAULT_TENORS, LadderModel
from .pricer import BondQuote, BondRef, RLCurvePricer
from .swap import SWAP_BIND, RLSwap, swap
from .wrap import wrap

CALENDARS.register("nyc", _compat.nyc_calendar)

STACK = Stack(
    name="rateslib", wrap=wrap,
    instruments={"usd_sofr_ois": {"factory": swap, "conventions": dict(USD_SOFR_OIS_CONVENTIONS)}, "ust": {"factory": bond, "conventions": dict(UST_CONVENTIONS)}},
    defaults={"swap:USD": "usd_sofr_ois", "bond:USD": "ust"},
    accepts_gs={"floating_rate_option": ("USD-SOFR", "USD-SOFR-COMPOUND", "USD-SOFR-OIS", "SOFR")},
)

__all__ = ["swap", "bond", "wrap", "STACK", "RLSwap", "RLBond", "RLCurvePricer", "LadderModel", "SWAP_BIND", "BOND_BIND", "DEFAULT_TENORS", "USD_SOFR_OIS_CONVENTIONS", "UST_CONVENTIONS",
           "SwapConv", "BondConv", "BondQuote", "BondRef"]
