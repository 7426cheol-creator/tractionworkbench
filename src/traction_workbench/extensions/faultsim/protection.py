"""Safety mechanisms, reaction paths and the reaction manager - all declared by the project, none built in.

A mechanism reads measured channels only (sensor conversions and analog outputs, the sensors' own hardware
diagnostics, the command message age, the control task's alive signal, the gate drivers' fault reports) and requests
a reaction through a declared path.  Two kinds:

* software mechanisms run in a task of a declared period and phase on the resources they depend on (a stopped task
  or an MCU in reset runs nothing): torque monitor, current-sum plausibility, software over-current / over- and
  under-voltage, position loss-of-signal, command timeout;
* hardware mechanisms act in continuous time: over-current and over-voltage comparators on analog sensor outputs
  (with a glitch filter: the condition must persist for ``filter_s``), desaturation per gated switch (the device's
  own V_CE sensing), gate-driver under-voltage lockout reports, the external watchdog on the alive signal.

A path (e.g. ``SW_MAIN``: the MCU writes the reaction; ``HW_CPLD``: a logic device forces the gates; ``DRIVER``: the
gate driver itself) has a delay and the resources it needs; a lost resource disables every mechanism, sensor and path
that needs it (a common cause).  A ``safe_state`` request is resolved by the project's ordered rules evaluated on
what the deciding logic can know (measured speed and DC voltage, the reporting mechanism and its device, the gate
drivers' under-voltage reports); a path may instead declare a fixed reaction.  Reactions have a declared priority: a
later request replaces the active reaction only if it ranks higher.  Latch and recovery (return to operation after a
delay once the requesting condition has cleared, at most N attempts, flying or cold restart) are declared too.

The reaction manager commands; the bridge decides what actually happens: an active short circuit commanded without
the gate supply of its side, with an open switch or with a desaturation-latched device is only partial, and the plant
computes it as such.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...errors import InputValidationError

SW_KINDS = ("torque_monitor", "current_plausibility", "overcurrent_sw", "overvoltage_sw", "undervoltage_sw",
            "position_los", "command_timeout", "torque_window_signed", "torque_integral", "osc_power", "osc_energy",
            "rx_monitor", "rotor_plausibility", "vdc_plausibility", "estimator_domain", "pwm_feedback")
HW_KINDS = ("overcurrent_hw", "overvoltage_hw", "desat", "gate_uvlo", "watchdog", "dc_overcurrent_hw", "gde_monitor")
SYS_KINDS = ("system",)                         # a safe state requested by the system layer (not a detection)
REACTIONS = ("safe_state", "asc_low", "asc_high", "six_switch_off", "torque_zero", "report_only")
BRIDGE_REACTIONS = ("asc_low", "asc_high", "six_switch_off")
RULE_KEYS = ("speed_above_rpm", "speed_below_rpm", "vdc_above_V", "vdc_below_V", "detected_by", "device",
             "uvlo", "mechanism")
# the declared parameters of each mechanism kind as the data names them (unit in the key): (key, unit, default,
# what it is) - the design editor builds its fields from this; other keys in the data are kept as they are
KIND_PARAMS = {
    "torque_monitor": (("abs_Nm", "N*m", 30.0, "absolute half-width of the torque window"),
                       ("rel", "-", 0.15, "relative half-width (fraction of |T|)"),
                       ("delay_ms", "ms", 1.0, "request delay the window allows (message period, latency)"),
                       ("response_tau_ms", "ms", 2.0, "normal first-order torque response the window follows"),
                       ("ramp_Nm_per_ms", "N*m/ms", 50.0, "healthy torque ramp the window follows (blank: none)"),
                       ("debounce_ms", "ms", 3.0, "time outside the window before the trip"),
                       ("request_input", ("monitor_message", "control_command", "vehicle"), "monitor_message",
                        "which request the monitor compares with")),
    "current_plausibility": (("threshold_A", "A", 60.0, "|i_a + i_b + i_c| threshold"),
                             ("debounce_ms", "ms", 1.0, "time above the threshold before the trip")),
    "overcurrent_sw": (("threshold_A", "A", 900.0, "measured phase-current threshold"),
                       ("debounce_ms", "ms", 0.2, "time above the threshold before the trip")),
    "overvoltage_sw": (("threshold_V", "V", 760.0, "measured DC-voltage threshold"),
                       ("debounce_ms", "ms", 0.2, "time above the threshold before the trip")),
    "undervoltage_sw": (("threshold_V", "V", 300.0, "measured DC-voltage threshold"),
                        ("debounce_ms", "ms", 1.0, "time below the threshold before the trip")),
    "position_los": (("debounce_ms", "ms", 0.0, "time the loss-of-signal flag must stay set"),),
    "command_timeout": (("timeout_ms", "ms", 50.0, "maximum age of the torque command"),),
    "overcurrent_hw": (("threshold_A", "A", 900.0, "comparator threshold on the analog current outputs"),
                       ("filter_us", "us", 2.0, "glitch filter: the condition must persist this long")),
    "overvoltage_hw": (("threshold_V", "V", 780.0, "comparator threshold on the analog DC-voltage output"),
                       ("filter_us", "us", 5.0, "glitch filter: the condition must persist this long")),
    "desat": (("threshold_A", "A", 1500.0, "device current at which the switch desaturates"),
              ("turnoff_us", "us", 2.0, "soft turn-off time of the gate driver")),
    "gate_uvlo": (("delay_us", "us", 2.0, "report delay of the under-voltage lockout"),),
    "watchdog": (("timeout_ms", "ms", 5.0, "alive-signal timeout"),
                 ("service_by", ("control", "safety_task"), "control",
                  "what services it: the control task's alive signal or the safety task's checkpoints")),
    "torque_window_signed": (("limit_source", ("factor", "envelope", "both"), "factor",
                              "window: the request scaled by the limit factor and / or the received envelope"),
                             ("limit_factor", "-", 1.2, "limit factor F (high = max(T F, T / F), low = min(...))"),
                             ("abs_Nm", "N*m", 20.0, "absolute margin added to the factor window"),
                             ("env_above_Nm", "N*m", 50.0, "received maximum above the request (no static max)"),
                             ("env_below_Nm", "N*m", 50.0, "received minimum below the request (no static min)"),
                             ("tol_Nm", "N*m", 0.0, "tolerance when no speed map is declared"),
                             ("tol_rule", ("add", "widen"), "add",
                              "add: T + tol > high / T - tol < low (as recovered); widen: T - tol > high / T + tol "
                              "< low"),
                             ("debounce_ms", "ms", 5.0, "time outside the window before the trip"),
                             ("response_tau_ms", "ms", 0.0, "healthy first-order response the window also covers "
                                                            "(0: the literal formula)"),
                             ("delay_ms", "ms", 0.0, "request delay the window also covers (0: none)"),
                             ("request_input", ("monitor_message", "control_command", "vehicle"), "monitor_message",
                              "which request the window is built on")),
    "torque_integral": (("limit_source", ("factor", "envelope", "both"), "factor", "window as the time monitor"),
                        ("limit_factor", "-", 1.2, "limit factor F"),
                        ("abs_Nm", "N*m", 20.0, "absolute margin added to the factor window"),
                        ("tol_Nm", "N*m", 0.0, "tolerance when no speed map is declared"),
                        ("tol_rule", ("add", "widen"), "add", "tolerance rule (as the time monitor)"),
                        ("limit_Nms", "N*m*s", 0.5, "integral of the excess beyond the window that trips"),
                        ("leak_per_s", "1/s", 0.0, "leak of the integral (0: none)"),
                        ("request_input", ("monitor_message", "control_command", "vehicle"), "monitor_message",
                         "which request the window is built on")),
    "osc_power": (("f_hp_Hz", "Hz", 2.0, "high-pass corner of the estimate-minus-request oscillation"),
                  ("tau_env_ms", "ms", 20.0, "envelope time constant of the oscillating power"),
                  ("threshold_W", "W", 5000.0, "oscillating-power threshold"),
                  ("debounce_ms", "ms", 50.0, "time above the threshold before the trip")),
    "osc_energy": (("f_hp_Hz", "Hz", 2.0, "high-pass corner"),
                   ("allow_W", "W", 1000.0, "oscillating power that accumulates nothing"),
                   ("limit_J", "J", 200.0, "accumulated oscillation energy that trips"),
                   ("leak_per_s", "1/s", 0.0, "leak of the accumulation (0: none)")),
    "rx_monitor": (("max_age_ms", "ms", 30.0, "maximum age of the last ACCEPTED torque frame"),
                   ("max_invalid", "-", 0, "consecutive invalid frames that trip (0: age only)"),
                   ("debounce_ms", "ms", 0.0, "time in violation before the trip")),
    "rotor_plausibility": (("threshold_deg", "deg", 20.0, "allowed angle difference of the two position channels"),
                           ("debounce_ms", "ms", 1.0, "time beyond the threshold before the trip")),
    "vdc_plausibility": (("threshold_V", "V", 30.0, "allowed difference of the two DC-voltage channels"),
                         ("debounce_ms", "ms", 1.0, "time beyond the threshold before the trip")),
    "estimator_domain": (("speed_max_rpm", "rpm", 11000.0, "qualified speed domain of the torque estimate"),
                         ("current_max_A", "A", 1000.0, "qualified current domain of the torque estimate"),
                         ("debounce_ms", "ms", 0.0, "time outside the domain before the trip")),
    "pwm_feedback": (("duty_tol", "-", 0.05, "allowed difference of the observed and commanded duty"),
                     ("debounce_ms", "ms", 0.2, "time in mismatch before the trip")),
    "dc_overcurrent_hw": (("threshold_A", "A", 600.0, "comparator threshold on the analog DC-current output"),
                          ("filter_us", "us", 2.0, "glitch filter: the condition must persist this long")),
    "gde_monitor": (("delay_us", "us", 1.0, "logic delay from the lost enable to the hardware selection"),),
}
REQUIRED_PARAMS = {"current_plausibility": ("threshold_A",), "overcurrent_sw": ("threshold_A",),
                   "overvoltage_sw": ("threshold_V",), "undervoltage_sw": ("threshold_V",),
                   "command_timeout": ("timeout_s",), "overcurrent_hw": ("threshold_A",),
                   "overvoltage_hw": ("threshold_V",), "desat": ("threshold_A",), "watchdog": ("timeout_s",),
                   "torque_monitor": ("abs_Nm",), "torque_integral": ("limit_Nms",), "osc_power": ("threshold_W",),
                   "osc_energy": ("limit_J",), "rotor_plausibility": ("threshold_deg",),
                   "vdc_plausibility": ("threshold_V",), "dc_overcurrent_hw": ("threshold_A",)}


@dataclass(frozen=True)
class PathSpec:
    path_id: str
    delay_s: float
    resources: tuple = ()
    fixed_reaction: str | None = None          # a path that always does the same thing (e.g. a CPLD forcing ASC)
    basis: str = ""

    def __post_init__(self):
        if not (self.delay_s >= 0.0 and math.isfinite(self.delay_s)):
            raise InputValidationError("a path delay must be finite and >= 0", field=f"paths.{self.path_id}.delay_s")
        if self.fixed_reaction is not None and not str(self.fixed_reaction).strip():
            raise InputValidationError("a fixed reaction needs a name (a reaction or a strategy id)",
                                       field=f"paths.{self.path_id}.fixed_reaction")


@dataclass(frozen=True)
class MechanismSpec:
    mech_id: str
    kind: str
    path: str
    reaction: str = "safe_state"
    params: dict = field(default_factory=dict)
    period_s: float | None = None              # software task period
    phase: float = 0.0                         # task activation phase (fraction of the period)
    resources: tuple = ()
    enabled: bool = True
    text: str = ""

    def __post_init__(self):
        if self.kind not in SW_KINDS + HW_KINDS + SYS_KINDS:
            raise InputValidationError(f"mechanism kind must be one of {SW_KINDS + HW_KINDS}",
                                       field=f"mechanisms.{self.mech_id}.kind")
        # a primitive reaction or a declared strategy id (checked against the strategies where the set is known)
        if not str(self.reaction).strip():
            raise InputValidationError(f"reaction must be one of {REACTIONS} or a strategy id",
                                       field=f"mechanisms.{self.mech_id}")
        if self.kind in SW_KINDS and not (self.period_s and self.period_s > 0):
            raise InputValidationError("a software mechanism needs its task period",
                                       field=f"mechanisms.{self.mech_id}.period_s")
        for k in REQUIRED_PARAMS.get(self.kind, ()):
            if self.params.get(k) is None:
                raise InputValidationError(f"a mechanism of kind {self.kind} needs {k}",
                                           field=f"mechanisms.{self.mech_id}.params.{k}")

    @property
    def hardware(self) -> bool:
        return self.kind in HW_KINDS


@dataclass(frozen=True)
class SafeStatePolicy:
    rules: tuple = ()                           # ({"if": {...}, "then": reaction}, ..., {"else": reaction})
    priority: tuple = ("asc_low", "asc_high", "six_switch_off", "torque_zero")
    latch: bool = True
    recovery_after_s: float = 0.05              # a non-latched reaction: condition cleared this long -> restart
    recovery_max_attempts: int = 1
    restart_mode: str = "flying"
    after_reset: str = "restart"                # MCU booted after a reset: restart | safe_state
    basis: str = ""
    speed_hysteresis_rpm: float = 0.0           # a speed rule that decided keeps deciding within this band
    replace_unexecutable: bool = True           # an active ASC of a side the drivers report lost is replaced
    strategies: tuple = ()                      # the declared strategy ids a rule may name

    def __post_init__(self):
        known = tuple(r for r in REACTIONS if r != "safe_state") + tuple(self.strategies)
        for i, r in enumerate(self.rules):
            if "else" in r:
                if r["else"] not in known:
                    raise InputValidationError(f"rule reaction must be one of {known}", field=f"policy.rules[{i}]")
                continue
            if r.get("then") not in known:
                raise InputValidationError(f"a rule needs 'then': a concrete reaction or a strategy {known}",
                                           field=f"policy.rules[{i}]")
            unknown = set(r.get("if") or {}) - set(RULE_KEYS)
            if unknown:
                raise InputValidationError(f"unknown rule condition(s) {sorted(unknown)}; known: {RULE_KEYS}",
                                           field=f"policy.rules[{i}]")
        if self.after_reset not in ("restart", "safe_state"):
            raise InputValidationError("after_reset must be restart or safe_state", field="policy.after_reset")

    def decide(self, info: dict, last_rule: int | None = None) -> tuple[str, str]:
        """(reaction, which rule) from what the deciding logic knows: ``speed_rpm`` / ``vdc_V`` (measured),
        ``detected_by`` (mechanism kind), ``mechanism`` (id), ``device`` (upper / lower, desat), ``uvlo`` (the
        set of sides whose gate supply the drivers report lost).  ``last_rule``: the rule of the previous decision -
        its speed thresholds are relaxed by the declared hysteresis (a decision does not flip on speed noise)."""
        for i, r in enumerate(self.rules):
            if "else" in r:
                return r["else"], f"rule {i + 1} (else)"
            cond = r.get("if") or {}
            ok = True
            sp, vd = info.get("speed_rpm"), info.get("vdc_V")
            hy = self.speed_hysteresis_rpm if last_rule == i else 0.0
            if "speed_above_rpm" in cond:
                ok &= sp is not None and abs(sp) > float(cond["speed_above_rpm"]) - hy
            if "speed_below_rpm" in cond:
                ok &= sp is not None and abs(sp) < float(cond["speed_below_rpm"]) + hy
            if "vdc_above_V" in cond:
                ok &= vd is not None and vd > float(cond["vdc_above_V"])
            if "vdc_below_V" in cond:
                ok &= vd is not None and vd < float(cond["vdc_below_V"])
            if "detected_by" in cond:
                want = cond["detected_by"]
                ok &= info.get("detected_by") in (want if isinstance(want, (list, tuple)) else (want,))
            if "mechanism" in cond:
                ok &= info.get("mechanism") == cond["mechanism"]
            if "device" in cond:
                ok &= info.get("device") == cond["device"]
            if "uvlo" in cond:
                ok &= cond["uvlo"] in (info.get("uvlo") or ())
            if ok:
                return r["then"], f"rule {i + 1}"
        return "six_switch_off", "no rule matched (default six-switch-off)"

    def rank(self, reaction: str) -> int:
        return self.priority.index(reaction) if reaction in self.priority else len(self.priority)


@dataclass
class Detection:
    t: float
    mech_id: str
    kind: str
    detail: str
    reaction: str


@dataclass
class MechanismState:
    spec: MechanismSpec
    count_s: float = 0.0                       # debounce timer (s in violation)
    tripped: bool = False
    t_trip: float | None = None
    cleared_since: float | None = None
    disabled_reason: str | None = None         # latent fault / lost resource
    pending_since: float | None = None         # hardware glitch filter: violation seen since
    lag_ref: float | None = None               # torque monitor: lagged reference state
    ref_hist: list = field(default_factory=list)   # torque monitor: (t, reference) over the delay allowance
    aux: dict = field(default_factory=dict)    # the extended monitors' own states (integrals, filters)


class ResourceBook:
    """Resource availability (supplies, MCU cores, logic devices, converters): lost -> every dependent is lost."""

    def __init__(self):
        self.lost: dict = {}

    def lose(self, name: str, t: float):
        self.lost.setdefault(name, t)

    def restore(self, name: str):
        self.lost.pop(name, None)

    def ok(self, names) -> bool:
        return not any(n in self.lost for n in names)

    def missing(self, names) -> list:
        return [n for n in names if n in self.lost]


def window_width(T: float, params: dict) -> float:
    return max(float(params.get("abs_Nm", 0.0)), float(params.get("rel", 0.0)) * abs(T))


def torque_window(T_min: float, T_max: float, T_lag: float, params: dict) -> tuple[float, float]:
    """Allowed torque band (the dynamic window): the union of the bands around every reference value of the last
    ``delay`` (its range [T_min, T_max]) and around the lagged (first-order normal response) reference, each
    max(abs, rel |T|) wide (``below_scale`` / ``above_scale`` scale one side)."""
    bs, as_ = float(params.get("below_scale", 1.0)), float(params.get("above_scale", 1.0))
    lo = min(T_min - bs * window_width(T_min, params), T_lag - bs * window_width(T_lag, params))
    hi = max(T_max + as_ * window_width(T_max, params), T_lag + as_ * window_width(T_lag, params))
    return lo, hi
