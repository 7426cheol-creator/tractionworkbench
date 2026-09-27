"""Datasheet-based module losses (independent review 8.8): analytic checks and semantics."""

import math

import numpy as np
import pytest

from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.module_loss import (ModuleLossModel, SwitchDevice, Table2D, electrothermal_fixed_point,
                                                        inverter_losses, leg_losses, linear_table, loss_claim,
                                                        standstill_hotspot)

IMAX = 1000.0


def _dev(v0=0.0, r=0.0, vd0=0.0, rd=0.0, e_a=0.0, err_a=0.0, tech="IGBT", basis="per_device", temps=(25.0, 150.0),
         **kw):
    zero = linear_table(0.0, 0.0, IMAX, temps, "mJ")
    return SwitchDevice(tech, linear_table(v0, r, IMAX, temps), linear_table(vd0, rd, IMAX, temps),
                        linear_table(0.0, e_a * 1e3, IMAX, temps, "mJ"), zero,
                        linear_table(0.0, err_a * 1e3, IMAX, temps, "mJ") if basis == "per_device" else None,
                        energy_basis=basis, **kw)


def _op(I, phi_deg, m, Vdc=600.0):
    """dq values with the requested current magnitude, power-factor angle and modulation index."""
    gi = math.radians(100.0)
    gv = gi + math.radians(phi_deg)
    V = m * Vdc / 2
    return I * math.cos(gi), I * math.sin(gi), V * math.cos(gv), V * math.sin(gv)


def test_resistive_devices_give_R_Irms2_per_leg():
    R = 2e-3
    mod = ModuleLossModel(_dev(r=R, rd=R), fsw_Hz=0.0, n_angle=3600)
    for phi, m in ((0.0, 0.9), (60.0, 0.4), (170.0, 0.8)):
        id_, iq, vd, vq = _op(400.0, phi, m)
        r = inverter_losses(mod, id_, iq, vd, vq, 600.0, 25.0)
        assert r["conduction_W"] == pytest.approx(3 * R * 400.0 ** 2 / 2, rel=1e-9)


def test_constant_drop_devices_give_V0_Iavg():
    V0 = 1.1
    mod = ModuleLossModel(_dev(v0=V0, vd0=V0), fsw_Hz=0.0, n_angle=3600)
    id_, iq, vd, vq = _op(300.0, 30.0, 0.7)
    r = inverter_losses(mod, id_, iq, vd, vq, 600.0, 25.0)
    assert r["conduction_W"] == pytest.approx(3 * V0 * 2 * 300.0 / math.pi, rel=1e-5)


@pytest.mark.parametrize("phi_deg, m", [(0.0, 0.9), (35.0, 0.6), (150.0, 0.8)])
def test_igbt_linear_model_matches_the_spwm_closed_form(phi_deg, m):
    V0, r, Vf, rd, I = 0.9, 1.5e-3, 1.0, 1.2e-3, 400.0
    mod = ModuleLossModel(_dev(v0=V0, r=r, vd0=Vf, rd=rd), fsw_Hz=0.0, modulation="spwm", n_angle=20000)
    phi = math.radians(phi_deg)
    leg = leg_losses(mod, I, phi, m * 300.0, 600.0, 25.0)
    c = leg["conduction_W"]
    pt = V0 * I * (1 / (2 * math.pi) + m * math.cos(phi) / 8) + r * I ** 2 * (1 / 8 + m * math.cos(phi) / (3 * math.pi))
    pd = Vf * I * (1 / (2 * math.pi) - m * math.cos(phi) / 8) + rd * I ** 2 * (1 / 8 - m * math.cos(phi) / (3 * math.pi))
    assert c["upper_switch"] == pytest.approx(pt, rel=2e-4)
    assert c["lower_reverse"] == pytest.approx(pd, rel=2e-4)
    assert c["upper_switch"] >= 0 and c["lower_reverse"] >= 0          # regeneration moves loss, never negates it


