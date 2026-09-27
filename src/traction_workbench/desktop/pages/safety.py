"""Safety screening: FTTI chain, DC-link discharge / overvoltage, safe-state (ASC / freewheel) comparison."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...plots import figures as F
from ...viz import safety as SF
from ..widgets import KeyValueTable, PlotPanel, check, combo, error_box, fmt, hint, number, primary_button

EXAMPLE_RULES = [{"rule_id": "PRJ-SR-01", "when": {"Vdc_below_V": 60}, "require": "FREEWHEEL",
                  "basis": "project safety concept: HVDC < 60 V -> force the freewheel path (customer/project rule, not physics)"}]
ITEM_COLS = ("id", "from", "to", "owner", "min_ms", "nom_ms", "max_ms", "period_ms")


def _claim_line(c: dict) -> str:
    return f"{c['status']} · {', '.join(c.get('reasons') or []) or '—'} · {c.get('detail', '')}"


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
        ex = api.EXAMPLE_TIMING
        g = QGroupBox(tr("체인 정의", "chain"))
        f = QFormLayout(g)
        self.f_id = QLineEdit(ex["chain_id"])
        self.f_fault = QLineEdit(ex["fault"])
        self.f_ftti = number(ex["ftti_ms"], 0.001, 1e6, "ms", 3)
        self.f_fdti = number(ex["fdti_budget_ms"], 0, 1e6, "ms", 3)
        self.f_frti = number(ex["frti_budget_ms"], 0, 1e6, "ms", 3)
        self.f_det = QLineEdit(ex["detection_event"])
        self.f_events = QLineEdit(", ".join(ex["events"]))
        for lab, wd in (("chain id", self.f_id), (tr("고장", "fault"), self.f_fault), ("FTTI", self.f_ftti),
                        (tr("FDTI 예산", "FDTI budget"), self.f_fdti), (tr("FRTI 예산", "FRTI budget"), self.f_frti),
                        (tr("검출 이벤트", "detection event"), self.f_det), (tr("이벤트 순서", "event order"), self.f_events)):
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
        split.addWidget(left)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.p_ftti = PlotPanel()
        self.t_ftti = KeyValueTable()
        rv.addWidget(self.p_ftti, 3)
        rv.addWidget(self.t_ftti, 1)
        split.addWidget(right)
        split.setSizes([520, 900])
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addWidget(split)
        return w

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
                "events": [e.strip() for e in self.f_events.text().split(",") if e.strip()], "items": items}

    def run_ftti(self):
        try:
            res = api.timing(self._timing_body())
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("FTTI 입력 오류", "FTTI input error"), str(exc))
            return
        tl = SF.ftti_timeline(res)
        self.p_ftti.draw(F.fig_ftti, tl, title=f"{res['chain_id']} · {res['fault']}", name="ftti",
                         csv=lambda: {k: [b[k] for b in tl["bars"]] for k in ("id", "owner", "from", "to", "start_worst_s",
                                                                              "worst_s", "min_s", "max_s", "counted")})
        rows = [(tr("판정", "claim"), _claim_line(res["claim"])),
                (tr("최악 / 최선 [ms]", "worst / best [ms]"), f"{res['worst_s'] * 1e3:.4g} / {res['best_s'] * 1e3:.4g}"),
                (tr("여유 [ms]", "margin [ms]"), fmt(None if res.get("margin_s") is None else res["margin_s"] * 1e3))]
        rows += [(tr("중복 예산", "duplicate budget"), d["message"]) for d in res["duplicate_budgets"]]
        rows += [(tr("공백", "gap"), str(g)) for g in res.get("gaps", [])]
        rows += [(c["budget"], f"{'OK' if c['ok'] else 'NG'} · {c}") for c in res.get("budget_checks", [])]
        rows += [(tr("주석", "note"), n) for n in res.get("notes", [])]
        self.t_ftti.set_rows(rows)

    # --------------------------------------------------------------- DC link
    def _dclink_tab(self):
        w = QWidget()
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        v = QVBoxLayout(left)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("능동 방전 (배터리 분리 후 RC)", "active discharge (RC, battery disconnected)"))
        f = QFormLayout(g)
        self.d_C = number(500, 0.1, 1e6, "µF", 1, 10)
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
        g = QGroupBox(tr("회생 중 배터리 차단 과전압", "battery disconnect during regen"))
        f = QFormLayout(g)
        self.o_C = number(500, 0.1, 1e6, "µF", 1, 10)
        self.o_V1 = number(600, 1, 5000, "V", 1, 10)
        self.o_Vlim = number(850, 1, 5000, "V", 1, 10)
        self.o_n = number(12000, 0, 30000, "rpm", 0, 100)
        self.o_T = number(-80, -5000, 0, "N·m", 2, 5)
        self.o_react = number(2, 0, 1e4, "ms", 3, 0.1)
        self.o_profile = combo([(tr("일정 전력", "constant power"), "constant"), (tr("선형 감소", "linear ramp-down"), "linear_ramp_down")])
        for lab, wd in (("C", self.o_C), ("V1", self.o_V1), (tr("전압 한계", "voltage limit"), self.o_Vlim), (tr("속도", "speed"), self.o_n),
                        (tr("회생 토크", "regen torque"), self.o_T), (tr("반응 시간", "reaction time"), self.o_react),
                        (tr("전력 프로파일", "power profile"), self.o_profile)):
            f.addRow(lab, wd)
        b = primary_button(tr("과전압 계산", "compute overvoltage"))
        b.clicked.connect(self.run_overvoltage)
        f.addRow(b)
        v.addWidget(g)
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(left)
        split.addWidget(sc)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.p_dis = PlotPanel(min_height=260)
        self.p_ov = PlotPanel(min_height=260)
        rv.addWidget(self.p_dis, 1)
        rv.addWidget(self.p_ov, 1)
        split.addWidget(right)
        split.setSizes([380, 1000])
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addWidget(split)
        return w

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
        cur = SF.discharge_curve(res)
        self.p_dis.draw(F.fig_discharge, res, cur, title=tr("능동 방전", "active discharge"), name="discharge",
                        csv=lambda: {"t_s": cur["t_s"], "V": cur["V"], "i_A": cur["i_A"], "p_W": cur["p_W"]})

    def run_overvoltage(self):
        s = self.win.state
        body = s.body(C_uF=self.o_C.value(), V1_V=self.o_V1.value(), V_limit_V=self.o_Vlim.value(), speed_rpm=self.o_n.value(),
                      torque_Nm=self.o_T.value(), reaction_time_ms=self.o_react.value(), profile=self.o_profile.currentData())
        try:
            res = api.overvoltage(body)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        cur = SF.overvoltage_curve(res)
        op = res.get("regen_operating_point") or {}
        title = tr("회생 중 배터리 차단", "battery disconnect while regenerating")
        if op:
            title += f" · P_dc = {op['Pdc_W'] / 1e3:.2f} kW (id {op['id_A']:.1f} A, iq {op['iq_A']:.1f} A)"
        self.p_ov.draw(F.fig_overvoltage, res, cur, title=title, name="overvoltage",
                       csv=lambda: {"t_s": cur["t_s"], "V": cur["V"], "E_J": cur["E_J"]})

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
        self.s_dev = number(0, 0, 1e5, "V", 0, 50, tr("0 = 미지정", "0 = not given"))
        self.s_link = number(0, 0, 1e5, "V", 0, 50, tr("0 = 미지정", "0 = not given"))
        for lab, wd in ((tr("속도", "speed"), self.s_n), ("Vdc", self.s_vdc), (tr("HV 상태", "HV state"), self.s_hv),
                        (tr("소자 정격 전압", "device rating"), self.s_dev), (tr("DC 링크 한계", "DC-link limit"), self.s_link)):
            f.addRow(lab, wd)
        v.addWidget(g)
        g = QGroupBox(tr("프로젝트 규칙 (JSON, 물리 법칙이 아닌 고객·프로젝트 규칙)", "project rules (JSON; customer/project rules, not physics)"))
        gl = QVBoxLayout(g)
        self.s_rules = QPlainTextEdit(json.dumps(EXAMPLE_RULES, indent=2, ensure_ascii=False))
        gl.addWidget(self.s_rules)
        v.addWidget(g, 1)
        b = primary_button(tr("안전 상태 스크리닝", "screen safe states"))
        b.clicked.connect(self.run_safe)
        v.addWidget(b)
        split.addWidget(left)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.p_safe = PlotPanel()
        self.t_safe = KeyValueTable()
        rv.addWidget(self.p_safe, 3)
        rv.addWidget(self.t_safe, 2)
        split.addWidget(right)
        split.setSizes([420, 1000])
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addWidget(split)
        return w

    def run_safe(self):
        s = self.win.state
        try:
            rules = json.loads(self.s_rules.toPlainText() or "[]")
            body = s.body(speed_rpm=self.s_n.value(), Vdc_V=self.s_vdc.value(), hv_state=self.s_hv.currentData(),
                          device_voltage_rating_V=self.s_dev.value() or None, dc_link_limit_V=self.s_link.value() or None,
                          rules=rules)
            res = api.safe_state(body)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        asc = SF.asc_vs_speed(s.drive, self.s_vdc.value())
        self.p_safe.draw(F.fig_safe_state, asc, self.s_n.value(), name="safe_state",
                         csv=lambda: {"speed_rpm": asc["speeds"], "asc_i_peak_A": asc["i_peak_A"],
                                      "asc_T_shaft_Nm": asc["Tshaft_Nm"], "back_emf_ll_peak_V": asc["back_emf_ll_peak_V"]})
        rows = [(tr("판정", "claim"), _claim_line(res["claim"]))]
        for c in res["candidates"]:
            for k, val in c.items():
                if k != "candidate":
                    rows.append((f"{c['candidate']} · {k}", fmt(val)))
        for r in res.get("project_rules", []):
            rows.append((tr("프로젝트 규칙", "project rule"), json.dumps(r, ensure_ascii=False)))
        rows += [(tr("주석", "note"), n) for n in res.get("notes", [])]
        self.t_safe.set_rows(rows)

    def redraw(self):
        for p in (self.p_ftti, self.p_dis, self.p_ov, self.p_safe):
            p.redraw()
