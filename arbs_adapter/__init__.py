"""arbs_adapter: pricebt adapter for the maintainer's own pricing/data infrastructure, ARBS (spec Z5: a foreign stack that plugs in through config
alone, proving genericity -- this one is real, not a fictional worked example).

    market:      {mdps: {eod: {type: "arbs_adapter.provider:ArbsErisEodProvider", kwargs: {...}}}, pricers: {primary: {mdp: eod, wrap: "arbs_adapter:wrap"}}}
    instruments: {usd_sofr_ois: {asset_class: swap, factory: "arbs_adapter:swap", conventions: {...}}}
    registry:    {allow: [arbs_adapter]}     # dotted paths outside `pricebt` are imported only under an allowed prefix

Structure: `_compat` (the only module that touches ARBS's import path and modules, always lazily), `provider` (ARBS's own EOD curve as a
`CachedProvider`, reusing `tests/support/common.py`), `wrap` (snapshot -> ARBS's `RLIRSwapCurve`, memoised), `swap` (the instrument, the factory, the
default bindings, the layers -- ARBS's own value functions for `value`/`rate`/`dv01` unstarted, pricebt's rateslib layer/ladder plumbing for the rest).

ARBS is never imported here or at any other module's top level: every ARBS access is lazy, inside a function, in `_compat.py` only.
"""
from __future__ import annotations

from pricebt.contrib.rateslib import USD_SOFR_OIS_CONVENTIONS

from . import _compat
from .provider import ArbsErisEodProvider
from .swap import ARBS_SWAP_BIND, ArbsSwap, swap, swap_factory
from .wrap import ArbsCurvePricer, wrap

#: alias kept for the shape the wire-external-library playbook expects of every adapter's bindings block
SWAP_BIND = ARBS_SWAP_BIND

__all__ = [
    "swap", "wrap", "ArbsSwap", "ArbsCurvePricer", "ArbsErisEodProvider", "swap_factory", "ARBS_SWAP_BIND", "SWAP_BIND", "USD_SOFR_OIS_CONVENTIONS",
]
