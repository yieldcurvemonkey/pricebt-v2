"""QuantLib OIS construction shared by the swap and the risk-curve builder (no market data, no pricebt contracts)."""
from __future__ import annotations

import datetime as dt
from typing import Sequence

from . import _compat as C
from .conventions import SwapConv

ql = C.ql

BDC = {
    "unadjusted": ql.Unadjusted, "following": ql.Following, "modified_following": ql.ModifiedFollowing, "preceding": ql.Preceding,
    "modified_preceding": ql.ModifiedPreceding,
}
FREQ = {"monthly": ql.Monthly, "quarterly": ql.Quarterly, "semiannual": ql.Semiannual, "annual": ql.Annual}


def adjusted_schedule(cal: "ql.Calendar", unadjusted: Sequence[dt.date], conv: SwapConv) -> "ql.Schedule":
    """The accrual schedule: every roll date adjusted by the convention (an explicit date list, so the roll day of the reference library is kept)."""
    bdc = BDC[conv.business_day_convention]
    return ql.Schedule([cal.adjust(C.qd(d), bdc) for d in unadjusted], cal, bdc)


def make_swap(cal: "ql.Calendar", index: "ql.OvernightIndex", conv: SwapConv, unadjusted: Sequence[dt.date], sign: int, notional: float, rate_percent: float) -> "ql.OvernightIndexedSwap":
    """Overnight-indexed swap, `sign` +1 = payer of fixed (holder-signed NPV: a payer gains when rates rise). `rate_percent` is PERCENT; QuantLib wants a decimal.

    ACT/360 fixed leg, daily-compounded overnight leg with payment delay, `telescopicValueDates=False` (the started-swap fixings are then read for every value date)."""
    typ = ql.Swap.Payer if sign > 0 else ql.Swap.Receiver
    return ql.OvernightIndexedSwap(
        typ, float(notional), adjusted_schedule(cal, unadjusted, conv), float(rate_percent) / 100.0, ql.Actual360(), index, 0.0, conv.payment_lag_days,
        BDC[conv.business_day_convention], cal, False,
    )


def engine(handle: "ql.YieldTermStructureHandle", eval_date: dt.date) -> "ql.DiscountingSwapEngine":
    """Discounting engine that INCLUDES flows paid on the evaluation date (`includeSettlementDateFlows=True`: a flow paid on D is inside V(D), swept as cash at D+1) and dates
    both the settlement and the NPV at the evaluation date explicitly (the default would be the curve's reference date)."""
    return ql.DiscountingSwapEngine(handle, True, C.qd(eval_date), C.qd(eval_date))
