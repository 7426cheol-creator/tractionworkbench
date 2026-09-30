"""Reaction strategies: a reaction as a declared sequence of steps instead of one bridge state.

A single bridge state (ASC-low, ASC-high, six-switch-off, zero torque) is a primitive reaction.  A strategy chains
steps, each an ACTION held until its EXIT condition, so the transition into the safe state can be shaped - the
representative ways of reducing the active-short-circuit transient or of handing over between safe states:

``fw_then_asc``         freewheel (six-switch-off) first, then the ASC: below the rectification onset the currents
                        decay through the diodes before the short is applied (a timer or a measured current exit);
``current_to_asc``      soft ASC by current pre-conditioning: the current controller moves its d/q references from
                        the operating point to the ASC steady-state point of the MEASURED speed (design L_d, L_q,
                        psi, R_s) over a ramp, then the short is applied where the machine already is;
``voltage_ramp``        soft ASC by voltage ramp-down: the controller's last voltage vector is ramped to zero (the
                        zero vector the ASC applies) in the rotor frame, open loop, then the short is applied;
``sequential_asc``      phase-sequential ASC: each leg's switch of the ASC side closes when its measured current
                        flows through that side's diode (no voltage step on that phase), the other legs freewheel;
``asc_then_6so``        speed-dependent handover: ASC while fast, six-switch-off once the measured speed is below the
                        rectification onset (the low-speed ASC braking torque is avoided);
``vdc_hysteresis``      freewheel while the measured DC voltage is low, ASC above an upper threshold, back below a
                        lower one (bounds the DC link without holding the ASC braking torque);
``torque_ramp``         soft shutdown: the torque command ramps to zero at a declared rate (PWM continues), then a
                        bridge-level safe state.

Actions.  ``asc_low`` / ``asc_high`` / ``six_switch_off`` (bridge states, any path); ``torque_zero`` /
``torque_ramp`` / ``current_to_asc`` / ``voltage_ramp`` (control actions: the current control must run - software
path, MCU running); ``sequential_asc_low`` / ``sequential_asc_high`` and ``vdc_hysteresis_low`` /
``vdc_hysteresis_high`` (measured-signal actions: software path).

Exits.  ``none`` (the final step), ``time`` (the step lasts ``value`` ms), ``done`` (the action's own completion:
the ramp elapsed and the measured current reached the target, the voltage reached zero, every leg closed, the torque
command reached zero), ``i_below`` (every measured phase current below ``value`` A), ``speed_below`` (measured speed
below ``value`` rpm), ``vdc_below`` / ``vdc_above`` (measured DC voltage).  ``max_ms`` bounds any exit except
``none`` / ``time``; measured exits are evaluated by the software at its control steps (never on the plant truth).

Executability.  A step whose action the requesting path cannot execute (a control or measured-signal action on a
hardware path, or with the MCU / current control not running) is replaced by the strategy's FALLBACK bridge state
(default: its last bridge-state step) for the rest of the reaction - a soft ASC degrades to the hard ASC, and the
event log says why.  A strategy ranks in the policy's priority list by its own id when listed there, else as its
fallback state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...errors import InputValidationError

BRIDGE_ACTIONS = ("asc_low", "asc_high", "six_switch_off")
CONTROL_ACTIONS = ("torque_zero", "torque_ramp", "current_to_asc", "voltage_ramp")
LEG_ACTIONS = ("sequential_asc_low", "sequential_asc_high")
HYST_ACTIONS = ("vdc_hysteresis_low", "vdc_hysteresis_high")
ACTIONS = BRIDGE_ACTIONS + CONTROL_ACTIONS + LEG_ACTIONS + HYST_ACTIONS
EXITS = ("none", "time", "done", "i_below", "speed_below", "vdc_below", "vdc_above")
MEASURED_EXITS = ("i_below", "speed_below", "vdc_below", "vdc_above")

# action -> declared parameters: (key, unit, default, what it is)
ACTION_PARAMS = {
    "torque_ramp": (("rate_Nm_per_ms", "N*m/ms", 20.0, "ramp rate of the torque command towards zero"),),
    "current_to_asc": (("ramp_ms", "ms", 3.0, "ramp time of the d/q current references to the ASC point"),
                       ("tol_A", "A", 40.0, "the measured current must be this close to the ASC point ('done')")),
    "voltage_ramp": (("ramp_ms", "ms", 2.0, "ramp time of the voltage vector to zero"),),
    "sequential_asc_low": (("i_zero_A", "A", 5.0, "a leg closes once its measured current is above -i_zero "
                                                  "(the lower diode conducts)"),),
    "sequential_asc_high": (("i_zero_A", "A", 5.0, "a leg closes once its measured current is below +i_zero "
                                                   "(the upper diode conducts)"),),
    "vdc_hysteresis_low": (("v_on_V", "V", 740.0, "measured DC voltage at which the ASC is applied"),
                           ("v_off_V", "V", 700.0, "measured DC voltage below which it freewheels again")),
    "vdc_hysteresis_high": (("v_on_V", "V", 740.0, "measured DC voltage at which the ASC is applied"),
                            ("v_off_V", "V", 700.0, "measured DC voltage below which it freewheels again")),
}
# which exits make sense for which action
EXITS_FOR = {**{a: ("none", "time", "i_below", "speed_below", "vdc_below", "vdc_above") for a in BRIDGE_ACTIONS},
             "torque_zero": ("none", "time"), "torque_ramp": ("none", "time", "done"),
             "current_to_asc": ("time", "done"), "voltage_ramp": ("time", "done"),
             **{a: ("none", "time", "done") for a in LEG_ACTIONS},
             **{a: ("none", "time", "speed_below") for a in HYST_ACTIONS}}


def action_kind(action: str) -> str:
    if action in BRIDGE_ACTIONS:
        return "bridge"
    if action in CONTROL_ACTIONS:
        return "control"
    if action in LEG_ACTIONS:
        return "legs"
    return "hysteresis"


@dataclass(frozen=True)
class StepSpec:
    action: str
    exit: str = "none"
    value: float | None = None          # the exit's value (ms for time, A, rpm, V)
    max_s: float | None = None          # timeout of a non-time exit
    params: dict = field(default_factory=dict)

    def validate(self, where: str) -> None:
        if self.action not in ACTIONS:
            raise InputValidationError(f"step action must be one of {ACTIONS}", field=f"{where}.action")
        if self.exit not in EXITS:
            raise InputValidationError(f"step exit must be one of {EXITS}", field=f"{where}.exit")
        if self.exit not in EXITS_FOR[self.action]:
            raise InputValidationError(f"exit {self.exit!r} does not apply to {self.action!r} (use one of "
                                       f"{EXITS_FOR[self.action]})", field=f"{where}.exit")
        if self.exit in ("time",) + MEASURED_EXITS:
            if self.value is None or not math.isfinite(float(self.value)) or float(self.value) < 0:
                raise InputValidationError(f"exit {self.exit!r} needs a finite value >= 0", field=f"{where}.value")
        if self.max_s is not None and not (math.isfinite(self.max_s) and self.max_s > 0):
            raise InputValidationError("a step's maximum time must be finite and > 0", field=f"{where}.max_ms")
        for k, unit, _d, _w in ACTION_PARAMS.get(self.action, ()):
            v = self.params.get(k)
            if v is not None and not (isinstance(v, (int, float)) and math.isfinite(float(v)) and float(v) >= 0):
                raise InputValidationError(f"{k} must be a finite number >= 0 [{unit}]", field=f"{where}.params.{k}")
        if self.action in ("current_to_asc", "voltage_ramp") and float(self.param("ramp_ms")) <= 0:
            raise InputValidationError("a ramp needs ramp_ms > 0", field=f"{where}.params.ramp_ms")
        if self.action in HYST_ACTIONS and not float(self.param("v_off_V")) < float(self.param("v_on_V")):
            raise InputValidationError("the hysteresis needs v_off_V < v_on_V", field=f"{where}.params")

    def param(self, key: str):
        for k, _u, d, _w in ACTION_PARAMS.get(self.action, ()):
            if k == key:
                return self.params.get(k, d) if self.params.get(k) is not None else d
        return self.params.get(key)

    def to_dict(self) -> dict:
        return {"action": self.action, "exit": self.exit, "value": self.value,
                "max_ms": None if self.max_s is None else self.max_s * 1e3, "params": dict(self.params)}


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    steps: tuple
    fallback: str | None = None       # bridge state when a step cannot execute (default: the last bridge step)
    text: str = ""
    basis: str = ""

    def __post_init__(self):
        where = f"strategies.{self.strategy_id}"
        if not self.strategy_id or not str(self.strategy_id).strip():
            raise InputValidationError("a strategy needs an id", field="strategies.id")
        if not self.steps:
            raise InputValidationError("a strategy needs at least one step", field=f"{where}.steps")
        for i, s in enumerate(self.steps):
            s.validate(f"{where}.steps[{i}]")
            if i < len(self.steps) - 1 and s.exit == "none":
                raise InputValidationError(f"step {i + 1} never ends (exit 'none') but steps follow it",
                                           field=f"{where}.steps[{i}].exit")
        if self.steps[-1].exit != "none":
            raise InputValidationError("the last step must hold (exit 'none'): a strategy ends in a state",
                                       field=f"{where}.steps[{len(self.steps) - 1}].exit")
        if self.fallback is not None and self.fallback not in BRIDGE_ACTIONS:
            raise InputValidationError(f"a strategy's fallback must be a bridge state {BRIDGE_ACTIONS}",
                                       field=f"{where}.fallback")

    @property
    def fallback_state(self) -> str:
        """The bridge state a non-executable step degrades to."""
        if self.fallback:
            return self.fallback
        for s in reversed(self.steps):
            if s.action in BRIDGE_ACTIONS:
                return s.action
            if s.action in ("sequential_asc_low", "vdc_hysteresis_low"):
                return "asc_low"
            if s.action in ("sequential_asc_high", "vdc_hysteresis_high"):
                return "asc_high"
        return "six_switch_off"

    def needs_software(self) -> bool:
        return any(action_kind(s.action) != "bridge" or s.exit in MEASURED_EXITS for s in self.steps)

    def min_duration_s(self) -> float:
        """Lower bound of the time before the final step starts: time exits exactly, a voltage / current ramp at
        least its ramp time when it ends by 'done' (a maximum time shorter than the ramp ends it earlier), any other
        exit possibly at once."""
        tot = 0.0
        for s in self.steps[:-1]:
            if s.exit == "time":
                tot += float(s.value) * 1e-3
            elif s.exit == "done" and s.action in ("current_to_asc", "voltage_ramp"):
                ramp = float(s.param("ramp_ms")) * 1e-3
                tot += ramp if s.max_s is None else min(ramp, s.max_s)
        return tot

    def static_duration_s(self) -> float | None:
        """Upper bound of the time before the final step starts, from the declared exits (None: unbounded - a
        measured or 'done' exit without max_ms)."""
        tot = 0.0
        for s in self.steps[:-1]:
            if s.exit == "time":
                tot += float(s.value) * 1e-3
            elif s.max_s is not None:
                tot += s.max_s
            else:
                return None
        return tot

    def to_dict(self) -> dict:
        return {"id": self.strategy_id, "steps": [s.to_dict() for s in self.steps], "fallback": self.fallback,
                "text": self.text, "basis": self.basis}


def strategy_from_dict(d: dict) -> StrategySpec:
    steps = []
    for s in d.get("steps") or []:
        v = s.get("value")
        mx = s.get("max_ms")
        steps.append(StepSpec(str(s.get("action", "")), str(s.get("exit", "none")),
                              None if v in (None, "") else float(v),
                              None if mx in (None, "") else float(mx) * 1e-3, dict(s.get("params") or {})))
    return StrategySpec(str(d.get("id", "")), tuple(steps), d.get("fallback") or None, str(d.get("text", "")),
                        str(d.get("basis", "")))


def strategies_from(data: dict) -> dict:
    out = {}
    for d in data.get("strategies") or []:
        s = strategy_from_dict(d)
        if s.strategy_id in out:
            raise InputValidationError(f"strategy id {s.strategy_id!r} is declared twice", field="strategies")
        out[s.strategy_id] = s
    return out


def asc_steady_point(w_e: float, Ld: float, Lq: float, psi: float, Rs: float) -> tuple[float, float]:
    """Steady-state d/q currents of the three-phase short (v_d = v_q = 0) at electrical speed w_e:
    i_d = -psi w^2 L_q / (R^2 + w^2 L_d L_q),  i_q = -psi w R / (R^2 + w^2 L_d L_q)."""
    den = Rs * Rs + w_e * w_e * Ld * Lq
    if den <= 0:
        return 0.0, 0.0
    return -psi * w_e * w_e * Lq / den, -psi * w_e * Rs / den


# ------------------------------------------------------------------------------------------ representative templates

def _t(sid, steps, text_en, text_ko, basis):
    return {"id": sid, "steps": steps, "text": text_en, "text_ko": text_ko, "basis": basis}


TEMPLATES = {
    "fw_then_asc": _t("FW2_ASC_LOW", [
        {"action": "six_switch_off", "exit": "time", "value": 2.0},
        {"action": "asc_low", "exit": "none"}],
        "freewheel 2 ms, then ASC-low", "2 ms 프리휠(6SO) 후 ASC-low",
        "the currents decay through the diodes before the short (useful below the rectification onset; above it "
        "the freewheel charges the DC link)"),
    "current_to_asc": _t("SOFT_ASC_I", [
        {"action": "current_to_asc", "exit": "done", "max_ms": 8.0, "params": {"ramp_ms": 3.0, "tol_A": 40.0}},
        {"action": "asc_low", "exit": "none"}],
        "soft ASC: current references ramped to the ASC point in 3 ms, then ASC-low",
        "소프트 ASC(전류 사전 조정): 3 ms 동안 전류 지령을 ASC 정상점으로 옮긴 뒤 ASC-low",
        "the short is applied where the machine already is: no d-axis overshoot (needs working current control)"),
    "voltage_ramp": _t("SOFT_ASC_V", [
        {"action": "voltage_ramp", "exit": "done", "max_ms": 6.0, "params": {"ramp_ms": 2.0}},
        {"action": "asc_low", "exit": "none"}],
        "soft ASC: voltage vector ramped to zero in 2 ms, then ASC-low",
        "소프트 ASC(전압 램프): 2 ms 동안 전압 벡터를 0으로 줄인 뒤 ASC-low",
        "the zero vector the ASC applies is reached gradually (open loop, needs the PWM and the angle)"),
    "sequential_asc": _t("SEQ_ASC_LOW", [
        {"action": "sequential_asc_low", "exit": "done", "max_ms": 5.0, "params": {"i_zero_A": 5.0}},
        {"action": "asc_low", "exit": "none"}],
        "phase-sequential ASC-low: each lower switch closes while its diode conducts",
        "상별 순차 ASC-low: 각 상은 하단 다이오드가 도통할 때 하단 스위치를 닫음",
        "no voltage step on any phase at its closing; the open legs freewheel meanwhile"),
    "asc_then_6so": _t("ASC_THEN_6SO", [
        {"action": "asc_low", "exit": "speed_below", "value": 3000.0},
        {"action": "six_switch_off", "exit": "none"}],
        "ASC-low while fast, six-switch-off below 3000 rpm", "고속은 ASC-low, 3,000 rpm 아래에서 6SO로 전환",
        "hands over below the rectification onset: the low-speed ASC braking torque is avoided"),
    "vdc_hysteresis": _t("HYST_6SO_ASC", [
        {"action": "vdc_hysteresis_low", "exit": "none", "params": {"v_on_V": 740.0, "v_off_V": 700.0}}],
        "freewheel, ASC-low above 740 V until below 700 V (hysteresis)",
        "프리휠, 740 V 넘으면 ASC-low, 700 V 아래로 내려가면 다시 프리휠(히스테리시스)",
        "bounds the DC link after a battery disconnect without holding the ASC braking torque"),
    "torque_ramp": _t("SOFT_TQ_6SO", [
        {"action": "torque_ramp", "exit": "done", "max_ms": 20.0, "params": {"rate_Nm_per_ms": 20.0}},
        {"action": "six_switch_off", "exit": "none"}],
        "soft shutdown: torque command ramped to zero at 20 N*m/ms, then six-switch-off",
        "소프트 셧다운: 토크 지령을 20 N·m/ms로 0까지 줄인 뒤 6SO",
        "for faults that leave the control working; not a reaction for control or sensor faults"),
}
