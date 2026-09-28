"""The transfer contract: what a MathWorks port must preserve, in a form MATLAB reads without interpretation.

``contract_document()`` is package-wide (conventions as checkable enumerations, the quantity dictionary with units
and tolerance classes, the status vocabularies, numerical settings, the map and loss-ownership rules, the three
layers).  ``model_document()`` is one drive model as data: every parameter with its unit in the key, the validity
and interpolation rules, the loss closures and their ownership, and the content identity of the Python model it was
written from.  Nothing is rounded, nothing missing is filled in: an undeclared closure is null and stays undefined
on the target.
"""

from __future__ import annotations

import math

from ..exchange import conventions as exchange_conventions
from ..identity import content_sha256
from ..models.components import LOSS_MODULE, LOSS_QUADRATIC, DriveModel
from ..models.flux import ConstantFluxModel
from ..models.provenance import FIDELITY_ALLOWED_CLAIMS
from ..settings import DEFAULT_SETTINGS
from ..status import Reason

CONTRACT_SCHEMA = "twb-mathworks-contract/1"
MODEL_SCHEMA = "twb-mathworks-model/1"

# checked verbatim by the MATLAB loader: a package with another convention is refused, never re-interpreted
CONVENTIONS = {
    "park": "amplitude_invariant",
    "dq_quantities": "phase_peak",
    "d_axis": "d_on_PM",
    "speed_input": "mechanical_rpm",
    "electrical_speed": "omega_e = pole_pairs * omega_m",
    "pole_count": "pole_pairs",
    "power_sign": "electrical_to_mechanical_positive",
    "torque": "Te = 1.5*p*(psi_d*iq - psi_q*id) electromagnetic; Tshaft = Te - tau_rot(omega_m) at the motor shaft",
    "ac_power": "Pac = 1.5*(vd*id + vq*iq) at the motor terminals",
    "rms": "i_phase_rms = i_peak/sqrt(2) (balanced fundamental; equivalent sinusoidal RMS at standstill)",
    "line_voltage": "v_LL_rms = sqrt(3/2)*v_phase_peak",
    "voltage_limit": "|v_cmd| <= scale*(1 - r_v)*Vdc/sqrt(3) (linear SVPWM, phase peak); r_v is a design reserve, "
                     "never a numerical tolerance",
    "undefined": "null / NaN = not defined by the model (never zero); a missing DC limit is not unlimited",
}

