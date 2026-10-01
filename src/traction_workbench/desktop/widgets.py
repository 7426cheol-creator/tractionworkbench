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
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..i18n import tr
from ..plots import style as S
from . import theme
from .cursor import DataCursor


def fmt(v, digits: int = 6) -> str:
    if v is None:
        return "—"
    if isinstance(v, (bool, np.bool_)):
        return tr("예", "yes") if v else tr("아니오", "no")
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
    if isinstance(v, dict):                           # "k: v · k: v" (a nested mapping in parentheses), never a repr
        return " · ".join(f"{k}: " + (f"({fmt(x, digits)})" if isinstance(x, dict) else fmt(x, digits))
                          for k, x in v.items())
    return str(v)


class Cell(str):
    """Table text with its own tooltip: a display name keeps the record's code one hover away."""

    def __new__(cls, text: str, tip: str = ""):
        s = super().__new__(cls, text)
        s.tip = tip
        return s


def claim_cell(name: str) -> Cell:
    """A claim's display name for a table; the claim code (as in the JSON record) is its tooltip."""
    from ..plots.labels import claim_label
    return Cell(claim_label(name), name)


# ---------------------------------------------------------------------------
# plotting
# ---------------------------------------------------------------------------

class PlotPanel(QWidget):
    """Matplotlib canvas + navigation toolbar + PNG/SVG/PDF/CSV export + readout line + data cursor (``cursor``: on
    at the start; a panel whose clicks and hover mean something else - a map - starts with it off)."""

    clicked = Signal(float, float)

    def __init__(self, parent=None, hint: str | None = None, min_height: int = 360, cursor: bool = True):
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
        self.readout.setWordWrap(True)                  # three lines (cursor values, pinned cursors) that never
        self.readout.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)     # widen the window or move the canvas
        self.readout.setFixedHeight(3 * self.readout.fontMetrics().lineSpacing() + 6)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.toolbar)
        top.addStretch(1)
        self.signal_box = QComboBox()
        self.signal_box.setToolTip(tr("데이터 커서가 따라갈 신호 (범례 항목을 클릭해도 고릅니다)",
                                      "the signal the data cursor follows (or click its legend entry)"))
        self.signal_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.signal_box.setMinimumContentsLength(14)
        self.signal_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)     # the room the row has, up to
        self.signal_box.setMaximumWidth(330)                   # a long signal name (not cut to a few letters)
        self.signal_box.setFixedHeight(26)
        self.signal_box.currentIndexChanged.connect(self._signal_chosen)
        top.addWidget(self.signal_box, 2)
        self.cursor_btn = QPushButton(tr("데이터 커서", "Data cursor"))
        self.cursor_btn.setCheckable(True)
        self.cursor_btn.setFixedHeight(26)
        self.cursor_btn.setToolTip(tr(
            "범례 항목(또는 목록)에서 신호를 고르면 커서가 그 곡선을 샘플 단위로 따라가며 값을 표시합니다 · 신호를 고르지 "
            "않으면 마우스에 가장 가까운 곡선 · 좌클릭: 커서 고정(두 개 → Δx·Δy) · ←/→: 고정 커서를 한 샘플씩 "
            "(Shift: 10) · 우클릭/Esc: 고정 커서 지우기",
            "Pick a signal in the legend (or the list) and the cursor follows that curve sample by sample with its "
            "value · without a pick it follows the curve nearest the mouse · left click: pin a cursor (two → Δx, Δy) "
            "· ←/→: move the last pin one sample (Shift: 10) · right click / Esc: remove the pins"))
        self.cursor_btn.toggled.connect(self.set_cursor)
        top.addWidget(self.cursor_btn)
        self.fig_buttons = []
        for label, fn in (("PNG", lambda: self.export("png")), ("SVG", lambda: self.export("svg")),
                          ("PDF", lambda: self.export("pdf")), ("CSV", self.export_csv)):
            b = QPushButton(label)
            b.setToolTip(tr(f"{label}로 내보내기", f"export as {label}"))
            b.clicked.connect(fn)
            b.setFixedHeight(26)
            top.addWidget(b)
            if label == "CSV":
                self.csv_button = b
            else:
                self.fig_buttons.append(b)
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
        self.canvas.setFocusPolicy(Qt.StrongFocus)                        # the cursor's arrow keys
        self.cursor = DataCursor(self.canvas, self.readout.setText, on_signals=self._fill_signals,
                                 on_track=self._show_track)
        self.canvas.mpl_connect("motion_notify_event", self._on_move)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        self.canvas.mpl_connect("resize_event", self._fit_texts)       # titles and legends follow the canvas size
        self.placeholder(hint or tr("입력을 확인하고 실행하면 결과 그래프가 여기에 표시됩니다.",
                                    "Run the calculation to see the plot here."))
        self.cursor_btn.setChecked(cursor)
        self.set_cursor(cursor)

    # -- drawing ---------------------------------------------------------------
    def placeholder(self, text: str):
        self._draw = None
        self._pending = False
        self._csv = None
        self.figure.clear()
        self.cursor.clear()
        t = S.theme()
        self.figure.set_facecolor(t["bg"])
        self.figure.text(0.5, 0.5, text, ha="center", va="center", color=t["muted"], fontsize=10, wrap=True)
        if self.isVisible():
            self.canvas.draw_idle()
        else:                                # a hidden tab draws its note when it is first shown (like a figure):
            self._stale = True               # text layout is not free, least of all while a calculation runs
        self.csv_button.setEnabled(False)
        for b in self.fig_buttons:                      # nothing to export yet
            b.setEnabled(False)

    def draw(self, fn, *args, name: str = "figure", csv=None, hover=None, **kwargs):
        self._draw = (fn, args, kwargs)
        self._csv = csv
        self._hover = hover
        self.name = name
        self.redraw()

    def redraw(self):
        if self._draw is None:
            return
        self.csv_button.setEnabled(self._csv is not None)
        for b in self.fig_buttons:
            b.setEnabled(True)
        if not self.isVisible():             # a hidden tab draws when it is first shown: a result on one tab does
            self._pending = True             # not freeze the window drawing every other tab
            return
        self._render()

    def _render(self):
        self._pending = False
        fn, args, kwargs = self._draw
        self.figure.set_facecolor(S.theme()["bg"])
        try:
            fn(self.figure, *args, **kwargs)
            self.cursor.rebuild()                       # the new figure's curves (the chosen signal is kept)
        except Exception as exc:  # noqa: BLE001 - a plotting failure must not crash the app
            self.figure.clear()
            self.cursor.clear()
            self.figure.text(0.5, 0.5, f"plot error: {exc}", ha="center", va="center", color="#cf222e")
        self._fit_texts()
        self.canvas.draw_idle()

    def _fit_texts(self, _ev=None):
        if self._draw is not None and not getattr(self, "_pending", False):
            from ..plots.figures import fit_texts
            fit_texts(self.figure)

    def showEvent(self, ev):
        super().showEvent(ev)
        if getattr(self, "_pending", False) and self._draw is not None:
            self._render()
        elif getattr(self, "_stale", False):
            self.canvas.draw_idle()
        self._stale = False

    # -- interaction -----------------------------------------------------------
    def set_cursor(self, on: bool):
        """Data cursor on / off (off: the page's own hover readout and clicks, as before)."""
        self.cursor.set_enabled(on)
        self.signal_box.setVisible(bool(on))
        if self.cursor_btn.isChecked() != bool(on):
            self.cursor_btn.setChecked(bool(on))

    def _fill_signals(self, names, track):
        self.signal_box.blockSignals(True)
        self.signal_box.clear()
        self.signal_box.addItem(tr("가까운 곡선", "nearest curve"))
        for n in names:
            self.signal_box.addItem(n)
        self.signal_box.setCurrentIndex(0 if track is None else track + 1)
        self.signal_box.setEnabled(bool(names))
        self.signal_box.blockSignals(False)

    def _show_track(self, track):
        self.signal_box.blockSignals(True)
        self.signal_box.setCurrentIndex(0 if track is None else track + 1)
        self.signal_box.blockSignals(False)

    def _signal_chosen(self, idx: int):
        self.cursor.select(None if idx <= 0 else idx - 1)

    def _on_move(self, ev):
        if self.toolbar.mode:                           # zoom / pan own the mouse
            return
        if self.cursor.enabled and self.cursor.signals:
            self.cursor.move(ev)
            return
        if self._hover is None or ev.inaxes is None or ev.xdata is None:
            return
        try:
            self.readout.setText(self._hover(ev.xdata, ev.ydata, ev.inaxes) or "")
        except Exception:  # noqa: BLE001 - readout is best effort
            self.readout.setText("")

    def _on_click(self, ev):
        if self.toolbar.mode:
            return
        if self.cursor.press(ev):
            self.canvas.setFocus()
            return
        if ev.inaxes is None or ev.xdata is None:
            return
        if ev.button == 1:
            self.clicked.emit(float(ev.xdata), float(ev.ydata))

    # -- export ------------------------------------------------------------------
    def export(self, kind: str):
        if self._draw is None:
            return
        if getattr(self, "_pending", False):          # never drawn yet (its tab was not opened): draw it now
            self._render()
        path, _ = QFileDialog.getSaveFileName(self, tr("그림 저장", "Save figure"), f"{self.name}.{kind}",
                                              f"{kind.upper()} (*.{kind})")
        if path:
            try:
                self.figure.savefig(path, dpi=200 if kind == "png" else None)   # the pinned cursors are exported
            except Exception as exc:  # noqa: BLE001 - e.g. a locked file or a folder without write access
                error_box(self, tr("그림 저장 실패", "could not save the figure"), str(exc))
            self.canvas.draw_idle()

    def export_csv(self):
        if self._csv is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("데이터 저장 (CSV)", "Save data (CSV)"), f"{self.name}.csv",
                                              "CSV (*.csv)")
        if path:
            try:
                write_csv(path, self._csv())
            except Exception as exc:  # noqa: BLE001
                error_box(self, tr("데이터 저장 실패", "could not save the data"), str(exc))


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
    """The verdict, its one-line conclusion and reasons; the requirement text, scope and record identity fold away
    under [details] (they stay one click away and in every saved record)."""

    details_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QToolButton
        self.setObjectName("Verdict")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        self.big = QLabel("—")
        f = QFont(theme.app_font())
        f.setPointSizeF(20)
        f.setBold(True)
        self.big.setFont(f)
        self.big.setMinimumWidth(130)
        self.text = QLabel(tr("요구를 입력하고 [판정 실행]을 누르세요.", "Enter a requirement and press Evaluate."))
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.more = QLabel("")
        self.more.setWordWrap(True)
        self.more.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.more.hide()
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addWidget(self.text)
        col.addWidget(self.more)
        self.more_btn = QToolButton()
        self.more_btn.setAutoRaise(True)
        self.more_btn.setCheckable(True)
        self.more_btn.toggled.connect(self._toggled)
        self.more_btn.hide()
        lay.addWidget(self.big)
        lay.addLayout(col, 1)
        lay.addWidget(self.more_btn, 0, Qt.AlignTop)
        self._toggled(False)
        self.set("NONE", "")

    def _toggled(self, on: bool):
        self.more_btn.setText(tr("접기 ▴", "less ▴") if on else tr("자세히 ▾", "details ▾"))
        self.more_btn.setToolTip(tr("요구 원문, 한정, 사유 코드, 범위, 기록 ID와 입력 해시", "requirement text, qualifiers, "
                                    "reason codes, scope, record id and input hash"))
        self.more.setVisible(on and bool(self.more.text()))
        self.details_toggled.emit(on)

    def set_details_shown(self, on: bool):
        self.more_btn.setChecked(bool(on))

    def restyle(self):
        self.set(*self._last)

    def set(self, verdict: str, html: str, details: str = ""):
        self._last = (verdict, html or self.text.text(), details)
        bg, fg = theme.verdict_colors(verdict)
        self.setStyleSheet(f"QFrame#Verdict {{ background: {bg}; border: 1px solid {fg}; border-radius: 8px; }}"
                           f"QLabel {{ background: transparent; }}")
        self.big.setStyleSheet(f"color: {fg};")
        self.text.setStyleSheet(f"color: {theme.colors()['fg']};")
        self.more.setStyleSheet(f"color: {theme.colors()['fg']};")
        self.big.setText({"NONE": "—"}.get(verdict, verdict))
        self.more.setText(details)
        self.more_btn.setVisible(bool(details))
        self.more.setVisible(bool(details) and self.more_btn.isChecked())
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
                from ..plots.labels import claim_label, reason_label, state_label
                it = QTreeWidgetItem([claim_label(c["name"]), state_label(c["status"]),
                                      ", ".join(reason_label(r) for r in c.get("reasons") or []), c.get("detail", "")])
                it.setToolTip(0, c["name"])
                it.setToolTip(1, c["status"])
                it.setToolTip(2, ", ".join(c.get("reasons") or []))
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
    """Read-only rows of (item, value, ...).  ``fit_rows``: the table is as tall as its rows (in a scrolling column
    every row stays readable, whatever the window size)."""

    def __init__(self, parent=None, headers=None, fit_rows: bool = False):
        super().__init__(parent)
        self._fit_rows = fit_rows
        headers = headers or [tr("항목", "item"), tr("값", "value")]
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        for i in range(len(headers) - 1):              # every column but the last fits its content (no cut headers)
            self.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)

    def set_rows(self, rows: list, colors: dict | None = None):
        self.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                it = QTableWidgetItem(v if isinstance(v, str) else fmt(v))
                it.setToolTip(getattr(v, "tip", "") or it.text())
                if colors and (i, j) in colors:
                    it.setForeground(QColor(colors[(i, j)]))
                self.setItem(i, j, it)
        self.resizeRowsToContents()
        self._fit_height()

    def _fit_height(self):
        if not self._fit_rows:
            return
        hh = self.horizontalHeader()                 # short columns wrap past 28 % of the width: the last column (the
        cap = max(90, int(self.viewport().width() * 0.28))           # explanation) keeps the room it needs
        for j in range(self.columnCount() - 1):
            hh.setSectionResizeMode(j, QHeaderView.Interactive)
            self.resizeColumnToContents(j)
            if hh.sectionSize(j) > cap:
                hh.resizeSection(j, cap)
        self.resizeRowsToContents()
        head = self.horizontalHeader().sizeHint().height()
        rows = sum(self.rowHeight(r) for r in range(self.rowCount()))
        bar = self.horizontalScrollBar().sizeHint().height() if self.horizontalHeader().length() > self.viewport().width() \
            else 0                                   # columns wider than the table: keep the last row above the bar
        self.setFixedHeight(head + max(rows, 24) + bar + 2 * self.frameWidth() + 2)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._fit_rows and ev.size().width() != ev.oldSize().width():   # wrapped rows re-flow with the width
            self._fit_height()


