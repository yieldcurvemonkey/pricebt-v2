# Formulas (with known answers)

Every formula below that is implemented in [`../scripts/research_stats.py`](../scripts/research_stats.py) has a known-answer test in `tests/skills/test_skill_research_stats.py`. Citation keys are as in [`principles.md`](principles.md).

## Significance

| Quantity | Formula | Known answer |
|---|---|---|
| Annualised Sharpe of daily P&L | √252 · mean / sd (ddof = 1). No risk-free rate: swap P&L is already excess of funding (Chan ch. 3 pp. 43–45). | constant P&L → undefined (sd = 0) |
| t-stat of the mean | ≈ SR · √years (GK ch. 5 p. 112) | SR 0.5 over 16 years → t = 2 |
| Standard error of SR | √((1 + SR²·dt/2)/years), with dt = 1/252 (GK ch. 17, reconstructed) | ≈ 1/√years for small SR |
| Years to t = 2 | (2/SR)² (GK ch. 17 p. 480) | SR 0.5 → 16; SR 1 → 4 |
| P(up over H years) | Φ(SR·√H) (GK ch. 17 p. 481) | SR 0.5: 56% over one month, 87% over five years |
| P(≥ 1 false positive in n trials) | 1 − 0.95ⁿ (GK ch. 12 p. 337) | n = 20 → 64% |
| Bonferroni z | Φ⁻¹(1 − α/(2n)) | n = 1 → 1.96 |
| Book mistake to avoid | a "profit t-stat" of √N·(annualised return)/(annualised vol) overstates t by about √252 (AQM ch. 1 p. 33) | use √N·mean/sd of daily P&L |

## Skill and breadth

| Quantity | Formula | Known answer |
|---|---|---|
| Fundamental law | IR = IC · √BR (GK ch. 6 p. 148) | IC 0.1, BR 500 → IR 2.24 |
| Implied breadth | BR = (IR/IC)² (GK ch. 12 p. 328) | a check on "independent bets" |
| Hit rate ↔ IC | IC = 2p − 1 (GK ch. 6 p. 154) | IC 0.0577 → 52.885% |
| Independent sources add in quadrature | IR² = Σ IR_i² (GK ch. 6 p. 154) | 0.75, 0.50, 0.30 → 0.95 |
| Signal → alpha | α = vol · IC · z (GK ch. 10 pp. 265–267) | in rates: vol = rate vol in bp × dv01 |

## Sizing

| Quantity | Formula | Known answer |
|---|---|---|
| Kelly leverage | f = m/s² (Chan ch. 6 pp. 96–97) | m = 7.23%, s = 16.91% → f = 2.528 |
| Growth at leverage f | g = r + f·m − f²s²/2 (Chan ch. 6 pp. 112–113) | at f = 2.528 and r = 4% → 13.14% |
| Tail-loss leverage cap | tolerable one-period loss / worst historical one-period loss (Chan ch. 6 p. 105) | use min(half-Kelly, cap) |
| Optimal active risk | ω* = IR/(2λ) (GK ch. 5 p. 123) | IR 0.75, λ 0.10 → 3.75% |

## Mean reversion

| Quantity | Formula | Note |
|---|---|---|
| OU half-life | regress Δz_t on (z_{t−1} − mean), giving slope β; half-life = −ln 2/β (Chan ch. 7 pp. 141–142) | **β ≥ 0 means no mean reversion, and the half-life is undefined**, not large |
| z-score | (x − rolling mean)/rolling sd, using past data only | the gs `MeanReversionTrigger` window excludes today |
| Stationarity | ADF on a spread with fixed weights; Engle–Granger critical values only when the weights are estimated (Chan ch. 7 pp. 128–133) | cointegration ≠ correlation |

## Drawdown and returns

- **Drawdown of additive P&L (currency):** DD_t = cum_t − max_{s ≤ t} cum_s, and max drawdown = min_t DD_t. Report depth *and* longest time under water; they usually come from different episodes (Chan ch. 2 p. 21; ch. 3 pp. 48–49).
- **Percentage drawdown** needs a capital base. Divide by equity **at the peak**. One book's worked code divides by current equity, which overstates deep drawdowns; do not copy it (Chan ch. 3 pp. 48–49).
- **Returns on swaps.** A swap book has no capital base, so percentage returns, CAGR and Calmar need a stated capital definition: fixed notional, risk capital (k × worst one-period loss), or margin. Currency Sharpe needs none.

## Turnover and costs

- **Cost per period** = one-way cost × Σ|w_t − w_{t−1}| (Chan ch. 3 p. 65).
- **Amortised cost** = round-trip cost / holding period in years (GK ch. 14 p. 387).
- **Expected round turns** of an m-day moving-average rule on a random walk, per 250 days: m = 2 → 125, m = 5 → 67, m = 17 → 35, m = 61 → 19 (AQM ch. 11 pp. 338–339).
