"""Table-based editor for thermal networks (no JSON editing needed).

Each node: name, temperature limit, heat source and share, coolant station, network type
(Foster R_i/tau_i or Cauer R_i/C_i), per-stage flow dependence.  The model can still be saved/loaded as JSON.
"""

from __future__ import annotations

import copy
import json
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import api
from ..i18n import tr
from .widgets import error_box, fmt, hint, number

SOURCES = (("inverter",), ("copper",), ("rotational",), ("copper", "rotational"))


def source_label(keys) -> str:
    names = {"inverter": tr("인버터 손실", "inverter loss"), "copper": tr("동손", "copper loss"),
             "rotational": tr("회전 손실", "rotational loss")}
    return " + ".join(names[k] for k in keys)


STATIONS = (("inverter", lambda: tr("인버터 냉각판", "inverter cold plate")),
            ("motor", lambda: tr("모터 워터재킷", "motor water jacket")),
            (None, lambda: tr("냉각수 입구 (유량 효과 없음)", "coolant inlet (no flow effect)")))

TEMPLATE_4 = {"foster": ([0.010, 0.030, 0.080, 0.080], [0.002, 0.03, 0.4, 2.5]),
              "cauer": ([0.012, 0.035, 0.070, 0.083], [0.2, 1.5, 6.0, 30.0])}

NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_stage_text(text: str) -> tuple[list[float], list[float], str]:
    """Pasted datasheet values -> (R, second) and the interpretation used.

    Accepts one stage per line ("R  tau") or two rows ("R1 R2 ... / tau1 tau2 ..."); an index row 1..N is dropped.
    """
    rows = []
    for line in text.splitlines():
        nums = [float(x) for x in NUM_RE.findall(line.replace(",", " ") if line.count(",") > 1 else line.replace(",", "."))]
        if nums:
            rows.append(nums)
    if len(rows) >= 2 and all(abs(v - (i + 1)) < 1e-12 for i, v in enumerate(rows[0])) and len(rows[0]) == len(rows[1]):
        rows = rows[1:]
    if len(rows) == 2 and len(rows[0]) == len(rows[1]) and len(rows[0]) > 2:
        return rows[0], rows[1], tr("두 줄 (R 행, 두 번째 값 행)", "two rows (R row, second row)")
    if rows and all(len(r) >= 2 for r in rows):
        return [r[-2] for r in rows], [r[-1] for r in rows], tr("한 줄에 한 단 (R, 두 번째 값)", "one stage per line (R, second)")
    raise ValueError(tr("숫자 쌍을 찾지 못했습니다. 'R τ' 형식의 줄 또는 R 행/τ 행 두 줄을 붙여 넣으세요.",
                        "no value pairs found; paste 'R tau' lines or an R row and a tau row"))


