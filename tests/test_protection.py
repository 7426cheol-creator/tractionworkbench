"""Protection thresholds / derating / fault reaction (independent review section 9): V1 closed forms,
causal event traces, detector semantics and the PROT review table."""

import math

import numpy as np
import pytest

from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.protection import (Plant, Sensor, n_sample_confirmation_bound, ov_peak_after_trigger,
                                                       ov_trigger_bound, phase_sweep, protection_review, simulate,
                                                       threshold_window)


# ---------------------------------------------------------------- 9.6.1 OV toy (synthetic, not a product value)

def test_ov_energy_bound_reproduces_the_worked_example():
    b = ov_trigger_bound(500e-6, 800.0, 100e3, 0.2e-3)
    assert b["E_after_J"] == pytest.approx(20.0)
    assert b["V_trigger_max_V"] == pytest.approx(748.331, abs=5e-4)
    assert 0.5 * 500e-6 * (800 ** 2 - 700 ** 2) == pytest.approx(37.5)          # headroom 700 -> 800 V
    assert 37.5 / 100e3 == pytest.approx(375e-6)                                 # limit after 375 us without reaction
    nominal = b["V_trigger_max_V"] - 5.0 - 3.0
    assert nominal == pytest.approx(740.331, abs=5e-4)
    assert ov_peak_after_trigger(500e-6, 753.0, 20.0) == pytest.approx(804.369, abs=5e-4)   # "745 V looks safe"


@pytest.mark.parametrize("xn, exists", [(730.0, True), (740.0, False)])
def test_ov_threshold_window_table(xn, exists):
    ub = ov_trigger_bound(500e-6, 800.0, 100e3, 0.2e-3)["V_trigger_max_V"]
    w = threshold_window(xn, 800.0, E_plus=5.0, E_minus=5.0, E_theta=3.0, upper_bound=ub, tight_attainable=True)
    assert w["nuisance_lower_bound"] == pytest.approx(xn + 8.0)
    assert w["protection_upper_bound"] == pytest.approx(740.331, abs=5e-4)
    assert w["window_exists"] is exists
    assert w["claim"]["status"] == ("FEASIBLE" if exists else "INFEASIBLE")        # tight toy set only


def test_empty_conservative_window_is_unknown_unless_tight():
    w = threshold_window(740.0, 800.0, E_plus=5.0, E_minus=5.0, E_theta=3.0, dx_after=52.0)
    assert not w["window_exists"] and w["claim"]["status"] == "UNKNOWN"
    assert w["claim"]["reasons"] == ["BOUND_INCONCLUSIVE"]


def test_radicand_not_positive_gives_no_guaranteed_threshold():
    b = ov_trigger_bound(100e-6, 800.0, 1e6, 1e-3)
    assert b["radicand_V2"] <= 0 and b["V_trigger_max_V"] is None


def test_immediate_ramp_understates_energy():
    b = ov_trigger_bound(500e-6, 800.0, 100e3, 0.1e-3, t_ramp_s=0.2e-3)
    assert b["E_after_J"] == pytest.approx(100e3 * 0.1e-3 + 0.5 * 100e3 * 0.2e-3)
    assert b["immediate_ramp_E_J"] < b["E_after_J"]


def _ov_plant():
    return Plant("capacitor_energy", 700.0, (("C_F", 500e-6), ("P0_W", 100e3), ("t_ramp_s", 0.0)))


@pytest.mark.parametrize("theta, protected", [(740.0, True), (745.0, False)])
def test_ov_causal_trace_matches_the_energy_argument(theta, protected):
    fast = Sensor(gain_error=0.0, offset=-5.0, period_s=1e-7, confirm_samples=1)   # under-reads by 5 V
    r = simulate(_ov_plant(), fast, theta, 800.0, 1e-3, action_delay_s=0.2e-3, threshold_error=+3.0)
    trig = theta + 3.0 + 5.0                                                      # physical trigger voltage
    assert r["events"]["t_confirm_s"] == pytest.approx(0.5 * 500e-6 * (trig ** 2 - 700 ** 2) / 100e3, abs=2e-7)
    assert r["events"]["peak"] == pytest.approx(ov_peak_after_trigger(500e-6, trig, 20.0), abs=0.05)
    assert r["protected"] is protected


# ---------------------------------------------------------------- 9.6.2 OT toy (synthetic, not a product value)

def _ot_plant():
    return Plant("thermal_1node", 135.0, (("R_K_per_W", 0.2), ("C_J_per_K", 20.0), ("T_coolant_C", 60.0),
                                          ("P_W", 600.0), ("P_after_W", 350.0)))


def test_ot_closed_forms():
    pl = _ot_plant()
    t = np.linspace(0.0, 5.0, 500001)
    free = pl.x(t, math.inf)
    assert t[np.argmax(free >= 150.0)] == pytest.approx(-4.0 * math.log(30.0 / 45.0), abs=2e-5)   # 1.62186 s
    assert -4.0 * math.log(30.0 / 45.0) == pytest.approx(1.62186, abs=1e-5)
    assert pl.x(np.array([0.5]), 0.5)[0] == pytest.approx(140.28764, abs=1e-5)
    assert pl.x(t, 0.5).max() < 150.0                          # derating effective after 0.5 s: cools down
    assert pl.x(t, 2.0).max() > 150.0                          # same derating 2 s later: the limit comes first


