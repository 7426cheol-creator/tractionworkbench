"""Integrated charging page (system view, item 9): the drive inverter and the motor windings as a boost converter
from a lower-voltage DC charger - one switching period exactly, the losses and junction temperature, the declared
limits, and the charging-power capability over charger and battery voltages with the limit that binds."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...insight.drive_system import charging_insight, charging_map_insight
from ...plots import drive_system_figures as F
from ..widgets import (ConceptNote, KeyValueTable, PlotPanel, check, combo, error_box, fmt, hint, number,
                       primary_button, reading_tab)

NOTE = lambda: tr(
    "<b>통합 충전</b>: 충전기 +를 모터 중성점에, −를 DC-link 음극에 연결하고 인버터 세 다리를 승압기로 씁니다(회전자 정지, "
    "주차 잠금). 각 상 권선이 승압 인덕터입니다.<br>• 한 스위칭 주기의 전류를 스위칭 순간 사이에서 정확히 적분합니다. 세 상에 "
    "공통인 영상분은 <b>영상 인덕턴스 L0</b>(대부분 누설, 선언 값 — L_d·L_q에서 유도되지 않음)로만, 상 사이 차이는 L_d·L_q로 "
    "제한됩니다. 120° 인터리브는 영상분을 상쇄합니다(듀티 1/3·2/3에서 완전).<br>• 소자 손실은 모듈 데이터시트 곡선을 "
    "<b>순간 전류</b>로 읽습니다(턴온은 골, 턴오프는 마루). 시험 전압과 다른 전압의 스위칭 에너지는 선언한 스케일 법칙이 있을 "
    "때만 씁니다. 최고 다이에 모듈 Rth를 적용한 전열 고정점으로 Tj를 구합니다.<br>• 한계(충전기 전류·전력, 배터리 급속충전 "
    "수용, 상 피크, 중성선 RMS, Tj, 고정자 동손)는 <b>선언된 것만</b> 판정하고 나머지는 '확인 안 함'으로 표시합니다.",
    "<b>Integrated charging</b>: the charger's + goes to the motor's star point, its − to the DC link's negative rail, "
    "and the three inverter legs work as a boost (rotor at standstill, parking lock). Each phase winding is a boost "
    "inductor.<br>• The current of one switching period is integrated exactly between the switching instants. The "
    "zero sequence common to the three phases is limited only by the <b>zero-sequence inductance L0</b> (mostly "
    "leakage, a declared value — not derivable from L_d, L_q), the difference between phases by L_d, L_q. 120° "
    "interleaving cancels the zero sequence (completely at duty 1/3 and 2/3).<br>• Device losses read the module's "
    "datasheet curves at the <b>instantaneous current</b> (turn-on at the valley, turn-off at the peak); switching "
    "energies away from the test voltage only with a declared scaling law. Tj from the electrothermal fixed point with "
    "the module Rth on the hottest die.<br>• Limits (charger current and power, the battery's fast-charge "
    "acceptance, phase peak, neutral RMS, Tj, stator copper loss) are judged <b>only where declared</b>; the others "
    "are shown as not checked.")


def _task(fn, label):
    def run(progress, body):
        progress(0.02, label, 1.0)
        return fn(body)
    return run


def _scroll(w):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    sc.setMinimumWidth(400)
    return sc


class ChargingPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last_point = self.last_map = None
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        ex = self.win.state.example("CHARGING")
        g = QGroupBox(tr("충전 경로 (프로젝트의 charging 섹션)", "charging path (the project's charging section)"))
        f = QFormLayout(g)
        self.p_neu = check(tr("중성점 인출 (설계 선언)", "neutral brought out (design statement)"), True)
        self.p_L0 = number(30.0, 0.1, 10000, "µH", 2, 1)
        self.p_L0b = QLineEdit()
        self.p_inl = combo([(tr("120° 인터리브", "120° interleaved"), "120deg"), (tr("동위상", "in phase"), "none")],
                           "120deg")
        self.p_neu_max = number(400.0, 0, 1e4, "A rms", 1, 10)
        self.p_pk_max = number(450.0, 0, 1e4, "A", 1, 10)
        self.p_tj = number(150.0, 0, 250, "°C", 1, 5)
        self.p_cu = number(1500.0, 0, 1e5, "W", 0, 50)
        for lab, w in (("", self.p_neu), (tr("영상 인덕턴스 L0", "zero-sequence inductance L0"), self.p_L0),
                       (tr("L0 근거", "L0 basis"), self.p_L0b), (tr("반송파", "carriers"), self.p_inl),
                       (tr("중성선 RMS 정격 (0 = 미선언)", "neutral RMS rating (0 = not declared)"), self.p_neu_max),
                       (tr("상 피크 전류 한계 (0 = 미선언)", "phase peak limit (0 = not declared)"), self.p_pk_max),
                       (tr("Tj 한계 (0 = 미선언)", "Tj limit (0 = not declared)"), self.p_tj),
                       (tr("고정자 동손 한계 (0 = 미선언)", "stator copper-loss limit (0 = not declared)"), self.p_cu)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("운전점", "operating point"))
        f = QFormLayout(g)
        self.k_mod = combo([(tr("프로젝트 모듈", "project module"), "MODULE"),
                            (tr("프로젝트 대안 (SiC)", "project alternative (SiC)"), "MODULE_SIC")], "MODULE")
        self.k_vc = number(ex["V_charger_V"], 1, 2000, "V", 1, 10)
        self.k_vb = number(ex["V_battery_V"], 1, 2000, "V", 1, 10)
        self.k_i = number(ex["I_charge_A"], -2000, 2000, "A", 1, 10)
        self.k_cool = number(ex["coolant_C"], -40, 120, "°C", 1, 5)
        self.k_ang = number(ex["rotor_angle_deg"], 0, 360, "° mech", 1, 5)
        for lab, w in ((tr("인버터 모듈", "inverter module"), self.k_mod), (tr("충전기 전압", "charger voltage"), self.k_vc),
                       (tr("배터리 전압", "battery voltage"), self.k_vb),
                       (tr("충전 전류 (음수 = V2X 방전)", "charging current (negative = V2X discharge)"), self.k_i),
                       (tr("냉각수", "coolant"), self.k_cool), (tr("회전자 각도", "rotor angle"), self.k_ang)):
            f.addRow(lab, w)
        f.addRow(hint(tr("스위칭 에너지: 합성 모듈의 시험 전압 600 V 밖은 예제에서 선언한 합성 E ~ V 법칙으로만 씁니다.",
                         "switching energies: away from the synthetic module's 600 V test voltage only with the "
                         "example's declared synthetic E ~ V law.")))
        v.addWidget(g)
        g = QGroupBox(tr("충전기·배터리 한계", "charger and battery limits"))
        f = QFormLayout(g)
        ch, bt = ex.get("charger") or {}, ex.get("battery") or {}
        self.c_i = number(ch.get("current_max_A") or 0, 0, 5000, "A", 1, 10)
        self.c_p = number((ch.get("power_max_W") or 0) / 1e3, 0, 2000, "kW", 1, 10)
        self.b_p = number((bt.get("battery_charge_power_max_W") or 0) / 1e3, 0, 2000, "kW", 1, 10)
        self.b_i = number(bt.get("battery_charge_current_max_A") or 0, 0, 5000, "A", 1, 10)
        for lab, w in ((tr("충전기 최대 전류 (0 = 미선언)", "charger current max (0 = not declared)"), self.c_i),
                       (tr("충전기 최대 전력", "charger power max"), self.c_p),
                       (tr("배터리 급속충전 수용 전력", "battery fast-charge power acceptance"), self.b_p),
                       (tr("배터리 급속충전 수용 전류", "battery fast-charge current acceptance"), self.b_i)):
            f.addRow(lab, w)
        self.m_vc = QLineEdit(" ".join(f"{x:g}" for x in ex["V_chargers_V"]))
        self.m_vb = QLineEdit(" ".join(f"{x:g}" for x in ex["V_batteries_V"]))
        f.addRow(tr("지도: 충전기 전압 [V]", "map: charger voltages [V]"), self.m_vc)
        f.addRow(tr("지도: 배터리 전압 [V]", "map: battery voltages [V]"), self.m_vb)
        v.addWidget(g)
        row = QHBoxLayout()
        self.btn = primary_button(tr("운전점", "operating point"))
        self.btn.clicked.connect(self.run_point)
        self.map_btn = QPushButton(tr("충전 능력 지도", "capability map"))
        self.map_btn.clicked.connect(self.run_map)
        for b in (self.btn, self.map_btn):
            b.setMinimumHeight(34)
            row.addWidget(b)
        v.addLayout(row)
        v.addWidget(ConceptNote(NOTE()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(("charging_point", "charging_capability"), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.p_wave = PlotPanel(hint=tr("'운전점'을 누르세요", "press 'operating point'"))
        self.p_loss = PlotPanel(hint=tr("'운전점'을 누르세요", "press 'operating point'"))
        self.p_map = PlotPanel(hint=tr("'충전 능력 지도'를 누르세요", "press 'capability map'"))
        for p, lab in ((self.p_wave, tr("파형", "waveforms")), (self.p_loss, tr("손실·한계", "losses and limits")),
                       (self.p_map, tr("충전 능력", "capability"))):
            self.tabs.addTab(p, lab)
        self.reading = reading_tab(self.tabs, tr(
            "계산하면 해석이 표시됩니다 — 한계 판정, 리플과 인터리브, 손실과 열, 어느 한계가 충전 전력을 막는지.",
            "Run to read the result — limits, ripple and interleaving, losses and heat, which limit caps the charging "
            "power."))
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

    def apply_project(self, _project=None):
        """The charging path from the active project (operating point and limits stay)."""
        c = self.win.state.example("CHARGING")["charging"]
        self.p_neu.setChecked(bool(c.get("neutral_access")))
        self.p_L0.setValue(float(c.get("L0_uH") or 30.0))
        self.p_L0b.setText(str(c.get("L0_basis", "")))
        self.p_inl.setCurrentIndex(self.p_inl.findData(c.get("interleave", "120deg")))
        self.p_neu_max.setValue(float(c.get("neutral_current_max_A") or 0.0))
        self.p_pk_max.setValue(float(c.get("phase_current_peak_max_A") or 0.0))
        self.p_tj.setValue(float(c.get("Tj_max_C") or 0.0))
        self.p_cu.setValue(float(c.get("winding_loss_max_W") or 0.0))

    def body(self) -> dict:
        b = self.win.state.example("CHARGING")
        opt = lambda w: w.value() if w.value() > 0 else None      # noqa: E731
        b["charging"] = {**b["charging"], "neutral_access": self.p_neu.isChecked(), "L0_uH": self.p_L0.value(),
                         "L0_basis": self.p_L0b.text().strip(), "interleave": self.p_inl.currentData(),
                         "neutral_current_max_A": opt(self.p_neu_max), "phase_current_peak_max_A": opt(self.p_pk_max),
                         "Tj_max_C": opt(self.p_tj), "winding_loss_max_W": opt(self.p_cu)}
        m = self.win.state.example(self.k_mod.currentData())
        b["module"] = {**m, "vdc_scaling": b["module"]["vdc_scaling"]}
        b.update({"V_charger_V": self.k_vc.value(), "V_battery_V": self.k_vb.value(), "I_charge_A": self.k_i.value(),
                  "coolant_C": self.k_cool.value(), "rotor_angle_deg": self.k_ang.value(),
                  "charger": {"current_max_A": opt(self.c_i), "power_max_W": opt(self.c_p) and self.c_p.value() * 1e3},
                  "battery": {"battery_charge_power_max_W": opt(self.b_p) and self.b_p.value() * 1e3,
                              "battery_charge_current_max_A": opt(self.b_i)}})
        vc = [float(x) for x in self.m_vc.text().split()]
        vb = [float(x) for x in self.m_vb.text().split()]
        if not vc or not vb:
            raise ValueError(tr("지도 전압 목록이 비었습니다", "the map voltage lists are empty"))
        b.update({"V_chargers_V": vc, "V_batteries_V": vb})
        b.update(self.win.state.body())
        return b

    def _run(self, key, label, fn, show, btn):
        try:
            body = self.body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        btn.setEnabled(False)
        self.win.runner.run(key, label, _task(fn, label), show, body, on_error=self._err)

    def _err(self, msg, tb):
        for b in (self.btn, self.map_btn):
            b.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def run_point(self):
        self._run("charging_point", tr("통합 충전 운전점", "integrated charging point"), api.charging_point,
                  self.show_point, self.btn)

    def run_map(self):
        self._run("charging_capability", tr("충전 능력 지도", "charging capability"), api.charging_capability,
                  self.show_map, self.map_btn)

    def show_point(self, res):
        self.btn.setEnabled(True)
        self.last_point = res
        w = res.get("waveform") or {}
        csv = (lambda r=w: {k: r[k] for k in ("t_us", "ia_A", "ib_A", "ic_A", "neutral_A", "upper_a", "upper_b",
                                              "upper_c")}) if w else None
        self.p_wave.draw(F.fig_charging_waveforms, res, name="charging_waveforms", csv=csv)
        self.p_loss.draw(F.fig_charging_losses, res, name="charging_losses")
        if self.tabs.currentWidget() is not self.reading:
            self.tabs.setCurrentWidget(self.p_wave)
        self.reading.read("charging_point", tr("통합 충전", "integrated charging"), charging_insight, res)
        if "checks" not in res:
            self.kv.set_rows([(tr("판정", "verdict"), res.get("status")), (tr("이유", "reason"), res.get("reason", ""))])
            return
        c, L = res["currents"], res["losses_W"]
        rows = [(tr("판정", "verdict"), res["status"] + ("" if not res.get("not_checked") else
                                                          tr(" (확인 안 함: ", " (not checked: ")
                                                          + ", ".join(res["not_checked"]) + ")")),
                (tr("충전기 → 배터리", "charger → battery"),
                 f"{res['P_charger_W'] / 1e3:.2f} kW → {res['P_battery_W'] / 1e3:.2f} kW, "
                 f"η {fmt(res['efficiency'] and 100 * res['efficiency'], 5)} %"),
                (tr("듀티 (상측 / 하측)", "duty (upper / lower)"), f"{res['duty_upper']:.4f} / {res['duty_lower']:.4f}"),
                (tr("상전류 DC / 피크 / RMS", "phase DC / peak / RMS"),
                 f"{c['phase_dc_A']:.1f} / {c['phase_peak_A']:.1f} / "
                 + " ".join(f"{x:.1f}" for x in c["phase_rms_A"]) + " A"),
                (tr("리플 pk-pk: 상 / 중성선 / 영상분", "ripple pk-pk: phase / neutral / zero seq."),
                 " ".join(f"{x:.1f}" for x in c["phase_ripple_pp_A"])
                 + f" / {c['neutral_ripple_pp_A']:.2f} / {c['zero_sequence_ripple_pp_A']:.2f} A"),
                (tr("손실", "losses"), f"devices {fmt(L['devices'], 5)} W · winding {fmt(L['motor_copper'], 5)} W · "
                                     f"DC link {fmt(L['dc_link_capacitor'], 4)} W"),
                (tr("최고 Tj", "hottest Tj"), f"{fmt(res['Tj_C'], 5)} °C ({res.get('hottest_die')})"),
                (tr("DC-link 커패시터 RMS", "DC-link capacitor RMS"), f"{fmt(res['dc_link'].get('cap_rms_A'), 4)} A"),
                (tr("토크 (평균 / |피크|)", "torque (mean / |peak|)"),
                 f"{fmt(res['torque']['mean_Nm'], 3)} / {fmt(res['torque']['peak_abs_Nm'], 4)} N·m"),
                (tr("스위칭 에너지", "switching energies"), res.get("energy_scaling", ""))]
        self.kv.set_rows(rows)

    def show_map(self, res):
        self.map_btn.setEnabled(True)
        self.last_map = res
        self.p_map.draw(F.fig_charging_capability, res, name="charging_capability",
                        csv=lambda r=res: {k: [row.get(k) for row in r["rows"]]
                                           for k in ("V_charger_V", "V_battery_V", "status", "I_max_A", "P_max_W")})
        if self.tabs.currentWidget() is not self.reading:
            self.tabs.setCurrentWidget(self.p_map)
        self.reading.read("charging_capability", tr("충전 능력", "charging capability"), charging_map_insight, res)

    def redraw(self):
        for p in (self.p_wave, self.p_loss, self.p_map, self.reading):
            p.redraw()
