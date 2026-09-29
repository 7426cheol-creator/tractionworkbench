"""Engineering readings of the power-stage page: datasheet module losses, DC-link ripple and the capacitor,
thermal cycling of the dies."""

from __future__ import annotations

from ..i18n import tr
from ..plots.labels import state_label
from . import Insight, esc, kw, num, pct, q, status_level
from .generic import claim_item, claims_section, not_modelled_section, notes_section, verdict_of
from .texts import engine_text

_DIE = {"upper_igbt": ("상단 IGBT", "upper IGBT"), "lower_igbt": ("하단 IGBT", "lower IGBT"),
        "upper_diode": ("상단 다이오드", "upper diode"), "lower_diode": ("하단 다이오드", "lower diode"),
        "upper_mosfet": ("상단 MOSFET", "upper MOSFET"), "lower_mosfet": ("하단 MOSFET", "lower MOSFET"),
        "upper_switch": ("상단 스위치", "upper switch"), "lower_switch": ("하단 스위치", "lower switch"),
        "upper_diode_or_reverse": ("상단 다이오드/역도통", "upper diode / reverse"),
        "lower_diode_or_reverse": ("하단 다이오드/역도통", "lower diode / reverse")}


def die_label(k: str) -> str:
    ko_en = _DIE.get(k)
    return tr(*ko_en) if ko_en else k


