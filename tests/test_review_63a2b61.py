"""Review of 63a2b61 (logic review document): decision rules that must hold whatever the input order, the time scale
or the rounding at a boundary.

* 2.1 policy Pareto set: equal uncertainty intervals are not equal values;
* 2.2 repeated load: the first limit reached is the earliest over all nodes, not the first node in the list;
* 2.3 repeated load: the peak and the limit crossing inside a phase are exact for every stationary point, however fast
  a thermal mode is against the phase (exponential-sum root isolation, no sampling grid);
* 2.4 Thevenin source: one boundary rule for the maximum-transfer proof and the terminal-voltage root;
* 3.2 periodic cycle judged on every Foster term (a node temperature is a sum whose terms can cancel).
"""

import math
from dataclasses import replace

import numpy as np
import pytest
from scipy.linalg import expm

from traction_workbench import api
from traction_workbench.analysis.source import MAX_TRANSFER_REL_TOL, TheveninSource, resolve_terminal_voltage
from traction_workbench.extensions.pwm_policy import _pareto
from traction_workbench.extensions.thermal import CauerNetwork
from traction_workbench.extensions.thermal_cycle import (Feedback, LoadPhase, _crossing, _expsum_zeros, _phase_max,
                                                         _state_residual, repeated_load)
from traction_workbench.scenario import Scenario


# -- 2.1 ----------------------------------------------------------------------------------------------------------

def _row(name, e_inv, lo, hi):
    return {"policy": {"name": name}, "E_inv_J": e_inv, "energy": {"lower_J": lo, "upper_J": hi}, "Tj_max_C": 100.0,
            "i_peak_bound_max_A": 400.0, "I_cap_rms_max_A": 100.0, "phase_margin_min_deg": 60.0}


def _names(rows):
    return sorted(r["policy"]["name"] for r in _pareto(rows))


def test_equal_energy_intervals_never_remove_a_policy():
    # A 90 J inverter + open part 25 J = 115 J, B 95 J + 10 J = 105 J: both fit [100, 120], B may use less energy
    assert _names([_row("A", 90.0, 100.0, 120.0), _row("B", 95.0, 100.0, 120.0)]) == ["A", "B"]
    assert _names([_row("A", 90.0, 100.0, None), _row("B", 95.0, 100.0, None)]) == ["A", "B"]    # open, identical
    # point intervals are known values: equal totals, the inverter energy decides
    assert _names([_row("A", 90.0, 110.0, 110.0), _row("B", 95.0, 110.0, 110.0)]) == ["A"]
    # separated intervals still decide: B is certainly above A in total energy and in inverter energy
    assert _names([_row("A", 90.0, 100.0, 120.0), _row("B", 95.0, 121.0, 130.0)]) == ["A"]


# -- 2.2 ----------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def setup():
    d, lim = api._drive({}), api._limits({})
    return d, Scenario("cyc", 3000.0, 600.0, lim, coolant_temp_C=65.0), api.thermal_model_from_dict(None, 65.0)


def test_the_first_limit_does_not_depend_on_the_node_order(setup):
    """Both nodes reach their limits inside one (long) step: the junction at 3.44 s, the winding (limit lowered to
    90 degC for the test) later.  Listing the winding first used to report the winding."""
    d, sc, model = setup
    j, w = model.nodes
    w90 = replace(w, limit_C=90.0)
    out = []
    for order in ((j, w90), (w90, j)):
        r = repeated_load(d, sc, replace(model, nodes=order), [LoadPhase(460.0, 3000.0, 20.0)], cycles=1,
                          feedback=Feedback(False), steps_per_phase=1, allowed=False)
        out.append(r["first_limit"])
    assert out[0]["node"] == out[1]["node"] == j.node_id
    assert out[0]["t_s"] == pytest.approx(out[1]["t_s"], rel=1e-12)
    assert out[0]["nodes_at_limit"] == [j.node_id]
    # it is the earliest crossing: the winding alone reaches its (lowered) limit later
    alone = repeated_load(d, sc, replace(model, nodes=(w90,)), [LoadPhase(460.0, 3000.0, 20.0)], cycles=1,
                          feedback=Feedback(False), steps_per_phase=1, allowed=False)["first_limit"]
    assert alone["t_s"] > out[0]["t_s"]


# -- 2.3 ----------------------------------------------------------------------------------------------------------

