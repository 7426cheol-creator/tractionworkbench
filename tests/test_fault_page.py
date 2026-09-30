"""The fault simulation page, driven through its real code paths (offscreen, synchronous runner).

* a representative scenario loads, runs and is read: requirement table (SG -> FSR -> TSR) with the failing TSR, its
  evidence on selection, the event table, the synchronized waveforms, the reading naming the cause and the reaction;
* the reaction candidates run from the same initial condition;
* a campaign finds a counterexample; it is re-run (reproduced), saved to a file, opened again, loaded back as the
  scenario, and marked stale - and re-run as such - when the project data it was made from changes;
* scenario files and the result export round-trip; the page's inputs and counterexamples survive a workspace.
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
    assert page.t_req.rowCount() == 3 + 2 + 7                        # SG, FSR, TSR
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
    page = win.pages["fault_sim"]
    _load(page, "res_lost")
    page.run_compare()
    cmp = page.last_cmp
    assert [r["candidate"] for r in cmp["rows"]] == ["policy", "none", "asc_low", "asc_high", "six_switch_off",
                                                     "torque_zero"]
    rows = {r["candidate"]: r for r in cmp["rows"]}
    assert rows["policy"]["overall"] == "FAIL" and "TSR-06" in rows["policy"]["failing"]
    assert "TSR-06" not in rows["asc_low"]["failing"]              # ASC holds the DC link where 6SO rectifies
    assert page.t_cmp.rowCount() == 6 and page.t_cmp.item(4, 0).text() == "6SO"
    assert page.i_cmp.text()


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
    page.t_cx.selectRow(0)
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
    page.overrides.setPlainText('{"mechanisms.SM-TQ.params.debounce_ms": 2.0}')
    sc = page.scenario()
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
