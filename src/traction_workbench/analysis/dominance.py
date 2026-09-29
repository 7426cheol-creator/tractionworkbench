"""Constraint dominance by relaxation (Blueprint 7.5, Handoff H7).

"Active at the current solution", "limiting the capability" and "worth
changing" are different things.  Dominance is evaluated by relaxing one
constraint slightly, re-solving the *whole coupled problem* and measuring the
change of the capability (or of the requirement status).  If only a joint
relaxation helps, the constraints form a joint bottleneck.  Relaxations here
are diagnostic; a realisable change (e.g. Vdc) moves several limits together
and is evaluated with ``sizing``.

Independent review F13: a gain is an interval (both capabilities carry a
resolution), and a relaxation whose capability could not be established is
"unresolved" - never "not limiting".

Second review R2 (D-R2-05): a limit of ZERO (e.g. no charge acceptance) is a
real, often decisive constraint - a relative change of it is undefined, so it
is relaxed by an explicit absolute step in its native unit (power W, current
A = the same W at Vdc) and reported as such; it is never dropped from the
diagnosis.  Tied power / current caps of one side are always also relaxed
together.  A declared-unlimited limit has nothing to relax.  The gain interval
is the sampled resolution, not an enclosure of the global capability change.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .. import progress
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


ABS_POWER_STEP_W = 1000.0          # absolute relaxation of a zero DC limit (currents: the same W at Vdc)


def _abs_step(name: str, scenario, drive, scale: float = 1.0) -> float:
    if name.startswith("DC_") and name.endswith("_POWER"):
        return ABS_POWER_STEP_W * scale
    if name.startswith("DC_") and name.endswith("_CURRENT"):
        return ABS_POWER_STEP_W * scale / scenario.Vdc_V
    if name == "ID_MIN":
        return -0.01 * drive.inverter.current_limit_A_peak * scale
    return 0.0


def _relaxed(drive, scenario, names, rel, abs_scale: float | None = None):
    """Relax each named limit by the relative ``rel`` - or, for a zero limit, by its absolute step (x ``abs_scale``,
    default rel / 1 %)."""
    d, s = drive, scenario
    for n in names:
        p, _ = RELAXABLE[n]
        base = get_value(d, s, p)
        if base == 0:
            d, s = apply(d, s, p, _abs_step(n, scenario, drive, rel / 0.01 if abs_scale is None else abs_scale))
        else:
            d, s = apply(d, s, p, base * (1.0 + rel))
    return d, s


def _perturbation(name, drive, scenario, rel):
    p, _ = RELAXABLE[name]
    base = get_value(drive, scenario, p)
    if base == 0:
        return (f"absolute {_abs_step(name, scenario, drive, rel / 0.01):+.6g} (zero limit: a relative change is "
                f"undefined)")
    return f"relative {rel:+.2%}"


def _available(drive, scenario):
    """Declared, finite limits (zero included); undeclared ones cannot be relaxed, unlimited ones need none."""
    import math
    out = []
    for n, (p, _) in RELAXABLE.items():
        v = get_value(drive, scenario, p)
        if v is None or not math.isfinite(v):
            continue
        out.append(n)
    return out


TIED = (("DC_DISCHARGE_POWER", "DC_DISCHARGE_CURRENT"), ("DC_CHARGE_POWER", "DC_CHARGE_CURRENT"))


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
    names = list(_available(drive, scenario))
    tied = [pr for pr in TIED if set(pr) <= set(names)]
    # one policy capability per step: the base, each limit relaxed alone, then the pairs (known after the singles)
    with progress.span(1 + len(names) + len(tied), "dominance") as steps:
        steps.step()
        base = policy_capability(PolicyEvaluator(drive, scenario, settings), direction, samples=samples, certify=False)
        ev0 = PolicyEvaluator(drive, scenario, settings)
        tscale = ev0.k.torque_scale()
        # resolution of one sampled capability boundary: bisection tolerance plus a numerical floor
        res = max(settings.capability_bisection_rel_tol * tscale, 1e-7 * tscale, 1e-9)
        eps = 2.0 * res
        base_v = base.value_Nm if base.accepted else None
        active = base.active_constraints
        rows = []
        gains = {}
        for name in names:
            steps.step()
            d, s = _relaxed(drive, scenario, [name], relaxation)
            cap = policy_capability(PolicyEvaluator(d, s, settings), direction, samples=samples, certify=False)
            v = cap.value_Nm if cap.accepted else None
            gain = None if (v is None or base_v is None) else direction * (v - base_v)
            gains[name] = gain
            p, _ = RELAXABLE[name]
            lim = get_value(drive, scenario, p)
            new_lim = get_value(d, s, p)
            if gain is None:
                cls = "unresolved (capability not established)"
            elif gain - eps > 0:
                cls = "limiting"
            elif gain + eps < 0:
                cls = "relaxation lowered the capability (non-monotonic response)"
            else:
                cls = "not limiting alone (gain within resolution)"
            rows.append((("constraint", name), ("parameter", p), ("limit", lim), ("relaxed_limit", new_lim),
                         ("perturbation", _perturbation(name, drive, scenario, relaxation)),
                         ("capability_Nm", v), ("gain_Nm", gain),
                         ("gain_interval_Nm", None if gain is None else [gain - eps, gain + eps]),
                         ("gain_interval_meaning", "sampled resolution (two bisected boundaries), not an enclosure of the "
                                                   "global capability change"),
                         ("sensitivity_Nm_per_unit", None if gain is None or new_lim == lim else gain / abs(new_lim - lim)),
                         ("active_at_base", name in active),
                         ("classification", cls)))
        joint = []
        avail = set(gains)
        weak = [n for n in gains if gains[n] is not None and gains[n] <= eps]
        cand_pairs = [pr for pr in combinations(weak, 2) if pr[0] in active or pr[1] in active]
        # tied caps of one side are always relaxed together (either can bind alone; together they are one constraint)
        cand_pairs += [pr for pr in TIED if set(pr) <= avail and pr not in cand_pairs]
        steps.remaining(len(cand_pairs))
        for a, b in cand_pairs:
            steps.step()
            d, s = _relaxed(drive, scenario, [a, b], relaxation)
            cap = policy_capability(PolicyEvaluator(d, s, settings), direction, samples=samples, certify=False)
            v = cap.value_Nm if cap.accepted else None
            gain = None if (v is None or base_v is None) else direction * (v - base_v)
            pert = [_perturbation(n, drive, scenario, relaxation) for n in (a, b)]
            if gain is None:
                joint.append((("constraints", [a, b]), ("perturbation", pert), ("gain_Nm", None),
                              ("classification", "unresolved")))
            elif gain - eps > 0 and not any(g is not None and g - eps > 0 for g in (gains.get(a), gains.get(b))):
                # only a pair helps: a joint bottleneck (a pair containing a limit that is limiting alone adds nothing)
                joint.append((("constraints", [a, b]), ("perturbation", pert), ("gain_Nm", gain),
                              ("gain_interval_Nm", [gain - eps, gain + eps]), ("classification", "joint bottleneck")))
        notes = ["relaxations are diagnostic (cause analysis), not realisable hardware changes",
                 f"gain resolution +-{eps:.2e} N*m (two sampled capability boundaries); a gain inside it is not a "
                 f"'limiting' finding, and a relaxation without an established capability is 'unresolved'",
                 "capabilities are sampled scans (certify=False): the classification is sampled evidence"]
        if base_v is None:
            notes.append("base capability not established: every classification is unresolved")
        return DominanceResult(direction, base_v, tuple(active), tuple(rows), tuple(joint), relaxation, tuple(notes))


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

    unresolved: dict[tuple, bool] = {}

    def first_ok(names):
        prev = 0.0
        seen_unknown = False
        with progress.span(len(fracs) + 40, "relaxation search") as search:     # the steps, then 40 bisections
            for f in fracs:
                search.step()
                d, s = _relaxed(drive, scenario, names, f)
                st = status(d, s)
                if st == "FEASIBLE":
                    search.remaining(40)
                    lo, hi = prev, f
                    for _ in range(40):
                        search.step()
                        m = 0.5 * (lo + hi)
                        d, s = _relaxed(drive, scenario, names, m)
                        sm = status(d, s)
                        seen_unknown |= sm == "UNKNOWN"
                        if sm == "FEASIBLE":
                            hi = m
                        else:
                            lo = m
                    unresolved[tuple(names)] = seen_unknown
                    return hi
                seen_unknown |= st == "UNKNOWN"
                prev = f
        unresolved[tuple(names)] = seen_unknown
        return None

    singles_ok = False
    names = list(_available(drive, scenario))
    with progress.span(len(names), "relaxation") as tried:      # each limit; then each pair, if none suffices alone
        for name in names:
            tried.step()
            f = first_ok([name])
            p, _ = RELAXABLE[name]
            lim = get_value(drive, scenario, p)
            rows.append((("constraint", name), ("parameter", p), ("limit", lim),
                         ("sufficient_alone", f is not None),
                         ("minimal_relative_relaxation", f),
                         ("minimal_meaning", "smallest sufficient relaxation found; UNKNOWN statuses were met below it "
                                             "- not a proven minimum" if unresolved.get((name,)) else
                                             "bracketed between an insufficient and a sufficient relaxation (sampled)"),
                         ("relaxed_limit", None if f is None else get_value(*_relaxed(drive, scenario, [name], f), p)),
                         ("perturbation", None if f is None else _perturbation(name, drive, scenario, f))))
            singles_ok |= f is not None
        if not singles_ok:
            pairs = list(combinations(names, 2))
            tried.remaining(len(pairs))
            for a, b in pairs:
                tried.step()
                f = first_ok([a, b])
                if f is not None:
                    joint.append((("constraints", [a, b]), ("minimal_relative_relaxation_each", f)))
    notes = [f"searched relaxations up to +{max_relaxation:.0%} ({steps} steps + bisection); diagnostic only",
             "a realisable change (e.g. Vdc) moves several limits at once: use one-parameter sizing for it"]
    if not singles_ok and joint:
        notes.append("no single relaxation suffices: the listed pairs are joint bottlenecks")
    return RelaxationResult(T_request, base, tuple(rows), tuple(joint), tuple(notes))
