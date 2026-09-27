# The four layers: exact definitions, and how to assemble them from a library's primitives

Source of truth: `docs/design/adr/005-attribution.md` (the FOLDED definitions), `src/pricebt/contracts/schemas/swap.yaml` (`layers:`), and the three implementations that agree
to the cent on the golden case: `src/pricebt/testing/refstack.py` (`RefSwap.decomposition`), `src/pricebt/contrib/quantlib/swap.py` (`QLSwap.decomposition`) and the worked example
`skills/pricebt-wire-external-library/example/acme_adapter/swap.py` (`_decomposition`).

## 1. Notation (per ONE unit of the position, interval `(t0, t1]` between two cadence points)

`V(curve, date, fixings)` = the position's PV in the WORLD made of a curve anchored at `date`, a valuation date equal to `date`, and the fixings published by `date`.

| symbol | meaning | how you get it from a library |
|---|---|---|
| `V0` | the value at t0 | the ordinary `value` at the previous pricer (`ctx.prev_pricer`) |
| `X_fwd` | the t0 world's flows valued at t1: the market is UNCHANGED IN DATE SPACE, only the valuation date moves. Flows paid in `[t0, t1)` count at face, flows paid on or after t1 are discounted to t1 on the t0 curve (`flow * DF0(pay) / DF0(t1)`), flows projected on the t0 curve with t0 fixings | a "value as of a later date on the same market" call (forward value, theta, `as_of=`); else discount the library's own projected flow list with its own discount function |
| `V1` | the value at t1 | the ordinary `value` at the current pricer |
| `cash` | the flows paid in `[t0, t1)`, in the t1 world | the `cash` your `value` already returns (`Valuation.cash`) |
| `X_roll` | the market unchanged in TENOR space, anchored at t1, with the REALISED t1 fixings, plus `cash`. The rolled curve has `DF(t1 + tau) = DF0(t0 + tau)` | a "roll the curve to a later date keeping its shape" call; else build a discount curve from the old node table shifted by `(t1 - t0)` days and the same discount factors |
| `V_base` | the rolled curve read at the t1 curve's node dates, valued at t1 | the same curve object resampled at the new nodes (`resampled`), or a curve built from the new node dates and the rolled curve's discount factors there |
| `dz` | the realised move of the zero rates at the t1 curve's nodes: `z1 - z_base`, `z = -ln(DF)/tau`, `tau = days/365` | arithmetic on the two node tables; the zero rate is the shock variable of the layers |
| `V(eps)` | `V_base` with the zero rates moved by `eps * dz` | a "shifted market" call with one shift per node |

## 2. The layers (folded: what the shipped adapters do, and what the golden case pins)

```
carry      = X_fwd - V0
roll       = X_roll - X_fwd                                    # tenor-space roll-down PLUS realised-versus-assumed fixings (folded in)
delta      = (V_base + cash - X_roll) + g.dz                   # resampling onto the new nodes PLUS the first directional derivative along dz
convexity  = 1/2 dz' H dz                                      # the second directional derivative along the same dz
unexplained (the ENGINE's, never yours) = (V1 + cash - V0) - (carry + roll + delta + convexity) = (V1 - V_base) - g.dz - 1/2 dz'H dz
```

`g.dz` and `1/2 dz'H dz` come from TWO extra shocked valuations, no gradient or Hessian is ever formed:

```
g.dz            = (V(+h) - V(-h)) / (2 h)
1/2 dz' H dz    = (V(+h) + V(-h) - 2 V(0)) / (2 h^2)          # V(0) = V_base, h = 0.1 in the QuantLib adapter
```

The reference stack and the worked example use the same two differences at `h` and `h/2` and combine them by Richardson extrapolation
(`first = (4 d_b - d_a) / 3`, `second = (4 s_b - s_a) / 3`): four shocked valuations instead of two, and the derivatives are smooth so the result is the exact remainder.

