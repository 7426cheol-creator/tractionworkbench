"""Second independent review (R2, baseline f6f166b): acceptance cases of the handoff contracts.

Each test states the CORRECT behaviour the handoff requires (the review's evidence scripts assert the observed
defects and are not merged).  Values are synthetic; they check equations, semantics and identity, not hardware.
"""

import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench import spec_fixtures as sf
from traction_workbench.errors import InputValidationError
from traction_workbench.models.components import TemperatureDependence
from traction_workbench.models.flux import CurrentBox
from traction_workbench.physics import DriveKernel, evaluate_point
from traction_workbench.scenario import DcSourceLimits, Scenario
from traction_workbench.solvers.capability import physical_capability, policy_capability
from traction_workbench.solvers.gate import check_witness
from traction_workbench.solvers.policy import PolicyEvaluator
from traction_workbench.status import Reason, Status

INF = math.inf
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verification"))


@pytest.fixture(scope="module")
def drive():
    return sf.synthetic_drive()


def _partial(d, id_box=(-500.0, -300.0), iq_box=(-600.0, 600.0)):
    return replace(d, motor=replace(d.motor, flux=replace(d.motor.flux, validity=CurrentBox(id_box, iq_box))))


def _claims(d, sc, T):
    r = PolicyEvaluator(d, sc).solve(T)
    return {c.name: c.status for c in r.claims}, r


# ---------------------------------------------------------------------------------------------- A1: C01 / C02 / C03


def test_c01_data_only_reduction_never_creates_a_full_domain_exclusion(drive):
    """Covered-data exclusion is not any-control exclusion: the partial-data case is UNKNOWN, the full data keep
    their FEASIBLE witness (16,316.8043 W)."""
    sc = Scenario("c01", 3000.0, 600.0, DcSourceLimits(18000.0, INF, INF, INF))
    full, rf = _claims(drive, sc, 50.0)
    assert full["physical_existence_with_dc"] is Status.FEASIBLE
    assert rf.point.Pdc_W == pytest.approx(16316.8043275, abs=1e-3)
    part, rp = _claims(_partial(drive), sc, 50.0)
    assert part["physical_existence_with_dc"] is Status.UNKNOWN
    assert Reason.OUTSIDE_MODEL_DOMAIN in rp.physical_dc.reasons
    assert part["policy_static"] is Status.UNKNOWN


@pytest.mark.parametrize("id_box,iq_box", [((-500.0, -300.0), (-600.0, 600.0)), ((-100.0, 0.0), (-600.0, 600.0)),
                                           ((-500.0, 0.0), (0.0, 100.0)), ((-500.0, 0.0), (-600.0, -50.0))])
@pytest.mark.parametrize("n,T", [(3000.0, 50.0), (3000.0, -40.0), (-3000.0, -50.0), (-3000.0, 40.0)])
def test_c01_invariant_over_partial_boxes_motoring_regen_and_reverse(drive, id_box, iq_box, n, T):
    """Whatever part of the data is removed, a full-data FEASIBLE never becomes an any-control INFEASIBLE."""
    sc = Scenario("c01inv", n, 600.0, DcSourceLimits(18000.0, 9000.0, INF, INF))
    full, _ = _claims(drive, sc, T)
    part, _ = _claims(_partial(drive, id_box, iq_box), sc, T)
    for name in ("physical_existence_with_dc", "electrical_existence"):
        if full[name] is Status.FEASIBLE:
            assert part[name] is not Status.INFEASIBLE, (name, id_box, iq_box, n, T)


def test_c02_zero_width_control_set_is_not_covered_by_area(drive):
    """iq fixed at 0 (a line): coverage is set inclusion; the origin is allowed but outside the data."""
    dd = replace(drive, domain=replace(drive.domain, iq_A=(0.0, 0.0)))
    sc = Scenario("c02", 0.0, 600.0, DcSourceLimits(1000.0, INF, INF, INF))
    full, rf = _claims(dd, sc, 0.0)
    assert full["policy_static"] is Status.FEASIBLE and full["physical_existence_with_dc"] is Status.FEASIBLE
    assert rf.point.Pdc_W == pytest.approx(200.0, abs=1e-6)
    part, rp = _claims(_partial(dd), sc, 0.0)
    assert rp.curve.coverage_limited and rp.curve.coverage_distance_A == pytest.approx(0.0, abs=1e-12)
    assert part["policy_static"] is Status.UNKNOWN and part["physical_existence_with_dc"] is Status.UNKNOWN


