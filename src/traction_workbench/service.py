"""JSON-in / JSON-out service layer shared by the CLI and the desktop application.

Everything returned here is plain JSON data (floats rounded only for charts;
decision data keep full precision).
"""

from __future__ import annotations

import time
from functools import lru_cache

import numpy as np

from . import __version__, progress
from . import spec_fixtures as sf
from .analysis.compare import compare_scenarios
from .analysis.dominance import capability_dominance, requirement_relaxation
from .analysis.sizing import size_parameter
from .decision import evaluate_requirement, jsonable as _jsonable
from .errors import InputValidationError, OutsideModelDomain
from .io import case_from_dict, drive_from_dict, limits_from_dict
from .models.components import DriveModel
from .models.flux import FluxMapModel
from .physics import DriveKernel, forward_evaluation
from .scenario import DcSourceLimits, Scenario
from .settings import DEFAULT_SETTINGS, NumericalSettings
from .solvers.capability import physical_capability, policy_capability
from .solvers.common import dc_ok, electrical_ok
from .solvers.policy import PolicyEvaluator
from .units import Conversions

CURVE_SETTINGS = DEFAULT_SETTINGS.with_(physical_grid_points=401, capability_scan_samples=13)


def resolve_drive(spec: dict | None) -> DriveModel:
    spec = spec or {"builtin": "SYNTH_IPMSM_200KW_REF_V1"}
    return drive_from_dict(spec)


def resolve_limits(spec: dict | None, drive_spec: dict | None) -> DcSourceLimits:
    if spec:
        return limits_from_dict(spec, Conversions())
    return sf.synthetic_limits()


def drive_info(drive: DriveModel) -> dict:
    out = drive.describe()
    if isinstance(drive.motor.flux, FluxMapModel):
        out["magnetic_qualification"] = [p.magnetic_qualification(drive.motor.flux.reciprocity_rel_tol)
                                         for p in drive.motor.flux.planes]
    return _jsonable(out)


