"""Conducted EMI page (handoff P1-C / section 11; OEW addendum 5.1-5.2).

Screening: requirement profile + entered limit curve, PWM edge source, declared CM/DM network with the artificial
network under four CM return models, the rectangular-IF envelope estimate with the line-sum / Gaussian-IF bound
beside it, margins and the required attenuation per band (CM- or DM-dominated).  Measured trace:
PASS / FAIL / INDETERMINATE against the same profile.  OEW: winding zero sequence versus chassis common mode.
"""

from __future__ import annotations

import copy
import csv

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...plots import oew_hev_figures as F
from ...plots import schematics as SC
from ...plots.labels import reason_label
from ...insight.systems import emi_insight, emi_oew_insight
from ..widgets import (Cell, ConceptNote, KeyValueTable, NumTable, PlotPanel, check, combo, error_box, fmt, hint, number,
                       primary_button, table_with_buttons, reading_tab, with_reading)

COUPLING = {"y_capacitor": ("Y 커패시터 (절연 고장 시 레일 에너지)", "Y capacitor (rail energy at an insulation fault)"),
            "dm_resonance": ("DM 공진", "DM resonance"),
            "common_mode": ("공통모드 전압 스텝", "common-mode voltage step")}

NOTE_EMI = lambda: tr(
    "<b>전도성 EMI (HV 포트)</b>는 <b>소스 → 경로 → 수신기</b>로 계산합니다. 소스는 게이트 명령에 데드타임(턴온 지연)과 전류 부호에 따른 "
    "다이오드 클램프를 적용한 실제 스위칭 순서의 정확한 선스펙트럼입니다(데드타임보다 짧은 게이트 펄스는 사라지고 펄스가 뒤집히지 않음). "
    "과변조, 에지 램프 겹침, 미선언 최소 펄스 처리, 정수가 아닌 비동기 캐리어 비는 소스 유효 범위 밖입니다. 경로는 선언한 DC-link(ESR/ESL), "
    "Y-cap, 스위치노드·모터·케이블→섀시 C, 하네스, CM 초크, 인공회로망(AN)을 절점해석으로 CM·DM 동시(위상 포함) 풉니다. CM 전류가 "
    "어느 레일로 돌아가는지는 전류(轉流)에 달려 있어 4개 귀환 모델(중점 ½·½, 에지 부호: 상승→HV+·하강→HV−, 전부 HV+, 전부 HV−)을 "
    "모두 봅니다. 수신기: 직사각 IF·피크 검출기가 읽는 창 안 선들의 <b>포락 최대가 추정</b>(중점·에지 부호 모델 중 큰 쪽, 필요 감쇠의 "
    "근거)이고, 선들의 크기 합과 가우시안 IF 가중합(두 포트·4개 모델 중 최대)은 그 <b>상한</b>(판정 쪽)입니다 — 창당 선이 많을수록 "
    "상한이 추정보다 커집니다(CISPR QP/AV는 미모델). 수신 주파수를 연속으로 옮길 때 창 안의 선 집합이 바뀌는 모든 지점과 한계 꼭짓점을 "
    "<b>정확히 열거</b>해 대역 전체의 최대값·최소 여유를 구합니다(표시 격자는 그림일 뿐 판정이 아님).<br>"
    "<b>판정</b>: 요구 프로파일이 빠지면 REQUIREMENT_INCOMPLETE, 방법·단위가 모델 출력(AN 측정단 dBµV)과 다르면 비교 불가(UNKNOWN). "
    "한계가 없는 구간은 '승인된 공백'으로 선언하지 않는 한 미정의입니다. 보정 기록(근거·holdout·취득·오차 모델·유한한 오차 한계·주파수 구간·"
    "측정 set-up·경로망 식별자·소스 범위)이 완전하고 <b>이번 계산의 구성과 일치할 때만</b> 주장: 모든 수신 주파수에서 상한 + U+ ≤ L − M_d이면 "
    "FEASIBLE, 4개 모델 중 가장 작은 포락 − U− > L − M_d인 주파수(증인)가 있어야 INFEASIBLE, 그 외는 UNKNOWN(상한 초과만으로는 위반이 "
    "아님). 측정 trace는 "
    "<b>trace 자체의 취득 조건</b>(표현·검출기·RBW·IF 형상·dwell·set-up)으로 판정하며, 읽음값 사이의 손실까지 포함한 커버리지가 없으면 PASS가 아닙니다.",
    "<b>Conducted EMI (HV port)</b> is computed as <b>source -> path -> receiver</b>. Source: the exact line spectrum of "
    "the switching sequence built from the gate commands with dead time (turn-on delay) and the diode clamp by current "
    "sign (a gate pulse shorter than the dead time vanishes; pulses never reverse). Overmodulation, overlapping edge "
    "ramps, an undeclared minimum-pulse handling and a non-integer asynchronous carrier ratio are outside the source "
    "validity. Path: the declared DC link (ESR / ESL), Y capacitors, switch-node / motor / cable capacitance to chassis, "
    "harness, CM choke and the artificial network, solved by nodal analysis with CM and DM together; the rail that "
    "returns the CM current depends on the commutation, so four return models are evaluated (midpoint ½·½, edge sign: "
    "rising -> HV+ / falling -> HV-, all HV+, all HV-). Receiver: the <b>estimate</b> is the envelope maximum of the "
    "in-window lines a rectangular IF with a peak detector reads (the larger of the midpoint and edge-sign models; the "
    "basis of the required attenuation); the magnitude sum and the Gaussian-IF weighted sum (largest over both ports and "
    "the four models) are its <b>bound</b> (the claim side) - the more lines per window, the larger the gap (CISPR QP / "
    "AV not modelled). Every point where the window content changes and every limit vertex is <b>enumerated "
    "exactly</b>, giving the supremum and the minimum margin over the continuous band (the display grid is a plot, "
    "never the claim).<br><b>Judgement</b>: an incomplete profile is "
    "REQUIREMENT_INCOMPLETE; a method or unit other than the model's output (AN measuring port, dBuV) is not comparable "
    "(UNKNOWN). A band without a limit is undefined unless declared an approved gap. A claim needs a complete calibration "
    "record (evidence, hold-out, acquisition, error model, finite error bounds, frequency intervals, measurement set-up, "
    "path-network identity, source ranges) that <b>matches this evaluation's configuration</b>: FEASIBLE when bound + U+ "
    "<= L - M_d at every receiver frequency, INFEASIBLE only with a witness frequency where the smallest envelope over the "
    "four models - U- > L - M_d, otherwise UNKNOWN (exceeding the upper bound alone is not a violation). A measured "
    "trace is judged with <b>its own "
    "acquisition</b> (representation, detector, RBW, IF shape, dwell, set-up); without coverage between the readings "
    "(their loss included) it is not a PASS.")