def parse_clipboard_grid(text: str) -> list[list[str]]:
    """Spreadsheet/datasheet text -> cells (tab-separated from Excel, else ';', else whitespace)."""
    rows = []
    for line in text.replace("\r", "").split("\n"):
        if not line.strip():
            continue
        if "\t" in line:
            cells = line.split("\t")
        elif ";" in line:
            cells = line.split(";")
        else:
            cells = line.replace(",", " ").split()
        rows.append([c.strip() for c in cells])
    return rows


class NumTable(QTableWidget):
    """Editable numeric table: add/remove rows, Ctrl+V pastes a block from a spreadsheet at the current cell."""

    def __init__(self, headers: list[str], rows=None, parent=None, min_height: int = 120, text_cols=(),
                 optional_cols=()):
        super().__init__(parent)
        self.text_cols = frozenset(text_cols)            # returned as stripped text (names, kinds)
        self.optional_cols = frozenset(optional_cols)    # a blank numeric cell here is None (not declared), not 0
        self.fit_columns = True                          # False: the page sizes the columns itself
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(True)
        self.verticalHeader().setDefaultSectionSize(22)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setMinimumHeight(min_height)
        if rows:
            self.load(rows)

    def _fit(self) -> None:
        """No header or value is cut: equal columns while each fits its share of the width, otherwise every column as
        wide as its header and values (spare width shared out) and a table narrower than that scrolls sideways (a cut
        header hides which quantity a column holds)."""
        cols = [j for j in range(self.columnCount()) if not self.isColumnHidden(j)]
        if not self.fit_columns or not cols:
            return
        h = self.horizontalHeader()
        need = {j: max(h.sectionSizeHint(j), self.sizeHintForColumn(j)) for j in cols}
        sb = self.verticalScrollBar()
        room = self.viewport().width() - (0 if sb.isVisible() else sb.sizeHint().width())   # rows may still be added
        if max(need.values()) * len(cols) <= room:
            if h.sectionResizeMode(cols[0]) != QHeaderView.Stretch:
                h.setSectionResizeMode(QHeaderView.Stretch)
            return
        h.setSectionResizeMode(QHeaderView.Interactive)
        spare = max(0, room - sum(need.values())) // len(cols)
        for j, w in need.items():
            h.resizeSection(j, w + spare)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()

    def load(self, rows) -> None:
        self.setRowCount(0)
        for r in rows:
            self._append(r)
        self._fit()

    def add_row(self, values=None) -> None:
        self._append(values)
        self._fit()

    def _append(self, values=None) -> None:
        i = self.rowCount()
        self.insertRow(i)
        for j in range(self.columnCount()):
            v = None if values is None or j >= len(values) else values[j]
            self.setItem(i, j, QTableWidgetItem("" if v is None else (v if isinstance(v, str) else fmt(v))))

    def remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.selectedIndexes()} or ({self.currentRow()} if self.currentRow() >= 0 else set()),
                      reverse=True)
        for r in rows:
            self.removeRow(r)

    def values(self) -> list[list[float]]:
        """Numeric rows (blank rows skipped); a non-numeric or partly filled row raises ValueError naming the cell.

        Text columns come back as text; a blank optional column comes back as None (not declared)."""
        out = []
        for i in range(self.rowCount()):
            cells = [(self.item(i, j).text().strip() if self.item(i, j) else "") for j in range(self.columnCount())]
            if not any(cells):
                continue
            row = []
            for j, c in enumerate(cells):
                if j in self.text_cols:
                    row.append(c)
                    continue
                if c == "" and j in self.optional_cols:
                    row.append(None)
                    continue
                try:
                    if "," in c:        # '1,5' (decimal comma) vs '1,000' (thousands) is ambiguous: never guessed
                        raise ValueError
                    v = float(c)
                except ValueError:
                    raise ValueError(tr(f"{i + 1}행 '{self.horizontalHeaderItem(j).text()}' 값이 숫자가 아닙니다: {c!r}",
                                        f"row {i + 1}, column '{self.horizontalHeaderItem(j).text()}' is not a number: "
                                        f"{c!r}")) from None
                if not math.isfinite(v):
                    raise ValueError(tr(f"{i + 1}행: 유한한 값이 필요합니다", f"row {i + 1}: finite value required"))
                row.append(v)
            out.append(row)
        return out

    def paste(self) -> None:
        from PySide6.QtWidgets import QApplication
        grid = parse_clipboard_grid(QApplication.clipboard().text())
        if not grid:
            return
        r0, c0 = max(self.currentRow(), 0), max(self.currentColumn(), 0)
        while self.rowCount() < r0 + len(grid):
            self._append()
        for di, row in enumerate(grid):
            for dj, cell in enumerate(row):
                if c0 + dj < self.columnCount():
                    self.setItem(r0 + di, c0 + dj, QTableWidgetItem(cell))
        self._fit()

    def keyPressEvent(self, ev):
        from PySide6.QtGui import QKeySequence
        if ev.matches(QKeySequence.Paste):
            self.paste()
            return
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.state() != QAbstractItemView.EditingState:
            for it in self.selectedItems():
                it.setText("")
            return
        super().keyPressEvent(ev)


