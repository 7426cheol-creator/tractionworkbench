"""Torque shaping and active driveline damping evaluation (variable-PWM / anti-jerk addendum, P1-DAMP).

Not a production anti-jerk controller and not a vehicle approval.  The question: which torque rise / fall excites
the torsional mode, and how much can a DECLARED causal shaper / feedback with its real sampling, delay and torque
authority reduce it - at what cost in response time, mean torque, current and loss?

Plant (first scope: fixed gear, one axle, small deformation, contact maintained, no large tyre slip), referred to
the motor side with the fixed ideal-gear ratio g = omega_m / omega_gear_out:
  J_l = J_out / g^2, k = k_out / g^2, c = c_out / g^2, T_L = T_L,out / g
  delta = theta_m - theta_l,  T_s = k delta + c delta',  J_m omega_m' = T_act - T_s,  J_l omega_l' = T_s - T_L
  E = J_m omega_m^2 / 2 + J_l omega_l^2 / 2 + k delta^2 / 2,  E' = T_act omega_m - T_L omega_l - c delta'^2
  omega_n = sqrt(k (1/J_m + 1/J_l)),  zeta = c (1/J_m + 1/J_l) / (2 omega_n)
T_act is the ACTUAL applied torque (after arbitration, clipping, delay and the actuator response), never the
request.  The linear plant is integrated EXACTLY (matrix exponential) between controller events (sample instants and
their delayed application), so peaks between samples are not missed and no delay is rounded to a grid.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass

import numpy as np
from scipy.linalg import expm

from ..errors import InputValidationError
from ..validation import finite as _finite

TWO_PI = 2.0 * math.pi


# --------------------------------------------------------------------------------------------- plant

@dataclass(frozen=True)
class Driveline:
    Jm_kgm2: float                    # motor-side inertia before the elastic element (motor coordinates)
    J_out_kgm2: float                 # load inertia in OUTPUT coordinates (includes M r^2 only for no-slip rolling)
    k_out_Nm_per_rad: float           # torsional stiffness of the elastic element, output coordinates
    c_out_Nms_per_rad: float          # its damping, output coordinates
    ratio: float = 1.0                # g = omega_m / omega_gear_out (ideal gear before the elastic shaft), > 0
    wheel_radius_m: float | None = None
    contact: str = "maintained"       # "maintained" (linear domain) | "backlash" (declared gap; transitions UNKNOWN)
    backlash_out_rad: float | None = None
    basis: str = ""

    def __post_init__(self):
        for n in ("Jm_kgm2", "J_out_kgm2", "k_out_Nm_per_rad", "ratio"):
            if _finite(n, getattr(self, n)) <= 0:
                raise InputValidationError(f"{n} must be > 0", field=n)
        if _finite("c_out_Nms_per_rad", self.c_out_Nms_per_rad) < 0:
            raise InputValidationError("damping must be >= 0", field="c_out_Nms_per_rad")
        if self.contact not in ("maintained", "backlash"):
            raise InputValidationError("contact must be maintained or backlash", field="contact")
        if not self.basis.strip():
            raise InputValidationError("the torsional ROM needs its basis (FRF identification / validated model)",
                                       field="basis")

    def referred(self) -> dict:
        g2 = self.ratio ** 2
        return {"Jm": self.Jm_kgm2, "Jl": self.J_out_kgm2 / g2, "k": self.k_out_Nm_per_rad / g2,
                "c": self.c_out_Nms_per_rad / g2, "g": self.ratio}

    def modal(self) -> dict:
        r = self.referred()
        s = 1.0 / r["Jm"] + 1.0 / r["Jl"]
        wn = math.sqrt(r["k"] * s)
        zeta = r["c"] * s / (2.0 * wn)
        mu = r["Jm"] * r["Jl"] / (r["Jm"] + r["Jl"])
        return {"omega_n_rad_s": wn, "f_n_Hz": wn / TWO_PI, "zeta": zeta, "mu_kgm2": mu,
                "alpha": r["Jl"] / (r["Jm"] + r["Jl"]),
                "omega_d_rad_s": wn * math.sqrt(max(0.0, 1.0 - zeta * zeta))}


def _plant(r: dict, tau_act: float):
    """x = [omega_m, omega_l, delta, T_act]; inputs [T_cmd, T_L].  Without an actuator lag T_act = T_cmd (3 states)."""
    Jm, Jl, k, c = r["Jm"], r["Jl"], r["k"], r["c"]
    if tau_act > 0:
        A = np.array([[-c / Jm, c / Jm, -k / Jm, 1 / Jm],
                      [c / Jl, -c / Jl, k / Jl, 0.0],
                      [1.0, -1.0, 0.0, 0.0],
                      [0.0, 0.0, 0.0, -1 / tau_act]])
        B = np.array([[0.0, 0.0], [0.0, -1 / Jl], [0.0, 0.0], [1 / tau_act, 0.0]])
    else:
        A = np.array([[-c / Jm, c / Jm, -k / Jm],
                      [c / Jl, -c / Jl, k / Jl],
                      [1.0, -1.0, 0.0]])
        B = np.array([[1 / Jm, 0.0], [0.0, -1 / Jl], [0.0, 0.0]])
    return A, B


def _step(A, B, x, u, h, cache):
    """Exact x(t+h) for a constant input over h (augmented matrix exponential, cached by h)."""
    key = round(h, 15)
    M = cache.get(key)
    if M is None:
        n, m = A.shape[0], B.shape[1]
        Z = np.zeros((n + m, n + m))
        Z[:n, :n] = A * h
        Z[:n, n:] = B * h
        E = expm(Z)
        M = (E[:n, :n], E[:n, n:])
        cache[key] = M
    Phi, Gam = M
    return Phi @ x + Gam @ u


# --------------------------------------------------------------------------------------------- controllers

@dataclass(frozen=True)
class Shaper:
    """Torque-request shaping in the controller task (runs at the controller sample period)."""

    kind: str = "none"                # none | rate | prefilter | zv (zero-vibration input shaper at a declared mode)
    rate_Nm_per_s: float | None = None
    tau_s: float | None = None
    zv_f_Hz: float | None = None
    zv_zeta: float | None = None

    def __post_init__(self):
        if self.kind not in ("none", "rate", "prefilter", "zv"):
            raise InputValidationError("shaper kind must be none, rate, prefilter or zv", field="kind")
        if self.kind == "rate" and not (self.rate_Nm_per_s and self.rate_Nm_per_s > 0):
            raise InputValidationError("a rate shaper needs rate_Nm_per_s > 0", field="rate_Nm_per_s")
        if self.kind == "prefilter" and not (self.tau_s and self.tau_s > 0):
            raise InputValidationError("a prefilter needs tau_s > 0", field="tau_s")
        if self.kind == "zv" and not (self.zv_f_Hz and self.zv_f_Hz > 0 and self.zv_zeta is not None
                                      and 0 <= self.zv_zeta < 1):
            raise InputValidationError("a ZV shaper needs the declared mode frequency and damping", field="zv_f_Hz")


@dataclass(frozen=True)
class Damping:
    """Active damping feedback in the controller task: T_ad = -Kd * estimate(omega_m - omega_l)."""

    kind: str = "none"                # none | relative_speed (motor + load speed sensed) | motor_speed_hpf
    Kd_Nms_per_rad: float = 0.0
    hpf_Hz: float | None = None       # motor-speed high-pass cut-off (motor_speed_hpf)
    quantization_rad_s: float = 0.0   # speed measurement resolution (0: ideal)
    load_speed_skew_s: float = 0.0    # the load (wheel) speed sample is this much OLDER than the motor speed sample
    dropouts: tuple = ()              # ((t_start_s, t_end_s), ...) windows where the sensed signal is unavailable
    dropout_signal: str = "load"      # which signal drops out: "load" (e.g. wheel speed message) | "motor" (resolver)
    stale_limit_s: float | None = None  # declared fallback: beyond this age the feedback fades out (None: not declared)
    fade_s: float = 0.0               # fade-out / fade-in ramp of the damping torque
    hpf_order: int = 1                # motor_speed_hpf: 1 = first-order high-pass; 2 = two cascaded stages (washout).
    # Under a sustained acceleration a the motor speed is a ramp: a first-order high-pass settles at a / omega_c, so the
    # feedback keeps a constant correction -Kd a / omega_c (a steady torque deficit while the vehicle accelerates);
    # the second-order washout returns to zero on a ramp (steady_hpf_correction gives both in closed form).

    def __post_init__(self):
        if self.hpf_order not in (1, 2):
            raise InputValidationError("hpf_order must be 1 or 2", field="hpf_order")
        if self.kind not in ("none", "relative_speed", "motor_speed_hpf"):
            raise InputValidationError("damping kind must be none, relative_speed or motor_speed_hpf", field="kind")
        if _finite("Kd", self.Kd_Nms_per_rad) < 0:
            raise InputValidationError("Kd must be >= 0", field="Kd_Nms_per_rad")
        if self.kind == "motor_speed_hpf" and not (self.hpf_Hz and self.hpf_Hz > 0):
            raise InputValidationError("motor-speed damping needs the high-pass cut-off", field="hpf_Hz")
        for n in ("load_speed_skew_s", "fade_s"):
            if _finite(n, getattr(self, n)) < 0:
                raise InputValidationError(f"{n} must be >= 0", field=n)
        if self.stale_limit_s is not None and _finite("stale_limit_s", self.stale_limit_s) <= 0:
            raise InputValidationError("the stale limit must be > 0", field="stale_limit_s")
        if self.dropout_signal not in ("load", "motor"):
            raise InputValidationError("dropout signal must be load or motor", field="dropout_signal")
        for w in self.dropouts:
            if len(w) != 2 or not (float(w[0]) < float(w[1])):
                raise InputValidationError("dropout windows are (t_start, t_end) with t_start < t_end", field="dropouts")

    def unavailable(self, signal: str, t: float) -> bool:
        return signal == self.dropout_signal and any(float(a) <= t < float(b) for a, b in self.dropouts)


@dataclass(frozen=True)
class Controller:
    sample_s: float                   # torque / damping task period
    delay_s: float                    # sample -> torque applied (from the declared delay ledger; not rounded)
    actuator_tau_s: float = 0.0       # first-order torque-response ROM of the current loop (declared, validated)
    shaper: Shaper = Shaper()
    damping: Damping = Damping()
    emergency_bypasses_shaper: bool = True

    def __post_init__(self):
        if _finite("sample_s", self.sample_s) <= 0:
            raise InputValidationError("sample period must be > 0", field="sample_s")
        for n in ("delay_s", "actuator_tau_s"):
            if _finite(n, getattr(self, n)) < 0:
                raise InputValidationError(f"{n} must be >= 0", field=n)


@dataclass(frozen=True)
class Maneuver:
    T0_Nm: float                      # base torque request before the step
    T1_Nm: float                      # after the step (a tip-in / tip-out)
    t_step_s: float
    t_end_s: float
    speed_rpm: float                  # operating speed at the start (motor side)
    TL_out_Nm: float = 0.0            # constant road / load torque at the OUTPUT
    window_Nm: tuple | None = None    # allowed actual torque window [T_min, T_max] (from arbitration / capability)
    emergency_t_s: float | None = None
    emergency_T_Nm: float | None = None
    output_dt_s: float = 5e-4


# --------------------------------------------------------------------------------------------- simulation

def simulate(dl: Driveline, ctl: Controller, man: Maneuver) -> dict:
    """Sampled controller + exact linear plant between events; returns dense output and the event record."""
    r = dl.referred()
    md = dl.modal()
    A, B = _plant(r, ctl.actuator_tau_s)
    n = A.shape[0]
    wm0 = man.speed_rpm * TWO_PI / 60.0
    TL = man.TL_out_Nm / r["g"]
    # steady initial state at the base request: the shaft carries T0 - J_m * a with a common acceleration a
    a0 = (man.T0_Nm - TL) / (r["Jm"] + r["Jl"])
    Ts0 = man.T0_Nm - r["Jm"] * a0
    x = np.zeros(n)
    x[0] = x[1] = wm0
    x[2] = Ts0 / r["k"]
    if n == 4:
        x[3] = man.T0_Nm
    Tsamp, tau = ctl.sample_s, ctl.delay_s
    cache = {}
    t_out = np.arange(0.0, man.t_end_s + 1e-12, man.output_dt_s)
    # event list: sample instants (measure + compute) and their applications at +tau
    ks = np.arange(0, int(math.floor(man.t_end_s / Tsamp)) + 1)
    pending = []                        # (t_apply, T_cmd, record)
    u_cmd = man.T0_Nm
    shaper_state = {"y": man.T0_Nm, "hist": []}
    hpf = {"y": 0.0, "prev": None, "y2": 0.0}
    t = 0.0
    xs = np.zeros((t_out.size, n))
    ucur = np.zeros(t_out.size)
    oi = 0
    rec = {"t_s": [], "T_request": [], "T_shaped": [], "T_ad": [], "T_cmd": [], "clipped": [], "clip_side": [],
           "age_s": [], "gain": [], "stale": [], "meas_error_rad_s": []}
    dp = ctl.damping
    skew = dp.load_speed_skew_s if dp.kind == "relative_speed" else 0.0
    a_init = (man.T0_Nm - TL) / (r["Jm"] + r["Jl"])
    held = {"motor": (wm0, 0.0), "load": (wm0 - a_init * skew, -skew)}      # (value, measurement time)
    load_meas = {}                                                            # sample index -> (value, time)
    meas_events = [(k * Tsamp - skew, k) for k in ks if skew > 0 and k * Tsamp - skew >= 0.0]
    mi = 0
    gain_state = 1.0
    zv = None
    if ctl.shaper.kind == "zv":
        z = ctl.shaper.zv_zeta
        wd = TWO_PI * ctl.shaper.zv_f_Hz * math.sqrt(1 - z * z)
        K = math.exp(-z * math.pi / math.sqrt(1 - z * z))
        zv = (1 / (1 + K), K / (1 + K), math.pi / wd)
    emerg_active = False
    applied = []                        # (t_apply, command, actuator torque at that instant): exact event data

    def request(tk):
        return man.T1_Nm if tk >= man.t_step_s else man.T0_Nm

    events = sorted([(k * Tsamp, 0, k) for k in ks])     # (time, type 0 = sample)
    ev_i = 0

    def advance(x, t, t_to, u):
        nonlocal oi
        while oi < t_out.size and t_out[oi] <= t_to + 1e-15:
            h = t_out[oi] - t
            xs[oi] = _step(A, B, x, np.array([u, TL]), h, cache) if h > 0 else x
            ucur[oi] = u
            oi += 1
        h = t_to - t
        return _step(A, B, x, np.array([u, TL]), h, cache) if h > 0 else x

    while True:
        t_next_sample = events[ev_i][0] if ev_i < len(events) else math.inf
        t_next_apply = pending[0][0] if pending else math.inf
        t_next_meas = meas_events[mi][0] if mi < len(meas_events) else math.inf
        t_next = min(t_next_sample, t_next_apply, t_next_meas, man.t_end_s)
        x = advance(x, t, t_next, u_cmd)
        t = t_next
        if t >= man.t_end_s - 1e-15 and t_next_sample > man.t_end_s and t_next_apply > man.t_end_s \
                and t_next_meas > man.t_end_s:
            break
        if t_next_meas <= min(t_next_sample, t_next_apply):
            load_meas[meas_events[mi][1]] = (float(x[1]), t)                 # skewed load-speed measurement
            mi += 1
            continue
        if t_next_apply <= t_next_sample:
            _t, u_new, _r = pending.pop(0)
            applied.append((t, float(u_new), float(x[3]) if n == 4 else float(u_cmd)))
            u_cmd = u_new
            continue
        # sample event: measure, shape, damp, arbitrate
        k = events[ev_i][2]
        ev_i += 1
        tk = t
        Treq = request(tk)
        emerg = man.emergency_t_s is not None and tk >= man.emergency_t_s
        if emerg:
            Treq = man.emergency_T_Nm
            emerg_active = True
        # shaping
        sh = ctl.shaper
        if emerg_active and ctl.emergency_bypasses_shaper:
            Tsh = Treq
            shaper_state["y"] = Treq
        elif sh.kind == "none":
            Tsh = Treq
        elif sh.kind == "rate":
            step_max = sh.rate_Nm_per_s * Tsamp
            y = shaper_state["y"]
            Tsh = y + max(-step_max, min(step_max, Treq - y))
            shaper_state["y"] = Tsh
        elif sh.kind == "prefilter":
            a = math.exp(-Tsamp / sh.tau_s)
            Tsh = a * shaper_state["y"] + (1 - a) * Treq
            shaper_state["y"] = Tsh
        else:
            A1, A2, Td = zv
            shaper_state["hist"].append((tk, Treq))
            past = [v for (tt, v) in shaper_state["hist"] if tt <= tk - Td + 1e-12]
            Tsh = A1 * Treq + A2 * (past[-1] if past else man.T0_Nm)
        # damping: sensed signals (skew, dropout -> held value with its age, quantization), declared stale fallback
        if skew > 0:
            t_meas = tk - skew
            wl_new, tl_new = load_meas[k] if k in load_meas else (wm0 + a_init * t_meas, t_meas)   # before t = 0:
        else:                                                                          # the steady initial motion
            wl_new, tl_new = float(x[1]), tk
        if not dp.unavailable("load", tl_new):
            held["load"] = (wl_new, tl_new)
        if not dp.unavailable("motor", tk):
            held["motor"] = (float(x[0]), tk)
        (wm, t_m), (wl, t_l) = held["motor"], held["load"]
        used = [t_m] + ([t_l] if dp.kind == "relative_speed" else [])
        age = tk - min(used)
        fresh = [tk] + ([tk - skew] if dp.kind == "relative_speed" else [])
        stale_now = any(u < f - 1e-12 for u, f in zip(used, fresh))          # a held (not fresh) value is in use
        if dp.quantization_rad_s > 0:
            q = dp.quantization_rad_s
            wm, wl = q * round(wm / q), q * round(wl / q)
        if dp.kind == "relative_speed":
            Tad = -dp.Kd_Nms_per_rad * (wm - wl)
            meas_err = (wm - wl) - float(x[0] - x[1])
        elif dp.kind == "motor_speed_hpf":
            ah = 1.0 / (1.0 + TWO_PI * dp.hpf_Hz * Tsamp)
            if hpf["prev"] is None:
                hpf["prev"] = wm
            y1 = ah * (hpf["y"] + wm - hpf["prev"])       # backward-Euler high-pass stage
            if dp.hpf_order == 2:                          # second identical stage on the first stage's output
                hpf["y2"] = ah * (hpf["y2"] + y1 - hpf["y"])
            hpf["y"] = y1
            hpf["prev"] = wm
            Tad = -dp.Kd_Nms_per_rad * (hpf["y2"] if dp.hpf_order == 2 else y1)
            meas_err = wm - float(x[0])
        else:
            Tad = 0.0
            meas_err = 0.0
        if dp.kind != "none" and dp.stale_limit_s is not None:
            target = 0.0 if age > dp.stale_limit_s + 1e-12 else 1.0
            step = 1.0 if dp.fade_s <= 0 else Tsamp / dp.fade_s
            gain_state = max(target, gain_state - step) if target < gain_state else min(target, gain_state + step)
            Tad *= gain_state
        if emerg_active:
            Tad = 0.0                              # the safety reduction is not modulated by comfort functions
        Tc = Tsh + Tad
        clipped, side = False, 0
        if man.window_Nm is not None:
            lo, hi = man.window_Nm
            Tcl = min(hi, max(lo, Tc))
            clipped = Tcl != Tc
            side = 0 if not clipped else (1 if Tc > hi else -1)
            Tc = Tcl
        rec["t_s"].append(tk)
        rec["T_request"].append(Treq)
        rec["T_shaped"].append(Tsh)
        rec["T_ad"].append(Tad)
        rec["T_cmd"].append(Tc)
        rec["clipped"].append(clipped)
        rec["clip_side"].append(side)
        rec["age_s"].append(age if dp.kind != "none" else 0.0)
        rec["gain"].append(gain_state if dp.kind != "none" else 1.0)
        rec["stale"].append(bool(stale_now) if dp.kind != "none" else False)
        rec["meas_error_rad_s"].append(meas_err)
        pending.append((tk + tau, Tc, k))
        pending.sort(key=lambda z: z[0])
    wm_, wl_, dl_ = xs[:, 0], xs[:, 1], xs[:, 2]
    Tact = xs[:, 3] if n == 4 else ucur
    Ts = r["k"] * dl_ + r["c"] * (wm_ - wl_)
    acc_l = (Ts - TL) / r["Jl"]                           # load angular acceleration (motor coordinates)
    ddelta = wm_ - wl_
    dwm = (Tact - Ts) / r["Jm"]
    jerk_l = (r["k"] * ddelta + r["c"] * (dwm - acc_l)) / r["Jl"]      # exact d/dt of acc_l (T_L constant)
    E = 0.5 * r["Jm"] * wm_ ** 2 + 0.5 * r["Jl"] * wl_ ** 2 + 0.5 * r["k"] * dl_ ** 2
    P_in = Tact * wm_ - TL * wl_ - r["c"] * ddelta ** 2
    dTact = (ucur - xs[:, 3]) / ctl.actuator_tau_s if n == 4 else None     # exact slew of the first-order ROM
    return {"t_s": t_out, "omega_m": wm_, "omega_l": wl_, "delta": dl_, "T_act": Tact, "T_shaft": Ts, "dTact_dt": dTact,
            "acc_l": acc_l, "jerk_l": jerk_l, "E_J": E, "dE_budget_W": P_in, "record": {k: np.array(v) for k, v in rec.items()},
            "referred": r, "modal": md, "TL_motor_Nm": TL, "wheel_radius_m": dl.wheel_radius_m,
            "applied": applied, "actuator_tau_s": ctl.actuator_tau_s, "T0_Nm": man.T0_Nm,
            "jerk_definition": "exact d/dt of the load angular acceleration from the plant state (motor coordinates); "
                               "no numerical differentiation, no filter"}


def steady_hpf_correction(dl: Driveline, damping: Damping, T_Nm: float, TL_out_Nm: float = 0.0) -> dict:
    """Closed-form steady state of motor-speed high-pass damping while the driveline accelerates under a constant
    command T (rigid body, J = J_m + J_l in motor coordinates).  The speed is a ramp of slope a; one backward-Euler
    high-pass stage settles exactly at a / omega_c, a second stage at 0.  With the correction fed back,
        a = (T - T_L - Kd a / omega_c) / J   ->   correction = -Kd (T - T_L) / (omega_c J + Kd)   (first order)
    and 0 for the second-order washout (and for relative-speed feedback: the twist rate is 0 at constant a).
    An independent check of the simulated steady deficit (not derived from the simulation)."""
    if damping.kind != "motor_speed_hpf":
        return {"correction_Nm": 0.0, "acceleration_rad_s2": None, "order": None,
                "meaning": "no speed high-pass in the loop" if damping.kind == "none" else
                           "relative-speed feedback: the twist rate is zero at a constant acceleration"}
    r = dl.referred()
    J = r["Jm"] + r["Jl"]
    TL = TL_out_Nm / r["g"]
    wc = TWO_PI * damping.hpf_Hz
    Kd = damping.Kd_Nms_per_rad
    if damping.hpf_order == 1:
        corr = -Kd * (T_Nm - TL) / (wc * J + Kd)
        a = (T_Nm - TL + corr) / J
    else:
        corr, a = 0.0, (T_Nm - TL) / J
    return {"correction_Nm": corr, "acceleration_rad_s2": a, "order": damping.hpf_order, "omega_c_rad_s": wc,
            "meaning": "steady torque correction while accelerating: first-order high-pass -Kd a / omega_c "
                       "(a deficit on the requested torque); second-order washout 0"}


def energy_residual(sim: dict) -> float:
    """|E(t_end) - E(0) - int (T_act w_m - T_L w_l - c delta'^2) dt| / max(|delta E|, E scale) (trapezoid on the
    dense output; the plant itself is integrated exactly)."""
    t, E, P = sim["t_s"], sim["E_J"], sim["dE_budget_W"]
    tr = getattr(np, "trapezoid", None) or np.trapz
    lhs = E[-1] - E[0]
    rhs = float(tr(P, t))
    return abs(lhs - rhs) / max(abs(lhs), 1e-9 * max(1.0, float(np.max(np.abs(E)))))


# --------------------------------------------------------------------------------------------- stability

def relative_mode_coefficients(dl: Driveline, Kd: float) -> dict:
    """delta'' + a1 delta' + a0 delta + b1 delta'(t - tau) = 0 for relative-speed feedback T_ad = -Kd (w_m - w_l)
    applied at the motor: a1 = c/mu, a0 = k/mu, b1 = Kd/J_m."""
    r = dl.referred()
    mu = r["Jm"] * r["Jl"] / (r["Jm"] + r["Jl"])
    return {"a1": r["c"] / mu, "a0": r["k"] / mu, "b1": Kd / r["Jm"]}


def undelayed_damping(dl: Driveline, Kd: float) -> dict:
    """Closed-loop relative mode without delay: zeta = (c + alpha Kd) / (2 sqrt(k mu))."""
    co = relative_mode_coefficients(dl, Kd)
    a1 = co["a1"] + co["b1"]
    wn = math.sqrt(co["a0"])
    md = dl.modal()
    return {"zeta": a1 / (2 * wn), "omega_n_rad_s": wn, "alpha": md["alpha"],
            "relative_power_term": f"-alpha Kd delta'^2 with alpha = {md['alpha']:.6g}",
            "total_power_term": "T_ad * omega_m (not a passive damper between the inertias)"}


def delay_crossings(a1: float, a0: float, b1: float, tau_max_s: float = 1.0) -> list[dict]:
    """Imaginary-axis crossings of s^2 + a1 s + a0 + b1 s e^{-s tau} (retarded, single delay): |P(jw)| = |Q(jw)|,
    tau_k = (phase + 2 pi k) / w, crossing direction sign Re(ds/dtau)."""
    # (a0 - w^2)^2 + a1^2 w^2 = b1^2 w^2  ->  w^4 - (2 a0 + b1^2 - a1^2) w^2 + a0^2 = 0
    B = 2 * a0 + b1 * b1 - a1 * a1
    disc = B * B - 4 * a0 * a0
    out = []
    if disc < 0:
        return out
    for w2 in ((B + math.sqrt(disc)) / 2, (B - math.sqrt(disc)) / 2):
        if w2 <= 0:
            continue
        w = math.sqrt(w2)
        P = complex(a0 - w2, a1 * w)
        Q = complex(0.0, b1 * w)
        e = -P / Q                                           # = e^{-j w tau}
        phase = (-cmath.phase(e)) % TWO_PI                   # w tau = -arg(e) mod 2 pi
        k = 0
        while True:
            tau = (phase + TWO_PI * k) / w
            if tau > tau_max_s:
                break
            s = 1j * w
            # ds/dtau = s Q e^{-s tau} / (P'(s) + Q'(s) e^{-s tau} - tau Q(s) e^{-s tau})
            E_ = cmath.exp(-s * tau)
            dP = 2 * s + a1
            dQ = b1
            dsdt = s * (b1 * s) * E_ / (dP + dQ * E_ - tau * (b1 * s) * E_)
            out.append({"omega_rad_s": w, "tau_s": tau, "direction": "into RHP" if dsdt.real > 0 else "into LHP"})
            k += 1
    return sorted(out, key=lambda d: d["tau_s"])


def rhp_roots_at(a1: float, a0: float, b1: float, tau: float) -> dict:
    """Number of RHP roots at the delay tau (from the stable undelayed count and the crossing directions) and the
    rightmost root found by continuation from the last crossing (Newton on the quasi-polynomial)."""
    f = lambda s: s * s + a1 * s + a0 + b1 * s * cmath.exp(-s * tau)                 # noqa: E731
    df = lambda s: 2 * s + a1 + b1 * cmath.exp(-s * tau) - b1 * s * tau * cmath.exp(-s * tau)   # noqa: E731
    n0 = 0 if (a1 + b1) > 0 and a0 > 0 else None
    if n0 is None:
        return {"n_rhp": None, "reason": "undelayed system not stable: count not established by crossings"}
    cr = [c for c in delay_crossings(a1, a0, b1, tau) if c["tau_s"] <= tau]
    n = n0 + sum(2 if c["direction"] == "into RHP" else -2 for c in cr)
    root = None
    if cr:
        c = cr[-1]
        s = complex(0.0, c["omega_rad_s"])
        steps = max(4, int(200 * (tau - c["tau_s"]) / max(tau, 1e-9)) + 4)
        for tt in np.linspace(c["tau_s"], tau, steps):
            g = lambda z, tt=tt: z * z + a1 * z + a0 + b1 * z * cmath.exp(-z * tt)            # noqa: E731
            dg = lambda z, tt=tt: 2 * z + a1 + b1 * cmath.exp(-z * tt) - b1 * z * tt * cmath.exp(-z * tt)  # noqa: E731
            for _ in range(50):
                ds = g(s) / dg(s)
                s -= ds
                if abs(ds) < 1e-13 * max(1.0, abs(s)):
                    break
        root = s
    return {"n_rhp": max(n, 0), "stable": n <= 0, "crossings": cr,
            "rightmost_root": None if root is None else {"re": root.real, "im": root.imag,
                                                         "residual": abs(f(root))}}


def sampled_eigenvalues(dl: Driveline, ctl: Controller) -> dict:
    """Closed-loop eigenvalues of the SAMPLED loop: ZOH plant (with the actuator ROM), the exact delay
    tau = (d + f) Ts (modified z-transform, no rounding) and the discrete damping law (relative speed or motor-speed
    high-pass).  The rigid-body mode (z = 1) is neutral and excluded from the stability verdict."""
    r = dl.referred()
    A, B2 = _plant(r, ctl.actuator_tau_s)
    B = B2[:, :1]
    n = A.shape[0]
    Ts = ctl.sample_s
    d = int(math.floor(ctl.delay_s / Ts + 1e-12))
    f = ctl.delay_s / Ts - d

    def gam(h):
        if h <= 0:
            return np.zeros((n, 1))
        Z = np.zeros((n + 1, n + 1))
        Z[:n, :n] = A * h
        Z[:n, n:] = B * h
        return expm(Z)[:n, n:]
    Phi = expm(A * Ts)
    G0 = gam((1 - f) * Ts)                                  # input issued d samples ago, applied from f Ts
    G1 = expm(A * (1 - f) * Ts) @ gam(f * Ts)              # input issued d+1 samples ago, still applied until f Ts
    dp = ctl.damping
    # controller output u[k] = -Kd * y[k]; y from x[k] (relative speed) or the HPF state
    hp = dp.kind == "motor_speed_hpf"
    hp2 = hp and dp.hpf_order == 2
    ah = 1.0 / (1.0 + TWO_PI * dp.hpf_Hz * Ts) if hp else 0.0
    m_hist = d + 1                                          # stored past commands u[k-1] .. u[k-d-1]
    N = n + m_hist + ((3 if hp2 else 2) if hp else 0)
    M = np.zeros((N, N))
    # plant: x[k+1] = Phi x + G0 u[k-d] + G1 u[k-d-1]; u[k-j] for j >= 1 are history states
    M[:n, :n] = Phi
    # u[k] expressed in states
    Cu = np.zeros(N)
    if dp.kind == "relative_speed":
        Cu[0], Cu[1] = -dp.Kd_Nms_per_rad, dp.Kd_Nms_per_rad
    elif hp:
        iy, ip = n + m_hist, n + m_hist + 1                 # HPF output y[k-1] and previous speed w[k-1]
        # y[k] = ah (y[k-1] + w[k] - w[k-1]);  u[k] = -Kd y[k]
        Cy = np.zeros(N)
        Cy[iy], Cy[0], Cy[ip] = ah, ah, -ah
        if hp2:                                             # second stage: y2[k] = ah (y2[k-1] + y[k] - y[k-1])
            iy2 = n + m_hist + 2
            Cy2 = ah * Cy
            Cy2[iy2] += ah
            Cy2[iy] -= ah
            Cu = -dp.Kd_Nms_per_rad * Cy2
        else:
            Cu = -dp.Kd_Nms_per_rad * Cy
    hist0 = n                                               # index of u[k-1]
    def u_lag(j):
        """row vector giving u[k-j] (j = 0 is the current command)."""
        if j == 0:
            return Cu
        e = np.zeros(N)
        e[hist0 + j - 1] = 1.0
        return e
    M[:n, :] += np.outer(G0[:, 0], u_lag(d)) + np.outer(G1[:, 0], u_lag(d + 1))
    # history shift: new u[k-1] = u[k]; new u[k-j] = old u[k-j+1]
    M[hist0, :] = Cu
    for j in range(1, m_hist):
        M[hist0 + j, hist0 + j - 1] = 1.0
    if hp:
        M[iy, :] = Cy
        M[ip, 0] = 1.0
        if hp2:
            M[iy2, :] = Cy2
    ev = np.linalg.eigvals(M)
    mags = np.abs(ev)
    rigid = np.argmin(np.abs(ev - 1.0))
    others = np.delete(ev, rigid)
    rho = float(np.max(np.abs(others))) if others.size else 0.0
    # dominant oscillatory pole -> continuous-equivalent damping
    osc = [z for z in others if abs(z.imag) > 1e-9]
    zeta = wd = None
    if osc:
        z = max(osc, key=lambda q: abs(q))
        s = np.log(z) / Ts
        wd = abs(s.imag)
        zeta = -s.real / abs(s)
    return {"eigenvalues": ev, "spectral_radius_excl_rigid": rho, "stable": rho < 1.0 - 1e-9,
            "dominant_zeta": zeta, "dominant_omega_rad_s": wd, "delay_samples": d, "delay_fraction": f,
            "note": "exact ZOH + fractional delay (modified z-transform); rigid-body mode excluded"}


# --------------------------------------------------------------------------------------------- metrics / evaluation

def clip_bias(base_Nm: float, amplitude_Nm: float, window: tuple, n: int = 200000) -> dict:
    """Mean of a sinusoidal correction after clipping the total to the window (the clipped correction is not
    zero-mean; this biases the delivered mean torque)."""
    th = (np.arange(n) + 0.5) * TWO_PI / n
    tot = np.clip(base_Nm + amplitude_Nm * np.sin(th), window[0], window[1])
    corr = tot - base_Nm
    return {"mean_correction_Nm": float(corr.mean()), "clipped_fraction": float(np.mean(np.abs(tot - base_Nm -
                                                                                       amplitude_Nm * np.sin(th)) > 1e-12))}


def _bracket(t: np.ndarray, cond: np.ndarray):
    """[last sample where cond is False, first later sample where it holds for good] - the output grid only
    brackets the event; None when it never holds for good."""
    bad = np.nonzero(~cond)[0]
    if bad.size == 0:
        return (float(t[0]), float(t[0]))
    if bad[-1] >= t.size - 1:
        return None
    return (float(t[bad[-1]]), float(t[bad[-1] + 1]))


def response_metrics(sim: dict, man: Maneuver, settle_band: float = 0.05) -> dict:
    """Comfort / response metrics of the maneuver against the REQUESTED target (review R2 CT-03).

    The reference is the common acceleration the requested torque produces, a_target = (T1 - T_L) / (J_m + J_l)
    (motor coordinates) - never the plateau a controller happened to reach: a response that delivers half the
    request does not reach 90 %.  Crossing instants are bracketed by the output grid ([last outside, first inside]),
    the bracket is part of the result.  With a safety request inside the window the metrics cover the window
    BEFORE the request (the comfort question); the safety reaction is judged on its own.  Peak jerk and shaft
    torque are maxima on the output grid (sampled, not certified)."""
    t_all = sim["t_s"]
    cut = man.emergency_t_s if (man.emergency_t_s is not None and man.emergency_t_s > man.t_step_s) else None
    keep = t_all < cut if cut is not None else np.ones(t_all.size, bool)
    t = t_all[keep]
    a = sim["acc_l"][keep]
    j = sim["jerk_l"][keep]
    post = t >= man.t_step_s
    r = sim["referred"]
    a0 = float(a[0])
    a_target = (man.T1_Nm - sim["TL_motor_Nm"]) / (r["Jm"] + r["Jl"])
    # the final level is the time average over the last WHOLE torsional periods of the evaluated window: a partial
    # period biases it by up to the oscillation amplitude (an exactly delivered request read as 1.05 of it, review 3)
    fn = (sim.get("modal") or {}).get("f_n_Hz")
    after = float(t[-1] - man.t_step_s)
    if fn and fn > 0 and after * fn >= 1.0:
        n_per = max(1, int(np.floor(0.5 * after * fn)))          # at most half of the window after the step
        sel = t >= float(t[-1]) - n_per / fn
        trap = getattr(np, "trapezoid", None) or np.trapz
        a_end = (float(trap(a[sel], t[sel]) / (t[sel][-1] - t[sel][0])) if sel.sum() > 1 else float(a[-1]))
        final_basis = f"time average over the last {n_per} torsional period(s) (f_n {fn:.4g} Hz)"
    else:
        a_end = float(np.mean(a[-max(3, int(0.05 * t.size)):]))
        final_basis = ("mean of the last 5 % of the samples: the window after the step is shorter than one torsional "
                       "period, the level is not settled")
    span = a_target - a0
    out = {"a_initial": a0, "a_target": a_target, "a_final": a_end, "a_final_basis": final_basis,
           "peak_jerk_abs": float(np.max(np.abs(j[post]))) if post.any() else None,
           "peak_basis": "maximum on the output grid (sampled, not a certified maximum)",
           "horizon_after_step_s": float(t[-1] - man.t_step_s)}
    if abs(span) > 1e-9:
        frac = (a - a0) / span
        out["achieved_fraction"] = (a_end - a0) / span
        tp, fp = t[post], frac[post]
        b10 = _bracket(tp, fp >= 0.1) if tp.size else None
        # first reaching of a level (not 'for good'): the first sample at or above it brackets the crossing
        def first(level):
            idx = np.nonzero(fp >= level)[0]
            if idx.size == 0:
                return None
            k = int(idx[0])
            return (float(tp[k - 1]) if k > 0 else float(tp[0]), float(tp[k]))
        f10, f90 = first(0.1), first(0.9)
        out["t_to_90_bracket_s"] = None if f90 is None else [f90[0] - man.t_step_s, f90[1] - man.t_step_s]
        out["t_to_90_s"] = None if f90 is None else f90[1] - man.t_step_s
        out["t_10_90_s"] = None if (f10 is None or f90 is None) else float(f90[1] - f10[1])
        out["overshoot"] = float(np.max(fp) - 1.0) if post.any() else None
        sb = _bracket(t, np.abs(frac - 1.0) <= settle_band)
        if sb is None:
            out["t_settle_s"], out["t_settle_bracket_s"] = None, None
        else:
            lo, hi = max(sb[0] - man.t_step_s, 0.0), max(sb[1] - man.t_step_s, 0.0)
            out["t_settle_s"], out["t_settle_bracket_s"] = hi, [lo, hi]
        a_rel = a_end - a0
        if abs(a_rel) > 1e-9:
            fr = (a[post] - a0) / a_rel
            idx = np.nonzero(fr >= 0.9)[0]
            out["rise_time_relative_to_achieved_value_s"] = (float(tp[idx[0]] - man.t_step_s) if idx.size else None)
    rec = sim["record"]
    rk = rec["t_s"] < cut if cut is not None else np.ones(rec["t_s"].size, bool)
    if rk.any():
        corr = rec["T_cmd"][rk] - rec["T_shaped"][rk]
        out["correction_rms_Nm"] = float(np.sqrt(np.mean(corr ** 2)))
        out["correction_mean_Nm"] = float(np.mean(corr))
        out["clipped_fraction"] = float(np.mean(rec["clipped"][rk]))
    if cut is not None:
        out["evaluated_until_s"] = float(cut)
        out["note"] = "comfort metrics up to the safety request; the reaction is judged separately"
    Ts = sim["T_shaft"]
    out["shaft_torque_peak_Nm"] = float(np.max(np.abs(Ts)))
    tol = 1e-6 * max(float(np.max(np.abs(Ts))), 1e-9)
    sgn = np.sign(np.where(np.abs(Ts) > tol, Ts, 0.0))
    nz = sgn[sgn != 0]
    out["torque_reversal"] = bool(nz.size > 1 and np.any(nz[1:] != nz[:-1]))
    out["min_shaft_torque_Nm"] = float(np.min(Ts))
    out["T_act_final_Nm"] = float(sim["T_act"][keep][-1])
    if sim.get("wheel_radius_m"):
        k = sim["wheel_radius_m"] / r["g"]                 # vehicle a = r * omega_w' = r * omega_l' / g (no slip)
        out["vehicle_acc_final_m_s2"] = a_end * k
        out["vehicle_acc_target_m_s2"] = a_target * k
        out["peak_vehicle_jerk_m_s3"] = None if out["peak_jerk_abs"] is None else out["peak_jerk_abs"] * k
    return out


def electrical_slew_limits(drive, speed_rpm: float, Vdc_V: float, limits, T_Nm: float, dI_A: float = 1.0) -> dict:
    """Torque slew the voltage headroom allows at an operating point (addendum 5.4, screening).

    At the policy point: k_t = dT/di_q and the differential q inductance dpsi_q/di_q from the model (central
    differences, not the apparent inductance); the q-axis voltage headroom up to the hardware ceiling V_c (the
    declared reserve between the command budget and the ceiling is the voltage kept for control dynamics):
    dv+ = sqrt(V_c^2 - v_d^2) - v_q (torque up), dv- = sqrt(V_c^2 - v_d^2) + v_q (torque down); slew = k_t dv / L_q.
    The d-axis voltage change with i_q, the resistive drop and the speed change are left out: an instantaneous
    first-order headroom, not a dynamic qualification of the flux model.  No declared reserve at a point on the
    voltage limit means no headroom."""
    from ..physics import forward_evaluation
    from ..scenario import Scenario
    from ..solvers.policy import PolicyEvaluator
    sc = Scenario("slew", float(speed_rpm), float(Vdc_V), limits)
    sol = PolicyEvaluator(drive, sc).solve(float(T_Nm))
    pt = sol.point
    if pt is None:
        return {"established": False, "reason": "no operating point: " + sol.policy_claim.detail}
    a = forward_evaluation(drive, sc, pt.id_A, pt.iq_A + dI_A).point
    b = forward_evaluation(drive, sc, pt.id_A, pt.iq_A - dI_A).point
    if a is None or b is None:
        return {"established": False, "reason": "model not evaluable around the operating point (domain edge)"}
    kt = (a.Te_Nm - b.Te_Nm) / (2 * dI_A)
    Lq = (a.psi_q_Wb - b.psi_q_Wb) / (2 * dI_A)
    Vc, vd, vq = pt.voltage_ceiling_V, pt.vd_V, pt.vq_V
    if Lq <= 0 or kt <= 0 or Vc * Vc <= vd * vd:
        return {"established": False, "reason": "no q-axis headroom or a non-positive k_t / L_q at the operating point"}
    root = math.sqrt(Vc * Vc - vd * vd)
    up, down = root - vq, root + vq
    return {"established": True, "T_Nm": float(T_Nm), "speed_rpm": float(speed_rpm), "Vdc_V": float(Vdc_V),
            "kt_Nm_per_A": kt, "Lq_diff_H": Lq, "vd_V": vd, "vq_V": vq, "V_ceiling_V": Vc,
            "V_budget_V": pt.voltage_budget_V,
            "headroom_up_V": up, "headroom_down_V": down,
            "pos_Nm_per_s": kt * max(up, 0.0) / Lq, "neg_Nm_per_s": kt * max(down, 0.0) / Lq,
            "basis": "instantaneous q-axis headroom to the voltage ceiling at the policy point (differential L_q, "
                     "k_t from the model; the declared reserve is the dynamic headroom)"}


def evaluate_variants(dl: Driveline, variants: dict, man: Maneuver, requirement: dict | None = None,
                      loss_fn=None, slew_limits: dict | None = None) -> dict:
    """Off / shaping / feedback / combined on the SAME maneuver and requirement.

    ``requirement``: {"t_to_90_max_s", "peak_jerk_max" | "peak_vehicle_jerk_max_m_s3", "settle_max_s",
    "safety_reaction_max_s"} (declared; missing -> UNKNOWN).  ``loss_fn(T_array, speed_rpm) -> W array`` adds the
    electrical loss cost of the actual torque trajectory.  ``slew_limits`` (electrical_slew_limits) checks that the
    actuator ROM does not slew faster than the voltage headroom allows.  Mandatory failure cases of the addendum
    (undeclared authority, stale / dropped signals, skew, one-sided reserve, safety interruption, voltage headroom)
    are reported and never silently replaced by FEASIBLE."""
    req = requirement or {}
    out = {}
    for name, ctl in variants.items():
        sim = simulate(dl, ctl, man)
        met = response_metrics(sim, man)
        st = sampled_eigenvalues(dl, ctl) if ctl.damping.kind != "none" else None
        res = {"metrics": met, "stability": None if st is None else {k: st[k] for k in (
            "stable", "spectral_radius_excl_rigid", "dominant_zeta", "delay_samples", "delay_fraction")},
               "energy_residual": energy_residual(sim)}
        reasons, notes, status = [], [], "FEASIBLE"

        def unknown(why):
            nonlocal status
            if status == "FEASIBLE":
                status = "UNKNOWN"
            reasons.append(why)
        if dl.contact == "backlash" and met["torque_reversal"]:
            unknown("shaft torque reverses with a declared backlash: contact transition outside the linear model "
                    "(qualified backlash model / external plant needed)")
        if st is not None and not st["stable"]:
            status = "INFEASIBLE"
            reasons.append("the sampled closed loop is unstable for THIS policy (not a physical impossibility)")
        if man.window_Nm is None:
            unknown("torque authority window not declared: clipping and the positive / negative reserve are not "
                    "evaluated (missing is not unlimited)")
        rec = sim["record"]
        Ts = ctl.sample_s
        dp = ctl.damping
        sens = {}
        if dp.kind != "none" and rec["t_s"].size:
            stale = rec["stale"].astype(bool)
            faded = rec["gain"] < 1.0 - 1e-12
            sens = {"max_age_s": float(rec["age_s"].max()), "stale_s": float(stale.sum() * Ts),
                    "unavailable_s": float(faded.sum() * Ts), "min_gain": float(rec["gain"].min()),
                    "max_meas_error_rad_s": float(np.abs(rec["meas_error_rad_s"]).max()),
                    "max_spurious_torque_Nm": float(dp.Kd_Nms_per_rad * np.abs(rec["meas_error_rad_s"]).max())}
            if stale.any() and dp.stale_limit_s is None:
                unknown(f"damping used a held (stale) {dp.dropout_signal} signal for {1e3 * sens['stale_s']:.4g} ms "
                        f"(age up to {1e3 * sens['max_age_s']:.4g} ms) without a declared stale limit / fallback")
            elif faded.any():
                notes.append(f"damping unavailable / fading for {1e3 * sens['unavailable_s']:.4g} ms (signal age > "
                             f"{1e3 * dp.stale_limit_s:.4g} ms, fade {1e3 * dp.fade_s:.4g} ms) - the response includes it")
            if dp.load_speed_skew_s > 0:
                notes.append(f"load-speed skew {1e3 * dp.load_speed_skew_s:.4g} ms: false relative speed up to "
                             f"{sens['max_meas_error_rad_s']:.4g} rad/s ({sens['max_spurious_torque_Nm']:.4g} N m of "
                             "spurious correction), included in the response")
        if dp.kind == "motor_speed_hpf":
            sc = steady_hpf_correction(dl, dp, man.T1_Nm, man.TL_out_Nm)
            res["steady_correction"] = sc
            if abs(sc["correction_Nm"]) > 1e-3 * max(1.0, abs(man.T1_Nm)):
                notes.append(f"first-order motor-speed high-pass: while the driveline accelerates it keeps a steady "
                             f"correction of {sc['correction_Nm']:.4g} N m (closed form -Kd (T - T_L) / (omega_c J + Kd))"
                             " - a torque deficit on the request; a second-order washout (hpf_order 2) or relative-"
                             "speed feedback returns to zero")
        side = rec["clip_side"] if rec["t_s"].size else np.array([])
        res["clipping"] = {"upper_s": float((side > 0).sum() * Ts), "lower_s": float((side < 0).sum() * Ts)}
        if res["clipping"]["lower_s"] > 0:
            notes.append(f"negative reserve (regen / charge acceptance) exhausted for {1e3 * res['clipping']['lower_s']:.4g} "
                         "ms: the correction is one-sided and biases the mean torque")
        if res["clipping"]["upper_s"] > 0:
            notes.append(f"positive reserve exhausted for {1e3 * res['clipping']['upper_s']:.4g} ms")
        if slew_limits is not None:
            if not slew_limits.get("established"):
                unknown("electrical torque slew limit not established: " + str(slew_limits.get("reason")))
            elif sim["dTact_dt"] is None:
                unknown("no actuator response ROM: torque steps are instantaneous in the model, the voltage headroom "
                        "cannot be checked")
            else:
                up = float(np.max(sim["dTact_dt"]))
                dn = float(-np.min(sim["dTact_dt"]))
                res["slew"] = {"max_up_Nm_per_s": up, "max_down_Nm_per_s": dn,
                               "limit_up_Nm_per_s": slew_limits["pos_Nm_per_s"],
                               "limit_down_Nm_per_s": slew_limits["neg_Nm_per_s"]}
                bad = [f"{w} {v:.4g} > {lim:.4g} N m/s" for w, v, lim in (("up", up, slew_limits["pos_Nm_per_s"]),
                                                                        ("down", dn, slew_limits["neg_Nm_per_s"]))
                       if v > lim]
                if bad:
                    unknown("voltage headroom: the actuator ROM slews faster than the q-axis headroom allows ("
                            + "; ".join(bad) + ") - nonlinear electrical dynamics or a validated response envelope needed")
        if man.window_Nm is not None and not (man.window_Nm[0] <= man.T1_Nm <= man.window_Nm[1]):
            status = "INFEASIBLE"
            reasons.append(f"the requested torque {man.T1_Nm:g} N m lies outside the declared authority "
                           f"[{man.window_Nm[0]:g}, {man.window_Nm[1]:g}] N m: the request cannot be delivered "
                           f"(achieved {met.get('achieved_fraction', float('nan')):.3g} of the requested change)")
        jk = ("peak_vehicle_jerk_max_m_s3", "peak_vehicle_jerk_m_s3", "peak vehicle jerk") if dl.wheel_radius_m else \
            ("peak_jerk_max", "peak_jerk_abs", "peak load angular jerk")
        covered = met.get("horizon_after_step_s", 0.0)
        for key, mkey, label, bkey in (("t_to_90_max_s", "t_to_90_s", "response time (to 90 % of the request)",
                                        "t_to_90_bracket_s"), (*jk, None),
                                       ("settle_max_s", "t_settle_s", "settling time (on the requested target)",
                                        "t_settle_bracket_s")):
            lim = req.get(key)
            val = met.get(mkey)
            br = met.get(bkey) if bkey else None
            if lim is None:
                unknown(f"{label}: no requirement declared")
            elif val is None:
                if bkey and covered >= lim:
                    # a miss computed on a model whose validity is open stays UNKNOWN (the reason is kept)
                    status = "INFEASIBLE" if status != "UNKNOWN" else status
                    reasons.append(f"{label}: not reached by {lim:g} s (horizon covers it)")
                else:
                    unknown(f"{label}: not reached within the horizon")
            elif br is not None and br[0] <= lim < br[1]:
                unknown(f"{label}: the output grid brackets it in [{br[0]:.4g}, {br[1]:.4g}] s around the limit "
                        f"{lim:g} s")
            elif (br[0] if br is not None else val) > lim:
                status = "INFEASIBLE" if status != "UNKNOWN" else status
                reasons.append(f"{label} {val:.4g} > {lim:g}")
        if man.emergency_t_s is not None:
            res["safety"] = _safety_reaction(sim, man, req.get("safety_reaction_max_s"),
                                             req.get("safety_band_Nm"))
            notes.append("a safety interruption is in the window: comfort metrics include it and are reported "
                         "separately from the protection time (never traded)")
        tr = getattr(np, "trapezoid", None) or np.trapz
        res["delivered_work_J"] = float(tr(sim["T_shaft"] * sim["omega_l"], sim["t_s"]))     # into the load side
        if loss_fn is not None:
            T = sim["T_act"]
            w = loss_fn(T, man.speed_rpm)
            if w is not None:
                res["loss_energy_J"] = float(tr(w, sim["t_s"]))
        res["status"] = status
        res["reasons"] = reasons
        res["notes"] = notes
        res["sensing"] = sens
        res["sim"] = {k: sim[k] for k in ("t_s", "omega_m", "omega_l", "T_act", "T_shaft", "acc_l", "jerk_l")}
        res["record"] = {k: v for k, v in sim["record"].items()}
        out[name] = res
    base = out.get("off")
    if base:
        for name, r in out.items():
            r["work_difference_J"] = r["delivered_work_J"] - base["delivered_work_J"]
            if r.get("loss_energy_J") is not None and base.get("loss_energy_J") is not None:
                r["extra_loss_energy_J"] = r["loss_energy_J"] - base["loss_energy_J"]
                if r["work_difference_J"] < -1e-6 * max(abs(base["delivered_work_J"]), 1.0):
                    r["loss_note"] = ("less loss in the window because less work is delivered (slower response): "
                                      "not an efficiency gain")
    return {"variants": out, "modal": dl.modal(), "referred": dl.referred(), "slew_limits": slew_limits,
            "meaning": "same maneuver and requirement for every variant; a delayed acceleration is not a jerk "
                       "improvement by itself; the linear model holds only while contact is maintained"}


def _safety_reaction(sim: dict, man: Maneuver, limit_s: float | None, band_Nm: float | None = None) -> dict:
    """Time from the safety request until the ACTUAL torque enters the declared band around the safe torque and
    stays there - from the exact actuator solution between command applications, independent of the output grid
    (review R2 CT-04).  First-order actuator ROM: T(t) = u + (T(t_i) - u) exp(-(t - t_i) / tau) on each command
    segment; without it the torque follows the commands.  An undeclared band never approves a reaction."""
    target = float(man.emergency_T_Nm)
    te = float(man.emergency_t_s)
    tau = float(sim.get("actuator_tau_s") or 0.0)
    t_end = float(sim["t_s"][-1])
    band = None if band_Nm is None else _finite("safety_band_Nm", band_Nm)
    b = band if band is not None else max(0.02 * abs(man.T1_Nm - target), 1.0)
    segs = [(ta, u, Tb) for (ta, u, Tb) in sim.get("applied", [])]
    # actuator torque at the request and the command in force then
    prior = [s_ for s_ in segs if s_[0] <= te]
    u_now = prior[-1][1] if prior else float(sim.get("T0_Nm", man.T0_Nm))
    t_i = prior[-1][0] if prior else 0.0
    T_i = prior[-1][2] if prior else float(sim.get("T0_Nm", man.T0_Nm))

    def value(t0, T0, u, t):
        return u if tau <= 0 else u + (T0 - u) * math.exp(-(t - t0) / tau)

    T_now = value(t_i, T_i, u_now, te) if prior else T_i
    pieces, t0, T0, u = [], te, T_now, u_now
    for (ta, un, _Tb) in [s_ for s_ in segs if te < s_[0] <= t_end]:
        pieces.append((t0, ta, T0, u))
        T0, t0, u = value(t0, T0, u, ta), ta, un
    pieces.append((t0, t_end, T0, u))
    last_out = None
    for (a_, b_, T0, u) in pieces:
        # |T - target| is monotone on each piece (exponential toward u): check the start and the end
        f0 = abs(value(a_, T0, u, a_) - target) > b
        f1 = abs(value(a_, T0, u, b_) - target) > b
        if f1:
            last_out = b_
        elif f0:
            if tau <= 0:
                exit_t = a_
            else:
                d0 = T0 - u
                lvl = [(target + s_ * b - u) / d0 for s_ in (1.0, -1.0) if d0 != 0]
                cand = [a_ - tau * math.log(x) for x in lvl if 0 < x < 1]
                cand = [c for c in cand if a_ <= c <= b_]
                exit_t = max(cand) if cand else a_
            last_out = exit_t
    settled_inside = abs(value(*pieces[-1][:1], pieces[-1][2], pieces[-1][3], t_end) - target) <= b
    if not settled_inside or last_out == t_end:
        t_react = None
    else:
        t_react = 0.0 if last_out is None else last_out - te
    if band is None:
        st, why = "UNKNOWN", "no safe-torque band declared (a heuristic band never approves a safety reaction)"
    elif limit_s is None:
        st, why = "UNKNOWN", "no protection (safety reaction) time declared"
    elif t_react is None:
        st, why = "INFEASIBLE", "the safe torque band is not reached for good within the horizon"
    elif t_react > limit_s:
        st, why = "INFEASIBLE", f"reaction {1e3 * t_react:.6g} ms > {1e3 * limit_s:.4g} ms"
    else:
        st, why = "FEASIBLE", f"reaction {1e3 * t_react:.6g} ms <= {1e3 * limit_s:.4g} ms"
    after = sim["t_s"] >= te
    jk = np.abs(sim["jerk_l"][after])
    return {"status": st, "reason": why, "reaction_s": t_react, "band_Nm": b, "band_declared": band is not None,
            "target_Nm": target, "limit_s": limit_s,
            "event_basis": "exact actuator solution between command applications (independent of the output grid)",
            "peak_load_jerk_during_reaction": float(jk.max()) if jk.size else None,
            "note": "comfort functions are bypassed during the reaction; its jerk is reported (output-grid maximum), "
                    "not traded"}
