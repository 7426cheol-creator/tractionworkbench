"""Power-module thermal cycling: rainflow counting and conditional damage (independent review, section 12).

temperature history -> turning points (plateaus kept with their dwell) -> ASTM E1049 rainflow (full and half
cycles with range, mean, min, max and timestamps) -> supplier cycling model -> damage per mechanism.

* Rainflow is a counting procedure, not a life model.  The cycle period is not silently used as the model's
  heating time t_on: t_on comes from a declared rule (``ton_rule``) approved for the supplier model.
* Damage D = sum n_i / N_f,i (linear accumulation) only with a declared supplier model of the exact package and
  failure mechanism, with its validity ranges (Delta T, reference temperature, t_on), failure quantile and
  scatter.  Cycles outside the validity are *uncovered* - reported, never given N_f = infinity.
* No LESIT / CIPS default coefficients: the law is always user/supplier supplied.  Without a model the result is
  the cycle histogram only (damage UNKNOWN).
* D = 1 is the exhaustion criterion of that model, not "every part fails".  Conditional verdicts use the
  declared scatter: D_L > D_allow -> INFEASIBLE, D_U <= D_allow -> FEASIBLE, overlap -> UNKNOWN.
* Power cycling (junction) and thermal cycling (case/substrate) are different mechanisms; separate models are
  never summed into one D.  Small-cycle cut-offs are optional and their excluded share is reported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..errors import InputValidationError
from ..models.flux import _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

K_B_EV = 8.617333262e-5


def turning_points(t, x, tol: float = 0.0) -> list[tuple[float, float, float]]:
    """Reversals as (value, t_first, t_last); a plateau at a reversal keeps its whole dwell interval."""
    t = np.asarray(t, dtype=float)
    x = np.asarray(x, dtype=float)
    if t.size != x.size or t.size < 2:
        raise InputValidationError("time and temperature need the same length >= 2", field="trace")
    if not (np.all(np.isfinite(t)) and np.all(np.isfinite(x))):
        raise InputValidationError("NaN / non-finite values in the history", field="trace")
    if np.any(np.diff(t) <= 0):
        raise InputValidationError("timestamps must be strictly increasing (no gaps silently merged)", field="t")
    # merge equal consecutive values into plateaus
    runs = []
    i = 0
    while i < x.size:
        j = i
        while j + 1 < x.size and abs(x[j + 1] - x[i]) <= tol:
            j += 1
        runs.append((float(x[i]), float(t[i]), float(t[j])))
        i = j + 1
    if len(runs) <= 2:
        return runs
    out = [runs[0]]
    for k in range(1, len(runs) - 1):
        a, b, c = runs[k - 1][0], runs[k][0], runs[k + 1][0]
        if (b - a) * (c - b) < 0:
            out.append(runs[k])
    out.append(runs[-1])
    return out


def rainflow(points: list[tuple[float, float, float]], periodic: bool = False) -> list[dict]:
    """ASTM E1049-85 (2023) rainflow counting (5.4.4) on reversals; half cycles for the residue.

    ``periodic=True`` expects a repeated history already arranged to start and close at its absolute maximum:
    every range then closes into a full cycle (the starting-point half-cycle rule does not apply).
    """
    stack: list[tuple[float, float, float]] = []
    cycles = []

    def rec(p, q, count):
        lo, hi = (p, q) if p[0] <= q[0] else (q, p)
        cycles.append({"range": abs(q[0] - p[0]), "mean": 0.5 * (p[0] + q[0]), "min": lo[0], "max": hi[0],
                       "count": count, "t_start": p[1], "t_end": q[2], "t_min_dwell": lo[2] - lo[1],
                       "t_max_dwell": hi[2] - hi[1], "rising": q[0] > p[0]})

    for pt in points:
        stack.append(pt)
        while len(stack) >= 3:
            X = abs(stack[-1][0] - stack[-2][0])
            Y = abs(stack[-2][0] - stack[-3][0])
            if X < Y:
                break
            if len(stack) == 3 and not periodic:
                rec(stack[0], stack[1], 0.5)
                stack.pop(0)
            else:
                rec(stack[-3], stack[-2], 1.0)
                last = stack[-1]
                del stack[-3:]
                stack.append(last)
    for a, b in zip(stack, stack[1:]):
        rec(a, b, 0.5)
    return cycles


def histogram(cycles: list[dict], bin_K: float = 5.0) -> list[dict]:
    if not cycles:
        return []
    top = max(c["range"] for c in cycles)
    edges = np.arange(0.0, top + bin_K, bin_K)
    rows = []
    for lo, hi in zip(edges, edges[1:]):
        sel = [c for c in cycles if lo <= c["range"] < hi or (hi >= top and c["range"] == top and lo <= top)]
        if sel:
            rows.append({"range_K": [float(lo), float(hi)], "cycles": sum(c["count"] for c in sel),
                         "mean_C_avg": float(np.mean([c["mean"] for c in sel]))})
    return rows


@dataclass(frozen=True)
class CyclingModel:
    """N_f = A * dT^a * exp(b / T_ref_K) * (t_on_s)^c, declared by the supplier for one package + mechanism."""

    mechanism: str                 # e.g. "bond-wire lift-off (power cycling, junction)"
    A: float
    a: float
    b_K: float                     # activation term Ea/k_B expressed in K
    c: float = 0.0
    T_ref: str = "min"             # which cycle temperature the law uses: min | mean | max
    dT_valid_K: tuple = (0.0, math.inf)
    Tref_valid_C: tuple = (-math.inf, math.inf)
    ton_valid_s: tuple = (0.0, math.inf)
    quantile: str = ""             # e.g. "B10", "15 % failure probability"
    scatter_factor: float = 1.0    # N_f known within x / scatter_factor (declared)
    basis: str = ""                # supplier document / revision

    def __post_init__(self):
        if not self.basis.strip():
            raise InputValidationError("a cycling model needs its supplier basis (document, revision, package, "
                                       "mechanism) - no default LESIT/CIPS coefficients", field="basis")
        if self.T_ref not in ("min", "mean", "max"):
            raise InputValidationError("T_ref must be min, mean or max", field="T_ref")
        if _finite("A", self.A) <= 0 or _finite("scatter_factor", self.scatter_factor) < 1.0:
            raise InputValidationError("A > 0 and scatter_factor >= 1 required", field="A")

    def nf(self, cyc: dict, ton_s: float | None) -> tuple[float | None, str]:
        dT = cyc["range"]
        tr = {"min": cyc["min"], "mean": cyc["mean"], "max": cyc["max"]}[self.T_ref]
        if not (self.dT_valid_K[0] <= dT <= self.dT_valid_K[1]):
            return None, f"dT {dT:.3g} K outside validity {list(self.dT_valid_K)}"
        if not (self.Tref_valid_C[0] <= tr <= self.Tref_valid_C[1]):
            return None, f"T_{self.T_ref} {tr:.4g} degC outside validity {list(self.Tref_valid_C)}"
        if self.c != 0.0:
            if ton_s is None:
                return None, "t_on needed by the model but no approved t_on rule declared"
            if not (self.ton_valid_s[0] <= ton_s <= self.ton_valid_s[1]):
                return None, f"t_on {ton_s:.3g} s outside validity {list(self.ton_valid_s)}"
        return (self.A * dT ** self.a * math.exp(self.b_K / (tr + 273.15)) * ((ton_s or 1.0) ** self.c)), ""


def damage(cycles: list[dict], model: CyclingModel | None, ton_rule: str = "none", D_allow: float | None = None,
           cutoff_K: float = 0.0, repeats: float = 1.0) -> dict:
    """Linear accumulation for ONE mechanism; uncovered cycles are reported, never set to zero damage."""
    kept = [c for c in cycles if c["range"] >= cutoff_K]
    excluded = [c for c in cycles if c["range"] < cutoff_K]
    out = {"cycles_counted": sum(c["count"] for c in kept),
           "excluded_small_cycles": {"count": sum(c["count"] for c in excluded), "cutoff_K": cutoff_K},
           "repeats": repeats}
    if model is None:
        out["claim"] = Claim("thermal_cycling_damage", Status.UNKNOWN, "thermal-fatigue damage",
                             "cycle counting only", reasons=(Reason.MISSING_INPUT,),
                             detail="no supplier cycling model for this package/mechanism: cycle histogram, hotspot "
                                    "and dT/mean-temperature comparison only").to_dict()
        return out
    if ton_rule not in ("none", "rise_time"):
        raise InputValidationError("ton_rule must be 'none' or 'rise_time' (a declared, supplier-approved rule)",
                                   field="ton_rule")
    D = 0.0
    uncovered = []
    rows = []
    for c in kept:
        ton = None
        if ton_rule == "rise_time":
            ton = max(c["t_end"] - c["t_start"], 0.0)
        nf, why = model.nf(c, ton)
        if nf is None:
            uncovered.append({"range": c["range"], "count": c["count"], "why": why})
            continue
        d = repeats * c["count"] / nf
        D += d
        rows.append({"range": c["range"], "mean": c["mean"], "count": c["count"], "Nf": nf, "damage": d})
    rows.sort(key=lambda r: -r["damage"])
    s = model.scatter_factor
    out.update({"mechanism": model.mechanism, "D": D, "D_lower": D / s, "D_upper": D * s,
                "dominant_cycles": rows[:10], "uncovered_cycles": uncovered, "model_basis": model.basis,
                "quantile": model.quantile})
    q = f"{model.mechanism}: damage over {repeats:g} mission repetition(s)"
    if uncovered:
        st, rs, det = Status.UNKNOWN, (Reason.OUTSIDE_MODEL_DOMAIN,), (
            f"{len(uncovered)} cycle group(s) outside the model validity: damage not established (never extrapolated "
            f"or set to zero)")
    elif D_allow is None:
        st, rs, det = Status.UNKNOWN, (Reason.REQUIREMENT_INCOMPLETE,), f"D = {D:.4g} (no allowed damage stated)"
    elif D / s > D_allow:
        st, rs, det = Status.INFEASIBLE, (Reason.CONSTRAINT_VIOLATION,), (
            f"lower bound D_L = {D / s:.4g} > D_allow {D_allow:g}")
    elif D * s <= D_allow:
        st, rs, det = Status.FEASIBLE, (), f"upper bound D_U = {D * s:.4g} <= D_allow {D_allow:g}"
    else:
        st, rs, det = Status.UNKNOWN, (Reason.UNCERTAINTY_OVERLAP,), (
            f"[D_L, D_U] = [{D / s:.4g}, {D * s:.4g}] overlaps D_allow {D_allow:g}")
    out["claim"] = Claim("thermal_cycling_damage", st, q, "conditional: declared mission, supplier model, linear "
                         "accumulation", reasons=rs,
                         evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND, f"Miner sum over "
                                                 f"{out['cycles_counted']:g} counted cycles", basis=model.basis),),
                         qualifiers=("a model-specific exhaustion criterion, not a guaranteed product life",
                                     "sequence, creep and dwell effects beyond the model are not represented"),
                         detail=det).to_dict()
    return out


def foster_trace(t_s, P_W, R_K_per_W, tau_s, T_ref_C: float) -> np.ndarray:
    """Junction temperature for a piecewise-constant loss history (exact superposition of Foster step responses)."""
    t = np.asarray(t_s, dtype=float)
    P = np.asarray(P_W, dtype=float)
    T = np.full(t.size, float(T_ref_C))
    states = np.zeros(len(R_K_per_W))
    T[0] = T_ref_C
    for k in range(1, t.size):
        dt = t[k] - t[k - 1]
        p = P[k - 1]
        for n, (r, tau) in enumerate(zip(R_K_per_W, tau_s)):
            a = math.exp(-dt / tau)
            states[n] = states[n] * a + p * r * (1.0 - a)
        T[k] = T_ref_C + states.sum()
    return T


def cycle_analysis(t_s, T_C, model: CyclingModel | None = None, ton_rule: str = "none", D_allow: float | None = None,
                   cutoff_K: float = 0.0, repeats: float = 1.0, repeating_mission: bool = False,
                   source: str = "trace") -> dict:
    """End-to-end: history -> reversals -> rainflow -> histogram -> conditional damage."""
    pts = turning_points(t_s, T_C)
    if repeating_mission and len(pts) > 2:
        # periodic repetition: start and close the history at the absolute maximum so that no residue is left
        k = int(np.argmax([p[0] for p in pts]))
        seq = pts[k:] + pts[:k] + [pts[k]]
        red = [seq[0]]
        for q in seq[1:]:
            if q[0] == red[-1][0]:
                continue
            if len(red) >= 2 and (red[-1][0] - red[-2][0]) * (q[0] - red[-1][0]) > 0:
                red[-1] = q                     # same direction: not a reversal at the seam
            else:
                red.append(q)
        pts = red
    cyc = rainflow(pts, periodic=bool(repeating_mission and len(pts) > 2))
    return {"source": source, "reversals": len(pts), "cycles": cyc, "histogram": histogram(cyc),
            "max_range_K": max((c["range"] for c in cyc), default=0.0),
            "T_max_C": float(np.max(T_C)), "T_min_C": float(np.min(T_C)),
            "damage": damage(cyc, model, ton_rule, D_allow, cutoff_K, repeats),
            "notes": ["rainflow counts reversals of the given history; sampling below the thermal bandwidth hides "
                      "cycles", "a Tj history from a screening thermal model gives screening damage only"]}
