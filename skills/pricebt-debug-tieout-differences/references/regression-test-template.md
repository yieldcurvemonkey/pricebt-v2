# Pin the cause you found: a regression test at two levels

A fix that is not pinned comes back. Pin every cause twice: (1) at the LOWEST level where it is visible (a unit test that resolves or prices the same snapshot through your factory and through the reference factory), and (2) at the tie-out
(a negative-control overlay that reproduces the mistake and must fail at ONE located level and quantity, after showing that the correct wiring passes that very row). A check that has never failed proves nothing: each pin below includes its "teeth" test.

Executed against the worked example (3 tests passed, about 6 s) with the cause "the wrap loads one holiday too many" (`config/mistakes/acme_swap_wrong_calendar.yaml`, the code in `example/acme_mistakes.py`):

```powershell
$env:PYTHONPATH = "src;tests"        # the test file inserts your package's directory itself (executed this way)
python -m pytest tests/test_<yourlib>_pin_<cause>.py -q -o addopts= -p no:cacheprovider
```

Adapt: the adapter import and `PKG_DIR`, `SPEC` (your Kit and conventions block), the mistaken `wrap` (a function in a small module of your own that reproduces the mistake, or a negative-control overlay that is only a `bind` and needs no code),
the overlay paths, the expected `(level, quantity, status)` and the located text. The file lives in `tests/` (so `parents[1]` is the repository root) and, under `tests/`, every test needs a partition marker (`core` or `adapter_<lib>`).
The `sys.path` block above the imports is required: `pytest.ini` puts only `. src tests` on the path, so without it `import acme_adapter` fails COLLECTION (`ModuleNotFoundError: No module named 'acme_adapter'`, `Interrupted: 1 error during collection`) and no test of the run executes.

```python
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = str(ROOT / "skills/pricebt-wire-external-library/example")  # <- the directory that CONTAINS your adapter package (and your mistake module)
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

import acme_adapter as A  # <- your adapter package  # noqa: E402
import acme_mistakes as M  # <- the code of a negative control that needs code (a wrap that forgets one holiday); a control that is only a `bind` needs none  # noqa: E402
from pricebt.contracts.schema import SchemaRegistry  # noqa: E402
from pricebt.contracts.spec import TradeTemplate, build_spec  # noqa: E402
from pricebt.snapshot import SnapshotPricer  # noqa: E402
from pricebt.testing import refstack as R  # noqa: E402
from pricebt.testing.layer_conformance import flat_swap_world  # noqa: E402
from pricebt.tieout import run_tieout  # noqa: E402

pytestmark = pytest.mark.core

CONFIG = ROOT / "skills/pricebt-wire-external-library/example/config"
HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
SPEC = build_spec("ois", {"factory": A.swap, "conventions": {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}}, schemas=SchemaRegistry.default(), allow=("pricebt", "acme_adapter", "acme_mistakes"))


# ---- 1. the LOWEST level at which the cause is visible: the same snapshot through your factory and through the reference factory
def resolved(wrap):
    sp = SnapshotPricer(flat_swap_world("cal", holidays=HOL)(dt.date(2024, 5, 24), 0.0))  # a Friday; Monday 2024-05-27 is a holiday, so spot is 2024-05-29
    mine = TradeTemplate("t", SPEC, {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": 4.0}).build(wrap(sp), sp.ts)
    rp = R.wrap(sp)
    ref = R.swap_factory(rp, rp.ts, terms={"maturity": "10Y", "notional": 1e7, "fixed_rate": 4.0, "direction": 1}, conventions={**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"})
    return (mine.terms["effective"], mine.terms["maturity"]), (ref.terms["effective"], ref.terms["maturity"])


def test_the_effective_date_skips_the_snapshots_holidays_like_the_reference():
    got, want = resolved(A.wrap)
    assert got == want and got[0] == dt.date(2024, 5, 29)


def test_that_pin_has_teeth_a_wrap_with_one_holiday_too_many_fails_it():
    got, want = resolved(M.wrap_with_an_extra_holiday)
    assert got != want and got[0] == dt.date(2024, 5, 30), "the extra holiday moves spot by one business day"


# ---- 2. the same cause at the tie-out: the correct wiring passes the very row that the control fails
def row(rep, level, quantity):
    (r,) = [x for x in rep.rows if x.level == level and x.quantity == quantity]
    return r


@pytest.fixture(scope="module")
def tieout():
    stacks = {"reference": [ROOT / "configs/adapters/refstack_swap.yaml"], "mine": [CONFIG / "acme_swap.yaml"], "wrong_calendar": [CONFIG / "mistakes/acme_swap_wrong_calendar.yaml"]}
    return run_tieout(CONFIG / "acme_tieout_base.yaml", stacks, audit_measures=("dv01", "gamma", "rate", "delta_ladder"))


def test_the_control_fails_at_l0_and_the_correct_wiring_does_not(tieout):
    good, bad = tieout.reports["reference_vs_mine"], tieout.reports["reference_vs_wrong_calendar"]
    assert good.passed and row(good, "L0", "resolved_terms").status == "exact", "known answer first: the correct wiring passes this very row"
    r = row(bad, "L0", "resolved_terms")
    assert r.status == "input" and "effective: 2024-05-29 vs 2024-05-30" in r.where, (r.status, r.where)
    assert row(bad, "L0", "snapshot_digests").status == "exact" and row(bad, "L0", "conventions_digest").status == "exact", "the inputs are identical: only the wiring differs"
    assert row(bad, "L1", "pv").status == "input", "and the cascade below L0 is attributed to it"
```

