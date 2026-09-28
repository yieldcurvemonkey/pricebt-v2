"""Live ARBS run scaffold (IMPLEMENTATION_PLAN.md P5.2; DESIGN.md section 2.1's ARBS carve-out:
"ARBS may appear only in configs/assets/*.yaml, notebooks/, docs/, and tests marked live_arbs" --
this file is that fourth place). Every test here is marked `live_arbs` (module-level `pytestmark`,
a plain marker object -- not an execution) and is SKIPPED by `tests/conftest.py`'s
`pytest_runtest_setup` unless `PRICEBT_LIVE_ARBS=1`.

**Why this file must never import ARBS (or rateslib/MDP/etc.) at module level:** pytest IMPORTS a
test file at COLLECTION time, on every `pytest tests` run, before any per-test skip decision is
made -- the `live_arbs` skip in conftest.py runs at RUN time, per test, strictly AFTER this module
is already imported. A module-level `import rateslib` here would therefore run on every ordinary
(non-live) `pytest tests` invocation, in direct violation of the hard safety limit this task was
given. Every import below is a plain `pricebt.*` (or stdlib) import -- none of them touch ARBS.
ARBS is reached only lazily, inside `pricebt.assets.namespace.AssetNamespace.eval()`, the FIRST
time an expression on `usd_sofr_ois_interest_rate_swap` is actually evaluated -- which happens only
inside a test FUNCTION BODY below, and therefore only runs when the user opts in with
PRICEBT_LIVE_ARBS=1. See docs/v2/LIVE_ARBS_REPORT.md for how to run this file and what each check
verifies.

The 6 checks below are copied faithfully from IMPLEMENTATION_PLAN.md section 7, P5.2.
"""
from __future__ import annotations

import math
import os
import socket
import time
from datetime import date
from pathlib import Path

import pytest

from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import MeanReversionTrigger, MeanReversionTriggerRequirements
from pricebt.data import measure_series
from pricebt.datetime import business_day_offset
from pricebt.errors import AssetEvaluationError
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta, IRFwdRate, Price
from pricebt.session import PricebtSession

pytestmark = pytest.mark.live_arbs

ASSET_PATH = "configs/assets/usd_sofr_ois_interest_rate_swap.yaml"
ASSET_NAME = "usd_sofr_ois_interest_rate_swap"


def _session() -> PricebtSession:
    return PricebtSession.use(assets=[ASSET_PATH])


def _payer_10y(name: str = "payer_10y") -> IRSwap:
    return IRSwap(
        pay_or_receive="Pay",
        termination_date="10y",
        notional_currency="USD",
        notional_amount=1_000_000,
        fixed_rate="ATM",
        name=name,
    )


# --------------------------------------------------------------------------------------- check 1


def test_load_market_reference_date_and_the_five_no_market_cases():
    """P5.2 check 1: load_market on 2024-05-20..24 returns markets with reference_date == d; a
    weekend, a US holiday (2024-05-27, Memorial Day), Good Friday 2024-03-29, date.today() and
    2026-09-15 (beyond _LAST_SAFE) each return None without raising. `has_market`/`market` are
    pricebt's own public wrappers around the config's `market.expr` ('load_market(pricebt_date)'),
    so this exercises exactly the function the plan names without reaching into the config's raw
    namespace globals.
    """
    session = _session()
    asset = session.registry[ASSET_NAME]

    good_days = [date(2024, 5, 20), date(2024, 5, 21), date(2024, 5, 22), date(2024, 5, 23), date(2024, 5, 24)]
    for d in good_days:
        market = session.pricing.market(asset, d, None)
        assert market.reference_date().date() == d, f"served reference date != {d}"

    no_market_days = {
        "weekend (Saturday)": date(2024, 5, 25),
        "US holiday (Memorial Day)": date(2024, 5, 27),
        "Good Friday 2024": date(2024, 3, 29),
        "today": date.today(),
        "beyond _LAST_SAFE": date(2026, 9, 15),
    }
    for label, d in no_market_days.items():
        assert session.pricing.has_market(asset, d, None) is False, f"expected no market for {label} ({d})"


# --------------------------------------------------------------------------------------- check 2


