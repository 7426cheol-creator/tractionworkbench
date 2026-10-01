"""Readings of the drive-system views: drive cycle, integrated charging, system budgets."""

from __future__ import annotations

from ..i18n import tr
from . import Insight, num, q
from .generic import notes_section

LOSS_NAMES = {"battery": lambda: tr("배터리·하니스", "battery + harness"), "inverter": lambda: tr("인버터", "inverter"),
              "motor_copper": lambda: tr("모터 동손", "motor copper"),
              "motor_rotational": lambda: tr("모터 회전·철손", "motor rotational / iron"),
              "reducer": lambda: tr("감속기", "reducer"), "axle": lambda: tr("액슬", "axle")}


def _lvl(st):
    return {"PASS": "ok", "FAIL": "bad", "UNKNOWN": "warn"}.get(st, "info")


def cycle_insight(res: dict) -> Insight:
    c = res.get("cycle") or {}
    cons = res.get("consumption_Wh_per_km") or {}
    reg = res.get("regeneration") or {}
    fol = res.get("followed") or {}
    ver = res.get("verdicts") or []
    worst = "FAIL" if any(v["status"] == "FAIL" for v in ver) else (
        "UNKNOWN" if any(v["status"] == "UNKNOWN" for v in ver) else ("PASS" if ver else None))
    co = cons.get("battery_ocv")
    if not res.get("complete"):
        head = tr(f"{c.get('name')}: 일부 구간이 UNKNOWN — 소비·주행거리는 판정하지 않습니다",
                  f"{c.get('name')}: some intervals are UNKNOWN — no consumption or range verdict")
    else:
        head = tr(f"{c.get('name')} {q(c.get('distance_km'), 'km')}: 배터리 소비 {q(co, 'Wh/km')}"
                  + (f", 주행거리 {q(res.get('range_km'), 'km', 3)}" if res.get("range_km") else "")
                  + (f", 회생 회수율 {num(100 * reg['recovery_ratio'], 3)} %" if reg.get("recovery_ratio") else ""),
                  f"{c.get('name')} {q(c.get('distance_km'), 'km')}: battery consumption {q(co, 'Wh/km')}"
                  + (f", range {q(res.get('range_km'), 'km', 3)}" if res.get("range_km") else "")
                  + (f", regeneration recovery {num(100 * reg['recovery_ratio'], 3)} %"
                     if reg.get("recovery_ratio") else ""))
    ins = Insight(headline=head, verdict=worst)
    ins.metrics += [(tr("배터리 (OCV)", "battery (OCV)"), q(co, "Wh/km"), "info"),
                    (tr("인버터 DC 순", "inverter DC net"), q(cons.get("inverter_dc_net"), "Wh/km"), "info"),
                    (tr("추종", "followed"), fol.get("status", "—"), _lvl(fol.get("status"))),
                    (tr("에너지 수지 잔차", "energy closure residual"),
                     q((res.get("closure") or {}).get("residual_kWh", 0) * 1e3, "Wh", 2), "info")]
    if ver:
        s = ins.section(tr("요구 판정", "requirements"))
        for v in ver:
            val = v.get("value")
            s.add(f"<b>{v['requirement']}</b>: {v['status']}"
                  + (f" — {num(val, 4)}" if isinstance(val, (int, float)) else ""), _lvl(v["status"]),
                  "" if v.get("margin") is None else tr(f"여유 {num(v['margin'], 4)}", f"margin {num(v['margin'], 4)}"))
    L = res.get("losses_kWh") or {}
    tot = sum(L.values()) or 0.0
    if tot > 0:
        s = ins.section(tr("손실이 큰 순서", "losses, largest first"),
                        tr("부품별 손실 에너지 (같은 사이클·같은 조건). 줄이면 소비가 그만큼 줄어드는 몫입니다.",
                           "loss energy per component (same trace, same conditions): what reducing it would save."))
        km = c.get("distance_km") or 0
        for k, v in sorted(L.items(), key=lambda kv: -kv[1]):
            if v <= 0:
                continue
            s.add(f"{LOSS_NAMES.get(k, lambda: k)()}: {num(v * 1e3, 4)} Wh ({num(100 * v / tot, 3)} %"
                  + (f", {num(v * 1e3 / km, 3)} Wh/km)" if km else ")"), "info")
    rw = res.get("road_work_kWh") or {}
    s = ins.section(tr("차량이 요구한 일", "work the vehicle demands"))
    s.add(tr(f"구름 {num(rw.get('rolling_constant', 0) * 1e3, 4)} Wh · 선형 {num(rw.get('linear', 0) * 1e3, 3)} Wh · 공기 "
             f"{num(rw.get('aero_quadratic', 0) * 1e3, 4)} Wh · 경사 {num(rw.get('grade', 0) * 1e3, 3)} Wh",
             f"rolling {num(rw.get('rolling_constant', 0) * 1e3, 4)} Wh · linear {num(rw.get('linear', 0) * 1e3, 3)} Wh · "
             f"aero {num(rw.get('aero_quadratic', 0) * 1e3, 4)} Wh · grade {num(rw.get('grade', 0) * 1e3, 3)} Wh"), "info")
    if reg.get("available_at_wheels_kWh"):
        e = res.get("energy_kWh") or {}
        s.add(tr(f"제동 에너지 {num(reg['available_at_wheels_kWh'] * 1e3, 4)} Wh 중 배터리로 "
                 f"{num(reg['recovered_to_battery_kWh'] * 1e3, 4)} Wh, 마찰 제동 {num(e.get('friction_brakes', 0) * 1e3, 3)} Wh",
                 f"of {num(reg['available_at_wheels_kWh'] * 1e3, 4)} Wh braking energy, "
                 f"{num(reg['recovered_to_battery_kWh'] * 1e3, 4)} Wh reach the battery and "
                 f"{num(e.get('friction_brakes', 0) * 1e3, 3)} Wh go to the friction brakes"), "info")
    if fol.get("count"):
        s = ins.section(tr("구동이 전달하지 못한 구간", "intervals the drive does not deliver"))
        for it in fol.get("intervals", [])[:8]:
            s.add(tr(f"t = {num(it['t_s'], 4)} s, {num(it['v_kmh'], 3)} km/h: 요구 {num(it['T_out_demand_Nm'], 4)} N·m, "
                     f"전달 {num(it['T_out_delivered_Nm'], 4)} N·m",
                     f"t = {num(it['t_s'], 4)} s, {num(it['v_kmh'], 3)} km/h: demand {num(it['T_out_demand_Nm'], 4)} N·m, "
                     f"delivered {num(it['T_out_delivered_Nm'], 4)} N·m"), "bad", it.get("reason", ""))
    if (res.get("unknown") or {}).get("count"):
        s = ins.section(tr("UNKNOWN 구간", "UNKNOWN intervals"))
        for r in res["unknown"]["reasons"][:8]:
            s.add(r, "warn")
    notes_section(ins, res.get("notes") or [])
    return ins.nonempty()


