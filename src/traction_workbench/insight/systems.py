"""Engineering readings of the system pages: open-end winding (OEW), hybrid (HEV) machines on one bus, conducted
EMI, and the machine-design studies (scaling trade study, winding, concept sizing).

Every number is the result's; ratios and differences are identities of reported numbers."""

from __future__ import annotations

import numpy as np

from ..i18n import tr
from . import Insight, esc, kw, num, pct, q, status_level
from .generic import claim_item, claims_section, not_modelled_section, notes_section, verdict_of
from .texts import engine_parts, engine_text


_BIND = {"upper_offset": ("상단 오프셋 (A 최대 − B 최소 + u0)", "upper offset (A max − B min + u0)"),
         "lower_offset": ("하단 오프셋 (B 최대 − A 최소 − u0)", "lower offset (B max − A min − u0)"),
         "spread_A": ("브리지 A 전압 폭", "bridge A voltage spread"), "spread_B": ("브리지 B 전압 폭", "bridge B voltage spread")}
_IDENT = {"ports_minus_winding_W": ("포트 합 − 권선 전력", "ports − winding power"),
          "winding_abc_minus_dq0_W": ("권선 abc − dq0 전력", "winding abc − dq0 power")}


def _res(v, unit: str) -> str:
    """A numerical residual: exactly what it is, or '0' below 1e-9 of the unit."""
    return "0" + (f" {unit}" if unit else "") if v is None or abs(v) < 1e-9 else q(v, unit, 2)


def _st(status) -> str:
    return {"FEASIBLE": tr("가능", "feasible"), "INFEASIBLE": tr("불가능", "infeasible"), "UNKNOWN": tr("미확정", "unknown")}.get(
        status, esc(status or "—"))


# ---------------------------------------------------------------------------------------------------- OEW
_OEW_KIND = {"common_bus": ("공통 bus", "common bus"), "isolated": ("분리 전원", "isolated sources")}


def oew_insight(res: dict) -> Insight:
    r = res.get("result") or {}
    w = r.get("witness") or {}
    rq = res.get("request") or {}
    topo = res.get("topology") or {}
    kind = tr(*_OEW_KIND.get(topo.get("kind"), (topo.get("kind", ""), topo.get("kind", ""))))
    where = tr(f"OEW ({kind}, {q(topo.get('VA_V'), 'V')} + {q(topo.get('VB_V'), 'V')}) @ {q(rq.get('speed_rpm'), 'rpm')} · "
               f"{q(rq.get('torque_Nm'), 'N·m')}", f"OEW ({kind}, {q(topo.get('VA_V'), 'V')} + {q(topo.get('VB_V'), 'V')}) @ "
               f"{q(rq.get('speed_rpm'), 'rpm')} · {q(rq.get('torque_Nm'), 'N·m')}")
    status = r.get("status")
    v = {"FEASIBLE": "PASS", "INFEASIBLE": "FAIL"}.get(status, "UNKNOWN")
    if not w:
        ins = Insight(headline=f"{where}: {_st(status)} — {esc(engine_text(r.get('reason', '')))}", verdict=v)
    else:
        op, va, cur = w.get("operating_point") or {}, w.get("voltage_allocation") or {}, w.get("currents") or {}
        head = tr(f"{where}: {_st(status)} — 전압 활용률 {pct(va.get('utilisation'), 1, 1)}, 상전류 {q(cur.get('phase_peak_A'), 'A')} peak "
                  f"(브리지 한도 {q(cur.get('bridge_limit_A'), 'A')})",
                  f"{where}: {_st(status)} — voltage utilisation {pct(va.get('utilisation'), 1, 1)}, phase current "
                  f"{q(cur.get('phase_peak_A'), 'A')} peak (bridge limit {q(cur.get('bridge_limit_A'), 'A')})")
        ins = Insight(headline=head, verdict=v)
        ins.metrics += [(tr("전압 활용률", "voltage utilisation"), pct(va.get("utilisation"), 1, 1), status_level(va.get("status"))),
                        (tr("상전류 peak / 한도", "phase peak / limit"), f"{num(cur.get('phase_peak_A'))} / {num(cur.get('bridge_limit_A'))} A",
                         "ok" if (cur.get("phase_peak_A") or 0) <= (cur.get("bridge_limit_A") or np.inf) else "bad"),
                        (tr("순환 전력", "circulating power"), q(w.get("circulating_power_W"), "W"), "info")]
        s = ins.section(tr("운전점과 전압 배분", "operating point and voltage allocation"))
        s.add(tr(f"id {q(op.get('id_A'), 'A')}, iq {q(op.get('iq_A'), 'A')} (|i| {q(op.get('i_dq_A'), 'A')}) → 권선 전압 {q(op.get('U_phase_pk_V'), 'V')} peak",
                 f"id {q(op.get('id_A'), 'A')}, iq {q(op.get('iq_A'), 'A')} (|i| {q(op.get('i_dq_A'), 'A')}) → winding voltage "
                 f"{q(op.get('U_phase_pk_V'), 'V')} peak"), "info",
              tr(f"역기전력 상 peak {q(res.get('emf_phase_peak_V'), 'V')}", f"back-EMF phase peak {q(res.get('emf_phase_peak_V'), 'V')}"))
        if va:
            bind = tr(*_BIND[va["binding"]]) if va.get("binding") in _BIND else esc(va.get("binding") or "—")
            s.add(tr(f"두 브리지가 모든 각도에서 이 전압을 만듦: 활용률 {num(va.get('utilisation'), 4)} (구속: {bind}, "
                     f"θ = {num(va.get('worst_theta_deg'), 4)}°), 최악 초과 {q(va.get('worst_excess_V'), 'V', 3)} + Lipschitz 여유 "
                     f"{q(va.get('lipschitz_margin_V'), 'V', 3)}",
                     f"the two bridges realise this voltage at every angle: utilisation {num(va.get('utilisation'), 4)} (binding: "
                     f"{bind}, θ = {num(va.get('worst_theta_deg'), 4)}°), worst excess "
                     f"{q(va.get('worst_excess_V'), 'V', 3)} + Lipschitz margin {q(va.get('lipschitz_margin_V'), 'V', 3)}"),
                  status_level(va.get("status")), esc(engine_text(va.get("policy", ""))))
        zs = w.get("zero_sequence") or {}
        if zs:
            s.add(tr(f"영상분: 역기전력 e0 {q(zs.get('e0_peak_V'), 'V')} peak, i0 {q(zs.get('i0_rms_A'), 'A')} rms ({_st(zs.get('status'))})",
                     f"zero sequence: back-EMF e0 {q(zs.get('e0_peak_V'), 'V')} peak, i0 {q(zs.get('i0_rms_A'), 'A')} rms "
                     f"({_st(zs.get('status'))})"), status_level(zs.get("status")), esc(engine_text(zs.get("policy", ""))))
        s.add(tr(f"토크 T_em {q(op.get('Tem_Nm'), 'N·m')} → 축 {q(op.get('Tshaft_Nm'), 'N·m')}; 영상분 토크 평균 {q(op.get('T0_mean_Nm'), 'N·m')} "
                 f"(리플 pp {q(op.get('T0_ripple_pp_Nm'), 'N·m')})", f"torque T_em {q(op.get('Tem_Nm'), 'N·m')} → shaft "
                 f"{q(op.get('Tshaft_Nm'), 'N·m')}; zero-sequence torque mean {q(op.get('T0_mean_Nm'), 'N·m')} (ripple pp "
                 f"{q(op.get('T0_ripple_pp_Nm'), 'N·m')})"), "info")
        br = w.get("bridges") or {}
        if br:
            s = ins.section(tr("브리지별 전력과 손실", "power and loss per bridge"))
            for tag in ("A", "B"):
                b = br.get(tag) or {}
                lo = b.get("loss") or {}
                s.add(tr(f"<b>브리지 {tag}</b>: P_ac {kw(b.get('P_ac_W'))}, 손실 {q(lo.get('dc_side_W') or lo.get('screening_W'), 'W')} "
                         f"(도통 {q(lo.get('conduction_W'), 'W')} · 스위칭 {q(lo.get('switching_W'), 'W')}) → P_dc {kw(b.get('P_dc_W'))}, "
                         f"듀티 {num(b.get('duty_min'), 3)}–{num(b.get('duty_max'), 3)}",
                         f"<b>bridge {tag}</b>: P_ac {kw(b.get('P_ac_W'))}, loss {q(lo.get('dc_side_W') or lo.get('screening_W'), 'W')} "
                         f"(conduction {q(lo.get('conduction_W'), 'W')} · switching {q(lo.get('switching_W'), 'W')}) → P_dc "
                         f"{kw(b.get('P_dc_W'))}, duty {num(b.get('duty_min'), 3)}–{num(b.get('duty_max'), 3)}"),
                      "ok" if lo.get("established") else "open", esc(engine_text(lo.get("model", ""))))
            src = (w.get("ports") or {}).get("shared_source") or {}
            if src:
                s.add(tr(f"공통 전원: {kw(src.get('P_dc_W'))} ({q(src.get('I_dc_A'), 'A')} @ {q(src.get('V_V'), 'V')})",
                         f"shared source: {kw(src.get('P_dc_W'))} ({q(src.get('I_dc_A'), 'A')} @ {q(src.get('V_V'), 'V')})"), "info")
            idn = w.get("identities") or {}
            if idn:
                s.add(tr("전력 항등식 잔차: ", "power identity residuals: ") + ", ".join(
                    f"{tr(*_IDENT[k]) if k in _IDENT else esc(k)} {_res(v, 'W')}" for k, v in idn.items()),
                      "ok" if all(abs(v or 0) < 1e-6 for v in idn.values()) else "warn")
        claims_section(ins, w.get("claims"))
        not_modelled_section(ins, w.get("not_modelled"))
    g = res.get("geometry") or {}
    if g:
        s = ins.section(tr("전압 상태쌍의 기하", "geometry of the voltage state pairs"))
        s.add(tr(f"상태쌍 {g.get('pairs')}개 → αβ 점 {g.get('unique_alphabeta')}개, 이 중 허용 {g.get('admissible_pairs')}쌍 / "
                 f"{g.get('admissible_unique_alphabeta')}점; 보장 반경 {q(g.get('hull_inradius_V'), 'V')} (단일 VSI {q(g.get('single_vsi_inradius_V'), 'V')}, "
                 f"{num((g.get('hull_inradius_V') or 0) / (g.get('single_vsi_inradius_V') or 1), 3)}배)",
                 f"{g.get('pairs')} state pairs → {g.get('unique_alphabeta')} αβ points, admissible {g.get('admissible_pairs')} pairs / "
                 f"{g.get('admissible_unique_alphabeta')} points; guaranteed radius {q(g.get('hull_inradius_V'), 'V')} (single VSI "
                 f"{q(g.get('single_vsi_inradius_V'), 'V')}, ×{num((g.get('hull_inradius_V') or 0) / (g.get('single_vsi_inradius_V') or 1), 3)})"),
              "info")
    rip = res.get("i0_ripple") or {}
    for k, x in rip.items():
        if isinstance(x, dict) and x.get("i0_rms_A") is not None:
            s = ins.section(tr("스위칭 영상분 리플 (공통 bus)", "switched zero-sequence ripple (common bus)"))
            s.add(tr(f"캐리어 위상차 {num(float(x.get('carrier_shift') or 0) * 360)}°: i0 {q(x.get('i0_pp_A'), 'A')} pp, {q(x.get('i0_rms_A'), 'A')} rms → "
                     f"영상분 동손 {q(x.get('copper_zero_sequence_W'), 'W')}",
                     f"carrier shift {num(float(x.get('carrier_shift') or 0) * 360)}°: i0 {q(x.get('i0_pp_A'), 'A')} pp, "
                     f"{q(x.get('i0_rms_A'), 'A')} rms → zero-sequence copper {q(x.get('copper_zero_sequence_W'), 'W')}"), "warn",
                  tr(f"L0 {q((x.get('L0_H') or 0) * 1e6, 'µH')}, fsw {q((x.get('fsw_Hz') or 0) / 1e3, 'kHz')}",
                     f"L0 {q((x.get('L0_H') or 0) * 1e6, 'µH')}, fsw {q((x.get('fsw_Hz') or 0) / 1e3, 'kHz')}"))
    return ins.nonempty()