class StageTable(QTableWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(0, 4, parent)
        self.kind = "foster"
        self.verticalHeader().setVisible(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setMinimumHeight(150)
        self._busy = False
        self.itemChanged.connect(self._item_changed)
        self._headers()

    def _headers(self):
        second = "τ_i [s]" if self.kind == "foster" else "C_i [J/K]"
        derived = tr("C_i = τ/R [J/K] (계산)", "C_i = τ/R [J/K] (derived)") if self.kind == "foster" else \
            tr("R·C [s] (참고)", "R·C [s] (info)")
        self.setHorizontalHeaderLabels(["R_i [K/W]", second, derived, tr("유량 의존", "flow-dependent")])
        self.setVerticalHeaderLabels([f"{i + 1}" for i in range(self.rowCount())])

    def set_kind(self, kind: str):
        self.kind = kind
        self._headers()
        self._recompute()

    def load(self, R, second, flags=None):
        self._busy = True
        self.setRowCount(0)
        flags = list(flags or [])
        for i, (r, x) in enumerate(zip(R, second)):
            self._append(r, x, bool(flags[i]) if i < len(flags) else False)
        self._busy = False
        self._headers()
        self._recompute()

    def _append(self, r, x, flag):
        i = self.rowCount()
        self.insertRow(i)
        self.setItem(i, 0, QTableWidgetItem(f"{r:.6g}"))
        self.setItem(i, 1, QTableWidgetItem(f"{x:.6g}"))
        d = QTableWidgetItem("")
        d.setFlags(Qt.ItemIsEnabled)
        self.setItem(i, 2, d)
        f = QTableWidgetItem("")
        f.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        f.setCheckState(Qt.Checked if flag else Qt.Unchecked)
        self.setItem(i, 3, f)

    def add_stage(self):
        R, X, F = self.values(strict=False)
        r = R[-1] if R else 0.05
        x = (X[-1] * 5.0) if X else (0.5 if self.kind == "foster" else 50.0)
        self._busy = True
        self._append(r, x, False)
        self._busy = False
        self._headers()
        self._recompute()
        self.changed.emit()

    def remove_stage(self):
        rows = sorted({i.row() for i in self.selectedIndexes()}, reverse=True) or [self.rowCount() - 1]
        for r in rows:
            if self.rowCount() > 1:
                self.removeRow(r)
        self._headers()
        self._recompute()
        self.changed.emit()

    def values(self, strict: bool = True):
        R, X, F = [], [], []
        for i in range(self.rowCount()):
            try:
                r = float(self.item(i, 0).text())
                x = float(self.item(i, 1).text())
            except (AttributeError, ValueError):
                if strict:
                    raise ValueError(tr(f"{i + 1}단의 값이 숫자가 아닙니다", f"stage {i + 1}: not a number")) from None
                continue
            R.append(r)
            X.append(x)
            F.append(self.item(i, 3).checkState() == Qt.Checked)
        return R, X, F

    def _item_changed(self, item):
        if self._busy:
            return
        if item.column() in (0, 1):
            self._recompute()
        self.changed.emit()

    def _recompute(self):
        self._busy = True
        for i in range(self.rowCount()):
            try:
                r = float(self.item(i, 0).text())
                x = float(self.item(i, 1).text())
                val = (x / r if r > 0 else float("inf")) if self.kind == "foster" else r * x
                self.item(i, 2).setText(fmt(val, 5))
            except (AttributeError, ValueError):
                if self.item(i, 2) is not None:
                    self.item(i, 2).setText("—")
        self._busy = False


class NodeEditor(QWidget):
    changed = Signal()

    def __init__(self, spec: dict | None = None, parent=None):
        super().__init__(parent)
        self.name = QLineEdit()
        self.limit = number(150, -50, 400, "°C", 1, 5)
        self.source = QComboBox()
        for keys in SOURCES:
            self.source.addItem(source_label(keys), keys)
        self.share = number(1.0, 0.0, 1.0, "", 4, 0.01, tr("해당 손실 중 이 노드를 가열하는 비율. 예: 스위치 1개 = 1/6 ≈ 0.1667",
                                                             "share of that loss heating this node, e.g. one of six switches = 1/6"))
        self.station = QComboBox()
        for key, lab in STATIONS:
            self.station.addItem(lab(), key)
        self.r_foster = QRadioButton("Foster (R_i, τ_i)")
        self.r_cauer = QRadioButton("Cauer (R_i, C_i)")
        grp = QButtonGroup(self)
        grp.addButton(self.r_foster)
        grp.addButton(self.r_cauer)
        self.r_foster.setChecked(True)
        self.table = StageTable()
        self.flow_ref = number(10.0, 0.1, 1000.0, "L/min", 2, 1.0, tr("유량 의존 단의 R_i가 측정된 기준 유량", "flow at which the flow-dependent R_i were measured"))
        self.flow_exp = number(0.8, 0.0, 2.0, "", 2, 0.05, tr("R ∝ (Q_ref/Q)^n, 난류 대류(Dittus–Boelter)에서 n ≈ 0.8",
                                                                "R ∝ (Q_ref/Q)^n; turbulent convection (Dittus–Boelter) n ≈ 0.8"))
        self.summary = QLabel("")
        self.summary.setObjectName("Hint")
        self.summary.setWordWrap(True)
        form = QFormLayout()
        form.addRow(tr("노드 이름", "node name"), self.name)
        form.addRow(tr("한계 온도", "temperature limit"), self.limit)
        row = QHBoxLayout()
        row.addWidget(self.source, 2)
        row.addWidget(QLabel("×"))
        row.addWidget(self.share, 1)
        form.addRow(tr("발열원 × 비율", "heat source × share"), row)
        form.addRow(tr("기준 냉각수 위치", "coolant reference"), self.station)
        kind_row = QHBoxLayout()
        kind_row.addWidget(self.r_foster)
        kind_row.addWidget(self.r_cauer)
        kind_row.addStretch(1)
        form.addRow(tr("회로 형식", "network type"), kind_row)
        buttons = QHBoxLayout()
        for text, fn in ((tr("+ 단 추가", "+ stage"), self.table.add_stage), (tr("− 단 삭제", "− stage"), self.table.remove_stage),
                         (tr("4단 템플릿", "4-stage template"), self._template), (tr("클립보드 붙여넣기", "paste from clipboard"), self._paste)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        buttons.addStretch(1)
        flow = QHBoxLayout()
        flow.addWidget(QLabel(tr("유량 의존 단: 기준 유량", "flow-dependent stages: reference flow")))
        flow.addWidget(self.flow_ref)
        flow.addWidget(QLabel(tr("지수 n", "exponent n")))
        flow.addWidget(self.flow_exp)
        flow.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(buttons)
        lay.addWidget(self.table, 1)
        lay.addLayout(flow)
        lay.addWidget(self.summary)
        lay.addWidget(hint(tr("Foster: 데이터시트의 r_i, τ_i를 그대로 입력(단 사이 절점은 물리 온도 아님). Cauer: 층별 R_i, C_i(접합부 → 냉각수 순서), "
                              "계산 시 정확히 Foster로 변환합니다. '유량 의존'을 체크한 단(보통 냉각판→냉각수 대류)은 R_i·(Q_ref/Q)^n으로 보정됩니다.",
                              "Foster: enter the datasheet r_i, tau_i (inner nodes are not physical). Cauer: layer R_i, C_i from the "
                              "junction to the coolant, converted exactly to Foster. Checked stages (usually plate-to-coolant "
                              "convection) are scaled by (Q_ref/Q)^n.")))
        self.r_foster.toggled.connect(self._kind_changed)
        for w in (self.name,):
            w.textChanged.connect(self.changed)
        for w in (self.limit, self.share, self.flow_ref, self.flow_exp):
            w.valueChanged.connect(self.changed)
        for w in (self.source, self.station):
            w.currentIndexChanged.connect(self.changed)
        self.table.changed.connect(self._update_summary)
        self.table.changed.connect(self.changed)
        self.load(spec or api.EXAMPLE_THERMAL["nodes"][0])

    def _kind_changed(self, *_):
        self.table.set_kind("foster" if self.r_foster.isChecked() else "cauer")
        self._update_summary()
        self.changed.emit()

    def _template(self):
        kind = "foster" if self.r_foster.isChecked() else "cauer"
        R, X = TEMPLATE_4[kind]
        self.table.load(R, X, [False, False, False, True])
        self._update_summary()
        self.changed.emit()

    def _paste(self):
        try:
            R, X, how = parse_stage_text(QApplication.clipboard().text())
        except ValueError as exc:
            error_box(self, tr("붙여넣기", "paste"), str(exc))
            return
        _R, _X, flags = self.table.values(strict=False)
        self.table.load(R, X, flags[:len(R)])
        self.summary.setText(tr(f"붙여넣기: {len(R)}단 · 해석: {how}", f"pasted {len(R)} stages · interpreted as {how}"))
        self.changed.emit()

    def load(self, spec: dict):
        self.name.setText(str(spec.get("id", "node")))
        self.limit.setValue(float(spec.get("limit_C", 150)))
        shares = spec.get("loss_share", {"inverter": 1.0})
        keys = tuple(k for k in ("inverter", "copper", "rotational") if k in shares)
        idx = next((i for i, s in enumerate(SOURCES) if s == keys), 0)
        self.source.setCurrentIndex(idx)
        self.share.setValue(float(next(iter(shares.values()))) if shares else 1.0)
        st = spec.get("station")
        self.station.setCurrentIndex(next((i for i, (k, _l) in enumerate(STATIONS) if k == st), 2 if st is None else 0))
        kind = str(spec.get("network", "foster")).lower()
        (self.r_cauer if kind == "cauer" else self.r_foster).setChecked(True)
        self.table.set_kind(kind)
        second = spec.get("C_J_per_K") if kind == "cauer" else spec.get("tau_s")
        if second is None and kind == "foster" and spec.get("C_J_per_K") is not None:
            second = [r * c for r, c in zip(spec["R_K_per_W"], spec["C_J_per_K"])]
        self.table.load(spec["R_K_per_W"], second, spec.get("flow_dependent"))
        self.flow_ref.setValue(float(spec.get("flow_ref_L_per_min") or 10.0))
        self.flow_exp.setValue(float(spec.get("flow_exponent", 0.8)))
        self._update_summary()

    def spec(self) -> dict:
        R, X, F = self.table.values()
        kind = "foster" if self.r_foster.isChecked() else "cauer"
        keys = self.source.currentData()
        out = {"id": self.name.text().strip() or "node", "network": kind, "R_K_per_W": R,
               ("tau_s" if kind == "foster" else "C_J_per_K"): X, "limit_C": self.limit.value(),
               "loss_share": {k: self.share.value() for k in keys}, "station": self.station.currentData()}
        if any(F):
            out.update(flow_dependent=F, flow_ref_L_per_min=self.flow_ref.value(), flow_exponent=self.flow_exp.value())
        return out

    def _update_summary(self):
        try:
            R, X, F = self.table.values()
            spec = self.spec()
            net = api._network(spec, None)
            dom = max(net.tau_s)
            self.summary.setText(tr(f"R_th 합계 {sum(R):.5g} K/W · 단 {len(R)}개 · 최장 시정수 {dom:.4g} s · "
                                    f"Z_th(1 s) = {net.zth(1.0):.4g} K/W" + (" · 유량 의존 단 있음" if any(F) else ""),
                                    f"ΣR_th {sum(R):.5g} K/W · {len(R)} stages · longest τ {dom:.4g} s · "
                                    f"Z_th(1 s) = {net.zth(1.0):.4g} K/W" + (" · flow-dependent stages" if any(F) else "")))
        except Exception as exc:  # noqa: BLE001 - live feedback only
            self.summary.setText(f"⚠ {exc}")


class ThermalModelEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        meta = QGroupBox(tr("모델 정보", "model"))
        g = QGridLayout(meta)
        self.model_id = QLineEdit()
        self.source = QLineEdit()
        self.validated = QCheckBox(tr("검증된 모델 (조건이 일치할 때만 FEASIBLE/INFEASIBLE 판정)",
                                      "validated model (definite verdicts only at matching conditions)"))
        self.v_temp = QCheckBox(tr("유효 냉각수 온도", "valid coolant temp."))
        self.v_temp_lo, self.v_temp_hi = number(60, -40, 150, "°C", 1), number(70, -40, 150, "°C", 1)
        self.v_flow = QCheckBox(tr("유효 유량", "valid flow"))
        self.v_flow_lo, self.v_flow_hi = number(8, 0.1, 1000, "L/min", 1), number(12, 0.1, 1000, "L/min", 1)
        g.addWidget(QLabel("model id"), 0, 0)
        g.addWidget(self.model_id, 0, 1, 1, 3)
        g.addWidget(QLabel(tr("출처", "source")), 1, 0)
        g.addWidget(self.source, 1, 1, 1, 3)
        g.addWidget(self.validated, 2, 0, 1, 4)
        g.addWidget(self.v_temp, 3, 0)
        g.addWidget(self.v_temp_lo, 3, 1)
        g.addWidget(QLabel("–"), 3, 2)
        g.addWidget(self.v_temp_hi, 3, 3)
        g.addWidget(self.v_flow, 4, 0)
        g.addWidget(self.v_flow_lo, 4, 1)
        g.addWidget(QLabel("–"), 4, 2)
        g.addWidget(self.v_flow_hi, 4, 3)
        buttons = QHBoxLayout()
        for text, fn in ((tr("+ 노드 추가", "+ node"), self.add_node), (tr("예시로 초기화", "reset to example"), self.reset),
                         (tr("JSON 불러오기…", "load JSON…"), self.load_file), (tr("JSON 저장…", "save JSON…"), self.save_file)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(buttons)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(meta)
        for w in (self.model_id, self.source):
            w.textChanged.connect(self.changed)
        for w in (self.validated, self.v_temp, self.v_flow):
            w.toggled.connect(self.changed)
        self.coolant_spec: dict | None = None
        self.load(api.EXAMPLE_THERMAL)

    def _close_tab(self, i):
        if self.tabs.count() > 1:
            w = self.tabs.widget(i)
            self.tabs.removeTab(i)
            w.deleteLater()
            self.changed.emit()

    def add_node(self, spec: dict | None = None):
        ed = NodeEditor(spec if isinstance(spec, dict) else {"id": tr("새 노드", "new node"), "network": "foster",
                                                              "R_K_per_W": [0.05, 0.1], "tau_s": [0.1, 5.0],
                                                              "limit_C": 150, "loss_share": {"inverter": 1 / 6},
                                                              "station": "inverter"})
        ed.changed.connect(self.changed)
        ed.name.textChanged.connect(lambda t, ed=ed: self.tabs.setTabText(self.tabs.indexOf(ed), t or "node"))
        self.tabs.addTab(ed, ed.name.text())
        self.tabs.setCurrentWidget(ed)
        self.changed.emit()

    def load(self, spec: dict):
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.deleteLater()
        for n in spec["nodes"]:
            self.add_node(n)
        self.tabs.setCurrentIndex(0)
        self.model_id.setText(spec.get("model_id", "UI_THERMAL"))
        self.source.setText(spec.get("source", ""))
        self.validated.setChecked(bool(spec.get("validated")))
        val = spec.get("validity") or {}
        self.v_temp.setChecked("coolant_temp_C" in val)
        if "coolant_temp_C" in val:
            self.v_temp_lo.setValue(val["coolant_temp_C"][0])
            self.v_temp_hi.setValue(val["coolant_temp_C"][1])
        self.v_flow.setChecked("coolant_flow_L_per_min" in val)
        if "coolant_flow_L_per_min" in val:
            self.v_flow_lo.setValue(val["coolant_flow_L_per_min"][0])
            self.v_flow_hi.setValue(val["coolant_flow_L_per_min"][1])
        self.coolant_spec = copy.deepcopy(spec.get("coolant"))
        self.changed.emit()

    def reset(self):
        self.load(api.EXAMPLE_THERMAL)

    def spec(self) -> dict:
        nodes = [self.tabs.widget(i).spec() for i in range(self.tabs.count())]
        out = {"model_id": self.model_id.text().strip() or "UI_THERMAL", "revision": "ui",
               "validated": self.validated.isChecked(), "source": self.source.text().strip() or "desktop editor",
               "nodes": nodes}
        val = {}
        if self.v_temp.isChecked():
            val["coolant_temp_C"] = [self.v_temp_lo.value(), self.v_temp_hi.value()]
        if self.v_flow.isChecked():
            val["coolant_flow_L_per_min"] = [self.v_flow_lo.value(), self.v_flow_hi.value()]
        if val:
            out["validity"] = val
        return out

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("열 모델 JSON", "thermal model JSON"), "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                spec = json.load(f)
            api._thermal_model(spec, 65.0)          # validate before accepting
            self.load(spec)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("열 모델 불러오기 실패", "could not load thermal model"), str(exc))

    def save_file(self, coolant: dict | None = None):
        path, _ = QFileDialog.getSaveFileName(self, tr("열 모델 JSON 저장", "save thermal model JSON"), "thermal_model.json", "JSON (*.json)")
        if path:
            spec = self.spec()
            if self.coolant_spec:
                spec["coolant"] = self.coolant_spec
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spec, f, indent=2, ensure_ascii=False)
