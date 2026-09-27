"""External peak/continuous rating envelopes (UC08).

A fixed-temperature electrical capability is never renamed "10-second peak"
or "continuous".  A duration claim comes only from a validated external
rating envelope whose duration and every declared condition (coolant,
initial state, Vdc, switching frequency, ...) match the requirement; the
envelope is only interpolated between its own speed points (never
extrapolated).  Without such an envelope the duration claim is UNKNOWN.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..errors import InputValidationError
from ..models.flux import _finite
from ..models.provenance import Provenance
from ..status import Claim, Evidence, EvidenceKind, Reason, Status


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

    def __post_init__(self):
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


def duration_claim(envelopes, duration_s: float | None, speed_rpm: float, torque_Nm: float,
                   stated_conditions: dict) -> Claim | None:
    """Duration claim for a shaft-torque request; None when no duration was requested."""
    if duration_s is None:
        return None
    dtext = "continuous" if math.isinf(duration_s) else f"{duration_s:g} s"
    q = f"{torque_Nm:g} N*m at {speed_rpm:g} rpm sustained for {dtext}"
    scope = "external rating envelope lookup under matching conditions only"
    same = [e for e in envelopes if (math.isinf(e.duration_s) and math.isinf(duration_s))
            or abs(e.duration_s - duration_s) <= 1e-9 * max(1.0, duration_s)]
    if not same:
        return Claim("duration", Status.UNKNOWN, q, scope, None, time_horizon=dtext,
                     reasons=(Reason.UNVALIDATED_DURATION,),
                     detail=f"no validated {dtext} rating envelope provided; the fixed-temperature electrical result "
                            f"is not renamed a {dtext} rating")
    notes = []
    for env in same:
        ok, missing, mismatch = _match_conditions(env, stated_conditions)
        if not ok:
            notes.append(f"{env.envelope_id}: " + "; ".join(
                ([f"condition not stated: {m}" for m in missing]) + mismatch))
            continue
        sp = np.array(env.speed_rpm)
        if not (sp[0] <= speed_rpm <= sp[-1]):
            notes.append(f"{env.envelope_id}: speed {speed_rpm:g} rpm outside the envelope axis (no extrapolation)")
            continue
        table = env.max_motoring_torque_Nm if torque_Nm >= 0 else env.min_braking_torque_Nm
        if table is None:
            notes.append(f"{env.envelope_id}: no braking-side table")
            continue
        tb = np.array(table)
        j = int(np.clip(np.searchsorted(sp, speed_rpm, side="right") - 1, 0, sp.size - 2))
        lin = float(np.interp(speed_rpm, sp, tb))
        pair = (tb[j], tb[j + 1]) if speed_rpm not in sp else (lin, lin)
        mag = abs(torque_Nm)
        cons = min(abs(pair[0]), abs(pair[1]))
        opt = max(abs(pair[0]), abs(pair[1]))
        lim = abs(lin) if env.interpolation == "linear_declared" else cons
        kind = EvidenceKind.SUPPLIER_RATED if env.evidence_kind == "supplier_rated" else EvidenceKind.VALIDATED_DOMAIN
        ev = Evidence.make(kind, f"{env.envelope_id} rev {env.revision} ({env.duration_text}): |T| limit {lim:.6g} N*m "
                                 f"at {speed_rpm:g} rpm ({env.interpolation})",
                           conservative_limit_Nm=cons, linear_limit_Nm=abs(lin), optimistic_limit_Nm=opt,
                           provenance=env.provenance.to_dict())
        if mag <= lim:
            return Claim("duration", Status.FEASIBLE, q, scope, None, time_horizon=dtext, evidence=(ev,),
                         qualifiers=(f"envelope {env.envelope_id}",),
                         detail="inside the validated rating envelope at matching conditions")
        if mag > opt:
            return Claim("duration", Status.INFEASIBLE, q, scope, None, time_horizon=dtext,
                         reasons=(Reason.CONSTRAINT_VIOLATION,), evidence=(ev,),
                         detail="above the validated rating envelope at matching conditions")
        return Claim("duration", Status.UNKNOWN, q, scope, None, time_horizon=dtext,
                     reasons=(Reason.BOUNDARY_WITHIN_TOLERANCE,), evidence=(ev,),
                     detail="between the conservative and optimistic readings of the tabulated envelope")
    return Claim("duration", Status.UNKNOWN, q, scope, None, time_horizon=dtext,
                 reasons=(Reason.UNVALIDATED_DURATION, Reason.MISSING_INPUT),
                 evidence=tuple(Evidence.make(EvidenceKind.DIRECT_EVALUATION, n) for n in notes),
                 detail="a rating envelope with this duration exists but its conditions are not shown to match")
