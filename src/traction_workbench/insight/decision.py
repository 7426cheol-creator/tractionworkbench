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

    rob = (v.get("layers") or {}).get("robustness") or {}
    if rob.get("budget"):                   # 'met in the model' vs 'with the declared error' at the top
        rlabel, _rt, rlevel = robustness_summary(rob)
        ins.headline += tr(f" · 선언 오차 대비 <b>{rlabel}</b>", f" · against the declared error: <b>{rlabel}</b>")

    # ------------------------------------------------------------------ key numbers (cards)
    ins.metrics.append((tr("판정", "verdict"), verdict, status_level(verdict)))
    if cap is not None:
        ins.metrics.append((cap_noun + (tr(" (인증됨)", " (certified)") if pcap.get("certified") else ""), cap_txt,
                            "info"))
    if margin is not None:
        ins.metrics.append((tr("토크 여유", "torque margin"), mtxt, "ok" if margin >= 0 else "bad"))
    if rob.get("budget"):
        ins.metrics.append((tr("선언 오차 대비", "against the declared error"), rlabel, rlevel))
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
    _claims_section(ins, c, ps, op, T, rec)
    robustness_section(ins, rec)
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
def _claims_section(ins: Insight, c: dict, ps: dict, op: dict | None, T: float, rec: dict | None = None) -> None:
    s = ins.section(tr("판정 항목별 분석", "the judged items"),
                    tr("요구 판정은 아래 항목의 AND입니다 — 증명된 위반이 하나라도 있으면 FAIL, 위반 없이 미확정이 있으면 UNKNOWN.",
                       "the verdict is the AND of these items — any proven violation gives FAIL, an open item without a "
                       "violation gives UNKNOWN."))
    if rec is not None:
        rq = rec.get("requirement") or {}
        temps = [x["scenario"].get("magnet_temp_C") for x in rec.get("conditions") or []]
        notes = []
        if rq.get("operator") == "band":
            notes.append(tr("∃: 대역 안의 토크 하나가 모든 항목을 같은 운전점에서 만족하면 충분 (대역 전체의 추종이 아님)",
                            "∃: one torque inside the band meeting every item at the same point is enough (not tracking "
                            "of the whole band)"))
        if isinstance((rq.get("conditions") or {}).get("Vdc_V_inverter_dc_terminal"), (list, tuple)) \
                or len({t for t in temps if t is not None}) > 1:
            notes.append(tr("∀: 범위의 모든 값에서 성립해야 함 (표본점 또는 인증서로 검토)",
                            "∀: must hold at every value of the range (examined at samples or by a certificate)"))
        s.add(tr("판정한 질문: ", "the question judged: ") + requirement_reading(rq, temps), "info", " · ".join(notes))
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


