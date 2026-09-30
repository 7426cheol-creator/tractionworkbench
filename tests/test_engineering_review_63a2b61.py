"""Independent engineering review of 63a2b61 (ENGINEERING_REVIEW_inv_app + the DC-source/DC-link and thermal
sub-reports): every finding reproduced with the reviewer's scripts, then fixed or answered with a better method.

Numbers are the reviewer's counterexamples on the synthetic fixture; each test pins the corrected behaviour."""

import json
import math
import sys
from pathlib import Path

import pytest

from traction_workbench import api
from traction_workbench import spec_fixtures as sf
from traction_workbench.analysis.source import TheveninSource, resolve_terminal_voltage
from traction_workbench.scenario import DcSourceLimits, Scenario
from traction_workbench.solvers.policy import PolicyEvaluator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verification"))

RELAXED = {"discharge_power_max_W": 250000.0, "charge_power_max_W": 300000.0, "discharge_current_max_A": 500.0,
           "charge_current_max_A": 500.0}


def _regen_edge_torque():
    """The reviewer's F-04 torque: the electrical existence edge lies between 600 and 601 V at 12000 rpm."""
    d = sf.synthetic_drive()
    lim = DcSourceLimits(source="relaxed", **RELAXED)
    lo, hi = -50.0, -400.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if PolicyEvaluator(d, Scenario("s", 12000.0, 601.0, lim)).solve(mid).point is not None:
            lo = mid
        else:
            hi = mid
    return round(lo, 2)


def _ocv_case(T, R_mohm, limits=None, **src):
    return api.evaluate({"requirement": {"id": "R", "text": "t", "torque_Nm": T, "speed_rpm": 12000.0, "Vdc_V": 600.0,
                                         "Vdc_port": "battery_ocv"},
                         "source_model": {"kind": "thevenin", "R_eq_mohm": R_mohm, "basis": "test", **src},
                         **({"limits": limits} if limits else {})})


# ---------------------------------------------------------------- F-04 / F-14 / D-F3: Thevenin terminal voltage


def test_f04_regeneration_is_resolved_above_the_ocv_where_the_point_exists():
    T = _regen_edge_torque()
    d = sf.synthetic_drive()
    lim = DcSourceLimits(source="relaxed", **RELAXED)
    assert PolicyEvaluator(d, Scenario("s", 12000.0, 600.0, lim)).solve(T).point is None      # none at the OCV
    for R, vt in ((10, 604.04), (30, 611.99)):
        r = _ocv_case(T, R, RELAXED)
        sc = r["source_coupling"]
        assert sc["status"] == "RESOLVED" and sc["V_terminal_V"][0] == pytest.approx(vt, abs=0.01)
        assert r["verdict"]["verdict"] == "PASS"          # was FAIL "no electrical solution at 600 V"
        p = sc["points"][0]
        assert p["V_terminal_max_V"] > 600.0 and p["monotone"] and p["history_V"][0] == p["V_terminal_max_V"]
        assert all(b <= a + 1e-9 for a, b in zip(p["history_V"], p["history_V"][1:]))     # monotone from above


def test_f04_with_the_fixture_limits_the_fail_names_the_charge_limit_at_the_terminal_voltage():
    r = _ocv_case(_regen_edge_torque(), 10)
    assert r["source_coupling"]["status"] == "RESOLVED" and r["verdict"]["verdict"] == "FAIL"
    text = json.dumps(r["conditions"])
    assert "DC_CHARGE" in text and "no electrical solution" not in text


@pytest.mark.parametrize("R_mohm, first", [(350, True), (300, False)])
def test_f14_a_motoring_request_the_source_cannot_carry_is_a_proven_fail(R_mohm, first):
    """350 mOhm: V_hi = 455 V lies below the electrical edge 479.6 V (no point anywhere).  300 mOhm: V_hi = 482.9 V
    has a point, but its P_dc sends the terminal voltage to 473 V, below the edge - with the I^2-form loss the
    downward iteration never passes the largest fixed point, so none exists (was UNKNOWN NUMERICAL_UNRESOLVED)."""
    r = _ocv_case(150.0, R_mohm)
    sc = r["source_coupling"]
    assert sc["status"] == "NO_SOLUTION" and r["verdict"]["verdict"] == "FAIL"
    assert "NECESSARY_CONDITION_VIOLATED" in r["verdict"]["reasons"]
    p = sc["points"][0]
    assert ("at V_hi" in p["reason"]) is first and "no operating point exists on this source" in p["reason"]
    # shown where an operating point could be, never at the OCV
    assert r["conditions"][0]["scenario"]["Vdc_V_inverter_dc_terminal"] == pytest.approx(p["V_terminal_max_V"])


def test_without_the_i2_form_leaving_the_existence_set_proves_nothing():
    from make_module_anchor import module_drive
    md, _ = module_drive()
    lim = sf.synthetic_limits()
    r = resolve_terminal_voltage(md, Scenario("s", 12000.0, 600.0, lim), TheveninSource(0.03, "t"), 150.0)
    assert r["status"] == "NOT_RESOLVED" and r["monotone"] is False
    assert "switching test voltage" in r["reason"]            # the loss model says why P_dc is unknown
    # electrical absence at V_hi is a proof for any loss model
    r = resolve_terminal_voltage(md, Scenario("s", 12000.0, 600.0, lim), TheveninSource(0.35, "t"), 150.0)
    assert r["status"] == "NO_SOLUTION"


def test_an_unresolved_source_never_turns_a_drive_side_violation_into_a_fail():
    """R_eq characterised only for |I_dc| <= 100 A: the resolved point draws -404 A, so the terminal voltage is not
    established - the charge-limit violation at the shown voltage is not decisive (was FAIL, judged at the OCV)."""
    r = _ocv_case(_regen_edge_torque(), 10, valid_current_A=[-100, 100])
    assert r["source_coupling"]["status"] == "NOT_RESOLVED"
    assert r["verdict"]["verdict"] == "UNKNOWN" and "COUPLED_MODEL_REQUIRED" in r["verdict"]["reasons"]


