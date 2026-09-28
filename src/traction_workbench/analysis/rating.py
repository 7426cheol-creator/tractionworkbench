"""External peak/continuous rating envelopes (UC08).

A fixed-temperature electrical capability is never renamed "10-second peak"
or "continuous".  A duration claim comes only from a validated external
rating envelope whose every declared condition (coolant, initial state, Vdc,
switching frequency, ...) matches the requirement; the envelope is only
interpolated between its own speed points (never extrapolated).  Without such
an envelope the duration claim is UNKNOWN.

Typed duration semantics (independent review F04):

* a continuous requirement is answered only by a continuous rating - a finite
  (e.g. 10 s) rating never passes a continuous requirement;
* a finite requirement D is answered by a finite rating of the same or a
  longer duration (a torque allowed for D_r seconds from the declared initial
  state is allowed for any D <= D_r from that state) or by a continuous rating
  when the requirement declares a start that is not hotter than that
  equilibrium (cold / equilibrium at the coolant);
* the result does not depend on the order of the envelopes: every applicable
  envelope of the highest declared priority is evaluated; equally
  authoritative envelopes that disagree give UNKNOWN (CONFLICTING_EVIDENCE);
* outside a validated envelope the requirement is not *rated*
  (RATING_NOT_MET); that is not a proof of physical impossibility;
* approval is an explicit TYPED state (second review R2, D-R2-01), default not
  approved, tied to an evidence identity (document id and revision) and a
  declared intended use.  A synthetic / estimated origin, a missing, rejected,
  not-approved or unknown state, or a blank evidence identity is evaluated as a
  model experiment and never answers a requirement.  Free-text provenance
  (``validation_status``) describes the data; it never approves them;
* the motoring table applies when torque and speed have the same sign (P >= 0),
  the braking table when they differ - also at negative speed (signed quadrants);
* a stated condition that is not a finite number never matches (NaN compares
  false, it is not "within tolerance").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from ..errors import InputValidationError
from ..validation import finite as _finite
from ..models.provenance import DataOrigin, Provenance
from ..status import Claim, Evidence, EvidenceKind, Reason, Status


class ApprovalState(str, Enum):
    APPROVED = "approved"
    NOT_APPROVED = "not_approved"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RatingApproval:
    """Explicit approval of a rating envelope as evidence: a typed state linked to the evidence that approves it.

    Only ``APPROVED`` with a non-blank evidence id, evidence revision and intended use approves; everything else
    (the default included) leaves the envelope a model experiment."""

    state: ApprovalState = ApprovalState.UNKNOWN
    evidence_id: str = ""          # rating sheet / test report / release note that approves the envelope
    evidence_revision: str = ""
    intended_use: str = ""         # what the approval covers, e.g. "10 s peak rating for requirement verification"
    approved_by: str = ""          # organisation / role

    def __post_init__(self):
        try:
            object.__setattr__(self, "state", ApprovalState(self.state))
        except ValueError:
            raise InputValidationError(f"approval state must be one of {[s.value for s in ApprovalState]}",
                                       field="approval.state") from None
        for name in ("evidence_id", "evidence_revision", "intended_use", "approved_by"):
            object.__setattr__(self, name, str(getattr(self, name) or "").strip())

    def to_dict(self) -> dict:
        return {"state": self.state.value, "evidence_id": self.evidence_id,
                "evidence_revision": self.evidence_revision, "intended_use": self.intended_use,
                "approved_by": self.approved_by}


@dataclass(frozen=True)
class RatingEnvelope:
    envelope_id: str
    revision: str
    duration_s: float
    speed_rpm: tuple
    max_motoring_torque_Nm: tuple
    provenance: Provenance
    min_braking_torque_Nm: tuple | None = None
    conditions: tuple = ()             # (("coolant_temp_C", 65.0), ("Vdc_V", (550, 650)), ...)
    condition_tolerances: tuple = ()   # (("coolant_temp_C", 1.0), ...)
    interpolation: str = "conservative"
    port: str = "motor_shaft"
    evidence_kind: str = "supplier_rated"
    priority: int = 0                  # authority when several envelopes apply (higher wins); ties must agree
    approval: RatingApproval | None = None   # typed approval; None = not approved (a model experiment)

    def __post_init__(self):
        if self.approval is not None and not isinstance(self.approval, RatingApproval):
            raise InputValidationError("approval must be a RatingApproval", field="approval")
        for key, want in self.conditions:
            vals = want if isinstance(want, tuple) else (want,)
            for v in vals:
                if not isinstance(v, str) and not math.isfinite(float(v)):
                    raise InputValidationError(f"rating condition {key!r} must be finite", field="conditions")
        for key, t in self.condition_tolerances:
            if not math.isfinite(float(t)) or float(t) < 0:
                raise InputValidationError(f"condition tolerance {key!r} must be finite and >= 0",
                                           field="condition_tolerances")
        d = float(self.duration_s)
        if math.isnan(d) or d <= 0:
            raise InputValidationError("rating duration must be > 0 (math.inf for continuous)", field="duration_s")
        sp = tuple(_finite("speed_rpm", x) for x in self.speed_rpm)
        if len(sp) < 2 or any(b <= a for a, b in zip(sp, sp[1:])):
            raise InputValidationError("rating speed axis must be strictly increasing with >= 2 points", field="speed_rpm")
        tm = tuple(_finite("max_motoring_torque_Nm", x) for x in self.max_motoring_torque_Nm)
        if len(tm) != len(sp):
            raise InputValidationError("torque table length must match the speed axis", field="max_motoring_torque_Nm")
        if self.min_braking_torque_Nm is not None:
            tb = tuple(_finite("min_braking_torque_Nm", x) for x in self.min_braking_torque_Nm)
            if len(tb) != len(sp):
                raise InputValidationError("torque table length must match the speed axis", field="min_braking_torque_Nm")
            object.__setattr__(self, "min_braking_torque_Nm", tb)
        if self.interpolation not in ("conservative", "linear_declared"):
            raise InputValidationError("interpolation must be 'conservative' or 'linear_declared'", field="interpolation")
        if self.port != "motor_shaft":
            raise InputValidationError("only motor-shaft rating envelopes are supported", field="port")
        object.__setattr__(self, "duration_s", d)
        object.__setattr__(self, "speed_rpm", sp)
        object.__setattr__(self, "max_motoring_torque_Nm", tm)

    @property
    def duration_text(self) -> str:
        return "continuous" if math.isinf(self.duration_s) else f"{self.duration_s:g} s"

    def describe(self) -> dict:
        return {
            "envelope_id": self.envelope_id,
            "revision": self.revision,
            "duration": self.duration_text,
            "speed_rpm": list(self.speed_rpm),
            "max_motoring_torque_Nm": list(self.max_motoring_torque_Nm),
            "min_braking_torque_Nm": None if self.min_braking_torque_Nm is None else list(self.min_braking_torque_Nm),
            "conditions": {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.conditions},
            "condition_tolerances": dict(self.condition_tolerances),
            "interpolation": self.interpolation,
            "provenance": self.provenance.to_dict(),
            "evidence_kind": self.evidence_kind,
            "priority": self.priority,
            "approval": None if self.approval is None else self.approval.to_dict(),
        }


def _match_conditions(env: RatingEnvelope, stated: dict) -> tuple[bool, list[str], list[str]]:
    """Returns (all_match, missing, mismatched) for the envelope's declared conditions."""
    tol = dict(env.condition_tolerances)
    missing, mismatch = [], []
    for key, want in env.conditions:
        have = stated.get(key)
        if have is None:
            missing.append(key)
            continue
        if not isinstance(want, str):
            try:
                finite = math.isfinite(float(have))
            except (TypeError, ValueError):
                finite = False
            if not finite:
                mismatch.append(f"{key}={have!r} is not a finite value (never within tolerance)")
                continue
        if isinstance(want, tuple):
            if not (want[0] <= have <= want[1]):
                mismatch.append(f"{key}={have!r} outside {list(want)}")
        elif isinstance(want, str):
            if str(have).strip().lower() != want.strip().lower():
                mismatch.append(f"{key}={have!r} != {want!r}")
        else:
            if abs(float(have) - float(want)) > float(tol.get(key, 0.0)):
                mismatch.append(f"{key}={have!r} != {want!r} (tol {tol.get(key, 0.0)})")
    return (not missing and not mismatch), missing, mismatch


