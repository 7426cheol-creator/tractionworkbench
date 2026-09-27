"""Operating-point views shared by the Decision and Explorer pages."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from ..i18n import tr
from ..physics import DriveKernel
from ..plots import figures as F
from ..viz import operating as O
from .widgets import KeyValueTable, PlotPanel, fmt


def plane_hover(pl: dict):
    """Readout at the cursor on an id-iq plane: torque, command voltage, current, DC power (model values)."""
    x, y = pl["x"], pl["y"]

    def _h(xd, yd, _ax):
        i = int(np.clip(round((yd - y[0]) / (y[1] - y[0])), 0, y.size - 1))
        j = int(np.clip(round((xd - x[0]) / (x[1] - x[0])), 0, x.size - 1))
        T, V = pl["T"][i, j], pl["V"][i, j]
        s = f"id = {xd:8.1f} A   iq = {yd:8.1f} A   |i| = {math.hypot(xd, yd):7.1f} A"
        if np.isfinite(T):
            s += f"   T = {T:8.2f} N·m   |v_cmd| = {V:7.1f} V (V_b {pl['budget_V']:.1f})"
        if pl.get("Pdc") is not None and np.isfinite(pl["Pdc"][i, j]):
            s += f"   P_dc = {pl['Pdc'][i, j] / 1e3:8.2f} kW"
        ok = pl["electrical_ok"][i, j]
        s += "   " + (tr("전기적 가능", "feasible") if ok else tr("제약 위반", "violates limits"))
        return s + tr("   (격자 근사값)", "   (grid value)")
    return _h


def point_rows(pt) -> list:
    d = pt.to_dict()
    rows = [
        ("id / iq [A peak]", f"{pt.id_A:.4f} / {pt.iq_A:.4f}"),
        (tr("|i| peak / rms [A]", "|i| peak / rms [A]"), f"{pt.i_peak_A:.4f} / {pt.i_phase_rms_A:.4f}"),
        ("ψd / ψq [Wb]", f"{pt.psi_d_Wb:.6f} / {pt.psi_q_Wb:.6f}"),
        ("vd / vq [V peak]", f"{pt.vd_V:.4f} / {pt.vq_V:.4f}"),
        (tr("|v| 상 peak / 선간 rms [V]", "|v| phase peak / LL rms [V]"), f"{pt.v_peak_V:.4f} / {pt.v_LL_rms_V:.4f}"),
        (tr("명령 전압 / 예산 / 여유 [V]", "command / budget / margin [V]"),
         f"{pt.v_cmd_peak_V:.4f} / {pt.voltage_budget_V:.4f} / {pt.voltage_margin_V:.4f}"),
        (tr("전자기 / 축 토크 [N·m]", "Te / T_shaft [N·m]"), f"{pt.Te_Nm:.4f} / {fmt(pt.Tshaft_Nm)}"),
        (tr("회전 손실 토크 [N·m]", "rotational loss torque [N·m]"), fmt(pt.tau_rot_Nm)),
        ("P_shaft / P_ac / P_dc [kW]", f"{fmt(None if pt.Pshaft_W is None else pt.Pshaft_W / 1e3)} / {pt.Pac_W / 1e3:.4f} / "
                                       f"{fmt(None if pt.Pdc_W is None else pt.Pdc_W / 1e3)}"),
        (tr("손실 동/회전/인버터 [W]", "loss Cu/rot/inv [W]"), f"{pt.Pcu_W:.2f} / {fmt(pt.Prot_W)} / {fmt(pt.Pinv_W)}"),
        (tr("DC 평균 전류 [A]", "DC average current [A]"), fmt(pt.Idc_A)),
        (tr("에너지 모드 / 효율", "energy mode / efficiency"), f"{pt.energy_mode} / {fmt(pt.efficiency)}"),
        (tr("효율 정의", "efficiency definition"), pt.efficiency_note),
        (tr("전기 주파수 [Hz]", "electrical frequency [Hz]"), f"{pt.f_e_Hz:.4f}"),
        (tr("PWM/전기 주파수비", "PWM / electrical ratio"), fmt(pt.pwm_ratio)),
        (tr("RMS 해석", "RMS interpretation"), pt.rms_interpretation),
    ]
    res = d["power_identity_residuals_W"]
    rows.append((tr("전력 항등식 잔차 [W]", "power identity residuals [W]"),
                 f"{fmt(res['Pac - (Te*wm + Pcu)'], 3)}, {fmt(res['Pac - (Pshaft + Pcu + Prot)'], 3)}, "
                 f"{fmt(res['Pdc - (Pac + Pinv)'], 3)} (tol {fmt(res['tolerance'], 3)}) → {'OK' if res['ok'] else 'NG'}"))
    for c in pt.constraints:
        rows.append((f"{tr('제약', 'constraint')} {c.name}", f"{c.state} · {tr('요구', 'demand')} {fmt(c.demand)} / "
                     f"{tr('한계', 'limit')} {fmt(c.limit)} {c.unit} · {tr('여유', 'slack')} {fmt(c.slack)}"))
    for n in pt.notes:
        rows.append((tr("주석", "note"), n))
    return rows


class OperatingViews(QWidget):
    """Tabs: id-iq map, waveforms, phasor/hexagon, power/constraints, numeric table."""

    plane_clicked = Signal(float, float)

    def __init__(self, parent=None, clickable: bool = False):
        super().__init__(parent)
        self.tabs = QTabWidget()
        self.map = PlotPanel(hint=tr("계산 후 id–iq 제약 지도가 표시됩니다.", "The id–iq constraint map appears after a run."))
        self.wave = PlotPanel(hint=tr("운전점의 상전류·전압·듀티·쇄교자속 파형", "phase current, voltage, duty and flux waveforms"))
        self.phasor = PlotPanel(hint=tr("dq 벡터도와 공간벡터 육각형", "dq phasor diagram and space-vector hexagon"))
        self.power = PlotPanel(hint=tr("전력 흐름과 제약 사용률", "power chain and constraint utilisation"))
        self.table = KeyValueTable()
        self.tabs.addTab(self.map, tr("id–iq 제약 지도", "id–iq map"))
        self.tabs.addTab(self.wave, tr("상 파형", "phase waveforms"))
        self.tabs.addTab(self.phasor, tr("벡터도·육각형", "phasor · hexagon"))
        self.tabs.addTab(self.power, tr("전력·제약", "power · constraints"))
        self.tabs.addTab(self.table, tr("수치", "numbers"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tabs)
        if clickable:
            self.map.clicked.connect(self.plane_clicked)
        self.pv = None
        self.plane = None

    def clear(self, msg: str):
        for p in (self.map, self.wave, self.phasor, self.power):
            p.placeholder(msg)
        self.table.set_rows([])

    def show_plane(self, plane: dict, traj=None, title=None):
        self.plane = plane
        name = f"idiq_{plane['speed_rpm']:.0f}rpm_{plane['Vdc_V']:.0f}V"
        self.map.draw(F.fig_idiq, plane, traj, title=title, name=name, hover=plane_hover(plane),
                      csv=None if traj is None else (lambda: {k: traj[k] for k in ("x", "id_A", "iq_A", "status")}))

    def show_point(self, pv: O.PointView, title: str):
        self.pv = pv
        w = O.waveforms(pv)
        tag = f"{pv.point.speed_rpm:.0f}rpm_{pv.point.Vdc_V:.0f}V"
        self.wave.draw(F.fig_waveforms, w, title=title, name=f"waveforms_{tag}",
                       csv=lambda w=w: {("t_ms" if w["x_kind"] == "time" else "theta_deg"): w["x"],
                                        "ia_A": w["i_abc"][0], "ib_A": w["i_abc"][1], "ic_A": w["i_abc"][2],
                                        "va_V": w["v_abc"][0], "vb_V": w["v_abc"][1], "vc_V": w["v_abc"][2],
                                        "vab_V": w["v_ll"][0], "da": w["duty_abc"][0], "db": w["duty_abc"][1],
                                        "dc": w["duty_abc"][2], "psi_a_Wb": w["psi_abc"][0], "p_inst_W": w["p_inst_W"]})
        self.phasor.draw(F.fig_phasor, O.phasor(pv), O.hexagon(pv), title=title, name=f"phasor_{tag}")
        rows = O.constraint_rows(pv.point)
        self.power.draw(F.fig_power_constraints, O.power_chain(pv.point), rows, title=title, name=f"power_{tag}",
                        csv=lambda rows=rows: {k: [r[k] for r in rows] for k in ("name", "group", "state", "demand",
                                                                                   "limit", "slack", "utilization")})
        self.table.set_rows(point_rows(pv.point))

    def redraw(self):
        for p in (self.map, self.wave, self.phasor, self.power):
            p.redraw()


def kernel_for(drive, scenario) -> DriveKernel:
    return DriveKernel(drive, scenario)