def test_the_iteration_records_its_history_and_conditioning():
    d, lim = sf.synthetic_drive(), sf.synthetic_limits()
    r = resolve_terminal_voltage(d, Scenario("s", 12000.0, 600.0, lim), TheveninSource(0.1, "t"), 150.0)
    assert r["status"] == "RESOLVED" and r["iterations"] == len(r["history_V"]) - 1 >= 2
    assert r["contraction"] is not None and 0.0 <= r["contraction"] < 0.2          # |f'| ~ 0.05 on this drive
    assert "largest fixed point" in r["fixed_point"]
    slow = resolve_terminal_voltage(d, Scenario("s", 12000.0, 600.0, lim), TheveninSource(0.1, "t"), 150.0, max_iter=1)
    assert slow["status"] == "NOT_RESOLVED" and "did not converge" in slow["reason"]
    ideal = resolve_terminal_voltage(d, Scenario("s", 12000.0, 450.0, lim), TheveninSource(0.0, "t"), 150.0)
    assert ideal["status"] == "RESOLVED" and ideal["V_terminal_V"] == 450.0 and ideal["I_dc_A"] is None


# ---------------------------------------------------------------- F-01: D2 policy claim certified by consequence


@pytest.mark.parametrize("n, T", [(3000.0, 5.0), (3000.0, 35.0), (3000.0, 55.0), (8000.0, 25.0), (8000.0, 75.0)])
def test_f01_an_uncertified_map_point_is_decided_by_its_consequence(n, T):
    """The cell-bound gap (~0.1 A at depth 7) left the policy claim UNKNOWN below ~100 A although every point of the
    bracket [lower bound, found] meets every limit: the decision is certified, the location is not."""
    d, lim = sf.manufactured_map_drive(), sf.synthetic_limits()
    ev = PolicyEvaluator(d, Scenario("s", n, 600.0, lim))
    sol = ev.solve(T)
    mc = dict(sol.certificates)["minimum_current"]
    assert mc["certified"] is False and 0.0 < mc["gap_A"] < 0.2          # the location stays uncertified
    assert mc["decided_by_consequence"] is True
    assert sol.policy_claim.status.value == "FEASIBLE"                   # was UNKNOWN (NUMERICAL_UNRESOLVED)
    assert any("certified by consequence" in q for q in sol.policy_claim.qualifiers)
    assert ev.quick_status(T)[0] == "FEASIBLE"


def test_f01_the_status_is_monotone_in_torque_and_the_capability_scan_has_no_unknown_swath():
    d, lim = sf.manufactured_map_drive(), sf.synthetic_limits()
    ev = PolicyEvaluator(d, Scenario("s", 8000.0, 600.0, lim))
    assert [ev.solve(T).policy_claim.status.value for T in (65.0, 75.0, 85.0)] == ["FEASIBLE"] * 3
    from traction_workbench.solvers.capability import policy_capability
    cap = policy_capability(PolicyEvaluator(d, Scenario("s", 3000.0, 600.0, lim)), 1)
    assert not any("scan samples UNKNOWN" in n for n in cap.notes)      # was "79 of 161 scan samples UNKNOWN"


def test_f01_a_bracket_that_straddles_a_dc_limit_stays_unknown():
    d = sf.manufactured_map_drive()
    base = sf.synthetic_limits()
    ev = PolicyEvaluator(d, Scenario("s", 3000.0, 600.0, base))
    sol = ev.solve(35.0)
    mc = dict(sol.certificates)["minimum_current"]
    k = ev.k
    tem = 35.0 + k.tau_rot_or_zero
    p_lo = k.i2_dc.pdc(tem, k.omega_m, mc["lower_bound_I_A"] ** 2)
    p_hi = k.i2_dc.pdc(tem, k.omega_m, mc["found_I_A"] ** 2)
    from dataclasses import replace
    lim = replace(base, discharge_power_max_W=0.5 * (p_lo + p_hi))       # the limit lies inside the bracket
    s2 = PolicyEvaluator(d, Scenario("s", 3000.0, 600.0, lim)).solve(35.0)
    assert s2.policy_claim.status.value == "UNKNOWN"
    assert dict(s2.certificates)["minimum_current"]["decided_by_consequence"] is False


# ---------------------------------------------------------------- F-02: unstated temperature on a temperature-law drive


def _law_drive(rs=True, psi=True):
    import dataclasses as dc

    from traction_workbench.models.components import TemperatureDependence
    base = sf.synthetic_drive()
    kw = {}
    if rs:
        kw.update(reference_winding_temp_C=20.0, rs_temperature=TemperatureDependence(0.00393, (-40, 200), "copper"))
    if psi:
        kw.update(reference_magnet_temp_C=20.0,
                  psi_temperature=TemperatureDependence(-0.0011, (-40, 180), "NdFeB Br ~ -0.11 %/K"))
    return dc.replace(base, motor=dc.replace(base.motor, **kw))


def _decide(drive, T, n, **req):
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    return evaluate_requirement(Requirement("R", "t", T, n, 600.0, **req), drive, source_limits=sf.synthetic_limits())


def test_f02_a_request_that_fails_hot_is_no_longer_a_pass_at_the_reference_temperature():
    """480 N*m at 3000 rpm: 498.8 N*m capability at the 20 degC reference, 459 at 150/140 degC - the record said PASS."""
    rec = _decide(_law_drive(), 480.0, 3000.0)
    st = {(c.scenario.winding_temp_C, c.scenario.magnet_temp_C): c.requirement_claim.status.value
          for c in rec.conditions}
    assert st == {(200.0, -40.0): "FEASIBLE", (200.0, 20.0): "FEASIBLE", (200.0, 180.0): "INFEASIBLE"}
    assert rec.verdict.status.value == "INFEASIBLE"
    assert any("counterexample" in q and "magnet 180 degC" in q for q in rec.qualifiers)
    lay = rec.layers["requirement"]
    assert any("winding temperature not stated" in x for x in lay["open_items"])
    assert lay["quantifiers"]["winding"].startswith("for all") and "worst end 200" in lay["quantifiers"]["winding"]
    assert any("state the winding / magnet temperature" in a for a in rec.next_actions)


def test_f02_motoring_is_certified_from_the_largest_rs_and_regen_stays_sampled():
    d = _law_drive(psi=False)
    mot = _decide(d, 150.0, 12000.0)
    assert [c.scenario.winding_temp_C for c in mot.conditions] == [200.0]     # the worst end certifies the range
    assert mot.verdict.status.value == "FEASIBLE" and any("(4/3) P_ac" in q for q in mot.qualifiers)
    gen = _decide(d, -80.0, 12000.0)
    assert [c.scenario.winding_temp_C for c in gen.conditions] == [-40.0, 20.0, 200.0]
    assert gen.verdict.status.value == "UNKNOWN" and "SAMPLED_COVERAGE" in [r.value for r in gen.verdict.reasons]