def _DETECTORS():
    return [(tr("피크", "peak"), "peak"), (tr("준첨두 (QP)", "quasi-peak"), "quasi_peak"), (tr("평균", "average"), "average")]


def _gaps(text: str) -> list:
    """'0.3-0.53, 1.8-5.9' (MHz) -> [[f_lo_Hz, f_hi_Hz], ...]"""
    out = []
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        a, b = part.split("-", 1)
        out.append([float(a) * 1e6, float(b) * 1e6])
    return out


def _task(fn):
    def run(progress, body):
        progress(0.1, tr("계산 중", "computing"), 1.0)      # the engine's loops, if any, move the rest of the bar
        return fn(body)
    return run


def _scroll(w):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    sc.setMinimumWidth(390)
    return sc


class EmiPage(QWidget):
    workspace_data = ("measured", "cal_binding")        # an imported trace and a calibration binding reach the request

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last = self.last_oew = None
        self.measured = None
        self.tabs = QTabWidget()
        self.tabs.addTab(self._main_tab(), tr("전도성 방출 (HV 포트)", "conducted emission (HV port)"))
        self.tabs.addTab(self._oew_tab(), tr("OEW 공통모드 vs 영상분", "OEW common mode vs zero sequence"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.tabs)

    # ------------------------------------------------------------------ main tab
    def _main_tab(self):
        ex = self.win.state.example("EMI")
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("운전점·소스", "operating point · source"))
        f = QFormLayout(g)
        self.e_n = number(ex["speed_rpm"], 1, 30000, "rpm", 0, 500)
        self.e_T = number(ex["torque_Nm"], -5000, 5000, "N·m", 1, 10)
        self.e_vdc = number(ex["Vdc_V"], 1, 2000, "V", 1, 10)
        sc = ex["source"]
        self.e_fsw = number(sc["fsw_kHz"], 0.5, 200, "kHz", 2, 1)
        self.e_tr = number(sc["t_rise_ns"], 1, 5000, "ns", 1, 5)
        self.e_tf = number(sc["t_fall_ns"], 1, 5000, "ns", 1, 5)
        self.e_td = number(sc["t_dead_us"], 0, 20, "µs", 3, 0.1)
        self.e_carrier = combo([(tr("비동기 (고정 fsw)", "asynchronous (fixed fsw)"), "asynchronous"),
                                (tr("동기 (fsw = 정수 × fe)", "synchronous (fsw = integer × fe)"), "synchronous")],
                               sc.get("carrier", "asynchronous"))
        self.e_minp = number(float(sc.get("min_pulse_us") or 0.0), 0, 50, "µs", 3, 0.1)
        self.e_minp_pol = combo([(tr("없음 (모든 펄스 출력)", "none (every pulse issued)"), "none"),
                                 (tr("제거 (캐리어 주기별 듀티 클램프)", "drop (duty clamped per carrier period)"), "drop"),
                                 (tr("미상 (처리 방식 미선언)", "unknown (handling not declared)"), "unknown")],
                                sc.get("min_pulse_policy", "none"))
        self.e_src_basis = QLineEdit(sc["basis"])
        for lab, w in ((tr("속도", "speed"), self.e_n), (tr("토크", "torque"), self.e_T), ("Vdc", self.e_vdc),
                       (tr("스위칭 주파수", "switching frequency"), self.e_fsw), (tr("캐리어", "carrier"), self.e_carrier),
                       (tr("상승 시간 (선언)", "rise time (declared)"), self.e_tr),
                       (tr("하강 시간 (선언)", "fall time (declared)"), self.e_tf), (tr("데드타임", "dead time"), self.e_td),
                       (tr("최소 펄스", "minimum pulse"), self.e_minp), (tr("최소 펄스 처리", "minimum-pulse handling"), self.e_minp_pol),
                       (tr("근거", "basis"), self.e_src_basis)):
            f.addRow(lab, w)
        v.addWidget(g)
        nw = ex["network"]
        g = QGroupBox(tr("경로 망 (선언값)", "path network (declared)"))
        f = QFormLayout(g)
        self.net = {}
        spec = (("C_dc_uF", "C_dc", "µF", 1.0), ("ESR_dc_mohm", "ESR_dc", "mΩ", 0.1), ("ESL_dc_nH", "ESL_dc", "nH", 1.0),
                ("C_y_nF", tr("C_y (레일당)", "C_y (per rail)"), "nF", 10.0), ("L_y_nH", tr("Y-cap 장착 L", "Y-cap mounting L"), "nH", 1.0),
                ("R_y_mohm", "R_y", "mΩ", 1.0), ("C_par_nF", tr("스위치노드·모터·케이블→섀시 C", "switch node / motor / cable C"), "nF", 0.1),
                ("R_par_ohm", "R_par", "Ω", 0.1), ("L_par_nH", "L_par", "nH", 10.0),
                ("R_h_mohm", tr("하네스 R (라인당)", "harness R (per line)"), "mΩ", 1.0), ("L_h_uH", tr("하네스 L (라인당)", "harness L (per line)"), "µH", 0.1),
                ("L_ch_uH", tr("CM 초크 L", "CM choke L"), "µH", 10.0), ("k_ch", tr("초크 결합 k", "choke coupling k"), "", 0.01),
                ("an_L_uH", "AN L", "µH", 1.0), ("an_R_meas_ohm", tr("AN 측정 R", "AN measuring R"), "Ω", 1.0),
                ("an_C_coup_nF", tr("AN 결합 C", "AN coupling C"), "nF", 10.0), ("an_C_sup_uF", tr("AN 전원측 C", "AN supply C"), "µF", 0.1),
                ("R_bat_mohm", tr("전원 R", "source R"), "mΩ", 1.0))
        for key, lab, unit, step in spec:
            w = number(float(nw[key]), 0, 1e7, unit, 4 if step < 0.1 else 3, step,
                       special=tr("없음", "none") if key == "L_ch_uH" else None)
            self.net[key] = w
            f.addRow(lab, w)
        self.e_valid_on = check(tr("검증된 최고 주파수 선언", "declare the validated upper frequency"), False)
        self.e_valid = number(30.0, 0.1, 1000, "MHz", 1, 1)
        self.e_net_basis = QLineEdit(nw["basis"])
        f.addRow("", self.e_valid_on)
        f.addRow(tr("유효 상한", "valid up to"), self.e_valid)
        f.addRow(tr("근거", "basis"), self.e_net_basis)
        v.addWidget(g)
        pr = ex["profile"]
        g = QGroupBox(tr("요구 프로파일 (빠지면 REQUIREMENT_INCOMPLETE)", "requirement profile (missing -> REQUIREMENT_INCOMPLETE)"))
        f = QFormLayout(g)
        self.prof = {}
        for key, lab in (("standard", tr("규격", "standard")), ("edition", tr("판", "edition")),
                         ("customer_revision", tr("고객 규격 개정", "customer spec revision")), ("curve_id", tr("클래스/곡선 ID", "class / curve ID")),
                         ("port", tr("포트", "port")), ("network", tr("AN 정의", "AN definition")),
                         ("fixture", tr("치구·하네스·본딩", "fixture / harness / bonding")), ("operating_condition", tr("운전 조건", "operating condition"))):
            w = QLineEdit(str(pr.get(key, "")))
            self.prof[key] = w
            f.addRow(lab, w)
        self.e_method = combo([(tr("전압법 (AN 측정단) — 모델 출력", "voltage method (AN measuring port) — the model's output"), "voltage_AN"),
                               (tr("전류 프로브 (이 모델로 예측 안 함)", "current probe (not predicted by this model)"), "current_probe")],
                              pr.get("method", "voltage_AN"))
        self.e_det = combo(_DETECTORS(), pr["detector"])
        self.e_rbw = number(pr["rbw_Hz"] / 1e3, 0.01, 1e4, "kHz", 2, 1)
        self.e_res = number(pr["design_reserve_dB"], 0, 60, "dB", 1, 1)
        self.e_rule = QLineEdit(str(pr.get("decision_rule") or ""))
        self.e_rule.setPlaceholderText(tr("합의된 판정 규칙 (비우면 워크벤치 기본 보수 규칙)", "agreed decision rule (empty: the workbench's guarded default)"))
        f.addRow(tr("방법", "method"), self.e_method)
        f.addRow(tr("검출기", "detector"), self.e_det)
        f.addRow("RBW", self.e_rbw)
        f.addRow(tr("설계 여유 M_d", "design reserve M_d"), self.e_res)
        f.addRow(tr("판정 규칙", "decision rule"), self.e_rule)
        lim = ex["limit"]
        self.e_lim = NumTable([tr("주파수 [MHz]", "frequency [MHz]"), tr("한계 [dBµV]", "limit [dBuV]")],
                              [[p[0] / 1e6, p[1]] for p in lim["points"]], min_height=110)
        f.addRow(QLabel(tr("한계 곡선 (승인된 원문에서 입력; 예시는 규격이 아님)", "limit curve (enter from the approved source; the example is not a standard)")))
        f.addRow(table_with_buttons(self.e_lim))
        self.e_lim_src = QLineEdit(lim["source"])
        f.addRow(tr("곡선 출처", "curve source"), self.e_lim_src)
        self.e_gaps = QLineEdit(", ".join(f"{a / 1e6:g}-{b / 1e6:g}" for a, b in lim.get("gaps_Hz") or []))
        self.e_gaps.setPlaceholderText(tr("요구 없음으로 승인된 구간 [MHz], 예: 0.3-0.53, 1.8-5.9", "bands approved without a requirement [MHz], e.g. 0.3-0.53, 1.8-5.9"))
        f.addRow(tr("선언 공백", "declared gaps"), self.e_gaps)
        v.addWidget(g)
        g = QGroupBox(tr("보정·결합", "calibration · coupling"))
        f = QFormLayout(g)
        self.e_cal_on = check(tr("보정 기록 선언 (없으면 스크리닝)", "declare a calibration record (else screening)"), False)
        self.e_cal_ev = QLineEdit()
        self.e_cal_ev.setPlaceholderText(tr("상관 보고서 ID", "correlation report ID"))
        self.e_cal_ho = QLineEdit()
        self.e_cal_ho.setPlaceholderText(tr("holdout 근거 (맞춤에 쓰지 않은 운전점·주파수)", "hold-out evidence (points / frequencies not used to fit)"))
        self.e_cal_acq = QLineEdit()
        self.e_cal_acq.setPlaceholderText(tr("취득 조건 (검출기·RBW·dwell·스캔)", "acquisition (detector, RBW, dwell, scan)"))
        self.e_cal_em = QLineEdit()
        self.e_cal_em.setPlaceholderText(tr("오차 모델 (U가 |E_측정 − E_모델|을 어떻게 제한하는가)", "error model (how U bounds |E_meas - E_model|)"))
        self.e_cal_u = number(3.0, 0, 40, "dB", 2, 0.5)
        self.e_cal_ul = number(3.0, 0, 40, "dB", 2, 0.5)
        self.e_cal_zero = QLineEdit()
        self.e_cal_zero.setPlaceholderText(tr("U = 0일 때 필수: 오차가 없는 이유", "required when a bound is 0: why the error vanishes"))
        self.e_cal_lo = number(0.15, 0.001, 1000, "MHz", 3, 0.05)
        self.e_cal_hi = number(30.0, 0.01, 1000, "MHz", 2, 1)
        self.cal_binding = None
        self.e_cal_bind = QPushButton(tr("마지막 결과의 구성에 결속", "bind to the last result's configuration"))
        self.e_cal_bind.clicked.connect(self.bind_calibration)
        self.e_cal_lab = QLabel(tr("구성에 결속 안 됨 (결속 전에는 적용 불가)", "not bound to a configuration (not applicable until bound)"))
        self.e_cal_lab.setWordWrap(True)
        self.e_ey_on = check(tr("Y-cap 허용 에너지 선언", "declare allowed Y-cap energy"), False)
        self.e_ey = number(0.2, 0, 100, "J", 3, 0.05)
        self.e_fc = number(ex["f_control_Hz"], 1, 1e6, "Hz", 0, 100)
        self.e_lo = number(0.15, 0.001, 1000, "MHz", 3, 0.05)
        self.e_hi = number(30.0, 0.01, 1000, "MHz", 2, 1)
        for lab, w in (("", self.e_cal_on), (tr("근거", "evidence"), self.e_cal_ev), ("holdout", self.e_cal_ho),
                       (tr("취득", "acquisition"), self.e_cal_acq), (tr("오차 모델", "error model"), self.e_cal_em),
                       (tr("모델 오차 상한 U+", "model-error bound U+"), self.e_cal_u),
                       (tr("모델 오차 하한 U−", "model-error bound U−"), self.e_cal_ul),
                       (tr("U = 0 근거", "zero-bound basis"), self.e_cal_zero),
                       (tr("보정 구간 하한", "calibrated from"), self.e_cal_lo), (tr("보정 구간 상한", "calibrated to"), self.e_cal_hi),
                       ("", self.e_cal_bind), ("", self.e_cal_lab),
                       ("", self.e_ey_on), (tr("허용 에너지", "allowed energy"), self.e_ey),
                       (tr("제어 대역", "control bandwidth"), self.e_fc), (tr("대역 하한", "band from"), self.e_lo),
                       (tr("대역 상한", "band to"), self.e_hi)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("측정 trace (선택)", "measured trace (optional)"))
        f = QFormLayout(g)
        self.e_meas_lab = QLabel(tr("가져온 trace 없음", "no trace imported"))
        self.e_meas_lab.setWordWrap(True)
        row = QHBoxLayout()
        b = QPushButton(tr("CSV 가져오기 (f_Hz, dBµV[, noise])", "import CSV (f_Hz, dBuV[, noise])"))
        b.clicked.connect(self.import_trace)
        b2 = QPushButton(tr("지우기", "clear"))
        b2.clicked.connect(self.clear_trace)
        row.addWidget(b)
        row.addWidget(b2)
        self.e_U = number(3.0, 0, 20, "dB", 1, 0.5)
        self.m_rep = combo([(tr("원시 스윕", "raw sweep"), "raw_sweep"),
                            (tr("최대 포락 압축", "max-envelope (compressed)"), "max_envelope"),
                            (tr("최종 측정 목록", "final-measurement list"), "final_list")], "raw_sweep")
        self.m_det = combo(_DETECTORS(), "peak")
        self.m_rbw = number(9.0, 0.01, 1e4, "kHz", 2, 1)
        self.m_if = combo([(tr("가우시안", "Gaussian"), "gaussian"), (tr("사각", "rectangular"), "rectangular"),
                           (tr("미선언", "not declared"), "")], "gaussian")
        self.m_dwell = number(10.0, 0, 1e5, "ms", 2, 1)
        self.m_raw = number(4.5, 0, 1e4, "kHz", 2, 0.5)
        self.m_peak = QLineEdit()
        self.m_peak.setPlaceholderText(tr("최대 포락: 피크 보존 근거", "max-envelope: peak-preservation evidence"))
        self.m_corr = QLineEdit()
        self.m_corr.setPlaceholderText(tr("적용한 보정 (AN 계수·케이블 손실·리미터) 또는 불필요한 이유",
                                          "corrections applied (AN factor, cable loss, limiter) or why none apply"))
        f.addRow(self.e_meas_lab)
        f.addRow(row)
        f.addRow(tr("측정 불확도 U", "measurement uncertainty U"), self.e_U)
        f.addRow(QLabel(tr("trace 자체의 취득 조건 (프로파일과 별개로 trace에 결속)", "the trace's own acquisition (bound to the trace, not the profile)")))
        for lab, w in ((tr("표현", "representation"), self.m_rep), (tr("검출기", "detector"), self.m_det), ("RBW", self.m_rbw),
                       (tr("IF 형상", "IF shape"), self.m_if), ("dwell", self.m_dwell),
                       (tr("원시 스캔 간격 (포락)", "raw scan step (envelope)"), self.m_raw), ("", self.m_peak),
                       (tr("보정", "corrections"), self.m_corr)):
            f.addRow(lab, w)
        v.addWidget(g)
        self.e_btn = primary_button(tr("EMI 계산", "compute EMI"))
        self.e_btn.clicked.connect(self.run)
        v.addWidget(self.e_btn)
        v.addWidget(hint(tr("예시 망·곡선·프로파일은 합성 값입니다.", "The example network, curve and profile are synthetic.")))
        v.addWidget(ConceptNote(NOTE_EMI()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(('emi',), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.e_tabs = QTabWidget()
        self.p_spec = PlotPanel(hint=tr("스펙트럼·한계·필요 감쇠", "spectrum, limit and required attenuation"))
        self.p_net = PlotPanel(min_height=260)
        self.p_meas = PlotPanel()
        self.e_tabs.addTab(self.p_spec, tr("스펙트럼·필요 감쇠", "spectrum · required attenuation"))
        self.e_tabs.addTab(self.p_net, tr("등가 회로", "equivalent network"))
        self.e_tabs.addTab(self.p_meas, tr("측정 trace 판정", "measured-trace verdict"))
        self.i_emi = reading_tab(self.e_tabs, tr(
            "계산하면 해석이 표시됩니다 — 최소 여유와 그 주파수, 대역별 필요 감쇠와 CM·DM 지배 경로, 판정 근거(정확 열거), 소스와 함께 볼 결합 "
            "효과(Y 커패시터 에너지, DM 공진, CM dv/dt).",
            "Run to read the screening — the minimum margin and where, the required attenuation per band and whether CM or "
            "DM dominates, the evidence (exact enumeration), the coupled effects to check alongside (Y-capacitor energy, DM "
            "resonance, CM dv/dt)."))
        self.t_emi = KeyValueTable()
        rl.addWidget(self.e_tabs, 3)
        rl.addWidget(self.t_emi, 2)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([410, 1050])
        return split

    def import_trace(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("측정 trace CSV", "measured trace CSV"), "", "CSV (*.csv *.txt)")
        if path:
            try:
                self.load_trace_csv(path)
            except Exception as exc:  # noqa: BLE001
                error_box(self, tr("가져오기 실패", "import failed"), str(exc))

    def load_trace_csv(self, path):
        """(f_Hz, level[, noise]) rows; optional header lines '# key: value' carry the trace's acquisition
        (representation, detector, rbw_Hz, if_shape, dwell_s, raw_step_Hz, peak_preservation, corrections) and set-up
        (port, method, network, fixture, operating_condition).  A set-up the file does not state is taken from the
        profile AS IT IS NOW (the operator's statement at import) and stays with the trace."""
        f, x, nf, head = [], [], [], {}
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.reader(fh):
                if row and row[0].lstrip().startswith("#"):
                    line = ",".join(row).lstrip()[1:]
                    if ":" in line:
                        k, v = line.split(":", 1)
                        head[k.strip()] = v.strip()
                    continue
                try:
                    a, b_ = float(row[0]), float(row[1])
                except (ValueError, IndexError):
                    continue
                f.append(a)
                x.append(b_)
                if len(row) > 2:
                    try:
                        nf.append(float(row[2]))
                    except ValueError:
                        pass
        if len(f) < 2:
            raise ValueError(tr("(f_Hz, dBµV) 행이 2개 이상 필요합니다", "need at least two (f_Hz, dBuV) rows"))
        prof = self._profile_fields()
        setup = {k: head.get(k, prof.get(k, "")) for k in ("port", "method", "network", "fixture", "operating_condition")}
        for key, w in (("representation", self.m_rep), ("detector", self.m_det), ("if_shape", self.m_if)):
            if key in head:
                i = w.findData(head[key])
                if i >= 0:
                    w.setCurrentIndex(i)
        for key, w, scale in (("rbw_Hz", self.m_rbw, 1e-3), ("dwell_s", self.m_dwell, 1e3), ("raw_step_Hz", self.m_raw, 1e-3)):
            try:
                w.setValue(float(head[key]) * scale)
            except (KeyError, ValueError):
                pass
        if "peak_preservation" in head:
            self.m_peak.setText(head["peak_preservation"])
        if "corrections" in head:
            self.m_corr.setText(head["corrections"])
        self.measured = {"f_Hz": f, "level_dB": x, "noise_floor_dB": nf if len(nf) == len(f) else None, "setup": setup}
        self.e_meas_lab.setText(tr(f"{len(f)}점 ({f[0] / 1e6:.3g}–{f[-1] / 1e6:.3g} MHz)", f"{len(f)} points "
                                   f"({f[0] / 1e6:.3g}-{f[-1] / 1e6:.3g} MHz)") + f" · {path}\n"
                                + tr("set-up: ", "set-up: ") + " · ".join(f"{k} {v}" for k, v in setup.items()))

    def clear_trace(self):
        self.measured = None
        self.e_meas_lab.setText(tr("가져온 trace 없음", "no trace imported"))

    def _profile_fields(self) -> dict:
        prof = {k: w.text().strip() for k, w in self.prof.items()}
        prof.update({"method": self.e_method.currentData(), "detector": self.e_det.currentData(),
                     "rbw_Hz": self.e_rbw.value() * 1e3, "design_reserve_dB": self.e_res.value(),
                     "decision_rule": self.e_rule.text().strip() or None})
        return prof

    def bind_calibration(self):
        if not self.last:
            error_box(self, tr("결속 불가", "cannot bind"), tr("먼저 EMI를 계산하세요 (결속은 계산된 구성에 합니다)",
                                                             "compute EMI first (a record binds to a computed configuration)"))
            return
        self.cal_binding = copy.deepcopy(self.last["configuration"])
        self._cal_label()

    def _cal_label(self):
        c = self.cal_binding
        if not c:
            self.e_cal_lab.setText(tr("구성에 결속 안 됨 (결속 전에는 적용 불가)", "not bound to a configuration (not "
                                                                        "applicable until bound)"))
            return
        src = c["source_ranges"]
        self.e_cal_lab.setText(tr("결속: ", "bound: ") + f"network {c['network_sha256'][:12]}…, "
                               f"Vdc {fmt(src['Vdc_V'][0])} V, |i| {fmt(src['I_pk_A'][0])} A, fe {fmt(src['fe_Hz'][0])} Hz, "
                               f"fsw {fmt(src['fsw_Hz'][0] / 1e3)} kHz, tr/tf {fmt(src['t_rise_s'][0] * 1e9)}/{fmt(src['t_fall_s'][0] * 1e9)} ns, "
                               f"{c['setup']['method']} / {c['setup']['detector']} / RBW {c['setup']['rbw_Hz']}"
                               + tr(" — 입력이 바뀌면 적용되지 않습니다", " — any changed input makes it inapplicable"))

    def after_workspace_restore(self):
        """The labels of the restored trace and binding (the file path is not part of the workspace)."""
        self._cal_label()
        m = self.measured
        if not m:
            self.e_meas_lab.setText(tr("가져온 trace 없음", "no trace imported"))
            return
        f = m["f_Hz"]
        self.e_meas_lab.setText(tr(f"{len(f)}점 ({f[0] / 1e6:.3g}–{f[-1] / 1e6:.3g} MHz) · 작업 공간에서 복원",
                                   f"{len(f)} points ({f[0] / 1e6:.3g}-{f[-1] / 1e6:.3g} MHz) · restored from the "
                                   f"workspace") + "\n" + tr("set-up: ", "set-up: ")
                                + " · ".join(f"{k} {v}" for k, v in (m.get("setup") or {}).items()))

    def apply_project(self, _project=None):
        """Switching source (controller: fsw, gate edges, dead time, carrier, minimum pulse) and the HV network /
        test set-up from the active project; operating point, profile, limit and traces stay."""
        ex = self.win.state.example("EMI")
        sc, nw = ex["source"], ex["network"]
        self.e_fsw.setValue(float(sc["fsw_kHz"]))
        self.e_tr.setValue(float(sc["t_rise_ns"]))
        self.e_tf.setValue(float(sc["t_fall_ns"]))
        self.e_td.setValue(float(sc.get("t_dead_us") or 0.0))
        for w, v in ((self.e_carrier, sc.get("carrier", "asynchronous")),
                     (self.e_minp_pol, sc.get("min_pulse_policy", "none"))):
            i = w.findData(v)
            if i >= 0:
                w.setCurrentIndex(i)
        self.e_minp.setValue(float(sc.get("min_pulse_us") or 0.0))
        self.e_src_basis.setText(str(sc.get("basis", "")))
        for k, w in self.net.items():
            if nw.get(k) is not None:
                w.setValue(float(nw[k]))
        self.e_valid_on.setChecked(nw.get("validated_up_to_MHz") is not None)
        if nw.get("validated_up_to_MHz") is not None:
            self.e_valid.setValue(float(nw["validated_up_to_MHz"]))
        self.e_net_basis.setText(str(nw.get("basis", "")))

    def body(self) -> dict:
        b = self.win.state.example("EMI")            # product data of the active project; widgets overlay it
        b.update(self.win.state.body())
        b.update({"speed_rpm": self.e_n.value(), "torque_Nm": self.e_T.value(), "Vdc_V": self.e_vdc.value()})
        b["source"] = {**b["source"], "fsw_kHz": self.e_fsw.value(), "t_rise_ns": self.e_tr.value(),
                       "t_fall_ns": self.e_tf.value(),
                       "t_dead_us": self.e_td.value(), "carrier": self.e_carrier.currentData(),
                       "min_pulse_us": self.e_minp.value(), "min_pulse_policy": self.e_minp_pol.currentData(),
                       "basis": self.e_src_basis.text().strip()}
        b["network"] = {**b["network"], **{k: w.value() for k, w in self.net.items()}}
        b["network"]["basis"] = self.e_net_basis.text().strip()
        b["network"]["validated_up_to_MHz"] = self.e_valid.value() if self.e_valid_on.isChecked() else None
        b["profile"] = self._profile_fields()
        pts = self.e_lim.values()
        b["limit"] = ({"points": [[r[0] * 1e6, r[1]] for r in pts], "unit": "dBuV", "detector": self.e_det.currentData(),
                       "source": self.e_lim_src.text().strip(), "gaps_Hz": _gaps(self.e_gaps.text())}
                      if len(pts) >= 2 else None)
        b["calibration"] = ({"evidence": self.e_cal_ev.text().strip(), "holdout": self.e_cal_ho.text().strip(),
                             "acquisition": self.e_cal_acq.text().strip(), "error_model": self.e_cal_em.text().strip(),
                             "U_upper_dB": self.e_cal_u.value(), "U_lower_dB": self.e_cal_ul.value(),
                             "zero_uncertainty_basis": self.e_cal_zero.text().strip(),
                             "f_intervals_Hz": [[self.e_cal_lo.value() * 1e6, self.e_cal_hi.value() * 1e6]],
                             **copy.deepcopy(self.cal_binding or {})}
                            if self.e_cal_on.isChecked() else None)
        b["E_y_allowed_J"] = self.e_ey.value() if self.e_ey_on.isChecked() else None
        b["f_control_Hz"] = self.e_fc.value()
        b["band_MHz"] = [self.e_lo.value(), self.e_hi.value()]
        if self.measured:
            meta = {"representation": self.m_rep.currentData(), "detector": self.m_det.currentData(), "unit": "dBuV",
                    "rbw_Hz": self.m_rbw.value() * 1e3, "if_shape": self.m_if.currentData() or None,
                    "dwell_s": self.m_dwell.value() / 1e3, "raw_step_Hz": self.m_raw.value() * 1e3,
                    "peak_preservation": self.m_peak.text().strip(), "corrections": self.m_corr.text().strip(),
                    **self.measured["setup"]}
            b["measured"] = {k: v for k, v in self.measured.items() if k != "setup"}
            b["measured"].update({"U_meas_dB": self.e_U.value(), "meta": meta})
        else:
            b["measured"] = None
        return b

    def run(self):
        try:
            body = self.body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.e_btn.setEnabled(False)
        self.win.runner.run("emi", tr("EMI", "EMI"), _task(api.emi), self._show, body, on_error=self._err)

    def _err(self, msg, tb):
        self.e_btn.setEnabled(True)
        self.o_btn.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _show(self, res):
        self.e_btn.setEnabled(True)
        self.last = res
        self.p_spec.draw(F.fig_emi_screening, res, name="emi_screening",
                         csv=lambda res=res: {k: res[k] for k in ("grid_Hz", "E_dBuV", "E_bound_dBuV", "lines_per_window",
                                                                   "plus_dBuV", "minus_dBuV", "from_cm_source_dBuV",
                                                                   "from_dm_source_dBuV", "limit_dBuV", "margin_est_dB",
                                                                   "margin_dB", "required_attenuation_dB",
                                                                   "required_attenuation_bound_dB") if k in res})
        self.p_net.draw(SC.fig_emi_network, res["network"], name="emi_network")
        self.p_meas.draw(F.fig_emi_measured, res, name="emi_measured")
        c = res["claim"]
        import numpy as np
        A = np.asarray(res["required_attenuation_dB"], dtype=float)
        g = np.asarray(res["grid_Hz"])
        so = res["source"]
        why = f" [{', '.join(reason_label(r) for r in c['reasons'])}]" if c.get("reasons") else ""
        rows = [(tr("판정", "claim"), f"{c['status']} — {c['detail']}{why}"),
                (tr("운전점", "operating point"), f"{fmt(res['operating_point']['speed_rpm'])} rpm, {fmt(res['operating_point']['torque_Nm'])} N·m, "
                                                 f"|i| {fmt(res['operating_point']['i_peak_A'])} A, m {fmt(res['operating_point']['modulation_index'], 3)}, "
                                                 f"carrier ratio {res['carrier_ratio']}"),
                (tr("스위칭 주파수", "switching frequency"), tr(f"요청 {fmt(so['fsw_requested_kHz'])} kHz → 평가 {fmt(so['fsw_used_kHz'])} kHz ({so['carrier']})",
                                                               f"requested {fmt(so['fsw_requested_kHz'])} kHz -> evaluated {fmt(so['fsw_used_kHz'])} kHz ({so['carrier']})")),
                (tr("소스 유효성", "source validity"), "OK" if so["validity"]["ok"] else "; ".join(so["validity"]["problems"]))]
        bd = res.get("band") or {}
        rows.append((tr("대역 평가", "band evaluation"),
                     (tr(f"정확 열거 (선 {bd.get('lines')}개, 해 잔차 {bd.get('solve_residual', 0):.1e})",
                         f"exact enumeration ({bd.get('lines')} lines, solve residual {bd.get('solve_residual', 0):.1e})")
                      if bd.get("certified") else tr("표본 격자만 (", "sampled grid only (") + str(bd.get("reason", "")) + ")")))
        for d in res.get("domain", []):
            txt = d["status"]
            if d.get("min_margin_dB") is not None:
                if d.get("E_est_sup_dBuV") is not None:
                    txt += (f" · {tr('추정', 'estimate')} {d['E_est_sup_dBuV']:.2f} dBµV @ {d['f_E_est_sup_Hz'] / 1e6:.4g} MHz, "
                            f"{tr('최소 여유', 'min margin')} {d['min_margin_est_dB']:.2f} dB")
                txt += (f" · {tr('상한', 'bound')} {d['E_sup_dBuV']:.2f} dBµV @ {d['f_E_sup_Hz'] / 1e6:.4g} MHz, "
                        f"{tr('최소 여유', 'min margin')} {d['min_margin_dB']:.2f} dB · {tr('창당 선', 'lines per window')} "
                        f"≤ {d.get('lines_per_window_max')}")
            if d["reasons"] or d["claim_reasons"]:
                txt += " — " + "; ".join(reason_label(r) for r in d["reasons"] + d["claim_reasons"])
            rows.append((f"{d['lo_Hz'] / 1e6:g}–{d['hi_Hz'] / 1e6:g} MHz", txt))
        cal = res.get("calibration") or {}
        if cal.get("declared"):
            rows.append((tr("보정 기록", "calibration record"),
                         tr("적용", "applicable") if cal["applicable"] else
                         "; ".join((cal.get("problems") or []) + (cal.get("mismatches") or []))))
        if np.any(np.isfinite(A)):
            j = int(np.nanargmax(A))
            Ab = np.asarray(res.get("required_attenuation_bound_dB", A), dtype=float)
            rows.append((tr("최대 필요 감쇠 (추정)", "max required attenuation (estimate)"),
                         f"{A[j]:.1f} dB at {g[j] / 1e6:.3g} MHz ({res['dominant_source'][j]}-dominated); "
                         + tr("상한 기준", "on the bound") + f" {np.nanmax(Ab):.1f} dB"))
            for lo, hi in ((0.15e6, 0.5e6), (0.5e6, 2e6), (2e6, 10e6), (10e6, 30e6)):
                m = (g >= lo) & (g <= hi)
                if m.any() and np.any(np.isfinite(A[m])):
                    cm = int(np.sum(np.asarray(res["dominant_source"])[m] == "CM"))
                    rows.append((f"{lo / 1e6:g}–{hi / 1e6:g} MHz", f"A_req max {np.nanmax(A[m]):.1f} dB · CM {cm}/{int(m.sum())} "
                                                                    f"{tr('격자점', 'grid points')}"))
        if res.get("profile_missing"):
            rows.append((tr("프로파일 누락", "profile missing"), ", ".join(res["profile_missing"])))
        for k, v in (res.get("coupling") or {}).items():
            rows.append((Cell(tr(*COUPLING[k]) if k in COUPLING else k, k), " · ".join(f"{kk}: {fmt(vv) if isinstance(vv, float) else vv}" for kk, vv in v.items())))
        ms = res.get("measured")
        if ms:
            rows.append((tr("측정 trace", "measured trace"), f"{ms['verdict']} — {ms['reason']}"))
            rows.append((tr("  적합 / 여유", "  compliance / reserve"),
                         f"{ms['compliance']['verdict']} / {ms['reserve']['verdict']} · "
                         + tr("판정 규칙: ", "decision rule: ") + ms["decision_rule"]["rule"]
                         + ("" if ms["decision_rule"]["agreed"] else tr(" (합의 안 됨)", " (not agreed)"))))
        for n in res.get("notes", []):
            rows.append((tr("주의", "note"), n))
        self.t_emi.set_rows(rows)
        self.i_emi.read("emi", tr("전도성 EMI", "conducted EMI"), emi_insight, res)
        if ms and self.e_tabs.currentWidget() is not self.i_emi:
            self.e_tabs.setCurrentWidget(self.p_meas)

    # ------------------------------------------------------------------ OEW tab
    def _oew_tab(self):
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        ex = self.win.state.example("OEW")
        g = QGroupBox(tr("공통 bus OEW (OEW 예시 토폴로지)", "common-bus OEW (OEW example topology)"))
        f = QFormLayout(g)
        self.o_V = number(ex["topology"]["VA_V"], 1, 2000, "V", 1, 10)
        self.o_n = number(ex["speed_rpm"], 1, 30000, "rpm", 0, 500)
        self.o_T = number(ex["torque_Nm"], -5000, 5000, "N·m", 1, 10)
        self.o_fsw = number(ex["fsw_kHz"], 0.5, 200, "kHz", 2, 1)
        self.o_te = number(50.0, 1, 5000, "ns", 1, 5)
        for lab, w in (("V", self.o_V), (tr("속도", "speed"), self.o_n), (tr("토크", "torque"), self.o_T),
                       (tr("스위칭 주파수", "switching frequency"), self.o_fsw), (tr("에지 시간", "edge time"), self.o_te)):
            f.addRow(lab, w)
        v.addWidget(g)
        self.o_btn = primary_button(tr("u0 vs 공통모드 계산", "compute u0 vs common mode"))
        self.o_btn.clicked.connect(self.run_oew)
        v.addWidget(self.o_btn)
        v.addWidget(ConceptNote(tr(
            "권선 영상분 u0 = v_cmA − v_cmB는 L0를 통해 순환 i0를 만들고, 섀시 기준 공통모드 v_cm6 = (v_cmA + v_cmB)/2는 기생 C를 통해 "
            "섀시 변위전류를 만듭니다. 캐리어 동위상은 u0를, 180° 교차는 v_cm6을 줄이는 경향이 있으나 서로 다른 claim입니다. "
            "영상분 제거(u0 = 0) 상태쌍만 써도 v_cm6은 V/3 계단으로 변합니다. 두 브리지 직류측 전류의 합성 리플은 S_AA + S_BB + 2Re S_AB이며 "
            "합이 작아도 각 로컬 커패시터 전류는 작지 않을 수 있습니다.",
            "The winding zero sequence u0 = v_cmA - v_cmB drives circulating i0 through L0; the chassis-referenced common mode "
            "v_cm6 = (v_cmA + v_cmB)/2 drives displacement current into the chassis through parasitic C. In-phase carriers tend "
            "to reduce u0, 180-degree interleaving tends to reduce v_cm6 - different claims. Even zero-u0 pairs step v_cm6 by "
            "V/3. The combined DC ripple is S_AA + S_BB + 2 Re S_AB; a small sum does not mean small local capacitor currents.")))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(('emi_oew',), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.p_ocm = PlotPanel()
        self.t_ocm = KeyValueTable()
        rl.addWidget(self.p_ocm, 3)
        rl.addWidget(self.t_ocm, 2)
        self.ocm_tabs, self.i_ocm = with_reading(right, tr(
            "계산하면 해석이 표시됩니다 — 캐리어 위상차에 따라 CM 전압, 권선 영상분 전압 u0, DC 전류 리플이 어떻게 맞바뀌는지.",
            "Run to read the result — how the carrier shift trades the CM voltage against the winding zero-sequence "
            "voltage u0 and the DC current ripple."))
        split.addWidget(self.ocm_tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 1080])
        return split

    def run_oew(self):
        b = self.win.state.example("OEW")
        b.update(self.win.state.body())
        b["topology"] = {**b["topology"], "VA_V": self.o_V.value()}
        b.update({"speed_rpm": self.o_n.value(), "torque_Nm": self.o_T.value(), "fsw_kHz": self.o_fsw.value(),
                  "t_edge_ns": self.o_te.value()})
        self.o_btn.setEnabled(False)
        self.win.runner.run("emi_oew", tr("OEW 공통모드", "OEW common mode"), _task(api.emi_oew), self._show_oew, b,
                            on_error=self._err)

    def _show_oew(self, res):
        self.o_btn.setEnabled(True)
        self.last_oew = res
        self.p_ocm.draw(F.fig_oew_cm, res, name="oew_common_mode")
        rows = []
        for k, c in res["cases"].items():
            dc = c.get("dc_currents") or {}
            rows.append((tr(f"캐리어 위상차 {float(k) * 360:.0f}°", f"carrier shift {float(k) * 360:.0f} deg"),
                         f"u0 RMS {fmt(c['u0_rms_V'])} V · v_cm6 RMS {fmt(c['cm6_rms_V'])} V · I_A {fmt(dc.get('I_A_rms_A'))} A · "
                         f"I_B {fmt(dc.get('I_B_rms_A'))} A · I_sum {fmt(dc.get('I_sum_rms_A'))} A (identity {dc.get('identity_residual', 0):.1e})"))
        z = res["zsv_free"]
        rows.append((tr("영상분 제거 상태쌍", "zero-u0 pairs"), f"u0 max {fmt(z['u0_max_V'])} V, v_cm6 {z['v_cm6_steps_V']} V — {z['note']}"))
        self.t_ocm.set_rows(rows)
        self.i_ocm.read("emi_oew", tr("OEW 공통모드", "OEW common mode"), emi_oew_insight, res)

    def redraw(self):
        for p in (self.p_spec, self.p_net, self.p_meas, self.p_ocm, self.i_emi, self.i_ocm):
            p.redraw()
