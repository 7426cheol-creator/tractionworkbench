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
