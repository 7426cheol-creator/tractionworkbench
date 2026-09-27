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


def test_exchange_sampling_and_transition_fixtures_match_their_closed_forms():
    import math
    fx = build_package(include_examples=False)["fixtures"]
    s = fx["PWM-single-shunt"]
    m, fsw, fe = s["inputs"]["m"], s["inputs"]["fsw_Hz"], s["inputs"]["fe_Hz"]
    Ts = 1.0 / fsw
    for j, w in enumerate(s["first_windows_s"]):
        th = (2 * math.pi * fe * (j + 0.5) * Ts) % (math.pi / 3)
        ref = min((math.sqrt(3) / 4) * m * Ts * math.sin(math.pi / 3 - th), (math.sqrt(3) / 4) * m * Ts * math.sin(th))
        assert w == pytest.approx(ref, abs=s["tol_abs_s"])
    t = fx["PWM-transition"]
    for k, v in t["expected"].items():
        assert t["output_jump_V"][k] == pytest.approx(v, abs=t["tol_abs_V"])
