"""Engineering readings of the efficiency page: the five boundaries at one point, the boundary maps, the mission
energy and the module A/B comparison.

Every number is the result's; the flow and loss splits are identities of the reported port powers."""

from __future__ import annotations

import numpy as np

from ..i18n import tr
from . import Insight, esc, kw, num, pct, q
from .generic import claims_section, notes_section, verdict_of
from .power import die_label
from .texts import engine_text

_BND = {"inverter": ("인버터", "inverter"), "motor": ("모터", "motor"), "inverter_motor": ("인버터+모터", "inverter+motor"),
        "reducer": ("감속기", "reducer"), "edrive": ("eDrive", "eDrive")}
_PORT = {"P_dc": ("DC 단자", "DC terminal"), "P_ac": ("모터 AC 단자", "motor AC terminal"), "P_m": ("모터 축", "motor shaft"),
         "P_o": ("감속기 출력", "reducer output")}
_ITEM = {"semiconductor / declared inverter loss": ("반도체 (인버터 손실)", "semiconductor (inverter loss)"),
         "copper (fundamental, 1.5 Rs |i|^2)": ("동손 (기본파, 1.5·R_s·|i|²)", "copper (fundamental, 1.5·R_s·|i|²)"),
         "rotational / iron (loss-equivalent torque)": ("회전·철손 (손실 등가 토크)", "rotational / iron (loss-equivalent torque)"),
         "PWM harmonic copper": ("PWM 고조파 동손", "PWM harmonic copper"),
         "PWM Fe+PM HF magnetic loss": ("PWM 고주파 철·자석 손실", "PWM Fe+PM HF magnetic loss"),
         "reducer (directional model)": ("감속기 (방향별 모델)", "reducer (directional model)")}
_AUX = {"useful_output_over_all_inputs": ("유용 출력 / 전체 입력 (LV 보조 포함)", "useful output / all inputs (incl. LV auxiliaries)"),
        "hv_recovery_ratio": ("HV 회수 비", "HV recovery ratio"),
        "net_recovery_after_lv_aux": ("LV 보조를 뺀 순 회수 비", "net recovery after LV auxiliaries"),
        "recovered_over_all_inputs": ("회수 / 전체 입력", "recovered / all inputs")}


def aux_label(key: str) -> str:
    """Display name of an auxiliary efficiency metric (the record's key when it has none)."""
    return tr(*_AUX[key]) if key in _AUX else key


_STATUS = {"DEFINED": None, "N/A": ("해당 없음", "N/A"), "UNKNOWN": ("미상", "unknown"), "INCONSISTENT": ("모순 (η > 1)", "inconsistent (η > 1)")}


def bnd_label(k: str) -> str:
    return tr(*_BND[k]) if k in _BND else k


def item_label(name: str) -> str:
    """A ledger item name ('motor: copper (...)' or 'copper (...)') in the reading's language."""
    grp, _, item = name.partition(": ") if ": " in name else ("", "", name)
    lab = tr(*_ITEM[item]) if item in _ITEM else item
    return f"{bnd_label(grp)}: {lab}" if grp and not lab.startswith(bnd_label(grp)) else lab


def _residual(r) -> str:
    return tr("잔차 없음", "no residual") if r is None or abs(r) < 1e-9 else tr(f"잔차 {num(r, 3)}", f"residual {num(r, 3)}")


def _eta(v, digits: int = 2) -> str:
    return "—" if v is None else f"{100.0 * float(v):.{digits}f} %"


def _flow_order(direction: str | None) -> list:
    return ["P_dc", "P_ac", "P_m", "P_o"] if direction != "reverse" else ["P_o", "P_m", "P_ac", "P_dc"]


