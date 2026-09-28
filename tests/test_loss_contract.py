"""The kernel's inverter-loss contract (system review R1-2): one place says which DC arguments a loss model supports.

* quadratic surrogate: the closed-form identity P_dc = T_em*omega_m + (1.5*Rs + a2)*I^2 + a0 (``i2_dc``) - the only
  basis of the I^2 band, the Lagrangian DC constraints, the cell bounds, the maximum-loss screen and grid P_dc;
* datasheet module: pointwise - DC claims need a directly evaluated witness; on a grid the DC limits are NOT
  EVALUATED (never shown as violated).
"""

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench import service as S
from traction_workbench.analysis.efficiency import ModuleCandidate
from traction_workbench.errors import InputValidationError
from traction_workbench.models.components import LOSS_MODULE, LOSS_QUADRATIC
from traction_workbench.physics import DriveKernel
from traction_workbench.scenario import Scenario
from traction_workbench.solvers.capability import physical_capability
from traction_workbench.solvers.common import dc_band_I2
from traction_workbench.solvers.policy import PolicyEvaluator
from traction_workbench.solvers.screens import regen_max_loss_vs_charge
from traction_workbench.viz.maps import idiq_plane
from traction_workbench.viz.sweeps import grid_extreme

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verification"))
from make_module_anchor import module_drive  # noqa: E402


@pytest.fixture(scope="module")
def lim():
    return api._limits({})


def test_quadratic_identity_is_the_kernel_evaluation(lim):
    """The identity every I^2 argument rests on equals the kernel's own P_dc at arbitrary points."""
    d = S.resolve_drive(None)
    k = DriveKernel(d, Scenario("t", 4500.0, 600.0, lim))
    assert k.loss_kind == LOSS_QUADRATIC and k.i2_dc is not None and not k.pointwise_loss
    rng = np.random.default_rng(3)
    D, Q = rng.uniform(-400, 0, 200), rng.uniform(-400, 400, 200)
    e = k.evaluate(D, Q)
    ok = np.asarray(e["ok"], bool)
    ident = k.i2_dc.pdc(e["tem"], k.omega_m, e["i2"])
    assert np.allclose(ident[ok], e["pdc"][ok], rtol=1e-12, atol=1e-9)
    assert k.i2_dc.c2_W_per_A2 == pytest.approx(1.5 * k.Rs + d.inverter.loss.ipk2_coeff_W_per_A2)


def test_module_model_is_pointwise_everywhere(lim):
    md, _ = module_drive()
    sc = Scenario("t", 3000.0, 600.0, lim)
    k = DriveKernel(md, sc)
    assert md.inverter.loss_kind == LOSS_MODULE and k.pointwise_loss and k.i2_dc is None and k.dc_defined
    assert dc_band_I2(k, 100.0) is None
    scr = regen_max_loss_vs_charge(k, -150.0)
    assert not scr.applicable and "datasheet module loss" in scr.statement
    cap = physical_capability(PolicyEvaluator(md, sc), +1, include_dc=True)
    assert any("point by point" in n for n in cap.notes)


def test_grid_dc_is_not_evaluated_with_a_pointwise_model(lim):
    """Before R1-2 the map showed no point meeting all limits and the grid envelope found no DC-feasible point:
    the NaN grid P_dc of a pointwise model was compared as if it were a violation."""
    md, _ = module_drive()
    pl = idiq_plane(md, lim, 3000.0, 600.0, T_request=150.0, resolution=61)
    assert pl["electrical_ok"].any()
    assert pl["all_ok"] is None and pl["Pdc"] is None and "point by point" in pl["dc_grid_note"]
    k = DriveKernel(md, Scenario("t", 3000.0, 600.0, lim))
    assert grid_extreme(k, +1, include_dc=True) is None
    assert grid_extreme(k, +1, include_dc=False) is not None
    q = idiq_plane(S.resolve_drive(None), lim, 3000.0, 600.0, T_request=150.0, resolution=61)
    assert q["all_ok"] is not None and q["all_ok"].any() and q["dc_grid_note"] is None


def test_service_plane_states_why_dc_contours_are_absent(lim):
    md, _ = module_drive()
    out = S.idiq_map(md, lim, 3000.0, 600.0, 150.0, resolution=61)
    assert "dc_discharge_limit" not in out["contours"] and "all_limits_feasible" not in out["regions"]
    assert "point by point" in out["dc_grid_note"]


def test_loss_models_are_typed(lim):
    d = S.resolve_drive(None)
    with pytest.raises(InputValidationError):
        replace(d.inverter, loss=None, module_loss=object(), module_Tj_C=150.0)
    with pytest.raises(InputValidationError):
        replace(d.inverter, loss=object())
    with pytest.raises(InputValidationError):
        ModuleCandidate("x", object(), 0.1)
