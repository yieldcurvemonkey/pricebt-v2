"""acmelib's own exception types (a foreign library never raises pricebt's errors: the adapter translates)."""


class AcmeError(Exception):
    """Base of everything acmelib raises."""


class NoValuationDate(AcmeError):
    """Every valuation needs the process-global valuation date (`acmelib.set_valuation_date`)."""


class BadInput(AcmeError):
    """A malformed date, tenor, code or number."""


class CalendarError(AcmeError):
    """An unknown calendar name, or a name registered twice with different holidays."""


class CurveError(AcmeError):
    """A date before the curve's anchor, or a curve not anchored at the valuation date."""


class MissingFixing(AcmeError):
    """A started swap without a published fixing for every business day since its start."""


class SwapExpired(AcmeError):
    """The swap has paid its last flow: it has no par rate."""
