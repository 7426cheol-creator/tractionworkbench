"""Verification page: production output vs the immutable golden fixtures, manifest hashes, known limits."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from ... import __version__
from ... import service as S
from ...i18n import tr
from ...plots import figures as F
from ..widgets import KeyValueTable, PlotPanel, hint, primary_button

LIMITATIONS_KO = """
**검증 범위** — 합성(synthetic) 참조 fixture에 대한 verification입니다. 하드웨어·공급사 데이터·외부 시뮬레이터
validation(V4–V5)은 수행하지 않았습니다. 수치 자릿수는 회귀 검산용이며 제품 정확도가 아닙니다.

**모델 계약** — 단일 3상 2-level 인버터 + PMSM/IPMSM, 정상상태 기본파 dq 모델(진폭 불변 Park, d축 = PM, dq = 상 peak).
선형 SVPWM 전압 예산 (1 − r_v)·Vdc/√3, 기본파 전류 한계, 선언된 운전 도메인, DC 평균 전력·전류 한계.

**알려진 한계**
- 판정 모델(요구 판정·capability)은 정상상태 기본파입니다. 과변조·6-step, 소자 강하(명시한 저항 모델 제외), 과도 현상, 철손
  분리는 그 밖입니다. PWM 리플·최소 펄스·지연·전환은 '가변 PWM' 페이지의 선언 기반 스크리닝이며, 데이터시트 모듈 손실의 온도
  결합은 효율·모듈 비교에서 계산됩니다.
- 파형·듀티 그래프는 같은 기본파 값의 재표현(평균값 모델)이며 스위칭 파형이 아닙니다.
- Vdc 범위 요구는 표본점 통과만으로 PASS가 되지 않습니다(SAMPLED_COVERAGE).
- flux map 모델은 셀 단위 쌍선형 보간, 데이터 밖 외삽 없음. 격자 기반 곡선·맵은 시각화용 근사입니다.
- 열·FTTI·방전·과전압·ASC/Freewheel은 선언 값 기반 스크리닝이며 기능안전 승인·SOA 검증을 대신하지 않습니다.
- 회생 경계는 에너지 회수(최소전류) 정책 기준이며, 의도적 손실 증가 운전은 진단으로만 보고합니다.
"""

LIMITATIONS_EN = """
**Scope** — verification against synthetic reference fixtures only. No hardware, supplier-data or external-simulator
validation (V4–V5) has been performed. Digits are regression checks, not product accuracy.

**Model contract** — single three-phase two-level inverter + PMSM/IPMSM, steady-state fundamental dq model
(amplitude-invariant Park, d axis = PM, dq = phase peak). Linear SVPWM budget (1 − r_v)·Vdc/√3, fundamental current
limit, declared operating domain, average DC power/current limits.

**Known limitations**
- The decision model (requirement verdicts, capability) is the steady-state fundamental. Overmodulation / six-step,
  device drops (except a declared resistive model), transients and separate iron loss are outside it. PWM ripple,
  minimum pulse, delay and transitions are declared-data screenings on the 'Variable PWM' page; the temperature
  coupling of datasheet module losses is computed in 'Efficiency & modules'.
- Waveform and duty plots re-express the same fundamental values (average model); they are not switching waveforms.
- A Vdc range requirement never becomes PASS from samples alone (SAMPLED_COVERAGE).
- Flux maps: bilinear per cell, no extrapolation. Grid-based curves/maps are visualisation approximations.
- Thermal, FTTI, discharge, overvoltage and ASC/freewheel are screenings of declared values; they do not replace
  functional-safety approval or SOA verification.
