"""Conducted EMI (handoff P1-C / section 11) and OEW common mode (addendum C-01, C-02).

Source, path and receiver are checked separately against independent references (V1-V2): the edge-sum spectrum
against an FFT of an independently reconstructed waveform, the nodal network against closed-form DM / CM
half-circuits, the receiver line sum against a single sinusoid.
"""

import math

import numpy as np
import pytest

from traction_workbench.errors import InputValidationError
from traction_workbench.extensions import emi as E


def src(**kw):
    base = dict(Vdc_V=600.0, I_pk_A=300.0, beta_rad=-0.3, m=0.8, alpha_rad=0.0, fe_Hz=400.0, fsw_Hz=10e3,
                t_rise_s=50e-9, t_fall_s=60e-9, t_dead_s=1e-6, modulation="svpwm", basis="test")
    base.update(kw)
    return E.SwitchingSource(**base)


def profile(**kw):
    fields = {"standard": "TEST", "edition": "1", "customer_revision": "A", "curve_id": "T-1", "port": "HV+/HV-",
              "method": "voltage_AN", "detector": "peak", "rbw_Hz": 9e3, "network": "AN 5uH/50ohm", "fixture": "bench",
              "operating_condition": "6000 rpm"}
    fields.update(kw.pop("fields", {}))
    lim = kw.pop("limit", E.LimitCurve(((150e3, 70.0), (30e6, 70.0)), "dBuV", "peak", "test curve"))
    return E.EmiProfile(fields, lim, kw.pop("reserve", 6.0))


def test_cm_dm_power_identity():
    rng = np.random.default_rng(3)
    vp, vm, ip, im = rng.normal(size=(4, 200))
    d = E.cm_dm(vp, vm, ip, im)
    assert np.allclose(vp * ip + vm * im, d["v_CM"] * d["i_CM"] + d["v_DM"] * d["i_DM"], atol=1e-12)


def test_edge_sum_spectrum_matches_an_independent_fft():
    s = src()
    w = E.sampled_waveforms(s, n_per_carrier=20000)
    n, T = w["t_s"].size, w["period_s"]
    harm = np.array([25, 50, 500, 2500])                       # fsw, 2 fsw, 200 kHz, 1 MHz
    lines = E.source_lines(s, harm / T)
    ref_v = 2 * np.fft.rfft(w["v_cm_V"])[harm] / n * np.exp(-1j * np.pi * harm / n)
    ref_i = 2 * np.fft.rfft(w["i_inv_A"])[harm] / n * np.exp(-1j * np.pi * harm / n)
    assert np.allclose(np.abs(lines["v_cm"]), np.abs(ref_v), rtol=0.01)
    # DM current: steps at the edges (slow in-interval variation neglected) - accurate in the EMI band
    assert np.allclose(np.abs(lines["i_dm"][2:]), np.abs(ref_i[2:]), rtol=0.02)


def test_dead_time_shifts_edges_by_current_sign():
    a = E.pwm_edges(src(t_dead_s=0.0))["edges"]
    b = E.pwm_edges(src(t_dead_s=1e-6))["edges"]
    for (k1, t1, s1, _, i1), (k2, t2, s2, _, i2) in zip(a, b):
        expect = 1e-6 if ((s1 > 0 and i1 > 0) or (s1 < 0 and i1 < 0)) else 0.0
        assert t2 - t1 == pytest.approx(expect, abs=1e-15)


def test_network_matches_closed_form_dm_and_cm_half_circuits():
    f = np.array([2e5, 1e6, 5e6])
    w = 2 * np.pi * f
    one = np.ones(3, dtype=complex)
    Rm = 1 / (1 / 50 + 1 / 1000)
    Zmb = 1 / (1j * w * 0.1e-6) + Rm
    Zsup = 1 / (1j * w * 1e-6)
    net = E.HvNetwork(C_dc_F=500e-6, ESR_dc_ohm=1e-3, ESL_dc_H=20e-9, R_h_ohm=5e-3, L_h_H=1e-6, basis="test")
    dm = E.solve_network(net, f, 0 * one, one)
    Zdc = 1e-3 + 1j * w * 20e-9 + 1 / (1j * w * 500e-6)
    Zan = 1 / (1 / Zmb + 1 / (1j * w * 5e-6 + 1 / (1 / Zsup + 1 / (0.01 / 2))))
    V5 = Zdc / (Zdc + 2 * (5e-3 + 1j * w * 1e-6) + 2 * Zan) * Zan * Rm / Zmb
    assert np.allclose(np.abs(dm["v_meas_plus"]), np.abs(V5), rtol=1e-9)
    assert np.allclose(dm["v_meas_plus"], -dm["v_meas_minus"], atol=1e-12)          # pure DM
    net2 = E.HvNetwork(C_dc_F=1.0, C_par_F=200e-12, R_h_ohm=5e-3, L_h_H=1e-6, basis="test")
    cm = E.solve_network(net2, f, one, 0 * one)
    Zan_cm = 1 / (1 / Zmb + 1 / (1j * w * 5e-6 + Zsup))
    V5c = 1 / (1 / (1j * w * 200e-12) + (5e-3 + 1j * w * 1e-6 + Zan_cm) / 2) / 2 * Zan_cm * Rm / Zmb
    assert np.allclose(np.abs(cm["v_meas_plus"]), np.abs(V5c), rtol=1e-9)
    assert np.allclose(cm["v_meas_plus"], cm["v_meas_minus"], atol=1e-12)            # pure CM


