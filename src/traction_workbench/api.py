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


def _ripple_bank(cfg: dict):
    from .extensions.dclink_ripple import CapacitorBank, SourceImpedance
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
    return bank, source


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


# ---------------------------------------------------------------------------------------------- conducted EMI (P1-C)

EXAMPLE_EMI = {
    "speed_rpm": 6000.0, "torque_Nm": 150.0, "Vdc_V": 600.0,
    "source": {"fsw_kHz": 10.0, "t_rise_ns": 50.0, "t_fall_ns": 50.0, "t_dead_us": 1.0, "modulation": "svpwm",
               "basis": "example gate setting (not a measured switch-node waveform)"},
    "network": {"C_dc_uF": 500.0, "ESR_dc_mohm": 1.0, "ESL_dc_nH": 15.0, "C_y_nF": 100.0, "L_y_nH": 10.0,
                "R_y_mohm": 5.0, "C_par_nF": 2.0, "R_par_ohm": 1.0, "L_par_nH": 100.0, "R_h_mohm": 5.0, "L_h_uH": 1.0,
                "L_ch_uH": 0.0, "k_ch": 0.0, "an_L_uH": 5.0, "an_C_coup_nF": 100.0, "an_R_meas_ohm": 50.0,
                "an_R_par_ohm": 1000.0, "an_C_sup_uF": 1.0, "R_bat_mohm": 10.0, "L_bat_uH": 0.0,
                "validated_up_to_MHz": None, "basis": "synthetic example network (not characterised)"},
    "profile": {"standard": "EXAMPLE (enter the standard)", "edition": "EXAMPLE", "customer_revision": "EXAMPLE",
                "curve_id": "EXAMPLE-FLAT-70", "port": "HV+ / HV-", "method": "voltage via artificial network",
                "detector": "peak", "rbw_Hz": 9000.0, "network": "AN 5 uH / 50 ohm (declare per your standard)",
                "fixture": "EXAMPLE", "operating_condition": "EXAMPLE", "design_reserve_dB": 6.0},
    "limit": {"points": [[150e3, 70.0], [30e6, 70.0]], "unit": "dBuV", "detector": "peak",
              "source": "EXAMPLE ONLY - not a standard limit; enter the approved curve"},
    "band_MHz": [0.15, 30.0], "n_grid": 160, "calibration": None, "E_y_allowed_J": None, "f_control_Hz": 1000.0,
    "measured": None,
}


def _emi_network(n: dict):
    from .extensions.emi import HvNetwork
    g = lambda k, sc, d=0.0: (float(n.get(k)) if n.get(k) not in (None, "") else d) * sc
    return HvNetwork(C_dc_F=g("C_dc_uF", 1e-6), ESR_dc_ohm=g("ESR_dc_mohm", 1e-3), ESL_dc_H=g("ESL_dc_nH", 1e-9),
                     C_y_F=g("C_y_nF", 1e-9), L_y_H=g("L_y_nH", 1e-9), R_y_ohm=g("R_y_mohm", 1e-3),
                     C_par_F=g("C_par_nF", 1e-9), R_par_ohm=g("R_par_ohm", 1.0), L_par_H=g("L_par_nH", 1e-9),
                     R_h_ohm=g("R_h_mohm", 1e-3), L_h_H=g("L_h_uH", 1e-6), L_ch_H=g("L_ch_uH", 1e-6),
                     k_ch=g("k_ch", 1.0), an_L_H=g("an_L_uH", 1e-6, 5.0), an_C_coup_F=g("an_C_coup_nF", 1e-9, 100.0),
                     an_R_meas_ohm=g("an_R_meas_ohm", 1.0, 50.0), an_R_par_ohm=g("an_R_par_ohm", 1.0, 1000.0),
                     an_C_sup_F=g("an_C_sup_uF", 1e-6, 1.0), R_bat_ohm=g("R_bat_mohm", 1e-3, 10.0),
                     L_bat_H=g("L_bat_uH", 1e-6), basis=str(n.get("basis", "")),
                     validated_up_to_Hz=None if n.get("validated_up_to_MHz") in (None, "") else
                     float(n["validated_up_to_MHz"]) * 1e6)


