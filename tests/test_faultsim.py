"""Causal fault simulation: the plant against independent references, the causal chain (fault -> measurement ->
control / monitoring -> reaction -> actual bridge -> truth -> requirement), and the reproduction of every behaviour
the tool must show (protection success, delay, wrong reaction, lost path, common cause, false detection, recovery
failure, no executable safe reaction) on the synthetic project.

The pinned numbers are what the synthetic architecture gives; every test states the physical reason it holds.
"""

from __future__ import annotations

import copy
import json
import math

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.faultsim import campaign as C
from traction_workbench.extensions.faultsim.configure import FAULT_SIM_EXAMPLE, apply_overrides, build_setup
from traction_workbench.extensions.faultsim.engine import simulate
from traction_workbench.extensions.faultsim.plant import DcParams, LegCommand, Plant, integrate
from traction_workbench.extensions.faultsim.reference import AbcReference, plant_run, validation_suite
from traction_workbench.extensions.faultsim.safety import evaluate
from traction_workbench.extensions.faultsim.study import SCENARIOS, scenario
from traction_workbench.project import builtin_project

PRODUCT = api.fault_product()
_CACHE: dict = {}


def run(key_or_scenario):
    """explain() of a representative scenario (cached per module) or of an explicit scenario."""
    if isinstance(key_or_scenario, str):
        if key_or_scenario not in _CACHE:
            _CACHE[key_or_scenario] = api.fault_sim({"example": key_or_scenario})
        return _CACHE[key_or_scenario]
    return api.fault_sim(key_or_scenario)


def events(r, kind=None, source=None):
    return [e for e in r["events"] if (kind is None or e["kind"] == kind) and (source is None or e["source"] == source)]


def machine():
    setup, _r, _i = build_setup(PRODUCT, {"speed_rpm": 6000, "torque_Nm": 100})
    return setup.machine, setup.dc


# -------------------------------------------------------------------------------------------- plant physics

def test_plant_against_closed_forms_independent_abc_reference_and_convergence():
    """Closed forms (steady ASC, rectification onset), the matrix exponential (numerics of the same equations), an
    independent abc formulation with conductance devices (six-switch-off, ASC, an asymmetric partial short) whose
    distance to the plant shrinks as its devices approach the ideal, the energy ledger and step convergence."""
    m, dc = machine()
    rows = validation_suite(m, dc, quick=True)
    bad = [r for r in rows if not r["pass"]]
    assert not bad, bad
    kinds = {r["reference_kind"] for r in rows}
    assert {"closed form", "independent formulation", "energy balance", "numerical convergence"} <= kinds
    assert any("partial short" in r["case"] for r in rows)


def test_a_floating_leg_at_the_rail_conducts_instead_of_floating_beyond_it():
    """Found by the independent reference: a leg whose zero-current pole voltage reaches a rail must start to conduct
    (look-ahead on the direction of motion), not float beyond the rail - the partial short builds its DC-offset
    currents like the abc reference does."""
    m, dc = machine()
    stiff = DcParams(C=dc.C, V_oc=dc.V_oc, R_bat=dc.R_bat)
    p = plant_run(m, stiff, ("upper_on", "off", "off"), 3000, 0.3, (0.0, 0.0), dc.V_oc, 10e-3, n_out=201)
    r = AbcReference(m, stiff, ("upper_on", "off", "off"), 1e5, 1e-6).run(3000, 0.3, (0.0, 0.0), dc.V_oc, 10e-3, 201)
    assert np.max(np.abs(p["i_c"])) > 50.0                     # the third phase conducts (it did not before)
    assert np.max(np.abs(p["i_a"] - r["i_a"])) < 1.0


def test_open_circuit_below_the_onset_carries_no_current_and_six_switch_off_above_it_brakes():
    m, dc = machine()
    onset = dc.V_oc / (math.sqrt(3) * m.psi * m.p) * 60 / (2 * math.pi)
    stiff = DcParams(C=dc.C, V_oc=dc.V_oc, R_bat=dc.R_bat)
    lo = plant_run(m, stiff, ("off",) * 3, 0.95 * onset, 0.1, (0.0, 0.0), dc.V_oc, 4e-3, n_out=81)
    hi = plant_run(m, stiff, ("off",) * 3, 1.3 * onset, 0.1, (0.0, 0.0), dc.V_oc, 12e-3, n_out=241)
    assert np.max(np.abs(lo["i_a"])) == 0.0
    assert np.mean(hi["T_em"][-60:]) < -20.0                    # uncontrolled rectification brakes