def oew_compare_insight(res: dict) -> Insight:
    rows = res.get("rows") or []
    basis = res.get("comparison_basis") or {}
    keys = [("single_vsi_Nm", tr("단일 VSI", "single VSI")), ("oew_common_bus_Nm", tr("OEW 공통 bus", "OEW common bus")),
            ("oew_isolated_Nm", tr("OEW 분리 전원", "OEW isolated")), ("single_vsi_same_stack_Nm", tr("단일 VSI 같은 총 전압", "single VSI, same stack"))]
    head = tr("토크-속도 비교 (같은 방법의 격자 최대 — 전기적 capability의 하한)", "torque-speed comparison (grid maxima by one method — "
                                                                  "lower bounds of the electrical capability)")
    ins = Insight(headline=head)
    if rows:
        s = ins.section(tr("속도별 최대 토크", "maximum torque per speed"))
        for r in rows:
            base = r.get("single_vsi_Nm")
            parts = []
            for k, lab in keys:
                v = r.get(k)
                if v is None:
                    continue
                gain = "" if k == "single_vsi_Nm" or not base else f" ({'+' if v >= base else ''}{pct(v - base, base, 0)})"
                parts.append(f"{lab} {num(v)}{gain}")
            s.add(f"<b>{q(r.get('speed_rpm'), 'rpm')}</b>: " + " · ".join(parts) + " N·m", "info")
        # where the configurations part: first speed where OEW common bus exceeds the single VSI
        split = next((r for r in rows if r.get("oew_common_bus_Nm") and r.get("single_vsi_Nm") and
                      r["oew_common_bus_Nm"] > r["single_vsi_Nm"] * (1 + 1e-9)), None)
        if split:
            s.add(tr(f"단일 VSI가 전압 한계에 걸리기 시작하는 속도 부근({q(split['speed_rpm'], 'rpm')})부터 OEW가 앞섭니다 — 같은 전류에서 "
                     f"더 큰 전압 반경", f"from about {q(split['speed_rpm'], 'rpm')}, where the single VSI meets its voltage limit, the "
                     f"OEW leads — a larger voltage radius at the same current"), "info")
    s = ins.section(tr("비교 기준", "comparison basis"))
    for k, lab in keys:
        b = basis.get(k.replace("_Nm", ""))
        if b:
            s.add(f"{lab}: {esc(engine_text(b))}", "info")
    notes_section(ins, [res.get("meaning")])
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- HEV
def hev_joint_insight(res: dict) -> Insight:
    ms = res.get("machines") or []
    rq = res.get("request") or {}
    jc, bc = res.get("joint_cells_feasible"), res.get("box_cells_feasible_separately")
    head = tr(f"두 기기 동시 가능 토크: {jc}/{bc} 셀 ({pct(jc, bc, 0)}) — 따로 본 최대값 사각형이 그대로 쓸 수 있는 영역이 아님",
              f"jointly feasible torque pairs: {jc}/{bc} cells ({pct(jc, bc, 0)}) — the rectangle of separate maxima is not "
              f"the available set")
    if rq:
        head += tr(f"; 요구 ({num(rq.get('T1_Nm'))}, {num(rq.get('T2_Nm'))}) N·m {_st(rq.get('status'))}",
                   f"; request ({num(rq.get('T1_Nm'))}, {num(rq.get('T2_Nm'))}) N·m {_st(rq.get('status'))}")
    ins = Insight(headline=head, verdict={"FEASIBLE": "PASS", "INFEASIBLE": "FAIL"}.get(rq.get("status")) if rq else None)
    ins.metrics.append((tr("동시 가능 / 개별 가능", "joint / separate"), f"{jc} / {bc}", "info"))
    s = ins.section(tr("기기별 범위 (각자 따로)", "per-machine range (each on its own)"))
    for m in ms:
        rg = m.get("range_Nm") or [None, None]
        s.add(f"<b>{esc(m.get('name', ''))}</b> ({esc(m.get('role', ''))}) @ {q(m.get('speed_rpm'), 'rpm')}: "
              f"[{num(rg[0])}, {num(rg[1])}] N·m", "info")
    # why cells fail: the most frequent limiting reasons
    reasons = res.get("reason") or []
    flat = [x for row in reasons for x in (row or []) if x]
    if flat:
        from collections import Counter
        kinds = Counter()
        for x in flat:
            for part in x.split("; "):
                kinds[part.split(" ")[0] + (" " + part.split(" ")[1] if part.startswith(("charge", "discharge")) else "")] += 1
        s = ins.section(tr("동시에 안 되는 이유 (셀 수)", "why pairs fail (cell counts)"),
                        tr("같은 전원을 나눠 쓰므로 배터리 충전·방전 전력과 인덕터 전류가 쌍을 제한합니다.",
                           "the machines share one source, so the battery charge / discharge power and the inductor "
                           "current limit the pairs."))
        lab = {"|I_L|": tr("부스트 인덕터 전류 한계", "boost inductor current limit"),
               "charge power": tr("배터리 충전 전력 한계", "battery charge power limit"),
               "discharge power": tr("배터리 방전 전력 한계", "battery discharge power limit")}
        for k, n in kinds.most_common():
            s.add(f"{lab.get(k, esc(k))}: {n}", "info")
    if rq:
        s = ins.section(tr("요구점의 전력 흐름", "power flow at the requested pair"))
        b = rq.get("branch_P_dc_W") or [None, None]
        s.add(tr(f"가지 전력 EM1 {kw(b[0])}, EM2 {kw(b[1])} → 기기 순합 {kw(rq.get('net_machines_W'))}, 순환 {kw(rq.get('circulating_W'))}, "
                 f"배터리 {kw(rq.get('P_source_W'))}", f"branch powers EM1 {kw(b[0])}, EM2 {kw(b[1])} → machines net "
                 f"{kw(rq.get('net_machines_W'))}, circulating {kw(rq.get('circulating_W'))}, battery {kw(rq.get('P_source_W'))}"),
              status_level(rq.get("status")), esc(engine_text(rq.get("note", ""))))
        bs = rq.get("boost_state") or {}
        if bs:
            s.add(tr(f"부스트: 듀티 {num(bs.get('duty'), 3)}, 인덕터 {q(bs.get('I_L_A'), 'A')}, 배터리 {q(bs.get('V_bat_V'), 'V')} · "
                     f"{kw(bs.get('P_bat_W'))}, 손실 {q(bs.get('P_loss_W'), 'W')}", f"boost: duty {num(bs.get('duty'), 3)}, inductor "
                     f"{q(bs.get('I_L_A'), 'A')}, battery {q(bs.get('V_bat_V'), 'V')} · {kw(bs.get('P_bat_W'))}, loss "
                     f"{q(bs.get('P_loss_W'), 'W')}"), "info")
    notes_section(ins, [res.get("meaning"), (res.get("bus") or {}).get("note")])
    return ins.nonempty()


