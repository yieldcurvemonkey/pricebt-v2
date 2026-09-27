"""End-to-end PricingContext/PricebtSession integration: a lazy (bucketed) value computed AFTER the
`with PricebtSession(...):` block has exited -- and after that session's own cache reset -- must
still resolve correctly, because the LazyFuture captures its own PricingService and (date, csa) at
construction rather than reading PricebtSession.current (IMPLEMENTATION_PLAN.md P2.2; DESIGN.md
section 8.2)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from pricebt.instrument import IRSwap
from pricebt.markets import PricingContext
from pricebt.risk import IRDelta
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