# quantity -> (unit, tolerance class, meaning); the forward outputs of every port
QUANTITIES = {
    "omega_m_rad_s": ("rad/s", "rad_s", "mechanical angular speed 2*pi*n/60"),
    "omega_e_rad_s": ("rad/s", "rad_s", "electrical angular speed p*omega_m"),
    "f_e_Hz": ("Hz", "Hz", "electrical fundamental frequency |omega_e|/(2*pi)"),
    "psi_d_Wb": ("Wb", "Wb", "d-axis flux linkage (phase peak)"),
    "psi_q_Wb": ("Wb", "Wb", "q-axis flux linkage (phase peak)"),
    "vd_V": ("V", "V", "d-axis motor voltage (phase peak)"),
    "vq_V": ("V", "V", "q-axis motor voltage (phase peak)"),
    "v_peak_V": ("V", "V", "motor phase voltage amplitude hypot(vd, vq)"),
    "v_LL_rms_V": ("V", "V", "line-to-line RMS voltage sqrt(3/2)*v_peak"),
    "v_cmd_peak_V": ("V", "V", "command voltage amplitude incl. the declared inverter voltage error"),
    "i_peak_A": ("A", "A", "phase current amplitude sqrt(id^2 + iq^2)"),
    "i_phase_rms_A": ("A", "A", "phase current RMS i_peak/sqrt(2)"),
    "Te_Nm": ("N*m", "Nm", "electromagnetic torque"),
    "tau_rot_Nm": ("N*m", "Nm", "rotational loss-equivalent torque (null = not declared)"),
    "Tshaft_Nm": ("N*m", "Nm", "shaft torque Te - tau_rot (null when tau_rot is not declared)"),
    "Pshaft_W": ("W", "W", "shaft power Tshaft*omega_m"),
    "Pcu_W": ("W", "W", "fundamental copper loss 1.5*Rs*|i|^2"),
    "Prot_W": ("W", "W", "rotational loss omega_m*tau_rot"),
    "Pac_W": ("W", "W", "motor terminal AC power"),
    "Pinv_W": ("W", "W", "inverter loss of the declared model (null = not declared)"),
    "Pdc_W": ("W", "W", "inverter DC terminal power Pac + Pinv"),
    "Idc_A": ("A", "A", "average DC current Pdc/Vdc"),
    "voltage_budget_V": ("V", "V", "command voltage budget scale*(1 - r_v)*Vdc/sqrt(3)"),
    "voltage_ceiling_V": ("V", "V", "hardware ceiling Vdc/sqrt(3)"),
    "voltage_margin_V": ("V", "V", "budget - v_cmd (the reserve is already subtracted)"),
    "efficiency": ("1", "ratio", "Pshaft/Pdc motoring, |Pdc|/|Pshaft| regenerating, null otherwise"),
    "Rs_ohm_used": ("Ohm", "ohm", "per-phase resistance after the declared temperature law"),
    "psi_pm_Wb_used": ("Wb", "Wb", "PM flux linkage after the declared temperature law (constant dq)"),
    "pwm_ratio": ("1", "ratio", "switching frequency / electrical frequency (reported only)"),
}

# |q_target - q_reference| <= atol + rtol*max(|q_target|, |q_reference|)
PARITY_TOLERANCE = {
    "basis": "IEEE-754 double in both tools and the same closed-form operation sequence: differences are a few ulp "
             "of the largest intermediate; the absolute floors cover quantities that cancel to ~0 (standstill "
             "power, vd at zero current).  A parity tolerance is not a requirement margin and never widens one.",
    "classes": {"V": [1e-9, 1e-12], "A": [1e-9, 1e-12], "Nm": [1e-9, 1e-12], "W": [1e-6, 1e-12],
                "Wb": [1e-15, 1e-12], "rad_s": [1e-9, 1e-12], "Hz": [1e-9, 1e-12], "ohm": [1e-15, 1e-12],
                "ratio": [1e-12, 1e-12]},
}
ORACLE_TOLERANCE = {
    "golden": {"atol": 1e-10, "rtol": 1e-10,
               "basis": "reference package acceptance: |x - x_ref| / max(1, |x_ref|) <= 1e-10"},
    "closed_form": {"atol_scale": 10.0, "rtol": 1e-11,
                    "basis": "contract equations re-associated: the parity class atol x 10 and rtol 1e-11"},
}

CONSTRAINT_STATES = ("SATISFIED", "ACTIVE", "VIOLATED", "NOT_EVALUATED")
ENERGY_MODES = ("MOTORING", "REGENERATING", "STANDSTILL", "ZERO_SHAFT_POWER", "BRAKING_WITHOUT_NET_DC_RECOVERY",
                "ACCOUNTING_INCONSISTENCY", "UNDETERMINED")
SETTINGS_KEYS = ("constraint_rel_tol", "voltage_abs_tol_V", "current_abs_tol_A", "power_abs_tol_W",
                 "speed_abs_tol_rpm", "power_zero_tol_W", "speed_zero_tol_rad_s", "torque_residual_abs_Nm",
                 "torque_residual_rel", "power_identity_abs_W", "power_identity_rel")


def settings_block() -> dict:
    from ..physics import TEMP_MATCH_TOL_C
    s = DEFAULT_SETTINGS.to_dict()
    if s.get("torque_scale_Nm") is not None:
        raise ValueError("a declared torque scale is not part of this contract revision")
    return {**{k: s[k] for k in SETTINGS_KEYS}, "temperature_match_tol_C": TEMP_MATCH_TOL_C}


