"""Safety screening: FTTI chain, DC-link discharge / overvoltage, safe-state (ASC / freewheel) comparison."""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLineEdit,
                               QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...extensions.dclink import back_emf_ll_peak, speed_for_back_emf
from ...plots import figures as F
from ...plots import schematics as SC
from ...plots.labels import reason_label
from ...viz import safety as SF
from ...insight.safety import (discharge_insight, ftti_insight, overvoltage_insight, passive_insight,
                               safe_state_insight)
from ..widgets import (ConceptNote, KeyValueTable, PlotPanel, check, combo, error_box, fmt, hint, number,
                       primary_button, reading_tab, with_reading)

NOTE_FTTI = lambda: (tr("<b>FTTI (Fault Tolerant Time Interval)</b>: 고장 발생부터 위험 사건이 일어나기 전까지 허용되는 시간입니다. "
                "감지 시간(FDTI: 센싱·필터·판정·디바운스)과 반응 시간(FRTI: 요청·게이트 차단·전류 감쇠)의 최악 합이 FTTI보다 짧아야 합니다. "
                "주기 태스크는 한 주기의 샘플링 지연을 더합니다. 같은 구간을 두 담당(예: SW와 System)이 각각 예산에 넣으면 "
                "<b>이중 계산</b>으로 표시합니다. 입력값 자체의 정확성은 검증하지 않습니다.",
                "<b>FTTI</b>: time allowed from the fault to the hazardous event. The worst-case detection (FDTI) plus reaction "
                "(FRTI) must stay below it. Periodic tasks add one sampling period. The same interval budgeted by two owners is "
                "flagged as a <b>double count</b>. The declared latencies themselves are not verified."))
NOTE_DISCHARGE = lambda: (tr("<b>능동 방전</b>: 배터리 릴레이가 열린 뒤 DC 링크 커패시터에 남은 에너지 ½·C·V²를 방전 저항으로 소모합니다.<br>"
                     "V(t) = V₀·e<sup>−t/RC</sup>, 목표 V_f 도달 시간 t = R·C·ln(V₀/V_f).<br>"
                     "모터가 돌고 있으면 역기전력(선간 peak = √3·ω_e·ψ)이 인버터 다이오드로 정류되어 링크 전압이 그 아래로 "
                     "내려가지 않습니다 → 속도가 충분히 낮아져야 방전이 끝납니다.",
                     "<b>Active discharge</b>: after the battery contactors open, the DC-link energy ½·C·V² is dissipated in the "
                     "discharge resistor. V(t) = V₀·e<sup>−t/RC</sup>, t = R·C·ln(V₀/V_f). While the motor spins, the rectified "
                     "back-EMF (line-line peak √3·ω_e·ψ) holds the link above it, so the discharge completes only at low speed."))
NOTE_PASSIVE = lambda: (tr(
    "<b>패시브 방전</b>: DC 링크에 스위치 없이 항상 연결된 블리더 저항 R_p입니다. 제어기 전원 상실·고장으로 능동 방전이 안 될 때의 "
    "백업입니다.<br>V(t) = V₀·e<sup>−t/(R_p·C)</sup>, 목표 V_f 도달 시간 t = R_p·C·ln(V₀/V_f) → 시간 조건은 "
    "R_p ≤ t_req/(C·ln(V₀/V_f)).<br>대신 릴레이가 닫혀 있는(주행) 동안 항상 P = V²/R_p를 소모합니다 → 손실·저항 정격 조건은 "
    "R_p ≥ V_max²/P_허용. 두 조건 사이가 <b>설계 창</b>이며, P·t = C·V²·ln(V₀/V_f)는 R_p와 무관하므로 빠른 방전과 작은 상시 손실은 "
    "맞바꿈 관계입니다. 능동 방전 저항 R_a가 켜지면 병렬(R_a‖R_p)로 더 빨리 방전합니다. 모터가 돌면 역기전력 정류가 방전을 막습니다.",
    "<b>Passive discharge</b>: a bleeder R_p permanently across the DC link — the backup when the active discharge is "
    "unavailable. V(t) = V₀·e<sup>−t/(R_p·C)</sup>, t = R_p·C·ln(V₀/V_f) → R_p ≤ t_req/(C·ln(V₀/V_f)). It always "
    "dissipates P = V²/R_p while the contactors are closed → R_p ≥ V_max²/P_allow. Between the two is the <b>design "
    "window</b>; P·t = C·V²·ln(V₀/V_f) does not depend on R_p, so a fast discharge costs continuous loss. With the "
    "active resistor on, R_a‖R_p discharges faster. A spinning motor's rectified back-EMF blocks the discharge."))
NOTE_OV = lambda: (tr("<b>회생 중 배터리 차단</b>: 모터가 발전(회생)하는 동안 릴레이가 열리면 회생 전력 P가 갈 곳이 커패시터뿐입니다.<br>"
              "½·C·(V₂² − V₁²) = ∫P dt → 한계 V_lim까지 시간 t = C·(V_lim² − V₁²)/(2P).<br>"
              "그 전에 토크를 줄이거나(반응 시간) ASC로 전환해야 합니다. 인버터를 끄는 것(freewheel)만으로는 역기전력이 "
              "V_dc보다 높으면 다이오드로 계속 충전됩니다.",
              "<b>Battery disconnect while regenerating</b>: the regenerated power P can only charge the capacitor: "
              "½·C·(V₂² − V₁²) = ∫P dt, so V_lim is reached after t = C·(V_lim² − V₁²)/(2P). Torque must be removed (reaction "
              "time) or ASC applied before that; turning the inverter off alone keeps charging through the diodes when the "
              "back-EMF exceeds V_dc."))
NOTE_SAFE = lambda: (tr("<b>ASC (능동 단락)</b>: 상단 또는 하단 스위치 3개를 켜 권선을 단락합니다. 정상상태 전류는 고속에서 ψ/L_d에 가까워지고 "
                "제동 토크는 작습니다(저속에서는 큼). DC 링크로 가는 전력은 없습니다.<br>"
                "<b>Freewheel / 6SO</b>: 스위치 6개를 모두 끕니다. 역기전력 선간 peak가 V_dc보다 크면 다이오드 정류로 제어되지 않는 "
                "회생이 일어나 과전압·과충전 위험이 있습니다.<br>전환 과도 전류, 소자 SOA, 검출·승인은 평가하지 않습니다(스크리닝).",
                "<b>ASC</b>: three high- or low-side switches on, windings shorted; the steady current tends to ψ/L_d at high "
                "speed with small braking torque (large at low speed); no DC power.<br><b>Freewheel / 6SO</b>: all six off; if "
                "the back-EMF line-line peak exceeds V_dc the diodes rectify uncontrolled regeneration (overvoltage/overcharge "
                "risk).<br>Transients, device SOA, detection and approval are not evaluated (screening)."))