CHECK_NAMES = {"charger_current": lambda: tr("충전기 전류", "charger current"),
               "charger_power": lambda: tr("충전기 전력", "charger power"),
               "battery_power": lambda: tr("배터리 충전 전력", "battery charge power"),
               "battery_current": lambda: tr("배터리 충전 전류", "battery charge current"),
               "phase_peak": lambda: tr("상 피크 전류", "phase peak current"),
               "neutral_rms": lambda: tr("중성선 RMS 전류", "neutral RMS current"),
               "junction": lambda: tr("최고 접합 온도", "hottest junction temperature"),
               "winding": lambda: tr("고정자 동손", "stator copper loss")}


def charging_insight(res: dict) -> Insight:
    st = res.get("status")
    if "checks" not in res:
        ins = Insight(headline=tr(f"통합 충전: {st} — {res.get('reason', '')}", f"integrated charging: {st} — "
                                                                                 f"{res.get('reason', '')}"),
                      verdict=None if st == "NOT_APPLICABLE" else "UNKNOWN")
        return ins
    i = res["inputs"]
    c = res["currents"]
    eff = res.get("efficiency")
    head = tr(f"{num(i['V_charger_V'], 4)} V 충전기 → {num(i['V_battery_V'], 4)} V 배터리, {num(i['I_charge_A'], 4)} A: {st}"
              + (f", 효율 {num(100 * eff, 4)} %" if eff else "") + (f", 최고 Tj {num(res['Tj_C'], 4)} °C"
                                                                   if res.get("Tj_C") is not None else ""),
              f"{num(i['V_charger_V'], 4)} V charger → {num(i['V_battery_V'], 4)} V battery, {num(i['I_charge_A'], 4)} A: "
              f"{st}" + (f", efficiency {num(100 * eff, 4)} %" if eff else "")
              + (f", hottest Tj {num(res['Tj_C'], 4)} °C" if res.get("Tj_C") is not None else ""))
    ins = Insight(headline=head, verdict=st if st in ("PASS", "FAIL", "UNKNOWN") else None)
    ins.metrics += [(tr("상측 듀티", "upper-path duty"), num(res["duty_upper"], 3), "info"),
                    (tr("상 리플 (최대)", "phase ripple (max)"), q(max(c["phase_ripple_pp_A"]), "A pk-pk", 3), "info"),
                    (tr("중성선 리플", "neutral ripple"), q(c["neutral_ripple_pp_A"], "A pk-pk", 3), "info"),
                    (tr("토크 리플 |피크|", "torque ripple |peak|"), q((res.get("torque") or {}).get("peak_abs_Nm"),
                                                                     "N·m", 3), "info")]
    s = ins.section(tr("한계 판정", "limits"))
    for ch in res["checks"]:
        name = CHECK_NAMES.get(ch["id"], lambda: ch["id"])()
        if ch.get("reason") == "limit not declared":
            s.add(tr(f"{name}: 한계 미선언 — 확인하지 않음", f"{name}: limit not declared — not checked"), "warn")
            continue
        s.add(f"{name}: {ch['status']} — {num(ch['value'], 4)} / {num(ch['limit'], 4)} {ch['unit']}", _lvl(ch["status"]))
    s = ins.section(tr("리플과 인터리브", "ripple and interleaving"),
                    tr("영상분 전류(세 상 공통)는 영상 인덕턴스 L0(대부분 누설)로만 제한되고, 상 사이 차이는 L_d·L_q로 "
                       "제한됩니다. 120° 인터리브는 영상분 전압을 상쇄합니다(듀티 1/3·2/3에서 완전 상쇄).",
                       "the zero-sequence current (common to the three phases) is limited only by L0 (mostly "
                       "leakage), the difference between phases by L_d, L_q. 120° interleaving cancels the "
                       "zero-sequence voltage (completely at duty 1/3 and 2/3)."))
    s.add(tr(f"영상분 리플 {num(c['zero_sequence_ripple_pp_A'], 3)} A pk-pk (L0 {num(i['L0_H'] * 1e6, 3)} µH), dq 리플 "
             f"{num(c['dq_ripple_pp_A'][0], 3)} / {num(c['dq_ripple_pp_A'][1], 3)} A pk-pk (L_d {num(i['Ld_H'] * 1e6, 3)} / "
             f"L_q {num(i['Lq_H'] * 1e6, 3)} µH)",
             f"zero-sequence ripple {num(c['zero_sequence_ripple_pp_A'], 3)} A pk-pk (L0 {num(i['L0_H'] * 1e6, 3)} µH), "
             f"dq ripple {num(c['dq_ripple_pp_A'][0], 3)} / {num(c['dq_ripple_pp_A'][1], 3)} A pk-pk (L_d "
             f"{num(i['Ld_H'] * 1e6, 3)} / L_q {num(i['Lq_H'] * 1e6, 3)} µH)"), "info")
    L = res.get("losses_W") or {}
    s = ins.section(tr("손실과 열", "losses and heat"))
    s.add(tr(f"소자 {num(L.get('devices'), 4)} W · 권선 {num(L.get('motor_copper'), 4)} W · DC-link 커패시터 "
             f"{num(L.get('dc_link_capacitor'), 3)} W; 가장 뜨거운 다이 {res.get('hottest_die')}",
             f"devices {num(L.get('devices'), 4)} W · winding {num(L.get('motor_copper'), 4)} W · DC-link capacitor "
             f"{num(L.get('dc_link_capacitor'), 3)} W; hottest die {res.get('hottest_die')}"), "info")
    if res.get("problems"):
        for p in res["problems"]:
            s.add(p, "warn")
    notes_section(ins, res.get("notes") or [])
    return ins.nonempty()


