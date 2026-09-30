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
            "position_los", "command_timeout")
HW_KINDS = ("overcurrent_hw", "overvoltage_hw", "desat", "gate_uvlo", "watchdog")
REACTIONS = ("safe_state", "asc_low", "asc_high", "six_switch_off", "torque_zero", "report_only")
BRIDGE_REACTIONS = ("asc_low", "asc_high", "six_switch_off")
RULE_KEYS = ("speed_above_rpm", "speed_below_rpm", "vdc_above_V", "vdc_below_V", "detected_by", "device",
             "uvlo", "mechanism")
REQUIRED_PARAMS = {"current_plausibility": ("threshold_A",), "overcurrent_sw": ("threshold_A",),
                   "overvoltage_sw": ("threshold_V",), "undervoltage_sw": ("threshold_V",),
                   "command_timeout": ("timeout_s",), "overcurrent_hw": ("threshold_A",),
                   "overvoltage_hw": ("threshold_V",), "desat": ("threshold_A",), "watchdog": ("timeout_s",),
                   "torque_monitor": ("abs_Nm",)}


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
        if self.fixed_reaction is not None and self.fixed_reaction not in REACTIONS:
            raise InputValidationError(f"fixed reaction must be one of {REACTIONS}",
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
        if self.kind not in SW_KINDS + HW_KINDS:
            raise InputValidationError(f"mechanism kind must be one of {SW_KINDS + HW_KINDS}",
                                       field=f"mechanisms.{self.mech_id}.kind")
        if self.reaction not in REACTIONS:
            raise InputValidationError(f"reaction must be one of {REACTIONS}", field=f"mechanisms.{self.mech_id}")
        if self.kind in SW_KINDS and not (self.period_s and self.period_s > 0):
            raise InputValidationError("a software mechanism needs its task period",
                                       field=f"mechanisms.{self.mech_id}.period_s")
        for k in REQUIRED_PARAMS.get(self.kind, ()):
            if k not in self.params:
                raise InputValidationError(f"a {self.kind} mechanism needs {k}",
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

    def __post_init__(self):
        for i, r in enumerate(self.rules):
            if "else" in r:
                if r["else"] not in REACTIONS:
                    raise InputValidationError(f"rule reaction must be one of {REACTIONS}", field=f"policy.rules[{i}]")
                continue
            if r.get("then") not in REACTIONS or r.get("then") == "safe_state":
                raise InputValidationError("a rule needs 'then': a concrete reaction", field=f"policy.rules[{i}]")
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
