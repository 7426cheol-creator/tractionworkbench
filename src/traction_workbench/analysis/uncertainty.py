"""Bounded-input analysis (Blueprint 7.4, Handoff H6).

Parameters are given as intervals; corners (and the centre) are examined.
Two robustness notions are kept apart:

* adaptive feasibility  (for all u there exists x): the policy is re-solved
  for every corner - this silently assumes the controller knows u;
* fixed-policy robustness (for all u: g(pi(y), u) <= 0): the nominal
  calibration (id, iq) is applied unchanged to every corner.

Corner sampling never proves a worst case over the continuous box, so an
all-pass result is reported as "feasible at the examined corners" (UNKNOWN
for the robust claim).  A failing corner is a counterexample only when the
interval is an *admissible set* (every value realisable); for an outer
enclosure it proves nothing.  No probability is attached to an interval.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from ..errors import InputValidationError, OutsideModelDomain
from ..models.components import DriveModel
from ..physics import DriveKernel, evaluate_point
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.gate import HARD_GROUPS, check_witness, torque_tolerance
from ..solvers.policy import PolicyEvaluator
from ..status import Claim, Evidence, EvidenceKind, Reason, Status
from .variation import PARAMETERS, apply


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
    rows = []
    for vals in combos:
        d, s = drive, scenario
        for iv, v in zip(intervals, vals):
            d, s = apply(d, s, iv.name, v)
        sol = PolicyEvaluator(d, s, settings).solve(T_request)
        adaptive = sol.policy_claim.status.value
        fixed, terr, fixed_viol, fixed_notes = None, None, [], []
        if npt is not None:
            fixed, terr, fixed_viol, fixed_notes = _fixed_calibration(d, s, settings, npt.id_A, npt.iq_A, T_request,
                                                                      torque_accuracy_Nm)
        pt = sol.point
        rows.append({
            "values": {iv.name: v for iv, v in zip(intervals, vals)},
            "adaptive_policy_status": adaptive,
            "adaptive_voltage_margin_V": None if pt is None else pt.voltage_margin_V,
            "adaptive_Pdc_W": None if pt is None else pt.Pdc_W,
            "fixed_calibration_status": fixed,
            "fixed_calibration_torque_error_Nm": terr,
            "fixed_calibration_violations": fixed_viol,
            "fixed_calibration_notes": fixed_notes,
        })
    kinds = {iv.kind for iv in intervals}
    admissible = kinds == {"admissible"}

    def robust_claim(key, name, meaning):
        sts = [r[key] for r in rows]
        bad = [r for r in rows if r[key] == "INFEASIBLE"]
        q = f"{T_request:g} N*m for every parameter value in the box ({meaning})"
        scope = "corner (+centre) sampling of the declared intervals"
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
            return Claim(name, Status.UNKNOWN, q, scope, reasons=(Reason.SAMPLED_COVERAGE,),
                         evidence=(Evidence.make(EvidenceKind.SAMPLED, f"{len(rows)} examined combinations feasible"),),
                         qualifiers=("feasible at every examined combination",),
                         detail="corner sampling does not establish the worst case over the continuous box")
        return Claim(name, Status.UNKNOWN, q, scope, reasons=(Reason.NUMERICAL_UNRESOLVED,),
                     detail="some combinations could not be decided")

    adaptive = robust_claim("adaptive_policy_status", "robust_adaptive",
                            "adaptive: policy re-solved per combination, assumes the controller knows the parameters")
    fixed = robust_claim("fixed_calibration_status", "robust_fixed_calibration",
                         "fixed nominal calibration applied to every combination")
    sts = [r["adaptive_policy_status"] for r in rows]
    if "FEASIBLE" in sts and "INFEASIBLE" in sts:
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
        "claims": [adaptive.to_dict(), fixed.to_dict(), actual.to_dict()],
        "notes": ["intervals are combined as an independent box; express correlated parameters as joint scenarios",
                  "no probability distribution is assumed or reported"],
    }