def test_f02_the_worst_end_certificate_holds_numerically():
    """d|v|^2/dRs = (4/3) P_ac at a fixed point, and the motoring capability falls monotonically with Rs."""
    import numpy as np

    from traction_workbench.physics import DriveKernel, evaluate_point
    d = _law_drive(psi=False)
    lim = sf.synthetic_limits()
    for (i_d, i_q) in ((-300.0, 200.0), (-100.0, 400.0), (-450.0, 50.0)):
        k0 = DriveKernel(d, Scenario("s", 9000.0, 600.0, lim, winding_temp_C=100.0))
        k1 = DriveKernel(d, Scenario("s", 9000.0, 600.0, lim, winding_temp_C=100.1))
        p0, p1 = evaluate_point(k0, i_d, i_q), evaluate_point(k1, i_d, i_q)
        dv2 = (p1.v_peak_V ** 2 - p0.v_peak_V ** 2) / (k1.Rs - k0.Rs)
        assert dv2 == pytest.approx(4.0 / 3.0 * p0.Pac_W, rel=2e-3)
    from traction_workbench.solvers.capability import policy_capability
    caps = [policy_capability(PolicyEvaluator(d, Scenario("s", 3000.0, 600.0, lim, winding_temp_C=t)), 1).value_Nm
            for t in (-40.0, 20.0, 120.0, 200.0)]
    assert all(np.diff(caps) <= 1e-6)


def test_f02_a_stated_temperature_is_judged_at_that_temperature_only():
    rec = _decide(_law_drive(), 440.0, 3000.0, winding_temp_C=120.0, magnet_temp_C=100.0)
    assert len(rec.conditions) == 1 and rec.verdict.status.value == "FEASIBLE"
    assert rec.layers["requirement"]["quantifiers"]["winding"] == "at 120 degC (stated)"


# ---------------------------------------------------------------- F-03: a loss-limited margin states its loss budget


def test_f03_the_flagship_margin_is_stated_as_a_loss_budget_and_break_even_changes():
    from dataclasses import replace

    from traction_workbench.solvers.capability import policy_capability
    r = api.evaluate({"requirement": {"id": "R", "text": "t", "torque_Nm": 150.0, "speed_rpm": 12000.0, "Vdc_V": 600.0}})
    assert r["verdict"]["verdict"] == "PASS"                          # the verdict is unchanged
    ms = r["margin_sensitivity"]
    assert ms["margin_Nm"] == pytest.approx(2.555, abs=1e-3) and "DC_DISCHARGE_POWER" in ms["active"]
    assert ms["loss_budget_W"] == pytest.approx(3368.6, abs=1.0)      # P_max - P_dc at the witness
    brk = {p["key"]: p["break_even"] for p in ms["parameters"]}
    assert 0.6 < brk["Rs"] < 1.2 and 0.8 < brk["b"] < 1.3 and brk["psi"] is None
    assert any(q.startswith("loss-limited margin 2.56 N*m") and "3.37 kW" in q for q in r["verdict"]["qualifiers"])
    # the linear break-even is a usable estimate: Rs raised by it leaves the capability close to the request
    d, lim = sf.synthetic_drive(), sf.synthetic_limits()
    d2 = replace(d, motor=replace(d.motor, Rs_ohm=d.motor.Rs_ohm * (1 + brk["Rs"])))
    cap = policy_capability(PolicyEvaluator(d2, Scenario("s", 12000.0, 600.0, lim)), 1).value_Nm
    assert cap == pytest.approx(150.0, abs=0.5)


def test_f03_a_current_limited_margin_carries_no_loss_sensitivity():
    r = api.evaluate({"requirement": {"id": "R", "text": "t", "torque_Nm": 300.0, "speed_rpm": 3000.0, "Vdc_V": 600.0}})
    assert r["verdict"]["verdict"] == "PASS" and "margin_sensitivity" not in r


def test_f03_the_markdown_record_has_the_sensitivity_table():
    from traction_workbench import service as S
    from traction_workbench.api import case_from_body
    rec, _ = S.evaluate_decision(case_from_body(
        {"requirement": {"id": "R", "text": "t", "torque_Nm": 150.0, "speed_rpm": 12000.0, "Vdc_V": 600.0}}))
    md = rec.to_markdown()
    assert "## Loss-limited margin (automatic sensitivity; the verdict is unchanged)" in md
    assert "| Rs | 0.015 ohm |" in md and "Loss budget: 3.37 kW" in md


# ---------------------------------------------------------------- thermal: F-05 .. F-08, F-11, F-13 (+ sub-report)


def _thermal_fixture(validated=True, rot_monitored=True, winding_limit=None, uncertainty=0.0):
    wind = dict(api.EXAMPLE_THERMAL["nodes"][1],
                loss_share={"copper": 1.0, "rotational": 1.0} if rot_monitored else {"copper": 1.0})
    if winding_limit is not None:
        wind["limit_C"] = winding_limit
    spec = {**api.EXAMPLE_THERMAL, "nodes": [api.EXAMPLE_THERMAL["nodes"][0], wind]}
    if validated:
        spec.update(validated=True, validation_evidence="TR-1 rev A",
                    validity={"coolant_temp_C": [60, 70], "coolant_flow_L_per_min": [8, 12]})
    if uncertainty:
        spec.update(uncertainty_K=uncertainty, uncertainty_basis="validation residual (test)")
    return api.thermal_model_from_dict(spec, 65.0)


def _law_drive_api():
    from dataclasses import replace

    from traction_workbench.models.components import TemperatureDependence
    d = api._drive({})
    law = TemperatureDependence(0.00393, (-40.0, 250.0), "copper coefficient (test)")
    return replace(d, motor=replace(d.motor, rs_temperature=law, reference_winding_temp_C=20.0))


W_NODE = "stator winding (hot spot)"