def charging_map_insight(cap: dict) -> Insight:
    rows = [r for r in cap.get("rows") or [] if r.get("P_max_W") is not None]
    if not rows:
        return Insight(headline=tr("충전 능력: 확정된 점 없음", "charging capability: no established point"),
                       verdict="UNKNOWN")
    lo = min(rows, key=lambda r: r["P_max_W"])
    hi = max(rows, key=lambda r: r["P_max_W"])
    ins = Insight(headline=tr(f"최대 충전 전력 {num(lo['P_max_W'] / 1e3, 4)}–{num(hi['P_max_W'] / 1e3, 4)} kW (충전기 측)",
                              f"maximum charging power {num(lo['P_max_W'] / 1e3, 4)}–{num(hi['P_max_W'] / 1e3, 4)} kW "
                              f"(charger side)"))
    from collections import Counter
    cnt = Counter((r.get("limiting") or ["—"])[0] for r in rows)
    s = ins.section(tr("어느 한계가 막는가", "which limit binds"))
    for k, n in cnt.most_common():
        s.add(f"{CHECK_NAMES.get(k, lambda: k)()}: {n} / {len(rows)}", "info")
    nc = sorted({x for r in rows for x in (r.get("not_checked") or [])})
    if nc:
        s.add(tr("선언되지 않아 확인하지 않은 한계: ", "limits not declared, not checked: ")
              + ", ".join(CHECK_NAMES.get(k, lambda: k)() for k in nc), "warn")
    unk = [r for r in cap.get("rows") or [] if r.get("P_max_W") is None]
    if unk:
        s = ins.section(tr("확정되지 않은 점", "points not established"))
        for r in unk[:6]:
            s.add(f"{num(r['V_charger_V'], 4)} V / {num(r['V_battery_V'], 4)} V: {r.get('status')} — {r.get('reason', '')}",
                  "warn")
    return ins.nonempty()


