"""OEW dual inverter and HEV system page (OEW/HEV addendum).

OEW: topology (common bus / isolated), zero-sequence data and policy, power split, per-port source limits; the
minimum-current witness with voltage allocation over the angle, both bridges' losses, port accounting, the switch-
state geometry, a same-motor T-n comparison, the paired safe-state matrix and the switched i0 ripple.
HEV: joint torque set of two machines on one bus (branch vs net power), cranking replay, load-rejection energy
ledger and the simple-planetary lever check.
"""

from __future__ import annotations

import copy
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...plots import oew_hev_figures as F
from ...plots import schematics as SC
from ..widgets import (ConceptNote, KeyValueTable, NumTable, PlotPanel, check, combo, error_box, fmt, hint, integer,
                       number, primary_button, table_with_buttons)

INF = math.inf

NOTE_OEW = lambda: tr(
    "<b>OEW(오픈 엔드 권선)</b>: 한 3상 권선의 양끝(6단자)을 두 브리지 A·B가 구동합니다. 단일 VSI 계산을 두 번 하거나 "
    "Vdc를 2배로 하지 않고, 회로 자체의 식으로 풉니다.<br>"
    "• <b>공통 bus</b>: 두 브리지가 같은 ± 레일 → 영상분 u0 = V(ΣsA − ΣsB)/3에 도전 귀환 경로가 있어 i0가 흐를 수 있습니다. "
    "u0 = 0 상태쌍(20쌍, 7벡터)의 보장 반경은 <b>V</b>(단일 VSI V/√3 대비 √3배, 2배 아님). 영상분 명령 u0*는 기본파와 같은 "
    "상전압 범위를 공유합니다(마지막 clip 금지).<br>"
    "• <b>절연 전원</b>: 저주파 귀환이 없어 ia+ib+ic = 0, 부유 δ가 u0를 흡수 → 보장 반경 (VA+VB)/√3. i0 = 0이어도 v0 = 0은 아님.<br>"
    "• 두 브리지 모두 <b>권선 전류 전체</b>를 운반합니다(반분 금지). 전력 배분 s는 전압 배분이며, s가 [0,1] 밖이면 전력 순환. "
    "공통 bus의 전원 한도는 순합에 <b>한 번만</b>, 절연 포트는 각각 적용합니다.<br>"
    "• L0·삼고조파 쇄교자속이 없으면 0으로 두지 않고 UNKNOWN입니다. 안전 상태는 두 브리지 '쌍'의 속성입니다(A111/B000 = 직류 u0 인가).",
    "<b>OEW (open-end winding)</b>: bridges A and B drive the two ends (six terminals) of one 3-phase winding. It is "
    "solved from its own circuit equations, not by running the single VSI twice or doubling Vdc.<br>"
    "• <b>common bus</b>: both bridges on the same rails - the zero sequence u0 = V(sum sA - sum sB)/3 has a conductive "
    "return and i0 can flow. The zero-u0 pairs (20 pairs, 7 vectors) guarantee a radius <b>V</b> (sqrt3 over a single "
    "VSI, not 2). The zero-sequence command u0* shares the phase-voltage range with the fundamental (no final clip).<br>"
    "• <b>isolated sources</b>: no low-frequency return, ia+ib+ic = 0, the floating delta takes up u0 -> (VA+VB)/sqrt3; "
    "i0 = 0 does not mean v0 = 0.<br>"
    "• Both bridges carry the <b>full winding current</b> (never halved). The split s is a voltage split; outside [0, 1] "
    "power circulates. A common-bus source cap applies <b>once</b> to the net; isolated ports separately.<br>"
    "• Missing L0 / triplen flux is UNKNOWN, never zero. A safe state is a property of the bridge PAIR "
    "(A111/B000 applies a DC u0).")

NOTE_HEV = lambda: tr(
    "<b>HEV</b>: P0–P4는 약칭이며 입력은 실제 축·기어·클러치·DC 노드 접속입니다. 공통 bus의 동시 토크 집합 K는 모든 부품과 "
    "공통 전원(배터리/부스트)·냉각 제약을 <b>함께</b> 만족하는 조합이며, 개별 최대의 직사각형이 아닙니다. 순전력이 작아도 가지 "
    "전력(예: +80/−70 kW)은 인버터·커패시터·냉각을 결정합니다.<br>크랭킹은 크랭크각별 압축 토크(부호 있음)·초기각·Vdc 강하·"
    "주행 여유를 포함한 시간영역 replay이며 결과 이름은 '크랭킹 요구'입니다(연소 성립은 엔진 근거 필요). 부하 차단 후에는 "
    "발전기가 반응 완료까지 에너지를 넣으므로 공통 커패시터 여유(한 번만 계상)와 반응 시간을 비교합니다.",
    "<b>HEV</b>: P0-P4 are shorthand; the input is the actual shaft / gear / clutch / DC-node graph. The joint torque "
    "set K on one bus contains the pairs that satisfy every component AND the shared source (battery / boost) and "
    "cooling <b>together</b>; it is not the box of separate maxima. A small net can hide large branch powers (e.g. "
    "+80 / -70 kW) that size inverters, capacitors and cooling.<br>Cranking is a time-domain replay with a signed "
    "crank-angle compression torque, initial angle, Vdc sag and traction reserve, named 'cranking requirement' "
    "(combustion needs engine evidence). After a load rejection the generator feeds the bus until its reaction "
    "completes: the common capacitor margin (counted once) is compared with that time.")


