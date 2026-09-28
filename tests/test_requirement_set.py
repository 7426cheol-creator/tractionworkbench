"""Requirement sets (engineering review 6198099, user features 1 - 4): many requirements on one product with the same
conditions and evidence, the class of every open answer, candidates re-judged against every requirement, and the
model-free necessary condition from the customer numbers alone."""

import csv
import io
import math

import pytest

from traction_workbench import requirement_set as RS
from traction_workbench import spec_fixtures as sf
from traction_workbench.decision import evaluate_requirement
from traction_workbench.errors import InputValidationError
from traction_workbench.status import Reason


@pytest.fixture(scope="module")
def base():
    drive, lim = sf.synthetic_drive(), sf.synthetic_limits()
    reqs = RS.parse_requirements_csv(RS.CSV_TEMPLATE, drive.motor.pole_pairs)
    return drive, lim, reqs, RS.evaluate_set(reqs, drive, lim, with_capability=True)


def test_the_template_parses_through_the_case_file_parser():
    reqs = RS.parse_requirements_csv(RS.CSV_TEMPLATE, 4)
    by = {r.req_id: r for r in reqs}
    assert list(by) == ["REQ-A", "REQ-B", "REQ-C", "REQ-D", "REQ-E"]
    assert by["REQ-C"].is_range and by["REQ-C"].Vdc_V == (550.0, 650.0) and by["REQ-C"].Vdc_quantifier == "for_all"
    assert by["REQ-D"].duration_s == 10.0 and by["REQ-D"].initial_state == "equilibrium_at_coolant"
    assert by["REQ-D"].coolant_temp_C == 65.0
    assert by["REQ-E"].operator == "band" and by["REQ-E"].band_Nm == 10.0
    assert by["REQ-B"].target_Nm == -100.0


@pytest.mark.parametrize("text, match", [
    ("id,torque_Nm,speed_rpm,Vdc_V,rpm_kind\nX,1,1,600,mech\n", "unknown column"),
    ("id,torque_Nm,speed_rpm,Vdc_V\nX,1,1,600\nX,2,1,600\n", "duplicate"),
    ("id,torque_Nm,speed_rpm,Vdc_V\nX,,1,600\n", "torque_Nm is required"),
    ("id,torque_Nm,speed_rpm,Vdc_V\nX,abc,1,600\n", "not a number"),
    ("id,torque_Nm,speed_rpm,Vdc_V,operator\nX,1,1,600,exceed\n", "operator"),
    ("id,torque_Nm,speed_rpm,Vdc_V,operator\nX,1,1,600,band\n", "band_Nm is required"),
    ("id,torque_Nm,speed_rpm,Vdc_V,Vdc_max_V\nX,1,1,650,550\n", "low, high"),
    ("id,torque_Nm,speed_rpm,Vdc_V,Vdc_port\nX,1,1,600,battery_terminal\n", "inverter DC terminal"),
    ("id,torque_Nm,speed_rpm,Vdc_V,speed_kind\nX,1,1,600,wheel\n", "speed_kind"),
    ("id,torque_Nm,speed_rpm,Vdc_V\n", "no rows"),
])
def test_ambiguous_or_wrong_rows_are_refused_before_any_calculation(text, match):
    with pytest.raises(InputValidationError, match=match):
        RS.parse_requirements_csv(text, 4)


def test_electrical_speed_is_converted_with_the_pole_pairs():
    r, = RS.parse_requirements_csv("id,torque_Nm,speed_rpm,Vdc_V,speed_kind\nX,100,48000,600,electrical\n", 4)
    assert r.speed_rpm == pytest.approx(12000.0)
    with pytest.raises(InputValidationError):
        RS.parse_requirements_csv("id,torque_Nm,speed_rpm,Vdc_V,speed_kind\nX,100,48000,600,electrical\n", None)


def test_every_reason_is_classed_or_is_a_violation_reason():
    classed = {r for c in RS.REASON_CLASSES for r in c[4]}
    violations = {Reason.CONSTRAINT_VIOLATION, Reason.NECESSARY_CONDITION_VIOLATED, Reason.RATING_NOT_MET}
    assert classed | violations == set(Reason) and not classed & violations
    assert len(classed) == sum(len(c[4]) for c in RS.REASON_CLASSES)          # each reason in exactly one class


