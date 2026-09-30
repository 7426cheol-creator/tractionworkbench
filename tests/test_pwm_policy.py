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
from traction_workbench.models.module_loss import inverter_losses


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


def test_a_pulse_cut_by_the_observation_window_is_not_a_runt():
    # [0, 8 us] starts at the window start and [99.5 us, ...) is still on at the window end: both widths are unknown,
    # neither is judged against the minimum (their dead-time gaps still are)
    cut = P.check_gate_events([(10e-6, 1), (90e-6, -1)], [(0.0, 1), (8e-6, -1), (99.5e-6, 1)], 1e-6, 1e-6, 100e-6)
    assert cut["ok"] and cut["open_pulses"] == 2
    whole = P.check_gate_events([(10e-6, 1), (90e-6, -1)], [(0.0, 1), (8e-6, -1), (95e-6, 1), (95.5e-6, -1)],
                                1e-6, 1e-6, 100e-6)
    assert not whole["ok"] and "shorter than the minimum" in whole["problems"][0]


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


# ------------------------------------------------------------------ addendum 7.2 mandatory failure cases (PWM side)

def _sens(kind, **kw):
    base = {"settle_s": 2e-6, "aperture_s": 1e-6, "basis": "test"}
    base.update(kw)
    return P.SensingConfig(kind, **base)


def test_single_shunt_windows_equal_the_classical_svpwm_active_vector_times():
    """Independent closed form (sector-local angle t): per half carrier period the two active vectors last
    (sqrt3 / 4) m Ts sin(pi/3 - t) and (sqrt3 / 4) m Ts sin(t); the shunt window is the shorter one."""
    m, fsw, fe = 0.8, 10e3, 50.0
    r = P.sampling_validity(m, "svpwm", fsw, fe, _sens("dc_link_shunt", settle_s=0.0, aperture_s=0.0))
    Ts = 1.0 / fsw
    loc = (2 * math.pi * fe * (r["t_s"] + 0.5 * Ts)) % (math.pi / 3)
    ref = np.minimum((math.sqrt(3) / 4) * m * Ts * np.sin(math.pi / 3 - loc), (math.sqrt(3) / 4) * m * Ts * np.sin(loc))
    assert np.max(np.abs(r["window_s"] - ref)) < 1e-15


def test_invalid_samples_are_never_silently_accepted():
    # single shunt at low modulation: no valid sample at all -> a violation whatever the fallback
    for pol in ("none", "hold"):
        r = P.sampling_validity(0.1, "svpwm", 10e3, 100.0, _sens("dc_link_shunt", invalid_policy=pol,
                                                                   max_sample_age_s=1e-3, current_error_max_A=50.0),
                                I_pk_A=300.0)
        assert r["status"] == "VIOLATION" and r["valid_fraction"] == 0.0 and math.isinf(r["max_age_s"])
    # partly invalid: no fallback -> violation; hold without limits -> UNKNOWN; hold with limits -> judged
    kw = dict(I_pk_A=300.0)
    assert P.sampling_validity(0.8, "svpwm", 10e3, 100.0, _sens("dc_link_shunt"), **kw)["status"] == "VIOLATION"
    r = P.sampling_validity(0.8, "svpwm", 10e3, 100.0, _sens("dc_link_shunt", invalid_policy="hold"), **kw)
    assert r["status"] == "UNKNOWN" and 0 < r["valid_fraction"] < 1
    age = r["max_age_s"]
    assert r["hold_error_bound_A"] == pytest.approx(2 * math.pi * 100.0 * 300.0 * age)
    ok = P.sampling_validity(0.8, "svpwm", 10e3, 100.0, _sens("dc_link_shunt", invalid_policy="hold",
                                                              max_sample_age_s=2 * age,
                                                              current_error_max_A=2 * r["hold_error_bound_A"]), **kw)
    assert ok["status"] == "OK"
    tight = P.sampling_validity(0.8, "svpwm", 10e3, 100.0, _sens("dc_link_shunt", invalid_policy="hold",
                                                                 max_sample_age_s=0.5 * age,
                                                                 current_error_max_A=1e6), **kw)
    assert tight["status"] == "VIOLATION"
    pred = P.sampling_validity(0.8, "svpwm", 10e3, 100.0, _sens("dc_link_shunt", invalid_policy="predict",
                                                                predict_error_fraction=0.25), **kw)
    assert pred["error_bound_A"] == pytest.approx(0.25 * r["hold_error_bound_A"])
    # at (near) standstill an invalid angle can persist: the held value may never refresh
    st = P.sampling_validity(0.8, "svpwm", 10e3, 0.0, _sens("dc_link_shunt", invalid_policy="hold",
                                                            max_sample_age_s=1.0, current_error_max_A=1e6), **kw)
    assert st["quasi_static"] and math.isinf(st["max_age_s"]) and st["status"] == "VIOLATION"
    # leg shunts with two-leg reconstruction keep valid windows where the single shunt cannot
    assert P.sampling_validity(0.1, "svpwm", 10e3, 100.0, _sens("leg_shunt"))["status"] == "OK"
    # inline sensing near the top of the linear range: the zero vector around the valley becomes too short
    assert P.sampling_validity(1.1, "svpwm", 10e3, 100.0, _sens("inline_phase"))["n_invalid"] > 0
    with pytest.raises(InputValidationError):
        _sens("leg_shunt", sample_points="peak")                    # low-side shunts cannot measure at the peak
    with pytest.raises(InputValidationError):
        _sens("inline_phase", invalid_policy="predict")             # a predictor needs its validated residual


