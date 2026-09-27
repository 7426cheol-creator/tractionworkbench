"""Variable switching-frequency policy evaluation (variable-PWM / anti-jerk addendum, P1-PWM).

Independent references: the phase-current ripple by time integration vs the analytic edge-sum spectrum, the
averaged switching loss vs an edge-by-edge event sum with the same energy curves, a clock-level timer for period
transitions, and the delay arithmetic of the addendum's section 6 example.
"""

import math

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions import pwm_policy as P
from traction_workbench.extensions.emi import SwitchingSource, pwm_edges
from traction_workbench.extensions.module_loss import inverter_losses


@pytest.mark.parametrize("fsw", [5e3, 10e3, 20e3])
def test_ripple_time_integration_matches_the_edge_sum_spectrum(fsw):
    r = P.phase_ripple(600.0, 0.8, 0.3, 400.0, fsw, 150e-6)
    assert r["ripple_rms_A"] == pytest.approx(r["ripple_rms_spectrum_A"], rel=1e-3)


def test_ripple_scales_with_one_over_fsw_and_vanishes_at_zero_modulation():
    a = P.phase_ripple(600.0, 0.7, 0.0, 200.0, 10e3, 200e-6)
    b = P.phase_ripple(600.0, 0.7, 0.0, 200.0, 20e3, 200e-6)
    assert b["ripple_rms_A"] == pytest.approx(0.5 * a["ripple_rms_A"], rel=0.02)
    z = P.phase_ripple(600.0, 0.0, 0.0, 200.0, 10e3, 200e-6)
    assert z["ripple_rms_A"] < 1e-9


def test_averaged_switching_loss_equals_the_event_sum():
    """fsw * mean E(i) is the event sum only for the actual event count and energy ownership."""
    model = api.module_model_from_dict({**api.EXAMPLE_MODULE, "fsw_kHz": 20.0})
    dev = model.device
    I, V, Vdc, Tj = 300.0, 250.0, 600.0, 125.0
    beta, alpha = math.radians(100.0), math.radians(125.0)            # current / voltage angles, phi = 25 deg
    op = inverter_losses(model, I * math.cos(beta), I * math.sin(beta), V * math.cos(alpha), V * math.sin(alpha),
                         Vdc, Tj, refine_check=False)
    src = SwitchingSource(Vdc, I, beta, V / (0.5 * Vdc), alpha, 200.0, 20e3, 1e-9, 1e-9, 0.0, "svpwm", "test")
    e = pwm_edges(src)
    E = 0.0
    for (_k, _t, sgn, _tau, i) in e["edges"]:
        a = np.array([abs(i)])
        if (sgn > 0 and i > 0) or (sgn < 0 and i < 0):              # a switch turns on, the opposite diode recovers
            E += float(dev.e_on.eval(a, Tj)[0] + dev.e_rr.eval(a, Tj)[0])
        else:                                                         # a switch turns off
            E += float(dev.e_off.eval(a, Tj)[0])
    P_events = E / e["period_s"]
    assert e["carrier_ratio"] == 100
    assert P_events == pytest.approx(op["switching_W"], rel=0.01)


def test_minimum_pulse_gets_tighter_with_frequency():
    lo = P.minimum_pulse(1.1, "svpwm", 10e3, 1.5e-6)
    hi = P.minimum_pulse(1.1, "svpwm", 20e3, 1.5e-6)
    assert lo["ok"] and not hi["ok"]
    assert hi["narrowest_pulse_s"] == pytest.approx(0.5 * lo["narrowest_pulse_s"], rel=1e-9)


def test_period_change_is_legal_only_at_the_atomic_reload():
    sh = P.transition_check(10e3, 20e3, 0.9, 1e-6, 1.5e-6, write_fraction=0.3, shadow=True)
    im = P.transition_check(10e3, 20e3, 0.9, 1e-6, 1.5e-6, write_fraction=0.3, shadow=False)
    assert sh["ok"] and sh["worst_period_duty_error"] < 1e-9
    assert not im["ok"] and (im["worst_period_duty_error"] > 0.02 or im["irregular_periods"] > 0)
    dn = P.transition_check(20e3, 10e3, 0.5, 1e-6, 1.5e-6, write_fraction=0.6, shadow=False)
    assert not dn["ok"]