def module_insight(res: dict) -> Insight:
    m, op, L = res.get("module") or {}, res.get("operating_point") or {}, res.get("losses") or {}
    rq = res.get("request") or {}
    where = tr(f"{num(rq.get('torque_Nm'))} N·m @ {num(rq.get('speed_rpm'))} rpm, {num(rq.get('Vdc_V'))} V",
               f"{num(rq.get('torque_Nm'))} N·m @ {num(rq.get('speed_rpm'))} rpm, {num(rq.get('Vdc_V'))} V")
    semi = L.get("semiconductor_W")
    ins = Insight(headline=tr(f"{where}: 반도체 손실 {kw(semi)} (도통 {pct(L.get('conduction_W'), semi, 0)} · 스위칭 "
                              f"{pct(L.get('switching_W'), semi, 0)}), 가장 뜨거운 다이 {die_label(L.get('hottest_position', ''))} "
                              f"{q(L.get('hottest_position_W'), 'W')}",
                              f"{where}: semiconductor loss {kw(semi)} (conduction {pct(L.get('conduction_W'), semi, 0)} · "
                              f"switching {pct(L.get('switching_W'), semi, 0)}), hottest die "
                              f"{die_label(L.get('hottest_position', ''))} {q(L.get('hottest_position_W'), 'W')}"),
                  verdict=verdict_of(res.get("claim")))
    ins.metrics += [(tr("반도체 손실", "semiconductor loss"), kw(semi), "info"),
                    (tr("가장 뜨거운 다이", "hottest die"), f"{die_label(L.get('hottest_position', ''))} {q(L.get('hottest_position_W'), 'W')}",
                     "info")]
    et = res.get("electrothermal") or {}
    if et.get("Tj_C") is not None:
        ins.metrics.append((tr("T_j (전열 수렴)", "T_j (electrothermal)"), f"{num(et['Tj_C'])} °C",
                            "ok" if et.get("converged") else "open"))
    s = ins.section(tr("손실이 어디서 나나", "where the loss comes from"))
    dies = L.get("per_die_W") or {}
    leg = sum(v for v in dies.values() if v)
    s.add(tr(f"인버터 전체: 도통 {kw(L.get('conduction_W'))} + 스위칭 {kw(L.get('switching_W'))} = {kw(semi)}"
             + (f" — 한 레그(다이 합) {q(leg, 'W')}" if leg else ""),
             f"the inverter: conduction {kw(L.get('conduction_W'))} + switching {kw(L.get('switching_W'))} = {kw(semi)}"
             + (f" — one leg (sum of its dies) {q(leg, 'W')}" if leg else "")), "info",
          tr(f"변조지수 {num(L.get('modulation_index'), 3)}, 역률 {num(L.get('power_factor'), 3)}, fsw {q((m.get('fsw_Hz') or 0) / 1e3, 'kHz')}, "
             f"T_j 평가 {num(m.get('Tj_eval_C'))} °C", f"modulation index {num(L.get('modulation_index'), 3)}, power factor "
             f"{num(L.get('power_factor'), 3)}, fsw {q((m.get('fsw_Hz') or 0) / 1e3, 'kHz')}, T_j evaluated at "
             f"{num(m.get('Tj_eval_C'))} °C"))
    if dies:
        s.add(tr("다이별: ", "per die: ") + ", ".join(f"{die_label(k)} {q(v, 'W')}" for k, v in sorted(dies.items(), key=lambda kv: -kv[1])),
              "info", esc(engine_text(L.get("die_basis", ""))))
    sw = L.get("switching_W") or 0
    if semi and sw / semi > 0.5:
        s.add(tr(f"스위칭 손실이 절반 이상({pct(sw, semi, 0)}) — fsw를 낮추거나 스위칭 에너지가 작은 소자에서 효과가 큽니다",
                 f"switching is more than half ({pct(sw, semi, 0)}) — a lower fsw or faster devices pay off most"), "info")
    elif semi:
        s.add(tr(f"도통 손실이 절반 이상({pct(L.get('conduction_W'), semi, 0)}) — 전류 경감이나 V_ce(sat)·R_ds(on)이 낮은 소자에서 효과가 큽니다",
                 f"conduction is more than half ({pct(L.get('conduction_W'), semi, 0)}) — less current or lower-drop "
                 f"devices pay off most"), "info")
    if op.get("Pinv_surrogate_W"):
        d = op["Pinv_module_W"] - op["Pinv_surrogate_W"]
        s = ins.section(tr("판정에 쓰는 대체 손실 모델과 비교", "against the loss surrogate the decisions use"))
        s.add(tr(f"데이터시트 모듈 {kw(op['Pinv_module_W'])} vs 대체 모델 {kw(op['Pinv_surrogate_W'])} — 차 {kw(d)} "
                 f"({pct(d, op['Pinv_surrogate_W'], 0)}); DC 전력 {kw(op.get('Pdc_module_W'))} vs {kw(op.get('Pdc_surrogate_W'))}",
                 f"datasheet module {kw(op['Pinv_module_W'])} vs surrogate {kw(op['Pinv_surrogate_W'])} — difference "
                 f"{kw(d)} ({pct(d, op['Pinv_surrogate_W'], 0)}); DC power {kw(op.get('Pdc_module_W'))} vs "
                 f"{kw(op.get('Pdc_surrogate_W'))}"), "warn" if abs(d) > 0 else "info")
        a, b = res.get("policy_with_module") or {}, res.get("policy_with_surrogate") or {}
        diff = [k for k in a if a.get(k) != b.get(k)]
        s.add(tr("판정 항목 상태가 두 손실 모델에서 " + ("같음" if not diff else f"다름: {', '.join(diff)}"),
                 "claim states " + ("agree under both loss models" if not diff else f"differ: {', '.join(diff)}")),
              "ok" if not diff else "warn")
    if et:
        s = ins.section(tr("전열 결합 (손실 ↔ 접합 온도)", "electrothermal coupling (loss ↔ junction temperature)"))
        s.add(tr(f"{'수렴' if et.get('converged') else '미수렴'} — T_j {num(et.get('Tj_C'))} °C, 가장 뜨거운 다이 {q(et.get('P_hot_W'), 'W')}, "
                 f"{et.get('iterations')}회 반복",
                 f"{'converged' if et.get('converged') else 'not converged'} — T_j {num(et.get('Tj_C'))} °C, hottest die "
                 f"{q(et.get('P_hot_W'), 'W')}, {et.get('iterations')} iterations"), "ok" if et.get("converged") else "open")
    s = ins.section(tr("판정", "claim"))
    if res.get("claim"):
        claim_item(s, res["claim"])
    not_modelled_section(ins, L.get("not_modelled"))
    notes_section(ins, L.get("problems"))
    return ins.nonempty()


