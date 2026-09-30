"""Functional-safety design work on the causal fault simulation: reaction strategies (soft ASC and the other
transition shapes) in the engine, the controller modes they use, the static design review, design variants as
reviewable changes, the verification matrix over the scenario catalog, the safety-case report and the validation of
engineer-entered values.

Each strategy test runs the same fault from the same initial condition with the hard reaction and with the strategy,
and checks the step sequence the engine logged (actions, exits, times) and the physical effect on the truth (peak
current, d-axis current, braking torque, DC-link voltage)."""

import copy

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.faultsim.campaign import run_one
from traction_workbench.extensions.faultsim.configure import FAULT_SIM_EXAMPLE, apply_overrides, validate_section
from traction_workbench.extensions.faultsim.design import change_rows, diff_overrides, get_at
from traction_workbench.extensions.faultsim.report import criterion_text, safety_case_html
from traction_workbench.extensions.faultsim.review import review_section
from traction_workbench.extensions.faultsim.strategy import (TEMPLATES, StepSpec, StrategySpec, asc_steady_point,
                                                             strategies_from)
from traction_workbench.extensions.faultsim.study import SCENARIOS, independence_data, scenario
from traction_workbench.extensions.faultsim.verification import catalog_for_design, verification_matrix


@pytest.fixture(scope="module")
def product():
    return api.fault_product()


def _run(product, key, **kw):
    sc = copy.deepcopy(scenario(key))
    sc.update(kw)
    return run_one(product, sc, keep_trace=True)


def _log(r):
    return r["result"].summary["strategy_log"]


def _fails(r):
    return sorted(k for k, v in r["verdicts"].items() if v == "FAIL")


# ------------------------------------------------------------------------------------------------ strategies
@pytest.fixture(scope="module")
def cs6k(product):
    """A current-sensor offset at 6000 rpm (software detection): the hard ASC and the soft variants."""
    return {rx: _run(product, "cs_offset", speed_rpm=6000.0, reaction_override=rx)
            for rx in ("asc_low", "SOFT_ASC_V", "SOFT_ASC_I", "FW2_ASC_LOW", "SEQ_ASC_LOW")}


def test_soft_asc_by_voltage_ramp_removes_the_asc_transient(cs6k):
    hard, soft = cs6k["asc_low"]["metrics"], cs6k["SOFT_ASC_V"]["metrics"]
    # the hard short from the operating point: a d-axis current excursion and a braking-torque step
    assert {"TSR-03", "TSR-08", "TSR-09"} <= set(_fails(cs6k["asc_low"]))
    assert hard["i_phase_peak_A"] > 1200 and hard["T_brake_peak_Nm"] > 600
    # the voltage vector ramped to the zero vector first: the short starts where the machine already is
    assert _fails(cs6k["SOFT_ASC_V"]) == []
    assert soft["i_phase_peak_A"] < 0.6 * hard["i_phase_peak_A"]
    assert soft["T_brake_peak_Nm"] < 0.3 * hard["T_brake_peak_Nm"]
    assert soft["i_d_min_A"] > hard["i_d_min_A"] + 400
    log = _log(cs6k["SOFT_ASC_V"])
    assert [(e["step"], e["action"], e["exit"]) for e in log] == [(1, "voltage_ramp", "done"), (2, "asc_low", "none")]
    dt = log[1]["t"] - log[0]["t"]
    ramp = TEMPLATES["voltage_ramp"]["steps"][0]["params"]["ramp_ms"] * 1e-3
    assert ramp - 1e-9 <= dt <= ramp + 0.5e-3                      # 'done' when the ramp ends (next control step)
    assert all(e["channel"] == "sw" and e["fallback"] is None for e in log)


