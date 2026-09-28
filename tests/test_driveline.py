"""Torque shaping and active driveline damping (variable-PWM / anti-jerk addendum, P1-DAMP).

Fixtures D-01..D-05 of the addendum are checked against independent references: an ODE of the SAME physics written
in the output (wheel-side) coordinates and integrated with scipy's solve_ivp, the eigenvalues of the continuous
closed loop, the analytic clipping integral, and a residual check of the delay root.
"""

import cmath
import math

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from traction_workbench import api
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions import driveline as D

D01 = D.Driveline(0.2, 2.0, 3000.0, 2.0, 1.0, basis="D-01 synthetic")


def test_D01_modal_values():
    md = D01.modal()
    assert md["f_n_Hz"] == pytest.approx(20.4438226, abs=5e-8)
    assert md["zeta"] == pytest.approx(0.04281744, abs=5e-9)


def test_D01_D05_exact_simulation_matches_an_independent_output_coordinate_ode():
    """D-05: g = 9 with J_out = 162, k_out = 243000, c_out = 162 is the same physics as D-01 in motor coordinates."""
    g = 9.0
    dl9 = D.Driveline(0.2, 162.0, 243000.0, 162.0, g, basis="D-05 synthetic")
    assert dl9.referred() == pytest.approx({"Jm": 0.2, "Jl": 2.0, "k": 3000.0, "c": 2.0, "g": 9.0})
    ctl = D.Controller(1e-3, 0.0)
    man = D.Maneuver(0.0, 100.0, 0.0, 0.6, 0.0, 0.0, output_dt_s=1e-3)
    sim = D.simulate(dl9, ctl, man)
    # independent: output coordinates, states (w_m, w_w, delta_out = theta_m / g - theta_w), step applied at t = 0

    def f(t, y):
        wm, ww, dd = y
        Ts_out = 243000.0 * dd + 162.0 * (wm / g - ww)
        return [(100.0 - Ts_out / g) / 0.2, Ts_out / 162.0, wm / g - ww]
    ref = solve_ivp(f, (0.0, 0.6), [0.0, 0.0, 0.0], t_eval=sim["t_s"], rtol=1e-11, atol=1e-13, method="DOP853")
    assert np.max(np.abs(ref.y[0] - sim["omega_m"])) < 1e-6 * np.max(np.abs(sim["omega_m"]))
    assert np.max(np.abs(ref.y[1] - sim["omega_l"] / g)) < 1e-6 * np.max(np.abs(sim["omega_l"] / g))
    Ts_out = 243000.0 * ref.y[2] + 162.0 * (ref.y[0] / g - ref.y[1])
    assert np.max(np.abs(Ts_out - g * sim["T_shaft"])) < 1e-6 * np.max(np.abs(Ts_out))
    # energy invariant E' = T_act w_m - T_L w_l - c delta'^2 (quadrature of the dense output)
    assert D.energy_residual(sim) < 1e-4


def test_D02_motor_only_feedback_damping_and_power_terms():
    d = D.undelayed_damping(D01, 5.0)
    assert d["zeta"] == pytest.approx(0.14012981, abs=5e-9)
    # independent: eigenvalues of the continuous closed loop with T_act = -5 (w_m - w_l)
    r = D01.referred()
    Jm, Jl, k, c, Kd = r["Jm"], r["Jl"], r["k"], r["c"], 5.0
    A = np.array([[-(c + Kd) / Jm, (c + Kd) / Jm, -k / Jm], [c / Jl, -c / Jl, k / Jl], [1.0, -1.0, 0.0]])
    ev = [e for e in np.linalg.eigvals(A) if abs(e.imag) > 1e-9]
    assert -ev[0].real / abs(ev[0]) == pytest.approx(0.14012981, abs=5e-9)
    assert D01.modal()["alpha"] == pytest.approx(2.0 / 2.2)
    assert "T_ad * omega_m" in d["total_power_term"]
    # the sampled loop converges to the continuous value as the period shrinks (no delay)
    fine = D.sampled_eigenvalues(D01, D.Controller(2e-5, 0.0, damping=D.Damping("relative_speed", 5.0)))
    assert fine["stable"] and fine["dominant_zeta"] == pytest.approx(0.14012981, abs=5e-4)