@pytest.mark.parametrize("dur", [600.0, math.inf])
def test_f05_a_declared_rs_law_makes_the_duration_claim_self_consistent(dur):
    """400 N*m at 3000 rpm: FEASIBLE with the winding at 158.5 degC and the copper loss at 20 degC before; the
    coupled path reaches 180 degC at ~242 s and settles at 230 degC."""
    from traction_workbench.extensions.thermal import thermal_duration
    from traction_workbench.extensions.thermal_cycle import Feedback, LoadPhase, repeated_load
    d = _law_drive_api()
    sc = Scenario("x", 3000.0, 600.0, api._limits({}), coolant_temp_C=65.0, initial_state="equilibrium_at_coolant")
    Q = _thermal_fixture()
    r = thermal_duration(d, sc, Q, 400.0, dur)
    assert r["claim"]["status"] == "INFEASIBLE" and "coupled" in r["claim"]["scope"]
    wind = next(n for n in r["nodes"] if n["node"] == W_NODE)
    assert wind["steady_state_C"] == pytest.approx(230.2, abs=0.5)
    ref = repeated_load(d, sc, Q, [LoadPhase(400.0, 3000.0, 600.0)], cycles=1, feedback=Feedback(True), allowed=False)
    assert r["time_to_first_limit_s"] == pytest.approx(ref["first_limit"]["t_s"], rel=0.005)   # same physics
    assert 230.0 < r["time_to_first_limit_s"] < 255.0


def test_f05_availability_with_the_law_is_bounded_at_the_hot_corner():
    from traction_workbench.extensions.thermal import torque_availability
    from traction_workbench.extensions.thermal_cycle import Feedback, LoadPhase, repeated_load
    d = _law_drive_api()
    sc = Scenario("x", 3000.0, 600.0, api._limits({}), coolant_temp_C=65.0, initial_state="equilibrium_at_coolant")
    Q = _thermal_fixture()
    av = torque_availability(d, sc, Q, (10.0, math.inf))
    cont = next(r for r in av["rows"] if math.isinf(r["duration_s"]))
    assert av["feedback_bound"] is not None and cont["torque_Nm"] < 345.0        # was 426 N*m, open loop
    # the bound is tight here: at that torque the self-consistent winding steady state sits at the limit
    r = repeated_load(d, sc, Q, [LoadPhase(cont["torque_Nm"], 3000.0, 1.0e4)], cycles=1, feedback=Feedback(True),
                      allowed=False, resolution_check=False)
    assert r["periodic"]["peak_C"][W_NODE] <= 180.0 + 0.05


def test_f06_the_requested_horizon_is_never_truncated():
    """500 cycles x 0.02 s, winding limit 90.933 degC: the run stopped after 401 cycles (0.02 K per cycle) and said
    FEASIBLE; the limit is crossed in cycle 449."""
    from traction_workbench.extensions.thermal_cycle import Feedback, LoadPhase, repeated_load
    sc = Scenario("cyc", 3000.0, 600.0, api._limits({}), coolant_temp_C=65.0)
    r = repeated_load(api._drive({}), sc, _thermal_fixture(winding_limit=90.933),
                      [LoadPhase(440.0, 3000.0, 0.01), LoadPhase(300.0, 3000.0, 0.01)], cycles=500,
                      feedback=Feedback(False), allowed=False, resolution_check=False)
    assert r["claim"]["status"] == "INFEASIBLE" and r["first_limit"]["cycle"] == 449
    assert r["claim"]["time_horizon"] == "500 x 0.02 s duty cycle"


def test_f06_a_periodic_exceedance_is_part_of_the_claim():
    from traction_workbench.extensions.thermal_cycle import Feedback, LoadPhase, repeated_load
    sc = Scenario("cyc", 3000.0, 600.0, api._limits({}), coolant_temp_C=65.0)
    r = repeated_load(api._drive({}), sc, _thermal_fixture(winding_limit=150.0),
                      [LoadPhase(420.0, 3000.0, 5.0), LoadPhase(340.0, 3000.0, 5.0)], cycles=20,
                      feedback=Feedback(False), allowed=False, resolution_check=False)
    assert r["claim"]["status"] == "FEASIBLE" and r["periodic"]["exceeds"]
    assert any("repeated indefinitely" in q and "20 cycle(s) only" in q for q in r["claim"]["qualifiers"])


def test_f06_a_run_that_reaches_the_periodic_state_repeats_it_for_the_rest_of_the_horizon():
    from traction_workbench.extensions.thermal_cycle import Feedback, LoadPhase, repeated_load
    sc = Scenario("cyc", 3000.0, 600.0, api._limits({}), coolant_temp_C=65.0)
    r = repeated_load(api._drive({}), sc, _thermal_fixture(), [LoadPhase(300.0, 3000.0, 400.0),
                                                              LoadPhase(50.0, 3000.0, 400.0)],
                      cycles=60, feedback=Feedback(False), allowed=False, resolution_check=False)
    assert r["cycles_repeating_periodic"] > 0 and r["cycles_run"] + r["cycles_repeating_periodic"] == 60
    assert "repeating the periodic cycle" in r["claim"]["detail"]


def test_f07_an_unqualified_model_never_proves_a_violation_through_its_own_temperature_estimate():
    from traction_workbench.extensions.thermal_cycle import Feedback, LoadPhase, repeated_load
    from traction_workbench.models.components import TemperatureDependence
    law = TemperatureDependence(0.00393, (-40.0, 250.0), "copper coefficient (test)")
    sc = Scenario("cyc", 3600.0, 600.0, api._limits({}), coolant_temp_C=65.0)
    r = repeated_load(api._drive({}), sc, api.thermal_model_from_dict(None, 65.0),
                      [LoadPhase(492.0, 3600.0, 30.0), LoadPhase(50.0, 3600.0, 5.0)], cycles=5,
                      feedback=Feedback(True, law, 20.0), allowed=False)
    assert r["stopped"] is not None and r["stopped"]["t_s"] > 0
    assert r["claim"]["status"] == "UNKNOWN" and r["claim"]["reasons"] == ["UNVALIDATED_DURATION"]  # was INFEASIBLE
    assert "estimated winding" in r["claim"]["detail"]
    # a point that fails at t = 0 from the stated start is a static fact
    r0 = repeated_load(api._drive({}), sc, api.thermal_model_from_dict(None, 65.0),
                       [LoadPhase(560.0, 3600.0, 30.0)], cycles=1, feedback=Feedback(False), allowed=False)
    assert r0["stopped"]["t_s"] == 0.0 and r0["claim"]["status"] == "INFEASIBLE"


def test_f08_heat_source_coverage_is_checked_over_every_phase():
    """Pulse at 0 rpm first (no rotational loss), rest at 3000 rpm: 197 W of rotational loss heated no node, and the
    run said FEASIBLE because only phase 0 was checked."""
    from traction_workbench.extensions.thermal_cycle import Feedback, LoadPhase, repeated_load
    sc = Scenario("cyc", 0.0, 600.0, api._limits({}), coolant_temp_C=65.0)
    r = repeated_load(api._drive({}), sc, _thermal_fixture(rot_monitored=False),
                      [LoadPhase(300.0, 0.0, 5.0), LoadPhase(50.0, 3000.0, 5.0)], cycles=2, feedback=Feedback(False),
                      allowed=False, resolution_check=False)
    assert r["claim"]["status"] == "UNKNOWN"
    assert any("rotational loss" in p and "heats no node" in p for p in r["qualification_problems"])


