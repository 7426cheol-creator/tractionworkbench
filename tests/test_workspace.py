"""Workspace (UX review J1): every page's inputs, the data behind them and the project, saved and restored.

* nothing a page reads as input is left out: every input widget under a page's input areas has a place;
* a workspace opened in a fresh window gives every page the same engine requests as the window it was saved from -
  with inputs changed from their defaults, a thermal node and a safety rule added, a datasheet curve edited but not yet
  stored, and data kept next to the widgets (an imported temperature trace, an EMI calibration binding, a candidate's
  module file, a winding hand-over);
* what cannot be restored is reported, never guessed (a field that does not exist, a choice not offered, a value
  outside the field's range, a table with other columns);
* the last session is kept for recovery and offered back at the next start; closing asks only when a workspace file has
  unsaved changes.
"""

import copy
import dataclasses
import json
import math
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from traction_workbench import api  # noqa: E402
from traction_workbench.i18n import language, set_language  # noqa: E402

RUNS = {"decision": ["run"], "requirement_set": ["run"], "explorer": ["run"], "trajectory": ["run"],
        "performance": ["run"], "design": ["run1", "run2"],
        "safety": ["run_ftti", "run_discharge", "run_passive", "run_overvoltage", "run_safe"],
        "thermal": ["run", "run_cycle"], "protection": ["run", "run_asc"],
        "power": ["run_module", "run_ripple", "run_life"],
        "oew_hev": ["run_oew", "run_compare", "run_joint", "run_crank", "run_rej", "run_planetary"],
        "emi": ["run", "run_oew"], "efficiency": ["run_point", "run_map", "run_mission", "run_ab"],
        "pwm_driveline": ["run_policies", "run_timing", "run_ripple", "run_transients", "run_driveline",
                          "run_stability"],
        "machine": ["run_trade", "run_wind", "run_size"]}
SAFETY_API = ("timing", "passive", "discharge", "overvoltage", "safe_state")


@pytest.fixture(scope="module")
def app():
    import matplotlib
    matplotlib.use("QtAgg")
    from PySide6.QtWidgets import QApplication
    before = language()
    set_language("ko")
    a = QApplication.instance() or QApplication([])
    a.setProperty("twb_selftest", True)
    from traction_workbench.desktop.worker import TaskRunner
    TaskRunner.synchronous = True
    yield a
    set_language(before)


def _window():
    from PySide6.QtWidgets import QApplication
    from traction_workbench.desktop.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    QApplication.processEvents()
    return w


def _norm(x, depth=0):
    """A comparable form of an engine request (objects by their content, never by their address)."""
    if depth > 8:
        return "..."
    if isinstance(x, dict):
        return {str(k): _norm(v, depth + 1) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_norm(v, depth + 1) for v in x]
    if isinstance(x, float):
        return x if math.isfinite(x) else str(x)
    if x is None or isinstance(x, (bool, int, str)):
        return x
    if hasattr(x, "tolist"):
        return _norm(x.tolist(), depth + 1)
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {type(x).__name__: _norm({f.name: getattr(x, f.name) for f in dataclasses.fields(x)}, depth + 1)}
    if hasattr(x, "__dict__") and not callable(x):
        return {type(x).__name__: _norm(vars(x), depth + 1)}
    return re.sub(r" at 0x[0-9a-fA-F]+", "", repr(x))


def _requests(win, monkeypatch) -> dict:
    """Every page action's engine request (or the error it reports), without running the calculations."""
    from PySide6.QtWidgets import QApplication
    from traction_workbench.desktop.worker import TaskRunner
    app = QApplication.instance()
    calls = []
    with monkeypatch.context() as m:
        m.setattr(TaskRunner, "run", lambda self, key, label, fn, on_result, *args, on_error=None, **kw:
                  calls.append(["run", key, _norm(args), _norm(kw)]))
        for name in SAFETY_API:
            orig = getattr(api, name)
            m.setattr(api, name, lambda body, *a, _o=orig, _n=name, **k: (calls.append(["api", _n, _norm(body)]),
                                                                        _o(body, *a, **k))[1])
        out = {}
        for page, actions in RUNS.items():
            win.show_page(page)
            for act in actions:
                calls.clear()
                app.setProperty("twb_errors", [])
                try:
                    getattr(win.pages[page], act)()
                    outcome = None
                except Exception as exc:  # noqa: BLE001 - the same failure must happen in both windows
                    outcome = f"{type(exc).__name__}: {exc}"
                out[f"{page}.{act}"] = {"calls": copy.deepcopy(calls), "errors": list(app.property("twb_errors") or []),
                                        "raised": outcome}
    return out


