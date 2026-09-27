"""Plot data must re-express the production model exactly (no new physics, no silent approximations)."""

import math

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench import spec_fixtures as sf
from traction_workbench.extensions.safe_state import asc_steady_state
from traction_workbench.io import drive_from_dict
from traction_workbench.physics import DriveKernel
from traction_workbench.scenario import Scenario
from traction_workbench.solvers.policy import PolicyEvaluator
from traction_workbench.viz import design as DS
from traction_workbench.viz import maps as M
from traction_workbench.viz import operating as O
from traction_workbench.viz import safety as SF
from traction_workbench.viz import sweeps as SW


@pytest.fixture(scope="module")
def drive():
    return sf.synthetic_drive()


@pytest.fixture(scope="module")
def limits():
    return sf.synthetic_limits()


def _pv(drive, limits, n, vdc, T):
    sc = Scenario("t", n, vdc, limits)
    sol = PolicyEvaluator(drive, sc).solve(T)
    return O.point_view(drive, sc, sol.point.id_A, sol.point.iq_A, T)


def park(abc, theta):
    """Independent amplitude-invariant forward Park (not the production inverse)."""
    a, b, c = abc
    k = 2.0 / 3.0
    d = k * (a * np.cos(theta) + b * np.cos(theta - 2 * np.pi / 3) + c * np.cos(theta + 2 * np.pi / 3))
    q = -k * (a * np.sin(theta) + b * np.sin(theta - 2 * np.pi / 3) + c * np.sin(theta + 2 * np.pi / 3))
    return d, q


@pytest.mark.parametrize("n, T", [(12000, 150.0), (12000, -80.0), (3000, 400.0), (-6000, 100.0)])
def test_waveforms_reexpress_the_operating_point(drive, limits, n, T):
    pv = _pv(drive, limits, n, 600.0, T)
    pt = pv.point
    w = O.waveforms(pv)
    d, q = park(w["i_abc"], w["theta_rad"])
    assert np.allclose(d, pt.id_A, atol=1e-9 * pt.i_peak_A) and np.allclose(q, pt.iq_A, atol=1e-9 * pt.i_peak_A)
    d, q = park(w["v_abc"], w["theta_rad"])
    assert np.allclose(d, pt.vd_V, atol=1e-9 * pt.v_peak_V) and np.allclose(q, pt.vq_V, atol=1e-9 * pt.v_peak_V)
    assert np.allclose(w["p_inst_W"], pt.Pac_W, rtol=1e-12, atol=1e-6)          # balanced: sum v*i = P_ac
    assert np.abs(w["i_abc"]).max() == pytest.approx(pt.i_peak_A, rel=1e-4)
    assert np.abs(w["v_ll"]).max() == pytest.approx(math.sqrt(3) * pt.v_peak_V, rel=1e-4)
    # direction of rotation of the current space vector follows the sign of omega_e
    ia, ib, ic = w["i_abc"]
    ang = np.unwrap(np.arctan2((ib - ic) / math.sqrt(3), (2 * ia - ib - ic) / 3))
    assert np.sign(ang[-1] - ang[0]) == np.sign(pt.omega_e)


def test_svpwm_duty_maps_the_voltage_reserve(drive, limits):
    pv = _pv(drive, limits, 12000, 600.0, 150.0)                 # voltage ACTIVE at the policy point
    w = O.waveforms(pv, samples=20001)
    r = pv.kernel.reserve_fraction
    lo, hi = w["duty_extremes"]
    assert lo == pytest.approx(0.5 * r, abs=2e-6) and hi == pytest.approx(1 - 0.5 * r, abs=2e-6)
    assert w["m_linear"] == pytest.approx(1 - r, abs=1e-9)
    vdc = pv.point.Vdc_V                                            # line-to-line reconstruction from duties
    assert np.allclose((w["duty_abc"][0] - w["duty_abc"][1]) * vdc, w["vcmd_abc"][0] - w["vcmd_abc"][1], atol=1e-6)


