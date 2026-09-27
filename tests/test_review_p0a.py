"""Regression tests for the independent engineering review, P0-A correctness cases (F01-F13).

Each test reproduces an observed wrong behaviour of the reviewed baseline and asserts the
requested behaviour.  Old outputs are not kept as expected values.
"""

import math
from dataclasses import replace

import numpy as np
import pytest

from conftest import scenario
from traction_workbench import spec_fixtures as sf
from traction_workbench.models.flux import ConstantFluxModel, CurrentBox, FluxMapModel, FluxMapPlane
from traction_workbench.physics import forward_evaluation
from traction_workbench.scenario import DcSourceLimits, Scenario
from traction_workbench.settings import DEFAULT_SETTINGS
from traction_workbench.solvers.capability import physical_capability, policy_capability
from traction_workbench.solvers.gate import check_witness
from traction_workbench.solvers.policy import PolicyEvaluator
from traction_workbench.status import Reason, Status


def _with_flux(drive, flux):
    return replace(drive, motor=replace(drive.motor, flux=flux))


def coarse_cross_saturated_drive():
    """3x3 map with strong cross-saturation: T_em is strongly curved inside a cell (sampled estimates are poor)."""
    ids = np.array([-200.0, -100.0, 0.0])
    iqs = np.array([-200.0, 0.0, 200.0])
    psd = np.empty((3, 3))
    psq = np.empty((3, 3))
    for i, d in enumerate(ids):
        for j, q in enumerate(iqs):
            psd[i, j] = (0.1 + 0.0002 * d) * (0.2 if q != 0 else 1.0)
            psq[i, j] = 0.0004 * q * (0.5 if q != 0 else 1.0)
    return sf.manufactured_map_drive(FluxMapModel((FluxMapPlane(ids, iqs, psd, psq),)))


# ----------------------------------------------------------------------------------------------
# F01 - one validity gate for every path
# ----------------------------------------------------------------------------------------------

def test_f01_missing_rs_reference_gates_forward_capability_and_policy(drive, limits):
    sc = Scenario("hot", 3000.0, 600.0, limits, winding_temp_C=150.0)   # Rs reference temperature not declared
    fr = forward_evaluation(drive, sc, -100.0, 200.0)
    assert fr.evaluable and fr.point is not None                    # the numbers stay available ...
    assert not fr.validity_gate_passed and not fr.accepted          # ... but only as a diagnostic
    assert fr.to_dict()["accepted_as_evidence"] is False and "DIAGNOSTIC ONLY" in fr.message
    ev = PolicyEvaluator(drive, sc)
    assert ev.solve(100.0).policy_claim.status is Status.UNKNOWN
    pc = policy_capability(ev, 1)
    assert pc.value_Nm is None and not pc.accepted and pc.gate_messages
    ph = physical_capability(ev, 1)
    assert not ph.certified and not ph.accepted
    assert ev.quick_status(100.0)[0] == "UNKNOWN"
    # the same point at a valid scenario passes the gate
    ok = forward_evaluation(drive, Scenario("ok", 3000.0, 600.0, limits), -100.0, 200.0)
    assert ok.validity_gate_passed and ok.accepted


def test_f01_gate_function_names_the_reason(drive, limits):
    from traction_workbench.physics import DriveKernel
    k = DriveKernel(drive, Scenario("hot", 3000.0, 600.0, limits, winding_temp_C=150.0))
    chk = check_witness(k, -100.0, 200.0, T_request=None)
    assert not chk.accepted and Reason.MISSING_INPUT in chk.reasons