# ---------------------------------------------------------------------- the question judged (quantifiers)
def requirement_reading(req: dict, magnet_temps=None) -> str:
    """The requirement as the question the verdict answers, with its quantifiers written out: a band is existence of
    ONE torque in it (∃), a Vdc range and an unstated magnet temperature on a multi-plane flux map are for every value
    (∀).  ``req``: a record's requirement (``Requirement.describe``); ``magnet_temps``: the magnet temperatures the
    conditions examine (for-all when there are several)."""
    c = req.get("conditions") or {}
    T = float(req.get("target_Nm") or 0.0)
    n = c.get("speed_rpm_mechanical")
    v = c.get("Vdc_V_inverter_dc_terminal")
    if req.get("operator") == "band":
        b = float(req.get("band_Nm") or 0.0)
        tq = tr(f"축 토크 {num(T - b)}–{num(T + b)} N·m 중 <b>어느 하나(∃)</b>",
                f"<b>some</b> shaft torque in {num(T - b)}–{num(T + b)} N·m <b>(∃)</b>")
    else:
        tq = tr(f"축 토크 {num(T)} N·m", f"the shaft torque {num(T)} N·m")
    if T < 0:
        tq += tr(" (회생 제동)", " (regenerative braking)")
    if isinstance(v, (list, tuple)):
        vd = tr(f"Vdc {num(v[0])}–{num(v[1])} V의 <b>모든 값(∀)</b>", f"<b>every</b> Vdc in {num(v[0])}–{num(v[1])} V "
                                                                  f"<b>(∀)</b>")
    else:
        vd = f"Vdc {num(v)} V"
    temps = sorted({float(t) for t in magnet_temps or () if t is not None})
    mt = c.get("magnet_temp_C")
    if mt is not None:
        mg = tr(f", 자석 {num(mt)} °C", f", magnet {num(mt)} °C")
    elif len(temps) > 1:
        mg = tr(f", flux map의 <b>모든 자석 온도(∀: {', '.join(num(t) for t in temps)} °C)</b>",
                f", <b>every</b> flux-map magnet temperature <b>(∀: {', '.join(num(t) for t in temps)} °C)</b>")
    else:
        mg = ""
    dur = str(req.get("duration") or "")
    if not dur or dur.startswith("not stated"):
        dt = tr("정적으로 (지속시간 없음)", "statically (no duration)")
    elif dur == "continuous":
        dt = tr("연속으로", "continuously")
    else:
        dt = tr(f"{dur} 동안", f"for {dur}")
    return tr(f"{num(n)} rpm, {vd}{mg}에서 {tq}를 {dt} 낼 수 있는가",
              f"at {num(n)} rpm, {vd}{mg}: can the drive deliver {tq} {dt}?")


# ---------------------------------------------------------------------- margin vs the declared error budget
ROBUST_STATUS = {"ROBUST": ("견고", "robust", "ok"), "WITHIN_ERROR": ("오차 범위 안", "within the error", "warn"),
                 "NOT_ESTABLISHED": ("미확립", "not established", "open"),
                 "NOT_ASSESSED": ("평가 안 함", "not assessed", "info")}
ROBUST_KIND = {"model": ("모델 불일치", "model mismatch"), "input": ("입력·측정", "input / measurement"),
               "numerical": ("수치 (공급 데이터)", "numerical (supplied data)")}
ROBUST_QUANTITY = {"torque": ("토크", "torque", "능력치", "the capability"),
                   "phase_current": ("상전류", "phase current", "운전점 상전류", "the phase current at the witness"),
                   "dc_power": ("DC 전력", "DC power", "운전점 DC 전력", "the DC power at the witness"),
                   "dc_current": ("DC 전류", "DC current", "운전점 DC 전류", "the DC current at the witness"),
                   "voltage": ("명령 전압", "command voltage", "운전점 명령 전압", "the command voltage at the witness")}
ROBUST_CONDITION = {"ROBUST_MET": ("충족 — 오차를 넘는 여유", "met, margin beyond the error"),
                    "MET_WITHIN_ERROR": ("충족 — 오차 범위 안", "met, within the error"),
                    "ROBUST_NOT_MET": ("불충족 — 오차를 넘는 부족", "not met, short beyond the error"),
                    "NOT_MET_WITHIN_ERROR": ("불충족 — 오차 범위 안", "not met, within the error"),
                    "NOT_MET_NOT_ESTABLISHED": ("불충족 — 오차 대비 미확립", "not met, not established against the error"),
                    "NOT_ESTABLISHED_SOURCE": ("소스 결합으로 미확립", "not established (source coupling)"),
                    "NOT_ASSESSED": ("평가 안 함", "not assessed")}
ROBUST_CHECK = {"ROBUST": ("오차를 넘는 여유", "margin beyond the error", "ok"),
                "WITHIN_ERROR": ("오차 범위 안", "within the error", "warn"),
                "ADAPTED": ("전압 한계 위 — 정책이 운전점을 옮겨 대응 (토크 쪽으로 비교)",
                            "on the voltage limit — the policy moves the point (compare in torque)", "open"),
                "NOT_EVALUATED": ("비교할 한계 없음", "no limit to compare", "open"),
                "NOT_ESTABLISHED_SOURCE": ("소스 결합으로 미확립", "not established (source coupling)", "open")}
