# Forward Curve vs Straddle — Systematic Backtest Spec

v0.4 · 2026-10-23 · derived from the `screener.py` prototype · v0.2: trades are two-sided (side +1 / −1) · v0.3: framed as an indicative math guide · v0.4: carry definition, carry horizon, horizon-matched vol, premium vs gamma sizing

> **Read this first: an indicative guide to the math, not an implementation spec.**
>
> - **Purpose.** This document defines the *signal*: what is computed, from which market quantities, with which formulas, and how a signal becomes a position, an exit and a P&L. Treat it as an indicative guide to the economics and the research protocol. It is not a build plan.
> - **`screener.py` is a prototype.** It shows how the signal is constructed and generated, and it is the reference for the formulas only. Its code is not a template. It reads one spreadsheet, uses flat-yield annuities, uses spot swap rates as a stand-in for the option underlyings, and has no position engine. None of that is meant to carry over.
> - **No architecture or API is assumed.** The author of this spec had no knowledge of the backtesting platform it will be implemented on. Read the words "ticket", "engine", "config key", "trial log", "config hash", "blotter", the YAML in Appendix B, and the plotly-style report in §17 as **descriptions of what must be computed or recorded**, not of how. Map each one onto the platform's own position model, pricing, configuration, data, logging and reporting.
> - **What must be preserved:**
>   - the formulas in §5;
>   - the trade logic in §6;
>   - the economics of marking and hedging in §7: positions age (their start shortens) and are marked off the strip at their live start, not at the constant-maturity series; hedges are sampled on one shared grid; gamma is re-matched;
>   - the attribution identities in §8;
>   - the controls (§11) and the evaluation protocol (§1, §12–§14).
> - **What can change freely:**
>   - parameter names and how they are stored;
>   - data structures, file layouts and code organisation;
>   - how positions are represented, provided the economics in §7 hold;
>   - output formats.
> - **Better platform inputs win.** Where the platform has better market data or pricing than the prototype, use it. Examples: real forward-starting underlyings, a vol smile, a full curve build, actual cost models. In that case the matching limitation in §18 falls away. Note each substitution, because it changes the numbers relative to the screener.
> - **The figures quoted in this spec are illustrative.** They are calibration examples from the prototype's dataset as of 17-Oct-2026: vol levels, shares of days on each side, ρ values and cost anchors. They are not targets to reproduce.

The curve and package formulas match the screener. Where this spec **extends** one, it is marked **[ext]**.
Every tunable number is named `like_this`. Their defaults, ranges and tuned/fixed status are in Appendix A. The names are labels, not required identifiers.

---

## 0. What is being tested

One package, traded on either side. `side` ∈ {+1, −1}:

| | Curve leg (DV01-neutral, tenor `n`) | Straddle leg (ATM, tail `m`, gamma-matched at entry) | Bought when |
|---|---|---|---|
| **side +1** | Flattener: receive the later start `l`, pay `s`. Long curve gamma | **Sell** | Curve gamma is cheap vs option gamma: σ_BE < σ_imp |
| **side −1** | Steepener: pay `l`, receive `s`. Short curve gamma | **Buy** | Curve gamma is rich vs option gamma: σ_BE > σ_imp |

- Both legs are delta-hedged on the same rule.
- The P&L identity is symmetric: side −1's edge is exactly −1 × side +1's edge (§5.3), so one signed score drives both sides.
- **The two sides are not economic mirror images.** Implied vol usually sits well above realised (about 91 vs 61 on the long forwards in Oct-2026). So side −1 rarely fires because options are cheap. It fires because the forward strip is strongly concave, which makes σ_BE high. Side +1 sells the vol risk premium against curve convexity; side −1 pays it to own a concave curve's roll-down. Their returns and stress behaviour are tested separately (§1).
- `sides: [+1]` reproduces v0.1 exactly.

**Where each side shows up (screener history, 2015-10 → 2026-10, vs 1Y.30Y)**

| Pairs | Share of days σ_BE > σ_imp | Reading |
|---|---|---|
| 10y5y/15y5y (and 10y10y/12y10y, below `min_dD`) | 75–85% | **Persistent.** The strip humps near 10y, so the flattener's carry is deep in term-premium roll-down, and σ_BE (≈129 on 17-Oct-2026) is mostly not convexity. This is a belly carry trade, and it is where T4's bias is largest |
| 10y5y/20y5y, 10y10y/15y10y (and 12y10y/15y10y, below `min_dD`) | 15–36%, mostly 2019–2026 | **Transient.** This is the genuine RV mirror |
| 10y10y/20y10y, 10y5y/25y5y, 10y5y/30y5y, 15y5y/20y5y, 25y5y/30y5y | 4–11%, mostly 2019–2021 and 2025–2026 | Occasional. Near fair value today (10y10y/20y10y: σ_BE 92 vs 91) |
| 20y5y/25y5y, 15y5y/30y5y, 20y5y/30y5y, 15y5y/25y5y, 15y10y/20y10y | < 1% | The long-end strip is straight or convex, so σ_BE is small or negative. Side +1 only |

The backtest answers four questions:

1. Does the screener's signed score pick packages that make positive risk-adjusted P&L **out of sample, after costs**, on each side?
2. Is that P&L the **gamma edge** the score claims? Or is it slope drift, or a plain short-vol (side +1) or long-vol (side −1) position that the curve leg adds nothing to?
3. Does side −1 add anything beyond a persistent belly carry trade?
4. Which parameters matter, and is performance stable across a range of settings or confined to one peak?

---

## 1. Pre-registered hypotheses (fixed before any results are seen)

| # | Hypothesis | Test | Pass line |
|---|---|---|---|
| H1 | Package makes money | Stage B, walk-forward OOS + holdout, 1× costs | Net Sharpe ≥ `accept_sharpe` (0.4); deflated Sharpe ≥ `accept_dsr` (0.95) |
| H2 | Side choice and timing add value | Stage B vs always-on: C3a for the combined book and side +1; C3b's side −1 days for side −1 | Signal Sharpe − control Sharpe > 0; bootstrap p < 0.10 |
| H3 | The curve leg hedges the option leg | Stage B vs control C1a (option-only, copied dates), stress windows (§15) | Side +1: package max drawdown in stress windows ≤ C1a's. Side −1: package P&L summed over stress windows > 0 (the long straddle should pay); if not, the long-gamma leg isn't doing what it claims |
| H4 | The edge is gamma edge | Attribution (§8.3) | Realised gamma-edge component > 0 and ≥ 50% of pre-cost P&L (when pre-cost P&L is positive) |
| H5 | Score ranks forward P&L | Stage A (§12) | Pooled IC > 0 with NW t ≥ 2 at h = 63; Q5−Q1 > 0 with t ≥ 2 |
| H6 | Survives costs | Cost sweep (§9) | Net Sharpe > 0 at 2× costs |

**Scope of each test.** H1–H6 are judged three times: on the combined book, on side +1 alone and on side −1 alone (`sides: [+1]`, `sides: [−1]`). A side with fewer than `min_side_trades` (20) OOS trades is reported descriptively, with no pass/fail.

