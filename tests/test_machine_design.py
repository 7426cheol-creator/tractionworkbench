"""Machine design around a validated reference (handoff section 10): coherent scaling, trade study, winding
layout, concept sizing.

References are independent of the implementation: the dq equations under a change of turns (same ampere-turns
give the same torque and copper loss, k_N times the voltage), textbook distribution / pitch factors
k_d = sin(q a / 2) / (q sin(a / 2)), k_p = sin(y / tau * 90 deg), and published fractional-slot winding factors.
"""

import math

import pytest

from traction_workbench import spec_fixtures as sf
from traction_workbench.analysis import machine_design as M
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.safe_state import asc_steady_state
from traction_workbench.physics import DriveKernel, evaluate_point
from traction_workbench.scenario import Scenario


def _pt(d, n, i_d, i_q, vdc=600.0):
    return evaluate_point(DriveKernel(d, Scenario("t", n, vdc, sf.synthetic_limits())), i_d, i_q)


@pytest.mark.parametrize("kN", [0.8, 1.25])
def test_turns_scaling_keeps_torque_and_copper_loss_at_the_same_ampere_turns(kN):
    d = sf.synthetic_drive()
    s, _ = M.scale_drive(d, M.ScalingSpec("N", k_turns=kN))
    for n, i_d, i_q in ((1500.0, -80.0, 250.0), (6000.0, -220.0, 180.0)):
        p0 = _pt(d, n, i_d, i_q)
        p1 = _pt(s, n, i_d / kN, i_q / kN)
        assert p1.Te_Nm == pytest.approx(p0.Te_Nm, rel=1e-12)
        assert p1.Pcu_W == pytest.approx(p0.Pcu_W, rel=1e-12)
        assert p1.v_peak_V == pytest.approx(kN * p0.v_peak_V, rel=1e-12)
    assert s.domain.iq_A[1] == pytest.approx(d.domain.iq_A[1] / kN)
    assert s.inverter.current_limit_A_peak == d.inverter.current_limit_A_peak      # the inverter is not scaled


def test_stack_scaling_uses_the_declared_end_winding_shares():
    d = sf.synthetic_drive()
    kL, eR, eL = 1.3, 0.35, 0.15
    s, lin = M.scale_drive(d, M.ScalingSpec("L", k_stack=kL, end_R_share=eR, end_L_share=eL))
    f0, f1 = d.motor.flux, s.motor.flux
    assert f1.psi_pm_Wb == pytest.approx(kL * f0.psi_pm_Wb)
    assert f1.Ld_H == pytest.approx((kL * (1 - eL) + eL) * f0.Ld_H)
    assert f1.Lq_H == pytest.approx((kL * (1 - eL) + eL) * f0.Lq_H)
    assert s.motor.Rs_ohm == pytest.approx((kL * (1 - eR) + eR) * d.motor.Rs_ohm)
    assert s.motor.rotational_loss.viscous_Nm_per_rad_s == pytest.approx(kL * d.motor.rotational_loss.viscous_Nm_per_rad_s)
    assert any("iron" in x for x in lin["invalidated"])


def test_a_stack_change_without_end_shares_is_refused():
    with pytest.raises(InputValidationError):
        M.ScalingSpec("L", k_stack=1.2)
    with pytest.raises(InputValidationError):
        M.ScalingSpec("L", k_stack=1.2, end_R_share=0.3)
    with pytest.raises(InputValidationError):
        M.ScalingSpec("bad", k_turns=0.0)


def test_derived_candidate_carries_lineage_and_is_not_validated():
    d = sf.synthetic_drive()
    s, lin = M.scale_drive(d, M.ScalingSpec("N+10", k_turns=1.1, basis="one more turn per coil"))
    assert lin["derived"] and s.provenance.validation_status.startswith("DERIVED")
    assert s.drive_id != d.drive_id and "N+10" in s.revision
    assert any("AC copper" in x for x in lin["invalidated"])
    assert any("demagnetisation" in x for x in lin["invalidated"])
    same, lin0 = M.scale_drive(d, M.ScalingSpec("ref"))
    assert same is d and not lin0["derived"]