def test_10y_atm_payer_on_2024_05_20():
    """P5.2 check 2: a 10y ATM payer on 2024-05-20: |npv| < 1 USD per 1mm; dv01 > 0 and in
    800-900 USD per 1mm; par_rate between 300 and 600 bp."""
    session = _session()
    d = date(2024, 5, 20)
    swap = _payer_10y()

    npv = session.pricing.value(swap, d, Price, None)
    dv01 = session.pricing.value(swap, d, IRDelta(aggregation_level="Type"), None)
    par_rate = session.pricing.value(swap, d, IRFwdRate, None)

    assert abs(float(npv)) < 1.0, f"ATM payer npv should be ~0, got {float(npv)}"
    assert 800.0 < float(dv01) < 900.0, f"dv01 {float(dv01)} outside the expected 800-900 USD/1mm band"
    assert 300.0 < float(par_rate) < 600.0, f"par_rate {float(par_rate)} outside the expected 300-600bp band"


# --------------------------------------------------------------------------------------- check 3


def test_seasoned_mark_on_2024_05_24_keeps_original_maturity_and_is_nonzero():
    """P5.2 check 3: the seasoned mark -- on 2024-05-24 the same trade (resolved on 2024-05-20) has
    npv != 0 and an unchanged maturity. The comparison against a swap resolved FRESH on 2024-05-24
    (a different termination_date, since a fresh '10y' tenor is measured from the later date) is
    what makes this able to fail: if resolution stopped pinning at trade date, the seasoned swap's
    maturity would silently drift to match the fresh one.

    The post-pricing maturity re-check calls `session.pricing.attribute(seasoned, "termination_date")`
    directly (same call `tests/test_pricing_service.py:342` uses) instead of indexing
    `seasoned.resolved_terms["termination_date"]` again. `attribute()` asset-registry-matches, then
    compiles/evals the config's `attributes: {termination_date: ...}` expression against a *fresh*
    `dict(resolved_terms)` copy, through its own `_attribute_cache` -- a genuinely different code path
    from the dict index. A same-object dict re-index after the value() call above is guaranteed to
    equal `maturity_at_trade` by Python reference semantics alone (nothing in the
    value()/_eval_unit_cached()/_trade_for() chain ever writes back to `inst.resolved_terms`, so
    re-reading the same field of the same object can never fail); going through `attribute()` instead
    exercises the actual code path a caller uses to read maturity, so a bug there (a stale/colliding
    `_attribute_cache` key, a broken injected `resolved` copy, a bad eval) can make this assertion
    fail. (Calling `session.pricing.attribute()` directly, rather than `seasoned.termination_date`,
    also avoids `Instrument.__getattr__`'s fallthrough to the plain `resolved_terms[field]` read when
    there is no current session or the asset can't be matched -- those silently degrade back to the
    tautology this replaces.)
    """
    session = _session()
    d0, d1 = date(2024, 5, 20), date(2024, 5, 24)

    seasoned = session.pricing.resolve(_payer_10y(), d0, None)
    maturity_at_trade = seasoned.resolved_terms["termination_date"]

    fresh_on_d1 = session.pricing.resolve(_payer_10y(), d1, None)
    maturity_if_traded_fresh_on_d1 = fresh_on_d1.resolved_terms["termination_date"]
    assert maturity_at_trade != maturity_if_traded_fresh_on_d1, (
        "test setup problem: a 10y swap traded 4 days apart should not land on the same maturity -- "
        "if it does, this check cannot tell 'seasoned' apart from 'fresh'"
    )

    npv_seasoned = session.pricing.value(seasoned, d1, Price, None)
    maturity_via_attribute = session.pricing.attribute(seasoned, "termination_date")
    assert maturity_via_attribute == maturity_at_trade, (
        "maturity drifted after re-pricing on a later date (checked via PricingService.attribute(), "
        "independent of the resolved_terms dict this test already read at trade time)"
    )
    assert float(npv_seasoned) != 0.0, "seasoned mark should be off the (now stale) trade-date ATM strike"


