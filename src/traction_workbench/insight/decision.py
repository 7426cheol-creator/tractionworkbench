"""Engineering reading of a requirement decision (``DecisionRecord`` as a dict, optionally with the PWM screen at
the same operating point).

The reading follows the order an engineer asks in: is it met and by how much; which judged item decides it and why;
what limits at the operating point (voltage / current / DC) and how the power splits into losses; which limit moves
the answer most (dominance) and what change would make a failing requirement pass (relaxation, sizing); what the
result does not cover.  Numbers are the record's; the few derived quantities are identities of reported numbers
(shares, ratios, the rotational voltage ω_e·|ψ|) and are labelled as such.
"""

from __future__ import annotations

import math
import re

from ..i18n import tr
from ..analysis.variation import PARAMETERS
from ..plots.labels import claim_label, constraint_label, param_label, reason_label, state_label
from ..requirement_set import classify
from . import Insight, esc, kw, num, pct, q, status_level
from .texts import engine_text

_DC = ("DC_DISCHARGE_POWER", "DC_DISCHARGE_CURRENT", "DC_CHARGE_POWER", "DC_CHARGE_CURRENT")


def _cons(op: dict | None, name: str) -> dict | None:
    if not op:
        return None
    return next((c for c in op.get("constraints") or [] if c.get("name") == name), None)


def _cond_name(sc: dict) -> str:
    s = f"{num(sc['speed_rpm_mechanical'], 6)} rpm · Vdc {num(sc['Vdc_V_inverter_dc_terminal'], 5)} V"
    if sc.get("magnet_temp_C") is not None:
        s += tr(f" · 자석 {num(sc['magnet_temp_C'], 4)} °C", f" · magnet {num(sc['magnet_temp_C'], 4)} °C")
    return s


def _governing(rec: dict) -> int:
    """The condition that decides the reading: the worst margin among the conditions that carry the verdict."""
    conds = rec["conditions"]
    status = rec["verdict"]["status"]
    st = [c["requirement_claim_at_this_condition"]["status"] for c in conds]
    cand = [i for i, s in enumerate(st) if s == status] or list(range(len(conds)))

    def margin(i):
        m = conds[i].get("torque_capability_margin_Nm")
        return math.inf if m is None else m
    return min(cand, key=margin)


def _evidence(claim: dict, kind: str) -> list[dict]:
    return [e for e in claim.get("evidence") or [] if e.get("kind") == kind]


def _torque_words(T: float) -> tuple[str, str]:
    """(the request in words, the capability noun) for motoring / braking."""
    if T >= 0:
        return tr(f"구동 {num(T)} N·m", f"motoring {num(T)} N·m"), tr("최대 토크", "maximum torque")
    return tr(f"회생 제동 {num(-T)} N·m", f"regenerative braking {num(-T)} N·m"), tr("최대 제동 토크", "maximum braking torque")


PENDING = {"pwm": ("PWM 영향 (같은 운전점)", "PWM consequences at the same point"), "sizing": ("역설계", "sizing"),
           "dominance": ("병목 기여도", "bottleneck contribution"), "relaxation": ("요구 완화", "requirement relaxation"),
           "comparison": ("Vdc 비교", "Vdc comparison"), "curves": ("설계 곡선", "design curves")}


def pending_text(pending) -> str:
    """The stages of a decision still being computed, for people (in order, each once)."""
    return ", ".join(tr(*PENDING[k]) if k in PENDING else str(k) for k in dict.fromkeys(pending))