def crank_insight(res: dict) -> Insight:
    c = res.get("claim") or {}
    runs = res.get("runs") or []
    inp = res.get("inputs") or {}
    ok = [r for r in runs if r.get("ok")]
    worst = res.get("worst") or {}
    head = tr(f"크랭킹 {q(inp.get('n_target_rpm'), 'rpm')}까지 {q(inp.get('t_max_s'), 's')} 안: 초기 크랭크각 {len(ok)}/{len(runs)}개에서 도달"
              + (f", 최악 {q(worst.get('reached_s'), 's', 3)} @ {num(worst.get('theta0_deg'))}°" if worst.get("reached_s") is not None else ""),
              f"cranking to {q(inp.get('n_target_rpm'), 'rpm')} within {q(inp.get('t_max_s'), 's')}: reached from {len(ok)}/{len(runs)} "
              f"initial crank angles" + (f", worst {q(worst.get('reached_s'), 's', 3)} @ {num(worst.get('theta0_deg'))}°"
                                         if worst.get("reached_s") is not None else ""))
    ins = Insight(headline=head, verdict=verdict_of(c))
    if runs:
        ins.metrics += [(tr("최악 도달 시간", "worst time"), q(max(r.get("reached_s") or 0 for r in runs), "s", 3), "info"),
                        (tr("최저 버스 전압", "lowest bus voltage"), q(min(r.get("V_bus_min_V") or np.inf for r in runs), "V"), "info"),
                        (tr("배터리 최대 전력", "peak battery power"), kw(max(r.get("P_bat_max_W") or 0 for r in runs)), "info")]
        s = ins.section(tr("초기 크랭크각별", "per initial crank angle"))
        for r in runs:
            s.add(tr(f"θ0 {num(r.get('theta0_deg'))}°: {q(r.get('reached_s'), 's', 3)} (여유 {q((inp.get('t_max_s') or 0) - (r.get('reached_s') or 0), 's', 3)}), "
                     f"버스 최저 {q(r.get('V_bus_min_V'), 'V')}, 배터리 {kw(r.get('P_bat_max_W'))}, 전류 {q(r.get('I_peak_max_A'), 'A')}, 에너지 {q(r.get('energy_J'), 'J')}",
                     f"θ0 {num(r.get('theta0_deg'))}°: {q(r.get('reached_s'), 's', 3)} (slack "
                     f"{q((inp.get('t_max_s') or 0) - (r.get('reached_s') or 0), 's', 3)}), bus min {q(r.get('V_bus_min_V'), 'V')}, "
                     f"battery {kw(r.get('P_bat_max_W'))}, current {q(r.get('I_peak_max_A'), 'A')}, energy {q(r.get('energy_J'), 'J')}"),
                  "ok" if r.get("ok") else "bad", "; ".join(esc(engine_text(x)) for x in r.get("failures") or []))
    cap = res.get("starter_capability") or {}
    if cap.get("Tmax_Nm"):
        s = ins.section(tr("시동 기기의 토크 여유", "starter torque headroom"))
        T = inp.get("T_cmd_Nm")
        s.add(tr(f"명령 {q(T, 'N·m')} vs 속도별 최대 {num(min(cap['Tmax_Nm']))}–{num(max(cap['Tmax_Nm']))} N·m (전압 하한 {q(cap.get('V_floor_V'), 'V')}에서)",
                 f"command {q(T, 'N·m')} vs maximum {num(min(cap['Tmax_Nm']))}–{num(max(cap['Tmax_Nm']))} N·m over speed (at the "
                 f"voltage floor {q(cap.get('V_floor_V'), 'V')})"), "ok" if T is not None and T <= min(cap["Tmax_Nm"]) else "info")
    s = ins.section(tr("판정", "claim"))
    if c:
        claim_item(s, c)
    notes_section(ins, res.get("notes"), [inp.get("load_basis")])
    return ins.nonempty()


