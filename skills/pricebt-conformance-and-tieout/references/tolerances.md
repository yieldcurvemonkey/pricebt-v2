# Tolerance policy: shipped defaults, declarations, floors, the ledger

Sources: `src/pricebt/tieout/tolerances.py` (`DEFAULT_TOLERANCES`, `Tol`, `Tolerances.for_`, `validate_declaration`, `merge_declarations`, `floor_unit`), `tasks/tolerance_ledger.yaml`, ADR 006.

**Rule (repository POLICY, checked in review, not by code): a tolerance is never widened to turn a run green without a written reason.** The policy is stated in `AGENTS.md`, `CONSTRAINTS.md` and ADR 006: a declaration carries a `reason` (the report prints it in `## Tolerances used`),
and every value and every change has an entry in `tasks/tolerance_ledger.yaml`. **What the code enforces is only this** (`validate_declaration`, `_check_value`): `rel` is required, finite and >= 0; a `reason`, when supplied, must be a non-empty string.
Executed: `validate_declaration` accepts `{'L1.pv': 1e-3}` (a bare number), `{'L3.carry': {'rel': 0.5, 'expected': True}}` and `{'L2.dv01': {'rel': 3.0}}` without a word, and no code reads `tasks/tolerance_ledger.yaml` (`tolerance_ledger` occurs in `src/`, `tests/`, `tools/` and the notebook sources only in comments).
So the harness will silently pass an unexplained widening: your own test must refuse it (`test_every_declared_tolerance_carries_a_reason` in `adapter-test-templates.md`) and the ledger row is a review item.
The tolerance is a statement about the library, not a knob: widening `L2.dv01` to `rel: 3.0` (executed, `--set 'tieout.tolerances={"L2.dv01": {rel: 3.0, reason: "demo"}}'`) turned a flipped dv01 sign (max rel 2.0) into `within tolerance`.
Only the L3 baseline rows (which read dv01) still failed. Fix the wiring first; declare only what you can explain with a measured mechanism.

## 1. The shipped defaults

Print them (the values are code, not documentation):

```powershell
$env:PYTHONPATH = "src"
python -c "from pricebt.tieout.tolerances import DEFAULT_TOLERANCES as D; [print(k, v.rel, v.floor, 'strict' if v.strict else '') for k, v in D.items()]"
```

Keys are `<level>.<quantity>[.<asset class>]`, most specific first: `L1.pv.bond` (a tighter bond value), `L1.pv`, `L1.*`. The shipped table has entries for `L1.pv`, `L1.pv.bond`, `L1.cash`, `L1.financing`, `L1.quantity` (strict), `L2.dv01`, `L2.gamma`,
`L2.delta_ladder` (a per-bucket floor), `L2.*`, `L3.tay_delta`, `L3.tay_convexity`, `L3.tay_unexplained`, `L3.*`, `L4.equity`, `cash`, `positions_value`, `trade_pv`, `trade_cash`, `stats`, `stats_ratios`, `stats_traded`, `L4.*`.
They are the placeholders of spec Appendix B plus four structural decisions recorded under `defaults_added` in the ledger.

Legal keys (`validate_declaration`): level `L1`..`L4` (L0 is exact by definition: `unknown tolerance key 'L0.pv': L0 (snapshot digests, resolved terms, conventions) is exact by definition and has no tolerance`; "exact" for the resolved terms means floats within 1e-12 relative (absolute below 1) and dates equal, `tieout-reference.md`, L0 row);
at L1 and L4 the quantity is from a closed set (`L1`: quantity, pv, cash, financing; `L4`: equity, cash, positions_value, trade_pv, trade_cash, stats, stats_ratios, stats_traded), a typo gets
`unknown quantity 'pvv' for level L1 (it compares quantity, pv, cash, financing); did you mean L1.pv?`; at L2 the quantity is a measure name (`delta_ladder` covers every bucket), at L3 a layer name or `<layer>/unit`; `*` means the whole level.

## 2. How a lookup resolves (`Tolerances.for_`)

1. a DECLARED key that names the quantity (`L1.pv.swap`, then `L1.pv`; for `carry/unit` its own key first, then `carry`);
2. a strict SHIPPED default (an invariant: `L1.quantity`): a wildcard declaration does not widen it, only a declaration that names it does;
3. a declared wildcard `L<n>.*`;
4. the shipped defaults, most specific first.

An explicit `tolerances=` argument of `run_tieout` removes every config-declared key it COVERS (`L1.pv` covers `L1.pv.<class>`; `L1.*` covers every `L1` key) and leaves the rest (`merge_declarations`).

## 3. Declaring: the three places

In the BASE config, next to the strategy it belongs to (the loader validates it at load time; example `configs/suite/s1_swap_carry_eod.yaml`):

```yaml
tieout:
  tolerances:
    L3.unexplained:
      rel: 1.5e-2
      floor: 1.0
      reason: "one stack's adapter takes its directional derivatives by central differences of step h = 0.1 of the realised move, so its remainder is (1 - h^2) = 0.99 times the true one (verified ratio 0.9896 .. 0.9905; measured 1.000e-2); 1.5e-2 is 1.5 x h^2 and still catches a wrong layer definition (tasks/tolerance_ledger.yaml)"
```

