"""Drive cycle page (system view, item 8): the vehicle on a standard or imported speed trace, the machine points,
energy per component, consumption and range, regeneration and the friction brakes, and where the trace is not
delivered."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...insight.drive_system import cycle_insight
from ...plots import drive_system_figures as F
from ..widgets import (ConceptNote, KeyValueTable, PlotPanel, check, combo, error_box, fmt, hint, number,
                       primary_button, reading_tab)

NOTE = lambda: tr(
    "<b>주행 사이클</b>: 차량을 속도 궤적 위에서 움직이며 각 1 s 구간의 모터 운전점을 구하고 부품별 에너지를 셉니다.<br>"
    "• 도로 부하 F = A + B v + C v² (또는 m g c_rr + ½ ρ CdA v²) + 경사 + 등가 질량 × 가속도. 바퀴 토크가 액슬·감속기의 "
    "<b>자기 역함수</b>로 모터 축 토크가 되고, 운전점은 효율 페이지와 같은 최소 전류 정책점입니다.<br>"
    "• 제동은 회생 분담률만큼 모터가, 나머지는 마찰 제동이 맡습니다. 모터·DC 충전 한계로 못 하면 줄인 만큼 마찰로 갑니다.<br>"
    "• 구동이 전달하지 못한 구간은 <b>보고</b>합니다(궤적을 고쳐 따라가지 않음). UNKNOWN 구간이 있으면 소비·주행거리를 "
    "판정하지 않습니다.<br>• 에너지 수지(OCV 에너지 = 바퀴 일 + 마찰 + 손실 + 보조)의 잔차를 함께 보여 줍니다. PWM 고조파 모터 "
    "손실과 DC-link 커패시터 손실은 포함하지 않습니다.",
    "<b>Drive cycle</b>: the vehicle moves along a speed trace; each 1 s interval gives a machine point and the "
    "energy per component is counted.<br>• Road load F = A + B v + C v² (or m g c_rr + ½ ρ CdA v²) + grade + "
    "equivalent mass × acceleration. The wheel torque becomes the motor-shaft torque through the axle and the "
    "reducer's <b>own inverse</b>; the point is the same minimum-current policy point as on the efficiency page.<br>"
    "• Braking: the machine takes the regeneration share, the friction brakes the rest; what the machine or the DC "
    "charge limit cannot take goes to the friction brakes.<br>• Intervals the drive cannot deliver are "
    "<b>reported</b> (the trace is not re-planned). With UNKNOWN intervals there is no consumption or range verdict."
    "<br>• The energy closure (OCV energy = wheel work + friction + losses + auxiliaries) is shown with its "
    "residual. PWM harmonic motor losses and DC-link capacitor losses are not included.")


def _task(fn):
    def run(progress, body):
        progress(0.02, tr("주행 사이클 계산", "drive cycle"), 1.0)
        return fn(body)
    return run


def _scroll(w):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    sc.setMinimumWidth(400)
    return sc


class DriveCyclePage(QWidget):
    workspace_data = ("_csv",)                           # an imported trace (name, CSV text)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last = None
        self._csv = None
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        ex = self.win.state.example("DRIVE_CYCLE")
        # -- trace
        g = QGroupBox(tr("주행 사이클", "speed trace"))
        f = QFormLayout(g)
        self.c_sel = combo([(f"{c['title']}", c["key"]) for c in api.drive_cycles()] +
                           [(tr("가져온 CSV", "imported CSV"), "csv")], ex["cycle"].get("builtin", "WLTC_3b"))
        self.c_import = QPushButton(tr("CSV 가져오기…", "import CSV…"))
        self.c_import.clicked.connect(self._import_csv)
        self.c_lab = QLabel(tr("가져온 CSV 없음", "no imported CSV"))
        self.c_lab.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.c_import)
        row.addWidget(self.c_lab, 1)
        f.addRow(tr("사이클", "cycle"), self.c_sel)
        f.addRow("", row)
        f.addRow(hint(tr("CSV: 시간 열(t, time)과 단위가 이름에 든 속도 열(kmh, mph, mps); 선택 열 grade_pct.",
                         "CSV: a time column (t, time) and a speed column whose name carries its unit (kmh, mph, "
                         "mps); optional grade_pct.")))
        v.addWidget(g)
        # -- vehicle
        g = QGroupBox(tr("차량 (프로젝트의 vehicle 섹션)", "vehicle (the project's vehicle section)"))
        f = QFormLayout(g)
        self.v_m = number(0, 100, 100000, "kg", 1, 50)
        self.v_r = number(0, 0.05, 2.0, "m", 4, 0.005)
        self.v_jw = number(0, 0, 1000, "kg·m²", 3, 0.5)
        self.v_jm = number(0, 0, 10, "kg·m²", 4, 0.005)
        self.v_form = combo([("A + B v + C v²", "abc"), (tr("물리식 m g c_rr + ½ρCdA v²", "physical m g c_rr + ½ρCdA v²"),
                                                           "physical")], "abc")
        self.v_A = number(0, 0, 1e5, "N", 2, 5)
        self.v_B = number(0, 0, 1e4, "N/(m/s)", 4, 0.05)
        self.v_C = number(0, 0, 100, "N/(m/s)²", 4, 0.01)
        self.v_crr = number(0, 0, 0.2, "", 5, 0.001)
        self.v_cda = number(0, 0, 20, "m²", 3, 0.05)
        self.v_drag = check(tr("도로 부하에 이 구동의 끌림 포함 (코스트다운 시 연결)", "road load contains this drive's drag "
                                                                         "(coast-down with the drive connected)"), False)
        self.v_rl_basis = QLineEdit()
        self.v_share = number(1.0, 0, 1, "", 3, 0.05)
        self.v_vcut = number(0, 0, 100, "km/h", 1, 1)
        self.v_aux = number(0, 0, 1e5, "W", 1, 50)
        self.v_use = number(0, 0, 1000, "kWh", 2, 1)
        for lab, w in ((tr("시험 질량", "test mass"), self.v_m), (tr("바퀴 동반경", "dynamic wheel radius"), self.v_r),
                       (tr("바퀴 관성 (전체)", "wheel inertia (all)"), self.v_jw),
                       (tr("모터 관성 (ROM)", "machine inertia (ROM)"), self.v_jm),
                       (tr("도로 부하 형식", "road-load form"), self.v_form), ("A", self.v_A), ("B", self.v_B),
                       ("C", self.v_C), ("c_rr", self.v_crr), ("CdA", self.v_cda), ("", self.v_drag),
                       (tr("도로 부하 근거", "road-load basis"), self.v_rl_basis),
                       (tr("회생 분담률", "regeneration share"), self.v_share),
                       (tr("회생 하한 속도", "regeneration cut-off speed"), self.v_vcut),
                       (tr("HV 보조 부하", "HV auxiliaries"), self.v_aux),
                       (tr("가용 배터리 에너지 (0 = 미선언)", "usable battery energy (0 = not declared)"), self.v_use)):
            f.addRow(lab, w)
        self.v_form.currentIndexChanged.connect(self._form_changed)
        v.addWidget(g)
        # -- conditions
        g = QGroupBox(tr("조건", "conditions"))
        f = QFormLayout(g)
        self.k_vdc = number(ex["Vdc_V"], 1, 2000, "V", 1, 10)
        self.k_R = number(ex["source_R_mohm"], 0, 1000, "mΩ", 2, 1)
        self.k_oil = number(ex["oil_temp_C"], -40, 200, "°C", 1, 5)
        self.k_tj = number(float(ex["module"].get("Tj_eval_C", 150.0)), -40, 200, "°C", 1, 5)
        self.k_loss = combo([(tr("데이터시트 모듈 (프로젝트)", "datasheet module (project)"), "module"),
                             (tr("모델의 2차 대리모델", "model's quadratic surrogate"), "surrogate")], "module")
        for lab, w in (("Vdc", self.k_vdc), (tr("소스 저항 (배터리+하니스)", "source resistance (battery + harness)"), self.k_R),
                       (tr("감속기 오일 온도", "reducer oil temperature"), self.k_oil),
                       (tr("모듈 손실 평가 Tj", "module loss evaluation Tj"), self.k_tj),
                       (tr("인버터 손실", "inverter loss"), self.k_loss)):
            f.addRow(lab, w)
        v.addWidget(g)
        # -- requirements
        g = QGroupBox(tr("요구 (선택)", "requirements (optional)"))
        f = QFormLayout(g)
        rq = ex.get("requirements") or {}
        self.q_cons_on = check(tr("소비 상한", "consumption limit"), rq.get("consumption_Wh_per_km_max") is not None)
        self.q_cons = number(rq.get("consumption_Wh_per_km_max") or 160.0, 1, 2000, "Wh/km", 1, 5)
        self.q_rng_on = check(tr("주행거리 하한", "range limit"), rq.get("range_km_min") is not None)
        self.q_rng = number(rq.get("range_km_min") or 400.0, 1, 5000, "km", 0, 10)
        self.q_rec_on = check(tr("회생 회수율 하한", "recovery-ratio limit"), rq.get("recovery_ratio_min") is not None)
        self.q_rec = number(100 * (rq.get("recovery_ratio_min") or 0.6), 0, 100, "%", 1, 5)
        for a, b in ((self.q_cons_on, self.q_cons), (self.q_rng_on, self.q_rng), (self.q_rec_on, self.q_rec)):
            f.addRow(a, b)
        v.addWidget(g)
        self.btn = primary_button(tr("사이클 계산", "run the cycle"))
        self.btn.setMinimumHeight(34)
        self.btn.clicked.connect(self.run)
        v.addWidget(self.btn)
        v.addWidget(ConceptNote(NOTE()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs("drive_cycle", form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.p_trace = PlotPanel(hint=tr("'사이클 계산'을 누르세요", "press 'run the cycle'"))
        self.p_energy = PlotPanel(hint=tr("'사이클 계산'을 누르세요", "press 'run the cycle'"))
        self.p_points = PlotPanel(hint=tr("'사이클 계산'을 누르세요", "press 'run the cycle'"))
        for p, lab in ((self.p_trace, tr("시간 이력", "time history")), (self.p_energy, tr("에너지 분해", "energy breakdown")),
                       (self.p_points, tr("모터 운전점", "machine points"))):
            self.tabs.addTab(p, lab)
        self.reading = reading_tab(self.tabs, tr(
            "계산하면 해석이 표시됩니다 — 소비·주행거리·회생 회수율의 판정, 손실이 큰 부품 순서, 차량이 요구한 일, 구동이 전달하지 "
            "못한 구간.", "Run to read the result — consumption, range and recovery verdicts, the largest losses, the "
                          "work the vehicle demands, intervals the drive does not deliver."))
        self.kv = KeyValueTable()
        rl.addWidget(self.tabs, 3)
        rl.addWidget(self.kv, 2)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([440, 1020])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)
        self.apply_project()

    # ------------------------------------------------------------------ inputs
    def _form_changed(self):
        abc = self.v_form.currentData() == "abc"
        for w in (self.v_A, self.v_B, self.v_C):
            w.setEnabled(abc)
        for w in (self.v_crr, self.v_cda):
            w.setEnabled(not abc)

    def apply_project(self, _project=None):
        """Vehicle, reducer and DC source from the active project (trace, conditions and requirements stay)."""
        ex = self.win.state.example("DRIVE_CYCLE")
        vh = ex["vehicle"]
        rl = vh.get("road_load") or {}
        self.v_m.setValue(float(vh["mass_kg"]))
        self.v_r.setValue(float(vh["wheel_radius_m"]))
        self.v_jw.setValue(float(vh.get("J_wheels_kgm2") or 0.0))
        self.v_jm.setValue(float(vh.get("J_motor_kgm2") or 0.0))
        self.v_form.setCurrentIndex(self.v_form.findData(rl.get("form", "abc")))
        self.v_A.setValue(float(rl.get("A_N") or 0.0))
        self.v_B.setValue(float(rl.get("B_N_per_mps") or 0.0))
        self.v_C.setValue(float(rl.get("C_N_per_mps2") or 0.0))
        self.v_crr.setValue(float(rl.get("c_rr") or 0.0))
        self.v_cda.setValue(float(rl.get("CdA_m2") or 0.0))
        self.v_drag.setChecked(bool(rl.get("includes_edrive_drag")))
        self.v_rl_basis.setText(str(rl.get("basis", "")))
        rg = vh.get("regen") or {}
        self.v_share.setValue(float(rg.get("share", 1.0)))
        self.v_vcut.setValue(float(rg.get("min_speed_kmh", 0.0)))
        self.v_aux.setValue(float(vh.get("aux_hv_W") or 0.0))
        self.v_use.setValue(float(vh.get("usable_energy_kWh") or 0.0))
        self.k_vdc.setValue(float(ex["Vdc_V"]))
        self.k_R.setValue(float(ex["source_R_mohm"]))
        self._form_changed()

    def _import_csv(self):
        from ...extensions.drive_cycle import cycle_from_csv
        path, _ = QFileDialog.getOpenFileName(self, tr("속도 궤적 CSV", "speed trace CSV"), "", "CSV (*.csv *.txt)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
            c = cycle_from_csv(text, Path(path).stem)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("가져오기 실패", "import failed"), str(exc))
            return
        self._csv = {"name": Path(path).stem, "text": text}
        st = c.stats()
        self.c_lab.setText(f"{Path(path).name}: {st['duration_s']:.0f} s, {st['distance_km']:.2f} km, "
                           f"v_max {st['v_max_kmh']:.1f} km/h")
        self.c_sel.setCurrentIndex(self.c_sel.findData("csv"))

    def body(self) -> dict:
        b = self.win.state.example("DRIVE_CYCLE")
        vh = b["vehicle"]
        form = self.v_form.currentData()
        vh.update({"mass_kg": self.v_m.value(), "wheel_radius_m": self.v_r.value(), "J_wheels_kgm2": self.v_jw.value(),
                   "J_motor_kgm2": self.v_jm.value(),
                   "road_load": {"form": form, "A_N": self.v_A.value(), "B_N_per_mps": self.v_B.value(),
                                 "C_N_per_mps2": self.v_C.value(), "c_rr": self.v_crr.value(),
                                 "CdA_m2": self.v_cda.value(), "rho_kg_m3": 1.2,
                                 "includes_edrive_drag": self.v_drag.isChecked(),
                                 "basis": self.v_rl_basis.text().strip()},
                   "regen": {"share": self.v_share.value(), "min_speed_kmh": self.v_vcut.value()},
                   "aux_hv_W": self.v_aux.value(),
                   "usable_energy_kWh": self.v_use.value() if self.v_use.value() > 0 else None})
        key = self.c_sel.currentData()
        if key == "csv":
            if not self._csv:
                raise ValueError(tr("가져온 CSV가 없습니다", "no CSV imported"))
            b["cycle_csv"], b["cycle_name"] = self._csv["text"], self._csv["name"]
        else:
            b["cycle"] = {"builtin": key}
        b.update({"Vdc_V": self.k_vdc.value(), "source_R_mohm": self.k_R.value(), "oil_temp_C": self.k_oil.value(),
                  "Tj_eval_C": self.k_tj.value(), "loss_model": self.k_loss.currentData(),
                  "requirements": {"consumption_Wh_per_km_max": self.q_cons.value() if self.q_cons_on.isChecked() else None,
                                   "range_km_min": self.q_rng.value() if self.q_rng_on.isChecked() else None,
                                   "recovery_ratio_min": self.q_rec.value() / 100 if self.q_rec_on.isChecked() else None}})
        b.update(self.win.state.body())
        return b

    # ------------------------------------------------------------------ run / show
    def run(self):
        try:
            body = self.body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.btn.setEnabled(False)
        self.win.runner.run("drive_cycle", tr("주행 사이클", "drive cycle"), _task(api.drive_cycle), self.show_result,
                            body, on_error=self._err)

    def _err(self, msg, tb):
        self.btn.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def show_result(self, res):
        self.btn.setEnabled(True)
        self.last = res
        tr_ = res.get("trace") or {}
        csv = (lambda r=tr_: {k: r[k] for k in ("t_mid_s", "v_kmh", "a_mps2", "n_rpm", "T_out_demand_Nm",
                                                 "T_out_drive_Nm", "T_m_Nm", "P_dc_W", "P_ocv_W", "P_friction_W",
                                                 "loss_W", "state")}) if tr_ else None
        self.p_trace.draw(F.fig_cycle_trace, res, name="drive_cycle_trace", csv=csv)
        self.p_energy.draw(F.fig_cycle_energy, res, name="drive_cycle_energy")
        self.p_points.draw(F.fig_cycle_points, res, name="drive_cycle_points", csv=csv)
        if self.tabs.currentWidget() is not self.reading:
            self.tabs.setCurrentWidget(self.p_energy)
        self.reading.read("drive_cycle", tr("주행 사이클", "drive cycle"), cycle_insight, res)
        c, cons, e = res["cycle"], res["consumption_Wh_per_km"], res["energy_kWh"]
        reg, clo = res["regeneration"], res["closure"]
        rows = [(tr("사이클", "cycle"), f"{c['name']} — {c['distance_km']:.3f} km, {c['duration_s']:.0f} s, "
                                       f"v_max {c['v_max_kmh']:.1f} km/h"),
                (tr("판정", "verdicts"), " · ".join(f"{v['id']}: {v['status']}" for v in res["verdicts"])),
                (tr("배터리 소비 (OCV)", "battery consumption (OCV)"), fmt(cons["battery_ocv"], 5) + " Wh/km"),
                (tr("배터리 단자 / 인버터 DC 순", "battery terminal / inverter DC net"),
                 f"{fmt(cons['battery_terminal'], 5)} / {fmt(cons['inverter_dc_net'], 5)} Wh/km"),
                (tr("주행거리", "range"), fmt(res.get("range_km"), 4) + " km" if res.get("range_km") else "—"),
                (tr("배터리 출력 / 회생 입력", "battery out / regeneration in"),
                 f"{e['battery_traction_out'] * 1e3:.1f} / {e['battery_regen_in'] * 1e3:.1f} Wh"),
                (tr("회생 회수율", "regeneration recovery"),
                 fmt(reg["recovery_ratio"] and 100 * reg["recovery_ratio"], 4) + " %"),
                (tr("마찰 제동", "friction brakes"), f"{e['friction_brakes'] * 1e3:.1f} Wh"),
                (tr("손실 (Wh)", "losses (Wh)"), " · ".join(f"{k} {v * 1e3:.1f}" for k, v in res["losses_kWh"].items())),
                (tr("에너지 수지 잔차", "energy closure residual"), f"{clo['residual_kWh'] * 1e3:.3g} Wh "
                                                                  f"(rel {clo['relative']:.2g})"),
                (tr("상태별 시간 (s)", "time per state (s)"),
                 " · ".join(f"{k} {v:.0f}" for k, v in res["time_s"].items() if v)),
                (tr("인버터 손실 모델", "inverter loss model"),
                 f"{res.get('loss_model')} (Tj {fmt(res.get('inverter_Tj_eval_C'), 4)} °C)")]
        self.kv.set_rows(rows)

    def redraw(self):
        for p in (self.p_trace, self.p_energy, self.p_points, self.reading):
            p.redraw()