**Decision rule, per side:**
- **Viable:** H1, H4 and H5 pass, and neither H3 nor H6 fails.
- **Viable as vol only:** H1 passes but H4 fails, or C1b (same side) ≥ package. Then the curve leg is a hedge, not alpha. Report side +1 as "short vol hedged with curve gamma" and side −1 as "long vol funded by curve roll-down".
- Anything else is reported as a negative result. A negative result is an acceptable outcome.
- **Combined book:** run both sides only if each passes on its own. Otherwise run the passing side alone.

---

## 2. Notation and units

- `t` is a business day (an aligned date, §3). Rates are stored in %. All math below is in **bp**.
- Vols are **normal bp/yr**. Package quantities are **bp/yr per 1 unit of curve DV01** ("per DV01") unless shown in $.
- Curve `c = (n, s, l)`: tenor `n`, starts `s < l`, both on the tenor-`n` forward strip.
- Option `o = (τ₀, m)`: expiry `τ₀` in years, tail `m`.
- `f_T(t)`: the constant-maturity `T×n` forward. `y_m(t)`: the spot `m`-year swap rate, used as a proxy for the option's underlying (§18).
- `φ`, `Φ`: standard normal pdf and cdf. `φ(0) = 1/√(2π)`.
- `z_L(u)`: rolling z-score of `u` over the last `L` days, `(u − mean_L) / std_L`, **strictly backward-looking**.

---

## 3. Data contract

This section lists the minimum inputs the signal needs, as the prototype had them. If the platform has richer or cleaner equivalents, use those instead (see the note at the top).

| Field | Content |
|---|---|
| Forward strips | 5y tenor: starts 5, 10, 15, 20, 25, 30. 10y tenor: starts 10, 12, 15, 20. Plus synthetic 5y10y and 25y10y, built by annuity-weighting adjacent 5y forwards, used **only** to anchor the roll derivative |
| Spot swap rates | 10Y, 20Y, 30Y (option underlying proxy) |
| ATM normal vols | Expiry × tail grid: 10Y tail 1M–10Y; 20Y and 30Y tails 1M, 3M, 6M, 1Y |

**Alignment rules**
1. Inner-join the forward and vol tables on date. Drop weekend rows.
2. **Stale days.** If every forward is unchanged from the prior row (a holiday carry-forward), drop the row (`stale_policy: drop_all_unchanged`). Log every dropped date.
3. Never forward-fill a forward or a vol across a gap. A cell is computed only on dates where all its inputs exist.

**Windows**
- Vols start 2015-10-08. Warmup is `warmup_days` (252), so the first possible trade is about 2016-10.
- **Locked holdout:** the final `holdout_months` (12), currently 2025-10-17 → 2026-10-17. It is not read for any tuning, plotting or debugging until §13.4.
- Development window: from the first tradeable date to the holdout start.
- Forward history before 2021 may be a LIBOR splice. A **curve-only** extension back to 1991 (control C2 only) is allowed but reported separately and never pooled with package results.

---

## 4. Universe and eligibility

- **Curves:** every same-tenor pair on the tradeable starts, exactly as in the screener. That is 10 pairs on the 5y strip and 6 on the 10y strip.
- **Curve filter:** `ΔD ≥ min_dD` (4.0). Small ΔD makes σ_BE explode; for example 10y10y/12y10y has ΔD 2.0.
- **Options:** `allowed_tails` × `allowed_expiries`. Default: tails {20Y, 30Y}, expiries {1M, 3M, 6M, 1Y}. The 10Y tail is available for the any-tail study.
- **One option per curve.** An open package keeps its option spec (τ₀, m) and its side for its whole life, including rolls.
- **One side per curve.** A curve holds at most one package at a time, on one side.
- The 30Y tail is the matched tail for the belly pairs too (median ρ 0.98 for 10y5y/15y5y vs 0.89 on the 10Y tail), so the default tails also cover side −1.

---

## 5. Signal generation

Everything is computed at the close of `t` from data up to and including `t`.

### 5.1 Curve quantities (identical to the screener)

```
r_i(t)  = 100 · ∂f_T/∂T at T = T_i          receiver roll, bp/yr. 3-point Lagrange quadratic on the strip:
                                            centred where possible, one-sided at strip ends   (roll_interp)
D_i(t)  = modified annuity duration of T_i×n, flat semi-annual yield = f_i(t)
ΔD      = D_l − D_s
carry c = r_l − r_s                          bp/yr per DV01
v       = −c · 10⁴ / ΔD
σ_BE    = sign(v) · √|v|                     curve-implied vol; negative = you are paid to own gamma
x       = 100 · (f_l − f_s)                  forward spread, bp
z_x     = z_{L_static}(x)
θ       = √(D_s / D_l)                       long gamma iff σ_l/σ_s > θ
```

**What "carry" means here.** Read this before implementing, because the research literature uses the word loosely.

- `c` is the **spot-unchanged roll-down** of the DV01-neutral pair. It is the P&L per year if today's curve stays exactly where it is while each leg's start date shortens with time.
- A forward-starting swap accrues no coupon before it starts, so for these legs all of `c` is roll-down; there is no coupon carry. JPM (2017) calls this whole quantity "carry" ("the ex-ante difference between spot and forward"). JPM (2018) splits coupon carry from slide. Both describe the same number for forward swaps.
- It is **not** carry under a "forwards realised" assumption, which is zero by construction for any forward.
- It contains the term premium as well as the convexity bias, so σ_BE is not pure convexity (§18, item 10).

**Carry horizon (`carry_horizon`, default `instant`).**

```
instant:  c   = r_l − r_s                                          the derivative above, bp/yr (screener)
h years:  c_h = 100 · [ (f_l(T_l) − f_l(T_l − h)) − (f_s(T_s) − f_s(T_s − h)) ] / h     bp/yr, annualised
              f(T − h) is today's strip interpolated at the shortened start, using roll_interp
```

- `c_h` is the average roll rate over a holding period of `h`. It tends to `c` as h → 0.
- The gap between `c_h` and `c` is about ½·f″·h per leg, from the strip's curvature. It is negligible on the straight long-end strip and noticeable near the 10y hump.
- When `carry_horizon` = h, use `c_h` everywhere `c` appears: v, σ_BE, E and X.

### 5.2 Forecasts [ext: method is configurable; the screener uses a 63-day rolling window]

For any bp series `u`, daily changes `Δu`:

```
rolling:  σ̂_u = √252 · std_{W}(Δu)
ewma:     σ̂_u = √(252 · EWMA_h(Δu²))        half-life h = rv_halflife, zero-mean
blend:    σ̂_u² = ½(rolling² + ewma²)
shrink:   σ̂_u² ← (1−ω)·σ̂_u² + ω·mean_{L_long}(σ̂_u²)      ω = rv_shrink (default 0)
```

Series to forecast:
- `f_l` and `f_s` → `σ̂_l`, `σ̂_s`
- `y_m` → `σ̂_m`
- `x` → `σ̂_x` (slope vol)
- `σ_imp` → `σ̂_vv` (vol of vol)
- the basis P&L `b` (below) → `σ̂_b`

The method is `rv_method` (default `ewma`). Correlations and `σ̂_b` always use a rolling window of `W` (63) days.

```
σ̂_c² = (D_l·σ̂_l² − D_s·σ̂_s²) / ΔD          the curve's effective realised variance (can be < 0)
b_t   = 10⁻⁴ · [D_l·Δf_l² − D_s·Δf_s² − ΔD·Δy_m²]     daily gamma-basis P&L per DV01
ρ     = corr_W( Δ(f_l+f_s)/2 , Δy_m )
```

