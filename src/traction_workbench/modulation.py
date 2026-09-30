"""Carrier-based PWM modulation laws: the zero sequence of each declared family and the average leg duty cycles.

One implementation for every model that needs duty cycles (datasheet module losses, EMI edge spectra, DC-link
capacitor current, phase-current ripple and sampling windows, the operating-point views), so that a loss, a ripple
and a sampling result at the same point always describe the same pulse pattern.

Phase references u_k = m cos(theta + alpha - 2 pi k / 3), k = a, b, c, in units of Vdc / 2 (m = V_pk / (Vdc / 2));
leg duty d_k = (1 + u_k + u_0) / 2 with the zero sequence u_0 of the family:

* spwm : u_0 = 0 (linear up to m = 1);
* svpwm: u_0 = -(max u + min u) / 2 (min-max injection, equivalent to SVPWM; linear up to m = 2 / sqrt 3);
* dpwm1: u_0 = sign(u_m) - u_m with u_m the phase of largest magnitude (60-degree clamping to its rail).

Duties are returned unclipped: a value outside [0, 1] is overmodulation, where the linear average model no longer
describes the pulses.
"""

from __future__ import annotations

import math

import numpy as np

from .errors import InputValidationError

MODULATIONS = ("svpwm", "spwm", "dpwm1")
TWO_PI = 2.0 * math.pi


def zero_sequence(u: np.ndarray, modulation: str, rail: float = 1.0) -> np.ndarray:
    """Zero-sequence of the family for phase references ``u`` (3 x N, any unit; ``rail`` = half the DC voltage in
    that unit, used by the clamping families only)."""
    u = np.asarray(u, dtype=float)
    if modulation == "spwm":
        return np.zeros(u.shape[1:])
    if modulation == "svpwm":
        return -0.5 * (u.max(axis=0) + u.min(axis=0))
    if modulation == "dpwm1":
        k = np.argmax(np.abs(u), axis=0)
        um = u[k, np.arange(u.shape[1])]
        return np.sign(um) * rail - um
    raise InputValidationError(f"modulation must be one of {MODULATIONS}", field="modulation")


def references(theta, m: float, alpha: float = 0.0) -> np.ndarray:
    """Phase references in units of Vdc / 2 (3 x N)."""
    theta = np.asarray(theta, dtype=float)
    return np.vstack([m * np.cos(theta + alpha - TWO_PI * k / 3.0) for k in range(3)])


def duties(theta, m: float, alpha: float = 0.0, modulation: str = "svpwm") -> np.ndarray:
    """Average leg duty cycles (3 x N, unclipped) of the declared family.

    A clamping family puts the clamped leg exactly on its rail (0 or 1): the rounding of u_m + (sign(u_m) - u_m)
    would otherwise leave a 1e-16 pulse that every edge, window and minimum-pulse model reads as switching."""
    u = references(theta, m, alpha)
    d = 0.5 * (1.0 + u + zero_sequence(u, modulation))
    if modulation == "dpwm1":
        d = np.where(np.abs(d) <= 1e-12, 0.0, np.where(np.abs(d - 1.0) <= 1e-12, 1.0, d))
    return d


def overmodulated(d: np.ndarray, tol: float = 1e-9) -> bool:
    return bool(np.any(d < -tol) or np.any(d > 1.0 + tol))
