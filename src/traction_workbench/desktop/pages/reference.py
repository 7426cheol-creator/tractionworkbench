"""Reference verification: a requirement / scenario package (a customer specification, a study reference or the
built-in example) verified item by item on the product - the requirement-to-evidence matrix with PASS / FAIL /
UNKNOWN / CONFLICT / MANUAL, the evidence of every check on one clock, the OPEN parameters with the items that wait for
them, the conflicts kept as variants, the safe-state feasibility map, HTML and CSV exports."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import api
from ...extensions.faultsim.refhier import LEVELS, REQ_LEVELS, hierarchy, level_name, level_of
from ...i18n import tr
from ...plots import reference_figures as RF
from ..widgets import Cell, ConceptNote, KeyValueTable, PlotPanel, combo, error_box, hint, primary_button

VCOLOR = {"PASS": "#1a7f37", "FAIL": "#cf222e", "UNKNOWN": "#b7791f", "CONFLICT": "#8250df", "MANUAL": "#0969da",
          "NOT_APPLICABLE": "#8c959f"}
VKO = {"PASS": "PASS", "FAIL": "FAIL", "UNKNOWN": "UNKNOWN", "CONFLICT": "CONFLICT", "MANUAL": "MANUAL",
       "NOT_APPLICABLE": "N/A"}

NOTE = lambda: tr(  # noqa: E731
    "<b>기능안전 요구 검증</b>: 요구·시나리오 패키지(고객 사양, 스터디 참고 문서, 또는 내장 예제)의 항목을 이 제품 모델에서 "
    "하나씩 판정합니다. 근거 수준(CONFIRMED / CUSTOMER-PAST / PROJECT / DERIVED / RESEARCH / OPEN / CONFLICT)을 그대로 "
    "유지하며, <b>OPEN 값은 추정하지 않습니다</b> - 그 값이 필요한 판정은 UNKNOWN이 되고 어떤 값이 필요한지 이름을 남깁니다. "
    "예시 값으로 보고 싶다면 '예시(illustrative)' 프로파일을 고르세요: 예시 값을 쓴 결과는 모두 ILL로 표시되고 고객 판정과 "
    "섞이지 않습니다. 기록이 충돌하는 항목은 변형(variant)으로 둘 다 실행하며, 확정된 두 판정이 다를 때만 CONFLICT입니다. "
    "안전 상태는 명령 비트가 아니라 <b>물리 결과</b>(C1-C4: 토크·DC 전력 방향·능동 펄싱·TLSR)로 판정합니다.<br>"
    "모든 시뮬레이션 판정은 이 앱에 불러온 <b>제품 모델</b>에 대한 것입니다 (기본은 합성 예제 제품).",
    "<b>Reference verification</b>: the items of a requirement / scenario package (a customer specification, a study "
    "reference or the built-in example) judged one by one on this product model.  The provenance tags (CONFIRMED / "
    "CUSTOMER-PAST / PROJECT / DERIVED / RESEARCH / OPEN / CONFLICT) are kept, and <b>OPEN values are never "
    "guessed</b>: a verdict that needs one is UNKNOWN and names it.  Choose the 'illustrative' profile to see example "
    "values: every result that used one is marked ILL and kept apart from the customer verdicts.  Conflicting records "
    "run as variants; CONFLICT only when two definite verdicts differ.  The safe state is judged on the <b>physical "
    "outcome</b> (C1-C4: torque, DC power direction, active pulsing, the TLSR band), never on a command bit.<br>"
    "Every simulation verdict is about the <b>product model</b> loaded in the application (by default the synthetic "
    "example product).")


def _task(progress, body, project):
    progress(0.0, tr("기능안전 요구 검증", "reference verification"))
    return api.reference_run(body, project, progress=progress)


def parse_value(text: str):
    """A parameter value as entered: empty (no value), a number, or JSON (a list, a map, a string)."""
    t = text.strip()
    if not t:
        return None
    try:
        return float(t) if t.replace(".", "", 1).replace("-", "", 1).replace("e", "", 1).isdigit() else json.loads(t)
    except (ValueError, json.JSONDecodeError):
        return t


def value_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return str(v)


class ReferencePage(QWidget):
    workspace_data = ()

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.package = api.reference_load(api.reference_example())
        self.source = tr("내장 예제 패키지", "built-in example package")
        self.values: dict = {}
        self.last = None
        self._rows_shown: list = []
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("패키지", "package"))
        f = QVBoxLayout(g)
        self.pkg_label = QLabel("")
        self.pkg_label.setWordWrap(True)
        f.addWidget(self.pkg_label)
        row = QHBoxLayout()
        self.builtin = QComboBox()
        for key, ko, en in api.reference_builtin():
            self.builtin.addItem(tr(ko, en), key)
        self.builtin.setToolTip(tr("앱에 들어 있는 패키지: 중립 예제, 고객 인버터 FuSa 참고 문서(계층 분류·추적·제안 포함)",
                                   "packages shipped with the application: the neutral example and the customer "
                                   "inverter FuSa reference (classified, traced, with proposals)"))
        f.addWidget(self.builtin)
        self.example_btn = QPushButton(tr("내장 패키지 불러오기", "load the built-in package"))
        self.example_btn.clicked.connect(lambda: self.load_builtin(self.builtin.currentData()))
        self.open_btn = QPushButton(tr("파일 불러오기…", "open file…"))
        self.open_btn.clicked.connect(self.open_package)
        self.save_pkg_btn = QPushButton(tr("패키지 저장…", "save package…"))
        self.save_pkg_btn.setToolTip(tr("지금 패키지를 JSON으로 저장 (고객 패키지의 틀로 쓰기)",
                                        "save the current package as JSON (a template for a customer package)"))
        self.save_pkg_btn.clicked.connect(self.save_package)
        for b in (self.example_btn, self.open_btn, self.save_pkg_btn):
            row.addWidget(b)
        f.addLayout(row)
        v.addWidget(g)
        g = QGroupBox(tr("판정 프로파일", "profile"))
        fl = QFormLayout(g)
        self.profile = combo([(tr("고객 (OPEN은 UNKNOWN)", "customer (OPEN stays UNKNOWN)"), "customer"),
                              (tr("예시 (illustrative, 결과에 ILL 표시)", "illustrative (results marked ILL)"),
                               "illustrative")])
        fl.addRow(self.profile)
        fl.addRow(hint(tr("파라미터 값은 '파라미터 레지스트리' 탭에서 입력합니다 (입력 값은 USER 근거로 표시).",
                          "Enter parameter values in the 'parameter registry' tab (entered values show as USER).")))
        v.addWidget(g)
        g = QGroupBox(tr("항목 선택", "items"))
        fl = QFormLayout(g)
        self.group = QComboBox()
        self.group.currentIndexChanged.connect(self._fill_matrix)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("ID·제목 검색", "search id / title"))
        self.search.textChanged.connect(self._fill_matrix)
        self.only_customer = combo([(tr("모든 항목", "all items"), "all"),
                                    (tr("고객 요구만", "customer requirements only"), "customer"),
                                    (tr("내부 (DERIVED 등)만", "internal only"), "internal")])
        self.only_customer.currentIndexChanged.connect(self._fill_matrix)
        self.level = QComboBox()
        self.level.addItem(tr("모든 레벨", "all levels"), None)
        self.level.addItem(tr("요구만 (SG·TLSR·FSR·TSR·SM·검증)", "requirements only (SG … verification)"), "REQ")
        for lv in LEVELS:
            self.level.addItem(level_name(lv), lv)
        self.level.currentIndexChanged.connect(self._fill_matrix)
        fl.addRow(tr("그룹", "group"), self.group)
        fl.addRow(tr("레벨", "level"), self.level)
        fl.addRow(tr("검색", "search"), self.search)
        fl.addRow(tr("근거", "provenance"), self.only_customer)
        v.addWidget(g)
        self.product_label = hint("")
        v.addWidget(self.product_label)
        self.run_btn = primary_button(tr("표시된 항목 검증 실행", "verify the listed items"))
        self.run_btn.clicked.connect(lambda: self.run(selected=False))
        self.run_sel_btn = QPushButton(tr("선택한 항목만 실행", "verify the selected items"))
        self.run_sel_btn.clicked.connect(lambda: self.run(selected=True))
        v.addWidget(self.run_btn)
        v.addWidget(self.run_sel_btn)
        row = QHBoxLayout()
        self.html_btn = QPushButton(tr("HTML 보고서…", "HTML report…"))
        self.html_btn.clicked.connect(self.export_html)
        self.csv_btn = QPushButton(tr("매트릭스 CSV…", "matrix CSV…"))
        self.csv_btn.clicked.connect(self.export_csv)
        for b in (self.html_btn, self.csv_btn):
            b.setEnabled(False)
            row.addWidget(b)
        v.addLayout(row)
        self.state = hint("")
        self.state.setWordWrap(True)
        v.addWidget(self.state)
        v.addStretch(1)
        split.addWidget(form)
        # ---- results
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(ConceptNote(NOTE()))
        self.tabs = QTabWidget()
        # matrix
        mw = QWidget()
        ml = QVBoxLayout(mw)
        msplit = QSplitter(Qt.Vertical)
        self.matrix = QTableWidget()
        heads = [tr("ID", "ID"), tr("판정", "verdict"), tr("레벨", "level"), tr("근거", "provenance"), tr("고객", "customer"),
                 "ASIL",
                 tr("합의", "agreement"), tr("그룹", "group"), tr("종류", "kind"), tr("제목", "title"),
                 tr("OPEN 값", "OPEN values"), tr("시나리오", "scenarios")]
        self.matrix.setColumnCount(len(heads))
        self.matrix.setHorizontalHeaderLabels(heads)
        self.matrix.verticalHeader().setVisible(False)
        self.matrix.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.matrix.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.matrix.setAlternatingRowColors(True)
        hh = self.matrix.horizontalHeader()
        for j in range(len(heads)):
            hh.setSectionResizeMode(j, QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        self.matrix.itemSelectionChanged.connect(self._item_selected)
        msplit.addWidget(self.matrix)
        self.checks = KeyValueTable(headers=[tr("판정", "verdict"), tr("검사", "check"), tr("시나리오 / 변형", "scenario / variant"),
                                             tr("기대", "expected"), tr("근거·측정", "reasons · measured")])
        self.checks.itemSelectionChanged.connect(self._check_selected)
        msplit.addWidget(self.checks)
        self.item_text = QPlainTextEdit()
        self.item_text.setReadOnly(True)
        msplit.addWidget(self.item_text)
        msplit.setSizes([420, 220, 120])
        ml.addWidget(msplit)
        self.tabs.addTab(mw, tr("요구-증거 매트릭스", "requirement-to-evidence matrix"))
        # hierarchy: SG -> TLSR -> FSR -> TSR -> SM -> verification, the gaps and the proposals
        hw = QWidget()
        hl = QVBoxLayout(hw)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(hint(tr(
            "레벨과 추적은 패키지에 적힌 대로입니다: 문서가 인용한 번호로 이은 추적은 실선, 주제로 <i>추론</i>한 연결은 '(추론)'으로 "
            "표시합니다(검토용). 판정 = 항목 자체, 하위 = 아래 모든 요구의 최악(FAIL > CONFLICT > UNKNOWN > PASS). "
            "'제안'은 시뮬레이션이 빠졌다고 보여 준 DERIVED 추가 요구로, 고객 요구로 세지 않습니다.",
            "Levels and traces as the package states them: a trace by a number the source cites is plain, a link "
            "<i>inferred</i> by topic is marked '(inferred)' (for review). Verdict = the item itself, subtree = the worst "
            "of every requirement below (FAIL > CONFLICT > UNKNOWN > PASS). A 'proposal' is a DERIVED addition the "
            "simulation shows is missing: never counted as a customer requirement.")))
        hsplit = QSplitter(Qt.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("ID", "ID"), tr("레벨", "level"), tr("판정", "verdict"), tr("하위", "subtree"),
                                   tr("근거", "provenance"), tr("제목", "title")])
        self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        hsplit.addWidget(self.tree)
        self.t_gaps = KeyValueTable(headers=[tr("항목", "item"), tr("레벨", "level"), tr("공백", "gap"),
                                             tr("다루는 제안", "addressed by")])
        hsplit.addWidget(self.t_gaps)
        self.t_props = KeyValueTable(headers=[tr("제안", "proposal"), tr("판정", "verdict"), tr("레벨", "level"),
                                              tr("연결", "traces to"), tr("내용·근거", "text · rationale")])
        for t in (self.t_gaps, self.t_props):           # one line a row (the full text in the tooltip): a long
            t.setWordWrap(False)                         # rationale would make one row fill the table
        hsplit.addWidget(self.t_props)
        hsplit.setSizes([520, 160, 160])
        hl.addWidget(hsplit, 1)
        self.tabs.addTab(hw, tr("계층·추적 (SG→TSR→SM)", "hierarchy · traces (SG→TSR→SM)"))
        self.tab_hier = hw
        # evidence
        self.p_ev = PlotPanel(hint=tr("매트릭스에서 항목과 검사를 고르면 그 검사가 본 궤적이 여기에 그려집니다.",
                                      "Pick an item and a check in the matrix to see the trajectory it judged."),
                              min_height=460)
        self.tabs.addTab(self.p_ev, tr("증거 파형·타임라인", "evidence · timeline"))
        self.p_tw = PlotPanel(hint=tr("토크창 감시가 있는 검사를 고르면 대시보드가 그려집니다.",
                                      "Pick a check with a torque-window monitor to see the dashboard."))
        self.tabs.addTab(self.p_tw, tr("토크창 대시보드", "torque-window dashboard"))
        # summary
        sw = QWidget()
        sl = QVBoxLayout(sw)
        self.p_sum = PlotPanel(hint=tr("실행하면 그룹별 판정 분포가 표시됩니다.", "Run to see the verdicts per group."))
        sl.addWidget(self.p_sum, 1)
        self.t_counts = KeyValueTable(headers=[tr("집합", "set")] + [VKO[x] for x in RF.VORDER] + [tr("합계", "total")],
                                      fit_rows=True)
        sl.addWidget(self.t_counts)
        self.tabs.addTab(sw, tr("요약", "summary"))
        # unknown / conflict
        uw = QWidget()
        ul = QVBoxLayout(uw)
        ul.addWidget(hint(tr("고객 근거가 없어 판정할 수 없는 값: FAIL이 아니라 UNKNOWN으로 두고, 각 값을 기다리는 항목 수로 "
                             "우선순위를 봅니다.", "Values the source does not give: UNKNOWN, never FAIL - ranked by "
                                                 "the number of items waiting for each.")))
        self.t_open = KeyValueTable(headers=[tr("파라미터", "parameter"), tr("근거", "provenance"), tr("단위", "unit"),
                                             tr("기다리는 항목", "items waiting"), tr("설명", "note")])
        ul.addWidget(self.t_open, 2)
        self.t_conf = KeyValueTable(headers=[tr("항목", "item"), tr("검사", "check"), tr("변형별 판정", "per variant")])
        ul.addWidget(QLabel(tr("CONFLICT (변형 간 확정 판정이 다름)", "CONFLICT (variants give different definite "
                                                                  "verdicts)")))
        ul.addWidget(self.t_conf, 1)
        self.t_man = KeyValueTable(headers=[tr("항목", "item"), tr("기록할 근거", "evidence to record")])
        ul.addWidget(QLabel(tr("MANUAL (조직·문서 근거를 기록할 항목)", "MANUAL (organisational evidence to record)")))
        ul.addWidget(self.t_man, 1)
        self.tabs.addTab(uw, tr("UNKNOWN·CONFLICT 보고", "unknown · conflict report"))
        # parameters
        pw = QWidget()
        pl = QVBoxLayout(pw)
        pl.addWidget(hint(tr("값 칸에 입력하면 이번 실행에 USER 근거로 쓰입니다 (비우면 패키지 값 / OPEN). 목록·맵은 JSON "
                             "(예: [[0, 5], [6000, 7.5]]).", "A value entered here is used for this run as USER "
                                                             "(empty: the package value / OPEN). Lists and maps as "
                                                             "JSON (e.g. [[0, 5], [6000, 7.5]]).")))
        self.params = QTableWidget()
        pheads = ["ID", tr("단위", "unit"), tr("근거", "provenance"), tr("패키지 값", "package value"),
                  tr("입력 값 (USER)", "entered value (USER)"), tr("예시 값", "illustrative"), tr("설명", "note")]
        self.params.setColumnCount(len(pheads))
        self.params.setHorizontalHeaderLabels(pheads)
        self.params.verticalHeader().setVisible(False)
        self.params.horizontalHeader().setStretchLastSection(True)
        self.params.itemChanged.connect(self._param_edited)
        pl.addWidget(self.params)
        self.clear_btn = QPushButton(tr("입력 값 모두 지우기", "clear the entered values"))
        self.clear_btn.clicked.connect(self._clear_values)
        pl.addWidget(self.clear_btn)
        self.tabs.addTab(pw, tr("파라미터 레지스트리", "parameter registry"))
        self.p_map = PlotPanel(hint=tr("타당성 지도 검사가 있는 항목을 실행하면 여기에 그려집니다.",
                                       "Run an item with a feasibility check to see the map."))
        self.tabs.addTab(self.p_map, tr("안전상태 타당성 지도", "safe-state feasibility map"))
        self.problems = QPlainTextEdit()
        self.problems.setReadOnly(True)
        self.tabs.addTab(self.problems, tr("패키지 정보·경고", "package notes"))
        rv.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setSizes([330, 1000])
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        self._package_changed()

    # ------------------------------------------------------------------ package
    def _package_changed(self):
        pk = self.package
        meta = pk.get("meta") or {}
        items = pk.get("items") or []
        probs = pk.get("_problems") or []
        self.pkg_label.setText(f"<b>{meta.get('title', '-')}</b><br>{self.source}<br>"
                               + tr(f"항목 {len(items)}개 · 시나리오 {len(pk.get('scenarios') or {})}개 · 파라미터 "
                                    f"{len(pk.get('parameters') or [])}개 · 경고 {len(probs)}",
                                    f"{len(items)} items · {len(pk.get('scenarios') or {})} scenarios · "
                                    f"{len(pk.get('parameters') or [])} parameters · {len(probs)} warnings"))
        groups = []
        for it in items:
            if it.get("group", "") not in groups:
                groups.append(it.get("group", ""))
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem(tr("모든 그룹", "all groups"), None)
        for gname in groups:
            self.group.addItem(gname, gname)
        self.group.blockSignals(False)
        self.values = {}
        self.last = None
        self._fill_params()
        self._fill_matrix()
        self._fill_hierarchy()
        for b in (self.html_btn, self.csv_btn):
            b.setEnabled(False)
        self.problems.setPlainText("\n".join([json.dumps(meta, ensure_ascii=False, indent=1)] +
                                             [f"[{p['level']}] {p['text']}" for p in probs]))
        pr = self.win.state.project
        self.product_label.setText(tr(f"제품 모델: {pr.label} ({pr.digest()[:12]})",
                                      f"product model: {pr.label} ({pr.digest()[:12]})"))

    def load_example(self):
        self.load_builtin("example")

    def load_builtin(self, key: str | None):
        key = key or "example"
        self.package = api.reference_load(api.reference_builtin_package(key))
        name = next((tr(ko, en) for k, ko, en in api.reference_builtin() if k == key), key)
        self.source = tr(f"내장 패키지: {name}", f"built-in package: {name}")
        self.builtin.setCurrentIndex(max(0, self.builtin.findData(key)))
        self._package_changed()

    def open_package(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, tr("참고 패키지 열기", "open a reference package"), "",
                                                  "JSON (*.json)")
        if not path:
            return
        try:
            self.package = api.reference_load(path)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("패키지 오류", "package error"), str(exc))
            return
        self.source = path
        self._package_changed()

    def save_package(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("패키지 저장", "save package"), "reference_package.json",
                                              "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({k: v for k, v in self.package.items() if not k.startswith("_")}, fh, ensure_ascii=False,
                          indent=1)
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("저장 실패", "save failed"), str(exc))
            return
        self.state.setText(tr(f"패키지 저장: {path}", f"package saved: {path}"))

    # ------------------------------------------------------------------ parameters
    def _fill_params(self):
        ps = self.package.get("parameters") or []
        self.params.blockSignals(True)
        self.params.setRowCount(len(ps))
        for i, p in enumerate(ps):
            cells = (p["id"], p.get("unit", ""), p.get("provenance", ""), value_text(p.get("value")),
                     value_text(self.values.get(p["id"])), value_text(p.get("illustrative")), p.get("note", ""))
            for j, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if j != 4:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if j == 2 and text in ("OPEN", "CONFLICT"):
                    it.setForeground(Qt.darkYellow)
                self.params.setItem(i, j, it)
        self.params.resizeColumnsToContents()
        self.params.blockSignals(False)

    def _param_edited(self, item):
        if item.column() != 4:
            return
        pid = self.params.item(item.row(), 0).text()
        v = parse_value(item.text())
        if v is None:
            self.values.pop(pid, None)
        else:
            self.values[pid] = v
        self.state.setText(tr(f"입력 값 {len(self.values)}개 (USER) - 다음 실행에 쓰입니다.",
                              f"{len(self.values)} entered value(s) (USER) - used by the next run."))

    def _clear_values(self):
        self.values = {}
        self._fill_params()
        self.state.setText(tr("입력 값을 모두 지웠습니다.", "entered values cleared"))

    # ------------------------------------------------------------------ matrix
    def _visible_items(self) -> list:
        g = self.group.currentData()
        q = self.search.text().strip().lower()
        which = self.only_customer.currentData()
        cust = set(self.package.get("customer_tags") or ("CONFIRMED", "CUSTOMER-PAST", "PROJECT"))
        out = []
        lvf = self.level.currentData()
        for it in self.package.get("items") or []:
            if g is not None and it.get("group", "") != g:
                continue
            lv = level_of(it)[0]
            if lvf == "REQ" and lv not in REQ_LEVELS or lvf not in (None, "REQ") and lv != lvf:
                continue
            if q and q not in it["id"].lower() and q not in (it.get("title") or "").lower():
                continue
            is_c = it.get("provenance") in cust
            if which == "customer" and not is_c or which == "internal" and is_c:
                continue
            out.append(it)
        return out

    def _fill_matrix(self):
        rows = {r["id"]: r for r in (self.last or {}).get("rows", [])}
        items = self._visible_items()
        self.matrix.clearSelection()                  # the rows change: a kept selection would point elsewhere
        self._rows_shown = [it["id"] for it in items]
        cust = set(self.package.get("customer_tags") or ("CONFIRMED", "CUSTOMER-PAST", "PROJECT"))
        self.matrix.setRowCount(len(items))
        for i, it in enumerate(items):
            r = rows.get(it["id"])
            verdict = "—" if r is None else VKO.get(r["verdict"], r["verdict"]) + (" ILL" if r.get("illustrative") else "")
            cells = (it["id"], verdict, level_of(it)[0] + (" ▸" + tr("제안", "proposal") if it.get("proposed") else ""),
                     it.get("provenance", ""), "●" if it.get("provenance") in cust else "",
                     it.get("asil_literal") or "", it.get("agreement") or "", it.get("group", ""), it.get("kind", ""),
                     it.get("title", ""), ", ".join((r or {}).get("open") or []),
                     ", ".join((r or {}).get("scenarios") or sorted({c.get("scenario") for c in it.get("checks") or []
                                                                     if c.get("scenario")})))
            for j, text in enumerate(cells):
                w = QTableWidgetItem(text)
                w.setToolTip(text)
                if j == 1 and r is not None:
                    from PySide6.QtGui import QColor
                    w.setForeground(QColor(VCOLOR.get(r["verdict"], "#57606a")))
                self.matrix.setItem(i, j, w)
        self.matrix.resizeColumnsToContents()
        for j in range(self.matrix.columnCount()):
            if self.matrix.columnWidth(j) > 360:
                self.matrix.setColumnWidth(j, 360)

    def _flat_checks(self, r) -> list:
        out = []
        for c in r.get("checks") or []:
            if c.get("variants"):
                out.append(c)
                for p in c["variants"]:
                    out.append(dict(p, label=f"{c.get('label') or c.get('check')} · {p.get('variant')}"))
            else:
                out.append(c)
        return out

    def _item_selected(self):
        rows = sorted({i.row() for i in self.matrix.selectedIndexes()})
        if not rows or rows[0] >= len(self._rows_shown):
            return
        iid = self._rows_shown[rows[0]]
        it = next((x for x in self.package.get("items") or [] if x["id"] == iid), {})
        r = next((x for x in (self.last or {}).get("rows", []) if x["id"] == iid), None)
        lines = [f"{iid} · {it.get('title', '')}", f"{it.get('group', '')} · {it.get('kind', '')} · "
                 f"{it.get('provenance', '')}" + (f" · ASIL {it['asil_literal']}" if it.get("asil_literal") else "")
                 + (f" · {it['agreement']}" if it.get("agreement") else "")]
        for k in ("source", "text", "status_note", "sim_note", "done_when", "inputs_needed", "note", "manual"):
            if it.get(k):
                lines.append(f"{k}: {it[k]}")
        self.item_text.setPlainText("\n".join(lines))
        self._checks_shown = [] if r is None else self._flat_checks(r)
        rows_ = []
        cols = {}
        for i, c in enumerate(self._checks_shown):
            where = " / ".join(x for x in (c.get("scenario"), c.get("variant")) if x)
            meas = "; ".join(f"{k}={v}" for k, v in (c.get("measured") or {}).items()
                             if not isinstance(v, (dict, list)))[:200]
            rows_.append((VKO.get(c.get("verdict"), c.get("verdict", "")), c.get("label") or c.get("check", ""),
                          where, c.get("expected", ""), " | ".join(x for x in c.get("reasons") or [] if x)
                          + (f" [{meas}]" if meas else "")))
            cols[(i, 0)] = VCOLOR.get(c.get("verdict"), "#57606a")
        self.checks.set_rows(rows_, colors=cols)
        first = next((i for i, c in enumerate(self._checks_shown) if (c.get("evidence") or {}).get("run")), None)
        if first is not None:
            self._draw_check(self._checks_shown[first])
        fm = next((c.get("evidence", {}).get("map") for c in self._checks_shown if (c.get("evidence") or {}).get("map")),
                  None)
        if fm:
            self.p_map.draw(RF.fig_feasibility_map, fm, title=tr("안전상태 타당성 지도", "safe-state feasibility map"),
                            name="feasibility_map")

    def _check_selected(self):
        rows = sorted({i.row() for i in self.checks.selectedIndexes()})
        if not rows or rows[0] >= len(getattr(self, "_checks_shown", [])):
            return
        self._draw_check(self._checks_shown[rows[0]])

    def _draw_check(self, c):
        key = (c.get("evidence") or {}).get("run")
        run = ((self.last or {}).get("evidence_runs") or {}).get(key)
        if run is None:
            return
        title = f"{c.get('label') or c.get('check')} · {c.get('scenario') or ''} {c.get('variant') or ''}".strip()
        self.p_ev.draw(RF.fig_reference_evidence, run, c, title=title, name="reference_evidence",
                       csv=lambda run=run: run["trace"])
        if "tw_hi" in (run.get("trace") or {}):
            self.p_tw.draw(RF.fig_torque_window, run, title=title, name="torque_window",
                           csv=lambda run=run: run["trace"])

    # ------------------------------------------------------------------ hierarchy
    def _fill_hierarchy(self):
        """The tree SG -> TLSR -> FSR -> TSR -> SM -> verification (with the actions, questions and definitions traced
        to a requirement), the gaps and the proposals; verdicts of the last run where there is one."""
        rows = (self.last or {}).get("rows") or []
        h = (self.last or {}).get("hierarchy") or hierarchy(self.package, rows)
        self.hier = h
        items = {it["id"]: it for it in self.package.get("items") or []}
        verdict = {r["id"]: r["verdict"] for r in rows}
        lv, kids, roll = h["levels"], h["children"], h["rollup"]
        self.tree.clear()
        bold = QFont()
        bold.setBold(True)

        def add(parent, i, basis, seen, depth):
            it = items.get(i, {})
            own = verdict.get(i)
            sub = (roll.get(i) or {}).get("verdict")
            cells = [i + (tr(" (추론)", " (inferred)") if basis == "inferred" else ""), lv.get(i, ""),
                     VKO.get(own, own or "—"), VKO.get(sub, sub or "—") if sub and sub != own else "",
                     it.get("provenance", "") + (tr(" · 제안", " · proposal") if it.get("proposed") else ""),
                     it.get("title", "")]
            node = QTreeWidgetItem(parent, cells)
            node.setData(0, Qt.UserRole, i)
            node.setToolTip(5, it.get("title", ""))
            for col, v in ((2, own), (3, sub)):
                if v:
                    node.setForeground(col, QColor(VCOLOR.get(v, "#57606a")))
            if lv.get(i) not in REQ_LEVELS or basis == "inferred":
                f = QFont()
                f.setItalic(True)
                node.setFont(0, f)
            if lv.get(i) in ("SG", "TLSR"):
                node.setFont(5, bold)
            if depth < 10:
                for c, b in kids.get(i) or []:
                    if c not in seen:
                        add(node, c, b, seen | {i}, depth + 1)
            return node
        for i in h["roots"]:
            if lv.get(i) == "SG":
                add(self.tree, i, "source", set(), 0)
        by_level = {}
        for i in [r for r in h["roots"] if lv.get(r) != "SG"] + list(h.get("loose") or []):
            by_level.setdefault(lv[i], []).append(i)
        for lvl, ids in by_level.items():
            req = lvl in REQ_LEVELS
            grp = QTreeWidgetItem(self.tree, [tr(f"{level_name(lvl)} · {'목표로 추적 안 됨' if req else '연결 없음'} "
                                                 f"({len(ids)})",
                                                 f"{level_name(lvl)} · {'not traced to a goal' if req else 'not traced'}"
                                                 f" ({len(ids)})"), lvl])
            grp.setFont(0, bold)
            for i in ids:
                add(grp, i, "source", set(), 0 if req else 9)
        for k in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(k)
            if lv.get(top.data(0, Qt.UserRole)) in ("SG", "TLSR"):
                top.setExpanded(True)
        for j, w in enumerate((200, 60, 80, 80, 130)):
            self.tree.setColumnWidth(j, w)
        self.t_gaps.set_rows([(g["id"] + (tr(" (고객)", " (customer)") if g.get("customer") else ""), g["level"],
                               g["text"], ", ".join(g.get("proposals") or []) or "—") for g in h.get("gaps") or []])
        prows, cols = [], {}
        for it in items.values():
            if not it.get("proposed"):
                continue
            v = verdict.get(it["id"])
            cols[(len(prows), 1)] = VCOLOR.get(v, "#57606a")
            full = it.get("title", "") + (f" — {it['rationale']}" if it.get("rationale") else "")
            prows.append((it["id"], VKO.get(v, v or "—"), level_of(it)[0],
                          ", ".join((it.get("traces_to") or []) + [x + tr(" (추론)", " (inferred)")
                                                                   for x in it.get("traces_to_inferred") or []]),
                          Cell(full if len(full) <= 200 else full[:197] + "…", full)))
        self.t_props.set_rows(prows, colors=cols)

    def _tree_selected(self):
        sel = self.tree.selectedItems()
        iid = sel[0].data(0, Qt.UserRole) if sel else None
        if iid:
            self.select_item(iid)

    def select_item(self, iid: str):
        """Show an item in the matrix (widening the filters when they hide it) and select it."""
        if iid not in self._rows_shown:
            for w, v in ((self.group, None), (self.level, None)):
                w.blockSignals(True)
                w.setCurrentIndex(max(0, w.findData(v)))
                w.blockSignals(False)
            self.search.blockSignals(True)
            self.search.setText("")
            self.search.blockSignals(False)
            self.only_customer.setCurrentIndex(0)
            self._fill_matrix()
        if iid in self._rows_shown:
            self.matrix.selectRow(self._rows_shown.index(iid))

    # ------------------------------------------------------------------ run
    def run(self, selected: bool = False):
        if selected:
            rows = sorted({i.row() for i in self.matrix.selectedIndexes()})
            ids = [self._rows_shown[r] for r in rows if r < len(self._rows_shown)]
            if not ids:
                error_box(self, tr("입력 오류", "input error"), tr("매트릭스에서 실행할 항목을 고르세요.",
                                                                 "Select the items to verify in the matrix."))
                return
        else:
            ids = list(self._rows_shown)
        body = {"package": self.package, "profile": self.profile.currentData(), "values": dict(self.values),
                "ids": ids}
        for b in (self.run_btn, self.run_sel_btn):
            b.setEnabled(False)
        self.state.setText(tr(f"항목 {len(ids)}개 검증 중…", f"verifying {len(ids)} item(s)…"))
        self.win.runner.run("reference", tr("기능안전 요구 검증", "reference verification"), _task, self._show, body,
                            self.win.state.project, on_error=self._err)

    def _err(self, msg, tb):
        for b in (self.run_btn, self.run_sel_btn):
            b.setEnabled(True)
        if msg == "CANCELLED":
            self.state.setText(tr("취소했습니다.", "cancelled"))
            return
        error_box(self, tr("검증 실패", "verification failed"), msg, tb)

    def _show(self, res):
        for b in (self.run_btn, self.run_sel_btn, self.html_btn, self.csv_btn):
            b.setEnabled(True)
        self.last = res
        self._fill_matrix()
        self._fill_hierarchy()
        cnt = res.get("counts") or {}
        rows, cols = [], {}
        for i, (k, c) in enumerate(cnt.items()):
            rows.append((k,) + tuple(str(c.get(v, 0)) for v in RF.VORDER) + (str(sum(c.values())),))
        self.t_counts.set_rows(rows, colors=cols)
        self.p_sum.draw(RF.fig_reference_summary, res, title=tr("그룹별 판정", "verdicts per group"),
                        name="reference_summary")
        orep = res.get("open_report") or {}
        rows = []
        for pid, o in sorted(orep.items(), key=lambda kv: -len(kv[1]["items"])):
            rows.append((pid, o.get("provenance", ""), o.get("unit", ""),
                         f"{len(o['items'])}: " + ", ".join(o["items"][:12]) + (" …" if len(o["items"]) > 12 else ""),
                         o.get("note", "")))
        self.t_open.set_rows(rows)
        rows = []
        for c in res.get("conflicts") or []:
            for ch in c["checks"]:
                rows.append((c["id"], ch.get("label") or ch.get("check", ""),
                             "; ".join(x for x in ch.get("reasons") or [] if x)))
        self.t_conf.set_rows(rows)
        items = {i["id"]: i for i in self.package.get("items") or []}
        rows = []
        for iid in res.get("manual") or []:
            it = items.get(iid, {})
            rows.append((iid, it.get("manual") or next((c.get("params", {}).get("reason") for c in it.get("checks") or []
                                                        if c.get("check") == "manual"), "") or it.get("title", "")))
        self.t_man.set_rows(rows)
        tot = sum(sum(c.values()) for c in cnt.values())
        fails = sum(c.get("FAIL", 0) for c in cnt.values())
        unk = sum(c.get("UNKNOWN", 0) for c in cnt.values())
        self.state.setText(tr(f"항목 {tot}개 판정 · FAIL {fails} · UNKNOWN {unk} · OPEN 값 {len(orep)}개 · 실행 "
                              f"{len(res.get('runs') or {})}회", f"{tot} item(s) judged · FAIL {fails} · UNKNOWN "
                                                                 f"{unk} · {len(orep)} OPEN value(s) · "
                                                                 f"{len(res.get('runs') or {})} run(s)"))
        fm = None
        for r in res.get("rows") or []:
            for c in self._flat_checks(r):
                if (c.get("evidence") or {}).get("map"):
                    fm = c["evidence"]["map"]
                    break
            if fm:
                break
        if fm:
            self.p_map.draw(RF.fig_feasibility_map, fm, title=tr("안전상태 타당성 지도", "safe-state feasibility map"),
                            name="feasibility_map")

    def apply_project(self, project=None):
        """Another product model: the label names it; a shown result stays, marked as made on the previous one."""
        pr = project or self.win.state.project
        was = (self.last or {}).get("project") or {}
        note = ""
        if self.last is not None and was.get("digest") and was.get("digest") != pr.digest():
            note = tr(" — 표시된 결과는 이전 제품 모델로 계산됨: 다시 실행하세요",
                      " — the shown result was made on the previous product model: run again")
        self.product_label.setText(tr(f"제품 모델: {pr.label} ({pr.digest()[:12]})",
                                      f"product model: {pr.label} ({pr.digest()[:12]})") + note)

    def redraw(self):
        """The theme changed: the plots draw again in its colors."""
        for p in (self.p_ev, self.p_tw, self.p_sum, self.p_map):
            p.redraw()

    # ------------------------------------------------------------------ export
    def export_html(self, path: str | None = None):
        if self.last is None:
            return
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, tr("검증 보고서 저장", "save the verification report"),
                                                  "reference_verification.html", "HTML (*.html)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(api.reference_html(self.last))
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("저장 실패", "save failed"), str(exc))
            return
        self.state.setText(tr(f"보고서 저장: {path}", f"report saved: {path}"))

    def export_csv(self, path: str | None = None):
        if self.last is None:
            return
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, tr("매트릭스 CSV 저장", "save the matrix CSV"),
                                                  "reference_matrix.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(api.reference_csv(self.last))
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("저장 실패", "save failed"), str(exc))
            return
        self.state.setText(tr(f"매트릭스 저장: {path}", f"matrix saved: {path}"))