def test_linear_switching_energy_gives_fsw_E_I_over_pi():
    a, fsw, I = 0.1e-3, 10e3, 350.0                                     # 0.1 mJ/A
    mod = ModuleLossModel(_dev(e_a=a), fsw_Hz=fsw, n_angle=20000)
    id_, iq, vd, vq = _op(I, 20.0, 0.8)
    r = inverter_losses(mod, id_, iq, vd, vq, 600.0, 25.0)
    assert r["leg"]["switching_W"]["upper_switch"] == pytest.approx(fsw * a * I / math.pi, rel=1e-4)
    zero = inverter_losses(ModuleLossModel(_dev(e_a=a), fsw_Hz=0.0), id_, iq, vd, vq, 600.0, 25.0)
    assert zero["switching_W"] == 0.0


def test_dpwm_clamps_one_third_of_the_period():
    mod = ModuleLossModel(_dev(e_a=0.1e-3), fsw_Hz=10e3, modulation="dpwm1", n_angle=3600)
    svp = ModuleLossModel(_dev(e_a=0.1e-3), fsw_Hz=10e3, modulation="svpwm", n_angle=3600)
    id_, iq, vd, vq = _op(300.0, 0.0, 0.8)
    d = inverter_losses(mod, id_, iq, vd, vq, 600.0, 25.0)
    s = inverter_losses(svp, id_, iq, vd, vq, 600.0, 25.0)
    assert d["leg"]["switching_fraction"] == pytest.approx(2 / 3, abs=2e-3)
    assert d["switching_W"] < s["switching_W"]


def test_pair_total_energy_is_not_double_counted():
    per = ModuleLossModel(_dev(e_a=0.1e-3, err_a=0.05e-3), fsw_Hz=10e3)
    tot = ModuleLossModel(_dev(e_a=0.1e-3, basis="commutation_pair_total"), fsw_Hz=10e3)
    id_, iq, vd, vq = _op(300.0, 10.0, 0.8)
    rp, rt = inverter_losses(per, id_, iq, vd, vq, 600.0, 25.0), inverter_losses(tot, id_, iq, vd, vq, 600.0, 25.0)
    assert rp["switching_W"] == pytest.approx(rt["switching_W"] * 1.5, rel=1e-9)
    assert rt["leg"]["switching_W"]["lower_recovery"] == 0.0


def test_sic_sync_rectification_splits_reverse_current_between_channel_and_body_diode():
    dev = _dev(r=2e-3, vd0=3.0, tech="SiC_MOSFET")
    no_dt = ModuleLossModel(dev, fsw_Hz=20e3, deadtime_s=0.0)
    dt = ModuleLossModel(dev, fsw_Hz=20e3, deadtime_s=500e-9)
    id_, iq, vd, vq = _op(300.0, 150.0, 0.8)                            # regenerating: much reverse conduction
    a = inverter_losses(no_dt, id_, iq, vd, vq, 600.0, 25.0)
    b = inverter_losses(dt, id_, iq, vd, vq, 600.0, 25.0)
    assert a["conduction_W"] == pytest.approx(3 * 2e-3 * 300.0 ** 2 / 2, rel=1e-6)   # channel only: pure R
    assert b["conduction_W"] > a["conduction_W"]                       # 3 V body diode during 2*t_d*fsw = 2 %
    assert b["leg"]["deadtime_fraction"] == pytest.approx(0.02)


def test_no_extrapolation_and_no_silent_voltage_scaling():
    mod = ModuleLossModel(_dev(r=1e-3, rd=1e-3, e_a=0.1e-3), fsw_Hz=10e3)
    id_, iq, vd, vq = _op(1500.0, 0.0, 0.8)                             # above the 1000 A tables
    r = inverter_losses(mod, id_, iq, vd, vq, 600.0, 25.0)
    assert not r["established"] and any("no extrapolation" in p for p in r["problems"])
    id_, iq, vd, vq = _op(300.0, 0.0, 0.5)                             # 150 V phase peak: linear at 400 and 600 V
    assert not inverter_losses(mod, id_, iq, vd, vq, 600.0, 175.0)["established"]     # above 150 degC data
    r2 = inverter_losses(mod, id_, iq, vd, vq, 400.0, 25.0)                            # test voltage is 600 V
    assert not r2["established"] and any("no scaling law" in p for p in r2["problems"])
    assert loss_claim(r2)["status"] == "UNKNOWN"
    with pytest.raises(InputValidationError):
        _dev(e_a=0.1e-3, vdc_scaling_exponent=1.0)                     # a law without a basis is rejected
    scaled = ModuleLossModel(_dev(r=1e-3, rd=1e-3, e_a=0.1e-3, vdc_scaling_exponent=1.3,
                                  vdc_scaling_basis="supplier app note (test)", vdc_scaling_valid_V=(300.0, 800.0)),
                             fsw_Hz=10e3)
    r3 = inverter_losses(scaled, id_, iq, vd, vq, 400.0, 25.0)
    r4 = inverter_losses(scaled, id_, iq, vd, vq, 600.0, 25.0)
    assert r3["established"] and r3["switching_W"] == pytest.approx(r4["switching_W"] * (400 / 600) ** 1.3, rel=1e-9)


