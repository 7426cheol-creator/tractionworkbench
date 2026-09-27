"""Operating trajectories: torque sweep at fixed speed or speed sweep at fixed torque."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QFormLayout, QGroupBox, QRadioButton, QScrollArea, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from ...i18n import tr
from ...plots import figures as F
from ...viz import maps as M
from ...viz import sweeps as SW
from ..opviews import plane_hover
from ..widgets import ConceptNote, KeyValueTable, PlotPanel, error_box, fmt, hint, integer, number, primary_button


def _task(progress, drive, limits, mode, n, T, vdc, points, n_max):
    if mode == "torque":
        sw = SW.torque_sweep(drive, limits, n, vdc, n=points, progress=lambda f, m: progress(0.85 * f, m))
        progress(0.9, tr("id–iq 지도", "id–iq map"))
        plane = M.idiq_plane(drive, limits, n, vdc, None)
    else:
        speeds = np.linspace(0.0, n_max, points)
        sw = SW.speed_sweep(drive, limits, T, vdc, speeds=speeds, progress=lambda f, m: progress(0.85 * f, m))
        progress(0.9, tr("id–iq 지도", "id–iq map"))
        extra = [s for s in np.linspace(0, n_max, 5)[1:-1]]
        plane = M.idiq_plane(drive, limits, n_max, vdc, T, extra_speeds=extra)
    return {"sweep": sw, "plane": plane, "mode": mode}


FIELD_LABELS = [("x", ""), ("status", ""), ("id_A", "id [A]"), ("iq_A", "iq [A]"), ("I_peak_A", "|i| [A]"),
                ("v_cmd_V", "|v_cmd| [V]"), ("v_margin_V", "V margin [V]"), ("m_linear", "m [-]"),
                ("Tshaft_Nm", "T_shaft [N·m]"), ("Pshaft_W", "P_shaft [W]"), ("Pdc_W", "P_dc [W]"), ("Idc_A", "I_dc [A]"),
                ("P_loss_W", "loss [W]"), ("eta", "η [-]"), ("pf", "PF [-]")]


class TrajectoryPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("궤적 종류", "trajectory"))
        f = QFormLayout(g)
        self.m_torque = QRadioButton(tr("토크 스윕 @ 고정 속도 (MTPA → 약계자 → 한계)", "torque sweep @ fixed speed"))
        self.m_speed = QRadioButton(tr("속도 스윕 @ 고정 토크 (기저속도·약계자 진입)", "speed sweep @ fixed torque"))
        grp = QButtonGroup(self)
        grp.addButton(self.m_torque)
        grp.addButton(self.m_speed)
        self.m_speed.setChecked(True)
        self.n = number(12000, -30000, 30000, "rpm", 1, 100)
        self.T = number(150, -5000, 5000, "N·m", 2, 5)
        self.vdc = number(600, 1, 2000, "V", 1, 10)
        self.points = integer(81, 11, 401, tr("점", "pts"))
        f.addRow(self.m_speed)
        f.addRow(tr("토크 (속도 스윕)", "torque (speed sweep)"), self.T)
        f.addRow(self.m_torque)
        f.addRow(tr("속도 (토크 스윕)", "speed (torque sweep)"), self.n)
        f.addRow("Vdc", self.vdc)
        f.addRow(tr("표본 수", "samples"), self.points)
        v.addWidget(g)
        self.run_btn = primary_button(tr("궤적 계산", "compute trajectory"))
        self.run_btn.clicked.connect(self.run)
        v.addWidget(self.run_btn)
        v.addWidget(hint(tr("각 점은 해당 토크·속도의 최소전류 정책점입니다. 초록 = DC 포함 가능, 주황 = DC 한계 위반, 붉은 배경 = 전기적 해 없음(증명), "
                            "보라 배경 = 전압 제한(약계자) 운전. 표본 사이의 연속성은 보장하지 않습니다.",
                            "Each point is the minimum-current policy point. Green = feasible incl. DC, orange = DC limit "
                            "violated, red = no electrical solution (proven), purple = voltage-limited (field weakening).")))
        v.addWidget(ConceptNote(tr(
            "<b>MTPA → 약계자</b>: 저속에서는 MTPA 곡선 위(같은 토크를 최소 전류로)에서 운전합니다. 속도가 오르면 역기전력이 커져 "
            "전압 타원이 작아지고, 운전점은 타원을 따라 음의 id 쪽으로 이동합니다(약계자). 그 전환 속도가 기저속도입니다. "
            "더 올라가면 전류·DC 전력 한계에 걸려 토크가 줄어듭니다.",
            "<b>MTPA → field weakening</b>: at low speed the drive runs on the MTPA curve; as speed rises the back-EMF grows, "
            "the voltage ellipse shrinks and the operating point moves along it towards negative id (field weakening). The "
            "transition speed is the base speed; further up, current and DC power limits reduce the torque.")))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(320)
        split.addWidget(sc)
        self.tabs = QTabWidget()
        self.p_plane = PlotPanel()
        self.p_vars = PlotPanel()
        self.table = KeyValueTable(headers=[lab or key for key, lab in FIELD_LABELS])
        self.tabs.addTab(self.p_plane, tr("dq 전류 궤적", "dq current trajectory"))
        self.tabs.addTab(self.p_vars, tr("변수 추이", "quantities"))
        self.tabs.addTab(self.table, tr("표", "table"))
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 1100])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)

    def run(self):
        s = self.win.state
        mode = "torque" if self.m_torque.isChecked() else "speed"
        pts = self.points.value()
        if s.is_flux_map():
            pts = min(pts, 41)
        self.run_btn.setEnabled(False)
        self.win.runner.run("trajectory", tr("궤적", "trajectory"), _task, self._show, s.drive, s.limits, mode,
                            self.n.value(), self.T.value(), self.vdc.value(), pts, s.speed_max(), on_error=self._err)

    def _err(self, msg, tb):
        self.run_btn.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _show(self, res):
        self.run_btn.setEnabled(True)
        sw, pl = res["sweep"], res["plane"]
        if res["mode"] == "torque":
            title = tr(f"토크 스윕 · n = {sw['speed_rpm']:g} rpm · Vdc = {sw['Vdc_V']:g} V",
                       f"torque sweep · n = {sw['speed_rpm']:g} rpm · Vdc = {sw['Vdc_V']:g} V")
        else:
            title = tr(f"속도 스윕 · T = {sw['T_Nm']:g} N·m · Vdc = {sw['Vdc_V']:g} V (전압 타원: 여러 속도)",
                       f"speed sweep · T = {sw['T_Nm']:g} N·m · Vdc = {sw['Vdc_V']:g} V (voltage ellipses at several speeds)")
        csv = lambda sw=sw: {k: sw[k] for k, _ in FIELD_LABELS}
        self.p_plane.draw(F.fig_idiq, pl, sw, title=title, name="trajectory_dq", csv=csv, hover=plane_hover(pl))
        self.p_vars.draw(F.fig_sweep, sw, title=title, name="trajectory_quantities", csv=csv)
        names = {0: "OK", 1: "DC_LIMIT", 2: "NO_SOLUTION", 3: "UNKNOWN"}
        rows = []
        for i in range(len(sw["x"])):
            rows.append([fmt(sw["x"][i]), names[int(sw["status"][i])]] + [fmt(sw[k][i]) for k, _ in FIELD_LABELS[2:]])
        self.table.set_rows(rows)

    def redraw(self):
        self.p_plane.redraw()
        self.p_vars.redraw()