EXAMPLE_RULES = [{"rule_id": "PRJ-SR-01", "when": {"Vdc_below_V": 60}, "require": "FREEWHEEL",
                  "basis": "project safety concept: HVDC < 60 V -> force the freewheel path (project rule, not physics)"}]
ITEM_COLS = ("id", "from", "to", "owner", "min_ms", "nom_ms", "max_ms", "period_ms")
RULE_NUM = (("Vdc_below_V", "Vdc < [V]"), ("Vdc_above_V", "Vdc > [V]"), ("speed_above_rpm", "|n| > [rpm]"),
            ("speed_below_rpm", "|n| < [rpm]"))
REACTIONS = ("", "ASC", "FREEWHEEL")


class RulesTable(QTableWidget):
    """Project safe-reaction rules as a table (one row per rule; empty cells = condition not used)."""

    def __init__(self, rules: list, parent=None):
        heads = [tr("규칙 ID", "rule id")] + [h for _k, h in RULE_NUM] + [tr("HV 상태", "HV state"), tr("요구", "require"),
                                                                          tr("금지", "forbid"), tr("근거 (필수)", "basis (required)")]
        super().__init__(0, len(heads), parent)
        self.setHorizontalHeaderLabels(heads)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        for r in rules:
            self.add(r)

    def _combo(self, items, current):
        from PySide6.QtWidgets import QComboBox
        c = QComboBox()
        for label, data in items:
            c.addItem(label, data)
        for i in range(c.count()):
            if c.itemData(i) == current:
                c.setCurrentIndex(i)
        return c

    def add(self, rule: dict | None = None):
        rule = rule or {"rule_id": f"PRJ-{self.rowCount() + 1:02d}", "when": {}, "basis": ""}
        r = self.rowCount()
        self.insertRow(r)
        when = rule.get("when", {})
        self.setItem(r, 0, QTableWidgetItem(rule.get("rule_id", "")))
        for j, (key, _h) in enumerate(RULE_NUM, start=1):
            self.setItem(r, j, QTableWidgetItem("" if when.get(key) is None else f"{when[key]:g}"))
        k = len(RULE_NUM) + 1
        self.setCellWidget(r, k, self._combo([(tr("무관", "any"), None), (tr("배터리 연결", "battery connected"), "battery_connected"),
                                              (tr("배터리 분리", "battery disconnected"), "battery_disconnected")], when.get("hv_state")))
        self.setCellWidget(r, k + 1, self._combo([(x or "—", x or None) for x in REACTIONS], rule.get("require")))
        self.setCellWidget(r, k + 2, self._combo([(x or "—", x or None) for x in REACTIONS], rule.get("forbid")))
        self.setItem(r, k + 3, QTableWidgetItem(rule.get("basis", "")))

    def remove_selected(self):
        for row in sorted({i.row() for i in self.selectedIndexes()}, reverse=True):
            self.removeRow(row)

    def workspace_state(self) -> list:
        """The rules as typed: text cells, the three choices, the basis."""
        k = len(RULE_NUM) + 1
        return [{"cells": [(self.item(r, j).text() if self.item(r, j) else "") for j in range(k)],
                 "choices": [self.cellWidget(r, k + i).currentData() for i in range(3)],
                 "basis": self.item(r, k + 3).text() if self.item(r, k + 3) else ""} for r in range(self.rowCount())]

    def restore_workspace(self, rows: list) -> list:
        k = len(RULE_NUM) + 1
        problems = []
        self.setRowCount(0)
        for n, row in enumerate(rows or []):
            self.add({"rule_id": "", "when": {}, "basis": ""})
            r = self.rowCount() - 1
            for j, txt in enumerate((row.get("cells") or [])[:k]):
                self.setItem(r, j, QTableWidgetItem(str(txt)))
            for i, data in enumerate((row.get("choices") or [])[:3]):
                c = self.cellWidget(r, k + i)
                idx = next((q for q in range(c.count()) if c.itemData(q) == data), None)
                if idx is None:
                    problems.append(tr(f"규칙 {n + 1}: 선택 '{data}'이 현재 선택지에 없음", f"rule {n + 1}: choice "
                                                                               f"'{data}' is not offered now"))
                else:
                    c.setCurrentIndex(idx)
            self.setItem(r, k + 3, QTableWidgetItem(str(row.get("basis", ""))))
        return problems

    def rules(self) -> list:
        out = []
        k = len(RULE_NUM) + 1
        for r in range(self.rowCount()):
            rid = (self.item(r, 0).text() if self.item(r, 0) else "").strip()
            when = {}
            for j, (key, _h) in enumerate(RULE_NUM, start=1):
                txt = (self.item(r, j).text() if self.item(r, j) else "").strip()
                if txt:
                    when[key] = float(txt)
            hv = self.cellWidget(r, k).currentData()
            if hv:
                when["hv_state"] = hv
            rule = {"rule_id": rid, "when": when, "basis": (self.item(r, k + 3).text() if self.item(r, k + 3) else "").strip()}
            req, forb = self.cellWidget(r, k + 1).currentData(), self.cellWidget(r, k + 2).currentData()
            if req:
                rule["require"] = req
            if forb:
                rule["forbid"] = forb
            if rid or when or rule["basis"]:
                out.append(rule)
        return out


def _reasons(c: dict) -> str:
    return ", ".join(reason_label(r) for r in c.get("reasons") or []) or "—"


def _claim_line(c: dict) -> str:
    return f"{c['status']} · {_reasons(c)} · {c.get('detail', '')}"


def ms_(x):
    return None if x is None else x * 1e3


def _claim_rows(c: dict) -> list:
    """Claim as table rows: status + reasons, then one row per clause of the detail (long messages stay readable)."""
    rows = [(tr("판정", "claim"), f"{c['status']} · {_reasons(c)}")]
    rows += [(tr("근거", "detail") if i == 0 else "", part) for i, part in enumerate(p for p in (c.get("detail") or "").split("; ") if p)]
    rows += [(tr("한정", "qualifier"), q) for q in c.get("qualifiers") or []]
    return rows