def test_exponential_sum_zeros_are_isolated_exactly():
    # e^-t - 2 e^-2t = 0 at t = ln 2;  3 e^-t - 3 e^-2t + ... no sign change -> no zero
    assert _expsum_zeros([1.0, -2.0], [1.0, 2.0], 10.0) == pytest.approx([math.log(2.0)], rel=1e-14)
    assert _expsum_zeros([1.0, 2.0, 0.5], [1.0, 2.0, 3.0], 10.0) == []
    # (e^-t - e^-2t)(... ) type: e^-t - 3 e^-2t + 2 e^-3t = e^-t (1 - e^-t)(1 - 2 e^-t): zeros at t = ln 2 only in (0, d)
    z = _expsum_zeros([1.0, -3.0, 2.0], [1.0, 2.0, 3.0], 10.0)
    assert z == pytest.approx([math.log(2.0)], rel=1e-12)
    # two zeros inside one decade of time, with rates six decades apart
    c, lam = [1.0, -1.0, 1e-3], [1e-3, 1e3, 1e6]
    z = _expsum_zeros(c, lam, 1e4)
    h = lambda t: sum(ci * math.exp(-li * t) for ci, li in zip(c, lam))      # noqa: E731
    assert all(abs(h(t)) < 1e-12 for t in z)


def _cauer_case():
    """A physical 3-layer ladder (every R, C > 0), physical layer rises >= 0, constant P: the junction rises, falls
    and rises again inside the first 7 us of a 156 s phase (found by a randomised search against the old grid)."""
    cn = CauerNetwork((0.08524, 0.02015, 0.01895), (3.240e-06, 2.218e-05, 5.445))
    rise0 = np.array([15.77, 5.54, 12.15])
    return cn, rise0, 117.8, 156.0


def test_a_peak_microseconds_into_a_long_phase_is_found_exactly():
    cn, rise0, P, d = _cauer_case()
    f = cn.to_foster()
    r, tau = np.asarray(f.R_K_per_W), np.asarray(f.tau_s)
    x0 = cn.foster_state(rise0)
    mx, t_mx = _phase_max(x0, r, tau, P, d, with_time=True)
    # the Cauer state equation itself (matrix exponential), not the Foster form, at and around the reported peak
    n = 3
    G = np.zeros((n, n))
    for i, ri in enumerate(cn.R_K_per_W):
        G[i, i] += 1 / ri
        if i + 1 < n:
            G[i + 1, i + 1] += 1 / ri
            G[i, i + 1] -= 1 / ri
            G[i + 1, i] -= 1 / ri
    C = np.diag(cn.C_J_per_K)
    A, b = -np.linalg.solve(C, G), np.linalg.solve(C, np.array([P, 0.0, 0.0]))
    xss = -np.linalg.solve(A, b)
    T1 = lambda t: float((xss + expm(A * t) @ (rise0 - xss))[0])            # noqa: E731
    assert mx == pytest.approx(T1(t_mx), abs=1e-9)
    assert mx >= max(T1(t) for t in np.linspace(0.0, 2e-5, 2001)) - 1e-9
    assert mx > rise0[0] + 8.0 and 1e-6 < t_mx < 1e-5                    # 24.6 K at 7.1 us, start 15.8 K
    # the first crossing of a limit between the start and that peak lies before the peak, and is exact
    lim = 0.5 * (rise0[0] + mx)
    tc = _crossing(x0, r, tau, P, 0.0, lim, d)
    assert tc is not None and 0 < tc < t_mx and T1(tc) == pytest.approx(lim, abs=1e-9)
    assert _crossing(x0, r, tau, P, 0.0, mx + 1e-6, d) is None


def test_the_phase_maximum_is_never_below_a_dense_sample_of_the_trajectory():
    rng = np.random.default_rng(7)
    for _ in range(300):
        n = int(rng.integers(2, 5))
        r, tau = 10 ** rng.uniform(-3, 0, n), 10 ** rng.uniform(-6, 2, n)
        x, P, d = rng.uniform(-20, 60, n), float(rng.uniform(0, 400)), float(10 ** rng.uniform(-2, 2))
        t = np.concatenate([[0.0], np.geomspace(1e-9, d, 4000), np.linspace(0.0, d, 2000)])
        dense = float(np.max(np.sum(r * P + (x - r * P) * np.exp(-t[:, None] / tau), axis=1)))
        assert _phase_max(x, r, tau, P, d) >= dense - 1e-9 * max(1.0, abs(dense))


# -- 2.4 ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [+0.5e-12, -0.5e-12])
def test_the_maximum_transfer_boundary_is_one_rule(rel):
    d, lim = api._drive({}), api._limits({})
    src, V, n = TheveninSource(0.1, "test"), 600.0, 6000.0
    w = n * 2.0 * math.pi / 60.0
    p_max = V * V / (4.0 * 0.1)
    r = resolve_terminal_voltage(d, Scenario("s", n, V, lim), src, p_max * (1.0 + rel) / w)
    assert r["status"] == "NOT_RESOLVED" and r["boundary"] and "neither proven impossible nor resolved" in r["reason"]
    beyond = resolve_terminal_voltage(d, Scenario("s", n, V, lim), src, p_max * (1.0 + 4 * MAX_TRANSFER_REL_TOL) / w)
    assert beyond["status"] == "NO_SOLUTION"