def test_f11_the_feedback_step_is_second_order():
    """400 N*m for 120 s with R_s(T): the first limit came 2 % late at 24 steps (start-of-step losses)."""
    from traction_workbench.extensions.thermal_cycle import Feedback, LoadPhase, repeated_load
    from traction_workbench.models.components import TemperatureDependence
    law = TemperatureDependence(0.00393, (-40.0, 250.0), "copper coefficient (test)")
    wind = dict(api.EXAMPLE_THERMAL["nodes"][1], loss_share={"copper": 1.0, "rotational": 1.0}, limit_C=120.0)
    inv = dict(api.EXAMPLE_THERMAL["nodes"][0], limit_C=400.0)
    M = api.thermal_model_from_dict({**api.EXAMPLE_THERMAL, "nodes": [inv, wind]}, 65.0)
    sc = Scenario("cyc", 3000.0, 600.0, api._limits({}), coolant_temp_C=65.0)
    ph = [LoadPhase(400.0, 3000.0, 120.0), LoadPhase(50.0, 3000.0, 20.0)]

    def first(spp):
        return repeated_load(api._drive({}), sc, M, ph, cycles=1, feedback=Feedback(True, law, 20.0),
                             steps_per_phase=spp, allowed=False, resolution_check=False)["first_limit"]["t_s"]
    t24, t384 = first(24), first(384)
    assert abs(t24 - t384) / t384 < 0.005                           # was +2.2 %


def test_f13_a_margin_inside_the_declared_uncertainty_is_not_decided():
    from traction_workbench.extensions.thermal_cycle import Feedback, LoadPhase, repeated_load
    sc = Scenario("cyc", 3000.0, 600.0, api._limits({}), coolant_temp_C=65.0)
    ph = [LoadPhase(430.0, 3000.0, 8.0), LoadPhase(50.0, 3000.0, 20.0)]
    exact = repeated_load(api._drive({}), sc, _thermal_fixture(), ph, cycles=40, feedback=Feedback(False),
                          allowed=False)
    assert exact["claim"]["status"] == "FEASIBLE" and 0 < exact["horizon_margin_K"] < 1.0
    assert any("declares no uncertainty band" in q for q in exact["claim"]["qualifiers"])
    band = repeated_load(api._drive({}), sc, _thermal_fixture(uncertainty=1.0), ph, cycles=40,
                         feedback=Feedback(False), allowed=False)
    assert band["claim"]["status"] == "UNKNOWN" and band["claim"]["reasons"] == ["BOUNDARY_WITHIN_TOLERANCE"]
    from traction_workbench.errors import InputValidationError
    with pytest.raises(InputValidationError, match="basis"):
        api.thermal_model_from_dict({**api.EXAMPLE_THERMAL, "uncertainty_K": 2.0}, 65.0)


def test_thermal_f5_the_availability_table_is_not_qualified_with_an_unmonitored_loss():
    from traction_workbench.extensions.thermal import torque_availability
    sc = Scenario("x", 3000.0, 600.0, api._limits({}), coolant_temp_C=65.0, initial_state="equilibrium_at_coolant")
    av = torque_availability(api._drive({}), sc, _thermal_fixture(rot_monitored=False), (10.0, math.inf))
    assert av["status_note"].startswith("screening estimate") and "rotational loss" in av["status_note"]


def test_thermal_f10_coolant_simplifications_travel_with_the_result():
    m = api.thermal_model_from_dict({**api.EXAMPLE_THERMAL, "coolant": {**api.EXAMPLE_THERMAL["coolant"],
                                                                          "glycol_vol_pct": 80.0}}, 65.0)
    rep = m.coolant.describe()
    assert rep["properties_clamped"] and "clamp_note" in rep and "inlet temperature" in rep["properties_note"]
    from traction_workbench.extensions.thermal import _coolant_report
    loose = api.thermal_model_from_dict({**api.EXAMPLE_THERMAL, "nodes": [
        {k: v for k, v in n.items() if k != "station"} for n in api.EXAMPLE_THERMAL["nodes"]]}, 65.0)
    assert _coolant_report(loose, None)["nodes_without_station"]


def test_thermal_f11_a_narrow_rest_window_is_found():
    from traction_workbench.extensions.thermal_cycle import _min_monotone
    assert _min_monotone(lambda t: 10.0 <= t <= 10.4, 1.0) == pytest.approx(10.0, rel=1e-6)   # 4 % wide


# ---------------------------------------------------------------- DC link: F-09 / F5 / F-10 / F9 / F-16


OV = {"C_uF": 500, "V1_V": 600, "V_limit_V": 800, "speed_rpm": 12000, "torque_Nm": -150}


@pytest.mark.parametrize("reaction, status", [("freewheel", "INFEASIBLE"), ("unspecified", "UNKNOWN"),
                                              ("asc", "FEASIBLE")])
def test_f09_above_the_ucg_speed_the_reaction_path_decides(reaction, status):
    """12000 rpm: the line-line back-EMF peak 870.6 V exceeds 800 V; the energy balance alone said FEASIBLE with the
    freewheel caveat only in a note."""
    r = api.overvoltage({**OV, "reaction_time_ms": 0.1, "reaction": reaction})
    assert r["back_emf_ll_peak_V"] == pytest.approx(870.6, abs=0.1)
    assert r["claim"]["status"] == status
    if reaction == "unspecified":
        assert r["claim"]["reasons"] == ["COUPLED_MODEL_REQUIRED"]


def test_f09_the_inflow_is_bounded_at_the_voltage_limit():
    r = api.overvoltage({**OV, "V_limit_V": 900, "reaction_time_ms": 0.5, "reaction": "asc"})
    assert r["P_in_upper_W"] > r["P_in_W"]                        # less field weakening at the higher voltage
    assert r["V_peak_bound_V"] > r["V_peak_V"] and "bound" in r["claim"]["detail"]


