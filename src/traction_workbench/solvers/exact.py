"""Exact boundary enumeration for the constant-parameter dq model.

With psi_d = psi + Ld*id, psi_q = Lq*iq and a current-independent tau_rot, the
shaft-torque condition is  k(id)*iq = a  with  k(id) = psi + (Ld - Lq)*id  and
a = (T_shaft + tau_rot) / (1.5*p).  Eliminating iq = a/k(id) turns every
constraint into a polynomial inequality in id after multiplying by k(id)^2 > 0:

    current :  (id^2 - Imax^2)*k^2 + a^2                          <= 0
    voltage :  (Rv*id*k - we*Lq*a)^2 + (Rv*a + we*(psi+Ld*id)*k)^2
               - Vb^2*k^2                                          <= 0
    iq max  :  a*k - iq_max*k^2                                    <= 0
    iq min  :  iq_min*k^2 - a*k                                    <= 0
    d(I^2)/d(id) = 0  <=>  id*k^3 - a^2*(Ld - Lq) = 0

Feasibility along the curve can only change at real roots of the constraint
polynomials, at the declared id bounds or at k = 0.  Testing every root and one
interior point of every sub-interval therefore establishes the feasible set
exactly (up to root precision); the minimum of I^2 lies at a feasible root,
a feasible stationary point or a bound.  This is the production method; the
independent fixture check uses a current-angle parametrisation instead.

The degenerate cases a = 0 (zero electromagnetic torque) and k = 0 (the
vertical line id = -psi/(Ld - Lq), where the torque is zero for any iq) are
treated with the original equations instead of the divided form.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.polynomial import Polynomial as Poly
from scipy.optimize import brentq

from ..physics import DriveKernel
from .common import CurveAnalysis, CurvePoint, CurveSegment, better, electrical_ok, worse


def _real_roots(P: Poly, lo: float, hi: float) -> list[float]:
    P = P.trim()
    if P.degree() < 1:
        return []
    span = max(hi - lo, 1e-12)
    out = []
    for z in P.roots():
        if abs(z.imag) <= 1e-6 * span and lo - 1e-9 * span <= z.real <= hi + 1e-9 * span:
            out.append(min(max(float(z.real), lo), hi))
    return out


def _polish(f, x0: float, scale: float) -> float:
    """Refine a root of the original (non-polynomial) function f near x0."""
    try:
        f0 = f(x0)
    except ZeroDivisionError:
        return x0
    if f0 == 0.0 or not math.isfinite(f0):
        return x0
    for h in (1e-10, 1e-8, 1e-6):
        a, b = x0 - h * scale, x0 + h * scale
        try:
            fa, fb = f(a), f(b)
        except ZeroDivisionError:
            continue
        if math.isfinite(fa) and math.isfinite(fb) and fa * fb < 0:
            return brentq(f, a, b, xtol=1e-15 * max(1.0, abs(x0)), rtol=1e-15, maxiter=200)
    return x0


class ConstantCurve:
    """Torque-matching curve of the constant-parameter model at one scenario."""

    def __init__(self, k: DriveKernel, T_shaft: float):
        if k.kind != "constant_dq":
            raise TypeError("exact enumeration requires the constant-parameter model")
        self.k = k
        self.T = float(T_shaft)
        self.tem = self.T + k.tau_rot_or_zero
        self.a = self.tem / (1.5 * k.p)
        self.psi = k.psi
        self.Ld, self.Lq = k.Ld, k.Lq
        self.dl = k.Ld - k.Lq
        self.Rv = k.Rs + k.R_drop
        self.we = k.omega_e
        self.scale = max(k.Imax, abs(k.domain.id_A[0]), abs(k.domain.id_A[1]), 1.0)
        # a = 0 within rounding of the torque arithmetic is treated as zero torque
        self.zero_torque = abs(self.a) <= 1e-14 * max(self.psi * k.Imax, abs(self.dl) * k.Imax ** 2, 1e-300)

    # -- divided form along the hyperbola ---------------------------------

    def kd(self, d):
        return self.psi + self.dl * d

    def iq_of(self, d):
        return self.a / self.kd(d)

    def _g_current(self, d):
        q = self.iq_of(d)
        return d * d + q * q - self.k.Imax ** 2

    def _g_voltage(self, d):
        q = self.iq_of(d)
        vd = self.Rv * d - self.we * self.Lq * q
        vq = self.Rv * q + self.we * (self.psi + self.Ld * d)
        return vd * vd + vq * vq - self.k.Vb ** 2

    def _g_qhi(self, d):
        return self.iq_of(d) - self.k.domain.iq_A[1]

    def _g_qlo(self, d):
        return self.k.domain.iq_A[0] - self.iq_of(d)

    def _g_stat(self, d):
        return d * self.kd(d) ** 3 - self.a ** 2 * self.dl

    def _hyperbola_candidates(self, lo: float, hi: float) -> list[tuple[float, str]]:
        k = self.k
        X = Poly.identity(domain=[lo, hi], window=[-1, 1]) if hi > lo else Poly([lo, 0.0])
        K = self.psi + self.dl * X
        a = self.a
        polys = {
            "current_boundary": ((X * X - k.Imax ** 2) * K * K + a * a, self._g_current),
            "voltage_boundary": ((self.Rv * X * K - self.we * self.Lq * a) ** 2
                                 + (self.Rv * a + self.we * (self.psi + self.Ld * X) * K) ** 2
                                 - k.Vb ** 2 * K * K, self._g_voltage),
            "iq_max_boundary": (a * K - k.domain.iq_A[1] * K * K, self._g_qhi),
            "iq_min_boundary": (k.domain.iq_A[0] * K * K - a * K, self._g_qlo),
            "stationary_I2": (X * K ** 3 - a * a * self.dl, self._g_stat),
        }
        out = [(lo, "id_bound"), (hi, "id_bound")]
        if hi <= lo:
            return out[:1]
        for tag, (P, g) in polys.items():
            for r in _real_roots(P, lo, hi):
                x = _polish(g, r, self.scale)
                out.append((min(max(x, lo), hi), tag))
        return out

    # -- analysis ---------------------------------------------------------

    def analyze(self) -> CurveAnalysis:
        k = self.k
        d_lo, d_hi = k.domain.id_A
        if k.drive.motor.flux.validity is not None:
            v = k.drive.motor.flux.validity
            d_lo, d_hi = max(d_lo, v.id_A[0]), min(d_hi, v.id_A[1])
        notes = []
        diag = {"a_Wb_A": self.a, "saliency_H": self.dl}
        if d_lo > d_hi:
            return self._result([], [], notes + ["declared id range is empty"], diag)
        if self.zero_torque:
            pts, segs, n2 = self._zero_torque(d_lo, d_hi)
            notes.append("zero electromagnetic torque: iq = 0 branch (and the k = 0 line if inside the domain) "
                         "analysed with the original equations")
            return self._result(pts, segs, notes, diag)
        # split at the k = 0 singularity (torque cannot be produced there for a != 0)
        pieces = [(d_lo, d_hi)]
        if self.dl != 0.0:
            d0 = -self.psi / self.dl
            diag["k_zero_id_A"] = d0
            if d_lo < d0 < d_hi:
                gap = 1e-9 * self.scale
                pieces = [(d_lo, d0 - gap), (d0 + gap, d_hi)]
                notes.append(f"torque curve has a pole at id = {d0:.6g} A (k = 0); pieces analysed separately")
        all_pts: list[CurvePoint] = []
        segs: list[CurveSegment] = []
        n_roots = 0
        for lo, hi in pieces:
            cands = self._hyperbola_candidates(lo, hi)
            n_roots += len(cands)
            pts, s = self._scan_piece(sorted(cands), lo, hi)
            all_pts += pts
            segs += s
        diag["candidates_examined"] = n_roots
        return self._result(all_pts, segs, notes, diag)

    def _point(self, d: float, tag: str) -> CurvePoint:
        q = self.iq_of(d)
        return CurvePoint(float(d), float(q), float(d * d + q * q), tag)

    def _feasible(self, d: float, q: float) -> bool:
        return bool(electrical_ok(self.k, np.array([d]), np.array([q]))[0])

    def _scan_piece(self, cands, lo, hi):
        """Exact feasible set on one smooth piece of the curve."""
        xs = sorted({round(x, 15) if False else x for x, _ in cands})
        tags = {}
        for x, t in cands:
            tags.setdefault(x, t)
        feas_pts = []
        for x in xs:
            p = self._point(x, tags.get(x, ""))
            if self._feasible(p.id_A, p.iq_A):
                feas_pts.append(p)
        # interior test of each sub-interval between consecutive candidates
        segs = []
        cur_start = None
        cur_pts: list[CurvePoint] = []
        for x0, x1 in zip(xs[:-1], xs[1:]):
            if x1 <= x0:
                continue
            mid = 0.5 * (x0 + x1)
            pm = self._point(mid, "interior")
            if self._feasible(pm.id_A, pm.iq_A):
                if cur_start is None:
                    cur_start = self._point(x0, tags.get(x0, ""))
                    cur_pts = [cur_start]
                cur_pts.append(self._point(x1, tags.get(x1, "")))
            else:
                if cur_start is not None:
                    segs.append(self._segment(cur_pts))
                    cur_start = None
        if cur_start is not None:
            segs.append(self._segment(cur_pts))
        # isolated feasible points (tangencies) not inside any segment
        for p in feas_pts:
            if not any(s.start.id_A <= p.id_A <= s.end.id_A for s in segs):
                segs.append(CurveSegment(p, p, p, p))
        return feas_pts, segs

    def _segment(self, pts):
        mn = None
        mx = None
        for p in pts:
            if better(p, mn):
                mn = p
            if worse(p, mx):
                mx = p
        return CurveSegment(pts[0], pts[-1], mn, mx)

    def _zero_torque(self, d_lo, d_hi):
        """a = 0: iq = 0 for every id, plus the k = 0 line (any iq) if inside the domain."""
        k = self.k
        q_lo, q_hi = k.domain.iq_A
        pts, segs = [], []
        if q_lo <= 0.0 <= q_hi:
            # constraints along iq = 0 are quadratics in id
            X = Poly.identity(domain=[d_lo, d_hi], window=[-1, 1]) if d_hi > d_lo else Poly([d_lo, 0.0])
            polys = [X * X - k.Imax ** 2,
                     (self.Rv * X) ** 2 + (self.we * (self.psi + self.Ld * X)) ** 2 - k.Vb ** 2]
            xs = {d_lo, d_hi}
            if d_lo <= 0.0 <= d_hi:
                xs.add(0.0)
            for P in polys:
                xs.update(_real_roots(P, d_lo, d_hi))
            xs = sorted(xs)
            feas = [CurvePoint(x, 0.0, x * x, "zero_torque_branch") for x in xs if self._feasible(x, 0.0)]
            pts += feas
            cur = []
            for x0, x1 in zip(xs[:-1], xs[1:]):
                if self._feasible(0.5 * (x0 + x1), 0.0):
                    if not cur:
                        cur = [CurvePoint(x0, 0.0, x0 * x0, "")]
                    cur.append(CurvePoint(x1, 0.0, x1 * x1, ""))
                elif cur:
                    segs.append(self._segment(cur))
                    cur = []
            if cur:
                segs.append(self._segment(cur))
            for p in feas:
                if not any(s.start.id_A <= p.id_A <= s.end.id_A for s in segs):
                    segs.append(CurveSegment(p, p, p, p))
        if self.dl != 0.0:
            d0 = -self.psi / self.dl
            if d_lo <= d0 <= d_hi:
                # vertical line: every iq gives zero torque; constraints quadratic in iq
                Y = Poly.identity(domain=[q_lo, q_hi], window=[-1, 1]) if q_hi > q_lo else Poly([q_lo, 0.0])
                vd = self.Rv * d0 - self.we * self.Lq * Y
                vq = self.Rv * Y + self.we * (self.psi + self.Ld * d0)
                polys = [Y * Y + d0 * d0 - k.Imax ** 2, vd * vd + vq * vq - k.Vb ** 2]
                ys = {q_lo, q_hi}
                if q_lo <= 0.0 <= q_hi:
                    ys.add(0.0)
                for P in polys:
                    ys.update(_real_roots(P, q_lo, q_hi))
                ys = sorted(ys)
                vfeas = [CurvePoint(d0, y, d0 * d0 + y * y, "k_zero_line") for y in ys if self._feasible(d0, y)]
                pts += vfeas
                cur = []
                for y0, y1 in zip(ys[:-1], ys[1:]):
                    if self._feasible(d0, 0.5 * (y0 + y1)):
                        if not cur:
                            cur = [CurvePoint(d0, y0, d0 * d0 + y0 * y0, "k_zero_line")]
                        cur.append(CurvePoint(d0, y1, d0 * d0 + y1 * y1, "k_zero_line"))
                    elif cur:
                        segs.append(CurveSegment(cur[0], cur[-1], min(cur, key=lambda p: p.I2),
                                                 max(cur, key=lambda p: p.I2), parameter="iq_A"))
                        cur = []
                if cur:
                    segs.append(CurveSegment(cur[0], cur[-1], min(cur, key=lambda p: p.I2),
                                             max(cur, key=lambda p: p.I2), parameter="iq_A"))
        return pts, segs, len(pts)

    def _result(self, pts, segs, notes, diag) -> CurveAnalysis:
        mn = mx = None
        for s in segs:
            if better(s.min_point, mn):
                mn = s.min_point
            if worse(s.max_point, mx):
                mx = s.max_point
        for p in pts:
            if better(p, mn):
                mn = p
            if worse(p, mx):
                mx = p
        k = self.k
        cov = math.inf
        limited = False
        if k.drive.motor.flux.validity is not None:
            from ..models.flux import uncovered_distance
            cov = uncovered_distance(k.uncovered_rectangles(), k.domain.current_box, k.Imax)
            limited = math.isfinite(cov)
        return CurveAnalysis(
            target_Tshaft_Nm=self.T,
            target_Tem_Nm=self.tem,
            method="exact_boundary_enumeration",
            exact=True,
            segments=tuple(sorted(segs, key=lambda s: (s.start.id_A, s.start.iq_A))),
            min_point=mn,
            max_point=mx,
            coverage_limited=limited,
            coverage_distance_A=cov,
            notes=tuple(notes),
            diagnostics=tuple(diag.items()),
        )


def analyze_constant(k: DriveKernel, T_shaft: float) -> CurveAnalysis:
    return ConstantCurve(k, T_shaft).analyze()


def point_on_curve_with_I2(k: DriveKernel, T_shaft: float, segment: CurveSegment, i2_target: float) -> CurvePoint | None:
    """Point on a feasible segment where I^2 equals i2_target (used for DC-band witnesses)."""
    c = ConstantCurve(k, T_shaft)
    if segment.parameter != "id_A" or c.zero_torque:
        # zero-torque branch or vertical line: I^2 is explicit
        if segment.parameter == "iq_A":
            d0 = segment.start.id_A
            y2 = i2_target - d0 * d0
            if y2 < 0:
                return None
            for y in (math.sqrt(y2), -math.sqrt(y2)):
                lo, hi = sorted((segment.start.iq_A, segment.end.iq_A))
                if lo - 1e-9 <= y <= hi + 1e-9:
                    return CurvePoint(d0, y, i2_target, "dc_band_edge")
            return None
        lo, hi = sorted((segment.start.id_A, segment.end.id_A))
        for x in (-math.sqrt(max(i2_target, 0)), math.sqrt(max(i2_target, 0))):
            if lo - 1e-9 <= x <= hi + 1e-9:
                return CurvePoint(x, 0.0, x * x, "dc_band_edge")
        return None

    def f(d):
        q = c.iq_of(d)
        return d * d + q * q - i2_target

    pts = sorted({segment.start.id_A, segment.end.id_A, segment.min_point.id_A, segment.max_point.id_A})
    for a, b in zip(pts[:-1], pts[1:]):
        fa, fb = f(a), f(b)
        if fa == 0:
            return c._point(a, "dc_band_edge")
        if fa * fb < 0:
            x = brentq(f, a, b, xtol=1e-13, rtol=1e-15)
            return c._point(x, "dc_band_edge")
    if f(pts[-1]) == 0:
        return c._point(pts[-1], "dc_band_edge")
    return None
