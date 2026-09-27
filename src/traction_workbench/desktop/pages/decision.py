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
from ...plots import figures as F
from ...viz import design as DS
from ...viz import maps as M
from ...viz import operating as O
from ...viz import sweeps as SW
from ..opviews import OperatingViews
from ..widgets import (ConceptNote, ClaimTree, KeyValueTable, PlotPanel, VerdictBanner, check, error_box, fmt, hint, number,
                       primary_button)


def _evaluate_task(progress, case: dict, analyses_curves: list):
    progress(0.05, tr("판정 계산", "evaluating"))
    rec_d, rec, case_obj = S.evaluate_case_full(case)
    out = {"record": rec_d, "rec": rec, "case": case_obj, "curves": []}
    progress(0.6, tr("id–iq 지도", "id–iq map"))
    out["views"] = {0: _condition_views(rec, case_obj, 0)}
    for i, (param, lo, hi) in enumerate(analyses_curves):
        progress(0.7 + 0.25 * i / max(1, len(analyses_curves)), f"{param}")
        sc = rec.conditions[0].scenario
        out["curves"].append(DS.capability_vs_parameter(case_obj.drive, sc, param, np.linspace(lo, hi, 31),
                                                        T_request=rec.requirement.target_Nm,
                                                        direction=1 if rec.requirement.target_Nm >= 0 else -1))
    progress(1.0, "")
    return out


def _condition_views(rec, case_obj, idx: int) -> dict:
    cond = rec.conditions[idx]
    sc = cond.scenario
    T = rec.requirement.target_Nm
    plane = M.idiq_plane(case_obj.drive, sc.source_limits, sc.speed_rpm, sc.Vdc_V, T, scenario=sc)
    pv = None
    pt = cond.solution.point
    if pt is not None:
        pv = O.point_view(case_obj.drive, sc, pt.id_A, pt.iq_A, T)
    return {"plane": plane, "pv": pv}


def _envelope_task(progress, drive, limits, Vdc):
    return SW.envelope(drive, limits, Vdc, n=33, progress=progress)


class DecisionPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.result = None
        self._env_cache = {}
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_form())
        split.addWidget(self._build_results())
        split.setStretchFactor(1, 1)
        split.setSizes([330, 1100])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)
        self._load_preset(0)

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
        self.vdc = number(600, 1, 2000, "V", 2, 10.0, tr("인버터 DC 단자 전압", "inverter DC terminal voltage"))
        self.range_on = check(tr("Vdc 범위 요구", "Vdc range"), False,
                              tr("범위 전체를 요구하면 표본점 통과만으로 PASS가 아닙니다 (SAMPLED_COVERAGE).",
                                 "a range requirement never becomes PASS from samples alone (SAMPLED_COVERAGE)"))
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
        f.addRow("ID", self.req_id)
        f.addRow(tr("원문", "text"), self.req_text)
        f.addRow(tr("토크", "torque"), self.torque)
        f.addRow(tr("속도", "speed"), self.speed)
        f.addRow("Vdc", self.vdc)
        f.addRow("", rr)
        f.addRow(tr("시간", "time"), self.dur_none)
        f.addRow("", dr)
        f.addRow("", self.dur_cont)
        f.addRow("", cr)
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
        self.run_btn.clicked.connect(self.run)
        self.run_btn.setShortcut("Ctrl+Return")
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
        body = self.win.state.body(requirement=req, analyses=an)
        return api._case(body), curves

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
        # summary
        summ = QSplitter(Qt.Horizontal)
        self.claims = ClaimTree()
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.key_table = KeyValueTable()
        self.layers_table = KeyValueTable(headers=[tr("층", "layer"), tr("상태", "status"), tr("의미", "meaning")])
        self.layers_table.setToolTip(tr("서로 다른 진술을 하나의 판정으로 합치지 않습니다: 수치 증거 / 이 모델의 요구 판정 / "
                                        "요구의 완결성 / 데이터의 qualification",
                                        "Separate statements never merged into one verdict: numerical evidence / the "
                                        "requirement verdict for this model / requirement completeness / data qualification"))
        self.limiting = QListWidget()
        self.actions = QListWidget()
        for lw in (self.limiting, self.actions):
            lw.setWordWrap(True)
            lw.setAlternatingRowColors(True)
        rv.addWidget(QLabel(tr("<b>판정 층</b> (수학 · 모델 · 요구 · qualification — 서로 다른 진술)",
                               "<b>claim layers</b> (mathematical · model · requirement · qualification — separate)")))
        rv.addWidget(self.layers_table, 2)
        rv.addWidget(QLabel(tr("<b>핵심 수치</b>", "<b>key numbers</b>")))
        rv.addWidget(self.key_table, 3)
        rv.addWidget(QLabel(tr("<b>제한 요인</b>", "<b>limiting factors</b>")))
        rv.addWidget(self.limiting, 1)
        rv.addWidget(QLabel(tr("<b>다음 조치 · 결론을 바꿀 자료</b>", "<b>next actions · data that would change the decision</b>")))
        rv.addWidget(self.actions, 2)
        summ.addWidget(self.claims)
        summ.addWidget(right)
        summ.setSizes([640, 460])
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
        reasons = ", ".join(v["reasons"]) or "—"
        qual = "".join(f"<br>· {q}" for q in v.get("qualifiers", []))
        html = (f"<b>{rec['requirement'].get('req_id', '')}</b> — {rec['requirement'].get('original_text', '')}<br>"
                f"{tr('사유', 'reasons')}: <b>{reasons}</b> · {tr('결정 claim', 'deciding claims')}: "
                f"{', '.join(v.get('deciding_claims', [])) or '—'}{qual}<br>"
                f"<span style='font-size:8pt'>{tr('범위', 'scope')}: {v.get('scope', '')}<br>record {rec['record_id']} · "
                f"input SHA-256 {rec['input_sha256'][:16]}… · {rec.get('elapsed_s', 0):.2f} s</span>")
        self.banner.set(v["verdict"], html)
        self.cond_combo.blockSignals(True)
        self.cond_combo.clear()
        for i, c in enumerate(rec["conditions"]):
            sc = c["scenario"]
            st = c["requirement_claim_at_this_condition"]["status"]
            self.cond_combo.addItem(f"#{i + 1}  n = {sc['speed_rpm_mechanical']:g} rpm · Vdc = {sc['Vdc_V_inverter_dc_terminal']:g} V · {st}", i)
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
            lrows.append((key, st, meaning))
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
                 f"{rec.requirement.target_Nm:g} N·m · {sc['Vdc_V_inverter_dc_terminal']:g} V")
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
                (tr("요구 witness (정적·DC·지속 모두 같은 점)", "requirement witness (static, DC, duration at one point)"),
                 "—" if not rw else f"T = {fmt(rw['torque_Nm'])} N·m, id / iq = {fmt(rw['id_A'])} / {fmt(rw['iq_A'])} A"),
                (tr("토크 capability 여유 [N·m]", "torque capability margin [N·m]"), fmt(margin)),
                (tr("정책 capability [N·m] / certified", "policy capability [N·m] / certified"),
                 f"{fmt(pcap.get('achieved_value_Nm'))} / {pcap.get('certified')}"),
                (tr("capability 제한 제약", "capability limited by"), ", ".join(pcap.get("active_constraints_at_witness") or []) or "—")]
        op = c["policy_solution"]["operating_point"]
        if op:
            cur = next((x for x in op["constraints"] if x["name"] == "CURRENT"), None)
            rows += [("id / iq [A]", f"{op['id_A_peak']:.4f} / {op['iq_A_peak']:.4f}"),
                     (tr("전류 여유 [A]", "current margin [A]"), fmt(cur["slack"] if cur else None)),
                     (tr("전압 여유 [V]", "voltage margin [V]"), fmt(op["voltage"]["remaining_command_margin_V"])),
                     ("P_dc [kW] / I_dc [A]", f"{fmt(None if op['Pdc_W'] is None else op['Pdc_W'] / 1e3)} / {fmt(op['Idc_A_average'])}"),
                     (tr("효율", "efficiency"), f"{fmt(op['efficiency'])} ({op['energy_mode']})"),
                     (tr("제약 위반", "violated"), ", ".join(op["violated_groups"]) or "—")]
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
            self.an_tabs.addTab(p, f"{tr('역설계', 'sizing')}: {name}")
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
        if self.an_tabs.count() == 0:
            lab = hint(tr("요청된 추가 분석이 없습니다. 왼쪽 '추가 분석'에서 선택하세요.", "No additional analyses requested."))
            lab.setAlignment(Qt.AlignCenter)
            self.an_tabs.addTab(lab, "—")

    def _tab_changed(self, idx):
        if self.tabs.widget(idx) is not self.env_panel or self.result is None:
            return
        rec = self.result["rec"]
        sc = rec.conditions[max(0, self.cond_combo.currentIndex())].scenario
        key = (id(self.result), sc.Vdc_V)
        if key in self._env_cache:
            self._draw_env(self._env_cache[key])
            return
        self.env_panel.placeholder(tr("T–n 곡선 계산 중…", "computing the T–n envelope…"))
        drive = self.result["case"].drive

        def done(env, key=key):
            self._env_cache[key] = env
            self._draw_env(env)
        self.win.runner.run("decision-env", tr("T–n 곡선", "T–n envelope"), _envelope_task, done, drive, sc.source_limits, sc.Vdc_V)

    def _draw_env(self, env):
        rec_d = self.result["record"]
        reqs = []
        for c in rec_d["conditions"]:
            sc = c["scenario"]
            if abs(sc["Vdc_V_inverter_dc_terminal"] - env["Vdc_V"]) < 1e-9:
                st = c["requirement_claim_at_this_condition"]["status"]
                reqs.append({"id": rec_d["requirement"].get("req_id", ""), "speed_rpm": sc["speed_rpm_mechanical"],
                             "torque_Nm": self.result["rec"].requirement.target_Nm,
                             "verdict": {"FEASIBLE": "PASS", "INFEASIBLE": "FAIL"}.get(st, "UNKNOWN")})
        self.env_panel.draw(F.fig_envelope, env, reqs, name=f"envelope_{env['Vdc_V']:.0f}V",
                            csv=lambda env=env: {"speed_rpm": env["x"], "policy_max_Nm": env["max"]["T_Nm"],
                                                 "electrical_max_Nm": env["max"]["electrical_T_Nm"],
                                                 "policy_min_Nm": env["min"]["T_Nm"],
                                                 "electrical_min_Nm": env["min"]["electrical_T_Nm"]})

    # ------------------------------------------------------------------- save
    def _save_json(self):
        rec = self.result["record"]
        path, _ = QFileDialog.getSaveFileName(self, tr("의사결정 기록 저장", "save decision record"), f"{rec['record_id']}.json", "JSON (*.json)")
        if path:
            data = {k: v for k, v in rec.items() if k != "markdown"}
            Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _save_md(self):
        rec = self.result["record"]
        path, _ = QFileDialog.getSaveFileName(self, tr("Markdown 저장", "save Markdown"), f"{rec['record_id']}.md", "Markdown (*.md)")
        if path:
            Path(path).write_text(rec["markdown"], encoding="utf-8")

    def _save_pdf(self):
        rec = self.result["record"]
        path, _ = QFileDialog.getSaveFileName(self, tr("PDF 보고서 저장", "save PDF report"), f"{rec['record_id']}.pdf", "PDF (*.pdf)")
        if path:
            from ...report_pdf import build_pdf
            res = self.result
            self.win.runner.run("pdf", tr("PDF 보고서", "PDF report"),
                                lambda progress: build_pdf(path, res["record"], res["rec"], res["case"], progress=progress),
                                lambda p: self.win.statusBar().showMessage(tr(f"보고서 저장: {p}", f"report saved: {p}"), 8000))

    def redraw(self):
        self.banner.restyle()
        self.views.redraw()
        self.env_panel.redraw()
        for i in range(self.an_tabs.count()):
            w = self.an_tabs.widget(i)
            if isinstance(w, PlotPanel):
                w.redraw()


def parameter_choices():
    return [(f"{k} · {v[0]} [{v[1]}]", k) for k, v in PARAMETERS.items()]
