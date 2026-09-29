"""What a shown result was computed from: snapshots of a page's input widgets and the difference to the current ones.

A page registers the widgets that hold the inputs of a task (``MainWindow.track_inputs``); the window takes a snapshot
when the task starts and keeps it with the shown result.  Any later edit (typing, a preset, a table row) makes the
current snapshot differ, and the page says that the result on screen was computed from earlier inputs, naming the
changed fields.  Only widgets that can carry an input are read: spin boxes, line edits, combo boxes, check and radio
boxes, editable text and tables.  A disabled widget contributes nothing (its value does not reach the calculation);
a widget marked ``setProperty("twb_not_input", True)`` (a view selector inside an input panel) is skipped.
"""

from __future__ import annotations

import re

from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel,
                               QLayout, QLineEdit, QPlainTextEdit, QRadioButton, QSpinBox, QTableWidget, QTextEdit,
                               QWidget)

from ..i18n import tr


def input_widgets(root: QWidget) -> list[QWidget]:
    out = []
    for w in [root] + root.findChildren(QWidget):
        if w.property("twb_not_input"):
            continue
        if isinstance(w, QLineEdit):
            if isinstance(w.parent(), (QAbstractSpinBox, QComboBox)) or w.isReadOnly():
                continue
            out.append(w)
        elif isinstance(w, (QAbstractSpinBox, QComboBox, QCheckBox, QRadioButton, QTableWidget)):
            out.append(w)
        elif isinstance(w, (QPlainTextEdit, QTextEdit)) and not w.isReadOnly():
            out.append(w)
    return out


def _value(w: QWidget) -> tuple:
    """(comparable value, text for a message)."""
    if not w.isEnabled():
        return None, tr("(해당 없음)", "(not used)")
    if isinstance(w, QDoubleSpinBox):
        return round(float(w.value()), 12), w.text().strip()
    if isinstance(w, QSpinBox):
        return int(w.value()), w.text().strip()
    if isinstance(w, QAbstractSpinBox):
        return w.text(), w.text().strip()
    if isinstance(w, QComboBox):
        return (w.currentIndex(), w.currentText()), w.currentText()
    if isinstance(w, (QCheckBox, QRadioButton)):
        return w.isChecked(), tr("선택", "on") if w.isChecked() else tr("해제", "off")
    if isinstance(w, QLineEdit):
        return w.text(), w.text()
    if isinstance(w, (QPlainTextEdit, QTextEdit)):
        t = w.toPlainText()
        return t, (t if len(t) <= 40 else t[:39] + "…").replace("\n", " ⏎ ")
    if isinstance(w, QTableWidget):
        cells = tuple(tuple((w.item(i, j).text() if w.item(i, j) else "") for j in range(w.columnCount()))
                      for i in range(w.rowCount()))
        return cells, tr(f"{w.rowCount()}행", f"{w.rowCount()} rows")
    return None, ""


class Snapshot:
    """Values of the input widgets under some roots, in widget order (a widget added or removed changes it)."""

    def __init__(self, roots):
        self.widgets = [w for r in roots if r is not None for w in input_widgets(r)]
        pairs = [_value(w) for w in self.widgets]
        self.values = tuple(p[0] for p in pairs)
        self.texts = [p[1] for p in pairs]

    def __eq__(self, other):
        return isinstance(other, Snapshot) and self.values == other.values

    def __hash__(self):
        return hash(self.values)

    def diff(self, other: Snapshot, label_scope) -> list[tuple[str, str, str]]:
        """(field, before, now) for the fields that differ, ``self`` being the earlier snapshot; field names are looked
        up in the widgets of ``label_scope`` (the page, so a single registered widget still finds its form label)."""
        if self.widgets == other.widgets:
            labels = labels_for(label_scope)
            return [(labels.get(id(w), _fallback_label(w)), a_t, b_t)
                    for w, a, b, a_t, b_t in zip(self.widgets, self.values, other.values, self.texts, other.texts)
                    if a != b]
        return [(tr("입력 구성", "input layout"), tr(f"항목 {len(self.widgets)}개", f"{len(self.widgets)} fields"),
                 tr(f"항목 {len(other.widgets)}개 (행·노드 추가/삭제)", f"{len(other.widgets)} fields (rows or nodes "
                                                                   f"added / removed)"))]


