"""Bounded one-parameter inverse sizing (Handoff H5.5).

Changes exactly one declared parameter inside an explicit search range,
recomputes the coupled problem (constraints and losses) at every sample and
bisects the status transitions.  Nothing is extrapolated beyond the range;
a non-monotonic response is reported as several feasible ranges, never as a
single "minimum" found by bisection alone.
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

    def to_dict(self) -> dict:
        return {
            "parameter": self.parameter,
            "baseline_value": self.baseline_value,
            "search_range": list(self.search_range),
            "samples": self.samples,
            "feasible_ranges": [list(r) for r in self.feasible_ranges],
            "minimal_feasible_value": self.minimal_feasible,
            "maximal_feasible_value": self.maximal_feasible,
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

    def bisect(a, b):
        # status(a) == FEASIBLE != status(b)
        for _ in range(100):
            if abs(b - a) <= tol:
                break
            m = 0.5 * (a + b)
            if status(m) == "FEASIBLE":
                a = m
            else:
                b = m
        return a

    ranges = []
    i = 0
    while i < samples:
        if st[i] != "FEASIBLE":
            i += 1
            continue
        j = i
        while j + 1 < samples and st[j + 1] == "FEASIBLE":
            j += 1
        a = xs[i] if i == 0 else bisect(xs[i], xs[i - 1])
        b = xs[j] if j == samples - 1 else bisect(xs[j], xs[j + 1])
        ranges.append((float(a), float(b)))
        i = j + 1
    notes = [f"one-parameter change of {parameter} ({PARAMETERS[parameter][0]}); coupled constraints and losses "
             f"recomputed at every sample; searched only inside [{lo:g}, {hi:g}] (no extrapolation)"]
    if parameter == "Vdc_V":
        notes.append("the synthetic inverter-loss surrogate has no Vdc dependence; switching-loss change with Vdc "
                     "needs loss data before this becomes a hardware proposal")
    if PARAMETERS[parameter][0] == "diagnostic":
        notes.append("diagnostic change: indicates the cause, not a realisable design proposal")
    if len(ranges) > 1:
        notes.append("non-monotonic response: several feasible ranges; bisection alone would not give a global minimum")
    if not ranges:
        notes.append("no feasible value found inside the searched range")
    if ranges and ranges[0][0] == lo:
        notes.append("feasible at the lower end of the range: smaller values were not explored")
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
    ev = (Evidence.make(EvidenceKind.SAMPLED, f"{samples} samples + bisection of every status transition to {tol:.1e}"),)
    return SizingResult(describe_parameter(parameter), base, (lo, hi), samples, tuple(ranges),
                        tuple((float(x), s) for x, s in zip(xs, st)), mn, mx, sol, ev, tuple(notes))
