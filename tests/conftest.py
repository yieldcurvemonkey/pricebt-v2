import warnings

import pytest

warnings.filterwarnings("ignore", message=r"(?s).*Rateslib is source-available.*")

from guards.partition import pytest_collection_modifyitems  # noqa: F401  (spec G-5: an unmarked test, or a `core` test that needs a library, stops the run)
from pricebt.engine import Engine, EngineSettings
from pricebt.market import MarketData
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyMDP
from pricebt.timeutil import Clock, TimeContext, TimeGrid


@pytest.fixture
def tctx():
    return TimeContext()


@pytest.fixture
def grid(tctx):
    return TimeGrid.daily("2024-01-02", "2024-02-29", "1b", tctx)


def make_engine(grid, strategy=None, mdp=None, **settings):
    mdp = mdp or ToyMDP()
    market = MarketData({"primary": mdp}, Clock())
    settings.setdefault("show_progress", False)
    return Engine(grid, market, strategy or ScriptedStrategy(), EngineSettings(**settings)), market, mdp


@pytest.fixture
def mk():
    return make_engine


def assert_identity(rec, initial=0.0, tol=1e-9):
    """equity == initial + cash + tcost + positions_value on every row (docs/DESIGN.md section 6)."""
    eq = rec.equity
    lhs = eq["equity"]
    rhs = initial + eq["cash"] + eq["tcost"] + eq["positions_value"]
    assert (lhs - rhs).abs().max() < tol
