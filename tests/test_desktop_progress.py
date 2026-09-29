"""Long calculations say where they are, stop when cancelled and show the verdict first (UX review A5, A3), driven
through the real task runner and decision page.

* the runner never shows a cancelled task's result, even one it finished computing (a stop that a handler absorbed);
  a partial result is shown before the final one, and never after a cancel;
* engine loop steps reach the status bar as "stage · loop k/n", with the running time, and a cancel stops the
  calculation at its next step;
* the decision page shows the verdict with its operating point before the PWM consequences and analyses: saving
  waits for the complete record, the reading and the banner name what is still computing; a cancel (or a failure)
  after the verdict keeps it and names what was not computed.
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


def _run(win, fn, *, key="t-progress", on_partial=None):
    got = {"result": [], "partial": [], "finished": []}
    win.runner.task_finished.connect(lambda k, o: got["finished"].append(o) if k == key else None)
    win.runner.run(key, "test", fn, got["result"].append, on_partial=on_partial or got["partial"].append)
    _settle()
    return got


def test_a_cancelled_task_never_shows_its_result(win):
    def absorbs_the_stop(progress):
        win.runner.cancel("t-progress")       # [cancel] pressed while it computes ...
        try:
            progress(0.5, "x")
        except BaseException:  # noqa: BLE001 - ... and some handler swallows the stop
            pass
        return "a result computed anyway"
    got = _run(win, absorbs_the_stop)
    assert got["result"] == [] and got["finished"] == ["cancelled"]

    def fails_on_its_way_out(progress):
        win.runner.cancel("t-progress")
        try:
            progress(0.5, "x")
        except BaseException:  # noqa: BLE001
            raise ValueError("raised while stopping") from None
    errors = []
    win.runner.failed.connect(lambda *a: errors.append(a))
    got = _run(win, fails_on_its_way_out)                    # no error dialog for a cancelled calculation
    assert got["finished"] == ["cancelled"] and errors == []


def test_a_partial_result_comes_first_and_never_after_a_cancel(win):
    def two_parts(progress):
        progress.partial("verdict")
        return "everything"
    got = _run(win, two_parts)
    assert got["partial"] == ["verdict"] and got["result"] == ["everything"] and got["finished"] == ["ok"]

    def cancelled_before_its_part(progress):
        win.runner.cancel("t-progress")
        progress.partial("verdict")
        return "everything"
    got = _run(win, cancelled_before_its_part)
    assert got["partial"] == [] and got["result"] == [] and got["finished"] == ["cancelled"]


def test_engine_steps_reach_the_status_bar_and_a_cancel_stops_at_the_next(win):
    from traction_workbench import progress as P
    seen, steps = [], []

    def loop(progress):
        progress(0.1, "1/2 stage", 0.9)
        win.runner.started_at["t-progress"] -= 5.0          # as if it had run for five seconds
        with P.span(40, "torque capability") as sp:
            for i in range(40):
                sp.step("scan")
                steps.append(i)
                seen.append(win.statusBar().currentMessage())
                if i == 6:
                    win._cancel_all()                        # the status bar's [cancel all]
                    seen.append(win.statusBar().currentMessage())
        return "done"
    got = _run(win, loop)
    assert got["result"] == [] and got["finished"] == ["cancelled"]
    assert steps == list(range(7))                           # stopped at the step after the cancel
    assert seen[0] == "test: 1/2 stage · 토크 능력 1/40 (스캔) · 5 s"
    assert "취소 요청됨" in seen[-1] and win.progress.isHidden()


def _decision_run(win, monkeypatch, preset: int, stop=None):
    """Run a decision preset, recording the partial and final displays (``stop(msg)`` may cancel at a stage)."""
    from traction_workbench.desktop import worker
    page = win.pages["decision"]
    page.presets.setCurrentIndex(preset)
    shown = []
    real_partial, real_show = page._show_partial, page._show

    def partial(res):
        real_partial(res)
        shown.append(("partial", page.save_json.isEnabled(), page.banner.text.text(), page.insight.text(),
                      [page.an_tabs.tabText(i) for i in range(page.an_tabs.count())], list(res["pending"])))

    def final(res):
        real_show(res)
        shown.append(("final", page.save_json.isEnabled(), page.banner.text.text(), page.insight.text(),
                      [page.an_tabs.tabText(i) for i in range(page.an_tabs.count())], []))
    monkeypatch.setattr(page, "_show_partial", partial)
    monkeypatch.setattr(page, "_show", final)
    if stop is not None:
        orig = worker.Task._progress

        def progress(self, frac, msg="", until=None):
            if stop(msg):
                self.cancel()
            return orig(self, frac, msg, until)
        monkeypatch.setattr(worker.Task, "_progress", progress)
    page.run()
    _settle()
    return page, shown


def test_the_verdict_comes_first_and_the_record_is_saved_complete(win, monkeypatch):
    page, shown = _decision_run(win, monkeypatch, 0)         # 600 V: PASS, with the bottleneck contribution
    kinds = [s[0] for s in shown]
    assert kinds == ["partial", "final"]
    _, saves, banner, reading, tabs, pending = shown[0]
    assert pending == ["pwm", "dominance"] and not saves
    assert "계산 중" in banner and "PWM" in banner and "병목 기여도" in banner
    assert "아직 계산 중" in reading and "병목 기여도" in reading
    assert tabs == ["⏳"]
    _, saves, banner, reading, tabs, _ = shown[1]
    assert saves and "계산 중" not in banner and "아직 계산 중" not in reading
    assert "병목·완화" in tabs and any("PWM" in t for t in tabs)
    assert page.banner.big.text() == "PASS" and page.result["record"]["analyses"].get("dominance")


def test_a_cancel_after_the_verdict_keeps_it_and_names_what_was_not_computed(win, monkeypatch):
    page, shown = _decision_run(win, monkeypatch, 0, stop=lambda msg: msg.startswith("3/"))   # at the analyses
    assert [s[0] for s in shown] == ["partial"]
    assert page.banner.big.text() == "PASS" and not page.save_json.isEnabled()
    text = page.banner.text.text()
    assert "끝나지 않은 계산" in text and "병목 기여도" in text and "취소" in text
    assert "계산되지 않은 항목" in page.insight.text() and "아직 계산 중" not in page.insight.text()
    assert [page.an_tabs.tabText(i) for i in range(page.an_tabs.count())] == ["—"]
    assert page.run_btn.isEnabled() and not win.runner.busy()
    _page, shown = _decision_run(win, monkeypatch, 0, stop=lambda msg: msg.startswith("1/"))  # before its verdict
    assert shown == [] and page.banner.big.text() == "PASS"
    assert "이전 결과" in page.banner.text.text() and "추가 계산을 취소" not in page.banner.text.text()
    monkeypatch.undo()                                       # no more stops: a full run completes the record again
    page.run()
    _settle()
    assert page.save_json.isEnabled() and "끝나지 않은" not in page.banner.text.text()


def test_a_failed_stage_after_the_verdict_keeps_it(win, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from traction_workbench import service as S
    app = QApplication.instance()

    def broken(rec, case):
        raise RuntimeError("analysis broke")
    monkeypatch.setattr(S, "decision_analyses", broken)
    app.setProperty("twb_errors", [])
    try:
        page, shown = _decision_run(win, monkeypatch, 0)
        assert [s[0] for s in shown] == ["partial"] and page.banner.big.text() == "PASS"
        assert "analysis broke" in page.banner.text.text() and not page.save_json.isEnabled()
        assert any("analysis broke" in e for e in app.property("twb_errors") or [])
    finally:
        app.setProperty("twb_errors", [])


@pytest.mark.filterwarnings("ignore:Enum value .*AA_UseHighDpiPixmaps:DeprecationWarning")
def test_a_hidden_panel_draws_nothing_until_it_is_shown(win):          # (matplotlib's toolbar asks Qt about it)
    """A result or a note on a tab nobody looks at costs no drawing (UX review A4); while a calculation runs, text
    layout in the window's thread would also take turns with the calculation for the interpreter."""
    from PySide6.QtWidgets import QTabWidget, QWidget
    from traction_workbench.desktop.widgets import PlotPanel
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "front")
    panel = PlotPanel()
    tabs.addTab(panel, "back")
    tabs.show()
    _settle()
    drawn = []
    panel.canvas.draw = lambda *a, **k: drawn.append("draw")      # the render an idle draw runs
    panel.placeholder("a note on a hidden tab")
    panel.draw(lambda fig: fig.add_subplot().plot([0, 1], [1, 0]))
    panel.placeholder("another note")
    _settle()
    assert drawn == [] and panel._stale
    tabs.setCurrentWidget(panel)
    _settle()
    assert drawn == ["draw"] and not panel._stale
    tabs.close()