def table_with_buttons(table: NumTable, note: str = "") -> QWidget:
    """A NumTable with add / remove / paste buttons underneath."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(3)
    lay.addWidget(table)
    row = QHBoxLayout()
    for text, fn in ((tr("+ 행", "+ row"), lambda: table.add_row()), (tr("− 선택 행", "− selected"), table.remove_selected),
                     (tr("붙여넣기", "paste"), table.paste)):
        b = QPushButton(text)
        b.setMinimumHeight(24)
        b.clicked.connect(fn)
        row.addWidget(b)
    row.addStretch(1)
    lay.addLayout(row)
    if note:
        lay.addWidget(hint(note))
    return w


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def number(value: float, lo: float, hi: float, suffix: str = "", decimals: int = 2, step: float | None = None,
           tip: str = "", special: str | None = None) -> QDoubleSpinBox:
    """``special``: the text shown at the minimum when the minimum means "not declared" / "none" (never "0 V")."""
    w = QDoubleSpinBox()
    if special:
        w.setSpecialValueText(special)
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


def tidy_inputs(root: QWidget) -> None:
    """Let every combo box shrink with its form column (a long item used to force the whole input panel wider than
    its column, so fields were cut off behind a horizontal scroll bar); the popup still shows full items and the
    tooltip the full current text."""
    for cb in root.findChildren(QComboBox):
        cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(8)
        view = cb.view()
        view.setTextElideMode(Qt.ElideNone)
        view.setMinimumWidth(view.sizeHintForColumn(0) + 28)
        if not cb.toolTip():
            cb.setToolTip(cb.currentText())
            cb.currentTextChanged.connect(cb.setToolTip)
    # a check box added as addRow("", box) sat in the field column next to the widest label: give it the full row
    for form in root.findChildren(QFormLayout):
        for row in range(form.rowCount() - 1, -1, -1):
            li = form.itemAt(row, QFormLayout.LabelRole)
            fi = form.itemAt(row, QFormLayout.FieldRole)
            lab = li.widget() if li is not None else None
            box = fi.widget() if fi is not None else None
            if isinstance(box, QCheckBox) and (lab is None or (isinstance(lab, QLabel) and not lab.text().strip())):
                form.takeRow(row)
                if lab is not None:
                    lab.deleteLater()
                form.insertRow(row, box)
    # an input panel is never squeezed below its content (the splitter takes the width from the results side)
    for sc in root.findChildren(QScrollArea):
        w = sc.widget()
        if w is None or not sc.widgetResizable():
            continue
        need = w.minimumSizeHint().width() + sc.verticalScrollBar().sizeHint().width() + 2 * sc.frameWidth() + 2
        if need > sc.minimumWidth():
            sc.setMinimumWidth(need)


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


class ElidedLabel(QLabel):
    """One line of plain text cut with an ellipsis to the width it gets; the full text is the tooltip."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._full = text
        self.setToolTip(text)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, ev):  # noqa: N802 - Qt override
        super().resizeEvent(ev)
        self._elide()

    def _elide(self) -> None:
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(0, self.width() - 2)))


