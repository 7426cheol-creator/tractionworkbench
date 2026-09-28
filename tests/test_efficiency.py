"""Efficiency by control volume (module-efficiency addendum): power-ledger fixtures E-01..E-06, the F-E01..F-E03
regressions, reducer directions, mission energy and the module-to-result consistency (one physics everywhere).

The fixtures are conservation arithmetic (independent of the implementation); they verify wiring and semantics,
not the physical accuracy of any product.
"""

import math
from dataclasses import replace

import pytest

from traction_workbench import api
from traction_workbench import spec_fixtures as sf
from traction_workbench.analysis import efficiency as E
from traction_workbench.errors import InputValidationError
from traction_workbench.physics import DriveKernel, evaluate_point
from traction_workbench.scenario import DcSourceLimits, Scenario
from traction_workbench.viz.sweeps import point_fields


def _eta(res, name):
    r = res[name]
    return r["eta"] if r["status"] == E.DEFINED else r["status"]


def test_E01_motoring_ledger():
    r = E.five_boundaries(100e3, 97e3, 92e3, 88e3)
    assert _eta(r, "inverter") == pytest.approx(0.97, abs=1e-12)
    assert _eta(r, "motor") == pytest.approx(0.94845361, abs=1e-8)
    assert _eta(r, "inverter_motor") == pytest.approx(0.92, abs=1e-12)
    assert _eta(r, "reducer") == pytest.approx(0.95652174, abs=1e-8)
    assert _eta(r, "edrive") == pytest.approx(0.88, abs=1e-12)
    assert [r[k]["loss_W"] for k in ("inverter", "motor", "reducer")] == pytest.approx([3e3, 5e3, 4e3])
    assert all(abs(v) < 1e-15 for v in r["telescoping_residuals"].values()) and len(r["telescoping_residuals"]) == 2


def test_E02_regeneration_ledger_uses_the_reverse_definitions():
    r = E.five_boundaries(-87e3, -90e3, -95e3, -100e3)
    assert _eta(r, "reducer") == pytest.approx(0.95, abs=1e-12)
    assert _eta(r, "motor") == pytest.approx(0.94736842, abs=1e-8)
    assert _eta(r, "inverter") == pytest.approx(0.96666667, abs=1e-8)
    assert _eta(r, "inverter_motor") == pytest.approx(0.91578947, abs=1e-8)
    assert _eta(r, "edrive") == pytest.approx(0.87, abs=1e-12)
    assert r["inverter"]["definition"] == "|P_dc|/|P_ac|" and r["inverter"]["direction"] == "reverse"
    assert [r[k]["loss_W"] for k in ("reducer", "motor", "inverter")] == pytest.approx([5e3, 5e3, 3e3])


def test_E03_standstill_keeps_the_inverter_terminal_ratio():
    r = E.five_boundaries(6300.0, 4500.0, 0.0, 0.0)
    assert _eta(r, "inverter") == pytest.approx(0.71428571, abs=1e-8)
    assert r["motor"]["status"] == E.NA and r["edrive"]["status"] == E.NA and r["inverter_motor"]["status"] == E.NA
    assert r["motor"]["loss_W"] == pytest.approx(4500.0)


def test_E04_missing_inverter_loss_keeps_the_motor_boundary_F_E01_F_E02():
    d = sf.synthetic_drive()
    d_noinv = replace(d, inverter=replace(d.inverter, loss=None))
    pt = evaluate_point(DriveKernel(d_noinv, Scenario("x", 3000.0, 600.0, sf.synthetic_limits())), -100.0, 300.0)
    assert pt.Pdc_W is None and pt.Pinv_W is None
    f = point_fields(pt)
    assert f["P_loss_W"] is None                                          # never 'total' with a missing term
    assert f["P_loss_known_W"] == pytest.approx(pt.Pcu_W + pt.Prot_W)
    assert f["eta_motor"] == pytest.approx(pt.Pshaft_W / pt.Pac_W) and f["eta_inverter"] is None
    led = E.point_ledger(pt, d_noinv)
    b = led["boundaries"]
    assert b["motor"]["status"] == E.DEFINED and b["inverter"]["status"] == E.UNKNOWN
    assert b["inverter_motor"]["status"] == E.UNKNOWN
    assert led["loss_total_W"] is None and any("inverter" in x for x in led["loss_unknown_items"])
    # standstill point of the full drive: the DC -> AC ratio is kept, the motor efficiency is N/A
    ps = evaluate_point(DriveKernel(d, Scenario("x", 0.0, 600.0, sf.synthetic_limits())), -100.0, 300.0)
    fs = point_fields(ps)
    assert fs["eta_inverter"] == pytest.approx(ps.Pac_W / ps.Pdc_W) and fs["eta_motor"] is None
    ls = E.point_ledger(ps, d)
    assert "standstill" in ls["boundaries"]["inverter"]["qualifier"]


