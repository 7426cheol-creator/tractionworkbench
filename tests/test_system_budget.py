"""System budgets (system view, item 11): the three stacks and their shares by hand, break-even growth, top-down
allocations that stack to the limit, the torque contributions against the dq torque equation, the functional-safety
view (window, false trip, undetected deviation) and the FTTI / cycle templates."""

import math

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions import system_budget as sb
from traction_workbench.project import builtin_project, check_project

C = sb.Contributor
DRIVE = api._drive({})
FL = DRIVE.motor.flux
P = DRIVE.motor.pole_pairs


def _T(i_d, i_q, psi=None, Ld=None, Lq=None):
    psi, Ld, Lq = (FL.psi_pm_Wb if psi is None else psi), (FL.Ld_H if Ld is None else Ld), (FL.Lq_H if Lq is None else Lq)
    return 1.5 * P * (psi * i_q + (Ld - Lq) * i_d * i_q)


def _custom():
    return [C("divider", "divider", 2.4, "random"), C("adc", "adc", 1.8, "random"),
            C("iso", "isolation", 1.5, "systematic")]


def test_three_stacks_and_shares_by_hand():
    b = sb.evaluate_budget("v", "V", 6.0, _custom(), "mixed")
    assert b["stacks"]["worst_case"] == pytest.approx(5.7)
    assert b["stacks"]["rss"] == pytest.approx(math.sqrt(2.4 ** 2 + 1.8 ** 2 + 1.5 ** 2))
    assert b["stacks"]["mixed"] == pytest.approx(1.5 + 3.0)               # sqrt(2.4^2 + 1.8^2) = 3
    assert b["status"] == "PASS" and b["margin"] == pytest.approx(1.5)
    sh = {r["id"]: r["share"] for r in b["contributors"]}
    assert sum(sh.values()) == pytest.approx(1.0)
    assert sh["iso"] == pytest.approx(1.5 / 4.5)
    assert sh["divider"] == pytest.approx((3.0 / 4.5) * 2.4 ** 2 / 9.0)
    for comb in ("worst_case", "rss"):
        s = sb.evaluate_budget("v", "V", 6.0, _custom(), comb)
        assert sum(r["share"] for r in s["contributors"]) == pytest.approx(1.0)


def test_break_even_growth_brings_the_stack_exactly_to_the_limit():
    for comb in sb.COMBINATIONS:
        b = sb.evaluate_budget("v", "V", 6.0, _custom(), comb)
        for r in b["contributors"]:
            g = r["break_even_growth"]
            grown = [C(c.id, c.title, c.value + (g if c.id == r["id"] else 0.0), c.kind) for c in _custom()]
            assert sb.evaluate_budget("v", "V", 6.0, grown, comb)["total"] == pytest.approx(6.0, rel=1e-12)


def test_allocations_stack_to_the_limit():
    for comb in sb.COMBINATIONS:
        for how in ("equal", "proportional"):
            b = sb.evaluate_budget("v", "V", 6.0, _custom(), comb, how)
            alloc = [C(r["id"], r["id"], r["allocation"], r["kind"]) for r in b["contributors"]]
            assert sb.evaluate_budget("v", "V", 6.0, alloc, comb)["total"] == pytest.approx(6.0, rel=1e-12)
    # mixed, equal: one systematic + two random share a = L / (1 + sqrt 2)
    b = sb.evaluate_budget("v", "V", 6.0, _custom(), "mixed", "equal")
    assert b["contributors"][0]["allocation"] == pytest.approx(6.0 / (1 + math.sqrt(2)))
    assert [r["allocation_status"] for r in b["contributors"]] == ["PASS", "PASS", "PASS"]
    d = sb.evaluate_budget("v", "V", 6.0, [C("a", "a", 2.0, allocation=1.5), C("b", "b", 1.0, allocation=None)],
                           "worst_case", "declared")
    assert [r["allocation_status"] for r in d["contributors"]] == ["FAIL", "UNKNOWN"]


def test_unknown_contributors_are_never_zero():
    cs = [C("a", "a", 2.0), C("b", "b", None)]
    b = sb.evaluate_budget("x", "u", 5.0, cs, "worst_case")
    assert b["status"] == "UNKNOWN" and b["missing"] == ["b"] and not b["complete"]
    over = sb.evaluate_budget("x", "u", 1.5, cs, "worst_case")
    assert over["status"] == "FAIL"                        # the known part alone already exceeds the limit
    assert sb.evaluate_budget("x", "u", None, [C("a", "a", 1.0)])["status"] == "UNKNOWN"
    with pytest.raises(InputValidationError):
        C("a", "a", 1.0, kind="sometimes")
    with pytest.raises(InputValidationError):
        sb.evaluate_budget("x", "u", 1.0, cs, "linear")