class MagnetTempInput(QWidget):
    """Optional magnet temperature of ONE operating condition (unchecked = not stated).

    A flux map with several temperature planes has no single operating point without it: ``sync(drive)`` pre-sets
    the first plane temperature whenever the drive's plane set changes (visible and editable, like the default speed
    and Vdc) and ``note`` lists the planes; leaving such a model resets it to "not stated"; otherwise it stays as the
    user set it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.on = QCheckBox()
        self.on.setToolTip(tr("체크하면 이 자석 온도에서 계산합니다 (체크 해제 = 미지정)",
                              "checked: computed at this magnet temperature (unchecked = not stated)"))
        self.value = number(20.0, -60.0, 250.0, "°C", 1, 5)
        self.value.setEnabled(False)
        self.on.toggled.connect(self.value.setEnabled)
        h.addWidget(self.on)
        h.addWidget(self.value, 1)
        self.note = hint("")
        self.note.hide()
        self.planes: list = []
        self._synced = None

    def get(self) -> float | None:
        return float(self.value.value()) if self.on.isChecked() else None

    def sync(self, drive) -> None:
        from ..viz.sweeps import plane_temperatures
        planes = plane_temperatures(drive)
        if planes == self._synced:
            return
        prev, self._synced, self.planes = self._synced, planes, planes
        if not planes and prev:
            self.on.setChecked(False)          # a temperature picked for another model's planes is not carried over
        if planes:
            self.on.setChecked(True)
            self.value.setValue(planes[0])
            txt = ", ".join(f"{t:g}" for t in planes)
            self.note.setText(tr(f"이 flux map의 자석 온도 plane: {txt} °C — 한 운전점은 한 온도에서 계산합니다(판정 페이지는 "
                                 f"온도를 말하지 않은 요구를 모든 plane에서, 성능 곡선은 plane마다 봅니다)",
                                 f"magnet-temperature planes of this flux map: {txt} degC - one operating point is "
                                 f"computed at one temperature (the decision page judges an unstated temperature at "
                                 f"every plane, the envelope page draws one curve per plane)"))
            self.note.show()
        else:
            self.note.hide()

    def missing(self, parent) -> bool:
        """True (and says why) when the model needs a magnet temperature for one operating point and none is set."""
        if self.planes and self.get() is None:
            txt = ", ".join(f"{t:g}" for t in self.planes)
            error_box(parent, tr("자석 온도 필요", "magnet temperature needed"),
                      tr(f"이 flux map은 자석 온도 plane이 여러 개입니다 ({txt} °C). 한 운전점은 한 온도에서만 정의되므로 자석 "
                         f"온도를 지정하세요.", f"this flux map has several magnet-temperature planes ({txt} degC); one "
                                              f"operating point is defined at one temperature - state the magnet "
                                              f"temperature."))
            return True
        return False


class InsightPanel(QWidget):
    """The engineering reading of a result (``insight.Insight``): the conclusion, key numbers, the judged items,
    the limiting mechanism and what would change the answer — every number from the result it reads.

    A page with several calculations keeps one reading per calculation (``set_reading``); a selector above the text
    switches between them and the latest one is shown."""

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QComboBox, QTextBrowser
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setObjectName("Insight")
        self.pick = QComboBox()
        self.pick.setVisible(False)
        self.pick.currentIndexChanged.connect(self._picked)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.pick)
        lay.addWidget(self.view)
        self.insight = None
        self.readings: dict = {}             # key -> (title, Insight)
        self._placeholder = placeholder or tr("계산하면 결과의 엔지니어링 해석(결론·항목별 분석·한계 원인·다음 단계)이 여기에 "
                                              "표시됩니다.", "Run the calculation to read the result here (conclusion, "
                                                             "item by item, what limits, next steps).")
        self.redraw()

    def show_insight(self, insight) -> None:
        self.insight = insight
        self.redraw()

    def set_reading(self, key: str, title: str, insight) -> None:
        """Keep the reading of one calculation and show it (the selector lists every calculation read so far)."""
        self.readings[key] = (title, insight)
        self.pick.blockSignals(True)
        i = self.pick.findData(key)
        if i < 0:
            self.pick.addItem(title, key)
            i = self.pick.count() - 1
        else:
            self.pick.setItemText(i, title)
        self.pick.setCurrentIndex(i)
        self.pick.blockSignals(False)
        self.pick.setVisible(self.pick.count() > 1)
        self.show_insight(insight)

    def read(self, key: str, title: str, fn, *args) -> None:
        """``fn(*args)`` -> Insight into this panel; a reading that fails is reported in the panel, never raised
        (the page and its result stay usable)."""
        try:
            ins = fn(*args)
        except Exception as exc:  # noqa: BLE001
            from ..insight import Insight
            ins = Insight(headline=tr(f"해석을 만들지 못했습니다: {type(exc).__name__}: {exc}",
                                      f"the reading could not be made: {type(exc).__name__}: {exc}"))
            ins.failed = True
        self.set_reading(key, title, ins)

    def _picked(self, i: int) -> None:
        key = self.pick.itemData(i)
        if key in self.readings:
            self.show_insight(self.readings[key][1])

    def redraw(self) -> None:
        c = theme.colors()
        if self.insight is None:
            self.view.setHtml(f"<p style='color:{c['muted']}'>{self._placeholder}</p>")
            return
        cols = {"fg": c["fg"], "muted": c["muted"], "border": c["border"], "panel": c["panel"],
                "ok": theme.verdict_colors("PASS")[1], "bad": theme.verdict_colors("FAIL")[1],
                "warn": theme.verdict_colors("UNKNOWN")[1], "open": theme.verdict_colors("UNKNOWN")[1],
                "info": c["muted"]}
        bar = self.view.verticalScrollBar().value()
        self.view.setHtml(self.insight.html(cols))
        self.view.verticalScrollBar().setValue(bar)

    def text(self) -> str:
        return self.view.toPlainText()


def reading_tab(tabs, placeholder: str = "", index: int = 0) -> InsightPanel:
    """An 'engineering reading' tab in a page's result tabs (first by default, and shown)."""
    panel = InsightPanel(placeholder)
    tabs.insertTab(index, panel, tr("엔지니어링 분석", "engineering reading"))
    tabs.setCurrentIndex(index)
    return panel