def test_E05_mixed_flow_braking_without_recovery():
    d = sf.synthetic_drive()
    pt = evaluate_point(DriveKernel(d, Scenario("x", 1000.0, 600.0, sf.synthetic_limits())), 0.0, -1.0)
    assert pt.Pshaft_W == pytest.approx(-84.764307, abs=1e-6)
    assert pt.Pac_W == pytest.approx(-62.809353, abs=1e-6)
    assert pt.Pdc_W == pytest.approx(137.198647, abs=1e-6)
    b = E.point_ledger(pt, d)["boundaries"]
    assert b["motor"]["status"] == E.DEFINED and b["motor"]["eta"] == pytest.approx(0.74099, abs=1e-5)
    assert b["inverter"]["status"] == E.NA and "mixed flow" in b["inverter"]["reason"]
    assert b["inverter_motor"]["status"] == E.NA
    assert b["inverter"]["loss_W"] == pytest.approx(pt.Pdc_W - pt.Pac_W)        # both ports feed the dissipation


def test_E06_mission_directions_are_energy_ratios_not_net_ratios():
    h = 3600.0
    segs = [{"duration_s": h, "P_dc": 10e3, "P_ac": 9.6e3, "P_m": 9.3e3, "P_o": 9e3},
            {"duration_s": h, "P_dc": -4e3, "P_ac": -4.3e3, "P_m": -4.7e3, "P_o": -5e3},
            {"duration_s": h, "P_dc": 1e3, "P_ac": 0.0, "P_m": 0.0, "P_o": 0.0}]
    r = E.mission_energy(segs)
    kwh = 3.6e6
    assert r["eta_traction"] == pytest.approx(0.9) and r["eta_regeneration"] == pytest.approx(0.8)
    assert r["E_dc_net_J"] / kwh == pytest.approx(7.0) and r["E_out_net_J"] / kwh == pytest.approx(4.0)
    assert r["segments"]["idle"]["dc_in"] / kwh == pytest.approx(1.0)
    assert "not a conversion efficiency" in r["note"]


def test_reverse_rotation_is_classified_by_power_sign():
    fwd = E.five_boundaries(50e3, 48e3, 46e3, 44e3)
    # reverse rotation, motoring: negative speed and negative torque give the same positive powers
    d = sf.synthetic_drive()
    pt = evaluate_point(DriveKernel(d, Scenario("x", -3000.0, 600.0, sf.synthetic_limits())), -50.0, -200.0)
    assert pt.Te_Nm < 0 and pt.Pshaft_W > 0
    b = E.point_ledger(pt, d)["boundaries"]
    assert b["inverter_motor"]["direction"] == "forward" == fwd["inverter_motor"]["direction"]


def test_no_clamping_no_epsilon_and_percentage_points():
    bad = E.boundary_eta(100.0, 101.0, 1e-9)
    assert bad["status"] == E.INCONSISTENT and bad["eta"] == pytest.approx(1.01)
    unsure = E.boundary_eta(5.0, 3.0, 1e-9, unc_in_W=10.0)
    assert unsure["status"] == E.UNKNOWN and unsure["eta"] is None
    assert E.boundary_eta(-5.0, 3.0, 1e-9)["status"] == E.INCONSISTENT          # power leaving at both ports
    ch = E.efficiency_change(0.975, 0.980)
    assert ch["delta_percentage_points"] == pytest.approx(0.5)
    assert ch["relative_loss_change"] == pytest.approx(-0.2)                   # 2.5 % -> 2.0 % of the input
    out = E.efficiency_change(0.975, 0.980, "same output power")
    assert out["relative_loss_change"] != pytest.approx(-0.2)                  # a different quantity


def test_reducer_is_directional_and_never_assumed_ideal():
    red = api._reducer(api.EXAMPLE_REDUCER)
    fw = red.output_from_motor(6000.0, 150.0, 80.0)
    w = 6000.0 * 2 * math.pi / 60
    Pd = (0.15 + 2e-4 * w) * w
    assert fw["P_o_W"] == pytest.approx(0.975 * (150.0 * w - Pd))
    rv = red.output_from_motor(6000.0, -150.0, 80.0)
    assert rv["P_o_W"] == pytest.approx(-(150.0 * w + Pd) / 0.970)
    for T_o in (1200.0, -1000.0):                                               # inverse at the output side
        tm = red.motor_torque_for_output(6000.0, T_o, 80.0)["T_m_Nm"]
        assert red.output_from_motor(6000.0, tm, 80.0)["T_o_Nm"] == pytest.approx(T_o, rel=1e-12)
    assert red.output_from_motor(6000.0, 150.0, None)["status"] == E.UNKNOWN       # oil temperature not stated
    assert red.output_from_motor(6000.0, 150.0, 150.0)["status"] == E.UNKNOWN      # outside the validated range
    with pytest.raises(InputValidationError):
        E.ReducerModel(9.0, "out", (0, 1e4), (0, 400), (20, 120), eta_forward=0.97, basis="x")   # reverse missing
    d = sf.synthetic_drive()
    pt = evaluate_point(DriveKernel(d, Scenario("x", 6000.0, 600.0, sf.synthetic_limits())), -100.0, 250.0)
    led = E.point_ledger(pt, d)                                                  # no reducer declared
    assert led["boundaries"]["reducer"]["status"] == E.UNKNOWN and led["boundaries"]["edrive"]["status"] == E.UNKNOWN