def _plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").replace("&nbsp;", " ").strip().rstrip(":")


def _widgets_in(item) -> list[QWidget]:
    """Widgets held by a form field (a widget and its children, or a nested layout)."""
    out = []
    if isinstance(item, QWidget):
        out = [item] + item.findChildren(QWidget)
    elif isinstance(item, QLayout):
        for k in range(item.count()):
            it = item.itemAt(k)
            if it.widget() is not None:
                out += _widgets_in(it.widget())
            elif it.layout() is not None:
                out += _widgets_in(it.layout())
    return out


def _group_of(w: QWidget) -> str:
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QGroupBox) and _plain(p.title()):
            return _plain(p.title())
        p = p.parentWidget()
    return ""


def labels_for(roots) -> dict[int, str]:
    """id(widget) -> the name a person sees for it: the form row label (or the check box starting the row); a name
    that appears in more than one group box gets the group title in front."""
    named: dict[int, str] = {}
    widgets: dict[int, QWidget] = {}
    for root in roots:
        if root is None:
            continue
        for form in root.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                li = form.itemAt(row, QFormLayout.LabelRole)
                fi = form.itemAt(row, QFormLayout.FieldRole) or form.itemAt(row, QFormLayout.SpanningRole)
                if fi is None:
                    continue
                inside = _widgets_in(fi.widget() if fi.widget() is not None else fi.layout())
                name = _plain(li.widget().text()) if li is not None and isinstance(li.widget(), QLabel) else ""
                if not name:
                    name = next((_plain(w.text()) for w in inside if isinstance(w, (QCheckBox, QRadioButton))), "")
                if not name:
                    continue
                for w in inside:
                    if id(w) not in named:
                        named[id(w)] = name
                        widgets[id(w)] = w
        for w in input_widgets(root):                  # outside a form: a check box's own text, else its group box
            if id(w) in named:
                continue
            if isinstance(w, (QCheckBox, QRadioButton)) and _plain(w.text()):
                named[id(w)] = _plain(w.text())
            elif _group_of(w):
                named[id(w)] = _group_of(w)
            else:
                continue
            widgets[id(w)] = w
    groups_per_name: dict[str, set] = {}
    for k, name in named.items():
        groups_per_name.setdefault(name, set()).add(_group_of(widgets[k]))
    out = {}
    for k, name in named.items():
        g = _group_of(widgets[k])
        out[k] = f"{g} › {name}" if len(groups_per_name[name]) > 1 and g and g != name else name
    return out


def _fallback_label(w: QWidget) -> str:
    if isinstance(w, (QCheckBox, QRadioButton)) and w.text():
        return _plain(w.text())
    tip = _plain(w.toolTip()).split("\n")[0]
    if tip:
        return tip if len(tip) <= 40 else tip[:39] + "…"
    return w.objectName() or type(w).__name__


def connect_changes(root: QWidget, slot) -> None:
    """Call ``slot()`` whenever an input widget under ``root`` changes (widgets added later are connected by a later
    call; each widget is connected once)."""
    for w in input_widgets(root):
        if w.property("twb_tracked"):
            continue
        w.setProperty("twb_tracked", True)
        if isinstance(w, QAbstractSpinBox):
            (w.valueChanged if hasattr(w, "valueChanged") else w.editingFinished).connect(lambda *_: slot())
        elif isinstance(w, QComboBox):
            w.currentIndexChanged.connect(lambda *_: slot())
        elif isinstance(w, (QCheckBox, QRadioButton)):
            w.toggled.connect(lambda *_: slot())
        elif isinstance(w, QLineEdit):
            w.textChanged.connect(lambda *_: slot())
        elif isinstance(w, (QPlainTextEdit, QTextEdit)):
            w.textChanged.connect(lambda: slot())
        elif isinstance(w, QTableWidget):
            w.itemChanged.connect(lambda *_: slot())
            w.model().rowsInserted.connect(lambda *_: slot())
            w.model().rowsRemoved.connect(lambda *_: slot())