def contract_document() -> dict:
    ex = exchange_conventions()
    return {
        "schema": CONTRACT_SCHEMA,
        "conventions": CONVENTIONS,
        "conventions_text": {k: ex[k] for k in ("power_ports", "loss_ownership", "claims")},
        "quantities": {k: {"unit": u, "class": c, "meaning": m} for k, (u, c, m) in QUANTITIES.items()},
        "parity_tolerance": PARITY_TOLERANCE,
        "oracle_tolerance": ORACLE_TOLERANCE,
        "numerical_settings": settings_block(),
        "vocabulary": {
            "constraint_states": list(CONSTRAINT_STATES),
            "constraint_rule": "slack = limit - demand (upper) or demand - limit (lower); tol = max(abs_floor, "
                               "constraint_rel_tol*|limit|); slack < -tol VIOLATED, |slack| <= tol ACTIVE (on the "
                               "boundary, a pass), else SATISFIED; undefined demand NOT_EVALUATED (not a pass)",
            "energy_modes": list(ENERGY_MODES),
            "reasons": [r.value for r in Reason],
            "claims": ["FEASIBLE", "INFEASIBLE", "UNKNOWN"],
            "claim_rule": "a single evaluated point is evidence (accepted) or a diagnostic; it is never an "
                          "INFEASIBLE proof - infeasibility needs an exclusion over the whole allowed domain",
        },
        "forward_evaluation": {
            "semantics": "the point is evaluated as given and never moved; outside the model's covered/valid domain "
                         "it is not evaluable (UNKNOWN, OUTSIDE_MODEL_DOMAIN) - no extrapolation or clipping",
            "accepted": "model valid for the scenario (no issue) AND every constraint of the groups VOLTAGE, "
                        "CURRENT, DOMAIN, DISCHARGE_SOURCE, CHARGE_SOURCE evaluated and not VIOLATED AND the DC "
                        "power defined AND every DC limit that can bind at this sign of P_dc declared AND the "
                        "power identities close AND no negative passive loss / accounting inconsistency",
            "identity_tolerance": "max(power_identity_abs_W, power_identity_rel * max(|Pac|, |Pcu|, |Pdc|, "
                                  "|Pshaft|, |Te*omega_m|))",
            "torque_tolerance": "max(torque_residual_abs_Nm, torque_residual_rel * torque_scale); torque_scale = "
                                "max(1, 1.5 p (psi_pm Imax + |Ld - Lq| Imax^2/2)) for constant dq, max(1, "
                                "max |Te| over the valid nodes of the plane) for a map",
        },
        "map_rules": [
            "array psi[i][j]: i indexes the id axis, j the iq axis (row = id).  jsondecode gives M(i_id, j_iq); "
            "the loader checks size(M) == [numel(id_axis), numel(iq_axis)] and never transposes",
            "bilinear inside valid cells only (a cell is valid when its four nodes are valid); a point on a cell "
            "edge is covered when any cell containing it is valid; everything else is NOT evaluable",
            "no extrapolation and no clipping: the n-D Lookup Table default (linear extrapolation) and 'Clip' both "
            "differ from this contract; a lookup block needs an explicit validity check that returns UNKNOWN",
            "a bilinear map stays bilinear: replacing it by a spline or smoothing is another model",
            "temperature dependence of a map comes from its planes only; a native temperature coefficient on top "
            "of the planes is a double correction (refused)",
            "declared q-odd symmetry is applied at export: the exported planes are the full planes the reference "
            "evaluates (source plane hashes are kept)",
            "static use only: the bilinear interpolant's Jacobian is discontinuous across cell edges; dynamic "
            "(current-state) use is NOT QUALIFIED",
        ],
        "layers": {
            "layer1_requirement_and_accepted_source": "requirements, scenarios, the accepted reference package and the "
                                                 "contract equations; the oracle values in the cases come from here",
            "layer2_python_reference": "this package's 'python' values: the executable reference implementation at the "
                                  "identified source revision",
            "layer3_mathworks": "native MATLAB / Simulink / System Composer artefacts, checked against 2 (parity) and 1 "
                           "(oracle); layer 2 is not absolute truth - a layer-2 / layer-1 disagreement is reported",
        },
        "verification_level": "implementation verification (V0-V4): parity and oracle agreement do not qualify the "
                              "physics; hardware / bench / vehicle evidence is separate (V5-V6)",
    }