### 5.3 Package quantities

```
E      = c + 10⁻⁴·ΔD·σ_imp²                         edge = curve carry + matched straddle theta
                                                    (= 10⁻⁴·ΔD·(σ_imp² − σ_BE²))
B      = w_B · 10⁻⁴·ΔD·(σ̂_c² − σ̂_m²)              realised-vol basis               (w_B default 1)
M      = w_M · (x − μ_x)·(1 − e^{−κ_x})            [ext] slope mean-reversion overlay (w_M default 0)
            μ_x = mean_{L_static}(x)
            κ_x = −252·ln(ρ_AR), ρ_AR = AR(1) coefficient of x over L_static, clipped to [κ_min, κ_max]
X      = E + B + M                                  expected P&L of side +1, bp/yr per DV01
τ_eff  = τ₀·(1 + roll_fraction)/2                   [ext] average remaining expiry under the roll rule
ν_pkg  = 2·10⁻⁴·ΔD·σ_imp·τ_eff                      vega of the matched straddle per DV01 (per 1bp vol)
R      = √( σ̂_x² + (ν_pkg·σ̂_vv)² + σ̂_b² )         risk, bp/yr per DV01 (same for both sides)
S      = X / R                                      signed structural score (annualised Sharpe proxy)
side*  = sign(S)                                    the side the score favours
S_side = side · S                                   score of a given side; side −1 expects −X
k_opt  = 10⁻⁴·ΔD·σ_imp·√τ₀ / φ(0)                   straddle underlying DV01 per curve DV01 (gamma match)
```

**Which σ_imp, and comparing like with like.**

- **Horizon consistency.** The vol in E must cover the same window as the carry:
  - `carry_horizon: instant` uses σ_imp of the traded option (τ₀, m), as the screener does.
  - `carry_horizon: h` uses σ_imp(h, m), the vol at expiry h on the traded tail, interpolated in total variance as in §7.3. The implied vol for expiry h is the market's price of variance over that same window, which is why expiry matching is right.
- **Compare rates, never carry against premium.** E compares the curve's carry per year with the straddle's theta per year, both at matched gamma. Never compare the carry over h with the **premium** of an h-expiry straddle:

```
gamma-matched straddle premium   = k_opt · 2σ_imp√τ₀·φ(0) = 2 · 10⁻⁴·ΔD·σ_imp²·τ₀
theta at entry over the horizon  =                          1 · 10⁻⁴·ΔD·σ_imp²·τ₀
```

- The premium is **twice** the horizon theta, because an ATM straddle's gamma grows as 1/√τ while it ages. Comparing carry with premium makes σ_BE look √2 too high relative to σ_imp.
- Example: 10y10y/20y10y on 17-Oct-2026 has ΔD ≈ 9.9, σ_BE ≈ 92 and σ_imp ≈ 91. Over 6m the curve costs about 4.2bp per DV01 and the matched straddle decays about 4.1bp, so the package is fair. The straddle's premium, though, is about 8.2bp. Set against that, the curve looks as if it implies about 130 vol.

**Option sizing (`option_sizing`, default `gamma`).**

```
gamma:    k = k_opt                                                  Γ_opt = Γ_c at entry (this spec's trade)
premium:  k = |c|·τ₀ / (2·σ_imp·√τ₀·φ(0))                            premium at entry = |carry over τ₀|  (JPM 2017 backtest)
          k_premium / k_opt = σ_BE² / (2·σ_imp²)        when c < 0
```

- `premium` reproduces the sizing in JPM's 2017 backtest: "sized such that the initial premium intake is equal to the carry over the same 1-year horizon".
- That package is **carry-neutral, not gamma-neutral.** At fair value the straddle carries only about half the curve's gamma at entry, so the package starts net long gamma (side +1) and holds a net vol position. E, B and R then do not describe its P&L. It is an ablation (§13.3), judged on P&L and attribution only.
- In `premium` mode, gamma re-matching (§7.4) is off. Delta hedging stays as in §7.
- JPM also held both legs unhedged to the horizon. This spec does not replicate that, because its thesis is delta-hedged gamma.

- **Why one score covers both sides.** Every term flips sign with the side: side −1 earns the curve carry the flattener pays, pays the theta the short straddle earns, and is short the realised-variance basis. Hence side −1's expected P&L is exactly −X, and risk R is unchanged.
- **Sign of M.** When the spread is far below its mean (unusually inverted), `x − μ_x < 0`, so M is negative: an expected steepening hurts side +1 and helps side −1. This is where the "carry looks free but the level is extreme" tension (e.g. 15y5y/30y5y) is formally tested. M is off by default and turned on only as an ablation (§13.3).
- **κ clipping.** `κ_min` = 0.05/yr, `κ_max` = 4/yr. If ρ_AR ≥ 1, set κ = κ_min.

### 5.4 Dislocation score [ext]

```
q  = σ_BE / σ_imp                      relative price of curve gamma vs option gamma
D  = −z_{L_disl}(q)                    signed. D > 0: curve gamma unusually cheap vs options → side +1
                                               D < 0: curve gamma unusually rich vs options  → side −1
```

- `disl_metric`:
  - `be_ratio` (default): as above.
  - `be_minus_imp`: q′ = σ_BE − σ_imp.
  - `both_legs`: score each leg against its own history, D = min(−z(σ_BE), z(σ_imp)) for side +1 and min(z(σ_BE), −z(σ_imp)) for side −1, keeping the larger with its sign. This is the literal "curve vol cheap **and** straddle vol rich" reading. It stops a move in σ_imp alone from triggering the signal.
- **Common-factor warning.** On 17-Oct-2026, z(q) ≈ −2.3 on **all 16 pairs at once**, because σ_imp moved and every q shares it. The ratio score then says the same thing on every curve, so in dislocation mode it says little about which curve to trade. That is why `structural` stays the default mode and `both_legs` is an ablation.
- **Relative and absolute can disagree.** For long-end pairs, the days when z(q) > 1 (relatively rich curve gamma) almost never have σ_BE > σ_imp. A pure relative rule would put on side −1 at a negative absolute edge, i.e. pay carry to bet the gap narrows. G6 (side·E > 0) blocks this by default; §13.3 runs the version without it.
- **Do not use z(E).** E is dominated by the level of σ_imp and barely varies across curves; the screener shows this.

### 5.5 Gates (all must pass to enter; evaluated for the side being entered)

| Gate | Condition | Key | Default |
|---|---|---|---|
| G1 gamma sign | σ̂_l/σ̂_s ≥ θ·(1 + g_margin). Same for both sides: the curve must actually carry the gamma that the straddle offsets (long for side +1, short for side −1) | `g_margin` | 0.02 |
| G2 basis | ρ ≥ ρ_min | `rho_min` | 0.75 |
| G3 static | side · z_x ≥ z_static_min (and ≤ z_static_max). Side +1 is blocked when the spread is already extremely low; side −1 when it is already extremely high | `z_static_min`, `z_static_max` | −2.5, +∞ |
| G4 vol trend | σ_imp(t) − σ_imp(t − k_vt) ≤ vt_max, both sides. Side +1: don't sell into a spike. Side −1: don't buy at the top of one | `k_vt`, `vt_max` | 10d, 8bp |
| G5 sanity | ΔD ≥ min_dD; \|σ_BE\| ≤ 300; no stale input in the last 3 days | `min_dD` | 4.0 |
| G6 absolute edge | side · E > 0. Near-automatic in structural mode; in dislocation mode it requires the relative signal to agree with the absolute edge | — | on |
| G7 persistence | Share of the last `L_persist` days on which sign(E) = side ≤ `max_side_persistence`. Separates transient RV from a structural carry trade. It applies to both sides, so at 0.6 it also removes the 20y5y/25y5y flattener | `max_side_persistence`, `L_persist` | off, 252 |

