"""Main window: navigation, header badges, menus, status bar with progress and cancel."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QStackedWidget, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from .. import __version__
from ..i18n import language, tr
from ..io import load_json_file
from ..project import request_usage, short, stale_sections
from . import theme
from .pages.charging import ChargingPage
from .pages.decision import DecisionPage
from .pages.design import DesignPage
from .pages.budget import BudgetPage
from .pages.drive_cycle import DriveCyclePage
from .pages.efficiency import EfficiencyPage
from .pages.emi import EmiPage
from .pages.explorer import ExplorerPage
from .pages.fault_sim import FaultSimPage
from .pages.machine import MachinePage
from .pages.model import ModelPage, examples_dir
from .pages.oew_hev import OewHevPage
from .pages.performance import PerformancePage
from .pages.power import PowerPage
from .pages.project import ProjectPage
from .pages.protection import ProtectionPage
from .pages.reference import ReferencePage
from .pages.requirement_set import RequirementSetPage
from .pages.pwm_driveline import PwmDrivelinePage
from .pages.safety import SafetyPage
from .pages.thermal import ThermalPage
from .pages.trajectory import TrajectoryPage
from .pages.verification import VerificationPage
from .state import AppState
from .widgets import ElidedLabel, error_box, tidy_inputs
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
    ("fault_sim", lambda: tr("고장 시뮬레이션·FuSa", "Fault simulation & FuSa"), FaultSimPage),
    ("reference", lambda: tr("기능안전 요구 검증", "FuSa reference verification"), ReferencePage),
    ("thermal", lambda: tr("열·지속시간", "Thermal & duration"), ThermalPage),
    ("power", lambda: tr("전력변환·수명", "Power stage & life"), PowerPage),
    ("efficiency", lambda: tr("효율·모듈 비교", "Efficiency & modules"), EfficiencyPage),
    ("pwm_driveline", lambda: tr("가변 PWM·Anti-jerk", "Variable PWM & anti-jerk"), PwmDrivelinePage),
    ("oew_hev", lambda: tr("OEW·HEV", "OEW & HEV"), OewHevPage),
    ("emi", lambda: tr("EMI (전도성)", "EMI (conducted)"), EmiPage),
    ("machine", lambda: tr("모터 설계", "Machine design"), MachinePage),
    ("drive_cycle", lambda: tr("주행 사이클", "Drive cycle"), DriveCyclePage),
    ("charging", lambda: tr("통합 충전", "Integrated charging"), ChargingPage),
    ("budget", lambda: tr("시스템 버짓", "System budgets"), BudgetPage),
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
    (lambda: tr("안전·보호", "Safety & protection"), ("safety", "protection", "fault_sim", "reference")),
    (lambda: tr("시스템·설계", "Systems & design"), ("oew_hev", "machine")),
    (lambda: tr("구동 시스템", "Drive system"), ("drive_cycle", "charging", "budget")),
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
    "fault_sim": lambda: tr("고장 → 측정·추정 → 제어·감시 → 보호 반응 → 실제 브리지·토크·전류·DC-link → SG/FSR/TSR 판정의 인과 "
                            "시뮬레이션, 반응 후보 비교, 캠페인·반례, 검증 근거",
                            "causal simulation fault → measurement → control and monitoring → reaction → actual "
                            "bridge, torque, current, DC link → SG / FSR / TSR verdicts; reaction candidates, "
                            "campaigns and counterexamples, validation evidence"),
    "reference": lambda: tr("사양서·스터디 참고 패키지의 항목을 이 제품 모델에서 하나씩 판정: 근거 수준 유지, OPEN 값은 "
                            "추정하지 않음(UNKNOWN), 충돌 기록은 변형으로, 안전 상태는 물리 결과(C1-C4)로 - 요구-증거 매트릭스"
                            "·UNKNOWN/CONFLICT 보고·타당성 지도·HTML/CSV",
                            "a specification or study reference verified item by item on this product "
                            "model: provenance kept, OPEN values never guessed (UNKNOWN), conflicting records as "
                            "variants, the safe state on the physics (C1-C4) - requirement-to-evidence matrix, "
                            "unknown / conflict report, feasibility map, HTML / CSV"),
    "oew_hev": lambda: tr("OEW 듀얼 인버터와 HEV 두 기기 공통 bus", "open-end winding dual inverter and HEV two-machine bus"),
    "machine": lambda: tr("모터 스케일링 트레이드, 권선 계산, 개념 사이징",
                          "machine scaling trade study, winding calculator, concept sizing"),
    "verification": lambda: tr("golden fixture 대비 acceptance와 참조 패키지 무결성",
                               "acceptance against golden fixtures and reference package integrity"),
    "drive_cycle": lambda: tr("차량을 표준(WLTC·UDDS·HWFET·US06) 또는 가져온 속도 궤적 위에서: 모터 운전점, 부품별 에너지·손실, "
                              "소비·주행거리, 회생과 마찰 제동, 구동이 전달하지 못한 구간",
                              "the vehicle on a standard (WLTC, UDDS, HWFET, US06) or imported speed trace: machine "
                              "points, energy and losses per component, consumption and range, regeneration and the "
                              "friction brakes, intervals the drive does not deliver"),
    "charging": lambda: tr("인버터와 모터 권선을 승압기로 쓰는 저전압 DC 충전: 한 스위칭 주기의 전류, 소자·권선 손실과 Tj, "
                           "선언된 한계, 충전기·배터리 전압별 최대 충전 전력과 막는 한계",
                           "low-voltage DC charging through the inverter and the motor windings as a boost: one "
                           "switching period, device and winding losses and Tj, the declared limits, the maximum "
                           "charging power over charger and battery voltages and the limit that binds"),
    "budget": lambda: tr("한계를 기여 항목에 나누고 아래에서 위로 확인: 운전점별 토크 정확도(센서·레졸버·자석 온도·모델 공차를 "
                         "기기 모델로 계산)와 기능안전 창·모니터 문턱, FTTI 체인, 주행 사이클 손실, 직접 선언하는 버짓",
                         "a limit split over its contributors and checked bottom-up: torque accuracy per operating "
                         "point (sensors, resolver, magnet temperature and model tolerance computed with the machine "
                         "model) with the safety window and monitor threshold, the FTTI chain, drive-cycle losses, "
                         "a budget you declare"),
}


# runner task -> page that shows it (safety-page analyses run inline and report through ``note_result``)
TASK_PAGE = {"decision": "decision", "decision-env": "decision", "requirement_set": "requirement_set",
             "explorer": "explorer", "trajectory": "trajectory",
             "performance": "performance", "design-sweep": "design", "design-dom": "design", "thermal": "thermal",
             "thermal_cycle": "thermal",
             "protection": "protection", "asc": "protection", "module": "power", "ripple": "power",
             "lifetime": "power", "efficiency": "efficiency", "efficiency_map": "efficiency",
             "efficiency_mission": "efficiency", "module_compare": "efficiency", "pwm_policies": "pwm_driveline",
             "pwm_timing": "pwm_driveline", "pwm_ripple": "pwm_driveline", "pwm_transients": "pwm_driveline",
             "driveline": "pwm_driveline", "driveline_stability": "pwm_driveline", "oew": "oew_hev",
             "oew_compare": "oew_hev", "hev_joint": "oew_hev", "hev_crank": "oew_hev", "hev_rejection": "oew_hev",
             "hev_planetary": "oew_hev", "emi": "emi", "emi_oew": "emi", "machine_trade": "machine",
             "winding": "machine", "concept_sizing": "machine", "ftti": "safety", "passive": "safety",
             "discharge": "safety", "overvoltage": "safety", "safe_state": "safety", "pdf": "decision",
             "acceptance": "verification", "fault_sim": "fault_sim", "fault_compare": "fault_sim",
             "fault_campaign": "fault_sim", "fault_rerun": "fault_sim", "fault_validation": "fault_sim",
             "fault_review": "fault_sim", "fault_verification": "fault_sim", "drive_cycle": "drive_cycle",
             "charging_point": "charging", "charging_capability": "charging", "budget_torque": "budget",
             "budget_ftti": "budget", "budget_cycle": "budget", "budget_custom": "budget"}
# what a task is called on screen (a running task uses the label it was started with)
TASK_LABELS = {
    "decision": lambda: tr("요구 판정", "decision"), "decision-env": lambda: tr("T–n 곡선", "T–n envelope"),
    "pdf": lambda: tr("PDF 보고서", "PDF report"), "requirement_set": lambda: tr("요구 묶음 판정", "requirement set"),
    "explorer": lambda: tr("운전점 탐색", "operating point"), "trajectory": lambda: tr("궤적", "trajectory"),
    "performance": lambda: tr("성능 곡선·맵", "envelope & maps"), "design-sweep": lambda: tr("역설계", "sizing"),
    "design-dom": lambda: tr("병목 분석", "bottleneck analysis"), "thermal": lambda: tr("열 가용성", "thermal availability"),
    "thermal_cycle": lambda: tr("반복 부하", "repeated load"), "protection": lambda: tr("보호 검토", "protection review"),
    "asc": lambda: tr("ASC 과도", "ASC transient"), "module": lambda: tr("모듈 손실", "module losses"),
    "ripple": lambda: tr("DC-link 리플", "DC-link ripple"), "lifetime": lambda: tr("열 사이클 수명", "thermal-cycle life"),
    "efficiency": lambda: tr("경계별 효율", "boundary efficiency"), "efficiency_map": lambda: tr("효율 지도", "efficiency maps"),
    "efficiency_mission": lambda: tr("미션 에너지", "mission energy"), "module_compare": lambda: tr("모듈 A/B", "module A/B"),
    "pwm_policies": lambda: tr("PWM 정책 비교", "PWM policies"), "pwm_timing": lambda: tr("타이밍·전환", "timing"),
    "pwm_ripple": lambda: tr("PWM 리플", "PWM ripple"), "pwm_transients": lambda: tr("샘플링·전환 과도", "transients"),
    "driveline": lambda: tr("anti-jerk 변형", "anti-jerk variants"), "driveline_stability": lambda: tr("감쇠 안정성", "stability"),
    "oew": lambda: tr("OEW 운전점", "OEW point"), "oew_compare": lambda: tr("OEW T–n 비교", "OEW T–n comparison"),
    "hev_joint": lambda: tr("HEV 동시 토크 집합", "HEV joint torque set"), "hev_crank": lambda: tr("크랭킹", "cranking"),
    "hev_rejection": lambda: tr("부하 차단 에너지", "load rejection"), "hev_planetary": lambda: tr("유성기어", "planetary"),
    "emi": lambda: tr("EMI", "EMI"), "emi_oew": lambda: tr("OEW 공통모드", "OEW common mode"),
    "machine_trade": lambda: tr("트레이드 스터디", "trade study"), "winding": lambda: tr("권선", "winding"),
    "concept_sizing": lambda: tr("개념 사이징", "concept sizing"), "ftti": lambda: tr("FTTI", "FTTI"),
    "passive": lambda: tr("패시브 방전", "passive discharge"), "discharge": lambda: tr("능동 방전", "active discharge"),
    "overvoltage": lambda: tr("회생 과전압", "regen overvoltage"), "safe_state": lambda: tr("안전 상태", "safe state"),
    "acceptance": lambda: tr("acceptance", "acceptance"),
    "fault_sim": lambda: tr("고장 시뮬레이션", "fault simulation"),
    "fault_compare": lambda: tr("반응 후보 비교", "reaction candidates"),
    "fault_campaign": lambda: tr("고장 캠페인", "fault campaign"),
    "fault_rerun": lambda: tr("반례 재실행", "counterexample re-run"),
    "fault_validation": lambda: tr("플랜트 검증", "plant validation"),
    "fault_review": lambda: tr("정적 설계 검토", "static design review"),
    "fault_verification": lambda: tr("검증 매트릭스", "verification matrix"),
    "reference": lambda: tr("기능안전 요구 검증", "reference verification"),
    "drive_cycle": lambda: tr("주행 사이클", "drive cycle"),
    "charging_point": lambda: tr("통합 충전 운전점", "integrated charging point"),
    "charging_capability": lambda: tr("충전 능력 지도", "charging capability"),
    "budget_torque": lambda: tr("토크 정확도 버짓", "torque-accuracy budget"),
    "budget_ftti": lambda: tr("FTTI 버짓", "FTTI budget"), "budget_cycle": lambda: tr("사이클 손실 버짓", "cycle-loss budget"),
    "budget_custom": lambda: tr("선언 버짓", "declared budget"),
}
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


def ask(parent, title: str, text: str, buttons: list) -> str | None:
    """A question with named answers: ``buttons`` = [(key, label), ...], the first is the default.  The key of the
    answer, or None when the dialog is closed."""
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    keys = {}
    for i, (key, label) in enumerate(buttons):
        b = box.addButton(label, QMessageBox.AcceptRole if i == 0 else QMessageBox.RejectRole
                          if key == "cancel" else QMessageBox.DestructiveRole)
        keys[id(b)] = key
        if i == 0:
            box.setDefaultButton(b)
    box.exec()
    return keys.get(id(box.clickedButton()))


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
        self.run_actions: dict[str, QToolButton] = {}
        self.usages: dict = {}                      # page -> task -> what the last result ran on
        # inputs of each task (MainWindow.track_inputs): roots, the snapshot a running task started from and the
        # snapshot of the result on screen; ``_changed`` holds the tasks whose inputs differ from their shown result
        self._input_roots: dict[str, list] = {}
        self._inputs_started: dict = {}
        self._inputs_before_show: dict = {}
        self._inputs_shown: dict = {}
        self._changed: dict[str, list] = {}
        self._check_pending = False
        self.runner.result_hook = self._on_result
        self.runner.shown_hook = self._on_shown
        for key, label, cls in PAGES:
            page = cls(self)
            self.pages[key] = page
            holder = QWidget()
            hv = QVBoxLayout(holder)
            hv.setContentsMargins(0, 0, 0, 0)
            hv.setSpacing(0)
            hv.addWidget(self._page_bar(key, label))
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
        for key, page in self.pages.items():          # the run action follows the tab a page shows
            for tw in page.findChildren(QTabWidget):
                tw.currentChanged.connect(lambda _i, k=key: self._refresh_run_action(k))
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)
        self._status_bar()
        self._menus()
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(seq), self).activated.connect(self.run_current)
        QShortcut(QKeySequence(Qt.Key_Escape), self).activated.connect(self.cancel_current)
        self.runner.task_started.connect(self._task_started_key)
        self.runner.task_finished.connect(self._task_finished_key)
        self.state.drive_changed.connect(self._update_badges)
        self.state.project_changed.connect(self._project_changed)
        self._update_badges()
        self.show_page("decision")
        for key in self.pages:
            self._refresh_run_action(key)
        self._restore_session()
        self._init_workspace()

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
            self._refresh_run_action(key)

    def show_page(self, key: str):
        """Show page ``key`` (the navigation and the page stack stay in step)."""
        self.nav.setCurrentRow(self._nav_rows[key])

    def current_page(self) -> str:
        it = self.nav.currentItem()
        return it.data(Qt.UserRole) if it is not None else ""

    # ------------------------------------------------------------------ page bar: title, purpose, run / cancel
    def _page_bar(self, key: str, label) -> QWidget:
        bar = QWidget()
        bar.setObjectName("PageBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 4, 8, 4)
        h.setSpacing(10)
        title = QLabel(label())
        title.setObjectName("PageTitle")
        info = ElidedLabel(PAGE_INFO[key]())
        info.setObjectName("PageInfo")
        btn = QToolButton()
        btn.setObjectName("RunAction")
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn.clicked.connect(lambda _=False, k=key: self._run_action(k))
        self.run_actions[key] = btn
        h.addWidget(title)
        h.addWidget(info, 1)
        h.addWidget(btn)
        return bar

    def page_tasks(self, key: str) -> list[str]:
        """Running tasks whose result this page shows."""
        return [k for k in self.runner.running() if TASK_PAGE.get(k) == key]

    def run_targets(self, key: str) -> list:
        """The page's run buttons on the tab it shows (the first is what Ctrl+Enter and the page bar run)."""
        page = self.pages[key]
        return [b for b in page.findChildren(QPushButton) if b.objectName() == "Primary" and b.isVisibleTo(page)]

    @staticmethod
    def _run_text(b) -> str:
        return b.text().replace("(Ctrl+Enter)", "").strip()

    def _refresh_run_action(self, key: str | None = None):
        key = key or self.current_page()
        btn = self.run_actions.get(key)
        if btn is None:
            return
        busy = self.page_tasks(key)
        old = btn.menu()
        btn.setMenu(None)
        if old is not None:
            old.deleteLater()
        if busy:
            names = ", ".join(self.runner.labels.get(k, k) for k in busy)
            btn.setText("■ " + tr(f"취소 — {names}", f"Cancel — {names}"))
            btn.setToolTip(tr("이 페이지에서 진행 중인 계산을 취소합니다 (Esc)", "cancel this page's calculation (Esc)"))
            btn.setPopupMode(QToolButton.DelayedPopup)
            btn.setProperty("busy", True)
            btn.setEnabled(True)
            btn.show()
        else:
            targets = self.run_targets(key)
            btn.setProperty("busy", False)
            if not targets:
                btn.hide()
            else:
                first = targets[0]
                btn.setText("▶ " + self._run_text(first))
                btn.setToolTip(tr("이 페이지의 계산을 실행합니다 (Ctrl+Enter) — 입력 칸이 길어 버튼이 가려져도 여기서 실행",
                                  "run this page's calculation (Ctrl+Enter), wherever its button is scrolled"))
                btn.setEnabled(first.isEnabled())
                if len(targets) > 1:
                    menu = QMenu(btn)
                    for b in targets:
                        a = menu.addAction(self._run_text(b))
                        a.setEnabled(b.isEnabled())
                        a.triggered.connect(lambda _=False, b=b: b.click())
                    btn.setMenu(menu)
                    btn.setPopupMode(QToolButton.MenuButtonPopup)
                else:
                    btn.setPopupMode(QToolButton.DelayedPopup)
                btn.show()
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _run_action(self, key: str):
        if self.page_tasks(key):
            for k in self.page_tasks(key):
                self.runner.cancel(k)
                self._progress_msg = (k, self.runner.labels.get(k, k))
            self._show_progress()
            return
        targets = [b for b in self.run_targets(key) if b.isEnabled()]
        if targets:
            targets[0].click()

    def run_current(self):
        """Ctrl+Enter: run the current page's calculation (its first run button on the shown tab)."""
        key = self.current_page()
        if key and not self.page_tasks(key):
            self._run_action(key)

    def cancel_current(self):
        """Esc: cancel the current page's running calculations (other pages keep running)."""
        key = self.current_page()
        if key and self.page_tasks(key):
            self._run_action(key)

    def _task_started_key(self, key: str, label: str):
        self._progress_msg = (key, f"{label} …")
        self._elapsed_timer.start()
        if key in self._input_roots:
            self._inputs_started[key] = self._snapshot(key)
        page = TASK_PAGE.get(key)
        if page:
            self._refresh_run_action(page)

    def _task_finished_key(self, key: str, outcome: str):
        """Every task end (result, error or cancel) gives the page its run buttons back and tells it the outcome."""
        self._inputs_started.pop(key, None)
        self._inputs_before_show.pop(key, None)
        page = TASK_PAGE.get(key)
        if not page:
            return
        pg = self.pages.get(page)
        if pg is not None and not self.page_tasks(page):
            for b in pg.findChildren(QPushButton):
                if b.objectName() == "Primary" and not b.isEnabled():
                    b.setEnabled(True)
        hook = getattr(pg, "task_finished", None)
        if hook is not None:
            hook(key, outcome)
        self._refresh_run_action(page)

    # ------------------------------------------------------------------ inputs behind the shown results
    def track_inputs(self, keys, *roots) -> None:
        """Tell the window which widgets hold the inputs of tasks ``keys``; a result shown for one of them is marked
        as computed from earlier inputs as soon as those widgets change."""
        from .inputs import connect_changes
        keys = (keys,) if isinstance(keys, str) else tuple(keys)
        for k in keys:
            self._input_roots[k] = list(roots)
        for r in roots:
            connect_changes(r, self._schedule_input_check)

    def _snapshot(self, key: str):
        from .inputs import Snapshot, connect_changes
        roots = self._input_roots.get(key) or []
        for r in roots:                              # rows, nodes or tabs added since registration
            connect_changes(r, self._schedule_input_check)
        return Snapshot(roots)

    def _on_shown(self, key: str):
        if key not in self._input_roots:
            return
        start = self._inputs_started.get(key)
        before = self._inputs_before_show.pop(key, None)
        after = self._snapshot(key)
        # edits made while the task ran keep the start snapshot (the result is already older than the inputs);
        # otherwise what the page set while showing the result (e.g. a synced field) belongs to the result
        self._inputs_shown[key] = start if (start is not None and before is not None and before != start) else after
        self._check_inputs(TASK_PAGE.get(key))

    def _schedule_input_check(self):
        if self._check_pending:
            return
        self._check_pending = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._check_all_inputs)

    def _check_all_inputs(self):
        self._check_pending = False
        for page in {TASK_PAGE.get(k) for k in self._inputs_shown}:
            self._check_inputs(page)

    def input_changes(self, key: str) -> list:
        """(field, value then, value now) for the inputs of task ``key`` that changed since its shown result."""
        shown = self._inputs_shown.get(key)
        if shown is None:
            return []
        now = self._snapshot(key)
        page = self.pages.get(TASK_PAGE.get(key, ""))
        return [] if now == shown else shown.diff(now, [page] if page is not None else self._input_roots.get(key) or [])

    def _check_inputs(self, page: str | None):
        if not page:
            return
        changed = {k: self.input_changes(k) for k in self._inputs_shown if TASK_PAGE.get(k) == page}
        changed = {k: v for k, v in changed.items() if v}
        if changed == self._changed.get(page, {}):
            return
        self._changed[page] = changed
        hook = getattr(self.pages.get(page), "inputs_changed", None)
        if hook is not None:
            hook(changed)
        self._refresh_banner(page)

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
        self.cancel_btn = QPushButton(tr("모두 취소", "cancel all"))
        self.cancel_btn.setToolTip(tr("모든 페이지의 진행 중인 계산을 취소합니다 (한 페이지만: 페이지 위 버튼 또는 Esc)",
                                      "cancel the calculations of every page (one page: its bar button or Esc)"))
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._cancel_all)
        self._progress_msg = None                    # (task key, message) of the calculation the status bar shows
        self._elapsed_timer = QTimer(self)           # keeps its running time current between progress messages
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._show_progress)
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

    def _task_progress(self, key, frac, msg):
        self.progress.setValue(int(1000 * max(0.0, min(1.0, frac))))
        self._progress_msg = (key, msg)
        self._show_progress()

    def _show_progress(self):
        """Where the shown calculation is, how long it has run (from one second on) and whether a cancel waits for
        its next step."""
        if self._progress_msg is None:
            return
        key, msg = self._progress_msg
        t = self.runner.elapsed(key)
        if t is None:                                # it has finished: _task_done says how
            return
        task = self.runner.active.get(key)
        if task is not None and task.cancelled:
            msg += tr(" — 취소 요청됨, 다음 계산 단계에서 멈춥니다", " — cancel requested, it stops at the next step")
        self.statusBar().showMessage(msg + (f" · {t:.0f} s" if t >= 1.0 else ""))

    def _cancel_all(self):
        self.runner.cancel_all()
        self._show_progress()

    def _task_done(self, label, elapsed, outcome):
        if not self.runner.busy():
            self.progress.hide()
            self.cancel_btn.hide()
            self._elapsed_timer.stop()
            self._progress_msg = None
        if outcome == "replaced":                    # a newer run of the same calculation took over: no news
            return
        word = {"ok": "OK", "cancelled": tr("취소됨", "cancelled"), "error": tr("오류", "error")}.get(outcome, outcome)
        self.statusBar().showMessage(f"{label}: {word} ({elapsed:.2f} s)", 10000)

    def _menus(self):
        mb = self.menuBar()
        m = mb.addMenu(tr("파일", "File"))
        a = QAction(tr("작업 공간 열기…", "Open workspace…"), self)
        a.setShortcut(QKeySequence("Ctrl+Shift+O"))
        a.triggered.connect(lambda: self.open_workspace())
        m.addAction(a)
        a = QAction(tr("작업 공간 저장", "Save workspace"), self)
        a.setShortcut(QKeySequence.Save)
        a.triggered.connect(lambda: self.save_workspace())
        m.addAction(a)
        a = QAction(tr("작업 공간을 다른 이름으로 저장…", "Save workspace as…"), self)
        a.setShortcut(QKeySequence.SaveAs)
        a.triggered.connect(lambda: self.save_workspace_as())
        m.addAction(a)
        m.addSeparator()
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
        if key in self._input_roots:
            self._inputs_before_show[key] = self._snapshot(key)
        if key in STATE_TASKS or not args or not isinstance(args[0], dict):
            body = self.state.body()
        elif key == "decision":
            body = _case_body(args[0])
        else:
            body = args[0]
        self.note_result(key, body, res, from_runner=True)

    def note_result(self, key: str, body: dict, res=None, from_runner: bool = False) -> dict | None:
        """Record what a result ran on (project identity for its sections, per component project data or a local
        edit) on the result and in the page banner.  A page that computes inline calls this after showing the
        result, which also records the inputs it was computed from."""
        if not from_runner and key in self._input_roots:
            self._inputs_shown[key] = self._snapshot(key)
            self._check_inputs(TASK_PAGE.get(key))
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

    def task_label(self, key: str) -> str:
        return self.runner.labels.get(key) or TASK_LABELS.get(key, lambda: key)()

    def _refresh_banner(self, page: str):
        """One line above the page: which product data its results came from, and whether the project or the page's
        own inputs changed since (then the results on screen are older than what the page shows as input)."""
        from ..plots.labels import section_label
        ban = self.banners.get(page)
        uses = self.usages.get(page) or {}
        changed = self._changed.get(page) or {}
        if ban is None or not (uses or changed):
            return
        stale, local, parts = [], [], []
        for task, use in uses.items():
            st = stale_sections(use, self.state.project)
            if st:
                stale.append(f"{self.task_label(task)} ({', '.join(section_label(s) for s in st)})")
            if use.get("local_edits"):
                local.append(f"{self.task_label(task)}: {', '.join(section_label(s) for s in use['local_edits'])}")
            parts.append(self.task_label(task))
        lines = []
        if changed:
            items = []
            for task, diffs in changed.items():
                shown = "; ".join(f"{f} {a} → {b}" for f, a, b in diffs[:3])
                if len(diffs) > 3:
                    shown += tr(f" 외 {len(diffs) - 3}개", f" and {len(diffs) - 3} more")
                items.append(f"<b>{self.task_label(task)}</b> ({shown})")
            lines.append(tr("⚠ <b>입력이 바뀜</b> — 화면의 결과는 바뀌기 전 입력으로 계산됐습니다: ",
                            "⚠ <b>inputs changed</b> — the results on screen were computed from the earlier inputs: ")
                         + "; ".join(items) + tr(" · 다시 실행: Ctrl+Enter", " · run again: Ctrl+Enter"))
        first = next(iter(uses.values()), None)
        if stale:
            lines.append(tr(f"⚠ <b>프로젝트 데이터가 바뀜</b> — 결과 계산 뒤 바뀐 데이터: {'; '.join(stale)}. 다시 계산하세요",
                            f"⚠ <b>project data changed</b> after these results: {'; '.join(stale)}. Recompute"))
        elif local and first:
            lines.append(tr(f"프로젝트 {first['project_label']}의 제품 데이터 + <b>이 페이지의 로컬 변경</b> "
                            f"({'; '.join(local)})",
                            f"product data of project {first['project_label']} + <b>local edits on this page</b> "
                            f"({'; '.join(local)})"))
        elif first and not changed:
            lines.append(tr(f"결과({', '.join(parts)})는 프로젝트 {first['project_label']}의 제품 데이터로 계산됨",
                            f"results ({', '.join(parts)}) computed from the product data of project "
                            f"{first['project_label']}"))
        state = "stale" if stale else "inputs" if changed else "local" if local else "info"
        ban.setProperty("state", state)
        ban.style().unpolish(ban)
        ban.style().polish(ban)
        ban.setText("<br>".join(lines))
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
        page.start(Path(path).name, case, [])

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

    # ------------------------------------------------------------------ session: window size, position, last page
    def _session_kept(self) -> bool:
        """The self-test always starts from the same window (deterministic captures); a user session is kept."""
        return not QApplication.instance().property("twb_selftest")

    def _restore_session(self) -> None:
        if not self._session_kept():
            return
        geo = self.settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        page = self.settings.value("window/page")
        if isinstance(page, str) and page in self._nav_rows:
            self.show_page(page)

    def _save_session(self) -> None:
        if not self._session_kept():
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/page", self.current_page())

    # ------------------------------------------------------------------ workspace: inputs, page data and project
    def _init_workspace(self) -> None:
        """A workspace file (open / save) and, for a person's session, a recovery copy every minute and at the end."""
        from PySide6.QtCore import QTimer
        from . import workspace as WS
        self._ws_path: str | None = None
        self._ws_saved = None                   # content of the file as last saved or opened
        self._ws_baseline = WS.content(WS.build(self))      # a fresh window: nothing to keep
        self._ws_autosaved = None
        self._ws_discard = False
        self._ws_timer = QTimer(self)
        self._ws_timer.setSingleShot(True)
        self._ws_timer.setInterval(400)
        self._ws_timer.timeout.connect(self._refresh_title)
        for key in WS.pages_with_inputs(self):
            from .inputs import connect_changes
            for r in WS.input_roots(self, key):
                connect_changes(r, self._ws_timer.start)
        self.state.project_changed.connect(self._ws_timer.start)
        if self._session_kept():
            self._autosave_timer = QTimer(self)
            self._autosave_timer.setInterval(60_000)
            self._autosave_timer.timeout.connect(self._autosave)
            self._autosave_timer.start()
        self._refresh_title()

    def _refresh_title(self) -> None:
        from pathlib import Path
        name = Path(self._ws_path).name if self._ws_path else tr("새 작업 공간", "new workspace")
        self.setWindowTitle(f"Traction Workbench {__version__} — {name}[*]")
        self.setWindowModified(self.workspace_dirty())

    def workspace_dirty(self) -> bool:
        """Inputs, page data or project differ from the workspace file (or, without a file, from a fresh window)."""
        from . import workspace as WS
        ref = self._ws_saved if self._ws_path else self._ws_baseline
        return WS.content(WS.build(self)) != ref

    def save_workspace(self, path: str | None = None) -> bool:
        from . import workspace as WS
        path = path or self._ws_path
        if not path:
            return self.save_workspace_as()
        ws = WS.build(self)
        try:
            WS.save(ws, path)
        except OSError as exc:
            error_box(self, tr("작업 공간 저장 실패", "workspace not saved"), str(exc))
            return False
        self._ws_path, self._ws_saved = str(path), WS.content(ws)
        self._refresh_title()
        self.statusBar().showMessage(tr(f"작업 공간 저장: {path}", f"workspace saved: {path}"), 6000)
        return True

    def save_workspace_as(self) -> bool:
        from PySide6.QtWidgets import QFileDialog
        from . import workspace as WS
        path, _ = QFileDialog.getSaveFileName(self, tr("작업 공간 저장", "save workspace"),
                                              self._ws_path or ("work" + WS.SUFFIX),
                                              tr("작업 공간", "workspace") + f" (*{WS.SUFFIX})")
        if not path:
            return False
        if not path.endswith(WS.SUFFIX):
            path += WS.SUFFIX
        return self.save_workspace(path)

    def open_workspace(self, path: str | None = None) -> bool:
        from PySide6.QtWidgets import QFileDialog
        from . import workspace as WS
        if not self._confirm_discard():
            return False
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, tr("작업 공간 열기", "open workspace"), "",
                                                  tr("작업 공간", "workspace") + f" (*{WS.SUFFIX});;JSON (*.json)")
            if not path:
                return False
        try:
            ws = WS.load(path)
            problems = WS.apply(self, ws)
        except (OSError, ValueError) as exc:
            error_box(self, tr("작업 공간 열기 실패", "workspace not opened"), str(exc))
            return False
        self._ws_path, self._ws_saved = str(path), WS.content(ws)
        self._refresh_title()
        self._report_restore(problems, path)
        return True

    def _report_restore(self, problems: list, where) -> None:
        """What was restored and what was not (never guessed): the status bar, or a list when something is left."""
        from PySide6.QtWidgets import QApplication, QMessageBox
        if not problems:
            self.statusBar().showMessage(tr(f"작업 공간을 복원했습니다: {where} — 결과는 다시 실행하면 계산됩니다 (Ctrl+Enter)",
                                            f"workspace restored: {where} - results are computed when you run "
                                            f"(Ctrl+Enter)"), 10000)
            return
        text = "\n".join(f"· {p}" for p in problems[:30]) + (tr(f"\n… 외 {len(problems) - 30}개", f"\n… and "
                                                                  f"{len(problems) - 30} more")
                                                               if len(problems) > 30 else "")
        if QApplication.instance().property("twb_selftest"):
            error_box(self, tr("작업 공간 복원", "workspace restore"), text)
            return
        QMessageBox.information(self, tr("작업 공간 복원", "workspace restore"),
                                tr("나머지는 복원했습니다. 다음 항목은 저장된 값을 쓸 수 없어 그대로 두었습니다:\n\n",
                                   "Everything else is restored. These saved values could not be used and were left "
                                   "unchanged:\n\n") + text)

    def _confirm_discard(self) -> bool:
        """Before the work on screen is replaced or the window closes: a workspace file with unsaved changes is
        saved, discarded or kept open (cancel).  Work without a file is kept for recovery instead of asking."""
        if not self._session_kept() or not self._ws_path or not self.workspace_dirty():
            return True
        from pathlib import Path
        a = ask(self, tr("저장하지 않은 변경", "unsaved changes"),
                tr(f"작업 공간 '{Path(self._ws_path).name}'에 저장하지 않은 변경이 있습니다.",
                   f"The workspace '{Path(self._ws_path).name}' has unsaved changes."),
                [("save", tr("저장", "Save")), ("discard", tr("저장하지 않음", "Don't save")),
                 ("cancel", tr("취소", "Cancel"))])
        if a == "save":
            return self.save_workspace()
        if a == "discard":
            self._ws_discard = True
            return True
        return False

    def confirm_close(self) -> bool:
        return self._confirm_discard()

    def _autosave(self) -> None:
        """Every minute: the work of this session, for recovery after a crash (only when it changed)."""
        from . import workspace as WS
        ws = WS.build(self)
        c = WS.content(ws)
        if c == self._ws_autosaved or c == self._ws_baseline:
            return
        try:
            WS.save(ws, WS.recovery_path())
            self._ws_autosaved = c
        except OSError:
            pass

    def _write_recovery(self) -> None:
        """At the end: keep the session for recovery unless it holds nothing new (fresh, saved, or discarded)."""
        if not self._session_kept():
            return
        from . import workspace as WS
        try:
            p = WS.recovery_path()
            ws = WS.build(self)
            c = WS.content(ws)
            if self._ws_discard or c == self._ws_baseline or (self._ws_path and c == self._ws_saved):
                if p.exists():
                    p.unlink()
                return
            WS.save(ws, p)
        except OSError:
            pass

    def offer_recovery(self) -> None:
        """At start: the last session's work, when it was not saved, is offered back (declined: kept aside)."""
        if not self._session_kept() or getattr(self, "_recovery_offered", False):
            return
        self._recovery_offered = True
        from . import workspace as WS
        p = WS.recovery_path()
        if not p.is_file():
            return
        try:
            ws = WS.load(p)
        except (OSError, ValueError):
            p.replace(p.with_suffix(".broken.json"))
            return
        if WS.content(ws) == self._ws_baseline:
            p.unlink()
            return
        labels = {k: lab() for k, lab, _cls in PAGES}
        fresh = self._ws_baseline["pages"]
        changed = [labels.get(k, k) for k, v in ws["pages"].items()
                   if {"fields": v.get("fields"), "data": v.get("data")} != fresh.get(k)]
        when = ws.get("saved_at", "")
        a = ask(self, tr("이전 작업 복원", "restore previous work"),
                tr(f"저장하지 않은 이전 작업이 있습니다 ({when}).\n바뀐 입력: {', '.join(changed) or '프로젝트'}\n\n"
                   f"복원할까요? 새로 시작하면 이 작업은 {p.with_suffix('.previous.json').name}로 남겨 둡니다.",
                   f"There is unsaved work from the last session ({when}).\nChanged inputs: "
                   f"{', '.join(changed) or 'project'}\n\nRestore it? Starting fresh keeps it as "
                   f"{p.with_suffix('.previous.json').name}."),
                [("restore", tr("복원", "Restore")), ("fresh", tr("새로 시작", "Start fresh"))])
        if a == "restore":
            problems = WS.apply(self, ws)
            self._refresh_title()
            self._report_restore(problems, tr("이전 세션", "the last session"))
        else:
            p.replace(p.with_suffix(".previous.json"))

    def closeEvent(self, ev):
        if not self.confirm_close():
            ev.ignore()
            return
        self._write_recovery()
        self._save_session()
        self.runner.cancel_all()
        super().closeEvent(ev)
