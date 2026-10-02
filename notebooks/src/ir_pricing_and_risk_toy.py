# %% [markdown]
# # IR pricing and risk on the toy assets: swaps, swaptions, bonds
#
# A tour of the interest-rate surface pricebt ports from gs_quant (`docs/v2/IR_RISK_DESIGN.md`),
# on the deterministic toy assets under `tests/assets/` (a flat-curve rates world, a Bachelier
# swaption, a toy bond master), so it runs anywhere with no market data:
#
# 1. the **measure contract** an `IRSwaption` config must satisfy;
# 2. pricing a Portfolio of an `IRSwaption`, a `Bond` and an `IRSwap` with several measures: scalars,
#    a bucketed delta ladder, a vega cube and the `Cashflows` table;
# 3. `Portfolio` paths, `subset`, `to_frame` and `aggregate`, and a historical calc;
# 4. measure parameters (`bump_size`, ...);
# 5. a delta-hedged swaption backtest explained by `swaption_pnl_definition()` and
#    `pnl_explain_table()`, a bond backtest whose coupon shows up as cash, and
#    `PnlExplain(CloseMarket(...))` between two dates.
#
# Only the imports and `PricebtSession.use(...)` differ from gs code.

# %%
import sys
from datetime import date

import pandas as pd

for _p in ("src", "tests"):  # the toy library lives under tests/ (run from the repo root)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pricebt.backtests.actions import AddTradeAction, HedgeAction
from pricebt.backtests.backtest_objects import bond_pnl_definition, swaption_pnl_definition
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements, PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.errors import NotSupportedError
from pricebt.instrument import Bond, IRSwap, IRSwaption
from pricebt.markets import CloseMarket, HistoricalPricingContext, PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import (Cashflows, IRDelta, IRDeltaParallel, IRFwdRate, IRGammaParallel, IRVega, IRVegaParallel,
                          PnlExplain, Price, Theta, contracts)
from pricebt.session import PricebtSession

ASSETS = ["tests/assets/toy_usd_irs_full.yaml", "tests/assets/toy_usd_swaption.yaml", "tests/assets/toy_usd_bond.yaml"]
PricebtSession.use(assets=ASSETS)    # replaces GsSession.use(...)
D = date(2024, 3, 4)

# %% [markdown]
# ## 1. The measure contract
#
# Every `IRSwap` and `IRSwaption` config must map each measure (and form) of its class's contract to
# a function with an allowed unit; `unsupported_measures:` cannot satisfy them (the strict classes,
# `docs/v2/IR_STRICT_CONTRACT.md`). A `Bond` config may still declare a measure it cannot compute,
# with a reason. Loading fails otherwise, listing every gap (DEV-I11). The contract is data in
# `pricebt.risk.contracts`:

# %%
contract = pd.DataFrame(
    [(r.measure, r.kind, ", ".join(r.forms), ", ".join(sorted(contracts.KINDS[r.kind][0] or ["frame"])))
     for r in contracts.contract_for("IRSwaption")],
    columns=["measure", "kind", "forms", "allowed units"],
)
contract

# %% [markdown]
# A swaption config that misses a measure gets a paste-ready mapping skeleton in the load error. Its
# `expr: '...'` stubs do not compile on purpose: each one must become the real computation (a
# literal 0.0 is honest only for the zero-by-convention measures, `contracts.ZERO_BY_CONVENTION`):

# %%
skeleton = contracts.mapping_skeleton("IRSwaption", [("IRVanna", "scalar"), ("IRVega", "bucketed")])
print(skeleton)

# %% [markdown]
# ## 2. Pricing a Portfolio
#
# A bought 1y10y ATM payer swaption, a long toy bond and a 10y pay-fixed swap, the two linear
# trades in a sub-portfolio. Each instrument is priced by the asset config that matches it.

# %%
swaption = IRSwaption("Pay", "10y", "USD", notional_amount=1e6, expiration_date="1y", strike="ATM", buy_sell="Buy", name="swaption")
bond = Bond(identifier="TOY 4.25 2034-11-15", size=1e6, buy_sell="Buy", settlement_currency="USD", name="bond")
swap = IRSwap("Pay", "10y", "USD", 1e6, fixed_rate="ATM", name="swap")
book = Portfolio((swaption, Portfolio((bond, swap), name="linear")), name="book")

with PricingContext(D):
    book.resolve()
    scalars = book.calc((Price, IRDeltaParallel, IRGammaParallel, IRVegaParallel, Theta, IRFwdRate))

scalars.to_frame()

# %% [markdown]
# Units: `Price` and `Theta` in USD (Theta per calendar day), deltas per +1bp of each instrument's
# own rate (`IRFwdRate`: swap par rate, swaption forward, bond yield, all in bp), gamma per bp², vega
# per bp of normal vol. The bond's delta is negative (long bond, per +1bp of yield).
#
# Bucketed forms: the book's delta ladder (summed across instruments by pillar), the swaption's
# vega cube keyed `'<tail>;<expiry>'`, and the bond's `Cashflows` table.