def test_gate_event_checker_catches_overlap_duplicates_and_runts():
    ok = P.check_gate_events([(6e-6, 1), (40e-6, -1)], [(0.0, 1), (5e-6, -1), (41e-6, 1)], 1e-6, 1e-6, 100e-6)
    assert ok["ok"] and ok["pulses_upper"] == 1 and ok["pulses_lower"] == 2
    bad = P.check_gate_events([(1e-6, 1), (2e-6, 1), (40e-6, -1)], [(0.0, 1), (0.5e-6, -1), (40.2e-6, 1)],
                              1e-6, 1e-6, 100e-6)
    txt = " ".join(bad["problems"])
    assert not bad["ok"] and "duplicate rising" in txt and "dead time" in txt
    runt = P.check_gate_events([(1e-6, 1), (1.2e-6, -1)], [], 1e-6, 0.0, 10e-6)
    assert not runt["ok"] and "shorter than the minimum" in runt["problems"][0]


def _noisy(mean, sigma, n, seed=1):
    rng = np.random.default_rng(seed)
    return [{"speed_rpm": mean + sigma * rng.standard_normal()} for _ in range(n)]


def test_schedule_hysteresis_dwell_protective_and_unavailable_measurements():
    rules = (P.FswRule("low speed", 20e3, speed_rpm=(0, 3000)), P.FswRule("default", 10e3))
    t = np.arange(0, 1, 1e-3)
    meas = _noisy(3000.0, 80.0, t.size)
    no_h = P.replay_schedule(P.FswSchedule("n", rules, 10e3), t, meas)
    with_h = P.replay_schedule(P.FswSchedule("h", rules, 10e3, (("speed_rpm", 300.0),), 0.05), t, meas)
    assert no_h["chatter"] and no_h["n_transitions"] > 100
    assert not with_h["chatter"] and with_h["dwell_violations"] == 0 and with_h["n_transitions"] < 10
    hot = P.FswSchedule("p", (P.FswRule("hot", 6e3, sensor_temp_C=(90, 200), protective=True),
                              P.FswRule("hi", 16e3, speed_rpm=(9000, 30000)), P.FswRule("default", 10e3)), 10e3,
                        min_dwell_s=10.0)
    s = hot.step(None, {"speed_rpm": 10000.0, "sensor_temp_C": 70.0}, 0.0)
    assert hot.fsw(s) == 16e3
    s = hot.step(s, {"speed_rpm": 5000.0, "sensor_temp_C": 70.0}, 1.0)            # dwell: deferred
    assert hot.fsw(s) == 16e3 and "deferred" in s["reason"]
    s = hot.step(s, {"speed_rpm": 10000.0, "sensor_temp_C": 95.0}, 2.0)           # protective: pre-empts the dwell
    assert hot.fsw(s) == 6e3 and s["reason"] == "protective pre-emption"
    only_temp = P.FswSchedule("t", (P.FswRule("hot", 6e3, sensor_temp_C=(90, 200)),), 12e3)
    s = only_temp.step(None, {"speed_rpm": 1000.0}, 0.0)                          # sensor not available
    assert only_temp.fsw(s) == 12e3                                               # fallback, never assumed hot/cold
    with pytest.raises(InputValidationError):
        P.FswRule("bad", 10e3, speed_rpm=(5000, 1000))


def test_delay_example_of_the_addendum_and_the_deadline():
    for fsw, tau_ms, ph_mode, ph_loop in ((10e3, 0.15, 1.104, 54.0), (5e3, 0.30, 2.208, 108.0)):
        tau = 1.5 / fsw
        assert tau * 1e3 == pytest.approx(tau_ms)
        assert P.phase_lag_deg(20.4438226, tau) == pytest.approx(ph_mode, abs=5e-4)
        assert P.phase_lag_deg(1000.0, tau) == pytest.approx(ph_loop, abs=1e-9)
    tc = P.TimingConfig(40e-6, 0.0, 1, 0.5, 0.0, basis="test")
    assert P.delay_ledger(20e3, tc)["deadline_ok"] and not P.delay_ledger(30e3, tc)["deadline_ok"]
    assert P.delay_ledger(30e3, tc)["total_delay_s"] is None                     # a missed deadline is a violation
    led = P.delay_ledger(10e3, P.TimingConfig(20e-6, 5e-6, 1, 0.5, basis="test"))
    assert led["total_delay_s"] == pytest.approx(5e-6 + 100e-6 + 50e-6)          # each delay counted once
    with pytest.raises(InputValidationError):
        P.TimingConfig(20e-6, basis="")


