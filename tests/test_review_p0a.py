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
    from traction_workbench.analysis.rating import ApprovalState, RatingApproval
    prov = Provenance(DataOrigin.SUPPLIER, "test sheet", rev, "supplier-rated (test fixture)")
    return RatingEnvelope(eid, rev, duration, (0.0, 16000.0), (t_max, t_max), prov,
                          min_braking_torque_Nm=(-t_max, -t_max), conditions=cond, priority=prio,
                          approval=RatingApproval(ApprovalState.APPROVED, f"TEST-{eid}", rev, "test rating"))


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


# ----------------------------------------------------------------------------------------------
# F06 - supplied / fixed policy: missing != unlimited, NOT_EVALUATED != pass, residual != accuracy
# ----------------------------------------------------------------------------------------------

def _single_point_table(drive, idq, torque=(0.0, 400.0), speed=(0.0, 16000.0), tol=0.5):
    from traction_workbench.analysis.supplied_policy import CurrentPolicyTable
    from traction_workbench.models.provenance import DataOrigin, Provenance
    ids = np.full((2, 2), idq[0])
    iqs = np.full((2, 2), idq[1])
    prov = Provenance(DataOrigin.SUPPLIER, "test map", "T", "test data")
    return CurrentPolicyTable("FIXED", "T", np.array(torque), np.array(speed), ids, iqs, prov, torque_tolerance_Nm=tol)


def test_f06_missing_loss_model_or_dc_limits_is_not_a_pass(drive, limits):
    from traction_workbench.analysis.supplied_policy import evaluate_supplied_policy
    sc = Scenario("s", 3000.0, 600.0, limits)
    pt = PolicyEvaluator(drive, sc).solve(100.0).point
    table = _single_point_table(drive, (pt.id_A, pt.iq_A))
    ok = evaluate_supplied_policy(table, drive, sc, 100.0, compare_min_current=False)
    assert ok["claim"]["status"] == "FEASIBLE"
    no_loss = replace(drive, inverter=replace(drive.inverter, loss=None))
    r1 = evaluate_supplied_policy(table, no_loss, sc, 100.0, compare_min_current=False)
    assert r1["claim"]["status"] == "UNKNOWN" and r1["claim"]["reasons"] == ["MISSING_INPUT"]
    only_charge = Scenario("s", 3000.0, 600.0, DcSourceLimits(charge_power_max_W=1e5, charge_current_max_A=200.0))
    r2 = evaluate_supplied_policy(table, drive, only_charge, 100.0, compare_min_current=False)
    assert r2["claim"]["status"] == "UNKNOWN" and "discharge" in r2["claim"]["detail"]
    unlimited = Scenario("s", 3000.0, 600.0, DcSourceLimits(math.inf, 1e5, math.inf, 200.0))
    assert evaluate_supplied_policy(table, drive, unlimited, 100.0, compare_min_current=False)["claim"]["status"] == "FEASIBLE"


def test_f06_numerical_residual_is_not_a_customer_torque_accuracy(drive, limits):
    from traction_workbench.analysis.supplied_policy import evaluate_supplied_policy
    sc = Scenario("s", 3000.0, 600.0, limits)
    pt = PolicyEvaluator(drive, sc).solve(100.0).point
    table = _single_point_table(drive, (pt.id_A, pt.iq_A), tol=5.0)      # the table claims +-5 N*m
    r = evaluate_supplied_policy(table, drive, sc, 102.0, compare_min_current=False)   # 2 N*m short
    assert r["claim"]["status"] == "UNKNOWN" and r["claim"]["reasons"] == ["REQUIREMENT_INCOMPLETE"]
    assert evaluate_supplied_policy(table, drive, sc, 102.0, compare_min_current=False,
                                    accuracy_Nm=3.0)["claim"]["status"] == "FEASIBLE"
    assert evaluate_supplied_policy(table, drive, sc, 102.0, compare_min_current=False,
                                    accuracy_Nm=1.0)["claim"]["status"] == "INFEASIBLE"


def test_f06_supplied_policy_honours_the_validity_gate(drive, limits):
    from traction_workbench.analysis.supplied_policy import evaluate_supplied_policy
    sc = Scenario("s", 3000.0, 600.0, limits, winding_temp_C=150.0)
    table = _single_point_table(drive, (-50.0, 200.0))
    assert evaluate_supplied_policy(table, drive, sc, 100.0, compare_min_current=False)["claim"]["status"] == "UNKNOWN"


