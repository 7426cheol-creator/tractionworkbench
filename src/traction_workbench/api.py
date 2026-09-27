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
from .decision import _jsonable
from .errors import InputValidationError
from .extensions.coolant import PROPERTY_SOURCE, CoolantLoop, CoolantStation, eg_water_properties
from .extensions.dclink import active_discharge, passive_discharge, regen_disconnect_overvoltage
from .extensions.safe_state import safe_state_screening
from .extensions.thermal import (CauerNetwork, FosterNetwork, ThermalModel, ThermalNode, flow_scaled, thermal_duration,
                                 torque_availability)
from .extensions.timing import TimingChain, TimingItem, analyze_timing
from .models import DataOrigin, Provenance
from .scenario import DcSourceLimits, Scenario
from .solvers.policy import PolicyEvaluator
from .analysis.dominance import capability_dominance, requirement_relaxation
from .analysis.sizing import size_parameter

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
     "hint": {"ko": "표본점 통과만으로 전 구간 PASS 아님", "en": "Sampled points do not prove the whole range"},
     "req": {"id": "REQ-RANGE", "text": "550~650 V 전 구간에서 12,000 rpm 100 N·m", "torque_Nm": 100, "speed_rpm": 12000,
             "Vdc_V": [550, 650]}},
    {"key": "stall", "title": {"ko": "정지 300 N·m", "en": "Standstill 300 N·m"},
     "hint": {"ko": "등가 RMS, 효율 N/A, 지속시간 미확인", "en": "Equivalent RMS, efficiency N/A, duration unknown"},
     "req": {"id": "REQ-ST-300", "text": "정지 상태 300 N·m", "torque_Nm": 300, "speed_rpm": 0, "Vdc_V": 600}},
]

EXAMPLE_TIMING = {
    "chain_id": "FC-OC-01", "fault": "phase overcurrent", "ftti_ms": 30, "detection_event": "confirmed",
    "fdti_budget_ms": 10, "frti_budget_ms": 15, "safe_event": "safe_state", "endpoint_kind": "physical_safe_state",
    "worst_case_attainable": False,
    "events": ["fault", "sensed", "filtered", "detected", "confirmed", "reaction_request", "gate_off", "safe_state"],
    "items": [
        {"id": "HW_SENSE", "from": "fault", "to": "sensed", "owner": "HW", "min_ms": 0.2, "max_ms": 0.5},
        {"id": "ADC_FILTER", "from": "sensed", "to": "filtered", "owner": "SW", "min_ms": 0.5, "max_ms": 1.0, "period_ms": 0.1},
        {"id": "DETECT", "from": "filtered", "to": "detected", "owner": "SW", "min_ms": 1.0, "max_ms": 2.0, "period_ms": 1.0},
        {"id": "DEBOUNCE", "from": "detected", "to": "confirmed", "owner": "SW", "min_ms": 5.0, "max_ms": 5.0},
        {"id": "SW_REACT", "from": "confirmed", "to": "reaction_request", "owner": "SW", "min_ms": 2.0, "max_ms": 10.0},
        {"id": "SYS_FRTI", "from": "confirmed", "to": "safe_state", "owner": "System", "max_ms": 20.0},
        {"id": "GATE", "from": "reaction_request", "to": "gate_off", "owner": "HW", "min_ms": 0.1, "max_ms": 0.2},
        {"id": "DECAY", "from": "gate_off", "to": "safe_state", "owner": "HW", "min_ms": 1.0, "max_ms": 3.0},
    ],
}

DEFAULT_LOOP = [{"station": "inverter", "losses": {"inverter": 1.0}},
                {"station": "motor", "losses": {"copper": 1.0, "rotational": 1.0}}]