def test_current_preconditioning_ends_by_its_maximum_time_when_the_measurement_is_wrong(cs6k):
    r = cs6k["SOFT_ASC_I"]
    log = _log(r)
    assert [(e["step"], e["action"]) for e in log] == [(1, "current_to_asc"), (2, "asc_low")]
    mx = TEMPLATES["current_to_asc"]["steps"][0]["max_ms"] * 1e-3
    assert log[1]["t"] - log[0]["t"] == pytest.approx(mx, abs=1e-6)  # the offset keeps the error above tol_A
    ev = [e for e in r["result"].events if e["kind"] == "strategy" and "(timeout)" in e["text"]]
    assert len(ev) == 1 and "not reached within 8 ms" in ev[0]["text"]
    # still softer than the hard short: the references moved towards the ASC point before it
    assert r["metrics"]["i_phase_peak_A"] < cs6k["asc_low"]["metrics"]["i_phase_peak_A"] - 300


def test_freewheel_first_and_phase_sequential_asc(cs6k):
    fw = _log(cs6k["FW2_ASC_LOW"])
    assert [(e["action"], e["exit"]) for e in fw] == [("six_switch_off", "time"), ("asc_low", "none")]
    assert fw[1]["t"] - fw[0]["t"] == pytest.approx(2e-3, abs=1e-9)
    assert cs6k["FW2_ASC_LOW"]["metrics"]["i_phase_peak_A"] < cs6k["asc_low"]["metrics"]["i_phase_peak_A"] - 200
    seq = _log(cs6k["SEQ_ASC_LOW"])
    assert [(e["action"], e["exit"]) for e in seq] == [("sequential_asc_low", "done"), ("asc_low", "none")]
    assert seq[1]["t"] > seq[0]["t"]                                  # 'done' once every leg closed on its diode
    assert cs6k["SEQ_ASC_LOW"]["result"].summary["final_bridge"] == "asc_low"
    assert cs6k["SEQ_ASC_LOW"]["metrics"]["i_phase_peak_A"] < cs6k["asc_low"]["metrics"]["i_phase_peak_A"]


def test_a_software_strategy_on_a_hardware_path_falls_back_to_its_bridge_state(product):
    soft = _run(product, "ov_regen", reaction_override="SOFT_ASC_V")
    hard = _run(product, "ov_regen", reaction_override="asc_low")
    log = _log(soft)
    assert len(log) == 1 and log[0]["channel"] == "hw" and log[0]["step"] is None
    assert log[0]["action"] == "asc_low" and "hardware path cannot execute it" in log[0]["fallback"]
    assert soft["result"].summary["final_bridge"] == "asc_low"
    assert soft["metrics"]["v_dc_max_V"] == pytest.approx(hard["metrics"]["v_dc_max_V"], abs=1.0)
    assert _fails(soft) == _fails(hard)


def test_asc_while_fast_then_freewheel_below_the_declared_speed(product):
    # a motor whose speed follows its own inertia (load torque holds it before the fault)
    r = _run(product, "cs_offset", speed_rpm=4000.0, J_kgm2=0.02, T_load_Nm=150.0, horizon_ms=60.0,
             reaction_override="ASC_THEN_6SO")
    log = _log(r)
    assert [(e["action"], e["exit"]) for e in log] == [("asc_low", "speed_below"), ("six_switch_off", "none")]
    tr = r["result"].trace
    t, n = np.asarray(tr["t"]), np.asarray(tr["speed_rpm"])
    i = int(np.searchsorted(t, log[1]["t"]))
    assert n[i] < 3000.0 + 100.0 and n[int(np.searchsorted(t, log[0]["t"]))] > 3500.0
    assert r["result"].summary["final_bridge"] == "six_switch_off"


def test_torque_ramp_down_then_freewheel_takes_the_declared_rate(product):
    r = _run(product, "cs_offset", reaction_override="SOFT_TQ_6SO")
    log = _log(r)
    assert [(e["action"], e["exit"]) for e in log] == [("torque_ramp", "done"), ("six_switch_off", "none")]
    rate = TEMPLATES["torque_ramp"]["steps"][0]["params"]["rate_Nm_per_ms"]
    assert log[1]["t"] - log[0]["t"] == pytest.approx(150.0 / rate * 1e-3, abs=1.2e-3)


