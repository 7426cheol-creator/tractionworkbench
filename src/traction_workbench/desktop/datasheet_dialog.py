"""Datasheet import dialog: a datasheet spec (JSON + CSV of digitized curves) -> preview of the section exactly as it
would enter the project, the import findings, and 'apply' (a modified working copy of the active project)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget,
                               QVBoxLayout)

from .. import datasheet as DS
from ..i18n import tr
from ..plots import datasheet_figures as DF
from ..project import short
from ..examples import examples_dir
from .widgets import KeyValueTable, PlotPanel, error_box


class DatasheetDialog(QDialog):
    def __init__(self, win, path: str | None = None):
        super().__init__(win)
        self.win = win
        self.result = None
        self.new_project = None
        self.setWindowTitle(tr("데이터시트 파일 가져오기", "Import datasheet file"))
        self.resize(1100, 720)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.path_label = QLabel(tr("데이터시트 spec(JSON)을 선택하세요 — 모듈 곡선·대표값, 커패시터, dv/dt, 모터",
                                    "choose a datasheet spec (JSON) - module curves or values, capacitor, dv/dt, "
                                    "motor"))
        self.path_label.setWordWrap(True)
        row.addWidget(self.path_label, 1)
        for text, fn in ((tr("spec 열기…", "open spec…"), self.choose),
                         (tr("예시 폴더", "examples folder"), self.open_examples)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.tabs = QTabWidget()
        self.plot = PlotPanel(hint=tr("가져온 곡선이 여기에 표시됩니다 (프로젝트에 들어갈 값 그대로).",
                                      "the imported curves appear here, exactly as they would enter the project"))
        self.findings = KeyValueTable(headers=[tr("수준", "level"), tr("항목", "item"), tr("내용", "detail")])
        self.tabs.addTab(self.plot, tr("미리보기", "preview"))
        self.tabs.addTab(self.findings, tr("가져오기 기록 (findings)", "import findings"))
        lay.addWidget(self.tabs, 1)
        note = QLabel(tr("외삽·추정 없음: 모든 온도가 디지타이즈된 전류 구간만 쓰고, 0 A 기준점은 spec에서 선언했을 때만 추가합니다. "
                         "적용하면 프로젝트가 '수정됨' 작업 사본이 되며, 새 개정은 프로젝트 페이지에서 만듭니다.",
                         "No extrapolation, no invented data: only the current range every temperature covers is used, "
                         "and a zero-current anchor only when the spec declares it. Applying makes the project a "
                         "modified working copy; make a new revision on the project page."))
        note.setWordWrap(True)
        note.setObjectName("Hint")
        lay.addWidget(note)
        bb = QDialogButtonBox()
        self.apply_btn = bb.addButton(tr("프로젝트에 적용", "apply to the project"), QDialogButtonBox.AcceptRole)
        self.apply_btn.setEnabled(False)
        bb.addButton(QDialogButtonBox.Close)
        bb.accepted.connect(self.apply)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if path:
            self.load(path)

    def choose(self):
        start = str((examples_dir() or Path(".")) / "datasheets")
        path, _ = QFileDialog.getOpenFileName(self, tr("데이터시트 spec", "datasheet spec"), start, "JSON (*.json)")
        if path:
            self.load(path)

    def open_examples(self):
        ex = examples_dir()
        if ex:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(ex / "datasheets")))

    def load(self, path: str):
        try:
            spec, base_dir = DS.load_spec(path)
            new, res = DS.apply(self.win.state.project, spec, base_dir)
        except Exception as exc:  # noqa: BLE001 - shown with the reason; nothing is applied
            self.apply_btn.setEnabled(False)
            error_box(self, tr("가져오기 실패", "import failed"), str(exc))
            return
        self.new_project, self.result = new, res
        self.path_label.setText(f"{Path(path).name} — {res['provenance']['source']}")
        sec = res["section"]
        self.summary.setText(tr(f"<b>{sec}</b> 섹션 → digest {short(new.sections[sec].digest)} · 출처 supplier (qualified: 아니오) · "
                                f"기록 {len(res['findings'])}건",
                                f"<b>{sec}</b> section → digest {short(new.sections[sec].digest)} · origin supplier "
                                f"(qualified: no) · {len(res['findings'])} finding(s)"))
        if sec == "module":
            self.plot.draw(DF.fig_datasheet_module, res["data"], name="datasheet_module")
        elif sec == "dc_link":
            er = spec.get("ESR_representative")
            self.plot.draw(DF.fig_datasheet_capacitor, res["data"], name="datasheet_capacitor",
                           point=(er["f_Hz"], er["ESR_mohm"]) if er else None)
        elif sec == "drive":
            from ..io import drive_from_dict
            from .datasheet_entry_dialog import motor_preview
            info, _rows = motor_preview(drive_from_dict(res["data"]),
                                        float(self.win.state.project.data("dc_source")["Vdc_nominal_V"]))
            self.plot.draw(DF.fig_datasheet_motor, info, name="datasheet_motor")
        else:
            g = res["data"].get("gate") or {}
            self.plot.placeholder(tr(f"게이트 에지: 상승 {g.get('t_rise_ns', 0):.4g} ns · 하강 {g.get('t_fall_ns', 0):.4g} ns\n{g.get('basis', '')}",
                                     f"gate edges: rise {g.get('t_rise_ns', 0):.4g} ns · fall {g.get('t_fall_ns', 0):.4g} ns\n{g.get('basis', '')}"))
        self.findings.set_rows([[f["level"], f["item"], f["detail"]] for f in res["findings"]])
        self.apply_btn.setEnabled(True)

    def apply(self):
        if self.new_project is None:
            return
        self.win.state.set_project(self.new_project)
        self.win.statusBar().showMessage(tr(f"데이터시트 적용: {self.result['provenance']['source']}",
                                            f"datasheet applied: {self.result['provenance']['source']}"), 10000)
        self.accept()