def test_split_energy_is_exact_at_zero_crossings():
    ep, en = E.split_energy([0.0, 2.0], [1.0, -1.0])
    assert ep == pytest.approx(0.5) and en == pytest.approx(0.5)
    ep, en = E.split_energy([0.0, 1.0, 3.0], [2.0, 2.0, -2.0])
    assert ep == pytest.approx(2.0 + 1.0) and en == pytest.approx(1.0)


def test_api_point_map_and_mission_run_with_the_module_model():
    r = api.efficiency({})
    b = r["ledger"]["boundaries"]
    assert all(b[k]["status"] == E.DEFINED for k in ("inverter", "motor", "inverter_motor", "reducer", "edrive"))
    assert abs(b["telescoping_residuals"]["edrive"]) < 1e-12
    assert r["ledger"]["inverter_scope"]["model"] == "datasheet module"
    assert "useful_output_over_all_inputs" in r["ledger"]["aux_metrics"]          # LV aux is a separate input
    mp = api.efficiency_map({"map_speeds_rpm": [2000.0, 8000.0], "map_torques_Nm": [-100.0, 100.0, 600.0]})
    assert mp["status"].shape == (3, 2)
    import numpy as np
    top = np.array(mp["grids"]["edrive"])[2]                                     # 600 N m: beyond the capability
    assert all(s != "FEASIBLE" for s in mp["status"][2]) and np.all(np.isnan(top))
    ms = api.efficiency_mission({})
    assert ms["delivered"] and ms["energy"]["eta_traction"] < 1 and ms["energy"]["eta_regeneration"] < 1


# --------------------------------------------------------------------------------------------- one physics

@pytest.fixture(scope="module")
def module_setup():
    d = sf.synthetic_drive()
    model = api.module_model_from_dict(api.EXAMPLE_MODULE)
    md = replace(d, inverter=replace(d.inverter, loss=None, module_loss=model, module_Tj_C=150.0))
    return d, md


def test_module_physics_reaches_solver_capability_sizing_and_maps(module_setup):
    """A DC cap between the module and the surrogate P_dc: every result path must follow the module model."""
    from traction_workbench.analysis.sizing import size_parameter
    from traction_workbench.solvers.capability import policy_capability
    from traction_workbench.solvers.policy import PolicyEvaluator
    from traction_workbench.viz.sweeps import DC, OK, policy_point
    d, md = module_setup
    lim = sf.synthetic_limits()
    sc = Scenario("m", 12000.0, 600.0, lim)
    pm = PolicyEvaluator(md, sc).solve(150.0).point.Pdc_W
    ps = PolicyEvaluator(d, sc).solve(150.0).point.Pdc_W
    assert abs(pm - ps) > 50.0
    cap = 0.5 * (pm + ps)
    sc_cap = Scenario("m", 12000.0, 600.0, DcSourceLimits(cap, 1e5, 1e4, 1e4))
    hi, lo = (md, d) if pm > ps else (d, md)
    assert PolicyEvaluator(hi, sc_cap).solve(150.0).policy_claim.status.value == "INFEASIBLE"
    assert PolicyEvaluator(lo, sc_cap).solve(150.0).policy_claim.status.value == "FEASIBLE"
    c_hi = policy_capability(PolicyEvaluator(hi, sc_cap), +1)
    c_lo = policy_capability(PolicyEvaluator(lo, sc_cap), +1)
    assert c_hi.value_Nm < 150.0 <= c_lo.value_Nm
    st_hi, _ = policy_point(PolicyEvaluator(hi, sc_cap), 150.0)
    st_lo, _ = policy_point(PolicyEvaluator(lo, sc_cap), 150.0)
    assert st_hi == DC and st_lo == OK
    s = size_parameter(md, sc, 150.0, "discharge_power_max_W", (0.5 * pm, 2.0 * pm), samples=9)
    assert s.minimal_feasible == pytest.approx(pm, rel=1e-6)          # the module P_dc, not the surrogate's