# ---------------------------------------------------------------------------------------------------- one point
def point_insight(res: dict) -> Insight:
    rq = res.get("request") or {}
    where = f"{q(rq.get('speed_rpm'), 'rpm')} · {q(rq.get('torque_Nm'), 'N·m')} · Vdc {q(rq.get('Vdc_V'), 'V')}"
    claims = res.get("claims") or []
    pol = next((c for c in claims if c.get("name") == "policy_static"), None)
    led = res.get("ledger")
    if not led:
        ins = Insight(headline=f"{where}: " + tr("운전점 없음 — 효율을 정의할 수 없음", "no operating point — no efficiency"),
                      verdict=verdict_of(pol))
        s = ins.section(tr("왜", "why"))
        s.add(esc(engine_text(res.get("reason", ""))), "open")
        claims_section(ins, claims)
        return ins.nonempty()
    B, P = led.get("boundaries") or {}, led.get("ports_W") or {}
    top = next((k for k in ("edrive", "inverter_motor", "inverter", "motor") if (B.get(k) or {}).get("status") == "DEFINED"), None)
    items = [it for it in led.get("loss_items") or [] if it.get("W") is not None and it.get("in_port_powers")]
    known = led.get("loss_known_subtotal_W")
    big = max(items, key=lambda it: it["W"]) if items else None
    if top:
        b = B[top]
        head = tr(f"{where}: η {bnd_label(top)} {_eta(b['eta'])} — 입력 {kw(abs(b['P_in_W']))} → 출력 {kw(abs(b['P_out_W']))}, "
                  f"손실 {kw(b['loss_W'])}", f"{where}: η {bnd_label(top)} {_eta(b['eta'])} — in {kw(abs(b['P_in_W']))} → out "
                  f"{kw(abs(b['P_out_W']))}, loss {kw(b['loss_W'])}")
        if big and known:
            head += tr(f" (가장 큰 손실: {item_label(big['item'])} {q(big['W'], 'W')}, {pct(big['W'], known, 0)})",
                       f" (largest: {item_label(big['item'])} {q(big['W'], 'W')}, {pct(big['W'], known, 0)})")
    else:
        head = f"{where}: " + tr("정의된 경계 효율 없음", "no boundary efficiency defined")
    ins = Insight(headline=head, verdict=verdict_of(pol))
    for k in ("inverter", "motor", "inverter_motor", "reducer", "edrive"):
        b = B.get(k)
        if not b:
            continue
        st = b.get("status")
        ins.metrics.append((f"η {bnd_label(k)}", _eta(b.get("eta")) if st == "DEFINED" else tr(*_STATUS.get(st) or (st, st)),
                            "info" if st in ("DEFINED", "N/A") else ("bad" if st == "INCONSISTENT" else "open")))
    # the power flow through the ports
    direction = (B.get("inverter") or {}).get("direction") or (B.get("edrive") or {}).get("direction")
    order = [p for p in _flow_order(direction) if P.get(p) is not None]
    if len(order) >= 2:
        s = ins.section(tr("전력 흐름 (포트 사이 손실)", "the power flow (loss between the ports)"),
                        tr("구동은 DC → 축 → 출력, 회생은 반대 방향 — 각 경계는 자기 두 포트로 판정합니다.",
                           "motoring flows DC → shaft → output, regeneration the other way — each boundary is judged "
                           "on its own two ports."))
        between = {("P_dc", "P_ac"): "inverter", ("P_ac", "P_m"): "motor", ("P_m", "P_o"): "reducer"}
        parts = [f"{tr(*_PORT[order[0]])} {kw(abs(P[order[0]]))}"]
        for a, c in zip(order, order[1:]):
            k = between.get((a, c)) or between.get((c, a))
            loss = abs(P[a]) - abs(P[c])
            parts.append(f"−{kw(loss)} ({bnd_label(k)}) → {tr(*_PORT[c])} {kw(abs(P[c]))}")
        s.add(" ".join(parts), "info")
        if P.get("P_em") is not None and P.get("P_m") is not None:
            s.add(tr(f"전자기 변환 전력 T_e·ω = {kw(P['P_em'])} ≠ 축 출력 {kw(P['P_m'])}: 차 {q(P['P_em'] - P['P_m'], 'W')} = 회전 손실",
                     f"electromagnetic conversion T_e·ω = {kw(P['P_em'])} ≠ shaft {kw(P['P_m'])}: the difference "
                     f"{q(P['P_em'] - P['P_m'], 'W')} is the rotational loss"), "info")
    # the loss ledger
    s = ins.section(tr("손실 원장 (어디서 나나)", "the loss ledger (where it comes from)"))
    for it in sorted(items, key=lambda it: -it["W"]):
        s.add(f"{item_label(it['boundary'] + ': ' + it['item'])} {q(it['W'], 'W')} ({pct(it['W'], known, 1)})", "info",
              esc(engine_text(it.get("scope", ""))))
    for it in led.get("loss_items") or []:
        if it.get("in_port_powers"):
            continue
        lab = item_label(it["boundary"] + ": " + it["item"])
        if it.get("W") is not None:
            s.add(tr(f"{lab} {q(it['W'], 'W')}" + (f" (하한 {q(it['lower_bound_W'], 'W')})" if it.get("lower_bound_W") is not None else "")
                     + " — 포트 전력 밖, η 구간에만 반영", f"{lab} {q(it['W'], 'W')}"
                     + (f" (lower bound {q(it['lower_bound_W'], 'W')})" if it.get("lower_bound_W") is not None else "")
                     + " — outside the port powers, only in the η interval"), "info", esc(engine_text(it.get("basis", ""))))
        elif it.get("upper_bound_W") is not None:
            s.add(tr(f"{lab} ≤ {q(it['upper_bound_W'], 'W')} (선언 상한, 기대값 아님)", f"{lab} ≤ {q(it['upper_bound_W'], 'W')} "
                     f"(declared upper bound, not an expected value)"), "open", esc(engine_text(it.get("basis", ""))))
        else:
            s.add(tr(f"{lab}: 미상 (0으로 두지 않음)", f"{lab}: unknown (never set to 0)"), "open",
                  esc(engine_text(it.get("basis", ""))))
    lo, hi = led.get("loss_interval_W") or (None, None)
    s.add(tr(f"알려진 소계 {q(known, 'W')}; 미상·상한까지 넣은 손실 구간 [{num(lo)}, {num(hi) if hi is not None else '∞'}] W",
             f"known subtotal {q(known, 'W')}; the loss interval with the open items [{num(lo)}, "
             f"{num(hi) if hi is not None else '∞'}] W"), "info")
    # the boundaries one by one
    s = ins.section(tr("경계별 효율 (각자의 두 포트)", "efficiency per boundary (each on its own two ports)"))
    for k in ("inverter", "motor", "inverter_motor", "reducer", "edrive"):
        b = B.get(k)
        if not b:
            continue
        if b.get("status") == "DEFINED":
            iv = b.get("eta_interval_incl_pwm_hf")
            det = []
            if iv:
                det.append(tr(f"PWM 고조파 손실 포함 η ∈ [{_eta(iv[0])}, {_eta(iv[1])}]", f"incl. PWM harmonic loss η ∈ "
                              f"[{_eta(iv[0])}, {_eta(iv[1])}]"))
            if b.get("qualifier") and not iv:
                det.append(esc(engine_text(b["qualifier"])))
            s.add(tr(f"<b>{bnd_label(k)}</b> {esc(b.get('definition', ''))} = {_eta(b['eta'], 3)} — 손실 {q(b.get('loss_W'), 'W')} "
                     f"({'구동' if b.get('direction') == 'forward' else '회생'})",
                     f"<b>{bnd_label(k)}</b> {esc(b.get('definition', ''))} = {_eta(b['eta'], 3)} — loss "
                     f"{q(b.get('loss_W'), 'W')} ({'motoring' if b.get('direction') == 'forward' else 'regeneration'})"),
                  "info", " · ".join(det))
        else:
            s.add(f"<b>{bnd_label(k)}</b>: {tr(*_STATUS.get(b.get('status')) or (b.get('status'), b.get('status')))}",
                  "bad" if b.get("status") == "INCONSISTENT" else "open", esc(engine_text(b.get("reason", ""))))
    bi, bm, br = B.get("inverter") or {}, B.get("motor") or {}, B.get("reducer") or {}
    res_ = (led.get("boundaries") or {}).get("telescoping_residuals") or {}
    if all(x.get("status") == "DEFINED" for x in (bi, bm)) and (B.get("inverter_motor") or {}).get("status") == "DEFINED":
        s.add(tr(f"같은 점·같은 방향의 곱: η_inv·η_m = {_eta(bi['eta'], 3)} × {_eta(bm['eta'], 3)} = {_eta(bi['eta'] * bm['eta'], 3)} "
                 f"= η 인버터+모터 ({_residual(res_.get('inverter_motor'))})",
                 f"product at one point and direction: η_inv·η_m = {_eta(bi['eta'], 3)} × {_eta(bm['eta'], 3)} = "
                 f"{_eta(bi['eta'] * bm['eta'], 3)} = η inverter+motor ({_residual(res_.get('inverter_motor'))})"), "ok")
        if br.get("status") == "DEFINED" and (B.get("edrive") or {}).get("status") == "DEFINED":
            s.add(tr(f"× η_감속기 {_eta(br['eta'], 3)} = {_eta(bi['eta'] * bm['eta'] * br['eta'], 3)} = η eDrive "
                     f"({_residual(res_.get('edrive'))})", f"× η_reducer {_eta(br['eta'], 3)} = "
                     f"{_eta(bi['eta'] * bm['eta'] * br['eta'], 3)} = η eDrive ({_residual(res_.get('edrive'))})"), "ok")
    sens = led.get("loss_sensitivity") or []
    if sens:
        s = ins.section(tr("어느 손실을 줄이면 효과가 큰가 (각 항 +10 %, 인버터+모터 η)",
                           "which loss matters most (each item +10 %, inverter+motor η)"),
                        tr("같은 비율의 변화가 η를 얼마나 움직이는지 — 큰 항일수록 개선 여지가 큽니다.",
                           "how far the same relative change moves η — the larger, the more there is to gain."))
        for x in sorted(sens, key=lambda x: x.get("delta_eta_points") or 0):
            s.add(tr(f"{item_label(x['item'])}: +{q(x.get('delta_W'), 'W')} → {num(x.get('delta_eta_points'), 3)} %p",
                     f"{item_label(x['item'])}: +{q(x.get('delta_W'), 'W')} → {num(x.get('delta_eta_points'), 3)} pp"), "info")
    hf = led.get("pwm_hf")
    if hf:
        s = ins.section(tr("PWM 고조파 손실 (기본파 밖)", "PWM harmonic loss (outside the fundamental)"))
        iv = hf.get("interval_W") or [None, None]
        s.add(tr(f"모터 고조파 손실 [{num(iv[0])}, {num(iv[1]) if iv[1] is not None else '∞'}] W — 리플 {q(hf.get('ripple_rms_A'), 'A')} rms, "
                 f"L_hf {q((hf.get('L_hf_H') or 0) * 1e6, 'µH')}, fsw {q((hf.get('fsw_requested_Hz') or 0) / 1e3, 'kHz')}",
                 f"motor harmonic loss [{num(iv[0])}, {num(iv[1]) if iv[1] is not None else '∞'}] W — ripple "
                 f"{q(hf.get('ripple_rms_A'), 'A')} rms, L_hf {q((hf.get('L_hf_H') or 0) * 1e6, 'µH')}, fsw "
                 f"{q((hf.get('fsw_requested_Hz') or 0) / 1e3, 'kHz')}"), "info", esc(engine_text(hf.get("basis", ""))))
    red = led.get("reducer") or {}
    ev = red.get("evaluation") or {}
    if ev.get("status") == "DEFINED":
        s = ins.section(tr("감속기", "reducer"))
        mesh, drag, ef = ev.get("mesh_power_W"), ev.get("P_drag_W"), red.get("eta_forward")
        if ev.get("mesh_direction") == "forward" and mesh is not None and drag is not None and isinstance(ef, (int, float)):
            s.add(tr(f"손실 {q(ev.get('loss_W'), 'W')} = 맞물림 (1 − η_f)·P_mesh = (1 − {num(ef, 4)}) × {kw(mesh)} = "
                     f"{q((1 - ef) * mesh, 'W')} + 드래그 {q(drag, 'W')}",
                     f"loss {q(ev.get('loss_W'), 'W')} = mesh (1 − η_f)·P_mesh = (1 − {num(ef, 4)}) × {kw(mesh)} = "
                     f"{q((1 - ef) * mesh, 'W')} + drag {q(drag, 'W')}"), "info", esc(engine_text(red.get("form", ""))))
        else:
            s.add(tr(f"손실 {q(ev.get('loss_W'), 'W')} (드래그 {q(drag, 'W')})", f"loss {q(ev.get('loss_W'), 'W')} (drag "
                     f"{q(drag, 'W')})"), "info", esc(engine_text(red.get("form", ""))))
        s.add(tr(f"출력 토크 {q(ev.get('T_o_Nm'), 'N·m')} (기어비 {num(red.get('ratio'))}) — 출력 경계: {esc(red.get('output_boundary', ''))}",
                 f"output torque {q(ev.get('T_o_Nm'), 'N·m')} (ratio {num(red.get('ratio'))}) — output boundary: "
                 f"{esc(red.get('output_boundary', ''))}"), "info", esc(red.get("basis", "")))
    aux = led.get("aux_metrics") or {}
    sc = led.get("inverter_scope") or {}
    if aux or sc:
        s = ins.section(tr("경계 밖 (η에 들어 있지 않은 것)", "outside the boundaries (not in η)"))
        for k, v in aux.items():
            s.add(f"{esc(aux_label(k))}: {_eta(v, 3)}", "info")
        if sc.get("excluded"):
            s.add(tr("인버터 경계 밖: ", "outside the inverter boundary: ") + ", ".join(esc(engine_text(x)) for x in sc["excluded"]),
                  "open", tr(f"인버터 모델: {esc(sc.get('model', ''))} ({esc(sc.get('technology', ''))}, {esc(sc.get('value_kind', ''))})",
                             f"inverter model: {esc(sc.get('model', ''))} ({esc(sc.get('technology', ''))}, "
                             f"{esc(sc.get('value_kind', ''))})"))
    claims_section(ins, claims)
    notes_section(ins, [res.get("model_efficiency"), led.get("note")])
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- maps
def map_insight(mp: dict) -> Insight:
    sp, tq = np.asarray(mp.get("speeds_rpm") or [], float), np.asarray(mp.get("torques_Nm") or [], float)
    st = np.asarray(mp.get("status") if mp.get("status") is not None else [], dtype=object)
    n_all = st.size
    counts = {k: int(np.sum(st == k)) for k in ("FEASIBLE", "UNKNOWN", "INFEASIBLE")}
    feas = st == "FEASIBLE"
    grids = {k: np.asarray(g, float) for k, g in (mp.get("grids") or {}).items()}

    def best(g, mask):
        m = mask & np.isfinite(g)
        if not m.any():
            return None
        i = np.nanargmax(np.where(m, g, -np.inf))
        r, c = np.unravel_index(i, g.shape)
        return float(g[r, c]), float(sp[c]), float(tq[r])

    def worst(g, mask):
        m = mask & np.isfinite(g)
        if not m.any():
            return None
        i = np.nanargmin(np.where(m, g, np.inf))
        r, c = np.unravel_index(i, g.shape)
        return float(g[r, c]), float(sp[c]), float(tq[r])

    at = lambda b: f"{q(b[1], 'rpm')} · {q(b[2], 'N·m')}"          # noqa: E731
    motoring = feas & (tq[:, None] > 0) if tq.size and st.ndim == 2 else feas
    braking = feas & (tq[:, None] < 0) if tq.size and st.ndim == 2 else feas & False
    ed = grids.get("edrive")
    head_b = best(ed, motoring) if ed is not None else None
    head = tr(f"모델 효율 지도 (Vdc {q(mp.get('Vdc_V'), 'V')}, {len(sp)}×{len(tq)}점): 가능 {counts['FEASIBLE']} · 미확정 {counts['UNKNOWN']} · "
              f"불가능 {counts['INFEASIBLE']}", f"model efficiency maps (Vdc {q(mp.get('Vdc_V'), 'V')}, {len(sp)}×{len(tq)} points): "
              f"feasible {counts['FEASIBLE']} · unknown {counts['UNKNOWN']} · infeasible {counts['INFEASIBLE']}")
    if head_b:
        head += tr(f" — eDrive 구동 최고 {_eta(head_b[0])} @ {at(head_b)}", f" — best eDrive motoring {_eta(head_b[0])} @ {at(head_b)}")
    ins = Insight(headline=head)
    ins.metrics += [(tr("가능한 점", "feasible points"), f"{counts['FEASIBLE']} / {n_all}", "info")]
    if head_b:
        ins.metrics.append((tr("eDrive 구동 최고", "best eDrive motoring"), _eta(head_b[0]), "info"))
    bb = best(ed, braking) if ed is not None else None
    if bb:
        ins.metrics.append((tr("eDrive 회생 최고", "best eDrive regeneration"), _eta(bb[0]), "info"))
    s = ins.section(tr("경계별 최고·최저 (가능한 점만)", "best and worst per boundary (feasible points only)"),
                    tr("서로 다른 점의 최고 효율을 곱하지 않습니다 — 각 값은 그 점에서의 값입니다.",
                       "peak efficiencies of different points are never multiplied — each value holds at its own point."))
    for k in ("inverter", "motor", "inverter_motor", "reducer", "edrive"):
        g = grids.get(k)
        if g is None:
            continue
        for side, mask in ((tr("구동", "motoring"), motoring), (tr("회생", "regeneration"), braking)):
            b, w = best(g, mask), worst(g, mask)
            if b is None:
                continue
            s.add(tr(f"<b>{bnd_label(k)}</b> ({side}): 최고 {_eta(b[0])} @ {at(b)} · 최저 {_eta(w[0])} @ {at(w)}",
                     f"<b>{bnd_label(k)}</b> ({side}): best {_eta(b[0])} @ {at(b)} · worst {_eta(w[0])} @ {at(w)}"), "info")
        n_def = int(np.sum(feas & np.isfinite(g)))
        if n_def < counts["FEASIBLE"]:
            s.add(tr(f"{bnd_label(k)}: 가능한 점 {counts['FEASIBLE']}개 중 {counts['FEASIBLE'] - n_def}개는 η 정의 안 됨 (N/A·미상)",
                     f"{bnd_label(k)}: {counts['FEASIBLE'] - n_def} of {counts['FEASIBLE']} feasible points have no defined η "
                     f"(N/A or unknown)"), "open")
    lk = mp.get("loss_known_W")
    if lk is not None:
        L = np.asarray(lk, float)
        b = best(L, feas)
        if b:
            s = ins.section(tr("손실", "loss"))
            s.add(tr(f"알려진 손실 최대 {q(b[0], 'W')} @ {at(b)}", f"largest known loss {q(b[0], 'W')} @ {at(b)}"), "info")
    notes_section(ins, [mp.get("model_efficiency"), mp.get("meaning")])
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- mission
def mission_insight(res: dict) -> Insight:
    e = res.get("energy") or {}
    segs = res.get("segments") or []
    wh = lambda J: None if J is None else J / 3600.0           # noqa: E731
    out = e.get("output_port", "P_o")
    tra, reg = (e.get("segments") or {}).get("traction") or {}, (e.get("segments") or {}).get("regeneration") or {}
    T = sum(s.get("duration_s") or 0 for s in segs)
    et, er = e.get("eta_traction"), e.get("eta_regeneration")
    head = tr(f"미션 {q(T, 's')}: 구동 η {_eta(et)} (DC {q(wh(tra.get('in')), 'Wh')} → 출력 {q(wh(tra.get('out')), 'Wh')}), 회생 η "
              f"{_eta(er)} (출력 {q(wh(reg.get('in')), 'Wh')} → DC {q(wh(reg.get('out')), 'Wh')})",
              f"mission {q(T, 's')}: traction η {_eta(et)} (DC {q(wh(tra.get('in')), 'Wh')} → output "
              f"{q(wh(tra.get('out')), 'Wh')}), regeneration η {_eta(er)} (output {q(wh(reg.get('in')), 'Wh')} → DC "
              f"{q(wh(reg.get('out')), 'Wh')})")
    if not res.get("delivered"):
        head = tr("일부 구간 미달성 — 에너지 순위를 매기지 않음. ", "not every segment is delivered — no energy ranking. ") + head
    ins = Insight(headline=head, verdict=None if res.get("delivered") else "FAIL")
    loss_t = (tra.get("in") or 0) - (tra.get("out") or 0)
    loss_r = (reg.get("in") or 0) - (reg.get("out") or 0)
    ins.metrics += [(tr("구동 η (에너지 비)", "traction η (energy)"), _eta(et), "info"),
                    (tr("회생 η (에너지 비)", "regeneration η (energy)"), _eta(er), "info"),
                    (tr("손실 에너지", "energy lost"), q(wh(loss_t + loss_r), "Wh"), "info")]
    cls = e.get("segments") or {}
    s = ins.section(tr("시간과 에너지의 분배", "time and energy split"))
    s.add(tr(f"구동 {q((cls.get('traction') or {}).get('t'), 's')} · 회생 {q((cls.get('regeneration') or {}).get('t'), 's')} · "
             f"정지 {q((cls.get('idle') or {}).get('t'), 's')} · 소산 제동 {q((cls.get('dissipative_braking') or {}).get('t'), 's')} · "
             f"미정 {q((cls.get('undetermined') or {}).get('t'), 's')}",
             f"traction {q((cls.get('traction') or {}).get('t'), 's')} · regeneration {q((cls.get('regeneration') or {}).get('t'), 's')}"
             f" · idle {q((cls.get('idle') or {}).get('t'), 's')} · dissipative braking "
             f"{q((cls.get('dissipative_braking') or {}).get('t'), 's')} · undetermined "
             f"{q((cls.get('undetermined') or {}).get('t'), 's')}"), "info")
    s.add(tr(f"손실 에너지: 구동 {q(wh(loss_t), 'Wh')} + 회생 {q(wh(loss_r), 'Wh')}", f"energy lost: traction {q(wh(loss_t), 'Wh')} + "
             f"regeneration {q(wh(loss_r), 'Wh')}"), "info")
    s.add(tr(f"순 DC {q(wh(e.get('E_dc_net_J')), 'Wh')} / 순 출력 {q(wh(e.get('E_out_net_J')), 'Wh')} — 이 비율은 효율이 아님 (구동과 회생이 상쇄)",
             f"net DC {q(wh(e.get('E_dc_net_J')), 'Wh')} / net output {q(wh(e.get('E_out_net_J')), 'Wh')} — this ratio is "
             f"not an efficiency (traction and regeneration cancel)"), "info")
    # where the energy is lost, by boundary (valid when every segment is pure traction, regeneration or idle)
    pos, neg = e.get("E_pos_J") or {}, e.get("E_neg_J") or {}
    clean = not (cls.get("dissipative_braking") or {}).get("t") and not (cls.get("undetermined") or {}).get("t")
    if clean and all(pos.get(k) is not None for k in ("P_dc", "P_ac", "P_m")):
        s = ins.section(tr("경계별 손실 에너지", "energy lost per boundary"),
                        tr("구동은 포트 에너지 E+의 차, 회생은 E−의 차 — 모든 구간이 순수 구동·회생·정지일 때의 항등식입니다.",
                           "traction: differences of the port energies E+, regeneration: of E− — an identity when every "
                           "segment is pure traction, regeneration or idle."))
        bd = e.get("boundary_direction_eta") or {}
        chain = [("inverter", "P_dc", "P_ac"), ("motor", "P_ac", "P_m"), ("reducer", "P_m", "P_o")]
        tot = loss_t + loss_r
        for k, a, c in chain:
            if pos.get(c) is None:
                continue
            lt = pos[a] - pos[c]
            lr = neg[c] - neg[a] if neg.get(a) is not None and neg.get(c) is not None else None
            d = bd.get(k) or {}
            s.add(tr(f"<b>{bnd_label(k)}</b>: 구동 {q(wh(lt), 'Wh')} (η {_eta(d.get('forward'))}) + 회생 {q(wh(lr), 'Wh')} "
                     f"(η {_eta(d.get('reverse'))}) = 전체 손실의 {pct(lt + (lr or 0), tot, 0)}",
                     f"<b>{bnd_label(k)}</b>: traction {q(wh(lt), 'Wh')} (η {_eta(d.get('forward'))}) + regeneration "
                     f"{q(wh(lr), 'Wh')} (η {_eta(d.get('reverse'))}) = {pct(lt + (lr or 0), tot, 0)} of the loss"), "info")
    rows = [x for x in segs if x.get("loss_known_W") is not None and x.get("duration_s")]
    if rows:
        tot_seg = sum(x["loss_known_W"] * x["duration_s"] for x in rows)
        s = ins.section(tr("구간별 손실 에너지 (큰 순)", "loss energy per segment (largest first)"),
                        tr("손실 전력 × 시간 — 오래 머무는 경부하 구간이 효율보다 에너지를 더 좌우할 수 있습니다.",
                           "loss power × time — a long light-load segment can matter more to the energy than its "
                           "efficiency suggests."))
        for x in sorted(rows, key=lambda x: -x["loss_known_W"] * x["duration_s"]):
            pw = x.get("ports_W") or {}
            pin, pout = pw.get("P_dc"), pw.get(out)
            eta = None
            if pin and pout and pin * pout > 0:
                eta = pout / pin if pin > 0 else pin / pout
            Ej = x["loss_known_W"] * x["duration_s"]
            if Ej <= 0:
                continue
            s.add(tr(f"{q(x['duration_s'], 's')} @ {q(x.get('speed_rpm'), 'rpm')} · {q(x.get('torque_Nm'), 'N·m')}: 손실 "
                     f"{q(x['loss_known_W'], 'W')} × {q(x['duration_s'], 's')} = {q(wh(Ej), 'Wh')} ({pct(Ej, tot_seg, 0)})"
                     + (f", eDrive η {_eta(eta)}" if eta is not None else ""),
                     f"{q(x['duration_s'], 's')} @ {q(x.get('speed_rpm'), 'rpm')} · {q(x.get('torque_Nm'), 'N·m')}: loss "
                     f"{q(x['loss_known_W'], 'W')} × {q(x['duration_s'], 's')} = {q(wh(Ej), 'Wh')} ({pct(Ej, tot_seg, 0)})"
                     + (f", eDrive η {_eta(eta)}" if eta is not None else "")), "info")
        bad = [x for x in segs if x.get("status") not in (None, "FEASIBLE")]
        for x in bad:
            s.add(tr(f"{q(x.get('speed_rpm'), 'rpm')} · {q(x.get('torque_Nm'), 'N·m')}: 달성 안 됨 ({esc(x.get('status', ''))})",
                     f"{q(x.get('speed_rpm'), 'rpm')} · {q(x.get('torque_Nm'), 'N·m')}: not delivered "
                     f"({esc(x.get('status', ''))})"), "bad")
    pt = e.get("partial")
    if pt:
        s = ins.section(tr("미상 구간", "undetermined segments"))
        s.add(tr(f"{q(pt.get('undetermined_s'), 's')} 동안 포트 전력 미상 — 미션 방향 효율은 미확정 (알려진 구간만: 구동 "
                 f"{_eta(pt.get('eta_traction_partial'))}, 회생 {_eta(pt.get('eta_regeneration_partial'))})",
                 f"port powers unknown for {q(pt.get('undetermined_s'), 's')} — the mission direction efficiencies are "
                 f"undecided (known segments only: traction {_eta(pt.get('eta_traction_partial'))}, regeneration "
                 f"{_eta(pt.get('eta_regeneration_partial'))})"), "open")
    notes_section(ins, [res.get("model_efficiency"), e.get("note")])
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- module A/B
_AB = {"A_LOWER_LOSS": ("A 손실이 작음", "A has the lower loss"), "B_LOWER_LOSS": ("B 손실이 작음", "B has the lower loss"),
       "UNDECIDED": ("순위 유보", "ranking reserved"), "NOT_COMPARABLE": ("비교 불가", "not comparable")}