ITEM_NAMES = {"current_gain": lambda: tr("전류 센서 이득", "current-sensor gain"),
              "current_offset": lambda: tr("전류 센서 오프셋", "current-sensor offset"),
              "resolver_offset": lambda: tr("레졸버 오프셋", "resolver offset"),
              "magnet_temperature": lambda: tr("자석 온도 추정", "magnet temperature estimate"),
              "model_tolerance": lambda: tr("모델 공차 (ψ, L_d, L_q)", "model tolerance (ψ, L_d, L_q)"),
              "estimator": lambda: tr("토크 추정기", "torque estimator"),
              "monitor_mismatch": lambda: tr("모니터 불일치", "monitor mismatch")}


def _iname(k):
    return ITEM_NAMES.get(k, lambda: k.replace("_", " "))()


def _cname(r: dict) -> str:
    """Display name of a budget contributor: the torque items and the cycle's loss components by name."""
    k = r.get("id")
    if k in ITEM_NAMES:
        return ITEM_NAMES[k]()
    if k in LOSS_NAMES:
        return LOSS_NAMES[k]()
    return r.get("title") or str(k)


COMB_SHORT = {"worst_case": lambda: tr("최악 (선형)", "worst case (linear)"), "rss": lambda: "RSS",
              "mixed": lambda: tr("혼합", "mixed")}


def _win(spec) -> str:
    if not spec:
        return "—"
    return f"max({num(spec.get('abs_Nm') or 0, 4)} N·m, {num(100 * (spec.get('rel') or 0), 3)} % |T|)"


def _break_even(r: dict, unit: str) -> str:
    if r.get("alone_insufficient"):
        return tr("이 기여를 0으로 줄여도 혼자서는 한계 안에 들지 못함", "even at zero this item alone cannot bring the "
                                                          "stack inside the limit")
    be = r.get("break_even_growth")
    return "" if be is None else tr(f"손익분기 증가 {num(be, 3)} {unit}", f"break-even growth {num(be, 3)} {unit}")