def test_asc_is_not_torque_free_at_low_speed_and_six_switch_off_is_not_current_free_at_high_speed():
    """A commanded safe state is not a safe condition: ASC brakes hard at low speed; six-switch-off rectifies
    (braking torque and charging current) above the onset - the simulation computes both instead of assuming them."""
    m, dc = machine()
    stiff = DcParams(C=dc.C, V_oc=dc.V_oc, R_bat=dc.R_bat)
    asc = plant_run(m, stiff, ("lower_on",) * 3, 500.0, 0.2, (0.0, 0.0), dc.V_oc, 60e-3, n_out=301)
    assert np.mean(asc["T_em"][-50:]) < -80.0                   # ~ -98 N*m steady ASC braking at 500 rpm
    six = plant_run(m, stiff, ("off",) * 3, 12000.0, 0.2, (0.0, 0.0), dc.V_oc, 20e-3, n_out=401)
    assert np.mean(six["T_em"][-40:]) < -100.0


# -------------------------------------------------------------------------------------------- the causal chain

def test_fault_free_operation_keeps_the_kernel_operating_point_and_never_reacts():
    """Fault-free: no detection, no reaction, the kernel's policy point.  The sampled control applies a voltage
    held for a period while the rotor turns (pulse ratio 12.5 at 12000 rpm: the period average is sinc(w T_s / 2)
    = 0.99 of the vector) and the dead time costs ~10 V; the pole-zero-cancellation PI rejects such a voltage
    disturbance only with tau = L_q / R (27 ms) - so the torque approaches the kernel's value, slowly."""
    r = run("normal_hs")
    s = r["summary"]
    assert r["status"] == "completed" and not events(r, "detection") and not events(r, "actuation")
    assert r["verdicts"]["TSR-05"] == "PASS"
    tr_ = r["trace"]
    late = tr_["t"] > 0.04
    assert abs(np.mean(tr_["T_shaft"][late]) - 150.0) < 0.03 * 150.0
    assert abs(s["initial"]["i_d_A"] - (-367.78)) < 0.05          # the kernel's minimum-current point (12000 rpm)
    assert s["energy"]["relative_residual"] < 1e-5
    long = run({"speed_rpm": 6000, "torque_Nm": 150, "horizon_ms": 120})
    t, T = long["trace"]["t"], long["trace"]["T_shaft"]
    err = [abs(np.mean(T[(t > a) & (t <= a + 0.02)]) - 150.0) for a in (0.02, 0.10)]
    assert err[1] < err[0] and err[1] < 0.005 * 150.0


def test_sensor_fault_acts_through_closed_loop_control_and_is_seen_by_measured_channels_only():
    """A current-sensor offset changes the ACTUAL currents (the loop regulates the wrong measurement) and the
    detection comes from the measured current sum - the mechanism never reads the plant."""
    r = run("cs_offset")
    tr_ = r["trace"]
    k = int(np.searchsorted(tr_["t"], 0.0109))
    assert abs(tr_["i_a_meas"][k] - tr_["i_a"][k] - 150.0) < 1.0   # the sensor reads 150 A more than the truth
    d = events(r, "detection")[0]
    assert d["source"] == "SM-SUM" and abs(d["t"] - 0.0109) < 1e-9  # 10 samples of 0.1 ms debounce after 10 ms
    assert r["verdicts"]["FSR-01"] == "PASS"
    tl = next(f["timeline"] for f in r["evaluation"]["fsr"] if f["id"] == "FSR-01")
    assert tl["FHTI"] == pytest.approx(tl["FDTI"] + tl["FRTI"])     # one trajectory: FHTI = FDTI + FRTI
    assert tl["t_S"] > tl["t_R"] >= tl["t_D"] > tl["t_F"]          # detection, command, safe condition in order


STALE = {"speed_rpm": 12000, "request": {"kind": "step", "T0_Nm": 150, "T1_Nm": 0, "t0_ms": 20}, "horizon_ms": 80,
         "faults": [{"kind": "torque_command", "t_ms": 15, "params": {"mode": "stale"}}]}
