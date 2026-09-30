"""UI-agnostic request API used by the desktop application and the CLI.

Numbers in request bodies use the units in their field names (rpm, V, N_m, A,
W, s, C, uF).  Every requirement request is converted into the documented case
format, so interactive input goes through the same validation as case files.
"""

from __future__ import annotations

import math

import numpy as np

from . import __version__
from . import service as S
from . import spec_fixtures as sf
from .decision import jsonable as _jsonable
from .errors import InputValidationError
from . import parsers as P
from .parsers import (DEFAULT_COOLANT_LOOP as DEFAULT_LOOP, capacitor_bank_from_dict as _ripple_bank,  # noqa: F401
                      coolant_from_dict as _coolant, current_loop_from_dict as _loop, curve_from_dict as _curve,
                      driveline_from_dict, emi_network_from_dict as _emi_network, module_model_from_dict,
                      noise_from_dict as _noise, num as _num, opt as _opt, pwm_timing_from_dict as _timing,
                      reducer_from_dict as _reducer, sensing_from_dict as _sensing, thermal_network_from_dict,
                      timing_chain_from_dict)
from .extensions.coolant import eg_water_properties
from .extensions.dclink import active_discharge, passive_discharge, regen_disconnect_overvoltage
from .extensions.safe_state import safe_state_screening
from .extensions.thermal import ThermalModel, thermal_duration, torque_availability
from .extensions.timing import analyze_timing
from .modulation import MODULATIONS
from .scenario import DcSourceLimits, Scenario
from .status import Claim, Reason, Status
from .solvers.policy import PolicyEvaluator
from .analysis.dominance import capability_dominance, requirement_relaxation
from .analysis.sizing import size_parameter
from .project import builtin_project

# ---------------------------------------------------------------------------------------------- product data (R2)
# Every page example takes its PRODUCT data - the module and its thermal path, the DC-link capacitor, the controller
# (switching frequency, modulation, dead time, gate edges, current loop, timing, sensing, torque path), the gearbox,
# the thermal networks, the FTTI chain and the EMI set-up - from ONE built-in synthetic project
# (examples.SYNTHETIC_PROJECT, validated by project.Project).  Only scenario inputs (operating points, missions,
# requirements, study variants) are defined with each example below.
PROJECT = builtin_project()
_CTRL = PROJECT.data("controller")

