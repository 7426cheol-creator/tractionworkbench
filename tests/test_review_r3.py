"""Third engineering review (ENGINEERING_REVIEW_inv_app.md, the audit of every remaining area by numerical
experiment): regression tests of the findings new in that review - F-10, F-11, F-22, F-23, F-24 and the P3 items
(OEW off/off threshold, driveline final level, A/B error band, EMI reserve and gap wording).  A21-A25 are the
review's adversarial cases.  Values are synthetic; they check equations, semantics and identity, not hardware."""

import copy
import math
import re

import numpy as np
import pytest

from conftest import emi_rail_lines, envelope_brute
from traction_workbench import api
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions import emi as E

SQ2 = math.sqrt(2.0)


def _dbuv(v_peak: float) -> float:
    return 20.0 * math.log10(v_peak / SQ2 / 1e-6)


# ---------------------------------------------------------------------------------------- F-10 PROT-04 (A21)

def _prot04(horizon_ms: float, delay_ms: float = 2.0):
    b = copy.deepcopy(api.EXAMPLE_PROTECTION)
    b["action_delay_ms"], b["horizon_ms"] = delay_ms, horizon_ms
    r = api.protection(b)
    return next(x for x in r["rows"] if x["id"] == "PROT-04"), r


def test_a21_prot04_is_decided_by_the_reaction_not_by_the_horizon():
    pl = api.EXAMPLE_PROTECTION["plant"]
    C, P0, x0 = pl["C_uF"] * 1e-6, pl["P0_kW"] * 1e3, pl["x0"]
    t_lim = (800.0 ** 2 - x0 ** 2) * C / (2.0 * P0)                   # closed form without the action: 0.375 ms
    # 0.1 ms: the threshold itself is not reached yet - nothing is decided (it was INFEASIBLE)
    short, _ = _prot04(0.1)
    assert short["status"] == "UNKNOWN" and "beyond the horizon" in short["detail"]
    # 0.3 ms: confirmed at 0.18 ms, the action only at 2.18 ms; the exact solution crosses 800 V at 0.375 ms before it
    # (it was FEASIBLE "peak 781 V < 800 V")
    mid, _ = _prot04(0.3)
    assert mid["status"] == "INFEASIBLE"
    t_cross = float(re.search(r"at ([0-9.eE+-]+) s, before the action", mid["detail"]).group(1))
    assert t_cross == pytest.approx(t_lim, abs=1e-9)
    # a horizon past the crossing observes the same instant: the verdict no longer depends on the horizon
    for h in (1.0, 5.0):
        row, r = _prot04(h)
        assert row["status"] == "INFEASIBLE" and "before any action took effect" in row["detail"]
        assert r["trace"]["events"]["t_limit_s"] == pytest.approx(t_lim, abs=1e-9)
    # the example itself (0.15 ms action delay) is unchanged: contained in every sampled phase
    ok = next(x for x in api.protection(api.EXAMPLE_PROTECTION)["rows"] if x["id"] == "PROT-04")
    assert ok["status"] == "FEASIBLE"


def test_a21_an_action_after_the_horizon_is_judged_on_the_exact_solution():
    from traction_workbench.extensions.protection import Plant, Sensor, protection_review
    pr = (("R_K_per_W", 0.2), ("C_J_per_K", 20.0), ("T_coolant_C", 60.0), ("P_W", 600.0), ("P_after_W", 350.0))
    plant = Plant("thermal_1node", 100.0, pr)          # T(t) = 180 - 80 exp(-t / 4 s) without the derating

    def p4(horizon_s, delay_s):
        r = protection_review(plant, Sensor(period_s=0.001), 110.0, 150.0, horizon_s, action_delay_s=delay_s,
                              phases=2)
        return next(x for x in r["rows"] if x["id"] == "PROT-04")
    # confirmed at 0.53 s, derated at 2.53 s (137.5 degC), then toward 130 degC: contained, inside or beyond the horizon
    beyond, inside = p4(1.0, 2.0), p4(5.0, 2.0)
    assert beyond["status"] == inside["status"] == "FEASIBLE"
    assert "after the horizon" in beyond["detail"] and "after the horizon" not in inside["detail"]
    # a 6 s delay: without the derating T reaches 150 degC at 4 ln(8/3) s, before the action - both horizons agree
    late_beyond, late_inside = p4(1.0, 6.0), p4(10.0, 6.0)
    assert late_beyond["status"] == late_inside["status"] == "INFEASIBLE"
    t_cross = float(re.search(r"at ([0-9.eE+-]+) s, before the action", late_beyond["detail"]).group(1))
    assert t_cross == pytest.approx(4.0 * math.log(8.0 / 3.0), abs=1e-5)
    # the threshold is reached only at 0.53 s: a 0.2 s horizon decides nothing (never INFEASIBLE)
    assert p4(0.2, 2.0)["status"] == "UNKNOWN"


