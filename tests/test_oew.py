"""Open-end winding (OEW) dual inverter: addendum acceptance cases O-01..O-07, R-01, X-01 and the section 9 examples.

Reference values are derived independently in the test (closed forms), not copied from the implementation.
"""

import math

import numpy as np
import pytest

from traction_workbench.errors import InputValidationError
from traction_workbench.extensions import oew as O
from traction_workbench.scenario import DcSourceLimits
from traction_workbench.spec_fixtures import synthetic_drive

SQ3 = math.sqrt(3.0)


def zs_example():
    return O.ZeroSequenceModel(50e-6, ((3, 0.004, 0.0),), basis="synthetic test data")


# ------------------------------------------------------------------ O-01 switch-state geometry

def test_o01_state_pairs_and_voltage_envelopes():
    V = 400.0
    g = O.switch_state_geometry(V)
    assert (g["pairs"], g["unique_alphabeta"]) == (64, 19)
    assert (g["admissible_pairs"], g["admissible_unique_alphabeta"]) == (20, 7)
    assert g["hull_inradius_V"] == pytest.approx(V, rel=1e-12)                     # zero-u0 hexagon: V
    assert g["hull_circumradius_V"] == pytest.approx(2 * V / SQ3, rel=1e-12)
    assert g["unconstrained_hull_inradius_V"] == pytest.approx(2 * V / SQ3, rel=1e-12)   # hides a v0
    assert g["single_vsi_inradius_V"] == pytest.approx(V / SQ3, rel=1e-12)
    assert round(V / SQ3, 2) == 230.94 and round(2 * V / SQ3, 2) == 461.88
    iso = O.switch_state_geometry(V, V, "isolated")
    assert iso["admissible_pairs"] == 64 and iso["hull_inradius_V"] == pytest.approx(2 * V / SQ3, rel=1e-12)
    asym = O.switch_state_geometry(400.0, 200.0, "isolated")
    assert asym["hull_inradius_V"] == pytest.approx(600.0 / SQ3, rel=1e-12)          # (VA + VB)/sqrt3
    # the gain of the zero-u0 common bus over a single VSI is sqrt3, not 2
    assert g["hull_inradius_V"] / g["single_vsi_inradius_V"] == pytest.approx(SQ3, rel=1e-12)


def test_o01_common_bus_rejects_two_voltages_and_a_second_limit():
    with pytest.raises(InputValidationError):
        O.OewTopology("common_bus", 400.0, 350.0)
    with pytest.raises(InputValidationError):
        O.OewTopology("common_bus", 400.0, limits_B=DcSourceLimits())
    with pytest.raises(InputValidationError):
        O.OewTopology("split_capacitor", 400.0)
    with pytest.raises(InputValidationError):
        O.OewTopology("isolated", 400.0)                                            # VB required


# ------------------------------------------------------------------ O-02 dq0 power / copper / reciprocity

def test_o02_dq0_power_identity_random_samples():
    rng = np.random.default_rng(7)
    worst = 0.0
    for _ in range(1000):
        vd, vq, v0, id_, iq, i0 = rng.normal(size=6) * [300, 300, 50, 400, 400, 60]
        th = rng.uniform(0, 2 * math.pi)
        va, vb, vc = O.dq0_to_abc(vd, vq, v0, th)
        ia, ib, ic = O.dq0_to_abc(id_, iq, i0, th)
        p_abc = va * ia + vb * ib + vc * ic
        p_dq0 = O.winding_power_dq0(vd, vq, v0, id_, iq, i0)
        worst = max(worst, abs(float(p_abc - p_dq0)) / max(1.0, abs(float(p_abc))))
        # round trip
        d2, q2, z2 = O.abc_to_dq0(ia, ib, ic, th)
        assert abs(d2 - id_) < 1e-9 and abs(q2 - iq) < 1e-9 and abs(z2 - i0) < 1e-9
    assert worst < 1e-12


