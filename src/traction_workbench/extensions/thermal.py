"""Thermal screening linked to torque availability (roadmap C preview).

P_loss(operating point) -> T_node(t) -> time-to-limit and T_max(t):
each thermal node (e.g. inverter junction, stator winding) has a Foster
network Z_th(t) = sum R_i (1 - exp(-t / tau_i)) above the coolant, a
temperature limit and declared shares of the loss components (inverter,
copper, rotational) that heat it.  Starting from equilibrium at the coolant,
a constant loss P gives  T(t) = T_coolant + P * Z_th(t).

Loss-temperature feedback, changing losses during the transient and
non-equilibrium initial states are not modelled.  A duration claim is
FEASIBLE/INFEASIBLE only when the thermal model is declared *validated* for
the stated conditions; otherwise the numbers are a screening estimate and the
duration claim stays UNKNOWN (UNVALIDATED_DURATION).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..errors import InputValidationError
from ..models.components import DriveModel
from ..models.flux import _finite
from ..models.provenance import Provenance
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.capability import policy_capability
from ..solvers.policy import PolicyEvaluator
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

LOSS_KEYS = ("inverter", "copper", "rotational")


@dataclass(frozen=True)
class FosterNetwork:
    R_K_per_W: tuple
    tau_s: tuple

    def __post_init__(self):
        r = tuple(_finite("R_K_per_W", x) for x in self.R_K_per_W)
        t = tuple(_finite("tau_s", x) for x in self.tau_s)
        if not r or len(r) != len(t) or any(x < 0 for x in r) or any(x <= 0 for x in t):
            raise InputValidationError("Foster network needs matching R >= 0 and tau > 0", field="foster")
        object.__setattr__(self, "R_K_per_W", r)
        object.__setattr__(self, "tau_s", t)

    def zth(self, t: float) -> float:
        if math.isinf(t):
            return sum(self.R_K_per_W)
        return sum(r * (1.0 - math.exp(-t / tau)) for r, tau in zip(self.R_K_per_W, self.tau_s))

    @classmethod
    def one_node(cls, R_K_per_W: float, C_J_per_K: float) -> "FosterNetwork":
        return cls((R_K_per_W,), (R_K_per_W * C_J_per_K,))


@dataclass(frozen=True)
class ThermalNode:
    node_id: str
    network: FosterNetwork
    limit_C: float
    loss_share: tuple            # (("inverter", 1/6), ("copper", 0.0), ...)

    def __post_init__(self):
        object.__setattr__(self, "limit_C", _finite("limit_C", self.limit_C))
        for k, v in self.loss_share:
            if k not in LOSS_KEYS or not (0 <= float(v) <= 1):
                raise InputValidationError(f"loss share {k}={v} invalid (keys {LOSS_KEYS}, 0..1)", field=self.node_id)

    def power(self, losses: dict) -> float:
        return sum(float(v) * losses.get(k, 0.0) for k, v in self.loss_share)


@dataclass(frozen=True)
class ThermalModel:
    model_id: str
    revision: str
    nodes: tuple
    provenance: Provenance
    validated: bool = False
    validity: tuple = ()          # (("coolant_temp_C", (60, 70)), ...)

    def conditions_ok(self, stated: dict) -> tuple[bool, list[str]]:
        problems = []
        for key, rng in self.validity:
            v = stated.get(key)
            if v is None:
                problems.append(f"{key} not stated")
            elif not (rng[0] <= v <= rng[1]):
                problems.append(f"{key}={v:g} outside {list(rng)}")
        return not problems, problems


def temperature(node: ThermalNode, P_W: float, t_s: float, coolant_C: float) -> float:
    return coolant_C + P_W * node.network.zth(t_s)


def time_to_limit(node: ThermalNode, P_W: float, coolant_C: float) -> float:
    allow = node.limit_C - coolant_C
    if allow <= 0:
        return 0.0
    if P_W <= 0 or P_W * node.network.zth(math.inf) <= allow:
        return math.inf
    lo, hi = 0.0, max(node.network.tau_s)
    while P_W * node.network.zth(hi) < allow:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if P_W * node.network.zth(mid) < allow:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-12 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def _losses(pt) -> dict:
    return {"inverter": pt.Pinv_W or 0.0, "copper": pt.Pcu_W, "rotational": pt.Prot_W or 0.0}


def thermal_duration(drive: DriveModel, scenario: Scenario, model: ThermalModel, T_request: float,
                     duration_s: float, settings: NumericalSettings = DEFAULT_SETTINGS) -> dict:
    if scenario.coolant_temp_C is None:
        raise InputValidationError("coolant temperature must be stated for a thermal evaluation",
                                   field="coolant_temp_C")
    sol = PolicyEvaluator(drive, scenario, settings).solve(T_request)
    q = f"{T_request:g} N*m at {scenario.speed_rpm:g} rpm for {duration_s:g} s"
    if sol.point is None or sol.policy_claim.status is not Status.FEASIBLE:
        claim = Claim("thermal_duration", sol.policy_claim.status if sol.policy_claim.status is Status.INFEASIBLE
                      else Status.UNKNOWN, q, "static policy not established", reasons=sol.policy_claim.reasons,
                      detail="the static operating point is not FEASIBLE, so no duration is evaluated")
        return {"claim": claim.to_dict(), "nodes": []}
    losses = _losses(sol.point)
    rows = []
    worst_t = math.inf
    violated = False
    for nd in model.nodes:
        p = nd.power(losses)
        tt = time_to_limit(nd, p, scenario.coolant_temp_C)
        temp = temperature(nd, p, duration_s, scenario.coolant_temp_C)
        rows.append({"node": nd.node_id, "power_W": p, "temperature_at_duration_C": temp, "limit_C": nd.limit_C,
                     "time_to_limit_s": tt, "steady_state_C": temperature(nd, p, math.inf, scenario.coolant_temp_C)})
        worst_t = min(worst_t, tt)
        violated |= temp > nd.limit_C
    stated = {"coolant_temp_C": scenario.coolant_temp_C, "Vdc_V": scenario.Vdc_V,
              "switching_frequency_Hz": scenario.switching_frequency_Hz}
    ok, problems = model.conditions_ok(stated)
    ev = Evidence.make(EvidenceKind.VALIDATED_DOMAIN if (model.validated and ok) else EvidenceKind.SAMPLED,
                       f"{model.model_id} rev {model.revision}: sustainable for {worst_t:.4g} s at constant loss",
                       validated=model.validated, provenance=model.provenance.to_dict())
    if model.validated and ok:
        st = Status.INFEASIBLE if violated else Status.FEASIBLE
        claim = Claim("thermal_duration", st, q, "validated thermal model at matching conditions", None, f"{duration_s:g} s",
                      reasons=(Reason.CONSTRAINT_VIOLATION,) if violated else (), evidence=(ev,),
                      detail=f"time to the first node limit {worst_t:.4g} s")
    else:
        why = "thermal model not declared validated" if not model.validated else "; ".join(problems)
        claim = Claim("thermal_duration", Status.UNKNOWN, q, "thermal screening estimate", None, f"{duration_s:g} s",
                      reasons=(Reason.UNVALIDATED_DURATION,), evidence=(ev,),
                      qualifiers=(f"screening estimate: {'exceeds' if violated else 'within'} limits "
                                  f"(first limit after {worst_t:.4g} s)",),
                      detail=f"{why}: the estimate is not a duration rating")
    return {"claim": claim.to_dict(), "nodes": rows, "time_to_first_limit_s": worst_t,
            "operating_point": {"id_A": sol.point.id_A, "iq_A": sol.point.iq_A, "losses_W": losses}}


def torque_availability(drive: DriveModel, scenario: Scenario, model: ThermalModel,
                        durations_s=(1.0, 10.0, 30.0, 60.0, math.inf), direction: int = 1,
                        settings: NumericalSettings = DEFAULT_SETTINGS) -> dict:
    """Largest static-policy torque whose node temperatures stay within limits for each duration."""
    if scenario.coolant_temp_C is None:
        raise InputValidationError("coolant temperature must be stated", field="coolant_temp_C")
    ev = PolicyEvaluator(drive, scenario, settings)
    cap = policy_capability(ev, direction, certify=False)
    if cap.value_Nm is None:
        return {"static_capability_Nm": None, "rows": [], "note": "no static policy capability"}

    def ok_for(T, t):
        s = ev.solve(T)
        if s.point is None or s.policy_claim.status is not Status.FEASIBLE:
            return False, None
        losses = _losses(s.point)
        worst = None
        for nd in model.nodes:
            if temperature(nd, nd.power(losses), t, scenario.coolant_temp_C) > nd.limit_C:
                return False, nd.node_id
        return True, worst

    rows = []
    for t in durations_s:
        hi = cap.value_Nm
        good, _ = ok_for(hi, t)
        if good:
            rows.append({"duration_s": t, "torque_Nm": hi, "limited_by": "static capability"})
            continue
        lo = 0.0
        if not ok_for(lo, t)[0]:
            rows.append({"duration_s": t, "torque_Nm": None, "limited_by": "thermal even at zero torque"})
            continue
        limiting = None
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            g, node = ok_for(mid, t)
            if g:
                lo = mid
            else:
                hi, limiting = mid, node or limiting
            if abs(hi - lo) <= 1e-6 * max(1.0, abs(hi)):
                break
        rows.append({"duration_s": t, "torque_Nm": lo, "limited_by": f"thermal node {limiting}"})
    return {
        "static_capability_Nm": cap.value_Nm,
        "coolant_temp_C": scenario.coolant_temp_C,
        "rows": rows,
        "validated": model.validated,
        "status_note": ("validated thermal model" if model.validated else
                        "screening estimate from an unvalidated thermal model: not a duration rating"),
        "assumptions": ["start from equilibrium at the coolant", "constant losses at the minimum-current point",
                        "no loss-temperature feedback", "declared loss shares per node"],
    }