def test_a_running_strategy_is_not_restarted_by_a_second_request(product):
    r = _run(product, "sw_short", reaction_override="FW2_ASC_LOW")
    kept = [e for e in r["result"].events if e["kind"] == "reaction_kept"]
    assert kept and "already running on the hw channel (not restarted)" in kept[0]["text"]
    log = _log(r)
    assert [e["action"] for e in log] == ["six_switch_off", "asc_low"]
    assert log[1]["t"] - log[0]["t"] == pytest.approx(2e-3, abs=1e-9)   # the timer of the first request runs on


def test_the_policy_selects_a_strategy_through_a_design_variant(product):
    r = _run(product, "cs_offset", speed_rpm=12000.0, overrides={"policy.rules.4.then": "SOFT_ASC_V"})
    log = _log(r)
    assert [e["action"] for e in log] == ["voltage_ramp", "asc_low"]
    base = _run(product, "cs_offset", speed_rpm=12000.0)
    assert _log(base) == [] and base["result"].summary["final_bridge"] == "asc_low"
    assert r["metrics"]["i_phase_peak_A"] < base["metrics"]["i_phase_peak_A"]


def test_the_asc_steady_point_solves_the_steady_dq_equations():
    for w, Ld, Lq, psi, R in ((600.0, 2e-4, 5e-4, 0.08, 0.01), (2500.0, 1.5e-4, 3.5e-4, 0.06, 0.02),
                              (80.0, 3e-4, 3e-4, 0.1, 0.05)):
        i_d, i_q = asc_steady_point(w, Ld, Lq, psi, R)
        # u_d = R i_d - w Lq i_q = 0, u_q = R i_q + w (Ld i_d + psi) = 0 (the short circuit)
        assert R * i_d - w * Lq * i_q == pytest.approx(0.0, abs=1e-9 * psi * w / R)
        assert R * i_q + w * (Ld * i_d + psi) == pytest.approx(0.0, abs=1e-9 * psi * w / R)
    i_d, i_q = asc_steady_point(1e5, 2e-4, 5e-4, 0.08, 0.01)
    assert i_d == pytest.approx(-0.08 / 2e-4, rel=1e-3) and abs(i_q) < 1.0   # the characteristic current


def test_strategy_declarations_are_validated():
    ok = strategies_from({"strategies": [TEMPLATES["fw_then_asc"]]})
    assert list(ok) == ["FW2_ASC_LOW"] and ok["FW2_ASC_LOW"].fallback_state == "asc_low"
    bad = [
        ({"id": "X", "steps": [{"action": "asc_low", "exit": "none"}, {"action": "six_switch_off"}]}, "exit"),
        ({"id": "X", "steps": [{"action": "six_switch_off", "exit": "time"}, {"action": "asc_low"}]}, "value"),
        ({"id": "X", "steps": [{"action": "voltage_ramp", "exit": "i_below", "value": 5}, {"action": "asc_low"}]},
         "does not apply"),
        ({"id": "X", "steps": [{"action": "warp", "exit": "none"}]}, "action"),
        ({"id": "X", "steps": [{"action": "vdc_hysteresis_low", "exit": "none",
                                "params": {"v_on_V": 700, "v_off_V": 720}}]}, "v_off_V < v_on_V"),
    ]
    for d, word in bad:
        with pytest.raises(InputValidationError) as e:
            strategies_from({"strategies": [d]})
        assert word in str(e.value)
    with pytest.raises(InputValidationError):
        strategies_from({"strategies": [TEMPLATES["fw_then_asc"], TEMPLATES["fw_then_asc"]]})
    st = StrategySpec("S", (StepSpec("six_switch_off", "time", 2.0), StepSpec("asc_low")))
    assert st.static_duration_s() == pytest.approx(2e-3) and not st.needs_software()
    assert StrategySpec("S", (StepSpec("voltage_ramp", "done", max_s=6e-3), StepSpec("asc_low"))).needs_software()


