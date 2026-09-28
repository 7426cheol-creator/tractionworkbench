"""Magnetic models.

* ``ConstantFluxModel``: psi_d = psi_PM + Ld*id, psi_q = Lq*iq.
* ``FluxMapModel``: nonlinear dq flux linkage given on an explicit (id, iq)
  grid per magnet-temperature plane, with a node validity mask.

Flux maps are interpolated bilinearly *inside valid cells only* (a cell is
valid when its four corner nodes are valid).  Points outside the axes, in a
hole, in an undeclared quadrant or at an unvalidated temperature raise or
flag ``OutsideModelDomain``; nothing is extrapolated.  Symmetry is used only
when it is declared in the data contract.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..errors import InputValidationError, OutsideModelDomain
from ..validation import axis as _axis, finite as _finite, interval as _interval  # noqa: F401 (re-export)

_INF = math.inf




@dataclass(frozen=True)
class CurrentBox:
    """A rectangular (id, iq) region, optionally with a current-magnitude cap."""

    id_A: tuple[float, float]
    iq_A: tuple[float, float]

    def __post_init__(self):
        object.__setattr__(self, "id_A", _interval("id_A", self.id_A))
        object.__setattr__(self, "iq_A", _interval("iq_A", self.iq_A))


def _box_distance(d0: float, d1: float, q0: float, q1: float) -> float:
    dx = 0.0 if d0 <= 0.0 <= d1 else min(abs(d0), abs(d1))
    dy = 0.0 if q0 <= 0.0 <= q1 else min(abs(q0), abs(q1))
    return math.hypot(dx, dy)


def uncovered_distance(rects, allowed: CurrentBox, i_max: float) -> float:
    """Infimum distance from the origin to an *allowed* point that is not covered (independent review R2, C02).

    Coverage is SET INCLUSION, not area: an allowed set of zero width (a fixed id or iq, a single point, a control
    line on or next to the data edge) is intersected like any other set.  ``rects`` (d0, d1, q0, q1, is_open)
    together contain every uncovered point: open ones are half-planes of the complement of a closed covered box
    (strict at their finite bounds, unbounded otherwise), closed ones are invalid map cells (edge contact counts -
    conservative).  The distance is taken to the closure of each intersection (an infimum: never larger than the
    true distance).  Returns +inf when every allowed point with |i| <= i_max is covered.
    """
    best = _INF
    A0, A1 = allowed.id_A
    B0, B1 = allowed.iq_A
    for d0, d1, q0, q1, is_open in rects:
        if is_open:
            hit = d0 < A1 and A0 < d1 and q0 < B1 and B0 < q1          # open set vs closed allowed box
        else:
            hit = d0 <= A1 and A0 <= d1 and q0 <= B1 and B0 <= q1      # closed cell vs closed allowed box
        if not hit:
            continue
        dist = _box_distance(max(d0, A0), min(d1, A1), max(q0, B0), min(q1, B1))
        if dist <= i_max:
            best = min(best, dist)
    return best


def complement_half_planes(d0: float, d1: float, q0: float, q1: float) -> list:
    """The complement of the closed box [d0, d1] x [q0, q1] as four open half-planes (overlap is harmless)."""
    return [(-_INF, d0, -_INF, _INF, True), (d1, _INF, -_INF, _INF, True),
            (-_INF, _INF, -_INF, q0, True), (-_INF, _INF, q1, _INF, True)]


@dataclass(frozen=True)
class ConstantFluxModel:
    psi_pm_Wb: float
    Ld_H: float
    Lq_H: float
    validity: CurrentBox | None = None

    kind = "constant_dq"

    def __post_init__(self):
        psi = _finite("psi_pm_Wb", self.psi_pm_Wb)
        ld = _finite("Ld_H", self.Ld_H)
        lq = _finite("Lq_H", self.Lq_H)
        if psi < 0:
            raise InputValidationError("PM flux linkage must be >= 0", field="psi_pm_Wb")
        if ld <= 0:
            raise InputValidationError("Ld must be > 0", field="Ld_H")
        if lq <= 0:
            raise InputValidationError("Lq must be > 0", field="Lq_H")
        if psi == 0 and ld == lq:
            raise InputValidationError("model produces no torque (psi_PM = 0 and Ld = Lq)", field="psi_pm_Wb")
        object.__setattr__(self, "psi_pm_Wb", psi)
        object.__setattr__(self, "Ld_H", ld)
        object.__setattr__(self, "Lq_H", lq)

    @property
    def saliency_H(self) -> float:
        return self.Ld_H - self.Lq_H

    def flux(self, id_A, iq_A, psi_pm_Wb: float | None = None):
        psi = self.psi_pm_Wb if psi_pm_Wb is None else psi_pm_Wb
        id_A = np.asarray(id_A, dtype=float)
        iq_A = np.asarray(iq_A, dtype=float)
        psd = psi + self.Ld_H * id_A
        psq = self.Lq_H * iq_A
        if self.validity is None:
            ok = np.isfinite(psd) & np.isfinite(psq)
        else:
            ok = (
                (id_A >= self.validity.id_A[0]) & (id_A <= self.validity.id_A[1])
                & (iq_A >= self.validity.iq_A[0]) & (iq_A <= self.validity.iq_A[1])
            )
        return psd, psq, ok

    def uncovered_rectangles(self):
        if self.validity is None:
            return []
        return complement_half_planes(*self.validity.id_A, *self.validity.iq_A)

    def describe(self) -> dict:
        out = {
            "kind": self.kind,
            "psi_pm_Wb": self.psi_pm_Wb,
            "Ld_H": self.Ld_H,
            "Lq_H": self.Lq_H,
            "machine_type": "SPMSM (Ld = Lq)" if self.Ld_H == self.Lq_H else "IPMSM / salient",
        }
        out["validity"] = (
            "not declared separately; parameters assumed valid on the declared operating domain"
            if self.validity is None
            else {"id_A": list(self.validity.id_A), "iq_A": list(self.validity.iq_A)}
        )
        return out




@dataclass(frozen=True, eq=False)
class FluxMapPlane:
    """One magnet-temperature plane: psi_d, psi_q[i_id, j_iq] on explicit axes."""

    id_axis_A: np.ndarray
    iq_axis_A: np.ndarray
    psi_d_Wb: np.ndarray
    psi_q_Wb: np.ndarray
    valid: np.ndarray | None = None
    magnet_temp_C: float | None = None
    label: str = ""

    def __post_init__(self):
        ax_d = _axis("id_axis_A", self.id_axis_A)
        ax_q = _axis("iq_axis_A", self.iq_axis_A)
        shape = (ax_d.size, ax_q.size)
        arrays = {}
        for name in ("psi_d_Wb", "psi_q_Wb"):
            try:
                arr = np.array(getattr(self, name), dtype=float)
            except (TypeError, ValueError):
                raise InputValidationError("flux array must be numeric", field=name) from None
            if arr.shape != shape:
                hint = " (rows must follow the id axis, columns the iq axis)" if arr.shape == shape[::-1] else ""
                raise InputValidationError(f"shape {arr.shape} does not match axes {shape}{hint}", field=name)
            arrays[name] = arr
        if self.valid is None:
            mask = np.ones(shape, dtype=bool)
        else:
            mask = np.array(self.valid)
            if mask.shape != shape:
                raise InputValidationError(f"validity mask shape {mask.shape} does not match axes {shape}", field="valid")
            if mask.dtype != bool:
                if not np.all(np.isin(mask, (0, 1))):
                    raise InputValidationError("validity mask must be boolean", field="valid")
                mask = mask.astype(bool)
        for name, arr in arrays.items():
            bad = mask & ~np.isfinite(arr)
            if np.any(bad):
                i, j = np.argwhere(bad)[0]
                raise InputValidationError(
                    f"non-finite value at valid node (id={ax_d[i]}, iq={ax_q[j]})", field=name
                )
            arr.setflags(write=False)
        mask.setflags(write=False)
        cell = mask[:-1, :-1] & mask[1:, :-1] & mask[:-1, 1:] & mask[1:, 1:]
        if not cell.any():
            raise InputValidationError("flux map has no valid cell", field="valid")
        cell.setflags(write=False)
        if self.magnet_temp_C is not None:
            object.__setattr__(self, "magnet_temp_C", _finite("magnet_temp_C", self.magnet_temp_C))
        object.__setattr__(self, "id_axis_A", ax_d)
        object.__setattr__(self, "iq_axis_A", ax_q)
        object.__setattr__(self, "psi_d_Wb", arrays["psi_d_Wb"])
        object.__setattr__(self, "psi_q_Wb", arrays["psi_q_Wb"])
        object.__setattr__(self, "valid", mask)
        object.__setattr__(self, "cell_valid", cell)

    # -- interpolation ----------------------------------------------------

    def _locate(self, d: np.ndarray, q: np.ndarray):
        ax_d, ax_q = self.id_axis_A, self.iq_axis_A
        nd, nq = ax_d.size, ax_q.size
        finite = np.isfinite(d) & np.isfinite(q)
        inside = finite & (d >= ax_d[0]) & (d <= ax_d[-1]) & (q >= ax_q[0]) & (q <= ax_q[-1])
        dd = np.where(finite, d, ax_d[0])
        qq = np.where(finite, q, ax_q[0])
        i = np.clip(np.searchsorted(ax_d, dd, side="right") - 1, 0, nd - 2)
        j = np.clip(np.searchsorted(ax_q, qq, side="right") - 1, 0, nq - 2)
        ok = inside & self.cell_valid[i, j]
        need = inside & ~ok
        if np.any(need):
            # A point on a cell edge is covered if any cell containing it is valid.
            on_d = need & (i >= 1) & (dd == ax_d[i])
            on_q = need & (j >= 1) & (qq == ax_q[j])
            for di, dj, sel in ((1, 0, on_d), (0, 1, on_q), (1, 1, on_d & on_q)):
                cand = sel & ~ok
                if not np.any(cand):
                    continue
                ii = np.where(cand, i - di, i)
                jj = np.where(cand, j - dj, j)
                good = cand & self.cell_valid[ii, jj]
                i = np.where(good, ii, i)
                j = np.where(good, jj, j)
                ok = ok | good
        return i, j, ok

    def interpolate(self, id_A, iq_A):
        """Bilinear psi_d, psi_q and a coverage mask (False -> NaN values)."""
        d, q = np.broadcast_arrays(np.asarray(id_A, dtype=float), np.asarray(iq_A, dtype=float))
        shape = d.shape
        d = d.ravel()
        q = q.ravel()
        i, j, ok = self._locate(d, q)
        ax_d, ax_q = self.id_axis_A, self.iq_axis_A
        t = (d - ax_d[i]) / (ax_d[i + 1] - ax_d[i])
        u = (q - ax_q[j]) / (ax_q[j + 1] - ax_q[j])
        out = []
        with np.errstate(invalid="ignore"):
            for arr in (self.psi_d_Wb, self.psi_q_Wb):
                v = (
                    (1 - t) * (1 - u) * arr[i, j]
                    + t * (1 - u) * arr[i + 1, j]
                    + (1 - t) * u * arr[i, j + 1]
                    + t * u * arr[i + 1, j + 1]
                )
                out.append(np.where(ok, v, np.nan).reshape(shape))
        return out[0], out[1], ok.reshape(shape)

    # -- coverage ---------------------------------------------------------

    def uncovered_rectangles(self):
        ax_d, ax_q = self.id_axis_A, self.iq_axis_A
        rects = complement_half_planes(float(ax_d[0]), float(ax_d[-1]), float(ax_q[0]), float(ax_q[-1]))
        for i, j in np.argwhere(~self.cell_valid):
            rects.append((float(ax_d[i]), float(ax_d[i + 1]), float(ax_q[j]), float(ax_q[j + 1]), False))
        return rects

    @property
    def data_sha256(self) -> str:
        """Digest of axes, flux arrays and mask, so decision records identify the exact map data."""
        import hashlib
        h = hashlib.sha256()
        for arr in (self.id_axis_A, self.iq_axis_A, self.psi_d_Wb, self.psi_q_Wb):
            h.update(np.ascontiguousarray(np.where(np.isfinite(arr), arr, 0.0), dtype="<f8").tobytes())
        h.update(np.ascontiguousarray(self.valid, dtype=np.uint8).tobytes())
        return h.hexdigest()

    def coverage_summary(self) -> dict:
        return {
            "data_sha256": self.data_sha256,
            "id_axis_A": [float(self.id_axis_A[0]), float(self.id_axis_A[-1]), int(self.id_axis_A.size)],
            "iq_axis_A": [float(self.iq_axis_A[0]), float(self.iq_axis_A[-1]), int(self.iq_axis_A.size)],
            "valid_nodes": int(self.valid.sum()),
            "total_nodes": int(self.valid.size),
            "valid_cells": int(self.cell_valid.sum()),
            "total_cells": int(self.cell_valid.size),
            "magnet_temp_C": self.magnet_temp_C,
        }

    # -- magnetic consistency --------------------------------------------

    def _node_derivatives(self):
        """Second-order derivatives at interior nodes, valid on NON-uniform axes (independent review F10).

        f'(x_i) ~ [h-^2 f(i+1) - h+^2 f(i-1) + (h+^2 - h-^2) f(i)] / (h- h+ (h- + h+));
        the plain secant (f(i+1) - f(i-1)) / (x(i+1) - x(i-1)) is only first order on a non-uniform grid.
        """
        ax_d, ax_q = self.id_axis_A, self.iq_axis_A
        psd, psq = self.psi_d_Wb, self.psi_q_Wb
        hm_d = (ax_d[1:-1] - ax_d[:-2])[:, None]
        hp_d = (ax_d[2:] - ax_d[1:-1])[:, None]
        hm_q = (ax_q[1:-1] - ax_q[:-2])[None, :]
        hp_q = (ax_q[2:] - ax_q[1:-1])[None, :]

        def dd(f):
            return (hm_d ** 2 * f[2:, 1:-1] - hp_d ** 2 * f[:-2, 1:-1] + (hp_d ** 2 - hm_d ** 2) * f[1:-1, 1:-1]) / \
                (hm_d * hp_d * (hm_d + hp_d))

        def dq(f):
            return (hm_q ** 2 * f[1:-1, 2:] - hp_q ** 2 * f[1:-1, :-2] + (hp_q ** 2 - hm_q ** 2) * f[1:-1, 1:-1]) / \
                (hm_q * hp_q * (hm_q + hp_q))

        with np.errstate(invalid="ignore"):
            return dd(psd), dq(psd), dd(psq), dq(psq)

    def reciprocity_report(self, rel_tol: float = 1e-3, abs_tol_H: float = 1e-12) -> dict:
        """Node plausibility of the *data*: cross-derivative reciprocity and positive-definite differential inductance.

        Second-order node derivatives on interior nodes whose full stencil is valid (non-uniform axes handled).
        Passing this check is a static data-consistency statement at the nodes only; it does not qualify the
        interpolant for dynamic use (see ``magnetic_qualification``) and does not validate the saturation model.
        """
        m = self.valid
        stencil = m[1:-1, 1:-1] & m[2:, 1:-1] & m[:-2, 1:-1] & m[1:-1, 2:] & m[1:-1, :-2]
        if not np.any(stencil):
            return {"checked_nodes": 0, "passed": None, "note": "no interior node with a complete valid stencil"}
        ldd, ldq, lqd, lqq = self._node_derivatives()
        mism = np.abs(ldq - lqd)[stencil]
        scale = np.maximum(np.abs(ldd), np.abs(lqq))[stencil]
        tol = np.maximum(rel_tol * scale, abs_tol_H)
        recip_fail = int(np.sum(mism > tol))
        lsym = 0.5 * (ldq + lqd)
        pd = (ldd > 0) & (lqq > 0) & (ldd * lqq - lsym ** 2 > 0)
        pd_fail = int(np.sum(~pd[stencil]))
        return {
            "method": "second-order node derivatives (non-uniform axes) on interior nodes with a complete valid stencil",
            "scope": "static data plausibility at the nodes; not a dynamic (interpolant) qualification",
            "checked_nodes": int(stencil.sum()),
            "max_abs_mismatch_H": float(mism.max()),
            "max_rel_mismatch": float(np.max(mism / np.maximum(scale, 1e-300))),
            "tolerance": {"rel": rel_tol, "abs_H": abs_tol_H},
            "nodes_failing_reciprocity": recip_fail,
            "nodes_not_positive_definite": pd_fail,
            "min_Ldd_H": float(np.min(ldd[stencil])),
            "min_Lqq_H": float(np.min(lqq[stencil])),
            "passed": recip_fail == 0 and pd_fail == 0,
            "note": "finite-difference truncation error is part of the tolerance budget; "
                    "this check does not validate the saturation model against hardware",
        }

    def interpolant_consistency(self) -> dict:
        """Off-grid properties of the bilinear interpolant actually used by the solvers.

        * closed-path work W = contour integral of (psi_d did + psi_q diq) around every valid cell (exact for the
          bilinear interpolant: psi is linear along each edge).  W = 0 for a conservative (energy-consistent)
          model; the bilinear sampling of even a conservative map leaves W != 0;
        * differential-inductance jumps across cell edges: the bilinear interpolant has a piecewise, discontinuous
          Jacobian, which a dynamic (current-state) model must not use as L_diff;
        * interior asymmetry d psi_q/d id - d psi_d/d iq of the interpolant: the cell-boundary work only measures
          its cell average, which can vanish while the pointwise mismatch does not (audit evidence F10).  In a
          bilinear cell d psi_q/d id is affine in the q-coordinate and d psi_d/d iq affine in the d-coordinate, so
          the maximum |asymmetry| is attained at a cell corner - the value below is exact, not sampled.
        """
        ax_d, ax_q = self.id_axis_A, self.iq_axis_A
        psd, psq = self.psi_d_Wb, self.psi_q_Wb
        cv = self.cell_valid
        dd = np.diff(ax_d)[:, None]
        dq = np.diff(ax_q)[None, :]
        with np.errstate(invalid="ignore"):
            bottom = 0.5 * (psd[:-1, :-1] + psd[1:, :-1]) * dd
            right = 0.5 * (psq[1:, :-1] + psq[1:, 1:]) * dq
            top = -0.5 * (psd[1:, 1:] + psd[:-1, 1:]) * dd
            left = -0.5 * (psq[:-1, 1:] + psq[:-1, :-1]) * dq
            work = bottom + right + top + left
            scale = (np.abs(psd[:-1, :-1]) + np.abs(psq[:-1, :-1]) + 1e-300) * (dd + dq)
        w = np.abs(work[cv])
        rel = (np.abs(work) / scale)[cv]
        # Jacobian of the bilinear interpolant inside each cell at the cell edges; jump across interior id-edges
        with np.errstate(invalid="ignore"):
            ldd_cell = (psd[1:, :-1] - psd[:-1, :-1]) / dd          # d psi_d / d id along the lower edge of each cell
            jumps = np.abs(ldd_cell[1:, :] - ldd_cell[:-1, :])
            both = cv[1:, :] & cv[:-1, :]
            jscale = np.maximum(np.abs(ldd_cell[1:, :]), np.abs(ldd_cell[:-1, :])) + 1e-300
        jrel = (jumps / jscale)[both] if np.any(both) else np.array([0.0])
        with np.errstate(invalid="ignore"):
            lqd = [(psq[1:, :-1] - psq[:-1, :-1]) / dd, (psq[1:, 1:] - psq[:-1, 1:]) / dd]      # u = 0, u = 1
            ldq = [(psd[:-1, 1:] - psd[:-1, :-1]) / dq, (psd[1:, 1:] - psd[1:, :-1]) / dq]      # t = 0, t = 1
            asym = np.maximum.reduce([np.abs(a - b) for a in lqd for b in ldq])
            lself = np.maximum.reduce([np.abs((psd[1:, :-1] - psd[:-1, :-1]) / dd), np.abs((psd[1:, 1:] - psd[:-1, 1:]) / dd),
                                       np.abs((psq[:-1, 1:] - psq[:-1, :-1]) / dq), np.abs((psq[1:, 1:] - psq[1:, :-1]) / dq)])
        arel = (asym / (lself + 1e-300))[cv]
        return {
            "cells": int(cv.sum()),
            "max_closed_path_work_J": float(w.max()) if w.size else 0.0,
            "max_rel_closed_path_work": float(rel.max()) if rel.size else 0.0,
            "max_rel_Ldd_jump_across_edges": float(jrel.max()),
            "max_interior_asymmetry_H": float(asym[cv].max()) if np.any(cv) else 0.0,
            "max_rel_interior_asymmetry": float(arel.max()) if arel.size else 0.0,
            "note": "per-unit-of-3/2 energy (Wb*A = J); the factor 3/2 converts to machine co-energy",
        }

    def magnetic_qualification(self, rel_tol: float = 1e-3) -> dict:
        """Static and dynamic use kept apart (review F10): node plausibility is not dynamic conservativeness."""
        nodes = self.reciprocity_report(rel_tol)
        interp = self.interpolant_consistency()
        static_ok = nodes.get("passed")
        return {
            "static_use": {
                "status": ("PLAUSIBLE at the nodes" if static_ok else
                           "NOT CHECKED (no complete stencil)" if static_ok is None else "INCONSISTENT at the nodes"),
                "basis": "reciprocity and positive-definite differential inductance of the data at the nodes",
                "node_checks": nodes,
                "limits": "does not validate the saturation model against hardware or FEA holdouts",
            },
            "dynamic_use": {
                "status": "NOT QUALIFIED",
                "reasons": ["bilinear interpolant: piecewise-constant, discontinuous Jacobian (L_diff jumps across cell "
                            f"edges up to {interp['max_rel_Ldd_jump_across_edges']:.3g} relative)",
                            "closed-path work of the interpolant is not zero (max relative "
                            f"{interp['max_rel_closed_path_work']:.3g}): not energy-consistent",
                            "pointwise cross-derivative asymmetry inside cells (L_dq != L_qd off-grid) up to "
                            f"{interp['max_rel_interior_asymmetry']:.3g} relative (exact corner maximum)",
                            "no declared dynamic qualification (inverse map, conditioning, off-grid holdouts)"],
                "interpolant": interp,
                "meaning": "static steady-state solves may use the map; transients (ASC, current-control) need a "
                           "qualified energy-consistent model",
            },
        }


def _mirror_q(plane: FluxMapPlane) -> FluxMapPlane:
    """Apply declared q-axis symmetry: psi_d even in iq, psi_q odd in iq."""
    ax_q = plane.iq_axis_A
    # the zero seam: odd symmetry requires psi_q(id, iq = 0) = 0 (otherwise the mirrored map jumps at iq = 0)
    j0 = 0 if ax_q[0] == 0.0 else (ax_q.size - 1 if ax_q[-1] == 0.0 else None)
    if j0 is not None:
        seam = np.abs(plane.psi_q_Wb[:, j0][plane.valid[:, j0]])
        ref = max(float(np.nanmax(np.abs(plane.psi_q_Wb[plane.valid]))), 1e-12)
        if seam.size and float(seam.max()) > 1e-6 * ref:
            raise InputValidationError(
                f"declared q-odd symmetry needs psi_q(iq = 0) = 0, found {float(seam.max()):.3g} Wb at the seam",
                field="symmetry")
    if ax_q[0] == 0.0 and ax_q[-1] > 0:
        neg = -ax_q[:0:-1]
        new_q = np.concatenate([neg, ax_q])
        idx = np.concatenate([np.arange(ax_q.size - 1, 0, -1), np.arange(ax_q.size)])
        sign = np.concatenate([-np.ones(ax_q.size - 1), np.ones(ax_q.size)])
    elif ax_q[-1] == 0.0 and ax_q[0] < 0:
        pos = -ax_q[-2::-1]
        new_q = np.concatenate([ax_q, pos])
        idx = np.concatenate([np.arange(ax_q.size), np.arange(ax_q.size - 2, -1, -1)])
        sign = np.concatenate([np.ones(ax_q.size), -np.ones(ax_q.size - 1)])
    else:
        raise InputValidationError(
            "declared q-axis symmetry needs a half map whose iq axis starts or ends at 0",
            field="symmetry",
        )
    psd = plane.psi_d_Wb[:, idx]
    psq = plane.psi_q_Wb[:, idx] * sign[None, :]
    valid = plane.valid[:, idx]
    return FluxMapPlane(plane.id_axis_A, new_q, psd, psq, valid, plane.magnet_temp_C,
                        (plane.label + " [q-odd symmetry applied]").strip())


@dataclass(frozen=True, eq=False)
class FluxMapModel:
    planes: tuple
    conservative: bool = True
    symmetry: str | None = None
    temperature_interpolation: str | None = None
    temperature_interpolation_basis: str = ""
    reciprocity_rel_tol: float = 1e-3
    temperature_match_tol_C: float = 0.01

    kind = "flux_map"

    def __post_init__(self):
        planes = tuple(self.planes)
        if not planes:
            raise InputValidationError("flux map needs at least one plane", field="planes")
        if not all(isinstance(p, FluxMapPlane) for p in planes):
            raise InputValidationError("planes must be FluxMapPlane objects", field="planes")
        if len(planes) > 1:
            temps = [p.magnet_temp_C for p in planes]
            if any(t is None for t in temps):
                raise InputValidationError("every plane of a multi-temperature map needs magnet_temp_C", field="planes")
            if len(set(temps)) != len(temps):
                raise InputValidationError("duplicate magnet temperature planes", field="planes")
            planes = tuple(sorted(planes, key=lambda p: p.magnet_temp_C))
        if self.symmetry not in (None, "q_odd"):
            raise InputValidationError(f"unsupported symmetry {self.symmetry!r}; only 'q_odd' may be declared",
                                       field="symmetry")
        if self.temperature_interpolation not in (None, "linear"):
            raise InputValidationError("temperature_interpolation must be None or 'linear'",
                                       field="temperature_interpolation")
        if self.temperature_interpolation == "linear":
            if not self.temperature_interpolation_basis.strip():
                raise InputValidationError("temperature interpolation requires a stated basis/evidence",
                                           field="temperature_interpolation_basis")
            ref = planes[0]
            for p in planes[1:]:
                if not (np.array_equal(p.id_axis_A, ref.id_axis_A) and np.array_equal(p.iq_axis_A, ref.iq_axis_A)):
                    raise InputValidationError("temperature interpolation requires identical axes on all planes",
                                               field="planes")
        object.__setattr__(self, "source_planes", planes)
        if self.symmetry == "q_odd":
            planes = tuple(_mirror_q(p) for p in planes)
        object.__setattr__(self, "planes", planes)

    def plane_for(self, magnet_temp_C: float | None) -> tuple[FluxMapPlane, str]:
        """Return the plane for a magnet temperature and a note on how it was chosen."""
        if magnet_temp_C is None:
            if len(self.planes) == 1:
                p = self.planes[0]
                t = "undeclared" if p.magnet_temp_C is None else f"{p.magnet_temp_C:g} degC"
                return p, f"scenario magnet temperature not stated; the single map plane ({t}) is used as supplied"
            raise OutsideModelDomain(
                "magnet temperature must be stated to select among flux-map planes",
                detail={"reason": "MISSING_INPUT", "planes_C": [p.magnet_temp_C for p in self.planes]},
            )
        for p in self.planes:
            if p.magnet_temp_C is not None and abs(p.magnet_temp_C - magnet_temp_C) <= self.temperature_match_tol_C:
                return p, f"flux-map plane at {p.magnet_temp_C:g} degC matches the scenario"
        temps = [p.magnet_temp_C for p in self.planes if p.magnet_temp_C is not None]
        if self.temperature_interpolation == "linear" and temps and temps[0] < magnet_temp_C < temps[-1]:
            k = int(np.searchsorted(temps, magnet_temp_C))
            lo, hi = self.planes[k - 1], self.planes[k]
            w = (magnet_temp_C - lo.magnet_temp_C) / (hi.magnet_temp_C - lo.magnet_temp_C)
            valid = lo.valid & hi.valid
            with np.errstate(invalid="ignore"):
                psd = np.where(valid, (1 - w) * lo.psi_d_Wb + w * hi.psi_d_Wb, np.nan)
                psq = np.where(valid, (1 - w) * lo.psi_q_Wb + w * hi.psi_q_Wb, np.nan)
            plane = FluxMapPlane(lo.id_axis_A, lo.iq_axis_A, psd, psq, valid, magnet_temp_C,
                                 f"linear interpolation {lo.magnet_temp_C:g}..{hi.magnet_temp_C:g} degC")
            return plane, (f"linear temperature interpolation between {lo.magnet_temp_C:g} and "
                           f"{hi.magnet_temp_C:g} degC (basis: {self.temperature_interpolation_basis})")
        raise OutsideModelDomain(
            f"no flux-map plane validated at {magnet_temp_C:g} degC and interpolation is not permitted",
            detail={"reason": "OUTSIDE_MODEL_DOMAIN", "planes_C": temps},
        )

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "planes": [p.coverage_summary() for p in self.planes],
            "interpolation": "bilinear inside valid cells; no extrapolation",
            "declared_symmetry": self.symmetry,
            "conservative_magnetic_model_declared": self.conservative,
            "temperature_interpolation": self.temperature_interpolation or "not permitted",
        }
