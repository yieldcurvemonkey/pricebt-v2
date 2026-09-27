"""The deliberately WRONG pieces the negative controls use (tests/test_skills_example_zeta_proofs.py). Never import this from the adapter.

Four of the mistakes are one-line configuration errors (`zeta_adapter/config/mistakes/*.yaml`: a binding that forgot a `scale` or the ladder's `sign`, a layer with a sign it must not have). The
ones below need code, so they live here and are named by dotted path in the overlays; the BASE config's `registry.allow` lists this module for that reason.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable

import zeta_adapter as A
from pricebt.snapshot import CalendarData, MarketSnapshot, SnapshotPricer
from zeta_adapter import _compat as C

EXTRA_HOLIDAY = dt.date(2024, 5, 28)  # the day after Memorial Day: with it the spot date of a trade struck on Friday 2024-05-24 is 2024-05-30 instead of 2024-05-29


def _wrap_with_holidays(sp: SnapshotPricer, holidays: Callable[[tuple], tuple]) -> Any:
    snap = sp.snapshot
    cals = {n: CalendarData(c.name, holidays(c.holidays), c.weekmask) for n, c in snap.calendars.items()}
    return A.wrap(SnapshotPricer(MarketSnapshot(snap.ts, snap.reference_date, snap.curves, snap.fixings, snap.quotes, cals), ts=sp.ts))


def wrap_with_an_extra_holiday(sp: SnapshotPricer) -> Any:
    """A `wrap` that loads the snapshot's holidays PLUS one of another market's: dates resolved on it shift, and no fixing is demanded that the market does not publish, so the run completes
    and the tie-out reports the difference (`L0.resolved_terms`)."""
    return _wrap_with_holidays(sp, lambda hols: (*hols, EXTRA_HOLIDAY))


def wrap_without_holidays(sp: SnapshotPricer) -> Any:
    """A `wrap` that loads the WEEKENDS and none of the holidays (the classic: a service's default calendar). A holiday inside a started swap's fixing window is then a business day that has
    no published fixing, and zeta answers Z530: the run STOPS with `MarketDataUnavailable` instead of pricing on invented data."""
    return _wrap_with_holidays(sp, lambda hols: ())


class _FixingsInPercent(A.ZetaPricer):
    """The classic unit mistake: the snapshot's fixings are PERCENT, zeta wants BASIS POINTS, and the wrap forgot the factor 100 (a 4.0 fixing is a 0.04 percent fixing to zeta)."""

    def __init__(self, source: SnapshotPricer):
        super().__init__(source)
        market = dict(self.need_market())
        market["fixings"] = {"SOFR": {d: v / 100.0 for d, v in market["fixings"]["SOFR"].items()}}
        object.__setattr__(self, "_market", market)


def wrap_fixings_in_percent(sp: SnapshotPricer) -> Any:
    return _FixingsInPercent(sp)


class _Chatty(A.ZetaPricer):
    """The classic service mistake: the market is uploaded again on EVERY request (no memo on the market id), 120 ms of simulated service time each. `client.stats["markets_uploaded"]` shows it."""

    def market_id(self) -> str:
        return C.upload(self._client, self.need_market(), self.ts, "snapshot")


def wrap_reuploading(sp: SnapshotPricer) -> Any:
    return _Chatty(sp)


def dv01_ladder_sum_in_every_state(swap: Any, ctx: Any) -> float:
    """A dv01 with ONE definition, the WRONG one: the ladder sum before the swap has started as well. At par it is the annuity to 1e-7, off par it differs by percents, and the reference answers the
    annuity (pricebt-layers-and-ladder, rule 5 without a declaration). Same unit as `ZetaSwap.dv01` (currency per +10bp), so the binding keeps `scale: 0.1`."""
    r = swap._res(ctx.pricer)["measures"]
    return 0.0 if r["PAR_BP"] is None else -float(sum(x["risk"] for x in r["LADDER_10BP"]))
