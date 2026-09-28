"""Main window: navigation, header badges, menus, status bar with progress and cancel."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton, QStackedWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

from .. import __version__
from ..i18n import language, tr
from ..io import load_json_file
from ..project import request_usage, short, stale_sections
from . import theme
from .pages.decision import DecisionPage
from .pages.design import DesignPage
from .pages.efficiency import EfficiencyPage
from .pages.emi import EmiPage
from .pages.explorer import ExplorerPage
from .pages.machine import MachinePage
from .pages.model import ModelPage, examples_dir
from .pages.oew_hev import OewHevPage
from .pages.performance import PerformancePage
from .pages.power import PowerPage
from .pages.project import ProjectPage
from .pages.protection import ProtectionPage
from .pages.requirement_set import RequirementSetPage
from .pages.pwm_driveline import PwmDrivelinePage
from .pages.safety import SafetyPage
from .pages.thermal import ThermalPage
from .pages.trajectory import TrajectoryPage
from .pages.verification import VerificationPage
from .state import AppState
from .widgets import error_box, tidy_inputs
from .worker import TaskRunner

PAGES = (
    ("decision", lambda: tr("요구 판정", "Decision"), DecisionPage),
    ("requirement_set", lambda: tr("요구 묶음·후보", "Requirement set"), RequirementSetPage),
    ("explorer", lambda: tr("운전점 탐색", "Operating point"), ExplorerPage),
    ("trajectory", lambda: tr("궤적", "Trajectories"), TrajectoryPage),
    ("performance", lambda: tr("성능 곡선·맵", "Envelope & maps"), PerformancePage),
    ("design", lambda: tr("설계·병목", "Design & bottleneck"), DesignPage),
    ("safety", lambda: tr("안전 스크리닝", "Safety screening"), SafetyPage),
    ("protection", lambda: tr("보호·고장", "Protection & fault"), ProtectionPage),
    ("thermal", lambda: tr("열·지속시간", "Thermal & duration"), ThermalPage),
    ("power", lambda: tr("전력변환·수명", "Power stage & life"), PowerPage),
    ("efficiency", lambda: tr("효율·모듈 비교", "Efficiency & modules"), EfficiencyPage),
    ("pwm_driveline", lambda: tr("가변 PWM·Anti-jerk", "Variable PWM & anti-jerk"), PwmDrivelinePage),
    ("oew_hev", lambda: tr("OEW·HEV", "OEW & HEV"), OewHevPage),
    ("emi", lambda: tr("EMI (전도성)", "EMI (conducted)"), EmiPage),
    ("machine", lambda: tr("모터 설계", "Machine design"), MachinePage),
    ("project", lambda: tr("프로젝트", "Project"), ProjectPage),
    ("model", lambda: tr("모델·데이터", "Model & data"), ModelPage),
    ("verification", lambda: tr("검증 (V&V)", "Verification"), VerificationPage),
)


# navigation: pages grouped by the engineering question they answer (the stack keeps the PAGES order)
NAV_GROUPS = (
    (lambda: tr("제품 데이터", "Product data"), ("project", "model")),
    (lambda: tr("요구·구동 성능", "Requirement & drive"), ("decision", "requirement_set", "explorer", "trajectory",
                                                                   "performance", "design")),
    (lambda: tr("전력·열·효율", "Power, heat & efficiency"), ("thermal", "power", "efficiency")),
    (lambda: tr("제어·EMC", "Control & EMC"), ("pwm_driveline", "emi")),
    (lambda: tr("안전·보호", "Safety & protection"), ("safety", "protection")),
    (lambda: tr("시스템·설계", "Systems & design"), ("oew_hev", "machine")),
    (lambda: tr("검증", "Verification"), ("verification",)),
)
PAGE_INFO = {
    "project": lambda: tr("한 제품의 제품 데이터(섹션·출처·개정), 일관성 검사, 데이터시트 값 입력·파일 가져오기, MathWorks 이식 "
                          "패키지", "one product's data (sections, provenance, revisions), consistency, datasheet value "
                                   "entry and file import, MathWorks package"),
    "model": lambda: tr("드라이브 모델과 DC 소스 한계 선택, 데이터 감사(이 데이터로 할 수 있는 것/없는 것)",
                        "drive model and DC source limits, data audit (what this data can and cannot support)"),
    "decision": lambda: tr("요구(토크·속도·전압·지속시간)를 PASS/FAIL/UNKNOWN으로 판정하고 근거·병목·다음 조치를 보여줍니다",
                           "judges a requirement (torque, speed, voltage, duration) PASS/FAIL/UNKNOWN with evidence, "
                           "bottlenecks and next actions"),
    "requirement_set": lambda: tr("요구 여러 건을 같은 제품·조건·근거로 한 번에 판정(CSV), UNKNOWN 원인 분류와 다음 자료, "
                                  "설계 후보를 모든 요구에 대해 재판정",
                                  "many requirements judged at once on one product, conditions and evidence (CSV), the "
                                  "class of each open answer and the next data, candidates re-judged against every "
                                  "requirement"),
    "explorer": lambda: tr("한 운전점(토크 요구 또는 id·iq 직접)의 전압·전류·전력·손실·제약",
                           "one operating point (torque request or id/iq): voltages, currents, powers, losses, constraints"),
    "trajectory": lambda: tr("속도·토크 스윕을 따라 정책 운전점과 한계 전환(약계자·DC 한계)",
                             "policy points along a speed or torque sweep and the limit transitions"),
    "performance": lambda: tr("T–n 성능 곡선과 효율·손실·변조지수 맵", "T–n envelope and efficiency / loss / modulation maps"),
    "design": lambda: tr("파라미터 민감도·역설계와 병목(제약 1% 완화·요구 완화)",
                         "parameter sensitivity / inverse design and bottleneck analysis"),
    "thermal": lambda: tr("냉각수·열 회로망으로 지속시간별 가용 토크 스크리닝",
                          "coolant loop and thermal networks: torque available per duration (screening)"),
    "power": lambda: tr("데이터시트 모듈 손실, DC-link 리플·커패시터, 열 사이클 수명",
                        "datasheet module losses, DC-link ripple and capacitor, thermal-cycle lifetime"),
    "efficiency": lambda: tr("다섯 경계별 효율, 효율 맵·미션 에너지, 모듈 A/B 비교",
                             "efficiency per boundary, maps, mission energy, module A/B comparison"),
    "pwm_driveline": lambda: tr("가변 PWM 정책·타이밍·전환과 anti-jerk(능동 감쇠)",
                                "variable PWM policies, timing, transitions and anti-jerk active damping"),
    "emi": lambda: tr("HV 포트 전도성 EMI 스크리닝(소스 → 경로 → 수신기)",
                      "conducted EMI on the HV port (source → path → receiver), screening"),
    "safety": lambda: tr("FTTI 체인, DC-link 방전·과전압, 안전 상태(ASC/Freewheel) 스크리닝",
                         "FTTI chain, DC-link discharge / overvoltage, safe state (ASC / freewheel) screening"),
    "protection": lambda: tr("임계값·디레이팅·고장 반응 검토와 ASC 과도",
                             "thresholds, derating and fault reaction review; ASC transient"),
    "oew_hev": lambda: tr("OEW 듀얼 인버터와 HEV 두 기기 공통 bus", "open-end winding dual inverter and HEV two-machine bus"),
    "machine": lambda: tr("모터 스케일링 트레이드, 권선 계산, 개념 사이징",
                          "machine scaling trade study, winding calculator, concept sizing"),
    "verification": lambda: tr("golden fixture 대비 acceptance와 참조 패키지 무결성",
                               "acceptance against golden fixtures and reference package integrity"),
}


# runner task -> page that shows it (safety-page analyses run inline and report through ``note_result``)
TASK_PAGE = {"decision": "decision", "decision-env": "decision", "requirement_set": "requirement_set",
             "explorer": "explorer", "trajectory": "trajectory",
             "performance": "performance", "design-sweep": "design", "design-dom": "design", "thermal": "thermal",
             "protection": "protection", "asc": "protection", "module": "power", "ripple": "power",
             "lifetime": "power", "efficiency": "efficiency", "efficiency_map": "efficiency",
             "efficiency_mission": "efficiency", "module_compare": "efficiency", "pwm_policies": "pwm_driveline",
             "pwm_timing": "pwm_driveline", "pwm_ripple": "pwm_driveline", "pwm_transients": "pwm_driveline",
             "driveline": "pwm_driveline", "driveline_stability": "pwm_driveline", "oew": "oew_hev",
             "oew_compare": "oew_hev", "hev_joint": "oew_hev", "hev_crank": "oew_hev", "hev_rejection": "oew_hev",
             "hev_planetary": "oew_hev", "emi": "emi", "emi_oew": "emi", "machine_trade": "machine",
             "winding": "machine", "concept_sizing": "machine", "ftti": "safety", "passive": "safety",
             "discharge": "safety", "overvoltage": "safety", "safe_state": "safety"}
# tasks whose argument is not a request body: they run on the state's drive and limits
STATE_TASKS = ("decision-env", "requirement_set", "explorer", "trajectory", "performance", "design-sweep",
               "design-dom")
LIMIT_PAIRS = (("discharge_power_max_W", "discharge_power_max"), ("charge_power_max_W", "charge_power_max"),
               ("discharge_current_max_A", "discharge_current_max"), ("charge_current_max_A", "charge_current_max"))


def _case_body(case: dict) -> dict:
    """The drive and limits a decision case runs on, in request-body form."""
    sl = ((case.get("scenario") or {}).get("source_limits") or {})
    lim = {k1: (sl[k2]["value"] if isinstance(sl.get(k2), dict) else None) for k1, k2 in LIMIT_PAIRS}
    return {"drive": case.get("drive"), "limits": lim}


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
        self.banners = {}
        self.usages: dict = {}                      # page -> task -> what the last result ran on
        self.runner.result_hook = self._on_result
        for key, label, cls in PAGES:
            page = cls(self)
            self.pages[key] = page
            holder = QWidget()
            hv = QVBoxLayout(holder)
            hv.setContentsMargins(0, 0, 0, 0)
            hv.setSpacing(0)
            ban = QLabel()
            ban.setObjectName("ProjectBanner")
            ban.setWordWrap(True)
            ban.hide()
            self.banners[key] = ban
            hv.addWidget(ban)
            hv.addWidget(page, 1)
            self.stack.addWidget(holder)
        self._page_index = {key: i for i, (key, *_r) in enumerate(PAGES)}
        self._build_nav()
        self.nav.currentRowChanged.connect(self._nav_changed)
        tidy_inputs(self.stack)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)
        self._status_bar()
        self._menus()
        self.state.drive_changed.connect(self._update_badges)
        self.state.project_changed.connect(self._project_changed)
        self._update_badges()
        self.show_page("decision")

    # ------------------------------------------------------------------ navigation
    def _build_nav(self):
        labels = {key: label for key, label, _cls in PAGES}
        placed = [k for _g, keys in NAV_GROUPS for k in keys]
        assert sorted(placed) == sorted(labels), "every page belongs to exactly one navigation group"
        self._nav_rows = {}
        for group, keys in NAV_GROUPS:
            head = QListWidgetItem(group())
            head.setFlags(Qt.NoItemFlags)                    # a group title: not selectable, skipped by the keyboard
            f = head.font()
            f.setPointSizeF(f.pointSizeF() * 0.85 if f.pointSizeF() > 0 else 8.0)
            f.setBold(True)
            head.setFont(f)
            self.nav.addItem(head)
            for key in keys:
                it = QListWidgetItem("  " + labels[key]())
                it.setData(Qt.UserRole, key)
                it.setToolTip(PAGE_INFO[key]())
                self.nav.addItem(it)
                self._nav_rows[key] = self.nav.row(it)

    def _nav_changed(self, row: int):
        it = self.nav.item(row)
        key = it.data(Qt.UserRole) if it is not None else None
        if key in self._page_index:
            self.stack.setCurrentIndex(self._page_index[key])

    def show_page(self, key: str):
        """Show page ``key`` (the navigation and the page stack stay in step)."""
        self.nav.setCurrentRow(self._nav_rows[key])

    def current_page(self) -> str:
        it = self.nav.currentItem()
        return it.data(Qt.UserRole) if it is not None else ""

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
        self.badge_project = QLabel()
        self.badge_project.setProperty("badge", "info")
        self.badge_hw = QLabel(tr("하드웨어 미검증", "not hardware-validated"))
        self.badge_hw.setProperty("badge", "warn")
        self.badge_hw.setToolTip(tr("합성 fixture에 대한 verification만 수행됨 (V4–V5 validation 미수행)",
                                    "verification against synthetic fixtures only (no V4–V5 validation)"))
        for b in (self.badge_project, self.badge_model, self.badge_data, self.badge_hw):
            lay.addWidget(b)
        return w

    def _update_badges(self):
        s = self.state
        info = s.info()
        prov = info.get("provenance", {})
        self.badge_model.setText(f"{info.get('drive_id')} · {info.get('fidelity')}")
        self.badge_model.setToolTip(prov.get("validation_status", ""))
        self.badge_data.setText(f"{tr('데이터', 'data')}: {prov.get('origin')}")
        p = s.project
        self.badge_project.setText(f"{tr('프로젝트', 'project')}: {p.label}")
        self.badge_project.setToolTip(f"{p.title}\nproject digest {short(p.digest())}")

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
        a = QAction(tr("프로젝트 열기…", "Open project…"), self)
        a.triggered.connect(lambda: self.open_project())
        m.addAction(a)
        a = QAction(tr("프로젝트 저장…", "Save project…"), self)
        a.triggered.connect(lambda: self.save_project())
        m.addAction(a)
        a = QAction(tr("데이터시트 값 입력…", "Enter datasheet values…"), self)
        a.triggered.connect(lambda: self.pages["project"].enter_datasheet())
        m.addAction(a)
        a = QAction(tr("데이터시트 파일 가져오기…", "Import datasheet file…"), self)
        a.triggered.connect(lambda: self.pages["project"].import_datasheet())
        m.addAction(a)
        a = QAction(tr("MathWorks 이식 패키지…", "MathWorks transfer package…"), self)
        a.triggered.connect(lambda: self.pages["project"].mathworks_package())
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
        a = QAction(tr("화면 안내", "Page guide"), self)
        a.setShortcut(QKeySequence.HelpContents)
        a.triggered.connect(self.page_guide)
        m.addAction(a)
        a = QAction(tr("정보", "About"), self)
        a.triggered.connect(self._about)
        m.addAction(a)

    # ----------------------------------------------------------------- project identity of results (R2)
    def _on_result(self, key, args, res):
        if key in STATE_TASKS or not args or not isinstance(args[0], dict):
            body = self.state.body()
        elif key == "decision":
            body = _case_body(args[0])
        else:
            body = args[0]
        self.note_result(key, body, res)

    def note_result(self, key: str, body: dict, res=None) -> dict | None:
        """Record what a result ran on (project identity for its sections, per component project data or a local
        edit) on the result and in the page banner."""
        use = request_usage(self.state.project, key, body)
        if use.get("analysis") is None:
            return None
        use["project_label"] = self.state.project.label
        for name, why in self.state.fallbacks.items():
            use.setdefault("fallbacks", {})[name] = why
        if isinstance(res, dict):
            res["project_usage"] = use
            if isinstance(res.get("record"), dict):
                res["record"]["project_context"] = use     # annotation of the record, outside its input identity
        page = TASK_PAGE.get(key)
        if page:
            self.usages.setdefault(page, {})[key] = use
            self._refresh_banner(page)
        return use

    def _refresh_banner(self, page: str):
        ban = self.banners.get(page)
        uses = self.usages.get(page) or {}
        if ban is None or not uses:
            return
        stale, local, parts = [], [], []
        for task, use in uses.items():
            st = stale_sections(use, self.state.project)
            if st:
                stale.append(f"{task}: {', '.join(st)}")
            if use.get("local_edits"):
                local.append(f"{task}: {', '.join(use['local_edits'])}")
            parts.append(task)
        first = next(iter(uses.values()))
        if stale:
            state = "stale"
            text = tr(f"<b>stale</b> — 결과 계산 후 프로젝트가 바뀌었습니다 ({'; '.join(stale)}): 다시 계산하세요",
                      f"<b>stale</b> — the project changed after these results ({'; '.join(stale)}): recompute")
        elif local:
            state = "local"
            text = tr(f"프로젝트 {first['project_label']}의 제품 데이터 + <b>이 페이지의 로컬 변경</b> ({'; '.join(local)})",
                      f"product data of project {first['project_label']} + <b>local edits on this page</b> "
                      f"({'; '.join(local)})")
        else:
            state = "info"
            text = tr(f"결과({', '.join(parts)})는 프로젝트 {first['project_label']}의 제품 데이터로 계산됨",
                      f"results ({', '.join(parts)}) computed from the product data of project {first['project_label']}")
        ban.setProperty("state", state)
        ban.style().unpolish(ban)
        ban.style().polish(ban)
        ban.setText(text)
        ban.show()

    def _project_changed(self):
        for page in self.pages.values():
            fn = getattr(page, "apply_project", None)
            if fn is not None:
                fn(self.state.project)
        for key in self.usages:
            self._refresh_banner(key)
        self._update_badges()

    def open_project(self, path: str | None = None):
        self.pages["project"].open_project(path)

    def save_project(self, path: str | None = None):
        self.pages["project"].save_project(path)

    # ----------------------------------------------------------------- actions
    def set_theme(self, name: str, persist: bool = True):
        theme.apply(QApplication.instance(), name)
        if persist:                                   # the self-test switches themes without touching user settings
            self.settings.setValue("theme", name)
        self.redraw_all()

    def redraw_all(self):
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
        self.show_page("decision")
        page.banner.set("NONE", tr(f"{Path(path).name} 평가 중…", f"evaluating {Path(path).name}…"))
        from .pages.decision import _evaluate_task
        self.runner.run("decision", Path(path).name, _evaluate_task, page._show, case, [], on_error=page._failed)

    def page_guide_html(self) -> str:
        labels = {key: label for key, label, _cls in PAGES}
        parts = [tr("<h3>작업 흐름</h3><ol><li><b>제품 데이터</b>: 프로젝트(한 제품의 데이터)를 확인하고, 필요하면 데이터시트 "
                    "대표값을 직접 입력하거나(모터·모듈·커패시터·dv/dt) 파일을 가져오고, 새 개정을 만듭니다.</li><li><b>요구 판정</b>: 핵심 요구를 PASS/FAIL/UNKNOWN으로 판정하고 병목을 "
                    "확인합니다.</li><li><b>상세 분석</b>: 전력·열·제어·안전 페이지에서 같은 제품 데이터로 세부 질문을 봅니다. "
                    "결과가 어떤 제품 데이터로 계산됐는지는 페이지 위 배너에 표시되고, 데이터가 바뀌면 stale로 표시됩니다.</li>"
                    "<li><b>이식·검증</b>: 프로젝트 페이지에서 MathWorks 이식 패키지를 만들고, 검증 페이지에서 golden 대비 "
                    "acceptance를 확인합니다.</li></ol><p>UNKNOWN은 실패가 아니라 '증명·근거가 부족함'입니다. 판정을 확정하려면 "
                    "다음 조치에 적힌 데이터나 선언을 보완하세요.</p>",
                    "<h3>Workflow</h3><ol><li><b>Product data</b>: check the project (one product's data); type representative "
                    "datasheet values (motor, module, capacitor, dv/dt) or import a datasheet file, and make a new "
                    "revision when needed.</li><li><b>Requirement decision</b>: judge the key "
                    "requirements PASS/FAIL/UNKNOWN and look at the bottlenecks.</li><li><b>Detailed analyses</b>: the "
                    "power, heat, control and safety pages use the same product data; the banner above each page names "
                    "it and marks results stale when it changes.</li><li><b>Port and verify</b>: build the MathWorks "
                    "package on the project page; check acceptance against the golden fixtures on the verification "
                    "page.</li></ol><p>UNKNOWN is not a failure: it means proof or evidence is missing. Supply the data "
                    "or declaration named under next actions to settle it.</p>")]
        for group, keys in NAV_GROUPS:
            parts.append(f"<h3>{group()}</h3><ul>")
            parts.extend(f"<li><b>{labels[k]()}</b> — {PAGE_INFO[k]()}</li>" for k in keys)
            parts.append("</ul>")
        return "".join(parts)

    def page_guide(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("화면 안내", "Page guide"))
        dlg.resize(760, 640)
        lay = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setHtml(self.page_guide_html())
        lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if not QApplication.instance().property("twb_selftest"):
            dlg.exec()
        return dlg

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
