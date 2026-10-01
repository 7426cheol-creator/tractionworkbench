"""Discrete field-oriented current control and the torque-command path, seen through the sensors only.

Command path.  The vehicle's torque request (the true intent) is sent every ``period_s`` and arrives ``latency_s``
later; the inverter uses the newest message.  Faults act on the path, not on the intent: ``stale`` (the sender
repeats the last value with a fresh message - no timeout), ``value`` / ``offset`` / ``sign_flip`` (a wrong payload),
``loss`` (no message: the message age grows and only a timeout can see it).

Controller.  At every sample instant (period T_s = 1 / (f_sw * updates per period)) it reads the current, position
and DC-voltage conversions, computes

    i_dq = Park(i_abc,meas, theta_meas)          (two-sensor reconstruction i_c = -i_a - i_b unless three are used)
    T*   = received command -> timeout ramp -> rate limit -> derating by the measured temperature -> table range
    i*   = the minimum-current policy point of T* at the operating speed / Vdc (the kernel's policy, tabulated)
    v    = PI(i* - i) + decoupling (design L_d, L_q, psi, estimated speed);  |v| <= k_v Vdc,meas / sqrt 3 with
           conditional integration while saturated (anti-windup)
    duty = shared modulation law at theta_meas + w_est (t_pos + angle_comp_periods T_s)   (delay compensation:
           the position sensor's nominal delay t_pos and the transport + modulator delay)

and the duties are applied at the next reload (one update period later).  A reaction strategy may put the
controller in a mode for one of its steps: ``torque`` (the command replaced by a value), ``torque_ramp`` (the
command ramped to zero at a rate), ``current_to_asc`` (the d/q references ramped from the last references to the
three-phase-short steady state of the ESTIMATED speed with the design parameters) and ``voltage_ramp`` (the last
voltage vector ramped to zero in the rotor frame, open loop); ``mode_done`` reports the mode's completion as the
software sees it (measured currents, its own references).  A stopped control task leaves the last
compare values in the PWM unit; an MCU in reset drives the declared reset state of the PWM outputs; after the boot
the software is halted until the reaction manager restarts it (flying start: the speed estimate is re-established
from two position readings before the first voltage is applied; cold start: the speed filter starts from zero).
The controller never reads the plant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ...errors import InputValidationError
from ...modulation import duties as _duties
from .sensors import unwrap_delta

SQ3 = math.sqrt(3.0)
COMMAND_FAULTS = ("stale", "value", "offset", "sign_flip", "loss", "oscillation")
RESTART_MODES = ("flying", "cold")


# --------------------------------------------------------------------------------------------------- command path

@dataclass
class CommandPath:
    period_s: float = 0.01
    latency_s: float = 0.0005
    fault: str | None = None
    value: float = 0.0
    t_fault: float = math.inf
    freq_Hz: float = 20.0

    def inject(self, t: float, mode: str, value: float = 0.0, freq_Hz: float = 20.0):
        if mode not in COMMAND_FAULTS:
            raise InputValidationError(f"command fault must be one of {COMMAND_FAULTS}", field="fault.mode")
        self.fault, self.value, self.t_fault, self.freq_Hz = mode, float(value), t, float(freq_Hz)

    def newest(self, t: float, request) -> tuple[float | None, float]:
        """(payload of the newest arrived message, its send time); None when no message ever arrived."""
        k = math.floor((t - self.latency_s) / self.period_s + 1e-9)
        if k < 0:
            return None, -math.inf
        if self.fault == "loss":
            k_last = math.ceil(self.t_fault / self.period_s - 1e-9) - 1        # last message sent before the fault
            k = min(k, k_last)
            if k < 0:
                return None, -math.inf
        t_send = k * self.period_s
        payload = request(t_send)
        if t_send >= self.t_fault - 1e-12:
            if self.fault == "stale":
                k_last = math.ceil(self.t_fault / self.period_s - 1e-9) - 1
                payload = request(max(0, k_last) * self.period_s)
            elif self.fault == "value":
                payload = self.value
            elif self.fault == "offset":
                payload = payload + self.value
            elif self.fault == "sign_flip":
                payload = -payload
            elif self.fault == "oscillation":
                payload = payload + self.value * math.sin(2.0 * math.pi * self.freq_Hz * (t_send - self.t_fault))
        return payload, t_send


# --------------------------------------------------------------------------------------------------- references

@dataclass
class ReferenceTable:
    """Minimum-current policy points (i_d, i_q) over torque at one speed and DC voltage (the kernel's policy)."""
    T: np.ndarray
    i_d: np.ndarray
    i_q: np.ndarray
    speed_rpm: float
    Vdc_V: float
    basis: str = ""

    @classmethod
    def build(cls, drive, scenario, n: int = 41) -> "ReferenceTable":
        from ...solvers.capability import policy_capability
        from ...solvers.policy import PolicyEvaluator
        ev = PolicyEvaluator(drive, scenario)
        hi = policy_capability(ev, 1).value_Nm
        lo = policy_capability(ev, -1).value_Nm
        if hi is None or lo is None:
            raise InputValidationError("no policy capability at this operating point (the controller has no current "
                                       "reference table)", field="operating_point")
        Ts, ids, iqs = [], [], []
        grid = sorted(set([float(v) for v in np.linspace(lo, hi, n)] + [0.0]))
        for T in grid:
            s = ev.solve(float(T))
            if s.point is not None:
                Ts.append(float(T))
                ids.append(float(s.point.id_A))
                iqs.append(float(s.point.iq_A))
        if len(Ts) < 2:
            raise InputValidationError("the policy table has fewer than two points", field="operating_point")
        return cls(np.array(Ts), np.array(ids), np.array(iqs), scenario.speed_rpm, scenario.Vdc_V,
                   f"minimum-current policy of the kernel at {scenario.speed_rpm:g} rpm, {scenario.Vdc_V:g} V "
                   f"({len(Ts)} points, {Ts[0]:.1f} .. {Ts[-1]:.1f} N*m)")

    def at(self, T: float) -> tuple[float, float, float]:
        Tc = min(max(T, float(self.T[0])), float(self.T[-1]))
        return float(np.interp(Tc, self.T, self.i_d)), float(np.interp(Tc, self.T, self.i_q)), Tc

    def to_dict(self) -> dict:
        return {"T": self.T.tolist(), "i_d": self.i_d.tolist(), "i_q": self.i_q.tolist(), "speed_rpm": self.speed_rpm,
                "Vdc_V": self.Vdc_V, "basis": self.basis}

    @classmethod
    def from_dict(cls, d: dict) -> "ReferenceTable":
        return cls(np.array(d["T"], float), np.array(d["i_d"], float), np.array(d["i_q"], float),
                   float(d["speed_rpm"]), float(d["Vdc_V"]), str(d.get("basis", "")))


# --------------------------------------------------------------------------------------------------- controller

@dataclass
class ControlConfig:
    fsw_Hz: float
    Kp_d: float
    Ki_d: float
    Kp_q: float
    Ki_q: float
    Ld: float                         # design values (decoupling, torque estimate)
    Lq: float
    psi: float
    Rs: float
    p: int
    updates_per_period: int = 1
    v_limit_frac: float = 1.0         # k_v: usable fraction of the linear limit Vdc / sqrt 3
    modulation: str = "svpwm"
    angle_comp_periods: float = 1.5   # angle advance for the transport + modulator delay (in update periods)
    angle_delay_comp_s: float = 0.0   # the position sensor's nominal delay the software compensates (design value)
    b_rot: float = 0.0                # design rotational loss torque b w + c w|w| (shaft torque from the air gap)
    c_rot: float = 0.0
    speed_tau_s: float = 0.5e-3       # speed estimate filter
    three_sensors: bool = False       # use all three current sensors (else i_c = -i_a - i_b)
    torque_rate_Nm_per_s: float | None = None
    derating: tuple = ()              # ((T_meas_C, torque_fraction), ...) by the measured temperature
    comm_timeout_s: float | None = 0.05
    timeout_ramp_Nm_per_s: float = 5000.0
    reset_output: str = "off"         # PWM outputs of an MCU in reset: off (pull-downs) | lower_on | upper_on
    boot_s: float = 0.02              # MCU reset -> software running again
    restart_mode: str = "flying"      # flying | cold
    restart_ramp_Nm_per_s: float | None = 50e3   # torque ramp after a restart (None: step to the command)
    basis: str = ""

    def __post_init__(self):
        if self.restart_mode not in RESTART_MODES:
            raise InputValidationError(f"restart mode must be one of {RESTART_MODES}", field="control.restart_mode")
        if self.reset_output not in ("off", "lower_on", "upper_on"):
            raise InputValidationError("reset output must be off, lower_on or upper_on", field="control.reset_output")

    @property
    def Ts(self) -> float:
        return 1.0 / (self.fsw_Hz * self.updates_per_period)


@dataclass
class ControlOutput:
    duties: tuple | None               # None: no new compare values (the PWM keeps the last ones)
    enabled: bool = True               # False: the controller asks for the gates off
    info: dict = field(default_factory=dict)


class Controller:
    def __init__(self, cfg: ControlConfig, table: ReferenceTable):
        self.cfg, self.table = cfg, table
        self.int_d = self.int_q = 0.0
        self.state = "run"             # run | frozen | reset | halted | starting | standby (software runs, no current
        #                                control: a passive operating state of the system layer)
        self.reset_until = -math.inf
        self.duties = (0.5, 0.5, 0.5)
        self.theta_prev: float | None = None
        self.w_est = 0.0
        self.T_used = 0.0
        self.timed_out = False
        self.ramping = False
        self.last: dict = {}
        self.mode: dict | None = None  # a reaction step's control mode (see the module note)
        self.mode_done = False

    def set_mode(self, mode: dict | None):
        """Enter (or leave, None) a reaction step's control mode; ``t0`` is the step's start."""
        self.mode = dict(mode) if mode else None
        self.mode_done = False

    def init_steady(self, i_d: float, i_q: float, w_e: float, theta_meas: float, T_ref: float):
        c = self.cfg
        # integrators hold the steady-state voltage less the decoupling feed-forward: the loop starts in equilibrium
        self.int_d = c.Rs * i_d
        self.int_q = c.Rs * i_q
        self.w_est = w_e
        self.theta_prev = None          # the first sample only stores the angle (no speed update from one reading)
        self.T_used = T_ref

    def restart(self, mode: str | None = None):
        """The software starts the current control again (after a reset or a recovery)."""
        mode = mode or self.cfg.restart_mode
        self.int_d = self.int_q = 0.0
        self.T_used = 0.0
        self.timed_out = False
        self.theta_prev = None
        self.ramping = self.cfg.restart_ramp_Nm_per_s is not None
        if mode == "cold":
            self.w_est = 0.0
            self.state = "run"
        else:
            self.state = "starting"     # one sample to re-establish the speed from two position readings

    def torque_estimate(self, i_d: float, i_q: float) -> float:
        c = self.cfg
        return 1.5 * c.p * (c.psi * i_q + (c.Ld - c.Lq) * i_d * i_q)

    def step(self, t: float, i_meas: tuple, theta_meas: float, vdc_meas: float, temp_meas: float | None,
             T_cmd: float | None, cmd_age_s: float) -> ControlOutput:
        c = self.cfg
        if self.state == "frozen":
            return ControlOutput(None, True, {"state": "frozen"})
        if self.state == "reset":
            if t < self.reset_until:
                return ControlOutput(None, False, {"state": "reset"})
            self.state = "halted"      # booted: the software decides (the reaction manager restarts or latches)
        if self.state in ("halted", "standby"):
            return ControlOutput(None, False, {"state": self.state})
        Ts = c.Ts
        if self.state == "starting":
            if self.theta_prev is None:
                self.theta_prev = theta_meas
                return ControlOutput(None, False, {"state": "starting"})
            self.w_est = unwrap_delta(theta_meas, self.theta_prev) / Ts
            self.state = "run"
        elif self.theta_prev is not None:
            w_raw = unwrap_delta(theta_meas, self.theta_prev) / Ts
            a = Ts / (c.speed_tau_s + Ts)
            self.w_est += a * (w_raw - self.w_est)
        self.theta_prev = theta_meas
        ia, ib = i_meas[0], i_meas[1]
        ic = i_meas[2] if c.three_sensors else -ia - ib
        th = theta_meas + self.w_est * c.angle_delay_comp_s      # the sensor's nominal delay, compensated
        cs, sn = math.cos(th), math.sin(th)
        i_al = (2.0 * ia - ib - ic) / 3.0
        i_be = (ib - ic) / SQ3
        i_d = i_al * cs + i_be * sn
        i_q = -i_al * sn + i_be * cs
        # torque command: timeout degradation, rate limit, derating
        T_req = T_cmd if T_cmd is not None else 0.0
        if c.comm_timeout_s is not None:
            if cmd_age_s > c.comm_timeout_s:
                self.timed_out = True
            elif self.timed_out:
                self.timed_out = False
        if self.timed_out:
            step = c.timeout_ramp_Nm_per_s * Ts
            T_req = max(0.0, self.T_used - step) if self.T_used > 0 else min(0.0, self.T_used + step)
        elif self.ramping:
            step = c.restart_ramp_Nm_per_s * Ts
            T_new = min(max(T_req, self.T_used - step), self.T_used + step)
            if T_new == T_req:
                self.ramping = False
            T_req = T_new
        elif c.torque_rate_Nm_per_s:
            step = c.torque_rate_Nm_per_s * Ts
            T_req = min(max(T_req, self.T_used - step), self.T_used + step)
        if c.derating and temp_meas is not None:
            frac = float(np.interp(temp_meas, [a for a, _ in c.derating], [b for _, b in c.derating]))
            T_req = max(min(T_req, frac * float(self.table.T[-1])), frac * float(self.table.T[0]))
        md = self.mode
        kind = md.get("kind") if md else None
        if kind == "torque":
            T_req = float(md.get("value", 0.0))
            self.mode_done = True
        elif kind == "torque_ramp":
            step = float(md["rate_Nm_per_s"]) * Ts
            T_req = max(0.0, self.T_used - step) if self.T_used > 0 else min(0.0, self.T_used + step)
        id_ref, iq_ref, T_used = self.table.at(T_req)
        if kind == "torque_ramp":
            self.mode_done = abs(T_req) <= 1e-9
        w_e = self.w_est
        if kind == "current_to_asc":
            # the references move from the last ones to the short-circuit steady state of the estimated speed
            if "i0" not in md:
                md["i0"] = (self.last.get("id_ref", i_d), self.last.get("iq_ref", i_q))
                from .strategy import asc_steady_point
                md["target"] = asc_steady_point(w_e, c.Ld, c.Lq, c.psi, c.Rs)
            s = min(1.0, max(0.0, (t - float(md["t0"])) / max(float(md["ramp_s"]), 1e-12)))
            (d0, q0), (dt_, qt_) = md["i0"], md["target"]
            id_ref, iq_ref = d0 + s * (dt_ - d0), q0 + s * (qt_ - q0)
            T_used = self.torque_estimate(id_ref, iq_ref)
            self.mode_done = s >= 1.0 and math.hypot(i_d - dt_, i_q - qt_) <= float(md.get("tol_A", 40.0))
        self.T_used = T_used
        # PI + decoupling
        ed, eq = id_ref - i_d, iq_ref - i_q
        ff_d = -w_e * c.Lq * i_q
        ff_q = w_e * (c.Ld * i_d + c.psi)
        vd = c.Kp_d * ed + self.int_d + ff_d
        vq = c.Kp_q * eq + self.int_q + ff_q
        vmax = c.v_limit_frac * max(vdc_meas, 1e-9) / SQ3
        if kind == "voltage_ramp":
            # open loop: the last applied voltage vector scaled down to the zero vector (the integrators hold)
            if "v0" not in md:
                md["v0"] = (self.last.get("vd", vd), self.last.get("vq", vq))
            s = min(1.0, max(0.0, (t - float(md["t0"])) / max(float(md["ramp_s"]), 1e-12)))
            vd, vq = (1.0 - s) * md["v0"][0], (1.0 - s) * md["v0"][1]
            self.mode_done = s >= 1.0
        mag = math.hypot(vd, vq)
        sat = mag > vmax
        if sat:
            vd, vq = vd * vmax / mag, vq * vmax / mag
        elif kind != "voltage_ramp":
            self.int_d += c.Ki_d * Ts * ed
            self.int_q += c.Ki_q * Ts * eq
        # to the stator frame with delay compensation, then the shared modulation law
        th_out = th + w_e * c.angle_comp_periods * Ts
        v_al = vd * math.cos(th_out) - vq * math.sin(th_out)
        v_be = vd * math.sin(th_out) + vq * math.cos(th_out)
        m = math.hypot(v_al, v_be) / (0.5 * max(vdc_meas, 1e-9))
        alpha = math.atan2(v_be, v_al)
        d = _duties(np.array([0.0]), m, alpha, c.modulation)[:, 0]
        self.duties = tuple(float(min(1.0, max(0.0, x))) for x in d)
        self.last = {"i_d": i_d, "i_q": i_q, "id_ref": id_ref, "iq_ref": iq_ref, "T_used": T_used,
                     "T_est": self.torque_estimate(i_d, i_q), "w_est": w_e, "saturated": sat, "vd": vd, "vq": vq}
        return ControlOutput(self.duties, True, dict(self.last, state="run"))
