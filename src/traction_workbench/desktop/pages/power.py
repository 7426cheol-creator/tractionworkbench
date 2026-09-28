"""Power electronics page: datasheet module losses coupled to P_dc (review 8.8), DC-link capacitor current /
ripple / ESR loss (8.9) and thermal-cycle counting with conditional damage (12).

All inputs are tables (datasheet curves temperature x current, ESR(f), mission segments, Foster stages) that
accept a block pasted from a spreadsheet; the module description can also be saved/loaded as JSON.
"""

from __future__ import annotations

import copy
import csv
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from ... import api
from ...extensions.dclink_ripple import LOCATIONS, QUANTITIES
from ...i18n import tr
from ...plots import review_figures as RF
from ..widgets import (ConceptNote, KeyValueTable, NumTable, PlotPanel, check, combo, error_box, fmt, hint, integer,
                       number, parse_clipboard_grid, primary_button, table_with_buttons)

NOTE_MODULE = lambda: tr(
    "<b>데이터시트 기반 모듈 손실</b>: 결정 운전점(id, iq, vd, vq)에서 전기각 θ에 걸쳐 PWM 듀티(SVPWM/SPWM/DPWM1)를 평균해 "
    "상·하단 스위치와 다이오드(또는 SiC 동기정류 + 데드타임 중 body diode) 손실을 따로 계산합니다. 곡선 범위 밖(온도·전류)은 "
    "<b>외삽하지 않고</b> 미확립으로 둡니다. 스위칭 에너지는 시험전압 V_test 기준이며, 전압 스케일링은 근거와 유효 범위를 "
    "선언했을 때만 적용합니다. E_on/E_off가 '소자별'인지 '정류 쌍 합계'인지(energy basis)를 반드시 선택하세요.<br>"
    "이 모델을 켜면 P_inv가 합성 2차 대리식 대신 이 모델로 계산되어 P_dc·DC 판정·열 판정에 들어갑니다. "
    "정지(저속)에서는 한 소자가 전류를 계속 운반하므로 핫스팟은 총손실/6이 아니라 최악 전기각의 소자 손실입니다. "
    "typical 값은 보장 상한이 아닙니다.",
    "<b>Datasheet module losses</b>: at the decision point (id, iq, vd, vq) the PWM duty (SVPWM/SPWM/DPWM1) is averaged "
    "over the electrical angle θ, and upper/lower switch and diode losses (or SiC synchronous rectification plus the "
    "body diode during dead time) are computed separately. Outside the curves (temperature, current) nothing is "
    "<b>extrapolated</b>; the loss is not established. Switching energies refer to the test voltage V_test; voltage "
    "scaling applies only with a declared basis and range. Select whether E_on/E_off are per device or the "
    "commutation-pair total (energy basis).<br>With this model P_inv comes from it (not the synthetic quadratic "
    "surrogate) and feeds P_dc, the DC claims and thermal. At standstill one device carries the current, so the hotspot "
    "is the worst-angle device loss, not total/6. Typical values are not guaranteed bounds.")

NOTE_RIPPLE = lambda: tr(
    "<b>DC-link 리플</b>: 캐리어 한 주기의 정확한 모멘트로 커패시터 RMS 전류를 구하고(SPWM에서 Kolar 식과 일치), "
    "스위칭 함수 파형과 FFT(Parseval 확인)로 고조파를 얻습니다. 고조파마다 소스 임피던스 Z_s와 커패시터 Z_C로 전류가 "
    "나뉘며(공진 근처에선 커패시터 전류가 인버터 AC 전류보다 클 수 있음), ESR(T)·전류 분배·발열은 <b>한 온도</b>에서 함께 "
    "풉니다(경계 온도에서 출발해 처음 만나는 평형; 종료 사유와 독립 잔차를 표시, 미수렴은 온도·수명 없음). "
    "ESR(f) 표 범위 밖은 임피던스를 모르는 것으로 보고 손실은 미확정, 리플은 모든 수동 임피던스에 대한 범위로만 판정합니다.<br>"
    "요구는 <b>위치·물리량·한계·측정 대역폭</b>이 모두 있어야 하고, 위치×물리량은 정확한 가지/노드로 매핑됩니다 "
    "(capacitor_branch의 current_ac_rms = capacitor_current_rms). 수명은 공급사 수명 데이터가 있을 때만 "
    "('10 K마다 절반' 같은 일반 규칙 미적용).",
    "<b>DC-link ripple</b>: the capacitor RMS current from exact carrier-period moments (matches Kolar's SPWM formula), "
    "harmonics from the switching-function waveform and an FFT (Parseval checked). Each harmonic splits between the "
    "source impedance Z_s and the capacitor Z_C (near resonance the capacitor current can exceed the inverter AC "
    "current); ESR(T), the split and the heat are solved at <b>one temperature</b> (the first equilibrium above the "
    "boundary temperature; termination and an independent residual shown, no temperature or life without "
    "convergence). Outside the ESR(f) table the impedance is unknown: the loss is not established and ripple is "
    "judged only by its range over every passive impedance.<br>A requirement is judged only with <b>location, "
    "quantity, limit and measurement bandwidth</b>, mapped to an exact branch / node (current_ac_rms at "
    "capacitor_branch = capacitor_current_rms). Life only from supplier data (no generic '10 K halves the life').")

NOTE_LIFE = lambda: tr(
    "<b>열 사이클 수명</b>: 미션 구간마다 정책 운전점에서 <b>물리 다이별</b> 손실(IGBT/다이오드 각각, SiC는 채널·바디다이오드를 "
    "한 다이로; 모듈 탭의 데이터시트 모델)을 구하고, 다이마다 Foster 망으로 Tj(t)를 만든 뒤 ASTM E1049 rainflow로 셉니다. "
    "최고 발열 포락선은 한 소자의 이력이 아니므로 피로 입력으로 쓰지 않습니다. 유한 미션은 냉각수 온도에서 출발해 식을 때까지의 "
    "닫힌 블록(시동·정지 1회), 주기 미션은 주기 정상상태 한 주기입니다(워밍업 제외). t_on '상승 시간' 규칙은 각 범위의 "
    "<b>가열 구간</b>(하한을 마지막으로 떠난 때부터 상한 도달까지)이며, 알 수 없으면 1 s로 바꾸지 않고 미확립입니다. "
    "손상은 <b>공급사 사이클링 모델</b>(N_f = A·ΔT^a·exp(b/T)·t_on^c, 근거·유효범위·scatter 필수)이 있을 때만 계산하며, "
    "생성된 스크리닝 체인(고정 Tj 손실)의 이력은 스크리닝 손상일 뿐입니다(판정 UNKNOWN). 자격을 선언한 측정 Tj CSV만 판정에 쓰입니다.",
    "<b>Thermal cycling</b>: per mission segment the loss of each <b>physical die</b> at the policy point (IGBT and "
    "diode separately, SiC channel and body diode as one die; datasheet model of the module tab), one Tj(t) per die "
    "by Foster superposition, then ASTM E1049 rainflow. The hottest-device envelope is not one device's history and "
    "is never a fatigue input. A finite mission is a closed block from the coolant temperature back to it (one "
    "start-up and shutdown); a periodic mission is one period at its periodic steady state (warm-up excluded). The "
    "'rise time' t_on rule is each range's <b>heating interval</b> (last departure from its lower level to arrival at "
    "its upper level); when unknown it stays unknown, never 1 s. Damage only with a <b>supplier cycling model</b> "
    "(N_f = A·dT^a·exp(b/T)·t_on^c with basis, validity and scatter); a history from the generated screening chain "
    "(losses at a fixed Tj) gives screening damage only (UNKNOWN). Only a measured Tj CSV declared qualified is judged.")

