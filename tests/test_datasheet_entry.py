"""The desktop value-entry dialog: typed representative values build the same spec and section as the spec file, the
conventions that change a value by sqrt 2 / sqrt 3 / 2 have no default, an empty required value is refused rather than
used as 0, and keys the form does not show are kept."""

import copy
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
matplotlib = pytest.importorskip("matplotlib")

from traction_workbench import datasheet as DS  # noqa: E402

EX = Path(__file__).resolve().parents[1] / "examples" / "datasheets"


@pytest.fixture(scope="module")
def win():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    matplotlib.use("QtAgg", force=True)
    from PySide6.QtWidgets import QApplication
    from traction_workbench.desktop import theme
    from traction_workbench.desktop.main_window import MainWindow
    from traction_workbench.desktop.worker import TaskRunner
    app = QApplication.instance() or QApplication([])
    app.setProperty("twb_selftest", True)
    theme.apply(app, "light")
    TaskRunner.synchronous = True
    w = MainWindow()
    yield w
    w.close()


def _spec(name):
    return json.loads((EX / name).read_text(encoding="utf-8"))


def test_each_form_builds_the_section_of_its_spec_file(win):
    from traction_workbench.desktop.datasheet_entry_dialog import EXAMPLES
    dlg = win.pages["project"].enter_datasheet("motor")
    for kind, name in EXAMPLES.items():
        dlg.load_spec(_spec(name))
        assert dlg.error is None and dlg.new_project is not None, dlg.error
        ref, res = DS.apply(win.state.project, _spec(name), EX)
        assert dlg.new_project.sections[res["section"]].digest == ref.sections[res["section"]].digest, kind
        again = dlg.form().spec()                                  # the form's spec, saved and imported again
        assert DS.apply(win.state.project, json.loads(json.dumps(again)))[0].sections[res["section"]].digest == \
            ref.sections[res["section"]].digest
    dlg.close()


def test_conventions_have_no_default_and_empty_values_are_refused(win):
    dlg = win.pages["project"].enter_datasheet("motor")
    assert dlg.new_project is None and ("enter a value" in dlg.error or "입력" in dlg.error)   # empty form: no model
    dlg.load_spec(_spec("motor_example.json"))
    assert dlg.error is None
    form = dlg.forms["motor"]
    form.ke_basis.setCurrentIndex(0)                               # '— choose —': line-line / phase, RMS / peak
    dlg.preview()
    assert dlg.new_project is None and ("sqrt" in dlg.error or "√" in dlg.error)
    form.ke_basis.setCurrentIndex(1)
    form.rs.setValue(0.0)                                          # 'not entered', never R = 0
    dlg.preview()
    assert dlg.new_project is None and dlg.error.startswith("R")
    form.rs.setValue(30.0)
    form.flux.setCurrentIndex(1)                                   # Kt without the definition check -> refused
    form.kt.setValue(0.6)
    form.kt_basis.setCurrentIndex(2)
    dlg.preview()
    assert dlg.new_project is None and "electromagnetic" in dlg.error
    form.kt_def.setChecked(True)
    dlg.preview()
    assert dlg.error is None and dlg.result["data"]["motor"]["Kt"]["torque"] == "electromagnetic"
    dlg.close()


def test_module_value_table_and_what_the_form_cannot_show(win):
    dlg = win.pages["project"].enter_datasheet("module")
    curves = _spec("module_example.json")                         # digitized curves belong to the file import
    with pytest.raises(ValueError, match="import datasheet file|파일 가져오기"):
        dlg.forms["module"].load(curves)
    spec = _spec("module_representative_example.json")
    spec["vdc_scaling"] = {"law": "linear", "basis": "test", "valid_Vdc_V": [300.0, 800.0]}
    dlg.load_spec(spec)
    built = dlg.forms["module"].spec()
    assert built["vdc_scaling"] == spec["vdc_scaling"]             # kept, and said under the form
    assert "vdc_scaling" in dlg.forms["module"].kept.text()
    t = dlg.forms["module"].table
    t.item(1, 6).setText("")                                       # E_on at 150 degC missing
    dlg.preview()
    assert dlg.new_project is None and "E_on" in dlg.error
    dlg.load_spec(_spec("module_representative_example.json"))
    dlg.forms["module"].sw.model.setCurrentIndex(3)                # switch -> R_DS(on): one column, the second hidden
    t.item(0, 1).setText("1.6")
    t.item(1, 1).setText("2.4")
    dlg.preview()
    assert dlg.error is None and t.isColumnHidden(2)
    assert dlg.result["data"]["curves"]["v_on"]["values"][0] == pytest.approx([0.0, 1.6e-3 * 1640.0])
    dlg.close()


def test_applying_typed_motor_values_replaces_the_project_drive(win):
    from traction_workbench.project import builtin_project
    before = copy.deepcopy(win.state.project.data("drive"))
    dlg = win.pages["project"].enter_datasheet("motor", _spec("motor_example.json"))
    assert dlg.apply()
    assert win.state.drive.drive_id == "EXMOT-200" and win.state.project.modified
    assert win.state.project.sections["drive"].provenance["origin"] == "supplier"
    assert win.state.drive.inverter.current_limit_A_peak == 600.0          # design data kept from the project drive
    win.state.set_project(builtin_project())
    assert win.state.project.data("drive") == before