def _perturb(win):
    """Every input away from its default (numbers by one step, the next choice, check boxes flipped, a copy of the
    last table row), plus the structures a flat widget list does not show."""
    from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QRadioButton, QSpinBox
    from traction_workbench.desktop import workspace as WS
    from traction_workbench.desktop.widgets import NumTable
    for key in WS.pages_with_inputs(win):
        page = win.pages[key]
        for path, w in WS._targets(page, WS.input_roots(win, key)).items():
            if isinstance(w, (QDoubleSpinBox, QSpinBox)):
                step = w.singleStep() or 1
                w.setValue(w.value() + step if w.value() + step <= w.maximum() else w.value() - step)
            elif isinstance(w, QComboBox) and w.count() > 1:
                w.setCurrentIndex((w.currentIndex() + 1) % w.count())
            elif isinstance(w, QCheckBox):
                w.setChecked(not w.isChecked())
            elif isinstance(w, QRadioButton) and not w.isChecked():
                w.setChecked(True)
            elif isinstance(w, NumTable) and w.rowCount():
                last = w.rowCount() - 1
                w.add_row([(w.item(last, j).text() if w.item(last, j) else "") for j in range(w.columnCount())])
    th = win.pages["thermal"].editor                           # a node added and its first stage retyped
    th.add_node()
    node = th.tabs.widget(th.tabs.count() - 1)
    node.table.item(0, 0).setText("0.0123")
    sf = win.pages["safety"]                                     # a project rule added with its choices
    sf.s_rules.add({"rule_id": "PRJ-WS", "when": {"speed_rpm_min": 1000.0, "hv_state": "battery_connected"},
                    "require": "ASC", "basis": "workspace test"})
    grid = win.pages["power"].grid                               # a curve stored, the next one edited, not stored
    grid.pick.setCurrentIndex((grid.pick.currentIndex() + 1) % grid.pick.count())
    if grid.table.rowCount() and grid.table.columnCount():
        grid.table.item(0, 0).setText("0.987")


def _hidden_data(win, tmp_path):
    """Data a page keeps next to its widgets, each through the page's own action."""
    from PySide6.QtWidgets import QApplication
    pw = win.pages["power"]
    csv = tmp_path / "trace.csv"
    csv.write_text("t_s,T_C\n0,60\n1,72\n2,81\n3,77\n", encoding="utf-8")
    pw.load_trace_csv(str(csv))
    mc = win.pages["machine"]
    win.show_page("machine")
    mc.run_wind()
    QApplication.processEvents()
    mc.send_candidate()
    emi = win.pages["emi"]
    win.show_page("emi")
    emi.run()
    QApplication.processEvents()
    emi.bind_calibration()
    ef = win.pages["efficiency"]
    ef._file_modules["A"] = win.state.example("MODULE")
    src = ef.ab["A"]["src"]
    src.setCurrentIndex(src.findData("file"))
    return {"trace": copy.deepcopy(pw.trace), "lineage": copy.deepcopy(mc.cand_lineage),
            "binding": copy.deepcopy(emi.cal_binding)}


def test_every_page_input_has_a_place_in_the_workspace(app):
    from traction_workbench.desktop import workspace as WS
    w = _window()
    try:
        pages = WS.pages_with_inputs(w)
        assert len(pages) == 15
        for key in pages:
            missing = WS.uncaptured(w.pages[key], WS.input_roots(w, key))
            assert not missing, (key, [type(x).__name__ for x in missing])
    finally:
        w.close()