def _emi_profile(pr: dict, lim: dict | None):
    from .extensions.emi import PROFILE_FIELDS, EmiProfile, LimitCurve
    curve = None
    if lim and lim.get("points"):
        curve = LimitCurve(tuple(tuple(x) for x in lim["points"]), lim.get("unit", "dBuV"), lim.get("detector", "peak"),
                           str(lim.get("source", "")))
    fields = {k: pr.get(k) for k in PROFILE_FIELDS}
    return EmiProfile(fields, curve, float(pr.get("design_reserve_dB") or 0.0))


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
                          math.atan2(pt.vq_V, pt.vd_V), abs(pt.f_e_Hz), float(sc["fsw_kHz"]) * 1e3,
                          float(sc["t_rise_ns"]) * 1e-9, float(sc["t_fall_ns"]) * 1e-9,
                          float(sc.get("t_dead_us") or 0.0) * 1e-6, sc.get("modulation", "svpwm"), str(sc.get("basis", "")))
    net = _emi_network({**EXAMPLE_EMI["network"], **(b.get("network") or {})})
    prof = _emi_profile({**EXAMPLE_EMI["profile"], **(b.get("profile") or {})}, b.get("limit"))
    lo, hi = (float(x) * 1e6 for x in b.get("band_MHz") or (0.15, 30.0))
    r = conducted_emission_screening(src, net, prof, lo, hi, int(b.get("n_grid", 160)), b.get("calibration"))
    r["coupling"] = coupling_checks(net, vdc, src, _opt(b, "E_y_allowed_J"), _opt(b, "f_control_Hz"))
    r["operating_point"] = {"speed_rpm": n, "torque_Nm": T, "Vdc_V": vdc, "i_peak_A": pt.i_peak_A,
                            "modulation_index": src.m, "f_e_Hz": src.fe_Hz, "policy": sol.policy_claim.status.value}
    r["source"] = {"fsw_kHz": sc["fsw_kHz"], "t_rise_ns": sc["t_rise_ns"], "t_fall_ns": sc["t_fall_ns"],
                   "t_dead_us": sc.get("t_dead_us"), "basis": src.basis}
    r["network"] = {**EXAMPLE_EMI["network"], **(b.get("network") or {})}
    r["profile"] = {**prof.fields, "design_reserve_dB": prof.design_reserve_dB,
                    "limit_source": None if prof.limit is None else prof.limit.source}
    ms = b.get("measured")
    if ms and ms.get("f_Hz"):
        band = ms.get("band_MHz")
        r["measured"] = measured_trace_verdict(ms["f_Hz"], ms["level_dB"], prof, float(ms.get("U_meas_dB") or 0.0),
                                               ms.get("noise_floor_dB"),
                                               None if not band else (float(band[0]) * 1e6, float(band[1]) * 1e6))
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

EXAMPLE_MODULE_SIC = {
    "name": "synthetic 750 V / 800 A SiC MOSFET half-bridge example (NOT a real product - replace with datasheet curves)",
    "technology": "SiC_MOSFET", "value_kind": "typical", "energy_basis": "per_device", "v_test_V": 600.0,
    "source": "synthetic example for demonstration; curves are linear stand-ins",
    "test_conditions": {"Rg_on_ohm": 2.5, "Rg_off_ohm": 1.0, "Vgs_on_V": 18.0, "Vgs_off_V": -4.0,
                        "deadtime_test_us": 0.3, "stray_L_nH": 12.0},
    "curves": {
        "v_on": _lin_curve("V", (25.0, 150.0), 800.0, (0.0, 0.0), (1.60e-3, 2.60e-3)),
        "v_channel_rev": _lin_curve("V", (25.0, 150.0), 800.0, (0.0, 0.0), (1.65e-3, 2.70e-3)),
        "v_rev": _lin_curve("V", (25.0, 150.0), 800.0, (2.90, 2.60), (1.30e-3, 1.50e-3)),
        "e_on": _lin_curve("mJ", (25.0, 150.0), 800.0, (0.05, 0.08), (0.012, 0.014)),
        "e_off": _lin_curve("mJ", (25.0, 150.0), 800.0, (0.03, 0.04), (0.006, 0.007)),
        "e_rr": _lin_curve("mJ", (25.0, 150.0), 800.0, (0.01, 0.02), (0.0008, 0.0012)),
    },
    "fsw_kHz": 10.0, "modulation": "svpwm", "deadtime_us": 0.3, "parallel": 1, "sharing_error_pct": 0.0,
    "driver_aux_W": 14.0, "aux_from_hv_dc": False, "Tj_eval_C": 150.0, "Rth_K_per_W": 0.12, "T_ref_C": 65.0,
}