def test_f10_the_reading_follows_the_prot04_verdict():
    from traction_workbench.insight.safety import protection_insight
    _, r = _prot04(0.3)
    ins = protection_insight(r)
    peak = next(m for m in ins.metrics if "peak" in m[0] or "최고" in m[0])
    assert peak[2] == "bad"                       # the in-horizon peak is below the limit, the verdict is not


# ---------------------------------------------------------------------------------------- F-11 receiver (A22)

def _src(fe: float):
    return E.SwitchingSource(600.0, 300.0, -0.3, 0.8, 0.0, fe, 10000.0, 50e-9, 60e-9, 1e-6, basis="review 3 synthetic")


def _net():
    return api._emi_network(api.EXAMPLE_EMI["network"])


def _profile(lo, hi, level=70.0, reserve=0.0, gaps=()):
    fields = dict(standard="review 3 fixture", edition="1", customer_revision="A", curve_id="r3", port="HV+/HV-",
                  method="voltage_AN", detector="peak", rbw_Hz=9000.0, network="AN 5uH/50ohm", fixture="bench",
                  operating_condition="synthetic")
    return E.EmiProfile(fields, E.LimitCurve(((lo, level), (hi, level)), "dBuV", "peak", "review 3 fixture",
                                             tuple(gaps)), reserve)


def test_envelope_peak_is_an_attained_maximum_never_above_the_line_sum():
    rng = np.random.default_rng(7)
    for d in (1, 2, 5, 23, 91):
        for trial in range(6):
            c = rng.normal(size=d) + 1j * rng.normal(size=d)
            if trial % 2:
                c = np.abs(c)                                          # coherent lines: the envelope is the line sum
            e = E.envelope_peak(c)
            assert e <= np.sum(np.abs(c)) * (1 + 1e-12)
            assert e >= envelope_brute(c) * (1 - 1e-12)
            assert e <= envelope_brute(c) * (1 + (math.pi * d / (1 << 16)) ** 2)
    assert E.envelope_peak(np.array([1.0, 1.0, 1.0])) == pytest.approx(3.0, rel=1e-12)


def test_a22_the_estimate_does_not_depend_on_the_fundamental_the_line_sum_does():
    lo, hi = 3.75e6, 3.85e6                       # the example network's 3.8 MHz DM resonance, same operating point
    rows = {fe: E.conducted_emission_screening(_src(fe), _net(), _profile(lo, hi), lo, hi, n_grid=8)["domain"][0]
            for fe in (400.0, 200.0, 100.0)}
    est = [r["E_est_sup_dBuV"] for r in rows.values()]
    assert max(est) - min(est) < 1.0                                    # the reading, not the number of lines
    gap = {fe: r["E_sup_dBuV"] - r["E_est_sup_dBuV"] for fe, r in rows.items()}
    assert gap[400.0] > 6.0 and gap[100.0] > gap[400.0] + 1.5           # the bound grows with the lines per window
    assert rows[400.0]["lines_per_window_max"] == 23 and rows[100.0]["lines_per_window_max"] == 91
    for r in rows.values():
        assert r["required_attenuation_est_dB"] < r["required_attenuation_bound_dB"] - 6.0
        assert r["E_est_sup_dBuV"] < r["E_sup_dBuV"]


