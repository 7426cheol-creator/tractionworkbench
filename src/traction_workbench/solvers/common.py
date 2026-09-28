"""Shared solver pieces: vectorised feasibility checks and curve results.

The vectorised checks use exactly the classification of
``physics.ConstraintResult`` (VIOLATED iff slack < -max(abs_floor,
rel_tol*|limit|)), so a point accepted by a solver is never reported as a
violation by the forward evaluation and vice versa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..physics import DriveKernel


def _tol(k: DriveKernel, limit: float, floor: float) -> float:
    return max(floor, k.settings.constraint_rel_tol * abs(limit))


def electrical_ok(k: DriveKernel, d, q, ev: dict | None = None) -> np.ndarray:
    """Voltage, current and declared (id, iq) domain; model coverage required."""
    s = k.settings
    d = np.asarray(d, dtype=float)
    q = np.asarray(q, dtype=float)
    if ev is None:
        ev = k.evaluate(d, q)
    vcmd = np.sqrt(ev["vcmd2"])
    ipk = np.sqrt(ev["i2"])
    dom = k.domain
    ok = np.asarray(ev["ok"], dtype=bool).copy()
    with np.errstate(invalid="ignore"):
        ok &= (k.Vb - vcmd) >= -_tol(k, k.Vb, s.voltage_abs_tol_V)
        ok &= (k.Imax - ipk) >= -_tol(k, k.Imax, s.current_abs_tol_A)
        ok &= (d - dom.id_A[0]) >= -_tol(k, dom.id_A[0], s.current_abs_tol_A)
        ok &= (dom.id_A[1] - d) >= -_tol(k, dom.id_A[1], s.current_abs_tol_A)
        ok &= (q - dom.iq_A[0]) >= -_tol(k, dom.iq_A[0], s.current_abs_tol_A)
        ok &= (dom.iq_A[1] - q) >= -_tol(k, dom.iq_A[1], s.current_abs_tol_A)
    return ok


def dc_ok(k: DriveKernel, pdc) -> np.ndarray:
    """DC power and average-current limits at the given P_dc (array)."""
    s = k.settings
    pdc = np.asarray(pdc, dtype=float)
    ok = np.isfinite(pdc)
    lim = k.limits
    with np.errstate(invalid="ignore"):
        if lim.discharge_power_max_W is not None and math.isfinite(lim.discharge_power_max_W):
            L = lim.discharge_power_max_W
            ok &= (L - pdc) >= -_tol(k, L, s.power_abs_tol_W)
        if lim.charge_power_max_W is not None and math.isfinite(lim.charge_power_max_W):
            L = -lim.charge_power_max_W
            ok &= (pdc - L) >= -_tol(k, L, s.power_abs_tol_W)
        idc = pdc / k.Vdc
        if lim.discharge_current_max_A is not None and math.isfinite(lim.discharge_current_max_A):
            L = lim.discharge_current_max_A
            ok &= (L - idc) >= -_tol(k, L, s.current_abs_tol_A)
        if lim.charge_current_max_A is not None and math.isfinite(lim.charge_current_max_A):
            L = -lim.charge_current_max_A
            ok &= (idc - L) >= -_tol(k, L, s.current_abs_tol_A)
    return ok


def dc_band_I2(k: DriveKernel, tem_target: float, nominal: bool = False) -> tuple[float, float] | None:
    """I^2 band compatible with the DC limits along a torque curve.

    Along any torque-matching curve P_dc = T_em*omega_m + (1.5*Rs + a2)*I^2 + a0
    (``DriveKernel.i2_dc``), so the DC limits become an interval of I^2.  The
    limits are the witness gate's acceptance set (``DriveKernel.dc_accept_*``,
    tolerances included), so the band excludes nothing the gate accepts.  Returns
    None without that identity (no loss model, or a pointwise loss model).
    ``nominal=True`` uses the declared limits without tolerance: that band lies inside the acceptance band and
    is where a witness is PLACED (a point on the tolerance edge itself can fall out by rounding).
    """
    q = k.i2_dc
    if q is None:
        return None
    c2 = q.c2_W_per_A2
    base = tem_target * k.omega_m + q.a0_W
    if nominal:
        hi, lo = k.P_dis_eff, (None if k.P_chg_eff is None else -k.P_chg_eff)
    else:
        hi, lo = k.dc_accept_hi_W, k.dc_accept_lo_W
    if c2 <= 0:
        ok_hi = hi is None or base <= hi
        ok_lo = lo is None or base >= lo
        return (0.0, math.inf) if (ok_hi and ok_lo) else (math.inf, -math.inf)
    i2_hi = math.inf if hi is None else (hi - base) / c2
    i2_lo = -math.inf if lo is None else (lo - base) / c2
    return (max(i2_lo, 0.0), i2_hi)


@dataclass(frozen=True)
class CurvePoint:
    id_A: float
    iq_A: float
    I2: float
    tag: str = ""

    def to_dict(self) -> dict:
        return {"id_A": self.id_A, "iq_A": self.iq_A, "i_peak_A": math.sqrt(self.I2), "tag": self.tag}


@dataclass(frozen=True)
class CurveSegment:
    """A connected, electrically feasible piece of the torque-matching curve."""

    start: CurvePoint
    end: CurvePoint
    min_point: CurvePoint
    max_point: CurvePoint
    parameter: str = "id_A"

    def to_dict(self) -> dict:
        return {"parameter": self.parameter, "start": self.start.to_dict(), "end": self.end.to_dict(),
                "min_current": self.min_point.to_dict(), "max_current": self.max_point.to_dict()}


@dataclass(frozen=True)
class CurveAnalysis:
    """Electrically feasible set on the curve T_shaft(id, iq) = target."""

    target_Tshaft_Nm: float
    target_Tem_Nm: float
    method: str
    exact: bool
    segments: tuple[CurveSegment, ...]
    min_point: CurvePoint | None
    max_point: CurvePoint | None
    coverage_limited: bool = False
    coverage_distance_A: float = math.inf
    notes: tuple[str, ...] = ()
    diagnostics: tuple = ()
    unresolved: bool = False

    @property
    def empty(self) -> bool:
        return self.min_point is None

    def to_dict(self) -> dict:
        return {
            "target_Tshaft_Nm": self.target_Tshaft_Nm,
            "target_Tem_Nm": self.target_Tem_Nm,
            "method": self.method,
            "exact": self.exact,
            "feasible_segments": [s.to_dict() for s in self.segments],
            "min_current_point": None if self.min_point is None else self.min_point.to_dict(),
            "max_current_point": None if self.max_point is None else self.max_point.to_dict(),
            "coverage_limited": self.coverage_limited,
            "coverage_distance_A": None if math.isinf(self.coverage_distance_A) else self.coverage_distance_A,
            "unresolved": self.unresolved,
            "notes": list(self.notes),
            "diagnostics": dict(self.diagnostics),
        }


def better(a: CurvePoint, b: CurvePoint | None, rel: float = 1e-12) -> bool:
    """Deterministic tie rule for minimum-current candidates.

    Smaller I^2 wins; if |dI^2| <= rel*I^2 the candidate with the larger id
    (less field weakening) wins, then the smaller |iq|, then the smaller iq.
    """
    if b is None:
        return True
    scale = max(a.I2, b.I2, 1e-30)
    if a.I2 < b.I2 - rel * scale:
        return True
    if a.I2 > b.I2 + rel * scale:
        return False
    if a.id_A != b.id_A:
        return a.id_A > b.id_A
    if abs(a.iq_A) != abs(b.iq_A):
        return abs(a.iq_A) < abs(b.iq_A)
    return a.iq_A < b.iq_A


def worse(a: CurvePoint, b: CurvePoint | None, rel: float = 1e-12) -> bool:
    """Deterministic rule for maximum-current candidates (larger I^2 wins)."""
    if b is None:
        return True
    scale = max(a.I2, b.I2, 1e-30)
    if a.I2 > b.I2 + rel * scale:
        return True
    if a.I2 < b.I2 - rel * scale:
        return False
    return a.id_A < b.id_A


TIE_RULE = ("minimum I^2; if |dI^2| <= 1e-12*I^2 prefer the larger id (less field weakening), "
            "then the smaller |iq|, then the smaller iq")
