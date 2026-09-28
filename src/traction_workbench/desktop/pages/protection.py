"""Protection & fault page: thresholds / derating / fault reaction on one causal trajectory (review section 9) and
the ASC fault transient with the customer's two current-time requirements (review 9.13)."""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QLabel, QLineEdit, QScrollArea, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...plots import review_figures as RF
from ..widgets import ConceptNote, KeyValueTable, PlotPanel, check, combo, error_box, fmt, integer, number, primary_button

NOTE_PROT = lambda: tr(
    "<b>보호 임계값 검토</b>는 '경고 &lt; 디레이팅 &lt; 고장' 순서만 보지 않습니다. 같은 물리 궤적 위에서 "
    "<b>센서(이득·오프셋·필터) → 샘플링(주기·위상) → N연속 확인(거짓 샘플이면 카운터 리셋) → 실행·구동 지연 → 플랜트 입력 변경</b>"
    "을 인과적으로 계산하고, 물리량이 한계를 넘는지(t_limit)로 판정합니다. 검출이 빨라도 한계를 넘으면 보호 실패입니다.<br>"
    "<b>임계값 창</b>: nuisance 방지 하한 θ &gt; x_N,max + E+ + Eθ, 보호 상한 θ ≤ x_lim − E− − Eθ − Δx_L(또는 에너지 bound "
    "V_tr,max = √(V_lim² − 2E_after/C)). 두 식은 <b>충분조건</b>이라 창이 비면 '보장 불가(UNKNOWN)'이며, bound가 tight·동시달성 "
    "가능하다고 선언된 경우에만 '임계값 조정만으로 불가(INFEASIBLE)'입니다. 예시 값은 검토 문서의 합성 toy 값입니다.",
    "<b>Protection review</b> is not just 'warning &lt; derating &lt; fault'. On one physical trajectory it computes "
    "<b>sensor (gain, offset, filter) → sampling (period, phase) → N consecutive samples (reset on a false sample) → "
    "execution/actuation delay → plant input change</b> and judges by whether the physical variable crosses its limit "
    "(t_limit). Early detection that still crosses the limit is a failure.<br><b>Threshold window</b>: no-nuisance "
    "θ &gt; x_N,max + E+ + Eθ, protection θ ≤ x_lim − E− − Eθ − Δx_L (or the energy bound V_tr,max = √(V_lim² − 2E_after/C)). "
    "They are <b>sufficient</b> conditions: an empty window means 'not guaranteed' (UNKNOWN); only tight, jointly "
    "attainable bounds make it 'threshold tuning alone cannot work' (INFEASIBLE). Example values are the review's toy values.")

NOTE_ASC = lambda: tr(
    "<b>ASC 두 시간영역 전류 요구</b>: 같은 고장 전류 파형 하나로 (1) 단락 직후 짧은 창의 peak, (2) 더 긴 창의 RMS 등 "
    "고객 요구 두 개를 <b>각자의 연산자·창·시간 원점</b>으로 평가합니다(자동으로 RMS·정상상태로 해석하지 않음). "
    "dq 전류 크기와 상전류 peak는 다르므로 초기 전기각을 훑어 최악을 찾습니다. 정규 전류 한계로 고장 전류를 자르지 않습니다.<br>"
    "상수 파라미터 모델은 과도 스크리닝용(포화·교차결합·온도 변화 미반영)이고 flux map은 동특성 미검증이므로, 공동 판정은 "
    "SCREENING(=UNKNOWN)입니다. 감자·소자 생존은 공급사 envelope를 넣어야 평가합니다.",
    "<b>ASC with two current-time requirements</b>: one fault waveform, two customer requirements, each with its own "
    "operator, window and time origin (never silently read as RMS or steady state). The dq norm is not the phase peak, "
    "so the initial electrical angle is swept for the worst case. Fault currents are never clipped at the normal limit.<br>"
    "The constant-parameter model is a transient screening model and flux maps are not dynamically qualified, so the "
    "joint claim is SCREENING (UNKNOWN). Demagnetisation and device survival need supplier envelopes.")