def test_f5_a_ramp_longer_than_the_headroom_is_a_proven_violation_not_a_negative_time():
    from traction_workbench.extensions.dclink import regen_disconnect_overvoltage
    r = regen_disconnect_overvoltage(500e-6, 600.0, 180e3, 800.0, None, "delay_then_ramp", ramp_s=2e-3)
    assert r["max_reaction_time_s"] == 0.0                        # was -0.611 ms
    assert r["claim"]["status"] == "INFEASIBLE" and r["max_ramp_s"] == pytest.approx(2 * r["energy_headroom_J"] / 180e3)
    assert "shorter than" in r["claim"]["detail"]


@pytest.mark.parametrize("fsw_kHz", [10.0, 16.0, 20.0, 30.0])
def test_f10_the_esr_gate_follows_energy_not_the_fft_length(fsw_kHz):
    r = api.dclink_ripple({"ripple": {"fsw_kHz": fsw_kHz}})
    assert r["claims"]["capacitor_loss"]["status"] == "FEASIBLE"   # 16 / 20 kHz were UNKNOWN (0.05 % outside)
    mb = r["model_band"]
    assert 0 < mb["inverter_I2_share_outside"] < mb["gate_share"]
    assert r["P_cap_W"] == pytest.approx(r["P_cap_covered_W"] + r["P_cap_indicative_W"])


def test_f10_the_result_does_not_depend_on_the_requested_samples_per_carrier():
    from traction_workbench.extensions.dclink_ripple import CapacitorBank, SourceImpedance, ripple_analysis
    tab = ((100.0, 3.0e-3), (1e3, 2.0e-3), (1e4, 1.6e-3), (1e5, 1.8e-3), (1e6, 3.0e-3))
    b = CapacitorBank(500e-6, tab, ESL_H=15e-9, T_valid_C=(-40, 105))
    src = SourceImpedance(0.025, 2e-6, "ex")
    rs = [ripple_analysis(350.0, 0.8, math.radians(20), 200.0, 10e3, 600.0, b, src, "svpwm", samples_per_carrier=spc)
          for spc in (32, 64, 128, 256)]
    assert len({(r["I_cap_rms_A"], r["P_cap_W"], r["V_ripple_pp_V"]) for r in rs}) == 1   # one FFT length for all


def test_f10_a_table_that_misses_the_switching_band_still_gates():
    from traction_workbench.extensions.dclink_ripple import CapacitorBank, ripple_analysis
    narrow = CapacitorBank(500e-6, ((100.0, 2e-3), (5e3, 2e-3)))
    r = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, narrow)
    assert r["P_cap_W"] is None and r["claims"]["capacitor_loss"]["status"] == "UNKNOWN"


def _life_bank():
    from traction_workbench.extensions.dclink_ripple import CapacitorBank
    tab = ((100.0, 3.0e-3), (1e3, 2.0e-3), (1e4, 1.6e-3), (1e5, 1.8e-3), (1e6, 3.0e-3))
    return CapacitorBank(500e-6, tab, ESL_H=15e-9, Rth_K_per_W=0.35, ESR_temp_coeff_per_K=0.004, T_ref_C=25.0,
                         T_valid_C=(-40, 105), life_hours_table=((60.0, 30.0), (85.0, 10.0), (105.0, 3.0)),
                         life_voltage_V=900.0, life_basis="test table")


def test_f16_an_expected_life_is_not_a_pass_without_a_required_life():
    from traction_workbench.extensions.dclink_ripple import SourceImpedance, ripple_analysis
    src = SourceImpedance(0.025, 2e-6, "ex")
    args = (231.6, 1.02, math.radians(25.2), 400.0, 10e3, 600.0, _life_bank(), src, "svpwm")
    r = ripple_analysis(*args, T_ref_C=65.0)
    hrs = r["life"]["hours_at_hotspot"]
    assert hrs is not None and hrs < 30.0                          # a few-hour capacitor showed PASS
    assert r["claims"]["capacitor_life"]["status"] == "UNKNOWN"
    assert r["claims"]["capacitor_life"]["reasons"] == ["REQUIREMENT_INCOMPLETE"]
    short = ripple_analysis(*args, T_ref_C=65.0, required_life_h=10000.0)
    assert short["claims"]["capacitor_life"]["status"] == "INFEASIBLE"
    ok = ripple_analysis(*args, T_ref_C=65.0, required_life_h=0.5 * hrs)
    assert ok["claims"]["capacitor_life"]["status"] == "FEASIBLE"
    assert r["life"]["V_with_ripple_peak_V"] > 600.0               # the voltage check includes the ripple peak


# ---------------------------------------------------------------- F-15: a rating with open conditions decides nothing

def _peak_env(conditions=(), irrelevant=()):
    from traction_workbench.analysis.rating import ApprovalState, RatingApproval, RatingEnvelope
    from traction_workbench.models.provenance import DataOrigin, Provenance
    prov = Provenance(DataOrigin.SUPPLIER, "supplier sheet", "A", "validated")
    return RatingEnvelope("PEAK10", "A", 10.0, (0.0, 3000.0, 6000.0), (500.0, 500.0, 300.0), prov,
                          conditions=conditions, irrelevant_conditions=irrelevant,
                          approval=RatingApproval(ApprovalState.APPROVED, "RS-1", "A", "10 s peak rating"))


@pytest.mark.parametrize("stated", [{"coolant_temp_C": 65.0, "Vdc_V": 600.0, "initial_state": "cold"},
                                    {"coolant_temp_C": 105.0, "Vdc_V": 600.0, "initial_state": "hot_soak"},
                                    {"coolant_temp_C": None, "Vdc_V": None, "initial_state": None}])
def test_f15_a_condition_less_envelope_is_not_a_duration_pass_at_any_condition(stated):
    from traction_workbench.analysis.rating import duration_claim
    from traction_workbench.status import Reason, Status
    c = duration_claim([_peak_env()], 10.0, 3000.0, 450.0, stated, product=None)
    assert c.status is Status.UNKNOWN and c.reasons == (Reason.APPLICABILITY_UNCONFIRMED,)   # was FEASIBLE, []
    assert "coolant_temp_C, Vdc_V, initial_state" in c.detail and "it reads FEASIBLE" in c.detail
    assert any(q.startswith("applicability to this product") for q in c.qualifiers)          # -> requirement open item


