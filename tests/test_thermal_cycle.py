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
from traction_workbench.extensions.thermal_cycle import (Feedback, InitialState, LoadPhase, _crossing,
                                                         _periodic_ends, _phase_max, _rest_needed, repeated_load)
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


def test_a_peak_inside_a_phase_is_found_exactly():
    """A fast term heating while a slow one cools peaks inside the phase: its ends alone understate it."""
    x, r, tau = np.array([0.0, 5.0]), np.array([1.0, 1.0]), np.array([1.0, 100.0])
    P, d = 3.0, 200.0
    t = np.linspace(0.0, d, 400001)
    brute = float(np.max(np.sum(r * P + (x - r * P) * np.exp(-t[:, None] / tau), axis=1)))
    ends = max(float(np.sum(x)), float(np.sum(r * P + (x - r * P) * np.exp(-d / tau))))
    assert _phase_max(x, r, tau, P, d) == pytest.approx(brute, abs=1e-6) and brute > ends + 1.0
    heating = np.array([0.0, 1.0])                     # every term moving the same way: the end is the peak
    assert _phase_max(heating, r, tau, P, d) == pytest.approx(float(np.sum(r * P + (heating - r * P) * np.exp(-d / tau))))


def test_the_first_limit_is_never_missed_between_samples():
    """The peak (7.88 at 5.06 s) lies between the 16 samples of a 200 s step: the exact maximum brackets it."""
    x, r, tau = np.array([0.0, 5.0]), np.array([1.0, 1.0]), np.array([1.0, 100.0])
    tc = _crossing(x, r, tau, 3.0, 0.0, 7.8, 200.0)
    assert tc is not None and 0 < tc < 5.1
    assert float(np.sum(r * 3.0 + (x - r * 3.0) * np.exp(-tc / tau))) == pytest.approx(7.8, abs=1e-9)
    assert _crossing(x, r, tau, 3.0, 0.0, 7.9, 200.0) is None


def test_a_hot_soak_start_peaks_inside_the_pulse_and_the_allowance_holds(setup):
    """After a hot soak the inner layers are hotter than the pulse sustains: the junction peaks inside the pulse and
    cools afterwards.  The first-pulse allowance comes from that inside peak (the pulse end alone overstated it by
    about 70 N*m) and does not depend on the declared pulse torque (the start is referred to the trial pulse)."""
    d, sc, _model = setup
    spec = api.EXAMPLE_THERMAL
    inv = dict(spec["nodes"][0], network="cauer", R_K_per_W=[0.01, 0.03, 0.08, 0.08], C_J_per_K=[0.2, 1.0, 5.0, 31.0])
    m2 = api.thermal_model_from_dict({**spec, "nodes": [inv, spec["nodes"][1]]}, 65.0)
    j = "inverter junction (1 of 6 switches)"
    init = InitialState("node_temperatures", node_temperatures_C=((j, (100.0, 128.0, 132.0, 118.0)),
                                                                  ("stator winding (hot spot)", (120.0, 115.0, 100.0))))
    run = lambda T, allowed=True: repeated_load(d, sc, m2, [LoadPhase(T, 3000.0, 20.0), LoadPhase(50.0, 3000.0, 10.0)],  # noqa: E731
                                                cycles=1, initial=init, feedback=Feedback(False), allowed=allowed)
    a = run(200.0)["allowed"]["first_pulse_torque_Nm"]
    assert a == pytest.approx(run(450.0)["allowed"]["first_pulse_torque_Nm"], rel=1e-9)
    inside, outside = run(0.999 * a, False), run(1.01 * a, False)
    assert inside["first_limit"] is None and outside["first_limit"]["node"] == j
    tr = outside["trace"]
    k20 = min(range(len(tr["t_s"])), key=lambda k: abs(tr["t_s"][k] - 20.0))
    assert tr["nodes"][j][k20] < outside["per_cycle"][0]["peak_C"][j] - 1.0         # the peak is inside the pulse


def test_a_rest_that_heats_the_slower_nodes_allows_repeating_only_inside_a_window():
    """Immediately after the pulse the fast term is too hot; a medium rest cools it; a long rest at this (heavy) rest
    load heats the slow term until the repeat fails again.  The first allowed rest is not the whole answer."""
    from types import SimpleNamespace as NS
    model = NS(nodes=[NS(network=NS(R_K_per_W=[1.0, 1.0], tau_s=[1.0, 1000.0]))])
    phases = [LoadPhase(400.0, 3000.0, 2.0), LoadPhase(300.0, 3000.0, 5.0)]
    r, tau, P0, P1, lim = np.array([1.0, 1.0]), np.array([1.0, 1000.0]), 10.0, 6.0, 9.7
    out = _rest_needed(model, phases, [([P0], [0.0]), ([P1], [0.0])], [np.zeros(2)], [lim], lambda *a: 1.0)

    def peak(t_rest):                                   # dense brute force over the rest and the repeated pulse
        x = r * P0 + (0.0 - r * P0) * np.exp(-2.0 / tau)
        ts = np.linspace(0.0, t_rest, 20001)[:, None]
        rest = r * P1 + (x - r * P1) * np.exp(-ts / tau)
        tp = np.linspace(0.0, 2.0, 20001)[:, None]
        pulse = r * P0 + (rest[-1] - r * P0) * np.exp(-tp / tau)
        return max(float(np.max(rest.sum(axis=1))), float(np.max(pulse.sum(axis=1))))

    t0, t1 = out["rest_before_repeat_s"], out["rest_before_repeat_window_end_s"]
    assert 0 < t0 < 5.0 < 20.0 < t1 < 100.0 and "between" in out["rest_before_repeat_note"]
    assert peak(0.99 * t0) > lim >= peak(1.01 * t0) and peak(0.99 * t1) <= lim < peak(1.01 * t1)


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
