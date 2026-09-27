"""UI-agnostic request API used by the desktop application and the CLI.

Numbers in request bodies use the units in their field names (rpm, V, N_m, A,
W, s, C, uF).  Every requirement request is converted into the documented case
format, so interactive input goes through the same validation as case files.
"""

from __future__ import annotations

import math

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
            "example_protection": EXAMPLE_PROTECTION, "example_protection_ot": EXAMPLE_PROTECTION_OT}


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
    "tight_attainable": False, "hw_path": "not declared", "phases": 16,
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
    "normal": [], "tight_attainable": False, "hw_path": "not declared", "phases": 8,
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


def acceptance(body):
    return S.acceptance_summary()


ROUTES = {
    "info": info, "evaluate": evaluate, "curve": curve, "map": idiq, "sizing": sizing, "dominance": dominance,
    "relaxation": relaxation, "timing": timing, "discharge": discharge, "passive": passive, "overvoltage": overvoltage,
    "safe_state": safe_state, "thermal": thermal, "acceptance": acceptance, "protection": protection,
}
