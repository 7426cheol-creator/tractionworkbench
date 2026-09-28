"""Engineering review of 6198099 (F1 - F3, G1): evidence direction of duration ratings, the declared linear rating,
thermal torque sets that keep the static gaps, and rating evidence bound to the product and its conditions.

The first seven tests are the reviewer's regression tests as delivered (test doubles, not supplier data); the rest
cover the fixes' own edges.  INFEASIBLE from a rating means RATING_NOT_MET (not rated), never physical impossibility.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from traction_workbench.analysis.rating import (ApprovalState, RatingApproval, RatingEnvelope, binding,
                                                duration_claim)
from traction_workbench.extensions import thermal
from traction_workbench.models.provenance import DataOrigin, Provenance
from traction_workbench.status import Reason, Status

STATED = {
    "coolant_temp_C": 65.0,
    "Vdc_V": 600.0,
    "switching_frequency_Hz": 10_000.0,
    "initial_state": "equilibrium_at_coolant",
}
PRODUCT = {"drive_id": "DRIVE-A", "drive_revision": "3", "drive_content_sha256": "a" * 64}


def approved_test_envelope(duration_s: float, torques=(100.0, 100.0), *, envelope_id="TEST-ONLY-ENVELOPE",
                           **kw) -> RatingEnvelope:
    return RatingEnvelope(
        envelope_id=envelope_id,
        revision="TEST",
        duration_s=duration_s,
        speed_rpm=(1000.0, 2000.0),
        max_motoring_torque_Nm=tuple(torques),
        provenance=Provenance(
            origin=DataOrigin.SUPPLIER,
            source="TEST DOUBLE ONLY; NOT ACTUAL SUPPLIER DATA",
            revision="TEST",
            validation_status="TEST DOUBLE for exercising the approved branch",
        ),
        conditions=tuple(STATED.items()),
        interpolation=kw.pop("interpolation", "linear_declared"),
        approval=RatingApproval(
            state=ApprovalState.APPROVED,
            evidence_id="TEST-ONLY-NOT-A-REAL-APPROVAL",
            evidence_revision="TEST",
            intended_use="unit test of rating inference only",
        ),
        **kw,
    )


# -- the reviewer's regression tests ------------------------------------------------------------------------------

def test_longer_rating_can_establish_a_lower_shorter_request():
    env = approved_test_envelope(30.0)
    claim = duration_claim((env,), 10.0, 1500.0, 80.0, dict(STATED))
    assert claim.status is Status.FEASIBLE


@pytest.mark.parametrize("rated_duration", [30.0, math.inf], ids=["30s", "continuous"])
def test_longer_rating_cannot_exclude_a_higher_shorter_request(rated_duration):
    env = approved_test_envelope(rated_duration)
    claim = duration_claim((env,), 10.0, 1500.0, 150.0, dict(STATED))
    assert claim.status is Status.UNKNOWN


def test_compatible_short_and_long_ratings_are_not_conflicting():
    short = approved_test_envelope(10.0, (160.0, 160.0), envelope_id="TEST-10S")
    long = approved_test_envelope(30.0, (100.0, 100.0), envelope_id="TEST-30S")
    for envs in ((short, long), (long, short)):
        claim = duration_claim(envs, 10.0, 1500.0, 150.0, dict(STATED))
        assert claim.status is Status.FEASIBLE


def test_declared_linear_rating_passes_below_interpolated_limit():
    env = approved_test_envelope(10.0, (200.0, 100.0))
    claim = duration_claim((env,), 10.0, 1500.0, 125.0, dict(STATED))
    assert claim.status is Status.FEASIBLE


def test_declared_linear_rating_fails_above_interpolated_limit():
    env = approved_test_envelope(10.0, (200.0, 100.0))
    claim = duration_claim((env,), 10.0, 1500.0, 175.0, dict(STATED))
    assert claim.status is Status.INFEASIBLE


def _fake_static(monkeypatch, static_segments, unknown_at=()):
    sampled = []

    class FakeEvaluator:
        def __init__(self, *_args, **_kwargs):
            pass

        def solve(self, torque):
            sampled.append(torque)
            accepted = any(a <= torque <= b for a, b in static_segments)
            unknown = any(abs(torque - u) < 1e-9 for u in unknown_at)
            point = SimpleNamespace(Pinv_W=0.0, Pcu_W=1.0, Prot_W=0.0,
                                    inverter_loss_detail=None) if accepted and not unknown else None
            st = Status.UNKNOWN if unknown else (Status.FEASIBLE if accepted else Status.INFEASIBLE)
            return SimpleNamespace(point=point, policy_claim=SimpleNamespace(status=st))

    monkeypatch.setattr(thermal, "PolicyEvaluator", FakeEvaluator)
    monkeypatch.setattr(thermal, "policy_capability", lambda *_a, **_k: SimpleNamespace(segments=static_segments))
    model = thermal.ThermalModel(
        model_id="TEST-ONLY-THERMAL", revision="TEST",
        nodes=(thermal.ThermalNode(node_id="test-winding", network=thermal.FosterNetwork((0.1,), (1.0,)),
                                   limit_C=150.0, loss_share=(("copper", 1.0),)),),
        provenance=Provenance(origin=DataOrigin.SYNTHETIC, source="aggregation unit test only", revision="TEST",
                              validation_status="not physically validated"))
    scenario = SimpleNamespace(coolant_temp_C=65.0, initial_state="equilibrium_at_coolant")
    return sampled, model, scenario


def test_thermal_set_cannot_bridge_a_known_static_gap(monkeypatch):
    static_segments = ((0.0, 10.0), (20.0, 30.0))
    sampled, model, scenario = _fake_static(monkeypatch, static_segments)
    result = thermal.torque_availability(object(), scenario, model, durations_s=(1.0,), samples=9)
    segments = result["rows"][0]["feasible_segments_Nm"]
    assert sampled
    assert not any(a <= 15.0 <= b for a, b in segments)
    assert len(segments) == 2
    for actual, expected in zip(segments, static_segments):
        assert actual == pytest.approx(expected)
    assert result["rows"][0]["feasible_segment_static_index"] == [0, 1]


# -- beyond the reviewer's cases ------------------------------------------------------------------------------------

def test_thermal_set_is_not_joined_across_an_unknown_sample(monkeypatch):
    static_segments = ((0.0, 30.0),)
    sampled, model, scenario = _fake_static(monkeypatch, static_segments, unknown_at=(12.0,))
    result = thermal.torque_availability(object(), scenario, model, durations_s=(1.0,), samples=5)
    segments = result["rows"][0]["feasible_segments_Nm"]
    assert 12.0 in sampled                                            # the grid is 0, 6, 12, 18, 24, 30 N*m
    assert len(segments) == 2 and not any(a <= 12.0 <= b for a, b in segments)
    assert all(0.0 <= a <= b <= 30.0 for a, b in segments)            # inside the one static segment


def test_a_same_duration_rating_still_answers_both_ways_and_conflicts_stay_conflicts():
    tight = approved_test_envelope(10.0, (90.0, 90.0), envelope_id="TEST-10S-90")
    assert duration_claim((tight,), 10.0, 1500.0, 95.0, dict(STATED)).reasons[0] is Reason.RATING_NOT_MET
    long = approved_test_envelope(30.0, (100.0, 100.0), envelope_id="TEST-30S-100")
    # a 30 s rating above a 10 s rating of the same authority: the documents contradict each other
    both = duration_claim((tight, long), 10.0, 1500.0, 95.0, dict(STATED))
    assert both.status is Status.UNKNOWN and Reason.CONFLICTING_EVIDENCE in both.reasons


def test_a_demonstrated_region_is_not_a_limit():
    env = approved_test_envelope(10.0, (100.0, 100.0), limit_semantics="demonstrated_region")
    assert duration_claim((env,), 10.0, 1500.0, 90.0, dict(STATED)).status is Status.FEASIBLE
    above = duration_claim((env,), 10.0, 1500.0, 150.0, dict(STATED))
    assert above.status is Status.UNKNOWN and Reason.RATING_NOT_MET not in above.reasons
    with pytest.raises(Exception, match="limit_semantics"):
        approved_test_envelope(10.0, limit_semantics="typical")


def test_conservative_interpolation_keeps_its_bracket():
    env = approved_test_envelope(10.0, (200.0, 100.0), interpolation="conservative")
    assert duration_claim((env,), 10.0, 1500.0, 100.0, dict(STATED)).status is Status.FEASIBLE
    assert duration_claim((env,), 10.0, 1500.0, 175.0, dict(STATED)).status is Status.UNKNOWN   # inside the bracket
    assert duration_claim((env,), 10.0, 1500.0, 210.0, dict(STATED)).status is Status.INFEASIBLE


def test_rating_bound_to_another_product_does_not_apply():
    env = approved_test_envelope(10.0, (200.0, 200.0), applies_to=(("drive_id", "DRIVE-B"),))
    c = duration_claim((env,), 10.0, 1500.0, 150.0, dict(STATED), PRODUCT)
    assert c.status is Status.UNKNOWN and "another product" in c.detail
    rev = approved_test_envelope(10.0, (200.0, 200.0), applies_to=(("drive_id", "DRIVE-A"), ("drive_revision", "2")))
    assert duration_claim((rev,), 10.0, 1500.0, 150.0, dict(STATED), PRODUCT).status is Status.UNKNOWN


def test_bound_and_complete_rating_is_confirmed_unbound_is_usable_but_says_so():
    bound = approved_test_envelope(10.0, (200.0, 200.0), applies_to=(("drive_id", "DRIVE-A"),
                                                                     ("drive_content_sha256", "a" * 64)))
    ok = duration_claim((bound,), 10.0, 1500.0, 150.0, dict(STATED), PRODUCT)
    assert ok.status is Status.FEASIBLE and not any("not confirmed" in q for q in ok.qualifiers)
    free = approved_test_envelope(10.0, (200.0, 200.0))
    early = duration_claim((free,), 10.0, 1500.0, 150.0, dict(STATED), PRODUCT)
    assert early.status is Status.FEASIBLE                                  # usable for an early review ...
    assert any("APPLICABILITY_UNCONFIRMED" in q and "names no product" in q for q in early.qualifiers)   # ... flagged
    over = duration_claim((free,), 10.0, 1500.0, 250.0, dict(STATED), PRODUCT)
    assert over.status is Status.INFEASIBLE and Reason.APPLICABILITY_UNCONFIRMED in over.reasons


def test_required_conditions_must_be_stated_or_declared_irrelevant():
    base = approved_test_envelope(10.0, (200.0, 200.0), applies_to=(("drive_id", "DRIVE-A"),))
    no_vdc = RatingEnvelope(**{**base.__dict__, "conditions": tuple((k, v) for k, v in STATED.items()
                                                                     if k != "Vdc_V")})
    state, msgs = binding(no_vdc, PRODUCT)
    assert state == "unconfirmed" and "Vdc_V" in msgs[0]
    irrelevant = RatingEnvelope(**{**no_vdc.__dict__, "irrelevant_conditions": ("Vdc_V",)})
    assert binding(irrelevant, PRODUCT) == ("confirmed", [])
    cont = RatingEnvelope(**{**irrelevant.__dict__, "duration_s": math.inf,
                             "conditions": (("coolant_temp_C", 65.0),)})
    assert binding(cont, PRODUCT)[0] == "confirmed"                        # no initial state for a continuous rating


def test_case_file_rating_fields_and_the_decision_record():
    from traction_workbench import service as S
    ex = Path(__file__).resolve().parents[1] / "examples" / "cases" / "req_ts_012_10s_with_example_rating.json"
    case = json.loads(ex.read_text(encoding="utf-8"))
    env = case["ratings"][0]
    assert env["applies_to"]["drive_id"] == "SYNTH_IPMSM_200KW_REF_V1"
    approved = copy.deepcopy(case)
    r = approved["ratings"][0]
    r["provenance"] = {"origin": "supplier", "source": "TEST DOUBLE", "revision": "X1",
                       "validation_status": "test double"}
    r["approval"] = {"state": "approved", "evidence_id": "TEST-ONLY", "evidence_revision": "1",
                     "intended_use": "unit test"}
    rec = S.evaluate_case(approved)
    layers = rec["verdict"]["layers"]
    assert any("not confirmed" in item for item in layers["requirement"]["open_items"])   # initial state not stated
    r["conditions"]["initial_state"] = "equilibrium_at_coolant"
    approved["requirement"]["conditions"]["initial_state"] = "equilibrium_at_coolant"
    rec2 = S.evaluate_case(approved)
    assert not any("not confirmed" in item for item in rec2["verdict"]["layers"]["requirement"]["open_items"])
    other = copy.deepcopy(approved)
    other["ratings"][0]["applies_to"] = {"drive_id": "ANOTHER-DRIVE"}
    rec3 = S.evaluate_case(other)
    dur = rec3["conditions"][0]["duration_claim"]
    assert dur["status"] == "UNKNOWN" and "another product" in dur["detail"]


# -- priority 4: Vdc-range certificate from the low endpoint --------------------------------------------------------

def _synthetic():
    from traction_workbench import spec_fixtures as sf
    return sf.synthetic_drive(), sf.synthetic_limits()


def test_vdc_range_is_certified_from_the_low_endpoint_and_the_theorem_holds_numerically():
    import numpy as np
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    from traction_workbench.scenario import Scenario
    from traction_workbench.solvers.policy import PolicyEvaluator
    drive, lim = _synthetic()
    req = Requirement("R-V", "550..650 V, 100 N*m @ 12000 rpm", 100.0, 12000.0, (550.0, 650.0),
                      Vdc_quantifier="for_all")
    rec = evaluate_requirement(req, drive, source_limits=lim, with_capability=False)
    assert rec.verdict.status is Status.FEASIBLE and rec.range_certificates[0]["applies"]
    assert rec.layers["mathematical"]["status"] == "CERTIFIED"
    assert not any("sampled points only" in a for a in rec.next_actions)
    # independent check of the argument on a dense grid: feasible everywhere, |i| and P_dc never grow with Vdc
    i_prev = p_prev = math.inf
    for v in np.linspace(550.0, 650.0, 21):
        sol = PolicyEvaluator(drive, Scenario("v", 12000.0, float(v), lim)).solve(100.0)
        assert sol.policy_claim.status is Status.FEASIBLE
        assert sol.point.i_peak_A <= i_prev * (1 + 1e-9) and sol.point.Pdc_W <= p_prev * (1 + 1e-9)
        i_prev, p_prev = sol.point.i_peak_A, sol.point.Pdc_W


def test_the_certificate_does_not_cover_regeneration_and_why():
    """Regen: less field-weakening current at a higher Vdc means less loss and MORE charging power - a charge limit
    met at the low endpoint can be exceeded at the high one, so the low endpoint proves nothing there."""
    from dataclasses import replace
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    from traction_workbench.scenario import Scenario
    from traction_workbench.solvers.policy import PolicyEvaluator
    drive, lim = _synthetic()
    free = replace(lim, charge_power_max_W=math.inf)
    p = {v: PolicyEvaluator(drive, Scenario("r", 12000.0, v, free)).solve(-60.0).point.Pdc_W for v in (550.0, 650.0)}
    assert p[650.0] < p[550.0] < 0                                   # more charging power at the higher Vdc
    cap = replace(lim, charge_power_max_W=-0.5 * (p[550.0] + p[650.0]))
    req = Requirement("R-G", "regen over 550..650 V", -60.0, 12000.0, (550.0, 650.0), Vdc_quantifier="for_all")
    rec = evaluate_requirement(req, drive, source_limits=cap, with_capability=False)
    assert rec.conditions[0].requirement_claim.status is Status.FEASIBLE          # the low endpoint passes ...
    assert rec.conditions[-1].requirement_claim.status is Status.INFEASIBLE        # ... the high one does not
    assert rec.verdict.status is Status.INFEASIBLE and not rec.range_certificates[0]["applies"]
    assert any("counterexample" in q for q in rec.qualifiers)


def test_no_certificate_with_a_vdc_dependent_loss_or_a_duration():
    from dataclasses import replace
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    drive, lim = _synthetic()
    req = Requirement("R-V", "range", 100.0, 12000.0, (550.0, 650.0), Vdc_quantifier="for_all")
    narrow = replace(drive, inverter=replace(drive.inverter, loss=replace(drive.inverter.loss, valid_Vdc_V=(500.0, 620.0))))
    rec = evaluate_requirement(req, narrow, source_limits=lim, with_capability=False)
    assert rec.verdict.status is not Status.FEASIBLE
    assert not rec.range_certificates[0]["checks"][1]["holds"]                       # loss validity misses 650 V
    timed = Requirement("R-T", "range 10 s", 100.0, 12000.0, (550.0, 650.0), Vdc_quantifier="for_all", duration_s=10.0)
    rec2 = evaluate_requirement(timed, drive, source_limits=lim, with_capability=False)
    assert rec2.range_certificates[0]["static_certified"] and not rec2.range_certificates[0]["applies"]
    assert rec2.verdict.status is Status.UNKNOWN and any("static part is certified" in q for q in rec2.qualifiers)


# -- multi-plane flux map without a stated magnet temperature: examined for all its temperatures -------------------

def _two_plane_map(interpolation=None):
    from dataclasses import replace
    from traction_workbench import spec_fixtures as sf
    d = sf.manufactured_map_drive()
    cold = replace(d.motor.flux.planes[0], magnet_temp_C=20.0, label="20 degC")
    hot = replace(cold, psi_d_Wb=cold.psi_d_Wb - 0.01, magnet_temp_C=120.0, label="120 degC (PM flux -0.01 Wb)")
    flux = replace(d.motor.flux, planes=(cold, hot), temperature_interpolation=interpolation,
                   temperature_interpolation_basis="test: linear in magnet temperature" if interpolation else "")
    return replace(d, motor=replace(d.motor, flux=flux)), sf.synthetic_limits()


def test_a_multi_plane_map_without_magnet_temperature_is_examined_at_every_plane():
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    drive, lim = _two_plane_map()
    ok = evaluate_requirement(Requirement("R-M", "100 N*m", 100.0, 3000.0, 600.0), drive, source_limits=lim,
                              with_capability=False)
    assert [c.scenario.magnet_temp_C for c in ok.conditions] == [20.0, 120.0]
    assert ok.verdict.status is Status.FEASIBLE                    # every temperature the model has
    assert any("for ALL flux-map temperatures" in q for q in ok.qualifiers)
    assert any("magnet temperature not stated" in x for x in ok.layers["requirement"]["open_items"])
    hot_fail = evaluate_requirement(Requirement("R-M", "125 N*m", 125.0, 3000.0, 600.0), drive, source_limits=lim,
                                    with_capability=False)
    st = {c.scenario.magnet_temp_C: c.requirement_claim.status for c in hot_fail.conditions}
    assert st == {20.0: Status.FEASIBLE, 120.0: Status.INFEASIBLE}
    assert hot_fail.verdict.status is Status.INFEASIBLE
    assert any("magnet 120 degC" in q for q in hot_fail.qualifiers)
    stated = evaluate_requirement(Requirement("R-M", "125 N*m at 20 degC", 125.0, 3000.0, 600.0, magnet_temp_C=20.0),
                                  drive, source_limits=lim, with_capability=False)
    assert len(stated.conditions) == 1 and stated.verdict.status is Status.FEASIBLE


def test_declared_interpolation_is_sampled_between_the_planes_never_certified():
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    drive, lim = _two_plane_map("linear")
    rec = evaluate_requirement(Requirement("R-M", "100 N*m", 100.0, 3000.0, 600.0), drive, source_limits=lim,
                               with_capability=False)
    temps = [c.scenario.magnet_temp_C for c in rec.conditions]
    assert temps[0] == 20.0 and temps[-1] == 120.0 and len(temps) > 2
    assert all(c.requirement_claim.status is Status.FEASIBLE for c in rec.conditions)
    assert rec.verdict.status is Status.UNKNOWN and Reason.SAMPLED_COVERAGE in rec.verdict.reasons


def test_envelope_family_one_exact_envelope_per_plane_without_a_temperature():
    import numpy as np
    from traction_workbench.viz import sweeps as SW
    drive, lim = _two_plane_map()
    assert SW.plane_temperatures(drive) == [20.0, 120.0]
    env, fam, note = SW.envelope_family(drive, lim, 600.0, None, n=5)
    assert [lab for lab, _e in fam] == ["magnet 20 degC", "magnet 120 degC"] and "not stated" in note
    assert not np.isfinite(env["max"]["T_Nm"]).any()                         # no temperature picked for the user
    cold, hot = fam[0][1]["max"]["T_Nm"], fam[1][1]["max"]["T_Nm"]
    assert np.isfinite(cold[1]) and np.isfinite(hot[1]) and hot[1] < cold[1]   # weaker magnets, less low-speed torque
    one, fam1, note1 = SW.envelope_family(drive, lim, 600.0, 120.0, n=5)
    assert fam1 == [] and note1 == "" and np.allclose(one["max"]["T_Nm"], hot, equal_nan=True)
