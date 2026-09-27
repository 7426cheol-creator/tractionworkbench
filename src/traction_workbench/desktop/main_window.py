"""Main window: navigation, header badges, menus, status bar with progress and cancel."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__
from ..i18n import language, tr
from ..io import load_json_file
from . import theme
from .pages.decision import DecisionPage
from .pages.design import DesignPage
from .pages.explorer import ExplorerPage
from .pages.model import ModelPage, examples_dir
from .pages.performance import PerformancePage
from .pages.safety import SafetyPage
from .pages.thermal import ThermalPage
from .pages.trajectory import TrajectoryPage
from .pages.verification import VerificationPage
from .state import AppState
from .widgets import error_box
from .worker import TaskRunner

PAGES = (
    ("decision", lambda: tr("요구 판정", "Decision"), DecisionPage),
    ("explorer", lambda: tr("운전점 탐색", "Operating point"), ExplorerPage),
    ("trajectory", lambda: tr("궤적", "Trajectories"), TrajectoryPage),
    ("performance", lambda: tr("성능 곡선·맵", "Envelope & maps"), PerformancePage),
    ("design", lambda: tr("설계·병목", "Design & bottleneck"), DesignPage),
    ("safety", lambda: tr("안전 스크리닝", "Safety screening"), SafetyPage),
    ("thermal", lambda: tr("열·지속시간", "Thermal & duration"), ThermalPage),
    ("model", lambda: tr("모델·데이터", "Model & data"), ModelPage),
    ("verification", lambda: tr("검증 (V&V)", "Verification"), VerificationPage),
)


class MainWindow(QMainWindow):
    def __init__(self, state: AppState | None = None):
        super().__init__()
        self.settings = QSettings("TractionWorkbench", "TractionWorkbench")
        self.state = state or AppState()
        self.runner = TaskRunner(self)
        self.setWindowTitle(f"Traction Workbench {__version__}")
        self.resize(1480, 920)
        self.setMinimumSize(1100, 700)
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFixedWidth(190)
        self.stack = QStackedWidget()
        self.pages = {}
        for key, label, cls in PAGES:
            page = cls(self)
            self.pages[key] = page
            self.stack.addWidget(page)
            self.nav.addItem(label())
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)
        self._status_bar()
        self._menus()
        self.state.drive_changed.connect(self._update_badges)
        self._update_badges()
        self.nav.setCurrentRow(0)

    # ------------------------------------------------------------------ chrome
    def _header(self):
        w = QWidget()
        w.setObjectName("Header")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 8, 14, 8)
        title = QLabel("Traction Workbench")
        title.setObjectName("AppTitle")
        sub = QLabel(tr("인버터–모터 요구 판정 · 근거 · 병목", "inverter–motor feasibility · evidence · bottlenecks"))
        sub.setObjectName("AppSubtitle")
        lay.addWidget(title)
        lay.addSpacing(10)
        lay.addWidget(sub)
        lay.addStretch(1)
        self.badge_model = QLabel()
        self.badge_model.setProperty("badge", "model")
        self.badge_data = QLabel()
        self.badge_data.setProperty("badge", "info")
        self.badge_hw = QLabel(tr("하드웨어 미검증", "not hardware-validated"))
        self.badge_hw.setProperty("badge", "warn")
        self.badge_hw.setToolTip(tr("합성 fixture에 대한 verification만 수행됨 (V4–V5 validation 미수행)",
                                    "verification against synthetic fixtures only (no V4–V5 validation)"))
        for b in (self.badge_model, self.badge_data, self.badge_hw):
            lay.addWidget(b)
        return w

    def _update_badges(self):
        s = self.state
        info = s.info()
        prov = info.get("provenance", {})
        self.badge_model.setText(f"{info.get('drive_id')} · {info.get('fidelity')}")
        self.badge_model.setToolTip(prov.get("validation_status", ""))
        self.badge_data.setText(f"{tr('데이터', 'data')}: {prov.get('origin')}")

    def _status_bar(self):
        sb = self.statusBar()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.cancel_btn = QPushButton(tr("취소", "cancel"))
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self.runner.cancel_all)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.cancel_btn)
        self.runner.started.connect(self._task_started)
        self.runner.progress.connect(self._task_progress)
        self.runner.done.connect(self._task_done)
        sb.showMessage(tr("준비 · 모든 계산은 이 PC 안에서 수행됩니다 (서버·네트워크 없음).",
                          "ready · everything runs on this PC (no server, no network)."))

    def _task_started(self, label):
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_btn.show()
        self.statusBar().showMessage(f"{label} …")

    def _task_progress(self, frac, msg):
        self.progress.setValue(int(1000 * max(0.0, min(1.0, frac))))
        self.statusBar().showMessage(msg)

    def _task_done(self, label, elapsed, ok):
        if not self.runner.busy():
            self.progress.hide()
            self.cancel_btn.hide()
        self.statusBar().showMessage(f"{label}: {'OK' if ok else tr('중단/오류', 'stopped/error')} ({elapsed:.2f} s)", 10000)

    def _menus(self):
        mb = self.menuBar()
        m = mb.addMenu(tr("파일", "File"))
        a = QAction(tr("case 파일 열기…", "Open case file…"), self)
        a.setShortcut(QKeySequence.Open)
        a.triggered.connect(self.open_case)
        m.addAction(a)
        a = QAction(tr("예시 폴더 열기", "Open examples folder"), self)
        a.triggered.connect(self._open_examples)
        m.addAction(a)
        m.addSeparator()
        a = QAction(tr("끝내기", "Quit"), self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        m.addAction(a)
        m = mb.addMenu(tr("보기", "View"))
        a = QAction(tr("다크 테마", "Dark theme"), self)
        a.setCheckable(True)
        a.setChecked(theme.current() == "dark")
        a.toggled.connect(lambda on: self.set_theme("dark" if on else "light"))
        m.addAction(a)
        lang = m.addMenu(tr("언어 (다시 시작 후 적용)", "Language (applies after restart)"))
        for code, label in (("ko", "한국어"), ("en", "English")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(language() == code)
            a.triggered.connect(lambda _=False, c=code: self._set_language(c))
            lang.addAction(a)
        m = mb.addMenu(tr("도움말", "Help"))
        a = QAction(tr("정보", "About"), self)
        a.triggered.connect(self._about)
        m.addAction(a)

    # ----------------------------------------------------------------- actions
    def set_theme(self, name: str):
        theme.apply(QApplication.instance(), name)
        self.settings.setValue("theme", name)
        for page in self.pages.values():
            page.redraw()

    def _set_language(self, code: str):
        self.settings.setValue("language", code)
        QMessageBox.information(self, "Traction Workbench",
                                tr("언어 설정은 다음 실행부터 적용됩니다.", "The language applies from the next start."))

    def _open_examples(self):
        ex = examples_dir()
        if ex:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(ex)))

    def open_case(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, tr("case 파일", "case file"), str(examples_dir() or ""), "JSON (*.json)")
        if not path:
            return
        try:
            case = load_json_file(path)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("case 파일 오류", "case file error"), str(exc))
            return
        page: DecisionPage = self.pages["decision"]
        self.nav.setCurrentRow(0)
        page.banner.set("NONE", tr(f"{Path(path).name} 평가 중…", f"evaluating {Path(path).name}…"))
        from .pages.decision import _evaluate_task
        self.runner.run("decision", Path(path).name, _evaluate_task, page._show, case, [], on_error=page._failed)

    def _about(self):
        QMessageBox.about(self, "Traction Workbench", tr(
            f"<b>Traction Workbench {__version__}</b><br>정상상태 기본파 dq 모델 기반 인버터–모터 요구 판정 도구.<br>"
            "판정(PASS/FAIL/UNKNOWN)은 증명·근거가 있을 때만 확정하며, 모든 결과는 재현 가능한 의사결정 기록으로 저장됩니다.<br><br>"
            "합성 fixture에 대한 verification만 수행되었습니다. 하드웨어 validation은 수행되지 않았습니다.",
            f"<b>Traction Workbench {__version__}</b><br>Inverter–motor requirement decisions on a steady-state fundamental "
            "dq model. Verdicts are only definite with proof/evidence; every result is a reproducible decision record.<br><br>"
            "Verified against synthetic fixtures only; no hardware validation."))

    def closeEvent(self, ev):
        self.runner.cancel_all()
        super().closeEvent(ev)
