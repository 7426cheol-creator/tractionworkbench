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