def test_o02_zero_sequence_copper_and_peak_example_9_2():
    """Section 9.2: 100 A balanced peak + 20 A DC zero sequence, Rs = 0.02 ohm."""
    m = O.abc_waveform_metrics(60.0, 80.0, 20.0, 0.02)
    assert m["phase_peak_A"] == pytest.approx(120.0, rel=1e-9)
    assert all(r == pytest.approx(math.sqrt(100 ** 2 / 2 + 20 ** 2), rel=1e-9) for r in m["phase_rms_A"])
    assert round(m["phase_rms_A"][0], 3) == 73.485
    assert m["copper_abc_W"] == pytest.approx(324.0, rel=1e-9)
    assert m["copper_dq_only_W"] == pytest.approx(300.0, rel=1e-12)
    # i0 with zero mean still heats: 3 Rs <i0^2>
    ac = O.abc_waveform_metrics(60.0, 80.0, lambda th: 20.0 * np.cos(3 * th), 0.02)
    assert abs(ac["i0_mean_A"]) < 1e-9 and ac["copper_zero_sequence_W"] == pytest.approx(3 * 0.02 * 200.0, rel=1e-6)


# ------------------------------------------------------------------ O-03 common vs isolated return

def _point(topo, T=150.0, n=12000):
    d = synthetic_drive()
    r = O.oew_min_current_point(d, topo, n, T)
    assert r["witness"] is not None, r
    return r["witness"]


def test_o03_isolated_kcl_forces_i0_zero_common_bus_does_not():
    iso = O.OewTopology("isolated", 400.0, 400.0, zero_sequence=zs_example())
    w = _point(iso)
    assert w["currents"]["i0_rms_A"] == 0.0
    assert "floating" in w["zero_sequence"]["policy"]
    nc = O.OewTopology("common_bus", 400.0, zero_sequence=zs_example(), zs_policy="no_compensation")
    w2 = _point(nc)
    zs = zs_example()
    we = 4 * 2 * math.pi * 12000 / 60
    i0_pk = 3 * we * 0.004 / math.hypot(0.0 if zs.R0_ohm is None else 0.0, 3 * we * 50e-6)   # R0 small vs 3 we L0
    assert w2["currents"]["i0_peak_A"] == pytest.approx(i0_pk, rel=0.01)
    # the zero-sequence circuit is driven by the rotor: <T0> omega_m + 3 R0 <i0^2> = 0
    wm = 2 * math.pi * 12000 / 60
    op = w2["operating_point"]
    assert op["T0_mean_Nm"] < 0
    assert op["T0_mean_Nm"] * wm + op["Pcu_zero_seq_W"] == pytest.approx(0.0, abs=1e-6)
    assert w2["currents"]["phase_peak_A"] > w2["currents"]["dq_norm_A"]                # O-07: dq norm is not the peak


def test_o03_regulated_i0_costs_voltage_headroom():
    reg = O.OewTopology("common_bus", 400.0, zero_sequence=zs_example(), zs_policy="regulate_i0")
    zero = O.OewTopology("common_bus", 400.0, zero_sequence=O.ZeroSequenceModel(50e-6, (), basis="no triplen (test)"))
    d = synthetic_drive()
    r1 = O.oew_min_current_point(d, reg, 12000, 150.0)["witness"]
    r0 = O.oew_min_current_point(d, zero, 12000, 150.0)["witness"]
    assert r1["currents"]["dq_norm_A"] > r0["currents"]["dq_norm_A"]    # u0* shares the range -> more FW current


# ------------------------------------------------------------------ O-04 B000 is not a floating star

def test_o04_b_clamp_is_not_a_floating_star():
    r = O.b_clamp_vs_floating_star(400.0, "100")
    assert r["B_000_common_bus_V"] == [400.0, 0.0, 0.0]
    assert r["floating_star_V"] == pytest.approx([800 / 3, -400 / 3, -400 / 3])
    assert r["B_000_u0_V"] == pytest.approx(400 / 3)


# ------------------------------------------------------------------ O-05 port accounting

def _module_drive():
    from dataclasses import replace

    from traction_workbench import api
    base = synthetic_drive()
    mdl = api.module_model_from_dict(api.EXAMPLE_MODULE)
    return replace(base, inverter=replace(base.inverter, loss=None, module_loss=mdl, module_Tj_C=150.0))