def test_D03_delay_crossing_and_the_rhp_root():
    co = D.relative_mode_coefficients(D01, 5.0)
    assert (co["a1"], co["a0"], co["b1"]) == pytest.approx((11.0, 16500.0, 25.0))
    cr = D.delay_crossings(co["a1"], co["a0"], co["b1"], 0.05)
    first = cr[0]
    assert first["direction"] == "into RHP"
    assert first["tau_s"] == pytest.approx(14.45702348e-3, abs=5e-11)
    assert first["omega_rad_s"] == pytest.approx(140.1668195, abs=5e-7)
    r = D.rhp_roots_at(co["a1"], co["a0"], co["b1"], 0.016)
    assert r["n_rhp"] == 2 and not r["stable"]
    s = complex(r["rightmost_root"]["re"], r["rightmost_root"]["im"])
    assert s.real == pytest.approx(1.92230146, abs=5e-8) and s.imag == pytest.approx(138.49047418, abs=5e-8)
    assert abs(s * s + 11 * s + 16500 + 25 * s * cmath.exp(-s * 0.016)) < 1e-8          # original characteristic
    assert D.rhp_roots_at(co["a1"], co["a0"], co["b1"], 0.014)["stable"]
    # the sampled loop with a small period agrees on both sides of the crossing
    ok = D.sampled_eigenvalues(D01, D.Controller(1e-4, 0.014, damping=D.Damping("relative_speed", 5.0)))
    bad = D.sampled_eigenvalues(D01, D.Controller(1e-4, 0.016, damping=D.Damping("relative_speed", 5.0)))
    assert ok["stable"] and not bad["stable"]


def test_D04_clipping_makes_the_correction_non_zero_mean():
    r = D.clip_bias(195.0, 20.0, (-120.0, 200.0))
    a = math.asin(0.25)
    exact = -(40.0 * math.cos(a) - 5.0 * (math.pi - 2 * a)) / (2 * math.pi)
    assert exact == pytest.approx(-4.06619756, abs=5e-9)
    assert r["mean_correction_Nm"] == pytest.approx(exact, abs=1e-6)


def test_fractional_delay_is_not_rounded():
    a = D.sampled_eigenvalues(D01, D.Controller(1e-3, 2.4e-3, damping=D.Damping("relative_speed", 5.0)))
    b = D.sampled_eigenvalues(D01, D.Controller(1e-3, 2.0e-3, damping=D.Damping("relative_speed", 5.0)))
    assert a["delay_samples"] == 2 and a["delay_fraction"] == pytest.approx(0.4)
    assert a["dominant_zeta"] != pytest.approx(b["dominant_zeta"], abs=1e-6)


def test_backlash_transition_is_unknown_and_instability_is_a_policy_failure():
    bl = D.Driveline(0.2, 2.0, 3000.0, 2.0, 1.0, contact="backlash", basis="t")
    man = D.Maneuver(40.0, -40.0, 0.02, 0.4, 1000.0)
    req = {"t_to_90_max_s": 1.0, "peak_jerk_max": 1e9, "settle_max_s": 1.0}
    r = D.evaluate_variants(bl, {"off": D.Controller(1e-3, 1e-3)}, man, req)["variants"]["off"]
    assert r["status"] == "UNKNOWN" and "backlash" in r["reasons"][0]
    unstable = D.Controller(1e-3, 0.024, damping=D.Damping("relative_speed", 5.0))
    r2 = D.evaluate_variants(D01, {"fb": unstable}, D.Maneuver(0.0, 50.0, 0.02, 0.5, 1000.0), req)["variants"]["fb"]
    assert r2["status"] == "INFEASIBLE" and "THIS policy" in r2["reasons"][0]
    r3 = D.evaluate_variants(D01, {"off": D.Controller(1e-3, 1e-3)}, D.Maneuver(0.0, 50.0, 0.02, 0.5, 1000.0), {})
    assert r3["variants"]["off"]["status"] == "UNKNOWN"                        # no requirement: never a pass


def test_emergency_reduction_bypasses_the_comfort_shaper_and_the_damping():
    ctl = D.Controller(1e-3, 1e-3, shaper=D.Shaper("rate", rate_Nm_per_s=200.0),
                       damping=D.Damping("relative_speed", 2.0))
    man = D.Maneuver(100.0, 100.0, 0.0, 0.2, 1000.0, emergency_t_s=0.05, emergency_T_Nm=0.0)
    sim = D.simulate(D01, ctl, man)
    rec = sim["record"]
    after = rec["t_s"] >= 0.05
    assert np.all(rec["T_cmd"][after] == 0.0) and np.all(rec["T_ad"][after] == 0.0)
    # the actual torque follows after the declared delay, not after a comfort ramp
    t = sim["t_s"]
    assert np.all(np.abs(sim["T_act"][t >= 0.05 + 1e-3 + 1e-9]) < 1e-12)


