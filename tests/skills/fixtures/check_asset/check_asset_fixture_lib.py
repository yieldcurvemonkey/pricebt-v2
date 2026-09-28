"""Deliberately WRONG wrappers around tests/toylib/rates.py, for check_asset fixtures only.

Each function reproduces one realistic asset-config mistake. The module name is unique because the
tests put this directory on sys.path (via check_asset's --sys-path).
"""
from __future__ import annotations

import toylib.rates as tr


def resolve_unpinned(market, kwargs):
    """MISTAKE: keeps the raw tenor ('10y') as termination_date instead of pinning it to a date.
    A string is plain data, so pricebt accepts it -- and it is re-read on every pricing date."""
    resolved = tr.resolve_swap(market, kwargs)
    resolved["termination_date"] = kwargs["termination_date"]
    return resolved


def build_swap_repinning(market, resolved):
    """The consequence of resolve_unpinned, not a second mistake: a real library given a tenor
    pins it from the PRICING date, so the swap's maturity rolls forward every day."""
    eff = resolved["effective_date"]
    term = tr._pin_date(market.ref_date, resolved["termination_date"])
    return tr.ToySwap(eff, term, resolved["fixed_rate"], resolved["notional"])


def market_raising_on_weekend(d, ccy, csa):
    """MISTAKE: raises on a non-trading day instead of returning None."""
    if d.weekday() >= 5:
        raise ValueError(f"no curve for {d}: not a business day")
    return tr.market(d, ccy, csa)