@pytest.mark.parametrize("line,limited", [(-300.0, False), (-500.0, False), (-299.999, True), (-500.001, True)])
def test_c02_control_line_on_and_just_outside_the_data_edge(drive, line, limited):
    from traction_workbench.models.flux import uncovered_distance
    part = _partial(drive)
    dd = replace(part, domain=replace(part.domain, id_A=(line, line)))
    k = DriveKernel(dd, Scenario("edge", 3000.0, 600.0, DcSourceLimits(INF, INF, INF, INF)))
    cov = uncovered_distance(k.uncovered_rectangles(), dd.domain.current_box, k.Imax)
    assert math.isfinite(cov) is limited


def test_c02_single_point_and_invalid_cell_edge():
    from traction_workbench.models.flux import complement_half_planes, uncovered_distance
    rects = complement_half_planes(-500.0, -300.0, -600.0, 600.0)
    assert uncovered_distance(rects, CurrentBox((0.0, 0.0), (0.0, 0.0)), 600.0) == 0.0          # outside point
    assert uncovered_distance(rects, CurrentBox((-400.0, -400.0), (0.0, 0.0)), 600.0) == INF     # inside point
    cell = [(-400.0, -350.0, 10.0, 20.0, False)]                                                # invalid cell
    assert uncovered_distance(cell, CurrentBox((-350.0, -350.0), (0.0, 30.0)), 600.0) == pytest.approx(math.hypot(350, 10))
    # an uncovered set exactly tangent to the current disk still counts (conservative)
    assert uncovered_distance(complement_half_planes(-600.0, 600.0, -600.0, 500.0),
                              CurrentBox((-600.0, 0.0), (0.0, 600.0)), 500.0) == pytest.approx(500.0)


@pytest.mark.parametrize("key,T", [("discharge_power_max_W", 50.0), ("discharge_current_max_A", 50.0),
                                   ("charge_power_max_W", -50.0), ("charge_current_max_A", -50.0)])
@pytest.mark.parametrize("m", [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0])
def test_c03_one_acceptance_set_for_gate_band_and_bounds(drive, key, T, m):
    """Around the boundary (in units of the gate tolerance): policy FEASIBLE implies physical FEASIBLE, and the
    forward point's constraint state agrees with both."""
    from traction_workbench.settings import DEFAULT_SETTINGS as S
    base = Scenario("c03", 3000.0, 600.0, DcSourceLimits(INF, INF, INF, INF))
    p = PolicyEvaluator(drive, base).solve(T).point.Pdc_W
    val = abs(p) if "power" in key else abs(p) / 600.0
    tol = max(S.power_abs_tol_W if "power" in key else S.current_abs_tol_A, S.constraint_rel_tol * val)
    lim = {"discharge_power_max_W": INF, "charge_power_max_W": INF, "discharge_current_max_A": INF,
           "charge_current_max_A": INF, key: val + m * tol}
    c, r = _claims(drive, Scenario("c03", 3000.0, 600.0, DcSourceLimits(**lim)), T)
    if c["policy_static"] is Status.FEASIBLE:
        assert c["physical_existence_with_dc"] is Status.FEASIBLE
    if c["physical_existence_with_dc"] is Status.INFEASIBLE:
        assert c["policy_static"] is not Status.FEASIBLE


