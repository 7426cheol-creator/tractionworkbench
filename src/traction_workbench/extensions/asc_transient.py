"""ASC fault-current transient and its two current-time requirements (independent review, 9.13).

One fault event, one trajectory, several questions evaluated on it:

* the balanced, symmetric active short circuit in the flux state
      d psi_d / dt = v_d - Rs i_d + w_e psi_q,     d psi_q / dt = v_q - Rs i_q - w_e psi_d,   v_d = v_q = 0
  from the pre-fault state (id, iq at the policy point, same angle for all phases, currents persist during the
  declared delay until the short is established).  Constant-parameter model: psi_d = psi_PM + Ld id,
  psi_q = Lq iq, solved exactly (matrix exponential); an independent Runge-Kutta integration is returned as a
  cross-check of the same equations.  With an inertia J the SIGNED mechanical state is integrated,
  J d omega_m/dt = T_em - tau_rot(omega_m) - T_load, d theta_e/dt = p omega_m (reverse speed and zero crossing
  included); its difference to the fixed-speed solution is a model sensitivity, not a solver check.
* the timeline is strictly ordered inside the horizon: a short established at or after the horizon is
  scheduled-but-not-observed (no extrapolation, no backward integration); a 6SO interval has no native model, so
  every window after the reaction starts depends on it and stays UNKNOWN.
* phase currents i_a,b,c(t) from the dq trajectory and the electrical angle.  The initial angle is unknown, so
  every phase operator is maximised over it: the phase peak, RMS and I^2 t have EXACT all-angle enclosures
  (sup_theta0 |i_a(t)| = |i_dq(t)|; max_theta0 int i_a^2 = 1/2 int |i|^2 + 1/2 |int (i_d + j i_q)^2 e^{2 j theta} dt|);
  a time-above operator is sampled over initial angles and phases.  Customer phase requirements and the
  phase-current stress screening of the device use the SAME enclosure (review R2 PD-03).
* each customer requirement is an explicit operator on the waveform with its own quantity, window, time origin
  and limit (``abs_peak``, ``rms``, ``envelope_after`` in A; ``time_above`` (cumulative) and
  ``time_above_contiguous`` with a current level and an allowed duration in s); windows use their exact
  endpoints and localised crossings; an incomplete definition is REQUIREMENT_INCOMPLETE.  Every verdict carries
  a numerical allowance from a refinement (twice the time points and angles).  I^2 t is not used as a survival
  criterion unless the supplier defines it so.
* motor demagnetisation and device survival are judged only against imported envelopes (minimum d-axis current
  at the magnet temperature, device peak / I^2 t / pulse limits with their conditions); without them UNKNOWN.

Claim level: the constant-parameter model is a SCREENING model for transients (saturation, cross-coupling and
temperature change are not represented), flux maps are not dynamically qualified (see the magnetic
qualification) - so the joint claim is at most SCREENING: its per-item verdicts are reported, the joint status
stays UNKNOWN.  A 6SO/freewheel interval before the short needs a rectifier model and is not native.  The normal
current limit is never used to clip fault currents.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

_trapezoid = getattr(np, "trapezoid", None) or np.trapz      # numpy < 2.0 compatibility
from scipy.integrate import solve_ivp
from scipy.linalg import expm

from ..errors import InputValidationError
from ..models.components import DriveModel
from ..models.flux import ConstantFluxModel
from ..validation import finite as _finite
from ..physics import DriveKernel
from ..scenario import DcSourceLimits, Scenario
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

TWO_PI = 2.0 * math.pi
OPERATORS = ("abs_peak", "rms", "envelope_after", "time_above", "time_above_contiguous")
TIME_OPERATORS = ("time_above", "time_above_contiguous")
QUANTITIES = ("phase", "dq_norm")
ORIGINS = ("fault", "asc_established")


@dataclass(frozen=True)
class CurrentTimeRequirement:
    req_id: str
    quantity: str            # phase | dq_norm
    operator: str            # abs_peak | rms | envelope_after | time_above | time_above_contiguous
    t_start_s: float
    t_end_s: float
    limit_A: float | None = None      # current limit (peak / rms / envelope)
    origin: str = "fault"             # time origin of the window
    level_A: float | None = None      # time operators: the current level
    text: str = ""
    limit_s: float | None = None      # time operators: the allowed duration (its own dimension)

    def missing(self) -> list[str]:
        out = []
        if self.quantity not in QUANTITIES:
            out.append(f"quantity one of {QUANTITIES}")
        if self.operator not in OPERATORS:
            out.append(f"operator one of {OPERATORS}")
        if self.origin not in ORIGINS:
            out.append(f"time origin one of {ORIGINS}")
        if not (math.isfinite(self.t_start_s) and math.isfinite(self.t_end_s)) or self.t_end_s <= self.t_start_s or \
                self.t_start_s < 0:
            out.append("window 0 <= t_start < t_end")
        if self.operator in TIME_OPERATORS:
            if self.level_A is None or not math.isfinite(self.level_A) or self.level_A < 0:
                out.append("current level_A >= 0 for a time operator")
            if self.limit_s is None or not math.isfinite(self.limit_s) or self.limit_s < 0:
                out.append("allowed duration limit_s >= 0 for a time operator (a duration is not an ampere limit)")
        elif self.limit_A is None or not math.isfinite(self.limit_A):
            out.append("current limit_A")
        return out


def _linear_system(k: DriveKernel):
    psi, ld, lq, rs = k.psi, k.Ld, k.Lq, k.Rs
    return psi, ld, lq, rs


def transient_constant(k: DriveKernel, id0: float, iq0: float, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Exact solution at constant speed: x = x_ss + expm(A t)(x0 - x_ss), x = [psi_d, psi_q]."""
    psi, ld, lq, rs = _linear_system(k)
    w = k.omega_e
    A = np.array([[-rs / ld, w], [-w, -rs / lq]])
    b = np.array([rs * psi / ld, 0.0])
    x_ss = -np.linalg.solve(A, b)
    x0 = np.array([psi + ld * id0, lq * iq0])
    out = np.empty((t.size, 2))
    for n, tt in enumerate(t):
        out[n] = x_ss + expm(A * tt) @ (x0 - x_ss)
    idt = (out[:, 0] - psi) / ld
    iqt = out[:, 1] / lq
    return idt, iqt