def ripple_insight(res: dict) -> Insight:
    o = res.get("operating") or {}
    cl = res.get("claims") or {}
    hs = res.get("hotspot") or {}
    rq = cl.get("ripple_requirement") or {}
    head = tr(f"DC-link: 커패시터 리플 전류 {q(res.get('I_cap_rms_A'), 'A')} rms, 전압 리플 {q(res.get('V_ripple_pp_V'), 'V')} pp, "
              f"ESR 손실 {q(res.get('P_cap_W'), 'W')} → 핫스팟 {num(hs.get('T_hot_C'))} °C",
              f"DC-link: capacitor ripple {q(res.get('I_cap_rms_A'), 'A')} rms, voltage ripple {q(res.get('V_ripple_pp_V'), 'V')} "
              f"pp, ESR loss {q(res.get('P_cap_W'), 'W')} → hotspot {num(hs.get('T_hot_C'))} °C")
    worst = "FAIL" if any(c.get("status") == "INFEASIBLE" for c in cl.values()) else (
        "UNKNOWN" if any(c.get("status") == "UNKNOWN" for c in cl.values()) else "PASS")
    ins = Insight(headline=head, verdict=worst)
    ins.metrics += [(tr("커패시터 전류", "capacitor current"), q(res.get("I_cap_rms_A"), "A rms"), "info"),
                    (tr("전압 리플", "voltage ripple"), q(res.get("V_ripple_pp_V"), "V pp"), status_level(rq.get("status"))),
                    (tr("핫스팟", "hotspot"), f"{num(hs.get('T_hot_C'))} °C", "info")]
    s = ins.section(tr("리플 전류는 어디서 오나", "where the ripple current comes from"))
    avg = res.get("average_model") or {}
    s.add(tr(f"인버터 입력 전류: 평균 {q(avg.get('I_dc_A'), 'A')} + 교류 성분 {q(res.get('I_inv_ac_rms_A'), 'A')} rms — 교류 성분의 대부분이 "
             f"커패시터로 ({q(res.get('I_cap_rms_A'), 'A')}), 배터리 쪽으로는 {q(res.get('I_source_ac_rms_A'), 'A')}",
             f"inverter input current: average {q(avg.get('I_dc_A'), 'A')} + ac {q(res.get('I_inv_ac_rms_A'), 'A')} rms — most "
             f"of the ac flows in the capacitor ({q(res.get('I_cap_rms_A'), 'A')}), {q(res.get('I_source_ac_rms_A'), 'A')} to the "
             f"battery"), "info",
          tr(f"운전점: 상전류 {q(o.get('I_pk_A'), 'A')} peak, m = {num(o.get('modulation_index'), 3)}, φ = {num(o.get('phi_deg'), 3)}°, "
             f"f_e {q(o.get('f_e_Hz'), 'Hz')}, fsw {q((o.get('fsw_used_Hz') or 0) / 1e3, 'kHz')} ({esc(o.get('modulation', ''))})",
             f"point: phase {q(o.get('I_pk_A'), 'A')} peak, m = {num(o.get('modulation_index'), 3)}, φ = "
             f"{num(o.get('phi_deg'), 3)}°, f_e {q(o.get('f_e_Hz'), 'Hz')}, fsw {q((o.get('fsw_used_Hz') or 0) / 1e3, 'kHz')}"))
    if hs:
        s.add(tr(f"ESR 손실 {q(res.get('P_cap_W'), 'W')} × R_th {num(hs.get('Rth_eff_K_per_W'))} K/W → 경계 {num(hs.get('boundary_T_C'))} °C 대비 "
                 f"+{num((hs.get('T_hot_C') or 0) - (hs.get('boundary_T_C') or 0), 3)} K (ESR(T)와 함께 수렴: "
                 f"{'예' if hs.get('converged') else '아니오'})",
                 f"ESR loss {q(res.get('P_cap_W'), 'W')} × R_th {num(hs.get('Rth_eff_K_per_W'))} K/W → "
                 f"+{num((hs.get('T_hot_C') or 0) - (hs.get('boundary_T_C') or 0), 3)} K over "
                 f"{num(hs.get('boundary_T_C'))} °C (settled with ESR(T): {'yes' if hs.get('converged') else 'no'})"), "info")
    cov = res.get("esr_coverage") or {}
    if cov and not cov.get("complete"):
        s.add(tr(f"ESR 표가 덮지 못한 고조파 {cov.get('uncovered_harmonics')}개 — 그 몫의 손실은 미상",
                 f"{cov.get('uncovered_harmonics')} harmonics outside the ESR table — their loss is unknown"), "open")
    claims_section(ins, cl)
    not_modelled_section(ins, res.get("not_modelled"))
    return ins.nonempty()


