"""Power-module thermal cycling: rainflow counting and conditional damage (independent review, section 12).

temperature history -> turning points (plateaus kept with their dwell) -> ASTM E1049 rainflow (full and half
cycles with range, mean, min, max and timestamps) -> supplier cycling model -> damage per mechanism.

* Rainflow is a counting procedure, not a life model.  The cycle period is not silently used as the model's
  heating time t_on: t_on comes from a declared rule (``ton_rule``) approved for the supplier model.  The rule
  "rise_time" is the HEATING interval of each counted range - from the last departure from its lower level to the
  first arrival at its upper level (dwell excluded, interruptions by nested cycles included) - never the cooling
  leg; when the history does not contain that heating, t_on is not determined (never replaced by 1 s).
* A repeating (periodic) history must close; it is rotated to start at its maximum on a monotone unwrapped time
  axis.  A warm-up is not a periodic cycle.
* Damage needs the temperature history of ONE physical junction; a screening history (e.g. a generated chain
  without loss(Tj) feedback) gives screening damage only (UNKNOWN), and a hottest-device envelope is never a
  device history.
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
from ..validation import finite as _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

K_B_EV = 8.617333262e-5


def turning_points(t, x, tol: float = 0.0) -> list[tuple]:
    """Reversals as (value, t_first, t_last, i_first, i_last); a plateau at a reversal keeps its whole dwell."""
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
        runs.append((float(x[i]), float(t[i]), float(t[j]), i, j))
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
        c = {"range": abs(q[0] - p[0]), "mean": 0.5 * (p[0] + q[0]), "min": lo[0], "max": hi[0],
             "count": count, "t_start": p[1], "t_end": q[2], "t_min_dwell": lo[2] - lo[1],
             "t_max_dwell": hi[2] - hi[1], "rising": q[0] > p[0]}
        if len(p) > 3 and len(q) > 3:
            c["p_idx"], c["q_idx"] = (p[3], p[4]), (q[3], q[4])
        cycles.append(c)

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
        if not self.basis.strip() or not str(self.mechanism).strip():
            raise InputValidationError("a cycling model needs its mechanism and supplier basis (document, revision, "
                                       "package) - no default LESIT/CIPS coefficients", field="basis")
        if self.T_ref not in ("min", "mean", "max"):
            raise InputValidationError("T_ref must be min, mean or max", field="T_ref")
        if _finite("A", self.A) <= 0 or _finite("scatter_factor", self.scatter_factor) < 1.0:
            raise InputValidationError("A > 0 and scatter_factor >= 1 required", field="A")
        for name in ("a", "b_K", "c"):
            _finite(name, getattr(self, name))
        for name in ("dT_valid_K", "Tref_valid_C", "ton_valid_s"):
            try:
                lo, hi = (float(v) for v in getattr(self, name))
            except (TypeError, ValueError):
                raise InputValidationError(f"{name} is (low, high)", field=name) from None
            if math.isnan(lo) or math.isnan(hi) or lo > hi:
                raise InputValidationError(f"{name} needs low <= high", field=name)

    def nf(self, cyc: dict, ton_s: float | None) -> tuple[float | None, str]:
        dT = cyc["range"]
        tr = {"min": cyc["min"], "mean": cyc["mean"], "max": cyc["max"]}[self.T_ref]
        if not (self.dT_valid_K[0] <= dT <= self.dT_valid_K[1]):
            return None, f"dT {dT:.3g} K outside validity {list(self.dT_valid_K)}"
        if not (self.Tref_valid_C[0] <= tr <= self.Tref_valid_C[1]):
            return None, f"T_{self.T_ref} {tr:.4g} degC outside validity {list(self.Tref_valid_C)}"
        if tr + 273.15 <= 0:
            return None, f"T_{self.T_ref} {tr:.4g} degC is not a positive absolute temperature"
        f_ton = 1.0
        if self.c != 0.0:
            if ton_s is None:
                return None, ("t_on needed by the model but not determined (no approved t_on rule, or the history "
                              "does not contain this cycle's heating)")
            if not (math.isfinite(ton_s) and ton_s > 0):
                return None, f"t_on {ton_s:.3g} s is not a heating time (zero / invalid t_on is never replaced)"
            if not (self.ton_valid_s[0] <= ton_s <= self.ton_valid_s[1]):
                return None, f"t_on {ton_s:.3g} s outside validity {list(self.ton_valid_s)}"
            f_ton = ton_s ** self.c
        nf = self.A * dT ** self.a * math.exp(self.b_K / (tr + 273.15)) * f_ton
        if not (math.isfinite(nf) and nf > 0):
            return None, f"N_f = {nf:.3g} is not a finite positive number (law outside its numeric domain)"
        return nf, ""


def damage(cycles: list[dict], model: CyclingModel | None, ton_rule: str = "none", D_allow: float | None = None,
           cutoff_K: float = 0.0, repeats: float = 1.0, trace_kind: str = "declared", trace_note: str = "") -> dict:
    """Linear accumulation for ONE mechanism of ONE junction; uncovered cycles are reported, never set to zero damage.

    ``trace_kind`` "declared": the caller's history is the qualified temperature history of that junction;
    "screening": a screening history - the Miner sum is reported but the damage claim stays UNKNOWN."""
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
        ton = c.get("t_heating_s") if ton_rule == "rise_time" else None
        nf, why = model.nf(c, ton)
        if nf is None:
            uncovered.append({"range": c["range"], "count": c["count"], "why": why})
            continue
        d = repeats * c["count"] / nf
        D += d
        rows.append({"range": c["range"], "mean": c["mean"], "count": c["count"], "t_on_s": ton, "Nf": nf,
                     "damage": d})
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
    elif trace_kind != "declared":
        st, rs, det = Status.UNKNOWN, (Reason.SCREENING_ONLY,), (
            f"D = {D:.4g} from a screening temperature history ({trace_note or 'not a qualified junction history'}): "
            f"screening damage only")
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
    out["trace_kind"] = trace_kind
    out["claim"] = Claim("thermal_cycling_damage", st, q, "conditional: declared mission, supplier model, linear "
                         "accumulation", reasons=rs,
                         evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND, f"Miner sum over "
                                                 f"{out['cycles_counted']:g} counted cycles", basis=model.basis),),
                         qualifiers=("a model-specific exhaustion criterion, not a guaranteed product life",
                                     "sequence, creep and dwell effects beyond the model are not represented"),
                         detail=det).to_dict()
    return out


def foster_states(t_s, P_W, R_K_per_W, tau_s, T_ref_C: float, s0=None) -> tuple[np.ndarray, np.ndarray]:
    """Junction temperature for a piecewise-constant loss history (exact superposition of Foster branches) and the
    branch states at the end.  P_W[k] acts on [t_k, t_k+1]; ``s0`` is the initial branch state (default: zero)."""
    t = np.asarray(t_s, dtype=float)
    P = np.asarray(P_W, dtype=float)
    R = np.asarray(R_K_per_W, dtype=float)
    tau = np.asarray(tau_s, dtype=float)
    states = np.zeros(R.size) if s0 is None else np.array(s0, dtype=float)
    T = np.empty(t.size)
    T[0] = T_ref_C + states.sum()
    for k in range(1, t.size):
        a = np.exp(-(t[k] - t[k - 1]) / tau)
        states = states * a + P[k - 1] * R * (1.0 - a)
        T[k] = T_ref_C + states.sum()
    return T, states


def foster_trace(t_s, P_W, R_K_per_W, tau_s, T_ref_C: float) -> np.ndarray:
    """Junction temperature for a piecewise-constant loss history (exact superposition of Foster step responses)."""
    return foster_states(t_s, P_W, R_K_per_W, tau_s, T_ref_C)[0]


COOL_DOWN_TOL_K = 1e-4


def die_mission(durations_s, die_W: list[dict], networks: dict, T_coolant_C: float, dt_s: float,
                kind: str = "finite", repeat: int = 1) -> dict:
    """One junction temperature history per PHYSICAL die (independent review R2 PT-06).

    ``die_W[k]`` is {die: W} for segment k (the same dies in every segment); ``networks`` maps every die to its
    junction-to-coolant Foster network (R_K_per_W, tau_s); die-to-die thermal coupling is not represented.
    kind "finite": a cold start at the coolant temperature, the segments ``repeat`` times, then a cool-down until
    every die is back within 1e-4 K - a closed block with ONE start-up and shutdown.  kind "periodic": the segments
    x repeat form one period at its periodic steady state (closed-form Foster states) - warm-up not included."""
    if kind not in ("finite", "periodic"):
        raise InputValidationError("mission kind is 'finite' (cold start + cool-down) or 'periodic' (steady "
                                   "periodic state)", field="mission_kind")
    if not die_W or not durations_s or len(die_W) != len(durations_s):
        raise InputValidationError("one {die: W} per segment is needed", field="segments")
    dies = sorted(die_W[0])
    if any(sorted(d) != dies for d in die_W):
        raise InputValidationError("every segment needs the same physical dies", field="segments")
    missing = [d for d in dies if d not in networks]
    if missing:
        raise InputValidationError(f"no junction network for die(s) {missing}", field="junction_network")
    dt = _finite("dt_s", dt_s)
    if dt <= 0:
        raise InputValidationError("time step must be > 0", field="dt_s")
    seg_t, seg_P = [], {d: [] for d in dies}
    now = 0.0
    for _ in range(max(1, int(repeat))):
        for dur, dw in zip(durations_s, die_W):
            dur = _finite("duration_s", dur)
            if dur <= 0:
                raise InputValidationError("segment durations must be > 0", field="duration_s")
            n = max(2, int(round(dur / dt)))
            seg_t.append(now + np.arange(n) * (dur / n))
            for d in dies:
                seg_P[d].append(np.full(n, _finite(f"{d} loss", dw[d])))
            now += dur
    t = np.concatenate(seg_t + [np.array([now])])
    P = {d: np.concatenate(seg_P[d] + [np.array([0.0])]) for d in dies}
    period = now
    T = {}
    if kind == "periodic":
        for d in dies:
            R, tau = networks[d]
            _, b = foster_states(t, P[d], R, tau, 0.0)
            s0 = b / (1.0 - np.exp(-period / np.asarray(tau, dtype=float)))
            T[d] = foster_states(t, P[d], R, tau, T_coolant_C, s0)[0]
        basis = ("one period at its periodic steady state (closed-form Foster states); warm-up and shutdown are not "
                 "in this history")
    else:
        ends = {d: foster_states(t, P[d], *networks[d], 0.0)[1] for d in dies}
        mag = max(float(np.sum(np.abs(s))) for s in ends.values())
        tau_max = max(float(np.max(networks[d][1])) for d in dies)
        if mag > COOL_DOWN_TOL_K:
            t_cd = tau_max * math.log(mag / COOL_DOWN_TOL_K)
            n_cd = max(2, int(math.ceil(t_cd / dt)))
            t = np.concatenate([t, now + np.arange(1, n_cd + 1) * (t_cd / n_cd)])
            P = {d: np.concatenate([P[d], np.zeros(n_cd)]) for d in dies}
        for d in dies:
            T[d] = foster_states(t, P[d], *networks[d], T_coolant_C)[0]
        basis = ("cold start at the coolant temperature, the mission, then a cool-down back to it: a closed block "
                 "with one start-up and shutdown")
    closure = max(abs(float(T[d][-1] - T[d][0])) for d in dies)
    return {"t_s": t, "T_C": T, "P_W": P, "dies": dies, "kind": kind, "period_s": period, "closure_K": closure,
            "basis": basis}


def _unwrap_periodic(t: np.ndarray, x: np.ndarray, tol_K: float) -> tuple[np.ndarray, np.ndarray]:
    """One closed period rotated to start and end at its absolute maximum on a MONOTONE unwrapped time axis."""
    if abs(x[-1] - x[0]) > tol_K:
        raise InputValidationError(f"a repeating history must close: it starts at {x[0]:.6g} and ends at "
                                   f"{x[-1]:.6g} degC (a warm-up is not a periodic cycle - count it as a finite "
                                   f"history)", field="repeating_mission")
    P = t[-1] - t[0]
    k = int(np.argmax(x[:-1]))
    return np.concatenate([t[k:-1], t[:k + 1] + P]), np.concatenate([x[k:-1], x[:k + 1]])


def _heating_time(c: dict, t: np.ndarray, x: np.ndarray) -> float | None:
    """Heating interval of a counted range: from the last departure from its lower level to the first arrival at
    its upper level (dwell at the upper level excluded; interruptions by nested cycles included).  None when the
    history does not contain that heating."""
    if "p_idx" not in c:
        return None
    lo, hi = c["min"], c["max"]
    if c["rising"]:                                       # p (low) -> q (high): the arrival at q
        j_arr, t_arr = c["q_idx"][0], t[c["q_idx"][0]]
    elif c["count"] == 1.0:                               # p (high) -> q (low), closed by the next rise past p
        j = c["q_idx"][1]
        while j + 1 < x.size and x[j + 1] < hi:
            j += 1
        if j + 1 >= x.size:
            return None
        t_arr = t[j] + (hi - x[j]) / (x[j + 1] - x[j]) * (t[j + 1] - t[j])
        j_arr = j + 1
    else:                                                 # falling half cycle: the rise that reached p
        j_arr, t_arr = c["p_idx"][0], t[c["p_idx"][0]]
    j = j_arr - 1
    while j >= 0 and x[j] > lo:
        j -= 1
    if j < 0:
        return None
    t_dep = t[j] if x[j] == lo else t[j] + (lo - x[j]) / (x[j + 1] - x[j]) * (t[j + 1] - t[j])
    return float(t_arr - t_dep)


def cycle_analysis(t_s, T_C, model: CyclingModel | None = None, ton_rule: str = "none", D_allow: float | None = None,
                   cutoff_K: float = 0.0, repeats: float = 1.0, repeating_mission: bool = False,
                   source: str = "trace", trace_kind: str = "declared", trace_note: str = "",
                   closure_tol_K: float = 1e-3) -> dict:
    """End-to-end: history -> reversals -> rainflow -> histogram -> conditional damage.

    ``repeating_mission``: the history is ONE closed block of a repetition (it must close within ``closure_tol_K``);
    it is rotated to start at its maximum on a monotone unwrapped time axis and every range closes.  Otherwise the
    history is finite and its residue is counted as half cycles."""
    turning_points(t_s, T_C)                                    # validation of the raw history
    t = np.asarray(t_s, dtype=float)
    x = np.asarray(T_C, dtype=float)
    periodic = bool(repeating_mission)
    tt, xx = _unwrap_periodic(t, x, closure_tol_K) if periodic else (t, x)
    pts = turning_points(tt, xx)
    cyc = rainflow(pts, periodic=periodic and len(pts) > 2)
    for c in cyc:
        c["t_heating_s"] = _heating_time(c, tt, xx)
        c.pop("p_idx", None)
        c.pop("q_idx", None)
    return {"source": source, "reversals": len(pts), "cycles": cyc, "histogram": histogram(cyc),
            "max_range_K": max((c["range"] for c in cyc), default=0.0),
            "T_max_C": float(np.max(x)), "T_min_C": float(np.min(x)),
            "count_basis": ("closed repeating block: rotated to start at its maximum on a monotone unwrapped time "
                            "axis, every range closed" if periodic else
                            "finite history: the residue is counted as half cycles"),
            "damage": damage(cyc, model, ton_rule, D_allow, cutoff_K, repeats, trace_kind, trace_note),
            "notes": ["rainflow counts reversals of the given history; sampling below the thermal bandwidth hides "
                      "cycles", "a Tj history from a screening thermal model gives screening damage only"]}