def test_phasor_chain_power_factor_and_torque_split(drive, limits):
    pv = _pv(drive, limits, 12000, 600.0, 150.0)
    ph = O.phasor(pv)
    s = np.sum([np.array(v) for _k, v in ph["chain"]], axis=0)
    assert np.allclose(s, ph["v"], atol=1e-9)
    assert ph["power_factor"] == pytest.approx(ph["power_factor_check"], abs=1e-12)
    ts = ph["torque_split_Nm"]
    assert ts["magnet"] + ts["reluctance"] == pytest.approx(pv.point.Te_Nm, rel=1e-12)
    chain = O.power_chain(pv.point)
    v = {r["key"]: r["value_W"] for r in chain}
    assert v["P_dc"] + v["P_inv"] == pytest.approx(v["P_ac"], abs=1e-6)
    assert v["P_ac"] + v["P_cu"] == pytest.approx(v["P_em"], abs=1e-6)
    assert v["P_em"] + v["P_rot"] == pytest.approx(v["P_shaft"], abs=1e-6)


def test_mtpa_locus_contains_low_speed_policy_points(drive, limits):
    k = DriveKernel(drive, Scenario("m", 1000.0, 600.0, limits))
    d, q = M.mtpa_locus(k)["motoring"]
    for T in (50.0, 200.0, 400.0):
        pt = PolicyEvaluator(drive, Scenario("m", 1000.0, 600.0, limits)).solve(T).point
        assert np.interp(pt.iq_A, q, d) == pytest.approx(pt.id_A, abs=2e-3)


def test_numeric_mtpa_satisfies_tangency_on_flux_map(limits):
    fm = drive_from_dict({"builtin": "MANUFACTURED_FLUX_MAP_TEST_DRIVE"})
    k = DriveKernel(fm, Scenario("m", 1000.0, 600.0, limits))
    d, q = M.mtpa_locus(k)["motoring"]
    assert len(d) > 20
    h = 0.05
    worst = 0.0
    for di, qi in list(zip(d, q))[5:-5:7]:
        tq = lambda a, b: float(k.evaluate(a, b)["tem"])
        dTd = (tq(di + h, qi) - tq(di - h, qi)) / (2 * h)
        dTq = (tq(di, qi + h) - tq(di, qi - h)) / (2 * h)
        tang = (-qi * dTd + di * dTq) / (math.hypot(di, qi) * math.hypot(dTd, dTq))   # dT/dbeta, normalised
        worst = max(worst, abs(tang))
    assert worst < 5e-3


@pytest.mark.parametrize("T", [150.0, -100.0])
def test_base_speed_curve_is_the_field_weakening_onset(drive, limits, T):
    nb = M.base_speed_curve(drive, limits, 600.0, [T])["speed_rpm"][0]
    below = SW.speed_sweep(drive, limits, T, 600.0, speeds=[nb - 2.0])
    above = SW.speed_sweep(drive, limits, T, 600.0, speeds=[nb + 2.0])
    assert not below["voltage_active"][0] and above["voltage_active"][0]
    assert below["v_margin_V"][0] > 0 and above["v_margin_V"][0] == pytest.approx(0.0, abs=1e-6)


def test_envelope_reproduces_golden_capability(drive, limits):
    env = SW.envelope(drive, limits, 600.0, speeds=[6000.0, 12000.0])
    assert env["max"]["T_Nm"][0] == pytest.approx(306.8288961202995, abs=1e-3)
    assert env["max"]["T_Nm"][1] == pytest.approx(152.5550589346928, abs=1e-3)
    assert env["min"]["T_Nm"][1] == pytest.approx(-83.71107155914689, abs=1e-3)
    assert set(env["max"]["active"][1]) >= {"VOLTAGE", "DC_DISCHARGE_POWER"}


def test_grid_estimate_never_exceeds_the_certified_capability(drive, limits):
    k = DriveKernel(drive, Scenario("g", 12000.0, 600.0, limits))
    g = SW.grid_extreme(k, +1, include_dc=True)
    assert 152.5550589346928 - 0.5 < g[0] <= 152.5550589346928 + 1e-6