def _prot_task(progress, body):
    progress(0.1, tr("인과 궤적 계산", "causal trajectories"))
    return api.protection(body)


def _asc_task(progress, body):
    progress(0.1, tr("ASC 과도 적분", "ASC transient"))
    return api.asc(body)


class ProtectionPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last = None
        self.last_asc = None
        self.tabs = QTabWidget()
        self.tabs.addTab(self._prot_tab(), tr("임계값·디레이팅·고장 반응", "thresholds · derating · fault reaction"))
        self.tabs.addTab(self._asc_tab(), tr("ASC 과도 (두 전류-시간 요구)", "ASC transient (two current-time windows)"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.tabs)
        self._apply_preset()

    # ------------------------------------------------------------------ protection tab
    def _prot_tab(self):
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        self.preset = combo([(tr("OV: 회생 중 배터리 차단 (검토 예시 9.6.1)", "OV: battery disconnect in regen (9.6.1)"), "ov"),
                             (tr("OT: 1-node 정션 디레이팅 (검토 예시 9.6.2)", "OT: one-node junction derating (9.6.2)"), "ot")])
        self.preset.currentIndexChanged.connect(self._apply_preset)
        v.addWidget(self.preset)
        g = QGroupBox(tr("플랜트", "plant"))
        f = QFormLayout(g)
        self.x0 = number(700, -1e6, 1e6, "", 3, 1)
        self.p1 = number(500, 0, 1e9, "", 4, 1)
        self.p2 = number(100, -1e9, 1e9, "", 4, 1)
        self.p3 = number(0, -1e9, 1e9, "", 4, 1)
        self.p4 = number(0, -1e9, 1e9, "", 4, 1)
        self.p5 = number(0, -1e9, 1e9, "", 4, 1)
        self.plabels = []
        for w in (self.x0, self.p1, self.p2, self.p3, self.p4, self.p5):
            lab = QLabel()
            self.plabels.append(lab)
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("센서·검출", "sensing · detection"))
        f = QFormLayout(g)
        self.gain = number(0, -50, 50, "%", 3, 0.1, tip=tr("측정 = (1+g)·x + o", "measured = (1+g)·x + o"))
        self.offset = number(5, -1e4, 1e4, "", 3, 0.5)
        self.tau = number(0, 0, 1e6, "ms", 4, 0.01)
        self.period = number(0.01, 1e-5, 1e6, "ms", 5, 0.01)
        self.nconf = integer(2, 1, 1000)
        self.execd = number(0, 0, 1e6, "ms", 4, 0.01)
        self.cmp = combo([(">=", ">="), (">", ">")])
        for lab, w in ((tr("이득 오차", "gain error"), self.gain), (tr("오프셋 (±)", "offset (±)"), self.offset),
                       (tr("필터 τ", "filter τ"), self.tau), (tr("샘플 주기 Ts", "sample period Ts"), self.period),
                       (tr("N 연속 확인", "N consecutive"), self.nconf), (tr("실행 지연", "exec delay"), self.execd),
                       (tr("비교 규칙", "comparator"), self.cmp)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("임계값·한계·반응", "thresholds · limit · reaction"))
        f = QFormLayout(g)
        self.th_w = number(725, -1e6, 1e6, "", 3, 1)
        self.th_f = number(738.5, -1e6, 1e6, "", 3, 0.5)
        self.th_r = number(720, -1e6, 1e6, "", 3, 1)
        self.e_theta = number(3, 0, 1e4, "", 3, 0.5)
        self.limit = number(800, -1e6, 1e6, "", 3, 1)
        self.adelay = number(0.15, 0, 1e7, "ms", 4, 0.01)
        self.horizon = number(1.0, 1e-4, 1e8, "ms", 4, 0.1)
        self.xn = number(720, -1e6, 1e6, "", 3, 1)
        self.xn_on = check(tr("정상 최대값 선언", "declare normal maximum"), True)
        self.xn_on.toggled.connect(self.xn.setEnabled)
        self.rip_a = number(15, 0, 1e6, "", 3, 1, tip=tr("정상 과도: x0 ± 진폭 사인 리플", "normal transient: x0 ± ripple"))
        self.rip_f = number(2000, 0, 1e7, "Hz", 1, 100)
        self.warn_need = number(0.05, 0, 1e7, "ms", 4, 0.01)
        self.tight = check(tr("bound가 tight·동시달성 가능 (선언)", "bounds tight and jointly attainable (declared)"), False)
        self.hw = QLineEdit()
        self.hw.setPlaceholderText(tr("비워 두면 '선언 안 됨'", "empty = not declared"))
        for lab, w in ((tr("경고", "warning"), self.th_w), (tr("고장", "fault"), self.th_f), (tr("해제", "release"), self.th_r),
                       (tr("임계값 공차 Eθ", "threshold tol. Eθ"), self.e_theta), (tr("물리 한계", "physical limit"), self.limit),
                       (tr("구동 지연", "action delay"), self.adelay), (tr("시뮬레이션 길이", "horizon"), self.horizon),
                       ("", self.xn_on), (tr("정상 최대 x_N,max", "normal max x_N,max"), self.xn),
                       (tr("정상 리플 진폭", "normal ripple amplitude"), self.rip_a),
                       (tr("정상 리플 주파수", "normal ripple frequency"), self.rip_f),
                       (tr("경고 후 필요 시간", "time needed after warning"), self.warn_need),
                       ("", self.tight), (tr("독립 HW 경로 (선언)", "independent HW path (declared)"), self.hw)):
            if lab:
                f.addRow(lab, w)
            else:
                f.addRow(w)                          # a label-less check box spans the form (never widens the panel)
        v.addWidget(g)
        self.run_btn = primary_button(tr("보호 검토 실행", "run protection review"))
        self.run_btn.clicked.connect(self.run)
        v.addWidget(self.run_btn)
        v.addWidget(ConceptNote(NOTE_PROT()))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(340)
        split.addWidget(sc)
        self.ptabs = QTabWidget()
        self.p_time = PlotPanel(hint=tr("같은 물리 궤적 위의 t_xcross·t_confirm·t_action·t_limit", "events on one trajectory"))
        w = QWidget()
        l2 = QVBoxLayout(w)
        l2.setContentsMargins(0, 0, 0, 0)
        self.p_win = PlotPanel()
        self.t_rows = KeyValueTable(headers=["PROT", tr("상태", "status"), tr("근거·내용", "detail")])
        l2.addWidget(self.p_win, 3)
        l2.addWidget(self.t_rows, 2)
        self.p_loop = PlotPanel(min_height=240)
        self.ptabs.addTab(self.p_time, tr("이벤트 타임라인", "event timeline"))
        self.ptabs.addTab(w, tr("임계값 창·PROT 표", "threshold window · PROT table"))
        self.ptabs.addTab(self.p_loop, tr("검출 루프 개요도", "detection loop diagram"))
        split.addWidget(self.ptabs)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 1100])
        return split

    def _apply_preset(self, *_):
        ex = self.win.state.example("PROTECTION" if self.preset.currentData() == "ov" else "PROTECTION_OT")
        p = ex["plant"]
        if p["kind"] == "capacitor_energy":
            labels = [tr("초기 전압 V0 [V]", "initial voltage V0 [V]"), "C [µF]", tr("유입 전력 P0 [kW]", "injected P0 [kW]"),
                      tr("반응 후 ramp [ms]", "ramp after reaction [ms]"), "—", "—"]
            vals = [p["x0"], p["C_uF"], p["P0_kW"], p["t_ramp_ms"], 0.0, 0.0]
        else:
            labels = [tr("초기 온도 T0 [°C]", "initial T0 [°C]"), "R [K/W]", "C_th [J/K]", tr("냉각수 T_c [°C]", "coolant T_c [°C]"),
                      tr("손실 P [W]", "loss P [W]"), tr("디레이팅 후 P [W]", "P after derating [W]")]
            vals = [p["x0"], p["R_K_per_W"], p["C_J_per_K"], p["T_coolant_C"], p["P_W"], p["P_after_W"]]
        for lab, w, text, val in zip(self.plabels, (self.x0, self.p1, self.p2, self.p3, self.p4, self.p5), labels, vals):
            lab.setText(text)
            w.setValue(val)
            used = text != "—"                         # a parameter this plant kind does not have: hidden, not "—"
            w.setEnabled(used)
            lab.setVisible(used)
            w.setVisible(used)
        se, th = ex["sensor"], ex["thresholds"]
        self.gain.setValue(se["gain_error_pct"])
        self.offset.setValue(se["offset"])
        self.tau.setValue(se["tau_filter_ms"])
        self.period.setValue(se["period_ms"])
        self.nconf.setValue(se["confirm_samples"])
        self.execd.setValue(se["exec_delay_ms"])
        self.th_w.setValue(th["warning"])
        self.th_f.setValue(th["fault"])
        self.th_r.setValue(th["release"])
        self.e_theta.setValue(th["E_theta"])
        self.limit.setValue(ex["limit"])
        self.adelay.setValue(ex["action_delay_ms"])
        self.horizon.setValue(ex["horizon_ms"])
        self.xn_on.setChecked(ex["x_normal_max"] is not None)
        if ex["x_normal_max"] is not None:
            self.xn.setValue(ex["x_normal_max"])
        nm = ex.get("normal") or []
        self.rip_a.setValue(nm[0]["ripple_amp"] if nm else 0.0)
        self.rip_f.setValue(nm[0]["ripple_hz"] if nm else 0.0)
        self.warn_need.setValue(ex["warning_needed_ms"] or 0.0)

    def apply_project(self, _project=None):
        """The OV example's DC-link capacitance is the project's capacitor bank."""
        if self.preset.currentData() == "ov":
            self.p1.setValue(float(self.win.state.example("PROTECTION")["plant"]["C_uF"]))

    def body(self) -> dict:
        kind = self.preset.currentData()
        ex = self.win.state.example("PROTECTION" if kind == "ov" else "PROTECTION_OT")
        if kind == "ov":
            ex["plant"] = {"kind": "capacitor_energy", "x0": self.x0.value(), "C_uF": self.p1.value(),
                           "P0_kW": self.p2.value(), "t_ramp_ms": self.p3.value()}
        else:
            ex["plant"] = {"kind": "thermal_1node", "x0": self.x0.value(), "R_K_per_W": self.p1.value(),
                           "C_J_per_K": self.p2.value(), "T_coolant_C": self.p3.value(), "P_W": self.p4.value(),
                           "P_after_W": self.p5.value()}
        ex["sensor"] = {"gain_error_pct": self.gain.value(), "offset": self.offset.value(),
                        "tau_filter_ms": self.tau.value(), "period_ms": self.period.value(), "phase_ms": 0.0,
                        "confirm_samples": self.nconf.value(), "exec_delay_ms": self.execd.value(),
                        "comparator": self.cmp.currentData()}
        ex["thresholds"] = {"warning": self.th_w.value(), "fault": self.th_f.value(), "release": self.th_r.value(),
                            "E_theta": self.e_theta.value()}
        ex["limit"] = self.limit.value()
        ex["action_delay_ms"] = self.adelay.value()
        ex["horizon_ms"] = self.horizon.value()
        ex["x_normal_max"] = self.xn.value() if self.xn_on.isChecked() else None
        ex["warning_needed_ms"] = self.warn_need.value() or None
        ex["normal"] = ([{"kind": "ramp", "x0": self.x0.value(), "slope_per_s": 0.0, "ripple_amp": self.rip_a.value(),
                          "ripple_hz": self.rip_f.value()}] if self.rip_a.value() > 0 else [])
        ex["tight_attainable"] = self.tight.isChecked()
        ex["hw_path"] = self.hw.text().strip() or None
        return ex

    def run(self):
        try:
            body = self.body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.run_btn.setEnabled(False)
        self.win.runner.run("protection", tr("보호 검토", "protection review"), _prot_task, self._show, body,
                            on_error=self._err)

    def _err(self, msg, tb):
        self.run_btn.setEnabled(True)
        self.asc_btn.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _show(self, res):
        self.run_btn.setEnabled(True)
        self.last = res
        tr_ = res["trace"]
        self.p_time.draw(RF.fig_protection_timeline, res, name="protection_timeline",
                         csv=lambda tr_=tr_: {"t_s": tr_["t_s"], "x": tr_["x"], "y_measured": tr_["y"]})
        self.p_win.draw(RF.fig_threshold_window, res, name="threshold_window")
        self.p_loop.draw(RF.fig_protection_loop, res, name="protection_loop")
        # a PROT row is a requirement check: PASS / FAIL / UNKNOWN (the engine status is kept in brackets)
        word = {"FEASIBLE": "PASS", "INFEASIBLE": "FAIL", "UNKNOWN": "UNKNOWN"}
        col = {"FEASIBLE": "#1a7f37", "INFEASIBLE": "#cf222e", "UNKNOWN": "#b7791f"}
        rows = res["rows"]
        self.t_rows.set_rows([(r["id"] + " " + r["item"], word.get(r["status"], r["status"]), r["detail"])
                              for r in rows],
                             colors={(i, 1): col.get(r["status"], "#57606a") for i, r in enumerate(rows)})

    # ------------------------------------------------------------------ ASC tab
    def _req_group(self, title, rid, op, t0, t1, lim, origin):
        g = QGroupBox(title)
        f = QFormLayout(g)
        w = {"id": QLineEdit(rid),
             "quantity": combo([(tr("상전류", "phase current"), "phase"), (tr("dq 크기", "dq norm"), "dq_norm")], "phase"),
             "operator": combo([(tr("절대 peak", "absolute peak"), "abs_peak"), ("RMS", "rms"),
                                (tr("이후 포락선", "envelope after"), "envelope_after"),
                                (tr("초과 시간 (누적)", "time above (cumulative)"), "time_above"),
                                (tr("초과 시간 (최장 연속)", "time above (longest contiguous)"), "time_above_contiguous")],
                               op),
             "t0": number(t0, 0, 1e7, "ms", 3, 1), "t1": number(t1, 0, 1e7, "ms", 3, 1),
             "limit": number(lim, 0, 1e9, "A", 2, 10),
             "origin": combo([(tr("단락 성립 시점", "short established"), "asc_established"),
                              (tr("고장 발생 시점", "fault inception"), "fault")], origin),
             "level": number(0, 0, 1e9, "A", 2, 10),
             "limit_ms": number(0, 0, 1e7, "ms", 3, 1,
                                tip=tr("초과 시간 연산자의 허용 시간 (전류 한계와 다른 차원)",
                                       "allowed duration of a time-above operator (a different dimension from a current limit)"))}
        for lab, key in ((tr("요구 ID", "requirement id"), "id"), (tr("물리량", "quantity"), "quantity"),
                         (tr("연산자", "operator"), "operator"), (tr("창 시작", "window start"), "t0"),
                         (tr("창 끝", "window end"), "t1"), (tr("전류 한계 (peak/RMS)", "current limit (peak/RMS)"), "limit"),
                         (tr("시간 원점", "time origin"), "origin"),
                         (tr("초과 기준 전류 (초과 시간)", "current level (time above)"), "level"),
                         (tr("허용 시간 (초과 시간)", "allowed duration (time above)"), "limit_ms")):
            f.addRow(lab, w[key])
        return g, w

    def _asc_tab(self):
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        ex = api.EXAMPLE_ASC
        g = QGroupBox(tr("사고 전 운전점·시간", "pre-fault point · timing"))
        f = QFormLayout(g)
        self.a_n = number(ex["speed_rpm"], 0, 30000, "rpm", 0, 100)
        self.a_vdc = number(ex["Vdc_V"], 1, 2000, "V", 1, 10)
        self.a_T = number(ex["torque_Nm"], -5000, 5000, "N·m", 2, 10)
        self.a_td = number(ex["t_delay_ms"], 0, 1e5, "ms", 3, 0.1)
        self.a_h = number(ex["horizon_ms"], 1, 1e6, "ms", 1, 10)
        self.a_J = number(0, 0, 1e3, "kg·m²", 4, 0.01, tip=tr("0 = 속도 일정", "0 = constant speed"))
        for lab, w in ((tr("속도", "speed"), self.a_n), ("Vdc", self.a_vdc), (tr("사고 전 토크", "pre-fault torque"), self.a_T),
                       (tr("고장→단락 지연", "fault → short delay"), self.a_td), (tr("계산 길이", "horizon"), self.a_h),
                       (tr("관성 J", "inertia J"), self.a_J)):
            f.addRow(lab, w)
        v.addWidget(g)
        r1, r2 = ex["requirements"]
        g1, self.req1 = self._req_group(tr("요구 1 (짧은 창)", "requirement 1 (short window)"), r1["req_id"], r1["operator"],
                                        r1["t_start_s"] * 1e3, r1["t_end_s"] * 1e3, r1["limit_A"], r1["origin"])
        g2, self.req2 = self._req_group(tr("요구 2 (긴 창)", "requirement 2 (long window)"), r2["req_id"], r2["operator"],
                                        r2["t_start_s"] * 1e3, r2["t_end_s"] * 1e3, r2["limit_A"], r2["origin"])
        v.addWidget(g1)
        v.addWidget(g2)
        g = QGroupBox(tr("공급사 envelope (없으면 UNKNOWN)", "supplier envelopes (UNKNOWN without)"))
        f = QFormLayout(g)
        self.demag_on = check(tr("감자 한계 id_min 사용", "use demag limit id_min"), False)
        self.demag = number(-1000, -1e6, 0, "A", 1, 10)
        self.dev_on = check(tr("소자 peak 한계 사용", "use device peak limit"), False)
        self.devpk = number(1500, 0, 1e7, "A", 1, 10)
        f.addRow(self.demag_on, self.demag)
        f.addRow(self.dev_on, self.devpk)
        v.addWidget(g)
        self.asc_btn = primary_button(tr("ASC 과도 계산", "run ASC transient"))
        self.asc_btn.clicked.connect(self.run_asc)
        v.addWidget(self.asc_btn)
        v.addWidget(ConceptNote(NOTE_ASC()))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(340)
        split.addWidget(sc)
        w = QWidget()
        l2 = QVBoxLayout(w)
        l2.setContentsMargins(0, 0, 0, 0)
        self.p_asc = PlotPanel()
        self.t_asc = KeyValueTable()
        l2.addWidget(self.p_asc, 3)
        l2.addWidget(self.t_asc, 2)
        split.addWidget(w)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 1100])
        return split

    def asc_body(self) -> dict:
        reqs = []
        for w in (self.req1, self.req2):
            r = {"req_id": w["id"].text().strip() or "REQ", "quantity": w["quantity"].currentData(),
                 "operator": w["operator"].currentData(), "t_start_s": w["t0"].value() * 1e-3,
                 "t_end_s": w["t1"].value() * 1e-3, "limit_A": w["limit"].value(), "origin": w["origin"].currentData()}
            if r["operator"] in ("time_above", "time_above_contiguous"):
                r["level_A"] = w["level"].value()
                r["limit_s"] = w["limit_ms"].value() * 1e-3
                r.pop("limit_A")
            reqs.append(r)
        s = self.win.state
        return {"drive": s.drive_spec, "limits": s.limits_dict, "speed_rpm": self.a_n.value(), "Vdc_V": self.a_vdc.value(),
                "torque_Nm": self.a_T.value(), "t_delay_ms": self.a_td.value(), "horizon_ms": self.a_h.value(),
                "J_kgm2": self.a_J.value() or None, "requirements": reqs,
                "demag_id_min_A": self.demag.value() if self.demag_on.isChecked() else None,
                "demag_basis": "UI entry" if self.demag_on.isChecked() else "",
                "device_peak_A": self.devpk.value() if self.dev_on.isChecked() else None,
                "device_basis": "UI entry" if self.dev_on.isChecked() else ""}

    def run_asc(self):
        self.asc_btn.setEnabled(False)
        self.win.runner.run("asc", tr("ASC 과도", "ASC transient"), _asc_task, self._show_asc, self.asc_body(),
                            on_error=self._err)

    def _show_asc(self, res):
        self.asc_btn.setEnabled(True)
        self.last_asc = res
        if not res.get("evaluable"):
            self.p_asc.placeholder(res["claim"]["detail"])
            self.t_asc.set_rows([(tr("판정", "claim"), res["claim"]["status"] + " — " + res["claim"]["detail"])])
            return
        w = res["waveform"]
        self.p_asc.draw(RF.fig_asc_transient, res, name="asc_transient",
                        csv=lambda w=w: {"t_s": w["t_s"], "id_A": w["id_A"], "iq_A": w["iq_A"], "ia_A": w["ia_A"],
                                         "ib_A": w["ib_A"], "ic_A": w["ic_A"]})
        rows = [(tr("공동 판정", "joint claim"), f"{res['claim']['status']} — {res['claim']['detail']}"),
                (tr("판정 수준", "claim level"), res["claim_level"]),
                (tr("사고 전 id, iq", "pre-fault id, iq"), f"{fmt(res['pre_fault']['id_A'])}, {fmt(res['pre_fault']['iq_A'])} A"),
                (tr("정상 ASC id, iq", "steady ASC id, iq"), f"{fmt(res['steady_asc']['id_A'])}, {fmt(res['steady_asc']['iq_A'])} A"),
                (tr("dq peak / 최소 id", "dq peak / min id"), f"{fmt(res['peak_dq_A'])} A / {fmt(res['min_id_A'])} A"),
                (tr("같은 식 수치 교차검증", "same-equation solver cross-check"), f"{fmt(res['rk_cross_check_A'])} A")]
        if res.get("fixed_speed_sensitivity_A") is not None:
            rows.append((tr("고정 속도 대비 (모델 민감도)", "vs fixed speed (model sensitivity)"),
                         f"{fmt(res['fixed_speed_sensitivity_A'])} A"))
        if res.get("energy_ledger"):
            rows.append((tr("에너지 장부 잔차", "energy ledger residual"), f"{fmt(res['energy_ledger']['residual_J'], 3)} J"))
        for rid, r in res["requirements"].items():
            if r.get("status") in ("REQUIREMENT_INCOMPLETE", "NOT_COVERED"):
                rows.append((rid, r["status"] + ": " + "; ".join(r.get("missing", []))))
            else:
                u = r.get("unit", "A")
                rows.append((rid, f"{r['operator']} {r['quantity']} = {fmt(r['value'])} {u} vs {fmt(r['limit'])} {u} → "
                                  f"{r['screening_verdict']} (screening; margin {fmt(r['margin'])} {u}, numerical allowance "
                                  f"{fmt(r.get('numerical_allowance'), 3)}, worst phase {r.get('worst_phase')}, angle "
                                  f"{fmt(r.get('worst_initial_angle_deg'))}°; {r.get('angle_basis', '')}"
                                  + (f"; {r['why']}" if r.get("why") else "") + ")"))
        for k, it in res["items"].items():
            rows.append((k, f"{it['status']} — {it.get('detail', '')}"))
        self.t_asc.set_rows(rows)

    def redraw(self):
        for p in (self.p_time, self.p_win, self.p_loop, self.p_asc):
            p.redraw()