def test_a_boundary_source_case_is_an_open_answer_at_the_boundary():
    base = {"id": "R-OCV", "text": "boundary", "speed_rpm": 6000.0, "Vdc_V": 600.0, "Vdc_port": "battery_ocv"}
    w = 6000.0 * 2.0 * math.pi / 60.0
    T = 600.0 ** 2 / (4.0 * 30.0) / w                                    # R_eq 30 Ohm: 3 kW, at 6000 rpm 4.77 N*m
    r = api.evaluate({"requirement": {**base, "torque_Nm": T}, "source_model": {"R_eq_ohm": 30.0, "basis": "test"}})
    assert r["source_coupling"]["status"] == "NOT_RESOLVED"
    assert r["verdict"]["verdict"] == "UNKNOWN" and "BOUNDARY_WITHIN_TOLERANCE" in r["verdict"]["reasons"]


# -- 3.2 ----------------------------------------------------------------------------------------------------------

def test_the_periodic_cycle_is_judged_on_every_foster_term(setup):
    # terms can cancel in the node temperature: +1 K on a fast term and -1 K on a slow one is not a settled cycle
    assert _state_residual([np.array([1.0, -1.0])], [np.array([0.0, 0.0])]) == 1.0
    d, sc, model = setup
    r = repeated_load(d, sc, model, [LoadPhase(460.0, 3000.0, 4.0), LoadPhase(50.0, 3000.0, 6.0)], cycles=3,
                      feedback=Feedback(False), allowed=False)
    assert r["periodic"]["reached"] and r["periodic"]["state_residual_K"] < 0.02


# -- 3.2 magnet temperature, loss bound over the temperature box, resolution --------------------------------------

from types import SimpleNamespace as NS                                     # noqa: E402

from traction_workbench.errors import InputValidationError                  # noqa: E402
from traction_workbench.extensions.thermal_cycle import (_allowed, _resolution_compare,  # noqa: E402
                                                         InitialState)
from traction_workbench.models.components import TemperatureDependence     # noqa: E402

PSI_LAW = TemperatureDependence(-0.0012, (-40.0, 200.0), "NdFeB remanence coefficient (test)")


def _magnet_spec(extra_node=True):
    spec = dict(api.EXAMPLE_THERMAL)
    mag = {"id": "rotor magnet", "network": "foster", "R_K_per_W": [0.05], "tau_s": [120.0], "limit_C": 160.0,
           "loss_share": {"rotational": 1.0}, "station": "motor", "temperature_of": "magnet"}
    return {**spec, "nodes": list(spec["nodes"]) + ([mag] if extra_node else [])}


def test_a_declared_magnet_node_feeds_the_flux_of_the_operating_point(setup):
    d, sc, _m = setup
    d_psi = replace(d, motor=replace(d.motor, psi_temperature=PSI_LAW, reference_magnet_temp_C=20.0))
    ph = [LoadPhase(400.0, 3000.0, 30.0), LoadPhase(50.0, 3000.0, 30.0)]
    with_node = repeated_load(d_psi, sc, api.thermal_model_from_dict(_magnet_spec(), 65.0), ph, cycles=2,
                              allowed=False, resolution_check=False)
    fb = with_node["feedback"]
    assert fb["magnet_node"] == "rotor magnet" and fb["magnet"]
    # the magnet heats from the coolant temperature: the flux weakens, the same torque takes more current and copper
    # loss than with the magnet held at the coolant temperature
    held = repeated_load(d_psi, sc.with_(magnet_temp_C=65.0), api.thermal_model_from_dict(_magnet_spec(False), 65.0),
                         ph, cycles=2, allowed=False, resolution_check=False)
    w = "stator winding (hot spot)"
    assert with_node["per_cycle"][-1]["peak_C"][w] > held["per_cycle"][-1]["peak_C"][w]
    assert any("magnet temperature" in n for n in held["feedback"]["notes"])
    # no magnet-temperature dependence in the motor: a declared magnet node is reported, not silently used
    plain = repeated_load(d, sc, api.thermal_model_from_dict(_magnet_spec(), 65.0), ph, cycles=1, allowed=False,
                          resolution_check=False)
    assert not plain["feedback"]["magnet"] and any("not fed back" in n for n in plain["feedback"]["notes"])


def test_one_node_per_represented_temperature():
    spec = _magnet_spec()
    spec["nodes"][0] = {**spec["nodes"][0], "temperature_of": "magnet"}
    with pytest.raises(InputValidationError):
        api.thermal_model_from_dict(spec, 65.0)


class _FakeLosses:
    """A loss that peaks INSIDE the temperature box (not monotone): the corners are not a bound."""

    def __init__(self, bump):
        self.rs, self.module, self.mag, self.bump = True, True, False, bump

    def at(self, ph, T_w, T_j, T_m=None):
        s = 0.0 if T_w is None else math.sin(math.pi * (T_w - 65.0) / (180.0 - 65.0))
        cu = 500.0 + self.bump * s
        return {"ok": True, "losses": {"copper": cu, "inverter": 600.0, "rotational": 0.0}, "reason": "", "status": ""}


