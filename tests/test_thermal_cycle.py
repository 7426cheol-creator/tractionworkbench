"""Repeated load and hot starts (engineering review 6198099, priority 1): pulse - rest - repeat from a stated initial
state, loss-temperature feedback, the periodic cycle as the fixed point of the exact cycle map, and the allowed
pulse duration / torque and the rest needed (closed form)."""

import math
from dataclasses import replace

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.thermal import _losses
from traction_workbench.extensions.thermal_cycle import (Feedback, InitialState, LoadPhase, _periodic_ends,
                                                         repeated_load)
from traction_workbench.models.components import TemperatureDependence
from traction_workbench.scenario import Scenario
from traction_workbench.solvers.policy import PolicyEvaluator

LAW = TemperatureDependence(0.00393, (-40.0, 250.0), "copper coefficient (test)")


@pytest.fixture(scope="module")
def setup():
    d, lim = api._drive({}), api._limits({})
    sc = Scenario("cyc", 3000.0, 600.0, lim, coolant_temp_C=65.0)
    return d, sc, api.thermal_model_from_dict(None, 65.0)


def _closed_form_peak(d, sc, model, phases):
    out = {}
    for nd in model.nodes:
        Ps, refs = [], []
        for p in phases:
            los = _losses(PolicyEvaluator(d, sc.with_(speed_rpm=p.speed_rpm)).solve(p.torque_Nm).point)
            fluid = model.coolant.fluid_temperatures(65.0, los)
            Ps.append(nd.power(los))
            refs.append(model.reference_C(nd, 65.0, los, fluid))
        ends = _periodic_ends(np.asarray(nd.network.R_K_per_W), np.asarray(nd.network.tau_s), Ps,
                              [p.duration_s for p in phases])
        out[nd.node_id] = max(r + e for r, e in zip(refs, ends))
    return out


def test_the_periodic_cycle_is_the_exact_fixed_point_not_a_run_until_it_settles(setup):
    """A 234 s winding mode with a 10 s cycle settles only ~4 % per cycle: stopping a run when a cycle changes little
    is ~1 K short.  The fixed point of the affine cycle map equals the closed form."""
    d, sc, model = setup
    ph = [LoadPhase(460.0, 3000.0, 4.0), LoadPhase(50.0, 3000.0, 6.0)]
    r = repeated_load(d, sc, model, ph, cycles=10, feedback=Feedback(False), allowed=False)
    ref = _closed_form_peak(d, sc, model, ph)
    assert r["periodic"]["reached"]
    for node, T in ref.items():
        assert r["periodic"]["peak_C"][node] == pytest.approx(T, abs=1e-6)
    wind = "stator winding (hot spot)"
    assert r["per_cycle"][-1]["peak_C"][wind] < ref[wind] - 1.0          # 10 physical cycles are far from it


def test_first_pulse_limit_is_found_inside_the_step(setup):
    d, sc, model = setup
    r = repeated_load(d, sc, model, [LoadPhase(460.0, 3000.0, 4.0), LoadPhase(50.0, 3000.0, 6.0)], cycles=2,
                      feedback=Feedback(False), allowed=False)
    fl = r["first_limit"]
    assert fl["cycle"] == 1 and fl["phase"] == 0 and 0 < fl["t_s"] < 4.0
    # the junction's closed-form step response reaches its limit at the same time
    nd = model.nodes[0]
    los = _losses(PolicyEvaluator(d, sc).solve(460.0).point)
    ref = model.reference_C(nd, 65.0, los, model.coolant.fluid_temperatures(65.0, los))
    assert ref + nd.power(los) * nd.network.zth(fl["t_s"]) == pytest.approx(nd.limit_C, abs=1e-6)


def test_a_hot_start_is_hotter_and_limits_sooner(setup):
    d, sc, model = setup
    ph = [LoadPhase(455.0, 3000.0, 8.0), LoadPhase(50.0, 3000.0, 20.0)]
    cold = repeated_load(d, sc, model, ph, cycles=3, feedback=Feedback(False), allowed=False)
    hot = repeated_load(d, sc, model, ph, cycles=3, initial=InitialState("steady_state_at", 250.0, 3000.0),
                        feedback=Feedback(False), allowed=False)
    for node in cold["initial"]["temperatures_C"]:
        assert hot["initial"]["temperatures_C"][node] > cold["initial"]["temperatures_C"][node]
    assert hot["first_limit"]["t_s"] < cold["first_limit"]["t_s"]
    # the periodic cycle does not remember the start
    for node, T in cold["periodic"]["peak_C"].items():
        assert hot["periodic"]["peak_C"][node] == pytest.approx(T, abs=1e-6)


