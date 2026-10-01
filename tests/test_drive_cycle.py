"""Drive cycle (system view, item 8): standard traces against their official values, road-load physics by hand,
energy closure, regeneration / friction split, followability, CSV import and the project's vehicle section."""

import math

import pytest

from traction_workbench import api
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions import drive_cycle as dc
from traction_workbench.parsers import reducer_from_dict
from traction_workbench.project import builtin_project, check_project

MI = 1609.344

# official values (independent of the data file): UN GTR No. 15 Annex 1 (WLTC class 3b: 1800 s, 23 266 m, phases
# 3 095 / 4 756 / 7 162 / 8 254 m, v_max 131.3 km/h); US EPA dynamometer schedules (UDDS 1369 s, 7.45 mi, 56.7 mph;
# HWFET 765 s, 10.26 mi, 59.9 mph; US06 8.01 mi, 80.3 mph)
OFFICIAL = {
    "WLTC_3b": {"duration_s": 1800, "distance_m": 23266, "v_max_kmh": 131.3,
                "phases_m": {"low": 3095, "medium": 4756, "high": 7162, "extra_high": 8254}},
    "UDDS": {"duration_s": 1369, "distance_m": 7.45 * MI, "v_max_kmh": 56.7 * 1.609344},
    "HWFET": {"duration_s": 765, "distance_m": 10.26 * MI, "v_max_kmh": 59.9 * 1.609344},
    "US06": {"distance_m": 8.01 * MI, "v_max_kmh": 80.3 * 1.609344},
}


@pytest.mark.parametrize("key", sorted(OFFICIAL))
def test_builtin_traces_reproduce_the_official_values(key):
    c = dc.cycle_from_builtin(key)
    o = OFFICIAL[key]
    if "duration_s" in o:
        assert c.duration_s == o["duration_s"]
    tol = 1.0 if key == "WLTC_3b" else 0.005 * MI           # WLTC in m, EPA distances published to 0.01 mi
    assert abs(c.distance_m - o["distance_m"]) <= tol
    assert abs(max(c.v_mps) * 3.6 - o["v_max_kmh"]) <= 0.05
    for ph in c.phases:
        t0, t1 = ph["start_s"], ph["end_s"]
        d = sum(0.5 * (c.v_mps[i] + c.v_mps[i + 1]) for i in range(int(t0), int(t1)))
        assert abs(d - o["phases_m"][ph["name"]]) <= 1.0, ph["name"]


def _lossless_reducer(ratio=9.0):
    return reducer_from_dict({"ratio": ratio, "output_boundary": "wheels (test)", "speed_rpm": [0, 20000],
                              "torque_Nm": [0, 2000], "oil_temp_C": [-50, 200], "eta_forward": 1.0,
                              "eta_reverse": 1.0, "drag_coeffs": [0, 0, 0], "basis": "test: lossless"})


def _vehicle(**kw):
    d = {"mass_kg": 1500.0, "wheel_radius_m": 0.3, "J_wheels_kgm2": 0.0,
         "road_load": {"form": "abc", "A_N": 100.0, "B_N_per_mps": 1.0, "C_N_per_mps2": 0.4, "basis": "test"},
         "regen": {"share": 1.0, "min_speed_kmh": 0.0}, "aux_hv_W": 0.0}
    d.update(kw)
    return dc.vehicle_from_dict(d, J_motor_kgm2=0.0)


def _run(cycle, vehicle, reducer=None, oil=60.0, R=0.0, req=None):
    b = {**api.EXAMPLE_DRIVE_CYCLE}
    d = api._eff_drive(b)
    return dc.run_cycle(d, cycle, vehicle, reducer or _lossless_reducer(), Vdc_V=600.0, limits=api._limits(b),
                        oil_temp_C=oil, source_R_ohm=R, requirements=req)


