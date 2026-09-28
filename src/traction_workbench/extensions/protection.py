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
from ..validation import finite as _finite
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
                     reserve_safety: float = 0.0, tight_attainable: bool = False, upper_bound=None,
                     candidate: float | None = None, sensor_memoryless: bool = True) -> dict:
    """Conservative nominal-threshold window for an upper-limit protection (section 9.4; review R2 PD-01).

    ``upper_bound`` is the protection-side physics bound on the MEASURED trigger value, with its validity:
    None = no dynamic bound requested (the generic memoryless bound x_limit - dx_after, valid only for a memoryless
    sensor); a number = a valid bound; a dict {"status": "valid" | "no_bound" | "not_applicable" | "invalid",
    "value": ..., "reason": ..., "assumptions": [...]}.  A missing or invalid bound is never replaced by the generic
    one: the protection side is then not guaranteed (UNKNOWN).
    """
    xn = _finite("x_normal_max", x_normal_max)
    xl = _finite("x_limit", x_limit)
    ep, em, et = (_nonneg("E_plus", E_plus), _nonneg("E_minus", E_minus), _nonneg("E_theta", E_theta))
    dx, rn, rs = _nonneg("dx_after", dx_after), _nonneg("reserve_normal", reserve_normal), \
        _nonneg("reserve_safety", reserve_safety)
    lo = xn + ep + et + rn
    if upper_bound is None:
        bound = ({"status": "valid", "value": xl - dx, "assumptions": ["memoryless sensor, monotone scalar"]}
                 if sensor_memoryless else
                 {"status": "not_applicable", "reason": "the sensor filters: the memoryless bound x_limit - dx_after "
                                                      "does not hold and no dynamic bound was supplied"})
    elif isinstance(upper_bound, dict):
        bound = dict(upper_bound)
        if bound.get("status") not in ("valid", "no_bound", "not_applicable", "invalid"):
            raise InputValidationError("bound status must be valid, no_bound, not_applicable or invalid",
                                       field="upper_bound")
    else:
        bound = {"status": "valid", "value": _finite("upper_bound", upper_bound), "assumptions": ["declared bound"]}
    valid = bound["status"] == "valid"
    hi = (_finite("upper_bound", bound["value"]) - em - et - rs) if valid else None
    exists = valid and lo < hi
    q = "a nominal threshold that avoids nuisance trips AND protects the limit"
    if not valid:
        st, reasons = Status.UNKNOWN, (Reason.BOUND_INCONCLUSIVE,)
        detail = (f"no valid protection-side bound ({bound['status']}: {bound.get('reason', '')}): no threshold is "
                  f"guaranteed to protect the limit - and a missing bound is not replaced by the static one")
    elif exists:
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
           "window_width": None if hi is None else hi - lo, "tight_attainable": bool(tight_attainable),
           "protection_bound": bound,
           "terms": {"x_normal_max": xn, "x_limit": xl, "E_plus": ep, "E_minus": em, "E_theta": et,
                     "dx_after": dx, "reserve_normal": rn, "reserve_safety": rs,
                     "physics_upper_bound": bound.get("value") if valid else None}}
    if candidate is not None:
        c = _finite("candidate", candidate)
        out["candidate"] = {"theta_nom": c, "no_nuisance_guaranteed": c > lo,
                            "protection_guaranteed": bool(valid and c <= hi)}
    out["claim"] = Claim("threshold_window", st, q, "sufficient-condition bounds (monotone scalar; sensor dynamics in "
                         "the bound's assumptions)", reasons=reasons,
                         evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND, detail),), detail=detail).to_dict()
    return out


