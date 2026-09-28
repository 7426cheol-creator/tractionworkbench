"""Product-data parsers: a JSON-style dict -> the validated engine model (units in the field names).

One parser per kind of product data (module curves, DC-link capacitor bank, thermal network, current loop and
sampling, torque path, driveline, reducer, EMI network, FTTI chain).  The request API and the project package use
the same parsers, so a section of a project file and a request body are validated identically.  Parsers never fill
product data from an example: a missing required field is an input error.
"""

from __future__ import annotations

import math

from .errors import InputValidationError
from .extensions.coolant import PROPERTY_SOURCE, CoolantLoop, CoolantStation, eg_water_properties
from .extensions.thermal import CauerNetwork, FosterNetwork, ThermalModel, ThermalNode, flow_scaled
from .extensions.timing import TimingChain, TimingItem
from .models import DataOrigin, Provenance

DEFAULT_COOLANT_LOOP = [{"station": "inverter", "losses": {"inverter": 1.0}},
                {"station": "motor", "losses": {"copper": 1.0, "rotational": 1.0}}]


def num(body, key, default=None):
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


def opt(body, key, scale=1.0):
    v = body.get(key)
    return None if v in (None, "") else float(v) * scale


def curve_from_dict(c: dict, name: str):
    from .models.module_loss import Table2D
    try:
        return Table2D(tuple(c["temps_C"]), tuple(c["currents_A"]), tuple(tuple(r) for r in c["values"]), c["unit"],
                       c.get("source", ""))
    except KeyError as exc:
        raise InputValidationError(f"curve {name!r} needs temps_C, currents_A, values and unit ({exc})",
                                   field=f"module.curves.{name}") from None


def module_model_from_dict(m: dict):
    """Datasheet module description -> ModuleLossModel (curves, test conditions, PWM, parallel modules)."""
    from .models.module_loss import ModuleLossModel, SwitchDevice
    cv = m.get("curves") or {}
    for key in ("v_on", "v_rev", "e_on", "e_off"):
        if key not in cv:
            raise InputValidationError(f"module curve {key!r} is required", field="module.curves")
    sc = m.get("vdc_scaling") or {}
    dev = SwitchDevice(
        technology=m.get("technology", "IGBT"), v_on=curve_from_dict(cv["v_on"], "v_on"), v_rev=curve_from_dict(cv["v_rev"], "v_rev"),
        e_on=curve_from_dict(cv["e_on"], "e_on"), e_off=curve_from_dict(cv["e_off"], "e_off"),
        e_rr=curve_from_dict(cv["e_rr"], "e_rr") if cv.get("e_rr") else None,
        v_channel_rev=curve_from_dict(cv["v_channel_rev"], "v_channel_rev") if cv.get("v_channel_rev") else None,
        energy_basis=m.get("energy_basis", "per_device"), v_test_V=float(m["v_test_V"]),
        vdc_scaling_exponent=None if sc.get("exponent") in (None, "") else float(sc["exponent"]),
        vdc_scaling_basis=str(sc.get("basis", "")), vdc_scaling_valid_V=tuple(sc["valid_V"]) if sc.get("valid_V") else None,
        value_kind=m.get("value_kind", "typical"), test_conditions=tuple((m.get("test_conditions") or {}).items()),
        source=m.get("source", ""))
    return ModuleLossModel(dev, fsw_Hz=float(m.get("fsw_kHz", 10.0)) * 1e3, modulation=m.get("modulation", "svpwm"),
                           deadtime_s=float(m.get("deadtime_us") or 0.0) * 1e-6, parallel=int(m.get("parallel", 1)),
                           sharing_error=float(m.get("sharing_error_pct") or 0.0) / 100.0,
                           driver_aux_W=float(m.get("driver_aux_W") or 0.0), aux_from_hv_dc=bool(m.get("aux_from_hv_dc")))


