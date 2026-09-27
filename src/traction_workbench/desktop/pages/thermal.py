"""Thermal screening: Foster network -> torque availability vs duration and node temperatures at a request."""

from __future__ import annotations

import json
import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QLabel, QPlainTextEdit, QScrollArea, QSplitter, QVBoxLayout,
                               QWidget)

from ... import api
from ...i18n import tr
from ...plots import figures as F
from ...viz import safety as SF
from ..widgets import KeyValueTable, PlotPanel, error_box, fmt, hint, number, primary_button

DURATIONS = [round(float(x), 4) for x in np.geomspace(0.1, 3000, 21)] + ["inf"]


def _task(progress, body, model_spec):
    progress(0.1, tr("가용 토크 (지속시간별 bisection)", "availability (bisection per duration)"))
    res = api.thermal(body)
    progress(0.9, tr("노드 온도", "node temperatures"))
    model = api._thermal_model(model_spec)
    req = res.get("request") or {}
    curves = None
    if req.get("nodes"):
        ttl = [float(n["time_to_limit_s"]) for n in req["nodes"] if isinstance(n["time_to_limit_s"], (int, float))]
        t_end = max([body["duration_s"] * 3] + [3 * t for t in ttl if math.isfinite(t)] + [10.0])
        curves = SF.thermal_curves(model, req["nodes"], body["coolant_temp_C"], t_end)
    return {"res": res, "curves": curves, "validated": model.validated}


class ThermalPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("조건", "conditions"))
        f = QFormLayout(g)
        self.n = number(3000, -30000, 30000, "rpm", 0, 100)
        self.vdc = number(600, 1, 2000, "V", 1, 10)
        self.coolant = number(65, -40, 150, "°C", 1, 1)
        self.T = number(450, -5000, 5000, "N·m", 2, 5)
        self.dur = number(10, 0.01, 1e6, "s", 2, 1)
        for lab, wd in ((tr("속도", "speed"), self.n), ("Vdc", self.vdc), (tr("냉각수", "coolant"), self.coolant),
                        (tr("요구 토크", "requested torque"), self.T), (tr("요구 지속시간", "requested duration"), self.dur)):
            f.addRow(lab, wd)
        v.addWidget(g)
        g = QGroupBox(tr("열 모델 (Foster, JSON)", "thermal model (Foster, JSON)"))
        gl = QVBoxLayout(g)
        self.model = QPlainTextEdit(json.dumps(api.EXAMPLE_THERMAL, indent=2, ensure_ascii=False))
        self.model.setMinimumHeight(220)
        gl.addWidget(self.model)
        gl.addWidget(hint(tr("노드별 R [K/W], τ [s], 한계 온도, 손실 분배(inverter/copper/rotational). \"validated\": true가 "
                             "아니면 결과는 스크리닝 추정이며 지속시간 claim은 UNKNOWN으로 남습니다.",
                             "Per node: R [K/W], tau [s], limit, loss shares. Unless \"validated\": true, results are screening "
                             "estimates and the duration claim stays UNKNOWN.")))
        v.addWidget(g, 1)
        self.run_btn = primary_button(tr("열 가용성 계산", "compute thermal availability"))
        self.run_btn.clicked.connect(self.run)
        v.addWidget(self.run_btn)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(340)
        split.addWidget(sc)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.headline = QLabel(tr("냉각수 온도와 요구를 입력하고 계산하세요.", "Enter the coolant temperature and request, then compute."))
        self.headline.setWordWrap(True)
        self.headline.setObjectName("Card")
        self.headline.setStyleSheet("padding: 10px; font-size: 11pt;")
        rv.addWidget(self.headline)
        self.plot = PlotPanel()
        rv.addWidget(self.plot, 3)
        self.table = KeyValueTable(headers=[tr("지속시간", "duration"), tr("가용 토크 [N·m]", "available torque [N·m]"),
                                            tr("제한", "limited by")])
        rv.addWidget(self.table, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 1060])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)

    def run(self):
        try:
            spec = json.loads(self.model.toPlainText())
        except json.JSONDecodeError as exc:
            error_box(self, tr("열 모델 JSON 오류", "thermal model JSON error"), str(exc))
            return
        s = self.win.state
        body = s.body(speed_rpm=self.n.value(), Vdc_V=self.vdc.value(), coolant_temp_C=self.coolant.value(),
                      torque_Nm=self.T.value(), duration_s=self.dur.value(), model=spec, durations_s=DURATIONS,
                      direction=1 if self.T.value() >= 0 else -1)
        self.run_btn.setEnabled(False)
        self.win.runner.run("thermal", tr("열 가용성", "thermal"), _task, self._show, body, spec, on_error=self._err)

    def _err(self, msg, tb):
        self.run_btn.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _show(self, out):
        self.run_btn.setEnabled(True)
        res = out["res"]
        av = res["availability"]
        cur = SF.availability_curve(av)
        req = res.get("request") or {}
        T, dur, cool = self.T.value(), self.dur.value(), self.coolant.value()
        claim = req.get("claim", {})
        ttl = req.get("time_to_first_limit_s")
        ttl = float(ttl) if isinstance(ttl, (int, float)) else math.inf
        cont = cur["continuous_Nm"]
        if req.get("nodes"):
            if math.isfinite(ttl):
                head = tr(f"냉각수 {cool:g} °C에서 {T:g} N·m는 약 <b>{ttl:.3g} s</b> 유지 가능, 이후 <b>{fmt(cont, 4)} N·m</b> (연속)",
                          f"At {cool:g} °C coolant, {T:g} N·m holds for about <b>{ttl:.3g} s</b>, then <b>{fmt(cont, 4)} N·m</b> continuous")
            else:
                head = tr(f"냉각수 {cool:g} °C에서 {T:g} N·m는 열 한계에 도달하지 않음 (연속 가능 추정)",
                          f"At {cool:g} °C coolant, {T:g} N·m does not reach a thermal limit (continuous estimate)")
        else:
            head = tr(f"{T:g} N·m는 정적으로 가능하지 않아 지속시간을 평가하지 않았습니다 ({claim.get('status', '')}).",
                      f"{T:g} N·m is not statically feasible, so no duration was evaluated ({claim.get('status', '')}).")
        status = claim.get("status", "—")
        note = tr("검증된 열모델" if out["validated"] else "미검증 열모델 → 스크리닝 추정 (지속시간 claim UNKNOWN 유지)",
                  "validated thermal model" if out["validated"] else "unvalidated thermal model → screening estimate (duration claim stays UNKNOWN)")
        self.headline.setText(f"{head}<br><span style='font-size:9pt'>claim: <b>{status}</b> · "
                              f"{', '.join(claim.get('reasons') or [])} · {note}</span>")
        self.plot.draw(F.fig_thermal, cur, out["curves"], T, dur,
                       title=tr(f"열 → 토크 가용성 · n = {self.n.value():g} rpm · 냉각수 {cool:g} °C",
                                f"thermal → torque availability · n = {self.n.value():g} rpm · coolant {cool:g} °C"),
                       name="thermal", csv=lambda: {"duration_s": cur["duration_s"], "torque_Nm": cur["torque_Nm"]})
        rows = []
        for r in av.get("rows", []):
            d = r["duration_s"]
            rows.append((tr("연속", "continuous") if d in ("Infinity", math.inf) else f"{float(d):.4g} s",
                         fmt(r["torque_Nm"]), r["limited_by"]))
        self.table.set_rows(rows)

    def redraw(self):
        self.plot.redraw()