def test_f11_the_estimate_equals_an_independent_dense_receiver_sweep():
    lo, hi, fe, h = 3.795e6, 3.803e6, 400.0, 4500.0
    src, net = _src(fe), _net()
    row = E.conducted_emission_screening(src, net, _profile(lo, hi), lo, hi, n_grid=4)["domain"][0]
    n = np.arange(math.ceil((lo - h) / fe), math.floor((hi + h) / fe) + 1)
    V = emi_rail_lines(E, src, net, n * fe)
    best, seen = 0.0, set()
    for x in np.arange(lo, hi + 1.0, fe / 8):
        sel = np.flatnonzero((n * fe >= x - h - 1e-6) & (n * fe <= x + h + 1e-6))
        if (sel[0], sel[-1]) in seen:
            continue
        seen.add((sel[0], sel[-1]))
        best = max(best, max(envelope_brute(V[m][q][sel]) for m in ("midpoint", "edge_sign") for q in (0, 1)))
    dense = _dbuv(best)
    assert dense - 1e-9 <= row["E_est_sup_dBuV"] <= dense + 1e-4


def test_f11_gaussian_if_cells_bound_every_tuning_inside_them():
    rng = np.random.default_rng(3)
    fe, rbw = 400.0, 9000.0
    mags = rng.random(400) * np.exp(rng.normal(size=400))
    G = E._gaussian_cells(mags, fe, rbw)
    f = (1000 + np.arange(400)) * fe
    for j in rng.integers(0, G.size, 60):
        t_j = 1000 * fe + j * fe / 2
        vals = [float(np.sum(mags * 2.0 ** (-(2.0 * (f - x) / rbw) ** 2))) for x in t_j + np.linspace(-fe / 4, fe / 4, 41)]
        assert max(vals) <= G[j]                                           # a bound over the whole cell
        assert G[j] <= 1.01 * max(vals)                                     # and a tight one (value, slope, curvature)


def test_f11_a_line_sum_exceedance_alone_is_never_a_witness():
    lo, hi = 3.75e6, 3.85e6
    src, net = _src(400.0), _net()
    ref = E.conducted_emission_screening(src, net, _profile(lo, hi), lo, hi, n_grid=8)
    row = ref["domain"][0]
    rec = {"evidence": "holdout correlation R-3", "holdout": "4 held-out points", "acquisition": "peak, 9 kHz",
           "error_model": "max |E_meas - E_model|", "uncertainty_dB": 1.0, "f_intervals_Hz": [[lo, hi]],
           **ref["configuration"]}
    # between the envelope estimate and the line sum: the bound exceeds, no model's reading does -> not INFEASIBLE
    level = 0.5 * (row["E_est_sup_dBuV"] + row["E_sup_dBuV"])
    r = E.conducted_emission_screening(src, net, _profile(lo, hi, level=level), lo, hi, n_grid=8, calibration=rec)
    assert r["claim"]["status"] == "UNKNOWN" and r["claim"]["reasons"] == ["UNCERTAINTY_OVERLAP"]
    assert r["domain"][0]["witness"] is None


# ---------------------------------------------------------------------------------------- F-23 CM return models

def test_f23_the_edge_sign_return_is_not_bracketed_by_the_static_rails():
    src, net = _src(400.0), _net()
    n = np.arange(math.ceil((3.8e6 - 4500) / 400.0), math.floor((3.8e6 + 4500) / 400.0) + 1)
    f = n * 400.0
    e = E.pwm_edges(src)
    cm_r, cm_f, dm = E.source_split(src, f, e)
    z = 0 * dm

    def plus(v, i, al):
        return E.solve_network(net, f, v, i, al)["v_meas_plus"]
    cm_only = {"midpoint": plus(cm_r + cm_f, z, 0.5), "edge_sign": plus(cm_r, z, 1.0) + plus(cm_f, z, 0.0),
               "hv_plus": plus(cm_r + cm_f, z, 1.0), "hv_minus": plus(cm_r + cm_f, z, 0.0)}
    ls = {k: float(np.sum(np.abs(v))) for k, v in cm_only.items()}
    assert ls["edge_sign"] > 30.0 * ls["midpoint"]                          # > 29 dB above the symmetric split
    assert ls["edge_sign"] > 1.25 * max(ls["hv_plus"], ls["hv_minus"])      # above both single-rail returns (+2.5 dB)
    # the midpoint split has no CM -> DM conversion in this symmetric network; the edge-sign split does
    mid = E.solve_network(net, f, cm_r + cm_f, z, 0.5)
    assert np.max(np.abs(mid["v_meas_plus"] - mid["v_meas_minus"])) < 1e-9 * np.max(np.abs(mid["v_meas_plus"]))
    # the engine's rail models (one Woodbury solve) equal the superposed direct solves
    H, res = E.port_transfers(net, f)
    V = E.rail_ports(H, cm_r, cm_f, dm)
    ref = emi_rail_lines(E, src, net, f)
    for m in E.RAIL_MODELS:
        for q in (0, 1):
            assert np.max(np.abs(V[m][q] - ref[m][q])) <= 1e-9 * np.max(np.abs(ref[m][q]))
    assert res < 1e-12


