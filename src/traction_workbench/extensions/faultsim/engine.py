"""Causal fault simulation: fault -> measurements -> control and monitoring -> reaction -> bridge -> plant.

One trajectory, one clock.  Between discrete instants (faults, reaction actuations, PWM reloads and - in the switched
model - carrier edges, ADC conversions with the control step, monitor task activations, watchdog expiry, the
hardware filters) the plant is integrated with its located events (diode mode changes, the battery branch, and the
continuous hardware mechanisms: comparators on analog sensor outputs, desaturation on device currents, the battery
management's charge-current limit).  At a discrete instant the parts act in a fixed order: faults, plant-side
actions (contactor, driver turn-off, reports), actuations, PWM reload, conversion + control, monitors, filters,
watchdog.  Every consumer reads sensors; nothing downstream of a sensor reads the plant.

Who commands the bridge.  A hardware path (e.g. a logic device forcing the gates) overrides the MCU; an MCU in reset
drives the declared reset state of its PWM outputs; otherwise the software reaction, if any, else the controller's
PWM (or gates off while the software is halted).  The commanded state is not the actual state: gate supplies,
desaturation latches, open and shorted devices decide what each leg does, and the plant computes that.

PWM.  ``averaged``: each update interval applies the period-averaged leg of its duty (dead time as the current-sign
dependent part).  ``switched``: a center-aligned carrier produces the actual gate sequence with dead time inserted at
every turn-on (lower on - off - upper on - off - lower on), so the plant sees the instantaneous bridge (current
ripple, the diode conduction in the dead time, comparators on ripple peaks).  Duties computed at a sample are loaded
at the next reload (one update period later).

The result keeps apart: the plant truth (currents, torque, DC voltage), what control and monitors measured and
estimated, the commands (duties, the reaction manager's command) and the actual bridge state.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

from ...errors import InputValidationError
from ...modulation import duties as _duties
from .control import CommandPath, ControlConfig, Controller, ReferenceTable
from .plant import (E_BAT, E_BLEED, E_CU, E_INV, E_MECH, IBAT, ID, IQ, PHASES, TH, VDC, WM, DcParams, ExternalEvent,
                    LegCommand, MachineParams, OutOfModel, Plant, ShootThrough, integrate, phase_currents)
from .protection import (BRIDGE_REACTIONS, Detection, MechanismSpec, MechanismState, PathSpec, ResourceBook,
                         SafeStatePolicy, torque_window)
from .sensors import SensorSpec, make_sensor, unwrap_delta

TWO_PI = 2.0 * math.pi
PWM_MODELS = ("averaged", "switched")
ROLE_DEFAULTS = {"current_hw_a": "current_a", "current_hw_b": "current_b", "current_hw_c": "current_c",
                 "current_mon_a": "current_a", "current_mon_b": "current_b", "current_mon_c": "current_c",
                 "position_monitor": "position_control", "vdc_monitor": "vdc_control", "vdc_hw": "vdc_control"}
REQUIRED_ROLES = ("current_a", "current_b", "current_c", "position_control", "vdc_control")
BRIDGE_CODES = {"pwm": 0, "asc_low": 1, "asc_high": 2, "six_switch_off": 3, "off": 4}

# fault kinds: description and parameters (name, kind, choices / default) - the UI builds its editors from this
FAULT_KINDS = {
    "sensor": ("a sensor's reading (offset / gain / stuck / stuck_last / lost / delay)",
               (("target", "sensor", None), ("mode", ("offset", "gain", "stuck", "stuck_last", "lost", "delay"),
                                              "offset"), ("value", "float", 0.0))),
    "torque_command": ("the torque command message (stale / value / offset / sign_flip / loss) on the control's "
                       "message, the monitor's copy or both (a common source, e.g. the sender)",
                       (("mode", ("stale", "value", "offset", "sign_flip", "loss"), "value"), ("value", "float", 0.0),
                        ("paths", ("control", "monitor", "both"), "control"))),
    "control_task_stop": ("the control task stops (the PWM unit keeps its last compare values)", ()),
    "mcu_reset": ("the MCU resets (PWM outputs take their reset state; software halted until booted)",
                  (("duration_s", "float", 0.005),)),
    "pwm_output": ("one leg's PWM output fails (stuck duty / off / upper_on / lower_on)",
                   (("leg", ("a", "b", "c"), "a"), ("mode", ("stuck_duty", "off", "upper_on", "lower_on"), "off"),
                    ("value", "float", 0.5))),
    "switch_open": ("a power switch fails open", (("leg", ("a", "b", "c"), "a"), ("device", ("upper", "lower"), "upper"))),
    "switch_short": ("a power switch fails short", (("leg", ("a", "b", "c"), "a"),
                                                   ("device", ("upper", "lower"), "upper"))),
    "diode_open": ("a freewheeling diode fails open", (("leg", ("a", "b", "c"), "a"),
                                                      ("device", ("upper", "lower"), "upper"))),
    "phase_open": ("a motor phase connection opens", (("phase", ("a", "b", "c"), "a"),)),
    "gate_supply_loss": ("the gate-driver supply of one side is lost (resource GATE_UPPER / GATE_LOWER)",
                         (("side", ("upper", "lower", "both"), "lower"),)),
    "battery_disconnect": ("the main contactor opens", ()),
    "contactor_stuck": ("the main contactor does not open when commanded (welded)", ()),
    "charge_acceptance_loss": ("the battery stops accepting charge: the BMS charge-current limit drops",
                               (("limit_A", "float", 0.0),)),
    "mechanism_disabled": ("a safety mechanism is silently inactive (latent fault)", (("mechanism", "mechanism", None),)),
    "path_lost": ("a reaction path cannot actuate (latent fault)", (("path", "path", None),)),
    "resource_loss": ("a shared resource is lost (every sensor, mechanism and path that needs it)",
                      (("resource", "resource", None),)),
}


@dataclass(frozen=True)
class FaultSpec:
    kind: str
    t_s: float
    params: dict = field(default_factory=dict)
    label: str = ""

    def __post_init__(self):
        if self.kind not in FAULT_KINDS:
            raise InputValidationError(f"fault kind must be one of {list(FAULT_KINDS)}", field="faults.kind")
        if not (math.isfinite(self.t_s) and self.t_s >= 0):
            raise InputValidationError("a fault time must be finite and >= 0", field="faults.t_s")

    def text(self) -> str:
        if self.label:
            return self.label
        p = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.kind}" + (f" ({p})" if p else "")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "t_s": self.t_s, "params": dict(self.params), "label": self.label}


@dataclass(frozen=True)
class RequestProfile:
    """The vehicle's torque request (the true intent): constant, step, ramp or a table (linear)."""
    kind: str = "constant"
    T0_Nm: float = 0.0
    T1_Nm: float = 0.0
    t0_s: float = 0.0
    t1_s: float = 0.0
    table: tuple = ()

    def __post_init__(self):
        if self.kind not in ("constant", "step", "ramp", "table"):
            raise InputValidationError("request kind must be constant, step, ramp or table", field="request.kind")
        if self.kind == "ramp" and not self.t1_s > self.t0_s:
            raise InputValidationError("a ramp needs t1 > t0", field="request.t1_s")
        if self.kind == "table" and len(self.table) < 2:
            raise InputValidationError("a request table needs >= 2 points", field="request.table")

    def __call__(self, t: float) -> float:
        k = self.kind
        if k == "constant":
            return self.T0_Nm
        if k == "step":
            return self.T0_Nm if t < self.t0_s else self.T1_Nm
        if k == "ramp":
            if t <= self.t0_s:
                return self.T0_Nm
            if t >= self.t1_s:
                return self.T1_Nm
            return self.T0_Nm + (self.T1_Nm - self.T0_Nm) * (t - self.t0_s) / (self.t1_s - self.t0_s)
        ts = [p[0] for p in self.table]
        return float(np.interp(t, ts, [p[1] for p in self.table]))

    def breakpoints(self) -> list:
        if self.kind == "step":
            return [self.t0_s]
        if self.kind == "ramp":
            return [self.t0_s, self.t1_s]
        if self.kind == "table":
            return [p[0] for p in self.table]
        return []

    def to_dict(self) -> dict:
        return {"kind": self.kind, "T0_Nm": self.T0_Nm, "T1_Nm": self.T1_Nm, "t0_s": self.t0_s, "t1_s": self.t1_s,
                "table": [list(p) for p in self.table]}


