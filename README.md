# pricebt

pricebt is an event-driven backtester whose public API is a near 1:1 port of
`gs_quant.backtests` — Strategy, Triggers, Actions, `GenericEngine`, and the `BackTest` results —
but it contains **no pricing and no market data of its own**. Every number (a market object, a
resolved trade, a PV, a risk, an FX rate) comes from one self-contained YAML **asset config** per
tradable asset, whose Python expression strings name the library the user chooses; an external
library such as ARBS is named only inside those config files, never in pricebt's own source
(`src/pricebt`).

## Skills for AI agents

[`skills/README.md`](skills/README.md) is a skills library for AI agents (and people). It covers two jobs:

- **Hook up a pricing/data library and reach a first backtest fast.** You get a fast path, a discovery questionnaire, a fictional bank-SDK worked example and an automated config checker. For swaps, swaptions and bonds it also covers the IR risk-measure contract: what each measure means, how to derive it from your library by bump-and-reprice, why a swap, swaption or bond must map every one (a bond also maps its repo financing), and how to verify it. It also covers pricing and risking portfolios.
- **Take a plain-English strategy idea to a reviewed tearsheet.** The steps are intake questions, implementation recipes, adversarial review, spot checks, P&L attribution by greek and a report generator.

Start with [`skills/pricebt-start-here/SKILL.md`](skills/pricebt-start-here/SKILL.md).

## Quick start

```python
from datetime import date
from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.common import Currency, PayReceive
from pricebt.instrument import IRSwap
from pricebt.session import PricebtSession

PricebtSession.use(assets=["tests/assets/toy_usd_irs.yaml"])  # a deterministic, no-dependency toy market

swap = IRSwap(pay_or_receive=PayReceive.Pay, termination_date="10y", notional_currency=Currency.USD, notional_amount=1e4)
trigger = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m"), AddTradeAction(swap, "1m"))
backtest = GenericEngine().run_backtest(Strategy(None, trigger), start=date(2024, 1, 2), end=date(2024, 4, 2), frequency="1b")
print(backtest.result_summary)
```

Run the full version of this idea — a mean-reversion strategy trading the toy 10y swap — as
[`notebooks/040304_mean_reversion_toy.ipynb`](notebooks/040304_mean_reversion_toy.ipynb); the ARBS
equivalent is [`notebooks/040304_mean_reversion_usd_sofr_arbs.ipynb`](notebooks/040304_mean_reversion_usd_sofr_arbs.ipynb).

## Porting a gs_quant backtest notebook

A gs backtest notebook ports to pricebt with three edits:

1. change `gs_quant` to `pricebt` in its imports;
2. replace `GsSession.use(...)` with `PricebtSession.use(assets=[...])`, naming the asset config(s)
   the notebook's instruments should price against;
3. replace any GS `Dataset(...)` call with a local series — `pricebt.data.measure_series(...)` for
   a derived series (see the quick start above and [`docs/v2/DEVIATIONS.md`](docs/v2/DEVIATIONS.md)),
   or your own `pandas.Series` for raw data.

## Writing an asset config

[`docs/v2/ASSET_CONFIG_GUIDE.md`](docs/v2/ASSET_CONFIG_GUIDE.md) is the full guide: the schema,
units, evaluation rules, `build_on`, FX configs, a toy walkthrough, and the ARBS safety rules that
[`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`](configs/assets/usd_sofr_ois_interest_rate_swap.yaml)
follows. FX configs specifically are covered in [`configs/fx/README.md`](configs/fx/README.md).

## Interest-rate pricing and risk

pricebt ports gs_quant's IR pricing and risk surface for swaps, swaptions and bonds
([`docs/v2/IR_RISK_DESIGN.md`](docs/v2/IR_RISK_DESIGN.md)):

- **Measures.** The whole gs 2.1.17 catalogue (`IRDelta`, `IRDeltaParallel`, `IRGammaParallel`,
  `IRVega`, `IRVanna`, `IRVolga`, `IRFwdRate`, `IRAnnualImpliedVol`, `Theta`, `Annuity`,
  `Cashflows`, ...) importable from `pricebt.risk`, with measure parameters (`bump_size`, ...)
  passed through to the config functions that name them.
- **Contracts.** An `IRSwap`, `IRSwaption` or `Bond` asset config must map every measure of its
  class's contract (no declarations; the load error prints a mapping skeleton). A `Bond` config maps
  bond analytics (clean and dirty price, accrued, duration, convexity, settlement) and its repo
  financing, and the engine books a financed position's coupons and repo interest as cash, so a
  bond backtest's `Total` is the financed P&L ([`docs/v2/BOND_DESIGN.md`](docs/v2/BOND_DESIGN.md)).
  Loading fails otherwise. The tables,
  units and signs are in the guide's
  ["Measure contracts"](docs/v2/ASSET_CONFIG_GUIDE.md#measure-contracts-irswap-irswaption-bond) section.
- **Portfolio.** gs `Portfolio` and `PortfolioRiskResult` semantics: nested portfolios, paths,
  `subset`, `to_frame`, `aggregate`, historical results, `from_frame`/`to_csv`.
- **P&L decomposition.** `swaption_pnl_definition()`, `bond_pnl_definition()` and
  `ir_pnl_definition()` for `run_backtest(pnl_explain=...)`; `BackTest.pnl_explain_table()` puts the
  attribution next to the actual and economic (coupon-inclusive) P&L per step; and
  `PnlExplain(CloseMarket(date=...))` explains a book between two markets.

A runnable tour on the toy assets:
[`notebooks/ir_pricing_and_risk_toy.ipynb`](notebooks/ir_pricing_and_risk_toy.ipynb) (source
[`notebooks/src/ir_pricing_and_risk_toy.py`](notebooks/src/ir_pricing_and_risk_toy.py)).

## More

- [`docs/v2/README.md`](docs/v2/README.md) — reading order for the full design and research notes.
- [`docs/v2/DESIGN.md`](docs/v2/DESIGN.md) — requirements, architecture, contracts.
- [`docs/v2/IMPLEMENTATION_PLAN.md`](docs/v2/IMPLEMENTATION_PLAN.md) — phased build plan and gates.
- [`docs/v2/IR_RISK_DESIGN.md`](docs/v2/IR_RISK_DESIGN.md) — the IR pricing-and-risk design, and what was built.
- [`docs/v2/DEVIATIONS.md`](docs/v2/DEVIATIONS.md) — every intentional difference from gs_quant,
  with the test that covers it.
