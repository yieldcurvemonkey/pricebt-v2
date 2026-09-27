# Probe skeleton: hypothesis, probe, number, test

One file per topic, run from the repository root. Copy the block, change ONLY the lines marked `LIB` (they name your library; nothing else in the file may), run it. The block below
is the exact text that was executed against the fictional example library `acmelib` (`skills/pricebt-wire-external-library/example/acmelib/`); its output is printed under it.

Rules the skeleton enforces (each one is a mistake this project made and recorded in `docs/design/11-quantlib-conventions.md`, section 1):

1. **The control runs first.** It is an input whose answer you already know without the library (a swap struck at its own par is worth 0). If the control fails, the probe is wrong,
   not the library: stop and fix the probe. A measuring tool that is itself wrong reports success and hides the thing it was built to find.
2. **One hypothesis per probe**, written as a sentence before the number, so a surprising number is visible as a surprise.
3. **Every measurement prints its number**, with the expected value when there is one. A probe that only asserts hides how close it was.
4. **A probe that never failed has not been shown to work.** After it passes, break one thing on purpose (flip a sign in the probe, use the wrong unit) and see it fail.

```python
"""probe_units_signs.py: hypothesis -> probe -> number.   Run from the repository root, bash: PYTHONPATH="src;skills/pricebt-wire-external-library/example" python probe_units_signs.py
PowerShell: $env:PYTHONPATH = "src;skills/pricebt-wire-external-library/example"; python probe_units_signs.py   (Linux separator: ':')"""
import sys

# ---- LIB: the only lines that name your library ------------------------------------------------------------------------------------------------------------------
import acmelib as lib                                                                                                                                # LIB
ANCHOR = "2024-06-14"                                                                                                                               # LIB the date the market is anchored at
def market(bp=0.0):                                                                                                                                 # LIB a FLAT 4% curve, shifted by bp basis points, no fixings
    lib.register_calendar("PROBE", ["2024-07-04"], replace=True)
    return lib.Market(lib.ZeroCurve(ANCHOR, ["2024-09-16", "2025-06-16", "2034-06-14"], [0.04 + bp * 1e-4] * 3))
def trade(fixed=0.04, notional=1e7):                                                                                                                # LIB a 5y swap starting after the anchor
    return lib.Swap("2024-06-18", "5y", notional, fixed, calendar="PROBE")
def value(t, m): return lib.pv(t, m)                                                                                                                # LIB value of the trade
def par(t, m): return lib.par_rate(t, m)                                                                                                            # LIB its market (par) rate
def risk(t, m): return lib.pv01(t, m)                                                                                                               # LIB the library's own risk number
def setup(): lib.set_valuation_date(ANCHOR)                                                                                                         # LIB any process-global the library needs
# -------------------------------------------------------------------------------------------------------------------------------------------------------------------

ROWS = []


def fact(topic, hypothesis, got, expected=None, tol=0.0, note=""):
    ok = True if expected is None else abs(got - expected) <= tol
    ROWS.append((topic, hypothesis, got, expected, ok, note))
    print(f"{'ok ' if ok else 'BAD'} {topic:<14} {hypothesis:<58} got {got:>16.10g}" + ("" if expected is None else f"  expected {expected:.10g}") + (f"   {note}" if note else ""))


def control():
    """KNOWN ANSWER: a swap struck at its own par rate is worth zero. If this fails, nothing below means anything."""
    setup()
    m = market()
    p = par(trade(0.0), m)
    fact("control", "a swap struck at its own par is worth 0", value(trade(p), m), 0.0, tol=1e-4)
    fact("control", "a 10x notional is worth 10x (off par)", value(trade(0.05, 1e8), m) / value(trade(0.05, 1e7), m), 10.0, tol=1e-9)


def probes():
    m0, mu = market(), market(+1.0)
    t = trade(0.04)
    d = value(t, mu) - value(t, m0)
    fact("sign", "H1 the library's swap gains when rates rise (a fixed PAYER)", 1.0 if d > 0 else -1.0, note="+1: payer's view, matches pricebt; -1: receiver's view, a `sign: -1` in every default binding")
    p = par(t, m0)
    fact("rate unit", "H2 par rate is a decimal (0.04), not percent (4.0)", p, 0.0403, tol=0.002, note="a decimal needs `scale: 100` on the `rate` binding")
    r1, r2 = risk(trade(0.04, 1e6), m0), risk(trade(0.04, 1e7), m0)
    fact("risk basis", "H3 risk does NOT scale with the trade's notional", r2 / r1, 1.0, tol=1e-9, note="1.0: per fixed notional (multiply by notional/basis in code); 10.0: per unit as built")
    k_bump = (value(trade(0.04 - 1e-4), m0) - value(t, m0)) * (1e6 / t.notional)  # lowering the STRIKE by 1bp changes the value by exactly the fixed leg's PV01: a known answer
    fact("risk unit", "H4 risk = value change for -1bp of the strike (per 1mm)", risk(t, m0) / k_bump, 1.0, tol=1e-9, note="100 or 1e4: a unit (percent, decimal); -1: a sign; else: not the fixed-leg PV01")
    zero_fd = (value(t, market(+0.5)) - value(t, market(-0.5))) * (1e6 / t.notional)
    fact("risk variable", "H4b risk equals a parallel move of the ZERO curve", risk(t, m0) / zero_fd, note="not 1: the risk variable is the par rate (annuity); a zero-curve move is another variable")
    lib.set_valuation_date(None)
    try:
        value(t, m0)
        fact("global state", "H5 a valuation without the process-global date fails", 0.0, note="NOT raised: the library has another way to know today; find it")
    except Exception as e:  # noqa: BLE001
        fact("global state", "H5 a valuation without the process-global date fails", 1.0, note=f"raises {type(e).__module__.split('.')[0]}.{type(e).__name__}: map it to a pricebt error")
    finally:
        setup()


if __name__ == "__main__":
    control()
    if not all(r[4] for r in ROWS):
        sys.exit("the control failed: fix the probe before believing any number")
    print("control ok")
    probes()
```

