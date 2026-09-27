"""Fault-reaction timing chain vs FTTI (screening).

A chain is a sequence of events (fault occurrence -> sensing -> filtering ->
detection -> confirmation -> decision -> actuation -> safe state).  Each
budget item covers one interval between two events with min/nom/max latency
and an owner.  The analysis (revised after the independent review, F05):

* enumerates every *contiguous* path of budget items from the fault to the
  declared physical safe endpoint.  An item that spans several intervals
  (e.g. a system-level FRTI) is a valid bound for its whole span, but it
  cannot be split: when a finer item covers only part of it, the rest of the
  span is not budgeted by anything else and the composite must be used.
  Every path gives a sound upper bound on the actual reaction time, so the
  tightest guaranteed bound is the minimum over paths; the best-case lower
  bound is the maximum over paths of the summed minima;
* reports intervals that no item covers (gaps) and chains whose items never
  connect fault and safe state (UNKNOWN);
* detects *duplicate budgets* (two items covering the same interval);
* adds periodic-task sampling delay (worst = period + execution time);
* keeps the difference between a guaranteed bound and a failure witness: on
  one trace FHTI = FDTI + FRTI, but for independent worst cases only
  sup(D + R) <= sup D + sup R holds.  A summed upper bound above the FTTI
  means the declared bounds *cannot guarantee* the FTTI (UNKNOWN,
  BOUND_INCONCLUSIVE) - it is INFEASIBLE only when even the summed minima
  exceed the FTTI or when the chain declares that the maxima occur together
  in one trace (``worst_case_attainable``);
* requires the end event to be the *physical* safe state: a gate command
  reaching the driver is not the current being extinguished.

The claim is only as good as the declared latencies: the tool checks the
arithmetic and the structure of the budget, not the latencies themselves.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..errors import InputValidationError
from ..models.flux import _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status


@dataclass(frozen=True)
class TimingItem:
    item_id: str
    start_event: str
    end_event: str
    owner: str
    max_s: float | None
    min_s: float | None = None
    nom_s: float | None = None
    period_s: float | None = None        # periodic task: detection waits up to one period
    source: str = ""

    def __post_init__(self):
        for name in ("max_s", "min_s", "nom_s", "period_s"):
            v = getattr(self, name)
            if v is not None:
                v = _finite(f"{self.item_id}.{name}", v)
                if v < 0:
                    raise InputValidationError("latencies must be >= 0", field=f"{self.item_id}.{name}")
                object.__setattr__(self, name, v)
        if self.min_s is not None and self.max_s is not None and self.min_s > self.max_s:
            raise InputValidationError("min latency exceeds max latency", field=self.item_id)
        if self.start_event == self.end_event:
            raise InputValidationError("an item must connect two different events", field=self.item_id)

    @property
    def worst_s(self) -> float | None:
        if self.max_s is None:
            return None
        return self.max_s + (self.period_s or 0.0)

    @property
    def best_s(self) -> float:
        return self.min_s if self.min_s is not None else 0.0

    @property
    def nominal_s(self) -> float | None:
        if self.nom_s is not None:
            return self.nom_s + 0.5 * (self.period_s or 0.0)
        return None


@dataclass(frozen=True)
class TimingChain:
    chain_id: str
    fault: str
    ftti_s: float
    events: tuple[str, ...]              # ordered: first = fault occurrence, last = safe state reached
    items: tuple[TimingItem, ...]
    detection_event: str | None = None   # end of FDTI (e.g. "fault_confirmed")
    fdti_budget_s: float | None = None
    frti_budget_s: float | None = None
    safe_event: str | None = None        # event where the physical safe state is reached (default: last event)
    endpoint_kind: str = "physical_safe_state"   # or "command_issued": a command is not a physical endpoint
    worst_case_attainable: bool = False  # declared: the item maxima can occur together in one trace

    def __post_init__(self):
        if len(self.events) < 2 or len(set(self.events)) != len(self.events):
            raise InputValidationError("events must be >= 2 distinct names in chain order", field="events")
        f = _finite("ftti_s", self.ftti_s)
        if f <= 0:
            raise InputValidationError("FTTI must be > 0", field="ftti_s")
        object.__setattr__(self, "ftti_s", f)
        pos = {e: i for i, e in enumerate(self.events)}
        for it in self.items:
            for ev in (it.start_event, it.end_event):
                if ev not in pos:
                    raise InputValidationError(f"event {ev!r} not in the chain", field=it.item_id)
            if pos[it.end_event] <= pos[it.start_event]:
                raise InputValidationError("item end event must come after its start event", field=it.item_id)
        if self.detection_event is not None and self.detection_event not in pos:
            raise InputValidationError("detection event not in the chain", field="detection_event")
        if self.safe_event is not None and self.safe_event not in pos:
            raise InputValidationError("safe event not in the chain", field="safe_event")
        if self.endpoint_kind not in ("physical_safe_state", "command_issued"):
            raise InputValidationError("endpoint_kind must be 'physical_safe_state' or 'command_issued'",
                                       field="endpoint_kind")


MAX_PATHS = 20000


def _paths(chain: TimingChain, pos: dict, target: int) -> tuple[list[list[TimingItem]], bool]:
    """Every contiguous chain of items from the first event to the target event (DAG enumeration)."""
    adj: dict[int, list[TimingItem]] = {}
    for it in chain.items:
        a, b = pos[it.start_event], pos[it.end_event]
        if b <= target:
            adj.setdefault(a, []).append(it)
    for a in adj:
        adj[a].sort(key=lambda x: (pos[x.end_event], x.item_id))
    out: list[list[TimingItem]] = []
    truncated = False

    def dfs(i, acc):
        nonlocal truncated
        if len(out) >= MAX_PATHS:
            truncated = True
            return
        if i == target:
            out.append(list(acc))
            return
        for it in adj.get(i, ()):
            acc.append(it)
            dfs(pos[it.end_event], acc)
            acc.pop()

    dfs(0, [])
    return out, truncated


def analyze_timing(chain: TimingChain) -> dict:
    pos = {e: i for i, e in enumerate(chain.events)}
    safe_event = chain.safe_event or chain.events[-1]
    target = pos[safe_event]
    n_seg = target
    cover: dict[int, list[TimingItem]] = {k: [] for k in range(n_seg)}
    for it in chain.items:
        for k in range(pos[it.start_event], min(pos[it.end_event], target)):
            cover[k].append(it)
    # duplicate budgets: any elementary segment covered by more than one item
    duplicates = []
    seen_pairs = set()
    for k, its in cover.items():
        if len(its) > 1:
            for a in range(len(its)):
                for b in range(a + 1, len(its)):
                    key = tuple(sorted((its[a].item_id, its[b].item_id)))
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    lo = max(pos[its[a].start_event], pos[its[b].start_event])
                    hi = min(pos[its[a].end_event], pos[its[b].end_event])
                    duplicates.append({
                        "items": list(key),
                        "owners": sorted({its[a].owner, its[b].owner}),
                        "overlap": f"{chain.events[lo]} -> {chain.events[hi]}",
                        "message": f"{key[0]} and {key[1]} both budget {chain.events[lo]} -> {chain.events[hi]}: "
                                   f"the interval is counted twice if both are summed",
                    })
    gaps = [f"{chain.events[k]} -> {chain.events[k + 1]}" for k, its in cover.items() if not its]
    paths, truncated = _paths(chain, pos, target)
    complete = [p for p in paths if all(it.worst_s is not None for it in p)]
    ub = None
    chosen: list[TimingItem] = []
    if complete:
        chosen = min(complete, key=lambda p: (sum(it.worst_s for it in p), -len(p), [it.item_id for it in p]))
        ub = sum(it.worst_s for it in chosen)
    lb = max((sum(it.best_s for it in p) for p in paths), default=0.0)
    if not chosen and paths:
        chosen = max(paths, key=len)
    missing_max = sorted({it.item_id for p in paths for it in p if it.worst_s is None}) if not complete else []
    noms = [it.nominal_s for it in chosen]
    nom = None if (not chosen or any(x is None for x in noms)) else sum(noms)

    # FDTI / FRTI split along the chosen path (unknown when a composite item spans the detection event)
    fdti = frti = None
    split_note = None
    if chain.detection_event is not None and ub is not None:
        cut = pos[chain.detection_event]
        spans = [it for it in chosen if pos[it.start_event] < cut < pos[it.end_event]]
        if spans:
            split_note = (f"{spans[0].item_id} spans the detection event {chain.detection_event!r}: the FDTI/FRTI "
                          f"split of the chosen path is unknown (a composite budget cannot be split)")
        else:
            fdti = sum(it.worst_s for it in chosen if pos[it.end_event] <= cut)
            frti = sum(it.worst_s for it in chosen if pos[it.start_event] >= cut)

    q = f"worst-case fault reaction within FTTI = {chain.ftti_s * 1e3:g} ms ({chain.fault})"
    scope = ("declared latency budget (values not verified); every contiguous item path fault -> "
             f"{safe_event} considered")
    ev = [Evidence.make(EvidenceKind.ANALYTIC_BOUND,
                        f"{len(paths)} contiguous path(s); tightest guaranteed bound on the chosen path of "
                        f"{len(chosen)} items", best_s=lb, nominal_s=nom, worst_s=ub,
                        chosen_path=[it.item_id for it in chosen])]
    if chain.endpoint_kind != "physical_safe_state":
        claim = Claim("ftti", Status.UNKNOWN, q, scope, reasons=(Reason.MISSING_INPUT,), evidence=tuple(ev),
                      detail=f"the chain ends at {safe_event!r}, declared as a command, not the physical safe state: "
                             "add the actuation -> current/torque decay -> safe-state interval")
    elif not paths:
        why = ("the chain has unbudgeted intervals: " + "; ".join(gaps)) if gaps else \
              "no contiguous chain of budget items connects the fault to the safe state (items overlap but never join)"
        claim = Claim("ftti", Status.UNKNOWN, q, scope, reasons=(Reason.MISSING_INPUT,), evidence=tuple(ev),
                      detail=why)
    elif ub is None:
        claim = Claim("ftti", Status.UNKNOWN, q, scope, reasons=(Reason.MISSING_INPUT,), evidence=tuple(ev),
                      detail="maximum latency missing on every contiguous path for: " + ", ".join(missing_max))
    elif lb > chain.ftti_s:
        claim = Claim("ftti", Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,), evidence=tuple(ev),
                      detail=f"even the summed minima {lb * 1e3:.4g} ms exceed the FTTI: every trace is too slow")
    elif ub <= chain.ftti_s:
        claim = Claim("ftti", Status.FEASIBLE, q, scope, evidence=tuple(ev),
                      qualifiers=("assumes the declared latencies are correct upper bounds",),
                      detail=f"guaranteed bound {ub * 1e3:.4g} ms leaves {(chain.ftti_s - ub) * 1e3:.4g} ms margin")
    elif chain.worst_case_attainable:
        claim = Claim("ftti", Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,), evidence=tuple(ev),
                      qualifiers=("the chain declares that the item maxima occur together in one trace",),
                      detail=f"attainable worst case {ub * 1e3:.4g} ms exceeds the FTTI by "
                             f"{(ub - chain.ftti_s) * 1e3:.4g} ms")
    else:
        claim = Claim("ftti", Status.UNKNOWN, q, scope, reasons=(Reason.BOUND_INCONCLUSIVE,), evidence=tuple(ev),
                      qualifiers=("sum of independent maxima: an upper bound, not a failure witness",),
                      detail=f"the summed maxima {ub * 1e3:.4g} ms exceed the FTTI by {(ub - chain.ftti_s) * 1e3:.4g} ms: "
                             f"the declared bounds cannot guarantee the FTTI (not proven to fail - refine with a joint "
                             f"worst-case trace, or declare that the maxima are jointly attainable)")
    budget_checks = []
    if fdti is not None and chain.fdti_budget_s is not None:
        budget_checks.append({"budget": "FDTI", "allocated_s": chain.fdti_budget_s, "worst_s": fdti,
                              "ok": fdti <= chain.fdti_budget_s})
    if frti is not None and chain.frti_budget_s is not None:
        budget_checks.append({"budget": "FRTI", "allocated_s": chain.frti_budget_s, "worst_s": frti,
                              "ok": frti <= chain.frti_budget_s})
    if chain.fdti_budget_s is not None and chain.frti_budget_s is not None:
        budget_checks.append({"budget": "FDTI + FRTI < FTTI", "allocated_s": chain.fdti_budget_s + chain.frti_budget_s,
                              "ftti_s": chain.ftti_s, "ok": chain.fdti_budget_s + chain.frti_budget_s < chain.ftti_s})
    notes = ["periodic items add one period of sampling delay to their worst case",
             "every contiguous item path is a sound bound; the tightest one is reported; items off the chosen path "
             "are not summed",
             "FHTI = FDTI + FRTI holds on one trace; for independent worst cases only sup(D+R) <= sup D + sup R"]
    if split_note:
        notes.append(split_note)
    if truncated:
        notes.append(f"path enumeration truncated at {MAX_PATHS} paths")
    return {
        "chain_id": chain.chain_id,
        "fault": chain.fault,
        "ftti_s": chain.ftti_s,
        "events": list(chain.events),
        "safe_event": safe_event,
        "endpoint_kind": chain.endpoint_kind,
        "items": [{"id": it.item_id, "from": it.start_event, "to": it.end_event, "owner": it.owner,
                   "min_s": it.min_s, "nom_s": it.nom_s, "max_s": it.max_s, "period_s": it.period_s,
                   "worst_s": it.worst_s, "counted": it in chosen} for it in chain.items],
        "duplicate_budgets": duplicates,
        "gaps": gaps,
        "paths": len(paths),
        "chosen_path": [it.item_id for it in chosen],
        "best_s": lb, "nominal_s": nom, "worst_s": ub,
        "fdti_worst_s": fdti, "frti_worst_s": frti,
        "margin_s": None if ub is None else chain.ftti_s - ub,
        "budget_checks": budget_checks,
        "claim": claim.to_dict(),
        "notes": notes,
    }
