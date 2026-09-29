"""Scenario comparison: what changed between conditions and why."""

from __future__ import annotations

from .. import progress
from ..models.components import DriveModel
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.capability import policy_capability
from ..solvers.policy import PolicyEvaluator


def compare_scenarios(drive: DriveModel, scenarios: list[Scenario], T_request: float,
                      settings: NumericalSettings = DEFAULT_SETTINGS, with_capability: bool = True) -> dict:
    rows = []
    with progress.span(len(scenarios), "scenarios") as steps:
        for sc in scenarios:
            steps.step()
            rows.append(_compared(drive, sc, T_request, settings, with_capability))
    changes = []
    if rows:
        b = rows[0]
        for r in rows[1:]:
            for name, st in r["claims"].items():
                if b["claims"].get(name) != st:
                    changes.append(f"{r['scenario_id']}: {name} {b['claims'].get(name)} -> {st}")
            for name, st in r["constraint_states"].items():
                if b["constraint_states"].get(name) not in (None, st):
                    changes.append(f"{r['scenario_id']}: constraint {name} {b['constraint_states'][name]} -> {st}")
            if b["policy_capability_Nm"] is not None and r["policy_capability_Nm"] is not None:
                changes.append(f"{r['scenario_id']}: policy capability {b['policy_capability_Nm']:.6g} -> "
                               f"{r['policy_capability_Nm']:.6g} N*m (limited by "
                               f"{', '.join(r['capability_limited_by']) or 'n/a'})")
            for s in r["violated_screens"]:
                changes.append(f"{r['scenario_id']}: necessary condition violated - {s}")
    return {"T_request_Nm": T_request, "baseline": rows[0]["scenario_id"] if rows else None,
            "scenarios": rows, "changes_vs_baseline": changes}


def _compared(drive: DriveModel, sc: Scenario, T_request: float, settings: NumericalSettings,
              with_capability: bool) -> dict:
    ev = PolicyEvaluator(drive, sc, settings)
    sol = ev.solve(T_request)
    pt = sol.point
    cap = None
    if with_capability and ev.speed_in_domain and ev.k.evaluable:
        cap = policy_capability(ev, 1 if T_request >= 0 else -1, certify=False)
    return {
        "scenario_id": sc.scenario_id,
        "speed_rpm": sc.speed_rpm,
        "Vdc_V": sc.Vdc_V,
        "claims": {c.name: c.status.value for c in sol.claims},
        "id_A": None if pt is None else pt.id_A,
        "iq_A": None if pt is None else pt.iq_A,
        "i_peak_A": None if pt is None else pt.i_peak_A,
        "voltage_margin_V": None if pt is None else pt.voltage_margin_V,
        "Pdc_W": None if pt is None else pt.Pdc_W,
        "Idc_A": None if pt is None else pt.Idc_A,
        "constraint_states": {} if pt is None else {c.name: c.state for c in pt.constraints},
        "policy_capability_Nm": None if cap is None else cap.value_Nm,
        "capability_limited_by": [] if cap is None else list(cap.active_constraints),
        "violated_screens": [s.statement for s in sol.screens if s.violated],
    }
