"""The fault-simulation inputs without typed text: the fault list with the form of the selected fault (a field per
declared parameter) and the campaign axes chosen from what the scenario and design can vary."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from traction_workbench import api  # noqa: E402
from traction_workbench.desktop.fault_editor import (AxisEditor, FaultEditor, axis_catalog,  # noqa: E402
                                                     default_params, fault_name, parse_params)
from traction_workbench.extensions.faultsim.engine import FAULT_KINDS  # noqa: E402
from traction_workbench.i18n import language, set_language  # noqa: E402

DESIGN = api.fault_design(None)["base"]


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    before = language()
    set_language("ko")
    a = QApplication.instance() or QApplication([])
    yield a
    set_language(before)


@pytest.fixture(scope="module")
def win(app):
    import matplotlib
    matplotlib.use("QtAgg")
    from traction_workbench.desktop.main_window import MainWindow
    from traction_workbench.desktop.worker import TaskRunner
    TaskRunner.synchronous = True
    w = MainWindow()
    w.show_page("fault_sim")
    app.processEvents()
    yield w
    w.close()


@pytest.fixture()
def editor(app):
    e = FaultEditor()
    e.set_choices(DESIGN)
    yield e
    e.deleteLater()


def test_every_preset_round_trips_through_the_editor(editor):
    for s in api.fault_scenarios():
        faults = s["scenario"].get("faults") or []
        editor.set_faults(faults)
        out = editor.faults()
        assert len(out) == len(faults)
        for a, b in zip(faults, out):
            p = dict(a.get("params") or {})
            if "duration_ms" in p:                       # one spelling in the editor: the engine's duration_s
                p["duration_s"] = p.pop("duration_ms") * 1e-3
            assert b["kind"] == a["kind"] and b["t_ms"] == float(a.get("t_ms", 0.0)) and b["params"] == p


def test_a_new_fault_and_a_kind_change_take_the_declared_defaults(editor, app):
    editor.set_faults([])
    editor.add()
    f = editor.faults()[0]
    assert f["kind"] == "sensor" and f["params"]["target"] == DESIGN["sensors"][0]["name"]
    editor._kind_changed(0, "pwm_output")
    app.processEvents()
    assert editor.faults()[0]["params"] == default_params("pwm_output")
    for k in FAULT_KINDS:
        assert fault_name(k) and not fault_name(k).count("_")


def test_the_form_edits_the_row_and_the_list_says_it_in_words(editor, app):
    from PySide6.QtWidgets import QComboBox, QDoubleSpinBox
    editor.set_faults([{"kind": "sensor", "t_ms": 10.0, "params": {"target": "CS_A", "mode": "offset", "value": 100}}])
    editor.table.selectRow(0)
    app.processEvents()
    spins = editor.box.findChildren(QDoubleSpinBox)
    combos = editor.box.findChildren(QComboBox)
    assert all(w.property("twb_not_input") for w in spins + combos)       # views of the row: the list holds the data
    mode = next(c for c in combos if c.findData("gain") >= 0)
    mode.setCurrentIndex(mode.findData("gain"))
    app.processEvents()
    assert editor.faults()[0]["params"]["mode"] == "gain"
    assert editor.faults()[0]["params"]["value"] == 0.1      # an offset of 100 A never becomes a gain of 100
    text = editor.table.item(0, 2).text()
    assert "CS_A" in text and ("이득" in text or "gain" in text)
    assert "=" not in text                                                 # words, not key=value


def test_an_older_workspace_with_parameter_text_is_read(editor):
    problems = editor.restore_workspace([{"kind": "sensor", "t_ms": "12.5",
                                          "params": "target=CS_B, mode=offset, value=80"},
                                         {"kind": "no_such_fault", "t_ms": 0, "params": {}}])
    assert len(problems) == 1 and "no_such_fault" in problems[0]
    assert editor.faults() == [{"kind": "sensor", "t_ms": 12.5,
                                "params": {"target": "CS_B", "mode": "offset", "value": 80}}]
    assert parse_params("a=1, b=x") == {"a": 1, "b": "x"}


def test_axis_catalog_names_what_the_scenario_can_vary():
    sc = {"speed_rpm": 12000.0, "request": {"kind": "constant", "T0_Nm": 150.0},
          "faults": [{"kind": "sensor", "t_ms": 10.0, "params": {"target": "CS_A", "mode": "offset", "value": 150}}]}
    cat = axis_catalog(sc, DESIGN, api.fault_design(None)["schema"]["kind_params"])
    by = {e["path"]: e for e in cat}
    for path in ("speed_rpm", "request.T0_Nm", "theta0_deg", "faults.0.t_ms", "faults.0.params.value",
                 "faults.0.params.mode", "tolerances.CS_A.gain_err", "tolerances.RES.offset_deg",
                 "tolerances.machine.psi_scale", "overrides.paths.HW.delay_us",
                 "overrides.mechanisms.SM-TQ.params.debounce_ms"):
        assert path in by, path
    cs = next(s for s in DESIGN["sensors"] if s["name"] == "CS_A")
    assert by["tolerances.CS_A.gain_err"]["range"] == [-cs["gain_tol"], cs["gain_tol"]]   # the declared corners
    assert by["faults.0.params.mode"]["kind"] == "choice" and "gain" in by["faults.0.params.mode"]["values"]


def test_axis_editor_rows_from_the_catalog_and_presets(app):
    sc = {"speed_rpm": 12000.0, "faults": [{"kind": "sensor", "t_ms": 10.0,
                                            "params": {"target": "CS_A", "mode": "offset", "value": 150}}]}
    ed = AxisEditor(lambda: axis_catalog(sc, DESIGN, api.fault_design(None)["schema"]["kind_params"]))
    try:
        ed.set_axes([{"path": "speed_rpm", "values": [9000, 12000]}])
        ed.pick.setCurrentIndex(ed.pick.findData("tolerances.CS_A.gain_err"))
        ed.add_picked()
        ed.pick.setCurrentIndex(ed.pick.findData("faults.0.params.mode"))
        ed.add_picked()
        axes = ed.axes()
        assert axes[0] == {"path": "speed_rpm", "values": [9000, 12000]}
        assert axes[1]["path"] == "tolerances.CS_A.gain_err" and axes[1]["n"] == 3
        assert axes[2]["values"][:2] == ["offset", "gain"]
        ws = ed.workspace_state()
        ed.restore_workspace(ws)
        assert ed.axes() == axes
        ed.table.item(1, 1).setText("")                      # neither a range nor a list: named, never skipped
        with pytest.raises(ValueError, match="CS_A"):
            ed.axes()
    finally:
        ed.deleteLater()


def test_the_page_uses_the_editors(win):
    page = win.pages["fault_sim"]
    page.preset.setCurrentIndex(page.preset.findData("cs_offset"))
    page._load_preset()
    assert isinstance(page.faults, FaultEditor) and isinstance(page.axes, AxisEditor)
    body = page.campaign_body()
    assert body["axes"] and all("path" in a for a in body["axes"])
    assert body["base"]["faults"][0]["params"]["target"] == "CS_A"
