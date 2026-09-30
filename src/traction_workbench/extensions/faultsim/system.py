"""The system layer around one inverter's fault simulation: vehicle inputs, the safety supervisor and the operating
state manager, the supply network, the vehicle torque interface, a second machine on the DC link, the DC-link
discharge and the self-tests.

The electrical engine (``engine.py``) knows the bridge, the plant, the sensors, the control and the safety
mechanisms.  This layer adds what decides WHEN the inverter must be in a safe state and whether it may leave it, what
powers the parts that act, and what the vehicle sends - each a declared, optional component; a scenario without a
``system`` runs exactly as before.

Inputs.  A schedule of vehicle signals (terminal-15 software and hardware state, the target mode on the bus, the
enables of the operating-state guards, the speed-limit request and its qualifier, the intervention torque, the
extended-torque request, the terminal-30 supply state, a discharge request, DTC clear and reset requests) changes at
declared instants; everything that reads them reads this schedule, nothing reads the fault list.

Supervisor.  Keeps the REASONS the safe state is required - bus standby, a no-torque target mode, terminal 15
software off, terminal 15 hardware off, a reset, an isolated terminal-30 supply condition, the default-error latch, a
failed latent-path test - each with its own entry and release; the normal torque permit is given only when no reason
holds.  A reason's safe state is requested through a declared reaction path (the same reaction manager, priorities
and strategies as a fault reaction).  The default-error latch is set by every executed fault reaction and released
only by the declared re-arm contract (terminal 15 software and hardware off -> on after the latch, in any order with
an optional maximum gap; software only; the level of both - a variant kept to show why it is not approved; never)
once the triggering condition is gone; a reset keeps it only with non-volatile retention, a DTC clear never releases
it unless the (non-approved) variant says so.

Operating states.  PowerOff -> STNDBY -> IDLE -> RUN / LHOM, DIAG, FAULT with the guards and timers of the declared
state manager (STNDBY -> IDLE not before a guard time after power-up and within a completion time with valid
signals; IDLE -> STNDBY after ignition off, low HV and low speed held for a persistence time; IDLE -> RUN on HV
enable, self-test done, ASC release and torque generation request; RUN -> IDLE on any of their withdrawals); each
state allows a set of power-stage states (ASO = six-switch-off, APS = active short circuit, PWM) and a state that
does not allow PWM holds the passive stage its rule selects (APS above a speed, ASO below).

Supply.  Low-voltage (terminal 30) and HV-derived sources (the latter available while the true DC-link voltage is
above a minimum, with hysteresis); rails fed by any of their sources keep their loads for a hold-up time after the
last source is gone and need a restart time when one returns; a rail's loss is the loss of the resources it powers
(the MCU, the gate supplies of each side, the logic device, the sensor supply) - the engine's common-cause mechanism.

Torque interface.  An end-to-end protected receive path (counter, data id, machine id, CRC; the newest ACCEPTED
frame is used; a repeated counter is not a new update and does not refresh the age), the speed-limit fallback
(an invalid wheel speed, an invalid or missing speed-limit request or qualifier, a limited qualifier or a wheel speed
above the request limit the positive torque to a safe value), the available-torque envelope with the intervention
torque (T_sum = request + intervention clamped to the maximum when positive, to the minimum when negative) and the
temporary positive-side extended torque (up to a minimum extended torque while requested, reverted after a maximum
time; never outside voltage-control mode, never on the negative side).

Second machine.  A reduced model on the shared DC link: its DC power follows a declared schedule while it operates;
after its reaction (the global contract: when this inverter reacts, after a delay; local: never) it holds an active
short circuit (no DC exchange) or freewheels (first-harmonic uncontrolled-rectifier current when its line-to-line
back-EMF exceeds the DC-link voltage).

Discharge.  A request opens the contactor (if declared) and switches the active-discharge resistor on; the
completion is indicated from the MEASURED DC voltage below the limit for a confirmation time - so a sensor fault can
indicate a false completion, which the judge compares with the true voltage.

Self-tests.  A gate test pulses the switches of one side for a declared time, only when its entry conditions hold
(measured speed and DC voltage, operating state) - it is refused otherwise and never enables normal PWM; a latent
path test checks the declared reaction paths at the start and periodically and blocks the torque permit when one is
lost.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...errors import InputValidationError

SIGNALS = {"kl15_sw": 1, "kl15_hw": 1, "target_mode": "run", "hv_enable": 1, "spt_done": 1, "aps_release": 1,
           "trq_gen_rq": 1, "rol_actv": 0, "pwr_stg_ctrl": 0, "ignition": 1, "energy_flow_block": 0,
           "t30_state": "normal", "discharge_request": 0, "wheel_speed_valid": 1, "wheel_speed_kph": 0.0,
           "speed_limit_valid": 1, "speed_limit_kph": 250.0, "speed_limit_qualifier": "ok", "intervention_Nm": 0.0,
           "ext_request": 0, "voltage_control_mode": 0, "dtc_clear": 0, "reset_request": 0}
PULSES = ("dtc_clear", "reset_request")
TARGET_MODES = ("run", "standby", "no_torque")
STATES = ("POWER_OFF", "STNDBY", "IDLE", "DIAG", "RUN", "LHOM", "FAULT")
STATE_CODES = {s: i for i, s in enumerate(STATES)}
STAGES = ("ASO", "APS", "PWM")
DEFAULT_ALLOW = {"POWER_OFF": ("ASO",), "STNDBY": ("ASO", "APS"), "IDLE": ("ASO", "APS"),
                 "DIAG": ("ASO", "APS", "PWM"), "RUN": ("ASO", "PWM"), "LHOM": ("ASO", "PWM"),
                 "FAULT": ("ASO", "APS")}
REASONS = ("standby", "no_torque", "kl15_sw_off", "kl15_hw_off", "reset", "supply_condition", "default_error",
           "latent_path")
REARM = ("kl15_sw_hw_off_on", "kl15_sw_off_on", "kl15_hw_off_on", "kl15_level_and", "never")
BRIDGE_STAGE = {"pwm": "PWM", "asc_low": "APS", "asc_high": "APS", "seq_asc_low": "APS", "seq_asc_high": "APS",
                "six_switch_off": "ASO", "off": "ASO", "test": "TEST"}


# the transition guards of the operating-state manager as pure functions of the signals (used by the state
# manager and by the truth-table check of the requirement formula)
def g_idle_to_run(s) -> bool:
    return bool(s["hv_enable"]) and bool(s["spt_done"]) and bool(s["aps_release"]) and bool(s["trq_gen_rq"])


def g_run_to_idle(s) -> bool:
    return (not s["hv_enable"]) or bool(s["rol_actv"]) or bool(s["pwr_stg_ctrl"]) or (not s["trq_gen_rq"])


def g_idle_to_stndby(s) -> bool:
    return (not s["ignition"]) and bool(s["hv_low"]) and bool(s["spd_low"])


GUARDS = {"IDLE->RUN": (g_idle_to_run, ("hv_enable", "spt_done", "aps_release", "trq_gen_rq")),
          "RUN->IDLE": (g_run_to_idle, ("hv_enable", "rol_actv", "pwr_stg_ctrl", "trq_gen_rq")),
          "IDLE->STNDBY": (g_idle_to_stndby, ("ignition", "hv_low", "spd_low"))}


def _pos(v, name, allow_zero=True):
    if v is None:
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or \
            (not allow_zero and v == 0):
        raise InputValidationError(f"{name} must be a finite number {'>=' if allow_zero else '>'} 0 (got {v!r})",
                                   field=f"system.{name}")


# ------------------------------------------------------------------------------------------------- declarations

@dataclass(frozen=True)
class InputEvent:
    t_s: float
    signal: str
    value: object

    def __post_init__(self):
        if self.signal not in SIGNALS:
            raise InputValidationError(f"input signal must be one of {sorted(SIGNALS)}", field="system.inputs.signal")
        _pos(self.t_s, "inputs.t_ms")
        if self.signal == "target_mode" and self.value not in TARGET_MODES:
            raise InputValidationError(f"target_mode must be one of {TARGET_MODES}", field="system.inputs.value")


@dataclass(frozen=True)
class SupervisorSpec:
    path: str = "SW"                       # the reaction path of a reason's safe-state request
    reaction: str = "safe_state"           # the default reaction (a policy decision) of a reason
    reactions: dict = field(default_factory=dict)      # reason -> reaction (e.g. no_torque -> six_switch_off)
    paths: dict = field(default_factory=dict)          # reason -> path (e.g. kl15_hw_off -> HW)
    rearm: str = "kl15_sw_hw_off_on"
    rearm_max_gap_s: float | None = None
    latch_retention: str = "nvm"           # nvm: a reset keeps the default-error latch; ram: a reset loses it
    latch_protection: bool = True          # a corrupted latch word is detected (complement check) and kept set
    dtc_clear_releases: bool = False       # non-approved variant: a DTC clear releases the latch
    latch_ignore: tuple = ()               # mechanism ids whose reaction does not set the default-error latch
    readiness_s: float = 0.0               # the triggering condition must be absent this long before a release
    supply_recover_s: float = 0.01         # an isolated terminal-30 condition clears this long after it is normal
    confirm_i_A: float = 20.0              # six-switch-off is confirmed only with every measured current below this
    task_s: float = 1e-3

    def __post_init__(self):
        if self.rearm not in REARM:
            raise InputValidationError(f"re-arm contract must be one of {REARM}", field="system.supervisor.rearm")
        if self.latch_retention not in ("nvm", "ram"):
            raise InputValidationError("latch retention must be nvm or ram", field="system.supervisor.latch_retention")
        for r in list(self.reactions) + list(self.paths):
            if r not in REASONS:
                raise InputValidationError(f"reason must be one of {REASONS}", field="system.supervisor.reactions")
        _pos(self.task_s, "supervisor.task_ms", allow_zero=False)
        _pos(self.readiness_s, "supervisor.readiness_ms")
        _pos(self.rearm_max_gap_s, "supervisor.rearm_max_gap_ms")


@dataclass(frozen=True)
class OpStateSpec:
    initial: str = "RUN"
    hv_low_V: float = 60.0
    spd_low_rpm: float = 300.0
    guard_stndby_idle_s: float = 0.05      # STNDBY -> IDLE not before this after PowerOff -> STNDBY
    complete_stndby_idle_s: float = 0.15   # ... and, with valid signals, within this
    persist_idle_stndby_s: float = 1.0     # ignition off + low HV + low speed held this long: IDLE -> STNDBY
    allow: dict = field(default_factory=lambda: dict(DEFAULT_ALLOW))
    aps_above_rpm: float = 3000.0          # the passive stage: APS (ASC) above this measured speed, else ASO
    aps_side: str = "asc_low"
    lhom: bool = False                     # RUN is reported as LHOM (a limp-home run)

    def __post_init__(self):
        if self.initial not in STATES:
            raise InputValidationError(f"initial state must be one of {STATES}", field="system.opstate.initial")
        for s, a in self.allow.items():
            if s not in STATES or any(x not in STAGES for x in a):
                raise InputValidationError(f"allow: state in {STATES}, stages in {STAGES}", field="system.opstate.allow")
        if self.aps_side not in ("asc_low", "asc_high"):
            raise InputValidationError("aps_side must be asc_low or asc_high", field="system.opstate.aps_side")
        for k in ("hv_low_V", "spd_low_rpm", "guard_stndby_idle_s", "complete_stndby_idle_s", "persist_idle_stndby_s",
                  "aps_above_rpm"):
            _pos(getattr(self, k), f"opstate.{k}")


@dataclass(frozen=True)
class RailSpec:
    name: str
    sources: tuple = ("LV",)               # LV (terminal 30) and / or HV (DC-link derived)
    hold_up_s: float = 0.0
    restart_s: float = 0.0
    resources: tuple = ()

    def __post_init__(self):
        if not self.sources or any(s not in ("LV", "HV") for s in self.sources):
            raise InputValidationError("rail sources must be LV and / or HV", field=f"system.supply.{self.name}")
        _pos(self.hold_up_s, f"supply.{self.name}.hold_up_ms")
        _pos(self.restart_s, f"supply.{self.name}.restart_ms")


@dataclass(frozen=True)
class SupplySpec:
    hv_min_V: float = 40.0
    hv_hyst_V: float = 2.0
    transfer_gap_s: float = 0.0            # a rail switching its source is unpowered this long (0: seamless)
    rails: tuple = ()

    def __post_init__(self):
        _pos(self.hv_min_V, "supply.hv_min_V")
        _pos(self.hv_hyst_V, "supply.hv_hyst_V")
        _pos(self.transfer_gap_s, "supply.transfer_gap_us")
        names = [r.name for r in self.rails]
        if len(set(names)) != len(names):
            raise InputValidationError("rail names must be unique", field="system.supply.rails")


@dataclass(frozen=True)
class InterfaceSpec:
    e2e: dict | None = None                # {period_s, latency_s, counter_mod, max_delta}
    fallback: dict | None = None           # {safe_value_Nm} or {axle_torque_Nm, wheel_ratio} (e2e variant)
    envelope: dict | None = None           # {max_Nm, min_Nm} (static) - the available-torque envelope
    extended: dict | None = None           # {c_m_min_Nm, c_t_imax_s}

    def __post_init__(self):
        if self.e2e is not None:
            _pos(self.e2e.get("period_s"), "interface.e2e.period_ms", allow_zero=False)
        if self.extended is not None:
            _pos(self.extended.get("c_t_imax_s"), "interface.extended.c_t_imax_ms", allow_zero=False)


@dataclass(frozen=True)
class Em2Spec:
    power_W: tuple = ((0.0, 0.0),)         # (t_s, P) steps: the second machine's DC power (+ motoring)
    speed_rpm: float = 0.0
    p: int = 4
    psi: float = 0.08
    Ld: float = 2e-4
    Lq: float = 4e-4
    Rs: float = 0.01
    contract: str = "global"               # global: it reacts when this inverter reacts; local: it keeps running
    delay_s: float = 0.0
    state: str = "asc"                     # its safe state: asc | six_switch_off

    def __post_init__(self):
        if self.contract not in ("global", "local"):
            raise InputValidationError("contract must be global or local", field="system.em2.contract")
        if self.state not in ("asc", "six_switch_off"):
            raise InputValidationError("state must be asc or six_switch_off", field="system.em2.state")


@dataclass(frozen=True)
class DischargeSpec:
    v_limit_V: float = 60.0
    confirm_s: float = 0.01
    open_contactor: bool = True


@dataclass(frozen=True)
class SelfTestSpec:
    tests: tuple = ()                      # ({t_s, legs, side, pulse_s, speed_max_rpm, vdc_max_V, states}, ...)
    path_test_period_s: float | None = None
    path_test_at_start: bool = False
    paths: tuple = ()


@dataclass(frozen=True)
class SystemSpec:
    inputs: tuple = ()
    initial: dict = field(default_factory=dict)
    supervisor: SupervisorSpec | None = None
    opstate: OpStateSpec | None = None
    supply: SupplySpec | None = None
    interface: InterfaceSpec | None = None
    em2: Em2Spec | None = None
    discharge: DischargeSpec | None = None
    selftest: SelfTestSpec | None = None

    def __post_init__(self):
        for k in self.initial:
            if k not in SIGNALS:
                raise InputValidationError(f"initial signal must be one of {sorted(SIGNALS)}",
                                           field="system.initial")


def _ms(d: dict, k: str, default=None):
    v = d.get(k)
    return default if v is None else float(v) * 1e-3


def system_from_dict(d: dict | None) -> SystemSpec | None:
    """The declared system components (times in ms in the data)."""
    if not d:
        return None
    sup = d.get("supervisor")
    ops = d.get("opstate")
    sp = d.get("supply")
    itf = d.get("interface")
    em2 = d.get("em2")
    dis = d.get("discharge")
    stt = d.get("selftest")
    inputs = tuple(InputEvent(float(e.get("t_ms", 0.0)) * 1e-3, str(e["signal"]), e.get("value"))
                   for e in (d.get("inputs") or ()))
    sup_s = None if sup is None else SupervisorSpec(
        path=str(sup.get("path", "SW")), reaction=str(sup.get("reaction", "safe_state")),
        reactions=dict(sup.get("reactions") or {}), paths=dict(sup.get("paths") or {}),
        rearm=str(sup.get("rearm", "kl15_sw_hw_off_on")), rearm_max_gap_s=_ms(sup, "rearm_max_gap_ms"),
        latch_retention=str(sup.get("latch_retention", "nvm")), latch_protection=bool(sup.get("latch_protection", True)),
        dtc_clear_releases=bool(sup.get("dtc_clear_releases", False)), latch_ignore=tuple(sup.get("latch_ignore") or ()),
        readiness_s=_ms(sup, "readiness_ms", 0.0), supply_recover_s=_ms(sup, "supply_recover_ms", 0.01),
        confirm_i_A=float(sup.get("confirm_i_A", 20.0)),
        task_s=_ms(sup, "task_ms", 1e-3))
    ops_s = None if ops is None else OpStateSpec(
        initial=str(ops.get("initial", "RUN")), hv_low_V=float(ops.get("hv_low_V", 60.0)),
        spd_low_rpm=float(ops.get("spd_low_rpm", 300.0)),
        guard_stndby_idle_s=_ms(ops, "guard_stndby_idle_ms", 0.05),
        complete_stndby_idle_s=_ms(ops, "complete_stndby_idle_ms", 0.15),
        persist_idle_stndby_s=_ms(ops, "persist_idle_stndby_ms", 1.0),
        allow={k: tuple(v) for k, v in (ops.get("allow") or DEFAULT_ALLOW).items()},
        aps_above_rpm=float(ops.get("aps_above_rpm", 3000.0)), aps_side=str(ops.get("aps_side", "asc_low")),
        lhom=bool(ops.get("lhom", False)))
    sp_s = None if sp is None else SupplySpec(
        hv_min_V=float(sp.get("hv_min_V", 40.0)), hv_hyst_V=float(sp.get("hv_hyst_V", 2.0)),
        transfer_gap_s=float(sp.get("transfer_gap_us", 0.0)) * 1e-6,
        rails=tuple(RailSpec(str(r["name"]), tuple(r.get("sources") or ("LV",)), _ms(r, "hold_up_ms", 0.0),
                             _ms(r, "restart_ms", 0.0), tuple(r.get("resources") or ())) for r in sp.get("rails") or ()))
    itf_s = None
    if itf is not None:
        e2e = itf.get("e2e")
        itf_s = InterfaceSpec(
            e2e=None if e2e is None else {"period_s": _ms(e2e, "period_ms"), "latency_s": _ms(e2e, "latency_ms", 0.0),
                                          "counter_mod": int(e2e.get("counter_mod", 16)),
                                          "max_delta": int(e2e.get("max_delta", 2))},
            fallback=itf.get("fallback"), envelope=itf.get("envelope"),
            extended=None if itf.get("extended") is None else {
                "c_m_min_Nm": float(itf["extended"]["c_m_min_Nm"]), "c_t_imax_s": _ms(itf["extended"], "c_t_imax_ms")})
    em2_s = None if em2 is None else Em2Spec(
        power_W=tuple((float(a) * 1e-3, float(b)) for a, b in (em2.get("power_W_ms") or ((0.0, 0.0),))),
        speed_rpm=float(em2.get("speed_rpm", 0.0)), p=int(em2.get("p", 4)), psi=float(em2.get("psi_Wb", 0.08)),
        Ld=float(em2.get("Ld_uH", 200.0)) * 1e-6, Lq=float(em2.get("Lq_uH", 400.0)) * 1e-6,
        Rs=float(em2.get("Rs_mohm", 10.0)) * 1e-3, contract=str(em2.get("contract", "global")),
        delay_s=_ms(em2, "delay_ms", 0.0), state=str(em2.get("state", "asc")))
    dis_s = None if dis is None else DischargeSpec(float(dis.get("v_limit_V", 60.0)), _ms(dis, "confirm_ms", 0.01),
                                                    bool(dis.get("open_contactor", True)))
    stt_s = None
    if stt is not None:
        tests = tuple({"t_s": float(x.get("t_ms", 0.0)) * 1e-3, "legs": tuple(x.get("legs") or ("a", "b", "c")),
                       "side": str(x.get("side", "lower")), "pulse_s": float(x.get("pulse_us", 10.0)) * 1e-6,
                       "speed_max_rpm": x.get("speed_max_rpm"), "vdc_max_V": x.get("vdc_max_V"),
                       "states": tuple(x.get("states") or ())} for x in stt.get("tests") or ())
        stt_s = SelfTestSpec(tests, _ms(stt, "path_test_period_ms"), bool(stt.get("path_test_at_start", False)),
                             tuple(stt.get("paths") or ()))
    return SystemSpec(inputs=inputs, initial=dict(d.get("initial") or {}), supervisor=sup_s, opstate=ops_s,
                      supply=sp_s, interface=itf_s, em2=em2_s, discharge=dis_s, selftest=stt_s)


# ------------------------------------------------------------------------------------------------- the layer

def em2_rectifier_current(sp: Em2Spec, v_dc: float) -> float:
    """First-harmonic uncontrolled rectification of the second machine (A into the DC link, >= 0): the diode bridge
    presents a fundamental phase voltage 2 Vdc / pi in phase with the current; |E|^2 = (V1 + R I)^2 + (w L I)^2 with
    L = (Ld + Lq) / 2 (a non-salient approximation, declared); no current while E <= V1."""
    w_e = sp.p * sp.speed_rpm * 2.0 * math.pi / 60.0
    E = abs(w_e) * sp.psi
    V1 = 2.0 * max(v_dc, 0.0) / math.pi
    if E <= V1:
        return 0.0
    X = abs(w_e) * 0.5 * (sp.Ld + sp.Lq)
    R = sp.Rs
    # (R^2 + X^2) I^2 + 2 V1 R I + V1^2 - E^2 = 0
    a, b, c = R * R + X * X, 2.0 * V1 * R, V1 * V1 - E * E
    I = (-b + math.sqrt(max(b * b - 4 * a * c, 0.0))) / (2 * a)
    return 1.5 * V1 * I / max(v_dc, 1e-9)


class SystemLayer:
    """The runtime of the declared components for one run (see the module note)."""

    def __init__(self, spec: SystemSpec):
        self.spec = spec
        self.sig = dict(SIGNALS)
        self.sig.update(spec.initial)
        self.edges: list = []             # (t, signal, value) of terminal-15 changes
        self.reasons: dict = {}           # reason -> since (active reasons)
        self.latch = None                 # {"t", "by", "reaction"} while the default-error latch is set
        self.latch_lost_word = False
        self.requested: dict = {}         # reason -> the pseudo mechanism state that requested its safe state
        self.state = spec.opstate.initial if spec.opstate else None
        self.state_since = 0.0
        self.state_log: list = []
        self.transitions: list = []
        self.allow_violations: list = []
        self.permit = True
        self.stndby_power_up = None
        self.idle_low_since = None
        self.rails: dict = {}
        self.src = {"LV": True, "HV": True, "HV_fault": False}
        self.rx = None
        self.ext = {"active": False, "since": None, "expired": False}
        self.em2_reacted_at = None
        self.dis = {"on": False, "t_req": None, "below_since": None, "complete_at": None}
        self.test = None
        self.t30_normal_since = None
        self.path_test_failed = None
        self._mechs: dict = {}
        self.owns_recovery = spec.supervisor is not None
        self.confirmed = False
        self.confirm_log: list = []

    # -- helpers -----------------------------------------------------------------------------------------------
    def channels(self) -> tuple:
        ch = ["sys_permit", "sys_reasons", "sys_latch", "sys_confirmed"]
        if self.spec.opstate:
            ch.append("sys_state")
        if self.spec.supply:
            ch += ["src_LV", "src_HV"] + [f"rail_{r.name}" for r in self.spec.supply.rails]
        if self.spec.interface:
            ch += ["itf_pos_limit", "itf_ext_active", "itf_fallback", "rx_age"]
        if self.spec.em2:
            ch.append("em2_i_dc")
        if self.spec.discharge:
            ch += ["dis_on", "dis_complete"]
        for s in ("kl15_sw", "kl15_hw"):
            ch.append(f"in_{s}")
        return tuple(ch)

    def sample(self, sim) -> dict:
        out = {"sys_permit": 1.0 if self.permit else 0.0,
               "sys_reasons": float(sum(1 << REASONS.index(r) for r in self.reasons)),
               "sys_latch": 1.0 if self.latch else 0.0, "sys_confirmed": 1.0 if self.confirmed else 0.0}
        if self.spec.opstate:
            out["sys_state"] = float(STATE_CODES[self.state])
        if self.spec.supply:
            out["src_LV"] = 1.0 if self.src["LV"] else 0.0
            out["src_HV"] = 1.0 if (self.src["HV"] and not self.src["HV_fault"]) else 0.0
            for r in self.spec.supply.rails:
                out[f"rail_{r.name}"] = 1.0 if self.rails[r.name]["powered"] else 0.0
        if self.spec.interface:
            out["itf_pos_limit"] = self.last_limit if hasattr(self, "last_limit") else math.inf
            out["itf_ext_active"] = 1.0 if self.ext["active"] else 0.0
            out["itf_fallback"] = 1.0 if getattr(self, "fallback_on", False) else 0.0
            out["rx_age"] = (sim.t_now - self.rx["t_acc"]) if (self.rx and self.rx["t_acc"] is not None) else math.nan
        if self.spec.em2:
            out["em2_i_dc"] = float(sim.plant.i_ext_fn(sim.x_now[4])) if sim.plant.i_ext_fn else 0.0
        if self.spec.discharge:
            out["dis_on"] = 1.0 if self.dis["on"] else 0.0
            out["dis_complete"] = 1.0 if self.dis["complete_at"] is not None else 0.0
        out["in_kl15_sw"] = float(self.sig["kl15_sw"])
        out["in_kl15_hw"] = float(self.sig["kl15_hw"])
        return out

    def _pseudo(self, sim, key: str, reaction: str, path: str):
        from .protection import MechanismSpec, MechanismState
        if path not in sim.s.paths:
            raise InputValidationError(f"the system requests its safe state through the undeclared path {path}",
                                       field="system.supervisor.path")
        k = (key, reaction, path)
        if k not in self._mechs:
            self._mechs[k] = MechanismState(MechanismSpec(f"SYS:{key}", "system", path, reaction,
                                                          resources=sim.s.paths[path].resources,
                                                          text=f"system: {key}"))
        return self._mechs[k]

    def _reason_reaction(self, reason: str) -> tuple:
        sup = self.spec.supervisor
        if sup is None:
            return "six_switch_off", next(iter(()), "SW")
        return sup.reactions.get(reason, sup.reaction), sup.paths.get(reason, sup.path)

    # -- start ---------------------------------------------------------------------------------------------------
    def start(self, sim, horizon: float):
        sp = self.spec
        for e in sp.inputs:
            if e.t_s <= horizon:
                sim.schedule(e.t_s, 0, "sys:input", e)
        if sp.supervisor or sp.opstate:
            task = sp.supervisor.task_s if sp.supervisor else 1e-3
            n = 1
            while n * task <= horizon + 1e-12:
                sim.schedule(round(n * task, 12), 5, "sys:task", None)
                n += 1
        if sp.supply:
            for r in sp.supply.rails:
                self.rails[r.name] = {"powered": True, "token": 0, "down": False}
        if sp.interface and sp.interface.e2e:
            e = sp.interface.e2e
            # the steady state before t = 0: the frame sent one period earlier was received and accepted
            self.rx = {"k_last": -1, "ctr_last": (-1) % e["counter_mod"], "payload": sim.s.request(0.0),
                       "t_acc": -e["period_s"] + e["latency_s"], "t_send": -e["period_s"],
                       "invalid_run": 0, "rejects": 0, "repeats": 0, "fault": None, "fault_t": math.inf,
                       "value": 0.0, "period": e["period_s"], "latency": e["latency_s"], "mod": e["counter_mod"],
                       "max_delta": e["max_delta"]}
        if sp.em2:
            self._em2_power(sim, 0.0)
            for (tb, _p) in sp.em2.power_W:
                if 0 < tb <= horizon:
                    sim.schedule(tb, 1, "sys:em2_step", None)
        if sp.selftest:
            for i, test in enumerate(sp.selftest.tests):
                if test["t_s"] <= horizon:
                    sim.schedule(test["t_s"], 1, "sys:selftest", i)
            if sp.selftest.path_test_at_start:
                sim.schedule(0.0, 1, "sys:path_test", None)
            if sp.selftest.path_test_period_s:
                n = 1
                while n * sp.selftest.path_test_period_s <= horizon + 1e-12:
                    sim.schedule(n * sp.selftest.path_test_period_s, 1, "sys:path_test", None)
                    n += 1
        # the initial condition: a state or a reason that does not allow PWM holds the passive stage from t = 0
        self._update_reasons(sim, 0.0, initial=True)
        if self.state is not None:
            sim.ev(0.0, "opstate", "state manager", f"initial operating state {self.state}", state=self.state)
            self.state_log.append((0.0, self.state))
        self._enforce(sim, 0.0, initial=True)

    # -- events ------------------------------------------------------------------------------------------------
    def handle(self, sim, t, x, kind, payload):
        if kind == "sys:input":
            self._input(sim, t, payload)
        elif kind == "sys:task":
            self._task(sim, t, x)
        elif kind == "sys:rail_down":
            name, token = payload
            r = self.rails[name]
            if token == r["token"] and not r["powered"] and not r["down"]:
                r["down"] = True
                spec = next(rr for rr in self.spec.supply.rails if rr.name == name)
                sim.ev(t, "supply", name, f"rail {name} lost after its hold-up ({spec.hold_up_s * 1e3:g} ms): "
                                          f"{', '.join(spec.resources) or 'no declared load'} lost")
                for res in spec.resources:
                    sim._lose_resource(t, x, res)
                sim.update_bridge(t, f"rail {name} lost")
        elif kind == "sys:rail_up":
            name, token = payload
            r = self.rails[name]
            if token == r["token"] and r["powered"] and r["down"]:
                r["down"] = False
                spec = next(rr for rr in self.spec.supply.rails if rr.name == name)
                sim.ev(t, "supply", name, f"rail {name} back after its restart time: "
                                          f"{', '.join(spec.resources) or 'no declared load'} available")
                for res in spec.resources:
                    sim.restore_resource(t, res)
                sim.update_bridge(t, f"rail {name} back")
        elif kind == "sys:em2_step":
            self._em2_power(sim, t)
        elif kind == "sys:em2_react":
            self._em2_react(sim, t)
        elif kind == "sys:selftest":
            self._selftest(sim, t, payload)
        elif kind == "sys:selftest_end":
            if self.test is not None and self.test["token"] == payload:
                sim.ev(t, "selftest", "self-test", "gate test pulse ended: the bridge returns to the state it had")
                self.test = None
                sim.selftest = None
                sim.update_bridge(t, "self-test ended")
        elif kind == "sys:path_test":
            self._path_test(sim, t)
        return x

    def _input(self, sim, t, e: InputEvent):
        old = self.sig.get(e.signal)
        if e.signal in PULSES:
            sim.ev(t, "input", e.signal, f"{e.signal} requested")
            if e.signal == "dtc_clear":
                self._dtc_clear(sim, t)
            elif e.signal == "reset_request":
                sim._mcu_reset(t, sim.s.control.boot_s)
            return
        self.sig[e.signal] = e.value
        sim.ev(t, "input", e.signal, f"{e.signal}: {old} -> {e.value}", signal=e.signal, value=e.value)
        if e.signal in ("kl15_sw", "kl15_hw") and old != e.value:
            self.edges.append((t, e.signal, int(bool(e.value))))
        if e.signal == "discharge_request" and e.value and not old and self.spec.discharge:
            self._discharge_start(sim, t)
        if e.signal == "t30_state" and e.value == "normal":
            self.t30_normal_since = t

    # -- supervisor --------------------------------------------------------------------------------------------
    def on_actuation(self, sim, t, spec, reaction):
        """An executed reaction of a safety mechanism sets the default-error latch (the system's own safe-state
        requests do not)."""
        sup = self.spec.supervisor
        if sup is None or spec.kind == "system" or spec.mech_id in sup.latch_ignore:
            return
        if self.latch is None:
            self.latch = {"t": t, "by": spec.mech_id, "reaction": reaction}
            sim.ev(t, "supervisor", "supervisor", f"default-error latch set by {spec.mech_id} "
                                                 f"(re-arm: {sup.rearm})", latch=True)
            self.reasons.setdefault("default_error", t)
            self.permit = False
            if self.state is not None:
                self._goto(sim, t, "FAULT", f"default error ({spec.mech_id})")

    def _dtc_clear(self, sim, t):
        sup = self.spec.supervisor
        if self.latch is None or sup is None:
            return
        if sup.dtc_clear_releases:
            sim.ev(t, "supervisor", "supervisor", "DTC clear releases the default-error latch (non-approved variant)")
            self.latch = None
            self.reasons.pop("default_error", None)
        else:
            sim.ev(t, "supervisor", "supervisor", "DTC cleared: the default-error latch is kept (a diagnostic clear "
                                                 "is not a re-arm event)")

    def corrupt_latch(self, sim, t):
        """A memory fault clears the latch word: detected (complement check) or lost."""
        sup = self.spec.supervisor
        if sup is None or self.latch is None:
            sim.ev(t, "supervisor", "supervisor", "latch word corrupted while no latch is set: no effect")
            return
        if sup.latch_protection:
            sim.ev(t, "detection", "SYS:latch_check", "default-error latch word corrupted: complement mismatch "
                                                     "detected, the latch is kept set", mech_kind="system")
        else:
            sim.ev(t, "supervisor", "supervisor", "default-error latch word corrupted and not protected: latch LOST")
            self.latch = None
            self.reasons.pop("default_error", None)

    def on_mcu_reset(self, sim, t):
        sup = self.spec.supervisor
        self.reasons.setdefault("reset", t)
        self.permit = False
        if sup is not None and self.latch is not None and sup.latch_retention == "ram":
            sim.ev(t, "supervisor", "supervisor", "MCU reset: the default-error latch was in RAM and is lost "
                                                 "(retention: RAM)")
            self.latch = None
            self.reasons.pop("default_error", None)
        if sup is not None and sup.latch_retention == "ram":
            self.edges = []
        self._mechs_requested_clear()

    def _mechs_requested_clear(self):
        self.requested = {}

    def on_mcu_boot(self, sim, t) -> bool:
        """The software runs again: True if the system decides what the software does (the supervisor)."""
        if self.spec.supervisor is None and self.spec.opstate is None:
            return False
        self.reasons.pop("reset", None)
        sim.ctrl.state = "standby"
        sim.ev(t, "supervisor", "supervisor", "MCU booted: the supervisor decides (safe state until the permit)")
        self._update_reasons(sim, t)
        self._enforce(sim, t, after_boot=True)
        return True

    def _update_reasons(self, sim, t, initial=False):
        s = self.sig
        want = {}
        if s["target_mode"] == "standby":
            want["standby"] = True
        if s["target_mode"] == "no_torque":
            want["no_torque"] = True
        if not s["kl15_sw"]:
            want["kl15_sw_off"] = True
        if not s["kl15_hw"]:
            want["kl15_hw_off"] = True
        sup = self.spec.supervisor
        if s["t30_state"] != "normal":
            want["supply_condition"] = True
        elif "supply_condition" in self.reasons:
            since = self.t30_normal_since if self.t30_normal_since is not None else t
            if sup is not None and t - since < sup.supply_recover_s - 1e-12:
                want["supply_condition"] = True
        if self.latch is not None:
            want["default_error"] = True
        if self.path_test_failed is not None:
            want["latent_path"] = True
        if sim.mcu_state == "reset":
            want["reset"] = True
        for r in list(self.reasons):
            if r not in want:
                sim.ev(t, "supervisor", "supervisor", f"reason {r} cleared")
                self.reasons.pop(r)
        for r in want:
            if r not in self.reasons:
                self.reasons[r] = t
                if not initial:
                    sim.ev(t, "supervisor", "supervisor", f"safe state required: {r}", reason=r)

    def _rearm_qualified(self, t) -> tuple:
        sup = self.spec.supervisor
        L = self.latch
        if L is None:
            return True, ""
        if sup.rearm == "never":
            return False, "re-arm contract: never"
        if sup.rearm == "kl15_level_and":
            ok = bool(self.sig["kl15_sw"]) and bool(self.sig["kl15_hw"])
            return ok, "both terminal-15 inputs high (level AND)"

        def off_on(sig):
            t_off = next((tt for tt, s_, v in self.edges if s_ == sig and v == 0 and tt > L["t"]), None)
            if t_off is None:
                return None
            return next((tt for tt, s_, v in self.edges if s_ == sig and v == 1 and tt > t_off), None)
        need = {"kl15_sw_hw_off_on": ("kl15_sw", "kl15_hw"), "kl15_sw_off_on": ("kl15_sw",),
                "kl15_hw_off_on": ("kl15_hw",)}[sup.rearm]
        ons = [off_on(s) for s in need]
        if any(v is None for v in ons):
            return False, "waiting for the terminal-15 off -> on sequence"
        if sup.rearm_max_gap_s is not None and len(ons) == 2 and abs(ons[0] - ons[1]) > sup.rearm_max_gap_s + 1e-12:
            return False, (f"terminal-15 software / hardware on events {abs(ons[0] - ons[1]) * 1e3:.4g} ms apart > "
                           f"{sup.rearm_max_gap_s * 1e3:g} ms")
        return True, f"terminal-15 off -> on after the latch ({', '.join(need)})"

    def _trigger_cleared(self, sim, t) -> bool:
        sup = self.spec.supervisor
        L = self.latch
        if L is None:
            return True
        m = next((m for m in sim.mechs if m.spec.mech_id == L["by"]), None)
        if m is None:
            return True
        if m.spec.hardware and m.spec.kind in ("overcurrent_hw", "overvoltage_hw"):
            if sim._hw_value(m, sim.x_now) <= 0.0:
                return False
            return True
        if m.cleared_since is None and m.tripped:
            return m.spec.hardware          # hardware latches (desaturation, UVLO) do not report clearance
        return t - (m.cleared_since if m.cleared_since is not None else t) >= sup.readiness_s - 1e-12

    def _task(self, sim, t, x):
        if sim.mcu_state != "run":
            return
        sp = self.spec
        self._update_reasons(sim, t)
        # the default-error latch: re-arm + readiness
        if self.latch is not None and sp.supervisor is not None:
            ok, why = self._rearm_qualified(t)
            if ok and self._trigger_cleared(sim, t):
                sim.ev(t, "supervisor", "supervisor", f"default-error latch released: {why}")
                self.latch = None
                self.reasons.pop("default_error", None)
                if self.state == "FAULT":
                    self._goto(sim, t, "IDLE", "default error re-armed")
        if sp.discharge and self.dis["on"]:
            self._discharge_check(sim, t)
        if sp.supervisor is not None:
            self._confirm(sim, t)
        if sp.opstate:
            self._opstate_step(sim, t)
        self._enforce(sim, t)
        # the stage allow-list of the state (an assertion recorded as evidence)
        if sp.opstate:
            stage = BRIDGE_STAGE.get(sim.bridge_cmd, "ASO")
            allowed = sp.opstate.allow.get(self.state, ())
            if stage != "TEST" and stage not in allowed:
                # (time, state, stage, time since the state was entered): a changeover in progress shows here too
                self.allow_violations.append((t, self.state, stage, t - self.state_since))

    # -- operating states ------------------------------------------------------------------------------------------
    def _goto(self, sim, t, new, why):
        if new == self.state:
            return
        old = self.state
        self.transitions.append({"t": t, "from": old, "to": new, "why": why})
        self.state, self.state_since = new, t
        self.state_log.append((t, new))
        sim.ev(t, "opstate", "state manager", f"{old} -> {new} ({why})", state=new, previous=old)
        if new == "STNDBY" and old == "POWER_OFF":
            self.stndby_power_up = t

    def _opstate_step(self, sim, t):
        op = self.spec.opstate
        s = self.sig
        vdc = sim.last_meas.get("vdc_V")
        spd = abs(sim.last_meas.get("speed_rpm") or 0.0)
        st = self.state
        if st == "FAULT":
            return
        if st == "POWER_OFF":
            if s["kl15_sw"] or s["kl15_hw"]:
                self._goto(sim, t, "STNDBY", "power up")
            return
        if st == "STNDBY":
            t0 = self.stndby_power_up if self.stndby_power_up is not None else self.state_since
            if s["ignition"] and s["target_mode"] != "standby" and t - t0 >= op.guard_stndby_idle_s - 1e-12:
                self._goto(sim, t, "IDLE", f"ignition on, {1e3 * (t - t0):.4g} ms after power-up")
            return
        if st == "IDLE":
            low = g_idle_to_stndby({"ignition": s["ignition"], "hv_low": vdc is not None and vdc < op.hv_low_V,
                                    "spd_low": spd < op.spd_low_rpm})
            if low:
                self.idle_low_since = t if self.idle_low_since is None else self.idle_low_since
                if t - self.idle_low_since >= op.persist_idle_stndby_s - 1e-12:
                    self._goto(sim, t, "STNDBY", f"ignition off, HV {vdc:.0f} V < {op.hv_low_V:g} V, "
                                                 f"speed {spd:.0f} rpm < {op.spd_low_rpm:g} rpm held "
                                                 f"{op.persist_idle_stndby_s * 1e3:g} ms")
                    self.idle_low_since = None
                return
            self.idle_low_since = None
            if g_idle_to_run(s) and not self.reasons:
                self._goto(sim, t, "LHOM" if op.lhom else "RUN", "HV enable, self-test done, ASC release, torque "
                                                                   "request")
            return
        if st in ("RUN", "LHOM") and g_run_to_idle(s):
            why = [n for n, bad in (("HV enable withdrawn", not s["hv_enable"]), ("roll-out requested", s["rol_actv"]),
                                    ("power-stage control requested", s["pwr_stg_ctrl"]),
                                    ("torque request inactive", not s["trq_gen_rq"])) if bad]
            if why:
                self._goto(sim, t, "IDLE", ", ".join(why))

    def passive_stage(self, sim) -> str:
        op = self.spec.opstate
        spd = abs(sim.last_meas.get("speed_rpm") or 0.0)
        if op is None:
            return "six_switch_off"
        if self.sig.get("energy_flow_block"):
            return op.aps_side if spd >= op.aps_above_rpm else "six_switch_off"
        return op.aps_side if spd >= op.aps_above_rpm else "six_switch_off"

    # -- enforcing: request / release the safe state -----------------------------------------------------------
    def _enforce(self, sim, t, initial=False, after_boot=False):
        sp = self.spec
        pwm_ok = True
        why = []
        if sp.opstate:
            pwm_ok = "PWM" in sp.opstate.allow.get(self.state, ())
            if not pwm_ok:
                why.append(f"state {self.state}")
        if self.reasons:
            pwm_ok = False
            why += sorted(self.reasons)
        was = self.permit
        self.permit = pwm_ok and sim.hw_reaction is None
        if not pwm_ok:
            # the safe state of the reasons: the reaction of the highest-priority reason (default error first)
            key = next((r for r in ("default_error", "latent_path", "kl15_hw_off", "kl15_sw_off", "standby",
                                    "no_torque", "supply_condition", "reset") if r in self.reasons), None)
            if key == "default_error" and sim.sw_reaction is not None:
                return          # the fault reaction holds; the latch keeps it
            if key == "reset":
                return          # the reset output state holds the bridge
            if key is None:     # the state does not allow PWM: its passive stage
                reaction, path = self.passive_stage(sim), (sp.supervisor.path if sp.supervisor else "SW")
                key = f"state_{self.state}"
            else:
                reaction, path = self._reason_reaction(key)
            if initial:
                self._initial_passive(sim, t, key, reaction)
                return
            if sim.sw_reaction is None and sim.hw_reaction is None and key not in self.requested:
                m = self._pseudo(sim, key, reaction, path)
                self.requested[key] = m
                sim.system_request(t, m, f"safe state required: {', '.join(why)}")
            return
        # permitted: release a system-held safe state (never a hardware reaction)
        if (not was or after_boot) and (sim.sw_reaction is not None or sim.ctrl.state in ("halted", "standby")
                                        or sim.torque_override is not None):
            if sim.hw_reaction is not None:
                return
            self.requested = {}
            sim.system_release(t, "normal torque permit: no safe-state reason holds"
                               + (f" (state {self.state})" if self.state else ""))

    def initial_stage(self, sim) -> str | None:
        """The bridge state the system holds at t = 0 (None: PWM allowed) - the plant starts in its steady state
        (the ASC's short-circuit current, no current in six-switch-off) instead of a transient from the PWM point."""
        s = self.sig
        reasons = []
        if s["target_mode"] in ("standby", "no_torque"):
            reasons.append("standby" if s["target_mode"] == "standby" else "no_torque")
        if not s["kl15_sw"]:
            reasons.append("kl15_sw_off")
        if not s["kl15_hw"]:
            reasons.append("kl15_hw_off")
        if s["t30_state"] != "normal":
            reasons.append("supply_condition")
        op = self.spec.opstate
        state_blocks = op is not None and "PWM" not in op.allow.get(op.initial, ())
        if not reasons and not state_blocks:
            return None
        if reasons and self.spec.supervisor is not None:
            reaction, _p = self._reason_reaction(reasons[0])
        else:
            spd = abs(sim.s.speed_rpm)
            reaction = (op.aps_side if (op is not None and spd >= op.aps_above_rpm) else "six_switch_off")
        if reaction == "safe_state":
            reaction, _r = sim.s.policy.decide({"speed_rpm": sim.s.speed_rpm, "vdc_V": None, "detected_by": "system"})
        if reaction in sim.s.strategies:
            reaction = sim.s.strategies[reaction].fallback_state
        return None if reaction == "torque_zero" else reaction

    def _initial_passive(self, sim, t, key, reaction):
        if reaction == "safe_state":
            reaction, _rule = sim.s.policy.decide({"speed_rpm": sim.last_meas.get("speed_rpm"),
                                                   "vdc_V": sim.last_meas.get("vdc_V"), "detected_by": "system"})
        if reaction in sim.s.strategies:
            reaction = sim.s.strategies[reaction].fallback_state
        if reaction == "torque_zero":
            sim.torque_override = 0.0
        else:
            sim.sw_reaction = reaction
        sim.ctrl.state = "standby" if reaction != "torque_zero" else sim.ctrl.state
        self.requested[key] = None
        sim.ev(t, "supervisor", "supervisor", f"initial safe state {reaction} ({key})")
        sim.update_bridge(t, f"initial safe state ({key})")

    # -- supply --------------------------------------------------------------------------------------------------
    def externals(self, sim) -> list:
        from .plant import VDC, ExternalEvent
        out = []
        sp = self.spec.supply
        if sp is not None and not self.src["HV_fault"]:
            if self.src["HV"]:
                lim = sp.hv_min_V - sp.hv_hyst_V
                out.append(ExternalEvent("HV supply", lambda t, x, lim=lim: x[VDC] - lim,
                                         lambda t, x: self._hv_change(sim, t, x, False)))
            else:
                lim = sp.hv_min_V
                out.append(ExternalEvent("HV supply", lambda t, x, lim=lim: lim - x[VDC],
                                         lambda t, x: self._hv_change(sim, t, x, True)))
        return out

    def _hv_change(self, sim, t, x, on: bool):
        self.src["HV"] = on
        sim.ev(t, "supply", "HV-derived supply", f"HV-derived supply {'available' if on else 'lost'} "
                                                 f"(DC link {x[4]:.1f} V)")
        self._rails_update(sim, t, "HV " + ("back" if on else "lost"))
        return True

    def source_event(self, sim, t, source: str, on: bool, why: str):
        if source == "LV":
            self.src["LV"] = on
        elif source == "HV_fault":
            self.src["HV_fault"] = not on
        sim.ev(t, "supply", source, why)
        self._rails_update(sim, t, why)

    def _rails_update(self, sim, t, why):
        sp = self.spec.supply
        hv = self.src["HV"] and not self.src["HV_fault"]
        for r in sp.rails:
            st = self.rails[r.name]
            have = [s for s in r.sources if (s == "LV" and self.src["LV"]) or (s == "HV" and hv)]
            powered = bool(have)
            if powered and st["powered"] and sp.transfer_gap_s > 0 and st.get("from") and st["from"] not in have:
                # the source changes: a transfer gap
                st["token"] += 1
                if sp.transfer_gap_s > r.hold_up_s:
                    sim.schedule(t + r.hold_up_s, 1, "sys:rail_down", (r.name, st["token"]))
                    st["powered"] = False
                    sim.schedule(t + sp.transfer_gap_s, 1, "sys:rail_transfer", (r.name, st["token"]))
                sim.ev(t, "supply", r.name, f"rail {r.name}: source transfer ({sp.transfer_gap_s * 1e6:g} us gap, "
                                            f"hold-up {r.hold_up_s * 1e3:g} ms)")
            st["from"] = have[0] if have else None
            if powered == st["powered"]:
                continue
            st["powered"] = powered
            st["token"] += 1
            if not powered:
                sim.schedule(t + r.hold_up_s, 1, "sys:rail_down", (r.name, st["token"]))
            elif st["down"]:
                sim.schedule(t + r.restart_s, 1, "sys:rail_up", (r.name, st["token"]))

    # -- torque interface ----------------------------------------------------------------------------------------
    def rx_fault(self, t, mode: str, value: float):
        if self.rx is None:
            raise InputValidationError("an end-to-end receive fault needs the interface's e2e declaration",
                                       field="faults.kind")
        self.rx["fault"], self.rx["fault_t"], self.rx["value"] = mode, t, value

    def rx_clear(self):
        if self.rx is not None:
            self.rx["fault"], self.rx["fault_t"] = None, math.inf

    def _frame(self, k: int, request):
        """The frame the sender sent at k * period as it arrives: (counter, data_id_ok, em_ok, crc_ok, payload) or
        None when it is lost."""
        rx = self.rx
        ts = k * rx["period"]
        ctr = k % rx["mod"]
        payload = request(ts)
        ok_id = ok_em = ok_crc = True
        f = rx["fault"] if ts >= rx["fault_t"] - 1e-12 else None
        if f == "loss":
            return None
        if f == "crc":
            ok_crc = False
        elif f == "data_id":
            ok_id = False
        elif f == "em_swap":
            ok_em = False
        elif f == "counter_repeat":
            k0 = math.ceil(rx["fault_t"] / rx["period"] - 1e-9) - 1
            ctr = max(k0, 0) % rx["mod"]
        elif f == "counter_jump":
            ctr = (k + int(rx["value"] or 3)) % rx["mod"]
        elif f == "value":
            payload = rx["value"]
        return ctr, ok_id, ok_em, ok_crc, payload, ts

    def rx_newest(self, sim, t, request):
        """Receive every frame arrived since the last call; return (payload of the newest ACCEPTED frame, its send
        time).  A frame is accepted when its CRC, data id and machine id are right and its counter advanced by 1 ..
        max_delta; a repeated counter is not a new update (the age is not refreshed)."""
        rx = self.rx
        k_new = math.floor((t - rx["latency"]) / rx["period"] + 1e-9)
        k0 = -1 if rx["k_last"] is None else rx["k_last"]
        for k in range(max(k0 + 1, 0), k_new + 1):
            fr = self._frame(k, request)
            rx["k_last"] = k
            if fr is None:
                continue
            ctr, ok_id, ok_em, ok_crc, payload, ts = fr
            bad = []
            if not ok_crc:
                bad.append("CRC")
            if not ok_id:
                bad.append("data id")
            if not ok_em:
                bad.append("machine id")
            if rx["ctr_last"] is not None and not bad:
                d = (ctr - rx["ctr_last"]) % rx["mod"]
                if d == 0:
                    rx["repeats"] += 1
                    bad.append("repeated counter")
                elif d > rx["max_delta"]:
                    bad.append(f"counter jump {d}")
            if bad:
                rx["rejects"] += 1
                rx["invalid_run"] += 1
                if rx["rejects"] <= 3 or rx["rejects"] % 50 == 0:
                    sim.ev(t, "rx", "E2E", f"frame {k} rejected ({', '.join(bad)})")
                if "repeated counter" not in bad and not any(b.startswith("counter jump") for b in bad):
                    continue
                if any(b.startswith("counter jump") for b in bad):
                    rx["ctr_last"] = ctr        # resynchronise on the new counter (the frame itself is rejected)
                continue
            rx["ctr_last"], rx["payload"], rx["t_acc"], rx["t_send"] = ctr, payload, ts + rx["latency"], ts
            rx["invalid_run"] = 0
        return rx["payload"], (rx["t_send"] if rx["t_send"] is not None else -math.inf)

    def rx_age(self, t) -> float:
        if self.rx is None or self.rx["t_acc"] is None:
            return math.inf
        return t - self.rx["t_send"]

    def limit_command(self, sim, t, T):
        """The torque command after the vehicle-interface limits (fallback, intervention + envelope, extension)."""
        itf = self.spec.interface
        if itf is None or T is None:
            return T
        s = self.sig
        pos_lim = math.inf
        self.fallback_on = False
        fb = itf.fallback
        if fb is not None:
            why = []
            if not s["wheel_speed_valid"]:
                why.append("wheel speed invalid")
            if not s["speed_limit_valid"]:
                why.append("speed-limit request invalid / missing")
            if s["speed_limit_qualifier"] == "invalid":
                why.append("qualifier invalid")
            if s["speed_limit_qualifier"] == "limited":
                why.append("qualifier LIMITED")
            if s["wheel_speed_valid"] and s["speed_limit_valid"] and s["wheel_speed_kph"] > s["speed_limit_kph"]:
                why.append("wheel speed above the requested limit")
            if why:
                if "safe_value_Nm" in fb:
                    pos_lim = float(fb["safe_value_Nm"])
                else:
                    pos_lim = float(fb["axle_torque_Nm"]) / float(fb["wheel_ratio"])
                self.fallback_on = True
                if not getattr(self, "_fb_logged", False):
                    sim.ev(t, "interface", "fallback", f"positive torque limited to the safe value {pos_lim:g} N*m "
                                                       f"({'; '.join(why)})")
                    self._fb_logged = True
            else:
                self._fb_logged = False
        T_sum = T + float(s["intervention_Nm"] or 0.0)
        env = itf.envelope
        if env is not None:
            T_max, T_min = float(env.get("max_Nm", math.inf)), float(env.get("min_Nm", -math.inf))
            ex = itf.extended
            if ex is not None:
                req = bool(s["ext_request"]) and not s["voltage_control_mode"]
                if req and not self.ext["expired"]:
                    if not self.ext["active"] and T_max < ex["c_m_min_Nm"]:
                        self.ext.update(active=True, since=t)
                        sim.ev(t, "interface", "extended torque", f"extended torque active: positive limit "
                                                                  f"{T_max:g} -> {ex['c_m_min_Nm']:g} N*m")
                    if self.ext["active"] and t - self.ext["since"] > ex["c_t_imax_s"] + 1e-12:
                        self.ext.update(active=False, expired=True)
                        sim.ev(t, "interface", "extended torque", f"extended torque expired after "
                                                                  f"{ex['c_t_imax_s'] * 1e3:g} ms: prior limit")
                elif not req:
                    if self.ext["active"]:
                        sim.ev(t, "interface", "extended torque", "extended torque request withdrawn: prior limit")
                    self.ext.update(active=False, expired=False, since=None)
                if self.ext["active"]:
                    T_max = max(T_max, ex["c_m_min_Nm"])
            if not sim.app_limit_failed:
                lim = min(T_sum, T_max) if T_sum > 0 else max(T_sum, T_min)
                clamped = abs(lim - T_sum) > 1e-9
                if clamped != getattr(self, "_clamp_on", False):
                    self._clamp_on = clamped
                    sim.ev(t, "controller", "interface", f"application torque limit {'active' if clamped else 'released'}: "
                                                         f"{T_sum:.1f} -> {lim:.1f} N*m" if clamped else
                           "application torque limit released")
                T_sum = lim
        if T_sum > pos_lim:
            T_sum = pos_lim
        self.last_limit = pos_lim
        return T_sum

    # -- second machine ----------------------------------------------------------------------------------------
    def _em2_power(self, sim, t):
        sp = self.spec.em2
        if self.em2_reacted_at is not None:
            return
        P = 0.0
        for tb, p in sp.power_W:
            if tb <= t + 1e-12:
                P = p
        sim.plant.i_ext_fn = (lambda v, P=P: P / max(v, 1.0))
        sim.ev(t, "em2", "second machine", f"second machine DC power {P / 1e3:.4g} kW")

    def on_reaction(self, sim, t):
        sp = self.spec.em2
        if sp is not None and sp.contract == "global" and self.em2_reacted_at is None:
            sim.schedule(t + sp.delay_s, 1, "sys:em2_react", None)

    def _em2_react(self, sim, t):
        sp = self.spec.em2
        self.em2_reacted_at = t
        if sp.state == "asc":
            sim.plant.i_ext_fn = lambda v: 0.0
            text = "active short circuit (no DC exchange)"
        else:
            sim.plant.i_ext_fn = lambda v, sp=sp: -em2_rectifier_current(sp, v)
            text = "six-switch-off (uncontrolled rectification above the back-EMF onset)"
        sim.ev(t, "em2", "second machine", f"second machine reacts (global contract): {text}")

    # -- discharge -------------------------------------------------------------------------------------------------
    def _discharge_start(self, sim, t):
        sp = self.spec.discharge
        self.dis.update(on=True, t_req=t, below_since=None, complete_at=None)
        if sp.open_contactor and sim.dc.contactor_closed:
            sim.x_now = sim._open_contactor(t, sim.x_now, "discharge request")
        if not sim.dc.R_active:
            sim.ev(t, "discharge", "discharge", "discharge requested but no active-discharge resistor is declared")
        else:
            sim.dc.active_discharge_on = True
            sim.ev(t, "discharge", "discharge", "active discharge on (request)")

    def _discharge_check(self, sim, t):
        sp = self.spec.discharge
        v = sim.read(t, "vdc_control")
        if v is None:
            return
        if v < sp.v_limit_V:
            self.dis["below_since"] = t if self.dis["below_since"] is None else self.dis["below_since"]
            if self.dis["complete_at"] is None and t - self.dis["below_since"] >= sp.confirm_s - 1e-12:
                self.dis["complete_at"] = t
                sim.ev(t, "discharge", "discharge", f"discharge complete indicated (measured {v:.1f} V < "
                                                    f"{sp.v_limit_V:g} V for {sp.confirm_s * 1e3:g} ms)")
        else:
            self.dis["below_since"] = None

    # -- self-tests ------------------------------------------------------------------------------------------------
    def _selftest(self, sim, t, i):
        test = self.spec.selftest.tests[i]
        spd = abs(sim.last_meas.get("speed_rpm") or 0.0)
        vdc = sim.last_meas.get("vdc_V")
        why = []
        if test["speed_max_rpm"] is not None and spd > float(test["speed_max_rpm"]):
            why.append(f"measured speed {spd:.0f} rpm > {float(test['speed_max_rpm']):g} rpm")
        if test["vdc_max_V"] is not None and (vdc is None or vdc > float(test["vdc_max_V"])):
            why.append(f"measured DC voltage {vdc if vdc is not None else float('nan'):.0f} V > "
                       f"{float(test['vdc_max_V']):g} V")
        if test["states"] and self.state not in test["states"]:
            why.append(f"state {self.state} not in {list(test['states'])}")
        if sim.bridge_cmd == "pwm":
            why.append("normal PWM is running")
        if why:
            sim.ev(t, "selftest", "self-test", f"gate test refused: {'; '.join(why)}", refused=True)
            return
        tok = (self.test or {}).get("token", 0) + 1
        self.test = {"token": tok, "t": t, "test": test}
        sim.selftest = {"legs": tuple(test["legs"]), "side": test["side"]}
        sim.ev(t, "selftest", "self-test", f"gate test pulse: {test['side']} switches of legs "
                                            f"{', '.join(test['legs'])} for {test['pulse_s'] * 1e6:g} us")
        sim.update_bridge(t, "self-test pulse")
        sim.schedule(t + test["pulse_s"], 1, "sys:selftest_end", tok)

    def _path_test(self, sim, t):
        paths = self.spec.selftest.paths or tuple(sim.s.paths)
        lost = [p for p in paths if p in sim.paths_lost or not sim.res.ok(sim.s.paths[p].resources)]
        if lost:
            if self.path_test_failed is None:
                self.path_test_failed = t
                sim.ev(t, "detection", "SYS:path_test", f"latent-path test: {', '.join(lost)} cannot actuate - torque "
                                                       f"permit blocked", mech_kind="system")
        else:
            sim.ev(t, "selftest", "path test", f"latent-path test passed ({', '.join(paths)})")

    # -- the confirmation of the reaction (observed, never assumed from the command) ------------------------------
    def _confirm(self, sim, t):
        """Requested / applied / confirmed: a reaction is confirmed only when the gates show the commanded state
        (no unavailable gate, no failed device reported) and, for six-switch-off, every measured phase current is
        below the confirmation threshold; an active short circuit is confirmed by the gate observation alone (its
        current is expected).  The confirmation is what the inverter REPORTS; the judge compares it with the truth."""
        sup = self.spec.supervisor
        active = sim.hw_reaction is not None or sim.sw_reaction is not None
        ok = False
        if active and sim.bridge_cmd != "pwm":
            notes = " ".join(sim.actual_bridge())
            gates_ok = not any(w in notes for w in ("unavailable", "open", "short"))
            if sim.bridge_cmd in ("six_switch_off", "off"):
                vals = [sim.read(t, f"current_{ph}") for ph in ("a", "b", "c")]
                ok = gates_ok and all(v is not None and abs(v) < sup.confirm_i_A for v in vals)
            else:
                ok = gates_ok
        if ok != self.confirmed:
            self.confirmed = ok
            self.confirm_log.append((t, ok))
            sim.ev(t, "supervisor", "confirmation", f"safe-state reaction {'confirmed' if ok else 'not confirmed'} "
                                                   f"({sim.bridge_cmd})")

    def report(self) -> dict:
        return {"transitions": list(self.transitions), "allow_violations": self.allow_violations[:50],
                "n_allow_violations": len(self.allow_violations), "state_log": list(self.state_log),
                "reasons": dict(self.reasons), "latch": self.latch, "edges": list(self.edges),
                "discharge": dict(self.dis), "extended": dict(self.ext), "em2_reacted_at": self.em2_reacted_at,
                "path_test_failed": self.path_test_failed, "confirm_log": list(self.confirm_log),
                "rx": None if self.rx is None else {k: self.rx[k] for k in ("rejects", "repeats", "invalid_run")}}
