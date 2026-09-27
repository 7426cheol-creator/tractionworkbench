"""Exceptions.

``InputValidationError`` is raised *before* any computation when inputs are
malformed (non-finite numbers, Vdc <= 0, unit or definition ambiguity, bad
map axes, ...).  It is an INVALID_INPUT outcome, never a physical FAIL.

``OutsideModelDomain`` is raised when a model is asked for a value it does
not define (flux-map hole, point outside the map, unvalidated temperature).
Callers turn it into an UNKNOWN claim; it is never silently extrapolated.
"""

from __future__ import annotations


class TractionWorkbenchError(Exception):
    """Base class for all workbench errors."""


class InputValidationError(TractionWorkbenchError, ValueError):
    """Invalid or ambiguous input detected before computation (INVALID_INPUT)."""

    def __init__(self, message: str, *, field: str | None = None):
        self.field = field
        super().__init__(f"{field}: {message}" if field else message)


class OutsideModelDomain(TractionWorkbenchError):
    """A model was queried outside the domain where it is defined."""

    def __init__(self, message: str, *, detail: dict | None = None):
        self.detail = dict(detail or {})
        super().__init__(message)
