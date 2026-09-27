"""V0/V1: forward goldens (golden_forward.json) and power identities."""

import math

import pytest

from conftest import golden, scenario
from traction_workbench import spec_fixtures as sf
from traction_workbench.physics import DriveKernel, evaluate_point, forward_evaluation

CASES = golden("golden_forward.json")["cases"]

FIELD = {
    "n_rpm": "speed_rpm", "id_A_peak": "id_A", "iq_A_peak": "iq_A", "i_peak_A": "i_peak_A",
    "i_phase_rms_A": "i_phase_rms_A", "vd_V_peak": "vd_V", "vq_V_peak": "vq_V", "v_peak_V": "v_peak_V",
    "Te_Nm": "Te_Nm", "Tshaft_Nm": "Tshaft_Nm", "Pshaft_W": "Pshaft_W", "Pcu_W": "Pcu_W", "Prot_W": "Prot_W",
    "Pac_W": "Pac_W", "Pinv_W": "Pinv_W", "Pdc_W": "Pdc_W", "Idc_A": "Idc_A", "Vmargin_V": "voltage_margin_V",
}


def _point(case):
    d = sf.synthetic_drive(case.get("parameter_overrides"))
    inp = case["input"]
    return evaluate_point(DriveKernel(d, scenario(inp["n_rpm"], inp["Vdc_V"])), inp["id_A_peak"], inp["iq_A_peak"])


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_forward_values_match_golden(case):
    pt = _point(case)
    for key, exp in case["expected"].items():
        got = getattr(pt, FIELD[key])
        # acceptance: normalised direct-substitution discrepancy <= 1e-10
        assert abs(got - exp) / max(1.0, abs(exp)) <= 1e-10, (key, got, exp)


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_forward_violation_groups(case):
    assert sorted(_point(case).violated_groups()) == sorted(case["expected_violations"])


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_power_identities(case):
    pt = _point(case)
    scale = max(abs(pt.Pac_W), abs(pt.Pdc_W), 1.0)
    tol = max(0.01, 1e-9 * scale)
    assert abs(pt.residual_pac_tem_W) <= tol
    assert abs(pt.residual_pac_shaft_W) <= tol
    assert abs(pt.residual_dc_W) <= tol
    assert pt.identities_ok


def test_standstill_semantics():
    case = next(c for c in CASES if c["case_id"] == "F00_STANDSTILL")
    pt = _point(case)
    assert pt.energy_mode == "STANDSTILL" and pt.efficiency is None
    assert "equivalent sinusoidal RMS" in pt.rms_interpretation
    assert pt.Pdc_W == pytest.approx(6300.0)          # the 6.3 kW consumption is kept
    assert any("stall thermal capability is not inferred" in n for n in pt.notes)


def test_energy_modes_and_signs():
    by_id = {c["case_id"]: _point(c) for c in CASES}
    assert by_id["F01_FORWARD_MOTORING"].energy_mode == "MOTORING"
    assert by_id["F03_REGENERATION"].energy_mode == "REGENERATING"
    rev = by_id["F04_REVERSE_MOTORING"]
    # negative speed x negative torque is positive motoring power
    assert rev.speed_rpm < 0 and rev.Tshaft_Nm < 0 and rev.Pshaft_W > 0 and rev.energy_mode == "MOTORING"
    eta = by_id["F03_REGENERATION"].efficiency
    assert 0 < eta < 1


def test_spmsm_is_not_lossless():
    pt = _point(next(c for c in CASES if c["case_id"].startswith("F05")))
    assert pt.Pcu_W == pytest.approx(300.0)      # copper loss remains even with zero rotational/inverter loss
    assert pt.Pdc_W - pt.Pshaft_W == pytest.approx(300.0)


def test_forward_does_not_move_a_violating_point(drive):
    r = forward_evaluation(drive, scenario(6000, 600), -200, 400)
    assert r.point.id_A == -200 and r.point.iq_A == 400
    assert r.all_constraints_ok is False
    assert "not a feasible witness" in r.to_dict()["semantics"]
