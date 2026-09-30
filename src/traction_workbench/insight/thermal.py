"""Engineering readings of the thermal page: torque availability over a duration, and a repeated load."""

from __future__ import annotations

import math

from ..i18n import tr
from . import Insight, esc, kw, num, pct, q
from .generic import claim_item, notes_section, verdict_of
from .texts import engine_parts, engine_text


def _t(v) -> float:
    if v in (None, "Infinity", "inf"):
        return math.inf
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.inf


def thermal_insight(out: dict, ask: dict) -> Insight:
    """``out``: the thermal page result (``request`` + ``availability``); ``ask``: speed_rpm, Vdc_V, torque_Nm,
    duration_s of the request."""
    rq, av = out["request"], out["availability"]
    claim = rq["claim"]
    T, n, dur = ask.get("torque_Nm"), ask.get("speed_rpm"), ask.get("duration_s")
    nodes = rq.get("nodes") or []
    tfirst = _t(rq.get("time_to_first_limit_s"))
    gov = min(nodes, key=lambda nd: _t(nd.get("time_to_limit_s"))) if nodes else None
    validated = bool(av.get("validated"))
    screen = "" if validated else tr(" (미검증 열모델 — 스크리닝)", " (unvalidated thermal model — screening)")
    what = tr(f"{num(T)} N·m @ {num(n)} rpm, {num(dur)} s", f"{num(T)} N·m @ {num(n)} rpm for {num(dur)} s")
    if gov is not None and math.isfinite(tfirst) and dur is not None and tfirst < dur:
        head = tr(f"{what}: <b>요구 시간 안에 온도 한계 도달</b> — {esc(gov['node'])}가 {num(tfirst, 3)} s에 {num(gov['limit_C'])} °C "
                  f"(요구의 {pct(tfirst, dur, 0)}){screen}",
                  f"{what}: <b>reaches a temperature limit within the request</b> — {esc(gov['node'])} at "
                  f"{num(tfirst, 3)} s to {num(gov['limit_C'])} °C ({pct(tfirst, dur, 0)} of the request){screen}")
    elif gov is not None and math.isfinite(tfirst):
        head = tr(f"{what}: 요구 시간 안에는 한계 이내 — 첫 한계는 {num(tfirst, 3)} s ({esc(gov['node'])}){screen}",
                  f"{what}: within the limits for the requested time — first limit at {num(tfirst, 3)} s "
                  f"({esc(gov['node'])}){screen}")
    else:
        head = tr(f"{what}: 정상상태까지 모든 노드가 한계 이내{screen}", f"{what}: every node stays within its limit up to "
                  f"steady state{screen}")
    ins = Insight(headline=head, verdict=verdict_of(claim))
    if math.isfinite(tfirst):
        ins.metrics.append((tr("첫 한계 도달", "first limit"), f"{num(tfirst, 3)} s", "warn" if dur and tfirst < dur else "ok"))
    if gov is not None:
        ins.metrics.append((tr("지배 노드", "governing node"), esc(gov["node"]), "info"))
    row = _row_near(av.get("rows") or [], dur)
    if row is not None:
        ins.metrics.append((tr(f"{num(row['duration_s'], 3)} s 가용 토크", f"torque for {num(row['duration_s'], 3)} s"),
                            q(row.get("torque_Nm"), "N·m"), "info"))
    s = ins.section(tr("노드별 온도 — 무엇이 먼저 한계에 닿나", "per node — what reaches its limit first"))
    for nd in sorted(nodes, key=lambda nd: _t(nd.get("time_to_limit_s"))):
        tl = _t(nd.get("time_to_limit_s"))
        ss, lim = nd.get("steady_state_C"), nd.get("limit_C")
        cont = (tr(f"정상상태 {num(ss)} °C > 한계 {num(lim)} °C → 이 부하로 연속 운전하면 이 노드가 한계를 넘음",
                   f"steady state {num(ss)} °C > limit {num(lim)} °C → continuous operation at this load exceeds it")
                if ss is not None and lim is not None and ss > lim else
                tr(f"정상상태 {num(ss)} °C ≤ 한계 {num(lim)} °C → 이 노드는 연속 운전해도 한계 이내",
                   f"steady state {num(ss)} °C ≤ limit {num(lim)} °C → this node stays within its limit continuously"))
        txt = tr(f"<b>{esc(nd['node'])}</b>: 발열 {q(nd.get('power_W'), 'W')}, {num(dur)} s 후 {num(nd.get('temperature_at_duration_C'))} °C "
                 f"/ 한계 {num(lim)} °C, 한계 도달 {('없음' if not math.isfinite(tl) else f'{num(tl, 3)} s')}",
                 f"<b>{esc(nd['node'])}</b>: heat {q(nd.get('power_W'), 'W')}, {num(nd.get('temperature_at_duration_C'))} °C "
                 f"after {num(dur)} s of {num(lim)} °C, limit reached {('never' if not math.isfinite(tl) else f'at {num(tl, 3)} s')}")
        lvl = "bad" if math.isfinite(tl) and dur and tl < dur else "warn" if ss is not None and lim is not None and ss > lim else "ok"
        s.add(txt, lvl, cont)
    op = rq.get("operating_point") or {}
    if op.get("losses_W"):
        L = op["losses_W"]
        tot = sum(v for v in L.values() if v)
        names = {"inverter": tr("인버터", "inverter"), "copper": tr("동손", "copper"), "rotational": tr("회전손", "rotational"),
                 "module": tr("모듈", "module")}
        s.add(tr("운전점 손실 (열원): ", "losses at the point (heat sources): ")
              + ", ".join(f"{names.get(k, k)} {q(v, 'W')} ({pct(v, tot, 0)})" for k, v in L.items() if v), "info",
              tr(f"id = {q(op.get('id_A'), 'A')}, iq = {q(op.get('iq_A'), 'A')} (최소전류 정책점)",
                 f"id = {q(op.get('id_A'), 'A')}, iq = {q(op.get('iq_A'), 'A')} (minimum-current policy point)"))
    rows = av.get("rows") or []
    if rows:
        s = ins.section(tr("지속시간별 가용 토크", "torque available per duration"),
                        tr("각 지속시간에서 한계를 넘지 않는 최대 토크 (정적 capability 이하, 표본 스캔 + 경계 이분법).",
                           "the largest torque that stays within the limits for each duration (below the static "
                           "capability; sampled scan + bisected boundaries)."))
        pick = [rows[0]] + ([row] if row is not None and row is not rows[0] and row is not rows[-1] else []) + [rows[-1]]
        for r in pick:
            d = _t(r["duration_s"])
            when = tr("연속", "continuous") if not math.isfinite(d) else f"{num(d, 3)} s"
            s.add(tr(f"{when}: {q(r.get('torque_Nm'), 'N·m')} (제한: {esc(engine_text(r.get('limited_by', '')))})",
                     f"{when}: {q(r.get('torque_Nm'), 'N·m')} (limited by {esc(r.get('limited_by', ''))})"), "info")
        if av.get("static_capability_Nm") is not None:
            s.add(tr(f"정적 capability {q(av['static_capability_Nm'], 'N·m')} — 짧은 지속시간의 상한은 열이 아니라 전기적 한계",
                     f"static capability {q(av['static_capability_Nm'], 'N·m')} — the short-duration ceiling is electrical, "
                     f"not thermal"), "info")
    fl = ((rq.get("coolant") or {}).get("fluid")) or {}
    if fl:
        s = ins.section(tr("냉각수", "coolant"))
        cool = rq.get("coolant") or {}
        s.add(tr(f"유량 {num(cool.get('flow_L_per_min'))} L/min, 열용량률 ṁ·c_p = {q(cool.get('capacity_rate_W_per_K'), 'W/K')}",
                 f"flow {num(cool.get('flow_L_per_min'))} L/min, capacity rate ṁ·c_p = {q(cool.get('capacity_rate_W_per_K'), 'W/K')}"),
              "info")
        for st, f in ((k, v) for k, v in fl.items() if not k.startswith("_")):
            s.add(tr(f"{esc(st)}: {kw(f.get('P_W'))} → 냉각수 {num(f.get('T_in_C'))} → {num(f.get('T_out_C'))} °C (상승 {num(f.get('rise_K'), 3)} K)",
                     f"{esc(st)}: {kw(f.get('P_W'))} → coolant {num(f.get('T_in_C'))} → {num(f.get('T_out_C'))} °C (rise "
                     f"{num(f.get('rise_K'), 3)} K)"), "info")
    s = ins.section(tr("판정", "claim"))
    claim_item(s, claim)
    notes_section(ins, rq.get("qualification_problems"), av.get("assumptions"),
                  [av.get("status_note", "")] if not validated else [])
    return ins.nonempty()