def test_the_allowed_values_say_when_the_corner_losses_are_not_a_bound(setup):
    _d, _sc, model = setup
    refs = lambda los: [65.0 for _ in model.nodes]                           # noqa: E731
    x0 = lambda refs: [np.zeros(len(nd.network.R_K_per_W)) for nd in model.nodes]   # noqa: E731
    ph = [LoadPhase(300.0, 3000.0, 10.0, "pulse"), LoadPhase(50.0, 3000.0, 20.0, "rest")]
    mono = _allowed(_FakeLosses(0.0), model, ph, InitialState(), None, (1, 0, None), refs, x0, 65.0)
    assert mono["loss_bound"]["is_bound"] and mono["loss_bound"]["corners"] == 4 and "ESTIMATE" not in mono["basis"]
    bump = _allowed(_FakeLosses(300.0), model, ph, InitialState(), None, (1, 0, None), refs, x0, 65.0)
    lb = bump["loss_bound"]
    assert not lb["is_bound"] and lb["check"].startswith("FAILED") and "ESTIMATE" in bump["basis"]
    assert lb["violations"][0]["loss"] == "copper" and lb["violations"][0]["W"] > lb["violations"][0]["corner_max_W"]


def test_a_verdict_that_changes_with_the_resolution_is_not_a_verdict():
    fl = {"t_s": 3.0, "node": "j"}
    per = {"peak_C": {"j": 149.0}, "margin_K": {"j": 1.0}}
    ok = _resolution_compare(24, 0.5, fl, per, [{"peak_C": {"j": 149.0}}],
                             {"first_limit": {"t_s": 3.01, "node": "j"}, "periodic": {"peak_C": {"j": 149.2},
                              "margin_K": {"j": 0.8}}, "per_cycle": [{"peak_C": {"j": 149.2}}], "stopped": None}, ["j"])
    assert ok["stable"] and ok["first_limit_change_s"] == pytest.approx(0.01) and "no decision changes" in ok["note"]
    bad = _resolution_compare(24, 0.5, None, per, [{"peak_C": {"j": 149.0}}],
                              {"first_limit": {"t_s": 9.0, "node": "j"}, "periodic": {"peak_C": {"j": 150.5},
                               "margin_K": {"j": -0.5}}, "per_cycle": [{"peak_C": {"j": 150.5}}], "stopped": None}, ["j"])
    assert not bad["stable"] and "one resolution" in bad["note"] and "changes sign" in bad["note"]
    # the allowed values are compared too: a value bounded at one resolution only is a decision change
    fine = {"first_limit": {"t_s": 3.01, "node": "j"}, "periodic": {"peak_C": {"j": 149.2}, "margin_K": {"j": 0.8}},
            "per_cycle": [{"peak_C": {"j": 149.2}}], "stopped": None,
            "allowed": {"pulse_duration_s": 4.1, "pulse_torque_Nm": math.inf, "first_pulse_torque_Nm": 431.0}}
    mine = {"pulse_duration_s": 4.0, "pulse_torque_Nm": 430.0, "first_pulse_torque_Nm": 431.0}
    flip = _resolution_compare(24, 0.5, fl, per, [{"peak_C": {"j": 149.0}}], fine, ["j"], mine)
    assert not flip["stable"] and "the allowed pulse torque is bounded at one resolution only" in flip["note"]
    assert flip["allowed_change_rel"]["pulse_duration_s"] == pytest.approx(0.1 / 4.1)


def test_the_default_run_reports_its_resolution_check(setup):
    d, sc, model = setup
    r = repeated_load(d, sc, model, [LoadPhase(460.0, 3000.0, 4.0), LoadPhase(50.0, 3000.0, 6.0)], cycles=2)
    rc = r["resolution_check"]
    assert rc["steps_per_phase"] == [24, 48] and rc["cache_K"] == [0.5, 0.25] and rc["stable"]
    assert r["allowed"]["loss_bound"]["corners"] >= 1
    # the allowed values are closed form in time: only the loss-cache grid could move them (review 3.2)
    assert rc["allowed_change_rel"] and rc["allowed_change_max_rel"] < 1e-3 and "allowed values" in rc["note"]


# -- 3.3 carrier frequency basis and the energy boundary -----------------------------------------------------------

from traction_workbench.extensions.pwm_policy import carrier_brackets      # noqa: E402


def test_the_waveform_models_bracket_a_non_integer_pulse_ratio():
    fe = 11000.0 * 4 / 60.0                                                # 733.3 Hz
    assert carrier_brackets(10e3, fe) == pytest.approx([13 * fe, 14 * fe])   # 9.53 and 10.27 kHz around 10 kHz
    assert carrier_brackets(11e3, fe) == [11e3]                            # an integer ratio is evaluated as requested
    assert carrier_brackets(100.0, 733.0) == [733.0]                       # below one carrier per period: one pulse