def transient_integrated(k: DriveKernel, id0: float, iq0: float, t: np.ndarray, J_kgm2: float | None = None,
                         T_load_Nm: float = 0.0, method: str = "RK45") -> dict:
    """Numerical integration of the flux state, optionally with the SIGNED mechanical state (review R2 PD-05):
    J d omega_m/dt = T_em - tau_rot(omega_m) - T_load (tau_rot re-evaluated at the state), d theta_e/dt = omega_e."""
    psi, ld, lq, rs = _linear_system(k)
    p = k.p
    w0 = k.omega_e
    rot = getattr(k, "rot", None)

    def rhs(tt, y):
        psd, psq, we, _th = y
        i_d = (psd - psi) / ld
        i_q = psq / lq
        dpsd = -rs * i_d + we * psq
        dpsq = -rs * i_q - we * psd
        if J_kgm2:
            tem = 1.5 * p * (psd * i_q - psq * i_d)
            trot = 0.0 if rot is None else rot.torque(we / p)
            dwe = p * (tem - trot - T_load_Nm) / J_kgm2
        else:
            dwe = 0.0
        return [dpsd, dpsq, dwe, we]

    sol = solve_ivp(rhs, (0.0, float(t[-1])), [psi + ld * id0, lq * iq0, w0, 0.0], t_eval=t, rtol=1e-10, atol=1e-12,
                    method=method)
    if not sol.success or not np.all(np.isfinite(sol.y)):
        return {"success": False, "message": sol.message if not sol.success else "non-finite state"}
    psd, psq, we, th = sol.y
    return {"success": True, "id_A": (psd - psi) / ld, "iq_A": psq / lq, "omega_e": we, "theta_e": th}


def _phase_currents(idt, iqt, theta):
    ia = idt * np.cos(theta) - iqt * np.sin(theta)
    ib = idt * np.cos(theta - TWO_PI / 3) - iqt * np.sin(theta - TWO_PI / 3)
    ic = idt * np.cos(theta + TWO_PI / 3) - iqt * np.sin(theta + TWO_PI / 3)
    return np.vstack([ia, ib, ic])


def _window(t, series, a, b):
    """Samples on [a, b] with the exact window endpoints (linear interpolation of every series)."""
    inner = (t > a) & (t < b)
    tw = np.concatenate([[a], t[inner], [b]])
    return tw, [np.concatenate([[np.interp(a, t, x)], x[inner], [np.interp(b, t, x)]]) for x in series]


