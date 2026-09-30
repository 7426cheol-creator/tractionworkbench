"""Engineering readings of the safety and protection pages: FTTI chain, DC-link discharge (active / passive),
battery disconnect while regenerating, safe-state screening, the protection review and the ASC transient.

Every number is the result's; derived numbers are identities of reported numbers (shown with their terms)."""

from __future__ import annotations

import math

from ..i18n import tr
from ..plots.labels import state_label
from . import Insight, esc, kw, num, pct, q, status_level
from .generic import VERDICT_OF, claim_item, not_modelled_section, notes_section, verdict_of
from .texts import engine_parts, engine_text


def _ms(s):
    return None if s is None else s * 1e3


def _msq(s, sig: int = 4) -> str:
    return q(_ms(s), "ms", sig)


# ---------------------------------------------------------------------------------------------------- FTTI
def ftti_insight(res: dict) -> Insight:
    claim = res.get("claim") or {}
    ftti, ub, lb, m = res.get("ftti_s"), res.get("worst_s"), res.get("best_s"), res.get("margin_s")
    reasons = claim.get("reasons") or []
    v = verdict_of(claim)
    fault = esc(res.get("fault", ""))
    if v == "PASS":
        head = tr(f"{fault}: 최악 보장 상한 {_msq(ub)} ≤ FTTI {_msq(ftti)} — 여유 {_msq(m)} (FTTI의 {pct(m, ftti)})",
                  f"{fault}: guaranteed worst case {_msq(ub)} ≤ FTTI {_msq(ftti)} — margin {_msq(m)} ({pct(m, ftti)} of the FTTI)")
    elif v == "FAIL" and lb is not None and ftti is not None and lb > ftti:
        head = tr(f"{fault}: 최소 지연의 합 {_msq(lb)}조차 FTTI {_msq(ftti)}를 넘음 — 어떤 경우에도 늦음",
                  f"{fault}: even the summed minima {_msq(lb)} exceed the FTTI {_msq(ftti)} — every trace is too slow")
    elif v == "FAIL":
        head = tr(f"{fault}: 동시에 일어날 수 있는 최악 {_msq(ub)} > FTTI {_msq(ftti)} — {_msq((ub or 0) - (ftti or 0))} 초과",
                  f"{fault}: the attainable worst case {_msq(ub)} > FTTI {_msq(ftti)} — {_msq((ub or 0) - (ftti or 0))} over")
    elif "BOUND_INCONCLUSIVE" in reasons:
        head = tr(f"{fault}: 최댓값의 합 {_msq(ub)}이 FTTI {_msq(ftti)}를 {_msq((ub or 0) - (ftti or 0))} 넘음 — 상한일 뿐 실패 증거는 "
                  f"아님 (미확정)",
                  f"{fault}: the summed maxima {_msq(ub)} exceed the FTTI {_msq(ftti)} by {_msq((ub or 0) - (ftti or 0))} — a "
                  f"bound, not a failure witness (undecided)")
    else:
        head = tr(f"{fault}: FTTI 판정 불가 — {esc(engine_text(claim.get('detail', '')))}",
                  f"{fault}: the FTTI cannot be judged — {esc(claim.get('detail', ''))}")
    ins = Insight(headline=head, verdict=v)
    ins.metrics += [("FTTI", _msq(ftti), "info"),
                    (tr("최악 (보장 상한)", "worst (guaranteed bound)"), _msq(ub), status_level(claim.get("status"))),
                    (tr("여유", "margin"), _msq(m), "info" if m is None else ("ok" if m >= 0 else "bad")),
                    (tr("최선 – 최악", "best – worst"), f"{num(_ms(lb))} – {num(_ms(ub))} ms", "info")]
    items = {it.get("id"): it for it in res.get("items") or []}
    path = [items[i] for i in res.get("chosen_path") or [] if i in items]
    if path and ub:
        s = ins.section(tr("시간이 어디서 쓰이나 (선택 경로, 사건 순서)", "where the time goes (chosen path, in event order)"),
                        tr(f"보장 상한은 고장부터 안전 종점까지 이어지는 {res.get('paths', 0)}개 경로 중 가장 촘촘한 경로의 합입니다. "
                           f"주기 항목은 샘플링 지연 한 주기를 더합니다.",
                           f"the guaranteed bound is the sum along the tightest of {res.get('paths', 0)} contiguous path(s) "
                           f"from the fault to the safe endpoint; a periodic item adds one sampling period."))
        top = max(path, key=lambda it: it.get("worst_s") or 0)
        for it in path:
            w = it.get("worst_s")
            per = it.get("period_s")
            det = tr(f"{esc(it.get('owner', ''))} · 최소 {_msq(it.get('min_s'))} · 최대 {_msq(it.get('max_s'))}"
                     + (f" + 주기 {_msq(per)}" if per else ""),
                     f"{esc(it.get('owner', ''))} · min {_msq(it.get('min_s'))} · max {_msq(it.get('max_s'))}"
                     + (f" + period {_msq(per)}" if per else ""))
            bold = it is top
            txt = (f"{'<b>' if bold else ''}{esc(it.get('id', ''))}{'</b>' if bold else ''} "
                   f"({esc(it.get('from', ''))} → {esc(it.get('to', ''))}): {_msq(w)} ({pct(w, ub, 0)})")
            s.add(txt + (tr(" — 가장 큰 항목", " — the largest item") if bold else ""), "info", det)
        fd, fr = res.get("fdti_worst_s"), res.get("frti_worst_s")
        if fd is not None and fr is not None:
            s.add(tr(f"감지(FDTI) {_msq(fd)} ({pct(fd, ub, 0)}) + 반응(FRTI) {_msq(fr)} ({pct(fr, ub, 0)}) = {_msq(fd + fr)}",
                     f"detection (FDTI) {_msq(fd)} ({pct(fd, ub, 0)}) + reaction (FRTI) {_msq(fr)} ({pct(fr, ub, 0)}) = "
                     f"{_msq(fd + fr)}"), "info")
        per_sum = sum(it.get("period_s") or 0 for it in path)
        if per_sum:
            s.add(tr(f"주기 샘플링 지연의 합 {_msq(per_sum)} ({pct(per_sum, ub, 0)}) — 태스크 주기를 줄이면 이만큼까지 줄어듭니다",
                     f"sampling delays of periodic items add up to {_msq(per_sum)} ({pct(per_sum, ub, 0)}) — a shorter "
                     f"task period removes up to that much"), "info")
        if lb is not None:
            s.add(tr(f"최선 {_msq(lb)} – 최악 {_msq(ub)}: 선언된 지연 편차 {_msq(ub - lb)}",
                     f"best {_msq(lb)} – worst {_msq(ub)}: the declared latency spread is {_msq(ub - lb)}"), "info")
        for it in (it for it in items.values() if it.get("id") not in set(res.get("chosen_path") or [])):
            # the same span on the chosen path: the path items from its start event to its end event
            i = next((k for k, p in enumerate(path) if p.get("from") == it.get("from")), None)
            j = next((k for k, p in enumerate(path) if p.get("to") == it.get("to")), None)
            same = sum(p.get("worst_s") or 0 for p in path[i:j + 1]) if i is not None and j is not None and i <= j else None
            s.add(tr(f"경로 밖 항목 (합산하지 않음): {esc(it.get('id', ''))} ({esc(it.get('owner', ''))}, {esc(it.get('from', ''))} → "
                     f"{esc(it.get('to', ''))}) {_msq(it.get('worst_s'))}"
                     + (f" — 같은 구간을 선택 경로는 {_msq(same)}로 보장" if same is not None else ""),
                     f"off the chosen path (not summed): {esc(it.get('id', ''))} ({esc(it.get('owner', ''))}, "
                     f"{esc(it.get('from', ''))} → {esc(it.get('to', ''))}) {_msq(it.get('worst_s'))}"
                     + (f" — the chosen path bounds the same span by {_msq(same)}" if same is not None else "")), "info")
    checks = res.get("budget_checks") or []
    if checks:
        s = ins.section(tr("예산 점검", "budget checks"))
        for c in checks:
            ok = c.get("ok")
            lvl = "open" if ok is None else ("ok" if ok else "bad")
            if "ftti_s" in c:
                s.add(tr(f"<b>FDTI + FRTI 할당</b> {_msq(c.get('allocated_s'))} vs FTTI {_msq(c.get('ftti_s'))} — "
                         f"{'들어감' if ok else '넘음'}",
                         f"<b>FDTI + FRTI allocated</b> {_msq(c.get('allocated_s'))} vs FTTI {_msq(c.get('ftti_s'))} — "
                         f"{'fits' if ok else 'exceeds'}"), lvl)
            elif c.get("worst_s") is None:
                s.add(tr(f"<b>{esc(c.get('budget', ''))}</b> 할당 {_msq(c.get('allocated_s'))}: 선택 경로에서 나눌 수 없음",
                         f"<b>{esc(c.get('budget', ''))}</b> allocated {_msq(c.get('allocated_s'))}: cannot be split on the "
                         f"chosen path"), lvl, esc(engine_text(c.get("note", ""))))
            else:
                slack = c["allocated_s"] - c["worst_s"]
                s.add(tr(f"<b>{esc(c.get('budget', ''))}</b>: 최악 {_msq(c['worst_s'])} / 할당 {_msq(c['allocated_s'])} — "
                         + (f"여유 {_msq(slack)} (할당의 {pct(slack, c['allocated_s'], 0)})" if ok else
                            f"{_msq(-slack)} 초과"),
                         f"<b>{esc(c.get('budget', ''))}</b>: worst {_msq(c['worst_s'])} / allocated {_msq(c['allocated_s'])} — "
                         + (f"slack {_msq(slack)} ({pct(slack, c['allocated_s'], 0)} of the allocation)" if ok else
                            f"{_msq(-slack)} over")), lvl)
    dups = res.get("duplicate_budgets") or []
    gaps = res.get("gaps") or []
    if dups or gaps:
        s = ins.section(tr("예산 정의의 문제", "problems in the budget definition"))
        chosen = set(res.get("chosen_path") or [])
        for d in dups:
            its = d.get("items") or []
            on = [i for i in its if i in chosen]
            span = esc(str(d.get("overlap", "")).replace("->", "→"))
            s.add(tr(f"<b>이중 계산</b>: {' · '.join(f'{esc(i)}({esc(o)})' for i, o in zip(its, d.get('owners') or []))}가 같은 구간 "
                     f"({span})을 각자 예산에 넣음 — 둘을 더하면 이 구간이 두 번 셈",
                     f"<b>double count</b>: {' · '.join(f'{esc(i)} ({esc(o)})' for i, o in zip(its, d.get('owners') or []))} "
                     f"both budget {span} — summing both counts it twice"), "warn",
                  tr(f"선택 경로의 합에는 {', '.join(on) or '둘 다 아님'}만 들어감", f"the chosen path sums only "
                     f"{', '.join(on) or 'neither'}"))
        for g in gaps:
            s.add(tr(f"<b>예산 공백</b>: {esc(g)}", f"<b>unbudgeted interval</b>: {esc(g)}"), "open")
    s = ins.section(tr("판정", "claim"))
    if claim:
        claim_item(s, claim)
    notes_section(ins, res.get("notes"))
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- DC link
def _emf_items(s, res: dict) -> None:
    bemf, vf = res.get("back_emf_ll_peak_V"), res.get("Vf_V")
    if bemf is None:
        if "back_emf_basis" in res:
            s.add(tr("역기전력을 알 수 없음 — 회전 중 방전 가능성은 미확정", "back-EMF unknown — discharge while spinning undecided"),
                  "open", esc(engine_text(res.get("back_emf_basis", ""))))
        return
    nmax = res.get("max_speed_for_target_rpm")
    if res.get("rectification_risk"):
        s.add(tr(f"역기전력 선간 peak {q(bemf, 'V')} > 목표 {q(vf, 'V')}: 인버터 다이오드가 링크를 다시 충전합니다 — RC 시간은 하한일 뿐이고, "
                 f"목표 도달 여부는 모터 임피던스와 R의 결합 모델이 정합니다",
                 f"back-EMF line-line peak {q(bemf, 'V')} > target {q(vf, 'V')}: the inverter diodes recharge the link — "
                 f"the RC time is only a lower bound; whether the target is reached needs the coupled machine/R model"), "bad",
              esc(engine_text(res.get("back_emf_basis", ""))))
        est = res.get("rectified_link_screening")
        if est:
            s.add(tr(f"스크리닝 추정: R에 걸린 링크 전압 {q(est.get('V_dc_V'), 'V')} (I_dc {q(est.get('I_dc_A'), 'A', 3)}, "
                     f"R 소모 {q(est.get('P_W'), 'W', 3)}, 상전류 {q(est.get('phase_current_A_peak'), 'A', 3)} peak) — 상한이 아닌 추정",
                     f"screening estimate: link held at {q(est.get('V_dc_V'), 'V')} across R (I_dc {q(est.get('I_dc_A'), 'A', 3)}, "
                     f"{q(est.get('P_W'), 'W', 3)} in R, phase {q(est.get('phase_current_A_peak'), 'A', 3)} peak) — an "
                     f"estimate, not a bound"), "warn")
    else:
        s.add(tr(f"역기전력 선간 peak {q(bemf, 'V')} ≤ 목표 {q(vf, 'V')}: 이 속도에서는 정류가 방전을 막지 않습니다",
                 f"back-EMF line-line peak {q(bemf, 'V')} ≤ target {q(vf, 'V')}: rectification does not block the "
                 f"discharge at this speed"), "ok", esc(engine_text(res.get("back_emf_basis", ""))))
    if nmax is not None:
        s.add(tr(f"역기전력이 목표 전압을 넘지 않는 최고 속도 {q(nmax, 'rpm')} — 이보다 빨리 돌면 방전이 목표에 닿지 못할 수 있습니다",
                 f"highest speed with back-EMF ≤ target: {q(nmax, 'rpm')} — above it the discharge may not reach the target"),
              "info")