def decision_insight(rec: dict, pwm_risk: dict | None = None, pending=(), running: bool = True) -> Insight:
    """``pending``: the stages after the verdict that are still being computed (``running``) or that the run ended
    without (cancelled, failed): ``pwm``, ``sizing``, ``dominance``, ``relaxation``, ``comparison``, ``curves``.  The
    reading says so and suggests none of them."""
    v = rec["verdict"]
    req = rec["requirement"]
    conds = rec["conditions"]
    T = float(req["target_Nm"])
    gi = _governing(rec)
    c = conds[gi]
    sc = c["scenario"]
    ps = c["policy_solution"]
    op = ps.get("operating_point")
    pcap = c.get("policy_capability") or {}
    cap = pcap.get("achieved_value_Nm")
    margin = c.get("torque_capability_margin_Nm")
    what, cap_noun = _torque_words(T)
    if cap is not None and not pcap.get("certified"):
        cap_noun += tr(" (찾은 값, 인증 안 됨)", " (found, not certified)")
    multi = len(conds) > 1
    where = _cond_name(sc)
    verdict = v["verdict"]
    ins = Insight(headline="", verdict=verdict)

    # ------------------------------------------------------------------ headline
    mtxt = "—" if margin is None else (f"{num(margin)} N·m" + (f" ({pct(margin, abs(T))})" if T else ""))
    cap_txt = "—" if cap is None else f"{num(abs(cap))} N·m"
    scope_txt = (tr(f"{len(conds)}개 조건 중 가장 불리한 조건({where})", f"the worst of {len(conds)} conditions ({where})")
                 if multi else where)
    limited = [constraint_label(x) for x in pcap.get("active_constraints_at_witness") or []]
    if verdict == "PASS":
        ins.headline = tr(f"{what} @ {scope_txt}: <b>달성 가능</b> — {cap_noun} {cap_txt}, 여유 {mtxt}",
                          f"{what} @ {scope_txt}: <b>achievable</b> — {cap_noun} {cap_txt}, margin {mtxt}")
    elif verdict == "FAIL":
        short = "—" if margin is None else f"{num(-margin)} N·m" + (f" ({pct(-margin, abs(T))})" if T else "")
        ins.headline = tr(f"{what} @ {scope_txt}: <b>불가능 (증명됨)</b> — 이 조건의 {cap_noun} {cap_txt}, 부족 {short}",
                          f"{what} @ {scope_txt}: <b>not achievable (proven)</b> — {cap_noun} here {cap_txt}, short by "
                          f"{short}")
    else:
        cls = classify(v["status"], v["reasons"])
        why = ", ".join(reason_label(r) for r in v["reasons"]) or tr(cls["label_ko"], cls["label_en"])
        static_ok = all(cl["status"] == "FEASIBLE" for cl in ps.get("claims") or []) and (margin or 0) >= 0
        if static_ok and c.get("duration_claim"):
            ins.headline = tr(f"{what} @ {scope_txt}: <b>미확정</b> — 정적으로는 가능 (여유 {mtxt}), 지속시간 근거 없음",
                              f"{what} @ {scope_txt}: <b>undecided</b> — statically achievable (margin {mtxt}), no "
                              f"duration evidence")
        else:
            ins.headline = tr(f"{what} @ {scope_txt}: <b>미확정</b> — {why}", f"{what} @ {scope_txt}: <b>undecided</b> — {why}")

    # ------------------------------------------------------------------ key numbers (cards)
    ins.metrics.append((tr("판정", "verdict"), verdict, status_level(verdict)))
    if cap is not None:
        ins.metrics.append((cap_noun + (tr(" (인증됨)", " (certified)") if pcap.get("certified") else ""), cap_txt,
                            "info"))
    if margin is not None:
        ins.metrics.append((tr("토크 여유", "torque margin"), mtxt, "ok" if margin >= 0 else "bad"))
    if limited:
        ins.metrics.append((tr("한계를 정하는 제약", "limited by"), ", ".join(limited), "info"))
    if op and op.get("efficiency") is not None:
        ins.metrics.append((tr("효율 (정책점)", "efficiency (policy point)"), f"{100 * op['efficiency']:.1f} %", "info"))

    if pending and running:
        ins.section(tr("아직 계산 중", "still being computed"),
                    tr("판정은 위와 같이 확정됐습니다. 아래 항목은 계산이 끝나면 이 해석에 더해집니다.",
                       "the verdict above is final; these are added to this reading when they finish.")).add(
            pending_text(pending), "open")
    elif pending:
        ins.section(tr("계산되지 않은 항목", "not computed"),
                    tr("이 실행은 판정 뒤 단계가 끝나기 전에 멈췄습니다 (취소 또는 오류). 판정은 완료된 결과이고, 아래 항목은 다시 "
                       "실행하면 계산됩니다.",
                       "this run stopped before the stages after its verdict finished (cancelled or failed). The "
                       "verdict is complete; these are computed when you run again.")).add(pending_text(pending), "open")
    _claims_section(ins, c, ps, op, T)
    if multi:
        _conditions_section(ins, rec, gi)
    if op:
        mechanism_section(ins, c, op, pcap, T)
        power_section(ins, op)
    an = rec.get("analyses") or {}
    dominance_section(ins, an.get("dominance"), T)
    fix_section(ins, an, verdict)
    if pwm_risk:
        pwm_section(ins, pwm_risk)
    _scope_section(ins, rec, op)
    _next_section(ins, rec, an, verdict, pending)
    return ins.nonempty()


# ---------------------------------------------------------------------- judged items
def _claims_section(ins: Insight, c: dict, ps: dict, op: dict | None, T: float) -> None:
    s = ins.section(tr("판정 항목별 분석", "the judged items"),
                    tr("요구 판정은 아래 항목의 AND입니다 — 증명된 위반이 하나라도 있으면 FAIL, 위반 없이 미확정이 있으면 UNKNOWN.",
                       "the verdict is the AND of these items — any proven violation gives FAIL, an open item without a "
                       "violation gives UNKNOWN."))
    for cl in ps.get("claims") or []:
        name, st = cl["name"], cl["status"]
        text, detail = _claim_reading(cl, op, T)
        s.add(f"<b>{claim_label(name)}</b>: {text}", status_level(st), detail)
    dc = c.get("duration_claim")
    if dc:
        st = dc["status"]
        if st == "UNKNOWN" and "UNVALIDATED_DURATION" in (dc.get("reasons") or []):
            text = tr("검증된 정격 포락선이나 검증된 열 모델이 없어 지속시간은 판정하지 않습니다 — 고정 온도의 정적 결과를 "
                      "지속시간 정격으로 바꿔 부르지 않습니다.",
                      "no validated rating envelope or thermal model applies, so the duration is not judged — a "
                      "fixed-temperature static result is not renamed a duration rating.")
        elif st == "FEASIBLE":
            text = tr("검증된 근거로 요구 지속시간을 만족합니다.", "met on validated evidence.")
        else:
            text = ", ".join(reason_label(r) for r in dc.get("reasons") or []) or st
        own = st == "UNKNOWN" and "UNVALIDATED_DURATION" in (dc.get("reasons") or [])
        s.add(f"<b>{claim_label('duration')}</b>: {text}", status_level(st), "" if own else esc(dc.get("detail", "")))