def _row_near(rows: list, dur) -> dict | None:
    if not rows or dur is None:
        return None
    return min(rows, key=lambda r: abs(math.log(max(float(r["duration_s"]), 1e-9) / max(float(dur), 1e-9))))


def cycle_insight(res: dict) -> Insight:
    claim = res["claim"]
    per = res.get("periodic") or {}
    fl = res.get("first_limit")
    al = res.get("allowed") or {}
    gov = per.get("governing_node")
    if fl:
        head = tr(f"반복 부하: <b>{fl['cycle']}번째 주기에서 한계 도달</b> — {esc(fl['node'])} ({num(fl['t_s'], 3)} s)",
                  f"repeated load: <b>a limit is reached in cycle {fl['cycle']}</b> — {esc(fl['node'])} ({num(fl['t_s'], 3)} s)")
    else:
        n_req = res.get("cycles_requested") or res.get("cycles_run")
        head = tr(f"반복 부하: 요청한 {n_req}주기 동안 한계 도달 없음", f"repeated load: no limit in the {n_req} requested cycles")
        if per.get("exceeds") and gov:
            over = -per["margin_K"][gov]
            head += tr(f" — 단, 계속 반복하면 {esc(gov)}가 주기 정상상태에서 한계를 {num(over, 2)} K 넘음",
                       f" — but repeated indefinitely {esc(gov)} exceeds its limit by {num(over, 2)} K in the periodic "
                       f"cycle")
    ins = Insight(headline=head, verdict=verdict_of(claim))
    if per.get("peak_C") and gov:
        ins.metrics.append((tr("주기 정상상태 최고온도", "periodic peak"), f"{num(per['peak_C'][gov])} °C", "info"))
        ins.metrics.append((tr("여유 (지배 노드)", "margin (governing node)"), f"{num(per['margin_K'][gov], 3)} K",
                            "bad" if per["margin_K"][gov] < 0 else "ok"))
    if al.get("pulse_duration_s") is not None:
        ins.metrics.append((tr("허용 펄스 시간", "allowed pulse time"), f"{num(_t(al['pulse_duration_s']), 3)} s", "info"))
    if per:
        s = ins.section(tr("주기 정상상태 (반복을 계속하면 수렴하는 온도)", "periodic steady state (where repetition settles)"))
        for node, pk in (per.get("peak_C") or {}).items():
            m = (per.get("margin_K") or {}).get(node)
            s.add(tr(f"<b>{esc(node)}</b>: 최고 {num(pk)} °C, 여유 {num(m, 3)} K", f"<b>{esc(node)}</b>: peak {num(pk)} °C, margin "
                     f"{num(m, 3)} K"), "bad" if m is not None and m < 0 else "ok")
        if not per.get("reached"):
            s.add(esc(engine_text(per.get("note", ""))) or tr("고정점 미수렴", "fixed point not reached"), "open")
    if al:
        s = ins.section(tr("허용값 — 한계에 맞추려면", "allowed values — to stay within the limits"),
                        esc(engine_text(al.get("basis", ""))))
        if al.get("pulse_duration_s") is not None:
            s.add(tr(f"같은 토크라면 펄스를 {num(_t(al['pulse_duration_s']), 3)} s 이하로", f"at this torque, pulses of at most "
                     f"{num(_t(al['pulse_duration_s']), 3)} s"), "info")
        if al.get("pulse_torque_Nm") is not None:
            s.add(tr(f"같은 펄스 시간이라면 토크를 {q(_t(al['pulse_torque_Nm']), 'N·m')} 이하로 (주기 정상상태)",
                     f"at this pulse time, a torque of at most {q(_t(al['pulse_torque_Nm']), 'N·m')} (periodic)"), "info",
                  esc(engine_text(al.get("pulse_torque_note", ""))))
        if al.get("first_pulse_torque_Nm") is not None:
            s.add(tr(f"첫 펄스만이라면 {q(_t(al['first_pulse_torque_Nm']), 'N·m')} 이하", f"the first pulse alone: at most "
                     f"{q(_t(al['first_pulse_torque_Nm']), 'N·m')}"), "info")
        lb = al.get("loss_bound") or {}
        if lb and not lb.get("is_bound", True):
            v = (lb.get("violations") or [{}])[0]
            s.add(tr("온도 범위 안에서 손실이 모서리 값보다 큰 점이 있어, 위 허용값은 <b>추정값</b>입니다 (상한 아님)",
                     "a loss exceeds its corner values inside the temperature range: the allowed values above are "
                     "<b>estimates</b>, not bounds"), "open",
                  esc(engine_text(lb.get("check", ""))) + (f" — {esc(str(v.get('phase')))}: {esc(str(v.get('loss')))} "
                                                           f"{num(v.get('W'))} W > {num(v.get('corner_max_W'))} W"
                                                           if v else ""))
        elif lb and lb.get("corners", 0) > 1:
            s.add(tr(f"손실은 온도 상자 모서리 {lb['corners']}곳의 최댓값으로 잡았고, 내부 표본 {lb.get('interior_samples')}점에서 "
                     f"그보다 큰 손실이 없음을 확인했습니다 (표본 확인, 증명 아님)",
                     f"losses taken at the largest of the {lb['corners']} temperature-box corners; no larger loss at "
                     f"{lb.get('interior_samples')} interior samples (a sampled check, not a proof)"), "info")
        for k, lab_ko, lab_en in (("rest_before_repeat", "첫 펄스 뒤 반복 전 필요한 휴지", "rest needed before repeating"),
                                  ("periodic_min_rest", "주기 유지에 필요한 최소 휴지", "shortest rest for the periodic cycle")):
            v, note = al.get(k + "_s"), al.get(k + "_note")
            if v is not None or note:
                val = (f"{num(_t(v), 3)} s" if v is not None and math.isfinite(_t(v)) else "∞" if v is not None else "")
                s.add(f"{tr(lab_ko, lab_en)}: " + " — ".join(x for x in (val, esc(engine_text(note or ""))) if x), "info")
    rc = res.get("resolution_check")
    if rc:
        s = ins.section(tr("수치 해상도 점검", "numerical resolution check"),
                        tr(f"같은 계산을 스텝 {rc['steps_per_phase'][1]}개/구간, 손실 캐시 {num(rc['cache_K'][1])} K로 다시 해 "
                           f"비교했습니다", f"the same run at {rc['steps_per_phase'][1]} steps per phase and a "
                           f"{num(rc['cache_K'][1])} K loss cache, compared"))
        s.add(tr("판정이 해상도에 따라 바뀌지 않음", "no decision changes with the resolution") if rc["stable"] else
              tr("<b>판정이 해상도에 따라 바뀜</b> — 이 결과는 판정으로 쓰지 않습니다",
                 "<b>the verdict changes with the resolution</b> — not used as a verdict"),
              "ok" if rc["stable"] else "open", esc(engine_parts(rc.get("note", ""))))
    fb = res.get("feedback") or {}
    if fb:
        s = ins.section(tr("손실–온도 피드백", "loss–temperature feedback"))
        s.add(tr(f"R_s(T): {'반영' if fb.get('rs') else '미반영'} ({esc(fb.get('winding_node', ''))}) · 모듈 T_j: "
                 f"{'반영' if fb.get('module') else '미반영'} ({esc(fb.get('junction_node', ''))}) · 자석 온도 → 자속: "
                 f"{'반영' if fb.get('magnet') else '미반영'} ({esc(fb.get('magnet_node') or '선언된 노드 없음')})",
                 f"R_s(T): {'on' if fb.get('rs') else 'off'} ({esc(fb.get('winding_node', ''))}) · module T_j: "
                 f"{'on' if fb.get('module') else 'off'} ({esc(fb.get('junction_node', ''))}) · magnet temperature → "
                 f"flux: {'on' if fb.get('magnet') else 'off'} ({esc(fb.get('magnet_node') or 'no declared node')})"),
              "info")
        for n in fb.get("notes") or []:
            s.add(esc(engine_text(n)), "info")
    s = ins.section(tr("판정", "claim"))
    claim_item(s, claim)
    notes_section(ins, res.get("qualification_problems"), res.get("assumptions"))
    return ins.nonempty()
