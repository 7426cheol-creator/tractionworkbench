"""Background computation: engine calls run in a thread pool, results come back as Qt signals.

Figures are always drawn in the GUI thread; workers only compute data.
In self-test mode (``TaskRunner.synchronous = True``) tasks run inline so results are deterministic.

A task function is called as ``fn(progress, *args)``.  ``progress(fraction, message)`` reports the task's own
stages; the loops inside the engine report their steps through ``traction_workbench.progress`` (the task listens
for its thread), so a long engine calculation says where it is and stops at its next step when cancelled.
``progress.partial(result)`` hands the page a first part of the result while the rest is computed.
"""

from __future__ import annotations

import time
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .. import progress as P
from ..errors import InputValidationError, OutsideModelDomain
from ..plots.labels import progress_label
from ..progress import Cancelled

SHOW_EVERY_S = 0.25         # engine steps are shown at most this often (every step still checks for a cancel)


def describe(levels) -> str:
    """The open engine loops as one line, outermost first: "1/3 verdict · torque capability 130/350 (scan)".
    A loop of a single step says nothing its enclosing loop does not."""
    parts = []
    for lv in levels:
        if lv.total <= 1 and not lv.detail:
            continue
        count = f"{min(lv.step, lv.total)}/{lv.total}" if lv.total > 1 else ""
        name = progress_label(lv.label) if lv.label else ""
        detail = progress_label(lv.detail) if lv.detail else ""
        if name:
            parts.append(" ".join(x for x in (name, count) if x) + (f" ({detail})" if detail else ""))
        else:
            parts.append(" ".join(x for x in (count, detail) if x))
    return " · ".join(parts)


class TaskSignals(QObject):
    progress = Signal(float, str)
    partial = Signal(object)
    result = Signal(object)
    error = Signal(str, str)
    finished = Signal(float)


class Progress:
    """What a task function gets as ``progress``: call it with (fraction, message) for the task's own stages;
    ``partial(result)`` shows a first part of the result (e.g. the verdict before its optional analyses)."""

    __slots__ = ("_task",)

    def __init__(self, task: Task):
        self._task = task

    def __call__(self, frac: float, msg: str = "", until: float | None = None) -> None:
        """The task is at ``frac`` doing ``msg``; with ``until`` the engine loops of this stage move the bar up to
        ``until`` (then the stage reports no fractions of its own)."""
        self._task._progress(frac, msg, until)

    def partial(self, result) -> None:
        self._task._partial(result)