def test_channel_skew_error_is_the_zero_vector_slope_times_the_skew():
    s = _sens("inline_phase", channel_skew_s=1e-6, current_error_max_A=1.0)
    r = P.sampling_validity(0.6, "svpwm", 10e3, 100.0, s, I_pk_A=200.0, V1_pk_V=180.0, L_hf_H=200e-6)
    expect = 1e-6 * (180.0 / 200e-6 + 2 * math.pi * 100.0 * 200.0)
    assert r["skew_error_bound_A"] == pytest.approx(expect)
    assert r["status"] == "OK" if expect <= 1.0 else r["status"] == "VIOLATION"
    assert P.sampling_validity(0.6, "svpwm", 10e3, 100.0, _sens("inline_phase", channel_skew_s=1e-6), I_pk_A=200.0,
                               V1_pk_V=180.0, L_hf_H=200e-6)["status"] == "UNKNOWN"      # no error limit declared


def _loop(**kw):
    L, R = 300e-6, 15e-3
    kp = 2 * math.pi * 500 * L
    return P.CurrentLoop(L, R, kp, kp * R / L, kw.pop("gain_mapping", "continuous"), 10e3, "t", **kw)


TC = P.TimingConfig(25e-6, 5e-6, 1, 0.5, 1.5e-6, "t")


def test_transition_transient_exposes_gain_state_jumps_and_resets():
    i_ref, e = -150.0, 180.0
    v_ss = 15e-3 * i_ref + e
    bumpless = P.transition_transient(_loop(), TC, 10e3, 20e3, i_ref, e, 346.0)
    assert bumpless["bumpless"] and bumpless["excursion_A"] == 0.0 and bumpless["Ki_eff_ratio"] == 1.0
    es = P.transition_transient(_loop(integrator_storage="error_sum"), TC, 10e3, 20e3, i_ref, e, 346.0)
    assert es["output_jump_V"] == pytest.approx((0.5 - 1.0) * v_ss, rel=1e-12)       # (Ts_to / Ts_from - 1) v_ss
    rs = P.transition_transient(_loop(on_transition="reset"), TC, 10e3, 20e3, i_ref, e, 346.0)
    assert rs["output_jump_V"] == pytest.approx(-v_ss, rel=1e-12) and rs["excursion_A"] > es["excursion_A"] > 50.0
    fx = P.transition_transient(_loop(gain_mapping="fixed_discrete"), TC, 10e3, 20e3, i_ref, e, 346.0)
    assert fx["bumpless"] and fx["Ki_eff_ratio"] == pytest.approx(2.0)                # dynamics change, no jump
    sat = P.transition_transient(_loop(integrator_storage="error_sum"), TC, 20e3, 10e3, -i_ref, e, 1.2 * (e - 15e-3 * i_ref))
    assert sat["saturated_samples"] > 0 and sat["output_jump_V"] == pytest.approx(0.2 * (e - 15e-3 * i_ref), rel=1e-9)
    late = P.transition_transient(_loop(), P.TimingConfig(80e-6, 0.0, 1, 0.5, 0.0, "t"), 10e3, 20e3, i_ref, e, 346.0)
    assert late["evaluated"] is False and "deadline" in late["reason"]


def test_threshold_chatter_needs_hysteresis_above_the_measurement_noise():
    sch = P.FswSchedule("s", (P.FswRule("light", 8e3, torque_abs_Nm=(0.0, 60.0)), P.FswRule("d", 10e3)), 10e3,
                        hysteresis=(("torque_abs_Nm", 2.0),))
    assert P.chatter_risk(sch, {"torque_abs_Nm": 5.0})["violations"]
    assert not P.chatter_risk(sch, {"torque_abs_Nm": 1.0})["violations"]
    assert P.chatter_risk(sch, None)["unknown"]