EXAMPLE_THERMAL = {
    "model_id": "EXAMPLE_THERMAL_UNVALIDATED", "revision": "2", "validated": False, "origin": "synthetic",
    "validation_evidence": "",
    "source": "synthetic example networks for demonstration (not a product model)",
    "coolant": {"glycol_vol_pct": 50.0, "flow_L_per_min": 10.0, "cp_J_per_kgK": None, "rho_kg_per_m3": None,
                "reference": "mean", "loop": DEFAULT_LOOP},
    "nodes": [
        {"id": "inverter junction (1 of 6 switches)", "network": "foster",
         "R_K_per_W": [0.010, 0.030, 0.080, 0.080], "tau_s": [0.002, 0.03, 0.4, 2.5],
         "flow_dependent": [False, False, False, True], "flow_ref_L_per_min": 10.0, "flow_exponent": 0.8,
         "limit_C": 150, "loss_share": {"inverter": 1 / 6}, "station": "inverter"},
        {"id": "stator winding (hot spot)", "network": "cauer",
         "R_K_per_W": [0.003, 0.005, 0.006], "C_J_per_K": [1500.0, 6000.0, 30000.0],
         "limit_C": 180, "loss_share": {"copper": 1.0}, "station": "motor"},
    ],
}


def _num(body, key, default=None):
    v = body.get(key, default)
    if v is None:
        raise InputValidationError(f"{key} is required", field=key)
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise InputValidationError(f"{key} must be a number", field=key) from None
    if not math.isfinite(x):
        raise InputValidationError(f"{key} must be finite", field=key)
    return x


def _drive(body):
    return S.resolve_drive(body.get("drive"))


def _limits(body) -> DcSourceLimits:
    lim = body.get("limits")
    if not lim:
        return sf.synthetic_limits()
    return DcSourceLimits(lim.get("discharge_power_max_W"), lim.get("charge_power_max_W"),
                          lim.get("discharge_current_max_A"), lim.get("charge_current_max_A"), source="UI input")


def _case(body) -> dict:
    r = body.get("requirement") or {}
    v = r.get("Vdc_V")
    vq = {"value": v, "unit": "V", "port": "inverter_dc_terminal"}
    req = {"id": r.get("id") or "REQ-UI", "text": r.get("text") or "(entered in the UI)",
           "target": {"value": r.get("torque_Nm"), "unit": "N*m", "torque": "shaft"},
           "conditions": {"speed": {"value": r.get("speed_rpm"), "unit": "rpm", "kind": "mechanical"}, "Vdc": vq}}
    if r.get("duration_s") not in (None, ""):
        req["duration"] = "continuous" if r["duration_s"] == "continuous" else {"value": r["duration_s"], "unit": "s"}
    if r.get("coolant_temp_C") not in (None, ""):
        req["conditions"]["coolant_temp"] = {"value": r["coolant_temp_C"], "unit": "degC"}
    if r.get("operator") == "band":
        req["operator"] = "band"
        req["band"] = {"value": r.get("band_Nm"), "unit": "N*m"}
    case = {"drive": body.get("drive") or {"builtin": "SYNTH_IPMSM_200KW_REF_V1"}, "requirement": req,
            "analyses": body.get("analyses") or {}}
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
    return S.evaluate_case(_case(body))


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
    b = body or EXAMPLE_TIMING
    ms = 1e-3
    items = tuple(TimingItem(i["id"], i["from"], i["to"], i.get("owner", ""),
                             None if i.get("max_ms") in (None, "") else float(i["max_ms"]) * ms,
                             None if i.get("min_ms") in (None, "") else float(i["min_ms"]) * ms,
                             None if i.get("nom_ms") in (None, "") else float(i["nom_ms"]) * ms,
                             None if i.get("period_ms") in (None, "") else float(i["period_ms"]) * ms)
                  for i in b["items"])
    ch = TimingChain(b.get("chain_id", "chain"), b.get("fault", ""), float(b["ftti_ms"]) * ms, tuple(b["events"]), items,
                     b.get("detection_event"),
                     None if b.get("fdti_budget_ms") in (None, "") else float(b["fdti_budget_ms"]) * ms,
                     None if b.get("frti_budget_ms") in (None, "") else float(b["frti_budget_ms"]) * ms,
                     safe_event=b.get("safe_event") or None,
                     endpoint_kind=b.get("endpoint_kind") or "physical_safe_state",
                     worst_case_attainable=bool(b.get("worst_case_attainable", False)))
    return _jsonable(analyze_timing(ch))


def discharge(body):
    d = _drive(body)
    return _jsonable(active_discharge(_num(body, "C_uF") * 1e-6, _num(body, "V0_V"), _num(body, "Vf_V"),
                                      _num(body, "t_target_s"),
                                      None if body.get("R_ohm") in (None, "") else float(body["R_ohm"]),
                                      drive=d if body.get("speed_rpm") not in (None, "") else None,
                                      speed_rpm=None if body.get("speed_rpm") in (None, "") else float(body["speed_rpm"]),
                                      magnet_temp_C=_opt(body, "magnet_temp_C")))


