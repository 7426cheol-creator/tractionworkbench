"""Constraint dominance by relaxation (Blueprint 7.5, Handoff H7).

"Active at the current solution", "limiting the capability" and "worth
changing" are different things.  Dominance is evaluated by relaxing one
constraint slightly, re-solving the *whole coupled problem* and measuring the
change of the capability (or of the requirement status).  If only a joint
relaxation helps, the constraints form a joint bottleneck.  Relaxations here
are diagnostic; a realisable change (e.g. Vdc) moves several limits together
and is evaluated with ``sizing``.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ..models.components import DriveModel
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.capability import policy_capability
from ..solvers.policy import PolicyEvaluator
from .variation import apply, get_value

RELAXABLE = {
    "VOLTAGE": ("voltage_budget_scale", 1),
    "CURRENT": ("I_peak_max_A", 1),
    "DC_DISCHARGE_POWER": ("discharge_power_max_W", 1),
    "DC_DISCHARGE_CURRENT": ("discharge_current_max_A", 1),
    "DC_CHARGE_POWER": ("charge_power_max_W", 1),
    "DC_CHARGE_CURRENT": ("charge_current_max_A", 1),
    "ID_MIN": ("id_min_A", 1),
}


def _relaxed(drive, scenario, names, rel):
    d, s = drive, scenario
    for n in names:
        p, _ = RELAXABLE[n]
        base = get_value(d, s, p)
        d, s = apply(d, s, p, base * (1.0 + rel))
    return d, s


def _available(drive, scenario):
    out = []
    for n, (p, _) in RELAXABLE.items():
        v = get_value(drive, scenario, p)
        if v is None or v == 0:
            continue
        out.append(n)
    return out


@dataclass(frozen=True)
class DominanceResult:
    direction: int
    base_capability_Nm: float | None
    active_at_base: tuple
    rows: tuple
    joint: tuple
    relaxation: float
    notes: tuple

    def to_dict(self) -> dict:
        return {
            "direction": "maximum" if self.direction > 0 else "minimum",
            "base_policy_capability_Nm": self.base_capability_Nm,
            "active_constraints_at_base_witness": list(self.active_at_base),
            "relaxation": f"+{self.relaxation:.2%} of each native limit, one at a time",
            "recomputation": "full minimum-current policy capability (coupled voltage/current/DC problem)",
            "single": [dict(r) for r in self.rows],
            "joint": [dict(r) for r in self.joint],
            "notes": list(self.notes),
        }


def capability_dominance(drive: DriveModel, scenario: Scenario, direction: int = 1, relaxation: float = 0.01,
                         samples: int = 61, settings: NumericalSettings = DEFAULT_SETTINGS) -> DominanceResult:
    base = policy_capability(PolicyEvaluator(drive, scenario, settings), direction, samples=samples, certify=False)
    ev0 = PolicyEvaluator(drive, scenario, settings)
    tscale = ev0.k.torque_scale()
    eps = max(1e-6 * tscale, 1e-7)
    active = base.active_constraints
    rows = []
    gains = {}
    for name in _available(drive, scenario):
        d, s = _relaxed(drive, scenario, [name], relaxation)
        cap = policy_capability(PolicyEvaluator(d, s, settings), direction, samples=samples, certify=False)
        gain = None if (cap.value_Nm is None or base.value_Nm is None) else direction * (cap.value_Nm - base.value_Nm)
        gains[name] = gain
        p, _ = RELAXABLE[name]
        lim = get_value(drive, scenario, p)
        rows.append((("constraint", name), ("parameter", p), ("limit", lim), ("relaxed_limit", lim * (1 + relaxation)),
                     ("capability_Nm", cap.value_Nm), ("gain_Nm", gain),
                     ("sensitivity_Nm_per_unit", None if gain is None else gain / abs(lim * relaxation)),
                     ("active_at_base", name in active),
                     ("classification", "limiting" if gain is not None and gain > eps else "not limiting alone")))
    joint = []
    weak = [n for n in gains if gains[n] is not None and gains[n] <= eps]
    cand_pairs = [pr for pr in combinations(weak, 2)
                  if pr[0] in active or pr[1] in active or set(pr) == {"DC_DISCHARGE_POWER", "DC_DISCHARGE_CURRENT"}
                  or set(pr) == {"DC_CHARGE_POWER", "DC_CHARGE_CURRENT"}]
    for a, b in cand_pairs:
        d, s = _relaxed(drive, scenario, [a, b], relaxation)
        cap = policy_capability(PolicyEvaluator(d, s, settings), direction, samples=samples, certify=False)
        gain = None if (cap.value_Nm is None or base.value_Nm is None) else direction * (cap.value_Nm - base.value_Nm)
        if gain is not None and gain > eps:
            joint.append((("constraints", [a, b]), ("gain_Nm", gain), ("classification", "joint bottleneck")))
    notes = ["relaxations are diagnostic (cause analysis), not realisable hardware changes",
             f"gain threshold {eps:.2e} N*m (numerical resolution of the capability boundary)"]
    return DominanceResult(direction, base.value_Nm, tuple(active), tuple(rows), tuple(joint), relaxation, tuple(notes))


@dataclass(frozen=True)
class RelaxationResult:
    T_request_Nm: float
    base_status: str
    rows: tuple
    joint: tuple
    notes: tuple

    def to_dict(self) -> dict:
        return {"T_request_Nm": self.T_request_Nm, "base_policy_status": self.base_status,
                "single": [dict(r) for r in self.rows], "joint": [dict(r) for r in self.joint],
                "notes": list(self.notes)}


def requirement_relaxation(drive: DriveModel, scenario: Scenario, T_request: float, max_relaxation: float = 0.5,
                           steps: int = 26, settings: NumericalSettings = DEFAULT_SETTINGS) -> RelaxationResult:
    """Smallest single (then pairwise) relaxation that makes the policy FEASIBLE at T_request."""
    def status(d, s):
        return PolicyEvaluator(d, s, settings).solve(T_request).policy_claim.status.value

    base = status(drive, scenario)
    rows, joint = [], []
    if base == "FEASIBLE":
        return RelaxationResult(T_request, base, (), (), ("already FEASIBLE: no relaxation needed",))
    fracs = [max_relaxation * i / (steps - 1) for i in range(1, steps)]

    def first_ok(names):
        prev = 0.0
        for f in fracs:
            d, s = _relaxed(drive, scenario, names, f)
            if status(d, s) == "FEASIBLE":
                lo, hi = prev, f
                for _ in range(40):
                    m = 0.5 * (lo + hi)
                    d, s = _relaxed(drive, scenario, names, m)
                    if status(d, s) == "FEASIBLE":
                        hi = m
                    else:
                        lo = m
                return hi
            prev = f
        return None

    singles_ok = False
    for name in _available(drive, scenario):
        f = first_ok([name])
        p, _ = RELAXABLE[name]
        lim = get_value(drive, scenario, p)
        rows.append((("constraint", name), ("parameter", p), ("limit", lim),
                     ("sufficient_alone", f is not None),
                     ("minimal_relative_relaxation", f),
                     ("relaxed_limit", None if f is None else lim * (1 + f))))
        singles_ok |= f is not None
    if not singles_ok:
        names = _available(drive, scenario)
        for a, b in combinations(names, 2):
            f = first_ok([a, b])
            if f is not None:
                joint.append((("constraints", [a, b]), ("minimal_relative_relaxation_each", f)))
    notes = [f"searched relaxations up to +{max_relaxation:.0%} ({steps} steps + bisection); diagnostic only",
             "a realisable change (e.g. Vdc) moves several limits at once: use one-parameter sizing for it"]
    if not singles_ok and joint:
        notes.append("no single relaxation suffices: the listed pairs are joint bottlenecks")
    return RelaxationResult(T_request, base, tuple(rows), tuple(joint), tuple(notes))