def test_a_restored_workspace_gives_every_page_the_same_requests(app, tmp_path, monkeypatch):
    from traction_workbench.desktop import workspace as WS
    a = _window()
    b = None
    try:
        hidden = _hidden_data(a, tmp_path)
        assert hidden["trace"] and hidden["lineage"] and hidden["binding"]
        _perturb(a)
        path = WS.save(WS.build(a), tmp_path / ("work" + WS.SUFFIX))
        req_a = _requests(a, monkeypatch)
        b = _window()
        problems = WS.apply(b, WS.load(path))
        assert problems == []
        assert WS.content(WS.build(b)) == WS.content(WS.load(path))
        pw, mc, emi = b.pages["power"], b.pages["machine"], b.pages["emi"]
        assert pw.trace == hidden["trace"] and mc.cand_lineage == hidden["lineage"]
        assert emi.cal_binding == json.loads(json.dumps(hidden["binding"]))
        assert "작업 공간" in pw.l_trace_lab.text() and "결속" in emi.e_cal_lab.text()
        req_b = _requests(b, monkeypatch)
        assert req_a.keys() == req_b.keys()
        differ = [k for k in req_a if req_a[k] != req_b[k]]
        assert not differ, differ
        sent = [k for k, v in req_a.items() if v["calls"]]
        assert len(sent) >= 25, sent                     # most actions reach the engine with the perturbed inputs
        fresh = _window()                                # and the perturbed inputs are not the defaults: a restore
        try:                                             # that fell back to defaults could not pass
            req_c = _requests(fresh, monkeypatch)
        finally:
            fresh.close()
        changed = [k for k in req_a if req_a[k] != req_c[k]]
        assert len(changed) >= 30, sorted(set(req_a) - set(changed))
    finally:
        a.close()
        if b is not None:
            b.close()


def test_what_cannot_be_restored_is_reported_not_guessed(app, tmp_path):
    from traction_workbench.desktop import workspace as WS
    w = _window()
    try:
        ws = WS.build(w)
        f = ws["pages"]["decision"]["fields"]
        f["torque"]["v"] = 1e9                                    # outside the field's range
        f["vdc_kind"] = {"t": "combo", "i": 7, "n": 99, "text": "no such choice", "data": "no_such"}
        f["retired_field"] = {"t": "num", "v": 1.0}
        ws["pages"]["requirement_set"]["fields"]["table"]["cols"] = 3
        ws["pages"]["no_such_page"] = {"fields": {}}
        problems = WS.apply(w, ws)
        text = "\n".join(problems)
        for needle in ("torque", "vdc_kind", "retired_field", "table", "no_such_page"):
            assert needle in text, (needle, text)
        with pytest.raises(ValueError):
            WS.check({"schema": "twb-project/1"})
    finally:
        w.close()


def test_the_session_is_offered_back_and_a_changed_workspace_file_is_confirmed(app, tmp_path, monkeypatch):
    from traction_workbench.desktop import workspace as WS
    from traction_workbench.desktop import main_window as MW
    monkeypatch.setenv("TWB_DATA_DIR", str(tmp_path / "data"))
    app.setProperty("twb_selftest", False)
    answers = []
    monkeypatch.setattr(MW, "ask", lambda parent, title, text, buttons: answers.pop(0))
    try:
        w = MW.MainWindow()
        w.pages["decision"].torque.setValue(222.0)
        w.close()                                                # no workspace file: kept for recovery, no question
        assert WS.recovery_path().is_file() and not answers
        answers.append("restore")
        w2 = MW.MainWindow()
        w2.offer_recovery()
        assert w2.pages["decision"].torque.value() == 222.0
        path = tmp_path / ("mine" + WS.SUFFIX)
        w2.save_workspace(str(path))
        assert not w2.workspace_dirty()
        w2.pages["decision"].torque.setValue(180.0)
        assert w2.workspace_dirty()
        answers.append("cancel")                                 # the close is cancelled: the window stays
        assert not w2.confirm_close()
        answers.append("save")
        assert w2.confirm_close()
        assert WS.load(path)["pages"]["decision"]["fields"]["torque"]["v"] == 180.0
        w2.close()
        assert not WS.recovery_path().exists()                   # saved: nothing left to recover
        answers.append("fresh")
        (tmp_path / "data").mkdir(exist_ok=True)
        WS.save(WS.build(w2), WS.recovery_path())
        w3 = MW.MainWindow()
        w3.offer_recovery()                                      # declined: kept aside, not deleted
        assert not WS.recovery_path().exists() and WS.recovery_path().with_suffix(".previous.json").is_file()
        w3.close()
    finally:
        app.setProperty("twb_selftest", True)