def discharge_insight(res: dict) -> Insight:
    claim = res.get("claim") or {}
    v = verdict_of(claim)
    t, tt = res.get("t_reach_s"), res.get("t_target_s")
    R, rmax = res.get("R_used_ohm"), res.get("R_max_ohm")
    head = tr(f"능동 방전 {q(res.get('V0_V'), 'V')} → {q(res.get('Vf_V'), 'V')}: {q(t, 's')} (허용 {q(tt, 's')}), R {q(R, 'Ω')}",
              f"active discharge {q(res.get('V0_V'), 'V')} → {q(res.get('Vf_V'), 'V')}: {q(t, 's')} (allowed {q(tt, 's')}), "
              f"R {q(R, 'Ω')}")
    if res.get("rectification_risk"):
        head += tr(" — 단 역기전력 정류로 목표 도달 미확정", " — but rectified back-EMF leaves the target undecided")
    ins = Insight(headline=head, verdict=v)
    ins.metrics += [(tr("도달 시간 / 허용", "time / allowed"), f"{num(t)} / {num(tt)} s", status_level(claim.get("status"))),
                    (tr("R 사용 / 최대", "R used / maximum"), f"{num(R)} / {num(rmax)} Ω", "info"),
                    (tr("초기 전류 · 전력", "initial current · power"), f"{q(res.get('I0_A'), 'A', 3)} · {q(res.get('P0_W'), 'W')}",
                     "info"),
                    (tr("저장 에너지", "stored energy"), q(res.get("stored_energy_J"), "J"), "info")]
    s = ins.section(tr("방전 메커니즘 (RC)", "the discharge (RC)"))
    ln = math.log(res["V0_V"] / res["Vf_V"]) if res.get("V0_V") and res.get("Vf_V") else None
    s.add(tr(f"시정수 τ = R·C = {q(res.get('tau_s'), 's')}; 목표까지 t = τ·ln(V₀/V_f) = {q(res.get('tau_s'), 's')} × {num(ln, 4)} = "
             f"{q(t, 's')}",
             f"time constant τ = R·C = {q(res.get('tau_s'), 's')}; to the target t = τ·ln(V₀/V_f) = {q(res.get('tau_s'), 's')} × "
             f"{num(ln, 4)} = {q(t, 's')}"), "info")
    if R and rmax:
        if abs(R - rmax) <= 1e-9 * rmax:
            s.add(tr(f"허용 시간을 지키는 최대 저항 R_max = {q(rmax, 'Ω')}를 사용 — 허용 시간에 정확히 도달 (시간 여유 0)",
                     f"the largest R meeting the time, R_max = {q(rmax, 'Ω')}, is used — the target is reached exactly at "
                     f"the allowed time (no slack)"), "ok")
        elif R < rmax:
            s.add(tr(f"허용 시간을 지키는 최대 저항 R_max = {q(rmax, 'Ω')} — 사용 R은 그 {pct(R, rmax, 0)}, 시간 여유 {q(tt - t, 's', 3)}",
                     f"the largest R meeting the time is {q(rmax, 'Ω')} — the R used is {pct(R, rmax, 0)} of it, time "
                     f"slack {q(tt - t, 's', 3)}"), "ok")
        else:
            s.add(tr(f"사용 R {q(R, 'Ω')} > R_max {q(rmax, 'Ω')}: {q(t - tt, 's', 3)} 늦음 — R을 R_max 이하로",
                     f"R used {q(R, 'Ω')} > R_max {q(rmax, 'Ω')}: {q(t - tt, 's', 3)} late — R must be ≤ R_max"), "bad")
    s.add(tr(f"저항이 받는 부담: 초기 {q(res.get('I0_A'), 'A', 3)} · {q(res.get('P0_W'), 'W')} (V₀²/R), 펄스 에너지 "
             f"{q(res.get('E_R_J'), 'J')} = ½·C·(V₀² − V_f²) — 저장 에너지 {q(res.get('stored_energy_J'), 'J')}의 "
             f"{pct(res.get('E_R_J'), res.get('stored_energy_J'), 0)}",
             f"what the resistor takes: initially {q(res.get('I0_A'), 'A', 3)} · {q(res.get('P0_W'), 'W')} (V₀²/R), pulse "
             f"energy {q(res.get('E_R_J'), 'J')} = ½·C·(V₀² − V_f²) — {pct(res.get('E_R_J'), res.get('stored_energy_J'), 0)} "
             f"of the stored {q(res.get('stored_energy_J'), 'J')}"), "info",
          tr("저항의 peak 전력·에너지 정격과 비교하세요 (정격을 넣으면 판정에 포함)",
             "compare with the resistor's peak power and energy ratings"))
    s = ins.section(tr("모터가 돌고 있으면", "if the motor is spinning"))
    _emf_items(s, res)
    s = ins.section(tr("판정", "claim"))
    if claim:
        claim_item(s, claim)
    notes_section(ins, res.get("assumptions"), title=tr("가정 (이 결과가 말하지 않는 것)", "assumptions (what this does not cover)"))
    return ins.nonempty()


