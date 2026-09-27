"""One bar, one line. tqdm's console bar redraws itself with a carriage return, one `stream` message per refresh; a notebook frontend that does not fold those messages back into a single
line (VS Code's does not) prints one line per step. Inside a notebook KERNEL the bar is therefore ONE display output that every refresh replaces (`update_display_data`); everywhere else
it is the console bar, unchanged."""
import sys
import types

import pytest

import pricebt.engine.progress as P
from pricebt.engine import Engine, EngineSettings
from pricebt.market import MarketData
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyMDP
from pricebt.timeutil import Clock, TimeContext, TimeGrid

pytestmark = pytest.mark.core


class FakeHandle:
    def __init__(self, log):
        self.log = log

    def update(self, bundle, **kw):
        self.log.append(("update", bundle, kw))


class FakeDisplay:
    """What `IPython.display.display(bundle, raw=True, display_id=True)` is to the sink: records every call, returns a handle that records its updates."""

    def __init__(self):
        self.log = []

    def __call__(self, bundle, **kw):
        self.log.append(("display", bundle, kw))
        return FakeHandle(self.log)

    def kinds(self):
        return [k for k, *_ in self.log]

    def texts(self):
        return [b["text/plain"] for _, b, *_ in self.log]


def in_a_notebook(monkeypatch):
    fake = FakeDisplay()
    monkeypatch.setattr(P, "_notebook_display", lambda: fake)
    return fake


def test_in_a_notebook_the_bar_is_one_display_output_updated_in_place(monkeypatch, capfd):
    fake = in_a_notebook(monkeypatch)
    with P.ProgressBar(5, mininterval=0) as bar:
        for _ in range(5):
            bar.update()
    kinds = fake.kinds()
    assert kinds[0] == "display" and kinds.count("display") == 1, "exactly ONE output is created"
    assert kinds.count("update") >= 5, "every step replaces it"
    text = fake.texts()
    assert all("\r" not in t and "\n" not in t for t in text), "no carriage return or newline reaches the frontend: nothing for it to fail to fold"
    assert text[0].startswith("BACKTESTING...:") and "0/5" in text[0]
    assert "100%" in text[-1] and "5/5" in text[-1]
    assert capfd.readouterr().err == "", "and nothing goes to stderr"


def test_every_display_call_asks_for_the_plain_text_of_the_bar_only(monkeypatch):
    fake = in_a_notebook(monkeypatch)
    with P.ProgressBar(2, mininterval=0) as bar:
        bar.update(2)
    assert all(set(b) == {"text/plain"} for _, b, *_ in fake.log)
    assert fake.log[0][2] == {"raw": True, "display_id": True}, "the first call creates the output with an id ..."
    assert all(kw == {"raw": True} for kind, _, kw in fake.log[1:] if kind == "update"), "... and the others update that same handle"


def test_the_bar_is_wide_enough_to_show_progress_in_a_notebook(monkeypatch):
    """tqdm's default in a stream without a terminal is a ten-character bar: eleven steps of resolution."""
    fake = in_a_notebook(monkeypatch)
    with P.ProgressBar(100, mininterval=0) as bar:
        bar.update(50)
    line = fake.texts()[-1]
    assert len(line) >= 70 and line.count("█") >= 8, line


def test_outside_a_notebook_the_bar_is_the_console_bar(capfd):
    with P.ProgressBar(3, mininterval=0) as bar:
        for _ in range(3):
            bar.update()
    err = capfd.readouterr().err
    assert P._notebook_display() is None, "pytest is not a notebook kernel"
    assert err.count("\r") >= 3 and "BACKTESTING..." in err and "3/3" in err


def test_a_disabled_bar_shows_nothing_anywhere(monkeypatch, capfd):
    fake = in_a_notebook(monkeypatch)
    with P.ProgressBar(3, show=False) as bar:
        bar.update(3)
    assert fake.log == [] and capfd.readouterr().err == ""


def test_a_display_that_fails_never_fails_the_run_and_falls_back_to_the_console_bar(monkeypatch, capfd):
    def broken(bundle, **kw):
        raise RuntimeError("no frontend attached")

    monkeypatch.setattr(P, "_notebook_display", lambda: broken)
    with P.ProgressBar(3, mininterval=0) as bar:
        for _ in range(3):
            bar.update()
    assert "BACKTESTING..." in capfd.readouterr().err


def test_a_display_that_returns_no_handle_falls_back_instead_of_writing_a_new_output_every_step(monkeypatch, capfd):
    calls = []
    monkeypatch.setattr(P, "_notebook_display", lambda: (lambda bundle, **kw: calls.append(bundle)))  # a `display` that is not attached to a kernel returns None
    with P.ProgressBar(3, mininterval=0) as bar:
        for _ in range(3):
            bar.update()
    assert len(calls) == 1, "one attempt, then the console bar"
    assert "BACKTESTING..." in capfd.readouterr().err


# ---- what counts as a notebook kernel ---------------------------------------------------------------------------------------------------------------------------------------------
def fake_ipython(monkeypatch, shell_class):
    shell = type(shell_class, (), {"config": {}})()
    ipython = types.ModuleType("IPython")
    ipython.get_ipython = lambda: shell
    display_mod = types.ModuleType("IPython.display")
    display_mod.display = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "IPython", ipython)
    monkeypatch.setitem(sys.modules, "IPython.display", display_mod)
    return display_mod.display


def test_a_notebook_kernel_is_recognised(monkeypatch):
    display = fake_ipython(monkeypatch, "ZMQInteractiveShell")
    assert P._notebook_display() is display


@pytest.mark.parametrize("shell_class", ["TerminalInteractiveShell", "InteractiveShell"])
def test_a_terminal_ipython_is_not_a_notebook(monkeypatch, shell_class):
    fake_ipython(monkeypatch, shell_class)
    assert P._notebook_display() is None


def test_ipython_that_was_never_imported_means_no_notebook_and_is_not_imported_by_the_check(monkeypatch):
    monkeypatch.delitem(sys.modules, "IPython", raising=False)
    assert P._notebook_display() is None
    assert "IPython" not in sys.modules, "detecting a notebook must not import anything"


def test_no_current_ipython_shell_means_no_notebook(monkeypatch):
    ipython = types.ModuleType("IPython")
    ipython.get_ipython = lambda: None
    monkeypatch.setitem(sys.modules, "IPython", ipython)
    assert P._notebook_display() is None


# ---- through the engine: still exactly one bar, and one output --------------------------------------------------------------------------------------------------------------------
def test_an_engine_run_in_a_notebook_creates_one_output_and_updates_it_once_per_point(monkeypatch, capfd):
    from tqdm import tqdm as TQ

    fake = in_a_notebook(monkeypatch)
    grid = TimeGrid.daily("2024-01-02", "2024-01-12", "1b", TimeContext())

    def fn(ts, view, submit):
        for _ in TQ(range(2), desc="a foreign bar"):  # created inside the run: forced off, as before
            pass

    eng = Engine(grid, MarketData({"primary": ToyMDP()}, Clock()), ScriptedStrategy(fn=fn), EngineSettings(show_progress=True))
    eng.run()
    assert fake.kinds().count("display") == 1
    assert not any("a foreign bar" in t for t in fake.texts())
    assert f"{len(grid)}/{len(grid)}" in fake.texts()[-1] and "100%" in fake.texts()[-1]
    assert capfd.readouterr().err == ""
