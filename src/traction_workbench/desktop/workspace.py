"""Workspace: the inputs of every page, the page data behind them and the project, in one file (``twb-workspace/1``).

What is kept
    * every input widget under a page's registered input areas (the widgets whose change marks a shown result as
      computed from earlier inputs), addressed by its attribute path on the page (``torque``, ``ab.A.rth``,
      ``lim.fsw_max.1``): a code name, the same in every language, never a position in the layout;
    * widgets that hold more than they show (the thermal network editor's nodes, a curve grid showing one curve of
      several, a table of rules with choice cells) through their own ``workspace_state`` / ``restore_workspace``;
    * data a page keeps next to its widgets that reaches a calculation (a loaded temperature trace, datasheet modules
      of the A/B candidates, a measured EMI trace and its calibration binding, a winding hand-over), named by the
      page in ``workspace_data``;
    * the active project (product data), so a workspace reproduces its calculations on another PC.

What is not kept: results.  They are recomputed from the restored inputs (the records hold their own inputs).

Restoring puts the project first (pages reload their product inputs from it), then every page's fields in the order
they were saved (a preset chooser before the fields it fills), then the page data.  A saved value the current app
cannot take - a field that no longer exists, a choice no longer offered, a number outside the field's range, a table
with other columns - is reported, never guessed.
"""

from __future__ import annotations

import copy
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QMainWindow,
                               QPlainTextEdit, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTextEdit, QWidget)

from .. import __version__
from ..i18n import language, tr
from .inputs import input_widgets

SCHEMA = "twb-workspace/1"
SUFFIX = ".twb-workspace.json"
_DEPTH = 4
_NO = object()


# ---------------------------------------------------------------------------------------------- values of widgets
def _jsonable(x):
    """``x`` as JSON data (tuples become lists), or ``_NO`` when it has no JSON form."""
    def conv(v):
        if isinstance(v, (tuple, list)):
            return [conv(i) for i in v]
        if isinstance(v, dict):
            return {str(k): conv(i) for k, i in v.items()}
        if v is None or isinstance(v, (bool, int, str)):
            return v
        if isinstance(v, float):
            if not math.isfinite(v):
                raise TypeError
            return v
        tolist = getattr(v, "tolist", None)            # numpy arrays and scalars
        if tolist is not None:
            return conv(tolist())
        raise TypeError
    try:
        return conv(x)
    except (TypeError, ValueError):
        return _NO


def _is_input(w) -> bool:
    """A widget that carries an input (the same kinds as ``inputs.input_widgets``, which marks results stale)."""
    if isinstance(w, QLineEdit):
        return not (isinstance(w.parent(), (QAbstractSpinBox, QComboBox)) or w.isReadOnly())
    if isinstance(w, (QAbstractSpinBox, QComboBox, QCheckBox, QRadioButton, QTableWidget)):
        return True
    if isinstance(w, (QPlainTextEdit, QTextEdit)):
        return not w.isReadOnly()
    return False


def _entry(w) -> dict | None:
    if hasattr(w, "workspace_state"):
        return {"t": "state", "v": w.workspace_state()}
    if isinstance(w, QDoubleSpinBox):
        return {"t": "num", "v": float(w.value())}
    if isinstance(w, QSpinBox):
        return {"t": "int", "v": int(w.value())}
    if isinstance(w, QAbstractSpinBox):
        return {"t": "spintext", "v": w.text()}
    if isinstance(w, QComboBox):
        e = {"t": "combo", "i": w.currentIndex(), "n": w.count(), "text": w.currentText()}
        d = _jsonable(w.currentData())
        if d is not _NO and d is not None:
            e["data"] = d
        if w.isEditable():
            e["edit"] = w.currentText()
        return e
    if isinstance(w, (QCheckBox, QRadioButton)):
        return {"t": "check", "v": bool(w.isChecked())}
    if isinstance(w, (QPlainTextEdit, QTextEdit)):
        return {"t": "ptext", "v": w.toPlainText()}
    if isinstance(w, QTableWidget):
        return _table_entry(w)
    if isinstance(w, QLineEdit):
        return {"t": "text", "v": w.text()}
    return None


def _table_entry(t: QTableWidget) -> dict:
    rows = []
    for i in range(t.rowCount()):
        row = []
        for j in range(t.columnCount()):
            cw = t.cellWidget(i, j)
            if cw is not None:
                row.append({"w": _entry(cw)})
                continue
            it = t.item(i, j)
            if it is None:
                row.append(None)
            elif it.flags() & Qt.ItemIsUserCheckable:
                row.append({"s": it.text(), "c": it.checkState() == Qt.Checked})
            else:
                row.append(it.text())
        rows.append(row)
    e = {"t": "table", "cols": t.columnCount(), "rows": rows,
         "heads": [(t.horizontalHeaderItem(j).text() if t.horizontalHeaderItem(j) else "") for j in range(t.columnCount())]}
    vh = [(t.verticalHeaderItem(i).text() if t.verticalHeaderItem(i) else "") for i in range(t.rowCount())]
    if any(vh):
        e["vheads"] = vh
    return e