def test_f23_the_bound_covers_every_cm_return_model():
    lo, hi, fe, h = 3.795e6, 3.803e6, 400.0, 4500.0
    src, net = _src(fe), _net()
    row = E.conducted_emission_screening(src, net, _profile(lo, hi), lo, hi, n_grid=4)["domain"][0]
    n = np.arange(math.ceil((lo - h) / fe), math.floor((hi + h) / fe) + 1)
    V = emi_rail_lines(E, src, net, n * fe)
    for m in V:
        worst = max(float(np.sum(np.abs(V[m][q][(n * fe >= x - h - 1e-6) & (n * fe <= x + h + 1e-6)])))
                    for x in np.arange(lo, hi + 1.0, fe / 8) for q in (0, 1))
        assert row["E_sup_line_sum_dBuV"] >= _dbuv(worst) - 1e-9


# ---------------------------------------------------------------------------------------- EMI P3 wording

def test_emi_p3_blank_reserve_is_stated_and_declared_gaps_are_named():
    b = copy.deepcopy(api.EXAMPLE_EMI)
    b["profile"]["design_reserve_dB"], b["band_MHz"], b["n_grid"] = "", [3.7, 3.9], 6
    r = api.emi(b)
    assert r["profile"]["design_reserve_declared"] is False
    assert any("design reserve not declared" in x for x in r["notes"])
    b["profile"]["design_reserve_dB"] = 6.0
    r2 = api.emi(b)
    assert r2["profile"]["design_reserve_declared"] is True
    assert not any("design reserve not declared" in x for x in r2["notes"])
    # a FEASIBLE claim names the declared gaps it does not judge
    lo, hi = 3.7e6, 3.9e6
    src, net = _src(400.0), _net()
    prof = _profile(lo, hi, level=200.0, gaps=((3.75e6, 3.8e6),))
    ref = E.conducted_emission_screening(src, net, prof, lo, hi, n_grid=6)
    rec = {"evidence": "holdout correlation R-3", "holdout": "4 held-out points", "acquisition": "peak, 9 kHz",
           "error_model": "max |E_meas - E_model|", "uncertainty_dB": 1.0, "f_intervals_Hz": [[lo, hi]],
           **ref["configuration"]}
    ok = E.conducted_emission_screening(src, net, prof, lo, hi, n_grid=6, calibration=rec)
    assert ok["claim"]["status"] == "FEASIBLE"
    assert "declared gaps (no requirement, not judged): 3.75-3.8 MHz" in ok["claim"]["detail"]


# ---------------------------------------------------------------------------------------- F-22 HEV cache (A23)

def test_a23_hev_points_are_cached_per_machine_and_names_are_unique():
    b = copy.deepcopy(api.EXAMPLE_HEV)
    b["machines"] = [{"name": "EM", "speed_rpm": 3000.0, "drive": None},
                     {"name": "EM", "speed_rpm": 12000.0, "drive": None}]
    b["request_Nm"] = [100.0, 100.0]
    with pytest.raises(InputValidationError):
        api.hev_joint(b)                                        # two machines under one name: rejected
    b["machines"][0]["name"], b["machines"][1]["name"] = "EM1", "EM2"
    r = api.hev_joint(b)
    p1, p2 = r["request"]["branch_P_dc_W"]
    assert p2 / p1 == pytest.approx(4.0, rel=0.05)               # 4x the speed at the same torque
    assert r["request"]["status"] == "INFEASIBLE"                # 164 kW of branch power on a 100 kW battery
    # the cache itself: two machine objects that share a name are two operating points
    from traction_workbench.extensions import hev as H
    d = api._drive(api.EXAMPLE_HEV)
    cache = {}
    slow = H._machine_point(H.BusMachine("EM", d, 3000.0), 100.0, 600.0, cache)
    fast = H._machine_point(H.BusMachine("EM", d, 12000.0), 100.0, 600.0, cache)
    assert fast["P_dc_W"] > 3.5 * slow["P_dc_W"]
    bat = api._battery(api.EXAMPLE_HEV["battery"])
    with pytest.raises(InputValidationError):
        H.unregulated_bus([H.BusMachine("EM", d, 3000.0), H.BusMachine("EM", d, 6000.0)], [50.0, 50.0], bat)


