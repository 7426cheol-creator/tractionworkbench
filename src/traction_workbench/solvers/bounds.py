"""Rigorous cell bounds (branch and bound) over the covered control region.

Inside one flux-map cell the interpolant is bilinear, so the ranges of psi_d
and psi_q over any sub-rectangle are attained at its corners (exactly).  From
these ranges, interval arithmetic gives conservative bounds of

    T_em  = 1.5 p (psi_d*iq - psi_q*id)
    |v|^2 >= mig(v_d)^2 + mig(v_q)^2          (mig = smallest |x| in interval)
    I^2   in [dist^2(origin, cell), max corner distance^2]
    P_dc  = T_em*omega_m + (1.5 Rs + a2) I^2 + a0

A cell whose bounds already violate a hard constraint (or cannot contain the
requested torque) contains no feasible point and is discarded; the rest are
split in four.  If no cell survives, infeasibility *within the covered data*
is proven.  Surviving cells bound the global minimum current or the maximum
torque from below/above.  The same machinery works for the constant model
(psi is affine, so corner ranges are exact there too).

Floating-point rounding in the interval arithmetic is covered by a relative
safety margin of 1e-12; bounds are only as good as the model data they use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..physics import DriveKernel

SAFETY = 1e-12


def _prod(a_lo, a_hi, b_lo, b_hi):
    c = np.stack([a_lo * b_lo, a_lo * b_hi, a_hi * b_lo, a_hi * b_hi])
    return c.min(axis=0), c.max(axis=0)


def _mig(lo, hi):
    return np.where((lo <= 0) & (hi >= 0), 0.0, np.minimum(np.abs(lo), np.abs(hi)))


@dataclass
class BoundResult:
    status: str               # "PROVEN_EMPTY", "BOUNDED", "UNRESOLVED"
    bound: float | None       # meaning depends on the query
    cells_remaining: int
    depth: int
    cells_evaluated: int
    note: str

    def to_dict(self) -> dict:
        return {"status": self.status, "bound": self.bound, "cells_remaining": self.cells_remaining,
                "depth": self.depth, "cells_evaluated": self.cells_evaluated, "note": self.note}


class CellBounds:
    def __init__(self, k: DriveKernel, base_step_A: float = 10.0):
        self.k = k
        dom = k.domain
        box = [dom.id_A[0], dom.id_A[1], dom.iq_A[0], dom.iq_A[1]]
        # the current disk bounds the useful region
        box = [max(box[0], -k.Imax), min(box[1], k.Imax), max(box[2], -k.Imax), min(box[3], k.Imax)]
        cells = []
        if k.kind == "flux_map":
            pl = k.plane
            ad, aq = pl.id_axis_A, pl.iq_axis_A
            for i, j in np.argwhere(pl.cell_valid):
                d0, d1 = max(ad[i], box[0]), min(ad[i + 1], box[1])
                q0, q1 = max(aq[j], box[2]), min(aq[j + 1], box[3])
                if d0 <= d1 and q0 <= q1:
                    cells.append((d0, d1, q0, q1))
        else:
            v = k.drive.motor.flux.validity
            if v is not None:
                box = [max(box[0], v.id_A[0]), min(box[1], v.id_A[1]), max(box[2], v.iq_A[0]), min(box[3], v.iq_A[1])]
            if box[0] <= box[1] and box[2] <= box[3]:
                nd = max(1, int(math.ceil((box[1] - box[0]) / base_step_A)))
                nq = max(1, int(math.ceil((box[3] - box[2]) / base_step_A)))
                de = np.linspace(box[0], box[1], nd + 1)
                qe = np.linspace(box[2], box[3], nq + 1)
                for i in range(nd):
                    for j in range(nq):
                        cells.append((de[i], de[i + 1], qe[j], qe[j + 1]))
        self.base = np.array(cells, dtype=float).reshape(-1, 4)

    # -- interval evaluation ---------------------------------------------

    def evaluate(self, cells: np.ndarray) -> dict:
        k = self.k
        d0, d1, q0, q1 = cells.T
        cd = np.stack([d0, d1, d0, d1], axis=1)
        cq = np.stack([q0, q0, q1, q1], axis=1)
        psd, psq, ok = k.flux(cd, cq)
        ok_all = np.all(ok, axis=1)
        psd = np.where(ok, psd, np.nan)
        psq = np.where(ok, psq, np.nan)
        a_lo, a_hi = np.nanmin(psd, axis=1), np.nanmax(psd, axis=1)
        b_lo, b_hi = np.nanmin(psq, axis=1), np.nanmax(psq, axis=1)
        p1 = _prod(a_lo, a_hi, q0, q1)
        p2 = _prod(b_lo, b_hi, d0, d1)
        kp = 1.5 * k.p
        tem_lo = kp * (p1[0] - p2[1])
        tem_hi = kp * (p1[1] - p2[0])
        we = k.omega_e
        rv = k.Rs + k.R_drop
        wb = (we * b_lo, we * b_hi) if we >= 0 else (we * b_hi, we * b_lo)
        wa = (we * a_lo, we * a_hi) if we >= 0 else (we * a_hi, we * a_lo)
        vd_lo, vd_hi = rv * d0 - wb[1], rv * d1 - wb[0]
        vq_lo, vq_hi = rv * q0 + wa[0], rv * q1 + wa[1]
        v2_lb = _mig(vd_lo, vd_hi) ** 2 + _mig(vq_lo, vq_hi) ** 2
        dx = np.where((d0 <= 0) & (d1 >= 0), 0.0, np.minimum(np.abs(d0), np.abs(d1)))
        dy = np.where((q0 <= 0) & (q1 >= 0), 0.0, np.minimum(np.abs(q0), np.abs(q1)))
        i2_lb = dx * dx + dy * dy
        i2_ub = np.maximum(np.abs(d0), np.abs(d1)) ** 2 + np.maximum(np.abs(q0), np.abs(q1)) ** 2
        out = {"tem_lo": tem_lo * (1 - np.sign(tem_lo) * SAFETY) - SAFETY,
               "tem_hi": tem_hi * (1 + np.sign(tem_hi) * SAFETY) + SAFETY,
               "v2_lb": v2_lb * (1 - SAFETY), "i2_lb": i2_lb * (1 - SAFETY), "i2_ub": i2_ub * (1 + SAFETY),
               "ok": ok_all}
        if k.inv_loss is not None:
            c2 = 1.5 * k.Rs + k.inv_loss.ipk2_coeff_W_per_A2
            wm = k.omega_m
            t_lo, t_hi = (tem_lo * wm, tem_hi * wm) if wm >= 0 else (tem_hi * wm, tem_lo * wm)
            out["pdc_lo"] = t_lo + c2 * i2_lb + k.inv_loss.offset_W - SAFETY * (1 + np.abs(t_lo))
            out["pdc_hi"] = t_hi + c2 * i2_ub + k.inv_loss.offset_W + SAFETY * (1 + np.abs(t_hi))
        return out

    def _may(self, ev: dict, tem_target: float | None, include_dc: bool) -> np.ndarray:
        k = self.k
        s = k.settings
        m = ev["ok"].copy()
        vtol = max(s.voltage_abs_tol_V, s.constraint_rel_tol * k.Vb)
        itol = max(s.current_abs_tol_A, s.constraint_rel_tol * k.Imax)
        m &= ev["v2_lb"] <= (k.Vb + vtol) ** 2
        m &= ev["i2_lb"] <= (k.Imax + itol) ** 2
        if tem_target is not None:
            m &= (ev["tem_lo"] <= tem_target) & (ev["tem_hi"] >= tem_target)
        if include_dc and "pdc_lo" in ev:
            ptol = s.power_abs_tol_W
            if k.P_dis_eff is not None:
                m &= ev["pdc_lo"] <= k.P_dis_eff + max(ptol, s.constraint_rel_tol * abs(k.P_dis_eff))
            if k.P_chg_eff is not None:
                m &= ev["pdc_hi"] >= -k.P_chg_eff - max(ptol, s.constraint_rel_tol * abs(k.P_chg_eff))
        return m

    @staticmethod
    def _split(cells: np.ndarray) -> np.ndarray:
        d0, d1, q0, q1 = cells.T
        dm, qm = 0.5 * (d0 + d1), 0.5 * (q0 + q1)
        return np.concatenate([
            np.stack([d0, dm, q0, qm], axis=1), np.stack([dm, d1, q0, qm], axis=1),
            np.stack([d0, dm, qm, q1], axis=1), np.stack([dm, d1, qm, q1], axis=1)])

    # -- queries -----------------------------------------------------------

    def prove_empty(self, tem_target: float | None, include_dc: bool = False) -> BoundResult:
        """Try to prove that no covered, allowed point meets the torque target and constraints."""
        s = self.k.settings
        cells = self.base
        evaluated = 0
        for depth in range(s.bnb_max_depth + 1):
            if cells.shape[0] == 0:
                return BoundResult("PROVEN_EMPTY", None, 0, depth, evaluated,
                                   "every covered cell excluded by interval bounds")
            ev = self.evaluate(cells)
            evaluated += cells.shape[0]
            cells = cells[self._may(ev, tem_target, include_dc)]
            if cells.shape[0] == 0:
                return BoundResult("PROVEN_EMPTY", None, 0, depth, evaluated,
                                   "every covered cell excluded by interval bounds")
            if depth < s.bnb_max_depth:
                if cells.shape[0] * 4 > s.bnb_max_cells:
                    break
                cells = self._split(cells)
        return BoundResult("UNRESOLVED", None, int(cells.shape[0]), depth, evaluated,
                           "cells that may contain feasible points remain at the depth/cell limit")

    def min_current_lower_bound(self, tem_target: float, incumbent_I2: float, rel_gap: float = 1e-6) -> BoundResult:
        """Lower bound on I^2 over feasible torque-matching points (within covered data)."""
        s = self.k.settings
        cells = self.base
        evaluated = 0
        lb = incumbent_I2
        for depth in range(s.bnb_max_depth + 1):
            if cells.shape[0] == 0:
                break
            ev = self.evaluate(cells)
            evaluated += cells.shape[0]
            keep = self._may(ev, tem_target, False) & (ev["i2_lb"] <= incumbent_I2)
            cells = cells[keep]
            if cells.shape[0] == 0:
                lb = incumbent_I2
                break
            lb = float(ev["i2_lb"][keep].min())
            if lb >= incumbent_I2 * (1 - rel_gap):
                break
            if depth < s.bnb_max_depth:
                if cells.shape[0] * 4 > s.bnb_max_cells:
                    break
                cells = self._split(cells)
        return BoundResult("BOUNDED", lb, int(cells.shape[0]), depth, evaluated,
                           "I^2 lower bound over cells that may hold feasible torque-matching points")

    def torque_bound(self, direction: int, incumbent_T: float, include_dc: bool = True,
                     abs_gap: float = 1e-3) -> BoundResult:
        """Upper (direction=+1) or lower (direction=-1) bound of shaft torque over the feasible set."""
        k = self.k
        s = k.settings
        tau = k.tau_rot_or_zero
        cells = self.base
        evaluated = 0
        bound = None
        for depth in range(s.bnb_max_depth + 1):
            if cells.shape[0] == 0:
                break
            ev = self.evaluate(cells)
            evaluated += cells.shape[0]
            may = self._may(ev, None, include_dc)
            if direction > 0:
                t_ext = ev["tem_hi"] - tau
                keep = may & (t_ext >= incumbent_T)
            else:
                t_ext = ev["tem_lo"] - tau
                keep = may & (t_ext <= incumbent_T)
            cells = cells[keep]
            if cells.shape[0] == 0:
                bound = incumbent_T
                break
            bound = float(t_ext[keep].max() if direction > 0 else t_ext[keep].min())
            if abs(bound - incumbent_T) <= abs_gap:
                break
            if depth < s.bnb_max_depth:
                if cells.shape[0] * 4 > s.bnb_max_cells:
                    break
                cells = self._split(cells)
        if bound is None:
            return BoundResult("PROVEN_EMPTY", None, 0, 0, evaluated, "no covered cell may be feasible")
        return BoundResult("BOUNDED", bound, int(cells.shape[0]), depth, evaluated,
                           f"shaft-torque {'upper' if direction > 0 else 'lower'} bound over cells that may be feasible")
