"""Project data -> one fault-simulation setup.

Everything product-specific comes from the project: the machine and its temperature laws (``drive``), the battery
(``dc_source``: open-circuit voltage, resistance, inductance, charge limits), the DC-link capacitor (``dc_link``),
the controller (``controller``: switching frequency, modulation, dead time, current-loop design values, update rate)
and the protection architecture with its safety requirements (``fault_sim``: sensors and the resources they need,
the sensor roles each consumer reads, reaction paths, safety mechanisms, the safe-state policy, the command path,
the battery management, SG / FSR / TSR).  Nothing of that is a built-in rule: another product changes the project,
not the code.  The built-in synthetic project carries a complete example so the whole flow runs without product
data (every value is labelled synthetic).

A scenario adds what varies between runs: operating point and request profile, temperatures, faults with their
times and sizes, the initial electrical angle, tolerance corners (sensor gain / offset, path delays, plant parameter
deviations from the controller's design values), the PWM model, protection on / off and a forced reaction.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass

from ...errors import InputValidationError
from .control import ControlConfig, ReferenceTable
from .engine import FAULT_KINDS, FaultSpec, RequestProfile, SimSetup
from .plant import DcParams, MachineParams
from .protection import KIND_PARAMS, REACTIONS, MechanismSpec, PathSpec, SafeStatePolicy
from .safety import requirements_from_dict
from .sensors import SensorSpec
from .strategy import strategies_from

TOPOLOGIES = ("two_level_vsi_star",)

from .example import FAULT_SIM as FAULT_SIM_EXAMPLE                        # noqa: E402  (the synthetic example)


def _nominal_position_delay(data: dict) -> float:
    """The control software compensates the nominal delay of the position sensor it reads (design value)."""
    name = (data.get("roles") or {}).get("position_control")
    for s in data.get("sensors") or []:
        if s.get("name") == name:
            return float(s.get("delay_us") or 0.0) * 1e-6
    return 0.0


def _f(d, k, scale=1.0, default=None):
    v = d.get(k, default)
    return None if v is None else float(v) * scale


def _res(v) -> tuple:
    return tuple(str(x) for x in (v or ()))


# ------------------------------------------------------------------------------------------ parsing the section

def sensors_from(data: dict, tol: dict) -> tuple:
    out = []
    for s in data.get("sensors") or []:
        name, kind = str(s["name"]), str(s["kind"])
        g = float(tol.get(f"{name}.gain_err", 0.0))
        if kind == "current":
            off, q = float(tol.get(f"{name}.offset", 0.0)), _f(s, "quant_A", default=0.0)
            vr = tuple(s.get("valid_range_A") or (-math.inf, math.inf))
            lost = _f(s, "lost_value_A", default=0.0)
        elif kind == "voltage":
            off, q = float(tol.get(f"{name}.offset", 0.0)), _f(s, "quant_V", default=0.0)
            vr = tuple(s.get("valid_range_V") or (-math.inf, math.inf))
            lost = _f(s, "lost_value_V", default=0.0)
        elif kind == "position":
            off = math.radians(float(tol.get(f"{name}.offset_deg", 0.0)))
            q = math.radians(float(s.get("quant_deg") or 0.0))
            vr, lost = (-math.inf, math.inf), 0.0
        else:
            off, q = float(tol.get(f"{name}.offset", 0.0)), _f(s, "quant_C", default=0.0)
            vr = tuple(s.get("valid_range_C") or (-math.inf, math.inf))
            lost = _f(s, "lost_value_C", default=0.0)
        out.append(SensorSpec(name, kind, g, off, _f(s, "delay_us", 1e-6, 0.0) + float(tol.get(f"{name}.delay_s", 0.0)),
                              q, lost, vr, _f(s, "los_detect_ms", 1e-3), _res(s.get("resources")),
                              str(s.get("basis", ""))))
    return tuple(out)


def tolerance_limits(data: dict) -> dict:
    """The declared tolerance of every sensor (for campaigns: the corners a sweep may use)."""
    out = {}
    for s in data.get("sensors") or []:
        n = s["name"]
        if s.get("gain_tol") is not None:
            out[f"{n}.gain_err"] = float(s["gain_tol"])
        for k in ("offset_tol_A", "offset_tol_V", "offset_tol_C"):
            if s.get(k) is not None:
                out[f"{n}.offset"] = float(s[k])
        if s.get("offset_tol_deg") is not None:
            out[f"{n}.offset_deg"] = float(s["offset_tol_deg"])
    return out


def _check_params(m: dict) -> None:
    """The declared parameters of a mechanism kind: numbers finite and not negative (blank = not set), choices
    among their values; other keys pass through."""
    spec = {p[0]: p for p in KIND_PARAMS.get(str(m.get("kind")), ())}
    for k, v in (m.get("params") or {}).items():
        ps = spec.get(k)
        if ps is None:
            continue
        where = f"mechanisms.{m.get('id')}.params.{k}"
        if isinstance(ps[1], tuple):
            if v not in ps[1]:
                raise InputValidationError(f"{k} must be one of {ps[1]}", field=where)
        elif v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                                or v < 0):
            raise InputValidationError(f"{k} must be a finite number >= 0 [{ps[1]}] (got {v!r})", field=where)


def mechanisms_from(data: dict) -> tuple:
    out = []
    for m in data.get("mechanisms") or []:
        _check_params(m)
        params = {}
        for k, v in (m.get("params") or {}).items():
            if k.endswith("_per_ms"):
                params[k[:-7] + "_per_s"] = None if v is None else float(v) * 1e3
            elif k.endswith("_ms"):
                params[k[:-3] + "_s"] = None if v is None else float(v) * 1e-3
            elif k.endswith("_us"):
                params[k[:-3] + "_s"] = None if v is None else float(v) * 1e-6
            else:
                params[k] = v
        out.append(MechanismSpec(str(m["id"]), str(m["kind"]), str(m["path"]), str(m.get("reaction", "safe_state")),
                                 params, _f(m, "period_ms", 1e-3), float(m.get("phase") or 0.0),
                                 _res(m.get("resources")), bool(m.get("enabled", True)), str(m.get("text", ""))))
    return tuple(out)


def paths_from(data: dict, tol: dict) -> dict:
    out = {}
    for p in data.get("paths") or []:
        pid = str(p["id"])
        out[pid] = PathSpec(pid, _f(p, "delay_us", 1e-6, 0.0) * float(tol.get(f"path.{pid}.delay_scale", 1.0)),
                            _res(p.get("resources")), p.get("fixed_reaction"), str(p.get("basis", "")))
    return out


def policy_from(data: dict) -> SafeStatePolicy:
    p = data.get("policy") or {}
    return SafeStatePolicy(tuple(p.get("rules") or ()), tuple(p.get("priority") or
                                                             ("asc_low", "asc_high", "six_switch_off", "torque_zero")),
                           bool(p.get("latch", True)), _f(p, "recovery_after_ms", 1e-3, 50.0),
                           int(p.get("recovery_max_attempts") if p.get("recovery_max_attempts") is not None else 1),
                           str(p.get("restart", "flying")),
                           str(p.get("after_reset", "restart")), str(p.get("basis", "")),
                           float(p.get("speed_hysteresis_rpm") or 0.0), bool(p.get("replace_unexecutable", True)),
                           tuple(str(x.get("id")) for x in data.get("strategies") or () if x.get("id")))


def validate_section(data: dict) -> None:
    """Project-section validation (the whole setup is checked again when a scenario is built)."""
    if data.get("topology", "two_level_vsi_star") not in TOPOLOGIES:
        raise InputValidationError(f"fault_sim.topology must be one of {TOPOLOGIES} (other topologies are not "
                                   f"represented by the switched-leg plant)", field="fault_sim.topology")
    sens = sensors_from(data, {})
    names = {s.name for s in sens}
    for r, n in (data.get("roles") or {}).items():
        if n not in names:
            raise InputValidationError(f"role {r} names an undeclared sensor {n}", field=f"fault_sim.roles.{r}")
    paths = paths_from(data, {})
    strategies = strategies_from(data)
    known = REACTIONS + tuple(strategies)
    ids = [str(m.get("id")) for m in data.get("mechanisms") or []]
    if len(set(ids)) != len(ids):
        raise InputValidationError("mechanism ids must be unique", field="fault_sim.mechanisms")
    for m in mechanisms_from(data):
        if m.path not in paths:
            raise InputValidationError(f"mechanism {m.mech_id} uses an undeclared path {m.path}",
                                       field=f"fault_sim.mechanisms.{m.mech_id}")
        if m.reaction not in known:
            raise InputValidationError(f"mechanism {m.mech_id}: reaction {m.reaction!r} is neither a reaction "
                                       f"{REACTIONS} nor a declared strategy", field=f"fault_sim.mechanisms.{m.mech_id}")
    for pid, p in paths.items():
        if p.fixed_reaction is not None and p.fixed_reaction not in known:
            raise InputValidationError(f"path {pid}: fixed reaction {p.fixed_reaction!r} is not declared",
                                       field=f"fault_sim.paths.{pid}")
    pol = policy_from(data)
    for r in pol.priority:
        if r not in known:
            raise InputValidationError(f"priority names {r!r}: neither a reaction nor a declared strategy",
                                       field="fault_sim.policy.priority")
    if data.get("requirements"):
        reqs = requirements_from_dict(data["requirements"])
        mids = {m.mech_id for m in mechanisms_from(data)}
        for f in reqs.fsrs:
            for m in f.mechanisms:
                if m not in mids:
                    raise InputValidationError(f"FSR {f.fsr_id} allocates an undeclared mechanism {m}",
                                               field="fault_sim.requirements.fsr")


# ------------------------------------------------------------------------------------------ the setup

@dataclass(frozen=True)
class ProductData:
    """The product a fault simulation runs on: the project (sections, identity) and the drive model resolved from
    its drive section (the services layer resolves it; the engine never reads files or registries)."""
    project: object
    drive: object


_TABLES: dict = {}


def _table(drive, speed: float, vdc: float, tmag, twind, limits) -> ReferenceTable:
    """The controller's reference table: the kernel's minimum-current policy under the project's declared limits
    (current, voltage, DC source) at the operating speed and battery voltage - what the drive is set up to deliver."""
    from ...identity import content_sha256
    from ...scenario import Scenario
    key = (content_sha256(drive), speed, vdc, tmag, twind, limits.discharge_power_max_W, limits.charge_power_max_W,
           limits.discharge_current_max_A, limits.charge_current_max_A)
    if key not in _TABLES:
        if len(_TABLES) > 64:
            _TABLES.clear()
        sc = Scenario("faultsim", speed, vdc, limits, magnet_temp_C=tmag, winding_temp_C=twind)
        _TABLES[key] = ReferenceTable.build(drive, sc)
    return _TABLES[key]


def vehicle_equivalent(project) -> dict | None:
    if not project.has("driveline"):
        return None
    rom = (project.data("driveline") or {}).get("rom") or {}
    r, ratio = rom.get("wheel_radius_m"), rom.get("ratio")
    if not r or not ratio or rom.get("J_out_kgm2") is None:
        return None
    J = float(rom["J_out_kgm2"]) + float(rom.get("Jm_kgm2") or 0.0) * float(ratio) ** 2
    return {"m_eq_kg": J / float(r) ** 2, "ratio": float(ratio), "wheel_radius_m": float(r),
            "basis": f"rigid-driveline equivalent from the project driveline ROM (J_out + Jm i^2) / r^2 = "
                     f"{J / float(r) ** 2:.0f} kg"}


def apply_overrides(data: dict, overrides: dict | None) -> dict:
    """Scenario-level design variants of the architecture data (sensitivity studies, candidate designs): dotted
    paths into the section, list items addressed by their id / name, e.g. ``mechanisms.SM-TQ.params.response_tau_ms``,
    ``paths.HW.delay_us``, ``policy.restart``, ``policy.rules.4.if.speed_above_rpm`` (a list index),
    ``requirements.tsr.TSR-01.criterion.abs_Nm``."""
    if not overrides:
        return data
    data = copy.deepcopy(data)
    for path, value in overrides.items():
        keys = str(path).split(".")
        node = data
        for i, k in enumerate(keys):
            last = i == len(keys) - 1
            if isinstance(node, list):
                hit = None
                if k.isdigit() and int(k) < len(node):
                    hit = int(k)
                else:
                    for j, it in enumerate(node):
                        if isinstance(it, dict) and k in (it.get("id"), it.get("name")):
                            hit = j
                            break
                if hit is None:
                    raise InputValidationError(f"override {path}: no list item {k!r}", field=f"overrides.{path}")
                if last:
                    node[hit] = value
                else:
                    node = node[hit]
            else:
                if not isinstance(node, dict):
                    raise InputValidationError(f"override {path}: {k!r} is not inside a mapping",
                                               field=f"overrides.{path}")
                if last:
                    node[k] = value
                else:
                    if k not in node:
                        node[k] = {}
                    node = node[k]
    return data


def build_setup(product: ProductData, scenario: dict) -> tuple:
    """(SimSetup, RequirementSet, info) for one scenario on a product (project + resolved drive)."""
    from ...scenario import DcSourceLimits, Scenario
    project, drive = product.project, product.drive
    sc_in = dict(scenario or {})
    data = copy.deepcopy(project.data("fault_sim")) if project.has("fault_sim") else copy.deepcopy(FAULT_SIM_EXAMPLE)
    data = apply_overrides(data, sc_in.get("overrides"))
    validate_section(data)
    tol = dict(sc_in.get("tolerances") or {})
    speed = float(sc_in.get("speed_rpm", 12000.0))
    dcs = project.data("dc_source")
    V_oc = float(sc_in.get("Voc_V") or dcs["Vdc_nominal_V"])
    tmag, twind = sc_in.get("magnet_temp_C"), sc_in.get("winding_temp_C")
    plant_sc = Scenario("faultsim", speed, V_oc, DcSourceLimits(), magnet_temp_C=tmag, winding_temp_C=twind)
    m = MachineParams.from_drive(drive, plant_sc, J=_f(sc_in, "J_kgm2"), T_load=float(sc_in.get("T_load_Nm") or 0.0))
    for k, attr in (("machine.psi_scale", "psi"), ("machine.Ld_scale", "Ld"), ("machine.Lq_scale", "Lq"),
                    ("machine.Rs_scale", "Rs")):
        if k in tol:
            setattr(m, attr, getattr(m, attr) * float(tol[k]))
    imp = dcs.get("impedance") or {}
    bat = data.get("battery") or {}
    dl = data.get("dc_link") or {}
    C = float(project.data("dc_link")["C_uF"]) * 1e-6 if project.has("dc_link") else float(dl.get("C_uF", 500.0)) * 1e-6
    L_bat = _f(bat, "L_uH", 1e-6, imp.get("L_uH"))
    dc = DcParams(C=C, V_oc=V_oc, R_bat=float(imp.get("R_mohm", 25.0)) * 1e-3, L_bat=L_bat,
                  R_bleed=_f(dl, "bleeder_ohm"), R_active=_f(dl, "active_discharge_ohm"),
                  charge_accepting=bool(bat.get("charge_accepting", True)))
    ctrl = project.data("controller") if project.has("controller") else {}
    cl = ctrl.get("current_loop") or {}
    timing = ctrl.get("timing") or {}
    cc = data.get("control") or {}
    fsw = float(ctrl.get("fsw_kHz", 10.0)) * 1e3
    w_bw = 2 * math.pi * float(cl.get("bandwidth_Hz", 450.0))
    Ld_des = float(cl.get("Ld_uH", m.Ld * 1e6)) * 1e-6
    Lq_des = float(cl.get("Lq_uH", m.Lq * 1e6)) * 1e-6
    R_des = float(cl.get("R_mohm", m.Rs * 1e3)) * 1e-3
    design_sc = Scenario("design", speed, V_oc, DcSourceLimits())
    psi_des = float(MachineParams.from_drive(drive, design_sc).psi)
    control = ControlConfig(
        fsw_Hz=fsw, Kp_d=w_bw * Ld_des, Ki_d=w_bw * R_des, Kp_q=w_bw * Lq_des, Ki_q=w_bw * R_des,
        Ld=Ld_des, Lq=Lq_des, psi=psi_des, Rs=R_des, p=m.p,
        updates_per_period=int(timing.get("updates_per_period") or 1),
        v_limit_frac=float(cc.get("v_limit_fraction", 1.0)), modulation=str(ctrl.get("modulation", "svpwm")),
        angle_comp_periods=float(cc.get("angle_comp_periods", 1.5)),
        angle_delay_comp_s=_f(cc, "angle_delay_comp_us", 1e-6, None) if cc.get("angle_delay_comp_us") is not None
        else _nominal_position_delay(data),
        speed_tau_s=float(cc.get("speed_filter_ms", 0.5)) * 1e-3, three_sensors=bool(cc.get("three_sensors", False)),
        torque_rate_Nm_per_s=_f(cc, "torque_rate_Nm_per_ms", 1e3),
        comm_timeout_s=_f(cc, "comm_timeout_ms", 1e-3), timeout_ramp_Nm_per_s=_f(cc, "timeout_ramp_Nm_per_ms", 1e3,
                                                                                 5.0),
        reset_output=str(cc.get("reset_output", "off")), boot_s=_f(cc, "boot_ms", 1e-3, 20.0),
        restart_mode=str((data.get("policy") or {}).get("restart", "flying")),
        restart_ramp_Nm_per_s=_f(data.get("policy") or {}, "restart_ramp_Nm_per_ms", 1e3, 50.0),
        b_rot=m.b_visc, c_rot=m.c_quad,
        basis=f"project {project.label} controller: fsw {fsw / 1e3:g} kHz, PI pole-zero cancellation at the design "
              f"L_d {Ld_des * 1e6:g} uH / L_q {Lq_des * 1e6:g} uH, R {R_des * 1e3:g} mohm, {cl.get('bandwidth_Hz', 450)} "
              f"Hz; design psi {psi_des:.4g} Wb")
    table = _table(drive, speed, V_oc, tmag, twind, project.dc_limits())
    rq = sc_in.get("request")
    if rq is None:
        request = RequestProfile("constant", float(sc_in.get("torque_Nm", 150.0)))
    else:
        request = RequestProfile(str(rq.get("kind", "constant")), float(rq.get("T0_Nm", 0.0)),
                                 float(rq.get("T1_Nm", rq.get("T0_Nm", 0.0))), _f(rq, "t0_ms", 1e-3, 0.0),
                                 _f(rq, "t1_ms", 1e-3, 0.0),
                                 tuple((float(a) * 1e-3, float(b)) for a, b in (rq.get("table_ms") or ())))
    faults = []
    known = set(FAULT_KINDS)
    for f in sc_in.get("faults") or []:
        if f.get("kind") not in known:
            raise InputValidationError(f"fault kind must be one of {sorted(known)}", field="faults.kind")
        params = dict(f.get("params") or {})
        if "duration_ms" in params:
            params["duration_s"] = float(params.pop("duration_ms")) * 1e-3
        faults.append(FaultSpec(str(f["kind"]), float(f.get("t_ms", 0.0)) * 1e-3, params, str(f.get("label", ""))))
    bms = bat.get("bms")
    lims = dcs.get("limits") or {}
    bms_d = None
    if bms:
        cmax = bms.get("charge_current_max_A", lims.get("charge_current_max_A"))
        bms_d = {"charge_current_max_A": float(cmax) if cmax is not None else math.inf,
                 "delay_s": float(bms.get("delay_ms", 0.0)) * 1e-3}
    setup = SimSetup(
        machine=m, dc=dc, control=control, table=table, sensors=sensors_from(data, tol),
        roles=dict(data.get("roles") or {}), mechanisms=mechanisms_from(data), paths=paths_from(data, tol),
        policy=policy_from(data), command_period_s=float(cc.get("command_period_ms", 10.0)) * 1e-3,
        command_latency_s=float(cc.get("command_latency_ms", 0.5)) * 1e-3, request=request, speed_rpm=speed,
        horizon_s=float(sc_in.get("horizon_ms", 60.0)) * 1e-3, faults=tuple(faults),
        theta0=math.radians(float(sc_in.get("theta0_deg", 0.0))), pwm_model=str(sc_in.get("pwm_model", "averaged")),
        deadtime_s=float(ctrl.get("deadtime_us", 0.0)) * 1e-6 * float(tol.get("deadtime_scale", 1.0)),
        h_max_s=float(sc_in.get("h_max_us", 10.0)) * 1e-6, temperature_C=_f(sc_in, "temperature_C"),
        protection_enabled=bool(sc_in.get("protection", True)), reaction_override=sc_in.get("reaction_override"),
        bms=bms_d, active_discharge_delay_s=_f(dl, "active_discharge_delay_ms", 1e-3),
        identity={"project": project.label, "project_digest": project.digest(),
                  "fault_sim_from": "project" if project.has("fault_sim") else "built-in synthetic example"},
        strategies=strategies_from(data))
    setup.vehicle = vehicle_equivalent(project)
    reqs = requirements_from_dict(data.get("requirements") or {})
    info = {"architecture_basis": data.get("basis", ""), "policy_basis": setup.policy.basis,
            "overrides": dict(sc_in.get("overrides") or {}),
            "machine_basis": m.basis, "control_basis": control.basis, "table_basis": table.basis,
            "resources": data.get("resources") or {}, "tolerance_limits": tolerance_limits(data),
            "vehicle": setup.vehicle, "requirements_basis": reqs.basis}
    return setup, reqs, info