def test_policy_evaluation_reports_the_failure_cases_and_keeps_unknown_apart_from_violation():
    r = api.pwm_policies({})
    for p in r["policies"]:
        for s in p["segments"]:
            # 16 kHz at 733 Hz (ratio 21.8): the carrier at 22 x f_e samples in valid windows, the one at 21 x f_e does
            # not - the verdict depends on the synchronous approximation and stays open (review of 63a2b61, 3.3)
            split = [u for u in s["sampling"]["unknown"] if "depends on the synchronous-carrier approximation" in u]
            assert s["sampling"]["status"] == "OK" or (split and s["sampling"]["status"] == "UNKNOWN"
                                                       and len(s["fsw_waveform_brackets_Hz"]) == 2)
        for e in p["transitions"]:
            if e["carrier_change"]:
                assert e["transient"]["evaluated"] and e["transient"]["bumpless"]
    assert any(p["chatter"]["rows"] for p in r["policies"][1:])
    # an error-sum integrator under Ki*Ts remapping: every carrier change becomes a transition violation
    lp = {**api.EXAMPLE_PWM["loop"], "integrator_storage": "error_sum"}
    r2 = api.pwm_policies({"loop": lp})
    sched = [p for p in r2["policies"][1:]]
    assert all(p["status"] == "VIOLATION" and any("transition" in v for v in p["violations"]) for p in sched)
    assert r2["policies"][0]["status"] == "ADMISSIBLE"                     # the fixed baseline has no transition
    # a single DC-link shunt fails at the low-modulation segment
    sn = {**api.EXAMPLE_PWM["sensing"], "kind": "dc_link_shunt"}
    r3 = api.pwm_policies({"sensing": sn})
    assert all(any("current sampling" in v for v in p["violations"]) for p in r3["policies"])
    # undeclared DC limits: the requirement is not established -> UNKNOWN, never a violation
    none = {k: None for k in ("discharge_power_max_W", "charge_power_max_W", "discharge_current_max_A",
                              "charge_current_max_A")}
    r4 = {p["policy"]["name"]: p for p in api.pwm_policies({"limits": none})["policies"]}
    for name in ("fixed 10 kHz", "light-load 8 kHz"):
        assert r4[name]["status"] == "UNKNOWN" and not r4[name]["violations"]
        assert any("requirement not established" in u for u in r4[name]["unverified"])
    # a violation proven independently of the DC limits (phase margin at 6 kHz) stays a violation
    assert r4["thermal fallback 6 kHz"]["status"] == "VIOLATION"


def test_transients_route():
    t = api.pwm_transients({})
    assert t["sampling_here"]["status"] == "OK"
    c = t["sampling_curves"]
    assert c["dc_link_shunt"]["valid_fraction"][0] == 0.0 and min(c["leg_shunt"]["valid_fraction"]) > 0.9
    v = t["transition"]["variants"]
    assert v["bumpless (volts, Ki*Ts remapped)"]["excursion_A"] == 0.0
    assert v["integrator reset"]["excursion_A"] > v["error-sum integrator, Ki*Ts remapped"]["excursion_A"] > 0


def test_one_pulse_pattern_for_losses_ripple_and_sampling():
    """The module data's declared modulation is replaced by the policy's (review finding: the page's SPWM choice
    reached the ripple and sampling but not the losses)."""
    r = api.pwm_policies({"modulation": "spwm"})
    assert r["module_modulation"] == {**r["module_modulation"], "declared": "svpwm", "used": "spwm"}
    base = api.pwm_policies({})
    assert r["policies"][0]["E_inv_J"] != base["policies"][0]["E_inv_J"]         # the losses follow the pattern
    # DPWM1 runs through every model with the same pattern (engineering review 2 of 63a2b61, P3: it used to be
    # rejected by the edge models only): a leg rests a third of the period, so the switching loss energy drops
    d1 = api.pwm_policies({"modulation": "dpwm1"})
    assert d1["module_modulation"]["used"] == "dpwm1"
    assert d1["policies"][0]["E_inv_J"] < base["policies"][0]["E_inv_J"]
    with pytest.raises(InputValidationError):
        api.pwm_policies({"modulation": "dpwm2"})                                 # not a declared family


def test_loop_margin_is_checked_on_both_machine_axes():
    """A single 'mean' L applies the same gains to both axes: the d axis (smaller L) crosses over higher and binds
    (review finding); per-axis design on the machine's inductances keeps both axes at the designed margin."""
    single = {**api.EXAMPLE_PWM["loop"], "L_uH": 300.0, "Ld_uH": None, "Lq_uH": None}
    r = {p["policy"]["name"]: p for p in api.pwm_policies({"loop": single})["policies"]}
    seg = r["light-load 8 kHz"]["segments"][1]
    assert seg["timing"]["binding_axis"] == "d" and seg["timing"]["phase_margin_deg"] < 45.0
    assert r["light-load 8 kHz"]["status"] == "VIOLATION"
    per_axis = {p["policy"]["name"]: p for p in api.pwm_policies({})["policies"]}
    ax = per_axis["light-load 8 kHz"]["segments"][1]["timing"]["axes"]
    # identical in continuous time; the sampled loop's pole-zero cancellation is only approximate (a ~0.1 deg
    # discretisation difference between the axes with different R / L)
    assert ax["d"]["phase_margin_deg"] == pytest.approx(ax["q"]["phase_margin_deg"], abs=0.3)
    assert per_axis["light-load 8 kHz"]["status"] == "ADMISSIBLE"
