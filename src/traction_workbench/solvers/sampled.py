"""Sampled torque-curve tracing for any flux model (used for flux maps).

The torque-matching curve T_shaft(id, iq) = target is located row by row
(id fixed) from sign changes of T_em on an iq grid that includes every map
node, then the minimum/maximum-current feasible points are refined locally
(root solves on the curve, bisection on the violated constraint, bounded
scalar minimisation for interior extrema).

This is *sampled* evidence: features narrower than the grid can be missed and
a missing solution is never reported as infeasible by this module.  Rigorous
statements for flux maps come from ``bounds.py`` (cell bounds) and from
coverage checks.  On the constant-parameter model this tracer is an
independent cross-check of the exact enumeration.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from ..models.flux import uncovered_distance
from ..physics import DriveKernel
from .common import CurveAnalysis, CurvePoint, CurveSegment, better, electrical_ok, worse


class SampledCurveTracer:
    def __init__(self, k: DriveKernel, n_id: int | None = None, n_iq: int | None = None):
        self.k = k
        s = k.settings
        n_id = n_id or s.sampled_id_points
        n_iq = n_iq or s.sampled_iq_points
        dom = k.domain
        d_lo, d_hi = dom.id_A
        q_lo, q_hi = dom.iq_A
        nodes_d = np.array([])
        nodes_q = np.array([])
        self.is_map = k.kind == "flux_map"
        if self.is_map:
            pl = k.plane
            d_lo, d_hi = max(d_lo, pl.id_axis_A[0]), min(d_hi, pl.id_axis_A[-1])
            q_lo, q_hi = max(q_lo, pl.iq_axis_A[0]), min(q_hi, pl.iq_axis_A[-1])
            nodes_d = pl.id_axis_A[(pl.id_axis_A >= d_lo) & (pl.id_axis_A <= d_hi)]
            nodes_q = pl.iq_axis_A[(pl.iq_axis_A >= q_lo) & (pl.iq_axis_A <= q_hi)]
        else:
            v = k.drive.motor.flux.validity
            if v is not None:
                d_lo, d_hi = max(d_lo, v.id_A[0]), min(d_hi, v.id_A[1])
                q_lo, q_hi = max(q_lo, v.iq_A[0]), min(q_hi, v.iq_A[1])
        self.region = (d_lo, d_hi, q_lo, q_hi)
        self.empty_region = d_lo > d_hi or q_lo > q_hi
        self.cov = uncovered_distance(k.uncovered_rectangles(), dom.current_box, k.Imax)
        if self.empty_region:
            self.ids = np.array([])
            return
        self.ids = np.unique(np.concatenate([np.linspace(d_lo, d_hi, n_id), nodes_d]))
        self.dq_step = (q_hi - q_lo) / max(n_iq - 1, 1)
        self.dd_step = (d_hi - d_lo) / max(n_id - 1, 1)
        if self.is_map:
            self.iqs = np.unique(np.concatenate([np.linspace(q_lo, q_hi, n_iq), nodes_q]))
            D, Q = np.meshgrid(self.ids, self.iqs, indexing="ij")
            ev = k.evaluate(D, Q)
            self.tem_grid = np.where(ev["ok"], ev["tem"], np.nan)

    # -- curve points -------------------------------------------------------

    def _tem_target(self, T_shaft: float) -> float:
        return T_shaft + self.k.tau_rot_or_zero

    def _row_roots(self, tem_target: float):
        """Estimated curve points (id, iq) from every row; list of arrays."""
        k = self.k
        d_lo, d_hi, q_lo, q_hi = self.region
        if not self.is_map:
            psi, dl = k.psi, k.Ld - k.Lq
            a = tem_target / (1.5 * k.p)
            kd = psi + dl * self.ids
            with np.errstate(divide="ignore", invalid="ignore"):
                q = np.where(kd != 0, a / kd, np.nan)
            if a == 0:
                q = np.zeros_like(self.ids)
            sel = np.isfinite(q) & (q >= q_lo) & (q <= q_hi)
            return self.ids[sel], q[sel], np.flatnonzero(sel)
        F = self.tem_grid - tem_target
        f0, f1 = F[:, :-1], F[:, 1:]
        with np.errstate(invalid="ignore"):
            cross = np.isfinite(f0) & np.isfinite(f1) & (f0 * f1 < 0)
            zero = np.isfinite(F) & (F == 0)
        ri, cj = np.nonzero(cross)
        q0, q1 = self.iqs[cj], self.iqs[cj + 1]
        fa, fb = f0[ri, cj], f1[ri, cj]
        q_est = q0 - fa * (q1 - q0) / (fb - fa)
        zi, zj = np.nonzero(zero)
        rows = np.concatenate([ri, zi])
        qs = np.concatenate([q_est, self.iqs[zj]])
        return self.ids[rows], qs, rows

    def solve_q(self, d: float, tem_target: float, q_guess: float) -> float | None:
        k = self.k
        d_lo, d_hi, q_lo, q_hi = self.region
        if not self.is_map:
            kd = k.psi + (k.Ld - k.Lq) * d
            if kd == 0:
                return None
            q = tem_target / (1.5 * k.p) / kd
            return q if q_lo <= q <= q_hi else None

        def f(q):
            ev = k.evaluate(d, q)
            return float(ev["tem"]) - tem_target if bool(ev["ok"]) else math.nan

        step = max(self.dq_step, 1e-6)
        for w in (1.0, 2.0, 4.0, 8.0):
            a = max(q_lo, q_guess - w * step)
            b = min(q_hi, q_guess + w * step)
            fa, fb = f(a), f(b)
            if not (math.isfinite(fa) and math.isfinite(fb)):
                continue
            if fa == 0:
                return a
            if fb == 0:
                return b
            if fa * fb < 0:
                return brentq(f, a, b, xtol=1e-12, rtol=1e-14, maxiter=200)
        return None

    def solve_d(self, q: float, tem_target: float, d_guess: float) -> float | None:
        k = self.k
        d_lo, d_hi, q_lo, q_hi = self.region

        def f(d):
            ev = k.evaluate(d, q)
            return float(ev["tem"]) - tem_target if bool(ev["ok"]) else math.nan

        step = max(self.dd_step, 1e-6)
        for w in (1.0, 2.0, 4.0, 8.0):
            a = max(d_lo, d_guess - w * step)
            b = min(d_hi, d_guess + w * step)
            fa, fb = f(a), f(b)
            if not (math.isfinite(fa) and math.isfinite(fb)):
                continue
            if fa == 0:
                return a
            if fb == 0:
                return b
            if fa * fb < 0:
                return brentq(f, a, b, xtol=1e-12, rtol=1e-14, maxiter=200)
        return None

    # -- constraint slacks (native units, positive = satisfied) ----------

    def slacks(self, d: float, q: float) -> dict:
        k = self.k
        ev = k.evaluate(d, q)
        if not bool(ev["ok"]):
            return {"coverage": -1.0}
        dom = k.domain
        return {
            "voltage": (k.Vb - math.sqrt(float(ev["vcmd2"]))) / k.Vb,
            "current": (k.Imax - math.sqrt(float(ev["i2"]))) / k.Imax,
            "id_min": (d - dom.id_A[0]) / k.Imax,
            "id_max": (dom.id_A[1] - d) / k.Imax,
            "iq_min": (q - dom.iq_A[0]) / k.Imax,
            "iq_max": (dom.iq_A[1] - q) / k.Imax,
        }

    def feasible(self, d: float, q: float) -> bool:
        return bool(electrical_ok(self.k, np.array([d]), np.array([q]))[0])

    # -- local refinement -------------------------------------------------

    def _local_param(self, d: float, q: float, tem_target: float) -> str:
        if not self.is_map:
            return "id"
        k = self.k
        h = 1e-4 * max(1.0, abs(d), abs(q))
        e0 = k.evaluate(np.array([d - h, d + h, d, d]), np.array([q, q, q - h, q + h]))
        t = e0["tem"]
        if not np.all(np.isfinite(t)):
            return "id"
        dTd = (t[1] - t[0]) / (2 * h)
        dTq = (t[3] - t[2]) / (2 * h)
        return "id" if abs(dTq) >= abs(dTd) else "iq"

    def _curve_at(self, param: str, x: float, tem_target: float, guess: float):
        if param == "id":
            q = self.solve_q(x, tem_target, guess)
            return None if q is None else (x, q)
        d = self.solve_d(x, tem_target, guess)
        return None if d is None else (d, x)

    def refine(self, p: CurvePoint, tem_target: float, mode: str = "min") -> tuple[CurvePoint, str]:
        """Refine a sampled extremum of I^2 along the curve near p."""
        param = self._local_param(p.id_A, p.iq_A, tem_target)
        x0 = p.id_A if param == "id" else p.iq_A
        guess0 = p.iq_A if param == "id" else p.id_A
        d_lo, d_hi, q_lo, q_hi = self.region
        lo_b, hi_b = (d_lo, d_hi) if param == "id" else (q_lo, q_hi)
        step = self.dd_step if param == "id" else self.dq_step
        H = 3.0 * max(step, 1e-9)
        xs = np.linspace(max(lo_b, x0 - H), min(hi_b, x0 + H), 41)
        pts = []
        guess = guess0
        # march outward from x0 in both directions for good root guesses
        order = np.argsort(np.abs(xs - x0))
        solved = {}
        for idx in order:
            x = xs[idx]
            near = min(solved, key=lambda j: abs(xs[j] - x)) if solved else None
            g = solved[near][1] if near is not None else guess0
            if param == "id":
                g = solved[near][1] if near is not None else guess0
            else:
                g = solved[near][0] if near is not None else guess0
            r = self._curve_at(param, float(x), tem_target, g)
            if r is not None:
                solved[idx] = r
        seq = []
        for j in range(xs.size):
            if j in solved:
                d, q = solved[j]
                seq.append((j, d, q, self.feasible(d, q), d * d + q * q))
        if not seq:
            return p, "refinement failed (curve not re-located); sampled point kept"
        feas = [t for t in seq if t[3]]
        if not feas:
            return p, "no feasible point re-located near the sample; sampled point kept"
        pick = min(feas, key=lambda t: t[4]) if mode == "min" else max(feas, key=lambda t: t[4])
        j = pick[0]
        pos = [t[0] for t in seq].index(j)
        left = seq[pos - 1] if pos > 0 else None
        right = seq[pos + 1] if pos + 1 < len(seq) else None

        def curve_pt(x):
            near = min(solved, key=lambda jj: abs(xs[jj] - x))
            g = solved[near][1] if param == "id" else solved[near][0]
            return self._curve_at(param, x, tem_target, g)

        # boundary case: a neighbour is infeasible and better in the objective direction
        for nb in (left, right):
            if nb is None or nb[3]:
                continue
            better_dir = nb[4] < pick[4] if mode == "min" else nb[4] > pick[4]
            if not better_dir:
                continue
            sl = self.slacks(nb[1], nb[2])
            name = min(sl, key=sl.get)
            if name == "coverage":
                continue

            def g(x, name=name):
                cp = curve_pt(x)
                if cp is None:
                    return math.nan
                return self.slacks(cp[0], cp[1]).get(name, math.nan)

            xa, xb = xs[pick[0]], xs[nb[0]]
            ga, gb = g(xa), g(xb)
            if math.isfinite(ga) and math.isfinite(gb) and ga * gb < 0:
                xr = brentq(g, xa, xb, xtol=1e-12, rtol=1e-14, maxiter=200)
                cp = curve_pt(xr)
                if cp is not None:
                    d, q = cp
                    if not self.feasible(d, q):
                        # step back towards the feasible side by a relative epsilon
                        xr2 = xr + (xa - xr) * 1e-9
                        cp2 = curve_pt(xr2)
                        if cp2 is not None:
                            d, q = cp2
                    if self.feasible(d, q):
                        return CurvePoint(d, q, d * d + q * q, f"{name}_boundary"), \
                            f"boundary refined by bisection on the {name} constraint along the curve"
        # interior extremum
        a = xs[left[0]] if left is not None else xs[pick[0]]
        b = xs[right[0]] if right is not None else xs[pick[0]]
        if b > a:
            sign = 1.0 if mode == "min" else -1.0

            def obj(x):
                cp = curve_pt(x)
                if cp is None:
                    return math.inf
                d, q = cp
                return sign * (d * d + q * q)

            res = minimize_scalar(obj, bounds=(a, b), method="bounded", options={"xatol": 1e-10})
            cp = curve_pt(res.x)
            if cp is not None and self.feasible(*cp):
                d, q = cp
                cand = CurvePoint(d, q, d * d + q * q, "stationary_I2")
                if (mode == "min" and better(cand, CurvePoint(pick[1], pick[2], pick[4]))) or \
                        (mode == "max" and worse(cand, CurvePoint(pick[1], pick[2], pick[4]))):
                    return cand, "interior extremum refined by bounded scalar minimisation along the curve"
        return CurvePoint(pick[1], pick[2], pick[4], "sampled"), "local re-sampling (41 points) without further refinement"

    # -- analysis -----------------------------------------------------------

    def analyze(self, T_shaft: float) -> CurveAnalysis:
        k = self.k
        tem_t = self._tem_target(T_shaft)
        notes = [f"sampled tracing: {self.ids.size} id rows" +
                 (f" x {self.iqs.size} iq samples (all map nodes included)" if self.is_map and not self.empty_region else
                  " (explicit iq(id) for the constant model)")]
        diag = {"coverage_distance_A": None if math.isinf(self.cov) else self.cov}
        limited = math.isfinite(self.cov)
        if limited:
            notes.append(f"allowed region not fully covered by model data: nearest uncovered allowed point at "
                         f"|i| = {self.cov:.6g} A")
        if self.empty_region:
            return CurveAnalysis(T_shaft, tem_t, "sampled_curve_tracing", False, (), None, None, limited,
                                 self.cov, tuple(notes + ["covered control region is empty"]), tuple(diag.items()))
        d, q, rows = self._row_roots(tem_t)
        if d.size == 0:
            return CurveAnalysis(T_shaft, tem_t, "sampled_curve_tracing", False, (), None, None, limited,
                                 self.cov, tuple(notes + ["no torque-matching point found in the covered region"]),
                                 tuple(diag.items()))
        counts = np.bincount(rows, minlength=self.ids.size)
        multi = bool(np.any(counts > 1))
        diag["rows_with_roots"] = int(np.sum(counts > 0))
        diag["max_roots_per_row"] = int(counts.max())
        if multi:
            notes.append("more than one torque-curve branch per id row: results are sampled; "
                         "segment structure not reported")
        feas = electrical_ok(k, d, q)
        i2 = d * d + q * q
        segs = []
        mn = mx = None
        if np.any(feas):
            idx = np.flatnonzero(feas)
            best = None
            worst_ = None
            for j in idx:
                cp = CurvePoint(float(d[j]), float(q[j]), float(i2[j]), "sampled")
                if better(cp, best):
                    best = cp
                if worse(cp, worst_):
                    worst_ = cp
            mn, note_mn = self.refine(best, tem_t, "min")
            mx, note_mx = self.refine(worst_, tem_t, "max")
            notes.append(f"minimum-current point: {note_mn}")
            notes.append(f"maximum-current point: {note_mx}")
            if not multi:
                order = np.argsort(d)
                run = []
                for j in order:
                    if feas[j]:
                        run.append(j)
                    elif run:
                        segs.append(self._seg(run, d, q, i2))
                        run = []
                if run:
                    segs.append(self._seg(run, d, q, i2))
        return CurveAnalysis(
            target_Tshaft_Nm=T_shaft, target_Tem_Nm=tem_t, method="sampled_curve_tracing", exact=False,
            segments=tuple(segs), min_point=mn, max_point=mx, coverage_limited=limited,
            coverage_distance_A=self.cov, notes=tuple(notes), diagnostics=tuple(diag.items()))

    @staticmethod
    def _seg(run, d, q, i2):
        pts = [CurvePoint(float(d[j]), float(q[j]), float(i2[j]), "sampled") for j in run]
        mn = mx = None
        for p in pts:
            if better(p, mn):
                mn = p
            if worse(p, mx):
                mx = p
        return CurveSegment(pts[0], pts[-1], mn, mx)