STALE_COMMON = {**STALE, "horizon_ms": 100,
                "faults": [{"kind": "torque_command", "t_ms": 15, "params": {"mode": "stale", "paths": "both"}}]}


def test_monitor_without_independent_request_misses_a_stale_command_common_cause():
    """An engine capability kept for the reference verification of documents that require it: a fault of the sender
    (another controller) is not a drive-system preset."""
    ok, bad = run(copy.deepcopy(STALE)), run(copy.deepcopy(STALE_COMMON))
    assert events(ok, "detection", "SM-TQ") and ok["verdicts"]["TSR-01"] == "PASS"
    assert all(v in ("PASS", "NOT_APPLICABLE") for k, v in ok["verdicts"].items() if k.startswith("TSR"))
    assert not events(bad, "detection") and bad["verdicts"]["TSR-01"] == "FAIL"
    tl = next(f["timeline"] for f in bad["evaluation"]["fsr"] if f["id"] == "FSR-01")
    assert tl["undetected"] and bad["verdicts"]["FSR-01"] == "FAIL" and bad["verdicts"]["SG-01"] == "FAIL"


def test_tight_monitor_trips_on_a_healthy_torque_step_false_detection():
    good, bad = run("step_ok"), run("false_trip")
    assert good["verdicts"]["TSR-05"] == "PASS" and not events(good, "detection")
    assert bad["verdicts"]["TSR-05"] == "FAIL" and events(bad, "detection", "SM-TQ")


def test_protection_path_lost_backup_reacts_later_and_delay_violates_the_rating():
    base, lost, slow = run("ov_regen"), run("ov_path_lost"), run("ov_slow")
    assert base["verdicts"]["TSR-06"] == lost["verdicts"]["TSR-06"] == "PASS"
    assert lost["summary"]["v_dc_max_V"] > base["summary"]["v_dc_max_V"] + 10.0     # the backup costs margin
    assert any(e["kind"] == "reaction_blocked" and "HW lost" in e["text"] for e in lost["events"])
    assert slow["verdicts"]["TSR-06"] == "FAIL" and slow["summary"]["v_dc_max_V"] > 1000.0


def test_frozen_position_sensor_makes_the_policy_choose_six_switch_off_at_high_speed_wrong_reaction():
    """The safe-state selection uses the speed measured from the faulty sensor: 6SO at a true 12000 rpm rectifies
    into the battery - a braking torque and a d-axis current beyond their limits, the safe state never reached.  The
    battery system's own protection is outside the drive-system scope: the battery stays connected (no BMS, no
    contactor event); the relay opening is a scenario event, and with it the DC link over-charges."""
    r = run("res_lost")
    act = events(r, "actuation")[0]
    assert act["reaction"] == "six_switch_off" and r["summary"]["final_bridge"] == "six_switch_off"
    v = r["verdicts"]
    assert v["TSR-08"] == v["TSR-09"] == v["TSR-03"] == "FAIL" and v["TSR-06"] == "PASS"
    assert not any(e["source"] in ("BMS", "contactor") for e in r["events"])
    assert r["summary"]["v_dc_max_V"] < 700.0                       # the battery holds the DC link
    relay = run("res_lost_relay")                                    # the relay opens at 22 ms, during the 6SO
    assert relay["verdicts"]["TSR-06"] == "FAIL" and relay["summary"]["v_dc_max_V"] > 850.0
    assert any(e["source"] == "contactor" and "fault" in e["text"] for e in relay["events"])


def test_the_example_is_scoped_to_the_drive_system():
    """No battery-system protection in the example and no preset built on another controller's fault."""
    fs = builtin_project().data("fault_sim")
    assert "bms" not in fs["battery"] and "BMS" in fs["scope"]["not_assumed"]
    from traction_workbench.extensions.faultsim.engine import OUTSIDE_DRIVE_SCOPE
    kinds = {f["kind"] for s in SCENARIOS for f in s["scenario"].get("faults", [])}
    assert not kinds & set(OUTSIDE_DRIVE_SCOPE) and "battery_disconnect" in kinds


