"""Fault simulation & functional safety: one causal trajectory per scenario (fault -> measurement -> control and
monitoring -> reaction -> actual bridge -> truth -> SG / FSR / TSR verdicts), the reaction candidates from the same
initial condition, campaigns with failure boundaries and re-runnable counterexamples, the plant's validation evidence
and the declared dependencies of the protection architecture."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from ... import api
from ...errors import InputValidationError
from ...i18n import tr
from ...insight.fault import (axis_word, campaign_insight, check_word, compare_insight, fault_insight, fault_word,
                              finding_word, react, safety_case_insight, strategy_log_text, validation_insight,
                              verdict_ko)
from ...plots import fault_figures as FF
from ..fault_design import KIND_KO, DesignEditor, VariantField, strategy_summary
from ..fault_editor import AxisEditor, FaultEditor, axis_catalog
from ..widgets import (Cell, ConceptNote, KeyValueTable, PlotPanel, check, combo, confirm, error_box, hint, integer,
                       number, primary_button, reading_tab, with_reading)

VCOLOR = {"PASS": "#1a7f37", "FAIL": "#cf222e", "UNKNOWN": "#b7791f", "NOT_APPLICABLE": "#8c959f",
          "OK": "#1a7f37", "INCONSISTENT": "#cf222e", "MISSING": "#cf222e", "WARNING": "#b7791f", "NOTE": "#57606a"}
OVERRIDES = ("", "asc_low", "asc_high", "six_switch_off", "torque_zero")
CLASSIC = ("policy", "none", "asc_low", "asc_high", "six_switch_off", "torque_zero")
FINDING_KO = {"OK": "OK", "INCONSISTENT": "모순", "MISSING": "누락", "WARNING": "경고", "NOTE": "참고"}

NOTE = lambda: tr(  # noqa: E731
    "<b>인과 시뮬레이션</b>: 고장 → 센서 측정(오차·고착·지연·소실) → 제어기(FOC)와 감시 메커니즘(측정만 봄) → 반응 경로(지연·"
    "필요 자원) → 브리지 명령 → <b>실제</b> 브리지(소자 상태·게이트 전원·desat 래치가 결정) → 모터·DC-link·배터리의 <b>참값</b> → "
    "요구 판정(참값 기준). ASC 명령이 곧 안전 상태가 아니며, 6SO는 정류 개시 속도 위에서 전류·에너지를 계속 흘립니다. "
    "FDTI/FRTI/FHTI는 한 궤적의 사건(t_F 고장, t_D 할당된 메커니즘의 검출, t_S 안전 조건 도달·유지)으로 잽니다.<br>"
    "판정 범위는 <b>이 시나리오 하나</b>입니다. 탐색 집합의 통과는 캠페인이, 연속 운전영역의 보장은 표본으로는 주장하지 않습니다. "
    "인버터 수준 근거이며 차량 안전성·ISO 26262 적합성 승인이 아닙니다.",
    "<b>Causal simulation</b>: fault → sensor measurement (error, stuck, delay, loss) → controller (FOC) and safety "
    "mechanisms (measurements only) → reaction paths (delay, resources) → bridge command → the <b>actual</b> bridge "
    "(device health, gate supplies, desat latches decide) → the <b>truth</b> of machine, DC link and battery → "
    "requirement verdicts (on the truth). An ASC command is not a safe state, and six-switch-off keeps current and "
    "energy flowing above the rectification onset. FDTI / FRTI / FHTI are measured on one trajectory (t_F fault, t_D "
    "detection by an allocated mechanism, t_S safe condition reached and held).<br>The scope is <b>this one "
    "scenario</b>: a campaign covers an explored set; a continuous region is never claimed from samples. Inverter-level "
    "evidence, not a vehicle safety or ISO 26262 approval.")


def _sim_task(progress, body, project):
    progress(0.05, tr("인과 시뮬레이션", "causal simulation"))
    return api.fault_sim(body, project)


def _cmp_task(progress, body, project):
    progress(0.02, tr("반응 후보 비교", "reaction candidates"))
    return api.fault_compare(body, project)


def _camp_task(progress, body, project):
    progress(0.01, tr("캠페인", "campaign"))
    return api.fault_campaign(body, project)


def _rerun_task(progress, body, project):
    progress(0.1, tr("반례 재실행", "counterexample re-run"))
    return api.fault_rerun(body, project)


def _val_task(progress, body, project):
    progress(0.02, tr("플랜트 검증", "plant validation"))
    return api.fault_validation(body, project)


def _review_task(progress, body, project):
    progress(0.1, tr("정적 설계 검토", "static design review"))
    return api.fault_review(body, project)


def _verif_task(progress, body, project):
    progress(0.01, tr("검증 매트릭스", "verification matrix"))
    return api.fault_verification(body, project)


class FaultSimPage(QWidget):
    workspace_data = ("counterexamples",)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last = self.last_cmp = self.last_camp = self.last_val = None
        self.counterexamples: list = []
        self.scenarios = api.fault_scenarios()
        self.design = api.fault_design(None, win.state.project)
        self.last_review = self.last_verif = None
        self._syncing = False
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("대표 시나리오 (합성 예제)", "representative scenarios (synthetic example)"))
        f = QFormLayout(g)
        self.preset = combo([(tr(f"[{s['category']}] ", f"[{s['category']}] ") + s["title"][tr("ko", "en")], s["key"])
                             for s in self.scenarios])
        self.preset_hint = hint("")
        self.preset_hint.setWordWrap(True)
        self.load_btn = QPushButton(tr("시나리오 불러오기", "load scenario"))
        self.load_btn.clicked.connect(self._load_preset)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        f.addRow(self.preset)
        f.addRow(self.preset_hint)
        f.addRow(self.load_btn)
        v.addWidget(g)
        g = QGroupBox(tr("운전점·요청", "operating point · request"))
        f = QFormLayout(g)
        self.speed = number(12000, -20000, 20000, "rpm", 0, 500)
        self.req_kind = combo([(tr("일정", "constant"), "constant"), (tr("스텝", "step"), "step"), (tr("램프", "ramp"), "ramp")])
        self.T0 = number(150, -1000, 1000, "N·m", 1, 10)
        self.T1 = number(0, -1000, 1000, "N·m", 1, 10)
        self.t0 = number(20, 0, 1e4, "ms", 2, 1)
        self.t1 = number(30, 0, 1e4, "ms", 2, 1)
        self.horizon = number(60, 1, 2000, "ms", 1, 10)
        self.J = number(0, 0, 100, "kg·m²", 3, 0.01, tip=tr("0: 속도는 부하(차량 관성)가 유지 · 값: 이 관성으로 속도가 변함(예: 클러치 "
                                                          "분리된 모터)", "0: the load (vehicle inertia) holds the "
                                                                        "speed · a value: the speed follows this "
                                                                        "inertia (e.g. a decoupled motor)"))
        self.theta0 = number(0, -360, 360, "°", 1, 15, tip=tr("t = 0의 전기각 (고장 시점의 각도를 바꿉니다)",
                                                             "electrical angle at t = 0 (moves the angle at the fault)"))
        self.pwm = combo([(tr("평균 PWM (빠름)", "averaged PWM (fast)"), "averaged"),
                          (tr("스위칭 PWM (리플·데드타임 순간값)", "switched PWM (ripple, dead-time instants)"), "switched")])
        self.hmax = number(10, 0.5, 50, "µs", 1, 1)
        for lab, w in ((tr("속도", "speed"), self.speed), (tr("요청 형태", "request"), self.req_kind),
                       (tr("요청 T0", "request T0"), self.T0), (tr("요청 T1 (스텝·램프)", "request T1 (step, ramp)"), self.T1),
                       (tr("변화 시작 t0", "change at t0"), self.t0), (tr("램프 끝 t1", "ramp end t1"), self.t1),
                       (tr("시뮬레이션 길이", "horizon"), self.horizon),
                       (tr("관성 J (0 = 속도 유지)", "inertia J (0 = speed held)"), self.J),
                       (tr("초기 전기각", "initial angle"), self.theta0),
                       (tr("PWM 모델", "PWM model"), self.pwm), (tr("최대 적분 스텝", "max step"), self.hmax)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("고장 (주 고장 + 잠재 고장 조합)", "faults (primary + latent combinations)"))
        gl = QVBoxLayout(g)
        self.faults = FaultEditor()          # a list and the form of the selected fault (no typed parameter text)
        gl.addWidget(self.faults)
        v.addWidget(g)
        g = QGroupBox(tr("보호·반응·설계 변형", "protection · reaction · design variants"))
        f = QFormLayout(g)
        # a short label and the explanation below it: the one-line label held the input panel at 655 px
        self.protect = check(tr("보호 반응 적용", "protection reactions on"), True)
        protect_hint = hint(tr("끄면 비교용 궤적입니다. 게이트 드라이버 자체 desat 차단은 남습니다.",
                               "off: the comparison trajectory; the gate drivers' own desaturation turn-off stays."))
        protect_hint.setWordWrap(True)
        # the design variant (the difference to the project's fault_sim data, edited in the editor tab) - created
        # before the forced-reaction choice: a restored workspace puts the variant (its strategies) back first
        self.design_variant = VariantField()
        self.design_variant.restore_hook = self._restore_variant
        self.override = combo([(tr("프로젝트 정책", "project policy"), "")] + [(react(x), x) for x in OVERRIDES[1:]])
        self.override.setToolTip(tr("이 실행에서 모든 반응 요청을 이 반응(기본 반응 또는 선언된 전략)으로 바꿉니다 — 후보 비교용",
                                    "every reaction request of this run becomes this reaction (a primitive one or a "
                                    "declared strategy) — for comparing candidates"))
        self.edit_design_btn = QPushButton(tr("보호 설계·안전 요구 편집 →", "edit protection design and requirements →"))
        self.edit_design_btn.clicked.connect(lambda: self.top.setCurrentWidget(self.editor))
        vbox = QVBoxLayout()
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(self.design_variant)
        vbox.addWidget(self.edit_design_btn)
        f.addRow(self.protect)
        f.addRow(protect_hint)
        f.addRow(tr("설계 변형", "design variant"), vbox)
        f.addRow(tr("반응 강제 (후보)", "forced reaction (candidate)"), self.override)
        v.addWidget(g)
        self.run_btn = primary_button(tr("시뮬레이션 실행", "run simulation"))
        self.run_btn.clicked.connect(self.run)
        v.addWidget(self.run_btn)
        row = QHBoxLayout()
        self.cmp_btn = QPushButton(tr("보호 적용/미적용·반응 후보 비교", "compare protection on/off and reactions"))
        self.cmp_btn.clicked.connect(self.run_compare)
        row.addWidget(self.cmp_btn)
        v.addLayout(row)
        row = QHBoxLayout()
        self.save_sc = QPushButton(tr("시나리오 저장", "save scenario"))
        self.open_sc = QPushButton(tr("시나리오 열기", "open scenario"))
        self.export_btn = QPushButton(tr("결과 JSON 저장", "save result JSON"))
        self.save_sc.clicked.connect(self._save_scenario)
        self.open_sc.clicked.connect(self._open_scenario)
        self.export_btn.clicked.connect(self._export)
        self.export_btn.setEnabled(False)
        for b in (self.save_sc, self.open_sc, self.export_btn):
            row.addWidget(b)
        v.addLayout(row)
        v.addWidget(ConceptNote(NOTE()))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(380)
        split.addWidget(sc)
        # ---- results
        self.tabs = QTabWidget()
        self.p_wave = PlotPanel(hint=tr("실행하면 참값·측정·추정·명령·실제 브리지가 같은 시간축에 표시됩니다.",
                                        "Run to see truth, measurement, estimate, command and actual bridge on one "
                                        "time axis."), min_height=520)
        self.tabs.addTab(self.p_wave, tr("동기 파형", "synchronized waveforms"))
        w = QWidget()
        lz = QVBoxLayout(w)
        lz.setContentsMargins(0, 0, 0, 0)
        self.p_zoom = PlotPanel(hint=tr("실행하면 고장 전후 과도 구간이 시뮬레이션 샘플 단위로 확대되어 극값의 값과 시각이 표시됩니다.",
                                        "Run to see the transient around the fault zoomed to the simulated samples, "
                                        "each extreme with its value and instant."), min_height=460)
        self.t_trans = KeyValueTable(headers=[tr("양", "quantity"), tr("고장 전", "pre-fault"),
                                              tr("고장 후 극값", "extreme"), tr("시각 [ms]", "at [ms]"),
                                              tr("고장 후 [ms]", "t − fault [ms]"),
                                              tr("반응 후 [ms]", "t − reaction [ms]"), tr("최대 / 최소", "max / min"),
                                              tr("한계 밖 [ms]", "outside limit [ms]"),
                                              tr("정착 [ms]", "settling [ms]"), tr("샘플 간격 [µs]", "step [µs]")])
        self.t_trans.setToolTip(tr(
            "극값은 시뮬레이션 샘플 그대로입니다(보간 없음). 샘플 간격 = 그 시각 주변의 계산 해상도. 정착 = 최종값(마지막 10 % 구간의 "
            "중앙값) 둘레 편차의 5 % 안에 머물기 시작한 시각(상전류는 max(|i_a|,|i_b|,|i_c|) 포락선, 6펄스 리플 15 % 허용).",
            "Extremes are simulated samples (no interpolation). Step = the sample spacing around that instant (the "
            "resolution the value was computed with). "
            "Settling = from when the quantity stays within 5 % of its excursion around the final value (the median of "
            "the last tenth); the phase currents by the envelope max(|i_a|, |i_b|, |i_c|) with 15 % for its six-pulse "
            "ripple."))
        lz.addWidget(self.p_zoom, 3)
        lz.addWidget(self.t_trans, 1)
        self.tabs.addTab(w, tr("과도 확대", "transient zoom"))
        self.tab_zoom = w
        w = QWidget()
        l2 = QVBoxLayout(w)
        l2.setContentsMargins(0, 0, 0, 0)
        self.p_time = PlotPanel(min_height=360)
        self.t_events = KeyValueTable(headers=[tr("시각 [ms]", "time [ms]"), tr("종류", "kind"), tr("출처", "source"),
                                               tr("내용", "text")])
        l2.addWidget(self.p_time, 3)
        l2.addWidget(self.t_events, 2)
        self.tabs.addTab(w, tr("사건 타임라인", "event timeline"))
        self.tab_timeline = w
        w = QWidget()
        l3 = QVBoxLayout(w)
        l3.setContentsMargins(0, 0, 0, 0)
        self.t_req = KeyValueTable(headers=["ID", tr("추적 (FSR → SG)", "trace (FSR → SG)"), tr("판정", "verdict"),
                                            tr("요구", "requirement"), tr("근거", "evidence")])
        self.t_req.itemSelectionChanged.connect(self._req_selected)
        self.req_detail = QLabel(tr("요구를 고르면 근거(물리량·창·시간 원점·유지 조건·측정값)가 여기에 표시됩니다.",
                                    "Select a requirement to see its evidence (quantity, window, time origin, hold, "
                                    "measured values)."))
        self.req_detail.setWordWrap(True)
        self.req_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l3.addWidget(self.t_req, 3)
        l3.addWidget(self.req_detail, 1)
        self.tabs.addTab(w, tr("요구 판정 (SG/FSR/TSR)", "requirement verdicts (SG/FSR/TSR)"))
        self.tab_req = w
        w = QWidget()
        l4 = QVBoxLayout(w)
        l4.setContentsMargins(0, 0, 0, 0)
        l4.addWidget(QLabel(tr("비교할 후보 (보호 적용/미적용, 기본 반응, 선언된 반응 전략 — 소프트 ASC 등):",
                               "candidates (protection on / off, primitive reactions, the declared strategies - soft "
                               "ASC and others):")))
        self.cand_list = QListWidget()
        self.cand_list.setFlow(QListWidget.LeftToRight)
        self.cand_list.setWrapping(True)
        self.cand_list.setFixedHeight(64)
        self.cand_list.setProperty("twb_not_input", True)
        l4.addWidget(self.cand_list)
        self.p_cmp = PlotPanel(hint=tr("'반응 후보 비교'를 누르면 같은 초기 조건·같은 고장에서 고른 후보(보호 적용/미적용, ASC-low·ASC-high·"
                                       "6SO·토크 0, 반응 전략)를 비교합니다.", "Compare runs the same initial condition "
                                                                       "and fault with the chosen candidates "
                                                                       "(protection on / off, ASC-low, ASC-high, 6SO, "
                                                                       "zero torque, strategies)."), min_height=420)
        self.t_cmp = KeyValueTable(headers=[tr("후보", "candidate"), tr("종합", "overall"), tr("위반 요구", "violated"),
                                            tr("최대 |i| [A]", "peak |i| [A]"), tr("최저 i_d [A]", "min i_d [A]"),
                                            tr("최대 제동 토크 [N·m]", "peak braking torque [N·m]"),
                                            tr("최대 V_dc [V]", "max V_dc [V]"), "FHTI [ms]",
                                            tr("실제 브리지", "actual bridge")])
        l4.addWidget(self.p_cmp, 3)
        l4.addWidget(self.t_cmp, 1)
        self.cmp_view, self.i_cmp = with_reading(w, tr("비교를 실행하면 후보별 해석이 표시됩니다.", "Run the comparison to read "
                                                                                     "it candidate by candidate."))
        self.tabs.addTab(self.cmp_view, tr("반응 후보 비교", "reaction candidates"))
        self.tab_camp = self._campaign_tab()
        self.tab_val = self._validation_tab()
        self.tab_dep = self._dependency_tab()
        self.tabs.addTab(self.tab_camp, tr("캠페인·반례", "campaign · counterexamples"))
        self.tabs.addTab(self.tab_val, tr("검증 근거", "validation evidence"))
        self.tabs.addTab(self.tab_dep, tr("의존성·공통 원인", "dependencies · common cause"))
        self.insight = reading_tab(self.tabs, tr(
            "실행하면 해석이 표시됩니다 — 인과 사슬(고장 → 검출 → 반응 → 실제 브리지 → 결과), 요구별 판정과 근거, FDTI/FRTI/FHTI, "
            "어디서 실패했고 무엇을 바꾸면 되는지, 모델 정밀도와 적용 범위.",
            "Run to read the result — the causal chain (fault → detection → reaction → actual bridge → result), each "
            "requirement with its evidence, FDTI / FRTI / FHTI, where it failed and what would change it, model "
            "precision and scope."))
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 1100])
        self.top = QTabWidget()
        self.top.addTab(split, tr("시뮬레이션", "simulation"))
        self.editor = DesignEditor(self.design["schema"])
        self.editor.load(self.design["base"])
        self.editor.changed.connect(self._design_changed)
        self.editor.apply_requested.connect(self._apply_design)
        self.top.addTab(self.editor, tr("보호 설계·안전 요구 (편집)", "protection design · safety requirements (edit)"))
        self.case_tab = self._case_tab()
        self.top.addTab(self.case_tab, tr("안전 근거 (심사)", "safety case (review)"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.top)
        self.win.track_inputs(("fault_sim", "fault_compare"), form)
        self._refresh_candidates()
        self._preset_changed()
        self._load_preset()
        self._design_changed()

    # ------------------------------------------------------------------ campaign / validation / dependency tabs
    def _campaign_tab(self):
        w = QSplitter(Qt.Horizontal)              # the axes panel can be widened (long quantity names)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        g = QGroupBox(tr("축 (현재 시나리오가 기준)", "axes (the current scenario is the base)"))
        gl = QVBoxLayout(g)
        self.axes_why = hint("")
        self.axes_why.setWordWrap(True)
        gl.addWidget(self.axes_why)
        # the axes are chosen from what this scenario and design can vary (no typed paths)
        self.axes = AxisEditor(lambda: axis_catalog(self.scenario(), self.editor.work,
                                                    self.design["schema"].get("kind_params")))
        gl.addWidget(self.axes)
        lv.addWidget(g)
        g = QGroupBox(tr("방식", "mode"))
        f = QFormLayout(g)
        self.cmode = combo([(tr("격자 (모든 조합)", "grid (every combination)"), "grid"),
                            (tr("무작위 (시드 고정)", "random (seeded)"), "random")])
        self.cn = integer(20, 2, 400)
        self.cseed = integer(1, 0, 10 ** 6)
        self.cref = integer(2, 0, 8, tip=tr("판정이 바뀌는 이웃 격자점 사이를 축을 따라 이분 탐색하는 횟수",
                                            "bisection steps along an axis between neighbouring grid points whose "
                                            "verdicts differ"))
        f.addRow(tr("방식", "mode"), self.cmode)
        f.addRow(tr("무작위 실행 수", "random runs"), self.cn)
        f.addRow(tr("시드", "seed"), self.cseed)
        f.addRow(tr("경계 이분 탐색", "boundary bisection"), self.cref)
        lv.addWidget(g)
        self.camp_btn = primary_button(tr("캠페인 실행", "run campaign"))
        self.camp_btn.clicked.connect(self.run_campaign)
        lv.addWidget(self.camp_btn)
        g = QGroupBox(tr("반례 (작업 공간에 저장)", "counterexamples (kept in the workspace)"))
        gl = QVBoxLayout(g)
        self.t_cx = KeyValueTable(headers=["ID", tr("실패 요구", "failing"), tr("만든 시점", "created"), tr("상태", "state")])
        self.t_cx.setProperty("twb_not_input", True)          # shows ``counterexamples`` (workspace data), not an input
        gl.addWidget(self.t_cx)
        row = QGridLayout()                        # two by two: one row of four held the panel 420 px wide
        self.cx_rerun = QPushButton(tr("선택 재실행", "re-run selected"))
        self.cx_load = QPushButton(tr("시나리오로 불러오기", "load as scenario"))
        self.cx_save = QPushButton(tr("파일로 저장", "save to file"))
        self.cx_open = QPushButton(tr("파일 열기", "open file"))
        self.cx_rerun.clicked.connect(self.rerun_selected)
        self.cx_load.clicked.connect(self._cx_to_scenario)
        self.cx_save.clicked.connect(self._cx_save)
        self.cx_open.clicked.connect(self._cx_open)
        for k, b in enumerate((self.cx_rerun, self.cx_load, self.cx_save, self.cx_open)):
            row.addWidget(b, k // 2, k % 2)
        gl.addLayout(row)
        self.cx_state = QLabel("")
        self.cx_state.setWordWrap(True)
        gl.addWidget(self.cx_state)
        lv.addWidget(g)
        lv.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(left)
        sc.setMinimumWidth(300)                   # narrower than its content when the window is: it scrolls, the
        sc.setProperty("twb_free_width", True)    # results stay whole (the splitter gives it its full width)
        w.addWidget(sc)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.p_camp = PlotPanel(hint=tr("캠페인을 실행하면 실행별 판정·실패 경계·최악값(각각 한 실행)이 표시됩니다.",
                                        "Run a campaign to see the verdict per run, the failure boundaries and the "
                                        "worst values (each from one run)."), min_height=360)
        self.t_runs = KeyValueTable(headers=["#", tr("점", "point"), tr("종합", "overall"), tr("최대 |i| [A]", "peak |i| [A]"),
                                             tr("최대 V_dc [V]", "max V_dc [V]"), "FHTI [ms]", tr("실패 요구", "failing")])
        rv.addWidget(self.p_camp, 3)
        rv.addWidget(self.t_runs, 2)
        view, self.i_camp = with_reading(right, tr("캠페인을 실행하면 경계·최악값·민감도·반례 해석이 표시됩니다.",
                                                   "Run a campaign to read boundaries, worst values, sensitivity and "
                                                   "counterexamples."))
        w.addWidget(view)
        w.setStretchFactor(1, 1)
        w.setSizes([460, 700])
        self.win.track_inputs("fault_campaign", left)
        return w

    def _validation_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.val_full = check(tr("전체 (2단계 이상화 기준, ~2분)", "full (two reference idealisations, ~2 min)"), False)
        self.val_btn = primary_button(tr("플랜트 검증 실행", "run plant validation"))
        self.val_btn.clicked.connect(self.run_validation)
        row.addWidget(self.val_full)
        row.addWidget(self.val_btn)
        row.addStretch(1)
        v.addWidget(bar)
        self.p_val = PlotPanel(hint=tr("폐형식(ASC 정상상태·정류 개시), 독립 abc 정식화(전도 소자 모델, Radau), 에너지 수지, 스텝 수렴과 "
                                       "비교합니다. 같은 방정식을 다른 적분기로 푼 비교는 수치 검증으로만 표시합니다.",
                                       "Compares with closed forms (steady ASC, rectification onset), an independent abc "
                                       "formulation (conductance devices, Radau), the energy balance and step "
                                       "convergence. A same-equation comparison is labelled as a numerics check."),
                               min_height=380)
        self.t_val = KeyValueTable(headers=[tr("사례", "case"), tr("물리량", "quantity"), tr("플랜트", "plant"),
                                            tr("참조", "reference"), "|Δ|", tr("허용", "tol."), tr("참조 종류", "reference")])
        v.addWidget(self.p_val, 3)
        v.addWidget(self.t_val, 2)
        view, self.i_val = with_reading(w, tr("검증을 실행하면 해석이 표시됩니다.", "Run the validation to read it."))
        self.win.track_inputs("fault_validation", bar)
        return view

    def _dependency_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.dep_btn = QPushButton(tr("선언된 의존성 분석", "analyse declared dependencies"))
        self.dep_btn.clicked.connect(self.show_dependencies)
        row.addWidget(self.dep_btn)
        row.addStretch(1)
        v.addLayout(row)
        self.p_dep = PlotPanel(min_height=320, hint=tr("프로젝트의 fault_sim 데이터에서 메커니즘·경로·센서가 어떤 자원(전원·MCU·"
                                                       "변환기)을 공유하는지, FSR마다 한 자원이 할당된 메커니즘을 모두 없애는지 봅니다.",
                                                       "From the project's fault_sim data: which resources (supplies, MCU, "
                                                       "converters) mechanisms, paths and sensors share, and per FSR "
                                                       "whether one resource removes every allocated mechanism."))
        self.t_dep = KeyValueTable(headers=["FSR", tr("할당 메커니즘", "allocated"), tr("단일 원인 자원", "single-point "
                                                                                             "resources"),
                                            tr("판단", "statement")])
        v.addWidget(self.p_dep, 3)
        v.addWidget(self.t_dep, 1)
        return w

    def _case_tab(self):
        """The assessor's view of the design under study (the project with the editor's variant): the static review,
        the traceability and latency bounds, the verification matrix over the scenario catalog, the simulation-based
        failure-mode table and the safety-case report."""
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.review_btn = primary_button(tr("정적 설계 검토", "static design review"))
        self.review_btn.setToolTip(tr("선언된 SG/FSR/TSR·메커니즘·경로·전략을 시뮬레이션 없이 점검 (ASIL 상속, 예산 vs FTTI, 검출 지연 "
                                      "상한 vs FDTI, 잠재 고장 시험, 실행 가능성, 공통 원인)",
                                      "checks the declared SG / FSR / TSR, mechanisms, paths and strategies without a "
                                      "simulation (ASIL inheritance, budgets vs FTTI, detection latency bounds vs FDTI, "
                                      "latent tests, executability, common causes)"))
        self.verif_btn = QPushButton(tr("검증 매트릭스 실행", "run the verification matrix"))
        self.verif_btn.setToolTip(tr("고른 대표 시나리오를 이 설계 하나로 실행해 요구 × 시나리오 판정, 커버리지, 최악 시간, 고장 모드 표를 "
                                     "만듭니다 (시나리오 자체의 설계 변형은 빼고 이 설계를 씀)",
                                     "runs the chosen catalog scenarios on this one design: requirement x scenario "
                                     "verdicts, coverage, worst times, failure-mode table (a scenario's own design "
                                     "variant is replaced by this design)"))
        self.report_btn = QPushButton(tr("안전 근거 보고서 저장 (HTML)…", "save the safety-case report (HTML)…"))
        self.review_btn.clicked.connect(self.run_review)
        self.verif_btn.clicked.connect(self.run_verification)
        self.report_btn.clicked.connect(self._save_report)
        for b in (self.review_btn, self.verif_btn, self.report_btn):
            row.addWidget(b)
        row.addStretch(1)
        v.addWidget(bar)
        v.addWidget(QLabel(tr("검증 매트릭스에 넣을 시나리오:", "scenarios in the verification matrix:")))
        self.verif_list = QListWidget()
        self.verif_list.setFlow(QListWidget.LeftToRight)
        self.verif_list.setWrapping(True)
        self.verif_list.setFixedHeight(84)
        self.verif_list.setProperty("twb_not_input", True)
        for sc_ in self.scenarios:
            it = QListWidgetItem(sc_["key"])
            it.setData(Qt.UserRole, sc_["key"])
            it.setToolTip(f"[{sc_['category']}] {sc_['title'][tr('ko', 'en')]}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.verif_list.addItem(it)
        v.addWidget(self.verif_list)
        self.case_state = QLabel("")
        self.case_state.setWordWrap(True)
        v.addWidget(self.case_state)
        self.case_tabs = QTabWidget()
        self.t_find = KeyValueTable(headers=[tr("상태", "status"), tr("요소", "element"), tr("점검", "check"),
                                             tr("내용", "detail")])
        self.case_tabs.addTab(self.t_find, tr("설계 검토 결과", "review findings"))
        tw = QSplitter(Qt.Vertical)
        self.t_trace = KeyValueTable(headers=["SG", "ASIL", "FTTI", "FSR", "ASIL", tr("할당 메커니즘", "mechanisms"),
                                              "TSR"])
        self.t_lat = KeyValueTable(headers=["FSR", tr("메커니즘", "mechanism"), tr("종류", "kind"), tr("경로", "path"),
                                            tr("검출 [ms] (최소…최대)", "detection [ms] (min…max)"),
                                            tr("경로 지연 [ms]", "path delay [ms]"),
                                            tr("FDTI 예산 [ms]", "FDTI budget [ms]"), tr("구성", "composed of")])
        tw.addWidget(self.t_trace)
        tw.addWidget(self.t_lat)
        self.case_tabs.addTab(tw, tr("추적성·검출 지연", "traceability · detection latency"))
        mw = QSplitter(Qt.Vertical)
        self.t_matrix = KeyValueTable(headers=[tr("요구", "requirement"), tr("발동", "exercised"), tr("실패", "fail")])
        self.t_timing = KeyValueTable(headers=["FSR", tr("최대 FDTI (시나리오)", "worst FDTI (scenario)"),
                                               tr("최대 FRTI (시나리오)", "worst FRTI (scenario)"),
                                               tr("최대 FHTI (시나리오)", "worst FHTI (scenario)")])
        mw.addWidget(self.t_matrix)
        mw.addWidget(self.t_timing)
        mw.setSizes([420, 160])
        self.case_tabs.addTab(mw, tr("검증 매트릭스", "verification matrix"))
        self.t_fmea = KeyValueTable(headers=[tr("시나리오", "scenario"), tr("고장", "fault"), tr("검출", "detection"),
                                             tr("반응", "reaction"), tr("최종 실제 브리지", "final actual bridge"),
                                             tr("최대 |i| [A]", "peak |i| [A]"), tr("최저 i_d [A]", "min i_d [A]"),
                                             tr("최대 제동 토크 [N·m]", "peak braking torque [N·m]"),
                                             tr("최대 V_dc [V]", "max V_dc [V]"), tr("실패 요구", "failing")])
        self.case_tabs.addTab(self.t_fmea, tr("고장 모드 (시뮬레이션 FMEA)", "failure modes (simulation FMEA)"))
        self.i_case = reading_tab(self.case_tabs, tr(
            "정적 설계 검토와 검증 매트릭스를 실행하면 심사 관점의 해석이 표시됩니다 — 모순·누락, 경고, 검출 지연 상한, 요구별 실패·미발동, "
            "FSR별 최악 시간, 검출되지 않은 고장.",
            "Run the static review and the verification matrix to read them as an assessor would — contradictions and "
            "gaps, warnings, detection latency bounds, requirements failing or never exercised, worst times per FSR, "
            "undetected faults."))
        v.addWidget(self.case_tabs, 1)
        v.addWidget(ConceptNote(tr(
            "<b>정적 검토</b>는 선언된 데이터끼리의 일관성(ASIL 상속, FDTI+FRTI ≤ FTTI, 큰 계단 고장의 검출 지연 상한 = 주기 + 디바운스 + "
            "센서 지연 ≤ FDTI 예산, 반응 경로 지연 + 전략의 시간 부분 ≤ FRTI 예산, 잠재 고장 시험, 선언된 공통 원인)을 봅니다. "
            "<b>검증 매트릭스</b>는 대표 시나리오를 이 설계 하나로 실행해 요구 × 시나리오 판정을 모읍니다 — PASS는 그 궤적들에 대한 진술, "
            "FAIL은 재현 가능한 반례입니다. 보고서는 두 결과와 설계 변형·요구·전략·공통 원인·한계를 한 HTML 파일로 묶습니다.",
            "The <b>static review</b> checks the declared data against each other (ASIL inheritance, FDTI + FRTI ≤ FTTI, "
            "the detection latency bound of a gross step fault = period + debounce + sensor delay ≤ the FDTI budget, "
            "path delay + the timed part of a strategy ≤ the FRTI budget, latent tests, declared common causes). The "
            "<b>verification matrix</b> runs the catalog scenarios on this one design and collects requirement x "
            "scenario verdicts — a PASS is a statement about those trajectories, a FAIL a reproducible "
            "counterexample. The report puts both results with the design variant, requirements, strategies, common "
            "causes and limits into one HTML file.")))
        self.win.track_inputs(("fault_review", "fault_verification"), self.design_variant)
        return w

    # ------------------------------------------------------------------ scenario <-> inputs
    def _preset_changed(self, *_):
        s = next((x for x in self.scenarios if x["key"] == self.preset.currentData()), None)
        self.preset_hint.setText(s["hint"][tr("ko", "en")] if s else "")

    def _load_preset(self, *_):
        s = next((x for x in self.scenarios if x["key"] == self.preset.currentData()), None)
        if s:
            self.set_scenario(s["scenario"], ask=True)
            self.set_axes((s.get("campaign") or {}).get("axes") or [], (s.get("campaign") or {}).get("why"))

    def set_axes(self, axes: list, why: dict | None = None):
        """The campaign axes (a scenario's suggestion or a counterexample's): path and values or range."""
        self.axes.set_axes(axes)
        self.axes_why.setText(tr("이 시나리오의 추천 캠페인: ", "suggested for this scenario: ") + why[tr("ko", "en")]
                              if why else "")

    def set_scenario(self, sc: dict, ask: bool = False):
        """The scenario into the inputs.  Its design variant replaces the editor's; with ``ask``, edits of the design
        that would be lost are kept if the engineer says so (the fault and operating point are loaded either way)."""
        self.speed.setValue(float(sc.get("speed_rpm", 12000.0)))
        rq = sc.get("request")
        if rq:
            self.req_kind.setCurrentIndex(max(0, self.req_kind.findData(rq.get("kind", "constant"))))
            self.T0.setValue(float(rq.get("T0_Nm", 0.0)))
            self.T1.setValue(float(rq.get("T1_Nm", rq.get("T0_Nm", 0.0))))
            self.t0.setValue(float(rq.get("t0_ms", 0.0)))
            self.t1.setValue(float(rq.get("t1_ms", 0.0)))
        else:
            self.req_kind.setCurrentIndex(0)
            self.T0.setValue(float(sc.get("torque_Nm", 150.0)))
        self.horizon.setValue(float(sc.get("horizon_ms", 60.0)))
        self.J.setValue(float(sc.get("J_kgm2") or 0.0))
        self.theta0.setValue(float(sc.get("theta0_deg", 0.0)))
        self.pwm.setCurrentIndex(max(0, self.pwm.findData(sc.get("pwm_model", "averaged"))))
        self.hmax.setValue(float(sc.get("h_max_us", 10.0)))
        self.faults.set_faults(sc.get("faults") or [])
        self.protect.setChecked(bool(sc.get("protection", True)))
        self._load_variant(sc.get("overrides"), ask)       # first: the variant may declare the forced strategy
        self.override.setCurrentIndex(max(0, self.override.findData(sc.get("reaction_override") or "")))

    def _load_variant(self, overrides, ask: bool):
        ov = dict(overrides or {})
        try:
            cur = self.editor.overrides()
        except InputValidationError:
            cur = None
        if ask and cur and cur != ov and not confirm(
                self, tr("설계 변형", "design variant"),
                tr(f"편집한 보호 설계·안전 요구의 변경 {self.editor.change_count()}건을 이 시나리오의 설계"
                   f"({'변경 ' + str(len(ov)) + '건' if ov else '프로젝트 설계 그대로'})로 바꿀까요?\n\n"
                   f"'아니오': 편집한 설계를 유지하고 고장·운전점만 불러옵니다.",
                   f"Replace the {self.editor.change_count()} edited design change(s) with this scenario's design "
                   f"({str(len(ov)) + ' change(s)' if ov else 'the project design'})?\n\n'No': keep the edited design "
                   f"and load only the faults and the operating point.")):
            return
        self._restore_variant(ov)

    def scenario(self) -> dict:
        kind = self.req_kind.currentData()
        sc = {"speed_rpm": self.speed.value(), "horizon_ms": self.horizon.value(), "theta0_deg": self.theta0.value(),
              "pwm_model": self.pwm.currentData(), "h_max_us": self.hmax.value(), "faults": self.faults.faults(),
              "protection": self.protect.isChecked()}
        if kind == "constant":
            sc["torque_Nm"] = self.T0.value()
        else:
            sc["request"] = {"kind": kind, "T0_Nm": self.T0.value(), "T1_Nm": self.T1.value(), "t0_ms": self.t0.value(),
                             "t1_ms": self.t1.value()}
        if self.J.value() > 0:
            sc["J_kgm2"] = self.J.value()
        if self.override.currentData():
            sc["reaction_override"] = self.override.currentData()
        ov = self.variant()
        if ov:
            sc["overrides"] = ov
        return sc

    def variant(self) -> dict:
        """The design variant of the editor tab (the difference to the project's fault_sim data); an invalid design
        is refused with the editor's reason."""
        if self.editor.error:
            raise ValueError(tr(f"보호 설계·안전 요구 편집 탭에 오류가 있습니다: {self.editor.error}",
                                f"the protection design & requirements tab has an error: {self.editor.error}"))
        return self.editor.overrides()

    # ------------------------------------------------------------------ design variant (the editor tab)
    def _design_changed(self, note: str = ""):
        """After an edit in the editor: the variant field (a run's input: the input tracking and the workspace see
        it), the comparison candidates and the forced-reaction choices (the declared strategies)."""
        ed = self.editor
        try:
            ov = ed.overrides()
        except InputValidationError:
            ov = {}
        self.design_variant.set_variant(ov, ed.change_count(), ed.error, note)
        self.faults.set_choices(ed.work)    # sensors, mechanisms, paths, resources, strategies of the edited design
        self._refresh_candidates()
        self._refresh_override_combo()

    def _restore_variant(self, overrides) -> list:
        """A saved variant (scenario, counterexample, workspace, the project changed) back into the editor; a change
        that no longer applies to the project's design is named, never kept silently."""
        problems = self.editor.load(self.design["base"], overrides or None)
        note = ""
        if problems:
            note = tr("적용하지 못한 설계 변형 (프로젝트 설계를 씀): ", "design variant not applicable (the project's design "
                                                              "is used): ") + "; ".join(problems)
        self._design_changed(note)
        return problems

    def _strategies(self) -> list:
        return [s for s in (self.editor.work.get("strategies") or []) if isinstance(s, dict) and s.get("id")]

    def _refresh_candidates(self):
        """Comparison candidates: protection on / off, the primitive reactions and every declared strategy (a new
        strategy comes checked; the choices made so far are kept)."""
        prev = {self.cand_list.item(i).data(Qt.UserRole): self.cand_list.item(i).checkState()
                for i in range(self.cand_list.count())}
        self.cand_list.clear()
        for key, st in [(c, None) for c in CLASSIC] + [(str(x["id"]), x) for x in self._strategies()]:
            it = QListWidgetItem(react(key) if st is None else key)
            it.setData(Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(prev.get(key, Qt.Checked))
            if st is not None:
                it.setToolTip(((st.get("text") or "") + "\n" if st.get("text") else "") + strategy_summary(st))
            self.cand_list.addItem(it)

    def candidates(self) -> list:
        return [self.cand_list.item(i).data(Qt.UserRole) for i in range(self.cand_list.count())
                if self.cand_list.item(i).checkState() == Qt.Checked]

    def _refresh_override_combo(self):
        cur = self.override.currentData() or ""
        self.override.blockSignals(True)
        self.override.clear()
        self.override.addItem(tr("프로젝트 정책", "project policy"), "")
        for x in OVERRIDES[1:]:
            self.override.addItem(react(x), x)
        for st in self._strategies():
            self.override.addItem(tr(f"전략 {st['id']}", f"strategy {st['id']}"), str(st["id"]))
            self.override.setItemData(self.override.count() - 1, strategy_summary(st), Qt.ToolTipRole)
        self.override.setCurrentIndex(max(0, self.override.findData(cur)))
        self.override.blockSignals(False)

    def _apply_design(self):
        """Write the edited design into the project (a modified working copy; the project page saves it as a new
        revision)."""
        import copy
        ed = self.editor
        if ed.error:
            error_box(self, tr("반영할 수 없음", "cannot apply"), ed.error)
            return
        n = ed.change_count()
        if not n:
            return
        if not confirm(self, tr("프로젝트에 반영", "write into the project"),
                       tr(f"설계 변경 {n}건을 프로젝트의 fault_sim 데이터로 씁니다. 이 데이터로 계산한 결과는 '프로젝트 데이터가 바뀜'으로 "
                          f"표시됩니다. 새 개정으로 남기려면 프로젝트 페이지에서 저장하세요. 계속할까요?",
                          f"Write {n} design change(s) into the project's fault_sim data? Results computed from it "
                          f"are then marked 'project data changed'. Save it as a new revision on the project page.")):
            return
        try:
            p = self.win.state.project.with_section("fault_sim", copy.deepcopy(ed.work))
        except InputValidationError as exc:
            error_box(self, tr("반영할 수 없음", "cannot apply"), str(exc))
            return
        self.win.state.set_project(p)

    # ------------------------------------------------------------------ safety case (review, matrix, report)
    def run_review(self):
        try:
            body = {"overrides": self.variant()}
        except ValueError as exc:
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.review_btn.setEnabled(False)
        self.win.runner.run("fault_review", tr("정적 설계 검토", "static design review"), _review_task, self._show_review,
                            body, self.win.state.project, on_error=self._err)

    def run_verification(self):
        try:
            body = {"overrides": self.variant()}
        except ValueError as exc:
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        keys = [self.verif_list.item(i).data(Qt.UserRole) for i in range(self.verif_list.count())
                if self.verif_list.item(i).checkState() == Qt.Checked]
        if not keys:
            error_box(self, tr("입력 오류", "input error"), tr("검증 매트릭스에 넣을 시나리오를 하나 이상 고르세요.",
                                                             "Choose at least one scenario for the matrix."))
            return
        body["keys"] = keys
        self.verif_btn.setEnabled(False)
        self.win.runner.run("fault_verification", tr("검증 매트릭스", "verification matrix"), _verif_task,
                            self._show_verif, body, self.win.state.project, on_error=self._err)

    def _show_review(self, res):
        self.review_btn.setEnabled(True)
        self.last_review = res
        rows, cols = [], {}
        for i, f in enumerate(res["findings"]):
            rows.append((finding_word(f["status"]), f["element"], check_word(f["check"]), f["detail"]))
            cols[(i, 0)] = VCOLOR.get(f["status"], "#57606a")
        self.t_find.set_rows(rows, colors=cols)
        ms = lambda x: "—" if x is None else f"{x * 1e3:.4g}"              # noqa: E731
        rows = []
        for g in res["trace"]:
            for f in g["fsr"] or [{"id": "—", "asil": "", "mechanisms": [], "tsr": []}]:
                rows.append((g["sg"], g["asil"] or "—", ms(g["ftti_s"]) + " ms" if g["ftti_s"] else "—", f["id"],
                             f["asil"] or "—", ", ".join(f["mechanisms"]) or "—", ", ".join(f["tsr"]) or "—"))
        self.t_trace.set_rows(rows)
        rows, cols = [], {}
        for i, r in enumerate(res["latency"]):
            hi, lo, bud = r["detection_s"], r.get("detection_min_s"), r["fdti_budget_s"]
            span = ms(hi) if lo is None or hi is None or abs(hi - lo) < 1e-12 else f"{ms(lo)}…{ms(hi)}"
            rows.append((r["fsr"], r["mechanism"], tr(KIND_KO.get(r["kind"], r["kind"]), r["kind"]), r["path"],
                         span, ms(r["path_delay_s"]), ms(bud), r["detection_basis"]))
            if hi is not None and bud is not None:
                cols[(i, 4)] = (VCOLOR["OK"] if hi <= bud + 1e-12 else
                                VCOLOR["INCONSISTENT"] if lo is not None and lo > bud + 1e-12 else VCOLOR["WARNING"])
        self.t_lat.set_rows(rows, colors=cols)
        c = res["counts"]
        self.case_state.setText(tr(f"정적 검토: 모순 {c['INCONSISTENT']} · 누락 {c['MISSING']} · 경고 {c['WARNING']} · 참고 "
                                   f"{c['NOTE']} · OK {c['OK']}", f"static review: inconsistent {c['INCONSISTENT']} · "
                                                                  f"missing {c['MISSING']} · warnings {c['WARNING']} · "
                                                                  f"notes {c['NOTE']} · OK {c['OK']}"))
        self.i_case.read("fault_case", tr("안전 근거", "safety case"), safety_case_insight, res,
                         self._current_matrix())

    def _current_matrix(self):
        """The latest verification matrix if it was computed on the current design and project (else None)."""
        from ...extensions.faultsim.campaign import stale_against
        m = self.last_verif
        if not m:
            return None
        try:
            ov = self.editor.overrides()
        except InputValidationError:
            return None
        if (m.get("overrides") or {}) != ov or stale_against({"project": m.get("project")}, self.win.state.project):
            return None
        return m

    def _show_verif(self, res):
        self.verif_btn.setEnabled(True)
        self.last_verif = res
        runs = res["rows"]
        keys = [r["key"] for r in runs]
        t = self.t_matrix
        heads = [tr("요구", "requirement"), tr("발동", "exercised"), tr("실패", "fail")] + keys
        t.setColumnCount(len(heads))
        t.setHorizontalHeaderLabels(heads)
        for j in range(len(heads)):
            t.horizontalHeader().setSectionResizeMode(j, QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(False)
        for j, r in enumerate(runs):
            t.horizontalHeaderItem(3 + j).setToolTip(r["title"].get(tr("ko", "en"), r["key"]) if isinstance(
                r["title"], dict) else str(r["title"]))
        mark = {"PASS": "✓", "FAIL": "✗", "UNKNOWN": "?", "NOT_APPLICABLE": "·"}
        rows, cols = [], {}
        for i, q_ in enumerate(res["requirements"]):
            cv = res["coverage"][q_]
            row = [q_, f"{cv['exercised']}/{len(runs)}", str(cv["fail"])]
            for j, k in enumerate(keys):
                vv = res["cells"][q_][k]
                row.append(mark.get(vv, vv))
                cols[(i, 3 + j)] = VCOLOR.get(vv, "#57606a")
            if cv["fail"]:
                cols[(i, 2)] = VCOLOR["FAIL"]
            if not cv["exercised"]:
                cols[(i, 1)] = VCOLOR["UNKNOWN"]
            rows.append(tuple(row))
        t.set_rows(rows, colors=cols)
        ms = lambda d, k: (f"{d[k]['value_s'] * 1e3:.4g} ms ({d[k]['scenario']})" if k in d else "—")   # noqa: E731
        self.t_timing.set_rows([(fid, ms(d, "FDTI"), ms(d, "FRTI"), ms(d, "FHTI")) for fid, d in res["timing"].items()])
        rows, cols = [], {}
        for i, r in enumerate(runs):
            fm = r["fmea"]
            rows.append((r["key"], fm["fault"],
                         f"{fm['detected_by']} @ {fm['t_detect_ms']:.4g} ms" if fm["detected_by"] else
                         tr("미검출", "not detected"),
                         (react(fm["reaction"]) + f" @ {fm['t_reaction_ms']:.4g} ms") if fm["reaction"] else "—",
                         "; ".join(fm["final_actual"]), f"{fm['i_phase_peak_A']:.0f}", f"{fm['i_d_min_A']:.0f}",
                         f"{fm['T_brake_max_Nm']:.0f}", f"{fm['v_dc_max_V']:.0f}", ", ".join(fm["failing"]) or "—"))
            if fm["failing"]:
                cols[(i, 9)] = VCOLOR["FAIL"]
            if fm["fault"] != "no fault" and not fm["detected_by"]:
                cols[(i, 2)] = VCOLOR["UNKNOWN"]
        self.t_fmea.set_rows(rows, colors=cols)
        fails = sum(1 for c in res["coverage"].values() if c["fail"])
        self.case_state.setText(tr(f"검증 매트릭스: 궤적 {len(runs)}개 · 실패 요구 {fails}개 · 미발동 {len(res['not_exercised'])}개"
                                   + (f" · 중복 제외 {len(res['skipped'])}개" if res["skipped"] else ""),
                                   f"verification matrix: {len(runs)} trajectories · {fails} failing requirement(s) · "
                                   f"{len(res['not_exercised'])} never exercised"
                                   + (f" · {len(res['skipped'])} duplicate(s) skipped" if res["skipped"] else "")))
        self.i_case.read("fault_case", tr("안전 근거", "safety case"), safety_case_insight, self.last_review, res)
        self.case_tabs.setCurrentIndex(0)

    def _save_report(self):
        try:
            ov = self.variant()
        except ValueError as exc:
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("안전 근거 보고서 저장", "save the safety-case report"),
                                              "safety_case.html", "HTML (*.html)")
        if not path:
            return
        m = self._current_matrix()
        try:
            html = api.fault_report({"overrides": ov, "matrix": m, "counterexamples": self.counterexamples},
                                    self.win.state.project)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("저장 실패", "save failed"), str(exc))
            return
        self.case_state.setText(tr(f"보고서 저장: {path}", f"report saved: {path}") + (
            "" if m else tr(" — 이 설계의 검증 매트릭스가 없어 매트릭스 절은 빠졌습니다 (먼저 '검증 매트릭스 실행')",
                            " — no verification matrix of this design, so its section is left out (run the matrix "
                            "first)")))

    # ------------------------------------------------------------------ runs
    def _body(self):
        try:
            return self.scenario()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return None

    def run(self):
        body = self._body()
        if body is None:
            return
        self.run_btn.setEnabled(False)
        self.win.runner.run("fault_sim", tr("고장 시뮬레이션", "fault simulation"), _sim_task, self._show, body,
                            self.win.state.project, on_error=self._err)

    def run_compare(self):
        body = self._body()
        if body is None:
            return
        cands = self.candidates()
        if not cands:
            error_box(self, tr("입력 오류", "input error"), tr("비교할 후보를 하나 이상 고르세요 ('반응 후보 비교' 탭).",
                                                             "Choose at least one candidate (the 'reaction "
                                                             "candidates' tab)."))
            return
        body["candidates"] = cands
        self.cmp_btn.setEnabled(False)
        self.win.runner.run("fault_compare", tr("반응 후보 비교", "reaction candidates"), _cmp_task, self._show_cmp, body,
                            self.win.state.project, on_error=self._err)

    def campaign_body(self) -> dict:
        axes = self.axes.axes()
        return {"base": self.scenario(), "axes": axes, "mode": self.cmode.currentData(), "n_random": self.cn.value(),
                "seed": self.cseed.value(), "boundary_refinements": self.cref.value()}

    def run_campaign(self):
        try:
            body = self.campaign_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.camp_btn.setEnabled(False)
        self.win.runner.run("fault_campaign", tr("고장 캠페인", "fault campaign"), _camp_task, self._show_camp, body,
                            self.win.state.project, on_error=self._err)

    def run_validation(self):
        self.val_btn.setEnabled(False)
        self.win.runner.run("fault_validation", tr("플랜트 검증", "plant validation"), _val_task, self._show_val,
                            {"quick": not self.val_full.isChecked()}, self.win.state.project, on_error=self._err)

    def rerun_selected(self):
        rows = sorted({i.row() for i in self.t_cx.selectedIndexes()})
        if not rows or rows[0] >= len(self.counterexamples):
            self.cx_state.setText(tr("다시 실행할 반례를 표에서 고르세요.", "Select a counterexample in the table."))
            return
        rec = self.counterexamples[rows[0]]
        self.cx_rerun.setEnabled(False)
        self.win.runner.run("fault_rerun", tr("반례 재실행", "counterexample re-run"), _rerun_task, self._show_rerun,
                            {"record": rec}, self.win.state.project, on_error=self._err)

    def _err(self, msg, tb):
        for b in (self.run_btn, self.cmp_btn, self.camp_btn, self.val_btn, self.cx_rerun, self.review_btn,
                  self.verif_btn):
            b.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    # ------------------------------------------------------------------ results
    def _title(self, res) -> str:
        sc = res.get("scenario") or {}
        fs = ", ".join(f"{f['kind']}@{f.get('t_ms', 0):g} ms" for f in sc.get("faults") or []) or tr("고장 없음", "no fault")
        return f"{sc.get('speed_rpm', 0):g} rpm · {fs}"

    def _show(self, res):
        self.run_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.last = res
        tr_ = res["trace"]
        title = self._title(res)
        csv = lambda tr_=tr_: {k: tr_[k] for k in ("t", "T_shaft", "T_request", "T_cmd", "T_est_mon", "i_a", "i_b", "i_c",
                                                   "i_a_meas", "v_dc", "v_dc_meas", "i_bat", "bridge")}   # noqa: E731
        self.p_wave.draw(FF.fig_fault_waveforms, res, title=title, name="fault_waveforms", csv=csv)
        self._show_transient(res, title)
        self.p_time.draw(FF.fig_fault_timeline, res, title=title, name="fault_timeline")
        kinds = {"fault": tr("고장", "fault"), "detection": tr("검출", "detection"), "actuation": tr("반응", "reaction"),
                 "bridge": tr("브리지", "bridge"), "reaction_blocked": tr("반응 차단", "blocked"),
                 "reaction_conflict": tr("반응 충돌", "conflict"), "reaction_kept": tr("유지", "kept"),
                 "recovery": tr("복귀", "recovery"), "fault_cleared": tr("고장 소멸", "cleared"),
                 "plant": tr("플랜트", "plant"), "controller": tr("제어기", "controller"), "driver": tr("드라이버", "driver"),
                 "out_of_model": tr("모델 밖", "out of model"), "note": tr("메모", "note"),
                 "strategy": tr("반응 전략", "strategy")}
        ev = res["events"]
        self.t_events.set_rows([(f"{e['t'] * 1e3:.4f}", kinds.get(e["kind"], e["kind"]),
                                 fault_word(e["source"]) if e["kind"] in ("fault", "fault_cleared") else e["source"],
                                 e["text"]) for e in ev])
        rows, cols = [], {}
        evl = res["evaluation"]
        for g in evl["sg"]:
            i = len(rows)
            rows.append((g["id"], tr(f"FSR {', '.join(g['fsr'])}", f"FSR {', '.join(g['fsr'])}"),
                         verdict_ko(g["inverter_evidence"]), g["text"],
                         tr("인버터 수준 근거 (차량 승인 아님)", "inverter-level evidence (not a vehicle approval)")))
            cols[(i, 2)] = VCOLOR.get(g["inverter_evidence"], "#57606a")
        for f in evl["fsr"]:
            i = len(rows)
            rows.append((f["id"], "→ " + ", ".join(f["sg"]), verdict_ko(f["verdict"]), f["text"],
                         f["timeline"].get("why", "")))
            cols[(i, 2)] = VCOLOR.get(f["verdict"], "#57606a")
        for x in evl["tsr"]:
            i = len(rows)
            rows.append((x["id"], f"→ {x['fsr']} → {', '.join(x['sg'])}", verdict_ko(x["verdict"]), x["text"],
                         x.get("detail", "")))
            cols[(i, 2)] = VCOLOR.get(x["verdict"], "#57606a")
        self._req_rows = [("sg", g) for g in evl["sg"]] + [("fsr", f) for f in evl["fsr"]] + [("tsr", x) for x in
                                                                                             evl["tsr"]]
        self.t_req.set_rows(rows, colors=cols)
        self.insight.read("fault_sim", tr("고장 시뮬레이션", "fault simulation"), fault_insight, res)

    def _req_selected(self):
        from ...extensions.faultsim.report import criterion_text
        rows = sorted({i.row() for i in self.t_req.selectedIndexes()})
        if not rows or not getattr(self, "_req_rows", None) or rows[0] >= len(self._req_rows):
            return
        kind, x = self._req_rows[rows[0]]
        ms = lambda v: "—" if v is None else f"{v * 1e3:.4g} ms"              # noqa: E731
        if kind == "tsr":
            ev = json.dumps(x.get("evidence") or {}, ensure_ascii=False, default=lambda o: None, indent=None)
            txt = (f"<b>{x['id']}</b> ({x['type']}, {x['level']}, ASIL {x.get('asil') or '—'}) · {tr('판정', 'verdict')} "
                   f"<b>{verdict_ko(x['verdict'])}</b><br>{tr('기준', 'criterion')}: {criterion_text(x['criterion'])}<br>"
                   f"{tr('할당', 'allocation')}: {x.get('allocation') or '—'} · {tr('검증 방법', 'verification')}: "
                   f"{', '.join(x.get('verification') or []) or '—'} · {tr('값의 근거', 'rationale')}: "
                   f"{x.get('rationale') or '—'}<br>"
                   f"{tr('근거', 'evidence')}: {ev[:900]}<br>{tr('적용 범위', 'scope')}: {x['scope']['text']}")
        elif kind == "fsr":
            tl = x["timeline"]
            txt = (f"<b>{x['id']}</b> ASIL {x.get('asil') or '—'} · {tr('할당 메커니즘', 'allocated mechanisms')}: "
                   f"{', '.join(x['mechanisms'])} · {tr('안전 상태 TSR', 'safe-state TSR')}: {x.get('safe_state') or '—'}<br>"
                   f"t_F {ms(tl['t_F'])} · t_V {ms(tl['t_V'])} · t_D {ms(tl['t_D'])} · t_R {ms(tl['t_R'])} · t_S "
                   f"{ms(tl['t_S'])}<br>FDTI {ms(tl['FDTI'])} · FRTI {ms(tl['FRTI'])} · FHTI {ms(tl['FHTI'])} "
                   f"({tr('예산', 'budgets')} FDTI {ms(x['budgets']['FDTI_s'])}, FRTI {ms(x['budgets']['FRTI_s'])})<br>"
                   f"{tr('경고·성능 저하', 'warning / degradation')}: {x.get('warning') or '—'} · "
                   f"{tr('검증 방법', 'verification')}: {', '.join(x.get('verification') or []) or '—'}<br>"
                   f"{tl.get('why', '')}")
        else:
            veh = x.get("vehicle") or {}
            txt = (f"<b>{x['id']}</b> ASIL {x.get('asil') or '—'} · FTTI {ms(x.get('ftti_s'))} · {x['statement']}<br>"
                   f"{tr('안전 상태', 'safe state')}: {x.get('safe_state') or '—'} · {tr('운전 상황', 'situation')}: "
                   f"{x.get('situation') or '—'}<br>"
                   f"{tr('차량 지표', 'vehicle indicator')}: {json.dumps(veh, ensure_ascii=False, default=str)[:500]}")
        self.req_detail.setText(txt)

    def _show_cmp(self, res):
        self.cmp_btn.setEnabled(True)
        self.last_cmp = res
        self.p_cmp.draw(FF.fig_fault_compare, res, title=self._title(res), name="fault_candidates")
        rows, cols = [], {}
        f = lambda v, d=0: "—" if v is None else f"{v:.{d}f}"              # noqa: E731
        for i, r in enumerate(res["rows"]):
            m = r.get("metrics") or {}
            steps = strategy_log_text(r.get("strategy_log"))
            rows.append((Cell(react(r["candidate"]), steps.replace("<b>", "").replace("</b>", "") or
                              react(r["candidate"])), verdict_ko(r["overall"]), ", ".join(r["failing"]) or "—",
                         f(m.get("i_phase_peak_A")), f(m.get("i_d_min_A")), f(m.get("T_brake_peak_Nm")),
                         f(m.get("v_dc_max_V")), f(m.get("FHTI_ms"), 2), "; ".join(r["final_actual"])))
            cols[(i, 1)] = VCOLOR.get(r["overall"], "#57606a")
        self.t_cmp.set_rows(rows, colors=cols)
        self.i_cmp.read("fault_compare", tr("반응 후보 비교", "reaction candidates"), compare_insight, res)
        self.tabs.setCurrentWidget(self.cmp_view)

    def _show_camp(self, res):
        self.camp_btn.setEnabled(True)
        self.last_camp = res
        self.p_camp.draw(FF.fig_fault_campaign, res, name="fault_campaign")
        rows, cols = [], {}
        for i, r in enumerate(res["runs"]):
            m = r.get("metrics") or {}
            f = lambda v: "—" if v is None else f"{v:.4g}"               # noqa: E731
            rows.append((str(r["index"]), ", ".join(f"{axis_word(k)} = {v}" for k, v in r["point"].items()),
                         verdict_ko(r.get("overall", "")),
                         f(m.get("i_phase_peak_A")), f(m.get("v_dc_max_V")), f(m.get("FHTI_ms")),
                         ", ".join(k for k, v in r["verdicts"].items() if v == "FAIL")))
            cols[(i, 2)] = VCOLOR.get(r.get("overall"), "#57606a")
        self.t_runs.set_rows(rows, colors=cols)
        known = {c["id"] for c in self.counterexamples}
        self.counterexamples += [c for c in res.get("counterexamples") or [] if c["id"] not in known]
        self._refresh_cx()
        self.i_camp.read("fault_campaign", tr("고장 캠페인", "fault campaign"), campaign_insight, res)

    def _show_val(self, res):
        self.val_btn.setEnabled(True)
        self.last_val = res
        self.p_val.draw(FF.fig_fault_validation, res, name="fault_validation")
        rows, cols = [], {}
        for i, r in enumerate(res["rows"]):
            rows.append((r["case"], r["quantity"], f"{r['plant']:.6g}", f"{r['reference']:.6g}", f"{r['abs_error']:.3g}",
                         f"{r['tolerance']:.3g}", r["reference_kind"]))
            cols[(i, 4)] = VCOLOR["PASS"] if r["pass"] else VCOLOR["FAIL"]
        self.t_val.set_rows(rows, colors=cols)
        self.i_val.read("fault_validation", tr("플랜트 검증", "plant validation"), validation_insight, res)

    def show_dependencies(self):
        try:
            ind = api.fault_independence(None, self.win.state.project)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("계산 실패", "failed"), str(exc))
            return
        self.p_dep.draw(FF.fig_fault_dependencies, ind, name="fault_dependencies")
        self.t_dep.set_rows([(f["fsr"], ", ".join(f["mechanisms"]), ", ".join(f["single_points"]) or "—", f["statement"])
                             for f in ind["fsr"]],
                            colors={(i, 2): "#cf222e" for i, f in enumerate(ind["fsr"]) if f["single_points"]})

    # ------------------------------------------------------------------ counterexamples
    def _refresh_cx(self):
        from ...extensions.faultsim.campaign import stale_against
        rows, cols = [], {}
        for i, c in enumerate(self.counterexamples):
            st = stale_against(c, self.win.state.project)
            rows.append((c["id"], ", ".join(c.get("failing") or []), str(c.get("created", ""))[:16].replace("T", " "),
                         tr("입력 바뀜: ", "inputs changed: ") + ", ".join(st) if st else tr("현재 프로젝트와 같음",
                                                                                         "same as the project")))
            if st:
                cols[(i, 3)] = "#b7791f"
        self.t_cx.set_rows(rows, colors=cols)

    def after_workspace_restore(self):
        self._refresh_cx()

    def apply_project(self, _project=None):
        """Another project (or its fault_sim data written): the editor's base follows it and the study's variant is
        put back on top (a change that no longer applies is named)."""
        self._refresh_cx()
        try:
            ov = self.editor.overrides()
        except InputValidationError:
            ov = {}
        self.design = api.fault_design(None, self.win.state.project)
        self._restore_variant(ov)

    def _show_rerun(self, res):
        self.cx_rerun.setEnabled(True)
        st = res["stale_sections"]
        self.cx_state.setText(
            (tr("재현됨 (같은 입력·같은 판정·같은 수치)", "reproduced (same inputs, verdicts and numbers)") if res["reproduced"]
             and not st else
             tr(f"입력이 바뀜({', '.join(st)}): 지금 결과 — 실패 {', '.join(res['still_failing']) or '없음'}",
                f"inputs changed ({', '.join(st)}): now failing {', '.join(res['still_failing']) or 'nothing'}") if st else
             tr("같은 입력에서 재현되지 않음 — 코드가 바뀜", "not reproduced on the same inputs — the code changed"))
            + (tr(f" · 수치 변화: {json.dumps(res['metric_changes'], default=str)[:300]}",
                  f" · changed numbers: {json.dumps(res['metric_changes'], default=str)[:300]}")
               if res.get("metric_changes") else ""))

    def _cx_to_scenario(self):
        rows = sorted({i.row() for i in self.t_cx.selectedIndexes()})
        if rows and rows[0] < len(self.counterexamples):
            self.set_scenario(self.counterexamples[rows[0]]["scenario"], ask=True)

    def _cx_save(self):
        if not self.counterexamples:
            self.cx_state.setText(tr("저장할 반례가 없습니다.", "No counterexamples to save."))
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("반례 저장", "save counterexamples"), "counterexamples.json",
                                              "JSON (*.json)")
        if path:
            from ...extensions.faultsim.campaign import save_records
            save_records(path, self.counterexamples)

    def _cx_open(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("반례 열기", "open counterexamples"), "", "JSON (*.json)")
        if not path:
            return
        from ...extensions.faultsim.campaign import load_records
        try:
            recs = load_records(path)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("열기 실패", "open failed"), str(exc))
            return
        known = {c["id"] for c in self.counterexamples}
        self.counterexamples += [c for c in recs if c["id"] not in known]
        self._refresh_cx()

    # ------------------------------------------------------------------ files
    def _save_scenario(self):
        body = self._body()
        if body is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("시나리오 저장", "save scenario"), "fault_scenario.json",
                                              "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"schema": "twb-fault-scenario/1", "scenario": body}, fh, ensure_ascii=False, indent=1)

    def _open_scenario(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("시나리오 열기", "open scenario"), "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            self.set_scenario(d.get("scenario", d), ask=True)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("열기 실패", "open failed"), str(exc))

    def _export(self):
        if self.last is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("결과 저장", "save result"), "fault_result.json", "JSON (*.json)")
        if path:
            from ...decision import jsonable
            out = jsonable({k: v for k, v in self.last.items()})
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, default=str)

    def _show_transient(self, res, title):
        """The transient zoom and its table (``transient.transient_metrics``: simulated samples, no interpolation)."""
        from ...extensions.faultsim.transient import transient_metrics
        m = transient_metrics(res, bounds=FF.tsr_bounds(res))
        tr_ = res["trace"]
        w = m.get("window_s")

        def csv(tr_=tr_, w=w):
            import numpy as np
            t = np.asarray(tr_["t"], dtype=float)
            k = np.ones(t.size, dtype=bool) if w is None else (t >= w[0]) & (t <= w[1])
            return {key: np.asarray(tr_[key])[k] for key in ("t", "T_shaft", "T_request", "T_cmd", "i_a", "i_b",
                                                              "i_c", "v_dc", "i_bat", "bridge")}
        self.p_zoom.draw(FF.fig_fault_transient, res, title=title, name="fault_transient", csv=csv)
        names = {"T_shaft": tr("축 토크 [N·m]", "shaft torque [N·m]"), "T_em": tr("전자기 토크 [N·m]", "EM torque [N·m]"),
                 "i_phase": tr("상전류 |i| [A]", "phase current |i| [A]"), "v_dc": "V_dc [V]",
                 "i_bat": tr("배터리 전류 [A]", "battery current [A]")}

        def f(v, d=4):
            return "—" if v is None else f"{v:.{d}g}"
        rows = []
        for r in m["rows"]:
            ext = f(r["extreme"]) + (f" (i_{r['phase']})" if r.get("phase") else "")
            lim = r.get("outside_bound_ms")
            rows.append((names.get(r["quantity"], r["quantity"]), f(r.get("pre")), ext,
                         f"{r['t_extreme_s'] * 1e3:.4f}", f(r.get("after_fault_ms")), f(r.get("after_reaction_ms")),
                         f"{f(r.get('max'))} / {f(r.get('min'))}",
                         "—" if lim is None else f"{lim:.4g}" + (tr(" (최초 +", " (first +") +
                                                                 f"{r['first_outside_ms']:.4g} ms)"
                                                                 if r.get("first_outside_ms") is not None else ""),
                         f(r.get("settling_ms")) if r.get("settling_ms") is not None else
                         tr("구간 안에 정착 안 함", "not within the run"), f"{r['resolution_us']:.3g}"))
        self.t_trans.set_rows(rows)

    def redraw(self):
        for p in (self.p_wave, self.p_zoom, self.p_time, self.p_cmp, self.p_camp, self.p_val, self.p_dep):
            p.redraw()
        for i in (self.insight, self.i_cmp, self.i_camp, self.i_val, self.i_case):
            i.redraw()