def test_current_loop_margin_and_gain_mapping():
    L, R = 300e-6, 0.015
    kp = 2 * math.pi * 500 * L
    cont = P.CurrentLoop(L, R, kp, kp * R / L)
    fixed = P.CurrentLoop(L, R, kp, kp * R / L, "fixed_discrete", 10e3)
    m10 = cont.margins(1.5 / 10e3)
    m5 = cont.margins(1.5 / 5e3)
    assert m10["crossover_Hz"] == pytest.approx(500.0, rel=1e-3)                  # pole-zero cancellation: L = wc L / (j w L)
    assert m10["phase_margin_deg"] == pytest.approx(90.0 - 360 * 500 * 1.5e-4, abs=0.05)
    assert m5["phase_margin_deg"] < m10["phase_margin_deg"]                       # carrier halving costs phase
    assert fixed.effective_Ki(5e3) == pytest.approx(0.5 * cont.Ki)               # Ki*Ts kept: a different controller
    assert cont.effective_Ki(5e3) == cont.Ki


def test_harmonic_copper_loss_needs_covering_rac_data():
    rip = P.phase_ripple(600.0, 0.8, 0.0, 400.0, 10e3, 200e-6)
    part = P.HarmonicLossData((1e3, 1e5), (1.0, 3.0), basis="t")
    assert P.harmonic_copper_loss(rip, 0.015, part)["W"] is None                  # low-order harmonics below 1 kHz
    full = P.HarmonicLossData((0.0, 1e5, 1e7), (1.0, 3.0, 30.0), basis="t")
    unit = P.HarmonicLossData((0.0, 1e7), (1.0, 1.0), basis="t")
    w_unit = P.harmonic_copper_loss(rip, 0.015, unit)["W"]
    assert w_unit == pytest.approx(3 * 0.015 * rip["ripple_rms_spectrum_A"] ** 2, rel=1e-9)   # 3 R sum I_rms^2
    assert P.harmonic_copper_loss(rip, 0.015, full)["W"] > w_unit
    assert P.harmonic_copper_loss(rip, 0.015, None)["W"] is None


def test_policy_comparison_never_trades_a_mandatory_constraint():
    r = api.pwm_policies({})
    pol = {p["policy"]["name"]: p for p in r["policies"]}
    base, light, fb = pol["fixed 10 kHz"], pol["light-load 8 kHz"], pol["thermal fallback 6 kHz"]
    assert base["admissible"] and light["admissible"]
    assert not fb["admissible"] and any("phase margin" in v for v in fb["violations"])
    assert light["E_inv_J"] < base["E_inv_J"]
    assert light["versus_baseline"]["total"]["status"] in ("UNDECIDED", "IMPROVED", "WORSE")
    assert r["best_inverter_energy_among_evaluated"] == "light-load 8 kHz"
    assert "thermal fallback 6 kHz" not in r["pareto"]
    assert any(s["fsw_Hz"] == 8e3 for s in light["segments"])
    assert any("protective" in e["reason"] for e in fb["transitions"])
    # without harmonic data the inverter gain is not a motor+inverter gain
    r2 = api.pwm_policies({"harmonic": None, "schedules": api.EXAMPLE_PWM["schedules"][:1]})
    v = {p["policy"]["name"]: p for p in r2["policies"]}["light-load 8 kHz"]["versus_baseline"]
    assert v["delta_E_inv_J"] < 0 and v["total"]["status"] == "UNKNOWN"
    # a missing limit is never a pass
    r3 = api.pwm_policies({"pwm_limits": {}, "schedules": []})
    assert any("no limit declared" in u for u in r3["policies"][0]["unverified"])