During the life of a trade only G1 is re-checked, as an exit trigger (exit X4). The list of gates re-checked on open trades is configurable (`exit_gates`).

### 5.6 Choosing the option for each curve (`option_select`)

- **`matched` (default):** pick the tail `m*` with the highest ρ (averaged over its allowed expiries). Then pick the expiry on `m*` with the highest |S| among cells that pass the gates for side sign(S).
- **`best`:** pick the cell with the highest |S| over all allowed (τ₀, m) that pass the gates for side sign(S). This is the any-tail study.
- **`fixed`:** use a single given (τ₀, m) for every curve.

The chosen cell's S, D and gates are that curve's signal for the day.

---

## 6. Trade rules

### 6.1 Timing
- The signal is formed at the close of `t`. The trade executes at the close of `t + lag` (`lag` = 1) at mid ± cost.
- Signals are evaluated every day (`eval_freq`).
- Every rolling statistic uses data up to `t` only. This is enforced by the look-ahead test (§16, T6).

### 6.2 Entry

| Mode (`signal_mode`) | Side | Enter when (for `confirm_days` consecutive days on the **same side**, default 3) |
|---|---|---|
| `structural` (default) | sign(S) | \|S\| ≥ `s_in` and all gates pass |
| `dislocation` | sign(D) | \|D\| ≥ `d_in` and all gates pass (G6 makes the absolute edge agree) |
| `blend` | sign(S + λ_D·D) | \|S + λ_D·D\| ≥ `s_in` and all gates pass (`lambda_D` = 0.1) |

- Only sides listed in `sides` (default [+1, −1]) can be entered.
- Thresholds are symmetric. `s_in_neg_mult` (default 1, fixed) scales `s_in` and `d_in` for side −1. It is an ablation for "side −1 needs a bigger edge to pay for owning vol", not a tuned parameter.
- When there are more candidates than free slots, rank by |entry score|, then by ρ.
- Fill slots subject to the portfolio constraints in §6.5.
- After a curve exits, the **same side** cannot be re-entered for `cooldown_days` (5). The opposite side can be entered as soon as its own entry rule holds, which is how a package reverses.

### 6.3 Exit (the first trigger to fire wins)

| # | Trigger | Rule |
|---|---|---|
| X1 | Signal decay | side·S ≤ `s_out` (structural; `s_out` = `hyst_ratio`·`s_in`), or side·D ≤ `d_out` (dislocation), for `confirm_exit_days` (1). Evaluated on the package's generic (constant-maturity) cell. A sign change of the score always fires X1 |
| X2 | Max hold | Days held ≥ `max_hold` (63) |
| X3 | Stop | Cumulative trade P&L per DV01 **of the position held** (signed by side), including costs, ≤ −`stop_k` · R_entry · √(`max_hold`/252). `stop_k` = 1.5 |
| X4 | Gamma flip | G1 fails for `gflip_days` (3). The curve no longer carries the gamma the straddle offsets, on either side |
| X5 | Take profit | P&L ≥ `tp_k` · R_entry · √(`max_hold`/252). Default off |
| X6 | Data | The option tail or a curve leg is missing for more than 2 days |

### 6.4 Option roll
- When the remaining expiry τ ≤ `roll_fraction` · τ₀ (0.5): close the straddle and open a new ATM straddle with the same (τ₀, m) and the same side (sell for +1, buy for −1), re-sized to the current curve gamma (§7.4).
- **At each roll, re-check X1 first.** If it fires, close the whole package instead of rolling.
- If `reset_curve_at_roll` (default false), also close the aged curve and reopen the generic pair at the roll, paying costs.

### 6.5 Portfolio constraints
- `max_positions` = 4 and `max_per_strip` = 2.
- `no_shared_legs` = true: two open packages may not share a forward leg (same start and tenor).
- `max_per_tail` = 3.
- `gross_dv01_cap` = $200k of total curve DV01.
- Side +1 and side −1 packages on different curves may be open at once. Their option vegas partly offset; report net vega daily (§17), but don't constrain it.

---

## 7. Position lifecycle and marking

Most backtests of this trade go wrong here.

The "ticket" model below describes the economics precisely. It is not a required data structure. Any position representation works if it reproduces the ageing, marking and hedging behaviour, which tests T1, T7 and T10 check.

### 7.1 Curve tickets
- A ticket is `(sgn, T₀, n, K, N, t₀)`, with `sgn` = +1 for receive and −1 for pay. Side +1 opens `l` at sgn +1 and `s` at −1; side −1 the reverse.
- **Ageing:** on day `t`, the ticket's age is `a = (t − t₀)/365.25` years and its live start is `T = T₀ − a`.
- **Marking:**
  - Rate: `f̃(t)` = the tenor-`n` strip on day `t` interpolated at `T`, using the same 3-point quadratic as the roll.
  - Annuity: `Ã = A(T, n, f̃)`.
  - Value: `V = sgn · N · Ã · (K − f̃)/10⁴`, with rates in bp.
- **Never mark at the constant-maturity series.** A 20y5y ticket opened 40 days ago is now about a 19.89y5y.
- **Entry:** set `N_l`, `N_s` so that DV01_l = DV01_s = the target DV01 (§10). Strikes are the market forwards at entry, so both legs start at zero value.
- **Reconciliation:** on a day with no market move, the ticket's ageing P&L must equal the screener's carry for that day. This is engine test T7.

### 7.2 Curve rebalancing (this is how the gamma is harvested)
- Γ_c = Σ_i 2·DV01_i·D_i·10⁻⁴ (signed). Net delta is Δ_net = Σ_i ∂V_i/∂(parallel bp).
- **Rule `hedge_mode: move` (default):** re-hedge when |Δ_net| ≥ |Γ_c|·`hedge_threshold_bp` (25bp). In other words, re-hedge when the equivalent parallel move since the last hedge reaches the threshold.
- **Rule `hedge_mode: time`:** re-hedge every `hedge_freq_days`.
- **Action:** open a **new ticket** at today's market rate on `hedge_leg` (default `short`, i.e. the generic forward of the pay leg) that brings Δ_net to 0. Do not edit existing notionals. Every ticket is aged and marked independently.
- Without rebalancing, the trade only earns gamma on the move from entry to exit. Rebalancing turns realised variance, sampled at the hedge interval, into P&L.

### 7.3 Option tickets
- A ticket is `(m, t_x, K, N_o, sgn = −side)`: a short straddle for side +1, a long straddle for side −1.
  - Remaining expiry: `τ(t) = (t_x − t)/365.25`.
  - Underlying: `F(t) = y_m(t)`, the spot proxy.
  - Strike: `K = F(t₀)` (ATM at entry).