# ---------------------------------------------------------------------------------------- F-24 ASC allowance (A24)

def test_a24_asc_peak_is_refined_and_the_allowance_bounds_the_grid_error():
    from traction_workbench.extensions.asc_transient import transient_constant
    from traction_workbench.physics import DriveKernel
    from traction_workbench.scenario import DcSourceLimits, Scenario
    r = api.asc(api.EXAMPLE_ASC)
    pk = r["requirements"]["ASC-PEAK"]
    assert pk["value"] == pytest.approx(1059.771, abs=1e-3)                  # the review's independent RK45 value
    pre = r["pre_fault"]
    k = DriveKernel(api._drive(api.EXAMPLE_ASC), Scenario("asc", 6000.0, 600.0, DcSourceLimits()))
    t = np.arange(0.0, 0.010, 0.5e-6)
    i_d, i_q = transient_constant(k, pre["id_A"], pre["iq_A"], t)
    j = int(np.argmax(np.hypot(i_d, i_q)))
    tt = np.linspace(t[j] - 0.5e-6, t[j] + 0.5e-6, 20001)
    a, b = transient_constant(k, pre["id_A"], pre["iq_A"], tt)
    fine = float(np.max(np.hypot(a, b)))
    assert pk["value"] >= fine - 1e-9                                        # the refined peak is the maximum
    assert abs(pk["value"] - fine) <= pk["numerical_allowance"] + 1e-9       # the allowance bounds its error
    assert pk["value"] >= float(np.max(np.hypot(i_d, i_q)))                  # never below a sampled maximum
    md = r["items"]["model_domain"]
    assert md["status"] == "OUTSIDE_DECLARED_DOMAIN" and md["excursion_factor"] == pytest.approx(2.12, abs=0.01)
    assert "declared current domain" in r["claim"]["detail"]


# ---------------------------------------------------------------------------------------- OEW off/off (A25)

def test_a25_common_bus_off_off_threshold_includes_the_triplen_emf():
    from traction_workbench.extensions.oew import OewTopology, ZeroSequenceModel, paired_state_screen
    V, w = 400.0, 2.0 * math.pi * 400.0
    e1 = 0.97 * V
    a3 = 0.10 * e1 / (3.0 * w)                                   # e3 = 3 w psi3 = 0.1 e1

    def topo(phase, amp=a3):
        return OewTopology("common_bus", V, zero_sequence=ZeroSequenceModel(50e-6, ((3, amp, phase),), basis="t"))
    for ph in (0.0, math.pi):
        row = paired_state_screen(topo(ph), "off", "off", e1, 50e-6, w)
        assert row["status"] != "FEASIBLE"                        # e1 + e3 = 1.067 V: it was FEASIBLE on e1 alone
        assert row["emf_total_bound_V"] == pytest.approx(1.1 * e1, rel=1e-9)            # 1.067 V
        th = np.linspace(0.0, 2.0 * math.pi, 200001)
        brute = float(np.max(np.abs(-e1 * np.sin(th) + w * (-3.0 * a3 * np.sin(3.0 * th + ph)))))
        assert row["emf_total_declared_phase_V"] == pytest.approx(brute, rel=1e-6)
    assert "peaks at" in paired_state_screen(topo(math.pi), "off", "off", e1, 50e-6, w)["reason"]
    # the triplen part is never assumed zero
    none = paired_state_screen(OewTopology("common_bus", V), "off", "off", e1, None, w)
    assert none["status"] == "UNKNOWN" and none["reason_code"] == "MISSING_INPUT"
    # comfortably below: fundamental + triplen <= V whatever the triplen phase
    low = paired_state_screen(topo(0.3, 0.1 * 0.8 * V / (3.0 * w)), "off", "off", 0.8 * V, 50e-6, w)
    assert low["status"] == "FEASIBLE"
    # isolated islands: two phases in series, the triplen part cancels - the line-EMF threshold is unchanged
    iso = paired_state_screen(OewTopology("isolated", V, V), "off", "off", e1, None)
    assert iso["threshold_phase_peak_V"] == pytest.approx(2.0 * V / math.sqrt(3.0))