def rejection_insight(res: dict) -> Insight:
    import re
    c = res.get("claim") or {}
    led = res.get("ledger") or {}
    m = re.search(r"stays below (\S+) V", str(c.get("quantity", "")))
    vmax = float(m.group(1)) if m else None
    lim = (tr(f" vs 한계 {q(vmax, 'V')}", f" vs limit {q(vmax, 'V')}") if vmax is not None else "")
    t_lim = res.get("time_to_limit_s")
    if t_lim is not None and t_lim != float("inf"):
        head = tr(f"부하 차단: 잉여 {kw(res.get('P_excess_W'))} → 커패시터 여유 {q(res.get('E_margin_J'), 'J')}을 {q(t_lim * 1e6, 'µs')}에 "
                  f"소진 — 최고 {q(res.get('V_peak_V'), 'V')}{lim}",
                  f"load rejection: excess {kw(res.get('P_excess_W'))} → the capacitor margin {q(res.get('E_margin_J'), 'J')} is "
                  f"used in {q(t_lim * 1e6, 'µs')} — peak {q(res.get('V_peak_V'), 'V')}{lim}")
    else:
        head = tr(f"부하 차단: 잉여 {kw(res.get('P_excess_W'))} — 최고 {q(res.get('V_peak_V'), 'V')}{lim} (여유 {q(res.get('E_margin_J'), 'J')} 안)",
                  f"load rejection: excess {kw(res.get('P_excess_W'))} — peak {q(res.get('V_peak_V'), 'V')}{lim} (within the "
                  f"{q(res.get('E_margin_J'), 'J')} margin)")
    ins = Insight(headline=head, verdict=verdict_of(c))
    ins.metrics += [(tr("잉여 전력", "excess power"), kw(res.get("P_excess_W")), "info"),
                    (tr("여유 에너지 / 유입", "margin / energy in"), f"{num(res.get('E_margin_J'))} / {num(res.get('E_peak_J'))} J",
                     "ok" if (res.get("E_peak_J") or 0) <= (res.get("E_margin_J") or 0) else "bad")]
    s = ins.section(tr("에너지 장부", "energy ledger"))
    src, snk = led.get("sources_W") or [], led.get("sinks_W") or []
    s.add(tr(f"소스 {' + '.join(kw(x) for x in src)} − 싱크 {' + '.join(kw(x) for x in snk)} = 잉여 {kw(res.get('P_excess_W'))}, 공통 커패시터 "
             f"{q(led.get('capacitor_uF'), 'µF')}", f"sources {' + '.join(kw(x) for x in src)} − sinks {' + '.join(kw(x) for x in snk)} = "
             f"excess {kw(res.get('P_excess_W'))}, common capacitor {q(led.get('capacitor_uF'), 'µF')}"), "info",
          esc(engine_text(led.get("note", ""))))
    if res.get("E_peak_J") is not None and res.get("E_margin_J"):
        s.add(tr(f"유입 에너지 {q(res['E_peak_J'], 'J')} = 여유의 {num(res['E_peak_J'] / res['E_margin_J'], 3)}배 (@ {q((res.get('t_peak_s') or 0) * 1e3, 'ms')})",
                 f"energy in {q(res['E_peak_J'], 'J')} = {num(res['E_peak_J'] / res['E_margin_J'], 3)}× the margin (@ "
                 f"{q((res.get('t_peak_s') or 0) * 1e3, 'ms')})"), "bad" if res["E_peak_J"] > res["E_margin_J"] else "ok")
    if res.get("domain_end_s") is not None:
        s.add(tr(f"모델 영역 끝 {q(res['domain_end_s'], 's')} (정전력 싱크가 커패시터를 비움)", f"model domain ends at "
                 f"{q(res['domain_end_s'], 's')} (the constant-power sink empties the capacitor)"), "open")
    s = ins.section(tr("판정", "claim"))
    if c:
        claim_item(s, c)
    return ins.nonempty()