@dataclass
class SimSetup:
    machine: MachineParams
    dc: DcParams
    control: ControlConfig
    table: ReferenceTable
    sensors: tuple                     # SensorSpec
    roles: dict                        # role -> sensor name (current_a/b/c, position_control, vdc_control, ...)
    mechanisms: tuple                  # MechanismSpec
    paths: dict                        # path_id -> PathSpec
    policy: SafeStatePolicy
    command_period_s: float
    command_latency_s: float
    request: RequestProfile
    speed_rpm: float
    horizon_s: float
    faults: tuple = ()
    theta0: float = 0.0
    pwm_model: str = "averaged"
    deadtime_s: float = 0.0
    h_max_s: float = 10e-6
    temperature_C: float | None = None  # the true temperature a temperature sensor sees (constant over the horizon)
    protection_enabled: bool = True
    reaction_override: str | None = None   # force every bridge-level reaction (candidate comparison)
    bms: dict | None = None            # {"charge_current_max_A", "delay_s"}: the battery opens its contactor
    active_discharge_delay_s: float | None = None   # contactor opened -> active discharge on after this delay
    identity: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.pwm_model not in PWM_MODELS:
            raise InputValidationError(f"PWM model must be one of {PWM_MODELS}", field="pwm_model")
        if not (self.horizon_s > 0 and math.isfinite(self.horizon_s)):
            raise InputValidationError("the horizon must be > 0", field="horizon_s")
        if not (0 < self.h_max_s <= 1e-4):
            raise InputValidationError("the maximum step must be in (0, 100 us]", field="h_max_s")
        names = {s.name for s in self.sensors}
        for r in REQUIRED_ROLES:
            if self.roles.get(r) not in names:
                raise InputValidationError(f"the sensor role {r} needs a declared sensor", field=f"roles.{r}")
        for r, n in self.roles.items():
            if n not in names:
                raise InputValidationError(f"role {r} names an undeclared sensor {n}", field=f"roles.{r}")
        for m in self.mechanisms:
            if m.path not in self.paths:
                raise InputValidationError(f"mechanism {m.mech_id} uses the undeclared path {m.path}",
                                           field=f"mechanisms.{m.mech_id}.path")
        if self.reaction_override is not None and self.reaction_override not in BRIDGE_REACTIONS + ("torque_zero",):
            raise InputValidationError(f"reaction override must be one of {BRIDGE_REACTIONS + ('torque_zero',)}",
                                       field="reaction_override")
        if self.control.updates_per_period not in (1, 2):
            raise InputValidationError("updates per PWM period must be 1 or 2", field="control.updates_per_period")

    def role(self, r: str) -> str:
        return self.roles.get(r) or self.roles.get(ROLE_DEFAULTS.get(r, ""), "")


@dataclass
class SimResult:
    trace: dict
    events: list
    status: str                        # completed | stopped_out_of_model
    stop_reason: str | None
    summary: dict
    setup_echo: dict

    def to_dict(self, with_trace: bool = True) -> dict:
        out = {"events": self.events, "status": self.status, "stop_reason": self.stop_reason,
               "summary": self.summary, "setup": self.setup_echo}
        if with_trace:
            out["trace"] = {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in self.trace.items()}
        return out


# ------------------------------------------------------------------------------------------------------- the PWM unit

class PwmUnit:
    """Compare values and the gate sequence of the three legs (center-aligned carrier, reload at the valley and, with
    two updates per period, at the peak; dead time inserted at every turn-on)."""

    def __init__(self, fsw: float, upp: int, deadtime_s: float, model: str):
        self.T = 1.0 / fsw
        self.upp, self.tdt, self.model = upp, deadtime_s, model
        self.duty = [0.5, 0.5, 0.5]
        self.ideal = ["lower", "lower", "lower"]   # the modulator's ideal state per leg (switched model)
        self.edges: list = []                        # (t, leg, state) of the actual gate sequence
        self.state = ["lower_on", "lower_on", "lower_on"]

    def load(self, t: float, duties):
        """Reload at an update instant: the averaged model takes the duties; the switched model generates the gate
        edges of the interval [t, t + T / upp)."""
        self.duty = [float(min(1.0, max(0.0, d))) for d in duties]
        if self.model != "switched":
            return
        T, half = self.T, 0.5 * self.T
        pos = (t / T) % 1.0
        rising = pos < 0.25 or pos > 0.999999                 # a reload at the valley starts the counter's up-slope
        segs = [(t, "up"), (t + half, "down")] if self.upp == 1 else [(t, "up" if rising else "down")]
        new = []
        for k in range(3):
            d = self.duty[k]
            trans = []
            for ts, slope in segs:
                if slope == "up":        # counter 0 -> 1: upper wanted once the carrier exceeds 1 - d
                    te = ts + (1.0 - d) * half
                    want = "upper" if d > 0.0 else "lower"
                    at = ts if d >= 1.0 else te
                else:                    # counter 1 -> 0: upper wanted while the carrier exceeds 1 - d
                    te = ts + d * half
                    want = "lower" if d < 1.0 else "upper"
                    at = ts if d <= 0.0 else te
                if want != (trans[-1][1] if trans else self.ideal[k]):
                    trans.append((at, want))
            # actual sequence with dead time: an ideal change turns the conducting switch off at once, the other
            # switch on t_dt later (unless the ideal state changes again before that)
            for i, (te, want) in enumerate(trans):
                new.append((te, k, "off"))
                t_on = te + self.tdt
                nxt = trans[i + 1][0] if i + 1 < len(trans) else math.inf
                if t_on < nxt:
                    new.append((t_on, k, "upper_on" if want == "upper" else "lower_on"))
            if trans:
                self.ideal[k] = trans[-1][1]
        # a dead-time completion of the previous interval still pending is kept unless a new transition of the same
        # leg comes first (the new ideal state supersedes it)
        kept = []
        for e in self.edges:
            first_new = min((n[0] for n in new if n[1] == e[1]), default=math.inf)
            if e[0] >= t - 1e-15 and e[0] < first_new:
                kept.append(e)
        self.edges = sorted(kept + new)

    def next_edge(self, t: float) -> float:
        for e in self.edges:
            if e[0] > t + 1e-15:
                return e[0]
        return math.inf

    def apply_edges(self, t: float) -> bool:
        changed = False
        keep = []
        for e in self.edges:
            if e[0] <= t + 1e-15:
                self.state[e[1]] = e[2]
                changed = True
            else:
                keep.append(e)
        self.edges = keep
        return changed

    def leg_command(self, k: int) -> LegCommand:
        if self.model == "switched":
            return LegCommand(self.state[k])
        return LegCommand("pwm", self.duty[k])


# ------------------------------------------------------------------------------------------------------- the run

class _History:
    """The plant state at every accepted step (a transport delay reads the truth at t - delay by interpolation;
    the electrical angle is interpolated unwrapped)."""

    def __init__(self, span_s: float = 0.2):
        self.t: list = []
        self.x: list = []
        self.span = span_s

    def add(self, t, x):
        if self.t and t <= self.t[-1] + 1e-15:
            self.t[-1], self.x[-1] = t, list(x)
        else:
            self.t.append(t)
            self.x.append(list(x))
        if len(self.t) > 4096 and self.t[-1] - self.t[0] > self.span:
            cut = len(self.t) // 2
            del self.t[:cut]
            del self.x[:cut]

    def at(self, tq):
        import bisect
        if not self.t or tq <= self.t[0]:
            return self.x[0] if self.x else None
        if tq >= self.t[-1]:
            return self.x[-1]
        i = bisect.bisect_right(self.t, tq)
        t0, t1 = self.t[i - 1], self.t[i]
        a = (tq - t0) / (t1 - t0) if t1 > t0 else 0.0
        x0, x1 = self.x[i - 1], self.x[i]
        out = [u + a * (v - u) for u, v in zip(x0, x1)]
        return out