def _task(fn):
    def run(progress, body):
        progress(0.1, tr("계산 중", "computing"))
        return fn(body)
    return run


def _scroll(w):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    sc.setMinimumWidth(380)
    return sc


def _kw(v):
    return None if v is None else v * 1e3


class OewHevPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last_oew = self.last_cmp = self.last_joint = self.last_crank = self.last_rej = self.last_pl = None
        self.tabs = QTabWidget()
        self.tabs.addTab(self._oew_tab(), tr("OEW 듀얼 인버터", "OEW dual inverter"))
        self.tabs.addTab(self._hev_tab(), tr("HEV 시스템", "HEV system"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.tabs)
        self._kind_changed()

    # ================================================================== OEW
    def _oew_tab(self):
        ex = api.EXAMPLE_OEW
        t = ex["topology"]
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("토폴로지 (실제 회로를 선택)", "topology (select the actual circuit)"))
        f = QFormLayout(g)
        self.o_kind = combo([(tr("공통 DC bus", "common DC bus"), "common_bus"), (tr("절연된 두 전원", "two isolated sources"),
                                                                              "isolated")], t["kind"])
        self.o_kind.currentIndexChanged.connect(self._kind_changed)
        self.o_VA = number(t["VA_V"], 1, 2000, "V", 1, 10)
        self.o_VB = number(400.0, 1, 2000, "V", 1, 10)
        self.o_split_on = check(tr("전력(전압) 배분 선언", "declare the power (voltage) split"), False)
        self.o_split = number(0.5, -5, 5, "", 3, 0.05, tip=tr("브리지 A의 권선 전압·전력 비율 s", "bridge A share s"))
        self.o_ilim_on = check(tr("브리지 전류 한도 별도 선언", "declare a bridge current limit"), False)
        self.o_ilim = number(600, 1, 1e5, "A", 1, 10)
        self.o_basis = QLineEdit(t.get("basis", ""))
        for lab, w in ((tr("DC 구성", "DC topology"), self.o_kind), ("VA", self.o_VA), ("VB", self.o_VB), ("", self.o_split_on),
                       (tr("배분 s (A)", "split s (A)"), self.o_split), ("", self.o_ilim_on),
                       (tr("브리지당 상전류 peak 한도", "per-bridge phase peak limit"), self.o_ilim),
                       (tr("회로 근거", "circuit basis"), self.o_basis)):
            f.addRow(lab, w)
        v.addWidget(g)
        z = t["zero_sequence"]
        g = QGroupBox(tr("영상분 (공통 bus)", "zero sequence (common bus)"))
        f = QFormLayout(g)
        self.o_zs_on = check(tr("L0 / 삼고조파 데이터 선언", "declare L0 / triplen data"), True)
        self.o_zs_pol = combo([(tr("i0 제어 (u0* = e0, 전압 여유 소모)", "regulate i0 (u0* = e0, uses headroom)"), "regulate_i0"),
                               (tr("보상 없음 (i0 흐름)", "no compensation (i0 flows)"), "no_compensation")], t["zs_policy"])
        self.o_L0 = number(z["L0_uH"], 0.1, 1e5, "µH", 2, 5)
        self.o_psi3 = number(z["psi0_mWb_deg"][0][1], 0, 1e3, "mWb", 3, 0.5)
        self.o_psi3ph = number(z["psi0_mWb_deg"][0][2], -360, 360, "°", 1, 5)
        self.o_zs_basis = QLineEdit(z["basis"])
        for lab, w in (("", self.o_zs_on), (tr("정책", "policy"), self.o_zs_pol), ("L0", self.o_L0),
                       (tr("ψ_PM,0 3차 진폭", "psi_PM,0 3rd amplitude"), self.o_psi3), (tr("3차 위상", "3rd phase"), self.o_psi3ph),
                       (tr("근거", "basis"), self.o_zs_basis)):
            f.addRow(lab, w)
        v.addWidget(g)
        la = t["limits_A"]
        g = QGroupBox(tr("전원 한도 (공통 bus: 한 번 / 절연: 포트별)", "source limits (common bus: once / isolated: per port)"))
        f = QFormLayout(g)
        self.o_disA = number(la["discharge_power_max_W"] / 1e3, 0, 1e5, "kW", 1, 5)
        self.o_chgA = number(la["charge_power_max_W"] / 1e3, 0, 1e5, "kW", 1, 5)
        self.o_disB = number(250.0, 0, 1e5, "kW", 1, 5)
        self.o_chgB = number(150.0, 0, 1e5, "kW", 1, 5)
        for lab, w in ((tr("A (또는 공통) 방전", "A (or shared) discharge"), self.o_disA),
                       (tr("A (또는 공통) 충전", "A (or shared) charge"), self.o_chgA),
                       (tr("B 방전 (절연)", "B discharge (isolated)"), self.o_disB), (tr("B 충전 (절연)", "B charge (isolated)"), self.o_chgB)):
            f.addRow(lab, w)
        f.addRow(hint(tr("전류 한도는 선언된 무제한(∞)으로 둡니다.", "current limits are declared unlimited (inf).")))
        v.addWidget(g)
        g = QGroupBox(tr("요구·손실", "request · losses"))
        f = QFormLayout(g)
        self.o_n = number(ex["speed_rpm"], 0, 30000, "rpm", 0, 500)
        self.o_T = number(ex["torque_Nm"], -5000, 5000, "N·m", 2, 10)
        self.o_mod = check(tr("브리지별 데이터시트 모듈 손실 (예시 모듈 + 예시 E∝V 스케일링)",
                              "per-bridge datasheet module losses (example module + example E~V scaling)"), True)
        self.o_fsw = number(ex["fsw_kHz"], 0.5, 200, "kHz", 2, 1)
        self.o_shift = number(0.0, 0, 1, "", 2, 0.25, tip=tr("브리지 B 캐리어 위상 (주기 비율)", "bridge B carrier phase (fraction)"))
        for lab, w in ((tr("속도", "speed"), self.o_n), (tr("축 토크", "shaft torque"), self.o_T), ("", self.o_mod),
                       (tr("스위칭 주파수", "switching frequency"), self.o_fsw), (tr("캐리어 위상차", "carrier shift"), self.o_shift)):
            f.addRow(lab, w)
        v.addWidget(g)
        row = QHBoxLayout()
        self.o_btn = primary_button(tr("OEW 운전점", "OEW operating point"))
        self.o_btn.clicked.connect(self.run_oew)
        self.o_cmp_btn = QPushButton(tr("T–n 비교", "T-n comparison"))
        self.o_cmp_btn.setMinimumHeight(34)
        self.o_cmp_btn.clicked.connect(self.run_compare)
        row.addWidget(self.o_btn)
        row.addWidget(self.o_cmp_btn)
        v.addLayout(row)
        v.addWidget(hint(tr("예시 값(L0, 삼고조파, 모듈 스케일링, 전원 한도)은 합성 값입니다.",
                            "Example values (L0, triplen, module scaling, limits) are synthetic.")))
        v.addWidget(ConceptNote(NOTE_OEW()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.o_tabs = QTabWidget()
        self.p_sets = PlotPanel()
        self.p_opt = PlotPanel()
        self.p_cmp = PlotPanel(hint=tr("'T–n 비교'를 실행하세요", "run 'T-n comparison'"))
        self.p_pair = PlotPanel()
        self.p_rip = PlotPanel()
        self.p_sch = PlotPanel(min_height=260)
        for p, lab in ((self.p_sets, tr("전압 집합", "voltage sets")), (self.p_opt, tr("운전점·포트", "point · ports")),
                       (self.p_cmp, tr("T–n 비교", "T-n comparison")), (self.p_pair, tr("안전 상태 쌍", "paired safe states")),
                       (self.p_rip, tr("i0 스위칭 리플", "i0 switching ripple")), (self.p_sch, tr("회로도", "circuit"))):
            self.o_tabs.addTab(p, lab)
        self.t_oew = KeyValueTable()
        rl.addWidget(self.o_tabs, 3)
        rl.addWidget(self.t_oew, 2)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 1060])
        return split

    def _kind_changed(self, *_):
        iso = self.o_kind.currentData() == "isolated"
        for w in (self.o_VB, self.o_disB, self.o_chgB):
            w.setEnabled(iso)
        for w in (self.o_zs_pol,):
            w.setEnabled(not iso)

    def oew_body(self) -> dict:
        b = copy.deepcopy(api.EXAMPLE_OEW)
        iso = self.o_kind.currentData() == "isolated"
        lim = lambda d, c: {"discharge_power_max_W": d * 1e3, "charge_power_max_W": c * 1e3,
                            "discharge_current_max_A": INF, "charge_current_max_A": INF}
        t = {"kind": self.o_kind.currentData(), "VA_V": self.o_VA.value(), "VB_V": self.o_VB.value() if iso else None,
             "zs_policy": self.o_zs_pol.currentData(),
             "zero_sequence": ({"L0_uH": self.o_L0.value(), "psi0_mWb_deg": [[3, self.o_psi3.value(), self.o_psi3ph.value()]],
                                "R0_mohm": None, "basis": self.o_zs_basis.text().strip()} if self.o_zs_on.isChecked() else None),
             "power_split_A": self.o_split.value() if self.o_split_on.isChecked() else None,
             "bridge_current_limit_A": self.o_ilim.value() if self.o_ilim_on.isChecked() else None,
             "reserve_fraction": None, "limits_A": lim(self.o_disA.value(), self.o_chgA.value()),
             "limits_B": lim(self.o_disB.value(), self.o_chgB.value()) if iso else None,
             "basis": self.o_basis.text().strip()}
        b.update({"topology": t, "speed_rpm": self.o_n.value(), "torque_Nm": self.o_T.value(),
                  "use_module": self.o_mod.isChecked(), "fsw_kHz": self.o_fsw.value(), "carrier_shift": self.o_shift.value()})
        b.update(self.win.state.body())
        return b

    def _start(self, key, label, fn, show, body, btn):
        btn.setEnabled(False)
        self.win.runner.run(key, label, _task(fn), show, body, on_error=self._err)

    def run_oew(self):
        try:
            body = self.oew_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self._start("oew", tr("OEW 운전점", "OEW point"), api.oew, self._show_oew, body, self.o_btn)

    def run_compare(self):
        try:
            body = self.oew_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self._start("oew_compare", tr("T–n 비교", "T-n comparison"), api.oew_compare, self._show_cmp, body, self.o_cmp_btn)

    def _err(self, msg, tb):
        for b in (self.o_btn, self.o_cmp_btn, self.h_btn, self.c_btn, self.r_btn, self.pl_btn):
            b.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _show_oew(self, res):
        self.o_btn.setEnabled(True)
        self.last_oew = res
        r = res["result"]
        w = r.get("witness")
        self.p_sets.draw(F.fig_oew_voltage_sets, res, name="oew_voltage_sets")
        self.p_pair.draw(F.fig_oew_paired, res, name="oew_paired_states")
        flows = None
        if w:
            self.p_opt.draw(F.fig_oew_point, res, name="oew_operating_point",
                            csv=lambda w=w: {k: w["waveforms"][k] for k in w["waveforms"]})
            br = w["bridges"]
            flows = {"A": f"P_A {br['A']['P_ac_W'] / 1e3:+.1f} kW", "B": f"P_B {br['B']['P_ac_W'] / 1e3:+.1f} kW"}
            src = w["ports"].get("shared_source")
            if src and src.get("P_dc_W") is not None:
                flows["src"] = tr(f"전원 {src['P_dc_W'] / 1e3:+.1f} kW", f"source {src['P_dc_W'] / 1e3:+.1f} kW")
        else:
            self.p_opt.placeholder(r.get("reason", ""))
        self.p_sch.draw(SC.fig_oew_schematic, res["topology"], name="oew_circuit", flows=flows)
        if res.get("i0_ripple"):
            self.p_rip.draw(F.fig_oew_ripple, res, name="oew_i0_ripple")
        else:
            self.p_rip.placeholder(tr("공통 bus + 영상분 데이터 + witness가 있을 때 계산", "needs a common bus, zero-sequence data "
                                                                                   "and a witness"))
        rows = [(tr("판정", "status"), f"{r['status']}" + (f" — {r.get('reason')}" if r.get("reason") else ""))]
        if w:
            op = w["operating_point"]
            rows += [(tr("운전점 id, iq", "point id, iq"), f"{fmt(op['id_A'])}, {fmt(op['iq_A'])} A (|i| {fmt(op['i_dq_A'])} A)"),
                     (tr("권선 전압 peak", "winding voltage peak"), f"{fmt(op['U_phase_pk_V'])} V (cmd {fmt(op['U_cmd_pk_V'])} V)"),
                     (tr("전압 배분", "voltage allocation"),
                      f"{w['voltage_allocation']['status']}: {tr('활용률', 'utilisation')} {fmt(w['voltage_allocation']['utilisation'], 4)} "
                      f"({w['voltage_allocation']['binding']}) · {w['voltage_allocation']['policy']}"),
                     (tr("상 peak / RMS", "phase peak / RMS"),
                      f"{fmt(w['currents']['phase_peak_A'])} A / {fmt(w['currents']['phase_rms_A'][0])} A "
                      f"({tr('브리지당 한도', 'per-bridge limit')} {fmt(w['currents']['bridge_limit_A'])} A)"),
                     (tr("영상분", "zero sequence"), str({k: (fmt(v) if isinstance(v, float) else v) for k, v in w["zero_sequence"].items()})),
                     (tr("토크 T_em / T_shaft / <T0>", "torque T_em / T_shaft / <T0>"),
                      f"{fmt(op['Tem_Nm'])} / {fmt(op['Tshaft_Nm'])} / {fmt(op['T0_mean_Nm'])} N·m"),
                     (tr("동손 dq / 영상분", "copper dq / zero sequence"), f"{fmt(op['Pcu_dq_W'])} W / {fmt(op['Pcu_zero_seq_W'])} W")]
            for tag in ("A", "B"):
                bb = w["bridges"][tag]
                lo = bb["loss"]
                rows.append((tr(f"브리지 {tag}", f"bridge {tag}"),
                             f"P_ac {fmt(bb['P_ac_W'])} W, loss {fmt(lo.get('dc_side_W') or lo.get('screening_W'))} W "
                             f"({lo.get('model')}; {'established' if lo.get('established') else 'NOT established: ' + '; '.join(lo.get('problems', []))}),"
                             f" P_dc {fmt(bb['P_dc_W'])} W, duty {fmt(bb['duty_min'], 3)}–{fmt(bb['duty_max'], 3)}"))
            rows.append((tr("순환 전력", "circulating power"), f"{fmt(w['circulating_power_W'])} W"))
            rows.append((tr("항등식 잔차", "identity residuals"), str({k: f"{v:.2e}" for k, v in w["identities"].items()})))
            for c in w["claims"]:
                rows.append((c["name"], f"{c['status']} — {c.get('detail', '')}"))
            rows.append((tr("모델 밖", "not modelled"), ", ".join(w["not_modelled"])))
        g = res["geometry"]
        rows.append((tr("상태쌍 기하", "state-pair geometry"),
                     f"{g['pairs']} pairs, {g['unique_alphabeta']} αβ points; admissible {g['admissible_pairs']} / "
                     f"{g['admissible_unique_alphabeta']}; guaranteed radius {fmt(g['hull_inradius_V'])} V "
                     f"(single VSI {fmt(g['single_vsi_inradius_V'])} V)"))
        bc = res["b_clamp"]
        rows.append(("B = 000 vs floating star", f"{bc['B_000_common_bus_V']} vs {[round(x, 1) for x in bc['floating_star_V']]} V "
                                                 f"(A = {bc['sA']})"))
        self.t_oew.set_rows(rows)

    def _show_cmp(self, res):
        self.o_cmp_btn.setEnabled(True)
        self.last_cmp = res
        rows = res["rows"]
        self.p_cmp.draw(F.fig_oew_compare, res, name="oew_tn_comparison",
                        csv=lambda rows=rows: {k: [r.get(k) for r in rows] for k in
                                               ("speed_rpm", "single_vsi_Nm", "oew_common_bus_Nm", "oew_isolated_Nm",
                                                "single_vsi_same_stack_Nm")})
        self.o_tabs.setCurrentWidget(self.p_cmp)

    # ================================================================== HEV
    def _hev_tab(self):
        ex = api.EXAMPLE_HEV
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("공통 bus의 두 기기 (현재 모델 사용)", "two machines on one bus (active drive model)"))
        f = QFormLayout(g)
        m1, m2 = ex["machines"]
        self.h_n1 = number(m1["speed_rpm"], 0, 30000, "rpm", 0, 250)
        self.h_r1 = QLineEdit(m1["role"])
        self.h_n2 = number(m2["speed_rpm"], 0, 30000, "rpm", 0, 250)
        self.h_r2 = QLineEdit(m2["role"])
        self.h_T1 = number(ex["request_Nm"][0], -5000, 5000, "N·m", 1, 10)
        self.h_T2 = number(ex["request_Nm"][1], -5000, 5000, "N·m", 1, 10)
        self.h_vdc = number(ex["Vdc_V"], 1, 2000, "V", 1, 10)
        self.h_aux = number(ex["aux_W"], 0, 1e5, "W", 0, 100)
        self.h_lv = integer(ex["n_levels"], 5, 41)
        for lab, w in (("EM1 " + tr("속도", "speed"), self.h_n1), ("EM1 " + tr("역할(이름)", "role (name)"), self.h_r1),
                       ("EM2 " + tr("속도", "speed"), self.h_n2), ("EM2 " + tr("역할(이름)", "role (name)"), self.h_r2),
                       (tr("요구 T1", "request T1"), self.h_T1), (tr("요구 T2", "request T2"), self.h_T2),
                       ("V_bus", self.h_vdc), (tr("보조 전력", "auxiliary"), self.h_aux), (tr("격자 수", "grid levels"), self.h_lv)):
            f.addRow(lab, w)
        v.addWidget(g)
        bt = ex["battery"]
        g = QGroupBox(tr("배터리·부스트", "battery · boost"))
        f = QFormLayout(g)
        self.h_ocv = number(bt["ocv_V"], 1, 2000, "V", 1, 10)
        self.h_R = number(bt["R_int_mohm"], 0, 1e4, "mΩ", 1, 5)
        self.h_dis = number(bt["limits"]["discharge_power_max_W"] / 1e3, 0, 1e5, "kW", 1, 5)
        self.h_chg = number(bt["limits"]["charge_power_max_W"] / 1e3, 0, 1e5, "kW", 1, 5)
        self.h_uv = number(bt["uv_min_V"], 0, 2000, "V", 1, 10)
        bs = ex["boost"]
        self.h_boost = check(tr("부스트 사용 (V_bus ≥ V_bat)", "use boost (V_bus >= V_bat)"), ex["use_boost"])
        self.h_Dmax = number(bs["D_max"], 0, 0.95, "", 3, 0.05)
        self.h_IL = number(bs["I_L_max_A"], 1, 1e5, "A", 1, 10)
        for lab, w in (("OCV", self.h_ocv), ("R_int", self.h_R), (tr("방전 한도", "discharge limit"), self.h_dis),
                       (tr("충전 한도", "charge limit"), self.h_chg), ("UV", self.h_uv), ("", self.h_boost),
                       ("D_max", self.h_Dmax), ("I_L,max", self.h_IL)):
            f.addRow(lab, w)
        v.addWidget(g)
        self.h_btn = primary_button(tr("동시 토크 집합", "joint torque set"))
        self.h_btn.clicked.connect(self.run_joint)
        v.addWidget(self.h_btn)
        c = ex["crank"]
        g = QGroupBox(tr("크랭킹 replay (EM1 = 기동기)", "cranking replay (EM1 = starter)"))
        f = QFormLayout(g)
        self.c_ratio = number(c["ratio"], 0.1, 20, "", 2, 0.1, tip=tr("기동기 속도 / 크랭크 속도", "starter / crank speed"))
        self.c_T = number(c["T_cmd_Nm"], 0, 2000, "N·m", 1, 5)
        self.c_nt = number(c["n_target_rpm"], 1, 5000, "rpm", 0, 50)
        self.c_tmax = number(c["t_max_s"], 0.01, 10, "s", 2, 0.05)
        self.c_vf = number(c["V_floor_V"], 1, 2000, "V", 1, 10)
        self.c_res = number(c["traction_reserve_W"] / 1e3, 0, 1e4, "kW", 1, 1)
        self.c_J = number(c["load"]["J_kgm2"], 0.001, 50, "kg·m²", 3, 0.01)
        self.c_f0 = number(c["load"]["f0_Nm"], 0, 1e3, "N·m", 1, 1)
        self.c_f1 = number(c["load"]["f1_Nm_s"], 0, 100, "N·m·s", 3, 0.05)
        self.c_period = number(c["load"]["period_deg"], 1, 720, "°", 1, 10)
        for lab, w in ((tr("기어비", "ratio"), self.c_ratio), (tr("기동 토크 명령 (EM 축)", "starter torque (EM shaft)"), self.c_T),
                       (tr("목표 크랭크 속도", "target crank speed"), self.c_nt), (tr("허용 시간", "time allowed"), self.c_tmax),
                       (tr("UV 바닥 (능력 평가 전압)", "UV floor (capability voltage)"), self.c_vf),
                       (tr("주행 여유 전력", "traction reserve"), self.c_res), (tr("반사 관성", "reflected inertia"), self.c_J),
                       (tr("마찰 f0", "friction f0"), self.c_f0), (tr("마찰 f1", "friction f1"), self.c_f1),
                       (tr("압축 주기", "compression period"), self.c_period)):
            f.addRow(lab, w)
        self.c_load = NumTable([tr("크랭크각 [°]", "crank angle [°]"), tr("압축 토크 [N·m] (부호)", "compression [N·m] (signed)")],
                               list(zip(c["load"]["angle_deg"], c["load"]["torque_Nm"])), min_height=150)
        f.addRow(table_with_buttons(self.c_load, tr("엔진 시험 trace 또는 공급사 envelope로 대체하세요 (예시는 합성).",
                                                    "replace with an engine test trace / supplier envelope (example is synthetic).")))
        self.c_btn = primary_button(tr("크랭킹 replay", "cranking replay"))
        self.c_btn.clicked.connect(self.run_crank)
        f.addRow(self.c_btn)
        v.addWidget(g)
        rj = ex["rejection"]
        g = QGroupBox(tr("부하 차단 에너지 (공통 커패시터)", "load rejection energy (common capacitor)"))
        f = QFormLayout(g)
        self.r_C = number(rj["C_uF"], 1, 1e5, "µF", 1, 10)
        self.r_V0 = number(rj["V0_V"], 1, 2000, "V", 1, 10)
        self.r_Vm = number(rj["V_max_V"], 1, 2000, "V", 1, 10)
        self.r_gen = number(rj["sources_W"][0] / 1e3, 0, 1e4, "kW", 1, 5)
        self.r_sink = number(rj["sinks_W"][0] / 1e3, 0, 1e4, "kW", 1, 5)
        self.r_tr = number(rj["t_react_ms"], 0, 1e4, "ms", 3, 0.1)
        self.r_ramp = number(rj["t_ramp_ms"], 0, 1e4, "ms", 3, 0.1)
        for lab, w in (("C_bus", self.r_C), ("V0", self.r_V0), ("V_max", self.r_Vm),
                       (tr("발전 지속 전력", "generation kept"), self.r_gen), (tr("수용 가능 (배터리 등)", "accepted (battery ...)"), self.r_sink),
                       (tr("반응 시간 (검출+연료차단)", "reaction (detect + fuel cut)"), self.r_tr), (tr("토크 제거 ramp", "torque ramp"), self.r_ramp)):
            f.addRow(lab, w)
        self.r_btn = primary_button(tr("에너지 장부", "energy ledger"))
        self.r_btn.clicked.connect(self.run_rej)
        f.addRow(self.r_btn)
        v.addWidget(g)
        pl = ex["planetary"]
        g = QGroupBox(tr("단순 유성기어 (동력 분배)", "simple planetary (power split)"))
        f = QFormLayout(g)
        self.pl_Ns = integer(pl["Ns"], 1, 500)
        self.pl_Nr = integer(pl["Nr"], 2, 1000)
        self.pl_ring = number(pl["known_rpm"]["ring"], -30000, 30000, "rpm", 0, 100)
        self.pl_carrier = number(pl["known_rpm"]["carrier"], -30000, 30000, "rpm", 0, 100)
        self.pl_T = number(pl["torque_Nm"], -5000, 5000, "N·m", 1, 10)
        self.pl_sunlim = number(pl["limits_rpm"]["sun"], 0, 50000, "rpm", 0, 500)
        for lab, w in (("Ns", self.pl_Ns), ("Nr", self.pl_Nr), (tr("링 (출력) 속도", "ring (output) speed"), self.pl_ring),
                       (tr("캐리어 (엔진) 속도", "carrier (engine) speed"), self.pl_carrier),
                       (tr("캐리어 토크 (기어로 +)", "carrier torque (into the set +)"), self.pl_T),
                       (tr("선(EM1) 속도 한도", "sun (EM1) speed limit"), self.pl_sunlim)):
            f.addRow(lab, w)
        self.pl_btn = primary_button(tr("레버도·검사", "lever diagram · check"))
        self.pl_btn.clicked.connect(self.run_planetary)
        f.addRow(self.pl_btn)
        v.addWidget(g)
        v.addWidget(ConceptNote(NOTE_HEV()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.h_tabs = QTabWidget()
        self.p_joint = PlotPanel()
        self.p_crank = PlotPanel()
        self.p_rej = PlotPanel()
        self.p_pl = PlotPanel()
        self.p_hsch = PlotPanel(min_height=260)
        for p, lab in ((self.p_joint, tr("동시 토크 집합", "joint torque set")), (self.p_crank, tr("크랭킹", "cranking")),
                       (self.p_rej, tr("부하 차단", "load rejection")), (self.p_pl, tr("유성기어", "planetary")),
                       (self.p_hsch, tr("시스템 구성도", "system diagram"))):
            self.h_tabs.addTab(p, lab)
        self.t_hev = KeyValueTable()
        rl.addWidget(self.h_tabs, 3)
        rl.addWidget(self.t_hev, 2)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 1060])
        return split

    def _battery(self):
        return {"ocv_V": self.h_ocv.value(), "R_int_mohm": self.h_R.value(), "uv_min_V": self.h_uv.value(),
                "basis": "UI entry", "limits": {"discharge_power_max_W": self.h_dis.value() * 1e3,
                                                "charge_power_max_W": self.h_chg.value() * 1e3,
                                                "discharge_current_max_A": INF, "charge_current_max_A": INF}}

    def hev_body(self) -> dict:
        b = copy.deepcopy(api.EXAMPLE_HEV)
        b.update(self.win.state.body())
        b["machines"] = [{"name": "EM1", "role": self.h_r1.text(), "speed_rpm": self.h_n1.value(), "drive": None},
                         {"name": "EM2", "role": self.h_r2.text(), "speed_rpm": self.h_n2.value(), "drive": None}]
        b["request_Nm"] = [self.h_T1.value(), self.h_T2.value()]
        b["Vdc_V"] = self.h_vdc.value()
        b["aux_W"] = self.h_aux.value()
        b["n_levels"] = self.h_lv.value()
        b["battery"] = self._battery()
        b["use_boost"] = self.h_boost.isChecked()
        b["boost"] = {**b["boost"], "D_max": self.h_Dmax.value(), "I_L_max_A": self.h_IL.value()}
        rows = self.c_load.values()
        b["crank"] = {**b["crank"], "ratio": self.c_ratio.value(), "T_cmd_Nm": self.c_T.value(),
                      "n_target_rpm": self.c_nt.value(), "t_max_s": self.c_tmax.value(), "V_floor_V": self.c_vf.value(),
                      "traction_reserve_W": self.c_res.value() * 1e3,
                      "load": {**b["crank"]["load"], "angle_deg": [r[0] for r in rows], "torque_Nm": [r[1] for r in rows],
                               "J_kgm2": self.c_J.value(), "f0_Nm": self.c_f0.value(), "f1_Nm_s": self.c_f1.value(),
                               "period_deg": self.c_period.value()}}
        b["rejection"] = {"C_uF": self.r_C.value(), "V0_V": self.r_V0.value(), "V_max_V": self.r_Vm.value(),
                          "sources_W": [self.r_gen.value() * 1e3], "sinks_W": [self.r_sink.value() * 1e3],
                          "t_react_ms": self.r_tr.value(), "t_ramp_ms": self.r_ramp.value()}
        b["planetary"] = {"Ns": self.pl_Ns.value(), "Nr": self.pl_Nr.value(),
                          "known_rpm": {"ring": self.pl_ring.value(), "carrier": self.pl_carrier.value()},
                          "port": "carrier", "torque_Nm": self.pl_T.value(),
                          "limits_rpm": {"sun": self.pl_sunlim.value(), "carrier": 8000.0, "ring": 16000.0}}
        return b

    def _hev_run(self, key, label, fn, show, btn):
        try:
            body = self.hev_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self._start(key, label, fn, show, body, btn)

    def run_joint(self):
        self._hev_run("hev_joint", tr("동시 토크 집합", "joint torque set"), api.hev_joint, self._show_joint, self.h_btn)

    def run_crank(self):
        self._hev_run("hev_crank", tr("크랭킹 replay", "cranking replay"), api.hev_crank, self._show_crank, self.c_btn)

    def run_rej(self):
        self._hev_run("hev_rejection", tr("부하 차단", "load rejection"), api.hev_rejection, self._show_rej, self.r_btn)

    def run_planetary(self):
        self._hev_run("hev_planetary", tr("유성기어", "planetary"), api.hev_planetary, self._show_pl, self.pl_btn)

    def _show_joint(self, res):
        self.h_btn.setEnabled(True)
        self.last_joint = res
        self.p_joint.draw(F.fig_hev_joint, res, name="hev_joint_torque_set")
        rq = res.get("request") or {}
        info = {"p1_W": (rq.get("branch_P_dc_W") or [None, None])[0], "p2_W": (rq.get("branch_P_dc_W") or [None, None])[1],
                "p_src_W": rq.get("P_source_W"), "em1_pos": res["machines"][0]["role"], "em2_pos": res["machines"][1]["role"]}
        self.p_hsch.draw(SC.fig_hev_schematic, info, name="hev_system")
        self.h_tabs.setCurrentWidget(self.p_joint)
        rows = [(tr("기기 범위", "machine ranges"), "; ".join(f"{m['name']} @ {fmt(m['speed_rpm'])} rpm: "
                                                             f"[{fmt(m['range_Nm'][0])}, {fmt(m['range_Nm'][1])}] N·m"
                                                             for m in res["machines"])),
                (tr("동시 가능 셀 / 개별 가능 셀", "jointly / separately feasible cells"),
                 f"{res['joint_cells_feasible']} / {res['box_cells_feasible_separately']} ({res['policy']})")]
        if rq:
            rows += [(tr("요구", "request"), f"({fmt(rq['T1_Nm'])}, {fmt(rq['T2_Nm'])}) N·m → {rq['status']} {rq.get('reason') or ''}"),
                     (tr("가지 P_dc", "branch P_dc"), ", ".join(fmt(x) for x in rq["branch_P_dc_W"]) + " W"),
                     (tr("가지 I_dc", "branch I_dc"), ", ".join(fmt(x) for x in rq["branch_I_dc_A"]) + " A"),
                     (tr("기기 순합 / 배터리 / 순환", "machines net / battery / circulating"),
                      f"{fmt(rq['net_machines_W'])} / {fmt(rq['P_source_W'])} / {fmt(rq['circulating_W'])} W"),
                     (tr("주의", "note"), rq["note"])]
        rows.append((tr("의미", "meaning"), res["meaning"]))
        self.t_hev.set_rows(rows)

    def _show_crank(self, res):
        self.c_btn.setEnabled(True)
        self.last_crank = res
        self.p_crank.draw(F.fig_hev_crank, res, name="hev_cranking")
        self.h_tabs.setCurrentWidget(self.p_crank)
        c = res["claim"]
        rows = [(tr("판정", "claim"), f"{c['status']} — {c['detail']}" + (f" [{'; '.join(c['qualifiers'])}]" if c.get("qualifiers") else ""))]
        for r in res["runs"]:
            rows.append((f"θ0 = {fmt(r['theta0_deg'])}°",
                         f"{'OK' if r['ok'] else 'FAIL'}: t = {fmt(r['reached_s'])} s, V_min {fmt(r['V_bus_min_V'])} V, "
                         f"P_bat,max {fmt(r['P_bat_max_W'])} W, I_peak {fmt(r['I_peak_max_A'])} A, E {fmt(r['energy_J'])} J"
                         + (f" — {'; '.join(r['failures'])}" if r["failures"] else "")))
        for n in res["notes"]:
            rows.append((tr("주의", "note"), n))
        self.t_hev.set_rows(rows)

    def _show_rej(self, res):
        self.r_btn.setEnabled(True)
        self.last_rej = res
        self.p_rej.draw(F.fig_hev_rejection, res, name="hev_load_rejection",
                        csv=lambda res=res: {"t_s": res["trace"]["t_s"], "V_V": res["trace"]["V_V"],
                                             "P_excess_W": res["trace"]["P_excess_W"]})
        self.h_tabs.setCurrentWidget(self.p_rej)
        c = res["claim"]
        self.t_hev.set_rows([(tr("판정", "claim"), f"{c['status']} — {c['detail']}"),
                             (tr("잉여 전력", "excess power"), f"{fmt(res['P_excess_W'])} W"),
                             (tr("커패시터 여유 에너지", "capacitor margin"), f"{fmt(res['E_margin_J'])} J"),
                             (tr("한계 도달 시간", "time to limit"), f"{fmt(res['time_to_limit_s'])} s"),
                             (tr("최대 전압", "peak voltage"), f"{fmt(res['V_peak_V'])} V"),
                             (tr("장부", "ledger"), str(res["ledger"]))])

    def _show_pl(self, res):
        self.pl_btn.setEnabled(True)
        self.last_pl = res
        self.p_pl.draw(F.fig_planetary, res, name="hev_planetary")
        self.h_tabs.setCurrentWidget(self.p_pl)
        ch = res["check"]
        self.t_hev.set_rows([(tr("판정", "check"), f"{ch['status']} — {ch['detail']}"),
                             (tr("속도 [rpm]", "speeds [rpm]"), str({k: round(v, 1) for k, v in res["speeds_rpm"].items()})),
                             (tr("토크 [N·m]", "torques [N·m]"), str({k: round(v, 2) for k, v in res["torques_Nm"].items()})),
                             (tr("전력 [W]", "powers [W]"), str({k: round(v, 1) for k, v in res["powers_W"].items()})),
                             (tr("규약", "convention"), res["convention"])])

    def redraw(self):
        for p in (self.p_sets, self.p_opt, self.p_cmp, self.p_pair, self.p_rip, self.p_sch, self.p_joint, self.p_crank,
                  self.p_rej, self.p_pl, self.p_hsch):
            p.redraw()
