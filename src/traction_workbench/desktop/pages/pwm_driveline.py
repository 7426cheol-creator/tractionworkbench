"""Variable PWM policy and torque shaping / active damping page (variable-PWM / anti-jerk addendum).

Variable PWM: a fixed-frequency baseline and declared, CAUSAL schedules (rules grouped by schedule name, hysteresis,
dwell, protective pre-emption, fallback) evaluated on the same trajectory through the module losses (coupled Tj),
the RL ripple, the DC-link capacitor current, the delay ledger / current-loop margin and the minimum pulse.
Anti-jerk: off / shaping / feedback / combined on one maneuver with the sampled controller, delay, actuator ROM and
the capability torque window; sampled-loop stability over gain and delay.
"""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...plots import pwm_figures as F
from ...plots.labels import reason_label
from ...insight.control import (driveline_insight, pwm_policies_insight, pwm_ripple_insight,
                                pwm_timing_insight, pwm_transients_insight, stability_insight)
from ..widgets import (ConceptNote, KeyValueTable, NumTable, PlotPanel, check, combo, error_box, fmt, hint, number,
                       primary_button, table_with_buttons, reading_tab)

NOTE_PWM = lambda: tr(
    "<b>가변 PWM</b> = 운전점별 <b>캐리어(스위칭) 주파수</b> 변경입니다(전기 기본파·회전수 제어와 다름). 질문은 '최적 fsw'가 아니라 "
    "<b>같은 요구·전원·냉각</b>에서 허용 정책이 전류·전압·열·타이밍·리플 제약을 지키며 에너지/여유를 개선하는가입니다.<br>"
    "• 정책은 <b>인과적</b>이어야 합니다: 관측 가능한 측정(속도, 토크 명령, Vdc, NTC 온도)만 사용하고 실제 Tj·미래 토크는 쓰지 "
    "않습니다. 히스테리시스·최소 dwell·보호 선점·fallback을 선언합니다.<br>"
    "• 손실은 선택 fsw의 데이터시트 모듈 모델(Tj 결합), 리플은 RL 기준(정확 적분 = 엣지 합 스펙트럼), 커패시터 전류는 DC-link "
    "해석으로 다시 계산합니다. 지연 원장(샘플→latch→reload→변조기)은 선언값이며 deadline 미준수는 위반입니다.<br>"
    "• <b>필수 제약 위반은 효율로 상쇄하지 않습니다</b>. 모터 고조파 손실(R_ac(f)와 철손 상한)이 없으면 인버터 손실 개선은 "
    "모터+인버터 개선이 아닙니다(UNKNOWN). 결과는 '평가 후보 중 최선'이며 전역 최적이 아닙니다.",
    "<b>Variable PWM</b> = the <b>carrier (switching) frequency</b> changed per operating point (not the electrical "
    "fundamental or speed control). The question is not 'the optimal fsw' but whether an admissible policy keeps "
    "current, voltage, thermal, timing and ripple constraints and improves energy / margins under the <b>same demand, "
    "source and cooling</b>.<br>• A policy must be <b>causal</b>: observable measurements only (speed, torque "
    "command, Vdc, NTC temperature) - never the true Tj or future torque. Hysteresis, minimum dwell, protective "
    "pre-emption and a fallback are declared.<br>• Losses come from the datasheet module model at the scheduled fsw "
    "(coupled Tj), ripple from the RL reference (exact integration = edge-sum spectrum), capacitor current from the "
    "DC-link analysis. The delay ledger (sample -> latch -> reload -> modulator) is declared; a missed deadline is a "
    "violation.<br>• <b>A mandatory violation is never traded for efficiency</b>. Without motor harmonic losses "
    "(R_ac(f) and an iron-loss bound) an inverter-loss gain is not a motor+inverter gain (UNKNOWN). The result is "
    "'best among evaluated', not a global optimum.")

NOTE_DAMP = lambda: tr(
    "<b>Anti-jerk·능동 감쇠</b>: 검증된 비틀림 ROM(2관성, 고정 기어, 접촉 유지)과 <b>실제 인가 토크</b>(중재·클리핑·지연·액추에이터 "
    "응답 후)로 평가합니다. 선형 플랜트는 제어 이벤트 사이를 정확 적분합니다.<br>• 변형 off/shaping/feedback/combined를 <b>같은 "
    "조작·요구</b>로 비교합니다. 늦어진 가속을 jerk 개선만으로 표시하지 않으며, 창 내 손실 감소가 전달 일 감소 때문이면 효율 이득이 "
    "아닙니다.<br>• 보정 토크는 한도 <b>뒤에 더하지 않습니다</b>(중재 후 클리핑); 클리핑은 평균 토크를 바꿉니다. 긴급 토크 감소는 "
    "comfort 필터로 늦추지 않습니다.<br>• 안정성은 실제 샘플 주기·지연(분수 지연 포함)·액추에이터로 판정하며 불안정은 '이 정책'의 "
    "실패입니다. 백래시가 있는 토크 반전은 선형 모델 밖(UNKNOWN)입니다.",
    "<b>Anti-jerk / active damping</b>: evaluated with a qualified torsional ROM (two inertias, fixed gear, contact "
    "maintained) and the <b>actual applied torque</b> (after arbitration, clipping, delay and the actuator response). "
    "The linear plant is integrated exactly between controller events.<br>• Off / shaping / feedback / combined are "
    "compared on the <b>same maneuver and requirement</b>. A delayed acceleration is not a jerk gain by itself; less "
    "loss in the window because less work was delivered is not an efficiency gain.<br>• The correction is <b>never "
    "added after the limits</b> (clipping after arbitration); clipping shifts the mean torque. An emergency torque "
    "reduction is not delayed by comfort filters.<br>• Stability is judged with the real sample period, delay "
    "(fractional delays included) and actuator; an instability is a failure of THIS policy. A torque reversal with "
    "backlash is outside the linear model (UNKNOWN).")

RULE_HEAD = ["schedule", "rule", "fsw [kHz]", "n lo", "n hi", "|T| lo", "|T| hi", "T_ntc lo", "T_ntc hi", "prot."]


def _set(w, data):
    i = w.findData(data)
    if i >= 0:
        w.setCurrentIndex(i)


def _task(fn):
    def run(progress, body):
        progress(0.1, tr("계산 중", "computing"))
        return fn(body)
    return run


def _scroll(w):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    sc.setMinimumWidth(460)
    return sc


def _rng(lo, hi):
    if lo is None and hi is None:
        return None
    return [-1e12 if lo is None else lo, 1e12 if hi is None else hi]


class PwmDrivelinePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last_pol = self.last_tim = self.last_rip = self.last_trn = self.last_dl = self.last_stab = None
        self.tabs = QTabWidget()
        self.tabs.addTab(self._pwm_tab(), tr("가변 PWM (fsw 정책)", "variable PWM (fsw policy)"))
        self.tabs.addTab(self._dl_tab(), tr("Anti-jerk·능동 감쇠", "anti-jerk · active damping"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.tabs)

    # ================================================================== variable PWM
    def _pwm_tab(self):
        ex = self.win.state.example("PWM")
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("모듈·냉각·기준", "module · cooling · baseline"))
        f = QFormLayout(g)
        self.p_mod = combo([(tr("프로젝트 모듈", "project module"), "igbt"),
                            (tr("프로젝트 대안 모듈", "project alternative"), "sic"),
                            (tr("전력변환 페이지 모듈", "power page module"), "power")], "igbt")
        self.p_rth = number(ex["Rth_K_per_W"], 0.001, 10, "K/W", 4, 0.01)
        self.p_cool = number(ex["coolant_C"], -40, 120, "°C", 1, 5)
        self.p_mdl = combo([("SVPWM", "svpwm"), ("SPWM", "spwm")], ex["modulation"])
        self.p_lhf = number(ex["L_hf_uH"], 1, 1e5, "µH", 1, 10, tip=tr("캐리어 주파수에서의 차동 인덕턴스 (선언)",
                                                                      "differential inductance at the carrier (declared)"))
        self.p_base = number(ex["baseline_fsw_kHz"], 0.5, 200, "kHz", 2, 1)
        for lab, w in ((tr("모듈 데이터", "module data"), self.p_mod), ("Rth", self.p_rth), (tr("냉각수", "coolant"), self.p_cool),
                       (tr("변조", "modulation"), self.p_mdl), ("L_hf", self.p_lhf), (tr("기준 고정 fsw", "baseline fixed fsw"), self.p_base)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("후보 스케줄 (행 = 규칙, 이름으로 묶음, 위에서부터 우선)", "candidate schedules (row = rule, grouped by "
                                                                          "name, first match wins)"))
        gl = QVBoxLayout(g)
        self.t_rules = NumTable(RULE_HEAD, min_height=170, text_cols=(0, 1), optional_cols=(3, 4, 5, 6, 7, 8, 9))
        rows = []
        for sd in ex["schedules"]:
            for r in sd["rules"]:
                sp, tq, tn = r.get("speed_rpm"), r.get("torque_abs_Nm"), r.get("sensor_temp_C")
                rows.append([sd["name"], r["name"], r["fsw_kHz"], sp and sp[0], sp and sp[1], tq and tq[0], tq and tq[1],
                             tn and tn[0], tn and tn[1], 1.0 if r.get("protective") else None])
        self.t_rules.load(rows)
        gl.addWidget(table_with_buttons(self.t_rules, tr(
            "빈 범위 = 조건 없음. prot. = 1이면 보호 규칙(dwell 무시). 규칙 조건의 측정이 없으면 그 규칙은 일치하지 않음(추정 안 함).",
            "blank range = no condition. prot. = 1: protective (pre-empts the dwell). A rule whose measurement is missing "
            "does not match (never assumed).")))
        f = QFormLayout()
        hy = ex["hysteresis"]
        self.h_n = number(hy["speed_rpm"], 0, 1e4, "rpm", 0, 50)
        self.h_T = number(hy["torque_abs_Nm"], 0, 1e3, "N·m", 1, 1)
        self.h_tn = number(hy["sensor_temp_C"], 0, 50, "K", 1, 1)
        self.h_dw = number(ex["min_dwell_s"], 0, 100, "s", 3, 0.1)
        for lab, w in ((tr("히스테리시스 속도", "hysteresis speed"), self.h_n), (tr("히스테리시스 토크", "hysteresis torque"), self.h_T),
                       (tr("히스테리시스 온도", "hysteresis temperature"), self.h_tn), (tr("최소 dwell", "minimum dwell"), self.h_dw)):
            f.addRow(lab, w)
        gl.addLayout(f)
        v.addWidget(g)
        g = QGroupBox(tr("궤적 (같은 요구를 모든 정책이 수행)", "trajectory (every policy delivers the same demand)"))
        gl = QVBoxLayout(g)
        self.t_seg = NumTable([tr("시간 [s]", "duration [s]"), "n [rpm]", "T [N·m]", "Vdc [V]", "T_ntc [°C]"], min_height=140,
                              optional_cols=(4,))
        self.t_seg.load([[s["duration_s"], s["speed_rpm"], s["torque_Nm"], s["Vdc_V"], s.get("sensor_temp_C")]
                         for s in ex["segments"]])
        gl.addWidget(table_with_buttons(self.t_seg))
        v.addWidget(g)
        tm, lp = ex["timing"], ex["loop"]
        g = QGroupBox(tr("타이밍 (선언) · 전류 루프", "timing (declared) · current loop"))
        f = QFormLayout(g)
        self.tm_lat = number(tm["sample_to_latch_us"], 0, 1e4, "µs", 2, 1)
        self.tm_flt = number(tm["filter_delay_us"], 0, 1e4, "µs", 2, 1)
        self.tm_upd = combo([(tr("주기당 1회 갱신", "single update"), 1), (tr("주기당 2회 갱신", "double update"), 2)],
                            tm["updates_per_period"])
        self.tm_mod = number(tm["modulator_delay_fraction"], 0, 1, "", 3, 0.05)
        self.tm_pul = number(tm["min_pulse_us"], 0, 100, "µs", 2, 0.1)
        self.tm_basis = QLineEdit(tm["basis"])
        self.lp_Ld = number(lp.get("Ld_uH") or lp.get("L_uH"), 1, 1e5, "µH", 1, 10,
                            tr("d축 설계 인덕턴스 (이득 설계값; 플랜트는 운전점의 기계 차동 인덕턴스)",
                               "d-axis design inductance (gain design; the plant is the machine's differential L at the point)"))
        self.lp_Lq = number(lp.get("Lq_uH") or lp.get("L_uH"), 1, 1e5, "µH", 1, 10)
        self.lp_R = number(lp["R_mohm"], 0, 1e4, "mΩ", 2, 1)
        self.lp_bw = number(lp["bandwidth_Hz"], 1, 1e5, "Hz", 0, 50)
        self.lp_map = combo([(tr("연속 이득 (Ki·Ts 재매핑)", "continuous gains (Ki·Ts remapped)"), "continuous"),
                             (tr("고정 이산 이득 (기준 fsw에서 튜닝)", "fixed discrete gains (tuned at the reference fsw)"),
                              "fixed_discrete")], lp["gain_mapping"])
        self.lp_int = combo([(tr("적분기 = 전압 (bumpless)", "integrator in volts (bumpless)"), "output"),
                             (tr("적분기 = 오차 합 (Ki_disc·Σe)", "integrator = error sum (Ki_disc·Σe)"), "error_sum")],
                            lp.get("integrator_storage", "output"))
        self.lp_tr = combo([(tr("전환 시 유지", "keep at a change"), "keep"), (tr("전환 시 리셋", "reset at a change"), "reset")],
                           lp.get("on_transition", "keep"))
        self.lp_aw = check(tr("anti-windup (포화 시 조건부 적분)", "anti-windup (conditional integration)"),
                           bool(lp.get("anti_windup", True)))
        for lab, w in ((tr("샘플→latch (ADC+WCET)", "sample→latch (ADC+WCET)"), self.tm_lat), (tr("필터 지연", "filter delay"), self.tm_flt),
                       (tr("갱신", "update"), self.tm_upd), (tr("변조기 지연 비율", "modulator delay fraction"), self.tm_mod),
                       (tr("최소 펄스", "minimum pulse"), self.tm_pul), (tr("근거", "basis"), self.tm_basis),
                       (tr("L_d 설계", "L_d design"), self.lp_Ld), (tr("L_q 설계", "L_q design"), self.lp_Lq), ("R", self.lp_R),
                       (tr("전류 루프 대역", "current-loop bandwidth"), self.lp_bw),
                       (tr("이득 매핑", "gain mapping"), self.lp_map), (tr("적분기 상태", "integrator state"), self.lp_int),
                       (tr("fsw 전환 시", "at an fsw change"), self.lp_tr), ("", self.lp_aw)):
            f.addRow(lab, w)
        v.addWidget(g)
        sn, nz = ex["sensing"], ex["measurement_noise"]
        g = QGroupBox(tr("전류 샘플링 (선언) · 스케줄 입력 잡음", "current acquisition (declared) · schedule input noise"))
        f = QFormLayout(g)
        self.sn_kind = combo([(tr("레그 저측 션트 3개", "three low-side leg shunts"), "leg_shunt"),
                              (tr("인라인 상전류 센서", "inline phase sensors"), "inline_phase"),
                              (tr("DC-link 단일 션트", "single DC-link shunt"), "dc_link_shunt")], sn["kind"])
        self.sn_settle = number(sn["settle_us"], 0, 100, "µs", 2, 0.1, tr("스위칭 엣지 후 링잉·증폭기 정착", "ringing / amplifier settling after an edge"))
        self.sn_ap = number(sn["aperture_us"], 0, 100, "µs", 2, 0.1)
        self.sn_noise = combo([(tr("모든 레그 엣지가 방해 (보수적)", "every leg's edge disturbs (conservative)"), "any_leg"),
                               (tr("자기 레그만 (레이아웃 근거 필요)", "own leg only (needs a layout basis)"), "own_leg")],
                              sn["edge_noise"])
        self.sn_two = check(tr("두 레그 + Kirchhoff 재구성 (센서 3개)", "two legs + Kirchhoff (three sensors)"),
                            bool(sn["reconstruct_from_two"]))
        self.sn_skew = number(sn["channel_skew_ns"], 0, 1e5, "ns", 0, 50, tr("상 채널 사이 샘플 시각 차", "time between the phase channels' samples"))
        self.sn_pol = combo([(tr("대체 없음", "no fallback"), "none"), (tr("마지막 유효값 유지", "hold the last valid sample"), "hold"),
                             (tr("예측기 (검증된 잔차 비율)", "predictor (validated residual fraction)"), "predict")],
                            sn["invalid_policy"])
        self.sn_pred = number(0.3, 0, 1, "", 2, 0.05)
        self.sn_age = number(sn["max_sample_age_us"], 1, 1e6, "µs", 1, 10)
        self.sn_err = number(sn["current_error_max_A"], 0.01, 1e4, "A", 2, 1)
        self.sn_basis = QLineEdit(sn["basis"])
        self.nz_n = number(nz["speed_rpm"], 0, 1e4, "rpm", 1, 5)
        self.nz_T = number(nz["torque_abs_Nm"], 0, 1e3, "N·m", 2, 0.5)
        self.nz_tn = number(nz["sensor_temp_C"], 0, 50, "K", 2, 0.5)
        self.nz_v = number(nz["Vdc_V"], 0, 500, "V", 1, 1)
        for lab, w in ((tr("방식", "kind"), self.sn_kind), ("settle", self.sn_settle), ("aperture", self.sn_ap),
                       (tr("엣지 잡음", "edge noise"), self.sn_noise), ("", self.sn_two), (tr("채널 skew", "channel skew"), self.sn_skew),
                       (tr("무효 샘플", "invalid samples"), self.sn_pol), (tr("예측 잔차 비율", "predictor residual"), self.sn_pred),
                       (tr("허용 샘플 나이", "allowed sample age"), self.sn_age), (tr("허용 재구성 오차", "allowed reconstruction error"), self.sn_err),
                       (tr("근거", "basis"), self.sn_basis), (tr("잡음 p-p: 속도", "noise p-p: speed"), self.nz_n),
                       (tr("잡음 p-p: 토크", "noise p-p: torque"), self.nz_T), (tr("잡음 p-p: NTC", "noise p-p: NTC"), self.nz_tn),
                       (tr("잡음 p-p: Vdc", "noise p-p: Vdc"), self.nz_v)):
            f.addRow(lab, w)
        v.addWidget(g)
        pl = ex["pwm_limits"]
        g = QGroupBox(tr("필수 제약 (미선언 = 검증 안 됨, 통과 아님)", "mandatory constraints (undeclared = unverified, not a pass)"))
        f = QFormLayout(g)
        self.lim = {}
        for key, lab, unit, lo, hi in (("Tj_max_C", "Tj max", "°C", 0, 250), ("i_peak_incl_ripple_max_A", tr("피크 전류 상한 (I_fund,pk + max|Δi|)", "peak-current bound (I_fund,pk + max|Δi|)"), "A", 1, 1e5),
                                       ("cap_rms_max_A", tr("커패시터 RMS", "capacitor RMS"), "A", 1, 1e5),
                                       ("phase_margin_min_deg", tr("위상 여유 min", "phase margin min"), "°", 0, 90),
                                       ("pulse_ratio_min", tr("펄스 비 min", "pulse ratio min"), "", 1, 1e3),
                                       ("transition_excursion_max_A", tr("fsw 전환 전류 편차 max", "fsw-change current excursion max"),
                                        "A", 0.1, 1e4)):
            on = check(lab, pl.get(key) is not None)
            w = number(pl.get(key) or (10.0 if key == "pulse_ratio_min" else 1.0), lo, hi, unit, 1, 1)
            row = QHBoxLayout()
            row.addWidget(on)
            row.addWidget(w, 1)
            f.addRow(row)
            self.lim[key] = (on, w)
        self.p_harm = check(tr("모터 고조파 데이터 사용 (예시 R_ac(f)·철손 상한)", "use motor harmonic data (example R_ac(f), iron bound)"), True)
        self.p_cap = check(tr("DC-link 커패시터 전류 계산 (예시 뱅크)", "compute DC-link capacitor current (example bank)"), True)
        f.addRow(self.p_harm)
        f.addRow(self.p_cap)
        v.addWidget(g)
        row = QHBoxLayout()
        self.p_btn = primary_button(tr("정책 비교", "compare policies"))
        self.p_btn.clicked.connect(self.run_policies)
        self.t_btn = QPushButton(tr("타이밍·전환", "timing · transition"))
        self.t_btn.clicked.connect(self.run_timing)
        self.r_btn = QPushButton(tr("리플 vs fsw", "ripple vs fsw"))
        self.r_btn.clicked.connect(self.run_ripple)
        self.x_btn = QPushButton(tr("샘플링·전환 과도", "sampling · transition"))
        self.x_btn.clicked.connect(self.run_transients)
        for b in (self.p_btn, self.t_btn, self.r_btn, self.x_btn):
            b.setMinimumHeight(34)
            row.addWidget(b)
        v.addLayout(row)
        v.addWidget(hint(tr("예시 스케줄·타이밍·고조파 데이터·한도는 합성 값입니다.", "Example schedules, timing, harmonic data and "
                                                                      "limits are synthetic.")))
        v.addWidget(ConceptNote(NOTE_PWM()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(('pwm_policies', 'pwm_timing', 'pwm_ripple', 'pwm_transients'), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.p_tabs = QTabWidget()
        self.pl_pol = PlotPanel(hint=tr("'정책 비교'를 누르세요", "press 'compare policies'"))
        self.pl_tim = PlotPanel(hint=tr("'타이밍·전환'을 누르세요", "press 'timing · transition'"))
        self.pl_rip = PlotPanel(hint=tr("'리플 vs fsw'를 누르세요", "press 'ripple vs fsw'"))
        self.pl_trn = PlotPanel(hint=tr("'샘플링·전환 과도'를 누르세요", "press 'sampling · transition'"))
        for p, lab in ((self.pl_pol, tr("정책 비교", "policies")), (self.pl_tim, tr("타이밍·전환", "timing · transition")),
                       (self.pl_rip, tr("리플", "ripple")), (self.pl_trn, tr("샘플링·전환 과도", "sampling · transition"))):
            self.p_tabs.addTab(p, lab)
        self.i_pwm = reading_tab(self.p_tabs, tr(
            "계산하면 해석이 표시됩니다 — 정책 비교: 인버터 에너지와 제어·파형 여유의 맞바꿈, 한계별 여유와 가장 빠듯한 항목 · 타이밍: "
            "fsw에 따른 지연·위상 여유 · 리플: fsw에 따른 리플 · 과도: 샘플링 유효성과 전환 구현 방식별 전류 편차.",
            "Run to read the result — policies: inverter energy against control and waveform margins, each limit's margin "
            "and the tightest · timing: delay and phase margin over fsw · ripple over fsw · transients: sampling validity "
            "and the current kick per implementation."))
        self.k_pwm = KeyValueTable()
        rl.addWidget(self.p_tabs, 3)
        rl.addWidget(self.k_pwm, 2)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([500, 960])
        return split

    def pwm_body(self) -> dict:
        b = self.win.state.example("PWM")             # product data of the active project; widgets overlay it
        key = self.p_mod.currentData()
        b["module"] = {"igbt": lambda: self.win.state.example("MODULE"),
                       "sic": lambda: self.win.state.example("MODULE_SIC")}.get(
            key, lambda: self.win.pages["power"].module_spec())()
        scheds = {}
        for r in self.t_rules.values():
            name, rule, fsw, nlo, nhi, tlo, thi, slo, shi, prot = r
            if not name:
                raise ValueError(tr("규칙마다 스케줄 이름이 필요합니다", "every rule needs a schedule name"))
            if fsw is None:
                raise ValueError(tr(f"'{name}/{rule}': fsw가 필요합니다", f"'{name}/{rule}': fsw is required"))
            scheds.setdefault(name, []).append({"name": rule or "rule", "fsw_kHz": fsw, "speed_rpm": _rng(nlo, nhi),
                                                "torque_abs_Nm": _rng(tlo, thi), "sensor_temp_C": _rng(slo, shi),
                                                "protective": bool(prot)})
        b["schedules"] = [{"name": n, "revision": "UI", "basis": "entered in the PWM page", "rules": rr}
                          for n, rr in scheds.items()]
        b["hysteresis"] = {"speed_rpm": self.h_n.value(), "torque_abs_Nm": self.h_T.value(), "sensor_temp_C": self.h_tn.value()}
        b["min_dwell_s"] = self.h_dw.value()
        b["segments"] = [{"duration_s": s[0], "speed_rpm": s[1], "torque_Nm": s[2], "Vdc_V": s[3], "sensor_temp_C": s[4]}
                         for s in self.t_seg.values()]
        if not b["segments"]:
            raise ValueError(tr("궤적 구간이 없습니다", "no trajectory segments"))
        b.update({"Rth_K_per_W": self.p_rth.value(), "coolant_C": self.p_cool.value(), "modulation": self.p_mdl.currentData(),
                  "L_hf_uH": self.p_lhf.value(), "baseline_fsw_kHz": self.p_base.value(),
                  "use_capacitor": self.p_cap.isChecked(), "harmonic": b["harmonic"] if self.p_harm.isChecked() else None})
        b["timing"] = {**b["timing"], "sample_to_latch_us": self.tm_lat.value(), "filter_delay_us": self.tm_flt.value(),
                       "updates_per_period": self.tm_upd.currentData(), "modulator_delay_fraction": self.tm_mod.value(),
                       "min_pulse_us": self.tm_pul.value(), "wcet_source": "declared estimate",
                       "basis": self.tm_basis.text().strip()}
        b["loop"] = {**b["loop"], "Ld_uH": self.lp_Ld.value(), "Lq_uH": self.lp_Lq.value(), "R_mohm": self.lp_R.value(),
                     "bandwidth_Hz": self.lp_bw.value(),
                     "gain_mapping": self.lp_map.currentData(), "reference_fsw_kHz": self.p_base.value(),
                     "integrator_storage": self.lp_int.currentData(), "on_transition": self.lp_tr.currentData(),
                     "anti_windup": self.lp_aw.isChecked()}
        kind = self.sn_kind.currentData()
        pts = "valley_and_peak" if (kind == "inline_phase" and self.tm_upd.currentData() == 2) else "valley"
        pol = self.sn_pol.currentData()
        b["sensing"] = {**b["sensing"], "kind": kind, "settle_us": self.sn_settle.value(), "aperture_us": self.sn_ap.value(),
                        "sample_points": pts, "edge_noise": self.sn_noise.currentData(),
                        "reconstruct_from_two": self.sn_two.isChecked(), "channel_skew_ns": self.sn_skew.value(),
                        "invalid_policy": pol, "predict_error_fraction": self.sn_pred.value() if pol == "predict" else None,
                        "max_sample_age_us": self.sn_age.value(), "current_error_max_A": self.sn_err.value(),
                        "basis": self.sn_basis.text().strip()}
        b["measurement_noise"] = {"speed_rpm": self.nz_n.value(), "torque_abs_Nm": self.nz_T.value(),
                                  "sensor_temp_C": self.nz_tn.value(), "Vdc_V": self.nz_v.value()}
        b["pwm_limits"] = {k: (w.value() if on.isChecked() else None) for k, (on, w) in self.lim.items()}
        b.update(self.win.state.body())
        return b

    def _start(self, key, label, fn, show, body, btn):
        btn.setEnabled(False)
        self.win.runner.run(key, label, _task(fn), show, body, on_error=self._err)

    def _err(self, msg, tb):
        for b in (self.p_btn, self.t_btn, self.r_btn, self.x_btn, self.d_btn, self.s_btn):
            b.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def apply_project(self, _project=None):
        """Controller (timing, current loop, sensing, fsw, modulation), module Rth and the gearbox from the active
        project; schedules, trajectories, limits and manoeuvres stay."""
        ex = self.win.state.example("PWM")
        self.p_rth.setValue(float(ex["Rth_K_per_W"]))
        self.p_base.setValue(float(ex["baseline_fsw_kHz"]))
        _set(self.p_mdl, ex["modulation"])
        tm, lp, sn = ex["timing"], ex["loop"], ex["sensing"]
        self.tm_lat.setValue(float(tm["sample_to_latch_us"]))
        self.tm_flt.setValue(float(tm.get("filter_delay_us") or 0.0))
        _set(self.tm_upd, int(tm.get("updates_per_period") or 1))
        self.tm_mod.setValue(float(tm.get("modulator_delay_fraction", 0.5)))
        self.tm_pul.setValue(float(tm.get("min_pulse_us") or 0.0))
        self.tm_basis.setText(str(tm.get("basis", "")))
        self.lp_Ld.setValue(float(lp.get("Ld_uH") or lp.get("L_uH")))
        self.lp_Lq.setValue(float(lp.get("Lq_uH") or lp.get("L_uH")))
        self.lp_R.setValue(float(lp["R_mohm"]))
        self.lp_bw.setValue(float(lp["bandwidth_Hz"]))
        _set(self.lp_map, lp.get("gain_mapping", "continuous"))
        _set(self.lp_int, lp.get("integrator_storage", "output"))
        _set(self.lp_tr, lp.get("on_transition", "keep"))
        self.lp_aw.setChecked(bool(lp.get("anti_windup", True)))
        _set(self.sn_kind, sn["kind"])
        self.sn_settle.setValue(float(sn["settle_us"]))
        self.sn_ap.setValue(float(sn["aperture_us"]))
        _set(self.sn_noise, sn["edge_noise"])
        self.sn_two.setChecked(bool(sn["reconstruct_from_two"]))
        self.sn_skew.setValue(float(sn["channel_skew_ns"]))
        _set(self.sn_pol, sn["invalid_policy"])
        self.sn_age.setValue(float(sn["max_sample_age_us"]))
        self.sn_err.setValue(float(sn["current_error_max_A"]))
        self.sn_basis.setText(str(sn.get("basis", "")))
        dl = self.win.state.example("DRIVELINE")
        dd, cc = dl["driveline"], dl["controller"]
        self.d_Jm.setValue(float(dd["Jm_kgm2"]))
        self.d_Jo.setValue(float(dd["J_out_kgm2"]))
        self.d_k.setValue(float(dd["k_out_Nm_per_rad"]))
        self.d_c.setValue(float(dd["c_out_Nms_per_rad"]))
        self.d_g.setValue(float(dd["ratio"]))
        self.d_r.setValue(float(dd["wheel_radius_m"]))
        _set(self.d_ct, dd["contact"])
        self.d_basis.setText(str(dd.get("basis", "")))
        self.c_ts.setValue(float(cc["sample_ms"]))
        self.c_dl.setValue(float(cc["delay_ms"]))
        self.c_act.setValue(float(cc.get("actuator_tau_ms") or 0.0))

    def _run_pwm(self, key, label, fn, show, btn):
        try:
            body = self.pwm_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self._start(key, label, fn, show, body, btn)

    def run_policies(self):
        self._run_pwm("pwm_policies", tr("PWM 정책 비교", "PWM policies"), api.pwm_policies, self._show_policies, self.p_btn)

    def run_timing(self):
        self._run_pwm("pwm_timing", tr("타이밍·전환", "timing"), api.pwm_timing, self._show_timing, self.t_btn)

    def run_ripple(self):
        self._run_pwm("pwm_ripple", tr("리플", "ripple"), api.pwm_ripple, self._show_ripple, self.r_btn)

    def run_transients(self):
        self._run_pwm("pwm_transients", tr("샘플링·전환 과도", "sampling · transition"), api.pwm_transients,
                      self._show_transients, self.x_btn)

    def _show_transients(self, res):
        self.x_btn.setEnabled(True)
        self.last_trn = res
        self.pl_trn.draw(F.fig_pwm_transients, res, name="pwm_transients",
                         csv=lambda r=res: {"m": r["sampling_curves"]["leg_shunt"]["m"],
                                            **{f"valid_{k}": c["valid_fraction"] for k, c in r["sampling_curves"].items()}})
        if self.p_tabs.currentWidget() is not self.i_pwm:
            self.p_tabs.setCurrentWidget(self.pl_trn)
        self.i_pwm.read("pwm_transients", tr("샘플링·전환 과도", "sampling · transition"), pwm_transients_insight, res)
        h, pt = res["sampling_here"], res["point"]
        rows = [(tr("운전점", "operating point"), f"{pt['speed_rpm']:.0f} rpm · {pt['torque_Nm']:.0f} N·m · m {pt['m']:.3f} · "
                                                f"f_e {pt['f_e_Hz']:.0f} Hz · fsw {pt['fsw_Hz'] / 1e3:g} kHz"),
                (tr("선언 샘플링", "declared acquisition"),
                 f"{h['status']} · {tr('유효', 'valid')} {100 * h['valid_fraction']:.1f} % · {tr('최대 나이', 'max age')} "
                 f"{fmt(h['max_age_s'] and 1e6 * h['max_age_s'], 4)} µs · {tr('오차 한계', 'error bound')} "
                 f"{fmt(h['error_bound_A'], 4)} A · skew {fmt(h['skew_error_bound_A'], 3)} A")]
        rows += [("   " + tr("위반", "violation"), x) for x in h["violations"]]
        rows += [("   " + tr("미검증", "unverified"), x) for x in h["unknown"]]
        lim = res["transition"].get("excursion_limit_A")
        for name, v in res["transition"]["variants"].items():
            if not v.get("evaluated"):
                rows.append((name, v.get("reason", "")))
                continue
            verdict = "" if lim is None else (" · OK" if v["excursion_A"] <= lim else tr(" · 한도 초과", " · above the limit"))
            rows.append((name, f"{tr('출력 점프', 'output jump')} {v['output_jump_V']:.4g} V · {tr('편차', 'excursion')} "
                               f"{v['excursion_A']:.4g} A{verdict} · {tr('정착', 'settle')} "
                               f"{fmt(v['settle_s'] and 1e3 * v['settle_s'], 4)} ms · Ki×{v['Ki_eff_ratio']:.3g} · "
                               f"{tr('포화 샘플', 'saturated samples')} {v['saturated_samples']}"))
        self.k_pwm.set_rows(rows)

    def _show_policies(self, res):
        self.p_btn.setEnabled(True)
        self.last_pol = res
        self.pl_pol.draw(F.fig_pwm_policies, res, name="pwm_policies",
                         csv=lambda r=res: {"policy": [p["policy"]["name"] for p in r["policies"]],
                                            "admissible": [p["admissible"] for p in r["policies"]],
                                            "E_inv_J": [p["E_inv_J"] for p in r["policies"]],
                                            "E_cu_pwm_J": [p["E_cu_pwm_J"] for p in r["policies"]],
                                            "E_cu_pwm_lower_bound_J": [p["E_cu_pwm_lower_bound_J"] for p in r["policies"]],
                                            "E_mag_hf_bound_J": [p["E_mag_hf_bound_J"] for p in r["policies"]],
                                            "energy_lower_J": [p["energy"]["lower_J"] for p in r["policies"]],
                                            "energy_upper_J": [p["energy"]["upper_J"] for p in r["policies"]],
                                            "Tj_max_C": [p["Tj_max_C"] for p in r["policies"]],
                                            "phase_margin_min_deg": [p["phase_margin_min_deg"] for p in r["policies"]]})
        if self.p_tabs.currentWidget() is not self.i_pwm:
            self.p_tabs.setCurrentWidget(self.pl_pol)
        self.i_pwm.read("pwm_policies", tr("정책 비교", "policies"), pwm_policies_insight, res)
        rows = []
        for p in res["policies"]:
            nm = p["policy"]["name"]
            rows.append((nm, (tr("허용", "admissible") if p["admissible"] else tr("허용 안 됨: ", "not admissible: ") +
                              "; ".join(p["violations"]))))
            kj = lambda v: fmt(v and v / 1e3, 4)
            cu = (f"{kj(p['E_cu_pwm_J'])} kJ" if p["E_cu_pwm_J"] is not None else
                  f"≥ {kj(p['E_cu_pwm_lower_bound_J'])} kJ ({tr('R_dc 하한만', 'R_dc lower bound only')})")
            mag = (f"≤ {kj(p['E_mag_hf_bound_J'])} kJ" if p["E_mag_hf_bound_J"] is not None else
                   tr("상한 없음 (UNKNOWN)", "no bound (UNKNOWN)"))
            e = p["energy"]
            rows.append(("   " + tr("에너지", "energy"),
                         f"{tr('인버터', 'inverter')} {kj(p['E_inv_J'])} kJ · {tr('모터 PWM 동손', 'motor PWM copper')} "
                         f"{cu} · Fe+PM HF {mag} · {tr('비교 구간', 'comparison interval')} "
                         f"[{kj(e['lower_J'])}, {kj(e['upper_J']) if e['upper_J'] is not None else '∞'}] kJ"
                         + (f" · C_dc ESR {kj(p['E_cap_J'])} kJ ({tr('별도', 'separate')})" if p.get("E_cap_J") else "")))
            rows.append(("   " + tr("여유", "margins"),
                         f"Tj {fmt(p['Tj_max_C'], 4)} °C · {tr('피크 전류 상한', 'peak-current bound')} "
                         f"{fmt(p['i_peak_bound_max_A'], 4)} A · I_cap "
                         f"{fmt(p['I_cap_rms_max_A'], 4)} A · PM {fmt(p['phase_margin_min_deg'], 3)}° · Np min "
                         f"{fmt(p['pulse_ratio_min'], 3)}"))
            if (p.get("fsw_waveform_error_max_percent") or 0.0) > 0.5:
                rows.append(("   " + tr("fsw 요청 vs 파형", "fsw requested vs waveform"),
                             tr(f"파형 모델(리플·샘플링·커패시터)은 동기 캐리어 사용 — 최대 오차 "
                                f"{p['fsw_waveform_error_max_percent']:.3g} %",
                                f"the waveform models (ripple, sampling, capacitor) use a synchronous carrier - max "
                                f"error {p['fsw_waveform_error_max_percent']:.3g} %")))
            smp = [sg["sampling"] for sg in p["segments"] if sg.get("sampling")]
            if smp:
                rows.append(("   " + tr("전류 샘플링", "current sampling"),
                             f"{tr('최저 유효', 'lowest valid')} {100 * min(x['valid_fraction'] for x in smp):.1f} % · "
                             f"{tr('최대 나이', 'max age')} {fmt(max(x['max_age_s'] for x in smp) * 1e6, 4)} µs · "
                             + ", ".join(sorted({x['status'] for x in smp}))))
            trs = [e["transient"] for e in p["transitions"] if e.get("carrier_change") and e.get("transient")]
            if trs:
                ev = [x for x in trs if x.get("evaluated")]
                rows.append(("   " + tr("fsw 전환", "fsw changes"),
                             f"{len(trs)} · {tr('최대 편차', 'max excursion')} "
                             f"{fmt(max((x['excursion_A'] for x in ev), default=None), 4)} A · "
                             f"{tr('bumpless', 'bumpless')} {sum(bool(x.get('bumpless')) for x in ev)}/{len(ev)}"))
            if p.get("chatter", {}).get("rows"):
                rows.append(("   " + tr("임계 채터", "threshold chatter"),
                             ", ".join(f"{c['measurement']}: {tr('위험', 'risk') if c['risk'] else ('OK' if c['risk'] is False else '—')}"
                                       for c in p["chatter"]["rows"])))
            if p["unverified"]:
                rows.append(("   " + tr("검증 안 됨", "unverified"), "; ".join(p["unverified"])))
            v = p.get("versus_baseline")
            if v:
                rows.append(("   " + tr("기준 대비", "vs baseline"),
                             (f"Δinverter {fmt(v.get('delta_E_inv_J') and v['delta_E_inv_J'] / 1e3, 4)} kJ "
                              f"({fmt(v.get('relative_inv') and 100 * v['relative_inv'], 3)} %) · total "
                              f"{v['total']['status']}: {v['total']['reason']}") if "total" in v else v.get("reason", "")))
        rows.append((tr("Pareto (허용 후보)", "Pareto (admissible)"), ", ".join(res["pareto"]) or tr("없음", "none")))
        rows.append((tr("평가 후보 중 인버터 에너지 최선", "best inverter energy among evaluated"),
                     res["best_inverter_energy_among_evaluated"] or tr("없음", "none")))
        be = res["best_policy_energy_among_evaluated"]
        rows.append((tr("정책 에너지 최선 (구간 비교)", "best policy energy (interval comparison)"),
                     f"{be['policy'] or '—'} · {be['status']}: {be['reason']}"))
        cv = res["energy_control_volume"]
        rows.append((tr("에너지 제어 체적", "energy control volume"),
                     cv["name"] + " — " + tr("제외: ", "excluded: ") + "; ".join(cv["excluded"])))
        rows.append((tr("피크 전류 의미", "peak current meaning"), res["peak_current_meaning"]))
        rows.append((tr("열 범위", "thermal scope"), res["thermal_scope"]))
        rows.append((tr("평가 안 함", "not evaluated"), "; ".join(res["not_evaluated"])))
        rows.append((tr("의미", "meaning"), res["meaning"]))
        self.k_pwm.set_rows(rows)

    def _show_timing(self, res):
        self.t_btn.setEnabled(True)
        self.last_tim = res
        self.pl_tim.draw(F.fig_pwm_timing, res, name="pwm_timing",
                         csv=lambda r=res: {k: [x.get(k) for x in r["rows"]] for k in
                                            ("fsw_Hz", "deadline_ok", "total_delay_s", "phase_margin_deg",
                                             "phase_at_mode_deg", "phase_at_crossover_deg")})
        if self.p_tabs.currentWidget() is not self.i_pwm:
            self.p_tabs.setCurrentWidget(self.pl_tim)
        self.i_pwm.read("pwm_timing", tr("타이밍·전환", "timing · transition"), pwm_timing_insight, res)
        rows = [(f"{x['fsw_Hz'] / 1e3:g} kHz", (tr("deadline 미준수", "deadline missed") if not x["deadline_ok"] else
                                                 f"τ {1e6 * x['total_delay_s']:.1f} µs · PM {fmt(x.get('phase_margin_deg'), 3)}° "
                                                 f"({tr('결속 축', 'binding axis')} {x.get('binding_axis') or '—'}: "
                                                 + ", ".join(f"{a} {fmt(v, 3)}°" for a, v in (x.get('phase_margin_by_axis_deg') or {}).items())
                                                 + f") · {tr('모드 위상', 'mode phase')} {fmt(x.get('phase_at_mode_deg'), 3)}°"))
                for x in res["rows"]]
        pl = res.get("plant_L_H")
        if pl:
            pp = res.get("plant_point") or {}
            rows.insert(0, (tr("플랜트 (기계 차동 L)", "plant (machine differential L)"),
                            f"L_d {1e6 * pl['d']:.1f} µH · L_q {1e6 * pl['q']:.1f} µH @ {pp.get('speed_rpm', 0):.0f} rpm, "
                            f"{pp.get('torque_Nm', 0):.0f} N·m"))
        for key in ("transition_shadow", "transition_immediate"):
            t = res[key]
            rows.append((t["update"], ("OK" if t["ok"] else tr("위반", "violation")) +
                         f" · duty error {fmt(t['worst_period_duty_error'] and 100 * t['worst_period_duty_error'], 3)} % · "
                         f"irregular periods {t['irregular_periods']} · " + "; ".join(t["gate_events"]["problems"][:2])))
        self.k_pwm.set_rows(rows)

    def _show_ripple(self, res):
        self.r_btn.setEnabled(True)
        self.last_rip = res
        self.pl_rip.draw(F.fig_pwm_ripple, res, name="pwm_ripple")
        if self.p_tabs.currentWidget() is not self.i_pwm:
            self.p_tabs.setCurrentWidget(self.pl_rip)
        self.i_pwm.read("pwm_ripple", tr("리플", "ripple"), pwm_ripple_insight, res)
        self.k_pwm.set_rows([(f"{x['fsw_Hz'] / 1e3:g} kHz",
                              f"ripple {x['ripple_rms_A']:.4g} A rms (spectrum {x['ripple_rms_spectrum_A']:.4g}) · pp "
                              f"{x['ripple_pp_A']:.4g} A · Np {fmt(x['pulse_ratio'], 3)} · narrowest pulse "
                              f"{fmt(x['narrowest_pulse_s'] and 1e6 * x['narrowest_pulse_s'], 3)} µs "
                              f"({'OK' if x['min_pulse_ok'] else tr('최소 펄스 위반', 'below the minimum pulse')})")
                             for x in res["rows"]])

    # ================================================================== anti-jerk
    def _dl_tab(self):
        ex = self.win.state.example("DRIVELINE")
        dd, mm, cc = ex["driveline"], ex["maneuver"], ex["controller"]
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("비틀림 ROM (출력 좌표로 입력, 모터 측으로 환산)", "torsional ROM (entered at the output, referred to the motor)"))
        f = QFormLayout(g)
        self.d_Jm = number(dd["Jm_kgm2"], 1e-4, 100, "kg·m²", 4, 0.01)
        self.d_Jo = number(dd["J_out_kgm2"], 1e-3, 1e5, "kg·m²", 2, 10)
        self.d_k = number(dd["k_out_Nm_per_rad"], 1, 1e8, "N·m/rad", 0, 500)
        self.d_c = number(dd["c_out_Nms_per_rad"], 0, 1e6, "N·m·s/rad", 2, 1)
        self.d_g = number(dd["ratio"], 0.1, 100, "", 3, 0.5)
        self.d_r = number(dd["wheel_radius_m"], 0.05, 2, "m", 3, 0.01)
        self.d_ct = combo([(tr("접촉 유지 (선형)", "contact maintained (linear)"), "maintained"),
                           (tr("백래시 있음 (반전 시 UNKNOWN)", "backlash (reversal -> UNKNOWN)"), "backlash")], dd["contact"])
        self.d_basis = QLineEdit(dd["basis"])
        for lab, w in (("J_m", self.d_Jm), ("J_out", self.d_Jo), ("k_out", self.d_k), ("c_out", self.d_c),
                       (tr("기어비 g", "ratio g"), self.d_g), (tr("휠 반경", "wheel radius"), self.d_r),
                       (tr("접촉", "contact"), self.d_ct), (tr("근거", "basis"), self.d_basis)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("조작 (tip-in/out)", "maneuver (tip-in / tip-out)"))
        f = QFormLayout(g)
        self.m_T0 = number(mm["T0_Nm"], -5000, 5000, "N·m", 1, 5)
        self.m_T1 = number(mm["T1_Nm"], -5000, 5000, "N·m", 1, 5)
        self.m_ts = number(mm["t_step_s"], 0, 10, "s", 3, 0.01)
        self.m_te = number(mm["t_end_s"], 0.05, 30, "s", 2, 0.1)
        self.m_n = number(mm["speed_rpm"], -30000, 30000, "rpm", 0, 100)
        self.m_vdc = number(mm["Vdc_V"], 1, 2000, "V", 1, 10)
        self.m_win = combo([(tr("정책 capability (현재 모델)", "policy capability (active model)"), "capability"),
                            (tr("선언 창", "declared window"), "declared")], "capability")
        self.m_lo = number(-300.0, -1e4, 1e4, "N·m", 1, 5)
        self.m_hi = number(300.0, -1e4, 1e4, "N·m", 1, 5)
        self.m_em = check(tr("안전 반응 (긴급 토크 감소)", "safety reaction (emergency torque reduction)"), False)
        self.m_em_t = number(0.6, 0, 30, "s", 3, 0.05)
        self.m_em_T = number(0.0, -5000, 5000, "N·m", 1, 5)
        for lab, w in (("T0", self.m_T0), ("T1", self.m_T1), (tr("스텝 시각", "step time"), self.m_ts), (tr("종료", "end"), self.m_te),
                       (tr("속도", "speed"), self.m_n), ("Vdc", self.m_vdc), (tr("토크 창", "torque window"), self.m_win),
                       (tr("창 하한", "window low"), self.m_lo), (tr("창 상한", "window high"), self.m_hi),
                       ("", self.m_em), (tr("안전 요청 시각", "safety request at"), self.m_em_t),
                       (tr("안전 토크", "safe torque"), self.m_em_T)):
            f.addRow(lab, w)
        v.addWidget(g)
        fb = ex["variants"]["combined"]
        g = QGroupBox(tr("제어기 (샘플·지연·액추에이터) 와 변형", "controller (sample · delay · actuator) and variants"))
        f = QFormLayout(g)
        self.c_ts = number(cc["sample_ms"], 0.01, 100, "ms", 3, 0.1)
        self.c_dl = number(cc["delay_ms"], 0, 1000, "ms", 3, 0.5)
        self.c_act = number(cc["actuator_tau_ms"], 0, 1000, "ms", 3, 0.5)
        self.c_sh = combo([(tr("rate 제한", "rate limit"), "rate"), (tr("1차 prefilter", "first-order prefilter"), "prefilter"),
                           (tr("ZV 입력 성형 (모드)", "ZV input shaper (mode)"), "zv")], fb["shaper"]["kind"])
        self.c_rate = number(fb["shaper"]["rate_Nm_per_s"], 1, 1e6, "N·m/s", 0, 100)
        self.c_tau = number(0.03, 1e-4, 10, "s", 4, 0.005)
        self.c_dp = combo([(tr("모터 속도 HPF", "motor-speed HPF"), "motor_speed_hpf"),
                           (tr("상대속도 (부하 속도 센서)", "relative speed (load speed sensed)"), "relative_speed")],
                          fb["damping"]["kind"])
        self.c_kd = number(fb["damping"]["Kd_Nms_per_rad"], 0, 1e4, "N·m·s/rad", 3, 0.1)
        self.c_hpf = number(fb["damping"]["hpf_Hz"], 0.01, 1000, "Hz", 2, 0.5)
        self.c_hpf_order = combo([(tr("2차 washout (가속 중 정상 편차 없음)", "2nd-order washout (no steady offset)"), 2),
                                  (tr("1차 HPF (가속 중 −Kd·a/ω_c 편차)", "1st-order HPF (−Kd·a/ω_c offset)"), 1)],
                                 int(fb["damping"].get("hpf_order", 1)))
        self.c_hpf_order.setToolTip(tr("차량이 가속하는 동안 모터 속도는 램프입니다. 1차 고역통과는 램프에서 a/ω_c로 수렴해 토크를 "
                                       "계속 빼앗고(요구 토크 미달), 2차 washout은 0으로 돌아갑니다.",
                                       "While the vehicle accelerates the motor speed is a ramp: a first-order high-pass "
                                       "settles at a/ω_c and keeps taking torque (a deficit on the request); the "
                                       "second-order washout returns to zero."))
        self.c_slew = check(tr("전압 여유로 토크 slew 확인 (FW)", "check the torque slew against the voltage headroom (FW)"),
                            bool(ex.get("check_slew", True)))
        for lab, w in ((tr("샘플 주기", "sample period"), self.c_ts), (tr("샘플→인가 지연", "sample→applied delay"), self.c_dl),
                       (tr("토크 응답 τ (ROM)", "torque response τ (ROM)"), self.c_act), (tr("성형", "shaping"), self.c_sh),
                       ("rate", self.c_rate), ("τ prefilter", self.c_tau), (tr("감쇠", "damping"), self.c_dp),
                       ("Kd", self.c_kd), ("HPF", self.c_hpf), (tr("HPF 차수", "HPF order"), self.c_hpf_order),
                       ("", self.c_slew)):
            f.addRow(lab, w)
        v.addWidget(g)
        sg = ex["sensing"]
        g = QGroupBox(tr("속도 신호 타이밍·대체 (선언)", "speed-signal timing and fallback (declared)"))
        f = QFormLayout(g)
        self.sg_skew = number(sg["load_speed_skew_ms"], 0, 1000, "ms", 3, 0.5,
                              tr("부하(휠) 속도 샘플이 모터 속도보다 오래된 정도 (timestamp 차)",
                                 "how much older the load (wheel) speed sample is than the motor speed sample"))
        self.sg_drop = QLineEdit("")
        self.sg_drop.setPlaceholderText(tr("신호 끊김 구간 [ms], 예: 300-420; 800-850", "dropout windows [ms], e.g. 300-420; 800-850"))
        self.sg_sig = combo([(tr("부하(휠) 속도", "load (wheel) speed"), "load"), (tr("모터 속도 (resolver)", "motor speed (resolver)"), "motor")],
                            sg["dropout_signal"])
        self.sg_lim_on = check(tr("stale 한계 선언 (넘으면 감쇠 페이드아웃)", "declared stale limit (beyond: fade the damping out)"),
                               sg["stale_limit_ms"] is not None)
        self.sg_lim = number(sg["stale_limit_ms"] or 20.0, 0.1, 1e4, "ms", 2, 1)
        self.sg_fade = number(sg["fade_ms"], 0, 1e4, "ms", 2, 1)
        for lab, w in ((tr("부하 속도 skew", "load-speed skew"), self.sg_skew), (tr("끊김 구간", "dropouts"), self.sg_drop),
                       (tr("끊기는 신호", "signal"), self.sg_sig), (self.sg_lim_on, self.sg_lim), (tr("페이드", "fade"), self.sg_fade)):
            f.addRow(lab, w)
        v.addWidget(g)
        rq = ex["requirement"]
        g = QGroupBox(tr("요구 (선언; 보편 기준 없음)", "requirement (declared; no universal threshold)"))
        f = QFormLayout(g)
        self.q_t90 = number(rq["t_to_90_max_s"], 0.001, 10, "s", 3, 0.01)
        self.q_j = number(rq["peak_vehicle_jerk_max_m_s3"], 0.1, 1e4, "m/s³", 1, 1)
        self.q_st = number(rq["settle_max_s"], 0.001, 30, "s", 3, 0.05)
        self.q_safe = number(1e3 * rq.get("safety_reaction_max_s", 0.02), 0.01, 1e4, "ms", 2, 1,
                             tr("보호 시간: comfort 지표와 별도로 판정 (상쇄 안 함)", "protection time: judged apart from comfort (never traded)"))
        self.q_band = number(rq.get("safety_band_Nm") or 2.0, 0.01, 1e4, "N·m", 2, 0.5,
                             tr("안전 토크 도달 판정 대역 (선언 필요: 휴리스틱 대역은 승인 근거가 아님)",
                                "safe-torque band (must be declared: a heuristic band never approves)"))
        for lab, w in ((tr("90% 응답 시간 max", "time to 90 % max"), self.q_t90), (tr("차량 저크 max", "vehicle jerk max"), self.q_j),
                       (tr("정착 시간 max", "settling time max"), self.q_st), (tr("안전 반응 max", "safety reaction max"), self.q_safe),
                       (tr("안전 토크 대역", "safe-torque band"), self.q_band)):
            f.addRow(lab, w)
        v.addWidget(g)
        row = QHBoxLayout()
        self.d_btn = primary_button(tr("변형 비교", "compare variants"))
        self.d_btn.clicked.connect(self.run_driveline)
        self.s_btn = QPushButton(tr("안정성 지도", "stability map"))
        self.s_btn.setMinimumHeight(34)
        self.s_btn.clicked.connect(self.run_stability)
        row.addWidget(self.d_btn)
        row.addWidget(self.s_btn)
        v.addLayout(row)
        v.addWidget(hint(tr("예시 구동계·제어기·요구는 합성 값입니다.", "Example driveline, controller and requirement are synthetic.")))
        v.addWidget(ConceptNote(NOTE_DAMP()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(('driveline', 'driveline_stability'), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.d_tabs = QTabWidget()
        self.pl_dl = PlotPanel(hint=tr("'변형 비교'를 누르세요", "press 'compare variants'"))
        self.pl_st = PlotPanel(hint=tr("'안정성 지도'를 누르세요", "press 'stability map'"))
        self.d_tabs.addTab(self.pl_dl, tr("응답", "response"))
        self.d_tabs.addTab(self.pl_st, tr("안정성", "stability"))
        self.i_dl = reading_tab(self.d_tabs, tr(
            "계산하면 해석이 표시됩니다 — 변형별 저크·응답·정착과 요구, 감쇠 없음 대비 무엇이 좋아지고 무엇을 잃는지, 전압 여유가 허용하는 "
            "토크 변화율 · 안정성: 감쇠 이득과 지연 내성의 맞바꿈.",
            "Run to read the result — jerk, response and settling per variant against the requirement, what improves and "
            "what is lost against no damping, the torque slew the voltage allows · stability: damping gain against delay "
            "tolerance."))
        self.k_dl = KeyValueTable()
        rl.addWidget(self.d_tabs, 3)
        rl.addWidget(self.k_dl, 2)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([500, 960])
        return split

    def dl_body(self) -> dict:
        b = self.win.state.example("DRIVELINE")       # product data of the active project; widgets overlay it
        b["driveline"] = {**b["driveline"], "Jm_kgm2": self.d_Jm.value(), "J_out_kgm2": self.d_Jo.value(), "k_out_Nm_per_rad": self.d_k.value(),
                          "c_out_Nms_per_rad": self.d_c.value(), "ratio": self.d_g.value(), "wheel_radius_m": self.d_r.value(),
                          "contact": self.d_ct.currentData(), "basis": self.d_basis.text().strip()}
        b["maneuver"] = {"T0_Nm": self.m_T0.value(), "T1_Nm": self.m_T1.value(), "t_step_s": self.m_ts.value(),
                         "t_end_s": self.m_te.value(), "speed_rpm": self.m_n.value(), "Vdc_V": self.m_vdc.value(),
                         "TL_out_Nm": 0.0, "window": "capability" if self.m_win.currentData() == "capability" else
                         [self.m_lo.value(), self.m_hi.value()],
                         "emergency_t_s": self.m_em_t.value() if self.m_em.isChecked() else None,
                         "emergency_T_Nm": self.m_em_T.value() if self.m_em.isChecked() else None}
        b["controller"] = {**b["controller"], "sample_ms": self.c_ts.value(), "delay_ms": self.c_dl.value(),
                           "actuator_tau_ms": self.c_act.value()}
        kind = self.c_sh.currentData()
        sh = {"kind": kind, "rate_Nm_per_s": self.c_rate.value(), "tau_s": self.c_tau.value()}
        if kind == "zv":
            md = api.driveline_from_dict(b["driveline"]).modal()
            sh.update({"zv_f_Hz": md["f_n_Hz"], "zv_zeta": md["zeta"]})
        dp = {"kind": self.c_dp.currentData(), "Kd_Nms_per_rad": self.c_kd.value(), "hpf_Hz": self.c_hpf.value(),
              "hpf_order": int(self.c_hpf_order.currentData())}
        b["variants"] = {"off": {}, "shaping": {"shaper": sh}, "feedback": {"damping": dp},
                         "combined": {"shaper": sh, "damping": dp}}
        b["requirement"] = {"t_to_90_max_s": self.q_t90.value(), "peak_vehicle_jerk_max_m_s3": self.q_j.value(),
                            "settle_max_s": self.q_st.value(), "safety_reaction_max_s": 1e-3 * self.q_safe.value(),
                            "safety_band_Nm": self.q_band.value(), "basis": "UI"}
        drops = []
        for part in self.sg_drop.text().replace(",", ";").split(";"):
            part = part.strip()
            if not part:
                continue
            a, _, c = part.partition("-")
            try:
                drops.append([float(a), float(c)])
            except ValueError:
                raise ValueError(tr(f"끊김 구간 형식 오류: {part!r} (예: 300-420)", f"bad dropout window {part!r} (e.g. 300-420)")) from None
        b["sensing"] = {"load_speed_skew_ms": self.sg_skew.value(), "dropouts_ms": drops,
                        "dropout_signal": self.sg_sig.currentData(),
                        "stale_limit_ms": self.sg_lim.value() if self.sg_lim_on.isChecked() else None,
                        "fade_ms": self.sg_fade.value(), "basis": "UI"}
        b["check_slew"] = self.c_slew.isChecked()
        b.update(self.win.state.body())
        return b

    def _run_dl(self, key, label, fn, show, btn):
        try:
            body = self.dl_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self._start(key, label, fn, show, body, btn)

    def run_driveline(self):
        self._run_dl("driveline", tr("anti-jerk 변형", "anti-jerk variants"), api.driveline, self._show_driveline, self.d_btn)

    def run_stability(self):
        self._run_dl("driveline_stability", tr("감쇠 안정성", "damping stability"), api.driveline_stability,
                     self._show_stability, self.s_btn)

    def _show_driveline(self, res):
        self.d_btn.setEnabled(True)
        self.last_dl = res
        self.pl_dl.draw(F.fig_driveline, res, name="driveline_variants",
                        csv=lambda r=res: {**{"t_s": r["variants"]["off"]["sim"]["t_s"]},
                                           **{f"{n}_T_act": v["sim"]["T_act"] for n, v in r["variants"].items()},
                                           **{f"{n}_acc_l": v["sim"]["acc_l"] for n, v in r["variants"].items()}})
        if self.d_tabs.currentWidget() is not self.i_dl:
            self.d_tabs.setCurrentWidget(self.pl_dl)
        self.i_dl.read("driveline", tr("응답 (변형 비교)", "response (variants)"), driveline_insight, res)
        md = res["modal"]
        rows = [(tr("모드", "mode"), f"{md['f_n_Hz']:.3f} Hz · ζ {md['zeta']:.4f} · α {md['alpha']:.4f}")]
        w = res.get("window")
        if w:
            rows.append((tr("토크 창", "torque window"), f"[{w['T_min_Nm']:.1f}, {w['T_max_Nm']:.1f}] N·m — {w['source']}"))
        for n, v in res["variants"].items():
            m = v["metrics"]
            st = v.get("stability") or {}
            rows.append((n, f"{v['status']}" + (f" — {'; '.join(reason_label(r) for r in v['reasons'])}" if v["reasons"] else "")))
            rows.append(("   " + tr("지표", "metrics"),
                         f"t90 {fmt(m.get('t_to_90_s') and 1e3 * m['t_to_90_s'], 3)} ms · jerk "
                         f"{fmt(m.get('peak_vehicle_jerk_m_s3'), 4)} m/s³ · settle {fmt(m.get('t_settle_s') and 1e3 * m['t_settle_s'], 3)} ms "
                         f"· overshoot {fmt(m.get('overshoot') and 100 * m['overshoot'], 3)} % · correction rms "
                         f"{fmt(m.get('correction_rms_Nm'), 3)} N·m (mean {fmt(m.get('correction_mean_Nm'), 3)}) · clipped "
                         f"{fmt(m.get('clipped_fraction') and 100 * m['clipped_fraction'], 3)} %"
                         + (f" · ζcl {st['dominant_zeta']:.3f}" if st.get("dominant_zeta") is not None else "")))
            if v.get("extra_loss_energy_J") is not None:
                rows.append(("   " + tr("비용", "cost"), f"Δloss {v['extra_loss_energy_J']:.1f} J · Δwork {v['work_difference_J']:.1f} J"
                             + (f" — {v['loss_note']}" if v.get("loss_note") else "")))
            cl = v.get("clipping") or {}
            if cl.get("upper_s") or cl.get("lower_s"):
                rows.append(("   " + tr("클리핑", "clipping"), f"{tr('양', 'positive')} {1e3 * cl['upper_s']:.4g} ms · "
                                                                f"{tr('음 (회생 여유)', 'negative (regen reserve)')} {1e3 * cl['lower_s']:.4g} ms"))
            sn = v.get("sensing") or {}
            if sn:
                rows.append(("   " + tr("센서", "sensing"), f"{tr('최대 나이', 'max age')} {1e3 * sn['max_age_s']:.4g} ms · stale "
                                                             f"{1e3 * sn['stale_s']:.4g} ms · {tr('감쇠 불가', 'unavailable')} "
                                                             f"{1e3 * sn['unavailable_s']:.4g} ms · {tr('거짓 상대속도', 'false relative speed')} "
                                                             f"{sn['max_meas_error_rad_s']:.3g} rad/s"))
            sl = v.get("slew")
            if sl:
                rows.append(("   slew", f"{tr('상승', 'up')} {sl['max_up_Nm_per_s']:.4g} / {sl['limit_up_Nm_per_s']:.4g} · "
                                       f"{tr('하강', 'down')} {sl['max_down_Nm_per_s']:.4g} / {sl['limit_down_Nm_per_s']:.4g} N·m/s"))
            sf = v.get("safety")
            if sf:
                rows.append(("   " + tr("안전 반응 (별도 판정)", "safety reaction (judged separately)"),
                             f"{sf['status']} · {sf['reason']}"))
            rows += [("   " + tr("주석", "note"), n) for n in v.get("notes", [])]
        sl = res.get("slew_limits")
        if sl and sl.get("established"):
            rows.append((tr("전압 여유 (T1 운전점)", "voltage headroom (T1 point)"),
                         f"k_t {sl['kt_Nm_per_A']:.3f} N·m/A · L_q,diff {1e6 * sl['Lq_diff_H']:.0f} µH · "
                         f"{tr('여유', 'headroom')} +{sl['headroom_up_V']:.1f} / −{sl['headroom_down_V']:.1f} V → "
                         f"{sl['pos_Nm_per_s']:.4g} / {sl['neg_Nm_per_s']:.4g} N·m/s"))
        rows.append((tr("의미", "meaning"), res["meaning"]))
        self.k_dl.set_rows(rows)

    def _show_stability(self, res):
        self.s_btn.setEnabled(True)
        self.last_stab = res
        self.pl_st.draw(F.fig_driveline_stability, res, name="driveline_stability")
        if self.d_tabs.currentWidget() is not self.i_dl:
            self.d_tabs.setCurrentWidget(self.pl_st)
        self.i_dl.read("driveline_stability", tr("안정성", "stability"), stability_insight, res)
        self.k_dl.set_rows([(f"Kd {c['Kd']:g}", f"ζ (no delay) {c['zeta_undelayed']:.3f} · first destabilising delay "
                                                f"{fmt(c['first_destabilising_delay_ms'], 4)} ms (relative-speed reference)")
                            for c in res["continuous_relative_speed"]] + [(tr("의미", "meaning"), res["meaning"])])

    def redraw(self):
        for p in (self.pl_pol, self.pl_tim, self.pl_rip, self.pl_trn, self.pl_dl, self.pl_st, self.i_pwm, self.i_dl):
            p.redraw()