Output of the block above (real, printed by the run; the numbers of H2 and the control are round-off dependent, the rest are facts):

```
ok  control        a swap struck at its own par is worth 0                    got -5.979217121e-10  expected 0
ok  control        a 10x notional is worth 10x (off par)                      got               10  expected 10
control ok
ok  sign           H1 the library's swap gains when rates rise (a fixed PAYER) got               -1   +1: payer's view, matches pricebt; -1: receiver's view, a `sign: -1` in every default binding
ok  rate unit      H2 par rate is a decimal (0.04), not percent (4.0)         got     0.0402521707  expected 0.0403   a decimal needs `scale: 100` on the `rate` binding
ok  risk basis     H3 risk does NOT scale with the trade's notional           got                1  expected 1   1.0: per fixed notional (multiply by notional/basis in code); 10.0: per unit as built
ok  risk unit      H4 risk = value change for -1bp of the strike (per 1mm)    got                1  expected 1   100 or 1e4: a unit (percent, decimal); -1: a sign; else: not the fixed-leg PV01
ok  risk variable  H4b risk equals a parallel move of the ZERO curve          got     0.9748163277   not 1: the risk variable is the par rate (annuity); a zero-curve move is another variable
ok  global state   H5 a valuation without the process-global date fails       got                1   raises acmelib.NoValuationDate: map it to a pricebt error
```

Read the rows as FACTS for the facts table, not as pass/fail (a row with no `expected` is always `ok`). Only the CONTROL is a gate: if it fails the script exits non-zero (`the control failed: fix the probe before
believing any number`); after it, a `BAD` row (a hypothesis that did not hold) still exits 0, so a wrapper that watches the exit code cannot see it: read the rows, and let the permanent tests (`probe-to-test.md`) assert them. For acmelib: H1 `-1` is the fact "acmelib values from the receiver's side"
(consequence: `sign: -1` on every default binding, and `value` converts in code); H3 `1` is "risk is per one million" (consequence: multiply by `notional / 1e6` in code);
H4b `0.975` is "its risk variable is the par rate, not the zero rate" (consequence: the `dv01` binding may not use a zero-shift finite difference; an extension measure carries
the zero-rate variant, as `acme_zero_dv01` does in `Kit.extra`).

**The probe found something the first draft did not expect.** The first draft of H4 compared the library's risk with a parallel bump of the zero curve and printed `0.9748`, not `1`.
That was not a library bug: it is the difference between two risk variables (par-rate space and zero-rate space: 2.5 percent on this 5Y swap; `docs/design/11-quantlib-conventions.md`
section 11 measured 2.6 percent on a 10Y for QuantLib). The known answer that pins the unit is the strike bump of H4 (lowering the fixed rate by 1bp changes the value by exactly the
fixed leg's PV01), and H4b keeps the zero-space ratio as a recorded fact. That is the method: a surprising number is a hypothesis you had wrong, and the fix is a better known answer.

**Showing the probe can fail** (each mutant of the block was run and printed a `BAD` row or `the control failed`): the control struck at `par + 10bp` fails the control; the strike bumped
UP instead of down gives `-1` on H4 (`BAD risk unit`, exit 0: see above); the `1e4` basis instead of `1e6` gives `100` on H4.

## Turning a probe into a permanent test

Each `fact` row becomes one test that asserts the FACT you found, so a library upgrade that changes a convention fails a named test instead of silently moving every number
(`references/probe-to-test.md`).

## What to probe next (each with a known answer)

| topic | control (known answer) | measurement |
|---|---|---|
| dates | a business day plus 0 days is itself; a weekend plus 1 business day is Monday | spot date, tenor addition and month-end rules over 1000 dates against `pricebt.timeutil.Calendar` |
| calendar horizon | a date inside the data is a holiday if you added it | the last date the holiday data covers; what the library does AFTER it (a silent business day is the trap) |
| schedule | a 1Y annual swap has exactly two dates | stub side, roll day, adjusted vs unadjusted, both legs |
| fixings | a swap struck today needs none | which fixing dates it asks for, the unit, what a missing one raises |
| dv01 | the annuity of a par swap against the ladder sum agree to about 1e-4 | started swaps: annuity vs ladder sum (`pricebt-layers-and-ladder`) |
| cost | none | seconds and library calls per valuation, per ladder, per curve build (`pricebt-enterprise-platform-patterns`) |
