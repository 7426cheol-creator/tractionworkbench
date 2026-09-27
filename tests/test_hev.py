"""HEV on one DC bus: addendum acceptance cases H-01..H-05, boost / battery sanity and the section 9.4 example."""

import math

import numpy as np
import pytest

from traction_workbench.errors import InputValidationError
from traction_workbench.extensions import hev as H
from traction_workbench.scenario import DcSourceLimits
from traction_workbench.spec_fixtures import synthetic_drive

INF = math.inf


def battery(dis=100e3, chg=30e3, ocv=400.0, R=0.05, uv=250.0):
    return H.Battery(ocv, R, DcSourceLimits(discharge_power_max_W=dis, charge_power_max_W=chg,
                                            discharge_current_max_A=INF, charge_current_max_A=INF), uv_min_V=uv,
                     basis="synthetic test battery")


@pytest.fixture(scope="module")
def joint():
    d = synthetic_drive()
    m1 = H.BusMachine("EM1", d, 3000.0, "generator / starter")
    m2 = H.BusMachine("EM2", d, 6000.0, "traction")
    return H.joint_torque_set([m1, m2], 600.0, battery(chg=0.0), aux_W=1500.0, n_levels=11, request=(250.0, -110.0))


# ------------------------------------------------------------------ H-01 branch stress vs net

def test_h01_branch_powers_are_kept_next_to_the_net(joint):
    r = joint["request"]
    p1, p2 = r["branch_P_dc_W"]
    assert p1 > 0 > p2                                                  # one consumes, one generates
    assert r["net_machines_W"] == pytest.approx(p1 + p2, rel=1e-12)
    assert r["circulating_W"] == pytest.approx(min(abs(p1), abs(p2)), rel=1e-12)
    assert r["P_source_W"] == pytest.approx(p1 + p2 + 1500.0, rel=1e-12)
    assert abs(r["net_machines_W"]) < 0.3 * p1                          # a small net hides large branch powers
    assert r["branch_I_dc_A"][0] == pytest.approx(p1 / 600.0)
    # zero charge acceptance does not forbid an internal generator -> motor transfer with a net discharge
    assert r["status"] == "FEASIBLE"


# ------------------------------------------------------------------ H-02 separately feasible, jointly not

def test_h02_joint_set_is_not_the_box_of_separate_maxima(joint):
    assert joint["box_cells_feasible_separately"] > joint["joint_cells_feasible"]
    st = np.array(joint["status"], dtype=object)
    t1, t2 = np.array(joint["T1_Nm"]), np.array(joint["T2_Nm"])
    tabs = joint["tables"]
    ok1 = [r["status"] == "FEASIBLE" for r in tabs["EM1"]]
    ok2 = [r["status"] == "FEASIBLE" for r in tabs["EM2"]]
    both_alone_joint_no = [(i, j) for i in range(t1.size) for j in range(t2.size)
                           if ok1[i] and ok2[j] and st[i, j] == "INFEASIBLE"]
    assert both_alone_joint_no, "expected pairs feasible alone but not together"
    i, j = both_alone_joint_no[0]
    assert "power" in joint["reason"][i][j] or "current" in joint["reason"][i][j]
    # the conditional envelope depends on the other machine's torque
    env = {round(e["T1_Nm"], 6): e["T2_segments_Nm"] for e in joint["conditional_envelope"]}
    hi = env[round(float(t1.max()), 6)]
    zero_row = env[round(float(t1[np.argmin(np.abs(t1))]), 6)]
    top = lambda segs: max((s[1] for s in segs), default=-INF)
    assert top(hi) < top(zero_row)


# ------------------------------------------------------------------ H-03 cranking replay

def _flat_load(J=0.25):
    return H.CrankLoad((0.0, 90.0, 180.0), (0.0, 0.0, 0.0), 180.0, J_kgm2=J, basis="flat test load")


def test_h03_constant_torque_inertia_matches_the_analytic_acceleration():
    d = synthetic_drive()
    T, ratio, J, n_t = 40.0, 2.0, 0.25, 600.0
    r = H.cranking_replay(d, ratio, _flat_load(J), battery(), T, n_t, 1.0, V_floor_V=300.0, theta0_deg=[0.0],
                          dt_s=1e-4)
    t_exact = J * (n_t * 2 * math.pi / 60) / (T * ratio)
    assert r["runs"][0]["reached_s"] == pytest.approx(t_exact, abs=2e-4)
    assert r["claim"]["status"] == "UNKNOWN" and r["claim"]["reasons"] == ["SAMPLED_COVERAGE"]   # sampled, not proof


def test_h03_compression_peak_and_initial_angle_decide():
    d = synthetic_drive()
    load = H.CrankLoad((0, 30, 60, 90, 120, 150, 180), (0, 40, 90, 60, -40, -60, 0), 180.0, f0_Nm=25.0,
                       f1_Nm_s=0.3, J_kgm2=0.25, basis="synthetic crank trace")
    weak = H.cranking_replay(d, 2.5, load, battery(), 40.0, 800.0, 0.6, V_floor_V=300.0,
                             theta0_deg=[0, 30, 60, 90, 120, 150])
    ok = [r["ok"] for r in weak["runs"]]
    assert weak["claim"]["status"] == "INFEASIBLE" and not all(ok)       # a counterexample angle exists
    strong = H.cranking_replay(d, 2.5, load, battery(), 120.0, 800.0, 0.6, V_floor_V=300.0,
                               theta0_deg=[0, 30, 60, 90, 120, 150])
    assert all(r["ok"] for r in strong["runs"]) and strong["claim"]["status"] == "UNKNOWN"


