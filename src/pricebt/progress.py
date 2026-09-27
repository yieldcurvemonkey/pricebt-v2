"""The ONE tqdm bar of a backtest run, and suppression of any other bar a called expression's own
library might draw while this one is active.

In a terminal the bar is tqdm's console bar (redrawn in place with a carriage return). Inside a
notebook KERNEL that would be one `stream` message per refresh, and a frontend that does not fold
carriage returns across messages prints one line per step; there the same tqdm bar is drawn into
ONE display output that every refresh replaces, which every frontend handles and which a saved
notebook keeps as a single final bar.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from tqdm import tqdm
from tqdm import std as _tqdm_std


class _OurBar(tqdm):
    """Marker subclass: the only bar allowed to draw while quiet_tqdm() is active."""


@contextmanager
def quiet_tqdm() -> Iterator[None]:
    """Force every tqdm created inside the block (except our bar) to disable=True. Restores the original __init__ on exit."""
    std_cls = _tqdm_std.tqdm
    orig = std_cls.__init__

    def patched(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if type(self) is not _OurBar:
            kwargs["disable"] = True
        orig(self, *args, **kwargs)

    std_cls.__init__ = patched  # type: ignore[method-assign]
    try:
        yield
    finally:
        std_cls.__init__ = orig  # type: ignore[method-assign]


def _notebook_display() -> Optional[Callable[..., Any]]:
    """`IPython.display.display` when this process is a notebook KERNEL, else None. IPython is never
    imported here, by name or otherwise: this only looks at `sys.modules`, so if nobody else has
    already imported IPython, this process is not a notebook and stays that way."""
    mod = sys.modules.get("IPython")
    ip = mod.get_ipython() if mod is not None and hasattr(mod, "get_ipython") else None
    if ip is None or not (type(ip).__name__ == "ZMQInteractiveShell" or "IPKernelApp" in (getattr(ip, "config", None) or {})):
        return None
    display_mod = sys.modules.get("IPython.display")
    display = getattr(display_mod, "display", None)
    return display


class _NotebookSink:
    """The file tqdm 'prints' to inside a notebook kernel: every refresh REPLACES one display output instead of appending a carriage-return chunk to the stream.

    The first refresh creates the output (`display(..., display_id=True)`), the others update that handle. A display that fails, or that hands back no handle (not attached to a kernel), never
    fails a run: the sink hands the text to stderr from then on, which is the console bar."""

    encoding = "utf-8"  # tqdm draws unicode block characters unless the file says otherwise

    def __init__(self, display: Callable[..., Any]):
        self._display = display
        self._handle: Any = None
        self._broken = False

    def isatty(self) -> bool:
        return False

    def flush(self) -> None:
        return None

    def write(self, s: str) -> int:
        if self._broken:
            sys.stderr.write(s)
            return len(s)
        text = s.replace("\r", "").strip()  # tqdm writes '\r' + bar + padding, and a final '\n' on close
        if not text:
            return len(s)
        bundle = {"text/plain": text}
        try:
            if self._handle is None:
                self._handle = self._display(bundle, raw=True, display_id=True)
                if self._handle is None:
                    raise RuntimeError("display() returned no handle")
            else:
                self._handle.update(bundle, raw=True)
        except Exception:
            self._broken = True
            sys.stderr.write("\r" + text)
        return len(s)


class ProgressBar:
    """Context manager owning the single bar of a run. `total` may grow as more work is discovered
    partway through (e.g. a new point is added to the run's timeline)."""

    def __init__(self, total: int, *, desc: str = "BACKTESTING...", show: bool = True, unit: str = "step", postfix_every: int = 200, mininterval: float = 0.1):
        self.total = total
        self.desc = desc
        self.show = show
        self.unit = unit
        self.postfix_every = postfix_every
        self.mininterval = mininterval
        self._bar: Optional[_OurBar] = None
        self._n = 0

    def __enter__(self) -> "ProgressBar":
        display = _notebook_display() if self.show else None
        where: dict = {"dynamic_ncols": True} if display is None else {"file": _NotebookSink(display), "ncols": 80}  # a ten-character bar is tqdm's default without a terminal
        self._bar = _OurBar(total=self.total, desc=self.desc, unit=self.unit, disable=not self.show, leave=True, mininterval=self.mininterval, **where)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._bar is not None:
            self._bar.close()

    def add_total(self, n: int = 1) -> None:
        self.total += n
        if self._bar is not None:
            self._bar.total = self.total
            self._bar.refresh()

    def update(self, n: int = 1, **postfix: float) -> None:
        self._n += n
        if self._bar is None:
            return
        self._bar.update(n)
        if postfix and self._n % self.postfix_every == 0:
            self._bar.set_postfix({k: f"{v:,.2f}" for k, v in postfix.items()}, refresh=False)
