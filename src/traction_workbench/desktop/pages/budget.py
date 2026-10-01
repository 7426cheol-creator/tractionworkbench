"""System budgets page (system view, item 11): a requirement split over its contributors and checked bottom-up -
the torque-accuracy budget over the envelope with its functional-safety link, the FTTI chain, the drive cycle's loss
energy per component, and a budget the engineer declares."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...insight.drive_system import budget_insight, budget_torque_insight
from ...plots import drive_system_figures as F
from ..widgets import (ConceptNote, KeyValueTable, NumTable, PlotPanel, combo, error_box, fmt, hint, number,
                       primary_button, reading_tab, table_with_buttons)

NOTE = lambda: tr(  # noqa: E731
    "<b>시스템 버짓</b>: 한 양(토크 오차·시간·에너지)의 한계를 기여 항목에 나누고 아래에서 위로 확인합니다.<br>• 기여마다 "
    "<b>계통(systematic)</b>(모든 유닛에서 같은 부호·크기, 선형 합)과 <b>랜덤(random)</b>(유닛 사이 독립 산포, 제곱합 근)을 "
    "정합니다. 최악(선형)·RSS·혼합(계통 선형 + 랜덤 RSS) 세 스택을 함께 보이고 선언한 스택이 판정합니다. 미확정 기여는 0으로 "
    "채우지 않습니다(UNKNOWN).<br>• <b>토크 정확도</b>: 전류 센서 이득·오프셋, 레졸버 오프셋, 자석 온도, 모델 공차를 운전점마다 "
    "기기 모델로 <b>계산</b>합니다(최소 전류 정책점, 정상 상태). 기능안전 연결: 정상 운전 오차가 안전 창 안에 있는지, 모니터가 "
    "오트립하지 않는지, 그리고 모니터 문턱 + 불일치 + 모니터가 못 보는 오차가 안전 창 안에 드는지(미검출 편차).",
    "<b>System budgets</b>: the limit of one quantity (torque error, time, energy) split over its contributors and "
    "checked bottom-up.<br>• Each contributor is <b>systematic</b> (same sign and size in every unit, added linearly) "
    "or <b>random</b> (independent spread between units, root sum of squares). Worst case (linear), RSS and mixed "
    "(systematic linear + random RSS) are shown side by side; the declared stack decides. A contributor that is not "
    "established is never filled with zero (UNKNOWN).<br>• <b>Torque accuracy</b>: current-sensor gain and offset, "
    "resolver offset, magnet temperature and model tolerance are <b>computed</b> with the machine model at every "
    "operating point (minimum-current policy point, steady state). Functional-safety link: the normal-operation error "
    "inside the safety window, no false trip of the monitor, and monitor threshold + mismatch + the errors the monitor "
    "cannot see inside the safety window (undetected deviation).")

KINDS = [(tr("랜덤 (RSS)", "random (RSS)"), "random"), (tr("계통 (선형)", "systematic (linear)"), "systematic")]
COMBS = [(tr("혼합 (계통 선형 + 랜덤 RSS)", "mixed (systematic linear + random RSS)"), "mixed"),
         (tr("최악 (선형 합)", "worst case (linear)"), "worst_case"), (tr("RSS (제곱합 근)", "RSS (root sum of squares)"), "rss")]
ALLOCS = [(tr("배분 없음", "no allocation"), None), (tr("균등", "equal"), "equal"),
          (tr("현재 값에 비례", "proportional"), "proportional"), (tr("선언 값", "declared"), "declared")]


def _task(fn, label):
    def run(progress, body):
        progress(0.02, label, 1.0)
        return fn(body)
    return run


def _scroll(w):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    sc.setMinimumWidth(420)
    return sc


def _floats(text: str, what: str) -> list:
    try:
        out = [float(x) for x in text.replace(",", " ").split()]
    except ValueError:
        raise ValueError(tr(f"{what}: 숫자 목록이 아닙니다", f"{what}: not a list of numbers")) from None
    if not out:
        raise ValueError(tr(f"{what}: 비었습니다", f"{what}: empty"))
    return out


class BudgetPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last_torque = self.last_budget = None
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        ex = self.win.state.example("BUDGET")
        tq = ex["torque"]
        g = QGroupBox(tr("토크 오차원 (프로젝트의 torque_errors 섹션)", "torque error sources (the project's torque_errors "
                                                                     "section)"))
        f = QFormLayout(g)
        self.e = {}
        self.k = {}

        def row(label, keys, kind_key=None):
            box = QHBoxLayout()
            for key, unit, lo, hi, dec, step in keys:
                w = number(0.0, lo, hi, unit, dec, step)
                self.e[key] = w
                box.addWidget(w)
            if kind_key:
                c = combo(KINDS, (tq.get("kinds") or {}).get(kind_key, "random"))
                self.k[kind_key] = c
                box.addWidget(c)
            holder = QWidget()
            holder.setLayout(box)
            box.setContentsMargins(0, 0, 0, 0)
            f.addRow(label, holder)

        row(tr("전류 센서 이득", "current-sensor gain"), [("current_gain_pct", "%", 0, 50, 3, 0.1)], "current_gain")
        row(tr("전류 센서 오프셋", "current-sensor offset"), [("current_offset_A", "A", 0, 500, 2, 0.5)], "current_offset")
        row(tr("레졸버 오프셋", "resolver offset"), [("resolver_offset_deg_e", "° e", 0, 90, 3, 0.1)], "resolver_offset")
        row(tr("자석 온도 추정 오차 · 계수", "magnet temperature error · coefficient"),
            [("magnet_temp_dev_K", "K", 0, 200, 1, 1), ("magnet_coeff_pct_per_K", "%/K", -1, 1, 3, 0.01)],
            "magnet_temperature")
        row(tr("모델 공차 ψ · L_d · L_q", "model tolerance ψ · L_d · L_q"),
            [("psi_tol_pct", "%", 0, 50, 2, 0.5), ("Ld_tol_pct", "%", 0, 50, 2, 0.5), ("Lq_tol_pct", "%", 0, 50, 2, 0.5)],
            "model_tolerance")
        row(tr("토크 추정기", "torque estimator"), [("estimator_pct", "%", 0, 50, 2, 0.5),
                                                  ("estimator_abs_Nm", "N·m", 0, 500, 2, 0.5)], "estimator")
        row(tr("모니터 불일치 (기능안전)", "monitor mismatch (FuSa)"), [("monitor_mismatch_pct", "%", 0, 50, 2, 0.5),
                                                                    ("monitor_mismatch_abs_Nm", "N·m", 0, 500, 2, 0.5)])
        f.addRow(hint(tr("0 = 선언하지 않음(버짓에서 빠지고 '선언 안 됨'으로 표시). 자석 계수는 기기의 자속 온도 법칙이 있으면 "
                         "그것을 씁니다. 센서 값은 고장 시뮬레이션의 센서 공차와 같아야 합니다(프로젝트 검사 PRJ-15).",
                         "0 = not declared (left out of the budget, listed as not declared). The magnet coefficient is "
                         "used only when the machine has no flux temperature law. The sensor values should equal the "
                         "fault simulation's sensor tolerances (project check PRJ-15).")))
        v.addWidget(g)
        g = QGroupBox(tr("요구와 운전점", "requirement and operating points"))
        f = QFormLayout(g)
        req = tq.get("requirement") or {}
        self.r_abs = number(float(req.get("abs_Nm") or 5.0), 0, 1000, "N·m", 2, 0.5)
        self.r_rel = number(100 * float(req.get("rel") or 0.05), 0, 100, "%", 2, 0.5)
        self.r_comb = combo(COMBS, tq.get("combination", "mixed"))
        self.r_vdc = number(float(tq.get("Vdc_V") or 600.0), 1, 2000, "V", 1, 10)
        self.r_n = QLineEdit(" ".join(f"{x:g}" for x in tq["speeds_rpm"]))
        self.r_f = QLineEdit(" ".join(f"{x:g}" for x in tq["fractions"]))
        for lab, w in ((tr("허용 오차 (절대)", "allowed error (absolute)"), self.r_abs),
                       (tr("허용 오차 (상대, |T|의)", "allowed error (relative, of |T|)"), self.r_rel),
                       (tr("스택", "stack"), self.r_comb), ("Vdc", self.r_vdc),
                       (tr("속도 [rpm]", "speeds [rpm]"), self.r_n),
                       (tr("능력 대비 토크 비율 (음수 = 제동)", "fractions of the capability (negative = braking)"), self.r_f)):
            f.addRow(lab, w)
        f.addRow(hint(tr("요구 = max(절대, 상대 × |T|). 운전점은 각 속도의 정책 토크 능력에 비율을 곱한 점입니다.",
                         "requirement = max(absolute, relative × |T|). The points are fractions of the policy torque "
                         "capability at each speed.")))
        v.addWidget(g)
        row_b = QHBoxLayout()
        self.btn = primary_button(tr("토크 정확도 버짓", "torque-accuracy budget"))
        self.btn.clicked.connect(self.run_torque)
        self.ftti_btn = QPushButton(tr("FTTI 버짓", "FTTI budget"))
        self.ftti_btn.clicked.connect(self.run_ftti)
        self.cyc_btn = QPushButton(tr("사이클 손실 버짓", "cycle-loss budget"))
        self.cyc_btn.clicked.connect(self.run_cycle)
        for b in (self.btn, self.ftti_btn, self.cyc_btn):
            b.setMinimumHeight(34)
            row_b.addWidget(b)
        v.addLayout(row_b)
        g = QGroupBox(tr("직접 선언하는 버짓", "a budget you declare"))
        f = QFormLayout(g)
        cu = ex["custom"]
        self.c_title = QLineEdit(str(cu.get("title", "")))
        self.c_unit = QLineEdit(str(cu.get("unit", "")))
        self.c_limit = number(float(cu.get("limit") or 0.0), 0, 1e9, "", 4, 0.1)
        self.c_comb = combo(COMBS, cu.get("combination", "mixed"))
        self.c_alloc = combo(ALLOCS, cu.get("allocation"))
        self.c_tab = NumTable([tr("이름", "name"), tr("값", "value"), tr("종류", "kind"), tr("배분 (선언)", "allocation"),
                               tr("근거", "basis")], text_cols=(0, 4), optional_cols=(1, 3), choice_cols={2: KINDS},
                              min_height=130)
        self.c_tab.load([[c.get("title") or c["id"], c.get("value"), c.get("kind", "random"), c.get("allocation"),
                          c.get("basis", "")] for c in cu.get("contributors") or []])
        for lab, w in ((tr("제목", "title"), self.c_title), (tr("단위", "unit"), self.c_unit),
                       (tr("한계 (0 = 미선언)", "limit (0 = not declared)"), self.c_limit), (tr("스택", "stack"), self.c_comb),
                       (tr("배분", "allocation"), self.c_alloc)):
            f.addRow(lab, w)
        f.addRow(table_with_buttons(self.c_tab, tr("값을 비우면 미확정(UNKNOWN). 종류는 목록에서 고릅니다.",
                                                   "a blank value is not established (UNKNOWN). Pick the kind from the "
                                                   "list.")))
        self.cust_btn = QPushButton(tr("선언 버짓 계산", "evaluate the declared budget"))
        self.cust_btn.setMinimumHeight(30)
        self.cust_btn.clicked.connect(self.run_custom)
        f.addRow(self.cust_btn)
        v.addWidget(g)
        v.addWidget(ConceptNote(NOTE()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(("budget_torque", "budget_ftti", "budget_cycle", "budget_custom"), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.p_pts = PlotPanel(hint=tr("'토크 정확도 버짓'을 누르세요", "press 'torque-accuracy budget'"))
        self.p_fusa = PlotPanel(hint=tr("'토크 정확도 버짓'을 누르세요", "press 'torque-accuracy budget'"))
        self.p_worst = PlotPanel(hint=tr("'토크 정확도 버짓'을 누르세요", "press 'torque-accuracy budget'"))
        self.p_bud = PlotPanel(hint=tr("FTTI·사이클 손실·선언 버짓 중 하나를 계산하세요",
                                       "evaluate the FTTI, cycle-loss or declared budget"))
        for p, lab in ((self.p_pts, tr("운전점별 토크 오차", "torque error per point")),
                       (self.p_fusa, tr("기능안전 연결", "functional-safety link")),
                       (self.p_worst, tr("최악점 배분", "worst-point allocation")),
                       (self.p_bud, tr("버짓", "budget"))):
            self.tabs.addTab(p, lab)
        self.reading = reading_tab(self.tabs, tr(
            "계산하면 해석이 표시됩니다 — 어디서 버짓이 넘치는지, 무엇이 지배하는지, 통과하려면 각 항목이 얼마여야 하는지, "
            "기능안전 창과의 관계.",
            "Run to read the result — where the budget overflows, what dominates, what each item must be to pass, and "
            "the relation to the safety window."))
        self.kv = KeyValueTable()
        rl.addWidget(self.tabs, 3)
        rl.addWidget(self.kv, 2)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([470, 990])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)
        self.apply_project()

    # -- project ---------------------------------------------------------------------------------------------
    def apply_project(self, _project=None):
        """The torque error sources from the active project (requirement, points and the declared budget stay)."""
        e = self.win.state.example("BUDGET")["torque"]["errors"] or {}
        for key, w in self.e.items():
            if key == "magnet_coeff_pct_per_K":
                c = e.get("magnet_coeff_per_K")
                w.setValue(0.0 if c is None else 100.0 * float(c))
            else:
                w.setValue(float(e.get(key) or 0.0))

    def _errors(self) -> dict:
        base = self.win.state.example("BUDGET")["torque"]["errors"] or {}
        out = {k: base[k] for k in ("basis", "magnet_coeff_basis", "source") if k in base}
        for key, w in self.e.items():
            val = w.value()
            if key == "magnet_coeff_pct_per_K":
                out["magnet_coeff_per_K"] = None if val == 0 else val / 100.0
            else:
                out[key] = None if val == 0 else val
        return out

    def body(self) -> dict:
        b = self.win.state.example("BUDGET")
        b["torque"] = {**b["torque"], "errors": self._errors(),
                       "requirement": {"abs_Nm": self.r_abs.value(), "rel": self.r_rel.value() / 100.0,
                                       "basis": tr("페이지에서 입력", "entered on the page")},
                       "combination": self.r_comb.currentData(), "Vdc_V": self.r_vdc.value(),
                       "kinds": {k: c.currentData() for k, c in self.k.items()},
                       "speeds_rpm": _floats(self.r_n.text(), tr("속도", "speeds")),
                       "fractions": _floats(self.r_f.text(), tr("비율", "fractions"))}
        rows = self.c_tab.values()
        b["custom"] = {"title": self.c_title.text().strip() or tr("선언 버짓", "declared budget"),
                       "unit": self.c_unit.text().strip(),
                       "limit": self.c_limit.value() if self.c_limit.value() > 0 else None,
                       "combination": self.c_comb.currentData(), "allocation": self.c_alloc.currentData(),
                       "contributors": [{"id": f"c{i + 1}", "title": r[0] or f"c{i + 1}", "value": r[1], "kind": r[2],
                                         "allocation": r[3], "basis": r[4]} for i, r in enumerate(rows)]}
        b.update(self.win.state.body())
        return b

    # -- runs ------------------------------------------------------------------------------------------------
    def _buttons(self):
        return (self.btn, self.ftti_btn, self.cyc_btn, self.cust_btn)

    def _run(self, key, label, fn, show, btn):
        try:
            body = self.body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        btn.setEnabled(False)
        self.win.runner.run(key, label, _task(fn, label), show, body, on_error=self._err)

    def _err(self, msg, tb):
        for b in self._buttons():
            b.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def run_torque(self):
        self._run("budget_torque", tr("토크 정확도 버짓", "torque-accuracy budget"), api.budget_torque,
                  self.show_torque, self.btn)

    def run_ftti(self):
        self._run("budget_ftti", tr("FTTI 버짓", "FTTI budget"), api.budget_ftti, self.show_budget, self.ftti_btn)

    def run_cycle(self):
        self._run("budget_cycle", tr("사이클 손실 버짓", "cycle-loss budget"), api.budget_cycle, self.show_budget,
                  self.cyc_btn)

    def run_custom(self):
        self._run("budget_custom", tr("선언 버짓", "declared budget"), api.budget_custom, self.show_budget,
                  self.cust_btn)

    # -- results ---------------------------------------------------------------------------------------------
    def show_torque(self, res):
        self.btn.setEnabled(True)
        self.last_torque = res
        cols = ("speed_rpm", "torque_Nm", "status", "total_Nm", "limit_Nm", "margin_Nm", "fusa_window_Nm",
                "monitor_threshold_Nm", "undetected_Nm", "undetected_status")
        items = ("current_gain", "current_offset", "resolver_offset", "magnet_temperature", "model_tolerance",
                 "estimator", "monitor_mismatch")
        csv = lambda r=res: {**{c: [p.get(c) for p in r["points"]] for c in cols},           # noqa: E731
                             **{f"{k}_Nm": [(p.get("contributions_Nm") or {}).get(k) for p in r["points"]]
                                for k in items}}
        self.p_pts.draw(F.fig_budget_torque, res, name="budget_torque_points", csv=csv)
        self.p_fusa.draw(F.fig_budget_fusa, res, name="budget_torque_fusa", csv=csv)
        if res.get("worst_point_budget"):
            self.p_worst.draw(F.fig_budget_bars, res["worst_point_budget"], name="budget_torque_worst")
        if self.tabs.currentWidget() is not self.reading:
            self.tabs.setCurrentWidget(self.p_pts)
        self.reading.read("budget_torque", tr("토크 정확도 버짓", "torque-accuracy budget"), budget_torque_insight, res)
        w = res.get("worst_point") or {}
        f = res.get("fusa") or {}
        cnt = res.get("counts") or {}
        rows = [(tr("판정", "verdict"), f"{res.get('status')} — PASS {cnt.get('PASS', 0)} / FAIL {cnt.get('FAIL', 0)} / "
                                       + tr(f"미평가 {res.get('not_evaluated', 0)}",
                                            f"not evaluated {res.get('not_evaluated', 0)}")),
                (tr("스택", "stack"), res.get("combination", ""))]
        if w:
            rows += [(tr("최악점", "worst point"), f"{fmt(w.get('speed_rpm'), 5)} rpm, {fmt(w.get('torque_Nm'), 4)} N·m: "
                                                 f"{fmt(w.get('total_Nm'), 4)} / {fmt(w.get('limit_Nm'), 4)} N·m")]
        if f:
            rows += [(tr("안전 창 · 오트립 · 미검출 편차", "safety window · false trip · undetected deviation"),
                      f"{f.get('window_status')} · {f.get('false_trip_status')} · {f.get('undetected_status')}"),
                     (tr("모니터가 보는 것", "what the monitor sees"), f.get("monitor_sees_basis", ""))]
        if res.get("not_declared"):
            rows.append((tr("선언 안 된 오차원", "error sources not declared"), ", ".join(res["not_declared"])))
        self.kv.set_rows(rows)

    def show_budget(self, res):
        for b in (self.ftti_btn, self.cyc_btn, self.cust_btn):
            b.setEnabled(True)
        self.last_budget = res
        self.p_bud.draw(F.fig_budget_bars, res, name="budget",
                        csv=lambda r=res: {k: [c.get(k) for c in r["contributors"]]
                                           for k in ("id", "title", "value", "kind", "share", "allocation",
                                                     "allocation_status", "break_even_growth")})
        if self.tabs.currentWidget() is not self.reading:
            self.tabs.setCurrentWidget(self.p_bud)
        self.reading.read("budget", res.get("title", tr("버짓", "budget")), budget_insight, res)
        st = res.get("stacks") or {}
        unit = str(res.get("unit", "")).replace("*", "·")
        self.kv.set_rows([(tr("판정", "verdict"), f"{res.get('status')} ({res.get('combination')})"),
                          (tr("합계 / 한계", "total / limit"), f"{fmt(res.get('total'), 5)} / {fmt(res.get('limit'), 5)} {unit}"),
                          (tr("스택: 최악 · RSS · 혼합", "stacks: worst case · RSS · mixed"),
                           " · ".join(fmt(st.get(k), 5) for k in ("worst_case", "rss", "mixed")) + f" {unit}"),
                          (tr("미확정 기여", "contributors not established"), ", ".join(res.get("missing") or []) or "—")])

    def redraw(self):
        for p in (self.p_pts, self.p_fusa, self.p_worst, self.p_bud, self.reading):
            p.redraw()
