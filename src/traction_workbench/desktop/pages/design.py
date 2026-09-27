"""Design and bottleneck analysis: capability vs one parameter, bounded sizing, dominance, relaxation."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QGroupBox, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget

from ...analysis.dominance import capability_dominance, requirement_relaxation
from ...analysis.sizing import size_parameter
from ...analysis.variation import PARAMETERS, get_value
from ...i18n import tr
from ...plots import figures as F
from ...scenario import Scenario
from ...viz import design as DS
from ..widgets import KeyValueTable, PlotPanel, combo, error_box, fmt, hint, integer, number, primary_button


def _sweep_task(progress, drive, limits, n, vdc, T, param, lo, hi, samples):
    sc = Scenario("design", n, vdc, limits)
    cv = DS.capability_vs_parameter(drive, sc, param, np.linspace(lo, hi, samples), T_request=T,
                                    direction=1 if T >= 0 else -1, progress=lambda f, m: progress(0.7 * f, m))
    progress(0.75, tr("역설계 bisection", "sizing bisection"))
    sz = size_parameter(drive, sc, T, param, (lo, hi), samples).to_dict()
    return {"curve": cv, "sizing": sz}


def _dominance_task(progress, drive, limits, n, vdc, T):
    sc = Scenario("design", n, vdc, limits)
    progress(0.1, tr("제약 1% 완화 재계산", "1% relaxation"))
    dom = capability_dominance(drive, sc, 1 if T >= 0 else -1).to_dict()
    progress(0.6, tr("요구 완화 탐색", "requirement relaxation"))
    rel = requirement_relaxation(drive, sc, T).to_dict()
    return {"dominance": dom, "relaxation": rel}


class DesignPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("요구 조건", "request"))
        f = QFormLayout(g)
        self.n = number(12000, -30000, 30000, "rpm", 1, 100)
        self.vdc = number(450, 1, 2000, "V", 1, 10)
        self.T = number(150, -5000, 5000, "N·m", 2, 5)
        f.addRow(tr("속도", "speed"), self.n)
        f.addRow("Vdc", self.vdc)
        f.addRow(tr("요구 토크", "requested torque"), self.T)
        v.addWidget(g)
        g = QGroupBox(tr("1-파라미터 역설계", "one-parameter sizing"))
        f = QFormLayout(g)
        self.param = combo([(f"{k}  ({v[0]}, {v[1]})", k) for k, v in PARAMETERS.items()], "Vdc_V")
        self.param.currentIndexChanged.connect(self._param_changed)
        self.lo = number(400, -1e9, 1e9, "", 6, 10)
        self.hi = number(800, -1e9, 1e9, "", 6, 10)
        self.samples = integer(41, 5, 201)
        f.addRow(tr("파라미터", "parameter"), self.param)
        f.addRow(tr("하한", "low"), self.lo)
        f.addRow(tr("상한", "high"), self.hi)
        f.addRow(tr("표본 수", "samples"), self.samples)
        self.kind_hint = hint("")
        f.addRow(self.kind_hint)
        v.addWidget(g)
        self.run_sweep = primary_button(tr("capability vs 파라미터 + 역설계", "capability vs parameter + sizing"))
        self.run_sweep.clicked.connect(self.run1)
        v.addWidget(self.run_sweep)
        self.run_dom = primary_button(tr("병목 분석 (1% 완화 · 요구 완화)", "bottleneck analysis"))
        self.run_dom.clicked.connect(self.run2)
        v.addWidget(self.run_dom)
        v.addWidget(hint(tr("변경 종류: boundary(시나리오 경계), hardware(부품 변경), design(설계·제어 선택), diagnostic(원인 진단용, 실현 "
                            "불가), data(자료 불확실성). diagnostic 결과는 설계안이 아닙니다. 표본 범위 밖은 외삽하지 않습니다.",
                            "Change kinds: boundary, hardware, design, diagnostic (cause only, not realisable), data. "
                            "No extrapolation outside the searched range.")))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(320)
        split.addWidget(sc)
        self.tabs = QTabWidget()
        w1 = QWidget()
        l1 = QVBoxLayout(w1)
        l1.setContentsMargins(0, 0, 0, 0)
        self.p_curve = PlotPanel()
        self.t_sizing = KeyValueTable()
        l1.addWidget(self.p_curve, 3)
        l1.addWidget(self.t_sizing, 1)
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(0, 0, 0, 0)
        self.p_dom = PlotPanel()
        self.t_dom = KeyValueTable(headers=[tr("제약", "constraint"), tr("분류", "class"), tr("증가 [N·m]", "gain [N·m]"),
                                            tr("민감도 [N·m/단위]", "sensitivity"), tr("기준점 활성", "active at base")])
        l2.addWidget(self.p_dom, 3)
        l2.addWidget(self.t_dom, 1)
        self.tabs.addTab(w1, tr("파라미터 민감도·역설계", "parameter sweep · sizing"))
        self.tabs.addTab(w2, tr("병목·완화", "bottleneck · relaxation"))
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 1100])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)
        self._param_changed()

    def _param_changed(self, *_):
        key = self.param.currentData()
        kind, unit, text = PARAMETERS[key]
        self.kind_hint.setText(f"{kind} · {unit} · {text}")
        s = self.win.state
        try:
            base = get_value(s.drive, Scenario("p", self.n.value(), self.vdc.value(), s.limits), key)
        except Exception:  # noqa: BLE001
            base = None
        if key == "Vdc_V":
            self.lo.setValue(400)
            self.hi.setValue(800)
        elif base not in (None, 0):
            lo, hi = sorted((0.5 * base, 1.5 * base))
            if key == "voltage_reserve_fraction":
                lo, hi = 0.0, 0.2
            self.lo.setValue(lo)
            self.hi.setValue(hi)

    def run1(self):
        s = self.win.state
        self.run_sweep.setEnabled(False)
        self.win.runner.run("design-sweep", tr("파라미터 역설계", "sizing"), _sweep_task, self._show1, s.drive, s.limits,
                            self.n.value(), self.vdc.value(), self.T.value(), self.param.currentData(), self.lo.value(),
                            self.hi.value(), self.samples.value(), on_error=self._err)

    def run2(self):
        s = self.win.state
        self.run_dom.setEnabled(False)
        self.win.runner.run("design-dom", tr("병목 분석", "bottleneck"), _dominance_task, self._show2, s.drive, s.limits,
                            self.n.value(), self.vdc.value(), self.T.value(), on_error=self._err)

    def _err(self, msg, tb):
        self.run_sweep.setEnabled(True)
        self.run_dom.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _show1(self, res):
        self.run_sweep.setEnabled(True)
        cv, sz = res["curve"], res["sizing"]
        name = cv["parameter"]["parameter"]
        self.p_curve.draw(F.fig_capability_vs_parameter, cv, sz, name=f"capability_vs_{name}",
                          csv=lambda cv=cv: {name: cv["values"], "policy_capability_Nm": cv["capability_Nm"],
                                             "status_at_request": cv["status"], "i_peak_A": cv["i_peak_A"],
                                             "Pdc_W": cv["Pdc_W"], "v_margin_V": cv["v_margin_V"]})
        sol = sz.get("solution_at_minimal_value") or {}
        rows = [(tr("파라미터", "parameter"), f"{name} ({sz['parameter']['change_kind']}, {sz['parameter']['unit']})"),
                (tr("기준값", "baseline"), fmt(sz["baseline_value"])),
                (tr("탐색 범위 / 표본", "range / samples"), f"{fmt(sz['search_range'])} / {sz['samples']}"),
                (tr("가능 구간", "feasible ranges"), "; ".join(f"[{fmt(a)}, {fmt(b)}]" for a, b in sz["feasible_ranges"]) or "—"),
                (tr("최소 가능값", "minimal feasible"), fmt(sz["minimal_feasible_value"])),
                (tr("최소값에서의 해", "solution at minimum"), ", ".join(f"{k}={fmt(v)}" for k, v in sol.items()) or "—")]
        rows += [(tr("주석", "note"), n) for n in sz["notes"]]
        self.t_sizing.set_rows(rows)

    def _show2(self, res):
        self.run_dom.setEnabled(True)
        dom, rel = res["dominance"], res["relaxation"]
        self.p_dom.draw(F.fig_dominance, dom, rel, name="dominance")
        rows = [(r["constraint"], r["classification"], fmt(r["gain_Nm"]), fmt(r["sensitivity_Nm_per_unit"]),
                 "yes" if r["active_at_base"] else "") for r in dom["single"]]
        self.t_dom.set_rows(rows)

    def redraw(self):
        self.p_curve.redraw()
        self.p_dom.redraw()