def test_shorted_switch_selects_the_asc_of_its_own_side_and_a_speed_only_policy_does_not():
    good, bad = run("sw_short_ls"), run("sw_short_wrong")          # same 3000 rpm, same fault
    assert events(good, "actuation")[0]["reaction"] == "asc_high" and good["verdicts"]["TSR-07"] == "PASS"
    assert events(bad, "actuation")[0]["reaction"] == "asc_low" and bad["verdicts"]["TSR-07"] == "FAIL"
    # the right reaction protects the devices, but at 3000 rpm its braking torque does not settle within the FRTI:
    # component protection and hazard containment are separate verdicts
    assert good["verdicts"]["TSR-03"] == "FAIL" and good["verdicts"]["FSR-02"] == "PASS"
    hs = run("sw_short")                                            # 12000 rpm: the same reaction contains both
    assert all(v in ("PASS", "NOT_APPLICABLE") for k, v in hs["verdicts"].items() if k.startswith("TSR"))


def test_gate_supply_loss_reported_by_uvlo_selects_the_other_side():
    r = run("gate_supply")
    d = events(r, "detection", "SM-UVLO")[0]
    assert d["t"] - 0.010 == pytest.approx(2e-6, abs=1e-9)
    assert events(r, "actuation")[0]["reaction"] == "asc_high"


def test_shared_current_sensor_supply_blinds_control_and_the_hardware_comparator():
    r = run("sens_supply")
    det = [e["source"] for e in events(r, "detection")]
    assert "SM-OC" not in det and "SM-DSAT" in det                 # the comparator reads the same dead sensors
    assert r["verdicts"]["TSR-07"] == "FAIL" and r["summary"]["i_phase_peak_A"] > 1600.0


def test_flying_restart_recovers_and_cold_restart_ends_in_the_safe_state():
    fly, cold = run("restart_flying"), run("restart_cold")
    assert not events(fly, "detection") and fly["summary"]["final_bridge"] == "pwm"
    assert events(cold, "detection") and cold["summary"]["final_bridge"] != "pwm"
    assert cold["summary"]["i_phase_peak_A"] > 2 * fly["summary"]["i_phase_peak_A"]


def test_recovery_attempts_with_a_persistent_fault_trip_again_and_latch():
    r = run("recovery_fail")
    rec = events(r, "recovery")
    tries = [e for e in rec if e["text"].startswith("recovery attempt ")]
    assert len(tries) == 2 and any("exhausted" in e["text"] for e in rec)
    t_last = tries[-1]["t"]
    assert any(e["kind"] == "detection" and e["t"] > t_last for e in r["events"])   # tripped again after attempt 2
    assert r["summary"]["final_bridge"] != "pwm"


def test_candidate_comparison_finds_no_executable_safe_reaction():
    c = api.fault_compare({"example": "no_safe_reaction"})
    assert c["passing_candidates"] == [] and "no executable safe reaction" in c["statement"]
    rows = {r["candidate"]: r for r in c["rows"]}
    assert "lower gate unavailable" in rows["asc_low"]["final_actual"][0]           # commanded is not actual


def test_candidate_comparison_on_regeneration_disconnect_prefers_asc():
    c = api.fault_compare({"example": "ov_regen", "candidates": ["policy", "none", "six_switch_off", "asc_low"]})
    rows = {r["candidate"]: r for r in c["rows"]}
    assert rows["policy"]["overall"] == "PASS" and rows["asc_low"]["overall"] == "PASS"
    assert rows["none"]["overall"] == "FAIL" and rows["six_switch_off"]["overall"] == "FAIL"
    assert rows["none"]["metrics"]["v_dc_max_V"] > 1500.0


def test_fault_instant_and_initial_angle_change_the_peaks_and_the_timing():
    """The electrical angle at the fault and the sampling phase are not averaged away: the ASC entry peak and the
    detection instant depend on them."""
    peaks, dets = [], []
    for th in (0.0, 90.0):
        for tf in (10.0, 10.04):
            sc = scenario("cs_offset")
            sc.update(theta0_deg=th, horizon_ms=16)
            sc["faults"][0]["t_ms"] = tf
            r = C.run_one(PRODUCT, sc)
            peaks.append(r["metrics"]["i_phase_peak_A"])
            dets.append(r["metrics"]["first_detection_ms"])
    assert max(peaks) - min(peaks) > 5.0
    assert len({round(d, 6) for d in dets}) > 1