def _loss(inv) -> dict:
    if inv.loss_kind == LOSS_QUADRATIC:
        l = inv.loss
        return {"kind": "quadratic_surrogate", "offset_W": l.offset_W, "coeff_W_per_A2": l.ipk2_coeff_W_per_A2,
                "current_basis": "fundamental phase peak", "valid_Vdc_V": None if l.valid_Vdc_V is None
                else list(l.valid_Vdc_V), "symmetric_motoring_regen": True, "surrogate_kind": l.kind,
                "native": True}
    if inv.loss_kind == LOSS_MODULE:
        return {"kind": "datasheet_module", "native": False,
                "note": "the datasheet module loss model is evaluated point by point in the reference; it is not "
                        "ported: DC quantities are UNSUPPORTED on the target"}
    return {"kind": "none", "native": True, "note": "no inverter loss model declared: DC power undefined"}


def _temperature(dep) -> dict | None:
    return None if dep is None else {"coeff_per_K": dep.coeff_per_K, "valid_C": list(dep.valid_C),
                                     "basis": dep.basis}


def _nodes(arr) -> list:
    """2-D array -> nested lists, rows = id axis; non-finite (only allowed at invalid nodes) -> null."""
    return [[float(v) if math.isfinite(v) else None for v in row] for row in arr.tolist()]


def _flux(motor) -> dict:
    f = motor.flux
    if isinstance(f, ConstantFluxModel):
        return {"kind": "constant_dq", "psi_pm_Wb": f.psi_pm_Wb, "Ld_H": f.Ld_H, "Lq_H": f.Lq_H,
                "validity": None if f.validity is None else {"id_A": list(f.validity.id_A),
                                                             "iq_A": list(f.validity.iq_A)},
                "reference_magnet_temp_C": motor.reference_magnet_temp_C,
                "psi_temperature": _temperature(motor.psi_temperature)}
    planes = [{"magnet_temp_C": p.magnet_temp_C, "label": p.label,
               "id_axis_A": [float(x) for x in p.id_axis_A], "iq_axis_A": [float(x) for x in p.iq_axis_A],
               "psi_d_Wb": _nodes(p.psi_d_Wb), "psi_q_Wb": _nodes(p.psi_q_Wb),
               "valid": [[bool(v) for v in row] for row in p.valid.tolist()], "data_sha256": p.data_sha256,
               "magnetic_qualification": _qualification(p)} for p in f.planes]
    return {"kind": "flux_map",
            "array_order": "psi[i][j]: i = id axis index (row), j = iq axis index (column)",
            "interpolation": "bilinear inside valid cells; edge covered if any adjacent cell is valid; "
                             "no extrapolation",
            "symmetry": {"declared": f.symmetry, "applied_at_export": f.symmetry is not None,
                         "source_planes_sha256": [p.data_sha256 for p in f.source_planes]},
            "temperature_interpolation": f.temperature_interpolation,
            "temperature_interpolation_basis": f.temperature_interpolation_basis,
            "temperature_match_tol_C": f.temperature_match_tol_C,
            "reference_magnet_temp_C": motor.reference_magnet_temp_C,
            "psi_temperature": None,
            "planes": planes}