def budget_torque_insight(res: dict) -> Insight:
    """The torque-accuracy budget over the envelope and its functional-safety link."""
    pts = res.get("points") or []
    ev = [p for p in pts if "total_Nm" in p]
    st = res.get("status")
    cnt = res.get("counts") or {}
    w = res.get("worst_point")
    if not ev:
        return Insight(headline=tr("토크 정확도: 평가한 운전점이 없습니다", "torque accuracy: no operating point evaluated"),
                       verdict="UNKNOWN")
    dom = max(((k, v) for k, v in (w.get("contributions_Nm") or {}).items()
               if v is not None and k != "monitor_mismatch"), key=lambda kv: kv[1], default=(None, None))
    head = tr(f"토크 정확도 {st}: {len(ev)}점 중 FAIL {cnt.get('FAIL', 0)} — 최악 {num(w['speed_rpm'], 5)} rpm, "
              f"{num(w['torque_Nm'], 4)} N·m에서 {num(w['total_Nm'], 3)} / {num(w['limit_Nm'], 3)} N·m"
              + (f", 가장 큰 기여 {_iname(dom[0])}" if dom[0] else ""),
              f"torque accuracy {st}: FAIL at {cnt.get('FAIL', 0)} of {len(ev)} points — worst "
              f"{num(w['speed_rpm'], 5)} rpm, {num(w['torque_Nm'], 4)} N·m: {num(w['total_Nm'], 3)} / "
              f"{num(w['limit_Nm'], 3)} N·m" + (f", largest contributor {_iname(dom[0])}" if dom[0] else ""))
    ins = Insight(headline=head, verdict=st)
    f = res.get("fusa") or {}
    ins.metrics += [(tr("PASS / FAIL / 미평가", "PASS / FAIL / not evaluated"),
                     f"{cnt.get('PASS', 0)} / {cnt.get('FAIL', 0)} / {res.get('not_evaluated', 0)}", _lvl(st)),
                    (tr("최악 여유", "worst margin"), q(w.get("margin_Nm"), "N·m", 3), _lvl(w.get("status"))),
                    (tr("스택", "stack"), res.get("combination", ""), "info")]
    if f:
        ins.metrics += [(tr("안전 창 (정상 운전)", "safety window (normal operation)"), f.get("window_status", "—"),
                         _lvl(f.get("window_status"))),
                        (tr("모니터 오트립", "monitor false trip"), f.get("false_trip_status", "—"),
                         _lvl(f.get("false_trip_status"))),
                        (tr("미검출 편차", "undetected deviation"), f.get("undetected_status", "—"),
                         _lvl(f.get("undetected_status")))]
    fails = sorted([p for p in ev if p["status"] == "FAIL"], key=lambda p: p["margin_Nm"])
    if fails:
        s = ins.section(tr("요구를 넘는 운전점", "operating points over the requirement"),
                        tr("여유가 작은 순서. 각 점에서 가장 큰 기여를 함께 적습니다.",
                           "smallest margin first, with the largest contributor at each point."))
        for p in fails[:10]:
            c = {k: v for k, v in (p.get("contributions_Nm") or {}).items() if v is not None and k != "monitor_mismatch"}
            k0 = max(c, key=c.get) if c else None
            s.add(tr(f"{num(p['speed_rpm'], 5)} rpm, {num(p['torque_Nm'], 4)} N·m: {num(p['total_Nm'], 3)} / "
                     f"{num(p['limit_Nm'], 3)} N·m (여유 {num(p['margin_Nm'], 3)})",
                     f"{num(p['speed_rpm'], 5)} rpm, {num(p['torque_Nm'], 4)} N·m: {num(p['total_Nm'], 3)} / "
                     f"{num(p['limit_Nm'], 3)} N·m (margin {num(p['margin_Nm'], 3)})"), "bad",
                  "" if k0 is None else tr(f"가장 큰 기여: {_iname(k0)} {num(c[k0], 3)} N·m",
                                           f"largest: {_iname(k0)} {num(c[k0], 3)} N·m"))
    wb = res.get("worst_point_budget")
    if wb:
        s = ins.section(tr("최악점의 배분 — 통과하려면", "allocation at the worst point — to pass"),
                        tr("현재 값에 비례해 한계를 나눈 배분(같은 스택에서 정확히 한계가 됨)과 다른 기여가 그대로일 때 "
                           "이 기여가 더 커질 수 있는 양(손익분기). 음수면 그 기여만으로는 통과할 수 없습니다.",
                           "the limit shared in proportion to the present values (stacking exactly to the limit) and "
                           "how much each item may grow with the others unchanged (break-even); negative: that item "
                           "alone cannot bring the point inside."))
        for r in sorted(wb["contributors"], key=lambda r: -(r.get("share") or 0)):
            if r.get("value") is None:
                s.add(tr(f"{_iname(r['id'])}: 미확정", f"{_iname(r['id'])}: not established"), "warn")
                continue
            s.add(tr(f"{_iname(r['id'])} ({r['kind']}): {num(r['value'], 3)} → 배분 {num(r.get('allocation'), 3)} N·m, "
                     f"몫 {num(100 * (r.get('share') or 0), 3)} %",
                     f"{_iname(r['id'])} ({r['kind']}): {num(r['value'], 3)} → allocation {num(r.get('allocation'), 3)} "
                     f"N·m, share {num(100 * (r.get('share') or 0), 3)} %"), _lvl(r.get("allocation_status")),
                  _break_even(r, "N·m"))
    mx = {k: v for k, v in (res.get("max_contribution_Nm") or {}).items() if k != "monitor_mismatch"}
    if mx:
        s = ins.section(tr("포락선 전체에서 각 오차원의 최대 기여", "largest contribution of each source over the envelope"))
        for k, v in sorted(mx.items(), key=lambda kv: -(kv[1] or 0)):
            if v:
                s.add(f"{_iname(k)}: {num(v, 3)} N·m", "info", (res.get("errors") or {}).get("basis", {}).get(k, ""))
        if res.get("not_declared"):
            s.add(tr("선언되지 않은 오차원 (버짓에 없음): ", "error sources not declared (not in the budget): ")
                  + ", ".join(_iname(k) for k in res["not_declared"]), "warn")
    if f:
        s = ins.section(tr("기능안전 연결", "functional-safety link"), f.get("meaning", ""))
        s.add(tr(f"안전 창 {_win(f.get('torque_window'))}, 모니터 문턱 {_win(f.get('monitor'))}",
                 f"safety window {_win(f.get('torque_window'))}, monitor threshold {_win(f.get('monitor'))}"), "info",
              f.get("source", ""))
        s.add(tr("모니터가 보는 오차: ", "errors the monitor sees: ")
              + (", ".join(_iname(k) for k in f.get("monitor_sees") or []) or tr("없음", "none")),
              "info", f.get("monitor_sees_basis", ""))
        u = f.get("worst_undetected_point")
        if u is not None and u.get("undetected_Nm") is not None:
            s.add(tr(f"미검출 최대 편차 (최악점 {num(u['speed_rpm'], 5)} rpm, {num(u['torque_Nm'], 4)} N·m): 문턱 "
                     f"{num(u['monitor_threshold_Nm'], 3)} + 불일치 {num(u['monitor_mismatch_Nm'], 3)} + 모니터가 못 보는 "
                     f"오차 {num(u['monitor_unseen_Nm'], 3)} = {num(u['undetected_Nm'], 4)} N·m vs 창 "
                     f"{num(u['fusa_window_Nm'], 4)} N·m",
                     f"largest undetected deviation (worst point {num(u['speed_rpm'], 5)} rpm, "
                     f"{num(u['torque_Nm'], 4)} N·m): threshold {num(u['monitor_threshold_Nm'], 3)} + mismatch "
                     f"{num(u['monitor_mismatch_Nm'], 3)} + errors the monitor cannot see "
                     f"{num(u['monitor_unseen_Nm'], 3)} = {num(u['undetected_Nm'], 4)} N·m vs window "
                     f"{num(u['fusa_window_Nm'], 4)} N·m"), _lvl(u.get("undetected_status")))
            if u.get("undetected_status") == "FAIL":
                s.add(tr("창을 지키는 방법: 모니터 문턱을 낮추거나(오트립 여유와 맞바꿈), 모니터 불일치를 줄이거나, "
                         "모니터에 별도 센서를 주어 제어 경로 오차를 보게 하거나, 못 보는 오차 자체를 줄입니다.",
                         "ways to keep the window: a lower monitor threshold (traded against false-trip margin), a "
                         "smaller monitor mismatch, own sensors for the monitor so that it sees the control path's "
                         "errors, or smaller unseen errors."), "info")
    notes_section(ins, res.get("notes") or [])
    return ins.nonempty()


