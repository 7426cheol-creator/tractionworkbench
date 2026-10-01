"""The faults of a fault-simulation scenario as a list and a form - no typed ``key=value`` text.

The list shows every fault in one line (kind, time, its parameters in words).  Selecting a line opens its form below:
the kind from a list (with its description), the time, and one field per parameter the engine declares for that kind
(``extensions.faultsim.engine.FAULT_KINDS``): a number with its unit, a choice among the allowed values, or a name from
the project's protection design (a sensor, a safety mechanism, a reaction path, a shared resource, a reaction).  An
intermittent fault gets its duration; a label is optional.  The scenario data stays what the engine reads
(``{"kind", "t_ms", "params", "label"}``) - presets, saved scenarios, campaigns and counterexamples are unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..extensions.faultsim.engine import FAULT_KINDS, OUTSIDE_DRIVE_SCOPE
from ..i18n import tr

# faults that are permanent by nature (the engine never clears them) and the one with its own duration
PERMANENT = ("switch_short", "switch_open", "diode_open", "phase_open")
OWN_DURATION = ("mcu_reset",)

FAULT_NAME = {
    "sensor": lambda: tr("센서 고장", "sensor fault"), "torque_command": lambda: tr("토크 명령 고장", "torque command fault"),
    "control_task_stop": lambda: tr("제어 태스크 정지", "control task stop"), "mcu_reset": lambda: tr("MCU 리셋", "MCU reset"),
    "pwm_output": lambda: tr("PWM 출력 고장", "PWM output fault"), "switch_open": lambda: tr("스위치 개방", "switch open"),
    "switch_short": lambda: tr("스위치 단락", "switch short"), "diode_open": lambda: tr("다이오드 개방", "diode open"),
    "phase_open": lambda: tr("상 개방", "phase open"), "gate_supply_loss": lambda: tr("게이트 전원 상실", "gate supply loss"),
    "battery_disconnect": lambda: tr("배터리 차단 (접촉기 열림)", "battery disconnect (contactor opens)"),
    "contactor_stuck": lambda: tr("접촉기 융착", "contactor welded"),
    "charge_acceptance_loss": lambda: tr("배터리 충전 수용 상실", "loss of charge acceptance"),
    "mechanism_disabled": lambda: tr("감시 메커니즘 잠재 고장", "latent safety-mechanism fault"),
    "path_lost": lambda: tr("반응 경로 잠재 상실", "latent reaction-path loss"),
    "resource_loss": lambda: tr("공유 자원 상실", "shared resource loss"),
    "command_reaction": lambda: tr("반응 명령 (시나리오 요소)", "commanded reaction (scenario element)"),
    "gde_disable": lambda: tr("게이트 드라이버 enable(GDE) 해제", "gate-driver enable (GDE) withdrawn"),
    "lv_loss": lambda: tr("저전압 전원(KL30) 상실", "low-voltage supply (KL30) loss"),
    "hv_supply_fault": lambda: tr("HV 유도 보조 전원 고장", "HV-derived auxiliary supply fault"),
    "e2e": lambda: tr("수신 토크 메시지 E2E 고장", "E2E fault on the received torque message"),
    "envelope": lambda: tr("토크 엔벨로프 손상", "torque envelope corrupted"),
    "latch_corruption": lambda: tr("기본 오류 래치 손상", "default-error latch corrupted"),
    "safety_task_stop": lambda: tr("안전 감시 태스크 정지", "safety-monitor task stop"),
    "application_limit_fail": lambda: tr("응용 토크 제한 실패", "application torque limitation fails"),
    "clock": lambda: tr("MCU 클럭 고장", "MCU clock fault"),
    "coupling": lambda: tr("기계 결합 변화 (클러치)", "mechanical coupling change (clutch)"),
}
FAULT_DESC_KO = {
    "sensor": "센서 읽음값의 고장: 오프셋 / 이득 / 고착 / 마지막 값 고착 / 신호 상실 / 추가 지연",
    "torque_command": "토크 명령 메시지의 고장(갱신 멈춤 / 값 / 오프셋 / 부호 반전 / 상실 / 진동: 진폭 value N·m, freq_Hz). "
                      "제어 경로, 모니터 사본, 또는 둘 다(송신기 같은 공통 원인)",
    "control_task_stop": "제어 태스크가 멈춤 (PWM 유닛은 마지막 비교값 유지)",
    "mcu_reset": "MCU 리셋 (PWM 출력은 리셋 상태, 부팅까지 소프트웨어 정지)",
    "pwm_output": "한 다리의 PWM 출력 고장 (듀티 고착 / 꺼짐 / 상측 켜짐 / 하측 켜짐)",
    "switch_open": "전력 스위치 개방 고장", "switch_short": "전력 스위치 단락 고장",
    "diode_open": "환류 다이오드 개방 고장", "phase_open": "모터 상 연결 개방",
    "gate_supply_loss": "한쪽 게이트 드라이버 전원 상실 (자원 GATE_UPPER / GATE_LOWER)",
    "battery_disconnect": "배터리 릴레이(주 접촉기)가 열림 — 구동 시스템 경계의 사건, 다른 고장과 함께 넣어 결합을 모사", "contactor_stuck": "명령해도 주 접촉기가 열리지 않음 (융착)",
    "charge_acceptance_loss": "배터리가 충전을 받지 않음: BMS 충전 전류 한계가 떨어짐",
    "mechanism_disabled": "안전 메커니즘이 조용히 동작하지 않음 (잠재 고장)",
    "path_lost": "반응 경로가 구동하지 못함 (잠재 고장)",
    "resource_loss": "공유 자원 상실 (그 자원을 쓰는 센서·메커니즘·경로 전부)",
    "command_reaction": "이 순간 경로를 통해 시험할 반응을 명령 (시나리오 요소: 검출 없음, '보호 끔'에도 막히지 않음)",
    "gde_disable": "프로세서의 게이트 드라이버 enable(GDE) 해제: PWM 권한 없음, 하드웨어 GDE 감시가 안전 상태 선택",
    "lv_loss": "저전압 전원(단자 30) 상실 (시스템 전원 모델 필요)",
    "hv_supply_fault": "HV 유도 보조 전원 고장 (시스템 전원 모델 필요)",
    "e2e": "수신 토크 메시지의 E2E 고장 (시스템 수신 경로 필요)",
    "envelope": "수신 토크 엔벨로프 손상: 모순 (최대 ≤ 최소) 또는 잘못된 최대값",
    "latch_corruption": "RAM의 기본 오류 래치 워드 손상 (시스템 감독기 필요)",
    "safety_task_stop": "안전 감시 태스크 정지 (제어 태스크는 계속 동작)",
    "application_limit_fail": "응용의 토크 제한 실패 (인터페이스 엔벨로프가 명령을 더 이상 제한하지 않음)",
    "clock": "MCU 클럭 고장: 주기 소프트웨어(제어 태스크, 소프트웨어 메커니즘, 감독기)가 공칭의 value 배로 돌거나(드리프트) "
             "멈춤. 하드웨어·플랜트·외부 워치독은 실제 시간",
    "coupling": "기계 결합 변화 (클러치 열림/닫힘): 축 관성이 J_kgm2가 되고(0: 부하가 속도 유지) 부하 토크 T_load_Nm",
}
CHOICE_NAME = {
    "offset": lambda: tr("오프셋", "offset"), "gain": lambda: tr("이득", "gain"), "stuck": lambda: tr("값 고착", "stuck at a value"),
    "stuck_last": lambda: tr("마지막 값 고착", "stuck at the last value"), "lost": lambda: tr("신호 상실", "signal lost"),
    "delay": lambda: tr("추가 지연", "extra delay"), "stale": lambda: tr("갱신 멈춤", "stale"),
    "value": lambda: tr("값", "value"), "sign_flip": lambda: tr("부호 반전", "sign flip"),
    "loss": lambda: tr("상실", "loss"), "oscillation": lambda: tr("진동", "oscillation"),
    "control": lambda: tr("제어 경로", "control path"), "monitor": lambda: tr("모니터 경로", "monitor path"),
    "both": lambda: tr("둘 다 (공통 원인)", "both (common cause)"), "upper": lambda: tr("상측", "upper"),
    "lower": lambda: tr("하측", "lower"), "stuck_duty": lambda: tr("듀티 고착", "stuck duty"), "off": lambda: tr("꺼짐", "off"),
    "upper_on": lambda: tr("상측 켜짐", "upper on"), "lower_on": lambda: tr("하측 켜짐", "lower on"),
    "crc": lambda: tr("CRC 오류", "CRC error"), "counter_repeat": lambda: tr("카운터 반복", "counter repeat"),
    "counter_jump": lambda: tr("카운터 점프", "counter jump"), "data_id": lambda: tr("데이터 ID 오류", "data ID error"),
    "em_swap": lambda: tr("EM 교차", "EM swap"), "contradiction": lambda: tr("모순 (최대 ≤ 최소)", "contradiction (max ≤ min)"),
    "drift": lambda: tr("드리프트", "drift"), "stop": lambda: tr("정지", "stop"),
    "a": lambda: tr("a상", "phase a"), "b": lambda: tr("b상", "phase b"), "c": lambda: tr("c상", "phase c"),
}
PARAM_NAME = {
    "target": lambda: tr("센서", "sensor"), "mode": lambda: tr("방식", "mode"), "value": lambda: tr("값", "value"),
    "paths": lambda: tr("경로", "paths"), "freq_Hz": lambda: tr("진동 주파수", "oscillation frequency"),
    "duration_s": lambda: tr("리셋 지속", "reset duration"), "leg": lambda: tr("다리", "leg"),
    "device": lambda: tr("소자", "device"), "phase": lambda: tr("상", "phase"), "side": lambda: tr("쪽", "side"),
    "limit_A": lambda: tr("새 충전 전류 한계", "new charge-current limit"),
    "mechanism": lambda: tr("안전 메커니즘", "safety mechanism"), "path": lambda: tr("반응 경로", "reaction path"),
    "resource": lambda: tr("자원", "resource"), "reaction": lambda: tr("반응", "reaction"),
    "J_kgm2": lambda: tr("축 관성 (0 = 부하가 속도 유지)", "shaft inertia (0 = the load holds the speed)"),
    "T_load_Nm": lambda: tr("부하 토크", "load torque"),
}
# float parameters: (unit, lo, hi, decimals, step); the scale converts the shown value to the engine's (duration_s in ms)
FLOAT_UI = {"freq_Hz": ("Hz", 0.0, 1e5, 2, 1.0, 1.0), "duration_s": ("ms", 0.0, 1e7, 3, 1.0, 1e-3),
            "limit_A": ("A", 0.0, 1e4, 1, 10.0, 1.0), "J_kgm2": ("kg·m²", 0.0, 1e3, 4, 0.01, 1.0),
            "T_load_Nm": ("N·m", -1e4, 1e4, 1, 10.0, 1.0)}
SENSOR_UNIT = {"current": "A", "voltage": "V", "position": "rad e", "temperature": "°C", "dc_current": "A"}
REACTION_CHOICES = ("asc_low", "asc_high", "six_switch_off", "torque_zero", "off", "safe_state")


def fault_name(kind: str) -> str:
    return FAULT_NAME.get(kind, lambda: kind.replace("_", " "))()


def fault_description(kind: str) -> str:
    return tr(FAULT_DESC_KO.get(kind, FAULT_KINDS.get(kind, ("",))[0]), FAULT_KINDS.get(kind, ("",))[0])


def choice_name(v) -> str:
    return CHOICE_NAME[v]() if isinstance(v, str) and v in CHOICE_NAME else str(v)


def param_name(k: str) -> str:
    return PARAM_NAME.get(k, lambda: k.replace("_", " "))()


def _num(s: str):
    s = s.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        v = float(s)
        return int(v) if v.is_integer() and "." not in s and "e" not in s.lower() else v
    except ValueError:
        return s


def parse_params(text: str) -> dict:
    """``key=value, key=value`` (an older workspace's parameter text; numbers where they parse)."""
    out = {}
    for part in (p for p in str(text).split(",") if p.strip()):
        if "=" not in part:
            raise ValueError(tr(f"매개변수는 key=value 형식: '{part.strip()}'", f"parameters are key=value: '{part.strip()}'"))
        k, v = part.split("=", 1)
        out[k.strip()] = _num(v)
    return out


def default_params(kind: str) -> dict:
    out = {}
    for name, typ, default in FAULT_KINDS[kind][1]:
        if default is not None:
            out[name] = default
        elif isinstance(typ, tuple):
            out[name] = typ[0]
    return out


class FaultEditor(QWidget):
    """The scenario's faults: a list (one line each) and the form of the selected one."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._choices: dict = {"sensor": [], "mechanism": [], "path": [], "resource": [],
                               "reaction": [(choice_name(r) if r in CHOICE_NAME else r, r) for r in REACTION_CHOICES]}
        self._sensor_kind: dict = {}
        self._roles: dict = {}
        self._building = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("고장", "fault"), tr("시각 [ms]", "time [ms]"),
                                              tr("내용", "details")])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        self.table.verticalHeader().setVisible(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setWordWrap(True)
        self.table.setMinimumHeight(84)
        self.table.setMaximumHeight(170)            # a few lines: the form of the selected fault stays in view
        self.table.itemSelectionChanged.connect(self._selected)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.add_btn = QPushButton(tr("+ 고장 추가", "+ add fault"))
        self.dup_btn = QPushButton(tr("복제", "duplicate"))
        self.del_btn = QPushButton(tr("− 선택 삭제", "− remove selected"))
        self.add_btn.clicked.connect(lambda: self.add())
        self.dup_btn.clicked.connect(self.duplicate)
        self.del_btn.clicked.connect(self.remove_selected)
        for b in (self.add_btn, self.dup_btn, self.del_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.scope_note = QLabel(tr(
            "범위: 구동 시스템(인버터·모터). 다른 제어기(BMS·VCU)와 차량 통신(CAN 수신·E2E)의 고장, 배터리 시스템 자체의 보호는 "
            "가정하지 않습니다. 배터리 릴레이 개방·저전압 전원 상실·커플링 변화는 구동 시스템 경계의 사건으로 넣고 다른 고장과 "
            "함께 쓸 수 있습니다.",
            "Scope: the drive system (inverter and motor). Faults of other controllers (BMS, VCU) and of the vehicle "
            "network (CAN reception, end-to-end) and the battery system's own protection are not assumed. The battery "
            "relay opening, the low-voltage supply lost and a coupling change are events at the drive's interfaces, "
            "alone or with other faults."))
        self.scope_note.setWordWrap(True)
        self.scope_note.setStyleSheet("color: palette(mid);")
        lay.addWidget(self.scope_note)
        self.box = QGroupBox(tr("선택한 고장", "selected fault"))
        self.form = QFormLayout(self.box)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        lay.addWidget(self.box)
        self._empty_form()

    # -- choices from the protection design ---------------------------------------------------------------
    def set_choices(self, design: dict | None) -> None:
        """Sensor, mechanism, path, resource and reaction names from the (edited) protection design."""
        d = design or {}
        self._sensor_kind = {s["name"]: s.get("kind", "") for s in d.get("sensors") or [] if s.get("name")}
        roles = self._roles = dict(d.get("roles") or {})
        sens = [(f"{n} ({k})" if k else n, n) for n, k in self._sensor_kind.items()]
        sens += [(tr(f"역할 {r} → {n}", f"role {r} → {n}"), r) for r, n in roles.items()]
        self._choices["sensor"] = sens
        self._choices["mechanism"] = [(f"{m['id']} ({m.get('kind', '')})", m["id"]) for m in d.get("mechanisms") or []
                                      if m.get("id")]
        self._choices["path"] = [(p["id"], p["id"]) for p in d.get("paths") or [] if p.get("id")]
        res = d.get("resources") or {}
        self._choices["resource"] = [(f"{k} — {v}" if v else k, k) for k, v in res.items()]
        strat = [(f"{s['id']} ({tr('전략', 'strategy')})", s["id"]) for s in d.get("strategies") or [] if s.get("id")]
        self._choices["reaction"] = [(choice_name(r) if r in CHOICE_NAME else r, r) for r in REACTION_CHOICES] + strat
        for i in range(len(self._rows)):
            self._refresh_row(i)
        self._selected()

    # -- rows -------------------------------------------------------------------------------------------------
    def add(self, f: dict | None = None) -> None:
        if f is None:
            f = {"kind": "sensor", "t_ms": 10.0, "params": {"target": self._first("sensor", "CS_A"), "mode": "offset",
                                                           "value": 100.0}}
        row = {"kind": str(f.get("kind", "sensor")), "t_ms": float(f.get("t_ms", 0.0) or 0.0),
               "params": dict(f.get("params") or {}), "label": str(f.get("label", "") or "")}
        if "duration_ms" in row["params"]:             # the scenario files' spelling: one field, the engine's unit
            row["params"]["duration_s"] = float(row["params"].pop("duration_ms")) * 1e-3
        if row["kind"] not in FAULT_KINDS:
            raise ValueError(tr(f"알 수 없는 고장 종류 {row['kind']!r}", f"unknown fault kind {row['kind']!r}"))
        self._rows.append(row)
        self.table.insertRow(self.table.rowCount())
        self._refresh_row(len(self._rows) - 1)
        self.table.selectRow(len(self._rows) - 1)
        self.changed.emit()

    def duplicate(self) -> None:
        i = self._current()
        if i is not None:
            r = self._rows[i]
            self.add({"kind": r["kind"], "t_ms": r["t_ms"], "params": dict(r["params"]), "label": r["label"]})

    def remove_selected(self) -> None:
        rows = sorted({ix.row() for ix in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self._rows):
                self._rows.pop(r)
                self.table.removeRow(r)
        self._selected()
        self.changed.emit()

    def set_faults(self, faults: list) -> None:
        self._rows = []
        self.table.setRowCount(0)
        for f in faults or []:
            self.add(f)
        if self._rows:
            self.table.selectRow(0)
        self._selected()

    def faults(self) -> list:
        out = []
        for r in self._rows:
            f = {"kind": r["kind"], "t_ms": float(r["t_ms"]), "params": dict(r["params"])}
            if r.get("label"):
                f["label"] = r["label"]
            out.append(f)
        return out

    def rowCount(self) -> int:          # noqa: N802  (the list's length, like the table it replaces)
        return len(self._rows)

    # -- workspace ---------------------------------------------------------------------------------------------
    def workspace_state(self) -> list:
        return self.faults()

    def restore_workspace(self, rows) -> list[str]:
        problems, keep = [], []
        for i, row in enumerate(rows or []):
            if row.get("kind") not in FAULT_KINDS:
                problems.append(tr(f"{i + 1}행: 고장 종류 '{row.get('kind')}'가 이 앱에 없음 — 뺌",
                                   f"row {i + 1}: fault kind '{row.get('kind')}' does not exist in this app - dropped"))
                continue
            p = row.get("params")
            if isinstance(p, str):                       # a workspace saved by the text-entry table
                try:
                    p = parse_params(p)
                except ValueError as exc:
                    problems.append(tr(f"{i + 1}행: 매개변수 '{p}'를 읽지 못함 ({exc}) — 기본값",
                                       f"row {i + 1}: parameters '{p}' not readable ({exc}) - defaults"))
                    p = default_params(row["kind"])
            try:
                t = float(row.get("t_ms", 0.0) or 0.0)
            except (TypeError, ValueError):
                problems.append(tr(f"{i + 1}행: 시각 '{row.get('t_ms')}'가 숫자가 아님 — 0 ms",
                                   f"row {i + 1}: time '{row.get('t_ms')}' is not a number - 0 ms"))
                t = 0.0
            keep.append({"kind": row["kind"], "t_ms": t, "params": dict(p or {}), "label": row.get("label", "")})
        self.set_faults(keep)
        return problems

    # -- display -----------------------------------------------------------------------------------------------
    def _first(self, what: str, fallback):
        ch = self._choices.get(what) or []
        return ch[0][1] if ch else fallback

    def _value_unit(self, row: dict) -> tuple[str, bool]:
        """(unit of the 'value' parameter, whether the value is used) for the row's kind and mode."""
        k, p = row["kind"], row["params"]
        mode = p.get("mode")
        if k == "sensor":
            name = str(p.get("target"))
            sk = self._sensor_kind.get(name) or self._sensor_kind.get(str(self._roles.get(name, "")), "")
            unit = SENSOR_UNIT.get(sk, tr("센서 단위", "sensor unit"))
            if mode == "gain":
                return tr("비율", "fraction"), True
            if mode == "delay":
                return "s", True
            return unit, mode in ("offset", "stuck")
        if k == "torque_command":
            return "N·m", mode in ("value", "offset", "oscillation")
        if k == "pwm_output":
            return tr("듀티 (0–1)", "duty (0–1)"), mode == "stuck_duty"
        if k == "e2e":
            return "N·m", mode == "value"
        if k == "envelope":
            return "N·m", mode == "value"
        if k == "clock":
            return tr("공칭 대비 배수", "× nominal rate"), mode == "drift"
        return "", True

    def _summary(self, row: dict) -> str:
        parts = []
        for name, typ, _d in FAULT_KINDS[row["kind"]][1]:
            if name not in row["params"]:
                continue
            v = row["params"][name]
            if name == "value":
                unit, used = self._value_unit(row)
                if not used:
                    continue
                parts.append(f"{param_name(name)} {v:g} {unit}" if isinstance(v, (int, float)) else f"{v}")
            elif name == "duration_s":
                parts.append(f"{param_name(name)} {float(v) * 1e3:g} ms")
            elif name == "freq_Hz" and row["params"].get("mode") != "oscillation":
                continue
            elif isinstance(typ, tuple):
                parts.append(choice_name(v))
            elif typ == "float":
                parts.append(f"{param_name(name)} {v:g} {FLOAT_UI.get(name, ('',))[0]}".strip())
            else:
                parts.append(str(v))
        dur = row["params"].get("duration_s")
        if dur is not None and row["kind"] not in OWN_DURATION:
            parts.append(tr(f"간헐 {float(dur) * 1e3:g} ms", f"intermittent {float(dur) * 1e3:g} ms"))
        if row.get("label"):
            parts.append(f"“{row['label']}”")
        return " · ".join(parts) or "—"

    def _refresh_row(self, i: int) -> None:
        r = self._rows[i]
        for j, text in enumerate((fault_name(r["kind"]), f"{float(r['t_ms']):g}", self._summary(r))):
            it = QTableWidgetItem(text)
            if j == 0:
                it.setToolTip(fault_description(r["kind"]))
            self.table.setItem(i, j, it)

    def _current(self) -> int | None:
        rows = sorted({ix.row() for ix in self.table.selectedIndexes()})
        return rows[0] if rows and rows[0] < len(self._rows) else None

    # -- the form of the selected row ---------------------------------------------------------------------------
    def _clear_form(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)

    def _empty_form(self) -> None:
        self._clear_form()
        lab = QLabel(tr("고장을 추가하거나 목록에서 고르면 여기서 종류·시각·매개변수를 고칩니다.",
                        "Add a fault or pick one in the list to edit its kind, time and parameters here."))
        lab.setWordWrap(True)
        self.form.addRow(lab)

    def _mark(self, w: QWidget) -> QWidget:
        w.setProperty("twb_not_input", True)          # a view of the selected row: the list holds the data
        if isinstance(w, QComboBox):                  # built after the page was tidied: it shrinks with the column
            w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)   # too (a long kind or sensor
            w.setMinimumContentsLength(8)                                              # name widened the panel)
        return w

    def _selected(self) -> None:
        i = self._current()
        if i is None:
            self._empty_form()
            self.box.setTitle(tr("선택한 고장", "selected fault"))
            return
        self._build_form(i)

    def _build_form(self, i: int) -> None:
        self._building = True
        self._clear_form()
        row = self._rows[i]
        self.box.setTitle(tr(f"고장 {i + 1}: {fault_name(row['kind'])}", f"fault {i + 1}: {fault_name(row['kind'])}"))
        kind = self._mark(QComboBox())
        for k in FAULT_KINDS:
            if k in OUTSIDE_DRIVE_SCOPE and k != row["kind"]:
                continue
            out = k in OUTSIDE_DRIVE_SCOPE          # a saved scenario keeps its kind, marked
            kind.addItem(fault_name(k) + (tr(" (구동 시스템 범위 밖)", " (outside the drive-system scope)") if out else ""), k)
            kind.setItemData(kind.count() - 1, fault_description(k), Qt.ToolTipRole)
        kind.setCurrentIndex(max(0, kind.findData(row["kind"])))
        kind.currentIndexChanged.connect(lambda _x, w=kind, i=i: self._kind_changed(i, w.currentData()))
        self.form.addRow(tr("종류", "kind"), kind)
        desc = QLabel(fault_description(row["kind"]))
        desc.setWordWrap(True)
        desc.setStyleSheet("color: palette(mid);")
        self.form.addRow(desc)
        t = self._mark(QDoubleSpinBox())
        t.setRange(0.0, 1e7)
        t.setDecimals(3)
        t.setSuffix(" ms")
        t.setValue(float(row["t_ms"]))
        t.valueChanged.connect(lambda v, i=i: self._set(i, None, v, time=True))
        t.setToolTip(tr("고장이 생기는 시각 (시뮬레이션 시작 기준)", "when the fault occurs (from the start of the run)"))
        self.form.addRow(tr("시각", "time"), t)       # short labels: the form fits the input panel (EN 534 > 488 px)
        for name, typ, default in FAULT_KINDS[row["kind"]][1]:
            self.form.addRow(self._param_label(row, name), self._param_widget(i, row, name, typ, default))
        if row["kind"] not in PERMANENT + OWN_DURATION:
            box = QWidget()
            hl = QHBoxLayout(box)
            hl.setContentsMargins(0, 0, 0, 0)
            chk = self._mark(QCheckBox(tr("간헐", "intermittent")))
            chk.setToolTip(tr("켜면 고장이 옆 시간 동안만 있다가 사라짐 (간헐 고장)",
                              "on: the fault lasts the time next to it, then clears (an intermittent fault)"))
            dur = self._mark(QDoubleSpinBox())
            dur.setRange(0.001, 1e7)
            dur.setDecimals(3)
            dur.setSuffix(" ms")
            on = row["params"].get("duration_s") is not None
            chk.setChecked(on)
            dur.setValue(float(row["params"].get("duration_s") or 0.005) * 1e3)
            dur.setEnabled(on)
            chk.toggled.connect(lambda c, d=dur, i=i: (d.setEnabled(c), self._set(i, "duration_s",
                                                                                    d.value() * 1e-3 if c else None)))
            dur.valueChanged.connect(lambda v, c=chk, i=i: self._set(i, "duration_s", v * 1e-3) if c.isChecked()
                                     else None)
            hl.addWidget(chk)
            hl.addWidget(dur)
            hl.addStretch(1)
            self.form.addRow(tr("지속", "duration"), box)
        else:
            note = QLabel(tr("영구 고장 (하드웨어 고장은 지워지지 않음)", "permanent (a hardware fault does not clear)")
                          if row["kind"] in PERMANENT else tr("리셋 지속은 위 매개변수", "the reset duration above"))
            note.setStyleSheet("color: palette(mid);")
            self.form.addRow(tr("지속", "duration"), note)
        lab = self._mark(QLineEdit(row.get("label", "")))
        lab.setPlaceholderText(tr("선택: 결과에 보일 이름", "optional: a name shown in the results"))
        lab.textChanged.connect(lambda s, i=i: self._set(i, None, s, label=True))
        self.form.addRow(tr("이름", "label"), lab)
        self._building = False

    def _param_label(self, row: dict, name: str) -> str:
        if name == "value":
            unit, used = self._value_unit(row)
            if row["kind"] == "sensor" and row["params"].get("mode") == "gain":
                unit += tr(", 0.1 = +10 %", ", 0.1 = +10 %")
            return f"{param_name(name)} [{unit}]" if unit else param_name(name)
        if name in FLOAT_UI:
            return f"{param_name(name)} [{FLOAT_UI[name][0]}]"
        return param_name(name)

    def _param_widget(self, i: int, row: dict, name: str, typ, default) -> QWidget:
        p = row["params"]
        if isinstance(typ, tuple) or typ in self._choices:
            items = [(choice_name(v), v) for v in typ] if isinstance(typ, tuple) else list(self._choices[typ])
            cur = p.get(name, default)
            if cur is not None and all(d != cur for _l, d in items):
                items.append((f"{cur} " + tr("(설계에 없음)", "(not in the design)"), cur))
            w = self._mark(QComboBox())
            for label, data in items:
                w.addItem(label, data)
            if not items:
                w.addItem(tr("(설계에 없음 — 편집 탭에서 추가)", "(none in the design - add one in the editor tab)"), None)
            w.setCurrentIndex(max(0, w.findData(cur)) if cur is not None else 0)
            if name not in p and not isinstance(typ, tuple) and w.currentData() is not None:
                p[name] = w.currentData()                # a design name the engine needs: the shown one
                self._refresh_row(i)
            # a mode or sensor decides the unit and use of 'value': the form follows it (after the signal returns -
            # the combo that emits it is part of the form)
            rebuild = name in ("mode", "target")
            w.currentIndexChanged.connect(lambda _x, w=w, i=i, n=name, rb=rebuild: (
                self._set(i, n, w.currentData()), self._mode_value(i, n), self._rebuild_later(i) if rb else None))
            return w
        w = self._mark(QDoubleSpinBox())
        if name == "value":
            w.setRange(-1e7, 1e7)
            w.setDecimals(4)
            w.setSingleStep(1.0)
            unit, used = self._value_unit(row)
            w.setValue(float(p.get(name, default if default is not None else 0.0)))
            w.setEnabled(used)
            if not used:
                w.setToolTip(tr("이 방식은 값을 쓰지 않습니다", "this mode does not use a value"))
            w.valueChanged.connect(lambda v, i=i: self._set(i, "value", v))
            return w
        unit, lo, hi, dec, step, scale = FLOAT_UI.get(name, ("", -1e9, 1e9, 4, 1.0, 1.0))
        w.setRange(lo, hi)
        w.setDecimals(dec)
        w.setSingleStep(step)
        if unit:
            w.setSuffix(f" {unit}")
        w.setValue(float(p.get(name, default if default is not None else 0.0)) / scale)
        if name == "freq_Hz" and p.get("mode", "value") != "oscillation":
            w.setEnabled(False)                          # only the oscillation mode uses a frequency
            w.setToolTip(tr("진동 방식에서만 씁니다", "used by the oscillation mode only"))
        w.valueChanged.connect(lambda v, i=i, n=name, s=scale: self._set(i, n, v * s))
        return w

    def _mode_value(self, i: int, name: str) -> None:
        """A sensor fault's mode decides what 'value' means (A, a fraction, seconds): a new mode starts from its own
        typical value - an offset of 100 A must not become a gain of 100 (x 101)."""
        r = self._rows[i]
        if name != "mode" or r["kind"] != "sensor":
            return
        typical = {"gain": 0.1, "delay": 1e-3}.get(r["params"].get("mode"))
        if typical is not None:
            self._set(i, "value", typical)

    def _rebuild_later(self, i: int) -> None:
        if not self._building:
            QTimer.singleShot(0, lambda i=i: self._build_form(i) if self._current() == i else None)

    def _kind_changed(self, i: int, kind: str) -> None:
        if self._building or kind == self._rows[i]["kind"]:
            return
        keep = {k: v for k, v in self._rows[i]["params"].items() if k == "duration_s"}
        self._rows[i]["kind"] = kind
        self._rows[i]["params"] = {**default_params(kind), **({} if kind in PERMANENT + OWN_DURATION else keep)}
        if kind == "sensor":
            self._rows[i]["params"].setdefault("target", self._first("sensor", "CS_A"))
        self._refresh_row(i)
        self._rebuild_later(i)
        self.changed.emit()

    def _set(self, i: int, name: str | None, value, time: bool = False, label: bool = False) -> None:
        if self._building or not (0 <= i < len(self._rows)):
            return
        r = self._rows[i]
        if time:
            r["t_ms"] = float(value)
        elif label:
            r["label"] = str(value).strip()
        elif value is None:
            r["params"].pop(name, None)
        else:
            r["params"][name] = value
        self._refresh_row(i)
        self.changed.emit()


