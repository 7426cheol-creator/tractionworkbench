"""Thermal screening: coolant loop + Foster/Cauer networks -> torque availability and node temperatures."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QLabel, QPlainTextEdit, QScrollArea, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from ... import api
from ...extensions.coolant import eg_water_properties
from ...i18n import tr
from ...plots import figures as F
from ...plots import review_figures as RF
from ...plots import schematics as SC
from ...plots.labels import reason_label
from ...viz import safety as SF
from ..thermal_editor import ThermalModelEditor
from ...insight.texts import engine_parts, engine_text
from ...insight.thermal import cycle_insight, thermal_insight
from ..widgets import (ConceptNote, KeyValueTable, PlotPanel, check, combo, error_box, fmt, hint, integer, number,
                       primary_button, reading_tab)

DURATIONS = [round(float(x), 4) for x in np.geomspace(0.1, 3000, 21)] + ["inf"]


def note_thermal():
    return tr("<b>열 스크리닝</b>: 운전점 손실 P(동손·인버터 손실·회전 손실)이 각 노드를 가열합니다. "
              "T_node(t) = T_냉각수(기준) + P·Z_th(t),  Z_th(t) = Σ R_i·(1 − e<sup>−t/τ_i</sup>).<br>"
              "<b>냉각수</b>: 질량유량 ṁ = ρ·Q, 열용량률 ṁ·c_p [W/K]. 냉각수는 순환 순서대로 각 부품의 열 P_k를 받아 "
              "ΔT_k = P_k/(ṁ·c_p)만큼 올라가며, 노드는 자기 위치(인버터 냉각판/모터 워터재킷)의 입구·평균·출구 온도를 기준으로 합니다. "
              "기본값은 에틸렌글리콜:물 = 50:50(부피) 일반 물성(근사)이며 공급사 값으로 바꿀 수 있습니다.<br>"
              "<b>Foster</b>는 데이터시트의 r_i, τ_i를 그대로 쓰는 곡선 맞춤(단 사이 절점은 물리 온도 아님), "
              "<b>Cauer</b>는 층별 R_i, C_i(접합부→냉각수)이며 계산 전에 정확히 Foster로 변환합니다.<br>"
              "검증되지 않은 열모델은 스크리닝 추정이며 지속시간 판정은 UNKNOWN으로 남습니다. 위 지속시간 계산은 일정 손실·냉간 시작의 "
              "스텝 응답이고, <b>반복 부하</b>는 펄스–휴지 반복, 고온 시작(예부하 정상상태 또는 Cauer 노드 온도), R_s(T)·모듈 T_j "
              "손실 피드백을 포함합니다(주기 정상상태는 주기 사상의 고정점으로 정확히 구함).",
              "<b>Thermal screening</b>: operating-point losses heat each node: T(t) = T_coolant,ref + P·Z_th(t), "
              "Z_th(t) = Σ R_i(1 − e<sup>−t/τ_i</sup>).<br><b>Coolant</b>: ṁ = ρ·Q, capacity rate ṁ·c_p [W/K]; the coolant "
              "picks up each station's heat in loop order (ΔT_k = P_k/(ṁ·c_p)) and a node references its station's inlet, "
              "mean or outlet temperature. Defaults are generic 50/50 (vol) ethylene-glycol/water properties (approximate).<br>"
              "<b>Foster</b> uses datasheet r_i, τ_i (inner nodes not physical); <b>Cauer</b> uses layer R_i, C_i and is "
              "converted exactly to Foster.<br>Unvalidated models give screening estimates; the duration claim stays UNKNOWN. "
              "The duration result above is a constant-loss step response from a cold start; the <b>repeated load</b> "
              "covers pulse-rest repetition, hot starts (steady state of a preload or Cauer node temperatures) and "
              "R_s(T) / module T_j loss feedback (the periodic cycle solved exactly as the fixed point of the cycle map).")


def _task(progress, body, spec):
    progress(0.05, tr("가용 토크 (지속시간별 bisection)", "availability (bisection per duration)"))
    res = api.thermal(body)
    progress(0.9, tr("노드 온도", "node temperatures"))
    model = api.thermal_model_from_dict(spec, body["coolant_temp_C"])
    req = res.get("request") or {}
    curves = None
    if req.get("nodes"):
        ttl = [float(n["time_to_limit_s"]) for n in req["nodes"] if isinstance(n["time_to_limit_s"], (int, float))]
        t_end = max([body["duration_s"] * 3] + [3 * t for t in ttl if math.isfinite(t)] + [10.0])
        curves = SF.thermal_curves(model, req["nodes"], body["coolant_temp_C"], t_end,
                                   trace=(req.get("coupled") or {}).get("trace"))
    ask = {k: body.get(k) for k in ("torque_Nm", "speed_rpm", "duration_s", "Vdc_V", "coolant_temp_C")}
    return {"res": res, "curves": curves, "validated": model.validated, "ask": ask}


class ThermalPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last = None
        self.last_cycle = None
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("운전 조건", "operating condition"))
        f = QFormLayout(g)
        self.n = number(3000, -30000, 30000, "rpm", 0, 100)
        self.vdc = number(600, 1, 2000, "V", 1, 10)
        self.T = number(450, -5000, 5000, "N·m", 2, 5)
        self.dur = number(10, 0.01, 1e6, "s", 2, 1)
        self.init = combo([(tr("냉각수 온도 평형에서 시작 (모델링된 유일한 시작)", "start at equilibrium with the coolant "
                               "(the only modelled start)"), "equilibrium_at_coolant"),
                           (tr("고온 시작 / 이전 부하 직후 (이 계산 밖 → UNKNOWN; 아래 '반복 부하'에서 계산)",
                               "hot start / right after a previous load (outside this calculation → UNKNOWN; see "
                               "'repeated load' below)"), "hot_start_after_load"),
                           (tr("미선언 (UNKNOWN)", "not stated (UNKNOWN)"), "")], "equilibrium_at_coolant")
        self.init.setToolTip(tr("지속시간 판정은 초기 열 상태에 의존합니다. 선언하지 않거나 모델이 표현하지 못하는 시작 상태는 "
                                "냉간 시작으로 가정하지 않고 UNKNOWN입니다.",
                                "A duration verdict depends on the initial thermal state; an undeclared start or one the "
                                "model cannot represent is UNKNOWN, never assumed cold."))
        for lab, wd in ((tr("속도", "speed"), self.n), ("Vdc", self.vdc), (tr("요구 토크", "requested torque"), self.T),
                        (tr("요구 지속시간", "requested duration"), self.dur), (tr("초기 열 상태", "initial thermal state"), self.init)):
            f.addRow(lab, wd)
        v.addWidget(g)
        op_box = g
        cool_box = self._coolant_box()
        v.addWidget(cool_box)
        self.run_btn = primary_button(tr("열 가용성 계산", "compute thermal availability"))
        self.run_btn.clicked.connect(self.run)
        v.addWidget(self.run_btn)
        v.addWidget(hint(tr("열 회로망(노드·단)은 오른쪽 '열 모델 편집' 탭에서 표로 입력합니다.",
                            "Edit the thermal networks (nodes, stages) in the 'thermal model' tab on the right.")))
        cyc_box = self._cycle_box()
        v.addWidget(cyc_box)
        v.addWidget(ConceptNote(note_thermal()))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(360)
        split.addWidget(sc)
        self.tabs = QTabWidget()
        res = QWidget()
        rv = QVBoxLayout(res)
        rv.setContentsMargins(0, 0, 0, 0)
        self.headline = QLabel(tr("냉각수·요구를 입력하고 계산하세요.", "Enter the coolant and request, then compute."))
        self.headline.setWordWrap(True)
        self.headline.setObjectName("Card")
        self.headline.setStyleSheet("padding: 10px; font-size: 11pt;")
        rv.addWidget(self.headline)
        self.plot = PlotPanel()
        rv.addWidget(self.plot, 3)
        self.table = KeyValueTable(headers=[tr("지속시간", "duration"), tr("가용 토크 [N·m]", "available torque [N·m]"),
                                            tr("제한", "limited by")])
        rv.addWidget(self.table, 1)
        self.p_net = PlotPanel(min_height=420)
        self.p_zth = PlotPanel()
        self.editor = ThermalModelEditor()
        self.editor.reset_source = lambda: self.win.state.example("THERMAL")
        self.editor.load(self.win.state.example("THERMAL"))
        self.tabs.addTab(res, tr("결과", "results"))
        cyc = QWidget()
        cv = QVBoxLayout(cyc)
        cv.setContentsMargins(0, 0, 0, 0)
        self.p_cyc = PlotPanel()
        self.t_cyc = KeyValueTable(headers=[tr("항목", "item"), tr("값", "value")])
        cv.addWidget(self.p_cyc, 3)
        cv.addWidget(self.t_cyc, 2)
        self.p_cyc.placeholder(tr("왼쪽 '반복 부하 · 고온 시작'에서 계산하세요.", "Compute from 'repeated load · hot start' on "
                                                                          "the left."))
        self.cyc_tab = cyc
        self.tabs.addTab(cyc, tr("반복 부하", "repeated load"))
        net_scroll = QScrollArea()
        net_scroll.setWidgetResizable(True)
        net_scroll.setWidget(self.p_net)
        self.net_tab = net_scroll
        self.tabs.addTab(net_scroll, tr("열 회로도·냉각수 순환", "thermal network · coolant loop"))
        self.tabs.addTab(self.p_zth, "Z_th(t)")
        # the editor keeps its minimum size and scrolls in a small window (it never squeezes its fields together)
        ed_scroll = QScrollArea()
        ed_scroll.setWidgetResizable(True)
        ed_scroll.setWidget(self.editor)
        self.editor_tab = ed_scroll
        self.tabs.addTab(ed_scroll, tr("열 모델 편집", "thermal model"))
        self.res_tab = res
        self.insight = reading_tab(self.tabs, tr(
            "계산하면 해석이 표시됩니다 — 어느 노드가 먼저 한계에 닿는지와 그 시간, 연속 운전 시 노드별 정상 온도 대 한계, 열원 분담, "
            "냉각수 온도 상승 · 반복 부하: 주기 정상 최고 온도와 여유, 허용 펄스·휴지.",
            "Run to read the result — which node reaches its limit first and when, each node's steady temperature against "
            "its limit, the heat sources, the coolant rise · repeated load: the periodic peaks and margins, the allowed "
            "pulse and rest."))
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 1040])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._refresh_diagrams)
        self.editor.changed.connect(self._schedule)
        self._apply_coolant_spec(self.editor.coolant_spec)
        self._refresh_diagrams()
        # availability: operating point, coolant and the networks; repeated load: its own cycle, speed, Vdc, coolant
        self.win.track_inputs("thermal", op_box, cool_box, self.editor)
        self.win.track_inputs("thermal_cycle", cyc_box, self.n, self.vdc, cool_box, self.editor)

    # ------------------------------------------------------------------ repeated load
    def _cycle_box(self):
        ex = api.EXAMPLE_THERMAL_CYCLE
        ph, fb = ex["phases"], ex["feedback"]
        g = QGroupBox(tr("반복 부하 · 고온 시작", "repeated load · hot start"))
        f = QFormLayout(g)
        self.cy_T = number(ph[0]["torque_Nm"], -5000, 5000, "N·m", 1, 5)
        self.cy_d = number(ph[0]["duration_s"], 0.01, 1e5, "s", 2, 1)
        self.cy_rT = number(ph[1]["torque_Nm"], -5000, 5000, "N·m", 1, 5)
        self.cy_rd = number(ph[1]["duration_s"], 0.01, 1e6, "s", 2, 1)
        self.cy_n = integer(ex["cycles"], 1, 500, tr("주기", "cycles"))
        self.cy_init = combo([(tr("냉각수 평형 (냉간)", "equilibrium at the coolant (cold)"), "equilibrium_at_coolant"),
                              (tr("예부하 정상상태 (고온 시작)", "steady state of a preload (hot start)"), "steady_state_at"),
                              (tr("노드 온도 입력 (Cauer만)", "node temperatures (Cauer only)"), "node_temperatures")],
                             "equilibrium_at_coolant")
        self.cy_pre = number(150, -5000, 5000, "N·m", 1, 5)
        self.cy_nodes = QPlainTextEdit()
        self.cy_nodes.setPlaceholderText(tr("노드 id: T1, T2, … (접합부부터, Cauer 노드)\n예) stator winding (hot spot): 120, 110, 95",
                                            "node id: T1, T2, … (junction first, Cauer nodes)\ne.g. stator winding (hot "
                                            "spot): 120, 110, 95"))
        self.cy_nodes.setFixedHeight(58)
        self.cy_fb = check(tr("손실–온도 피드백 (R_s(T), 모듈 T_j)", "loss-temperature feedback (R_s(T), module T_j)"), True)
        self.cy_rs = check(tr("R_s(T) 법칙 선언 (드라이브에 없을 때)", "declare an R_s(T) law (when the drive has none)"), True,
                           tr("드라이브에 R_s 온도 법칙이 없으면 이 분석에서만 쓰는 선언입니다. 공급된 R_s의 기준 온도를 적으세요.",
                              "Used only for this analysis when the drive has no R_s temperature law; state the temperature "
                              "the supplied R_s refers to."))
        self.cy_alpha = number(fb["rs_alpha_per_K"], 0.0, 0.02, "/K", 5, 0.0001)
        self.cy_ref = number(fb["rs_reference_C"], -40, 200, "°C", 1, 1)
        for lab, w in ((tr("펄스 토크", "pulse torque"), self.cy_T), (tr("펄스 시간", "pulse time"), self.cy_d),
                       (tr("휴지 토크", "rest torque"), self.cy_rT), (tr("휴지 시간", "rest time"), self.cy_rd),
                       (tr("반복", "repeat"), self.cy_n), (tr("시작 상태", "initial state"), self.cy_init),
                       (tr("예부하 토크", "preload torque"), self.cy_pre), (tr("노드 온도", "node temperatures"), self.cy_nodes)):
            f.addRow(lab, w)
        f.addRow(self.cy_fb)
        f.addRow(self.cy_rs)
        f.addRow(tr("R_s 온도계수", "R_s coefficient"), self.cy_alpha)
        f.addRow(tr("R_s 기준 온도", "R_s reference"), self.cy_ref)
        self.cy_btn = primary_button(tr("반복 부하 계산", "compute repeated load"))
        self.cy_btn.clicked.connect(self.run_cycle)
        f.addRow(self.cy_btn)
        f.addRow(hint(tr("속도·Vdc·냉각수는 위 운전 조건을 씁니다. Foster 노드의 내부 상태는 물리 층 온도가 아니므로 노드 온도 "
                         "시작은 Cauer로 입력한 노드만 가능합니다.",
                         "Speed, Vdc and coolant come from the operating condition above. A Foster node's inner states are "
                         "not layer temperatures: a start from node temperatures needs Cauer-entered nodes.")))
        return g

    def _node_temps(self) -> dict:
        out = {}
        for n, line in enumerate(self.cy_nodes.toPlainText().splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            if ":" not in line:
                raise ValueError(tr(f"{n}행: '노드 id: T1, T2, …' 형식", f"line {n}: use 'node id: T1, T2, …'"))
            name, vals = line.rsplit(":", 1)
            out[name.strip()] = [float(x) for x in vals.replace(";", ",").split(",") if x.strip()]
        return out

    def cycle_body(self) -> dict:
        spec = self.full_spec()
        kind = self.cy_init.currentData()
        init = {"kind": kind}
        if kind == "steady_state_at":
            init.update(torque_Nm=self.cy_pre.value(), speed_rpm=self.n.value())
        elif kind == "node_temperatures":
            init["node_temperatures_C"] = self._node_temps()
        fb = {"enabled": self.cy_fb.isChecked()}
        if self.cy_rs.isChecked():
            fb.update(rs_alpha_per_K=self.cy_alpha.value(), rs_reference_C=self.cy_ref.value(),
                      rs_valid_C=[-40.0, 250.0], rs_basis="declared on the thermal page for this analysis")
        return self.win.state.body(
            Vdc_V=self.vdc.value(), coolant_temp_C=self.c_in.value(), model=spec, cycles=self.cy_n.value(),
            phases=[{"name": "pulse", "torque_Nm": self.cy_T.value(), "speed_rpm": self.n.value(),
                     "duration_s": self.cy_d.value()},
                    {"name": "rest", "torque_Nm": self.cy_rT.value(), "speed_rpm": self.n.value(),
                     "duration_s": self.cy_rd.value()}],
            initial=init, feedback=fb)

    def run_cycle(self):
        try:
            body = self.cycle_body()
            api.thermal_model_from_dict(body["model"], self.c_in.value())
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.cy_btn.setEnabled(False)
        self.win.runner.run("thermal_cycle", tr("반복 부하", "repeated load"), lambda progress, b: api.thermal_cycle(b),
                            self._show_cycle, body, on_error=self._cycle_err)

    def _cycle_err(self, msg, tb):
        self.cy_btn.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _show_cycle(self, res):
        self.cy_btn.setEnabled(True)
        self.last_cycle = res
        self.insight.read("thermal_cycle", tr("반복 부하", "repeated load"), cycle_insight, res)
        if self.tabs.currentWidget() is not self.insight:
            self.tabs.setCurrentWidget(self.cyc_tab)
        self.p_cyc.draw(RF.fig_thermal_cycle, res, name="thermal_repeated_load",
                        csv=lambda r=res: {"t_s": r["trace"]["t_s"], **{f"T_{k}_C": v for k, v in r["trace"]["nodes"].items()}})
        c = res["claim"]
        rows = [(tr("판정", "claim"), f"{c['status']} · {', '.join(reason_label(r) for r in c.get('reasons') or [])} · "
                                     f"{'; '.join(c.get('qualifiers') or [])} {c.get('detail', '')}")]
        fl = res.get("first_limit")
        rows.append((tr("첫 한계 도달", "first limit"),
                     tr(f"{fl['t_s']:.4g} s · {fl['cycle']}번째 주기 · {fl['node']}", f"{fl['t_s']:.4g} s · cycle {fl['cycle']} · "
                                                                              f"{fl['node']}") if fl else
                     tr(f"{res['cycles_run']}주기 안에 없음", f"none in {res['cycles_run']} cycles")))
        if res.get("stopped"):
            st = res["stopped"]
            rows.append((tr("중단", "stopped"), f"{st['t_s']:.4g} s: {st['reason']}"))
        per = res.get("periodic") or {}
        if per.get("peak_C"):
            rows.append((tr("주기 정상상태 최고온도", "periodic cycle peak"),
                         " · ".join(f"{k}: {v:.1f} °C ({tr('여유', 'margin')} {per['margin_K'][k]:+.1f} K)"
                                    for k, v in per["peak_C"].items())))
            rows.append((tr("지배 노드", "governing node"), f"{per['governing_node']} · "
                         + (tr("고정점 수렴", "fixed point reached") if per.get("reached") else per.get("note", ""))))
        al = res.get("allowed") or {}
        if al:
            inf = lambda v: "∞" if v in ("Infinity", math.inf) else fmt(v)      # noqa: E731
            rows.append((tr("허용 펄스 시간 (주기)", "allowed pulse time (periodic)"), f"{inf(al.get('pulse_duration_s'))} s"))
            rows.append((tr("허용 펄스 토크 (주기)", "allowed pulse torque (periodic)"),
                         f"{inf(al.get('pulse_torque_Nm'))} N·m · {al.get('pulse_torque_note', '')}"))
            rows.append((tr("허용 첫 펄스 토크", "allowed first-pulse torque"),
                         f"{inf(al.get('first_pulse_torque_Nm'))} N·m · {al.get('first_pulse_torque_note', '')}"))
            rest = lambda k: (f"{inf(al[k + '_s'])} s" + (f" · {al[k + '_note']}" if al.get(k + "_note") else "")  # noqa: E731
                              if al.get(k + "_s") is not None else al.get(k + "_note", ""))
            rows.append((tr("첫 펄스 후 반복 전 필요 휴지", "rest before repeating after the first pulse"),
                         rest("rest_before_repeat")))
            rows.append((tr("주기 유지에 필요한 최소 휴지", "shortest rest for the periodic cycle"), rest("periodic_min_rest")))
            rows.append((tr("허용값 근거", "basis of allowed values"), engine_text(al.get("basis", ""))))
            lb = al.get("loss_bound") or {}
            if lb:
                box = " · ".join(f"{k} {v[0]:g}–{v[1]:g} °C" for k, v in (lb.get("temperatures_C") or {}).items())
                viol = "; ".join(f"{v['phase']}: {v['loss']} {v['W']:.4g} W > {v['corner_max_W']:.4g} W "
                                 f"@ {v['temperatures_C']}" for v in lb.get("violations") or [])
                rows.append((tr("손실 상한 점검", "loss bound check"),
                             f"{engine_text(lb.get('check', ''))} · {tr('모서리', 'corners')} {lb.get('corners')} · "
                             f"{tr('내부 표본', 'interior samples')} {lb.get('interior_samples')}" + (f" · {box}" if box else "")
                             + (f" · {viol}" if viol else "")))
        rc = res.get("resolution_check")
        if rc:
            rows.append((tr("해상도 점검 (스텝 2배·캐시 ½)", "resolution check (2x steps, ½ cache)"),
                         (tr("판정 유지", "stable") if rc["stable"] else tr("판정이 바뀜", "verdict changes")) + " · "
                         + engine_parts(rc.get("note", ""))))
        fbk = res["feedback"]
        rows.append((tr("피드백", "feedback"), f"R_s(T): {fbk['rs']} ({fbk['winding_node']}) · T_j: {fbk['module']} "
                                               f"({fbk['junction_node']}) · {tr('자석', 'magnet')}: {fbk.get('magnet')} "
                                               f"({fbk.get('magnet_node')}) · "
                                               + "; ".join(engine_text(n) for n in fbk["notes"])))
        rows.append((tr("시작 온도", "initial temperatures"),
                     " · ".join(f"{k}: {v:.1f} °C" for k, v in res["initial"]["temperatures_C"].items())))
        for a in res["assumptions"]:
            rows.append((tr("가정", "assumption"), a))
        self.t_cyc.set_rows(rows)

    # ------------------------------------------------------------------ coolant
    def _coolant_box(self):
        g = QGroupBox(tr("냉각수", "coolant"))
        f = QFormLayout(g)
        self.c_in = number(65, -40, 150, "°C", 1, 1, tr("냉각수 입구 온도 = 요구의 냉각수 조건", "coolant inlet temperature"))
        self.c_flow = number(10, 0.1, 1000, "L/min", 2, 0.5)
        self.c_eg = number(50, 0, 60, "vol %", 1, 5, tr("에틸렌글리콜 부피 농도 (물과 혼합). 50 = 50:50", "ethylene glycol by volume; 50 = 50:50"))
        self.c_manual = check(tr("물성 직접 입력", "enter properties"), False,
                              tr("공급사 데이터시트 값으로 c_p, ρ를 직접 입력", "use the coolant supplier's c_p and ρ"))
        self.c_cp = number(3500, 500, 6000, "J/(kg·K)", 1, 10)
        self.c_rho = number(1045, 500, 2000, "kg/m³", 1, 1)
        self.c_order = combo([(tr("인버터 → 모터", "inverter → motor"), "inv_first"), (tr("모터 → 인버터", "motor → inverter"), "motor_first")])
        self.c_ref = combo([(tr("평균 (부품 입·출구 평균)", "mean (station in/out)"), "mean"), (tr("입구", "inlet"), "inlet"),
                            (tr("출구 (보수적)", "outlet (conservative)"), "outlet")])
        self.c_rot = check(tr("회전 손실도 냉각수로", "rotational loss into coolant"), True,
                           tr("해제하면 모터 워터재킷은 동손만 받습니다 (회전 손실은 공기·베어링으로)",
                              "off: the motor jacket receives copper loss only"))
        self.c_props = QLabel("")
        self.c_props.setObjectName("Hint")
        self.c_props.setWordWrap(True)
        for lab, wd in ((tr("입구 온도", "inlet temperature"), self.c_in), (tr("유량", "flow"), self.c_flow),
                        (tr("부동액 (EG)", "glycol (EG)"), self.c_eg), ("", self.c_manual), ("c_p", self.c_cp),
                        ("ρ", self.c_rho), (tr("순환 순서", "loop order"), self.c_order),
                        (tr("기준 유체 온도", "reference fluid temp."), self.c_ref), ("", self.c_rot)):
            f.addRow(lab, wd)
        f.addRow(self.c_props)
        self.c_manual.toggled.connect(self._props_mode)
        for w in (self.c_in, self.c_flow, self.c_eg, self.c_cp, self.c_rho):
            w.valueChanged.connect(self._coolant_changed)
        for w in (self.c_order, self.c_ref):
            w.currentIndexChanged.connect(self._coolant_changed)
        self.c_rot.toggled.connect(self._coolant_changed)
        self._props_mode(False)
        return g

    def _props_mode(self, manual: bool):
        self.c_cp.setEnabled(manual)
        self.c_rho.setEnabled(manual)
        self._coolant_changed()

    def _coolant_changed(self, *_):
        p = eg_water_properties(self.c_eg.value(), self.c_in.value())
        if not self.c_manual.isChecked():
            for w, key in ((self.c_cp, "cp_J_per_kgK"), (self.c_rho, "rho_kg_per_m3")):
                w.blockSignals(True)
                w.setValue(p[key])
                w.blockSignals(False)
        cp, rho = self.c_cp.value(), self.c_rho.value()
        mdot = rho * self.c_flow.value() / 60000.0
        src = tr("직접 입력", "user values") if self.c_manual.isChecked() else \
            tr(f"EG {self.c_eg.value():g}% · {self.c_in.value():g} °C 일반값(근사)", f"typical EG {self.c_eg.value():g}% at {self.c_in.value():g} °C (approx.)")
        warn = tr(" · 표 범위 밖(끝값 사용)", " · outside the table (clamped)") if p["clamped_to_table"] and not self.c_manual.isChecked() else ""
        self.c_props.setText(f"c_p {cp:.0f} J/(kg·K) · ρ {rho:.0f} kg/m³ ({src}{warn})<br>"
                             f"ṁ = {mdot:.4f} kg/s · ṁ·c_p = {mdot * cp:.0f} W/K "
                             f"({tr('1 kW당', 'per kW')} {1000.0 / (mdot * cp):.2f} K)")
        self._schedule()

    def coolant_spec(self) -> dict:
        motor_losses = {"copper": 1.0, "rotational": 1.0} if self.c_rot.isChecked() else {"copper": 1.0}
        loop = [{"station": "inverter", "losses": {"inverter": 1.0}}, {"station": "motor", "losses": motor_losses}]
        if self.c_order.currentData() == "motor_first":
            loop.reverse()
        return {"glycol_vol_pct": self.c_eg.value(), "flow_L_per_min": self.c_flow.value(),
                "cp_J_per_kgK": self.c_cp.value() if self.c_manual.isChecked() else None,
                "rho_kg_per_m3": self.c_rho.value() if self.c_manual.isChecked() else None,
                "reference": self.c_ref.currentData(), "loop": loop}

    def _apply_coolant_spec(self, cs: dict | None):
        if not cs:
            return
        self.c_flow.setValue(float(cs.get("flow_L_per_min", 10.0)))
        if cs.get("glycol_vol_pct") is not None:
            self.c_eg.setValue(float(cs["glycol_vol_pct"]))
        manual = cs.get("cp_J_per_kgK") is not None and cs.get("rho_kg_per_m3") is not None
        self.c_manual.setChecked(manual)
        if manual:
            self.c_cp.setValue(float(cs["cp_J_per_kgK"]))
            self.c_rho.setValue(float(cs["rho_kg_per_m3"]))
        for i in range(self.c_ref.count()):
            if self.c_ref.itemData(i) == cs.get("reference", "mean"):
                self.c_ref.setCurrentIndex(i)
        loop = cs.get("loop") or []
        if loop and loop[0].get("station") == "motor":
            self.c_order.setCurrentIndex(1)
        self._coolant_changed()

    def full_spec(self) -> dict:
        spec = self.editor.spec()
        spec["coolant"] = self.coolant_spec()
        return spec

    # ----------------------------------------------------------------- diagrams
    def _schedule(self, *_):
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.start()

    def _network_nodes(self, spec):
        nodes = []
        ref_names = {"inverter": tr("T_f 인버터", "T_f inverter"), "motor": tr("T_f 모터", "T_f motor"),
                     None: tr("T_냉각수 입구", "T_coolant inlet")}
        for n in spec["nodes"]:
            ref = ref_names.get(n.get("station"), str(n.get("station")))
            if str(n.get("network", "foster")).lower() == "cauer":
                nodes.append({"name": n["id"], "kind": "cauer", "R": n["R_K_per_W"], "C": n["C_J_per_K"], "ref": ref})
            else:
                nodes.append({"name": n["id"], "kind": "foster", "R": n["R_K_per_W"], "tau": n["tau_s"], "ref": ref})
        return nodes

    def _refresh_diagrams(self):
        try:
            spec = self.full_spec()
            det = api.thermal_details(spec, self.c_in.value())
        except Exception as exc:  # noqa: BLE001 - live preview of an incomplete edit
            self.p_net.placeholder(tr(f"열 모델 입력을 확인하세요: {exc}", f"check the thermal model: {exc}"))
            return
        cool = dict(det["coolant"] or {})
        if self.last is not None:
            fl = ((self.last["res"].get("request") or {}).get("coolant") or {}).get("fluid")
            if fl:
                cool["fluid"] = fl
        if "fluid" not in cool:
            cool["fluid"] = {"_loop": {"T_inlet_C": self.c_in.value()}}
        nodes = self._network_nodes(spec)
        h = 3.3 * len(nodes) + 2.6
        self.p_net.draw(SC.fig_thermal_network, nodes, cool, title=tr("열 회로망 (노드 → 냉각수) · 냉각수 순환", "thermal networks (node → coolant) · coolant loop"),
                        name="thermal_network")
        self.p_net.canvas.setMinimumHeight(int(max(420, 72 * h)))
        zc = SF.zth_curves(det["model"])
        self.p_zth.draw(F.fig_zth, zc, title=tr("열 임피던스 Z_th(t)", "thermal impedance Z_th(t)"), name="zth",
                        csv=lambda zc=zc: {"t_s": zc["t_s"], **{f"zth_{i}_{n['node']}": n["zth_K_per_W"] for i, n in enumerate(zc["nodes"])}})

    # --------------------------------------------------------------------- run
    def run(self):
        try:
            spec = self.full_spec()
            api.thermal_model_from_dict(spec, self.c_in.value())
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("열 모델 입력 오류", "thermal model input error"), str(exc))
            return
        s = self.win.state
        body = s.body(speed_rpm=self.n.value(), Vdc_V=self.vdc.value(), coolant_temp_C=self.c_in.value(),
                      torque_Nm=self.T.value(), duration_s=self.dur.value(), model=spec, durations_s=DURATIONS,
                      direction=1 if self.T.value() >= 0 else -1, initial_state=self.init.currentData() or None)
        self.run_btn.setEnabled(False)
        self.win.runner.run("thermal", tr("열 가용성", "thermal"), _task, self._show, body, spec, on_error=self._err)

    def _err(self, msg, tb):
        self.run_btn.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _show(self, out):
        self.run_btn.setEnabled(True)
        self.last = out
        res = out["res"]
        av = res["availability"]
        cur = SF.availability_curve(av)
        req = res.get("request") or {}
        T, dur, cool = self.T.value(), self.dur.value(), self.c_in.value()
        claim = req.get("claim", {})
        ttl = req.get("time_to_first_limit_s")
        ttl = float(ttl) if isinstance(ttl, (int, float)) else math.inf
        cont = cur["continuous_Nm"]
        flow_txt = tr(f" (유량 {self.c_flow.value():g} L/min, EG {self.c_eg.value():g}%)",
                      f" ({self.c_flow.value():g} L/min, EG {self.c_eg.value():g}%)")
        if req.get("nodes"):
            if math.isfinite(ttl):
                head = tr(f"냉각수 입구 {cool:g} °C{flow_txt}에서 {T:g} N·m는 약 <b>{ttl:.3g} s</b> 유지 가능, 이후 <b>{fmt(cont, 4)} N·m</b> (연속)",
                          f"At {cool:g} °C coolant inlet{flow_txt}, {T:g} N·m holds for about <b>{ttl:.3g} s</b>, then <b>{fmt(cont, 4)} N·m</b> continuous")
            else:
                head = tr(f"냉각수 입구 {cool:g} °C{flow_txt}에서 {T:g} N·m는 열 한계에 도달하지 않음 (연속 가능 추정)",
                          f"At {cool:g} °C coolant inlet{flow_txt}, {T:g} N·m does not reach a thermal limit (continuous estimate)")
            fl = (req.get("coolant") or {}).get("fluid") or {}
            parts = [f"{k}: {v['T_in_C']:.1f}→{v['T_out_C']:.1f} °C (+{v['P_W'] / 1e3:.2f} kW)" for k, v in fl.items() if k != "_loop"]
            if parts:
                head += "<br><span style='font-size:9pt'>" + tr("냉각수: ", "coolant: ") + " · ".join(parts) + "</span>"
        else:
            head = tr(f"{T:g} N·m는 정적으로 가능하지 않아 지속시간을 평가하지 않았습니다 ({claim.get('status', '')}).",
                      f"{T:g} N·m is not statically feasible, so no duration was evaluated ({claim.get('status', '')}).")
        note = tr("검증된 열모델" if out["validated"] else "미검증 열모델 → 스크리닝 추정 (지속시간 claim UNKNOWN 유지)",
                  "validated thermal model" if out["validated"] else "unvalidated thermal model → screening estimate (duration claim stays UNKNOWN)")
        why = ", ".join(reason_label(r) for r in claim.get("reasons") or [])
        self.headline.setText(f"{head}<br><span style='font-size:9pt'>{tr('판정', 'claim')}: <b>{claim.get('status', '—')}</b>"
                              f"{' · ' + why if why else ''} · {note}</span>")
        self.plot.draw(F.fig_thermal, cur, out["curves"], T, dur,
                       title=tr(f"열 → 토크 가용성 · n = {self.n.value():g} rpm · 냉각수 입구 {cool:g} °C",
                                f"thermal → torque availability · n = {self.n.value():g} rpm · coolant inlet {cool:g} °C"),
                       name="thermal", csv=lambda: {"duration_s": cur["duration_s"], "torque_Nm": cur["torque_Nm"]})
        rows = []
        for r in av.get("rows", []):
            d = r["duration_s"]
            rows.append((tr("연속", "continuous") if d in ("Infinity", math.inf) else f"{float(d):.4g} s",
                         fmt(r["torque_Nm"]), r["limited_by"]))
        self.table.set_rows(rows)
        self._refresh_diagrams()
        self.insight.read("thermal", tr("열 가용성", "thermal availability"), thermal_insight, res,
                          out.get("ask") or {"torque_Nm": T, "speed_rpm": self.n.value(), "duration_s": dur})

    def apply_project(self, _project=None):
        """Thermal networks and cooling system of the active project."""
        self.editor.load(self.win.state.example("THERMAL"))
        self._apply_coolant_spec(self.editor.coolant_spec)

    def redraw(self):
        for p in (self.plot, self.p_net, self.p_zth, self.p_cyc, self.insight):
            p.redraw()