def planetary_insight(res: dict) -> Insight:
    ch = res.get("check") or {}
    n, T, P = res.get("speeds_rpm") or {}, res.get("torques_Nm") or {}, res.get("powers_W") or {}
    nm = {"sun": tr("선기어", "sun"), "ring": tr("링기어", "ring"), "carrier": tr("캐리어", "carrier")}
    head = tr(f"유성기어 (Ns {res.get('Ns')}, Nr {res.get('Nr')}): ", f"planetary set (Ns {res.get('Ns')}, Nr {res.get('Nr')}): ") + \
        " · ".join(f"{nm.get(k, k)} {num(n.get(k))} rpm / {num(T.get(k))} N·m" for k in ("sun", "ring", "carrier"))
    ins = Insight(headline=head, verdict=verdict_of(ch))
    s = ins.section(tr("세 축의 전력 (기어 세트로 들어가는 방향 +)", "power at the three shafts (into the set +)"))
    for k in ("sun", "ring", "carrier"):
        p = P.get(k)
        s.add(tr(f"{nm.get(k, k)}: {kw(p)} ({'입력' if (p or 0) > 0 else '출력'})", f"{nm.get(k, k)}: {kw(p)} "
                 f"({'in' if (p or 0) > 0 else 'out'})"), "info")
    s.add(tr(f"Willis 잔차 {_res(ch.get('willis_residual'), '')}, 토크비 잔차 {_res(ch.get('torque_ratio_residual_Nm'), 'N·m')}, 전력 합 잔차 "
             f"{_res(ch.get('power_residual_W'), 'W')}", f"Willis residual {_res(ch.get('willis_residual'), '')}, torque-ratio residual "
             f"{_res(ch.get('torque_ratio_residual_Nm'), 'N·m')}, power-sum residual {_res(ch.get('power_residual_W'), 'W')}"),
          status_level(ch.get("status")), esc(engine_text(ch.get("assumptions", ""))))
    notes_section(ins, [res.get("convention")])
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- EMI
def emi_insight(res: dict) -> Insight:
    c = res.get("claim") or {}
    dom = res.get("domain") or []
    g = np.asarray(res.get("grid_Hz") if res.get("grid_Hz") is not None else [], float)
    A = np.asarray(res.get("required_attenuation_dB") if res.get("required_attenuation_dB") is not None else [], float)
    Ab = np.asarray(res.get("required_attenuation_bound_dB") if res.get("required_attenuation_bound_dB") is not None
                    else [], float)
    src_dom = np.asarray(res.get("dominant_source") if res.get("dominant_source") is not None else [], dtype=object)
    d0 = next((d for d in dom if d.get("min_margin_dB") is not None), None)
    prof = res.get("profile") or {}
    band = res.get("band") or {}
    if d0:
        m = d0.get("min_margin_est_dB")
        fm = d0.get("f_min_margin_est_Hz")
        if m is None:
            m, fm = d0["min_margin_dB"], d0["f_min_margin_Hz"]
        head = tr(f"전도성 방출 ({num(d0['lo_Hz'] / 1e6)}–{num(d0['hi_Hz'] / 1e6)} MHz, 스크리닝): 추정 최소 여유 {num(m, 4)} dB @ "
                  f"{num(fm / 1e6, 4)} MHz — " + ("한계 초과 예측" if m < 0 else "한계 안")
                  + f" (상한 기준 {num(d0['min_margin_dB'], 4)} dB)",
                  f"conducted emission ({num(d0['lo_Hz'] / 1e6)}–{num(d0['hi_Hz'] / 1e6)} MHz, screening): estimated minimum "
                  f"margin {num(m, 4)} dB @ {num(fm / 1e6, 4)} MHz — " + ("exceedance predicted" if m < 0 else "within the limit")
                  + f" (on the bound {num(d0['min_margin_dB'], 4)} dB)")
    else:
        head = tr("전도성 방출: ", "conducted emission: ") + esc(engine_parts(c.get("detail", "")))
    ins = Insight(headline=head, verdict=verdict_of(c))
    if d0:
        me = d0.get("min_margin_est_dB")
        if me is not None:
            ins.metrics.append((tr("최소 여유 (추정)", "minimum margin (estimate)"), f"{num(me, 4)} dB", "bad" if me < 0 else "ok"))
        ins.metrics += [(tr("최소 여유 (상한)", "minimum margin (bound)"), f"{num(d0['min_margin_dB'], 4)} dB",
                         "warn" if d0["min_margin_dB"] < 0 else "ok"),
                        (tr("최대 방출 추정 / 상한", "peak emission estimate / bound"),
                         f"{num(d0.get('E_est_sup_dBuV'), 4)} / {num(d0.get('E_sup_dBuV'), 4)} dBµV", "info")]
    fin = np.isfinite(A) if A.size else np.array([], bool)
    if A.size and fin.any():
        j = int(np.nanargmax(np.where(fin, A, -np.inf)))
        ins.metrics.append((tr("최대 필요 감쇠 (추정)", "max required attenuation (estimate)"), f"{num(A[j], 4)} dB",
                            "warn" if A[j] > 0 else "ok"))
        s = ins.section(tr("대역별 필요 감쇠와 지배 경로", "required attenuation and dominant path per band"),
                        tr("필요 감쇠 = 추정 방출 + 보정 오차 − (한계 − 설계 예비). 선합 상한 기준 값을 옆에 적습니다 — 필터를 상한으로 "
                           "설계하면 추정보다 그만큼 과설계됩니다. CM(공통모드)·DM(차동모드) 중 큰 쪽이 필터 설계를 정합니다.",
                           "required attenuation = estimated emission + model error − (limit − design reserve); the figure on "
                           "the line-sum bound is shown next to it — a filter sized on the bound is over-specified by the "
                           "difference. The larger of the common-mode and differential-mode paths drives the filter."))
        for lo, hi in ((0.15e6, 0.5e6), (0.5e6, 2e6), (2e6, 10e6), (10e6, 30e6)):
            mk = (g >= lo) & (g <= hi) & fin
            if not mk.any():
                continue
            k = int(np.nanargmax(np.where(mk, A, -np.inf)))
            cm = int(np.sum(src_dom[mk] == "CM")) if src_dom.size == g.size else 0
            kb = (float(np.nanmax(np.where(mk, Ab, -np.inf))) if Ab.size == g.size else None)
            s.add(tr(f"{num(lo / 1e6)}–{num(hi / 1e6)} MHz: 최대 {num(A[k], 4)} dB @ {num(g[k] / 1e6, 4)} MHz"
                     + (f" (상한 기준 {num(kb, 4)} dB)" if kb is not None else "") + f" — CM 지배 {cm}/{int(mk.sum())} 점",
                     f"{num(lo / 1e6)}–{num(hi / 1e6)} MHz: up to {num(A[k], 4)} dB @ {num(g[k] / 1e6, 4)} MHz"
                     + (f" (on the bound {num(kb, 4)} dB)" if kb is not None else "")
                     + f" — CM-dominated at {cm}/{int(mk.sum())} points"), "bad" if A[k] > 0 else "ok")
    s = ins.section(tr("수신기 모델 (추정 · 상한 · 반례)", "receiver model (estimate · bound · witness)"),
                    tr("직사각 IF·피크 검출기는 창 안 선들의 포락 최대를 읽습니다. 선의 크기 합은 그 상한일 뿐이고 (창당 선이 많을수록 "
                       "커짐), CM 전류가 어느 레일로 돌아가는지는 모델이 정하지 못해 4가지 귀환 모델을 모두 봅니다.",
                       "a rectangular IF with a peak detector reads the envelope maximum of the in-window lines; the "
                       "magnitude sum is only its upper bound (larger with more lines per window), and the rail that returns "
                       "the CM current is not fixed by this model, so four return models are evaluated."))
    s.add(tr("추정: 직사각 IF 포락 피크 — 중점(½·½)·에지 부호(상승→HV+, 하강→HV−) 모델 중 큰 쪽, 설계 수치(필요 감쇠)의 근거",
             "estimate: rectangular-IF envelope peak — the larger of the midpoint (½·½) and edge-sign (rising → HV+, "
             "falling → HV−) models; the basis of the design figures (required attenuation)"), "info")
    s.add(tr("상한: 두 포트·4개 귀환 모델(중점·에지 부호·HV+·HV−)의 선합과 가우시안 IF 가중합 중 최대 — 합격 판정(FEASIBLE)의 근거",
             "bound: the largest of the line sums over both ports and four return models (midpoint, edge sign, HV+, HV−) "
             "and the Gaussian-IF weighted sum — the basis of a FEASIBLE claim"), "info")
    s.add(tr("반례: 4개 귀환 모델 중 가장 작은 포락 피크 − U− — 위반(INFEASIBLE)은 이것이 한계를 넘어야만",
             "witness: the smallest envelope peak over the four return models − U− — a violation (INFEASIBLE) only when "
             "it exceeds the limit"), "info")
    for d in dom:
        if d.get("min_margin_dB") is None:
            continue
        w = d.get("witness") or {}
        s = ins.section(tr("판정 근거 (정확 열거)", "the evidence (exact enumeration)"))
        s.add(tr(f"수신기 창 {num(d.get('lines'))}개 선 · 후보 {num(d.get('candidates'))}개 전부 열거 (창당 선 최대 "
                 f"{num(d.get('lines_per_window_max'))}개): 추정 최대 {num(d.get('E_est_sup_dBuV'), 5)} dBµV @ "
                 f"{num((d.get('f_E_est_sup_Hz') or 0) / 1e6, 5)} MHz, 상한 최대 {num(d.get('E_sup_dBuV'), 5)} dBµV @ "
                 f"{num((d.get('f_E_sup_Hz') or 0) / 1e6, 5)} MHz (창당 선 {num(d.get('lines_at_E_sup'))}개)",
                 f"{num(d.get('lines'))} lines · {num(d.get('candidates'))} candidate windows enumerated (up to "
                 f"{num(d.get('lines_per_window_max'))} lines per window): estimate peak {num(d.get('E_est_sup_dBuV'), 5)} dBµV @ "
                 f"{num((d.get('f_E_est_sup_Hz') or 0) / 1e6, 5)} MHz, bound peak {num(d.get('E_sup_dBuV'), 5)} dBµV @ "
                 f"{num((d.get('f_E_sup_Hz') or 0) / 1e6, 5)} MHz ({num(d.get('lines_at_E_sup'))} lines in that window)"), "info")
        if w:
            s.add(tr(f"반례 창 {num(w.get('f_Hz', 0) / 1e6, 5)} MHz: 방출 하한 {num(w.get('E_lower_dBuV'), 5)} dBµV vs 한계 {num(w.get('limit_dBuV'))} "
                     f"(예비 뺀 {num(w.get('limit_minus_reserve_dBuV'))}) dBµV",
                     f"witness window {num(w.get('f_Hz', 0) / 1e6, 5)} MHz: emission lower bound {num(w.get('E_lower_dBuV'), 5)} dBµV "
                     f"vs limit {num(w.get('limit_dBuV'))} (less reserve {num(w.get('limit_minus_reserve_dBuV'))}) dBµV"), "bad")
    if band.get("estimate_capped"):
        s = ins.section(tr("추정 계산 한도", "estimate budget"))
        s.add(tr("포락 분지한정 계산이 한도에서 멈춤 — 추정 수치는 대역 값의 하한이며 반례를 놓쳤을 수 있음 (상한 쪽은 정확)",
                 "the envelope branch and bound stopped at its budget — the estimate figures are lower bounds of their "
                 "band values and a witness may be missed (the bound side is exact)"), "warn")
    if prof.get("design_reserve_declared") is False:
        ins.metrics.append((tr("설계 예비", "design reserve"), tr("미선언 (0 dB 사용)", "not declared (0 dB used)"), "warn"))
    so = res.get("source") or {}
    if so:
        s = ins.section(tr("소스 (스위칭 파형)", "the source (switching waveform)"))
        car = {"asynchronous": tr("비동기 캐리어", "asynchronous carrier"), "synchronous": tr("동기 캐리어", "synchronous carrier")}.get(
            so.get("carrier"), esc(so.get("carrier", "")))
        s.add(tr(f"fsw 요청 {q(so.get('fsw_requested_kHz'), 'kHz')} → 평가 {q(so.get('fsw_used_kHz'), 'kHz')} ({car}, "
                 f"캐리어 비 {so.get('carrier_ratio')}), 상승/하강 {q(so.get('t_rise_ns'), 'ns')}/{q(so.get('t_fall_ns'), 'ns')}, 데드타임 "
                 f"{q(so.get('t_dead_us'), 'µs')}", f"fsw requested {q(so.get('fsw_requested_kHz'), 'kHz')} → evaluated "
                 f"{q(so.get('fsw_used_kHz'), 'kHz')} ({car}, carrier ratio {so.get('carrier_ratio')}), rise/fall "
                 f"{q(so.get('t_rise_ns'), 'ns')}/{q(so.get('t_fall_ns'), 'ns')}, dead time {q(so.get('t_dead_us'), 'µs')}"),
              "ok" if (so.get("validity") or {}).get("ok") else "bad", esc(so.get("basis", "")))
    cp = res.get("coupling") or {}
    if cp:
        s = ins.section(tr("같이 봐야 할 결합 효과", "coupled effects to check alongside"))
        y = cp.get("y_capacitor") or {}
        if y:
            s.add(tr(f"Y 커패시터 {q((y.get('C_y_per_rail_F') or 0) * 1e9, 'nF')}/레일: 절연 고장 시 한 레일 에너지 {q((y.get('energy_per_rail_at_Vdc_J') or 0) * 1e3, 'mJ')} "
                     f"— 접촉 에너지 요구와 비교", f"Y capacitor {q((y.get('C_y_per_rail_F') or 0) * 1e9, 'nF')}/rail: energy on one rail "
                     f"at an insulation fault {q((y.get('energy_per_rail_at_Vdc_J') or 0) * 1e3, 'mJ')} — compare with the touch-energy "
                     f"requirement"), "open")
        r_ = cp.get("dm_resonance") or {}
        if r_:
            s.add(tr(f"DM 공진 {q(r_.get('f_res_Hz'), 'Hz')} (Q {num(r_.get('Q'), 3)}) — 제어 대역과 가까우면 입력 임피던스 안정성 확인",
                     f"DM resonance {q(r_.get('f_res_Hz'), 'Hz')} (Q {num(r_.get('Q'), 3)}) — check the input-impedance stability "
                     f"near the control bandwidth"), "open")
        cm = cp.get("common_mode") or {}
        if cm:
            s.add(tr(f"CM 전압 스텝 {q(cm.get('v_cm_step_V'), 'V')}, dv/dt {q((cm.get('dv_dt_V_per_s') or 0) / 1e9, 'kV/µs')} → 기생 C 전류 "
                     f"{q(cm.get('i_Cpar_sanity_A'), 'A')} (국부 추정)", f"CM voltage step {q(cm.get('v_cm_step_V'), 'V')}, dv/dt "
                     f"{q((cm.get('dv_dt_V_per_s') or 0) / 1e9, 'kV/µs')} → parasitic-C current {q(cm.get('i_Cpar_sanity_A'), 'A')} "
                     f"(local estimate)"), "info")
    if prof.get("limit_source"):
        s = ins.section(tr("한계 곡선", "limit curve"))
        s.add(esc(prof.get("limit_source", "")), "open" if "EXAMPLE" in str(prof.get("limit_source")) else "info",
              tr(f"설계 예비 {q(prof.get('design_reserve_dB'), 'dB')}, 검출기 {esc(prof.get('detector', ''))}, RBW {q((prof.get('rbw_Hz') or 0) / 1e3, 'kHz')}",
                 f"design reserve {q(prof.get('design_reserve_dB'), 'dB')}, detector {esc(prof.get('detector', ''))}, RBW "
                 f"{q((prof.get('rbw_Hz') or 0) / 1e3, 'kHz')}"))
    s = ins.section(tr("판정", "claim"))
    if c:
        claim_item(s, c)
    notes_section(ins, res.get("notes"))
    return ins.nonempty()