def _emf_rows(res: dict) -> list:
    """Back-EMF / rectification screening of a discharge result (review F08b): risk, basis and the coupled estimate."""
    if "back_emf_ll_peak_V" not in res:
        return [(tr("역기전력", "back-EMF"), tr("회전 속도 미지정: 정지 가정 (정류 위험 미평가)",
                                                "no speed given: standstill assumed (rectification not assessed)"))]
    risk = res.get("rectification_risk")
    rows = [(tr("역기전력 선간 peak", "back-EMF line-line peak"),
             f"{fmt(res.get('back_emf_ll_peak_V'))} V · {res.get('back_emf_basis', '')}"),
            (tr("정류 위험", "rectification risk"),
             "UNKNOWN" if risk is None else (tr("있음: 다이오드가 링크를 충전 (RC 시간은 하한)",
                                                "YES: the diodes feed the link (the RC time is only a lower bound)")
                                             if risk else tr("없음 (역기전력 ≤ 목표 전압)", "no (back-EMF ≤ target voltage)"))),
            (tr("목표 전압을 넘지 않는 최고 속도", "highest speed with back-EMF ≤ target"),
             f"{fmt(res.get('max_speed_for_target_rpm'))} rpm")]
    est = res.get("rectified_link_screening")
    if est:
        rows += [(tr("정류 링크 전압 (스크리닝 추정)", "rectified link voltage (screening estimate)"),
                  f"{est['V_dc_V']:.4g} V · I_dc {est['I_dc_A']:.3g} A · P {est['P_W']:.3g} W · "
                  f"{tr('상전류', 'phase current')} {est['phase_current_A_peak']:.3g} A peak"),
                 (tr("추정 방법", "estimate method"), est["method"])]
    elif risk:
        rows.append((tr("정류 링크 전압", "rectified link voltage"),
                     tr("추정 불가 (일정 파라미터 모델 아님): 결합 모델 필요", "not estimated (not a constant-parameter model): "
                        "coupled model required")))
    return rows


class SafetyPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.tabs = QTabWidget()
        self.tabs.addTab(self._ftti_tab(), tr("FTTI 체인", "FTTI chain"))
        self.tabs.addTab(self._dclink_tab(), tr("DC 링크 방전·과전압", "DC link discharge · overvoltage"))
        self.tabs.addTab(self._safe_tab(), tr("안전 상태 (ASC / Freewheel)", "safe state (ASC / freewheel)"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(hint(tr("스크리닝 전용: 선언된 값과 축약 모델로 계산하며, 기능안전 승인·과도 해석·SOA 검증을 대신하지 않습니다.",
                              "Screening only: declared values and reduced-order models; not a substitute for FuSa approval, "
                              "transient simulation or SOA verification.")))
        lay.addWidget(self.tabs, 1)

    # ------------------------------------------------------------------ FTTI
    def _ftti_tab(self):
        w = QWidget()
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        v = QVBoxLayout(left)
        v.setContentsMargins(0, 0, 6, 0)
        ex = self.win.state.example("TIMING")
        g = QGroupBox(tr("체인 정의", "chain"))
        f = QFormLayout(g)
        self.f_id = QLineEdit(ex["chain_id"])
        self.f_fault = QLineEdit(ex["fault"])
        self.f_ftti = number(ex["ftti_ms"], 0.001, 1e6, "ms", 3)
        self.f_fdti = number(ex["fdti_budget_ms"], 0, 1e6, "ms", 3)
        self.f_frti = number(ex["frti_budget_ms"], 0, 1e6, "ms", 3)
        self.f_det = QLineEdit(ex["detection_event"])
        self.f_events = QLineEdit(", ".join(ex["events"]))
        self.f_safe = QLineEdit(ex.get("safe_event") or "")
        self.f_safe.setPlaceholderText(tr("비우면 마지막 이벤트", "empty = last event"))
        self.f_endpoint = combo([(tr("물리적 안전 상태 (전류·토크 소멸)", "physical safe state (current / torque extinguished)"),
                                  "physical_safe_state"),
                                 (tr("명령 발행 (게이트 차단 요청 등)", "command issued (e.g. gate-off request)"), "command_issued")],
                                ex.get("endpoint_kind", "physical_safe_state"))
        self.f_endpoint.setToolTip(tr("명령이 드라이버에 도달한 시점은 물리적 안전 상태가 아닙니다: 명령으로 끝나는 체인은 UNKNOWN입니다.",
                                      "a command reaching the driver is not the physical safe state: a chain that ends at a "
                                      "command is UNKNOWN"))
        self.f_attain = check(tr("최악값 동시 발생 선언", "maxima jointly attainable"), bool(ex.get("worst_case_attainable", False)),
                              tr("항목 최댓값들이 한 트레이스에서 함께 일어날 수 있다고 선언하면, 합이 FTTI를 넘을 때 INFEASIBLE "
                                 "(선언하지 않으면 독립 최댓값의 합은 상한일 뿐이므로 UNKNOWN)",
                                 "declares that the item maxima occur together in one trace: a sum above the FTTI is then "
                                 "INFEASIBLE (otherwise the sum of independent maxima is only a bound -> UNKNOWN)"))
        for lab, wd in (("chain id", self.f_id), (tr("고장", "fault"), self.f_fault), ("FTTI", self.f_ftti),
                        (tr("FDTI 예산", "FDTI budget"), self.f_fdti), (tr("FRTI 예산", "FRTI budget"), self.f_frti),
                        (tr("검출 이벤트", "detection event"), self.f_det), (tr("이벤트 순서", "event order"), self.f_events),
                        (tr("안전 종점 이벤트", "safe endpoint event"), self.f_safe),
                        (tr("종점 종류", "endpoint kind"), self.f_endpoint), ("", self.f_attain)):
            f.addRow(lab, wd)
        v.addWidget(g)
        g = QGroupBox(tr("지연 항목 (min / nom / max, 주기 항목은 1주기 추가)", "latency items"))
        gl = QVBoxLayout(g)
        self.items = QTableWidget(0, len(ITEM_COLS))
        self.items.setHorizontalHeaderLabels(list(ITEM_COLS))
        self.items.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.items.setSelectionBehavior(QAbstractItemView.SelectRows)
        for it in ex["items"]:
            self._add_item(it)
        gl.addWidget(self.items)
        row = QHBoxLayout()
        b1 = QPushButton(tr("행 추가", "add row"))
        b1.clicked.connect(lambda: self._add_item({}))
        b2 = QPushButton(tr("선택 행 삭제", "delete row"))
        b2.clicked.connect(lambda: [self.items.removeRow(r.row()) for r in sorted(self.items.selectionModel().selectedRows(), key=lambda r: -r.row())])
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        gl.addLayout(row)
        v.addWidget(g, 1)
        b = primary_button(tr("FTTI 분석", "analyse FTTI"))
        b.clicked.connect(self.run_ftti)
        v.addWidget(b)
        v.addWidget(ConceptNote(NOTE_FTTI()))
        split.addWidget(left)
        self.win.track_inputs("ftti", left)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.p_ftti = PlotPanel()
        self.t_ftti = KeyValueTable()
        rv.addWidget(self.p_ftti, 3)
        rv.addWidget(self.t_ftti, 1)
        self.ftti_tabs, self.i_ftti = with_reading(right, tr(
            "분석하면 해석이 표시됩니다 — 시간이 어느 항목에서 쓰이는지(선택 경로, 사건 순서), 감지·반응 분담, 예산 점검, 이중 계산·공백.",
            "Analyse to read the chain — where the time goes (chosen path, in event order), the detection / reaction "
            "split, the budget checks, double counts and gaps."))
        self.ftti_tabs.setTabText(1, tr("타임라인·표", "timeline · table"))
        split.addWidget(self.ftti_tabs)
        split.setSizes([520, 900])
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addWidget(split)
        return w

    def _dc_link_uF(self) -> float:
        p = self.win.state.project
        return float(p.data("dc_link")["C_uF"]) if p.has("dc_link") else 500.0

    def _project_rules(self) -> list:
        p = self.win.state.project
        return p.safe_state_rules() if p.has("safety") else EXAMPLE_RULES

    def load_chain(self, ex: dict):
        """An FTTI chain into the editor (the project's chain, or a file)."""
        self.f_id.setText(str(ex["chain_id"]))
        self.f_fault.setText(str(ex["fault"]))
        self.f_ftti.setValue(float(ex["ftti_ms"]))
        self.f_fdti.setValue(float(ex.get("fdti_budget_ms") or 0.0))
        self.f_frti.setValue(float(ex.get("frti_budget_ms") or 0.0))
        self.f_det.setText(str(ex.get("detection_event") or ""))
        self.f_events.setText(", ".join(ex["events"]))
        self.f_safe.setText(str(ex.get("safe_event") or ""))
        i = self.f_endpoint.findData(ex.get("endpoint_kind", "physical_safe_state"))
        if i >= 0:
            self.f_endpoint.setCurrentIndex(i)
        self.f_attain.setChecked(bool(ex.get("worst_case_attainable", False)))
        self.items.setRowCount(0)
        for it in ex["items"]:
            self._add_item(it)

    def apply_project(self, _project=None):
        """FTTI chain, safe-state rules and the DC-link capacitance from the active project."""
        self.load_chain(self.win.state.example("TIMING"))
        self.s_rules.setRowCount(0)
        for r in self._project_rules():
            self.s_rules.add(r)
        for w in (self.d_C, self.p_C, self.o_C):
            w.setValue(self._dc_link_uF())

    def _add_item(self, it: dict):
        r = self.items.rowCount()
        self.items.insertRow(r)
        for j, key in enumerate(ITEM_COLS):
            val = it.get(key)
            self.items.setItem(r, j, QTableWidgetItem("" if val is None else str(val)))

    def _timing_body(self) -> dict:
        items = []
        for r in range(self.items.rowCount()):
            d = {}
            for j, key in enumerate(ITEM_COLS):
                cell = self.items.item(r, j)
                txt = "" if cell is None else cell.text().strip()
                if txt == "":
                    continue
                d[key] = float(txt) if key.endswith("_ms") else txt
            if d.get("id"):
                items.append(d)
        return {"chain_id": self.f_id.text(), "fault": self.f_fault.text(), "ftti_ms": self.f_ftti.value(),
                "fdti_budget_ms": self.f_fdti.value() or None, "frti_budget_ms": self.f_frti.value() or None,
                "detection_event": self.f_det.text().strip() or None,
                "events": [e.strip() for e in self.f_events.text().split(",") if e.strip()], "items": items,
                "safe_event": self.f_safe.text().strip() or None, "endpoint_kind": self.f_endpoint.currentData(),
                "worst_case_attainable": self.f_attain.isChecked()}

    def run_ftti(self):
        try:
            body = self._timing_body()
            res = api.timing(body)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("FTTI 입력 오류", "FTTI input error"), str(exc))
            return
        self.win.note_result("ftti", body, res)
        tl = SF.ftti_timeline(res)
        self.p_ftti.draw(F.fig_ftti, tl, title=f"{res['chain_id']} · {res['fault']}", name="ftti",
                         csv=lambda: {k: [b[k] for b in tl["bars"]] for k in ("id", "owner", "from", "to", "start_worst_s",
                                                                              "worst_s", "min_s", "max_s", "counted")})
        ms = ms_
        rows = [(tr("판정", "claim"), _claim_line(res["claim"])),
                (tr("종점", "endpoint"), f"{res['safe_event']} · {res['endpoint_kind']}"),
                (tr("보장 상한 경로", "chosen (tightest) path"),
                 " → ".join(res.get("chosen_path") or []) + f"  ({res.get('paths', 0)} {tr('개 경로 중', 'path(s) considered')})"),
                (tr("최악 / 공칭 / 최선 [ms]", "worst / nominal / best [ms]"),
                 f"{fmt(ms(res['worst_s']))} / {fmt(ms(res.get('nominal_s')))} / {fmt(ms(res['best_s']))}"),
                (tr("여유 [ms]", "margin [ms]"), fmt(ms(res.get("margin_s")))),
                (tr("FDTI / FRTI 최악 (선택 경로) [ms]", "FDTI / FRTI worst on the chosen path [ms]"),
                 f"{fmt(ms(res.get('fdti_worst_s')))} / {fmt(ms(res.get('frti_worst_s')))}")]
        rows += [(tr("중복 예산", "duplicate budget"), d["message"]) for d in res["duplicate_budgets"]]
        rows += [(tr("공백", "gap"), str(g)) for g in res.get("gaps", [])]
        word = lambda ok: "UNKNOWN" if ok is None else ("OK" if ok else "NG")
        for c in res.get("budget_checks", []):
            if "ftti_s" in c:
                txt = f"{word(c['ok'])} · {fmt(ms(c['allocated_s']))} ms vs FTTI {fmt(ms(c['ftti_s']))} ms"
            else:
                txt = (f"{word(c['ok'])} · {tr('최악', 'worst')} {fmt(ms(c.get('worst_s')))} ms / "
                       f"{tr('할당', 'allocated')} {fmt(ms(c['allocated_s']))} ms")
                if c.get("note"):
                    txt += f" · {c['note']}"
            rows.append((c["budget"], txt))
        rows += [(tr("주석", "note"), n) for n in res.get("notes", [])]
        self.t_ftti.set_rows(rows)
        self.i_ftti.read("ftti", "FTTI", ftti_insight, res)

    # --------------------------------------------------------------- DC link
    def _dclink_tab(self):
        w = QWidget()
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        v = QVBoxLayout(left)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("능동 방전 (배터리 분리 후 RC)", "active discharge (RC, battery disconnected)"))
        f = QFormLayout(g)
        self.d_C = number(self._dc_link_uF(), 0.1, 1e6, "µF", 1, 10)
        self.d_V0 = number(600, 1, 5000, "V", 1, 10)
        self.d_Vf = number(60, 0.1, 5000, "V", 1, 5)
        self.d_t = number(2, 0.0001, 1e5, "s", 4, 0.1)
        self.d_R_on = check(tr("저항 지정", "given R"), False)
        self.d_R = number(1000, 0.001, 1e9, "Ω", 3, 10)
        self.d_n_on = check(tr("회전 중 (역기전력 확인)", "spinning (check back-EMF)"), True)
        self.d_n = number(500, 0, 30000, "rpm", 0, 100)
        for lab, wd in (("C", self.d_C), ("V0", self.d_V0), (tr("목표 V", "target V"), self.d_Vf), (tr("허용 시간", "allowed time"), self.d_t),
                        (self.d_R_on, self.d_R), (self.d_n_on, self.d_n)):
            f.addRow(lab, wd)
        b = primary_button(tr("방전 계산", "compute discharge"))
        b.clicked.connect(self.run_discharge)
        f.addRow(b)
        v.addWidget(g)
        dis_box = g
        v.addWidget(ConceptNote(NOTE_DISCHARGE()))
        g = QGroupBox(tr("패시브 방전 (상시 연결 블리더 저항)", "passive discharge (always-connected bleeder)"))
        f = QFormLayout(g)
        self.p_C = number(self._dc_link_uF(), 0.1, 1e6, "µF", 1, 10)
        self.p_V0 = number(600, 1, 5000, "V", 1, 10)
        self.p_Vf = number(60, 0.1, 5000, "V", 1, 5)
        self.p_t = number(120, 0.001, 1e6, "s", 3, 5, tr("요구 방전 시간 (요구값을 입력)", "required discharge time"))
        self.p_R_on = check(tr("R_p 지정", "given R_p"), True, tr("해제하면 시간 조건을 만족하는 최대 R_p를 사용", "off: the largest R_p meeting the time"))
        self.p_R = number(90, 0.001, 1e6, "kΩ", 3, 1)
        self.p_Vnom = number(400, 1, 5000, "V", 1, 10, tr("상시 손실 계산용 정격 링크 전압", "nominal link voltage for the continuous loss"))
        self.p_Vmax = number(600, 1, 5000, "V", 1, 10, tr("최대 링크 전압 (저항 손실·정격 확인)", "maximum link voltage (loss/rating check)"))
        self.p_P_on = check(tr("허용 상시 손실", "allowed continuous loss"), True, tr("저항 전력 정격(디레이팅 반영) 또는 허용 대기 손실",
                                                                                     "resistor rating (derated) or allowed standby loss"))
        self.p_P = number(5, 0.001, 1e5, "W", 3, 0.5)
        self.p_a_on = check(tr("능동 저항 병렬", "active resistor in parallel"), True)
        self.p_Ra = number(1737, 0.001, 1e9, "Ω", 1, 10)
        self.p_n_on = check(tr("회전 중 (역기전력 확인)", "spinning (check back-EMF)"), False)
        self.p_n = number(300, 0, 30000, "rpm", 0, 100)
        for lab, wd in (("C", self.p_C), ("V0", self.p_V0), (tr("목표 V", "target V"), self.p_Vf), (tr("요구 시간", "required time"), self.p_t),
                        (self.p_R_on, self.p_R), (tr("정격 전압", "nominal V"), self.p_Vnom), (tr("최대 전압", "maximum V"), self.p_Vmax),
                        (self.p_P_on, self.p_P), (self.p_a_on, self.p_Ra), (self.p_n_on, self.p_n)):
            f.addRow(lab, wd)
        b = primary_button(tr("패시브 방전 계산", "compute passive discharge"))
        b.clicked.connect(self.run_passive)
        f.addRow(b)
        v.addWidget(g)
        pas_box = g
        v.addWidget(ConceptNote(NOTE_PASSIVE()))
        g = QGroupBox(tr("회생 중 배터리 차단 과전압", "battery disconnect during regen"))
        f = QFormLayout(g)
        self.o_C = number(self._dc_link_uF(), 0.1, 1e6, "µF", 1, 10)
        self.o_V1 = number(600, 1, 5000, "V", 1, 10)
        self.o_Vlim = number(850, 1, 5000, "V", 1, 10)
        self.o_n = number(12000, 0, 30000, "rpm", 0, 100)
        self.o_T = number(-80, -5000, 0, "N·m", 2, 5)
        self.o_react = number(2, 0, 1e4, "ms", 3, 0.1)
        self.o_profile = combo([(tr("일정 전력", "constant power"), "constant"), (tr("선형 감소", "linear ramp-down"), "linear_ramp_down")])
        # what removes the inflow (engineering review 2 of 63a2b61, F-09): above the uncontrolled-generation speed a
        # freewheel reaction charges the link towards the back-EMF peak, so the path must be stated
        self.o_reaction = combo([(tr("미지정", "unspecified"), "unspecified"), (tr("ASC (능동 단락)", "ASC (active short)"),
                                                                              "asc"),
                                 (tr("프리휠 (전 스위치 개방)", "freewheel (all switches open)"), "freewheel")])
        for lab, wd in (("C", self.o_C), ("V1", self.o_V1), (tr("전압 한계", "voltage limit"), self.o_Vlim), (tr("속도", "speed"), self.o_n),
                        (tr("회생 토크", "regen torque"), self.o_T), (tr("반응 시간", "reaction time"), self.o_react),
                        (tr("전력 프로파일", "power profile"), self.o_profile),
                        (tr("반응 경로", "reaction path"), self.o_reaction)):
            f.addRow(lab, wd)
        b = primary_button(tr("과전압 계산", "compute overvoltage"))
        b.clicked.connect(self.run_overvoltage)
        f.addRow(b)
        v.addWidget(g)
        ov_box = g
        v.addWidget(ConceptNote(NOTE_OV()))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(left)
        split.addWidget(sc)
        self.win.track_inputs("discharge", dis_box)
        self.win.track_inputs("passive", pas_box)
        self.win.track_inputs("overvoltage", ov_box)
        right = QTabWidget()
        self.dc_tabs = right
        for key, label in (("dis", tr("능동 방전", "active discharge")), ("pas", tr("패시브 방전", "passive discharge")),
                           ("ov", tr("회생 중 배터리 차단 (과전압)", "battery disconnect while regenerating"))):
            w2 = QWidget()
            l2 = QVBoxLayout(w2)
            l2.setContentsMargins(0, 0, 0, 0)
            sch = PlotPanel(min_height=240)
            plot = PlotPanel(min_height=260)
            table = KeyValueTable()
            table.setMinimumHeight(110)
            l2.addWidget(sch, 5)
            l2.addWidget(plot, 6)
            l2.addWidget(table, 3)
            setattr(self, f"s_{key}", sch)
            setattr(self, f"p_{key}", plot)
            setattr(self, f"t_{key}", table)
            right.addTab(w2, label)
        self.i_dc = reading_tab(right, tr(
            "계산하면 해석이 표시됩니다 — 능동 방전: RC 메커니즘과 저항 부담, 회전 중 역기전력 · 패시브: 시간·손실 조건의 설계 창과 맞바꿈 · "
            "과전압: 에너지 수지, 허용 반응 시간, 필요한 커패시턴스.",
            "Run to read the result — active discharge: the RC mechanism and the resistor stress, the back-EMF while "
            "spinning · passive: the time / loss design window and its trade · overvoltage: the energy balance, the "
            "allowed reaction time, the capacitance it would take."))
        split.addWidget(right)
        split.setSizes([380, 1000])
        self._default_dclink_schematics()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addWidget(split)
        return w

    def run_passive(self):
        s = self.win.state
        body = s.body(C_uF=self.p_C.value(), V0_V=self.p_V0.value(), Vf_V=self.p_Vf.value(), t_target_s=self.p_t.value(),
                      R_kohm=self.p_R.value() if self.p_R_on.isChecked() else None, V_nom_V=self.p_Vnom.value(),
                      V_max_V=self.p_Vmax.value(), P_allow_W=self.p_P.value() if self.p_P_on.isChecked() else None,
                      active_R_ohm=self.p_Ra.value() if self.p_a_on.isChecked() else None,
                      speed_rpm=self.p_n.value() if self.p_n_on.isChecked() else None)
        try:
            res = api.passive(body)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.win.note_result("passive", body, res)
        cur = SF.passive_curves(res)
        win = SF.passive_window(res)
        self.p_pas.draw(F.fig_passive_discharge, res, cur, win, title=tr("패시브 방전 (블리더)", "passive discharge (bleeder)"),
                        name="passive_discharge",
                        csv=lambda: {"t_s": cur["t_s"], "V": cur["V"], "R_ohm": win["R_ohm"], "t_reach_s": win["t_reach_s"],
                                     "P_cont_max_W": win["P_cont_max_W"]})
        bemf = res.get("back_emf_ll_peak_V")
        rect = bool(bemf and bemf > res["Vf_V"])
        note = tr(f"{res['claim']['status']}: t = {res['t_reach_s']:.4g} s · 상시 손실 {res['P_cont_nom_W']:.3g} W @ {res['V_nom_V']:g} V · "
                  f"{res['P_cont_max_W']:.3g} W @ {res['V_max_V']:g} V",
                  f"{res['claim']['status']}: t = {res['t_reach_s']:.4g} s · continuous loss {res['P_cont_nom_W']:.3g} W @ {res['V_nom_V']:g} V · "
                  f"{res['P_cont_max_W']:.3g} W @ {res['V_max_V']:g} V")
        self.s_pas.draw(SC.fig_dclink_schematic, "passive",
                        {"C_uF": res["C_F"] * 1e6, "V0_V": res["V0_V"], "Vf_V": res["Vf_V"], "R_ohm": res["R_used_ohm"],
                         "rectifying": rect, "spinning": self.p_n_on.isChecked(),
                         "motor_label": (tr(f"역기전력 {bemf:.0f} V (선간 peak)", f"back-EMF {bemf:.0f} V (LL peak)") if bemf else None),
                         "note": note},
                        title=tr("패시브 방전 회로 (R_p 상시 연결)", "passive discharge circuit (R_p always connected)"),
                        name="passive_circuit")
        wa = res.get("with_active") or {}
        rows = _claim_rows(res["claim"])
        rows += [(tr("R_p 사용 / 허용 범위", "R_p used / window"),
                  f"{res['R_used_ohm'] / 1e3:.4g} kΩ · [{fmt(None if res.get('R_min_ohm') is None else res['R_min_ohm'] / 1e3)}, "
                  f"{fmt(None if res.get('R_max_ohm') is None else res['R_max_ohm'] / 1e3)}] kΩ"),
                 (tr("도달 시간 / 요구", "time to target / required"), f"{res['t_reach_s']:.4g} s / {res['t_target_s']:g} s"),
                 (tr("상시 손실 (정격 / 최대 V)", "continuous loss (nominal / max V)"),
                  f"{res['P_cont_nom_W']:.3g} W / {res['P_cont_max_W']:.3g} W")]
        if wa:
            rows.append((tr("능동 R_a 병렬 시", "with active R_a in parallel"), f"{wa['t_reach_s']:.4g} s"))
        self.t_pas.set_rows(rows + _emf_rows(res))
        self.i_dc.read("passive", tr("패시브 방전", "passive discharge"), passive_insight, res)

    def _default_dclink_schematics(self):
        self.s_dis.draw(SC.fig_dclink_schematic, "discharge",
                        {"C_uF": self.d_C.value(), "V0_V": self.d_V0.value(), "Vf_V": self.d_Vf.value(), "R_ohm": None,
                         "note": tr("배터리 릴레이 개방 → 방전 스위치 ON → 커패시터 에너지를 저항으로 소모",
                                    "contactors open → discharge switch on → capacitor energy dissipated in R")},
                        title=tr("능동 방전 회로", "active discharge circuit"), name="discharge_circuit")
        self.s_pas.draw(SC.fig_dclink_schematic, "passive",
                        {"C_uF": self.p_C.value(), "V0_V": self.p_V0.value(), "Vf_V": self.p_Vf.value(), "R_ohm": None,
                         "note": tr("릴레이 개방 → 스위치 없이 연결된 R_p가 항상 방전 (능동 방전의 백업)",
                                    "contactors open → the always-connected R_p discharges (backup of the active discharge)")},
                        title=tr("패시브 방전 회로", "passive discharge circuit"), name="passive_circuit")
        self.s_ov.draw(SC.fig_dclink_schematic, "overvoltage",
                       {"C_uF": self.o_C.value(), "V1_V": self.o_V1.value(), "V_limit_V": self.o_Vlim.value(), "P_in_W": None,
                        "note": tr("회생 중 릴레이 개방 → 회생 전력이 커패시터로만 유입", "contactors open while regenerating → power only into C")},
                       title=tr("회생 중 배터리 차단", "battery disconnect while regenerating"), name="overvoltage_circuit")

    def run_discharge(self):
        s = self.win.state
        body = s.body(C_uF=self.d_C.value(), V0_V=self.d_V0.value(), Vf_V=self.d_Vf.value(), t_target_s=self.d_t.value(),
                      R_ohm=self.d_R.value() if self.d_R_on.isChecked() else None,
                      speed_rpm=self.d_n.value() if self.d_n_on.isChecked() else None)
        try:
            res = api.discharge(body)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.win.note_result("discharge", body, res)
        cur = SF.discharge_curve(res)
        self.p_dis.draw(F.fig_discharge, res, cur, title=tr("능동 방전", "active discharge"), name="discharge",
                        csv=lambda: {"t_s": cur["t_s"], "V": cur["V"], "i_A": cur["i_A"], "p_W": cur["p_W"]})
        n = self.d_n.value() if self.d_n_on.isChecked() else 0.0
        bemf = res.get("back_emf_ll_peak_V")
        rect = bool(bemf and bemf > res["Vf_V"])
        lab = tr(f"n = {n:.0f} rpm · 역기전력 {bemf:.0f} V (선간 peak)", f"n = {n:.0f} rpm · back-EMF {bemf:.0f} V (LL peak)") if bemf else \
            tr("정지", "standstill")
        self.s_dis.draw(SC.fig_dclink_schematic, "discharge",
                        {"C_uF": res["C_F"] * 1e6, "V0_V": res["V0_V"], "Vf_V": res["Vf_V"], "R_ohm": res["R_used_ohm"],
                         "rectifying": rect, "spinning": n > 0, "motor_label": lab,
                         "note": (tr(f"{res['claim']['status']}: 역기전력 {bemf:.0f} V > 목표 {res['Vf_V']:g} V → 다이오드 정류가 방전을 막음",
                                     f"{res['claim']['status']}: back-EMF {bemf:.0f} V > target {res['Vf_V']:g} V → rectification blocks the discharge")
                                  if rect else f"{res['claim']['status']}: t = {res['t_reach_s']:.4g} s · I₀ = {res['I0_A']:.3g} A · P₀ = {res['P0_W']:.4g} W")},
                        title=tr("능동 방전 회로", "active discharge circuit"), name="discharge_circuit")
        rows = _claim_rows(res["claim"])
        rows += [(tr("R 사용 / 최대", "R used / maximum"), f"{res['R_used_ohm']:.4g} Ω / {res['R_max_ohm']:.4g} Ω"),
                 (tr("도달 시간 / 허용", "time to target / allowed"), f"{res['t_reach_s']:.4g} s / {res['t_target_s']:g} s"),
                 (tr("초기 전류 · 전력 · 저항 에너지", "initial current · power · resistor energy"),
                  f"{res['I0_A']:.3g} A · {res['P0_W']:.4g} W · {res['E_R_J']:.4g} J")]
        self.t_dis.set_rows(rows + _emf_rows(res))
        self.i_dc.read("discharge", tr("능동 방전", "active discharge"), discharge_insight, res)

    def run_overvoltage(self):
        s = self.win.state
        body = s.body(C_uF=self.o_C.value(), V1_V=self.o_V1.value(), V_limit_V=self.o_Vlim.value(), speed_rpm=self.o_n.value(),
                      torque_Nm=self.o_T.value(), reaction_time_ms=self.o_react.value(), profile=self.o_profile.currentData(),
                      reaction=self.o_reaction.currentData())
        try:
            res = api.overvoltage(body)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.win.note_result("overvoltage", body, res)
        cur = SF.overvoltage_curve(res)
        op = res.get("regen_operating_point") or {}
        title = tr("회생 중 배터리 차단", "battery disconnect while regenerating")
        if op:
            title += f" · P_dc = {op['Pdc_W'] / 1e3:.2f} kW (id {op['id_A']:.1f} A, iq {op['iq_A']:.1f} A)"
        self.p_ov.draw(F.fig_overvoltage, res, cur, title=title, name="overvoltage",
                       csv=lambda: {"t_s": cur["t_s"], "V": cur["V"], "E_J": cur["E_J"]})
        self.s_ov.draw(SC.fig_dclink_schematic, "overvoltage",
                       {"C_uF": res["C_F"] * 1e6, "V1_V": res["V1_V"], "V_limit_V": res["V_limit_V"], "P_in_W": res["P_in_W"],
                        "motor_label": f"{self.o_n.value():.0f} rpm · {self.o_T.value():g} N·m",
                        "note": tr(f"dV/dt = P/(C·V₁) = {res['dVdt_initial_V_per_s'] / 1e3:.0f} V/ms · 허용 반응 {res['max_reaction_time_s'] * 1e3:.3g} ms",
                                   f"dV/dt = P/(C·V₁) = {res['dVdt_initial_V_per_s'] / 1e3:.0f} V/ms · allowed reaction {res['max_reaction_time_s'] * 1e3:.3g} ms")},
                       title=tr("회생 중 배터리 차단", "battery disconnect while regenerating"), name="overvoltage_circuit")
        rows = _claim_rows(res["claim"])
        rows += [(tr("유입 전력 / 에너지", "power / energy in"), f"{res['P_in_W'] / 1e3:.4g} kW / {res['energy_in_J']:.4g} J"),
                 (tr("최고 전압 / 한계", "peak / limit"), f"{res['V_peak_V']:.4g} V / {res['V_limit_V']:g} V"
                  + ("" if res.get("V_peak_bound_V") is None else
                     tr(f" (상한 {res['V_peak_bound_V']:.4g} V: 한계 전압에서의 유입 전력)",
                        f" (bound {res['V_peak_bound_V']:.4g} V: inflow at the limit voltage)"))),
                 (tr("허용 반응 시간 / 선언", "allowed / declared reaction time"),
                  f"{fmt(ms_(res.get('max_reaction_time_s')))} ms / {fmt(ms_(res.get('reaction_time_s')))} ms")]
        if op:
            rows.append((tr("회생 동작점", "regen operating point"),
                         f"id {op['id_A']:.1f} A · iq {op['iq_A']:.1f} A · P_dc {op['Pdc_W'] / 1e3:.2f} kW"))
        if res.get("back_emf_ll_peak_V") is not None:
            rows.append((tr("역기전력 선간 peak", "back-EMF line-line peak"), f"{res['back_emf_ll_peak_V']:.4g} V"))
        rows += [(tr("주석", "note"), n) for n in res.get("notes", [])]
        self.t_ov.set_rows(rows)
        self.i_dc.read("overvoltage", tr("회생 중 배터리 차단", "battery disconnect while regenerating"), overvoltage_insight, res)

    # ------------------------------------------------------------ safe state
    def _safe_tab(self):
        w = QWidget()
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        v = QVBoxLayout(left)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("조건", "conditions"))
        f = QFormLayout(g)
        self.s_n = number(12000, 0, 30000, "rpm", 0, 100)
        self.s_vdc = number(600, 1, 5000, "V", 1, 10)
        self.s_hv = combo([(tr("배터리 연결", "battery connected"), "battery_connected"),
                           (tr("배터리 분리", "battery disconnected"), "battery_disconnected")])
        self.s_dev = number(0, 0, 1e5, "V", 0, 50, tr("0 = 미지정", "0 = not given"), special=tr("미지정", "not given"))
        self.s_link = number(0, 0, 1e5, "V", 0, 50, tr("0 = 미지정", "0 = not given"), special=tr("미지정", "not given"))
        for lab, wd in ((tr("속도", "speed"), self.s_n), ("Vdc", self.s_vdc), (tr("HV 상태", "HV state"), self.s_hv),
                        (tr("소자 정격 전압", "device rating"), self.s_dev), (tr("DC 링크 한계", "DC-link limit"), self.s_link)):
            f.addRow(lab, wd)
        v.addWidget(g)
        g = QGroupBox(tr("프로젝트 규칙 (물리 법칙이 아님)", "project rules (not physics)"))
        gl = QVBoxLayout(g)
        self.s_rules = RulesTable(self._project_rules())
        gl.addWidget(self.s_rules)
        row = QHBoxLayout()
        b1 = QPushButton(tr("규칙 추가", "add rule"))
        b1.clicked.connect(lambda: self.s_rules.add())
        b2 = QPushButton(tr("선택 규칙 삭제", "delete rule"))
        b2.clicked.connect(self.s_rules.remove_selected)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        gl.addLayout(row)
        gl.addWidget(hint(tr("조건 칸이 비어 있으면 그 조건은 쓰지 않습니다. 모든 조건이 맞을 때 규칙이 적용되며, 결과는 물리 판정과 "
                             "별도 계층(프로젝트 규칙)으로 표시됩니다.",
                             "Empty condition cells are not used; a rule applies when all its conditions hold and is reported "
                             "as a separate project-rule layer, not physics.")))
        v.addWidget(g, 1)
        b = primary_button(tr("안전 상태 스크리닝", "screen safe states"))
        b.clicked.connect(self.run_safe)
        v.addWidget(b)
        v.addWidget(ConceptNote(NOTE_SAFE()))
        split.addWidget(left)
        self.win.track_inputs("safe_state", left)
        right = QTabWidget()
        self.s_safe = PlotPanel(min_height=300)
        self.p_safe = PlotPanel()
        self.t_safe = KeyValueTable()
        right.addTab(self.s_safe, tr("회로 비교 (ASC / Freewheel)", "circuits (ASC / freewheel)"))
        right.addTab(self.p_safe, tr("속도에 따른 전류·토크·역기전력", "current, torque, back-EMF vs speed"))
        right.addTab(self.t_safe, tr("판정 표", "screening table"))
        self.i_safe = reading_tab(right, tr(
            "스크리닝하면 해석이 표시됩니다 — ASC 정상 전류·제동 토크·동손, freewheel의 정류 시작 속도와 위험, 프로젝트 규칙.",
            "Screen to read the candidates — the ASC steady current, braking torque and copper loss, the freewheel "
            "rectification onset and its risk, the project rules."))
        self.safe_tabs = right
        split.addWidget(right)
        split.setSizes([420, 1000])
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addWidget(split)
        return w

    def run_safe(self):
        s = self.win.state
        try:
            rules = self.s_rules.rules()
            body = s.body(speed_rpm=self.s_n.value(), Vdc_V=self.s_vdc.value(), hv_state=self.s_hv.currentData(),
                          device_voltage_rating_V=self.s_dev.value() or None, dc_link_limit_V=self.s_link.value() or None,
                          rules=rules)
            res = api.safe_state(body)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.win.note_result("safe_state", body, res)
        asc = SF.asc_vs_speed(s.drive, self.s_vdc.value())
        n, vdc = self.s_n.value(), self.s_vdc.value()
        bemf = back_emf_ll_peak(s.drive, n)
        a = res.get("asc_detail") or {}
        onset = speed_for_back_emf(s.drive, vdc)
        rect = bool(bemf and bemf > vdc)
        self.s_safe.draw(SC.fig_safe_state_schematic,
                         {"Vdc_V": vdc, "rectifying": rect,
                          "asc_label": (f"|i| {a['i_peak_A']:.0f} A · T {a['Tshaft_Nm']:.1f} N·m" if a.get("evaluable") else "UNKNOWN"),
                          "fw_label": (f"V̂_LL {bemf:.0f} V {'>' if rect else '≤'} Vdc" if bemf else "UNKNOWN"),
                          "fw_note": (tr(f"역기전력 > Vdc (n > {onset:.0f} rpm): 다이오드 정류로 제어되지 않는 충전",
                                         f"back-EMF > Vdc (n > {onset:.0f} rpm): uncontrolled rectified charging") if rect else
                                      tr("역기전력 ≤ Vdc: 정상상태 전류 없음", "back-EMF ≤ Vdc: no steady-state current"))},
                         title=tr(f"안전 상태 후보 @ {n:.0f} rpm, Vdc {vdc:.0f} V", f"safe-state candidates @ {n:.0f} rpm, Vdc {vdc:.0f} V"),
                         name="safe_state_circuits")
        self.p_safe.draw(F.fig_safe_state, asc, self.s_n.value(), name="safe_state",
                         csv=lambda: {"speed_rpm": asc["speeds"], "asc_i_peak_A": asc["i_peak_A"],
                                      "asc_T_shaft_Nm": asc["Tshaft_Nm"], "back_emf_ll_peak_V": asc["back_emf_ll_peak_V"]})
        rows = [(tr("판정", "claim"), _claim_line(res["claim"]))]
        for c in res["candidates"]:
            for k, val in c.items():
                if k != "candidate":
                    rows.append((f"{c['candidate']} · {k}", fmt(val)))
        for r in res.get("project_rules", []):
            rows.append((f"{tr('프로젝트 규칙', 'project rule')} {r['rule_id']}",
                         f"{tr('적용됨', 'APPLIES') if r['applies'] else tr('조건 불일치', 'not applicable')} · "
                         f"{tr('요구', 'require')} {r.get('require') or '—'} · {tr('금지', 'forbid')} {r.get('forbid') or '—'} · "
                         f"{r['basis']} ({r['kind']})"))
        rows += [(tr("주석", "note"), n) for n in res.get("notes", [])]
        self.t_safe.set_rows(rows)
        self.i_safe.read("safe_state", tr("안전 상태", "safe state"), safe_state_insight, res)

    def redraw(self):
        for p in (self.p_ftti, self.p_dis, self.p_ov, self.p_safe, self.s_dis, self.s_ov, self.s_safe, self.p_pas, self.s_pas,
                  self.i_ftti, self.i_dc, self.i_safe):
            p.redraw()