def test_quadratic_certificates_are_never_used_with_the_module_model(module_setup, monkeypatch):
    """Leak detector: every I^2-surrogate proof path raises if it is reached with a datasheet module model."""
    from traction_workbench.solvers import bounds, capability, certificate, common, policy
    from traction_workbench.solvers.capability import physical_capability, policy_capability
    from traction_workbench.solvers.policy import PolicyEvaluator
    d, md = module_setup

    calls = []

    def guard(orig):
        def f(*a, **kw):
            k = next((x for x in a if hasattr(x, "module")), None) or getattr(a[0], "k", None)
            assert k is None or getattr(k, "module", None) is None, f"{orig.__name__} reached with a module model"
            calls.append(orig.__name__)
            return orig(*a, **kw)
        f.__name__ = orig.__name__
        return f
    for mod in (common, policy, capability, certificate, bounds):
        if hasattr(mod, "dc_band_I2"):
            monkeypatch.setattr(mod, "dc_band_I2", guard(getattr(mod, "dc_band_I2")))
    monkeypatch.setattr(policy.PolicyEvaluator, "_pdc_at", guard(policy.PolicyEvaluator._pdc_at))
    sc = Scenario("m", 12000.0, 600.0, sf.synthetic_limits())
    ev = PolicyEvaluator(md, sc)
    for T in (60.0, 150.0, -80.0):
        ev.solve(T)
    policy_capability(ev, +1)
    physical_capability(ev, +1, include_dc=True)
    sc0 = Scenario("m", 4000.0, 450.0, sf.synthetic_limits())          # module data at 600 V only: open claims
    sol = PolicyEvaluator(md, sc0).solve(100.0)
    assert sol.dc_claim.status.value == "UNKNOWN"
    assert not calls
    # positive control: the same guarded paths ARE used with the quadratic surrogate (the detector is live)
    PolicyEvaluator(d, sc).solve(150.0)
    assert calls


def test_module_comparison_protocol():
    r = api.module_compare({})
    assert r["mode"] == "fixed_policy" and r["common_fsw_Hz"] == 10e3
    for row in r["rows"]:
        assert row["A"]["status"] == row["B"]["status"] == "FEASIBLE"
        assert row["A"]["Tj_C"] != row["B"]["Tj_C"]                     # Tj is a result, not forced equal
        assert row["compare"]["verdict"] in ("A_LOWER_LOSS", "B_LOWER_LOSS", "UNDECIDED")
        if row["compare"]["verdict"] != "UNDECIDED":
            assert "not a production-population winner" in row["compare"]["reason"]
    assert r["mission"]["compare"]["unit"] == "J"
    # without a declared error budget the ranking is reserved
    c = {**api.EXAMPLE_EFFICIENCY["compare"], "A": {**api.EXAMPLE_EFFICIENCY["compare"]["A"], "loss_error_rel": None}}
    r2 = api.module_compare({"compare": {**c, "requests": [[6000.0, 150.0, 600.0]]}, "mission": None})
    assert r2["rows"][0]["compare"]["verdict"] == "UNDECIDED"
    # a requirement a candidate cannot deliver is not ranked
    r3 = api.module_compare({"compare": {**api.EXAMPLE_EFFICIENCY["compare"], "requests": [[6000.0, 900.0, 600.0]]},
                             "mission": None})
    assert r3["rows"][0]["compare"]["verdict"] == "NOT_COMPARABLE"
    with pytest.raises(InputValidationError):
        E.ModuleCandidate("x", None, 0.1, 0.1, "")                      # a budget needs its basis


def test_mission_direction_efficiency_is_withheld_when_part_of_the_mission_is_unknown():
    """A ratio over the known segments only is not the mission's direction efficiency (review finding)."""
    h = 3600.0
    segs = [{"duration_s": h, "P_dc": 10e3, "P_ac": 9.6e3, "P_m": 9.3e3, "P_o": 9e3},
            {"duration_s": h, "P_dc": None, "P_ac": None, "P_m": None, "P_o": None},       # port powers not known
            {"duration_s": h, "P_dc": -4e3, "P_ac": -4.3e3, "P_m": -4.7e3, "P_o": -5e3}]
    r = E.mission_energy(segs)
    assert r["eta_traction"] is None and r["eta_regeneration"] is None and not r["complete"]
    assert r["output_port"] == "P_m"                              # P_o not known everywhere: labelled fallback
    assert r["partial"]["eta_traction_partial"] == pytest.approx(0.93)
    assert r["partial"]["undetermined_s"] == pytest.approx(h)
    assert all(v is None for v in r["boundary_direction_eta"]["edrive"].values())
    full = E.mission_energy([segs[0], segs[2]])
    assert full["complete"] and full["partial"] is None and full["eta_traction"] == pytest.approx(0.9)
