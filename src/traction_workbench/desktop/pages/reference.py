"""Reference verification: a requirement / scenario package (a specification, a study reference or the
built-in example) verified item by item on the product - the requirement-to-evidence matrix with PASS / FAIL /
UNKNOWN / CONFLICT / MANUAL, the evidence of every check on one clock, the OPEN parameters with the items that wait for
them, the conflicts kept as variants, the safe-state feasibility map, HTML and CSV exports."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import api
from ...extensions.faultsim.refhier import (DEFAULT_SOURCE_TAGS, LEVELS, REQ_LEVELS, hierarchy, level_name,
                                            level_of, set_name)
from ...i18n import tr
from ...plots import reference_figures as RF
from ..widgets import Cell, ConceptNote, KeyValueTable, PlotPanel, combo, error_box, hint, primary_button

VCOLOR = {"PASS": "#1a7f37", "FAIL": "#cf222e", "UNKNOWN": "#b7791f", "CONFLICT": "#8250df", "MANUAL": "#0969da",
          "NOT_APPLICABLE": "#8c959f"}
VKO = {"PASS": "PASS", "FAIL": "FAIL", "UNKNOWN": "UNKNOWN", "CONFLICT": "CONFLICT", "MANUAL": "MANUAL",
       "NOT_APPLICABLE": "N/A"}

NOTE = lambda: tr(  # noqa: E731
    "<b>기능안전 요구 검증</b>: 요구·시나리오 패키지(사양서, 스터디 참고 문서, 또는 내장 예제)의 항목을 이 제품 모델에서 "
    "하나씩 판정합니다. 근거 수준(CONFIRMED / PAST-PROJECT / PROJECT / DERIVED / RESEARCH / OPEN / CONFLICT)을 그대로 "
    "유지하며, <b>OPEN 값은 추정하지 않습니다</b> - 그 값이 필요한 판정은 UNKNOWN이 되고 어떤 값이 필요한지 이름을 남깁니다. "
    "예시 값으로 보고 싶다면 '예시(illustrative)' 프로파일을 고르세요: 예시 값을 쓴 결과는 모두 ILL로 표시되고 원문 값 판정과 "
    "섞이지 않습니다. 기록이 충돌하는 항목은 변형(variant)으로 둘 다 실행하며, 확정된 두 판정이 다를 때만 CONFLICT입니다. "
    "안전 상태는 명령 비트가 아니라 <b>물리 결과</b>(C1-C4: 토크·DC 전력 방향·능동 펄싱·TLSR)로 판정합니다.<br>"
    "모든 시뮬레이션 판정은 이 앱에 불러온 <b>제품 모델</b>에 대한 것입니다 (기본은 합성 예제 제품).",
    "<b>Reference verification</b>: the items of a requirement / scenario package (a specification, a study "
    "reference or the built-in example) judged one by one on this product model.  The provenance tags (CONFIRMED / "
    "PAST-PROJECT / PROJECT / DERIVED / RESEARCH / OPEN / CONFLICT) are kept, and <b>OPEN values are never "
    "guessed</b>: a verdict that needs one is UNKNOWN and names it.  Choose the 'illustrative' profile to see example "
    "values: every result that used one is marked ILL and kept apart from the verdicts on the given values.  "
    "Conflicting records "
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
        # ① what is verified: a package (the built-in ones or a file)
        g = QGroupBox(tr("① 패키지 — 무엇을 검증하나", "① package — what is verified"))
        f = QVBoxLayout(g)
        self.builtin = QComboBox()
        for key, ko, en in api.reference_builtin():
            self.builtin.addItem(tr(ko, en), key)
        self.builtin.setToolTip(tr("앱에 들어 있는 패키지: 중립 예제, 인버터 FuSa 참고 문서(계층 분류·추적·제안 포함)",
                                   "packages shipped with the application: the neutral example and the inverter "
                                   "FuSa reference (classified, traced, with proposals)"))
        f.addWidget(self.builtin)
        row = QHBoxLayout()
        self.example_btn = QPushButton(tr("불러오기", "load"))
        self.example_btn.clicked.connect(lambda: self.load_builtin(self.builtin.currentData()))
        self.open_btn = QPushButton(tr("파일…", "file…"))
        self.open_btn.setToolTip(tr("참고 패키지 JSON 파일 열기", "open a reference package JSON file"))
        self.open_btn.clicked.connect(self.open_package)
        self.save_pkg_btn = QPushButton(tr("저장…", "save…"))
        self.save_pkg_btn.setToolTip(tr("지금 패키지를 JSON으로 저장 (새 패키지의 틀로 쓰기)",
                                        "save the current package as JSON (a template for a new package)"))
        self.save_pkg_btn.clicked.connect(self.save_package)
        for b in (self.example_btn, self.open_btn, self.save_pkg_btn):
            row.addWidget(b)
        f.addLayout(row)
        self.pkg_label = QLabel("")
        self.pkg_label.setWordWrap(True)
        f.addWidget(self.pkg_label)
        v.addWidget(g)
        # ② which items: one safety goal and what traces to it, a level, a group, a search
        g = QGroupBox(tr("② 범위 — 어떤 항목을", "② scope — which items"))
        fl = QFormLayout(g)
        self.goal = QComboBox()
        self.goal.setToolTip(tr("안전 목표 하나를 고르면 그 목표와 그 아래로 추적된 요구만 남습니다 (처음이라면 여기서 시작)",
                                "one safety goal keeps the goal and every requirement traced below it (a good first "
                                "run)"))
        self.goal.currentIndexChanged.connect(self._fill_matrix)
        self.group = QComboBox()
        self.group.currentIndexChanged.connect(self._fill_matrix)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("ID·제목 검색", "search id / title"))
        self.search.textChanged.connect(self._fill_matrix)
        self.only_customer = combo([(tr("모든 항목", "all items"), "all"),
                                    (tr("원문 요구만", "source requirements only"), "customer"),
                                    (tr("내부 (DERIVED 등)만", "internal only"), "internal")])
        self.only_customer.currentIndexChanged.connect(self._fill_matrix)
        self.level = QComboBox()
        self.level.addItem(tr("모든 레벨", "all levels"), None)
        self.level.addItem(tr("요구만 (SG·TLSR·FSR·TSR·SM·검증)", "requirements only (SG … verification)"), "REQ")
        for lv in LEVELS:
            self.level.addItem(level_name(lv), lv)
        self.level.currentIndexChanged.connect(self._fill_matrix)
        fl.addRow(tr("안전 목표", "safety goal"), self.goal)
        fl.addRow(tr("레벨", "level"), self.level)
        fl.addRow(tr("그룹", "group"), self.group)
        fl.addRow(tr("검색", "search"), self.search)
        fl.addRow(tr("근거", "provenance"), self.only_customer)
        self.scope_label = QLabel("")
        self.scope_label.setWordWrap(True)
        fl.addRow(self.scope_label)
        v.addWidget(g)
        # ③ the values the source leaves OPEN: never guessed - as given (UNKNOWN), entered (USER) or illustrative
        g = QGroupBox(tr("③ 값 — 원문에 없는 (OPEN) 값", "③ values — the ones the source leaves OPEN"))
        fl = QFormLayout(g)
        self.profile = combo([(tr("원문 값 (OPEN은 UNKNOWN)", "as given (OPEN stays UNKNOWN)"), "customer"),
                              (tr("예시 (illustrative, 결과에 ILL 표시)", "illustrative (results marked ILL)"),
                               "illustrative")])
        fl.addRow(self.profile)
        self.open_label = QLabel("")
        self.open_label.setWordWrap(True)
        self.open_go = QPushButton(tr("OPEN 값 입력 →", "enter OPEN values →"))
        self.open_go.clicked.connect(lambda: self.tabs.setCurrentWidget(self.tab_params))
        fl.addRow(self.open_label)
        fl.addRow(self.open_go)
        v.addWidget(g)
        self.product_label = hint("")
        v.addWidget(self.product_label)
        self.run_btn = primary_button(tr("표시된 항목 검증 실행", "verify the listed items"))
        self.run_btn.setMinimumHeight(38)
        self.run_btn.clicked.connect(lambda: self.run(selected=False))
        self.run_sel_btn = QPushButton(tr("매트릭스에서 선택한 항목만 실행", "verify the items selected in the matrix"))
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
        v.addWidget(ConceptNote(NOTE()))
        v.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        scroll.setMinimumWidth(330)
        split.addWidget(scroll)
        # ---- results
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        # overview: what the package holds before a run, what to look at first after it (every line a link)
        self.overview = QTextBrowser()
        self.overview.setOpenLinks(False)
        self.overview.anchorClicked.connect(self._link)
        self.tabs.addTab(self.overview, tr("개요 · 먼저 볼 것", "overview · look here first"))
        # matrix
        mw = QWidget()
        ml = QVBoxLayout(mw)
        top = QHBoxLayout()
        top.addWidget(hint(tr("항목을 고르면 아래에 판정 이유·검사·원문이 나옵니다.",
                              "Select an item to read its verdict, checks and source text below.")), 1)
        self.all_cols = QCheckBox(tr("모든 열 보기", "all columns"))
        self.all_cols.setProperty("twb_not_input", True)
        self.all_cols.toggled.connect(self._columns)
        top.addWidget(self.all_cols)
        ml.addLayout(top)
        msplit = QSplitter(Qt.Vertical)
        self.matrix = QTableWidget()
        heads = [tr("ID", "ID"), tr("판정", "verdict"), tr("레벨", "level"), tr("근거", "provenance"), tr("원문", "source"),
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
        self.item_text = QTextBrowser()             # the selected item, readable: verdict, why, text, OPEN values
        self.item_text.setOpenLinks(False)
        self.item_text.anchorClicked.connect(self._link)
        msplit.insertWidget(1, self.item_text)
        msplit.setSizes([380, 190, 200])
        ml.addWidget(msplit)
        self.tab_matrix = mw
        self.tabs.addTab(mw, tr("요구-증거 매트릭스", "requirement-to-evidence matrix"))
        self._columns(False)
        # hierarchy: SG -> TLSR -> FSR -> TSR -> SM -> verification, the gaps and the proposals
        hw = QWidget()
        hl = QVBoxLayout(hw)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(hint(tr(
            "레벨과 추적은 패키지에 적힌 대로입니다: 문서가 인용한 번호로 이은 추적은 실선, 주제로 <i>추론</i>한 연결은 '(추론)'으로 "
            "표시합니다(검토용). 판정 = 항목 자체, 하위 = 아래 모든 요구의 최악(FAIL > CONFLICT > UNKNOWN > PASS). "
            "'제안'은 시뮬레이션이 빠졌다고 보여 준 DERIVED 추가 요구로, 원문 요구로 세지 않습니다.",
            "Levels and traces as the package states them: a trace by a number the source cites is plain, a link "
            "<i>inferred</i> by topic is marked '(inferred)' (for review). Verdict = the item itself, subtree = the worst "
            "of every requirement below (FAIL > CONFLICT > UNKNOWN > PASS). A 'proposal' is a DERIVED addition the "
            "simulation shows is missing: never counted as a source requirement.")))
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
        ul.addWidget(hint(tr("원문이 값을 주지 않아 판정할 수 없는 값: FAIL이 아니라 UNKNOWN으로 두고, 각 값을 기다리는 항목 수로 "
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
        self.tab_report = uw
        self.tabs.addTab(uw, tr("UNKNOWN·CONFLICT 보고", "unknown · conflict report"))
        # parameters
        pw = QWidget()
        pl = QVBoxLayout(pw)
        pl.addWidget(hint(tr("노란 칸(입력 값)에 숫자를 넣으면 다음 실행에 USER 근거로 쓰입니다. 비우면 패키지 값 또는 OPEN "
                             "(UNKNOWN). 목록 값(예: 속도별 허용 오차)은 '목록 편집…'으로 표에서 넣습니다.",
                             "A number typed in the yellow cell (entered value) is used by the next run as USER; empty "
                             "means the package value or OPEN (UNKNOWN). List values (e.g. a tolerance per speed) are "
                             "entered as a table with 'edit list…'.")))
        row = QHBoxLayout()
        self.use_ill_btn = QPushButton(tr("선택한 행에 예시 값 넣기", "use the illustrative value (selected rows)"))
        self.use_ill_btn.clicked.connect(self._use_illustrative)
        self.list_btn = QPushButton(tr("목록 편집…", "edit list…"))
        self.list_btn.clicked.connect(self._edit_list)
        self.only_open = QCheckBox(tr("OPEN 값만", "OPEN values only"))
        self.only_open.setProperty("twb_not_input", True)
        self.only_open.toggled.connect(lambda _c: self._fill_params())
        for w in (self.use_ill_btn, self.list_btn, self.only_open):
            row.addWidget(w)
        row.addStretch(1)
        pl.addLayout(row)
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
        self.tab_params = pw
        self.tabs.addTab(pw, tr("OPEN 값 입력 (파라미터)", "OPEN values (parameters)"))
        self.p_map = PlotPanel(hint=tr("타당성 지도 검사가 있는 항목을 실행하면 여기에 그려집니다.",
                                       "Run an item with a feasibility check to see the map."))
        self.tabs.addTab(self.p_map, tr("안전상태 타당성 지도", "safe-state feasibility map"))
        self.problems = QPlainTextEdit()
        self.problems.setReadOnly(True)
        self.tabs.addTab(self.problems, tr("패키지 정보·경고", "package notes"))
        rv.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 1000])
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
        self.hier = hierarchy(self.package, [])
        self.goal.blockSignals(True)
        self.goal.clear()
        self.goal.addItem(tr("모든 안전 목표", "every safety goal"), None)
        for it in items:
            if level_of(it)[0] == "SG":
                t = it.get("title", "")
                self.goal.addItem(f"{it['id']} — {t if len(t) <= 60 else t[:57] + '…'}", it["id"])
                self.goal.setItemData(self.goal.count() - 1, t, Qt.ToolTipRole)
        self.goal.blockSignals(False)
        self._fill_params()
        self._fill_matrix()
        self._fill_hierarchy()
        self._fill_overview()
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
        if getattr(self, "only_open", None) is not None and self.only_open.isChecked():
            ps = [p for p in ps if p.get("provenance") in ("OPEN", "CONFLICT") or p["id"] in self.values]
        self.params.blockSignals(True)
        self.params.setRowCount(len(ps))
        entry = QColor("#fff8c5")
        for i, p in enumerate(ps):
            cells = (p["id"], p.get("unit", ""), p.get("provenance", ""), value_text(p.get("value")),
                     value_text(self.values.get(p["id"])), value_text(p.get("illustrative")), p.get("note", ""))
            for j, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if j != 4:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                else:
                    it.setBackground(entry)                  # the one cell to type in
                    it.setForeground(QColor("#1f2328"))
                    it.setToolTip(tr("숫자를 입력 (목록 값은 '목록 편집…')", "type a number (a list value: 'edit list…')"))
                if j == 2 and text in ("OPEN", "CONFLICT"):
                    it.setForeground(Qt.darkYellow)
                self.params.setItem(i, j, it)
        self.params.resizeColumnsToContents()
        self.params.blockSignals(False)
        self._open_summary()

    def _open_summary(self):
        ps = self.package.get("parameters") or []
        n_open = sum(1 for p in ps if p.get("provenance") in ("OPEN", "CONFLICT"))
        n_in = len(self.values)
        self.open_label.setText(tr(f"원문이 값을 주지 않은 파라미터 {n_open}개 · 입력한 값 {n_in}개. 원문 값 프로파일에서 OPEN 값이 "
                                   f"필요한 판정은 UNKNOWN이고, 그 값을 넣으면 판정됩니다.",
                                   f"{n_open} parameter(s) the source leaves open · {n_in} entered. With 'as given', a "
                                   f"verdict that needs an OPEN value is UNKNOWN; entering the value decides it."))

    def _selected_params(self) -> list:
        rows = sorted({i.row() for i in self.params.selectedIndexes()} or
                      ({self.params.currentRow()} if self.params.currentRow() >= 0 else set()))
        return [self.params.item(r, 0).text() for r in rows if self.params.item(r, 0)]

    def _use_illustrative(self):
        by = {p["id"]: p for p in self.package.get("parameters") or []}
        done = []
        for pid in self._selected_params():
            ill = by.get(pid, {}).get("illustrative")
            if ill is not None:
                self.values[pid] = ill
                done.append(pid)
        self._fill_params()
        self.state.setText(tr(f"예시 값을 입력 값으로: {', '.join(done) or '없음 (예시 값이 없는 행)'}",
                              f"illustrative values entered: {', '.join(done) or 'none (rows without one)'}"))

    def _edit_list(self, pid: str | None = None):
        """A list value (e.g. a tolerance per speed) as a small table: one column per element of a row."""
        from ..widgets import NumTable, table_with_buttons
        pid = pid or next(iter(self._selected_params()), None)
        if pid is None:
            error_box(self, tr("입력 오류", "input error"), tr("파라미터 표에서 행을 고르세요.", "Select a parameter row."))
            return
        p = next((x for x in self.package.get("parameters") or [] if x["id"] == pid), {})
        cur = self.values.get(pid, p.get("value") if p.get("value") is not None else p.get("illustrative"))
        rows = cur if isinstance(cur, list) else ([] if cur is None else [cur])
        rows = [r if isinstance(r, list) else [r] for r in rows]
        ncol = max([len(r) for r in rows] + [2 if isinstance(p.get("illustrative"), list) and
                                             p["illustrative"] and isinstance(p["illustrative"][0], list) else 1])
        dlg = QDialog(self)
        dlg.setWindowTitle(tr(f"{pid} 목록 값", f"{pid} list value"))
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"<b>{pid}</b> [{p.get('unit', '')}] — {p.get('note', '')}"))
        heads = [tr(f"열 {j + 1}", f"column {j + 1}") for j in range(ncol)]
        tab = NumTable(heads, rows, min_height=180)
        lay.addWidget(table_with_buttons(tab, tr("행마다 한 점 (예: 속도, 허용 오차). Ctrl+V로 스프레드시트에서 붙여넣기.",
                                                 "one point per row (e.g. speed, tolerance); Ctrl+V pastes from a "
                                                 "spreadsheet.")))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            vals = tab.values()
        except ValueError as exc:
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        if vals:
            self.values[pid] = [r if ncol > 1 else r[0] for r in vals]
        else:
            self.values.pop(pid, None)
        self._fill_params()
        self.state.setText(tr(f"{pid}: 목록 값 {len(vals)}점 입력 (USER)", f"{pid}: list value with {len(vals)} point(s) "
                                                                        f"entered (USER)"))

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
        self._open_summary()

    def _clear_values(self):
        self.values = {}
        self._fill_params()
        self.state.setText(tr("입력 값을 모두 지웠습니다.", "entered values cleared"))

    # ------------------------------------------------------------------ matrix
    def _visible_items(self) -> list:
        g = self.group.currentData()
        q = self.search.text().strip().lower()
        which = self.only_customer.currentData()
        cust = set(self.package.get("customer_tags") or DEFAULT_SOURCE_TAGS)
        out = []
        lvf = self.level.currentData()
        sub = self._subtree(self.goal.currentData()) if self.goal.currentData() else None
        for it in self.package.get("items") or []:
            if sub is not None and it["id"] not in sub:
                continue
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

    def _subtree(self, goal: str) -> set:
        """A safety goal and every item traced below it (cited and inferred traces)."""
        kids = (getattr(self, "hier", None) or {}).get("children") or {}
        out, todo = set(), [goal]
        while todo:
            i = todo.pop()
            if i in out:
                continue
            out.add(i)
            todo += [c for c, _b in kids.get(i) or []]
        return out

    @staticmethod
    def _scenarios_of(items) -> set:
        return {c.get("scenario") for it in items for c in it.get("checks") or [] if c.get("scenario")}

    def _scope_text(self, items) -> None:
        n_sc = len(self._scenarios_of(items))
        minutes = max(1, round(n_sc * 5.5 / 60.0)) if n_sc else 0     # ~5.5 s per scenario on the example package
        est = (tr("시뮬레이션 없음 (수 초)", "no simulation (seconds)") if not n_sc else
               tr(f"시나리오 {n_sc}개 시뮬레이션 · 약 {minutes}분", f"{n_sc} scenario simulation(s) · about {minutes} min"))
        self.scope_label.setText(tr(f"<b>검증할 항목 {len(items)}개</b> · {est}", f"<b>{len(items)} item(s) to verify</b> · "
                                                                             f"{est}"))
        self.run_btn.setText(tr(f"항목 {len(items)}개 검증 실행", f"verify {len(items)} item(s)"))
        self.run_btn.setEnabled(bool(items) and not getattr(self, "_running", False))
        refresh = getattr(self.win, "_refresh_run_action", None)      # the page bar shows the same words
        if refresh is not None and getattr(self.win, "run_actions", None) is not None:
            refresh("reference")

    def _fill_matrix(self):
        rows = {r["id"]: r for r in (self.last or {}).get("rows", [])}
        items = self._visible_items()
        self._scope_text(items)
        self.matrix.clearSelection()                  # the rows change: a kept selection would point elsewhere
        self._rows_shown = [it["id"] for it in items]
        cust = set(self.package.get("customer_tags") or DEFAULT_SOURCE_TAGS)
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
        self.item_text.setHtml(self._item_card(it, r))
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

    def _item_card(self, it: dict, r: dict | None) -> str:
        """The selected item as people read it: verdict and why, the requirement text, what it waits for."""
        import html as _h
        e = _h.escape
        v = (r or {}).get("verdict")
        col = VCOLOR.get(v, "#57606a")
        out = [f"<p style='margin:2px 0'><b>{e(it.get('id', ''))}</b> · {e(it.get('title', ''))}</p>",
               f"<p style='margin:2px 0'><span style='color:{col}; font-weight:700'>"
               f"{VKO.get(v, v) if v else tr('아직 실행 안 함', 'not run yet')}</span>"
               + (" <span style='color:#9a6700'>ILL</span>" if (r or {}).get("illustrative") else "")
               + f" · {e(level_name(level_of(it)[0]))} · {e(it.get('group', ''))} · {e(it.get('provenance', ''))}"
               + (f" · ASIL {e(it['asil_literal'])}" if it.get("asil_literal") else "") + "</p>"]
        if r is not None:
            why = []
            for c in self._flat_checks(r):
                reasons = "; ".join(x for x in c.get("reasons") or [] if x)
                if c.get("verdict") in ("FAIL", "UNKNOWN", "CONFLICT") and reasons:
                    why.append(f"<li><span style='color:{VCOLOR.get(c.get('verdict'), '#57606a')}'>"
                               f"{VKO.get(c.get('verdict'), c.get('verdict'))}</span> "
                               f"{e(c.get('label') or c.get('check', ''))}: {e(reasons)}</li>")
            if why:
                out.append("<p style='margin:6px 0 0 0'><b>" + tr("왜", "why") + "</b></p><ul style='margin:0'>"
                           + "".join(why[:6]) + "</ul>")
            if r.get("open"):
                links = ", ".join(f"<a href='param:{e(x)}'>{e(x)}</a>" for x in r["open"])
                out.append("<p style='margin:6px 0 0 0'>" + tr("기다리는 OPEN 값 (눌러서 입력): ",
                                                               "waiting for OPEN values (click to enter): ")
                           + links + "</p>")
        for k, lab in (("text", tr("요구", "requirement")), ("source", tr("출처", "source")),
                       ("done_when", tr("완료 조건", "done when")), ("status_note", tr("상태", "status")),
                       ("sim_note", tr("시뮬레이션 메모", "simulation note")),
                       ("inputs_needed", tr("필요한 입력", "inputs needed")), ("note", tr("메모", "note")),
                       ("manual", tr("기록할 근거", "evidence to record")), ("rationale", tr("근거", "rationale"))):
            if it.get(k):
                out.append(f"<p style='margin:4px 0 0 0'><b>{lab}</b>: {e(str(it[k]))}</p>")
        tr_ = list(it.get("traces_to") or [])
        inf = list(it.get("traces_to_inferred") or [])
        if tr_ or inf:
            out.append("<p style='margin:4px 0 0 0'><b>" + tr("추적", "traces to") + "</b>: "
                       + ", ".join(f"<a href='item:{e(x)}'>{e(x)}</a>" for x in tr_)
                       + ("" if not inf else ((", " if tr_ else "") + ", ".join(
                           f"<a href='item:{e(x)}'>{e(x)}</a>" + tr(" (추론)", " (inferred)") for x in inf)))
                       + "</p>")
        return "".join(out)

    def _columns(self, all_: bool):
        """The columns that matter first (ID, verdict, level, title, OPEN values); the others on request."""
        for j in (3, 4, 5, 6, 7, 8, 11):
            self.matrix.setColumnHidden(j, not all_)

    # ------------------------------------------------------------------ overview and links
    def _link(self, url):
        kind, _, val = url.toString().partition(":")
        if kind == "item":
            self.tabs.setCurrentWidget(self.tab_matrix)
            self.select_item(val)
        elif kind == "param":
            self.enter_value(val)
        elif kind == "tab":
            w = {"report": self.tab_report, "params": self.tab_params, "matrix": self.tab_matrix,
                 "hier": self.tab_hier}.get(val)
            if w is not None:
                self.tabs.setCurrentWidget(w)

    def enter_value(self, pid: str):
        """One OPEN value entered where it is asked for: a number with its unit (the illustrative value offered), a
        list as a small table."""
        from PySide6.QtWidgets import QInputDialog
        p = next((x for x in self.package.get("parameters") or [] if x["id"] == pid), None)
        if p is None:
            return
        cur = self.values.get(pid, p.get("value") if p.get("value") is not None else p.get("illustrative"))
        if isinstance(cur, (list, dict)) or isinstance(p.get("illustrative"), (list, dict)):
            self._edit_list(pid)
            return
        start = float(cur) if isinstance(cur, (int, float)) else 0.0
        v, ok = QInputDialog.getDouble(
            self, tr("OPEN 값 입력", "enter an OPEN value"),
            f"{pid} [{p.get('unit', '')}]\n{p.get('note', '')}\n"
            + (tr(f"예시 값: {value_text(p.get('illustrative'))}", f"illustrative: {value_text(p.get('illustrative'))}")
               if p.get("illustrative") is not None else ""), start, -1e12, 1e12, 6)
        if not ok:
            return
        self.values[pid] = v
        self._fill_params()
        self.state.setText(tr(f"{pid} = {v:g} {p.get('unit', '')} (USER) — 다음 실행에 쓰입니다. 같은 범위로 다시 실행하세요.",
                              f"{pid} = {v:g} {p.get('unit', '')} (USER) — used by the next run; run the same scope "
                              f"again."))

    def _fill_overview(self):
        import html as _h
        e = _h.escape
        pk = self.package
        items = pk.get("items") or []
        meta = pk.get("meta") or {}
        if self.last is None:
            by = {}
            for it in items:
                lv = level_of(it)[0]
                by[lv] = by.get(lv, 0) + 1
            n_open = sum(1 for p in pk.get("parameters") or [] if p.get("provenance") in ("OPEN", "CONFLICT"))
            levels = " · ".join(f"{e(level_name(lv))} {by[lv]}" for lv in LEVELS if by.get(lv))
            sgs = [it for it in items if level_of(it)[0] == "SG"]
            out = [f"<h3 style='margin:4px 0'>{e(meta.get('title', '-'))}</h3>",
                   "<p>" + tr(f"항목 {len(items)}개 ({levels}) · 시나리오 {len(pk.get('scenarios') or {})}개 · "
                              f"원문이 값을 주지 않은 파라미터 {n_open}개",
                              f"{len(items)} items ({levels}) · {len(pk.get('scenarios') or {})} scenarios · {n_open} "
                              f"parameter(s) the source leaves open") + "</p>",
                   "<p><b>" + tr("시작하는 법", "how to start") + "</b></p><ol>",
                   "<li>" + tr("왼쪽 ② 범위에서 <b>안전 목표 하나</b>를 고릅니다 — 그 목표와 그 아래로 추적된 요구만 남아 빨리 "
                               "끝납니다. 전부 보려면 그대로 두세요.",
                               "In ② scope on the left, pick <b>one safety goal</b> - only the goal and what traces "
                               "below it remain, so the run is quick. Leave it to verify everything.") + "</li>",
                   "<li>" + tr("③ 값: 원문 값 그대로(OPEN은 UNKNOWN) 또는 예시 값. 아는 값은 'OPEN 값 입력'에서 넣습니다.",
                               "③ values: as given (OPEN stays UNKNOWN) or illustrative; values you know go in "
                               "'OPEN values'.") + "</li>",
                   "<li>" + tr("<b>▶ 검증 실행</b>. 끝나면 이 탭에 FAIL·CONFLICT와 UNKNOWN을 푸는 값이 먼저 나옵니다.",
                               "<b>▶ verify</b>. When it ends, this tab lists FAIL, CONFLICT and the values that would "
                               "resolve UNKNOWN first.") + "</li></ol>"]
            if sgs:
                out.append("<p><b>" + tr("안전 목표", "safety goals") + "</b></p><ul>" + "".join(
                    f"<li><a href='item:{e(it['id'])}'>{e(it['id'])}</a> {e(it.get('title', ''))}</li>" for it in sgs)
                    + "</ul>")
            self.overview.setHtml("".join(out))
            return
        res = self.last
        rows = res.get("rows") or []
        cnt = {}
        for r in rows:
            cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
        tiles = "".join(f"<td style='padding:6px 12px; border:1px solid #d0d7de'><span style='font-size:16pt; "
                        f"font-weight:700; color:{VCOLOR.get(v, '#57606a')}'>{cnt.get(v, 0)}</span><br>"
                        f"{VKO.get(v, v)}</td>" for v in ("PASS", "FAIL", "UNKNOWN", "CONFLICT", "MANUAL",
                                                           "NOT_APPLICABLE"))
        items_by = {it["id"]: it for it in items}
        out = ["<p>" + tr(f"<b>{len(rows)}개 항목 판정</b> ({'예시 값' if res.get('profile') == 'illustrative' else '원문 값'}"
                          f" 프로파일, 입력 값 {len(res.get('values') or {})}개)",
                          f"<b>{len(rows)} item(s) judged</b> ({res.get('profile')} profile, "
                          f"{len(res.get('values') or {})} entered value(s))") + "</p>",
               f"<table cellspacing='0'><tr>{tiles}</tr></table>"]

        def first_reason(r):
            for c in self._flat_checks(r):
                if c.get("verdict") == r["verdict"]:
                    x = "; ".join(y for y in c.get("reasons") or [] if y)
                    if x:
                        return x if len(x) <= 160 else x[:157] + "…"
            return ""
        for v, head in (("FAIL", tr("FAIL — 먼저 볼 것", "FAIL — look here first")),
                        ("CONFLICT", tr("CONFLICT — 기록끼리 판정이 다름", "CONFLICT — the records disagree"))):
            bad = [r for r in rows if r["verdict"] == v]
            if bad:
                out.append(f"<p style='margin:10px 0 2px 0'><b style='color:{VCOLOR[v]}'>{head}</b></p><ul>")
                for r in bad[:15]:
                    out.append(f"<li><a href='item:{e(r['id'])}'>{e(r['id'])}</a> "
                               f"{e(items_by.get(r['id'], {}).get('title', ''))}<br><span style='color:#57606a'>"
                               f"{e(first_reason(r))}</span></li>")
                out.append("</ul>" + (tr(f"<p>… 외 {len(bad) - 15}개 (매트릭스에서)</p>", f"<p>… {len(bad) - 15} more "
                                                                                   f"(in the matrix)</p>")
                                      if len(bad) > 15 else ""))
        orep = res.get("open_report") or {}
        if orep:
            pmap = {p["id"]: p for p in pk.get("parameters") or []}
            out.append("<p style='margin:10px 0 2px 0'><b style='color:#9a6700'>"
                       + tr("UNKNOWN을 푸는 값 — 기다리는 항목이 많은 순", "values that resolve UNKNOWN — most items "
                                                                      "waiting first") + "</b></p><ul>")
            for pid, o in sorted(orep.items(), key=lambda kv: -len(kv[1]["items"]))[:12]:
                ill = pmap.get(pid, {}).get("illustrative")
                out.append(f"<li><a href='param:{e(pid)}'>{e(pid)}</a> [{e(o.get('unit', ''))}] — "
                           + tr(f"{len(o['items'])}개 항목이 기다림", f"{len(o['items'])} item(s) waiting")
                           + (tr(f" · 예시 값 {e(value_text(ill))}", f" · illustrative {e(value_text(ill))}")
                              if ill is not None else "")
                           + f"<br><span style='color:#57606a'>{e(o.get('note', ''))}</span></li>")
            out.append("</ul><p style='color:#57606a'>" + tr(
                "값 이름을 누르면 바로 입력합니다(USER). 예시 값으로 한꺼번에 보려면 ③에서 '예시' 프로파일을 고르고 다시 "
                "실행하세요 — 그 결과는 ILL로 따로 표시됩니다.",
                "Click a value's name to enter it (USER). To see all of them with the illustrative values, choose the "
                "'illustrative' profile in ③ and run again - those results are marked ILL.") + "</p>")
        man = res.get("manual") or []
        if man:
            out.append("<p style='margin:10px 0 2px 0'><b style='color:#0969da'>MANUAL</b> — "
                       + tr(f"조직·문서 근거를 기록할 항목 {len(man)}개 (<a href='tab:report'>목록</a>)",
                            f"{len(man)} item(s) need organisational evidence recorded (<a href='tab:report'>list"
                            f"</a>)") + "</p>")
        out.append("<p style='margin:10px 0 2px 0'>" + tr("계층·추적은 <a href='tab:hier'>계층 탭</a>, 전체 판정은 <a "
                                                          "href='tab:matrix'>매트릭스</a>에서.",
                                                          "Levels and traces in the <a href='tab:hier'>hierarchy</a>, "
                                                          "every verdict in the <a href='tab:matrix'>matrix</a>.")
                   + "</p>")
        self.overview.setHtml("".join(out))

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
        self.t_gaps.set_rows([(g["id"] + (tr(" (원문)", " (source)") if g.get("customer") else ""), g["level"],
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
            for w, v in ((self.group, None), (self.level, None), (self.goal, None)):
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
        self._running = True
        self.state.setText(tr(f"항목 {len(ids)}개 검증 중…", f"verifying {len(ids)} item(s)…"))
        self.win.runner.run("reference", tr("기능안전 요구 검증", "reference verification"), _task, self._show, body,
                            self.win.state.project, on_error=self._err)

    def _err(self, msg, tb):
        self._running = False
        for b in (self.run_btn, self.run_sel_btn):
            b.setEnabled(True)
        if msg == "CANCELLED":
            self.state.setText(tr("취소했습니다.", "cancelled"))
            return
        error_box(self, tr("검증 실패", "verification failed"), msg, tb)

    def _show(self, res):
        self._running = False
        for b in (self.run_btn, self.run_sel_btn, self.html_btn, self.csv_btn):
            b.setEnabled(True)
        self.last = res
        self._fill_matrix()
        self._fill_hierarchy()
        self._fill_overview()
        self.tabs.setCurrentWidget(self.overview)
        cnt = res.get("counts") or {}
        rows, cols = [], {}
        for i, (k, c) in enumerate(cnt.items()):
            rows.append((set_name(k),) + tuple(str(c.get(v, 0)) for v in RF.VORDER) + (str(sum(c.values())),))
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
