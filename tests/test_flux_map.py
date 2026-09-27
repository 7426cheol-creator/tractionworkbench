"""V3: nonlinear manufactured flux map - nodes, orientation, torque, reciprocity,
refinement, coverage and no-extrapolation behaviour."""

import math

import numpy as np
import pytest

from conftest import golden, scenario
from traction_workbench import spec_fixtures as sf
from traction_workbench.errors import InputValidationError, OutsideModelDomain
from traction_workbench.models import FluxMapModel, FluxMapPlane
from traction_workbench.physics import DriveKernel, evaluate_point, forward_evaluation
from traction_workbench.solvers.policy import solve_policy

FX = golden("manufactured_flux_map.json")
TP = FX["test_point"]


def analytic(d, q):
    psd = 0.1 + 0.0002 * d - 1e-10 * d ** 3 - 1e-10 * d * q ** 2
    psq = 0.0004 * q - 2e-10 * q ** 3 - 1e-10 * d ** 2 * q
    return psd, psq


def plane_from_analytic(step, lo=-200.0, hi=200.0, valid=None):
    ax = np.arange(lo, hi + step / 2, step)
    D, Q = np.meshgrid(ax, ax, indexing="ij")
    psd, psq = analytic(D, Q)
    return FluxMapPlane(ax, ax, psd, psq, valid)


@pytest.fixture(scope="module")
def plane():
    return sf.manufactured_flux_plane()


def test_nodes_reproduced_exactly(plane):
    D, Q = np.meshgrid(plane.id_axis_A, plane.iq_axis_A, indexing="ij")
    psd, psq, ok = plane.interpolate(D, Q)
    a, b = analytic(D, Q)
    assert ok.all()
    assert np.max(np.abs(psd - a)) < 1e-15 and np.max(np.abs(psq - b)) < 1e-15


def test_test_point_flux_and_torque(plane):
    psd, psq, ok = plane.interpolate(TP["id_A_peak"], TP["iq_A_peak"])
    assert float(psd) == pytest.approx(TP["psi_d_Wb"], abs=1e-15)
    assert float(psq) == pytest.approx(TP["psi_q_Wb"], abs=1e-15)
    k = DriveKernel(sf.manufactured_map_drive(), scenario(0, 600))
    pt = evaluate_point(k, TP["id_A_peak"], TP["iq_A_peak"])
    assert pt.Te_Nm == pytest.approx(TP["Te_Nm"], abs=1e-12)


def test_axis_orientation_is_enforced():
    ax = np.arange(-200.0, 201.0, 10.0)
    axq = np.arange(-100.0, 101.0, 10.0)
    D, Q = np.meshgrid(ax, axq, indexing="ij")
    psd, psq = analytic(D, Q)
    FluxMapPlane(ax, axq, psd, psq)                       # rows = id: accepted
    with pytest.raises(InputValidationError, match="rows must follow the id axis"):
        FluxMapPlane(ax, axq, psd.T, psq.T)               # transposed arrays: rejected


def test_reciprocity_and_differential_inductance(plane):
    rep = FluxMapModel((plane,)).planes[0].reciprocity_report()
    assert rep["passed"] and rep["max_abs_mismatch_H"] < 1e-15
    assert rep["nodes_not_positive_definite"] == 0
    # analytic derivative values at the test point
    d0, q0 = TP["id_A_peak"], TP["iq_A_peak"]
    assert 0.0002 - 3e-10 * d0 ** 2 - 1e-10 * q0 ** 2 == pytest.approx(TP["dpsi_d_did_H"], abs=1e-15)
    assert -2e-10 * d0 * q0 == pytest.approx(TP["dpsi_d_diq_H"], abs=1e-15)


def test_non_conservative_map_is_detected(plane):
    bad = np.array(plane.psi_q_Wb)
    bad[:, :] += 1e-5 * np.linspace(-1, 1, bad.shape[0])[:, None] ** 3 * 200   # breaks d psi_q / d id symmetry
    p2 = FluxMapPlane(plane.id_axis_A, plane.iq_axis_A, plane.psi_d_Wb, bad)
    rep = p2.reciprocity_report()
    assert rep["passed"] is False and rep["nodes_failing_reciprocity"] > 0


def test_off_grid_refinement_is_second_order():
    """Interpolation error (separate from solver error) shrinks ~4x per grid halving (bilinear, h^2)."""
    rng = np.random.default_rng(7)
    pts = rng.uniform(-190.0, 190.0, size=(4000, 2))
    rms, peak = [], []
    for step in (20.0, 10.0, 5.0, 2.5):
        pl = plane_from_analytic(step)
        psd, psq, ok = pl.interpolate(pts[:, 0], pts[:, 1])
        assert ok.all()
        a, b = analytic(pts[:, 0], pts[:, 1])
        err = 6.0 * (psd * pts[:, 1] - psq * pts[:, 0]) - 6.0 * (a * pts[:, 1] - b * pts[:, 0])
        rms.append(float(np.sqrt(np.mean(err ** 2))))
        peak.append(float(np.max(np.abs(err))))
    ratios = [rms[i] / rms[i + 1] for i in range(len(rms) - 1)]
    assert all(3.6 < r < 4.4 for r in ratios), (rms, ratios)
    assert peak[1] < 0.01   # fixture spacing (10 A): torque interpolation error below 0.01 N*m


