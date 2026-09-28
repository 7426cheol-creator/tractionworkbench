"""Project page (system review R2): the ONE set of product data every page uses - identity, sections with provenance
and digests, cross-section consistency, revisions and what a revision change affects."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel, QPushButton, QSplitter, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ...i18n import tr
from ...project import (SECTIONS, builtin_project, check_project, diff_projects, load_project, save_project,
                        short)
from ..widgets import ConceptNote, KeyValueTable, error_box, fmt

NOTE_PROJECT = lambda: tr(  # noqa: E731
    "<b>프로젝트 데이터 패키지</b>는 한 제품(한 구동 시스템)의 제품 데이터 — 드라이브, DC 전원, 파워 모듈과 열 경로, DC-link 커패시터, "
    "제어기(fsw·변조·데드타임·게이트 에지·전류 루프·타이밍·센싱·토크 경로), 감속기, 열망, 안전(FTTI·규칙), EMI 시험 set-up — 를 ID·개정과 함께 "
    "한 번만 담습니다. 모든 페이지는 활성 프로젝트에서 제품 데이터를 가져오고, 모든 결과는 사용한 섹션의 digest와 페이지에서 "
    "<b>로컬로 바꾼</b> 구성 요소를 기록합니다. 프로젝트가 바뀌면 그 섹션을 읽은 결과는 <b>stale</b>로 표시됩니다. 시나리오(운전점·미션·요구)는 "
    "각 분석에 남습니다. 일관성 검사는 같은 물리량의 두 값(INCONSISTENT), 분석을 UNKNOWN/낙관적으로 만드는 조합(WARNING), 서로 다른 추상화의 "
    "병기(NOTE)를 구분합니다.",
    "The <b>project data package</b> holds the product data of one product (one drive system) once, with an ID and a "
    "revision: drive, DC source, power module and its thermal path, DC-link capacitor, controller (fsw, modulation, "
    "dead time, gate edges, current loop, timing, sensing, torque path), gearbox, thermal networks, safety (FTTI, "
    "rules) and the EMI test set-up. Every page takes its product data from the active project; every result records "
    "the digests of the sections it used and the components the page <b>changed locally</b>. When the project changes, "
    "results that read a changed section are marked <b>stale</b>. Scenarios (operating points, missions, "
    "requirements) stay with each analysis. The consistency check separates two values for one quantity "
    "(INCONSISTENT), combinations that make analyses UNKNOWN or optimistic (WARNING) and different abstractions "
    "stated side by side (NOTE).")


def _selftest() -> bool:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    return bool(app and app.property("twb_selftest"))


class ProjectPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last_diff = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.title)
        row = QHBoxLayout()
        for label, fn in ((tr("프로젝트 열기…", "open project…"), self.open_project),
                          (tr("다른 이름으로 저장…", "save as…"), self.save_project),
                          (tr("새 개정…", "new revision…"), self.new_revision),
                          (tr("파일과 비교…", "compare with file…"), self.diff_with),
                          (tr("데이터시트 가져오기…", "import datasheet…"), self.import_datasheet),
                          (tr("내장 합성 프로젝트", "built-in synthetic project"), self.reset_builtin)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.tabs = QTabWidget()
        self.t_sections = KeyValueTable(headers=[tr("섹션", "section"), "digest", tr("출처", "origin"),
                                                 tr("검증", "qualified"), tr("개정", "revision"), tr("근거", "source")])
        self.t_check = KeyValueTable(headers=[tr("상태", "status"), tr("규칙", "rule"), tr("항목", "item"),
                                              tr("내용", "detail")])
        self.t_log = KeyValueTable(headers=[tr("개정", "revision"), tr("변경", "change"), tr("이전", "from")])
        self.t_diff = KeyValueTable()
        self.t_data = QTreeWidget()                     # the selected section's content (what the analyses read)
        self.t_data.setHeaderLabels([tr("항목", "item"), tr("값", "value")])
        self.t_data.setColumnWidth(0, 300)
        self.t_data.setAlternatingRowColors(True)
        self.t_sections.itemSelectionChanged.connect(self._show_selected)
        sec = QSplitter(Qt.Vertical)
        sec.addWidget(self.t_sections)
        sec.addWidget(self.t_data)
        sec.setSizes([330, 380])
        self.tabs.addTab(sec, tr("섹션", "sections"))
        self.tabs.addTab(self.t_check, tr("일관성 검사", "consistency"))
        self.tabs.addTab(self.t_log, tr("개정 이력", "change log"))
        self.tabs.addTab(self.t_diff, tr("개정 비교", "revision diff"))
        lay.addWidget(self.tabs, 1)
        lay.addWidget(ConceptNote(NOTE_PROJECT()))
        self.win.state.project_changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ view
    def refresh(self):
        p = self.win.state.project
        ident = p.identity()
        chk = check_project(p)
        self.last_check = chk
        self.title.setText(f"<b>{p.label}</b> — {p.title}<br>{tr('출처', 'origin')}: {p.origin} · "
                           f"project digest {short(ident['project_digest'])} · {tr('일관성', 'consistency')}: "
                           f"<b>{chk['status']}</b>" + (f"<br>{p.note}" if p.note else ""))
        rows, meaning = [], []
        for n, s in p.sections.items():
            pv = s.provenance
            rows.append([n, short(s.digest), str(pv.get("origin", "")), tr("예", "yes") if pv.get("qualified") else
                         tr("아니오", "no"), str(pv.get("revision", "")), str(pv.get("source", ""))])
            meaning.append(SECTIONS[n].title)
        for n, spec in SECTIONS.items():
            if n not in p.sections:
                rows.append([n, "-", "", "", "", tr("(프로젝트에 없음)", "(not in the project)")])
                meaning.append(spec.title)
        self.t_sections.set_rows(rows)
        for r, text in enumerate(meaning):              # what the section holds: on the section name
            self.t_sections.item(r, 0).setToolTip(text)
        self._section_names = list(p.sections) + [n for n in SECTIONS if n not in p.sections]
        self._show_selected()
        self.t_check.set_rows([[f["status"], f["rule"], f["title"], f["detail"]] for f in chk["findings"]])
        self.t_log.set_rows([[str(c.get("revision", "")), str(c.get("change", "")), str(c.get("from", ""))]
                             for c in p.change_log])

    def _show_selected(self):
        """The selected section as a tree: provenance first, then the data exactly as the analyses read it."""
        self.t_data.clear()
        rows = self.t_sections.selectionModel().selectedRows() if self.t_sections.selectionModel() else []
        names = getattr(self, "_section_names", [])
        name = names[rows[0].row()] if rows and rows[0].row() < len(names) else None
        p = self.win.state.project
        if name is None or name not in p.sections:
            QTreeWidgetItem(self.t_data, [tr("섹션을 선택하면 내용이 여기에 표시됩니다", "select a section to see its data"),
                                          ""])
            return
        s = p.sections[name]
        head = QTreeWidgetItem(self.t_data, [f"{name} — {SECTIONS[name].title}", f"digest {short(s.digest)}"])
        prov = QTreeWidgetItem(head, [tr("출처 (provenance)", "provenance"), ""])
        self._fill(prov, s.provenance)
        data = QTreeWidgetItem(head, [tr("데이터", "data"), ""])
        self._fill(data, s.data)
        head.setExpanded(True)
        prov.setExpanded(True)
        data.setExpanded(True)

    def _fill(self, parent, value):
        if isinstance(value, dict):
            for k, v in value.items():
                it = QTreeWidgetItem(parent, [str(k), "" if isinstance(v, (dict, list)) and v else fmt(v)])
                if isinstance(v, (dict, list)) and v:
                    self._fill(it, v)
        elif isinstance(value, list):
            if value and all(not isinstance(x, (dict, list)) for x in value):
                parent.setText(1, fmt(value) if len(value) <= 12 else fmt(value[:12]) + f", … ({len(value)})")
                return
            for i, v in enumerate(value):
                it = QTreeWidgetItem(parent, [f"[{i}]", "" if isinstance(v, (dict, list)) and v else fmt(v)])
                if isinstance(v, (dict, list)) and v:
                    self._fill(it, v)

    # ------------------------------------------------------------------ actions
    def open_project(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, tr("프로젝트 파일", "project file"), "", "JSON (*.json)")
        if not path:
            return
        try:
            p = load_project(path)
            self.win.state.set_project(p)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("프로젝트 오류", "project error"), str(exc))

    def save_project(self, path: str | None = None):
        if not path:
            p = self.win.state.project
            path, _ = QFileDialog.getSaveFileName(self, tr("프로젝트 저장", "save project"),
                                                  f"{p.id}_rev{p.revision}.json", "JSON (*.json)")
        if not path:
            return
        try:
            save_project(self.win.state.project, path)
            self.win.statusBar().showMessage(tr(f"저장: {Path(path).name}", f"saved: {Path(path).name}"), 8000)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("저장 실패", "save failed"), str(exc))

    def new_revision(self, revision: str | None = None, change: str | None = None):
        p = self.win.state.project
        if revision is None:
            revision, ok = QInputDialog.getText(self, tr("새 개정", "new revision"), tr("개정 이름", "revision name"))
            if not ok:
                return
        if change is None:
            change, ok = QInputDialog.getText(self, tr("새 개정", "new revision"), tr("변경 내용", "change note"))
            if not ok:
                return
        try:
            self.win.state.set_project(p.as_revision(revision, change))
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("개정 실패", "revision failed"), str(exc))

    def diff_with(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, tr("비교할 프로젝트", "project to compare"), "", "JSON (*.json)")
        if not path:
            return
        try:
            other = load_project(path)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("프로젝트 오류", "project error"), str(exc))
            return
        self.show_diff(diff_projects(other, self.win.state.project))

    def show_diff(self, d: dict):
        self.last_diff = d
        f, t = d["from"], d["to"]
        rows = [(tr("비교", "compared"), f"{f['project_id']} rev {f['revision']} → {t['project_id']} rev {t['revision']}"
                 + (" (modified)" if t.get("modified") else ""))]
        for name, c in d["changed_sections"].items():
            rows.append((f"{tr('섹션', 'section')} {name}", c["change"] + (f" · {short(c.get('from'))} → "
                                                                           f"{short(c.get('to'))}" if c.get("from") else "")))
            for p in c.get("paths", [])[:20]:
                rows.append(("   " + p["path"], f"{p['from']} → {p['to']}"))
        rows.append((tr("영향받는 분석", "affected analyses"),
                     ", ".join(f"{v['title']} ({', '.join(v['sections'])})" for v in d["affected_analyses"].values())
                     or tr("없음", "none")))
        rows.append((tr("영향 없는 분석", "unaffected analyses"), ", ".join(d["unaffected_analyses"]) or tr("없음", "none")))
        self.t_diff.set_rows(rows)
        self.tabs.setCurrentWidget(self.t_diff)

    def import_datasheet(self, path: str | None = None, apply: bool = False):
        """The datasheet import dialog; with ``path`` (and ``apply``) it loads (and applies) without waiting for the
        user - the self-test path."""
        from ..datasheet_dialog import DatasheetDialog
        dlg = DatasheetDialog(self.win, path)
        if apply:
            dlg.apply()
        elif not _selftest():
            dlg.exec()
        return dlg

    def reset_builtin(self):
        self.win.state.set_project(builtin_project())

    def redraw(self):
        pass