_ROBUST_OTHER = {"duration": ("지속시간 부분(자체 정격 근거)", "the duration part (its own rating evidence)"),
                 "source": ("소스 결합", "the source coupling"),
                 "coverage": ("연속 범위의 커버리지", "the coverage of the continuous range")}


def _unit(u: str) -> str:
    return "N·m" if u == "N*m" else u


def _check_words(c: dict, at: str = "") -> str:
    """One limit comparison at the witness in words (no bare comparison signs: the reading is rich text)."""
    qk, qe = ROBUST_QUANTITY[c["quantity"]][:2]
    u = _unit(c["unit"])
    if c.get("robust"):
        return tr(f"{qk} 여유 {num(c['slack'])} {u} ≥ 선언 오차 {num(c['delta'])} {u}{at}",
                  f"{qe} margin {num(c['slack'])} {u} ≥ declared error {num(c['delta'])} {u}{at}")
    return tr(f"{qk} 여유 {num(c['slack'])} {u}가 선언 오차 {num(c['delta'])} {u}보다 작음{at}",
              f"{qe} margin {num(c['slack'])} {u} is below the declared error {num(c['delta'])} {u}{at}")


def robustness_summary(rob: dict | None) -> tuple[str, str, str]:
    """(status label, one sentence, level) of the robustness layer: the model's margins against the declared error
    budget.  Built from the layer's numbers (never a new threshold: the budget is the engineer's)."""
    rob = rob or {}
    st = rob.get("status", "NOT_ASSESSED")
    ko, en, level = ROBUST_STATUS.get(st, ROBUST_STATUS["NOT_ASSESSED"])
    label = tr(ko, en)
    per, names, g = rob.get("conditions") or [], rob.get("names") or [], rob.get("governing")
    checks, gc = rob.get("checks") or [], rob.get("governing_check")
    several = len(per) > 1

    def at(i):
        return f" ({names[i]})" if several and i is not None and i < len(names) else ""
    p = per[g] if g is not None and g < len(per) else {}
    model = rob.get("model_static")
    budget = rob.get("budget")
    torque = any(it.get("quantity", "torque") == "torque" for it in (budget or {}).get("items") or [])
    if st == "NOT_ASSESSED" and not budget:
        if p.get("margin_Nm") is None:
            text = tr("오차 예산 미선언 — 토크 능력치가 확립되지 않아 여유도 없습니다",
                      "no error budget declared — and no established torque capability to compare")
        elif model == "not met":
            text = tr(f"오차 예산 미선언: 모델상 부족 {num(-p['margin_Nm'])} N·m{at(g)}만 있습니다 — 이 부족이 모델·입력 "
                      f"오차보다 큰지는 평가하지 않았습니다",
                      f"no error budget declared: a model shortfall of {num(-p['margin_Nm'])} N·m{at(g)} only — whether "
                      f"it exceeds the model and input error is not assessed")
        else:
            text = tr(f"오차 예산 미선언: 모델 여유 {num(p['margin_Nm'])} N·m{at(g)}만 있습니다 — 설계 판단에 충분한지는 "
                      f"평가하지 않았습니다",
                      f"no error budget declared: a model margin of {num(p['margin_Nm'])} N·m{at(g)} only — its "
                      f"sufficiency for a design decision is not assessed")
    elif st == "NOT_ASSESSED" and model == "not met":
        text = tr(f"모델상 부족 {num(-p.get('margin_Nm', 0.0))} N·m{at(g)}: FAIL은 토크 능력치로 비교하는데 토크 오차가 선언되지 "
                  f"않았습니다 (한계 오차는 요구를 만족하는 운전점에서만 비교합니다)",
                  f"a model shortfall of {num(-p.get('margin_Nm', 0.0))} N·m{at(g)}: a FAIL is compared on the torque "
                  f"capability and no torque error is declared (limit errors are compared at witnesses that meet the "
                  f"requirement)")
    elif st == "NOT_ASSESSED":
        text = tr("선언한 오차 가운데 여기서 비교할 수 있는 것이 없습니다 (토크 오차가 없고, 선언한 한계 오차에 맞는 한계가 운전점에 없음)",
                  "none of the declared errors can be compared here (no torque error, and no declared limit error meets "
                  "a limit at the witnesses)")
    elif model == "not met" and st == "ROBUST":
        text = tr(f"인증 상한 기준으로도 {num(p['shortfall_at_bound_Nm'])} N·m 부족해 선언 오차 합 "
                  f"{num(p['delta_at_bound_Nm'])} N·m보다 큽니다{at(g)}: 선언 오차를 고려해도 불충족",
                  f"short by at least {num(p['shortfall_at_bound_Nm'])} N·m at the certified bound, more than the "
                  f"declared error {num(p['delta_at_bound_Nm'])} N·m{at(g)}: not met even with the declared error")
    elif model == "not met" and st == "WITHIN_ERROR":
        text = tr(f"모델상 {num(-p['margin_Nm'])} N·m 부족 ≤ 선언 오차 합 {num(p['delta_Nm'])} N·m{at(g)}: 오차 범위 안이라 "
                  f"실제 제품은 충족할 수도 있습니다 — 모델 FAIL만으로 설계를 기각하지 마세요",
                  f"short by {num(-p['margin_Nm'])} N·m in the model ≤ declared error {num(p['delta_Nm'])} N·m{at(g)}: "
                  f"within the error, so the product may still meet it — do not reject the design on the model FAIL "
                  f"alone")
    elif st == "WITHIN_ERROR" and gc is not None and gc.get("robust") is False:
        text = (_check_words(gc, at(gc.get("condition")))
                + tr(": 모델상으로는 충족하지만 이 한계를 선언 오차보다 크게 지키지 못합니다 — 이 근거만으로는 설계 판단에 부족",
                     ": met in the model, but the limit is not held by more than the declared error — not sufficient "
                     "for a design decision on this evidence"))
    elif st == "WITHIN_ERROR":
        text = tr(f"토크 여유 {num(p['margin_Nm'])} N·m가 선언 오차 합 {num(p['delta_Nm'])} N·m보다 작습니다{at(g)}: "
                  f"모델상으로는 충족하지만 이 근거만으로는 설계 판단에 부족",
                  f"torque margin {num(p['margin_Nm'])} N·m is below the declared error {num(p['delta_Nm'])} N·m"
                  f"{at(g)}: met in the model, but not sufficient for a design decision on this evidence")
    elif st == "ROBUST":
        parts = []
        if torque and p.get("delta_Nm") is not None:
            parts.append(tr(f"토크 여유 {num(p['margin_Nm'])} N·m ≥ 선언 오차 합 {num(p['delta_Nm'])} N·m{at(g)}",
                            f"torque margin {num(p['margin_Nm'])} N·m ≥ declared error {num(p['delta_Nm'])} N·m{at(g)}"))
        if checks and gc is not None:
            parts.append(tr("선언한 한계 오차를 운전점에서 모두 덮음 (가장 빠듯한 것: ", "every declared limit error is covered at "
                            "the witnesses (tightest: ") + _check_words(gc, at(gc.get("condition"))) + ")")
        text = "; ".join(parts) + tr(": 선언 오차를 고려해도 충족 — 설계 판단에 쓸 수 있는 여유",
                                     ": met with the declared error — a margin a design decision can use")
    else:
        why = [engine_text(x.get("reason", "")) for x in per if x.get("reason") and x.get("robust") is None]
        why += [engine_text(c.get("reason", "")) for c in checks if c.get("robust") is None and c.get("reason")]
        text = tr("선언 오차 대비 판단이 서지 않습니다: ", "not decidable against the declared error: ") + esc(
            "; ".join(dict.fromkeys(why)))
    rel = rob.get("verdict_relation")
    if rel:
        parts = " · ".join(tr(*_ROBUST_OTHER[x]) for x in rel.get("parts") or [] if x in _ROBUST_OTHER)
        text += tr(f" — 판정({rel['verdict']})은 {parts}이(가) 정하며, 이 비교는 그 부분을 다루지 않습니다",
                   f" — the verdict ({rel['verdict']}) is decided by {parts}, which this comparison does not cover")
    cov = rob.get("coverage")
    if cov == "examined_only":
        text += tr(" (검토한 조건에서만의 비교 — 연속 범위는 미확립)",
                   " (at the examined conditions only — the continuous range is not established)")
    elif cov == "range_certified":
        text += tr(" (정적 판정은 Vdc 범위 전체로 인증됐지만, 여유 비교는 검토한 조건에서만)",
                   " (the static claim is certified over the Vdc range; the margin comparison is at the examined "
                   "conditions)")
    return label, text, level


