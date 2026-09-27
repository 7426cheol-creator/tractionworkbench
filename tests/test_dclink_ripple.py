"""DC-link ripple / capacitor current / ESR loss / life gating (independent review 8.9)."""

import math

import numpy as np
import pytest

from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.dclink_ripple import (CapacitorBank, SourceImpedance, average_model,
                                                          kolar_spwm_cap_rms, ripple_analysis, spectrum,
                                                          switching_waveform)


@pytest.mark.parametrize("m, phi_deg", [(0.9, 0.0), (0.6, 30.0), (0.3, 80.0), (0.8, 180.0), (0.95, 150.0)])
def test_average_model_reproduces_the_spwm_closed_form(m, phi_deg):
    I = 400.0
    phi = math.radians(phi_deg)
    am = average_model(I, m, phi, "spwm", n=20000)
    assert am["I_inv_ac_rms_A"] == pytest.approx(kolar_spwm_cap_rms(I, m, phi), rel=1e-6)
    assert am["I_dc_A"] == pytest.approx(0.75 * m * I * math.cos(phi), rel=1e-6, abs=1e-9)   # P balance, sign kept


def test_time_domain_switching_matches_the_average_model_and_parseval():
    I, m, phi = 350.0, 0.8, math.radians(25.0)
    am = average_model(I, m, phi, "svpwm")
    w = switching_waveform(I, m, phi, 200.0, 20e3, "svpwm", samples_per_carrier=512)
    x = w["i_inv_A"]
    ac = math.sqrt(np.mean(x ** 2) - np.mean(x) ** 2)
    assert ac == pytest.approx(am["I_inv_ac_rms_A"], rel=5e-3)
    assert np.mean(x) == pytest.approx(am["I_dc_A"], rel=5e-3)
    sp = spectrum(x, 200.0)
    assert abs(sp["parseval_residual"]) < 1e-9 * max(1.0, float(np.sqrt(np.mean(x ** 2))))


def _bank(esr=2e-3, **kw):
    return CapacitorBank(500e-6, ((100.0, esr), (1e7, esr)), **kw)


def test_stiff_source_puts_all_ac_into_the_capacitor_and_constant_esr_loss():
    r = ripple_analysis(350.0, 0.8, math.radians(20.0), 200.0, 10e3, 600.0, _bank())
    assert r["I_cap_rms_A"] == pytest.approx(r["I_inv_ac_rms_A"], rel=1e-9)
    assert r["P_cap_W"] == pytest.approx(r["I_cap_rms_A"] ** 2 * 2e-3, rel=1e-9)
    assert r["assumption"] and "not a bound" in r["assumption"]


def test_source_impedance_splits_and_can_amplify_near_resonance():
    lo_z = ripple_analysis(350.0, 0.8, math.radians(20.0), 200.0, 10e3, 600.0, _bank(),
                           SourceImpedance(0.02, 1e-9, "test"))
    assert lo_z["I_cap_rms_A"] < lo_z["I_inv_ac_rms_A"]                  # a stiff battery takes part of the AC
    # L_s chosen so that the series resonance with C sits on a strong harmonic: the capacitor current exceeds the
    # inverter AC current - "all AC into the capacitor" is not an upper bound
    f_res = 6 * 200.0
    L = 1.0 / ((2 * math.pi * f_res) ** 2 * 500e-6)
    res = ripple_analysis(350.0, 0.8, math.radians(20.0), 200.0, 10e3, 600.0, _bank(esr=1e-4),
                          SourceImpedance(1e-4, L, "test"))
    assert res["I_cap_rms_A"] > res["I_inv_ac_rms_A"]


def test_esr_outside_its_band_is_not_extrapolated():
    narrow = CapacitorBank(500e-6, ((100.0, 2e-3), (5e3, 2e-3)))
    r = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, narrow)
    assert r["P_cap_W"] is None and r["current_share_outside_ESR_band"] > 0.1
    assert r["claims"]["capacitor_loss"]["status"] == "UNKNOWN"


def test_requirement_must_define_location_quantity_and_bandwidth():
    r = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, _bank(), requirement={"limit": 10.0})
    assert r["claims"]["ripple_requirement"]["reasons"] == ["REQUIREMENT_INCOMPLETE"]
    req = {"location": "dc_link_bus", "quantity": "voltage_pp", "limit": 50.0, "bandwidth_Hz": 20e3}
    r2 = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, _bank(), requirement=req)
    assert r2["claims"]["ripple_requirement"]["status"] == "UNKNOWN"          # source impedance missing
    r3 = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, _bank(), SourceImpedance(0.02, 2e-6, "harness"),
                         requirement=req)
    assert r3["claims"]["ripple_requirement"]["status"] in ("FEASIBLE", "INFEASIBLE")


def test_electrical_result_with_unknown_life_and_supplier_life_table():
    r = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, _bank(Rth_K_per_W=0.3), T_ref_C=65.0)
    assert r["hotspot"]["converged"] and r["claims"]["capacitor_life"]["status"] == "UNKNOWN"
    lt = _bank(Rth_K_per_W=0.3, life_hours_table=((60.0, 300000.0), (85.0, 100000.0), (105.0, 30000.0)),
               life_voltage_V=650.0, life_basis="test series table")
    r2 = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, lt, T_ref_C=65.0)
    assert r2["life"]["status"] == "CONDITIONAL" and r2["life"]["hours_at_hotspot"] > 0


def test_zero_current_and_invalid_inputs():
    z = average_model(0.0, 0.5, 0.0)
    assert z["I_inv_ac_rms_A"] == 0.0 and z["I_dc_A"] == 0.0
    with pytest.raises(InputValidationError):
        average_model(100.0, 1.3, 0.0, "svpwm")                             # overmodulation
    with pytest.raises(InputValidationError):
        CapacitorBank(500e-6, ((100.0, 1e-3),), count=2)                      # equal split needs declared symmetry
