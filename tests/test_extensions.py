"""Screening extensions: timing/FTTI, DC link, safe-state screening, thermal (incl. fixture E01)."""

import math

import pytest

from conftest import golden, scenario
from traction_workbench import spec_fixtures as sf
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.dclink import active_discharge, back_emf_ll_peak, regen_disconnect_overvoltage
from traction_workbench.extensions.safe_state import asc_steady_state, safe_state_screening
from traction_workbench.extensions.thermal import (FosterNetwork, ThermalModel, ThermalNode, temperature,
                                                   thermal_duration, time_to_limit, torque_availability)
from traction_workbench.extensions.timing import TimingChain, TimingItem, analyze_timing
from traction_workbench.models import DataOrigin, Provenance

EVENTS = ("fault", "sensed", "detected", "confirmed", "reaction_request", "gate_off", "safe_state")


def chain(extra=(), ftti=0.030):
    items = (TimingItem("SENSE", "fault", "sensed", "HW", 0.0005, 0.0002),
             TimingItem("DETECT", "sensed", "detected", "SW", 0.002, 0.001, period_s=0.001),
             TimingItem("DEBOUNCE", "detected", "confirmed", "SW", 0.005, 0.005),
             TimingItem("SW_REACT", "confirmed", "reaction_request", "SW", 0.010, 0.002),
             TimingItem("GATE", "reaction_request", "gate_off", "HW", 0.0002, 0.0001),
             TimingItem("DECAY", "gate_off", "safe_state", "HW", 0.003, 0.001)) + tuple(extra)
    return TimingChain("C1", "overcurrent", ftti, EVENTS, items, detection_event="confirmed",
                       fdti_budget_s=0.010, frti_budget_s=0.015)


def test_timing_worst_case_with_periodic_delay():
    r = analyze_timing(chain())
    assert r["worst_s"] == pytest.approx(0.0005 + 0.003 + 0.005 + 0.010 + 0.0002 + 0.003)
    assert r["claim"]["status"] == "FEASIBLE" and not r["duplicate_budgets"]
    assert r["fdti_worst_s"] == pytest.approx(0.0085) and r["frti_worst_s"] == pytest.approx(0.0132)


def test_timing_duplicate_budget_detected():
    sysfrti = TimingItem("SYS_FRTI", "confirmed", "safe_state", "System", 0.020)
    r = analyze_timing(chain((sysfrti,)))
    dups = {tuple(d["items"]) for d in r["duplicate_budgets"]}
    assert ("SW_REACT", "SYS_FRTI") in dups
    assert any("counted twice" in d["message"] for d in r["duplicate_budgets"])
    assert r["worst_s"] == pytest.approx(0.0217)        # the composite item is not added on top


def test_timing_violation_gap_and_missing():
    assert analyze_timing(chain(ftti=0.015))["claim"]["status"] == "INFEASIBLE"
    gap = TimingChain("C2", "f", 0.03, EVENTS, (TimingItem("A", "fault", "sensed", "HW", 0.001),))
    assert analyze_timing(gap)["claim"]["status"] == "UNKNOWN" and analyze_timing(gap)["gaps"]
    nomax = TimingChain("C3", "f", 0.03, ("a", "b"), (TimingItem("A", "a", "b", "HW", None, 0.001),))
    assert analyze_timing(nomax)["claim"]["reasons"] == ["MISSING_INPUT"]
    with pytest.raises(InputValidationError):
        TimingItem("X", "a", "b", "HW", 0.001, 0.002)


def test_active_discharge_formulas():
    r = active_discharge(500e-6, 600.0, 60.0, 2.0)
    assert r["R_max_ohm"] == pytest.approx(-2.0 / (500e-6 * math.log(60 / 600)))
    assert r["I0_A"] == pytest.approx(600 / r["R_max_ohm"])
    assert r["P0_W"] == pytest.approx(600 ** 2 / r["R_max_ohm"])
    assert r["E_R_J"] == pytest.approx(0.5 * 500e-6 * (600 ** 2 - 60 ** 2))
    assert r["t_reach_s"] == pytest.approx(2.0)
    slow = active_discharge(500e-6, 600.0, 60.0, 2.0, R_ohm=5000.0)
    assert slow["claim"]["status"] == "INFEASIBLE"


def test_discharge_blocked_by_back_emf(drive):
    r = active_discharge(500e-6, 600.0, 60.0, 2.0, drive=drive, speed_rpm=2000.0)
    assert r["claim"]["status"] == "INFEASIBLE"
    assert r["back_emf_ll_peak_V"] == pytest.approx(math.sqrt(3) * 4 * 2 * math.pi * 2000 / 60 * 0.1)
    assert r["max_speed_for_target_rpm"] == pytest.approx(60 / (math.sqrt(3) * 0.1) / 4 * 60 / (2 * math.pi))


