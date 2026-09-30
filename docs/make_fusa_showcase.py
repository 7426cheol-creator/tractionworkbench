#!/usr/bin/env python3
"""README showcase of the functional-safety design work: curated captures of the fault simulation page.

    python docs/make_fusa_showcase.py                     # writes docs/screenshots/fusa_*.jpg
    python docs/make_fusa_showcase.py --out out/showcase  # somewhere else

The app runs headless (offscreen) at twice its logical size, so text and plots stay crisp when GitHub scales the
images down; each capture is the part of the page that shows the point (not the whole window), put into a state
by the same code paths a person uses: the same fault with the hard active short circuit and two transition
strategies, the strategy and requirement editors, the safety-case reading and the verification matrix over the
whole scenario catalog, and the safety-case report the page saves.  Deterministic: light theme, Korean, built-in
synthetic project.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")

ROOT = Path(__file__).resolve().parents[1]
WIDTH = {"fusa_hero_transition": 1800, "fusa_report": 1600, "fusa_waveform_hard_asc": 1600,
         "fusa_waveform_soft_asc": 1600}                          # px after scaling; others DEFAULT_WIDTH
DEFAULT_WIDTH = 1400


def save(pixmap, path: Path, width: int) -> None:
    from PIL import Image
    tmp = Path(tempfile.mkdtemp()) / "cap.png"
    pixmap.save(str(tmp))
    im = Image.open(tmp).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(path, quality=90, subsampling=0, optimize=True, progressive=True)
    print(f"{path.name}: {im.width} x {im.height}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    import matplotlib
    matplotlib.use("QtAgg")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from traction_workbench import api
    from traction_workbench.desktop import theme
    from traction_workbench.desktop.main_window import MainWindow
    from traction_workbench.desktop.worker import TaskRunner
    from traction_workbench.i18n import set_language

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setProperty("twb_selftest", True)          # questions answered yes, no modal boxes
    set_language("ko")
    theme.apply(app, "light")
    TaskRunner.synchronous = True
    win = MainWindow()
    win.show()
    win.show_page("fault_sim")
    fs = win.pages["fault_sim"]
    split = fs.top.widget(0)

    def settle():
        for _ in range(6):
            app.processEvents()

    def shot(widget, name, size, height=None):
        """``widget`` at window ``size`` (logical px); ``height``: keep only the top part (logical px)."""
        win.resize(*size)
        settle()
        pm = widget.grab()
        if height is not None:
            r = pm.devicePixelRatio()
            pm = pm.copy(0, 0, int(pm.width()), int(min(pm.height(), height * r)))
        save(pm, out / f"{name}.jpg", WIDTH.get(name, DEFAULT_WIDTH))

    # 1. the same fault, the same initial condition: hard ASC vs transition strategies
    fs.preset.setCurrentIndex(fs.preset.findData("cs_offset"))
    fs._load_preset()
    fs.speed.setValue(6000.0)
    keep = ("asc_low", "FW2_ASC_LOW", "SOFT_ASC_V")
    for i in range(fs.cand_list.count()):
        it = fs.cand_list.item(i)
        it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in keep else Qt.Unchecked)
    fs.run_compare()
    split.setSizes([0, 4000])                      # the results take the page
    fs.tabs.setCurrentWidget(fs.cmp_view)
    fs.cmp_view.setCurrentIndex(1)                 # plots and table
    shot(fs.tabs, "fusa_hero_transition", (1240, 1080))
    fs.cmp_view.setCurrentIndex(0)                 # the reading, candidate by candidate
    shot(fs.tabs, "fusa_transition_reading", (1240, 780), height=330)

    # 1b. the synchronized waveforms of one run each: the hard ASC and the voltage-ramp soft ASC, same fault
    for react, name in (("asc_low", "fusa_waveform_hard_asc"), ("SOFT_ASC_V", "fusa_waveform_soft_asc")):
        fs.override.setCurrentIndex(fs.override.findData(react))
        fs.run()
        fs.tabs.setCurrentWidget(fs.p_wave)
        shot(fs.tabs, name, (1240, 1180))
    fs.tabs.setCurrentWidget(fs.tab_timeline)      # the soft ASC's steps on the event timeline
    shot(fs.tabs, "fusa_timeline_soft_asc", (1240, 900))
    fs.override.setCurrentIndex(0)

    # 2. the design editor: a reaction strategy, the mechanisms (typed fields, no JSON)
    ed = fs.editor
    fs.top.setCurrentWidget(ed)
    ed.tabs.setCurrentIndex(4)
    ed.l_strat.setCurrentRow(next(i for i in range(ed.l_strat.count()) if ed.l_strat.item(i).text() == "SOFT_ASC_V"))
    ed.t_steps.selectRow(0)
    shot(ed, "fusa_strategy_editor", (1240, 780))
    ed.tabs.setCurrentIndex(2)
    ed.t_tsr.selectRow(next(r for r, rec in enumerate(ed.t_tsr.records) if rec["id"] == "TSR-08"))
    shot(ed, "fusa_requirement_editor", (1240, 780))
    ed.tabs.setCurrentIndex(3)
    r_tq = next(r for r, rec in enumerate(ed.t_mech.records) if rec["id"] == "SM-TQ")
    col = next(j for j, c in enumerate(ed.t_mech.cols) if c.key == "p_time")
    ed.t_mech.selectRow(r_tq)
    ed.t_mech.item(r_tq, col).setText("5")
    shot(ed, "fusa_mechanism_editor", (1240, 780))
    ed.revert()

    # 3. the safety case of the project's design: the verification matrix over the whole catalog
    fs.top.setCurrentWidget(fs.case_tab)
    for i in range(fs.verif_list.count()):
        fs.verif_list.item(i).setCheckState(Qt.Checked)
    fs.run_verification()
    fs.case_tabs.setCurrentIndex(fs.case_tabs.indexOf(fs.t_matrix.parentWidget()))
    shot(fs.case_tab, "fusa_verification_matrix", (1240, 780))
    html = api.fault_report({"overrides": fs.variant(), "matrix": fs._current_matrix()}, win.state.project)

    # 4. the static review catches a contradiction before any simulation: a debounce beyond the FDTI budget
    fs.top.setCurrentWidget(ed)
    ed.tabs.setCurrentIndex(3)
    ed.t_mech.selectRow(r_tq)
    ed.t_mech.item(r_tq, col).setText("25")
    fs.top.setCurrentWidget(fs.case_tab)
    fs.run_review()
    fs.case_tabs.setCurrentWidget(fs.i_case)
    shot(fs.case_tab, "fusa_design_review", (1240, 780))
    ed.revert()

    # 5. the report the page saves, rendered by a browser (a person opens the HTML file): its verification section
    page = Path(tempfile.mkdtemp()) / "safety_case.html"
    page.write_text(html, encoding="utf-8")
    try:
        from playwright.sync_api import sync_playwright
        exe = os.environ.get("TWB_CHROMIUM") or next(
            (str(p) for p in Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")).glob(
                "chromium-*/chrome-linux/chrome")), None)
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            pg = b.new_page(viewport={"width": 1400, "height": 860}, device_scale_factor=2)
            pg.goto(page.as_uri())
            pg.wait_for_timeout(300)
            pg.evaluate("window.scrollTo(0, [...document.querySelectorAll('h2')]"
                        ".find(h => h.textContent.startsWith('5.')).offsetTop - 16)")
            pg.wait_for_timeout(200)
            tmp = Path(tempfile.mkdtemp()) / "report.png"
            pg.screenshot(path=str(tmp))
            b.close()
        from PIL import Image
        im = Image.open(tmp).convert("RGB")
        w = WIDTH["fusa_report"]
        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
        im.save(out / "fusa_report.jpg", quality=90, subsampling=0, optimize=True, progressive=True)
        print(f"fusa_report.jpg: {im.width} x {im.height}")
    except Exception as exc:  # noqa: BLE001 - the browser capture is optional
        print(f"report capture skipped ({type(exc).__name__}: {exc})")
    errors = app.property("twb_errors") or []
    if errors:
        print("errors shown during the captures:", errors, file=sys.stderr)
        return 1
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