def robustness_section(ins: Insight, rec: dict) -> None:
    """'Met in the model' and 'met with enough margin for a design decision' kept apart (review of 63a2b61, 4)."""
    rob = ((rec.get("verdict") or {}).get("layers") or {}).get("robustness") or {}
    label, text, level = robustness_summary(rob)
    s = ins.section(tr("설계 판단 여유 — 선언 오차 대비", "margin for a design decision — against the declared error"),
                    tr("모델 판정(위)은 바뀌지 않습니다. 선언한 오차를 물리량마다 최악 조합으로 합쳐 따로 비교합니다: 토크는 능력치 여유"
                       "(충족은 찾은 능력치, 불충족은 인증 상한), 전류·DC·전압은 운전점의 한계 여유(y + Δ ≤ y_max).",
                       "the model verdict above does not change. The declared error of each quantity is summed worst "
                       "case and compared separately: torque with the capability margin ('met' at the found "
                       "capability, 'not met' at the certified bound), current, DC and voltage with the limit margins "
                       "at the witness (y + Δ ≤ y_max)."))
    s.add(f"<b>{label}</b>: {text}", level)
    budget = rob.get("budget")
    per, names, g = rob.get("conditions") or [], rob.get("names") or [], rob.get("governing")
    checks = rob.get("checks") or []
    several = len(per) > 1
    if not budget:
        s.add(tr("요구 폼의 '오차 예산'에 모델 불일치(예: flux map 토크 정확도 %), 입력·측정 오차(예: 전류 센서 이득 %), 공급 데이터의 "
                 "수치 오차를 근거와 함께 적으면 이 비교를 합니다. Vdc·온도의 불확실성은 범위 요구(∀)로 넣으세요.",
                 "declare model mismatch (e.g. the flux-map torque accuracy in %), input / measurement error (e.g. the "
                 "current-sensor gain in %) and the numerical error of supplied data, each with its basis, in the "
                 "form's error budget to get this comparison. Put Vdc and temperature uncertainty in the requirement "
                 "as ranges (for all)."), "info")
        return
    p = per[g] if g is not None and g < len(per) else {}
    if (rec.get("requirement") or {}).get("operator") == "band" and rob.get("T_edge_Nm") is not None:
        s.add(tr(f"대역 요구(∃): 대역 안의 토크 하나면 되므로 토크 여유는 대역 끝 {num(rob['T_edge_Nm'])} N·m 기준입니다 "
                 f"(위의 토크 여유는 대역 중심 기준)",
                 f"band requirement (exists): one torque inside the band is enough, so the torque margin is taken at "
                 f"the band edge {num(rob['T_edge_Nm'])} N·m (the torque margin above is at the band centre)"), "info")
    for it in budget.get("items") or []:
        qd = ROBUST_QUANTITY.get(it.get("quantity", "torque"), ROBUST_QUANTITY["torque"])
        size = (f"{num(it['value'])} {_unit(it.get('unit', 'N*m'))}" if it.get("value") is not None else
                tr(f"{qd[2]}의 {num(it['percent'])} %", f"{num(it['percent'])} % of {qd[3]}"))
        s.add(f"{esc(it['source'])} ({tr(*ROBUST_KIND.get(it['kind'], (it['kind'], it['kind'])))}, "
              f"{tr(qd[0], qd[1])}): {size}", "info",
              tr("근거: ", "basis: ") + (esc(it.get("basis") or "") or tr("명시 안 됨", "not stated")))
    if p.get("delta_Nm") is not None:
        s.add(tr(f"토크 오차 합 Δ = {num(p['delta_Nm'])} N·m (능력치 {num(abs(p['capability_Nm']))} N·m 기준) — 선언한 한계의 "
                 f"최악 조합 합, 확률을 붙이지 않음",
                 f"torque error sum Δ = {num(p['delta_Nm'])} N·m (at the capability {num(abs(p['capability_Nm']))} N·m) — "
                 f"worst-case sum of the declared bounds, no probability"), "info")
    if several and any(x.get("delta_Nm") is not None for x in per):
        for n, x in zip(names, per):
            m = x.get("margin_Nm")
            s.add(f"{esc(n)}: " + (tr("토크 여유 ", "torque margin ") + f"{num(m)} N·m" if m is not None else "—")
                  + ("" if x.get("delta_Nm") is None else f" / Δ {num(x['delta_Nm'])} N·m")
                  + f" → {tr(*ROBUST_CONDITION.get(x['status'], (x['status'], x['status'])))}",
                  {"ROBUST_MET": "ok", "ROBUST_NOT_MET": "ok", "MET_WITHIN_ERROR": "warn",
                   "NOT_MET_WITHIN_ERROR": "warn"}.get(x["status"], "open"))
    for c in checks:
        ko, en, lv = ROBUST_CHECK.get(c["status"], (c["status"], c["status"], "open"))
        where = f"{esc(names[c['condition']])}: " if several and c.get("condition") is not None else ""
        if c.get("constraint") is None or c.get("slack") is None:
            s.add(where + tr(f"{ROBUST_QUANTITY[c['quantity']][0]}: {ko}", f"{ROBUST_QUANTITY[c['quantity']][1]}: {en}"),
                  lv, esc(engine_text(c.get("reason", ""))))
            continue
        u = _unit(c["unit"])
        s.add(where + f"{constraint_label(c['constraint'])} {num(c['demand'])} / {num(c['limit'])} {u} — "
              + tr(f"여유 {num(c['slack'])} {u}, 선언 오차 {num(c['delta'])} {u} → {ko}",
                   f"margin {num(c['slack'])} {u}, declared error {num(c['delta'])} {u} → {en}"), lv)


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
    for x in qual.get("iron_loss_scope") or []:
        s.add(*_iron_scope_reading(x, len(rec.get("conditions") or []) > 1))
    vol = _cons(op, "VOLTAGE")
    if vol is not None and vol.get("state") == "ACTIVE":
        s.add(tr("전압 여유 0 V: 전류 제어·과도 응답에 쓸 전압 헤드룸이 없습니다 (과도·제어 동특성은 평가 범위 밖)",
                 "0 V voltage margin: no headroom for current control and transients (dynamics are outside this "
                 "evaluation)"), "warn")
    for x in rec.get("not_evaluated") or []:
        s.add(tr("평가 안 함: ", "not evaluated: ") + esc(engine_text(x)), "open")