def _claim_reading(cl: dict, op: dict | None, T: float) -> tuple[str, str]:
    name, st = cl["name"], cl["status"]
    reasons = ", ".join(reason_label(r) for r in cl.get("reasons") or [])
    if name == "electrical_existence":
        if st == "FEASIBLE":
            pt = (tr(f" — 예: 정책 운전점 id = {q(op['id_A_peak'], 'A')}, iq = {q(op['iq_A_peak'], 'A')}",
                     f" — e.g. the policy point id = {q(op['id_A_peak'], 'A')}, iq = {q(op['iq_A_peak'], 'A')}")
                  if op else "")
            return tr("전압·전류·운전 영역 제약을 모두 만족하는 전기적 해가 있습니다", "an electrical solution meets the "
                      "voltage, current and domain limits") + pt, ""
        if st == "INFEASIBLE":
            for e in _evidence(cl, "analytic_necessary_condition"):
                d = e.get("data") or {}
                if "abs_vd_lower_bound_V" in d:
                    return (tr(f"불가능 — 선언된 id 범위 안에서 요구 토크에 필요한 최소 q축 전류 {q(d['iq_min_A'], 'A')}만으로도 "
                               f"d축 전압 |v_d| ≥ ω_e·Lq·iq_min = {q(d['abs_vd_lower_bound_V'], 'V')}가 명령 전압 예산 "
                               f"{q(d['voltage_budget_V'], 'V')}을 넘습니다. 전압(속도·Lq·Vdc)의 문제라 전류 한계를 키워도 "
                               f"해결되지 않습니다.",
                               f"impossible — the smallest q-axis current the torque needs in the declared id range, "
                               f"{q(d['iq_min_A'], 'A')}, alone gives |v_d| ≥ ω_e·Lq·iq_min = "
                               f"{q(d['abs_vd_lower_bound_V'], 'V')}, above the command budget "
                               f"{q(d['voltage_budget_V'], 'V')}: a voltage (speed, Lq, Vdc) problem that a larger "
                               f"current rating cannot fix."), esc(engine_text(d.get("scope", ""))))
            if _evidence(cl, "exact_boundary_enumeration"):
                return tr("불가능 — 토크 곡선 위 모든 제약 다항식 근과 구간을 검사해 가능한 점이 없음을 확인했습니다",
                          "impossible — every constraint-polynomial root and interval on the torque curve was "
                          "checked: no feasible point"), ""
        return (reasons or st), esc(cl.get("detail", ""))
    if name in ("policy_static", "dc_source"):
        if op is None:
            if st == "INFEASIBLE":
                return tr("전기적 해가 없어 정책이 고를 운전점이 없습니다", "no electrical solution, so no policy point"), ""
            return (reasons or st), esc(cl.get("detail", ""))
        viol = [x for x in op.get("constraints") or [] if x.get("state") == "VIOLATED"
                and (name == "policy_static" or x["name"] in _DC)]
        if viol:
            parts = [f"{constraint_label(x['name'])} {_amount(x)} ({tr('한계', 'limit')} {_amount(x, 'limit')}, "
                     f"{tr('초과', 'over by')} {_amount(x, 'excess')})" for x in viol]
            return tr("정책 운전점에서 위반: ", "violated at the policy point: ") + "; ".join(parts), ""
        if name == "policy_static":
            cur, vol = _cons(op, "CURRENT"), _cons(op, "VOLTAGE")
            bits = []
            if cur:
                bits.append(f"|i| = {q(cur['demand'], 'A')} / {q(cur['limit'], 'A')} ({pct(cur['demand'], cur['limit'])})")
            if vol:
                bits.append(tr(f"전압 {q(vol['demand'], 'V')} / {q(vol['limit'], 'V')} ({pct(vol['demand'], vol['limit'])})",
                               f"voltage {q(vol['demand'], 'V')} / {q(vol['limit'], 'V')} ({pct(vol['demand'], vol['limit'])})"))
            ok = tr("최소전류 정책 운전점이 DC를 포함한 모든 제약을 만족합니다", "the minimum-current policy point meets every "
                    "limit incl. DC")
            return (ok + " — " + ", ".join(bits)) if st == "FEASIBLE" else (reasons or st), ""
        # dc_source
        side = ("DC_DISCHARGE_POWER", "DC_DISCHARGE_CURRENT") if (op.get("Pdc_W") or 0) >= 0 else \
            ("DC_CHARGE_POWER", "DC_CHARGE_CURRENT")
        bits = []
        for n in side:
            x = _cons(op, n)
            if x and x.get("limit") is not None and math.isfinite(x["limit"]):
                bits.append(f"{constraint_label(n)}: {_amount(x)} / {_amount(x, 'limit')} ({pct(x['demand'], x['limit'])})")
        text = tr("평균 DC 전력·전류가 선언된 소스 한계 안입니다", "average DC power and current are within the declared "
                  "source limits") if st == "FEASIBLE" else (reasons or st)
        return text + (" — " + "; ".join(bits) if bits else ""), ""
    if name == "physical_existence_with_dc":
        if st == "FEASIBLE":
            return tr("정책 운전점 자체가 DC 한계와 양립합니다 (필요조건 통과)", "the policy point itself is DC-compatible "
                      "(necessary condition passed)"), ""
        for e in _evidence(cl, "analytic_necessary_condition"):
            d = e.get("data") or {}
            if "effective_discharge_cap_W" in d:
                cap = d["effective_discharge_cap_W"]
                why = (tr(f"= min(방전 전력 한계 {kw(d.get('discharge_power_max_W'))}, Vdc × 방전 전류 한계 "
                          f"{kw(d.get('Vdc_times_Idis_W'))})",
                          f"= min(discharge power limit {kw(d.get('discharge_power_max_W'))}, Vdc × discharge current "
                          f"limit {kw(d.get('Vdc_times_Idis_W'))})"))
                return (tr(f"불가능 — 요구 축출력 {kw(d['P_shaft_W'])}가 손실이 0이라 해도 유효 방전 한도 {kw(cap)} {why}를 "
                           f"넘습니다. 어떤 제어·모터 모델로도 불가능합니다.",
                           f"impossible — the requested shaft power {kw(d['P_shaft_W'])} exceeds the effective "
                           f"discharge cap {kw(cap)} {why} even with zero losses: no control or motor model helps."),
                        esc(engine_text(d.get("scope", ""))))
            if "max_possible_Pdc_W" in d:
                return (tr(f"불가능 — 회생 축출력 {kw(d['P_shaft_W'])}에서, 전류 한계에서 낼 수 있는 최대 손실 "
                           f"{kw(d['max_total_loss_W'])}을 빼도 DC로 돌아가는 전력이 최소 {kw(-d['max_possible_Pdc_W'])}로 "
                           f"충전 한계 {kw(-d['charge_cap_W'])}를 넘습니다.",
                           f"impossible — at the braking shaft power {kw(d['P_shaft_W'])}, even the largest loss the "
                           f"current limit allows ({kw(d['max_total_loss_W'])}) leaves at least "
                           f"{kw(-d['max_possible_Pdc_W'])} flowing back, above the charge limit "
                           f"{kw(-d['charge_cap_W'])}."), esc(engine_text(d.get("scope", ""))))
        return (reasons or st), esc(cl.get("detail", ""))
    return (reasons or tr("가능", "feasible") if st == "FEASIBLE" else reasons or st), esc(cl.get("detail", ""))