EXAMPLE_REDUCER = {
    "ratio": 9.0, "output_boundary": "single-speed gearbox output shaft (differential input)",
    "speed_rpm": [0.0, 16000.0], "torque_Nm": [0.0, 400.0], "oil_temp_C": [20.0, 120.0],
    "eta_forward": 0.975, "eta_reverse": 0.970, "drag_coeffs": [0.15, 2.0e-4, 0.0],
    "basis": "synthetic example (declare supplier map / directional test data)",
}

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
    "compare": {"mode": "fixed_policy", "common_fsw_kHz": 10.0, "coolant_C": 65.0,
                "A": {"label": "IGBT design", "module": EXAMPLE_MODULE, "Rth_K_per_W": 0.09, "loss_error_rel": 0.10,
                      "error_basis": "example engineering budget - replace with DPT / holdout evidence "
                                     "(not a statistical confidence)"},
                "B": {"label": "SiC design", "module": EXAMPLE_MODULE_SIC, "Rth_K_per_W": 0.12,
                      "loss_error_rel": 0.10, "error_basis": "example engineering budget - replace with DPT / holdout "
                                                             "evidence (not a statistical confidence)"},
                "B_fsw_kHz": 20.0,
                "requests": [[2000.0, 250.0, 600.0], [6000.0, 150.0, 600.0], [12000.0, 60.0, 600.0],
                             [4000.0, -120.0, 600.0]]},
    "note": "example reducer, auxiliaries, modules and error budgets are synthetic",
}


def _reducer(r):
    from .analysis.efficiency import LossMap, ReducerModel
    if not r:
        return None
    mk = lambda m: None if not m else LossMap(tuple(m["speeds_rpm"]), tuple(m["torques_Nm"]),   # noqa: E731
                                              tuple(tuple(x) for x in m["loss_W"]))
    return ReducerModel(float(r["ratio"]), str(r.get("output_boundary", "")), tuple(r["speed_rpm"]),
                        tuple(r["torque_Nm"]), tuple(r["oil_temp_C"]), _opt(r, "eta_forward"), _opt(r, "eta_reverse"),
                        tuple(float(x) for x in (r.get("drag_coeffs") or (0.0, 0.0, 0.0))),
                        mk(r.get("map_forward")), mk(r.get("map_reverse")), str(r.get("basis", "")))


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


def efficiency(body):
    """Five boundary efficiencies, loss ledger and flow table at one policy point (module-efficiency addendum)."""
    from .analysis.efficiency import point_ledger
    b = {**EXAMPLE_EFFICIENCY, **(body or {})}
    d = _eff_drive(b)
    n, T, vdc = _num(b, "speed_rpm"), _num(b, "torque_Nm"), _num(b, "Vdc_V")
    sol = PolicyEvaluator(d, Scenario("eff", n, vdc, _limits(b))).solve(T)
    out = {"request": {"speed_rpm": n, "torque_Nm": T, "Vdc_V": vdc}, "claims": [c.to_dict() for c in sol.claims],
           "loss_model": b.get("loss_model", "module")}
    if sol.point is None:
        out["ledger"] = None
        out["reason"] = sol.policy_claim.detail
        return _jsonable(out)
    out["ledger"] = point_ledger(sol.point, d, _reducer(b.get("reducer")), _opt(b, "oil_temp_C"), _aux(b))
    out["point"] = {"id_A": sol.point.id_A, "iq_A": sol.point.iq_A, "i_peak_A": sol.point.i_peak_A,
                    "energy_mode": sol.point.energy_mode, "Tshaft_Nm": sol.point.Tshaft_Nm,
                    "Te_Nm": sol.point.Te_Nm, "module_detail": sol.point.inverter_loss_detail}
    return _jsonable(out)


