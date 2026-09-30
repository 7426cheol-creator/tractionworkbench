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
from dataclasses import dataclass, field, replace as _replace

import numpy as np

from . import __version__, progress
from .analysis.error_budget import ErrorBudget, aggregate_robustness, constraint_robustness, torque_robustness
from .analysis.margin_sensitivity import loss_limited_margin
from .analysis.rating import RatingEnvelope, duration_claim
from .identity import content_sha256, implementation
from .models.components import DriveModel, RotationalLossModel
from .models.provenance import FIDELITY_ALLOWED_CLAIMS
from .requirement import Requirement
from .scenario import DcSourceLimits, Scenario
from .settings import DEFAULT_SETTINGS, NumericalSettings
from .solvers.capability import CapabilityResult, physical_capability, policy_capability
from .solvers.policy import POLICY_TEXT, PolicyEvaluator, PolicySolution
from .status import Aggregate, Claim, Evidence, EvidenceKind, Reason, Status, aggregate_and
from .units import shown


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
    range_certificates: tuple = ()
    source_coupling: dict = field(default_factory=dict)
    robustness: dict = field(default_factory=dict)     # model margin vs the declared error budget (never the verdict)
    iron_loss_scope: tuple = ()                        # field-weakening witnesses: what rests on the loss model
    margin_sensitivity: dict = field(default_factory=dict)   # loss-limited margin: loss budget + break-even changes

    @property
    def layers(self) -> dict:
        return claim_layers(self.requirement, self.drive, self.conditions, self.verdict, self.range_certificates,
                            self.source_coupling, self.robustness, self.iron_loss_scope)

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
            "vdc_range_certificates": list(self.range_certificates),
            **({"source_coupling": self.source_coupling} if self.source_coupling else {}),
            **({"margin_sensitivity": self.margin_sensitivity} if self.margin_sensitivity else {}),
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