def emi_oew_insight(res: dict) -> Insight:
    cases = res.get("cases") or {}
    rows = sorted(cases.items(), key=lambda kv: float(kv[0]))
    best = min(rows, key=lambda kv: kv[1].get("cm6_rms_V") or np.inf) if rows else None
    head = tr("OEW 공통모드 전압 (캐리어 위상차별)", "OEW common-mode voltage (per carrier shift)")
    if best:
        head += tr(f": 6-스위치 CM 전압 rms 최소 {q(best[1].get('cm6_rms_V'), 'V')} @ {num(float(best[0]) * 360)}°",
                   f": lowest 6-switch CM rms {q(best[1].get('cm6_rms_V'), 'V')} @ {num(float(best[0]) * 360)}°")
    ins = Insight(headline=head)
    s = ins.section(tr("캐리어 위상차에 따른 맞바꿈", "the trade over the carrier shift"),
                    tr("브리지 간 캐리어 위상을 옮기면 섀시로 가는 CM 전압과 권선 영상분 전압 u0, DC 전류 리플이 서로 반대로 움직입니다.",
                       "shifting one bridge's carrier trades the CM voltage to chassis against the winding zero-sequence "
                       "voltage u0 and the DC current ripple."))
    for k, c in rows:
        dc = c.get("dc_currents") or {}
        s.add(tr(f"<b>{num(float(k) * 360)}°</b>: CM(6스위치) {q(c.get('cm6_rms_V'), 'V')} rms · u0 {q(c.get('u0_rms_V'), 'V')} rms · DC 전류 "
                 f"A {q(dc.get('I_A_rms_A'), 'A')} / B {q(dc.get('I_B_rms_A'), 'A')} / 합 {q(dc.get('I_sum_rms_A'), 'A')} rms",
                 f"<b>{num(float(k) * 360)}°</b>: CM (6 switches) {q(c.get('cm6_rms_V'), 'V')} rms · u0 {q(c.get('u0_rms_V'), 'V')} "
                 f"rms · DC current A {q(dc.get('I_A_rms_A'), 'A')} / B {q(dc.get('I_B_rms_A'), 'A')} / sum "
                 f"{q(dc.get('I_sum_rms_A'), 'A')} rms"), "info",
              tr(f"항등식 잔차 {num(dc.get('identity_residual'), 2)}", f"identity residual {num(dc.get('identity_residual'), 2)}"))
    z = res.get("zsv_free") or {}
    if z:
        s = ins.section(tr("영상분 없는 상태쌍", "zero-u0 state pairs"))
        steps = ", ".join(num(x) for x in z.get("v_cm6_steps_V") or [])
        s.add(tr(f"u0 최대 {q(z.get('u0_max_V'), 'V')}, CM 전압 단계 {steps} V", f"u0 max {q(z.get('u0_max_V'), 'V')}, CM voltage "
                 f"steps {steps} V"), "info", esc(engine_text(z.get("note", ""))))
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- machine design
_CHECK = {"low-speed torque": ("저속 토크", "low-speed torque"), "high-speed torque @ min Vdc": ("최소 Vdc 고속 토크", "high-speed torque @ min Vdc"),
          "requirement point": ("요구점 전류", "requirement-point current"), "UGO back-EMF": ("무제어 역기전력 (UGO)", "uncontrolled back-EMF (UGO)"),
          "steady ASC current": ("정상 ASC 전류", "steady ASC current")}