def test_ot_same_threshold_and_power_but_later_action_fails():
    s = Sensor(period_s=1e-3, confirm_samples=1)
    ok = simulate(_ot_plant(), s, 135.0, 150.0, 6.0, action_delay_s=0.5)
    late = simulate(_ot_plant(), s, 135.0, 150.0, 6.0, action_delay_s=2.0)
    assert ok["protected"] and not late["protected"]
    assert late["events"]["t_limit_s"] == pytest.approx(1.62186, abs=2e-3)


# ---------------------------------------------------------------- detector semantics (V1 / V2)

def test_first_order_filter_ramp_response_is_exact():
    a, tau = 100.0, 0.01
    s = Sensor(tau_filter_s=tau, period_s=1e-4)
    r = simulate(Plant("ramp", 0.0, (("slope_per_s", a),)), s, 1e9, 1e12, 0.05)
    t, y = r["t_s"], r["y"]
    exact = a * (t - tau * (1.0 - np.exp(-t / tau)))
    assert np.max(np.abs(y - exact)) < 1e-9 * a


def test_n_consecutive_samples_and_phase():
    Ts, n = 1e-3, 3
    s = Sensor(period_s=Ts, confirm_samples=n)
    for ph in (0.0, 0.25e-3, 0.999e-3):
        r = simulate(Plant("ramp", 0.0, (("slope_per_s", 1000.0),)), Sensor(period_s=Ts, confirm_samples=n, phase_s=ph),
                     10.0, 1e9, 0.05)
        t_cross = 10.0 / 1000.0
        wait = r["events"]["t_confirm_s"] - t_cross
        assert 0.0 <= wait <= n_sample_confirmation_bound(Ts, n) + 1e-12       # worst waiting N*Ts (no jitter)
    sw = phase_sweep(Plant("ramp", 0.0, (("slope_per_s", 1000.0),)), s, 10.0, 1e9, 0.05, phases=10)
    assert sw["latest_confirm_s"] - sw["earliest_confirm_s"] == pytest.approx(0.9e-3, abs=1e-9)


def test_comparator_equality_convention():
    ramp = Plant("ramp", 0.0, (("slope_per_s", 1000.0),))            # x = 10 exactly at the sample t = 10 ms
    ge = simulate(ramp, Sensor(period_s=1e-3, comparator=">="), 10.0, 1e9, 0.05)
    gt = simulate(ramp, Sensor(period_s=1e-3, comparator=">"), 10.0, 1e9, 0.05)
    assert ge["events"]["t_confirm_s"] == pytest.approx(10e-3) and gt["events"]["t_confirm_s"] == pytest.approx(11e-3)


def test_ripple_resets_the_debounce_counter():
    # slow ramp + ripple: samples alternate above/below the threshold for a while; 3 consecutive needed
    rip = Plant("ramp", 0.0, (("slope_per_s", 50.0), ("ripple_amp", 2.0), ("ripple_hz", 250.0)))
    r = simulate(rip, Sensor(period_s=1e-3, confirm_samples=3), 10.0, 1e9, 1.0)
    flags = r["sample_flags"]
    first_true = int(np.argmax(flags))
    k = np.flatnonzero(r["samples_t_s"] == r["events"]["t_confirm_s"])[0]
    assert flags[k] and flags[k - 1] and flags[k - 2]
    assert k - first_true > 2                                   # the counter was reset in between


def test_invalid_detector_inputs_are_rejected():
    with pytest.raises(InputValidationError):
        Sensor(period_s=1e-3, phase_s=1e-3)
    with pytest.raises(InputValidationError):
        Sensor(confirm_samples=0)
    with pytest.raises(InputValidationError):
        Sensor(comparator="=>")


# ---------------------------------------------------------------- PROT review table

def test_protection_review_rows_and_statuses():
    s = Sensor(offset=5.0, period_s=1e-5, confirm_samples=2)
    normal = (Plant("capacitor_energy", 700.0, (("C_F", 500e-6), ("P0_W", 20e3), ("t_ramp_s", 0.0))),)
    ub = ov_trigger_bound(500e-6, 800.0, 100e3, 0.2e-3)["V_trigger_max_V"]
    rv = protection_review(_ov_plant(), s, 738.5, 800.0, 1e-3, action_delay_s=0.15e-3, E_theta=3.0,
                           normal_plants=normal, x_normal_max=720.0, upper_bound=ub, release_threshold=720.0,
                           phases=8)
    rows = {r["id"]: r for r in rv["rows"]}
    assert set(rows) == {f"PROT-0{i}" for i in range(1, 10)}
    assert rows["PROT-04"]["status"] == "FEASIBLE"
    assert rows["PROT-05"]["status"] == "FEASIBLE"
    assert rows["PROT-07"]["status"] == "UNKNOWN" and rows["PROT-09"]["status"] == "UNKNOWN"
    late = protection_review(_ov_plant(), s, 745.0, 800.0, 1e-3, action_delay_s=0.3e-3, E_theta=3.0, phases=4)
    assert {r["id"]: r for r in late["rows"]}["PROT-04"]["status"] == "INFEASIBLE"
    assert {r["id"]: r for r in late["rows"]}["PROT-01"]["status"] == "UNKNOWN"      # no normal set declared


def test_nuisance_trip_is_a_counterexample():
    s = Sensor(offset=5.0, period_s=1e-5)
    normal = (Plant("ramp", 700.0, (("slope_per_s", 0.0), ("ripple_amp", 36.0), ("ripple_hz", 300.0))),)
    rv = protection_review(_ov_plant(), s, 740.0, 800.0, 1e-3, E_theta=3.0, normal_plants=normal, phases=2)
    assert {r["id"]: r for r in rv["rows"]}["PROT-01"]["status"] == "INFEASIBLE"
