"""The fault simulation page, driven through its real code paths (offscreen, synchronous runner).

* a representative scenario loads, runs and is read: requirement table (SG -> FSR -> TSR) with the failing TSR, its
  evidence on selection, the event table, the synchronized waveforms, the reading naming the cause and the reaction;
* the reaction candidates run from the same initial condition;
* a campaign finds a counterexample; it is re-run (reproduced), saved to a file, opened again, loaded back as the
  scenario, and marked stale - and re-run as such - when the project data it was made from changes;
* scenario files and the result export round-trip; the page's inputs and counterexamples survive a workspace;
* the design editor changes the FTTI, a TSR limit, a debounce time and the reaction strategies in typed fields (no
  JSON): the difference to the project is the run's design variant, renaming an id renames its references, an invalid
  design is refused before a run, a loaded scenario asks before replacing edited changes, and the design can be
  written into the project;
* the comparison runs the declared strategies (soft ASC and others) next to the primitive reactions;
* the safety-case tab reviews the declared design, runs the verification matrix on it and saves the report.
"""

import copy
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from traction_workbench.i18n import language, set_language  # noqa: E402


@pytest.fixture(scope="module")
def win():
    import matplotlib
    matplotlib.use("QtAgg")
    from PySide6.QtWidgets import QApplication
    before = language()
    set_language("ko")
    app = QApplication.instance() or QApplication([])
    app.setProperty("twb_selftest", True)
    from traction_workbench.desktop.main_window import MainWindow
    from traction_workbench.desktop.worker import TaskRunner
    TaskRunner.synchronous = True
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.show_page("fault_sim")
    app.processEvents()
    yield w
    w.close()
    set_language(before)


def _load(page, key):
    page.preset.setCurrentIndex(page.preset.findData(key))
    page._load_preset()


def _row(table, first):
    return next(r for r in range(table.rowCount()) if table.item(r, 0).text() == first)


def _rec(table, rid):
    """Row of the record ``rid`` in an editor table."""
    return next(r for r, rec in enumerate(table.records) if str(rec.get("id")) == rid)


def _col(table, key):
    return next(j for j, c in enumerate(table.cols) if c.key == key)


def _edit(table, rid, key, text):
    """Type ``text`` into a cell of the editor table, as a person does."""
    r, j = _rec(table, rid), _col(table, key)
    table.selectRow(r)
    table.item(r, j).setText(text)


def _field(form, key, text):
    w = form.widgets[key]
    w.setText(text)
    w.editingFinished.emit()