def test_classify_keeps_every_cause_of_an_open_answer():
    c = RS.classify("UNKNOWN", [Reason.SAMPLED_COVERAGE, Reason.MISSING_INPUT])
    assert c["class"] == "missing_input" and c["classes"] == ["missing_input", "continuous_range"]
    assert RS.classify("INFEASIBLE", [Reason.RATING_NOT_MET])["class"] == "not_rated"
    assert RS.classify("INFEASIBLE", [Reason.RATING_NOT_MET, Reason.CONSTRAINT_VIOLATION])["class"] == "violation"
    assert RS.classify("FEASIBLE", [])["class"] == "pass"
    assert RS.classify("UNKNOWN", [Reason.APPLICABILITY_UNCONFIRMED])["class"] == "applicability"


def test_the_set_is_the_single_requirement_decision_row_by_row(base):
    drive, lim, reqs, res = base
    assert res["summary"] == {**res["summary"], "PASS": 3, "FAIL": 1, "UNKNOWN": 1, "total": 5}
    rows = {r["id"]: r for r in res["rows"]}
    assert rows["REQ-B"]["class"] == "violation" and rows["REQ-D"]["class"] == "applicability"
    assert rows["REQ-A"]["margin_Nm"] > 0 > rows["REQ-B"]["margin_Nm"]
    # same product, same evidence: each row is exactly the record the single-requirement decision gives
    for req, row, rec in zip(reqs, res["rows"], res["records"]):
        one = evaluate_requirement(req, drive, source_limits=lim, with_capability=True)
        assert one.record_id == row["record_id"] == rec.record_id
        assert one.verdict.status.value == row["status"]
    assert res["drive"]["drive_id"] == drive.drive_id and "qualification" in res["note"]


def test_rows_say_how_the_requirement_was_read_and_what_could_change_it(base):
    rows = {r["id"]: r for r in base[3]["rows"]}
    e = rows["REQ-E"]["interpretation"]
    assert "existence" in e["operator"] and "not control accuracy" in e["operator"]
    assert "for ALL values" in rows["REQ-C"]["interpretation"]["Vdc"]
    assert "not decided" in rows["REQ-A"]["interpretation"]["duration"]
    assert {x["parameter"] for x in rows["REQ-B"]["levers"]} >= {"charge_power_max_W"}
    assert {x["parameter"] for x in rows["REQ-A"]["levers"]} >= {"voltage_reserve_fraction"}
    assert all(x["parameter"] != "Vdc_V" for r in rows.values() for x in r["levers"])     # Vdc is a condition


def test_next_data_is_prioritised_by_effort_and_reach():
    rows = [{"id": "R1", "verdict": "UNKNOWN", "class": "missing_input", "classes": ["missing_input"]},
            {"id": "R2", "verdict": "UNKNOWN", "class": "applicability", "classes": ["applicability"]},
            {"id": "R3", "verdict": "UNKNOWN", "class": "applicability",
             "classes": ["applicability", "continuous_range"]},
            {"id": "R4", "verdict": "PASS", "class": "pass", "classes": []}]
    pr = RS.next_data_priorities(rows)
    assert [p["class"] for p in pr] == ["missing_input", "applicability", "continuous_range"]
    app = pr[1]
    assert app["requirements"] == ["R2", "R3"] and app["settles"] == ["R2"]
    assert app["also_needs"] == {"R3": ["continuous_range"]}         # closing one cause alone does not settle R3


def test_candidates_are_rejudged_against_every_requirement(base):
    drive, lim, reqs, res = base
    out = RS.evaluate_candidates(reqs, drive, lim, [
        {"name": "charge 150 kW", "changes": {"charge_power_max_W": 150000.0, "charge_current_max_A": 400.0}},
        {"name": "current 250 A", "changes": {"I_peak_max_A": 250.0}},
        {"name": "psi", "changes": {"psi_pm_Wb": 0.08}}], baseline=res)
    by = {c["name"]: c for c in out["candidates"]}
    assert by["charge 150 kW"]["improves"] == ["REQ-B"] and not by["charge 150 kW"]["worsens"]
    assert set(by["current 250 A"]["worsens"]) == {"REQ-A", "REQ-C", "REQ-D", "REQ-E"}   # never averaged away
    assert by["psi"]["diagnostic_only"] and not by["charge 150 kW"]["diagnostic_only"]
    assert not any("score" in k for c in out["candidates"] for k in c)                    # no weighted score
    assert "no cost" in out["note"]


@pytest.mark.parametrize("changes, match", [({"Vdc_V": 700.0}, "condition of each requirement"),
                                            ({"magic": 1.0}, "unknown candidate parameter"),
                                            ({"I_peak_max_A": math.nan}, "finite")])