def _amount(x: dict, which: str = "demand") -> str:
    unit = str(x.get("unit") or "")
    if which == "excess":
        val = -float(x["slack"])
    else:
        val = float(x[which])
    if unit.startswith("W"):
        return kw(abs(val) if which == "excess" else val)
    u = unit.split(" ")[0]
    return q(val, u)


# ---------------------------------------------------------------------- range / several conditions
def _conditions_section(ins: Insight, rec: dict, gi: int) -> None:
    s = ins.section(tr("조건별 결과", "per condition"),
                    tr("범위·온도 plane 등 요구의 모든 조건에서 판정합니다. 가장 불리한 조건이 위 해석의 기준입니다.",
                       "every condition of the requirement is judged; the worst one is read above."))
    for i, c in enumerate(rec["conditions"]):
        st = c["requirement_claim_at_this_condition"]["status"]
        m = c.get("torque_capability_margin_Nm")
        mark = tr(" ← 기준", " ← read above") if i == gi else ""
        s.add(f"{_cond_name(c['scenario'])}: {state_label(st)}"
              + ("" if m is None else tr(f", 여유 {num(m)} N·m", f", margin {num(m)} N·m")) + mark, status_level(st))
    for qual in rec["verdict"].get("qualifiers") or []:
        m = re.match(r"static claim certified for every Vdc in \[(.*?)\] V from the low endpoint: (.*)", qual)
        if m:
            s.add(tr(f"정적 판정은 저전압 끝점 결과로 Vdc [{m.group(1)}] V 전 구간에 대해 인증됩니다 — 전압이 오르면 예산이 커지고 "
                     f"전류·영역·P_dc는 그대로이며 I_dc는 줄어듭니다 (단조성 조건이 성립할 때)",
                     f"the static claim is certified for every Vdc in [{m.group(1)}] V from the low endpoint (monotonicity "
                     f"conditions hold)"), "ok", esc(m.group(2)))
        else:
            s.add(esc(engine_text(qual)), "info")


