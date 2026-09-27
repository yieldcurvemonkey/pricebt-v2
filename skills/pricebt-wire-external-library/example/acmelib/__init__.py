"""acmelib: a small FICTIONAL in-house rates library with a deliberately foreign API, the worked example of wiring an external library into pricebt.

ISO-string dates, lower-case tenors, decimal rates, receiver's-point-of-view values and risk, risk per one million, a pandas ladder with an extra bucket, a process-global
valuation date, a global calendar registry, its own exception types, and NO attribution. See `swap.py` for the list and for the honest caveat: its arithmetic is
reused from `pricebt.testing.refstack`, so it ties out against the reference stack to round-off (a real library is independent and shows noise floors).
"""
from .calendars import add_business_days, add_tenor, adjust, is_business_day, register_calendar
from .curve import Market, ZeroCurve
from .errors import AcmeError, BadInput, CalendarError, CurveError, MissingFixing, NoValuationDate, SwapExpired
from .state import set_valuation_date, valuation_date
from .swap import MM, Swap, bucket_risk, cashflows, gamma, par_rate, pv, pv01

__all__ = ["Swap", "ZeroCurve", "Market", "pv", "cashflows", "par_rate", "pv01", "bucket_risk", "gamma", "MM", "set_valuation_date", "valuation_date", "register_calendar",
           "add_business_days", "add_tenor", "adjust", "is_business_day", "AcmeError", "BadInput", "CalendarError", "CurveError", "MissingFixing", "NoValuationDate", "SwapExpired"]
