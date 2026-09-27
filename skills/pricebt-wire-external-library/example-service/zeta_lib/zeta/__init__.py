"""zeta: a service-style rates pricing platform. Everything crosses the boundary as plain dicts; see ZETA_DOCS.md."""
from ._check import MEASURES
from ._engine import PILLAR_MONTHS
from .client import Client

__version__ = "2.3.1"
ERROR_CODES = {"Z101": "bad field", "Z204": "tenor not supported", "Z301": "curve node grid unsorted", "Z412": "unknown market_id", "Z530": "required fixing missing", "Z500": "internal error"}
__all__ = ["Client", "MEASURES", "PILLAR_MONTHS", "ERROR_CODES"]
