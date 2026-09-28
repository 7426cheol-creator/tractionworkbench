"""PWM harmonic & variable-PWM handoff, P0 acceptance tests (section 19 A-F): loss / upper-bound / UNKNOWN semantics,
the R_dc lower bound of the PWM copper, R_ac coverage, the Fe+PM HF bound as an interval end, the conservative
peak-current bound, requested vs waveform-used fsw, interval-based energy comparison and Pareto, the thermal scope
and the PWM items of the main efficiency ledger."""

import math

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.pwm_policy import (HarmonicLossData, _pareto, compare_intervals,
                                                      harmonic_copper_loss, motor_hf_interval, phase_ripple,
                                                      policy_energy)

RS = 0.015
TABLE = ((0.0, 1e3, 1e4, 1e5, 1e6, 3e6), (1.0, 1.02, 1.8, 8.0, 25.0, 45.0))


@pytest.fixture(scope="module")
def pol():
    return api.pwm_policies({})


@pytest.fixture(scope="module")
def rip():
    return phase_ripple(600.0, 0.6, 0.0, 200.0, 10e3, 200e-6, "svpwm")


# -- A. PWM ripple -------------------------------------------------------------------------------------------------

def test_a1_time_integration_rms_matches_the_edge_spectrum(rip):
    assert rip["ripple_rms_A"] == pytest.approx(rip["ripple_rms_spectrum_A"], rel=1e-3)


@pytest.mark.parametrize("mod", ["svpwm", "spwm"])
def test_a2_doubling_fsw_halves_the_scalar_l_hf_ripple(mod):
    a = phase_ripple(600.0, 0.6, 0.0, 200.0, 10e3, 200e-6, mod)
    b = phase_ripple(600.0, 0.6, 0.0, 200.0, 20e3, 200e-6, mod)
    assert b["ripple_rms_A"] / a["ripple_rms_A"] == pytest.approx(0.5, rel=0.01)


@pytest.mark.parametrize("mod", ["svpwm", "spwm"])
def test_a3_zero_modulation_has_no_phase_voltage_ripple(mod):
    """m = 0: every leg switches with the same duty at the same instants, the phase-to-neutral voltage of an isolated
    star is zero - no switching ripple in the phase current (the model's meaning of 'phase ripple')."""
    r = phase_ripple(600.0, 0.0, 0.0, 200.0, 10e3, 200e-6, mod)
    assert r["ripple_rms_A"] < 1e-9 and r["ripple_rms_spectrum_A"] < 1e-9


def test_a4_non_integer_carrier_ratio_is_reported_not_hidden(pol):
    r = phase_ripple(600.0, 0.6, 0.0, 733.0, 10e3, 200e-6, "svpwm")
    assert r["carrier_ratio"] == 14 and r["fsw_used_Hz"] == pytest.approx(14 * 733.0)
    segs = [s for p in pol["policies"] for s in p["segments"]]
    assert all({"fsw_requested_Hz", "fsw_waveform_used_Hz", "fsw_error_percent"} <= set(s) for s in segs)
    worst = max(segs, key=lambda s: abs(s["fsw_error_percent"]))
    assert worst["fsw_waveform_used_Hz"] != worst["fsw_requested_Hz"]
    assert worst["fsw_error_percent"] == pytest.approx(
        100 * (worst["fsw_waveform_used_Hz"] - worst["fsw_requested_Hz"]) / worst["fsw_requested_Hz"])
    assert any("synchronous carrier" in a for p in pol["policies"] for a in p["advisory"])


# -- B. PWM copper -------------------------------------------------------------------------------------------------

def test_b1_unit_rac_ratio_gives_three_rs_ripple_rms_squared(rip):
    flat = HarmonicLossData((0.0, 1e8), (1.0, 1.0), basis="test: R_ac = R_dc")
    cu = harmonic_copper_loss(rip, RS, flat)
    assert cu["status"] == "ESTABLISHED" and cu["rac_coverage_I2_fraction"] == pytest.approx(1.0)
    assert cu["W"] == pytest.approx(3 * RS * rip["ripple_rms_spectrum_A"] ** 2, rel=1e-12)
    assert cu["W"] == pytest.approx(3 * RS * rip["ripple_rms_A"] ** 2, rel=2e-3)
    assert cu["W"] == pytest.approx(cu["lower_bound_W"], rel=1e-12)