def test_overmodulation_is_not_supported():
    mod = ModuleLossModel(_dev(r=1e-3, rd=1e-3), fsw_Hz=10e3)
    id_, iq, vd, vq = _op(300.0, 0.0, 1.3)                              # beyond 2/sqrt(3) for SVPWM
    r = inverter_losses(mod, id_, iq, vd, vq, 600.0, 25.0)
    assert not r["established"] and any("overmodulation" in p for p in r["problems"])


def test_angle_refinement_is_below_the_budget():
    mod = ModuleLossModel(_dev(v0=0.8, r=1.5e-3, vd0=0.9, rd=1e-3, e_a=0.08e-3, err_a=0.03e-3), fsw_Hz=10e3,
                          n_angle=1440)
    id_, iq, vd, vq = _op(420.0, 25.0, 0.85)
    assert inverter_losses(mod, id_, iq, vd, vq, 600.0, 25.0)["angle_refinement_rel_diff"] < 2e-3


def test_standstill_hotspot_is_not_total_over_six():
    mod = ModuleLossModel(_dev(v0=0.8, r=1.5e-3, vd0=0.9, rd=1e-3, e_a=0.08e-3, err_a=0.03e-3), fsw_Hz=10e3)
    h = standstill_hotspot(mod, 400.0, 600.0, 25.0)
    assert h["established"] and h["hottest_device_W"] > 1.2 * h["total_over_six_W"]
    assert abs(math.cos(math.radians(h["angle_at_max_deg"]))) == pytest.approx(1.0, abs=1e-4)   # peak current device


def test_electrothermal_fixed_point_and_temperature_dependence():
    temps = (25.0, 150.0)
    dev = SwitchDevice("IGBT", Table2D(temps, (0.0, IMAX), ((0.8, 0.8 + 1.2), (0.7, 0.7 + 2.0)), "V"),
                       linear_table(0.9, 1e-3, IMAX, temps), linear_table(0.0, 0.08, IMAX, temps, "mJ"),
                       linear_table(0.0, 0.0, IMAX, temps, "mJ"), linear_table(0.0, 0.03, IMAX, temps, "mJ"))
    mod = ModuleLossModel(dev, fsw_Hz=8e3)
    id_, iq, vd, vq = _op(400.0, 20.0, 0.8)
    fp = electrothermal_fixed_point(mod, {"id_A": id_, "iq_A": iq, "vd_V": vd, "vq_V": vq, "Vdc_V": 600.0},
                                    0.15, 65.0)
    assert fp["converged"] and 65.0 < fp["Tj_C"] < 150.0
    cold = inverter_losses(mod, id_, iq, vd, vq, 600.0, 65.0)["hottest_position_W"]
    assert fp["P_hot_W"] > cold                                          # losses rise with Tj on these curves
    hot_sink = electrothermal_fixed_point(mod, {"id_A": id_, "iq_A": iq, "vd_V": vd, "vq_V": vq, "Vdc_V": 600.0},
                                          5.0, 65.0)                     # leaves the 150 degC data: not established
    assert hot_sink["converged"] is False and "outside the table" in hot_sink["reason"]


# ---------------------------------------------------------------- coupling into the drive (P_dc, DC claim, thermal)

def _module_drive(drive, Tj=150.0):
    from dataclasses import replace
    from traction_workbench import api
    model = api.module_model_from_dict(api.EXAMPLE_MODULE)
    return replace(drive, inverter=replace(drive.inverter, loss=None, module_loss=model, module_Tj_C=Tj)), model