# ----------------------------------------------------------------------------------------------
# F02 - a witness is re-verified against the ORIGINAL request
# ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("n_grid", [3, 5, 9])
@pytest.mark.parametrize("T", [10.0, 20.0, 30.0])
def test_f02_coarse_search_never_hands_over_a_point_for_another_torque(n_grid, T, limits):
    dm = coarse_cross_saturated_drive()
    s = replace(DEFAULT_SETTINGS, sampled_id_points=n_grid, sampled_iq_points=n_grid)
    sc = Scenario("m", 1000.0, 600.0, limits)
    sol = PolicyEvaluator(dm, sc, s).solve(T)
    for claim in (sol.electrical, sol.policy_claim, sol.physical_dc):
        if claim.status is not Status.FEASIBLE:
            continue
        for e in claim.evidence:
            d = e.to_dict().get("data", {})
            if "id_A" in d and "iq_A" in d:
                pt = forward_evaluation(dm, sc, d["id_A"], d["iq_A"], s).point
                assert pt.Tshaft_Nm == pytest.approx(T, abs=2e-3)
        if claim.name == "physical_existence_with_dc":
            data = claim.evidence[0].to_dict()["data"]
            assert data["accepted_as_witness"] is True
            assert abs(data["torque_residual_Nm"]) <= data["torque_residual_tolerance_Nm"]


def test_f02_gate_rejects_a_point_answering_a_different_torque(limits):
    from traction_workbench.physics import DriveKernel
    dm = coarse_cross_saturated_drive()
    k = DriveKernel(dm, Scenario("m", 1000.0, 600.0, limits))
    pt = forward_evaluation(dm, Scenario("m", 1000.0, 600.0, limits), 0.0, 60.0).point
    chk = check_witness(k, 0.0, 60.0, T_request=pt.Tshaft_Nm + 5.0, require_dc=True)
    assert not chk.accepted and Reason.NUMERICAL_UNRESOLVED in chk.reasons


# ----------------------------------------------------------------------------------------------
# F03 - control domain != data domain
# ----------------------------------------------------------------------------------------------

def test_f03_coverage_limited_to_deep_field_weakening_gives_global_unknown(drive, limits):
    fl = drive.motor.flux
    dcov = _with_flux(drive, ConstantFluxModel(fl.psi_pm_Wb, fl.Ld_H, fl.Lq_H,
                                               validity=CurrentBox((-600.0, -300.0), (-600.0, 600.0))))
    for n, T in ((3000.0, -80.0), (6000.0, -100.0), (9000.0, -150.0), (12000.0, -100.0)):
        full = PolicyEvaluator(drive, Scenario("r", n, 600.0, limits)).solve(T)
        ev = PolicyEvaluator(dcov, Scenario("r", n, 600.0, limits))
        sol = ev.solve(T)
        assert full.point.id_A > -300.0                         # the true optimum is outside the data
        assert sol.policy_claim.status is Status.UNKNOWN        # never PASS (nor FAIL) from the covered part
        assert Reason.OUTSIDE_MODEL_DOMAIN in sol.policy_claim.reasons
        assert ev.quick_status(T)[0] == "UNKNOWN"
        assert sol.electrical.status is Status.FEASIBLE         # existence of *a* point is still shown


def test_f03_coverage_that_contains_the_optimum_still_certifies(drive, limits):
    fl = drive.motor.flux
    dcov = _with_flux(drive, ConstantFluxModel(fl.psi_pm_Wb, fl.Ld_H, fl.Lq_H,
                                               validity=CurrentBox((-600.0, 0.0), (-600.0, 600.0))))
    sol = PolicyEvaluator(dcov, Scenario("r", 3000.0, 600.0, limits)).solve(100.0)
    assert sol.policy_claim.status is Status.FEASIBLE


# ----------------------------------------------------------------------------------------------
# F03b - degenerate / boundary inputs: explicit handling, no crash
# ----------------------------------------------------------------------------------------------