def test_b2_exact_copper_is_never_below_the_rdc_lower_bound(rip):
    cu = harmonic_copper_loss(rip, RS, HarmonicLossData(*TABLE, basis="test table"))
    assert cu["W"] is not None and cu["W"] >= cu["lower_bound_W"] > 0


def test_b3_lines_outside_the_table_leave_only_the_lower_bound(rip):
    short = HarmonicLossData((0.0, 1e3, 3e4), (1.0, 1.02, 2.5), basis="test: table ends at 30 kHz")
    cu = harmonic_copper_loss(rip, RS, short)
    assert cu["W"] is None and cu["status"] == "LOWER_BOUND_ONLY" and "no extrapolation" in cu["reason"]
    assert 0.0 < cu["rac_coverage_I2_fraction"] < 1.0
    assert cu["lower_bound_W"] == pytest.approx(3 * RS * rip["ripple_rms_spectrum_A"] ** 2, rel=1e-12)
    none = harmonic_copper_loss(rip, RS, None)                          # no data: never 0 W, the bound stays
    assert none["W"] is None and none["lower_bound_W"] == pytest.approx(cu["lower_bound_W"])
    assert none["rac_coverage_I2_fraction"] is None


def test_b4_synthetic_harmonic_data_stays_labelled_synthetic():
    led = api.efficiency({})["ledger"]
    item = next(i for i in led["loss_items"] if i["item"] == "PWM harmonic copper")
    assert "synthetic" in item["basis"] and "synthetic" in led["pwm_hf"]["basis"]
    with pytest.raises(InputValidationError, match="basis"):
        HarmonicLossData(*TABLE, basis="")


# -- C. magnetic HF loss -------------------------------------------------------------------------------------------

def test_c1_the_bound_is_never_summed_as_a_loss(pol):
    led = api.efficiency({})["ledger"]
    mag = next(i for i in led["loss_items"] if i["item"] == "PWM Fe+PM HF magnetic loss")
    assert mag["W"] is None and mag["upper_bound_W"] > 0 and mag["status"] == "UPPER_BOUND"
    assert led["loss_total_W"] is None
    assert led["loss_interval_W"][1] == pytest.approx(led["loss_interval_W"][0] + mag["upper_bound_W"])
    for p in pol["policies"]:
        e = p["energy"]
        assert e["lower_J"] == pytest.approx(p["E_inv_J"] + p["E_cu_pwm_J"])            # bound not in the value
        assert e["upper_J"] == pytest.approx(e["lower_J"] + p["E_mag_hf_bound_J"])


def test_c2_a_missing_bound_leaves_the_comparison_open():
    r = api.pwm_policies({"harmonic": {"f_Hz": list(TABLE[0]), "rac_over_rdc": list(TABLE[1]),
                                       "basis": "test: R_ac only, no magnetic bound"}})
    for p in r["policies"]:
        assert p["E_mag_hf_bound_J"] is None and p["energy"]["upper_J"] is None and p["energy"]["status"] == "OPEN"
        if p.get("versus_baseline", {}).get("total"):
            assert p["versus_baseline"]["total"]["status"] in ("UNKNOWN", "IMPROVED", "WORSE")
            assert p["versus_baseline"]["total"]["status"] != "UNDECIDED"
    assert r["best_policy_energy_among_evaluated"]["policy"] is None


def test_c3_no_interpolation_between_anchor_frequencies():
    h = HarmonicLossData(*TABLE, magnetic_hf_loss_bound_W=((8e3, 350.0), (10e3, 300.0)), basis="test")
    assert h.magnetic_hf_bound(10e3) == 300.0 and h.magnetic_hf_bound(9e3) is None
    assert h.iron_bound(8e3) == 350.0                                            # the alias reads the same bound
    old = HarmonicLossData(*TABLE, basis="test", iron_bound_W=((8e3, 350.0),))  # older files
    assert old.magnetic_hf_bound(8e3) == 350.0
    with pytest.raises(InputValidationError, match="disagree"):
        HarmonicLossData(*TABLE, magnetic_hf_loss_bound_W=((8e3, 1.0),), basis="t", iron_bound_W=((8e3, 2.0),))


def test_c4_fe_and_pm_are_not_split_without_data():
    led = api.efficiency({})["ledger"]
    names = [i["item"] for i in led["loss_items"]]
    assert "PWM Fe+PM HF magnetic loss" in names
    assert not any(("iron" in n.lower() or "magnet" in n.lower()) and n.startswith("PWM") and "Fe+PM" not in n
                   for n in names)


