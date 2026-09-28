"""Reference cases of the transfer package: each case carries the layer-2 (Python) values and, where the accepted
source defines them, layer-1 (oracle) values, so a port is compared with both and Python is not the only truth.

Case kinds:

* ``forward``    - one (id, iq) point at one scenario through the whole forward evaluation (quantities, constraint
                   states, energy mode, identities, the evidence gate);
* ``flux_lookup`` - one flux-map query (plane selection by magnet temperature, coverage, bilinear value);
* ``requirement_witness`` - a requirement's witness point re-checked against the ORIGINAL request (target torque /
                   band, every limit, DC side, validity).  A port can reproduce a FEASIBLE claim this way; INFEASIBLE
                   and UNKNOWN claims rest on Python evidence (exclusion proofs, certificates, ratings) that the
                   first port does not re-derive - the case says so instead of pretending.

The models the cases run on are the project's product drive and verification fixtures: the reference package's
drives and small synthetic maps built to catch transposition, hole, edge, temperature-plane and symmetry errors.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .. import spec_fixtures as sf
from ..errors import OutsideModelDomain
from ..models import (DataOrigin, DriveModel, Fidelity, FluxMapModel, FluxMapPlane, InverterLossModel, InverterModel,
                      MotorModel, OperatingDomain, Provenance, RotationalLossModel, TemperatureDependence, VoltageModel)
from ..physics import DriveKernel, forward_evaluation
from ..scenario import DcSourceLimits, Scenario
from ..solvers.gate import check_witness, torque_tolerance
from . import oracle as O
from .contract import ORACLE_TOLERANCE, PARITY_TOLERANCE, QUANTITIES, model_document, settings_block

CASES_SCHEMA = "twb-mathworks-cases/1"
LIMIT_KEYS = ("discharge_power_max_W", "charge_power_max_W", "discharge_current_max_A", "charge_current_max_A")
VF_PROVENANCE = Provenance(DataOrigin.SYNTHETIC, "traction_workbench.mathworks verification fixture", "1",
                           "synthetic verification only - no physical claim")


# -- scenarios ------------------------------------------------------------------------------------------------


def limits_spec(lim: DcSourceLimits) -> dict:
    """null = not declared, 'unlimited' = declared unlimited, else a finite value - three states, never merged."""
    out = {}
    for k in LIMIT_KEYS:
        v = getattr(lim, k)
        out[k] = ({"state": "not_declared", "value": None} if v is None else
                  {"state": "unlimited", "value": None} if math.isinf(v) else {"state": "finite", "value": float(v)})
    return out


def _limits_from(spec: dict) -> DcSourceLimits:
    vals = {k: (None if spec[k]["state"] == "not_declared" else math.inf if spec[k]["state"] == "unlimited"
                else spec[k]["value"]) for k in LIMIT_KEYS}
    return DcSourceLimits(**vals, source="transfer package case")


def scenario_spec(speed_rpm, Vdc_V, limits: DcSourceLimits, winding_temp_C=None, magnet_temp_C=None,
                  switching_frequency_Hz=None) -> dict:
    return {"speed_rpm": float(speed_rpm), "Vdc_V": float(Vdc_V), "winding_temp_C": winding_temp_C,
            "magnet_temp_C": magnet_temp_C, "switching_frequency_Hz": switching_frequency_Hz,
            "limits": limits_spec(limits)}


def scenario_of(case_id: str, spec: dict) -> Scenario:
    return Scenario(case_id, spec["speed_rpm"], spec["Vdc_V"], _limits_from(spec["limits"]),
                    winding_temp_C=spec["winding_temp_C"], magnet_temp_C=spec["magnet_temp_C"],
                    switching_frequency_Hz=spec["switching_frequency_Hz"])


# -- layer 2: the Python reference ----------------------------------------------------------------------------


def _f(x):
    return None if x is None else float(x)


def forward_block(drive: DriveModel, sc: Scenario, id_a: float, iq_a: float) -> dict:
    res = forward_evaluation(drive, sc, id_a, iq_a)
    k = DriveKernel(drive, sc)
    gate = check_witness(k, id_a, iq_a, point=res.point, require_dc=True)
    out = {"evaluable": res.evaluable, "reason": None if res.reason is None else res.reason.value,
           "issues": [i.reason.value for i in res.issues], "accepted": bool(res.accepted),
           "gate_reasons": [r.value for r in gate.reasons]}
    pt = res.point
    if pt is None:
        out.update({q: None for q in QUANTITIES}, constraints={}, violated_groups=[], energy_mode=None,
                   identities_ok=None)
        return out
    out.update({
        "omega_m_rad_s": pt.omega_m, "omega_e_rad_s": pt.omega_e, "f_e_Hz": pt.f_e_Hz, "psi_d_Wb": pt.psi_d_Wb,
        "psi_q_Wb": pt.psi_q_Wb, "vd_V": pt.vd_V, "vq_V": pt.vq_V, "v_peak_V": pt.v_peak_V,
        "v_LL_rms_V": pt.v_LL_rms_V, "v_cmd_peak_V": pt.v_cmd_peak_V, "i_peak_A": pt.i_peak_A,
        "i_phase_rms_A": pt.i_phase_rms_A, "Te_Nm": pt.Te_Nm, "tau_rot_Nm": _f(pt.tau_rot_Nm),
        "Tshaft_Nm": _f(pt.Tshaft_Nm), "Pshaft_W": _f(pt.Pshaft_W), "Pcu_W": pt.Pcu_W, "Prot_W": _f(pt.Prot_W),
        "Pac_W": pt.Pac_W, "Pinv_W": _f(pt.Pinv_W), "Pdc_W": _f(pt.Pdc_W), "Idc_A": _f(pt.Idc_A),
        "voltage_budget_V": pt.voltage_budget_V, "voltage_ceiling_V": pt.voltage_ceiling_V,
        "voltage_margin_V": pt.voltage_margin_V, "efficiency": _f(pt.efficiency), "Rs_ohm_used": k.Rs,
        "psi_pm_Wb_used": _f(k.psi), "pwm_ratio": _f(pt.pwm_ratio),
        "constraints": {c.name: c.state for c in pt.constraints}, "violated_groups": pt.violated_groups(),
        "energy_mode": pt.energy_mode, "identities_ok": bool(pt.identities_ok)})
    return {key: (float(v) if isinstance(v, (float, np.floating)) else v) for key, v in out.items()}


def lookup_block(flux: FluxMapModel, magnet_temp_C, id_a: float, iq_a: float) -> dict:
    try:
        plane, _ = flux.plane_for(magnet_temp_C)
    except OutsideModelDomain as exc:
        return {"covered": False, "psi_d_Wb": None, "psi_q_Wb": None,
                "reason": "MISSING_INPUT" if exc.detail.get("reason") == "MISSING_INPUT" else "OUTSIDE_MODEL_DOMAIN"}
    psd, psq, ok = plane.interpolate(id_a, iq_a)
    ok = bool(ok)
    return {"covered": ok, "psi_d_Wb": float(psd) if ok else None, "psi_q_Wb": float(psq) if ok else None,
            "reason": None if ok else "OUTSIDE_MODEL_DOMAIN"}


def witness_block(drive: DriveModel, sc: Scenario, id_a: float, iq_a: float, T_request=None, band=None) -> dict:
    k = DriveKernel(drive, sc)
    chk = check_witness(k, id_a, iq_a, T_request=T_request, band=band, require_dc=True)
    pt = chk.point
    return {"witness_accepted": bool(chk.accepted), "reasons": [r.value for r in chk.reasons],
            "torque_residual_Nm": _f(chk.torque_residual_Nm), "torque_tolerance_Nm": float(torque_tolerance(k)),
            "Tshaft_Nm": None if pt is None else _f(pt.Tshaft_Nm),
            "violated_groups": [] if pt is None else pt.violated_groups(),
            "claim_support": "FEASIBLE" if chk.accepted else "UNKNOWN"}


# -- layer 1: oracle blocks -----------------------------------------------------------------------------------


def _closed_form(mdoc: dict, spec: dict, id_a: float, iq_a: float, psd_psq=None, Rs=None, psi_pm=None,
                 source: str = "") -> dict:
    """Contract equations with the EXPORTED parameters; psi from the constant-dq law or the map definition."""
    settings = settings_block()
    n, vdc = spec["speed_rpm"], spec["Vdc_V"]
    if psd_psq is None:
        psd_psq = O.constant_dq_flux(mdoc, id_a, iq_a, psi_pm)
    rs = mdoc["motor"]["Rs_ohm"] if Rs is None else Rs
    q = O.dq_quantities(mdoc, n, vdc, id_a, iq_a, *psd_psq, rs)
    q["i_peak_A"] = math.sqrt(id_a * id_a + iq_a * iq_a)
    cons = O.constraints(mdoc, settings, q, n, id_a, iq_a, spec["limits"])
    mode = O.energy_mode(q["Pshaft_W"], q["Pdc_W"], q["omega_m_rad_s"], settings["power_zero_tol_W"],
                         settings["speed_zero_tol_rad_s"])
    return {"kind": "closed_form", "source": source or "model-contract equations with the exported parameters",
            "tolerance": ORACLE_TOLERANCE["closed_form"], "values": q, "constraints": cons, "energy_mode": mode}


def _golden(ref: dict, gid: str, g: dict) -> dict:
    return {"kind": "golden", "source": O.source_label(ref, "golden_forward.json", gid),
            "tolerance": ORACLE_TOLERANCE["golden"], "values": dict(g["expected"]),
            "violated_groups": sorted(g["expected_violations"])}


def _close(a, b, atol, rtol) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= atol + rtol * max(abs(a), abs(b))


def oracle_agreement(case: dict) -> dict:
    """Layer 2 against layer 1, checked at export: a disagreement is reported with the case, never hidden."""
    fails = []
    py = case["python"]
    for orc in case.get("oracle", []):
        tol = orc["tolerance"]
        for qn, want in orc.get("values", {}).items():
            got = py.get(qn)
            if orc["kind"] == "golden":
                atol, rtol = tol["atol"], tol["rtol"]
            else:
                cls = QUANTITIES[qn][1] if qn in QUANTITIES else "ratio"
                atol, rtol = PARITY_TOLERANCE["classes"][cls][0] * tol.get("atol_scale", 1.0), tol["rtol"]
            if not _close(got, want, atol, rtol):
                fails.append(f"{orc['kind']}:{qn} python {got!r} oracle {want!r}")
        for name, st in orc.get("constraints", {}).items():
            if py.get("constraints", {}).get(name) != st:
                fails.append(f"{orc['kind']}:constraint {name} python {py.get('constraints', {}).get(name)} "
                             f"oracle {st}")
        if "violated_groups" in orc and sorted(py.get("violated_groups", [])) != orc["violated_groups"]:
            fails.append(f"{orc['kind']}:violated_groups python {py.get('violated_groups')} oracle "
                         f"{orc['violated_groups']}")
        if "energy_mode" in orc and py.get("energy_mode") != orc["energy_mode"]:
            fails.append(f"{orc['kind']}:energy_mode python {py.get('energy_mode')} oracle {orc['energy_mode']}")
        for key in ("covered", "psi_d_Wb", "psi_q_Wb"):
            if key in orc and key not in orc.get("values", {}):
                want = orc[key]
                got = py.get("evaluable") if key == "covered" and "covered" not in py else py.get(key)
                ok = (got == want) if key == "covered" else _close(got, want, 1e-15, 1e-12)
                if not ok:
                    fails.append(f"{orc['kind']}:{key} python {got!r} oracle {want!r}")
    return {"status": "PASS" if not fails else "FAIL", "failures": fails}


# -- verification fixture models ------------------------------------------------------------------------------

VF_ID = [-300.0, -220.0, -150.0, -90.0, -40.0, 0.0]            # 6 nodes, non-uniform
VF_IQ = [-120.0, -50.0, 0.0, 60.0, 130.0, 210.0, 300.0]       # 7 nodes, non-uniform, asymmetric about 0
VF_HOLE = (2, 3)                                              # node (id=-150, iq=60) invalid
VF_ALPHA_PM = -0.0012                                         # PM flux temperature coefficient of the family
VF_PLANES_C = (20.0, 120.0)


def _vf_plane(T: float, iq_axis=VF_IQ, hole=VF_HOLE) -> FluxMapPlane:
    psi_pm = 0.1 * (1.0 + VF_ALPHA_PM * (T - 20.0))
    psd = np.empty((len(VF_ID), len(iq_axis)))
    psq = np.empty_like(psd)
    for i, d in enumerate(VF_ID):
        for j, q in enumerate(iq_axis):
            psd[i, j], psq[i, j] = O.verification_flux(d, q, psi_pm)
    valid = np.ones(psd.shape, dtype=bool)
    if hole is not None:
        valid[hole] = False
        psd[hole] = psq[hole] = np.nan
    return FluxMapPlane(np.array(VF_ID), np.array(iq_axis), psd, psq, valid, T, f"verification family at {T:g} degC")


def _vf_drive(drive_id: str, flux, fidelity=Fidelity.D2, **motor_kw) -> DriveModel:
    motor = MotorModel(drive_id + "_MOTOR", 4, flux, 0.015, RotationalLossModel(0.002, basis="verification fixture"),
                       fidelity=fidelity, **motor_kw)
    inv = InverterModel("VF_2L_VSI", 600.0, VoltageModel(0.05), InverterLossModel(200.0, 0.008, True),
                        switching_frequency_context_Hz=20000.0)
    dom = OperatingDomain((-300.0, 0.0), (-120.0, 300.0), (-16000.0, 16000.0),
                          interpretation="verification fixture domain")
    return DriveModel(drive_id, "1", motor, inv, dom, VF_PROVENANCE,
                      ("synthetic verification fixture of the transfer package; not a machine",))


def vf_map_drive() -> DriveModel:
    """Non-square, non-uniform, asymmetric axes; one invalid node; two temperature planes with declared linear
    interpolation: catches transposition, index, hole, edge and plane-selection errors."""
    flux = FluxMapModel(tuple(_vf_plane(T) for T in VF_PLANES_C), temperature_interpolation="linear",
                        temperature_interpolation_basis="verification fixture: two planes of one analytic family")
    return _vf_drive("VF_D2_MAP", flux)


def vf_qodd_drive() -> DriveModel:
    """A half map (iq >= 0) with declared q-odd symmetry; the export carries the mirrored full plane."""
    half = [q for q in VF_IQ if q >= 0.0]
    return _vf_drive("VF_D2_QODD", FluxMapModel((_vf_plane(20.0, half, None),), symmetry="q_odd"))


def vf_temperature_drive() -> DriveModel:
    base = sf.synthetic_drive()
    motor = replace(base.motor, motor_id="VF_D1_TEMPERATURE_LAWS_MOTOR", reference_winding_temp_C=20.0,
                    reference_magnet_temp_C=20.0,
                    rs_temperature=TemperatureDependence(0.00393, (-40.0, 180.0), "copper, verification fixture"),
                    psi_temperature=TemperatureDependence(-0.0012, (-40.0, 180.0), "PM, verification fixture"))
    return replace(base, drive_id="VF_D1_TEMPERATURE_LAWS", revision="1", motor=motor, provenance=VF_PROVENANCE,
                   notes=("the reference synthetic drive with declared temperature laws; verification only",))


# -- case builders --------------------------------------------------------------------------------------------


class Builder:
    """Collects models (keyed, with their documents) and cases."""

    def __init__(self, ref: dict):
        self.ref = ref
        self.models: dict[str, dict] = {}
        self.drives: dict[str, DriveModel] = {}
        self.cases: dict[str, list] = {"forward": [], "flux_lookup": [], "requirement_witness": []}

    def model(self, key: str, drive: DriveModel, role: str, purpose: str, extra: dict | None = None) -> str:
        if key not in self.models:
            self.models[key] = model_document(drive, key, role, purpose, extra)
            self.drives[key] = drive
        return key

    def forward(self, cid: str, key: str, spec: dict, id_a: float, iq_a: float, purpose: str, oracles=(),
                unsupported=()):
        drive = self.drives[key]
        c = {"case_id": cid, "kind": "forward", "model": key, "purpose": purpose, "scenario": spec,
             "point": {"id_A": float(id_a), "iq_A": float(iq_a)},
             "python": forward_block(drive, scenario_of(cid, spec), id_a, iq_a),
             "oracle": list(oracles), "unsupported": list(unsupported)}
        c["python_vs_oracle"] = oracle_agreement(c)
        self.cases["forward"].append(c)
        return c

    def lookup(self, cid: str, key: str, magnet_temp_C, id_a: float, iq_a: float, purpose: str, analytic=None):
        drive = self.drives[key]
        mdoc = self.models[key]
        plane, reason = O.plane_for(mdoc["motor"]["flux"], magnet_temp_C)
        orc = {"kind": "definition", "source": "interpolant definition on the exported planes (set test)",
               "tolerance": {"atol": 1e-15, "rtol": 1e-12}}
        if plane is None:
            orc.update(covered=False, psi_d_Wb=None, psi_q_Wb=None, reason=reason)
        else:
            orc.update(O.map_lookup(plane, id_a, iq_a))
        oracles = [orc]
        if analytic is not None and orc["covered"]:
            ad, aq = analytic(id_a, iq_a, magnet_temp_C)
            oracles.append({"kind": "analytic_info", "source": "the analytic family of the map (information: "
                            "interpolation error off-grid is not a failure; at nodes it must vanish)",
                            "tolerance": {"atol": 0.0, "rtol": 0.0}, "psi_d_Wb_analytic": ad, "psi_q_Wb_analytic": aq,
                            "interpolation_error_Wb": [orc["psi_d_Wb"] - ad, orc["psi_q_Wb"] - aq]})
        c = {"case_id": cid, "kind": "flux_lookup", "model": key, "purpose": purpose, "magnet_temp_C": magnet_temp_C,
             "point": {"id_A": float(id_a), "iq_A": float(iq_a)},
             "python": lookup_block(drive.motor.flux, magnet_temp_C, id_a, iq_a), "oracle": oracles}
        c["python_vs_oracle"] = oracle_agreement(c)
        self.cases["flux_lookup"].append(c)
        return c


def _spec(sc_limits, n, vdc, **kw):
    return scenario_spec(n, vdc, sc_limits, **kw)


def reference_cases(b: Builder) -> None:
    ref = b.ref
    lim = sf.synthetic_limits()
    golden = O.golden_forward(ref)
    syn = b.model("REF_D1_SYNTH", sf.synthetic_drive(), "reference_fixture",
                  "reference synthetic IPMSM drive of the accepted package (constant dq)",
                  {"reference_file": O.source_label(ref, "synthetic_drive.json")})
    f05 = b.model("REF_D1_F05", sf.synthetic_drive(golden["F05_SPMSM_NO_ROTATIONAL_OR_INVERTER_LOSS"]["overrides"]),
                  "reference_fixture", "reference SPMSM variant F05 (zero rotational and inverter loss fixture)",
                  {"reference_file": O.source_label(ref, "golden_forward.json", "F05 parameter_overrides")})
    for gid, g in golden.items():
        key = f05 if gid.startswith("F05") else syn
        inp = g["input"]
        spec = _spec(lim, inp["n_rpm"], inp["Vdc_V"])
        b.forward(f"REF.{gid}", key, spec, inp["id_A_peak"], inp["iq_A_peak"],
                  "golden forward case of the accepted reference package",
                  [_golden(ref, gid, g), _closed_form(b.models[key], spec, inp["id_A_peak"], inp["iq_A_peak"])])
    m = b.models[syn]
    s = settings_block()
    # voltage boundary: |v_cmd| placed +-0.5 and +-2 tolerances from the budget (ACTIVE is on the boundary, a pass)
    spec = _spec(lim, 6000.0, 600.0)
    vb = 0.95 * 600.0 / math.sqrt(3.0)
    tol_v = O.tolerance(vb, s["voltage_abs_tol_V"], s["constraint_rel_tol"])
    for tag, k in (("inside_2tol", 2.0), ("inside_half_tol", 0.5), ("outside_half_tol", -0.5),
                   ("outside_2tol", -2.0)):
        iq = O.iq_on_voltage_circle(m, 6000.0, -300.0, vb - k * tol_v)
        b.forward(f"BND.VOLTAGE.{tag}", syn, spec, -300.0, iq,
                  f"voltage budget boundary: slack {k:+g} x tolerance ({tol_v:.3g} V)",
                  [_closed_form(m, spec, -300.0, iq)])
    # current limit: exactly on it (a 3-4-5 point), and 2 tolerances either side
    spec = _spec(lim, 1000.0, 600.0)
    tol_i = O.tolerance(600.0, s["current_abs_tol_A"], s["constraint_rel_tol"])
    for tag, mag in (("exact", 600.0), ("inside_2tol", 600.0 - 2 * tol_i), ("outside_2tol", 600.0 + 2 * tol_i)):
        iq = 480.0 if tag == "exact" else math.sqrt(mag * mag - 360.0 ** 2)
        b.forward(f"BND.CURRENT.{tag}", syn, spec, -360.0, iq, f"current limit boundary |i| = {mag!r} A",
                  [_closed_form(m, spec, -360.0, iq)])
    # allowed domain: id = 0 (on the declared edge) and just beyond; top speed and just beyond
    spec = _spec(lim, 3000.0, 600.0)
    for tag, d in (("id_on_edge", 0.0), ("id_beyond", 1e-6)):
        b.forward(f"BND.DOMAIN.{tag}", syn, spec, d, 100.0, "declared allowed domain edge (id <= 0)",
                  [_closed_form(m, spec, d, 100.0)])
    tol_n = O.tolerance(16000.0, s["speed_abs_tol_rpm"], s["constraint_rel_tol"])
    for tag, n in (("speed_on_edge", 16000.0), ("speed_beyond", 16000.0 + 2 * tol_n)):
        spec = _spec(lim, n, 600.0)
        b.forward(f"BND.DOMAIN.{tag}", syn, spec, -450.0, 50.0, "declared allowed speed edge",
                  [_closed_form(m, spec, -450.0, 50.0)])
    # regeneration against the charge power limit
    spec = _spec(lim, 12000.0, 600.0)
    tol_p = O.tolerance(1e5, s["power_abs_tol_W"], s["constraint_rel_tol"])
    for tag, p in (("exact", -1e5), ("inside_2tol", -1e5 + 2 * tol_p), ("outside_2tol", -1e5 - 2 * tol_p)):
        iq = O.iq_for_dc_power(m, 12000.0, -300.0, p)
        b.forward(f"BND.CHARGE.{tag}", syn, spec, -300.0, iq, f"DC charge power boundary P_dc = {p!r} W",
                  [_closed_form(m, spec, -300.0, iq)])
    # a missing limit is not an unlimited one
    for tag, L in (("not_declared", DcSourceLimits()),
                   ("declared_unlimited", DcSourceLimits(math.inf, math.inf, math.inf, math.inf))):
        spec = _spec(L, 3000.0, 600.0)
        b.forward(f"SEM.DC_LIMITS.{tag}", syn, spec, -200.0, 400.0,
                  "DC limits " + tag.replace("_", " ") + ": not declared -> MISSING_INPUT, unlimited -> no constraint",
                  [_closed_form(m, spec, -200.0, 400.0)])
    # a stated winding temperature without a declared reference temperature
    spec = _spec(lim, 3000.0, 600.0, winding_temp_C=90.0)
    b.forward("SEM.TEMPERATURE.no_reference", syn, spec, -200.0, 400.0,
              "winding temperature stated, Rs reference temperature not declared -> MISSING_INPUT (diagnostic only)",
              [_closed_form(m, spec, -200.0, 400.0)])
    # the manufactured map of the reference package
    man = b.model("REF_D2_MANUFACTURED", sf.manufactured_map_drive(), "reference_fixture",
                  "manufactured conservative flux map (41 x 41) of the accepted package with the borrowed test "
                  "configuration (synthetic verification only)",
                  {"reference_file": O.source_label(ref, "manufactured_flux_map.json")})
    md = b.models[man]
    tp = ref["files"]["manufactured_flux_map.json"]["content"]["test_point"]
    for tag, d, q, why in (("test_point", tp["id_A_peak"], tp["iq_A_peak"], "the package's stated test point"),
                           ("off_grid", -95.0, 155.0, "interior off-grid point (bilinear)"),
                           ("allowed_domain_violated", 50.0, 0.0, "covered by the map, outside the allowed domain"),
                           ("outside_map", -250.0, 0.0, "outside the map: not evaluable, no extrapolation")):
        spec = _spec(lim, 3000.0, 600.0)
        lk = O.map_lookup(md["motor"]["flux"]["planes"][0], d, q)
        orcs = []
        if lk["covered"]:
            orcs.append(_closed_form(md, spec, d, q, (lk["psi_d_Wb"], lk["psi_q_Wb"]),
                                     source="interpolant definition on the exported plane + contract equations"))
        else:
            orcs.append({"kind": "definition", "source": "interpolant definition: not covered",
                         "tolerance": {"atol": 0.0, "rtol": 0.0}, "values": {}, "covered": False})
        if tag == "test_point":
            ad, aq = O.manufactured_flux(d, q)
            orcs.append({"kind": "golden", "source": O.source_label(ref, "manufactured_flux_map.json",
                                                                  "test_point / analytic_flux"),
                         "tolerance": ORACLE_TOLERANCE["golden"],
                         "values": {"psi_d_Wb": tp["psi_d_Wb"], "psi_q_Wb": tp["psi_q_Wb"], "Te_Nm": tp["Te_Nm"]},
                         "analytic_psi": [ad, aq]})
        b.forward(f"REF.MANUFACTURED.{tag}", man, spec, d, q, why, orcs)
        b.lookup(f"REF.MANUFACTURED.lookup.{tag}", man, None, d, q, why,
                 lambda x, y, T: O.manufactured_flux(x, y))


def verification_cases(b: Builder) -> None:
    lim = sf.synthetic_limits()
    # temperature laws of a constant-dq model
    t = b.model("VF_D1_TEMP", vf_temperature_drive(), "verification_fixture",
                "constant dq with declared Rs and psi_PM temperature laws (20 degC reference)")
    m = b.models[t]
    for tag, tw, tm, why in (("not_stated", None, None, "temperatures not stated: parameters as supplied"),
                             ("hot_120C", 120.0, 120.0, "both laws applied at 120 degC"),
                             ("winding_outside_law", 200.0, None, "winding 200 degC outside the law's validity -> "
                                                                  "OUTSIDE_MODEL_DOMAIN, diagnostic only")):
        spec = _spec(lim, 3000.0, 600.0, winding_temp_C=tw, magnet_temp_C=tm)
        mo = m["motor"]
        rs = mo["Rs_ohm"] * (1 + mo["rs_temperature"]["coeff_per_K"] * (tw - 20.0)) if tw == 120.0 else None
        psi = (mo["flux"]["psi_pm_Wb"] * (1 + mo["flux"]["psi_temperature"]["coeff_per_K"] * (tm - 20.0))
               if tm is not None else None)
        b.forward(f"VF.TEMP.{tag}", t, spec, -200.0, 400.0, why,
                  [_closed_form(m, spec, -200.0, 400.0, Rs=rs, psi_pm=psi,
                                source="contract equations with the exported temperature laws")])
    # non-square asymmetric map with a hole and two planes
    key = b.model("VF_D2_MAP", vf_map_drive(), "verification_fixture",
                  "6 x 7 non-uniform asymmetric map, one invalid node, planes at 20 and 120 degC, linear temperature "
                  "interpolation declared")

    def fam(x, y, T):
        return O.verification_flux(x, y, 0.1 * (1.0 + VF_ALPHA_PM * ((T if T is not None else 20.0) - 20.0)))
    pts = (("node", 20.0, -220.0, 130.0, "a grid node: the node value exactly"),
           ("interior", 20.0, -60.0, 100.0, "interior of a valid cell (bilinear)"),
           ("edge_left_cell_valid", 20.0, -220.0, 30.0, "on an id grid line: the cell to the right is invalid, the "
                                                        "left one valid -> covered"),
           ("edge_lower_cell_valid", 20.0, -190.0, 0.0, "on an iq grid line: the upper cell is invalid, the lower "
                                                        "one valid -> covered"),
           ("edge_both_invalid", 20.0, -150.0, 30.0, "on an edge shared by two invalid cells -> not covered"),
           ("in_hole_cell", 20.0, -120.0, 30.0, "inside a cell with an invalid node -> not covered"),
           ("upper_corner", 20.0, 0.0, 300.0, "the last node of both axes -> covered"),
           ("outside_id", 20.0, -300.5, 0.0, "below the id axis -> not covered (no extrapolation)"),
           ("outside_iq", 20.0, -60.0, 300.25, "above the iq axis -> not covered (no clipping)"),
           ("plane_120C", 120.0, -60.0, 100.0, "the 120 degC plane"),
           ("blend_70C", 70.0, -60.0, 100.0, "declared linear interpolation between the planes"),
           ("blend_hole", 70.0, -120.0, 30.0, "blended plane keeps the hole (mask intersection)"),
           ("above_planes", 150.0, -60.0, 100.0, "above the planes -> OUTSIDE_MODEL_DOMAIN (not extrapolated)"),
           ("no_temperature", None, -60.0, 100.0, "several planes, no magnet temperature -> MISSING_INPUT"))
    for tag, T, d, q, why in pts:
        b.lookup(f"VF.MAP.{tag}", key, T, d, q, why, fam)
    md = b.models[key]
    for tag, T, d, q, why in (("forward_blend", 70.0, -60.0, 100.0, "forward evaluation on the blended plane"),
                              ("forward_edge", 20.0, -220.0, 30.0, "forward evaluation on a covered edge"),
                              ("forward_hole", 20.0, -120.0, 30.0, "forward evaluation in the hole -> not evaluable"),
                              ("forward_no_temperature", None, -60.0, 100.0,
                               "no magnet temperature -> not evaluable (MISSING_INPUT)")):
        spec = _spec(lim, 4000.0, 600.0, magnet_temp_C=T)
        plane, reason = O.plane_for(md["motor"]["flux"], T)
        lk = O.map_lookup(plane, d, q) if plane is not None else {"covered": False}
        orc = (_closed_form(md, spec, d, q, (lk["psi_d_Wb"], lk["psi_q_Wb"]),
                            source="interpolant definition on the exported planes + contract equations")
               if lk["covered"] else {"kind": "definition", "source": f"not covered ({reason or 'outside cells'})",
                                      "tolerance": {"atol": 0.0, "rtol": 0.0}, "values": {}, "covered": False})
        b.forward(f"VF.MAP.{tag}", key, spec, d, q, why, [orc])
    # declared q-odd symmetry
    qo = b.model("VF_D2_QODD", vf_qodd_drive(), "verification_fixture",
                 "half map (iq >= 0) with declared q-odd symmetry; exported as the mirrored full plane")
    for tag, d, q in (("positive_iq", -60.0, 100.0), ("negative_iq", -60.0, -100.0), ("mirror_node", -220.0, -130.0)):
        b.lookup(f"VF.QODD.{tag}", qo, None, d, q, "q-odd symmetry: psi_d even, psi_q odd in iq",
                 lambda x, y, T: O.verification_flux(x, y, 0.1))


def product_cases(b: Builder, drive: DriveModel, project) -> str:
    """The project's own drive: a systematic set over +-speed, motoring / regeneration and zero speed, plus the
    voltage and current boundaries (constant dq), with the project's DC source."""
    dc = project.data("dc_source")
    lim = project.dc_limits()
    vdc = float(dc["Vdc_nominal_V"])
    key = b.model("PRODUCT", drive, "product",
                  f"the drive of project {project.label} (section 'drive')",
                  {"project": {"id": project.id, "revision": project.revision, "modified": project.modified,
                               "project_digest": project.digest(),
                               "drive_section_digest": project.sections["drive"].digest,
                               "dc_source_section_digest": project.sections["dc_source"].digest}})
    m = b.models[key]
    dom = m["domain"]
    imax = m["inverter"]["current_limit_A_peak"]
    nmax = max(abs(dom["speed_rpm"][0]), abs(dom["speed_rpm"][1]))

    def clip(v, lo_hi):
        return min(max(v, lo_hi[0]), lo_hi[1])
    unsupported = ["Pinv_W", "Pdc_W", "Idc_A", "efficiency", "energy_mode", "accepted", "gate_reasons",
                   "constraints:DC"] if m["inverter"]["loss"]["kind"] == "datasheet_module" else []
    speeds = [0.0, clip(0.25 * nmax, dom["speed_rpm"]), clip(-0.25 * nmax, dom["speed_rpm"]),
              clip(0.75 * nmax, dom["speed_rpm"])]
    # tagged by the torque sign: with a negative speed a positive torque is braking (the energy mode says which)
    points = (("pos_torque", clip(-0.35 * imax, dom["id_A"]), clip(0.6 * imax, dom["iq_A"])),
              ("neg_torque", clip(-0.35 * imax, dom["id_A"]), clip(-0.6 * imax, dom["iq_A"])),
              ("light_load", clip(-0.1 * imax, dom["id_A"]), clip(0.15 * imax, dom["iq_A"])))
    for n in speeds:
        spec = _spec(lim, n, vdc)
        for tag, d, q in points:
            orcs = []
            if m["family"] == "constant_dq":
                orcs.append(_closed_form(m, spec, d, q))
            else:
                plane, _ = O.plane_for(m["motor"]["flux"], None)
                lk = O.map_lookup(plane, d, q) if plane is not None else {"covered": False}
                if lk["covered"]:
                    orcs.append(_closed_form(m, spec, d, q, (lk["psi_d_Wb"], lk["psi_q_Wb"]),
                                             source="interpolant definition on the exported plane + equations"))
            b.forward(f"PRODUCT.{tag}.n{n:+g}", key, spec, d, q,
                      f"product drive, {tag.replace('_', ' ')} at {n:g} rpm, Vdc {vdc:g} V", orcs, unsupported)
    if m["family"] == "constant_dq" and m["inverter"]["voltage"]["voltage_error"] == "ideal":
        s = settings_block()
        n = clip(0.5 * nmax, dom["speed_rpm"])
        spec = _spec(lim, n, vdc)
        v = m["inverter"]["voltage"]
        vb = v["diagnostic_budget_scale"] * (1 - v["reserve_fraction"]) * vdc / math.sqrt(3.0)
        tol_v = O.tolerance(vb, s["voltage_abs_tol_V"], s["constraint_rel_tol"])
        d = clip(-0.5 * imax, dom["id_A"])
        for tag, k in (("inside_2tol", 2.0), ("outside_2tol", -2.0)):
            try:
                q = O.iq_on_voltage_circle(m, n, d, vb - k * tol_v)
            except ValueError:
                break
            if dom["iq_A"][0] <= q <= dom["iq_A"][1]:
                b.forward(f"PRODUCT.BND.VOLTAGE.{tag}", key, spec, d, q,
                          f"product voltage budget boundary: slack {k:+g} x tolerance", [_closed_form(m, spec, d, q)],
                          unsupported)
    return key