CURVES = (("v_on", lambda: tr("순방향 전압 V_on (스위치)", "forward voltage V_on (switch)"), True),
          ("v_rev", lambda: tr("역방향 전압 (다이오드/body)", "reverse voltage (diode / body)"), True),
          ("e_on", lambda: tr("턴온 에너지 E_on", "turn-on energy E_on"), True),
          ("e_off", lambda: tr("턴오프 에너지 E_off", "turn-off energy E_off"), True),
          ("e_rr", lambda: tr("역회복 에너지 E_rr", "reverse-recovery energy E_rr"), False),
          ("v_channel_rev", lambda: tr("SiC 채널 역도통 전압", "SiC channel reverse voltage"), False))


def _floats(text: str) -> list[float]:
    vals = [float(x) for x in text.replace(";", " ").replace(",", " ").split()]
    if not vals:
        raise ValueError("empty list")
    return vals


class CurveGrid(QWidget):
    """Datasheet curves as temperature x current grids (one curve shown at a time)."""

    def __init__(self, curves: dict, parent=None):
        super().__init__(parent)
        self.curves = copy.deepcopy(curves)
        self.current = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.pick = combo([(label(), key) for key, label, _ in CURVES])
        self.pick.currentIndexChanged.connect(self._switch)
        self.use = check(tr("이 곡선 사용", "use this curve"), True)
        top = QHBoxLayout()
        top.addWidget(self.pick, 1)
        top.addWidget(self.use)
        lay.addLayout(top)
        f = QFormLayout()
        self.unit = combo([("V", "V"), ("mJ", "mJ"), ("µJ", "uJ"), ("J", "J")])
        self.temps = QLineEdit()
        self.currents = QLineEdit()
        self.src = QLineEdit()
        self.temps.editingFinished.connect(self._reshape)
        self.currents.editingFinished.connect(self._reshape)
        f.addRow(tr("단위", "unit"), self.unit)
        f.addRow(tr("온도 [°C] (행)", "temperatures [°C] (rows)"), self.temps)
        f.addRow(tr("전류 [A] (열)", "currents [A] (columns)"), self.currents)
        f.addRow(tr("출처", "source"), self.src)
        lay.addLayout(f)
        self.table = NumTable(["—"], min_height=150)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        b = QPushButton(tr("전체 표 붙여넣기 (첫 행=전류, 첫 열=온도)", "paste full grid (row 1 = currents, col 1 = temps)"))
        b.clicked.connect(self.paste_grid)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(hint(tr("셀 선택 후 Ctrl+V로 값 블록 붙여넣기. 곡선 범위 밖은 외삽하지 않습니다.",
                              "Ctrl+V pastes a value block at the selected cell. Nothing outside the grid is extrapolated.")))
        self._switch()

    # -- one curve <-> widgets
    def _store(self):
        key = self.current
        if key is None:
            return
        if not self.use.isChecked():
            self.curves.pop(key, None)
            return
        temps, cur = _floats(self.temps.text()), _floats(self.currents.text())
        vals = self.table.values()
        if len(vals) != len(temps) or any(len(r) != len(cur) for r in vals):
            raise ValueError(tr(f"'{key}' 표 크기가 온도 {len(temps)} × 전류 {len(cur)}와 다릅니다",
                                f"curve '{key}': table must be {len(temps)} temperatures x {len(cur)} currents"))
        self.curves[key] = {"unit": self.unit.currentData(), "temps_C": temps, "currents_A": cur, "values": vals,
                            "source": self.src.text().strip()}

    def _load(self, key):
        c = self.curves.get(key)
        required = next(r for k, _, r in CURVES if k == key)
        self.use.setEnabled(not required)
        self.use.blockSignals(True)
        self.use.setChecked(c is not None or required)
        self.use.blockSignals(False)
        if c is None:
            c = {"unit": "V" if key.startswith("v_") else "mJ", "temps_C": [25.0, 150.0], "currents_A": [0.0, 400.0, 800.0],
                 "values": [[None] * 3, [None] * 3], "source": ""}
        for i in range(self.unit.count()):
            if self.unit.itemData(i) == c["unit"]:
                self.unit.setCurrentIndex(i)
        self.temps.setText(", ".join(fmt(t) for t in c["temps_C"]))
        self.currents.setText(", ".join(fmt(a) for a in c["currents_A"]))
        self.src.setText(c.get("source", ""))
        self._fill(c["temps_C"], c["currents_A"], c["values"])

    def _fill(self, temps, cur, vals):
        self.table.setColumnCount(len(cur))
        self.table.setHorizontalHeaderLabels([f"{fmt(a)} A" for a in cur])
        self.table.load(vals)
        self.table.setVerticalHeaderLabels([f"{fmt(t)} °C" for t in temps])

    def _switch(self, *_):
        try:
            self._store()
        except ValueError as exc:
            error_box(self, tr("곡선 입력 오류", "curve input error"), str(exc))
            for i in range(self.pick.count()):
                if self.pick.itemData(i) == self.current:
                    self.pick.blockSignals(True)
                    self.pick.setCurrentIndex(i)
                    self.pick.blockSignals(False)
            return
        self.current = self.pick.currentData()
        self._load(self.current)

    def _reshape(self):
        try:
            temps, cur = _floats(self.temps.text()), _floats(self.currents.text())
        except ValueError:
            return
        old = []
        for i in range(self.table.rowCount()):
            old.append([(self.table.item(i, j).text() if self.table.item(i, j) else "")
                        for j in range(self.table.columnCount())])
        vals = [[(old[i][j] if i < len(old) and j < len(old[i]) else "") for j in range(len(cur))] for i in range(len(temps))]
        self._fill(temps, cur, vals)

    def paste_grid(self):
        grid = parse_clipboard_grid(QApplication.clipboard().text())
        try:
            cur = [float(x) for x in grid[0][1:] if x]
            temps = [float(r[0]) for r in grid[1:]]
            vals = [[float(x) for x in r[1:1 + len(cur)]] for r in grid[1:]]
            if not cur or not temps or any(len(r) != len(cur) for r in vals):
                raise ValueError
        except (ValueError, IndexError):
            error_box(self, tr("붙여넣기 실패", "paste failed"),
                      tr("첫 행에 전류, 첫 열에 온도, 나머지에 값이 있는 표를 복사하세요.",
                         "copy a grid with currents in the first row, temperatures in the first column"))
            return
        self.temps.setText(", ".join(fmt(t) for t in temps))
        self.currents.setText(", ".join(fmt(a) for a in cur))
        self._fill(temps, cur, vals)

    # -- all curves
    def spec(self) -> dict:
        self._store()
        return copy.deepcopy(self.curves)

    def load(self, curves: dict):
        self.curves = copy.deepcopy(curves)
        self.current = None
        self._switch()


def _module_task(progress, body):
    progress(0.1, tr("모듈 손실 (각도 평균, 토크 스윕)", "module losses (angle average, torque sweep)"))
    return api.module_losses(body)


def _ripple_task(progress, body):
    progress(0.1, tr("리플 파형·스펙트럼", "ripple waveform and spectrum"))
    return api.dclink_ripple(body)


def _life_task(progress, body):
    progress(0.1, tr("미션 → Tj → rainflow", "mission -> Tj -> rainflow"))
    return api.lifetime(body)


def _scroll(form: QWidget) -> QScrollArea:
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(form)
    sc.setMinimumWidth(380)
    return sc


def _result_side(plot: PlotPanel, table: KeyValueTable) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(plot, 3)
    lay.addWidget(table, 2)
    return w


def _claim_text(c: dict) -> str:
    return f"{c['status']} — {c.get('detail', '')}" + (f"  [{', '.join(c['reasons'])}]" if c.get("reasons") else "")


class PowerPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last_module = self.last_ripple = self.last_life = None
        self.trace = None
        self.tabs = QTabWidget()
        self.tabs.addTab(self._module_tab(), tr("모듈 손실 (데이터시트)", "module losses (datasheet)"))
        self.tabs.addTab(self._ripple_tab(), tr("DC-link 리플·커패시터", "DC-link ripple · capacitor"))
        self.tabs.addTab(self._life_tab(), tr("열 사이클 수명", "thermal-cycling life"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.tabs)
        self.load_module(self.win.state.example("MODULE"))
        self.load_ripple(self.win.state.example("RIPPLE"))

    def apply_project(self, _project=None):
        """Product inputs from the active project (module, capacitor, source impedance, switching, junction
        network); operating points, missions and requirements stay."""
        self.load_module(self.win.state.example("MODULE"))
        self.load_ripple(self.win.state.example("RIPPLE"))
        net = self.win.state.example("MISSION").get("junction_network")
        self.l_net.load([] if not net else list(zip(net["R_K_per_W"], net["tau_s"])))

    def load_ripple(self, ex: dict):
        """Capacitor, source impedance and switching of the ripple analysis; fields without a widget (bank count,
        layout, Rth basis, source text) pass through to the request unchanged."""
        cap, src = ex["capacitor"], ex.get("source")
        self._cap_base = dict(cap)
        k = 1e3 if cap.get("ESR_unit") == "ohm" else 1.0                 # the table widget is in mOhm
        self.r_fsw.setValue(float(ex["fsw_kHz"]))
        i = self.r_mod.findData(ex["modulation"])
        if i >= 0:
            self.r_mod.setCurrentIndex(i)
        self.r_C.setValue(float(cap["C_uF"]))
        self.r_esl.setValue(float(cap.get("ESL_nH") or 0.0))
        self.r_rth_on.setChecked(cap.get("Rth_K_per_W") is not None)
        if cap.get("Rth_K_per_W") is not None:
            self.r_rth.setValue(float(cap["Rth_K_per_W"]))
        self.r_tref.setValue(float(cap.get("T_ref_C") or 65.0))
        self.r_alpha.setValue(float(cap.get("ESR_temp_coeff_per_K") or 0.0))
        self.r_tesr.setValue(float(cap["ESR_table_T_C"]) if cap.get("ESR_table_T_C") is not None else 25.0)
        dom = cap.get("T_valid_C")
        self.r_dom_on.setChecked(bool(dom))
        if dom:
            self.r_tmin.setValue(float(dom[0]))
            self.r_tmax.setValue(float(dom[1]))
        self.r_esr.load([[f, r * k] for f, r in cap["ESR_table"]])
        self.r_life.load(cap.get("life_hours_table") or [])
        self.r_life_v.setValue(float(cap.get("life_voltage_V") or 0.0))
        self.r_life_basis.setText(str(cap.get("life_basis") or ""))
        self.r_src_on.setChecked(src is not None)
        if src:
            self.r_srcR.setValue(float(src["R_mohm"]))
            self.r_srcL.setValue(float(src["L_uH"]))
            self.r_src_basis.setText(str(src.get("basis", "")))

    # ================================================================== module tab
    def _module_tab(self):
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("운전점 (정책 해의 id·iq·vd·vq 사용)", "operating point (policy solution id, iq, vd, vq)"))
        f = QFormLayout(g)
        self.m_n = number(12000, 0, 30000, "rpm", 0, 500)
        self.m_vdc = number(600, 1, 2000, "V", 1, 10)
        self.m_T = number(150, -5000, 5000, "N·m", 2, 10)
        for lab, w in ((tr("속도", "speed"), self.m_n), ("Vdc", self.m_vdc), (tr("토크", "torque"), self.m_T)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("모듈 (데이터시트)", "module (datasheet)"))
        f = QFormLayout(g)
        self.m_name = QLineEdit()
        self.m_tech = combo([("IGBT + diode", "IGBT"), ("SiC MOSFET", "SiC_MOSFET")])
        self.m_kind = combo([(tr("typical (보장 상한 아님)", "typical (not a bound)"), "typical"), ("max", "max")])
        self.m_basis = combo([(tr("소자별 (E_rr는 다이오드)", "per device (E_rr on the diode)"), "per_device"),
                              (tr("정류 쌍 합계", "commutation-pair total"), "commutation_pair_total")])
        self.m_vtest = number(600, 1, 3000, "V", 1, 10)
        self.m_src = QLineEdit()
        for lab, w in ((tr("이름", "name"), self.m_name), (tr("소자 기술", "technology"), self.m_tech),
                       (tr("값 종류", "value kind"), self.m_kind), (tr("에너지 기준", "energy basis"), self.m_basis),
                       (tr("시험 전압 V_test", "test voltage V_test"), self.m_vtest), (tr("출처", "source"), self.m_src)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("PWM·구동", "PWM · drive"))
        f = QFormLayout(g)
        self.m_fsw = number(10, 0.5, 200, "kHz", 2, 1)
        self.m_mod = combo([("SVPWM", "svpwm"), ("SPWM", "spwm"), ("DPWM1", "dpwm1")])
        self.m_dt = number(1.5, 0, 20, "µs", 2, 0.1)
        self.m_par = integer(1, 1, 8)
        self.m_share = number(0, 0, 50, "%", 1, 1)
        self.m_aux = number(12, 0, 1000, "W", 1, 1)
        self.m_aux_hv = check(tr("구동 보조전원이 HV DC에서 공급", "driver supply from HV DC"), False)
        for lab, w in ((tr("스위칭 주파수", "switching frequency"), self.m_fsw), (tr("변조", "modulation"), self.m_mod),
                       (tr("데드타임", "dead time"), self.m_dt), (tr("병렬 모듈 수", "parallel modules"), self.m_par),
                       (tr("전류 분배 오차", "sharing error"), self.m_share), (tr("드라이버 보조 손실", "driver aux"), self.m_aux),
                       ("", self.m_aux_hv)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("스위칭 에너지 전압 스케일링 (선언 시만)", "switching-energy voltage scaling (declared only)"))
        f = QFormLayout(g)
        self.m_sc_on = check(tr("스케일링 선언", "declare scaling"), False)
        self.m_sc_exp = number(1.0, 0, 3, "", 3, 0.05)
        self.m_sc_lo = number(400, 0, 3000, "V", 1, 10)
        self.m_sc_hi = number(800, 0, 3000, "V", 1, 10)
        self.m_sc_basis = QLineEdit()
        self.m_sc_basis.setPlaceholderText(tr("근거 (측정 보고서 등)", "basis (measurement report ...)"))
        for lab, w in (("", self.m_sc_on), (tr("지수 (E ∝ V^k)", "exponent (E ∝ V^k)"), self.m_sc_exp),
                       (tr("유효 하한", "valid from"), self.m_sc_lo), (tr("유효 상한", "valid to"), self.m_sc_hi),
                       (tr("근거", "basis"), self.m_sc_basis)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("곡선 (온도 × 전류)", "curves (temperature x current)"))
        gl = QVBoxLayout(g)
        self.grid = CurveGrid(self.win.state.example("MODULE")["curves"])
        gl.addWidget(self.grid)
        v.addWidget(g)
        g = QGroupBox(tr("열·판정", "thermal · judgement"))
        f = QFormLayout(g)
        self.m_tj = number(150, -40, 200, "°C", 1, 5, tip=tr("곡선을 읽을 정션 온도", "junction temperature for the curves"))
        self.m_rth_on = check(tr("전열 고정점 계산 (Rth 사용)", "electrothermal fixed point (use Rth)"), True)
        self.m_rth = number(0.09, 0.0001, 10, "K/W", 4, 0.01)
        self.m_tref = number(65, -40, 150, "°C", 1, 1)
        self.m_pal_on = check(tr("허용 손실 선언", "declare allowed loss"), False)
        self.m_pal = number(2500, 0, 1e6, "W", 1, 50)
        for lab, w in ((tr("평가 Tj", "evaluation Tj"), self.m_tj), ("", self.m_rth_on),
                       (tr("최고 소자 Rth(j-냉각수)", "hottest device Rth(j-coolant)"), self.m_rth),
                       (tr("냉각수 기준 온도", "coolant reference"), self.m_tref), ("", self.m_pal_on),
                       (tr("허용 반도체 손실", "allowed semiconductor loss"), self.m_pal)):
            f.addRow(lab, w)
        v.addWidget(g)
        row = QHBoxLayout()
        for text, fn in ((tr("JSON 불러오기", "load JSON"), self.load_module_file),
                         (tr("JSON 저장", "save JSON"), self.save_module_file),
                         (tr("프로젝트 값으로 초기화", "reset to the project"),
                          lambda: self.load_module(self.win.state.example("MODULE"))),
                         (tr("데이터시트 값 입력…", "datasheet values…"),
                          lambda: self.win.pages["project"].enter_datasheet("module"))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        self.m_to_project = QPushButton(tr("이 모듈을 프로젝트에 반영…", "apply this module to the project…"))
        self.m_to_project.setToolTip(tr("곡선·시험 조건·Tj·Rth를 프로젝트의 module 섹션으로 (수정된 작업 사본). "
                                        "스위칭 주파수·변조·데드타임은 제어기 섹션의 값이라 반영하지 않습니다.",
                                        "curves, test conditions, Tj and Rth become the project's module section (a "
                                        "modified working copy); fsw, modulation and dead time are controller data "
                                        "and are not applied"))
        self.m_to_project.clicked.connect(lambda: self.module_to_project())
        v.addWidget(self.m_to_project)
        self.m_btn = primary_button(tr("모듈 손실 계산", "compute module losses"))
        self.m_btn.clicked.connect(self.run_module)
        v.addWidget(self.m_btn)
        v.addWidget(hint(tr("예시 곡선은 합성 선형 곡선입니다(실제 제품 아님). 실제 데이터시트 값을 넣으세요.",
                            "The example curves are synthetic linear stand-ins (not a product); enter datasheet values.")))
        v.addWidget(ConceptNote(NOTE_MODULE()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.p_mod = PlotPanel(hint=tr("소자 위치별 손실 · 토크 스윕 (모듈 vs 대리식) · 정지 핫스팟",
                                       "per-position loss · torque sweep (module vs surrogate) · standstill hotspot"))
        self.t_mod = KeyValueTable()
        split.addWidget(_result_side(self.p_mod, self.t_mod))
        split.setStretchFactor(1, 1)
        split.setSizes([400, 1060])
        return split

    def load_module(self, m: dict):
        self.m_name.setText(m.get("name", ""))
        for w, key, default in ((self.m_tech, "technology", "IGBT"), (self.m_kind, "value_kind", "typical"),
                                (self.m_basis, "energy_basis", "per_device"), (self.m_mod, "modulation", "svpwm")):
            val = m.get(key, default)
            for i in range(w.count()):
                if w.itemData(i) == val:
                    w.setCurrentIndex(i)
        self.m_vtest.setValue(float(m.get("v_test_V", 600.0)))
        self.m_src.setText(m.get("source", ""))
        self.m_fsw.setValue(float(m.get("fsw_kHz", 10.0)))
        self.m_dt.setValue(float(m.get("deadtime_us") or 0.0))
        self.m_par.setValue(int(m.get("parallel", 1)))
        self.m_share.setValue(float(m.get("sharing_error_pct") or 0.0))
        self.m_aux.setValue(float(m.get("driver_aux_W") or 0.0))
        self.m_aux_hv.setChecked(bool(m.get("aux_from_hv_dc")))
        sc = m.get("vdc_scaling") or {}
        self.m_sc_on.setChecked(sc.get("exponent") not in (None, ""))
        if sc.get("exponent") not in (None, ""):
            self.m_sc_exp.setValue(float(sc["exponent"]))
            lo, hi = (sc.get("valid_V") or (0.0, 3000.0))
            self.m_sc_lo.setValue(float(lo))
            self.m_sc_hi.setValue(float(hi))
            self.m_sc_basis.setText(str(sc.get("basis", "")))
        self.m_tj.setValue(float(m.get("Tj_eval_C", 150.0)))
        self.m_rth_on.setChecked(bool(m.get("Rth_K_per_W")))
        if m.get("Rth_K_per_W"):
            self.m_rth.setValue(float(m["Rth_K_per_W"]))
        self.m_tref.setValue(float(m.get("T_ref_C", 65.0)))
        self._test_conditions = dict(m.get("test_conditions") or {})
        self.grid.load(m.get("curves") or {})

    def module_to_project(self, origin: str | None = None, source: str | None = None):
        """The page's module (datasheet curves pasted or loaded here) becomes the project's module section."""
        from ...project import module_section_from_spec
        st = self.win.state
        prj = st.project
        try:
            data, notes = module_section_from_spec(self.module_spec(), prj.data("module") if prj.has("module") else None,
                                                   prj.data("controller") if prj.has("controller") else None)
        except Exception as exc:  # noqa: BLE001 - shown with the reason; the project is unchanged
            error_box(self, tr("반영할 수 없음", "cannot apply"), str(exc))
            return None
        title = tr("모듈을 프로젝트에 반영", "apply the module to the project")
        if origin is None:
            origins = ["supplier", "measured", "estimated", "synthetic"]
            origin, ok = QInputDialog.getItem(self, title, tr("이 곡선의 출처", "origin of these curves"), origins, 0, False)
            if not ok:
                return None
        if source is None:
            source, ok = QInputDialog.getText(self, title, tr("근거 (데이터시트 이름·개정, 측정 보고서 등)",
                                                              "basis (datasheet and revision, test report, ...)"))
            if not ok:
                return None
            if notes and QMessageBox.question(self, title, tr("반영하지 않는 항목:\n", "not applied:\n") + "\n".join(notes)
                                              + tr("\n\n계속할까요?", "\n\nContinue?")) != QMessageBox.Yes:
                return None
        prov = {"origin": origin, "source": source or tr("전력변환 페이지 편집", "power page edit"),
                "revision": "", "qualified": False, "evidence": ""}
        st.set_project(prj.with_section("module", data, prov))
        return notes

    def module_spec(self) -> dict:
        m = {"name": self.m_name.text().strip(), "technology": self.m_tech.currentData(),
             "value_kind": self.m_kind.currentData(), "energy_basis": self.m_basis.currentData(),
             "v_test_V": self.m_vtest.value(), "source": self.m_src.text().strip(),
             "test_conditions": dict(getattr(self, "_test_conditions", {})), "curves": self.grid.spec(),
             "fsw_kHz": self.m_fsw.value(), "modulation": self.m_mod.currentData(), "deadtime_us": self.m_dt.value(),
             "parallel": self.m_par.value(), "sharing_error_pct": self.m_share.value(),
             "driver_aux_W": self.m_aux.value(), "aux_from_hv_dc": self.m_aux_hv.isChecked(),
             "Tj_eval_C": self.m_tj.value(), "Rth_K_per_W": self.m_rth.value() if self.m_rth_on.isChecked() else None,
             "T_ref_C": self.m_tref.value()}
        if self.m_sc_on.isChecked():
            m["vdc_scaling"] = {"exponent": self.m_sc_exp.value(), "valid_V": [self.m_sc_lo.value(), self.m_sc_hi.value()],
                                "basis": self.m_sc_basis.text().strip()}
        return m

    def module_body(self) -> dict:
        body = self.win.state.body(speed_rpm=self.m_n.value(), Vdc_V=self.m_vdc.value(), torque_Nm=self.m_T.value(),
                                   module=self.module_spec())
        if self.m_pal_on.isChecked():
            body["P_allow_W"] = self.m_pal.value()
        return body

    def load_module_file(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("모듈 JSON 불러오기", "load module JSON"), "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                m = json.load(fh)
            api.module_model_from_dict(m)            # validates before the form is replaced
            self.load_module(m)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("불러오기 실패", "load failed"), str(exc))

    def save_module_file(self):
        try:
            m = self.module_spec()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("모듈 JSON 저장", "save module JSON"), "module.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(m, fh, indent=2, ensure_ascii=False)

    def run_module(self):
        try:
            body = self.module_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.m_btn.setEnabled(False)
        self.win.runner.run("module", tr("모듈 손실", "module losses"), _module_task, self._show_module, body,
                            on_error=self._err)

    def _err(self, msg, tb):
        for b in (self.m_btn, self.r_btn, self.l_btn):
            b.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _show_module(self, res):
        self.m_btn.setEnabled(True)
        self.last_module = res
        rows = res.get("torque_sweep") or []
        self.p_mod.draw(RF.fig_module_losses, res, name="module_losses",
                        csv=lambda rows=rows: {k: [r[k] for r in rows] for k in
                                               ("torque_Nm", "module_W", "conduction_W", "switching_W", "hottest_W",
                                                "surrogate_W")})
        out = [(tr("모듈", "module"), f"{res['module']['name']} ({res['module']['technology']}, "
                                      f"{res['module']['value_kind']}, {res['module']['energy_basis']})")]
        if "operating_point" not in res:
            out.append((tr("운전점", "operating point"), res.get("note", "")))
            self.t_mod.set_rows(out)
            return
        op, lo = res["operating_point"], res["losses"]
        out += [(tr("판정", "claim"), _claim_text(res["claim"])),
                (tr("운전점 id, iq, |i|", "point id, iq, |i|"),
                 f"{fmt(op['id_A'])}, {fmt(op['iq_A'])} A, {fmt(op['i_peak_A'])} A peak"),
                ("P_ac", f"{fmt(op['Pac_W'])} W"),
                (tr("P_inv 모듈 / 대리식", "P_inv module / surrogate"),
                 f"{fmt(op['Pinv_module_W'])} W / {fmt(op['Pinv_surrogate_W'])} W"),
                (tr("P_dc 모듈 / 대리식", "P_dc module / surrogate"),
                 f"{fmt(op['Pdc_module_W'])} W / {fmt(op['Pdc_surrogate_W'])} W"),
                (tr("도통 / 스위칭", "conduction / switching"),
                 f"{fmt(lo.get('conduction_W'))} W / {fmt(lo.get('switching_W'))} W"),
                (tr("최고 발열 위치", "hottest position"), f"{lo['hottest_position']}: {fmt(lo['hottest_position_W'])} W"),
                (tr("변조지수 m, cosφ", "modulation index m, cos φ"),
                 f"{fmt(lo['modulation_index'], 4)}, {fmt(lo['power_factor'], 4)}"),
                (tr("각도 세분화 차이", "angle refinement"), fmt(lo.get("angle_refinement_rel_diff"), 3))]
        sc = res["leg_detail"].get("energy_scaling")
        if sc:
            out.append((tr("에너지 전압 스케일링", "energy voltage scaling"), str(sc)))
        ss = res.get("standstill") or {}
        if ss.get("established"):
            out.append((tr("정지 핫스팟", "standstill hotspot"),
                        f"{fmt(ss['hottest_device_W'])} W at {fmt(ss['angle_at_max_deg'])}° vs total/6 "
                        f"{fmt(ss['total_over_six_W'])} W (not valid)"))
        et = res.get("electrothermal")
        if et:
            out.append((tr("전열 고정점", "electrothermal fixed point"),
                        f"Tj = {fmt(et.get('Tj_C'))} °C, P_hot = {fmt(et.get('P_hot_W'))} W, "
                        f"{'converged' if et.get('converged') else 'NOT converged'} ({et.get('iterations')} it.)"))
        for name, st in res["policy_with_module"].items():
            out.append((f"{name} ({tr('모듈', 'module')} / {tr('대리식', 'surrogate')})",
                        f"{st} / {res['policy_with_surrogate'].get(name)}"))
        if lo.get("problems"):
            out.append((tr("문제", "problems"), "; ".join(lo["problems"])))
        out.append((tr("모델 밖", "not modelled"), ", ".join(lo.get("not_modelled", []))))
        self.t_mod.set_rows(out)

    # ================================================================== ripple tab
    def _ripple_tab(self):
        ex = self.win.state.example("RIPPLE")
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("운전점", "operating point"))
        f = QFormLayout(g)
        self.r_n = number(6000, 0, 30000, "rpm", 0, 500)
        self.r_vdc = number(600, 1, 2000, "V", 1, 10)
        self.r_T = number(150, -5000, 5000, "N·m", 2, 10)
        self.r_fsw = number(ex["fsw_kHz"], 0.5, 200, "kHz", 2, 1)
        self.r_mod = combo([("SVPWM", "svpwm"), ("SPWM", "spwm"), ("DPWM1", "dpwm1")], ex["modulation"])
        for lab, w in ((tr("속도", "speed"), self.r_n), ("Vdc", self.r_vdc), (tr("토크", "torque"), self.r_T),
                       (tr("스위칭 주파수", "switching frequency"), self.r_fsw), (tr("변조", "modulation"), self.r_mod)):
            f.addRow(lab, w)
        v.addWidget(g)
        cap = ex["capacitor"]
        g = QGroupBox(tr("커패시터 뱅크", "capacitor bank"))
        f = QFormLayout(g)
        self.r_C = number(cap["C_uF"], 1, 1e5, "µF", 1, 10)
        self.r_esl = number(cap["ESL_nH"], 0, 1e4, "nH", 2, 1)
        self.r_rth_on = check(tr("핫스팟 Rth 선언", "declare hotspot Rth"), cap.get("Rth_K_per_W") is not None)
        self.r_rth = number(cap.get("Rth_K_per_W") or 0.35, 0.0001, 100, "K/W", 4, 0.05)
        self.r_tref = number(cap.get("T_ref_C") or 65, -40, 150, "°C", 1, 1,
                             tip=tr("Rth 가 가리키는 경계 온도 (냉각수/주변)", "boundary temperature Rth refers to (coolant / ambient)"))
        self.r_alpha = number(cap.get("ESR_temp_coeff_per_K") or 0.0, -0.1, 0.1, "1/K", 5, 0.001,
                              tip=tr("ESR(T) = ESR 표 × (1 + a (T − 표 온도)); 0 = 온도 무관",
                                     "ESR(T) = table ESR × (1 + a (T − table temperature)); 0 = temperature independent"))
        self.r_tesr = number(cap.get("ESR_table_T_C") if cap.get("ESR_table_T_C") is not None else 25.0, -40, 150, "°C", 1, 1)
        dom = cap.get("T_valid_C")
        self.r_dom_on = check(tr("온도 도메인 선언 (ESR(T)·데이터 유효 범위)", "declare temperature domain (ESR(T) / data validity)"),
                              bool(dom))
        self.r_tmin = number(dom[0] if dom else -40.0, -60, 200, "°C", 1, 1)
        self.r_tmax = number(dom[1] if dom else 105.0, -60, 250, "°C", 1, 1)
        for lab, w in (("C", self.r_C), ("ESL", self.r_esl), ("", self.r_rth_on), ("Rth(hotspot-ref)", self.r_rth),
                       (tr("경계 온도", "boundary temperature"), self.r_tref),
                       (tr("ESR 온도계수", "ESR temperature coefficient"), self.r_alpha),
                       (tr("ESR 표 온도", "ESR table temperature"), self.r_tesr), ("", self.r_dom_on),
                       ("T_min", self.r_tmin), ("T_max", self.r_tmax)):
            f.addRow(lab, w)
        self.r_esr = NumTable([tr("주파수 [Hz]", "frequency [Hz]"), "ESR [mΩ]"], cap["ESR_table"], min_height=150)
        f.addRow(QLabel(tr("ESR(f) 표 (범위 밖 외삽 안 함)", "ESR(f) table (no extrapolation)")))
        f.addRow(table_with_buttons(self.r_esr))
        self.r_life = NumTable([tr("핫스팟 [°C]", "hotspot [°C]"), tr("수명 [h]", "life [h]")],
                               cap.get("life_hours_table") or [], min_height=90)
        f.addRow(QLabel(tr("공급사 수명 표 (없으면 수명 UNKNOWN)", "supplier life table (life UNKNOWN without)")))
        f.addRow(table_with_buttons(self.r_life))
        self.r_life_v = number(0, 0, 3000, "V", 1, 10, tip=tr("0 = 미선언", "0 = not declared"),
                               special=tr("미선언", "not declared"))
        self.r_life_basis = QLineEdit(cap.get("life_basis", ""))
        f.addRow(tr("수명 정격 전압", "life rated voltage"), self.r_life_v)
        f.addRow(tr("수명 근거", "life basis"), self.r_life_basis)
        b = QPushButton(tr("커패시터 데이터시트 값 입력…", "enter capacitor datasheet values…"))
        b.clicked.connect(lambda: self.win.pages["project"].enter_datasheet("capacitor"))
        f.addRow(b)
        v.addWidget(g)
        src = ex["source"]
        g = QGroupBox(tr("소스 임피던스 (배터리+하네스)", "source impedance (battery + harness)"))
        f = QFormLayout(g)
        self.r_src_on = check(tr("선언 (없으면 전부 커패시터로 — 가정 표시)", "declared (else all to the capacitor, flagged)"), True)
        self.r_srcR = number(src["R_mohm"], 0, 1e5, "mΩ", 2, 1)
        self.r_srcL = number(src["L_uH"], 0, 1e5, "µH", 3, 0.1)
        self.r_src_basis = QLineEdit(src.get("basis", ""))
        for lab, w in (("", self.r_src_on), ("R_s", self.r_srcR), ("L_s", self.r_srcL), (tr("근거", "basis"), self.r_src_basis)):
            f.addRow(lab, w)
        v.addWidget(g)
        rq = ex["requirement"]
        g = QGroupBox(tr("리플 요구 (위치·물리량·한계·대역폭)", "ripple requirement (location, quantity, limit, bandwidth)"))
        f = QFormLayout(g)
        self.r_req_on = check(tr("요구 선언", "declare requirement"), True)
        self.r_loc = combo([(x, x) for x in LOCATIONS], rq["location"])
        self.r_qty = combo([(x, x) for x in QUANTITIES], rq["quantity"])
        self.r_lim = number(rq["limit"], 0, 1e6, "", 3, 1, tip=tr("V 또는 A (물리량에 따름)", "V or A (per quantity)"))
        self.r_bw = number(rq["bandwidth_Hz"] / 1e3, 0.1, 1e4, "kHz", 1, 10)
        for lab, w in (("", self.r_req_on), (tr("위치", "location"), self.r_loc), (tr("물리량", "quantity"), self.r_qty),
                       (tr("한계", "limit"), self.r_lim), (tr("측정 대역폭", "measurement bandwidth"), self.r_bw)):
            f.addRow(lab, w)
        v.addWidget(g)
        self.r_btn = primary_button(tr("리플 계산", "compute ripple"))
        self.r_btn.clicked.connect(self.run_ripple)
        v.addWidget(self.r_btn)
        v.addWidget(ConceptNote(NOTE_RIPPLE()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.p_rip = PlotPanel(hint=tr("스위칭 함수 파형 · 스펙트럼과 가지 분배", "switching-function waveform · spectrum and split"))
        self.t_rip = KeyValueTable()
        split.addWidget(_result_side(self.p_rip, self.t_rip))
        split.setStretchFactor(1, 1)
        split.setSizes([400, 1060])
        return split

    def ripple_body(self) -> dict:
        esr = self.r_esr.values()
        if len(esr) < 2 or any(len(r) != 2 for r in esr):
            raise ValueError(tr("ESR 표에는 (주파수, ESR) 두 행 이상이 필요합니다", "the ESR table needs at least two (f, ESR) rows"))
        cap = {**getattr(self, "_cap_base", {}), "C_uF": self.r_C.value(), "ESL_nH": self.r_esl.value(),
               "Rth_K_per_W": self.r_rth.value() if self.r_rth_on.isChecked() else None, "T_ref_C": self.r_tref.value(),
               "ESR_temp_coeff_per_K": self.r_alpha.value(), "ESR_table_T_C": self.r_tesr.value(),
               "T_valid_C": [self.r_tmin.value(), self.r_tmax.value()] if self.r_dom_on.isChecked() else None,
               "ESR_table": esr, "ESR_unit": "mohm", "life_hours_table": self.r_life.values(),
               "life_voltage_V": self.r_life_v.value() or None, "life_basis": self.r_life_basis.text().strip()}
        cfg = {"capacitor": cap, "fsw_kHz": self.r_fsw.value(), "modulation": self.r_mod.currentData(),
               "source": ({"R_mohm": self.r_srcR.value(), "L_uH": self.r_srcL.value(),
                           "basis": self.r_src_basis.text().strip()} if self.r_src_on.isChecked() else None),
               "requirement": ({"location": self.r_loc.currentData(), "quantity": self.r_qty.currentData(),
                                "limit": self.r_lim.value(), "bandwidth_Hz": self.r_bw.value() * 1e3}
                               if self.r_req_on.isChecked() else None)}
        return self.win.state.body(speed_rpm=self.r_n.value(), Vdc_V=self.r_vdc.value(), torque_Nm=self.r_T.value(),
                                   ripple=cfg)

    def run_ripple(self):
        try:
            body = self.ripple_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.r_btn.setEnabled(False)
        self.win.runner.run("ripple", tr("DC-link 리플", "DC-link ripple"), _ripple_task, self._show_ripple, body,
                            on_error=self._err)

    def _show_ripple(self, res):
        self.r_btn.setEnabled(True)
        self.last_ripple = res
        sp = res["spectrum"]
        self.p_rip.draw(RF.fig_ripple, res, name="dclink_ripple",
                        csv=lambda sp=sp: {k: sp[k] for k in ("f_Hz", "I_inv_rms_A", "I_cap_rms_A")})
        op = res["operating_point"]
        stt = res.get("state") or {}

        def val(k, unit, b):
            v, bd = res.get(k), res.get(b) or [None, None]
            if v is not None:
                return f"{fmt(v)} {unit}"
            if bd[0] is not None:
                return tr(f"ESR 표 밖 임피던스 미정: [{fmt(bd[0])}, {fmt(bd[1])}] {unit}",
                          f"impedance unknown outside the ESR table: [{fmt(bd[0])}, {fmt(bd[1])}] {unit}")
            return tr("— (운전 온도의 ESR 미확정)", "— (ESR at the operating temperature not established)")

        rows = [(tr("운전점", "operating point"), f"{fmt(op['speed_rpm'])} rpm, {fmt(op['torque_Nm'])} N·m, "
                                                 f"P_dc {fmt(op['Pdc_W'])} W, I_dc {fmt(op['Idc_avg_A'])} A"),
                (tr("커패시터 상태 온도", "capacitor state temperature"), f"{fmt(stt.get('T_C'))} °C — {stt.get('basis', '')}"),
                (tr("커패시터 RMS 전류", "capacitor RMS current"), val("I_cap_rms_A", "A", "I_cap_rms_bounds_A")),
                (tr("인버터 AC 성분", "inverter AC"), f"{fmt(res['I_inv_ac_rms_A'])} A"),
                (tr("소스 AC", "source AC"), val("I_source_ac_rms_A", "A", "I_source_ac_rms_bounds_A")),
                (tr("평균 모델 (캐리어 모멘트)", "average model (carrier moments)"), res.get("average_model")),
                (tr("전압 리플 p-p", "voltage ripple p-p"), val("V_ripple_pp_V", "V", "V_ripple_pp_bounds_V")),
                (tr("전압 리플 RMS", "voltage ripple RMS"), val("V_ripple_ac_rms_V", "V", "V_ripple_ac_rms_bounds_V")),
                (tr("ESR 손실", "ESR loss"), "—" if res["P_cap_W"] is None else f"{fmt(res['P_cap_W'])} W"),
                (tr("ESR 표 밖 인버터 전류² 비중", "inverter current² share outside ESR table"),
                 fmt(res["current_share_outside_ESR_band"], 3)),
                ("KCL / Parseval", f"{fmt(res.get('kcl_residual_rel'), 3)} / {fmt(res['parseval_residual_A'], 3)}")]
        rq = res.get("requirement")
        if rq:
            rows.append((tr("요구 대상 (가지/노드)", "requirement target (branch / node)"), rq["branch"]))
            rows.append((tr("요구 대상 값 (대역 제한)", "requirement value (band-limited)"),
                         f"{fmt(rq['value'])} · [{fmt(rq['bounds'][0])}, {fmt(rq['bounds'][1])}] · "
                         + tr(f"샘플 분해능 {fmt(rq['resolution_delta'], 3)}", f"sampling resolution {fmt(rq['resolution_delta'], 3)}")))
        for k, c in (res.get("claims") or {}).items():
            rows.append((k, _claim_text(c)))
        hs = res.get("hotspot")
        if hs:
            if hs.get("converged"):
                txt = (f"{fmt(hs['T_hot_C'])} °C, P {fmt(hs['P_W'])} W ({hs['termination']}; "
                       + tr(f"잔차 {fmt(hs['residual_K'], 3)} K", f"residual {fmt(hs['residual_K'], 3)} K") + ")")
            else:
                txt = tr(f"정착 상태 없음: {hs['termination']}", f"no settled state: {hs['termination']}")
            an = hs.get("analytic_fixed_current")
            if an:
                txt += tr(f" · 해석 검사 (고정 전류): 기울기 {fmt(an['slope'], 4)}, 평형 {'있음' if an['equilibrium_exists'] else '없음'}",
                          f" · analytic check (fixed current): slope {fmt(an['slope'], 4)}, equilibrium "
                          f"{'exists' if an['equilibrium_exists'] else 'none'}")
            rows.append((tr("핫스팟", "hotspot"), txt))
        if res.get("assumption"):
            rows.append((tr("가정", "assumption"), res["assumption"]))
        rows.append((tr("모델 밖", "not modelled"), ", ".join(res.get("not_modelled", []))))
        self.t_rip.set_rows(rows)

    # ================================================================== lifetime tab
    def _life_tab(self):
        ex = self.win.state.example("MISSION")
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("미션 (구간별 정책 운전점)", "mission (policy point per segment)"))
        f = QFormLayout(g)
        self.l_seg = NumTable([tr("시간 [s]", "duration [s]"), tr("속도 [rpm]", "speed [rpm]"), tr("토크 [N·m]", "torque [N·m]")],
                              [[s["duration_s"], s["speed_rpm"], s["torque_Nm"]] for s in ex["segments"]], min_height=170)
        f.addRow(table_with_buttons(self.l_seg))
        self.l_rep = integer(ex["repeat_in_trace"], 1, 100)
        self.l_dt = number(ex["dt_s"], 0.001, 10, "s", 3, 0.01)
        self.l_vdc = number(ex["Vdc_V"], 1, 2000, "V", 1, 10)
        self.l_cool = number(ex["coolant_C"], -40, 150, "°C", 1, 1)
        for lab, w in ((tr("이력 내 반복", "repeats in the trace"), self.l_rep), (tr("시간 간격", "time step"), self.l_dt),
                       ("Vdc", self.l_vdc), (tr("냉각수 온도", "coolant temperature"), self.l_cool)):
            f.addRow(lab, w)
        v.addWidget(g)
        net = ex["junction_network"]
        g = QGroupBox(tr("최고 소자 Foster 망 (j → 냉각수)", "hottest-device Foster network (j -> coolant)"))
        gl = QVBoxLayout(g)
        self.l_net = NumTable(["R [K/W]", "τ [s]"], list(zip(net["R_K_per_W"], net["tau_s"])), min_height=120)
        gl.addWidget(table_with_buttons(self.l_net))
        v.addWidget(g)
        g = QGroupBox(tr("측정 Tj 이력 (선택)", "measured Tj history (optional)"))
        gl = QVBoxLayout(g)
        self.l_trace_lab = QLabel(tr("가져온 이력 없음 — 미션에서 계산", "no imported trace - computed from the mission"))
        self.l_trace_lab.setWordWrap(True)
        row = QHBoxLayout()
        b = QPushButton(tr("CSV 가져오기 (t_s, T_C)", "import CSV (t_s, T_C)"))
        b.clicked.connect(self.import_trace)
        b2 = QPushButton(tr("지우기", "clear"))
        b2.clicked.connect(self.clear_trace)
        row.addWidget(b)
        row.addWidget(b2)
        row.addStretch(1)
        gl.addWidget(self.l_trace_lab)
        gl.addLayout(row)
        v.addWidget(g)
        g = QGroupBox(tr("공급사 사이클링 모델 (없으면 손상 UNKNOWN)", "supplier cycling model (damage UNKNOWN without)"))
        f = QFormLayout(g)
        self.l_cm_on = check(tr("모델 선언", "declare model"), False)
        self.l_mech = QLineEdit(tr("본드와이어 리프트오프 (파워 사이클링)", "bond-wire lift-off (power cycling)"))
        self.l_A = QLineEdit("3.0e14")
        self.l_a = number(-5.0, -20, 0, "", 3, 0.1)
        self.l_b = number(1500, 0, 1e5, "K", 1, 50)
        self.l_c = number(0.0, -2, 2, "", 3, 0.05)
        self.l_tref = combo([("T_min", "min"), ("T_mean", "mean"), ("T_max", "max")], "min")
        self.l_dT = QLineEdit("10, 120")
        self.l_Tv = QLineEdit("-40, 175")
        self.l_ton = QLineEdit("0.5, 60")
        self.l_q = QLineEdit("B10")
        self.l_scat = number(2.0, 1, 100, "", 2, 0.1)
        self.l_basis = QLineEdit()
        self.l_basis.setPlaceholderText(tr("공급사 문서·개정·패키지 (필수)", "supplier document, revision, package (required)"))
        self.l_ton_rule = combo([(tr("t_on 없음", "no t_on"), "none"), (tr("상승 시간 (승인된 규칙)", "rise time (approved rule)"),
                                                                        "rise_time")])
        self.l_D_on = check(tr("허용 손상 D_allow 선언", "declare D_allow"), False)
        self.l_D = number(1.0, 0, 100, "", 3, 0.1)
        self.l_cut = number(0.0, 0, 100, "K", 2, 0.5)
        self.l_mrep = QLineEdit("1")
        for lab, w in (("", self.l_cm_on), (tr("메커니즘", "mechanism"), self.l_mech), ("A", self.l_A), ("a (ΔT 지수)", self.l_a),
                       ("b [K]", self.l_b), ("c (t_on 지수)", self.l_c), (tr("기준 온도", "reference temperature"), self.l_tref),
                       (tr("ΔT 유효 [K]", "ΔT valid [K]"), self.l_dT), (tr("T 유효 [°C]", "T valid [°C]"), self.l_Tv),
                       (tr("t_on 유효 [s]", "t_on valid [s]"), self.l_ton), (tr("분위", "quantile"), self.l_q),
                       (tr("scatter 계수", "scatter factor"), self.l_scat), (tr("근거", "basis"), self.l_basis),
                       (tr("t_on 규칙", "t_on rule"), self.l_ton_rule), ("", self.l_D_on), ("D_allow", self.l_D),
                       (tr("작은 사이클 제외", "small-cycle cutoff"), self.l_cut),
                       (tr("미션 반복 수", "mission repetitions"), self.l_mrep)):
            f.addRow(lab, w)
        v.addWidget(g)
        self.l_btn = primary_button(tr("사이클·손상 계산", "count cycles and damage"))
        self.l_btn.clicked.connect(self.run_life)
        v.addWidget(self.l_btn)
        v.addWidget(hint(tr("예시 사이클링 계수는 형식 예시일 뿐 제품 값이 아닙니다(기본 비활성).",
                            "The example cycling coefficients only show the format; they are not product values (off by default).")))
        v.addWidget(ConceptNote(NOTE_LIFE()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.p_life = PlotPanel(hint=tr("Tj 이력과 rainflow 히스토그램", "Tj history and rainflow histogram"))
        self.t_life = KeyValueTable()
        split.addWidget(_result_side(self.p_life, self.t_life))
        split.setStretchFactor(1, 1)
        split.setSizes([400, 1060])
        return split

    def import_trace(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Tj 이력 CSV", "Tj history CSV"), "", "CSV (*.csv *.txt)")
        if path:
            try:
                self.load_trace_csv(path)
            except Exception as exc:  # noqa: BLE001
                error_box(self, tr("가져오기 실패", "import failed"), str(exc))

    def load_trace_csv(self, path):
        t, T = [], []
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.reader(fh):
                try:
                    a, b = float(row[0]), float(row[1])
                except (ValueError, IndexError):
                    continue                            # header or blank line
                t.append(a)
                T.append(b)
        if len(t) < 3 or any(b <= a for a, b in zip(t, t[1:])):
            raise ValueError(tr("t_s가 증가하는 3개 이상의 (t_s, T_C) 행이 필요합니다",
                                "need at least three (t_s, T_C) rows with increasing t_s"))
        self.trace = {"t_s": t, "T_C": T}
        self.l_trace_lab.setText(tr(f"가져온 이력: {len(t)}점, {t[-1] - t[0]:.4g} s", f"imported: {len(t)} points, "
                                    f"{t[-1] - t[0]:.4g} s") + f" ({path})")

    def clear_trace(self):
        self.trace = None
        self.l_trace_lab.setText(tr("가져온 이력 없음 — 미션에서 계산", "no imported trace - computed from the mission"))

    def life_body(self) -> dict:
        segs = self.l_seg.values()
        if not segs or any(len(r) != 3 or r[0] <= 0 for r in segs):
            raise ValueError(tr("미션 구간은 (시간>0, 속도, 토크) 행이 필요합니다", "segments need (duration>0, speed, torque) rows"))
        net = self.l_net.values()
        if not net or any(len(r) != 2 or r[0] <= 0 or r[1] <= 0 for r in net):
            raise ValueError(tr("Foster 망은 양수 (R, τ) 행이 필요합니다", "the Foster network needs positive (R, tau) rows"))
        body = self.win.state.body(
            segments=[{"duration_s": r[0], "speed_rpm": r[1], "torque_Nm": r[2]} for r in segs],
            repeat_in_trace=self.l_rep.value(), dt_s=self.l_dt.value(), Vdc_V=self.l_vdc.value(),
            coolant_C=self.l_cool.value(),
            junction_network={"R_K_per_W": [r[0] for r in net], "tau_s": [r[1] for r in net]},
            module=self.module_spec(), ton_rule=self.l_ton_rule.currentData(), cutoff_K=self.l_cut.value(),
            mission_repeats=float(self.l_mrep.text().strip() or 1.0),
            D_allow=self.l_D.value() if self.l_D_on.isChecked() else None, cycling_model=None)
        if self.l_cm_on.isChecked():
            rng = lambda w: [float(x) for x in _floats(w.text())[:2]]
            body["cycling_model"] = {"mechanism": self.l_mech.text().strip(), "A": float(self.l_A.text()),
                                     "a": self.l_a.value(), "b_K": self.l_b.value(), "c": self.l_c.value(),
                                     "T_ref": self.l_tref.currentData(), "dT_valid_K": rng(self.l_dT),
                                     "Tref_valid_C": rng(self.l_Tv), "ton_valid_s": rng(self.l_ton),
                                     "quantile": self.l_q.text().strip(), "scatter_factor": self.l_scat.value(),
                                     "basis": self.l_basis.text().strip()}
        if self.trace:
            body["trace"] = self.trace
        return body

    def run_life(self):
        try:
            body = self.life_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self.l_btn.setEnabled(False)
        self.win.runner.run("lifetime", tr("열 사이클", "thermal cycling"), _life_task, self._show_life, body,
                            on_error=self._err)

    def _show_life(self, res):
        self.l_btn.setEnabled(True)
        self.last_life = res
        tr_ = res["trace"]
        self.p_life.draw(RF.fig_lifetime, res, name="thermal_cycling",
                         csv=lambda tr_=tr_: {"t_s": tr_["t_s"], "Tj_C": tr_["T_C"]})
        d = res["damage"]
        rows = [(tr("손상 판정 (모든 다이)", "damage claim (every die)"), _claim_text(d["claim"])),
                (tr("출처", "source"), res["source"]),
                (tr("미션 종류", "mission kind"), f"{res.get('mission_kind', '')} — {res.get('count_basis', '')}")]
        if res.get("governing_device"):
            rows.append((tr("지배 다이 (그림)", "governing die (plot)"), res["governing_device"]))
        for name, dv in (res.get("devices") or {}).items():
            rows.append((tr(f"다이 {name}", f"die {name}"),
                         f"ΔTj max {fmt(dv['max_range_K'])} K, Tj {fmt(dv['T_min_C'])}–{fmt(dv['T_max_C'])} °C, "
                         f"{fmt(dv['cycles_counted'])} " + tr("사이클", "cycles") +
                         ("" if dv.get("D") is None else f", D {fmt(dv['D'])}") + f" ({dv['claim']})"))
        rows += [
                 (tr("반전점 / 계수 사이클", "reversals / counted cycles"), f"{res['reversals']} / {fmt(d['cycles_counted'])}"),
                 (tr("최대 ΔTj", "max ΔTj"), f"{fmt(res['max_range_K'])} K"),
                 ("Tj max / min", f"{fmt(res['T_max_C'])} / {fmt(res['T_min_C'])} °C")]
        for q in res.get("qualifiers") or []:
            rows.append((tr("한정", "qualifier"), q))
        if "D" in d:
            rows.append(("D [D/s, D·s]", f"{fmt(d['D'])} [{fmt(d['D_lower'])}, {fmt(d['D_upper'])}]"))
            if d.get("uncovered_cycles"):
                rows.append((tr("모델 밖 사이클", "uncovered cycles"),
                             "; ".join(f"ΔT {fmt(u['range'], 3)} K ×{fmt(u['count'], 3)}: {u['why']}"
                                       for u in d["uncovered_cycles"][:6])))
        for s in res.get("segments") or []:
            rows.append((f"{fmt(s['duration_s'])} s @ {fmt(s['speed_rpm'])} rpm, {fmt(s['torque_Nm'])} N·m",
                         f"{s['policy']}{'' if s.get('achieved', True) else ' (NOT achieved)'}, "
                         f"P_hot {fmt(s['P_hot_device_W'])} W ({s.get('hottest_die', '')})"))
        for n in res.get("notes") or []:
            rows.append((tr("주의", "note"), n))
        self.t_life.set_rows(rows)

    def redraw(self):
        for p in (self.p_mod, self.p_rip, self.p_life):
            p.redraw()