def capacitor_bank_from_dict(cfg: dict):
    from .extensions.dclink_ripple import CapacitorBank, SourceImpedance
    cap = cfg["capacitor"]
    k_esr = {"mohm": 1e-3, "ohm": 1.0}[cap.get("ESR_unit", "mohm")]
    dom = cap.get("T_valid_C")
    bank = CapacitorBank(float(cap["C_uF"]) * 1e-6, tuple((float(f), float(r) * k_esr) for f, r in cap["ESR_table"]),
                         ESL_H=float(cap.get("ESL_nH") or 0.0) * 1e-9,
                         Rth_K_per_W=None if cap.get("Rth_K_per_W") in (None, "") else float(cap["Rth_K_per_W"]),
                         ESR_temp_coeff_per_K=float(cap.get("ESR_temp_coeff_per_K") or 0.0),
                         T_ref_C=float(cap.get("ESR_table_T_C") if cap.get("ESR_table_T_C") not in (None, "") else 25.0),
                         T_valid_C=None if not dom else tuple(float(v) for v in dom),
                         Rth_basis=str(cap.get("Rth_basis") or ""), count=int(cap.get("count") or 1),
                         symmetric_layout=bool(cap.get("symmetric_layout")),
                         life_hours_table=tuple((float(t), float(h)) for t, h in (cap.get("life_hours_table") or [])),
                         life_voltage_V=None if cap.get("life_voltage_V") in (None, "") else float(cap["life_voltage_V"]),
                         life_basis=str(cap.get("life_basis") or ""))
    src = cfg.get("source")
    source = None if not src else SourceImpedance(float(src["R_mohm"]) * 1e-3, float(src["L_uH"]) * 1e-6,
                                                  str(src.get("basis", "")))
    return bank, source


def coolant_from_dict(cs: dict | None, inlet_C: float | None) -> CoolantLoop | None:
    if not cs:
        return None
    g = cs.get("glycol_vol_pct")
    props = eg_water_properties(50.0 if g in (None, "") else float(g), 65.0 if inlet_C is None else float(inlet_C))
    cp, rho = cs.get("cp_J_per_kgK"), cs.get("rho_kg_per_m3")
    user = cp not in (None, "") and rho not in (None, "")
    source = "user-entered coolant properties" if user else PROPERTY_SOURCE
    loop = tuple(CoolantStation(str(st["station"]), tuple((k, float(v)) for k, v in st["losses"].items()))
                 for st in (cs.get("loop") or DEFAULT_COOLANT_LOOP))
    return CoolantLoop(num(cs, "flow_L_per_min"), float(cp) if cp not in (None, "") else props["cp_J_per_kgK"],
                       float(rho) if rho not in (None, "") else props["rho_kg_per_m3"], loop,
                       cs.get("reference", "mean"), None if g in (None, "") else float(g), source)


def thermal_network_from_dict(n: dict, coolant: CoolantLoop | None) -> FosterNetwork:
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


def _cauer_of(n: dict, coolant: CoolantLoop | None):
    """The declared Cauer ladder (flow-scaled like the Foster equivalent), kept for physical node states."""
    if str(n.get("network", "foster")).lower() != "cauer":
        return None
    r = flow_scaled(n["R_K_per_W"], n.get("flow_dependent"), n.get("flow_ref_L_per_min"),
                    None if coolant is None else coolant.flow_L_per_min, n.get("flow_exponent", 0.8))
    return CauerNetwork(tuple(r), tuple(n["C_J_per_K"]))


def thermal_model_from_dict(spec, inlet_C: float | None = None) -> ThermalModel:
    if not spec:
        raise InputValidationError("a thermal model needs its nodes", field="thermal")
    coolant = coolant_from_dict(spec.get("coolant"), inlet_C)
    nodes = tuple(ThermalNode(str(n["id"]), thermal_network_from_dict(n, coolant), float(n["limit_C"]),
                              tuple((k, float(v)) for k, v in n["loss_share"].items()),
                              n.get("station") if coolant is not None else None, _cauer_of(n, coolant))
                  for n in spec["nodes"])
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


def reducer_from_dict(r):
    from .analysis.efficiency import LossMap, ReducerModel
    if not r:
        return None
    mk = lambda m: None if not m else LossMap(tuple(m["speeds_rpm"]), tuple(m["torques_Nm"]),   # noqa: E731
                                              tuple(tuple(x) for x in m["loss_W"]))
    return ReducerModel(float(r["ratio"]), str(r.get("output_boundary", "")), tuple(r["speed_rpm"]),
                        tuple(r["torque_Nm"]), tuple(r["oil_temp_C"]), opt(r, "eta_forward"), opt(r, "eta_reverse"),
                        tuple(float(x) for x in (r.get("drag_coeffs") or (0.0, 0.0, 0.0))),
                        mk(r.get("map_forward")), mk(r.get("map_reverse")), str(r.get("basis", "")))