# ---------------------------------------------------------------------------------------- driveline final level (P3)

def test_driveline_final_level_is_averaged_over_whole_torsional_periods():
    from traction_workbench.extensions import driveline as D
    dl = D.Driveline(0.2, 2.0, 3000.0, 30.0, basis="review 3 synthetic")
    for dur in (0.3, 0.55, 1.0):
        man = D.Maneuver(0.0, 100.0, 0.1, dur, 1000.0, window_Nm=(-500.0, 500.0), output_dt_s=0.0005)
        m = D.evaluate_variants(dl, {"off": D.Controller(0.001, 0.0, 0.01)}, man,
                                {"t_to_90_max_s": 0.2, "peak_jerk_max": 1e9, "settle_max_s": 1.0})["variants"]["off"]
        m = m["metrics"]
        assert m["achieved_fraction"] == pytest.approx(1.0, abs=1e-3)      # an exactly delivered request reads 1
        assert "torsional period" in m["a_final_basis"]


# ---------------------------------------------------------------------------------------- module A/B band (P3)

def test_ab_error_band_is_the_linear_sum_of_the_declared_bounds_and_states_it():
    from traction_workbench.analysis.efficiency import _verdict
    v = _verdict(1000.0, 1200.0, 0.05, 0.05, {"max"}, True)
    assert v["band"] == pytest.approx(0.05 * 1000.0 + 0.05 * 1200.0)
    assert v["band_rss"] == pytest.approx(math.hypot(50.0, 60.0))
    assert "linear sum" in v["band_basis"] and v["verdict"] == "A_LOWER_LOSS"


def test_f10_prot03_reads_the_same_exact_solution_beyond_the_horizon():
    from traction_workbench.extensions.protection import Plant, Sensor, protection_review
    pr = (("R_K_per_W", 0.2), ("C_J_per_K", 20.0), ("T_coolant_C", 60.0), ("P_W", 600.0), ("P_after_W", 350.0))
    plant = Plant("thermal_1node", 100.0, pr)

    def rows(horizon_s, delay_s):
        r = protection_review(plant, Sensor(period_s=0.001), 110.0, 150.0, horizon_s, action_delay_s=delay_s, phases=2)
        return {x["id"]: x for x in r["rows"]}
    beyond, inside = rows(1.0, 2.0), rows(5.0, 2.0)
    assert beyond["PROT-03"]["status"] == inside["PROT-03"]["status"] == "FEASIBLE"      # the same row either way
    assert "after the horizon" in beyond["PROT-03"]["detail"]
    late_beyond, late_inside = rows(1.0, 6.0), rows(10.0, 6.0)
    assert late_beyond["PROT-03"]["status"] == late_inside["PROT-03"]["status"] == "INFEASIBLE"
    assert rows(0.2, 2.0)["PROT-03"]["status"] == "UNKNOWN"                              # not confirmed yet


def test_f24_the_event_peak_and_minimum_id_are_refined_like_the_requirements():
    from traction_workbench.extensions.asc_transient import transient_constant
    from traction_workbench.physics import DriveKernel
    from traction_workbench.scenario import DcSourceLimits, Scenario
    r = api.asc(api.EXAMPLE_ASC)
    assert r["peak_dq_A"] >= r["requirements"]["ASC-PEAK"]["value"] - 1e-9       # the event covers the window
    pre = r["pre_fault"]
    k = DriveKernel(api._drive(api.EXAMPLE_ASC), Scenario("asc", 6000.0, 600.0, DcSourceLimits()))
    t = np.arange(0.0, 0.02, 0.25e-6)
    i_d, i_q = transient_constant(k, pre["id_A"], pre["iq_A"], t)
    assert r["peak_dq_A"] >= float(np.max(np.hypot(i_d, i_q))) - 1e-9
    assert r["min_id_A"] <= float(np.min(i_d)) + 1e-9
    j = int(np.argmin(i_d))
    tt = np.linspace(t[max(j - 1, 0)], t[j + 1], 4001)
    fine = float(np.min(transient_constant(k, pre["id_A"], pre["iq_A"], tt)[0]))
    assert r["min_id_A"] == pytest.approx(fine, abs=1e-6)