def test_c03_zero_and_unlimited_caps(drive):
    zero = Scenario("z", 3000.0, 600.0, DcSourceLimits(0.0, INF, INF, INF))
    c, _ = _claims(drive, zero, 50.0)
    assert c["policy_static"] is Status.INFEASIBLE and c["physical_existence_with_dc"] is Status.INFEASIBLE
    unl = Scenario("u", 3000.0, 600.0, DcSourceLimits(INF, INF, INF, INF))
    c, _ = _claims(drive, unl, 50.0)
    assert c["policy_static"] is Status.FEASIBLE and c["physical_existence_with_dc"] is Status.FEASIBLE


def test_c03_certificate_reports_the_numerical_allowance_separately(drive):
    from traction_workbench.solvers.certificate import certify_torque
    k = DriveKernel(drive, Scenario("cert", 6000.0, 600.0, sf.synthetic_limits()))
    ev = PolicyEvaluator(drive, k.scenario)
    cap = physical_capability(ev, +1, include_dc=True)
    cert = certify_torque(k, +1, (cap.witness.id_A, cap.witness.iq_A))
    assert cert.valid and cert.numerical_allowance > 0
    assert cap.bound_Nm == pytest.approx(cert.upper_bound + cert.numerical_allowance, abs=1e-12)
    assert cap.value_Nm <= cap.bound_Nm


# ---------------------------------------------------------------------------------------------- A2 (part): C04


def test_c04_non_passive_temperature_law_is_rejected_at_input(drive):
    with pytest.raises(InputValidationError, match="non-passive"):
        replace(drive, motor=replace(drive.motor, reference_winding_temp_C=25.0,
                                     rs_temperature=TemperatureDependence(-0.02, (25.0, 150.0), "declared fit")))


def test_c04_normal_copper_law_and_ideal_fixture_keep_working(drive):
    ok = replace(drive, motor=replace(drive.motor, reference_winding_temp_C=25.0,
                                      rs_temperature=TemperatureDependence(0.00393, (-40.0, 180.0), "copper alpha")))
    c, r = _claims(ok, Scenario("cu", 3000.0, 600.0, sf.synthetic_limits(), winding_temp_C=150.0), 200.0)
    assert c["policy_static"] is Status.FEASIBLE and r.point.Pcu_W > 0
    ideal = replace(drive, motor=replace(drive.motor, Rs_ohm=0.0, reference_winding_temp_C=25.0,
                                         rs_temperature=TemperatureDependence(0.00393, (-40.0, 180.0), "ideal")))
    assert DriveKernel(ideal, Scenario("i", 3000.0, 600.0, sf.synthetic_limits(), winding_temp_C=100.0)).Rs == 0.0


def test_c04_gate_rejects_a_non_passive_energy_state(drive):
    k = DriveKernel(drive, Scenario("g", 3000.0, 600.0, sf.synthetic_limits()))
    pt = evaluate_point(k, -50.0, 200.0)
    assert check_witness(k, pt.id_A, pt.iq_A, point=pt).accepted
    bad = replace(pt, Pcu_W=-10.0)
    chk = check_witness(k, pt.id_A, pt.iq_A, point=bad)
    assert not chk.accepted and any("non-passive" in m for m in chk.messages)
    worse = replace(pt, energy_mode="ACCOUNTING_INCONSISTENCY")
    assert not check_witness(k, pt.id_A, pt.iq_A, point=worse).accepted


# ---------------------------------------------------------------------------------------------- A1.4 module witness


def test_a1_module_physical_capability_keeps_the_accepted_policy_witness():
    from make_module_anchor import module_drive
    md, _ = module_drive()
    ev = PolicyEvaluator(md, Scenario("m", 3000.0, 600.0, DcSourceLimits(18000.0, INF, INF, INF)))
    pol = policy_capability(ev, +1, certify=False)
    phys = physical_capability(ev, +1, include_dc=True)
    assert pol.value_Nm is not None and not pol.gate_messages
    assert phys.value_Nm is not None and phys.value_Nm >= pol.value_Nm - 1e-9
    assert not phys.gate_messages and not phys.certified
    assert any("achieved lower bound" in n for n in phys.notes)
    assert phys.bound_Nm is None or phys.bound_Nm >= phys.value_Nm
