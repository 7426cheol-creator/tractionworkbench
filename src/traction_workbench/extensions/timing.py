"""Fault-reaction timing chain vs FTTI (screening).

A chain is a sequence of events (fault occurrence -> sensing -> filtering ->
detection -> confirmation -> decision -> actuation -> safe state).  Each
budget item covers one interval between two events with min/nom/max latency
and an owner.  The analysis

* orders the events, checks that the chain is contiguous (gaps -> UNKNOWN),
* detects *duplicate budgets*: two items (e.g. a SW owner's
  FaultConfirmed->SafeReactionRequest and a system owner's FRTI that already
  contains that interval) covering the same part of the chain,
* adds periodic-task sampling delay (worst = period + execution time),
* computes best / nominal / worst reaction time on a non-overlapping cover and
  compares FDTI + FRTI with the FTTI.

The claim is only as good as the declared latencies: the tool checks the
arithmetic and the structure of the budget, not the latencies themselves.
The worst case assumes the declared maxima can occur together.
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


def analyze_timing(chain: TimingChain) -> dict:
    pos = {e: i for i, e in enumerate(chain.events)}
    n_seg = len(chain.events) - 1
    cover: dict[int, list[TimingItem]] = {k: [] for k in range(n_seg)}
    for it in chain.items:
        for k in range(pos[it.start_event], pos[it.end_event]):
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
                                   f"the interval is counted twice",
                    })
    gaps = [f"{chain.events[k]} -> {chain.events[k + 1]}" for k, its in cover.items() if not its]
    # non-overlapping cover: prefer the finest items (shortest spans); composite items that
    # overlap finer ones are excluded from the sum and reported as duplicates
    chosen: list[TimingItem] = []
    k = 0
    while k < n_seg:
        cands = [it for it in chain.items if pos[it.start_event] == k]
        if not cands:
            k += 1
            continue
        it = min(cands, key=lambda x: (pos[x.end_event] - pos[x.start_event], x.item_id))
        chosen.append(it)
        k = pos[it.end_event]
    missing_max = [it.item_id for it in chosen if it.worst_s is None]
    best = sum(it.best_s for it in chosen)
    worst = None if missing_max else sum(it.worst_s for it in chosen)
    noms = [it.nominal_s for it in chosen]
    nom = None if any(x is None for x in noms) else sum(noms)

    def split(to_event):
        if to_event is None or worst is None:
            return None, None
        cut = pos[to_event]
        fd = sum(it.worst_s for it in chosen if pos[it.end_event] <= cut)
        fr = sum(it.worst_s for it in chosen if pos[it.start_event] >= cut)
        return fd, fr

    fdti, frti = split(chain.detection_event)
    q = f"worst-case fault reaction within FTTI = {chain.ftti_s * 1e3:g} ms ({chain.fault})"
    scope = "declared latency budget (values not verified); maxima assumed able to co-occur"
    ev = [Evidence.make(EvidenceKind.ANALYTIC_BOUND, f"sum over {len(chosen)} non-overlapping items",
                        best_s=best, nominal_s=nom, worst_s=worst)]
    if gaps:
        claim = Claim("ftti", Status.UNKNOWN, q, scope, reasons=(Reason.MISSING_INPUT,), evidence=tuple(ev),
                      detail="the chain has unbudgeted intervals: " + "; ".join(gaps))
    elif worst is None:
        claim = Claim("ftti", Status.UNKNOWN, q, scope, reasons=(Reason.MISSING_INPUT,), evidence=tuple(ev),
                      detail="maximum latency missing for: " + ", ".join(missing_max))
    elif best > chain.ftti_s:
        claim = Claim("ftti", Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,), evidence=tuple(ev),
                      detail=f"even the best case {best * 1e3:.4g} ms exceeds the FTTI")
    elif worst > chain.ftti_s:
        claim = Claim("ftti", Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,), evidence=tuple(ev),
                      qualifiers=("worst case of the declared independent maxima",),
                      detail=f"worst case {worst * 1e3:.4g} ms exceeds the FTTI by {(worst - chain.ftti_s) * 1e3:.4g} ms")
    else:
        claim = Claim("ftti", Status.FEASIBLE, q, scope, evidence=tuple(ev),
                      qualifiers=("assumes the declared latencies are correct upper bounds",),
                      detail=f"worst case {worst * 1e3:.4g} ms leaves {(chain.ftti_s - worst) * 1e3:.4g} ms margin")
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
    return {
        "chain_id": chain.chain_id,
        "fault": chain.fault,
        "ftti_s": chain.ftti_s,
        "events": list(chain.events),
        "items": [{"id": it.item_id, "from": it.start_event, "to": it.end_event, "owner": it.owner,
                   "min_s": it.min_s, "nom_s": it.nom_s, "max_s": it.max_s, "period_s": it.period_s,
                   "worst_s": it.worst_s, "counted": it in chosen} for it in chain.items],
        "duplicate_budgets": duplicates,
        "gaps": gaps,
        "best_s": best, "nominal_s": nom, "worst_s": worst,
        "fdti_worst_s": fdti, "frti_worst_s": frti,
        "margin_s": None if worst is None else chain.ftti_s - worst,
        "budget_checks": budget_checks,
        "claim": claim.to_dict(),
        "notes": ["periodic items add one period of sampling delay to their worst case",
                  "composite items overlapping finer items are excluded from the sum and reported as duplicates"],
    }
