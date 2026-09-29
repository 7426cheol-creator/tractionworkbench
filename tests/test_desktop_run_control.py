"""Run control and the inputs behind a shown result, driven through the real pages (UX review A1, A2, A3, A7, D1, H3).

* a cancelled calculation gives the page back: the run button works again, the banner says the run was cancelled
  (a previous result stays on screen, labelled as such) and the next run gives a result;
* the page bar's button runs the page and turns into that page's cancel button while its calculation runs;
* a result remembers the inputs it was computed from: an edited input marks it (page banner and verdict banner name
  the field, before -> now), saving the record asks first, and reverting the input clears the mark;
* the source-resistance row is shown only when Vdc is a battery OCV (D1);
* the window's size and last page are kept for a person's session and never by the self-test (H3).
"""

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
    app.processEvents()
    w.show_page("decision")
    yield w
    w.close()
    set_language(before)


def _settle():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def _cancel_at_first_step(m, seen=None, win=None):
    """The next task is cancelled at its first progress checkpoint (as if [cancel] were pressed while it ran)."""
    from traction_workbench.desktop import worker
    orig = worker.Task._progress

    def progress(self, frac, msg="", until=None):
        if seen is not None:
            seen.append(win.run_actions["decision"].text())
        self.cancel()
        return orig(self, frac, msg, until)
    m.setattr(worker.Task, "_progress", progress)


def test_a_cancelled_evaluation_gives_the_page_back(win, monkeypatch):
    page = win.pages["decision"]
    assert page.result is None
    seen = []
    with monkeypatch.context() as m:
        _cancel_at_first_step(m, seen, win)
        page.run()
    _settle()
    assert seen and seen[0].startswith("■")                  # while running, the page bar offered the cancel
    assert page.run_btn.isEnabled() and not win.runner.busy()
    assert win.run_actions["decision"].text().startswith("▶")
    assert page.result is None and "취소" in page.banner.text.text()
    page.run()                                               # and the next run works
    _settle()
    assert page.result is not None and page.banner.big.text() == "PASS"
    with monkeypatch.context() as m:                         # a cancelled re-run keeps the result, and says so
        _cancel_at_first_step(m)
        page.run()
    _settle()
    assert page.result is not None and page.banner.big.text() == "PASS"
    assert "이전 결과" in page.banner.text.text() and page.run_btn.isEnabled()


def test_the_page_bar_runs_the_shown_tab_and_lists_the_others(win):
    win.show_page("safety")
    _settle()
    targets = win.run_targets("safety")
    btn = win.run_actions["safety"]
    assert targets and btn.isVisible() and btn.text().startswith("▶")
    assert (btn.menu() is not None) == (len(targets) > 1)
    win.show_page("decision")
    _settle()


def test_an_edited_input_marks_the_result_and_reverting_clears_it(win):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    page = win.pages["decision"]
    if page.result is None:
        page.run()
    _settle()
    ban = win.banners["decision"]
    assert ban.property("state") != "inputs" and not win.input_changes("decision")
    t0 = page.torque.value()
    page.torque.setValue(t0 + 250.0)
    _settle()
    diffs = win.input_changes("decision")
    assert ban.property("state") == "inputs" and len(diffs) == 1
    field, then, now = diffs[0]
    assert "150" in then and "400" in now and "⚠" in page.banner.text.text()
    assert page.result["record"]["requirement"]["target_Nm"] == t0     # the record is still the earlier inputs' one
    app.setProperty("twb_questions", [])
    assert page._confirm_current()                           # the headless run answers yes, and the question is kept
    asked = list(app.property("twb_questions") or [])
    assert asked and field in asked[0]
    page.torque.setValue(t0)
    _settle()
    assert ban.property("state") != "inputs" and not win.input_changes("decision")
    assert "⚠" not in page.banner.text.text()
    app.setProperty("twb_questions", [])
    assert page._confirm_current() and not app.property("twb_questions")


def test_a_preset_with_other_inputs_marks_the_shown_verdict(win):
    page = win.pages["decision"]
    if page.result is None:
        page.run()
    _settle()
    i0 = page.presets.currentIndex()
    page.presets.setCurrentIndex(1)                          # the low-voltage example: its own verdict is FAIL
    _settle()
    assert win.banners["decision"].property("state") == "inputs" and page.banner.big.text() == "PASS"
    assert any("450" in now for _f, _then, now in win.input_changes("decision"))
    page.presets.setCurrentIndex(i0)
    _settle()
    assert not win.input_changes("decision")


def test_the_source_resistance_row_follows_the_source_kind(win):
    page = win.pages["decision"]
    form, row = page._req_form, page.src_row
    i0 = page.vdc_kind.currentIndex()
    try:
        for i in range(page.vdc_kind.count()):
            page.vdc_kind.setCurrentIndex(i)
            assert form.isRowVisible(row) == (page.vdc_kind.itemData(i) == "battery_ocv")
    finally:
        page.vdc_kind.setCurrentIndex(i0)
        _settle()


def test_the_window_session_is_kept_for_people_not_for_the_self_test(win, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    kept = QSettings(str(tmp_path / "session.ini"), QSettings.IniFormat)
    orig = win.settings
    win.settings = kept
    try:
        win.show_page("thermal")
        win._save_session()
        assert kept.value("window/page") is None             # the self-test always starts from the same window
        app.setProperty("twb_selftest", False)
        win._save_session()
        assert kept.value("window/page") == "thermal" and kept.value("window/geometry") is not None
        win.show_page("decision")
        win._restore_session()
        assert win.current_page() == "thermal"
    finally:
        app.setProperty("twb_selftest", True)
        win.settings = orig
        win.show_page("decision")
        _settle()


def test_the_verdict_banner_folds_its_record_details_and_the_summary_keeps_every_row(win):
    """UX review E2/E4: the banner says the conclusion and why; the requirement text, scope and record identity fold
    under [details]; the summary tables are as tall as their rows (a short window scrolls, never a bare header)."""
    page = win.pages["decision"]
    if page.result is None:
        page.run()
    _settle()
    b, rec = page.banner, page.result["record"]
    assert rec["record_id"] not in b.text.text() and rec["record_id"] in b.more.text()
    assert b.more_btn.isVisible() and not b.more.isVisible()
    b.set_details_shown(True)
    _settle()
    assert b.more.isVisible()
    b.set_details_shown(False)
    page.tabs.setCurrentWidget(page.summary_tab)
    _settle()
    for t in (page.layers_table, page.key_table):
        assert t.rowCount() and sum(t.rowHeight(r) for r in range(t.rowCount())) <= t.viewport().height() + 1
    page.tabs.setCurrentIndex(0)
    _settle()
