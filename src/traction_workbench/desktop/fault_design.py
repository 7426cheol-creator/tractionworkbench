"""Protection design and safety requirements editor of the fault simulation page - every value in a typed field,
never JSON.

The editor works on a copy of the project's ``fault_sim`` section: safety goals (ASIL, FTTI, safe state), FSRs
(ASIL, budgets, allocated mechanisms, safe-state TSR, warning, verification), TSRs (the criterion of each type in its
own fields: window, bound, safe state with its conditions), safety mechanisms (enable, path, reaction, task period,
thresholds, debounce and filter times, latent test), reaction strategies (steps with their exits and parameters, the
representative templates), the safe-state policy (rules, priority, latch and recovery) and the reaction paths.
Renaming an id renames its references.  Every edit is validated at once; the difference to the project is the
study's DESIGN VARIANT (a list of path / project value / study value), which runs, is saved with scenarios and
workspaces, and can be written into the project as its new fault_sim data.
"""

from __future__ import annotations

import copy
import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QMenu, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from ..errors import InputValidationError
from ..extensions.faultsim.configure import apply_overrides, validate_section
from ..extensions.faultsim.design import change_rows, diff_overrides
from ..extensions.faultsim.labels import quantity_label
from ..i18n import tr

NOT_INPUT = "twb_not_input"
HAZARDS = ("accel", "decel", "component", "other")
HAZ_KO = {"accel": "가속 토크", "decel": "감속 토크", "component": "부품 과부하", "other": "기타"}
KIND_KO = {"torque_monitor": "토크 감시", "current_plausibility": "세 상 전류 합", "overcurrent_sw": "SW 과전류",
           "overvoltage_sw": "SW 과전압", "undervoltage_sw": "SW 저전압", "position_los": "위치 신호 소실",
           "command_timeout": "명령 시간 초과", "overcurrent_hw": "HW 과전류 비교기", "overvoltage_hw": "HW 과전압 비교기",
           "desat": "desat", "gate_uvlo": "게이트 UVLO", "watchdog": "워치독"}
ACTION_KO = {"asc_low": "ASC-low", "asc_high": "ASC-high", "six_switch_off": "6SO (프리휠)", "torque_zero": "토크 0",
             "torque_ramp": "토크 램프 → 0", "current_to_asc": "전류 사전 조정 → ASC 점", "voltage_ramp": "전압 램프 → 0",
             "sequential_asc_low": "상별 순차 ASC-low", "sequential_asc_high": "상별 순차 ASC-high",
             "vdc_hysteresis_low": "Vdc 히스테리시스 6SO↔ASC-low", "vdc_hysteresis_high": "Vdc 히스테리시스 6SO↔ASC-high"}
EXIT_KO = {"none": "유지 (마지막 단계)", "time": "시간 [ms]", "done": "완료 시", "i_below": "측정 |i| < [A]",
           "speed_below": "측정 속도 < [rpm]", "vdc_below": "측정 Vdc < [V]", "vdc_above": "측정 Vdc > [V]"}
CRIT_KO = {"torque_window": "토크 창", "bound": "물리량 한계", "safe_state": "안전 상태 도달·유지",
           "no_false_reaction": "오반응 없음", "timing": "시간 (FDTI/FRTI/FHTI)"}
REACT_KO = {"safe_state": "안전 상태 (정책 결정)", "asc_low": "ASC-low", "asc_high": "ASC-high", "six_switch_off": "6SO",
            "torque_zero": "토크 0", "report_only": "보고만"}
REACT_EN = {"safe_state": "safe state (the policy decides)", "torque_zero": "torque 0", "report_only": "report only"}
PARAM_KO = {"abs_Nm": ("절대 폭", "absolute half-width"), "rel": ("상대 폭 (|T| 비율)", "relative half-width"),
            "delay_ms": ("요청 지연 허용", "request delay allowance"),
            "response_tau_ms": ("정상 응답 시상수", "normal response time constant"),
            "ramp_Nm_per_ms": ("정상 토크 램프 (추종)", "healthy torque ramp"), "debounce_ms": ("디바운스", "debounce"),
            "request_input": ("비교할 요청", "request compared with"), "threshold_A": ("임계값", "threshold"),
            "threshold_V": ("임계값", "threshold"), "timeout_ms": ("시간 제한", "timeout"),
            "filter_us": ("글리치 필터", "glitch filter"), "turnoff_us": ("소프트 차단 시간", "soft turn-off"),
            "delay_us": ("보고 지연", "report delay"), "ramp_ms": ("램프 시간", "ramp time"),
            "tol_A": ("완료 판정 허용 오차", "'done' tolerance"), "rate_Nm_per_ms": ("램프율", "ramp rate"),
            "i_zero_A": ("닫힘 판정 전류", "closing current threshold"),
            "v_on_V": ("ASC 전환 전압", "ASC-on voltage"), "v_off_V": ("프리휠 복귀 전압", "freewheel-again voltage")}


def _react(r: str) -> str:
    return tr(REACT_KO.get(r, r), REACT_EN.get(r, r))


def _plabel(key: str) -> str:
    ko, en = PARAM_KO.get(key, (key.replace("_", " "), key.replace("_", " ")))
    return tr(ko, en)


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v)


def strategy_summary(st: dict) -> str:
    """A declared strategy in one line: its steps with their exits, then the fallback bridge state."""
    parts = []
    for i, s in enumerate(st.get("steps") or []):
        a, e, v, mx = s.get("action"), s.get("exit") or "none", s.get("value"), s.get("max_ms")
        act = tr(ACTION_KO.get(a, a), a)
        if e == "none":
            ex = ""
        elif e == "time":
            ex = tr(f" {_fmt(v)} ms", f" for {_fmt(v)} ms")
        elif e == "done":
            ex = tr(" 완료까지", " until done")
        else:
            what = {"i_below": ("|i| <", "|i| <", "A"), "speed_below": ("속도 <", "speed <", "rpm"),
                    "vdc_below": ("Vdc <", "Vdc <", "V"), "vdc_above": ("Vdc >", "Vdc >", "V")}.get(e, (e, e, ""))
            ex = tr(f" ({what[0]} {_fmt(v)} {what[2]}까지)", f" until {what[1]} {_fmt(v)} {what[2]}")
        if mx and e not in ("none", "time"):
            ex += tr(f", 최대 {_fmt(mx)} ms", f", max {_fmt(mx)} ms")
        parts.append(f"{i + 1}. {act}{ex}")
    out = " → ".join(parts)
    if st.get("fallback"):
        out += tr(f" · 실행 불가 시 {REACT_KO.get(st['fallback'], st['fallback'])}", f" · fallback {_react(st['fallback'])}")
    return out


class Col:
    """A column of a record table: ``kind`` text | num | int | choice | bool | list; ``get`` / ``put`` for a nested
    value; ``choices`` a list of (label, value) or a callable returning it."""

    def __init__(self, key, label, kind="text", choices=None, tip="", get=None, put=None, width=None):
        self.key, self.label, self.kind, self.choices, self.tip = key, label, kind, choices, tip
        self.get = get or (lambda r, k=key: r.get(k))
        self.put = put or (lambda r, v, k=key: r.__setitem__(k, v))
        self.width = width

    def options(self):
        c = self.choices() if callable(self.choices) else self.choices
        return list(c or [])


