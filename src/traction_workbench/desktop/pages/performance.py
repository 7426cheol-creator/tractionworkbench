"""T-n envelope, along-envelope quantities and efficiency/loss maps."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ...i18n import tr
from ...plots import figures as F
from ...viz import maps as M
from ...viz import sweeps as SW
from ..widgets import ConceptNote, PlotPanel, combo, error_box, hint, number, primary_button

RESOLUTION = {"fast": (25, 24, 21), "normal": (41, 40, 33), "fine": (61, 60, 49)}


def _task(progress, drive, limits, vdc, compare, res, flux_map):
    ns, nt, ne = RESOLUTION[res]
    if flux_map:
        ns, nt, ne = min(ns, 17), min(nt, 16), min(ne, 17)
    parts = 2 + len(compare)
    env = SW.envelope(drive, limits, vdc, n=ne, progress=lambda f, m: progress(f / parts, m))
    envs = []
    for i, v in enumerate(compare):
        envs.append((f"Vdc = {v:g} V", SW.envelope(drive, limits, v, n=ne,
                                                    progress=lambda f, m, i=i: progress((1 + i + f) / parts, m))))
    tmax = np.nanmax(env["max"]["electrical_T_Nm"]) if np.isfinite(env["max"]["electrical_T_Nm"]).any() else None
    tmin = np.nanmin(env["min"]["electrical_T_Nm"]) if np.isfinite(env["min"]["electrical_T_Nm"]).any() else None
    speeds, torques = M.default_axes(drive, limits, vdc, ns, nt, T_max=tmax, T_min=tmin)
    mp = M.tn_map(drive, limits, vdc, speeds, torques, progress=lambda f, m: progress((parts - 1 + f) / parts, m))
    return {"env": env, "compare": envs, "map": mp}


class PerformancePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.res = None
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("조건", "conditions"))
        f = QFormLayout(g)
        self.vdc = number(600, 1, 2000, "V", 1, 10)
        self.compare = QLineEdit("450")
        self.compare.setToolTip(tr("쉼표로 구분한 비교 Vdc 목록 (예: 450, 700)", "comma-separated Vdc values to compare"))
        self.res_combo = combo([(tr("빠름", "fast"), "fast"), (tr("보통", "normal"), "normal"), (tr("정밀", "fine"), "fine")], "normal")
        f.addRow("Vdc", self.vdc)
        f.addRow(tr("비교 Vdc", "compare Vdc"), self.compare)
        f.addRow(tr("해상도", "resolution"), self.res_combo)
        v.addWidget(g)
        self.run_btn = primary_button(tr("성능 곡선·맵 계산", "compute envelope & maps"))
        self.run_btn.clicked.connect(self.run)
        v.addWidget(self.run_btn)
        v.addWidget(hint(tr("곡선: 속도 표본마다 정책 capability(스캔+bisection) — 표본 사이는 보장하지 않음. 맵: 각 격자점의 최소전류 "
                            "정책점. 빗금 = 정책점이 DC 한계를 위반. 보라 점선 = 기저속도 곡선(이보다 빠르면 약계자). "
                            "flux map 드라이브는 계산량 때문에 해상도를 자동으로 낮춥니다.",
                            "Envelope: policy capability per speed sample (not guaranteed between samples). Map: minimum-current "
                            "policy point per node. Hatched = policy point violates a DC limit. Purple dashed = base-speed curve.")))
        v.addWidget(ConceptNote(tr(
            "<b>T–n 곡선과 맵</b>: 각 속도에서 최소전류 정책(DC 한계 포함)으로 낼 수 있는 최대·최소 토크입니다. 저속은 전류 한계(일정 "
            "토크), 고속은 전압·DC 전력 한계(일정 출력 부근)로 줄어들며, 곡선 색은 그 구간에서 활성인 제약입니다. 맵은 각 격자점의 정책 "
            "운전점 기준 효율·손실·전류 등이고, 보라 점선(기저속도) 오른쪽이 약계자 영역, 빗금은 DC 한계를 넘는 점입니다.",
            "<b>Envelope and maps</b>: maximum/minimum torque of the minimum-current policy (incl. DC) per speed: current-limited "
            "(constant torque) at low speed, voltage/DC-power-limited at high speed; colours show the active constraint. Maps show "
            "efficiency, losses, current… of the policy point per node; right of the purple dashed base-speed curve the drive "
            "field-weakens; hatched = DC limit exceeded.")))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(300)
        split.addWidget(sc)
        self.tabs = QTabWidget()
        self.p_env = PlotPanel()
        self.p_detail = PlotPanel()
        mapw = QWidget()
        ml = QVBoxLayout(mapw)
        ml.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("맵 변수:", "quantity:")))
        self.quantity = QComboBox()
        for key, (lab, _s) in F.MAP_QUANTITIES.items():
            self.quantity.addItem(lab(), key)
        self.quantity.currentIndexChanged.connect(self._draw_map)
        row.addWidget(self.quantity, 1)
        ml.addLayout(row)
        self.p_map = PlotPanel()
        ml.addWidget(self.p_map, 1)
        self.tabs.addTab(self.p_env, tr("T–n 성능 곡선", "T–n envelope"))
        self.tabs.addTab(mapw, tr("효율·손실 맵", "efficiency / loss maps"))
        self.tabs.addTab(self.p_detail, tr("최대 토크 곡선 상세", "along the max-torque envelope"))
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([310, 1100])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)

    def run(self):
        try:
            compare = [float(x) for x in self.compare.text().replace(";", ",").split(",") if x.strip()]
        except ValueError:
            error_box(self, tr("입력 오류", "input error"), tr("비교 Vdc는 숫자 목록이어야 합니다.", "compare Vdc must be numbers"))
            return
        s = self.win.state
        self.run_btn.setEnabled(False)
        self.win.runner.run("performance", tr("성능 곡선·맵", "envelope & maps"), _task, self._show, s.drive, s.limits,
                            self.vdc.value(), compare, self.res_combo.currentData(), s.is_flux_map(), on_error=self._err)

    def _err(self, msg, tb):
        self.run_btn.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _show(self, res):
        self.run_btn.setEnabled(True)
        self.res = res
        env = res["env"]
        csv = lambda env=env: {"speed_rpm": env["x"], "policy_max_Nm": env["max"]["T_Nm"],
                               "electrical_max_Nm": env["max"]["electrical_T_Nm"], "policy_min_Nm": env["min"]["T_Nm"],
                               "electrical_min_Nm": env["min"]["electrical_T_Nm"],
                               "id_at_max_A": env["max"]["id_A"], "iq_at_max_A": env["max"]["iq_A"]}
        self.p_env.draw(F.fig_envelope, env, (), res["compare"], name=f"envelope_{env['Vdc_V']:.0f}V", csv=csv)
        det = {**env["max"], "x": env["x"], "x_kind": "speed", "current_limit_A": env["current_limit_A"],
               "voltage_budget_V": env["voltage_budget_V"], "P_dis_eff_W": env["P_dis_eff_W"],
               "P_chg_eff_W": env["P_chg_eff_W"], "Vdc_V": env["Vdc_V"]}
        self.p_detail.draw(F.fig_sweep, det, title=tr("최대 토크(정책) 곡선을 따라가는 운전점", "operating points along the max-torque (policy) envelope"),
                           name="envelope_detail", csv=csv)
        self._draw_map()

    def _draw_map(self):
        if self.res is None:
            return
        q = self.quantity.currentData()
        mp = self.res["map"]
        sp, tq = mp["speeds"], mp["torques"]

        def hover(x, y, _ax, mp=mp):
            j = int(np.argmin(np.abs(sp - x)))
            i = int(np.argmin(np.abs(tq - y)))
            g = mp["grids"]
            st = {0: "OK", 1: "DC_LIMIT", 2: "NO_SOLUTION", 3: "UNKNOWN"}[int(mp["status"][i, j])]
            parts = [f"n = {sp[j]:.0f} rpm", f"T = {tq[i]:.1f} N·m", st]
            for key, lab, sc in (("eta", "η", 100), ("I_rms_A", "I_rms", 1), ("id_A", "id", 1), ("iq_A", "iq", 1),
                                 ("P_loss_W", "loss kW", 1e-3), ("m_linear", "m", 1), ("pf", "PF", 1)):
                val = g[key][i, j]
                if np.isfinite(val):
                    parts.append(f"{lab} = {val * sc:.4g}")
            return "   ".join(parts) + tr("   (가장 가까운 격자점)", "   (nearest node)")

        def csv(mp=mp, q=q):
            SP, TQ = np.meshgrid(sp, tq)
            out = {"speed_rpm": SP.ravel(), "torque_Nm": TQ.ravel(), "status": mp["status"].ravel()}
            for k, arr in mp["grids"].items():
                out[k] = arr.ravel()
            return out
        self.p_map.draw(F.fig_map, mp, q, self.res["env"], name=f"map_{q}", csv=csv, hover=hover)

    def redraw(self):
        for p in (self.p_env, self.p_detail, self.p_map):
            p.redraw()