def test_cm_choke_attenuates_common_mode_only():
    f = np.array([1e6])
    one = np.ones(1, dtype=complex)
    base = dict(C_dc_F=500e-6, C_par_F=2e-9, R_h_ohm=5e-3, L_h_H=1e-6, basis="test")
    no = E.solve_network(E.HvNetwork(**base), f, one, 0 * one)
    ch = E.solve_network(E.HvNetwork(**base, L_ch_H=200e-6, k_ch=0.995), f, one, 0 * one)
    assert abs(ch["v_meas_plus"][0]) < 0.1 * abs(no["v_meas_plus"][0])
    dm_no = E.solve_network(E.HvNetwork(**base), f, 0 * one, one)
    dm_ch = E.solve_network(E.HvNetwork(**base, L_ch_H=200e-6, k_ch=0.995), f, 0 * one, one)
    # only the leakage (1 - k) L acts on DM: far smaller effect than on CM
    assert abs(dm_ch["v_meas_plus"][0]) > 0.3 * abs(dm_no["v_meas_plus"][0])


def test_receiver_line_sum_of_a_single_line_is_its_rms():
    fe = 1000.0
    est, n = E.line_sum_estimate([500e3], 9e3, fe, lambda fr: np.where(np.isclose(fr, 500e3), 2.0 + 0j, 0.0))
    assert est[0] == pytest.approx(2.0 / math.sqrt(2.0))
    assert n == 9                                            # lines inside +-4.5 kHz


def test_limit_curve_log_interpolation_steps_and_no_extrapolation():
    c = E.LimitCurve(((150e3, 80.0), (1e6, 60.0), (1e6, 50.0), (30e6, 50.0)), "dBuV", "peak", "test")
    assert c.at(150e3)[0] == pytest.approx(80.0)
    assert c.at(math.sqrt(150e3 * 1e6))[0] == pytest.approx(70.0)                    # log-f midpoint
    assert c.at(1e6)[0] == pytest.approx(50.0)                                        # step: the stricter value
    assert np.isnan(c.at(100e3)[0]) and np.isnan(c.at(40e6)[0])
    with pytest.raises(InputValidationError):
        E.LimitCurve(((150e3, 80.0), (30e6, 50.0)), "dBuV", "peak", "")               # needs its source


def test_incomplete_profile_withholds_the_verdict():
    net = E.HvNetwork(C_dc_F=500e-6, C_y_F=100e-9, C_par_F=2e-9, L_h_H=1e-6, basis="test")
    r = E.conducted_emission_screening(src(), net, profile(fields={"standard": ""}), n_grid=20)
    assert r["claim"]["status"] == "UNKNOWN" and r["claim"]["reasons"] == ["REQUIREMENT_INCOMPLETE"]
    assert "standard" in r["profile_missing"]
    assert np.all(np.asarray(r["required_attenuation_dB"]) >= 0)                    # the need is still reported


def record(res, **kw):
    """A complete calibration record bound to the configuration of ``res`` (review R2, EMC-01)."""
    rec = {"evidence": "holdout correlation R-1", "holdout": "6 held-out operating points, 2 harnesses",
           "acquisition": "peak detector, 9 kHz RBW, 10 ms dwell, stepped scan", "error_model":
           "max |E_meas - E_model| over the hold-out, per receiver frequency", "uncertainty_dB": 3.0,
           "f_intervals_Hz": [[150e3, 30e6]], **res["configuration"]}
    rec.update(kw)
    return rec


def test_screening_is_never_a_pass_calibration_is_declared():
    net = E.HvNetwork(C_dc_F=500e-6, C_y_F=100e-9, C_par_F=2e-9, L_h_H=1e-6, basis="test")
    lenient = profile(limit=E.LimitCurve(((150e3, 200.0), (30e6, 200.0)), "dBuV", "peak", "test"))
    scr = E.conducted_emission_screening(src(), net, lenient, n_grid=20)
    assert scr["claim"]["status"] == "UNKNOWN" and "SCREENING_ONLY" in scr["claim"]["reasons"]
    # a title string is not calibration evidence: the record is incomplete, the result stays a screening
    title = E.conducted_emission_screening(src(), net, lenient, n_grid=20,
                                           calibration={"evidence": "holdout correlation R-1", "uncertainty_dB": 3.0})
    assert title["claim"]["status"] == "UNKNOWN" and "MISSING_INPUT" in title["claim"]["reasons"]
    cal = E.conducted_emission_screening(src(), net, lenient, n_grid=20, calibration=record(scr))
    assert cal["claim"]["status"] == "FEASIBLE" and cal["calibrated"]
    strict = profile(limit=E.LimitCurve(((150e3, 20.0), (30e6, 20.0)), "dBuV", "peak", "test"))
    bad = E.conducted_emission_screening(src(), net, strict, n_grid=20, calibration=record(scr))
    assert bad["claim"]["status"] == "INFEASIBLE"
    w = bad["claim"]["evidence"][0]["data"]                                 # the LOWER bound exceeds: a witness
    assert w["E_lower_dBuV"] > w["limit_minus_reserve_dBuV"]
    # the required attenuation follows A = max(0, E_U + M_d - L)
    A = np.asarray(bad["required_attenuation_dB"])
    assert np.allclose(A, np.maximum(0, np.asarray(bad["E_upper_dBuV"]) + 6.0 - 20.0))