def data_audit(drive: DriveModel) -> dict:
    """Machine-data package audit (independent review P0-B, section 10.2): what the data may be used for.

    Static, dynamic, loss, thermal, demagnetisation and fault-domain uses are stated separately; nothing is
    promoted because a neighbouring use is supported.  The audit states gaps; it does not fill them.
    """
    flux = drive.motor.flux
    motor = drive.motor
    uses, gaps = [], []
    if isinstance(flux, FluxMapModel):
        quals = [p.magnetic_qualification(flux.reciprocity_rel_tol) for p in flux.planes]
        coverage = [p.coverage_summary() for p in flux.planes]
        holes = [c["total_cells"] - c["valid_cells"] for c in coverage]
        static_status = ("SUPPORTED within the covered cells (bilinear, no extrapolation)"
                         if all(q["static_use"]["status"].startswith("PLAUSIBLE") for q in quals) else
                         "SUPPORTED with node-plausibility warnings - check the data")
        uses.append({"use": "static steady-state torque / voltage / current", "status": static_status,
                     "basis": f"{len(flux.planes)} plane(s); invalid cells per plane: {holes}"})
        uses.append({"use": "dynamic transients (ASC entry, current control)", "status": "NOT QUALIFIED",
                     "basis": quals[0]["dynamic_use"]["reasons"][0]})
        temps = [p.magnet_temp_C for p in flux.planes]
        uses.append({"use": "magnet-temperature dependence",
                     "status": ("plane selection at stated temperatures " + str(temps) +
                                (f"; linear interpolation declared ({flux.temperature_interpolation_basis})"
                                 if flux.temperature_interpolation else "; no interpolation between planes")),
                     "basis": "planes are raw per-temperature data: no additional psi scaling is applied"})
        magnetic = {"kind": "flux_map", "planes": coverage, "qualification": quals}
    else:
        v = flux.validity
        uses.append({"use": "static steady-state torque / voltage / current",
                     "status": "SUPPORTED on the declared domain" if v is None else
                     f"SUPPORTED inside the parameter validity box id {list(v.id_A)} A, iq {list(v.iq_A)} A",
                     "basis": "constant psi_PM, Ld, Lq (no saturation, no cross-coupling)"})
        uses.append({"use": "dynamic transients (ASC entry, current control)",
                     "status": "SCREENING ONLY (linear constant-inductance model; saturation not represented)",
                     "basis": "apparent = differential inductance only for a linear magnetic model"})
        uses.append({"use": "magnet-temperature dependence",
                     "status": ("declared coefficient " + str(motor.psi_temperature.coeff_per_K) + " /K valid "
                                + str(list(motor.psi_temperature.valid_C)) + " degC") if motor.psi_temperature else
                     "not declared: a stated magnet temperature away from the reference gives UNKNOWN",
                     "basis": f"reference {motor.reference_magnet_temp_C} degC"})
        magnetic = {"kind": "constant_dq", **flux.describe()}
        if v is not None:
            gaps.append("parameter validity box declared: outside it the model says nothing (control domain != "
                        "data domain)")
    uses.append({"use": "winding resistance vs temperature",
                 "status": ("declared coefficient " + str(motor.rs_temperature.coeff_per_K) + " /K valid "
                            + str(list(motor.rs_temperature.valid_C)) + " degC") if motor.rs_temperature else
                 "not declared: a stated winding temperature away from the reference gives UNKNOWN",
                 "basis": f"reference {motor.reference_winding_temp_C} degC"})
    inv = drive.inverter.loss
    mod = drive.inverter.module_loss
    if mod is not None:
        dev = mod.device
        uses.append({"use": "inverter loss / DC power",
                     "status": f"datasheet module model ({dev.technology}, {dev.value_kind} values; fsw "
                               f"{mod.fsw_Hz / 1e3:g} kHz, {mod.modulation}; Tj {drive.inverter.module_Tj_C:g} degC)",
                     "basis": (dev.source or "source not stated") + " - covered V/I/T only (no extrapolation); "
                              "typical values are not bounds"})
    else:
        uses.append({"use": "inverter loss / DC power",
                     "status": "MISSING (DC claims UNKNOWN)" if inv is None else
                     f"{inv.kind}: P = a0 + a2*Ipk^2 (no Vdc/fsw/Tj/modulation dependence)",
                     "basis": "synthetic energy-loss surrogate, not a device conduction/switching model"
                     if inv is not None and inv.kind == "quadratic_current_surrogate" else "declared"})
    rot = motor.rotational_loss
    uses.append({"use": "rotational / iron loss", "status": "MISSING (shaft torque undefined)" if rot is None else
                 rot.basis, "basis": "loss-equivalent resisting torque; not a dq iron-loss-current model"})
    uses.append({"use": "thermal duration", "status": "NOT PART OF THE MOTOR MODEL",
                 "basis": "needs a matching rating envelope or a qualified thermal model"})
    uses.append({"use": "demagnetisation", "status": "NOT SUPPORTED",
                 "basis": "the declared id domain is a restriction, not a demagnetisation safe-domain"})
    uses.append({"use": "fault-current domain (ASC/short circuit beyond normal Imax)",
                 "status": "NOT COVERED unless the data extend beyond the normal current limit",
                 "basis": "normal-operation maps are never clipped or extrapolated into the fault domain"})
    uses.append({"use": "extrapolation outside the data", "status": "NEVER", "basis": "no-extrapolation policy"})
    prov = drive.provenance
    if prov.origin.value in ("synthetic", "estimated"):
        gaps.append(f"{prov.origin.value} data: no engineering qualification (P0-C needs one real motor-inverter "
                    f"combination with calibration and holdout data)")
    gaps.append("no calibration / holdout split, uncertainty or correlation model is declared")
    return _jsonable({"identity": {"drive_id": drive.drive_id, "revision": drive.revision,
                                   "provenance": prov.to_dict(), "fidelity": drive.fidelity.value},
                      "conventions": {"dq": "amplitude-invariant Park, phase-peak values, d on PM flux",
                                      "connection": motor.connection, "speed": "mechanical rpm",
                                      "torque": "shaft torque = T_em - tau_rot (sign: + with + speed = motoring)"},
                      "magnetic": magnetic, "supported_uses": uses, "qualification_gaps": gaps})


def evaluate_case(case_dict: dict) -> dict:
    return evaluate_case_full(case_dict)[0]