def test_a_reaction_that_names_an_undeclared_strategy_is_refused():
    d = copy.deepcopy(FAULT_SIM_EXAMPLE)
    d["mechanisms"][0]["reaction"] = "NO_SUCH_STRATEGY"
    with pytest.raises(InputValidationError):
        validate_section(d)
    d = copy.deepcopy(FAULT_SIM_EXAMPLE)
    d["policy"]["rules"][4]["then"] = "NO_SUCH_STRATEGY"
    with pytest.raises(InputValidationError):
        validate_section(d)


# ------------------------------------------------------------------------------------------------ static review
def _review(d):
    return review_section(d, independence_data(d))


def _find(rv, status, check, element=None):
    return [f for f in rv["findings"] if f["status"] == status and f["check"] == check
            and (element is None or element in f["element"])]


def test_the_example_design_has_no_contradiction_or_gap_and_names_its_weak_points():
    rv = _review(copy.deepcopy(FAULT_SIM_EXAMPLE))
    assert rv["counts"]["INCONSISTENT"] == 0 and rv["counts"]["MISSING"] == 0
    assert _find(rv, "WARNING", "latent-fault test", "SM-OVSW")          # no latent test declared on purpose
    assert _find(rv, "WARNING", "bounded step", "ASC_THEN_6SO")          # a measured exit without a maximum time
    # the latency bound of the torque monitor: period + debounce + sensor delay + window delay
    row = next(r for r in rv["latency"] if r["mechanism"] == "SM-TQ")
    m = next(x for x in FAULT_SIM_EXAMPLE["mechanisms"] if x["id"] == "SM-TQ")
    assert row["detection_s"] >= (m["period_ms"] + m["params"]["debounce_ms"] + m["params"]["delay_ms"]) * 1e-3
    assert row["detection_s"] <= row["fdti_budget_s"]
    assert [g["sg"] for g in rv["trace"]] == ["SG-01", "SG-02", "SG-03"]
    # budgets that no timing TSR judges on the trajectories are named (FSR-02: component protection, bounds only)
    assert _find(rv, "NOTE", "budgets judged on trajectories", "FSR-02")
    assert _find(rv, "OK", "budgets judged on trajectories", "FSR-01")
    assert rv["trace"][0]["fsr"][0]["timing_tsr"] == ["TSR-04"] and rv["trace"][2]["fsr"][0]["timing_tsr"] == []


def test_the_review_finds_contradictions_between_declared_values():
    d = copy.deepcopy(FAULT_SIM_EXAMPLE)
    rq = d["requirements"]
    rq["safety_goals"][0]["ftti_ms"] = 40.0                              # FSR-01: 20 + 30 ms > 40 ms
    rq["fsr"][1]["asil"] = "A"                                           # FSR-02 below SG-03 (ASIL B)
    next(m for m in d["mechanisms"] if m["id"] == "SM-TQ")["params"]["debounce_ms"] = 25.0   # > FDTI budget 20 ms
    rv = _review(d)
    assert _find(rv, "INCONSISTENT", "budgets vs FTTI", "FSR-01")
    assert _find(rv, "INCONSISTENT", "ASIL inheritance", "FSR-02")
    assert _find(rv, "INCONSISTENT", "detection latency vs FDTI budget", "FSR-01/SM-TQ")
    assert rv["worst"] == "INCONSISTENT" and rv["findings"][0]["status"] == "INCONSISTENT"   # worst first


