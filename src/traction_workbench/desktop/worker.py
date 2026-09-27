"""Background computation: engine calls run in a thread pool, results come back as Qt signals.

Figures are always drawn in the GUI thread; workers only compute data.
In self-test mode (``TaskRunner.synchronous = True``) tasks run inline so results are deterministic.
"""

from __future__ import annotations

import time
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..errors import InputValidationError, OutsideModelDomain
from ..viz.sweeps import Cancelled


class TaskSignals(QObject):
    progress = Signal(float, str)
    result = Signal(object)
    error = Signal(str, str)
    finished = Signal(float)


class Task(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = TaskSignals()
        self._cancel = False
        self.setAutoDelete(True)

    def cancel(self):
        self._cancel = True

    def _progress(self, frac: float, msg: str = ""):
        if self._cancel:
            raise Cancelled()
        self.signals.progress.emit(float(frac), str(msg))

    def run(self):
        t0 = time.perf_counter()
        try:
            res = self.fn(self._progress, *self.args, **self.kwargs)
        except Cancelled:
            self.signals.error.emit("CANCELLED", "")
        except InputValidationError as exc:
            self.signals.error.emit(f"INVALID_INPUT: {exc}", "")
        except OutsideModelDomain as exc:
            self.signals.error.emit(f"OUTSIDE_MODEL_DOMAIN: {exc}", "")
        except Exception as exc:  # noqa: BLE001 - surfaced to the user with the traceback
            self.signals.error.emit(f"{type(exc).__name__}: {exc}", traceback.format_exc())
        else:
            self.signals.result.emit(res)
        finally:
            self.signals.finished.emit(time.perf_counter() - t0)


class TaskRunner(QObject):
    """Starts tasks and reports progress to the main window."""

    started = Signal(str)
    progress = Signal(float, str)
    done = Signal(str, float, bool)
    failed = Signal(str, str, str)

    synchronous = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self.active: dict[str, Task] = {}

    def run(self, key: str, label: str, fn, on_result, *args, on_error=None, **kwargs) -> Task:
        old = self.active.get(key)
        if old is not None:
            old.cancel()
        task = Task(fn, *args, **kwargs)
        self.active[key] = task
        ok = {"v": False}

        def _result(res):
            if self.active.get(key) is task:
                ok["v"] = True
                on_result(res)

        def _error(msg, tb):
            if msg == "CANCELLED":
                return
            if on_error is not None:
                on_error(msg, tb)
            self.failed.emit(label, msg, tb)

        def _finished(elapsed):
            if self.active.get(key) is task:
                del self.active[key]
            self.done.emit(label, elapsed, ok["v"])

        task.signals.progress.connect(lambda f, m: self.progress.emit(f, f"{label}: {m}" if m else label))
        task.signals.result.connect(_result)
        task.signals.error.connect(_error)
        task.signals.finished.connect(_finished)
        self.started.emit(label)
        if self.synchronous:
            task.setAutoDelete(False)
            task.run()
        else:
            self.pool.start(task)
        return task

    def cancel_all(self):
        for t in list(self.active.values()):
            t.cancel()

    def busy(self) -> bool:
        return bool(self.active)