def test_swap_priced_on_maturity_and_maturity_plus_1b_gives_finite_npv():
    """P5.2 check 3b: a swap priced on its maturity date and on maturity+1b gives a finite npv. If
    ARBS errors there instead, fall back to the maturity-based guard the asset config's own
    alive()/unsettled() functions already encode: dv01 must be 0 once the swap is no longer alive.
    That fallback path accepts the final coupon (paid at maturity + 2b) as not independently
    verified by this test -- record that in LIVE_ARBS_REPORT.md's Results section if it triggers.

    Uses a 1y (not 10y) payer: a 10y swap resolved on 2024-05-20 matures around 2034, past
    `_LAST_SAFE` (2026-08-20) -- `market()` would raise MarketDataUnavailable before the pricer is
    ever reached, for a reason unrelated to what this check verifies. A 1y payer's ~2025-05
    maturity stays comfortably inside the config's supported window.
    """
    session = _session()
    asset = session.registry[ASSET_NAME]
    d0 = date(2024, 5, 20)
    short_swap = IRSwap(
        pay_or_receive="Pay", termination_date="1y", notional_currency="USD", notional_amount=1_000_000, fixed_rate="ATM", name="payer_1y"
    )
    seasoned = session.pricing.resolve(short_swap, d0, None)
    maturity = seasoned.resolved_terms["termination_date"]
    maturity_plus_1b = business_day_offset(maturity, 1)

    for d in (maturity, maturity_plus_1b):
        assert session.pricing.has_market(asset, d, None), (
            f"test setup problem: no market on {d} (weekend/holiday?) -- pricebt.datetime.business_day_offset "
            "is weekends-only (DEV-T3) and does not know US holidays, so a holiday landing on maturity+1b "
            "must be fixed by changing the tenor here, not silently absorbed by the fallback below"
        )
        try:
            npv = session.pricing.value(seasoned, d, Price, None)
        except AssetEvaluationError as exc:
            # a market exists on `d` (asserted above); this is ARBS's own pricer erroring AT/PAST
            # maturity, exactly the case check 3b's fallback is for -- not a missing-market bug.
            dv01 = session.pricing.value(seasoned, d, IRDelta(aggregation_level="Type"), None)
            assert float(dv01) == 0.0, (
                f"pricing at {d} raised ({exc!r}) AND the maturity-based fallback guard (dv01 == 0 "
                "once not alive) also failed -- check 3b's fallback assumes that guard still holds"
            )
            print(f"P5.2 check3b: npv pricing at {d} raised ({exc!r}); fell back to the dv01==0 guard; final coupon not independently verified")
            continue
        assert math.isfinite(float(npv)), f"non-finite npv pricing at {d}"


# --------------------------------------------------------------------------------------- check 4


def test_delta_ladder_for_payer_10y_sums_within_2pct_of_dv01():
    """P5.2 check 4: delta_ladder for [payer 10y] sums to within 2% of dv01."""
    session = _session()
    d = date(2024, 5, 20)
    swap = _payer_10y()

    dv01 = float(session.pricing.value(swap, d, IRDelta(aggregation_level="Type"), None))
    ladder = session.pricing.value(swap, d, IRDelta, None).result()
    ladder_sum = float(ladder["value"].sum())

    assert dv01 != 0.0, "test setup problem: dv01 is 0, so a 2% tolerance is meaningless"
    assert abs(ladder_sum - dv01) <= 0.02 * abs(dv01), f"ladder sum {ladder_sum} vs scalar dv01 {dv01}: diverges by more than 2%"


# --------------------------------------------------------------------------------------- check 5