def test_f03b_psi_pm_zero_synchronous_reluctance_matches_analytic_mtpa(drive, limits):
    fl = drive.motor.flux
    syn = _with_flux(drive, ConstantFluxModel(0.0, fl.Ld_H, fl.Lq_H))
    sol = PolicyEvaluator(syn, Scenario("s", 1000.0, 600.0, limits)).solve(50.0)
    wm = 2 * math.pi * 1000 / 60
    tem = 50.0 + 0.002 * wm
    prod = tem / (1.5 * 4 * (fl.Ld_H - fl.Lq_H))                  # id*iq for psi = 0
    i45 = math.sqrt(-prod)
    assert sol.policy_claim.status is Status.FEASIBLE
    assert sol.point.id_A == pytest.approx(-i45, abs=1e-6) and sol.point.iq_A == pytest.approx(i45, abs=1e-6)


@pytest.mark.parametrize("n", [-3000.0, 0.0, 3000.0, 12000.0])
@pytest.mark.parametrize("T", [0.0, 1e-9, 50.0, -50.0, 300.0])
def test_f03b_no_crash_on_degenerate_models(n, T, drive, limits):
    fl = drive.motor.flux
    models = [ConstantFluxModel(0.0, fl.Ld_H, fl.Lq_H),
              ConstantFluxModel(fl.psi_pm_Wb, fl.Ld_H, fl.Lq_H, validity=CurrentBox((-600.0, 0.0), (0.0, 300.0))),
              ConstantFluxModel(fl.psi_pm_Wb, fl.Ld_H, fl.Lq_H, validity=CurrentBox((-600.0, 0.0), (-300.0, 0.0)))]
    for m in models:
        ev = PolicyEvaluator(_with_flux(drive, m), Scenario("x", n, 600.0, limits))
        sol = ev.solve(T)
        assert sol.policy_claim.status in (Status.FEASIBLE, Status.INFEASIBLE, Status.UNKNOWN)
        if sol.point is not None and sol.policy_claim.status is Status.FEASIBLE:
            assert check_witness(ev.k, sol.point.id_A, sol.point.iq_A, T_request=T, require_dc=True).accepted
        policy_capability(ev, 1 if T >= 0 else -1)


def test_f03b_iq_validity_edge_is_a_boundary_not_an_exception(drive, limits):
    fl = drive.motor.flux
    dv = _with_flux(drive, ConstantFluxModel(fl.psi_pm_Wb, fl.Ld_H, fl.Lq_H,
                                             validity=CurrentBox((-600.0, 0.0), (0.0, 300.0))))
    sol = PolicyEvaluator(dv, Scenario("x", 3000.0, 600.0, limits)).solve(300.0)
    # the unconstrained optimum (iq ~ 362 A) is outside the validity box: a covered point exists on the
    # iq = 300 A edge, but the global minimum-current policy cannot be certified from the covered data
    assert sol.electrical.status is Status.FEASIBLE
    assert sol.point is not None and sol.point.iq_A <= 300.0 + 1e-6
    assert sol.policy_claim.status is Status.UNKNOWN


# ----------------------------------------------------------------------------------------------
# F04 - typed finite / continuous rating semantics, order invariance
# ----------------------------------------------------------------------------------------------

def _env(eid, duration, t_max, prio=0, cond=(), rev="A"):
    from traction_workbench.analysis.rating import RatingEnvelope
    from traction_workbench.models.provenance import DataOrigin, Provenance
    prov = Provenance(DataOrigin.SUPPLIER, "test sheet", rev, "supplier-rated (test fixture)")
    return RatingEnvelope(eid, rev, duration, (0.0, 16000.0), (t_max, t_max), prov,
                          min_braking_torque_Nm=(-t_max, -t_max), conditions=cond, priority=prio)


def test_f04_ten_second_rating_never_passes_a_continuous_requirement():
    from traction_workbench.analysis.rating import duration_claim
    c = duration_claim((_env("PEAK10", 10.0, 500.0),), math.inf, 3000.0, 100.0, {})
    assert c.status is Status.UNKNOWN and Reason.UNVALIDATED_DURATION in c.reasons
    ok = duration_claim((_env("CONT", math.inf, 300.0),), math.inf, 3000.0, 100.0, {})
    assert ok.status is Status.FEASIBLE


