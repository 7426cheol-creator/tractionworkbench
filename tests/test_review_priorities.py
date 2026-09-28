"""Engineering review 6198099, priorities 3 and 5: PWM consequences at the decision's own operating point (the
fundamental current limit kept apart from the instantaneous peak bound) and the scope of the rotational / iron
loss (speed-only equivalent torque, loss sensitivity, the premise of the Vdc certificate)."""

import pytest

from traction_workbench import api
from traction_workbench import service as S
from traction_workbench import spec_fixtures as sf
from traction_workbench.decision import evaluate_requirement
from traction_workbench.requirement import Requirement


@pytest.fixture(scope="module")
def point():
    _d, rec, case = S.evaluate_case_full(api.case_from_body(
        {"requirement": {"id": "R", "text": "150 N*m at 12000 rpm", "torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 600}}))
    c = rec.conditions[0]
    return case.drive, c.scenario, c.primary.point


def test_the_fundamental_limit_and_the_instantaneous_peak_are_different_statements(point):
    d, sc, pt = point
    r = api.pwm_risk_at(d, sc, pt)
    fu, pk = r["fundamental"], r["instantaneous_peak"]
    assert fu["i_peak_A"] == pytest.approx(pt.i_peak_A) and fu["limit_A"] == pytest.approx(600.0)
    assert pk["bound_A"] > fu["i_peak_A"] and "not the exact" in pk["meaning"]
    assert pk["status"] == "WITHIN" and pk["limit_A"] == 700.0
    tight = api.pwm_risk_at(d, sc, pt, {"peak_limit_A": fu["i_peak_A"] + 1.0})     # between the two quantities
    assert tight["instantaneous_peak"]["status"] == "EXCEEDS_BOUND"
    none = api.pwm_risk_at(d, sc, pt, {"peak_limit_A": None})
    assert none["instantaneous_peak"]["status"] == "UNKNOWN" and "not a peak limit" in none["instantaneous_peak"]["reason"]


def test_same_point_same_carrier_for_rms_lines_and_dc_link(point):
    d, sc, pt = point
    r = api.pwm_risk_at(d, sc, pt)
    assert r["rms"]["total_A"] > r["rms"]["fundamental_A"] > 0
    amps = [ln["I_pk_A"] for ln in r["lines"]]
    assert amps == sorted(amps, reverse=True) and len(amps) == 5
    assert r["dc_link"]["I_cap_rms_A"] > 0 and r["dc_link"]["fsw_used_Hz"] == pytest.approx(r["fsw_waveform_used_Hz"])
    assert r["fsw_requested_Hz"] == 10e3 and r["fsw_waveform_used_Hz"] != r["fsw_requested_Hz"]     # 12000 rpm
    assert r["motor_pwm_loss"]["interval_W"][0] > 0
    assert any("NVH" in x for x in r["not_evaluated"])


def test_at_standstill_the_lines_carry_no_order_and_the_dc_link_says_why():
    _d, rec, case = S.evaluate_case_full(api.case_from_body(
        {"requirement": {"id": "R0", "text": "300 N*m at standstill", "torque_Nm": 300, "speed_rpm": 0, "Vdc_V": 600}}))
    c = rec.conditions[0]
    r = api.pwm_risk_at(case.drive, c.scenario, c.primary.point)
    assert r["ripple_quasi_static"] and r["lines"] and all(ln["order"] is None for ln in r["lines"])
    assert r["dc_link"] is None and "f_e = 0" in r["dc_link_reason"]


def test_the_rotational_item_states_its_scope_and_the_efficiency_sensitivity():
    led = api.efficiency({})["ledger"]
    rot = next(i for i in led["loss_items"] if i["item"].startswith("rotational"))
    iron = api._eff_drive(api.EXAMPLE_EFFICIENCY).motor.rotational_loss.includes_iron_loss
    assert "speed-only" in rot["scope"]
    assert ("double counting" in rot["scope"]) if iron else ("NOT declared" in rot["scope"])
    sens = led["loss_sensitivity"]
    assert sens and all(x["delta_eta_points"] < 0 for x in sens)
    assert [x["delta_eta_points"] for x in sens] == sorted(x["delta_eta_points"] for x in sens)   # biggest first
    assert not any(x["item"].startswith("motor: PWM") for x in sens)


def test_the_vdc_certificate_names_its_speed_only_loss_premise():
    rec = evaluate_requirement(Requirement("R", "r", 100.0, 12000.0, (550.0, 650.0), Vdc_quantifier="for_all"),
                               sf.synthetic_drive(), source_limits=sf.synthetic_limits(), with_capability=False)
    names = [c["condition"] for c in rec.range_certificates[0]["checks"]]
    assert any("speed-only rotational / iron loss" in n for n in names)