def _opt(body, key, scale=1.0):
    v = body.get(key)
    return None if v in (None, "") else float(v) * scale


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
    out = regen_disconnect_overvoltage(_num(body, "C_uF") * 1e-6, _num(body, "V1_V"), float(p), _num(body, "V_limit_V"),
                                       None if body.get("reaction_time_ms") in (None, "") else float(body["reaction_time_ms"]) * 1e-3,
                                       body.get("profile", "constant"), drive=d,
                                       speed_rpm=None if body.get("speed_rpm") in (None, "") else float(body["speed_rpm"]),
                                       ramp_s=_opt(body, "ramp_ms", 1e-3), magnet_temp_C=_opt(body, "magnet_temp_C"))
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


def _coolant(cs: dict | None, inlet_C: float | None) -> CoolantLoop | None:
    if not cs:
        return None
    g = cs.get("glycol_vol_pct")
    props = eg_water_properties(50.0 if g in (None, "") else float(g), 65.0 if inlet_C is None else float(inlet_C))
    cp, rho = cs.get("cp_J_per_kgK"), cs.get("rho_kg_per_m3")
    user = cp not in (None, "") and rho not in (None, "")
    source = "user-entered coolant properties" if user else PROPERTY_SOURCE
    loop = tuple(CoolantStation(str(st["station"]), tuple((k, float(v)) for k, v in st["losses"].items()))
                 for st in (cs.get("loop") or DEFAULT_LOOP))
    return CoolantLoop(_num(cs, "flow_L_per_min"), float(cp) if cp not in (None, "") else props["cp_J_per_kgK"],
                       float(rho) if rho not in (None, "") else props["rho_kg_per_m3"], loop,
                       cs.get("reference", "mean"), None if g in (None, "") else float(g), source)


def _network(n: dict, coolant: CoolantLoop | None) -> FosterNetwork:
    kind = str(n.get("network", "foster")).lower()
    r = flow_scaled(n["R_K_per_W"], n.get("flow_dependent"), n.get("flow_ref_L_per_min"),
                    None if coolant is None else coolant.flow_L_per_min, n.get("flow_exponent", 0.8))
    if kind == "cauer":
        return CauerNetwork(tuple(r), tuple(n["C_J_per_K"])).to_foster()
    if kind != "foster":
        raise InputValidationError(f"network must be 'foster' or 'cauer', got {kind!r}", field="network")
    if n.get("tau_s") is None and n.get("C_J_per_K") is not None:
        return FosterNetwork(tuple(r), tuple(float(ri) * float(ci) for ri, ci in zip(n["R_K_per_W"], n["C_J_per_K"])))
    return FosterNetwork(tuple(r), tuple(n["tau_s"]))


def _thermal_model(spec, inlet_C: float | None = None) -> ThermalModel:
    spec = spec or EXAMPLE_THERMAL
    coolant = _coolant(spec.get("coolant"), inlet_C)
    nodes = tuple(ThermalNode(str(n["id"]), _network(n, coolant), float(n["limit_C"]),
                              tuple((k, float(v)) for k, v in n["loss_share"].items()),
                              n.get("station") if coolant is not None else None) for n in spec["nodes"])
    # the data origin is declared, never derived from the 'validated' flag (a flag is not supplier evidence)
    try:
        origin = DataOrigin(str(spec.get("origin", "estimated")))
    except ValueError:
        raise InputValidationError(f"origin must be one of {[o.value for o in DataOrigin]}", field="origin") from None
    evidence = str(spec.get("validation_evidence") or "").strip()
    prov = Provenance(origin, spec.get("source", "UI example network"), spec.get("revision", "1"),
                      (f"declared validated ({evidence})" if evidence else "declared validated WITHOUT evidence reference")
                      if spec.get("validated") else "unvalidated")
    return ThermalModel(spec.get("model_id", "UI_THERMAL"), spec.get("revision", "1"), nodes, prov,
                        validated=bool(spec.get("validated")),
                        validity=tuple((k, tuple(v)) for k, v in (spec.get("validity") or {}).items()),
                        coolant=coolant, validation_evidence=evidence)