def test_o05_port_sign_power_split_and_source_budget():
    d = _module_drive()
    lim = DcSourceLimits(discharge_power_max_W=1e6, charge_power_max_W=1e6, discharge_current_max_A=math.inf,
                         charge_current_max_A=math.inf)
    base = O.OewTopology("isolated", 600.0, 600.0, limits_A=lim, limits_B=lim)
    w = _point(base, T=200.0, n=3000)
    op = w["operating_point"]
    idq = (op["id_A"], op["iq_A"])
    pw = op["P_winding_W"]
    # +100 / -30 split of a 70-unit winding power: s = 10/7 (bridge B absorbs, power circulates)
    s = 10.0 / 7.0
    circ = O.OewTopology("isolated", 600.0, 600.0, power_split_A=s, limits_A=lim,
                         limits_B=DcSourceLimits(discharge_power_max_W=1e6, charge_power_max_W=0.25 * pw,
                                                 discharge_current_max_A=math.inf, charge_current_max_A=math.inf))
    r = O.oew_point(d, circ, 3000, *idq)
    PA, PB = r["bridges"]["A"]["P_ac_W"], r["bridges"]["B"]["P_ac_W"]
    assert PA == pytest.approx(s * pw, rel=1e-9) and PB == pytest.approx((1 - s) * pw, rel=1e-9)
    assert PA + PB == pytest.approx(pw, rel=1e-12)
    assert PB < 0 < PA and r["circulating_power_W"] == pytest.approx(-PB, rel=1e-12)
    # B must accept about 3/7 pw > 0.25 pw of charge: its port claim fails although the net power is positive
    claims = {c["name"]: c["status"] for c in r["claims"]}
    assert r["bridges"]["A"]["loss"]["established"] and r["bridges"]["B"]["loss"]["established"]
    assert r["bridges"]["B"]["P_dc_W"] < -0.25 * pw
    assert claims["oew_port_B"] == "INFEASIBLE" and claims["oew_port_A"] == "FEASIBLE"
    assert r["status"] == "INFEASIBLE"


def test_o05_common_bus_cap_applies_once_to_the_net():
    """With datasheet losses the shared source sees P_dc,A + P_dc,B once; a cap between the branch power and the
    net decides by the net, and the branch stress is reported separately."""
    d = _module_drive()
    open_lim = DcSourceLimits(discharge_power_max_W=1e9, charge_power_max_W=1e9, discharge_current_max_A=math.inf,
                              charge_current_max_A=math.inf)
    cb = O.OewTopology("common_bus", 600.0, zero_sequence=O.ZeroSequenceModel(50e-6, (), basis="test"),
                       limits_A=open_lim, power_split_A=10.0 / 7.0)
    w = O.oew_min_current_point(d, cb, 3000, 200.0)["witness"]
    assert w is not None
    PA, PB = w["bridges"]["A"]["P_dc_W"], w["bridges"]["B"]["P_dc_W"]
    assert PA is not None and PB is not None and PB < 0 < PA
    net = w["ports"]["shared_source"]["P_dc_W"]
    assert net == pytest.approx(PA + PB, rel=1e-12)
    cap = 0.5 * (net + PA)                                  # above the net, below bridge A's own branch power
    cb2 = O.OewTopology("common_bus", 600.0, zero_sequence=cb.zero_sequence, power_split_A=10.0 / 7.0,
                        limits_A=DcSourceLimits(discharge_power_max_W=cap, charge_power_max_W=1e9,
                                                discharge_current_max_A=math.inf, charge_current_max_A=math.inf))
    r = O.oew_point(d, cb2, 3000, w["operating_point"]["id_A"], w["operating_point"]["iq_A"])
    assert {c["name"]: c["status"] for c in r["claims"]}["oew_shared_source"] == "FEASIBLE"
    assert r["bridges"]["A"]["loss"]["established"] and r["bridges"]["B"]["loss"]["established"]


def test_o05_regen_signs():
    iso = O.OewTopology("isolated", 400.0, 400.0)
    w = _point(iso, T=-100.0, n=6000)
    assert w["bridges"]["A"]["P_ac_W"] < 0 and w["bridges"]["B"]["P_ac_W"] < 0


# ------------------------------------------------------------------ O-06 switching ripple

def test_o06_average_u0_zero_but_i0_ripples():
    r = O.zero_sequence_switching_ripple(400.0, 300.0, 0.3, 10e3, 400.0, 50e-6, 0.02)
    assert r["max_carrier_average_u0_error_V"] < 1e-6
    assert r["i0_pp_A"] > 1.0 and r["i0_ripple_rms_A"] > 0.1
    shifted = O.zero_sequence_switching_ripple(400.0, 300.0, 0.3, 10e3, 400.0, 50e-6, 0.02, carrier_shift=0.5)
    assert shifted["max_carrier_average_u0_error_V"] < 1e-6
    assert shifted["i0_ripple_rms_A"] != pytest.approx(r["i0_ripple_rms_A"], rel=0.05)


# ------------------------------------------------------------------ O-07 sensing