# ---------------------------------------------------------------------- limiting mechanism at the point
def mechanism_section(ins: Insight, c: dict, op: dict, pcap: dict, T: float) -> None:
    s = ins.section(tr("무엇이 한계인가 — 운전점 분석", "what limits — the operating point"))
    vol, cur = _cons(op, "VOLTAGE"), _cons(op, "CURRENT")
    vinfo = op.get("voltage") or {}
    if vol:
        used = pct(vol["demand"], vol["limit"])
        if vol.get("state") == "ACTIVE":
            s.add(tr(f"<b>전압 한계에 닿아 있음</b>: 명령 전압 {q(vol['demand'], 'V')} = 예산 {q(vol['limit'], 'V')} ({used}); "
                     f"예산은 하드웨어 상한 {q(vinfo.get('hardware_ceiling_V_peak'), 'V')}에서 예비 "
                     f"{q(vinfo.get('reserve_V'), 'V')}를 뺀 값입니다.",
                     f"<b>at the voltage limit</b>: command {q(vol['demand'], 'V')} = budget {q(vol['limit'], 'V')} "
                     f"({used}); the budget is the hardware ceiling {q(vinfo.get('hardware_ceiling_V_peak'), 'V')} "
                     f"less the reserve {q(vinfo.get('reserve_V'), 'V')}."), "warn")
        else:
            s.add(tr(f"전압 여유 {q(vol['slack'], 'V')} — 명령 전압 {q(vol['demand'], 'V')} / 예산 {q(vol['limit'], 'V')} ({used})",
                     f"voltage margin {q(vol['slack'], 'V')} — command {q(vol['demand'], 'V')} of {q(vol['limit'], 'V')} ({used})"),
                  "ok" if vol.get("state") == "SATISFIED" else "bad")
    we, pd, pq = op.get("omega_e_rad_s"), op.get("psi_d_Wb"), op.get("psi_q_Wb")
    if we and pd is not None and pq is not None and op.get("v_phase_peak_V"):
        emf = abs(we) * math.hypot(pd, pq)
        gen = emf > op["v_phase_peak_V"]
        s.add(tr(f"회전에 의한 전압 ω_e·|ψ| = {q(emf, 'V')} (상전압 {q(op['v_phase_peak_V'], 'V')}의 "
                 f"{pct(emf, op['v_phase_peak_V'])}) — 전기 주파수 {q(op.get('f_e_Hz'), 'Hz')}에서 전압은 쇄교 자속이 정합니다"
                 + (" (발전 중이라 R_s 강하만큼 단자 전압이 더 낮음)." if gen else "."),
                 f"rotational voltage ω_e·|ψ| = {q(emf, 'V')} ({pct(emf, op['v_phase_peak_V'])} of the phase voltage "
                 f"{q(op['v_phase_peak_V'], 'V')}) — at f_e = {q(op.get('f_e_Hz'), 'Hz')} the flux linkage sets the "
                 f"voltage" + (" (generating: the terminal voltage is lower by the R_s drop)." if gen else ".")), "info",
              tr("ω_e와 ψ_d, ψ_q는 운전점의 보고값 (항등식 v = R_s·i + jω_e·ψ의 회전 항)",
                 "ω_e, ψ_d, ψ_q as reported at the point (the rotational term of v = R_s·i + jω_e·ψ)"))
    if vol and vol.get("state") == "ACTIVE" and (op.get("id_A_peak") or 0) < 0:
        s.add(tr(f"<b>약계자 운전</b>: id = {q(op['id_A_peak'], 'A')}로 d축 자속을 줄여 전압 한계 안에 머뭅니다. 이 전류는 "
                 f"토크를 직접 만들지 않고 전압을 맞추는 데 쓰입니다.",
                 f"<b>field weakening</b>: id = {q(op['id_A_peak'], 'A')} reduces the d-axis flux to stay within the "
                 f"voltage limit; this current buys voltage, not torque."), "info")
    if cur:
        state = cur.get("state")
        s.add(tr(f"전류 |i| = {q(cur['demand'], 'A')} / {q(cur['limit'], 'A')} ({pct(cur['demand'], cur['limit'])}), "
                 f"여유 {q(cur['slack'], 'A')}",
                 f"current |i| = {q(cur['demand'], 'A')} of {q(cur['limit'], 'A')} ({pct(cur['demand'], cur['limit'])}), "
                 f"margin {q(cur['slack'], 'A')}"),
              "warn" if state == "ACTIVE" else "bad" if state == "VIOLATED" else "ok")
    for n in _DC:
        x = _cons(op, n)
        if x is None or x.get("limit") is None or not math.isfinite(float(x["limit"])):
            continue
        if (n.startswith("DC_DISCHARGE") and (op.get("Pdc_W") or 0) < 0) or \
                (n.startswith("DC_CHARGE") and (op.get("Pdc_W") or 0) >= 0):
            continue
        st = x.get("state")
        s.add(f"{constraint_label(n)}: {_amount(x)} / {_amount(x, 'limit')} ({pct(x['demand'], x['limit'])})"
              + (tr(f", 여유 {_amount({**x, 'demand': x['slack']})}", f", margin {_amount({**x, 'demand': x['slack']})}")
                 if st != "VIOLATED" else tr(f", 초과 {_amount(x, 'excess')}", f", over by {_amount(x, 'excess')}")),
              "bad" if st == "VIOLATED" else "warn" if st == "ACTIVE" else "ok")
    if pcap.get("achieved_value_Nm") is not None:
        act = ", ".join(constraint_label(n) for n in pcap.get("active_constraints_at_witness") or []) or "—"
        if pcap.get("certified"):
            cert = tr(f"인증됨 (상·하한 차 {num(pcap.get('gap_Nm'), 2)} N·m ≤ 허용 {num(pcap.get('gap_tolerance_Nm'), 2)} N·m)",
                      f"certified (bound gap {num(pcap.get('gap_Nm'), 2)} N·m ≤ {num(pcap.get('gap_tolerance_Nm'), 2)} N·m)")
        elif pcap.get("certified_opposite_bound_Nm") is not None:
            cert = tr(f"인증 안 됨 — 찾은 값(표본 스캔 + 이분법)과 물리 상한 {q(pcap['certified_opposite_bound_Nm'], 'N·m')}의 차 "
                      f"{q(pcap.get('gap_Nm'), 'N·m', 3)}가 허용 {q(pcap.get('gap_tolerance_Nm'), 'N·m', 2)}보다 큼",
                      f"not certified — the found value (sampled scan + bisection) and the physical bound "
                      f"{q(pcap['certified_opposite_bound_Nm'], 'N·m')} differ by {q(pcap.get('gap_Nm'), 'N·m', 3)} > "
                      f"{q(pcap.get('gap_tolerance_Nm'), 'N·m', 2)}")
        else:
            cert = tr("인증 안 됨", "not certified")
        s.add(tr(f"이 조건의 정책 capability {q(pcap['achieved_value_Nm'], 'N·m')} — {cert}; 그 점에서 활성: {act}",
                 f"policy capability here {q(pcap['achieved_value_Nm'], 'N·m')} — {cert}; active there: {act}"), "info")
    if cur and cur.get("state") == "SATISFIED" and vol and vol.get("state") != "SATISFIED":
        m = c.get("torque_capability_margin_Nm")
        if m is not None and m < 0:
            head = tr(f"전류 한계에는 닿지 않았는데(여유 {q(cur['slack'], 'A')}) 요구가 capability를 {q(-m, 'N·m')} 넘습니다",
                      f"the current limit is not reached (margin {q(cur['slack'], 'A')}) yet the request exceeds the "
                      f"capability by {q(-m, 'N·m')}")
        else:
            head = tr(f"전류 한계에는 닿지 않았고(여유 {q(cur['slack'], 'A')}) 토크 여유는 {q(m, 'N·m')}입니다",
                      f"the current limit is not reached (margin {q(cur['slack'], 'A')}); the torque margin is "
                      f"{q(m, 'N·m')}")
        s.add(head + tr(" — 이 조건의 병목은 전류가 아니라 전압·DC입니다. 전류 정격을 키워도 이 조건의 토크는 늘지 않습니다.",
                        " — the bottleneck here is voltage/DC, not current; a larger current rating does not add torque "
                        "at this condition."), "warn")


