"""System Composer / Simulink Data Dictionary candidates: stable IDs for components, connections, signals, data
items and requirement links, written from the project so the company architecture can MAP them - never overwrite it.

Three relations are kept apart, as the architecture tools keep them apart:

* physical connections (DC, three-phase AC, shaft, heat) - domain, across / through quantity, polarity; never a
  signal bus;
* information signals (torque request, measurements, dq references) - direction, type, unit, rate, latency and
  range where the project declares them, "not declared" where it does not (nothing is invented);
* allocation / evidence relations (which requirement is verified where, by which case, on what evidence).

Data items are SLDD candidates.  A value, its interface type / range and a software saturation are different
objects: the inverter's fundamental current limit is a physical limit of this design, not a saturation parameter
nor an interface range.  Target names come from the company profile's explicit ``id_map``; a name that merely
matches an existing entry is never taken to be the same item.
"""

from __future__ import annotations

import math

from ..io import drive_from_dict
from ..models.flux import ConstantFluxModel

ARCH_SCHEMA = "twb-mathworks-architecture/1"
PROFILE_SCHEMA = "twb-mathworks-profile/1"
NOT_DECLARED = "not declared by the Workbench project (company profile / controller specification)"


def _item(iid, name, value, unit, meaning, section, project, kind="physical_parameter", **extra):
    s = project.sections[section]
    if isinstance(value, float) and math.isinf(value):
        value = "unlimited"                          # a declared unlimited DC limit (the project file token)
    return {"id": iid, "default_name": name, "value": value, "unit": unit, "kind": kind, "meaning": meaning,
            "owner": f"project section '{section}'", "section_digest": s.digest,
            "provenance": dict(s.provenance), **extra}


def data_items(project) -> list:
    drive = drive_from_dict(project.data("drive"))
    m, inv, dom = drive.motor, drive.inverter, drive.domain
    out = [_item("PAR.MOTOR.pole_pairs", "Motor_PolePairs", m.pole_pairs, "1", "pole PAIRS (not poles)", "drive",
                 project),
           _item("PAR.MOTOR.Rs", "Motor_Rs", m.Rs_ohm, "Ohm", "per-phase stator resistance (wye equivalent) at "
                 + (f"{m.reference_winding_temp_C:g} degC" if m.reference_winding_temp_C is not None
                    else "an undeclared reference temperature"), "drive", project)]
    if isinstance(m.flux, ConstantFluxModel):
        out += [_item("PAR.MOTOR.psi_pm", "Motor_PsiPM", m.flux.psi_pm_Wb, "Wb",
                      "PM flux linkage, phase peak, amplitude-invariant dq", "drive", project),
                _item("PAR.MOTOR.Ld", "Motor_Ld", m.flux.Ld_H, "H", "d-axis inductance (constant-parameter model)",
                      "drive", project),
                _item("PAR.MOTOR.Lq", "Motor_Lq", m.flux.Lq_H, "H", "q-axis inductance (constant-parameter model)",
                      "drive", project)]
    else:
        out.append(_item("PAR.MOTOR.flux_map", "Motor_FluxMap", None, "Wb",
                         "dq flux-linkage map: exported as model data (models/PRODUCT.json), not as a scalar entry",
                         "drive", project, kind="table_reference"))
    if m.rotational_loss is not None:
        out.append(_item("PAR.MOTOR.rot_b", "Motor_RotLoss_b", m.rotational_loss.viscous_Nm_per_rad_s,
                         "N*m*s/rad", "viscous loss-equivalent torque coefficient", "drive", project))
    out += [_item("PAR.INV.Imax", "Inv_CurrentLimit", inv.current_limit_A_peak, "A",
                  "fundamental phase-peak current limit of THIS design (a physical design limit - not a software "
                  "saturation parameter and not an interface range)", "drive", project, kind="design_limit"),
            _item("PAR.INV.r_v", "Inv_VoltageReserve", inv.voltage.reserve_fraction, "1",
                  "voltage reserve fraction of the linear SVPWM budget (design reserve, not a numerical tolerance)",
                  "drive", project, kind="design_limit")]
    if inv.loss is not None:
        out += [_item("PAR.INV.loss_a0", "Inv_LossOffset", inv.loss.offset_W, "W", "quadratic surrogate offset",
                      "drive", project),
                _item("PAR.INV.loss_a2", "Inv_LossCoeff", inv.loss.ipk2_coeff_W_per_A2, "W/A^2",
                      "quadratic surrogate coefficient per phase-peak A^2", "drive", project)]
    out += [_item("PAR.DOMAIN.id", "Domain_Id", list(dom.id_A), "A", f"allowed id range ({dom.kind})", "drive",
                  project, kind="design_limit"),
            _item("PAR.DOMAIN.iq", "Domain_Iq", list(dom.iq_A), "A", f"allowed iq range ({dom.kind})", "drive",
                  project, kind="design_limit"),
            _item("PAR.DOMAIN.speed", "Domain_Speed", list(dom.speed_rpm), "rpm", "allowed mechanical speed range",
                  "drive", project, kind="design_limit")]
    dc = project.data("dc_source")
    out.append(_item("PAR.DC.Vdc_nominal", "DC_VdcNominal", dc["Vdc_nominal_V"], "V",
                     "nominal voltage at the inverter DC terminal", "dc_source", project))
    for k, v in dc["limits"].items():
        out.append(_item(f"PAR.DC.{k}", "DC_" + "".join(w.capitalize() for w in k.split("_")[:-1]), v,
                         "W" if k.endswith("_W") else "A",
                         "average DC limit at the inverter DC terminal; null = NOT DECLARED (never unlimited)",
                         "dc_source", project, kind="design_limit"))
    if project.has("controller"):
        c = project.data("controller")
        out += [_item("PAR.CTRL.fsw", "Ctrl_Fsw", c["fsw_kHz"] * 1e3, "Hz", "PWM carrier frequency", "controller",
                      project, kind="control_parameter"),
                _item("PAR.CTRL.deadtime", "Ctrl_DeadTime", c["deadtime_us"] * 1e-6, "s", "dead time", "controller",
                      project, kind="control_parameter")]
    return out