def _above(tw: np.ndarray, v: np.ndarray, level: float) -> tuple[float, float]:
    """(cumulative, longest contiguous) time with |v| > level on the piecewise-linear signal, crossings exact."""
    v0, v1 = v[:-1], v[1:]
    cuts = [tw]
    for L in (level, -level):
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (L - v0) / (v1 - v0)
        ok = np.isfinite(u) & (u > 0) & (u < 1)
        cuts.append(tw[:-1][ok] + u[ok] * (tw[1:][ok] - tw[:-1][ok]))
    tt = np.unique(np.concatenate(cuts))
    if tt.size < 2:
        return 0.0, 0.0
    on = np.abs(np.interp(0.5 * (tt[:-1] + tt[1:]), tw, v)) > level
    d = np.diff(tt)
    if not on.any():
        return 0.0, 0.0
    edges = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(int), [0]])))
    cs = np.concatenate([[0.0], np.cumsum(d)])
    return float(np.sum(d[on])), float(np.max(cs[edges[1::2]] - cs[edges[::2]]))


def _sq_integral_all_angles(tw, idw, iqw, thw) -> tuple[float, float]:
    """max over the initial angle theta0 of int i_a^2 dt, and the maximising theta0:
    i_a = Re((i_d + j i_q) e^{j (theta0 + theta)}) so int i_a^2 = 1/2 int |i|^2 + 1/2 Re(e^{2 j theta0} Z)."""
    a = float(_trapezoid(idw * idw + iqw * iqw, tw))
    z = (idw + 1j * iqw) ** 2 * np.exp(2j * thw)
    Z = complex(_trapezoid(z.real, tw), _trapezoid(z.imag, tw))
    return 0.5 * a + 0.5 * abs(Z), (-0.5 * math.atan2(Z.imag, Z.real)) % math.pi