COLD_STARTS = ("cold", "ambient", "equilibrium_at_coolant", "coolant_equilibrium")

def approval(env: RatingEnvelope) -> tuple[bool, str]:
    """Is this envelope approved rating evidence?  Only an explicit typed APPROVED state with its evidence identity
    and intended use approves (review R2, D-R2-01); a text in the provenance never does."""
    prov = env.provenance
    if prov.origin in (DataOrigin.SYNTHETIC, DataOrigin.ESTIMATED):
        return False, f"{prov.origin.value} envelope: a model experiment, not an approved rating"
    a = env.approval
    if a is None or a.state is not ApprovalState.APPROVED:
        state = "not declared" if a is None else a.state.value
        return False, (f"approval state {state}: not approved rating evidence (a validation-status text such as "
                       f"{prov.validation_status!r} describes the data, it does not approve them)")
    missing = [n for n, v in (("evidence_id", a.evidence_id), ("evidence_revision", a.evidence_revision),
                              ("intended_use", a.intended_use)) if not v]
    if missing:
        return False, f"approved state without its evidence identity ({', '.join(missing)} blank): not approved"
    return True, (f"{prov.origin.value} envelope approved by {a.evidence_id} rev {a.evidence_revision} for "
                  f"'{a.intended_use}'")


def applicability(env: RatingEnvelope, duration_s: float, stated_conditions: dict) -> tuple[bool, str]:
    """Can this envelope answer a requirement of this duration (typed finite / continuous semantics)?"""
    req_inf = math.isinf(duration_s)
    env_inf = math.isinf(env.duration_s)
    if req_inf:
        return (env_inf, "continuous rating for a continuous requirement" if env_inf else
                f"a finite {env.duration_text} rating cannot establish a continuous requirement")
    if env_inf:
        start = str(stated_conditions.get("initial_state") or "").strip().lower()
        if start in COLD_STARTS:
            return True, (f"continuous rating covers {duration_s:g} s from a declared '{start}' start "
                          f"(not hotter than the rated equilibrium)")
        return False, ("a continuous rating covers a finite duration only from a declared start that is not hotter "
                       "than the rated equilibrium (state initial_state: cold / equilibrium_at_coolant)")
    tol = 1e-9 * max(1.0, duration_s)
    if abs(env.duration_s - duration_s) <= tol:
        return True, f"{env.duration_text} rating for a {duration_s:g} s requirement"
    if env.duration_s > duration_s:
        return True, (f"{env.duration_text} rating covers {duration_s:g} s (duration monotonicity from the same "
                      f"declared initial state)")
    return False, f"a {env.duration_text} rating is shorter than the required {duration_s:g} s"