def test_clipping_after_arbitration_is_reported():
    ctl = D.Controller(1e-3, 1e-3, damping=D.Damping("relative_speed", 3.0))
    man = D.Maneuver(0.0, 95.0, 0.01, 0.5, 1000.0, window_Nm=(-20.0, 100.0))
    sim = D.simulate(D01, ctl, man)
    assert np.all(sim["record"]["T_cmd"] <= 100.0 + 1e-12)
    met = D.response_metrics(sim, man)
    assert met["clipped_fraction"] > 0 and abs(met["correction_mean_Nm"]) > 0


def test_api_example_compares_the_four_variants_on_the_same_maneuver():
    r = api.driveline({})
    v = r["variants"]
    assert set(v) == {"off", "shaping", "feedback", "combined"}
    assert v["off"]["metrics"]["peak_vehicle_jerk_m_s3"] > v["shaping"]["metrics"]["peak_vehicle_jerk_m_s3"]
    assert v["shaping"]["metrics"]["t_to_90_s"] > v["off"]["metrics"]["t_to_90_s"]      # the cost of shaping
    # measured on the REQUESTED target (review R2 CT-03): the speed feedback leaves a steady torque deficit, so it
    # does not settle on the request - a lower delivered response is never credited as a jerk improvement
    assert v["feedback"]["metrics"]["achieved_fraction"] < 0.95 and v["feedback"]["metrics"]["t_settle_s"] is None
    assert any("settling time" in x for x in v["feedback"]["reasons"])
    assert v["off"]["metrics"]["achieved_fraction"] == pytest.approx(1.0, abs=5e-3)
    assert v["feedback"]["stability"]["stable"]
    assert "not an efficiency gain" in v["shaping"]["loss_note"]
    assert r["window"]["source"].startswith("policy capability")
    s = api.driveline_stability({})
    assert any(not x["stable"] for row in s["grid"] for x in row) and any(x["stable"] for row in s["grid"] for x in row)
    with pytest.raises(InputValidationError):
        D.Driveline(0.2, 2.0, 3000.0, 2.0, 1.0, basis="")


# ------------------------------------------------------------------ addendum 7.2 mandatory failure cases (damping side)

REQ = {"t_to_90_max_s": 1.0, "peak_jerk_max": 1e9, "settle_max_s": 1.0}


def test_load_speed_skew_error_equals_the_load_speed_change_over_the_skew():
    """With Kd = 0 the feedback does not act, so the plant is untouched and the measured relative-speed error must be
    w_l(t_k) - w_l(t_k - skew) exactly (independent reference: the dense plant output, interpolated)."""
    skew = 3.3e-3
    ctl = D.Controller(1e-3, 1e-3, damping=D.Damping("relative_speed", 0.0, load_speed_skew_s=skew))
    man = D.Maneuver(0.0, 80.0, 0.02, 0.3, 1000.0, output_dt_s=1e-5)
    sim = D.simulate(D01, ctl, man)
    rec = sim["record"]
    t, wl = sim["t_s"], sim["omega_l"]
    k = rec["t_s"] >= skew
    ref = np.interp(rec["t_s"][k], t, wl) - np.interp(rec["t_s"][k] - skew, t, wl)
    err = rec["meas_error_rad_s"][k]
    assert np.max(np.abs(err - ref)) < 1e-6 * max(1.0, np.max(np.abs(ref)))
    assert np.max(np.abs(ref)) > 1e-3                                  # a real, non-zero false relative speed


def test_signal_dropout_without_a_fallback_is_unknown_and_with_one_the_damping_fades():
    man = D.Maneuver(0.0, 80.0, 0.02, 0.4, 1000.0, window_Nm=(-500.0, 500.0))
    stale = D.Controller(1e-3, 1e-3, damping=D.Damping("relative_speed", 2.0, dropouts=((0.1, 0.2),)))
    r = D.evaluate_variants(D01, {"fb": stale}, man, REQ)["variants"]["fb"]
    assert r["status"] == "UNKNOWN" and any("stale" in x for x in r["reasons"])
    assert r["sensing"]["stale_s"] == pytest.approx(0.1, abs=1.5e-3) and r["sensing"]["min_gain"] == 1.0
    fb = D.Controller(1e-3, 1e-3, damping=D.Damping("relative_speed", 2.0, dropouts=((0.1, 0.2),), stale_limit_s=0.02,
                                                     fade_s=0.01))
    r2 = D.evaluate_variants(D01, {"fb": fb}, man, REQ)["variants"]["fb"]
    assert not any("stale" in x for x in r2["reasons"]) and r2["sensing"]["min_gain"] == 0.0
    # age passes 20 ms at t = 0.12 s, fade-out 10 ms, the signal returns at 0.2 s, fade-in 10 ms
    assert r2["sensing"]["unavailable_s"] == pytest.approx(0.09, abs=3e-3)
    assert any("unavailable" in n for n in r2["notes"])
    rec = D.simulate(D01, fb, man)["record"]
    assert np.all(rec["T_ad"][(rec["t_s"] > 0.131) & (rec["t_s"] < 0.2)] == 0.0)


