"""Thermal-cycle counting and conditional damage (independent review section 12)."""

import math

import numpy as np
import pytest

from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.lifetime import (CyclingModel, cycle_analysis, damage, foster_trace, rainflow,
                                                     turning_points)


def _hist(cycles):
    h = {}
    for c in cycles:
        h[round(c["range"], 9)] = h.get(round(c["range"], 9), 0.0) + c["count"]
    return h


def test_astm_e1049_reference_example():
    x = [-2, 1, -3, 5, -1, 3, -4, 4, -2]                 # ASTM E1049 rainflow example
    cyc = rainflow(turning_points(np.arange(len(x)), x))
    assert _hist(cyc) == {3.0: 0.5, 4.0: 1.5, 6.0: 0.5, 8.0: 1.0, 9.0: 0.5}
    assert sum(c["count"] for c in cyc) == pytest.approx(4.0)


def test_nested_cycle_and_plateau_dwell_kept():
    t = np.arange(12.0)
    x = [20, 80, 80, 80, 50, 70, 50, 90, 90, 30, 30, 20]
    pts = turning_points(t, x)
    assert any(p[0] == 80 and p[2] - p[1] == 2.0 for p in pts)            # the plateau dwell is not lost
    cyc = rainflow(pts)
    full = [c for c in cyc if c["count"] == 1.0]
    assert any(c["range"] == 20 and c["min"] == 50 for c in full)         # the nested 50-70 cycle


def test_repeating_mission_leaves_no_residue():
    t = np.arange(9.0)
    x = [40, 100, 60, 90, 40, 70, 50, 100, 40]
    r = cycle_analysis(t, x, repeating_mission=True)
    assert all(c["count"] == 1.0 for c in r["cycles"])


def test_invalid_histories_are_rejected():
    with pytest.raises(InputValidationError):
        turning_points([0, 1, 1, 2], [1, 2, 3, 4])                         # non-increasing time
    with pytest.raises(InputValidationError):
        turning_points([0, 1, 2], [1, float("nan"), 2])


def _model(**kw):
    base = dict(mechanism="test power cycling", A=1e12, a=-5.0, b_K=0.0, T_ref="min", dT_valid_K=(10.0, 150.0),
                Tref_valid_C=(-40.0, 150.0), basis="test law (not a product model)", scatter_factor=2.0)
    base.update(kw)
    return CyclingModel(**base)


def test_constant_amplitude_damage_is_n_over_nf():
    t = np.arange(201.0)
    x = [40.0 if k % 2 == 0 else 100.0 for k in range(201)]              # 100 full 60 K cycles
    r = cycle_analysis(t, x, _model(), D_allow=1.0)
    nf = 1e12 * 60.0 ** -5.0
    assert r["damage"]["D"] == pytest.approx(100 / nf, rel=1e-9)
    twice = cycle_analysis(t, x, _model(), repeats=2.0)
    assert twice["damage"]["D"] == pytest.approx(2 * r["damage"]["D"], rel=1e-12)   # linear accumulation


def test_conditional_verdicts_use_the_declared_scatter():
    t = np.arange(201.0)
    x = [40.0 if k % 2 == 0 else 100.0 for k in range(201)]
    D = 100 / (1e12 * 60.0 ** -5.0)
    assert cycle_analysis(t, x, _model(), D_allow=3 * D)["damage"]["claim"]["status"] == "FEASIBLE"
    assert cycle_analysis(t, x, _model(), D_allow=0.3 * D)["damage"]["claim"]["status"] == "INFEASIBLE"
    assert cycle_analysis(t, x, _model(), D_allow=1.0 * D)["damage"]["claim"]["status"] == "UNKNOWN"


def test_cycles_outside_the_model_are_uncovered_not_zero():
    t = np.arange(21.0)
    x = [40.0 if k % 2 == 0 else 45.0 for k in range(21)]                # 5 K cycles below the 10 K validity
    r = cycle_analysis(t, x, _model(), D_allow=1.0)
    assert r["damage"]["claim"]["status"] == "UNKNOWN" and r["damage"]["uncovered_cycles"]
    assert cycle_analysis(t, x, None)["damage"]["claim"]["reasons"] == ["MISSING_INPUT"]     # no model: histogram
    with pytest.raises(InputValidationError):
        CyclingModel("m", 1e12, -5.0, 0.0, basis="")                     # no default law without a basis
    with_ton = _model(c=-0.3, ton_valid_s=(0.5, 60.0))
    r2 = cycle_analysis(np.arange(201.0), [40.0 if k % 2 == 0 else 100.0 for k in range(201)], with_ton)
    assert r2["damage"]["claim"]["status"] == "UNKNOWN"                  # t_on needed but no approved rule


def test_foster_trace_matches_the_step_response():
    t = np.linspace(0, 10, 1001)
    P = np.where(t < 5.0, 100.0, 0.0)
    T = foster_trace(t, P, (0.1, 0.2), (0.5, 2.0), 60.0)
    k = int(np.searchsorted(t, 5.0))
    step = 60.0 + 100.0 * (0.1 * (1 - math.exp(-t[k] / 0.5)) + 0.2 * (1 - math.exp(-t[k] / 2.0)))
    assert T[k] == pytest.approx(step, rel=1e-9)


def test_mission_api_runs_through_the_module_model():
    from traction_workbench import api
    r = api.lifetime({})
    assert r["reversals"] > 3 and r["max_range_K"] > 0
    assert all(s["P_hot_device_W"] > 0 for s in r["segments"])
    assert r["damage"]["claim"]["status"] == "UNKNOWN"                   # no supplier model in the example
    tr = api.lifetime({"trace": {"t_s": [0, 1, 2, 3, 4], "T_C": [60, 100, 60, 100, 60]}})
    assert tr["source"] == "imported Tj trace" and tr["max_range_K"] == 40.0