def _check_label(n: str) -> str:
    if n in _CHECK:
        return tr(*_CHECK[n])
    if n.startswith("copper loss @ "):
        return tr("동손 @ ", "copper loss @ ") + esc(n[len("copper loss @ "):])
    return esc(n)


def machine_trade_insight(res: dict) -> Insight:
    rows = [r for r in res.get("rows") or [] if "checks" in r]
    bad = [r for r in res.get("rows") or [] if "error" in r]
    ok = [r for r in rows if r.get("all_feasible")]
    ref = next((r for r in rows if not (r.get("lineage") or {}).get("derived")), None)
    head = tr(f"설계 후보 {len(rows)}개 중 모든 요구 충족 {len(ok)}개 ({', '.join(esc(r['candidate']) for r in ok) or '없음'})",
              f"{len(ok)} of {len(rows)} candidates meet every requirement ({', '.join(esc(r['candidate']) for r in ok) or 'none'})")
    if ref:
        tight = min(((n, c) for n, c in ref["checks"].items() if c.get("rel_margin") is not None), key=lambda kv: kv[1]["rel_margin"],
                    default=None)
        if tight:
            head += tr(f"; 기준의 가장 빠듯한 항목: {_check_label(tight[0])} ({pct(tight[1]['rel_margin'], 1, 1)})",
                       f"; the reference's tightest item: {_check_label(tight[0])} ({pct(tight[1]['rel_margin'], 1, 1)})")
    ins = Insight(headline=head, verdict=None)
    s = ins.section(tr("후보별 상대 여유 (+ 여유, − 위반)", "relative margin per candidate (+ margin, − violation)"),
                    tr("같은 요구·온도·전원에서 결합 계산한 여유입니다. 파생 후보는 기준에서 스케일한 것으로 검증되지 않았습니다.",
                       "coupled margins on the same requirements, temperatures and sources; derived candidates are scaled "
                       "from the reference and not validated."))
    for r in rows:
        parts = []
        for n, c in r["checks"].items():
            rm = c.get("rel_margin")
            parts.append(f"{_check_label(n)} {'+' if (rm or 0) >= 0 else ''}{pct(rm, 1, 1)}" if rm is not None else
                         f"{_check_label(n)} {_st(c.get('status'))}")
        s.add(f"<b>{esc(r['candidate'])}</b>" + tr(f" (구속: {_check_label(r.get('binding', ''))}): ", f" (binding: "
                                                   f"{_check_label(r.get('binding', ''))}): ") + " · ".join(parts),
              "ok" if r.get("all_feasible") else "bad")
    if ref and len(rows) > 1:
        s = ins.section(tr("기준 대비 무엇이 바뀌나 (상대 여유의 차)", "what changes against the reference (difference of relative margins)"))
        for r in rows:
            if r is ref:
                continue
            d = []
            for n, c in r["checks"].items():
                a, b = (ref["checks"].get(n) or {}).get("rel_margin"), c.get("rel_margin")
                if a is not None and b is not None:
                    dd = 0.0 if abs(b - a) < 1e-9 else 100 * (b - a)
                    d.append(f"{_check_label(n)} {'+' if dd >= 0 else ''}{num(dd, 3)} %p")
            f = (r.get("lineage") or {}).get("factors") or {}
            s.add(f"<b>{esc(r['candidate'])}</b>: " + ", ".join(d), "info",
                  tr(f"ψ ×{num(f.get('psi'), 3)}, L ×{num(f.get('L'), 3)}, R ×{num(f.get('R'), 3)}, 전류 축 ×{num(f.get('current_axis'), 4)}",
                     f"ψ ×{num(f.get('psi'), 3)}, L ×{num(f.get('L'), 3)}, R ×{num(f.get('R'), 3)}, current axes "
                     f"×{num(f.get('current_axis'), 4)}") if f else "")
    derived = [r for r in rows if (r.get("lineage") or {}).get("derived")]
    if derived:
        s = ins.section(tr("파생 후보에서 무효가 되는 데이터", "data a derived candidate invalidates"))
        for r in derived:
            s.add(f"<b>{esc(r['candidate'])}</b>: " + "; ".join(esc(engine_text(x)) for x in (r["lineage"].get("invalidated") or [])),
                  "open")
    for r in bad:
        ins.section(tr("거부된 후보", "refused candidates")).add(f"<b>{esc(r['candidate'])}</b>: {esc(engine_text(r['error']))}", "bad")
    notes_section(ins, [res.get("meaning")])
    return ins.nonempty()