- **Vol at τ:** interpolate linearly in total variance `w(τ) = σ²τ` across tail `m`'s quoted expiries on day `t`. Below the shortest quote, keep σ flat; above the longest, keep it flat. Use ATM vol at the strike; no smile is available (§18).
- **Value:** `P = sgn · N_o · A_m · Bach(F, K, σ, τ)`, with A_m the annuity of an `m`-year swap at y_m. In Bachelier (normal model) terms, with `d = (F−K)/(σ√τ)`:
  - straddle value `(F−K)(2Φ(d)−1) + 2σ√τ·φ(d)`
  - Δ = 2Φ(d)−1
  - Γ = 2φ(d)/(σ√τ)
  - vega = 2√τ·φ(d)
  - θ = −σφ(d)/√τ
- **Entry size:** option underlying DV01 = k × curve DV01, with k from `option_sizing` (§5.3). The default, k_opt, matches Γ_opt to Γ_c at entry.

### 7.4 Option hedging and gamma re-match
- **Delta hedge:** trade an `m`-year spot swap. Trigger when |F − F_last_hedge| ≥ `opt_hedge_threshold_bp`. The default is `same_as_curve`, so both legs sample realised variance on the same grid; different grids add basis the signal doesn't see.
- **Gamma re-match:** if `|Γ_opt/Γ_c − 1| > rematch_band` (0.25), change the option notional at the current mark to restore the ratio to 1, paying vega-based costs.
  - This drift happens as the option ages (gamma rises into expiry) and as F moves away from K (gamma falls).
- **Expiry or roll:** close at mark (intrinsic value at τ = 0).
- **Short expiries are effectively unhedged.** At about 90 vol, the expected move over the roughly 10 business days a 1M straddle is held (roll at 0.5·τ₀) is about 18bp at one standard deviation. So with `hedge_threshold_bp` ≥ 25 and τ₀ ≤ 3M, the option leg is rarely delta-hedged before it rolls. This is the intended same-grid sampling, not an engine bug. The `opt_hedge_threshold_bp: daily` ablation shows the alternative.

---

## 8. P&L and attribution

### 8.1 P&L
`PnL_t` is the sum over all tickets (curve, curve-hedge, option, option-hedge) of `ΔV`, minus costs, plus funding.

Funding is off by default (`fund_premium: false`) because no short rate is available. If it is enabled, the premium received is invested at the rate given in `fund_rate`.

### 8.2 Daily Greek attribution (each ticket, `t−1 → t`)

| Component | Curve ticket | Option ticket |
|---|---|---|
| Carry / roll / theta | Ageing on the unchanged `t−1` curve | `P(τ−dt) − P(τ)` at unchanged F, σ |
| First order | −sgn·DV01·Δf̃ | Δ_o·ΔF·(DV01 scale) |
| Gamma | ½·sgn·Γ_i·Δf̃² | ½·Γ_o·ΔF² |
| Vega | — | vega_o·Δσ |
| Hedge | Hedge tickets' first-order + gamma | Hedge swap P&L |
| Residual | Full reval − sum | Full reval − sum |

Package-level buckets:
- `curve_roll`
- `slope` (net first-order curve P&L)
- `curve_gamma`
- `opt_theta`
- `opt_gamma`
- `vega`
- `hedge` (should offset the first-order option P&L)
- `costs`
- `residual`

**Acceptance:** Σ|residual| ≤ 10% of Σ|gross| over the run. Otherwise the engine is wrong, not the trade.

### 8.3 Edge decomposition (tests H4)

```
realised gamma edge = curve_roll + curve_gamma + opt_theta + opt_gamma
other               = slope + vega + hedge residue + costs
```

The buckets carry their signs, so the same formula applies to both sides. Report the decomposition separately for side +1 and side −1.

For each trade also report:
- **Ex ante:** side, σ_BE, σ_imp and S at entry.
- **Ex post:**
  - the curve's realised effective vol at the hedge interval;
  - the option underlying's realised vol at the hedge interval;
  - realised Σ(curve_gamma + opt_gamma) vs the forecast basis B·(days/252).

This shows whether a loss came from the price (σ_BE vs σ_imp), the basis, the slope or vega.

---

## 9. Costs

All costs are charged at execution and multiplied by `cost_mult`.

| Item | Formula | Key | Default |
|---|---|---|---|
| Curve entry and exit | half-spread × curve DV01, per package | `curve_pkg_halfspread_bp` | 0.40bp |
| Curve rebalance | per hedge × DV01 traded | `curve_hedge_cost_bp` | 0.35bp |
| Option entry, exit, roll, re-match | half-spread (normal vol bp) × vega × N_o | `opt_halfspread_vol` | 1M 0.75 / 3M 0.60 / 6M 0.50 / 1Y 0.50 / longer 0.50 |
| Option delta hedge | × DV01 traded | `opt_hedge_cost_bp` | 0.10bp |

- **Anchor:** Citi *Trading long-dated convexity* (May-2019), Fig 9: 0.75–1bp to initiate a curve package and 0.3–0.4bp per hedge.
- These are placeholders until a dealer quotes the actual packages.
- Costs are the same for both sides: buying the straddle crosses the offer by the same half-spread as selling crosses the bid.
- **Required sweep:** `cost_mult` ∈ {0, 0.5, 1, 2}. Tuning uses 1×. H6 uses 2×.

---

## 10. Sizing

- **Unit:** 1 curve DV01. Option underlying DV01 = k × curve DV01 (k_opt by default; §5.3 `option_sizing`).
- **`sizing: inverse_risk` (default):** curve DV01 = `target_vol_usd` / R_entry, capped at `max_dv01`. Defaults are $250k/yr per position and a $50k cap. For example, R = 12.5bp/yr gives DV01 ≈ $20k.
- **`sizing: fixed`:** curve DV01 = `unit_dv01` ($10k) for every position.
- **`sizing: signal`:** inverse_risk × min(|S|/s_in, `signal_cap`) (cap 2).
- **Portfolio vol target:** off by default (`port_vol_target`). If on, scale all positions to a target using a 126-day covariance of unit-package daily P&L with Ledoit-Wolf shrinkage. Changes to the scaling are made only at entries and rolls, not daily.

---

## 11. Controls

These decide whether the trade is real.

| # | Control | Construction | What it isolates |
|---|---|---|---|
Every control keeps the package's side: a side −1 trade's option-only control is a **long** straddle, and its curve-only control is a steepener.

| # | Control | Construction | What it isolates |
|---|---|---|---|
| C1a | Option-only, copied dates | The package's straddle leg alone, same side. Entry and exit dates are **copied** from the package run (no stop of its own); same Γ-sizing, hedging and rolls | Leg attribution for H3 and H4 |
| C1b | Option-only, standalone | The same straddle run under its own §6 rules, so the stop is on its own P&L | Realistic short-vol (side +1) or long-vol (side −1) benchmark |
| C2 | Curve-only | The package's curve leg alone, same side, with entry and exit dates copied from the package run and the same rebalancing; no option | Curve gamma plus slope drift |
| C3a | Always-on, side +1 | Every gated cell (matched tail), always flattener + short straddle, held continuously, equal risk, rolled per §6.4. This is v0.1's C3 | Whether choosing a side and timing add value over the v0.1 trade |
| C3b | Always-on, signed | Every gated cell, always on the side sign(S), with `s_in` = 0 | Whether the entry threshold adds value |
| C4 | Random entry | Entry dates and sides drawn at random from gated days, same number of trades per side and same holding-period distribution; 1,000 draws | p-value of the real Sharpe |
| C5 | Inverted signal | Take the side opposite to sign(S) whenever \|S\| ≥ `s_in` | Should lose. If it doesn't, the score is noise |