def test_tn_map_nodes_are_policy_points(drive, limits):
    mp = M.tn_map(drive, limits, 600.0, [3000.0, 12000.0], [100.0, 150.0, 170.0, -90.0])
    st = mp["status"]
    assert st[0, 0] == SW.OK and st[1, 1] == SW.OK                      # 100 N*m @ 3000, 150 N*m @ 12000
    assert st[2, 1] == SW.DC                                           # 170 N*m @ 12000: electrically OK, DC violated
    eta = mp["grids"]["eta"]
    assert np.all((eta[np.isfinite(eta)] > 0) & (eta[np.isfinite(eta)] < 1))
    pt = PolicyEvaluator(drive, Scenario("m", 12000.0, 600.0, limits)).solve(150.0).point
    assert mp["grids"]["id_A"][1, 1] == pytest.approx(pt.id_A, abs=1e-9)


def test_idiq_plane_marks_the_policy_point(drive, limits):
    pl = M.idiq_plane(drive, limits, 12000.0, 600.0, 150.0, resolution=201)
    pt = PolicyEvaluator(drive, Scenario("m", 12000.0, 600.0, limits)).solve(150.0).point
    assert pl["policy_point"] == (pt.id_A, pt.iq_A)
    j = int(np.argmin(np.abs(pl["x"] - pt.id_A)))
    i = int(np.argmin(np.abs(pl["y"] - pt.iq_A)))
    assert abs(pl["T"][i, j] - 150.0) < 6.0 and abs(pl["V"][i, j] - pl["budget_V"]) < 6.0


def test_capability_vs_vdc_brackets_the_sized_minimum(drive, limits):
    sc = Scenario("s", 12000.0, 450.0, limits)
    cv = DS.capability_vs_parameter(drive, sc, "Vdc_V", [490.0, 497.0, 498.5, 520.0], T_request=150.0)
    assert list(cv["status"]) == [SW.NONE, SW.NONE, SW.OK, SW.OK] or (cv["status"][0] != SW.OK and cv["status"][2] == SW.OK)
    assert cv["capability_Nm"][1] < 150.0 < cv["capability_Nm"][2]     # sized minimum 497.68 V lies in between


def test_screening_curves_use_the_extension_equations(drive):
    dis = api.discharge({"C_uF": 500, "V0_V": 600, "Vf_V": 60, "t_target_s": 2})
    c = SF.discharge_curve(dis)
    assert np.interp(dis["t_reach_s"], c["t_s"], c["V"]) == pytest.approx(60.0, rel=1e-3)
    ov = api.overvoltage({"C_uF": 500, "V1_V": 600, "V_limit_V": 850, "speed_rpm": 12000, "torque_Nm": -80,
                          "reaction_time_ms": 2})
    c = SF.overvoltage_curve(ov)
    assert c["V"].max() == pytest.approx(ov["V_peak_V"], rel=1e-9)
    assert np.interp(ov["time_to_limit_constant_power_s"], c["t_s"], c["V_unlimited"]) == pytest.approx(850.0, rel=1e-3)
    tl = SF.ftti_timeline(api.timing(api.EXAMPLE_TIMING))
    assert tl["worst_event_s"]["safe_state"] == pytest.approx(tl["worst_s"], rel=1e-12)
    assert {"SW_REACT", "SYS_FRTI"} <= tl["duplicates"]
    asc = SF.asc_vs_speed(drive, 600.0, speeds=[12000.0])
    assert asc["i_peak_A"][0] == pytest.approx(asc_steady_state(drive, 12000.0, 600.0)["i_peak_A"], rel=1e-12)
    assert asc["ucg_onset_rpm"] == pytest.approx(8269.933431326881, rel=1e-9)


def test_thermal_curves_hit_the_limit_at_the_time_to_limit():
    th = api.thermal({"speed_rpm": 3000, "Vdc_V": 600, "coolant_temp_C": 65, "torque_Nm": 450, "duration_s": 10})
    nodes = th["request"]["nodes"]
    model = api._thermal_model(None)
    j = next(i for i, n in enumerate(nodes) if isinstance(n["time_to_limit_s"], float))
    ttl = nodes[j]["time_to_limit_s"]
    c = SF.thermal_curves(model, nodes, 65.0, 3 * ttl, samples=4000)["curves"][j]
    assert np.interp(ttl, SF.thermal_curves(model, nodes, 65.0, 3 * ttl, samples=4000)["t_s"], c["T_C"]) == \
        pytest.approx(c["limit_C"], abs=0.05)
    assert ttl == pytest.approx(5.47, abs=0.01)
