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