def efficiency_map(body):
    """Boundary efficiency maps on a speed x torque grid (policy points; status mask kept, no hole filling)."""
    from .analysis.efficiency import DEFINED, point_ledger
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
                      "meaning": "values at minimum-current policy points; only FEASIBLE cells are feasible "
                                 "operation, UNKNOWN cells are shown hatched, INFEASIBLE cells are blank"})


def efficiency_mission(body):
    """Mission energy ledger per direction and boundary (E+ / E- per port; no averaged eta)."""
    from .analysis.efficiency import mission_energy, point_ledger
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
                      "loss_model": b.get("loss_model", "module")})


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
    from .analysis.efficiency import compare_modules
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
    return _jsonable(r)


# ------------------------------------------------------------------ variable PWM (variable-PWM / anti-jerk addendum)

EXAMPLE_PWM = {
    "module": EXAMPLE_MODULE, "Rth_K_per_W": 0.09, "coolant_C": 65.0, "modulation": "svpwm", "L_hf_uH": 200.0,
    "baseline_fsw_kHz": 10.0,
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
    "timing": {"sample_to_latch_us": 25.0, "filter_delay_us": 5.0, "updates_per_period": 1,
               "modulator_delay_fraction": 0.5, "min_pulse_us": 1.5, "wcet_source": "declared estimate",
               "basis": "example target timing (replace with the measured delay chain of the ECU)"},
    "loop": {"L_uH": 300.0, "R_mohm": 15.0, "bandwidth_Hz": 500.0, "gain_mapping": "continuous",
             "reference_fsw_kHz": 10.0, "integrator_storage": "output", "on_transition": "keep", "anti_windup": True,
             "basis": "example PI with pole-zero cancellation at the mean dq inductance"},
    "sensing": {"kind": "leg_shunt", "settle_us": 2.0, "aperture_us": 0.6, "sample_points": "valley",
                "edge_noise": "own_leg", "reconstruct_from_two": True, "channel_skew_ns": 200.0,
                "invalid_policy": "hold", "max_sample_age_us": 150.0, "current_error_max_A": 15.0,
                "basis": "example: three Kelvin-connected low-side shunts, simultaneous sampling ADCs, the other legs' "
                         "edges assumed outside the aperture - replace with the target trigger / ADC timing"},
    "measurement_noise": {"speed_rpm": 20.0, "torque_abs_Nm": 4.0, "sensor_temp_C": 1.0, "Vdc_V": 10.0,
                          "basis": "example peak-to-peak noise of the schedule inputs (declare the measured values)"},
    "harmonic": {"f_Hz": [0.0, 1e3, 5e3, 10e3, 20e3, 50e3, 1e5, 3e5, 1e6, 3e6],
                 "rac_over_rdc": [1.0, 1.02, 1.3, 1.8, 2.8, 5.0, 8.0, 14.0, 25.0, 45.0],
                 "iron_bound_W": [[6e3, 420.0], [8e3, 350.0], [10e3, 300.0], [16e3, 220.0]],
                 "basis": "synthetic example (declare FEA / measured R_ac(f) and the harmonic iron-loss bound)"},
    "pwm_limits": {"Tj_max_C": 150.0, "i_peak_incl_ripple_max_A": 700.0, "cap_rms_max_A": 250.0,
                   "phase_margin_min_deg": 45.0, "pulse_ratio_min": None, "transition_excursion_max_A": 50.0},
    "use_capacitor": True,
    "transition": {"from_kHz": 10.0, "to_kHz": 20.0, "duty": 0.9, "deadtime_us": 1.0, "write_fraction": 0.3},
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


def _timing(t: dict):
    from .extensions.pwm_policy import TimingConfig
    return TimingConfig(float(t["sample_to_latch_us"]) * 1e-6, float(t.get("filter_delay_us") or 0.0) * 1e-6,
                        int(t.get("updates_per_period") or 1), float(t.get("modulator_delay_fraction", 0.5)),
                        float(t.get("min_pulse_us") or 0.0) * 1e-6, str(t.get("basis", "")),
                        str(t.get("wcet_source", "declared estimate")))


def _loop(lp: dict | None):
    from .extensions.pwm_policy import CurrentLoop
    if not lp:
        return None
    L, R = float(lp["L_uH"]) * 1e-6, float(lp["R_mohm"]) * 1e-3
    kp = 2 * math.pi * float(lp["bandwidth_Hz"]) * L
    return CurrentLoop(L, R, kp, kp * R / L, str(lp.get("gain_mapping", "continuous")),
                       None if lp.get("reference_fsw_kHz") in (None, "") else float(lp["reference_fsw_kHz"]) * 1e3,
                       str(lp.get("basis", "")), str(lp.get("integrator_storage", "output")),
                       str(lp.get("on_transition", "keep")), bool(lp.get("anti_windup", True)))


def _sensing(sd: dict | None):
    from .extensions.pwm_policy import SensingConfig
    if not sd:
        return None
    return SensingConfig(str(sd["kind"]), float(sd["settle_us"]) * 1e-6, float(sd["aperture_us"]) * 1e-6,
                         str(sd.get("sample_points", "valley")), str(sd.get("edge_noise", "any_leg")),
                         bool(sd.get("reconstruct_from_two", True)), float(sd.get("channel_skew_ns") or 0.0) * 1e-9,
                         str(sd.get("invalid_policy", "none")), _opt(sd, "predict_error_fraction"),
                         _opt(sd, "max_sample_age_us", 1e-6), _opt(sd, "current_error_max_A"), str(sd.get("basis", "")))


def _noise(nd: dict | None):
    if not nd:
        return None
    return {k: float(v) for k, v in nd.items() if k != "basis" and v not in (None, "")}


def pwm_policies(body):
    """Fixed-frequency baseline vs a declared schedule on the same trajectory (P1-PWM)."""
    from .analysis.efficiency import ModuleCandidate
    from .extensions.pwm_policy import HarmonicLossData, PwmLimits, evaluate_policies, fixed_schedule
    b = {**EXAMPLE_PWM, **(body or {})}
    model = module_model_from_dict(b.get("module") or EXAMPLE_MODULE)
    cand = ModuleCandidate(str((b.get("module") or {}).get("name", "module")), model, float(b["Rth_K_per_W"]))
    hd = b.get("harmonic")
    harm = None if not hd else HarmonicLossData(tuple(float(x) for x in hd["f_Hz"]),
                                                tuple(float(x) for x in hd["rac_over_rdc"]),
                                                tuple((float(f), float(w)) for f, w in (hd.get("iron_bound_W") or [])),
                                                str(hd.get("basis", "")))
    bank, source = _ripple_bank(EXAMPLE_RIPPLE) if b.get("use_capacitor") else (None, None)
    lim = PwmLimits(**{k: (None if v in (None, "") else float(v)) for k, v in (b.get("pwm_limits") or {}).items()})
    pols = [fixed_schedule(float(b["baseline_fsw_kHz"]) * 1e3)] + [_schedule(sd, b) for sd in b.get("schedules") or []]
    names = [p.name for p in pols]
    if len(set(names)) != len(names):
        raise InputValidationError("policy names must be unique", field="schedules")
    segs = [{**s, "Vdc_V": float(s.get("Vdc_V") or 600.0)} for s in b["segments"]]
    r = evaluate_policies(_drive(b), cand, segs, pols, float(b["coolant_C"]), _limits(b), _timing(b["timing"]),
                          float(b["L_hf_uH"]) * 1e-6, _loop(b.get("loop")), harm, bank, source,
                          str(b.get("modulation", "svpwm")), lim, _sensing(b.get("sensing")),
                          _noise(b.get("measurement_noise")))
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
    loop = _loop(b.get("loop"))
    trn = b["transition"]
    f0, f1 = float(trn["from_kHz"]) * 1e3, float(trn["to_kHz"]) * 1e3
    variants = {}
    if loop is not None:
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
            variants[label] = transition_transient(lp, tc, f0, f1, pt.iq_A, e, pt.voltage_budget_V)
    lim = (b.get("pwm_limits") or {}).get("transition_excursion_max_A")
    return _jsonable({"point": {"speed_rpm": n, "torque_Nm": T, "Vdc_V": vdc, "m": m, "f_e_Hz": fe, "fsw_Hz": fsw,
                                "iq_A": pt.iq_A, "vq_V": pt.vq_V, "i_peak_A": pt.i_peak_A,
                                "voltage_budget_V": pt.voltage_budget_V},
                      "sensing": sens.__dict__, "sampling_here": here, "sampling_curves": curves,
                      "transition": {"from_Hz": f0, "to_Hz": f1, "excursion_limit_A": lim, "variants": variants}})


def pwm_timing(body):
    """Delay ledger, current-loop margin and phase lag vs carrier frequency; one clock-level period transition."""
    from .extensions.pwm_policy import delay_ledger, phase_lag_deg, transition_check
    b = {**EXAMPLE_PWM, **(body or {})}
    tc = _timing(b["timing"])
    loop = _loop(b.get("loop"))
    f_mode = float(b.get("mode_frequency_Hz") or 20.0)
    rows = []
    for fsw in [float(x) * 1e3 for x in (b.get("fsw_sweep_kHz") or [4, 5, 6, 8, 10, 12, 16, 20, 25, 30, 40])]:
        led = delay_ledger(fsw, tc)
        row = {"fsw_Hz": fsw, "deadline_ok": led["deadline_ok"], "deadline_margin_s": led["deadline_margin_s"],
               "total_delay_s": led["total_delay_s"]}
        if led["total_delay_s"] is not None:
            row["phase_at_mode_deg"] = phase_lag_deg(f_mode, led["total_delay_s"])
            if loop is not None:
                mg = loop.margins(led["total_delay_s"], loop.effective_Ki(fsw, tc.updates_per_period))
                row.update({"phase_margin_deg": mg["phase_margin_deg"], "crossover_Hz": mg["crossover_Hz"],
                            "phase_at_crossover_deg": mg.get("delay_phase_at_crossover_deg")})
        rows.append(row)
    trn = b["transition"]
    out = {"rows": rows, "mode_frequency_Hz": f_mode, "timing": tc.__dict__,
           "loop": None if loop is None else loop.__dict__}
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
    "driveline": {"Jm_kgm2": 0.04, "J_out_kgm2": 200.0, "k_out_Nm_per_rad": 12000.0, "c_out_Nms_per_rad": 30.0,
                  "ratio": 9.0, "wheel_radius_m": 0.33, "contact": "maintained", "backlash_out_rad": None,
                  "basis": "synthetic two-inertia ROM (1800 kg, r 0.33 m, two half-shafts) - replace with an "
                           "FRF-identified / validated torsional model"},
    "maneuver": {"T0_Nm": 20.0, "T1_Nm": 150.0, "t_step_s": 0.05, "t_end_s": 1.2, "speed_rpm": 2000.0,
                 "Vdc_V": 600.0, "TL_out_Nm": 0.0, "window": "capability", "emergency_t_s": None,
                 "emergency_T_Nm": None},
    "controller": {"sample_ms": 1.0, "delay_ms": 2.0, "actuator_tau_ms": 1.5,
                   "basis": "example timing / current-loop ROM (replace with the target delay chain and a "
                            "validated torque response)"},
    "variants": {"off": {},
                 "shaping": {"shaper": {"kind": "rate", "rate_Nm_per_s": 1500.0}},
                 "feedback": {"damping": {"kind": "motor_speed_hpf", "Kd_Nms_per_rad": 1.5, "hpf_Hz": 2.0}},
                 "combined": {"shaper": {"kind": "rate", "rate_Nm_per_s": 1500.0},
                              "damping": {"kind": "motor_speed_hpf", "Kd_Nms_per_rad": 1.5, "hpf_Hz": 2.0}}},
    "requirement": {"t_to_90_max_s": 0.25, "peak_vehicle_jerk_max_m_s3": 35.0, "settle_max_s": 0.6,
                    "safety_reaction_max_s": 0.02,
                    "basis": "example comfort / response targets (declare the program's definitions)"},
    "sensing": {"load_speed_skew_ms": 0.0, "dropouts_ms": [], "dropout_signal": "load", "stale_limit_ms": 20.0,
                "fade_ms": 10.0, "basis": "example speed-signal timing and fallback (declare the target's message "
                                          "timing, timestamps and the degraded-mode strategy)"},
    "check_slew": True,
    "stability": {"Kd_list": [0.5, 1.0, 1.5, 2.5, 4.0], "delay_ms_list": [0.0, 1.0, 2.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0]},
    "note": "example driveline, controller and targets are synthetic",
}