def pwm_timing_from_dict(t: dict):
    from .extensions.pwm_policy import TimingConfig
    return TimingConfig(float(t["sample_to_latch_us"]) * 1e-6, float(t.get("filter_delay_us") or 0.0) * 1e-6,
                        int(t.get("updates_per_period") or 1), float(t.get("modulator_delay_fraction", 0.5)),
                        float(t.get("min_pulse_us") or 0.0) * 1e-6, str(t.get("basis", "")),
                        str(t.get("wcet_source", "declared estimate")))


def current_loop_from_dict(lp: dict | None):
    """PI current loop(s) with pole-zero cancellation at the DESIGN inductance: per axis ({'d', 'q'}) when L_d and
    L_q are declared, else one loop whose gains apply to both axes (checked against both machine axes)."""
    from .extensions.pwm_policy import CurrentLoop
    if not lp:
        return None
    R = float(lp["R_mohm"]) * 1e-3
    w = 2 * math.pi * float(lp["bandwidth_Hz"])

    def one(L):
        return CurrentLoop(L, R, w * L, w * R, str(lp.get("gain_mapping", "continuous")),
                           None if lp.get("reference_fsw_kHz") in (None, "") else float(lp["reference_fsw_kHz"]) * 1e3,
                           str(lp.get("basis", "")), str(lp.get("integrator_storage", "output")),
                           str(lp.get("on_transition", "keep")), bool(lp.get("anti_windup", True)))
    if lp.get("Ld_uH") not in (None, "") and lp.get("Lq_uH") not in (None, ""):
        return {"d": one(float(lp["Ld_uH"]) * 1e-6), "q": one(float(lp["Lq_uH"]) * 1e-6)}
    return one(float(lp["L_uH"]) * 1e-6)


def sensing_from_dict(sd: dict | None):
    from .extensions.pwm_policy import SensingConfig
    if not sd:
        return None
    return SensingConfig(str(sd["kind"]), float(sd["settle_us"]) * 1e-6, float(sd["aperture_us"]) * 1e-6,
                         str(sd.get("sample_points", "valley")), str(sd.get("edge_noise", "any_leg")),
                         bool(sd.get("reconstruct_from_two", True)), float(sd.get("channel_skew_ns") or 0.0) * 1e-9,
                         str(sd.get("invalid_policy", "none")), opt(sd, "predict_error_fraction"),
                         opt(sd, "max_sample_age_us", 1e-6), opt(sd, "current_error_max_A"), str(sd.get("basis", "")))


def noise_from_dict(nd: dict | None):
    if not nd:
        return None
    return {k: float(v) for k, v in nd.items() if k != "basis" and v not in (None, "")}


def driveline_from_dict(dd: dict):
    from .extensions.driveline import Driveline
    return Driveline(float(dd["Jm_kgm2"]), float(dd["J_out_kgm2"]), float(dd["k_out_Nm_per_rad"]),
                     float(dd["c_out_Nms_per_rad"]), float(dd.get("ratio") or 1.0), opt(dd, "wheel_radius_m"),
                     str(dd.get("contact", "maintained")), opt(dd, "backlash_out_rad"), str(dd.get("basis", "")))


def emi_network_from_dict(n: dict):
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


def timing_chain_from_dict(b: dict) -> TimingChain:
    """FTTI chain: events, timing items (ms) with owners, budgets and the safe endpoint."""
    ms = 1e-3
    items = tuple(TimingItem(i["id"], i["from"], i["to"], i.get("owner", ""),
                             None if i.get("max_ms") in (None, "") else float(i["max_ms"]) * ms,
                             None if i.get("min_ms") in (None, "") else float(i["min_ms"]) * ms,
                             None if i.get("nom_ms") in (None, "") else float(i["nom_ms"]) * ms,
                             None if i.get("period_ms") in (None, "") else float(i["period_ms"]) * ms)
                  for i in b["items"])
    return TimingChain(b.get("chain_id", "chain"), b.get("fault", ""), float(b["ftti_ms"]) * ms, tuple(b["events"]),
                       items, b.get("detection_event"),
                       None if b.get("fdti_budget_ms") in (None, "") else float(b["fdti_budget_ms"]) * ms,
                       None if b.get("frti_budget_ms") in (None, "") else float(b["frti_budget_ms"]) * ms,
                       safe_event=b.get("safe_event") or None,
                       endpoint_kind=b.get("endpoint_kind") or "physical_safe_state",
                       worst_case_attainable=bool(b.get("worst_case_attainable", False)))