def resolve_case_source(case_dict: dict):
    """A requirement whose Vdc is the battery OCV (``conditions.Vdc.port = "battery_ocv"``) with a declared Thevenin
    ``source_model`` -> (the case at the resolved inverter terminal voltage(s), the source record, the source claim).
    Any other case is returned unchanged with (None, ()).  When no terminal voltage is resolved the drive side is
    judged at the OCV itself (no source drop: an optimistic view, stated) and the source claim decides: INFEASIBLE
    when the shaft power alone exceeds the source's maximum transfer (proven), otherwise UNKNOWN."""
    import copy
    from .analysis.source import resolve_terminal_voltage, source_from_dict
    from .status import Claim, Reason, Status
    req_d = case_dict.get("requirement") or {}
    vd = (req_d.get("conditions") or {}).get("Vdc") or {}
    if vd.get("port") != "battery_ocv":
        return case_dict, None, ()
    src = source_from_dict(case_dict.get("source_model"))
    if src is None:
        raise InputValidationError("a Vdc stated as battery OCV needs a declared source model (Thevenin R_eq, basis) "
                                   "- or state the inverter DC terminal voltage", field="source_model")
    base = copy.deepcopy(case_dict)
    base["requirement"]["conditions"]["Vdc"] = {**vd, "port": "inverter_dc_terminal"}
    case = case_from_dict(base)
    r, tmpl = case.requirement, case.scenario
    lim = case.limits if case.limits is not None else (tmpl.source_limits if tmpl is not None else DcSourceLimits())
    temps = {k: (getattr(r, k) if getattr(r, k) is not None else (getattr(tmpl, k) if tmpl is not None else None))
             for k in ("coolant_temp_C", "winding_temp_C", "magnet_temp_C")}
    ocvs = list(r.Vdc_V) if r.is_range else [r.Vdc_V]
    pts = [resolve_terminal_voltage(case.drive, Scenario(f"{r.req_id}-source@{v:g}V", r.speed_rpm, v, lim, **temps),
                                    src, r.target_Nm) for v in ocvs]
    info = {"source": src.describe(), "points": _jsonable(pts), "V_oc_V": ocvs,
            "status": "RESOLVED" if all(p["status"] == "RESOLVED" for p in pts) else
                      "NO_SOLUTION" if any(p["status"] == "NO_SOLUTION" for p in pts) else "NOT_RESOLVED"}
    q = f"inverter terminal voltage from battery OCV {' .. '.join(f'{v:g}' for v in ocvs)} V"
    scope = f"declared Thevenin source R_eq = {1e3 * src.R_eq_ohm:g} mOhm ({src.basis})"
    if info["status"] == "RESOLVED":
        vt = [p["V_terminal_V"] for p in pts]
        info["V_terminal_V"] = vt
        out = copy.deepcopy(case_dict)
        out["requirement"]["conditions"]["Vdc"] = {"value": sorted(vt) if r.is_range else vt[0], "unit": "V",
                                                   "port": "inverter_dc_terminal"}
        text = "; ".join(f"OCV {p['V_oc_V']:g} V -> terminal {p['V_terminal_V']:.5g} V (I_dc {p['I_dc_A']:.5g} A, "
                         f"{'drop' if p['sag_V'] >= 0 else 'rise'} {abs(p['sag_V']):.4g} V)" for p in pts)
        claim = Claim("source_coupling", Status.FEASIBLE, q, scope,
                      detail=f"Vdc stated as battery OCV: judged at the resolved inverter terminal voltage ({text}; "
                             f"resolved at the requirement's target torque)")
        return out, info, (claim,)
    bad = [p for p in pts if p["status"] != "RESOLVED"]
    if info["status"] == "NO_SOLUTION":
        claim = Claim("source_coupling", Status.INFEASIBLE, q, scope, reasons=(Reason.NECESSARY_CONDITION_VIOLATED,),
                      detail=bad[0]["reason"] + " (the drive-side conditions below are shown at the OCV, without "
                                               "a source drop)")
    else:
        why = (Reason.OUTSIDE_MODEL_DOMAIN,) if any(p["status"] == "OUTSIDE_SOURCE_MODEL" for p in bad) else \
            (Reason.NUMERICAL_UNRESOLVED,)
        claim = Claim("source_coupling", Status.UNKNOWN, q, scope, reasons=why,
                      detail=bad[0]["reason"] + " (no terminal voltage resolved: the drive-side conditions below are "
                                               "shown at the OCV, without a source drop - optimistic)")
    return base, info, (claim,)