## The pattern, in words

* **Known answer first.** The unit test asserts the value the reference gives AND a hand-derivable fact (`2024-05-29`: Friday 2024-05-24 plus two business days with Monday 2024-05-27 a holiday). The tie-out test asserts that the correct stack
  passes the same row (`good.passed`, `status == "exact"`).
* **One located failure.** A control fails at ONE level and quantity with a `where` that names the position, the timestamp or the field. `CONTROLS` of `tests/test_skills_example_acme.py` is the table form
  `{"no_percent": ("L2", "rate", "exceeds", "position="), "wrong_sign": ("L2", "dv01", "exceeds", "position="), "lower_key": ("L2", "delta_ladder.30Y", "structure", "position="), "wrong_calendar": ("L0", "resolved_terms", "input", "effective: 2024-05-29 vs 2024-05-30")}`
  driven by one parametrised test (`test_negative_control_the_harness_catches_the_mistake_at_the_expected_level_and_quantity`).
* **Only what the cause touches fails.** Assert that unrelated rows stay `exact` or `noise` (`test_the_forgotten_sign_flips_dv01_exactly_and_touches_only_what_reads_dv01`): a control that breaks everything localises nothing.
* **A run that must STOP is a `pytest.raises`, not a report:** `with pytest.raises(MarketDataUnavailable, match=r"1 overnight fixings missing between 2024-05-28 and 2024-06-19; first 2024-06-19"): run_stack(BASE, [MISTAKES / "acme_swap_no_holidays.yaml"], audit_measures=AUDIT)`
  (`from pricebt.errors import MarketDataUnavailable`, `from pricebt.tieout import run_stack`).
* **Mutation check your pins.** Apply the one-line fault to your adapter (drop a `sign`, a `scale`, a holiday) and confirm the pinning test fails: `python tools/mutcheck.py <spec.json>` with
  `{"file": "<your adapter file>", "tests": ["tests/test_<yourlib>_pin_<cause>.py"], "mutations": [{"name": "...", "find": "<text>", "replace": "<text>"}]}` applies each textual mutation, runs the named tests, expects them to FAIL,
  restores the file byte for byte and exits 0 only if every mutation was killed.
  * **Your `PYTHONPATH` is kept.** The child pytest gets `PYTHONPATH` = `src`, then yours (`tests/test_mutcheck_env.py`), so a library or an adapter that is importable only through your `PYTHONPATH` (every adapter outside
    `src/pricebt`, and a service client such as `zeta`) is visible to the mutated run: start `mutcheck.py` from the shell in which the tests pass. Without it every `importorskip` test skips, every mutant "survives"
    and the control means nothing (an older `mutcheck.py` replaced `PYTHONPATH` by `src`).
  * **`runs` for several pytest calls per mutant.** A spec may say `"runs": [["tests/test_<lib>_wrap.py", "-k", "wrap"], ["tests/test_<lib>_swap.py"]]` (a list of pytest argument lists) instead of `tests`: one pytest call each, so a `-k` on
    one file cannot filter the others (one `-k` over several files weakened seven mutants in the cold-start run). A mutant is killed when ANY run fails; the unmutated control needs ALL of them green.
* Record a widened tolerance, if the cause was a real noise floor, in `tasks/tolerance_ledger.yaml` (`pricebt-conformance-and-tieout`, `skills/pricebt-conformance-and-tieout/references/tolerances.md`).