# -- D. power ledger -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("torque", [150.0, -100.0])
def test_d_port_identities_hold_and_the_hf_loss_only_widens_the_efficiency(torque):
    with_hf = api.efficiency({"torque_Nm": torque})["ledger"]
    without = api.efficiency({"torque_Nm": torque, "pwm_hf": None})["ledger"]
    assert with_hf["ports_W"] == without["ports_W"]                      # the fundamental ports are untouched
    p = with_hf["ports_W"]
    items = {i["item"]: i for i in with_hf["loss_items"]}
    fund = sum(i["W"] for i in with_hf["loss_items"] if i["in_port_powers"] and i["boundary"] != "reducer")
    assert p["P_dc"] - p["P_m"] == pytest.approx(fund, rel=1e-9, abs=1e-6)  # inverter + fundamental motor losses
    assert not items["PWM harmonic copper"]["in_port_powers"]
    for k in ("motor", "inverter_motor"):
        b = with_hf["boundaries"][k]
        assert b["status"] == "DEFINED" and b["eta"] == without["boundaries"][k]["eta"]
        lo, hi = b["eta_interval_incl_pwm_hf"]
        assert lo is not None and lo <= hi <= b["eta"]                    # extra loss never raises efficiency
        assert b["direction"] == ("forward" if torque > 0 else "reverse")
    assert "eta_interval_incl_pwm_hf" not in without["boundaries"]["motor"]


# -- E. variable PWM -----------------------------------------------------------------------------------------------

def test_e2_a_mandatory_violation_is_never_bought_with_energy(pol):
    bad = [p for p in pol["policies"] if p["status"] == "VIOLATION"]
    assert bad and all(p["policy"]["name"] not in pol["pareto"] for p in bad)
    assert pol["best_inverter_energy_among_evaluated"] not in [p["policy"]["name"] for p in bad]


def _row(name, e_inv, lo, hi, **kw):
    return {"policy": {"name": name}, "E_inv_J": e_inv, "energy": {"lower_J": lo, "upper_J": hi},
            "Tj_max_C": kw.get("tj", 100.0), "i_peak_bound_max_A": 400.0, "I_cap_rms_max_A": 100.0,
            "phase_margin_min_deg": 60.0}


def test_e3_e5_the_energy_axis_decides_only_by_separated_intervals():
    a = _row("A", 90.0, 100.0, 120.0)
    names = lambda rows: sorted(r["policy"]["name"] for r in _pareto(rows))
    b = _row("B", 95.0, 110.0, 130.0)                  # overlaps A: B may still use less total energy
    assert names([a, b]) == ["A", "B"]                 # UNDECIDED on energy -> neither is dominated
    same = _row("S", 95.0, 100.0, 120.0)               # the same interval, worse inverter energy: dominated
    assert names([a, same]) == ["A"]
    b2 = _row("B", 85.0, 110.0, 130.0)                 # better inverter energy, overlapping interval: both stay
    assert names([a, b2]) == ["A", "B"]
    c = _row("C", 95.0, 121.0, 140.0)                  # separated above A and worse inverter energy: dominated
    assert names([a, c]) == ["A"]
    d = _row("D", 80.0, 99.0, None)                    # open interval never dominates on energy
    assert names([a, d]) == ["A", "D"]


def test_e4_an_unknown_objective_is_never_zero():
    e = policy_energy(1000.0, None, 40.0, 300.0)
    assert e["lower_J"] == 1040.0 and e["upper_J"] is None and e["status"] == "OPEN"
    assert "lower bound only" in e["reason"]
    assert motor_hf_interval({"W": None, "lower_bound_W": 5.0}, 100.0) == [5.0, None]
    assert motor_hf_interval({"W": 7.0, "lower_bound_W": 5.0}, None) == [7.0, None]
    assert motor_hf_interval({"W": 7.0, "lower_bound_W": 5.0}, 100.0) == [7.0, 107.0]


@pytest.mark.parametrize("a, b, status", [((1, 2), (3, 4), "IMPROVED"), ((5, 6), (3, 4), "WORSE"),
                                          ((1, 3.5), (3, 4), "UNDECIDED"), ((1, None), (3, 4), "UNKNOWN"),
                                          ((5, None), (3, 4), "WORSE"), ((1, 2), (3, None), "IMPROVED"),
                                          ((None, 2), (3, 4), "UNKNOWN")])