def evaluate_decision(case_dict: dict):
    """(DecisionRecord, Case): the verdict of a case, before the optional analyses it asks for."""
    case_dict, source_info, extra = resolve_case_source(case_dict)
    case = case_from_dict(case_dict)
    rec = evaluate_requirement(case.requirement, case.drive, scenario=case.scenario, source_limits=case.limits,
                               ratings=case.ratings, extra_claims=extra, source_coupling=source_info)
    return rec, case


def pending_analyses(rec, case) -> list[str]:
    """The optional analyses the case asks for that will run after its verdict (``decision_analyses``), in order."""
    an = case.analyses
    out = ["sizing"] * len(an.get("sizing") or ())
    if an.get("dominance"):
        out.append("dominance")
    if an.get("relaxation") and rec.verdict.status.value == "INFEASIBLE":
        out.append("relaxation")
    if an.get("compare_Vdc"):
        out.append("comparison")
    return out


def decision_analyses(rec, case) -> dict:
    """The optional analyses around a verdict: sizing (one per parameter), dominance, relaxation (only after a proven
    FAIL) and a Vdc comparison, each at the verdict's first condition."""
    req = case.requirement
    an = case.analyses
    base_sc = rec.conditions[0].scenario
    analyses = {}
    with progress.span(len(pending_analyses(rec, case)), "analyses") as steps:
        for s in an.get("sizing") or ():
            steps.step("sizing")
            analyses.setdefault("sizing", []).append(
                size_parameter(case.drive, base_sc, req.target_Nm, s["parameter"], tuple(s["range"]),
                               int(s.get("samples", 41))).to_dict())
        if an.get("dominance"):
            steps.step("dominance")
            analyses["dominance"] = capability_dominance(case.drive, base_sc, req.direction).to_dict()
        if an.get("relaxation") and rec.verdict.status.value == "INFEASIBLE":
            steps.step("relaxation")
            analyses["relaxation"] = requirement_relaxation(case.drive, base_sc, req.target_Nm).to_dict()
        if an.get("compare_Vdc"):
            steps.step("comparison")
            scs = [base_sc.with_(Vdc_V=float(v), scenario_id=f"Vdc={float(v):g} V") for v in an["compare_Vdc"]]
            analyses["comparison"] = compare_scenarios(case.drive, scs, req.target_Nm)
    return analyses


def decision_record(rec, case, analyses: dict, t0: float) -> dict:
    """The record dict of a verdict and its analyses (what ``evaluate_case_full`` returns first); ``elapsed_s`` counts
    from ``t0`` (``time.perf_counter``)."""
    out = rec.to_dict()
    out["analyses"] = _jsonable(analyses)
    out["unit_conversions"] = _jsonable(case.conversions.records)
    out["markdown"] = rec.to_markdown()
    out["elapsed_s"] = time.perf_counter() - t0
    return out


def evaluate_case_full(case_dict: dict):
    """(record dict, DecisionRecord, Case): the objects are kept for plotting the operating points."""
    t0 = time.perf_counter()
    rec, case = evaluate_decision(case_dict)
    return decision_record(rec, case, decision_analyses(rec, case), t0), rec, case


def forward(drive: DriveModel, limits: DcSourceLimits, speed_rpm: float, Vdc_V: float, id_A: float, iq_A: float) -> dict:
    sc = Scenario("forward", speed_rpm, Vdc_V, limits)
    return _jsonable(forward_evaluation(drive, sc, id_A, iq_A).to_dict())


def solve(drive: DriveModel, limits: DcSourceLimits, speed_rpm: float, Vdc_V: float, T: float) -> dict:
    sc = Scenario("solve", speed_rpm, Vdc_V, limits)
    return _jsonable(PolicyEvaluator(drive, sc).solve(T).to_dict())