def meta(**kw):
    m = {"representation": "raw_sweep", "detector": "peak", "unit": "dBuV", "rbw_Hz": 9e3, "if_shape": "gaussian",
         "dwell_s": 0.01, "corrections": "AN factor and cable loss applied (cal file C-12)", "port": "HV+/HV-", "method": "voltage_AN", "network": "AN 5uH/50ohm", "fixture": "bench",
         "operating_condition": "6000 rpm"}
    m.update(kw)
    return m


def test_measured_trace_verdicts():
    p = profile()
    f = np.arange(150e3, 30e6 + 1, 4.5e3)                   # raw sweep, step = RBW / 2: <= 1.5 dB between readings
    ok = E.measured_trace_verdict(f, np.full(f.size, 50.0), p, 3.0, meta=meta())
    assert ok["verdict"] == "PASS" and ok["coverage"]["allowance_max_dB"] == pytest.approx(6.0206 / 4)
    fail = E.measured_trace_verdict(f, np.where(f > 1e6, 75.0, 50.0), p, 3.0, meta=meta())
    assert fail["verdict"] == "FAIL"
    grey = E.measured_trace_verdict(f, np.full(f.size, 62.0), p, 3.0, meta=meta())   # 62 + 3 + 1.5 > 70 - 6
    assert grey["verdict"] == "INDETERMINATE"
    partial = E.measured_trace_verdict(f[f < 5e6], np.full(int(np.sum(f < 5e6)), 50.0), p, 3.0, meta=meta())
    assert partial["verdict"] == "INDETERMINATE"                              # band not covered
    floor = E.measured_trace_verdict(f, np.full(f.size, 50.0), p, 3.0, noise_floor=np.full(f.size, 66.0), meta=meta())
    assert floor["verdict"] == "INDETERMINATE"
    # 400 log-spaced readings leave up to ~400 kHz between readings at 9 kHz RBW: coverage is not established
    sparse = np.geomspace(150e3, 30e6, 400)
    assert E.measured_trace_verdict(sparse, np.full(sparse.size, 50.0), p, 3.0, meta=meta())["verdict"] == "INDETERMINATE"


def test_coupling_checks():
    net = E.HvNetwork(C_dc_F=500e-6, C_y_F=100e-9, C_par_F=2e-9, L_h_H=1e-6, R_h_ohm=5e-3, basis="test")
    c = E.coupling_checks(net, 600.0, src(), E_y_allowed_J=0.01)
    assert c["y_capacitor"]["energy_per_rail_at_Vdc_J"] == pytest.approx(0.5 * 100e-9 * 600 ** 2)
    assert c["y_capacitor"]["status"] == "INFEASIBLE"
    assert c["dm_resonance"]["f_res_Hz"] == pytest.approx(1 / (2 * math.pi * math.sqrt(2e-6 * 500e-6)))
    assert c["common_mode"]["v_cm_step_V"] == pytest.approx(200.0)


def test_c02_zero_sequence_free_pairs_still_move_the_chassis_common_mode():
    z = E.zsv_free_sequence_example(400.0)
    assert z["u0_max_V"] == 0.0
    steps = z["v_cm6_steps_V"]
    assert np.allclose(np.diff(steps), 400.0 / 3.0)


def test_c01_c02_carrier_phase_trades_winding_u0_against_chassis_cm():
    a = E.oew_common_mode(400.0, 300.0, 0.4, 400.0, 10e3, 50e-9, 0.5, 0.0, None, 40, i_pk_A=300.0, beta_rad=0.2)
    b = E.oew_common_mode(400.0, 300.0, 0.4, 400.0, 10e3, 50e-9, 0.5, 0.5, None, 40, i_pk_A=300.0, beta_rad=0.2)
    assert a["u0_rms_V"] < b["u0_rms_V"] and a["cm6_rms_V"] > b["cm6_rms_V"]
    for r in (a, b):
        assert r["dc_currents"]["identity_residual"] < 1e-6          # S_sum = S_AA + S_BB + 2 Re S_AB
