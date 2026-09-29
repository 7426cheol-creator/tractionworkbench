"""Progress and cancellation inside long calculations (UX review A3, A5).

A loop that can run for seconds opens a span and takes one step per item::

    with progress.span(len(grid), "torque scan") as sp:
        for t in grid:
            sp.step()
            ...

A step is a point where the calculation may stop: nothing is half-computed there.  Without a listener (the command
line, the tests, the engine used as a library) opening a span costs one context-variable read and a step one
attribute test.

Spans nest.  A span opened during the third of ten steps of an enclosing span covers 0.2 .. 0.3 of the enclosing
span's range, and a span that closes has used its range up, so the reported fraction never goes back, whichever
loop reports (a second span opened in the same step reports its steps without moving the fraction).  The listener
also sees every open loop (label, step, total) from the outside in.  Spans follow the context (``contextvars``): a
thread reports only its own loops.

A listener stops the calculation by raising ``Cancelled`` from a step.  ``Cancelled`` is a ``BaseException``, like
``KeyboardInterrupt``: the engine turns a numerical failure into UNKNOWN with ``except Exception``, and a stop must
never become a verdict.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass


class Cancelled(BaseException):
    """The calculation was stopped on request: not an error, and never a result."""


@dataclass(frozen=True)
class Level:
    """One open loop as the listener sees it: step ``step`` of ``total`` (1-based; 0 before its first step)."""

    label: str
    step: int
    total: int
    detail: str = ""


Listener = Callable[[float, "Span"], None]


class Span:
    """One open loop: ``total`` steps sharing the part of the run the enclosing step gave it."""

    __slots__ = ("label", "total", "detail", "_done", "_pos", "_end", "_hi", "_parent", "_listener")

    def __init__(self, label: str, total: int, lo: float, hi: float, parent: Span | None, listener: Listener | None):
        self.label, self.total, self.detail = label, max(0, int(total)), ""
        self._done = 0
        self._pos = self._end = lo          # the current step's unused part: _pos .. _end
        self._hi = hi
        self._parent, self._listener = parent, listener

    @property
    def fraction(self) -> float:
        """How far the whole run is (0 .. 1)."""
        return self._pos

    def step(self, detail: str = "") -> None:
        """The next step starts: the listener hears where the calculation is, and may stop it here."""
        if self._listener is None:
            return
        self._pos = self._end               # the previous step is complete
        self._done += 1
        left = self.total - self._done + 1
        self._end = self._pos + (self._hi - self._pos) / left if left > 0 else self._pos
        self.detail = detail
        self._listener(self._pos, self)

    def remaining(self, n: int) -> None:
        """From here on the loop has ``n`` more steps: what is left of its range is shared among them, so a loop
        whose length becomes known late (a refinement after a scan) never moves the fraction back."""
        if self._listener is not None:
            self.total = self._done + max(0, int(n))

    def levels(self) -> tuple[Level, ...]:
        """The open loops from the outside in (the listener's own run is not one of them)."""
        out = []
        s: Span | None = self
        while s is not None and s._parent is not None:
            out.append(Level(s.label, s._done, s.total, s.detail))
            s = s._parent
        return tuple(reversed(out))


_NULL = Span("", 0, 0.0, 0.0, None, None)
_current: contextvars.ContextVar[Span | None] = contextvars.ContextVar("traction_workbench_progress", default=None)


@contextmanager
def span(total: int, label: str) -> Iterator[Span]:
    """A loop of ``total`` steps (see the module text); inert without a listener."""
    parent = _current.get()
    if parent is None:
        yield _NULL
        return
    sp = Span(label, total, parent._pos, parent._end, parent, parent._listener)
    token = _current.set(sp)
    try:
        yield sp
    finally:
        _current.reset(token)
        parent._pos = parent._end           # its range is used up


@contextmanager
def listening(listener: Listener) -> Iterator[Span]:
    """Report the loops run inside this block to ``listener(fraction, span)``; ``span.levels()`` names them.

    The listener runs at every step of every loop: it should be quick, and it stops the calculation by raising
    ``Cancelled``.  The block's own span (returned) is the whole run; the loops move the fraction only inside a
    stretch the run gave them (``at``), otherwise they name the steps they are at."""
    root = Span("", 1, 0.0, 1.0, None, listener)
    root._done = 1
    token = _current.set(root)
    try:
        yield root
    finally:
        _current.reset(token)


def at(root: Span, fraction: float, until: float | None = None) -> None:
    """The run is at ``fraction`` by its own count.  Loops opened from here on share ``fraction .. until``; without
    ``until`` they name the steps they are at without moving the fraction.  A run that counts its own fractions
    inside such a stretch would move the fraction twice: a stretch is either the run's or its loops'."""
    f = min(max(float(fraction), root._pos), 1.0)
    root._pos = f
    root._end = f if until is None else min(max(float(until), f), 1.0)