def test_node_temperatures_need_a_cauer_ladder(setup):
    d, sc, model = setup
    ph = [LoadPhase(300.0, 3000.0, 5.0), LoadPhase(50.0, 3000.0, 5.0)]
    with pytest.raises(InputValidationError, match="Foster network"):
        repeated_load(d, sc, model, ph, cycles=1, initial=InitialState("node_temperatures", node_temperatures_C=(
            ("inverter junction (1 of 6 switches)", (100.0, 90.0, 80.0, 70.0)),
            ("stator winding (hot spot)", (100.0, 90.0, 80.0)))), feedback=Feedback(False))
    # a model whose nodes are both Cauer: physical node temperatures of the preload's steady state give exactly the
    # steady-state start
    spec = api.EXAMPLE_THERMAL
    inv = dict(spec["nodes"][0], network="cauer", R_K_per_W=[0.01, 0.03, 0.08, 0.08], C_J_per_K=[0.2, 1.0, 5.0, 31.0])
    cauer_spec = {**spec, "nodes": [inv, spec["nodes"][1]]}
    m2 = api.thermal_model_from_dict(cauer_spec, 65.0)
    hot = repeated_load(d, sc, m2, ph, cycles=1, initial=InitialState("steady_state_at", 200.0, 3000.0),
                        feedback=Feedback(False), allowed=False)
    pre = hot["initial"]["preload"]["losses_W"]
    first = _losses(PolicyEvaluator(d, sc).solve(ph[0].torque_Nm).point)
    f_pre, f_first = m2.coolant.fluid_temperatures(65.0, pre), m2.coolant.fluid_temperatures(65.0, first)
    temps = {}
    for nd in m2.nodes:
        # the ladder's steady node rises under the preload, above the fluid reference of the first phase (the
        # coolant follows the losses instantly, so every start is referred to it from t = 0+)
        R = np.asarray(nd.cauer.R_K_per_W)
        temps[nd.node_id] = tuple(m2.reference_C(nd, 65.0, first, f_first) + nd.power(pre) * np.cumsum(R[::-1])[::-1])
    same = repeated_load(d, sc, m2, ph, cycles=1, initial=InitialState(
        "node_temperatures", node_temperatures_C=tuple(temps.items())), feedback=Feedback(False), allowed=False)
    for nd in m2.nodes:
        shift = m2.reference_C(nd, 65.0, pre, f_pre) - m2.reference_C(nd, 65.0, first, f_first)
        assert same["initial"]["temperatures_C"][nd.node_id] == pytest.approx(temps[nd.node_id][0], abs=1e-9)
        assert hot["initial"]["temperatures_C"][nd.node_id] - same["initial"]["temperatures_C"][nd.node_id] == \
            pytest.approx(shift, abs=1e-9)
    for node, T in hot["per_cycle"][0]["peak_C"].items():           # the same Foster states from t = 0+
        assert same["per_cycle"][0]["peak_C"][node] == pytest.approx(T, abs=1e-9)


def test_winding_resistance_feedback_heats_the_winding_more(setup):
    d, sc, model = setup
    ph = [LoadPhase(400.0, 3000.0, 10.0), LoadPhase(50.0, 3000.0, 20.0)]
    off = repeated_load(d, sc, model, ph, cycles=3, feedback=Feedback(False), allowed=False)
    none = repeated_load(d, sc, model, ph, cycles=3, feedback=Feedback(True), allowed=False)
    on = repeated_load(d, sc, model, ph, cycles=3, feedback=Feedback(True, LAW, 20.0), allowed=False)
    w = "stator winding (hot spot)"
    assert any("no R_s temperature law" in n for n in none["feedback"]["notes"]) and not none["feedback"]["rs"]
    assert none["periodic"]["peak_C"][w] == pytest.approx(off["periodic"]["peak_C"][w])
    assert on["feedback"]["rs"] and on["periodic"]["peak_C"][w] > off["periodic"]["peak_C"][w] + 5.0
    with pytest.raises(InputValidationError, match="refers to"):
        repeated_load(d, sc, model, ph, cycles=1, feedback=Feedback(True, LAW, None))


def test_allowed_pulse_duration_and_torque_sit_on_the_limit(setup):
    """Without feedback the closed-form allowed values are exact: just inside stays below, just outside exceeds."""
    d, sc, model = setup
    ph = [LoadPhase(450.0, 3000.0, 8.0), LoadPhase(50.0, 3000.0, 20.0)]
    r = repeated_load(d, sc, model, ph, cycles=1, feedback=Feedback(False))
    al = r["allowed"]
    dur, tq = al["pulse_duration_s"], al["pulse_torque_Nm"]
    assert 0 < dur < 8.0 and 300.0 < tq < 450.0 and al["pulse_torque_note"] == "limited by a node temperature"
    margin = lambda phs: min(n.limit_C - T for n, T in zip(model.nodes, _closed_form_peak(d, sc, model, phs).values()))  # noqa: E731
    assert margin([replace(ph[0], duration_s=dur * 0.999), ph[1]]) > 0 > margin([replace(ph[0], duration_s=dur * 1.001),
                                                                                  ph[1]])
    assert margin([replace(ph[0], torque_Nm=tq - 0.05), ph[1]]) > 0 > margin([replace(ph[0], torque_Nm=tq + 0.05),
                                                                             ph[1]])


