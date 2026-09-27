"""V2: inverse goldens (golden_inverse.json) under the minimum-current policy."""

import math

import numpy as np
import pytest

from conftest import golden, scenario
from traction_workbench.solvers.policy import solve_policy

CASES = golden("golden_inverse.json")["cases"]


def _solve(drive, case):
    inp = case["input"]
    return solve_policy(drive, scenario(inp["n_rpm"], inp["Vdc_V"]), inp["Tshaft_requested_Nm"])


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_labels(drive, case):
    sol = _solve(drive, case)
    assert sol.electrical.status.value == case["electrical_existence"]
    assert sol.policy_claim.status.value == case["minimum_current_policy_with_dc"]


@pytest.mark.parametrize("case", [c for c in CASES if c["expected_policy_solution"]],
                         ids=[c["case_id"] for c in CASES if c["expected_policy_solution"]])
def test_policy_solution_matches_golden(drive, case):
    sol = _solve(drive, case)
    pt = sol.point
    exp = case["expected_policy_solution"]
    assert abs(pt.id_A - exp["id_A_peak"]) <= 1e-3
    assert abs(pt.iq_A - exp["iq_A_peak"]) <= 1e-3
    for got, key in ((pt.vd_V, "vd_V_peak"), (pt.vq_V, "vq_V_peak"), (pt.v_peak_V, "v_peak_V")):
        assert abs(got - exp[key]) <= 1e-3, key
    for got, key in ((pt.Pshaft_W, "Pshaft_W"), (pt.Pcu_W, "Pcu_W"), (pt.Prot_W, "Prot_W"), (pt.Pac_W, "Pac_W"),
                     (pt.Pinv_W, "Pinv_W"), (pt.Pdc_W, "Pdc_W")):
        assert abs(got - exp[key]) <= 0.5, key
    dc_ok = all(c.ok for c in pt.constraints if c.group in ("DISCHARGE_SOURCE", "CHARGE_SOURCE"))
    assert dc_ok == exp["dc_ok"]


@pytest.mark.parametrize("case", [c for c in CASES if c["expected_policy_solution"]],
                         ids=[c["case_id"] for c in CASES if c["expected_policy_solution"]])
def test_numerical_acceptance(drive, case):
    sol = _solve(drive, case)
    acc = dict(sol.acceptance)
    req = case["input"]["Tshaft_requested_Nm"]
    assert acc["torque_residual_Nm"] <= max(1e-3, 1e-6 * acc["torque_scale_Nm"])
    assert acc["max_normalized_hard_violation"] <= 1e-7
    assert acc["passed"]
    assert sol.point.Tshaft_Nm == pytest.approx(req, abs=1e-9)


@pytest.mark.parametrize("case", [c for c in CASES if c["expected_policy_solution"]],
                         ids=[c["case_id"] for c in CASES if c["expected_policy_solution"]])
def test_feasible_id_intervals(drive, case):
    sol = _solve(drive, case)
    got = [[s.start.id_A, s.end.id_A] for s in sol.curve.segments]
    ref = case["reference_feasible_id_intervals_A"]
    assert len(got) == len(ref)
    for g, r in zip(got, ref):
        assert abs(g[0] - r[0]) <= 1e-3 and abs(g[1] - r[1]) <= 1e-3


def test_i03_infeasibility_is_proven_not_a_solver_failure(drive):
    case = next(c for c in CASES if c["case_id"].startswith("I03"))
    sol = _solve(drive, case)
    assert sol.point is None
    kinds = {e.kind.value for e in sol.electrical.evidence}
    assert "exact_boundary_enumeration" in kinds and "analytic_necessary_condition" in kinds
    b = case["independent_infeasibility_bound"]
    sc = next(s for s in sol.screens if s.name == "d_axis_voltage_exceeds_budget")
    v = dict(sc.values)
    assert sc.violated
    assert v["iq_min_A"] == pytest.approx(b["iq_min_A"], abs=1e-9)
    assert v["abs_vd_lower_bound_V"] == pytest.approx(b["required_abs_vd_lower_bound_V"], abs=1e-9)
    assert v["voltage_budget_V"] == pytest.approx(b["allowed_voltage_peak_V"], abs=1e-9)
    assert "declared domain" in sol.electrical.detail


def test_i06_source_bound_is_physical(drive):
    case = next(c for c in CASES if c["case_id"].startswith("I06"))
    sol = _solve(drive, case)
    b = case["independent_infeasibility_bound"]
    sc = next(s for s in sol.screens if s.name == "shaft_power_exceeds_discharge_cap")
    assert sc.violated and dict(sc.values)["P_shaft_W"] == pytest.approx(b["required_shaft_power_W"], abs=1e-6)
    assert sol.physical_dc.status.value == "INFEASIBLE"


def test_i07_charge_cap_unreachable_even_with_maximum_loss(drive):
    case = next(c for c in CASES if c["case_id"].startswith("I07"))
    sol = _solve(drive, case)
    b = case["independent_infeasibility_bound"]
    sc = next(s for s in sol.screens if s.name == "charge_cap_unreachable_even_with_maximum_loss")
    assert sc.violated and dict(sc.values)["max_possible_Pdc_W"] == pytest.approx(b["maximum_possible_Pdc_W"], abs=1e-6)
    assert sol.physical_dc.status.value == "INFEASIBLE"
    assert sol.active_loss_candidate is None


def test_duration_is_never_implied(drive):
    for case in CASES:
        sol = _solve(drive, case)
        assert all(c.time_horizon.startswith("static") for c in sol.claims)