def ov_trigger_bound(C_min_F: float, V_lim_min_V: float, P0_W: float, t_delay_s: float, t_ramp_s: float = 0.0,
                     E_extra_J: float = 0.0, E_reserve_J: float = 0.0, tau_filter_s: float = 0.0,
                     V_start_min_V: float | None = None) -> dict:
    """Highest trigger voltage from which the declared post-trigger energy still fits below V_lim (review R2 PD-01).

    E_after = P0 t_delay + P0 t_ramp / 2 (constant power until the reaction starts, then a linear ramp to zero);
    V_tr,max = sqrt(V_lim^2 - 2 (E_after + E_extra + E_reserve) / C_min).  E_extra is only the energy of paths
    NOT already contained in the integrated DC-port power.  A first-order sensor filter is not a pure delay: with
    the filter settled at the fault start, its lag is at most (max slope) x tau, and the max slope of
    V = sqrt(V0^2 + 2 P0 t / C) is P0 / (C V_start); the MEASURED trigger bound is V_tr,max - lag.  The result
    carries its validity: a radicand <= 0 or an unknown start voltage with a filter is "no_bound" (UNKNOWN unless
    the energy bound is tight and attainable) - never a static fallback.
    """
    C = _pos("C_min_F", C_min_F)
    Vl = _pos("V_lim_min_V", V_lim_min_V)
    P0 = _nonneg("P0_W", P0_W)
    td, tr = _nonneg("t_delay_s", t_delay_s), _nonneg("t_ramp_s", t_ramp_s)
    ex, er = _nonneg("E_extra_J", E_extra_J), _nonneg("E_reserve_J", E_reserve_J)
    tau = _nonneg("tau_filter_s", tau_filter_s)
    e_after = P0 * td + 0.5 * P0 * tr
    rad = Vl * Vl - 2.0 * (e_after + ex + er) / C
    out = {"E_after_J": e_after, "E_extra_J": ex, "E_reserve_J": er, "radicand_V2": rad,
           "V_trigger_max_V": math.sqrt(rad) if rad > 0 else None, "immediate_ramp_E_J": 0.5 * P0 * (td + tr),
           "tau_filter_s": tau, "filter_lag_bound_V": 0.0}
    assumptions = ["constant pre-reaction power P0 until the reaction, then the declared linear ramp",
                   "confirmation within the declared samples after the measured crossing (monotone rise)"]
    if rad <= 0:
        out.update(status="no_bound", value=None,
                   reason="radicand <= 0: the declared post-trigger energy alone exceeds the capacitor margin, no "
                          "positive trigger voltage is guaranteed by this bound")
        return out
    lag = 0.0
    if tau > 0:
        if V_start_min_V is None or _finite("V_start_min_V", V_start_min_V) <= 0:
            out.update(status="no_bound", value=None,
                       reason="filtered sensor: the lag bound needs the lowest voltage of the pre-detection rise")
            return out
        lag = P0 / (C * float(V_start_min_V)) * tau
        assumptions.append(f"first-order filter settled at the fault start: lag <= P0 / (C V_start) x tau = "
                           f"{lag:.4g} V")
    out.update(status="valid", value=math.sqrt(rad) - lag, filter_lag_bound_V=lag, assumptions=assumptions,
               note="treating the whole delay as an immediate ramp would understate E_after (optimistic)")
    return out


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
    initial_state: float | None = None     # filter state at t = 0 (None: settled at the plant's initial value)

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
        if self.initial_state is not None:
            _finite("initial_state", self.initial_state)


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


def _detect(samples_t, samples_y, threshold: float, comparator: str, n: int):
    """First confirmation (n consecutive samples; a false sample resets the counter) and the first sample."""
    flags = samples_y > threshold if comparator == ">" else samples_y >= threshold
    count, t_first = 0, None
    for tk, f in zip(samples_t, flags):
        if f:
            count += 1
            if t_first is None:
                t_first = float(tk)
            if count >= n:
                return float(tk), t_first, flags
        else:
            count, t_first = 0, None
    return None, None, flags


