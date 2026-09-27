"""Doubles for the tie-out tests (contains no tests): a toy base config, an identical second 'library' and deliberately different ones."""
from __future__ import annotations

import datetime as dt
from typing import Any, Mapping

from pricebt.contracts.spec import Built, Kit
from pricebt.snapshot import CalendarData, MarketSnapshot, SnapshotPricer
from pricebt.testing import refstack as R
from pricebt.testing.toys import FORWARD_BIND, TOY_SCHEMA, ToyForward, _ToyFactory


class _ShiftedStrike(_ToyFactory):
    """A 'library' that mis-prices: it builds the same forward with the strike moved by `shift` (a model difference the harness must locate)."""

    def __init__(self, shift: float):
        super().__init__(ToyForward)
        self.shift = shift

    def __call__(self, pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
        return super().__call__(pricer, ts, terms=terms, conventions={**conventions, "strike": conventions["strike"] + self.shift})


def shifted_forward(shift: float) -> Kit:
    return Kit(factory=_ShiftedStrike(shift), asset_class="toy", default_bind=FORWARD_BIND, cls=ToyForward, schema=TOY_SCHEMA, doc="a forward whose strike is off")


mutant_forward = shifted_forward(0.05)

BASE = {
    "name": "tieout_toy",
    "registry": {"allow": ["pricebt", "tieout_helpers"]},
    "backtest": {"tz": "America/New_York", "grid": {"start": "2024-01-02", "end": "2024-02-29", "freq": "1b"}, "progress": {"show": False}, "attribution": {"layers": ["carry", "delta"], "cadence": "eod"},
                 "measures": ["dv01"]},
    "market": {"mdps": {"rates": {"type": "toy"}}, "pricers": {"primary": {"mdp": "rates"}}},
    "instruments": {"fwd": {"asset_class": "toy", "factory": "pricebt.testing.toys:forward", "conventions": {"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 0.5}, "layers": ["carry", "delta"]}},
    "strategy": {"triggers": [{"type": "periodic", "frequency": "1w", "actions": [{"type": "add_trade", "priceables": "fwd", "trade_duration": "2w"}]}]},
}

SAME = {"instruments": {"fwd": {"factory": "pricebt.testing.toys:forward"}}}
DIFFERENT = {"instruments": {"fwd": {"factory": "tieout_helpers:mutant_forward"}}}


# ------------------------------------------------------------------ a mutated convention on ONE side (harness self-test X5b), on the library-free reference stack
class _WrongDayCount:
    """A 'library' that reads `act360` as `act365f`: the same shared conventions block, honoured wrongly."""

    def __call__(self, pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
        return R.swap_factory(pricer, ts, terms=terms, conventions={**conventions, "day_count": "act365f"})


wrong_day_count_swap = Kit(factory=_WrongDayCount(), asset_class="swap", default_bind=R.SWAP_BIND, cls=R.RefSwap, extra={"dv01_zero": "measure", "gamma_zero": "measure"})

EXTRA_HOLIDAY = dt.date(2024, 6, 5)  # the spot date of a trade struck on Monday 2024-06-03


def wrap_with_an_extra_holiday(sp: SnapshotPricer) -> Any:
    """A `wrap` that hands the library a calendar with one more holiday than the snapshot has: dates resolved on it shift."""
    snap = sp.snapshot
    cals = {n: CalendarData(c.name, (*c.holidays, EXTRA_HOLIDAY), c.weekmask) for n, c in snap.calendars.items()}
    return R.wrap(SnapshotPricer(MarketSnapshot(snap.ts, snap.reference_date, snap.curves, snap.fixings, snap.quotes, cals), ts=sp.ts))