The four layers add to the interval P&L except for the remainder, which is the engine's `unexplained` (`Engine._flush_layers`: `unexpl = pos.interval_pnl - explained`, booked as the
reserved layer `unexplained`; a name in `RESERVED_LAYERS` (`src/pricebt/types.py`: `total, transactions, cash_interest, unexplained, financing` and the baseline names) cannot be a layer of yours).
It is a residual BY CONSTRUCTION, so no identity `layers + unexplained == P&L` can fail; a small remainder proves the layers COMPLETE, not RIGHT (`docs/design/adr/005-attribution.md`,
Consequences). What proves them right is the golden case and the conformance kit (`golden-case-control.md`, `pricebt-conformance-and-tieout`).

Sanity identity to keep in your head: `carry + roll + (V_base + cash - X_roll) + (V1 - V_base) = V1 + cash - V0`. Every term telescopes; if your four numbers plus your own
`(V1 - V_base) - g.dz - 1/2 dz'H dz` do not sum to `V1 + cash - V0`, a world is inconsistent (usually a fixing or a cash window).

## 3. Assembling them: the six calls, in order (all inside ONE guarded call for a library with process-global state)

1. **t0 world.** `V0`; and the t0 flows valued at t1 -> `X_fwd`. (acme: `acme.pv(c, m0)` and `acme.pv(c, m0, as_of=t1) + _paid_before(acme.cashflows(c, m0, since=t0), t1)`; QuantLib: `w0.npv()` and `sum(a * c0.discount(d) / d1 if d >= t1 else a ...)`.)
2. **t1 world.** `V1` and `cash` (flows paid in `[t0, t1)`).
3. **Rolled market.** The t0 curve moved to t1 in tenor space, with the t1 fixings; value it -> `X_roll = pv(rolled) + cash`.
4. **Base.** The rolled curve at the t1 curve's nodes -> `V_base`, and `dz`.
5. **Shocks.** `V(+h)`, `V(-h)` (and `h/2` for Richardson) -> `g.dz`, `1/2 dz'H dz`.
6. **Assemble** the four numbers and the remainder; multiply by the direction (`side`: payer +1) so the layers are holder-signed.