@pytest.mark.parametrize("cap, status", [(70.0, "VIOLATION"), (73.0, "UNKNOWN"), (76.0, "ADMISSIBLE")])
def test_a_limit_between_the_two_bracketing_carriers_is_not_decided(cap, status):
    """At 11000 rpm (733 Hz) the capacitor RMS current is 71.1 A with 13 x f_e and 74.7 A with 14 x f_e: a limit in
    between is decided by neither carrier alone."""
    seg = api.EXAMPLE_PWM["segments"][2]
    lim = {**(api.EXAMPLE_PWM.get("pwm_limits") or {}), "cap_rms_max_A": cap}
    p = api.pwm_policies({"segments": [seg], "schedules": [], "pwm_limits": lim})["policies"][0]
    assert p["status"] == status
    if status == "UNKNOWN":
        assert any("bracketing the requested frequency disagree" in u for u in p["unverified_required"])
        assert not p["violations"]


# -- 4 model margin vs the declared error budget; quantifiers ---------------------------------------------------------

from traction_workbench.analysis.error_budget import (ErrorBudget, ErrorItem, aggregate_robustness,  # noqa: E402
                                                      constraint_robustness, error_budget_from_dict,
                                                      torque_robustness)
from traction_workbench.errors import InputValidationError  # noqa: E402


def _budget(nm=None, pct=None):
    return ErrorBudget((ErrorItem("model", "model", "torque", nm, pct, "test"),))


@pytest.mark.parametrize("item", [
    {"source": "x", "kind": "guess", "torque_Nm": 1.0},                     # unknown kind
    {"source": "x", "kind": "model"},                                       # neither Nm nor %
    {"source": "x", "kind": "model", "torque_Nm": 1.0, "torque_percent": 2.0},
    {"source": "x", "kind": "model", "torque_Nm": -1.0},
    {"source": "x", "kind": "model", "torque_percent": 101.0},
    {"source": "x", "kind": "model", "torque_Nm": 0.0},                     # a zero error needs its basis
    {"source": "x", "kind": "input", "quantity": "phase_current", "value": 2.0},   # so does every bound
    {"source": "", "kind": "model", "torque_Nm": 1.0},
    {"source": "x", "kind": "model", "torque_Nm": 1.0, "sigma": 0.3},       # no probability fields
    {"source": "x", "kind": "model", "quantity": "flux", "value": 1.0},     # unknown quantity
    {"source": "x", "kind": "input", "quantity": "phase_current", "value": 20.0, "unit": "mA"},
    {"source": "x", "kind": "input", "quantity": "phase_current", "torque_Nm": 1.0},   # two ways at once
])
def test_an_error_budget_item_is_declared_completely(item):
    with pytest.raises(InputValidationError):
        error_budget_from_dict([item])


def test_no_budget_is_not_assessed_and_the_model_answer_stays_readable():
    assert error_budget_from_dict(None) is None and error_budget_from_dict([]) is None
    r = torque_robustness(None, 1, 100.0, 104.0, 104.1, "FEASIBLE")
    assert r["status"] == "NOT_ASSESSED" and r["met"] is True and r["margin_Nm"] == pytest.approx(4.0)
    zero = error_budget_from_dict([{"source": "x", "kind": "numerical", "torque_Nm": 0, "basis": "exact map"}])
    assert torque_robustness(zero, 1, 100.0, 100.0, None, "FEASIBLE")["status"] == "ROBUST_MET"   # y + 0 <= y_max


@pytest.mark.parametrize("d, T, c_lo, c_hi, budget, status", [
    # motoring: met at the found capability (a witness), not met at the certified bound
    (1, 100.0, 110.0, 110.0, _budget(5.0), "ROBUST_MET"),
    (1, 100.0, 110.0, 110.0, _budget(10.0), "ROBUST_MET"),                 # margin = error: y + delta <= y_max
    (1, 100.0, 110.0, 110.0, _budget(15.0), "MET_WITHIN_ERROR"),
    (1, 100.0, 90.0, 92.0, _budget(5.0), "ROBUST_NOT_MET"),                # short >= 8 at the bound > 5
    (1, 100.0, 90.0, 92.0, _budget(12.0), "NOT_MET_WITHIN_ERROR"),         # short 10 <= 12
    (1, 100.0, 90.0, None, _budget(9.0), "NOT_MET_NOT_ESTABLISHED"),       # no certified bound
    (1, 100.0, 90.0, 95.0, _budget(6.0), "NOT_MET_NOT_ESTABLISHED"),       # 5 at the bound < 6 < 10 at the witness
    # braking mirrors it (capability negative, more negative = more braking)
    (-1, -80.0, -84.0, -84.0, _budget(3.0), "ROBUST_MET"),
    (-1, -80.0, -84.0, -84.0, _budget(5.0), "MET_WITHIN_ERROR"),
    (-1, -100.0, -84.0, -89.0, _budget(4.0), "ROBUST_NOT_MET"),            # any-control bound -89: short >= 11
    (-1, -100.0, -84.0, -89.0, _budget(pct=20.0), "NOT_MET_WITHIN_ERROR"),  # 16 <= 20 % of 84 = 16.8
    # a percentage scales the capability end it is judged at
    (1, 100.0, 110.0, 110.0, _budget(pct=9.0), "ROBUST_MET"),              # 9.9 <= 10
    (1, 100.0, 110.0, 110.0, _budget(pct=9.2), "MET_WITHIN_ERROR"),        # 10.12 > 10
])
def test_the_guarded_rule_on_the_capability_margin(d, T, c_lo, c_hi, budget, status):
    assert torque_robustness(budget, d, T, c_lo, c_hi, None)["status"] == status