# ---------------------------------------------------------------------------------------------- campaign axes

def _span(v: float, rel: float = 0.5) -> tuple[float, float]:
    v = float(v)
    if abs(v) < 1e-12:
        return 0.0, 1.0
    a, b = v * (1 - rel), v * (1 + rel)
    return (min(a, b), max(a, b))


def axis_catalog(scenario: dict, design: dict | None, kind_params: dict | None) -> list[dict]:
    """What a campaign can vary for this scenario and design, as people name it: label, the scenario path the
    engine reads, numeric or a list of choices, and a starting range (the declared tolerance where there is one)."""
    sc, d = scenario or {}, design or {}
    out: list[dict] = []

    def num(group, label, path, cur, lo, hi, n=5, unit=""):
        out.append({"group": group, "label": label + (f" [{unit}]" if unit else ""), "path": path, "kind": "num",
                    "range": [float(lo), float(hi)], "n": int(n), "current": cur})

    def choice(group, label, path, values, cur=None):
        out.append({"group": group, "label": label, "path": path, "kind": "choice", "values": list(values),
                    "current": cur})
    g = tr("운전점", "operating point")
    spd = float(sc.get("speed_rpm", 12000.0))
    num(g, tr("속도", "speed"), "speed_rpm", spd, *_span(spd, 0.3), unit="rpm")
    rq = sc.get("request")
    if rq:
        t0 = float(rq.get("T0_Nm", 0.0))
        num(g, tr("요청 T0", "request T0"), "request.T0_Nm", t0, *((-100.0, 100.0) if t0 == 0 else _span(t0)), unit="N·m")
        if rq.get("kind") in ("step", "ramp"):
            t1 = float(rq.get("T1_Nm", 0.0))
            num(g, tr("요청 T1", "request T1"), "request.T1_Nm", t1, *((-100.0, 100.0) if t1 == 0 else _span(t1)),
                unit="N·m")
    elif "torque_Nm" in sc:
        t0 = float(sc["torque_Nm"])
        num(g, tr("토크 요청", "torque request"), "torque_Nm", t0, *_span(t0), unit="N·m")
    num(g, tr("초기 전기각", "initial electrical angle"), "theta0_deg", float(sc.get("theta0_deg", 0.0)), 0.0, 330.0, 12,
        unit="°")
    for i, f in enumerate(sc.get("faults") or []):
        k = f.get("kind")
        if k not in FAULT_KINDS:
            continue
        g = tr(f"고장 {i + 1} ({fault_name(k)})", f"fault {i + 1} ({fault_name(k)})")
        t = float(f.get("t_ms", 0.0))
        num(g, tr("시각", "time"), f"faults.{i}.t_ms", t, t, t + 5.0, 6, unit="ms")
        p = f.get("params") or {}
        for name, typ, default in FAULT_KINDS[k][1]:
            path = f"faults.{i}.params.{name}"
            if isinstance(typ, tuple):
                choice(g, param_name(name), path, typ, p.get(name, default))
            elif typ == "float":
                cur = float(p.get(name, default if default is not None else 0.0))
                unit = FLOAT_UI.get(name, ("",))[0] if name != "value" else ""
                if name == "duration_s":
                    num(g, param_name(name), path, cur, *_span(cur), unit="s")
                else:
                    num(g, param_name(name), path, cur, *_span(cur), unit=unit)
    g = tr("센서 공차", "sensor tolerances")
    for s in d.get("sensors") or []:
        n, kind = s.get("name"), s.get("kind", "")
        if not n:
            continue
        if kind != "position":
            gt = float(s.get("gain_tol") or 0.01)
            num(g, tr(f"{n} 이득 오차", f"{n} gain error"), f"tolerances.{n}.gain_err", 0.0, -gt, gt, 3,
                unit=tr("비율", "fraction"))
            ot = next((float(s[x]) for x in ("offset_tol_A", "offset_tol_V", "offset_tol_C") if s.get(x) is not None),
                      None)
            if ot:
                num(g, tr(f"{n} 오프셋", f"{n} offset"), f"tolerances.{n}.offset", 0.0, -ot, ot, 3,
                    unit=SENSOR_UNIT.get(kind, ""))
        else:
            ot = float(s.get("offset_tol_deg") or 1.0)
            num(g, tr(f"{n} 각도 오프셋", f"{n} angle offset"), f"tolerances.{n}.offset_deg", 0.0, -ot, ot, 3,
                unit="° e")
        num(g, tr(f"{n} 추가 지연", f"{n} extra delay"), f"tolerances.{n}.delay_s", 0.0, 0.0, 50e-6, 3, unit="s")
    g = tr("기기 모델", "machine model")
    for key, lab in (("psi_scale", "ψ"), ("Ld_scale", "L_d"), ("Lq_scale", "L_q"), ("Rs_scale", "R_s")):
        num(g, tr(f"{lab} 배율", f"{lab} scale"), f"tolerances.machine.{key}", 1.0, 0.95, 1.05, 3)
    num(g, tr("데드타임 배율", "dead-time scale"), "tolerances.deadtime_scale", 1.0, 0.8, 1.2, 3)
    g = tr("반응 경로", "reaction paths")
    for pth in d.get("paths") or []:
        pid = pth.get("id")
        if not pid:
            continue
        cur = float(pth.get("delay_us") or 0.0)
        num(g, tr(f"{pid} 지연", f"{pid} delay"), f"overrides.paths.{pid}.delay_us", cur, *_span(cur), unit="µs")
    g = tr("안전 메커니즘", "safety mechanisms")
    kp = kind_params or {}
    for m in d.get("mechanisms") or []:
        mid, mk = m.get("id"), m.get("kind")
        if not mid:
            continue
        mp = m.get("params") or {}
        for key, unit, default, desc in [tuple(x) for x in kp.get(mk) or []]:
            path = f"overrides.mechanisms.{mid}.params.{key}"
            if isinstance(unit, (list, tuple)):
                choice(g, f"{mid} · {desc}", path, unit, mp.get(key, default))
                continue
            cur = mp.get(key, default)
            if cur is None:
                continue
            num(g, f"{mid} · {desc}", path, float(cur), *_span(float(cur)), unit=str(unit) if unit != "-" else "")
    return out


