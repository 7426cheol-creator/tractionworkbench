"""Requirement decision page: requirement in, verdict + claims + evidence + operating-point graphs out."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea,
                               QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ... import api
from ... import service as S
from ...analysis.variation import PARAMETERS
from ...i18n import language, tr
from ...insight.decision import decision_insight
from ...insight.texts import engine_text
from ...plots import figures as F
from ...plots.labels import change_kind_label, claim_label, constraint_label, param_label, reason_label, yes_no
from ...requirement_set import classify
from ...viz import design as DS
from ...viz import maps as M
from ...viz import operating as O
from ...viz import sweeps as SW
from ..opviews import OperatingViews
from ..widgets import (ConceptNote, ClaimTree, InsightPanel, KeyValueTable, PlotPanel, VerdictBanner, check, combo, confirm,
                       error_box, fmt, hint, number, primary_button)


LAYER_NAMES = {"mathematical": lambda: tr("수학 (수치 증거)", "mathematical (numerical evidence)"),
               "model": lambda: tr("모델 판정", "model verdict"),
               "requirement": lambda: tr("요구 완결성", "requirement completeness"),
               "qualification": lambda: tr("데이터 적격성", "data qualification")}


def _evaluate_task(progress, case: dict, analyses_curves: list):
    progress(0.05, tr("판정 계산", "evaluating"))
    rec_d, rec, case_obj = S.evaluate_case_full(case)
    out = {"record": rec_d, "rec": rec, "case": case_obj, "curves": []}
    progress(0.6, tr("id–iq 지도", "id–iq map"))
    out["views"] = {0: _condition_views(rec, case_obj, 0)}
    cond = rec.conditions[0]
    if cond.primary.point is not None:          # PWM consequences at the same operating point (review priority 3)
        try:
            out["pwm_risk"] = api.pwm_risk_at(case_obj.drive, cond.scenario, cond.primary.point)
        except Exception as exc:  # noqa: BLE001 - a screening add-on never hides the decision
            out["pwm_risk"] = {"status": "ERROR", "reason": str(exc)}
    for i, (param, lo, hi) in enumerate(analyses_curves):
        progress(0.7 + 0.25 * i / max(1, len(analyses_curves)), f"{param}")
        sc = rec.conditions[0].scenario
        out["curves"].append(DS.capability_vs_parameter(case_obj.drive, sc, param, np.linspace(lo, hi, 31),
                                                        T_request=rec.requirement.target_Nm,
                                                        direction=1 if rec.requirement.target_Nm >= 0 else -1))
    progress(1.0, "")
    return out


def _pwm_risk_rows(pr: dict) -> list:
    """PWM consequences at the decision's operating point, fundamental limit and instantaneous peak kept apart."""
    if pr.get("status") != "EVALUATED":
        return [(tr("상태", "status"), f"{pr.get('status')}: {pr.get('reason', '')}")]
    fu, pk, rm = pr["fundamental"], pr["instantaneous_peak"], pr["rms"]
    rows = [(tr("캐리어", "carrier"), f"{pr['fsw_requested_Hz'] / 1e3:g} kHz " + tr("요청", "requested") + " · "
             + tr("파형 모델", "waveform") + f" {pr['fsw_waveform_used_Hz'] / 1e3:.4g} kHz ({pr['fsw_error_percent']:+.2g} %) · "
             f"{pr['modulation']} · m {pr['modulation_index']:.3f} · L_hf {pr['L_hf_H'] * 1e6:g} µH"),
            (tr("기본파 전류 (정책의 한계)", "fundamental current (the policy's limit)"),
             f"{fu['i_peak_A']:.4g} A / {fu['limit_A']:.4g} A · {tr('여유', 'margin')} {fu['margin_A']:.4g} A — {fu['meaning']}"),
            (tr("순간 피크 (보수 상한)", "instantaneous peak (conservative bound)"),
             f"{pk['bound_A']:.4g} A · " + (f"{tr('선언 피크 한계', 'declared peak limit')} {pk['limit_A']:.4g} A · "
                                             if pk.get("limit_A") is not None else "")
             + f"{pk['status']} {pk.get('reason', '')} — {pk['meaning']}"),
            ("RMS", f"{tr('기본파', 'fundamental')} {rm['fundamental_A']:.4g} A · {tr('리플', 'ripple')} {rm['ripple_A']:.4g} A · "
                    f"{tr('합', 'total')} {rm['total_A']:.4g} A (+{fmt(rm['added_percent'], 3)} %)"),
            (tr("주요 선 (상전류)", "dominant lines (phase current)"),
             " · ".join(f"{ln['f_Hz'] / 1e3:.4g} kHz" + ("" if ln.get("order") is None else f" ({ln['order']:.3g}·f_e)")
                        + f" {ln['I_pk_A']:.3g} A" for ln in pr["lines"])
             + (tr(" — 정지·저속: f_e 대신 fsw/400으로 계산한 준정적 리플",
                   " — standstill / low speed: quasi-static ripple at a stand-in f_e = fsw/400")
                if pr.get("ripple_quasi_static") else ""))]
    dl = pr.get("dc_link")
    if not dl and pr.get("dc_link_reason"):
        rows.append((tr("DC-link 부담", "DC-link burden"), pr["dc_link_reason"]))
    if dl:
        rows.append((tr("DC-link 부담", "DC-link burden"),
                     f"I_cap {dl['I_cap_rms_A']:.4g} A rms · P_ESR {fmt(dl['P_cap_W'])} W · ΔV {fmt(dl['V_ripple_pp_V'])} V pp · "
                     f"I_dc {dl['I_dc_avg_A']:.4g} A" + (f" · {dl['assumption']}" if dl.get("assumption") else "")))
    ml = pr.get("motor_pwm_loss") or {}
    cu = ml.get("copper") or {}
    if cu:
        iv = ml.get("interval_W") or [None, None]
        rows.append((tr("모터 PWM 손실", "motor PWM loss"),
                     (f"{tr('동손', 'copper')} {fmt(cu['W'])} W" if cu.get("W") is not None else
                      f"{tr('동손', 'copper')} ≥ {fmt(cu['lower_bound_W'])} W") + " · Fe+PM "
                     + (f"≤ {fmt(ml['magnetic_hf_bound_W'])} W" if ml.get("magnetic_hf_bound_W") is not None else "UNKNOWN")
                     + f" · [{fmt(iv[0])}, {fmt(iv[1]) if iv[1] is not None else '∞'}] W"))
    rows.append((tr("평가 안 함", "not evaluated"), "; ".join(pr.get("not_evaluated") or [])))
    return rows


