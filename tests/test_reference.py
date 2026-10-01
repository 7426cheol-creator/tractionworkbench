"""Reference packages: a requirement / scenario catalogue with provenance verified item by item on the synthetic
project - the physical safe-state judge (C1..C4, never a command bit), the fault timeline on one clock, the system
layer (supervisor, re-arm variants, operating states, supplies, torque interface, discharge, second machine, clock and
coupling faults) and the check library, with OPEN values never guessed.

Every test names the behaviour it pins and why it holds on the synthetic architecture.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.faultsim.protection import signed_window, speed_tolerance, window_excess
from traction_workbench.extensions.faultsim.refcheck import CHECKS, _arith, origin_time
from traction_workbench.extensions.faultsim.refexample import REFERENCE_EXAMPLE, SCENARIOS
from traction_workbench.extensions.faultsim.refpkg import ReferenceRunner, load_package, validate_package
from traction_workbench.extensions.faultsim.refreport import decimate_indices, matrix_csv, reference_html
from traction_workbench.extensions.faultsim.safestate import fault_timeline, judge_safe_state

PRODUCT = api.fault_product()


@pytest.fixture(scope="module")
def pkg():
    return load_package(REFERENCE_EXAMPLE)


@pytest.fixture(scope="module")
def runner(pkg):
    """One illustrative runner for the module: runs are cached by the digest of the resolved scenario."""
    return ReferenceRunner(PRODUCT, pkg, profile="illustrative")


@pytest.fixture(scope="module")
def customer(pkg):
    return ReferenceRunner(PRODUCT, pkg, profile="customer")


def verdict(runner, iid):
    return runner.item_result(iid)


def checks(r):
    return [(c["check"], c.get("variant"), c["verdict"]) for c in r["checks"]]


# -------------------------------------------------------------------------------------------- the package

def test_example_package_is_valid_and_keeps_provenance(pkg):
    """The built-in example is a valid twb-reference/1 package: unique ids, every check known, every scenario and
    variant declared; OPEN parameters carry no value; a CONFLICT parameter has two variants."""
    assert pkg["_problems"] == []
    assert validate_package(REFERENCE_EXAMPLE) == []
    for p in pkg["parameters"]:
        if p["provenance"] == "OPEN":
            assert p["value"] is None, p["id"]
    seq = next(p for p in pkg["parameters"] if p["id"] == "SEQ")
    assert len(seq["variants"]) == 2


def test_invalid_package_is_refused():
    bad = copy.deepcopy(REFERENCE_EXAMPLE)
    bad["items"].append(dict(bad["items"][0]))              # a duplicate id
    bad["items"][-1]["checks"] = [{"check": "no_such_check", "params": {}}]
    with pytest.raises(InputValidationError):
        load_package(bad)
    probs = validate_package(bad)
    assert any("unique" in p["text"] for p in probs) and any("no_such_check" in p["text"] for p in probs)


def test_open_value_is_unknown_in_customer_and_flagged_in_illustrative(customer, runner):
    """A check needing an OPEN value is UNKNOWN and names it (customer); the illustrative profile runs it with the
    example value and marks the result - never a customer verdict."""
    rc = customer.item_result("EX-ITF-03")
    assert rc["verdict"] == "UNKNOWN" and "SAFE_VALUE" in rc["open"]
    ri = runner.item_result("EX-ITF-03")
    assert ri["verdict"] == "PASS" and ri["illustrative"] == ["SAFE_VALUE"]


def test_user_value_replaces_open(pkg):
    r = ReferenceRunner(PRODUCT, pkg, profile="customer", values={"SAFE_VALUE": 50.0})
    res = r.item_result("EX-ITF-03")
    assert res["verdict"] == "PASS" and not res["illustrative"] and "SAFE_VALUE" not in res["open"]


# -------------------------------------------------------------------------------------------- physics judges

def test_safe_state_is_judged_on_the_physics(runner):
    """ASC at 12,000 rpm reaches the safe state; six-switch-off at 12,000 rpm rectifies into the DC link (C2), a
    counterexample the check reproduces; a soft ASC is judged with its pulsing steps (C3)."""
    r = runner.item_result("EX-SS-01")
    assert r["verdict"] == "PASS", checks(r)
    assert "counterexample reproduced: C2" in " ".join(r["checks"][1]["reasons"])
    assert runner.item_result("EX-SS-02")["verdict"] == "PASS"


def test_low_dc_voltage_does_not_make_freewheeling_safe(runner):
    """At 50 V and 3000 rpm the back-EMF exceeds the link: six-switch-off keeps charging the battery (C2)."""
    r = runner.item_result("EX-SS-03")
    assert r["verdict"] == "PASS" and "C2" in " ".join(r["checks"][0]["reasons"])


def test_open_tolerance_is_judged_at_zero_with_break_even(customer):
    """Without the tolerances the judge takes zero (the strictest): a pass holds for every value, otherwise the
    break-even tolerance is reported and the verdict is UNKNOWN - never a guessed pass."""
    r = customer.item_result("EX-SS-01")
    assert r["verdict"] == "UNKNOWN"
    assert any("OPEN" in x for c in r["checks"] for x in c["reasons"])


def test_timeline_six_instants_on_one_clock(runner):
    r = runner.item_result("EX-TL-01")
    m = r["checks"][0]["measured"]
    assert r["verdict"] == "PASS"
    order = [m[k] for k in ("t_fault", "t_criterion", "t_detect", "t_reaction_req", "t_gate_applied",
                            "t_physical_safe")]
    assert all(v is not None for v in order) and order == sorted(order)


def test_scenario_command_request_precedes_a_later_detection(runner):
    """A reaction commanded by the scenario is the request even when a battery-management detection follows later
    (the gate time is the command's, not undefined)."""
    run = runner.run({"scenario": "S-REACT", "variant": "fw_12000"})
    tl = fault_timeline(run.result)
    assert tl["t_reaction_req"] == pytest.approx(2e-3) and tl["t_gate_applied"] == pytest.approx(2.005e-3, abs=1e-6)


def test_condition_subset_no_torque_mode_is_c3_only(runner):
    """A no-torque target mode forbids torque by active pulsing (C3) - not the full safe state."""
    r = runner.item_result("EX-SS-04")
    assert r["verdict"] == "PASS"
    run = runner.run({"scenario": "S-MODE", "variant": "no_torque"})
    j = judge_safe_state(run.result, 0.02, torque_tol_Nm=5.0, power_tol_W=500.0, conditions=("C3",))
    assert set(j["conditions"]) == {"C3"}


# -------------------------------------------------------------------------------------------- torque window

def test_signed_window_formula_holds_for_every_factor_and_tolerance(runner):
    r = runner.item_result("EX-TQ-04")
    assert r["verdict"] == "PASS"
    assert r["checks"][0]["measured"]["mismatches"] == 0 and r["checks"][0]["measured"]["sign_flips"] > 0


def test_signed_window_pure_functions():
    """Regeneration keeps its sign: a positive actual torque against a negative request violates; the tolerance
    map interpolates over |speed| and is flat outside."""
    hi, lo, _c = signed_window(-100.0, {"limit_factor": 1.2, "abs_Nm": 0.0})
    assert hi == pytest.approx(-100 / 1.2) and lo == pytest.approx(-120.0)
    x_hi, _x_lo = window_excess(100.0, 0.0, hi, lo, {})
    assert x_hi > 0
    tm = [[0.0, 5.0], [6000.0, 7.5]]
    assert speed_tolerance(-3000.0, {"tol_map": tm}) == pytest.approx(6.25)
    assert speed_tolerance(9000.0, {"tol_map": tm}) == pytest.approx(7.5)
    assert _arith("max(T*F, T/F) + A", {"T": -10, "F": 2, "A": 1}) == pytest.approx(-4.0)
    with pytest.raises(ValueError):
        _arith("__import__('os')", {})


def test_time_and_integral_monitors_and_the_blind_spot(runner):
    r = runner.item_result("EX-TQ-02")
    assert [v for _c, _v, v in checks(r)] == ["PASS", "PASS", "PASS"]


def test_sweep_reports_the_break_even_of_an_open_value(customer, runner):
    """The small long deviation is detected for small limit factors and missed for large ones: the customer profile
    reports the passing values (UNKNOWN - the value is OPEN), the illustrative profile judges at its example value."""
    rc = customer.item_result("EX-TQ-05")
    c = rc["checks"][0]
    assert rc["verdict"] == "UNKNOWN"
    per = {tuple(x["values"]): x["verdict"] for x in c["measured"]["sweep"]}
    assert per[(1.05,)] == "PASS" and per[(1.6,)] == "FAIL"
    ri = runner.item_result("EX-TQ-05")
    assert ri["verdict"] == "PASS" and "LIMIT_FACTOR" in ri["illustrative"]


def test_received_envelope_and_independent_bound(runner):
    r = runner.item_result("EX-TQ-06")
    assert [v for _c, _v, v in checks(r)] == ["PASS", "PASS", "PASS"]


# -------------------------------------------------------------------------------------------- system layer

def test_rearm_contract_and_non_approved_variants(runner):
    assert runner.item_result("EX-SUP-02")["verdict"] == "PASS"
    assert runner.item_result("EX-SUP-03")["verdict"] == "PASS"


def test_isolated_supply_condition_is_not_a_default_error(runner):
    assert runner.item_result("EX-SUP-04")["verdict"] == "PASS"


def test_operating_states_guards_and_allow_list(runner):
    for iid in ("EX-OPS-01", "EX-OPS-03", "EX-OPS-04", "EX-OPS-05"):
        assert runner.item_result(iid)["verdict"] == "PASS", (iid, checks(runner.item_result(iid)))


def test_supply_transfer_gap_and_redundant_source(runner):
    """A seamless transfer keeps the gate rail; a 1 ms gap longer than the 0.5 ms hold-up loses it; a failed
    HV-derived source is flagged while the LV source keeps the drive running."""
    for iid in ("EX-PW-01", "EX-PW-03", "EX-PW-04", "EX-PW-05"):
        assert runner.item_result(iid)["verdict"] == "PASS", (iid, checks(runner.item_result(iid)))


def test_clock_and_coupling_faults(runner):
    """A stopped MCU clock is caught by the external watchdog, a 50 % drift is not (a timeout watchdog's blind
    spot); an opened coupling lets the machine run away into the overspeed monitor unless the position freezes."""
    assert runner.item_result("EX-CLK-01")["verdict"] == "PASS"
    assert runner.item_result("EX-OS-01")["verdict"] == "PASS"
    run = runner.run({"scenario": "S-CLOCK", "variant": "drift_half"})
    ctrl = [e["t"] for e in run.result.events if e["kind"] == "controller" and e["source"] == "clock"]
    assert ctrl and ctrl[0] == pytest.approx(0.01)


def test_hardware_paths(runner):
    for iid in ("EX-HW-02", "EX-HW-03", "EX-HW-04", "EX-HW-05"):
        assert runner.item_result(iid)["verdict"] == "PASS", (iid, checks(runner.item_result(iid)))


def test_gde_sequences_kept_as_variants(runner):
    """The two recorded GDE sequences: both reach the ASC within 150 us, but the second ends in six-switch-off at
    12,000 rpm (C2) - the variants give different definite verdicts: CONFLICT, never one chosen silently."""
    r = runner.item_result("EX-HW-01")
    assert r["verdict"] == "CONFLICT"
    c = next(c for c in r["checks"] if c["check"] == "safe_state")
    assert {p["variant"]: p["verdict"] for p in c["variants"]} == {"SEQ_A": "PASS", "SEQ_B": "FAIL"}


def test_interface_regeneration_and_vehicle(runner):
    for iid in ("EX-ITF-01", "EX-ITF-02", "EX-REG-01", "EX-VEH-01", "EX-VEH-02"):
        assert runner.item_result(iid)["verdict"] == "PASS", (iid, checks(runner.item_result(iid)))


def test_discharge_active_and_passive(runner):
    assert runner.item_result("EX-DIS-01")["verdict"] == "PASS"
    r = runner.item_result("EX-DIS-02")
    assert r["verdict"] == "PASS" and r["checks"][0]["measured"]["t_s"] == pytest.approx(
        100e3 * 500e-6 * np.log(600 / 60), rel=1e-6)


# -------------------------------------------------------------------------------------------- package checks

def test_open_kept_coverage_question_and_interfaces(runner):
    assert runner.item_result("EX-META-02")["verdict"] == "PASS"
    assert runner.item_result("EX-MOD-02")["verdict"] == "PASS"
    q = runner.item_result("EX-Q-01")
    assert q["verdict"] == "MANUAL" and q["checks"][0]["measured"]["waiting_items"] > 0


def test_origin_forms(runner):
    run = runner.run({"scenario": "S-RESET", "variant": "rotating"})
    assert origin_time(run, "event::MCU booted") is not None
    assert origin_time(run, "event:fault:mcu_reset") == pytest.approx(0.01)
    assert origin_time(run, 12.5) == pytest.approx(0.0125)


def test_a_broken_check_never_stops_the_others(pkg):
    bad = copy.deepcopy(REFERENCE_EXAMPLE)
    bad["items"] = [{"id": "X-1", "title": "x", "provenance": "DERIVED",
                     "checks": [{"check": "no_pwm", "scenario": "S-RESET", "variant": "rotating",
                                 "params": {"from": "event:nothing:never"}}]}]
    r = ReferenceRunner(PRODUCT, load_package(bad)).run_all()
    assert r["rows"][0]["verdict"] == "NOT_APPLICABLE"


def test_every_check_is_used_by_the_example():
    used = {c["check"] for it in REFERENCE_EXAMPLE["items"] for c in it.get("checks") or []}
    assert set(CHECKS) - used == set(), sorted(set(CHECKS) - used)


def test_every_example_scenario_builds():
    from traction_workbench.extensions.faultsim.configure import build_setup
    r = ReferenceRunner(PRODUCT, load_package(REFERENCE_EXAMPLE), profile="illustrative")
    for sid, s in SCENARIOS.items():
        for v in [None] + list(s.get("variants") or {}):
            build_setup(PRODUCT, r.scenario_dict(sid, v))


# -------------------------------------------------------------------------------------------- outputs

def test_api_run_report_and_csv(tmp_path):
    body = {"profile": "illustrative", "ids": ["EX-SS-01", "EX-HW-01", "EX-META-01"]}
    res = api.reference_run(body)
    assert [r["id"] for r in res["rows"]] == ["EX-SS-01", "EX-HW-01", "EX-META-01"]
    assert res["conflicts"] and res["conflicts"][0]["id"] == "EX-HW-01"
    key = res["rows"][0]["checks"][0]["evidence"]["run"]
    ev = res["evidence_runs"][key]
    assert len(ev["trace"]["t"]) <= 3100 and ev["timeline"]["t_gate_applied"] is not None
    html = api.reference_html(res)
    assert "<html" in html and "EX-HW-01" in html and "CONFLICT" in html
    csv = api.reference_csv(res)
    assert csv.splitlines()[0].startswith("item,group") and "EX-SS-01" in csv
    json.dumps({k: v for k, v in res.items() if k != "evidence_runs"}, default=str)


def test_decimation_keeps_the_peaks():
    t = np.linspace(0, 1, 200001)
    y = np.sin(40 * t)
    y[123457] = 50.0
    idx = decimate_indices(t, [y], 2000)
    assert len(idx) <= 2100 and 123457 in idx and idx[0] == 0 and idx[-1] == len(t) - 1


def test_html_and_csv_from_a_summary_without_runs():
    s = {"rows": [], "counts": {}, "package": {"title": "t"}, "profile": "customer"}
    assert "<h1>t</h1>" in reference_html(s)
    assert matrix_csv(s).startswith("item,")


# -------------------------------------------------------------------------------------------- hierarchy

def test_example_hierarchy_levels_traces_rollup_and_gaps(pkg):
    from traction_workbench.extensions.faultsim.refhier import REQ_LEVELS, hierarchy, level_of
    assert all(it.get("level") for it in REFERENCE_EXAMPLE["items"])            # the example states every level
    h = hierarchy(pkg)
    assert h["roots"] == ["EX-SG-01", "EX-SG-02"] and not h["unknown_refs"]
    kids = {c for c, _b in h["children"]["EX-TLSR-02"]}
    assert {"EX-SS-01", "EX-TQ-01"} <= kids
    assert ("EX-SM-01", "source") in [tuple(x) for x in h["children"]["EX-HW-02"]]
    assert [g["code"] for g in h["gaps"] if g["id"] == "EX-TLSR-03"] == ["text_missing"]
    # the roll-up: a subtree takes its worst verdict, MANUAL only when nothing below was checked
    rows = [{"id": "EX-HW-01", "verdict": "FAIL"}, {"id": "EX-HW-04", "verdict": "PASS"},
            {"id": "EX-SS-01", "verdict": "PASS"}, {"id": "EX-SG-01", "verdict": "MANUAL"}]
    h2 = hierarchy(pkg, rows)
    assert h2["rollup"]["EX-HW-01"]["verdict"] == "FAIL" and h2["rollup"]["EX-SG-01"]["verdict"] == "FAIL"
    assert any(g["id"] == "EX-SS-01" and g["code"] == "failing_below" for g in h2["gaps"])
    assert level_of({"kind": "tsr"}) == ("TSR", True) and "VER" in REQ_LEVELS


def test_a_proposal_is_derived_and_never_a_customer_requirement(pkg):
    bad = copy.deepcopy(REFERENCE_EXAMPLE)
    p = next(it for it in bad["items"] if it["id"] == "EX-PROP-01")
    p["provenance"] = "CONFIRMED"
    assert any(x["level"] == "error" and "proposal" in x["text"] for x in validate_package(bad))
    bad = copy.deepcopy(REFERENCE_EXAMPLE)
    bad["items"][0]["level"] = "XYZ"
    bad["items"][1]["traces_to"] = ["NOPE"]
    probs = validate_package(bad)
    assert any("unknown level" in x["text"] for x in probs) and any("NOPE" in x["text"] for x in probs)


def test_the_cycling_proposal_is_demonstrated_and_its_fix_holds(runner, customer):
    r = runner.item_result("EX-PROP-01")
    assert r["verdict"] == "PASS"
    cyc = r["checks"][0]["measured"]
    assert cyc["count"] >= 6 and 5.0 < cyc["median_period_ms"] < 12.0          # ~7.7 ms at 6000 rpm
    assert r["checks"][3]["measured"]["max"] < 60.0                             # the ASC held: below 60 V
    assert customer.item_result("EX-PROP-01")["verdict"] == "UNKNOWN"          # X_UPP is OPEN
    s = runner.summary(["EX-PROP-01", "EX-DIS-01"])
    assert s["counts"].get("proposed") or s["counts"].get("illustrative")
    row = next(x for x in s["rows"] if x["id"] == "EX-PROP-01")
    assert row["proposed"] and not row["customer"] and row["level"] == "TSR" and row["rationale"]
    assert "EX-PROP-01" in s["hierarchy"]["gaps"][0]["proposals"] or any(
        c == "EX-PROP-01" for c, _b in s["hierarchy"]["children"]["EX-DIS-01"])


def test_the_customer_package_is_shipped_classified_and_traced():
    from traction_workbench.extensions.faultsim.refhier import hierarchy
    raw = api.reference_builtin_package("customer_inverter")
    pkg = api.reference_load(raw)
    assert not [p for p in pkg["_problems"] if p["level"] == "error"]
    items = {it["id"]: it for it in pkg["items"]}
    assert len(items) >= 370 and all(it.get("level") for it in items.values())
    h = hierarchy(pkg)
    assert {"A-02", "A-03", "A-07", "PROP-SG-HV"} <= set(h["roots"])
    assert items["WI-14677"]["level"] == "TLSR" and items["TLSR-08"]["provenance"] == "OPEN"
    assert "WI-14714" in items["TSR-FRONT-02"]["traces_to"]                     # the number the source cites
    assert "WI-14712" in items["SM-I-01"]["traces_to"]
    assert "WI-14716" in items["C-02"].get("traces_to_inferred", [])            # inferred by topic, marked
    props = [i for i, it in items.items() if it.get("proposed")]
    assert len(props) >= 8 and all(items[i]["provenance"] == "DERIVED" for i in props)
    codes = {g["code"] for g in h["gaps"]}
    assert {"text_missing", "not_refined", "not_verified"} <= codes
    assert not h["unknown_refs"]


def test_hierarchy_in_the_html_and_the_csv():
    res = api.reference_run({"profile": "illustrative", "ids": ["EX-SG-01", "EX-TLSR-01", "EX-SS-01"]})
    html = api.reference_html(res)
    assert "Hierarchy" in html and "EX-TLSR-01" in html and "Gaps the structure shows" in html
    head = api.reference_csv(res).splitlines()[0].split(",")
    assert {"level", "traces_to", "traces_to_inferred", "proposed"} <= set(head)
