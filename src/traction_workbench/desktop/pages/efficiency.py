"""Efficiency by boundary and module comparison page (module-efficiency addendum).

Point: the five control volumes (inverter, motor, inverter+motor, reducer, eDrive) judged on their own ports, the
loss ledger with the known subtotal and the unknown items, auxiliaries at their supply port.  Maps: boundary
efficiencies at policy points (feasible cells only, UNKNOWN hatched).  Mission: E+/E- per port and direction.
Module A/B: fixed-policy or design-specific comparison of two module designs (e.g. IGBT vs SiC) on the same
delivered requirement, Tj as a result, ranking only beyond the declared error budget.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QPushButton, QScrollArea,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...plots import efficiency_figures as F
from ...insight.efficiency import ab_insight, aux_label, map_insight, mission_insight, point_insight
from ...insight.texts import engine_parts
from ..widgets import (Cell, ConceptNote, KeyValueTable, NumTable, PlotPanel, check, claim_cell, combo, error_box, fmt,
                       hint, number, primary_button, table_with_buttons, reading_tab, with_reading)

NOTE = lambda: tr(
    "<b>효율은 경계(control volume)의 성질</b>입니다. 포트: P_dc(선언한 인버터 HV DC 단자 입력), P_ac(모터 AC 단자 입력), "
    "P_m(모터 축 출력), P_o(선언한 감속기/eDrive 출력; 기어 출력·디퍼렌셜 출력·하프샤프트 합은 서로 다른 경계). 전기→기계를 양으로 "
    "둡니다.<br>• 구동: η = 출력/입력, 회생: η = |입력|/|출력| — 각 경계를 <b>자기 두 포트로 독립 판정</b>합니다(정지나 인버터 손실 "
    "누락이 다른 경계를 지우지 않음). 혼합 흐름·유용 출력 없음은 N/A, 알 수 없는 손실·0 근처 분모는 UNKNOWN, η>1은 clamp 없이 "
    "INCONSISTENT입니다.<br>• η_im = η_inv·η_m, η_ed = η_inv·η_m·η_r은 <b>같은 점·같은 방향</b>에서만 성립합니다. 서로 다른 점의 "
    "최고 효율을 곱하지 않습니다.<br>• 감속기는 방향별로 선언합니다(구동 η의 역수를 회생 η로 쓰지 않음). 데이터가 없으면 eDrive η는 "
    "UNKNOWN이며 100%로 채우지 않습니다.<br>• 효율 차이는 <b>%p</b>와 손실 W/에너지를 함께 봅니다(97.5→98.0%는 +0.5%p, 같은 입력에서 "
    "손실 20% 감소).",
    "<b>Efficiency is a property of a boundary (control volume)</b>. Ports: P_dc (declared inverter HV DC terminal input), "
    "P_ac (motor AC terminal input), P_m (motor shaft output), P_o (declared reducer / eDrive output; gearbox output, "
    "differential output and the half-shaft sum are different boundaries). Electrical -> mechanical is positive.<br>"
    "• Drive: eta = out/in, regen: eta = |in|/|out| - each boundary is <b>judged on its own two ports</b> (a standstill "
    "or a missing inverter loss does not erase the others). Mixed flow / no useful output is N/A, an unknown loss or a "
    "near-zero denominator is UNKNOWN, eta > 1 is INCONSISTENT (never clamped).<br>• eta_im = eta_inv eta_m and "
    "eta_ed = eta_inv eta_m eta_r hold only for the <b>same point and direction</b>; peak efficiencies of different "
    "points are never multiplied.<br>• The reducer is declared per direction (the motoring eta is never inverted for "
    "regen). Without reducer data the eDrive eta is UNKNOWN, not 100 %.<br>• Differences are shown in <b>percentage "
    "points</b> together with loss W / energy (97.5 -> 98.0 % is +0.5 pp and 20 % less loss at the same input).")

NOTE_AB = lambda: tr(
    "<b>모듈 A/B 프로토콜</b>: SiC가 항상 낫다는 규칙은 없습니다. 두 후보 모두 <b>같은 요구를 실제로 수행(FEASIBLE)</b>해야 순위를 "
    "매깁니다.<br>• 고정 정책: 같은 모터·전원·요구·PWM 주파수·냉각수 온도. 각 모듈은 자기 데이터·게이트/데드타임·열 경로를 쓰며 "
    "<b>Tj는 결과</b>입니다(같은 Tj를 강제하지 않음).<br>• 설계별 정책: 후보마다 선언한 fsw — EMC·dv/dt·리플·제어 타이밍은 "
    "다시 평가해야 하므로(여기서 미평가) 손실 비교이지 승인이 아닙니다.<br>• 순위는 차이가 <b>선언한 모듈별 오차 예산의 합</b>보다 클 "
    "때만 (공통 모터 오차는 같은 모터 운전점일 때만 상쇄). 예산이 없으면 추정만 표시하고 순위는 유보합니다. Typical 데이터는 생산 "
    "모집단의 승자가 아닙니다. 효율이 높아도 SOA·단락·수명·EMC 승인은 상속되지 않습니다.",
    "<b>Module A/B protocol</b>: no rule says SiC is always better. Both candidates must <b>deliver the same "
    "requirement (FEASIBLE)</b> before any ranking.<br>• Fixed policy: same motor, source, demand, PWM frequency and "
    "coolant temperature; each module keeps its own data, gate / dead time and thermal path and <b>Tj is a result</b> "
    "(never forced equal).<br>• Design-specific policy: each candidate at its declared fsw - EMC, dv/dt, ripple and "
    "control timing must be re-evaluated (not here), so it is a loss comparison, not an approval.<br>• A ranking only "
    "when the difference exceeds the <b>sum of the declared module error budgets</b> (the common motor error cancels "
    "only for the identical motor point). Without budgets the estimate is shown and the ranking reserved. Typical data "
    "do not rank a production population. Higher efficiency does not approve SOA, short circuit, life or EMC.")

MODULE_SOURCES = [("project module", "igbt"), ("project alternative", "sic"), ("power page module", "power"),
                  ("JSON file", "file")]


def _task(fn):
    def run(progress, body):
        progress(0.1, tr("계산 중", "computing"))
        return fn(body)
    return run


def _scroll(w):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    sc.setMinimumWidth(400)
    return sc


class EfficiencyPage(QWidget):
    workspace_data = ("_file_modules",)                  # module JSON files loaded for the A/B candidates

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last_point = self.last_map = self.last_mission = self.last_ab = None
        self._file_modules = {"A": None, "B": None}
        self.tabs = QTabWidget()
        self.tabs.addTab(self._eff_tab(), tr("경계별 효율", "efficiency by boundary"))
        self.tabs.addTab(self._ab_tab(), tr("모듈 A/B (IGBT·SiC)", "module A/B (IGBT·SiC)"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.tabs)

    # ================================================================== efficiency by boundary
    def _eff_tab(self):
        ex = self.win.state.example("EFFICIENCY")
        rd = self.win.state.example("REDUCER")
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("운전점·손실 모델", "operating point · loss model"))
        f = QFormLayout(g)
        self.e_n = number(ex["speed_rpm"], -30000, 30000, "rpm", 0, 500)
        self.e_T = number(ex["torque_Nm"], -5000, 5000, "N·m", 2, 10)
        self.e_vdc = number(ex["Vdc_V"], 1, 2000, "V", 1, 10)
        self.e_loss = combo([(tr("데이터시트 모듈 (프로젝트)", "datasheet module (project)"), "module"),
                             (tr("전력변환 페이지의 모듈", "module of the power page"), "power"),
                             (tr("모델의 2차 대리모델 a0 + a2 I²", "model's quadratic surrogate a0 + a2 I²"), "surrogate")],
                            "module")
        for lab, w in ((tr("속도", "speed"), self.e_n), (tr("축 토크", "shaft torque"), self.e_T), ("Vdc", self.e_vdc),
                       (tr("인버터 손실", "inverter loss"), self.e_loss)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("감속기 (방향별 선언)", "reducer (declared per direction)"))
        f = QFormLayout(g)
        self.r_on = check(tr("감속기 손실 모델 선언", "declare a reducer loss model"), True)
        self.r_ratio = number(rd["ratio"], 0.1, 100, "", 3, 0.5)
        self.r_ef = number(rd["eta_forward"], 0.5, 1.0, "", 4, 0.005)
        self.r_er = number(rd["eta_reverse"], 0.5, 1.0, "", 4, 0.005)
        self.r_c0 = number(rd["drag_coeffs"][0], 0, 100, "N·m", 4, 0.05)
        self.r_c1 = number(rd["drag_coeffs"][1] * 1e3, 0, 100, "mN·m·s/rad", 4, 0.05)
        self.r_oil = number(ex["oil_temp_C"], -40, 200, "°C", 1, 5)
        self.r_bnd = QLineEdit(rd["output_boundary"])
        self.r_basis = QLineEdit(rd["basis"])
        for lab, w in (("", self.r_on), (tr("기어비 g = ω_m/ω_out", "ratio g = ω_m/ω_out"), self.r_ratio),
                       (tr("η 구동 (모터→출력)", "η forward (motor→output)"), self.r_ef),
                       (tr("η 회생 (출력→모터)", "η reverse (output→motor)"), self.r_er),
                       (tr("드래그 c0", "drag c0"), self.r_c0), (tr("드래그 c1", "drag c1"), self.r_c1),
                       (tr("오일 온도", "oil temperature"), self.r_oil), (tr("출력 경계", "output boundary"), self.r_bnd),
                       (tr("근거", "basis"), self.r_basis)):
            f.addRow(lab, w)
        f.addRow(hint(tr("검증 범위: 0–16000 rpm, |T| ≤ 400 N·m, 오일 20–120 °C (예시). 밖이면 UNKNOWN.",
                         "validated: 0-16000 rpm, |T| <= 400 N m, oil 20-120 degC (example); outside: UNKNOWN.")))
        v.addWidget(g)
        g = QGroupBox(tr("보조 전력 (공급 포트에서 한 번)", "auxiliaries (counted once, at their supply port)"))
        f = QFormLayout(g)
        self.a_lv = number(ex["aux"][0]["P_W"], 0, 1e4, "W", 1, 5)
        f.addRow(tr("외부 LV 공급 (게이트 드라이버·제어기)", "external LV supply (gate drivers, controller)"), self.a_lv)
        v.addWidget(g)
        g = QGroupBox(tr("지도·미션", "maps · mission"))
        gl = QVBoxLayout(g)
        f = QFormLayout()
        self.m_sp = QLineEdit(" ".join(f"{x:g}" for x in ex["map_speeds_rpm"]))
        self.m_tq = QLineEdit(" ".join(f"{x:g}" for x in ex["map_torques_Nm"]))
        f.addRow(tr("지도 속도 [rpm]", "map speeds [rpm]"), self.m_sp)
        f.addRow(tr("지도 토크 [N·m]", "map torques [N·m]"), self.m_tq)
        gl.addLayout(f)
        self.t_mis = NumTable([tr("시간 [s]", "duration [s]"), "n [rpm]", tr("축 T [N·m]", "shaft T [N·m]")], min_height=140)
        self.t_mis.load([[s["duration_s"], s["speed_rpm"], s["torque_Nm"]] for s in ex["mission"]["segments"]])
        gl.addWidget(table_with_buttons(self.t_mis, tr("미션 구간 (모터 축 토크)", "mission segments (motor-shaft torque)")))
        v.addWidget(g)
        row = QHBoxLayout()
        self.e_btn = primary_button(tr("운전점 (5경계)", "point (5 boundaries)"))
        self.e_btn.clicked.connect(self.run_point)
        self.e_map_btn = QPushButton(tr("효율 지도", "efficiency maps"))
        self.e_map_btn.clicked.connect(self.run_map)
        self.e_mis_btn = QPushButton(tr("미션 에너지", "mission energy"))
        self.e_mis_btn.clicked.connect(self.run_mission)
        for b in (self.e_btn, self.e_map_btn, self.e_mis_btn):
            b.setMinimumHeight(34)
            row.addWidget(b)
        v.addLayout(row)
        v.addWidget(ConceptNote(NOTE()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(('efficiency', 'efficiency_map', 'efficiency_mission'), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.e_tabs = QTabWidget()
        self.p_point = PlotPanel(hint=tr("'운전점 (5경계)'를 누르세요", "press 'point (5 boundaries)'"))
        self.p_map = PlotPanel(hint=tr("'효율 지도'를 누르세요", "press 'efficiency maps'"))
        self.p_mis = PlotPanel(hint=tr("'미션 에너지'를 누르세요", "press 'mission energy'"))
        for p, lab in ((self.p_point, tr("운전점", "point")), (self.p_map, tr("경계별 지도", "maps by boundary")),
                       (self.p_mis, tr("미션", "mission"))):
            self.e_tabs.addTab(p, lab)
        self.i_eff = reading_tab(self.e_tabs, tr(
            "계산하면 해석이 표시됩니다 — 운전점: 포트 사이 전력 흐름과 경계별 손실, 손실 원장, 어느 손실을 줄이면 효과가 큰지 · 지도: "
            "경계별 최고·최저 효율과 위치 · 미션: 경계별·구간별 손실 에너지.",
            "Run to read the result — point: the power flow between the ports and each boundary's loss, the loss ledger, "
            "which loss matters most · maps: best and worst efficiency per boundary and where · mission: loss energy per "
            "boundary and per segment."))
        self.k_eff = KeyValueTable()
        rl.addWidget(self.e_tabs, 3)
        rl.addWidget(self.k_eff, 2)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([440, 1020])
        return split

    def _reducer_spec(self):
        if not self.r_on.isChecked():
            return None
        rd = self.win.state.example("REDUCER")                  # fields without a widget pass through
        rd.update({"ratio": self.r_ratio.value(), "eta_forward": self.r_ef.value(), "eta_reverse": self.r_er.value(),
                   "drag_coeffs": [self.r_c0.value(), self.r_c1.value() * 1e-3, 0.0],
                   "output_boundary": self.r_bnd.text().strip(), "basis": self.r_basis.text().strip()})
        return rd

    def eff_body(self) -> dict:
        b = self.win.state.example("EFFICIENCY")
        src = self.e_loss.currentData()
        b.update({"speed_rpm": self.e_n.value(), "torque_Nm": self.e_T.value(), "Vdc_V": self.e_vdc.value(),
                  "loss_model": "surrogate" if src == "surrogate" else "module",
                  "reducer": self._reducer_spec(), "oil_temp_C": self.r_oil.value(),
                  "aux": [{"name": "gate drivers + controller (LV)", "P_W": self.a_lv.value(), "supply": "lv_external",
                           "basis": "entered"}] if self.a_lv.value() > 0 else []})
        if src == "power":
            b["module"] = self.win.pages["power"].module_spec()
        sp = [float(x) for x in self.m_sp.text().split()]
        tq = [float(x) for x in self.m_tq.text().split()]
        if len(sp) < 2 or len(tq) < 2:
            raise ValueError(tr("지도 축에는 값이 2개 이상 필요합니다", "map axes need at least two values"))
        b.update({"map_speeds_rpm": sp, "map_torques_Nm": tq})
        b["mission"] = {"torque_at": "motor_shaft", "distance_km": None,
                        "segments": [{"duration_s": r[0], "speed_rpm": r[1], "torque_Nm": r[2]} for r in self.t_mis.values()]}
        b.update(self.win.state.body())
        return b

    def _start(self, key, label, fn, show, body, btn):
        btn.setEnabled(False)
        self.win.runner.run(key, label, _task(fn), show, body, on_error=self._err)

    def _err(self, msg, tb):
        for b in (self.e_btn, self.e_map_btn, self.e_mis_btn, self.ab_btn):
            b.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _run(self, key, label, fn, show, btn):
        try:
            body = self.eff_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self._start(key, label, fn, show, body, btn)

    def run_point(self):
        self._run("efficiency", tr("경계별 효율", "boundary efficiency"), api.efficiency, self._show_point, self.e_btn)

    def run_map(self):
        self._run("efficiency_map", tr("효율 지도", "efficiency maps"), api.efficiency_map, self._show_map, self.e_map_btn)

    def run_mission(self):
        self._run("efficiency_mission", tr("미션 에너지", "mission energy"), api.efficiency_mission, self._show_mission,
                  self.e_mis_btn)

    def _show_point(self, res):
        self.e_btn.setEnabled(True)
        self.last_point = res
        self.p_point.draw(F.fig_efficiency_point, res, name="efficiency_point")
        if self.e_tabs.currentWidget() is not self.i_eff:
            self.e_tabs.setCurrentWidget(self.p_point)
        self.i_eff.read("point", tr("운전점", "point"), point_insight, res)
        rows = [(claim_cell(c["name"]), f"{c['status']} — {engine_parts(c.get('detail') or '')}")
                for c in res.get("claims", [])]
        led = res.get("ledger")
        if led:
            for k, r in led["boundaries"].items():
                if k == "telescoping_residuals":
                    continue
                val = f"{100 * r['eta']:.4f}% ({r['definition']}, {r['direction']})" if r["status"] == "DEFINED" else \
                    f"{r['status']} — {r.get('reason', '')}"
                iv = r.get("eta_interval_incl_pwm_hf")
                if iv:
                    val += tr(" · PWM 고조파 손실 포함 η ∈ ", " · incl. PWM harmonic loss η ∈ ") + (
                        f"[{'—' if iv[0] is None else f'{100 * iv[0]:.3f}'}, {100 * iv[1]:.3f}] %")
                rows.append((f"η {r['label']}", val + (f" · {r['qualifier']}" if r.get("qualifier") else "")))
            rows.append((tr("알려진 손실 소계", "known loss subtotal"), f"{fmt(led['loss_known_subtotal_W'])} W"))
            lo, hi = led.get("loss_interval_W") or (None, None)
            rows.append((tr("손실 구간 (미상·상한 포함)", "loss interval (open items and bounds)"),
                         f"[{fmt(lo)}, {fmt(hi) if hi is not None else '∞'}] W"))
            for it in led["loss_items"]:
                if it["item"].startswith("PWM"):
                    v = (f"{fmt(it['W'])} W" if it["W"] is not None else
                         f"≥ {fmt(it['lower_bound_W'])} W" if it.get("lower_bound_W") is not None else
                         f"≤ {fmt(it['upper_bound_W'])} W" if it.get("upper_bound_W") is not None else "UNKNOWN")
                    rows.append((f"{it['item']}", f"{v} · {it.get('status', '')} · {it.get('basis', '')}"))
            hf = led.get("pwm_hf")
            if hf:
                rows.append((tr("PWM 고조파 입력", "PWM harmonic inputs"),
                             f"L_hf {fmt(hf['L_hf_H'] and hf['L_hf_H'] * 1e6)} µH · fsw {fmt(hf['fsw_requested_Hz'] / 1e3)} kHz"
                             f" ({tr('파형', 'waveform')} {fmt(hf['fsw_waveform_used_Hz'] / 1e3)} kHz) · m "
                             f"{fmt(hf['modulation_index'], 4)} · {hf['basis']}"))
            rows.append((tr("미상 손실 항", "unknown loss items"), ", ".join(led["loss_unknown_items"]) or tr("없음", "none")))
            sens = led.get("loss_sensitivity") or []
            if sens:
                rows.append((tr("손실 민감도 (각 항 +10%, 인버터+모터 η)", "loss sensitivity (each item +10 %, inverter+motor η)"),
                             " · ".join(f"{x['item']}: {x['delta_eta_points']:+.3f} %p" for x in sens)))
            rot = next((i for i in led["loss_items"] if i["item"].startswith("rotational")), None)
            if rot and rot.get("scope"):
                rows.append((tr("회전·철손 항의 범위", "scope of the rotational / iron item"), rot["scope"]))
            for k, val in (led.get("aux_metrics") or {}).items():
                rows.append((Cell(aux_label(k), k), f"{100 * val:.3f}%"))
            sc = led["inverter_scope"]
            rows.append((tr("인버터 경계", "inverter boundary"), f"{sc.get('model')}: {', '.join(sc.get('included', []))}"))
            rows.append((tr("인버터 경계 밖", "outside the inverter boundary"), ", ".join(sc.get("excluded", []))))
            red = led.get("reducer")
            if red:
                ev = red.get("evaluation") or {}
                rows.append((tr("감속기", "reducer"), f"{red['form']} · {ev.get('status')} {ev.get('reason', '')}"))
        else:
            rows.append((tr("운전점 없음", "no point"), res.get("reason", "")))
        self.k_eff.set_rows(rows)

    def _show_map(self, res):
        self.e_map_btn.setEnabled(True)
        self.last_map = res
        import numpy as np

        def csv(mp=res):
            sp, tq = np.meshgrid(mp["speeds_rpm"], mp["torques_Nm"])
            out = {"speed_rpm": sp.ravel(), "torque_Nm": tq.ravel(), "status": np.asarray(mp["status"]).ravel()}
            for k, g in mp["grids"].items():
                out[f"eta_{k}"] = np.asarray(g, float).ravel()
            out["loss_known_W"] = np.asarray(mp["loss_known_W"], float).ravel()
            return out
        self.p_map.draw(F.fig_efficiency_maps, res, name="efficiency_maps", csv=csv)
        if self.e_tabs.currentWidget() is not self.i_eff:
            self.e_tabs.setCurrentWidget(self.p_map)
        self.i_eff.read("map", tr("경계별 지도", "maps by boundary"), map_insight, res)

    def _show_mission(self, res):
        self.e_mis_btn.setEnabled(True)
        self.last_mission = res
        self.p_mis.draw(F.fig_efficiency_mission, res, name="efficiency_mission",
                        csv=lambda r=res: {k: [s.get(k) if k in s else s["ports_W"].get(k) for s in r["segments"]]
                                           for k in ("duration_s", "speed_rpm", "torque_Nm", "status", "P_dc", "P_ac",
                                                     "P_m", "P_o")})
        if self.e_tabs.currentWidget() is not self.i_eff:
            self.e_tabs.setCurrentWidget(self.p_mis)
        self.i_eff.read("mission", tr("미션", "mission"), mission_insight, res)
        e = res["energy"]
        kwh = 3.6e6
        rows = [(tr("요구 수행", "requirement delivered"), tr("모든 구간", "every segment") if res["delivered"] else
                 tr("일부 구간 미달성 — 에너지 순위 금지", "not every segment - no energy ranking")),
                (tr("구동 η (에너지 비)", "traction η (energy ratio)"), fmt(e["eta_traction"] and 100 * e["eta_traction"], 5) + " %"),
                (tr("회생 η (에너지 비)", "regeneration η (energy ratio)"),
                 fmt(e["eta_regeneration"] and 100 * e["eta_regeneration"], 5) + " %"),
                (tr("순 DC / 순 출력", "net DC / net output"),
                 f"{e['E_dc_net_J'] / kwh * 1e3:.2f} Wh / {e['E_out_net_J'] / kwh * 1e3:.2f} Wh ({tr('비율은 효율 아님', 'ratio is not an efficiency')})")]
        rows.append((tr("출력 포트", "output port"), e.get("output_port", "P_o") +
                     ("" if e.get("output_port", "P_o") == "P_o" else
                      tr(" (P_o가 모든 구간에 없음: 모터 축 경계로 대체)", " (P_o not known in every segment: motor-shaft boundary)"))))
        pt = e.get("partial")
        if pt:
            rows.append((tr("미션 방향 효율", "mission direction efficiency"),
                         tr(f"UNKNOWN — {pt['undetermined_s']:.1f} s 구간의 포트 전력 미상 (알려진 구간만: 구동 "
                            f"{fmt(pt['eta_traction_partial'] and 100 * pt['eta_traction_partial'], 5)} %, 회생 "
                            f"{fmt(pt['eta_regeneration_partial'] and 100 * pt['eta_regeneration_partial'], 5)} %)",
                            f"UNKNOWN - port powers missing for {pt['undetermined_s']:.1f} s (known segments only: traction "
                            f"{fmt(pt['eta_traction_partial'] and 100 * pt['eta_traction_partial'], 5)} %, regeneration "
                            f"{fmt(pt['eta_regeneration_partial'] and 100 * pt['eta_regeneration_partial'], 5)} %)")))
        for k in ("P_dc", "P_ac", "P_m", "P_o"):
            rows.append((f"{k} E+ / E−", f"{e['E_pos_J'][k] / kwh * 1e3:.3f} / {e['E_neg_J'][k] / kwh * 1e3:.3f} Wh"))
        rows.append((tr("구간 분류", "segment classes"), {k: round(v.get("t", 0.0), 2) for k, v in e["segments"].items()}))
        self.k_eff.set_rows(rows)

    # ================================================================== module A/B
    def _ab_tab(self):
        c = self.win.state.example("EFFICIENCY")["compare"]
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("비교 모드", "comparison mode"))
        f = QFormLayout(g)
        self.ab_mode = combo([(tr("고정 정책 (같은 fsw·냉각수)", "fixed policy (same fsw, coolant)"), "fixed_policy"),
                              (tr("설계별 정책 (후보별 fsw)", "design-specific policy (fsw per candidate)"), "design_specific")],
                             c["mode"])
        self.ab_fsw = number(c["common_fsw_kHz"], 0.5, 200, "kHz", 2, 1)
        self.ab_cool = number(c["coolant_C"], -40, 120, "°C", 1, 5)
        self.ab_mis = check(tr("미션 에너지 포함 (경계별 효율 탭의 미션)", "include mission energy (mission of the boundary tab)"), True)
        for lab, w in ((tr("모드", "mode"), self.ab_mode), (tr("공통 fsw (고정 정책)", "common fsw (fixed policy)"), self.ab_fsw),
                       (tr("냉각수 온도", "coolant temperature"), self.ab_cool), ("", self.ab_mis)):
            f.addRow(lab, w)
        v.addWidget(g)
        self.ab = {}
        for tag, default in (("A", "igbt"), ("B", "sic")):
            cc = c[tag]
            g = QGroupBox(tr(f"후보 {tag}", f"candidate {tag}"))
            f = QFormLayout(g)
            src = combo([(lab, key) for lab, key in MODULE_SOURCES], default)
            lbl = QLineEdit(cc["label"])
            rth = number(cc["Rth_K_per_W"], 0.001, 10, "K/W", 4, 0.01)
            fsw = number(c.get(f"{tag}_fsw_kHz") or c["common_fsw_kHz"], 0.5, 200, "kHz", 2, 1)
            err_on = check(tr("손실 오차 예산 선언", "declare a loss error budget"), cc["loss_error_rel"] is not None)
            err = number(100 * (cc["loss_error_rel"] or 0.1), 0, 99, "%", 1, 1)
            basis = QLineEdit(cc["error_basis"])
            load = QPushButton(tr("모듈 JSON…", "module JSON…"))
            load.clicked.connect(lambda _=False, t=tag: self._load_file(t))
            for lab, w in ((tr("모듈 데이터", "module data"), src), ("", load), (tr("이름", "label"), lbl),
                           (tr("Rth (최고온 위치→냉각수)", "Rth (hottest position→coolant)"), rth),
                           (tr("fsw (설계별 정책)", "fsw (design-specific)"), fsw), ("", err_on),
                           (tr("오차 예산 (모듈 손실 대비)", "error budget (of module loss)"), err), (tr("근거", "basis"), basis)):
                f.addRow(lab, w)
            v.addWidget(g)
            self.ab[tag] = {"src": src, "label": lbl, "rth": rth, "fsw": fsw, "err_on": err_on, "err": err, "basis": basis}
        g = QGroupBox(tr("비교 요구점 (같은 요구를 두 후보가 수행)", "requirement points (both candidates deliver them)"))
        gl = QVBoxLayout(g)
        self.t_req = NumTable(["n [rpm]", tr("축 T [N·m]", "shaft T [N·m]"), "Vdc [V]"], min_height=120)
        self.t_req.load(c["requests"])
        gl.addWidget(table_with_buttons(self.t_req))
        v.addWidget(g)
        self.ab_btn = primary_button(tr("A/B 비교", "A/B comparison"))
        self.ab_btn.clicked.connect(self.run_ab)
        v.addWidget(self.ab_btn)
        v.addWidget(hint(tr("예시 모듈·Rth·오차 예산은 합성 값입니다 (실제 제품 아님).",
                            "Example modules, Rth and error budgets are synthetic (not products).")))
        v.addWidget(ConceptNote(NOTE_AB()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(('module_compare',), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.p_ab = PlotPanel(hint=tr("'A/B 비교'를 누르세요", "press 'A/B comparison'"))
        self.k_ab = KeyValueTable()
        rl.addWidget(self.p_ab, 3)
        rl.addWidget(self.k_ab, 2)
        self.ab_tabs, self.i_ab = with_reading(right, tr(
            "비교하면 해석이 표시됩니다 — 운전점별 손실 차와 오차 예산, 차이가 도통·스위칭 중 어디서 오는지, T_j, 미션 인버터 손실.",
            "Run to read the comparison — the loss difference per point against the error budget, whether it comes from "
            "conduction or switching, T_j, the mission inverter loss."))
        split.addWidget(self.ab_tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([440, 1020])
        return split

    def apply_project(self, _project=None):
        """Reducer and A/B design data from the active project (operating points, maps, missions stay)."""
        rd = self.win.state.example("REDUCER")
        self.r_ratio.setValue(float(rd["ratio"]))
        self.r_ef.setValue(float(rd["eta_forward"]))
        self.r_er.setValue(float(rd["eta_reverse"]))
        self.r_c0.setValue(float(rd["drag_coeffs"][0]))
        self.r_c1.setValue(float(rd["drag_coeffs"][1]) * 1e3)
        self.r_bnd.setText(str(rd.get("output_boundary", "")))
        self.r_basis.setText(str(rd.get("basis", "")))
        c = self.win.state.example("EFFICIENCY")["compare"]
        self.ab_fsw.setValue(float(c["common_fsw_kHz"]))
        for tag in ("A", "B"):
            w, cc = self.ab[tag], c[tag]
            w["label"].setText(str(cc["label"]))
            w["rth"].setValue(float(cc["Rth_K_per_W"]))

    def _load_file(self, tag):
        path, _ = QFileDialog.getOpenFileName(self, tr("모듈 JSON 불러오기", "load module JSON"), "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                m = json.load(fh)
            api.module_model_from_dict(m)
            self._file_modules[tag] = m
            w = self.ab[tag]["src"]
            w.setCurrentIndex(w.findData("file"))
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("불러오기 실패", "load failed"), str(exc))

    def _module(self, tag):
        key = self.ab[tag]["src"].currentData()
        if key == "igbt":
            return self.win.state.example("MODULE")
        if key == "sic":
            return self.win.state.example("MODULE_SIC")
        if key == "power":
            return self.win.pages["power"].module_spec()
        if self._file_modules[tag] is None:
            raise ValueError(tr(f"후보 {tag}: 모듈 JSON 파일을 먼저 불러오세요", f"candidate {tag}: load a module JSON first"))
        return self._file_modules[tag]

    def ab_body(self) -> dict:
        b = self.eff_body() if self.ab_mis.isChecked() else {**self.win.state.example("EFFICIENCY"), "mission": None,
                                                              **self.win.state.body()}
        cmp = {"mode": self.ab_mode.currentData(), "common_fsw_kHz": self.ab_fsw.value(), "coolant_C": self.ab_cool.value(),
               "requests": self.t_req.values(), "include_mission": self.ab_mis.isChecked()}
        for tag in ("A", "B"):
            w = self.ab[tag]
            cmp[tag] = {"label": w["label"].text().strip() or tag, "module": self._module(tag), "Rth_K_per_W": w["rth"].value(),
                        "loss_error_rel": w["err"].value() / 100 if w["err_on"].isChecked() else None,
                        "error_basis": w["basis"].text().strip()}
            cmp[f"{tag}_fsw_kHz"] = w["fsw"].value()
        if not cmp["requests"]:
            raise ValueError(tr("비교 요구점이 없습니다", "no requirement points"))
        b["compare"] = cmp
        return b

    def run_ab(self):
        try:
            body = self.ab_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self._start("module_compare", tr("모듈 A/B", "module A/B"), api.module_compare, self._show_ab, body, self.ab_btn)

    def _show_ab(self, res):
        self.ab_btn.setEnabled(True)
        self.last_ab = res
        self.p_ab.draw(F.fig_module_compare, res, name="module_ab_comparison",
                       csv=lambda r=res: {"speed_rpm": [x["speed_rpm"] for x in r["rows"]],
                                          "torque_Nm": [x["torque_Nm"] for x in r["rows"]],
                                          "Pinv_A_W": [(x["A"].get("point") or {}).get("Pinv_W") for x in r["rows"]],
                                          "Pinv_B_W": [(x["B"].get("point") or {}).get("Pinv_W") for x in r["rows"]],
                                          "Tj_A_C": [x["A"].get("Tj_C") for x in r["rows"]],
                                          "Tj_B_C": [x["B"].get("Tj_C") for x in r["rows"]],
                                          "verdict": [x["compare"]["verdict"] for x in r["rows"]]})
        rows = []
        for c in res["candidates"]:
            budget = "—" if c["loss_error_rel"] is None else f"{100 * c['loss_error_rel']:g} %"
            rows.append((c["name"], f"{c['technology']} · {c['value_kind']} · fsw {c['fsw_Hz'] / 1e3:g} kHz · dead time "
                                    f"{c['deadtime_s'] * 1e6:g} µs · Rth {c['Rth_K_per_W']:g} K/W · budget {budget}"))
        for r in res["rows"]:
            cv = r["compare"]
            rows.append((f"{r['speed_rpm']:g} rpm / {r['torque_Nm']:g} N·m / {r['Vdc_V']:g} V",
                         f"{cv['verdict']}: {cv.get('reason', '')}"))
        mc = (res.get("mission") or {}).get("compare")
        if mc:
            rows.append((tr("미션", "mission"), f"{mc['verdict']}: {mc.get('reason', '')}"))
        rows.append((tr("여기서 평가하지 않은 것", "not evaluated here"), "; ".join(res["not_evaluated"])))
        rows.append((tr("의미", "meaning"), res["meaning"]))
        self.k_ab.set_rows(rows)
        self.i_ab.read("module_compare", tr("모듈 A/B", "module A/B"), ab_insight, res)

    def redraw(self):
        for p in (self.p_point, self.p_map, self.p_mis, self.p_ab, self.i_eff, self.i_ab):
            p.redraw()