def test_interval_comparison(a, b, status):
    assert compare_intervals(a, b)["status"] == status


def test_e5_best_energy_needs_separation(pol):
    be = pol["best_policy_energy_among_evaluated"]
    adm = [p for p in pol["policies"] if p["admissible"]]
    if be["policy"] is not None:
        win = next(p for p in adm if p["policy"]["name"] == be["policy"])
        assert all(win["energy"]["upper_J"] < p["energy"]["lower_J"] for p in adm if p is not win)
    else:
        assert be["status"] in ("UNDECIDED", "UNKNOWN")
    assert "no global" in pol["meaning"]


def test_e6_peak_current_is_labelled_a_conservative_bound(pol):
    assert "conservative bound" in pol["peak_current_meaning"] and "not the exact" in pol["peak_current_meaning"]
    for p in pol["policies"]:
        for s in p["segments"]:
            if s.get("i_peak_bound_A") is not None:
                assert s["i_peak_bound_A"] == pytest.approx(s["i_peak_A"] + s["ripple_peak_A"])
        assert "i_peak_incl_ripple_max_A" not in p                          # the old name is gone from results


def test_the_peak_limit_takes_its_canonical_name():
    lim = {"Tj_max_C": 150.0, "cap_rms_max_A": 250.0, "phase_margin_min_deg": 45.0, "pulse_ratio_min": 10.0,
           "transition_excursion_max_A": 50.0}
    r = api.pwm_policies({"pwm_limits": {**lim, "i_peak_bound_max_A": 700.0}})
    assert r["limits"]["i_peak_incl_ripple_max_A"] == 700.0
    with pytest.raises(InputValidationError, match="disagree"):
        api.pwm_policies({"pwm_limits": {**lim, "i_peak_bound_max_A": 700.0, "i_peak_incl_ripple_max_A": 650.0}})


def test_e7_one_modulation_family_for_every_model(pol):
    assert pol["module_modulation"]["used"] == pol["modulation"]


def test_control_volume_ownership_is_declared(pol):
    cv = pol["energy_control_volume"]
    assert any("capacitor" in x for x in cv["excluded"]) and any("LV" in x for x in cv["excluded"])
    assert any(p.get("E_cap_J") for p in pol["policies"])                  # shown separately, not in the energy
    for p in pol["policies"]:
        if p["E_cap_J"] is not None and p["energy"]["upper_J"] is not None:
            assert p["energy"]["upper_J"] == pytest.approx(p["E_inv_J"] + p["E_cu_pwm_J"] + p["E_mag_hf_bound_J"])


# -- F. thermal interpretation -------------------------------------------------------------------------------------

def test_f_the_supplied_sensor_trajectory_is_not_a_closed_loop(pol):
    ts = pol["thermal_scope"]
    assert "SUPPLIED trajectory" in ts and "not a closed-loop" in ts
    assert any("EMI / NVH / bearing-current" in x for x in pol["not_evaluated"])


def test_rs_follows_the_scenario_temperature_law():
    """The lower bound uses the same R_s(T) as the fundamental copper loss of that scenario."""
    from traction_workbench import spec_fixtures as sf
    from traction_workbench.extensions.pwm_policy import point_hf_losses
    from traction_workbench.physics import DriveKernel
    from traction_workbench.solvers.policy import PolicyEvaluator
    from traction_workbench.scenario import Scenario
    d, lim = sf.synthetic_drive(), sf.synthetic_limits()
    sc = Scenario("t", 6000.0, 600.0, lim)
    pt = PolicyEvaluator(d, sc).solve(100.0).point
    hf = point_hf_losses(d, sc, pt, 10e3, 200e-6, "svpwm", None)
    rs = DriveKernel(d, sc).Rs
    assert hf["copper"]["Rs_ohm"] == rs and hf["status"] == "OPEN" and hf["interval_W"][1] is None
    assert math.isclose(hf["copper"]["lower_bound_W"] / rs,
                        3 * float(np.sum(0.5 * phase_ripple(600.0, pt.v_peak_V / 300.0, 0.0, abs(pt.f_e_Hz), 10e3,
                                                            200e-6, "svpwm", n_per_carrier=128)
                                         ["harmonic_I_pk_A"] ** 2)), rel_tol=1e-12)
