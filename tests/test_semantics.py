"""semantic_boundary_cases.json S00-S08 and related decision semantics."""

import math

import numpy as np
import pytest

from conftest import golden, scenario
from traction_workbench import spec_fixtures as sf
from traction_workbench.analysis.loss_interval import dc_acceptance_with_loss_interval
from traction_workbench.analysis.supplied_policy import CurrentPolicyTable, evaluate_supplied_policy
from traction_workbench.analysis.uncertainty import ParameterInterval, bounded_input_analysis
from traction_workbench.decision import evaluate_requirement
from traction_workbench.errors import InputValidationError
from traction_workbench.io import parse_json
from traction_workbench.models import DataOrigin, Provenance
from traction_workbench.requirement import Requirement
from traction_workbench.scenario import Scenario
from traction_workbench.settings import DEFAULT_SETTINGS
from traction_workbench.solvers.policy import solve_policy
from traction_workbench.status import Status

CASES = {c["case_id"]: c for c in golden("semantic_boundary_cases.json")["cases"]}


def test_s00_loss_interval_three_answers():
    c = CASES["S00_REGEN_LOSS_INTERVAL"]
    r = dc_acceptance_with_loss_interval(c["Pshaft_W"], tuple(c["total_loss_interval_W"]), charge_cap_W=c["charge_cap_W"])
    assert list(r.Pdc_interval_W) == c["Pdc_interval_W"]
    assert r.actual.status is Status.UNKNOWN and r.actual.reasons[0].value == "UNCERTAINTY_OVERLAP"
    assert r.robust.status is Status.INFEASIBLE
    assert "L = 2000 W" in r.robust.evidence[1].summary          # minimum loss is the counterexample
    assert r.enclosure.status is Status.UNKNOWN


def test_s00_max_loss_only_would_be_optimistic():
    # checking regen with the largest loss alone would pass; the smallest loss fails
    r = dc_acceptance_with_loss_interval(-80000, (2000, 8000), charge_cap_W=75000)
    assert -80000 + 8000 >= -75000 and r.robust.status is Status.INFEASIBLE


def test_s01_missing_duration(drive, limits):
    req = Requirement("REQ-S01", "I02 with a 10 s hold", 150, 12000, 600, duration_s=10)
    rec = evaluate_requirement(req, drive, source_limits=limits)
    cr = rec.conditions[0]
    assert cr.solution.electrical.status is Status.FEASIBLE
    assert cr.duration.status is Status.UNKNOWN and cr.duration.reasons[0].value == "UNVALIDATED_DURATION"
    assert rec.verdict.status is Status.UNKNOWN


@pytest.mark.parametrize("vdc", CASES["S02_INVALID_VDC"]["input_values_V"])
def test_s02_invalid_vdc(vdc, limits):
    with pytest.raises(InputValidationError, match="strictly positive"):
        Scenario("bad", 3000, vdc, limits)
    with pytest.raises(InputValidationError):
        Requirement("R", "bad", 100, 3000, vdc)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_s03_nonfinite_tokens(token, limits):
    with pytest.raises(InputValidationError, match="non-finite"):
        parse_json('{"value": %s}' % token)
    with pytest.raises(InputValidationError):
        Scenario("bad", float(token.replace("Infinity", "inf")), 600, limits)


def test_s05_numerical_unresolved_is_unknown_not_infeasible():
    """A flux-map case with deliberately starved search/bounds resolution must not become INFEASIBLE."""
    settings = DEFAULT_SETTINGS.with_(sampled_id_points=3, sampled_iq_points=3, bnb_max_depth=0)
    # region only partly reachable: 30 N*m at 6000 rpm has solutions, but 3x3 sampling cannot find them
    d = sf.manufactured_map_drive()
    sol = solve_policy(d, scenario(6000, 600), 30.0, settings)
    assert sol.electrical.status is not Status.INFEASIBLE
    if sol.electrical.status is Status.UNKNOWN:
        assert "NUMERICAL_UNRESOLVED" in [r.value for r in sol.electrical.reasons]


def test_s06_zero_shaft_torque_at_speed(drive):
    sol = solve_policy(drive, scenario(12000, 600), 0.0)
    pt = sol.point
    assert sol.policy_claim.status is Status.FEASIBLE
    assert pt.Tshaft_Nm == pytest.approx(0.0, abs=1e-9)
    assert pt.Te_Nm == pytest.approx(0.002 * 2 * math.pi * 12000 / 60, rel=1e-12)   # drag compensation
    assert pt.id_A < -100.0 and pt.iq_A > 0                                          # field weakening, not id=iq=0
    assert pt.constraint("VOLTAGE").state == "ACTIVE"