def test_h03_source_sag_and_traction_reserve():
    d = synthetic_drive()
    load = _flat_load()
    sag = H.cranking_replay(d, 2.5, load, battery(ocv=320.0, R=0.6), 120.0, 800.0, 0.6, V_floor_V=300.0,
                            theta0_deg=[0.0])
    assert sag["claim"]["status"] == "INFEASIBLE"
    assert "floor" in " ".join(sag["runs"][0]["failures"]) or "collapse" in " ".join(sag["runs"][0]["failures"])
    res = H.cranking_replay(d, 2.5, load, battery(dis=40e3), 120.0, 800.0, 0.6, V_floor_V=300.0, theta0_deg=[0.0],
                            traction_reserve_W=30e3)
    assert res["claim"]["status"] == "INFEASIBLE" and "reserve" in " ".join(res["runs"][0]["failures"])


def test_h03_crank_load_needs_a_basis():
    with pytest.raises(InputValidationError):
        H.CrankLoad((0.0, 180.0), (0.0, 0.0), 180.0, basis="")


# ------------------------------------------------------------------ H-04 mechanical constraints

def test_h04_planetary_kinematics_and_torque_ratio():
    sp = H.planetary_speeds(30, 78, ring=3000.0, carrier=2000.0)
    assert 30 * sp["sun"] + 78 * sp["ring"] == pytest.approx(108 * sp["carrier"])
    tq = H.planetary_torques(30, 78, "carrier", -150.0)
    assert tq["sun"] / 30 == pytest.approx(tq["ring"] / 78) == pytest.approx(-tq["carrier"] / 108)
    ok = H.planetary_check(30, 78, sp, tq)
    assert ok["status"] == "FEASIBLE" and abs(ok["power_residual_W"]) < 1e-6
    # a split that is kinematically fine but has the wrong torque ratio is rejected
    wrong = dict(tq, ring=tq["ring"] * 1.1)
    assert H.planetary_check(30, 78, sp, wrong)["status"] == "INFEASIBLE"
    # overspeed of the sun machine
    fast = H.planetary_speeds(30, 78, ring=-2000.0, carrier=4000.0)
    assert H.planetary_check(30, 78, fast, H.planetary_torques(30, 78, "carrier", -50.0),
                             limits_rpm={"sun": 12000.0})["status"] == "INFEASIBLE"
    with pytest.raises(InputValidationError):
        H.planetary_speeds(78, 30, ring=1.0, carrier=1.0)


# ------------------------------------------------------------------ H-05 load rejection energy

def test_h05_example_9_4_capacitor_margin_is_counted_once():
    r = H.load_rejection(500.0, 400.0, 450.0, [70e3], [20e3], t_react_s=1e-3)
    assert r["E_margin_J"] == pytest.approx(0.5 * 500e-6 * (450 ** 2 - 400 ** 2)) == pytest.approx(10.625)
    assert r["time_to_limit_s"] == pytest.approx(212.5e-6)
    assert r["claim"]["status"] == "INFEASIBLE"
    fast = H.load_rejection(500.0, 400.0, 450.0, [70e3], [20e3], t_react_s=100e-6)
    assert fast["claim"]["status"] == "FEASIBLE" and fast["V_peak_V"] < 450.0
    # two generating branches share ONE margin: time to limit uses the summed excess
    two = H.load_rejection(500.0, 400.0, 450.0, [70e3, 30e3], [20e3], t_react_s=0.0)
    assert two["time_to_limit_s"] == pytest.approx(10.625 / 80e3)
    # energy ledger closes: 1/2 C (V_end^2 - V0^2) = integral of the excess
    tr = r["trace"]
    E = (getattr(np, "trapezoid", None) or np.trapz)(tr["P_excess_W"], tr["t_s"])
    assert 0.5 * 500e-6 * (tr["V_V"][-1] ** 2 - 400 ** 2) == pytest.approx(E, rel=1e-9)


# ------------------------------------------------------------------ boost / battery

def test_boost_duty_current_and_direction():
    b = H.BoostStage(D_max=0.5, I_L_max_A=300.0, a0_W=100.0, a2_W_per_A2=0.01, bidirectional=False, basis="test")
    r = b.battery_side(60e3, 600.0, 400.0)
    assert r["duty"] == pytest.approx(1 - 400 / 600) and not r["problems"]
    assert r["P_bat_W"] == pytest.approx(60e3 + 100.0 + 0.01 * r["I_L_A"] ** 2)
    assert r["I_L_A"] * 400.0 == pytest.approx(r["P_bat_W"])
    assert b.battery_side(60e3, 900.0, 400.0)["problems"]                 # duty above D_max
    assert b.battery_side(-10e3, 600.0, 400.0)["problems"]               # regen through a unidirectional boost
    assert b.battery_side(200e3, 600.0, 400.0)["problems"]               # inductor current


def test_battery_terminal_voltage_and_collapse():
    bt = battery(ocv=400.0, R=0.1)
    v, i = bt.terminal(50e3)
    assert v * i == pytest.approx(50e3) and v == pytest.approx(400.0 - 0.1 * i)
    assert bt.terminal(400.0 ** 2 / (4 * 0.1) * 1.01) == (None, None)
