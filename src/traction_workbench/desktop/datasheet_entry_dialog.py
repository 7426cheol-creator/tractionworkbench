"""Datasheet value entry: representative values typed from a datasheet's characteristic tables (motor, power module,
DC-link capacitor, gate dv/dt) -> the same spec the datasheet import reads (``datasheet.apply``), a live preview of
the model the DECLARED construction rules build, every assumption as a finding, and 'apply' (a modified working copy
of the active project).

A typed value fixes a point, not a curve or a machine: the rules are the ones of ``traction_workbench.datasheet``
(threshold + slope, two points, R_DS(on); E ~ I^k; one ESR over a declared band; the Ke / R / L conversions of the
drive parser).  Conventions that change a value by sqrt 2, sqrt 3 or 2 (voltage basis, resistance reference, current
basis, energy basis) have no default: they must be chosen.  A required number left at zero reads 'not entered' and is
refused instead of entering the model as 0."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import datasheet as DS
from ..examples import examples_dir
from ..i18n import tr
from ..plots import datasheet_figures as DF
from ..project import short
from .widgets import (KeyValueTable, NumTable, PlotPanel, combo, error_box, fmt, hint, integer, number, primary_button,
                      table_with_buttons, tidy_inputs)

EXAMPLES = {"motor": "motor_example.json", "module": "module_representative_example.json",
            "capacitor": "capacitor_representative_example.json", "gate_edges": "gate_edges_example.json"}
LEVEL_COLOR = {"ERROR": "#cf222e", "WARNING": "#9a6700"}


def _not_entered() -> str:
    return tr("미입력", "not entered")


def _choose() -> str:
    return tr("— 선택 —", "— choose —")


def _required(w, name: str) -> float:
    """A required number: the minimum (0) shows 'not entered' and is refused, never used as 0."""
    if w.value() <= w.minimum():
        raise ValueError(tr(f"{name}: 값을 입력하세요", f"{name}: enter a value"))
    return float(w.value())


def _chosen(w: QComboBox, name: str):
    if w.currentData() is None:
        raise ValueError(tr(f"{name}: 정의를 선택하세요 (값이 √2·√3·2배 달라지는 규약이라 기본값이 없습니다)",
                            f"{name}: choose the definition (it changes the value by sqrt 2, sqrt 3 or 2, so there is "
                            "no default)"))
    return w.currentData()


def _set_combo(w: QComboBox, data) -> None:
    for i in range(w.count()):
        if w.itemData(i) == data:
            w.setCurrentIndex(i)
            return


def _list(v, n: int) -> list:
    if isinstance(v, (list, tuple)):
        return list(v) + [None] * (n - len(v))
    return [v] * n


class _Opt(QWidget):
    """An optional number: unticked = not declared (None), never a silent 0."""

    def __init__(self, value: float, lo: float, hi: float, suffix: str = "", decimals: int = 2, on: bool = False,
                 tip: str = ""):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.box = QCheckBox(tr("선언", "declared"))
        self.box.setChecked(on)
        self.num = number(value, lo, hi, suffix, decimals, tip=tip)
        self.num.setEnabled(on)
        self.box.toggled.connect(self.num.setEnabled)
        h.addWidget(self.box)
        h.addWidget(self.num, 1)

    def value(self):
        return float(self.num.value()) if self.box.isChecked() else None

    def set(self, v) -> None:
        self.box.setChecked(v is not None)
        if v is not None:
            self.num.setValue(float(v))


class _Part(QGroupBox):
    """Datasheet identity - the provenance of the section (part number and revision are required)."""

    def __init__(self):
        super().__init__(tr("데이터시트", "datasheet"))
        f = QFormLayout(self)
        self.maker, self.pn, self.rev, self.doc = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.pn.setPlaceholderText(tr("필수", "required"))
        self.rev.setPlaceholderText(tr("필수 (데이터시트 개정)", "required (datasheet revision)"))
        self.doc.setPlaceholderText(tr("선택: 문서 번호, 표·페이지", "optional: document number, table / page"))
        self.kind = combo([(tr("대표값 (typical)", "typical"), "typical"), (tr("최대값 (max)", "max"), "max")])
        f.addRow(tr("제조사", "manufacturer"), self.maker)
        f.addRow(tr("품번", "part number"), self.pn)
        f.addRow(tr("개정", "revision"), self.rev)
        f.addRow(tr("문서", "document"), self.doc)
        f.addRow(tr("값 종류", "value kind"), self.kind)

    def spec(self) -> tuple[dict, str]:
        part = {"manufacturer": self.maker.text().strip(), "part_number": self.pn.text().strip(),
                "revision": self.rev.text().strip()}
        if self.doc.text().strip():
            part["document"] = self.doc.text().strip()
        return part, self.kind.currentData()

    def load(self, part: dict, value_kind: str | None) -> None:
        self.maker.setText(str(part.get("manufacturer") or ""))
        self.pn.setText(str(part.get("part_number") or ""))
        self.rev.setText(str(part.get("revision") or ""))
        self.doc.setText(str(part.get("document") or ""))
        _set_combo(self.kind, value_kind or "typical")


def _group(title: str) -> tuple[QGroupBox, QFormLayout]:
    g = QGroupBox(title)
    return g, QFormLayout(g)


def _rows_by_choice(form: QFormLayout, choice: QComboBox, groups: dict) -> None:
    """Show only the rows of the chosen option (one form, so every label stays in the same column)."""
    def show(*_):
        for key, widgets in groups.items():
            for w in widgets:
                form.setRowVisible(w, key == choice.currentData())
    choice.currentIndexChanged.connect(show)
    show()


class _Form(QWidget):
    """One datasheet kind: ``spec()`` builds the import spec, ``load(spec)`` fills the form.  Keys of a loaded spec
    that the form does not show are kept unchanged (listed under the form), never dropped."""

    kind = ""
    shown: tuple = ()

    def __init__(self):
        super().__init__()
        self.extra: dict = {}
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 4, 0)
        self.part = _Part()
        self.body.addWidget(self.part)
        self.kept = hint("")
        self.kept.setVisible(False)

    def finish(self, note: str) -> None:
        self.body.addWidget(hint(note))
        self.body.addWidget(self.kept)
        self.body.addStretch(1)

    def keep_extra(self, spec: dict, shown: tuple) -> None:
        self.extra = {k: copy.deepcopy(v) for k, v in spec.items()
                      if k not in shown and k not in ("kind", "part", "value_kind", "note")}
        self.kept.setText(tr("양식에 없는 항목은 불러온 값 그대로 유지: ", "kept unchanged from the loaded spec (not in "
                                                                  "this form): ") + ", ".join(sorted(self.extra)))
        self.kept.setVisible(bool(self.extra))

    def base(self) -> dict:
        part, vk = self.part.spec()
        return {**copy.deepcopy(self.extra), "kind": self.kind, "part": part, "value_kind": vk}


# -- motor ------------------------------------------------------------------------------------------------------------

class MotorForm(_Form):
    kind = "motor"

    def __init__(self):
        super().__init__()
        self.motor_extra: dict = {}
        g, f = _group(tr("모터 특성값 (상수 dq 모델, D1)", "motor characteristic values (constant dq model, D1)"))
        self.poles = integer(0, 0, 96, tr("극", "poles"))
        self.poles.setSingleStep(2)
        self.poles.setSpecialValueText(_not_entered())
        f.addRow(tr("극수 (극쌍 아님)", "poles (not pole pairs)"), self.poles)
        self.conn = combo([(tr("Y 결선", "wye"), "wye"), (tr("Y 등가 (다른 결선의 등가값)", "wye equivalent"),
                                                           "wye_equivalent")])
        self.conn_note = QLineEdit()
        self.conn_note.setPlaceholderText(tr("Y 등가: 정규화 방법 (필수)", "wye equivalent: how it was normalised "
                                                                      "(required)"))
        self.conn.currentIndexChanged.connect(lambda _i: self.conn_note.setEnabled(self.conn.currentData() != "wye"))
        self.conn_note.setEnabled(False)
        f.addRow(tr("결선", "connection"), self.conn)
        f.addRow("", self.conn_note)
        self.flux = combo([(tr("Ke 역기전력 상수", "Ke back-EMF constant"), "Ke"),
                           (tr("Kt 토크 상수", "Kt torque constant"), "Kt"),
                           (tr("ψ_PM 직접", "psi_PM directly"), "psi")])
        f.addRow(tr("영구자석 자속", "PM flux given as"), self.flux)
        self.ke = number(0.0, 0.0, 1e5, "V", 4)
        self.ke.setSpecialValueText(_not_entered())
        self.ke_basis = combo([(_choose(), None), (tr("선간 RMS", "line-to-line RMS"), "line_line_rms"),
                               (tr("선간 피크", "line-to-line peak"), "line_line_peak"),
                               (tr("상 RMS", "phase RMS"), "phase_rms"), (tr("상 피크", "phase peak"), "phase_peak")])
        self.ke_speed = number(1000.0, 1.0, 1e6, "rpm", 1)
        self.ke_kind = combo([(tr("기계 속도", "mechanical"), "mechanical"), (tr("전기 속도", "electrical"),
                                                                             "electrical")])
        f.addRow("Ke", self.ke)
        f.addRow(tr("전압 정의", "voltage basis"), self.ke_basis)
        f.addRow(tr("기준 속도 (당)", "per speed"), self.ke_speed)
        f.addRow(tr("속도 종류", "speed kind"), self.ke_kind)
        self.kt = number(0.0, 0.0, 1e3, "N·m/A", 5)
        self.kt.setSpecialValueText(_not_entered())
        self.kt_basis = combo([(_choose(), None), (tr("RMS 전류당", "per RMS ampere"), "fundamental_rms"),
                               (tr("피크 전류당", "per peak ampere"), "fundamental_peak")])
        self.kt_def = QCheckBox(tr("id = 0 에서 전자기 토크 / 선전류로 정의된 값 (손실·포화 제외)",
                                   "defined as electromagnetic torque per line current at id = 0 (no losses, no "
                                   "saturation)"))
        self.kt_def.setToolTip(tr("토크-전류 곡선에서 읽은 축 토크 Kt는 손실과 포화를 포함하므로 ψ_PM으로 바꾸지 않습니다.",
                                  "a shaft Kt read off a torque-current curve contains losses and saturation and is "
                                  "not converted to psi_PM"))
        f.addRow("Kt", self.kt)
        f.addRow(tr("전류 정의", "current basis"), self.kt_basis)
        f.addRow(self.kt_def)
        self.psi = number(0.0, 0.0, 1e4, "mWb", 4)
        self.psi.setSpecialValueText(_not_entered())
        f.addRow(tr("ψ_PM (상 피크, 진폭 불변 dq)", "psi_PM (phase peak, amplitude-invariant dq)"), self.psi)
        _rows_by_choice(f, self.flux, {"Ke": (self.ke, self.ke_basis, self.ke_speed, self.ke_kind),
                                       "Kt": (self.kt, self.kt_basis, self.kt_def), "psi": (self.psi,)})
        self.rs = number(0.0, 0.0, 1e5, "mΩ", 4)
        self.rs.setSpecialValueText(_not_entered())
        self.rs_ref = combo([(_choose(), None), (tr("선간 측정값 (Y: ÷2)", "line-to-line (wye: / 2)"), "line_to_line"),
                             (tr("상당 (per phase)", "per phase"), "per_phase")])
        self.rs_T = _Opt(20.0, -60.0, 250.0, "°C", 1)
        f.addRow(tr("권선 저항 R", "winding resistance R"), self.rs)
        f.addRow(tr("R 측정 기준", "R reference"), self.rs_ref)
        f.addRow(tr("R 기준 온도", "R reference temperature"), self.rs_T)
        self.ld = number(0.0, 0.0, 1e4, "mH", 5)
        self.lq = number(0.0, 0.0, 1e4, "mH", 5)
        for w_ in (self.ld, self.lq):
            w_.setSpecialValueText(_not_entered())
        f.addRow("Ld", self.ld)
        f.addRow("Lq", self.lq)
        self.mag_T = _Opt(20.0, -60.0, 250.0, "°C", 1)
        f.addRow(tr("자속 기준 자석 온도", "magnet temperature of psi_PM"), self.mag_T)
        self.body.addWidget(g)
        g, f = _group(tr("회전 손실 (축 토크·전력에 필요)", "rotational loss (needed for shaft torque and power)"))
        self.rot = combo([(tr("미선언 → 판정 UNKNOWN", "not declared -> decisions UNKNOWN"), "none"),
                          (tr("무부하 손실 (한 속도)", "no-load loss at one speed"), "no_load"),
                          (tr("점성 계수 b", "viscous coefficient b"), "viscous")])
        f.addRow(tr("모델", "model"), self.rot)
        rot_none = hint(tr("축 토크 = 전자기 토크 − 회전 손실 토크: 미선언이면 요구 판정은 UNKNOWN입니다.",
                           "shaft torque = electromagnetic - rotational-loss torque: without it the requirement "
                           "decisions stay UNKNOWN."))
        f.addRow(rot_none)
        self.p0 = number(0.0, 0.0, 1e6, "W", 2)
        self.p0.setSpecialValueText(_not_entered())
        self.n0 = number(0.0, 0.0, 1e6, "rpm", 0)
        self.n0.setSpecialValueText(_not_entered())
        f.addRow(tr("무부하 손실 P0", "no-load loss P0"), self.p0)
        f.addRow(tr("측정 속도", "at speed"), self.n0)
        rot_note = hint(tr("b = P0 / ω0² (손실 ∝ ω², 점성 모델) — 한 점에서 모양을 정하는 선언입니다.",
                           "b = P0 / omega0^2 (loss ~ omega^2, viscous) - one point, the shape is a declaration."))
        f.addRow(rot_note)
        self.b = number(0.0, 0.0, 100.0, "N·m·s/rad", 6)
        self.b.setSpecialValueText(_not_entered())
        f.addRow("b", self.b)
        self.iron = QCheckBox(tr("철손 포함 (데이터시트 무부하 손실이 철손을 포함)", "includes iron loss (the datasheet "
                                                                     "no-load loss contains it)"))
        f.addRow(self.iron)
        _rows_by_choice(f, self.rot, {"none": (rot_none,), "no_load": (self.p0, self.n0, rot_note, self.iron),
                                      "viscous": (self.b, self.iron)})
        self.body.addWidget(g)
        self.finish(tr("인버터(전류 한계·전압 여유·손실)와 허용 운전 영역은 모터 데이터가 아니므로 현재 프로젝트 드라이브의 값을 "
                       "유지합니다 (기록에 표시). 포화·교차 결합이 필요한 영역은 flux map(D2) 드라이브 파일로 가져오세요.",
                       "The inverter (current limit, voltage reserve, loss) and the allowed operating domain are not "
                       "motor data: they are kept from the project's drive (said in the findings). For saturation / "
                       "cross-coupling import a flux-map (D2) drive file."))

    SHOWN_MOTOR = ("poles", "pole_pairs", "connection", "connection_note", "Ke", "Kt", "psi_pm", "Rs", "Ld", "Lq",
                   "reference_temperatures", "rotational_loss", "model")

    def spec(self) -> dict:
        d = self.base()
        m = copy.deepcopy(self.motor_extra)
        m["poles"] = int(_required(self.poles, tr("극수", "poles")))
        m["connection"] = self.conn.currentData()
        if m["connection"] != "wye":
            m["connection_note"] = self.conn_note.text().strip()
        fl = self.flux.currentData()
        if fl == "Ke":
            m["Ke"] = {"value": _required(self.ke, "Ke"), "unit": "V", "basis": _chosen(self.ke_basis, "Ke"),
                       "per_speed": {"value": float(self.ke_speed.value()), "unit": "rpm",
                                     "kind": self.ke_kind.currentData()}}
        elif fl == "Kt":
            m["Kt"] = {"value": _required(self.kt, "Kt"), "unit": "N*m/A", "current_basis": _chosen(self.kt_basis, "Kt"),
                       "current_reference": "line"}
            if self.kt_def.isChecked():
                m["Kt"].update(definition="shaft_or_em_torque_per_current_at_id0", torque="electromagnetic")
        else:
            m["psi_pm"] = {"value": _required(self.psi, "ψ_PM"), "unit": "mWb", "basis": "phase_peak"}
        m["Rs"] = {"value": _required(self.rs, "R"), "unit": "mohm", "reference": _chosen(self.rs_ref, "R")}
        m["Ld"] = {"value": _required(self.ld, "Ld"), "unit": "mH"}
        m["Lq"] = {"value": _required(self.lq, "Lq"), "unit": "mH"}
        temps = {k: v for k, v in (("winding_C", self.rs_T.value()), ("magnet_C", self.mag_T.value())) if v is not None}
        if temps:
            m["reference_temperatures"] = temps
        rot = self.rot.currentData()
        if rot == "no_load":
            m["rotational_loss"] = {"no_load_loss": {"P_W": _required(self.p0, "P0"),
                                                     "speed_rpm": _required(self.n0, tr("측정 속도", "speed"))},
                                    "includes_iron_loss": self.iron.isChecked()}
        elif rot == "viscous":
            m["rotational_loss"] = {"viscous": {"value": _required(self.b, "b"), "unit": "N*m/(rad/s)"},
                                    "includes_iron_loss": self.iron.isChecked(),
                                    "basis": "datasheet viscous coefficient"}
        d["motor"] = m
        return d

    def load(self, spec: dict) -> None:
        from ..units import SCALE
        self.keep_extra(spec, ("motor",))
        self.part.load(spec.get("part") or {}, spec.get("value_kind"))
        m = copy.deepcopy(spec.get("motor") or {})
        if m.get("model", "constant_dq") != "constant_dq":
            raise ValueError(tr("flux map 모터는 드라이브 파일로 가져오세요 (모델 페이지)", "a flux-map motor is imported "
                                                                             "as a drive file (model page)"))
        self.motor_extra = {k: v for k, v in m.items() if k not in self.SHOWN_MOTOR}
        if self.motor_extra:
            self.kept.setText(self.kept.text() + ("; " if self.extra else tr("양식에 없는 항목은 그대로 유지: ",
                                                                             "kept unchanged: "))
                              + "motor." + ", motor.".join(sorted(self.motor_extra)))
            self.kept.setVisible(True)
        self.poles.setValue(int(m["poles"]) if "poles" in m else 2 * int(m.get("pole_pairs") or 0))
        _set_combo(self.conn, m.get("connection", "wye"))
        self.conn_note.setText(str(m.get("connection_note") or ""))

        def si(obj, kind):
            return float(obj["value"]) * SCALE[kind][str(obj["unit"]).strip()]
        if "Ke" in m:
            ke = m["Ke"]
            self.flux.setCurrentIndex(0)
            self.ke.setValue(si(ke, "voltage"))
            _set_combo(self.ke_basis, ke.get("basis"))
            per = ke.get("per_speed") or {}
            k = {"rpm": 1.0, "rad/s": 60.0 / (2 * math.pi), "Hz": 60.0}.get(per.get("unit"), 1.0)
            self.ke_speed.setValue(float(per.get("value") or 1000.0) * k)
            _set_combo(self.ke_kind, per.get("kind", "mechanical"))
        elif "Kt" in m:
            kt = m["Kt"]
            self.flux.setCurrentIndex(1)
            from ..units import KT_UNITS
            self.kt.setValue(float(kt["value"]) * KT_UNITS.get(str(kt.get("unit", "N*m/A")), 1.0))
            _set_combo(self.kt_basis, kt.get("current_basis"))
            self.kt_def.setChecked(kt.get("definition") == "shaft_or_em_torque_per_current_at_id0"
                                   and kt.get("torque") == "electromagnetic")
        elif "psi_pm" in m:
            self.flux.setCurrentIndex(2)
            self.psi.setValue(si(m["psi_pm"], "flux") * 1e3)
        if "Rs" in m:
            self.rs.setValue(si(m["Rs"], "resistance") * 1e3)
            _set_combo(self.rs_ref, m["Rs"].get("reference"))
        for w_, key in ((self.ld, "Ld"), (self.lq, "Lq")):
            if key in m:
                w_.setValue(si(m[key], "inductance") * 1e3)
        rt = m.get("reference_temperatures") or {}
        self.rs_T.set(rt.get("winding_C"))
        self.mag_T.set(rt.get("magnet_C"))
        rl = m.get("rotational_loss")
        if not rl:
            self.rot.setCurrentIndex(0)
        elif rl.get("no_load_loss"):
            self.rot.setCurrentIndex(1)
            self.p0.setValue(float(rl["no_load_loss"].get("P_W") or 0.0))
            self.n0.setValue(float(rl["no_load_loss"].get("speed_rpm") or 0.0))
        elif rl.get("viscous"):
            self.rot.setCurrentIndex(2)
            self.b.setValue(si(rl["viscous"], "drag"))
        if rl:
            self.iron.setChecked(bool(rl.get("includes_iron_loss", False)))


# -- power module ---------------------------------------------------------------------------------------------------

def _cond_models():
    return [(tr("임계 V0 + I_nom에서의 전압", "threshold V0 + voltage at I_nom"), "v0_vnom"),
            (tr("임계 V0 + 기울기 r", "threshold V0 + slope r"), "v0_r"),
            (tr("출력 특성의 두 점", "two points of the output characteristic"), "two_points"),
            (tr("저항 R (R_DS(on))", "resistance R (R_DS(on))"), "resistance")]


class _Cond(QWidget):
    """Conduction model choice of one device; its two table columns change meaning with it."""

    def __init__(self, default: str):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.model = combo(_cond_models(), default)
        v.addWidget(self.model)
        self.pts = QWidget()
        h = QHBoxLayout(self.pts)
        h.setContentsMargins(0, 0, 0, 0)
        self.i1 = number(0.0, 0.0, 1e5, "A", 1)
        self.i2 = number(0.0, 0.0, 1e5, "A", 1)
        for w_ in (self.i1, self.i2):
            w_.setSpecialValueText(_not_entered())
        h.addWidget(QLabel("I1"))
        h.addWidget(self.i1, 1)
        h.addWidget(QLabel("I2"))
        h.addWidget(self.i2, 1)
        v.addWidget(self.pts)
        self.model.currentIndexChanged.connect(lambda _i: self.pts.setVisible(self.model.currentData() == "two_points"))
        self.pts.setVisible(default == "two_points")

    def headers(self, dev: str) -> tuple[str, str]:
        return {"v0_vnom": (f"{dev}\nV0 [V]", f"{dev}\nV@I_nom [V]"), "v0_r": (f"{dev}\nV0 [V]", f"{dev}\nr [mΩ]"),
                "two_points": (f"{dev}\nV@I1 [V]", f"{dev}\nV@I2 [V]"),
                "resistance": (f"{dev}\nR [mΩ]", f"{dev}\n—")}[self.model.currentData()]

    def needs_b(self) -> bool:
        return self.model.currentData() != "resistance"

    def spec(self, a: list, b: list) -> dict:
        m = self.model.currentData()
        if m == "v0_vnom":
            return {"model": "threshold_slope", "V0_V": a, "V_nom_V": b}
        if m == "v0_r":
            return {"model": "threshold_slope", "V0_V": a, "r_mohm": b}
        if m == "two_points":
            return {"model": "two_points", "I_A": [_required(self.i1, "I1"), _required(self.i2, "I2")], "V_V": [a, b]}
        return {"model": "resistance", "R_mohm": a}

    def load(self, d: dict, n: int) -> tuple[list, list]:
        mod = d.get("model")
        if mod == "threshold_slope" and d.get("r_mohm") is not None:
            _set_combo(self.model, "v0_r")
            return _list(d.get("V0_V"), n), _list(d.get("r_mohm"), n)
        if mod == "threshold_slope":
            _set_combo(self.model, "v0_vnom")
            return _list(d.get("V0_V"), n), _list(d.get("V_nom_V"), n)
        if mod == "two_points":
            _set_combo(self.model, "two_points")
            cur = list(d.get("I_A") or [0.0, 0.0]) + [0.0, 0.0]
            self.i1.setValue(float(cur[0] or 0.0))
            self.i2.setValue(float(cur[1] or 0.0))
            vv = list(d.get("V_V") or [[], []]) + [[], []]
            return _list(vv[0], n), _list(vv[1], n)
        if mod == "resistance":
            _set_combo(self.model, "resistance")
            return _list(d.get("R_mohm"), n), [None] * n
        raise ValueError(tr(f"도통 모델 {mod!r}은 이 양식에 없습니다", f"conduction model {mod!r} is not in this form"))


class ModuleForm(_Form):
    kind = "module"
    SHOWN = ("technology", "energy_basis", "v_test_V", "test_conditions", "representative", "thermal_path",
             "zth_foster")
    COLS = 9                  # Tj | switch a b | reverse a b | channel R | E_on E_off E_rr

    def __init__(self):
        super().__init__()
        g, f = _group(tr("소자와 시험 조건", "device and test conditions"))
        self.tech = combo([("IGBT", "IGBT"), ("SiC MOSFET", "SiC_MOSFET")])
        self.basis = combo([(_choose(), None), (tr("소자별 (per device)", "per device"), "per_device"),
                            (tr("정류 쌍 합계 (E_rr 포함)", "commutation-pair total (E_rr inside)"),
                             "commutation_pair_total")])
        self.basis.setToolTip(tr("E_on/E_off가 한 소자의 값인지, 정류하는 쌍(스위치 + 상대 다이오드 회복)의 합인지 데이터시트의 "
                                 "정의를 따르세요.", "whether E_on / E_off belong to one device or to the commutating "
                                                   "pair (switch + recovery of the opposite diode): follow the "
                                                   "datasheet's definition"))
        self.v_test = number(0.0, 0.0, 5000.0, "V", 1)
        self.v_test.setSpecialValueText(_not_entered())
        self.rg_on = _Opt(0.0, 0.0, 1e3, "Ω", 3)
        self.rg_off = _Opt(0.0, 0.0, 1e3, "Ω", 3)
        self.vge = _Opt(15.0, -30.0, 30.0, "V", 1)
        f.addRow(tr("기술", "technology"), self.tech)
        f.addRow(tr("에너지 기준", "energy basis"), self.basis)
        f.addRow(tr("스위칭 시험 전압 V_test", "switching test voltage V_test"), self.v_test)
        f.addRow("Rg,on", self.rg_on)
        f.addRow("Rg,off", self.rg_off)
        f.addRow("V_GE / V_GS", self.vge)
        self.body.addWidget(g)
        g, f = _group(tr("도통 모델과 전류 범위", "conduction models and current range"))
        self.i_nom = number(0.0, 0.0, 1e5, "A", 1)
        self.i_max = number(0.0, 0.0, 1e5, "A", 1)
        for w_ in (self.i_nom, self.i_max):
            w_.setSpecialValueText(_not_entered())
        self.i_max.setToolTip(tr("곡선이 끝나는 전류 (예: 반복 피크 전류 I_CRM): 그 위의 손실은 확립되지 않습니다(UNKNOWN).",
                                 "where the curves end (e.g. the repetitive peak current I_CRM): above it the losses "
                                 "are not established (UNKNOWN)"))
        f.addRow(tr("정격 전류 I_nom", "nominal current I_nom"), self.i_nom)
        f.addRow(tr("곡선 범위 끝 I_max", "curve range end I_max"), self.i_max)
        self.sw = _Cond("v0_vnom")
        self.rev = _Cond("v0_vnom")
        f.addRow(tr("스위치 도통", "switch conduction"), self.sw)
        f.addRow(tr("역방향 (다이오드/바디 다이오드)", "reverse (diode / body diode)"), self.rev)
        self.channel = combo([(tr("순방향 채널과 대칭 (선언)", "same as the forward channel (declared)"), "symmetric"),
                              (tr("역방향 채널 R 입력 (표의 채널 열)", "reverse channel R typed (table column)"),
                               "typed")])
        self.channel_row = QLabel(tr("SiC 동기정류 채널", "SiC synchronous-rectification channel"))
        f.addRow(self.channel_row, self.channel)
        self.body.addWidget(g)
        g = QGroupBox(tr("접합 온도별 특성값 (데이터시트 표의 행 = 온도)", "characteristic values per junction temperature "
                                                              "(one row per temperature)"))
        gv = QVBoxLayout(g)
        self.table = NumTable([""] * self.COLS, [[25.0], [150.0]], min_height=110,
                              optional_cols=tuple(range(1, self.COLS)))
        gv.addWidget(table_with_buttons(self.table, tr("E_rr 빈칸 = 주지 않음 (IGBT 소자별 기준은 필요), 0 = 무시 가능하다고 "
                                                       "명시. 셀 블록을 스프레드시트에서 붙여넣을 수 있습니다.",
                                                       "E_rr blank = not given (per-device IGBT energies need it), 0 = "
                                                       "stated negligible. Paste a block from a spreadsheet.")))
        self.body.addWidget(g)
        g, f = _group(tr("스위칭 에너지의 전류 의존성 E(I) = E_ref (I / I_ref)^k", "current scaling E(I) = E_ref (I / I_ref)^k"))
        self.i_ref = _Opt(0.0, 0.0, 1e5, "A", 1)
        self.i_ref.box.setText(tr("I_nom과 다름", "differs from I_nom"))
        self.k_on = number(1.0, 0.05, 3.0, "", 3, 0.05)
        self.k_off = number(1.0, 0.05, 3.0, "", 3, 0.05)
        self.k_rr = number(1.0, 0.05, 3.0, "", 3, 0.05)
        self.k_basis = QLineEdit()
        self.k_basis.setPlaceholderText(tr("k ≠ 1이면 근거 (예: 데이터시트 E(I) 그림의 I_nom/2..I_nom 모양)",
                                           "basis when k != 1 (e.g. the shape of the datasheet E(I) figure)"))
        f.addRow(tr("에너지 시험 전류 I_ref", "energy test current I_ref"), self.i_ref)
        f.addRow("k_on", self.k_on)
        f.addRow("k_off", self.k_off)
        f.addRow("k_rr", self.k_rr)
        f.addRow(tr("지수 근거", "exponent basis"), self.k_basis)
        self.body.addWidget(g)
        g, f = _group(tr("열 경로 (접합 → 냉각수)", "thermal path (junction -> coolant)"))
        self.th = combo([(tr("프로젝트 값 유지", "keep the project's"), "keep"),
                         (tr("Rth 한 값 (정상 상태)", "one Rth (steady state)"), "rth"),
                         (tr("Foster 표 Zth (r_i, τ_i)", "Foster table Zth (r_i, tau_i)"), "foster")])
        f.addRow(tr("열 경로", "thermal path"), self.th)
        th_keep = hint(tr("모듈 데이터시트는 이 냉각 시스템의 접합-냉각수 경로를 주지 않는 경우가 많습니다: 프로젝트 값을 "
                          "유지합니다 (기록에 표시).", "a module datasheet often has no junction-to-coolant path for "
                                                  "this cooling system: the project's is kept (said in the findings)."))
        f.addRow(th_keep)
        self.rth = number(0.0, 0.0, 100.0, "K/W", 5)
        self.rth.setSpecialValueText(_not_entered())
        self.rth_tref = number(65.0, -60.0, 200.0, "°C", 1)
        self.rth_basis = QLineEdit()
        self.rth_basis.setPlaceholderText(tr("필수: 예) R_th(j-f), 유량 10 l/min", "required: e.g. R_th(j-f) at "
                                                                              "10 l/min"))
        f.addRow("Rth", self.rth)
        f.addRow(tr("냉각수 기준 온도", "coolant reference temperature"), self.rth_tref)
        f.addRow(tr("근거", "basis"), self.rth_basis)
        self.foster = NumTable(["r_i [K/W]", "τ_i [s]"], min_height=100)
        self.foster_ref = QLineEdit()
        self.foster_ref.setPlaceholderText(tr("필수: junction-to-case / junction-to-fluid, 유량", "required: junction-"
                                                                                           "to-case / -fluid, flow"))
        self.foster_extra = _Opt(0.0, 0.0, 10.0, "K/W", 5)
        self.foster_tau = number(30.0, 0.001, 1e4, "s", 3)
        self.foster_tref = number(65.0, -60.0, 200.0, "°C", 1)
        foster_box = table_with_buttons(self.foster)
        f.addRow(foster_box)
        f.addRow(tr("기준", "reference"), self.foster_ref)
        f.addRow(tr("케이스→냉각수 R 추가", "added case-to-coolant R"), self.foster_extra)
        f.addRow(tr("그 시정수", "its time constant"), self.foster_tau)
        f.addRow(tr("냉각수 기준 온도", "coolant reference temperature"), self.foster_tref)
        _rows_by_choice(f, self.th, {"keep": (th_keep,), "rth": (self.rth, self.rth_tref, self.rth_basis),
                                     "foster": (foster_box, self.foster_ref, self.foster_extra, self.foster_tau,
                                                self.foster_tref)})
        self.body.addWidget(g)
        self.finish(tr("값 하나는 한 점일 뿐입니다: 도통 곡선은 선언한 모델(직선)로, 에너지 곡선은 E ∝ I^k로 [0, I_max]에 만들어지고 "
                       "각 구성 규칙이 기록에 남습니다. I_max 위와 표에 없는 온도는 외삽하지 않습니다(UNKNOWN). 병렬 수·구동부 "
                       "손실·평가 온도는 데이터시트 값이 아니므로 프로젝트 값을 유지합니다.",
                       "One value is one point: the on-state curve is built by the declared model (a line), the "
                       "energies by E ~ I^k on [0, I_max], and every rule is a finding. Above I_max and outside the "
                       "table temperatures nothing is extrapolated (UNKNOWN). Parallel count, driver loss and the "
                       "evaluation temperature are not datasheet values and are kept from the project."))
        for w_ in (self.sw.model, self.rev.model, self.tech, self.channel):
            w_.currentIndexChanged.connect(self.relabel)
        self.relabel()

    def relabel(self, *_):
        sic = self.tech.currentData() == "SiC_MOSFET"
        self.channel.setVisible(sic)
        self.channel_row.setVisible(sic)
        sa, sb = self.sw.headers(tr("스위치", "switch"))
        ra, rb = self.rev.headers(tr("역방향", "reverse"))
        self.table.setHorizontalHeaderLabels(["Tj\n[°C]", sa, sb, ra, rb, tr("채널 역방향\nR [mΩ]", "reverse channel\n"
                                                                                           "R [mΩ]"),
                                              "E_on\n[mJ]", "E_off\n[mJ]", "E_rr\n[mJ]"])
        self.table.setColumnHidden(2, not self.sw.needs_b())
        self.table.setColumnHidden(4, not self.rev.needs_b())
        self.table.setColumnHidden(5, not (sic and self.channel.currentData() == "typed"))

    def spec(self) -> dict:
        d = self.base()
        rows = self.table.values()
        if not rows:
            raise ValueError(tr("특성값 표에 접합 온도 행이 없습니다", "the value table has no junction-temperature row"))

        def col(j: int, required: bool = True) -> list:
            vals = [r[j] for r in rows]
            if required and any(v is None for v in vals):
                name = self.table.horizontalHeaderItem(j).text().replace("\n", " ")
                raise ValueError(tr(f"특성값 표의 '{name}' 열에 빈 칸이 있습니다", f"column '{name}' of the value table "
                                                                          "has an empty cell"))
            return vals
        rep = {"temps_C": col(0), "I_nom_A": _required(self.i_nom, "I_nom"), "I_max_A": _required(self.i_max, "I_max"),
               "switch": self.sw.spec(col(1), col(2) if self.sw.needs_b() else None),
               "reverse": self.rev.spec(col(3), col(4) if self.rev.needs_b() else None)}
        if self.tech.currentData() == "SiC_MOSFET" and self.channel.currentData() == "typed":
            rep["channel_reverse"] = {"model": "resistance", "R_mohm": col(5)}
        en = {"E_on_mJ": col(6), "E_off_mJ": col(7), "k_on": float(self.k_on.value()), "k_off": float(self.k_off.value())}
        if self.i_ref.value() is not None:
            en["I_A"] = self.i_ref.value()
        err = col(8, required=False)
        if any(v is not None for v in err):
            if any(v is None for v in err):
                raise ValueError(tr("E_rr: 모든 온도에 값을 넣거나 모두 비우세요", "E_rr: give it at every temperature or "
                                                                          "leave it all blank"))
            en["E_rr_mJ"] = err
            en["k_rr"] = float(self.k_rr.value())
        if self.k_basis.text().strip():
            en["basis"] = self.k_basis.text().strip()
        rep["energies"] = en
        tc = {k: v for k, v in (("Rg_on_ohm", self.rg_on.value()), ("Rg_off_ohm", self.rg_off.value()),
                                ("Vge_V", self.vge.value())) if v is not None}
        d.update(technology=self.tech.currentData(), energy_basis=_chosen(self.basis, tr("에너지 기준", "energy basis")),
                 v_test_V=_required(self.v_test, "V_test"), test_conditions=tc, representative=rep)
        th = self.th.currentData()
        if th == "rth":
            d["thermal_path"] = {"Rth_K_per_W": _required(self.rth, "Rth"), "T_ref_C": float(self.rth_tref.value()),
                                 "basis": self.rth_basis.text().strip()}
        elif th == "foster":
            fr = self.foster.values()
            z = {"R_K_per_W": [r[0] for r in fr], "tau_s": [r[1] for r in fr],
                 "reference": self.foster_ref.text().strip(), "T_ref_C": float(self.foster_tref.value())}
            if self.foster_extra.value():
                z.update(extra_Rth_K_per_W=self.foster_extra.value(), extra_tau_s=float(self.foster_tau.value()))
            d["zth_foster"] = z
        return d

    def load(self, spec: dict) -> None:
        if spec.get("curves") and not spec.get("representative"):
            raise ValueError(tr("디지타이즈된 곡선 형식입니다: 프로젝트 페이지의 '데이터시트 파일 가져오기'로 여세요",
                                "this spec holds digitized curves: open it with 'import datasheet file' on the project "
                                "page"))
        self.keep_extra(spec, self.SHOWN)
        self.part.load(spec.get("part") or {}, spec.get("value_kind"))
        _set_combo(self.tech, spec.get("technology", "IGBT"))
        _set_combo(self.basis, spec.get("energy_basis"))
        self.v_test.setValue(float(spec.get("v_test_V") or 0.0))
        tc = spec.get("test_conditions") or {}
        self.rg_on.set(tc.get("Rg_on_ohm"))
        self.rg_off.set(tc.get("Rg_off_ohm"))
        self.vge.set(tc.get("Vge_V"))
        rep = spec.get("representative") or {}
        temps = _list(rep.get("temps_C"), 1) if not isinstance(rep.get("temps_C"), list) else list(rep["temps_C"])
        n = len(temps)
        self.i_nom.setValue(float(rep.get("I_nom_A") or 0.0))
        self.i_max.setValue(float(rep.get("I_max_A") or 0.0))
        sa, sb = self.sw.load(rep.get("switch") or {"model": "threshold_slope"}, n)
        ra, rb = self.rev.load(rep.get("reverse") or {"model": "threshold_slope"}, n)
        ch = [None] * n
        if rep.get("channel_reverse"):
            if rep["channel_reverse"].get("model") != "resistance":
                raise ValueError(tr("채널 역방향은 이 양식에서 R로만 입력합니다", "the reverse channel is entered as R in "
                                                                       "this form"))
            ch = _list(rep["channel_reverse"].get("R_mohm"), n)
        _set_combo(self.channel, "typed" if rep.get("channel_reverse") else "symmetric")
        en = rep.get("energies") or {}
        eon, eoff = _list(en.get("E_on_mJ"), n), _list(en.get("E_off_mJ"), n)
        err = _list(en.get("E_rr_mJ"), n) if en.get("E_rr_mJ") is not None else [None] * n
        self.table.load([[temps[t], sa[t], sb[t], ra[t], rb[t], ch[t], eon[t], eoff[t], err[t]] for t in range(n)])
        self.i_ref.set(en.get("I_A") if en.get("I_A") not in (None, rep.get("I_nom_A")) else None)
        for w_, key in ((self.k_on, "k_on"), (self.k_off, "k_off"), (self.k_rr, "k_rr")):
            w_.setValue(float(en.get(key, 1.0)))
        self.k_basis.setText(str(en.get("basis") or ""))
        if spec.get("thermal_path"):
            tp = spec["thermal_path"]
            self.th.setCurrentIndex(1)
            self.rth.setValue(float(tp.get("Rth_K_per_W") or 0.0))
            self.rth_tref.setValue(float(tp.get("T_ref_C", 65.0)))
            self.rth_basis.setText(str(tp.get("basis") or ""))
        elif spec.get("zth_foster"):
            z = spec["zth_foster"]
            self.th.setCurrentIndex(2)
            self.foster.load([[r, t] for r, t in zip(z.get("R_K_per_W") or [], z.get("tau_s") or [])])
            self.foster_ref.setText(str(z.get("reference") or ""))
            self.foster_extra.set(z.get("extra_Rth_K_per_W") or None)
            self.foster_tau.setValue(float(z.get("extra_tau_s") or 30.0))
            self.foster_tref.setValue(float(z.get("T_ref_C", 65.0)))
        else:
            self.th.setCurrentIndex(0)
        self.relabel()


# -- DC-link capacitor --------------------------------------------------------------------------------------------------

class CapacitorForm(_Form):
    kind = "capacitor"
    SHOWN = ("C_uF", "ESL_nH", "rated_voltage_V", "ESR_representative", "ESR_table", "ESR_unit", "ESR_table_T_C",
             "ESR_temp_coeff_per_K", "T_valid_C", "Rth_K_per_W", "T_ref_C", "Rth_basis", "life_hours_table",
             "life_voltage_V", "life_basis")

    def __init__(self):
        super().__init__()
        g, f = _group(tr("커패시터 특성값", "capacitor characteristic values"))
        self.c = number(0.0, 0.0, 1e6, "µF", 2)
        self.c.setSpecialValueText(_not_entered())
        self.esl = _Opt(0.0, 0.0, 1e4, "nH", 3)
        self.v_rated = _Opt(0.0, 0.0, 1e5, "V", 1)
        f.addRow(tr("정전 용량 C", "capacitance C"), self.c)
        f.addRow("ESL", self.esl)
        f.addRow(tr("정격 전압", "rated voltage"), self.v_rated)
        self.esr_mode = combo([(tr("대표값 한 개 + 유효 대역 선언", "one value + declared band"), "rep"),
                               (tr("ESR(f) 표", "ESR(f) table"), "table")])
        f.addRow(tr("ESR 입력", "ESR given as"), self.esr_mode)
        self.esr = number(0.0, 0.0, 1e4, "mΩ", 4)
        self.esr.setSpecialValueText(_not_entered())
        self.esr_f = number(0.0, 0.0, 1e8, "Hz", 1)
        self.esr_f.setSpecialValueText(_not_entered())
        self.band_lo = number(0.0, 0.0, 1e9, "Hz", 1)
        self.band_hi = number(0.0, 0.0, 1e9, "Hz", 1)
        for w_ in (self.band_lo, self.band_hi):
            w_.setSpecialValueText(_not_entered())
        self.band_basis = QLineEdit()
        self.band_basis.setPlaceholderText(tr("이 대역에서 한 값이 성립하는 근거 (ESR(f)·tanδ 그림)", "why one value holds over the "
                                                                                     "band (ESR(f) / tan delta figure)"))
        f.addRow("ESR", self.esr)
        f.addRow(tr("측정 주파수", "at frequency"), self.esr_f)
        f.addRow(tr("유효 대역 하한", "band low"), self.band_lo)
        f.addRow(tr("유효 대역 상한", "band high"), self.band_hi)
        f.addRow(tr("대역 근거", "band basis"), self.band_basis)
        band_note = hint(tr("리플 전류의 고조파는 스위칭 주파수의 수십 배까지 흐릅니다: 대역 밖 고조파가 있으면 ESR 손실은 "
                            "UNKNOWN (추정하지 않음).", "ripple harmonics reach tens of times the switching frequency: "
                                                     "with a current-carrying harmonic outside the band the ESR loss "
                                                     "is UNKNOWN (never estimated)."))
        f.addRow(band_note)
        self.esr_table = NumTable([tr("주파수 [Hz]", "frequency [Hz]"), "ESR [mΩ]"], min_height=110)
        esr_box = table_with_buttons(self.esr_table)
        f.addRow(esr_box)
        _rows_by_choice(f, self.esr_mode, {"rep": (self.esr, self.esr_f, self.band_lo, self.band_hi, self.band_basis,
                                                   band_note), "table": (esr_box,)})
        self.esr_T = _Opt(25.0, -60.0, 200.0, "°C", 1)
        self.esr_alpha = _Opt(0.0, -0.1, 0.1, "1/K", 5)
        f.addRow(tr("ESR 기준 온도", "ESR temperature"), self.esr_T)
        f.addRow(tr("ESR 온도 계수", "ESR temperature coefficient"), self.esr_alpha)
        tv = QWidget()
        h = QHBoxLayout(tv)
        h.setContentsMargins(0, 0, 0, 0)
        self.tv_on = QCheckBox(tr("선언", "declared"))
        self.tv_lo = number(-40.0, -80.0, 250.0, "°C", 1)
        self.tv_hi = number(105.0, -80.0, 250.0, "°C", 1)
        for w_ in (self.tv_lo, self.tv_hi):
            w_.setEnabled(False)
            self.tv_on.toggled.connect(w_.setEnabled)
        h.addWidget(self.tv_on)
        h.addWidget(self.tv_lo, 1)
        h.addWidget(QLabel("…"))
        h.addWidget(self.tv_hi, 1)
        f.addRow(tr("사용 온도 범위", "operating temperature range"), tv)
        self.body.addWidget(g)
        g, f = _group(tr("열과 수명", "thermal and life"))
        self.rth = _Opt(0.0, 0.0, 100.0, "K/W", 4)
        self.rth_tref = number(65.0, -60.0, 200.0, "°C", 1)
        self.rth_basis = QLineEdit()
        self.rth_basis.setPlaceholderText(tr("핫스팟-냉각 기준 (조립 조건)", "hotspot to reference (mounting)"))
        f.addRow(tr("핫스팟 Rth", "hotspot Rth"), self.rth)
        f.addRow(tr("기준 온도", "reference temperature"), self.rth_tref)
        f.addRow(tr("Rth 근거", "Rth basis"), self.rth_basis)
        self.life = NumTable([tr("핫스팟 [°C]", "hotspot [°C]"), tr("수명 [h]", "life [h]")], min_height=90)
        self.life_v = _Opt(0.0, 0.0, 1e5, "V", 1)
        self.life_basis = QLineEdit()
        self.life_basis.setPlaceholderText(tr("예: 정격 전압에서의 유효 수명 곡선", "e.g. useful-life curve at rated "
                                                                         "voltage"))
        f.addRow(tr("수명 표", "life table"), table_with_buttons(self.life))
        f.addRow(tr("수명 기준 전압", "life at voltage"), self.life_v)
        f.addRow(tr("수명 근거", "life basis"), self.life_basis)
        self.body.addWidget(g)
        self.finish(tr("Rth·기준 온도·개수는 조립 데이터라 입력하지 않으면 프로젝트 값을 유지합니다. 수명 표 밖 온도는 외삽하지 "
                       "않습니다(UNKNOWN).", "Rth, reference temperature and count are mounting data: kept from the "
                                          "project unless typed. Temperatures outside the life table are not "
                                          "extrapolated (UNKNOWN)."))

    def spec(self) -> dict:
        d = self.base()
        d["C_uF"] = _required(self.c, "C")
        for key, w_ in (("ESL_nH", self.esl), ("rated_voltage_V", self.v_rated), ("ESR_table_T_C", self.esr_T),
                        ("ESR_temp_coeff_per_K", self.esr_alpha)):
            if w_.value() is not None:
                d[key] = w_.value()
        if self.esr_mode.currentData() == "rep":
            er = {"ESR_mohm": _required(self.esr, "ESR"), "f_Hz": _required(self.esr_f, tr("측정 주파수", "frequency")),
                  "band_Hz": [_required(self.band_lo, tr("대역 하한", "band low")),
                              _required(self.band_hi, tr("대역 상한", "band high"))]}
            if self.band_basis.text().strip():
                er["basis"] = self.band_basis.text().strip()
            d["ESR_representative"] = er
        else:
            d["ESR_table"] = self.esr_table.values()
            d["ESR_unit"] = "mohm"
        if self.tv_on.isChecked():
            d["T_valid_C"] = [float(self.tv_lo.value()), float(self.tv_hi.value())]
        if self.rth.value() is not None:
            d.update(Rth_K_per_W=self.rth.value(), T_ref_C=float(self.rth_tref.value()))
            if self.rth_basis.text().strip():
                d["Rth_basis"] = self.rth_basis.text().strip()
        life = self.life.values()
        if life:
            d["life_hours_table"] = life
            if self.life_v.value() is not None:
                d["life_voltage_V"] = self.life_v.value()
            if self.life_basis.text().strip():
                d["life_basis"] = self.life_basis.text().strip()
        return d

    def load(self, spec: dict) -> None:
        if isinstance(spec.get("ESR_table"), dict):
            raise ValueError(tr("ESR 표가 CSV 파일 참조입니다: '데이터시트 파일 가져오기'로 여세요", "the ESR table refers to "
                                                                                   "a CSV file: use 'import datasheet "
                                                                                   "file'"))
        self.keep_extra(spec, self.SHOWN)
        self.part.load(spec.get("part") or {}, spec.get("value_kind"))
        self.c.setValue(float(spec.get("C_uF") or 0.0))
        self.esl.set(spec.get("ESL_nH"))
        self.v_rated.set(spec.get("rated_voltage_V"))
        er = spec.get("ESR_representative")
        if er:
            self.esr_mode.setCurrentIndex(0)
            self.esr.setValue(float(er.get("ESR_mohm") or 0.0))
            self.esr_f.setValue(float(er.get("f_Hz") or 0.0))
            band = list(er.get("band_Hz") or [0.0, 0.0]) + [0.0, 0.0]
            self.band_lo.setValue(float(band[0] or 0.0))
            self.band_hi.setValue(float(band[1] or 0.0))
            self.band_basis.setText(str(er.get("basis") or ""))
        else:
            self.esr_mode.setCurrentIndex(1)
            k = 1e3 if spec.get("ESR_unit") == "ohm" else 1.0
            self.esr_table.load([[f_, r * k] for f_, r in spec.get("ESR_table") or []])
        self.esr_T.set(spec.get("ESR_table_T_C"))
        self.esr_alpha.set(spec.get("ESR_temp_coeff_per_K"))
        tv = spec.get("T_valid_C")
        self.tv_on.setChecked(bool(tv))
        if tv:
            self.tv_lo.setValue(float(tv[0]))
            self.tv_hi.setValue(float(tv[1]))
        self.rth.set(spec.get("Rth_K_per_W"))
        self.rth_tref.setValue(float(spec.get("T_ref_C", 65.0)))
        self.rth_basis.setText(str(spec.get("Rth_basis") or ""))
        self.life.load(spec.get("life_hours_table") or [])
        self.life_v.set(spec.get("life_voltage_V"))
        self.life_basis.setText(str(spec.get("life_basis") or ""))


# -- gate dv/dt -----------------------------------------------------------------------------------------------------------

class GateForm(_Form):
    kind = "gate_edges"
    SHOWN = ("dv_dt_on_V_per_ns", "dv_dt_off_V_per_ns", "condition", "Rg_on_ohm", "Rg_off_ohm")

    def __init__(self):
        super().__init__()
        g, f = _group(tr("스위치 노드 전압 에지", "switch-node voltage edges"))
        self.on = _Opt(0.0, 0.0, 1e3, "V/ns", 3)
        self.off = _Opt(0.0, 0.0, 1e3, "V/ns", 3)
        self.cond = QLineEdit()
        self.cond.setPlaceholderText(tr("필수: Vcc, Ic, Rg, Tj, 측정 구간 (예: 10-90 %)", "required: Vcc, Ic, Rg, Tj, "
                                                                                  "measured span (e.g. 10-90 %)"))
        self.rg_on = _Opt(0.0, 0.0, 1e3, "Ω", 3)
        self.rg_off = _Opt(0.0, 0.0, 1e3, "Ω", 3)
        f.addRow(tr("턴온 dv/dt", "turn-on dv/dt"), self.on)
        f.addRow(tr("턴오프 dv/dt", "turn-off dv/dt"), self.off)
        f.addRow(tr("시험 조건", "test condition"), self.cond)
        f.addRow("Rg,on", self.rg_on)
        f.addRow("Rg,off", self.rg_off)
        self.body.addWidget(g)
        self.finish(tr("EMI 소스의 에지는 전압 에지 t = Vdc / (dv/dt)입니다. 데이터시트의 tr/tf는 전류 상승·하강 시간(IEC 60747-9)"
                       "이라 전압 에지로 쓰지 않습니다. 두 에지에 더 빠른 기울기를 씁니다(보수적).",
                       "The EMI source edge is the voltage edge t = Vdc / (dv/dt). The datasheet tr / tf are current "
                       "rise / fall times (IEC 60747-9) and are never used as voltage edges. Both edges take the faster "
                       "slope (conservative)."))

    def spec(self) -> dict:
        d = self.base()
        for key, w_ in (("dv_dt_on_V_per_ns", self.on), ("dv_dt_off_V_per_ns", self.off), ("Rg_on_ohm", self.rg_on),
                        ("Rg_off_ohm", self.rg_off)):
            if w_.value() is not None:
                d[key] = w_.value()
        d["condition"] = self.cond.text().strip()
        return d

    def load(self, spec: dict) -> None:
        self.keep_extra(spec, self.SHOWN)
        self.part.load(spec.get("part") or {}, spec.get("value_kind"))
        self.on.set(spec.get("dv_dt_on_V_per_ns"))
        self.off.set(spec.get("dv_dt_off_V_per_ns"))
        self.cond.setText(str(spec.get("condition") or ""))
        self.rg_on.set(spec.get("Rg_on_ohm"))
        self.rg_off.set(spec.get("Rg_off_ohm"))


# -- the dialog -----------------------------------------------------------------------------------------------------------

def _scroll(w: QWidget) -> QScrollArea:
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    sc.setMinimumWidth(460)
    return sc


def motor_preview(drive, vdc: float) -> tuple[dict, list]:
    """Figure input and parameter rows of the motor preview: the typed values after conversion and what they imply
    with the project's inverter (all from the constant-parameter model, nothing else)."""
    mo, inv, dom = drive.motor, drive.inverter, drive.domain
    fl = mo.flux
    p, psi, ld, lq = mo.pole_pairs, fl.psi_pm_Wb, fl.Ld_H, fl.Lq_H
    n_max = max(abs(x) for x in dom.speed_rpm)
    imax = inv.current_limit_A_peak
    e_ll = math.sqrt(3.0) * psi * p * 2.0 * math.pi * n_max / 60.0
    rows = [(tr("극쌍수 p", "pole pairs p"), fmt(p), ""),
            ("ψ_PM", f"{psi * 1e3:.5g} mWb", tr("상 피크 (진폭 불변 dq)", "phase peak (amplitude-invariant dq)")),
            (tr("상저항 Rs", "phase resistance Rs"), f"{mo.Rs_ohm * 1e3:.5g} mΩ",
             tr(f"기준 {mo.reference_winding_temp_C:g} °C", f"at {mo.reference_winding_temp_C:g} degC")
             if mo.reference_winding_temp_C is not None else tr("기준 온도 미선언", "reference temperature not declared")),
            ("Ld / Lq", f"{ld * 1e3:.5g} / {lq * 1e3:.5g} mH", tr(f"돌극비 Lq/Ld = {lq / ld:.4g}", f"saliency Lq/Ld = "
                                                                                         f"{lq / ld:.4g}")),
            (tr("특성 전류 ψ/Ld", "characteristic current psi/Ld"), f"{psi / ld:,.1f} A",
             tr(f"인버터 전류 한계 {imax:g} A 피크", f"inverter current limit {imax:g} A peak")),
            (tr("Kt (id = 0)", "Kt (id = 0)"), f"{1.5 * p * psi:.5g} N·m/A_pk",
             f"= {1.5 * p * psi * math.sqrt(2):.5g} N·m/A_rms"),
            (tr("최고 속도 무부하 역기전력", "no-load back-EMF at top speed"), f"{e_ll:,.1f} V",
             tr(f"선간 피크 @ {n_max:,.0f} rpm, Vdc = {vdc:g} V", f"line-line peak @ {n_max:,.0f} rpm, Vdc = {vdc:g} V"))]
    rl = mo.rotational_loss
    if rl is not None:
        w = 2.0 * math.pi * n_max / 60.0
        rows.append((tr("회전 손실 @ 최고 속도", "rotational loss @ top speed"),
                     f"{rl.viscous_Nm_per_rad_s * w * w + rl.quadratic_Nm_per_rad2_s2 * w ** 3:,.1f} W",
                     rl.basis))
    else:
        rows.append((tr("회전 손실", "rotational loss"), tr("미선언", "not declared"),
                     tr("축 토크·전력 판정 UNKNOWN", "shaft torque / power decisions UNKNOWN")))
    info = {"p": p, "psi_Wb": psi, "Ld_H": ld, "Lq_H": lq, "I_max_A": imax, "Vdc_V": vdc,
            "reserve": inv.voltage.reserve_fraction, "n_max_rpm": n_max}
    return info, rows