def winding_insight(w: dict) -> Insight:
    hs = sorted([h for h in w.get("harmonics") or [] if h.get("nu_el") not in (None, 1.0)], key=lambda h: -(h.get("kw") or 0))
    head = tr(f"{w.get('Q')}슬롯 / {w.get('poles')}극 ({esc(w.get('type', ''))}, q = {num(w.get('q'), 4)}): 기본파 권선계수 kw1 {num(w.get('kw1'), 5)}",
              f"{w.get('Q')} slots / {w.get('poles')} poles ({esc(w.get('type', ''))}, q = {num(w.get('q'), 4)}): fundamental "
              f"winding factor kw1 {num(w.get('kw1'), 5)}")
    ins = Insight(headline=head, verdict="PASS" if w.get("valid") else "FAIL")
    ins.metrics += [("kw1", num(w.get("kw1"), 5), "info"), ("y/τ", num(w.get("y_over_pole_pitch"), 4), "info"),
                    (tr("코깅 지표 LCM(Q, 2p)", "cogging LCM(Q, 2p)"), str(w.get("cogging_lcm_Q_2p")), "info")]
    s = ins.section(tr("권선계수가 말하는 것", "what the winding factors say"))
    s.add(tr(f"코일 피치 y = {w.get('y')} 슬롯 (극 피치의 {num(w.get('y_over_pole_pitch'), 4)}) — 단절권은 기본파를 조금 줄이는 대신 특정 고조파를 크게 줄입니다",
             f"coil pitch y = {w.get('y')} slots ({num(w.get('y_over_pole_pitch'), 4)} of the pole pitch) — short pitching trades a "
             f"little fundamental for large reductions of selected harmonics"), "info")
    slot = {w["Q"] / w["p"] - 1, w["Q"] / w["p"] + 1} if w.get("Q") and w.get("p") else set()
    low = sorted((h for h in hs if (h.get("nu_el") or 0) <= max(slot or {13})), key=lambda h: h.get("nu_el") or 0)
    for h in low:
        is_slot = (h.get("nu_el") in slot)
        s.add(tr(f"{num(h.get('nu_el'))}차 (기계 {h.get('order_mech')}차, {'정방향' if h.get('direction') == 'forward' else '역방향'}): kw "
                 f"{num(h.get('kw'), 4)} = kw1의 {pct(h.get('kw'), w.get('kw1'), 1)}"
                 + (" — 슬롯 고조파 (Q/p ± 1): 피치·분포 계수가 기본파와 같아 이 방법으로는 줄지 않음" if is_slot else ""),
                 f"order {num(h.get('nu_el'))} (mechanical {h.get('order_mech')}, {h.get('direction', '')}): kw {num(h.get('kw'), 4)} = "
                 f"{pct(h.get('kw'), w.get('kw1'), 1)} of kw1"
                 + (" — a slot harmonic (Q/p ± 1): its pitch and distribution factors equal the fundamental's, so pitching "
                    "does not reduce it" if is_slot else "")), "warn" if is_slot else "info")
    subs = w.get("subharmonics") or []
    s.add(tr("서브하모닉 없음" if not subs else "서브하모닉: " + ", ".join(f"기계 {h['order_mech']}차 kw {num(h['kw'], 3)}" for h in subs),
             "no sub-harmonics" if not subs else "sub-harmonics: " + ", ".join(f"mech. {h['order_mech']} kw {num(h['kw'], 3)}" for h in subs)),
          "ok" if not subs else "warn")
    if "N_series" in w:
        s.add(tr(f"직렬 턴 {num(w.get('N_series'))} × kw1 = 유효 턴 {num(w.get('N_eff'), 4)} ({w.get('turns_per_coil')}턴/코일, 병렬 {w.get('parallel_paths')})",
                 f"series turns {num(w.get('N_series'))} × kw1 = effective turns {num(w.get('N_eff'), 4)} ({w.get('turns_per_coil')} "
                 f"turns/coil, {w.get('parallel_paths')} parallel paths)"), "info")
    s = ins.section(tr("일관성 검사", "consistency checks"))
    for c in w.get("consistency") or []:
        s.add(f"{esc(engine_text(c.get('item', '')))}: {esc(engine_text(c.get('detail', '')))}", "ok" if c.get("ok") else "open")
    cmp = w.get("compare")
    if cmp:
        s = ins.section(tr("대안 권선", "alternative winding"))
        if cmp.get("sendable"):
            s.add(tr(f"{cmp.get('turns_per_coil')}턴/코일, 병렬 {cmp.get('parallel_paths')} → 유효 턴 {num(cmp.get('N_eff'), 4)}, k_N = {num(cmp.get('k_turns'), 4)} "
                     f"(ψ ×k_N, L·R ×k_N²)", f"{cmp.get('turns_per_coil')} turns/coil, {cmp.get('parallel_paths')} paths → effective "
                     f"turns {num(cmp.get('N_eff'), 4)}, k_N = {num(cmp.get('k_turns'), 4)} (ψ ×k_N, L and R ×k_N²)"), "info",
                  esc(engine_text(cmp.get("kind", ""))))
        else:
            s.add(tr("k_N을 보낼 수 없음: ", "k_N not handed over: ") + "; ".join(esc(engine_text(x)) for x in cmp.get("refusals") or []), "open")
    notes_section(ins, [w.get("note")])
    return ins.nonempty()


def concept_sizing_insight(cs: dict) -> Insight:
    rows = cs.get("rows") or []
    lim = cs.get("tip_speed_limit_m_s")
    okr = [r for r in rows if r.get("tip_speed_ok", True)]
    head = tr(f"{q(cs.get('T_Nm'), 'N·m')} 개념 크기: 조합 {len(rows)}개 중 팁 속도 한계({q(lim, 'm/s')} @ {q(cs.get('n_max_rpm'), 'rpm')}) 안 {len(okr)}개 "
              f"— 회전자 지름 ≤ {q(cs.get('D_max_tip_mm'), 'mm')}",
              f"{q(cs.get('T_Nm'), 'N·m')} concept size: {len(okr)} of {len(rows)} combinations within the tip-speed limit "
              f"({q(lim, 'm/s')} @ {q(cs.get('n_max_rpm'), 'rpm')}) — rotor diameter ≤ {q(cs.get('D_max_tip_mm'), 'mm')}")
    ins = Insight(headline=head)
    s = ins.section(tr("전단응력 σ와 L/D에 따른 크기", "size over shear stress σ and L/D"),
                    tr("T = 2·σ·V_r (V_r = π/4·D²·L) — σ가 크면 부피가 작아지고, 같은 부피에서 L/D가 크면 지름이 작아져 팁 속도가 낮아집니다.",
                       "T = 2·σ·V_r (V_r = π/4·D²·L) — a higher σ shrinks the volume; at one volume a larger L/D shrinks the "
                       "diameter and the tip speed."))
    for r in rows:
        s.add(tr(f"σ {num(r.get('sigma_kPa'))} kPa, L/D {num(r.get('L_over_D'))}: V_r {q(r.get('rotor_volume_L'), 'L', 3)} → D {q(r.get('D_rotor_mm'), 'mm')}, "
                 f"L {q(r.get('L_stack_mm'), 'mm')}, 팁 속도 {q(r.get('tip_speed_m_s'), 'm/s')}",
                 f"σ {num(r.get('sigma_kPa'))} kPa, L/D {num(r.get('L_over_D'))}: V_r {q(r.get('rotor_volume_L'), 'L', 3)} → D "
                 f"{q(r.get('D_rotor_mm'), 'mm')}, L {q(r.get('L_stack_mm'), 'mm')}, tip speed {q(r.get('tip_speed_m_s'), 'm/s')}"),
              "ok" if r.get("tip_speed_ok", True) else "bad",
              tr(f"검산 T = {q(r.get('check_T_Nm'), 'N·m')}", f"check T = {q(r.get('check_T_Nm'), 'N·m')}") if r.get("check_T_Nm") else "")
    notes_section(ins, [cs.get("meaning"), cs.get("basis")])
    return ins.nonempty()