# -------------------------------------------------------------------------------------------- requirement semantics

def test_a_violation_before_leaving_the_model_is_a_fail_and_an_unobserved_window_is_unknown():
    """Both switches of a leg shorted: the plant stops (out of model).  Before that nothing was violated, so the
    torque windows are UNKNOWN (never PASS); a violation seen earlier would stay a FAIL."""
    sc = {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 30,
          "faults": [{"kind": "switch_short", "t_ms": 10, "params": {"leg": "b", "device": "upper"}},
                     {"kind": "switch_short", "t_ms": 12, "params": {"leg": "b", "device": "lower"}}]}
    r = run(sc)
    assert r["status"] == "stopped_out_of_model" and "DC-link short" in r["stop_reason"]
    assert r["verdicts"]["TSR-01"] == "UNKNOWN"
    reasons = {x["id"]: x.get("reason") for x in r["evaluation"]["tsr"]}
    assert reasons["TSR-01"] == "WINDOW_NOT_OBSERVED"


def test_a_safe_state_missed_by_its_deadline_is_a_fail_even_if_the_hold_window_is_cut_off():
    """Resolver loss at 12000 rpm with ASC-low forced: |T| <= 60 N*m is not reached by detection + 30 ms, and the
    trace ends before detection + 30 ms + the 20 ms hold.  No start inside the window can still hold, so the verdict
    is decided (FAIL) - an UNKNOWN here would hide a valid counterexample.  FRTI / FHTI are bounded below by the end
    of the observed trace and fail against their budgets on that bound."""
    sc = scenario("res_lost")
    sc["reaction_override"] = "asc_low"
    r = run(sc)
    tsr3 = next(x for x in r["evaluation"]["tsr"] if x["id"] == "TSR-03")
    o, end = tsr3["evidence"]["origin_s"], r["trace"]["t"][-1]
    assert o + 0.030 < end < o + 0.030 + 0.020                     # deadline observed, hold window cut off
    first = tsr3["counter"]["first_safe_s"]                          # reached only after the deadline (or never)
    assert tsr3["verdict"] == "FAIL" and (first is None or first > o + 0.030)
    tl = next(f["timeline"] for f in r["evaluation"]["fsr"] if f["id"] == "FSR-01")
    lo = (first if first is not None else end) - tl["t_D"]           # t_S cannot come earlier than that
    assert tl["FRTI"] is None and tl["FRTI_min"] == pytest.approx(lo, abs=1e-9) and tl["FRTI_min"] > 0.030
    tsr4 = next(x for x in r["evaluation"]["tsr"] if x["id"] == "TSR-04")
    assert tsr4["verdict"] == "FAIL" and "FRTI >=" in tsr4["detail"]


def test_sg_carries_inverter_evidence_and_a_vehicle_indicator_never_a_vehicle_approval():
    r = run(copy.deepcopy(STALE_COMMON))
    sg = {g["id"]: g for g in r["evaluation"]["sg"]}["SG-01"]
    assert sg["inverter_evidence"] == "FAIL" and "not a vehicle safety approval" in sg["statement"]
    assert sg["vehicle"]["status"] == "INDICATOR" and "rigid-driveline" in sg["vehicle"]["detail"]
    assert r["evaluation"]["scope"]["kind"] == "scenario"


def test_event_definitions_are_explicit():
    ev = run("cs_offset")["evaluation"]["event_definitions"]
    assert ev["FHTI"] == "t_S - t_F" and "allocated" in ev["t_D"] and "holds" in ev["t_S"]


# -------------------------------------------------------------------------------------------- campaigns, records