def test_a_representative_scenario_runs_and_reads_on_the_page(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from traction_workbench.extensions.faultsim.study import scenario
    page = win.pages["fault_sim"]
    _load(page, "res_lost")
    sc = page.scenario()
    ref = scenario("res_lost")
    assert sc["speed_rpm"] == ref["speed_rpm"] and sc["faults"] == [dict(f, t_ms=float(f["t_ms"])) for f in
                                                                    ref["faults"]]
    page.run()
    res = page.last
    assert res is not None and res["verdicts"]["TSR-06"] == "FAIL"
    assert page.t_req.rowCount() == 3 + 2 + 9                        # SG, FSR, TSR
    r6 = _row(page.t_req, "TSR-06")
    assert page.t_req.item(r6, 2).text() == "실패" and "FSR-02" in page.t_req.item(r6, 1).text()
    page.t_req.selectRow(r6)
    assert "TSR-06" in page.req_detail.text() and "850" in page.req_detail.text()
    assert page.t_events.rowCount() == len(res["events"])
    assert page.p_wave._draw is not None and page.p_time._draw is not None
    text = page.insight.text()                                      # the reading names cause, reaction and result
    assert "센서" in text and "6SO" in text and "TSR-06" in text and "정류 개시" in text
    assert "SYNTH-TRACTION" in win.banners["fault_sim"].text()     # the product data the result came from
    out = tmp_path / "result.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    page._export()
    back = json.loads(out.read_text(encoding="utf-8"))
    assert back["verdicts"]["TSR-06"] == "FAIL" and back["project_usage"]["analysis"] == "fault_sim"


def test_reaction_candidates_run_from_the_same_initial_condition(win):
    from PySide6.QtCore import Qt
    page = win.pages["fault_sim"]
    _load(page, "res_lost")
    # every declared strategy is a candidate next to the primitive reactions; keep one strategy for a short test
    keys = [page.cand_list.item(i).data(Qt.UserRole) for i in range(page.cand_list.count())]
    assert keys[:6] == ["policy", "none", "asc_low", "asc_high", "six_switch_off", "torque_zero"]
    assert {"SOFT_ASC_I", "SOFT_ASC_V", "FW2_ASC_LOW", "SEQ_ASC_LOW", "ASC_THEN_6SO", "HYST_6SO_ASC",
            "SOFT_TQ_6SO"} <= set(keys)
    for i in range(page.cand_list.count()):
        it = page.cand_list.item(i)
        it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in keys[:6] + ["FW2_ASC_LOW"] else Qt.Unchecked)
    page.run_compare()
    cmp = page.last_cmp
    assert [r["candidate"] for r in cmp["rows"]] == ["policy", "none", "asc_low", "asc_high", "six_switch_off",
                                                     "torque_zero", "FW2_ASC_LOW"]
    rows = {r["candidate"]: r for r in cmp["rows"]}
    assert rows["policy"]["overall"] == "FAIL" and "TSR-06" in rows["policy"]["failing"]
    assert "TSR-06" not in rows["asc_low"]["failing"]              # ASC holds the DC link where 6SO rectifies
    # freewheel first, then the ASC: the DC link stays bounded and the ASC current / torque steps are avoided
    fw = rows["FW2_ASC_LOW"]
    assert "TSR-06" not in fw["failing"] and "TSR-03" not in fw["failing"]
    assert fw["metrics"]["v_dc_max_V"] < rows["policy"]["metrics"]["v_dc_max_V"]
    assert fw["metrics"]["i_phase_peak_A"] < rows["asc_low"]["metrics"]["i_phase_peak_A"]
    assert page.t_cmp.rowCount() == 7 and page.t_cmp.columnCount() == 9 and page.t_cmp.item(4, 0).text() == "6SO"
    assert page.t_cmp.item(6, 0).text() == "FW2_ASC_LOW" and page.t_cmp.item(6, 4).text() != "—"   # min i_d shown
    assert [e["action"] for e in fw["strategy_log"]] == ["six_switch_off", "asc_low"]
    assert "6SO" in page.t_cmp.item(6, 0).toolTip() and "ASC-low" in page.t_cmp.item(6, 0).toolTip()   # its steps
    assert "단계:" in page.i_cmp.text()
    from PySide6.QtWidgets import QApplication
    for i in range(page.cand_list.count()):
        page.cand_list.item(i).setCheckState(Qt.Unchecked)
    before = list(QApplication.instance().property("twb_errors") or [])
    page.run_compare()                                             # nothing chosen: refused with the reason
    after = list(QApplication.instance().property("twb_errors") or [])
    assert len(after) == len(before) + 1 and "후보" in after[-1]
    for i in range(page.cand_list.count()):
        page.cand_list.item(i).setCheckState(Qt.Checked)


