"""Boundary behaviour (H10) and solver cross-checks."""

import math

import numpy as np
import pytest

from conftest import golden, scenario
from traction_workbench import spec_fixtures as sf
from traction_workbench.physics import DriveKernel
from traction_workbench.solvers.bounds import CellBounds
from traction_workbench.solvers.exact import analyze_constant
from traction_workbench.solvers.policy import solve_policy
from traction_workbench.solvers.sampled import SampledCurveTracer
from traction_workbench.status import Status

SPM = {"Rs_phase_ohm": 0.02, "psi_pm_Wb": 0.08, "Ld_H": 0.0003, "Lq_H": 0.0003,
       "drag_coefficient_Nm_per_rad_s": 0, "inverter_loss_offset_W": 0, "inverter_loss_Ipk2_coefficient_ohm": 0}


def test_spmsm_inverse_no_singularity():
    d = sf.synthetic_drive(SPM)
    sol = solve_policy(d, scenario(3000, 400), 48.0)
    assert sol.policy_claim.status is Status.FEASIBLE
    assert sol.point.id_A == pytest.approx(0.0, abs=1e-9) and sol.point.iq_A == pytest.approx(100.0, abs=1e-9)
    # at high speed the SPMSM needs negative id (field weakening) - still no division problem
    hi = solve_policy(d, scenario(9000, 400), 10.0)
    assert hi.point is not None and hi.point.id_A < 0


def test_zero_torque_at_standstill():
    d = sf.synthetic_drive()
    sol = solve_policy(d, scenario(0, 600), 0.0)
    assert sol.point.id_A == 0.0 and sol.point.iq_A == 0.0
    assert sol.point.energy_mode == "STANDSTILL"


def test_reverse_motoring_mirrors_forward():
    d = sf.synthetic_drive()
    fwd = solve_policy(d, scenario(3000, 600), 300.0).point
    rev = solve_policy(d, scenario(-3000, 600), -300.0).point
    assert rev.id_A == pytest.approx(fwd.id_A, abs=1e-9) and rev.iq_A == pytest.approx(-fwd.iq_A, abs=1e-9)
    assert rev.energy_mode == "MOTORING" and rev.Pshaft_W == pytest.approx(fwd.Pshaft_W, rel=1e-12)


def test_salient_machine_with_k_zero_inside_domain():
    """Ld > Lq and a domain containing id0 = -psi/(Ld-Lq): the k = 0 pole is handled, not divided by."""
    d = sf.synthetic_drive({"Ld_H": 0.0006, "Lq_H": 0.0002})
    k = DriveKernel(d, scenario(3000, 600))
    id0 = -0.1 / (0.0006 - 0.0002)
    assert d.domain.id_A[0] < id0 < d.domain.id_A[1]
    ca = analyze_constant(k, 100.0)
    assert not ca.empty and all(s.start.id_A > id0 or s.end.id_A < id0 for s in ca.segments)
    z = analyze_constant(DriveKernel(d, scenario(0, 600)), 0.0)   # zero torque: iq = 0 branch and k = 0 line
    assert z.min_point.id_A == 0.0 and z.min_point.iq_A == 0.0


def test_missing_rotational_loss_is_not_promoted():
    from dataclasses import replace
    d = sf.synthetic_drive()
    d2 = replace(d, motor=replace(d.motor, rotational_loss=None))
    sol = solve_policy(d2, scenario(3000, 600), 300.0)
    assert sol.electrical.status is Status.UNKNOWN and "MISSING_INPUT" in [r.value for r in sol.electrical.reasons]


def test_missing_inverter_loss_keeps_dc_unknown_but_uses_bound():
    from dataclasses import replace
    d = sf.synthetic_drive()
    d2 = replace(d, inverter=replace(d.inverter, loss=None))
    ok = solve_policy(d2, scenario(12000, 600), 150.0)
    assert ok.dc_claim.status is Status.UNKNOWN
    bad = solve_policy(d2, scenario(6000, 600), 350.0)       # P_ac already above 200 kW
    assert bad.dc_claim.status is Status.INFEASIBLE


@pytest.mark.parametrize("case", golden("golden_inverse.json")["cases"], ids=lambda c: c["case_id"])
def test_sampled_tracer_cross_checks_exact(case):
    """Two different production paths (root enumeration vs sampled tracing) agree."""
    d = sf.synthetic_drive()
    inp = case["input"]
    k = DriveKernel(d, scenario(inp["n_rpm"], inp["Vdc_V"]))
    ex = analyze_constant(k, inp["Tshaft_requested_Nm"])
    sa = SampledCurveTracer(k).analyze(inp["Tshaft_requested_Nm"])
    assert ex.empty == sa.empty
    if not ex.empty:
        assert abs(ex.min_point.id_A - sa.min_point.id_A) < 1e-4
        assert abs(ex.min_point.iq_A - sa.min_point.iq_A) < 1e-4


def test_cell_bounds_agree_with_exact_enumeration():
    d = sf.synthetic_drive()
    k = DriveKernel(d, scenario(12000, 450))
    assert CellBounds(k).prove_empty(150 + k.tau_rot).status == "PROVEN_EMPTY"
    k2 = DriveKernel(d, scenario(12000, 600))
    ex = analyze_constant(k2, 150.0)
    lb = CellBounds(k2).min_current_lower_bound(150 + k2.tau_rot, ex.min_point.I2)
    assert lb.bound <= ex.min_point.I2 * (1 + 1e-12)          # a valid lower bound
    assert math.sqrt(ex.min_point.I2) - math.sqrt(lb.bound) < 0.5