def budget_insight(b: dict) -> Insight:
    """One budget (FTTI, cycle losses, custom): stacks, contributors by share, allocations, what is missing."""
    unit = str(b.get("unit", "")).replace("*", "·")
    st = b.get("status")
    head = tr(f"{b.get('title', '')}: {st} — {num(b.get('total'), 4)} {unit}"
              + (f" / 한계 {num(b['limit'], 4)} {unit}" if b.get("limit") is not None else " (한계 미선언)"),
              f"{b.get('title', '')}: {st} — {num(b.get('total'), 4)} {unit}"
              + (f" of limit {num(b['limit'], 4)} {unit}" if b.get("limit") is not None else " (no limit declared)"))
    ins = Insight(headline=head, verdict=st)
    stacks = b.get("stacks") or {}
    for k in ("worst_case", "rss", "mixed"):
        if k in stacks:
            ins.metrics.append((COMB_SHORT[k]() + (tr(" (판정)", " (decides)") if k == b.get("combination") else ""),
                                q(stacks[k], unit, 4), _lvl(st) if k == b.get("combination") else "info"))
    if b.get("margin") is not None:
        ins.metrics.append((tr("여유", "margin"), q(b["margin"], unit, 3), _lvl(st)))
    s = ins.section(tr("기여 (몫이 큰 순서)", "contributors (largest share first)"), b.get("meaning", ""))
    for r in sorted(b.get("contributors") or [], key=lambda r: -(r.get("share") or 0)):
        if r.get("value") is None:
            s.add(tr(f"{_cname(r)}: 미확정 — 버짓을 판정하지 않습니다", f"{_cname(r)}: not established — no verdict"),
                  "warn")
            continue
        alloc = r.get("allocation")
        s.add(f"{_cname(r)}: {num(r['value'], 4)} {unit}"
              + tr(f" (몫 {num(100 * (r.get('share') or 0), 3)} %)", f" (share {num(100 * (r.get('share') or 0), 3)} %)")
              + ("" if alloc is None else tr(f", 배분 {num(alloc, 4)} {unit}", f", allocation {num(alloc, 4)} {unit}")),
              _lvl(r.get("allocation_status")) if alloc is not None else "info",
              " · ".join(x for x in (r.get("basis", ""), r.get("owner", ""), _break_even(r, unit)) if x))
    if b.get("timing_worst_ms") is not None:
        s = ins.section(tr("타이밍 해석과 같은 합", "same sum as the timing analysis"))
        s.add(tr(f"타이밍 해석의 최악 경로 {num(b['timing_worst_ms'], 4)} ms = 이 버짓의 선형 합",
                 f"the timing analysis' worst path {num(b['timing_worst_ms'], 4)} ms = this budget's linear sum"), "info")
    return ins.nonempty()


