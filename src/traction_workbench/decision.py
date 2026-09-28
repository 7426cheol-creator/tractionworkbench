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
from .identity import content_sha256, implementation
from .models.components import DriveModel
from .models.provenance import FIDELITY_ALLOWED_CLAIMS
from .requirement import Requirement
from .scenario import DcSourceLimits, Scenario
from .settings import DEFAULT_SETTINGS, NumericalSettings
from .solvers.capability import CapabilityResult, physical_capability, policy_capability
from .solvers.policy import POLICY_TEXT, PolicyEvaluator, PolicySolution
from .status import Aggregate, Claim, Evidence, EvidenceKind, Reason, Status, aggregate_and


def jsonable(x):
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
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
    return json.dumps(jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
    witness_torque_Nm: float | None = None      # torque of the single witness behind every part of the claim
    witness_solution: PolicySolution | None = None

    # review R2 D-R2-03: every output (record, report, PDF, desktop, limiting factors, actions) shows the solution
    # at the ACCEPTED requirement witness; a band centre that failed is a named diagnostic, never the primary point
    @property
    def primary(self) -> PolicySolution:
        return self.witness_solution or self.solution

    @property
    def primary_torque_Nm(self) -> float:
        return self.primary.T_request_Nm

    @property
    def rejected_centre(self) -> PolicySolution | None:
        ws = self.witness_solution
        return None if ws is None or ws is self.solution else self.solution

    def to_dict(self) -> dict:
        ws = self.witness_solution
        wp = None if ws is None else ws.point
        rc = self.rejected_centre
        return {
            "scenario": self.scenario.describe(),
            "requirement_claim_at_this_condition": self.requirement_claim.to_dict(),
            "requirement_witness": None if self.witness_torque_Nm is None else {
                "torque_Nm": self.witness_torque_Nm,
                "id_A": None if wp is None else wp.id_A,
                "iq_A": None if wp is None else wp.iq_A,
                "note": "the static, DC and duration parts of the claim are evaluated at this same witness",
            },
            "torque_capability_margin_Nm": self.torque_margin_Nm,
            "torque_capability_margin_basis": "policy capability vs the requirement target (band centre for a band)",
            "policy_solution": self.primary.to_dict(),
            "policy_solution_torque_Nm": self.primary_torque_Nm,
            "rejected_band_centre": None if rc is None else {
                "note": "diagnostic only: the band centre failed; the requirement is answered at the witness above",
                "torque_Nm": rc.T_request_Nm, "policy_claim": rc.policy_claim.status.value,
                "Pdc_W": None if rc.point is None else rc.point.Pdc_W, "solution": rc.to_dict()},
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
    implementation: dict = field(default_factory=dict)

    @property
    def layers(self) -> dict:
        return claim_layers(self.requirement, self.drive, self.conditions, self.verdict)

    def to_dict(self) -> dict:
        return jsonable({
            "record_type": "EngineeringDecisionRecord",
            "record_id": self.record_id,
            "software": {"name": "traction-workbench", "version": __version__},
            "implementation": self.implementation,
            "input_sha256": self.input_sha256,
            "verdict": {**self.verdict.to_dict(), "scope": self.verdict_scope, "qualifiers": list(self.qualifiers),
                        "layers": self.layers},
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

# ---------------------------------------------------------------------------
# Claim layers (independent review F11/F12): mathematical / model / requirement / qualification
# ---------------------------------------------------------------------------

_CERTIFIED_KINDS = (EvidenceKind.EXACT_ENUMERATION, EvidenceKind.CERTIFIED_BOUND)


def _sub_models(drive: DriveModel) -> list[str]:
    out = []
    inv = drive.inverter.loss
    mod = drive.inverter.module_loss
    if mod is not None:
        dev = mod.device
        out.append(f"inverter loss: datasheet module model ({dev.technology}, {dev.value_kind} values, fsw "
                   f"{mod.fsw_Hz / 1e3:g} kHz, {mod.modulation}, evaluated at Tj {drive.inverter.module_Tj_C:g} degC; "
                   f"source: {dev.source or 'not stated'}) - the I^2 certificates do not apply, DC claims rest on "
                   "direct witnesses")
    elif inv is None:
        out.append("inverter loss: not modelled (DC claims UNKNOWN)")
    else:
        rng = "" if inv.valid_Vdc_V is None else f", declared valid Vdc {list(inv.valid_Vdc_V)} V"
        out.append(f"inverter loss: {inv.kind} P = a0 + a2*Ipk^2 (no Vdc/fsw/Tj/modulation dependence{rng})")
    rot = drive.motor.rotational_loss
    out.append("rotational loss: " + ("not modelled" if rot is None else f"{rot.basis}"))
    out.append("DC source: average power/current limits at the inverter DC terminal (no source impedance, sag or "
               "charge-acceptance dynamics)")
    out.append("thermal: not part of the static claim (duration only via a matching rating envelope)")
    return out


def claim_layers(req: Requirement, drive: DriveModel, conditions, verdict: Aggregate) -> dict:
    """Four separate statements that must not be merged into one boolean.

    * mathematical  - the numerical evidence (exact enumeration / certificates / residuals vs sampled search);
    * model         - the requirement verdict for THIS model and data (the headline verdict);
    * requirement   - whether the requirement is complete enough to be decided (duration, quantifier, ...);
    * qualification - the evidence level of the data behind the model.  Never promoted automatically:
                      synthetic or unvalidated data, or a numerical certificate, are not hardware qualification.
    """
    kinds, acc_ok, cert_ok, sampled = set(), True, True, False
    for cr in conditions:
        sol = cr.witness_solution or cr.solution
        for c in sol.claims:
            kinds.update(e.kind for e in c.evidence)
        acc = dict(sol.acceptance)
        if acc and not acc.get("passed", True):
            acc_ok = False
        mc = dict(sol.certificates).get("minimum_current")
        if mc and not mc.get("certified"):
            cert_ok = False
    sampled = any(Reason.SAMPLED_COVERAGE in c.requirement_claim.reasons for c in conditions) or req.is_range
    if not acc_ok:
        m_status, m_text = "UNRESOLVED", "numerical acceptance failed at a witness"
    elif kinds & set(_CERTIFIED_KINDS) and cert_ok and not sampled:
        m_status, m_text = "CERTIFIED", "exact enumeration / certified bounds; residuals within the numerical budget"
    elif cert_ok:
        m_status, m_text = "SAMPLED_OR_BOUNDED", "sampled search and/or cell bounds; sampled coverage is not a proof"
    else:
        m_status, m_text = "UNCERTIFIED", "the minimum-current point is not certified (coverage or bound gap)"
    open_items = []
    if req.duration_s is None:
        open_items.append("duration not stated: static item only (the duration aspect is undetermined)")
    elif req.initial_state is None:
        open_items.append("initial (thermal) state not stated for a duration requirement")
    if req.is_range:
        open_items.append("Vdc range examined at sampled points: a continuous-range claim needs monotonicity or "
                          "denser analysis")
    if req.operator == "band":
        open_items.append("band requirement: existence of one torque inside the band (not tracking of every torque)")
    prov = drive.provenance
    origin = prov.origin.value
    if origin in ("synthetic", "estimated"):
        q_status = f"NOT QUALIFIED ({origin} data)"
    elif origin in ("supplier", "fea"):
        q_status = f"DATA-DECLARED ({origin} data; no hardware correlation evidence in this record)"
    else:
        q_status = "MEASURED DATA (correlation, holdouts and uncertainty are not verified by this tool)"
    return {
        "mathematical": {"status": m_status, "meaning": m_text + " - numerical evidence, not physical accuracy"},
        "model": {"status": verdict.status.value, "verdict": verdict.status.verdict,
                  "meaning": "requirement verdict for this model and its data (static fundamental steady state, "
                             "minimum-current policy, declared domain)"},
        "requirement": {"status": "COMPLETE" if not open_items else "OPEN_ITEMS", "open_items": open_items},
        "qualification": {"status": q_status, "data_origin": origin, "validation_status": prov.validation_status,
                          "fidelity": drive.fidelity.value, "sub_models": _sub_models(drive),
                          "meaning": "hardware qualification is a separate claim: a model PASS or a numerical "
                                     "certificate never qualifies the product; simplified loss/thermal/source models "
                                     "hold only in their declared narrow domain"},
    }


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


BAND_SAMPLES = 9


def _requirement_claim(req: Requirement, sc: Scenario, ev: PolicyEvaluator, sol: PolicySolution,
                       cap: CapabilityResult | None, ratings, stated: dict):
    """Requirement claim at one condition -> (claim, margin, duration claim, witness torque, witness solution).

    ``achieve``: AND of the policy claim and the duration claim, both at the requested torque.
    ``band``: existence of ONE torque T' inside [target - band, target + band] for which the policy claim
    and the duration claim hold at the same witness; a failing band centre does not fail the band, and the
    band is INFEASIBLE only when an exclusion proof covers every torque in it.
    """
    margin = None
    if cap is not None and cap.accepted and cap.value_Nm is not None:
        margin = (cap.value_Nm - req.target_Nm) if req.direction > 0 else (req.target_Nm - cap.value_Nm)
    if req.operator == "band":
        return _band_claim(req, sc, ev, sol, cap, ratings, stated, margin)
    policy = sol.policy_claim
    dur = duration_claim(ratings, req.duration_s, req.speed_rpm, req.target_Nm, stated)
    parts = [policy] + ([dur] if dur is not None else [])
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
    wit_T = req.target_Nm if sol.point is not None else None
    return claim, margin, dur, wit_T, sol


def _band_claim(req, sc, ev, sol_c, cap, ratings, stated, margin):
    lo, hi = req.target_Nm - req.band_Nm, req.target_Nm + req.band_Nm
    q = (f"{req.req_id}: some shaft torque in [{lo:g}, {hi:g}] N*m at {req.speed_rpm:g} rpm, Vdc = {sc.Vdc_V:g} V "
         f"(band existence, one witness for every part)")
    cands = [req.target_Nm, lo, hi] + [float(x) for x in np.linspace(lo, hi, BAND_SAMPLES)]
    if cap is not None and cap.accepted:
        cands += [x for seg in cap.segments for x in seg if lo <= x <= hi]
    order = sorted(dict.fromkeys(round(x, 12) for x in cands), key=lambda x: (abs(x - req.target_Nm), x))
    tried = []
    for T in order:
        s_T = sol_c if T == req.target_Nm else ev.solve(T)
        d_T = duration_claim(ratings, req.duration_s, req.speed_rpm, T, stated)
        parts = [s_T.policy_claim] + ([d_T] if d_T is not None else [])
        agg = aggregate_and(parts)
        tried.append((T, agg.status.value))
        if agg.status is Status.FEASIBLE:
            claim = Claim("requirement_at_condition", Status.FEASIBLE, q, s_T.policy_claim.scope, s_T.policy_claim.policy,
                          d_T.time_horizon if d_T else "static steady-state (no duration)", (),
                          tuple(e for p in parts for e in p.evidence),
                          tuple(p2 for p in parts for p2 in p.qualifiers) + (f"band witness at {T:.6g} N*m",),
                          f"witness torque {T:.6g} N*m inside the band: " + ", ".join(
                              f"{p.name}={p.status.value}" for p in parts))
            return claim, margin, d_T, T, s_T
    # no witness among the examined torques: INFEASIBLE only with an exclusion that covers the whole band
    proofs, evid = [], []
    phys = physical_capability(ev, req.direction, include_dc=True) if ev.speed_in_domain and ev.k.evaluable \
        and ev.k.tau_rot is not None else None
    if phys is not None and phys.accepted and phys.bound_Nm is not None and phys.certified:
        U, tol = phys.bound_Nm, phys.gap_tolerance_Nm
        if (req.direction > 0 and U < lo - tol) or (req.direction < 0 and U > hi + tol):
            proofs.append(f"certified any-control capability bound {U:.6g} N*m (incl. DC limits) excludes the band")
            evid.extend(phys.evidence)
    if req.duration_s is not None:
        t_small = 0.0 if lo <= 0.0 <= hi else (lo if abs(lo) < abs(hi) else hi)
        d_small = duration_claim(ratings, req.duration_s, req.speed_rpm, t_small, stated)
        if d_small is not None and d_small.status is Status.INFEASIBLE:
            proofs.append(f"even the smallest |T| in the band ({t_small:g} N*m) is not rated for {req.duration_text()}")
            evid.extend(d_small.evidence)
    sol_T = sol_c
    d_c = duration_claim(ratings, req.duration_s, req.speed_rpm, req.target_Nm, stated)
    if proofs:
        claim = Claim("requirement_at_condition", Status.INFEASIBLE, q, sol_c.policy_claim.scope,
                      sol_c.policy_claim.policy, d_c.time_horizon if d_c else "static steady-state (no duration)",
                      (Reason.CONSTRAINT_VIOLATION,) if "capability" in proofs[0] else (Reason.RATING_NOT_MET,),
                      tuple(evid), (), "; ".join(proofs))
        return claim, margin, d_c, None, sol_T
    claim = Claim("requirement_at_condition", Status.UNKNOWN, q, sol_c.policy_claim.scope, sol_c.policy_claim.policy,
                  d_c.time_horizon if d_c else "static steady-state (no duration)", (Reason.SAMPLED_COVERAGE,),
                  (Evidence.make(EvidenceKind.SAMPLED, f"{len(tried)} torques in the band examined",
                                 examined=[[t, st] for t, st in tried]),),
                  ("a failing band centre does not fail the band",),
                  "no examined torque in the band is feasible with every part at the same witness, but no exclusion "
                  "proof covers the whole band")
    return claim, margin, d_c, None, sol_T


def _limiting_and_actions(req: Requirement, results: list[ConditionResult], sampled_range: bool) -> tuple[list, list, list]:
    limiting, actions, unevaluated = [], [], []
    seen = set()

    def add(lst, text):
        if text not in seen:
            seen.add(text)
            lst.append(text)

    for cr in results:
        sol = cr.primary                     # the accepted witness when a band found one (review R2 D-R2-03)
        tag = f"[Vdc={cr.scenario.Vdc_V:g} V]"
        rc = cr.rejected_centre
        if rc is not None:
            add(limiting, f"{tag} band centre {rc.T_request_Nm:g} N*m is {rc.policy_claim.status.value} (rejected "
                          f"candidate, diagnostic only); the requirement is answered at the witness "
                          f"{cr.primary_torque_Nm:g} N*m")
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
        if cr.capability is not None and cr.capability.witness is not None and cr.capability.accepted:
            add(limiting, f"{tag} policy capability {cr.capability.value_Nm:.6g} N*m limited by: "
                          f"{', '.join(cr.capability.active_constraints) or 'n/a'}")
        elif cr.capability is not None and cr.capability.gate_messages:
            add(limiting, f"{tag} policy capability not established (diagnostic only): "
                          + "; ".join(cr.capability.gate_messages))
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
        rc, margin, dur, wit_T, wit_sol = _requirement_claim(req, sc, ev, sol, cap, ratings, stated)
        results.append(ConditionResult(sc, sol, cap, dur, rc, margin, wit_T, wit_sol))
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
        # review R2 D-R2-02: the descriptions above are for people; the identity is the CONTENT of every model the
        # verdict used - all loss tables, axes, masks, scaling laws, auxiliary ownership, coefficients and flags
        "content_sha256": {
            "drive": content_sha256(drive), "requirement": content_sha256(req),
            "scenario_template": None if scenario is None else content_sha256(scenario),
            "source_limits": None if source_limits is None else content_sha256(source_limits),
            "ratings": [content_sha256(r) for r in ratings], "numerical_settings": content_sha256(settings)},
    }
    digest = sha256_of(snapshot)
    impl = implementation()
    rid = sha256_of({"input_sha256": digest, "implementation": impl})
    return DecisionRecord(
        record_id=f"DR-{req.req_id}-{rid[:12]}", requirement=req, drive=drive, conditions=tuple(results),
        verdict=agg, verdict_scope=scope, qualifiers=tuple(qualifiers), limiting_factors=tuple(limiting),
        next_actions=tuple(actions), unevaluated=tuple(unevaluated), assumptions=tuple(assumptions),
        snapshot=jsonable(snapshot), input_sha256=digest, settings=settings, implementation=dict(impl))


_jsonable = jsonable          # former private name (compatibility)
