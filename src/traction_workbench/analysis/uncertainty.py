"""Bounded-input analysis (Blueprint 7.4, Handoff H6).

Parameters are given as intervals; corners (and the centre) are examined.
Two robustness notions are kept apart:

* adaptive feasibility  (for all u there exists x): the policy is re-solved
  for every corner - this silently assumes the controller knows u;
* fixed-policy robustness (for all u: g(pi(y), u) <= 0): the nominal
  calibration (id, iq) is applied unchanged to every corner.

Corner sampling alone never proves a worst case over the continuous box.  The
vertex certificate (``vertex_certificate``) does, where it applies: at a FIXED
operating point every quantity the witness gate judges is, in each box
parameter with the others held, affine, the norm of an affine vector (convex)
or monotone, so its worst case over the box sits at a corner (engineering
review 2 of 63a2b61, F-18).  A calibration that passes at every corner then
passes over the whole continuous box, and when no box parameter moves the
torque of a fixed current point, that one point also settles the adaptive
claim.  Without the certificate an all-pass result stays "feasible at the
examined corners" (UNKNOWN for the robust claim).  A failing corner is a
counterexample only when the interval is an *admissible set* (every value
realisable); for an outer enclosure it proves nothing.  No probability is
attached to an interval.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from ..errors import InputValidationError, OutsideModelDomain
from ..models.components import DriveModel, RotationalLossModel
from ..models.flux import ConstantFluxModel
from ..physics import DriveKernel, evaluate_point
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.gate import HARD_GROUPS, check_witness, torque_tolerance
from ..solvers.policy import PolicyEvaluator
from ..status import Claim, Evidence, EvidenceKind, Reason, Status
from .variation import PARAMETERS, apply

# box parameters that move the shaft torque of a fixed (id, iq): T_em = 1.5 p (psi iq + (Ld - Lq) id iq) and
# T_shaft = T_em - s_rot tau_rot(w)
TORQUE_MOVING = ("psi_pm_Wb", "Ld_H", "Lq_H", "rotational_loss_scale")
MAX_COMMON_CANDIDATES = 8


@dataclass(frozen=True)
class ParameterInterval:
    name: str
    low: float
    high: float
    kind: str = "admissible"     # "admissible" | "outer_enclosure"
    basis: str = ""

    def __post_init__(self):
        if self.name not in PARAMETERS:
            raise InputValidationError(f"unknown parameter {self.name!r}", field="name")
        if not (math.isfinite(self.low) and math.isfinite(self.high)) or self.low > self.high:
            raise InputValidationError("interval must be finite with low <= high", field=self.name)
        if self.kind not in ("admissible", "outer_enclosure"):
            raise InputValidationError("kind must be 'admissible' or 'outer_enclosure'", field="kind")


def _fixed_calibration(d: DriveModel, s: Scenario, settings: NumericalSettings, id_A: float, iq_A: float,
                       T_request: float, accuracy_Nm: float | None) -> tuple[str, float | None, list, list]:
    """The nominal calibration applied unchanged: same witness gate as every other path (review DV-03).

    VIOLATED -> INFEASIBLE; NOT_EVALUATED, an undeclared DC limit that can bind, missing loss data or model issues
    -> UNKNOWN.  The torque error is judged against the stated accuracy, never against the numerical residual.
    """
    k = DriveKernel(d, s, settings)
    if k.issues:
        return "UNKNOWN", None, [], [i.message for i in k.issues]
    try:
        fp = evaluate_point(k, id_A, iq_A)
    except OutsideModelDomain as exc:
        return "UNKNOWN", None, [], [str(exc)]
    terr = None if fp.Tshaft_Nm is None else fp.Tshaft_Nm - T_request
    viol = [c.name for c in fp.violations()]
    gate = check_witness(k, id_A, iq_A, point=fp, groups=HARD_GROUPS, require_dc=True, include_validity=False)
    not_eval = [m for m in gate.messages if "not evaluated" in m or "not declared" in m or "undefined" in m]
    num_tol = torque_tolerance(k)
    if viol:
        return "INFEASIBLE", terr, viol, []
    if terr is None:
        return "UNKNOWN", terr, viol, ["shaft torque undefined (rotational loss missing)"]
    if accuracy_Nm is not None and abs(terr) > accuracy_Nm:
        return "INFEASIBLE", terr, viol, [f"torque error {terr:+.4g} N*m > stated accuracy {accuracy_Nm:g} N*m"]
    if accuracy_Nm is None and abs(terr) > num_tol:
        return "UNKNOWN", terr, viol, [f"torque error {terr:+.4g} N*m; no torque-accuracy requirement stated "
                                       f"(REQUIREMENT_INCOMPLETE; the numerical residual is not a requirement)"]
    if not_eval:
        return "UNKNOWN", terr, viol, not_eval
    return "FEASIBLE", terr, viol, []


def vertex_certificate(drive: DriveModel, intervals: list[ParameterInterval],
                       torque_accuracy_Nm: float | None) -> dict:
    """Is the worst case over the continuous box, at a FIXED operating point, attained at a corner?

    At a fixed (id, iq), with the others held, each box parameter enters every quantity the witness gate judges as
    follows: v_cmd = ((Rs + R_drop) id - w Lq iq, (Rs + R_drop) iq + w (psi + Ld id)) is affine in Rs, psi, Ld and Lq
    one at a time, so |v_cmd| is convex in each; P_ac = 1.5 (Rs I^2 + w psi iq + w (Ld - Lq) id iq) and P_dc = P_ac +
    s_inv (a0 + a2 I^2) are affine; I_dc = P_dc / Vdc is monotone in Vdc; T_shaft = 1.5 p (psi iq + (Ld - Lq) id iq)
    - s_rot tau_rot(w) is affine, so a torque error |T_shaft - T| is convex; every limit ((1 - r_v) k Vdc / sqrt 3,
    I_max, the DC limits, id_min) and its tolerance are monotone in their own parameter, and no parameter enters both
    the demand and the limit of one constraint.  A function that is convex or monotone along every coordinate attains
    its maximum over a box at a vertex, so a calibration that passes at every corner passes over the whole box.

    It needs the datasheet module loss to be absent (a pointwise loss moves with the modulation index and the power
    factor), a speed-only rotational loss, psi_PM / Ld / Lq only on the constant-parameter model (the variation layer
    refuses them on a flux map), and - when a box parameter moves the torque of a fixed point - a stated torque
    accuracy (the numerical torque tolerance itself scales with psi, Ld, Lq).  The same point serves every parameter
    value (the adaptive claim) only when no box parameter moves its torque."""
    names = [iv.name for iv in intervals]
    moving = [n for n in names if n in TORQUE_MOVING]
    inv, m = drive.inverter, drive.motor
    checks = []

    def chk(name, ok, detail):
        checks.append({"condition": name, "holds": bool(ok), "detail": detail})
    chk("inverter loss of the form s (a0 + a2 I^2) or none (not the pointwise datasheet module loss)",
        inv.module_loss is None, "datasheet module loss" if inv.module_loss is not None else
        ("none" if inv.loss is None else "a0 + a2 I^2"))
    chk("speed-only rotational loss b w + c w|w| (or none)",
        m.rotational_loss is None or isinstance(m.rotational_loss, RotationalLossModel), "speed-only")
    flux_params = [n for n in names if n in ("psi_pm_Wb", "Ld_H", "Lq_H")]
    chk("psi_PM, Ld, Lq varied only on the constant-parameter model (affine flux linkage)",
        not flux_params or isinstance(m.flux, ConstantFluxModel),
        ", ".join(flux_params) if flux_params else "no flux parameter in the box")
    chk("a torque-moving parameter needs a stated torque accuracy (a fixed threshold)",
        not moving or torque_accuracy_Nm is not None,
        (", ".join(moving) + (f": accuracy {torque_accuracy_Nm:g} N*m" if torque_accuracy_Nm is not None else
                              ": no accuracy stated")) if moving else "no box parameter moves the torque of a point")
    applies = all(c["holds"] for c in checks)
    return {"applies": applies, "checks": checks, "single_point": applies and not moving, "torque_moving": moving,
            "statement": ("at a fixed operating point every gate quantity is affine, convex (the norm of an affine "
                          "vector) or monotone in each box parameter with the others held, so its worst case over "
                          "the continuous box is at a corner: a point that passes at every corner passes everywhere "
                          "in the box")}


def bounded_input_analysis(drive: DriveModel, scenario: Scenario, T_request: float,
                           intervals: list[ParameterInterval], include_center: bool = True,
                           settings: NumericalSettings = DEFAULT_SETTINGS,
                           torque_accuracy_Nm: float | None = None) -> dict:
    """``torque_accuracy_Nm``: stated torque-accuracy requirement for the fixed-calibration check (None = not
    stated: a torque error above the numerical residual is then UNKNOWN, not a counterexample)."""
    if torque_accuracy_Nm is not None and (not math.isfinite(torque_accuracy_Nm) or torque_accuracy_Nm < 0):
        raise InputValidationError("torque accuracy must be finite and >= 0", field="torque_accuracy_Nm")
    if not intervals:
        raise InputValidationError("at least one parameter interval is required", field="intervals")
    if len(intervals) > 8:
        raise InputValidationError("at most 8 intervals (256 corners) per analysis", field="intervals")
    nominal = PolicyEvaluator(drive, scenario, settings).solve(T_request)
    npt = nominal.point
    combos = list(itertools.product(*[(iv.low, iv.high) for iv in intervals]))
    if include_center:
        combos.append(tuple(0.5 * (iv.low + iv.high) for iv in intervals))
    rows, models, points = [], [], []
    for vals in combos:
        d, s = drive, scenario
        for iv, v in zip(intervals, vals):
            d, s = apply(d, s, iv.name, v)
        models.append((d, s))
        sol = PolicyEvaluator(d, s, settings).solve(T_request)
        adaptive = sol.policy_claim.status.value
        fixed, terr, fixed_viol, fixed_notes = None, None, [], []
        if npt is not None:
            fixed, terr, fixed_viol, fixed_notes = _fixed_calibration(d, s, settings, npt.id_A, npt.iq_A, T_request,
                                                                      torque_accuracy_Nm)
        pt = sol.point
        points.append(None if pt is None or adaptive != "FEASIBLE" else pt)
        rows.append({
            "values": {iv.name: v for iv, v in zip(intervals, vals)},
            "adaptive_policy_status": adaptive,
            "adaptive_point": None if pt is None else {"id_A": pt.id_A, "iq_A": pt.iq_A},
            "adaptive_voltage_margin_V": None if pt is None else pt.voltage_margin_V,
            "adaptive_Pdc_W": None if pt is None else pt.Pdc_W,
            "fixed_calibration_status": fixed,
            "fixed_calibration_torque_error_Nm": terr,
            "fixed_calibration_violations": fixed_viol,
            "fixed_calibration_notes": fixed_notes,
        })
    kinds = {iv.kind for iv in intervals}
    admissible = kinds == {"admissible"}
    cert = vertex_certificate(drive, intervals, torque_accuracy_Nm)
    n_corners = 2 ** len(intervals)
    common = None
    if cert["single_point"] and all(r["adaptive_policy_status"] == "FEASIBLE" for r in rows):
        # one operating point for the whole box: the nominal calibration first, then the corner witnesses from the
        # largest current down (the most demanding corner's point is the likeliest to serve every corner)
        cands = ([] if npt is None else [("nominal calibration", npt)]) + [
            (f"witness at {rows[i]['values']}", p) for i, p in sorted(
                ((i, p) for i, p in enumerate(points[:n_corners]) if p is not None), key=lambda x: -x[1].i_peak_A)]
        for label, p in cands[:MAX_COMMON_CANDIDATES]:
            if all(_fixed_calibration(d, s, settings, p.id_A, p.iq_A, T_request, torque_accuracy_Nm)[0] == "FEASIBLE"
                   for d, s in models[:n_corners]):
                common = (label, p)
                break

    def robust_claim(key, name, meaning):
        sts = [r[key] for r in rows]
        bad = [r for r in rows if r[key] == "INFEASIBLE"]
        q = f"{T_request:g} N*m for every parameter value in the box ({meaning})"
        scope = "corner (+centre) sampling of the declared intervals"
        if not bad and cert["applies"]:
            fixed_all = key == "fixed_calibration_status" and all(x == "FEASIBLE" for x in sts)
            if fixed_all or (key == "adaptive_policy_status" and common is not None):
                label, p = (("nominal calibration", npt) if fixed_all else common)
                return Claim(name, Status.FEASIBLE, q, "the continuous box (vertex certificate)",
                             evidence=(Evidence.make(EvidenceKind.CERTIFIED_BOUND,
                                                     f"vertex certificate: {cert['statement']}; the {label} "
                                                     f"(id {p.id_A:.4g} A, iq {p.iq_A:.4g} A) passes the witness "
                                                     f"gate at all {n_corners} corners"),),
                             qualifiers=(("one operating point serves every parameter value in the box",)
                                         if key == "adaptive_policy_status" else ()),
                             detail=(f"every parameter value in the box meets the requirement with the {label}: "
                                     "the worst case over the continuous box is at a corner (vertex certificate)"))
        if bad:
            if admissible:
                return Claim(name, Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,),
                             evidence=(Evidence.make(EvidenceKind.NUMERICAL_WITNESS,
                                                     f"admissible counterexample: {bad[0]['values']}"),),
                             detail="a realisable parameter combination violates the requirement")
            return Claim(name, Status.UNKNOWN, q, scope, reasons=(Reason.UNCERTAINTY_OVERLAP,),
                         evidence=(Evidence.make(EvidenceKind.SAMPLED, f"violation at enclosure corner {bad[0]['values']}"),),
                         detail="violation at a corner of an outer enclosure is not a proven realisable counterexample")
        if all(x == "FEASIBLE" for x in sts):
            why = ("; ".join(c["condition"] for c in cert["checks"] if not c["holds"]) if not cert["applies"] else
                   f"{', '.join(cert['torque_moving'])} move(s) the torque of a fixed point: no single operating "
                   f"point serves the box, and the policy point moves with the parameters"
                   if key == "adaptive_policy_status" and not cert["single_point"] else
                   "no single operating point passed at every corner")
            return Claim(name, Status.UNKNOWN, q, scope, reasons=(Reason.SAMPLED_COVERAGE,),
                         evidence=(Evidence.make(EvidenceKind.SAMPLED, f"{len(rows)} examined combinations feasible"),),
                         qualifiers=("feasible at every examined combination", f"no vertex certificate: {why}"),
                         detail="corner sampling does not establish the worst case over the continuous box")
        return Claim(name, Status.UNKNOWN, q, scope, reasons=(Reason.NUMERICAL_UNRESOLVED,),
                     detail="some combinations could not be decided")

    adaptive = robust_claim("adaptive_policy_status", "robust_adaptive",
                            "adaptive: policy re-solved per combination, assumes the controller knows the parameters")
    fixed = robust_claim("fixed_calibration_status", "robust_fixed_calibration",
                         "fixed nominal calibration applied to every combination")
    sts = [r["adaptive_policy_status"] for r in rows]
    if fixed.status is Status.FEASIBLE:
        # the real system's parameters lie in the declared box (admissible set or outer enclosure), and the nominal
        # calibration meets the requirement for every value in it
        actual = Claim("actual_system", Status.FEASIBLE, f"{T_request:g} N*m for the (single, unknown) real system",
                       "declared intervals", evidence=fixed.evidence,
                       qualifiers=("with the nominal calibration, for every parameter value in the declared box "
                                   "(within this model)",),
                       detail="the real system's parameters lie in the declared box and the nominal calibration meets "
                              "the requirement over the whole box (vertex certificate)")
    elif "FEASIBLE" in sts and "INFEASIBLE" in sts:
        actual = Claim("actual_system", Status.UNKNOWN, f"{T_request:g} N*m for the (single, unknown) real system",
                       "declared intervals", reasons=(Reason.UNCERTAINTY_OVERLAP,),
                       detail="results straddle pass and fail over the declared parameter intervals")
    else:
        actual = Claim("actual_system", Status.UNKNOWN if sts else Status.UNKNOWN,
                       f"{T_request:g} N*m for the (single, unknown) real system", "declared intervals",
                       reasons=(Reason.SAMPLED_COVERAGE,),
                       qualifiers=(f"all examined combinations {sts[0]}" if len(set(sts)) == 1 else "",),
                       detail="sampled corners only; no probability is attached to the intervals")
    return {
        "T_request_Nm": T_request,
        "torque_accuracy_Nm": torque_accuracy_Nm,
        "intervals": [{"name": iv.name, "low": iv.low, "high": iv.high, "kind": iv.kind, "basis": iv.basis}
                      for iv in intervals],
        "nominal_policy_status": nominal.policy_claim.status.value,
        "nominal_calibration": None if npt is None else {"id_A": npt.id_A, "iq_A": npt.iq_A},
        "combinations": rows,
        "vertex_certificate": {**cert, "common_point": None if common is None else {
            "source": common[0], "id_A": common[1].id_A, "iq_A": common[1].iq_A}},
        "claims": [adaptive.to_dict(), fixed.to_dict(), actual.to_dict()],
        "notes": ["intervals are combined as an independent box; express correlated parameters as joint scenarios",
                  "no probability distribution is assumed or reported"],
    }