class Task(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = TaskSignals()
        self._cancel = False
        self._root = None                   # the engine loops' listener span while the task runs
        self._stage = ""                    # the task's own last message
        self._shown_at = float("-inf")
        self.setAutoDelete(True)

    def cancel(self):
        self._cancel = True

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def _progress(self, frac: float, msg: str = "", until: float | None = None):
        if self._cancel:
            raise Cancelled()
        if self._root is not None:
            P.at(self._root, frac, until)
        self._stage = str(msg)
        self.signals.progress.emit(float(frac), str(msg))

    def _step(self, frac: float, span) -> None:
        """A step of an engine loop: stop here when cancelled; show where the calculation is (at most every
        ``SHOW_EVERY_S``: a fast loop does not flood the window with messages)."""
        if self._cancel:
            raise Cancelled()
        now = time.perf_counter()
        if now - self._shown_at < SHOW_EVERY_S:
            return
        self._shown_at = now
        msg = " · ".join(x for x in (self._stage, describe(span.levels())) if x)
        self.signals.progress.emit(float(frac), msg)

    def _partial(self, result) -> None:
        if self._cancel:
            raise Cancelled()
        self.signals.partial.emit(result)

    def run(self):
        t0 = time.perf_counter()
        try:
            with P.listening(self._step) as root:
                self._root = root
                res = self.fn(Progress(self), *self.args, **self.kwargs)
        except Cancelled:
            self.signals.error.emit("CANCELLED", "")
        except Exception as exc:  # noqa: BLE001 - surfaced to the user with the traceback
            self._failed(exc)
        else:
            if self._cancel:                # a stop absorbed on the way never becomes a result
                self.signals.error.emit("CANCELLED", "")
            else:
                self.signals.result.emit(res)
        finally:
            self._root = None
            self.signals.finished.emit(time.perf_counter() - t0)

    def _failed(self, exc: Exception) -> None:
        if self._cancel:                    # what a cancelled calculation raised on its way out is the cancel's
            self.signals.error.emit("CANCELLED", "")
        elif isinstance(exc, InputValidationError):
            self.signals.error.emit(f"INVALID_INPUT: {exc}", "")
        elif isinstance(exc, OutsideModelDomain):
            self.signals.error.emit(f"OUTSIDE_MODEL_DOMAIN: {exc}", "")
        else:
            self.signals.error.emit(f"{type(exc).__name__}: {exc}", traceback.format_exc())


class TaskRunner(QObject):
    """Starts tasks and reports progress to the main window.

    Every task ends with exactly one ``done`` (label, elapsed, outcome) and, unless a newer task with the same key
    replaced it, one ``task_finished`` (key, outcome); outcome is ``ok``, ``error`` or ``cancelled`` (``replaced`` on
    ``done`` only).  A page that disabled its run button can rely on ``task_finished`` to come back on every path,
    cancellation included.  A cancelled task shows no result, even one it finished computing; a partial result it
    showed stays the page's to keep or clear (``task_finished`` says ``cancelled``).
    """

    started = Signal(str)
    progress = Signal(str, float, str)       # key, fraction, message
    done = Signal(str, float, str)
    failed = Signal(str, str, str)
    task_started = Signal(str, str)          # key, label
    task_finished = Signal(str, str)         # key, outcome

    synchronous = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self.active: dict[str, Task] = {}
        self.labels: dict[str, str] = {}
        self.started_at: dict[str, float] = {}
        self.result_hook = None          # (key, args, result) -> None: called before the page shows a result
        self.shown_hook = None           # (key) -> None: called after the page has shown it

    def run(self, key: str, label: str, fn, on_result, *args, on_error=None, on_partial=None, **kwargs) -> Task:
        old = self.active.get(key)
        if old is not None:
            old.cancel()
        task = Task(fn, *args, **kwargs)
        self.active[key] = task
        self.labels[key] = label
        self.started_at[key] = time.monotonic()
        state = {"outcome": "error"}

        def _show(res, show):
            if self.result_hook is not None:
                self.result_hook(key, args, res)
            show(res)
            if self.shown_hook is not None:
                self.shown_hook(key)

        def _partial(res):
            if on_partial is not None and self.active.get(key) is task and not task.cancelled:
                _show(res, on_partial)

        def _result(res):
            if self.active.get(key) is not task:
                return
            if task.cancelled:              # cancelled after it finished computing: the cancel wins
                state["outcome"] = "cancelled"
                return
            state["outcome"] = "ok"
            _show(res, on_result)

        def _error(msg, tb):
            if msg == "CANCELLED":
                state["outcome"] = "cancelled"
                return
            if self.active.get(key) is not task:        # a replaced task's late error is not the page's news
                return
            if on_error is not None:
                on_error(msg, tb)
            self.failed.emit(label, msg, tb)

        def _finished(elapsed):
            current = self.active.get(key) is task
            if current:
                del self.active[key]
                self.started_at.pop(key, None)
            self.done.emit(label, elapsed, state["outcome"] if current else "replaced")
            if current:
                self.task_finished.emit(key, state["outcome"])

        task.signals.progress.connect(lambda f, m: self.progress.emit(key, f, f"{label}: {m}" if m else label))
        task.signals.partial.connect(_partial)
        task.signals.result.connect(_result)
        task.signals.error.connect(_error)
        task.signals.finished.connect(_finished)
        self.started.emit(label)
        self.task_started.emit(key, label)
        if self.synchronous:
            task.setAutoDelete(False)
            task.run()
        else:
            self.pool.start(task)
        return task

    def cancel(self, key: str) -> bool:
        t = self.active.get(key)
        if t is None:
            return False
        t.cancel()
        return True

    def cancel_all(self):
        for t in list(self.active.values()):
            t.cancel()

    def busy(self) -> bool:
        return bool(self.active)

    def running(self) -> list[str]:
        return list(self.active)

    def elapsed(self, key: str) -> float | None:
        """Seconds since task ``key`` started (None when it is not running)."""
        t0 = self.started_at.get(key)
        return None if t0 is None else time.monotonic() - t0