def ab_insight(res: dict) -> Insight:
    cands = res.get("candidates") or [{}, {}]
    na, nb = (cands + [{}, {}])[0].get("name", "A"), (cands + [{}, {}])[1].get("name", "B")
    rows = res.get("rows") or []
    verdicts = [(r.get("compare") or {}).get("verdict") for r in rows]
    mode = tr("고정 정책", "fixed policy") if res.get("mode") == "fixed_policy" else tr("설계별 정책", "design-specific policy")
    cond = tr(f"{mode}, fsw {q((res.get('common_fsw_Hz') or 0) / 1e3, 'kHz')}, 냉각수 {q(res.get('coolant_C'), '°C')}",
              f"{mode}, fsw {q((res.get('common_fsw_Hz') or 0) / 1e3, 'kHz')}, coolant {q(res.get('coolant_C'), '°C')}") \
        if res.get("common_fsw_Hz") else mode
    same = len(set(verdicts)) == 1 and verdicts
    if same and verdicts[0] in ("A_LOWER_LOSS", "B_LOWER_LOSS"):
        win = na if verdicts[0] == "A_LOWER_LOSS" else nb
        ds = [abs((r.get("compare") or {}).get("delta") or 0) for r in rows]
        head = tr(f"모듈 A/B ({cond}): {len(rows)}개 운전점 모두 {esc(win)}의 반도체 손실이 작음 — 차 {num(min(ds))}–{num(max(ds))} W, "
                  f"오차 예산 밖", f"module A/B ({cond}): {esc(win)} has the lower semiconductor loss at all {len(rows)} points — "
                  f"{num(min(ds))}–{num(max(ds))} W apart, beyond the error budget")
    else:
        head = tr(f"모듈 A/B ({cond}): ", f"module A/B ({cond}): ") + ", ".join(
            f"{tr(*_AB.get(v, (v, v)))} {verdicts.count(v)}" for v in dict.fromkeys(verdicts))
    ins = Insight(headline=head)
    rel = [(r.get("compare") or {}).get("relative_change") for r in rows]
    rel = [x for x in rel if x is not None]
    if rel:
        ins.metrics.append((tr("B − A 반도체 손실", "B − A semiconductor loss"),
                            f"{pct(min(rel), 1, 0)} … {pct(max(rel), 1, 0)}" if len(rel) > 1 else pct(rel[0], 1, 0), "info"))
    mc0 = (res.get("mission") or {}).get("compare") or {}
    if mc0.get("loss_A") is not None:
        ins.metrics.append((tr("미션 인버터 손실 A → B", "mission inverter loss A → B"),
                            f"{num(mc0['loss_A'] / 3600)} → {num(mc0['loss_B'] / 3600)} Wh", "info"))
    s = ins.section(tr("후보", "candidates"))
    for tag, c in zip(("A", "B"), cands):
        tech = esc(str(c.get("technology", "")).replace("_", " "))
        s.add(tr(f"<b>{tag} {esc(c.get('name', ''))}</b>: {tech} ({esc(c.get('value_kind', ''))}), "
                 f"데드타임 {q((c.get('deadtime_s') or 0) * 1e6, 'µs', 3)}, R_th {q(c.get('Rth_K_per_W'), 'K/W', 3)}, 오차 예산 "
                 + (f"{num(100 * c['loss_error_rel'], 3)} %" if c.get("loss_error_rel") is not None else "없음"),
                 f"<b>{tag} {esc(c.get('name', ''))}</b>: {tech} ({esc(c.get('value_kind', ''))}), "
                 f"dead time {q((c.get('deadtime_s') or 0) * 1e6, 'µs', 3)}, R_th {q(c.get('Rth_K_per_W'), 'K/W', 3)}, error "
                 f"budget " + (f"{num(100 * c['loss_error_rel'], 3)} %" if c.get("loss_error_rel") is not None else "none")),
              "info", esc(c.get("error_basis", "")))
    for r in rows:
        cv = r.get("compare") or {}
        A, Bc = r.get("A") or {}, r.get("B") or {}
        where = f"{q(r.get('speed_rpm'), 'rpm')} · {q(r.get('torque_Nm'), 'N·m')} · {q(r.get('Vdc_V'), 'V')}"
        s = ins.section(where)
        v = cv.get("verdict")
        lvl = {"A_LOWER_LOSS": "ok", "B_LOWER_LOSS": "ok", "UNDECIDED": "open", "NOT_COMPARABLE": "bad"}.get(v, "info")
        if cv.get("loss_A") is not None:
            s.add(tr(f"<b>{tr(*_AB.get(v, (v, v)))}</b>: A {q(cv['loss_A'], 'W')} vs B {q(cv['loss_B'], 'W')} → Δ {q(cv.get('delta'), 'W')} "
                     f"({pct(cv.get('delta'), cv['loss_A'], 0)})" + (f", 오차 예산 ±{q(cv.get('band'), 'W')}" if cv.get("band") is not None else ""),
                     f"<b>{tr(*_AB.get(v, (v, v)))}</b>: A {q(cv['loss_A'], 'W')} vs B {q(cv['loss_B'], 'W')} → Δ "
                     f"{q(cv.get('delta'), 'W')} ({pct(cv.get('delta'), cv['loss_A'], 0)})"
                     + (f", error budget ±{q(cv.get('band'), 'W')}" if cv.get("band") is not None else "")), lvl,
                  esc(engine_text(cv.get("reason", ""))))
        else:
            s.add(f"<b>{tr(*_AB.get(v, (v, v)))}</b>", lvl, esc(engine_text(cv.get("reason", ""))))
        da, db = A.get("detail") or {}, Bc.get("detail") or {}
        if da.get("established") and db.get("established"):
            dc = (db.get("conduction_W") or 0) - (da.get("conduction_W") or 0)
            dsw = (db.get("switching_W") or 0) - (da.get("switching_W") or 0)
            s.add(tr(f"차이의 구성: 도통 {q(dc, 'W')} (A {q(da.get('conduction_W'), 'W')} → B {q(db.get('conduction_W'), 'W')}), 스위칭 "
                     f"{q(dsw, 'W')} (A {q(da.get('switching_W'), 'W')} → B {q(db.get('switching_W'), 'W')})",
                     f"what the difference is made of: conduction {q(dc, 'W')} (A {q(da.get('conduction_W'), 'W')} → B "
                     f"{q(db.get('conduction_W'), 'W')}), switching {q(dsw, 'W')} (A {q(da.get('switching_W'), 'W')} → B "
                     f"{q(db.get('switching_W'), 'W')})"), "info")
        if A.get("Tj_C") is not None or Bc.get("Tj_C") is not None:
            s.add(tr(f"T_j (결과, 강제하지 않음): A {num(A.get('Tj_C'))} °C ({die_label(da.get('hottest_position', ''))} "
                     f"{q(A.get('P_hot_W'), 'W')}) · B {num(Bc.get('Tj_C'))} °C ({die_label(db.get('hottest_position', ''))} "
                     f"{q(Bc.get('P_hot_W'), 'W')})",
                     f"T_j (a result, not forced): A {num(A.get('Tj_C'))} °C ({die_label(da.get('hottest_position', ''))} "
                     f"{q(A.get('P_hot_W'), 'W')}) · B {num(Bc.get('Tj_C'))} °C ({die_label(db.get('hottest_position', ''))} "
                     f"{q(Bc.get('P_hot_W'), 'W')})"), "info")
        de = r.get("delta_eta_pp") or {}
        if any(v is not None for v in de.values()):
            s.add(tr("η 변화 (B − A): ", "η change (B − A): ") + ", ".join(
                f"{bnd_label(k)} {'+' if (v or 0) >= 0 else ''}{num(v, 3)} %p" for k, v in de.items() if v is not None), "info")
        for tag, x in (("A", A), ("B", Bc)):
            if x.get("status") not in (None, "FEASIBLE"):
                s.add(tr(f"{tag}: 요구 미달성 ({esc(x.get('status', ''))})", f"{tag}: requirement not delivered "
                         f"({esc(x.get('status', ''))})"), "bad", esc(engine_text(x.get("reason", ""))))
    mi = res.get("mission") or {}
    mc = mi.get("compare")
    if mc:
        s = ins.section(tr("미션 (인버터 손실 에너지)", "mission (inverter loss energy)"))
        v = mc.get("verdict")
        if mc.get("loss_A") is not None:
            s.add(tr(f"<b>{tr(*_AB.get(v, (v, v)))}</b>: A {q(mc['loss_A'] / 3600, 'Wh')} vs B {q(mc['loss_B'] / 3600, 'Wh')} → Δ "
                     f"{q((mc.get('delta') or 0) / 3600, 'Wh')} ({pct(mc.get('delta'), mc['loss_A'], 0)})",
                     f"<b>{tr(*_AB.get(v, (v, v)))}</b>: A {q(mc['loss_A'] / 3600, 'Wh')} vs B {q(mc['loss_B'] / 3600, 'Wh')} → Δ "
                     f"{q((mc.get('delta') or 0) / 3600, 'Wh')} ({pct(mc.get('delta'), mc['loss_A'], 0)})"),
                  "ok" if v in ("A_LOWER_LOSS", "B_LOWER_LOSS") else "open", esc(engine_text(mc.get("reason", ""))))
        else:
            s.add(f"<b>{tr(*_AB.get(v, (v, v)))}</b>", "bad", esc(engine_text(mc.get("reason", ""))))
        for name, pc in (mi.get("per_candidate") or {}).items():
            en = pc.get("energy") or {}
            s.add(tr(f"{esc(name)}: 구동 η {_eta(en.get('eta_traction'))}, 회생 η {_eta(en.get('eta_regeneration'))}",
                     f"{esc(name)}: traction η {_eta(en.get('eta_traction'))}, regeneration η "
                     f"{_eta(en.get('eta_regeneration'))}"), "info")
    s = ins.section(tr("이 비교가 말하지 않는 것", "what this comparison does not say"))
    s.add(esc(engine_text(res.get("ranking_scope", ""))), "info")
    for x in res.get("not_evaluated") or []:
        s.add(esc(engine_text(x)), "open")
    s.add(esc(engine_text(res.get("meaning", ""))), "info")
    if res.get("model_efficiency"):
        s.add(esc(engine_text(res["model_efficiency"])), "info")
    return ins.nonempty()