def test_outside_map_is_not_extrapolated():
    d = sf.manufactured_map_drive()
    r = forward_evaluation(d, scenario(3000, 600), -250.0, 100.0)
    assert not r.evaluable and r.reason.value == "OUTSIDE_MODEL_DOMAIN" and r.point is None
    psd, psq, ok = sf.manufactured_flux_plane().interpolate(200.0000001, 0.0)
    assert not bool(ok) and math.isnan(float(psd))


def test_hole_is_not_interpolated():
    step = 10.0
    ax = np.arange(-200.0, 200.1, step)
    valid = np.ones((ax.size, ax.size), bool)
    i = int(np.flatnonzero(ax == -100.0)[0])
    j = int(np.flatnonzero(ax == 100.0)[0])
    valid[i, j] = False                                    # one missing node -> four invalid cells
    pl = plane_from_analytic(step, valid=valid)
    _, _, ok = pl.interpolate(np.array([-95.0, -105.0, -100.0, -50.0]), np.array([95.0, 105.0, 100.0, 50.0]))
    assert list(ok) == [False, False, False, True]


def test_invalid_axes_rejected():
    ax = np.arange(-200.0, 201.0, 10.0)
    D, Q = np.meshgrid(ax, ax, indexing="ij")
    psd, psq = analytic(D, Q)
    dup = ax.copy()
    dup[3] = dup[2]
    with pytest.raises(InputValidationError, match="duplicate"):
        FluxMapPlane(dup, ax, psd, psq)
    with pytest.raises(InputValidationError, match="strictly increasing"):
        FluxMapPlane(ax[::-1], ax, psd, psq)
    nan = psd.copy()
    nan[5, 5] = np.nan
    with pytest.raises(InputValidationError, match="non-finite"):
        FluxMapPlane(ax, ax, nan, psq)


def test_declared_symmetry_only():
    ax = np.arange(-200.0, 201.0, 10.0)
    axq = np.arange(0.0, 201.0, 10.0)
    D, Q = np.meshgrid(ax, axq, indexing="ij")
    psd, psq = analytic(D, Q)
    half = FluxMapPlane(ax, axq, psd, psq)
    undeclared = FluxMapModel((half,))
    declared = FluxMapModel((half,), symmetry="q_odd")
    d0, q0 = -60.0, -90.0
    _, _, ok = undeclared.planes[0].interpolate(d0, q0)
    assert not bool(ok)                                     # unknown quadrant stays unknown
    a, b = analytic(d0, q0)
    psd2, psq2, ok2 = declared.planes[0].interpolate(d0, q0)
    assert bool(ok2) and float(psd2) == pytest.approx(a, abs=1e-15) and float(psq2) == pytest.approx(b, abs=1e-15)


def test_temperature_planes_not_interpolated_without_basis():
    ax = np.arange(-200.0, 201.0, 10.0)
    D, Q = np.meshgrid(ax, ax, indexing="ij")
    psd, psq = analytic(D, Q)
    p20 = FluxMapPlane(ax, ax, psd, psq, magnet_temp_C=20.0)
    p120 = FluxMapPlane(ax, ax, psd * 0.9, psq, magnet_temp_C=120.0)
    m = FluxMapModel((p20, p120))
    assert m.plane_for(20.0)[0] is m.planes[0]
    with pytest.raises(OutsideModelDomain):
        m.plane_for(80.0)
    with pytest.raises(OutsideModelDomain):
        m.plane_for(None)
    with pytest.raises(InputValidationError):
        FluxMapModel((p20, p120), temperature_interpolation="linear")      # basis missing
    ml = FluxMapModel((p20, p120), temperature_interpolation="linear",
                      temperature_interpolation_basis="test-only linear PM remanence assumption")
    pl, note = ml.plane_for(70.0)
    assert "linear temperature interpolation" in note
    assert float(pl.interpolate(0.0, 0.0)[0]) == pytest.approx(0.095, abs=1e-12)


def test_map_inverse_solution_consistent_with_fine_map():
    """Coarse (fixture) vs 1 A analytic map: the decision-relevant difference is reported, not hidden."""
    coarse = sf.manufactured_map_drive()
    fine = sf.manufactured_map_drive(FluxMapModel((plane_from_analytic(1.0),)))
    s = scenario(6000, 600)
    a = solve_policy(coarse, s, 100.0)
    b = solve_policy(fine, s, 100.0)
    assert a.policy_claim.status.value == "FEASIBLE" and b.policy_claim.status.value == "FEASIBLE"
    assert abs(a.point.Tshaft_Nm - 100.0) < 1e-6
    assert abs(a.point.i_peak_A - b.point.i_peak_A) < 0.1    # interpolation effect on the current, ~0.02 A
    cert = dict(a.certificates)["minimum_current"]
    assert cert["certified"] and cert["gap_A"] < 0.2


def test_map_infeasibility_is_bounded_search_evidence():
    d = sf.manufactured_map_drive()
    sol = solve_policy(d, scenario(12000, 600), 50.0)
    assert sol.electrical.status.value == "INFEASIBLE"
    assert any(e.kind.value == "bounded_search" for e in sol.electrical.evidence)


def test_incomplete_coverage_is_not_a_physical_limit():
    """S04: the allowed domain extends beyond the map; no solution within data -> UNKNOWN, not INFEASIBLE."""
    d = sf.manufactured_map_drive(i_max=600.0, id_range=(-500.0, 0.0), iq_range=(-600.0, 600.0))
    sol = solve_policy(d, scenario(12000, 600), 50.0)
    assert sol.electrical.status.value == "UNKNOWN"
    assert "OUTSIDE_MODEL_DOMAIN" in [r.value for r in sol.electrical.reasons]
    assert sol.policy_claim.status.value == "UNKNOWN"
