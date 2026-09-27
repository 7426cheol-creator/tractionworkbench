"""V2: capability / source-boundary goldens (golden_capability.json)."""

import pytest

from conftest import golden, scenario
from traction_workbench.physics import DriveKernel
from traction_workbench.solvers.capability import physical_capability, policy_capability
from traction_workbench.solvers.certificate import certify_torque
from traction_workbench.solvers.policy import PolicyEvaluator

CASES = golden("golden_capability.json")["cases"]
IDS = [f"{c['n_rpm']}rpm_{c['Vdc_V']}V_{c['active_source_power_W']:.0f}W" for c in CASES]


@pytest.fixture(scope="module")
def results(drive):
    out = []
    for c in CASES:
        ev = PolicyEvaluator(drive, scenario(c["n_rpm"], c["Vdc_V"]))
        out.append(policy_capability(ev, 1 if c["expected_Tshaft_Nm"] > 0 else -1))
    return out


@pytest.mark.parametrize("i", range(len(CASES)), ids=IDS)
def test_capability_value(results, i):
    c, r = CASES[i], results[i]
    tol = max(0.1, 0.0005 * 360.0)  # acceptance gap; torque scale of this fixture ~ 1.5*p*psi*Imax = 360 N*m
    assert abs(r.value_Nm - c["expected_Tshaft_Nm"]) <= tol
    # our achieved boundary is far tighter than the acceptance gap
    assert abs(r.value_Nm - c["expected_Tshaft_Nm"]) <= 1e-5


@pytest.mark.parametrize("i", range(len(CASES)), ids=IDS)
def test_capability_witness(results, i):
    c, r = CASES[i], results[i]
    e = c["expected_solution"]
    assert abs(r.witness.id_A - e["id_A_peak"]) <= 1e-3
    assert abs(r.witness.iq_A - e["iq_A_peak"]) <= 1e-3
    assert abs(r.witness.Pdc_W - e["Pdc_W"]) <= 0.5


def test_motoring_capabilities_are_certified(results):
    for c, r in zip(CASES, results):
        if c["expected_Tshaft_Nm"] > 0:
            assert r.certified and r.gap_Nm <= r.gap_tolerance_Nm
            assert r.bound_Nm == pytest.approx(c["expected_Tshaft_Nm"], abs=1e-6)


def test_regen_boundary_is_policy_not_physical_maximum(drive, results):
    i = next(i for i, c in enumerate(CASES) if c["expected_Tshaft_Nm"] < 0)
    r = results[i]
    assert not r.certified                       # sampled scan only
    ev = PolicyEvaluator(drive, scenario(12000, 600))
    phys = physical_capability(ev, -1, include_dc=True)
    assert phys.value_Nm < r.value_Nm - 1.0      # deliberate losses brake harder: not the policy
    assert any("raising losses" in n for n in r.notes)


@pytest.mark.parametrize("i", [i for i, c in enumerate(CASES) if c.get("maximum_torque_upper_bound_certificate")],
                         ids=[IDS[i] for i, c in enumerate(CASES) if c.get("maximum_torque_upper_bound_certificate")])
def test_certificate_matches_fixture(drive, i):
    c = CASES[i]
    fc = c["maximum_torque_upper_bound_certificate"]
    e = c["expected_solution"]
    k = DriveKernel(drive, scenario(c["n_rpm"], c["Vdc_V"]))
    cert = certify_torque(k, +1, (e["id_A_peak"], e["iq_A_peak"]))
    m = dict(cert.multipliers)
    assert cert.valid
    assert m["VOLTAGE"] == pytest.approx(fc["lambda_voltage"], rel=1e-6)
    assert m["DC_DISCHARGE"] == pytest.approx(fc["lambda_dc_power"], rel=1e-6)
    assert sorted(cert.hessian_eigenvalues) == pytest.approx(sorted(fc["lagrangian_hessian_eigenvalues"]), rel=1e-6)
    assert cert.upper_bound == pytest.approx(c["expected_Tshaft_Nm"], abs=1e-9)


def test_current_margin_is_not_torque_margin(drive):
    """Blueprint section 9: ~204 A current margin but only ~2.555 N*m torque margin at 12,000 rpm / 600 V."""
    ev = PolicyEvaluator(drive, scenario(12000, 600))
    sol = ev.solve(150)
    cap = policy_capability(ev, +1)
    assert sol.point.constraint("CURRENT").slack == pytest.approx(204.221, abs=1e-3)
    assert cap.value_Nm - 150 == pytest.approx(2.555, abs=1e-3)
    assert sol.point.constraint("DC_DISCHARGE_POWER").slack == pytest.approx(3368.6, abs=0.5)
    assert sol.point.constraint("VOLTAGE").state == "ACTIVE"
