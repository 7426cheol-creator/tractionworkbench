"""Feasibility states, reasons, evidence and claims.

A *claim* always carries the quantity it is about, its scope (model, domain,
scenario), the policy, the time horizon and the evidence that supports it.
The three states follow the handoff (H6):

* FEASIBLE   - a solution was confirmed in the stated scope and every
               constraint needed for the claim was evaluated;
* INFEASIBLE - an applicable hard bound, necessary condition or reliable
               capability bound contradicts the requirement;
* UNKNOWN    - neither could be established (missing input, outside the
               model domain, unvalidated duration, overlapping uncertainty,
               unresolved numerics, ...).

Evidence kinds are recorded separately from the state; there is no 0-100
confidence score anywhere in the package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


class Status(str, Enum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"

    @property
    def verdict(self) -> str:
        return {"FEASIBLE": "PASS", "INFEASIBLE": "FAIL", "UNKNOWN": "UNKNOWN"}[self.value]


class Reason(str, Enum):
    """Why a claim is not a plain FEASIBLE (or why it is INFEASIBLE)."""

    MISSING_INPUT = "MISSING_INPUT"
    OUTSIDE_MODEL_DOMAIN = "OUTSIDE_MODEL_DOMAIN"
    OUTSIDE_ALLOWED_OPERATING_DOMAIN = "OUTSIDE_ALLOWED_OPERATING_DOMAIN"
    UNVALIDATED_DURATION = "UNVALIDATED_DURATION"
    UNCERTAINTY_OVERLAP = "UNCERTAINTY_OVERLAP"
    NUMERICAL_UNRESOLVED = "NUMERICAL_UNRESOLVED"
    INVALID_INPUT = "INVALID_INPUT"
    POLICY_LIMITATION = "POLICY_LIMITATION"
    SAMPLED_COVERAGE = "SAMPLED_COVERAGE"
    BOUNDARY_WITHIN_TOLERANCE = "BOUNDARY_WITHIN_TOLERANCE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    CONSTRAINT_VIOLATION = "CONSTRAINT_VIOLATION"
    NECESSARY_CONDITION_VIOLATED = "NECESSARY_CONDITION_VIOLATED"
    RATING_NOT_MET = "RATING_NOT_MET"                  # outside a validated rating: not rated, not "physically impossible"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"      # equally authoritative sources disagree
    BOUND_INCONCLUSIVE = "BOUND_INCONCLUSIVE"          # a conservative bound exceeds the limit; no violation witness
    REQUIREMENT_INCOMPLETE = "REQUIREMENT_INCOMPLETE"  # the requirement lacks a definition needed to decide
    COUPLED_MODEL_REQUIRED = "COUPLED_MODEL_REQUIRED"  # the answer depends on a source/load coupling not modelled


class EvidenceKind(str, Enum):
    DIRECT_EVALUATION = "direct_evaluation"
    ANALYTIC_BOUND = "analytic_necessary_condition"
    EXACT_ENUMERATION = "exact_boundary_enumeration"
    NUMERICAL_WITNESS = "numerical_witness"
    CERTIFIED_BOUND = "certified_bound"
    BOUNDED_SEARCH = "bounded_search"
    SAMPLED = "sampled_coverage"
    SUPPLIER_RATED = "supplier_rated_envelope"
    VALIDATED_DOMAIN = "empirically_validated_domain"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple((str(k), _freeze(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, tuple) and value and all(isinstance(x, tuple) and len(x) == 2 and isinstance(x[0], str) for x in value):
        return {k: _thaw(v) for k, v in value}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class Evidence:
    kind: EvidenceKind
    summary: str
    data: tuple = ()

    @classmethod
    def make(cls, kind: EvidenceKind, summary: str, **data: Any) -> "Evidence":
        return cls(kind, summary, _freeze(data))

    def to_dict(self) -> dict:
        out = {"kind": self.kind.value, "summary": self.summary}
        if self.data:
            out["data"] = _thaw(self.data)
        return out


@dataclass(frozen=True)
class Claim:
    """One statement about one quantity in one scope."""

    name: str
    status: Status
    quantity: str
    scope: str
    policy: str | None = None
    time_horizon: str = "static steady-state (no duration)"
    reasons: tuple[Reason, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    qualifiers: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "quantity": self.quantity,
            "scope": self.scope,
            "policy": self.policy,
            "time_horizon": self.time_horizon,
            "reasons": [r.value for r in self.reasons],
            "qualifiers": list(self.qualifiers),
            "evidence": [e.to_dict() for e in self.evidence],
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Aggregate:
    status: Status
    reasons: tuple[Reason, ...] = field(default_factory=tuple)
    deciding_claims: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "verdict": self.status.verdict,
            "reasons": [r.value for r in self.reasons],
            "deciding_claims": list(self.deciding_claims),
        }


def aggregate_and(claims: Iterable[Claim]) -> Aggregate:
    """AND-aggregation of required claims (H6).

    Any proven violation gives INFEASIBLE; otherwise any UNKNOWN gives
    UNKNOWN; only when every required claim is FEASIBLE is the result
    FEASIBLE.  An empty set of claims is UNKNOWN (nothing was shown).
    """
    claims = list(claims)
    if not claims:
        return Aggregate(Status.UNKNOWN, (Reason.MISSING_INPUT,), ())
    bad = [c for c in claims if c.status is Status.INFEASIBLE]
    if bad:
        reasons = _unique(r for c in bad for r in c.reasons)
        return Aggregate(Status.INFEASIBLE, reasons, tuple(c.name for c in bad))
    unknown = [c for c in claims if c.status is Status.UNKNOWN]
    if unknown:
        reasons = _unique(r for c in unknown for r in c.reasons)
        return Aggregate(Status.UNKNOWN, reasons, tuple(c.name for c in unknown))
    return Aggregate(Status.FEASIBLE, (), tuple(c.name for c in claims))


def _unique(items: Iterable[Reason]) -> tuple[Reason, ...]:
    seen: list[Reason] = []
    for r in items:
        if r not in seen:
            seen.append(r)
    return tuple(seen)