def requirement_cases(b: Builder, case_files: list, prefer_key: str | None = None) -> list:
    """Requirement witnesses: each condition's witness re-checked against the ORIGINAL request on the target.

    A FEASIBLE condition claim is reproduced when the witness passes the same gate there; INFEASIBLE / UNKNOWN
    claims are carried as layer-2 evidence (their exclusion proofs, certificates and ratings are not re-derived by
    the first port) and their witness points - where they exist - are still re-evaluated as diagnostics.
    """
    from ..identity import content_sha256
    from ..service import evaluate_case_full
    by_hash = {content_sha256(d): k for k, d in b.drives.items()}
    out = []
    for source, cd in case_files:
        body = {k: v for k, v in cd.items() if k != "analyses"}
        rec_dict, rec, case = evaluate_case_full(body)
        h = content_sha256(case.drive)
        key = prefer_key if prefer_key and by_hash.get(h) == prefer_key else by_hash.get(h)
        if key is None:
            key = b.model(f"REQ_{case.drive.drive_id}"[:60], case.drive, "requirement_model",
                          f"the drive the requirement {rec.requirement.req_id} was evaluated with")
            by_hash[h] = key
        req = rec.requirement
        band = (req.target_Nm - req.band_Nm, req.target_Nm + req.band_Nm) if req.operator == "band" else None
        for i, cond in enumerate(rec.conditions):
            sc = cond.scenario
            spec = scenario_spec(sc.speed_rpm, sc.Vdc_V, sc.source_limits, sc.winding_temp_C, sc.magnet_temp_C,
                                 sc.switching_frequency_Hz)
            ws = cond.witness_solution or cond.solution
            wp = None if ws is None or ws.point is None else ws.point
            claim = cond.requirement_claim
            c = {"case_id": f"REQ.{source}.{req.req_id}@{req.revision}.c{i}", "kind": "requirement_witness",
                 "model": key, "requirement_source": source, "requirement": req.describe(), "scenario": spec,
                 "witness": None if wp is None else {"id_A": float(wp.id_A), "iq_A": float(wp.iq_A),
                                                     "torque_Nm": float(ws.T_request_Nm)},
                 "request": {"T_request_Nm": None if band else float(req.target_Nm),
                             "band_Nm": None if band is None else [float(band[0]), float(band[1])]},
                 "layer2_claim": {"status": claim.status.value, "reasons": [r.value for r in claim.reasons],
                                  "evidence_kinds": [e.kind.value for e in claim.evidence], "scope": claim.scope,
                                  "policy": claim.policy, "time_horizon": claim.time_horizon,
                                  "record_input_sha256": rec.input_sha256,
                                  "verdict": {"status": rec.verdict.status.value,
                                              "reasons": [r.value for r in rec.verdict.reasons]}},
                 "python": None if wp is None else witness_block(case.drive, sc, float(wp.id_A), float(wp.iq_A),
                                                                None if band else req.target_Nm, band),
                 "native_scope": (
                     "the target re-evaluates the witness against the original request: a FEASIBLE static / DC "
                     "claim is reproduced when it passes; the duration part, a sampled Vdc range and INFEASIBLE / "
                     "UNKNOWN claims rest on layer-2 evidence (ratings, exclusion proofs, certificates) that is not "
                     "re-derived natively" if wp is not None else
                     "no witness point: the layer-2 claim rests on exclusion evidence that the first port does not "
                     "re-derive (NOT_SUPPORTED natively)")}
            b.cases["requirement_witness"].append(c)
            out.append(c)
    return out