def sim_insight(maps: dict) -> Insight:
    """What the simulator export holds: cells by status, the envelope, what is not in the losses, what each file is."""
    c = maps.get("counts") or {}
    V = maps.get("Vdc_V") or []
    tmax = [v for row in maps.get("T_max_Nm") or [] for v in row if v is not None]
    tmin = [v for row in maps.get("T_min_Nm") or [] for v in row if v is not None]
    n_cells = sum(c.values())
    feas = c.get("FEASIBLE", 0)
    head = tr(f"지도 {n_cells}칸: 값 {feas}칸 · 최대 토크 밖 {c.get('BEYOND', 0)} · UNKNOWN {c.get('UNKNOWN', 0)} · "
              f"Vdc {', '.join(f'{x:g}' for x in V)} V",
              f"{n_cells} cells: {feas} with a value · {c.get('BEYOND', 0)} beyond the full load · UNKNOWN "
              f"{c.get('UNKNOWN', 0)} · Vdc {', '.join(f'{x:g}' for x in V)} V")
    ins = Insight(headline=head, verdict="UNKNOWN" if c.get("UNKNOWN") and not feas else None)
    ins.metrics += [(tr("최대 구동 토크", "peak motoring torque"), q(max(tmax) if tmax else None, "N·m", 4), "info"),
                    (tr("최대 회생 토크", "peak generating torque"), q(min(tmin) if tmin else None, "N·m", 4), "info"),
                    (tr("속도 범위", "speed range"), f"{maps['speeds_rpm'][0]:g}–{maps['speeds_rpm'][-1]:g} rpm", "info")]
    m = maps.get("meta") or {}
    if c.get("UNKNOWN"):
        s = ins.section(tr("UNKNOWN 칸", "UNKNOWN cells"))
        s.add(tr("이 칸은 모델이 판정하지 못했습니다(예: 스위칭 시험 전압 밖인데 전압 스케일 법칙이 선언되지 않음). 값으로 채우지 "
                 "않았습니다 — 다른 전압을 내보내려면 모듈의 스케일 법칙을 선언하세요.",
                 "the model could not decide these cells (e.g. away from the switching test voltage without a declared "
                 "voltage-scaling law); they are not filled - to export other voltages, declare the module's "
                 "scaling law."), "warn")
    holes = m.get("empty_in_envelope") or {}
    if holes:
        s = ins.section(tr("최대 토크 안인데 값이 없는 칸", "cells inside the full load without a value"))
        cnt = ", ".join(f"{k} {n}" for k, n in holes.items())
        why = m.get("empty_in_envelope_why") or ""
        s.add(tr(f"{cnt}칸 — {why}. 모든 형식에서 빈 칸(NaN)이고 FMU도 채우지 않습니다(그 근처의 출력은 NaN). 총손실은 한 "
                 "부품이라도 손실이 없으면 비웁니다: 그 부품을 뺀 합은 더 작은 손실로 읽히기 때문입니다. 감속기 데이터의 범위를 "
                 "넓히거나 감속기 없이 내보내세요.",
                 f"{cnt} cell(s) - {why}. Empty (NaN) in every format and not filled in the FMU either (the outputs "
                 "next to them are NaN). The total loss is left empty when one part has no loss: the sum without it "
                 "would read as a lower loss. Extend the reducer data or export without the reducer."), "warn")
    s = ins.section(tr("손실에 들어 있지 않은 것 (모든 칸 또는 일부 칸)", "what the losses do not contain (in every or in some "
                                                                       "cells)"))
    for it in m.get("not_evaluated") or []:
        s.add(it, "warn")
    s.add(tr(f"스위칭 에너지의 전압 의존: {(m.get('source') or {}).get('switching_energy_vs_Vdc', '—')}",
             f"switching energy vs voltage: {(m.get('source') or {}).get('switching_energy_vs_Vdc', '—')}"), "info")
    s = ins.section(tr("내보내는 파일", "the files"))
    s.add(tr("CSV (긴 표): 칸마다 한 행 — 모든 양과 상태. CSV 격자: 양·전압마다 표 하나(속도 가로, 토크 세로) + 최대 토크 곡선.",
             "CSV (long): one row per cell - every quantity and the status. CSV grids: one table per quantity and "
             "voltage (speeds across, torques down) + the full-load curves."), "info")
    s.add(tr("MATLAB .mat: twb_maps 구조체 — 중단점과 [토크 × 속도 × Vdc] 표(NaN = 값 없음), 최대 토크 곡선, 메타데이터(JSON). "
             "Simulink 2-D/n-D Lookup Table에 바로 연결.",
             "MATLAB .mat: struct twb_maps - breakpoints and [torque x speed x Vdc] tables (NaN = no value), the "
             "full-load curves and the metadata (JSON); for Simulink 2-D / n-D lookup tables."), "info")
    s.add(tr("FMU (FMI 2.0, ME + CS): 입력 속도·토크 요청·Vdc → 요청을 최대 토크 곡선으로 제한한 뒤 표를 보간. 경계 옆 보간에 "
             "필요한 바깥 칸만 가장 가까운 값으로 채웠고(설명에 명시), 역회전은 거울상. C 컴파일러가 있으면 바이너리 포함, 없으면 "
             "소스 FMU.",
             "FMU (FMI 2.0, ME + CS): inputs speed, torque request, Vdc - the request is clamped to the full-load curve, "
             "then the tables are interpolated. Only the outside cells an interpolation next to the boundary needs are "
             "filled with the nearest value (stated in its description); reverse rotation is the mirror. With a C "
             "compiler the binary is included, otherwise a source FMU."), "info")
    return ins.nonempty()