class _CatalogCombo(QComboBox):
    """A combo that fills itself from a provider each time it opens (the scenario may have changed)."""

    def __init__(self, provider):
        super().__init__()
        self.provider = provider

    def showPopup(self):                # noqa: N802
        self.provider()
        super().showPopup()


class AxisEditor(QWidget):
    """Campaign axes: chosen from what this scenario and design can vary (no typed paths), each a range (from, to,
    points) or a list of values."""
    changed = Signal()
    COLS = 5

    def __init__(self, catalog_provider=None, parent=None):
        super().__init__(parent)
        self.catalog_provider = catalog_provider or (lambda: [])
        self._catalog: list[dict] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.pick = _CatalogCombo(self.refresh_catalog)
        self.pick.setProperty("twb_not_input", True)      # a chooser: the axes table holds the inputs
        self.pick.setMinimumContentsLength(28)
        self.add_btn = QPushButton(tr("+ 축 추가", "+ add axis"))
        self.add_btn.clicked.connect(self.add_picked)
        row.addWidget(QLabel(tr("바꿀 양", "vary")))
        row.addWidget(self.pick, 1)
        row.addWidget(self.add_btn)
        lay.addLayout(row)
        self.table = QTableWidget(0, self.COLS)
        self.table.setHorizontalHeaderLabels([tr("양", "quantity"), tr("부터", "from"), tr("까지", "to"),
                                              tr("점", "points"), tr("값 목록", "values")])
        self.table.horizontalHeaderItem(4).setToolTip(tr("값 목록 (a, b, …)을 쓰면 범위 대신 그 값들로 실행합니다",
                                                         "a list (a, b, …) runs those values instead of the range"))
        self.table.setWordWrap(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for j in (1, 2, 3):
            hh.setSectionResizeMode(j, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(110)
        self.table.itemChanged.connect(lambda *_: self.changed.emit())
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.del_btn = QPushButton(tr("− 선택 삭제", "− remove selected"))
        self.del_btn.clicked.connect(self.remove_selected)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.refresh_catalog()

    # -- catalog -------------------------------------------------------------------------------------------------
    def refresh_catalog(self) -> None:
        try:
            self._catalog = list(self.catalog_provider() or [])
        except Exception:  # noqa: BLE001  (an unfinished scenario: keep the last list)
            pass
        cur = self.pick.currentData()
        self.pick.blockSignals(True)
        self.pick.clear()
        last = None
        for e in self._catalog:
            if e["group"] != last:
                self.pick.addItem(f"— {e['group']} —", None)
                k = self.pick.count() - 1
                self.pick.model().item(k).setEnabled(False)
                last = e["group"]
            self.pick.addItem(f"   {e['label']}", e["path"])
            self.pick.setItemData(self.pick.count() - 1, e["path"], Qt.ToolTipRole)
        k = self.pick.findData(cur) if cur is not None else -1
        self.pick.setCurrentIndex(k if k >= 0 else (1 if self.pick.count() > 1 else 0))
        self.pick.blockSignals(False)
        for r in range(self.table.rowCount()):           # names follow the catalog (a fault re-labelled)
            it = self.table.item(r, 0)
            e = self._entry(it.data(Qt.UserRole)) if it is not None else None
            if e is not None:
                it.setText(f"{e['group']} · {e['label']}")

    def _entry(self, path) -> dict | None:
        return next((e for e in self._catalog if e["path"] == path), None)

    # -- rows ----------------------------------------------------------------------------------------------------
    def add_picked(self) -> None:
        path = self.pick.currentData()
        e = self._entry(path)
        if e is None:
            return
        if e["kind"] == "choice":
            self._append(path, values=e["values"])
        else:
            self._append(path, rng=e["range"], n=e["n"])
        self.changed.emit()

    def _append(self, path: str, rng=None, n=None, values=None) -> None:
        e = self._entry(path)
        r = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(r)
        q = QTableWidgetItem(f"{e['group']} · {e['label']}" if e else path)
        q.setData(Qt.UserRole, path)
        q.setToolTip((f"{e['group']} · {e['label']}\n" if e else "") + path)
        q.setFlags(q.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, 0, q)
        choice = e is not None and e["kind"] == "choice"
        cells = ("", "", "") if (choice or rng is None) else (f"{rng[0]:g}", f"{rng[1]:g}", f"{int(n or 5)}")
        for j, text in enumerate(cells, start=1):
            it = QTableWidgetItem(text)
            if choice:
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                it.setText("—")
            self.table.setItem(r, j, it)
        self.table.setItem(r, 4, QTableWidgetItem(", ".join(str(v) for v in values) if values else ""))
        self.table.resizeRowToContents(r)
        self.table.blockSignals(False)

    def remove_selected(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)
        self.changed.emit()

    def set_axes(self, axes: list) -> None:
        self.refresh_catalog()
        self.table.setRowCount(0)
        for a in axes or []:
            if a.get("range"):
                self._append(a["path"], rng=a["range"], n=a.get("n", 5))
            else:
                self._append(a["path"], values=a.get("values") or [])
        self.changed.emit()

    def axes(self) -> list:
        """The axes for the campaign: a value list where one is given, the range otherwise (a row with neither, or
        a number that does not read, is named - never skipped silently)."""
        out = []
        for r in range(self.table.rowCount()):
            path = self.table.item(r, 0).data(Qt.UserRole)
            name = self.table.item(r, 0).text()
            vals = (self.table.item(r, 4).text() if self.table.item(r, 4) else "").strip()
            if vals:
                out.append({"path": path, "values": [_num(x) for x in vals.split(",") if x.strip()]})
                continue
            try:
                lo, hi = (float(self.table.item(r, j).text()) for j in (1, 2))
                n = int(float(self.table.item(r, 3).text()))
            except (AttributeError, ValueError):
                raise ValueError(tr(f"축 '{name}': 부터·까지·점 수 또는 값 목록이 필요합니다",
                                    f"axis '{name}': from, to and points, or a list of values, are needed")) from None
            if n < 2:
                raise ValueError(tr(f"축 '{name}': 점 수는 2 이상", f"axis '{name}': at least 2 points"))
            out.append({"path": path, "range": [lo, hi], "n": n})
        return out

    # -- workspace -----------------------------------------------------------------------------------------------
    def workspace_state(self) -> list:
        rows = []
        for r in range(self.table.rowCount()):
            rows.append({"path": self.table.item(r, 0).data(Qt.UserRole),
                         "cells": [(self.table.item(r, j).text() if self.table.item(r, j) else "") for j in (1, 2, 3, 4)]})
        return rows

    def restore_workspace(self, rows) -> list[str]:
        self.refresh_catalog()
        self.table.setRowCount(0)
        for row in rows or []:
            self._append(str(row.get("path", "")))
            r = self.table.rowCount() - 1
            for j, text in zip((1, 2, 3, 4), row.get("cells") or []):
                it = self.table.item(r, j)
                if it is not None and it.flags() & Qt.ItemIsEditable:
                    it.setText(str(text))
        self.changed.emit()
        return []