def test_f06_forward_point_without_relevant_dc_limit_is_not_accepted(drive):
    sc = Scenario("s", 3000.0, 600.0, DcSourceLimits(charge_power_max_W=1e5, charge_current_max_A=200.0))
    fr = forward_evaluation(drive, sc, -50.0, 200.0)          # motoring point: discharge limits can bind
    assert fr.point.all_satisfied() and not fr.accepted and fr.gate_messages


# ----------------------------------------------------------------------------------------------
# F07 / F07b - thermal evidence and disconnected feasible sets
# ----------------------------------------------------------------------------------------------

def _tm(validated=True, evidence="TR-9 rev B", validity=(("coolant_temp_C", (60.0, 70.0)),), nodes=None):
    from traction_workbench.extensions.thermal import FosterNetwork, ThermalModel, ThermalNode
    from traction_workbench.models.provenance import DataOrigin, Provenance
    nodes = nodes if nodes is not None else (
        ThermalNode("junction", FosterNetwork((0.05, 0.15), (0.05, 2.0)), 150.0, (("inverter", 1 / 6),)),
        ThermalNode("winding", FosterNetwork((0.004, 0.01), (20.0, 300.0)), 180.0,
                    (("copper", 1.0), ("rotational", 1.0))))
    return ThermalModel("TM", "1", nodes, Provenance(DataOrigin.ESTIMATED, "t", "1", "test"), validated=validated,
                        validity=validity, validation_evidence=evidence)


def test_f07_validated_flag_is_not_evidence(drive):
    from traction_workbench.extensions.thermal import thermal_duration
    sc = scenario(3000, 600, coolant_temp_C=65.0, initial_state="equilibrium_at_coolant")
    assert thermal_duration(drive, sc, _tm(), 300.0, 10.0)["claim"]["status"] == "FEASIBLE"
    r = thermal_duration(drive, sc, _tm(evidence=""), 300.0, 10.0)
    assert r["claim"]["status"] == "UNKNOWN" and any("flag is not evidence" in p for p in r["qualification_problems"])
    r = thermal_duration(drive, sc, _tm(validity=()), 300.0, 10.0)
    assert r["claim"]["status"] == "UNKNOWN" and any("validity domain" in p for p in r["qualification_problems"])


def test_f07_empty_network_is_invalid_input():
    from traction_workbench.errors import InputValidationError
    with pytest.raises(InputValidationError):
        _tm(nodes=())


def test_f07_unmonitored_heat_source_and_initial_state_and_domain(drive):
    from traction_workbench.extensions.thermal import FosterNetwork, ThermalNode, thermal_duration
    only_j = (ThermalNode("junction", FosterNetwork((0.05, 0.15), (0.05, 2.0)), 150.0, (("inverter", 1 / 6),)),)
    sc = scenario(3000, 600, coolant_temp_C=65.0, initial_state="equilibrium_at_coolant")
    r = thermal_duration(drive, sc, _tm(nodes=only_j), 300.0, 10.0)
    assert r["claim"]["status"] == "UNKNOWN" and any("heats no node" in p for p in r["qualification_problems"])
    hot = scenario(3000, 600, coolant_temp_C=65.0, initial_state="after_peak_hot")
    assert thermal_duration(drive, hot, _tm(), 300.0, 10.0)["claim"]["status"] == "UNKNOWN"
    unstated = scenario(3000, 600, coolant_temp_C=65.0)
    assert thermal_duration(drive, unstated, _tm(), 300.0, 10.0)["claim"]["status"] == "UNKNOWN"
    dom = _tm(validity=(("coolant_temp_C", (60.0, 70.0)), ("speed_rpm", (0.0, 2000.0))))
    r = thermal_duration(drive, sc, dom, 300.0, 10.0)            # 3000 rpm is outside the validated speed range
    assert r["claim"]["status"] == "UNKNOWN" and any("speed_rpm" in p for p in r["qualification_problems"])


def test_f07_api_does_not_turn_the_flag_into_supplier_origin():
    from traction_workbench import api
    spec = dict(api.EXAMPLE_THERMAL, validated=True)
    m = api._thermal_model(spec, 65.0)
    assert m.provenance.origin.value == "synthetic" and "WITHOUT evidence" in m.provenance.validation_status