def test_f04_duration_monotonicity_and_continuous_to_finite_needs_a_cold_start():
    from traction_workbench.analysis.rating import duration_claim
    assert duration_claim((_env("P30", 30.0, 400.0),), 10.0, 3000.0, 350.0, {}).status is Status.FEASIBLE
    assert duration_claim((_env("P5", 5.0, 400.0),), 10.0, 3000.0, 350.0, {}).status is Status.UNKNOWN
    cont = (_env("CONT", math.inf, 300.0),)
    assert duration_claim(cont, 10.0, 3000.0, 250.0, {}).status is Status.UNKNOWN
    assert duration_claim(cont, 10.0, 3000.0, 250.0, {"initial_state": "cold"}).status is Status.FEASIBLE


def test_f04_result_is_independent_of_envelope_order():
    from traction_workbench.analysis.rating import duration_claim
    a, b = _env("A", 10.0, 400.0), _env("B", 10.0, 300.0)       # disagree at 350 N*m
    r1 = duration_claim((a, b), 10.0, 3000.0, 350.0, {})
    r2 = duration_claim((b, a), 10.0, 3000.0, 350.0, {})
    assert r1.status is r2.status is Status.UNKNOWN and Reason.CONFLICTING_EVIDENCE in r1.reasons
    b_auth = _env("B", 10.0, 300.0, prio=1, rev="B")               # declared authority decides, not the order
    for envs in ((a, b_auth), (b_auth, a)):
        r = duration_claim(envs, 10.0, 3000.0, 350.0, {})
        assert r.status is Status.INFEASIBLE and Reason.RATING_NOT_MET in r.reasons


def test_f04_outside_envelope_is_rating_not_met_not_physical_impossibility():
    from traction_workbench.analysis.rating import duration_claim
    r = duration_claim((_env("P10", 10.0, 300.0),), 10.0, 3000.0, 350.0, {})
    assert r.status is Status.INFEASIBLE and Reason.RATING_NOT_MET in r.reasons
    assert "not a proof of physical impossibility" in r.detail


# ----------------------------------------------------------------------------------------------
# F04b - band existence: one witness for every part; a failing centre does not fail the band
# ----------------------------------------------------------------------------------------------

def _req(**kw):
    from traction_workbench.requirement import Requirement
    base = dict(req_id="R-BAND", text="band test", target_Nm=0.0, speed_rpm=16000.0, Vdc_V=450.0)
    base.update(kw)
    return Requirement(**base)


def test_f04b_band_centre_failure_does_not_fail_the_band(drive):
    from traction_workbench.decision import evaluate_requirement
    lim = DcSourceLimits(3000.0, 3000.0, math.inf, math.inf, source="test: 3 kW both ways, currents unlimited")
    req = _req(operator="band", band_Nm=5.0)
    rec = evaluate_requirement(req, drive, source_limits=lim)
    cr = rec.conditions[0]
    assert cr.solution.policy_claim.status is Status.INFEASIBLE      # the centre (0 N*m) exceeds the discharge cap
    assert rec.verdict.status is Status.FEASIBLE
    assert cr.witness_torque_Nm is not None and -5.0 - 1e-9 <= cr.witness_torque_Nm < 0.0
    w = cr.witness_solution.point
    assert w.Pdc_W > 0          # mechanical braking that still draws DC power (not "regeneration")


def test_f04b_duration_is_checked_at_the_same_witness(drive, limits):
    from traction_workbench.decision import evaluate_requirement
    req = _req(target_Nm=-100.0, speed_rpm=12000.0, Vdc_V=600.0, operator="band", band_Nm=20.0, duration_s=10.0)
    rec = evaluate_requirement(req, drive, source_limits=limits, ratings=(_env("P10", 10.0, 90.0),))
    cr = rec.conditions[0]
    assert rec.verdict.status is Status.FEASIBLE
    assert abs(cr.witness_torque_Nm) <= 90.0                          # rated AND statically feasible there
    assert cr.duration.status is Status.FEASIBLE