def passive_insight(res: dict) -> Insight:
    claim = res.get("claim") or {}
    v = verdict_of(claim)
    t, tt = res.get("t_reach_s"), res.get("t_target_s")
    R, rmin, rmax = res.get("R_used_ohm"), res.get("R_min_ohm"), res.get("R_max_ohm")
    k = lambda r: None if r is None else r / 1e3            # noqa: E731
    head = tr(f"블리더 R_p {q(k(R), 'kΩ')}: {q(res.get('V0_V'), 'V')} → {q(res.get('Vf_V'), 'V')} {q(t, 's')} (요구 {q(tt, 's')}), "
              f"상시 손실 {q(res.get('P_cont_nom_W'), 'W', 3)} @ {q(res.get('V_nom_V'), 'V')}",
              f"bleeder R_p {q(k(R), 'kΩ')}: {q(res.get('V0_V'), 'V')} → {q(res.get('Vf_V'), 'V')} in {q(t, 's')} (required "
              f"{q(tt, 's')}), continuous loss {q(res.get('P_cont_nom_W'), 'W', 3)} @ {q(res.get('V_nom_V'), 'V')}")
    ins = Insight(headline=head, verdict=v)
    ins.metrics += [(tr("도달 시간 / 요구", "time / required"), f"{num(t)} / {num(tt)} s",
                     "ok" if t is not None and tt is not None and t <= tt * (1 + 1e-12) else "bad"),
                    (tr("R_p 창", "R_p window"), f"{num(k(rmin))} – {num(k(rmax))} kΩ" if rmin is not None else
                     f"≤ {num(k(rmax))} kΩ", "info"),
                    (tr("상시 손실 (정격 / 최대 V)", "continuous loss (nominal / max V)"),
                     f"{num(res.get('P_cont_nom_W'), 3)} / {num(res.get('P_cont_max_W'), 3)} W",
                     "info" if res.get("P_allow_W") is None else
                     ("ok" if res.get("P_cont_max_W", 0) <= res["P_allow_W"] * (1 + 1e-12) else "bad"))]
    s = ins.section(tr("설계 창: 빠른 방전 ↔ 작은 상시 손실", "the design window: fast discharge ↔ small continuous loss"))
    s.add(tr(f"시간 조건 R_p ≤ t_req/(C·ln(V₀/V_f)) = {q(k(rmax), 'kΩ')}", f"time: R_p ≤ t_req/(C·ln(V₀/V_f)) = {q(k(rmax), 'kΩ')}"),
          "ok" if R is not None and rmax is not None and R <= rmax * (1 + 1e-12) else "bad",
          tr(f"사용 R_p에서 {q(t, 's')} → 요구 대비 여유 {q((tt or 0) - (t or 0), 's', 3)}",
             f"with the R_p used: {q(t, 's')} → slack {q((tt or 0) - (t or 0), 's', 3)} to the requirement"))
    if rmin is not None:
        s.add(tr(f"손실 조건 R_p ≥ V_max²/P_허용 = {num(res.get('V_max_V'))}²/{num(res.get('P_allow_W'))} W = {q(k(rmin), 'kΩ')}",
                 f"loss: R_p ≥ V_max²/P_allow = {num(res.get('V_max_V'))}²/{num(res.get('P_allow_W'))} W = {q(k(rmin), 'kΩ')}"),
              "ok" if R is not None and R >= rmin * (1 - 1e-12) else "bad",
              tr(f"사용 R_p에서 최대 전압 손실 {q(res.get('P_cont_max_W'), 'W', 3)} / 허용 {q(res.get('P_allow_W'), 'W', 3)}",
                 f"with the R_p used: {q(res.get('P_cont_max_W'), 'W', 3)} at the maximum voltage / allowed "
                 f"{q(res.get('P_allow_W'), 'W', 3)}"))
        if rmax is not None and rmin <= rmax * (1 + 1e-12) and R is not None:
            s.add(tr(f"창 [{num(k(rmin))}, {num(k(rmax))}] kΩ 안에서 R_p {num(k(R))} kΩ의 위치: 손실 쪽 끝에서 {pct(R - rmin, rmax - rmin, 0)}"
                     f" (0 % = 손실 한계, 100 % = 시간 한계)",
                     f"R_p {num(k(R))} kΩ in the window [{num(k(rmin))}, {num(k(rmax))}] kΩ: {pct(R - rmin, rmax - rmin, 0)} "
                     f"from the loss edge (0 % = loss limit, 100 % = time limit)"), "info")
        elif rmax is not None and rmin > rmax:
            s.add(tr(f"창이 비어 있음: 손실 조건 {q(k(rmin), 'kΩ')} > 시간 조건 {q(k(rmax), 'kΩ')} — 블리더만으로는 불가, 능동 방전이나 "
                     f"요구 완화가 필요",
                     f"the window is empty: the loss needs ≥ {q(k(rmin), 'kΩ')} but the time needs ≤ {q(k(rmax), 'kΩ')} — "
                     f"a bleeder alone cannot do both"), "bad")
    s.add(tr(f"맞바꿈: 상시 손실 × 방전 시간 = C·V_nom²·ln(V₀/V_f) = {q(res.get('loss_time_product_Ws'), 'W·s')} — R_p와 무관하므로 "
             f"방전을 2배 빠르게 하면 상시 손실이 2배",
             f"the trade: continuous loss × discharge time = C·V_nom²·ln(V₀/V_f) = {q(res.get('loss_time_product_Ws'), 'W·s')} "
             f"— independent of R_p, so a discharge twice as fast costs twice the continuous loss"), "info")
    wa = res.get("with_active") or {}
    if wa:
        s = ins.section(tr("능동 방전과 함께", "with the active discharge"))
        s.add(tr(f"R_a {q(wa.get('R_active_ohm'), 'Ω')} ‖ R_p = {q(wa.get('R_parallel_ohm'), 'Ω')} → {q(wa.get('t_reach_s'), 's')} "
                 f"(블리더만 {q(t, 's')})",
                 f"R_a {q(wa.get('R_active_ohm'), 'Ω')} ‖ R_p = {q(wa.get('R_parallel_ohm'), 'Ω')} → {q(wa.get('t_reach_s'), 's')} "
                 f"(bleeder alone {q(t, 's')})"), "info")
    if "back_emf_ll_peak_V" in res or "back_emf_basis" in res:
        s = ins.section(tr("모터가 돌고 있으면", "if the motor is spinning"))
        _emf_items(s, res)
    s = ins.section(tr("판정", "claim"))
    if claim:
        claim_item(s, claim)
    notes_section(ins, res.get("assumptions"), title=tr("가정 (이 결과가 말하지 않는 것)", "assumptions (what this does not cover)"))
    return ins.nonempty()