def test_module_loss_feeds_pdc_and_the_dc_claim(drive, limits):
    from traction_workbench.physics import forward_evaluation
    from traction_workbench.scenario import Scenario
    from traction_workbench.solvers.policy import PolicyEvaluator
    md, model = _module_drive(drive)
    sc = Scenario("m", 12000.0, 600.0, limits)
    sol = PolicyEvaluator(md, sc).solve(150.0)
    pt = sol.point
    ref = inverter_losses(model, pt.id_A, pt.iq_A, pt.vd_V, pt.vq_V, 600.0, 150.0)
    assert pt.Pinv_W == pytest.approx(ref["dc_side_W"], rel=1e-12)
    assert pt.Pdc_W == pytest.approx(pt.Pac_W + ref["dc_side_W"], rel=1e-12)          # P_dc = P_ac + P_inv, once
    assert sol.dc_claim.status.value == "FEASIBLE" and sol.policy_claim.status.value == "FEASIBLE"
    # the I^2-band certificate is not used with the module model: only a verified witness counts
    assert "module losses" in sol.physical_dc.evidence[0].summary
    fr = forward_evaluation(md, sc, pt.id_A, pt.iq_A)
    assert fr.point.inverter_loss_detail["established"] and fr.accepted
    # tight DC cap between the module and surrogate P_dc: the module model decides (never both summed)
    from traction_workbench.scenario import DcSourceLimits
    cap = Scenario("m", 12000.0, 600.0, DcSourceLimits(pt.Pdc_W - 500.0, 1e5, 1e4, 1e4))
    assert PolicyEvaluator(md, cap).solve(150.0).policy_claim.status.value == "INFEASIBLE"
    assert PolicyEvaluator(drive, cap).solve(150.0).policy_claim.status.value == "FEASIBLE"


def test_module_loss_outside_data_makes_dc_unknown_not_zero(drive, limits):
    from traction_workbench.scenario import Scenario
    from traction_workbench.solvers.policy import PolicyEvaluator
    md, _ = _module_drive(drive)
    sol = PolicyEvaluator(md, Scenario("m", 12000.0, 450.0, limits)).solve(100.0)   # switching data at 600 V only
    assert sol.point is not None and sol.point.Pdc_W is None
    assert sol.dc_claim.status.value == "UNKNOWN" and sol.policy_claim.status.value == "UNKNOWN"
    with pytest.raises(InputValidationError):
        from dataclasses import replace
        replace(drive.inverter, module_loss=_module_drive(drive)[1], module_Tj_C=150.0)   # both models: rejected


def test_hottest_device_heat_source_needs_a_device_level_model(drive, limits):
    from traction_workbench.extensions.thermal import FosterNetwork, ThermalModel, ThermalNode, thermal_duration
    from traction_workbench.models.provenance import DataOrigin, Provenance
    from traction_workbench.scenario import Scenario
    node = ThermalNode("junction (hottest device)", FosterNetwork((0.03, 0.06), (0.05, 1.5)), 150.0,
                       (("inverter_hottest_device", 1.0),))
    wnd = ThermalNode("winding", FosterNetwork((0.004, 0.01), (20.0, 300.0)), 180.0, (("copper", 1.0), ("rotational", 1.0)))
    tm = ThermalModel("TM", "1", (node, wnd), Provenance(DataOrigin.ESTIMATED, "t", "1", "test"), validated=True,
                      validity=(("coolant_temp_C", (60.0, 70.0)),), validation_evidence="TR-1")
    sc = Scenario("t", 6000.0, 600.0, limits, coolant_temp_C=65.0, initial_state="equilibrium_at_coolant")
    md, _ = _module_drive(drive)
    with_module = thermal_duration(md, sc, tm, 150.0, 10.0)
    assert with_module["claim"]["status"] in ("FEASIBLE", "INFEASIBLE")
    assert with_module["nodes"][0]["power_W"] > 0
    surrogate = thermal_duration(drive, sc, tm, 150.0, 10.0)            # quadratic surrogate: no device loss
    assert surrogate["claim"]["status"] == "UNKNOWN"
    assert any("total/6 is not substituted" in p for p in surrogate["qualification_problems"])


def test_module_api_example_runs():
    from traction_workbench import api
    r = api.module_losses({})
    assert r["losses"]["established"] and r["operating_point"]["Pdc_module_W"] > r["operating_point"]["Pac_W"]
    assert r["standstill"]["hottest_device_W"] > r["standstill"]["total_over_six_W"]
    assert r["electrothermal"]["converged"]