PRESETS = [
    {"key": "ts012_600", "title": {"ko": "REQ-TS-012 · 600 V", "en": "REQ-TS-012 · 600 V"},
     "hint": {"ko": "정적 가능. 전류 여유 204 A지만 토크 여유는 2.6 N·m", "en": "Static PASS; 204 A current margin but only 2.6 N·m torque margin"},
     "req": {"id": "REQ-TS-012", "text": "REQ-TS-012: inverter DC terminal 600 V, motor shaft 12,000 rpm에서 shaft torque 150 N·m를 유지한다.",
             "torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 600},
     "analyses": {"dominance": True}},
    {"key": "ts012_450", "title": {"ko": "저전압 450 V", "en": "Low voltage 450 V"},
     "hint": {"ko": "필요조건으로 불가능 증명. 인버터를 키워도 해결 안 됨", "en": "Proven infeasible; a bigger inverter does not help"},
     "req": {"id": "REQ-TS-012-LV", "text": "REQ-TS-012 at 450 V stress condition", "torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 450},
     "analyses": {"sizing": [{"parameter": "Vdc_V", "range": [400, 800]}, {"parameter": "I_peak_max_A", "range": [600, 1200], "samples": 13}],
                  "relaxation": True, "compare_Vdc": [600, 450]}},
    {"key": "ts012_10s", "title": {"ko": "10초 유지 요구", "en": "10-second hold"},
     "hint": {"ko": "전기적으로 가능해도 지속시간 근거가 없으면 UNKNOWN", "en": "Electrically feasible, duration UNKNOWN without evidence"},
     "req": {"id": "REQ-TS-012-10S", "text": "600 V, 12,000 rpm에서 150 N·m를 10초 유지한다.", "torque_Nm": 150,
             "speed_rpm": 12000, "Vdc_V": 600, "duration_s": 10}},
    {"key": "regen_80", "title": {"ko": "회생 −80 N·m", "en": "Regen −80 N·m"},
     "hint": {"ko": "에너지 회수 회생 가능", "en": "Energy-recovering regeneration feasible"},
     "req": {"id": "REQ-RG-080", "text": "12,000 rpm 회생 제동 80 N·m", "torque_Nm": -80, "speed_rpm": 12000, "Vdc_V": 600}},
    {"key": "regen_100", "title": {"ko": "회생 −100 N·m", "en": "Regen −100 N·m"},
     "hint": {"ko": "손실을 최대로 늘려도 충전 한계 초과 (배터리 수용이 병목)", "en": "Charge cap exceeded even with maximum loss"},
     "req": {"id": "REQ-RG-100", "text": "12,000 rpm 회생 제동 100 N·m", "torque_Nm": -100, "speed_rpm": 12000, "Vdc_V": 600}},
    {"key": "dis_350", "title": {"ko": "6,000 rpm 350 N·m", "en": "6,000 rpm 350 N·m"},
     "hint": {"ko": "축 출력만으로 방전 한계 초과", "en": "Shaft power alone exceeds the discharge cap"},
     "req": {"id": "REQ-PK-350", "text": "6,000 rpm에서 350 N·m", "torque_Nm": 350, "speed_rpm": 6000, "Vdc_V": 600}},
    {"key": "range", "title": {"ko": "Vdc 550–650 V 전 구간", "en": "Vdc 550–650 V range"},
     "hint": {"ko": "저전압 끝점 + 단조성 조건으로 전 구간 입증 (표본만으로는 PASS 아님)",
              "en": "Proven for the whole range from the low end by monotonicity (samples alone would not)"},
     "req": {"id": "REQ-RANGE", "text": "550~650 V 전 구간에서 12,000 rpm 100 N·m", "torque_Nm": 100, "speed_rpm": 12000,
             "Vdc_V": [550, 650]}},
    {"key": "stall", "title": {"ko": "정지 300 N·m", "en": "Standstill 300 N·m"},
     "hint": {"ko": "등가 RMS, 효율 N/A, 지속시간 미확인", "en": "Equivalent RMS, efficiency N/A, duration unknown"},
     "req": {"id": "REQ-ST-300", "text": "정지 상태 300 N·m", "torque_Nm": 300, "speed_rpm": 0, "Vdc_V": 600}},
]

EXAMPLE_TIMING = PROJECT.ftti_chain(0)

EXAMPLE_THERMAL = PROJECT.thermal_spec()


def _drive(body):
    return S.resolve_drive(body.get("drive"))


def _limits(body) -> DcSourceLimits:
    lim = body.get("limits")
    if not lim:
        return sf.synthetic_limits()
    return DcSourceLimits(lim.get("discharge_power_max_W"), lim.get("charge_power_max_W"),
                          lim.get("discharge_current_max_A"), lim.get("charge_current_max_A"), source="UI input")


def case_from_body(body) -> dict:
    r = body.get("requirement") or {}
    v = r.get("Vdc_V")
    vq = {"value": v, "unit": "V", "port": r.get("Vdc_port") or "inverter_dc_terminal"}
    req = {"id": r.get("id") or "REQ-UI", "text": r.get("text") or "(entered in the UI)",
           "target": {"value": r.get("torque_Nm"), "unit": "N*m", "torque": "shaft"},
           "conditions": {"speed": {"value": r.get("speed_rpm"), "unit": "rpm", "kind": "mechanical"}, "Vdc": vq}}
    if r.get("duration_s") not in (None, ""):
        req["duration"] = "continuous" if r["duration_s"] == "continuous" else {"value": r["duration_s"], "unit": "s"}
    if r.get("coolant_temp_C") not in (None, ""):
        req["conditions"]["coolant_temp"] = {"value": r["coolant_temp_C"], "unit": "degC"}
    for key, cond in (("magnet_temp_C", "magnet_temp"), ("winding_temp_C", "winding_temp")):
        if r.get(key) not in (None, ""):
            req["conditions"][cond] = {"value": r[key], "unit": "degC"}
    if r.get("operator") == "band":
        req["operator"] = "band"
        req["band"] = {"value": r.get("band_Nm"), "unit": "N*m"}
    case = {"drive": body.get("drive") or {"builtin": "SYNTH_IPMSM_200KW_REF_V1"}, "requirement": req,
            "analyses": body.get("analyses") or {}}
    if body.get("source_model"):
        case["source_model"] = body["source_model"]
    if body.get("error_budget"):
        case["error_budget"] = body["error_budget"]
    lim = body.get("limits")
    if lim:
        case["scenario"] = {"source_limits": {
            k2: {"value": lim[k1], "unit": u} for k1, k2, u in (
                ("discharge_power_max_W", "discharge_power_max", "W"), ("charge_power_max_W", "charge_power_max", "W"),
                ("discharge_current_max_A", "discharge_current_max", "A"),
                ("charge_current_max_A", "charge_current_max", "A")) if lim.get(k1) is not None}}
    return case


def info(body):
    d = S.resolve_drive(None)
    return {"version": __version__, "drives": ["SYNTH_IPMSM_200KW_REF_V1", "MANUFACTURED_FLUX_MAP_TEST_DRIVE"],
            "drive": S.drive_info(d), "limits": sf.synthetic_limits().describe(), "presets": PRESETS,
            "example_timing": EXAMPLE_TIMING, "example_thermal": EXAMPLE_THERMAL,
            "example_protection": EXAMPLE_PROTECTION, "example_protection_ot": EXAMPLE_PROTECTION_OT,
            "example_module": EXAMPLE_MODULE, "example_ripple": EXAMPLE_RIPPLE, "example_asc": EXAMPLE_ASC,
            "example_mission": EXAMPLE_MISSION}


def evaluate(body):
    return S.evaluate_case(case_from_body(body))


def curve(body):
    return S.capability_curve(_drive(body), _limits(body), _num(body, "Vdc_V"),
                              body.get("speeds_rpm"))


def idiq(body):
    return S.idiq_map(_drive(body), _limits(body), _num(body, "speed_rpm"), _num(body, "Vdc_V"),
                      _num(body, "torque_Nm"), int(body.get("resolution", 161)))


def sizing(body):
    sc = Scenario("sizing", _num(body, "speed_rpm"), _num(body, "Vdc_V"), _limits(body))
    rng = body.get("range") or [None, None]
    return size_parameter(_drive(body), sc, _num(body, "torque_Nm"), body.get("parameter", "Vdc_V"),
                          (float(rng[0]), float(rng[1])), int(body.get("samples", 41))).to_dict()


def dominance(body):
    sc = Scenario("dominance", _num(body, "speed_rpm"), _num(body, "Vdc_V"), _limits(body))
    return capability_dominance(_drive(body), sc, 1 if _num(body, "direction", 1) >= 0 else -1).to_dict()


def relaxation(body):
    sc = Scenario("relax", _num(body, "speed_rpm"), _num(body, "Vdc_V"), _limits(body))
    return requirement_relaxation(_drive(body), sc, _num(body, "torque_Nm")).to_dict()


def timing(body):
    return _jsonable(analyze_timing(timing_chain_from_dict(body or EXAMPLE_TIMING)))


def discharge(body):
    d = _drive(body)
    return _jsonable(active_discharge(_num(body, "C_uF") * 1e-6, _num(body, "V0_V"), _num(body, "Vf_V"),
                                      _num(body, "t_target_s"),
                                      None if body.get("R_ohm") in (None, "") else float(body["R_ohm"]),
                                      drive=d if body.get("speed_rpm") not in (None, "") else None,
                                      speed_rpm=None if body.get("speed_rpm") in (None, "") else float(body["speed_rpm"]),
                                      magnet_temp_C=_opt(body, "magnet_temp_C")))


def passive(body):
    d = _drive(body)
    return _jsonable(passive_discharge(_num(body, "C_uF") * 1e-6, _num(body, "V0_V"), _num(body, "Vf_V"),
                                       _num(body, "t_target_s"), _opt(body, "R_kohm", 1e3), _opt(body, "V_nom_V"),
                                       _opt(body, "V_max_V"), _opt(body, "P_allow_W"), _opt(body, "active_R_ohm"),
                                       drive=d if body.get("speed_rpm") not in (None, "") else None,
                                       speed_rpm=_opt(body, "speed_rpm"), magnet_temp_C=_opt(body, "magnet_temp_C")))


def overvoltage(body):
    d = _drive(body)
    p = body.get("P_in_W")
    point = None
    if p in (None, ""):
        sc = Scenario("ov", _num(body, "speed_rpm"), _num(body, "V1_V"), _limits(body))
        sol = PolicyEvaluator(d, sc).solve(_num(body, "torque_Nm"))
        if sol.point is None or sol.point.Pdc_W is None or sol.point.Pdc_W >= 0:
            raise InputValidationError("the operating point does not regenerate into the DC link (P_dc >= 0)",
                                       field="torque_Nm")
        p = -sol.point.Pdc_W
        point = {"id_A": sol.point.id_A, "iq_A": sol.point.iq_A, "Pdc_W": sol.point.Pdc_W}
    p_up = None
    if point is not None:
        # after the disconnect no battery limit applies: the same torque at the voltage limit - with less
        # field-weakening current the regenerated power is larger there (engineering review 2 of 63a2b61, F-09)
        hi = PolicyEvaluator(d, Scenario("ov@limit", _num(body, "speed_rpm"), _num(body, "V_limit_V"),
                                         DcSourceLimits())).solve(_num(body, "torque_Nm")).point
        if hi is not None and hi.Pdc_W is not None and hi.Pdc_W < 0:
            p_up = -hi.Pdc_W
            point["Pdc_at_limit_W"] = hi.Pdc_W
    out = regen_disconnect_overvoltage(_num(body, "C_uF") * 1e-6, _num(body, "V1_V"), float(p), _num(body, "V_limit_V"),
                                       None if body.get("reaction_time_ms") in (None, "") else float(body["reaction_time_ms"]) * 1e-3,
                                       body.get("profile", "constant"), drive=d,
                                       speed_rpm=None if body.get("speed_rpm") in (None, "") else float(body["speed_rpm"]),
                                       ramp_s=_opt(body, "ramp_ms", 1e-3), magnet_temp_C=_opt(body, "magnet_temp_C"),
                                       reaction=body.get("reaction") or "unspecified", P_in_upper_W=p_up)
    out["regen_operating_point"] = point
    return _jsonable(out)


def safe_state(body):
    return _jsonable(safe_state_screening(
        _drive(body), _num(body, "speed_rpm"), _num(body, "Vdc_V"), body.get("hv_state", "battery_connected"),
        None if body.get("device_voltage_rating_V") in (None, "") else float(body["device_voltage_rating_V"]),
        None if body.get("dc_link_limit_V") in (None, "") else float(body["dc_link_limit_V"]),
        body.get("hardware_paths"), body.get("transition_times_s"), body.get("rules"),
        magnet_temp_C=_opt(body, "magnet_temp_C"), winding_temp_C=_opt(body, "winding_temp_C")))


def coolant_properties(glycol_vol_pct: float, T_C: float) -> dict:
    return eg_water_properties(glycol_vol_pct, T_C)


def thermal_model_from_dict(spec, inlet_C: float | None = None) -> ThermalModel:
    """Thermal model from a spec; None -> the example model."""
    return P.thermal_model_from_dict(spec or EXAMPLE_THERMAL, inlet_C)


def thermal_details(spec, inlet_C: float | None = None) -> dict:
    """Model as used in the calculation (flow-scaled, Cauer converted) for display: networks, Z_th(inf), coolant."""
    model = thermal_model_from_dict(spec, inlet_C)
    spec = spec or EXAMPLE_THERMAL
    nodes = []
    for n, nd in zip(spec["nodes"], model.nodes):
        nodes.append({"id": nd.node_id, "input": n, "foster": {"R_K_per_W": list(nd.network.R_K_per_W),
                                                                 "tau_s": list(nd.network.tau_s)},
                      "Rth_total_K_per_W": sum(nd.network.R_K_per_W), "limit_C": nd.limit_C, "station": nd.station})
    return {"model": model, "nodes": nodes, "coolant": None if model.coolant is None else model.coolant.describe()}


def thermal(body):
    d = _drive(body)
    sc = Scenario("thermal", _num(body, "speed_rpm"), _num(body, "Vdc_V"), _limits(body),
                  coolant_temp_C=_num(body, "coolant_temp_C"),
                  initial_state=body.get("initial_state", "equilibrium_at_coolant") or None)
    model = thermal_model_from_dict(body.get("model"), sc.coolant_temp_C)
    durs = body.get("durations_s") or [1, 3, 10, 30, 60, 300, "inf"]
    durs = tuple(math.inf if x in ("inf", "continuous") else float(x) for x in durs)
    out = {"availability": torque_availability(d, sc, model, durs, 1 if _num(body, "direction", 1) >= 0 else -1)}
    if body.get("torque_Nm") not in (None, ""):
        out["request"] = thermal_duration(d, sc, model, _num(body, "torque_Nm"), _num(body, "duration_s", 10))
    return _jsonable(out)


EXAMPLE_THERMAL_CYCLE = {
    "phases": [{"name": "pulse", "torque_Nm": 450.0, "speed_rpm": 3000.0, "duration_s": 8.0},
               {"name": "rest", "torque_Nm": 50.0, "speed_rpm": 3000.0, "duration_s": 20.0}],
    "cycles": 40, "initial": {"kind": "equilibrium_at_coolant"},
    "feedback": {"enabled": True, "rs_alpha_per_K": 0.00393, "rs_reference_C": 20.0, "rs_valid_C": [-40.0, 250.0],
                 "rs_basis": "copper resistivity coefficient (declare the measured R_s reference temperature)"},
    "note": "example duty cycle (declare the target cycle; the thermal model is the project's)",
}


def thermal_cycle(body):
    """Repeated load (pulse - rest - repeat) from a stated initial thermal state with loss-temperature feedback
    (review 6198099, priority 1)."""
    from .extensions.thermal_cycle import Feedback, InitialState, LoadPhase, repeated_load
    from .models.components import TemperatureDependence
    b = {**EXAMPLE_THERMAL_CYCLE, **(body or {})}
    d = _drive(b)
    sc = Scenario("thermal-cycle", float(b["phases"][0]["speed_rpm"]), _num(b, "Vdc_V", 600.0), _limits(b),
                  coolant_temp_C=_num(b, "coolant_temp_C", 65.0))
    model = thermal_model_from_dict(b.get("model"), sc.coolant_temp_C)
    phases = [LoadPhase(float(p["torque_Nm"]), float(p["speed_rpm"]), float(p["duration_s"]), str(p.get("name", "")))
              for p in b["phases"]]
    ini = b.get("initial") or {}
    init = InitialState(str(ini.get("kind", "equilibrium_at_coolant")), _opt(ini, "torque_Nm"), _opt(ini, "speed_rpm"),
                        tuple((k, tuple(float(x) for x in v)) for k, v in (ini.get("node_temperatures_C") or {}).items()))
    fbd = b.get("feedback") or {}
    law = None
    if fbd.get("rs_alpha_per_K") not in (None, ""):
        law = TemperatureDependence(float(fbd["rs_alpha_per_K"]), tuple(fbd.get("rs_valid_C") or (-40.0, 250.0)),
                                    str(fbd.get("rs_basis") or "declared for this analysis"))
    fb = Feedback(bool(fbd.get("enabled", True)), law, _opt(fbd, "rs_reference_C"))
    return _jsonable(repeated_load(d, sc, model, phases, int(b.get("cycles") or 20), init, fb))


EXAMPLE_PROTECTION = {
    "name": "OV after battery disconnect during regeneration (synthetic toy values from the review, section 9.6.1)",
    "variable": "DC-link capacitor voltage", "unit": "V",
    "plant": {"kind": "capacitor_energy", "x0": 700.0, "C_uF": PROJECT.data("dc_link")["C_uF"], "P0_kW": 100.0,
              "t_ramp_ms": 0.0},
    "sensor": {"gain_error_pct": 0.0, "offset": 5.0, "tau_filter_ms": 0.0, "period_ms": 0.01, "phase_ms": 0.0,
               "confirm_samples": 2, "exec_delay_ms": 0.0, "comparator": ">="},
    "thresholds": {"warning": 725.0, "fault": 738.5, "release": 720.0, "E_theta": 3.0},
    "limit": 800.0, "horizon_ms": 1.0, "action_delay_ms": 0.15, "x_normal_max": 720.0, "warning_needed_ms": 0.05,
    "normal": [{"kind": "ramp", "x0": 700.0, "slope_per_s": 0.0, "ripple_amp": 15.0, "ripple_hz": 2000.0}],
    "tight_attainable": False, "hw_path": None, "phases": 16,
}

EXAMPLE_PROTECTION_OT = {
    "name": "OT derating on a one-node junction model (synthetic toy values from the review, section 9.6.2)",
    "variable": "junction temperature", "unit": "degC",
    "plant": {"kind": "thermal_1node", "x0": 135.0, "R_K_per_W": 0.2, "C_J_per_K": 20.0, "T_coolant_C": 60.0,
              "P_W": 600.0, "P_after_W": 350.0},
    "sensor": {"gain_error_pct": 0.0, "offset": 0.0, "tau_filter_ms": 0.0, "period_ms": 1.0, "phase_ms": 0.0,
               "confirm_samples": 1, "exec_delay_ms": 0.0, "comparator": ">="},
    "thresholds": {"warning": 130.0, "fault": 135.0, "release": 125.0, "E_theta": 0.0},
    "limit": 150.0, "horizon_ms": 8000.0, "action_delay_ms": 500.0, "x_normal_max": None, "warning_needed_ms": None,
    "normal": [], "tight_attainable": False, "hw_path": None, "phases": 8,
}


def _plant(d: dict):
    from .extensions.protection import Plant
    kind = d.get("kind")
    if kind == "capacitor_energy":
        return Plant(kind, float(d["x0"]), (("C_F", float(d["C_uF"]) * 1e-6), ("P0_W", float(d["P0_kW"]) * 1e3),
                                            ("t_ramp_s", float(d.get("t_ramp_ms") or 0.0) * 1e-3)))
    if kind == "thermal_1node":
        return Plant(kind, float(d["x0"]), tuple((k, float(d[k])) for k in ("R_K_per_W", "C_J_per_K", "T_coolant_C",
                                                                           "P_W", "P_after_W")))
    if kind == "ramp":
        return Plant(kind, float(d["x0"]), tuple((k, float(d.get(k) or 0.0))
                                                 for k in ("slope_per_s", "ripple_amp", "ripple_hz")))
    raise InputValidationError("plant kind must be capacitor_energy, thermal_1node or ramp", field="plant.kind")


def protection(body):
    """Threshold / derating / fault-reaction review on one causal trajectory (section 9)."""
    from .extensions.protection import Sensor, ov_trigger_bound, protection_review, simulate
    b = body or EXAMPLE_PROTECTION
    se = b.get("sensor", {})
    ms = 1e-3
    sensor = Sensor(gain_error=float(se.get("gain_error_pct") or 0.0) / 100.0, offset=float(se.get("offset") or 0.0),
                    tau_filter_s=float(se.get("tau_filter_ms") or 0.0) * ms, period_s=float(se["period_ms"]) * ms,
                    phase_s=float(se.get("phase_ms") or 0.0) * ms, confirm_samples=int(se.get("confirm_samples", 1)),
                    exec_delay_s=float(se.get("exec_delay_ms") or 0.0) * ms, comparator=se.get("comparator", ">="))
    plant = _plant(b["plant"])
    th = b.get("thresholds", {})
    fault = float(th["fault"])
    e_theta = float(th.get("E_theta") or 0.0)
    horizon = float(b["horizon_ms"]) * ms
    delay = float(b.get("action_delay_ms") or 0.0) * ms
    bound = None
    if plant.kind == "capacitor_energy":
        # the bound carries its validity and the sensor dynamics (review R2 PD-01): sampling / debounce /
        # execution / action delays are in the energy, the filter lag in the measured trigger bound
        worst_detect = sensor.confirm_samples * sensor.period_s + sensor.exec_delay_s
        bound = ov_trigger_bound(plant.p("C_F"), float(b["limit"]), plant.p("P0_W"), worst_detect + delay,
                                 plant.p("t_ramp_s", 0.0), tau_filter_s=sensor.tau_filter_s, V_start_min_V=plant.x0)
    rv = protection_review(plant, sensor, fault, float(b["limit"]), horizon, delay, e_theta,
                           tuple(_plant(n) for n in (b.get("normal") or [])),
                           None if th.get("warning") in (None, "") else float(th["warning"]),
                           None if b.get("warning_needed_ms") in (None, "") else float(b["warning_needed_ms"]) * ms,
                           None if th.get("release") in (None, "") else float(th["release"]),
                           None if b.get("x_normal_max") in (None, "") else float(b["x_normal_max"]),
                           bool(b.get("tight_attainable")), b.get("hw_path"),
                           None if bound is None else {k: bound.get(k) for k in ("status", "value", "reason",
                                                                                  "assumptions")},
                           0.0, int(b.get("phases", 16)))
    tr = rv["trace"]
    step = max(1, tr["t_s"].size // 1500)
    trace = {"t_s": tr["t_s"][::step].tolist(), "x": tr["x"][::step].tolist(), "y": tr["y"][::step].tolist(),
             "samples_t_s": tr["samples_t_s"][:400].tolist(), "samples_y": tr["samples_y"][:400].tolist(),
             "events": tr["events"], "threshold_true": tr["threshold_true"], "limit": tr["limit"],
             "protected": tr["protected"]}
    free = simulate(plant, sensor, 1e18, math.inf, horizon)          # no reaction: where would the variable go?
    trace["no_reaction_x"] = free["x"][::max(1, free["x"].size // 1500)].tolist()
    trace["no_reaction_t_s"] = free["t_s"][::max(1, free["t_s"].size // 1500)].tolist()
    trace["no_reaction_note"] = "counterfactual without any reaction (not the reviewed trajectory)"
    trace["sensor_initial_state"] = tr["sensor_initial_state"]
    return _jsonable({"name": b.get("name", ""), "variable": b.get("variable", "x"), "unit": b.get("unit", ""),
                      "thresholds": th, "limit": float(b["limit"]), "rows": rv["rows"],
                      "summary_status": rv["summary_status"], "window": rv["window"], "ov_bound": bound,
                      "phase_sweep": {k: v for k, v in rv["phase_sweep"].items() if k != "rows"},
                      "phase_rows": rv["phase_sweep"]["rows"], "trace": trace, "note": rv["note"]})


EXAMPLE_MODULE = PROJECT.module_spec()


def module_losses(body):
    """Datasheet-based inverter losses at the decision operating point, fed into P_dc (review 8.8)."""
    from dataclasses import replace as _rep
    from .models.module_loss import electrothermal_fixed_point, loss_claim, point_losses, standstill_hotspot
    b = body or {}
    mspec = b.get("module") or EXAMPLE_MODULE
    model = module_model_from_dict(mspec)
    base = _drive(b)
    tj = float(mspec.get("Tj_eval_C", 150.0))
    drv = _rep(base, inverter=_rep(base.inverter, loss=None, module_loss=model, module_Tj_C=tj))
    n, vdc, T = _num(b, "speed_rpm", 12000.0), _num(b, "Vdc_V", 600.0), _num(b, "torque_Nm", 150.0)
    sc = Scenario("module", n, vdc, _limits(b))
    sol_m = PolicyEvaluator(drv, sc).solve(T)
    sol_q = PolicyEvaluator(base, sc).solve(T)
    pt = sol_m.point
    out = {"module": {"name": mspec.get("name", ""), "technology": model.device.technology,
                      "value_kind": model.device.value_kind, "energy_basis": model.device.energy_basis,
                      "v_test_V": model.device.v_test_V, "fsw_Hz": model.fsw_Hz, "modulation": model.modulation,
                      "deadtime_s": model.deadtime_s, "Tj_eval_C": tj, "source": model.device.source},
           "request": {"speed_rpm": n, "Vdc_V": vdc, "torque_Nm": T},
           "policy_with_module": {c.name: c.status.value for c in sol_m.claims},
           "policy_with_surrogate": {c.name: c.status.value for c in sol_q.claims}}
    if pt is None:
        out["note"] = "no operating point: " + sol_m.policy_claim.detail
        return _jsonable(out)
    # the SAME evaluation the kernel used for P_dc (review R2 PT-02): at standstill the DC-current per-angle model,
    # never a rotating-period average of a point that does not rotate
    from .settings import DEFAULT_SETTINGS as _DS
    stand = abs(pt.omega_e) <= _DS.speed_zero_tol_rad_s
    full = point_losses(model, pt.id_A, pt.iq_A, pt.vd_V, pt.vq_V, vdc, tj, standstill=stand, refine_check=True)
    out["operating_point"] = {"id_A": pt.id_A, "iq_A": pt.iq_A, "i_peak_A": pt.i_peak_A, "Pac_W": pt.Pac_W,
                              "Pdc_module_W": pt.Pdc_W, "Pinv_module_W": pt.Pinv_W,
                              "Pinv_surrogate_W": None if sol_q.point is None else sol_q.point.Pinv_W,
                              "Pdc_surrogate_W": None if sol_q.point is None else sol_q.point.Pdc_W}
    out["losses"] = {k: full.get(k) for k in ("per_position_W", "per_die_W", "die_basis", "conduction_W",
                                             "switching_W", "semiconductor_W", "driver_aux_W", "dc_side_W",
                                             "hottest_position", "hottest_position_W", "sharing", "modulation_index",
                                             "power_factor", "phi_deg", "established", "problems",
                                             "angle_refinement_rel_diff", "not_modelled")}
    out["losses"]["evaluation"] = "standstill (DC phase currents, worst sampled angle)" if stand else \
        "fundamental-period average"
    if full.get("leg") is not None:
        out["leg_detail"] = {"conduction_W": full["leg"]["conduction_W"], "switching_W": full["leg"]["switching_W"],
                             "switching_fraction": full["leg"]["switching_fraction"],
                             "energy_scaling": full["leg"]["energy_scaling"]}
    out["claim"] = loss_claim(full, None if b.get("P_allow_W") in (None, "") else float(b["P_allow_W"]))
    out["standstill"] = {k: v for k, v in standstill_hotspot(model, pt.i_peak_A, vdc, tj).items() if k != "curve"}
    if mspec.get("Rth_K_per_W"):
        fp = electrothermal_fixed_point(model, {"id_A": pt.id_A, "iq_A": pt.iq_A, "vd_V": pt.vd_V, "vq_V": pt.vq_V,
                                                "Vdc_V": vdc, "standstill": stand}, float(mspec["Rth_K_per_W"]),
                                        float(mspec.get("T_ref_C", 65.0)))
        out["electrothermal"] = {k: v for k, v in fp.items() if k != "history"}
        out["electrothermal"]["iterations_history"] = fp.get("history", [])[:20]
    # losses along the torque axis (same speed): module vs surrogate
    tq = [float(x) for x in np.linspace(0.0, max(T, 1.0) * 1.25, 11)]
    rows = []
    ev_m, ev_q = PolicyEvaluator(drv, sc), PolicyEvaluator(base, sc)
    for t in tq:
        sm, sq = ev_m.solve(t).point, ev_q.solve(t).point
        det = None if sm is None else sm.inverter_loss_detail
        rows.append({"torque_Nm": t,
                     "module_W": None if sm is None or sm.Pinv_W is None else sm.Pinv_W,
                     "conduction_W": None if not det or not det["established"] else det["conduction_W"],
                     "switching_W": None if not det or not det["established"] else det["switching_W"],
                     "hottest_W": None if not det or not det["established"] else det["hottest_position_W"],
                     "surrogate_W": None if sq is None else sq.Pinv_W})
    out["torque_sweep"] = rows
    return _jsonable(out)


EXAMPLE_RIPPLE = {
    "capacitor": PROJECT.capacitor(), "source": PROJECT.source_impedance(),
    "fsw_kHz": _CTRL["fsw_kHz"], "modulation": _CTRL["modulation"],
    "requirement": {"location": "dc_link_bus", "quantity": "voltage_pp", "limit": 15.0, "bandwidth_Hz": 50e3,
                    "note": "example requirement; a real one needs the customer's measurement definition"},
}


def dclink_ripple(body):
    """Capacitor current, ripple and ESR loss at the decision operating point (review 8.9)."""
    from .extensions.dclink_ripple import ripple_analysis
    b = body or {}
    cfg = {**EXAMPLE_RIPPLE, **(b.get("ripple") or {})}
    cap = cfg["capacitor"]
    bank, source = _ripple_bank(cfg)
    d = _drive(b)
    n, vdc, T = _num(b, "speed_rpm", 6000.0), _num(b, "Vdc_V", 600.0), _num(b, "torque_Nm", 150.0)
    sol = PolicyEvaluator(d, Scenario("ripple", n, vdc, _limits(b))).solve(T)
    pt = sol.point
    if pt is None:
        raise InputValidationError("no operating point for the ripple analysis: " + sol.policy_claim.detail,
                                   field="torque_Nm")
    phi = math.atan2(pt.vq_V, pt.vd_V) - math.atan2(pt.iq_A, pt.id_A)
    m = pt.v_peak_V / (0.5 * vdc)
    fe = abs(pt.f_e_Hz)
    r = ripple_analysis(pt.i_peak_A, m, phi, fe, float(cfg.get("fsw_kHz", 10.0)) * 1e3, vdc, bank, source,
                        cfg.get("modulation", "svpwm"), cfg.get("requirement"),
                        None if cap.get("T_ref_C") in (None, "") else float(cap["T_ref_C"]),
                        required_life_h=None if cfg.get("required_life_h") in (None, "", 0) else
                        float(cfg["required_life_h"]))
    r["operating_point"] = {"id_A": pt.id_A, "iq_A": pt.iq_A, "Pdc_W": pt.Pdc_W, "Idc_avg_A": pt.Idc_A,
                            "speed_rpm": n, "torque_Nm": T}
    r["config"] = cfg
    return _jsonable(r)


EXAMPLE_ASC = {
    "speed_rpm": 6000.0, "Vdc_V": 600.0, "torque_Nm": 150.0, "t_delay_ms": 1.0, "t_6so_ms": 0.0,
    "horizon_ms": 300.0, "J_kgm2": None,
    "requirements": [
        {"req_id": "ASC-PEAK", "quantity": "phase", "operator": "abs_peak", "t_start_s": 0.0, "t_end_s": 0.010,
         "limit_A": 1200.0, "origin": "asc_established",
         "text": "example: phase current peak <= 1200 A within 10 ms after the short (synthetic value)"},
        {"req_id": "ASC-RMS", "quantity": "phase", "operator": "rms", "t_start_s": 0.050, "t_end_s": 0.100,
         "limit_A": 300.0, "origin": "asc_established",
         "text": "example: phase RMS <= 300 A between 50 and 100 ms after the short (synthetic value)"}],
    "demag_id_min_A": None, "demag_basis": "", "device_peak_A": None, "device_i2t_A2s": None, "device_basis": "",
}


def asc(body):
    """ASC fault transient and the customer's current-time requirements on one trajectory (review 9.13)."""
    from .extensions.asc_transient import CurrentTimeRequirement, asc_transient
    b = {**EXAMPLE_ASC, **(body or {})}
    d = _drive(b)
    n, vdc = float(b["speed_rpm"]), float(b["Vdc_V"])
    sc = Scenario("asc", n, vdc, _limits(b), magnet_temp_C=_opt(b, "magnet_temp_C"),
                  winding_temp_C=_opt(b, "winding_temp_C"))
    sol = PolicyEvaluator(d, sc).solve(float(b["torque_Nm"]))
    if sol.point is None:
        raise InputValidationError("no pre-fault operating point: " + sol.policy_claim.detail, field="torque_Nm")
    reqs = tuple(CurrentTimeRequirement(**{k: r[k] for k in ("req_id", "quantity", "operator", "t_start_s", "t_end_s",
                                                              "limit_A", "origin", "text", "limit_s") if k in r},
                                        level_A=r.get("level_A")) for r in (b.get("requirements") or []))
    r = asc_transient(d, Scenario("asc", n, vdc, DcSourceLimits(), magnet_temp_C=_opt(b, "magnet_temp_C"),
                                  winding_temp_C=_opt(b, "winding_temp_C")),
                      sol.point.id_A, sol.point.iq_A, float(b["t_delay_ms"]) * 1e-3, float(b["horizon_ms"]) * 1e-3,
                      reqs, float(b.get("t_6so_ms") or 0.0) * 1e-3, _opt(b, "J_kgm2"), _opt(b, "demag_id_min_A"),
                      str(b.get("demag_basis") or ""), _opt(b, "device_peak_A"), _opt(b, "device_i2t_A2s"),
                      str(b.get("device_basis") or ""))
    r["request"] = {k: b[k] for k in ("speed_rpm", "Vdc_V", "torque_Nm", "t_delay_ms", "horizon_ms")}
    r["requirement_texts"] = {q.req_id: q.text for q in reqs}
    return _jsonable(r)


EXAMPLE_MISSION = {
    "segments": [{"duration_s": 10.0, "speed_rpm": 2000.0, "torque_Nm": 250.0},
                 {"duration_s": 20.0, "speed_rpm": 5000.0, "torque_Nm": 60.0},
                 {"duration_s": 8.0, "speed_rpm": 4000.0, "torque_Nm": -120.0},
                 {"duration_s": 15.0, "speed_rpm": 0.0, "torque_Nm": 0.0},
                 {"duration_s": 6.0, "speed_rpm": 1000.0, "torque_Nm": 300.0},
                 {"duration_s": 25.0, "speed_rpm": 8000.0, "torque_Nm": 40.0}],
    "repeat_in_trace": 3, "dt_s": 0.05, "Vdc_V": 600.0, "coolant_C": 65.0,
    "junction_network": {**PROJECT.junction_network(),
                         "note": PROJECT.junction_network()["note"] + "; used for every die unless "
                                                                      "junction_networks names the die's role"},
    "junction_networks": None,
    "mission_kind": "finite",
    "cycling_model": None, "D_allow": None, "mission_repeats": 1.0, "ton_rule": "none", "cutoff_K": 0.0,
    "note": "synthetic mission; the Tj histories come from a screening electrothermal chain at a fixed "
            "loss-evaluation temperature (screening damage only)",
}


def _damage_over_devices(per: dict) -> dict:
    """ONE fatigue verdict over every physical die: violated if one die is, established only if every die is."""
    claims = {d: r["damage"]["claim"] for d, r in per.items()}
    bad = [d for d, c in claims.items() if c["status"] == "INFEASIBLE"]
    if bad:
        c = dict(claims[bad[0]])
        c["detail"] = f"die {bad[0]}: " + c["detail"]
        return c
    if all(c["status"] == "FEASIBLE" for c in claims.values()):
        c = dict(next(iter(claims.values())))
        c["detail"] = "every die: " + "; ".join(f"{d}: {v['detail']}" for d, v in claims.items())
        return c
    d0 = next(d for d, c in claims.items() if c["status"] != "FEASIBLE")
    c = dict(claims[d0])
    c["detail"] = f"die {d0}: " + c["detail"]
    return c


def lifetime(body):
    """Mission -> losses per PHYSICAL die (datasheet module model) -> one Tj(t) per die -> rainflow -> conditional
    damage per die (review 12; R2 PT-06 / PT-07)."""
    from dataclasses import replace as _rep
    from .extensions.lifetime import CyclingModel, cycle_analysis, die_mission, foster_trace
    b = {**EXAMPLE_MISSION, **(body or {})}
    cm = b.get("cycling_model")
    model_obj = None if not cm else CyclingModel(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in cm.items()})
    ton_rule = b.get("ton_rule", "none")
    D_allow = None if b.get("D_allow") in (None, "") else float(b["D_allow"])
    cutoff, repeats = float(b.get("cutoff_K") or 0.0), float(b.get("mission_repeats") or 1.0)
    qualifiers = []
    if b.get("trace"):
        tr = b["trace"]
        t, T = np.asarray(tr["t_s"], float), np.asarray(tr["T_C"], float)
        dev = str(tr.get("device") or "declared device")
        basis = str(tr.get("basis") or "").strip()
        qualified = bool(tr.get("qualified")) and bool(basis)
        note = ("qualified history of one junction: " + basis) if qualified else (
            "imported history without a declared qualification of its junction and source")
        per = {dev: cycle_analysis(t, T, model_obj, ton_rule, D_allow, cutoff, repeats,
                                   repeating_mission=bool(b.get("repeating_mission", False)),
                                   source="imported Tj trace", trace_kind="declared" if qualified else "screening",
                                   trace_note=note)}
        traces = {dev: T}
        seg_rows, kind, src = [], "imported", "imported Tj trace"
        qualifiers.append(note)
    else:
        mspec = b.get("module") or EXAMPLE_MODULE
        model = module_model_from_dict(mspec)
        base = _drive(b)
        tj_eval = float(mspec.get("Tj_eval_C", 150.0))
        drv = _rep(base, inverter=_rep(base.inverter, loss=None, module_loss=model, module_Tj_C=tj_eval))
        kind = b.get("mission_kind") or "finite"
        seg_rows, die_W, unknown, unachieved = [], [], [], []
        for k, sg in enumerate(b["segments"]):
            sc = Scenario("mission", float(sg["speed_rpm"]), float(b["Vdc_V"]), _limits(b))
            sol = PolicyEvaluator(drv, sc).solve(float(sg["torque_Nm"]))
            if sol.point is None:
                raise InputValidationError(f"segment {k + 1} {sg}: no operating point ({sol.policy_claim.detail}) - "
                                           f"an unachieved segment is never credited as mission", field="segments")
            det = sol.point.inverter_loss_detail
            if not det or not det.get("established"):
                raise InputValidationError(f"segment {k + 1} {sg}: module losses not established "
                                           f"({None if not det else det.get('problems')})", field="segments")
            infeasible = [c.name for c in (sol.policy_claim, sol.dc_claim) if c is not None and c.status is
                          Status.INFEASIBLE]
            if infeasible:
                unachieved.append(k + 1)
            pd = det.get("per_die_thermal_W") or {}
            if not pd:
                unknown.append(k + 1)
            die_W.append(pd)
            seg_rows.append({**sg, "policy": sol.policy_claim.status.value, "achieved": not infeasible,
                             "P_hot_device_W": det["hottest_position_W"], "hottest_die": det["hottest_position"],
                             "per_die_W": pd or None})
        durations = [float(sg["duration_s"]) for sg in b["segments"]]
        nets_by_role = b.get("junction_networks") or {}
        net0 = b["junction_network"]
        src = ("screening electrothermal chain: datasheet module losses per physical die at the policy points "
               f"(fixed Tj {tj_eval:g} degC, no loss(Tj) feedback), one Foster network per die")
        qualifiers += [f"losses evaluated at a fixed Tj of {tj_eval:g} degC (no loss(Tj) feedback): screening chain",
                       "die-to-die thermal coupling not modelled",
                       "fundamental-period average losses (no sub-fundamental junction ripple)"]
        if unknown:
            # the per-die heat of a standstill segment with current depends on the unknown electrical angle: no die
            # history exists, only the hottest-die envelope (a temperature screening, never a fatigue input)
            t_list, p_list, now = [], [], 0.0
            dt = float(b.get("dt_s") or 0.05)
            for _ in range(int(b.get("repeat_in_trace") or 1)):
                for sg, row in zip(b["segments"], seg_rows):
                    n = max(2, int(round(float(sg["duration_s"]) / dt)))
                    t_list.append(now + np.arange(n) * (float(sg["duration_s"]) / n))
                    p_list.append(np.full(n, row["P_hot_device_W"]))
                    now += float(sg["duration_s"])
            t = np.concatenate(t_list + [np.array([now])])
            P = np.concatenate(p_list + [np.array([0.0])])
            T_env = foster_trace(t, P, tuple(net0["R_K_per_W"]), tuple(net0["tau_s"]), float(b["coolant_C"]))
            env = cycle_analysis(t, T_env, None, source="hottest-die envelope (temperature screening only)")
            env["damage"]["claim"] = Claim(
                "thermal_cycling_damage", Status.UNKNOWN, "thermal-fatigue damage per physical die",
                "per-die mission", reasons=(Reason.MISSING_INPUT,),
                detail=f"segment(s) {unknown} at standstill with current: the per-die heat depends on the unknown "
                       f"electrical angle, so no die history exists (the hottest-die envelope is a temperature "
                       f"screening, not a fatigue input)").to_dict()
            step = max(1, t.size // 3000)
            env["trace"] = {"t_s": t[::step].tolist(), "T_C": T_env[::step].tolist(),
                            "basis": "hottest-die envelope (not one device)"}
            env.update(segments=seg_rows, devices={}, governing_device=None, mission_kind=kind, qualifiers=qualifiers)
            env["cycles"] = env["cycles"][:500]
            return _jsonable(env)
        nets = {}
        for d in die_W[0]:
            role = d.split("_", 1)[-1]
            n_ = nets_by_role.get(role) or net0
            nets[d] = (tuple(n_["R_K_per_W"]), tuple(n_["tau_s"]))
        ms = die_mission(durations, die_W, nets, float(b["coolant_C"]), float(b.get("dt_s") or 0.05), kind,
                         int(b.get("repeat_in_trace") or 1))
        t = ms["t_s"]
        traces = ms["T_C"]
        note = "generated screening chain (" + ms["basis"] + ")"
        per = {d: cycle_analysis(t, traces[d], model_obj, ton_rule, D_allow, cutoff, repeats, repeating_mission=True,
                                 source=src, trace_kind="screening", trace_note=note) for d in ms["dies"]}
        qualifiers.append(ms["basis"])
        if unachieved:
            for d in per:
                per[d]["damage"]["claim"] = Claim(
                    "thermal_cycling_damage", Status.UNKNOWN, "thermal-fatigue damage per physical die",
                    "per-die mission", reasons=(Reason.OUTSIDE_ALLOWED_OPERATING_DOMAIN,),
                    detail=f"segment(s) {unachieved} are not achieved (policy / DC claim INFEASIBLE): no lifetime "
                           f"for a mission the drive does not perform").to_dict()

    def rank(d):
        dm = per[d]["damage"]
        return (dm.get("D") if dm.get("D") is not None else -1.0, per[d]["max_range_K"], d)
    gov = max(sorted(per), key=rank)
    r = dict(per[gov])
    r["damage"] = {**per[gov]["damage"], "claim": _damage_over_devices(per)}
    r["devices"] = {d: {"max_range_K": v["max_range_K"], "T_max_C": v["T_max_C"], "T_min_C": v["T_min_C"],
                        "reversals": v["reversals"], "cycles_counted": v["damage"]["cycles_counted"],
                        "D": v["damage"].get("D"), "claim": v["damage"]["claim"]["status"],
                        "histogram": v["histogram"]} for d, v in sorted(per.items())}
    r["governing_device"] = gov
    r["mission_kind"] = kind
    r["qualifiers"] = qualifiers
    step = max(1, t.size // 3000)
    r["trace"] = {"t_s": t[::step].tolist(), "T_C": np.asarray(traces[gov])[::step].tolist(),
                  "device": gov, "per_device_T_C": {d: np.asarray(v)[::step].tolist() for d, v in sorted(traces.items())}}
    r["segments"] = seg_rows
    r["cycles"] = r["cycles"][:500]
    return _jsonable(r)


# ---------------------------------------------------------------------------------------------- OEW / HEV (addendum)

INF = math.inf


def _lim_dict(d, name):
    if not d:
        return None
    return DcSourceLimits(d.get("discharge_power_max_W"), d.get("charge_power_max_W"), d.get("discharge_current_max_A"),
                          d.get("charge_current_max_A"), source=name)


def _opt_f(d, key, scale=1.0):
    v = (d or {}).get(key)
    return None if v in (None, "") else float(v) * scale


def oew_topology_from_dict(t: dict):
    from .extensions.oew import OewTopology, ZeroSequenceModel
    zs = t.get("zero_sequence")
    zsm = None
    if zs:
        harm = tuple((int(h[0]), float(h[1]) * 1e-3, math.radians(float(h[2]) if len(h) > 2 else 0.0))
                     for h in (zs.get("psi0_mWb_deg") or []))
        zsm = ZeroSequenceModel(float(zs["L0_uH"]) * 1e-6, harm, _opt_f(zs, "R0_mohm", 1e-3), str(zs.get("basis", "")))
    return OewTopology(t.get("kind", "common_bus"), float(t["VA_V"]), _opt_f(t, "VB_V"), zsm,
                       t.get("zs_policy", "regulate_i0"), _opt_f(t, "power_split_A"),
                       _lim_dict(t.get("limits_A"), "OEW source A"), _lim_dict(t.get("limits_B"), "OEW source B"),
                       _opt_f(t, "bridge_current_limit_A"), _opt_f(t, "reserve_fraction"), str(t.get("revision", "")),
                       str(t.get("basis", "")))


EXAMPLE_OEW = {
    "topology": {"kind": "common_bus", "VA_V": 400.0, "VB_V": None, "zs_policy": "regulate_i0",
                 "zero_sequence": {"L0_uH": 50.0, "psi0_mWb_deg": [[3, 4.0, 0.0]], "R0_mohm": None,
                                   "basis": "synthetic example values (not measured) - replace with L0 / triplen data"},
                 "power_split_A": None, "bridge_current_limit_A": None, "reserve_fraction": None,
                 "limits_A": {"discharge_power_max_W": 250e3, "charge_power_max_W": 150e3,
                              "discharge_current_max_A": INF, "charge_current_max_A": INF},
                 "limits_B": None, "basis": "synthetic OEW example: two bridges of the example module on one bus"},
    "speed_rpm": 10000.0, "torque_Nm": 150.0, "use_module": True, "fsw_kHz": _CTRL["fsw_kHz"], "carrier_shift": 0.0,
    # the example module was characterised at 600 V: the 400 V bridges need a declared switching-energy scaling
    "module": {**EXAMPLE_MODULE, "vdc_scaling": {"exponent": 1.0, "valid_V": [300.0, 700.0],
                                                 "basis": "synthetic example assumption E ~ V (replace with measured "
                                                          "E(V) data)"}},
    "compare_speeds_rpm": [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0, 8000.0, 10000.0, 12000.0, 14000.0, 16000.0],
    "magnet_temp_C": None, "winding_temp_C": None,
}


def _oew_drive(b):
    from dataclasses import replace as _rep
    d = _drive(b)
    if b.get("use_module"):
        mspec = b.get("module") or EXAMPLE_MODULE
        d = _rep(d, inverter=_rep(d.inverter, loss=None, module_loss=module_model_from_dict(mspec),
                                  module_Tj_C=float(mspec.get("Tj_eval_C", 150.0))))
    return d


def oew(body):
    """Open-end-winding dual inverter at one request: minimum-current witness, geometry, paired states, i0 ripple."""
    from .extensions.dclink import back_emf_ll_peak
    from .extensions.oew import (b_clamp_vs_floating_star, oew_min_current_point, paired_state_table,
                                 switch_state_geometry, zero_sequence_switching_ripple)
    b = {**EXAMPLE_OEW, **(body or {})}
    topo = oew_topology_from_dict(b["topology"])
    d = _oew_drive(b)
    n, T = _num(b, "speed_rpm"), _num(b, "torque_Nm")
    mt, wt = _opt(b, "magnet_temp_C"), _opt(b, "winding_temp_C")
    r = oew_min_current_point(d, topo, n, T, mt, wt)
    geo = switch_state_geometry(topo.VA_V, topo.VB_V, topo.kind)
    emf_ll = back_emf_ll_peak(d, n, mt)
    emf = None if emf_ll is None else emf_ll / math.sqrt(3.0)
    zs = topo.zero_sequence
    out = {"topology": topo.describe(), "request": {"speed_rpm": n, "torque_Nm": T}, "result": r,
           "geometry": {k: v for k, v in geo.items() if k != "rows"}, "state_rows": geo["rows"],
           "b_clamp": b_clamp_vs_floating_star(topo.VA_V), "emf_phase_peak_V": emf,
           "paired_states": paired_state_table(topo, emf, None if zs is None else zs.L0_H),
           "loss_model": "datasheet module per bridge" if b.get("use_module") else "drive's declared loss model"}
    w = r.get("witness")
    if topo.kind == "common_bus" and zs is not None and w is not None:
        op = w["operating_point"]
        we = d.motor.pole_pairs * 2 * math.pi * n / 60
        u0 = (lambda th: we * zs.dpsi0(th)) if topo.zs_policy == "regulate_i0" else None
        rip = {}
        for shift in (float(b.get("carrier_shift") or 0.0), 0.5):
            rip[f"{shift:g}"] = zero_sequence_switching_ripple(
                topo.VA_V, op["U_phase_pk_V"], math.atan2(op["vq_V"], op["vd_V"]), float(b.get("fsw_kHz", 10.0)) * 1e3,
                we / (2 * math.pi), zs.L0_H, zs.R0_ohm if zs.R0_ohm is not None else d.motor.Rs_ohm, topo.split,
                shift, u0)
        out["i0_ripple"] = rip
    return _jsonable(out)


def oew_compare(body):
    """Same motor, same grid method: single VSI, common-bus OEW, isolated OEW, single VSI on the same stack."""
    from .extensions.oew import capability_comparison
    b = {**EXAMPLE_OEW, **(body or {})}
    topo = oew_topology_from_dict(b["topology"])
    d = _drive(b)
    r = capability_comparison(d, topo.VA_V, [float(x) for x in b["compare_speeds_rpm"]], topo.zero_sequence,
                              topo.zs_policy if topo.kind == "common_bus" else "regulate_i0",
                              topo.VB_V if topo.kind == "isolated" else None, int(b.get("direction", 1)),
                              _opt(b, "magnet_temp_C"), _opt(b, "winding_temp_C"))
    return _jsonable(r)


EXAMPLE_HEV = {
    "Vdc_V": 600.0, "aux_W": 1500.0, "bus_loss_W": 0.0, "n_levels": 13,
    "machines": [{"name": "EM1", "role": "generator / starter (P1)", "speed_rpm": 3000.0, "drive": None},
                 {"name": "EM2", "role": "traction (P2)", "speed_rpm": 6000.0, "drive": None}],
    "request_Nm": [250.0, -110.0],
    "battery": {"ocv_V": 400.0, "R_int_mohm": 50.0, "uv_min_V": 250.0, "basis": "synthetic example battery",
                "limits": {"discharge_power_max_W": 100e3, "charge_power_max_W": 30e3,
                           "discharge_current_max_A": INF, "charge_current_max_A": INF}},
    "boost": {"D_max": 0.5, "I_L_max_A": 400.0, "a0_W": 150.0, "a2_W_per_A2": 0.004, "bidirectional": True,
              "basis": "synthetic example boost"},
    "use_boost": True, "cooling_heat_max_W": None,
    "crank": {"ratio": 2.5, "T_cmd_Nm": 60.0, "n_target_rpm": 800.0, "t_max_s": 0.6, "V_floor_V": 300.0,
              "theta0_deg": [0, 20, 40, 60, 80, 100, 120, 140, 160], "traction_reserve_W": 20e3, "aux_elec_W": 800.0,
              "load": {"angle_deg": [0, 30, 60, 90, 120, 150, 180], "torque_Nm": [0, 40, 90, 60, -40, -60, 0],
                       "period_deg": 180.0, "f0_Nm": 25.0, "f1_Nm_s": 0.3, "f2_Nm_s2": 0.0, "aux_Nm": 0.0,
                       "J_kgm2": 0.25, "basis": "synthetic crank-angle load (not an engine test trace)"}},
    "rejection": {"C_uF": 500.0, "V0_V": 400.0, "V_max_V": 450.0, "sources_W": [70e3], "sinks_W": [20e3],
                  "t_react_ms": 1.0, "t_ramp_ms": 0.0},
    "planetary": {"Ns": 30, "Nr": 78, "known_rpm": {"ring": 3000.0, "carrier": 2000.0}, "port": "carrier",
                  "torque_Nm": 150.0, "limits_rpm": {"sun": 10000.0, "carrier": 6000.0, "ring": 12000.0}},
}


def _battery(bt: dict):
    from .extensions.hev import Battery
    return Battery(float(bt["ocv_V"]), float(bt.get("R_int_mohm") or 0.0) * 1e-3,
                   _lim_dict(bt.get("limits"), "battery") or DcSourceLimits(), _opt_f(bt, "uv_min_V"),
                   str(bt.get("basis", "")))


def hev_joint(body):
    """Joint torque set of two machines on one bus, branch vs net power (addendum 6.2)."""
    from .extensions.hev import BoostStage, BusMachine, joint_torque_set
    b = {**EXAMPLE_HEV, **(body or {})}
    ms = []
    for m in b["machines"]:
        drv = S.resolve_drive(m.get("drive")) if m.get("drive") else _drive(b)
        ms.append(BusMachine(str(m["name"]), drv, float(m["speed_rpm"]), str(m.get("role", ""))))
    boost = None
    if b.get("use_boost") and b.get("boost"):
        bs = b["boost"]
        boost = BoostStage(float(bs["D_max"]), float(bs["I_L_max_A"]), float(bs.get("a0_W") or 0.0),
                           float(bs.get("a2_W_per_A2") or 0.0), bool(bs.get("bidirectional", True)), str(bs.get("basis", "")))
    req = b.get("request_Nm")
    r = joint_torque_set(ms, _num(b, "Vdc_V"), _battery(b["battery"]), float(b.get("aux_W") or 0.0),
                         float(b.get("bus_loss_W") or 0.0), boost, int(b.get("n_levels", 13)),
                         None if not req else (float(req[0]), float(req[1])),
                         _opt(b, "cooling_heat_max_W"))
    for m in r["tables"].values():
        for row in m:
            row.pop("detail", None)
    return _jsonable(r)


def hev_crank(body):
    """Cranking replay with a declared crank-angle load over sampled initial angles (addendum 6.3)."""
    from .extensions.hev import CrankLoad, cranking_replay
    b = {**EXAMPLE_HEV, **(body or {})}
    c = {**EXAMPLE_HEV["crank"], **(b.get("crank") or {})}
    ld = c["load"]
    load = CrankLoad(tuple(float(x) for x in ld["angle_deg"]), tuple(float(x) for x in ld["torque_Nm"]),
                     float(ld.get("period_deg", 180.0)), float(ld.get("f0_Nm") or 0.0), float(ld.get("f1_Nm_s") or 0.0),
                     float(ld.get("f2_Nm_s2") or 0.0), float(ld.get("aux_Nm") or 0.0), float(ld["J_kgm2"]),
                     str(ld.get("basis", "")))
    drv = S.resolve_drive(c.get("drive")) if c.get("drive") else _drive(b)
    r = cranking_replay(drv, float(c["ratio"]), load, _battery(b["battery"]), float(c["T_cmd_Nm"]),
                        float(c["n_target_rpm"]), float(c["t_max_s"]), float(c["V_floor_V"]),
                        [float(x) for x in c.get("theta0_deg") or []] or None, float(c.get("traction_reserve_W") or 0.0),
                        float(c.get("aux_elec_W") or 0.0))
    r["load_table"] = {"angle_deg": list(load.angle_deg), "torque_Nm": list(load.torque_Nm), "period_deg": load.period_deg}
    return _jsonable(r)


def hev_rejection(body):
    """Load-rejection energy ledger on the one common capacitor (addendum 6.4 / 9.4)."""
    from .extensions.hev import load_rejection
    b = {**EXAMPLE_HEV, **(body or {})}
    c = {**EXAMPLE_HEV["rejection"], **(b.get("rejection") or {})}
    return _jsonable(load_rejection(float(c["C_uF"]), float(c["V0_V"]), float(c["V_max_V"]),
                                    [float(x) for x in c["sources_W"]], [float(x) for x in c["sinks_W"]],
                                    float(c["t_react_ms"]) * 1e-3, float(c.get("t_ramp_ms") or 0.0) * 1e-3))


def hev_planetary(body):
    """Simple planetary kinematics, ideal torque ratio and the H-04 check."""
    from .extensions.hev import planetary_check, planetary_speeds, planetary_torques
    b = {**EXAMPLE_HEV, **(body or {})}
    c = {**EXAMPLE_HEV["planetary"], **(b.get("planetary") or {})}
    Ns, Nr = int(c["Ns"]), int(c["Nr"])
    kn = {k: float(v) for k, v in (c.get("known_rpm") or {}).items()}
    sp = planetary_speeds(Ns, Nr, kn.get("sun"), kn.get("ring"), kn.get("carrier"))
    tq = planetary_torques(Ns, Nr, c.get("port", "carrier"), float(c["torque_Nm"]))
    chk = planetary_check(Ns, Nr, sp, tq, {k: float(v) for k, v in (c.get("limits_rpm") or {}).items()})
    return _jsonable({"Ns": Ns, "Nr": Nr, "speeds_rpm": sp, "torques_Nm": tq, "check": chk,
                      "powers_W": {k: tq[k] * sp[k] * 2 * math.pi / 60 for k in sp},
                      "convention": "torques positive INTO the gear set; massless, lossless ideal set"})


# ---------------------------------------------------------------------------------------------- conducted EMI (P1-C)

EXAMPLE_EMI = {
    "speed_rpm": 6000.0, "torque_Nm": 150.0, "Vdc_V": 600.0,
    "source": PROJECT.emi_source(),
    "network": PROJECT.emi_network(),
    "profile": {"standard": "EXAMPLE (enter the standard)", "edition": "EXAMPLE", "customer_revision": "EXAMPLE",
                "curve_id": "EXAMPLE-FLAT-70", "port": "HV+ / HV-", "method": "voltage_AN",
                "detector": "peak", "rbw_Hz": 9000.0, "network": "AN 5 uH / 50 ohm (declare per your standard)",
                "fixture": "EXAMPLE", "operating_condition": "EXAMPLE", "design_reserve_dB": 6.0,
                "decision_rule": None},
    "limit": {"points": [[150e3, 70.0], [30e6, 70.0]], "unit": "dBuV", "detector": "peak", "gaps_Hz": [],
              "source": "EXAMPLE ONLY - not a standard limit; enter the approved curve"},
    "band_MHz": [0.15, 30.0], "n_grid": 160, "calibration": None, "E_y_allowed_J": None, "f_control_Hz": 1000.0,
    "measured": None,
}


def _emi_profile(pr: dict, lim: dict | None):
    from .extensions.emi import PROFILE_FIELDS, EmiProfile, LimitCurve
    curve = None
    if lim and lim.get("points"):
        curve = LimitCurve(tuple(tuple(x) for x in lim["points"]), lim.get("unit", "dBuV"), lim.get("detector", "peak"),
                           str(lim.get("source", "")), tuple(tuple(g) for g in (lim.get("gaps_Hz") or ())))
    fields = {k: pr.get(k) for k in PROFILE_FIELDS + ("decision_rule", "min_dwell_s")}
    reserve = pr.get("design_reserve_dB")
    return EmiProfile(fields, curve, 0.0 if reserve in (None, "") else _num(pr, "design_reserve_dB"))


def emi(body):
    """Conducted-emission screening of the HV DC port at one operating point (source -> path -> receiver)."""
    from .extensions.emi import (SwitchingSource, conducted_emission_screening, coupling_checks,
                                 measured_trace_verdict)
    b = {**EXAMPLE_EMI, **(body or {})}
    d = _drive(b)
    n, T, vdc = _num(b, "speed_rpm"), _num(b, "torque_Nm"), _num(b, "Vdc_V")
    sol = PolicyEvaluator(d, Scenario("emi", n, vdc, _limits(b))).solve(T)
    pt = sol.point
    if pt is None:
        raise InputValidationError("no operating point for the EMI source: " + sol.policy_claim.detail, field="torque_Nm")
    if abs(pt.f_e_Hz) <= 0:
        raise InputValidationError("EMI screening needs a rotating operating point (fe > 0)", field="speed_rpm")
    sc = {**EXAMPLE_EMI["source"], **(b.get("source") or {})}
    src = SwitchingSource(vdc, pt.i_peak_A, math.atan2(pt.iq_A, pt.id_A), pt.v_peak_V / (0.5 * vdc),
                          math.atan2(pt.vq_V, pt.vd_V), abs(pt.f_e_Hz), _num(sc, "fsw_kHz") * 1e3,
                          _num(sc, "t_rise_ns") * 1e-9, _num(sc, "t_fall_ns") * 1e-9,
                          float(sc.get("t_dead_us") or 0.0) * 1e-6, sc.get("modulation", "svpwm"), str(sc.get("basis", "")),
                          str(sc.get("carrier") or "asynchronous"), float(sc.get("min_pulse_us") or 0.0) * 1e-6,
                          str(sc.get("min_pulse_policy") or "none"))
    net = _emi_network({**EXAMPLE_EMI["network"], **(b.get("network") or {})})
    prof = _emi_profile({**EXAMPLE_EMI["profile"], **(b.get("profile") or {})}, b.get("limit"))
    lo, hi = (float(x) * 1e6 for x in b.get("band_MHz") or (0.15, 30.0))
    r = conducted_emission_screening(src, net, prof, lo, hi, int(b.get("n_grid", 160)), b.get("calibration"))
    r["coupling"] = coupling_checks(net, vdc, src, _opt(b, "E_y_allowed_J"), _opt(b, "f_control_Hz"))
    r["operating_point"] = {"speed_rpm": n, "torque_Nm": T, "Vdc_V": vdc, "i_peak_A": pt.i_peak_A,
                            "modulation_index": src.m, "f_e_Hz": src.fe_Hz, "policy": sol.policy_claim.status.value}
    r["source"] = {"fsw_kHz": sc["fsw_kHz"], "fsw_requested_kHz": src.fsw_Hz / 1e3,
                   "fsw_used_kHz": src.fsw_used_Hz / 1e3, "carrier_ratio": src.carrier_ratio, "carrier": src.carrier,
                   "t_rise_ns": sc["t_rise_ns"], "t_fall_ns": sc["t_fall_ns"], "t_dead_us": sc.get("t_dead_us"),
                   "min_pulse_us": src.min_pulse_s * 1e6, "min_pulse_policy": src.min_pulse_policy,
                   "basis": src.basis, "validity": r["source_validity"]}
    r["network"] = {**EXAMPLE_EMI["network"], **(b.get("network") or {})}
    r["profile"] = {**prof.fields, "design_reserve_dB": prof.design_reserve_dB,
                    "limit_source": None if prof.limit is None else prof.limit.source,
                    "limit_gaps_Hz": [] if prof.limit is None else [list(g) for g in prof.limit.gaps]}
    ms = b.get("measured")
    if ms and ms.get("f_Hz"):
        band = ms.get("band_MHz")
        U = ms.get("U_meas_dB")
        r["measured"] = measured_trace_verdict(ms["f_Hz"], ms["level_dB"], prof, U, ms.get("noise_floor_dB"),
                                               None if not band else (float(band[0]) * 1e6, float(band[1]) * 1e6),
                                               ms.get("meta"))
        r["measured"]["f_Hz"] = list(ms["f_Hz"])
        r["measured"]["level_dB"] = list(ms["level_dB"])
    return _jsonable(r)


def emi_oew(body):
    """Common-bus OEW: winding zero sequence vs chassis common mode, branch DC currents (C-01, C-02)."""
    from .extensions.emi import oew_common_mode, zsv_free_sequence_example
    from .extensions.oew import OewTopology, oew_min_current_point
    b = {**EXAMPLE_OEW, **(body or {})}
    topo = oew_topology_from_dict({**b["topology"], "kind": "common_bus", "VB_V": None, "limits_B": None})
    d = _drive(b)
    n, T = _num(b, "speed_rpm"), _num(b, "torque_Nm")
    r = oew_min_current_point(d, OewTopology("common_bus", topo.VA_V, zero_sequence=topo.zero_sequence,
                                             zs_policy=topo.zs_policy), n, T)
    w = r.get("witness")
    if w is None:
        raise InputValidationError("no OEW operating point: " + r.get("reason", ""), field="torque_Nm")
    op = w["operating_point"]
    we = d.motor.pole_pairs * 2 * math.pi * n / 60
    zs = topo.zero_sequence
    u0 = (lambda th: we * zs.dpsi0(th)) if (zs is not None and topo.zs_policy == "regulate_i0") else None
    t_edge = float(b.get("t_edge_ns") or 50.0) * 1e-9
    out = {"operating_point": op, "V_V": topo.VA_V, "cases": {}}
    for shift in (0.0, 0.5):
        cm = oew_common_mode(topo.VA_V, op["U_phase_pk_V"], math.atan2(op["vq_V"], op["vd_V"]), we / (2 * math.pi),
                             float(b.get("fsw_kHz", 10.0)) * 1e3, t_edge, topo.split, shift, u0, 40,
                             op["i_dq_A"], math.atan2(op["iq_A"], op["id_A"]))
        out["cases"][f"{shift:g}"] = cm
    out["zsv_free"] = zsv_free_sequence_example(topo.VA_V)
    return _jsonable(out)


# ------------------------------------------------------------------ efficiency by boundary (module-efficiency addendum)

EXAMPLE_MODULE_SIC = PROJECT.module_spec("sic")

EXAMPLE_REDUCER = PROJECT.reducer()

EXAMPLE_EFFICIENCY = {
    "speed_rpm": 6000.0, "torque_Nm": 150.0, "Vdc_V": 600.0, "loss_model": "module", "module": EXAMPLE_MODULE,
    "reducer": EXAMPLE_REDUCER, "oil_temp_C": 80.0,
    "aux": [{"name": "gate drivers + controller (12 V LV supply)", "P_W": 25.0, "supply": "lv_external",
             "basis": "example value"}],
    "map_speeds_rpm": [500.0, 1500.0, 2500.0, 3500.0, 4500.0, 5500.0, 6500.0, 7500.0, 8500.0, 9500.0, 10500.0,
                       11500.0, 12500.0, 13500.0, 14500.0, 15500.0],
    "map_torques_Nm": [-250.0, -200.0, -150.0, -100.0, -60.0, -30.0, -10.0, 10.0, 30.0, 60.0, 100.0, 150.0, 200.0,
                       250.0, 300.0, 350.0],
    "mission": {"torque_at": "motor_shaft", "distance_km": None,
                "segments": [{"duration_s": 12.0, "speed_rpm": 3000.0, "torque_Nm": 220.0},
                             {"duration_s": 40.0, "speed_rpm": 7000.0, "torque_Nm": 45.0},
                             {"duration_s": 10.0, "speed_rpm": 5000.0, "torque_Nm": -110.0},
                             {"duration_s": 6.0, "speed_rpm": 800.0, "torque_Nm": -40.0},
                             {"duration_s": 15.0, "speed_rpm": 0.0, "torque_Nm": 0.0},
                             {"duration_s": 30.0, "speed_rpm": 11000.0, "torque_Nm": 30.0}]},
    "compare": {"mode": "fixed_policy", "common_fsw_kHz": _CTRL["fsw_kHz"], "coolant_C": 65.0,
                "A": {"label": "IGBT design", "module": EXAMPLE_MODULE, "Rth_K_per_W": EXAMPLE_MODULE["Rth_K_per_W"],
                      "loss_error_rel": 0.10,
                      "error_basis": "example engineering budget - replace with DPT / holdout evidence "
                                     "(not a statistical confidence)"},
                "B": {"label": "SiC design", "module": EXAMPLE_MODULE_SIC, "Rth_K_per_W": EXAMPLE_MODULE_SIC["Rth_K_per_W"],
                      "loss_error_rel": 0.10, "error_basis": "example engineering budget - replace with DPT / holdout "
                                                             "evidence (not a statistical confidence)"},
                "B_fsw_kHz": 20.0,
                "requests": [[2000.0, 250.0, 600.0], [6000.0, 150.0, 600.0], [12000.0, 60.0, 600.0],
                             [4000.0, -120.0, 600.0]]},
    "note": "example reducer, auxiliaries, modules and error budgets are synthetic",
}


def _aux(b):
    from .analysis.efficiency import AuxLoad
    return tuple(AuxLoad(str(a.get("name", "aux")), float(a["P_W"]), str(a["supply"]), str(a.get("basis", "")))
                 for a in (b.get("aux") or []))


def _eff_drive(b):
    from dataclasses import replace as _rep
    d = _drive(b)
    if b.get("loss_model", "module") == "module":
        mspec = b.get("module") or EXAMPLE_MODULE
        d = _rep(d, inverter=_rep(d.inverter, loss=None, module_loss=module_model_from_dict(mspec),
                                  module_Tj_C=float(mspec.get("Tj_eval_C", 150.0))))
    return d


def _pwm_hf(b: dict, drive, sc, pt):
    """Motor PWM harmonic losses at the point from the declared HF data (``pwm_hf``: L_hf_uH, fsw_kHz, modulation,
    harmonic); default = the variable-PWM page's example data (synthetic, labelled).  ``pwm_hf: null`` = not
    evaluated."""
    from .extensions.pwm_policy import point_hf_losses
    spec = b["pwm_hf"] if "pwm_hf" in b else {"L_hf_uH": EXAMPLE_PWM["L_hf_uH"], "fsw_kHz": _CTRL["fsw_kHz"],
                                              "modulation": _CTRL["modulation"], "harmonic": EXAMPLE_PWM["harmonic"]}
    if not spec:
        return None
    L = float(spec["L_hf_uH"]) * 1e-6
    if not (math.isfinite(L) and L > 0):
        raise InputValidationError("L_hf must be > 0", field="pwm_hf.L_hf_uH")
    mod = str(spec.get("modulation", "svpwm"))
    if mod not in MODULATIONS:
        return {"status": "UNKNOWN", "interval_W": [None, None], "copper": None, "magnetic_hf_bound_W": None,
                "reason": f"ripple model supports {', '.join(MODULATIONS)}, not {mod}", "basis": ""}
    return point_hf_losses(drive, sc, pt, float(spec.get("fsw_kHz") or _CTRL["fsw_kHz"]) * 1e3, L, mod,
                           harmonic_data(spec.get("harmonic")))


def efficiency(body):
    """Five boundary efficiencies, loss ledger and flow table at one policy point (module-efficiency addendum)."""
    from .analysis.efficiency import MODEL_EFFICIENCY, point_ledger
    b = {**EXAMPLE_EFFICIENCY, **(body or {})}
    d = _eff_drive(b)
    n, T, vdc = _num(b, "speed_rpm"), _num(b, "torque_Nm"), _num(b, "Vdc_V")
    sol = PolicyEvaluator(d, Scenario("eff", n, vdc, _limits(b))).solve(T)
    out = {"request": {"speed_rpm": n, "torque_Nm": T, "Vdc_V": vdc}, "claims": [c.to_dict() for c in sol.claims],
           "loss_model": b.get("loss_model", "module"), "model_efficiency": MODEL_EFFICIENCY}
    if sol.point is None:
        out["ledger"] = None
        out["reason"] = sol.policy_claim.detail
        return _jsonable(out)
    hf = _pwm_hf(b, d, Scenario("eff", n, vdc, _limits(b)), sol.point)
    out["ledger"] = point_ledger(sol.point, d, _reducer(b.get("reducer")), _opt(b, "oil_temp_C"), _aux(b), pwm_hf=hf)
    out["point"] = {"id_A": sol.point.id_A, "iq_A": sol.point.iq_A, "i_peak_A": sol.point.i_peak_A,
                    "energy_mode": sol.point.energy_mode, "Tshaft_Nm": sol.point.Tshaft_Nm,
                    "Te_Nm": sol.point.Te_Nm, "module_detail": sol.point.inverter_loss_detail}
    return _jsonable(out)


def efficiency_map(body):
    """Boundary model-efficiency maps on a speed x torque grid (policy points; status mask kept, no hole filling)."""
    from .analysis.efficiency import DEFINED, MODEL_EFFICIENCY, point_ledger
    b = {**EXAMPLE_EFFICIENCY, **(body or {})}
    d = _eff_drive(b)
    red = _reducer(b.get("reducer"))
    oil = _opt(b, "oil_temp_C")
    vdc = _num(b, "Vdc_V")
    sp = [float(x) for x in b["map_speeds_rpm"]]
    tq = [float(x) for x in b["map_torques_Nm"]]
    names = ("inverter", "motor", "inverter_motor", "reducer", "edrive")
    grids = {k: np.full((len(tq), len(sp)), np.nan) for k in names}
    status = np.full((len(tq), len(sp)), "", dtype=object)
    loss_known = np.full((len(tq), len(sp)), np.nan)
    for j, n in enumerate(sp):
        ev = PolicyEvaluator(d, Scenario("map", n, vdc, _limits(b)))
        for i, T in enumerate(tq):
            sol = ev.solve(T)
            status[i, j] = sol.policy_claim.status.value
            if sol.point is None or status[i, j] == "INFEASIBLE":      # a violating point is not a map value
                continue
            led = point_ledger(sol.point, d, red, oil)
            loss_known[i, j] = led["loss_known_subtotal_W"]
            for k in names:
                r = led["boundaries"][k]
                if r["status"] == DEFINED:
                    grids[k][i, j] = r["eta"]
    return _jsonable({"speeds_rpm": sp, "torques_Nm": tq, "Vdc_V": vdc, "grids": grids, "status": status,
                      "loss_known_W": loss_known, "reducer": None if red is None else red.describe(),
                      "oil_temp_C": oil, "loss_model": b.get("loss_model", "module"),
                      "model_efficiency": MODEL_EFFICIENCY,
                      "meaning": "model efficiency map: values at minimum-current policy points; only FEASIBLE cells "
                                 "are feasible operation, UNKNOWN cells are shown hatched, INFEASIBLE cells are blank"})


def efficiency_mission(body):
    """Mission energy ledger per direction and boundary (E+ / E- per port; no averaged eta)."""
    from .analysis.efficiency import MODEL_EFFICIENCY, mission_energy, point_ledger
    b = {**EXAMPLE_EFFICIENCY, **(body or {})}
    d = _eff_drive(b)
    red = _reducer(b.get("reducer"))
    oil = _opt(b, "oil_temp_C")
    vdc = _num(b, "Vdc_V")
    m = b["mission"]
    rows, segs, delivered = [], [], True
    for sg in m["segments"]:
        n, T = float(sg["speed_rpm"]), float(sg["torque_Nm"])
        if m.get("torque_at", "motor_shaft") == "output":
            if red is None:
                raise InputValidationError("an output-side mission needs a reducer model", field="reducer")
            inv = red.motor_torque_for_output(n, T, oil)
            if inv["T_m_Nm"] is None:
                raise InputValidationError(f"segment {sg}: {inv['reason']}", field="mission")
            T = inv["T_m_Nm"]
        sol = PolicyEvaluator(d, Scenario("mission", n, vdc, _limits(b))).solve(T)
        st = sol.policy_claim.status.value
        if sol.point is None or st != "FEASIBLE":
            delivered = False
        led = None if sol.point is None else point_ledger(sol.point, d, red, oil)
        p = led["ports_W"] if led else {"P_dc": None, "P_ac": None, "P_m": None, "P_o": None}
        segs.append({"duration_s": float(sg["duration_s"]), **{k: p[k] for k in ("P_dc", "P_ac", "P_m", "P_o")}})
        rows.append({**sg, "T_motor_Nm": T, "status": st, "ports_W": p,
                     "loss_known_W": None if led is None else led["loss_known_subtotal_W"]})
    e = mission_energy(segs, distance_km=_opt(m, "distance_km"))
    return _jsonable({"segments": rows, "energy": e, "delivered": delivered, "Vdc_V": vdc,
                      "loss_model": b.get("loss_model", "module"), "model_efficiency": MODEL_EFFICIENCY})


def _cand(c: dict, fsw_kHz=None):
    from dataclasses import replace as _rep
    from .analysis.efficiency import ModuleCandidate
    model = module_model_from_dict(c["module"])
    if fsw_kHz:
        model = _rep(model, fsw_Hz=float(fsw_kHz) * 1e3)
    return ModuleCandidate(str(c.get("label") or c["module"].get("name", "module")), model, float(c["Rth_K_per_W"]),
                           _opt(c, "loss_error_rel"), str(c.get("error_basis", "")))


def module_compare(body):
    """Module A/B (e.g. IGBT vs SiC design) on the same delivered requirement and mission (addendum section 7)."""
    from .analysis.efficiency import MODEL_EFFICIENCY, compare_modules
    b = {**EXAMPLE_EFFICIENCY, **(body or {})}
    c = b["compare"]
    mode = c.get("mode", "fixed_policy")
    A = _cand(c["A"], None if mode == "fixed_policy" else c.get("A_fsw_kHz"))
    B = _cand(c["B"], None if mode == "fixed_policy" else c.get("B_fsw_kHz"))
    m = b.get("mission") or {}
    mission = None
    if m.get("segments") and c.get("include_mission", True):
        if m.get("torque_at", "motor_shaft") != "motor_shaft":
            raise InputValidationError("the module comparison takes the mission at the motor shaft", field="mission")
        mission = [(float(s["duration_s"]), float(s["speed_rpm"]), float(s["torque_Nm"]), _num(b, "Vdc_V"))
                   for s in m["segments"]]
    r = compare_modules(_drive(b), [A, B], [tuple(float(x) for x in q) for q in c["requests"]], _limits(b),
                        float(c.get("coolant_C", 65.0)), mode,
                        float(c["common_fsw_kHz"]) * 1e3 if mode == "fixed_policy" else None,
                        _reducer(b.get("reducer")), _opt(b, "oil_temp_C"), mission)
    return _jsonable({**r, "model_efficiency": MODEL_EFFICIENCY})


# ------------------------------------------------------------------ variable PWM (variable-PWM / anti-jerk addendum)

EXAMPLE_PWM = {
    "module": EXAMPLE_MODULE, "Rth_K_per_W": EXAMPLE_MODULE["Rth_K_per_W"], "coolant_C": 65.0,
    "modulation": _CTRL["modulation"], "L_hf_uH": 200.0,
    "baseline_fsw_kHz": _CTRL["fsw_kHz"],
    "min_dwell_s": 0.5, "hysteresis": {"speed_rpm": 300.0, "torque_abs_Nm": 10.0, "sensor_temp_C": 3.0},
    "schedules": [
        {"name": "light-load 8 kHz", "revision": "A", "basis": "example schedule (declare the target policy)",
         "rules": [{"name": "hot module (protective)", "fsw_kHz": 8.0, "sensor_temp_C": [90.0, 200.0], "protective": True},
                   {"name": "light load", "fsw_kHz": 8.0, "torque_abs_Nm": [0.0, 60.0]},
                   {"name": "default", "fsw_kHz": 10.0}]},
        {"name": "thermal fallback 6 kHz", "revision": "A", "basis": "example schedule (declare the target policy)",
         "rules": [{"name": "hot module (protective)", "fsw_kHz": 6.0, "sensor_temp_C": [90.0, 200.0], "protective": True},
                   {"name": "high speed: pulse ratio", "fsw_kHz": 16.0, "speed_rpm": [9000.0, 30000.0]},
                   {"name": "default", "fsw_kHz": 10.0}]}],
    "segments": [{"duration_s": 20.0, "speed_rpm": 1500.0, "torque_Nm": 250.0, "Vdc_V": 600.0, "sensor_temp_C": 75.0},
                 {"duration_s": 40.0, "speed_rpm": 6000.0, "torque_Nm": 45.0, "Vdc_V": 600.0, "sensor_temp_C": 72.0},
                 {"duration_s": 30.0, "speed_rpm": 11000.0, "torque_Nm": 40.0, "Vdc_V": 600.0, "sensor_temp_C": 74.0},
                 {"duration_s": 10.0, "speed_rpm": 5000.0, "torque_Nm": -100.0, "Vdc_V": 600.0, "sensor_temp_C": 73.0},
                 {"duration_s": 15.0, "speed_rpm": 3000.0, "torque_Nm": 280.0, "Vdc_V": 600.0, "sensor_temp_C": 93.0}],
    "timing": _CTRL["timing"], "loop": _CTRL["current_loop"], "sensing": _CTRL["sensing"],
    "measurement_noise": {"speed_rpm": 20.0, "torque_abs_Nm": 4.0, "sensor_temp_C": 1.0, "Vdc_V": 10.0,
                          "basis": "example peak-to-peak noise of the schedule inputs (declare the measured values)"},
    "harmonic": {"f_Hz": [0.0, 1e3, 5e3, 10e3, 20e3, 50e3, 1e5, 3e5, 1e6, 3e6],
                 "rac_over_rdc": [1.0, 1.02, 1.3, 1.8, 2.8, 5.0, 8.0, 14.0, 25.0, 45.0],
                 "magnetic_hf_loss_bound_W": [[6e3, 420.0], [8e3, 350.0], [10e3, 300.0], [16e3, 220.0]],
                 "basis": "synthetic example (declare FEA / measured R_ac(f) and the Fe+PM HF magnetic-loss bound)"},
    "pwm_limits": {"Tj_max_C": 150.0, "i_peak_incl_ripple_max_A": 700.0, "cap_rms_max_A": 250.0,
                   "phase_margin_min_deg": 45.0, "pulse_ratio_min": 10.0, "transition_excursion_max_A": 50.0,
                   "not_applicable": []},
    "use_capacitor": True,
    "transition": {"from_kHz": _CTRL["fsw_kHz"], "to_kHz": 20.0, "duty": 0.9, "deadtime_us": _CTRL["deadtime_us"],
                   "write_fraction": 0.3},
    "note": "example schedule, timing, harmonic data and limits are synthetic",
}


def _schedule(sd: dict, common: dict):
    """A schedule; fallback, dwell and hysteresis default to the common settings of the comparison."""
    from .extensions.pwm_policy import FswRule, FswSchedule
    sd = {"fallback_fsw_kHz": common.get("baseline_fsw_kHz"), "min_dwell_s": common.get("min_dwell_s"),
          "hysteresis": common.get("hysteresis"), **sd}
    rules = []
    for r in sd["rules"]:
        rules.append(FswRule(str(r.get("name", "rule")), float(r["fsw_kHz"]) * 1e3,
                             *(tuple(r[k]) if r.get(k) not in (None, "") else None
                               for k in ("speed_rpm", "torque_abs_Nm", "Vdc_V", "sensor_temp_C")),
                             protective=bool(r.get("protective"))))
    return FswSchedule(str(sd.get("name", "schedule")), tuple(rules), float(sd["fallback_fsw_kHz"]) * 1e3,
                       tuple((k, float(v)) for k, v in (sd.get("hysteresis") or {}).items()),
                       float(sd.get("min_dwell_s") or 0.0), str(sd.get("revision", "1")), str(sd.get("basis", "")))


def _plant_point(b: dict, default_speed: float = 6000.0, default_torque: float = 150.0):
    """Policy point at the body's speed / torque and the machine's differential inductances there."""
    from .extensions.pwm_policy import differential_inductances
    d = _drive(b)
    n, T, vdc = _num(b, "speed_rpm", default_speed), _num(b, "torque_Nm", default_torque), _num(b, "Vdc_V", 600.0)
    sc = Scenario("plant", n, vdc, _limits(b))
    sol = PolicyEvaluator(d, sc).solve(T)
    if sol.point is None:
        raise InputValidationError("no operating point: " + sol.policy_claim.detail, field="torque_Nm")
    return sol.point, differential_inductances(d, sc, sol.point.id_A, sol.point.iq_A), (n, T, vdc)


def harmonic_data(hd):
    """Declared motor PWM harmonic data: R_ac/R_dc(f) table and the Fe+PM HF magnetic-loss bound
    (``magnetic_hf_loss_bound_W``; ``iron_bound_W`` is accepted as its alias)."""
    from .extensions.pwm_policy import HarmonicLossData
    if not hd:
        return None
    pairs = lambda key: tuple((float(f), float(w)) for f, w in (hd.get(key) or []))
    return HarmonicLossData(tuple(float(x) for x in hd["f_Hz"]), tuple(float(x) for x in hd["rac_over_rdc"]),
                            pairs("magnetic_hf_loss_bound_W"), str(hd.get("basis", "")), pairs("iron_bound_W"))


def pwm_risk_at(drive, scenario, pt, spec: dict | None = None) -> dict:
    """PWM ripple, lines and DC-link burden at a decision operating point, with the project's controller (fsw,
    modulation), the declared L_hf / harmonic data / peak limit (the variable-PWM page's example unless given) and
    the project's DC-link capacitor."""
    from .extensions.pwm_policy import point_pwm_risk
    sp = {"L_hf_uH": EXAMPLE_PWM["L_hf_uH"], "fsw_kHz": _CTRL["fsw_kHz"], "modulation": _CTRL["modulation"],
          "harmonic": EXAMPLE_PWM["harmonic"],
          "peak_limit_A": (EXAMPLE_PWM.get("pwm_limits") or {}).get("i_peak_incl_ripple_max_A"), **(spec or {})}
    mod = str(sp.get("modulation", "svpwm"))
    if mod not in MODULATIONS:
        return {"status": "UNKNOWN", "reason": f"the ripple model supports {', '.join(MODULATIONS)}, not {mod}"}
    bank, source = _ripple_bank(EXAMPLE_RIPPLE)
    cap = EXAMPLE_RIPPLE["capacitor"]
    return _jsonable(point_pwm_risk(drive, scenario, pt, float(sp["fsw_kHz"]) * 1e3, float(sp["L_hf_uH"]) * 1e-6, mod,
                                    harmonic_data(sp.get("harmonic")), bank, source,
                                    None if cap.get("T_ref_C") in (None, "") else float(cap["T_ref_C"]),
                                    None if sp.get("peak_limit_A") in (None, "") else float(sp["peak_limit_A"])))


def pwm_policies(body):
    """Fixed-frequency baseline vs a declared schedule on the same trajectory (P1-PWM)."""
    from .analysis.efficiency import ModuleCandidate
    from .extensions.pwm_policy import PwmLimits, evaluate_policies, fixed_schedule
    b = {**EXAMPLE_PWM, **(body or {})}
    model = module_model_from_dict(b.get("module") or EXAMPLE_MODULE)
    cand = ModuleCandidate(str((b.get("module") or {}).get("name", "module")), model, float(b["Rth_K_per_W"]))
    harm = harmonic_data(b.get("harmonic"))
    bank, source = _ripple_bank(EXAMPLE_RIPPLE) if b.get("use_capacitor") else (None, None)
    pl = dict(b.get("pwm_limits") or {})
    if "i_peak_bound_max_A" in pl:                   # the canonical name of the limit on the conservative peak bound
        v = pl.pop("i_peak_bound_max_A")
        if pl.get("i_peak_incl_ripple_max_A") not in (None, "", v):
            raise InputValidationError("i_peak_bound_max_A and its alias i_peak_incl_ripple_max_A disagree",
                                       field="pwm_limits")
        pl["i_peak_incl_ripple_max_A"] = v
    na = tuple(pl.pop("not_applicable", None) or ())
    lim = PwmLimits(**{k: (None if v in (None, "") else float(v)) for k, v in pl.items()}, not_applicable=na)
    pols = [fixed_schedule(float(b["baseline_fsw_kHz"]) * 1e3)] + [_schedule(sd, b) for sd in b.get("schedules") or []]
    names = [p.name for p in pols]
    if len(set(names)) != len(names):
        raise InputValidationError("policy names must be unique", field="schedules")
    segs = [{**s, "Vdc_V": float(s.get("Vdc_V") or 600.0)} for s in b["segments"]]
    r = evaluate_policies(_drive(b), cand, segs, pols, float(b["coolant_C"]), _limits(b), _timing(b["timing"]),
                          float(b["L_hf_uH"]) * 1e-6, _loop(b.get("loop")), harm, bank, source,
                          str(b.get("modulation", "svpwm")), lim, _sensing(b.get("sensing")),
                          _noise(b.get("measurement_noise")),
                          capacitor_in_boundary=bool(b.get("capacitor_in_energy_boundary")) and bank is not None)
    return _jsonable(r)


def pwm_transients(body):
    """Current-sample validity vs modulation index for the three acquisition kinds, the declared acquisition at an
    operating point, and one carrier-frequency change replayed with alternative integrator / gain mappings."""
    from dataclasses import replace as _rep
    from .extensions.pwm_policy import sampling_validity, transition_transient
    b = {**EXAMPLE_PWM, **(body or {})}
    d = _drive(b)
    n, T, vdc = _num(b, "speed_rpm", 6000.0), _num(b, "torque_Nm", 150.0), _num(b, "Vdc_V", 600.0)
    sol = PolicyEvaluator(d, Scenario("transients", n, vdc, _limits(b))).solve(T)
    if sol.point is None:
        raise InputValidationError("no operating point: " + sol.policy_claim.detail, field="torque_Nm")
    pt = sol.point
    mod = str(b.get("modulation", "svpwm"))
    m = pt.v_peak_V / (0.5 * vdc)
    fe = abs(pt.f_e_Hz)
    fsw = float(b["baseline_fsw_kHz"]) * 1e3
    L_hf = float(b["L_hf_uH"]) * 1e-6
    sens = _sensing(b.get("sensing") or EXAMPLE_PWM["sensing"])
    dead = float((b.get("module") or EXAMPLE_MODULE).get("deadtime_us") or 0.0) * 1e-6
    here = sampling_validity(m, mod, fsw, fe, sens, dead, pt.i_peak_A, 0.5 * m * vdc, L_hf)
    m_grid = [0.05 * k for k in range(1, 24)]
    curves = {}
    for kind in ("inline_phase", "leg_shunt", "dc_link_shunt"):
        sk = _rep(sens, kind=kind, sample_points="valley")
        rows = [sampling_validity(mm, mod, fsw, max(fe, 1.0), sk, dead, pt.i_peak_A, 0.5 * mm * vdc, L_hf) for mm in m_grid]
        curves[kind] = {"m": m_grid, "valid_fraction": [r["valid_fraction"] for r in rows],
                        "max_age_s": [r["max_age_s"] for r in rows], "error_bound_A": [r["error_bound_A"] for r in rows]}
    tc = _timing(b["timing"])
    loops = _loop(b.get("loop"))
    trn = b["transition"]
    f0, f1 = float(trn["from_kHz"]) * 1e3, float(trn["to_kHz"]) * 1e3
    variants = {}
    from .extensions.pwm_policy import axis_loops, differential_inductances
    loop = axis_loops(loops).get("q")
    if loop is not None:
        pl = differential_inductances(d, Scenario("transients", n, vdc, _limits(b)), pt.id_A, pt.iq_A) or {}
        e = pt.vq_V - loop.R_ohm * pt.iq_A
        for label, lp in (("declared", loop),
                          ("bumpless (volts, Ki*Ts remapped)", _rep(loop, gain_mapping="continuous", integrator_storage="output",
                                                                    on_transition="keep")),
                          ("error-sum integrator, Ki*Ts remapped", _rep(loop, gain_mapping="continuous",
                                                                        integrator_storage="error_sum", on_transition="keep")),
                          ("integrator reset", _rep(loop, on_transition="reset", integrator_storage="output")),
                          ("fixed discrete gains", _rep(loop, gain_mapping="fixed_discrete", integrator_storage="output",
                                                        on_transition="keep",
                                                        reference_fsw_Hz=loop.reference_fsw_Hz or f0))):
            variants[label] = transition_transient(lp, tc, f0, f1, pt.iq_A, e, pt.voltage_budget_V,
                                                   plant_L_H=pl.get("q"))
    lim = (b.get("pwm_limits") or {}).get("transition_excursion_max_A")
    return _jsonable({"point": {"speed_rpm": n, "torque_Nm": T, "Vdc_V": vdc, "m": m, "f_e_Hz": fe, "fsw_Hz": fsw,
                                "iq_A": pt.iq_A, "vq_V": pt.vq_V, "i_peak_A": pt.i_peak_A,
                                "voltage_budget_V": pt.voltage_budget_V},
                      "sensing": sens.__dict__, "sampling_here": here, "sampling_curves": curves,
                      "transition": {"from_Hz": f0, "to_Hz": f1, "excursion_limit_A": lim, "variants": variants}})


def pwm_timing(body):
    """Delay ledger, current-loop margin per axis (machine plant at a reference point) and phase lag vs carrier
    frequency; one clock-level period transition."""
    from .extensions.pwm_policy import axis_loops, axis_margins, delay_ledger, phase_lag_deg, transition_check
    b = {**EXAMPLE_PWM, **(body or {})}
    tc = _timing(b["timing"])
    loop = _loop(b.get("loop"))
    f_mode = float(b.get("mode_frequency_Hz") or 20.0)
    plant, ref = None, None
    if loop is not None:
        _pt, plant, ref = _plant_point(b)
    rows = []
    for fsw in [float(x) * 1e3 for x in (b.get("fsw_sweep_kHz") or [4, 5, 6, 8, 10, 12, 16, 20, 25, 30, 40])]:
        led = delay_ledger(fsw, tc)
        row = {"fsw_Hz": fsw, "deadline_ok": led["deadline_ok"], "deadline_margin_s": led["deadline_margin_s"],
               "total_delay_s": led["total_delay_s"]}
        if led["total_delay_s"] is not None:
            row["phase_at_mode_deg"] = phase_lag_deg(f_mode, led["total_delay_s"])
            if loop is not None:
                am = axis_margins(loop, led["total_delay_s"], fsw, plant, tc.updates_per_period, tc)
                bx = am["axes"][am["binding_axis"]] if am["binding_axis"] else {}
                row.update({"phase_margin_deg": am["phase_margin_deg"], "binding_axis": am["binding_axis"],
                            "sampled_stable": am["sampled_stable"],
                            "continuous_screen_phase_margin_deg": bx.get("continuous_screen_phase_margin_deg"),
                            "crossover_Hz": bx.get("crossover_Hz"),
                            "phase_at_crossover_deg": bx.get("delay_phase_at_crossover_deg"),
                            "phase_margin_by_axis_deg": {a: v.get("phase_margin_deg") for a, v in am["axes"].items()}})
        rows.append(row)
    trn = b["transition"]
    out = {"rows": rows, "mode_frequency_Hz": f_mode, "timing": tc.__dict__,
           "loop": None if loop is None else {a: lp.__dict__ for a, lp in axis_loops(loop).items()},
           "plant_L_H": plant, "plant_point": None if ref is None else
           {"speed_rpm": ref[0], "torque_Nm": ref[1], "Vdc_V": ref[2]}}
    for sh in (True, False):
        r = transition_check(float(trn["from_kHz"]) * 1e3, float(trn["to_kHz"]) * 1e3, float(trn["duty"]),
                             float(trn["deadtime_us"]) * 1e-6, tc.min_pulse_s, write_fraction=float(trn["write_fraction"]),
                             shadow=sh)
        out["transition_shadow" if sh else "transition_immediate"] = r
    return _jsonable(out)


def pwm_ripple(body):
    """Phase-current ripple vs carrier frequency at one operating point (time integration and edge-sum spectrum)."""
    from .extensions.pwm_policy import minimum_pulse, phase_ripple
    b = {**EXAMPLE_PWM, **(body or {})}
    d = _drive(b)
    n, T, vdc = _num(b, "speed_rpm", 6000.0), _num(b, "torque_Nm", 150.0), _num(b, "Vdc_V", 600.0)
    sol = PolicyEvaluator(d, Scenario("ripple", n, vdc, _limits(b))).solve(T)
    if sol.point is None:
        raise InputValidationError("no operating point: " + sol.policy_claim.detail, field="torque_Nm")
    pt = sol.point
    m = pt.v_peak_V / (0.5 * vdc)
    fe = abs(pt.f_e_Hz)
    L = float(b["L_hf_uH"]) * 1e-6
    rows = []
    for fsw in [float(x) * 1e3 for x in (b.get("fsw_list_kHz") or [5.0, 10.0, 20.0])]:
        r = phase_ripple(vdc, m, 0.0, max(fe, fsw / 400.0), fsw, L, str(b.get("modulation", "svpwm")))
        mp = minimum_pulse(m, str(b.get("modulation", "svpwm")), fsw, float(b["timing"].get("min_pulse_us") or 0) * 1e-6)
        rows.append({"fsw_Hz": fsw, **{k: r[k] for k in ("ripple_rms_A", "ripple_rms_spectrum_A", "ripple_pp_A",
                                                          "ripple_peak_A", "carrier_ratio")},
                     "t_s": r["t_s"][:: max(1, r["t_s"].size // 3000)], "di_A": r["di_A"][:: max(1, r["t_s"].size // 3000)],
                     "harmonic_f_Hz": r["harmonic_f_Hz"], "harmonic_I_pk_A": r["harmonic_I_pk_A"],
                     "narrowest_pulse_s": mp["narrowest_pulse_s"], "min_pulse_ok": mp["ok"],
                     "pulse_ratio": fsw / fe if fe > 0 else None})
    return _jsonable({"point": {"speed_rpm": n, "torque_Nm": T, "Vdc_V": vdc, "i_peak_A": pt.i_peak_A, "m": m,
                                "f_e_Hz": fe}, "L_hf_H": L, "rows": rows})


# ------------------------------------------------------------------ anti-jerk / active damping (P1-DAMP)

EXAMPLE_DRIVELINE = {
    "driveline": PROJECT.driveline_rom(),
    "maneuver": {"T0_Nm": 20.0, "T1_Nm": 150.0, "t_step_s": 0.05, "t_end_s": 1.2, "speed_rpm": 2000.0,
                 "Vdc_V": 600.0, "TL_out_Nm": 0.0, "window": "capability", "emergency_t_s": None,
                 "emergency_T_Nm": None},
    "controller": PROJECT.torque_path(),
    "variants": {"off": {},
                 "shaping": {"shaper": {"kind": "rate", "rate_Nm_per_s": 1500.0}},
                 # motor-speed feedback through a second-order washout: no steady correction while the vehicle
                 # accelerates (a first-order high-pass keeps -Kd a / omega_c: hpf_order 1 shows that deficit)
                 "feedback": {"damping": {"kind": "motor_speed_hpf", "Kd_Nms_per_rad": 1.5, "hpf_Hz": 2.0,
                                          "hpf_order": 2}},
                 "combined": {"shaper": {"kind": "rate", "rate_Nm_per_s": 1500.0},
                              "damping": {"kind": "motor_speed_hpf", "Kd_Nms_per_rad": 1.5, "hpf_Hz": 2.0,
                                          "hpf_order": 2}}},
    "requirement": {"t_to_90_max_s": 0.25, "peak_vehicle_jerk_max_m_s3": 35.0, "settle_max_s": 0.6,
                    "safety_reaction_max_s": 0.02, "safety_band_Nm": 2.0,
                    "basis": "example comfort / response targets (declare the program's definitions)"},
    "sensing": {"load_speed_skew_ms": 0.0, "dropouts_ms": [], "dropout_signal": "load", "stale_limit_ms": 20.0,
                "fade_ms": 10.0, "basis": "example speed-signal timing and fallback (declare the target's message "
                                          "timing, timestamps and the degraded-mode strategy)"},
    "check_slew": True,
    "stability": {"Kd_list": [0.5, 1.0, 1.5, 2.5, 4.0], "delay_ms_list": [0.0, 1.0, 2.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0]},
    "note": "example driveline, controller and targets are synthetic",
}


def _controller(c: dict, v: dict, sensing: dict | None = None):
    from .extensions.driveline import Controller, Damping, Shaper
    sh = v.get("shaper") or {}
    dp = v.get("damping") or {}
    sn = {**(sensing or {}), **(dp.get("sensing") or {})}
    return Controller(float(c["sample_ms"]) * 1e-3, float(c["delay_ms"]) * 1e-3,
                      float(c.get("actuator_tau_ms") or 0.0) * 1e-3,
                      Shaper(sh.get("kind", "none"), _opt(sh, "rate_Nm_per_s"), _opt(sh, "tau_s"), _opt(sh, "zv_f_Hz"),
                             _opt(sh, "zv_zeta")),
                      Damping(dp.get("kind", "none"), float(dp.get("Kd_Nms_per_rad") or 0.0), _opt(dp, "hpf_Hz"),
                              float(dp.get("quantization_rad_s") or 0.0),
                              float(sn.get("load_speed_skew_ms") or 0.0) * 1e-3,
                              tuple((float(a) * 1e-3, float(b) * 1e-3) for a, b in (sn.get("dropouts_ms") or [])),
                              str(sn.get("dropout_signal") or "load"), _opt(sn, "stale_limit_ms", 1e-3),
                              float(sn.get("fade_ms") or 0.0) * 1e-3, _hpf_order(dp)))


def _hpf_order(dp: dict) -> int:
    v = dp.get("hpf_order", 1)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or float(v) not in (1.0, 2.0):
        raise InputValidationError("damping.hpf_order must be 1 or 2", field="damping.hpf_order")
    return int(v)


def _torque_window(d, n, vdc, lim):
    """Allowed actual torque window at the operating speed: the policy capability in both directions (incl. DC)."""
    from .solvers.capability import policy_capability
    ev = PolicyEvaluator(d, Scenario("window", n, vdc, lim))
    hi = policy_capability(ev, +1)
    lo = policy_capability(ev, -1)
    return (lo.value_Nm if lo.accepted else None, hi.value_Nm if hi.accepted else None)


def _loss_lookup(d, n, vdc, lim, T_lo, T_hi, npts=41):
    """Electrical loss vs torque at the operating speed from the policy points (for the damping energy cost)."""
    grid = np.linspace(T_lo, T_hi, npts)
    ev = PolicyEvaluator(d, Scenario("loss", n, vdc, lim))
    w = []
    for T in grid:
        sol = ev.solve(float(T))
        p = sol.point
        w.append(np.nan if (p is None or p.Pdc_W is None or p.Pshaft_W is None) else p.Pdc_W - p.Pshaft_W)
    w = np.array(w)

    def fn(T, _n):
        T = np.asarray(T, float)
        if np.any(T < grid[0] - 1e-9) or np.any(T > grid[-1] + 1e-9) or np.any(np.isnan(w)):
            return None
        return np.interp(T, grid, w)
    return fn


def driveline(body):
    """Off / shaping / feedback / combined on one maneuver: response, jerk, correction, clipping, loss, stability."""
    from .extensions.driveline import Maneuver, evaluate_variants
    b = {**EXAMPLE_DRIVELINE, **(body or {})}
    dl = driveline_from_dict(b["driveline"])
    m = b["maneuver"]
    d = _drive(b)
    lim = _limits(b)
    n, vdc = float(m["speed_rpm"]), float(m.get("Vdc_V") or 600.0)
    win = m.get("window")
    window_info = None
    if win == "capability":
        lo, hi = _torque_window(d, n, vdc, lim)
        if lo is None or hi is None:
            raise InputValidationError("the torque window (capability) is not established at this operating point",
                                       field="window")
        window = (lo, hi)
        window_info = {"source": "policy capability at the operating speed and Vdc (incl. DC limits)",
                       "T_min_Nm": lo, "T_max_Nm": hi}
    elif win:
        window = (float(win[0]), float(win[1]))
        window_info = {"source": "declared", "T_min_Nm": window[0], "T_max_Nm": window[1]}
    else:
        window = None
    man = Maneuver(float(m["T0_Nm"]), float(m["T1_Nm"]), float(m["t_step_s"]), float(m["t_end_s"]), n,
                   float(m.get("TL_out_Nm") or 0.0), window, _opt(m, "emergency_t_s"), _opt(m, "emergency_T_Nm"))
    variants = {name: _controller(b["controller"], v or {}, b.get("sensing")) for name, v in b["variants"].items()}
    loss_fn = None
    if window is not None:
        loss_fn = _loss_lookup(d, n, vdc, lim, window[0], window[1])
    req = {k: v for k, v in (b.get("requirement") or {}).items() if k != "basis" and v not in (None, "")}
    slew = None
    if b.get("check_slew"):
        from .extensions.driveline import electrical_slew_limits
        slew = electrical_slew_limits(d, n, vdc, lim, man.T1_Nm)
    r = evaluate_variants(dl, variants, man, req, loss_fn, slew)
    r["window"] = window_info
    r["wheel_radius_m"] = dl.wheel_radius_m
    if window is not None:
        r["dynamic_reserve"] = {"positive_Nm": window[1] - man.T1_Nm, "negative_Nm": man.T1_Nm - window[0],
                                "note": "quasi-static capability window at the operating speed; the electrical "
                                        "di/dt at the torsional mode frequency is not the binding term here"}
    r["requirement"] = b.get("requirement")
    r["maneuver"] = m
    r["controller"] = b["controller"]
    return _jsonable(r)


def driveline_stability(body):
    """Sampled-loop stability and damping over (Kd, delay); continuous delay crossings for relative-speed feedback."""
    from dataclasses import replace as _rep
    from .extensions.driveline import (Controller, Damping, delay_crossings, relative_mode_coefficients,
                                       sampled_eigenvalues, undelayed_damping)
    b = {**EXAMPLE_DRIVELINE, **(body or {})}
    dl = driveline_from_dict(b["driveline"])
    c = b["controller"]
    st = b["stability"]
    fbd = (b["variants"].get("feedback") or {}).get("damping", {})
    kind = fbd.get("kind", "relative_speed")
    hpf = fbd.get("hpf_Hz")
    order = _hpf_order(fbd)
    base = Controller(float(c["sample_ms"]) * 1e-3, 0.0, float(c.get("actuator_tau_ms") or 0.0) * 1e-3)
    grid = []
    for Kd in st["Kd_list"]:
        row = []
        for dms in st["delay_ms_list"]:
            ctl = _rep(base, delay_s=float(dms) * 1e-3, damping=Damping(kind, float(Kd), hpf, hpf_order=order))
            e = sampled_eigenvalues(dl, ctl)
            row.append({"Kd": Kd, "delay_ms": dms, "stable": e["stable"], "rho": e["spectral_radius_excl_rigid"],
                        "zeta": e["dominant_zeta"]})
        grid.append(row)
    cont = []
    for Kd in st["Kd_list"]:
        co = relative_mode_coefficients(dl, float(Kd))
        cr = delay_crossings(co["a1"], co["a0"], co["b1"], 0.2)
        first = next((x for x in cr if x["direction"] == "into RHP"), None)
        cont.append({"Kd": Kd, "zeta_undelayed": undelayed_damping(dl, float(Kd))["zeta"],
                     "first_destabilising_delay_ms": None if first is None else first["tau_s"] * 1e3,
                     "crossing_omega_rad_s": None if first is None else first["omega_rad_s"]})
    return _jsonable({"grid": grid, "continuous_relative_speed": cont, "modal": dl.modal(), "feedback_kind": kind,
                      "sample_ms": float(c["sample_ms"]), "actuator_tau_ms": float(c.get("actuator_tau_ms") or 0.0),
                      "meaning": "sampled loop: exact ZOH + fractional delay; continuous crossings are for ideal "
                                 "relative-speed feedback (an analysis reference, not the target controller)"})


# ------------------------------------------------------------------ machine design (handoff section 10)

EXAMPLE_MACHINE = {
    "candidates": [
        {"name": "ref", "basis": "validated reference (the active drive model)"},
        {"name": "N-10%", "k_turns": 0.9, "basis": "example: fewer turns per coil / more parallel paths"},
        {"name": "N+10%", "k_turns": 1.1, "basis": "example: more turns per coil"},
        {"name": "L+20%", "k_stack": 1.2, "end_R_share": 0.3, "end_L_share": 0.1,
         "basis": "example end-winding shares (declare them from the machine design)"},
        {"name": "PM-10%", "k_pm": 0.9, "basis": "example: lower-remanence grade at the same geometry"},
    ],
    "checks": [
        {"name": "low-speed torque", "kind": "capability", "speed_rpm": 2000.0, "Vdc_V": 450.0, "torque_Nm": 400.0},
        {"name": "high-speed torque @ min Vdc", "kind": "capability", "speed_rpm": 12000.0, "Vdc_V": 450.0,
         "torque_Nm": 130.0},
        {"name": "requirement point", "kind": "point", "speed_rpm": 6000.0, "Vdc_V": 450.0, "torque_Nm": 200.0},
        {"name": "UGO back-EMF", "kind": "ugo", "speed_rpm": 12000.0, "Vdc_V": 450.0, "limit": 900.0},
        {"name": "steady ASC current", "kind": "asc", "speed_rpm": 12000.0, "Vdc_V": 450.0, "limit": 600.0},
        {"name": "copper loss @ 300 N·m", "kind": "copper", "speed_rpm": 2000.0, "Vdc_V": 450.0, "torque_Nm": 300.0,
         "limit": 6000.0},
    ],
    "envelope_speeds_rpm": [0.0, 2000.0, 4000.0, 6000.0, 8000.0, 10000.0, 12000.0, 14000.0, 16000.0],
    "envelope_Vdc_V": 450.0,
    "magnet_temp_C": None,
    "note": "example requirements and limits (UGO withstand 900 V, ASC 600 A, copper 6 kW) are synthetic",
}

EXAMPLE_WINDING = {"Q": 48, "p": 4, "y": 5, "parallel_paths": 2, "turns_per_coil": 4, "harmonics": 25,
                   "compare": {"turns_per_coil": 5, "parallel_paths": 2}}

EXAMPLE_SIZING = {"T_Nm": 350.0, "sigma_kPa": [25.0, 35.0, 45.0], "aspect_L_over_D": [0.6, 0.9, 1.2],
                  "n_max_rpm": 16000.0, "tip_speed_limit_m_s": 150.0,
                  "basis": "example shear-stress band for a liquid-cooled traction IPM (declare the cooling class)"}


def machine_trade(body):
    """Candidates derived from the validated reference, judged on the same coupled requirement margins."""
    from .analysis.machine_design import DesignCheck, trade_study
    b = {**EXAMPLE_MACHINE, **(body or {})}
    d = _drive(b)
    mt = _opt(b, "magnet_temp_C")
    checks = [DesignCheck(str(c.get("name") or c["kind"]), str(c["kind"]), _num(c, "speed_rpm"), _num(c, "Vdc_V"),
                          _opt(c, "torque_Nm"), _opt(c, "limit"), mt) for c in b["checks"]]
    if not checks:
        raise InputValidationError("at least one requirement check is needed", field="checks")
    names = [c.name for c in checks]
    if len(set(names)) != len(names):
        raise InputValidationError("check names must be unique", field="checks")
    specs = [dict(c) for c in b["candidates"]]
    if not specs:
        raise InputValidationError("at least one candidate is needed", field="candidates")
    r = trade_study(d, specs, checks, _limits(b), [float(x) for x in b.get("envelope_speeds_rpm") or []] or None,
                    _opt(b, "envelope_Vdc_V"))
    r["reference"] = {"drive_id": d.drive_id, "revision": d.revision, "validation_status": d.provenance.validation_status,
                      "flux_model": d.motor.flux.kind, "pole_pairs": d.motor.pole_pairs,
                      "inverter_limit_A": d.inverter.current_limit_A_peak}
    r["source_limits"] = _limits(b).describe()
    return _jsonable(r)


def winding(body):
    """Star-of-slots layout, winding factors, three-phase MMF spectrum and consistency with the drive model; the k_N
    hand-over to the trade study passes the core gate (valid layouts, this machine's pole pairs and declared winding)."""
    from .analysis.machine_design import winding_change, winding_layout
    from .validation import integer
    b = {**EXAMPLE_WINDING, **(body or {})}

    def whole(d, key, default=None, lo=1):
        v = d.get(key, default)
        return None if v is None or v == "" else integer(key, v, lo)
    Q, p = whole(b, "Q"), whole(b, "p")
    if Q is None or p is None:
        raise InputValidationError("slots Q and pole pairs p are required", field="Q")
    y = whole(b, "y")
    a = whole(b, "parallel_paths", 1)
    nc = whole(b, "turns_per_coil")
    w = winding_layout(Q, p, y, 3, whole(b, "harmonics", 25), a, nc)
    d = _drive(b)
    dw = d.motor.winding
    cons = [{"item": "pole pairs", "ok": d.motor.pole_pairs == p,
             "detail": f"winding p = {p}, drive model p = {d.motor.pole_pairs}"},
            {"item": "feasible slot / pole combination", "ok": w["feasible"], "detail": f"Q/(3 t) = {Q / (3 * w['t_periodicity']):g}"},
            {"item": "balanced three-phase", "ok": w["balanced"], "detail": w["phase_sequence"]},
            {"item": "parallel paths symmetric", "ok": w["parallel_paths_ok"],
             "detail": f"a = {a}, divisors of {w['max_parallel_paths']} allowed"},
            {"item": "the machine's declared winding", "ok": dw is not None and w["identity"] == _winding_id(dw),
             "detail": "not declared by the active model (a k_N is a generic thought experiment)" if dw is None else
             f"declared Q {dw.Q}, p {dw.p}, y {dw.y}, {dw.parallel_paths} paths, {dw.turns_per_coil} turns / coil"}]
    w["consistency"] = cons
    cmp = b.get("compare")
    if cmp and nc is not None:
        w2 = winding_layout(Q, p, y, 3, 1, whole(cmp, "parallel_paths", a), whole(cmp, "turns_per_coil", nc))
        gate = winding_change(d, w, w2)
        w["compare"] = {"turns_per_coil": w2["turns_per_coil"], "parallel_paths": w2["parallel_paths"],
                        "N_series": w2["N_series"], "N_eff": w2["N_eff"], "k_turns": gate["k_turns"],
                        "parallel_paths_ok": w2["parallel_paths_ok"], "valid": w2["valid"],
                        "sendable": gate["sendable"], "refusals": gate["refusals"], "binding": gate["binding"],
                        "kind": gate["kind"], "candidate": gate["candidate"], "assumptions": gate["assumptions"],
                        "meaning": "k_N for the scaling trade study (same slots, poles and pitch only)"}
    return _jsonable(w)


def _winding_id(wd):
    from .analysis.machine_design import winding_identity
    return winding_identity(wd)


def concept_sizing(body):
    """Rotor volume from a declared air-gap shear stress: a concept envelope, not a rating."""
    from .analysis.machine_design import concept_sizing as cs
    b = {**EXAMPLE_SIZING, **(body or {})}
    r = cs(_num(b, "T_Nm"), tuple(float(x) for x in b["sigma_kPa"]), tuple(float(x) for x in b["aspect_L_over_D"]),
           _opt(b, "n_max_rpm"), _opt(b, "tip_speed_limit_m_s"))
    r["basis"] = b.get("basis", "")
    return _jsonable(r)


EXAMPLE_NAMES = ("TIMING", "THERMAL", "PROTECTION", "PROTECTION_OT", "MODULE", "MODULE_SIC", "RIPPLE", "ASC",
                 "MISSION", "OEW", "HEV", "EMI", "REDUCER", "EFFICIENCY", "PWM", "DRIVELINE", "MACHINE", "WINDING",
                 "SIZING")


def _product(name: str, prj, ex: dict) -> dict:
    """Example ``name`` with the product data of project ``prj`` (scenario inputs stay).  Raises
    InputValidationError when the project lacks a section the example needs - the caller decides, never a silent mix
    of two products."""
    if name == "TIMING":
        return prj.ftti_chain(0)
    if name == "THERMAL":
        return prj.thermal_spec()
    if name == "MODULE":
        return prj.module_spec()
    if name == "MODULE_SIC":
        return prj.module_spec(prj.first_alternative())
    if name == "REDUCER":
        return prj.reducer()
    if name == "PROTECTION":
        ex["plant"]["C_uF"] = prj.data("dc_link")["C_uF"]
    elif name == "RIPPLE":
        c = prj.data("controller")
        ex.update(capacitor=prj.capacitor(), source=prj.source_impedance(), fsw_kHz=c["fsw_kHz"],
                  modulation=c["modulation"])
    elif name == "MISSION":
        jn = prj.junction_network()
        ex["junction_network"] = None if jn is None else {
            **jn, "note": jn["note"] + "; used for every die unless junction_networks names the die's role"}
    elif name == "OEW":
        ex["fsw_kHz"] = prj.data("controller")["fsw_kHz"]
        ex["module"] = {**prj.module_spec(), "vdc_scaling": ex["module"]["vdc_scaling"]}
    elif name == "EMI":
        ex["source"], ex["network"] = prj.emi_source(), prj.emi_network()
    elif name == "EFFICIENCY":
        c = prj.data("controller")
        ex["module"], ex["reducer"] = prj.module_spec(), prj.reducer()
        cmp = ex["compare"]
        cmp["common_fsw_kHz"] = c["fsw_kHz"]
        cmp["A"].update(module=ex["module"], Rth_K_per_W=ex["module"]["Rth_K_per_W"])
        alt = prj.first_alternative()
        b = prj.module_spec(alt)
        cmp["B"].update(module=b, Rth_K_per_W=b["Rth_K_per_W"], label=dict(prj.alternative_keys())[alt])
    elif name == "PWM":
        c = prj.data("controller")
        m = prj.module_spec()
        ex.update(module=m, Rth_K_per_W=m["Rth_K_per_W"], modulation=c["modulation"], baseline_fsw_kHz=c["fsw_kHz"],
                  timing=c["timing"], loop=c["current_loop"], sensing=c["sensing"])
        ex["transition"].update(from_kHz=c["fsw_kHz"], deadtime_us=c["deadtime_us"])
    elif name == "DRIVELINE":
        ex["driveline"], ex["controller"] = prj.driveline_rom(), prj.torque_path()
    return ex


def example(name: str, project=None) -> dict:
    """A copy of page example ``name`` (see EXAMPLE_NAMES), with the product data of ``project`` when given."""
    import copy as _copy
    if name not in EXAMPLE_NAMES:
        raise InputValidationError(f"unknown example {name!r}", field="example")
    ex = _copy.deepcopy(globals()[f"EXAMPLE_{name}"])
    return ex if project is None else _product(name, project, ex)


def acceptance(body):
    return S.acceptance_summary()


# ------------------------------------------------------------------------------------ causal fault simulation

_FAULT_PRODUCTS: dict = {}


def fault_product(project=None):
    """The product a fault simulation runs on: the project and its resolved drive model (cached per project)."""
    from .extensions.faultsim.configure import ProductData
    pr = project or PROJECT
    key = pr.digest()
    if key not in _FAULT_PRODUCTS:
        if len(_FAULT_PRODUCTS) > 8:
            _FAULT_PRODUCTS.clear()
        _FAULT_PRODUCTS[key] = ProductData(pr, S.resolve_drive(pr.data("drive")))
    return _FAULT_PRODUCTS[key]


def fault_scenarios() -> list:
    """The representative fault scenarios of the synthetic example (key, category, title, hint, scenario)."""
    import copy as _copy
    from .extensions.faultsim.study import SCENARIOS
    return _copy.deepcopy(SCENARIOS)


def fault_sim(body, project=None):
    """One causal fault simulation judged against the project's SG / FSR / TSR (``body`` = the scenario, or
    ``{"example": key}``)."""
    from .extensions.faultsim.study import explain, scenario
    b = dict(body or {})
    sc = scenario(b.pop("example")) if b.get("example") else b
    return explain(fault_product(project), sc)


def fault_compare(body, project=None):
    """Protection on / off and the reaction candidates from the same initial condition and fault."""
    from .extensions.faultsim.study import CANDIDATES, compare, scenario
    b = dict(body or {})
    cands = tuple(b.pop("candidates", None) or CANDIDATES)
    sc = scenario(b.pop("example")) if b.get("example") else b
    return compare(fault_product(project), sc, cands)


def fault_campaign(body, project=None):
    """A campaign over declared axes of a base scenario (see ``faultsim.campaign.run_campaign``)."""
    from .extensions.faultsim.campaign import run_campaign
    from .extensions.faultsim.study import scenario
    b = dict(body or {})
    if b.get("example"):
        b["base"] = scenario(b.pop("example"))
    return run_campaign(fault_product(project), b)


def fault_rerun(body, project=None):
    """Re-run a saved counterexample record on the current project and code (stale inputs reported first)."""
    from .extensions.faultsim.campaign import rerun_record
    return rerun_record(fault_product(project), dict(body["record"]))


def fault_validation(body=None, project=None):
    """The plant's validation against closed forms, an independent abc formulation and numerical convergence."""
    from .extensions.faultsim.configure import build_setup
    from .extensions.faultsim.reference import validation_suite
    setup, _reqs, _info = build_setup(fault_product(project), {"speed_rpm": 6000.0, "torque_Nm": 100.0})
    rows = validation_suite(setup.machine, setup.dc, quick=bool((body or {}).get("quick", True)))
    return {"rows": rows, "passed": sum(r["pass"] for r in rows), "total": len(rows),
            "machine": setup.machine.basis}


def fault_independence(body=None, project=None):
    """Declared dependencies of the protection architecture: shared resources and sensors per mechanism / FSR."""
    from .extensions.faultsim.study import independence
    return independence(project or PROJECT)


ROUTES = {
    "info": info, "evaluate": evaluate, "curve": curve, "map": idiq, "sizing": sizing, "dominance": dominance,
    "relaxation": relaxation, "timing": timing, "discharge": discharge, "passive": passive, "overvoltage": overvoltage,
    "safe_state": safe_state, "thermal": thermal, "acceptance": acceptance, "protection": protection,
    "module_losses": module_losses, "dclink_ripple": dclink_ripple, "asc": asc,
    "lifetime": lifetime, "oew": oew, "oew_compare": oew_compare, "hev_joint": hev_joint, "hev_crank": hev_crank,
    "hev_rejection": hev_rejection, "hev_planetary": hev_planetary, "emi": emi, "emi_oew": emi_oew,
    "machine_trade": machine_trade, "winding": winding, "concept_sizing": concept_sizing,
    "efficiency": efficiency, "efficiency_map": efficiency_map, "efficiency_mission": efficiency_mission,
    "module_compare": module_compare, "pwm_policies": pwm_policies, "pwm_timing": pwm_timing, "pwm_ripple": pwm_ripple,
    "pwm_transients": pwm_transients,
    "driveline": driveline, "driveline_stability": driveline_stability,
    "fault_sim": fault_sim, "fault_compare": fault_compare, "fault_campaign": fault_campaign,
    "fault_rerun": fault_rerun, "fault_validation": fault_validation, "fault_independence": fault_independence,
}


# former private names (compatibility; the desktop uses the public names)
_case = case_from_body
_network = thermal_network_from_dict
_thermal_model = thermal_model_from_dict
_driveline = driveline_from_dict