_PROFILE = {"constant": lambda: tr("일정 (반응 시간까지 유지)", "constant until the reaction"),
            "linear_ramp_down": lambda: tr("선형 감소 (즉시 시작)", "linear ramp-down (starting at once)"),
            "delay_then_ramp": lambda: tr("지연 후 선형 감소", "delay, then a linear ramp")}


def overvoltage_insight(res: dict) -> Insight:
    claim = res.get("claim") or {}
    v = verdict_of(claim)
    vp, vl, v1 = res.get("V_peak_V"), res.get("V_limit_V"), res.get("V1_V")
    ta, tr_ = res.get("max_reaction_time_s"), res.get("reaction_time_s")
    if vp is None:
        head = tr(f"회생 {kw(res.get('P_in_W'))} 중 배터리 차단: {q(v1, 'V')} → 한계 {q(vl, 'V')}까지 허용 반응 {_msq(ta, 3)}",
                  f"battery disconnect while regenerating {kw(res.get('P_in_W'))}: {q(v1, 'V')} → limit {q(vl, 'V')} "
                  f"allows {_msq(ta, 3)} to react")
    else:
        head = tr(f"회생 {kw(res.get('P_in_W'))} 중 배터리 차단: 최고 {q(vp, 'V')} {'≤' if vp <= vl else '>'} 한계 {q(vl, 'V')} — "
                  f"허용 반응 {_msq(ta, 3)}, 선언 {_msq(tr_, 3)}",
                  f"battery disconnect while regenerating {kw(res.get('P_in_W'))}: peak {q(vp, 'V')} "
                  f"{'≤' if vp <= vl else '>'} limit {q(vl, 'V')} — allowed reaction {_msq(ta, 3)}, declared {_msq(tr_, 3)}")
    ins = Insight(headline=head, verdict=v)
    ins.metrics += [(tr("최고 / 한계", "peak / limit"), f"{num(vp)} / {num(vl)} V", status_level(claim.get("status"))),
                    (tr("허용 / 선언 반응", "allowed / declared reaction"), f"{num(_ms(ta), 3)} / {num(_ms(tr_), 3)} ms",
                     "info" if tr_ is None else ("ok" if tr_ <= (ta or 0) * (1 + 1e-12) else "bad")),
                    (tr("초기 dV/dt", "initial dV/dt"), q((res.get("dVdt_initial_V_per_s") or 0) / 1e3, "V/ms", 3), "info")]
    s = ins.section(tr("에너지 수지 (링크가 받는 것)", "energy balance (what the link takes)"))
    op = res.get("regen_operating_point") or {}
    if op:
        s.add(tr(f"회생 동작점 id {q(op.get('id_A'), 'A')}, iq {q(op.get('iq_A'), 'A')} → DC로 {kw(-op['Pdc_W'] if op.get('Pdc_W') else None)}"
                 f" 유입 (배터리가 끊기면 커패시터만 받음)",
                 f"regen point id {q(op.get('id_A'), 'A')}, iq {q(op.get('iq_A'), 'A')} → {kw(-op['Pdc_W'] if op.get('Pdc_W') else None)}"
                 f" into the DC link (only the capacitor takes it once the battery is gone)"), "info")
    s.add(tr(f"한계까지 흡수 여유 ½·C·(V_lim² − V₁²) = {q(res.get('energy_headroom_J'), 'J')}; 일정 전력이면 "
             f"{_msq(res.get('time_to_limit_constant_power_s'), 3)} 만에 소진 (초기 dV/dt = P/(C·V₁) = "
             f"{q((res.get('dVdt_initial_V_per_s') or 0) / 1e3, 'V/ms', 3)})",
             f"headroom to the limit ½·C·(V_lim² − V₁²) = {q(res.get('energy_headroom_J'), 'J')}; at constant power it is "
             f"used up in {_msq(res.get('time_to_limit_constant_power_s'), 3)} (initial dV/dt = P/(C·V₁) = "
             f"{q((res.get('dVdt_initial_V_per_s') or 0) / 1e3, 'V/ms', 3)})"), "info")
    ein, head_j = res.get("energy_in_J"), res.get("energy_headroom_J")
    if ein is not None and head_j:
        over = ein > head_j
        s.add(tr(f"선언 반응 {_msq(tr_, 3)} 동안 유입 {q(ein, 'J')} = 여유의 {num(ein / head_j, 3)}배 → V_peak = √(V₁² + 2E/C) = {q(vp, 'V')}",
                 f"during the declared reaction {_msq(tr_, 3)} {q(ein, 'J')} flows in = {num(ein / head_j, 3)}× the headroom → "
                 f"V_peak = √(V₁² + 2E/C) = {q(vp, 'V')}"), "bad" if over else "ok",
              tr("전력 프로파일: ", "power profile: ") + _PROFILE.get(res.get("profile"), lambda: esc(res.get("profile", "")))())
        c, vl2 = res.get("C_F"), (vl or 0) ** 2 - (v1 or 0) ** 2
        if over and c and vl2 > 0:
            s = ins.section(tr("무엇이 이 결과를 바꾸나", "what would change it"))
            s.add(tr(f"반응 시간을 {_msq(ta, 3)} 이하로 (현재 {_msq(tr_, 3)})", f"react within {_msq(ta, 3)} (now {_msq(tr_, 3)})"),
                  "info")
            s.add(tr(f"또는 이 반응 시간을 흡수할 커패시턴스 C ≥ 2E/(V_lim² − V₁²) = {q(2 * ein / vl2 * 1e6, 'µF')} (현재 "
                     f"{q(c * 1e6, 'µF')})",
                     f"or the capacitance that absorbs this reaction: C ≥ 2E/(V_lim² − V₁²) = {q(2 * ein / vl2 * 1e6, 'µF')} "
                     f"(now {q(c * 1e6, 'µF')})"), "info",
                  tr("E는 C와 무관한 유입 에너지 (선언된 전력 프로파일)", "E is the energy flowing in, independent of C (declared profile)"))
    bemf = res.get("back_emf_ll_peak_V")
    if bemf is not None:
        s = ins.section(tr("인버터를 끄면 (freewheel)", "if the inverter only turns off (freewheel)"))
        s.add(tr(f"역기전력 선간 peak {q(bemf, 'V')} {'>' if bemf > (vl or 0) else '≤'} 한계 {q(vl, 'V')} — "
                 + ("다이오드 정류가 고립된 링크를 계속 충전할 수 있어 freewheel만으로는 부족"
                    if bemf > (vl or 0) else "정상상태 정류로는 한계를 넘지 않음"),
                 f"back-EMF line-line peak {q(bemf, 'V')} {'>' if bemf > (vl or 0) else '≤'} limit {q(vl, 'V')} — "
                 + ("the diodes can keep charging the isolated link: freewheel alone is not sufficient"
                    if bemf > (vl or 0) else "steady rectification does not reach the limit")),
              "bad" if bemf > (vl or 0) else "ok")
    s = ins.section(tr("판정", "claim"))
    if claim:
        claim_item(s, claim)
    notes_section(ins, res.get("notes"), res.get("assumptions"))
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- safe state
def safe_state_insight(res: dict) -> Insight:
    claim = res.get("claim") or {}
    n, vdc = res.get("speed_rpm"), res.get("Vdc_V")
    a = res.get("asc_detail") or {}
    fw = next((c for c in res.get("candidates") or [] if str(c.get("candidate", "")).startswith("FREEWHEEL")), {})
    bemf, onset = fw.get("back_emf_ll_peak_V"), fw.get("ucg_onset_speed_rpm")
    hv = {"battery_connected": tr("배터리 연결", "battery connected"),
          "battery_disconnected": tr("배터리 분리", "battery disconnected")}.get(res.get("hv_state"), res.get("hv_state"))
    parts = []
    if a.get("evaluable"):
        parts.append(tr(f"ASC 정상상태 |i| {q(a.get('i_peak_A'), 'A')} (한계 {q(a.get('current_limit_A'), 'A')}), 제동 "
                        f"{q(a.get('Tshaft_Nm'), 'N·m', 3)}",
                        f"ASC steady |i| {q(a.get('i_peak_A'), 'A')} (limit {q(a.get('current_limit_A'), 'A')}), braking "
                        f"{q(a.get('Tshaft_Nm'), 'N·m', 3)}"))
    if bemf is not None:
        parts.append(tr(f"freewheel 역기전력 {q(bemf, 'V')} {'>' if bemf > vdc else '≤'} Vdc"
                        + (" → 비제어 정류" if bemf > vdc else " → 정류 없음"),
                        f"freewheel back-EMF {q(bemf, 'V')} {'>' if bemf > vdc else '≤'} Vdc"
                        + (" → uncontrolled rectification" if bemf > vdc else " → no rectification")))
    head = tr(f"{q(n, 'rpm')}, Vdc {q(vdc, 'V')} ({hv}): ", f"{q(n, 'rpm')}, Vdc {q(vdc, 'V')} ({hv}): ") + "; ".join(parts)
    ins = Insight(headline=head, verdict=verdict_of(claim))
    if a.get("evaluable"):
        ins.metrics += [(tr("ASC 정상 전류", "ASC steady current"), f"{num(a.get('i_peak_A'))} A",
                         "ok" if a.get("i_peak_A", 0) <= a.get("current_limit_A", float("inf")) else "bad"),
                        (tr("ASC 제동 토크", "ASC braking torque"), q(a.get("Tshaft_Nm"), "N·m", 3), "info"),
                        (tr("ASC 동손", "ASC copper loss"), kw(a.get("copper_loss_W")), "info")]
    if bemf is not None:
        ins.metrics.append((tr("freewheel 역기전력 / Vdc", "freewheel back-EMF / Vdc"), f"{num(bemf)} / {num(vdc)} V",
                            "bad" if bemf > vdc else "ok"))
    s = ins.section(tr("ASC (3상 단락)", "ASC (three-phase short)"))
    if a.get("evaluable"):
        s.add(tr(f"정상상태 id {q(a.get('id_A'), 'A')}, iq {q(a.get('iq_A'), 'A')} → |i| {q(a.get('i_peak_A'), 'A')} peak "
                 f"({q(a.get('i_phase_rms_A'), 'A')} rms) = 전류 한계의 {pct(a.get('i_peak_A'), a.get('current_limit_A'), 0)}",
                 f"steady id {q(a.get('id_A'), 'A')}, iq {q(a.get('iq_A'), 'A')} → |i| {q(a.get('i_peak_A'), 'A')} peak "
                 f"({q(a.get('i_phase_rms_A'), 'A')} rms) = {pct(a.get('i_peak_A'), a.get('current_limit_A'), 0)} of the "
                 f"current limit"), "ok" if a.get("i_peak_A", 0) <= a.get("current_limit_A", float("inf")) else "bad",
              esc(engine_text(a.get("method", ""))))
        idv, iqv = abs(a.get("id_A") or 0), abs(a.get("iq_A") or 0)
        if idv + iqv > 0:
            s.add(tr(f"전류의 {pct(idv, idv + iqv, 0)}가 d축(|id|/(|id|+|iq|)) — 단락 전류가 자석 자속을 상쇄하는 방향이라 토크가 작습니다: "
                     f"축 토크 {q(a.get('Tshaft_Nm'), 'N·m', 3)} (전자기 {q(a.get('Te_Nm'), 'N·m', 3)})",
                     f"{pct(idv, idv + iqv, 0)} of the current is on the d axis (|id|/(|id|+|iq|)) — the short-circuit "
                     f"current opposes the magnet flux, so the torque is small: shaft {q(a.get('Tshaft_Nm'), 'N·m', 3)} "
                     f"(electromagnetic {q(a.get('Te_Nm'), 'N·m', 3)})"), "info")
        s.add(tr(f"권선 동손 {kw(a.get('copper_loss_W'))} — ASC 동안 에너지는 권선에서 열이 되고 DC 링크로는 0 W",
                 f"winding copper loss {kw(a.get('copper_loss_W'))} — during ASC the energy becomes heat in the windings, "
                 f"0 W to the DC link"), "info")
        s.add(tr("진입 과도 전류·토크는 이 정상상태 값보다 클 수 있습니다 (보호 페이지의 ASC 과도에서 평가)",
                 "the entry transient current and torque can exceed these steady values (evaluate on the protection page's "
                 "ASC transient)"), "open")
    else:
        s.add(tr("ASC 정상상태를 평가할 수 없음", "the ASC steady state is not evaluable"), "open",
              esc(engine_text(a.get("reason", ""))))
    s = ins.section(tr("Freewheel / 6SO (전 스위치 off)", "freewheel / 6SO (all switches off)"))
    if bemf is None:
        s.add(tr("역기전력을 알 수 없음", "back-EMF unknown"), "open", esc(engine_text(fw.get("back_emf_risk", ""))))
    elif bemf > vdc:
        s.add(tr(f"역기전력 선간 peak {q(bemf, 'V')} > Vdc {q(vdc, 'V')} ({pct(bemf - vdc, vdc, 0)} 높음): 다이오드가 정류해 제어되지 "
                 f"않는 회생 제동이 걸립니다",
                 f"back-EMF line-line peak {q(bemf, 'V')} > Vdc {q(vdc, 'V')} ({pct(bemf - vdc, vdc, 0)} above): the diodes "
                 f"rectify, uncontrolled regenerative braking"), "bad")
        if onset is not None:
            s.add(tr(f"정류 시작 속도 {q(onset, 'rpm')} — 그 위에서는 freewheel이 안전 상태가 아닐 수 있음 (현재 {q(n, 'rpm')})",
                     f"rectification starts above {q(onset, 'rpm')} — above it freewheel may not be a safe state (now "
                     f"{q(n, 'rpm')})"), "warn")
        s.add(tr("배터리 분리 상태: 정류 에너지를 받을 곳이 없어 과전압 위험 높음" if res.get("hv_state") == "battery_disconnected"
                 else "배터리 연결 상태: 충전 전류가 제어되지 않음 — 충전 한계 확인",
                 "battery disconnected: the rectified energy has no sink (high overvoltage risk)"
                 if res.get("hv_state") == "battery_disconnected" else
                 "battery connected: the charging current is uncontrolled — check the charge limits"), "warn")
    else:
        s.add(tr(f"역기전력 선간 peak {q(bemf, 'V')} ≤ Vdc {q(vdc, 'V')}: 정상상태 정류 전류 없음, 제동 토크 ≈ 0 (회전 손실만)",
                 f"back-EMF line-line peak {q(bemf, 'V')} ≤ Vdc {q(vdc, 'V')}: no rectified current in steady state, "
                 f"braking ≈ 0 (rotational loss only)"), "ok")
        if onset is not None:
            s.add(tr(f"정류 시작 속도 {q(onset, 'rpm')} (현재 {q(n, 'rpm')}, 여유 {q(onset - n, 'rpm')})",
                     f"rectification would start above {q(onset, 'rpm')} (now {q(n, 'rpm')}, {q(onset - n, 'rpm')} below)"),
                  "info")
    for key, lab in (("device_voltage_stress", tr("소자 전압", "device voltage")),
                     ("dc_link_limit_check", tr("DC 링크 한계", "DC-link limit"))):
        if fw.get(key):
            s.add(f"{lab}: {esc(engine_text(fw[key]))}", "warn" if "exceed" in fw[key] or "above" in fw[key] else "info")
    rules = res.get("project_rules") or []
    if rules:
        s = ins.section(tr("프로젝트·고객 규칙 (물리 판정과 별개)", "project / customer rules (separate from physics)"))
        for r in rules:
            s.add(tr(f"<b>{esc(r.get('rule_id', ''))}</b>: {'적용됨' if r.get('applies') else '조건 불일치 (적용 안 됨)'} — 요구 "
                     f"{esc(r.get('require') or '—')}, 금지 {esc(r.get('forbid') or '—')}",
                     f"<b>{esc(r.get('rule_id', ''))}</b>: {'applies' if r.get('applies') else 'does not apply'} — require "
                     f"{esc(r.get('require') or '—')}, forbid {esc(r.get('forbid') or '—')}"),
                  "warn" if r.get("applies") else "info", esc(r.get("basis", "")))
    s = ins.section(tr("판정", "claim"))
    if claim:
        claim_item(s, claim)
    notes_section(ins, res.get("notes"))
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- protection
_PROT_ITEM = {"no nuisance trip": ("오동작 없음 (정상 운전에서 트립 안 함)", "no nuisance trip"),
              "fault protection before the physical limit": ("물리 한계 전에 보호", "protection before the physical limit"),
              "warning usefulness": ("경고의 유효성 (고장 전 개입 시간)", "warning usefulness (lead before the fault)"),
              "derating / reaction effectiveness": ("디레이팅·반응 효과", "derating / reaction effectiveness"),
              "threshold feasibility window": ("임계값 창", "threshold window"),
              "recovery (hysteresis, re-trigger)": ("복귀 (히스테리시스·재트리거)", "recovery (hysteresis, re-trigger)"),
              "independent HW path": ("독립 HW 경로", "independent HW path"),
              "combined faults / priority": ("복합 고장·우선순위", "combined faults / priority"),
              "model sufficiency": ("모델 충분성", "model sufficiency")}