**How to read them**
- Package ≈ C1a in Sharpe and drawdown: the curve contributes nothing.
- Side +1 Sharpe ≈ C1a but drawdown smaller in stress windows: the curve is a **hedge**. Report it as "short vol, hedged with curve gamma".
- Side −1 ≈ C1a: it is a long-vol position, and the curve's roll-down only pays part of the theta.
- Package > C1b and C2, H4 holds: the trade is the convexity-RV trade it claims to be.
- C2 strongly positive with large `slope` P&L: the curve leg is a slope bet in disguise. For side −1 on belly pairs, also check C2's `curve_roll` share: if it dominates, side −1 is the belly carry trade (§0).
- H2 is judged against C3a (combined book, side +1) and against C3b's side −1 days (side −1).

---

## 12. Stage A — signal evaluation (no portfolio logic)

Run Stage A first. Stage B (§13) runs only for signals that pass it.

**Unit-package panel**
- For each matched cell (one per curve) and each start date `t` (every `panel_step` = 5 days to limit overlap):
  - open a 1-DV01 package at `t + lag`;
  - hold it `h` days under §7's hedging and roll rules, with no exits other than the horizon;
  - run it with costs off and with costs on.
- The panel always holds the **side +1** unit package. With costs off, side −1's return is exactly −u, so the signed score S is tested against u directly and one IC covers both sides. With costs on, side −1's return is −u_precost − costs (costs hit both sides), so the costed side split is computed from the signed position, not by flipping u.
- Normalised return: `u = PnL / (R_t · √(h/252))`.
- Horizons: `panel_horizons` = {5, 21, 63, 126}.

**Tests**

| Test | Detail |
|---|---|
| Pooled IC | Spearman(S_t, u) over all (cell, t). NW t-stat with lag = h/`panel_step` |
| Time-series IC by curve | Spearman(S_t, u) per curve. Report the distribution across the 16 curves |
| Cross-sectional IC by date | Spearman across curves on each date. Reported, but power is low with 16 names |
| Quintile sort | Pooled quintiles of S: mean u and NW t per quintile; Q5−Q1 |
| IC decay | IC vs h |
| Subperiods | Thirds of the development window |
| Repeat for D | Same tests, using the dislocation score |
| Side split | Mean u when S ≥ s_in (side +1 should be > 0) and when S ≤ −s_in (side −1 should be < 0), each with NW t |
| Side persistence | Per curve, the share of days with S > 0 vs S < 0, and the longest run on each side |

- **Caution on pooled IC.** Pooled IC mixes curves that sit on one side almost permanently (belly pairs negative, long-end pairs positive). So it can be high just because the belly and the long end earn different average returns, with no timing skill. The per-curve time-series IC is the read on timing.

**Pass (H5):** at h = 63, pooled IC > 0 with NW t ≥ 2; Q5−Q1 > 0 with t ≥ 2; at least 3 of 4 quintile steps increasing; same sign in at least 2 of 3 subperiods; median per-curve time-series IC > 0. Per side: the side-split mean has the right sign with |NW t| ≥ 2.

---

## 13. Stage B — strategy simulation

### 13.1 Walk-forward
- In-sample (IS) window: `wf_is_years` = 3, expanding (`wf_expanding: true`). Out-of-sample (OOS) window: `wf_oos_years` = 1. Step: `wf_step_years` = 1.
- The OOS years are stitched into one OOS track record. The holdout is excluded.

### 13.2 Tuning protocol
- **Tuned set (at most 6):** `s_in`, `d_in` (dislocation mode only), `hedge_threshold_bp`, `rv_halflife`, `max_hold`, `roll_fraction`. Everything else is **fixed** at its Appendix A default.
- **Objective:** IS net Sharpe at 1× costs.
- **Plateau rule:** choose the grid point whose **median objective over its ±1-step neighbourhood** is highest, not the single peak. Report the full grid as heatmaps.
- Choice of `signal_mode` and `option_select` counts toward the number of trials (§14).

### 13.3 Ablations (fixed parameters, OOS track record)
- `sides` = [+1] (v0.1) and `sides` = [−1]
- `max_side_persistence` = 0.6 (transient episodes only)
- `s_in_neg_mult` = 1.5
- Dislocation mode without G6 (pure relative rule)
- `disl_metric` = `both_legs`
- `w_M` = 1 (slope overlay on)
- `w_B` = 0 (no basis term)
- `carry_horizon` ∈ {0.5, 1.0}, with σ_imp at the matching expiry
- `option_sizing` = `premium` (JPM 2017 construction)
- `option_select` = `best` (any tail)
- `option_select` = `fixed` at 1Y on the matched tail. `matched` takes the maximum S over four expiries, which slightly inflates S; the expected result is no difference
- `opt_hedge_threshold_bp` = daily
- `rv_method` = rolling
- `hedge_mode` = time with daily hedging
- `reset_curve_at_roll` = true
- Each gate removed one at a time
- `lag` ∈ {2, 5}

### 13.4 Holdout
- Run **once**, with the parameters chosen in the final IS fold.
- Report the result whatever it is. If the holdout fails H1, the strategy fails H1.

---

## 14. Statistics and overfitting control

**Metrics, per run**
- Return and vol, in bp/yr per DV01 and in $
- Sharpe, Sortino, max drawdown, Calmar
- Hit rate, average win and loss, P&L per trade
- Skew, kurtosis, worst 1-day, 1-week and 1-month
- Time in market, turnover, cost drag as % of gross
- Factor regression of daily P&L on Δ10y, Δslope(x), Δσ_imp(1Y.30Y) and C1b's P&L; report alpha and its t-stat

**Inference**
- Newey-West t-stat on daily P&L, with lag = median holding period.
- Stationary block bootstrap (block = median holding period, 5,000 draws) for the Sharpe confidence interval and the H2 difference.
- **Deflated Sharpe ratio** (Bailey & López de Prado), using the trial count N from the trial log:

```
SR*  = √V[SR_trials] · ((1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)))      γ = 0.5772
DSR  = Φ( (SR̂ − SR*)·√(T−1) / √(1 − skew·SR̂ + ((kurt−1)/4)·SR̂²) )   per-period SR, T = number of obs
```

- Probability of backtest overfitting (combinatorially symmetric cross-validation, 16 blocks): optional, report if run.

**Trial log (mandatory)**
- Every run appends its config hash, timestamp, mode, the tuned values and the IS and OOS metrics.
- N in the DSR is the number of rows in the log, not the number the researcher remembers.

---

## 15. Regime reporting

Report Sharpe, P&L and maximum drawdown, separately for each side:
- by **σ_imp quintile** (1Y.30Y) at entry;
- by **realised-minus-implied** quintile;
- over these named stress windows (`stress_windows`):

| Window | Dates |
|---|---|
| COVID | 2020-02-20 → 2020-04-30 |
| 2022 hiking | 2022-01-01 → 2022-12-31 |
| SVB | 2023-03-01 → 2023-03-31 |
| Tariff shock | 2025-04-01 → 2025-04-30 |