class DatasheetEntryDialog(QDialog):
    """Representative datasheet values typed by hand -> the same spec and import as a datasheet spec file."""

    def __init__(self, win, kind: str = "motor", spec: dict | None = None):
        super().__init__(win)
        self.win = win
        self.result = None
        self.new_project = None
        self.error = None
        self.setWindowTitle(tr("데이터시트 값 입력", "Enter datasheet values"))
        self.resize(1320, 860)
        lay = QVBoxLayout(self)
        lay.addWidget(hint(tr("데이터시트 특성표의 대표값을 직접 입력합니다. 값 하나는 한 점일 뿐이므로 곡선과 모델은 <b>선언된 구성 "
                              "규칙</b>으로 만들어지고, 규칙과 가정은 모두 '기록'에 남습니다. 입력하는 동안 결과가 바로 갱신됩니다.",
                              "Type representative values from the datasheet's characteristic tables. One value is "
                              "one point, so curves and models are built by <b>declared construction rules</b>, and "
                              "every rule and assumption is listed under 'findings'. The preview follows your "
                              "typing.")))
        split = QSplitter(Qt.Horizontal)
        self.forms = {"motor": MotorForm(), "module": ModuleForm(), "capacitor": CapacitorForm(),
                      "gate_edges": GateForm()}
        self.form_tabs = QTabWidget()
        for key, label in (("motor", tr("모터", "motor")), ("module", tr("파워 모듈", "power module")),
                           ("capacitor", tr("DC-link 커패시터", "DC-link capacitor")),
                           ("gate_edges", tr("게이트 dv/dt", "gate dv/dt"))):
            self.form_tabs.addTab(_scroll(self.forms[key]), label)
        split.addWidget(self.form_tabs)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rv.addWidget(self.summary)
        self.out_tabs = QTabWidget()
        prev = QSplitter(Qt.Vertical)
        self.plot = PlotPanel(hint=tr("값을 입력하면 프로젝트에 들어갈 모델이 여기에 표시됩니다.",
                                      "the model that would enter the project appears here as you type"), min_height=300)
        self.params = KeyValueTable(headers=[tr("항목", "item"), tr("값", "value"), tr("비고", "note")])
        prev.addWidget(self.plot)
        prev.addWidget(self.params)
        prev.setSizes([520, 220])
        self.findings = KeyValueTable(headers=[tr("수준", "level"), tr("항목", "item"), tr("내용", "detail")])
        self.conversions = KeyValueTable(headers=[tr("필드", "field"), tr("규칙", "rule"), tr("입력", "given"),
                                                  tr("변환값", "converted")])
        self.spec_text = QPlainTextEdit()
        self.spec_text.setReadOnly(True)
        self.out_tabs.addTab(prev, tr("미리보기", "preview"))
        self.out_tabs.addTab(self.findings, tr("기록 (구성 규칙·가정)", "findings (rules, assumptions)"))
        self.out_tabs.addTab(self.conversions, tr("단위 변환", "unit conversions"))
        self.out_tabs.addTab(self.spec_text, tr("사양 JSON", "spec JSON"))
        rv.addWidget(self.out_tabs, 1)
        split.addWidget(right)
        split.setSizes([520, 800])
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        self.example_btn = QPushButton(tr("예시 값 채우기", "fill example values"))
        self.example_btn.setToolTip(tr("가상 부품의 형식 예시 (제품 아님)", "format example of a fictitious part (not a "
                                                                   "product)"))
        self.example_btn.clicked.connect(lambda: self.fill_example())
        row.addWidget(self.example_btn)
        for text, fn in ((tr("사양 불러오기…", "load spec…"), self.load_spec_file),
                         (tr("사양 저장…", "save spec…"), self.save_spec_file)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        row.addStretch(1)
        self.apply_btn = primary_button(tr("프로젝트에 적용", "apply to the project"))
        self.apply_btn.clicked.connect(self.apply)
        self.apply_btn.setEnabled(False)
        row.addWidget(self.apply_btn)
        b = QPushButton(tr("닫기", "close"))
        b.clicked.connect(self.reject)
        row.addWidget(b)
        lay.addLayout(row)
        self.example_btn.setEnabled(bool(examples_dir()))
        tidy_inputs(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.preview)
        for form in self.forms.values():
            self._watch(form)
        self.form_tabs.currentChanged.connect(lambda _i: self.preview())
        self.set_kind(kind)
        if spec is not None:
            self.load_spec(spec)
        self.preview()

    # -- plumbing ------------------------------------------------------------------------------------------------------
    def _watch(self, root: QWidget) -> None:
        for w in root.findChildren(QLineEdit):
            if not isinstance(w.parent(), QAbstractSpinBox):
                w.textChanged.connect(self.schedule)
        for w in root.findChildren(QAbstractSpinBox):
            w.valueChanged.connect(self.schedule)
        for w in root.findChildren(QComboBox):
            w.currentIndexChanged.connect(self.schedule)
        for w in root.findChildren(QCheckBox):
            w.toggled.connect(self.schedule)
        for w in root.findChildren(NumTable):
            w.itemChanged.connect(self.schedule)
            w.model().rowsRemoved.connect(self.schedule)

    def schedule(self, *_):
        self.timer.start()

    def kind(self) -> str:
        return list(self.forms)[self.form_tabs.currentIndex()]

    def form(self) -> _Form:
        return self.forms[self.kind()]

    def set_kind(self, kind: str) -> None:
        self.form_tabs.setCurrentIndex(list(self.forms).index(kind))

    def load_spec(self, spec: dict) -> None:
        kind = spec.get("kind")
        if kind not in self.forms:
            raise ValueError(tr(f"사양 종류 {kind!r}: motor, module, capacitor, gate_edges 중 하나여야 합니다",
                                f"spec kind {kind!r}: must be motor, module, capacitor or gate_edges"))
        self.set_kind(kind)
        self.forms[kind].load(spec)
        self.preview()

    # -- actions -------------------------------------------------------------------------------------------------------
    def fill_example(self, kind: str | None = None) -> None:
        kind = kind or self.kind()
        ex = examples_dir()
        path = ex / "datasheets" / EXAMPLES[kind] if ex else None
        if path is None or not path.is_file():
            error_box(self, tr("예시 없음", "no example"), tr("예시 폴더를 찾지 못했습니다", "the examples folder was not found"))
            return
        try:
            self.load_spec(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("예시 불러오기 실패", "could not load the example"), str(exc))

    def load_spec_file(self, path: str | None = None) -> None:
        if path is None:
            ex = examples_dir()
            start = str(ex / "datasheets") if ex else ""
            path, _ = QFileDialog.getOpenFileName(self, tr("데이터시트 사양", "datasheet spec"), start, "JSON (*.json)")
            if not path:
                return
        try:
            spec, _base = DS.load_spec(path)
            self.load_spec(spec)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("사양 불러오기 실패", "could not load the spec"), str(exc))

    def save_spec_file(self, path: str | None = None):
        try:
            spec = self.form().spec()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return None
        if path is None:
            name = (spec.get("part") or {}).get("part_number") or self.kind()
            path, _ = QFileDialog.getSaveFileName(self, tr("사양 저장", "save spec"), f"{name}_{self.kind()}.json",
                                                  "JSON (*.json)")
            if not path:
                return None
        try:
            Path(path).write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as exc:
            error_box(self, tr("저장 실패", "could not save"), str(exc))
            return None
        self.win.statusBar().showMessage(tr(f"사양 저장: {path} (twb datasheet 로 같은 결과)",
                                            f"spec saved: {path} (twb datasheet gives the same result)"), 8000)
        return path

    def preview(self) -> None:
        """Build the spec of the visible form and import it into a copy of the active project (nothing is applied)."""
        self.timer.stop()
        kind = self.kind()
        self.new_project = self.result = self.error = None
        self.apply_btn.setEnabled(False)
        try:
            spec = self.form().spec()
        except Exception as exc:  # noqa: BLE001 - incomplete input is the normal state while typing
            self._show_error(str(exc), None)
            return
        self.spec_text.setPlainText(json.dumps(spec, indent=1, ensure_ascii=False))
        try:
            new, res = DS.apply(self.win.state.project, spec)
        except Exception as exc:  # noqa: BLE001 - the importer's refusal, with its reason
            self._show_error(str(exc), spec)
            return
        self.new_project, self.result = new, res
        sec = res["section"]
        n = {lv: sum(1 for f in res["findings"] if f["level"] == lv) for lv in ("WARNING", "NOTE")}
        self.summary.setText(tr(f"<b style='color:#1a7f37'>✓ {sec}</b> 섹션 → digest {short(new.sections[sec].digest)} · "
                                f"출처 supplier (검증 아님) · 경고 {n['WARNING']} · 기록 {n['NOTE']} — "
                                "'프로젝트에 적용'을 누르면 작업 사본이 이 값으로 바뀝니다.",
                                f"<b style='color:#1a7f37'>✓ {sec}</b> section → digest "
                                f"{short(new.sections[sec].digest)} · origin supplier (not qualified) · "
                                f"{n['WARNING']} warning(s) · {n['NOTE']} note(s) — 'apply' puts these values into "
                                "the working copy."))
        rows, colors = [], {}
        for i, f in enumerate(sorted(res["findings"], key=lambda f: {"ERROR": 0, "WARNING": 1}.get(f["level"], 2))):
            rows.append((f["level"], f["item"], f["detail"]))
            if f["level"] in LEVEL_COLOR:
                colors[(i, 0)] = LEVEL_COLOR[f["level"]]
        self.findings.set_rows(rows, colors)
        conv = res.get("conversions") or []
        self.conversions.set_rows([(c["field"], c["rule"], json.dumps(c["given"], ensure_ascii=False),
                                    fmt(c["converted"])) for c in conv])
        self.out_tabs.setTabText(1, tr(f"기록 ({len(rows)})", f"findings ({len(rows)})"))
        self.out_tabs.setTabText(2, tr(f"단위 변환 ({len(conv)})", f"unit conversions ({len(conv)})"))
        self._draw(kind, spec, res)
        self.apply_btn.setEnabled(True)

    def _show_error(self, msg: str, spec: dict | None) -> None:
        self.error = msg
        self.summary.setText(tr(f"<b style='color:#cf222e'>✗ 아직 모델을 만들 수 없습니다</b>: {msg}",
                                f"<b style='color:#cf222e'>✗ no model yet</b>: {msg}"))
        if spec is None:
            self.spec_text.setPlainText("")
        self.findings.set_rows([])
        self.conversions.set_rows([])
        self.params.set_rows([])
        self.plot.placeholder(tr("입력이 완성되면 모델이 여기에 표시됩니다.\n" + msg, "the model appears here once the input "
                                                                           "is complete.\n" + msg))

    def _draw(self, kind: str, spec: dict, res: dict) -> None:
        data = res["data"]
        self.params.setVisible(kind in ("motor", "gate_edges"))
        self.plot.setVisible(kind != "gate_edges")
        if kind == "module":
            self.plot.draw(DF.fig_datasheet_module, data, name="datasheet_module")
            self.params.set_rows([])
        elif kind == "capacitor":
            er = spec.get("ESR_representative")
            self.plot.draw(DF.fig_datasheet_capacitor, data, name="datasheet_capacitor",
                           point=(er["f_Hz"], er["ESR_mohm"]) if er else None)
            self.params.set_rows([])
        elif kind == "motor":
            from ..io import drive_from_dict
            vdc = float(self.win.state.project.data("dc_source")["Vdc_nominal_V"])
            info, rows = motor_preview(drive_from_dict(copy.deepcopy(data)), vdc)
            self.plot.draw(DF.fig_datasheet_motor, info, name="datasheet_motor")
            self.params.set_rows(rows)
        else:
            g = data.get("gate") or {}
            vdc = float(self.win.state.project.data("dc_source")["Vdc_nominal_V"])
            self.params.set_rows([(tr("상승 에지", "rising edge"), f"{g.get('t_rise_ns', 0):.4g} ns", ""),
                                  (tr("하강 에지", "falling edge"), f"{g.get('t_fall_ns', 0):.4g} ns", ""),
                                  ("Vdc", f"{vdc:g} V", tr("프로젝트 정격 DC 전압", "project nominal DC voltage")),
                                  (tr("근거", "basis"), g.get("basis", ""), "")])

    def apply(self) -> bool:
        if self.new_project is None:
            self.preview()
        if self.new_project is None:
            error_box(self, tr("적용할 수 없음", "cannot apply"), self.error or "")
            return False
        self.win.state.set_project(self.new_project)
        self.win.statusBar().showMessage(tr(f"데이터시트 값 적용: {self.result['provenance']['source']} → "
                                            f"{self.result['section']}",
                                            f"datasheet values applied: {self.result['provenance']['source']} -> "
                                            f"{self.result['section']}"), 10000)
        self.accept()
        return True