def test_a_detection_bound_is_a_contradiction_only_when_its_minimum_exceeds_the_budget():
    d = copy.deepcopy(FAULT_SIM_EXAMPLE)
    m = next(x for x in d["mechanisms"] if x["id"] == "SM-TQ")
    # FDTI budget 20 ms: debounce 19 ms + up to one 1 ms period + 1 ms window delay -> 19 ... 21 ms
    m["params"]["debounce_ms"] = 19.0
    rv = _review(d)
    row = next(r for r in rv["latency"] if r["mechanism"] == "SM-TQ")
    assert row["detection_min_s"] == pytest.approx(0.019, abs=1e-6) and row["detection_s"] > row["fdti_budget_s"]
    assert _find(rv, "WARNING", "detection latency vs FDTI budget", "FSR-01/SM-TQ")
    assert not _find(rv, "INCONSISTENT", "detection latency vs FDTI budget")
    m["params"]["debounce_ms"] = 20.5
    assert _find(_review(d), "INCONSISTENT", "detection latency vs FDTI budget", "FSR-01/SM-TQ")


def test_the_review_finds_gaps_and_unexecutable_strategies():
    d = copy.deepcopy(FAULT_SIM_EXAMPLE)
    rq = d["requirements"]
    del rq["safety_goals"][2]["ftti_ms"]
    rq["fsr"][0]["verification"] = []
    rv = _review(d)
    assert _find(rv, "MISSING", "FTTI", "SG-03") and _find(rv, "MISSING", "verification methods", "FSR-01")
    # a software strategy requested by a hardware mechanism degrades to its fallback: a warning
    d = copy.deepcopy(FAULT_SIM_EXAMPLE)
    next(m for m in d["mechanisms"] if m["id"] == "SM-OV")["reaction"] = "SOFT_ASC_V"
    rv = _review(d)
    assert _find(rv, "WARNING", "executable on its paths", "SOFT_ASC_V")
    # a timed strategy longer than the FRTI budget: the reaction cannot meet it even on a gross fault
    d = copy.deepcopy(FAULT_SIM_EXAMPLE)
    next(s for s in d["strategies"] if s["id"] == "FW2_ASC_LOW")["steps"][0]["value"] = 5.0
    next(m for m in d["mechanisms"] if m["id"] == "SM-OVSW")["reaction"] = "FW2_ASC_LOW"
    rv = _review(d)
    # a warning, not a contradiction: the safe condition may already hold during the freewheel step
    assert _find(rv, "WARNING", "reaction latency vs FRTI budget", "FSR-02/SM-OVSW")
    assert not _find(rv, "INCONSISTENT", "reaction latency vs FRTI budget")


# ------------------------------------------------------------------------------------------------ design variants
def test_a_design_variant_is_the_difference_and_applies_back():
    base = copy.deepcopy(FAULT_SIM_EXAMPLE)
    new = copy.deepcopy(base)
    new["requirements"]["safety_goals"][0]["ftti_ms"] = 80.0
    next(t for t in new["requirements"]["tsr"] if t["id"] == "TSR-08")["criterion"]["max"] = 400.0
    next(m for m in new["mechanisms"] if m["id"] == "SM-TQ")["params"]["debounce_ms"] = 5.0
    new["strategies"].append(dict(copy.deepcopy(TEMPLATES["voltage_ramp"]), id="SOFT_ASC_V2"))
    new["policy"]["rules"][4]["then"] = "SOFT_ASC_V"
    new["paths"][0]["delay_us"] = 150.0
    ov = diff_overrides(base, new)
    assert ov["requirements.safety_goals.SG-01.ftti_ms"] == 80.0
    assert ov["mechanisms.SM-TQ.params.debounce_ms"] == 5.0
    assert "strategies" in ov and "policy.rules" in ov                  # lists without stable ids: whole list
    back = apply_overrides(base, ov)
    assert diff_overrides(back, new) == {} and diff_overrides(new, back) == {}
    validate_section(back)
    rows = change_rows(base, ov)
    kinds = {(r["path"], r["change"]) for r in rows}
    assert ("strategies.SOFT_ASC_V2", "added") in kinds                  # a list change shown item by item
    assert ("policy.rules[5]", "changed") in kinds                       # rules have no ids: by position
    assert get_at(base, "mechanisms.SM-TQ.params.debounce_ms") == 3.0


