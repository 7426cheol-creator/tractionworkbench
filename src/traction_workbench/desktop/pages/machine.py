"""Machine design page (handoff section 10): changes around a validated reference, judged on system requirements.

Not a motor CAD / FEA: the page derives candidates from the ACTIVE validated drive model by coherent scaling
(turns / parallel paths, stack length, PM flux of constant-parameter data) with lineage and the list of data the
scaling invalidates, and judges every candidate on the same coupled requirement margins.  The winding tab checks
a slot / pole / pitch / parallel-path layout (star of slots) and hands its effective-turns ratio to the trade
study; the concept tab gives a rotor-volume envelope from a declared shear stress.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from ... import api
from ...i18n import tr
from ...plots import machine_figures as F
from ...insight.systems import concept_sizing_insight, machine_trade_insight, winding_insight
from ..widgets import (ConceptNote, KeyValueTable, NumTable, PlotPanel, check, error_box, fmt, hint, integer, number,
                       primary_button, table_with_buttons, with_reading)

KINDS = "capability | point | ugo | asc | copper"

NOTE = lambda: tr(
    "<b>모터 설계 (FEA 아님)</b>: 형상·비선형 자계·회전자 기계강도·감자는 외부(FEA/CAD) 근거입니다. 이 페이지는 "
    "<b>검증된 기준 모델</b>을 시스템 판정에 연결합니다.<br>"
    "• <b>일관 스케일링</b>: 직렬 턴(병렬 경로 포함) k_N, 적층 k_L, PM k_PM(상수 모델만)을 바꾸면 ψ_PM ∝ k_N k_L k_PM, "
    "L ∝ k_N²(k_L(1−e_L)+e_L), R ∝ k_N²(k_L(1−e_R)+e_R), 전류축·도메인 ∝ 1/k_N(같은 암페어-턴)가 <b>함께</b> 움직입니다. "
    "적층을 바꾸면 엔드권선 비율 e_R, e_L을 반드시 선언합니다(0 가정 금지).<br>"
    "• 스케일링으로 옮길 수 없는 것(AC 동손, 감자 한계, HF 기생, 회전자 기계, 열망, 새 자속밀도의 철손, 코깅)은 후보의 "
    "<b>무효화 데이터</b>로 표시합니다. 파생 후보는 '검증 안 됨'입니다.<br>"
    "• 후보는 부품 최고효율이 아니라 <b>같은 요구·온도·전원</b>의 결합 여유(저속 토크, 최소 Vdc 고속 토크, 요구점, UGO "
    "역기전력, 정상 ASC 전류, 동손)로 비교하며, 가장 작은 상대 여유가 구속 요구입니다.",
    "<b>Machine design (not FEA)</b>: geometry, nonlinear fields, rotor mechanics and demagnetisation are external "
    "(FEA / CAD) evidence. This page connects a <b>validated reference model</b> to system decisions.<br>"
    "• <b>Coherent scaling</b>: series turns (incl. parallel paths) k_N, stack k_L and PM k_PM (constant model only) "
    "move psi_PM ~ k_N k_L k_PM, L ~ k_N^2 (k_L (1-e_L) + e_L), R ~ k_N^2 (k_L (1-e_R) + e_R) and the current axes / "
    "domain ~ 1/k_N (same ampere-turns) <b>together</b>. A stack change needs the declared end-winding shares e_R, "
    "e_L (never assumed zero).<br>"
    "• What scaling cannot carry (AC copper loss, demag envelope, HF parasitics, rotor mechanics, thermal network, "
    "iron loss at a new flux density, cogging) is listed as <b>invalidated data</b>; a derived candidate is 'not "
    "validated'.<br>"
    "• Candidates are compared on coupled margins under the <b>same requirements, temperatures and sources</b> "
    "(low-speed torque, high-speed torque at min Vdc, a requirement point, UGO back-EMF, steady ASC current, copper "
    "loss), not on component peak efficiency; the smallest relative margin is the binding requirement.")

NOTE_W = lambda: tr(
    "<b>권선 (star of slots)</b>: 이층 권선, 60° 상대(belt). 가능 조건 Q/(3t) 정수 (t = gcd(Q, p)), 평형은 세 상의 코일변 수가 "
    "같고 축이 120° 간격. 스펙트럼은 평형 3상 전류의 MMF 계수(3의 배수 차수 상쇄, 회전 방향 표시)이며, p보다 낮은 차수는 "
    "서브하모닉(분수슬롯: 회전자 손실·NVH 위험, 정량화 안 함). 병렬 경로 a는 동일 구간 수(t, Q/t가 짝수면 2t)의 약수여야 "
    "합니다. 권선계수는 손실·AC 저항·NVH·제작성을 승인하지 않습니다. 같은 Q·p·y에서 턴/병렬 경로만 바꾸면 유효 턴 비 "
    "k_N = (k_w1 N)'/(k_w1 N)를 트레이드 스터디 후보로 보낼 수 있습니다 — 단 <b>기준·후보 모두 유효한 권선</b>(가능·평형·대칭 병렬 경로)이고 "
    "극쌍수가 현재 기계와 같을 때만. 현재 모델이 권선을 선언하면 기준이 바로 그 권선이어야 하고(계보가 후보에 결속), 선언하지 않으면 "
    "k_N은 이 기계의 권선 재설계가 아닌 <b>일반적 사고 실험</b>으로 표시됩니다.",
    "<b>Winding (star of slots)</b>: double layer, 60-degree phase belts. Feasible iff Q/(3t) is an integer (t = "
    "gcd(Q, p)); balanced iff the three phases get equal coil sides with axes 120 degrees apart. The spectrum is "
    "the MMF factor for balanced three-phase currents (triplen orders cancel, direction shown); orders below p are "
    "sub-harmonics (fractional slot: rotor loss / NVH risk, not quantified). Parallel paths a must divide the "
    "number of identical sections (t, or 2t when Q/t is even). A winding factor approves neither losses, AC "
    "resistance, NVH nor manufacturability. With the same Q, p, y and only turns / paths changed, the effective-"
    "turns ratio k_N = (k_w1 N)'/(k_w1 N) can be sent to the trade study - only when <b>both layouts are valid "
    "windings</b> (feasible, balanced, symmetric paths) and the pole pairs are the machine's. When the active model "
    "declares its winding the reference must be that winding (the lineage is bound to the candidate); when it does not, "
    "the k_N is a <b>generic thought experiment</b>, not a redesign of this machine's winding.")

NOTE_S = lambda: tr(
    "<b>개념 사이징</b>: 공극 전단응력 σ(냉각 등급 등으로 선언한 범위)에서 T = 2σV_r → 회전자 체적, L/D로 D·L. "
    "정격이 아닌 개념 범위이며 열·감자·회전자 기계(원주속도는 참고)·손실은 별도 근거가 필요합니다.",
    "<b>Concept sizing</b>: from a declared air-gap shear stress sigma (a range, e.g. by cooling class) T = 2 sigma V_r "
    "gives the rotor volume and, with L/D, D and L. A concept envelope, not a rating: thermal, demagnetisation, rotor "
    "mechanics (tip speed is indicative) and losses need their own evidence.")


def _task(fn):
    def run(progress, body):
        progress(0.1, tr("계산 중", "computing"))
        return fn(body)
    return run


def _scroll(w):
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(w)
    sc.setMinimumWidth(420)
    return sc


def _floats(text: str, name: str) -> list[float]:
    try:
        vals = [float(x) for x in text.replace(";", " ").split() if x.strip()]
    except ValueError:
        raise ValueError(tr(f"{name}: 공백으로 구분한 숫자 목록이 필요합니다", f"{name}: a space-separated list of numbers "
                                                                 "is required")) from None
    if not vals:
        raise ValueError(tr(f"{name}: 값이 없습니다", f"{name}: no values"))
    return vals


class MachinePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.last_trade = self.last_wind = self.last_size = None
        self.cand_lineage = {}              # candidate name -> the winding gate's hand-over (basis, winding lineage)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._trade_tab(), tr("스케일링 트레이드 스터디", "scaling trade study"))
        self.tabs.addTab(self._wind_tab(), tr("권선 (star of slots)", "winding (star of slots)"))
        self.tabs.addTab(self._size_tab(), tr("개념 사이징", "concept sizing"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.tabs)

    # ================================================================== trade study
    def _trade_tab(self):
        ex = api.EXAMPLE_MACHINE
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("후보 (현재 모델 = 검증된 기준에서 파생)", "candidates (derived from the active validated model)"))
        gl = QVBoxLayout(g)
        self.t_cand = NumTable(["name", "k_N", "k_L", "k_PM", "e_R", "e_L"], min_height=150, text_cols=(0,),
                               optional_cols=(4, 5))
        self.t_cand.load([[c["name"], c.get("k_turns", 1.0), c.get("k_stack", 1.0), c.get("k_pm", 1.0),
                           c.get("end_R_share"), c.get("end_L_share")] for c in ex["candidates"]])
        gl.addWidget(table_with_buttons(self.t_cand, tr(
            "k_N 직렬 유효 턴 비(병렬 경로 포함), k_L 적층 비, k_PM PM 자속 비(상수 모델만), e_R·e_L 엔드권선 비율(적층 변경 시 "
            "필수, 빈칸 = 미선언). 1,1,1 행이 기준입니다.",
            "k_N effective series-turns ratio (incl. parallel paths), k_L stack ratio, k_PM PM-flux ratio (constant model "
            "only), e_R / e_L end-winding shares (required for a stack change; blank = not declared). The 1, 1, 1 row "
            "is the reference.")))
        v.addWidget(g)
        g = QGroupBox(tr("요구 검사 (모든 후보에 같은 조건)", "requirement checks (same conditions for every candidate)"))
        gl = QVBoxLayout(g)
        self.t_chk = NumTable(["name", "kind", "n [rpm]", "Vdc [V]", "T [N·m]", "limit"], min_height=170,
                              text_cols=(0, 1), optional_cols=(4, 5))
        self.t_chk.load([[c["name"], c["kind"], c["speed_rpm"], c["Vdc_V"], c.get("torque_Nm"), c.get("limit")]
                         for c in ex["checks"]])
        gl.addWidget(table_with_buttons(self.t_chk, tr(
            f"kind: {KINDS}. capability = 정책 최대 토크 ≥ T, point = T 요구점(전류 여유), ugo = 개방 선간 역기전력 ≤ limit V "
            "(빈칸이면 Vdc), asc = 정상 ASC 전류 ≤ limit A, copper = T에서 동손 ≤ limit W. 한도 빈칸은 UNKNOWN (통과 아님).",
            f"kind: {KINDS}. capability = policy max torque >= T, point = requirement point T (current margin), ugo = "
            "open-circuit line back-EMF <= limit V (blank: Vdc), asc = steady ASC current <= limit A, copper = copper loss "
            "at T <= limit W. A blank limit is UNKNOWN (never a pass).")))
        v.addWidget(g)
        g = QGroupBox(tr("T–n 포락선 (표시용)", "T-n envelope (display)"))
        f = QFormLayout(g)
        self.t_env_on = check(tr("후보별 포락선 계산", "compute the envelope per candidate"), True)
        self.t_env_vdc = number(ex["envelope_Vdc_V"], 1, 2000, "V", 1, 10)
        self.t_env_n = QLineEdit(" ".join(f"{x:g}" for x in ex["envelope_speeds_rpm"]))
        for lab, w in (("", self.t_env_on), ("Vdc", self.t_env_vdc), (tr("속도 [rpm]", "speeds [rpm]"), self.t_env_n)):
            f.addRow(lab, w)
        v.addWidget(g)
        self.t_btn = primary_button(tr("트레이드 스터디 실행", "run the trade study"))
        self.t_btn.clicked.connect(self.run_trade)
        v.addWidget(self.t_btn)
        v.addWidget(hint(tr("예시 요구·한도(UGO 900 V, ASC 600 A, 동손 6 kW)와 엔드권선 비율은 합성 값입니다. 전원 한도는 "
                            "현재 모델 설정을 씁니다.", "Example requirements, limits (UGO 900 V, ASC 600 A, copper 6 kW) and "
                            "end-winding shares are synthetic. Source limits come from the active model settings.")))
        v.addWidget(ConceptNote(NOTE()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(('machine_trade',), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.p_trade = PlotPanel(hint=tr("'트레이드 스터디 실행'을 누르세요", "press 'run the trade study'"))
        self.k_trade = KeyValueTable()
        rl.addWidget(self.p_trade, 3)
        rl.addWidget(self.k_trade, 2)
        self.trade_tabs, self.i_trade = with_reading(right, tr(
            "실행하면 해석이 표시됩니다 — 후보별 상대 여유와 구속 항목, 기준 대비 무엇이 좋아지고 나빠지는지, 파생 후보에서 무효가 되는 데이터.",
            "Run to read the study — each candidate's relative margins and binding item, what improves and what worsens "
            "against the reference, the data a derived candidate invalidates."))
        split.addWidget(self.trade_tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([470, 990])
        return split

    def trade_body(self) -> dict:
        b = copy.deepcopy(api.EXAMPLE_MACHINE)
        cands = []
        for name, kN, kL, kPM, eR, eL in self.t_cand.values():
            c = {"name": name or f"C{len(cands) + 1}", "k_turns": kN, "k_stack": kL, "k_pm": kPM,
                 "end_R_share": eR, "end_L_share": eL, "basis": "entered in the machine-design page"}
            lin = self.cand_lineage.get(c["name"])
            if lin and kN is not None and abs(float(kN) - lin["k_turns"]) <= 1e-9 * lin["k_turns"]:
                c["basis"] = lin["basis"]                       # sent from the winding gate, k_N unchanged
                if lin.get("winding_from"):
                    c.update(winding_from=lin["winding_from"], winding_to=lin["winding_to"])
            cands.append(c)
        checks = []
        for name, kind, n, vdc, T, lim in self.t_chk.values():
            checks.append({"name": name or kind, "kind": kind.strip().lower(), "speed_rpm": n, "Vdc_V": vdc,
                           "torque_Nm": T, "limit": lim})
        b.update({"candidates": cands, "checks": checks, "envelope_Vdc_V": self.t_env_vdc.value(),
                  "envelope_speeds_rpm": _floats(self.t_env_n.text(), tr("속도", "speeds")) if self.t_env_on.isChecked() else []})
        b.update(self.win.state.body())
        return b

    def _start(self, key, label, fn, show, body, btn):
        btn.setEnabled(False)
        self.win.runner.run(key, label, _task(fn), show, body, on_error=self._err)

    def _err(self, msg, tb):
        for b in (self.t_btn, self.w_btn, self.s_btn):
            b.setEnabled(True)
        error_box(self, tr("계산 실패", "failed"), msg, tb)

    def run_trade(self):
        try:
            body = self.trade_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self._start("machine_trade", tr("트레이드 스터디", "trade study"), api.machine_trade, self._show_trade, body,
                    self.t_btn)

    def _show_trade(self, res):
        self.t_btn.setEnabled(True)
        self.last_trade = res
        rows = [r for r in res["rows"] if "checks" in r]
        names = [c["name"] for c in res["checks"]]

        def csv(rows=rows, names=names):
            out = {"candidate": [r["candidate"] for r in rows], "binding": [r["binding"] for r in rows]}
            for n in names:
                out[f"{n} status"] = [r["checks"][n]["status"] for r in rows]
                out[f"{n} value"] = [r["checks"][n]["value"] for r in rows]
                out[f"{n} rel_margin"] = [r["checks"][n].get("rel_margin") for r in rows]
            return out
        if rows:
            self.p_trade.draw(F.fig_machine_trade, res, name="machine_trade_study", csv=csv)
        else:
            self.p_trade.placeholder(tr("유효한 후보가 없습니다", "no valid candidate"))
        ref = res["reference"]
        out = [(tr("기준", "reference"), f"{ref['drive_id']} rev {ref['revision']} · {ref['flux_model']} · p = {ref['pole_pairs']}"
                                         f" · {tr('인버터', 'inverter')} {fmt(ref['inverter_limit_A'])} A · {ref['validation_status']}")]
        for r in res["rows"]:
            if "error" in r:
                out.append((r["candidate"], tr("거부: ", "refused: ") + r["error"]))
                continue
            par = r["parameters"]
            lin = r["lineage"]
            st = tr("모두 FEASIBLE", "all FEASIBLE") if r["all_feasible"] else ", ".join(
                f"{k}: {v['status']}" for k, v in r["checks"].items() if v["status"] != "FEASIBLE")
            out.append((r["candidate"], f"{tr('구속', 'binding')}: {r['binding']} · {st}"))
            out.append(("   " + tr("파라미터", "parameters"),
                        f"ψ_PM {fmt(par['psi_pm_Wb'], 4)} Wb, Ld {fmt(par['Ld_H'] and par['Ld_H'] * 1e6, 4)} µH, "
                        f"Lq {fmt(par['Lq_H'] and par['Lq_H'] * 1e6, 4)} µH, Rs {fmt(par['Rs_ohm'] * 1e3, 4)} mΩ"
                        if par.get("psi_pm_Wb") is not None else f"flux map (scaled), Rs {fmt(par['Rs_ohm'] * 1e3, 4)} mΩ"))
            if lin["derived"]:
                out.append(("   " + tr("스케일링", "scaling"), "; ".join(lin["carried"])))
                out.append(("   " + tr("무효화 데이터", "invalidated data"), "; ".join(lin["invalidated"])))
        self.k_trade.set_rows(out)
        self.i_trade.read("machine_trade", tr("트레이드 스터디", "trade study"), machine_trade_insight, res)

    # ================================================================== winding
    def _wind_tab(self):
        ex = api.EXAMPLE_WINDING
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("권선 (이층, 3상)", "winding (double layer, 3 phases)"))
        f = QFormLayout(g)
        self.w_Q = integer(ex["Q"], 3, 400, tip=tr("슬롯 수", "slots"))
        self.w_p = integer(ex["p"], 1, 60, tip=tr("극쌍 수 (현재 모델과 비교)", "pole pairs (compared with the active model)"))
        self.w_y_auto = check(tr("코일 피치 자동 (≈ Q/2p)", "coil pitch automatic (~ Q/2p)"), False)
        self.w_y = integer(ex["y"], 1, 399, tr(" 슬롯", " slots"))
        self.w_y_auto.toggled.connect(lambda on: self.w_y.setEnabled(not on))
        self.w_a = integer(ex["parallel_paths"], 1, 64)
        self.w_nc = integer(ex["turns_per_coil"], 1, 500)
        self.w_h = integer(ex["harmonics"], 3, 60)
        for lab, w in (("Q", self.w_Q), ("p", self.w_p), ("", self.w_y_auto), (tr("코일 피치 y", "coil pitch y"), self.w_y),
                       (tr("병렬 경로 a", "parallel paths a"), self.w_a), (tr("코일당 턴", "turns per coil"), self.w_nc),
                       (tr("최대 전기 차수", "max electrical order"), self.w_h)):
            f.addRow(lab, w)
        v.addWidget(g)
        g = QGroupBox(tr("대안 권선 (같은 Q·p·y)", "alternative winding (same Q, p, y)"))
        f = QFormLayout(g)
        self.w_cmp_on = check(tr("턴/병렬 경로 변경 비교", "compare a turns / parallel-path change"), True)
        self.w_nc2 = integer(ex["compare"]["turns_per_coil"], 1, 500)
        self.w_a2 = integer(ex["compare"]["parallel_paths"], 1, 64)
        for lab, w in (("", self.w_cmp_on), (tr("코일당 턴", "turns per coil"), self.w_nc2), (tr("병렬 경로", "parallel paths"), self.w_a2)):
            f.addRow(lab, w)
        v.addWidget(g)
        row = QHBoxLayout()
        self.w_btn = primary_button(tr("권선 계산", "compute the winding"))
        self.w_btn.clicked.connect(self.run_wind)
        self.w_send = QPushButton(tr("k_N → 트레이드 후보", "k_N -> trade candidate"))
        self.w_send.setMinimumHeight(34)
        self.w_send.setEnabled(False)
        self.w_send.clicked.connect(self.send_candidate)
        row.addWidget(self.w_btn)
        row.addWidget(self.w_send)
        v.addLayout(row)
        v.addWidget(ConceptNote(NOTE_W()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(('winding',), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.p_wind = PlotPanel(hint=tr("'권선 계산'을 누르세요", "press 'compute the winding'"))
        self.k_wind = KeyValueTable()
        rl.addWidget(self.p_wind, 3)
        rl.addWidget(self.k_wind, 2)
        self.wind_tabs, self.i_wind = with_reading(right, tr(
            "계산하면 해석이 표시됩니다 — 기본파·고조파 권선계수와 단절권 효과, 슬롯 고조파, 유효 턴, 일관성 검사, 대안 권선의 k_N.",
            "Run to read the winding — fundamental and harmonic winding factors and the pitch effect, slot harmonics, "
            "effective turns, consistency checks, the k_N of the alternative."))
        split.addWidget(self.wind_tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 1040])
        return split

    def wind_body(self) -> dict:
        b = {"Q": self.w_Q.value(), "p": self.w_p.value(), "y": None if self.w_y_auto.isChecked() else self.w_y.value(),
             "parallel_paths": self.w_a.value(), "turns_per_coil": self.w_nc.value(), "harmonics": self.w_h.value(),
             "compare": ({"turns_per_coil": self.w_nc2.value(), "parallel_paths": self.w_a2.value()}
                         if self.w_cmp_on.isChecked() else None)}
        b.update(self.win.state.body())
        return b

    def run_wind(self):
        try:
            body = self.wind_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self._start("winding", tr("권선", "winding"), api.winding, self._show_wind, body, self.w_btn)

    def _show_wind(self, w):
        self.w_btn.setEnabled(True)
        self.last_wind = w
        self.p_wind.draw(F.fig_winding, w, name="winding_star_of_slots",
                         csv=lambda w=w: {"slot": list(range(1, w["Q"] + 1)), "angle_el_deg": w["slot_angles_deg"],
                                          "top": w["slot_top"], "bottom": w["slot_bottom"]})
        ok = lambda x: "OK" if x else tr("아니오", "NO")      # noqa: E731
        rows = [(tr("기본파 권선계수 kw1", "fundamental winding factor kw1"), f"{w['kw1']:.5f}"),
                (tr("형식", "type"), f"{w['type']}, q = {w['q']:.4g}, t = {w['t_periodicity']}, y = {w['y']} "
                                   f"(y/τ = {w['y_over_pole_pitch']:.3f})"),
                (tr("상순", "phase sequence"), w["phase_sequence"]),
                (tr("상 축 [전기°]", "phase axes [el. deg]"), ", ".join(f"{k} {v:.1f}" for k, v in w["phase_axes_deg"].items())),
                (tr("코일변 수", "coil sides"), w["coil_sides"]),
                (tr("서브하모닉", "sub-harmonics"), ", ".join(f"ν_m {h['order_mech']}: {h['kw']:.3f}" for h in w["subharmonics"])
                 or tr("없음", "none")),
                (tr("코깅 지표 LCM(Q, 2p)", "cogging indicator LCM(Q, 2p)"), w["cogging_lcm_Q_2p"])]
        for c in w.get("consistency", []):
            rows.append((c["item"], f"{ok(c['ok'])} — {c['detail']}"))
        if "N_series" in w:
            rows.append((tr("직렬 턴 / 유효 턴", "series / effective turns"), f"{fmt(w['N_series'])} / {w['N_eff']:.3f}"))
        cmp = w.get("compare")
        if cmp:
            if cmp["sendable"]:
                rows.append((tr("대안: 유효 턴 비 k_N", "alternative: effective-turns ratio k_N"),
                             f"{cmp['k_turns']:.4f} ({cmp['turns_per_coil']}/coil, {cmp['parallel_paths']} paths, "
                             f"N_eff {cmp['N_eff']:.3f}) — {cmp['kind']}"))
            else:
                rows.append((tr("대안: k_N 보내기 거부", "alternative: k_N not handed over"), "; ".join(cmp["refusals"])))
        rows.append((tr("의미", "meaning"), w["note"]))
        self.k_wind.set_rows(rows)
        self.i_wind.read("winding", tr("권선", "winding"), winding_insight, w)
        self.w_send.setEnabled(bool(cmp and cmp["sendable"]))

    def send_candidate(self):
        cmp = (self.last_wind or {}).get("compare")
        if not cmp or not cmp.get("sendable"):          # the same gate as the API / core: never a refused k_N
            return
        c = cmp["candidate"]
        self.cand_lineage[c["name"]] = c
        self.t_cand.add_row([c["name"], c["k_turns"], 1.0, 1.0, None, None])
        self.tabs.setCurrentIndex(0)

    # ================================================================== concept sizing
    def _size_tab(self):
        ex = api.EXAMPLE_SIZING
        split = QSplitter(Qt.Horizontal)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 6, 0)
        g = QGroupBox(tr("선언 입력", "declared inputs"))
        f = QFormLayout(g)
        self.s_T = number(ex["T_Nm"], 1, 1e5, "N·m", 1, 10)
        self.s_sig = QLineEdit(" ".join(f"{x:g}" for x in ex["sigma_kPa"]))
        self.s_asp = QLineEdit(" ".join(f"{x:g}" for x in ex["aspect_L_over_D"]))
        self.s_n = number(ex["n_max_rpm"], 0, 1e5, "rpm", 0, 500)
        self.s_v = number(ex["tip_speed_limit_m_s"], 0, 1e3, "m/s", 1, 5)
        self.s_basis = QLineEdit(ex["basis"])
        for lab, w in ((tr("토크 (회전자 기준)", "torque (rotor)"), self.s_T), (tr("전단응력 σ [kPa]", "shear stress σ [kPa]"), self.s_sig),
                       ("L/D", self.s_asp), (tr("최고 속도", "max speed"), self.s_n), (tr("원주속도 한계", "tip-speed limit"), self.s_v),
                       (tr("근거", "basis"), self.s_basis)):
            f.addRow(lab, w)
        v.addWidget(g)
        self.s_btn = primary_button(tr("개념 사이징", "concept sizing"))
        self.s_btn.clicked.connect(self.run_size)
        v.addWidget(self.s_btn)
        v.addWidget(ConceptNote(NOTE_S()))
        v.addStretch(1)
        split.addWidget(_scroll(form))
        self.win.track_inputs(('concept_sizing',), form)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.p_size = PlotPanel(hint=tr("'개념 사이징'을 누르세요", "press 'concept sizing'"))
        self.k_size = KeyValueTable(headers=["σ [kPa]", "L/D", "V_r [L]", "D [mm]", "L [mm]", tr("원주속도 [m/s]", "tip speed [m/s]")])
        rl.addWidget(self.p_size, 3)
        rl.addWidget(self.k_size, 2)
        self.size_tabs, self.i_size = with_reading(right, tr(
            "계산하면 해석이 표시됩니다 — 전단응력·종횡비에 따른 회전자 크기와 팁 속도 한계 안에 드는 조합.",
            "Run to read the sizes — rotor size over shear stress and aspect ratio, and which combinations stay within "
            "the tip-speed limit."))
        split.addWidget(self.size_tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 1040])
        return split

    def size_body(self) -> dict:
        return {"T_Nm": self.s_T.value(), "sigma_kPa": _floats(self.s_sig.text(), "σ"),
                "aspect_L_over_D": _floats(self.s_asp.text(), "L/D"), "n_max_rpm": self.s_n.value() or None,
                "tip_speed_limit_m_s": self.s_v.value() or None, "basis": self.s_basis.text().strip()}

    def run_size(self):
        try:
            body = self.size_body()
        except Exception as exc:  # noqa: BLE001
            error_box(self, tr("입력 오류", "input error"), str(exc))
            return
        self._start("concept_sizing", tr("개념 사이징", "concept sizing"), api.concept_sizing, self._show_size, body,
                    self.s_btn)

    def _show_size(self, cs):
        self.s_btn.setEnabled(True)
        self.last_size = cs
        rows = cs["rows"]
        self.p_size.draw(F.fig_concept_sizing, cs, name="concept_sizing",
                         csv=lambda rows=rows: {k: [r.get(k) for r in rows] for k in
                                                ("sigma_kPa", "L_over_D", "rotor_volume_L", "D_rotor_mm", "L_stack_mm",
                                                 "tip_speed_m_s")})
        self.k_size.set_rows([[fmt(r["sigma_kPa"]), fmt(r["L_over_D"]), fmt(r["rotor_volume_L"], 4), fmt(r["D_rotor_mm"], 4),
                               fmt(r["L_stack_mm"], 4),
                               (fmt(r["tip_speed_m_s"], 4) + ("" if r.get("tip_speed_ok", True) else tr(" (초과)", " (exceeded)")))
                               if "tip_speed_m_s" in r else "—"] for r in rows])
        self.i_size.read("concept_sizing", tr("개념 사이징", "concept sizing"), concept_sizing_insight, cs)

    def redraw(self):
        for p in (self.p_trade, self.p_wind, self.p_size, self.i_trade, self.i_wind, self.i_size):
            p.redraw()