def with_reading(widget, placeholder: str = "") -> tuple:
    """A result widget without tabs wrapped with an 'engineering reading' tab in front: (tab widget, panel)."""
    from PySide6.QtWidgets import QTabWidget
    tabs = QTabWidget()
    tabs.addTab(widget, tr("그래프·표", "plots · table"))
    return tabs, reading_tab(tabs, placeholder)


class ConceptNote(QWidget):
    """Collapsible explanation for newcomers (the expert content stays unchanged)."""

    def __init__(self, html: str, title: str | None = None, expanded: bool = False, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QToolButton
        self.button = QToolButton()
        self.button.setText("ⓘ " + (title or tr("개념 설명", "concept")))
        self.button.setCheckable(True)
        self.button.setChecked(expanded)
        self.button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.button.setStyleSheet("QToolButton { border: none; font-weight: 600; padding: 2px 0px; }")
        self.body = QLabel(html)
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setObjectName("Card")
        self.body.setStyleSheet("padding: 8px; font-size: 8.8pt;")
        self.body.setVisible(expanded)
        self.button.toggled.connect(self.body.setVisible)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(2)
        lay.addWidget(self.button)
        lay.addWidget(self.body)


def confirm(parent, title: str, text: str) -> bool:
    """Yes/no question; the headless self-test answers yes and records the question."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None and app.property("twb_selftest"):
        app.setProperty("twb_questions", list(app.property("twb_questions") or []) + [f"{title}: {text}"])
        return True
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes


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