def test_o07_two_sensor_reconstruction_hides_i0():
    th = np.linspace(0, 2 * math.pi, 721)
    ia, ib, ic = O.dq0_to_abc(0.0, 100.0, 20.0 * np.cos(3 * th), th)
    r = O.two_sensor_reconstruction(ia, ib, ic)
    assert r["i0_true_rms_A"] > 10 and r["i0_reconstructed_rms_A"] < 1e-9


# ------------------------------------------------------------------ R-01 paired states

def test_r01_opposite_clamps_are_a_counterexample_example_9_3():
    cb = O.OewTopology("common_bus", 400.0)
    r = O.paired_state_screen(cb, "asc_top", "asc_bottom", L0_H=50e-6)
    assert r["status"] == "INFEASIBLE" and r["winding_voltage_V"] == [400.0, 400.0, 400.0] and r["u0_V"] == 400.0
    assert r["di0_dt_initial_A_per_s"] == pytest.approx(8e6)
    assert r["di0_dt_initial_A_per_s"] * 2e-6 == pytest.approx(16.0)
    same = O.paired_state_screen(cb, "asc_bottom", "asc_bottom")
    assert same["status"] == "UNKNOWN" and same["reason_code"] == "COUPLED_MODEL_REQUIRED"
    assert O.paired_state_screen(cb, "asc_top", "pwm")["status"] == "INFEASIBLE"            # torque not removed
    table = O.paired_state_table(cb, emf_phase_peak_V=300.0)
    assert len(table) == 16


def test_r01_six_switch_open_thresholds_differ_by_topology():
    cb = O.OewTopology("common_bus", 400.0)
    iso = O.OewTopology("isolated", 400.0, 400.0)
    # phase EMF 420 V: above the common-bus per-phase threshold V = 400 V, below (VA+VB)/sqrt3 = 461.9 V
    assert O.paired_state_screen(cb, "off", "off", emf_phase_peak_V=420.0)["status"] == "UNKNOWN"
    assert O.paired_state_screen(iso, "off", "off", emf_phase_peak_V=420.0)["status"] == "FEASIBLE"
    assert O.paired_state_screen(cb, "off", "off")["reason_code"] == "MISSING_INPUT"


# ------------------------------------------------------------------ X-01 / P0 topology identity

def test_x01_missing_zero_sequence_data_is_unknown_not_zero():
    cb = O.OewTopology("common_bus", 400.0)
    r = O.oew_min_current_point(synthetic_drive(), cb, 12000, 150.0)
    w = r["witness"]
    assert w["zero_sequence"]["status"] == "UNKNOWN"
    assert all(c["status"] != "FEASIBLE" for c in w["claims"])
    assert "MISSING_INPUT" in {x for c in w["claims"] for x in c["reasons"]}


def test_p0_topology_identity_single_vsi_loader_refuses_oew():
    from traction_workbench.io import drive_from_dict
    import json
    from pathlib import Path
    spec = json.loads((Path(__file__).parent.parent / "examples" / "drives" / "synthetic_ipmsm_declared_units.json")
                      .read_text(encoding="utf-8"))
    spec["inverter"]["topology"] = "oew_common_bus"
    with pytest.raises(InputValidationError) as e:
        drive_from_dict(spec)
    assert "single-VSI solver never re-interprets" in str(e.value)
    spec["inverter"]["topology"] = "single_vsi"
    drive_from_dict(spec)


def test_p0_oew_refuses_a_wye_equivalent_winding():
    from dataclasses import replace
    d = synthetic_drive()
    weq = replace(d, motor=replace(d.motor, connection="wye_equivalent", connection_note="delta -> wye (test)"))
    with pytest.raises(InputValidationError):
        O.oew_point(weq, O.OewTopology("isolated", 400.0, 400.0), 3000, -50.0, 100.0)


def test_capability_comparison_same_method_all_configurations():
    cmp = O.capability_comparison(synthetic_drive(), 400.0, [2000.0, 10000.0], zero_sequence=zs_example(),
                                  n_id=81, n_iq=121)
    low, high = cmp["rows"]
    # low speed: current limited, all equal (same current limit per bridge - never doubled)
    assert low["single_vsi_Nm"] == pytest.approx(low["oew_common_bus_Nm"]) == pytest.approx(low["oew_isolated_Nm"])
    # high speed: single VSI < common-bus OEW < isolated OEW = single VSI on the same total stack
    assert high["single_vsi_Nm"] < high["oew_common_bus_Nm"] < high["oew_isolated_Nm"]
    assert high["oew_isolated_Nm"] == pytest.approx(high["single_vsi_same_stack_Nm"])