def test_flux_map_turns_scaling_and_the_refused_changes():
    d = sf.manufactured_map_drive()
    kN = 1.2
    s, _ = M.scale_drive(d, M.ScalingSpec("N", k_turns=kN))
    p0 = _pt(d, 3000.0, -60.0, 120.0)
    p1 = _pt(s, 3000.0, -60.0 / kN, 120.0 / kN)
    assert p1.Te_Nm == pytest.approx(p0.Te_Nm, rel=1e-9)
    assert p1.v_peak_V == pytest.approx(kN * p0.v_peak_V, rel=1e-9)
    with pytest.raises(InputValidationError):          # a PM change on a nonlinear map is not a scaling
        M.scale_drive(d, M.ScalingSpec("PM", k_pm=0.9))
    with pytest.raises(InputValidationError):          # end leakage is not in 2D map data
        M.scale_drive(d, M.ScalingSpec("L", k_stack=1.2, end_R_share=0.3, end_L_share=0.1))
    s2, _ = M.scale_drive(d, M.ScalingSpec("L", k_stack=1.2, end_R_share=0.3, end_L_share=0.0))
    assert _pt(s2, 3000.0, -60.0, 120.0).Te_Nm == pytest.approx(1.2 * p0.Te_Nm, rel=1e-9)


def test_steady_asc_current_scales_with_one_over_k_turns():
    d = sf.synthetic_drive()
    s, _ = M.scale_drive(d, M.ScalingSpec("N", k_turns=1.25))
    a0 = asc_steady_state(d, 12000.0)["i_peak_A"]
    a1 = asc_steady_state(s, 12000.0)["i_peak_A"]
    assert a1 == pytest.approx(a0 / 1.25, rel=1e-9)


def test_trade_study_shows_the_turns_trade_on_the_same_requirements():
    d = sf.synthetic_drive()
    checks = [M.DesignCheck("low", "capability", 2000.0, 450.0, torque_Nm=400.0),
              M.DesignCheck("high", "capability", 12000.0, 450.0, torque_Nm=130.0),
              M.DesignCheck("ugo", "ugo", 12000.0, 450.0, limit=900.0),
              M.DesignCheck("cu", "copper", 2000.0, 450.0, torque_Nm=300.0, limit=6000.0),
              M.DesignCheck("asc", "asc", 12000.0, 450.0, limit=None)]
    res = M.trade_study(d, [M.ScalingSpec("ref"), M.ScalingSpec("N+10", k_turns=1.1)], checks, sf.synthetic_limits())
    ref, more = res["rows"]
    c0, c1 = ref["checks"], more["checks"]
    assert c1["low"]["value"] > c0["low"]["value"]                  # more turns: more torque per inverter ampere
    assert c1["high"]["value"] < c0["high"]["value"]                # ... and less at high speed / low Vdc
    assert c1["high"]["status"] == "INFEASIBLE" and c0["high"]["status"] == "FEASIBLE"
    assert c1["ugo"]["value"] == pytest.approx(1.1 * c0["ugo"]["value"], rel=1e-12)
    assert c1["ugo"]["status"] == "INFEASIBLE" and more["binding"] in ("ugo", "high")
    assert c1["cu"]["value"] == pytest.approx(c0["cu"]["value"], rel=1e-6)   # same copper, same torque
    assert c0["asc"]["status"] == "UNKNOWN"                          # no limit declared: never a pass
    assert not more["all_feasible"]


# --------------------------------------------------------------------------------------------- winding

def _kd(q, alpha_deg):
    a = math.radians(alpha_deg)
    return math.sin(q * a / 2) / (q * math.sin(a / 2))


@pytest.mark.parametrize("Q,p,y", [(48, 4, 6), (48, 4, 5), (36, 3, 5), (72, 4, 7), (54, 3, 8), (24, 4, 3)])
def test_integer_slot_winding_factor_is_distribution_times_pitch(Q, p, y):
    w = M.winding_layout(Q, p, y)
    q = Q // (6 * p)
    tau = Q / (2 * p)
    ref = _kd(q, 360.0 * p / Q) * math.sin(math.radians(90.0 * y / tau))
    assert w["feasible"] and w["balanced"] and w["type"] == "integer-slot"
    assert w["kw1"] == pytest.approx(ref, rel=1e-12)
    assert w["coil_sides"]["A"] == w["coil_sides"]["B"] == w["coil_sides"]["C"] == 2 * Q // 3


