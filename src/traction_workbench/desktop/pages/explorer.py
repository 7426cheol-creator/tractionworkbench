"""Operating-point explorer: policy point for a torque, or any (id, iq) picked on the map (forward evaluation)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QFormLayout, QGroupBox, QLabel, QRadioButton, QScrollArea,
                               QSplitter, QVBoxLayout, QWidget)

from ...i18n import tr
from ...physics import forward_evaluation
from ...scenario import Scenario
from ...solvers.policy import PolicyEvaluator
from ...viz import maps as M
from ...viz import operating as O
from ..opviews import OperatingViews
from ..widgets import ConceptNote, KeyValueTable, MagnetTempInput, error_box, hint, number, primary_button


def _task(progress, drive, limits, n, vdc, mode, T, idv, iqv, magnet_temp_C=None):
    progress(0.1, tr("계산", "computing"))
    sc = Scenario("explorer", n, vdc, limits, magnet_temp_C=magnet_temp_C)
    out = {"mode": mode, "scenario": sc}
    if mode == "policy":
        sol = PolicyEvaluator(drive, sc).solve(T)
        out["claims"] = [c.to_dict() for c in sol.claims]
        pt = sol.point
        out["T"] = T
    else:
        fr = forward_evaluation(drive, sc, idv, iqv)
        out["claims"] = []
        viol = [] if fr.point is None else [c.name for c in fr.point.constraints if c.state == "VIOLATED"]
        notev = [] if fr.point is None else [c.name for c in fr.point.constraints if c.state == "NOT_EVALUATED"]
        out["forward"] = {"evaluable": fr.evaluable, "message": fr.message, "accepted": fr.accepted,
                          "validity_gate": fr.validity_gate_passed, "gate_messages": list(fr.gate_messages),
                          "issues": [i.message for i in fr.issues], "violated": viol, "not_evaluated": notev}
        pt = fr.point
        out["T"] = None if pt is None else pt.Tshaft_Nm
    out["pv"] = None if pt is None else O.point_view(drive, sc, pt.id_A, pt.iq_A, out["T"])
    progress(0.5, tr("id–iq 지도", "id–iq map"))
    out["plane"] = M.idiq_plane(drive, limits, n, vdc, out["T"] if mode == "policy" else None, scenario=sc)
    if out["pv"] is not None and mode == "forward":
        out["plane"]["policy_point"] = None
        out["plane"]["picked_point"] = (pt.id_A, pt.iq_A)
    return out


class ExplorerPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.plane_key = None
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("운전 조건", "operating condition"))
        f = QFormLayout(g)
        self.n = number(12000, -30000, 30000, "rpm", 1, 100)
        self.vdc = number(600, 1, 2000, "V", 2, 10)
        f.addRow(tr("속도", "speed"), self.n)
        f.addRow("Vdc", self.vdc)
        self.magnet = MagnetTempInput()
        f.addRow(tr("자석 온도", "magnet temp."), self.magnet)
        f.addRow(self.magnet.note)
        self.magnet.sync(win.state.drive)
        win.state.drive_changed.connect(lambda: self.magnet.sync(self.win.state.drive))
        v.addWidget(g)
        g = QGroupBox(tr("운전점 지정", "operating point"))
        f = QFormLayout(g)
        self.m_policy = QRadioButton(tr("토크 요구 → 최소전류 정책점", "torque → minimum-current policy point"))
        self.m_forward = QRadioButton(tr("id / iq 직접 입력 (정방향 평가)", "given id / iq (forward evaluation)"))
        grp = QButtonGroup(self)
        grp.addButton(self.m_policy)
        grp.addButton(self.m_forward)
        self.m_policy.setChecked(True)
        self.T = number(150, -5000, 5000, "N·m", 3, 5)
        self.id = number(-300, -5000, 5000, "A", 3, 5)
        self.iq = number(200, -5000, 5000, "A", 3, 5)
        f.addRow(self.m_policy)
        f.addRow(tr("축 토크", "shaft torque"), self.T)
        f.addRow(self.m_forward)
        f.addRow("id (peak)", self.id)
        f.addRow("iq (peak)", self.iq)
        v.addWidget(g)
        self.run_btn = primary_button(tr("계산", "compute"))
        self.run_btn.clicked.connect(self.run)
        v.addWidget(self.run_btn)
        v.addWidget(hint(tr("id–iq 지도를 클릭하면 그 전류 벡터를 그대로 평가합니다 (점을 옮기지 않음). 제약을 위반하는 점은 진단용이며 "
                            "가능한 해가 아닙니다. 마우스를 올리면 격자 값(토크, 전압, DC 전력)을 읽을 수 있습니다.",
                            "Click the id–iq map to evaluate that current vector as given (never moved); a violating point is a "
                            "diagnostic, not a feasible witness. Hover to read torque, voltage and DC power.")))
        v.addWidget(ConceptNote(tr(
            "<b>id–iq 지도 읽는 법</b>: 보라 타원 = 이 속도에서 인버터가 낼 수 있는 전압 한계(속도가 오르면 작아짐), 빨간 원 = 전류 "
            "한계, 점선 사각형 = 선언된 운전 도메인, 회색 곡선 = 등토크선, 청록선 = MTPA(같은 토크를 최소 전류로), 주황 = 요구 토크, "
            "일점쇄선 = DC 전력 한계. 초록 영역 안의 점만 가능하며, 요구 토크 곡선 위에서 원점에 가장 가까운 가능점이 최소전류 "
            "정책점(★)입니다. 전압 타원에 걸리면 d축 전류를 음으로 키워 자속을 줄이는 약계자 운전입니다.",
            "<b>Reading the id–iq map</b>: purple ellipse = voltage limit at this speed (shrinks with speed), red circle = "
            "current limit, dotted box = declared domain, grey = constant-torque lines, teal = MTPA, orange = requested torque, "
            "dash-dot = DC power limits. Only points in the green region are feasible; the feasible point on the torque curve "
            "closest to the origin is the minimum-current policy point (★). On the voltage ellipse the drive field-weakens "
            "(more negative id).")))
        self.claims = KeyValueTable(headers=[tr("판정 항목", "claim"), tr("상태", "status"), tr("설명", "detail")])
        v.addWidget(QLabel(tr("<b>결과</b>", "<b>result</b>")))
        v.addWidget(self.claims, 1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(form)
        sc.setMinimumWidth(320)
        split.addWidget(sc)
        self.views = OperatingViews(clickable=True)
        self.views.plane_clicked.connect(self._picked)
        split.addWidget(self.views)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 1100])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)
        self.win.track_inputs("explorer", form)

    def run(self):
        s = self.win.state
        mode = "policy" if self.m_policy.isChecked() else "forward"
        if self.magnet.missing(self):
            return
        self.run_btn.setEnabled(False)
        self.win.runner.run("explorer", tr("운전점 탐색", "explorer"), _task, self._show, s.drive, s.limits, self.n.value(),
                            self.vdc.value(), mode, self.T.value(), self.id.value(), self.iq.value(), self.magnet.get(),
                            on_error=self._err)

    def _err(self, msg, tb):
        self.run_btn.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def _picked(self, x, y):
        self.m_forward.setChecked(True)
        self.id.setValue(round(x, 1))
        self.iq.setValue(round(y, 1))
        self.run()

    def _show(self, res):
        self.run_btn.setEnabled(True)
        pl = res["plane"]
        sc = res["scenario"]
        title = f"n = {sc.speed_rpm:g} rpm · Vdc = {sc.Vdc_V:g} V"
        if sc.magnet_temp_C is not None:
            title += tr(f" · 자석 {sc.magnet_temp_C:g} °C", f" · magnet {sc.magnet_temp_C:g} degC")
        if res["mode"] == "forward":
            diag = "" if res["forward"].get("accepted") else tr(" · 진단용 (DIAGNOSTIC ONLY)", " · DIAGNOSTIC ONLY")
            title += f" · id = {self.id.value():g} A, iq = {self.iq.value():g} A ({tr('정방향 평가', 'forward evaluation')}){diag}"
            if res["pv"] is not None:
                pl["policy_point"] = res["plane"].get("picked_point")
                pl["point_label"] = tr("선택한 운전점 (정방향 평가, 이동 없음)", "picked point (forward evaluation, not moved)")
        else:
            title += f" · T = {res['T']:g} N·m ({tr('최소전류 정책', 'minimum-current policy')})"
        self.views.show_plane(pl, title=title)
        rows = []
        if res["mode"] == "forward":
            fwd = res["forward"]
            if not fwd["evaluable"]:
                rows.append((tr("정방향 평가", "forward evaluation"), "UNKNOWN", fwd["message"]))
            elif fwd["accepted"]:
                rows.append((tr("정방향 평가", "forward evaluation"), "ACCEPTED",
                             tr("모든 제약 충족, 모델 유효, 증거로 인정", "all constraints met, model valid: admissible evidence")))
            else:
                why = (fwd["issues"] + [f"violated: {', '.join(fwd['violated'])}" if fwd["violated"] else ""] +
                       [f"not evaluated: {', '.join(fwd['not_evaluated'])}" if fwd["not_evaluated"] else ""] +
                       fwd["gate_messages"])
                rows.append((tr("정방향 평가", "forward evaluation"), "DIAGNOSTIC ONLY",
                             tr("가능한 해가 아닌 진단값: ", "a diagnostic, not a feasible witness: ") +
                             "; ".join(w for w in why if w)))
            if not fwd["validity_gate"]:
                rows.append((tr("모델 유효성 gate", "model validity gate"), "UNKNOWN", "; ".join(fwd["issues"])))
        for c in res["claims"]:
            rows.append((c["name"], c["status"], c["detail"]))
        from ..theme import status_color
        colors = {(i, 1): status_color(r[1]) for i, r in enumerate(rows)}
        self.claims.set_rows(rows, colors)
        if res["pv"] is not None:
            self.views.show_point(res["pv"], title)
        else:
            for p in (self.views.overview, self.views.wave, self.views.phasor, self.views.power):
                p.placeholder(tr("운전점이 없습니다 (해 없음/모델 영역 밖).", "no operating point (no solution / outside model)"))

    def redraw(self):
        self.views.redraw()
