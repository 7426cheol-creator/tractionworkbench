"""Conducted EMI page (handoff P1-C / section 11; OEW addendum 5.1-5.2).

Screening: requirement profile + entered limit curve, PWM edge source, declared CM/DM network with the artificial
network, RBW line-sum estimate, margins and the required attenuation per band (CM- or DM-dominated).  Measured trace:
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
from ..widgets import (ConceptNote, KeyValueTable, NumTable, PlotPanel, check, combo, error_box, fmt, hint, number,
                       primary_button, table_with_buttons)

NOTE_EMI = lambda: tr(
    "<b>전도성 EMI (HV 포트)</b>는 <b>소스 → 경로 → 수신기</b>로 계산합니다. 소스는 PWM 모든 스위칭 에지의 정확한 선스펙트럼"
    "(선언한 상승·하강 시간, 전류 부호에 따른 데드타임 에지 이동)이고, 경로는 선언한 DC-link(ESR/ESL), Y-cap(장착 L), 스위치노드·"
    "모터·케이블→섀시 C, 하네스, CM 초크(결합 인덕터), 인공회로망(AN)을 절점해석으로 CM·DM 동시(위상 포함) 풉니다. 수신기는 RBW 안 "
    "선들의 크기 합(피크 검출기 판독의 상한 추정)이며 QP/AV 가중·IF 필터·dwell은 모델 밖입니다.<br>"
    "<b>판정</b>: 요구 프로파일(규격·판·고객 개정·곡선 ID·포트·방법·검출기·RBW·AN·치구·운전조건)이 빠지면 REQUIREMENT_INCOMPLETE. "
    "규격 한계값은 내장하지 않습니다(승인된 곡선을 입력). 보정 근거가 없으면 SCREENING: 여유와 <b>필요 감쇠 A = max(0, E_U + M_d − L)</b>, "
    "지배 경로(CM→Y-cap/CM 초크/본딩, DM→X-cap/DM L/ESL)를 보여주며 FAIL 대신 '예측 초과'로 표시합니다. 측정 trace는 같은 프로파일로 "
    "PASS/FAIL/INDETERMINATE를 판정합니다(시험 대표성·승인은 별도). C·dv/dt 한 값이나 평균 dq 파형 FFT로 EMI를 승인하지 않습니다.",
    "<b>Conducted EMI (HV port)</b> is computed as <b>source -> path -> receiver</b>. Source: the exact line spectrum of "
    "every PWM edge (declared rise / fall times, dead-time edge shift by current sign). Path: the declared DC link "
    "(ESR / ESL), Y capacitors (mounting L), switch-node / motor / cable capacitance to chassis, harness, CM choke "
    "(coupled inductors) and the artificial network, solved by nodal analysis with CM and DM together (phases kept). "
    "Receiver: magnitude sum of the lines inside the RBW (an upper estimate of a peak-detector reading); QP / AV weighting, "
    "IF filter and dwell are outside the model.<br><b>Judgement</b>: a profile missing standard, edition, customer "
    "revision, curve ID, port, method, detector, RBW, AN, fixture or operating condition is REQUIREMENT_INCOMPLETE. No "
    "standard limit values are built in (enter the approved curve). Without calibration evidence it is SCREENING: margins, "
    "the <b>required attenuation A = max(0, E_U + M_d - L)</b> and the dominant path (CM -> Y cap / CM choke / bonding, "
    "DM -> X cap / DM L / ESL), 'predicted exceedance' instead of FAIL. A measured trace gets PASS / FAIL / INDETERMINATE "
    "against the same profile (representativeness and approval are separate). No EMI approval from a single C dv/dt "
    "value or an FFT of averaged dq waveforms.")

def _task(fn):
    def run(progress, body):
        progress(0.1, tr("계산 중", "computing"))
        return fn(body)
    return run


def _scroll(w):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    sc.setMinimumWidth(390)
    return sc


class EmiPage(QWidget):
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
        ex = api.EXAMPLE_EMI
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
        self.e_src_basis = QLineEdit(sc["basis"])
        for lab, w in ((tr("속도", "speed"), self.e_n), (tr("토크", "torque"), self.e_T), ("Vdc", self.e_vdc),
                       (tr("스위칭 주파수", "switching frequency"), self.e_fsw), (tr("상승 시간 (선언)", "rise time (declared)"), self.e_tr),
                       (tr("하강 시간 (선언)", "fall time (declared)"), self.e_tf), (tr("데드타임", "dead time"), self.e_td),
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
                ("L_ch_uH", tr("CM 초크 L (0=없음)", "CM choke L (0 = none)"), "µH", 10.0), ("k_ch", tr("초크 결합 k", "choke coupling k"), "", 0.01),
                ("an_L_uH", "AN L", "µH", 1.0), ("an_R_meas_ohm", tr("AN 측정 R", "AN measuring R"), "Ω", 1.0),
                ("an_C_coup_nF", tr("AN 결합 C", "AN coupling C"), "nF", 10.0), ("an_C_sup_uF", tr("AN 전원측 C", "AN supply C"), "µF", 0.1),
                ("R_bat_mohm", tr("전원 R", "source R"), "mΩ", 1.0))
        for key, lab, unit, step in spec:
            w = number(float(nw[key]), 0, 1e7, unit, 4 if step < 0.1 else 3, step)
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
                         ("port", tr("포트", "port")), ("method", tr("방법", "method")), ("network", tr("AN 정의", "AN definition")),
                         ("fixture", tr("치구·하네스·본딩", "fixture / harness / bonding")), ("operating_condition", tr("운전 조건", "operating condition"))):
            w = QLineEdit(str(pr.get(key, "")))
            self.prof[key] = w
            f.addRow(lab, w)
        self.e_det = combo([(tr("피크", "peak"), "peak"), (tr("준첨두 (QP)", "quasi-peak"), "quasi_peak"), (tr("평균", "average"), "average")],
                           pr["detector"])
        self.e_rbw = number(pr["rbw_Hz"] / 1e3, 0.01, 1e4, "kHz", 2, 1)
        self.e_res = number(pr["design_reserve_dB"], 0, 60, "dB", 1, 1)
        f.addRow(tr("검출기", "detector"), self.e_det)
        f.addRow("RBW", self.e_rbw)
        f.addRow(tr("설계 여유 M_d", "design reserve M_d"), self.e_res)
        lim = ex["limit"]
        self.e_lim = NumTable([tr("주파수 [MHz]", "frequency [MHz]"), tr("한계 [dBµV]", "limit [dBuV]")],
                              [[p[0] / 1e6, p[1]] for p in lim["points"]], min_height=110)
        f.addRow(QLabel(tr("한계 곡선 (승인된 원문에서 입력; 예시는 규격이 아님)", "limit curve (enter from the approved source; the example is not a standard)")))
        f.addRow(table_with_buttons(self.e_lim))
        self.e_lim_src = QLineEdit(lim["source"])
        f.addRow(tr("곡선 출처", "curve source"), self.e_lim_src)
        v.addWidget(g)
        g = QGroupBox(tr("보정·결합", "calibration · coupling"))
        f = QFormLayout(g)
        self.e_cal_on = check(tr("보정 근거 선언 (없으면 스크리닝)", "declare calibration evidence (else screening)"), False)
        self.e_cal_ev = QLineEdit()
        self.e_cal_ev.setPlaceholderText(tr("상관 보고서·holdout 결과", "correlation report / holdout results"))
        self.e_cal_u = number(3.0, 0, 40, "dB", 1, 0.5)
        self.e_ey_on = check(tr("Y-cap 허용 에너지 선언", "declare allowed Y-cap energy"), False)
        self.e_ey = number(0.2, 0, 100, "J", 3, 0.05)
        self.e_fc = number(ex["f_control_Hz"], 1, 1e6, "Hz", 0, 100)
        self.e_lo = number(0.15, 0.001, 1000, "MHz", 3, 0.05)
        self.e_hi = number(30.0, 0.01, 1000, "MHz", 2, 1)
        for lab, w in (("", self.e_cal_on), (tr("근거", "evidence"), self.e_cal_ev), (tr("모델 불확도", "model uncertainty"), self.e_cal_u),
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
        f.addRow(self.e_meas_lab)
        f.addRow(row)
        f.addRow(tr("측정 불확도 U", "measurement uncertainty U"), self.e_U)
        v.addWidget(g)
        self.e_btn = primary_button(tr("EMI 계산", "compute EMI"))
        self.e_btn.clicked.connect(self.run)
        v.addWidget(self.e_btn)
        v.addWidget(hint(tr("예시 망·곡선·프로파일은 합성 값입니다.", "The example network, curve and profile are synthetic.")))
        v.addWidget(ConceptNote(NOTE_EMI()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
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
        f, x, nf = [], [], []
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.reader(fh):
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
        self.measured = {"f_Hz": f, "level_dB": x, "noise_floor_dB": nf if len(nf) == len(f) else None}
        self.e_meas_lab.setText(tr(f"{len(f)}점 ({f[0] / 1e6:.3g}–{f[-1] / 1e6:.3g} MHz)", f"{len(f)} points "
                                   f"({f[0] / 1e6:.3g}-{f[-1] / 1e6:.3g} MHz)") + f" · {path}")

    def clear_trace(self):
        self.measured = None
        self.e_meas_lab.setText(tr("가져온 trace 없음", "no trace imported"))

    def body(self) -> dict:
        b = copy.deepcopy(api.EXAMPLE_EMI)
        b.update(self.win.state.body())
        b.update({"speed_rpm": self.e_n.value(), "torque_Nm": self.e_T.value(), "Vdc_V": self.e_vdc.value()})
        b["source"] = {"fsw_kHz": self.e_fsw.value(), "t_rise_ns": self.e_tr.value(), "t_fall_ns": self.e_tf.value(),
                       "t_dead_us": self.e_td.value(), "modulation": "svpwm", "basis": self.e_src_basis.text().strip()}
        b["network"] = {k: w.value() for k, w in self.net.items()}
        b["network"]["basis"] = self.e_net_basis.text().strip()
        b["network"]["validated_up_to_MHz"] = self.e_valid.value() if self.e_valid_on.isChecked() else None
        prof = {k: w.text().strip() for k, w in self.prof.items()}
        prof.update({"detector": self.e_det.currentData(), "rbw_Hz": self.e_rbw.value() * 1e3,
                     "design_reserve_dB": self.e_res.value()})
        b["profile"] = prof
        pts = self.e_lim.values()
        b["limit"] = ({"points": [[r[0] * 1e6, r[1]] for r in pts], "unit": "dBuV", "detector": self.e_det.currentData(),
                       "source": self.e_lim_src.text().strip()} if len(pts) >= 2 else None)
        b["calibration"] = ({"evidence": self.e_cal_ev.text().strip(), "uncertainty_dB": self.e_cal_u.value()}
                            if self.e_cal_on.isChecked() else None)
        b["E_y_allowed_J"] = self.e_ey.value() if self.e_ey_on.isChecked() else None
        b["f_control_Hz"] = self.e_fc.value()
        b["band_MHz"] = [self.e_lo.value(), self.e_hi.value()]
        b["measured"] = None if not self.measured else {**self.measured, "U_meas_dB": self.e_U.value()}
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
                         csv=lambda res=res: {k: res[k] for k in ("grid_Hz", "E_dBuV", "plus_dBuV", "minus_dBuV",
                                                                   "from_cm_source_dBuV", "from_dm_source_dBuV",
                                                                   "limit_dBuV", "margin_dB", "required_attenuation_dB")})
        self.p_net.draw(SC.fig_emi_network, res["network"], name="emi_network")
        self.p_meas.draw(F.fig_emi_measured, res, name="emi_measured")
        c = res["claim"]
        import numpy as np
        A = np.asarray(res["required_attenuation_dB"], dtype=float)
        g = np.asarray(res["grid_Hz"])
        rows = [(tr("판정", "claim"), f"{c['status']} — {c['detail']}" + (f" [{', '.join(c['reasons'])}]" if c.get("reasons") else "")),
                (tr("운전점", "operating point"), f"{fmt(res['operating_point']['speed_rpm'])} rpm, {fmt(res['operating_point']['torque_Nm'])} N·m, "
                                                 f"|i| {fmt(res['operating_point']['i_peak_A'])} A, m {fmt(res['operating_point']['modulation_index'], 3)}, "
                                                 f"carrier ratio {res['carrier_ratio']}")]
        if np.any(np.isfinite(A)):
            j = int(np.nanargmax(A))
            rows.append((tr("최대 필요 감쇠", "max required attenuation"),
                         f"{A[j]:.1f} dB at {g[j] / 1e6:.3g} MHz ({res['dominant_source'][j]}-dominated)"))
            for lo, hi in ((0.15e6, 0.5e6), (0.5e6, 2e6), (2e6, 10e6), (10e6, 30e6)):
                m = (g >= lo) & (g <= hi)
                if m.any() and np.any(np.isfinite(A[m])):
                    cm = int(np.sum(np.asarray(res["dominant_source"])[m] == "CM"))
                    rows.append((f"{lo / 1e6:g}–{hi / 1e6:g} MHz", f"A_req max {np.nanmax(A[m]):.1f} dB · CM {cm}/{int(m.sum())} "
                                                                    f"{tr('격자점', 'grid points')}"))
        if res.get("profile_missing"):
            rows.append((tr("프로파일 누락", "profile missing"), ", ".join(res["profile_missing"])))
        for k, v in (res.get("coupling") or {}).items():
            rows.append((k, " · ".join(f"{kk}: {fmt(vv) if isinstance(vv, float) else vv}" for kk, vv in v.items())))
        ms = res.get("measured")
        if ms:
            rows.append((tr("측정 trace", "measured trace"), f"{ms['verdict']} — {ms['reason']}"))
        for n in res.get("notes", []):
            rows.append((tr("주의", "note"), n))
        self.t_emi.set_rows(rows)
        if ms:
            self.e_tabs.setCurrentWidget(self.p_meas)

    # ------------------------------------------------------------------ OEW tab
    def _oew_tab(self):
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        ex = api.EXAMPLE_OEW
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
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.p_ocm = PlotPanel()
        self.t_ocm = KeyValueTable()
        rl.addWidget(self.p_ocm, 3)
        rl.addWidget(self.t_ocm, 2)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 1080])
        return split

    def run_oew(self):
        b = copy.deepcopy(api.EXAMPLE_OEW)
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

    def redraw(self):
        for p in (self.p_spec, self.p_net, self.p_meas, self.p_ocm):
            p.redraw()