- Regenerative boundaries use the energy-recovering (minimum-current) policy; deliberate loss increase is diagnostic only.
"""


def _task(progress):
    progress(0.1, tr("golden 비교", "golden comparison"))
    return S.acceptance_summary()


class VerificationPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        self.run_btn = primary_button(tr("acceptance 실행 (production vs golden)", "run acceptance (production vs golden)"))
        self.run_btn.clicked.connect(self.run)
        top.addWidget(self.run_btn)
        self.summary = QLabel(f"software {__version__}")
        top.addWidget(self.summary, 1)
        self.exch_btn = QPushButton(tr("교환 패키지 내보내기 (JSON)", "export exchange package (JSON)"))
        self.exch_btn.setToolTip(tr("MathWorks 이식·도구 간 parity용: 규약, 모델 식별, 예시 입력, fixture (구현 검증이며 물리 승인 아님)",
                                    "for a MathWorks port / cross-tool parity: conventions, identities, example inputs, "
                                    "fixtures (implementation verification, not physical qualification)"))
        self.exch_btn.clicked.connect(self.export_exchange)
        top.addWidget(self.exch_btn)
        lay.addLayout(top)
        split = QSplitter(Qt.Horizontal)
        self.tabs = QTabWidget()
        self.plot = PlotPanel()
        self.table = KeyValueTable(headers=["group", "case", "metric", "value", "tolerance", "labels", "result"])
        self.manifest = KeyValueTable(headers=["file", "sha256", "ok"])
        self.tabs.addTab(self.plot, tr("오차 / 허용오차", "error / tolerance"))
        self.tabs.addTab(self.table, tr("상세 표", "table"))
        self.tabs.addTab(self.manifest, tr("참조 패키지 무결성", "reference integrity"))
        split.addWidget(self.tabs)
        doc = QTextBrowser()
        doc.setMarkdown(LIMITATIONS_KO if tr("ko", "en") == "ko" else LIMITATIONS_EN)
        split.addWidget(doc)
        split.setSizes([900, 520])
        lay.addWidget(split, 1)
        lay.addWidget(hint(tr("독립 검산(production 코드 미사용)은 verification/independent_fixture_check.py로 별도 실행합니다.",
                              "The independent re-derivation (no production code) runs separately: verification/independent_fixture_check.py.")))

    def run(self):
        self.run_btn.setEnabled(False)
        self.win.runner.run("acceptance", "acceptance", _task, self._show, on_error=lambda m, t: self.run_btn.setEnabled(True))

    def _show(self, acc):
        self.run_btn.setEnabled(True)
        n_pass = sum(r["pass"] for r in acc["rows"])
        ok = acc["all_pass"] and acc["manifest_ok"]
        color = "#1a7f37" if ok else "#cf222e"
        self.summary.setText(f"<span style='color:{color}; font-weight:600'>{n_pass}/{len(acc['rows'])} PASS · manifest "
                             f"{'OK' if acc['manifest_ok'] else 'MISMATCH'}</span> · {acc['elapsed_s']:.2f} s · {acc['scope']}")
        self.summary.setWordWrap(True)
        self.plot.draw(F.fig_acceptance, acc, name="acceptance")
        rows = []
        colors = {}
        for i, r in enumerate(acc["rows"]):
            rows.append((r["group"], r["case"], r["metric"] + (" (certified)" if r.get("certified") else ""),
                         "—" if r["value"] is None else f"{r['value']:.3e}",
                         "—" if r["tolerance"] is None else f"{r['tolerance']:.3g}",
                         "OK" if r["labels_ok"] else "MISMATCH", "PASS" if r["pass"] else "FAIL"))
            colors[(i, 6)] = "#1a7f37" if r["pass"] else "#cf222e"
        self.table.set_rows(rows, colors)
        self.manifest.set_rows([(m["name"], m["sha256"], "OK" if m["ok"] else "MISMATCH") for m in acc["manifest"]])

    def export_exchange(self, path: str | None = None):
        import json
        from ...decision import jsonable as _jsonable
        from ...exchange import build_package
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, tr("교환 패키지 저장", "save exchange package"),
                                                  "twb_exchange.json", "JSON (*.json)")
        if not path:
            return None
        pkg = build_package()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_jsonable(pkg), fh, indent=2, ensure_ascii=False)
        self.summary.setText(tr(f"교환 패키지 저장: {path}", f"exchange package written: {path}"))
        return path

    def redraw(self):
        self.plot.redraw()