def test_empty_containers_and_missing_keys_are_not_changes():
    base = {"a": {"x": 1}, "lst": [{"id": "S", "steps": [{"action": "asc_low", "exit": "none"}]}]}
    new = copy.deepcopy(base)
    new["a"]["params"] = {}
    new["b"] = []
    new["lst"][0]["steps"][0]["params"] = {}
    new["c"] = None
    assert diff_overrides(base, new) == {}
    new["lst"][0]["steps"][0]["params"] = {"ramp_ms": 2.0}
    assert list(diff_overrides(base, new)) == ["lst.S.steps"]
    assert diff_overrides({"k": [1, 2]}, {})["k"] is None                 # a removed key


def test_removing_and_reordering_items_are_named():
    base = copy.deepcopy(FAULT_SIM_EXAMPLE)
    new = copy.deepcopy(base)
    new["mechanisms"] = [m for m in new["mechanisms"] if m["id"] != "SM-WD"]
    new["mechanisms"].insert(0, new["mechanisms"].pop(2))
    rows = change_rows(base, diff_overrides(base, new))
    assert {"path": "mechanisms.SM-WD", "change": "removed"}.items() <= next(
        r for r in rows if r["path"] == "mechanisms.SM-WD").items()
    assert any(r["change"] == "reordered" and r["path"] == "mechanisms" for r in rows)


# ------------------------------------------------------------------------------------------------ verification
def test_the_catalog_runs_on_one_design_without_its_own_design_variants():
    runs, skipped = catalog_for_design(SCENARIOS, None)
    keys = [r["key"] for r in runs]
    assert "false_trip" not in keys and any(s["key"] == "false_trip" for s in skipped)
    assert all("overrides" not in r["scenario"] and "reaction_override" not in r["scenario"] for r in runs)
    ov = {"mechanisms.SM-TQ.params.debounce_ms": 5.0}
    runs2, _ = catalog_for_design(SCENARIOS, ov)
    assert all(r["scenario"]["overrides"] == ov for r in runs2)


def test_the_verification_matrix_counts_coverage_and_worst_times(product):
    scs = [s for s in SCENARIOS if s["key"] in ("step_ok", "false_trip", "cs_offset", "ov_regen")]
    mx = verification_matrix(product, scs, None)
    assert [r["key"] for r in mx["rows"]] == ["step_ok", "cs_offset", "ov_regen"]
    assert [s["key"] for s in mx["skipped"]] == ["false_trip"]
    for q, c in mx["coverage"].items():
        vs = list(mx["cells"][q].values())
        assert c["exercised"] == c["pass"] + c["fail"] + c["unknown"] == sum(v != "NOT_APPLICABLE" for v in vs)
        assert c["failing_in"] == [k for k, v in mx["cells"][q].items() if v == "FAIL"]
    for fid, d in mx["timing"].items():
        for key, w in d.items():
            row = next(r for r in mx["rows"] if r["key"] == w["scenario"])
            assert row["timelines"][fid][key] == w["value_s"]            # the worst value IS one trajectory's
            assert all((r["timelines"][fid].get(key) or 0.0) <= w["value_s"] for r in mx["rows"])
    fm = {r["key"]: r["fmea"] for r in mx["rows"]}
    assert fm["step_ok"]["fault"] == "no fault" and fm["step_ok"]["detected_by"] is None
    assert fm["cs_offset"]["detected_by"] and fm["cs_offset"]["reaction"]
    assert mx["project"]["project_id"] and "trajectories" in mx["statement"]