def test_candidate_changes_that_are_not_design_changes_are_refused(changes, match):
    with pytest.raises(InputValidationError, match=match):
        RS.candidate_changes(changes)


def test_the_customer_numbers_alone_give_a_necessary_condition():
    lim = sf.synthetic_limits()                                  # 200 kW / 400 A discharge, 100 kW / 200 A charge
    one = lambda text: RS.parse_requirements_csv("id,torque_Nm,speed_rpm,Vdc_V,Vdc_max_V,operator,band_Nm\n" + text,
                                                 4)[0]
    # 200 N*m at 12000 rpm = 251 kW shaft: above the 200 kW discharge limit for ANY motor and inverter
    v = RS.spec_check(one("X,200,12000,600,,,\n"), lim)
    assert v["status"] == "violated" and v["P_shaft_min_W"] == pytest.approx(200 * 12000 * math.pi / 30)
    # the current check uses the LOWEST Vdc of a for-all range: 150 kW / 350 V = 429 A > 400 A
    r = RS.spec_check(one("X,119.4,12000,350,650,,\n"), lim)
    assert r["status"] == "violated" and any(c["limit"] == "discharge_current_max_A" and not c["holds"]
                                             for c in r["checks"])
    assert RS.spec_check(one("X,100,12000,600,,,\n"), lim)["status"] == "necessary_ok"
    # a band takes the least power it accepts; a band through zero power gives no bound
    assert RS.spec_check(one("X,165,12000,600,,band,10\n"), lim)["status"] == "necessary_ok"
    assert RS.spec_check(one("X,5,12000,600,,band,10\n"), lim)["status"] == "not_applicable"
    assert RS.spec_check(one("X,300,0,600,,,\n"), lim)["status"] == "not_applicable"            # standstill
    # regeneration: losses could absorb power above a charge limit - only the model decides
    assert RS.spec_check(one("X,-100,12000,600,,,\n"), lim)["status"] == "needs_model"
    assert RS.spec_check(one("X,-50,12000,600,,,\n"), lim)["status"] == "necessary_ok"


def test_the_model_never_contradicts_the_model_free_condition_and_it_decides_what_the_model_left_open():
    from dataclasses import replace
    drive, lim = sf.synthetic_drive(), sf.synthetic_limits()
    drive = replace(drive, domain=replace(drive.domain, kind="model_validity"))   # beyond it the model is silent
    text = ("id,torque_Nm,speed_rpm,Vdc_V\n"
            "IN,200,12000,600\n"                                 # inside the model: the model FAILs it too
            "OUT,20,99000,600\n"                                 # 207 kW beyond the model's speed range
            "LOW,10,99000,600\n")                                # 104 kW beyond it: nothing model-free to say
    res = RS.evaluate_set(RS.parse_requirements_csv(text, 4), drive, lim, with_capability=False)
    rows = {r["id"]: r for r in res["rows"]}
    assert rows["IN"]["verdict"] == "FAIL" and "decided_by" not in rows["IN"]
    out = rows["OUT"]
    assert out["model_verdict"] == "UNKNOWN" and out["model_reasons"] == ["OUTSIDE_MODEL_DOMAIN"]
    assert out["verdict"] == "FAIL" and out["reasons"] == ["NECESSARY_CONDITION_VIOLATED"]
    assert out["decided_by"].startswith("model-free") and out["class"] == "violation"
    assert rows["LOW"]["verdict"] == "UNKNOWN" and rows["LOW"]["class"] == "outside_model"
    assert res["summary"]["FAIL"] == 2 and res["summary"]["UNKNOWN"] == 1
    with pytest.raises(AssertionError, match="contradicts"):                  # a PASS against it is never shown
        RS._with_spec_verdict({"id": "X", "verdict": "PASS", "spec_check": {"status": "violated", "text": "t"}})


def test_results_csv_round_trips(base):
    text = RS.rows_to_csv(base[3]["rows"])
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [r["id"] for r in rows] == ["REQ-A", "REQ-B", "REQ-C", "REQ-D", "REQ-E"]
    assert rows[2]["Vdc_V"] == "550..650" and rows[1]["verdict"] == "FAIL"
    assert rows[0]["spec_check"] == "necessary_ok"


