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

- **Hook up a pricing/data library and reach a first backtest fast.** You get a fast path, a discovery questionnaire, a fictional bank-SDK worked example and an automated config checker.
- **Take a plain-English strategy idea to a reviewed tearsheet.** The steps are intake questions, implementation recipes, adversarial review, spot checks and a report generator.

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

## More

- [`docs/v2/README.md`](docs/v2/README.md) — reading order for the full design and research notes.
- [`docs/v2/DESIGN.md`](docs/v2/DESIGN.md) — requirements, architecture, contracts.
- [`docs/v2/IMPLEMENTATION_PLAN.md`](docs/v2/IMPLEMENTATION_PLAN.md) — phased build plan and gates.
- [`docs/v2/DEVIATIONS.md`](docs/v2/DEVIATIONS.md) — every intentional difference from gs_quant,
  with the test that covers it.