def capability(drive: DriveModel, limits: DcSourceLimits, speed_rpm: float, Vdc_V: float, direction: int,
               kind: str = "policy") -> dict:
    ev = PolicyEvaluator(drive, Scenario("cap", speed_rpm, Vdc_V, limits))
    if kind == "policy":
        r = policy_capability(ev, direction)
    else:
        r = physical_capability(ev, direction, include_dc=(kind == "physical"))
    return _jsonable(r.to_dict())


def capability_curve(drive: DriveModel, limits: DcSourceLimits, Vdc_V: float, speeds=None,
                     settings: NumericalSettings = CURVE_SETTINGS) -> dict:
    """T-n envelopes: minimum-current policy (incl. DC) and electrical (V/I/domain only), both directions."""
    lo, hi = drive.domain.speed_rpm
    if speeds is None:
        top = max(abs(lo), abs(hi))
        speeds = list(np.linspace(0.0, top, 17))
    rows = []
    for n in speeds:
        ev = PolicyEvaluator(drive, Scenario("curve", float(n), Vdc_V, limits), settings)
        row = {"speed_rpm": float(n)}
        for direction, tag in ((1, "max"), (-1, "min")):
            try:
                pc = policy_capability(ev, direction, certify=False)
                el = physical_capability(ev, direction, include_dc=False)
                row[f"policy_{tag}_Nm"] = pc.value_Nm
                row[f"electrical_{tag}_Nm"] = el.value_Nm
                row[f"policy_{tag}_limited_by"] = list(pc.active_constraints)
                row[f"policy_{tag}_segments"] = [list(s) for s in pc.segments]
            except (OutsideModelDomain, InputValidationError) as exc:  # pragma: no cover - defensive
                row[f"policy_{tag}_Nm"] = None
                row[f"error_{tag}"] = str(exc)
        rows.append(row)
    return _jsonable({"Vdc_V": Vdc_V, "rows": rows,
                      "note": "sampled speed grid; policy boundary from a torque scan + bisection per speed; "
                              "between speed samples the envelope is not established"})


# ---------------------------------------------------------------------------
# id-iq constraint map
# ---------------------------------------------------------------------------

def contour_segments(x: np.ndarray, y: np.ndarray, F: np.ndarray, level: float = 0.0, digits: int = 3) -> list:
    """Marching squares: line segments of F(x_i, y_j) = level (F indexed [i, j])."""
    G = F - level
    a, b, c, d = G[:-1, :-1], G[1:, :-1], G[1:, 1:], G[:-1, 1:]      # corners: (i,j) (i+1,j) (i+1,j+1) (i,j+1)
    X0, X1 = x[:-1][:, None], x[1:][:, None]
    Y0, Y1 = y[:-1][None, :], y[1:][None, :]
    segs = []

    def cross(fa, fb):
        with np.errstate(divide="ignore", invalid="ignore"):
            return fa / (fa - fb)

    edges = [
        ((a, b), lambda t: (X0 + t * (X1 - X0), Y0 + 0 * t)),   # bottom (j fixed)
        ((b, c), lambda t: (X1 + 0 * t, Y0 + t * (Y1 - Y0))),   # right
        ((d, c), lambda t: (X0 + t * (X1 - X0), Y1 + 0 * t)),   # top
        ((a, d), lambda t: (X0 + 0 * t, Y0 + t * (Y1 - Y0))),   # left
    ]
    pts = []
    has = []
    for (fa, fb), fn in edges:
        m = np.isfinite(fa) & np.isfinite(fb) & ((fa > 0) != (fb > 0))
        t = np.where(m, cross(fa, fb), 0.0)
        px, py = fn(t)
        pts.append((np.broadcast_to(px, m.shape), np.broadcast_to(py, m.shape)))
        has.append(m)
    cnt = sum(h.astype(int) for h in has)
    ii, jj = np.nonzero(cnt == 2)
    for i, j in zip(ii, jj):
        p = [(float(pts[e][0][i, j]), float(pts[e][1][i, j])) for e in range(4) if has[e][i, j]]
        segs.append([round(p[0][0], digits), round(p[0][1], digits), round(p[1][0], digits), round(p[1][1], digits)])
    ii, jj = np.nonzero(cnt == 4)
    for i, j in zip(ii, jj):
        p = [(float(pts[e][0][i, j]), float(pts[e][1][i, j])) for e in range(4)]
        centre = 0.25 * (a[i, j] + b[i, j] + c[i, j] + d[i, j])
        pairs = ((0, 1), (2, 3)) if (centre > 0) == (a[i, j] > 0) else ((0, 3), (1, 2))
        for u, v in pairs:
            segs.append([round(p[u][0], digits), round(p[u][1], digits), round(p[v][0], digits), round(p[v][1], digits)])
    return segs