def test_f07b_zero_torque_failure_does_not_remove_the_feasible_set(drive):
    from traction_workbench.extensions.thermal import FosterNetwork, ThermalNode, torque_availability
    lim = DcSourceLimits(200000.0, 100000.0, 400.0, 200.0)
    sc = Scenario("fw", 16000.0, 450.0, lim, coolant_temp_C=65.0, initial_state="equilibrium_at_coolant")
    ev = PolicyEvaluator(drive, sc)
    p0, p5 = ev.solve(0.0).point, ev.solve(-5.0).point
    assert p5.Pcu_W < p0.Pcu_W and p5.Pdc_W > 0          # -5 N*m: less copper loss, braking but drawing DC power
    limit = 65.0 + 0.05 * 0.5 * (p0.Pcu_W + p5.Pcu_W)    # between the two winding temperatures
    node = ThermalNode("winding", FosterNetwork((0.05,), (1.0,)), limit, (("copper", 1.0),))
    ta = torque_availability(drive, sc, _tm(nodes=(node,), evidence=""), durations_s=(math.inf,), direction=-1,
                             samples=81)
    row = ta["rows"][0]
    assert row["zero_torque_feasible"] is False
    assert row["feasible_segments_Nm"], "a failure at zero torque must not remove the feasible set"
    assert any(a <= -5.0 <= b or abs(a + 5.0) < 3.0 for a, b in row["feasible_segments_Nm"])


# ----------------------------------------------------------------------------------------------
# F08 / F08b - input/time/temperature gates; the back-EMF is a rectification risk, not a floor
# ----------------------------------------------------------------------------------------------

def _two_plane_drive():
    p = sf.manufactured_flux_plane()
    cold = FluxMapPlane(p.id_axis_A, p.iq_axis_A, p.psi_d_Wb, p.psi_q_Wb, p.valid, 20.0)
    hot = FluxMapPlane(p.id_axis_A, p.iq_axis_A, p.psi_d_Wb * 0.9, p.psi_q_Wb, p.valid, 120.0)
    return sf.manufactured_map_drive(FluxMapModel((cold, hot)))


def test_f08_negative_reaction_time_is_invalid_input():
    from traction_workbench.errors import InputValidationError
    from traction_workbench.extensions.dclink import regen_disconnect_overvoltage
    with pytest.raises(InputValidationError):
        regen_disconnect_overvoltage(500e-6, 700.0, 100e3, 800.0, reaction_time_s=-1e-3)


def test_f08_delay_then_ramp_energy_is_not_an_immediate_ramp():
    from traction_workbench.extensions.dclink import regen_disconnect_overvoltage
    P, td, tr = 100e3, 0.1e-3, 0.2e-3
    r = regen_disconnect_overvoltage(500e-6, 700.0, P, 800.0, reaction_time_s=td, profile="delay_then_ramp", ramp_s=tr)
    assert r["energy_in_J"] == pytest.approx(P * td + 0.5 * P * tr)
    ramp_only = regen_disconnect_overvoltage(500e-6, 700.0, P, 800.0, reaction_time_s=td + tr, profile="linear_ramp_down")
    assert ramp_only["energy_in_J"] < r["energy_in_J"]          # an immediate ramp overstates the headroom


def test_f08_multi_plane_map_needs_the_magnet_temperature():
    from traction_workbench.extensions.dclink import back_emf_ll_peak
    from traction_workbench.extensions.safe_state import asc_steady_state, safe_state_screening
    dm = _two_plane_drive()
    assert back_emf_ll_peak(dm, 6000.0) is None                  # no silently chosen first plane
    cold, hot = back_emf_ll_peak(dm, 6000.0, 20.0), back_emf_ll_peak(dm, 6000.0, 120.0)
    assert cold == pytest.approx(hot / 0.9)
    a = asc_steady_state(dm, 6000.0, 600.0)                       # used to raise AttributeError
    assert a["evaluable"] is False and a["reason_code"] == "MISSING_INPUT"
    s = safe_state_screening(dm, 6000.0, 600.0)
    assert s["candidates"][1]["steady_state"] == "UNKNOWN"


