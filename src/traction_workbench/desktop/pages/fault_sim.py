"""Fault simulation & functional safety: one causal trajectory per scenario (fault -> measurement -> control and
monitoring -> reaction -> actual bridge -> truth -> SG / FSR / TSR verdicts), the reaction candidates from the same
initial condition, campaigns with failure boundaries and re-runnable counterexamples, the plant's validation evidence
and the declared dependencies of the protection architecture."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...extensions.faultsim.engine import FAULT_KINDS
from ...i18n import tr
from ...insight.fault import (axis_word, campaign_insight, compare_insight, fault_insight, fault_word,
                              react, validation_insight, verdict_ko)
from ...plots import fault_figures as FF
from ..widgets import (ConceptNote, KeyValueTable, PlotPanel, check, combo, error_box, hint, integer, number,
                       primary_button, reading_tab, with_reading)

VCOLOR = {"PASS": "#1a7f37", "FAIL": "#cf222e", "UNKNOWN": "#b7791f", "NOT_APPLICABLE": "#8c959f"}
OVERRIDES = ("", "asc_low", "asc_high", "six_switch_off", "torque_zero")

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


def _num(s: str):
    s = s.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        v = float(s)
        return int(v) if v.is_integer() and "." not in s and "e" not in s.lower() else v
    except ValueError:
        return s


def parse_params(text: str) -> dict:
    """``key=value, key=value`` (numbers where they parse)."""
    out = {}
    for part in (p for p in text.split(",") if p.strip()):
        if "=" not in part:
            raise ValueError(tr(f"매개변수는 key=value 형식: '{part.strip()}'", f"parameters are key=value: '{part.strip()}'"))
        k, v = part.split("=", 1)
        out[k.strip()] = _num(v)
    return out


def params_text(p: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in (p or {}).items())


def _values(text: str) -> dict:
    """An axis: ``a, b, c`` (values) or ``lo:hi:n`` (range)."""
    t = text.strip()
    if ":" in t and "," not in t:
        lo, hi, n = (x.strip() for x in t.split(":"))
        return {"range": [float(lo), float(hi)], "n": int(float(n))}
    return {"values": [_num(x) for x in t.split(",") if x.strip()]}


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


class FaultTable(QTableWidget):
    """Faults of the scenario: kind (choice), time [ms], parameters (key=value, ...)."""

    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.setHorizontalHeaderLabels([tr("고장 종류", "fault kind"), tr("시각 [ms]", "time [ms]"),
                                        tr("매개변수 (key=value, …)", "parameters (key=value, …)")])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setMinimumHeight(120)

    def add(self, f: dict | None = None):
        f = f or {"kind": "sensor", "t_ms": 10.0, "params": {"target": "CS_A", "mode": "offset", "value": 100}}
        r = self.rowCount()
        self.insertRow(r)
        c = QComboBox()
        for k, (desc, _p) in FAULT_KINDS.items():
            c.addItem(k, k)
            c.setItemData(c.count() - 1, desc, Qt.ToolTipRole)
        c.setCurrentIndex(max(0, c.findData(f["kind"])))
        self.setCellWidget(r, 0, c)
        self.setItem(r, 1, QTableWidgetItem(f"{float(f.get('t_ms', 0.0)):g}"))
        self.setItem(r, 2, QTableWidgetItem(params_text(f.get("params") or {})))

    def remove_selected(self):
        for row in sorted({i.row() for i in self.selectedIndexes()}, reverse=True):
            self.removeRow(row)

    def set_faults(self, faults: list):
        self.setRowCount(0)
        for f in faults or []:
            self.add(f)

    # workspace: the rows as typed (a parameter text that does not parse yet is kept as it is)
    def workspace_state(self) -> list:
        return [{"kind": self.cellWidget(r, 0).currentData(),
                 "t_ms": self.item(r, 1).text() if self.item(r, 1) else "",
                 "params": self.item(r, 2).text() if self.item(r, 2) else ""} for r in range(self.rowCount())]

    def restore_workspace(self, rows) -> list[str]:
        problems = []
        self.setRowCount(0)
        for i, row in enumerate(rows or []):
            if row.get("kind") not in FAULT_KINDS:
                problems.append(tr(f"{i + 1}행: 고장 종류 '{row.get('kind')}'가 이 앱에 없음 — 뺌",
                                   f"row {i + 1}: fault kind '{row.get('kind')}' does not exist in this app - dropped"))
                continue
            self.add({"kind": row["kind"], "t_ms": 0.0, "params": {}})
            r = self.rowCount() - 1
            self.item(r, 1).setText(str(row.get("t_ms", "")))
            self.item(r, 2).setText(str(row.get("params", "")))
        return problems

    def faults(self) -> list:
        out = []
        for r in range(self.rowCount()):
            kind = self.cellWidget(r, 0).currentData()
            t = float((self.item(r, 1).text() if self.item(r, 1) else "0") or 0.0)
            out.append({"kind": kind, "t_ms": t, "params": parse_params(self.item(r, 2).text() if self.item(r, 2)
                                                                        else "")})
        return out


class FaultSimPage(QWidget):
    workspace_data = ("counterexamples",)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last = self.last_cmp = self.last_camp = self.last_val = None
        self.counterexamples: list = []
        self.scenarios = api.fault_scenarios()
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
        self.theta0 = number(0, -360, 360, "°", 1, 15, tip=tr("t = 0의 전기각 (고장 시점의 각도를 바꿉니다)",
                                                             "electrical angle at t = 0 (moves the angle at the fault)"))
        self.pwm = combo([(tr("평균 PWM (빠름)", "averaged PWM (fast)"), "averaged"),
                          (tr("스위칭 PWM (리플·데드타임 순간값)", "switched PWM (ripple, dead-time instants)"), "switched")])
        self.hmax = number(10, 0.5, 50, "µs", 1, 1)
        for lab, w in ((tr("속도", "speed"), self.speed), (tr("요청 형태", "request"), self.req_kind),
                       (tr("요청 T0", "request T0"), self.T0), (tr("요청 T1 (스텝·램프)", "request T1 (step, ramp)"), self.T1),
                       (tr("변화 시작 t0", "change at t0"), self.t0), (tr("램프 끝 t1", "ramp end t1"), self.t1),
                       (tr("시뮬레이션 길이", "horizon"), self.horizon), (tr("초기 전기각", "initial angle"), self.theta0),
                       (tr("PWM 모델", "PWM model"), self.pwm), (tr("최대 적분 스텝", "max step"), self.hmax)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("고장 (주 고장 + 잠재 고장 조합)", "faults (primary + latent combinations)"))
        gl = QVBoxLayout(g)
        self.faults = FaultTable()
        gl.addWidget(self.faults)
        row = QHBoxLayout()
        self.add_fault = QPushButton(tr("고장 추가", "add fault"))
        self.del_fault = QPushButton(tr("선택 삭제", "remove selected"))
        self.add_fault.clicked.connect(lambda: self.faults.add())
        self.del_fault.clicked.connect(self.faults.remove_selected)
        row.addWidget(self.add_fault)
        row.addWidget(self.del_fault)
        gl.addLayout(row)
        gl.addWidget(hint(tr("매개변수 예: sensor → target=CS_A, mode=offset|gain|stuck|stuck_last|lost|delay, value=… · "
                             "torque_command → mode=stale|value|offset|sign_flip|loss, paths=control|monitor|both · "
                             "switch_short → leg=a, device=upper · gate_supply_loss → side=lower · resource_loss → "
                             "resource=SENS_5V · duration_ms=… (간헐 고장)",
                             "parameters: sensor → target=CS_A, mode=offset|gain|stuck|stuck_last|lost|delay, value=… · "
                             "torque_command → mode=stale|value|offset|sign_flip|loss, paths=control|monitor|both · "
                             "switch_short → leg=a, device=upper · gate_supply_loss → side=lower · resource_loss → "
                             "resource=SENS_5V · duration_ms=… (intermittent)")))
        v.addWidget(g)
        g = QGroupBox(tr("보호·반응·설계 변형", "protection · reaction · design variants"))
        f = QFormLayout(g)
        self.protect = check(tr("보호 반응 적용 (끄면 비교용 궤적; 게이트 드라이버 자체 desat 차단은 남음)",
                                "protection reactions on (off: the comparison trajectory; the gate drivers' own "
                                "desaturation turn-off stays)"), True)
        self.override = combo([(tr("프로젝트 정책", "project policy"), "")] + [(x, x) for x in OVERRIDES[1:]])
        self.overrides = QPlainTextEdit()
        self.overrides.setPlaceholderText('{"mechanisms.SM-TQ.params.debounce_ms": 1.0}')
        self.overrides.setFixedHeight(70)
        f.addRow(self.protect)
        f.addRow(tr("반응 강제 (후보)", "forced reaction (candidate)"), self.override)
        f.addRow(tr("설계 변형 (JSON)", "design variants (JSON)"), self.overrides)
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
        self.p_cmp = PlotPanel(hint=tr("'반응 후보 비교'를 누르면 같은 초기 조건·같은 고장에서 보호 적용/미적용과 ASC-low·ASC-high·6SO·"
                                       "토크 0을 비교합니다.", "Compare runs the same initial condition and fault with "
                                                          "protection on / off, ASC-low, ASC-high, 6SO and zero "
                                                          "torque."), min_height=420)
        self.t_cmp = KeyValueTable(headers=[tr("후보", "candidate"), tr("종합", "overall"), tr("위반 요구", "violated"),
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
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)
        self.win.track_inputs(("fault_sim", "fault_compare"), form)
        self._preset_changed()
        self._load_preset()

    # ------------------------------------------------------------------ campaign / validation / dependency tabs
    def _campaign_tab(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        g = QGroupBox(tr("축 (현재 시나리오가 기준)", "axes (the current scenario is the base)"))
        gl = QVBoxLayout(g)
        self.axes = QTableWidget(0, 2)
        self.axes.setHorizontalHeaderLabels([tr("경로", "path"), tr("값 (a, b, c) 또는 범위 lo:hi:n", "values (a, b, c) or "
                                                                                                 "range lo:hi:n")])
        self.axes.horizontalHeader().setStretchLastSection(True)
        self.axes.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.axes_why = hint("")
        self.axes_why.setWordWrap(True)
        gl.addWidget(self.axes_why)
        gl.addWidget(self.axes)
        row = QHBoxLayout()
        self.ax_add = QPushButton(tr("축 추가", "add axis"))
        self.ax_del = QPushButton(tr("선택 삭제", "remove selected"))
        self.ax_add.clicked.connect(lambda: (self.axes.insertRow(self.axes.rowCount())))
        self.ax_del.clicked.connect(lambda: [self.axes.removeRow(r) for r in sorted({i.row() for i in
                                                                                    self.axes.selectedIndexes()},
                                                                                   reverse=True)])
        row.addWidget(self.ax_add)
        row.addWidget(self.ax_del)
        gl.addLayout(row)
        gl.addWidget(hint(tr("경로 예: speed_rpm · torque_Nm · theta0_deg · faults.0.t_ms · faults.0.params.value · "
                             "tolerances.CS_A.gain_err · tolerances.machine.psi_scale · overrides.mechanisms.SM-TQ.params."
                             "debounce_ms · overrides.paths.HW.delay_us",
                             "paths: speed_rpm · torque_Nm · theta0_deg · faults.0.t_ms · faults.0.params.value · "
                             "tolerances.CS_A.gain_err · tolerances.machine.psi_scale · overrides.mechanisms.SM-TQ.params."
                             "debounce_ms · overrides.paths.HW.delay_us")))
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
        row = QHBoxLayout()
        self.cx_rerun = QPushButton(tr("선택 재실행", "re-run selected"))
        self.cx_load = QPushButton(tr("시나리오로 불러오기", "load as scenario"))
        self.cx_save = QPushButton(tr("파일로 저장", "save to file"))
        self.cx_open = QPushButton(tr("파일 열기", "open file"))
        self.cx_rerun.clicked.connect(self.rerun_selected)
        self.cx_load.clicked.connect(self._cx_to_scenario)
        self.cx_save.clicked.connect(self._cx_save)
        self.cx_open.clicked.connect(self._cx_open)
        for b in (self.cx_rerun, self.cx_load, self.cx_save, self.cx_open):
            row.addWidget(b)
        gl.addLayout(row)
        self.cx_state = QLabel("")
        self.cx_state.setWordWrap(True)
        gl.addWidget(self.cx_state)
        lv.addWidget(g)
        lv.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(left)
        sc.setMinimumWidth(420)
        lay.addWidget(sc)
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
        lay.addWidget(view, 1)
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

    # ------------------------------------------------------------------ scenario <-> inputs
    def _preset_changed(self, *_):
        s = next((x for x in self.scenarios if x["key"] == self.preset.currentData()), None)
        self.preset_hint.setText(s["hint"][tr("ko", "en")] if s else "")

    def _load_preset(self, *_):
        s = next((x for x in self.scenarios if x["key"] == self.preset.currentData()), None)
        if s:
            self.set_scenario(s["scenario"])
            self.set_axes((s.get("campaign") or {}).get("axes") or [], (s.get("campaign") or {}).get("why"))

    def set_axes(self, axes: list, why: dict | None = None):
        """The campaign axes (a scenario's suggestion or a counterexample's): path and values or range."""
        self.axes.setRowCount(0)
        for a in axes:
            r = self.axes.rowCount()
            self.axes.insertRow(r)
            self.axes.setItem(r, 0, QTableWidgetItem(a["path"]))
            txt = (f"{a['range'][0]:g}:{a['range'][1]:g}:{int(a.get('n', 5))}" if a.get("range") else
                   ", ".join(str(v) for v in a.get("values") or []))
            self.axes.setItem(r, 1, QTableWidgetItem(txt))
        self.axes_why.setText(tr("이 시나리오의 추천 캠페인: ", "suggested for this scenario: ") + why[tr("ko", "en")]
                              if why else "")

    def set_scenario(self, sc: dict):
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
        self.theta0.setValue(float(sc.get("theta0_deg", 0.0)))
        self.pwm.setCurrentIndex(max(0, self.pwm.findData(sc.get("pwm_model", "averaged"))))
        self.hmax.setValue(float(sc.get("h_max_us", 10.0)))
        self.faults.set_faults(sc.get("faults") or [])
        self.protect.setChecked(bool(sc.get("protection", True)))
        self.override.setCurrentIndex(max(0, self.override.findData(sc.get("reaction_override") or "")))
        ov = sc.get("overrides")
        self.overrides.setPlainText(json.dumps(ov, ensure_ascii=False) if ov else "")

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
        if self.override.currentData():
            sc["reaction_override"] = self.override.currentData()
        txt = self.overrides.toPlainText().strip()
        if txt:
            ov = json.loads(txt)
            if not isinstance(ov, dict):
                raise ValueError(tr("설계 변형은 JSON 객체여야 합니다", "design variants must be a JSON object"))
            sc["overrides"] = ov
        return sc

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
        self.cmp_btn.setEnabled(False)
        self.win.runner.run("fault_compare", tr("반응 후보 비교", "reaction candidates"), _cmp_task, self._show_cmp, body,
                            self.win.state.project, on_error=self._err)

    def campaign_body(self) -> dict:
        axes = []
        for r in range(self.axes.rowCount()):
            p = self.axes.item(r, 0).text().strip() if self.axes.item(r, 0) else ""
            vals = self.axes.item(r, 1).text().strip() if self.axes.item(r, 1) else ""
            if p and vals:
                axes.append({"path": p, **_values(vals)})
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
        for b in (self.run_btn, self.cmp_btn, self.camp_btn, self.val_btn, self.cx_rerun):
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
        self.p_time.draw(FF.fig_fault_timeline, res, title=title, name="fault_timeline")
        kinds = {"fault": tr("고장", "fault"), "detection": tr("검출", "detection"), "actuation": tr("반응", "reaction"),
                 "bridge": tr("브리지", "bridge"), "reaction_blocked": tr("반응 차단", "blocked"),
                 "reaction_conflict": tr("반응 충돌", "conflict"), "reaction_kept": tr("유지", "kept"),
                 "recovery": tr("복귀", "recovery"), "fault_cleared": tr("고장 소멸", "cleared"),
                 "plant": tr("플랜트", "plant"), "controller": tr("제어기", "controller"), "driver": tr("드라이버", "driver"),
                 "out_of_model": tr("모델 밖", "out of model"), "note": tr("메모", "note")}
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
        rows = sorted({i.row() for i in self.t_req.selectedIndexes()})
        if not rows or not getattr(self, "_req_rows", None) or rows[0] >= len(self._req_rows):
            return
        kind, x = self._req_rows[rows[0]]
        if kind == "tsr":
            ev = json.dumps(x.get("evidence") or {}, ensure_ascii=False, default=lambda o: None, indent=None)
            txt = (f"<b>{x['id']}</b> ({x['type']}, {x['level']}) · {tr('판정', 'verdict')} <b>{verdict_ko(x['verdict'])}</b>"
                   f"<br>{tr('기준', 'criterion')}: {json.dumps(x['criterion'], ensure_ascii=False)}<br>"
                   f"{tr('근거', 'evidence')}: {ev[:900]}<br>{tr('적용 범위', 'scope')}: {x['scope']['text']}")
        elif kind == "fsr":
            tl = x["timeline"]
            ms = lambda v: "—" if v is None else f"{v * 1e3:.4g} ms"          # noqa: E731
            txt = (f"<b>{x['id']}</b> · {tr('할당 메커니즘', 'allocated mechanisms')}: {', '.join(x['mechanisms'])}<br>"
                   f"t_F {ms(tl['t_F'])} · t_V {ms(tl['t_V'])} · t_D {ms(tl['t_D'])} · t_R {ms(tl['t_R'])} · t_S "
                   f"{ms(tl['t_S'])}<br>FDTI {ms(tl['FDTI'])} · FRTI {ms(tl['FRTI'])} · FHTI {ms(tl['FHTI'])} "
                   f"({tr('예산', 'budgets')} FDTI {ms(x['budgets']['FDTI_s'])}, FRTI {ms(x['budgets']['FRTI_s'])})<br>"
                   f"{tl.get('why', '')}")
        else:
            veh = x.get("vehicle") or {}
            ftti = "—" if x.get("ftti_s") is None else f"{x['ftti_s'] * 1e3:g} ms"
            txt = (f"<b>{x['id']}</b> ASIL {x.get('asil') or '—'} · FTTI {ftti} · {x['statement']}<br>"
                   f"{tr('차량 지표', 'vehicle indicator')}: {json.dumps(veh, ensure_ascii=False, default=str)[:500]}")
        self.req_detail.setText(txt)

    def _show_cmp(self, res):
        self.cmp_btn.setEnabled(True)
        self.last_cmp = res
        self.p_cmp.draw(FF.fig_fault_compare, res, title=self._title(res), name="fault_candidates")
        rows, cols = [], {}
        for i, r in enumerate(res["rows"]):
            rows.append((react(r["candidate"]), verdict_ko(r["overall"]), ", ".join(r["failing"]),
                         "; ".join(r["final_actual"])))
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
        self._refresh_cx()

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
            self.set_scenario(self.counterexamples[rows[0]]["scenario"])

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
            self.set_scenario(d.get("scenario", d))
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

    def redraw(self):
        for p in (self.p_wave, self.p_time, self.p_cmp, self.p_camp, self.p_val, self.p_dep):
            p.redraw()
        for i in (self.insight, self.i_cmp, self.i_camp, self.i_val):
            i.redraw()