def test_regen_disconnect_overvoltage(drive):
    p = 95421.46289808393     # I08 regen DC power (golden) as power into the link
    r = regen_disconnect_overvoltage(500e-6, 600.0, p, 850.0)
    assert r["time_to_limit_constant_power_s"] == pytest.approx(500e-6 * (850 ** 2 - 600 ** 2) / (2 * p))
    assert r["claim"]["status"] == "UNKNOWN"          # no reaction time given
    fast = regen_disconnect_overvoltage(500e-6, 600.0, p, 850.0, reaction_time_s=0.0005)
    slow = regen_disconnect_overvoltage(500e-6, 600.0, p, 850.0, reaction_time_s=0.002)
    assert fast["claim"]["status"] == "FEASIBLE" and slow["claim"]["status"] == "INFEASIBLE"
    ramp = regen_disconnect_overvoltage(500e-6, 600.0, p, 850.0, reaction_time_s=0.0015, profile="linear_ramp_down")
    assert ramp["claim"]["status"] == "FEASIBLE"      # half the energy of a constant profile


def test_asc_closed_form(drive):
    a = asc_steady_state(drive, 12000.0)
    we = 4 * 2 * math.pi * 12000 / 60
    den = 0.015 ** 2 + we * we * 0.0002 * 0.0004
    assert a["id_A"] == pytest.approx(-we * we * 0.0004 * 0.1 / den)
    assert a["iq_A"] == pytest.approx(-we * 0.015 * 0.1 / den)
    assert a["Te_Nm"] < 0 and a["braking"]
    low = asc_steady_state(drive, 1000.0)
    assert abs(low["Tshaft_Nm"]) > abs(a["Tshaft_Nm"])   # large braking torque at low speed


def test_safe_state_screening_is_never_a_selection(drive):
    s = safe_state_screening(drive, 12000.0, 600.0, "battery_disconnected",
                             project_rules=[{"rule_id": "PRJ-1", "when": {"Vdc_below_V": 60}, "require": "FREEWHEEL",
                                             "basis": "customer rule"}])
    fw = s["candidates"][0]
    assert "UNCONTROLLED RECTIFICATION" in fw["back_emf_risk"] and fw["dc_overvoltage_risk"].startswith("HIGH")
    assert s["claim"]["status"] == "UNKNOWN"
    assert s["project_rules"][0]["applies"] is False and "not physics" in s["project_rules"][0]["kind"]
    lv = safe_state_screening(drive, 500.0, 48.0, project_rules=[{"rule_id": "PRJ-1", "when": {"Vdc_below_V": 60},
                                                                  "require": "FREEWHEEL", "basis": "customer rule"}])
    assert lv["project_rules"][0]["applies"] is True


def test_e01_one_node_thermal_fixture():
    fx = golden("semantic_boundary_cases.json")["future_only_not_MVP"][1]
    node = ThermalNode("node", FosterNetwork.one_node(fx["thermal_resistance_K_W"], fx["thermal_capacity_J_K"]),
                       80.0, (("inverter", 1.0),))
    assert temperature(node, fx["constant_loss_W"], 100.0, fx["coolant_temperature_C"]) == pytest.approx(fx["T_at_100s_C"], abs=1e-9)
    assert time_to_limit(node, fx["constant_loss_W"], fx["coolant_temperature_C"]) == pytest.approx(fx["time_to_80C_s"], abs=1e-6)


def _thermal(validated):
    prov = Provenance(DataOrigin.SYNTHETIC, "test network", "1", "validated for test" if validated else "unvalidated")
    return ThermalModel("TM", "1", (ThermalNode("junction", FosterNetwork((0.05, 0.15), (0.05, 2.0)), 150.0,
                                                (("inverter", 1 / 6),)),
                                    ThermalNode("winding", FosterNetwork((0.004, 0.01), (20.0, 300.0)), 180.0,
                                                (("copper", 1.0),))),
                        prov, validated=validated, validity=(("coolant_temp_C", (60.0, 70.0)),))


def test_thermal_duration_needs_validation(drive):
    sc = scenario(3000, 600, coolant_temp_C=65.0)
    unval = thermal_duration(drive, sc, _thermal(False), 450.0, 10.0)
    assert unval["claim"]["status"] == "UNKNOWN" and unval["claim"]["reasons"] == ["UNVALIDATED_DURATION"]
    val = thermal_duration(drive, sc, _thermal(True), 450.0, 10.0)
    assert val["claim"]["status"] == "INFEASIBLE"      # junction limit reached before 10 s at 450 N*m
    ok = thermal_duration(drive, sc, _thermal(True), 300.0, 10.0)
    assert ok["claim"]["status"] == "FEASIBLE"
    wrong_coolant = thermal_duration(drive, scenario(3000, 600, coolant_temp_C=90.0), _thermal(True), 300.0, 10.0)
    assert wrong_coolant["claim"]["status"] == "UNKNOWN"


def test_torque_availability_decreases_with_duration(drive):
    ta = torque_availability(drive, scenario(3000, 600, coolant_temp_C=65.0), _thermal(False),
                             durations_s=(1.0, 10.0, math.inf))
    t = [r["torque_Nm"] for r in ta["rows"]]
    assert t[0] >= t[1] >= t[2] and t[2] < ta["static_capability_Nm"]
    assert "not a duration rating" in ta["status_note"]