def test_f08b_back_emf_is_not_a_resistor_independent_floor(drive):
    from traction_workbench.extensions.dclink import active_discharge, rectified_link_voltage_screening
    r = active_discharge(500e-6, 600.0, 60.0, 2.0, drive=drive, speed_rpm=2000.0)
    assert r["claim"]["status"] == "UNKNOWN" and "COUPLED_MODEL_REQUIRED" in r["claim"]["reasons"]
    # the held link voltage depends on R (a smaller R pulls it down): not a floor
    lo = rectified_link_voltage_screening(drive, 2000.0, 10.0)["V_dc_V"]
    hi = rectified_link_voltage_screening(drive, 2000.0, 10000.0)["V_dc_V"]
    assert lo < hi < r["back_emf_ll_peak_V"] * 1.0001
    # the diodes can only charge the link: a too-slow RC design is still proven too slow
    slow = active_discharge(500e-6, 600.0, 60.0, 2.0, R_ohm=5000.0, drive=drive, speed_rpm=2000.0)
    assert slow["claim"]["status"] == "INFEASIBLE"


# ----------------------------------------------------------------------------------------------
# F09 - Kt units and current reference are explicit; garbage is rejected, not read as N*m/A
# ----------------------------------------------------------------------------------------------

def _kt(value, unit, basis="fundamental_peak", ref="line"):
    d = {"value": value, "unit": unit, "definition": "shaft_or_em_torque_per_current_at_id0",
         "torque": "electromagnetic", "current_basis": basis}
    if ref is not None:
        d["current_reference"] = ref
    return {"Kt": d}


def test_f09_kt_units_are_converted_or_rejected():
    from traction_workbench.errors import InputValidationError
    from traction_workbench.units import Conversions, pm_flux_linkage
    si = pm_flux_linkage(_kt(0.6, "N*m/A"), 4, Conversions())
    assert si == pytest.approx(0.6 / 6.0)
    assert pm_flux_linkage(_kt(600.0, "mN*m/A"), 4, Conversions()) == pytest.approx(si)
    assert pm_flux_linkage(_kt(0.0006, "kN*m/A"), 4, Conversions()) == pytest.approx(si)
    for bad in ("", "garbage", "N*m", "Nm/Arms"):
        with pytest.raises(InputValidationError):
            pm_flux_linkage(_kt(0.6, bad), 4, Conversions())
    with pytest.raises(InputValidationError):
        pm_flux_linkage(_kt(0.6, "N*m/A", ref=None), 4, Conversions())       # current reference not declared
    with pytest.raises(InputValidationError):
        pm_flux_linkage(_kt(0.6, "N*m/A", ref="winding_phase"), 4, Conversions(), connection="wye_equivalent")
    assert pm_flux_linkage(_kt(0.6 * math.sqrt(2), "N*m/A", basis="fundamental_rms"), 4, Conversions()) == \
        pytest.approx(si)


# ----------------------------------------------------------------------------------------------
# F10 - node reciprocity is static data plausibility, not dynamic qualification of the interpolant
# ----------------------------------------------------------------------------------------------

def _analytic_flux(d, q):
    return (0.1 + 0.0002 * d - 1e-10 * d ** 3 - 1e-10 * d * q ** 2,
            0.0004 * q - 2e-10 * q ** 3 - 1e-10 * d ** 2 * q)


def test_f10_non_uniform_axes_use_a_true_derivative():
    ax = np.array([-200.0, -101.0, -100.0, -1.0, 0.0, 99.0, 100.0, 200.0])     # alternating 1 A / 99 A spacing
    D, Q = np.meshgrid(ax, ax, indexing="ij")
    psd, psq = _analytic_flux(D, Q)
    pl = FluxMapPlane(ax, ax, psd, psq)
    rep = pl.reciprocity_report()
    assert rep["passed"] is True and rep["max_rel_mismatch"] < 1e-9            # conservative data: no false alarm
    # the plain secant used before is only first order on this grid and raises a false reciprocity failure
    hd = (ax[2:] - ax[:-2])
    ldq = (psd[1:-1, 2:] - psd[1:-1, :-2]) / hd[None, :]
    lqd = (psq[2:, 1:-1] - psq[:-2, 1:-1]) / hd[:, None]
    scale = np.maximum(np.abs((psd[2:, 1:-1] - psd[:-2, 1:-1]) / hd[:, None]),
                       np.abs((psq[1:-1, 2:] - psq[1:-1, :-2]) / hd[None, :]))
    assert np.max(np.abs(ldq - lqd) / scale) > 1e-3