def _condition_views(rec, case_obj, idx: int) -> dict:
    cond = rec.conditions[idx]
    sc = cond.scenario
    T = cond.primary_torque_Nm            # the accepted witness torque (review R2 D-R2-03), not a failed band centre
    plane = M.idiq_plane(case_obj.drive, sc.source_limits, sc.speed_rpm, sc.Vdc_V, T, scenario=sc)
    pv = None
    pt = cond.primary.point
    if pt is not None:
        pv = O.point_view(case_obj.drive, sc, pt.id_A, pt.iq_A, T)
    return {"plane": plane, "pv": pv}


def _envelope_task(progress, drive, limits, Vdc, magnet_temp_C=None):
    return SW.envelope(drive, limits, Vdc, n=33, progress=progress, magnet_temp_C=magnet_temp_C)


class _AdaptiveSplitter(QSplitter):
    """Side by side when at least ``threshold`` px wide, one above the other when narrower (each part keeps a
    readable width instead of two slivers)."""

    def __init__(self, threshold: int):
        super().__init__(Qt.Horizontal)
        self._threshold = threshold

    def resizeEvent(self, ev):
        want = Qt.Horizontal if ev.size().width() >= self._threshold else Qt.Vertical
        if self.orientation() != want:
            self.setOrientation(want)
            total = ev.size().width() if want == Qt.Horizontal else ev.size().height()
            self.setSizes([total // 2, total - total // 2] if want == Qt.Horizontal else [total * 2 // 5, total * 3 // 5])
        super().resizeEvent(ev)


class DecisionPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.result = None
        self._env_cache = {}
        self._banner_last = ("NONE", "", "")        # verdict, the lines always shown, the folded details
        self._inputs_note = ""
        split = QSplitter(Qt.Horizontal)
        form = self._build_form()
        split.addWidget(form)
        split.addWidget(self._build_results())
        split.setStretchFactor(1, 1)
        split.setSizes([330, 1100])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)
        self._load_preset(0)
        self.win.track_inputs("decision", form)

    # ------------------------------------------------------------------ window hooks
    def task_finished(self, key: str, outcome: str) -> None:
        """A cancelled evaluation leaves the last result (or the empty page) as it was, and says so."""
        if key != "decision" or outcome != "cancelled":
            return
        if self.result is not None:
            v, html, details = self._banner_last
            self._set_banner(v, html, details, tr("새 계산은 취소됨 — 이전 결과를 표시 중입니다.",
                                                  "the new evaluation was cancelled - showing the previous result."))
        else:
            self.banner.set("NONE", tr("계산을 취소했습니다. 입력을 확인하고 다시 실행하세요 (Ctrl+Enter).",
                                       "Evaluation cancelled. Check the inputs and run again (Ctrl+Enter)."))

    def inputs_changed(self, changed: dict) -> None:
        """The verdict on screen belongs to the inputs it was computed from: say so when the form differs."""
        diffs = changed.get("decision") or []
        if diffs:
            shown = "; ".join(f"{f}: {a} → {b}" for f, a, b in diffs[:4])
            if len(diffs) > 4:
                shown += tr(f" 외 {len(diffs) - 4}개", f" and {len(diffs) - 4} more")
            self._inputs_note = tr(f"⚠ 입력이 바뀌었습니다 — 이 판정은 바뀌기 전 입력으로 계산됐습니다 ({shown}). "
                                   f"다시 실행: Ctrl+Enter",
                                   f"⚠ inputs changed - this verdict was computed from the earlier inputs ({shown}). "
                                   f"Run again: Ctrl+Enter")
        else:
            self._inputs_note = ""
        if self.result is not None:
            self._set_banner(*self._banner_last)

    def _source_row(self, *_):
        """The source resistance row (label and fields) is shown only when Vdc is a battery OCV."""
        self._req_form.setRowVisible(self.src_row, self.vdc_kind.currentData() == "battery_ocv")

    def _set_banner(self, verdict: str, html: str, details: str = "", note: str = "") -> None:
        """Notes (inputs changed since, a cancelled re-run) always show; the record details fold away."""
        extra = "".join(f"<br><span style='color:#b35900'><b>{x}</b></span>" for x in (self._inputs_note, note) if x)
        self.banner.set(verdict, html + extra, details)

    # ------------------------------------------------------------------ form
    def _build_form(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("예시 질문", "examples"))
        gl = QVBoxLayout(g)
        self.presets = QComboBox()
        for p in api.PRESETS:
            self.presets.addItem(p["title"][language()], p["key"])
        self.presets.currentIndexChanged.connect(self._load_preset)
        self.preset_hint = hint("")
        gl.addWidget(self.presets)
        gl.addWidget(self.preset_hint)
        v.addWidget(g)

        g = QGroupBox(tr("요구 (원문 보존)", "requirement (text kept verbatim)"))
        f = QFormLayout(g)
        self.req_id = QLineEdit()
        self.req_text = QPlainTextEdit()
        self.req_text.setFixedHeight(64)
        self.torque = number(150, -5000, 5000, "N·m", 3, 1.0, tr("축 토크 (+ 구동, − 회생 제동). 요구를 clip하지 않습니다.",
                                                                 "shaft torque (+ motoring, − braking); never clipped"))
        self.speed = number(12000, -30000, 30000, "rpm", 1, 100.0, tr("기계 회전속도", "mechanical speed"))
        self.vdc = number(600, 1, 2000, "V", 2, 10.0, tr("인버터 DC 단자 전압 (또는 아래에서 배터리 OCV로 지정)",
                                                          "inverter DC terminal voltage (or the battery OCV, below)"))
        self.vdc_kind = combo([(tr("인버터 DC 단자 전압 (보장값 — 강하를 다시 빼지 않음)",
                                   "inverter DC terminal voltage (guaranteed - no drop subtracted again)"),
                                "inverter_dc_terminal"),
                               (tr("배터리 OCV → Thevenin 소스로 단자 전압 계산", "battery OCV → terminal voltage via a "
                                                                          "Thevenin source"), "battery_ocv")],
                              "inverter_dc_terminal")
        self.src_R = number(20.0, 0.0, 10000.0, "mΩ", 2, 1.0, tr("소스 등가 저항 R_eq (SOC·온도에서)",
                                                                "source equivalent resistance R_eq (at SOC, temperature)"))
        self.src_basis = QLineEdit(tr("팩 R_eq (SOC·온도·펄스 시간 명시)", "pack R_eq (state SOC, temperature, pulse time)"))
        self.src_row = QWidget()
        sr = QHBoxLayout(self.src_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(self.src_R)
        sr.addWidget(self.src_basis, 1)
        self.vdc_kind.currentIndexChanged.connect(self._source_row)
        self.range_on = check(tr("Vdc 범위 요구", "Vdc range"), False,
                              tr("범위 전체를 요구하면 표본점 통과만으로 PASS가 아닙니다 (SAMPLED_COVERAGE). 단조성 조건(정적 순구동, Vdc "
                                 "무관 손실, 고정 소스 한계)이 성립하면 저전압 끝점으로 범위 전체를 입증합니다.",
                                 "a range requirement never becomes PASS from samples alone (SAMPLED_COVERAGE); when "
                                 "the monotonicity conditions hold (static motoring, Vdc-independent loss, fixed source "
                                 "limits) the low endpoint proves the whole range"))
        self.vdc_hi = number(650, 1, 2000, "V", 2, 10.0)
        self.vdc_hi.setEnabled(False)
        self.range_on.toggled.connect(self.vdc_hi.setEnabled)
        rr = QHBoxLayout()
        rr.addWidget(self.range_on)
        rr.addWidget(self.vdc_hi)
        self.dur_none = QRadioButton(tr("정적 (지속시간 없음)", "static (no duration)"))
        self.dur_sec = QRadioButton(tr("지속시간", "duration"))
        self.dur_cont = QRadioButton(tr("연속", "continuous"))
        self.dur_group = QButtonGroup(self)
        for b in (self.dur_none, self.dur_sec, self.dur_cont):
            self.dur_group.addButton(b)
        self.dur_none.setChecked(True)
        self.duration = number(10, 0.001, 1e6, "s", 3, 1.0)
        dr = QHBoxLayout()
        dr.addWidget(self.dur_sec)
        dr.addWidget(self.duration)
        self.coolant_on = check(tr("냉각수 온도", "coolant temp."), False)
        self.coolant = number(65, -40, 150, "°C", 1)
        cr = QHBoxLayout()
        cr.addWidget(self.coolant_on)
        cr.addWidget(self.coolant)
        self.magnet_on = check(tr("자석 온도", "magnet temp."), False,
                               tr("flux map에 온도 plane이 여러 개일 때 미지정이면 map의 모든 온도에서 for-all로 평가합니다 "
                                  "(선언된 보간 사이는 표본 검사). 한 온도에서의 요구면 여기에 적으세요. 상수 dq 모델은 자속 "
                                  "온도 법칙과 기준 온도가 있어야 이 값을 씁니다.",
                                  "with several flux-map temperature planes an unstated magnet temperature is examined "
                                  "for ALL the map's temperatures (declared interpolation sampled in between); state it "
                                  "when the requirement applies at one temperature. A constant-dq model needs its flux "
                                  "temperature law and reference temperature to use it."))
        self.magnet = number(120, -40, 250, "°C", 1)
        mr = QHBoxLayout()
        mr.addWidget(self.magnet_on)
        mr.addWidget(self.magnet)
        self.winding_on = check(tr("권선 온도", "winding temp."), False,
                                tr("Rs 온도 법칙과 기준 온도가 선언된 모델에서만 쓰입니다 (없으면 MISSING_INPUT).",
                                   "used only with a declared Rs temperature law and reference temperature (else "
                                   "MISSING_INPUT)"))
        self.winding = number(120, -40, 250, "°C", 1)
        wr = QHBoxLayout()
        wr.addWidget(self.winding_on)
        wr.addWidget(self.winding)
        for on, w in ((self.coolant_on, self.coolant), (self.magnet_on, self.magnet), (self.winding_on, self.winding)):
            w.setEnabled(on.isChecked())
            on.toggled.connect(w.setEnabled)
        f.addRow("ID", self.req_id)
        f.addRow(tr("원문", "text"), self.req_text)
        f.addRow(tr("토크", "torque"), self.torque)
        f.addRow(tr("속도", "speed"), self.speed)
        f.addRow("Vdc", self.vdc)
        f.addRow(tr("Vdc 의미", "Vdc meaning"), self.vdc_kind)
        f.addRow(tr("소스 저항 R_eq", "source R_eq"), self.src_row)
        self._req_form = f
        self._source_row()                   # the label and the fields show only for a battery OCV
        f.addRow("", rr)
        f.addRow(tr("시간", "time"), self.dur_none)
        f.addRow("", dr)
        f.addRow("", self.dur_cont)
        f.addRow("", cr)
        f.addRow("", mr)
        f.addRow("", wr)
        v.addWidget(g)

        g = QGroupBox(tr("추가 분석", "additional analyses"))
        gl = QVBoxLayout(g)
        self.an_dom = check(tr("병목 기여도 (제약 1% 완화 재계산)", "dominance (1% relaxation)"), False)
        self.an_relax = check(tr("요구 달성 최소 완화 (FAIL 시)", "minimal relaxation (if FAIL)"), True)
        self.an_size_v = check(tr("Vdc 역설계 (400–800 V)", "size Vdc (400–800 V)"), False)
        self.an_size_i = check(tr("인버터 전류 역설계 (+0–100%)", "size inverter current (+0–100%)"), False)
        for w in (self.an_dom, self.an_relax, self.an_size_v, self.an_size_i):
            gl.addWidget(w)
        v.addWidget(g)
        self.run_btn = primary_button(tr("판정 실행  (Ctrl+Enter)", "Evaluate  (Ctrl+Enter)"))
        self.run_btn.clicked.connect(self.run)          # Ctrl+Enter is the window's shortcut (every page)
        v.addWidget(self.run_btn)
        v.addWidget(ConceptNote(tr(
            "<b>판정 방식</b>: 요구 하나를 여러 판정 항목(claim)으로 나눕니다 — 전기적 존재(전압·전류·도메인), 최소전류 정책의 "
            "정적 달성(DC 한계 포함), DC 소스 한계, (있으면) 지속시간. 요구 판정은 이들의 AND입니다: 증명된 위반이 하나라도 있으면 "
            "<b>FAIL</b>, 위반은 없지만 확인하지 못한 항목이 있으면 <b>UNKNOWN</b>, 모두 가능하면 명시된 범위에서 <b>PASS</b>.<br>"
            "INFEASIBLE은 증명(제약 다항식 근 전수 열거, 해석적 필요조건, 인증 상한)이 있을 때만 냅니다. '운전점 그래프' 탭에서 "
            "시스템 개요도·파형·벡터도로 그 운전점을 확인할 수 있습니다.",
            "<b>How the verdict is formed</b>: the requirement is split into claims — electrical existence (voltage, current, "
            "domain), static achievement by the minimum-current policy (incl. DC limits), DC source limits and (if stated) "
            "duration. The verdict is their AND: any proven violation → <b>FAIL</b>; no violation but something unconfirmed → "
            "<b>UNKNOWN</b>; all feasible → <b>PASS</b> within the stated scope. INFEASIBLE needs a proof (exact root enumeration, "
            "analytic necessary condition or certified bound).")))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(box)
        sc.setMinimumWidth(320)
        return sc

    def _load_preset(self, idx):
        p = api.PRESETS[idx]
        r = p["req"]
        self.preset_hint.setText(p["hint"][language()])
        self.req_id.setText(r["id"])
        self.req_text.setPlainText(r["text"])
        self.torque.setValue(r["torque_Nm"])
        self.speed.setValue(r["speed_rpm"])
        if isinstance(r["Vdc_V"], list):
            self.vdc.setValue(r["Vdc_V"][0])
            self.vdc_hi.setValue(r["Vdc_V"][1])
            self.range_on.setChecked(True)
        else:
            self.vdc.setValue(r["Vdc_V"])
            self.range_on.setChecked(False)
        if r.get("duration_s"):
            self.dur_sec.setChecked(True)
            self.duration.setValue(r["duration_s"])
        else:
            self.dur_none.setChecked(True)
        an = p.get("analyses", {})
        self.an_dom.setChecked(bool(an.get("dominance")))
        sz = {s["parameter"] for s in an.get("sizing", [])}
        self.an_size_v.setChecked("Vdc_V" in sz)
        self.an_size_i.setChecked("I_peak_max_A" in sz)

    def show_requirement(self, req) -> None:
        """Fill the form from a parsed requirement (e.g. one opened from the requirement set).  Items the form does
        not hold (band operator, initial state) travel in the case itself; the hint says so."""
        self.req_id.setText(req.req_id)
        self.req_text.setPlainText(req.text)
        self.torque.setValue(req.target_Nm)
        self.speed.setValue(req.speed_rpm)
        if req.is_range:
            self.vdc.setValue(req.Vdc_V[0])
            self.vdc_hi.setValue(req.Vdc_V[1])
            self.range_on.setChecked(True)
        else:
            self.vdc.setValue(req.Vdc_V)
            self.range_on.setChecked(False)
        if req.duration_s is None:
            self.dur_none.setChecked(True)
        elif req.duration_s == float("inf"):
            self.dur_cont.setChecked(True)
        else:
            self.dur_sec.setChecked(True)
            self.duration.setValue(req.duration_s)
        for on, w, val in ((self.coolant_on, self.coolant, req.coolant_temp_C),
                           (self.magnet_on, self.magnet, req.magnet_temp_C),
                           (self.winding_on, self.winding, req.winding_temp_C)):
            on.setChecked(val is not None)
            if val is not None:
                w.setValue(val)
        extra = []
        if req.operator == "band":
            extra.append(tr(f"band ±{req.band_Nm:g} N·m", f"band +/-{req.band_Nm:g} N*m"))
        if req.initial_state:
            extra.append(tr(f"초기 상태 {req.initial_state}", f"initial state {req.initial_state}"))
        self.preset_hint.setText(tr("요구 묶음에서 연 요구", "opened from the requirement set") + (
            tr(f" — {', '.join(extra)}는 이 폼에 없어 case에 담겨 계산됩니다(여기서 다시 실행하면 빠짐)",
               f" - {', '.join(extra)} are not on this form; they travel in the case (a re-run from here drops them)")
            if extra else ""))

    def _case(self) -> tuple[dict, list]:
        vdc = [self.vdc.value(), self.vdc_hi.value()] if self.range_on.isChecked() else self.vdc.value()
        req = {"id": self.req_id.text().strip() or "REQ-UI", "text": self.req_text.toPlainText().strip() or "(desktop input)",
               "torque_Nm": self.torque.value(), "speed_rpm": self.speed.value(), "Vdc_V": vdc}
        if self.dur_sec.isChecked():
            req["duration_s"] = self.duration.value()
        elif self.dur_cont.isChecked():
            req["duration_s"] = "continuous"
        if self.coolant_on.isChecked():
            req["coolant_temp_C"] = self.coolant.value()
        if self.magnet_on.isChecked():
            req["magnet_temp_C"] = self.magnet.value()
        if self.winding_on.isChecked():
            req["winding_temp_C"] = self.winding.value()
        extra = {}
        if self.vdc_kind.currentData() == "battery_ocv":
            req["Vdc_port"] = "battery_ocv"
            extra["source_model"] = {"kind": "thevenin", "R_eq_mohm": self.src_R.value(),
                                     "basis": self.src_basis.text().strip()}
        an = {}
        curves = []
        base_v = self.vdc.value()
        imax = self.win.state.drive.inverter.current_limit_A_peak
        if self.an_dom.isChecked():
            an["dominance"] = True
        if self.an_relax.isChecked():
            an["relaxation"] = True
        sizing = []
        if self.an_size_v.isChecked():
            sizing.append({"parameter": "Vdc_V", "range": [400, 800]})
            curves.append(("Vdc_V", 400.0, 800.0))
        if self.an_size_i.isChecked():
            sizing.append({"parameter": "I_peak_max_A", "range": [imax, 2 * imax], "samples": 21})
            curves.append(("I_peak_max_A", imax, 2 * imax))
        if sizing:
            an["sizing"] = sizing
        if isinstance(vdc, float) and self.an_size_v.isChecked():
            an["compare_Vdc"] = sorted({base_v, 600.0})
        body = self.win.state.body(requirement=req, analyses=an, **extra)
        return api.case_from_body(body), curves

    def run(self):
        try:
            case, curves = self._case()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.run_btn.setEnabled(False)
        self.banner.set("NONE", tr("계산 중…", "computing…"))
        self.win.runner.run("decision", tr("요구 판정", "decision"), _evaluate_task, self._show, case, curves,
                            on_error=self._failed)

    def _failed(self, msg, tb):
        self.run_btn.setEnabled(True)
        self.banner.set("NONE", f"<b>{msg.split(':')[0]}</b><br>{msg}")
        error_box(self, tr("판정 실패", "evaluation failed"), msg, tb)

    # --------------------------------------------------------------- results
    def _build_results(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 0, 0, 0)
        self.banner = VerdictBanner()
        kept = self.win._session_kept()                      # the details stay folded or open as the person left them
        self.banner.set_details_shown(kept and self.win.settings.value("decision/banner_details", False, type=bool))
        self.banner.details_toggled.connect(
            lambda on: self.win.settings.setValue("decision/banner_details", on) if self.win._session_kept() else None)
        v.addWidget(self.banner)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("표시 조건:", "condition:")))
        self.cond_combo = QComboBox()
        self.cond_combo.currentIndexChanged.connect(self._condition_changed)
        row.addWidget(self.cond_combo, 1)
        self.save_json = QPushButton(tr("JSON 저장", "save JSON"))
        self.save_md = QPushButton(tr("Markdown 저장", "save Markdown"))
        self.save_pdf = QPushButton(tr("PDF 보고서", "PDF report"))
        for b, fn in ((self.save_json, self._save_json), (self.save_md, self._save_md), (self.save_pdf, self._save_pdf)):
            b.clicked.connect(fn)
            b.setEnabled(False)
            row.addWidget(b)
        v.addLayout(row)
        self.tabs = QTabWidget()
        # the engineering reading of the result (conclusion, item by item, mechanism, what would change it)
        self.insight = InsightPanel(tr("판정을 실행하면 결과의 엔지니어링 해석이 여기에 표시됩니다 — 결론과 여유, 판정 항목별 근거, "
                                       "운전점에서 무엇이 한계인지, 전력·손실, 병목 기여도, 요구를 만족시키려면 무엇을 바꿔야 하는지, "
                                       "이 결과가 말하지 않는 것.",
                                       "Run the evaluation to read the result here: the conclusion and margin, each judged "
                                       "item, what limits at the operating point, power and losses, the bottleneck "
                                       "contributions, what would make it pass and what the result does not cover."))
        self.tabs.addTab(self.insight, tr("엔지니어링 분석", "engineering reading"))
        # summary: the claim tree beside the four sections on a wide result area, above them on a narrow one
        summ = _AdaptiveSplitter(800)
        self.claims = ClaimTree()
        self.key_table = KeyValueTable(fit_rows=True)
        self.layers_table = KeyValueTable(headers=[tr("층", "layer"), tr("상태", "status"), tr("의미", "meaning")],
                                          fit_rows=True)
        self.layers_table.setToolTip(tr("서로 다른 진술을 하나의 판정으로 합치지 않습니다: 수치 증거 / 이 모델의 요구 판정 / "
                                        "요구의 완결성 / 데이터의 qualification",
                                        "Separate statements never merged into one verdict: numerical evidence / the "
                                        "requirement verdict for this model / requirement completeness / data qualification"))
        self.limiting = QListWidget()
        self.actions = QListWidget()
        for lw in (self.limiting, self.actions):
            lw.setWordWrap(True)
            lw.setAlternatingRowColors(True)
        # four titled sections in one scrolling column; the tables are as tall as their rows, so no row is ever cut
        # to a bare header (a short window scrolls instead)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 4, 0)
        rl.setSpacing(4)
        for title, body in ((tr("<b>판정 층</b> (수학 · 모델 · 요구 · qualification — 서로 다른 진술)",
                             "<b>claim layers</b> (mathematical · model · requirement · qualification — separate)"),
                          self.layers_table),
                         (tr("<b>핵심 수치</b>", "<b>key numbers</b>"), self.key_table),
                         (tr("<b>제한 요인</b>", "<b>limiting factors</b>"), self.limiting),
                         (tr("<b>다음 조치 · 결론을 바꿀 자료</b>", "<b>next actions · data that would change the decision</b>"),
                          self.actions)):
            head = QLabel(title)
            head.setWordWrap(True)
            rl.addWidget(head)
            rl.addWidget(body)
        for lw in (self.limiting, self.actions):
            lw.setMinimumHeight(120)
        rl.addStretch(1)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.NoFrame)
        right_scroll.setWidget(right)
        summ.addWidget(self.claims)
        summ.addWidget(right_scroll)
        summ.setSizes([560, 560])
        self.summary_tab = summ
        self.tabs.addTab(summ, tr("요약·근거", "summary · evidence"))
        self.views = OperatingViews()
        self.tabs.addTab(self.views, tr("운전점 그래프", "operating point"))
        self.env_panel = PlotPanel(hint=tr("이 탭을 열면 요구 Vdc에서 T–n 곡선을 계산합니다.", "Opening this tab computes the T–n envelope at the request Vdc."))
        self.tabs.addTab(self.env_panel, tr("T–n 위치", "T–n position"))
        an = QWidget()
        al = QVBoxLayout(an)
        al.setContentsMargins(0, 0, 0, 0)
        self.an_tabs = QTabWidget()
        al.addWidget(self.an_tabs)
        self.an_tab = an
        self.tabs.addTab(an, tr("추가 분석", "analyses"))
        self.record_view = QTextBrowser()
        self.record_view.setOpenExternalLinks(False)
        self.tabs.addTab(self.record_view, tr("의사결정 기록", "decision record"))
        self.tabs.currentChanged.connect(self._tab_changed)
        v.addWidget(self.tabs, 1)
        return w

    def _show(self, res):
        self.run_btn.setEnabled(True)
        self.result = res
        rec = res["record"]
        v = rec["verdict"]
        reasons = ", ".join(reason_label(r) for r in v["reasons"]) or "—"
        qual = "".join(f"<br>· {q}" for q in v.get("qualifiers", []))
        cls = classify(v["status"], v["reasons"])
        why = why_more = ""
        if v["status"] != "FEASIBLE":           # the class of the answer says which kind of work could change it
            why = f" · {tr('분류', 'class')}: <b>{tr(cls['label_ko'], cls['label_en'])}</b>"
            why_more = ((f"<br>{tr('분류', 'class')}: {engine_text(cls['hint'])}" if cls["hint"] else "")
                        + (tr(f" (원인 {len(cls['classes'])}개: {', '.join(cls['classes'])})",
                              f" ({len(cls['classes'])} causes: {', '.join(cls['classes'])})")
                           if len(cls.get("classes") or []) > 1 else ""))
        ins = decision_insight(rec, res.get("pwm_risk"))
        self.insight.show_insight(ins)
        deciding = ", ".join(dict.fromkeys(claim_label(n) for n in v.get("deciding_claims", []))) or "—"
        # always shown: the conclusion with its numbers, the requirement id, why, and (not proven) what kind of work
        # could change it; folded: the requirement text, qualifiers, reason codes, scope and the record identity
        html = (f"<span style='font-size:10.5pt'>{ins.headline}</span><br>"
                f"<b>{rec['requirement'].get('req_id', '')}</b> · {tr('사유', 'reasons')}: <b>{reasons}</b> · "
                f"{tr('결정 항목', 'deciding items')}: {deciding}{why}")
        codes = ", ".join(v["reasons"]) or "—"
        details = (f"<b>{rec['requirement'].get('req_id', '')}</b> — {rec['requirement'].get('original_text', '')}{qual}"
                   f"{why_more}<br>{tr('사유 코드', 'reason codes')}: {codes}"
                   f"<br><span style='font-size:8.5pt'>{tr('범위', 'scope')}: {v.get('scope', '')}<br>record "
                   f"{rec['record_id']} · input SHA-256 {rec['input_sha256'][:16]}… · {rec.get('elapsed_s', 0):.2f} s</span>")
        self._banner_last = (v["verdict"], html, details)
        self._inputs_note = ""
        self._set_banner(*self._banner_last)
        self.cond_combo.blockSignals(True)
        self.cond_combo.clear()
        for i, c in enumerate(rec["conditions"]):
            sc = c["scenario"]
            st = c["requirement_claim_at_this_condition"]["status"]
            mt = "" if sc.get("magnet_temp_C") is None else tr(f" · 자석 {sc['magnet_temp_C']:g} °C",
                                                              f" · magnet {sc['magnet_temp_C']:g} °C")
            self.cond_combo.addItem(f"#{i + 1}  n = {sc['speed_rpm_mechanical']:g} rpm · Vdc = "
                                    f"{sc['Vdc_V_inverter_dc_terminal']:g} V{mt} · {st}", i)
        self.cond_combo.blockSignals(False)
        groups = []
        for i, c in enumerate(rec["conditions"]):
            ps = c["policy_solution"]
            cl = [c["requirement_claim_at_this_condition"]] + ps["claims"]
            if c.get("duration_claim"):
                cl.append(c["duration_claim"])
            groups.append((f"{tr('조건', 'condition')} #{i + 1}: {c['scenario']['scenario_id']}", cl))
        self.claims.set_claims(groups)
        self.limiting.clear()
        self.limiting.addItems(rec["limiting_factors"] or ["—"])
        self.actions.clear()
        self.actions.addItems((rec["next_actions"] or []) + [f"[{tr('미평가', 'not evaluated')}] {x}" for x in rec["not_evaluated"]])
        self.record_view.setMarkdown(rec["markdown"])
        lay = v.get("layers") or {}
        lrows, lcol = [], {}
        good = {"CERTIFIED", "COMPLETE", "FEASIBLE"}
        for i, key in enumerate(("mathematical", "model", "requirement", "qualification")):
            L = lay.get(key) or {}
            st = str(L.get("status", "—"))
            meaning = L.get("meaning", "")
            if key == "requirement" and L.get("open_items"):
                meaning = "; ".join(L["open_items"])
            if key == "qualification":
                meaning = f"{L.get('validation_status', '')} · " + " | ".join(L.get("sub_models", []))
            lrows.append((LAYER_NAMES[key](), st, meaning))
            lcol[(i, 1)] = "#1a7f37" if (st in good or st.startswith("FEASIBLE")) else (
                "#cf222e" if st in ("INFEASIBLE", "UNRESOLVED") else "#b7791f")
        self.layers_table.set_rows(lrows, lcol)
        for b in (self.save_json, self.save_md, self.save_pdf):
            b.setEnabled(True)
        self._fill_analyses(res)
        self._condition_changed(0)
        self._env_cache.pop("current", None)
        self.env_panel.placeholder(tr("이 탭을 열면 T–n 곡선을 계산합니다.", "Opening this tab computes the T–n envelope."))
        if self.tabs.currentWidget() is self.env_panel:
            self._tab_changed(self.tabs.currentIndex())

    def _condition_changed(self, idx):
        if self.result is None or idx < 0:
            return
        rec_d, rec, case = self.result["record"], self.result["rec"], self.result["case"]
        views = self.result["views"].get(idx)
        if views is None:
            views = _condition_views(rec, case, idx)
            self.result["views"][idx] = views
        c = rec_d["conditions"][idx]
        sc = c["scenario"]
        title = (f"{rec_d['requirement'].get('req_id', '')} · {sc['speed_rpm_mechanical']:g} rpm · "
                 f"{rec.conditions[idx].primary_torque_Nm:g} N·m · {sc['Vdc_V_inverter_dc_terminal']:g} V"
                 + ("" if sc.get("magnet_temp_C") is None else f" · {sc['magnet_temp_C']:g} °C"))
        self.views.show_plane(views["plane"], title=title)
        if views["pv"] is not None:
            self.views.show_point(views["pv"], title + tr(" · 최소전류 정책점", " · minimum-current policy point"))
        else:
            for p in (self.views.overview, self.views.wave, self.views.phasor, self.views.power):
                p.placeholder(tr("이 조건에는 정책 운전점이 없습니다 (전기적 해 없음 또는 미확정). id–iq 지도에서 원인을 확인하세요.",
                                 "No policy operating point at this condition. See the id–iq map for the cause."))
            self.views.table.set_rows([])
        margin = c["torque_capability_margin_Nm"]
        pcap = c.get("policy_capability") or {}
        rw = c.get("requirement_witness")
        rows = [(tr("요구 판정 (이 조건)", "requirement at condition"), c["requirement_claim_at_this_condition"]["status"]),
                (tr("요구 근거점 witness (정적·DC·지속 모두 같은 점)", "requirement witness (static, DC, duration at one point)"),
                 "—" if not rw else f"T = {fmt(rw['torque_Nm'])} N·m, id / iq = {fmt(rw['id_A'])} / {fmt(rw['iq_A'])} A"),
                (tr("토크 capability 여유 [N·m]", "torque capability margin [N·m]"), fmt(margin)),
                (tr("정책 capability [N·m] / 인증", "policy capability [N·m] / certified"),
                 f"{fmt(pcap.get('achieved_value_Nm'))} / {yes_no(pcap.get('certified'))}"),
                (tr("capability를 정하는 제약", "capability limited by"),
                 ", ".join(constraint_label(n) for n in pcap.get("active_constraints_at_witness") or []) or "—")]
        rc = c.get("rejected_band_centre")
        if rc:
            rows.append((tr("밴드 중심 (탈락 후보, 진단용)", "band centre (rejected candidate, diagnostic)"),
                         f"T = {fmt(rc['torque_Nm'])} N·m: {rc['policy_claim']} · P_dc = {fmt(rc['Pdc_W'])} W"))
        op = c["policy_solution"]["operating_point"]
        if op:
            cur = next((x for x in op["constraints"] if x["name"] == "CURRENT"), None)
            rows += [("id / iq [A]", f"{op['id_A_peak']:.4f} / {op['iq_A_peak']:.4f}"),
                     (tr("전류 여유 [A]", "current margin [A]"), fmt(cur["slack"] if cur else None)),
                     (tr("전압 여유 [V]", "voltage margin [V]"), fmt(op["voltage"]["remaining_command_margin_V"])),
                     ("P_dc [kW] / I_dc [A]", f"{fmt(None if op['Pdc_W'] is None else op['Pdc_W'] / 1e3)} / {fmt(op['Idc_A_average'])}"),
                     (tr("효율", "efficiency"), f"{fmt(op['efficiency'])} ({op['energy_mode']})"),
                     (tr("제약 위반", "violated"), ", ".join(constraint_label(n) for n in op["violated_groups"]) or "—")]
        self.key_table.set_rows(rows)

    def _fill_analyses(self, res):
        while self.an_tabs.count():
            w = self.an_tabs.widget(0)
            self.an_tabs.removeTab(0)
            w.deleteLater()
        an = res["record"].get("analyses") or {}
        sizing = {s["parameter"]["parameter"]: s for s in an.get("sizing", [])}
        for cv in res["curves"]:
            p = PlotPanel()
            name = cv["parameter"]["parameter"]
            p.draw(F.fig_capability_vs_parameter, cv, sizing.get(name), name=f"capability_vs_{name}",
                   csv=lambda cv=cv: {name: cv["values"], "policy_capability_Nm": cv["capability_Nm"],
                                      "status_at_request": cv["status"]})
            self.an_tabs.addTab(p, f"{tr('역설계', 'sizing')}: {param_label(name)}")
        if an.get("dominance") or an.get("relaxation"):
            p = PlotPanel()
            dom = an.get("dominance") or {"single": [], "base_policy_capability_Nm": None}
            p.draw(F.fig_dominance, dom, an.get("relaxation"), name="dominance")
            self.an_tabs.addTab(p, tr("병목·완화", "bottleneck · relaxation"))
        if an.get("comparison"):
            t = KeyValueTable(headers=[tr("시나리오", "scenario"), "policy", "id / iq [A]", "P_dc [kW]", tr("전압 여유 [V]", "V margin [V]")])
            rows = []
            for s in an["comparison"]["scenarios"]:
                rows.append((s["scenario_id"], s["claims"].get("policy_static", "—"),
                             f"{fmt(s.get('id_A'))} / {fmt(s.get('iq_A'))}",
                             fmt(None if s.get("Pdc_W") is None else s["Pdc_W"] / 1e3), fmt(s.get("voltage_margin_V"))))
            t.set_rows(rows)
            self.an_tabs.addTab(t, tr("시나리오 비교", "comparison"))
        pr = res.get("pwm_risk")
        if pr:
            t = KeyValueTable(headers=[tr("항목", "item"), tr("값", "value")])
            t.set_rows(_pwm_risk_rows(pr))
            self.an_tabs.addTab(t, tr("PWM 위험 (같은 운전점)", "PWM risk (same point)"))
        if self.an_tabs.count() == 0:
            lab = hint(tr("요청된 추가 분석이 없습니다. 왼쪽 '추가 분석'에서 선택하세요.", "No additional analyses requested."))
            lab.setAlignment(Qt.AlignCenter)
            self.an_tabs.addTab(lab, "—")

    def _tab_changed(self, idx):
        if self.tabs.widget(idx) is not self.env_panel or self.result is None:
            return
        rec = self.result["rec"]
        sc = rec.conditions[max(0, self.cond_combo.currentIndex())].scenario
        key = (id(self.result), sc.Vdc_V, sc.magnet_temp_C)
        if key in self._env_cache:
            self._draw_env(self._env_cache[key])
            return
        self.env_panel.placeholder(tr("T–n 곡선 계산 중…", "computing the T–n envelope…"))
        drive = self.result["case"].drive

        def done(env, key=key):
            self._env_cache[key] = env
            self._draw_env(env)
        self.win.runner.run("decision-env", tr("T–n 곡선", "T–n envelope"), _envelope_task, done, drive, sc.source_limits,
                            sc.Vdc_V, sc.magnet_temp_C)

    def _draw_env(self, env):
        rec_d = self.result["record"]
        reqs = []
        for c in rec_d["conditions"]:
            sc = c["scenario"]
            if abs(sc["Vdc_V_inverter_dc_terminal"] - env["Vdc_V"]) < 1e-9 and \
                    sc.get("magnet_temp_C") == env.get("magnet_temp_C"):
                st = c["requirement_claim_at_this_condition"]["status"]
                reqs.append({"id": rec_d["requirement"].get("req_id", ""), "speed_rpm": sc["speed_rpm_mechanical"],
                             "torque_Nm": self.result["rec"].requirement.target_Nm,
                             "verdict": {"FEASIBLE": "PASS", "INFEASIBLE": "FAIL"}.get(st, "UNKNOWN")})
        title = None if env.get("magnet_temp_C") is None else tr(
            f"T–n 성능 곡선 · Vdc = {env['Vdc_V']:.0f} V · 자석 {env['magnet_temp_C']:g} °C",
            f"T–n envelope · Vdc = {env['Vdc_V']:.0f} V · magnet {env['magnet_temp_C']:g} °C")
        self.env_panel.draw(F.fig_envelope, env, reqs, title=title, name=f"envelope_{env['Vdc_V']:.0f}V",
                            csv=lambda env=env: {"speed_rpm": env["x"], "policy_max_Nm": env["max"]["T_Nm"],
                                                 "electrical_max_Nm": env["max"]["electrical_T_Nm"],
                                                 "policy_min_Nm": env["min"]["T_Nm"],
                                                 "electrical_min_Nm": env["min"]["electrical_T_Nm"]})

    # ------------------------------------------------------------------- save
    def _confirm_current(self) -> bool:
        """Saving a result whose inputs were edited since: say which inputs it belongs to before writing it."""
        diffs = self.win.input_changes("decision")
        if not diffs:
            return True
        shown = "\n".join(f"· {f}: {a} → {b}" for f, a, b in diffs[:8])
        return confirm(self, tr("바뀌기 전 입력의 결과", "result of the earlier inputs"),
                       tr(f"화면의 판정은 바뀌기 전 입력으로 계산됐습니다:\n{shown}\n\n저장되는 기록도 그 입력의 결과입니다. "
                          f"그대로 저장할까요? (지금 입력으로 저장하려면 먼저 다시 실행하세요.)",
                          f"The verdict on screen was computed from the earlier inputs:\n{shown}\n\nThe saved record is "
                          f"that result. Save it anyway? (Run again first to save the current inputs.)"))

    def _save_json(self):
        if not self._confirm_current():
            return
        rec = self.result["record"]
        path, _ = QFileDialog.getSaveFileName(self, tr("의사결정 기록 저장", "save decision record"), f"{rec['record_id']}.json", "JSON (*.json)")
        if path:
            data = {k: v for k, v in rec.items() if k != "markdown"}
            Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _save_md(self):
        if not self._confirm_current():
            return
        rec = self.result["record"]
        path, _ = QFileDialog.getSaveFileName(self, tr("Markdown 저장", "save Markdown"), f"{rec['record_id']}.md", "Markdown (*.md)")
        if path:
            reading = decision_insight(rec, self.result.get("pwm_risk"))
            md = ("## " + tr("엔지니어링 분석 (아래 기록의 수치에서 도출)", "Engineering reading (from the record's numbers below)")
                  + "\n\n" + reading.markdown() + "\n\n---\n\n" + rec["markdown"])
            if rec.get("project_context"):                      # the product data it was computed from (R2)
                from ...report_pdf import project_line
                md += "\n\n## " + tr("프로젝트 (제품 데이터)", "Project (product data)") + "\n\n" + \
                    project_line(rec["project_context"]) + "\n"
            Path(path).write_text(md, encoding="utf-8")

    def _save_pdf(self):
        if not self._confirm_current():
            return
        rec = self.result["record"]
        path, _ = QFileDialog.getSaveFileName(self, tr("PDF 보고서 저장", "save PDF report"), f"{rec['record_id']}.pdf", "PDF (*.pdf)")
        if path:
            from ...report_pdf import build_pdf
            res = self.result
            self.win.runner.run("pdf", tr("PDF 보고서", "PDF report"),
                                lambda progress: build_pdf(path, res["record"], res["rec"], res["case"], progress=progress,
                                                           pwm_risk=res.get("pwm_risk")),
                                self._pdf_done)

    def _pdf_done(self, p):
        # the report is built in light theme on a worker thread: a figure the user drew meanwhile may carry it
        self.win.redraw_all()
        self.win.statusBar().showMessage(tr(f"보고서 저장: {p}", f"report saved: {p}"), 8000)

    def redraw(self):
        self.banner.restyle()
        self.insight.redraw()
        self.views.redraw()
        self.env_panel.redraw()
        for i in range(self.an_tabs.count()):
            w = self.an_tabs.widget(i)
            if isinstance(w, PlotPanel):
                w.redraw()


def parameter_choices():
    return [(f"{param_label(k)} [{v[1]}] · {change_kind_label(v[0])}", k) for k, v in PARAMETERS.items()]