# ---------------------------------------------------------------------- power and losses
def power_section(ins: Insight, op: dict) -> None:
    ps, pac, pdc = op.get("Pshaft_W"), op.get("Pac_W"), op.get("Pdc_W")
    if ps is None or pdc is None:
        return
    s = ins.section(tr("전력 흐름과 손실", "power flow and losses"))
    lb = op.get("loss_breakdown_W") or {}
    total = sum(float(x) for x in lb.values() if x is not None)
    names = {"copper": tr("동손", "copper"), "rotational": tr("회전손", "rotational"), "inverter": tr("인버터 손실", "inverter"),
             "module": tr("모듈 손실", "module"), "iron": tr("철손", "iron")}
    motoring = ps >= 0
    if motoring:
        s.add(tr(f"DC {kw(pdc)} → 인버터 → AC {kw(pac)} → 모터 → 축 {kw(ps)}",
                 f"DC {kw(pdc)} → inverter → AC {kw(pac)} → motor → shaft {kw(ps)}"), "info")
    else:
        s.add(tr(f"축 {kw(ps)} → 모터 → AC {kw(pac)} → 인버터 → DC {kw(pdc)} (음수 = 배터리로 회수)",
                 f"shaft {kw(ps)} → motor → AC {kw(pac)} → inverter → DC {kw(pdc)} (negative = recovered)"), "info")
    if total > 0:
        parts = [f"{names.get(k, k)} {kw(w)} ({pct(w, total, 0)})" for k, w in sorted(lb.items(), key=lambda kv: -(kv[1] or 0))
                 if w]
        s.add(tr(f"손실 합 {kw(total)}: ", f"losses {kw(total)}: ") + ", ".join(parts), "info")
    if op.get("efficiency") is not None:
        mode = {"MOTORING": tr("구동", "motoring"), "REGENERATING": tr("회생", "regenerating")}.get(
            op.get("energy_mode"), op.get("energy_mode", ""))
        s.add(tr(f"효율 {100 * op['efficiency']:.2f} % ({mode})", f"efficiency {100 * op['efficiency']:.2f} % ({mode})"), "info",
              esc(op.get("efficiency_note", "")))
    res = (op.get("power_identity_residuals_W") or {})
    if res:
        s.add(tr("전력 항등식 (P_ac = T_e·ω_m + P_cu, P_dc = P_ac + P_inv) " + ("성립" if res.get("ok") else "불성립"),
                 "power identities (P_ac = T_e·ω_m + P_cu, P_dc = P_ac + P_inv) " + ("hold" if res.get("ok") else "fail")),
              "ok" if res.get("ok") else "bad",
              tr(f"허용 {num(res.get('tolerance'))} W", f"tolerance {num(res.get('tolerance'))} W"))


# ---------------------------------------------------------------------- dominance
def dominance_section(ins: Insight, dom: dict | None, T: float) -> None:
    if not dom or not dom.get("single"):
        return
    s = ins.section(tr("병목 기여도 — 한계를 하나씩 1 % 완화하면", "bottleneck contribution — relaxing one limit by 1 %"),
                    tr("각 한계를 따로 1 % 완화해 정책 capability 전체를 다시 계산한 진단 결과입니다. 실제 설계 변경(예: Vdc)은 여러 "
                       "한계를 함께 움직입니다.",
                       "each limit relaxed alone by 1 % and the whole policy capability recomputed (diagnostic); a real "
                       "change such as Vdc moves several limits at once."))
    rows = sorted(dom["single"], key=lambda r: -(r.get("gain_Nm") or 0.0))
    effective = [r for r in rows if r.get("classification") == "limiting" and (r.get("gain_Nm") or 0) > 0]
    for r in effective:
        s.add(f"<b>{constraint_label(r['constraint'])}</b> +1 % ({_pchange(r['parameter'], r['limit'], r['relaxed_limit'])}): "
              f"{cap_word(T)} +{num(r['gain_Nm'], 3)} N·m", "warn",
              tr(f"분해능 구간 [{num(r['gain_interval_Nm'][0], 3)}, {num(r['gain_interval_Nm'][1], 3)}] N·m",
                 f"resolution [{num(r['gain_interval_Nm'][0], 3)}, {num(r['gain_interval_Nm'][1], 3)}] N·m")
              if r.get("gain_interval_Nm") else "")
    if len(effective) >= 2 and effective[1]["gain_Nm"] > 0:
        a, b = effective[0], effective[1]
        s.add(tr(f"가장 효과적인 완화는 {constraint_label(a['constraint'])} — {constraint_label(b['constraint'])}보다 "
                 f"{num(a['gain_Nm'] / b['gain_Nm'], 3)}배 효과 (1 %당)",
                 f"the most effective relaxation is {constraint_label(a['constraint'])} — "
                 f"{num(a['gain_Nm'] / b['gain_Nm'], 3)}× the gain of {constraint_label(b['constraint'])} (per 1 %)"), "info")
    neg = [r for r in rows if (r.get("gain_Nm") or 0) < 0 and "non-monotonic" in str(r.get("classification", ""))]
    for r in neg:
        s.add(tr(f"{constraint_label(r['constraint'])} 완화는 capability를 {num(r['gain_Nm'], 3)} N·m 바꿈 (비단조 응답)",
                 f"relaxing {constraint_label(r['constraint'])} changes the capability by {num(r['gain_Nm'], 3)} N·m "
                 f"(non-monotonic response)"), "info")
    none = [constraint_label(r["constraint"]) for r in rows if r not in effective and r not in neg]
    if none:
        s.add(tr("단독으로는 영향 없음 (분해능 이내): ", "no effect alone (within resolution): ") + ", ".join(none), "info")