**The realised fixings matter for `cash`, not only for `roll`.** `cash` is the flows paid in `[t0, t1)` with the amounts of the t1 world. A flow paid at p < t1 accrues up to p minus the payment lag and needs every fixing up
to then, and the last of them is published at that accrual end: after t0 exactly when p minus the lag is after t0, which a step LONGER than the lag allows and a step of at most the lag never does. A library whose cash query is
"flows paid up to `as_of`" on the OLD market implies that fixing from the old curve and books the wrong amount (executed on the example service, a 20mm payer over a step of 15 days: 5,163.50 against the reference's 6,304.28;
the daily tie-out and the kit's flat world never see it, because both publish the fixings the old curve implies). When the library reads fixings from the market you hand it, rebuild the old market with the t1
fixings for the cash query (in a service `{**old_market, "fixings": new_market["fixings"]}`, a derived world; in an in-process library the t1 market, as acme does). Check it with `cash_window_check.py` (`kit-blind-spots.md`,
section "The coarse step and the cash window").

Cache the whole result under `ctx.cache` (`("<lib>_swap_decomposition", id(self))`): four bound layers are four calls but ONE decomposition. The cache is per position and per mark
context (`MarkContext.cache`); the engine builds a fresh context for the layer flush, so the layers never see the marks' cache. A key with `id(self)` is safe in `ctx.cache` (the position is alive for the whole mark) but NOT in a memo that lives as long as a pricer: an `id` can be reused once its object is collected, and the reference stack
and the worked example key some pricer memos by `id(swap)`, so they pin the swap objects (`_Book._pinned` in `skills/pricebt-wire-external-library/example/acmelib/swap.py`); use a content key or pin the object. A `ctx.prev_pricer` of `None` is a
`ConfigError` (`[LAYER] layers need a previous pricer (ctx.prev_pricer)`); a swap that has nothing left at t0 (`last payment < t0`) returns zeros for all four.

## 4. What a library may lack, and the fallback

| the library has | the library lacks | do this |
|---|---|---|
| a forward-value call (`as_of`, theta) | - | use it, and add the cash paid before `as_of` |
| a projected cash-flow list with amounts and pay dates | a forward-value call | discount the flows yourself with the library's discount function (QuantLib adapter) |
| a curve constructor from node dates and discount factors | a roll call | build the rolled curve yourself: nodes `(t1, 1.0)` then `(d + (t1 - t0) days, DF)` for the old nodes after t0 |
| only a scenario engine (bump curves by name) | a curve constructor | ask the platform's team for a scenario that re-anchors a curve, or price on a curve YOU build from the snapshot and hand to the platform as a market environment: one upload per derived world, counted in `pricebt-enterprise-platform-patterns` step 3 (worked: `skills/pricebt-wire-external-library/example-service/zeta_adapter/wrap.py`, `world_id`, and `swap.py`, `decomposition`) |
| nothing but `value` | all of the above | build the four layers in the adapter from `value` calls in worlds you construct from the snapshot; the snapshot has the nodes and the fixings, so you can build any world |

## 5. The step of a finite-difference derivative changes the REMAINDER (executed on the golden case)

Mechanism (tasks/tolerance_ledger.yaml, `swap_book`, verified by `tools/probe_unexplained_ratio.py`): a central difference with step `h` of a third-order smooth function is exact only
to `O(h^2)`; the `h^2` of the third-order term is absorbed by `delta`, so the remainder is `(1 - h^2)` times the true one. With `h = 0.1` the QuantLib adapter's `unexplained` is 0.99 times
the reference's, and that is a DECLARED tolerance (`L3.unexplained`, rel 1.5e-2), not a bug. On the golden case the run below changed only `h` in `pricebt.contrib.quantlib.swap._H`
(the module, obtained with `importlib.import_module`, because `pricebt.contrib.quantlib.swap` is also the Kit attribute):

| h | QuantLib remainder | ratio to the reference's remainder | 1 - h^2 | delta minus the reference's | convexity minus the reference's |
|---|---|---|---|---|---|
| 0.2 | 0.02300775 | 0.96001 | 0.96000 | 9.6e-04 | -6.3e-07 |
| 0.1 | 0.02372662 | 0.99001 | 0.99000 | 2.4e-04 | -3.3e-07 |
| 0.05 | 0.02390638 | 0.99751 | 0.99750 | 6.0e-05 | -2.4e-07 |
| 0.01 | 0.02392468 | 0.99827 | 0.99990 | 2.3e-06 | +3.9e-05 |

(reference remainder 0.02396611, by Richardson: exact). Two lessons. (1) The ratio follows `1 - h^2` down to `h = 0.05`: the remainder of a finite-difference stack is NOT a property of the market, it is
a property of the step, so two stacks with different steps differ in `unexplained` by construction and the tie-out needs a declared, reasoned tolerance
(`tieout.tolerances`, entry in `tasks/tolerance_ledger.yaml`). (2) Too small a step is worse: at `h = 0.01` round-off in the second difference dominates (convexity is off by 3.9e-5, two
orders of magnitude worse than at `h = 0.1`) and the ratio no longer follows `1 - h^2`. Either use Richardson at two steps (exact to round-off, four shocked valuations), or fix `h = 0.1`, say
so in the module docstring and declare the tolerance.

The script that printed the table is in `references/golden-case-control.md` (section "The step experiment").

## 6. Where the value is spent

Layers are OFF unless `backtest.attribution.layers` is set; a full decomposition is 6 full valuations in the QuantLib adapter (`V0, V1, X_roll, V_base, V(+h), V(-h)`; counted by wrapping `_World.npv`) and 13 library `pv` calls plus 2 cash-flow list calls in
the worked example (counted at the library boundary: `V0`, forward value, `V1`, `X_roll`, `V_base`, and `v_eps` EIGHT times, because the first- and second-difference comprehensions each re-evaluate the same four
shocked points: four distinct valuations suffice, so memoise `v_eps` when a valuation costs a network call) per position per FLUSH (`cadence: each | eod | every_n:<k>`). On a remote platform this is the dominant cost of a run: budget it
(`pricebt-enterprise-platform-patterns`, the cost section); a service that needs derived markets also pays one upload per derived world, `5 x intervals` in all, plus one cash world per interval longer than the payment lag (step 3 of that skill).

## 7. Walk-through of the two real implementations (read both; they make opposite choices for the same six calls)

`QLSwap.decomposition` (`src/pricebt/contrib/quantlib/swap.py`) works on a library whose numbers are already payer-positive and that has NO roll or forward-value call: it builds the worlds itself.
`_decomposition` (`skills/pricebt-wire-external-library/example/acme_adapter/swap.py`) works on a library whose numbers are the RECEIVER's and that HAS `pv(as_of=)`, `Market.rolled`,
`Market.shifted`, `ZeroCurve.resampled`: it calls them.

| step | QuantLib adapter | worked example (acmelib) |
|---|---|---|
| guard | `p0 is None` -> `ConfigError("layers need a previous pricer (ctx.prev_pricer)", code="LAYER")`; `t1 < t0` -> `ValueError("the layers need t1 >= t0, got ...")` | the same two checks |
| nothing left at t0 | `self.last_payment(cal) < t0` -> all zeros | `to_date(c.last_payment) < t0` -> all zeros |
| t0 world, under `C.guard(t0)` | `w0 = _World(...)`, `v0 = w0.npv()`, `x_fwd = sum(a * c0.discount(d) / d1 if d >= t1 else a for d, a in w0.flows(t0))`: discounts the projected flows itself | `v0 = acme.pv(c, m0)`, `x_fwd = acme.pv(c, m0, as_of=t1) + _paid_before(acme.cashflows(c, m0, since=t0), t1)` |
| t1 world, under `C.guard(t1)` | `v1 = w.npv()`, `cash = sum(a for d, a in w.flows(t0) if d < t1)` | `v1 = acme.pv(c, m1)`, `cash = _paid_before(acme.cashflows(c, m1, since=t0), t1)` |
| rolled curve | builds `cr = C.discount_curve([t1] + [d + n days for d in dates0 if d > t0], [1.0] + [DF ...])`, `w.link(cr)`, `x_roll = w.npv() + cash` | `rolled = m0.rolled(t1, fixings=m1.fixings)`, `x_roll = acme.pv(c, rolled) + cash` |
| base and `dz` | `base = [cr.discount(d) for d in dates1]`; `dz = -ln(a)/tau + ln(b)/tau` over the t1 nodes | `base = acme.Market(rolled.curve.resampled(m1.curve.pillars), m1.fixings)`; `dz = [z1 - zb ...]` |
| shocks | `v_eps(eps)` relinks a curve with DFs `b * exp(-eps * dz * tau)`; `up, dn = v_eps(_H), v_eps(-_H)` | `v_eps(eps) = acme.pv(c, base.shifted([eps * d for d in dz]))`; two steps, Richardson |
| assemble | `out = {carry, roll, delta, convexity, residual, V0, V1, cash}` (payer's sign already) | `out = {k: sw.direction * v ...}`: the receiver's number times the holder's direction |
| cache | `ctx.cache[("ql_swap_decomposition", id(self))]` | `ctx.cache[("acme_swap_decomposition", id(sw))]` |
| bound as | METHODS: `_bind("carry")` = `{target: {method: carry}, kwargs: {ctx: "@ctx"}}`, no `sign` | FUNCTIONS: `{target: {function: "acme_adapter.swap:carry"}, kwargs: {swap: "@instrument", ctx: "@ctx"}, sign: -1}`; a dotted path, so `registry.allow` must name `acme_adapter` |

Which to choose for your library: methods if the instrument class can hold them (no allow-list entry, works in the Python facade); a function target when the layer needs the instrument, the
context AND a second call, or lives outside the class (`pricebt-bindings-cookbook`, `pricebt-instrument-kit`). The receiver's-view library carries `sign: -1` on the four layers, `dv01`, `gamma`,
the ladder and its extension measures, one visible and overridable line per binding; the conformance kit sees a missing `sign` on `delta` (four shock rows), on `convexity` (two rows) and, weakly, on `carry` (only the unexplained
share), and NOT on `roll`, `dv01`, `gamma` or the ladder: the golden case is the check for the layers, `dv01_consistency.py` for `dv01` and the ladder, `risk_known_answers.py` for `gamma` (`kit-blind-spots.md`).