def test_f15_stated_or_declared_irrelevant_conditions_restore_the_envelope():
    from traction_workbench.analysis.rating import duration_claim
    from traction_workbench.status import Status
    stated = {"coolant_temp_C": 65.0, "Vdc_V": 600.0, "initial_state": "cold"}
    env = _peak_env(conditions=(("coolant_temp_C", 65.0), ("initial_state", "cold")), irrelevant=("Vdc_V",))
    assert duration_claim([env], 10.0, 3000.0, 450.0, stated, product=None).status is Status.FEASIBLE
    hot = duration_claim([env], 10.0, 3000.0, 450.0, {**stated, "coolant_temp_C": 105.0}, product=None)
    assert hot.status is Status.UNKNOWN                              # stated conditions that do not match: no rating
    both = duration_claim([_peak_env(), env], 10.0, 3000.0, 450.0, stated, product=None)
    assert both.status is Status.FEASIBLE and any("required conditions open" in q for q in both.qualifiers)


# ---------------------------------------------------------------- F-18: bounded-input analysis, vertex certificate

def _box(n, T, iv, acc=None):
    from traction_workbench.analysis.uncertainty import bounded_input_analysis
    r = bounded_input_analysis(sf.synthetic_drive(), sf.synthetic_scenario(n, 600), T, iv, torque_accuracy_Nm=acc)
    return r, {c["name"]: c for c in r["claims"]}


def test_f18_all_corners_passing_is_a_proof_where_the_vertex_certificate_applies():
    from traction_workbench.analysis.uncertainty import ParameterInterval as P
    r, c = _box(3000, 100, [P("Rs_ohm", 0.016, 0.024), P("inverter_loss_scale", 0.8, 1.2)])
    assert r["vertex_certificate"]["applies"] and r["vertex_certificate"]["single_point"]
    assert c["robust_fixed_calibration"]["status"] == "FEASIBLE"             # was UNKNOWN (SAMPLED_COVERAGE)
    assert c["robust_adaptive"]["status"] == "FEASIBLE" and c["actual_system"]["status"] == "FEASIBLE"
    assert c["robust_fixed_calibration"]["evidence"][0]["kind"] == "certified_bound"


def test_f18_a_common_witness_settles_the_adaptive_claim_when_the_nominal_calibration_fails():
    from traction_workbench.analysis.uncertainty import ParameterInterval as P
    r, c = _box(12000, 150, [P("Rs_ohm", 0.016, 0.024), P("Vdc_V", 580, 620)])
    assert c["robust_fixed_calibration"]["status"] == "INFEASIBLE"           # the nominal FW point at 580 V: a real
    assert c["robust_adaptive"]["status"] == "FEASIBLE"                      # counterexample; the policy re-solved
    cp = r["vertex_certificate"]["common_point"]                            # per value is feasible everywhere
    assert cp["source"].startswith("witness at") and "580" in cp["source"]


def test_f18_the_certified_point_holds_inside_the_box():
    import numpy as np

    from traction_workbench.analysis.uncertainty import ParameterInterval as P, _fixed_calibration
    from traction_workbench.analysis.variation import apply
    from traction_workbench.settings import DEFAULT_SETTINGS
    iv = [P("Rs_ohm", 0.01, 0.03), P("Vdc_V", 560, 640), P("voltage_reserve_fraction", 0.0, 0.08),
          P("inverter_loss_scale", 0.5, 2.0)]
    r, c = _box(8000, 200, iv)
    assert c["robust_adaptive"]["status"] == "FEASIBLE"
    cp = r["vertex_certificate"]["common_point"]
    rng = np.random.default_rng(1)
    d0, s0 = sf.synthetic_drive(), sf.synthetic_scenario(8000, 600)
    for _ in range(120):
        d, s = d0, s0
        for i in iv:
            d, s = apply(d, s, i.name, rng.uniform(i.low, i.high))
        assert _fixed_calibration(d, s, DEFAULT_SETTINGS, cp["id_A"], cp["iq_A"], 200, None)[0] == "FEASIBLE"


def test_f18_torque_moving_parameters_certify_the_fixed_calibration_only_with_a_stated_accuracy():
    from traction_workbench.analysis.uncertainty import ParameterInterval as P
    iv = [P("rotational_loss_scale", 0.5, 1.5)]
    r, c = _box(3000, 100, iv, acc=1.0)
    assert c["robust_fixed_calibration"]["status"] == "FEASIBLE"
    assert c["robust_adaptive"]["status"] == "UNKNOWN"                       # no single point: the torque moves
    assert any("move(s) the torque" in q for q in c["robust_adaptive"]["qualifiers"])
    r2, c2 = _box(3000, 100, iv)
    assert not r2["vertex_certificate"]["applies"] and c2["robust_fixed_calibration"]["status"] != "FEASIBLE"


def test_f18_the_module_loss_is_outside_the_certificate():
    from dataclasses import replace

    from traction_workbench.analysis.uncertainty import ParameterInterval as P, vertex_certificate
    d = sf.synthetic_drive()
    d = replace(d, inverter=replace(d.inverter, loss=None, module_loss=api.module_model_from_dict(api.EXAMPLE_MODULE),
                                    module_Tj_C=150.0))
    cert = vertex_certificate(d, [P("Rs_ohm", 0.016, 0.024)], None)
    assert not cert["applies"] and "module" in cert["checks"][0]["detail"]


# ---------------------------------------------------------------- F-17: model efficiency is labelled as such

def test_f17_efficiency_results_are_labelled_model_efficiency():
    from traction_workbench.analysis.efficiency import MODEL_EFFICIENCY
    body = {"map_speeds_rpm": [3000.0, 9000.0], "map_torques_Nm": [50.0, 100.0]}
    pt, mp = api.efficiency({}), api.efficiency_map(body)
    assert pt["model_efficiency"] == MODEL_EFFICIENCY == mp["model_efficiency"]
    assert mp["meaning"].startswith("model efficiency map") and "not a loss-optimal point" in MODEL_EFFICIENCY
    from traction_workbench.i18n import language, set_language
    from traction_workbench.insight.efficiency import map_insight, point_insight
    lang = language()
    try:
        for code, words in (("en", "model efficiency"), ("ko", "모델 효율")):
            set_language(code)
            assert any(words in it.text for s in point_insight(pt).sections for it in s.items)
            assert words in map_insight(mp).headline
    finally:
        set_language(lang)


# ---------------------------------------------------------------- F-19: braking beyond a charge limit keeps a witness