def _qualification(plane) -> dict:
    q = plane.magnetic_qualification()
    return {"static_use": q["static_use"]["status"], "dynamic_use": q["dynamic_use"]["status"]}


def loss_ownership(drive: DriveModel) -> list:
    """Which model owns each loss term, so a target block that brings its own losses is not summed twice."""
    rot = drive.motor.rotational_loss
    loss = _loss(drive.inverter)
    return [
        {"term": "copper_fundamental", "owner": "motor dq voltage equations (Rs)", "formula": "1.5*Rs*(id^2+iq^2)",
         "inside": "Pac"},
        {"term": "rotational", "owner": "rotational loss-equivalent torque at the shaft" if rot else "not declared",
         "formula": "tau_rot = b*omega_m + c*omega_m*|omega_m|" if rot else None, "inside": "Te - Tshaft"},
        {"term": "iron", "owner": ("included in the rotational loss-equivalent torque" if rot is not None
                                   and rot.includes_iron_loss else "not modelled"), "formula": None, "inside": None},
        {"term": "inverter", "owner": {"quadratic_surrogate": "inverter quadratic surrogate at the DC side",
                                       "datasheet_module": "datasheet module model (not ported)",
                                       "none": "not declared"}[loss["kind"]],
         "formula": "offset + coeff*|i|^2" if loss["kind"] == "quadratic_surrogate" else None,
         "inside": "Pdc - Pac"},
        {"term": "harmonic_pwm", "owner": "not modelled in the fundamental model", "formula": None, "inside": None},
    ]


def model_document(drive: DriveModel, key: str, role: str, purpose: str, extra_identity: dict | None = None) -> dict:
    m, inv, dom = drive.motor, drive.inverter, drive.domain
    rot = m.rotational_loss
    v = inv.voltage
    return {
        "schema": MODEL_SCHEMA,
        "model_key": key,
        "role": role,
        "purpose": purpose,
        "family": m.flux.kind,
        "fidelity": m.fidelity.value,
        "fidelity_allows": FIDELITY_ALLOWED_CLAIMS[m.fidelity],
        "identity": {"drive_id": drive.drive_id, "revision": drive.revision,
                     "content_sha256": content_sha256(drive), "provenance": drive.provenance.to_dict(),
                     "notes": list(drive.notes), **(extra_identity or {})},
        "conventions": CONVENTIONS,
        "motor": {"motor_id": m.motor_id, "pole_pairs": m.pole_pairs, "connection": m.connection,
                  "Rs_ohm": m.Rs_ohm, "reference_winding_temp_C": m.reference_winding_temp_C,
                  "rs_temperature": _temperature(m.rs_temperature),
                  "rotational_loss": None if rot is None else {
                      "b_Nm_per_rad_s": rot.viscous_Nm_per_rad_s, "c_Nm_per_rad2_s2": rot.quadratic_Nm_per_rad2_s2,
                      "includes_iron_loss": rot.includes_iron_loss, "basis": rot.basis},
                  "flux": _flux(m)},
        "inverter": {"inverter_id": inv.inverter_id, "topology": "single three-phase two-level VSI",
                     "current_limit_A_peak": inv.current_limit_A_peak,
                     "current_limit_basis": inv.current_limit_basis,
                     "voltage": {"reserve_fraction": v.reserve_fraction, "modulation": v.modulation,
                                 "voltage_error": v.voltage_error, "resistive_drop_ohm": v.resistive_drop_ohm,
                                 "diagnostic_budget_scale": v.diagnostic_budget_scale},
                     "loss": _loss(inv),
                     "switching_frequency_context_Hz": inv.switching_frequency_context_Hz},
        "domain": {"id_A": list(dom.id_A), "iq_A": list(dom.iq_A), "speed_rpm": list(dom.speed_rpm),
                   "kind": dom.kind, "interpretation": dom.interpretation},
        "loss_ownership": loss_ownership(drive),
    }