def test_one_sided_clipping_is_reported_as_an_exhausted_negative_reserve():
    ctl = D.Controller(1e-3, 1e-3, damping=D.Damping("relative_speed", 4.0))
    man = D.Maneuver(30.0, 0.0, 0.01, 0.4, 1000.0, window_Nm=(-2.0, 200.0))       # tip-out, full battery
    r = D.evaluate_variants(D01, {"fb": ctl}, man, REQ)["variants"]["fb"]
    assert r["clipping"]["lower_s"] > 0 and r["clipping"]["upper_s"] == 0
    assert any("regen" in n for n in r["notes"])


def test_an_undeclared_authority_window_is_not_a_pass():
    r = D.evaluate_variants(D01, {"off": D.Controller(1e-3, 1e-3)}, D.Maneuver(0.0, 50.0, 0.02, 0.5, 1000.0),
                            REQ)["variants"]["off"]
    assert r["status"] == "UNKNOWN" and any("authority window not declared" in x for x in r["reasons"])


def test_safety_reaction_is_judged_on_its_own():
    ctl = D.Controller(1e-3, 1e-3, shaper=D.Shaper("rate", rate_Nm_per_s=200.0))
    man = D.Maneuver(100.0, 100.0, 0.0, 0.3, 1000.0, window_Nm=(-500.0, 500.0), emergency_t_s=0.1, emergency_T_Nm=0.0)
    for lim, st in ((0.02, "FEASIBLE"), (5e-4, "INFEASIBLE"), (None, "UNKNOWN")):
        req = {**REQ, "safety_band_Nm": 2.0, **({} if lim is None else {"safety_reaction_max_s": lim})}
        r = D.evaluate_variants(D01, {"x": ctl}, man, req)["variants"]["x"]
        assert r["safety"]["status"] == st
        assert r["safety"]["reaction_s"] == pytest.approx(1e-3, abs=6e-4)     # the declared delay, not the ramp
        assert any("reported separately" in n for n in r["notes"])


def test_electrical_slew_limit_matches_the_constant_model_and_binds_at_high_speed():
    from traction_workbench import service as S
    d = S.resolve_drive(None)
    f = d.motor.flux
    lim = api._limits({})
    s = D.electrical_slew_limits(d, 2000.0, 600.0, lim, 150.0)
    from traction_workbench.scenario import Scenario
    from traction_workbench.solvers.policy import PolicyEvaluator
    pt = PolicyEvaluator(d, Scenario("t", 2000.0, 600.0, lim)).solve(150.0).point
    kt = 1.5 * d.motor.pole_pairs * (f.psi_pm_Wb + (f.Ld_H - f.Lq_H) * pt.id_A)
    assert s["kt_Nm_per_A"] == pytest.approx(kt, rel=1e-9) and s["Lq_diff_H"] == pytest.approx(f.Lq_H, rel=1e-9)
    up = math.sqrt(pt.voltage_ceiling_V ** 2 - pt.vd_V ** 2) - pt.vq_V
    assert s["pos_Nm_per_s"] == pytest.approx(kt * up / f.Lq_H, rel=1e-9)
    b = {"maneuver": {**api.EXAMPLE_DRIVELINE["maneuver"], "speed_rpm": 11000.0, "T1_Nm": 100.0},
         "controller": {**api.EXAMPLE_DRIVELINE["controller"], "actuator_tau_ms": 0.3}}
    r = api.driveline(b)
    assert r["slew_limits"]["pos_Nm_per_s"] < 0.2 * r["slew_limits"]["neg_Nm_per_s"]      # FW: little room upward
    assert r["variants"]["off"]["status"] == "UNKNOWN"
    assert any("voltage headroom" in x for x in r["variants"]["off"]["reasons"])
