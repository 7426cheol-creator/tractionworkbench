"""Supplied static current-policy evaluation (Handoff H5.4, UC07).

The given current map is evaluated as it is: its (id, iq) at the request are
passed to the forward evaluation.  A hole, an undefined quadrant, a request
outside the table or a Vdc the table was not calibrated for gives UNKNOWN
(POLICY_LIMITATION).  A physically better point found by another policy is
reported as a policy gap and never substituted, so an existing policy's
failure is not overwritten by another policy's success.  Static success is
not a proof of closed-loop dynamics, estimation or transient behaviour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..errors import InputValidationError, OutsideModelDomain
from ..models.components import DriveModel
from ..models.flux import _axis, _finite, _interval
from ..models.provenance import Provenance
from ..physics import DriveKernel, evaluate_point
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.policy import PolicyEvaluator
from ..status import Claim, Evidence, EvidenceKind, Reason, Status


@dataclass(frozen=True, eq=False)
class CurrentPolicyTable:
    policy_id: str
    revision: str
    torque_axis_Nm: np.ndarray
    speed_axis_rpm: np.ndarray
    id_A: np.ndarray
    iq_A: np.ndarray
    provenance: Provenance
    valid: np.ndarray | None = None
    Vdc_valid_V: tuple[float, float] | None = None
    torque_command_basis: str = "shaft"
    torque_tolerance_Nm: float = 0.5

    def __post_init__(self):
        ta = _axis("torque_axis_Nm", self.torque_axis_Nm)
        sa = _axis("speed_axis_rpm", self.speed_axis_rpm)
        shape = (ta.size, sa.size)
        arrs = {}
        for name in ("id_A", "iq_A"):
            a = np.array(getattr(self, name), dtype=float)
            if a.shape != shape:
                raise InputValidationError(f"shape {a.shape} does not match axes {shape} (rows torque, cols speed)",
                                           field=name)
            arrs[name] = a
        m = np.ones(shape, bool) if self.valid is None else np.array(self.valid, dtype=bool)
        if m.shape != shape:
            raise InputValidationError("validity mask shape mismatch", field="valid")
        for name, a in arrs.items():
            if np.any(m & ~np.isfinite(a)):
                raise InputValidationError("non-finite value at a valid node", field=name)
        if self.torque_command_basis not in ("shaft", "electromagnetic"):
            raise InputValidationError("torque_command_basis must be 'shaft' or 'electromagnetic'",
                                       field="torque_command_basis")
        if self.Vdc_valid_V is not None:
            object.__setattr__(self, "Vdc_valid_V", _interval("Vdc_valid_V", self.Vdc_valid_V))
        tol = _finite("torque_tolerance_Nm", self.torque_tolerance_Nm)
        if tol < 0:
            raise InputValidationError("torque tolerance must be >= 0", field="torque_tolerance_Nm")
        object.__setattr__(self, "torque_tolerance_Nm", tol)
        object.__setattr__(self, "torque_axis_Nm", ta)
        object.__setattr__(self, "speed_axis_rpm", sa)
        object.__setattr__(self, "id_A", arrs["id_A"])
        object.__setattr__(self, "iq_A", arrs["iq_A"])
        object.__setattr__(self, "valid", m)
        object.__setattr__(self, "cell_valid", m[:-1, :-1] & m[1:, :-1] & m[:-1, 1:] & m[1:, 1:])

    def lookup(self, torque_cmd: float, speed_rpm: float, Vdc: float) -> tuple[float, float]:
        if self.Vdc_valid_V is not None and not (self.Vdc_valid_V[0] <= Vdc <= self.Vdc_valid_V[1]):
            raise OutsideModelDomain(f"policy table calibrated for Vdc in {list(self.Vdc_valid_V)} V, not {Vdc:g} V")
        ta, sa = self.torque_axis_Nm, self.speed_axis_rpm
        if not (ta[0] <= torque_cmd <= ta[-1] and sa[0] <= speed_rpm <= sa[-1]):
            raise OutsideModelDomain(f"request ({torque_cmd:g} N*m, {speed_rpm:g} rpm) outside the policy table axes")
        i = int(np.clip(np.searchsorted(ta, torque_cmd, side="right") - 1, 0, ta.size - 2))
        j = int(np.clip(np.searchsorted(sa, speed_rpm, side="right") - 1, 0, sa.size - 2))
        if not self.cell_valid[i, j]:
            raise OutsideModelDomain(f"policy table hole at ({torque_cmd:g} N*m, {speed_rpm:g} rpm)")
        t = (torque_cmd - ta[i]) / (ta[i + 1] - ta[i])
        u = (speed_rpm - sa[j]) / (sa[j + 1] - sa[j])

        def bil(a):
            return ((1 - t) * (1 - u) * a[i, j] + t * (1 - u) * a[i + 1, j] + (1 - t) * u * a[i, j + 1]
                    + t * u * a[i + 1, j + 1])

        return float(bil(self.id_A)), float(bil(self.iq_A))


def evaluate_supplied_policy(table: CurrentPolicyTable, drive: DriveModel, scenario: Scenario, T_request: float,
                             settings: NumericalSettings = DEFAULT_SETTINGS, compare_min_current: bool = True) -> dict:
    k = DriveKernel(drive, scenario, settings)
    scope = f"supplied static policy {table.policy_id} rev {table.revision}; static fundamental steady state"
    q = f"static achievement of {T_request:g} N*m at {scenario.speed_rpm:g} rpm by the supplied current map"
    cmd = T_request
    if table.torque_command_basis == "electromagnetic":
        if k.tau_rot is None:
            claim = Claim("supplied_policy", Status.UNKNOWN, q, scope, table.policy_id, reasons=(Reason.MISSING_INPUT,),
                          detail="electromagnetic-torque table needs the rotational loss to map shaft torque")
            return {"claim": claim.to_dict(), "operating_point": None, "policy_gap": None}
        cmd = T_request + k.tau_rot
    try:
        idq = table.lookup(cmd, scenario.speed_rpm, scenario.Vdc_V)
    except OutsideModelDomain as exc:
        claim = Claim("supplied_policy", Status.UNKNOWN, q, scope, table.policy_id, reasons=(Reason.POLICY_LIMITATION,),
                      evidence=(Evidence.make(EvidenceKind.DIRECT_EVALUATION, str(exc)),),
                      detail="the supplied policy does not define a command here; the physical optimum is NOT "
                             "substituted")
        return {"claim": claim.to_dict(), "operating_point": None, "policy_gap": None}
    try:
        pt = evaluate_point(k, *idq)
    except OutsideModelDomain as exc:
        claim = Claim("supplied_policy", Status.UNKNOWN, q, scope, table.policy_id,
                      reasons=(Reason.OUTSIDE_MODEL_DOMAIN,),
                      evidence=(Evidence.make(EvidenceKind.DIRECT_EVALUATION, str(exc)),),
                      detail="the commanded currents lie outside the motor model domain")
        return {"claim": claim.to_dict(), "operating_point": None, "policy_gap": None}
    terr = None if pt.Tshaft_Nm is None else pt.Tshaft_Nm - T_request
    viol = [c.name for c in pt.violations()]
    ev = [Evidence.make(EvidenceKind.DIRECT_EVALUATION,
                        f"table command id = {idq[0]:.6g} A, iq = {idq[1]:.6g} A -> T_shaft = {pt.Tshaft_Nm:.6g} N*m")]
    if terr is None:
        st, reasons, detail = Status.UNKNOWN, (Reason.MISSING_INPUT,), "shaft torque undefined (rotational loss missing)"
    elif abs(terr) > table.torque_tolerance_Nm:
        st, reasons = Status.INFEASIBLE, (Reason.CONSTRAINT_VIOLATION,)
        detail = f"the supplied policy delivers {pt.Tshaft_Nm:.6g} N*m (error {terr:+.4g} N*m > tolerance " \
                 f"{table.torque_tolerance_Nm:g} N*m)"
    elif viol:
        st, reasons, detail = Status.INFEASIBLE, (Reason.CONSTRAINT_VIOLATION,), "violations: " + ", ".join(viol)
    else:
        st, reasons, detail = Status.FEASIBLE, (), "static command meets every constraint (not a dynamics proof)"
    claim = Claim("supplied_policy", st, q, scope, table.policy_id, reasons=reasons, evidence=tuple(ev), detail=detail)
    gap = None
    if compare_min_current:
        mc = PolicyEvaluator(drive, scenario, settings).solve(T_request)
        mp = mc.point
        gap = {
            "minimum_current_policy_status": mc.policy_claim.status.value,
            "note": "comparison only: the minimum-current result does not replace the supplied policy's status",
        }
        if mp is not None:
            gap.update({
                "extra_current_A": pt.i_peak_A - mp.i_peak_A,
                "extra_loss_W": (None if pt.Pdc_W is None or mp.Pdc_W is None or pt.Pshaft_W is None or mp.Pshaft_W is None
                                 else (pt.Pdc_W - pt.Pshaft_W) - (mp.Pdc_W - mp.Pshaft_W)),
                "supplied_voltage_margin_V": pt.voltage_margin_V,
                "minimum_current_voltage_margin_V": mp.voltage_margin_V,
                "supplied_id_A": pt.id_A, "minimum_current_id_A": mp.id_A,
            })
    return {"claim": claim.to_dict(), "operating_point": pt.to_dict(), "torque_error_Nm": terr, "policy_gap": gap}