class RecordTable(QTableWidget):
    """Rows = dicts of the working copy (edited in place); ``edited(row, key)`` after every change."""
    edited = Signal(int, str)

    def __init__(self, cols, parent=None, min_height=150):
        super().__init__(0, len(cols), parent)
        self.cols = cols
        self.records: list = []
        self._busy = False
        self.setProperty(NOT_INPUT, True)
        self.setHorizontalHeaderLabels([c.label for c in cols])
        for j, c in enumerate(cols):
            it = self.horizontalHeaderItem(j)
            if it is not None and c.tip:
                it.setToolTip(c.tip)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setMinimumHeight(min_height)
        self.verticalHeader().setVisible(False)
        self.itemChanged.connect(self._item_changed)

    def set_records(self, records: list, keep_row: bool = True):
        row = self.currentRow() if keep_row else -1
        self._busy = True
        self.records = records
        self.setRowCount(0)
        for r, rec in enumerate(records):
            self.insertRow(r)
            for j, c in enumerate(self.cols):
                self._fill(r, j, c, rec)
        self._busy = False
        self.resizeColumnsToContents()
        for j, c in enumerate(self.cols):
            if c.width:
                self.setColumnWidth(j, c.width)
            elif self.columnWidth(j) > 320:
                self.setColumnWidth(j, 320)
        if 0 <= row < self.rowCount():
            self.selectRow(row)
        elif self.rowCount():
            self.selectRow(0)

    def _fill(self, r, j, c, rec):
        v = c.get(rec)
        if c.kind == "choice":
            cb = QComboBox()
            cb.setProperty(NOT_INPUT, True)
            opts = c.options()
            for lab, val in opts:
                cb.addItem(lab, val)
            i = cb.findData(v)
            if i < 0 and v not in (None, ""):
                cb.addItem(f"{v} (?)", v)
                i = cb.count() - 1
            cb.setCurrentIndex(max(0, i))
            cb.currentIndexChanged.connect(lambda _i, cb=cb, c=c, r=r: self._combo_changed(r, c, cb))
            self.setCellWidget(r, j, cb)
            return
        it = QTableWidgetItem()
        if c.kind == "bool":
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            it.setCheckState(Qt.Checked if v else Qt.Unchecked)
        else:
            it.setText(_fmt(v))
        if c.tip:
            it.setToolTip(c.tip)
        self.setItem(r, j, it)

    def _combo_changed(self, r, c, cb):
        if self._busy or r >= len(self.records):
            return
        c.put(self.records[r], cb.currentData())
        self.edited.emit(r, c.key)

    def _item_changed(self, it):
        if self._busy:
            return
        r, j = it.row(), it.column()
        if r >= len(self.records):
            return
        c, rec = self.cols[j], self.records[r]
        try:
            if c.kind == "bool":
                v = it.checkState() == Qt.Checked
            elif c.kind in ("num", "int"):
                t = it.text().strip().replace(",", ".")
                v = None if t == "" else (int(float(t)) if c.kind == "int" else float(t))
            elif c.kind == "list":
                v = [x.strip() for x in it.text().split(",") if x.strip()]
            else:
                v = it.text().strip()
        except ValueError:
            self._busy = True
            it.setText(_fmt(c.get(rec)))
            self._busy = False
            return
        c.put(rec, v)
        self.edited.emit(r, c.key)

    def current(self):
        r = self.currentRow()
        return self.records[r] if 0 <= r < len(self.records) else None


class ParamForm(QWidget):
    """Typed fields for a dict (edited in place): specs of (key, label, kind, unit / choices, tip)."""
    edited = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty(NOT_INPUT, True)
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.target: dict | None = None
        self.widgets: dict = {}            # key -> its field
        self._busy = False

    def set_target(self, target: dict | None, specs: list):
        self._busy = True
        while self.form.rowCount():
            self.form.removeRow(0)
        self.widgets = {}
        self.target = target
        if target is None:
            self._busy = False
            return
        for key, label, kind, extra, tip in specs:
            v = target.get(key)
            if kind == "choice":
                w = QComboBox()
                for lab, val in extra:
                    w.addItem(lab, val)
                i = w.findData(v)
                w.setCurrentIndex(max(0, i))
                w.currentIndexChanged.connect(lambda _i, w=w, k=key: self._set(k, w.currentData()))
            elif kind == "bool":
                w = QCheckBox()
                w.setChecked(bool(v))
                w.toggled.connect(lambda b, k=key: self._set(k, bool(b)))
            elif kind in ("num", "num_opt"):
                w = QLineEdit(_fmt(v))
                w.setPlaceholderText(tr("비움 = 없음", "blank = none") if kind == "num_opt" else "")
                w.editingFinished.connect(lambda w=w, k=key, opt=(kind == "num_opt"): self._num(k, w, opt))
            else:
                w = QLineEdit(_fmt(v))
                w.editingFinished.connect(lambda w=w, k=key: self._set(k, w.text().strip()))
            w.setProperty(NOT_INPUT, True)
            self.widgets[key] = w
            if tip:
                w.setToolTip(tip)
            unit = f" [{extra}]" if kind in ("num", "num_opt") and extra else ""
            self.form.addRow(f"{label}{unit}", w)
        self._busy = False

    def _num(self, key, w, optional):
        t = w.text().strip().replace(",", ".")
        if t == "" and optional:
            return self._set(key, None)
        try:
            v = float(t)
        except ValueError:
            w.setText(_fmt(self.target.get(key)) if self.target else "")
            return
        self._set(key, v)

    def _set(self, key, v):
        if self._busy or self.target is None:
            return
        if self.target.get(key) == v:
            return
        self.target[key] = v
        self.edited.emit(key)


def _buttons(*pairs) -> tuple:
    row = QHBoxLayout()
    out = []
    for text, fn in pairs:
        b = QPushButton(text)
        b.clicked.connect(fn)
        row.addWidget(b)
        out.append(b)
    row.addStretch(1)
    return row, out


def _unique(base: str, used) -> str:
    i, name = 1, base
    while name in used:
        i += 1
        name = f"{base}-{i}"
    return name


