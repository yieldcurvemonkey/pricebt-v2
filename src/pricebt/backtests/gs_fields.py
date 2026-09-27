"""gs_quant vocabulary the compat facade must recognise (C6): the constructor fields of `gs_quant.instrument.IRSwap` that describe conventions or
servicing rather than terms. `pricebt.instrument` refuses each one unless it is None, a zero fee, or a value the session's stack declares it accepts; the
list only lets it tell a gs field (NotSupportedError: the registered spec's conventions define it) from a typo (TypeError)."""
from __future__ import annotations

SWAP_GS_FIELDS = (
    "principal_exchange", "floating_rate_for_the_initial_calculation_period", "floating_rate_option", "floating_rate_designated_maturity",
    "floating_rate_spread", "floating_rate_frequency", "floating_rate_day_count_fraction", "floating_rate_business_day_convention", "fixed_rate_frequency",
    "fixed_rate_day_count_fraction", "fixed_rate_business_day_convention", "fee", "fee_currency", "fee_payment_date", "clearing_house", "fixed_first_stub",
    "floating_first_stub", "fixed_last_stub", "floating_last_stub", "fixed_holidays", "floating_holidays", "roll_convention",
    "fixed_rate_accrual_convention", "floating_rate_accrual_convention",
)
