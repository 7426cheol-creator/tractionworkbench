"""Protection thresholds, derating and fault reaction on one causal trajectory (independent review, section 9).

The question is not "are warning < derating < fault ordered?" but: in normal operation no nuisance trip,
in a real fault the physical limit is not crossed after sensing, detection and actuation delays, and a
derating actually stops the variable from rising.  Everything is evaluated on the SAME physical trajectory:

    plant x(t)  ->  sensor (gain / offset, first-order filter)  ->  samples y(t_k) at phase phi + k*Ts
    ->  detector (comparator '>' or '>=', N consecutive samples, counter reset on a false sample)
    ->  execution delay  ->  action (fault reaction / derating) that changes the plant input
    ->  x(t) again, peak, first limit crossing, safe window

Reported instants (section 9.3): t_xcross (physical crossing of the true threshold), t_ycross (filtered
measurement crosses the nominal threshold), t_sample (first sample above), t_confirm (N-th consecutive sample),
t_act (action effective), t_limit (first physical limit crossing), peak and time of peak.

Plants with exact piecewise solutions:

* ``capacitor_energy``  - DC-link overvoltage: 1/2 C (V^2 - V0^2) = integral of the net injected power; the
  power stays at P0 until the action, then ramps to zero over t_ramp (E_after = P0 t_delay + P0 t_ramp / 2);
* ``thermal_1node``     - C_th dT/dt = P - (T - T_c)/R from an initial temperature (hot starts included);
  a derating changes P;
* ``ramp``              - x(t) = x0 + slope * t (+ optional ripple A sin 2 pi f t) until the action, frozen
  afterwards (detector / debounce studies; ripple exercises the counter reset).

Threshold window (section 9.4), for a monotone scalar and a memoryless sensor:

    no nuisance (sufficient):  theta_nom >  x_N,max + E+ + E_theta + reserve_normal
    protection (sufficient):   theta_nom <= x_lim  - E- - E_theta - dx_L - reserve_safety
    OV energy bound:           V_tr,max = sqrt(V_lim^2 - 2 (E_after + E_extra + E_reserve) / C_min)

These are *sufficient* conditions: an empty window means the declared bounds cannot guarantee both
requirements (UNKNOWN, BOUND_INCONCLUSIVE); only when the bounds are declared tight and jointly attainable
(``tight_attainable``) is an empty window a proof that threshold tuning alone cannot satisfy both (INFEASIBLE).
A radicand <= 0 in the OV bound is likewise UNKNOWN unless tight.  Sampled phases and scenarios are sampled
evidence, never a proof over a continuous fault domain.  Device-level fast OC / short-circuit survival, ringing
and SOA are outside this native model (external switched simulation / DPT / supplier data).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from ..errors import InputValidationError
from ..models.flux import _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status


def _nonneg(name, v):
    x = _finite(name, v)
    if x < 0:
        raise InputValidationError("must be >= 0", field=name)
    return x


def _pos(name, v):
    x = _finite(name, v)
    if x <= 0:
        raise InputValidationError("must be > 0", field=name)
    return x


# ---------------------------------------------------------------------------------------------------------
# closed-form bounds
# ---------------------------------------------------------------------------------------------------------

def threshold_window(x_normal_max: float, x_limit: float, E_plus: float = 0.0, E_minus: float = 0.0,
                     E_theta: float = 0.0, dx_after: float = 0.0, reserve_normal: float = 0.0,
                     reserve_safety: float = 0.0, tight_attainable: bool = False, upper_bound: float | None = None,
                     candidate: float | None = None) -> dict:
    """Conservative nominal-threshold window for an upper-limit protection (section 9.4).

    ``upper_bound`` replaces x_limit - dx_after by a stronger physics bound (e.g. the OV energy trigger bound
    V_tr,max) before the sensor/threshold tolerances are subtracted.
    """
    xn = _finite("x_normal_max", x_normal_max)
    xl = _finite("x_limit", x_limit)
    ep, em, et = (_nonneg("E_plus", E_plus), _nonneg("E_minus", E_minus), _nonneg("E_theta", E_theta))
    dx, rn, rs = _nonneg("dx_after", dx_after), _nonneg("reserve_normal", reserve_normal), \
        _nonneg("reserve_safety", reserve_safety)
    lo = xn + ep + et + rn
    base = (xl - dx) if upper_bound is None else _finite("upper_bound", upper_bound)
    hi = base - em - et - rs
    exists = lo < hi
    q = "a nominal threshold that avoids nuisance trips AND protects the limit"
    if exists:
        st, reasons = Status.FEASIBLE, ()
        detail = f"window ({lo:.6g}, {hi:.6g}]: width {hi - lo:.4g} (before model/measurement reserves)"
    elif tight_attainable:
        st, reasons = Status.INFEASIBLE, (Reason.CONSTRAINT_VIOLATION,)
        detail = (f"no threshold: nuisance bound {lo:.6g} >= protection bound {hi:.6g} and the bounds are declared "
                  f"tight and jointly attainable - threshold tuning alone cannot meet both; change sensing error, "
                  f"delay, stored energy, actuator authority or the operating envelope")
    else:
        st, reasons = Status.UNKNOWN, (Reason.BOUND_INCONCLUSIVE,)
        detail = (f"the conservative bounds do not leave a window (lower {lo:.6g} >= upper {hi:.6g}): satisfaction "
                  f"cannot be guaranteed with these bounds; this is not a proof that no threshold exists - refine "
                  f"the bounds (correlations, attainable trajectories) first")
    out = {"nuisance_lower_bound": lo, "protection_upper_bound": hi, "window_exists": exists,
           "window_width": hi - lo, "tight_attainable": bool(tight_attainable),
           "terms": {"x_normal_max": xn, "x_limit": xl, "E_plus": ep, "E_minus": em, "E_theta": et,
                     "dx_after": dx, "reserve_normal": rn, "reserve_safety": rs, "physics_upper_bound": upper_bound}}
    if candidate is not None:
        c = _finite("candidate", candidate)
        out["candidate"] = {"theta_nom": c, "no_nuisance_guaranteed": c > lo, "protection_guaranteed": c <= hi}
    out["claim"] = Claim("threshold_window", st, q, "sufficient-condition bounds (monotone scalar, memoryless sensor)",
                         reasons=reasons, evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND, detail),),
                         detail=detail).to_dict()
    return out


def ov_trigger_bound(C_min_F: float, V_lim_min_V: float, P0_W: float, t_delay_s: float, t_ramp_s: float = 0.0,
                     E_extra_J: float = 0.0, E_reserve_J: float = 0.0) -> dict:
    """Highest physical trigger voltage from which the declared post-trigger energy still fits below V_lim.

    E_after = P0 t_delay + P0 t_ramp / 2 (constant power until the reaction starts, then a linear ramp to zero);
    V_tr,max = sqrt(V_lim^2 - 2 (E_after + E_extra + E_reserve) / C_min).  E_extra is only the energy of paths
    NOT already contained in the integrated DC-port power (no double counting of magnetic/rotor energy).
    """
    C = _pos("C_min_F", C_min_F)
    Vl = _pos("V_lim_min_V", V_lim_min_V)
    P0 = _nonneg("P0_W", P0_W)
    td, tr = _nonneg("t_delay_s", t_delay_s), _nonneg("t_ramp_s", t_ramp_s)
    ex, er = _nonneg("E_extra_J", E_extra_J), _nonneg("E_reserve_J", E_reserve_J)
    e_after = P0 * td + 0.5 * P0 * tr
    rad = Vl * Vl - 2.0 * (e_after + ex + er) / C
    return {"E_after_J": e_after, "E_extra_J": ex, "E_reserve_J": er, "radicand_V2": rad,
            "V_trigger_max_V": math.sqrt(rad) if rad > 0 else None,
            "immediate_ramp_E_J": 0.5 * P0 * (td + tr),
            "note": ("radicand <= 0: no positive trigger voltage is guaranteed by this bound (UNKNOWN unless the "
                     "energy bound is tight and attainable)" if rad <= 0 else
                     "treating the whole delay as an immediate ramp would understate E_after (optimistic)")}


def ov_peak_after_trigger(C_F: float, V_trigger_V: float, E_after_J: float) -> float:
    """V_peak = sqrt(V_tr^2 + 2 E_after / C)."""
    return math.sqrt(_pos("V_trigger_V", V_trigger_V) ** 2 + 2.0 * _nonneg("E_after_J", E_after_J) / _pos("C_F", C_F))


# ---------------------------------------------------------------------------------------------------------
# causal event simulation
# ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Sensor:
    """Measured value y = (1 + gain_error) * filtered(x) + offset, sampled every period at a phase."""

    gain_error: float = 0.0
    offset: float = 0.0
    tau_filter_s: float = 0.0
    period_s: float = 1e-4
    phase_s: float = 0.0
    confirm_samples: int = 1
    exec_delay_s: float = 0.0
    comparator: str = ">="

    def __post_init__(self):
        _finite("gain_error", self.gain_error)
        _finite("offset", self.offset)
        _nonneg("tau_filter_s", self.tau_filter_s)
        _pos("period_s", self.period_s)
        ph = _nonneg("phase_s", self.phase_s)
        if ph >= self.period_s:
            raise InputValidationError("sample phase must lie in [0, period)", field="phase_s")
        if not isinstance(self.confirm_samples, int) or self.confirm_samples < 1:
            raise InputValidationError("confirm_samples must be an integer >= 1", field="confirm_samples")
        _nonneg("exec_delay_s", self.exec_delay_s)
        if self.comparator not in (">", ">="):
            raise InputValidationError("comparator must be '>' or '>='", field="comparator")


@dataclass(frozen=True)
class Plant:
    """Scalar plant with an exact piecewise solution; the action switches its input at t_act."""

    kind: str                          # capacitor_energy | thermal_1node | ramp
    x0: float                          # V (capacitor), degC (thermal), unit of x (ramp)
    params: tuple = ()                 # (("C_F", 5e-4), ("P0_W", 1e5), ("t_ramp_s", 0.0)) etc.

    def p(self, key, default=None):
        return dict(self.params).get(key, default)

    def __post_init__(self):
        if self.kind not in ("capacitor_energy", "thermal_1node", "ramp"):
            raise InputValidationError("plant kind must be capacitor_energy, thermal_1node or ramp", field="kind")
        _finite("x0", self.x0)
        if self.kind == "capacitor_energy":
            _pos("C_F", self.p("C_F"))
            _finite("P0_W", self.p("P0_W"))
            _nonneg("t_ramp_s", self.p("t_ramp_s", 0.0))
        elif self.kind == "thermal_1node":
            _pos("R_K_per_W", self.p("R_K_per_W"))
            _pos("C_J_per_K", self.p("C_J_per_K"))
            _finite("T_coolant_C", self.p("T_coolant_C"))
            _finite("P_W", self.p("P_W"))
            _finite("P_after_W", self.p("P_after_W", 0.0))
        else:
            _finite("slope_per_s", self.p("slope_per_s"))
            _nonneg("ripple_amp", self.p("ripple_amp", 0.0))
            _nonneg("ripple_hz", self.p("ripple_hz", 0.0))

    def x(self, t: np.ndarray, t_act: float) -> np.ndarray:
        """Physical trajectory for an action effective at t_act (math.inf = no action)."""
        t = np.asarray(t, dtype=float)
        if self.kind == "capacitor_energy":
            C, P0, tr = self.p("C_F"), self.p("P0_W"), self.p("t_ramp_s", 0.0)
            before = P0 * np.minimum(t, t_act)
            if math.isinf(t_act):
                after = 0.0
            else:
                u = np.clip(t - t_act, 0.0, None)
                if tr > 0:
                    uu = np.minimum(u, tr)
                    after = P0 * (uu - uu * uu / (2 * tr))
                else:
                    after = 0.0 * u
            E = before + after
            return np.sqrt(np.maximum(self.x0 ** 2 + 2.0 * E / C, 0.0))
        if self.kind == "thermal_1node":
            R, Cth, Tc = self.p("R_K_per_W"), self.p("C_J_per_K"), self.p("T_coolant_C")
            P, Pa = self.p("P_W"), self.p("P_after_W", 0.0)
            tau = R * Cth
            T1 = Tc + P * R + (self.x0 - Tc - P * R) * np.exp(-np.minimum(t, t_act) / tau)
            if math.isinf(t_act):
                return T1
            Ta = Tc + P * R + (self.x0 - Tc - P * R) * math.exp(-t_act / tau)
            T2 = Tc + Pa * R + (Ta - Tc - Pa * R) * np.exp(-np.clip(t - t_act, 0.0, None) / tau)
            return np.where(t <= t_act, T1, T2)
        slope = self.p("slope_per_s")
        a, f = self.p("ripple_amp", 0.0), self.p("ripple_hz", 0.0)
        te = np.minimum(t, t_act)
        return self.x0 + slope * te + (a * np.sin(2 * math.pi * f * te) if a > 0 and f > 0 else 0.0)


def _filter(t: np.ndarray, x: np.ndarray, tau: float, y0: float) -> np.ndarray:
    """First-order filter, exact for a piecewise-linear input between the grid points."""
    if tau <= 0:
        return x.copy()
    z = np.empty_like(x)
    z[0] = y0
    for k in range(1, t.size):
        dt = t[k] - t[k - 1]
        if dt <= 0:
            z[k] = z[k - 1]
            continue
        s = (x[k] - x[k - 1]) / dt
        a = math.exp(-dt / tau)
        z[k] = x[k] - s * tau + (z[k - 1] - x[k - 1] + s * tau) * a
    return z


def _first_crossing(t, v, level):
    idx = np.flatnonzero(v >= level)
    if idx.size == 0:
        return None
    k = int(idx[0])
    if k == 0:
        return float(t[0])
    v0, v1 = v[k - 1], v[k]
    return float(t[k - 1] + (level - v0) * (t[k] - t[k - 1]) / (v1 - v0)) if v1 != v0 else float(t[k])


def simulate(plant: Plant, sensor: Sensor, threshold: float, limit: float, horizon_s: float,
             action_delay_s: float = 0.0, threshold_error: float = 0.0, dt_s: float | None = None,
             release_threshold: float | None = None) -> dict:
    """One causal trace: detection on sampled measurements, action after the delays, physical consequence.

    ``threshold_error`` shifts the *actual* comparator threshold (theta_true = theta_nom + error).
    The detector counter resets on every false sample; the action becomes effective at
    t_confirm + exec_delay + action_delay.  The trajectory is re-evaluated with that action time.
    """
    H = _pos("horizon_s", horizon_s)
    th = _finite("threshold", threshold) + _finite("threshold_error", threshold_error)
    lim = float(limit)
    if math.isnan(lim):
        raise InputValidationError("limit must be a number (math.inf = no physical limit on this trace)", field="limit")
    ad = _nonneg("action_delay_s", action_delay_s)
    Ts = sensor.period_s
    dt = dt_s or min(Ts / 20.0, H / 4000.0, (sensor.tau_filter_s / 20.0) if sensor.tau_filter_s > 0 else math.inf)
    samples = np.arange(sensor.phase_s, H + 1e-15, Ts)
    grid = np.unique(np.concatenate([np.arange(0.0, H + dt / 2, dt), samples, [H]]))
    x_free = plant.x(grid, math.inf)
    y_free = (1.0 + sensor.gain_error) * _filter(grid, x_free, sensor.tau_filter_s, x_free[0]) + sensor.offset
    ys = np.interp(samples, grid, y_free)
    flags = ys > th if sensor.comparator == ">" else ys >= th
    count, t_first, t_confirm = 0, None, None
    for tk, f in zip(samples, flags):
        if f:
            count += 1
            if t_first is None:
                t_first = float(tk)
            if count >= sensor.confirm_samples:
                t_confirm = float(tk)
                break
        else:
            count = 0                   # a false sample resets the confirmation counter
            t_first = None
    t_act = math.inf if t_confirm is None else t_confirm + sensor.exec_delay_s + ad
    if not math.isinf(t_act):
        grid = np.unique(np.concatenate([grid, [t_act]]))
    x = plant.x(grid, t_act)
    y = (1.0 + sensor.gain_error) * _filter(grid, x, sensor.tau_filter_s, x[0]) + sensor.offset
    k_pk = int(np.argmax(x))
    t_limit = _first_crossing(grid, x, lim)
    t_xcross = _first_crossing(grid, x, th)
    t_ycross = _first_crossing(grid, y, th)
    rel = None
    if release_threshold is not None and t_confirm is not None:
        after = grid > t_act
        below = np.flatnonzero(after & (x < _finite("release_threshold", release_threshold)))
        rel = None if below.size == 0 else float(grid[below[0]])
    protected = t_limit is None
    return {
        "t_s": grid, "x": x, "y": y, "samples_t_s": samples, "samples_y": ys, "sample_flags": flags,
        "events": {"t_xcross_s": t_xcross, "t_ycross_s": t_ycross, "t_first_sample_s": t_first,
                   "t_confirm_s": t_confirm, "t_action_effective_s": None if math.isinf(t_act) else t_act,
                   "t_limit_s": t_limit, "t_release_s": rel,
                   "peak": float(x[k_pk]), "t_peak_s": float(grid[k_pk])},
        "threshold_true": th, "limit": lim, "protected": protected,
        "detected": t_confirm is not None,
    }


def phase_sweep(plant: Plant, sensor: Sensor, threshold: float, limit: float, horizon_s: float,
                action_delay_s: float = 0.0, threshold_error: float = 0.0, phases: int = 16) -> dict:
    """Sampled worst case over the sample phase (a sampled statement, not a proof over the continuum)."""
    rows = []
    for i in range(max(1, int(phases))):
        ph = sensor.period_s * i / max(1, int(phases))
        r = simulate(plant, replace(sensor, phase_s=ph), threshold, limit, horizon_s, action_delay_s, threshold_error)
        ev = r["events"]
        rows.append({"phase_s": ph, "t_confirm_s": ev["t_confirm_s"], "peak": ev["peak"],
                     "t_limit_s": ev["t_limit_s"], "protected": r["protected"]})
    worst = max(rows, key=lambda r: r["peak"])
    late = [r["t_confirm_s"] for r in rows if r["t_confirm_s"] is not None]
    return {"rows": rows, "worst_peak": worst["peak"], "worst_phase_s": worst["phase_s"],
            "latest_confirm_s": max(late) if late else None, "earliest_confirm_s": min(late) if late else None,
            "all_protected": all(r["protected"] for r in rows), "phases": len(rows)}


def n_sample_confirmation_bound(period_s: float, n: int) -> float:
    """Worst waiting from a persistent crossing to the N-th consecutive sample (no jitter): N * Ts."""
    return _pos("period_s", period_s) * int(n)


# ---------------------------------------------------------------------------------------------------------
# requirement review table (PROT-01 .. PROT-09)
# ---------------------------------------------------------------------------------------------------------

def _row(pid, item, status, detail, evidence=""):
    return {"id": pid, "item": item, "status": status.value if isinstance(status, Status) else status,
            "detail": detail, "evidence": evidence}


def protection_review(plant: Plant, sensor: Sensor, fault_threshold: float, limit: float, horizon_s: float,
                      action_delay_s: float = 0.0, E_theta: float = 0.0, normal_plants: tuple = (),
                      warning_threshold: float | None = None, warning_needed_s: float | None = None,
                      release_threshold: float | None = None, x_normal_max: float | None = None,
                      tight_attainable: bool = False, hw_path: str | None = None,
                      upper_bound: float | None = None, dx_after: float = 0.0, phases: int = 16) -> dict:
    """Review of one threshold set on causal trajectories; every row states its evidence level.

    Sensor error extremes are applied as separate traces: the protection trace reads LOW (under-read:
    -|gain|, -|offset|, threshold +E_theta), the nuisance trace reads HIGH (over-read, threshold -E_theta).
    """
    rows = []
    g, o = abs(sensor.gain_error), abs(sensor.offset)
    under = replace(sensor, gain_error=-g, offset=-o)
    over = replace(sensor, gain_error=+g, offset=+o)
    # PROT-01 no nuisance on every declared normal trajectory (over-reading sensor, low threshold)
    if normal_plants:
        trips = []
        n_ph = max(1, int(phases))
        for i, npl in enumerate(normal_plants):
            nsw = phase_sweep(npl, over, fault_threshold, math.inf, horizon_s, action_delay_s, -E_theta, n_ph)
            hit = [r for r in nsw["rows"] if r["t_confirm_s"] is not None]
            if hit:
                trips.append((i, hit[0]["t_confirm_s"], hit[0]["phase_s"]))
        if trips:
            rows.append(_row("PROT-01", "no nuisance trip", Status.INFEASIBLE,
                             f"normal trajectory #{trips[0][0]} confirms a fault at {trips[0][1]:.4g} s "
                             f"(sample phase {trips[0][2]:.4g} s, over-reading sensor, threshold at its low "
                             f"tolerance): a valid counterexample", "causal simulation witness"))
        else:
            rows.append(_row("PROT-01", "no nuisance trip", Status.FEASIBLE,
                             f"{len(normal_plants)} declared normal trajectories never confirm in {n_ph} sampled "
                             f"phases (coverage limited to the declared set and sampled phases)",
                             "causal simulation, declared set only"))
    else:
        rows.append(_row("PROT-01", "no nuisance trip", Status.UNKNOWN,
                         "no normal operating/transient trajectory declared: nuisance behaviour not covered"))
    # PROT-04 fault protection over sample phases (under-reading sensor, high threshold)
    sw = phase_sweep(plant, under, fault_threshold, limit, horizon_s, action_delay_s, +E_theta, phases)
    base = simulate(plant, replace(under, phase_s=sw["worst_phase_s"]), fault_threshold, limit, horizon_s,
                    action_delay_s, +E_theta, release_threshold=release_threshold)
    if not all(r["t_confirm_s"] is not None for r in sw["rows"]):
        rows.append(_row("PROT-04", "fault protection before the physical limit", Status.INFEASIBLE,
                         "the fault is not confirmed in some sample phase within the horizon (filtered/sampled "
                         "measurement never satisfies the detector)", "causal simulation witness"))
    elif sw["all_protected"]:
        rows.append(_row("PROT-04", "fault protection before the physical limit", Status.FEASIBLE,
                         f"peak {sw['worst_peak']:.6g} < limit {limit:.6g} in all {sw['phases']} sampled phases "
                         f"(latest confirmation {sw['latest_confirm_s']:.4g} s); sampled phases, not a proof over the "
                         f"continuous fault domain", "causal simulation, sampled phases"))
    else:
        rows.append(_row("PROT-04", "fault protection before the physical limit", Status.INFEASIBLE,
                         f"worst phase peak {sw['worst_peak']:.6g} >= limit {limit:.6g} (confirmation alone is not "
                         f"protection)", "causal simulation witness"))
    # PROT-02 warning usefulness
    if warning_threshold is not None and warning_needed_s is not None:
        wr = simulate(plant, under, warning_threshold, limit, horizon_s, 0.0, +E_theta)
        tw = wr["events"]["t_confirm_s"]
        tl = base["events"]["t_limit_s"]
        t_fault = base["events"]["t_confirm_s"]
        ref = t_fault if t_fault is not None else tl
        if tw is None:
            rows.append(_row("PROT-02", "warning usefulness", Status.INFEASIBLE, "warning never confirmed"))
        elif ref is None:
            rows.append(_row("PROT-02", "warning usefulness", Status.FEASIBLE,
                             f"warning at {tw:.4g} s; neither fault nor limit reached in the horizon"))
        else:
            avail = ref - tw
            st = Status.FEASIBLE if avail >= warning_needed_s else Status.INFEASIBLE
            rows.append(_row("PROT-02", "warning usefulness", st,
                             f"warning confirmed {avail * 1e3:.4g} ms before the fault reaction (needed "
                             f"{warning_needed_s * 1e3:.4g} ms)"))
    else:
        rows.append(_row("PROT-02", "warning usefulness", Status.UNKNOWN, "no warning threshold / required "
                         "intervention time declared"))
    # PROT-03 derating effectiveness (does the action reverse the rise before the limit?)
    ev = base["events"]
    if ev["t_action_effective_s"] is None:
        rows.append(_row("PROT-03", "derating / reaction effectiveness", Status.UNKNOWN, "no action within the horizon"))
    else:
        t, x = base["t_s"], base["x"]
        after = t >= ev["t_action_effective_s"]
        dxdt = np.gradient(x[after], t[after]) if after.sum() > 2 else np.array([0.0])
        reversed_ = bool(np.any(dxdt < 0)) or float(np.max(dxdt)) <= 0
        ok = reversed_ and base["protected"]
        rows.append(_row("PROT-03", "derating / reaction effectiveness", Status.FEASIBLE if ok else Status.INFEASIBLE,
                         ("the variable stops rising after the action at "
                          f"{ev['t_action_effective_s']:.4g} s and stays below the limit" if ok else
                          "after the action the variable keeps rising or crosses the limit (an earlier warning is "
                          "not evidence that the derating works)"), "causal simulation"))
    # PROT-05 threshold feasibility (conservative window)
    if x_normal_max is not None:
        win = threshold_window(x_normal_max, limit, E_plus=o + g * abs(x_normal_max), E_minus=o + g * abs(limit),
                               E_theta=E_theta, dx_after=dx_after, tight_attainable=tight_attainable,
                               upper_bound=upper_bound, candidate=fault_threshold)
        rows.append(_row("PROT-05", "threshold feasibility window", win["claim"]["status"], win["claim"]["detail"],
                         "sufficient-condition bounds"))
    else:
        win = None
        rows.append(_row("PROT-05", "threshold feasibility window", Status.UNKNOWN,
                         "normal maximum not declared: the nuisance side of the window is open"))
    # PROT-06 recovery / chatter
    if release_threshold is not None:
        if release_threshold >= fault_threshold:
            rows.append(_row("PROT-06", "recovery (hysteresis, re-trigger)", Status.INFEASIBLE,
                             "release threshold is not below the assert threshold: no hysteresis"))
        elif ev["t_release_s"] is None:
            rows.append(_row("PROT-06", "recovery (hysteresis, re-trigger)", Status.UNKNOWN,
                             "the variable does not fall below the release threshold in the horizon"))
        else:
            rows.append(_row("PROT-06", "recovery (hysteresis, re-trigger)", Status.UNKNOWN,
                             f"release condition met at {ev['t_release_s']:.4g} s; restoring authority needs the "
                             f"declared dwell, latch and restart rules (not modelled natively)"))
    else:
        rows.append(_row("PROT-06", "recovery (hysteresis, re-trigger)", Status.UNKNOWN, "no release threshold declared"))
    rows.append(_row("PROT-07", "independent HW path", Status.UNKNOWN,
                     f"declared architecture: {hw_path or 'not declared'} - conditional on the declaration; not "
                     f"functional-safety evidence"))
    rows.append(_row("PROT-08", "combined faults / priority", Status.UNKNOWN,
                     "needs the project's priority table and viable reaction paths per fault combination"))
    rows.append(_row("PROT-09", "model sufficiency", Status.UNKNOWN,
                     "scalar plant with exact piecewise solution; device ringing, SOA, fast OC/SC and sensor "
                     "saturation are outside this model (switched simulation / DPT / HIL needed)"))
    order = {"FEASIBLE": 0, "UNKNOWN": 1, "INFEASIBLE": 2}
    worst = max((r["status"] for r in rows[:6]), key=lambda s: order[s])
    return {"rows": rows, "trace": base, "phase_sweep": sw, "window": win,
            "summary_status": worst,
            "note": "rows 01-06 are computed on causal trajectories / bounds; 07-09 state what this native model "
                    "cannot establish"}