def test_f10_static_plausibility_is_not_dynamic_qualification():
    ax = np.arange(-200.0, 201.0, 20.0)
    D, Q = np.meshgrid(ax, ax, indexing="ij")
    psd, psq = _analytic_flux(D, Q)
    good = FluxMapPlane(ax, ax, psd, psq).magnetic_qualification()
    assert good["static_use"]["status"].startswith("PLAUSIBLE")
    assert good["dynamic_use"]["status"] == "NOT QUALIFIED"
    bad_q = psq + 1e-5 * np.linspace(-1, 1, ax.size)[:, None] ** 3 * 200        # non-conservative data
    bad = FluxMapPlane(ax, ax, psd, bad_q).magnetic_qualification()
    assert bad["static_use"]["status"].startswith("INCONSISTENT")
    assert bad["dynamic_use"]["interpolant"]["max_rel_closed_path_work"] > \
        1e3 * max(good["dynamic_use"]["interpolant"]["max_rel_closed_path_work"], 1e-12)


def test_f10_drive_info_reports_the_split():
    from traction_workbench import service
    info = service.drive_info(sf.manufactured_map_drive())
    q = info["magnetic_qualification"][0]
    assert q["dynamic_use"]["status"] == "NOT QUALIFIED" and "static_use" in q


# ----------------------------------------------------------------------------------------------
# F13 - an UNKNOWN boundary is not a minimal sizing; an unresolved gain is not "not limiting"
# ----------------------------------------------------------------------------------------------

def test_f13_unknown_region_boundary_is_not_a_minimal_sizing(drive, limits):
    from traction_workbench.analysis.sizing import size_parameter
    sc = Scenario("s", 12000.0, 450.0, limits)
    full = size_parameter(drive, sc, 150.0, "Vdc_V", (400.0, 700.0), samples=31)
    assert full.minimal_is_bracketed and 480.0 < full.minimal_feasible < 510.0
    # the loss surrogate is only validated from 520 V: below it the model says nothing (UNKNOWN)
    dl = replace(drive, inverter=replace(drive.inverter, loss=replace(drive.inverter.loss, valid_Vdc_V=(520.0, 700.0))))
    r = size_parameter(dl, sc, 150.0, "Vdc_V", (400.0, 700.0), samples=31)
    assert r.minimal_feasible == pytest.approx(520.0) and not r.minimal_is_bracketed
    assert [st for *_, st in r.regions] == ["UNKNOWN", "FEASIBLE"]
    d = r.to_dict()
    assert "not a proven minimum" in d["minimal_meaning"]


def test_f13_unresolved_gain_is_not_classified_not_limiting(drive, limits):
    from traction_workbench.analysis.dominance import capability_dominance
    sc = Scenario("hot", 12000.0, 600.0, limits, winding_temp_C=150.0)     # validity gate fails: nothing established
    r = capability_dominance(drive, sc, +1, samples=21)
    cls = {dict(x)["constraint"]: dict(x)["classification"] for x in r.rows}
    assert cls and all(c.startswith("unresolved") for c in cls.values())
    ok = capability_dominance(drive, Scenario("ok", 12000.0, 600.0, limits), +1, samples=21)
    row = {dict(x)["constraint"]: dict(x) for x in ok.rows}["VOLTAGE"]
    lo, hi = row["gain_interval_Nm"]
    assert row["classification"] == "limiting" and lo > 0 and lo <= row["gain_Nm"] <= hi


# ----------------------------------------------------------------------------------------------
# F11 / F12 - mathematical / model / requirement / qualification layers are separate statements
# ----------------------------------------------------------------------------------------------