# ------------------------------------------------------------------------------------------------ report and API
def test_the_safety_case_report_is_one_self_contained_page(product):
    data = copy.deepcopy(FAULT_SIM_EXAMPLE)
    ind = independence_data(data)
    html = safety_case_html(data, project={"label": "P", "digest": "abc"}, code={"version": "x", "commit": "c"},
                            changes=[{"path": "a.b", "project": 1, "study": 2, "change": "changed"}],
                            review=review_section(data, ind), matrix=None, independence=ind,
                            precision_notes=["note"], counterexamples=[])
    assert html.startswith("<!doctype html>") and "<script" not in html and "http" not in html.split("<main")[0]
    for part in ("1. 평가한 설계", "2. 안전 요구", "3. 정적 설계 검토", "4. 반응 전략", "6. 공통 원인",
                 "8. 가정·한계·미결 사항", "a.b", "SOFT_ASC_V", "TSR-09", "ISO 26262"):
        assert part in html
    assert "5. 검증 매트릭스" not in html                                 # no matrix given: no section
    texts = [criterion_text(t["criterion"]) for t in data["requirements"]["tsr"]]
    assert all(t and "{" not in t for t in texts)


def test_the_api_serves_the_editor_schema_review_matrix_and_report():
    d = api.fault_design()
    assert d["from"] == "project" and {"kind_params", "actions", "exits_for", "templates", "asil", "criteria",
                                       "quantities", "verification"} <= set(d["schema"])
    assert set(d["schema"]["templates"]) == set(TEMPLATES)
    rv = api.fault_review({"overrides": {"mechanisms.SM-TQ.params.debounce_ms": 25.0}})
    assert _find(rv, "INCONSISTENT", "detection latency vs FDTI budget", "SM-TQ")
    with pytest.raises(InputValidationError):
        api.fault_review({"overrides": {"requirements.safety_goals.SG-01.ftti_ms": -1}})
    mx = api.fault_verification({"keys": ["step_ok"]})
    assert [r["key"] for r in mx["rows"]] == ["step_ok"]
    html = api.fault_report({"overrides": {"requirements.safety_goals.SG-01.ftti_ms": 90.0}, "matrix": mx})
    assert "requirements.safety_goals.SG-01.ftti_ms" in html and "5. 검증 매트릭스" in html
    assert api.fault_independence({"overrides": {"mechanisms.SM-TQ.enabled": False}})["fsr"]


# ------------------------------------------------------------------------------------------------ entered values
@pytest.mark.parametrize("edit,field", [
    (lambda d: d["requirements"]["safety_goals"][0].__setitem__("ftti_ms", -5), "ftti_ms"),
    (lambda d: d["requirements"]["safety_goals"][0].__setitem__("ftti_ms", float("nan")), "ftti_ms"),
    (lambda d: d["requirements"]["fsr"][0].__setitem__("fdti_budget_ms", 0), "fdti_budget_ms"),
    (lambda d: d["requirements"]["tsr"][0]["criterion"].__setitem__("tolerance_ms", -1), "tolerance_ms"),
    (lambda d: d["requirements"]["tsr"][0]["criterion"].__setitem__("abs_Nm", "wide"), "abs_Nm"),
    (lambda d: d["requirements"]["safety_goals"][0].__setitem__("asil", "E"), "asil"),
    (lambda d: d["mechanisms"][0]["params"].__setitem__("debounce_ms", -1), "debounce_ms"),
    (lambda d: d["mechanisms"][1]["params"].__setitem__("threshold_A", None), "threshold_A"),
    (lambda d: d["mechanisms"][1]["params"].__setitem__("filter_us", -2), "filter_us"),
    (lambda d: d["mechanisms"][0]["params"].__setitem__("request_input", "radio"), "request_input"),
    (lambda d: d["requirements"]["tsr"].append(copy.deepcopy(d["requirements"]["tsr"][0])), "unique"),
])
def test_engineer_entered_values_are_checked_where_they_are_entered(edit, field):
    d = copy.deepcopy(FAULT_SIM_EXAMPLE)
    edit(d)
    with pytest.raises(InputValidationError) as e:
        validate_section(d)
    assert field in str(e.value) or field in (getattr(e.value, "field", "") or "")