def thermal_details(spec, inlet_C: float | None = None) -> dict:
    """Model as used in the calculation (flow-scaled, Cauer converted) for display: networks, Z_th(inf), coolant."""
    model = _thermal_model(spec, inlet_C)
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
    model = _thermal_model(body.get("model"), sc.coolant_temp_C)
    durs = body.get("durations_s") or [1, 3, 10, 30, 60, 300, "inf"]
    durs = tuple(math.inf if x in ("inf", "continuous") else float(x) for x in durs)
    out = {"availability": torque_availability(d, sc, model, durs, 1 if _num(body, "direction", 1) >= 0 else -1)}
    if body.get("torque_Nm") not in (None, ""):
        out["request"] = thermal_duration(d, sc, model, _num(body, "torque_Nm"), _num(body, "duration_s", 10))
    return _jsonable(out)


EXAMPLE_PROTECTION = {
    "name": "OV after battery disconnect during regeneration (synthetic toy values from the review, section 9.6.1)",
    "variable": "DC-link capacitor voltage", "unit": "V",
    "plant": {"kind": "capacitor_energy", "x0": 700.0, "C_uF": 500.0, "P0_kW": 100.0, "t_ramp_ms": 0.0},
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
        worst_detect = sensor.confirm_samples * sensor.period_s + sensor.exec_delay_s
        bound = ov_trigger_bound(plant.p("C_F"), float(b["limit"]), plant.p("P0_W"), worst_detect + delay,
                                 plant.p("t_ramp_s", 0.0))
    rv = protection_review(plant, sensor, fault, float(b["limit"]), horizon, delay, e_theta,
                           tuple(_plant(n) for n in (b.get("normal") or [])),
                           None if th.get("warning") in (None, "") else float(th["warning"]),
                           None if b.get("warning_needed_ms") in (None, "") else float(b["warning_needed_ms"]) * ms,
                           None if th.get("release") in (None, "") else float(th["release"]),
                           None if b.get("x_normal_max") in (None, "") else float(b["x_normal_max"]),
                           bool(b.get("tight_attainable")), b.get("hw_path"),
                           None if bound is None else bound["V_trigger_max_V"], 0.0, int(b.get("phases", 16)))
    tr = rv["trace"]
    step = max(1, tr["t_s"].size // 1500)
    trace = {"t_s": tr["t_s"][::step].tolist(), "x": tr["x"][::step].tolist(), "y": tr["y"][::step].tolist(),
             "samples_t_s": tr["samples_t_s"][:400].tolist(), "samples_y": tr["samples_y"][:400].tolist(),
             "events": tr["events"], "threshold_true": tr["threshold_true"], "limit": tr["limit"],
             "protected": tr["protected"]}
    free = simulate(plant, sensor, 1e18, math.inf, horizon)          # no reaction: where would the variable go?
    trace["no_reaction_x"] = free["x"][::max(1, free["x"].size // 1500)].tolist()
    trace["no_reaction_t_s"] = free["t_s"][::max(1, free["t_s"].size // 1500)].tolist()
    return _jsonable({"name": b.get("name", ""), "variable": b.get("variable", "x"), "unit": b.get("unit", ""),
                      "thresholds": th, "limit": float(b["limit"]), "rows": rv["rows"],
                      "summary_status": rv["summary_status"], "window": rv["window"], "ov_bound": bound,
                      "phase_sweep": {k: v for k, v in rv["phase_sweep"].items() if k != "rows"},
                      "phase_rows": rv["phase_sweep"]["rows"], "trace": trace, "note": rv["note"]})


def _curve(c: dict, name: str):
    from .extensions.module_loss import Table2D
    try:
        return Table2D(tuple(c["temps_C"]), tuple(c["currents_A"]), tuple(tuple(r) for r in c["values"]), c["unit"],
                       c.get("source", ""))
    except KeyError as exc:
        raise InputValidationError(f"curve {name!r} needs temps_C, currents_A, values and unit ({exc})",
                                   field=f"module.curves.{name}") from None


def module_model_from_dict(m: dict):
    """Datasheet module description -> ModuleLossModel (curves, test conditions, PWM, parallel modules)."""
    from .extensions.module_loss import ModuleLossModel, SwitchDevice
    cv = m.get("curves") or {}
    for key in ("v_on", "v_rev", "e_on", "e_off"):
        if key not in cv:
            raise InputValidationError(f"module curve {key!r} is required", field="module.curves")
    sc = m.get("vdc_scaling") or {}
    dev = SwitchDevice(
        technology=m.get("technology", "IGBT"), v_on=_curve(cv["v_on"], "v_on"), v_rev=_curve(cv["v_rev"], "v_rev"),
        e_on=_curve(cv["e_on"], "e_on"), e_off=_curve(cv["e_off"], "e_off"),
        e_rr=_curve(cv["e_rr"], "e_rr") if cv.get("e_rr") else None,
        v_channel_rev=_curve(cv["v_channel_rev"], "v_channel_rev") if cv.get("v_channel_rev") else None,
        energy_basis=m.get("energy_basis", "per_device"), v_test_V=float(m["v_test_V"]),
        vdc_scaling_exponent=None if sc.get("exponent") in (None, "") else float(sc["exponent"]),
        vdc_scaling_basis=str(sc.get("basis", "")), vdc_scaling_valid_V=tuple(sc["valid_V"]) if sc.get("valid_V") else None,
        value_kind=m.get("value_kind", "typical"), test_conditions=tuple((m.get("test_conditions") or {}).items()),
        source=m.get("source", ""))
    return ModuleLossModel(dev, fsw_Hz=float(m.get("fsw_kHz", 10.0)) * 1e3, modulation=m.get("modulation", "svpwm"),
                           deadtime_s=float(m.get("deadtime_us") or 0.0) * 1e-6, parallel=int(m.get("parallel", 1)),
                           sharing_error=float(m.get("sharing_error_pct") or 0.0) / 100.0,
                           driver_aux_W=float(m.get("driver_aux_W") or 0.0), aux_from_hv_dc=bool(m.get("aux_from_hv_dc")))


def _lin_curve(unit, temps, i_max, a_by_t, b_by_t, n=9):
    cur = [round(i_max * k / (n - 1), 6) for k in range(n)]
    return {"unit": unit, "temps_C": list(temps), "currents_A": cur,
            "values": [[round(a + b * i, 6) for i in cur] for a, b in zip(a_by_t, b_by_t)],
            "source": "synthetic example curve (not a product datasheet)"}


EXAMPLE_MODULE = {
    "name": "synthetic 750 V / 800 A IGBT half-bridge example (NOT a real product - replace with datasheet curves)",
    "technology": "IGBT", "value_kind": "typical", "energy_basis": "per_device", "v_test_V": 600.0,
    "source": "synthetic example for demonstration; curves are linear stand-ins",
    "test_conditions": {"Rg_on_ohm": 1.8, "Rg_off_ohm": 1.8, "Vge_V": 15.0, "deadtime_test_us": 1.5,
                        "stray_L_nH": 20.0},
    "curves": {
        "v_on": _lin_curve("V", (25.0, 150.0), 800.0, (0.80, 0.70), (1.10e-3, 1.60e-3)),
        "v_rev": _lin_curve("V", (25.0, 150.0), 800.0, (0.90, 0.80), (1.00e-3, 1.30e-3)),
        "e_on": _lin_curve("mJ", (25.0, 150.0), 800.0, (0.3, 0.5), (0.034, 0.045)),
        "e_off": _lin_curve("mJ", (25.0, 150.0), 800.0, (0.4, 0.6), (0.040, 0.050)),
        "e_rr": _lin_curve("mJ", (25.0, 150.0), 800.0, (0.2, 0.3), (0.015, 0.022)),
    },
    "fsw_kHz": 10.0, "modulation": "svpwm", "deadtime_us": 1.5, "parallel": 1, "sharing_error_pct": 0.0,
    "driver_aux_W": 12.0, "aux_from_hv_dc": False, "Tj_eval_C": 150.0, "Rth_K_per_W": 0.09, "T_ref_C": 65.0,
}


def module_losses(body):
    """Datasheet-based inverter losses at the decision operating point, fed into P_dc (review 8.8)."""
    from dataclasses import replace as _rep
    from .extensions.module_loss import electrothermal_fixed_point, inverter_losses, loss_claim, standstill_hotspot
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
    full = inverter_losses(model, pt.id_A, pt.iq_A, pt.vd_V, pt.vq_V, vdc, tj)
    out["operating_point"] = {"id_A": pt.id_A, "iq_A": pt.iq_A, "i_peak_A": pt.i_peak_A, "Pac_W": pt.Pac_W,
                              "Pdc_module_W": pt.Pdc_W, "Pinv_module_W": pt.Pinv_W,
                              "Pinv_surrogate_W": None if sol_q.point is None else sol_q.point.Pinv_W,
                              "Pdc_surrogate_W": None if sol_q.point is None else sol_q.point.Pdc_W}
    out["losses"] = {k: full[k] for k in ("per_position_W", "conduction_W", "switching_W", "semiconductor_W",
                                         "driver_aux_W", "dc_side_W", "hottest_position", "hottest_position_W",
                                         "modulation_index", "power_factor", "phi_deg", "established", "problems",
                                         "angle_refinement_rel_diff", "not_modelled")}
    out["leg_detail"] = {"conduction_W": full["leg"]["conduction_W"], "switching_W": full["leg"]["switching_W"],
                         "switching_fraction": full["leg"]["switching_fraction"],
                         "energy_scaling": full["leg"]["energy_scaling"]}
    out["claim"] = loss_claim(full, None if b.get("P_allow_W") in (None, "") else float(b["P_allow_W"]))
    out["standstill"] = {k: v for k, v in standstill_hotspot(model, pt.i_peak_A, vdc, tj).items() if k != "curve"}
    if mspec.get("Rth_K_per_W"):
        fp = electrothermal_fixed_point(model, {"id_A": pt.id_A, "iq_A": pt.iq_A, "vd_V": pt.vd_V, "vq_V": pt.vq_V,
                                                "Vdc_V": vdc}, float(mspec["Rth_K_per_W"]),
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
    "capacitor": {"C_uF": 500.0, "ESL_nH": 15.0, "Rth_K_per_W": 0.35, "T_ref_C": 65.0,
                  "ESR_table": [[100.0, 3.0], [1e3, 2.0], [1e4, 1.6], [1e5, 1.8], [1e6, 3.0]],
                  "ESR_unit": "mohm", "life_hours_table": [], "life_voltage_V": None, "life_basis": "",
                  "source": "synthetic film-capacitor bank example (not a product)"},
    "source": {"R_mohm": 25.0, "L_uH": 2.0, "basis": "example battery + harness impedance (not measured)"},
    "fsw_kHz": 10.0, "modulation": "svpwm",
    "requirement": {"location": "dc_link_bus", "quantity": "voltage_pp", "limit": 15.0, "bandwidth_Hz": 50e3,
                    "note": "example requirement; a real one needs the customer's measurement definition"},
}


def dclink_ripple(body):
    """Capacitor current, ripple and ESR loss at the decision operating point (review 8.9)."""
    from .extensions.dclink_ripple import CapacitorBank, SourceImpedance, ripple_analysis
    b = body or {}
    cfg = {**EXAMPLE_RIPPLE, **(b.get("ripple") or {})}
    cap = cfg["capacitor"]
    k_esr = {"mohm": 1e-3, "ohm": 1.0}[cap.get("ESR_unit", "mohm")]
    bank = CapacitorBank(float(cap["C_uF"]) * 1e-6, tuple((float(f), float(r) * k_esr) for f, r in cap["ESR_table"]),
                         ESL_H=float(cap.get("ESL_nH") or 0.0) * 1e-9,
                         Rth_K_per_W=None if cap.get("Rth_K_per_W") in (None, "") else float(cap["Rth_K_per_W"]),
                         life_hours_table=tuple((float(t), float(h)) for t, h in (cap.get("life_hours_table") or [])),
                         life_voltage_V=None if cap.get("life_voltage_V") in (None, "") else float(cap["life_voltage_V"]),
                         life_basis=str(cap.get("life_basis") or ""))
    src = cfg.get("source")
    source = None if not src else SourceImpedance(float(src["R_mohm"]) * 1e-3, float(src["L_uH"]) * 1e-6,
                                                  str(src.get("basis", "")))
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
                        None if cap.get("T_ref_C") in (None, "") else float(cap["T_ref_C"]))
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
                                                              "limit_A", "origin", "text") if k in r},
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
    "junction_network": {"R_K_per_W": [0.02, 0.05, 0.06, 0.04], "tau_s": [0.005, 0.08, 0.8, 6.0],
                         "note": "synthetic junction-to-coolant Foster network (not a product)"},
    "cycling_model": None, "D_allow": None, "mission_repeats": 1.0, "ton_rule": "none", "cutoff_K": 0.0,
    "note": "synthetic mission; the Tj history comes from a screening electrothermal chain (screening damage only)",
}


def lifetime(body):
    """Mission -> hottest-device losses (datasheet module model) -> Tj(t) -> rainflow -> conditional damage (12)."""
    from dataclasses import replace as _rep
    from .extensions.lifetime import CyclingModel, cycle_analysis, foster_trace
    b = {**EXAMPLE_MISSION, **(body or {})}
    if b.get("trace"):
        tr = b["trace"]
        t, T = np.asarray(tr["t_s"], float), np.asarray(tr["T_C"], float)
        src = "imported Tj trace"
        seg_rows = []
    else:
        mspec = b.get("module") or EXAMPLE_MODULE
        model = module_model_from_dict(mspec)
        base = _drive(b)
        drv = _rep(base, inverter=_rep(base.inverter, loss=None, module_loss=model,
                                       module_Tj_C=float(mspec.get("Tj_eval_C", 150.0))))
        dt = float(b.get("dt_s") or 0.05)
        net = b["junction_network"]
        seg_rows, t_list, p_list = [], [], []
        now = 0.0
        for _rep_k in range(int(b.get("repeat_in_trace") or 1)):
            for sg in b["segments"]:
                sc = Scenario("mission", float(sg["speed_rpm"]), float(b["Vdc_V"]), _limits(b))
                sol = PolicyEvaluator(drv, sc).solve(float(sg["torque_Nm"]))
                det = None if sol.point is None else sol.point.inverter_loss_detail
                p_hot = det["hottest_position_W"] if det and det.get("established") else None
                if _rep_k == 0:
                    seg_rows.append({**sg, "policy": sol.policy_claim.status.value, "P_hot_device_W": p_hot})
                if p_hot is None:
                    raise InputValidationError(f"segment {sg}: hottest-device loss not established "
                                               f"({sol.policy_claim.detail if sol.point is None else det.get('problems')})",
                                               field="segments")
                n = max(2, int(round(float(sg["duration_s"]) / dt)))
                ts = now + np.arange(n) * (float(sg["duration_s"]) / n)
                t_list.append(ts)
                p_list.append(np.full(n, p_hot))
                now += float(sg["duration_s"])
        t = np.concatenate(t_list + [np.array([now])])
        P = np.concatenate(p_list + [np.array([0.0])])
        T = foster_trace(t, P, tuple(net["R_K_per_W"]), tuple(net["tau_s"]), float(b["coolant_C"]))
        src = "screening electrothermal chain (datasheet module losses at the policy points, Foster network)"
    cm = b.get("cycling_model")
    model_obj = None if not cm else CyclingModel(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in cm.items()})
    r = cycle_analysis(t, T, model_obj, b.get("ton_rule", "none"),
                       None if b.get("D_allow") in (None, "") else float(b["D_allow"]),
                       float(b.get("cutoff_K") or 0.0), float(b.get("mission_repeats") or 1.0),
                       repeating_mission=bool(b.get("repeating_mission", True)), source=src)
    step = max(1, t.size // 3000)
    r["trace"] = {"t_s": t[::step].tolist(), "T_C": T[::step].tolist()}
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
    "speed_rpm": 10000.0, "torque_Nm": 150.0, "use_module": True, "fsw_kHz": 10.0, "carrier_shift": 0.0,
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


def acceptance(body):
    return S.acceptance_summary()


ROUTES = {
    "info": info, "evaluate": evaluate, "curve": curve, "map": idiq, "sizing": sizing, "dominance": dominance,
    "relaxation": relaxation, "timing": timing, "discharge": discharge, "passive": passive, "overvoltage": overvoltage,
    "safe_state": safe_state, "thermal": thermal, "acceptance": acceptance, "protection": protection,
    "module_losses": module_losses, "dclink_ripple": dclink_ripple, "asc": asc,
    "lifetime": lifetime, "oew": oew, "oew_compare": oew_compare, "hev_joint": hev_joint, "hev_crank": hev_crank,
    "hev_rejection": hev_rejection, "hev_planetary": hev_planetary,
}