def _apply(w, e: dict, path: str) -> list[str]:
    """Put saved entry ``e`` into widget ``w``; the problems (never a silent substitute)."""
    kind = e.get("t")
    try:
        if kind == "state" and hasattr(w, "restore_workspace"):
            return [f"{path}: {p}" for p in (w.restore_workspace(e.get("v")) or [])]
        if kind == "num" and isinstance(w, QDoubleSpinBox):
            v = float(e["v"])
            w.setValue(v)
            tol = 0.51 * 10.0 ** (-w.decimals())
            if abs(w.value() - v) > tol:
                return [tr(f"{path}: 저장된 값 {v:g}이 입력 범위 [{w.minimum():g}, {w.maximum():g}] 밖 — {w.value():g}로 둠",
                           f"{path}: saved value {v:g} is outside the field range [{w.minimum():g}, {w.maximum():g}] "
                           f"- left at {w.value():g}")]
            return []
        if kind == "int" and isinstance(w, QSpinBox):
            v = int(e["v"])
            w.setValue(v)
            if w.value() != v:
                return [tr(f"{path}: 저장된 값 {v}이 입력 범위 밖 — {w.value()}로 둠",
                           f"{path}: saved value {v} is outside the field range - left at {w.value()}")]
            return []
        if kind == "spintext" and isinstance(w, QAbstractSpinBox):
            w.lineEdit().setText(str(e["v"]))
            return []
        if kind == "combo" and isinstance(w, QComboBox):
            if "edit" in e and w.isEditable():
                i = w.findText(e["edit"])
                if i >= 0:
                    w.setCurrentIndex(i)
                else:
                    w.setEditText(e["edit"])
                return []
            i = _combo_index(w, e)
            if i is None:
                return [tr(f"{path}: 저장된 선택 '{e.get('text', '')}'이 현재 선택지에 없음 — 바꾸지 않음",
                           f"{path}: the saved choice '{e.get('text', '')}' is not offered now - left unchanged")]
            w.setCurrentIndex(i)
            return []
        if kind == "check" and isinstance(w, (QCheckBox, QRadioButton)):
            if bool(e["v"]) or isinstance(w, QCheckBox):
                w.setChecked(bool(e["v"]))
            return []
        if kind == "ptext" and isinstance(w, (QPlainTextEdit, QTextEdit)):
            w.setPlainText(str(e["v"]))
            return []
        if kind == "table" and isinstance(w, QTableWidget):
            return _apply_table(w, e, path)
        if kind == "text" and isinstance(w, QLineEdit):
            w.setText(str(e["v"]))
            return []
    except (KeyError, TypeError, ValueError) as exc:
        return [tr(f"{path}: 저장된 값을 읽지 못함 ({exc})", f"{path}: the saved value could not be read ({exc})")]
    return [tr(f"{path}: 저장된 종류({kind})와 현재 입력 칸의 종류가 다름 — 바꾸지 않음",
               f"{path}: the saved kind ({kind}) does not match the current field - left unchanged")]


def _combo_index(w: QComboBox, e: dict):
    if "data" in e:
        for k in range(w.count()):
            d = _jsonable(w.itemData(k))
            if d is not _NO and d == e["data"]:
                return k
    if e.get("text"):
        k = w.findText(e["text"])
        if k >= 0:
            return k
    if e.get("n") == w.count() and 0 <= int(e.get("i", -1)) < w.count():   # same list (text in another language)
        return int(e["i"])
    return None


def _apply_table(t: QTableWidget, e: dict, path: str) -> list[str]:
    cols = int(e["cols"])
    if cols != t.columnCount():
        if not t.property("twb_dynamic_columns"):
            return [tr(f"{path}: 저장된 표의 열 수 {cols}가 현재 {t.columnCount()}와 다름 — 표를 바꾸지 않음",
                       f"{path}: the saved table has {cols} columns, the current one {t.columnCount()} - left "
                       f"unchanged")]
        t.setColumnCount(cols)
    if t.property("twb_dynamic_columns") and e.get("heads"):
        t.setHorizontalHeaderLabels(e["heads"])
    problems = []
    t.setRowCount(0)
    t.setRowCount(len(e["rows"]))
    for i, row in enumerate(e["rows"]):
        for j, cell in enumerate(row[:cols]):
            if isinstance(cell, dict) and "w" in cell:
                cw = t.cellWidget(i, j)
                if cw is None and j in getattr(t, "choice_cols", {}):     # a drop-down column builds its cell
                    t._set_choice(i, j, None)
                    cw = t.cellWidget(i, j)
                if cw is None:
                    problems.append(tr(f"{path}: {i + 1}행 {j + 1}열의 선택 칸이 없음", f"{path}: row {i + 1}, column "
                                                                               f"{j + 1} has no choice cell"))
                else:
                    problems += _apply(cw, cell["w"], f"{path}[{i + 1},{j + 1}]")
            elif isinstance(cell, dict):
                it = QTableWidgetItem(str(cell.get("s", "")))
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if cell.get("c") else Qt.Unchecked)
                t.setItem(i, j, it)
            elif cell is not None:
                t.setItem(i, j, QTableWidgetItem(str(cell)))
    if e.get("vheads"):
        t.setVerticalHeaderLabels(e["vheads"])
    fit = getattr(t, "_fit", None)
    if fit is not None:
        fit()
    return problems