def _f19_drive(psi, Ld, Lq, Rs, Imax, p, id_lo, id_hi, iq_hi):
    from traction_workbench.models.components import (DriveModel, InverterLossModel, InverterModel, MotorModel,
                                                        OperatingDomain, RotationalLossModel, VoltageModel)
    from traction_workbench.models.flux import ConstantFluxModel
    from traction_workbench.models.provenance import Fidelity
    motor = MotorModel("m", p, ConstantFluxModel(psi, Ld, Lq), Rs, RotationalLossModel(viscous_Nm_per_rad_s=0.001,
                                                                                        basis="test"),
                       "wye", fidelity=Fidelity.D1)
    inv = InverterModel("i", Imax, VoltageModel(0.05), InverterLossModel(100.0, 0.005, True))
    return DriveModel("d", "1", motor, inv, OperatingDomain((id_lo, id_hi), (-iq_hi, iq_hi), (-20000, 20000)),
                      sf.synthetic_drive().provenance)


@pytest.mark.parametrize("case, lim, brute", [
    ((0.2, 5.681235051181135e-4, 3.408741030708681e-4, 0.0, 900, 3, -900.0, 0.0, 630.0),
     (50e3, math.inf, 300.0, 150.0), -125.8786945557631),
    ((0.1, 5.401600623426953e-4, 3.2409603740561713e-4, 0.02, 600, 6, -900.0, 50.0, 720.0),
     (math.inf, 30e3, 300.0, 150.0), -106.67952978627359)])
def test_f19_a_witness_on_a_charge_limit_edge_is_moved_inside_not_lost(case, lim, brute):
    """Reviewer's seeds 2 / 3 (trial 57 / 40): Ld > Lq, id <= 0, a charge limit - the braking capability had no
    witness (UNKNOWN) although a brute-force grid finds feasible points down to ``brute``."""
    from traction_workbench.solvers.capability import capability
    sc = Scenario("s", 3000.0, 250.0, DcSourceLimits(*lim))
    cap = capability(_f19_drive(*case), sc, -1, "physical")
    assert cap.accepted and cap.certified and cap.value_Nm is not None
    assert cap.value_Nm <= brute                                   # at least as far as the brute-force grid
    assert abs(cap.bound_Nm - cap.value_Nm) <= cap.gap_tolerance_Nm


# ---------------------------------------------------------------- F12: display precision per quantity class

def test_f12_model_limited_quantities_are_shown_at_the_precision_the_model_supports():
    from traction_workbench.units import shown
    assert shown(589.9912, "voltage") == "590" and shown(1041.04, "voltage") == "1040"
    assert shown(12.9243, "loss") == "12.9" and shown(69.5213, "temperature") == "69.5"
    assert shown(4321.7, "speed") == "4320" and shown(152.5547, "capability") == "152.6"
    assert shown(0.000639377, "loss") == "0.000639" and shown(None, "loss") == "-"


def test_f12_the_capacitor_loss_and_hotspot_carry_their_sampling_resolution():
    r = api.dclink_ripple({})
    c = r["claims"]["capacitor_loss"]
    assert c["status"] == "FEASIBLE" and r["P_cap_resolution_W"] is not None and r["hotspot_resolution_K"] is not None
    assert "sampling resolution" in c["detail"] and "degC" in c["detail"]
    lead = c["detail"].split(" W at ")[0]
    assert len(lead.replace(".", "").lstrip("0")) <= 3                   # "12.9", not "12.92"
    assert r["P_cap_resolution_W"] < 0.01 * r["P_cap_W"]                 # the resolution is small against the loss


def test_f12_terminal_voltage_text_is_rounded_the_value_is_not():
    res = _ocv_case(_regen_edge_torque(), 10, RELAXED)
    vt = res["source_coupling"]["V_terminal_V"][0]
    assert vt == pytest.approx(604.04, abs=0.01)                         # full precision in the data
    text = " ".join(res["verdict"]["qualifiers"])
    assert "terminal 604 V" in text and "604.04" not in text             # 3 significant digits in the sentence


# ---------------------------------------------------------------- P3: one modulation list for every consumer

def test_p3_dpwm1_is_accepted_by_every_pulse_pattern_consumer_and_matches_a_brute_force_ripple():
    import numpy as np

    from traction_workbench.extensions.emi import SwitchingSource, pwm_edges
    from traction_workbench.extensions.pwm_policy import phase_ripple
    from traction_workbench.modulation import MODULATIONS, duties
    assert "dpwm1" in MODULATIONS
    Vdc, fe, fsw, L, m, alpha = 600.0, 200.0, 10e3, 200e-6, 0.9, 0.3
    e_sv = pwm_edges(SwitchingSource(Vdc, 300.0, 0.2, m, alpha, fe, fsw, 50e-9, 50e-9, 0.0, "svpwm"))
    e_d1 = pwm_edges(SwitchingSource(Vdc, 300.0, 0.2, m, alpha, fe, fsw, 50e-9, 50e-9, 0.0, "dpwm1"))
    assert e_d1["validity"]["ok"] and len(e_d1["edges"]) < 0.75 * len(e_sv["edges"])   # a leg rests 1/3 of the time
    r = phase_ripple(Vdc, m, alpha, fe, fsw, L, "dpwm1")
    N = e_d1["carrier_ratio"]
    T, n = 1.0 / fe, 4000 * N
    Ts = T / N
    t = (np.arange(n) + 0.5) * T / n
    j = np.minimum((t // Ts).astype(int), N - 1)
    d = np.clip(duties(2 * math.pi * fe * ((np.arange(N) + 0.5) * Ts), m, alpha, "dpwm1"), 0, 1)
    v0 = np.where(np.abs(t - (j + 0.5) * Ts) < 0.5 * d[:, j] * Ts, Vdc / 2, -Vdc / 2)
    van = v0[0] - v0.mean(axis=0)
    w = 2 * math.pi * fe
    vh = van - 2 * np.mean(van * np.cos(w * t)) * np.cos(w * t) - 2 * np.mean(van * np.sin(w * t)) * np.sin(w * t)
    ih = np.cumsum(vh - vh.mean()) * (T / n) / L
    rms_bf = math.sqrt(np.mean((ih - ih.mean()) ** 2))
    assert r["ripple_rms_A"] == pytest.approx(rms_bf, rel=2e-3)
    assert r["ripple_rms_spectrum_A"] == pytest.approx(r["ripple_rms_A"], rel=1e-4)


def test_p3_the_clamped_leg_sits_exactly_on_its_rail():
    import numpy as np

    from traction_workbench.modulation import duties
    d = duties(np.linspace(0, 2 * math.pi, 7201), 0.95, 0.1, "dpwm1")
    clamped = (d == 0.0) | (d == 1.0)
    assert clamped.any(axis=0).all()                                    # one leg on a rail at every angle
    assert not ((d > 0) & (d < 1e-9)).any() and not ((d < 1) & (d > 1 - 1e-9)).any()