def _evaluate_envelope(env: RatingEnvelope, speed_rpm: float, torque_Nm: float, stated: dict):
    ok, missing, mismatch = _match_conditions(env, stated)
    if not ok:
        return None, f"{env.envelope_id}: " + "; ".join([f"condition not stated: {m}" for m in missing] + mismatch)
    sp = np.array(env.speed_rpm)
    if not (sp[0] <= speed_rpm <= sp[-1]):
        return None, f"{env.envelope_id}: speed {speed_rpm:g} rpm outside the envelope axis (no extrapolation)"
    motoring = torque_Nm * speed_rpm >= 0 if speed_rpm != 0 else torque_Nm >= 0    # signed quadrant (P = T*n)
    table = env.max_motoring_torque_Nm if motoring else env.min_braking_torque_Nm
    if table is None:
        return None, f"{env.envelope_id}: no braking-side table"
    tb = np.array(table)
    j = int(np.clip(np.searchsorted(sp, speed_rpm, side="right") - 1, 0, sp.size - 2))
    lin = float(np.interp(speed_rpm, sp, tb))
    pair = (tb[j], tb[j + 1]) if speed_rpm not in sp else (lin, lin)
    mag = abs(torque_Nm)
    cons = min(abs(pair[0]), abs(pair[1]))
    opt = max(abs(pair[0]), abs(pair[1]))
    lim = abs(lin) if env.interpolation == "linear_declared" else cons
    approved, _why = approval(env)
    if not approved:
        kind = EvidenceKind.DIRECT_EVALUATION                    # a model experiment, not rating evidence
    elif env.provenance.origin is DataOrigin.SUPPLIER and env.evidence_kind == "supplier_rated":
        kind = EvidenceKind.SUPPLIER_RATED
    else:
        kind = EvidenceKind.VALIDATED_DOMAIN
    ev = Evidence.make(kind, f"{env.envelope_id} rev {env.revision} ({env.duration_text}): |T| limit {lim:.6g} N*m "
                             f"at {speed_rpm:g} rpm ({env.interpolation}, {'motoring' if motoring else 'braking'} "
                             f"quadrant)",
                       conservative_limit_Nm=cons, linear_limit_Nm=abs(lin), optimistic_limit_Nm=opt,
                       priority=env.priority, provenance=env.provenance.to_dict(),
                       approval=None if env.approval is None else env.approval.to_dict(),
                       interpolation_meaning=("the smaller bracketing table value: a bound between the speed points "
                                              "only if the declared envelope does not dip between them (staircase "
                                              "or monotone declaration by the supplier)"
                                              if env.interpolation == "conservative" else
                                              "linear between the declared points (the supplier declares linearity)"))
    if mag <= lim:
        return (Status.FEASIBLE, ev), None
    if mag > opt:
        return (Status.INFEASIBLE, ev), None
    return (Status.UNKNOWN, ev), None


