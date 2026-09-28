"""Safe-reaction candidate screening: ASC vs freewheel (6SO), steady state only.

For (speed, Vdc, PM flux, HV state) each candidate is screened for

* back-EMF / uncontrolled rectification: open-inverter line-to-line back-EMF
  peak sqrt(3)*omega_e*psi(0,0) vs the DC-link voltage;
* steady-state ASC short-circuit current and braking torque from the same dq
  model with v_d = v_q = 0 (constant model in closed form, flux maps solved
  numerically inside the covered data);
* DC overvoltage risk: freewheel above the back-EMF onset charges the link;
  with the battery disconnected there is no sink;
* component stress proxies: steady-state current vs the fundamental current
  limit, back-EMF vs the declared device voltage rating;
* transition time and hardware-path availability as *declared* inputs.

Project/customer rules (e.g. "below 60 V HVDC use a specific freewheel path")
are evaluated in a separate layer and labelled as rules, not physics.

Not evaluated (UNKNOWN by construction): ASC entry transient peak current and
torque, UCG current magnitude (needs a rectifier model), device SOA, gate-drive
supply behaviour, fault detection, and any functional-safety approval.  The
screening never selects a safe state on its own.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import fsolve

from ..errors import InputValidationError
from ..models.components import DriveModel
from ..models.flux import ConstantFluxModel
from ..validation import finite as _finite
from ..physics import DriveKernel
from ..scenario import DcSourceLimits, Scenario
from ..status import Claim, Evidence, EvidenceKind, Reason, Status
from .dclink import back_emf_ll_peak, speed_for_back_emf

SUPPORTED_RULE_KEYS = ("Vdc_below_V", "Vdc_above_V", "speed_above_rpm", "speed_below_rpm", "hv_state")


def asc_steady_state(drive: DriveModel, speed_rpm: float, Vdc_V: float = 1.0, magnet_temp_C: float | None = None,
                     winding_temp_C: float | None = None) -> dict:
    """Steady-state active-short-circuit currents (v_d = v_q = 0) at the stated temperatures.

    A model that is not valid at these temperatures (e.g. a multi-plane flux map without a magnet
    temperature) gives an explicit ``evaluable: False`` with the reason - never an exception.
    """
    sc = Scenario("asc", speed_rpm, Vdc_V, DcSourceLimits(), magnet_temp_C=magnet_temp_C,
                  winding_temp_C=winding_temp_C)
    k = DriveKernel(drive, sc)
    if not k.evaluable or k.issues:
        return {"evaluable": False,
                "reason": "; ".join(i.message for i in k.issues) or "model not evaluable at this scenario",
                "reason_code": (k.issues[0].reason.value if k.issues else "OUTSIDE_MODEL_DOMAIN")}
    we, rs = k.omega_e, k.Rs
    if k.kind == "constant_dq":
        psi, ld, lq = k.psi, k.Ld, k.Lq
        den = rs * rs + we * we * ld * lq
        if den == 0:
            return {"evaluable": False, "reason": "standstill without resistance: ASC current undefined"}
        idv = -we * we * lq * psi / den
        iqv = -we * rs * psi / den
        method = "closed form (constant-parameter model)"
    else:
        def f(x):
            psd, psq, ok = k.flux(x[0], x[1])
            if not bool(ok):
                return [1e3, 1e3]
            return [rs * x[0] - we * float(psq), rs * x[1] + we * float(psd)]
        guess = [-0.9 * min(abs(k.plane.id_axis_A[0]), 1e9), 0.0]
        sol, info, ier, msg = fsolve(f, guess, full_output=True, xtol=1e-12)
        idv, iqv = float(sol[0]), float(sol[1])
        psd, psq, ok = k.flux(idv, iqv)
        if ier != 1 or not bool(ok) or max(abs(v) for v in f(sol)) > 1e-6 * max(1.0, k.Imax):
            return {"evaluable": False, "reason": "ASC operating point not found inside the covered flux-map data "
                                                  "(no extrapolation)"}
        method = "numerical solve of v = 0 inside the covered flux-map data"
    ev = k.evaluate(idv, iqv)
    tem = float(ev["tem"])
    tsh = tem - k.tau_rot_or_zero
    ipk = math.hypot(idv, iqv)
    return {"evaluable": True, "method": method, "id_A": idv, "iq_A": iqv, "i_peak_A": ipk,
            "i_phase_rms_A": ipk / math.sqrt(2), "Te_Nm": tem, "Tshaft_Nm": tsh,
            "copper_loss_W": 1.5 * rs * ipk * ipk,
            "braking": (tem * k.omega_m) < 0 if k.omega_m != 0 else False,
            "dc_link_power_W": 0.0,
            "current_limit_A": k.Imax,
            "note": "steady state only: the ASC entry transient can exceed these values"}


def _rule_applies(rule: dict, speed_rpm: float, Vdc_V: float, hv_state: str) -> bool:
    when = rule.get("when", {})
    for key, v in when.items():
        if key not in SUPPORTED_RULE_KEYS:
            raise InputValidationError(f"unsupported rule condition {key!r}", field=rule.get("rule_id", "rule"))
        if key == "Vdc_below_V" and not Vdc_V < v:
            return False
        if key == "Vdc_above_V" and not Vdc_V > v:
            return False
        if key == "speed_above_rpm" and not abs(speed_rpm) > v:
            return False
        if key == "speed_below_rpm" and not abs(speed_rpm) < v:
            return False
        if key == "hv_state" and hv_state != v:
            return False
    return True


def safe_state_screening(drive: DriveModel, speed_rpm: float, Vdc_V: float, hv_state: str = "battery_connected",
                         device_voltage_rating_V: float | None = None, dc_link_limit_V: float | None = None,
                         hardware_paths: dict | None = None, transition_times_s: dict | None = None,
                         project_rules: list | None = None, magnet_temp_C: float | None = None,
                         winding_temp_C: float | None = None) -> dict:
    n = _finite("speed_rpm", speed_rpm)
    vdc = _finite("Vdc_V", Vdc_V)
    if vdc <= 0:
        raise InputValidationError("Vdc must be > 0", field="Vdc_V")
    if hv_state not in ("battery_connected", "battery_disconnected"):
        raise InputValidationError("hv_state must be 'battery_connected' or 'battery_disconnected'", field="hv_state")
    hardware_paths = hardware_paths or {}
    transition_times_s = transition_times_s or {}
    vll = back_emf_ll_peak(drive, n, magnet_temp_C)
    onset = speed_for_back_emf(drive, vdc, magnet_temp_C)
    asc = asc_steady_state(drive, n, vdc, magnet_temp_C, winding_temp_C)
    rows = []
    # --- freewheel / 6SO -------------------------------------------------
    fw = {"candidate": "FREEWHEEL (6SO)", "back_emf_ll_peak_V": vll, "ucg_onset_speed_rpm": onset}
    if vll is None:
        fw["back_emf_risk"] = "UNKNOWN (flux at zero current not available at the stated magnet temperature)"
        fw["braking_torque"] = "UNKNOWN"
        fw["dc_overvoltage_risk"] = "UNKNOWN"
    elif vll <= vdc:
        fw["back_emf_risk"] = f"below the DC link ({vll:.4g} V <= {vdc:g} V): no rectified current in steady state"
        fw["braking_torque"] = "~0 (no conduction; rotational loss only)"
        fw["dc_overvoltage_risk"] = "none from back-EMF in steady state"
    else:
        fw["back_emf_risk"] = (f"UNCONTROLLED RECTIFICATION: {vll:.4g} V line-line peak > {vdc:g} V "
                               f"(onset above {onset:.5g} rpm)")
        fw["braking_torque"] = "UNKNOWN magnitude (needs a rectifier model); uncontrolled regenerative braking"
        fw["dc_overvoltage_risk"] = ("HIGH: link isolated, rectified energy has no sink"
                                     if hv_state == "battery_disconnected" else
                                     "charging current into the battery is uncontrolled - check charge limits")
    if device_voltage_rating_V is not None and vll is not None:
        fw["device_voltage_stress"] = ("back-EMF exceeds the device rating" if vll > device_voltage_rating_V
                                       else f"back-EMF {vll:.4g} V below the device rating {device_voltage_rating_V:g} V")
    if dc_link_limit_V is not None and vll is not None and hv_state == "battery_disconnected":
        fw["dc_link_limit_check"] = ("link can be driven above the limit" if vll > dc_link_limit_V
                                     else "back-EMF below the link limit")
    rows.append(fw)
    # --- ASC -------------------------------------------------------------
    a = {"candidate": "ASC (active short circuit)"}
    if not asc.get("evaluable"):
        a.update({"steady_state": "UNKNOWN", "reason": asc.get("reason")})
    else:
        a.update({
            "steady_state_current_A_peak": asc["i_peak_A"],
            "steady_state_braking_torque_Nm": asc["Tshaft_Nm"],
            "current_vs_limit": ("above the fundamental current limit" if asc["i_peak_A"] > asc["current_limit_A"]
                                 else f"{asc['i_peak_A']:.4g} A <= {asc['current_limit_A']:g} A (steady state)"),
            "dc_link_power_W": 0.0,
            "dc_overvoltage_risk": "no steady-state DC power flow (ideal switches)",
            "back_emf_risk": "terminals shorted: no back-EMF on the link in steady state",
            "transient": "UNKNOWN: entry transient peak current/torque not evaluated",
        })
    rows.append(a)
    for r in rows:
        key = "ASC" if r["candidate"].startswith("ASC") else "FREEWHEEL"
        r["hardware_path_available"] = hardware_paths.get(key, "UNKNOWN (not declared)")
        r["transition_time_s"] = transition_times_s.get(key, "UNKNOWN (not declared)")
    rules = []
    for rule in project_rules or []:
        if not rule.get("rule_id") or not rule.get("basis"):
            raise InputValidationError("project rules need rule_id and basis", field="project_rules")
        applies = _rule_applies(rule, n, vdc, hv_state)
        rules.append({"rule_id": rule["rule_id"], "applies": applies, "require": rule.get("require"),
                      "forbid": rule.get("forbid"), "basis": rule["basis"],
                      "kind": "project/customer rule (not physics)"})
    claim = Claim("safe_state_selection", Status.UNKNOWN,
                  f"safe-reaction candidate at {n:g} rpm, Vdc = {vdc:g} V, {hv_state}",
                  "steady-state fundamental screening only",
                  reasons=(Reason.OUT_OF_SCOPE,),
                  evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND, "back-EMF and steady-state ASC evaluated"),),
                  detail="transients, UCG current magnitude, SOA, detection and FuSa approval are not evaluated; "
                         "the screening informs, it does not select a safe state")
    return {"speed_rpm": n, "Vdc_V": vdc, "hv_state": hv_state, "candidates": rows, "project_rules": rules,
            "claim": claim.to_dict(),
            "asc_detail": asc,
            "notes": ["low speed: ASC gives large braking torque; high speed: ASC current tends to psi/Ld with small "
                      "torque while freewheel risks uncontrolled rectification",
                      "controlled zero torque or normal high-speed operation is not the same as 6SO safety"]}