class DesignEditor(QWidget):
    """The editor (see the module note).  ``changed`` after every edit; ``overrides()`` = the design variant."""
    changed = Signal()
    apply_requested = Signal()

    def __init__(self, schema: dict, parent=None):
        super().__init__(parent)
        self.setProperty(NOT_INPUT, True)
        self.schema = schema
        self.base: dict = {}
        self.work: dict = {}
        self.error: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.revert_btn = QPushButton(tr("프로젝트 값으로 되돌리기", "revert to the project"))
        self.apply_btn = QPushButton(tr("프로젝트에 반영…", "write into the project…"))
        self.revert_btn.clicked.connect(self.revert)
        self.apply_btn.clicked.connect(self.apply_requested.emit)
        top.addWidget(self.status, 1)
        top.addWidget(self.revert_btn)
        top.addWidget(self.apply_btn)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self._build_sg()
        self._build_fsr()
        self._build_tsr()
        self._build_mech()
        self._build_strategies()
        self._build_policy()
        self._build_paths()
        self._build_changes()

    # ------------------------------------------------------------------ data
    def load(self, base: dict, overrides: dict | None = None) -> list:
        """Base (the project's section) and the variant on it; returns problems (an override that no longer applies
        is dropped and named)."""
        self.base = self._normalized(copy.deepcopy(base))
        problems = []
        try:
            self.work = apply_overrides(copy.deepcopy(base), overrides)
        except InputValidationError as exc:
            problems.append(str(exc))
            self.work = copy.deepcopy(base)
        self.work = self._normalized(self.work)
        self.refresh_all()
        self._after_edit(emit=False)
        return problems

    @staticmethod
    def _normalized(d: dict) -> dict:
        """The containers the editor shows, present (the base gets them too, so they are not changes)."""
        for k in ("strategies", "mechanisms", "paths"):
            d.setdefault(k, [])
        req = d.setdefault("requirements", {})
        for k in ("safety_goals", "fsr", "tsr"):
            req.setdefault(k, [])
        pol = d.setdefault("policy", {})
        pol.setdefault("rules", [])
        pol.setdefault("priority", ["asc_low", "asc_high", "six_switch_off", "torque_zero"])
        return d

    def overrides(self) -> dict:
        return diff_overrides(self.base, self.work)

    def revert(self):
        self.load(self.base, None)
        self.changed.emit()

    def refresh_all(self):
        rq = self.work["requirements"]
        self.t_sg.set_records(rq["safety_goals"])
        self.t_fsr.set_records(rq["fsr"])
        self.t_tsr.set_records(rq["tsr"])
        self.t_mech.set_records(self.work["mechanisms"])
        self.t_path.set_records(self.work["paths"])
        self.t_rules.set_records(self.work["policy"]["rules"])
        self._policy_scalars()
        self._fill_priority()
        self._fill_strategy_list()
        self._tsr_selected()
        self._mech_selected()

    def _after_edit(self, emit=True):
        try:
            validate_section(self.work)
            self.error = None
        except InputValidationError as exc:
            self.error = str(exc)
        except (TypeError, ValueError, KeyError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        try:
            ov = self.overrides()
        except InputValidationError as exc:
            ov, self.error = {}, str(exc)
        n = len(change_rows(self.base, ov))
        if self.error:
            self.status.setText(tr(f"<b style='color:#cf222e'>설계 오류</b>: {self.error}",
                                   f"<b style='color:#cf222e'>design error</b>: {self.error}"))
        else:
            self.status.setText(tr(f"<b style='color:#1a7f37'>유효</b> · 프로젝트 대비 변경 {n}건 (이 설계 변형으로 실행·저장됩니다)",
                                   f"<b style='color:#1a7f37'>valid</b> · {n} change(s) against the project (runs and "
                                   f"saves with this design variant)"))
        self.apply_btn.setEnabled(not self.error and n > 0)
        self.revert_btn.setEnabled(n > 0)
        self._fill_changes(ov)
        if emit:
            self.changed.emit()

    # ------------------------------------------------------------------ references
    def _ids(self, which) -> list:
        rq = self.work.get("requirements") or {}
        src = {"sg": rq.get("safety_goals"), "fsr": rq.get("fsr"), "tsr": rq.get("tsr"),
               "mech": self.work.get("mechanisms"), "path": self.work.get("paths"),
               "strategy": self.work.get("strategies")}[which] or []
        return [str(x.get("id")) for x in src]

    def reaction_choices(self, with_policy=True):
        base = [(_react(r), r) for r in self.schema["reactions"]
                if with_policy or r not in ("safe_state", "report_only")]
        return base + [(tr(f"전략: {s}", f"strategy: {s}"), s) for s in self._ids("strategy")]

    def rename(self, what: str, old, new):
        """Rename the references of an id (old -> new) across the working copy."""
        if not old or old == new:
            return
        rq = self.work["requirements"]
        if what == "sg":
            for f in rq["fsr"]:
                f["sg"] = [new if x == old else x for x in f.get("sg") or []]
        elif what == "fsr":
            for t in rq["tsr"]:
                if t.get("fsr") == old:
                    t["fsr"] = new
        elif what == "tsr":
            for f in rq["fsr"]:
                if f.get("safe_state") == old:
                    f["safe_state"] = new
        elif what == "mech":
            for f in rq["fsr"]:
                f["mechanisms"] = [new if x == old else x for x in f.get("mechanisms") or []]
            for r in self.work["policy"]["rules"]:
                if (r.get("if") or {}).get("mechanism") == old:
                    r["if"]["mechanism"] = new
        elif what == "path":
            for m in self.work["mechanisms"]:
                if m.get("path") == old:
                    m["path"] = new
        elif what == "strategy":
            for m in self.work["mechanisms"]:
                if m.get("reaction") == old:
                    m["reaction"] = new
            for p in self.work["paths"]:
                if p.get("fixed_reaction") == old:
                    p["fixed_reaction"] = new
            pol = self.work["policy"]
            for r in pol["rules"]:
                for k in ("then", "else"):
                    if r.get(k) == old:
                        r[k] = new
            pol["priority"] = [new if x == old else x for x in pol.get("priority") or []]

    def _id_col(self, what):
        def put(rec, v, what=what):
            old = rec.get("id")
            v = str(v).strip().replace(".", "_")
            if not v or v in [x for x in self._ids(what) if x != old]:
                return                                    # empty or duplicate: keep the old id
            rec["id"] = v
            self.rename(what, old, v)
        return Col("id", "ID", "text", put=put, width=110)

    def _edited(self, *_):
        self._after_edit()

    def _edited_refresh(self, *_):
        self.refresh_all()
        self._after_edit()

    # ------------------------------------------------------------------ SG
    def _build_sg(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(tr("안전 목표: ASIL·FTTI·위험 종류·안전 상태. FTTI는 FSR 예산(FDTI + FRTI)과 FHTI 판정의 상한입니다.",
                              "Safety goals: ASIL, FTTI, hazard, safe state. The FTTI bounds the FSR budgets (FDTI + "
                              "FRTI) and the FHTI verdict.")))

        def veh_get(r):
            return (r.get("vehicle") or {}).get("max_delta_v_mps")

        def veh_put(r, val):
            if val is None:
                if r.get("vehicle"):
                    r["vehicle"].pop("max_delta_v_mps", None)
            else:
                r.setdefault("vehicle", {})
                r["vehicle"] = dict(r["vehicle"] or {}, max_delta_v_mps=val)
        cols = [self._id_col("sg"), Col("text", tr("안전 목표", "safety goal"), width=260),
                Col("asil", "ASIL", "choice", [("—", "")] + [(a, a) for a in self.schema["asil"]]),
                Col("ftti_ms", "FTTI [ms]", "num", tip=tr("고장 허용 시간", "fault-tolerant time interval")),
                Col("hazard", tr("위험", "hazard"), "choice", [(tr(HAZ_KO[h], h), h) for h in HAZARDS]),
                Col("safe_state", tr("안전 상태", "safe state"), width=220),
                Col("situation", tr("운전 상황", "operating situation"), width=180),
                Col("vmax", tr("차량 Δv 한계 [m/s]", "vehicle Δv limit [m/s]"), "num", get=veh_get, put=veh_put)]
        self.t_sg = RecordTable(cols)
        self.t_sg.edited.connect(self._edited)
        v.addWidget(self.t_sg, 1)
        row, _ = _buttons((tr("안전 목표 추가", "add goal"), self._add_sg), (tr("선택 삭제", "remove"), self._del_sg))
        v.addLayout(row)
        self.tabs.addTab(w, tr("안전 목표 (SG)", "safety goals (SG)"))

    def _add_sg(self):
        ids = self._ids("sg")
        self.work["requirements"]["safety_goals"].append(
            {"id": _unique("SG-NEW", ids), "text": "", "asil": "B", "ftti_ms": 100.0, "hazard": "other"})
        self._edited_refresh()

    def _del_sg(self):
        r = self.t_sg.currentRow()
        if r >= 0:
            del self.work["requirements"]["safety_goals"][r]
            self._edited_refresh()

    # ------------------------------------------------------------------ FSR
    def _build_fsr(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(tr("기능 안전 요구: 담당 SG, ASIL, FDTI·FRTI 예산, 할당 메커니즘(쉼표로 구분), 안전 상태를 정의하는 TSR, "
                              "경고·성능 저하와 검증 방법.",
                              "Functional safety requirements: goals, ASIL, FDTI / FRTI budgets, allocated mechanisms "
                              "(comma separated), the TSR that defines the safe state, warning / degradation and "
                              "verification.")))
        cols = [self._id_col("fsr"), Col("sg", "SG", "list", tip=tr("쉼표로 구분", "comma separated")),
                Col("asil", "ASIL", "choice", [("—", "")] + [(a, a) for a in self.schema["asil"]]),
                Col("text", tr("요구", "requirement"), width=260),
                Col("fdti_budget_ms", tr("FDTI 예산 [ms]", "FDTI budget [ms]"), "num"),
                Col("frti_budget_ms", tr("FRTI 예산 [ms]", "FRTI budget [ms]"), "num"),
                Col("safe_state", tr("안전 상태 TSR", "safe-state TSR"), "choice",
                    lambda: [("—", None)] + [(t, t) for t in self._ids("tsr")]),
                Col("mechanisms", tr("할당 메커니즘", "allocated mechanisms"), "list", width=220),
                Col("warning", tr("경고·성능 저하", "warning / degradation"), width=200),
                Col("allocation", tr("할당 요소", "elements"), "list"),
                Col("verification", tr("검증 방법", "verification"), "list",
                    tip=", ".join(self.schema["verification"]))]
        self.t_fsr = RecordTable(cols)
        self.t_fsr.edited.connect(self._edited)
        v.addWidget(self.t_fsr, 1)
        row, _ = _buttons((tr("FSR 추가", "add FSR"), self._add_fsr), (tr("선택 삭제", "remove"), self._del_fsr))
        v.addLayout(row)
        self.tabs.addTab(w, "FSR")

    def _add_fsr(self):
        rq = self.work["requirements"]
        rq["fsr"].append({"id": _unique("FSR-NEW", self._ids("fsr")), "sg": self._ids("sg")[:1], "asil": "B",
                          "text": "", "mechanisms": [], "fdti_budget_ms": 10.0, "frti_budget_ms": 20.0,
                          "safe_state": None})
        self._edited_refresh()

    def _del_fsr(self):
        r = self.t_fsr.currentRow()
        if r >= 0:
            del self.work["requirements"]["fsr"][r]
            self._edited_refresh()

    # ------------------------------------------------------------------ TSR
    def _build_tsr(self):
        w = QWidget()
        v = QVBoxLayout(w)
        cols = [self._id_col("tsr"), Col("fsr", "FSR", "choice", lambda: [(f, f) for f in self._ids("fsr")]),
                Col("asil", "ASIL", "choice", [("—", "")] + [(a, a) for a in self.schema["asil"]]),
                Col("level", tr("수준", "level"), "choice", [(tr("인버터", "inverter"), "inverter"),
                                                              (tr("부품", "component"), "component")]),
                Col("text", tr("요구", "requirement"), width=280),
                Col("type", tr("판정 유형", "criterion"), "choice",
                    [(tr(CRIT_KO[c], c), c) for c in self.schema["criteria"]],
                    get=lambda r: (r.get("criterion") or {}).get("type"), put=self._set_crit_type),
                Col("allocation", tr("할당 (HW/SW 요소)", "allocation"), width=180),
                Col("verification", tr("검증 방법", "verification"), "list"),
                Col("rationale", tr("값의 근거", "rationale"), width=220)]
        self.t_tsr = RecordTable(cols, min_height=180)
        self.t_tsr.edited.connect(self._tsr_edited)
        self.t_tsr.itemSelectionChanged.connect(self._tsr_selected)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.t_tsr)
        det = QGroupBox(tr("선택한 TSR의 판정 기준 (값을 바꾸면 바로 적용)", "criterion of the selected TSR"))
        dv = QVBoxLayout(det)
        self.crit_form = ParamForm()
        self.crit_form.edited.connect(self._edited)
        dv.addWidget(self.crit_form)
        self.cond_box = QWidget()
        cv = QVBoxLayout(self.cond_box)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(QLabel(tr("안전 상태 조건 (모두 동시에 성립해야 함)", "safe-state conditions (all at once)")))
        qch = [(quantity_label(q) + f" [{u}]", q) for q, u in self.schema["quantities"].items()]
        self.t_cond = RecordTable([Col("quantity", tr("물리량", "quantity"), "choice", qch, width=260),
                                   Col("min", tr("최소", "min"), "num"), Col("max", tr("최대", "max"), "num")],
                                  min_height=90)
        self.t_cond.edited.connect(self._edited)
        cv.addWidget(self.t_cond)
        row, _ = _buttons((tr("조건 추가", "add condition"), self._add_cond),
                          (tr("조건 삭제", "remove condition"), self._del_cond))
        cv.addLayout(row)
        dv.addWidget(self.cond_box)
        split.addWidget(det)
        v.addWidget(split, 1)
        row, _ = _buttons((tr("TSR 추가", "add TSR"), self._add_tsr), (tr("선택 삭제", "remove"), self._del_tsr))
        v.addLayout(row)
        self.tabs.addTab(w, "TSR")

    def _crit_specs(self, typ):
        origins = [(tr("t=0부터", "from t = 0"), "t0"), (tr("고장부터", "from the fault"), "fault"),
                   (tr("검출부터", "from the detection"), "detection")]
        if typ == "torque_window":
            return [("side", tr("방향", "side"), "choice", [(tr("가속 쪽", "acceleration"), "accel"),
                                                          (tr("감속 쪽", "deceleration"), "decel"),
                                                          (tr("양쪽", "both"), "both")], ""),
                    ("abs_Nm", tr("절대 폭", "absolute half-width"), "num", "N·m", ""),
                    ("rel", tr("상대 폭 (|T| 비율)", "relative half-width (of |T|)"), "num", "", ""),
                    ("delay_ms", tr("요청 지연 허용", "request delay allowance"), "num", "ms", ""),
                    ("response_tau_ms", tr("정상 응답 시상수", "normal response τ"), "num", "ms", ""),
                    ("tolerance_ms", tr("허용 이탈 시간", "tolerated excursion"), "num", "ms",
                     tr("이 시간보다 긴 이탈이 위반", "an excursion longer than this is a violation")),
                    ("impulse_Nms", tr("허용 초과 충격량 (선택)", "tolerated excess impulse (optional)"), "num_opt", "N·m·s",
                     ""),
                    ("reduction_allowed", tr("토크 감소 허용 (0~요청 사이는 위반 아님)", "reduction allowed"), "bool", None,
                     ""),
                    ("origin", tr("시간 원점", "time origin"), "choice", origins, "")]
        if typ == "bound":
            return [("quantity", tr("물리량", "quantity"), "choice",
                     [(quantity_label(q) + f" [{u}]", q) for q, u in self.schema["quantities"].items()], ""),
                    ("min", tr("최소 (선택)", "min (optional)"), "num_opt", "", ""),
                    ("max", tr("최대 (선택)", "max (optional)"), "num_opt", "", ""),
                    ("tolerance_ms", tr("허용 이탈 시간 (선택)", "tolerated excursion (optional)"), "num_opt", "ms", ""),
                    ("origin", tr("시간 원점", "time origin"), "choice", origins, "")]
        if typ == "safe_state":
            return [("origin", tr("시간 원점", "time origin"), "choice", origins[1:][::-1], ""),
                    ("within_ms", tr("도달 시한", "reached within"), "num", "ms", ""),
                    ("hold_ms", tr("유지 시간", "held for"), "num", "ms", "")]
        return []

    def _set_crit_type(self, rec, typ):
        c = rec.get("criterion") or {}
        if c.get("type") == typ:
            return
        defaults = {"torque_window": {"side": "both", "abs_Nm": 50.0, "rel": 0.2, "delay_ms": 11.0,
                                      "response_tau_ms": 2.0, "tolerance_ms": 50.0, "reduction_allowed": True,
                                      "origin": "t0"},
                    "bound": {"quantity": "v_dc", "max": 850.0, "origin": "t0"},
                    "safe_state": {"origin": "detection", "within_ms": 30.0, "hold_ms": 20.0,
                                   "conditions": [{"quantity": "torque_abs", "max": 60.0}]}}
        rec["criterion"] = {"type": typ, **copy.deepcopy(defaults.get(typ, {}))}

    def _tsr_edited(self, _r, key):
        if key == "type":
            self._tsr_selected()
        self._after_edit()

    def _tsr_selected(self):
        rec = self.t_tsr.current()
        crit = None if rec is None else rec.setdefault("criterion", {"type": "timing"})
        typ = None if crit is None else crit.get("type")
        self.crit_form.set_target(crit, self._crit_specs(typ) if crit is not None else [])
        show = typ == "safe_state"
        self.cond_box.setVisible(show)
        if show:
            self.t_cond.set_records(crit.setdefault("conditions", []))

    def _add_cond(self):
        rec = self.t_tsr.current()
        if rec and (rec.get("criterion") or {}).get("type") == "safe_state":
            rec["criterion"].setdefault("conditions", []).append({"quantity": "torque_abs", "max": 60.0})
            self._tsr_selected()
            self._after_edit()

    def _del_cond(self):
        rec = self.t_tsr.current()
        r = self.t_cond.currentRow()
        if rec and r >= 0:
            del rec["criterion"]["conditions"][r]
            self._tsr_selected()
            self._after_edit()

    def _add_tsr(self):
        rq = self.work["requirements"]
        fsr = self._ids("fsr")
        rq["tsr"].append({"id": _unique("TSR-NEW", self._ids("tsr")), "fsr": fsr[0] if fsr else "", "text": "",
                          "level": "inverter", "asil": "B",
                          "criterion": {"type": "bound", "quantity": "v_dc", "max": 850.0, "origin": "t0"}})
        self._edited_refresh()

    def _del_tsr(self):
        r = self.t_tsr.currentRow()
        if r >= 0:
            del self.work["requirements"]["tsr"][r]
            self._edited_refresh()

    # ------------------------------------------------------------------ mechanisms
    def _param_key(self, rec, which):
        """The key of the threshold / time column of a mechanism kind (None: the kind has none)."""
        keys = [p[0] for p in self.schema["kind_params"].get(rec.get("kind"), [])]
        order = {"thr": ("threshold_A", "threshold_V", "abs_Nm", "timeout_ms"),
                 "time": ("debounce_ms", "filter_us", "turnoff_us", "delay_us")}[which]
        return next((k for k in order if k in keys), None)

    def _pcol(self, which, label, tip):
        def get(r, which=which):
            k = self._param_key(r, which)
            return None if k is None else (r.get("params") or {}).get(k)

        def put(r, v, which=which):
            k = self._param_key(r, which)
            if k is not None:
                r.setdefault("params", {})[k] = v
        return Col(f"p_{which}", label, "num", get=get, put=put, tip=tip)

    def _build_mech(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(tr("안전 메커니즘: 사용 여부, 반응 경로, 요청 반응(정책·기본 반응·전략), SW 주기, 임계값과 디바운스·필터. "
                              "종류별 나머지 매개변수는 아래에서 편집합니다.",
                              "Safety mechanisms: enabled, path, reaction (policy, primitive or strategy), task "
                              "period, threshold and debounce / filter; the other parameters below.")))
        kinds = [(tr(KIND_KO.get(k, k), k) + (" (SW)" if k in self.schema["sw_kinds"] else " (HW)"), k)
                 for k in self.schema["sw_kinds"] + self.schema["hw_kinds"]]
        cols = [Col("enabled", tr("사용", "on"), "bool", get=lambda r: r.get("enabled", True)),
                self._id_col("mech"),
                Col("kind", tr("종류", "kind"), "choice", kinds, width=170),
                Col("path", tr("경로", "path"), "choice", lambda: [(p, p) for p in self._ids("path")]),
                Col("reaction", tr("반응", "reaction"), "choice", lambda: self.reaction_choices(True), width=180),
                Col("period_ms", tr("주기 [ms] (SW)", "period [ms] (SW)"), "num"),
                self._pcol("thr", tr("임계값", "threshold"), tr("A / V / N·m / ms (종류에 따라)",
                                                                 "A / V / N*m / ms by kind")),
                self._pcol("time", tr("디바운스·필터", "debounce / filter"),
                           tr("SW: 디바운스 [ms], HW: 필터·차단·보고 [µs]", "SW: debounce [ms]; HW: filter / turn-off / "
                                                                         "report [us]")),
                Col("latent_test", tr("잠재 고장 시험", "latent-fault test"), width=180),
                Col("coverage", tr("진단 커버리지 (주장)", "claimed coverage"), "choice",
                    [("—", None), (tr("낮음", "low"), "low"), (tr("중간", "medium"), "medium"),
                     (tr("높음", "high"), "high")]),
                Col("text", tr("설명", "description"), width=260)]
        self.t_mech = RecordTable(cols, min_height=220)
        self.t_mech.edited.connect(self._mech_edited)
        self.t_mech.itemSelectionChanged.connect(self._mech_selected)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.t_mech)
        self.mech_box = QGroupBox(tr("선택한 메커니즘의 매개변수", "parameters of the selected mechanism"))
        mv = QVBoxLayout(self.mech_box)
        self.mech_form = ParamForm()
        self.mech_form.edited.connect(self._mech_param_edited)
        mv.addWidget(self.mech_form)
        split.addWidget(self.mech_box)
        v.addWidget(split, 1)
        self.add_mech_btn = QToolButton()
        self.add_mech_btn.setText(tr("메커니즘 추가 ▾", "add mechanism ▾"))
        self.add_mech_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.add_mech_btn)
        for lab, k in kinds:
            menu.addAction(lab, lambda k=k: self._add_mech(k))
        self.add_mech_btn.setMenu(menu)
        row, _ = _buttons((tr("선택 삭제", "remove"), self._del_mech))
        row.insertWidget(0, self.add_mech_btn)
        v.addLayout(row)
        self.tabs.addTab(w, tr("안전 메커니즘", "safety mechanisms"))

    def _mech_edited(self, _r, key):
        if key == "kind":
            rec = self.t_mech.current()
            if rec is not None:
                rec["params"] = {p[0]: p[2] for p in self.schema["kind_params"].get(rec["kind"], [])}
                if rec["kind"] in self.schema["sw_kinds"]:
                    rec.setdefault("period_ms", 1.0)
                else:
                    rec.pop("period_ms", None)
            self.t_mech.set_records(self.work["mechanisms"])
        self._mech_selected()
        self._after_edit()

    def _mech_param_edited(self, _key):
        self.t_mech.set_records(self.work["mechanisms"])
        self._after_edit()

    def _mech_selected(self):
        rec = self.t_mech.current()
        if rec is None:
            self.mech_form.set_target(None, [])
            return
        params = rec.setdefault("params", {})
        specs = []
        for key, unit, default, what in self.schema["kind_params"].get(rec.get("kind"), []):
            if isinstance(unit, (list, tuple)):
                specs.append((key, _plabel(key), "choice", [(u, u) for u in unit], what))
            else:
                specs.append((key, _plabel(key), "num_opt", unit.replace("us", "µs").replace("*", "·"), what))
        self.mech_form.set_target(params, specs)
        self.mech_box.setTitle(tr(f"{rec.get('id')} ({KIND_KO.get(rec.get('kind'), rec.get('kind'))})의 매개변수",
                                  f"parameters of {rec.get('id')} ({rec.get('kind')})"))

    def _add_mech(self, kind):
        m = {"id": _unique(f"SM-{kind[:3].upper()}", self._ids("mech")), "kind": kind,
             "path": (self._ids("path") or ["SW"])[0], "reaction": "safe_state", "enabled": True,
             "params": {p[0]: p[2] for p in self.schema["kind_params"].get(kind, [])}, "resources": [], "text": ""}
        if kind in self.schema["sw_kinds"]:
            m["period_ms"] = 1.0
            m["resources"] = ["MCU"]
        self.work["mechanisms"].append(m)
        self._edited_refresh()

    def _del_mech(self):
        r = self.t_mech.currentRow()
        if r >= 0:
            del self.work["mechanisms"][r]
            self._edited_refresh()

    # ------------------------------------------------------------------ strategies
    def _build_strategies(self):
        w = QWidget()
        h = QHBoxLayout(w)
        left = QVBoxLayout()
        left.addWidget(QLabel(tr("반응 전략", "reaction strategies")))
        self.l_strat = QListWidget()
        self.l_strat.setProperty(NOT_INPUT, True)
        self.l_strat.currentRowChanged.connect(self._strategy_selected)
        left.addWidget(self.l_strat, 1)
        self.add_tpl_btn = QToolButton()
        self.add_tpl_btn.setText(tr("대표 방법 추가 ▾", "add a representative method ▾"))
        self.add_tpl_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.add_tpl_btn)
        for key, t in self.schema["templates"].items():
            menu.addAction(tr(t.get("text_ko") or t["text"], t["text"]), lambda key=key: self._add_template(key))
        self.add_tpl_btn.setMenu(menu)
        left.addWidget(self.add_tpl_btn)
        row, _ = _buttons((tr("빈 전략", "empty"), self._add_strategy), (tr("복제", "copy"), self._copy_strategy),
                          (tr("삭제", "remove"), self._del_strategy))
        left.addLayout(row)
        h.addLayout(left, 1)
        right = QVBoxLayout()
        self.s_head = ParamForm()
        self.s_head.edited.connect(self._strategy_head_edited)
        right.addWidget(self.s_head)
        right.addWidget(QLabel(tr("단계: 각 동작을 종료 조건까지 유지합니다. 마지막 단계는 '유지'. 제어 동작(토크·전류·전압 램프)은 SW 경로와 "
                                  "동작 중인 전류 제어가 필요하며, 불가능하면 대체 상태로 바뀝니다.",
                                  "Steps: each action holds until its exit; the last step holds. Control actions "
                                  "need the software path and running current control, else the fallback state "
                                  "takes over.")))
        acts = [(tr(ACTION_KO.get(a, a), a), a) for a in self.schema["actions"]]
        exits = [(tr(EXIT_KO.get(e, e), e), e) for e in self.schema["exits"]]
        self.t_steps = RecordTable([Col("action", tr("동작", "action"), "choice", acts, width=230),
                                    Col("exit", tr("종료 조건", "exit"), "choice", exits, width=170),
                                    Col("value", tr("값", "value"), "num"),
                                    Col("max_ms", tr("최대 시간 [ms]", "max time [ms]"), "num")], min_height=140)
        self.t_steps.edited.connect(self._step_edited)
        self.t_steps.itemSelectionChanged.connect(self._step_selected)
        right.addWidget(self.t_steps, 1)
        row, _ = _buttons((tr("단계 추가", "add step"), self._add_step), (tr("단계 삭제", "remove step"), self._del_step),
                          ("▲", lambda: self._move_step(-1)), ("▼", lambda: self._move_step(1)))
        right.addLayout(row)
        self.step_box = QGroupBox(tr("선택한 단계의 매개변수", "parameters of the selected step"))
        sv = QVBoxLayout(self.step_box)
        self.step_form = ParamForm()
        self.step_form.edited.connect(self._edited)
        sv.addWidget(self.step_form)
        right.addWidget(self.step_box)
        h.addLayout(right, 3)
        self.tabs.addTab(w, tr("반응 전략", "reaction strategies"))

    def _fill_strategy_list(self, select: str | None = None):
        cur = select or (self.l_strat.currentItem().text() if self.l_strat.currentItem() else None)
        self.l_strat.blockSignals(True)
        self.l_strat.clear()
        for s in self.work["strategies"]:
            self.l_strat.addItem(str(s.get("id")))
        self.l_strat.blockSignals(False)
        ids = self._ids("strategy")
        self.l_strat.setCurrentRow(ids.index(cur) if cur in ids else (0 if ids else -1))
        self._strategy_selected(self.l_strat.currentRow())

    def _strategy(self):
        r = self.l_strat.currentRow()
        return self.work["strategies"][r] if 0 <= r < len(self.work["strategies"]) else None

    def _strategy_selected(self, _row=None):
        st = self._strategy()
        if st is None:
            self.s_head.set_target(None, [])
            self.t_steps.set_records([])
            self.step_form.set_target(None, [])
            return
        self._strat_old_id = st.get("id")
        self.s_head.set_target(st, [
            ("id", "ID", "text", None, ""),
            ("text", tr("설명", "description"), "text", None, ""),
            ("fallback", tr("대체 상태 (실행 불가 시)", "fallback state"), "choice",
             [(tr("자동 (마지막 브리지 상태)", "automatic (last bridge state)"), None), ("ASC-low", "asc_low"),
              ("ASC-high", "asc_high"), ("6SO", "six_switch_off")], ""),
            ("basis", tr("근거", "basis"), "text", None, "")])
        self.t_steps.set_records(st.setdefault("steps", []))
        self._step_selected()

    def _strategy_head_edited(self, key):
        st = self._strategy()
        if key == "id" and st is not None:
            old = getattr(self, "_strat_old_id", None)
            new = str(st.get("id") or "").strip().replace(".", "_")
            others = [x for i, x in enumerate(self._ids("strategy")) if i != self.l_strat.currentRow()]
            if not new or new in others or new in self.schema["reactions"]:
                new = old                                   # empty, duplicate or a reaction name: keep the old id
            st["id"] = new
            self.rename("strategy", old, new)
            self._strat_old_id = new
            self._fill_strategy_list(select=new)
            self.refresh_all()
        self._after_edit()

    def _step_edited(self, _r, key):
        if key == "action":
            rec = self.t_steps.current()
            allowed = self.schema["exits_for"].get(rec.get("action"), [])
            if rec.get("exit") not in allowed:
                rec["exit"] = allowed[0] if allowed else "none"
            rec["params"] = {p[0]: p[2] for p in self.schema["action_params"].get(rec["action"], [])}
            self.t_steps.set_records(self._strategy()["steps"])
        self._step_selected()
        self._after_edit()

    def _step_selected(self):
        rec = self.t_steps.current()
        if rec is None:
            self.step_form.set_target(None, [])
            return
        specs = [(k, _plabel(k), "num", unit.replace("*", "·"), what)
                 for k, unit, _d, what in self.schema["action_params"].get(rec.get("action"), [])]
        self.step_form.set_target(rec.setdefault("params", {}), specs)

    def _add_template(self, key):
        t = copy.deepcopy(self.schema["templates"][key])
        t.pop("text_ko", None)
        t["id"] = _unique(t["id"], self._ids("strategy"))
        self.work["strategies"].append(t)
        self._fill_strategy_list(select=t["id"])
        self._after_edit()

    def _add_strategy(self):
        sid = _unique("STRATEGY", self._ids("strategy"))
        self.work["strategies"].append({"id": sid, "steps": [{"action": "asc_low", "exit": "none"}], "text": ""})
        self._fill_strategy_list(select=sid)
        self._after_edit()

    def _copy_strategy(self):
        st = self._strategy()
        if st is None:
            return
        c = copy.deepcopy(st)
        c["id"] = _unique(st["id"] + "-COPY", self._ids("strategy"))
        self.work["strategies"].append(c)
        self._fill_strategy_list(select=c["id"])
        self._after_edit()

    def _del_strategy(self):
        r = self.l_strat.currentRow()
        if r >= 0:
            del self.work["strategies"][r]
            self._fill_strategy_list()
            self._after_edit()

    def _add_step(self):
        st = self._strategy()
        if st is None:
            return
        steps = st.setdefault("steps", [])
        if steps and steps[-1].get("exit") == "none":
            steps[-1]["exit"] = "time"
            steps[-1]["value"] = 2.0
        steps.append({"action": "asc_low", "exit": "none"})
        self.t_steps.set_records(steps)
        self._after_edit()

    def _del_step(self):
        st = self._strategy()
        r = self.t_steps.currentRow()
        if st is not None and r >= 0:
            del st["steps"][r]
            self.t_steps.set_records(st["steps"])
            self._after_edit()

    def _move_step(self, d):
        st = self._strategy()
        r = self.t_steps.currentRow()
        if st is None or r < 0 or not 0 <= r + d < len(st["steps"]):
            return
        s = st["steps"]
        s[r], s[r + d] = s[r + d], s[r]
        self.t_steps.set_records(s, keep_row=False)
        self.t_steps.selectRow(r + d)
        self._after_edit()

    # ------------------------------------------------------------------ policy
    def _build_policy(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(tr("안전 상태 정책: '안전 상태'를 요청한 메커니즘의 반응을 위에서부터 첫 규칙이 정합니다(측정 정보만). 빈 칸 = 조건 없음, "
                              "'기본'을 켠 행은 else.",
                              "Safe-state policy: the first matching rule decides a 'safe state' request (measured "
                              "information only). Blank = no condition; a 'default' row is the else.")))

        def cond(k, label, kind="num", choices=None):
            def get(r, k=k):
                return (r.get("if") or {}).get(k)

            def put(r, val, k=k):
                if "else" in r:
                    return
                c = dict(r.get("if") or {})
                if val in (None, ""):
                    c.pop(k, None)
                else:
                    c[k] = val
                r["if"] = c
            return Col(k, label, kind, choices, get=get, put=put)

        def is_else_get(r):
            return "else" in r

        def is_else_put(r, val):
            react = r.get("then") or r.get("else") or "six_switch_off"
            r.clear()
            if val:
                r["else"] = react
            else:
                r.update({"if": {}, "then": react})

        def then_get(r):
            return r.get("else") if "else" in r else r.get("then")

        def then_put(r, val):
            r["else" if "else" in r else "then"] = val
        kinds = [("—", None)] + [(tr(KIND_KO.get(k, k), k), k) for k in self.schema["sw_kinds"] + self.schema["hw_kinds"]]
        side = [("—", None), (tr("상단", "upper"), "upper"), (tr("하단", "lower"), "lower")]
        cols = [Col("else", tr("기본 (else)", "default (else)"), "bool", get=is_else_get, put=is_else_put),
                cond("speed_above_rpm", tr("속도 > [rpm]", "speed > [rpm]")),
                cond("speed_below_rpm", tr("속도 < [rpm]", "speed < [rpm]")),
                cond("vdc_above_V", "Vdc > [V]"), cond("vdc_below_V", "Vdc < [V]"),
                cond("detected_by", tr("검출 종류", "detected by"), "choice", kinds),
                cond("device", tr("desat 소자", "desat device"), "choice", side),
                cond("uvlo", tr("UVLO 측", "UVLO side"), "choice", side),
                cond("mechanism", tr("메커니즘", "mechanism"), "choice",
                     lambda: [("—", None)] + [(m, m) for m in self._ids("mech")]),
                Col("then", tr("→ 반응", "→ reaction"), "choice", lambda: self.reaction_choices(False),
                    get=then_get, put=then_put, width=190)]
        self.t_rules = RecordTable(cols, min_height=180)
        self.t_rules.edited.connect(self._rules_edited)
        v.addWidget(self.t_rules, 1)
        row, _ = _buttons((tr("규칙 추가", "add rule"), self._add_rule), (tr("삭제", "remove"), self._del_rule),
                          ("▲", lambda: self._move_rule(-1)), ("▼", lambda: self._move_rule(1)))
        v.addLayout(row)
        h = QHBoxLayout()
        g = QGroupBox(tr("래치·복귀", "latch and recovery"))
        gv = QVBoxLayout(g)
        self.pol_form = ParamForm()
        self.pol_form.edited.connect(self._edited)
        gv.addWidget(self.pol_form)
        h.addWidget(g, 1)
        g = QGroupBox(tr("우선순위 (위가 높음: 더 높은 반응만 활성 반응을 바꿈)", "priority (top first)"))
        gv = QVBoxLayout(g)
        self.l_prio = QListWidget()
        self.l_prio.setProperty(NOT_INPUT, True)
        gv.addWidget(self.l_prio)
        row, _ = _buttons(("▲", lambda: self._move_prio(-1)), ("▼", lambda: self._move_prio(1)),
                          (tr("전략 추가", "add strategy"), self._add_prio), (tr("빼기", "remove"), self._del_prio))
        gv.addLayout(row)
        h.addWidget(g, 1)
        v.addLayout(h)
        self.tabs.addTab(w, tr("안전 상태 정책", "safe-state policy"))

    def _policy_scalars(self):
        pol = self.work["policy"]
        self.pol_form.set_target(pol, [
            ("latch", tr("래치 (반응 유지)", "latch"), "bool", None, ""),
            ("recovery_after_ms", tr("복귀 대기 (비래치)", "recovery after (not latched)"), "num", "ms", ""),
            ("recovery_max_attempts", tr("복귀 시도 횟수", "recovery attempts"), "num", "", ""),
            ("restart", tr("재시동 방식", "restart"), "choice", [("flying", "flying"), ("cold", "cold")], ""),
            ("after_reset", tr("MCU 리셋 후", "after an MCU reset"), "choice",
             [(tr("재시동", "restart"), "restart"), (tr("안전 상태", "safe state"), "safe_state")], ""),
            ("speed_hysteresis_rpm", tr("속도 규칙 히스테리시스", "speed hysteresis"), "num", "rpm", ""),
            ("replace_unexecutable", tr("실행 불가 ASC 교체", "replace an unexecutable ASC"), "bool", None, "")])

    def _rules_edited(self, *_):
        self.t_rules.set_records(self.work["policy"]["rules"])
        self._after_edit()

    def _add_rule(self):
        rules = self.work["policy"]["rules"]
        at = next((i for i, r in enumerate(rules) if "else" in r), len(rules))
        rules.insert(at, {"if": {"speed_above_rpm": 6000.0}, "then": "asc_low"})
        self.t_rules.set_records(rules)
        self._after_edit()

    def _del_rule(self):
        r = self.t_rules.currentRow()
        if r >= 0:
            del self.work["policy"]["rules"][r]
            self.t_rules.set_records(self.work["policy"]["rules"])
            self._after_edit()

    def _move_rule(self, d):
        rules = self.work["policy"]["rules"]
        r = self.t_rules.currentRow()
        if 0 <= r < len(rules) and 0 <= r + d < len(rules):
            rules[r], rules[r + d] = rules[r + d], rules[r]
            self.t_rules.set_records(rules, keep_row=False)
            self.t_rules.selectRow(r + d)
            self._after_edit()

    def _fill_priority(self):
        self.l_prio.clear()
        for x in self.work["policy"]["priority"]:
            self.l_prio.addItem(_react(x) if x in REACT_KO else x)

    def _move_prio(self, d):
        pr = self.work["policy"]["priority"]
        r = self.l_prio.currentRow()
        if 0 <= r < len(pr) and 0 <= r + d < len(pr):
            pr[r], pr[r + d] = pr[r + d], pr[r]
            self._fill_priority()
            self.l_prio.setCurrentRow(r + d)
            self._after_edit()

    def _add_prio(self):
        pr = self.work["policy"]["priority"]
        missing = [s for s in self._ids("strategy") if s not in pr]
        if missing:
            pr.append(missing[0])
            self._fill_priority()
            self._after_edit()

    def _del_prio(self):
        pr = self.work["policy"]["priority"]
        r = self.l_prio.currentRow()
        if 0 <= r < len(pr) and pr[r] not in ("asc_low", "asc_high", "six_switch_off", "torque_zero"):
            del pr[r]
            self._fill_priority()
            self._after_edit()

    # ------------------------------------------------------------------ paths
    def _build_paths(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(tr("반응 경로: 요청에서 브리지까지의 지연과 필요한 자원(자원을 잃으면 경로도 잃음). MCU를 쓰지 않는 경로는 HW 경로 — "
                              "소프트웨어 단계가 있는 전략은 그 경로에서 대체 상태로 바뀝니다.",
                              "Reaction paths: delay from the request to the bridge and the resources they need. A "
                              "path without the MCU is a hardware path - strategies with software steps degrade "
                              "there.")))
        cols = [self._id_col("path"), Col("delay_us", tr("지연 [µs]", "delay [us]"), "num"),
                Col("resources", tr("필요 자원", "resources"), "list", width=160),
                Col("fixed_reaction", tr("고정 반응", "fixed reaction"), "choice",
                    lambda: [("—", None)] + self.reaction_choices(False), width=170),
                Col("basis", tr("근거", "basis"), width=320)]
        self.t_path = RecordTable(cols)
        self.t_path.edited.connect(self._edited)
        v.addWidget(self.t_path, 1)
        row, _ = _buttons((tr("경로 추가", "add path"), self._add_path), (tr("선택 삭제", "remove"), self._del_path))
        v.addLayout(row)
        self.tabs.addTab(w, tr("반응 경로", "reaction paths"))

    def _add_path(self):
        self.work["paths"].append({"id": _unique("PATH", self._ids("path")), "delay_us": 10.0, "resources": []})
        self._edited_refresh()

    def _del_path(self):
        r = self.t_path.currentRow()
        if r >= 0:
            del self.work["paths"][r]
            self._edited_refresh()

    # ------------------------------------------------------------------ changes
    def _build_changes(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(tr("프로젝트 fault_sim 데이터 대비 이 설계 변형의 변경 (실행·시나리오 파일·반례·보고서에 그대로 기록됨)",
                              "Changes of this design variant against the project's fault_sim data (recorded in "
                              "runs, scenario files, counterexamples and the report)")))
        self.t_changes = QTableWidget(0, 4)
        self.t_changes.setProperty(NOT_INPUT, True)
        self.t_changes.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_changes.setHorizontalHeaderLabels([tr("변경", "change"), tr("경로", "path"), tr("프로젝트 값", "project"),
                                                  tr("이 설계", "this design")])
        self.t_changes.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.t_changes, 1)
        self.tabs.addTab(w, tr("변경 목록", "changes"))

    def _fill_changes(self, ov):
        rows = change_rows(self.base, ov) if ov else []
        ko = {"changed": tr("변경", "changed"), "added": tr("추가", "added"), "removed": tr("삭제", "removed"),
              "reordered": tr("순서", "reordered")}
        self.t_changes.setRowCount(0)
        for r in rows:
            i = self.t_changes.rowCount()
            self.t_changes.insertRow(i)
            for j, x in enumerate((ko.get(r["change"], r["change"]), r["path"],
                                   json.dumps(r["project"], ensure_ascii=False, default=str)[:300],
                                   json.dumps(r["study"], ensure_ascii=False, default=str)[:300])):
                self.t_changes.setItem(i, j, QTableWidgetItem(x))
        self.t_changes.resizeColumnsToContents()
        self.tabs.setTabText(self.tabs.count() - 1, tr(f"변경 목록 ({len(rows)})", f"changes ({len(rows)})"))

    def change_count(self) -> int:
        try:
            return len(change_rows(self.base, self.overrides()))
        except InputValidationError:
            return 0


