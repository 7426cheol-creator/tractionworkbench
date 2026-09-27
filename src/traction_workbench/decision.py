"""Engineering Decision Record.

evaluate_requirement() links: original requirement -> interpreted quantity,
port and conditions -> examined scenarios -> model/version/provenance ->
operating solution or infeasibility evidence -> constraint margins (native
units) -> capability margin -> duration -> AND-aggregated verdict ->
limiting factors -> next actions, together with an immutable input snapshot
and its SHA-256.  Same inputs and settings reproduce the same record body.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

import numpy as np

from . import __version__
from .analysis.rating import RatingEnvelope, duration_claim
from .models.components import DriveModel
from .models.provenance import FIDELITY_ALLOWED_CLAIMS
from .requirement import Requirement
from .scenario import DcSourceLimits, Scenario
from .settings import DEFAULT_SETTINGS, NumericalSettings
from .solvers.capability import CapabilityResult, policy_capability
from .solvers.policy import POLICY_TEXT, PolicyEvaluator, PolicySolution
from .status import Aggregate, Claim, Evidence, EvidenceKind, Reason, Status, aggregate_and


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating,)):
        x = float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, float):
        if math.isnan(x):
            return "NaN"
        if math.isinf(x):
            return "Infinity" if x > 0 else "-Infinity"
    return x


def canonical_json(obj) -> str:
    return json.dumps(_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConditionResult:
    scenario: Scenario
    solution: PolicySolution
    capability: CapabilityResult | None
    duration: Claim | None
    requirement_claim: Claim
    torque_margin_Nm: float | None

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario.describe(),
            "requirement_claim_at_this_condition": self.requirement_claim.to_dict(),
            "torque_capability_margin_Nm": self.torque_margin_Nm,
            "policy_solution": self.solution.to_dict(),
            "policy_capability": None if self.capability is None else self.capability.to_dict(),
            "duration_claim": None if self.duration is None else self.duration.to_dict(),
        }


@dataclass(frozen=True)
class DecisionRecord:
    record_id: str
    requirement: Requirement
    drive: DriveModel
    conditions: tuple[ConditionResult, ...]
    verdict: Aggregate
    verdict_scope: str
    qualifiers: tuple[str, ...]
    limiting_factors: tuple[str, ...]
    next_actions: tuple[str, ...]
    unevaluated: tuple[str, ...]
    assumptions: tuple[str, ...]
    snapshot: dict
    input_sha256: str
    settings: NumericalSettings
    analyses: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return _jsonable({
            "record_type": "EngineeringDecisionRecord",
            "record_id": self.record_id,
            "software": {"name": "traction-workbench", "version": __version__},
            "input_sha256": self.input_sha256,
            "verdict": {**self.verdict.to_dict(), "scope": self.verdict_scope, "qualifiers": list(self.qualifiers)},
            "requirement": self.requirement.describe(),
            "model": {
                "drive_id": self.drive.drive_id,
                "revision": self.drive.revision,
                "fidelity": self.drive.fidelity.value,
                "fidelity_allows": FIDELITY_ALLOWED_CLAIMS[self.drive.fidelity],
                "provenance": self.drive.provenance.to_dict(),
                "policy": POLICY_TEXT,
            },
            "conditions": [c.to_dict() for c in self.conditions],
            "limiting_factors": list(self.limiting_factors),
            "next_actions": list(self.next_actions),
            "not_evaluated": list(self.unevaluated),
            "assumptions": list(self.assumptions),
            "analyses": self.analyses,
            "numerical_settings": self.settings.to_dict(),
            "input_snapshot": self.snapshot,
        })

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        from .report import decision_markdown
        return decision_markdown(self)


# ---------------------------------------------------------------------------

def _condition_points(req: Requirement, samples: int) -> tuple[list[float], bool]:
    if not req.is_range:
        return [req.Vdc_V], False
    lo, hi = req.Vdc_V
    if lo == hi:
        return [lo], False
    return [float(v) for v in np.linspace(lo, hi, max(samples, 2))], True


def _scenario_for(req: Requirement, base: Scenario | None, limits: DcSourceLimits | None, vdc: float) -> Scenario:
    kw = dict(
        scenario_id=f"{req.req_id}@{req.speed_rpm:g}rpm/{vdc:g}V",
        speed_rpm=req.speed_rpm,
        Vdc_V=vdc,
        winding_temp_C=req.winding_temp_C,
        magnet_temp_C=req.magnet_temp_C,
        coolant_temp_C=req.coolant_temp_C,
        initial_state=req.initial_state,
        switching_frequency_Hz=req.switching_frequency_Hz,
    )
    if base is not None:
        kw.update({k: v for k, v in (("winding_temp_C", base.winding_temp_C), ("magnet_temp_C", base.magnet_temp_C),
                                     ("coolant_temp_C", base.coolant_temp_C), ("initial_state", base.initial_state),
                                     ("switching_frequency_Hz", base.switching_frequency_Hz))
                   if kw[k] is None and v is not None})
        limits = base.source_limits
        kw["description"] = base.description
    return Scenario(source_limits=limits or DcSourceLimits(), **kw)


def _requirement_claim(req: Requirement, sc: Scenario, sol: PolicySolution, cap: CapabilityResult | None,
                       dur: Claim | None) -> tuple[Claim, float | None]:
    """AND of the static policy claim (and duration) at one condition, honouring a torque band."""
    policy = sol.policy_claim
    margin = None
    if cap is not None and cap.value_Nm is not None:
        margin = (cap.value_Nm - req.target_Nm) if req.direction > 0 else (req.target_Nm - cap.value_Nm)
    parts = [policy]
    if req.operator == "band" and policy.status is not Status.FEASIBLE and cap is not None and cap.value_Nm is not None:
        lo, hi = req.target_Nm - req.band_Nm, req.target_Nm + req.band_Nm
        if lo <= cap.value_Nm <= hi:
            parts = [Claim("policy_static_in_band", Status.FEASIBLE,
                           f"shaft torque within [{lo:g}, {hi:g}] N*m", policy.scope, policy.policy,
                           evidence=cap.evidence, qualifiers=("achieved at the capability boundary",),
                           detail=f"the policy achieves {cap.value_Nm:.6g} N*m, inside the requested band")]
    if dur is not None:
        parts.append(dur)
    agg = aggregate_and(parts)
    quals = tuple(q for p in parts for q in p.qualifiers)
    if cap is not None and margin is not None and agg.status is Status.FEASIBLE:
        if abs(margin) <= cap.gap_tolerance_Nm:
            quals += ("boundary-qualified: requested torque within the capability bound tolerance",)
    claim = Claim("requirement_at_condition", agg.status,
                  f"{req.req_id}: {req.target_Nm:g} N*m at {req.speed_rpm:g} rpm, Vdc = {sc.Vdc_V:g} V",
                  policy.scope, policy.policy, dur.time_horizon if dur else "static steady-state (no duration)",
                  agg.reasons, tuple(e for p in parts for e in p.evidence), quals,
                  "AND of: " + ", ".join(f"{p.name}={p.status.value}" for p in parts))
    return claim, margin


def _limiting_and_actions(req: Requirement, results: list[ConditionResult], sampled_range: bool) -> tuple[list, list, list]:
    limiting, actions, unevaluated = [], [], []
    seen = set()

    def add(lst, text):
        if text not in seen:
            seen.add(text)
            lst.append(text)

    for cr in results:
        sol = cr.solution
        tag = f"[Vdc={cr.scenario.Vdc_V:g} V]"
        for sc in sol.screens:
            if sc.violated:
                add(limiting, f"{tag} necessary condition violated: {sc.statement}")
        pt = sol.point
        if pt is not None:
            for c in pt.violations():
                add(limiting, f"{tag} {c.name} violated at the policy point: demand {c.demand:.6g} vs limit "
                              f"{c.limit:.6g} {c.unit} (slack {c.slack:.6g})")
            act = [c.name for c in pt.active()]
            if act:
                add(limiting, f"{tag} active at the policy point (within tolerance): {', '.join(act)}")
            cur = pt.constraint("CURRENT")
            vol = pt.constraint("VOLTAGE")
            if cur is not None and vol is not None and cur.state == "SATISFIED" and vol.state != "SATISFIED" \
                    and cr.torque_margin_Nm is not None:
                m = cr.torque_margin_Nm
                add(limiting, f"{tag} current margin {cur.slack:.4g} A but " + (
                    f"torque-capability margin only {m:.4g} N*m" if m >= 0 else
                    f"the request exceeds the policy capability by {-m:.4g} N*m") +
                    ": the limit is voltage/DC, not current")
        if cr.capability is not None and cr.capability.witness is not None:
            add(limiting, f"{tag} policy capability {cr.capability.value_Nm:.6g} N*m limited by: "
                          f"{', '.join(cr.capability.active_constraints) or 'n/a'}")
        el, pc, dc = sol.electrical, sol.policy_claim, sol.dc_claim
        if el.status is Status.INFEASIBLE:
            if any(s.name == "d_axis_voltage_exceeds_budget" and s.violated for s in sol.screens):
                add(actions, f"{tag} voltage is a necessary-condition limit independent of the current rating: a "
                             f"larger current rating cannot fix it; examine DC terminal voltage (see sizing) or the "
                             f"motor/declared id domain")
            else:
                add(actions, f"{tag} no electrical solution in the declared domain: examine Vdc, current limit, "
                             f"declared id domain or motor data (see sizing/dominance)")
        if dc is not None and dc.status is Status.INFEASIBLE and pt is not None:
            dis = [c for c in pt.violations() if c.group == "DISCHARGE_SOURCE"]
            chg = [c for c in pt.violations() if c.group == "CHARGE_SOURCE"]
            if dis:
                add(actions, f"{tag} DC discharge limit binds (" + "; ".join(
                    f"{c.name}: {c.demand:.6g} vs {c.limit:.6g} {c.unit}" for c in dis) +
                    "): raise the source limit or lower the request")
            if chg:
                add(actions, f"{tag} battery charge acceptance binds (" + ", ".join(c.name for c in chg) +
                    "); deliberately raising losses is not an energy-recovering policy - confirm charge limits at the "
                    "relevant SOC/temperature or split braking with the friction brake (outside this model)")
        for c in sol.claims:
            if Reason.MISSING_INPUT in c.reasons or Reason.OUTSIDE_MODEL_DOMAIN in c.reasons:
                for i in sol.model_issues:
                    add(actions, f"{tag} data needed: {i.message}")
        if sol.curve is not None and sol.curve.coverage_limited:
            add(actions, f"{tag} extend flux-map coverage (nearest uncovered allowed point |i| = "
                         f"{sol.curve.coverage_distance_A:.4g} A) before claiming infeasibility beyond the data")
        if cr.duration is not None and cr.duration.status is Status.UNKNOWN:
            add(actions, f"{tag} provide a validated {req.duration_text()} rating envelope at matching conditions "
                         f"(coolant, initial state, Vdc, switching frequency) or a validated thermal model; "
                         f"the static result is not a duration rating")
    if req.duration_s is None:
        add(unevaluated, "duration: not stated in the requirement -> static item only (duration aspect undetermined)")
    if sampled_range:
        add(actions, "the Vdc range was examined at sampled points only: a PASS over the continuous range needs a "
                     "monotonicity argument or denser analysis")
    for item in ("PWM ripple, instantaneous peak current, semiconductor SOA and OC overshoot",
                 "current-control dynamics, stability and voltage headroom for transients",
                 "thermal duration unless a matching validated rating envelope is supplied",
                 "demagnetisation, rotor strength and insulation (declared domains are not certifications)",
                 "fault transitions (ASC/6SO) and functional-safety approval"):
        add(unevaluated, item)
    return limiting, actions, unevaluated


def evaluate_requirement(req: Requirement, drive: DriveModel, *, scenario: Scenario | None = None,
                         source_limits: DcSourceLimits | None = None, ratings: tuple[RatingEnvelope, ...] = (),
                         settings: NumericalSettings = DEFAULT_SETTINGS, range_samples: int = 5,
                         with_capability: bool = True) -> DecisionRecord:
    vdcs, sampled = _condition_points(req, range_samples)
    results: list[ConditionResult] = []
    for vdc in vdcs:
        sc = _scenario_for(req, scenario, source_limits, vdc)
        ev = PolicyEvaluator(drive, sc, settings)
        sol = ev.solve(req.target_Nm)
        cap = None
        if with_capability and ev.speed_in_domain and ev.k.evaluable and ev.k.tau_rot is not None:
            cap = policy_capability(ev, req.direction)
        stated = {"coolant_temp_C": sc.coolant_temp_C, "initial_state": sc.initial_state, "Vdc_V": sc.Vdc_V,
                  "switching_frequency_Hz": sc.switching_frequency_Hz, "winding_temp_C": sc.winding_temp_C,
                  "magnet_temp_C": sc.magnet_temp_C}
        dur = duration_claim(ratings, req.duration_s, req.speed_rpm, req.target_Nm, stated)
        rc, margin = _requirement_claim(req, sc, sol, cap, dur)
        results.append(ConditionResult(sc, sol, cap, dur, rc, margin))
    agg = aggregate_and(r.requirement_claim for r in results)
    qualifiers = []
    if sampled and agg.status is Status.FEASIBLE:
        agg = Aggregate(Status.UNKNOWN, (Reason.SAMPLED_COVERAGE,), agg.deciding_claims)
        qualifiers.append(f"FEASIBLE at every examined Vdc point ({len(vdcs)} points); the continuous range is not "
                          f"established")
    if agg.status is Status.INFEASIBLE and sampled:
        bad = [r.scenario.Vdc_V for r in results if r.requirement_claim.status is Status.INFEASIBLE]
        qualifiers.append(f"counterexample(s) inside the required Vdc range at {', '.join(f'{v:g} V' for v in bad)}: "
                          f"the for-all requirement fails")
    for r in results:
        for q in r.requirement_claim.qualifiers:
            if q not in qualifiers:
                qualifiers.append(q)
    scope = (f"{drive.drive_id} rev {drive.revision} ({drive.fidelity.value}, {drive.provenance.origin.value} data, "
             f"{drive.provenance.validation_status}); minimum-current policy; static fundamental steady state; "
             f"declared operating domain; n = {req.speed_rpm:g} rpm; Vdc "
             + (f"in [{req.Vdc_V[0]:g}, {req.Vdc_V[1]:g}] V (sampled)" if req.is_range else f"= {req.Vdc_V:g} V")
             + ("" if req.duration_s is None else f"; duration {req.duration_text()}"))
    limiting, actions, unevaluated = _limiting_and_actions(req, results, sampled)
    assumptions = [
        "balanced three-phase, no zero sequence, single 2-level VSI, wye (or declared wye-equivalent), fundamental "
        "steady state, linear SVPWM",
        "dq currents/voltages are fundamental phase-peak values (amplitude-invariant Park, d on PM flux)",
        "speed, Vdc and temperatures are given scenario boundaries",
        "DC limits are average power/current at the inverter DC terminal",
    ]
    for n in drive.notes:
        assumptions.append(n)
    snapshot = {
        "requirement": req.describe(),
        "drive": drive.describe(),
        "scenario_template": None if scenario is None else scenario.describe(),
        "source_limits": None if source_limits is None else source_limits.describe(),
        "ratings": [r.describe() for r in ratings],
        "range_samples": range_samples,
        "numerical_settings": settings.to_dict(),
        "software_version": __version__,
    }
    digest = sha256_of(snapshot)
    return DecisionRecord(
        record_id=f"DR-{req.req_id}-{digest[:12]}", requirement=req, drive=drive, conditions=tuple(results),
        verdict=agg, verdict_scope=scope, qualifiers=tuple(qualifiers), limiting_factors=tuple(limiting),
        next_actions=tuple(actions), unevaluated=tuple(unevaluated), assumptions=tuple(assumptions),
        snapshot=_jsonable(snapshot), input_sha256=digest, settings=settings)
