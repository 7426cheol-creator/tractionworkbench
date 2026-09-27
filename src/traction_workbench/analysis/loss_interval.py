"""DC acceptance under a loss interval (Handoff H6, reference case S00).

P_dc = P_shaft + L with L in [L_min, L_max].  The discharge cap is checked
against the largest P_dc (largest loss), the charge cap against the smallest
P_dc (smallest loss): applying the maximum loss to regeneration as well would
be falsely optimistic.

Three different questions get three different answers:

* actual system, loss unknown within the interval -> FEASIBLE only if every
  value satisfies, INFEASIBLE only if every value violates, else UNKNOWN;
* robust requirement "for every admissible loss"  -> an admissible value that
  violates is a counterexample (INFEASIBLE);
* the interval is only an outer enclosure (not every value realisable) -> a
  violating end point proves nothing; UNKNOWN until a realisable
  counterexample or a tighter bound exists.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import InputValidationError
from ..models.flux import _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status


@dataclass(frozen=True)
class LossIntervalResult:
    Pdc_interval_W: tuple[float, float]
    actual: Claim
    robust: Claim
    enclosure: Claim

    def to_dict(self) -> dict:
        return {"Pdc_interval_W": list(self.Pdc_interval_W), "actual_system_unknown_loss": self.actual.to_dict(),
                "robust_requirement_all_admissible_losses": self.robust.to_dict(),
                "if_interval_is_outer_enclosure": self.enclosure.to_dict()}


def dc_acceptance_with_loss_interval(P_shaft_W: float, loss_W: tuple[float, float],
                                     discharge_cap_W: float | None = None,
                                     charge_cap_W: float | None = None) -> LossIntervalResult:
    p = _finite("P_shaft_W", P_shaft_W)
    lo, hi = (_finite("loss_W", x) for x in loss_W)
    if lo < 0 or hi < lo:
        raise InputValidationError("loss interval must satisfy 0 <= L_min <= L_max", field="loss_W")
    pmin, pmax = p + lo, p + hi
    checks = []   # (value_that_is_worst, ok_at_worst, ok_at_best, text)
    if discharge_cap_W is not None:
        cap = _finite("discharge_cap_W", discharge_cap_W)
        checks.append(("discharge", pmax <= cap, pmin <= cap, cap, f"P_dc <= {cap:g} W (worst: largest loss)"))
    if charge_cap_W is not None:
        cap = _finite("charge_cap_W", charge_cap_W)
        checks.append(("charge", pmin >= -cap, pmax >= -cap, cap, f"P_dc >= {-cap:g} W (worst: smallest loss)"))
    if not checks:
        raise InputValidationError("at least one DC cap is required", field="caps")
    all_ok = all(c[1] for c in checks)
    all_bad = any(not c[2] for c in checks)   # even the most favourable value violates
    ev = Evidence.make(EvidenceKind.ANALYTIC_BOUND, f"P_dc in [{pmin:g}, {pmax:g}] W from L in [{lo:g}, {hi:g}] W",
                       checks=[c[4] for c in checks])
    q = "DC acceptance with an uncertain total loss"
    scope = "loss uncertainty propagated as P_dc = P_shaft + L"
    if all_ok:
        actual = Claim("dc_actual_unknown_loss", Status.FEASIBLE, q, scope, evidence=(ev,),
                       detail="every admissible loss satisfies the DC limits")
    elif all_bad:
        actual = Claim("dc_actual_unknown_loss", Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,),
                       evidence=(ev,), detail="even the most favourable loss violates a DC limit")
    else:
        actual = Claim("dc_actual_unknown_loss", Status.UNKNOWN, q, scope, reasons=(Reason.UNCERTAINTY_OVERLAP,),
                       evidence=(ev,), detail="admissible values include both satisfaction and violation")
    worst = []
    for name, ok_w, _, cap, _t in checks:
        if not ok_w:
            if name == "discharge":
                worst.append(f"L = {hi:g} W gives P_dc = {pmax:g} W > {cap:g} W")
            else:
                worst.append(f"L = {lo:g} W gives P_dc = {pmin:g} W < {-cap:g} W")
    if all_ok:
        robust = Claim("dc_robust_all_admissible", Status.FEASIBLE, q, scope + "; every value admissible",
                       evidence=(ev,), detail="satisfied for every admissible loss")
        enclosure = Claim("dc_outer_enclosure", Status.FEASIBLE, q, scope + "; interval is an outer enclosure",
                          evidence=(ev,), detail="satisfied over the whole enclosure, hence for the true loss")
    else:
        robust = Claim("dc_robust_all_admissible", Status.INFEASIBLE, q, scope + "; every value admissible",
                       reasons=(Reason.CONSTRAINT_VIOLATION,),
                       evidence=(ev, Evidence.make(EvidenceKind.ANALYTIC_BOUND, "explicit admissible counterexample: "
                                                   + "; ".join(worst))),
                       detail="an admissible loss value violates the DC limit")
        enclosure = Claim("dc_outer_enclosure", Status.INFEASIBLE if all_bad else Status.UNKNOWN, q,
                          scope + "; interval is an outer enclosure",
                          reasons=(Reason.CONSTRAINT_VIOLATION,) if all_bad else (Reason.UNCERTAINTY_OVERLAP,),
                          evidence=(ev,),
                          detail=("violated over the whole enclosure" if all_bad else
                                  "a violating end point of an outer enclosure does not prove a realisable violation; "
                                  "no guarantee until a realisable counterexample or a tighter bound exists"))
    return LossIntervalResult((pmin, pmax), actual, robust, enclosure)