def candidates(project, requirement_cases: list) -> dict:
    ctrl = project.data("controller") if project.has("controller") else {}
    t = ctrl.get("timing") or {}
    fs = None if not ctrl else ctrl["fsw_kHz"] * 1e3 * (t.get("updates_per_period") or 1)
    lat = (None if not t else (t.get("sample_to_latch_us", 0.0) + t.get("filter_delay_us", 0.0)) * 1e-6)
    comps = [("CMP.DC_SOURCE", "DCSource", "HV DC source seen at the inverter DC terminal", "dc_source"),
             ("CMP.DC_LINK", "DCLinkCapacitor", "DC-link capacitor bank", "dc_link"),
             ("CMP.INVERTER", "TractionInverter", "single three-phase two-level VSI (+ power module)", "drive"),
             ("CMP.MOTOR", "TractionMotor", "PM synchronous machine (dq model)", "drive"),
             ("CMP.CONTROL", "MotorControl", "current control, modulation, torque path", "controller"),
             ("CMP.REDUCER", "Reducer", "single-speed reducer and torsional ROM", "driveline"),
             ("CMP.COOLING", "Cooling", "coolant loop and thermal networks", "thermal")]
    components = [{"id": i, "default_name": n, "role": r, "project_section": s,
                   "section_digest": project.sections[s].digest if project.has(s) else None,
                   "status": "exported" if project.has(s) else "section missing in the project"}
                  for i, n, r, s in comps]
    physical = [
        {"id": "CON.HV_DC", "domain": "electrical DC", "ends": ["CMP.DC_SOURCE", "CMP.DC_LINK", "CMP.INVERTER"],
         "across": "Vdc [V] at the inverter DC terminal", "through": "Idc [A] (average in this model)",
         "sign": "positive power flows from the DC source into the inverter (electrical -> mechanical positive)"},
        {"id": "CON.AC_3PH", "domain": "electrical three-phase", "ends": ["CMP.INVERTER", "CMP.MOTOR"],
         "across": "phase voltages; dq amplitude-invariant phase peak in the model",
         "through": "phase currents; dq phase peak", "sign": "Pac = 1.5 (vd id + vq iq) into the motor"},
        {"id": "CON.SHAFT", "domain": "rotational mechanical", "ends": ["CMP.MOTOR", "CMP.REDUCER"],
         "across": "omega_m [rad/s] mechanical", "through": "Tshaft [N*m]",
         "sign": "positive torque with positive speed = motoring"},
        {"id": "CON.HEAT", "domain": "thermal", "ends": ["CMP.INVERTER", "CMP.MOTOR", "CMP.COOLING"],
         "across": "temperature [K]", "through": "heat flow [W]", "sign": "loss heat into the coolant"}]
    signals = [
        {"id": "SIG.TORQUE_REQUEST", "from": "vehicle", "to": "CMP.CONTROL", "type": "double", "unit": "N*m",
         "quantity": "shaft torque request at the motor shaft", "rate": NOT_DECLARED, "latency": NOT_DECLARED,
         "range": NOT_DECLARED},
        {"id": "SIG.IDQ_REF", "from": "CMP.CONTROL", "to": "CMP.INVERTER", "type": "double", "dimension": [2],
         "unit": "A", "quantity": "dq current references (phase peak, amplitude-invariant)",
         "rate": NOT_DECLARED if fs is None else f"{fs:g} Hz (fsw x updates per period, controller section)",
         "latency": NOT_DECLARED, "range": "the allowed domain (PAR.DOMAIN.*) is a design limit, not this range"},
        {"id": "SIG.PHASE_CURRENT_MEAS", "from": "CMP.INVERTER", "to": "CMP.CONTROL", "type": "double",
         "dimension": [3], "unit": "A", "quantity": "sampled phase currents",
         "rate": NOT_DECLARED if fs is None else f"{fs:g} Hz",
         "latency": NOT_DECLARED if lat is None else f"{lat:g} s sample-to-latch + filter (declared estimate)",
         "validity": "held / predicted when the sample window is not edge-free (controller sensing)"},
        {"id": "SIG.SPEED_MEAS", "from": "CMP.MOTOR", "to": "CMP.CONTROL", "type": "double", "unit": "rpm",
         "quantity": "mechanical speed", "rate": NOT_DECLARED, "latency": NOT_DECLARED},
        {"id": "SIG.VDC_MEAS", "from": "CMP.DC_LINK", "to": "CMP.CONTROL", "type": "double", "unit": "V",
         "quantity": "DC-link voltage", "rate": NOT_DECLARED, "latency": NOT_DECLARED}]
    links = {}
    for c in requirement_cases:
        r = c["requirement"]
        key = f"{r['req_id']}@{r['revision']}"
        e = links.setdefault(key, {"requirement": key, "text": r["original_text"],
                                   "verified_at": "CON.SHAFT (motor shaft port)", "cases": [], "layer2_claims": []})
        e["cases"].append(c["case_id"])
        e["layer2_claims"].append({"case": c["case_id"], "status": c["layer2_claim"]["status"],
                                   "reasons": c["layer2_claim"]["reasons"]})
    return {"schema": ARCH_SCHEMA,
            "status": "candidate (generic): no company profile applied; the company architecture stays the source",
            "components": components, "physical_connections": physical, "signals": signals,
            "data_items": data_items(project), "requirement_links": list(links.values()),
            "rules": ["physical connections are not signal buses; signals carry type / unit / rate / latency",
                      "allocation and evidence links are not simulation wires",
                      "a target name comes from the profile's explicit id_map; a matching name alone is never the "
                      "same item (conflicts report unit / value / scope / owner differences)",
                      "the generator writes new models in a new folder; it never deletes or overwrites a company "
                      "element, and an ID missing from a later package is not a delete command"]}


def profile_template(arch: dict) -> dict:
    ids = [c["id"] for c in arch["components"]] + [s["id"] for s in arch["signals"]] + \
        [i["id"] for i in arch["data_items"]]
    return {"schema": PROFILE_SCHEMA, "profile_id": "generic", "company": None,
            "naming": {"data_item_prefix": "", "note": "e.g. 'Sys_' - applied to default names only"},
            "stereotypes": {}, "namespaces": {},
            "dictionary": {"file": None, "section": "Design Data"},
            "id_map": [{"id": i, "target": None} for i in ids],
            "notes": ["fill id_map with the company element name of each stable ID; keep this file in the company "
                      "environment (it is not part of the exported package identity)"]}