def _pchange(parameter: str, before, after) -> str:
    """'<parameter> 200 kW → 202 kW' (the parameter's own unit; W shown in kW)."""
    unit = (PARAMETERS.get(parameter) or ("", "", ""))[1]
    if unit == "W":
        a, b = kw(before), kw(after)
    elif unit in ("", "-", "1"):
        a, b = num(before), num(after)
    else:
        a, b = q(before, unit), q(after, unit)
    return f"{param_label(parameter)} {a} → {b}"


def _qual_ko(qual: dict) -> str:
    st = str(qual.get("status", ""))
    if st.startswith("NOT QUALIFIED"):
        origin = qual.get("data_origin", "")
        return tr(f"적격성 없음 ({origin} 데이터)", st)
    return st


def cap_word(T: float) -> str:
    return tr("최대 토크", "maximum torque") if T >= 0 else tr("최대 제동 토크", "maximum braking torque")


# ---------------------------------------------------------------------- what would make it pass
def fix_section(ins: Insight, an: dict, verdict: str) -> None:
    rel, sizing, comp = an.get("relaxation"), an.get("sizing") or [], an.get("comparison")
    if not (rel or sizing or comp):
        return
    title = (tr("요구를 만족시키려면", "what would make it pass") if verdict != "PASS"
             else tr("설계 변경 검토 (역설계·비교)", "design changes (sizing, comparison)"))
    s = ins.section(title)
    if rel and rel.get("single"):
        ok = [r for r in rel["single"] if r.get("sufficient_alone")]
        for r in ok:
            chg = _pchange(r["parameter"], r["limit"], r["relaxed_limit"])
            s.add(tr(f"<b>{constraint_label(r['constraint'])}</b>만 +{100 * r['minimal_relative_relaxation']:.1f} % "
                     f"({chg}) 완화하면 단독으로 충분",
                     f"relaxing only <b>{constraint_label(r['constraint'])}</b> by "
                     f"+{100 * r['minimal_relative_relaxation']:.1f} % ({chg}) is enough"), "ok",
                  esc(engine_text(r.get("minimal_meaning", ""))))
        if not ok and rel.get("base_policy_status") != "FEASIBLE":
            s.add(tr("어떤 한계도 단독 완화(+50 %까지 탐색)로는 충분하지 않습니다", "no single limit suffices alone (searched "
                     "up to +50 %)"), "bad")
        for j in rel.get("joint") or []:
            names = " + ".join(constraint_label(n) for n in j["constraints"])
            s.add(tr(f"<b>공동 병목</b>: {names}를 함께 각각 +{100 * j['minimal_relative_relaxation_each']:.1f} % 완화하면 충분",
                     f"<b>joint bottleneck</b>: relaxing {names} together by "
                     f"+{100 * j['minimal_relative_relaxation_each']:.1f} % each is enough"), "warn")
    for sz in sizing:
        p = sz["parameter"]
        name = param_label(p["parameter"])
        rng = f"{num(sz['search_range'][0])}–{num(sz['search_range'][1])} {p.get('unit', '')}"
        if sz.get("minimal_feasible_value") is not None:
            lab = tr("경계 (표본 + 이분법 국소 bracket)", "edge (samples + bisection, local bracket)") \
                if sz.get("minimal_is_bracketed") else tr("찾은 최솟값 (증명된 최솟값 아님)", "smallest found (not a proven minimum)")
            sol = sz.get("solution_at_minimal_value") or {}
            det = ""
            if sol:
                det = tr(f"그 경계에서 |i| = {q(sol.get('i_peak_A'), 'A')}, P_dc = {kw(sol.get('Pdc_W'))}, I_dc = "
                         f"{q(sol.get('Idc_A'), 'A')}, 활성: {', '.join(constraint_label(n) for n in sol.get('active_constraints') or [])}",
                         f"at that edge |i| = {q(sol.get('i_peak_A'), 'A')}, P_dc = {kw(sol.get('Pdc_W'))}, I_dc = "
                         f"{q(sol.get('Idc_A'), 'A')}, active: {', '.join(constraint_label(n) for n in sol.get('active_constraints') or [])}")
            base = q(sz.get("baseline_value"), p.get("unit", ""))
            s.add(tr(f"<b>{name}</b> ≥ {q(sz['minimal_feasible_value'], p.get('unit', ''))}이면 가능 ({lab}; 탐색 {rng}, 기준 "
                     f"{base})",
                     f"<b>{name}</b> ≥ {q(sz['minimal_feasible_value'], p.get('unit', ''))} passes ({lab}; searched {rng}, "
                     f"baseline {base})"), "ok", det)
        elif not sz.get("feasible_ranges"):
            s.add(tr(f"<b>{name}</b>: 탐색 범위 {rng} 전체에서 불가능 — 이 파라미터만으로는 해결되지 않습니다",
                     f"<b>{name}</b>: infeasible over the whole searched range {rng} — this parameter alone does not "
                     f"solve it"), "bad")
    if comp and comp.get("scenarios"):
        for scn in comp["scenarios"]:
            st = "FEASIBLE" if scn["claims"] and all(x == "FEASIBLE" for x in scn["claims"].values()) else (
                "INFEASIBLE" if any(x == "INFEASIBLE" for x in scn["claims"].values()) else "UNKNOWN")
            s.add(tr(f"비교 {scn['scenario_id']}: {state_label(st)} — 정책 capability {q(scn.get('policy_capability_Nm'), 'N·m')}, 제한: "
                     f"{', '.join(constraint_label(n) for n in scn.get('capability_limited_by') or []) or '—'}",
                     f"compare {scn['scenario_id']}: {state_label(st)} — policy capability {q(scn.get('policy_capability_Nm'), 'N·m')}, "
                     f"limited by {', '.join(constraint_label(n) for n in scn.get('capability_limited_by') or []) or '—'}"),
                  status_level(st))