def _driveline(dd: dict):
    from .extensions.driveline import Driveline
    return Driveline(float(dd["Jm_kgm2"]), float(dd["J_out_kgm2"]), float(dd["k_out_Nm_per_rad"]),
                     float(dd["c_out_Nms_per_rad"]), float(dd.get("ratio") or 1.0), _opt(dd, "wheel_radius_m"),
                     str(dd.get("contact", "maintained")), _opt(dd, "backlash_out_rad"), str(dd.get("basis", "")))


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
                              float(sn.get("fade_ms") or 0.0) * 1e-3))


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
    dl = _driveline(b["driveline"])
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
    dl = _driveline(b["driveline"])
    c = b["controller"]
    st = b["stability"]
    kind = (b["variants"].get("feedback") or {}).get("damping", {}).get("kind", "relative_speed")
    hpf = (b["variants"].get("feedback") or {}).get("damping", {}).get("hpf_Hz")
    base = Controller(float(c["sample_ms"]) * 1e-3, 0.0, float(c.get("actuator_tau_ms") or 0.0) * 1e-3)
    grid = []
    for Kd in st["Kd_list"]:
        row = []
        for dms in st["delay_ms_list"]:
            ctl = _rep(base, delay_s=float(dms) * 1e-3, damping=Damping(kind, float(Kd), hpf))
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
    """Star-of-slots layout, winding factors, three-phase MMF spectrum and consistency with the drive model."""
    from .analysis.machine_design import effective_turns_ratio, winding_layout
    b = {**EXAMPLE_WINDING, **(body or {})}
    Q, p = int(_num(b, "Q")), int(_num(b, "p"))
    y = None if b.get("y") in (None, "") else int(_num(b, "y"))
    a = int(_num(b, "parallel_paths", 1))
    nc = None if b.get("turns_per_coil") in (None, "") else int(_num(b, "turns_per_coil"))
    w = winding_layout(Q, p, y, 3, int(b.get("harmonics") or 25), a, nc)
    d = _drive(b)
    cons = [{"item": "pole pairs", "ok": d.motor.pole_pairs == p,
             "detail": f"winding p = {p}, drive model p = {d.motor.pole_pairs}"},
            {"item": "feasible slot / pole combination", "ok": w["feasible"], "detail": f"Q/(3 t) = {Q / (3 * w['t_periodicity']):g}"},
            {"item": "balanced three-phase", "ok": w["balanced"], "detail": w["phase_sequence"]},
            {"item": "parallel paths symmetric", "ok": w["parallel_paths_ok"],
             "detail": f"a = {a}, divisors of {w['max_parallel_paths']} allowed"}]
    w["consistency"] = cons
    cmp = b.get("compare")
    if cmp and nc is not None:
        w2 = winding_layout(Q, p, y, 3, 1, int(cmp.get("parallel_paths") or a), int(cmp.get("turns_per_coil") or nc))
        w["compare"] = {"turns_per_coil": w2["turns_per_coil"], "parallel_paths": w2["parallel_paths"],
                        "N_series": w2["N_series"], "N_eff": w2["N_eff"], "k_turns": effective_turns_ratio(w, w2),
                        "parallel_paths_ok": w2["parallel_paths_ok"],
                        "meaning": "k_N for the scaling trade study (same slots, poles and pitch only)"}
    return _jsonable(w)


def concept_sizing(body):
    """Rotor volume from a declared air-gap shear stress: a concept envelope, not a rating."""
    from .analysis.machine_design import concept_sizing as cs
    b = {**EXAMPLE_SIZING, **(body or {})}
    r = cs(_num(b, "T_Nm"), tuple(float(x) for x in b["sigma_kPa"]), tuple(float(x) for x in b["aspect_L_over_D"]),
           _opt(b, "n_max_rpm"), _opt(b, "tip_speed_limit_m_s"))
    r["basis"] = b.get("basis", "")
    return _jsonable(r)


def acceptance(body):
    return S.acceptance_summary()


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
}
