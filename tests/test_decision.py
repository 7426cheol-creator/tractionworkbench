"""End-to-end decision records: traceability, AND aggregation, reproducibility."""

import json
import math

import pytest

from conftest import scenario
from traction_workbench.analysis.rating import RatingEnvelope
from traction_workbench.decision import evaluate_requirement
from traction_workbench.models import DataOrigin, Provenance
from traction_workbench.requirement import Requirement
from traction_workbench.status import Status

TEXT = "REQ-TS-012: inverter DC terminal 600 V, motor shaft 12,000 rpm에서 shaft torque 150 N·m를 유지한다."


def req(**kw):
    base = dict(req_id="REQ-TS-012", text=TEXT, target_Nm=150, speed_rpm=12000, Vdc_V=600)
    base.update(kw)
    return Requirement(**base)


def test_req_ts_012_static_pass_with_scope(drive, limits):
    rec = evaluate_requirement(req(), drive, source_limits=limits)
    assert rec.verdict.status is Status.FEASIBLE
    assert "static fundamental steady state" in rec.verdict_scope and "synthetic" in rec.verdict_scope
    cr = rec.conditions[0]
    assert cr.torque_margin_Nm == pytest.approx(2.555, abs=1e-3)
    assert any("not current" in lf for lf in rec.limiting_factors)
    d = rec.to_dict()
    assert d["requirement"]["original_text"] == TEXT                  # wording preserved verbatim
    assert d["requirement"]["duration"].startswith("not stated")
    assert any("duration" in u for u in d["not_evaluated"])


def test_450v_fail_with_evidence_and_action(drive, limits):
    rec = evaluate_requirement(req(Vdc_V=450), drive, source_limits=limits)
    assert rec.verdict.status is Status.INFEASIBLE
    assert any("larger current rating cannot fix it" in a for a in rec.next_actions)
    assert any("necessary condition violated" in lf for lf in rec.limiting_factors)


def test_record_is_reproducible(drive, limits):
    a = evaluate_requirement(req(), drive, source_limits=limits)
    b = evaluate_requirement(req(), drive, source_limits=limits)
    assert a.input_sha256 == b.input_sha256
    assert a.to_json() == b.to_json()
    c = evaluate_requirement(req(target_Nm=149), drive, source_limits=limits)
    assert c.input_sha256 != a.input_sha256


def test_markdown_render(drive, limits):
    md = evaluate_requirement(req(), drive, source_limits=limits).to_markdown()
    assert "Verdict: PASS" in md and TEXT in md and "Voltage budget" in md and "Next actions" in md


def _rating(duration=10.0, table=(330.0, 330.0, 200.0, 160.0), conditions=(("coolant_temp_C", 65.0),),
            irrelevant=("Vdc_V", "initial_state")):
    # every required condition is stated or declared irrelevant: a rating with open conditions decides nothing
    # (engineering review 2 of 63a2b61, F-15)
    from traction_workbench.analysis.rating import ApprovalState, RatingApproval
    prov = Provenance(DataOrigin.SUPPLIER, "test envelope", "A", "supplier-rated (test data)")
    return RatingEnvelope("ENV-10S", "A", duration, (0.0, 6000.0, 9000.0, 12000.0), table, prov,
                          conditions=conditions, condition_tolerances=(("coolant_temp_C", 1.0),),
                          irrelevant_conditions=irrelevant,
                          approval=RatingApproval(ApprovalState.APPROVED, "TEST-RS-1", "A",
                                                  "rating for requirement verification (test)"))


def test_duration_needs_matching_envelope(drive, limits):
    r10 = req(duration_s=10.0, coolant_temp_C=65.0)
    rec = evaluate_requirement(r10, drive, source_limits=limits, ratings=(_rating(),))
    assert rec.conditions[0].duration.status is Status.FEASIBLE
    assert rec.verdict.status is Status.FEASIBLE
    # coolant not stated -> cannot confirm the envelope conditions
    rec2 = evaluate_requirement(req(duration_s=10.0), drive, source_limits=limits, ratings=(_rating(),))
    assert rec2.conditions[0].duration.status is Status.UNKNOWN and rec2.verdict.status is Status.UNKNOWN
    # different duration -> no envelope
    rec3 = evaluate_requirement(req(duration_s=30.0, coolant_temp_C=65.0), drive, source_limits=limits,
                                ratings=(_rating(),))
    assert rec3.conditions[0].duration.reasons[0].value == "UNVALIDATED_DURATION"
    # open required conditions (Vdc, initial state neither stated nor declared irrelevant): not a rating for it
    rec4 = evaluate_requirement(r10, drive, source_limits=limits, ratings=(_rating(irrelevant=()),))
    assert rec4.conditions[0].duration.status is Status.UNKNOWN
    assert rec4.conditions[0].duration.reasons[0].value == "APPLICABILITY_UNCONFIRMED"


def test_duration_envelope_violation_fails(drive, limits):
    rec = evaluate_requirement(req(duration_s=10.0, coolant_temp_C=65.0), drive, source_limits=limits,
                               ratings=(_rating(table=(330.0, 330.0, 150.0, 120.0)),))
    assert rec.conditions[0].duration.status is Status.INFEASIBLE
    assert rec.verdict.status is Status.INFEASIBLE


def test_and_aggregation_unknown_does_not_hide_fail(drive, limits):
    rec = evaluate_requirement(req(Vdc_V=450, duration_s=10.0), drive, source_limits=limits)
    assert rec.verdict.status is Status.INFEASIBLE      # proven violation wins over the unknown duration


def test_band_operator(drive, limits):
    # 153.5 N*m is above the 152.555 N*m capability, but a +-1.5 N*m band contains the capability
    rec = evaluate_requirement(req(target_Nm=153.5, operator="band", band_Nm=1.5), drive, source_limits=limits)
    assert rec.verdict.status is Status.FEASIBLE
    rec2 = evaluate_requirement(req(target_Nm=153.5), drive, source_limits=limits)
    assert rec2.verdict.status is Status.INFEASIBLE


def test_missing_dc_limits_is_unknown(drive):
    from traction_workbench.scenario import DcSourceLimits
    rec = evaluate_requirement(req(), drive, source_limits=DcSourceLimits())
    assert rec.verdict.status is Status.UNKNOWN
    assert "MISSING_INPUT" in [r.value for r in rec.verdict.reasons]


def test_temperature_mismatch_is_unknown(drive, limits):
    rec = evaluate_requirement(req(winding_temp_C=150.0), drive, source_limits=limits)
    assert rec.verdict.status is Status.UNKNOWN
    assert any("reference temperature is not declared" in a for a in rec.next_actions)


def test_speed_outside_declared_domain(drive, limits):
    rec = evaluate_requirement(req(speed_rpm=17000, target_Nm=20), drive, source_limits=limits)
    assert rec.verdict.status is Status.INFEASIBLE
    c = rec.conditions[0].solution.electrical
    assert c.reasons[0].value == "OUTSIDE_ALLOWED_OPERATING_DOMAIN"
    assert "not a rotor-strength" in c.detail
