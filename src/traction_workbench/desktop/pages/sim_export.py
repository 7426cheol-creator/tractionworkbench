"""Simulator export page (system view, item 12): the drive's maps for a vehicle model - DC power, losses per component,
output power and currents on a speed x torque grid at one or more DC voltages, with the full-load curves - previewed,
then written as CSV (long or grids), a MATLAB .mat file or an FMI 2.0 FMU."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...insight.drive_system import sim_insight
from ...plots import drive_system_figures as F
from ..widgets import (ConceptNote, KeyValueTable, PlotPanel, check, combo, error_box, hint, number, primary_button,
                       reading_tab)

NOTE = lambda: tr(  # noqa: E731
    "<b>시뮬레이터 내보내기</b>: 차량 시뮬레이터(Simulink, GT-SUITE, AVL CRUISE, CarMaker, FMI 도구)가 쓰는 구동계 지도를 이 "
    "제품 모델에서 만듭니다.<br>• 각 칸 = 그 속도·토크의 최소 전류 정책점(정상 상태): DC 전력, 모터 단자·축·출력 전력, 인버터·"
    "모터·감속기 손실, i_d·i_q. 속도마다 최대 토크(구동·회생). 최대 토크 밖·판정 못한 칸은 <b>빈 칸</b>(NaN)이며 채우지 "
    "않습니다.<br>• 모델이 평가하지 않는 손실(PWM 고조파 등)은 메타데이터에 이름으로 남습니다.<br>• FMU는 요청 토크를 최대 토크 "
    "곡선으로 제한한 뒤 표를 보간합니다. 경계 옆 보간에 필요한 바깥 칸만 가장 가까운 값으로 채우며(설명에 명시), C 컴파일러가 "
    "있으면 바이너리를 넣고 없으면 소스 FMU를 만듭니다.",
    "<b>Simulator export</b>: the drive maps a vehicle simulator (Simulink, GT-SUITE, AVL CRUISE, CarMaker, FMI tools) "
    "uses, made from this product model.<br>• Each cell = the minimum-current policy point at that speed and torque "
    "(steady state): DC power, motor-terminal, shaft and output power, inverter, motor and reducer losses, i_d, i_q. "
    "Per speed the full-load torque (motoring and generating). Cells beyond the full load or not decided are "
    "<b>blank</b> (NaN), never filled.<br>• Losses the model does not evaluate (PWM harmonics and others) are named in "
    "the metadata.<br>• The FMU clamps the torque request to the full-load curve, then interpolates the tables. Only "
    "the outside cells an interpolation next to the boundary needs are filled with the nearest value (stated in its "
    "description); with a C compiler the binary is included, otherwise a source FMU.")

QUANTS = ("P_dc_W", "loss_total_W", "loss_inverter_W", "loss_motor_W", "loss_reducer_W", "P_out_W", "id_A", "iq_A")


def _task(progress, body):
    progress(0.02, tr("구동계 지도", "drive maps"), 1.0)
    return api.sim_maps(body)


def _floats(text: str) -> list:
    try:
        out = [float(x) for x in text.replace(",", " ").split()]
    except ValueError:
        raise ValueError(tr("DC 전압: 숫자 목록이 아닙니다", "DC voltages: not a list of numbers")) from None
    if not out:
        raise ValueError(tr("DC 전압이 비었습니다", "no DC voltage given"))
    return sorted(set(out))


class SimExportPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last = None
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        ex = self.win.state.example("SIM_EXPORT")
        g = QGroupBox(tr("격자", "grid"))
        f = QFormLayout(g)
        self.s_max = number(ex["speed_max_rpm"], 100, 40000, "rpm", 0, 500)
        self.s_step = number(ex["speed_step_rpm"], 50, 5000, "rpm", 0, 250)
        self.t_step = number(ex["torque_step_Nm"], 1, 500, "N·m", 1, 5)
        for lab, w in ((tr("최고 속도", "maximum speed"), self.s_max), (tr("속도 간격", "speed step"), self.s_step),
                       (tr("토크 간격", "torque step"), self.t_step)):
            f.addRow(lab, w)
        f.addRow(hint(tr("토크 범위는 계산한 최대 토크 곡선에 맞춰 정해집니다 (0 포함).",
                         "the torque range follows the full-load curves found (zero included).")))
        v.addWidget(g)
        g = QGroupBox(tr("DC 전압", "DC voltage"))
        f = QFormLayout(g)
        self.vdc = QLineEdit(" ".join(f"{x:g}" for x in ex["Vdc_V"]))
        self.scaling = check(tr("합성 E ∝ V 스위칭 에너지 법칙 선언 (시험 전압 밖)",
                                "declare the synthetic E ∝ V switching-energy law (away from the test voltage)"), False)
        f.addRow(tr("전압 [V] (여러 개 가능)", "voltages [V] (several allowed)"), self.vdc)
        f.addRow(self.scaling)
        f.addRow(hint(tr("모듈의 스위칭 에너지는 시험 전압에서 측정한 값입니다. 다른 전압은 스케일 법칙이 선언되어야 판정됩니다 — "
                         "선언하지 않으면 그 칸은 UNKNOWN(빈 칸).",
                         "the module's switching energies were measured at its test voltage; other voltages need a "
                         "declared scaling law - without one those cells are UNKNOWN (blank).")))
        v.addWidget(g)
        g = QGroupBox(tr("감속기·온도", "reducer and temperatures"))
        f = QFormLayout(g)
        self.red = check(tr("감속기 포함 (출력 전력·감속기 손실)", "include the reducer (output power, reducer loss)"), True)
        self.oil = number(ex.get("oil_temp_C") or 80.0, -40, 160, "°C", 1, 5)
        f.addRow(self.red)
        f.addRow(tr("오일 온도", "oil temperature"), self.oil)
        v.addWidget(g)
        self.btn = primary_button(tr("지도 계산", "compute the maps"))
        self.btn.setMinimumHeight(36)
        self.btn.clicked.connect(self.run)
        v.addWidget(self.btn)
        g = QGroupBox(tr("내보내기", "export"))
        gl = QVBoxLayout(g)
        self.model_name = QLineEdit(ex.get("model_name", "TwbDriveMaps"))
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("FMU 모델 이름", "FMU model name")))
        row.addWidget(self.model_name, 1)
        gl.addLayout(row)
        self.exp = {}
        for kind, lab in (("csv", tr("CSV (긴 표)…", "CSV (long)…")), ("grids", tr("CSV 격자 (폴더)…", "CSV grids (folder)…")),
                          ("mat", tr("MATLAB .mat…", "MATLAB .mat…")), ("fmu", tr("FMU (FMI 2.0)…", "FMU (FMI 2.0)…"))):
            b = QPushButton(lab)
            b.setEnabled(False)
            b.clicked.connect(lambda _c=False, k=kind: self.export(k))
            self.exp[kind] = b
            gl.addWidget(b)
        self.exp_state = hint(tr("먼저 지도를 계산하세요.", "compute the maps first."))
        self.exp_state.setWordWrap(True)
        gl.addWidget(self.exp_state)
        v.addWidget(g)
        v.addWidget(ConceptNote(NOTE()))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(380)
        split.addWidget(sc)
        self.win.track_inputs("sim_maps", form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.q_pick = combo([(F.SIM_Q[k](), k) for k in QUANTS], "P_dc_W")
        self.v_pick = combo([(f"{x:g} V", i) for i, x in enumerate(ex["Vdc_V"])], 0)
        for w in (self.q_pick, self.v_pick):
            w.setProperty("twb_not_input", True)          # view selectors
            w.currentIndexChanged.connect(self._draw_map)
        bar.addWidget(QLabel(tr("지도", "map")))
        bar.addWidget(self.q_pick)
        bar.addWidget(QLabel("Vdc"))
        bar.addWidget(self.v_pick)
        bar.addStretch(1)
        rl.addLayout(bar)
        self.tabs = QTabWidget()
        self.p_map = PlotPanel(hint=tr("'지도 계산'을 누르세요", "press 'compute the maps'"))
        self.p_full = PlotPanel(hint=tr("'지도 계산'을 누르세요", "press 'compute the maps'"))
        self.tabs.addTab(self.p_map, tr("지도", "map"))
        self.tabs.addTab(self.p_full, tr("최대 토크 곡선", "full-load curves"))
        self.reading = reading_tab(self.tabs, tr("계산하면 해석이 표시됩니다 — 칸의 상태, 손실에 없는 것, 파일별 내용.",
                                                 "Run to read the result - cell status, what the losses do not "
                                                 "contain, what each file holds."))
        self.kv = KeyValueTable()
        rl.addWidget(self.tabs, 3)
        rl.addWidget(self.kv, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 1040])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)

    def apply_project(self, _project=None):
        ex = self.win.state.example("SIM_EXPORT")
        self.vdc.setText(" ".join(f"{x:g}" for x in ex["Vdc_V"]))

    def body(self) -> dict:
        b = self.win.state.example("SIM_EXPORT")
        pr = self.win.state.project
        b.update({"Vdc_V": _floats(self.vdc.text()), "declare_vdc_scaling": self.scaling.isChecked(),
                  "speed_max_rpm": self.s_max.value(), "speed_step_rpm": self.s_step.value(),
                  "torque_step_Nm": self.t_step.value(), "include_reducer": self.red.isChecked(),
                  "oil_temp_C": self.oil.value(), "model_name": self.model_name.text().strip() or "TwbDriveMaps",
                  "source": {"project": pr.label, "project_digest": pr.digest()[:16]}})
        b.update(self.win.state.body())
        return b

    def run(self):
        try:
            body = self.body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.btn.setEnabled(False)
        self.win.runner.run("sim_maps", tr("구동계 지도", "drive maps"), _task, self.show_maps, body, on_error=self._err)

    def _err(self, msg, tb):
        self.btn.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def show_maps(self, res):
        self.btn.setEnabled(True)
        self.last = res
        self.v_pick.blockSignals(True)
        self.v_pick.clear()
        for i, x in enumerate(res["Vdc_V"]):
            self.v_pick.addItem(f"{x:g} V", i)
        self.v_pick.blockSignals(False)
        self._draw_map()
        self.p_full.draw(F.fig_sim_fullload, res, name="sim_fullload",
                         csv=lambda r=res: {"speed_rpm": r["speeds_rpm"],
                                            **{f"T_max_{v:g}V": r["T_max_Nm"][a] for a, v in enumerate(r["Vdc_V"])},
                                            **{f"T_min_{v:g}V": r["T_min_Nm"][a] for a, v in enumerate(r["Vdc_V"])}})
        if self.tabs.currentWidget() is not self.reading:
            self.tabs.setCurrentWidget(self.p_map)
        self.reading.read("sim_maps", tr("시뮬레이터 내보내기", "simulator export"), sim_insight, res)
        for b in self.exp.values():
            b.setEnabled(True)
        c = res.get("counts") or {}
        self.kv.set_rows([(tr("격자", "grid"), f"{len(res['speeds_rpm'])} × {len(res['torques_Nm'])} × "
                                             f"{len(res['Vdc_V'])} (speed × torque × Vdc)"),
                          (tr("칸 상태", "cells"), ", ".join(f"{k} {v}" for k, v in c.items())),
                          (tr("평가 안 된 손실", "losses not evaluated"),
                           ", ".join(res["meta"].get("not_evaluated") or []) or "—")])
        self.exp_state.setText(tr("내보낼 수 있습니다.", "ready to export."))

    def _draw_map(self, *_):
        if self.last is None:
            return
        q, a = self.q_pick.currentData(), self.v_pick.currentData() or 0
        self.p_map.draw(F.fig_sim_map, self.last, quantity=q, vdc_index=a, name=f"sim_map_{q}")

    def export(self, kind: str, path: str | None = None):
        if self.last is None:
            return
        if not path:
            if kind == "grids":
                path = QFileDialog.getExistingDirectory(self, tr("CSV 격자를 저장할 폴더", "folder for the CSV grids"))
            else:
                ext = {"csv": "CSV (*.csv)", "mat": "MATLAB (*.mat)", "fmu": "FMU (*.fmu)"}[kind]
                name = {"csv": "drive_maps.csv", "mat": "drive_maps.mat",
                        "fmu": f"{self.model_name.text().strip() or 'TwbDriveMaps'}.fmu"}[kind]
                path, _ = QFileDialog.getSaveFileName(self, tr("내보내기", "export"), name, ext)
        if not path:
            return
        try:
            r = api.sim_write(self.last, kind, path, self.model_name.text().strip() or "TwbDriveMaps")
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("내보내기 실패", "export failed"), str(exc))
            return
        if kind == "fmu":
            what = (tr(f"바이너리 {r['binary']}", f"binary {r['binary']}") if r.get("binary") else
                    tr("소스 FMU (이 PC에 C 컴파일러 없음 — 가져오는 도구가 컴파일)", "source FMU (no C compiler on this PC - "
                                                                          "the importing tool compiles it)"))
            self.exp_state.setText(tr(f"FMU 저장: {Path(path).name} — {what}", f"FMU saved: {Path(path).name} — {what}"))
        else:
            n = len(r.get("files") or [])
            self.exp_state.setText(tr(f"저장: {path} ({n}개 파일)", f"saved: {path} ({n} file(s))"))

    def redraw(self):
        for p in (self.p_map, self.p_full, self.reading):
            p.redraw()