def test_a_counterexample_is_rerun_saved_opened_loaded_and_marked_stale(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    page = win.pages["fault_sim"]
    _load(page, "res_lost")
    page.set_axes([{"path": "speed_rpm", "values": [9000, 12000]}])
    page.cref.setValue(0)
    page.counterexamples = []
    page.run_campaign()
    camp = page.last_camp
    assert page.t_runs.rowCount() == 2 and camp["scope"]["region"] == "NOT_ESTABLISHED"
    cx = [c for c in page.counterexamples if c["scenario"]["speed_rpm"] == 12000]
    assert cx and "TSR-06" in cx[0]["failing"]
    # 9000 rpm is above the rectification onset too: the six-switch-off braking torque fails TSR-08 there
    assert "TSR-08" in next(c for c in page.counterexamples if c["scenario"]["speed_rpm"] == 9000)["failing"]
    assert "현재 프로젝트와 같음" in page.t_cx.item(0, 3).text()
    page.t_cx.selectRow(0)
    page.rerun_selected()
    assert "재현됨" in page.cx_state.text()
    path = tmp_path / "cx.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    page._cx_save()
    saved = copy.deepcopy(page.counterexamples)
    page.counterexamples = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    page._cx_open()
    assert [c["id"] for c in page.counterexamples] == [c["id"] for c in saved]
    i12 = next(i for i, c in enumerate(page.counterexamples) if c["scenario"]["speed_rpm"] == 12000)
    page.t_cx.selectRow(i12)
    page._cx_to_scenario()
    assert page.scenario()["speed_rpm"] == 12000
    # the project data the counterexample was made from changes: it is marked, and a re-run says so
    proj = win.state.project
    data = json.loads(json.dumps(proj.data("fault_sim")))
    data["paths"][1]["delay_us"] = float(data["paths"][1]["delay_us"]) + 1.0
    try:
        win.state.set_project(proj.with_section("fault_sim", data))
        assert "fault_sim" in page.t_cx.item(0, 3).text()
        page.t_cx.selectRow(0)
        page.rerun_selected()
        assert "입력이 바뀜" in page.cx_state.text() and "fault_sim" in page.cx_state.text()
    finally:
        win.state.set_project(proj)
    assert "현재 프로젝트와 같음" in page.t_cx.item(0, 3).text()


def test_scenario_files_round_trip_and_the_page_survives_a_workspace(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from traction_workbench.desktop import workspace as WS
    page = win.pages["fault_sim"]
    _load(page, "sw_short_ls")
    page.faults.add({"kind": "path_lost", "t_ms": 0.0, "params": {"path": "HW"}})
    _edit(page.editor.t_mech, "SM-TQ", "p_time", "2")               # the debounce time, typed in its cell
    assert page.editor.overrides() == {"mechanisms.SM-TQ.params.debounce_ms": 2.0}
    sc = page.scenario()
    assert sc["overrides"] == {"mechanisms.SM-TQ.params.debounce_ms": 2.0}
    path = tmp_path / "sc.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    page._save_scenario()
    _load(page, "normal_hs")
    assert page.scenario() != sc
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    page._open_scenario()
    assert page.scenario() == sc
    # a workspace keeps the fault rows (with their choice cells) and the counterexamples
    page.counterexamples = [{"id": "CX-TEST", "failing": ["TSR-03"], "created": "now", "scenario": sc}]
    assert not WS.uncaptured(page, WS.input_roots(win, "fault_sim"))
    ws = WS.build(win)
    _load(page, "normal_hs")
    page.counterexamples = []
    problems = WS.apply(win, ws)
    assert problems == []
    assert page.scenario() == sc and [c["id"] for c in page.counterexamples] == ["CX-TEST"]
    bad = copy.deepcopy(ws)
    bad["pages"]["fault_sim"]["fields"]["faults"]["v"][0]["kind"] = "no_such_fault"
    problems = WS.apply(win, bad)
    assert any("no_such_fault" in p for p in problems)             # reported, never guessed


def test_the_design_editor_changes_ftti_tsr_limits_debounce_and_strategies_without_json(win, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from traction_workbench.desktop.pages import fault_sim as FS
    page = win.pages["fault_sim"]
    ed = page.editor
    _load(page, "cs_offset")
    ed.revert()
    assert ed.change_count() == 0 and page.design_variant.sig.text() == "프로젝트 설계"
    # browsing every record adds no change (display defaults are not design changes)
    for t in (ed.t_sg, ed.t_fsr, ed.t_tsr, ed.t_mech, ed.t_path, ed.t_rules):
        for r in range(t.rowCount()):
            t.selectRow(r)
    for i in range(ed.l_strat.count()):
        ed.l_strat.setCurrentRow(i)
    assert ed.overrides() == {}
    # the FTTI of a goal, an FSR budget, a TSR limit (criterion field) and a debounce time, typed in their fields
    _edit(ed.t_sg, "SG-01", "ftti_ms", "80")
    _edit(ed.t_fsr, "FSR-01", "fdti_budget_ms", "12")
    ed.t_tsr.selectRow(_rec(ed.t_tsr, "TSR-08"))
    _field(ed.crit_form, "max", "400")
    _edit(ed.t_mech, "SM-TQ", "p_time", "5")
    ov = ed.overrides()
    assert ov == {"requirements.safety_goals.SG-01.ftti_ms": 80.0, "requirements.fsr.FSR-01.fdti_budget_ms": 12.0,
                  "requirements.tsr.TSR-08.criterion.max": 400.0, "mechanisms.SM-TQ.params.debounce_ms": 5.0}
    assert page.scenario()["overrides"] == ov                       # the run's design variant
    assert "변경 4건" in page.design_variant.sig.text()
    assert ed.t_changes.rowCount() == 4                             # path / project value / this design
    # a strategy from the representative templates: a candidate and a forced reaction at once
    ed._add_template("voltage_ramp")
    new_id = ed.work["strategies"][-1]["id"]
    assert new_id.startswith("SOFT_ASC_V") and new_id != "SOFT_ASC_V"
    assert page.override.findData(new_id) >= 0
    assert new_id in [page.cand_list.item(i).data(Qt.UserRole) for i in range(page.cand_list.count())]
    # renaming a mechanism renames its references (FSR allocation)
    _edit(ed.t_mech, "SM-TQ", "id", "SM-TQ2")
    fsr1 = next(f for f in ed.work["requirements"]["fsr"] if f["id"] == "FSR-01")
    assert "SM-TQ2" in fsr1["mechanisms"] and "SM-TQ" not in fsr1["mechanisms"] and ed.error is None
    # an invalid design is refused before a run, with the editor's reason
    _edit(ed.t_sg, "SG-01", "ftti_ms", "-5")
    assert ed.error and "설계 오류" in page.design_variant.label.text()
    before = list(QApplication.instance().property("twb_errors") or [])
    page.run()
    after = list(QApplication.instance().property("twb_errors") or [])
    assert len(after) == len(before) + 1 and "편집 탭에 오류" in after[-1]
    _edit(ed.t_sg, "SG-01", "ftti_ms", "80")
    assert ed.error is None
    # loading a scenario asks before replacing edited changes: 'no' keeps the design, 'yes' takes the scenario's
    kept = ed.overrides()
    monkeypatch.setattr(FS, "confirm", lambda *a, **k: False)
    _load(page, "ov_slow")
    assert ed.overrides() == kept and page.scenario()["faults"][0]["kind"] != ""
    monkeypatch.setattr(FS, "confirm", lambda *a, **k: True)
    _load(page, "ov_slow")
    from traction_workbench.extensions.faultsim.study import scenario
    assert ed.overrides() == (scenario("ov_slow").get("overrides") or {})
    ed.revert()
    assert ed.change_count() == 0


def test_the_edited_design_is_written_into_the_project_and_survives_a_workspace(win):
    from traction_workbench.desktop import workspace as WS
    page = win.pages["fault_sim"]
    ed = page.editor
    proj = win.state.project
    _load(page, "cs_offset")
    ed.revert()
    _edit(ed.t_mech, "SM-TQ", "p_time", "4")
    ed._add_template("current_to_asc")
    sid = ed.work["strategies"][-1]["id"]
    page.override.setCurrentIndex(page.override.findData(sid))
    sc = page.scenario()
    assert sc["reaction_override"] == sid and sc["overrides"]["mechanisms.SM-TQ.params.debounce_ms"] == 4.0
    ws = WS.build(win)                                              # the variant is saved with the workspace
    ed.revert()
    assert page.override.currentData() == ""                        # the strategy is gone with the variant
    assert WS.apply(win, ws) == []
    assert page.scenario() == sc
    try:
        page._apply_design()                                        # into the project (confirmed in the self-test)
        assert win.state.project is not proj and win.state.project.modified
        data = win.state.project.data("fault_sim")
        assert next(m for m in data["mechanisms"] if m["id"] == "SM-TQ")["params"]["debounce_ms"] == 4.0
        assert sid in [s["id"] for s in data["strategies"]]
        assert ed.change_count() == 0 and page.design_variant.sig.text() == "프로젝트 설계"
        assert page.override.currentData() == sid                   # still declared: now by the project
    finally:
        win.state.set_project(proj)
    assert sid not in [s["id"] for s in win.state.project.data("fault_sim")["strategies"]]
    ed.revert()


def test_the_safety_case_tab_reviews_verifies_and_saves_the_report(win, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFileDialog
    page = win.pages["fault_sim"]
    ed = page.editor
    _load(page, "cs_offset")
    ed.revert()
    page.run_review()
    rv = page.last_review
    assert rv["counts"]["INCONSISTENT"] == 0 and rv["counts"]["MISSING"] == 0 and rv["counts"]["WARNING"] >= 1
    assert page.t_find.rowCount() == len(rv["findings"]) and page.t_lat.rowCount() == len(rv["latency"])
    head = page.i_case.text().split("\n")[0]
    assert head.startswith("모순·누락·실패 없음") and f"경고 {rv['counts']['WARNING']}건" in head
    # a debounce longer than the FDTI budget allows is a contradiction the review names
    _edit(ed.t_mech, "SM-TQ", "p_time", "40")
    page.run_review()
    bad = [f for f in page.last_review["findings"] if f["status"] == "INCONSISTENT"]
    assert any(f["check"] == "detection latency vs FDTI budget" and "SM-TQ" in f["element"] for f in bad)
    assert page.i_case.text().startswith("설계 근거에 모순") and page.t_find.item(0, 0).text() == "모순"
    ed.revert()
    # the verification matrix on a subset of the catalog (a duplicate is skipped, not run twice)
    chosen = ("step_ok", "false_trip", "cs_offset")
    for i in range(page.verif_list.count()):
        it = page.verif_list.item(i)
        it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in chosen else Qt.Unchecked)
    page.run_verification()
    mx = page.last_verif
    assert [r["key"] for r in mx["rows"]] == ["step_ok", "cs_offset"]
    assert mx["skipped"] and mx["skipped"][0]["key"] == "false_trip"
    assert page.t_matrix.columnCount() == 3 + 2 and page.t_matrix.rowCount() == len(mx["requirements"])
    assert page.t_fmea.rowCount() == 2 and page.t_timing.rowCount() == len(mx["timing"])
    assert "궤적" in page.case_state.text() and page.i_case.text()
    # the report: design, requirements, review, matrix of THIS design, common causes and limits in one file
    out = tmp_path / "case.html"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    page._save_report()
    html = out.read_text(encoding="utf-8")
    for part in ("안전 근거 보고서", "1. 평가한 설계", "3. 정적 설계 검토", "5. 검증 매트릭스", "6. 공통 원인",
                 "8. 가정·한계·미결 사항", "TSR-08", "SOFT_ASC_V"):
        assert part in html
    # the matrix belongs to one design: after an edit the report leaves it out and says so
    _edit(ed.t_sg, "SG-01", "ftti_ms", "90")
    page._save_report()
    html = out.read_text(encoding="utf-8")
    assert "5. 검증 매트릭스" not in html and "requirements.safety_goals.SG-01.ftti_ms" in html
    assert "매트릭스 절은 빠졌습니다" in page.case_state.text()
    ed.revert()
    for i in range(page.verif_list.count()):
        page.verif_list.item(i).setCheckState(Qt.Checked)