def test_f12_claim_layers_are_separate(drive, limits):
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.models.provenance import DataOrigin, Provenance
    from traction_workbench.requirement import Requirement
    req = Requirement("R-L", "150 N*m @ 12000 rpm, 600 V", 150.0, 12000.0, 600.0)
    rec = evaluate_requirement(req, drive, source_limits=limits)
    lay = rec.to_dict()["verdict"]["layers"]
    assert rec.verdict.status is Status.FEASIBLE
    assert lay["model"]["verdict"] == "PASS" and lay["mathematical"]["status"] == "CERTIFIED"
    assert lay["qualification"]["status"].startswith("NOT QUALIFIED")       # synthetic data
    assert any("duration not stated" in x for x in lay["requirement"]["open_items"])
    assert any("a0 + a2*Ipk^2" in x for x in lay["qualification"]["sub_models"])
    sup = replace(drive, provenance=Provenance(DataOrigin.SUPPLIER, "sheet", "B", "supplier-declared"))
    lay2 = evaluate_requirement(req, sup, source_limits=limits).layers
    assert lay2["qualification"]["status"].startswith("DATA-DECLARED")      # never promoted to "qualified"
    rng = Requirement("R-R", "range (regen)", -50.0, 12000.0, (550.0, 650.0), Vdc_quantifier="for_all")
    assert evaluate_requirement(rng, drive, source_limits=limits).layers["mathematical"]["status"] == \
        "SAMPLED_OR_BOUNDED"                                    # no monotonicity certificate for regen: sampled
    mot = Requirement("R-M", "range (motoring)", 100.0, 12000.0, (550.0, 650.0), Vdc_quantifier="for_all")
    assert evaluate_requirement(mot, drive, source_limits=limits).layers["mathematical"]["status"] == "CERTIFIED"


# ----------------------------------------------------------------------------------------------
# additional boundary cases requested in the P0-A acceptance (negative speed, zero torque, equal limits)
# ----------------------------------------------------------------------------------------------

def _sym_speed(drive):
    return replace(drive, domain=replace(drive.domain, speed_rpm=(-16000.0, 16000.0)))


def test_negative_speed_uses_power_sign_not_torque_sign(drive, limits):
    d = _sym_speed(drive)
    sol = PolicyEvaluator(d, Scenario("rev", -3000.0, 600.0, limits)).solve(-100.0)
    assert sol.policy_claim.status is Status.FEASIBLE
    pt = sol.point
    assert pt.Pshaft_W > 0 and pt.Pdc_W > 0 and pt.energy_mode == "MOTORING"   # negative torque, reverse motoring
    reg = PolicyEvaluator(d, Scenario("rev", -3000.0, 600.0, limits)).solve(100.0).point
    assert reg.Pshaft_W < 0 and reg.energy_mode in ("REGENERATING", "BRAKING_WITHOUT_NET_DC_RECOVERY")


def test_zero_torque_and_equal_limits_are_explicit(drive, limits):
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    z = PolicyEvaluator(drive, Scenario("z", 3000.0, 600.0, limits)).solve(0.0)
    assert z.policy_claim.status is Status.FEASIBLE and z.point.Tshaft_Nm == pytest.approx(0.0, abs=1e-6)
    eq = Requirement("R-EQ", "equal range ends", 100.0, 3000.0, (600.0, 600.0), Vdc_quantifier="for_all")
    rec = evaluate_requirement(eq, drive, source_limits=limits)
    assert len(rec.conditions) == 1 and rec.verdict.status is Status.FEASIBLE     # [600, 600] is one point
    zero_dc = Scenario("z", 3000.0, 600.0, DcSourceLimits(0.0, 0.0, 0.0, 0.0))
    s = PolicyEvaluator(drive, zero_dc).solve(50.0)                              # no DC power allowed at all
    assert s.policy_claim.status is Status.INFEASIBLE
    pinned = replace(drive, domain=replace(drive.domain, id_A=(-50.0, -50.0)))  # id pinned to one value
    p = PolicyEvaluator(pinned, Scenario("p", 3000.0, 600.0, limits)).solve(100.0)
    assert p.policy_claim.status in (Status.FEASIBLE, Status.INFEASIBLE)
    if p.point is not None:
        assert p.point.id_A == pytest.approx(-50.0, abs=1e-9)


# --------------------------------------------------------------------------- audit evidence (solver memo F06)

