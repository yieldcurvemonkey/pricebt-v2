# The layer conformance kit: fields, identities, what it does NOT see

Source: `src/pricebt/testing/layer_conformance.py` (`Setup`, `run_kit`, `KitReport`, `Row`, the re-exported `flat_swap_world`, `flat_bond_world`).
Proven on sabotaged specs by `tests/test_layer_conformance.py` (one line of fault per test) and applied to the shipped adapters by
`tests/test_layer_conformance_adapters.py`. The kit needs no second library and no market data source: its worlds are library-free.

## `Setup` (what the kit needs from your adapter)

| field | meaning | notes |
|---|---|---|
| `spec` | an `InstrumentSpec` built with YOUR Kit and the shared conventions block | `build_spec(name, {"factory": <Kit>, "conventions": {...}}, schemas=SchemaRegistry.default(), allow=("pricebt", "<pkg>"))`. Do NOT pass a `bind:` unless you are sabotaging: the proof is of the Kit's default block |
| `wrap` | your `wrap(SnapshotPricer) -> pricer` | the same callable the overlay names |
| `terms` | the holder: `side: pay` (swap) or `buy` (bond), plus maturity / notional / fixed_rate or security | use a non-trivial notional (1e7) and a fixed rate off par |
| `mirror_terms` | the same terms with the opposite side | `{**terms, "side": "receive"}` |
| `world(reference_date, shock_bp) -> MarketSnapshot` | an unchanged market re-anchored at another date (`shock_bp=0`) or a parallel zero-rate shock of it | `flat_swap_world("cal", holidays=HOL)`; a bond: `flat_bond_world(SEC, "cal", holidays=HOL)` |
| `t0`, `t1` | the reference date the position is struck on / first marked at, and the next business day | `dt.date(2024, 3, 4)`, `dt.date(2024, 3, 5)` in the shipped tests |
| `shocks_bp` | default `(-25.0, -3.0, 3.0, 25.0)` | small AND large: the large ones make a dropped convexity visible |
| `move_bp` | default `5.0`: the market move of the realistic-move check | |
| `tol` | default `{"fd": 2e-3, "mirror": 1e-9, "static": 1e-8, "unexplained_share": 5e-2}` | do not loosen to pass: fix the layer |
| `birth` | the date the position is struck on (default `t0`); an EARLIER date gives a SEASONED position | needed for a started swap / aged bond |
| `payment_window` | `(before, after)`: two dates around a payment date of the position | **without it the cash-sweep check does not run at all** (no rows, no warning) |

The `conventions` calendar NAME must equal the world's calendar name (`"cal"` above), or the snapshot's calendar is not found.

## The four identities and what each one proves

