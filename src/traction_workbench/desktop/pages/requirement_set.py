"""Requirement-set page (engineering review 6198099, user features 1 - 3): many requirements judged on the active
project's product data with the same conditions and evidence, one row each (verdict, class of an open answer, margin,
limiting cause, next data), and candidate design changes re-judged against every requirement."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QGroupBox, QHeaderView, QLabel, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget, QHBoxLayout)

from ... import requirement_set as RS
from ...analysis.variation import PARAMETERS
from ...i18n import tr
from ..widgets import ConceptNote, NumTable, error_box, fmt, hint, primary_button, table_with_buttons

VERDICT_COLOR = {"PASS": "#1a7f37", "FAIL": "#cf222e", "UNKNOWN": "#9a6700"}
TEXT_COLS = (0, 1, 6, 7, 11, 13, 14)          # id, text, duration, initial_state, operator, speed kind, Vdc port
EFFORT = {"declaration": ("선언 (값 명시)", "declaration (state a value)"),
          "documents": ("문서 (근거 결속·정격)", "documents (bind evidence / ratings)"),
          "analysis": ("해석", "analysis"), "new data": ("새 자료 (측정·모델 확장)", "new data (measurement / model extension)")}
SPEC = {"violated": ("위반 (모델 무관)", "violated (model-free)"), "necessary_ok": ("필요조건 충족", "necessary met"),
        "needs_model": ("모델 필요", "needs the model"), "not_applicable": ("해당 없음", "n/a")}


def _class_label(key: str) -> str:
    fixed = {"pass": ("만족", "met"), "violation": ("증명된 위반", "proven violation"),
             "not_rated": ("정격 밖", "not rated"), "open": ("미확정", "open")}
    if key in fixed:
        return tr(*fixed[key])
    for k, ko, en, *_r in RS.REASON_CLASSES:
        if k == key:
            return tr(ko, en)
    return key


def _pair(d: dict, key) -> str:
    ko, en = d.get(key, (str(key), str(key))) if key is not None else ("—", "—")
    return tr(ko, en)


parse_candidates = RS.parse_candidates


def _task(progress, text_rows, drive, limits, candidates):
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(RS.CSV_COLUMNS)
    for r in text_rows:
        w.writerow(["" if v is None else v for v in r])
    dicts = []
    reqs = RS.parse_requirements_csv(buf.getvalue(), drive.motor.pole_pairs, dicts)
    n_c = len(candidates)
    base = RS.evaluate_set(reqs, drive, limits, with_capability=True,
                           progress=lambda f, m: progress(f / (1 + n_c), tr(f"요구 {m}", f"requirement {m}")))
    cands = RS.evaluate_candidates(reqs, drive, limits, candidates, baseline=base,
                                   progress=lambda f, m: progress((1 + f * n_c) / (1 + n_c),
                                                                  tr(f"후보 · {m}", f"candidate · {m}"))) \
        if candidates else None
    return {"set": base, "candidates": cands, "req_dicts": dicts}


class RequirementSetPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.result = None
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("요구 목록 (한 제품·같은 조건·같은 근거로 판정)", "requirements (one product, same conditions and "
                                                                "evidence)"))
        gl = QVBoxLayout(g)
        heads = ["ID", tr("원문", "text"), "T [N·m]", "n [rpm]", "Vdc [V]", tr("Vdc 상한 [V]", "Vdc max [V]"),
                 tr("지속 [s]", "duration [s]"), tr("초기 상태", "initial state"), tr("냉각수 [°C]", "coolant [°C]"),
                 tr("자석 [°C]", "magnet [°C]"), tr("권선 [°C]", "winding [°C]"), tr("연산", "operator"),
                 tr("밴드 [N·m]", "band [N·m]"), tr("속도 종류", "speed kind"), tr("Vdc 포트", "Vdc port")]
        self.table = NumTable(heads, min_height=220, text_cols=TEXT_COLS, optional_cols=(5, 8, 9, 10, 12))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for j, wdt in enumerate((70, 170, 60, 64, 60, 70, 64, 120, 70, 64, 64, 64, 64, 84, 130)):
            self.table.setColumnWidth(j, wdt)
        gl.addWidget(table_with_buttons(self.table, tr(
            "토크는 모터 축 토크, 속도는 기계 rpm(속도 종류 electrical이면 극쌍수로 환산), Vdc는 인버터 DC 단자 전압입니다(배터리 "
            "쪽 값은 소스 모델이 필요해 거절). 지속은 초 또는 'continuous', 연산은 achieve / band. 비운 온도는 미지정(자석 온도: 온도 "
            "plane이 여러 개인 flux map은 모든 plane에서 for-all). 스프레드시트에서 붙여넣을 수 있습니다.",
            "torque is motor shaft torque, speed mechanical rpm (speed kind 'electrical' is converted with the pole "
            "pairs), Vdc the inverter DC terminal voltage (a battery-side value needs a source model and is refused). "
            "Duration in s or 'continuous', operator achieve / band. A blank temperature is not stated (magnet: a "
            "flux map with several planes is judged for all of them). Paste from a spreadsheet.")))
        row = QHBoxLayout()
        for text, fn in ((tr("해석 확인", "check reading"), self.check_reading),
                         (tr("예시 채우기", "fill example"), self.fill_example),
                         (tr("CSV 가져오기…", "import CSV…"), self.import_csv),
                         (tr("CSV 템플릿 저장…", "save CSV template…"), self.save_template)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        row.addStretch(1)
        gl.addLayout(row)
        v.addWidget(g)
        g = QGroupBox(tr("후보 (설계 변경안) — 한 줄에 하나", "candidates (design changes) - one per line"))
        gl = QVBoxLayout(g)
        self.cands = QPlainTextEdit()
        self.cands.setPlaceholderText(tr("예) 전류 +20%: I_peak_max_A=720\n방전 250 kW: discharge_power_max_W=250000",
                                         "e.g. current +20%: I_peak_max_A=720\ndischarge 250 kW: "
                                         "discharge_power_max_W=250000"))
        self.cands.setFixedHeight(84)
        gl.addWidget(self.cands)
        gl.addWidget(hint(tr("파라미터: ", "parameters: ") + ", ".join(
            f"{k} ({kind}, {unit})" for k, (kind, unit, _t) in PARAMETERS.items() if k != "Vdc_V")
            + tr(" — Vdc는 각 요구의 조건이라 후보가 아닙니다. diagnostic은 설계 손잡이가 아닙니다.",
                 " - Vdc is a condition of each requirement, not a candidate; 'diagnostic' is not a design knob.")))
        v.addWidget(g)
        self.run_btn = primary_button(tr("요구 묶음 판정", "judge the requirement set"))
        self.run_btn.clicked.connect(self.run)
        v.addWidget(self.run_btn)
        v.addWidget(ConceptNote(tr(
            "<b>요구 묶음</b>: 모든 요구를 같은 제품 데이터·조건·근거로 판정하고, 요구마다 판정·여유·제한 원인과 함께 "
            "<b>UNKNOWN의 원인 분류</b>(입력 결측 / 적용성 미확인 / 연속 범위 미입증 / 모델 범위 밖 / 수치 미해결 / 근거 충돌 / "
            "정책 한계)와 결론을 바꿀 수 있는 다음 자료를 보여줍니다. 후보는 모든 요구에 대해 다시 판정하며, 한 요구의 개선이 다른 "
            "요구를 악화시키면 그대로 보입니다 — 가중 점수로 상쇄하지 않고, 비용 자료 없이 비용 최적이라고 하지 않습니다.",
            "<b>Requirement set</b>: every requirement is judged on the same product data, conditions and evidence; "
            "each row shows the verdict, margin, limiting cause, the <b>class of an open answer</b> (missing input / "
            "applicability unconfirmed / continuous range not proven / outside the model / numerically open / "
            "conflicting evidence / policy limitation) and the next data that could change it. Candidates are re-judged "
            "against every requirement; an improvement that breaks another requirement is shown as such - no weighted "
            "score, no cost optimum without cost data.")))
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(460)
        split.addWidget(sc)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel(tr("요구를 입력하고 판정하세요.", "enter the requirements and judge them."))
        self.summary.setWordWrap(True)
        rv.addWidget(self.summary)
        self.tabs = QTabWidget()
        res = QSplitter(Qt.Vertical)
        self.res_table = QTableWidget(0, 7)
        self.res_table.setHorizontalHeaderLabels(["ID", tr("판정", "verdict"), tr("분류", "class"),
                                                  tr("여유 [N·m]", "margin [N·m]"),
                                                  tr("사양 필요조건", "model-free check"),
                                                  tr("제한 원인", "limiting cause"), tr("다음 자료", "next data")])
        self.res_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.res_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.res_table.setWordWrap(False)             # one line per requirement; the full text is in the tooltip
        self.res_table.setTextElideMode(Qt.ElideRight)
        hh = self.res_table.horizontalHeader()
        for j in range(5):
            hh.setSectionResizeMode(j, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        self.res_table.itemSelectionChanged.connect(self._show_detail)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        res.addWidget(self.res_table)
        res.addWidget(self.detail)
        res.setSizes([440, 240])
        self.cand_table = QTableWidget(0, 0)
        self.cand_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.prio_table = QTableWidget(0, 6)
        self.prio_table.setHorizontalHeaderLabels([tr("원인 분류", "class"), tr("작업 종류", "kind of work"),
                                                   tr("관련 요구", "requirements"),
                                                   tr("이것만으로 확정", "settled by this alone"),
                                                   tr("추가로 필요한 것", "also needed"), tr("방법", "how")])
        self.prio_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.prio_table.setWordWrap(True)
        ph = self.prio_table.horizontalHeader()
        for j in range(5):
            ph.setSectionResizeMode(j, QHeaderView.ResizeToContents)
        ph.setSectionResizeMode(5, QHeaderView.Stretch)
        self.tabs.addTab(res, tr("판정 요약", "verdicts"))
        self.tabs.addTab(self.prio_table, tr("다음 자료 우선순위", "next-data priorities"))
        self.tabs.addTab(self.cand_table, tr("후보 × 요구", "candidates x requirements"))
        rv.addWidget(self.tabs, 1)
        br = QHBoxLayout()
        self.open_btn = QPushButton(tr("선택한 요구를 판정 페이지에서 열기", "open the selected requirement on the decision page"))
        self.open_btn.setToolTip(tr("같은 요구를 판정 페이지에서 계산해 그래프·4층 판정·보고서를 봅니다",
                                    "evaluates the same requirement on the decision page: graphs, four claim layers, "
                                    "reports"))
        self.open_btn.clicked.connect(lambda: self.open_in_decision())
        br.addWidget(self.open_btn)
        br.addStretch(1)
        b = QPushButton(tr("결과 CSV 저장…", "save results CSV…"))
        b.clicked.connect(lambda: self.save_results())
        br.addWidget(b)
        rv.addLayout(br)
        split.addWidget(right)
        split.setSizes([560, 900])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)
        self.fill_example()
        self.win.track_inputs("requirement_set", form)

    # -- inputs ------------------------------------------------------------------------------------------------------
    def fill_example(self):
        import csv as _csv
        import io as _io
        rows = list(_csv.reader(_io.StringIO(RS.CSV_TEMPLATE)))[1:]
        self.table.load([[c for c in r] for r in rows])

    def import_csv(self, path: str | None = None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, tr("요구 CSV", "requirements CSV"), "", "CSV (*.csv)")
            if not path:
                return
        import csv as _csv
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
            RS.parse_requirements_csv(text, self.win.state.drive.motor.pole_pairs)        # validate first
            rows = list(_csv.DictReader(text.splitlines()))
            self.table.load([[r.get(c, "") or "" for c in RS.CSV_COLUMNS] for r in rows])
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("CSV 가져오기 실패", "could not import the CSV"), str(exc))

    def save_template(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("CSV 템플릿", "CSV template"), "requirements.csv", "CSV (*.csv)")
        if path:
            Path(path).write_text(RS.CSV_TEMPLATE, encoding="utf-8")

    def _rows(self) -> list:
        rows = []
        for i in range(self.table.rowCount()):
            cells = [(self.table.item(i, j).text().strip() if self.table.item(i, j) else "")
                     for j in range(self.table.columnCount())]
            if any(cells):
                rows.append(cells)
        if not rows:
            raise ValueError(tr("요구가 없습니다", "no requirement"))
        return rows

    # -- run -----------------------------------------------------------------------------------------------------------
    def run(self):
        try:
            rows = self._rows()
            cands = parse_candidates(self.cands.toPlainText())
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        s = self.win.state
        self.run_btn.setEnabled(False)
        self.win.runner.run("requirement_set", tr("요구 묶음 판정", "requirement set"), _task, self._show, rows, s.drive,
                            s.limits, cands, on_error=self._err)

    def _err(self, msg, tb):
        self.run_btn.setEnabled(True)
        error_box(self, tr("판정 실패", "failed"), msg, tb)

    def _show(self, res):
        self.run_btn.setEnabled(True)
        self.result = res
        st = res["set"]
        sm = st["summary"]
        by = ", ".join(f"{_class_label(k)} {n}" for k, n in sm["by_class"].items() if n)
        self.summary.setText(tr(f"<b>{sm['total']}건</b> · PASS {sm['PASS']} · FAIL {sm['FAIL']} · UNKNOWN {sm['UNKNOWN']} "
                                f"· 분류: {by} · 모델 {st['drive']['drive_id']} ({st['drive']['origin']}, "
                                f"{st['drive']['fidelity']}) — 모델 판정이며 제품 qualification은 별도 층입니다",
                                f"<b>{sm['total']}</b> · PASS {sm['PASS']} · FAIL {sm['FAIL']} · UNKNOWN {sm['UNKNOWN']} "
                                f"· classes: {by} · model {st['drive']['drive_id']} ({st['drive']['origin']}, "
                                f"{st['drive']['fidelity']}) - model verdicts; product qualification is a separate layer"))
        rows = st["rows"]
        self.res_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            label = tr(r["class_label_ko"], r["class_label_en"])
            if len(r.get("classes") or []) > 1:
                label += tr(f" 외 {len(r['classes']) - 1}", f" +{len(r['classes']) - 1}")
            spec = r.get("spec_check") or {}
            cells = (r["id"], r["verdict"], label, fmt(r["margin_Nm"]), _pair(SPEC, spec.get("status")),
                     r["limiting"] or "—", " · ".join(r["next_data"][:2]) or "—")
            tips = {4: spec.get("text", ""), 2: ", ".join(r.get("classes") or [])}
            for j, c in enumerate(cells):
                it = QTableWidgetItem(c)
                it.setToolTip(tips.get(j) or c)
                if j == 1:
                    it.setForeground(QColor(VERDICT_COLOR.get(r["verdict"], "#57606a")))
                if j == 4 and spec.get("status") == "violated":
                    it.setForeground(QColor(VERDICT_COLOR["FAIL"]))
                self.res_table.setItem(i, j, it)
        if rows:
            self.res_table.selectRow(0)
        self._fill_priorities(st.get("priorities") or [])
        self._fill_candidates(res.get("candidates"))

    def _fill_priorities(self, prio):
        t = self.prio_table
        if not prio:
            t.setRowCount(1)
            t.setItem(0, 0, QTableWidgetItem(tr("UNKNOWN 요구가 없습니다.", "no UNKNOWN requirement.")))
            for j in range(1, 6):
                t.setItem(0, j, QTableWidgetItem(""))
            return
        t.setRowCount(len(prio))
        for i, e in enumerate(prio):
            also = "; ".join(f"{k}: {', '.join(v)}" for k, v in e["also_needs"].items()) or "—"
            cells = (tr(e["label_ko"], e["label_en"]), _pair(EFFORT, e["effort"]), ", ".join(e["requirements"]),
                     ", ".join(e["settles"]) or "—", also, e["hint"])
            for j, c in enumerate(cells):
                it = QTableWidgetItem(c)
                it.setToolTip(c)
                t.setItem(i, j, it)
        t.resizeRowsToContents()

    def check_reading(self):
        """Condition and operator check before any calculation: how every row is read (port, torque definition,
        speed kind, Vdc quantifier, operator meaning, duration) and the model-free necessary condition."""
        import csv as _csv
        import io as _io
        try:
            buf = _io.StringIO()
            w = _csv.writer(buf)
            w.writerow(RS.CSV_COLUMNS)
            for r in self._rows():
                w.writerow(r)
            reqs = RS.parse_requirements_csv(buf.getvalue(), self.win.state.drive.motor.pole_pairs)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return None
        lines = [tr("해석 확인 — 계산 전, 각 요구를 이렇게 읽습니다:", "reading check - before any calculation, each "
                                                              "requirement is read as:"), ""]
        for q in reqs:
            it = RS.interpretation(q)
            sc = RS.spec_check(q, self.win.state.limits)
            lines += [f"{q.req_id} — {q.text}", f"  · {it['torque']}", f"  · {it['operator']}",
                      f"  · {it['speed']}; {it['Vdc']}", f"  · {it['duration']}", f"  · {it['temperatures']}",
                      f"  · {tr('사양 필요조건', 'model-free check')}: {_pair(SPEC, sc['status'])} — {sc['text']}", ""]
        lines.append(tr("각 요구의 토크와 속도는 같은 운전점의 값으로만 씁니다(한 요구의 최대 토크와 다른 요구의 최대 속도를 곱하지 "
                        "않음).", "each requirement's torque and speed are used as ONE operating point (never the "
                                  "maximum torque of one requirement times the maximum speed of another)."))
        self.tabs.setCurrentIndex(0)
        self.detail.setPlainText("\n".join(lines))
        return reqs

    def _fill_candidates(self, cands):
        t = self.cand_table
        rows = self.result["set"]["rows"]
        if not cands:
            t.setRowCount(1)
            t.setColumnCount(1)
            t.setHorizontalHeaderLabels([tr("후보", "candidates")])
            t.setItem(0, 0, QTableWidgetItem(tr("후보를 입력하면 모든 요구를 후보마다 다시 판정합니다.",
                                                "enter candidates to re-judge every requirement per candidate.")))
            t.horizontalHeader().setStretchLastSection(True)
            return
        cs = cands["candidates"]
        t.setColumnCount(1 + len(cs))
        t.setHorizontalHeaderLabels([tr("기준 (현재 설계)", "baseline (current design)")] + [
            c["name"] + (tr(" [진단용]", " [diagnostic]") if c["diagnostic_only"] else "") for c in cs])
        t.setRowCount(len(rows) + 3)
        t.setVerticalHeaderLabels([r["id"] for r in rows] + [tr("개선", "improves"), tr("악화", "worsens"),
                                                             tr("전부 만족", "all met")])
        for i, r in enumerate(rows):
            it = QTableWidgetItem(r["verdict"])
            it.setForeground(QColor(VERDICT_COLOR.get(r["verdict"], "#57606a")))
            t.setItem(i, 0, it)
            for j, c in enumerate(cs, start=1):
                vv = c["rows"][i]["verdict"]
                it = QTableWidgetItem(vv + (" ▲" if r["id"] in c["improves"] else " ▼" if r["id"] in c["worsens"]
                                            else ""))
                it.setForeground(QColor(VERDICT_COLOR.get(vv, "#57606a")))
                t.setItem(i, j, it)
        n = len(rows)
        base_all = all(r["verdict"] == "PASS" for r in rows)
        t.setItem(n + 2, 0, QTableWidgetItem(tr("예", "yes") if base_all else tr("아니오", "no")))
        for j, c in enumerate(cs, start=1):
            t.setItem(n, j, QTableWidgetItem(", ".join(c["improves"]) or "—"))
            t.setItem(n + 1, j, QTableWidgetItem(", ".join(c["worsens"]) or "—"))
            t.setItem(n + 2, j, QTableWidgetItem(tr("예", "yes") if c["all_met"] else tr("아니오", "no")))
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)

    def _show_detail(self):
        """Review order: requirement -> conditions and data level -> conclusion and margin -> limiting cause ->
        what could be changed -> next data -> details (the four claim layers stay separate)."""
        if self.result is None:
            return
        sel = self.res_table.selectionModel().selectedRows()
        if not sel:
            return
        i = sel[0].row()
        st = self.result["set"]
        r = st["rows"][i]
        rec = st["records"][i]
        it = r["interpretation"]
        L = [f"1. {tr('요구', 'requirement')}: {r['id']} — {r['text']}",
             f"   {it['torque']}; {it['operator']}", "",
             f"2. {tr('조건·데이터 수준', 'conditions and data level')}: {it['speed']}; {it['Vdc']}; {it['duration']}; "
             f"{it['temperatures']}",
             f"   {tr('모델', 'model')} {st['drive']['drive_id']} rev {st['drive']['revision']} ({st['drive']['origin']}, "
             f"{st['drive']['fidelity']}) · {r['conditions']} {tr('개 조건 평가', 'condition(s) evaluated')} · "
             f"qualification: {r['qualification']}", "",
             f"3. {tr('결론·여유', 'conclusion and margin')}: {r['verdict']} ({r['status']}) · "
             f"{tr(r['class_label_ko'], r['class_label_en'])}"
             + (f" · {tr('여유', 'margin')} {fmt(r['margin_Nm'])} N·m" if r["margin_Nm"] is not None else "")
             + (f" · reasons: {', '.join(r['reasons'])}" if r["reasons"] else "")]
        if r.get("decided_by"):
            L.append(f"   {tr('결정 근거', 'decided by')}: {r['decided_by']} ({tr('모델 판정', 'model verdict')} "
                     f"{r['model_verdict']}: {', '.join(r['model_reasons'])})")
        sc = r.get("spec_check") or {}
        if sc:
            L.append(f"   {tr('사양 필요조건', 'model-free check')}: {_pair(SPEC, sc['status'])} — {sc['text']}")
        L += ["", f"4. {tr('제한 원인', 'limiting cause')}:"] + [f"   - {q}" for q in (r["limiting_all"] or ["—"])]
        L += ["", f"5. {tr('바꿀 수 있는 항목 (후보는 모든 요구로 다시 판정)', 'what could change (a candidate is re-judged against every requirement)')}:"]
        L += [f"   - {x['parameter']} ({x['change_kind']}, {x['unit']}) — {x['meaning']} [{x['constraint']}]"
              for x in r["levers"]] or ["   —"]
        L += ["", f"6. {tr('다음 자료 (결론을 바꿀 수 있는 것)', 'next data (what could change the answer)')}:"]
        L += [f"   - {q}" for q in r["next_data"]] or ["   —"]
        if r["open_items"]:
            L += [f"   {tr('요구 층 미결', 'requirement-layer open items')}:"] + [f"   - {q}" for q in r["open_items"]]
        L += ["", f"7. {tr('상세', 'details')}: scope: {rec.verdict_scope}"]
        if rec.qualifiers:
            L += [f"   {tr('한정', 'qualifiers')}:"] + [f"   - {q}" for q in rec.qualifiers]
        L += [f"   record {r['record_id']} — {tr('판정 페이지에서 같은 요구를 열면 그래프와 4층 판정을 봅니다', 'open the same requirement on the decision page for the graphs and the four claim layers')}"]
        self.detail.setPlainText("\n".join(L))

    def open_in_decision(self, index: int | None = None):
        """The selected row as a case (the same requirement dict, drive and limits) on the decision page."""
        if self.result is None:
            return None
        if index is None:
            sel = self.res_table.selectionModel().selectedRows()
            if not sel:
                return None
            index = sel[0].row()
        from ... import api
        from .decision import _evaluate_task
        st = self.win.state
        case = api.case_from_body(st.body(requirement={}))
        case["requirement"] = dict(self.result["req_dicts"][index])
        page = self.win.pages["decision"]
        page.show_requirement(self.result["set"]["records"][index].requirement)
        self.win.show_page("decision")
        self.win.runner.run("decision", case["requirement"]["id"], _evaluate_task, page._show, case, [],
                            on_error=page._failed)
        return case

    def save_results(self, path: str | None = None):
        if self.result is None:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, tr("결과 CSV", "results CSV"), "requirement_set.csv",
                                                  "CSV (*.csv)")
            if not path:
                return None
        Path(path).write_text(RS.rows_to_csv(self.result["set"]["rows"]), encoding="utf-8")
        return path

    def redraw(self):
        pass
