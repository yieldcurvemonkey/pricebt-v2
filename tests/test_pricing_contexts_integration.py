"""End-to-end PricingContext/PricebtSession integration: a lazy (bucketed) value computed AFTER the
`with PricebtSession(...):` block has exited -- and after that session's own cache reset -- must
still resolve correctly, because the LazyFuture captures its own PricingService and (date, csa) at
construction rather than reading PricebtSession.current (IMPLEMENTATION_PLAN.md P2.2; DESIGN.md
section 8.2)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from pricebt.instrument import IRSwap
from pricebt.markets import HistoricalPricingContext, PricingContext
from pricebt.risk import IRDelta, Price
from pricebt.risk.results import DataFrameWithInfo, MultipleRiskMeasureResult
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
_BUCKET_COLS = ["mkt_type", "mkt_asset", "mkt_class", "mkt_point", "mkt_quoting_style", "value"]


def test_lazy_value_survives_session_reset_and_exit():
    d = date(2024, 3, 4)
    session = PricebtSession(
        assets=[ASSETS / "toy_usd_irs.yaml", ASSETS / "toy_eur_irs.yaml", ASSETS / "toy_usd_swaption.yaml"],
        fx=ASSETS / "toy_fx.yaml",
    )
    with session:
        swap = IRSwap("Pay", "10y", "USD", 1_000_000, name="s")
        with PricingContext(d):
            future = swap.calc(IRDelta)  # a LazyFuture: the thunk has not run yet
        session.pricing.reset()  # clears the caches the thunk will need to rebuild

    assert PricebtSession.current is not session  # the session block has exited
    df = future.result()  # first real evaluation happens here, with no session current at all
    assert list(df.columns) == _BUCKET_COLS
    assert len(df) > 0


def test_lazy_value_survives_a_second_unrelated_session_being_current():
    d = date(2024, 3, 4)
    first = PricebtSession(
        assets=[ASSETS / "toy_usd_irs.yaml", ASSETS / "toy_eur_irs.yaml", ASSETS / "toy_usd_swaption.yaml"],
        fx=ASSETS / "toy_fx.yaml",
    )
    with first:
        swap = IRSwap("Pay", "10y", "USD", 1_000_000, name="s")
        with PricingContext(d):
            future = swap.calc(IRDelta)

    second = PricebtSession(assets=[ASSETS / "toy_eur_irs.yaml"])
    with second:
        # `future` was built against `first`'s PricingService, captured at construction; evaluating
        # it now, with a DIFFERENT session current (one that does not even register toy_usd_irs),
        # must still use `first`'s service, not PricebtSession.current.
        df = future.result()
    assert list(df.columns) == _BUCKET_COLS
    assert len(df) > 0


# ------------------------------------------------------------ standalone Instrument.calc (gs instrument/core.py)


def test_calc_outside_an_entered_context_returns_values_never_a_lazy_future():
    """gs Pricing_Context tutorial: `PricingContext.current = PricingContext(d)` then `swap.calc(...)`
    returns the result itself (gs `future.result()` when not entered), bucketed measures included."""
    PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])
    swap = IRSwap("Pay", "10y", "USD", 1_000_000, name="s")
    PricingContext.current = PricingContext(date(2024, 3, 4))
    ladder = swap.calc(IRDelta)
    assert isinstance(ladder, DataFrameWithInfo) and list(ladder.columns) == _BUCKET_COLS
    both = swap.calc((Price, IRDelta))
    assert isinstance(both, MultipleRiskMeasureResult) and isinstance(both[IRDelta], DataFrameWithInfo)
    with PricingContext(date(2024, 3, 4)):
        entered = swap.calc((Price, IRDelta))
    assert isinstance(entered.result()[IRDelta], DataFrameWithInfo)  # entered: the MRMR holds values too
    pd.testing.assert_frame_equal(pd.DataFrame(entered.result()[IRDelta]), pd.DataFrame(ladder))


def test_calc_fn_sees_the_value_and_its_exception_waits_in_the_future():
    PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])
    swap = IRSwap("Pay", "10y", "USD", 1_000_000, name="s")

    def boom(_value):
        raise ArithmeticError("fn failed")

    with PricingContext(date(2024, 3, 4)):
        kinds = swap.calc((Price, IRDelta), fn=lambda mrmr: sorted(type(v).__name__ for v in mrmr.values()))
        failed = swap.calc(IRDelta, fn=boom)  # does not raise here (gs: `ret.set_exception`)
    assert kinds.result() == ["DataFrameWithInfo", "FloatWithInfo"]
    with pytest.raises(ArithmeticError, match="fn failed"):
        failed.result()
    with HistoricalPricingContext(dates=[date(2024, 3, 4), date(2024, 3, 5)]):
        dated = swap.calc(IRDelta, fn=lambda df: sorted(set(df.index)))
    assert dated.result() == [date(2024, 3, 4), date(2024, 3, 5)]
    PricingContext.current = PricingContext(date(2024, 3, 4))
    with pytest.raises(ArithmeticError, match="fn failed"):  # not entered: calc itself raises (gs `.result()`)
        swap.calc(IRDelta, fn=boom)