Short-straddle strategies usually succeed or fail in weeks like these. Side −1 should do well in them; its weak spot is long calm stretches, which the σ_imp-quintile table shows. H3 is judged here.

---

## 16. Engine known-answer tests (run before any real backtest)

These are stated as properties the implementation must satisfy, whatever form it takes. Implement them with the platform's own test tooling.

| # | Test | Pass |
|---|---|---|
| T1 | Flat strip, zero moves | Curve P&L = 0 to 1e-9; option P&L = theta only |
| T2 | Bachelier Monte Carlo, σ_real = σ_imp, daily hedge, 5,000 paths | Mean hedged straddle P&L ≈ 0 within 2 standard errors |
| T3 | Same, with σ_real = 2·σ_imp | Mean ≈ ½Γ(σ_r² − σ_i²) integrated over the path, within 2 standard errors |
| T4 | Ho-Lee-consistent synthetic curve (forwards with convexity bias at σ, zero term premium) | σ_BE recovers σ within 5%. **Also record the ratio σ_BE,recovered / σ_true as a bias factor and report it next to every σ_BE.** It measures the combined error of the two approximations, leg Γ ≈ 2·DV01·D_A and spot-unchanged roll ≈ risk-neutral drift. A 5% bias is about 4.5 vol, which is as large as the whole edge on fairly priced pairs |
| T5 | Gamma-match identity (as in the screener) | Θ_opt = 10⁻⁴·ΔD·σ² and Γ_opt = 2·ΔD·10⁻⁴ per DV01, both within 0.1% |
| T6 | Look-ahead injection: signal shifted 1 day into the future | Sharpe rises sharply. If it doesn't, the pipeline is already using future data or the signal has no information |
| T7 | Zero-move day reconciliation | Curve ageing P&L = screener carry · Δdays/365.25 per DV01; package = E · Δdays/365.25. Δdays is calendar days, so a Friday→Monday step is 3× a midweek step. Carry and theta are per calendar year; vols are annualised with √252 |
| T8 | Date permutation of the signal | Sharpe ≈ 0 (\|SR\| < 2 standard errors) |
| T9 | Attribution residual on a real 1-year run | ≤ 10% of gross |
| T10 | Side symmetry: same cell, same dates, side +1 vs side −1, costs off | Daily P&L of side −1 = −(side +1) to 1e-9, every bucket. Hedge triggers depend on \|Δ\| only, so both sides hedge on the same days. A failure means a sign is hard-coded somewhere |
| T11 | Premium vs theta | Gamma-matched straddle premium = 2·10⁻⁴·ΔD·σ²·τ₀ within 0.1%. In `premium` mode, k/k_opt = σ_BE²/(2σ_imp²) within 0.1% |
| T12 | Carry horizon | Flat strip: c_h = 0 for every h. Quadratic strip: c_h equals the analytic finite difference to 1e-9. As h → 0 (1e-4), c_h tends to c within 1e-6 bp/yr |

---

## 17. Outputs

- **Trade blotter:** entry and exit dates, cell, side, triggers, sizes, ex-ante and ex-post figures (§8.3).
- **Daily P&L** by bucket (§8.2), per position, per side and in total. Daily net option vega.
- **Side persistence table:** per curve, the share of days on each side and the longest run (§12).
This is the content that has to be produced. The format and tooling are the platform's choice.

- **Report** containing:
  - equity curves for the package (combined, side +1, side −1) and C1a, C1b, C2, C3a, C3b;
  - drawdown;
  - stacked attribution;
  - rolling 1-year Sharpe;
  - Stage A quintile bars and IC decay;
  - tuning heatmaps;
  - contribution by curve;
  - the regime table;
  - the cost sweep;
  - the H1–H6 scorecard with pass/fail, for the combined book and each side.
- **Trial log row** and **config hash** for each run.

---

## 18. Known limitations (state these in every report)

1. **No smile.** The ATM vol is used at a strike that drifts. Losses on short straddles after big moves are likely understated.
2. **Spot proxy.** `y_m` stands in for the option's 1y-forward underlying. Upgrade: build forward tails from the strips.
3. **Tail coverage.** 20Y and 30Y tails stop at 1Y expiry. Vol beyond 1Y is not available for the matched tails.
4. **Strip ends.** The roll derivative is one-sided at strip ends (30y5y, 20y10y), so it is noisier there.
5. **Funding.** MTM and premium funding are ignored by default.
6. **Snapshot timing.** Forwards and vols are assumed to be on the same close.
7. **Mid marks.** All marks are mid, with costs layered on. There are no margin, CSA or balance-sheet charges.
8. **Cross-gamma.** Vanna and volga are ignored. Vega P&L is first order only.
9. **Splice.** Forwards before 2021 may be a LIBOR splice (curve-only extension, §3).
10. **Term premium inside σ_BE.** σ_BE assumes all curve carry is convexity. In the belly, the hump's term-premium roll-down dominates, so a high σ_BE there is not evidence of rich curve gamma. Side −1 on belly pairs is the trade most exposed to this (T4, G7).

---

## Appendix A — Configuration

T = tuned in §13.2 · F = fixed (ablation only)

