"""Input validation helpers shared by every layer (numbers, intervals, table axes).

Kept free of any physics model so that analyses and extensions do not depend on the flux model just to validate a
number; ``models.flux`` re-exports them under their former private names for compatibility.
"""

from __future__ import annotations

import math

import numpy as np

from .errors import InputValidationError


def finite(name: str, value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise InputValidationError(f"expected a number, got {value!r}", field=name) from None
    if not math.isfinite(v):
        raise InputValidationError(f"non-finite value {value!r}", field=name)
    return v



def interval(name: str, pair) -> tuple[float, float]:
    try:
        lo, hi = pair
    except (TypeError, ValueError):
        raise InputValidationError(f"expected [lo, hi], got {pair!r}", field=name) from None
    lo, hi = finite(name, lo), finite(name, hi)
    if lo > hi:
        raise InputValidationError(f"lower bound {lo} exceeds upper bound {hi}", field=name)
    return lo, hi



def axis(name: str, values) -> np.ndarray:
    try:
        arr = np.array(values, dtype=float)
    except (TypeError, ValueError):
        raise InputValidationError("axis must be numeric", field=name) from None
    if arr.ndim != 1 or arr.size < 2:
        raise InputValidationError("axis must be one-dimensional with at least 2 points", field=name)
    if not np.all(np.isfinite(arr)):
        raise InputValidationError("axis contains non-finite values", field=name)
    diffs = np.diff(arr)
    if np.any(diffs == 0):
        raise InputValidationError("axis contains duplicate values", field=name)
    if np.any(diffs < 0):
        raise InputValidationError("axis must be strictly increasing", field=name)
    arr.setflags(write=False)
    return arr