class _Sim:
    def __init__(self, s: SimSetup):
        self.s = s
        self.dc = DcParams(**s.dc.__dict__)                        # the run's own copy (faults change it)
        self.plant = Plant(s.machine, self.dc, s.deadtime_s * s.control.fsw_Hz)
        self.ctrl = Controller(s.control, s.table)
        self.cmd_path = CommandPath(s.command_period_s, s.command_latency_s)
        self.mon_cmd_path = CommandPath(s.command_period_s, s.command_latency_s)     # the monitor's own message
        self.res = ResourceBook()
        self.sens = {sp.name: make_sensor(sp) for sp in s.sensors}
        self.mechs = [MechanismState(m) for m in s.mechanisms if m.enabled]
        self.pwm = PwmUnit(s.control.fsw_Hz, s.control.updates_per_period, s.deadtime_s, s.pwm_model)
        self.hist = _History()
        self.events: list = []
        self.detections: list = []
        self.heap: list = []
        self.seq = 0
        self.paths_lost: dict = {}
        self.pwm_fault: dict = {}
        self.hw_reaction: str | None = None
        self.sw_reaction: str | None = None
        self.reaction_by: list = []                  # mechanisms whose trip caused the active reaction
        self.torque_override: float | None = None
        self.task_stopped = False
        self.mcu_state = "run"                        # run | reset | halted
        self.last_alive = 0.0
        self.wd_token = 0
        self.recovery_attempts = 0
        self.contactor_welded = False
        self.bms_limit = None if s.bms is None else float(s.bms["charge_current_max_A"])
        self.bms_since = None                         # the charging current has been above the limit since
        self.bms_token = 0
        self.uvlo_sides: set = set()
        self.last_meas = {"speed_rpm": s.speed_rpm, "vdc_V": None}
        self.last_cmd, self.cmd_sent = None, None
        self.mon_view = {"T_est": math.nan, "lo": math.nan, "hi": math.nan}
        self.stop_reason = None
        self.bridge_cmd = "pwm"
        self.last_rule = None                         # the safe-state rule of the previous decision (hysteresis)

    # -- helpers ---------------------------------------------------------------------------------------------
    def sensor(self, role: str):
        return self.sens[self.s.role(role)]

    def ev(self, t, kind, source, text, **kw):
        self.events.append({"t": t, "kind": kind, "source": source, "text": text, **kw})

    def schedule(self, t, order, kind, payload=None):
        self.seq += 1
        heapq.heappush(self.heap, (t, order, self.seq, kind, payload))

    def truth_currents(self, x):
        return phase_currents(x[ID], x[IQ], x[TH])

    # -- faults ----------------------------------------------------------------------------------------------
    def apply_fault(self, t, x, f: FaultSpec):
        p, pl, k = f.params, self.plant, f.kind
        x = list(x)
        if k == "sensor":
            name = p.get("target") or p.get("sensor")
            if name not in self.sens:
                name = self.s.role(str(name))
            if name not in self.sens:
                raise InputValidationError(f"sensor fault on an undeclared sensor/role {p.get('target')}",
                                           field="faults.params.target")
            sen = self.sens[name]
            sen.inject(t, p.get("mode", "offset"), float(p.get("value", 0.0)), self._truth_for(sen, x))
        elif k == "torque_command":
            which = p.get("paths", "control")
            if which not in ("control", "monitor", "both"):
                raise InputValidationError("torque command fault paths must be control, monitor or both",
                                           field="faults.params.paths")
            if which in ("control", "both"):
                self.cmd_path.inject(t, p.get("mode", "value"), float(p.get("value", 0.0)))
            if which in ("monitor", "both"):
                self.mon_cmd_path.inject(t, p.get("mode", "value"), float(p.get("value", 0.0)))
        elif k == "control_task_stop":
            self.ctrl.state = "frozen"
            self.task_stopped = True
        elif k == "mcu_reset":
            self._mcu_reset(t, float(p.get("duration_s", 0.005)))
        elif k == "pwm_output":
            leg = PHASES.index(p.get("leg", "a"))
            self.pwm_fault[leg] = (p.get("mode", "off"), float(p.get("value", 0.5)))
        elif k in ("switch_open", "switch_short"):
            leg = PHASES.index(p.get("leg", "a"))
            setattr(pl.health[leg], f"{p.get('device', 'upper')}_switch", "open" if k == "switch_open" else "short")
        elif k == "diode_open":
            leg = PHASES.index(p.get("leg", "a"))
            setattr(pl.health[leg], f"{p.get('device', 'upper')}_diode", "open")
        elif k == "phase_open":
            pl.health[PHASES.index(p.get("phase", "a"))].phase_open = True
        elif k == "gate_supply_loss":
            side = p.get("side", "lower")
            for sd in (("upper", "lower") if side == "both" else (side,)):
                self._lose_resource(t, x, f"GATE_{sd.upper()}")
        elif k == "battery_disconnect":
            x = self._open_contactor(t, x, "fault: the contactor opened", forced=True)
        elif k == "contactor_stuck":
            self.contactor_welded = True
        elif k == "charge_acceptance_loss":
            self.bms_limit = float(p.get("limit_A", 0.0))
            if self.s.bms is None:
                self.ev(t, "note", "BMS", "no battery management declared: the lost charge acceptance is not enforced "
                                          "(the battery keeps taking the charge)")
        elif k == "mechanism_disabled":
            found = False
            for m in self.mechs:
                if m.spec.mech_id == p.get("mechanism"):
                    m.disabled_reason = f"latent fault at {t * 1e3:.4g} ms"
                    found = True
            if not found:
                raise InputValidationError(f"no enabled mechanism {p.get('mechanism')}", field="faults.params.mechanism")
        elif k == "path_lost":
            if p.get("path") not in self.s.paths:
                raise InputValidationError(f"no path {p.get('path')}", field="faults.params.path")
            self.paths_lost[p.get("path")] = t
        elif k == "resource_loss":
            self._lose_resource(t, x, str(p.get("resource")))
        self.ev(t, "fault", f.kind, f.text())
        dur = p.get("duration_s")
        if dur is not None and k not in ("mcu_reset", "switch_short", "switch_open", "diode_open", "phase_open"):
            self.schedule(t + float(dur), 0, "fault_clear", f)
        return x

    def clear_fault(self, t, x, f: FaultSpec):
        """An intermittent fault disappears (the part works again; latched consequences stay)."""
        p, k = f.params, f.kind
        if k == "sensor":
            name = p.get("target") or p.get("sensor")
            if name not in self.sens:
                name = self.s.role(str(name))
            sen = self.sens[name]
            sen.mode, sen.value, sen.frozen, sen.lost_since = None, 0.0, None, None
        elif k == "torque_command":
            which = p.get("paths", "control")
            if which in ("control", "both"):
                self.cmd_path.fault = None
            if which in ("monitor", "both"):
                self.mon_cmd_path.fault = None
        elif k == "control_task_stop":
            if self.ctrl.state == "frozen":
                self.ctrl.state = "run"
            self.task_stopped = False
        elif k == "pwm_output":
            self.pwm_fault.pop(PHASES.index(p.get("leg", "a")), None)
            if self.bridge_cmd == "pwm":
                self.apply_leg_commands()
        elif k == "gate_supply_loss":
            side = p.get("side", "lower")
            for sd in (("upper", "lower") if side == "both" else (side,)):
                self.res.restore(f"GATE_{sd.upper()}")
                for h in self.plant.health:
                    setattr(h, f"{sd}_gate", True)
        elif k == "resource_loss":
            self.res.restore(str(p.get("resource")))
        elif k == "path_lost":
            self.paths_lost.pop(p.get("path"), None)
        elif k == "mechanism_disabled":
            for m in self.mechs:
                if m.spec.mech_id == p.get("mechanism"):
                    m.disabled_reason = None
        self.ev(t, "fault_cleared", f.kind, f"{f.text()} cleared (intermittent fault)")

    def _truth_for(self, sen, x):
        name = sen.spec.name
        for ph in range(3):
            for r in (f"current_{PHASES[ph]}", f"current_hw_{PHASES[ph]}", f"current_mon_{PHASES[ph]}"):
                if self.s.role(r) == name:
                    return self.truth_currents(x)[ph]
        if sen.spec.kind == "position":
            return x[TH]
        if sen.spec.kind == "voltage":
            return x[VDC]
        if sen.spec.kind == "temperature":
            return self.s.temperature_C or 0.0
        return 0.0

    def _lose_resource(self, t, x, r: str):
        if r in self.res.lost:
            return
        self.res.lose(r, t)
        for obj in self.sens.values():
            if r in obj.spec.resources:
                obj.lose(t, self._truth_for(obj, x))
        if r in ("GATE_UPPER", "GATE_LOWER"):
            side = r[5:].lower()
            for h in self.plant.health:
                setattr(h, f"{side}_gate", False)
            for m in self.mechs:
                if m.spec.kind == "gate_uvlo" and not m.disabled_reason and self.res.ok(m.spec.resources):
                    self.schedule(t + float(m.spec.params.get("delay_s", 2e-6)), 1, "uvlo_report", (m, side))
        if r == "MCU" and self.mcu_state != "reset":
            self._mcu_reset(t, math.inf)

    # -- MCU, contactor ----------------------------------------------------------------------------------------
    def _mcu_reset(self, t, duration):
        # RAM is lost: the software mechanisms' debounce counters, trips and reference histories start again
        for m in self.mechs:
            if "MCU" in m.spec.resources and not m.spec.hardware:
                m.count_s, m.tripped, m.t_trip, m.cleared_since = 0.0, False, None, None
                m.lag_ref, m.ref_hist = None, []
        self.mcu_state = "reset"
        self.ctrl.state = "reset"
        self.ctrl.reset_until = t + max(duration, self.s.control.boot_s)
        self.res.lose("MCU", t)
        self.sw_reaction, self.torque_override = None, None          # RAM cleared: the software reaction is gone
        if math.isfinite(duration):
            self.schedule(t + max(duration, self.s.control.boot_s), 1, "mcu_boot", None)
        self.ev(t, "controller", "MCU", f"MCU reset: PWM outputs take their reset state "
                                        f"({self.s.control.reset_output})")
        self.update_bridge(t, "MCU reset state of the PWM outputs")

    def _open_contactor(self, t, x, why, forced=False):
        if not self.dc.contactor_closed:
            return x
        if self.contactor_welded and not forced:
            self.ev(t, "plant", "contactor", f"contactor commanded open ({why}) but it is welded: stays closed")
            return x
        x = self.plant.open_contactor(x)
        self.ev(t, "plant", "contactor", f"main contactor open ({why})")
        if self.s.active_discharge_delay_s is not None and self.dc.R_active:
            self.schedule(t + self.s.active_discharge_delay_s, 1, "active_discharge", None)
        return x

    # -- bridge ----------------------------------------------------------------------------------------------
    def update_bridge(self, t, why):
        """Resolve who commands the bridge and apply it to the legs."""
        if self.hw_reaction is not None:
            cmd = self.hw_reaction
        elif self.mcu_state == "reset":
            cmd = {"off": "six_switch_off", "lower_on": "asc_low", "upper_on": "asc_high"}[self.s.control.reset_output]
        elif self.sw_reaction is not None:
            cmd = self.sw_reaction
        elif self.mcu_state == "halted" or self.ctrl.state in ("halted", "starting"):
            cmd = "six_switch_off"
        else:
            cmd = "pwm"
        changed = cmd != self.bridge_cmd
        self.bridge_cmd = cmd
        self.apply_leg_commands()
        if changed:
            self.ev(t, "bridge", "bridge", f"bridge commanded {cmd} ({why})", command=cmd,
                    actual=self.actual_bridge())
        return changed

    def apply_leg_commands(self):
        pl = self.plant
        for k in range(3):
            if self.bridge_cmd == "pwm":
                c = self.pwm.leg_command(k)
                if k in self.pwm_fault:
                    md, val = self.pwm_fault[k]
                    c = LegCommand("pwm", val) if md == "stuck_duty" else LegCommand(md)
                    if md == "stuck_duty" and self.s.pwm_model == "switched":
                        c = LegCommand("upper_on" if val >= 0.5 else "lower_on")   # a stuck compare as a level
            elif self.bridge_cmd == "asc_low":
                c = LegCommand("lower_on")
            elif self.bridge_cmd == "asc_high":
                c = LegCommand("upper_on")
            else:
                c = LegCommand("off")
            pl.cmd[k] = c

    def actual_bridge(self) -> list:
        """What each leg actually can do (commanded state limited by device health and gate supplies)."""
        out = []
        for k in range(3):
            h, c = self.plant.health[k], self.plant.cmd[k]
            if h.phase_open:
                out.append("phase open")
                continue
            parts = [c.label()]
            if h.upper_switch != "ok" or h.lower_switch != "ok":
                parts.append(f"upper {h.upper_switch}, lower {h.lower_switch}")
            if h.upper_diode != "ok" or h.lower_diode != "ok":
                parts.append(f"diodes upper {h.upper_diode}, lower {h.lower_diode}")
            if not h.upper_gate and c.kind in ("upper_on", "pwm"):
                parts.append("upper gate unavailable")
            if not h.lower_gate and c.kind in ("lower_on", "pwm"):
                parts.append("lower gate unavailable")
            out.append("; ".join(parts))
        return out

    # -- reactions -------------------------------------------------------------------------------------------
    def request(self, t, mech: MechanismState, detail: str, info: dict | None = None):
        spec = mech.spec
        self.detections.append(Detection(t, spec.mech_id, spec.kind, detail, spec.reaction))
        self.ev(t, "detection", spec.mech_id, detail, reaction=spec.reaction, mech_kind=spec.kind)
        if not self.s.protection_enabled:
            self.ev(t, "reaction_blocked", spec.mech_id, "protection disabled for this run (comparison)")
            return
        if spec.reaction == "report_only":
            return
        path = self.s.paths[spec.path]
        why = self._path_down(path)
        if why:
            self.ev(t, "reaction_blocked", spec.mech_id, why)
            return
        self.schedule(t + path.delay_s, 2, "actuate", (mech, path, t, dict(info or {}, detected_by=spec.kind,
                                                                             mechanism=spec.mech_id)))

    def _path_down(self, path: PathSpec) -> str | None:
        if path.path_id in self.paths_lost:
            return f"path {path.path_id} lost (latent fault)"
        if not self.res.ok(path.resources):
            return f"path {path.path_id} lost its resource(s) {', '.join(self.res.missing(path.resources))}"
        return None

    def actuate(self, t, mech: MechanismState, path: PathSpec, t_req: float, info: dict):
        why = self._path_down(path)
        if why:
            self.ev(t, "reaction_blocked", mech.spec.mech_id, why + " before actuation")
            return
        spec = mech.spec
        reaction, rule = path.fixed_reaction or spec.reaction, "fixed by the path" if path.fixed_reaction else ""
        if reaction == "safe_state":
            # the deciding logic knows the MCU's last measurement (a hardware path gets it as the MCU's pre-selection,
            # frozen when the MCU stops), the reporting mechanism, its device and the drivers' UVLO reports
            known = dict(info)
            known.setdefault("speed_rpm", self.last_meas.get("speed_rpm"))
            known.setdefault("vdc_V", self.last_meas.get("vdc_V"))
            known["uvlo"] = tuple(sorted(self.uvlo_sides))
            reaction, rule = self.s.policy.decide(known, self.last_rule)
            if rule.startswith("rule "):
                self.last_rule = int(rule.split()[1]) - 1
            sp = known.get("speed_rpm")
            rule += f" (measured {sp:.0f} rpm)" if sp is not None else " (no speed information)"
        if self.s.reaction_override:
            if reaction in BRIDGE_REACTIONS or reaction == "torque_zero":
                reaction, rule = self.s.reaction_override, "override (candidate comparison)"
        hw = "MCU" not in path.resources
        if reaction == "torque_zero":
            if hw:
                self.ev(t, "reaction_blocked", spec.mech_id, "torque_zero needs the software (MCU) path")
                return
            if self.sw_reaction is None and self.hw_reaction is None and self.torque_override is None:
                self.torque_override = 0.0
                self.reaction_by.append(spec.mech_id)
                self.ev(t, "actuation", spec.mech_id, f"torque command forced to zero (PWM continues) - {rule}",
                        reaction="torque_zero", requested_at=t_req)
            return
        cur = self.hw_reaction if hw else self.sw_reaction
        side_lost = {"asc_low": "lower", "asc_high": "upper"}.get(cur) in self.uvlo_sides
        if cur is not None and self.s.policy.rank(reaction) >= self.s.policy.rank(cur):
            if not (side_lost and self.s.policy.replace_unexecutable and reaction != cur):
                self.ev(t, "reaction_kept", spec.mech_id, f"{reaction} requested ({rule}), {cur} kept (priority)")
                return
            self.ev(t, "reaction_conflict", spec.mech_id, f"{cur} is not executable (the drivers report the "
                                                          f"{'lower' if cur == 'asc_low' else 'upper'} gate supply "
                                                          f"lost): replaced by {reaction}")
        if hw:
            self.hw_reaction = reaction
            if self.sw_reaction is not None and self.sw_reaction != reaction:
                self.ev(t, "reaction_conflict", spec.mech_id, f"hardware path forces {reaction} over the software "
                                                              f"reaction {self.sw_reaction}")
        else:
            self.sw_reaction = reaction
            if self.hw_reaction is not None and self.hw_reaction != reaction:
                self.ev(t, "reaction_conflict", spec.mech_id, f"software requests {reaction}, hardware keeps "
                                                              f"{self.hw_reaction}")
        self.reaction_by.append(spec.mech_id)
        self.ev(t, "actuation", spec.mech_id, f"{reaction} via {path.path_id} ({rule}; requested "
                                              f"{t_req * 1e3:.4g} ms)", reaction=reaction, requested_at=t_req,
                path=path.path_id, rule=rule)
        self.update_bridge(t, f"{spec.mech_id} -> {reaction}")

    # -- measurements ----------------------------------------------------------------------------------------
    def convert_all(self, t, x):
        for name, sen in self.sens.items():
            if not self.res.ok(sen.spec.resources):
                continue
            d = sen.delay()
            xd = x if d <= 0.0 else self.hist.at(t - d)
            sen.convert(t, self._truth_from(name, sen, xd, self.truth_currents(xd)))

    def _truth_from(self, name, sen, x, ia):
        for ph in range(3):
            for r in (f"current_{PHASES[ph]}", f"current_hw_{PHASES[ph]}", f"current_mon_{PHASES[ph]}"):
                if self.s.role(r) == name:
                    return ia[ph]
        k = sen.spec.kind
        if k == "position":
            return x[TH]
        if k == "voltage":
            return x[VDC]
        if k == "temperature":
            return self.s.temperature_C or 0.0
        return 0.0

    def read(self, t, role):
        v = self.sensor(role).read(t)
        return v

    # -- software mechanisms -----------------------------------------------------------------------------------
    def run_mechanism(self, t, x, m: MechanismState):
        spec = m.spec
        if m.disabled_reason or not self.res.ok(spec.resources):
            return
        if spec.params.get("task", "monitor") == "control" and (self.task_stopped or self.ctrl.state != "run"):
            return
        P = spec.params
        viol, detail = False, ""
        if spec.kind == "torque_monitor" and (self.hw_reaction or self.sw_reaction):
            # a bridge-level safe state is active: torque against the request means nothing there; the monitor
            # starts again from the torque it sees once the drive runs again
            m.count_s, m.lag_ref, m.ref_hist = 0.0, None, []
            if m.tripped and m.cleared_since is None:
                m.cleared_since = t
            return
        if spec.kind == "torque_monitor":
            ia, ib = self.read(t, "current_mon_a"), self.read(t, "current_mon_b")
            th = self.read(t, "position_monitor")
            if ia is None or ib is None or th is None:
                return
            ic = self.read(t, "current_mon_c") if P.get("three_sensors") else -ia - ib
            # the monitor compensates its position sensor's nominal delay with the control's speed estimate (a
            # declared dependency on the control software)
            th = th + self.ctrl.w_est * float(P.get("angle_delay_comp_s", self.sensor("position_monitor").spec.delay_s))
            cs, sn = math.cos(th), math.sin(th)
            i_al, i_be = (2 * ia - ib - ic) / 3.0, (ib - ic) / math.sqrt(3.0)
            i_d, i_q = i_al * cs + i_be * sn, -i_al * sn + i_be * cs
            c = self.s.control
            w_m = self.ctrl.w_est / c.p
            T_est = 1.5 * c.p * (c.psi * i_q + (c.Ld - c.Lq) * i_d * i_q) - (c.b_rot * w_m + c.c_rot * w_m * abs(w_m))
            src = P.get("request_input", "monitor_message")
            if src == "vehicle":                         # an ideal independent view of the intent
                T_ref = self.s.request(max(0.0, t - float(P.get("request_latency_s", 0.0))))
            elif src == "monitor_message":               # its own copy of the request message (own E2E, same timing)
                pay, _ts = self.mon_cmd_path.newest(t, self.s.request)
                T_ref = pay if pay is not None else self.s.request(0.0)
            else:                                        # the control's received command (a common cause)
                T_ref = self.last_cmd if self.last_cmd is not None else 0.0
            dly = float(P.get("delay_s", 0.0))
            m.ref_hist.append((t, T_ref))
            while len(m.ref_hist) > 1 and m.ref_hist[1][0] <= t - dly + 1e-12:
                m.ref_hist.pop(0)
            vals = [v for _, v in m.ref_hist]
            tau = float(P.get("response_tau_s", 0.0))
            lag_in = m.ref_hist[0][1]
            if m.lag_ref is None:
                m.lag_ref = T_est if len(m.ref_hist) == 1 and t > 0 else lag_in   # a restarted monitor starts
                #                                                                 from the torque it sees
            else:
                step = (1.0 - math.exp(-spec.period_s / tau)) * (lag_in - m.lag_ref) if tau > 0 else lag_in - m.lag_ref
                rate = P.get("ramp_Nm_per_s")            # the healthy drive's torque ramp (restart, rate limit)
                if rate:
                    step = max(-float(rate) * spec.period_s, min(float(rate) * spec.period_s, step))
                m.lag_ref += step
            lo, hi = torque_window(min(vals), max(vals), m.lag_ref, P)
            self.mon_view = {"T_est": T_est, "lo": lo, "hi": hi}
            viol = not (lo <= T_est <= hi)
            detail = f"estimated torque {T_est:.1f} N*m outside [{lo:.1f}, {hi:.1f}] N*m"
        elif spec.kind == "current_plausibility":
            vals = [self.read(t, f"current_mon_{ph}") for ph in PHASES]
            if any(v is None for v in vals):
                return
            s = abs(sum(vals))
            viol = s > float(P["threshold_A"])
            detail = f"|i_a + i_b + i_c| = {s:.1f} A > {float(P['threshold_A']):g} A"
        elif spec.kind == "overcurrent_sw":
            vals = [self.read(t, f"current_mon_{ph}") for ph in PHASES]
            if any(v is None for v in vals):
                return
            mx = max(abs(v) for v in vals)
            viol = mx > float(P["threshold_A"])
            detail = f"measured phase current {mx:.1f} A > {float(P['threshold_A']):g} A"
        elif spec.kind in ("overvoltage_sw", "undervoltage_sw"):
            v = self.read(t, "vdc_monitor")
            if v is None:
                return
            thr = float(P["threshold_V"])
            viol = v > thr if spec.kind == "overvoltage_sw" else v < thr
            detail = f"measured Vdc {v:.1f} V {'>' if spec.kind == 'overvoltage_sw' else '<'} {thr:g} V"
        elif spec.kind == "position_los":
            sen = self.sensor("position_monitor")
            viol = bool(sen.flags(t, sen.read(t)).get("loss_of_signal"))
            detail = "position converter loss-of-signal flag"
        elif spec.kind == "command_timeout":
            age = t - self.cmd_sent if self.cmd_sent is not None else math.inf
            viol = age > float(P["timeout_s"])
            detail = f"torque command age {age * 1e3:.4g} ms > {float(P['timeout_s']) * 1e3:g} ms"
        if viol:
            m.count_s += spec.period_s
            m.cleared_since = None
            if not m.tripped and m.count_s >= float(P.get("debounce_s", 0.0)) - 1e-12:
                m.tripped, m.t_trip = True, t
                self.request(t, m, detail)
        else:
            m.count_s = 0.0
            if m.tripped and m.cleared_since is None:
                m.cleared_since = t

    # -- continuous (hardware) mechanisms -----------------------------------------------------------------------
    def externals(self):
        out = []
        pl = self.plant
        for m in self.mechs:
            spec = m.spec
            if (not spec.hardware or m.tripped or m.disabled_reason or not self.res.ok(spec.resources)
                    or m.pending_since is not None):
                continue
            P = spec.params
            if spec.kind == "overcurrent_hw":
                thr = float(P["threshold_A"])
                sens = [self.sensor(f"current_hw_{ph}") for ph in PHASES]

                def val(t, x, sens=sens, thr=thr):
                    ia = phase_currents(x[ID], x[IQ], x[TH])
                    return thr - max(abs(s.analog(i)) for s, i in zip(sens, ia))
                out.append(ExternalEvent(spec.mech_id, val, self._hw_fire(m, val, "analog phase-current comparator")))
            elif spec.kind == "overvoltage_hw":
                thr = float(P["threshold_V"])
                sv = self.sensor("vdc_hw")

                def val(t, x, sv=sv, thr=thr):
                    return thr - sv.analog(x[VDC])
                out.append(ExternalEvent(spec.mech_id, val, self._hw_fire(m, val, "analog DC-link voltage comparator")))
            elif spec.kind == "desat":
                I = float(P["threshold_A"])
                for k in range(3):
                    h, c = pl.health[k], pl.cmd[k]
                    up_on = c.kind == "upper_on" or (c.kind == "pwm" and c.duty > 0.0)
                    lo_on = c.kind == "lower_on" or (c.kind == "pwm" and c.duty < 1.0)
                    if up_on and h.upper_switch == "ok" and h.upper_gate:
                        out.append(ExternalEvent(f"{spec.mech_id}:{PHASES[k]}+",
                                                 lambda t, x, k=k: I - phase_currents(x[ID], x[IQ], x[TH])[k],
                                                 self._desat_fire(m, k, "upper")))
                    if lo_on and h.lower_switch == "ok" and h.lower_gate:
                        out.append(ExternalEvent(f"{spec.mech_id}:{PHASES[k]}-",
                                                 lambda t, x, k=k: I + phase_currents(x[ID], x[IQ], x[TH])[k],
                                                 self._desat_fire(m, k, "lower")))
        if self.s.bms is not None and self.bms_limit is not None and self.dc.contactor_closed:
            lim = self.bms_limit
            if self.bms_since is None:
                out.append(ExternalEvent("BMS", lambda t, x: lim + pl.battery_current(x), self._bms_fire))
            else:              # the charging current must stay above the limit for the BMS delay
                out.append(ExternalEvent("BMS clear", lambda t, x: -(lim + pl.battery_current(x)), self._bms_clear))
        return out

    def _hw_fire(self, m: MechanismState, val, what: str):
        def fire(t, x):
            f = float(m.spec.params.get("filter_s", 0.0))
            if f > 0.0:
                m.pending_since = t
                self.schedule(t + f, 6, "hw_confirm", (m, val, what))
                return True
            m.tripped, m.t_trip = True, t
            self.request(t, m, f"{what} tripped (threshold {self._thr_text(m)})")
            return True
        return fire

    @staticmethod
    def _thr_text(m):
        P = m.spec.params
        return f"{P['threshold_A']:g} A" if "threshold_A" in P else f"{P.get('threshold_V', 0):g} V"

    def _desat_fire(self, m: MechanismState, leg: int, device: str):
        def fire(t, x):
            self.schedule(t + float(m.spec.params.get("turnoff_s", 2e-6)), 1, "desat_off", (leg, device, m))
            self.ev(t, "detection", m.spec.mech_id, f"desaturation of the {device} switch of leg {PHASES[leg]}",
                    reaction=m.spec.reaction, mech_kind="desat")
            return True
        return fire

    def _bms_fire(self, t, x):
        self.bms_since = t
        self.bms_token += 1
        self.schedule(t + float(self.s.bms.get("delay_s", 0.0)), 1, "bms_open", self.bms_token)
        return True

    def _bms_clear(self, t, x):
        self.bms_since = None
        return True

    # -- recovery ----------------------------------------------------------------------------------------------
    def _check_recovery(self, t, x):
        pol = self.s.policy
        if pol.latch or (self.sw_reaction is None and self.hw_reaction is None and self.torque_override is None):
            return
        if self.mcu_state != "run":
            return
        causes = [m for m in self.mechs if m.spec.mech_id in self.reaction_by]
        for m in causes:
            if m.spec.hardware:
                # a comparator clears when its analog condition is back inside the threshold
                if m.spec.kind in ("overcurrent_hw", "overvoltage_hw"):
                    ok = self._hw_value(m, x) > 0.0
                    if ok and m.cleared_since is None:
                        m.cleared_since = t
                    elif not ok:
                        m.cleared_since = None
            if m.cleared_since is None or t - m.cleared_since < pol.recovery_after_s - 1e-12:
                return
        if self.recovery_attempts >= pol.recovery_max_attempts:
            if self.recovery_attempts == pol.recovery_max_attempts:
                self.recovery_attempts += 1
                self.ev(t, "recovery", "reaction manager", f"recovery attempts exhausted "
                                                           f"({pol.recovery_max_attempts}): the reaction stays latched")
            return
        self.recovery_attempts += 1
        for m in causes:
            m.tripped, m.t_trip, m.count_s, m.cleared_since, m.pending_since = False, None, 0.0, None, None
        self.reaction_by = []
        self.sw_reaction = self.hw_reaction = None
        self.torque_override = None
        self.ctrl.restart(pol.restart_mode)
        self.ev(t, "recovery", "reaction manager", f"recovery attempt {self.recovery_attempts}: condition cleared "
                                                   f"for {pol.recovery_after_s * 1e3:g} ms, {pol.restart_mode} restart")
        self.update_bridge(t, "recovery")

    def _hw_value(self, m, x):
        P = m.spec.params
        if m.spec.kind == "overcurrent_hw":
            ia = phase_currents(x[ID], x[IQ], x[TH])
            return float(P["threshold_A"]) - max(abs(self.sensor(f"current_hw_{ph}").analog(i))
                                                 for ph, i in zip(PHASES, ia))
        return float(P["threshold_V"]) - self.sensor("vdc_hw").analog(x[VDC])

    # -- control step ------------------------------------------------------------------------------------------
    def control_step(self, t, x):
        s = self.s
        self.convert_all(t, x)                     # the ADC trigger grid; a converter on a lost resource is silent
        if self.mcu_state != "run":
            return
        if self.ctrl.state in ("run", "starting"):
            self.last_alive = t
            self.wd_token += 1
            for m in self.mechs:
                if m.spec.kind == "watchdog" and not m.tripped:
                    self.schedule(t + float(m.spec.params["timeout_s"]), 7, "wd_expire", (m, self.wd_token))
        i_meas = tuple(self.read(t, f"current_{ph}") for ph in PHASES)
        th = self.read(t, "position_control")
        vdc = self.read(t, "vdc_control")
        temp = self.read(t, "temperature") if s.roles.get("temperature") else None
        if any(v is None for v in i_meas) or th is None or vdc is None:
            return
        T_cmd, t_sent = self.cmd_path.newest(t, s.request)
        if T_cmd is not None:
            self.last_cmd, self.cmd_sent = T_cmd, t_sent
        age = t - self.cmd_sent if self.cmd_sent is not None else math.inf
        use = self.torque_override if self.torque_override is not None else self.last_cmd
        if s.reaction_override == "torque_zero" and self.torque_override is not None:
            use = 0.0
        prev_state = self.ctrl.state
        out = self.ctrl.step(t, i_meas, th, vdc, temp, use, age)
        self.last_meas = {"speed_rpm": self.ctrl.w_est / s.machine.p * 60.0 / TWO_PI, "vdc_V": vdc}
        if prev_state == "starting" and self.ctrl.state == "run":
            self.update_bridge(t, "flying restart: speed re-established")
        if out.duties is not None:
            self.schedule(t + s.control.Ts, 3, "reload", out.duties)
        self._check_recovery(t, x)

    # -- the run ---------------------------------------------------------------------------------------------
    def initial_state(self):
        s, m, c = self.s, self.s.machine, self.s.control
        w_m = s.speed_rpm * TWO_PI / 60.0
        w_e = m.p * w_m
        T0 = s.request(0.0)
        i_d, i_q, T_used = s.table.at(T0)
        v_d = m.Rs * i_d - w_e * m.Lq * i_q
        v_q = m.Rs * i_q + w_e * (m.Ld * i_d + m.psi)
        P = 1.5 * (v_d * i_d + v_q * i_q)
        if self.dc.contactor_closed:
            disc = self.dc.V_oc ** 2 - 4.0 * self.dc.R_bat * P
            if disc < 0:
                raise InputValidationError("the battery cannot carry the initial operating point (no terminal "
                                           "voltage)", field="operating_point")
            v_dc = 0.5 * (self.dc.V_oc + math.sqrt(disc))
            i_bat = (self.dc.V_oc - v_dc) / self.dc.R_bat
            if not self.dc.charge_accepting and i_bat < 0:
                raise InputValidationError("a source without charge acceptance cannot start in regeneration",
                                           field="operating_point")
        else:
            v_dc, i_bat = self.dc.V_oc, 0.0
        x = [i_d, i_q, s.theta0 % TWO_PI, w_m, v_dc, i_bat, 0.0, 0.0, 0.0, 0.0, 0.0]
        # the controller starts in equilibrium at the initial reading; the PWM holds the steady-state duties (the
        # history before t = 0 is the steady rotation: the angle runs back at the initial speed)
        w0 = x[WM] * s.machine.p
        self.hist.add(-1.0, [i_d, i_q, x[TH] - w0 * 1.0, w_m, v_dc, i_bat, 0.0, 0.0, 0.0, 0.0, 0.0])
        self.hist.add(0.0, x)
        self.convert_all(0.0, x)
        th_meas = self.read(0.0, "position_control")
        self.ctrl.init_steady(i_d, i_q, w_e, th_meas, T_used)
        th = x[TH] + w_e * 0.5 * c.Ts                      # the first interval's voltage is centered half an update in
        v_al = v_d * math.cos(th) - v_q * math.sin(th)
        v_be = v_d * math.sin(th) + v_q * math.cos(th)
        mm = math.hypot(v_al, v_be) / (0.5 * v_dc)
        dd = _duties(np.array([0.0]), mm, math.atan2(v_be, v_al), c.modulation)[:, 0]
        self.pwm.load(0.0, [float(v) for v in dd])
        if s.pwm_model == "switched":
            self.pwm.apply_edges(0.0)
        self.apply_leg_commands()
        self.last_cmd, self.cmd_sent = T0, 0.0
        self.last_meas = {"speed_rpm": s.speed_rpm, "vdc_V": v_dc}
        return x, {"i_d_A": i_d, "i_q_A": i_q, "T_Nm": T_used, "v_dc_V": v_dc, "i_bat_A": i_bat,
                   "v_d_V": v_d, "v_q_V": v_q}

    def run(self) -> SimResult:
        s, pl, c = self.s, self.plant, self.s.control
        x, init = self.initial_state()
        modes = pl.decide_modes(x)
        rec = _Recorder(self)
        H = s.horizon_s
        for f in s.faults:
            if f.t_s <= H:
                self.schedule(f.t_s, 0, "fault", f)
        Ts = c.Ts
        k = 1
        while k * Ts <= H + 1e-12:
            self.schedule(round(k * Ts, 12), 4, "control", None)
            k += 1
        self.schedule(0.0, 4, "control", None)
        for mst in self.mechs:
            if not mst.spec.hardware and mst.spec.kind != "watchdog":
                per = mst.spec.period_s
                n = 0
                while (n + mst.spec.phase) * per <= H + 1e-12:
                    self.schedule(round((n + mst.spec.phase) * per, 12), 5, "monitor", mst)
                    n += 1
        for m in self.mechs:
            if m.spec.kind == "watchdog":
                self.schedule(float(m.spec.params["timeout_s"]), 7, "wd_expire", (m, 0))
        for tb in s.request.breakpoints():
            if 0 < tb <= H:
                self.schedule(tb, 8, "breakpoint", None)
        self.schedule(H, 9, "end", None)
        t, status = 0.0, "completed"
        rec.add(0.0, x, modes)
        ext = self.externals()
        while self.heap:
            t_next = self.heap[0][0]
            if s.pwm_model == "switched" and self.bridge_cmd == "pwm":
                t_next = min(t_next, self.pwm.next_edge(t))
            if t_next > t + 1e-15:
                try:
                    x, modes, t_r, stop = integrate(pl, x, modes, t, t_next, s.h_max_s, ext, on_step=rec.add)
                except ShootThrough as st:
                    if self._desat_handles(st, t):
                        modes = pl.decide_modes(x)
                        ext = self.externals()
                        continue
                    status = self._stop(t, st)
                    break
                except OutOfModel as om:
                    status = self._stop(t, om)
                    break
                t = t_r
                if stop is not None:
                    ext = self.externals()
                    continue
            if s.pwm_model == "switched" and self.pwm.apply_edges(t) and self.bridge_cmd == "pwm":
                self.apply_leg_commands()
            batch = []
            while self.heap and self.heap[0][0] <= t + 1e-12:
                batch.append(heapq.heappop(self.heap))
            batch.sort(key=lambda e: (e[1], e[2]))      # one instant: the fixed order, then scheduling order
            ended = False
            try:
                for (_te, _o, _sq, kind, payload) in batch:
                    if kind == "end":
                        ended = True
                        break
                    x = self._handle(t, x, kind, payload)
                modes = pl.decide_modes(x)
            except ShootThrough as st:
                if not self._desat_handles(st, t):
                    status = self._stop(t, st)
                    break
                modes = pl.decide_modes(x)
            except OutOfModel as om:
                status = self._stop(t, om)
                break
            rec.add(t, x, modes)
            if ended:
                self.heap.clear()
                break
            ext = self.externals()
        trace = rec.arrays()
        summary = self._summary(trace, x, init, status)
        echo = {"speed_rpm": s.speed_rpm, "horizon_s": s.horizon_s, "theta0": s.theta0, "pwm_model": s.pwm_model,
                "faults": [f.to_dict() for f in s.faults], "request": s.request.to_dict(),
                "protection_enabled": s.protection_enabled, "reaction_override": s.reaction_override,
                "h_max_s": s.h_max_s, "identity": s.identity}
        return SimResult(trace, self.events, status, self.stop_reason, summary, echo)

    def _stop(self, t, exc) -> str:
        self.stop_reason = str(exc)
        self.ev(t, "out_of_model", "plant", str(exc))
        return "stopped_out_of_model"

    def _handle(self, t, x, kind, payload):
        pl = self.plant
        if kind == "fault_clear":
            self.clear_fault(t, x, payload)
        elif kind == "fault":
            x = self.apply_fault(t, x, payload)
            if payload.kind in ("gate_supply_loss", "resource_loss", "pwm_output", "mcu_reset"):
                self.update_bridge(t, payload.kind)
                self.apply_leg_commands()
        elif kind == "actuate":
            mech, path, t_req, info = payload
            self.actuate(t, mech, path, t_req, info)
        elif kind == "desat_off":
            leg, device, mst = payload
            setattr(pl.health[leg], f"{device}_gate", False)
            self.ev(t, "driver", mst.spec.mech_id, f"gate driver turned the {device} switch of leg {PHASES[leg]} off "
                                                   f"(desaturation, latched)", actual=self.actual_bridge())
            if not mst.tripped:
                mst.tripped, mst.t_trip = True, t
                self.request(t, mst, f"desaturation reported (leg {PHASES[leg]} {device})",
                             {"device": device})
        elif kind == "uvlo_report":
            mst, side = payload
            self.uvlo_sides.add(side)
            if not mst.tripped:
                mst.tripped, mst.t_trip = True, t
                self.request(t, mst, f"gate-driver under-voltage lockout reported ({side} side)", {"uvlo_side": side})
        elif kind == "bms_open":
            if payload == self.bms_token and self.bms_since is not None:
                self.ev(t, "detection", "BMS", f"battery charging current above {self.bms_limit:g} A for "
                                               f"{float(self.s.bms.get('delay_s', 0.0)) * 1e3:g} ms "
                                               f"(battery management)")
                x = self._open_contactor(t, x, "battery management: charge-current limit")
                self.bms_since = None
        elif kind == "active_discharge":
            self.dc.active_discharge_on = True
            self.ev(t, "plant", "active discharge", "active discharge on")
        elif kind == "mcu_boot":
            self.res.restore("MCU")
            self.mcu_state = "run"
            if self.s.policy.after_reset == "restart" and self.hw_reaction is None:
                self.ctrl.restart(self.s.policy.restart_mode)
                self.ev(t, "controller", "MCU", f"MCU booted: {self.s.policy.restart_mode} restart of the current "
                                                f"control")
            else:
                self.ctrl.state = "halted"
                self.ev(t, "controller", "MCU", "MCU booted: software halted (policy: safe state after a reset)"
                        if self.s.policy.after_reset != "restart" else
                        "MCU booted: a hardware reaction is latched - software stays halted")
                if self.s.policy.after_reset != "restart":
                    reaction, rule = self.s.policy.decide({"speed_rpm": None, "vdc_V": None, "detected_by": "reset",
                                                           "uvlo": tuple(sorted(self.uvlo_sides))})
                    if reaction in BRIDGE_REACTIONS:
                        self.sw_reaction = reaction
            self.update_bridge(t, "MCU booted")
        elif kind == "reload":
            if self.mcu_state == "run" and self.ctrl.state == "run":
                self.pwm.load(t, payload)
                if self.bridge_cmd == "pwm":
                    if self.s.pwm_model == "switched":
                        self.pwm.apply_edges(t)
                    self.apply_leg_commands()
        elif kind == "control":
            self.control_step(t, x)
        elif kind == "monitor":
            if self.mcu_state == "run" or "MCU" not in payload.spec.resources:
                self.run_mechanism(t, x, payload)
        elif kind == "hw_confirm":
            m, val, what = payload
            m.pending_since = None
            if not m.tripped and not m.disabled_reason and self.res.ok(m.spec.resources) and val(t, x) <= 0.0:
                m.tripped, m.t_trip = True, t
                self.request(t, m, f"{what} tripped (threshold {self._thr_text(m)}, filter "
                                   f"{float(m.spec.params.get('filter_s', 0)) * 1e6:g} us)")
        elif kind == "wd_expire":
            m, token = payload
            if (token == self.wd_token and not m.tripped and not m.disabled_reason
                    and self.res.ok(m.spec.resources)):
                m.tripped, m.t_trip = True, t
                self.request(t, m, f"no control-task alive signal for {(t - self.last_alive) * 1e3:.4g} ms")
        return x

    def _desat_handles(self, st: ShootThrough, t: float) -> bool:
        """A shoot-through against a shorted switch: the desaturation detector of the healthy (gated) switch turns
        it off within its turn-off time; the short itself stays.  Without a working desaturation mechanism the leg
        short is outside the plant."""
        for m in self.mechs:
            if m.spec.kind == "desat" and not m.disabled_reason and self.res.ok(m.spec.resources):
                setattr(self.plant.health[st.leg], f"{st.healthy}_gate", False)
                self.ev(t, "detection", m.spec.mech_id, f"shoot-through in leg {PHASES[st.leg]}: the {st.healthy} "
                        f"switch desaturated and was turned off (short-circuit withstand of the device and the energy "
                        f"of the short: not evaluated - supplier SC SOA and the loop inductance needed)",
                        reaction=m.spec.reaction, mech_kind="desat")
                if not m.tripped:
                    m.tripped, m.t_trip = True, t
                    self.request(t, m, f"desaturation (shoot-through leg {PHASES[st.leg]})", {"device": st.healthy})
                self.apply_leg_commands()
                return True
        return False

    # -- summary -----------------------------------------------------------------------------------------------
    def _summary(self, tr: dict, x, init: dict, status: str) -> dict:
        s = self.s
        t = tr["t"]
        ia = np.vstack([tr["i_a"], tr["i_b"], tr["i_c"]])
        iabs = np.max(np.abs(ia), axis=0) if ia.size else np.zeros(0)
        k_i = int(np.argmax(iabs)) if iabs.size else 0
        E0_cap = 0.5 * self.dc.C * tr["v_dc"][0] ** 2
        E1_cap = 0.5 * self.dc.C * x[VDC] ** 2
        E0_mag = self.plant.magnetic_energy(tr["i_d"][0], tr["i_q"][0])
        E1_mag = self.plant.magnetic_energy(x[ID], x[IQ])
        dc_res = x[E_BAT] - x[E_BLEED] - x[E_INV] - (E1_cap - E0_cap)
        m_res = x[E_INV] - x[E_CU] - x[E_MECH] - (E1_mag - E0_mag)
        scale = max(abs(x[E_BAT]), abs(x[E_INV]), abs(x[E_CU]), abs(x[E_MECH]), abs(E1_cap - E0_cap), 1e-9)
        det = [e for e in self.events if e["kind"] == "detection"]
        act = [e for e in self.events if e["kind"] == "actuation"]
        return {
            "status": status, "stop_reason": self.stop_reason, "t_end_s": float(t[-1]) if len(t) else 0.0,
            "initial": init,
            "i_phase_peak_A": float(iabs[k_i]) if iabs.size else 0.0,
            "t_i_phase_peak_s": float(t[k_i]) if iabs.size else 0.0,
            "v_dc_max_V": float(np.max(tr["v_dc"])), "t_v_dc_max_s": float(t[int(np.argmax(tr["v_dc"]))]),
            "v_dc_min_V": float(np.min(tr["v_dc"])),
            "i_bat_charge_max_A": float(max(0.0, -np.min(tr["i_bat"]))),
            "T_em_max_Nm": float(np.max(tr["T_em"])), "T_em_min_Nm": float(np.min(tr["T_em"])),
            "T_shaft_max_Nm": float(np.max(tr["T_shaft"])), "T_shaft_min_Nm": float(np.min(tr["T_shaft"])),
            "first_detection": det[0] if det else None, "first_actuation": act[0] if act else None,
            "detections": len(det), "final_bridge": self.bridge_cmd, "final_actual": self.actual_bridge(),
            "energy": {"E_bat_J": x[E_BAT], "E_cu_J": x[E_CU], "E_mech_J": x[E_MECH], "E_bleed_J": x[E_BLEED],
                       "E_inv_J": x[E_INV], "dE_cap_J": E1_cap - E0_cap, "dE_mag_J": E1_mag - E0_mag,
                       "E_arc_J": self.plant.E_arc, "dc_residual_J": dc_res, "machine_residual_J": m_res,
                       "relative_residual": max(abs(dc_res), abs(m_res)) / scale},
            "n_samples": int(len(t)),
            "zeno": dict(self.plant.zeno),
        }


