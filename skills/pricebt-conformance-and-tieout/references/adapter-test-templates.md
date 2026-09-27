# Test template: the two proofs of an adapter in one file

Executed against the worked example (`acme_adapter`; every test of this file passed, about 5 s) with the run command

```powershell
$env:PYTHONPATH = "src;tests"        # the test file inserts your package's directory itself (executed this way); the CLI needs it on PYTHONPATH
python -m pytest tests/test_<yourlib>_proofs.py -q -o addopts= -p no:cacheprovider
```

Adapt: the import of your package AND the directory that contains it (`PKG_DIR`), `CONFIG` (your base and overlay), the conventions constant (`USD_SOFR_OIS_CONVENTIONS`), `ALLOW`, the marker, and the two size assertions of the last test (below).
The file lives in `tests/` (so `parents[1]` is the repository root). Under `tests/` EVERY test needs a partition marker (`core` for a library-free adapter, `adapter_<lib>` when the library must be installed):
an unmarked test stops the collection with `every test must carry one of the partition markers ...` (`tests/guards/partition.py`, see `pricebt-guards-and-packaging`).

**The `sys.path` block is not optional.** `pytest.ini` sets `pythonpath = . src tests`, which does not contain the directory of an adapter that lives elsewhere. Without the block the module-level `import acme_adapter` raises
`ModuleNotFoundError: No module named 'acme_adapter'` at COLLECTION (`Interrupted: 1 error during collection`), and a collection error stops every test of the run, not only this file (executed with `PYTHONPATH=src;tests` and no block; with the block, or with the directory on `PYTHONPATH`, the file passes).
`tests/test_skills_example_acme.py` does the same insert.