def test_candidate_text_is_parsed_and_refused_before_any_run():
    got = RS.parse_candidates("# comment\ncharge: charge_power_max_W=150000, charge_current_max_A=400\n\nI: I_peak_max_A=720")
    assert got == [{"name": "charge", "changes": {"charge_power_max_W": 150000.0, "charge_current_max_A": 400.0}},
                   {"name": "I", "changes": {"I_peak_max_A": 720.0}}]
    for bad, match in (("no colon", "name: parameter=value"), ("x: I_peak_max_A", "has no '='"),
                       ("x: I_peak_max_A=abc", "not a number"), ("x: Vdc_V=700", "condition of each requirement")):
        with pytest.raises(InputValidationError, match=match):
            RS.parse_candidates(bad)


def test_cli_reqset(tmp_path, capsys):
    from traction_workbench.cli import main
    reqs, cands, out = tmp_path / "reqs.csv", tmp_path / "c.txt", tmp_path / "res.csv"
    assert main(["reqset", "--template", str(reqs)]) == 0
    cands.write_text("charge 150 kW: charge_power_max_W=150000, charge_current_max_A=400\n", encoding="utf-8")
    assert main(["reqset", str(reqs), "--candidates", str(cands), "--out", str(out), "--fast", "--exit-code"]) == 2
    text = capsys.readouterr().out
    assert "PASS 3, FAIL 1, UNKNOWN 1" in text and "candidate charge 150 kW: improves REQ-B" in text
    assert [r["id"] for r in csv.DictReader(io.StringIO(out.read_text(encoding="utf-8")))][-1] == "REQ-E"
    cands.write_text("bad: Vdc_V=700\n", encoding="utf-8")
    assert main(["reqset", str(reqs), "--candidates", str(cands)]) == 4          # refused as invalid input


def test_desktop_requirement_set_page_and_magnet_temperature_inputs(tmp_path):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import matplotlib
    matplotlib.use("QtAgg", force=True)
    from PySide6.QtWidgets import QApplication
    from traction_workbench.desktop import theme
    from traction_workbench.desktop.main_window import MainWindow
    from traction_workbench.desktop.worker import TaskRunner
    from traction_workbench.plots import style
    from test_review_6198099 import _two_plane_map
    app = QApplication.instance() or QApplication([])
    app.setProperty("twb_selftest", True)
    theme.apply(app, "light")
    TaskRunner.synchronous = True
    try:
        win = MainWindow()
        rs = win.pages["requirement_set"]
        reqs = rs.check_reading()
        assert [r.req_id for r in reqs] == ["REQ-A", "REQ-B", "REQ-C", "REQ-D", "REQ-E"]
        assert "existence" in rs.detail.toPlainText() and rs.result is None      # read, nothing calculated
        rs.run()
        assert [rs.res_table.item(i, 1).text() for i in range(5)] == ["PASS", "FAIL", "PASS", "UNKNOWN", "PASS"]
        rs.res_table.selectRow(3)
        text = rs.detail.toPlainText()
        order = [text.index(f"{k}. ") for k in range(1, 8)]
        assert order == sorted(order)                      # requirement -> ... -> details, in the review's order
        assert rs.prio_table.item(0, 2).text() == "REQ-D"
        out = rs.save_results(str(tmp_path / "set.csv"))
        assert (tmp_path / "set.csv").read_text(encoding="utf-8").startswith("id,verdict,status,class")
        assert out
        rs.res_table.selectRow(4)                            # a band requirement travels in the case
        case = rs.open_in_decision()
        dp = win.pages["decision"]
        assert case["requirement"]["operator"] == "band" and dp.result["record"]["requirement"]["req_id"] == "REQ-E"
        assert "band" in dp.preset_hint.text()
        # a flux map with two magnet-temperature planes: single-point pages get a visible, pre-set temperature
        drive, _lim = _two_plane_map()
        win.state.drive = drive
        win.state.drive_changed.emit()
        ex = win.pages["explorer"]
        assert ex.magnet.get() == 20.0 and ex.magnet.planes == [20.0, 120.0]
        ex.n.setValue(3000.0)
        ex.T.setValue(100.0)
        ex.run()
        assert ex.claims.item(0, 1).text() == "FEASIBLE"
        ex.magnet.on.setChecked(False)                       # not stated: refused with the reason, no crash
        assert ex.magnet.missing(ex) and any("magnet" in e.lower() or "자석" in e
                                            for e in (app.property("twb_errors") or []))
        app.setProperty("twb_errors", [])
        win.state.drive = sf.synthetic_drive()
        win.state.drive_changed.emit()
        assert ex.magnet.get() is None and ex.magnet.planes == []               # not carried to another model
        win.close()
    finally:
        TaskRunner.synchronous = False
        matplotlib.use("Agg", force=True)
        style.apply("light")
