"""Sizing, dominance, comparison, loss interval and rating behaviours."""

import math

import pytest

from conftest import scenario
from traction_workbench.analysis.compare import compare_scenarios
from traction_workbench.analysis.dominance import capability_dominance, requirement_relaxation
from traction_workbench.analysis.loss_interval import dc_acceptance_with_loss_interval
from traction_workbench.analysis.sizing import size_parameter
from traction_workbench.errors import InputValidationError
from traction_workbench.status import Status


def test_sizing_vdc_finds_minimum_with_coupled_limits(drive):
    r = size_parameter(drive, scenario(12000, 450), 150, "Vdc_V", (400, 800), samples=41)
    assert len(r.feasible_ranges) == 1
    lo = r.minimal_feasible
    assert 490 < lo < 505
    # at the minimum both voltage and the DC average-current limit are active (Vdc moves both)
    assert set(r.solution_at_minimal["active_constraints"]) >= {"VOLTAGE", "DC_DISCHARGE_CURRENT"}
    assert r.feasible_ranges[0][1] == 800.0
    assert any("no extrapolation" in n for n in r.notes)


def test_bigger_inverter_does_not_fix_low_voltage(drive):
    r = size_parameter(drive, scenario(12000, 450), 150, "I_peak_max_A", (600, 1500), samples=19)
    assert r.feasible_ranges == ()
    assert any("no feasible value" in n for n in r.notes)


def test_sizing_rejects_bad_range(drive):
    with pytest.raises(InputValidationError):
        size_parameter(drive, scenario(12000, 450), 150, "Vdc_V", (800, 400))
    with pytest.raises(InputValidationError):
        size_parameter(drive, scenario(12000, 450), 150, "unknown", (1, 2))


def test_dominance_active_vs_limiting(drive):
    r = capability_dominance(drive, scenario(12000, 600), +1)
    rows = {dict(x)["constraint"]: dict(x) for x in r.rows}
    assert rows["DC_DISCHARGE_POWER"]["classification"] == "limiting"
    assert rows["VOLTAGE"]["classification"] == "limiting"
    assert rows["CURRENT"]["classification"] == "not limiting alone"
    assert not r.joint


def test_joint_bottleneck_when_power_and_current_limits_coincide(drive):
    # 500 V x 400 A = 200 kW = discharge power limit: neither relaxation alone helps
    r = capability_dominance(drive, scenario(12000, 500), +1)
    rows = {dict(x)["constraint"]: dict(x) for x in r.rows}
    assert rows["DC_DISCHARGE_POWER"]["classification"] == "not limiting alone"
    assert rows["DC_DISCHARGE_CURRENT"]["classification"] == "not limiting alone"
    joint = [dict(j) for j in r.joint]
    assert any(set(j["constraints"]) == {"DC_DISCHARGE_POWER", "DC_DISCHARGE_CURRENT"} for j in joint)


def test_requirement_relaxation_joint(drive):
    r = requirement_relaxation(drive, scenario(12000, 450), 150)
    assert not any(dict(x)["sufficient_alone"] for x in r.rows)
    assert any(set(dict(j)["constraints"]) == {"VOLTAGE", "DC_DISCHARGE_CURRENT"} for j in r.joint)


def test_compare_explains_changes(drive):
    c = compare_scenarios(drive, [scenario(12000, 600, "600V"), scenario(12000, 450, "450V")], 150)
    text = "\n".join(c["changes_vs_baseline"])
    assert "electrical_existence FEASIBLE -> INFEASIBLE" in text
    assert "necessary condition violated" in text


def test_loss_interval_motoring_uses_largest_loss():
    r = dc_acceptance_with_loss_interval(195000, (2000, 8000), discharge_cap_W=200000)
    assert r.actual.status is Status.UNKNOWN            # 197..203 kW straddles 200 kW
    r2 = dc_acceptance_with_loss_interval(150000, (2000, 8000), discharge_cap_W=200000)
    assert r2.actual.status is Status.FEASIBLE and r2.robust.status is Status.FEASIBLE
    with pytest.raises(InputValidationError):
        dc_acceptance_with_loss_interval(1, (5, 1), discharge_cap_W=1)