```python
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = str(ROOT / "skills/pricebt-wire-external-library/example")  # <- the directory that CONTAINS your adapter package
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

import acme_adapter as A  # <- your adapter package  # noqa: E402
from pricebt import api  # noqa: E402
from pricebt.contracts.schema import SchemaRegistry  # noqa: E402
from pricebt.contracts.spec import build_spec  # noqa: E402
from pricebt.testing.layer_conformance import Setup, flat_swap_world, run_kit  # noqa: E402
from pricebt.tieout import run_tieout  # noqa: E402

pytestmark = pytest.mark.core  # library-free; a real adapter: pytest.mark.adapter_<lib> and pytest.importorskip("<lib>")

CONFIG = ROOT / "skills/pricebt-wire-external-library/example/config"  # <- your base config and overlay directory
HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
CONV = {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}  # the calendar NAME of the world below
ALLOW = ("pricebt", "acme_adapter")  # what a base config's registry.allow gives the loader; required as soon as a default binding has a `function` target


def spec_with(bind=None):
    return build_spec("ois", {"factory": A.swap, "conventions": CONV, **({"bind": bind} if bind else {})}, schemas=SchemaRegistry.default(), allow=ALLOW)


def setup(spec=None, **kw):
    terms = {"side": "pay", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2}
    args = dict(spec=spec or spec_with(), wrap=A.wrap, terms=terms, mirror_terms={**terms, "side": "receive"}, world=flat_swap_world("cal", holidays=HOL),
                t0=dt.date(2024, 3, 4), t1=dt.date(2024, 3, 5))
    return Setup(**{**args, **kw})


def test_the_layers_conform():
    rep = run_kit(setup())
    rep.assert_ok()
    assert {r.check for r in rep.rows} >= {"fd_vs_delta_convexity", "mirror", "unexplained_share"}


def test_the_layers_conform_on_a_seasoned_swap_across_a_payment_date():
    terms = {"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}
    rep = run_kit(setup(terms=terms, mirror_terms={**terms, "side": "receive"}, birth=dt.date(2023, 5, 15), t0=dt.date(2024, 5, 20), t1=dt.date(2024, 5, 21),
                        payment_window=(dt.date(2024, 5, 21), dt.date(2024, 5, 23))))
    rep.assert_ok()
    assert any(r.check == "cash_sweep" and r.quantity.startswith("value drop") for r in rep.rows), "the cash sweep really ran (it is skipped without payment_window)"


def test_the_layers_conform_over_a_coarse_step():
    """t1 nine days after t0, more than the payment lag: a flow paid inside the interval can depend on a fixing published inside it, a path a one-day step never runs (for a service: the rebuilt market and its upload).
    The kit cannot tell an old-market `cash` from the right one (its flat world publishes the fixings the old curve implies): `cash_window_check.py` of `skills/pricebt-layers-and-ladder/references/kit-blind-spots.md` can."""
    terms = {"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}
    rep = run_kit(setup(terms=terms, mirror_terms={**terms, "side": "receive"}, birth=dt.date(2023, 5, 15), t0=dt.date(2024, 5, 20), t1=dt.date(2024, 5, 29),
                        payment_window=(dt.date(2024, 5, 21), dt.date(2024, 5, 23))))
    rep.assert_ok()
    assert any(r.check == "cash_sweep" for r in rep.rows), "the cash sweep really ran (it is skipped without payment_window)"


def test_the_kit_has_teeth_on_this_adapter():
    """Known answer first (the shipped block passes above), then one layer bound without its sign must fail."""
    bad = spec_with({"delta": {**A.SWAP_BIND["delta"], "sign": 1.0}})
    rep = run_kit(setup(bad))
    assert not rep.ok and "fd_vs_delta_convexity" in {r.check for r in rep.failures()}


BASE, REF_OVERLAY, MY_OVERLAY = CONFIG / "acme_tieout_base.yaml", ROOT / "configs/adapters/refstack_swap.yaml", CONFIG / "acme_swap.yaml"


@pytest.fixture(scope="module")
def tieout():
    # delta_ladder is NOT in the default audit measures: without it the ladder is not compared at all
    return run_tieout(BASE, {"reference": [REF_OVERLAY], "mine": [MY_OVERLAY]}, audit_measures=("dv01", "gamma", "rate", "delta_ladder"))


def test_the_stack_ties_out_with_the_shipped_tolerances(tieout):
    rep = tieout.reports["reference_vs_mine"]
    assert tieout.header["selftest"] == "passed" and rep.passed
    assert {r.level for r in rep.rows} == {"L0", "L1", "L2", "L3", "L4"}
    assert rep.header["n_positions"] >= 3 and rep.header["n_points"] >= 15, "it compared something: a tie-out over zero positions, or over tiny sizes below the floors, PASSES"  # <- adapt: the trades and days of YOUR base
    assert {r.status for r in rep.rows} <= {"exact", "noise"}, [(r.level, r.quantity, r.status) for r in rep.rows if r.status not in ("exact", "noise")]
    assert any(r.quantity.startswith("delta_ladder.") for r in rep.rows), "the ladder was compared"
    assert not rep.header["declared_tolerances"], "nothing declared: the shipped defaults passed (a real library: assert the declared set instead, see the Notes)"


def test_a_payment_falls_inside_the_window_so_the_cash_path_was_compared(tieout):
    marks = tieout.results["reference"].record.audit["marks"]
    assert (marks["cash"].abs() > 100).any(), "no cash flow in the window: L1.cash compared zeros (add a trade that pays or matures inside the grid)"


def test_every_declared_tolerance_carries_a_reason():
    """The loader accepts a bare number or a mapping without `reason` (`validate_declaration` only refuses an EMPTY reason that is present): the rule is policy, so this test is what enforces it."""
    declared = (api.load(BASE).get("tieout") or {}).get("tolerances") or {}
    assert all(isinstance(v, dict) and str(v.get("reason", "")).strip() for v in declared.values()), f"declared without a reason: {[k for k, v in declared.items() if not (isinstance(v, dict) and str(v.get('reason', '')).strip())]}"
```

Notes

* The key of `tieout.reports` is `<reference>_vs_<other>` (`reference_vs_mine`); `tieout.results[name]` is the `BacktestResult` of a stack; `tieout.builts[name]` its `Built`; `tieout.header["selftest"]` is `"passed"` or `"skipped"`.
* A real independent library will NOT be all `exact`/`noise` with the shipped tolerances: measure the noise, then declare each widened key with a `reason` (`tolerances.md`), and assert the declared set (`rep.header["declared_tolerances"]` is the sorted list of declared keys) instead of `not ... declared_tolerances`.
* The size assertions are tied to the BASE: `n_positions >= 3` and `n_points >= 15` fit the worked example's base (three trades, about a month of daily points) and the recipe base of `pricebt-run-config-and-reports`; a smaller base fails them, which is the intent. Set both from your own base.
  The cash assertion needs a trade that pays or matures inside the grid; a base without one (the recipe base without `pay_short`) fails it.
* Neither the loader nor the harness enforces the reason or the ledger entry (`tolerances.md` section 3); `test_every_declared_tolerance_carries_a_reason` is the enforcement, and the ledger row is checked in review.
* Pin the negative controls next to this file (`pricebt-debug-tieout-differences`, `skills/pricebt-debug-tieout-differences/references/regression-test-template.md`): a check that has never failed proves nothing.
