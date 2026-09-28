"""Bounded one-parameter inverse sizing (Handoff H5.5).

Changes exactly one declared parameter inside an explicit search range,
recomputes the coupled problem (constraints and losses) at every sample and
bisects the status transitions.  Nothing is extrapolated beyond the range;
a non-monotonic response is reported as several feasible ranges, never as a
single "minimum" found by bisection alone.

Independent review F13: the search range is split into FEASIBLE (witnessed),
INFEASIBLE (proven excluded at the samples) and UNKNOWN (unresolved) regions.
Only a FEASIBLE <-> INFEASIBLE transition is bisected into a bracketed
boundary; the smallest feasible value is called a bracketed minimum only when
the region below it is proven excluded - next to an UNKNOWN region it is the
"smallest feasible value found", never a minimal sizing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..errors import InputValidationError
from ..models.components import DriveModel
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.policy import PolicyEvaluator
from ..status import Evidence, EvidenceKind
from .variation import PARAMETERS, apply, describe_parameter, get_value


@dataclass(frozen=True)
class SizingResult:
    parameter: dict
    baseline_value: float
    search_range: tuple[float, float]
    samples: int
    feasible_ranges: tuple
    status_samples: tuple
    minimal_feasible: float | None
    maximal_feasible: float | None
    solution_at_minimal: dict | None
    evidence: tuple
    notes: tuple
    regions: tuple = ()
    minimal_is_bracketed: bool = False
    maximal_is_bracketed: bool = False

    def to_dict(self) -> dict:
        return {
            "parameter": self.parameter,
            "baseline_value": self.baseline_value,
            "search_range": list(self.search_range),
            "samples": self.samples,
            "feasible_ranges": [list(r) for r in self.feasible_ranges],
            "minimal_feasible_value": self.minimal_feasible,
            "minimal_is_bracketed": self.minimal_is_bracketed,
            "minimal_label": "local bracket" if self.minimal_is_bracketed else "smallest witnessed",
            "minimal_meaning": ("local bracket: the edge found by bisection between a witnessed and an excluded "
                                "sample; excluded at the samples below (sampled, not a continuous proof of a "
                                "global minimum)" if self.minimal_is_bracketed else
                                "smallest feasible value FOUND - not a proven minimum (unresolved or unexplored below)"),
            "maximal_feasible_value": self.maximal_feasible,
            "maximal_is_bracketed": self.maximal_is_bracketed,
            "maximal_label": "local bracket" if self.maximal_is_bracketed else "largest witnessed",
            "maximal_meaning": ("local bracket: excluded at the samples above (sampled)" if self.maximal_is_bracketed
                                else "largest feasible value FOUND - not a proven maximum"),
            "regions": [{"range": [a, b], "status": st} for a, b, st in self.regions],
            "solution_at_minimal_value": self.solution_at_minimal,
            "status_samples": [list(s) for s in self.status_samples],
            "evidence": [e.to_dict() for e in self.evidence],
            "notes": list(self.notes),
        }


def size_parameter(drive: DriveModel, scenario: Scenario, T_request: float, parameter: str,
                   search_range: tuple[float, float], samples: int = 41,
                   settings: NumericalSettings = DEFAULT_SETTINGS) -> SizingResult:
    if parameter not in PARAMETERS:
        raise InputValidationError(f"unknown parameter {parameter!r}", field="parameter")
    lo, hi = float(search_range[0]), float(search_range[1])
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo >= hi:
        raise InputValidationError("search range must be finite with low < high", field="search_range")
    base = get_value(drive, scenario, parameter)

    def status(v: float) -> str:
        try:
            d2, s2 = apply(drive, scenario, parameter, v)
            ev = PolicyEvaluator(d2, s2, settings)
        except InputValidationError:
            return "INVALID"
        return ev.solve(T_request).policy_claim.status.value

    xs = np.linspace(lo, hi, samples)
    st = [status(float(x)) for x in xs]
    tol = 1e-9 * max(abs(lo), abs(hi), 1.0)

    def bisect(a, b, keep):
        """status(a) == keep, status(b) != keep: move a towards b while the status stays `keep`.  Returns the last
        kept point and every OTHER status met on the way (review R2 D-R2-04: an edge between FEASIBLE and UNKNOWN
        is not a bracket, and a status met between the samples is recorded, not filled in)."""
        seen = set()
        for _ in range(100):
            if abs(b - a) <= tol:
                break
            m = 0.5 * (a + b)
            st_m = status(m)
            if st_m == keep:
                a = m
            else:
                b = m
                seen.add(st_m)
        return a, b, seen

    # runs of equal status -> regions; every FEASIBLE edge is bisected (the witnessed side is extended); only an
    # edge whose other side is excluded throughout is a bracket
    runs = []
    i = 0
    while i < samples:
        j = i
        while j + 1 < samples and st[j + 1] == st[i]:
            j += 1
        runs.append([i, j, st[i]])
        i = j + 1
    regions = []
    gap_lo, gap_hi = {}, {}              # statuses met between a FEASIBLE run's bisected edge and its neighbour
    for r, (i, j, sti) in enumerate(runs):
        a, b = float(xs[i]), float(xs[j])
        if sti == "FEASIBLE":
            if i > 0:
                a0, _b, seen = bisect(xs[i], xs[i - 1], "FEASIBLE")
                a, gap_lo[r] = float(a0), seen | {runs[r - 1][2]}
            if j < samples - 1:
                b0, _a, seen = bisect(xs[j], xs[j + 1], "FEASIBLE")
                b, gap_hi[r] = float(b0), seen | {runs[r + 1][2]}
        regions.append((a, b, sti))
    # every stretch between two regions is stated explicitly: the one status met there, else UNKNOWN (never left
    # unlabelled, never filled in as feasible)
    full = []
    for r, (a, b, sti) in enumerate(regions):
        if full and full[-1][1] < a:
            pa, pb, pst = full[-1]
            met = gap_hi.get(r - 1) if pst == "FEASIBLE" else (gap_lo.get(r) if sti == "FEASIBLE" else {pst, sti})
            met = set(met or ())
            full.append((pb, a, next(iter(met)) if len(met) == 1 else "UNKNOWN"))
        full.append((a, b, sti))
    merged = []                          # adjacent stretches of one status are one region
    for a, b, sti in full:
        if merged and merged[-1][2] == sti:
            merged[-1] = (merged[-1][0], b, sti)
        else:
            merged.append((a, b, sti))
    regions = merged
    ranges = [(a, b) for a, b, sti in regions if sti == "FEASIBLE"]
    notes = [f"one-parameter change of {parameter} ({PARAMETERS[parameter][0]}); coupled constraints and losses "
             f"recomputed at every sample; searched only inside [{lo:g}, {hi:g}] (no extrapolation)",
             "regions: FEASIBLE = witnessed, INFEASIBLE = proven excluded at the samples, UNKNOWN = unresolved; "
             "between samples every statement is sampled, not a continuous proof"]
    if parameter == "Vdc_V":
        if drive.inverter.module_loss is not None:
            notes.append("the datasheet module model scales switching losses with Vdc only through a declared scaling "
                         "law; without one, samples away from the switching test voltage are UNKNOWN (no extrapolation)")
        else:
            notes.append("the synthetic inverter-loss surrogate has no Vdc dependence; switching-loss change with Vdc "
                         "needs loss data before this becomes a hardware proposal")
    if PARAMETERS[parameter][0] == "diagnostic":
        notes.append("diagnostic change: indicates the cause, not a realisable design proposal")
    if len(ranges) > 1:
        notes.append("non-monotonic response: several feasible ranges; bisection alone would not give a global minimum")
    if not ranges:
        notes.append("no feasible value found inside the searched range")
    if any(sti == "UNKNOWN" for *_, sti in regions):
        notes.append("unresolved (UNKNOWN) regions exist: a feasible value next to them is not a minimal/maximal "
                     "sizing")
    if ranges and ranges[0][0] == lo:
        notes.append("feasible at the lower end of the range: smaller values were not explored")
    first = next((r for r, (*_, sti) in enumerate(regions) if sti == "FEASIBLE"), None)
    last = max((r for r, (*_, sti) in enumerate(regions) if sti == "FEASIBLE"), default=None)
    excluded = ("INFEASIBLE", "INVALID")
    min_br = first is not None and first > 0 and all(sti in excluded for *_, sti in regions[:first])
    max_br = last is not None and last < len(regions) - 1 and all(sti in excluded for *_, sti in regions[last + 1:])
    sol = None
    mn = ranges[0][0] if ranges else None
    mx = ranges[-1][1] if ranges else None
    if mn is not None:
        d2, s2 = apply(drive, scenario, parameter, mn)
        ps = PolicyEvaluator(d2, s2, settings).solve(T_request)
        pt = ps.point
        sol = None if pt is None else {"id_A": pt.id_A, "iq_A": pt.iq_A, "i_peak_A": pt.i_peak_A, "Pdc_W": pt.Pdc_W,
                                       "Idc_A": pt.Idc_A, "voltage_margin_V": pt.voltage_margin_V,
                                       "active_constraints": [c.name for c in pt.active()]}
    ev = (Evidence.make(EvidenceKind.SAMPLED, f"{samples} samples + bisection of every FEASIBLE edge to {tol:.1e} "
                                              f"(the found witness side)"),)
    return SizingResult(describe_parameter(parameter), base, (lo, hi), samples, tuple(ranges),
                        tuple((float(x), s) for x, s in zip(xs, st)), mn, mx, sol, ev, tuple(notes),
                        tuple(regions), min_br, max_br)