| check (rows' `check`) | rows' `quantity` | identity | catches |
|---|---|---|---|
| `fd_vs_delta_convexity` | `shock <n>bp`, `carry <n>bp`, `roll <n>bp` | full revaluation of a parallel shock at ONE date equals `delta + convexity` (`value` = layers, `expected` = revaluation); `carry` and `roll` are zero because no time passes | a delta or convexity layer with a wrong sign, unit, scale or reference date; a dropped convexity (visible at +-25bp only); a carry or roll that moves when nothing rolls |
| `mirror` | `value`, `dv01`, `gamma`, `carry`, `roll`, `delta`, `convexity` | a payer and the matching receiver are equal and opposite | a layer or measure that is not antisymmetric (an offset, a side-dependent bug); a wrong mirror |
| `cash_sweep` | `value drop equals cash booked`, `roll`, `delta`, `convexity` (or `a payment falls in the window`) | with the market unchanged in date space, `V1 + cash + financing - V0 == carry` across a payment date and the market-move layers are zero | a carry that does not match the cash actually booked; a double-counted flow; a market-move layer that is not zero on an unchanged market. A window WITHOUT a payment is a failing row, not a pass |
| `unexplained_share` | `share (static market)`, `share (moved market)` | the residual `pnl - sum(layers)` over `max(|pnl|, sum|layer|)` is bounded by `tol["unexplained_share"]` | a layer that leaves most of a market move unexplained. It is a BOUND, not an identity (`KitReport.unexplained_share` reports the numbers) |

`run_kit` never raises on a failing identity. `KitReport`: `.rows` (`Row(check, quantity, value, expected, tolerance, passed, note)`), `.ok`, `.failures()`,
`.frame()` (a DataFrame), `.unexplained_share`, `.assert_ok()` (raises `AssertionError("layer conformance failed:\n  <check>/<quantity>: <value> vs expected <expected> (tolerance <tol>) <note>")`). A NaN or infinite value is a failing row.

## What the kit does NOT see (the reason the tie-out exists)

Each row of this table was run on the worked example (`skills/pricebt-wire-external-library/example`) with ONE default binding edited, the kit on a fresh AND a seasoned
swap, and then the same fault as a stack overlay in `run_tieout(..., audit_measures=("dv01", "gamma", "rate", "delta_ladder"))`.
How to edit one binding for the kit: `build_spec` has NO `bind=` keyword (`build_spec(name, raw, *, schemas, allow=...)`: passing one is `TypeError: build_spec() got an unexpected keyword argument 'bind'`). `bind` is a KEY of the raw mapping, and it maps a schema name to a binding:
`build_spec("ois", {"factory": A.swap, "conventions": CONV, "bind": {"dv01": dict(A.SWAP_BIND["dv01"], sign=1.0)}}, schemas=SchemaRegistry.default(), allow=("pricebt", "acme_adapter"))` (`spec_with` in `adapter-test-templates.md` is this call).
`A.SWAP_BIND` is the Kit's own `default_bind`. Passing the binding itself instead of `{name: binding}` fails at the first key of the binding: `[CFG-UNKNOWN-BINDING] instruments.ois.bind.target: instrument 'ois': binding 'target' is not a name of asset class 'swap'; bindable: [...]`.

| one-line fault | kit | tie-out (reference vs your stack) |
|---|---|---|
| `dv01` binding without `sign: -1` | passes | `L2.dv01` `exceeds` (max rel 2.0), then `L3.tay_delta`, `L3.tay_unexplained` |
| `gamma` binding without `sign: -1` | passes | `L2.gamma` `exceeds`, then `L3.tay_convexity`, `L3.tay_unexplained` |
| `rate` binding without `scale: 100` (decimal vs percent) | passes | `L2.rate` `exceeds` (max rel 0.99, a ratio of 0.01), then `L3.tay_delta`, `L3.tay_convexity`, `L3.tay_unexplained` |
| the wrap loads one holiday too many | passes (every identity is internal: a wrong calendar applied consistently is invisible to the kit) | `L0.resolved_terms` `input` (`effective: 2024-05-29 vs 2024-05-30`), everything downstream `input` |
| ladder reducer lower-cases a key | passes (the kit never evaluates `delta_ladder`) | every `L2.delta_ladder.<bucket>` `structure` (needs `delta_ladder` in `audit_measures`) |
| `delta` layer binding without `sign: -1` | FAILS `fd_vs_delta_convexity/shock`, `unexplained_share` | `L3.delta`, `L3.unexplained` `exceeds` |
| `carry` layer binding with `offset: 500` | FAILS `fd_vs_delta_convexity/carry`, `mirror/carry`, `cash_sweep`, `unexplained_share` | `L3.carry`, `L3.unexplained` `exceeds` |

Why: a `sign` or `scale` on a measure that is wrong on BOTH sides of a mirror stays antisymmetric, and the kit never compares a measure with anything but its own mirror. Conversely the tie-out only says "same as the reference stack": when
your library defines a layer differently, `L3.layer_definitions` is `info` and only the kit's independent identities can arbitrate. Run both, always.

## Shapes to cover (mirror `tests/test_layer_conformance_adapters.py`)

1. a fresh swap (defaults above);
2. a SEASONED swap across a payment date: `terms={"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}` (and its mirror), `birth=dt.date(2023, 5, 15)`, `t0=dt.date(2024, 5, 20)`, `t1=dt.date(2024, 5, 21)`,
   `payment_window=(dt.date(2024, 5, 21), dt.date(2024, 5, 23))`; assert a `cash_sweep` row named `value drop equals cash booked` exists (proof that the sweep ran);
3. if the library prices bonds: `SEC = Security("KIT001", 4.0, dt.date(2023, 11, 15), dt.date(2033, 11, 15))` (`from pricebt.snapshot import Security`), `terms={"side": "buy", "security": SEC.id, "notional": 1e7}`, mirror `sell`,
   `world=flat_bond_world(SEC, "cal", holidays=HOL)`, `t0=dt.date(2024, 6, 3)`, `t1=dt.date(2024, 6, 4)`, `birth=dt.date(2024, 3, 4)`, `payment_window=(dt.date(2024, 5, 14), dt.date(2024, 5, 16))`, conventions `{**UST_CONVENTIONS, "calendar": "cal"}`.
   (Executed on the reference stack: all four checks run.)

`HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))` is the holiday set the shipped tests use.
