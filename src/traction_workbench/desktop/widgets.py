"""Reusable widgets: plot panel with export, verdict banner, claim tree, key-value tables, number fields."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QSizePolicy, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..i18n import tr
from ..plots import style as S
from . import theme


def fmt(v, digits: int = 6) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, np.integer)):
        return f"{int(v):d}"
    if isinstance(v, (float, np.floating)):
        v = float(v)
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "∞" if v > 0 else "−∞"
        if v != 0 and (abs(v) >= 1e7 or abs(v) < 1e-4):
            return f"{v:.{digits - 2}e}"
        return f"{v:.{digits}g}"
    if isinstance(v, (list, tuple)):
        return ", ".join(fmt(x, digits) for x in v)
    return str(v)


# ---------------------------------------------------------------------------
# plotting
# ---------------------------------------------------------------------------

class PlotPanel(QWidget):
    """Matplotlib canvas + navigation toolbar + PNG/SVG/PDF/CSV export + hover readout line."""

    clicked = Signal(float, float)

    def __init__(self, parent=None, hint: str | None = None, min_height: int = 360):
        super().__init__(parent)
        self.figure = Figure(layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.setMinimumHeight(min_height)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setIconSize(self.toolbar.iconSize() * 0.85)
        self.readout = QLabel("")
        self.readout.setObjectName("Readout")
        self.readout.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.toolbar)
        top.addStretch(1)
        for label, fn in (("PNG", lambda: self.export("png")), ("SVG", lambda: self.export("svg")),
                          ("PDF", lambda: self.export("pdf")), ("CSV", self.export_csv)):
            b = QPushButton(label)
            b.setToolTip(tr(f"{label}로 내보내기", f"export as {label}"))
            b.clicked.connect(fn)
            b.setFixedHeight(26)
            top.addWidget(b)
            if label == "CSV":
                self.csv_button = b
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        lay.addLayout(top)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.readout)
        self._draw = None
        self._csv = None
        self._hover = None
        self.name = "figure"
        self.canvas.mpl_connect("motion_notify_event", self._on_move)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        self.placeholder(hint or tr("입력을 확인하고 실행하면 결과 그래프가 여기에 표시됩니다.",
                                    "Run the calculation to see the plot here."))

    # -- drawing ---------------------------------------------------------------
    def placeholder(self, text: str):
        self._draw = None
        self._csv = None
        self.figure.clear()
        t = S.theme()
        self.figure.set_facecolor(t["bg"])
        self.figure.text(0.5, 0.5, text, ha="center", va="center", color=t["muted"], fontsize=10, wrap=True)
        self.canvas.draw_idle()
        self.csv_button.setEnabled(False)

    def draw(self, fn, *args, name: str = "figure", csv=None, hover=None, **kwargs):
        self._draw = (fn, args, kwargs)
        self._csv = csv
        self._hover = hover
        self.name = name
        self.redraw()

    def redraw(self):
        if self._draw is None:
            return
        fn, args, kwargs = self._draw
        self.figure.set_facecolor(S.theme()["bg"])
        try:
            fn(self.figure, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a plotting failure must not crash the app
            self.figure.clear()
            self.figure.text(0.5, 0.5, f"plot error: {exc}", ha="center", va="center", color="#cf222e")
        self.canvas.draw_idle()
        self.csv_button.setEnabled(self._csv is not None)

    # -- interaction -----------------------------------------------------------
    def _on_move(self, ev):
        if self._hover is None or ev.inaxes is None or ev.xdata is None:
            return
        try:
            self.readout.setText(self._hover(ev.xdata, ev.ydata, ev.inaxes) or "")
        except Exception:  # noqa: BLE001 - readout is best effort
            self.readout.setText("")

    def _on_click(self, ev):
        if ev.inaxes is None or ev.xdata is None or self.toolbar.mode:
            return
        if ev.button == 1:
            self.clicked.emit(float(ev.xdata), float(ev.ydata))

    # -- export ------------------------------------------------------------------
    def export(self, kind: str):
        if self._draw is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("그림 저장", "Save figure"), f"{self.name}.{kind}",
                                              f"{kind.upper()} (*.{kind})")
        if path:
            self.figure.savefig(path, dpi=200 if kind == "png" else None)

    def export_csv(self):
        if self._csv is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("데이터 저장 (CSV)", "Save data (CSV)"), f"{self.name}.csv",
                                              "CSV (*.csv)")
        if path:
            write_csv(path, self._csv())


def write_csv(path, data: dict) -> Path:
    cols = {k: np.atleast_1d(np.asarray(v)) for k, v in data.items()}
    n = max(len(v) for v in cols.values())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(list(cols))
        for i in range(n):
            w.writerow([_cell(v[i]) if i < len(v) else "" for v in cols.values()])
    return Path(path)


def _cell(x):
    if isinstance(x, (float, np.floating)):
        return "" if not np.isfinite(x) else repr(float(x))
    return x


# ---------------------------------------------------------------------------
# decision display
# ---------------------------------------------------------------------------

class VerdictBanner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Verdict")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.big = QLabel("—")
        f = QFont(theme.app_font())
        f.setPointSizeF(20)
        f.setBold(True)
        self.big.setFont(f)
        self.big.setMinimumWidth(130)
        self.text = QLabel(tr("요구를 입력하고 [판정 실행]을 누르세요.", "Enter a requirement and press Evaluate."))
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.big)
        lay.addWidget(self.text, 1)
        self.set("NONE", "")

    def restyle(self):
        self.set(*self._last)

    def set(self, verdict: str, html: str):
        self._last = (verdict, html or self.text.text())
        bg, fg = theme.verdict_colors(verdict)
        self.setStyleSheet(f"QFrame#Verdict {{ background: {bg}; border: 1px solid {fg}; border-radius: 8px; }}"
                           f"QLabel {{ background: transparent; }}")
        self.big.setStyleSheet(f"color: {fg};")
        self.text.setStyleSheet(f"color: {theme.colors()['fg']};")
        self.big.setText({"NONE": "—"}.get(verdict, verdict))
        if html:
            self.text.setText(html)


class ClaimTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels([tr("판정 항목", "claim"), tr("상태", "status"), tr("사유", "reasons"), tr("설명", "detail")])
        self.setAlternatingRowColors(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideNone)
        self.setUniformRowHeights(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.header().setStretchLastSection(True)

    def set_claims(self, groups: list[tuple[str, list[dict]]]):
        self.clear()
        for title, claims in groups:
            top = QTreeWidgetItem([title, "", "", ""])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.addTopLevelItem(top)
            for c in claims:
                it = QTreeWidgetItem([c["name"], c["status"], ", ".join(c.get("reasons") or []), c.get("detail", "")])
                it.setForeground(1, QColor(theme.status_color(c["status"])))
                fb = it.font(1)
                fb.setBold(True)
                it.setFont(1, fb)
                it.setToolTip(3, c.get("detail", ""))
                for q in c.get("qualifiers") or []:
                    QTreeWidgetItem(it, [tr("한정", "qualifier"), "", "", q])
                for ev in c.get("evidence") or []:
                    e = QTreeWidgetItem(it, [tr("근거", "evidence"), "", ev.get("kind", ""), ev.get("summary", "")])
                    e.setToolTip(3, ev.get("summary", ""))
                if c.get("scope"):
                    QTreeWidgetItem(it, [tr("범위", "scope"), "", "", c["scope"]])
                top.addChild(it)
            top.setExpanded(True)


class KeyValueTable(QTableWidget):
    def __init__(self, parent=None, headers=None):
        super().__init__(parent)
        headers = headers or [tr("항목", "item"), tr("값", "value")]
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)

    def set_rows(self, rows: list, colors: dict | None = None):
        self.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                it = QTableWidgetItem(v if isinstance(v, str) else fmt(v))
                it.setToolTip(it.text())
                if colors and (i, j) in colors:
                    it.setForeground(QColor(colors[(i, j)]))
                self.setItem(i, j, it)
        self.resizeRowsToContents()


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def number(value: float, lo: float, hi: float, suffix: str = "", decimals: int = 2, step: float | None = None,
           tip: str = "") -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(decimals)
    w.setValue(value)
    w.setKeyboardTracking(False)
    w.setAccelerated(True)
    if suffix:
        w.setSuffix(f" {suffix}")
    if step:
        w.setSingleStep(step)
    if tip:
        w.setToolTip(tip)
    w.setMinimumWidth(120)
    return w


def integer(value: int, lo: int, hi: int, suffix: str = "", tip: str = "") -> QSpinBox:
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setValue(value)
    if suffix:
        w.setSuffix(f" {suffix}")
    if tip:
        w.setToolTip(tip)
    return w


def combo(items: list[tuple[str, object]], current=None) -> QComboBox:
    w = QComboBox()
    for label, data in items:
        w.addItem(label, data)
    if current is not None:
        for i in range(w.count()):
            if w.itemData(i) == current:
                w.setCurrentIndex(i)
    return w


def check(label: str, value: bool = False, tip: str = "") -> QCheckBox:
    w = QCheckBox(label)
    w.setChecked(value)
    if tip:
        w.setToolTip(tip)
    return w


def primary_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("Primary")
    b.setMinimumHeight(34)
    return b


def hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Hint")
    lab.setWordWrap(True)
    return lab


def error_box(parent, title: str, msg: str, detail: str = ""):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None and app.property("twb_selftest"):
        errors = app.property("twb_errors") or []
        app.setProperty("twb_errors", list(errors) + [f"{title}: {msg}"])
        return
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(msg)
    if detail:
        box.setDetailedText(detail)
    box.exec()