A value is a number (the relative tolerance) or a mapping `{rel, floor, expected, strict, reason}`: `rel` required, finite, >= 0; `floor` finite, > 0 (default 1.0); `expected` and `strict` booleans; `reason` a non-empty string IF present (it may be omitted: the policy above requires it, the loader does not).
On the command line the whole mapping is set at once (`--set` splits on every dot, so `--set tieout.tolerances.L2.dv01.rel=1.0e-3` fails with `[CFG-TIEOUT] tieout.tolerances.L2: malformed tolerance key 'L2'`):

```powershell
python -m pricebt tieout <base> --stack ... --set 'tieout.tolerances={"L2.dv01": {rel: 1.0e-3, reason: "why, with the measured value"}}'
```

In Python: `run_tieout(base, stacks, tolerances={"L1.pv": {"rel": 1e-5, "reason": "..."}})`, or a `tieout` block in a base given as a dict (`notebooks/src/showcase_swap_book.py`, section 8, does this).
Validation errors are `[CFG-TIEOUT] tieout.tolerances.<key>: ...`: `` `reason` must be a non-empty string (why the tolerance is what it is), got '' ``, ``a tolerance mapping needs `rel` ...``, ``malformed tolerance key 'L2': expected `<level>.<quantity>[.<asset class>]` ...``.

## 4. Floors and their units

`difference = |a - b| / max(|reference|, floor)`; `noise` if the max of that is `<= rel`. Below the floor the test is therefore ABSOLUTE: `|a - b| <= rel * floor`.
The floor is in the unit of the compared value (`floor_unit`, printed as `floor unit` in `## Tolerances used`): a currency amount per unit of position for `L1`/`L3`, "the measure's own unit, per unit of position (, per bucket)" for `L2`, "position size" for `L1.quantity`, "currency traded" for `L4.stats_traded`.
Measures and layer `unit` values are per unit AS BUILT (a 1mm-style unit), and the shipped floors assume templates sized like the shipped ones.

Check what a floor can see (executed; the ladder's absolute tolerance below its floor is 0.1 currency per bp per unit):

```powershell
python -c "from pricebt.tieout.tolerances import Tolerances; t = Tolerances().for_('L2', 'delta_ladder', 'swap'); print(t.rel * t.floor)"
```

A bucket of size x hides every relative error below `0.1 / x` (3.5% at x = 2.8; everything at a bucket of 0); a 2x error on a 2.8 bucket is `exceeds`, a 2% error on it is `noise`. Measured with `pricebt.tieout.compare._measure` and `_status`.
Consequence: do not trust the ladder tie-out for small buckets; also assert the ladder against the reference directly in a unit test. If your units are much smaller than the shipped templates, declare a smaller `floor` with a `reason` (and `L2.gamma`, the layers, the same).

## 5. `strict` and `expected`

* `strict: true` marks an invariant that a wildcard cannot widen (`L1.quantity`, the position size). A strategy that sizes trades from a MEASURED risk (a ladder hedge) gets different sizes from each stack: declare `L1.quantity` by name with the measured amplification and the reason (ledger run `swap_book`).
* `expected: true` (spec X6) declares a known DEFINITION difference (for example a layer that two libraries decompose differently): an exceedance is reported under status `expected` and is not a failure. By policy it still needs a `reason` (not enforced by the loader, see the rule above). Prefer fixing the definition; use `expected` only when the definitions cannot be aligned.

## 6. The procedure: measure, classify, declare, record

1. Run with the shipped defaults, nothing declared. List every row that is not `exact`: `rep.frame().query("status != 'exact'")` (columns `level, quantity, asset_class, n, max_abs, max_rel, where, tol_rel, tol_floor, status, note`).
2. Classify each row: a WIRING bug (a sign, a unit, a calendar: fix the adapter, see `pricebt-debug-tieout-differences`), a real noise floor of an independent library (solver tolerance, finite-difference step), or a DEFINITION difference.
3. For the last two, find the MECHANISM (one hypothesis, one change, a known-answer control), then declare `rel` at about 1.5 to 2 x the measured max, with a `reason` that names the mechanism, the measured value and what the tolerance still catches.
4. Add the entry to `tasks/tolerance_ledger.yaml`: under `runs:` a name with `what`, `command`, `self_test`, `measured` (per level, each pair), `declared_in_base_config` (a list of `{key, value, reason}`) and `verdict`. The shape is in the existing entries (`swap_book`, `suite_s1_swap_carry`, `suite_s05_bond`).
5. Re-run: the declared keys appear in `report.header["declared_tolerances"]` and in `## Tolerances used` with their reason.

Worked mechanism (do not re-derive): the QuantLib adapter's `unexplained` is 0.99 x the reference's; see `pricebt-debug-tieout-differences`, `skills/pricebt-debug-tieout-differences/references/worked-examples.md`.
