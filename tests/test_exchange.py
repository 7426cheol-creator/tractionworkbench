"""Exchange package for a MathWorks port: schema, conventions and fixtures with the addenda's reference values."""

import json

import pytest

from traction_workbench.exchange import SCHEMA, build_package


def test_exchange_package_carries_the_reference_fixtures():
    pkg = build_package()
    json.dumps(pkg, default=float)                                   # serialisable
    assert pkg["schema"] == SCHEMA and "not qualify the physics" in pkg["scope"]
    fx = pkg["fixtures"]
    assert fx["E-01"]["result"]["edrive"]["eta"] == pytest.approx(0.88)
    assert fx["E-02"]["result"]["inverter"]["eta"] == pytest.approx(0.96666667, abs=1e-8)
    assert fx["E-03"]["result"]["motor"]["status"] == "N/A"
    assert (fx["E-06"]["eta_traction"], fx["E-06"]["eta_regeneration"]) == pytest.approx((0.9, 0.8))
    assert fx["D-01"]["f_n_Hz"] == pytest.approx(20.4438226, abs=5e-8)
    assert fx["D-02"]["zeta"] == pytest.approx(0.14012981, abs=5e-9)
    assert fx["D-03"]["first_crossing_tau_s"] == pytest.approx(14.45702348e-3, abs=5e-11)
    assert fx["D-03"]["rhp_root"]["re"] == pytest.approx(1.92230146, abs=5e-8)
    assert fx["D-04"]["mean_correction_Nm"] == pytest.approx(-4.06619756, abs=5e-9)
    assert fx["PWM-delay"]["phase_at_1000_Hz_deg"] == pytest.approx({"10k": 54.0, "5k": 108.0})
    r = fx["PWM-ripple"]
    assert r["ripple_rms_A_time"] == pytest.approx(r["ripple_rms_A_spectrum"], rel=1e-3)
    assert "referral" in pkg["conventions"]["driveline"] and "telescoping" in pkg["conventions"]["efficiency_boundaries"]
    assert pkg["example_inputs"]["pwm"]["schedules"]