def simulate(plant: Plant, sensor: Sensor, threshold: float, limit: float, horizon_s: float,
             action_delay_s: float = 0.0, threshold_error: float = 0.0, dt_s: float | None = None,
             release_threshold: float | None = None) -> dict:
    """One causal trace on [0, horizon]: detection on sampled measurements, action after the delays, physical
    consequence (review R2 PD-04 / PD-07).

    ``threshold_error`` shifts the *actual* comparator threshold (theta_true = theta_nom + error).
    The detector counter resets on every false sample; the action becomes effective at
    t_confirm + exec_delay + action_delay.  An action after the horizon is scheduled-but-not-observed: the grid,
    the peak and every event stay inside the horizon.  The returned samples are the measurements of the ACTUAL
    trajectory (the no-action counterfactual is a separate field); the release instant is read from the sensed
    samples, not from the physical variable.
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
    samples = samples[samples <= H]
    grid = np.unique(np.concatenate([np.arange(0.0, H, dt), samples, [H]]))
    grid = grid[grid <= H]
    x_free = plant.x(grid, math.inf)
    y0 = x_free[0] if sensor.initial_state is None else sensor.initial_state
    y_free = (1.0 + sensor.gain_error) * _filter(grid, x_free, sensor.tau_filter_s, y0) + sensor.offset
    ys_free = np.interp(samples, grid, y_free)
    t_confirm, t_first, _ = _detect(samples, ys_free, th, sensor.comparator, sensor.confirm_samples)
    t_act = math.inf if t_confirm is None else t_confirm + sensor.exec_delay_s + ad
    observed = not math.isinf(t_act) and t_act <= H
    if observed:
        grid = np.unique(np.concatenate([grid, [t_act]]))
    x = plant.x(grid, t_act if observed else math.inf)
    y = (1.0 + sensor.gain_error) * _filter(grid, x, sensor.tau_filter_s, y0) + sensor.offset
    ys = np.interp(samples, grid, y)                     # the ACTUAL measured samples
    flags = ys > th if sensor.comparator == ">" else ys >= th
    k_pk = int(np.argmax(x))
    t_limit = _first_crossing(grid, x, lim)
    t_xcross = _first_crossing(grid, x, th)
    t_ycross = _first_crossing(grid, y, th)
    rel = None
    if release_threshold is not None and observed:
        r_th = _finite("release_threshold", release_threshold)
        below = np.flatnonzero((samples > t_act) & (ys < r_th))
        rel = None if below.size == 0 else float(samples[below[0]])
    return {
        "t_s": grid, "x": x, "y": y, "samples_t_s": samples, "samples_y": ys, "sample_flags": flags,
        "no_action_samples_y": ys_free,
        "events": {"t_xcross_s": t_xcross, "t_ycross_s": t_ycross, "t_first_sample_s": t_first,
                   "t_confirm_s": t_confirm, "t_action_effective_s": t_act if observed else None,
                   "t_action_scheduled_s": None if math.isinf(t_act) else t_act, "action_observed": observed,
                   "t_limit_s": t_limit, "t_release_sensed_s": rel, "t_release_s": rel,
                   "peak": float(x[k_pk]), "t_peak_s": float(grid[k_pk])},
        "threshold_true": th, "limit": lim, "protected": t_limit is None, "horizon_s": H,
        "detected": t_confirm is not None,
        "sensor_initial_state": {"value": float(y0), "basis": "declared" if sensor.initial_state is not None else
                                 "settled at the plant's initial value (default)"},
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


def _containment(plant: Plant, x_act: float, limit: float) -> tuple[float | None, str]:
    """Exact supremum of the variable after the action for the native plants (None: not available)."""
    if plant.kind == "thermal_1node":
        T_eq = plant.p("T_coolant_C") + plant.p("R_K_per_W") * plant.p("P_after_W", 0.0)
        return max(x_act, T_eq), (f"one-node exact solution: after the action T(t) moves monotonically from "
                                  f"{x_act:.6g} toward the equilibrium {T_eq:.6g}, so it never exceeds "
                                  f"max({x_act:.6g}, {T_eq:.6g})")
    if plant.kind == "capacitor_energy":
        C, P0, tr = plant.p("C_F"), plant.p("P0_W"), plant.p("t_ramp_s", 0.0)
        v = math.sqrt(max(x_act * x_act + 2.0 * P0 * 0.5 * tr / C, 0.0)) if P0 > 0 else x_act
        return v, "exact energy balance: the net power ramps to zero after the action, the voltage then stays constant"
    return x_act, "the ramp plant is frozen after the action"


def protection_review(plant: Plant, sensor: Sensor, fault_threshold: float, limit: float, horizon_s: float,
                      action_delay_s: float = 0.0, E_theta: float = 0.0, normal_plants: tuple = (),
                      warning_threshold: float | None = None, warning_needed_s: float | None = None,
                      release_threshold: float | None = None, x_normal_max: float | None = None,
                      tight_attainable: bool = False, hw_path: str | None = None,
                      upper_bound=None, dx_after: float = 0.0, phases: int = 16,
                      warning_confirm_samples: int | None = None, require_immediate_reversal: bool = False,
                      threshold_errors_independent: bool = False) -> dict:
    """Review of one threshold set on causal trajectories; every row states its evidence level.

    Sensor error extremes are applied as separate traces: the protection trace reads LOW (under-read:
    -|gain|, -|offset|, threshold +E_theta), the nuisance trace reads HIGH (over-read, threshold -E_theta).
    The warning lead is evaluated on the SAME trace as the fault reaction it precedes (same plant, sensor
    realisation and sample phase), minimised over the sampled realisations with its own witness (review R2 PD-02).
    The threshold tolerance E_theta shifts both comparators together (one error set) unless
    ``threshold_errors_independent`` declares independent comparators (then warning late, fault early).
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
    # PROT-02 warning usefulness: on each realisation the warning and the fault detector read the SAME samples of
    # the same trace; the warning does not act on the plant; the minimum lead over realisations is the claim
    if warning_threshold is not None and warning_needed_s is not None:
        nw = sensor.confirm_samples if warning_confirm_samples is None else int(warning_confirm_samples)
        if nw < 1:
            raise InputValidationError("warning_confirm_samples must be >= 1", field="warning_confirm_samples")
        worst = None
        n_ph = max(1, int(phases))
        shifts = ((E_theta, -E_theta),) if threshold_errors_independent else ((E_theta, E_theta), (-E_theta, -E_theta))
        n_real = 0
        for name, sen in (("under-reading", under), ("over-reading", over)):
            for e_w, e_f in shifts:
                for i in range(n_ph):
                    ph = sen.period_s * i / n_ph
                    n_real += 1
                    tr_ = simulate(plant, replace(sen, phase_s=ph), fault_threshold, limit, horizon_s,
                                   action_delay_s, e_f)
                    t_f = tr_["events"]["t_confirm_s"]
                    ref = t_f if t_f is not None else tr_["events"]["t_limit_s"]
                    t_w, _, _ = _detect(tr_["samples_t_s"], tr_["samples_y"], warning_threshold + e_w,
                                        sen.comparator, nw)
                    if ref is None and t_w is None:
                        continue
                    lead = -math.inf if t_w is None else (math.inf if ref is None else ref - t_w)
                    if worst is None or lead < worst["lead_s"]:
                        worst = {"lead_s": lead, "phase_s": ph, "sensor": name, "t_warning_s": t_w,
                                 "t_reference_s": ref, "threshold_errors": [e_w, e_f]}
        if worst is None:
            rows.append(_row("PROT-02", "warning usefulness", Status.UNKNOWN,
                             "neither warning, fault nor limit reached in the horizon on any sampled realisation"))
        elif worst["t_warning_s"] is None:
            rows.append(_row("PROT-02", "warning usefulness", Status.INFEASIBLE,
                             f"no warning before the fault reaction on the {worst['sensor']} sensor at sample phase "
                             f"{worst['phase_s']:.4g} s (same trace)", "causal simulation witness"))
        elif math.isinf(worst["lead_s"]):
            rows.append(_row("PROT-02", "warning usefulness", Status.FEASIBLE,
                             f"warning confirmed; neither fault nor limit reached in the horizon"))
        else:
            ok = worst["lead_s"] >= warning_needed_s
            rows.append(_row("PROT-02", "warning usefulness", Status.FEASIBLE if ok else Status.INFEASIBLE,
                             f"minimum lead {worst['lead_s'] * 1e3:.4g} ms over {n_real} sampled realisations (needed "
                             f"{warning_needed_s * 1e3:.4g} ms; witness: {worst['sensor']} sensor, sample phase "
                             f"{worst['phase_s']:.4g} s, warning and fault on the same samples)",
                             "causal simulation, sampled phases" if ok else "causal simulation witness"))
    else:
        rows.append(_row("PROT-02", "warning usefulness", Status.UNKNOWN, "no warning threshold / required "
                         "intervention time declared"))
    # PROT-03 derating / reaction effectiveness: the declared objective is containment below the limit (exact
    # supremum after the action for the native plants); an immediate reversal is a separate, optional requirement
    ev = base["events"]
    if ev["t_action_effective_s"] is None:
        rows.append(_row("PROT-03", "derating / reaction effectiveness", Status.UNKNOWN,
                         "no action effective within the horizon" + (
                             f" (scheduled at {ev['t_action_scheduled_s']:.4g} s: not observed)"
                             if ev.get("t_action_scheduled_s") is not None else "")))
    else:
        ta = ev["t_action_effective_s"]
        x_act = float(np.interp(ta, base["t_s"], base["x"]))
        sup, how = _containment(plant, x_act, limit)
        contained = sup < limit and base["protected"]
        det = (f"after the action at {ta:.4g} s the variable is bounded by {sup:.6g} {'<' if sup < limit else '>='} "
               f"limit {limit:.6g} ({how})")
        st = Status.FEASIBLE if contained else Status.INFEASIBLE
        if require_immediate_reversal and plant.kind == "thermal_1node":
            T_eq = plant.p("T_coolant_C") + plant.p("R_K_per_W") * plant.p("P_after_W", 0.0)
            if x_act < T_eq:
                st = Status.INFEASIBLE
                det += f"; immediate reversal required but T keeps rising toward {T_eq:.6g}"
        rows.append(_row("PROT-03", "derating / reaction effectiveness", st, det, "exact post-action solution"))
    # PROT-05 threshold feasibility (conservative window)
    if x_normal_max is not None:
        win = threshold_window(x_normal_max, limit, E_plus=o + g * abs(x_normal_max), E_minus=o + g * abs(limit),
                               E_theta=E_theta, dx_after=dx_after, tight_attainable=tight_attainable,
                               upper_bound=upper_bound, candidate=fault_threshold,
                               sensor_memoryless=sensor.tau_filter_s == 0)
        prot04 = next(r for r in rows if r["id"] == "PROT-04")
        if win["candidate"]["protection_guaranteed"] and prot04["status"] == "INFEASIBLE":
            # a sufficient guarantee and an admissible failing trajectory of the same declared model cannot coexist
            win["candidate"]["protection_guaranteed"] = False
            win["candidate"]["contradicted_by"] = prot04["detail"]
            win["claim"] = Claim("threshold_window", Status.UNKNOWN, win["claim"]["quantity"], win["claim"]["scope"],
                                 reasons=(Reason.CONFLICTING_EVIDENCE,),
                                 detail="the bound claims protection but a trajectory of the same declared model "
                                        "crosses the limit (PROT-04): the bound's assumptions do not hold for this "
                                        "scenario - no guarantee").to_dict()
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
                             "the sensed samples do not fall below the release threshold in the horizon"))
        else:
            rows.append(_row("PROT-06", "recovery (hysteresis, re-trigger)", Status.UNKNOWN,
                             f"sensed release condition at {ev['t_release_s']:.4g} s; restoring authority needs the "
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