def test_torque_contributions_against_the_dq_equation():
    sc = api.Scenario("t", 1000.0, 600.0, api.sf.synthetic_limits())
    i_d, i_q = -100.0, 200.0
    T0 = _T(i_d, i_q)
    err = {"current_gain_pct": 1.0, "resolver_offset_deg_e": 0.5, "magnet_temp_dev_K": 15.0,
           "magnet_coeff_per_K": -0.0011, "psi_tol_pct": 1.0, "Ld_tol_pct": 2.0, "Lq_tol_pct": 2.0,
           "estimator_pct": 1.0, "monitor_mismatch_abs_Nm": 4.0}
    con = sb.torque_contributions(DRIVE, sc, i_d, i_q, T0, err)
    # gain: the loop regulates the measured current - the true current is i / (1 -+ g)
    gain = max(abs(_T(i_d / (1 + s * 0.01), i_q / (1 + s * 0.01)) - T0) for s in (1, -1))
    assert con["current_gain"] == pytest.approx(gain, rel=1e-9)
    # resolver offset: the true current is the reference rotated by -+ delta
    dl = math.radians(0.5)
    rot = max(abs(_T(i_d * math.cos(s * dl) - i_q * math.sin(s * dl), i_d * math.sin(s * dl) + i_q * math.cos(s * dl))
                  - T0) for s in (1, -1))
    assert con["resolver_offset"] == pytest.approx(rot, rel=1e-9)
    # magnet temperature: only the PM term moves, by psi * |alpha| * dT
    assert con["magnet_temperature"] == pytest.approx(1.5 * P * FL.psi_pm_Wb * 0.0011 * 15.0 * i_q, rel=1e-9)
    # model tolerance: the worst corner - psi up and L_d - L_q more negative (i_d < 0)
    corner = _T(i_d, i_q, FL.psi_pm_Wb * 1.01, FL.Ld_H * 0.98, FL.Lq_H * 1.02) - T0
    assert con["model_tolerance"] == pytest.approx(corner, rel=1e-9)
    assert con["estimator"] == pytest.approx(0.01 * T0)
    assert con["monitor_mismatch"] == pytest.approx(4.0)
    assert con["current_offset"] is None                    # not declared - not zero


def test_current_offset_peak_over_a_revolution():
    sc = api.Scenario("t", 1000.0, 600.0, api.sf.synthetic_limits())
    i_d, i_q, o = -100.0, 200.0, 2.0
    con = sb.torque_contributions(DRIVE, sc, i_d, i_q, _T(i_d, i_q), {"current_offset_A": o})
    # a DC offset on phase a: (2 o / 3) along alpha, rotating in dq at the electrical frequency
    th = np.linspace(0.0, 2 * math.pi, 20001)
    da = 2.0 * o / 3.0
    fine = np.max(np.abs(_T(i_d - da * np.cos(th), i_q + da * np.sin(th)) - _T(i_d, i_q)))
    assert con["current_offset"] == pytest.approx(fine, rel=2e-3)
    assert con["current_offset"] <= fine * (1 + 1e-12)     # the 144-point scan never exceeds the true peak


def test_validate_torque_errors():
    sb.validate_torque_errors({"current_gain_pct": 1.0, "magnet_coeff_per_K": -0.001, "basis": {}})
    with pytest.raises(InputValidationError):
        sb.validate_torque_errors({"current_gain_pct": -1.0})
    with pytest.raises(InputValidationError):
        sb.validate_torque_errors({"current_gain": 1.0})


def test_monitor_view_follows_the_sensor_roles():
    fs = api.example("BUDGET", builtin_project())["fault_sim"]
    sees, why = sb.monitor_view(fs)
    assert sees == [] and "control's sensors" in why
    own = {**fs, "roles": {**fs["roles"], "current_mon_a": "CS_C", "current_mon_b": "CS_C"}}
    assert sb.monitor_view(own)[0] == ["current_gain", "current_offset"]


