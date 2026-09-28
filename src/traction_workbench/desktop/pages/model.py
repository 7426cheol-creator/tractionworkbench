"""Model & data page: choose/load the drive, DC source limits, provenance and model description."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ...i18n import tr
from ...io import load_json_file
from ..state import BUILTIN_DRIVES
from ..widgets import KeyValueTable, combo, error_box, fmt, hint, number, primary_button


def _fill(parent, key, value):
    if isinstance(value, dict):
        it = QTreeWidgetItem(parent, [str(key), ""])
        for k, v in value.items():
            _fill(it, k, v)
        return it
    if isinstance(value, list) and value and isinstance(value[0], (dict, list)):
        it = QTreeWidgetItem(parent, [str(key), f"[{len(value)}]"])
        for i, v in enumerate(value):
            _fill(it, i, v)
        return it
    it = QTreeWidgetItem(parent, [str(key), fmt(value) if not isinstance(value, str) else value])
    it.setToolTip(1, it.text(1))
    return it


def examples_dir() -> Path | None:
    import sys
    cands = []
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        cands.append(Path(frozen) / "examples")
    cands.append(Path(__file__).resolve().parents[4] / "examples")
    for c in cands:
        if c.is_dir():
            return c
    return None


class ModelPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        v = QVBoxLayout(left)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("드라이브 모델", "drive model"))
        gl = QVBoxLayout(g)
        self.builtin = combo([(tr("합성 IPMSM 200 kW (상수 dq, D1)", "synthetic IPMSM 200 kW (constant dq, D1)"), BUILTIN_DRIVES[0]),
                              (tr("제작 flux map 시험 드라이브 (D2)", "manufactured flux-map test drive (D2)"), BUILTIN_DRIVES[1])])
        b = QPushButton(tr("내장 모델 적용", "use built-in model"))
        b.clicked.connect(self._use_builtin)
        gl.addWidget(self.builtin)
        gl.addWidget(b)
        b = QPushButton(tr("드라이브 JSON 불러오기… (단위 선언 형식)", "load drive JSON… (declared units)"))
        b.clicked.connect(self._load_drive)
        gl.addWidget(b)
        b = QPushButton(tr("case 파일 열기 → 요구 판정", "open case file → decision"))
        b.clicked.connect(self.win.open_case)
        gl.addWidget(b)
        gl.addWidget(hint(tr("단위·정의(peak/RMS, 상/선간, 기계/전기 속도, Ke/Kt 규약)가 모호하면 계산 전에 INVALID_INPUT으로 거부합니다. "
                             "모든 변환은 기록에 남습니다.",
                             "Ambiguous units/definitions are rejected as INVALID_INPUT before any calculation; every conversion is recorded.")))
        v.addWidget(g)
        g = QGroupBox(tr("DC 소스 한계 (인버터 DC 단자 평균값)", "DC source limits (average, inverter DC terminal)"))
        f = QFormLayout(g)
        lim = self.win.state.limits_dict
        self.lim_fields = {}
        for key, lab, unit, scale in (("discharge_power_max_W", tr("방전 전력", "discharge power"), "kW", 1e-3),
                                      ("charge_power_max_W", tr("충전 전력", "charge power"), "kW", 1e-3),
                                      ("discharge_current_max_A", tr("방전 전류", "discharge current"), "A", 1.0),
                                      ("charge_current_max_A", tr("충전 전류", "charge current"), "A", 1.0)):
            cur = lim.get(key)
            mode = "value" if (cur is not None and math.isfinite(cur)) else ("unlimited" if cur is not None else "missing")
            kind = combo([(tr("값", "value"), "value"), (tr("미선언 (UNKNOWN)", "not declared (UNKNOWN)"), "missing"),
                          (tr("선언된 무제한 (∞)", "declared unlimited (∞)"), "unlimited")], mode)
            kind.setToolTip(tr("미선언은 무제한이 아닙니다: 묶일 수 있는 한계가 미선언이면 DC claim은 UNKNOWN. '선언된 무제한'은 "
                               "근거가 있을 때만 선택하세요.",
                               "Not declared is not unlimited: a limit that can bind but is not declared makes the DC claim "
                               "UNKNOWN. Choose 'declared unlimited' only with a basis."))
            val = number((cur if (cur is not None and math.isfinite(cur)) else 0.0) * scale, 0, 1e7, unit, 3)
            kind.currentIndexChanged.connect(lambda _i, k=kind, w=val: w.setEnabled(k.currentData() == "value"))
            val.setEnabled(mode == "value")
            row = QHBoxLayout()
            row.addWidget(kind)
            row.addWidget(val, 1)
            f.addRow(lab, row)
            self.lim_fields[key] = (kind, val, scale)
        b = primary_button(tr("한계 적용", "apply limits"))
        b.clicked.connect(self._apply_limits)
        f.addRow(b)
        v.addWidget(g)
        ex = examples_dir()
        if ex:
            v.addWidget(hint(tr(f"예시 case/드라이브 파일: {ex}", f"example case/drive files: {ex}")))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(left)
        sc.setMinimumWidth(340)
        split.addWidget(sc)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setWordWrap(True)
        rv.addWidget(self.title)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("항목", "item"), tr("값", "value")])
        self.tree.setColumnWidth(0, 320)
        self.tree.setAlternatingRowColors(True)
        rv.addWidget(self.tree, 3)
        rv.addWidget(QLabel(tr("<b>데이터 감사</b> — 이 데이터로 할 수 있는 것 / 없는 것 (이웃 용도로 승격하지 않음)",
                               "<b>data audit</b> — what these data may and may not be used for (never promoted from a "
                               "neighbouring use)")))
        self.audit = KeyValueTable(headers=[tr("용도", "use"), tr("상태", "status"), tr("근거", "basis")])
        rv.addWidget(self.audit, 2)
        split.addWidget(right)
        split.setSizes([360, 1060])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)
        self.win.state.drive_changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        s = self.win.state
        info = s.info()
        prov = info.get("provenance", {})
        self.title.setText(f"<b>{info.get('drive_id')}</b> rev {info.get('revision')} · fidelity {info.get('fidelity')} · "
                           f"{prov.get('origin')} · {tr('출처', 'source')}: {s.drive_source}<br>"
                           f"<span style='font-size:8.5pt'>{prov.get('validation_status', '')}</span>")
        self.tree.clear()
        for k, val in info.items():
            _fill(self.tree, k, val)
        _fill(self.tree, tr("DC 소스 한계", "DC source limits"), s.limits.describe())
        self.tree.expandToDepth(0)
        from ... import service as S
        try:
            au = S.data_audit(s.drive)
        except Exception as exc:  # noqa: BLE001 - the audit never blocks the page
            self.audit.set_rows([("audit", "ERROR", str(exc))])
            return
        rows, colors = [], {}
        for i, u in enumerate(au["supported_uses"]):
            st = str(u["status"])
            rows.append((u["use"], st, u.get("basis", "")))
            up = st.upper()
            colors[(i, 1)] = ("#cf222e" if any(w in up for w in ("MISSING", "NOT SUPPORTED", "NEVER", "NOT QUALIFIED"))
                              else "#9a6700" if any(w in up for w in ("SCREENING", "NOT COVERED", "WARNING", "NOT DECLARED",
                                                                     "NOT PART")) else "#1a7f37")
        for g in au["qualification_gaps"]:
            rows.append((tr("qualification 공백", "qualification gap"), "GAP", g))
            colors[(len(rows) - 1, 1)] = "#cf222e"
        self.audit.set_rows(rows, colors)

    def _use_builtin(self):
        key = self.builtin.currentData()
        try:
            self.win.state.set_drive({"builtin": key}, key, "builtin")
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("모델 오류", "model error"), str(exc))

    def _load_drive(self):
        start = str(examples_dir() or "")
        path, _ = QFileDialog.getOpenFileName(self, tr("드라이브 JSON", "drive JSON"), start, "JSON (*.json)")
        if not path:
            return
        try:
            d = load_json_file(path)
            spec = d.get("drive", d)
            self.win.state.set_drive(spec, Path(path).stem, f"file {Path(path).name}")
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("드라이브 불러오기 실패", "could not load drive"), str(exc))

    def _apply_limits(self):
        d = {}
        for key, (kind, val, scale) in self.lim_fields.items():
            m = kind.currentData()
            d[key] = val.value() / scale if m == "value" else (math.inf if m == "unlimited" else None)
        try:
            self.win.state.set_limits(d)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("한계 오류", "limit error"), str(exc))

    def redraw(self):
        pass