def test_campaign_brackets_the_failure_boundary_and_keeps_worst_cases_per_run(tmp_path):
    base = scenario("ov_regen")
    base["horizon_ms"] = 25
    base["faults"] = [{"kind": "mechanism_disabled", "t_ms": 0, "params": {"mechanism": "SM-OVSW"}}] + base["faults"]
    r = C.run_campaign(PRODUCT, {"base": base, "mode": "grid", "boundary_refinements": 2,
                                 "axes": [{"path": "overrides.mechanisms.SM-OV.params.threshold_V",
                                           "values": [800.0, 880.0]}]})
    assert r["summary"]["overall"]["FAIL"] == 1 and r["scope"]["explored_set"] == "FAIL"
    assert r["scope"]["region"] == "NOT_ESTABLISHED"
    b = r["boundaries"][0]
    assert b["verdicts"] == ["PASS", "FAIL"] and 800.0 <= b["between"][0] < b["between"][1] <= 880.0
    assert r["worst"]["v_dc_max_V"]["run"] == 1
    if "FDTI_plus_FRTI_bound_ms" in r["worst"]:
        assert r["worst"]["FDTI_plus_FRTI_bound_ms"]["run"] is None          # a bound, not a trajectory
    rec = r["counterexamples"][0]
    path = tmp_path / "cx.json"
    C.save_records(path, [rec])
    back = C.load_records(path)[0]
    again = C.rerun_record(PRODUCT, back)
    assert again["reproduced"] and again["stale_sections"] == []
    p2 = PRODUCT.project
    data = json.loads(json.dumps(p2.data("fault_sim")))
    data["paths"][1]["delay_us"] = 1.0
    from traction_workbench.extensions.faultsim.configure import ProductData
    changed = ProductData(p2.with_section("fault_sim", data), PRODUCT.drive)
    moved = C.rerun_record(changed, back)
    assert moved["stale_sections"] == ["fault_sim"] and "inputs changed" in moved["statement"]


def test_campaign_axes_and_scenario_paths():
    sc = C.set_path({"faults": [{"t_ms": 1, "params": {}}]}, "faults.0.params.value", 3.0)
    assert sc["faults"][0]["params"]["value"] == 3.0
    sc = C.set_path({}, "overrides.mechanisms.SM-TQ.params.abs_Nm", 20.0)
    assert sc["overrides"] == {"mechanisms.SM-TQ.params.abs_Nm": 20.0}
    assert C.axis_values({"path": "x", "range": [0, 1], "n": 3}) == [0.0, 0.5, 1.0]
    with pytest.raises(InputValidationError):
        C.run_campaign(PRODUCT, {"base": {}, "axes": []})


# -------------------------------------------------------------------------------------------- project data

def test_architecture_is_project_data_and_is_validated():
    p = builtin_project()
    assert p.has("fault_sim")
    bad = json.loads(json.dumps(p.data("fault_sim")))
    bad["roles"]["current_a"] = "NOPE"
    with pytest.raises(InputValidationError):
        p.with_section("fault_sim", bad)
    bad = json.loads(json.dumps(p.data("fault_sim")))
    bad["mechanisms"][0]["path"] = "NOPE"
    with pytest.raises(InputValidationError):
        p.with_section("fault_sim", bad)
    bad = json.loads(json.dumps(p.data("fault_sim")))
    bad["topology"] = "three_level_npc"
    with pytest.raises(InputValidationError):
        p.with_section("fault_sim", bad)
    o = apply_overrides(FAULT_SIM_EXAMPLE, {"mechanisms.SM-TQ.params.abs_Nm": 11.0, "policy.rules.4.if.speed_above_rpm":
                                            5000.0})
    assert o["mechanisms"][0]["params"]["abs_Nm"] == 11.0 and o["policy"]["rules"][4]["if"]["speed_above_rpm"] == 5000.0
    assert FAULT_SIM_EXAMPLE["mechanisms"][0]["params"]["abs_Nm"] == 30.0     # the example itself is untouched


def test_every_representative_scenario_is_declared_with_its_category():
    cats = {s["category"] for s in SCENARIOS}
    for c in ("protection success", "protection delay", "wrong reaction", "lost protection path", "common cause",
              "false detection", "recovery failure", "no executable safe reaction", "normal operation"):
        assert c in cats
    for s in SCENARIOS:
        build_setup(PRODUCT, s["scenario"])                     # every scenario builds on the project


def test_independence_view_names_the_shared_sensors_and_resources():
    ind = api.fault_independence()
    tq = next(m for m in ind["mechanisms"] if m["mechanism"] == "SM-TQ")
    assert set(tq["shares_sensors_with_control"]) >= {"CS_A", "CS_B", "RES"}
    oc = next(m for m in ind["mechanisms"] if m["mechanism"] == "SM-OC")
    assert "SENS_5V" in oc["needs"]
    assert any(r["resource"] == "MCU" and "SM-TQ" in r["mechanisms"] for r in ind["resources"])