def test_the_rest_needed_before_repeating(setup):
    d, sc, model = setup
    ph = [LoadPhase(440.0, 3000.0, 3.0), LoadPhase(50.0, 3000.0, 1.0)]
    r = repeated_load(d, sc, model, ph, cycles=1, feedback=Feedback(False))
    rest = r["allowed"]["rest_before_repeat_s"]
    assert rest is not None and 0 < rest < math.inf
    peak = lambda t_rest: repeated_load(d, sc, model, [ph[0], replace(ph[1], duration_s=t_rest), ph[0]], cycles=1,  # noqa: E731
                                        feedback=Feedback(False), allowed=False)["first_limit"]
    assert peak(rest * 1.02) is None and peak(rest * 0.9) is not None
    assert r["allowed"]["periodic_min_rest_s"] >= rest


def test_an_unqualified_model_is_a_screening_estimate_a_qualified_one_decides(setup):
    d, sc, model = setup
    ph = [LoadPhase(300.0, 3000.0, 5.0), LoadPhase(50.0, 3000.0, 10.0)]
    r = repeated_load(d, sc, model, ph, cycles=2, feedback=Feedback(False), allowed=False)
    assert r["claim"]["status"] == "UNKNOWN" and r["claim"]["reasons"] == ["UNVALIDATED_DURATION"]
    wind = dict(api.EXAMPLE_THERMAL["nodes"][1], loss_share={"copper": 1.0, "rotational": 1.0})   # every heat source
    q = api.thermal_model_from_dict({**api.EXAMPLE_THERMAL, "nodes": [api.EXAMPLE_THERMAL["nodes"][0], wind],
                                     "validated": True, "validation_evidence": "TR-1 rev A",
                                     "validity": {"coolant_temp_C": [60, 70], "coolant_flow_L_per_min": [8, 12]}}, 65.0)
    ok = repeated_load(d, sc, q, ph, cycles=2, feedback=Feedback(False), allowed=False)
    assert ok["claim"]["status"] == "FEASIBLE"
    bad = repeated_load(d, sc, q, [LoadPhase(460.0, 3000.0, 6.0), ph[1]], cycles=2, feedback=Feedback(False),
                        allowed=False)
    assert bad["claim"]["status"] == "INFEASIBLE"


def test_module_junction_feedback_never_extrapolates_the_module_table():
    b = {"loss_model": "module"}
    d = api._eff_drive({**api.EXAMPLE_EFFICIENCY, **b})
    sc = Scenario("cyc", 3000.0, 600.0, api._limits({}), coolant_temp_C=65.0)
    dev = dict(api.EXAMPLE_THERMAL["nodes"][0], loss_share={"inverter_hottest_device": 1.0})
    model = api.thermal_model_from_dict({**api.EXAMPLE_THERMAL, "nodes": [dev, api.EXAMPLE_THERMAL["nodes"][1]]}, 65.0)
    r = repeated_load(d, sc, model, [LoadPhase(150.0, 3000.0, 5.0), LoadPhase(20.0, 3000.0, 5.0)], cycles=2,
                      feedback=Feedback(True), allowed=False)
    assert r["feedback"]["module"] and r["feedback"]["junction_node"] == dev["id"]
    hot = repeated_load(d, sc, model, [LoadPhase(480.0, 3000.0, 30.0), LoadPhase(20.0, 3000.0, 5.0)], cycles=2,
                        feedback=Feedback(True), allowed=False)
    if hot["stopped"] is not None:                          # beyond the table: stopped with the reason, not guessed
        assert hot["claim"]["status"] in ("UNKNOWN", "INFEASIBLE") and hot["stopped"]["reason"]


def test_input_contract():
    with pytest.raises(InputValidationError):
        LoadPhase(100.0, 3000.0, 0.0)
    with pytest.raises(InputValidationError):
        InitialState("warm")
    with pytest.raises(InputValidationError, match="preload"):
        InitialState("steady_state_at")
    r = api.thermal_cycle({"cycles": 3})
    assert r["cycles_requested"] == 3 and r["trace"]["t_s"][0] == 0.0 and r["periodic"]["reached"]