# %%
with PricingContext(D):
    ladder = book.calc(IRDelta).aggregate()
    cube = swaption.calc(IRVega).result()
    flows = bond.calc(Cashflows).result()
ladder

# %%
cube[cube.value.abs() > 1.0]

# %%
flows.head()

# %% [markdown]
# ## 3. Portfolio paths, subset, to_frame, aggregate, history

# %%
print([str(p) for p in book.all_paths])
bond_path = book.paths("bond")
print(bond_path, book[bond_path[0]].name)

linear = scalars.subset(book.paths("swap") + bond_path)
linear.to_frame()

# %%
book_delta = scalars[IRDeltaParallel].aggregate()
hedge_ratio = -book_delta / float(scalars[swap][IRDeltaParallel])  # swaps that flatten the book
print(f"book IRDeltaParallel {float(book_delta):,.2f} USD/bp; hedge with {hedge_ratio:.3f} x the swap")

# %%
with HistoricalPricingContext(date(2024, 3, 4), date(2024, 3, 8)):
    history = book.calc(Price)
history.aggregate()

# %% [markdown]
# ## 4. Measure parameters
#
# `bump_size`, `finite_difference_method`, `local_curve` and `scale_factor` reach a config function
# as `pricebt_bump_size`, ... (DEV-I10). None of the toy functions takes a bump size, so pricebt
# refuses the request instead of silently ignoring it:

# %%
try:
    with PricingContext(D):
        swaption.calc(IRDelta(aggregation_level="Type", bump_size=5))
except NotSupportedError as exc:
    print(exc)

# %% [markdown]
# ## 5. P&L decomposition
#
# ### A delta-hedged swaption
#
# The swaption bought on the first day, delta-hedged daily with a 10y payer held for one business
# day. `swaption_pnl_definition()` attributes each step to delta, gamma, vega, vanna, volga and theta;
# `pnl_explain_table()` puts that next to the actual P&L. The hedges add delta, gamma and theta but no
# vol terms (a swap's vega is 0).

# %%
start, end = date(2024, 1, 2), date(2024, 1, 31)
hedge = HedgeAction(IRDeltaParallel, IRSwap("Pay", "10y", "USD", 1e6, fixed_rate="ATM", name="hedge"), "1b", name="Hedge")
triggers = [
    DateTrigger(DateTriggerRequirements(dates=[start]), [AddTradeAction(IRSwaption("Pay", "10y", "USD", notional_amount=1e6, expiration_date="1y", buy_sell="Buy", name="swaption"), name="Add")]),
    PeriodicTrigger(PeriodicTriggerRequirements(start_date=start, end_date=end, frequency="1b"), [hedge]),
]
hedged = GenericEngine().run_backtest(Strategy(None, triggers), start=start, end=end, frequency="1b",
                                      pnl_explain=swaption_pnl_definition(), show_progress=False)
table = hedged.pnl_explain_table()
table.round(2).head()

# %%
residual_summary = pd.Series({
    "steps": len(table),
    "sum |actual_pnl|": table["actual_pnl"].abs().sum(),
    "sum |residual_pnl|": table["residual_pnl"].abs().sum(),
    "max |residual_pnl|": table["residual_pnl"].abs().max(),
    "residual / actual": table["residual_pnl"].abs().sum() / table["actual_pnl"].abs().sum(),
})
residual_summary.round(4)

# %% [markdown]
# ### A bond across a coupon date
#
# `Price` drops each coupon on its payment date and `Cashflows` lists the flows still to drop, so
# `cashflow_pnl` carries the coupon (face 1mm x 4.25% / 2 = 21,250) on the step that pays it, and the
# economic P&L stays smooth.

# %%
bond_bt = GenericEngine().run_backtest(
    Strategy(None, DateTrigger(DateTriggerRequirements(dates=[date(2024, 5, 6)]), [AddTradeAction(Bond(identifier="TOY 4.25 2034-11-15", size=1e6, buy_sell="Buy", settlement_currency="USD", name="bond"), name="Add")])),
    start=date(2024, 5, 6), end=date(2024, 5, 20), frequency="1b", risks=[Cashflows],
    pnl_explain=bond_pnl_definition(), show_progress=False)
bond_table = bond_bt.pnl_explain_table()
bond_table[["actual_pnl", "cashflow_pnl", "economic_pnl", "explained_pnl", "residual_pnl"]].round(2)

# %% [markdown]
# ### `PnlExplain` between two dates
#
# gs's instrument-level explain (notebook 030007): full revaluation of the book from the market of
# one date to the market of another, by risk factor. The time component is not part of it.

# %%
F, T = date(2024, 3, 4), date(2024, 3, 11)
explain = PnlExplain(CloseMarket(date=T))
with PricingContext(pricing_date=F):
    explained = book.calc((Price, explain))
with PricingContext(pricing_date=F, market=CloseMarket(date=T)):
    moved = book.calc(Price)
explain_rows = explained[explain].aggregate()
print(f"market move at fixed time: {float(moved.aggregate()) - float(explained[Price].aggregate()):,.2f} USD")
explain_rows