def test_a_margin_that_contradicts_the_static_claim_is_not_used():
    # a feasible request above the first feasible segment (non-contiguous feasible set): no single margin
    r = torque_robustness(_budget(1.0), 1, 100.0, 95.0, 96.0, "FEASIBLE")
    assert r["status"] == "NOT_ASSESSED" and "not contiguous" in r["reason"]
    assert torque_robustness(_budget(1.0), 1, 100.0, 110.0, 110.0, "UNKNOWN")["status"] == "NOT_ASSESSED"
    assert torque_robustness(_budget(1.0), 1, 100.0, None, None, "FEASIBLE")["status"] == "NOT_ASSESSED"


def _cond(status, met, robust, m, surplus, **kw):
    return {"status": status, "met": met, "robust": robust, "margin_Nm": m, "surplus_Nm": surplus,
            "delta_Nm": 5.0, **kw}


def test_conditions_aggregate_with_the_for_all_quantifier():
    b = _budget(5.0)
    names = ["Vdc 600 V", "Vdc 650 V"]
    ok = _cond("ROBUST_MET", True, True, 9.0, 4.0)
    weak = _cond("MET_WITHIN_ERROR", True, False, 3.0, -2.0)
    na = {"status": "NOT_ASSESSED", "met": None, "robust": None, "reason": "the torque capability is not established"}
    assert aggregate_robustness([ok, ok], names, b)["status"] == "ROBUST"
    a = aggregate_robustness([ok, weak], names, b)
    assert a["status"] == "WITHIN_ERROR" and a["governing"] == 1                  # one weak condition decides
    assert aggregate_robustness([ok, na], names, b)["status"] == "NOT_ESTABLISHED"
    rob_fail = _cond("ROBUST_NOT_MET", False, True, -9.0, 4.0, shortfall_at_bound_Nm=9.0, delta_at_bound_Nm=5.0)
    assert aggregate_robustness([weak, rob_fail], names, b)["status"] == "ROBUST"  # one robust counterexample
    open_fail = _cond("NOT_MET_NOT_ESTABLISHED", False, None, -9.0, 4.0, reason="no certified upper bound")
    in_err = _cond("NOT_MET_WITHIN_ERROR", False, False, -3.0, -2.0)
    assert aggregate_robustness([in_err, open_fail], names, b)["status"] == "NOT_ESTABLISHED"
    assert aggregate_robustness([in_err, in_err], names, b)["status"] == "WITHIN_ERROR"
    assert aggregate_robustness([ok, weak], names, None)["status"] == "NOT_ASSESSED"


class _C:                                   # a constraint result at a witness (name, demand, limit, slack, state)
    def __init__(self, name, demand, limit, unit, sense="upper", state=None):
        self.name, self.demand, self.limit, self.unit = name, demand, limit, unit
        self.slack = limit - demand if sense == "upper" else demand - limit
        self.state = state or ("ACTIVE" if abs(self.slack) < 1e-9 else "SATISFIED")


def test_a_limit_margin_is_compared_with_its_declared_error_in_its_own_unit():
    """The reviewer's example: limit 500 A, model 488 A, input + model error 20 A -> met in the model, within the
    error (y + D <= y_max fails); the same margin against a 2 % current-sensor gain (9.8 A) is robust."""
    items = lambda *its: error_budget_from_dict(list(its))                    # noqa: E731
    cur = [_C("CURRENT", 488.0, 500.0, "A")]
    (c,) = constraint_robustness(items({"source": "sensor + map", "kind": "input", "quantity": "phase_current",
                                        "value": 20.0, "basis": "review example"}), cur)
    assert c["status"] == "WITHIN_ERROR" and c["slack"] == pytest.approx(12.0) and c["delta"] == 20.0
    (c,) = constraint_robustness(items({"source": "sensor gain", "kind": "input", "quantity": "phase_current",
                                        "percent": 2.0, "basis": "datasheet"}), cur)
    assert c["status"] == "ROBUST" and c["delta"] == pytest.approx(9.76)
    # a voltage limit the witness sits on (field weakening) is held by the policy: not compared there
    (v,) = constraint_robustness(items({"source": "dead time", "kind": "model", "quantity": "voltage", "value": 5.0,
                                        "basis": "x"}), [_C("VOLTAGE", 329.1, 329.1, "V")])
    assert v["status"] == "ADAPTED" and v["robust"] is None
    # discharge and charge limits of one quantity: the one the point is near
    dc = [_C("DC_DISCHARGE_POWER", 196e3, 200e3, "W"), _C("DC_CHARGE_POWER", 196e3, -100e3, "W", sense="lower")]
    (d,) = constraint_robustness(items({"source": "loss model", "kind": "model", "quantity": "dc_power",
                                        "value": 5e3, "basis": "x"}), dc)
    assert d["constraint"] == "DC_DISCHARGE_POWER" and d["status"] == "WITHIN_ERROR"
    (n,) = constraint_robustness(items({"source": "x", "kind": "model", "quantity": "dc_current", "value": 1.0,
                                        "basis": "x"}), cur)
    assert n["status"] == "NOT_EVALUATED" and n["robust"] is None


