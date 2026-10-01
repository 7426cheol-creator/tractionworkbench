"""The data cursor of every plot panel (offscreen): a MATLAB / Simulink-like cursor on the lines as drawn.

* the signals are the labelled data lines (limit lines drawn in axes coordinates and helper lines are not);
* the cursor snaps to the plotted samples by time (a step line reads the sample in force), shows the followed value
  and the value of every signal of the hovered axes (and its twin) at that instant;
* a legend click selects a signal - also a line of a twin axes, whose legend matplotlib would not pick - and the
  cursor follows it from any axes sharing the time axis; a second click returns to the nearest curve;
* two pins give Δx, Δy and the slope; the arrow keys move the last pin one sample; Esc removes the pins;
* the choice survives a redraw; the cursor off gives the page's own hover readout and click signal back;
* a curve in a plane (x not monotonic) is read at the sample nearest the mouse.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")


@pytest.fixture(scope="module")
def app():
    import matplotlib
    matplotlib.use("QtAgg")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


T = np.linspace(0.0, 80.0, 8001)


def two_rows(fig):
    fig.clear()
    ax1, ax2 = fig.subplots(2, 1, sharex=True)
    ax1.plot(T, 100 * np.sin(T), label="torque")
    ax1.plot(T, np.where(T >= 10, 50.0, 0.0), drawstyle="steps-post", label="command")
    ax1.axhline(120, color="r", label="limit")                      # a limit line, not a signal
    ax1.plot(T, T * 0 + 5, label="_helper")                        # an unlabelled helper line
    ax1.set_ylabel("torque [N·m]")
    ax1.legend(loc="upper right")
    ax2.plot(T, 600 + 10 * np.cos(T), label="V_dc")
    tw = ax2.twinx()
    tw.plot(T, 5 * T, color="k", label="i_bat")
    tw.set_ylabel("battery current [A]")
    ax2.set_ylabel("V_dc [V]")
    ax2.set_xlabel("time [ms]")
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = tw.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="upper left")                 # the twin's line in the host's legend


def plane(fig):
    fig.clear()
    ax = fig.add_subplot(111)
    th = np.linspace(0, 2 * np.pi, 721)
    ax.plot(100 * np.cos(th), 50 * np.sin(th), label="trajectory")
    ax.set_xlabel("i_d [A]")
    ax.set_ylabel("i_q [A]")


def panel(app, fn=two_rows):
    from traction_workbench.desktop.widgets import PlotPanel
    p = PlotPanel()
    p.resize(1000, 700)
    p.show()
    app.processEvents()
    p.draw(fn, name="t")
    app.processEvents()
    p.canvas.draw()
    return p


def move(p, ax, x, y):
    from matplotlib.backend_bases import MouseEvent
    px, py = ax.transData.transform((x, y))
    MouseEvent("motion_notify_event", p.canvas, px, py)._process()


def press(p, px, py, button=1):
    from matplotlib.backend_bases import MouseEvent
    MouseEvent("button_press_event", p.canvas, px, py, button=button)._process()


def key(p, k):
    from matplotlib.backend_bases import KeyEvent
    KeyEvent("key_press_event", p.canvas, k)._process()


def legend_entry(ax, text):
    t = next(t for t in ax.get_legend().get_texts() if t.get_text() == text)
    bb = t.get_window_extent()
    return bb.x0 + 3, 0.5 * (bb.y0 + bb.y1)


def test_signals_are_the_labelled_data_lines(app):
    p = panel(app)
    assert p.cursor.enabled and p.cursor_btn.isChecked()
    assert p.cursor.names == ["torque", "command", "V_dc", "i_bat"]
    assert [p.signal_box.itemText(i) for i in range(1, p.signal_box.count())] == p.cursor.names


def test_snaps_to_samples_and_reads_every_signal_of_the_hovered_axes(app):
    p = panel(app)
    ax1 = p.figure.axes[0]
    move(p, ax1, 10.0473, 100 * np.sin(10.0473))
    k, i = p.cursor.live
    assert p.cursor.names[k] == "torque" and T[i] == pytest.approx(10.05)          # nearest sample (10 us steps)
    text = p.readout.text()
    assert "time = 10.05 ms" in text and f"▶ torque = {100 * np.sin(T[i]):.6g} N·m" in text
    assert "command = 50 N·m" in text                                              # the step in force at 10.05
    move(p, ax1, 9.999, 0.0)
    assert "command = 0 N·m" in p.readout.text()                                   # before the step


def test_legend_selects_a_twin_signal_followed_from_any_shared_axes(app):
    p = panel(app)
    ax1, ax2 = p.figure.axes[:2]
    press(p, *legend_entry(ax2, "i_bat"))
    assert p.cursor.track == p.cursor.names.index("i_bat")
    assert p.signal_box.currentText() == "i_bat"
    move(p, ax1, 20.0, 0.0)                                                         # hovering the torque axes
    text = p.readout.text()
    assert text.split("│")[1].strip() == "▶ i_bat = 100 A"
    assert "torque = " in text and "V_dc = " in text
    press(p, *legend_entry(ax2, "i_bat"))                                          # again: nearest curve
    assert p.cursor.track is None and p.signal_box.currentIndex() == 0


def test_two_pins_give_delta_and_arrow_keys_step_one_sample(app):
    p = panel(app)
    ax1 = p.figure.axes[0]
    p.signal_box.setCurrentIndex(1 + p.cursor.names.index("i_bat"))                # the list selects too
    for x in (20.0, 30.0):
        move(p, ax1, x, 0.0)
        px, py = ax1.transData.transform((x, 0.0))
        press(p, px, py)
    pins = p.cursor.pin_text()
    assert "1: i_bat @ 20 ms = 100 A" in pins and "2: i_bat @ 30 ms = 150 A" in pins
    assert "Δx = 10 ms · Δy = 50 A" in pins and "Δy/Δx = 5 A/ms" in pins
    key(p, "right")
    assert "2: i_bat @ 30.01 ms = 150.05 A" in p.cursor.pin_text()
    key(p, "shift+left")
    assert "2: i_bat @ 29.91 ms" in p.cursor.pin_text()                           # 30.01 - 10 samples
    assert any(a.get_label() == "_cursor" for a in ax1.get_children())             # drawn (and exported)
    key(p, "escape")
    assert p.cursor.pins == [] and not any(a.get_label() == "_cursor" and a.get_visible() and not a.get_animated()
                                           for a in ax1.get_children())


def test_choice_survives_a_redraw_and_off_restores_hover_and_clicks(app):
    p = panel(app)
    p.signal_box.setCurrentIndex(1 + p.cursor.names.index("V_dc"))
    p.draw(two_rows, name="t", hover=lambda x, y, ax: f"hover {x:.1f}")
    app.processEvents()
    assert p.cursor.names[p.cursor.track] == "V_dc" and p.signal_box.currentText() == "V_dc"
    got = []
    p.clicked.connect(lambda x, y: got.append(x))
    p.set_cursor(False)
    assert not p.signal_box.isVisible() and p.readout.text() == ""
    ax1 = p.figure.axes[0]
    move(p, ax1, 12.0, 0.0)
    assert p.readout.text() == "hover 12.0"
    px, py = ax1.transData.transform((12.0, 0.0))
    press(p, px, py)
    assert got and got[0] == pytest.approx(12.0, abs=0.2)


def test_a_curve_in_a_plane_is_read_at_the_nearest_sample(app):
    p = panel(app, plane)
    ax = p.figure.axes[0]
    move(p, ax, 0.0, 51.0)                                                          # near the top of the ellipse
    k, i = p.cursor.live
    s = p.cursor.signals[k]
    assert not s.mono and s.x[i] == pytest.approx(0.0, abs=1.0) and s.y[i] == pytest.approx(50.0, abs=0.1)
    assert p.readout.text().startswith("▶ trajectory: x = ")


def test_map_panels_start_with_the_cursor_off(app):
    from traction_workbench.desktop.widgets import PlotPanel
    p = PlotPanel(cursor=False)
    assert not p.cursor.enabled and not p.cursor_btn.isChecked()