# ---------------------------------------------------------------------------------------------- walking a page
def _is_page_or_window(v, owner) -> bool:
    if v is owner:
        return False
    if isinstance(v, QMainWindow):
        return True
    mod = type(v).__module__
    return mod.startswith("traction_workbench.desktop.pages.") and type(v).__name__.endswith("Page")


def _targets(obj, within=None) -> dict:
    """{attribute path: widget} for the inputs (and composite inputs) reachable from ``obj``'s attributes, in the order
    they were created; ``within``: only widgets inside these widgets (a page's input areas)."""
    out: dict = {}
    seen: set = set()

    def inside(w) -> bool:
        return within is None or any(r is w or r.isAncestorOf(w) for r in within)

    def walk(val, path, depth):
        if isinstance(val, QWidget):
            if id(val) in seen or _is_page_or_window(val, obj):
                return
            seen.add(id(val))
            if hasattr(val, "workspace_state") and val is not obj:
                if inside(val):
                    out[path] = val
                return
            if _is_input(val):
                if inside(val) and not val.property("twb_not_input"):
                    out[path] = val
                return
            if depth < _DEPTH and type(val).__module__.startswith("traction_workbench.desktop"):
                for k, v in vars(val).items():
                    walk(v, f"{path}.{k}" if path else str(k), depth + 1)
        elif isinstance(val, dict) and depth < _DEPTH:
            for k, v in list(val.items()):
                walk(v, f"{path}.{k}" if path else str(k), depth + 1)
        elif isinstance(val, (list, tuple)) and depth < _DEPTH:
            for k, v in enumerate(val):
                walk(v, f"{path}.{k}" if path else str(k), depth + 1)

    seen.add(id(obj))
    for k, v in vars(obj).items():
        if k in ("win", "parent_page"):
            continue
        walk(v, str(k), 1)
    return out


def capture_fields(obj, within=None) -> dict:
    """{attribute path: saved entry} for the inputs of ``obj`` (a page or a composite widget)."""
    out = {}
    for path, w in _targets(obj, within).items():
        e = _entry(w)
        if e is not None:
            out[path] = e
    return out


def restore_fields(obj, fields: dict, within=None) -> list[str]:
    """Put saved entries back (in their saved order); the problems, each naming the field."""
    targets = _targets(obj, within)
    problems = []
    for path, e in (fields or {}).items():
        w = targets.get(path)
        if w is None:
            problems.append(tr(f"{path}: 현재 앱에 이 입력 칸이 없음 — 무시함", f"{path}: no such field in this app - "
                                                                      f"ignored"))
            continue
        problems += _apply(w, e, path)
    return problems


def uncaptured(obj, within) -> list:
    """Input widgets inside ``within`` that no attribute path reaches (a workspace would silently drop them)."""
    reach = set()
    for w in _targets(obj, within).values():
        reach.add(id(w))
        if hasattr(w, "workspace_state"):
            reach.update(id(x) for x in input_widgets(w))
        if isinstance(w, QTableWidget):                 # the table's entry carries its cell widgets
            reach.update(id(w.cellWidget(i, j)) for i in range(w.rowCount()) for j in range(w.columnCount())
                         if w.cellWidget(i, j) is not None)
    return [w for r in within for w in input_widgets(r) if id(w) not in reach and not w.property("twb_not_input")]


# ---------------------------------------------------------------------------------------------- pages and window
def input_roots(win, key: str) -> list:
    from .main_window import TASK_PAGE
    roots = []
    for task, rs in win._input_roots.items():
        if TASK_PAGE.get(task) == key:
            roots += [r for r in rs if r is not None and r not in roots]
    return roots


def _tab_state(page) -> dict:
    return {k: v.currentIndex() for k, v in vars(page).items() if isinstance(v, QTabWidget)}


def page_state(win, key: str) -> dict:
    page = win.pages[key]
    st = {"fields": capture_fields(page, input_roots(win, key)), "tabs": _tab_state(page)}
    data = {}
    for name in getattr(page, "workspace_data", ()):
        d = _jsonable(copy.deepcopy(getattr(page, name, None)))
        if d is not _NO:
            data[name] = d
    if data:
        st["data"] = data
    return st