class VariantField(QWidget):
    """The design variant a run uses, as an input of the page: one line for people (the change count), a hidden line
    for the input tracking (the change count and a digest of the variant, so an edit in the editor marks a shown
    result as computed from earlier inputs) and the whole variant for the workspace (``workspace_state``).
    ``restore_hook(overrides) -> problems`` puts a saved variant back into the editor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ov: dict = {}
        self.restore_hook = None
        self.sig = QLineEdit()
        self.sig.setVisible(False)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.RichText)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)
        lay.addWidget(self.sig)

    def set_variant(self, overrides: dict, n_changes: int, error: str | None = None, note: str = ""):
        import hashlib
        self._ov = copy.deepcopy(overrides or {})
        digest = hashlib.sha256(json.dumps(self._ov, sort_keys=True, default=str).encode()).hexdigest()[:8]
        if error:
            self.sig.setText(tr(f"설계 오류 · {digest}", f"design error · {digest}"))
            self.label.setText(tr(f"<b style='color:#cf222e'>설계 오류</b> — 실행 전 편집 탭에서 고치세요: {error}",
                                  f"<b style='color:#cf222e'>design error</b> — fix it in the editor tab before a "
                                  f"run: {error}"))
        elif n_changes:
            self.sig.setText(tr(f"변경 {n_changes}건 · {digest}", f"{n_changes} change(s) · {digest}"))
            self.label.setText(tr(f"프로젝트 설계 + <b>변경 {n_changes}건</b> (편집 탭의 '변경 목록')",
                                  f"the project's design + <b>{n_changes} change(s)</b> (the editor's 'changes' list)"))
        else:
            self.sig.setText(tr("프로젝트 설계", "the project's design"))
            self.label.setText(tr("프로젝트 설계 그대로 (변경 없음)", "the project's design as it is (no changes)"))
        if note:
            self.label.setText(self.label.text() + "<br><span style='color:#b7791f'>" + note + "</span>")

    def overrides(self) -> dict:
        return copy.deepcopy(self._ov)

    # workspace: the whole variant (the hidden line only summarises it)
    def workspace_state(self) -> dict:
        return {"overrides": copy.deepcopy(self._ov)}

    def restore_workspace(self, v) -> list:
        ov = (v or {}).get("overrides") if isinstance(v, dict) else None
        if self.restore_hook is None:
            return []
        return list(self.restore_hook(ov or {}) or [])


__all__ = ["DesignEditor", "RecordTable", "ParamForm", "Col", "VariantField", "strategy_summary"]
