"""MathWorks transfer package dialog: export the active project's design as a twb-mathworks/1 package, run it on a
local MATLAB / GNU Octave when one exists, re-import a target report - with the stages shown apart (package check,
target environment, model generation, parity, physical validation), never merged into one green light."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTabWidget, QVBoxLayout)

from .. import mathworks as MW
from ..i18n import tr
from ..project import short
from .widgets import KeyValueTable, error_box

OK_COLOR, BAD_COLOR, OPEN_COLOR = "#2e7d32", "#c62828", "#8a6d00"
GOOD = ("PASS",)
BAD = ("FAIL", "ERROR", "FOREIGN_REPORT")


def _color(status: str) -> str | None:
    s = str(status).split(" ")[0]
    return OK_COLOR if s in GOOD else BAD_COLOR if s in BAD else OPEN_COLOR


class MathWorksDialog(QDialog):
    def __init__(self, win, folder: str | None = None):
        super().__init__(win)
        self.win = win
        self.dir: Path | None = None
        self.manifest = None
        self.verification = None
        self.setWindowTitle(tr("MathWorks 이식 패키지", "MathWorks transfer package"))
        self.resize(1080, 700)
        lay = QVBoxLayout(self)
        intro = QLabel(tr(
            "활성 프로젝트의 설계를 MATLAB / Simulink / System Composer로 옮기는 패키지를 만듭니다: 모델 데이터·계약·"
            "비교 case(Python 값 + 수용된 원천의 oracle 값)·native MATLAB 코드·아키텍처 후보. 회사 PC에서 "
            "twb.runAll로 실행하고 보고서를 다시 가져오면, 이 패키지와 이 설계 개정에만 연결됩니다.",
            "Builds the package that carries the active project's design to MATLAB / Simulink / System Composer: model "
            "data, the contract, comparison cases (Python values + oracle values of the accepted source), native MATLAB "
            "code and architecture candidates. Run twb.runAll on the target; a report read back is linked only to this "
            "package and this design revision."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        self.dir_label = QLabel(tr("폴더: (선택 안 됨)", "folder: (none)"))
        self.dir_label.setWordWrap(True)
        row.addWidget(self.dir_label, 1)
        runtimes = MW.find_runtimes()
        self.runtime = runtimes[0] if runtimes else None
        rt = (f"{self.runtime['kind']}" if self.runtime else tr("없음", "none"))
        self.buttons = {}
        for key, text, fn in (("export", tr("패키지 만들기…", "export package…"), self.choose_and_export),
                              ("open", tr("기존 패키지 열기…", "open package…"), self.choose_existing),
                              ("run", tr("로컬 실행 ({})", "run locally ({})").format(rt), self.run_local),
                              ("report", tr("결과 보고서 가져오기…", "import report…"), self.import_report),
                              ("folder", tr("폴더 열기", "open folder"), self.open_folder)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
            self.buttons[key] = b
        if self.runtime is None:
            self.buttons["run"].setToolTip(tr("PATH에 MATLAB(matlab)이나 GNU Octave(octave-cli)가 없습니다 — 회사 PC에서 "
                                              "twb.runAll을 실행한 뒤 보고서를 가져오십시오.",
                                              "no MATLAB (matlab) or GNU Octave (octave-cli) on PATH - run twb.runAll "
                                              "on the target and import its report"))
        lay.addLayout(row)
        self.tabs = QTabWidget()
        self.t_status = KeyValueTable(headers=[tr("단계", "stage"), tr("상태", "status"), tr("의미", "meaning")])
        self.t_gaps = KeyValueTable(headers=[tr("항목", "capability"), tr("상태", "status"), tr("대상", "target"),
                                             tr("내용", "detail")])
        h = self.t_gaps.horizontalHeader()
        for i, w in enumerate((330, 160, 280)):         # long capability names wrap instead of pushing columns out
            h.setSectionResizeMode(i, QHeaderView.Interactive)
            self.t_gaps.setColumnWidth(i, w)
        self.t_gaps.setWordWrap(True)
        self.t_models = KeyValueTable(headers=[tr("모델", "model"), tr("역할", "role"), tr("형태", "family"),
                                               "drive_id", "content SHA-256"])
        self.t_issues = KeyValueTable(headers=[tr("구분", "kind"), tr("내용", "detail")])
        self.tabs.addTab(self.t_status, tr("상태", "status"))
        self.tabs.addTab(self.t_gaps, tr("포함 범위 (gap report)", "scope (gap report)"))
        self.tabs.addTab(self.t_models, tr("모델", "models"))
        self.tabs.addTab(self.t_issues, tr("문제·불일치", "problems"))
        # rows are fitted while a tab is hidden (its stretch column still narrow): refit when a tab is shown
        self.tabs.currentChanged.connect(lambda _i: self.tabs.currentWidget().resizeRowsToContents())
        lay.addWidget(self.tabs, 1)
        note = QLabel(tr("Octave 실행은 MATLAB 언어 호환 proxy입니다 — MATLAB·Simulink·System Composer 단계는 해당 제품이 있는 "
                         "환경에서 실행될 때까지 NOT_RUN입니다. 비교 검증 통과는 구현 동등성이며 물리 검증이 아닙니다.",
                         "An Octave run is a MATLAB-language proxy - MATLAB, Simulink and System Composer stages stay "
                         "NOT_RUN until run where those products exist. A parity pass is implementation equivalence, "
                         "not a physical validation."))
        note.setWordWrap(True)
        note.setObjectName("Hint")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._enable()
        self._show_status()
        if folder:
            self.export(folder)

    # -- actions --------------------------------------------------------------------------------------------
    def _enable(self):
        has = self.dir is not None and (self.dir / "manifest.json").is_file()
        for k in ("report", "folder"):
            self.buttons[k].setEnabled(has)
        self.buttons["run"].setEnabled(has and self.runtime is not None)

    def choose_and_export(self):
        d = QFileDialog.getExistingDirectory(self, tr("패키지 폴더 (비어 있는 폴더)", "package folder (an empty folder)"))
        if d:
            self.export(d)

    def choose_existing(self):
        d = QFileDialog.getExistingDirectory(self, tr("패키지 폴더", "package folder"))
        if d:
            self.dir = Path(d)
            self.verify()

    def export(self, folder: str):
        prj = self.win.state.project

        def task(progress, out, project):
            progress(0.1, tr("모델·case·oracle 값 계산", "models, cases and oracle values"))
            return MW.export_package(out, project)

        def done(man):
            self.dir = Path(man["dir"])
            self.manifest = man
            self.verify()
        self.win.runner.run("mathworks", tr("MathWorks 이식 패키지", "MathWorks package"), task, done, folder, prj,
                            on_error=lambda msg, tb: error_box(self, tr("패키지 만들기 실패", "export failed"), msg))

    def run_local(self):
        if self.dir is None or self.runtime is None:
            return

        def task(progress, d, kind):
            progress(0.1, tr("twb.runAll 실행 중", "running twb.runAll"))
            return MW.run_local(d, kind)

        def done(res):
            self.verification = res["verification"]
            self._show_status(run=res)
        self.win.runner.run("mathworks-run", tr("MathWorks 로컬 실행", "MathWorks local run"), task, done, self.dir,
                            self.runtime["kind"],
                            on_error=lambda msg, tb: error_box(self, tr("로컬 실행 실패", "local run failed"), msg))

    def import_report(self, path: str | None = None):
        if self.dir is None:
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, tr("대상 보고서", "target report"),
                                                  str(self.dir / "results"), "JSON (*.json)")
            if not path:
                return
        self.verify(path)

    def verify(self, report: str | None = None):
        try:
            self.verification = MW.verify_report(self.dir, report, self.win.state.project)
        except (OSError, ValueError) as exc:
            error_box(self, tr("보고서를 읽을 수 없습니다", "cannot read the report"), str(exc))
            return
        self._show_status()

    def open_folder(self):
        if self.dir is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.dir)))

    # -- display --------------------------------------------------------------------------------------------
    def _show_status(self, run: dict | None = None):
        self._enable()
        v = self.verification
        if self.dir is None or v is None:
            self.dir_label.setText(tr("폴더: (선택 안 됨) — 비어 있는 폴더를 고르면 패키지를 만듭니다",
                                      "folder: (none) - choose an empty folder to export a package"))
            self.t_status.set_rows([(tr("패키지 검사", "package check"), "-", tr("패키지 없음", "no package"))])
            return
        try:
            man = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            man = {}
        self.dir_label.setText(tr("폴더: {} — fingerprint {} · 설계 {}", "folder: {} - fingerprint {} · design {}").format(
            self.dir, short(man.get("semantic_fingerprint")), man.get("project_label", "?")))
        cases = v.get("cases") or {}
        par = v["parity"] + (f"  ({cases.get('PASS', 0)} PASS · {cases.get('FAIL', 0)} FAIL · "
                             f"{cases.get('ERROR', 0)} ERROR · {cases.get('NOT_SUPPORTED', 0)} NOT_SUPPORTED)"
                             if cases else "")
        stages = v.get("stages") or {}
        rows = [(tr("패키지 검사", "package check"), v["package_check"],
                 tr("schema·SHA-256·축과 형상·단위·규약", "schema, SHA-256, axes and shapes, units, conventions")),
                (tr("대상 환경", "target environment"), v["target_environment"],
                 tr("실제로 실행한 제품·버전 (preflight)", "the product and version that actually ran (preflight)")),
                (tr("모델 생성 (Simulink harness)", "model generation (Simulink harness)"), v["model_generation"],
                 tr("정적 평가 block — 동적 plant가 아님", "static evaluation block - not a dynamic plant")),
                (tr("비교 검증 (parity)", "parity"), par,
                 tr("native 결과 vs Python 값(2)과 oracle 값(1)", "native results vs Python values (2) and oracle "
                                                                  "values (1)")),
                (tr("guard 검사", "guard checks"), stages.get("self_checks", "NOT_RUN"),
                 tr("전치·이중 온도 보정·규약·손실 이중 계산·사전 충돌 거부", "transposition, double temperature "
                    "correction, conventions, loss double counting, dictionary conflicts")),
                (tr("물리 검증", "physical validation"), v["physical_validation"], ""),
                (tr("현재 설계의 근거", "evidence for the current design"),
                 tr("예", "yes") if v["linked_as_current_evidence"] else tr("아니오", "no"),
                 tr("이 패키지·이 개정의 보고서가 통과했을 때만", "only a passing report of this package and "
                    "revision"))]
        colors = {(i, 1): _color(r[1]) for i, r in enumerate(rows) if i != 1}
        colors[(1, 1)] = OPEN_COLOR if v["target_environment"] == "NOT_CHECKED" else None
        colors[(len(rows) - 1, 1)] = OK_COLOR if v["linked_as_current_evidence"] else OPEN_COLOR
        colors = {k: c for k, c in colors.items() if c}
        self.t_status.set_rows(rows, colors)
        try:
            gaps = json.loads((self.dir / "gap_report.json").read_text(encoding="utf-8"))["items"]
            self.t_gaps.set_rows([(g["capability"], g["status"], g["target"], g["detail"]) for g in gaps])
        except (OSError, ValueError, KeyError):
            self.t_gaps.set_rows([])
        self.t_models.set_rows([(k, m["role"], m["family"], m["drive_id"], short(m["content_sha256"]))
                                for k, m in (man.get("models") or {}).items()])
        issues = [(tr("패키지", "package"), p) for p in v["package_problems"]] + \
                 [(tr("보고서", "report"), p) for p in v["problems"]] + \
                 [(f["case_id"], ", ".join(f["failures"])) for f in v.get("failed_cases", [])]
        if run is not None:
            issues.append((tr("실행", "run"), f"{run['runtime']['kind']} exit {run['exit_code']}, log {run['log']}"))
        self.t_issues.set_rows(issues or [("-", tr("없음", "none"))])
        self.tabs.currentWidget().resizeRowsToContents()

    def showEvent(self, event):
        super().showEvent(event)
        self.tabs.currentWidget().resizeRowsToContents()