| Key | Default | Range | T/F | § |
|---|---|---|---|---|
| `lag` | 1 | {1, 2, 5} | F | 6.1 |
| `warmup_days` | 252 | — | F | 3 |
| `holdout_months` | 12 | — | F | 3 |
| `stale_policy` | drop_all_unchanged | {drop_all_unchanged, keep} | F | 3 |
| `min_dD` | 4.0 | {3, 4, 5} | F | 4 |
| `allowed_tails` | [20Y, 30Y] | + 10Y | F | 4 |
| `allowed_expiries` | [1M, 3M, 6M, 1Y] | + 2Y… | F | 4 |
| `option_select` | matched | {matched, best, fixed} | F* | 5.6 |
| `roll_interp` | quad3 | {quad3, linear} | F | 5.1 |
| `carry_horizon` | instant | {instant, 0.5, 1.0} | F | 5.1 |
| `option_sizing` | gamma | {gamma, premium} | F | 5.3 |
| `L_static` | 252 | {126, 252, 504} | F | 5.1 |
| `rv_method` | ewma | {rolling, ewma, blend} | F | 5.2 |
| `W` | 63 | {42, 63, 126} | F | 5.2 |
| `rv_halflife` | 21 | {10, 21, 42, 63} | T | 5.2 |
| `rv_shrink` | 0 | [0, 0.5] | F | 5.2 |
| `L_long` | 756 | — | F | 5.2 |
| `w_B` | 1 | {0, 0.5, 1} | F | 5.3 |
| `w_M` | 0 | {0, 1} | F | 5.3 |
| `kappa_min`, `kappa_max` | 0.05, 4 | — | F | 5.3 |
| `disl_metric` | be_ratio | {be_ratio, be_minus_imp, both_legs} | F | 5.4 |
| `L_disl` | 252 | {126, 252, 504} | F | 5.4 |
| `max_side_persistence` | off | {off, 0.6} | F | 5.5 |
| `L_persist` | 252 | — | F | 5.5 |
| `sides` | [+1, −1] | {[+1, −1], [+1], [−1]} | F* | 6.2 |
| `s_in_neg_mult` | 1 | {1, 1.5} | F | 6.2 |
| `min_side_trades` | 20 | — | F | 1 |
| `signal_mode` | structural | {structural, dislocation, blend} | F* | 6.2 |
| `s_in` | 0.25 | {0.10, 0.15, 0.25, 0.35, 0.50} | T | 6.2 |
| `hyst_ratio` | 0.2 | — | F | 6.3 |
| `d_in` | 1.0 | {0.5, 1.0, 1.5, 2.0} | T | 6.2 |
| `d_out` | 0.0 | — | F | 6.3 |
| `lambda_D` | 0.1 | — | F | 6.2 |
| `confirm_days` | 3 | {1, 3, 5} | F | 6.2 |
| `confirm_exit_days` | 1 | — | F | 6.3 |
| `cooldown_days` | 5 | — | F | 6.2 |
| `g_margin` | 0.02 | {0, 0.02, 0.05} | F | 5.5 |
| `rho_min` | 0.75 | {0.6, 0.75, 0.85} | F | 5.5 |
| `z_static_min`, `z_static_max` | −2.5, +inf | — | F | 5.5 |
| `k_vt`, `vt_max` | 10, 8 | — | F | 5.5 |
| `exit_gates` | [G1] | — | F | 5.5 |
| `max_hold` | 63 | {21, 42, 63, 126} | T | 6.3 |
| `stop_k` | 1.5 | {1, 1.5, 2, off} | F | 6.3 |
| `tp_k` | off | — | F | 6.3 |
| `gflip_days` | 3 | — | F | 6.3 |
| `roll_fraction` | 0.5 | {0.25, 0.5, 0.75} | T | 6.4 |
| `reset_curve_at_roll` | false | {false, true} | F | 6.4 |
| `max_positions` | 4 | {2, 4, 6} | F | 6.5 |
| `max_per_strip` | 2 | — | F | 6.5 |
| `no_shared_legs` | true | — | F | 6.5 |
| `max_per_tail` | 3 | — | F | 6.5 |
| `gross_dv01_cap` | 200000 | — | F | 6.5 |
| `hedge_mode` | move | {move, time} | F | 7.2 |
| `hedge_threshold_bp` | 25 | {10, 15, 25, 35} | T | 7.2 |
| `hedge_freq_days` | 1 | — | F | 7.2 |
| `hedge_leg` | short | {short, long, both_half} | F | 7.2 |
| `opt_hedge_threshold_bp` | same_as_curve | {same_as_curve, daily} | F | 7.4 |
| `rematch_band` | 0.25 | {0.15, 0.25, 0.5} | F | 7.4 |
| `fund_premium`, `fund_rate` | false, — | — | F | 8.1 |
| `cost_mult` | 1 | {0, 0.5, 1, 2} | F (sweep) | 9 |
| `curve_pkg_halfspread_bp` | 0.40 | — | F | 9 |
| `curve_hedge_cost_bp` | 0.35 | — | F | 9 |
| `opt_halfspread_vol` | {1M: .75, 3M: .60, 6M: .50, 1Y: .50, else: .50} | — | F | 9 |
| `opt_hedge_cost_bp` | 0.10 | — | F | 9 |
| `sizing` | inverse_risk | {inverse_risk, fixed, signal} | F | 10 |
| `target_vol_usd` | 250000 | — | F | 10 |
| `max_dv01` | 50000 | — | F | 10 |
| `unit_dv01` | 10000 | — | F | 10 |
| `signal_cap` | 2 | — | F | 10 |
| `port_vol_target` | off | — | F | 10 |
| `panel_step` | 5 | — | F | 12 |
| `panel_horizons` | [5, 21, 63, 126] | — | F | 12 |
| `wf_is_years`, `wf_oos_years`, `wf_step_years`, `wf_expanding` | 3, 1, 1, true | — | F | 13.1 |
| `accept_sharpe`, `accept_dsr` | 0.4, 0.95 | — | F | 1 |
| `stress_windows` | §15 table | — | F | 15 |

\* A categorical choice: each value tried adds to the trial count N.

## Appendix B — Default config

The default values from Appendix A, grouped for readability. This is an illustration, not a schema. Express it in whatever configuration format the platform uses.

```yaml
run:        {lag: 1, warmup_days: 252, holdout_months: 12, stale_policy: drop_all_unchanged}
universe:
  strips:   {5: [10, 15, 20, 25, 30], 10: [10, 12, 15, 20]}
  min_dD: 4.0
  allowed_tails: [20Y, 30Y]
  allowed_expiries: [1M, 3M, 6M, 1Y]
  option_select: matched
signal:
  roll_interp: quad3
  carry_horizon: instant
  option_sizing: gamma
  L_static: 252
  forecast:  {rv_method: ewma, W: 63, rv_halflife: 21, rv_shrink: 0.0, L_long: 756}
  package:   {w_B: 1.0, w_M: 0.0, kappa_min: 0.05, kappa_max: 4.0}
  dislocation: {disl_metric: be_ratio, L_disl: 252}
  mode: structural
  sides: [1, -1]
  entry:     {s_in: 0.25, s_in_neg_mult: 1.0, d_in: 1.0, lambda_D: 0.1, confirm_days: 3, cooldown_days: 5}
  exit:      {hyst_ratio: 0.2, d_out: 0.0, confirm_exit_days: 1, max_hold: 63,
              stop_k: 1.5, tp_k: null, gflip_days: 3}
  gates:     {g_margin: 0.02, rho_min: 0.75, z_static_min: -2.5, z_static_max: null,
              k_vt: 10, vt_max: 8.0, max_side_persistence: null, L_persist: 252, exit_gates: [G1]}
portfolio:  {max_positions: 4, max_per_strip: 2, no_shared_legs: true, max_per_tail: 3, gross_dv01_cap: 200000}
lifecycle:
  curve:    {hedge_mode: move, hedge_threshold_bp: 25, hedge_freq_days: 1, hedge_leg: short}
  option:   {opt_hedge_threshold_bp: same_as_curve, rematch_band: 0.25, roll_fraction: 0.5,
             reset_curve_at_roll: false}
  funding:  {fund_premium: false, fund_rate: null}
costs:
  cost_mult: 1.0
  curve_pkg_halfspread_bp: 0.40
  curve_hedge_cost_bp: 0.35
  opt_halfspread_vol: {1M: 0.75, 3M: 0.60, 6M: 0.50, 1Y: 0.50, default: 0.50}
  opt_hedge_cost_bp: 0.10
sizing:     {mode: inverse_risk, target_vol_usd: 250000, max_dv01: 50000, unit_dv01: 10000,
             signal_cap: 2.0, port_vol_target: null}
evaluation:
  stage_a:  {panel_step: 5, panel_horizons: [5, 21, 63, 126]}
  walk_forward: {is_years: 3, oos_years: 1, step_years: 1, expanding: true}
  tuned: [s_in, d_in, hedge_threshold_bp, rv_halflife, max_hold, roll_fraction]
  accept:   {sharpe: 0.4, dsr: 0.95, max_residual_frac: 0.10, min_side_trades: 20}
  stress_windows:
    covid:  [2020-02-20, 2020-04-30]
    hikes:  [2022-01-01, 2022-12-31]
    svb:    [2023-03-01, 2023-03-31]
    tariff: [2025-04-01, 2025-04-30]
```