def test_short_040304_run_completes_and_reports_runtime():
    """P5.2 check 5: a short 040304 run (2024-01-02..2024-06-28, windows 30) completes. The ledger
    is non-empty, or LIVE_ARBS_REPORT.md's Results section explains why nothing fired -- that "or"
    is a human documentation step this test cannot encode as a single assertion, so this test only
    requires the run to complete and, if trades exist, for the ledger to have the expected shape.
    Runtime is printed (rerun with `-s` to see it) so it can be recorded in the report.
    """
    _session()
    start_date, end_date = date(2024, 1, 2), date(2024, 6, 28)
    swap = IRSwap(
        pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1e4, fixed_rate="ATM", name="swap_10y"
    )

    t0 = time.perf_counter()
    s = measure_series(IRSwap(termination_date="10y", notional_currency="USD"), "par_rate", start_date, end_date, frequency="1b")
    action = AddTradeAction(swap)
    data_source = GenericDataSource(s, MissingDataStrategy.fill_forward)
    trig_req = MeanReversionTriggerRequirements(data_source, 2, 30, 30)
    trigger = MeanReversionTrigger(trig_req, action)
    strategy = Strategy(None, trigger)
    backtest = GenericEngine().run_backtest(strategy, start=start_date, end=end_date, frequency="1b", show_progress=False)
    runtime_s = time.perf_counter() - t0

    ledger = backtest.trade_ledger()
    print(f"P5.2 check5: runtime_seconds={runtime_s:.2f} ledger_rows={len(ledger)}")
    if not ledger.empty:
        assert list(ledger.columns) == ["Open", "Close", "Open Value", "Close Value", "Long Short", "Status", "Trade PnL"]
    # else: nothing fired over this window. Allowed by the plan, but LIVE_ARBS_REPORT.md's Results
    # section must then say why (e.g. the 10y par rate never strayed 2 std devs from its own 30d
    # rolling mean over this particular 6-month window) before this check counts as done.


# --------------------------------------------------------------------------------------- check 6


def _fixings_cache_dir() -> Path:
    return (
        Path(os.environ["LOCALAPPDATA"])
        / "ARBS" / "MDP" / "IRSwaps" / "ARBS" / "MDP" / "IRSwaps" / "Cache" / "fixings_cache" / "USD-SOFR-1D_fixings"
    )


def _curve_store_root() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "ARBS" / "Cache" / "curve_store" / "raw"


def _curve_store_asset_dirs() -> list[Path]:
    # Every ARBS write this config's code path (or the commented-out NOJUMPS line right next to it
    # in the yaml) could plausibly make lands in one of these two CurveStore asset partitions:
    #   - USD-SOFR-1D-RLBASIC: what our config actually reads (IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC"),
    #     docs/v2/research/06-arbs-pricing-api.md line 43) and what `_promote_eris_curve_store_day`
    #     writes on a store-miss-then-network-fetch (line 343).
    #   - USD-SOFR-1D: the shared asset `_write_curve_store_products`/`store.write_day(..., overwrite=True)`
    #     clobbers on the NOJUMPS network path (line 342; also named directly in the yaml's own comment
    #     and in DESIGN.md decision 0.2).
    # Both are network-gated writes, and the socket block below already turns any network attempt into
    # a loud AssertionError -- so this check is redundant-by-design with that block, not a substitute
    # for it, exactly like DESIGN.md decision 0.2 / the verifier's finding describe.
    #
    # ponytail: scoped to these two asset dirs rather than the whole `curve_store/raw` tree -- measured
    # on this machine, a full recursive listing of `raw` (~20 unrelated currencies/venues) did not finish
    # in 120s, while these two dirs together take ~9s (2,846 + 4,142 entries). No write path reachable
    # from this config's `source="ERIS_EOD_LIVE-RL_BASIC"` touches any other asset's partition. Widen the
    # scope (or snapshot `raw` itself) if a future asset config adds another CurveStore source.
    base = _curve_store_root()
    return [base / "asset=USD-SOFR-1D-RLBASIC", base / "asset=USD-SOFR-1D"]


def _snapshot(cache_dir: Path) -> list[tuple[str, int, int]] | None:
    """Sorted (relpath, mtime_ns, size) for every entry under cache_dir, or None if it doesn't exist.

    Recording the stat, not just the relative path, matters: a listing of names alone would miss an
    in-place overwrite (same filename, rewritten content -- e.g. `write_day(..., overwrite=True)`,
    which unlinks and rewrites the same parquet path) and would miss a deletion (present before,
    gone after -- e.g. `_cleanup_old_cache_dirs` deleting old fixings CSVs), because both leave the
    *set of names* looking unchanged or only show up as a name disappearing that a same-length
    before/after listing could plausibly skip past at a glance.
    """
    if not cache_dir.exists():
        return None
    entries = []
    for p in cache_dir.rglob("*"):
        st = p.stat()
        entries.append((str(p.relative_to(cache_dir)), st.st_mtime_ns, st.st_size))
    return sorted(entries)