def test_missing_rotational_loss_keeps_physical_claim_open():
    """Solver-audit F06: an exact empty curve traced with zero assumed drag must not prove the shaft request
    infeasible; all claims stay consistent (UNKNOWN)."""
    from dataclasses import replace

    from traction_workbench.solvers.policy import solve_policy
    from traction_workbench.spec_fixtures import synthetic_drive, synthetic_scenario
    base = synthetic_drive()
    missing = replace(base, motor=replace(base.motor, rotational_loss=None))
    s = solve_policy(missing, synthetic_scenario(3000, 600), -1000.0)
    st = {c.name: c.status.value for c in s.claims}
    assert st["electrical_existence"] == "UNKNOWN"
    assert st["physical_existence_with_dc"] == "UNKNOWN"
    assert "INFEASIBLE" not in st.values()
    # with the rotational-loss model the same exclusion is proven
    full = solve_policy(base, synthetic_scenario(3000, 600), -1000.0)
    assert {c.name: c.status.value for c in full.claims}["physical_existence_with_dc"] == "INFEASIBLE"


def test_loss_independent_shaft_power_screen_survives_missing_data():
    """The shaft-power screen needs no loss data (losses are passive), so it may exclude even when the
    rotational-loss model is missing."""
    from dataclasses import replace

    from traction_workbench.scenario import DcSourceLimits
    from traction_workbench.solvers.policy import solve_policy
    from traction_workbench.spec_fixtures import synthetic_drive, synthetic_scenario
    base = synthetic_drive()
    missing = replace(base, motor=replace(base.motor, rotational_loss=None))
    lim = DcSourceLimits(discharge_power_max_W=50e3, charge_power_max_W=50e3)
    s = solve_policy(missing, synthetic_scenario(6000, 600, source_limits=lim), 150.0)   # 94 kW shaft > 50 kW
    phys = {c.name: c for c in s.claims}["physical_existence_with_dc"]
    assert phys.status.value == "INFEASIBLE"
    assert "NECESSARY_CONDITION_VIOLATED" in [r.value for r in phys.reasons]


# --------------------------------------------------------------------------- audit evidence (decision memo DV-03)

def _hold_limits():
    from traction_workbench.spec_fixtures import synthetic_limits
    return synthetic_limits()


def test_fixed_calibration_row_uses_the_witness_gate():
    """DV-03: the fixed-calibration uncertainty row may not pass with undeclared DC limits or missing loss data."""
    from dataclasses import replace

    from traction_workbench.analysis.uncertainty import ParameterInterval, bounded_input_analysis
    from traction_workbench.scenario import DcSourceLimits
    from traction_workbench.spec_fixtures import synthetic_drive, synthetic_scenario
    d = synthetic_drive()
    s = synthetic_scenario(3000, 600)
    iv = [ParameterInterval("Vdc_V", 600, 600)]
    no_lim = bounded_input_analysis(d, replace(s, source_limits=DcSourceLimits()), 100, iv)
    assert {r["fixed_calibration_status"] for r in no_lim["combinations"]} == {"UNKNOWN"}
    no_loss = bounded_input_analysis(replace(d, inverter=replace(d.inverter, loss=None)), s, 100, iv)
    assert {r["fixed_calibration_status"] for r in no_loss["combinations"]} == {"UNKNOWN"}
    ok = bounded_input_analysis(d, s, 100, iv)
    assert {r["fixed_calibration_status"] for r in ok["combinations"]} == {"FEASIBLE"}


def test_fixed_calibration_torque_error_needs_a_stated_accuracy():
    """A torque error from a parameter change is judged against the stated accuracy, not the solver residual."""
    from traction_workbench.analysis.uncertainty import ParameterInterval, bounded_input_analysis
    from traction_workbench.spec_fixtures import synthetic_drive, synthetic_scenario
    d = synthetic_drive()
    s = synthetic_scenario(3000, 600)
    iv = [ParameterInterval("psi_pm_Wb", 0.095, 0.105, basis="test tolerance")]
    none = bounded_input_analysis(d, s, 100, iv)
    fixed = {c["name"]: c for c in none["claims"]}["robust_fixed_calibration"]
    assert fixed["status"] == "UNKNOWN"                       # not a counterexample without an accuracy requirement
    assert any("REQUIREMENT_INCOMPLETE" in " ".join(r["fixed_calibration_notes"]) for r in none["combinations"])
    wide = bounded_input_analysis(d, s, 100, iv, torque_accuracy_Nm=10.0)
    assert {r["fixed_calibration_status"] for r in wide["combinations"]} == {"FEASIBLE"}
    tight = bounded_input_analysis(d, s, 100, iv, torque_accuracy_Nm=0.5)
    assert {c["name"]: c for c in tight["claims"]}["robust_fixed_calibration"]["status"] == "INFEASIBLE"