def _mtpa_only_table(drive):
    """A supplied policy that never field-weakens (MTPA only), built in closed form for the test."""
    psi, ld, lq, p = 0.1, 0.0002, 0.0004, 4
    torques = np.linspace(-350, 350, 71)
    speeds = np.linspace(0, 16000, 17)
    ids = np.zeros((torques.size, speeds.size))
    iqs = np.zeros_like(ids)
    for i, t in enumerate(torques):
        for j, n in enumerate(speeds):
            tem = t + 0.002 * 2 * math.pi * n / 60
            a = tem / (1.5 * p)
            # MTPA on the torque curve: id*psi + (ld-lq)(id^2 - iq^2) = 0 with iq = a/(psi+(ld-lq) id); solve by bisection
            f = lambda x: x * psi + (ld - lq) * (x * x - (a / (psi + (ld - lq) * x)) ** 2)
            lo, hi = -500.0, 0.0
            if a == 0:
                ids[i, j], iqs[i, j] = 0.0, 0.0
                continue
            for _ in range(200):
                m = 0.5 * (lo + hi)
                lo, hi = (m, hi) if f(m) < 0 else (lo, m)
            ids[i, j] = 0.5 * (lo + hi)
            iqs[i, j] = a / (psi + (ld - lq) * ids[i, j])
    prov = Provenance(DataOrigin.SYNTHETIC, "test MTPA-only map", "T1", "test data")
    return CurrentPolicyTable("MTPA_ONLY_TEST", "T1", torques, speeds, ids, iqs, prov, Vdc_valid_V=(550, 650),
                              torque_tolerance_Nm=0.5)


def test_s07_supplied_policy_failure_not_overwritten(drive):
    table = _mtpa_only_table(drive)
    res = evaluate_supplied_policy(table, drive, scenario(12000, 600), 150.0)
    assert res["claim"]["status"] == "INFEASIBLE"                        # MTPA-only violates voltage at high speed
    assert "VOLTAGE" in res["claim"]["detail"]
    assert res["policy_gap"]["minimum_current_policy_status"] == "FEASIBLE"
    assert "does not replace" in res["policy_gap"]["note"]


def test_s07_policy_hole_and_vdc_are_unknown(drive):
    table = _mtpa_only_table(drive)
    assert evaluate_supplied_policy(table, drive, scenario(12000, 450), 100.0)["claim"]["reasons"] == ["POLICY_LIMITATION"]
    assert evaluate_supplied_policy(table, drive, scenario(3000, 600), 400.0)["claim"]["status"] == "UNKNOWN"
    ok = evaluate_supplied_policy(table, drive, scenario(3000, 600), 200.0)
    assert ok["claim"]["status"] == "FEASIBLE"


def test_s08_sampled_coverage_is_not_a_worst_case_pass(drive, limits):
    req = Requirement("REQ-S08", "550..650 V, 100 N*m @ 12000 rpm", 100, 12000, (550, 650), Vdc_quantifier="for_all")
    rec = evaluate_requirement(req, drive, source_limits=limits)
    assert rec.verdict.status is Status.UNKNOWN and rec.verdict.reasons[0].value == "SAMPLED_COVERAGE"
    assert all(c.requirement_claim.status is Status.FEASIBLE for c in rec.conditions)


def test_s08_bounded_input_adaptive_vs_fixed(drive):
    b = bounded_input_analysis(drive, scenario(12000, 600), 150,
                               [ParameterInterval("psi_pm_Wb", 0.095, 0.105, basis="test tolerance")])
    claims = {c["name"]: c for c in b["claims"]}
    assert claims["robust_adaptive"]["status"] == "UNKNOWN"                       # corners only
    assert claims["robust_fixed_calibration"]["status"] == "INFEASIBLE"           # torque error with fixed currents
    enc = bounded_input_analysis(drive, scenario(12000, 600), 150,
                                 [ParameterInterval("psi_pm_Wb", 0.095, 0.105, kind="outer_enclosure")])
    assert {c["name"]: c for c in enc["claims"]}["robust_fixed_calibration"]["status"] == "UNKNOWN"


def test_range_requirement_counterexample_fails(drive, limits):
    req = Requirement("REQ-R", "450..600 V", 150, 12000, (450, 600), Vdc_quantifier="for_all")
    rec = evaluate_requirement(req, drive, source_limits=limits)
    assert rec.verdict.status is Status.INFEASIBLE
    assert any("counterexample" in q for q in rec.qualifiers)


def test_active_loss_candidate_is_flagged_not_adopted(drive):
    # -90 N*m at 12000 rpm: minimum-current regen exceeds the charge cap, but raising losses could meet it
    sol = solve_policy(drive, scenario(12000, 600), -86.0)
    assert sol.policy_claim.status is Status.INFEASIBLE
    assert sol.physical_dc.status is Status.FEASIBLE
    assert sol.active_loss_candidate is not None
    assert sol.active_loss_candidate.i_peak_A > sol.point.i_peak_A
    assert any("not adopted" in n for n in sol.notes)
