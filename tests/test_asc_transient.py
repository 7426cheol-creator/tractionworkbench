"""ASC fault transient with two customer current-time requirements (independent review 9.13)."""

import math

import numpy as np
import pytest

from traction_workbench import spec_fixtures as sf
from traction_workbench.extensions.asc_transient import CurrentTimeRequirement, asc_transient
from traction_workbench.extensions.safe_state import asc_steady_state
from traction_workbench.scenario import DcSourceLimits, Scenario
from traction_workbench.solvers.policy import PolicyEvaluator

REQS = (CurrentTimeRequirement("ASC-PEAK", "phase", "abs_peak", 0.0, 0.010, 900.0, "asc_established"),
        CurrentTimeRequirement("ASC-RMS", "phase", "rms", 0.050, 0.100, 300.0, "asc_established"))


def _sc(n):
    return Scenario("asc", n, 600.0, DcSourceLimits())


def test_transient_converges_to_the_steady_asc_and_matches_rk(drive):
    n = 6000.0
    pt = PolicyEvaluator(drive, Scenario("p", n, 600.0, sf.synthetic_limits())).solve(150.0).point
    r = asc_transient(drive, _sc(n), pt.id_A, pt.iq_A, 1e-3, 0.3, REQS)
    ss = asc_steady_state(drive, n, 600.0)
    assert r["steady_asc"]["id_A"] == pytest.approx(ss["id_A"], rel=1e-4)
    assert r["steady_asc"]["iq_A"] == pytest.approx(ss["iq_A"], rel=1e-3, abs=1e-3)
    assert r["rk_cross_check_A"] < 1e-5                                   # independent integration (V2)
    assert r["waveform"]["id_A"][0] == pytest.approx(pt.id_A)             # pre-fault state held until the short


def test_no_load_short_of_a_salient_free_machine_peaks_near_two_psi_over_L(drive):
    spm = sf.synthetic_drive({"Rs_phase_ohm": 1e-4, "psi_pm_Wb": 0.08, "Ld_H": 0.0003, "Lq_H": 0.0003,
                              "drag_coefficient_Nm_per_rad_s": 0, "inverter_loss_offset_W": 0,
                              "inverter_loss_Ipk2_coefficient_ohm": 0})
    r = asc_transient(spm, _sc(6000.0), 0.0, 0.0, 0.0, 0.05, REQS)
    assert r["min_id_A"] == pytest.approx(-2 * 0.08 / 0.0003, rel=0.02)   # classic -2 psi/L first swing
    assert r["peak_dq_A"] > 1.9 * 0.08 / 0.0003


def test_each_requirement_is_its_own_operator_and_the_joint_claim_is_screening(drive):
    pt = PolicyEvaluator(drive, Scenario("p", 3000.0, 600.0, sf.synthetic_limits())).solve(200.0).point
    r = asc_transient(drive, _sc(3000.0), pt.id_A, pt.iq_A, 0.5e-3, 0.3, REQS)
    peak, rms = r["requirements"]["ASC-PEAK"], r["requirements"]["ASC-RMS"]
    assert peak["operator"] == "abs_peak" and rms["operator"] == "rms"
    assert peak["value"] >= rms["value"]                                  # a peak is not an RMS
    assert peak["value"] <= r["peak_dq_A"] + 1e-9                         # phase peak <= dq norm
    assert r["claim"]["status"] == "UNKNOWN" and r["claim_level"].startswith("SCREENING")
    assert r["items"]["demagnetisation"]["status"] == "UNKNOWN"
    assert r["items"]["device_survival"]["status"] == "UNKNOWN"
    tight = asc_transient(drive, _sc(3000.0), pt.id_A, pt.iq_A, 0.5e-3, 0.3,
                          (CurrentTimeRequirement("P", "phase", "abs_peak", 0.0, 0.01, 10.0, "asc_established"),))
    assert tight["requirements"]["P"]["screening_verdict"] == "FAIL" and tight["claim"]["status"] == "UNKNOWN"


def test_incomplete_requirement_and_imported_envelopes(drive):
    bad = CurrentTimeRequirement("X", "phase", "rms", 0.02, 0.01, 300.0)          # window reversed
    r = asc_transient(drive, _sc(3000.0), -100.0, 200.0, 0.0, 0.1, (bad,))
    assert r["requirements"]["X"]["status"] == "REQUIREMENT_INCOMPLETE"
    assert r["claim"]["reasons"] == ["REQUIREMENT_INCOMPLETE"]
    r2 = asc_transient(drive, _sc(3000.0), -100.0, 200.0, 0.0, 0.1, REQS, demag_id_min_A=-5000.0,
                       demag_basis="test envelope", device_peak_A=5000.0, device_basis="test")
    assert r2["items"]["demagnetisation"]["status"] == "SCREENING_PASS"
    assert r2["items"]["device_survival"]["status"] == "SCREENING_PASS"


def test_flux_map_is_not_dynamically_qualified():
    r = asc_transient(sf.manufactured_map_drive(), _sc(3000.0), -50.0, 50.0, 0.0, 0.05, REQS)
    assert r["evaluable"] is False and "not dynamically qualified" in r["claim"]["detail"]


def test_inertia_slows_the_rotor(drive):
    r = asc_transient(drive, _sc(3000.0), -100.0, 200.0, 0.0, 0.5, REQS, J_kgm2=0.02)
    assert r["evaluable"] and r["rk_cross_check_A"] is not None