def _decide(req, budget=None, **extra):
    from traction_workbench import service as S
    body = {"requirement": {"id": "R", "text": "t", **req}, **extra}
    if budget is not None:
        body["error_budget"] = budget
    return S.evaluate_decision(api.case_from_body(body))[0]


PCT = lambda x: [{"source": "flux-map torque", "kind": "model", "torque_percent": x, "basis": "FEA vs dyno"}]  # noqa


def test_the_layer_never_changes_the_model_verdict():
    base = {"torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 600}
    none, one, three = _decide(base), _decide(base, PCT(1.0)), _decide(base, PCT(3.0))
    assert {r.verdict.status.value for r in (none, one, three)} == {"FEASIBLE"}
    assert none.layers["model"] == one.layers["model"] == three.layers["model"]
    assert none.layers["robustness"]["status"] == "NOT_ASSESSED"
    assert one.layers["robustness"]["status"] == "ROBUST"                 # margin 2.56 N*m >= 1 % of 152.6 N*m
    assert three.layers["robustness"]["status"] == "WITHIN_ERROR"         # 2.56 N*m < 3 % of 152.6 N*m = 4.58 N*m
    assert "error_budget" not in none.snapshot and none.input_sha256 != three.input_sha256   # the budget is input
    fail = {"torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 450}
    assert _decide(fail, PCT(1.0)).layers["robustness"]["status"] == "ROBUST"        # short >= 15 N*m at the bound
    assert _decide(fail, PCT(20.0)).layers["robustness"]["status"] == "WITHIN_ERROR"  # 15 N*m <= 20 % of 135 N*m


def test_a_band_is_judged_at_its_edge_and_a_duration_keeps_its_own_evidence():
    band = _decide({"torque_Nm": 160, "speed_rpm": 12000, "Vdc_V": 600, "operator": "band", "band_Nm": 12}, PCT(1.0))
    rob = band.layers["robustness"]
    assert rob["T_edge_Nm"] == pytest.approx(148.0)
    assert rob["conditions"][0]["margin_Nm"] == pytest.approx(band.conditions[0].capability.value_Nm - 148.0)
    timed = _decide({"torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 600, "duration_s": 10}, PCT(1.0))
    assert timed.verdict.status.value == "UNKNOWN"
    assert timed.layers["robustness"]["verdict_relation"]["parts"] == ["duration"]


def test_the_limits_at_the_witness_join_the_torque_margin_in_one_statement():
    base = {"torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 600}
    both = _decide(base, PCT(1.0) + [{"source": "loss model", "kind": "model", "quantity": "dc_power",
                                      "value": 5000.0, "basis": "loss map"}])
    rob = both.layers["robustness"]
    assert both.verdict.status.value == "FEASIBLE"                         # the model verdict is unchanged
    assert rob["conditions"][0]["status"] == "ROBUST_MET"                   # 2.56 N*m >= 1 % of 152.6 N*m
    assert rob["status"] == "WITHIN_ERROR" and rob["governing_check"]["constraint"] == "DC_DISCHARGE_POWER"
    assert rob["governing_check"]["slack"] == pytest.approx(3368.6, abs=0.5)  # 200 kW - 196.63 kW < 5 kW
    ok = _decide(base, [{"source": "sensor gain", "kind": "input", "quantity": "phase_current", "percent": 2.0,
                         "basis": "datasheet"}]).layers["robustness"]
    assert ok["status"] == "ROBUST" and ok["checks"][0]["constraint"] == "CURRENT"
    fw = _decide(base, [{"source": "dead time", "kind": "model", "quantity": "voltage", "value": 5.0,
                         "basis": "x"}]).layers["robustness"]
    assert fw["status"] == "NOT_ESTABLISHED" and fw["checks"][0]["status"] == "ADAPTED"
    fail = _decide({**base, "Vdc_V": 450}, [{"source": "sensor gain", "kind": "input", "quantity": "phase_current",
                                            "value": 20.0, "basis": "x"}]).layers["robustness"]
    assert fail["status"] == "NOT_ASSESSED" and "torque capability" in fail["meaning"]   # a FAIL is judged in torque


def test_an_ocv_stated_vdc_is_not_established_against_the_error():
    rec = _decide({"torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 620, "Vdc_port": "battery_ocv"}, PCT(1.0),
                  source_model={"kind": "thevenin", "R_eq_mohm": 20, "basis": "pack at 50 % SOC, 25 degC"})
    rob = rec.layers["robustness"]
    assert rob["status"] == "NOT_ESTABLISHED" and "terminal voltage moves with the torque" in rob["meaning"]


def test_the_quantifiers_are_written_out_in_the_record_and_the_reading():
    from traction_workbench.insight.decision import decision_insight
    from traction_workbench.service import decision_record
    band = _decide({"torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": [600, 650], "operator": "band", "band_Nm": 5})
    q = band.layers["requirement"]["quantifiers"]
    assert q["torque"].startswith("exists: some shaft torque in [145, 155]") and q["Vdc"].startswith("for all")
    point = _decide({"torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 600})
    assert point.layers["requirement"]["quantifiers"]["torque"] == "the shaft torque 150 N*m itself"
    import re
    lines = decision_insight(decision_record(band, _case_of(band), {}, 0.0)).plain_lines()
    judged = next(x for x in lines if "question judged" in x or "판정한 질문" in x)
    assert "(∃)" in judged and "(∀)" in judged
    assert not re.search(r"\b[A-Z]{3,}_[A-Z_]+\b", judged)


def _case_of(rec):
    from types import SimpleNamespace
    from traction_workbench.units import Conversions
    return SimpleNamespace(conversions=Conversions())


def test_a_multi_plane_map_reads_for_every_magnet_temperature():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_review_6198099 import _two_plane_map
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    drive, lim = _two_plane_map()
    rec = evaluate_requirement(Requirement("R-M", "t", 125.0, 3000.0, 600.0), drive, source_limits=lim,
                               error_budget=error_budget_from_dict(PCT(2.0)))
    assert rec.layers["requirement"]["quantifiers"]["magnet"].startswith("for all: every flux-map magnet temperature")
    rob = rec.layers["robustness"]
    assert [c["status"] for c in rob["conditions"]] == ["MET_WITHIN_ERROR", "ROBUST_NOT_MET"]
    assert rob["status"] == "ROBUST" and rob["names"][rob["governing"]].endswith("magnet 120 degC")


def test_the_markdown_record_shows_the_budget_and_the_quantifiers():
    from traction_workbench import service as S
    body = {"requirement": {"id": "R", "text": "t", "torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 600},
            "error_budget": PCT(3.0)}
    out, rec, case = S.evaluate_case_full(api.case_from_body(body))
    md = out["markdown"]
    assert "| robustness | WITHIN_ERROR |" in md and "## Margin vs the declared error budget" in md
    assert "| quantifiers | torque: the shaft torque 150 N*m itself" in md
    assert isinstance(case.error_budget, ErrorBudget)


# -- 3.1 iron loss at a field-weakening witness: what rests on the speed-only loss model -----------------------------

@pytest.mark.parametrize("iron, T, direction", [(False, 150.0, "unmodelled"), (False, -80.0, "conservative"),
                                                 (True, 150.0, "not proven"), (True, -80.0, "not proven")])
def test_a_field_weakening_witness_states_what_rests_on_the_loss_model(iron, T, direction):
    from traction_workbench import spec_fixtures as sf
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    d = sf.synthetic_drive()
    d = replace(d, motor=replace(d.motor, rotational_loss=replace(d.motor.rotational_loss, includes_iron_loss=iron)))
    rec = evaluate_requirement(Requirement("R", "t", T, 12000.0, 600.0), d, source_limits=sf.synthetic_limits())
    pt = rec.conditions[0].primary.point
    assert pt.constraint("VOLTAGE").state == "ACTIVE"                          # a voltage-limited witness
    (x,) = rec.layers["qualification"]["iron_loss_scope"]
    assert x["direction"] == direction and 0.5 < x["flux_ratio"] < 0.8          # |psi| well below the no-load flux
    assert x["Nm_per_kW"] == pytest.approx(1e3 / (12000.0 * 2 * math.pi / 60))  # identity: 1 kW / omega_m
    assert x["torque_margin_Nm"] == rec.conditions[0].torque_margin_Nm
    if T > 0:
        dis = [c for c in pt.constraints if c.group == "DISCHARGE_SOURCE"]
        assert x["dc_margin_W"] == pytest.approx(min(c.slack if c.unit == "W" else c.slack * 600.0 for c in dis))
    assert any("field-weakening witness" in m for m in rec.layers["qualification"]["sub_models"])
    assert rec.verdict.status.value in ("FEASIBLE", "INFEASIBLE")                  # a scope statement, not a verdict


def test_a_point_below_the_voltage_limit_has_no_field_weakening_statement():
    from traction_workbench import spec_fixtures as sf
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    rec = evaluate_requirement(Requirement("R", "t", 300.0, 3000.0, 600.0), sf.synthetic_drive(),
                               source_limits=sf.synthetic_limits())
    assert rec.conditions[0].primary.point.constraint("VOLTAGE").state != "ACTIVE"
    assert rec.layers["qualification"]["iron_loss_scope"] == []