def test_cruise_road_load_and_machine_torque_by_hand():
    v = 60 / 3.6
    cyc = dc.cycle_from_dict({"name": "cruise", "t_s": [0, 50, 100], "speed": [60, 60, 60], "unit": "km/h"})
    veh = _vehicle()
    r = _run(cyc, veh)
    F = 100.0 + 1.0 * v + 0.4 * v * v
    E = F * v * 100.0
    road = r["road_work_kWh"]
    assert road["rolling_constant"] * 3.6e6 == pytest.approx(100.0 * v * 100.0, rel=1e-12)
    assert road["linear"] * 3.6e6 == pytest.approx(1.0 * v * v * 100.0, rel=1e-12)
    assert road["aero_quadratic"] * 3.6e6 == pytest.approx(0.4 * v ** 3 * 100.0, rel=1e-12)
    assert r["energy_kWh"]["wheel_positive"] * 3.6e6 == pytest.approx(E, rel=1e-9)
    # lossless reducer, axle 1: T_m = F r / g at n = v / r * g * 60 / 2 pi
    tr = r["trace"]
    assert tr["T_m_Nm"][0] == pytest.approx(F * 0.3 / 9.0, rel=1e-9)
    assert tr["n_rpm"][0] == pytest.approx(v / 0.3 * 9.0 * 60 / (2 * math.pi), rel=1e-12)
    assert r["losses_kWh"]["reducer"] == pytest.approx(0.0, abs=1e-12)
    assert r["closure"]["relative"] < 1e-9
    assert r["followed"]["status"] == "PASS"


def test_grade_work_and_kinetic_energy_are_exact():
    # 50 km/h up a 5 % grade, then a ramp to rest: grade work m g sin(atan 0.05) v t, kinetic nets to zero
    cyc = dc.cycle_from_dict({"t_s": [0, 10, 20, 30], "speed": [0, 50, 50, 0], "unit": "km/h",
                              "grade_pct": [5, 5, 5, 5]})
    veh = _vehicle()
    r = _run(cyc, veh)
    v = 50 / 3.6
    dist = 0.5 * v * 10 + v * 10 + 0.5 * v * 10
    assert r["road_work_kWh"]["grade"] * 3.6e6 == pytest.approx(1500 * dc.G * math.sin(math.atan(0.05)) * dist,
                                                               rel=1e-12)
    assert abs(r["road_work_kWh"]["kinetic_net"]) * 3.6e6 < 1e-6
    assert r["closure"]["relative"] < 1e-9


def test_regeneration_share_and_friction_split():
    cyc = dc.cycle_from_dict({"t_s": [0, 20, 40, 52], "speed": [0, 80, 80, 0], "unit": "km/h"})
    full = _run(cyc, _vehicle())
    none = _run(cyc, _vehicle(regen={"share": 0.0, "min_speed_kmh": 0.0}))
    cut = _run(cyc, _vehicle(regen={"share": 1.0, "min_speed_kmh": 200.0}))
    avail = full["regeneration"]["available_at_wheels_kWh"]
    assert avail > 0
    assert full["regeneration"]["recovery_ratio"] > 0.5
    assert full["energy_kWh"]["friction_brakes"] < 0.05 * avail
    # no regeneration: every braking joule not taken by the drive's own drag (lossless test reducer: none) goes to
    # the friction brakes, and the battery recovers nothing
    for r in (none, cut):
        assert r["energy_kWh"]["friction_brakes"] == pytest.approx(avail, rel=1e-6)
        assert r["regeneration"]["recovered_to_battery_kWh"] == pytest.approx(0.0, abs=1e-9)
        assert r["closure"]["relative"] < 1e-9
    assert none["energy_kWh"]["battery_ocv_net"] > full["energy_kWh"]["battery_ocv_net"]


def test_battery_resistance_loss_is_R_I_squared():
    cyc = dc.cycle_from_dict({"t_s": [0, 100], "speed": [90, 90], "unit": "km/h"})
    r0 = _run(cyc, _vehicle(aux_hv_W=500.0))
    r1 = _run(cyc, _vehicle(aux_hv_W=500.0), R=0.05)
    P = r0["trace"]["P_dc_W"][0] + 500.0
    assert r1["losses_kWh"]["battery"] * 3.6e6 == pytest.approx(0.05 * (P / 600.0) ** 2 * 100.0, rel=1e-9)
    assert r1["energy_kWh"]["battery_ocv_net"] - r0["energy_kWh"]["battery_ocv_net"] == pytest.approx(
        r1["losses_kWh"]["battery"], rel=1e-9)


def test_a_trace_the_drive_cannot_deliver_is_reported_not_replanned():
    cyc = dc.cycle_from_dict({"t_s": [0, 1, 30], "speed": [0, 100, 100], "unit": "km/h"})   # 0 -> 100 km/h in 1 s
    r = _run(cyc, _vehicle(), req={"consumption_Wh_per_km_max": 1000})
    assert r["followed"]["status"] == "FAIL"
    assert r["followed"]["count"] >= 1 and r["followed"]["not_delivered_s"] >= 1.0
    assert r["energy_kWh"]["shortfall_at_wheels"] > 0
    ver = {v["id"]: v["status"] for v in r["verdicts"]}
    assert ver["followed"] == "FAIL"
    it = r["followed"]["intervals"][0]
    assert it["T_out_delivered_Nm"] < it["T_out_demand_Nm"]
    assert r["closure"]["relative"] < 1e-9                   # the ledger closes on what was delivered