# --------------------------------------------------------------------------- audit evidence (decision memo DV-05)

def test_synthetic_or_unvalidated_envelope_is_a_model_experiment_not_a_rating():
    from traction_workbench.analysis.rating import RatingEnvelope, duration_claim
    from traction_workbench.models.provenance import DataOrigin, Provenance

    from traction_workbench.analysis.rating import ApprovalState, RatingApproval

    def env(origin, status, kind="supplier_rated"):
        # R2 D-R2-01: approval is typed; the free-text status only describes.  The 'released' fixtures carry an
        # explicit approval with its evidence identity, the others none.
        appr = (RatingApproval(ApprovalState.APPROVED, "RS-AUDIT-0", "0", "10 s rating (audit fixture)")
                if "released" in status else None)
        return RatingEnvelope("E10", "0", 10.0, (0.0, 12000.0), (200.0, 200.0),
                              Provenance(origin, "audit example", "0", status), evidence_kind=kind, approval=appr)
    syn = duration_claim((env(DataOrigin.SYNTHETIC, "UNVALIDATED"),), 10, 12000, 150, {})
    assert syn.status is Status.UNKNOWN and "model experiment" in syn.detail
    assert all(e.kind.value != "supplier_rated_envelope" for e in syn.evidence)
    unval = duration_claim((env(DataOrigin.SUPPLIER, "UNVALIDATED draft"),), 10, 12000, 150, {})
    assert unval.status is Status.UNKNOWN
    ok = duration_claim((env(DataOrigin.SUPPLIER, "supplier-rated, released rev 0"),), 10, 12000, 150, {})
    assert ok.status is Status.FEASIBLE and ok.evidence[0].kind.value == "supplier_rated_envelope"
    # a synthetic envelope cannot out-vote or create a conflict with an approved one either
    both = duration_claim((env(DataOrigin.SYNTHETIC, "UNVALIDATED"),
                           env(DataOrigin.SUPPLIER, "supplier-rated, released rev 0")), 10, 12000, 150, {})
    assert both.status is Status.FEASIBLE


def test_unsplittable_fdti_frti_budgets_are_reported_not_dropped():
    """System-audit repro: a composite item spanning the detection event; the declared FDTI/FRTI budgets
    cannot be verified and must be reported as such (ok=None)."""
    from traction_workbench.extensions.timing import TimingChain, TimingItem, analyze_timing
    chain = TimingChain("split", "f", .050, ("f", "d", "s"), (TimingItem("whole", "f", "s", "SYS", .020),),
                        detection_event="d", fdti_budget_s=.001, frti_budget_s=.001)
    r = analyze_timing(chain)
    checks = {c["budget"]: c for c in r["budget_checks"]}
    assert checks["FDTI"]["ok"] is None and checks["FRTI"]["ok"] is None
    assert "spans the detection event" in checks["FDTI"]["note"]


def test_conservative_nodes_pass_but_interpolant_asymmetry_is_reported():
    """Additional-repro F10: exact conservative nodal data pass the node check on uniform and non-uniform axes,
    while the bilinear interpolant's off-grid L_dq != L_qd is reported (exact corner maximum) and dynamic use stays
    NOT QUALIFIED."""
    import numpy as np

    from traction_workbench.models.flux import FluxMapPlane
    a = 1e-5

    def plane(daxis, qaxis):
        da, qa = np.array(daxis, float), np.array(qaxis, float)
        D, Q = np.meshgrid(da, qa, indexing="ij")
        return FluxMapPlane(da, qa, .1 + .001 * D + 2 * a * D * Q ** 2, .002 * Q + 2 * a * D ** 2 * Q, label="audit")
    uni, non = plane([0, 1, 2], [0, 1, 2]), plane([0, 1, 3], [0, 1, 2])
    assert uni.reciprocity_report()["passed"] and non.reciprocity_report()["passed"]
    ic = uni.interpolant_consistency()
    assert ic["max_interior_asymmetry_H"] > 9e-6          # the audit's off-grid mismatch at (0.25, 0.75) A
    assert ic["max_rel_interior_asymmetry"] > 1e-3
    q = uni.magnetic_qualification()
    assert q["dynamic_use"]["status"] == "NOT QUALIFIED"
    assert any("asymmetry" in r for r in q["dynamic_use"]["reasons"])