# ---------------------------------------------------------------------- PWM at the same point
def pwm_section(ins: Insight, pr: dict) -> None:
    s = ins.section(tr("PWM 영향 (같은 운전점, 스크리닝)", "PWM consequences (same point, screening)"))
    if pr.get("status") != "EVALUATED":
        s.add(tr(f"평가 안 됨: {esc(pr.get('reason', pr.get('status')))}", f"not evaluated: {esc(pr.get('reason', pr.get('status')))}"), "open")
        return
    fu, pk, rm = pr["fundamental"], pr["instantaneous_peak"], pr["rms"]
    s.add(tr(f"기본파 피크 {q(fu['i_peak_A'], 'A')} / 한계 {q(fu['limit_A'], 'A')} (여유 {q(fu['margin_A'], 'A')}) — 정책이 지키는 한계",
             f"fundamental peak {q(fu['i_peak_A'], 'A')} of {q(fu['limit_A'], 'A')} (margin {q(fu['margin_A'], 'A')}) — "
             f"the limit the policy keeps"), "ok" if fu["margin_A"] >= 0 else "bad")
    lim = pk.get("limit_A")
    lvl = {"FEASIBLE": "ok", "INFEASIBLE": "bad"}.get(pk.get("status"), "open")
    s.add(tr(f"리플 포함 순간 피크 (보수 상한) {q(pk['bound_A'], 'A')}"
             + (f" / 선언된 피크 한계 {q(lim, 'A')}" if lim is not None else " — 피크 한계가 선언되지 않아 판정 없음"),
             f"instantaneous peak incl. ripple (conservative bound) {q(pk['bound_A'], 'A')}"
             + (f" of the declared peak limit {q(lim, 'A')}" if lim is not None else " — no peak limit declared, not judged")),
          lvl, esc(pk.get("reason", "")))
    s.add(tr(f"RMS: 기본파 {q(rm['fundamental_A'], 'A')} + 리플 {q(rm['ripple_A'], 'A')} → {q(rm['total_A'], 'A')} "
             f"(+{num(rm['added_percent'], 3)} %) — 발열은 합산 RMS 기준",
             f"RMS: fundamental {q(rm['fundamental_A'], 'A')} + ripple {q(rm['ripple_A'], 'A')} → {q(rm['total_A'], 'A')} "
             f"(+{num(rm['added_percent'], 3)} %) — heating follows the total RMS"), "info")
    dl = pr.get("dc_link")
    if dl:
        s.add(tr(f"DC-link: 커패시터 전류 {q(dl['I_cap_rms_A'], 'A')} rms, ESR 손실 {q(dl['P_cap_W'], 'W')}, 리플 "
                 f"{q(dl['V_ripple_pp_V'], 'V')} pp",
                 f"DC-link: capacitor current {q(dl['I_cap_rms_A'], 'A')} rms, ESR loss {q(dl['P_cap_W'], 'W')}, ripple "
                 f"{q(dl['V_ripple_pp_V'], 'V')} pp"), "info")


# ---------------------------------------------------------------------- scope
def _scope_section(ins: Insight, rec: dict, op: dict | None) -> None:
    s = ins.section(tr("이 결과가 말하지 않는 것", "what this result does not cover"))
    layers = rec["verdict"].get("layers") or {}
    qual = layers.get("qualification") or {}
    if qual:
        s.add(tr(f"데이터 등급: {esc(_qual_ko(qual))} — 모델 판정이나 수치 인증서가 하드웨어 적격성을 뜻하지 않습니다",
                 f"data qualification: {esc(qual.get('status', ''))} — a model verdict or a numerical certificate is not "
                 f"hardware qualification"), "warn" if "NOT" in str(qual.get("status", "")) else "info")
    vol = _cons(op, "VOLTAGE")
    if vol is not None and vol.get("state") == "ACTIVE":
        s.add(tr("전압 여유 0 V: 전류 제어·과도 응답에 쓸 전압 헤드룸이 없습니다 (과도·제어 동특성은 평가 범위 밖)",
                 "0 V voltage margin: no headroom for current control and transients (dynamics are outside this "
                 "evaluation)"), "warn")
    for x in rec.get("not_evaluated") or []:
        s.add(tr("평가 안 함: ", "not evaluated: ") + esc(engine_text(x)), "open")


def _next_section(ins: Insight, rec: dict, an: dict, verdict: str, pending=()) -> None:
    s = ins.section(tr("다음 단계", "next steps"))
    for a in rec.get("next_actions") or []:
        s.add(esc(engine_text(a)), "info")
    v = rec["verdict"]
    if verdict != "PASS":
        cls = classify(v["status"], v["reasons"])
        for h in cls.get("hints") or ([cls["hint"]] if cls.get("hint") else []):
            s.add(tr("분류", "class") + f" — {tr(cls['label_ko'], cls['label_en'])}: {esc(engine_text(h))}", "info")
    if verdict == "PASS" and not an.get("dominance") and "dominance" not in pending:
        s.add(tr("여유를 늘릴 한계를 알려면 '병목 기여도' 분석을 켜고 다시 실행하세요",
                 "turn on the dominance analysis to see which limit would add margin"), "info")