def claim_layers(req: Requirement, drive: DriveModel, conditions, verdict: Aggregate, certificates=(),
                 source_coupling: dict | None = None, robustness: dict | None = None, iron_scope=()) -> dict:
    """Separate statements that must not be merged into one boolean.

    * mathematical  - the numerical evidence (exact enumeration / certificates / residuals vs sampled search);
    * model         - the requirement verdict for THIS model and data (the headline verdict);
    * requirement   - whether the requirement is complete enough to be decided (duration, quantifier, ...);
    * qualification - the evidence level of the data behind the model.  Never promoted automatically:
                      synthetic or unvalidated data, or a numerical certificate, are not hardware qualification;
    * robustness    - the model's torque margin against the declared error budget (analysis.error_budget):
                      whether the model answer survives what the model, its inputs and the numerics can be wrong
                      by.  It never changes the model verdict.
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
        if mc and not mc.get("certified") and not mc.get("decided_by_consequence"):
            cert_ok = False
    v_cert = bool(certificates) and all(c.get("applies") for c in certificates)
    sampled = (any(Reason.SAMPLED_COVERAGE in c.requirement_claim.reasons for c in conditions)
               or (req.is_range and not v_cert) or Reason.SAMPLED_COVERAGE in verdict.reasons)
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
    if req.is_range and not v_cert:
        open_items.append("Vdc range examined at sampled points: a continuous-range claim needs monotonicity or "
                          "denser analysis")
    if req.operator == "band":
        open_items.append("band requirement: existence of one torque inside the band (not tracking of every torque)")
    from .models.flux import FluxMapModel
    law_m = not isinstance(drive.motor.flux, FluxMapModel)
    if any("/winding" in cr.scenario.scenario_id for cr in conditions):
        open_items.append("winding temperature not stated: examined for all temperatures of the declared Rs law "
                          "(for-all)")
    if any("/magnet" in cr.scenario.scenario_id for cr in conditions):
        open_items.append("magnet temperature not stated: examined for all temperatures of the declared psi_PM law "
                          "(for-all, sampled)" if law_m else
                          "magnet temperature not stated: examined for all flux-map temperatures (for-all)")
    if source_coupling:
        src = source_coupling.get("source") or {}
        open_items.append(f"Vdc stated as battery OCV: judged at the inverter terminal voltage of a declared Thevenin "
                          f"source (R_eq {1e3 * float(src.get('R_eq_ohm') or 0.0):g} mOhm; {src.get('basis', '')}) - "
                          f"the source model's own validity (SOC, temperature, current) is part of the answer")
    quant = req.quantifiers()
    temps = sorted({cr.scenario.magnet_temp_C for cr in conditions if cr.scenario.magnet_temp_C is not None})
    quant["magnet"] = ((f"for all: every magnet temperature of the declared psi_PM law, sampled at "
                        f"{', '.join(f'{t:g}' for t in temps)} degC" if law_m else
                        f"for all: every flux-map magnet temperature examined ({', '.join(f'{t:g}' for t in temps)} "
                        f"degC)") if len(temps) > 1 and req.magnet_temp_C is None else
                       f"at {req.magnet_temp_C:g} degC (stated)" if req.magnet_temp_C is not None else
                       f"at {temps[0]:g} degC (scenario)" if temps else
                       "not stated: the model's reference or single flux-map plane")
    wtemps = sorted({cr.scenario.winding_temp_C for cr in conditions if "/winding" in cr.scenario.scenario_id})
    quant["winding"] = (f"at {req.winding_temp_C:g} degC (stated)" if req.winding_temp_C is not None else
                        (f"for all: every winding temperature of the declared Rs law ("
                         + ("worst end " if len(wtemps) == 1 else "sampled at ")
                         + f"{', '.join(f'{t:g}' for t in wtemps)} degC)") if wtemps else
                        "not stated: the model's Rs as supplied (no Rs temperature law declared)"
                        if drive.motor.rs_temperature is None else "stated in the scenario")
    unconf = sorted({q for cr in conditions if cr.duration is not None for q in cr.duration.qualifiers
                     if q.startswith("applicability to this product")})
    if unconf:
        open_items.append("rating evidence " + unconf[0][0].lower() + unconf[0][1:]
                          + " - bind the envelope (applies_to) and address its required conditions")
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
        "requirement": {"status": "COMPLETE" if not open_items else "OPEN_ITEMS", "open_items": open_items,
                        "quantifiers": quant},
        "qualification": {"status": q_status, "data_origin": origin, "validation_status": prov.validation_status,
                          "fidelity": drive.fidelity.value, "sub_models": _sub_models(drive) + [
                              f"iron loss at the field-weakening witness ({x['condition']}): {x['statement']}"
                              for x in iron_scope],
                          "iron_loss_scope": [dict(x) for x in iron_scope],
                          "meaning": "hardware qualification is a separate claim: a model PASS or a numerical "
                                     "certificate never qualifies the product; simplified loss/thermal/source models "
                                     "hold only in their declared narrow domain"},
        "robustness": robustness or {"status": "NOT_ASSESSED", "model_static": "not assessed",
                                     "meaning": "no error budget declared: the model margin is shown, its "
                                                "sufficiency for a design decision is not assessed"},
    }


def _condition_points(req: Requirement, samples: int) -> tuple[list[float], bool]:
    if not req.is_range:
        return [req.Vdc_V], False
    lo, hi = req.Vdc_V
    if lo == hi:
        return [lo], False
    return [float(v) for v in np.linspace(lo, hi, max(samples, 2))], True


def _scenario_for(req: Requirement, base: Scenario | None, limits: DcSourceLimits | None, vdc: float,
                  magnet_temp_C: float | None = None, winding_temp_C: float | None = None) -> Scenario:
    kw = dict(
        scenario_id=f"{req.req_id}@{req.speed_rpm:g}rpm/{vdc:g}V" + ("" if winding_temp_C is None
                                                                      else f"/winding {winding_temp_C:g}C")
        + ("" if magnet_temp_C is None else f"/magnet {magnet_temp_C:g}C"),
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
    if magnet_temp_C is not None:
        kw["magnet_temp_C"] = float(magnet_temp_C)
    if winding_temp_C is not None:
        kw["winding_temp_C"] = float(winding_temp_C)
    return Scenario(source_limits=limits or DcSourceLimits(), **kw)


def _magnet_points(req: Requirement, drive: DriveModel, base: Scenario | None, samples: int) -> tuple[list, bool, str]:
    """Magnet temperatures to examine -> (points, sampled, note).

    A requirement without a magnet temperature on a flux map with several temperature planes is examined FOR ALL the
    map's temperatures (the map 'as supplied' is all its planes): every plane, and - when the map declares linear
    interpolation between planes - sampled temperatures in between (then the continuous range is sampled, never
    certified).  [None] = no expansion (a stated temperature, a single plane, a constant-parameter model)."""
    from .models.flux import FluxMapModel
    stated = req.magnet_temp_C if req.magnet_temp_C is not None else (None if base is None else base.magnet_temp_C)
    flux = drive.motor.flux
    m = drive.motor
    if stated is None and not isinstance(flux, FluxMapModel) and m.psi_temperature is not None \
            and m.reference_magnet_temp_C is not None:
        # a declared psi_PM(T) law: the model's answer depends on a temperature the requirement does not state -
        # for all temperatures of the law, like the planes of a map (engineering review 2 of 63a2b61, F-02); the
        # law is continuous and psi_PM moves both the torque per ampere and the back-EMF, so no end is the worst
        # case in general: the range is sampled (ends + reference), never certified
        dep, ref = m.psi_temperature, m.reference_magnet_temp_C
        pts = _law_points(dep, ref)
        note = (f"magnet temperature not stated: examined for ALL magnet temperatures of the declared psi_PM law "
                f"({dep.valid_C[0]:g}..{dep.valid_C[1]:g} degC) at {', '.join(f'{t:g}' for t in pts)} degC (the ends "
                f"and the reference); psi_PM changes both the torque per ampere and the back-EMF, so the continuous "
                f"range is sampled, not certified - state the magnet temperature if the requirement applies at one "
                f"temperature only")
        return pts, True, note
    if stated is not None or not isinstance(flux, FluxMapModel) or len(flux.planes) < 2:
        return [None], False, ""
    temps = sorted(float(pl.magnet_temp_C) for pl in flux.planes if pl.magnet_temp_C is not None)
    if len(temps) != len(flux.planes):
        return [None], False, ""                     # an undeclared plane temperature: plane selection stays MISSING
    pts, sampled = list(temps), False
    if flux.temperature_interpolation == "linear":
        inner = [float(t) for t in np.linspace(temps[0], temps[-1], max(samples, len(temps)))]
        pts = sorted(set(pts) | set(inner))
        sampled = len(pts) > len(temps)
    note = (f"magnet temperature not stated: examined for ALL flux-map temperatures (planes "
            f"{', '.join(f'{t:g}' for t in temps)} degC" + (
                f"; declared linear interpolation sampled at {', '.join(f'{t:g}' for t in pts if t not in temps)} "
                f"degC" if sampled else "; no interpolation declared: the model has no other temperatures")
            + ") - state the magnet temperature if the requirement applies at one temperature only")
    return pts, sampled, note


def _law_points(dep, ref: float | None) -> list[float]:
    lo, hi = (float(x) for x in dep.valid_C)
    return sorted({lo, hi} | ({float(ref)} if ref is not None and lo <= ref <= hi else set()))


def _motoring(req: Requirement) -> bool:
    """Every requested shaft torque has the sign of the speed (or standstill) - so P_em >= 0 on the torque curve."""
    if req.operator == "band":
        ts = (req.target_Nm - req.band_Nm, req.target_Nm + req.band_Nm)
        return all(t * req.speed_rpm >= 0 for t in ts) and not (ts[0] <= 0.0 <= ts[1] and req.speed_rpm != 0)
    return req.target_Nm != 0 and req.target_Nm * req.speed_rpm >= 0


def winding_law_certificate(req: Requirement, drive: DriveModel, worst_C: float) -> dict:
    """Sufficient condition for an unstated winding temperature: the largest Rs of the declared law is the worst case.

    At a fixed operating point |v_cmd|^2 = |(Rs + R_drop) i + j w psi|^2 has d/dRs = 2 (v . i) + 2 R_drop I^2 =
    (4/3) P_ac + 2 R_drop I^2, and P_dc = P_ac + P_inv has d/dRs = 1.5 I^2; current, domain and torque do not depend
    on Rs.  For a motoring request P_ac = T_em w_m + P_cu >= 0 at EVERY point of the torque curve (T_em = T_shaft +
    tau_rot, passive drag), so every constraint only tightens as Rs grows: the feasible set shrinks.  The
    minimum-current point at a smaller Rs then has no more current, and with the I^2-form inverter loss no more P_dc.
    So the static claim at the largest Rs holds at every smaller Rs of the law (and a violation at the smallest Rs
    holds at every larger one).  Regeneration (P_ac changes sign on the curve), a pointwise module loss or a duration
    are outside it."""
    from .models.components import LOSS_QUADRATIC
    inv = drive.inverter
    checks = []

    def chk(name, ok, detail):
        checks.append({"condition": name, "holds": bool(ok), "detail": detail})
    chk("static motoring request (torque and speed of one sign, or standstill): P_ac >= 0 on the whole torque curve",
        _motoring(req), f"{req.target_Nm:g} N*m at {req.speed_rpm:g} rpm")
    quad = inv.module_loss is None and (inv.loss is None or (inv.loss_kind == LOSS_QUADRATIC and
                                                           inv.loss.ipk2_coeff_W_per_A2 >= 0))
    chk("inverter loss of the I^2 form a0 + a2 I^2 (or none)", quad,
        "datasheet module loss (pointwise)" if inv.module_loss is not None else "I^2 form")
    rot = drive.motor.rotational_loss
    chk("speed-only rotational loss (the torque curve does not move with Rs)",
        rot is None or isinstance(rot, RotationalLossModel), "speed-only" if rot is not None else "none")
    chk("static requirement (a duration is not covered)", req.duration_s is None,
        "static" if req.duration_s is None else req.duration_text())
    ok = all(c["holds"] for c in checks)
    return {"applies": ok, "worst_C": worst_C, "checks": checks,
            "statement": (f"motoring: d|v|^2/dRs = (4/3) P_ac >= 0 and dP_dc/dRs = 1.5 I^2 at every point of the "
                          f"torque curve, so the largest Rs ({worst_C:g} degC) is the worst case for every constraint "
                          f"- the static claim there holds for the whole law range")}


def _winding_points(req: Requirement, drive: DriveModel, base: Scenario | None) -> tuple[list, bool, str, dict | None]:
    """Winding temperatures to examine -> (points, sampled, note, certificate).  A requirement without a winding
    temperature on a drive that declares an Rs(T) law is examined for all temperatures of the law (engineering review
    2 of 63a2b61, F-02): at the worst end only when ``winding_law_certificate`` holds, else at the ends and the
    reference (sampled).  [None] = no expansion."""
    stated = req.winding_temp_C if req.winding_temp_C is not None else (None if base is None else base.winding_temp_C)
    m = drive.motor
    dep, ref = m.rs_temperature, m.reference_winding_temp_C
    if stated is not None or dep is None or ref is None:
        return [None], False, "", None
    lo, hi = (float(x) for x in dep.valid_C)

    def rs(t):
        return m.Rs_ohm * (1.0 + dep.coeff_per_K * (t - ref))
    worst = hi if rs(hi) >= rs(lo) else lo
    cert = winding_law_certificate(req, drive, worst)
    law = f"the declared Rs law ({lo:g}..{hi:g} degC, {dep.basis})"
    if cert["applies"]:
        return [worst], False, (f"winding temperature not stated: examined for ALL winding temperatures of {law}: "
                                + cert["statement"]), cert
    pts = _law_points(dep, ref)
    failed = "; ".join(c["condition"] for c in cert["checks"] if not c["holds"])
    return pts, True, (f"winding temperature not stated: examined for ALL winding temperatures of {law} at "
                       f"{', '.join(f'{t:g}' for t in pts)} degC; no worst-end certificate ({failed}): the continuous "
                       f"range is sampled - state the winding temperature if the requirement applies at one "
                       f"temperature only"), cert


def vdc_range_certificate(req: Requirement, drive: DriveModel, low: "ConditionResult") -> dict:
    """Sufficient condition for a static Vdc-range claim from its LOW-voltage endpoint (engineering review 6198099,
    priority 4).

    For a static motoring (discharging) request at a fixed speed, fixed temperatures, domain and source limits, with
    the inverter loss the Vdc-independent surrogate a0 + a2 I^2 (a0, a2 >= 0, valid over the range): the witness point
    at the low endpoint stays a witness at every higher Vdc - the voltage budget (1 - r_v) Vdc / sqrt 3 only grows,
    the current and domain constraints do not depend on Vdc, P_dc at a fixed point does not change and
    I_dc = P_dc / Vdc falls.  So the minimum-current point exists at every Vdc of the range with no more current and
    no more P_dc than at the low endpoint, and every constraint holds there.  This proves the STATIC subclaim only;
    regen (charge acceptance grows with Vdc), Vdc-dependent switching loss or temperature / source-limit changes are
    outside it, and a duration or hardware qualification is never proven by it."""
    from .models.components import LOSS_QUADRATIC
    lo, hi = req.Vdc_V
    inv = drive.inverter
    checks = []

    def chk(name, ok, detail):
        checks.append({"condition": name, "holds": bool(ok), "detail": detail})
    motoring = _motoring(req)
    chk("static motoring request (torque and speed of one sign, or standstill)", motoring,
        f"{req.target_Nm:g} N*m at {req.speed_rpm:g} rpm" + (f" (band +/-{req.band_Nm:g} N*m)"
                                                            if req.operator == "band" else ""))
    ls = inv.loss
    quad = inv.module_loss is None and ls is not None and inv.loss_kind == LOSS_QUADRATIC
    valid = quad and ls.offset_W >= 0 and ls.ipk2_coeff_W_per_A2 >= 0 and (
        ls.valid_Vdc_V is None or (ls.valid_Vdc_V[0] <= lo and hi <= ls.valid_Vdc_V[1]))
    chk("Vdc-independent inverter loss a0 + a2 I^2 (a0, a2 >= 0) valid over the range", valid,
        "datasheet module loss (Vdc-dependent switching)" if inv.module_loss is not None else
        "no inverter loss model" if ls is None else
        f"a0 = {ls.offset_W:g} W, a2 = {ls.ipk2_coeff_W_per_A2:g} W/A^2, valid Vdc "
        f"{'not restricted' if ls.valid_Vdc_V is None else list(ls.valid_Vdc_V)}")
    rot = drive.motor.rotational_loss
    chk("speed-only rotational / iron loss (b w + c w|w|; no id/iq-dependent iron loss)",
        rot is None or isinstance(rot, RotationalLossModel),
        "an id/iq-dependent iron-loss model would void this argument" if rot is not None else "no rotational loss")
    vb_lo, vb_hi = inv.voltage.command_budget_V(lo), inv.voltage.command_budget_V(hi)
    chk("voltage budget non-decreasing in Vdc", vb_hi >= vb_lo,
        f"{vb_lo:.6g} V at {lo:g} V, {vb_hi:.6g} V at {hi:g} V ((1 - r_v) Vdc / sqrt 3)")
    static = low.primary.policy_claim.status             # the static part only: a duration is not proven by this
    ok_low = static is Status.FEASIBLE and abs(low.scenario.Vdc_V - lo) <= 1e-9 * max(1.0, lo)
    chk("static claim FEASIBLE at the low-voltage endpoint", ok_low, f"{static.value} at {low.scenario.Vdc_V:g} V")
    pt = low.primary.point
    pdc = None if pt is None else pt.Pdc_W
    chk("discharging witness (P_dc > 0)", pdc is not None and pdc > 0,
        "no witness point" if pdc is None else f"P_dc = {pdc:.6g} W")
    static_ok = all(c["holds"] for c in checks)
    return {"static_certified": static_ok, "applies": static_ok and req.duration_s is None, "range_V": [lo, hi],
            "checks": checks,
            "statement": ("static claim certified for every Vdc in [{lo:g}, {hi:g}] V from the low endpoint: the low "
                          "witness stays a witness as Vdc rises (budget grows; I, domain, P_dc unchanged; I_dc = "
                          "P_dc/Vdc falls)").format(lo=lo, hi=hi)}


BAND_SAMPLES = 9


def _requirement_claim(req: Requirement, sc: Scenario, ev: PolicyEvaluator, sol: PolicySolution,
                       cap: CapabilityResult | None, ratings, stated: dict, product: dict | None = None):
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
        return _band_claim(req, sc, ev, sol, cap, ratings, stated, margin, product)
    policy = sol.policy_claim
    dur = duration_claim(ratings, req.duration_s, req.speed_rpm, req.target_Nm, stated, product)
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


def _band_claim(req, sc, ev, sol_c, cap, ratings, stated, margin, product=None):
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
        d_T = duration_claim(ratings, req.duration_s, req.speed_rpm, T, stated, product)
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
        d_small = duration_claim(ratings, req.duration_s, req.speed_rpm, t_small, stated, product)
        if d_small is not None and d_small.status is Status.INFEASIBLE:
            proofs.append(f"even the smallest |T| in the band ({t_small:g} N*m) is not rated for {req.duration_text()}")
            evid.extend(d_small.evidence)
    sol_T = sol_c
    d_c = duration_claim(ratings, req.duration_s, req.speed_rpm, req.target_Nm, stated, product)
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
        tag = (f"[Vdc={cr.scenario.Vdc_V:g} V" + (f", winding {cr.scenario.winding_temp_C:g} degC"
                                                  if "/winding" in cr.scenario.scenario_id else "")
               + (f", magnet {cr.scenario.magnet_temp_C:g} degC" if "/magnet" in cr.scenario.scenario_id else "") + "]")
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
            add(limiting, f"{tag} policy capability {shown(cr.capability.value_Nm, 'capability')} N*m limited by: "
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


def iron_loss_scope(drive: DriveModel, k, pt, torque_margin_Nm: float | None) -> dict | None:
    """At a voltage-limited (field-weakening) witness: how much the torque and DC margins rest on the rotational /
    iron-loss model (review of 63a2b61, 3.1).  The loss is a speed-only loss torque in this tool (the certificates
    rely on that form), so at reduced flux it does not follow the operating point.  Numbers of the record only - no
    threshold and no correction.  None when the witness is not voltage-limited or has no rotational loss model."""
    rot = drive.motor.rotational_loss
    vol = None if pt is None else pt.constraint("VOLTAGE")
    if rot is None or vol is None or vol.state != "ACTIVE" or pt.Prot_W is None:
        return None
    try:
        psd0, psq0, ok0 = k.flux(0.0, 0.0)
        psi0 = math.hypot(float(psd0), float(psq0)) if bool(ok0) else None
    except Exception:  # noqa: BLE001 - no no-load flux (outside the data): the ratio is not stated
        psi0 = None
    psi = math.hypot(pt.psi_d_Wb, pt.psi_q_Wb)
    motoring = (pt.Pdc_W or 0.0) > 0
    dis = [c for c in pt.constraints if c.group == "DISCHARGE_SOURCE" and c.slack is not None and math.isfinite(c.slack)]
    dc_w = min((c.slack if c.unit == "W" else c.slack * k.Vdc) for c in dis) if (dis and motoring) else None
    nm_per_kw = 1e3 / abs(k.omega_m) if k.omega_m else None
    r = None if not psi0 else psi / psi0
    head = (f"field-weakening witness (voltage limit active" + ("" if r is None else f", |psi| = {100 * r:.0f} % of the "
                                                                                 f"no-load flux") + ")")
    margins = (("" if torque_margin_Nm is None else f" the torque margin {torque_margin_Nm:.4g} N*m")
               + ("" if dc_w is None else f" and the smallest DC discharge margin {dc_w:.4g} W")
               + ("" if nm_per_kw is None else f" - 1 kW of loss is {nm_per_kw:.3g} N*m at this speed"))
    if not rot.includes_iron_loss and motoring:
        direction = "unmodelled"
        text = (f"{head}: the iron loss is NOT in the model (the rotational loss is mechanical: {rot.basis}); iron loss "
                f"at this point would lower{margins}: declare it in the error budget (model) or use loss data that "
                f"include it")
    elif not rot.includes_iron_loss:
        direction = "conservative"
        text = (f"{head}: the iron loss is NOT in the model (mechanical rotational loss: {rot.basis}); for "
                f"regenerative braking an unmodelled loss adds braking torque and eases a charge limit - the model is "
                f"on the conservative side for this claim")
    else:
        direction = "not proven"
        help_ = ("less braking help than modelled" if not motoring else "less loss than modelled")
        text = (f"{head}: the iron loss is a speed-only loss torque ({rot.basis}) that does not follow the flux - at "
                f"reduced flux the fundamental iron loss is likely smaller ({help_}), harmonic and PM eddy-current "
                f"loss are not represented, so neither direction is proven; P_rot = {pt.Prot_W:.4g} W against"
                + (margins or " the margins"))
    return {"flux_ratio": r, "psi_Wb": psi, "psi_no_load_Wb": psi0, "P_rot_W": pt.Prot_W,
            "tau_rot_Nm": pt.Prot_W / k.omega_m if k.omega_m else None, "includes_iron_loss": rot.includes_iron_loss,
            "basis": rot.basis, "motoring": motoring, "direction": direction, "torque_margin_Nm": torque_margin_Nm,
            "dc_margin_W": dc_w, "Nm_per_kW": nm_per_kw, "statement": text}


def _condition_tag(sc: Scenario, several_magnets: bool, several_windings: bool = False) -> str:
    return (f"Vdc {sc.Vdc_V:g} V" + (f" / winding {sc.winding_temp_C:g} degC" if several_windings else "")
            + (f" / magnet {sc.magnet_temp_C:g} degC" if several_magnets else ""))


def requirement_robustness(req: Requirement, results, budget: ErrorBudget | None, *, sampled: bool = False,
                           range_certified: bool = False, source_coupled: bool = False,
                           verdict: Aggregate | None = None, source_status: str | None = None) -> dict:
    """The static torque claim's capability margin at every condition against the declared error budget
    (``analysis.error_budget``), aggregated with the requirement's for-all quantifier.  The model verdict is not
    touched.  'Met' uses the found capability (a verified witness), 'not met' a certified upper bound of it."""
    if req.operator == "band":
        edge = req.target_Nm - req.band_Nm if req.direction > 0 else req.target_Nm + req.band_Nm
        edge_basis = (f"band edge {edge:g} N*m nearest to zero on the capability side (the band needs one torque "
                      f"inside it)")
    else:
        edge, edge_basis = req.target_Nm, f"the requested torque {req.target_Nm:g} N*m"
    several = len({cr.scenario.magnet_temp_C for cr in results}) > 1
    several_w = any("/winding" in cr.scenario.scenario_id for cr in results)
    source_reason = ("the capability is evaluated at the terminal voltage resolved for the target torque; with the "
                     "declared source the terminal voltage moves with the torque, so this margin is not the coupled "
                     "system's margin")
    per, names, checks = [], [], []
    for i, cr in enumerate(results):
        cap = cr.capability
        c_lo = cap.value_Nm if cap is not None and cap.accepted else None
        c_hi = None
        if c_lo is not None and cap.bound_Nm is not None and any(e.kind is EvidenceKind.CERTIFIED_BOUND
                                                                  for e in cap.evidence):
            c_hi = cap.bound_Nm
        if req.operator == "band":             # existence: the capability answers it; a centre's status does not
            st = "FEASIBLE" if cr.witness_torque_Nm is not None else None
        else:
            st = cr.solution.policy_claim.status.value
        r = torque_robustness(budget, req.direction, edge, c_lo, c_hi, st)
        if source_coupled and r["robust"] is not None:
            r = {**r, "robust": None, "status": "NOT_ESTABLISHED_SOURCE", "reason": source_reason}
        per.append(r)
        names.append(_condition_tag(cr.scenario, several, several_w))
        # the declared limit errors at a witness that meets the requirement (y + Delta <= y_max per limit)
        wit_ok = (cr.witness_torque_Nm is not None if req.operator == "band"
                  else cr.solution.policy_claim.status is Status.FEASIBLE)
        pt = cr.primary.point
        if budget is not None and wit_ok and pt is not None:
            for c in constraint_robustness(budget, pt.constraints):
                if source_coupled and c["robust"] is not None:
                    c = {**c, "robust": None, "status": "NOT_ESTABLISHED_SOURCE",
                         "reason": "with the declared source the DC terminal voltage moves with the operating point, "
                                   "so this witness margin is not the coupled system's margin"}
                checks.append({**c, "condition": i})
    out = aggregate_robustness(per, names, budget, checks)
    # the verdict can rest on more than the static torque claim (duration, source coupling, range coverage): say so
    others = []
    if any(cr.duration is not None and cr.duration.status is not Status.FEASIBLE for cr in results):
        others.append("duration")
    if source_coupled and source_status not in (None, "RESOLVED"):
        others.append("source")
    if verdict is not None and Reason.SAMPLED_COVERAGE in verdict.reasons:
        others.append("coverage")
    static_answer = {"met": Status.FEASIBLE, "not met": Status.INFEASIBLE}.get(out["model_static"])
    if verdict is not None and others and verdict.status is not static_answer:
        words = {"duration": "the duration part (its own rating evidence)", "source": "the source coupling",
                 "coverage": "the coverage of the continuous range"}
        rel = (f"the verdict ({verdict.status.verdict}) is decided by {' and '.join(words[o] for o in others)}, which "
               f"this comparison does not cover")
        out = {**out, "verdict_relation": {"verdict": verdict.status.verdict, "parts": others, "text": rel},
               "meaning": out["meaning"] + "; " + rel}
    if (sampled or range_certified) and len(per) > 1 and out["status"] != "NOT_ASSESSED":
        cov = "range_certified" if range_certified else "examined_only"
        out = {**out, "coverage": cov, "meaning": out["meaning"] + (
            " (at the examined conditions; the static claim is certified over the Vdc range, the margin comparison is "
            "not)" if range_certified else " (at the examined conditions only; the continuous range is not "
                                           "established)")}
    return {**out, "T_edge_Nm": edge, "T_edge_basis": edge_basis, "scope":
            "static torque claim only: a duration part keeps its own rating evidence; operating-condition "
            "uncertainty (Vdc, temperatures) belongs in the requirement as a range; 'met' is judged at the found "
            "capability (a verified witness), 'not met' at a certified upper bound, so the capability's numerical "
            "tolerance needs no budget line"}


def _loss_limited_sensitivity(req: Requirement, drive: DriveModel, results, settings, several=(False, False)):
    """The governing loss-limited FEASIBLE condition's loss budget and break-even parameter changes (engineering
    review 2 of 63a2b61, F-03); None when no FEASIBLE condition is limited by a DC limit."""
    if req.operator == "band":
        edge = req.target_Nm - req.band_Nm if req.direction > 0 else req.target_Nm + req.band_Nm
    else:
        edge = req.target_Nm
    best = None
    for cr in results:
        cap = cr.capability
        if cr.requirement_claim.status is not Status.FEASIBLE or cap is None or not cap.accepted:
            continue
        if not any(a.startswith("DC_") for a in cap.active_constraints or ()):
            continue
        m = req.direction * (cap.value_Nm - edge)
        if best is None or m < best[0]:
            best = (m, cr)
    if best is None:
        return None
    cr = best[1]
    with progress.span(1, "margin sensitivity (loss-limited)") as steps:
        steps.step()
        out = loss_limited_margin(drive, cr.scenario, req.direction, edge, cr.capability, cr.primary.point, settings)
    if out is None:
        return None
    return {"condition": _condition_tag(cr.scenario, *several), **out}


def _not_decisive(c: Claim) -> Claim:
    if c.status is not Status.INFEASIBLE:
        return c
    note = ("terminal voltage not resolved: a violation at the highest terminal voltage an operating point can see "
            "is not decisive")
    return _replace(c, status=Status.UNKNOWN, reasons=(Reason.COUPLED_MODEL_REQUIRED,),
                    qualifiers=tuple(dict.fromkeys((*c.qualifiers, note))), detail=f"{c.detail} ({note})")


def evaluate_requirement(req: Requirement, drive: DriveModel, *, scenario: Scenario | None = None,
                         source_limits: DcSourceLimits | None = None, ratings: tuple[RatingEnvelope, ...] = (),
                         settings: NumericalSettings = DEFAULT_SETTINGS, range_samples: int = 5,
                         with_capability: bool = True, extra_claims: tuple = (),
                         source_coupling: dict | None = None,
                         error_budget: ErrorBudget | None = None) -> DecisionRecord:
    """``extra_claims``: requirement-level claims AND-aggregated with the conditions (e.g. the DC source coupling
    of an OCV-stated Vdc); ``source_coupling``: the resolution record behind such a claim; ``error_budget``: the
    declared error the torque margin is compared with (a separate layer - the verdict never changes)."""
    vdcs, sampled_v = _condition_points(req, range_samples)
    mags, sampled_t, mag_note = _magnet_points(req, drive, scenario, range_samples)
    winds, sampled_w, wind_note, wind_cert = _winding_points(req, drive, scenario)
    sampled = sampled_v or sampled_t or sampled_w
    results: list[ConditionResult] = []
    product = ({"drive_id": drive.drive_id, "drive_revision": drive.revision,
                "drive_content_sha256": content_sha256(drive)} if ratings else None)
    points = [(v, w, t) for w in winds for t in mags for v in vdcs]
    iron = []
    with progress.span(len(points), "operating conditions") as steps:
        for vdc, wind, mag in points:
            steps.step()
            sc = _scenario_for(req, scenario, source_limits, vdc, mag, wind)
            ev = PolicyEvaluator(drive, sc, settings)
            sol = ev.solve(req.target_Nm)
            cap = None
            if with_capability and ev.speed_in_domain and ev.k.evaluable and ev.k.tau_rot is not None:
                cap = policy_capability(ev, req.direction)
            stated = {"coolant_temp_C": sc.coolant_temp_C, "initial_state": sc.initial_state, "Vdc_V": sc.Vdc_V,
                      "switching_frequency_Hz": sc.switching_frequency_Hz, "winding_temp_C": sc.winding_temp_C,
                      "magnet_temp_C": sc.magnet_temp_C}
            rc, margin, dur, wit_T, wit_sol = _requirement_claim(req, sc, ev, sol, cap, ratings, stated, product)
            results.append(ConditionResult(sc, sol, cap, dur, rc, margin, wit_T, wit_sol))
            fw = iron_loss_scope(drive, ev.k, (wit_sol or sol).point, margin)
            if fw is not None:
                iron.append((len(results) - 1, fw))
    if (source_coupling or {}).get("status") == "NOT_RESOLVED":
        # the drive side is shown at the highest terminal voltage an operating point can see; with the terminal
        # voltage not resolved a violation there is not decisive (e.g. a regen charge limit loosens as the voltage
        # falls): never a FAIL from it (engineering review 2 of 63a2b61, F-04)
        results = [_replace(r, requirement_claim=_not_decisive(r.requirement_claim)) for r in results]
    agg = aggregate_and([*(r.requirement_claim for r in results), *extra_claims])
    qualifiers = [q for q in (wind_note, mag_note) if q]
    certificates = []
    if sampled_v:                          # a Vdc range: the monotonicity certificate per temperature condition
        for wind in winds:
            for mag in mags:
                group = [r for r in results if (mag is None or r.scenario.magnet_temp_C == mag)
                         and (wind is None or r.scenario.winding_temp_C == wind)]
                certificates.append({"magnet_temp_C": mag, **({} if wind is None else {"winding_temp_C": wind}),
                                     **vdc_range_certificate(req, drive, group[0])})
    v_cert = bool(certificates) and all(c["applies"] for c in certificates)
    if sampled and agg.status is Status.FEASIBLE:
        if v_cert and not sampled_t and not sampled_w:
            qualifiers.append(certificates[0]["statement"] + " (conditions: " + "; ".join(
                c["condition"] for c in certificates[0]["checks"]) + ")")
        else:
            agg = Aggregate(Status.UNKNOWN, (Reason.SAMPLED_COVERAGE,), agg.deciding_claims)
            what = " x ".join(x for x in (f"{len(vdcs)} Vdc point(s)" if sampled_v else "",
                                          f"{len(winds)} winding temperature(s)" if sampled_w else "",
                                          f"{len(mags)} magnet temperature(s)" if sampled_t else "") if x)
            qualifiers.append(f"FEASIBLE at every examined point ({what}); the continuous range is not established")
    if sampled_v and certificates and not v_cert:
        failed = sorted({c["condition"] for cert in certificates for c in cert["checks"] if not c["holds"]})
        static_only = all(c["static_certified"] for c in certificates)
        qualifiers.append("the static part is certified over the Vdc range by monotonicity; the duration part stays "
                          "sampled" if static_only else
                          "no Vdc-monotonicity certificate (" + "; ".join(failed) + ")")
    several_w = winds != [None]
    if agg.status is Status.INFEASIBLE and (sampled or len(mags) > 1 or several_w):
        bad = [r.scenario for r in results if r.requirement_claim.status is Status.INFEASIBLE]
        qualifiers.append("counterexample(s) inside the examined range at " + ", ".join(
            f"{sc.Vdc_V:g} V" + ("" if not several_w else f" / winding {sc.winding_temp_C:g} degC")
            + ("" if sc.magnet_temp_C is None or len(mags) < 2 else f" / magnet {sc.magnet_temp_C:g} degC")
            for sc in bad) + ": the for-all requirement fails")
    for r in results:
        for q in r.requirement_claim.qualifiers:
            if q not in qualifiers:
                qualifiers.append(q)
    for c in extra_claims:
        for q in (*c.qualifiers, c.detail):
            if q and q not in qualifiers:
                qualifiers.append(q)
    scope = (f"{drive.drive_id} rev {drive.revision} ({drive.fidelity.value}, {drive.provenance.origin.value} data, "
             f"{drive.provenance.validation_status}); minimum-current policy; static fundamental steady state; "
             f"declared operating domain; n = {req.speed_rpm:g} rpm; Vdc "
             + (f"in [{req.Vdc_V[0]:g}, {req.Vdc_V[1]:g}] V ({'certified by monotonicity' if v_cert else 'sampled'})"
                if req.is_range else f"= {req.Vdc_V:g} V")
             + ("" if not several_w else f"; winding temperature for all of the declared Rs law ("
                + ("worst end " if not sampled_w else "sampled at ") + ', '.join(f'{t:g}' for t in winds) + " degC)")
             + ("" if len(mags) < 2 else f"; magnet temperature for all of {', '.join(f'{t:g}' for t in mags)} degC")
             + ("" if req.duration_s is None else f"; duration {req.duration_text()}"))
    limiting, actions, unevaluated = _limiting_and_actions(req, results, sampled_v and not (v_cert and not sampled_t
                                                                                           and not sampled_w))
    if sampled_w or sampled_t or several_w:
        actions.append("state the winding / magnet temperature (or its range) in the requirement: an unstated "
                       "temperature on a temperature-dependent model is judged for every temperature the model declares")
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
        "vdc_range_certificates": certificates,
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
    if source_coupling:                    # part of the input identity only when present (other records unchanged)
        snapshot["source_coupling"] = source_coupling
    if error_budget is not None:
        snapshot["error_budget"] = error_budget.describe()
    if extra_claims:
        snapshot["extra_claims"] = [c.to_dict() for c in extra_claims]
    sens = _loss_limited_sensitivity(req, drive, results, settings, several=(len(mags) > 1, several_w))
    if sens:
        qualifiers.append(
            f"loss-limited margin {sens['margin_Nm']:.3g} N*m at {sens['condition']}"
            + ("" if sens["loss_budget_W"] is None else
               f": {sens['loss_budget_W'] / 1e3:.3g} kW of additional loss at the witness reaches the DC limit")
            + (f"; erased by about {'; '.join(sens['erased_by'])} (linear estimate)" if sens["erased_by"] else "")
            + " - the loss models behind it: " + "; ".join(sens["loss_models"]))
    robustness = requirement_robustness(req, results, error_budget, sampled=sampled, range_certified=v_cert,
                                        source_coupled=bool(source_coupling), verdict=agg,
                                        source_status=(source_coupling or {}).get("status"))
    digest = sha256_of(snapshot)
    impl = implementation()
    rid = sha256_of({"input_sha256": digest, "implementation": impl})
    return DecisionRecord(
        record_id=f"DR-{req.req_id}-{rid[:12]}", requirement=req, drive=drive, conditions=tuple(results),
        range_certificates=tuple(certificates),
        verdict=agg, verdict_scope=scope, qualifiers=tuple(qualifiers), limiting_factors=tuple(limiting),
        next_actions=tuple(actions), unevaluated=tuple(unevaluated), assumptions=tuple(assumptions),
        snapshot=jsonable(snapshot), input_sha256=digest, settings=settings, implementation=dict(impl),
        source_coupling=dict(source_coupling or {}), robustness=jsonable(robustness),
        iron_loss_scope=tuple(jsonable({"condition": _condition_tag(results[i].scenario, len(mags) > 1, several_w),
                                        **x}) for i, x in iron),
        margin_sensitivity=jsonable(sens or {}))


_jsonable = jsonable          # former private name (compatibility)
