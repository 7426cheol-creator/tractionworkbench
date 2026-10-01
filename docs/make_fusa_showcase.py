#!/usr/bin/env python3
"""README showcase of the functional-safety design work: curated captures of the fault simulation page.

    python docs/make_fusa_showcase.py                     # writes docs/screenshots/fusa_*.jpg (English)
    python docs/make_fusa_showcase.py --lang ko           # the same captures in Korean
    python docs/make_fusa_showcase.py --out out/showcase  # somewhere else

The app runs headless (offscreen) at twice its logical size, so text and plots stay crisp when GitHub scales the
images down; each capture is the part of the page that shows the point (not the whole window), put into a state
by the same code paths a person uses: the same fault with the hard active short circuit and two transition
strategies, the strategy and requirement editors, the safety-case reading and the verification matrix over the
whole scenario catalog, the safety-case report the page saves, the transient zoom and the data cursor, the hardware
selection by the DC voltage cycling while the vehicle rolls with the processor lost, and the reference verification
(the built-in reference's hierarchy, a proposal with its evidence).  Deterministic: light theme, English by default,
built-in synthetic project.
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
         "fusa_waveform_soft_asc": 1600, "fusa_transient_zoom": 1600, "fusa_data_cursor": 1600,
         "fusa_hw_vdc_cycling": 1600, "fusa_hw_vdc_zoom": 1600, "fusa_reference_hierarchy": 1600,
         "fusa_reference_proposal": 1600}                         # px after scaling; others DEFAULT_WIDTH
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
    ap.add_argument("--lang", choices=("en", "ko"), default="en", help="the language of the captured pages")
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
    set_language(a.lang)
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

    def cursor_demo(panel, signal_word, times_ms, size, redraw=True):
        """Follow the signal whose name contains ``signal_word`` and pin the cursor at two instants."""
        from matplotlib.backend_bases import MouseEvent
        if redraw:
            win.resize(*size)
            settle()
        panel.canvas.draw()
        settle()
        k = next(i for i, n in enumerate(panel.cursor.names) if signal_word in n.lower())
        panel.signal_box.setCurrentIndex(k + 1)
        s = panel.cursor.signals[k]
        ax = panel.figure.axes[0]
        for t_ms in times_ms:
            i = s.index(t_ms)
            px, py = s.ax.transData.transform((s.x[i], s.y[i]))
            MouseEvent("motion_notify_event", panel.canvas, px, py)._process()
            MouseEvent("button_press_event", panel.canvas, px, py, button=1)._process()
            settle()
        x_live = times_ms[0] + 2.4 if times_ms[1] - times_ms[0] < 2.0 else 0.5 * (times_ms[0] + times_ms[1])
        px, py = ax.transData.transform((x_live, sum(ax.get_ylim()) / 2))
        MouseEvent("motion_notify_event", panel.canvas, px, py)._process()
        settle()

    def zoom_cycle(panel, res, size):
        """Zoom the synchronized waveforms to the first periods of a bridge cycling (as the zoom tool would, every axes
        to its visible signals), follow the DC-link voltage and pin two consecutive switches to six-switch-off."""
        import numpy as np
        win.resize(*size)
        settle()
        panel.canvas.draw()
        settle()
        from traction_workbench.insight.fault import bridge_cycling
        t = np.asarray(res["trace"]["t"], dtype=float) * 1e3
        br = np.asarray(res["trace"]["bridge"]).astype(int)
        t6 = t[np.where((br[1:] == 3) & (br[:-1] != 3))[0] + 1]            # entries into six-switch-off ...
        t6 = t6[t6 >= bridge_cycling(res)["first_s"] * 1e3 - 1e-6]           # ... once the cycling runs
        a, b = float(t6[0]) - 3.0, float(t6[3]) + 2.0
        panel.figure.axes[0].set_xlim(a, b)
        seen = {}
        for s in panel.cursor.signals:
            m = (s.x >= a) & (s.x <= b)
            if m.any():
                seen.setdefault(s.ax, []).append(s.y[m])
        for ax, ys in seen.items():
            y = np.concatenate(ys)
            lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
            pad = 0.15 * ((hi - lo) or 1.0)
            ax.set_ylim(lo - pad, hi + pad)
        v = np.asarray(res["trace"]["v_dc"], dtype=float)
        lows = []                                   # the link's low point (X_low) just before each of the two switches
        for x in t6[:2]:
            m = np.where((t >= x - 0.3) & (t <= x + 0.05))[0]
            lows.append(float(t[m[np.argmin(v[m])]]))
        cursor_demo(panel, "dc-link voltage", tuple(lows), size, redraw=False)

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
        if react == "asc_low":
            # the transient zoomed to the simulated samples, every extreme with value and instant
            fs.tabs.setCurrentWidget(fs.tab_zoom)
            shot(fs.tabs, "fusa_transient_zoom", (1240, 1180))
            # the data cursor on the zoomed transient: the battery current followed, two pins (dt, dI)
            cursor_demo(fs.p_zoom, "battery", (10.49, 11.10), (1240, 1180))
            shot(fs.tabs, "fusa_data_cursor", (1240, 1180))
            fs.p_zoom.cursor.clear_pins()
            fs.p_zoom.signal_box.setCurrentIndex(0)
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

    # 4b. the processor lost while rolling: the hardware selection by the DC voltage cycles (FW <-> ASC)
    fs.top.setCurrentIndex(0)
    fs.preset.setCurrentIndex(fs.preset.findData("hw_vdc_rolling"))
    fs._load_preset()
    fs.run()
    fs.tabs.setCurrentWidget(fs.p_wave)
    shot(fs.tabs, "fusa_hw_vdc_cycling", (1240, 1180))
    # the cycling zoomed, the cursor on the DC-link voltage pinned at two consecutive switches to 6SO: dt = the period
    zoom_cycle(fs.p_wave, fs.last, (1240, 1180))
    shot(fs.tabs, "fusa_hw_vdc_zoom", (1240, 1180))
    fs.p_wave.cursor.clear_pins()
    fs.p_wave.signal_box.setCurrentIndex(0)
    fs.tabs.setCurrentWidget(fs.insight)
    shot(fs.tabs, "fusa_hw_vdc_reading", (1240, 780), height=560)

    # 4c. reference verification: the built-in reference classified (SG -> TLSR -> FSR -> TSR -> SM), and a proposal
    win.show_page("reference")
    rf = win.pages["reference"]
    rf.load_builtin("customer_inverter")
    rf.tabs.setCurrentWidget(rf.tab_hier)
    for k in range(rf.tree.topLevelItemCount()):                  # open one TLSR under the first goal
        top = rf.tree.topLevelItem(k)
        if top.data(0, Qt.UserRole) == "A-03":
            top.setExpanded(True)
            for j in range(top.childCount()):
                top.child(j).setExpanded(True)
    shot(rf, "fusa_reference_hierarchy", (1400, 960))
    rf.load_builtin("example")
    rf.profile.setCurrentIndex(rf.profile.findData("illustrative"))
    rf.search.setText("EX-PROP-01")
    rf.run()
    rf.matrix.selectRow(0)
    rf.tabs.setCurrentWidget(rf.p_ev)
    shot(rf, "fusa_reference_proposal", (1400, 960))
    rf.search.setText("")
    win.show_page("fault_sim")

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
