"""The reference verification page, driven through its real code paths (offscreen, synchronous runner).

* the built-in packages load: the neutral example and the customer inverter reference (classified and traced);
* the level filter narrows the matrix; the hierarchy tab shows the tree from the goals down, the gaps and the
  proposals; selecting a node selects the item in the matrix (widening the filters that hide it);
* a proposal runs and reads PASS with its evidence drawn; the HTML report and the matrix CSV are written;
* a value entered in the parameter registry is used as USER for the next run.
"""

from __future__ import annotations

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
    w.show_page("reference")
    app.processEvents()
    yield w
    w.close()
    set_language(before)


def _top_ids(tree):
    from PySide6.QtCore import Qt
    return [tree.topLevelItem(k).data(0, Qt.UserRole) for k in range(tree.topLevelItemCount())]


def test_builtin_packages_and_the_hierarchy(win):
    page = win.pages["reference"]
    keys = [page.builtin.itemData(k) for k in range(page.builtin.count())]
    assert keys == ["example", "customer_inverter"]
    page.load_builtin("customer_inverter")
    assert len(page.package["items"]) >= 370 and page.matrix.rowCount() == len(page.package["items"])
    tops = _top_ids(page.tree)
    assert {"A-02", "A-03", "A-07", "PROP-SG-HV"} <= set(tops)
    assert page.t_gaps.rowCount() > 20 and page.t_props.rowCount() >= 8
    labels = ([page.profile.itemText(k) for k in range(page.profile.count())]           # the page's own words:
              + [page.only_customer.itemText(k) for k in range(page.only_customer.count())]  # "원문 요구",
              + [page.builtin.itemText(k) for k in range(page.builtin.count())]              # never "고객 요구"
              + [page.matrix.horizontalHeaderItem(j).text() for j in range(page.matrix.columnCount())]
              + [page.t_gaps.item(r, 0).text() for r in range(page.t_gaps.rowCount())]
              + [page.group.itemText(k) for k in range(page.group.count())] + [page.pkg_label.text()])
    assert not [x for x in labels if "고객" in x or "customer" in x.lower()]
    page.level.setCurrentIndex(page.level.findData("SM"))
    assert page.matrix.rowCount() == sum(1 for it in page.package["items"] if it.get("level") == "SM")
    page.level.setCurrentIndex(page.level.findData("REQ"))
    n_req = page.matrix.rowCount()
    assert 0 < n_req < len(page.package["items"])
    page.select_item("RULE-01")                                  # hidden by the filter: the filter widens
    assert page.level.currentData() is None and "RULE-01" in page._rows_shown
    sel = sorted({i.row() for i in page.matrix.selectedIndexes()})
    assert sel and page._rows_shown[sel[0]] == "RULE-01"


def test_a_proposal_runs_with_evidence_and_the_reports(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog
    page = win.pages["reference"]
    page.load_builtin("example")
    page.profile.setCurrentIndex(page.profile.findData("illustrative"))
    page.search.setText("EX-PROP-01")
    assert page._rows_shown == ["EX-PROP-01"]
    page.run(selected=False)
    row = next(r for r in page.last["rows"] if r["id"] == "EX-PROP-01")
    assert row["verdict"] == "PASS" and row["proposed"]
    assert page.matrix.item(0, 1).text().startswith("PASS") and "제안" in page.matrix.item(0, 2).text()
    page.matrix.selectRow(0)
    page.tabs.setCurrentWidget(page.p_ev)
    QApplication.instance().processEvents()
    assert page.p_ev._draw is not None and page.p_ev.cursor.signals             # the data cursor on the evidence
    props = [page.t_props.item(r, 0).text() for r in range(page.t_props.rowCount())]
    assert "EX-PROP-01" in props
    html, csv = tmp_path / "r.html", tmp_path / "m.csv"
    page.export_html(str(html))
    page.export_csv(str(csv))
    assert "Hierarchy" in html.read_text(encoding="utf-8") and "EX-PROP-01" in csv.read_text(encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "p.json"), ""))
    page.save_package()
    assert (tmp_path / "p.json").exists()
    page.search.setText("")


def test_an_entered_value_is_used_as_user(win):
    page = win.pages["reference"]
    page.load_builtin("example")
    page.profile.setCurrentIndex(page.profile.findData("customer"))
    r = next(k for k in range(page.params.rowCount()) if page.params.item(k, 0).text() == "X_UPP")
    page.params.item(r, 4).setText("100")
    assert page.values == {"X_UPP": 100.0}
    page.search.setText("EX-PROP-01")
    page.run(selected=False)
    row = next(x for x in page.last["rows"] if x["id"] == "EX-PROP-01")
    assert "X_UPP" not in row["open"]                            # entered: no longer waiting for it
    page._clear_values()
    assert page.values == {}
    page.search.setText("")