def _iron_scope_reading(x: dict, several: bool) -> tuple[str, str, str]:
    """(text, level, detail) of the iron-loss scope at a field-weakening witness (review of 63a2b61, 3.1)."""
    r = x.get("flux_ratio")
    where = f" · {esc(x['condition'])}" if several else ""
    head = tr("약계자 운전점 (전압 한계 활성" + ("" if r is None else f", |ψ| = 무부하 자속의 {100 * r:.0f} %") + f"){where}",
              "field-weakening point (voltage limit active" + ("" if r is None else f", |ψ| = {100 * r:.0f} % of the "
                                                                                 f"no-load flux") + f"){where}")
    m, dc, k = x.get("torque_margin_Nm"), x.get("dc_margin_W"), x.get("Nm_per_kW")
    marg = tr(("" if m is None else f"토크 여유 {num(m)} N·m") + ("" if dc is None else f", DC 방전 여유 {q(dc, 'W')}"),
              ("" if m is None else f"torque margin {num(m)} N·m") + ("" if dc is None else f", DC discharge margin "
                                                                                       f"{q(dc, 'W')}"))
    per_kw = "" if k is None else tr(f" — 이 속도에서 손실 1 kW = {num(k)} N·m", f" — 1 kW of loss is {num(k)} N·m at this "
                                                                           f"speed")
    stages = tr("단계적 충실도: 지금은 속도만의 손실 토크 → 선언 계수로 자속을 따르는 철손 → FEA 철손 맵(id, iq, n). 정적·동적 "
                "적격성은 따로 판단합니다 (데이터 감사).",
                "staged fidelity: a speed-only loss torque now → iron loss following the flux with declared coefficients "
                "→ FEA iron-loss maps over (id, iq, n). Static and dynamic qualification stay separate (data audit).")
    # the loss model's own basis text stays in the record (qualification layer): it is model data, and may quote
    # its formula with code names
    d = x.get("direction")
    if d == "unmodelled":
        return (tr(f"{head}: 철손이 모델에 없습니다 (회전 손실은 기계 손실만). 이 운전점의 철손만큼 {marg}가 줄어듭니다"
                   f"{per_kw}. 철손을 오차 예산(모델)에 넣거나 철손을 포함한 손실 데이터를 쓰세요.",
                   f"{head}: the iron loss is not in the model (mechanical rotational loss only). The iron loss at this "
                   f"point would lower the {marg}{per_kw}. Declare it in the error budget (model) or use loss data "
                   f"that include it."), "warn", stages)
    if d == "conservative":
        return (tr(f"{head}: 철손이 모델에 없지만, 회생 제동에서는 모델 밖 손실이 제동 토크를 더하고 충전 한계를 덜어 주므로 이 "
                   f"판정에는 보수 측입니다.",
                   f"{head}: the iron loss is not in the model, but for regenerative braking an unmodelled loss adds "
                   f"braking torque and eases a charge limit, so the model is on the conservative side for this "
                   f"claim."), "info", stages)
    help_ = (tr("구동: 모델보다 손실이 작음", "motoring: less loss than modelled") if x.get("motoring") else
             tr("제동: 모델보다 제동 보조가 작음", "braking: less braking help than modelled"))
    return (tr(f"{head}: 철손이 속도만의 손실 토크라 자속 감소를 따르지 않습니다 — 줄어든 자속에서 기본파 철손은 더 작을 "
               f"가능성이 크고({help_}), 고조파·자석 와전류 손실은 없으므로 어느 쪽인지 증명되지 않습니다. 회전·철손 "
               f"{q(x.get('P_rot_W'), 'W')} 대 {marg}{per_kw}.",
               f"{head}: the iron loss is a speed-only loss torque that does not follow the flux — at reduced "
               f"flux the fundamental iron loss is likely smaller ({help_}), harmonic and PM eddy-current loss are not "
               f"represented, so neither direction is proven. Rotational / iron loss {q(x.get('P_rot_W'), 'W')} against "
               f"the {marg}{per_kw}."), "open", stages)


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