def _run(k: DriveKernel, id0: float, iq0: float, td: float, t6: float, H: float, J: float | None, n_time: int,
         T_load: float) -> dict:
    """One strictly ordered trajectory on [0, H]: pre-fault currents to t_asc = td + t6, the short afterwards."""
    t_asc = td + t6
    n_post = max(200, int(n_time))
    t_post = np.linspace(t_asc, H, n_post)                  # exact endpoints: nothing beyond the horizon
    ts = t_post - t_asc
    exact = transient_constant(k, id0, iq0, ts)
    num = transient_integrated(k, id0, iq0, ts, J, T_load)
    if not num["success"]:
        return {"success": False, "message": num["message"]}
    if J:
        other = transient_integrated(k, id0, iq0, ts, J, T_load, method="DOP853")
        if not other["success"]:
            return {"success": False, "message": other["message"]}
        idt, iqt, we_post, th_post = num["id_A"], num["iq_A"], num["omega_e"], num["theta_e"]
        cross = float(max(np.max(np.abs(other["id_A"] - idt)), np.max(np.abs(other["iq_A"] - iqt))))
        sens = float(max(np.max(np.abs(exact[0] - idt)), np.max(np.abs(exact[1] - iqt))))
    else:
        idt, iqt = exact
        we_post = np.full(ts.size, k.omega_e)
        th_post = k.omega_e * ts
        cross = float(max(np.max(np.abs(num["id_A"] - idt)), np.max(np.abs(num["iq_A"] - iqt))))
        sens = None
    n_pre = max(40, int(n_time) // 20) if t_asc > 0 else 0
    pre_t = np.linspace(0.0, t_asc, n_pre, endpoint=False) if t_asc > 0 else np.array([])
    t = np.concatenate([pre_t, t_post])
    if not np.all(np.diff(t) > 0):
        return {"success": False, "message": "time stamps not strictly increasing"}
    w0 = k.omega_e
    id_all = np.concatenate([np.full(pre_t.size, id0), idt])
    iq_all = np.concatenate([np.full(pre_t.size, iq0), iqt])
    omega = np.concatenate([np.full(pre_t.size, w0), we_post])
    theta = np.concatenate([w0 * pre_t, w0 * t_asc + th_post])
    out = {"success": True, "t": t, "id": id_all, "iq": iq_all, "omega_e": omega, "theta": theta, "t_asc": t_asc,
           "solver_cross_check_A": cross, "fixed_speed_sensitivity_A": sens}
    if not J:                                     # constant speed: the exact solution between the samples
        def exact(tt, _k=k, _a=t_asc, _i0=(id0, iq0)):
            tt = np.atleast_1d(np.asarray(tt, dtype=float))
            d_, q_ = transient_constant(_k, _i0[0], _i0[1], np.maximum(tt - _a, 0.0))
            pre = tt < _a
            return np.where(pre, _i0[0], d_), np.where(pre, _i0[1], q_)
        out["exact"] = exact
    if J:
        p = k.p
        rot = getattr(k, "rot", None)
        ld, lq, rs = k.Ld, k.Lq, k.Rs
        wm = we_post / p
        W = 1.5 * (0.5 * ld * idt ** 2 + 0.5 * lq * iqt ** 2) + 0.5 * J * wm ** 2
        P_diss = 1.5 * rs * (idt ** 2 + iqt ** 2) + (0.0 if rot is None else
                                                     np.array([rot.torque(w) * w for w in wm])) + T_load * wm
        lost = float(_trapezoid(P_diss, ts))
        out["energy_ledger"] = {"stored_start_J": float(W[0]), "stored_end_J": float(W[-1]), "dissipated_J": lost,
                                "residual_J": float(W[0] - W[-1] - lost),
                                "note": "magnetic 3/2 (Ld id^2 + Lq iq^2)/2 + kinetic J wm^2 / 2 against copper, "
                                        "rotational and load losses (short circuit: no terminal power)"}
    return out


PEAK_TOL_S = 1e-10          # golden-section tolerance of a refined peak instant


def _refine_max(fx, tw, vals) -> tuple[float, float, int]:
    """sup over the window of a smooth scalar signal with an exact evaluator fx(t): every sampled local maximum within
    reach of the largest sample (a smooth maximum lies within one sample of a sampled local maximum; the second
    difference, taken generously, decides which ones can matter), refined by golden section on its two neighbouring
    intervals to PEAK_TOL_S.  The window endpoints are samples themselves.  Returns (value, instant, maxima refined)."""
    n = vals.size
    k = int(np.argmax(vals))
    best_v, best_t = float(vals[k]), float(tw[k])
    if n < 3:
        return best_v, best_t, 0
    d2 = float(np.max(np.abs(np.diff(vals, 2))))
    reach = best_v - 4.0 * d2 - 1e-9 * max(abs(best_v), 1.0)
    loc = [j for j in range(1, n - 1) if vals[j] >= vals[j - 1] and vals[j] >= vals[j + 1] and vals[j] >= reach]
    g = (math.sqrt(5.0) - 1.0) / 2.0
    for j in loc:
        a, b = float(tw[j - 1]), float(tw[j + 1])
        x1, x2 = b - g * (b - a), a + g * (b - a)
        f1, f2 = fx(x1), fx(x2)
        while b - a > PEAK_TOL_S:
            if f1 < f2:
                a, x1, f1 = x1, x2, f2
                x2 = a + g * (b - a)
                f2 = fx(x2)
            else:
                b, x2, f2 = x2, x1, f1
                x1 = b - g * (b - a)
                f1 = fx(x1)
        xm = 0.5 * (a + b)
        vm = fx(xm)
        if vm > best_v:
            best_v, best_t = vm, xm
    return best_v, best_t, len(loc)


def _refine_peak(exact, tw, mag) -> tuple[float, float, int]:
    """sup |i_dq| over the window from the exact solution (see _refine_max)."""
    def m_at(x):
        a_, b_ = exact(x)
        return float(np.hypot(a_[0], b_[0]))
    return _refine_max(m_at, tw, mag)


def _evaluate(req: CurrentTimeRequirement, run: dict, angles: int) -> dict:
    t, idq_d, idq_q, th = run["t"], run["id"], run["iq"], run["theta"]
    off = 0.0 if req.origin == "fault" else run["t_asc"]
    a, b = req.t_start_s + off, req.t_end_s + off
    if b > t[-1] * (1 + 1e-12):
        return {"req_id": req.req_id, "status": "REQUIREMENT_INCOMPLETE",
                "missing": [f"simulation horizon {t[-1]:.4g} s shorter than the window end {b:.4g} s"]}
    tw, (idw, iqw, thw) = _window(t, (idq_d, idq_q, th), a, b)
    mag = np.hypot(idw, iqw)
    out = {"req_id": req.req_id, "quantity": req.quantity, "operator": req.operator, "window_s": [req.t_start_s,
           req.t_end_s], "origin": req.origin, "worst_phase": None, "worst_initial_angle_deg": None, "at_s": None}
    if req.operator in ("abs_peak", "envelope_after"):
        k = int(np.argmax(mag))
        val, at = float(mag[k]), float(tw[k])
        exact = run.get("exact")
        if exact is not None:
            val, at, n_ref = _refine_peak(exact, tw, mag)
            out["peak_basis"] = (f"exact solution maximised between the samples around {n_ref} sampled local maxima "
                                 f"(golden section to {PEAK_TOL_S:.0e} s)")
            out["sampling_bound_A"] = 0.0
        else:
            # integrated trajectory: the sampled maximum is below the true one by at most dt^2/8 max|m''|; the second
            # difference estimates dt^2 m'' at the samples, taken twice for safety (review 3 F-24)
            d2 = np.abs(np.diff(mag, 2)) if mag.size > 2 else np.zeros(1)
            out["sampling_bound_A"] = float(np.max(d2)) / 4.0
            out["peak_basis"] = "sampled maximum of the integrated trajectory with a curvature bound"
        out.update(value=val, at_s=at)
        if req.quantity == "phase":
            idk, iqk = (exact(at) if exact is not None else (np.array([idw[k]]), np.array([iqw[k]])))
            thk = float(np.interp(at, tw, thw))
            ang = (-math.atan2(float(iqk[0]), float(idk[0])) - thk) % TWO_PI
            out.update(worst_phase="a", worst_initial_angle_deg=math.degrees(ang),
                       angle_basis="exact: sup over the initial angle of |i_phase(t)| is |i_dq(t)|")
    elif req.operator == "rms":
        if req.quantity == "phase":
            sq, ang = _sq_integral_all_angles(tw, idw, iqw, thw)
            out.update(worst_phase="a", worst_initial_angle_deg=math.degrees(ang),
                       angle_basis="exact: max over the initial angle of the window integral of i_phase^2")
        else:
            sq = float(_trapezoid(mag * mag, tw))
        out["value"] = math.sqrt(max(sq, 0.0) / (b - a))
    else:
        contiguous = req.operator == "time_above_contiguous"
        if req.quantity == "dq_norm":
            cum, run_ = _above(tw, mag, req.level_A)
            out["value"] = run_ if contiguous else cum
        else:
            best = (-1.0, None, None)
            for a0 in np.arange(max(4, angles)) * TWO_PI / max(4, angles):
                iph = _phase_currents(idw, iqw, a0 + thw)
                for ph in range(3):
                    cum, run_ = _above(tw, iph[ph], req.level_A)
                    v = run_ if contiguous else cum
                    if v > best[0]:
                        best = (v, ph, a0)
            out.update(value=best[0], worst_phase="abc"[best[1]], worst_initial_angle_deg=math.degrees(best[2]),
                       angle_basis=f"sampled: {max(4, angles)} initial angles x 3 phases (each phase's own duration)")
    return out


def asc_transient(drive: DriveModel, scenario: Scenario, id0_A: float, iq0_A: float, t_delay_s: float,
                  horizon_s: float, requirements: tuple = (), t_6so_s: float = 0.0, J_kgm2: float | None = None,
                  demag_id_min_A: float | None = None, demag_basis: str = "", device_peak_A: float | None = None,
                  device_i2t_A2s: float | None = None, device_basis: str = "", angles: int = 72,
                  n_time: int = 4000, T_load_Nm: float = 0.0) -> dict:
    """Fault at t = 0, short established at t_asc = t_delay + t_6so (strictly inside the horizon)."""
    k = DriveKernel(drive, scenario)
    td = _finite("t_delay_s", t_delay_s)
    H = _finite("horizon_s", horizon_s)
    t6 = _finite("t_6so_s", t_6so_s)
    id0, iq0 = _finite("id0_A", id0_A), _finite("iq0_A", iq0_A)
    if td < 0 or H <= 0 or t6 < 0:
        raise InputValidationError("need t_delay >= 0, t_6so >= 0 and a horizon > 0", field="t_delay_s")
    if J_kgm2 is not None and _finite("J_kgm2", J_kgm2) <= 0:
        raise InputValidationError("inertia must be > 0 when given", field="J_kgm2")
    _finite("T_load_Nm", T_load_Nm)
    reqs = [r if isinstance(r, CurrentTimeRequirement) else CurrentTimeRequirement(**r) for r in requirements]
    ids = [r.req_id for r in reqs]
    if len(set(ids)) != len(ids):
        raise InputValidationError("requirement ids must be unique", field="requirements")
    t_asc = td + t6
    base = {"model": k.kind, "t_delay_s": td, "t_6so_s": t6, "t_asc_s": t_asc, "horizon_s": H}
    if not k.evaluable or k.issues:
        return {**base, "evaluable": False, "claim": Claim(
            "asc_joint", Status.UNKNOWN, "ASC transient requirements", "fault transient",
            reasons=tuple(dict.fromkeys(i.reason for i in k.issues)) or (Reason.OUTSIDE_MODEL_DOMAIN,),
            detail="model not valid at this scenario: " + "; ".join(i.message for i in k.issues)).to_dict()}
    if not isinstance(drive.motor.flux, ConstantFluxModel):
        return {**base, "evaluable": False, "claim": Claim(
            "asc_joint", Status.UNKNOWN, "ASC transient requirements", "fault transient",
            reasons=(Reason.OUTSIDE_MODEL_DOMAIN,),
            detail="flux map: the bilinear interpolant is not dynamically qualified (no qualified inverse map / "
                   "differential inductance) - the transient is not evaluated; import a qualified fault-domain "
                   "model").to_dict()}
    if t_asc >= H:
        return {**base, "evaluable": False, "requirements": {r.req_id: {"req_id": r.req_id, "status": "NOT_COVERED"}
                                                             for r in reqs},
                "claim": Claim("asc_joint", Status.UNKNOWN, "ASC transient requirements", "fault transient",
                               reasons=(Reason.OUTSIDE_MODEL_DOMAIN,),
                               detail=f"the short is established at t_asc = {t_asc * 1e3:.4g} ms, not before the "
                                      f"horizon {H * 1e3:.4g} ms: scheduled but not observed - the ASC windows are not "
                                      f"covered (no extrapolation, no backward integration)").to_dict()}
    runs = [_run(k, id0, iq0, td, t6, H, J_kgm2, n, T_load_Nm) for n in (n_time, 2 * int(n_time))]
    if not all(r["success"] for r in runs):
        bad = next(r for r in runs if not r["success"])
        return {**base, "evaluable": False, "claim": Claim(
            "asc_joint", Status.UNKNOWN, "ASC transient requirements", "fault transient",
            reasons=(Reason.NUMERICAL_UNRESOLVED,),
            detail=f"integration failed ({bad['message']}): no fallback to another model").to_dict()}
    run, fine = runs
    six = t6 > 0
    per_req = {}
    for r in reqs:
        miss = r.missing()
        if miss:
            per_req[r.req_id] = {"req_id": r.req_id, "status": "REQUIREMENT_INCOMPLETE", "missing": miss}
            continue
        e0 = _evaluate(r, run, angles)
        if e0.get("status") == "REQUIREMENT_INCOMPLETE":
            per_req[r.req_id] = e0
            continue
        e1 = _evaluate(r, fine, 2 * angles)
        lim = r.limit_s if r.operator in TIME_OPERATORS else r.limit_A
        # the refinement difference alone is not a bound (two grids can miss a peak alike): the fine run's own
        # sampling bound is the floor, and a refined peak is exact to the golden-section tolerance (review 3 F-24)
        allowance = max(abs(e1["value"] - e0["value"]), float(e1.get("sampling_bound_A") or 0.0),
                        1e-9 * abs(e1["value"]))
        margin = lim - e1["value"]
        off = 0.0 if r.origin == "fault" else t_asc
        if six and r.t_end_s + off > td:
            verdict = "UNKNOWN"
            why = "the window reaches the unmodelled 6SO interval or the short that follows it"
        elif margin >= allowance:
            verdict, why = "PASS", ""
        elif margin < -allowance:
            verdict, why = "FAIL", ""
        else:
            verdict, why = "UNRESOLVED", "the margin lies within the numerical allowance"
        per_req[r.req_id] = {**e1, "limit": lim, "margin": margin, "numerical_allowance": allowance,
                             "screening_verdict": verdict, "why": why,
                             "unit": "s" if r.operator in TIME_OPERATORS else "A",
                             "level_A": r.level_A if r.operator in TIME_OPERATORS else None}
    # the same all-angle enclosure over the whole event for the phase-current stress screening of the device
    t, idd, iqq, th = fine["t"], fine["id"], fine["iq"], fine["theta"]
    mag = np.hypot(idd, iqq)
    pk_i = int(np.argmax(mag))
    # the event's dq peak and minimum i_d between the samples (review 3 F-24): exact solution where it exists, else the
    # sampled extremum with a curvature bound (a sampled extremum is not a bound)
    ex_f = fine.get("exact")
    if ex_f is not None:
        pk_val, pk_t, _ = _refine_peak(ex_f, t, mag)
        mn, _, _ = _refine_max(lambda x: -float(ex_f(x)[0][0]), t, -idd)
        min_id, pk_bound, id_bound = -mn, 0.0, 0.0
    else:
        pk_val, pk_t, min_id = float(mag[pk_i]), float(t[pk_i]), float(np.min(idd))
        pk_bound = float(np.max(np.abs(np.diff(mag, 2)))) / 4.0 if mag.size > 2 else 0.0
        id_bound = float(np.max(np.abs(np.diff(idd, 2)))) / 4.0 if idd.size > 2 else 0.0
    if ex_f is not None:
        _idk, _iqk = ex_f(pk_t)
        pk_ang = math.degrees((-math.atan2(float(_iqk[0]), float(_idk[0])) - float(np.interp(pk_t, t, th))) % TWO_PI)
    else:
        pk_ang = math.degrees((-math.atan2(iqq[pk_i], idd[pk_i]) - th[pk_i]) % TWO_PI)
    i2t, i2t_ang = _sq_integral_all_angles(t, idd, iqq, th)
    t0_, idd0, iqq0, th0 = run["t"], run["id"], run["iq"], run["theta"]
    i2t_coarse = _sq_integral_all_angles(t0_, idd0, iqq0, th0)[0]
    items = {}
    if demag_id_min_A is None:
        items["demagnetisation"] = {"status": "UNKNOWN", "detail": "no validated demagnetisation envelope imported "
                                    "(the declared id domain is not a demag safe-domain)"}
    elif six:
        items["demagnetisation"] = {"status": "UNKNOWN", "detail": "the minimum d-axis current depends on the "
                                    "unmodelled 6SO interval"}
    else:
        m = min_id
        items["demagnetisation"] = {"status": "SCREENING_PASS" if m - id_bound >= demag_id_min_A else "SCREENING_FAIL",
                                    "min_id_A": m, "sampling_bound_A": id_bound, "limit_A": demag_id_min_A,
                                    "basis": demag_basis or "not stated",
                                    "detail": "dq current against an imported single-threshold envelope (local "
                                              "field, magnet temperature and history not represented)"}
    signal = ("phase-current proxy over every initial angle (exact enclosure, the same basis as the phase-current "
              "requirements); no conduction-topology model: not switch / diode / die current and not module "
              "survival")
    if device_peak_A is None and device_i2t_A2s is None:
        items["device_survival"] = {"status": "UNKNOWN", "signal": signal,
                                    "detail": "no supplier pulse / SOA / I^2 t envelope for this waveform, voltage, "
                                              "temperature and gate condition (a short-circuit withstand time is not "
                                              "an ASC permission)", "phase_peak_A": pk_val,
                                    "phase_I2t_A2s": i2t}
    elif six:
        items["device_survival"] = {"status": "UNKNOWN", "signal": signal,
                                    "detail": "the phase-current stress depends on the unmodelled 6SO interval"}
    else:
        ok, det = True, []
        if device_peak_A is not None:
            ok &= pk_val + pk_bound <= device_peak_A
            det.append(f"phase peak {pk_val:.6g} A at {pk_t * 1e3:.4g} ms"
                       + (f" (+ sampling bound {pk_bound:.2g} A)" if pk_bound else "") + f" vs {device_peak_A:g} A")
        if device_i2t_A2s is not None:
            ok &= i2t + abs(i2t - i2t_coarse) <= device_i2t_A2s
            det.append(f"phase I^2t {i2t:.6g} A^2s (+/- {abs(i2t - i2t_coarse):.2g}) vs {device_i2t_A2s:g} A^2s")
        items["device_survival"] = {"status": "SCREENING_PASS" if ok else "SCREENING_FAIL", "signal": signal,
                                    "detail": "; ".join(det), "basis": device_basis or "not stated",
                                    "phase_peak_A": pk_val, "phase_I2t_A2s": i2t,
                                    "worst_initial_angle_deg": pk_ang}
    if six:
        items["6so_interval"] = {"status": "UNKNOWN", "detail": f"{t6 * 1e3:g} ms freewheel before the short: the "
                                 "diode-rectifier interval is not modelled natively; the trajectory holds the pre-fault "
                                 "currents there, so every result after the reaction start is UNKNOWN"}
    dom = drive.domain
    id_lo, id_hi = dom.id_A
    iq_lo, iq_hi = dom.iq_A
    ex_id = [min(float(np.min(idd)), min_id), float(np.max(idd))]     # the refined minimum where it exists
    ex_iq = [float(np.min(iqq)), float(np.max(iqq))]
    outside = ex_id[0] < id_lo or ex_id[1] > id_hi or ex_iq[0] < iq_lo or ex_iq[1] > iq_hi
    factor = max(ex_id[0] / id_lo if id_lo < 0 else 1.0, ex_id[1] / id_hi if id_hi > 0 else 1.0,
                 ex_iq[0] / iq_lo if iq_lo < 0 else 1.0, ex_iq[1] / iq_hi if iq_hi > 0 else 1.0)
    items["model_domain"] = {
        "status": "OUTSIDE_DECLARED_DOMAIN" if outside else "INSIDE", "id_range_A": ex_id, "iq_range_A": ex_iq,
        "domain_id_A": [id_lo, id_hi], "domain_iq_A": [iq_lo, iq_hi], "domain_kind": dom.kind,
        "excursion_factor": float(factor),
        "detail": (f"the trajectory leaves the declared current domain (i_d {ex_id[0]:.4g}..{ex_id[1]:.4g} A vs "
                   f"{id_lo:g}..{id_hi:g} A, i_q {ex_iq[0]:.4g}..{ex_iq[1]:.4g} A vs {iq_lo:g}..{iq_hi:g} A: up to "
                   f"{factor:.2f}x): the constant-parameter model is extrapolated there - saturation lowers or raises "
                   f"the peak depending on the axis, so no direction is claimed" if outside else
                   "the trajectory stays inside the declared current domain")}
    reqs_done = list(per_req.values())
    incomplete = [v for v in reqs_done if v.get("status") == "REQUIREMENT_INCOMPLETE"]
    fails = [v for v in reqs_done if v.get("screening_verdict") == "FAIL"]
    level = ("SCREENING (constant-parameter linear magnetic model; saturation, cross-coupling and temperature change "
             "not represented)")
    if incomplete or not reqs:
        joint_detail, reasons = "current-time requirements incomplete or not stated", \
            (Reason.REQUIREMENT_INCOMPLETE,)
    elif fails:
        joint_detail = ("screening indicates the requirement(s) " + ", ".join(v["req_id"] for v in fails) +
                        " are exceeded - confirm with a qualified nonlinear fault-domain model before redesign")
        reasons = (Reason.OUTSIDE_MODEL_DOMAIN,)
    else:
        joint_detail = ("screening meets or does not decide the stated current-time requirements; demagnetisation "
                        "and device survival need imported envelopes and a qualified transient model for a joint PASS")
        reasons = (Reason.OUTSIDE_MODEL_DOMAIN,)
    if outside:
        joint_detail += "; " + items["model_domain"]["detail"]
    step = max(1, t.size // 3000)
    show = next((v["worst_initial_angle_deg"] for v in per_req.values() if v.get("worst_initial_angle_deg")
                 is not None), math.degrees((-math.atan2(iqq[pk_i], idd[pk_i]) - th[pk_i]) % TWO_PI))
    iph0 = _phase_currents(idd, iqq, th + math.radians(show))
    tem = 1.5 * k.p * ((k.psi + (k.Ld - k.Lq) * idd) * iqq)
    cross = run["solver_cross_check_A"]
    return {**base, "evaluable": True, "claim_level": level,
            "pre_fault": {"id_A": id0, "iq_A": iq0, "speed_rpm": k.speed_rpm, "omega_e_rad_s": k.omega_e},
            "steady_asc": {"id_A": float(idd[-1]), "iq_A": float(iqq[-1])},
            "peak_dq_A": pk_val, "peak_dq_at_s": pk_t, "min_id_A": min_id,
            "peak_torque_Nm": float(np.min(tem)) if abs(np.min(tem)) > abs(np.max(tem)) else float(np.max(tem)),
            "rk_cross_check_A": cross, "solver_cross_check_A": cross,
            "fixed_speed_sensitivity_A": run["fixed_speed_sensitivity_A"],
            "energy_ledger": fine.get("energy_ledger"), "requirements": per_req, "items": items,
            "claim": Claim("asc_joint", Status.UNKNOWN, "ASC current-time requirements + demag + device survival",
                           level, reasons=reasons,
                           evidence=(Evidence.make(EvidenceKind.DIRECT_EVALUATION,
                                                   f"transient with a same-equation solver cross-check {cross:.2e} A; "
                                                   f"refinement allowance on every verdict"),),
                           detail=joint_detail).to_dict(),
            "waveform": {"t_s": t[::step].tolist(), "id_A": idd[::step].tolist(), "iq_A": iqq[::step].tolist(),
                         "ia_A": iph0[0][::step].tolist(), "ib_A": iph0[1][::step].tolist(),
                         "ic_A": iph0[2][::step].tolist(), "omega_e_rad_s": fine["omega_e"][::step].tolist(),
                         "theta_e_rad": th[::step].tolist(), "T_em_Nm": tem[::step].tolist(), "t_asc_s": t_asc,
                         "shown_initial_angle_deg": show},
            "not_modelled": ["saturation / cross-coupling in the fault domain", "device drops and diode/channel "
                             "commutation", "phase skew of the short", "gate-driver UVLO / OC priority over ASC",
                             "magnet temperature change during the event", "6SO / freewheel rectifier interval"]}