class _Recorder:
    """Samples at every accepted integration step and every discrete instant (the requirement evaluation reads these
    samples; the plots draw them)."""
    KEYS = ("t", "i_a", "i_b", "i_c", "i_a_meas", "i_b_meas", "i_c_meas", "i_d", "i_q", "T_em", "T_shaft", "T_request",
            "T_cmd",
            "T_used", "T_est_mon", "mon_lo", "mon_hi", "v_dc", "v_dc_meas", "i_bat", "i_dc", "speed_rpm",
            "speed_est_rpm", "theta_err_deg", "d_a", "d_b", "d_c", "bridge", "mcu")

    def __init__(self, sim: _Sim):
        self.sim = sim
        self.d = {k: [] for k in self.KEYS}
        self.modes = []
        self.legs = []

    def add(self, t, x, modes):
        sim, pl = self.sim, self.sim.plant
        sim.hist.add(t, x)
        d = self.d
        if d["t"] and t <= d["t"][-1] + 1e-15:
            for k in self.KEYS:                          # a discrete instant: the sample after it replaces the last
                d[k].pop()
            self.modes.pop()
            self.legs.pop()
        ia = phase_currents(x[ID], x[IQ], x[TH])
        d["t"].append(t)
        for k, ph in enumerate(PHASES):
            d[f"i_{ph}"].append(ia[k])
            d[f"i_{ph}_meas"].append(sim.sensor(f"current_{ph}").analog(ia[k]))
            c = pl.cmd[k]
            d[f"d_{ph}"].append(c.duty if c.kind == "pwm" else {"upper_on": 1.0, "lower_on": 0.0}.get(c.kind, math.nan))
        d["i_d"].append(x[ID])
        d["i_q"].append(x[IQ])
        T_em = pl.torque(x[ID], x[IQ])
        m = sim.s.machine
        d["T_em"].append(T_em)
        d["T_shaft"].append(T_em - (m.b_visc * x[WM] + m.c_quad * x[WM] * abs(x[WM])))
        d["T_request"].append(sim.s.request(t))
        d["T_cmd"].append(sim.last_cmd if sim.last_cmd is not None else math.nan)
        d["T_used"].append(sim.ctrl.T_used if (sim.bridge_cmd == "pwm" and sim.ctrl.state == "run") else math.nan)
        d["T_est_mon"].append(sim.mon_view["T_est"])
        d["mon_lo"].append(sim.mon_view["lo"])
        d["mon_hi"].append(sim.mon_view["hi"])
        d["v_dc"].append(x[VDC])
        d["v_dc_meas"].append(sim.sensor("vdc_control").analog(x[VDC]))
        d["i_bat"].append(pl.battery_current(x))
        try:
            d["i_dc"].append(pl.dc_current(x, modes))
        except OutOfModel:
            d["i_dc"].append(math.nan)
        d["speed_rpm"].append(x[WM] * 60.0 / TWO_PI)
        d["speed_est_rpm"].append(sim.ctrl.w_est / sim.s.machine.p * 60.0 / TWO_PI)
        th_m = sim.sensor("position_control").analog(x[TH])
        d["theta_err_deg"].append(math.degrees(unwrap_delta(th_m, x[TH] % TWO_PI)))
        d["bridge"].append(BRIDGE_CODES.get(sim.bridge_cmd, 4))
        d["mcu"].append({"run": 0, "reset": 1, "halted": 2}.get(sim.mcu_state, 2))
        self.modes.append(tuple(modes))
        self.legs.append(tuple(c.kind for c in pl.cmd))

    def arrays(self) -> dict:
        out = {k: np.asarray(v, dtype=float) for k, v in self.d.items()}
        out["leg_modes"] = [list(m) for m in self.modes]
        out["leg_cmds"] = [list(m) for m in self.legs]
        return out


def simulate(setup: SimSetup) -> SimResult:
    """Run one causal fault simulation."""
    return _Sim(setup).run()