def duration_claim(envelopes, duration_s: float | None, speed_rpm: float, torque_Nm: float,
                   stated_conditions: dict) -> Claim | None:
    """Duration claim for a shaft-torque request; None when no duration was requested.

    ``torque_Nm`` must be the torque of the *same witness* as the static claim it is combined with.
    """
    if duration_s is None:
        return None
    dtext = "continuous" if math.isinf(duration_s) else f"{duration_s:g} s"
    q = f"{torque_Nm:g} N*m at {speed_rpm:g} rpm sustained for {dtext}"
    scope = "external rating envelope lookup under matching conditions only"
    notes, results, experiments = [], [], []
    for env in envelopes:
        app, why = applicability(env, duration_s, stated_conditions)
        if not app:
            notes.append(f"{env.envelope_id}: {why}")
            continue
        res, note = _evaluate_envelope(env, speed_rpm, torque_Nm, stated_conditions)
        if res is None:
            notes.append(note)
            continue
        approved, awhy = approval(env)
        if not approved:
            experiments.append(f"{env.envelope_id}: {awhy}; as a model experiment it reads {res[0].value} "
                               f"({res[1].summary})")
            continue
        results.append((env, res[0], res[1], why))
    if not results:
        detail = (f"no validated rating envelope applies to a {dtext} requirement; the fixed-temperature electrical "
                  f"result is not renamed a {dtext} rating")
        reasons = (Reason.UNVALIDATED_DURATION,) + ((Reason.MISSING_INPUT,) if notes else ())
        info = notes + experiments
        return Claim("duration", Status.UNKNOWN, q, scope, None, time_horizon=dtext, reasons=reasons,
                     evidence=tuple(Evidence.make(EvidenceKind.DIRECT_EVALUATION, n) for n in info),
                     qualifiers=("model-experiment envelope(s) shown, not used as a rating",) if experiments else (),
                     detail=detail if not info else detail + " (" + "; ".join(info) + ")")
    top = max(e.priority for e, *_ in results)
    group = [r for r in results if r[0].priority == top]
    feas = [r for r in group if r[1] is Status.FEASIBLE]
    bad = [r for r in group if r[1] is Status.INFEASIBLE]
    evid = tuple(r[2] for r in group)
    ignored = [r[0].envelope_id for r in results if r[0].priority < top]
    quals = tuple(dict.fromkeys(r[3] for r in group)) + (
        (f"lower-priority envelope(s) not used: {', '.join(ignored)}",) if ignored else ())
    if feas and bad:
        return Claim("duration", Status.UNKNOWN, q, scope, None, time_horizon=dtext,
                     reasons=(Reason.CONFLICTING_EVIDENCE,), evidence=evid, qualifiers=quals,
                     detail="equally authoritative rating envelopes disagree (" +
                            ", ".join(f"{r[0].envelope_id}={r[1].value}" for r in group) +
                            "); declare the authoritative one (priority) - the order of the list does not decide")
    if feas:
        return Claim("duration", Status.FEASIBLE, q, scope, None, time_horizon=dtext, evidence=evid,
                     qualifiers=quals + tuple(f"envelope {r[0].envelope_id}" for r in feas),
                     detail="inside a validated rating envelope at matching conditions")
    if bad:
        return Claim("duration", Status.INFEASIBLE, q, scope, None, time_horizon=dtext,
                     reasons=(Reason.RATING_NOT_MET,), evidence=evid, qualifiers=quals,
                     detail="above the validated rating envelope at matching conditions: the request is not rated "
                            "(no evidence that it is sustained); this is not a proof of physical impossibility")
    return Claim("duration", Status.UNKNOWN, q, scope, None, time_horizon=dtext,
                 reasons=(Reason.BOUNDARY_WITHIN_TOLERANCE,), evidence=evid, qualifiers=quals,
                 detail="between the conservative and optimistic readings of the tabulated envelope")