def test_unknown_reducer_domain_makes_the_cycle_incomplete():
    cyc = dc.cycle_from_dict({"t_s": [0, 10], "speed": [50, 50], "unit": "km/h"})
    red = reducer_from_dict(api.EXAMPLE_DRIVE_CYCLE["reducer"])          # oil 20 .. 120 degC
    r = _run(cyc, _vehicle(), reducer=red, oil=150.0, req={"consumption_Wh_per_km_max": 200})
    assert not r["complete"]
    assert r["consumption_Wh_per_km"]["battery_ocv"] is None
    assert r["followed"]["status"] == "UNKNOWN"
    assert {v["id"]: v["status"] for v in r["verdicts"]}["consumption"] == "UNKNOWN"
    assert "oil temperature" in r["unknown"]["reasons"][0]


def test_wltc_on_the_builtin_project_closes_and_reports_per_phase():
    r = api.drive_cycle({})
    assert r["complete"] and r["followed"]["status"] == "PASS"
    assert r["closure"]["relative"] < 1e-9
    names = [p["name"] for p in r["phases"]]
    assert names == ["low", "medium", "high", "extra_high"]
    w = [p["Wh_per_km_battery_ocv"] for p in r["phases"]]
    assert w[-1] > w[0]                                   # aerodynamics: the extra-high phase costs most per km
    assert sum(p["distance_km"] for p in r["phases"]) == pytest.approx(r["cycle"]["distance_km"], rel=1e-12)
    total = sum(r["losses_kWh"].values())
    assert total > 0 and r["range_km"] == pytest.approx(75e3 / r["consumption_Wh_per_km"]["battery_ocv"])
    e = r["energy_kWh"]
    assert e["battery_ocv_net"] == pytest.approx(e["battery_traction_out"] - e["battery_regen_in"], rel=1e-12)


def test_csv_import_and_input_validation():
    c = dc.cycle_from_csv("time,speed_kmh,grade_pct\n0,0,0\n1,3.6,0\n2,7.2,1\n3,0,0\n", "csv test")
    assert c.v_mps[2] == pytest.approx(2.0)
    assert c.grade[2] == pytest.approx(0.01)
    with pytest.raises(InputValidationError, match="unit"):
        dc.cycle_from_csv("t,speed\n0,1\n1,2\n2,3\n")
    with pytest.raises(InputValidationError, match="increasing"):
        dc.cycle_from_dict({"t_s": [0, 1, 1], "speed": [0, 1, 2]})
    with pytest.raises(InputValidationError, match=">= 0"):
        dc.cycle_from_dict({"t_s": [0, 1], "speed": [0, -1]})
    with pytest.raises(InputValidationError, match="basis"):
        dc.vehicle_from_dict({"mass_kg": 1000, "wheel_radius_m": 0.3, "road_load": {"form": "abc", "A_N": 1}})


def test_project_vehicle_section_and_consistency_rule():
    prj = builtin_project()
    assert prj.has("vehicle")
    assert prj.vehicle()["J_motor_kgm2"] == prj.driveline_rom()["Jm_kgm2"]      # from the ROM unless declared
    assert "vehicle" in prj.usage("drive_cycle")["sections"]
    ex = api.example("DRIVE_CYCLE", prj)
    assert ex["vehicle"]["mass_kg"] == prj.data("vehicle")["mass_kg"]
    bad = prj.with_section("vehicle", {**prj.data("vehicle"), "wheel_radius_m": 0.35})
    f = {x["rule"]: x["status"] for x in check_project(bad)["findings"]}
    assert f["PRJ-13"] == "INCONSISTENT"
    assert f.get("PRJ-14") is None                      # the vehicle takes the ROM's rotor inertia


def test_physical_road_load_form():
    veh = dc.vehicle_from_dict({"mass_kg": 1000, "wheel_radius_m": 0.3,
                                "road_load": {"form": "physical", "c_rr": 0.01, "CdA_m2": 0.6, "rho_kg_m3": 1.2,
                                              "basis": "test"}})
    c, lin, q = veh.road_force(20.0, 0.0)
    assert c == pytest.approx(1000 * dc.G * 0.01) and lin == 0.0 and q == pytest.approx(0.5 * 1.2 * 0.6 * 400)