def restore_page(win, key: str, st: dict) -> list[str]:
    page = win.pages[key]
    problems = restore_fields(page, st.get("fields") or {}, input_roots(win, key))
    for name, value in (st.get("data") or {}).items():
        if name in getattr(page, "workspace_data", ()):
            setattr(page, name, copy.deepcopy(value))
        else:
            problems.append(tr(f"{name}: 이 페이지가 쓰지 않는 데이터 — 무시함", f"{name}: data this page does not use - "
                                                                     f"ignored"))
    for k, i in (st.get("tabs") or {}).items():
        tw = getattr(page, k, None)
        if isinstance(tw, QTabWidget) and 0 <= int(i) < tw.count():
            tw.setCurrentIndex(int(i))
    hook = getattr(page, "after_workspace_restore", None)      # labels that describe restored data
    if hook is not None:
        hook()
    return problems


def pages_with_inputs(win) -> list[str]:
    return [k for k in win.pages if input_roots(win, k)]


def build(win) -> dict:
    """The workspace of window ``win`` now."""
    st = win.state
    return {"schema": SCHEMA, "software": __version__, "language": language(),
            "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "project": st.project.to_dict(), "drive_label": st.drive_label, "drive_source": st.drive_source,
            "page": win.current_page(),
            "pages": {k: page_state(win, k) for k in pages_with_inputs(win)}}


def content(ws: dict | None) -> dict | None:
    """What decides whether two workspaces hold the same work (not when or where they were saved, nor the tabs)."""
    if ws is None:
        return None
    return {"project": ws.get("project"),
            "pages": {k: {"fields": v.get("fields"), "data": v.get("data")} for k, v in (ws.get("pages") or {}).items()}}


def check(ws) -> dict:
    """``ws`` if it is a workspace this app can read (else ``ValueError`` saying why)."""
    if not isinstance(ws, dict) or ws.get("schema") != SCHEMA:
        raise ValueError(tr(f"작업 공간 파일이 아닙니다 (schema {SCHEMA} 필요)", f"not a workspace file (schema {SCHEMA} "
                                                                         f"required)"))
    if not isinstance(ws.get("pages"), dict) or not isinstance(ws.get("project"), dict):
        raise ValueError(tr("작업 공간에 pages 또는 project가 없습니다", "the workspace has no pages or no project"))
    return ws


def apply(win, ws: dict) -> list[str]:
    """Restore workspace ``ws`` into ``win``: project, then page inputs, then the page shown.  The problems, each
    naming the page and the field."""
    from ..project import Project
    check(ws)
    st = win.state
    st.set_project(Project.from_dict(ws["project"]))             # validated; pages reload their product inputs
    if ws.get("drive_label"):
        st.drive_label = ws["drive_label"]
    if ws.get("drive_source"):
        st.drive_source = ws["drive_source"]
    problems = []
    labels = {k: lab() for k, lab, _cls in _pages_table()}
    for key, pst in ws["pages"].items():
        if key not in win.pages:
            problems.append(tr(f"페이지 '{key}'가 이 앱에 없음 — 무시함", f"page '{key}' does not exist in this app - ignored"))
            continue
        problems += [f"{labels.get(key, key)} · {p}" for p in restore_page(win, key, pst)]
    if ws.get("page") in win.pages:
        win.show_page(ws["page"])
    if ws.get("software") and ws["software"] != __version__:
        problems.insert(0, tr(f"다른 버전({ws['software']})에서 저장한 작업 공간 — 위 항목 외에는 그대로 복원됨",
                              f"saved by another version ({ws['software']}) - restored except the items listed"))
    return problems


def _pages_table():
    from .main_window import PAGES
    return PAGES


def save(ws: dict, path) -> Path:
    p = Path(path)
    p.write_text(json.dumps(ws, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return p


def load(path) -> dict:
    try:
        ws = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(tr(f"작업 공간 파일이 올바른 JSON이 아닙니다: {exc}", f"the workspace file is not valid JSON: {exc}"))
    return check(ws)


def recovery_path() -> Path:
    """Where the automatic copy of the last session is kept (``TWB_DATA_DIR`` overrides the user data folder)."""
    base = os.environ.get("TWB_DATA_DIR")
    if not base:
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or str(Path.home() / ".traction_workbench")
    d = Path(base)
    d.mkdir(parents=True, exist_ok=True)
    return d / ("recovery" + SUFFIX)


__all__ = ["SCHEMA", "SUFFIX", "apply", "build", "capture_fields", "check", "content", "input_roots", "load",
           "page_state", "pages_with_inputs", "recovery_path", "restore_fields", "restore_page", "save", "uncaptured"]