@pytest.mark.parametrize("Q,p,kw", [(12, 5, 0.933), (9, 4, 0.945), (12, 4, 0.866), (18, 8, 0.945), (15, 4, 0.951)])
def test_fractional_slot_winding_factors_match_published_values(Q, p, kw):
    w = M.winding_layout(Q, p, 1 if Q < 2 * p * 1.5 else None)
    assert w["balanced"] and w["type"] == "fractional-slot"
    assert w["kw1"] == pytest.approx(kw, abs=1e-3)


def test_infeasible_slot_pole_combination_is_flagged():
    w = M.winding_layout(10, 4, 1)
    assert not w["feasible"] and not w["balanced"]
    assert w["phase_sequence"].startswith("not")


def test_three_phase_mmf_spectrum_cancels_triplen_orders_and_shows_subharmonics():
    w = M.winding_layout(12, 5, 1)
    orders = {h["order_mech"]: h["kw"] for h in w["harmonics"]}
    assert 3 not in orders and 9 not in orders
    assert orders[5] == pytest.approx(w["kw1"], rel=1e-12)
    assert orders[1] == pytest.approx(0.067, abs=1e-3) and orders[7] == pytest.approx(0.933, abs=1e-3)
    assert [h["order_mech"] for h in w["subharmonics"]] == [1]
    w2 = M.winding_layout(48, 4, 6)
    o2 = {h["order_mech"]: h["kw"] for h in w2["harmonics"]}
    assert o2[20] == pytest.approx(_kd(2, 5 * 30.0), rel=1e-12)       # 5th electrical: k_d5 at full pitch
    assert not w2["subharmonics"] and 12 not in o2


def test_parallel_paths_and_effective_turns():
    assert M.winding_layout(48, 4, 6)["max_parallel_paths"] == 8
    assert M.winding_layout(12, 5, 1)["max_parallel_paths"] == 2
    w98 = M.winding_layout(9, 4, 1, parallel_paths=2)
    assert w98["max_parallel_paths"] == 1 and not w98["parallel_paths_ok"]
    a = M.winding_layout(48, 4, 5, turns_per_coil=5)
    b = M.winding_layout(48, 4, 5, turns_per_coil=6)
    c = M.winding_layout(48, 4, 5, turns_per_coil=5, parallel_paths=2)
    assert a["N_series"] == pytest.approx(48 * 5 / 3)
    assert M.effective_turns_ratio(a, b) == pytest.approx(1.2)
    assert M.effective_turns_ratio(a, c) == pytest.approx(0.5)
    with pytest.raises(InputValidationError):
        M.effective_turns_ratio(a, M.winding_layout(48, 4, 6, turns_per_coil=5))


# --------------------------------------------------------------------------------------------- concept sizing

def test_concept_sizing_rotor_volume_identity():
    r = M.concept_sizing(300.0, (30.0, 45.0), (0.7, 1.1), n_max_rpm=15000.0, tip_speed_limit_m_s=150.0)
    assert len(r["rows"]) == 4
    for row in r["rows"]:
        D, L = row["D_rotor_mm"] / 1e3, row["L_stack_mm"] / 1e3
        sigma = row["sigma_kPa"] * 1e3
        assert L == pytest.approx(row["L_over_D"] * D)
        assert 2 * sigma * math.pi * D * D / 4 * L == pytest.approx(300.0, rel=1e-12)   # T = 2 sigma V_r
        assert row["tip_speed_m_s"] == pytest.approx(math.pi * D * 15000.0 / 60.0)
        assert row["tip_speed_ok"] == (row["tip_speed_m_s"] <= 150.0)
    with pytest.raises(InputValidationError):
        M.concept_sizing(300.0, (0.0,), (1.0,))