def test_f04b_band_beyond_certified_capability_is_infeasible_with_proof(drive, limits):
    from traction_workbench.decision import evaluate_requirement
    req = _req(target_Nm=260.0, speed_rpm=12000.0, Vdc_V=450.0, operator="band", band_Nm=10.0)
    rec = evaluate_requirement(req, drive, source_limits=limits)
    assert rec.verdict.status is Status.INFEASIBLE
    assert "capability bound" in rec.conditions[0].requirement_claim.detail


# ----------------------------------------------------------------------------------------------
# F05 - the selected chain must actually connect fault and physical safe state
# ----------------------------------------------------------------------------------------------

def test_f05_finer_item_cannot_drop_the_rest_of_a_composite_span():
    from traction_workbench.extensions.timing import TimingChain, TimingItem, analyze_timing
    ev = ("fault", "detected", "confirmed", "safe_state")
    items = (TimingItem("SW_DETECT", "fault", "detected", "SW", 0.002),           # finer, covers only part
             TimingItem("SYS_FDTI", "fault", "confirmed", "System", 0.012),       # composite: the only budget of
             TimingItem("REACT", "confirmed", "safe_state", "HW", 0.005))         # detected -> confirmed
    r = analyze_timing(TimingChain("C", "oc", 0.010, ev, items, detection_event="confirmed"))
    # the reviewed baseline summed SW_DETECT + REACT = 7 ms (skipping detected->confirmed) and passed
    assert r["chosen_path"] == ["SYS_FDTI", "REACT"] and r["worst_s"] == pytest.approx(0.017)
    assert r["claim"]["status"] != "FEASIBLE"
    # a fine budget for the missing interval makes a tighter contiguous path possible
    items2 = items + (TimingItem("CONFIRM", "detected", "confirmed", "SW", 0.002),)
    r2 = analyze_timing(TimingChain("C", "oc", 0.010, ev, items2, detection_event="confirmed"))
    assert r2["chosen_path"] == ["SW_DETECT", "CONFIRM", "REACT"] and r2["claim"]["status"] == "FEASIBLE"


def test_f05_overlapping_items_that_never_join_are_unknown():
    from traction_workbench.extensions.timing import TimingChain, TimingItem, analyze_timing
    ev = ("a", "b", "c", "d")
    items = (TimingItem("X", "a", "c", "SW", 0.001), TimingItem("Y", "b", "d", "HW", 0.001))
    r = analyze_timing(TimingChain("C", "f", 0.01, ev, items))
    assert not r["gaps"] and r["paths"] == 0 and r["claim"]["status"] == "UNKNOWN"


def test_f05_command_endpoint_is_not_a_physical_safe_state():
    from traction_workbench.extensions.timing import TimingChain, TimingItem, analyze_timing
    ev = ("fault", "confirmed", "gate_off_command")
    items = (TimingItem("D", "fault", "confirmed", "SW", 0.002), TimingItem("R", "confirmed", "gate_off_command", "SW", 0.001))
    r = analyze_timing(TimingChain("C", "f", 0.01, ev, items, endpoint_kind="command_issued"))
    assert r["claim"]["status"] == "UNKNOWN" and "physical safe state" in r["claim"]["detail"]


def test_f05_composite_across_detection_event_gives_unknown_split():
    from traction_workbench.extensions.timing import TimingChain, TimingItem, analyze_timing
    ev = ("fault", "confirmed", "safe_state")
    r = analyze_timing(TimingChain("C", "f", 0.02, ev, (TimingItem("ALL", "fault", "safe_state", "System", 0.01),),
                                   detection_event="confirmed", fdti_budget_s=0.005, frti_budget_s=0.01))
    assert r["claim"]["status"] == "FEASIBLE" and r["fdti_worst_s"] is None and r["frti_worst_s"] is None
    assert any("cannot be split" in n for n in r["notes"])
