"""One acceptance gate for every public result (independent review, P0-A: F01/F02/F06).

A number produced by a solver, a scan or a forward evaluation becomes
*evidence* only after it passes the same gate on every call path:

1. model validity - the kernel reports no issue for this scenario (for example
   a stated winding temperature without an Rs reference temperature, or a
   magnet temperature without a validated flux-map plane);
2. coverage - the point lies inside the covered model data (no extrapolation);
3. the original request - the shaft torque *of this point* matches the
   requested torque within the numerical residual tolerance, or lies inside the
   requested band.  A coarse search may end UNKNOWN; it can never hand over a
   point that answers a different question;
4. every required constraint is *evaluated* and satisfied: NOT_EVALUATED is not
   a pass, and a limit that is not declared is not "unlimited" wherever it can
   bind at this point (declare math.inf explicitly for "no limit");
5. the power identities close.

A point that fails is still returned with its numbers as a diagnostic; it is
never a feasible witness.  The torque tolerance is a numerical budget, not a
customer torque-accuracy requirement.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import OutsideModelDomain
from ..physics import NOT_EVALUATED, VIOLATED, DriveKernel, OperatingPoint, evaluate_point
from ..status import Reason

HARD_GROUPS = ("VOLTAGE", "CURRENT", "DOMAIN")
DC_GROUPS = ("DISCHARGE_SOURCE", "CHARGE_SOURCE")


def torque_tolerance(k: DriveKernel) -> float:
    s = k.settings
    return max(s.torque_residual_abs_Nm, s.torque_residual_rel * k.torque_scale())


def validity_reasons(k: DriveKernel) -> tuple[Reason, ...]:
    return tuple(dict.fromkeys(i.reason for i in k.issues))


def missing_relevant_dc_limits(k: DriveKernel, p_dc: float | None) -> list[str]:
    """DC limits that can bind at this P_dc but were not declared (None; math.inf means declared unlimited)."""
    lim = k.limits
    if p_dc is None:
        return []
    tol = k.settings.power_zero_tol_W
    out = []
    if p_dc > tol:
        for name, v in (("discharge power", lim.discharge_power_max_W), ("discharge current", lim.discharge_current_max_A)):
            if v is None:
                out.append(name)
    elif p_dc < -tol:
        for name, v in (("charge power", lim.charge_power_max_W), ("charge current", lim.charge_current_max_A)):
            if v is None:
                out.append(name)
    return out


@dataclass(frozen=True)
class WitnessCheck:
    accepted: bool
    point: OperatingPoint | None
    reasons: tuple[Reason, ...]
    messages: tuple[str, ...]
    torque_residual_Nm: float | None
    torque_tolerance_Nm: float

    def to_dict(self) -> dict:
        return {
            "accepted_as_witness": self.accepted,
            "reasons": [r.value for r in self.reasons],
            "messages": list(self.messages),
            "torque_residual_Nm": self.torque_residual_Nm,
            "torque_residual_tolerance_Nm": self.torque_tolerance_Nm,
            "note": "the tolerance is a numerical budget, not a torque-accuracy requirement",
        }


def check_witness(k: DriveKernel, id_A: float, iq_A: float, *, T_request: float | None = None,
                  band: tuple[float, float] | None = None, groups: tuple[str, ...] = HARD_GROUPS,
                  require_dc: bool = False, point: OperatingPoint | None = None,
                  include_validity: bool = True) -> WitnessCheck:
    """Evaluate (id, iq) and decide whether it may serve as a witness for the stated request.

    ``include_validity=False`` checks only the point itself (used where the caller already turns
    model-validity issues into an UNKNOWN claim and keeps the point as a conditional diagnostic).
    """
    reasons: list[Reason] = []
    msgs: list[str] = []
    tol = torque_tolerance(k)
    if include_validity:
        for i in k.issues:
            reasons.append(i.reason)
            msgs.append(f"model validity: {i.message}")
    pt = point
    if pt is None:
        try:
            pt = evaluate_point(k, id_A, iq_A)
        except OutsideModelDomain as exc:
            return WitnessCheck(False, None, tuple(dict.fromkeys(reasons + [Reason.OUTSIDE_MODEL_DOMAIN])),
                                tuple(msgs + [str(exc)]), None, tol)
    residual = None
    if T_request is not None or band is not None:
        if pt.Tshaft_Nm is None:
            reasons.append(Reason.MISSING_INPUT)
            msgs.append("shaft torque undefined (rotational loss model missing)")
        else:
            t = pt.Tshaft_Nm
            if band is None:
                residual = t - float(T_request)
                ok = abs(residual) <= tol
                want = f"{T_request:.6g} N*m"
            else:
                lo, hi = band
                residual = 0.0 if lo <= t <= hi else (t - hi if t > hi else t - lo)
                ok = abs(residual) <= tol
                want = f"[{lo:.6g}, {hi:.6g}] N*m"
            if not ok:
                reasons.append(Reason.NUMERICAL_UNRESOLVED)
                msgs.append(f"point gives {t:.6g} N*m, the request is {want} (residual {residual:+.3g} N*m > "
                            f"tolerance {tol:.2g} N*m): not a witness for this request")
    need = tuple(groups) + (DC_GROUPS if require_dc else ())
    for c in pt.constraints:
        if c.group not in need:
            continue
        if c.state == NOT_EVALUATED:
            reasons.append(Reason.MISSING_INPUT)
            msgs.append(f"{c.name} not evaluated (demand undefined): not a pass")
        elif c.state == VIOLATED:
            reasons.append(Reason.CONSTRAINT_VIOLATION)
            msgs.append(f"{c.name} violated: demand {c.demand:.6g} vs limit {c.limit:.6g} {c.unit}")
    if require_dc:
        if pt.Pdc_W is None:
            reasons.append(Reason.MISSING_INPUT)
            msgs.append("DC power undefined (loss model missing)")
        else:
            miss = missing_relevant_dc_limits(k, pt.Pdc_W)
            if miss:
                reasons.append(Reason.MISSING_INPUT)
                msgs.append("DC limit(s) that can bind at this point are not declared: " + ", ".join(miss)
                            + " (a missing limit is not 'unlimited'; declare math.inf for no limit)")
    if not pt.identities_ok:
        reasons.append(Reason.NUMERICAL_UNRESOLVED)
        msgs.append("power identities do not close within tolerance")
    # physical admissibility is a separate gate from algebraic closure (review R2, C04): negative passive losses or
    # an efficiency outside [0, 1] close the identities and are still not physics
    ptol = k.settings.power_zero_tol_W
    bad = [n for n, v in (("copper loss", pt.Pcu_W), ("inverter loss", pt.Pinv_W), ("rotational loss", pt.Prot_W))
           if v is not None and v < -ptol]
    if bad or pt.energy_mode == "ACCOUNTING_INCONSISTENCY":
        reasons.append(Reason.OUTSIDE_MODEL_DOMAIN)
        msgs.append("non-passive energy state (" + (", ".join(f"negative {b}" for b in bad) or pt.efficiency_note)
                    + "): not accepted as physical evidence")
    reasons = list(dict.fromkeys(reasons))
    return WitnessCheck(not reasons, pt, tuple(reasons), tuple(msgs), residual, tol)