def test_fusa_row_window_false_trip_and_undetected_deviation():
    fusa = {"torque_window": {"abs_Nm": 50.0, "rel": 0.2}, "monitor": {"abs_Nm": 30.0, "rel": 0.15},
            "monitor_sees": ["current_gain"]}
    con = {"current_gain": 3.0, "resolver_offset": 2.0, "estimator": 4.0, "monitor_mismatch": 5.0}
    r = sb._fusa_row(fusa, 100.0, con, ["current_gain", "resolver_offset", "estimator"], True)
    assert r["fusa_window_Nm"] == 50.0 and r["monitor_threshold_Nm"] == 30.0
    assert r["fusa_error_Nm"] == pytest.approx(9.0) and r["fusa_window_status"] == "PASS"
    assert r["monitor_seen_Nm"] == pytest.approx(3.0 + 5.0) and r["false_trip_status"] == "PASS"
    assert r["undetected_Nm"] == pytest.approx(30.0 + 5.0 + 6.0) and r["undetected_status"] == "PASS"
    hi = sb._fusa_row(fusa, 500.0, {k: 5 * v for k, v in con.items()}, ["current_gain", "resolver_offset", "estimator"],
                      True)
    assert hi["undetected_Nm"] == pytest.approx(75.0 + 25.0 + 30.0) and hi["undetected_status"] == "FAIL"
    nomm = sb._fusa_row(fusa, 100.0, {**con, "monitor_mismatch": None}, ["current_gain"], True)
    assert nomm["false_trip_status"] == "UNKNOWN" and nomm["undetected_status"] == "UNKNOWN"


def test_torque_budget_api_on_the_project():
    r = api.budget_torque({"torque": {"speeds_rpm": [3000.0, 12000.0], "fractions": [-0.5, 0.5, 1.0]}})
    ev = [p for p in r["points"] if "total_Nm" in p]
    assert ev and r["status"] in ("PASS", "FAIL")
    for p in ev:
        assert p["limit_Nm"] == pytest.approx(max(5.0, 0.05 * abs(p["torque_Nm"])))
        assert p["status"] == ("PASS" if p["total_Nm"] <= p["limit_Nm"] else "FAIL")
        assert "monitor_mismatch" not in [k for k in p["stacks_Nm"]]
    w = r["worst_point"]
    assert w["margin_Nm"] == min(p["margin_Nm"] for p in ev)
    wb = r["worst_point_budget"]
    assert wb["total"] == pytest.approx(w["total_Nm"]) and wb["allocation_method"] == "proportional"
    f = r["fusa"]
    assert f["torque_window"] == {"abs_Nm": 50.0, "rel": 0.2} and f["monitor"] == {"abs_Nm": 30.0, "rel": 0.15}
    assert f["monitor_sees"] == [] and f["window_status"] == "PASS"
    assert r["not_declared"] == []


def test_ftti_budget_is_the_timing_worst_path():
    b = api.budget_ftti({})
    assert b["total"] == pytest.approx(b["timing_worst_ms"], rel=1e-12)
    assert b["status"] == ("PASS" if b["total"] <= b["limit"] else "FAIL")
    assert sum(r["allocation"] for r in b["contributors"]) == pytest.approx(b["limit"])


def test_cycle_loss_budget_per_component():
    res = {"cycle": {"name": "x", "distance_km": 10.0}, "complete": True,
           "losses_kWh": {"inverter": 0.05, "motor_copper": 0.02}}
    b = sb.cycle_loss_budget(res, {"inverter": 4.0, "motor_copper": 3.0}, 8.0)
    v = {r["id"]: r["value"] for r in b["contributors"]}
    assert v == pytest.approx({"inverter": 5.0, "motor_copper": 2.0})
    assert b["total"] == pytest.approx(7.0) and b["status"] == "PASS"
    assert {r["id"]: r["allocation_status"] for r in b["contributors"]} == {"inverter": "FAIL", "motor_copper": "PASS"}
    inc = sb.cycle_loss_budget({**res, "complete": False}, {}, 8.0)
    assert inc["status"] == "UNKNOWN" and set(inc["missing"]) == {"inverter", "motor_copper"}


def test_project_torque_errors_section_and_rule():
    prj = builtin_project()
    assert prj.has("torque_errors") and "torque_errors" in prj.usage("budget")["sections"]
    f = [x for x in check_project(prj)["findings"] if x["rule"] == "PRJ-15"]
    assert f and f[0]["status"] == "OK"
    bad = prj.with_section("torque_errors", {**prj.data("torque_errors"), "resolver_offset_deg_e": 1.0})
    g = [x for x in check_project(bad)["findings"] if x["rule"] == "PRJ-15"]
    assert g[0]["status"] == "INCONSISTENT" and "RES offset" in g[0]["detail"]


def test_custom_budget_api():
    b = api.budget_custom({})
    assert b["status"] == "PASS" and b["stacks"]["mixed"] == pytest.approx(4.5)
    b2 = api.budget_custom({"custom": {"limit": 4.0}})
    assert b2["status"] == "FAIL"