def _runs(xs, ys, mask) -> list:
    """Row-wise runs [x0, x1, y0, y1] of a boolean mask[i (x), j (y)] for compact region drawing."""
    out = []
    dx = xs[1] - xs[0] if xs.size > 1 else 1.0
    dy = ys[1] - ys[0] if ys.size > 1 else 1.0
    for j in range(ys.size):
        col = mask[:, j]
        i = 0
        while i < xs.size:
            if not col[i]:
                i += 1
                continue
            k = i
            while k + 1 < xs.size and col[k + 1]:
                k += 1
            out.append([round(float(xs[i] - dx / 2), 3), round(float(xs[k] + dx / 2), 3),
                        round(float(ys[j] - dy / 2), 3), round(float(ys[j] + dy / 2), 3)])
            i = k + 1
    return out


def idiq_map(drive: DriveModel, limits: DcSourceLimits, speed_rpm: float, Vdc_V: float, T: float,
             resolution: int = 161) -> dict:
    sc = Scenario("map", speed_rpm, Vdc_V, limits)
    ev = PolicyEvaluator(drive, sc)
    k = ev.k
    dom = drive.domain
    span = 1.08 * k.Imax
    x = np.linspace(max(dom.id_A[0], -span) - 0.05 * span, min(dom.id_A[1], span) + 0.05 * span, resolution)
    y = np.linspace(max(dom.iq_A[0], -span) - 0.05 * span, min(dom.iq_A[1], span) + 0.05 * span, resolution)
    X, Y = np.meshgrid(x, y, indexing="ij")
    e = k.evaluate(X, Y)
    ok = np.asarray(e["ok"], bool)
    nanify = lambda A: np.where(ok, A, np.nan)
    vmag = nanify(np.sqrt(e["vcmd2"]))
    out = {
        "speed_rpm": speed_rpm, "Vdc_V": Vdc_V, "T_request_Nm": T,
        "axes": {"id_A": [float(x[0]), float(x[-1])], "iq_A": [float(y[0]), float(y[-1])]},
        "domain_box": {"id_A": list(dom.id_A), "iq_A": list(dom.iq_A)},
        "current_limit_A": k.Imax,
        "contours": {
            "voltage_budget": contour_segments(x, y, vmag, k.Vb),
            "torque_request": contour_segments(x, y, nanify(e["tsh"]), T) if k.tau_rot is not None else [],
        },
    }
    if k.i2_dc is not None:                 # grid P_dc only through the surrogate's I^2 identity
        pdc = nanify(e["pdc"])
        if k.P_dis_eff is not None:
            out["contours"]["dc_discharge_limit"] = contour_segments(x, y, pdc, k.P_dis_eff)
        if k.P_chg_eff is not None:
            out["contours"]["dc_charge_limit"] = contour_segments(x, y, pdc, -k.P_chg_eff)
    coarse = max(41, resolution // 3)
    xc = np.linspace(x[0], x[-1], coarse)
    yc = np.linspace(y[0], y[-1], coarse)
    Xc, Yc = np.meshgrid(xc, yc, indexing="ij")
    ec = k.evaluate(Xc, Yc)
    el = electrical_ok(k, Xc, Yc, ec)
    out["regions"] = {"electrical_feasible": _runs(xc, yc, el)}
    if k.i2_dc is not None:
        out["regions"]["all_limits_feasible"] = _runs(xc, yc, el & dc_ok(k, ec["pdc"]))
    elif k.pointwise_loss:
        out["dc_grid_note"] = (f"DC limit contours and regions not drawn: the {k.loss_label} is evaluated point by "
                               "point (no grid P_dc); DC compatibility is judged at the policy point")
    if k.kind == "flux_map":
        out["regions"]["model_coverage"] = _runs(xc, yc, np.asarray(ec["ok"], bool))
    sol = ev.solve(T)
    out["policy_point"] = None if sol.point is None else {"id_A": sol.point.id_A, "iq_A": sol.point.iq_A}
    out["active_loss_candidate"] = None if sol.active_loss_candidate is None else {
        "id_A": sol.active_loss_candidate.id_A, "iq_A": sol.active_loss_candidate.iq_A}
    out["claims"] = {c.name: c.status.value for c in sol.claims}
    return _jsonable(out)


@lru_cache(maxsize=1)
def acceptance_summary() -> dict:
    """Live comparison of production output with the immutable golden fixtures."""
    from .physics import evaluate_point
    t0 = time.perf_counter()
    rows = []
    fwd = sf.load("golden_forward.json")
    fmap = {"i_peak_A": "i_peak_A", "vd_V_peak": "vd_V", "vq_V_peak": "vq_V", "v_peak_V": "v_peak_V",
            "Te_Nm": "Te_Nm", "Tshaft_Nm": "Tshaft_Nm", "Pdc_W": "Pdc_W", "Pac_W": "Pac_W"}
    for c in fwd["cases"]:
        d = sf.synthetic_drive(c.get("parameter_overrides"))
        inp = c["input"]
        pt = evaluate_point(DriveKernel(d, sf.synthetic_scenario(inp["n_rpm"], inp["Vdc_V"])),
                            inp["id_A_peak"], inp["iq_A_peak"])
        worst = max(abs(getattr(pt, a) - c["expected"][g]) / max(1.0, abs(c["expected"][g])) for g, a in fmap.items())
        rows.append({"group": "forward", "case": c["case_id"], "metric": "max normalised discrepancy",
                     "value": worst, "tolerance": 1e-10, "pass": worst <= 1e-10,
                     "labels_ok": sorted(pt.violated_groups()) == sorted(c["expected_violations"])})
    inv = sf.load("golden_inverse.json")
    drive = sf.synthetic_drive()
    for c in inv["cases"]:
        inp = c["input"]
        sol = PolicyEvaluator(drive, sf.synthetic_scenario(inp["n_rpm"], inp["Vdc_V"])).solve(inp["Tshaft_requested_Nm"])
        e = c["expected_policy_solution"]
        labels = (sol.electrical.status.value == c["electrical_existence"]
                  and sol.policy_claim.status.value == c["minimum_current_policy_with_dc"])
        if e is None:
            rows.append({"group": "inverse", "case": c["case_id"], "metric": "no solution (proof)",
                         "value": None, "tolerance": None, "pass": sol.point is None and labels, "labels_ok": labels})
            continue
        err = max(abs(sol.point.id_A - e["id_A_peak"]), abs(sol.point.iq_A - e["iq_A_peak"]))
        rows.append({"group": "inverse", "case": c["case_id"], "metric": "max |d id|, |d iq| (A)", "value": err,
                     "tolerance": 1e-3, "pass": err <= 1e-3 and labels, "labels_ok": labels,
                     "dPdc_W": abs(sol.point.Pdc_W - e["Pdc_W"])})
    cap = sf.load("golden_capability.json")
    for c in cap["cases"]:
        ev = PolicyEvaluator(drive, sf.synthetic_scenario(c["n_rpm"], c["Vdc_V"]))
        r = policy_capability(ev, 1 if c["expected_Tshaft_Nm"] > 0 else -1)
        err = abs(r.value_Nm - c["expected_Tshaft_Nm"])
        rows.append({"group": "capability", "case": f"{c['n_rpm']} rpm / {c['Vdc_V']} V "
                                                    f"({'+' if c['expected_Tshaft_Nm'] > 0 else '-'})",
                     "metric": "|d T| (N*m)", "value": err, "tolerance": r.gap_tolerance_Nm,
                     "pass": err <= r.gap_tolerance_Nm, "certified": r.certified, "labels_ok": True})
    manifest = sf.verify_manifest()
    return _jsonable({
        "software_version": __version__,
        "manifest": manifest,
        "manifest_ok": all(m["ok"] for m in manifest),
        "rows": rows,
        "all_pass": all(r["pass"] for r in rows),
        "elapsed_s": time.perf_counter() - t0,
        "scope": "verification against synthetic fixtures only; no hardware, supplier-data or external-simulator "
                 "validation has been performed",
    })