def _unit(u: str) -> str:
    return {"degC": "°C"}.get(u, u)


def protection_insight(res: dict) -> Insight:
    u = _unit(res.get("unit", ""))
    th = res.get("thresholds") or {}
    lim = res.get("limit")
    ps = res.get("phase_sweep") or {}
    rows = res.get("rows") or []
    summary = res.get("summary_status")
    v = {"FEASIBLE": "PASS", "INFEASIBLE": "FAIL", "UNKNOWN": "UNKNOWN"}.get(summary)
    peak = ps.get("worst_peak")
    failed = [r for r in rows if r.get("status") == "INFEASIBLE"]
    lab = lambda r: tr(*_PROT_ITEM[r["item"]]) if r.get("item") in _PROT_ITEM else r.get("item", "")    # noqa: E731
    p4 = next((r.get("status") for r in rows if r.get("id") == "PROT-04"), None)
    p4_level = {"FEASIBLE": "ok", "INFEASIBLE": "bad"}.get(p4, "warn")
    if peak is not None and lim is not None:
        if not ps.get("all_protected"):
            prot = tr(f"최고 {q(peak, u)} ≥ 한계 {q(lim, u)} ({num(ps.get('phases'))}개 샘플 위상 중 보호 실패 있음)",
                      f"peak {q(peak, u)} ≥ limit {q(lim, u)} (not protected in some of {num(ps.get('phases'))} "
                      f"sampled phases)")
        elif p4 == "FEASIBLE":
            prot = tr(f"최고 {q(peak, u)} < 한계 {q(lim, u)} ({num(ps.get('phases'))}개 샘플 위상 모두 보호)",
                      f"peak {q(peak, u)} < limit {q(lim, u)} (protected in all {num(ps.get('phases'))} sampled phases)")
        elif p4 == "INFEASIBLE":
            prot = tr(f"관측 구간 최고 {q(peak, u)} < 한계 {q(lim, u)}이지만 반응 전·후 정확해가 한계에 도달 (보호 실패)",
                      f"peak {q(peak, u)} < limit {q(lim, u)} inside the horizon, but the exact solution reaches the "
                      f"limit before or after the reaction (not protected)")
        else:
            prot = tr(f"관측 구간 최고 {q(peak, u)} < 한계 {q(lim, u)} — 검출이 구간 밖이라 미확정",
                      f"peak {q(peak, u)} < limit {q(lim, u)} inside the horizon — the detection lies beyond it "
                      f"(undecided)")
    else:
        prot = ""
    tail = (tr(" — 불만족: ", " — not met: ") + ", ".join(f"{r['id']} {lab(r)}" for r in failed)) if failed else ""
    ins = Insight(headline=f"{esc(engine_text(res.get('variable', '')))}: {prot}{tail}", verdict=v)
    if peak is not None and lim is not None:
        ins.metrics.append((tr("최고 / 한계", "peak / limit"), f"{num(peak)} / {num(lim)} {u}", p4_level))
        ins.metrics.append((tr("한계까지 여유", "margin to the limit"), q(lim - peak, u, 3),
                            "ok" if lim - peak > 0 else "bad"))
    w = res.get("window") or {}
    if w:
        ins.metrics.append((tr("임계값 창", "threshold window"), f"({num(w.get('nuisance_lower_bound'))}, "
                            f"{num(w.get('protection_upper_bound'))}] {u}", "ok" if w.get("window_exists") else "bad"))
    s = ins.section(tr("PROT 항목", "PROT items"))
    for r in rows:
        word = state_label(VERDICT_OF.get(r.get("status", ""), r.get("status", "")))     # a PROT row is a requirement check
        s.add(f"<b>{esc(r.get('id', ''))} {lab(r)}</b>: {word}", status_level(r.get("status")),
              esc(engine_parts(r.get("detail", ""))))
    ev = (res.get("trace") or {}).get("events") or {}
    if ev:
        s = ins.section(tr("한 궤적 위의 인과 순서 (최악 위상)", "cause and effect on one trajectory (worst phase)"),
                        tr("물리량이 임계를 넘는 순간부터 반응이 효과를 내기까지 — 각 단계의 지연이 한계까지의 여유를 씁니다.",
                           "from the physical crossing to the reaction taking effect — every stage's delay spends margin."))
        steps = [("t_xcross_s", tr("물리량이 고장 임계 통과", "the physical variable crosses the fault threshold")),
                 ("t_ycross_s", tr("측정값이 임계 통과 (센서 이득·오프셋·필터)", "the measurement crosses it (sensor gain, offset, "
                                                                          "filter)")),
                 ("t_first_sample_s", tr("임계를 넘은 첫 샘플", "first sample above it")),
                 ("t_confirm_s", tr("N연속 확인", "N consecutive samples confirm")),
                 ("t_action_effective_s", tr("반응이 플랜트에 효과", "the reaction takes effect on the plant")),
                 ("t_peak_s", tr("최고치", "peak"))]
        t0 = ev.get("t_xcross_s")
        prev = None
        for t, text in sorted(((ev[k], text) for k, text in steps if ev.get(k) is not None), key=lambda kv: kv[0]):
            s.add(f"{_msq(t, 4)}: {text}" + ("" if prev is None else f" (+{_msq(t - prev, 3)})"), "info")
            prev = t
        if t0 is not None and ev.get("t_action_effective_s") is not None:
            s.add(tr(f"임계 통과 → 반응 효과까지 {_msq(ev['t_action_effective_s'] - t0, 3)}; 그 동안 물리량이 최고 {q(ev.get('peak'), u)}까지"
                     + (f" — 한계까지 {q(lim - ev['peak'], u, 3)} 남음" if lim is not None and ev.get("peak") is not None else ""),
                     f"crossing → reaction effective: {_msq(ev['t_action_effective_s'] - t0, 3)}; meanwhile the variable rises "
                     f"to {q(ev.get('peak'), u)}"
                     + (f" — {q(lim - ev['peak'], u, 3)} left to the limit" if lim is not None and ev.get("peak") is not None else "")),
                  "ok" if ev.get("t_limit_s") is None else "bad")
        if ev.get("t_limit_s") is not None:
            s.add(tr(f"한계 도달 {_msq(ev['t_limit_s'], 4)} — 검출이 있어도 보호 실패", f"the limit is reached at {_msq(ev['t_limit_s'], 4)} "
                     f"— a failure even though detection happened"), "bad")
    if ps:
        s = ins.section(tr("샘플 위상에 따른 차이", "across the sampling phases"))
        s.add(tr(f"{num(ps.get('phases'))}개 위상: 확인 시각 {_msq(ps.get('earliest_confirm_s'), 4)} – {_msq(ps.get('latest_confirm_s'), 4)}, "
                 f"최악 최고치 {q(peak, u)} (위상 {_msq(ps.get('worst_phase_s'), 3)})",
                 f"{num(ps.get('phases'))} phases: confirmation {_msq(ps.get('earliest_confirm_s'), 4)} – "
                 f"{_msq(ps.get('latest_confirm_s'), 4)}, worst peak {q(peak, u)} (phase {_msq(ps.get('worst_phase_s'), 3)})"),
              p4_level,
              tr("표본 위상에 대한 결과이며 연속 고장 영역 전체의 증명은 아님", "sampled phases, not a proof over the continuous fault domain"))
    if w:
        t = w.get("terms") or {}
        s = ins.section(tr("임계값 창 (충분조건)", "threshold window (sufficient conditions)"),
                        tr("창이 비면 '보장 불가(미확정)'이고, bound가 tight·동시달성 가능하다고 선언된 경우에만 '임계값 조정만으로 불가'입니다.",
                           "an empty window means 'not guaranteed' (undecided); only tight, jointly attainable bounds make "
                           "it 'threshold tuning alone cannot work'."))
        s.add(tr(f"오동작 방지 하한 θ > x_N,max + E₊ + E_θ = {num(t.get('x_normal_max'))} + {num(t.get('E_plus'))} + "
                 f"{num(t.get('E_theta'))} = {q(w.get('nuisance_lower_bound'), u)}",
                 f"no-nuisance lower bound θ > x_N,max + E₊ + E_θ = {num(t.get('x_normal_max'))} + {num(t.get('E_plus'))} + "
                 f"{num(t.get('E_theta'))} = {q(w.get('nuisance_lower_bound'), u)}"), "info",
              tr(f"정상 운전 예비 {num(t.get('reserve_normal'))}", f"normal reserve {num(t.get('reserve_normal'))}"))
        s.add(tr(f"보호 상한 θ ≤ {num(t.get('physics_upper_bound'))} − E₋ {num(t.get('E_minus'))} − E_θ {num(t.get('E_theta'))}"
                 f" − Δx {num(t.get('dx_after'))} = {q(w.get('protection_upper_bound'), u)}",
                 f"protection upper bound θ ≤ {num(t.get('physics_upper_bound'))} − E₋ {num(t.get('E_minus'))} − E_θ "
                 f"{num(t.get('E_theta'))} − Δx {num(t.get('dx_after'))} = {q(w.get('protection_upper_bound'), u)}"), "info",
              tr(f"물리 상한 {num(t.get('physics_upper_bound'))} {u} (한계 {num(t.get('x_limit'))} {u}에서 반응 뒤 추가 상승을 뺀 값)",
                 f"physics bound {num(t.get('physics_upper_bound'))} {u} (the limit {num(t.get('x_limit'))} {u} less the rise "
                 f"after the reaction)"))
        cand = w.get("candidate") or {}
        if w.get("window_exists"):
            th_n = cand.get("theta_nom", th.get("fault"))
            lo, hi = w.get("nuisance_lower_bound"), w.get("protection_upper_bound")
            s.add(tr(f"창 폭 {q(w.get('window_width'), u, 3)}; 고장 임계 {q(th_n, u)}는 하한에서 {q(th_n - lo, u, 3)}, 상한까지 {q(hi - th_n, u, 3)}",
                     f"window width {q(w.get('window_width'), u, 3)}; the fault threshold {q(th_n, u)} sits {q(th_n - lo, u, 3)} "
                     f"above the lower bound and {q(hi - th_n, u, 3)} below the upper"),
                  "ok" if cand.get("no_nuisance_guaranteed") and cand.get("protection_guaranteed") else "warn")
        else:
            s.add(tr(f"창이 비어 있음 (하한 {q(w.get('nuisance_lower_bound'), u)} ≥ 상한 {q(w.get('protection_upper_bound'), u)})",
                     f"the window is empty (lower {q(w.get('nuisance_lower_bound'), u)} ≥ upper "
                     f"{q(w.get('protection_upper_bound'), u)})"), "bad" if w.get("tight_attainable") else "open")
    ob = res.get("ov_bound") or {}
    if ob and ob.get("V_trigger_max_V") is not None:
        s = ins.section(tr("에너지 bound (과전압)", "energy bound (overvoltage)"))
        s.add(tr(f"트리거 후 유입 에너지 E_after {q(ob.get('E_after_J'), 'J')} → V_tr,max = √(V_lim² − 2E_after/C) = "
                 f"{q(ob.get('V_trigger_max_V'), 'V')}",
                 f"energy after the trigger E_after {q(ob.get('E_after_J'), 'J')} → V_tr,max = √(V_lim² − 2E_after/C) = "
                 f"{q(ob.get('V_trigger_max_V'), 'V')}"), "info", esc(engine_text(ob.get("note", ""))))
    notes_section(ins, [res.get("note")])
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- ASC transient
def asc_insight(res: dict) -> Insight:
    claim = res.get("claim") or {}
    rq = res.get("request") or {}
    where = tr(f"ASC @ {q(rq.get('speed_rpm'), 'rpm')}, 사고 전 {q(rq.get('torque_Nm'), 'N·m')}, Vdc {q(rq.get('Vdc_V'), 'V')}",
               f"ASC @ {q(rq.get('speed_rpm'), 'rpm')}, pre-fault {q(rq.get('torque_Nm'), 'N·m')}, Vdc {q(rq.get('Vdc_V'), 'V')}")
    if not res.get("evaluable"):
        ins = Insight(headline=f"{where}: " + tr("평가 불가", "not evaluable"), verdict=verdict_of(claim))
        s = ins.section(tr("판정", "claim"))
        if claim:
            claim_item(s, claim)
        return ins.nonempty()
    reqs = res.get("requirements") or {}
    parts = []
    for rid, r in reqs.items():
        if r.get("status") in ("REQUIREMENT_INCOMPLETE", "NOT_COVERED"):
            parts.append(tr(f"{esc(rid)} 미정의", f"{esc(rid)} undefined"))
            continue
        ok = r.get("screening_verdict") == "PASS"
        u = r.get("unit", "A")
        parts.append(tr(f"{esc(rid)} {q(r.get('value'), u)} {'≤' if ok else '>'} {q(r.get('limit'), u)}",
                        f"{esc(rid)} {q(r.get('value'), u)} {'≤' if ok else '>'} {q(r.get('limit'), u)}"))
    head = f"{where}: " + "; ".join(parts) + tr(" (스크리닝)", " (screening)")
    ins = Insight(headline=head, verdict=verdict_of(claim))
    st = res.get("steady_asc") or {}
    st_i = math.hypot(st.get("id_A") or 0, st.get("iq_A") or 0) if st else None
    ins.metrics += [(tr("과도 peak (dq)", "transient peak (dq)"), q(res.get("peak_dq_A"), "A"), "info"),
                    (tr("최소 id", "minimum id"), q(res.get("min_id_A"), "A"), "info"),
                    (tr("최대 제동 토크", "peak braking torque"), q(res.get("peak_torque_Nm"), "N·m"), "info")]
    s = ins.section(tr("요구별 (같은 고장 파형, 각자의 연산자·창·원점)", "per requirement (one fault waveform, each its own operator, "
                                                                "window and origin)"))
    op_word = {"abs_peak": tr("절대 peak", "absolute peak"), "rms": "RMS", "envelope_after": tr("이후 포락선", "envelope after"),
               "time_above": tr("초과 시간 (누적)", "time above (cumulative)"),
               "time_above_contiguous": tr("초과 시간 (최장 연속)", "time above (longest contiguous)")}
    for rid, r in reqs.items():
        if r.get("status") in ("REQUIREMENT_INCOMPLETE", "NOT_COVERED"):
            s.add(tr(f"<b>{esc(rid)}</b>: 정의 불완전 — {esc('; '.join(r.get('missing', [])))}",
                     f"<b>{esc(rid)}</b>: incomplete — {esc('; '.join(r.get('missing', [])))}"), "open")
            continue
        u = r.get("unit", "A")
        ok = r.get("screening_verdict") == "PASS"
        win = r.get("window_s") or [None, None]
        m = r.get("margin")
        s.add(tr(f"<b>{esc(rid)}</b> — {op_word.get(r.get('operator'), esc(r.get('operator', '')))} "
                 f"({'상전류' if r.get('quantity') == 'phase' else 'dq 크기'}, {_msq(win[0], 3)}–{_msq(win[1], 3)}): "
                 f"{q(r.get('value'), u)} vs {q(r.get('limit'), u)} → {'만족' if ok else '초과'} "
                 f"({'여유' if (m or 0) >= 0 else '초과량'} {q(abs(m) if m is not None else None, u)}, 한계의 {pct(abs(m or 0), r.get('limit'), 0)})",
                 f"<b>{esc(rid)}</b> — {op_word.get(r.get('operator'), esc(r.get('operator', '')))} "
                 f"({'phase current' if r.get('quantity') == 'phase' else 'dq norm'}, {_msq(win[0], 3)}–{_msq(win[1], 3)}): "
                 f"{q(r.get('value'), u)} vs {q(r.get('limit'), u)} → {'met' if ok else 'exceeded'} "
                 f"({'margin' if (m or 0) >= 0 else 'excess'} {q(abs(m) if m is not None else None, u)}, "
                 f"{pct(abs(m or 0), r.get('limit'), 0)} of the limit)"), "ok" if ok else "bad",
              tr(f"최악 상 {esc(r.get('worst_phase') or '—')}, 초기 전기각 {num(r.get('worst_initial_angle_deg'))}° · 수치 여유폭 "
                 f"{num(r.get('numerical_allowance'), 3)} {u} · 시간 원점: "
                 f"{'단락 성립' if r.get('origin') == 'asc_established' else '고장 발생'}",
                 f"worst phase {esc(r.get('worst_phase') or '—')}, initial electrical angle "
                 f"{num(r.get('worst_initial_angle_deg'))}° · numerical allowance {num(r.get('numerical_allowance'), 3)} {u} · "
                 f"time origin: {'short established' if r.get('origin') == 'asc_established' else 'fault inception'}"))
    s = ins.section(tr("과도의 모양", "the shape of the transient"))
    pf = res.get("pre_fault") or {}
    s.add(tr(f"사고 전 id {q(pf.get('id_A'), 'A')}, iq {q(pf.get('iq_A'), 'A')} → 정상 ASC id {q(st.get('id_A'), 'A')}, "
             f"iq {q(st.get('iq_A'), 'A')} (|i| {q(st_i, 'A')}) 로 전기 주파수에서 감쇠 진동하며 이동",
             f"from the pre-fault id {q(pf.get('id_A'), 'A')}, iq {q(pf.get('iq_A'), 'A')} to the steady ASC id "
             f"{q(st.get('id_A'), 'A')}, iq {q(st.get('iq_A'), 'A')} (|i| {q(st_i, 'A')}), a decaying oscillation at the "
             f"electrical frequency"), "info")
    if st_i:
        s.add(tr(f"과도 peak {q(res.get('peak_dq_A'), 'A')} = 정상 ASC 전류의 {num(res['peak_dq_A'] / st_i, 3)}배 — 정상상태 값으로는 "
                 f"peak 요구를 판단할 수 없습니다",
                 f"transient peak {q(res.get('peak_dq_A'), 'A')} = {num(res['peak_dq_A'] / st_i, 3)}× the steady ASC current — "
                 f"the steady value cannot judge a peak requirement"), "info")
    s.add(tr(f"최소 id {q(res.get('min_id_A'), 'A')} — 감자 한계와 비교할 값", f"minimum id {q(res.get('min_id_A'), 'A')} — the "
             f"value to compare with the demagnetisation limit"), "info")
    s.add(tr(f"최대 제동 토크 {q(res.get('peak_torque_Nm'), 'N·m')} — 구동계가 받는 충격 토크", f"peak braking torque "
             f"{q(res.get('peak_torque_Nm'), 'N·m')} — the shock torque on the driveline"), "info")
    cc = res.get("solver_cross_check_A", res.get("rk_cross_check_A"))
    if cc is not None:
        s.add(tr(f"같은 방정식을 다른 적분기로 풀어 비교한 차이 {q(cc, 'A', 3)} — 수치 오차는 판정에 영향 없음",
                 f"a second solver on the same equations differs by {q(cc, 'A', 3)} — numerics do not move the verdicts"), "ok")
    if res.get("fixed_speed_sensitivity_A") is not None:
        s.add(tr(f"속도 일정 가정 대비 차이 {q(res['fixed_speed_sensitivity_A'], 'A', 3)} (관성 반영)",
                 f"difference to the fixed-speed assumption {q(res['fixed_speed_sensitivity_A'], 'A', 3)} (inertia included)"),
              "info")
    its = dict(res.get("items") or {})
    dom = its.pop("model_domain", None)
    if its:
        s = ins.section(tr("공급사 envelope가 있어야 판정되는 것", "what needs supplier envelopes"))
        names = {"demagnetisation": tr("감자", "demagnetisation"), "device_survival": tr("소자 생존", "device survival")}
        for k, it in its.items():
            extra = ""
            if k == "device_survival" and it.get("phase_I2t_A2s") is not None:
                extra = tr(f" — 상전류 peak {q(it.get('phase_peak_A'), 'A')}, I²t {q(it.get('phase_I2t_A2s'), 'A²s')}",
                           f" — phase peak {q(it.get('phase_peak_A'), 'A')}, I²t {q(it.get('phase_I2t_A2s'), 'A²s')}")
            s.add(f"<b>{names.get(k, esc(k))}</b>: {state_label(it.get('status', ''))}{extra}", status_level(it.get("status")),
                  esc(engine_text(it.get("detail", ""))))
    if dom:
        # the constant-parameter model outside its declared current domain (review 3 F-24)
        out = dom.get("status") == "OUTSIDE_DECLARED_DOMAIN"
        (a, b), (c, d) = dom.get("id_range_A") or (None, None), dom.get("iq_range_A") or (None, None)
        (e, f), (g, h) = dom.get("domain_id_A") or (None, None), dom.get("domain_iq_A") or (None, None)
        traj = f"i_d {num(a)} … {num(b)} A, i_q {num(c)} … {num(d)} A"
        decl = f"i_d {num(e)} … {num(f)} A, i_q {num(g)} … {num(h)} A"
        s = ins.section(tr("모델 적용 범위", "model validity"))
        if out:
            s.add(tr(f"궤적 {traj}이 선언된 전류 영역 {decl} 밖 (최대 {num(dom.get('excursion_factor'), 3)}배)",
                     f"the trajectory {traj} leaves the declared current domain {decl} (up to "
                     f"{num(dom.get('excursion_factor'), 3)}x)"), "warn",
                  tr("상수 파라미터 모델을 외삽한 구간 — 포화는 축에 따라 피크를 낮추거나 높이므로 방향을 주장하지 않음",
                     "the constant-parameter model is extrapolated there — saturation lowers or raises the peak "
                     "depending on the axis, so no direction is claimed"))
        else:
            s.add(tr(f"궤적 {traj}이 선언된 전류 영역 {decl} 안", f"the trajectory {traj} stays inside the declared "
                     f"current domain {decl}"), "ok")
    s = ins.section(tr("판정", "claim"))
    if claim:
        claim_item(s, claim)
    s.add(tr("판정 수준: ", "claim level: ") + esc(engine_text(res.get("claim_level", ""))), "info")
    not_modelled_section(ins, res.get("not_modelled"))
    return ins.nonempty()