def life_insight(res: dict) -> Insight:
    dmg = res.get("damage") or {}
    claim = dmg.get("claim") or {}
    head = tr(f"열 사이클: 최대 ΔT_j {num(res.get('max_range_K'), 3)} K, T_j 최고 {num(res.get('T_max_C'))} °C, 사이클 "
              f"{num(dmg.get('cycles_counted'))}개 (rainflow)",
              f"thermal cycling: largest ΔT_j {num(res.get('max_range_K'), 3)} K, peak T_j {num(res.get('T_max_C'))} °C, "
              f"{num(dmg.get('cycles_counted'))} cycles (rainflow)")
    ins = Insight(headline=head, verdict=verdict_of(claim))
    ins.metrics += [(tr("최대 ΔT_j", "largest ΔT_j"), f"{num(res.get('max_range_K'), 3)} K", "info"),
                    (tr("T_j 범위", "T_j range"), f"{num(res.get('T_min_C'))}–{num(res.get('T_max_C'))} °C", "info"),
                    (tr("손상 D", "damage D"), tr("미상 (공급사 모델 없음)", "unknown (no supplier model)")
                     if claim.get("status") == "UNKNOWN" else state_label(claim.get("status", "")), status_level(claim.get("status")))]
    devs = res.get("devices") or {}
    if devs:
        s = ins.section(tr("다이별 사이클", "cycles per die"),
                        tr("같은 부하 이력에서도 다이마다 온도 진폭이 다릅니다 — 수명을 정하는 것은 진폭이 큰 다이입니다.",
                           "the same load history swings each die differently — the largest swing governs life."))
        for k, d in sorted(devs.items(), key=lambda kv: -(kv[1].get("max_range_K") or 0)):
            s.add(tr(f"<b>{die_label(k)}</b>: 최대 ΔT {num(d.get('max_range_K'), 3)} K, 최고 {num(d.get('T_max_C'))} °C, "
                     f"사이클 {num(d.get('cycles_counted'))}개, 손상 {('미상' if d.get('D') is None else num(d['D'], 3))}",
                     f"<b>{die_label(k)}</b>: largest ΔT {num(d.get('max_range_K'), 3)} K, peak {num(d.get('T_max_C'))} °C, "
                     f"{num(d.get('cycles_counted'))} cycles, damage {('unknown' if d.get('D') is None else num(d['D'], 3))}"),
                  "open" if d.get("D") is None else "info")
    hist = res.get("histogram") or []
    if hist:
        s = ins.section(tr("ΔT 분포 (rainflow)", "ΔT distribution (rainflow)"))
        for h in hist:
            r = h.get("range_K") or [None, None]
            s.add(tr(f"ΔT {num(r[0])}–{num(r[1])} K: {num(h.get('cycles'))}회 (평균 온도 {num(h.get('mean_C_avg'))} °C)",
                     f"ΔT {num(r[0])}–{num(r[1])} K: {num(h.get('cycles'))} (mean temperature {num(h.get('mean_C_avg'))} °C)"),
                  "info")
    s = ins.section(tr("판정", "claim"))
    if claim:
        claim_item(s, claim)
    notes_section(ins, res.get("notes"), [res.get("source", "")])
    return ins.nonempty()