def test_no_network_load_market_and_fixings_cache_unchanged(monkeypatch):
    """P5.2 check 6: monkeypatch socket.socket to raise; call load_market(date(2026, 9, 15))
    (-> None) and load_market(date(2024, 5, 20)) (-> a market), with no exception. Record BOTH a
    listing of the fixings-cache folder AND a listing of the curve-store asset partitions this
    config's read path (or its commented-out NOJUMPS sibling) could write to, before and after --
    separate, disjoint directories (see `_curve_store_asset_dirs` / `_fixings_cache_dir`). The only
    allowed change anywhere is an empty today-dated folder under the fixings cache; the curve store
    must show ZERO change at all (DESIGN.md decision 0.2: this source has "no curve-store writes" --
    unlike the fixings cache, there is no exception for it here).

    Also stats `curve_store/raw` itself (one `Path.stat()` call, not a listing): the two asset-dir
    snapshots above are scoped to the only two partitions this source's read path could plausibly
    write (see `_curve_store_asset_dirs`), so a brand-new *sibling* partition directory created
    directly under `raw` wouldn't show up in either snapshot -- but creating any new directory entry
    updates its parent directory's own mtime, so a before/after stat of `raw` itself catches that
    case for free.

    `_session()` (which constructs a fresh PricebtSession/AssetNamespace, and therefore a fresh
    `IRSwapsMDP(...)`) is called AFTER the socket patch below, so this is stricter than the plan's
    literal wording (which only names `load_market`): it also requires the ARBS client's own
    construction to need no network. Per docs/v2/research/06-arbs-pricing-api.md section 1.2, for an
    Eris source the constructor only opens (and, if absent, creates) a local diskcache directory --
    no network call -- so `_session()` here is expected to succeed under the socket block; if that
    ever stops holding, this call raises loudly (AssertionError from `_blocked_socket`) rather than
    silently passing.
    """
    fixings_dir = _fixings_cache_dir()
    store_dirs = _curve_store_asset_dirs()
    store_root = _curve_store_root()
    fixings_before = _snapshot(fixings_dir)
    store_before = [_snapshot(d) for d in store_dirs]
    store_root_mtime_before = store_root.stat().st_mtime_ns if store_root.exists() else None

    def _blocked_socket(*args, **kwargs):
        raise AssertionError("socket.socket() was called: load_market must not touch the network for a date <= _LAST_SAFE")

    monkeypatch.setattr(socket, "socket", _blocked_socket)

    session = _session()
    asset = session.registry[ASSET_NAME]

    assert session.pricing.has_market(asset, date(2026, 9, 15), None) is False
    market = session.pricing.market(asset, date(2024, 5, 20), None)
    assert market is not None

    fixings_after = _snapshot(fixings_dir)
    before_set = set(fixings_before or [])
    after_set = set(fixings_after or [])
    removed = before_set - after_set
    added = after_set - before_set
    assert not removed, f"fixings-cache entries disappeared or were overwritten (mtime/size changed): {sorted(removed)}"
    today_name = date.today().isoformat()
    # Exactly one new entry, and it is the empty today-dated folder itself -- if anything were written
    # INSIDE that folder, it would show up here as a second, distinct relpath ("<today>/something").
    assert len(added) <= 1 and all(rel == today_name for rel, _, _ in added), f"unexpected new/changed fixings-cache entries: {sorted(added)}"

    for store_dir, before in zip(store_dirs, store_before):
        after = _snapshot(store_dir)
        assert after == before, (
            f"curve-store partition {store_dir} changed -- this source must never write the curve store: "
            f"before={before} after={after}"
        )

    store_root_mtime_after = store_root.stat().st_mtime_ns if store_root.exists() else None
    assert store_root_mtime_after == store_root_mtime_before, (
        f"curve_store/raw's own mtime changed ({store_root_mtime_before} -> {store_root_mtime_after}) -- "
        "a new sibling asset partition may have appeared directly under raw that the two scoped "
        "snapshots above wouldn't see"
    )
