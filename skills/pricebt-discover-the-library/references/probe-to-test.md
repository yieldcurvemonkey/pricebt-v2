# From a probe to a permanent test

A probe that ran once is an anecdote. The same probe as a test is a tripwire: when the bank upgrades its library, or a colleague changes a curve builder, the convention that moved
fails ONE test with a name that says which fact broke, instead of moving every number of a backtest by a little.

Where the files go (paths relative to the repository root):

| file | what |
|---|---|
| `tests/support/<lib>_probe.py` | the `LIB` block of the probe skeleton (`market`, `trade`, `value`, `par`, `risk`, `setup`, with its `import <lib> as lib` line): the only place the tests name the library. `tests/support/` is a PACKAGE (`__init__.py`) and `tests/` is on the path (`pytest.ini`: `pythonpath = . src tests`), so the tests import it as `support.<lib>_probe`, as the repository's own tests import `support.common` |
| `tests/test_<lib>_facts.py` | one test per fact row (below) |
| `docs/design/11-<lib>-conventions.md` | the document that says WHY each fact holds (`conventions-doc-template.md`) |

The test file. It was executed against the fictional `acmelib` (with the skeleton of `probe-skeleton.md` as the support module); every test passed, and every mutant below made
its own test fail.

```python
"""The facts table of <LIB> as tests: a library upgrade that changes a convention fails ONE named test."""
import pytest

lib = pytest.importorskip("acmelib")                      # LIB: skips WITH A REASON when the library is not installed (the default run has no bank library)
from support.acme_probe import market, par, risk, setup, trade, value   # LIB: tests/support/<lib>_probe.py (a bare `from acme_probe import ...` fails: ModuleNotFoundError)

pytestmark = pytest.mark.adapter_acme                     # LIB: the partition marker of the library (pricebt-guards-and-packaging)


@pytest.fixture(autouse=True)
def _valuation_date():
    setup()                                               # any process-global the library needs, set before and restored after every test
    yield
    setup()


def test_control_a_swap_struck_at_par_is_worth_nothing():
    m = market()
    assert value(trade(par(trade(0.0), m)), m) == pytest.approx(0.0, abs=1e-4)


def test_fact_the_library_values_from_the_receivers_side():
    t = trade(0.04)
    assert value(t, market(+1.0)) < value(t, market()), "a rise in rates LOSES: the receiver's view; every default binding carries sign -1"


def test_fact_rates_are_decimals():
    assert 0.03 < par(trade(0.04), market()) < 0.05, "0.04 is a decimal: the `rate` binding carries scale 100"


def test_fact_risk_is_per_million_whatever_the_notional():
    assert risk(trade(0.04, 1e7), market()) == pytest.approx(risk(trade(0.04, 1e6), market()), rel=1e-12)


def test_fact_risk_is_the_fixed_leg_pv01_per_million():
    m, t = market(), trade(0.04)
    strike_bump = (value(trade(0.04 - 1e-4), m) - value(t, m)) * (1e6 / t.notional)
    assert risk(t, m) == pytest.approx(strike_bump, rel=1e-9)


def test_fact_its_risk_variable_is_the_par_rate_not_the_zero_rate():
    m, t = market(), trade(0.04)
    zero_fd = (value(t, market(+0.5)) - value(t, market(-0.5))) * (1e6 / t.notional)
    assert 0.9 < risk(t, m) / zero_fd < 0.99, "the two variables differ by a few percent: dv01 is par space, the zero-space variant is an extension measure"


def test_fact_a_valuation_without_the_global_date_raises_the_librarys_own_error():
    lib.set_valuation_date(None)
    with pytest.raises(lib.NoValuationDate):
        value(trade(0.04), market())
```

Run it (from the repository root; in the repository the `tests/` conftest also demands a partition marker, so register `adapter_<lib>` first: `pricebt-guards-and-packaging`). `pytest.ini` already puts `src` and `tests`
on the path, so the only entry you add is the directory that holds the library (here the worked example's):

```
PYTHONPATH="skills/pricebt-wire-external-library/example" python -m pytest tests/test_<lib>_facts.py -q -o addopts= -p no:cacheprovider
```

(PowerShell: `$env:PYTHONPATH = "skills/pricebt-wire-external-library/example"` first.) Result of the executed run, in a scratch copy of the repository layout (`pytest.ini`, `tests/support/__init__.py`, `tests/support/acme_probe.py`,
`tests/test_acme_facts.py`; the repository's `src` was added to `PYTHONPATH` by hand there, because the fictional `acmelib` imports pricebt's reference arithmetic and a scratch copy has no `src`): `7 passed`.

## Prove the tests can fail (mutate the library, not the test)

Each mutant patches ONE function of the library before the tests run (a pytest plugin loaded with `-p`); the test named in the second column must fail.

| mutant (what an upgrade could do) | must fail |
|---|---|
| `pv` returns the payer's value (sign flipped) | `test_fact_the_library_values_from_the_receivers_side` |
| `par_rate` returns percent (x 100) | `test_fact_rates_are_decimals` |
| `pv01` returns risk per unit as built (times notional / 1e6) | `test_fact_risk_is_per_million_whatever_the_notional` |
| `pv01` returns the zero-shift derivative instead of the par one | `test_fact_risk_is_the_fixed_leg_pv01_per_million` |

The executed mutant run: `7 passed` unmutated; with each mutant the test named above failed (the sign mutant also broke the two risk tests that compare values, the percent mutant also broke
the control, the per-unit mutant also the two risk tests): 3, 2, 3 and 2 failures. If a mutant survives, the fact is not pinned: tighten the assertion or add the probe that distinguishes it.

For the adapter's own code (not the library), `python tools/mutcheck.py <spec.json>` applies each textual mutation of the spec to ONE file, expects the named tests to fail and restores the file: the same idea as the plugin
above (`tests/support/mutcheck/` holds the shipped specs; `docs/DESIGN.md` section 14). It runs pytest with YOUR `PYTHONPATH` kept after `src`, so a library or adapter that is importable only through it is visible; before that fix every
`importorskip` test skipped and the mutants "survived" or the control meant nothing. Give `"runs": [[<pytest args>], ...]` instead of `"tests"` to make one pytest call per list, so a `-k` on one file does not filter the others.
The control run must be green (exit 2 otherwise), and the exit code is 0 only when every mutant is killed (`pricebt-guards-and-packaging`, step 7).

## Rules

* One fact, one test, named `test_fact_...`; the docstring or message says which binding or line of the adapter depends on it.
* Tests that need the library carry its partition marker and `pytest.importorskip`; they never run in the core-only job. Tests that need a live service or data are a further marker,
  opt-in (`pricebt-enterprise-platform-patterns`).
* A test asserts the FACT (a sign, a ratio, an exception class), never a market number: numbers move with the curve, facts move with the library.
* When a fact changes on purpose (an upgrade), change the test, the adapter and the conventions document in ONE commit, and say so in the commit message.
