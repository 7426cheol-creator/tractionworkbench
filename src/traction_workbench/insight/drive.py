"""Engineering readings of the drive-performance pages: one operating point, sweeps, the T–n envelope and maps,
one-parameter sizing and bottlenecks, and a requirement set.

Sweeps and envelopes are sampled: a transition is reported between the two samples that bracket it, never as an
exact value, and nothing is said about the space between samples beyond what the samples show.
"""

from __future__ import annotations

import numpy as np

from ..i18n import tr
from ..plots.labels import claim_label, constraint_label, param_label, state_label
from . import Insight, esc, kw, num, q, status_level
from .decision import dominance_section, fix_section, mechanism_section, power_section
from .texts import engine_text

STATUS_NAMES = {0: "OK", 1: "DC_LIMIT", 2: "NO_SOLUTION", 3: "UNKNOWN"}


def _arr(v) -> np.ndarray:
    try:
        return np.asarray(v, dtype=float)
    except (TypeError, ValueError):
        return np.full(len(v), np.nan)


def _between(x: np.ndarray, i: int, unit: str) -> str:
    """'a–b unit (between samples)' for a change first seen at sample i."""
    if i <= 0:
        return tr(f"첫 표본 {num(x[0])} {unit}부터", f"from the first sample {num(x[0])} {unit}")
    return tr(f"{num(x[i - 1])}–{num(x[i])} {unit} 사이 (표본 사이)", f"between {num(x[i - 1])} and {num(x[i])} {unit} (samples)")


def _first(mask) -> int | None:
    idx = np.flatnonzero(np.asarray(mask, dtype=bool))
    return int(idx[0]) if idx.size else None


# ---------------------------------------------------------------------- one operating point
def point_insight(op: dict | None, title: str, T_request: float | None = None, forward: dict | None = None,
                  claims: list | None = None) -> Insight:
    """The operating-point page: a policy point for a torque request, or a picked id/iq evaluated forward."""
    if op is None:
        ins = Insight(headline=tr(f"{title}: 운전점 없음 — 이 조건에서 전기적 해가 없거나 모델 영역 밖입니다",
                                  f"{title}: no operating point — no electrical solution or outside the model"),
                      verdict="FAIL" if claims and any(c.get("status") == "INFEASIBLE" for c in claims) else "UNKNOWN")
    else:
        T = op.get("Tshaft_Nm")
        if forward is not None:
            ok = forward.get("accepted")
            head = (tr("정방향 평가 — 모든 제약을 만족하고 모델이 유효하여 근거로 인정되는 점",
                       "forward evaluation — all limits met and the model valid: admissible evidence") if ok else
                    tr("정방향 평가 — 진단값 (가능한 해로 인정되지 않음)", "forward evaluation — a diagnostic, not a witness"))
            verdict = "PASS" if ok else "UNKNOWN"
        else:
            head = tr("최소전류 정책점", "minimum-current policy point")
            verdict = None
        ins = Insight(headline=f"{title}: {head} — T_shaft {q(T, 'N·m')}, |i| {q(op.get('i_peak_A'), 'A')}, "
                               f"P_dc {kw(op.get('Pdc_W'))}", verdict=verdict)
        ins.metrics += [(tr("축 토크", "shaft torque"), q(T, "N·m"), "info"),
                        (tr("상전류 peak", "phase current peak"), q(op.get("i_peak_A"), "A"), "info"),
                        (tr("DC 전력", "DC power"), kw(op.get("Pdc_W")), "info")]
        if op.get("efficiency") is not None:
            ins.metrics.append((tr("효율", "efficiency"), f"{100 * op['efficiency']:.1f} %", "info"))
        mechanism_section(ins, {}, op, {}, T if T is not None else (T_request or 0.0))
        power_section(ins, op)
    if forward is not None and not forward.get("accepted"):
        s = ins.section(tr("진단값인 이유", "why it is a diagnostic"))
        for x in forward.get("issues") or []:
            s.add(esc(engine_text(x)), "open")
        if forward.get("violated"):
            s.add(tr("위반: ", "violated: ") + ", ".join(constraint_label(v) for v in forward["violated"]), "bad")
        if forward.get("not_evaluated"):
            s.add(tr("평가 안 됨: ", "not evaluated: ") + ", ".join(esc(x) for x in forward["not_evaluated"]), "open")
        for x in forward.get("gate_messages") or []:
            s.add(esc(engine_text(x)), "open")
    if claims:
        s = ins.section(tr("판정 항목", "judged items"))
        for c in claims:
            s.add(f"<b>{claim_label(c.get('name', ''))}</b>: {state_label(c.get('status', ''))}", status_level(c.get("status")),
                  esc(engine_text(c.get("detail", ""))))
    return ins.nonempty()


# ---------------------------------------------------------------------- sweeps
def trajectory_insight(sw: dict) -> Insight:
    x = _arr(sw["x"])
    st = np.asarray(sw["status"]).astype(int)
    kind = sw.get("x_kind", "speed")
    unit = "rpm" if kind == "speed" else "N·m"
    va = np.asarray(sw.get("voltage_active", np.zeros_like(x)), dtype=bool)
    ca = np.asarray(sw.get("current_active", np.zeros_like(x)), dtype=bool)
    ok = st == 0
    if kind == "speed":
        what = tr(f"토크 {num(sw.get('T_Nm'))} N·m를 속도에 따라", f"torque {num(sw.get('T_Nm'))} N·m along speed")
    else:
        what = tr(f"속도 {num(sw.get('speed_rpm'))} rpm에서 토크에 따라", f"at {num(sw.get('speed_rpm'))} rpm along torque")
    n_ok = int(ok.sum())
    ins = Insight(headline=tr(f"{what} (Vdc {num(sw.get('Vdc_V'))} V): 표본 {len(x)}개 중 {n_ok}개에서 정책점이 모든 한계를 만족",
                              f"{what} (Vdc {num(sw.get('Vdc_V'))} V): the policy point meets every limit at {n_ok} of "
                              f"{len(x)} samples"),
                  verdict="PASS" if n_ok == len(x) else None)
    note = tr("전이는 그것을 끼고 있는 두 표본 사이에 있습니다. 표본 사이의 연속성은 보장하지 않습니다.",
              "a transition lies between the two samples around it; nothing is claimed between samples.")
    if kind == "speed":
        _transitions(ins.section(tr("한계가 바뀌는 곳 (표본 기준)", "where the limit changes (samples)"), note),
                     x, st, va, ca, unit)
    else:
        # read outward from zero torque: the motoring side upward, the braking side downward
        for side, sel, title in ((1, x >= 0, tr("구동 쪽 (0에서 위로)", "motoring side (up from 0)")),
                                 (-1, x <= 0, tr("제동 쪽 (0에서 아래로)", "braking side (down from 0)"))):
            idx = np.flatnonzero(sel)
            if idx.size < 2:
                continue
            order = idx[np.argsort(side * x[idx])]
            _transitions(ins.section(title, note), x[order], st[order], va[order], ca[order], unit)
    eta = _arr(sw.get("eta", [])) if sw.get("eta") is not None else None
    if eta is not None and eta.size and np.isfinite(np.where(ok, eta, np.nan)).any():
        j = int(np.nanargmax(np.where(ok, eta, np.nan)))
        s2 = ins.section(tr("효율·전력", "efficiency and power"))
        s2.add(tr(f"가능 표본 중 최고 효율 {100 * eta[j]:.1f} % ({num(x[j])} {unit})",
                  f"best efficiency among feasible samples {100 * eta[j]:.1f} % ({num(x[j])} {unit})"), "info")
        pdc = _arr(sw.get("Pdc_W", []))
        if pdc.size and np.isfinite(np.where(ok, pdc, np.nan)).any():
            k = int(np.nanargmax(np.where(ok, np.abs(pdc), np.nan)))
            s2.add(tr(f"가능 표본 중 가장 큰 DC 전력 {kw(pdc[k])} ({num(x[k])} {unit})",
                      f"largest DC power among feasible samples {kw(pdc[k])} ({num(x[k])} {unit})"), "info")
    return ins.nonempty()


def _transitions(s, x, st, va, ca, unit) -> None:
    ok = st == 0
    i = _first(va & ok)
    if i == 0:
        s.add(tr(f"<b>첫 표본({num(x[0])} {unit})부터 이미 전압 한계</b> — 이 구간 전체가 약계자 운전입니다",
                 f"<b>at the voltage limit from the first sample ({num(x[0])} {unit})</b> — the whole range is field "
                 f"weakening"), "info")
    elif i is not None:
        s.add(tr(f"<b>전압 한계 진입 (약계자)</b>: {_between(x, i, unit)}", f"<b>voltage limit reached (field weakening)</b>: "
                 f"{_between(x, i, unit)}"), "info")
    i = _first(ca & ok)
    if i is not None:
        s.add(tr(f"<b>전류 한계 도달</b>: {_between(x, i, unit)}", f"<b>current limit reached</b>: {_between(x, i, unit)}"), "info")
    i = _first(st == 1)
    if i is not None:
        s.add(tr(f"<b>DC 한계 위반 시작</b>: {_between(x, i, unit)} — 전기적으로는 가능하나 소스 한계를 넘음",
                 f"<b>DC limit violated from</b> {_between(x, i, unit)} — electrically possible but beyond the source"),
              "warn")
    i = _first(st == 2)
    if i is not None:
        last = int(np.flatnonzero(ok[:i])[-1]) if ok[:i].any() else None
        s.add(tr(f"<b>전기적 해 없음 (증명)</b>: {_between(x, i, unit)}부터"
                 + (f" — 그 전 마지막 가능 표본 {num(x[last])} {unit}" if last is not None else ""),
                 f"<b>no electrical solution (proven)</b> from {_between(x, i, unit)}"
                 + (f" — last feasible sample before it {num(x[last])} {unit}" if last is not None else "")), "bad")
    if (st == 3).any():
        s.add(tr(f"미확정 표본 {int((st == 3).sum())}개 (모델 영역·수치 경계)", f"{int((st == 3).sum())} undecided samples "
                 f"(model domain / numerical boundary)"), "open")
    if ok.all():
        s.add(tr("모든 표본에서 가능", "feasible at every sample"), "ok")


def envelope_insight(env: dict, compare=None, mp: dict | None = None, note: str | None = None) -> Insight:
    x = _arr(env["x"])
    mx = env["max"]
    T = _arr(mx["T_Nm"])
    P = _arr(mx.get("Pshaft_W", np.full_like(T, np.nan)))
    act = mx.get("active") or [[] for _ in x]
    fin = np.isfinite(T)
    vdc = env.get("Vdc_V")
    if not fin.any():
        return Insight(headline=tr(f"Vdc {num(vdc)} V: 곡선 위에 정책 운전점이 없습니다", f"Vdc {num(vdc)} V: no policy point on "
                                   f"the envelope"), verdict="UNKNOWN")
    it = int(np.nanargmax(T))
    ip = int(np.nanargmax(np.where(fin, P, np.nan)))
    last = int(np.flatnonzero(fin)[-1])
    ins = Insight(headline=tr(f"Vdc {num(vdc)} V 성능 곡선: 최대 토크 {q(T[it], 'N·m')} ({num(x[it])} rpm) · 최대 축출력 "
                              f"{kw(P[ip])} ({num(x[ip])} rpm) · 최고 표본 {num(x[last])} rpm에서 {q(T[last], 'N·m')}",
                              f"Vdc {num(vdc)} V envelope: max torque {q(T[it], 'N·m')} ({num(x[it])} rpm) · max shaft "
                              f"power {kw(P[ip])} ({num(x[ip])} rpm) · {q(T[last], 'N·m')} at the top sample "
                              f"{num(x[last])} rpm"))
    ins.metrics += [(tr("최대 토크", "max torque"), q(T[it], "N·m"), "info"),
                    (tr("최대 축출력", "max shaft power"), f"{kw(P[ip])} @ {num(x[ip])} rpm", "info")]
    iv = next((i for i, a in enumerate(act) if fin[i] and "VOLTAGE" in (a or [])), None)
    if iv is not None:
        ins.metrics.append((tr("전압 한계 진입 (기저속도)", "voltage limit from (base speed)"),
                            f"{num(x[iv - 1]) if iv else '—'}–{num(x[iv])} rpm", "info"))
    s = ins.section(tr("속도 구간별로 무엇이 토크를 정하나", "what sets the torque, speed band by band"),
                    tr("각 속도 표본의 정책 capability에서 활성인 제약입니다 (구간 경계는 표본 사이).",
                       "the constraints active at each speed sample's policy capability (band edges between samples)."))
    start = 0
    for i in range(1, len(x) + 1):
        if i == len(x) or tuple(sorted(act[i] or [])) != tuple(sorted(act[start] or [])) or fin[i] != fin[start]:
            names = ", ".join(constraint_label(n) for n in sorted(act[start] or [])) or (
                tr("해 없음", "no solution") if not fin[start] else "—")
            seg = T[start:i]
            rng = (f"{q(np.nanmax(seg), 'N·m')}" if np.isfinite(seg).any() and np.nanmax(seg) == np.nanmin(seg) else
                   f"{q(np.nanmax(seg), 'N·m')} → {q(np.nanmin(seg), 'N·m')}" if np.isfinite(seg).any() else "—")
            band = f"{num(x[start])} rpm" if i - 1 == start else f"{num(x[start])}–{num(x[i - 1])} rpm"
            s.add(f"{band}: <b>{names}</b> — {tr('최대 토크', 'max torque')} {rng}",
                  "bad" if not fin[start] else "info")
            start = i
    if "VOLTAGE" in " ".join(str(a) for a in act):
        s.add(tr("전압 한계 구간에서는 d축 전류(약계자)로 전압을 맞추므로, 같은 전류로 낼 수 있는 토크가 속도에 따라 줄어듭니다",
                 "in the voltage-limited band the d-axis current (field weakening) holds the voltage, so the torque "
                 "available falls with speed"), "info")
    eta = _arr(mx.get("eta", []))
    if eta.size and np.isfinite(eta).any():
        j = int(np.nanargmax(eta))
        s.add(tr(f"곡선 위 최고 효율 {100 * eta[j]:.1f} % ({num(x[j])} rpm)", f"best efficiency on the envelope "
                 f"{100 * eta[j]:.1f} % ({num(x[j])} rpm)"), "info")
    mn = env.get("min") or {}
    Tm = _arr(mn.get("T_Nm", []))
    if Tm.size and np.isfinite(Tm).any():
        k = int(np.nanargmin(Tm))
        s.add(tr(f"제동(회생) 쪽 최대 제동 토크 {q(-Tm[k], 'N·m')} ({num(x[k])} rpm)", f"braking side: max braking torque "
                 f"{q(-Tm[k], 'N·m')} ({num(x[k])} rpm)"), "info")
    if compare:
        s = ins.section(tr("다른 Vdc와 비교", "other Vdc"))
        for label, e2 in compare:
            T2 = _arr(e2["max"]["T_Nm"])
            x2 = _arr(e2["x"])
            if T2.size and np.isfinite(T2).any():
                l2 = int(np.flatnonzero(np.isfinite(T2))[-1])
                a2 = e2["max"].get("active") or []
                v2 = next((i for i, a in enumerate(a2) if "VOLTAGE" in (a or [])), None)
                s.add(f"{esc(label)}: " + tr(f"최대 토크 {q(np.nanmax(T2), 'N·m')}, 최고 표본 {num(x2[l2])} rpm에서 {q(T2[l2], 'N·m')}"
                                             + (f", 전압 한계 진입 {num(x2[v2])} rpm 부근" if v2 else ""),
                                             f"max torque {q(np.nanmax(T2), 'N·m')}, {q(T2[l2], 'N·m')} at the top sample "
                                             f"{num(x2[l2])} rpm" + (f", voltage limit from about {num(x2[v2])} rpm" if v2 else "")),
                      "info")
    if mp is not None:
        stg = np.asarray(mp["status"])
        tot = stg.size
        s = ins.section(tr("효율·손실 맵 (격자점마다 최소전류 정책점)", "efficiency / loss map (minimum-current policy point per node)"))
        s.add(tr(f"격자 {tot}점: 가능 {int((stg == 0).sum())} · DC 한계 위반 {int((stg == 1).sum())} · 해 없음 "
                 f"{int((stg == 2).sum())} · 미확정 {int((stg == 3).sum())}",
                 f"{tot} nodes: feasible {int((stg == 0).sum())} · DC limit {int((stg == 1).sum())} · no solution "
                 f"{int((stg == 2).sum())} · undecided {int((stg == 3).sum())}"), "info")
        g = mp.get("grids") or {}
        if "eta" in g:
            e = np.where(stg == 0, np.asarray(g["eta"], dtype=float), np.nan)
            if np.isfinite(e).any():
                i, j = np.unravel_index(int(np.nanargmax(e)), e.shape)
                s.add(tr(f"최고 효율 {100 * e[i, j]:.2f} % — {num(mp['speeds'][j])} rpm, {num(mp['torques'][i])} N·m (격자점)",
                         f"best efficiency {100 * e[i, j]:.2f} % — {num(mp['speeds'][j])} rpm, {num(mp['torques'][i])} N·m "
                         f"(node)"), "ok")
                hi = e >= np.nanmax(e) - 0.01
                s.add(tr(f"최고값에서 1 %p 안인 격자점 {int(hi.sum())}개 — 속도 {num(np.min(np.asarray(mp['speeds'])[np.any(hi, 0)]))}–"
                         f"{num(np.max(np.asarray(mp['speeds'])[np.any(hi, 0)]))} rpm",
                         f"{int(hi.sum())} nodes within 1 point of the best — speed "
                         f"{num(np.min(np.asarray(mp['speeds'])[np.any(hi, 0)]))}–"
                         f"{num(np.max(np.asarray(mp['speeds'])[np.any(hi, 0)]))} rpm"), "info")
    s = ins.section(tr("이 결과가 말하지 않는 것", "what this result does not cover"))
    s.add(tr("곡선은 속도 표본마다의 정책 capability입니다 — 표본 사이의 값은 보장하지 않습니다",
             "the envelope is the policy capability per speed sample — nothing is guaranteed between samples"), "open")
    if note:
        s.add(esc(engine_text(note)), "info")
    return ins.nonempty()


# ---------------------------------------------------------------------- design page
def sizing_insight(cv: dict, sz: dict) -> Insight:
    p = sz["parameter"]
    name = param_label(p["parameter"])
    unit = p.get("unit", "")
    T = cv.get("T_request_Nm")
    base = sz.get("baseline_value")
    if sz.get("minimal_feasible_value") is not None:
        head = tr(f"{name}: {q(sz['minimal_feasible_value'], unit)} 이상에서 요구 {q(T, 'N·m')} 가능 (기준 {q(base, unit)})",
                  f"{name}: the request {q(T, 'N·m')} passes from {q(sz['minimal_feasible_value'], unit)} (baseline "
                  f"{q(base, unit)})")
        verdict = "PASS"
    elif sz.get("feasible_ranges"):
        head = tr(f"{name}: 탐색 범위에서 가능한 구간이 있음 (기준 {q(base, unit)})", f"{name}: feasible ranges found "
                  f"(baseline {q(base, unit)})")
        verdict = "PASS"
    else:
        head = tr(f"{name}: 탐색 범위 {num(sz['search_range'][0])}–{num(sz['search_range'][1])} {unit} 전체에서 요구 {q(T, 'N·m')} "
                  f"불가능 — 이 파라미터만으로는 해결 안 됨",
                  f"{name}: the request {q(T, 'N·m')} fails over the whole searched range — this parameter alone does "
                  f"not solve it")
        verdict = "FAIL"
    ins = Insight(headline=head, verdict=verdict)
    fix_section(ins, {"sizing": [sz]}, "FAIL" if verdict != "PASS" else "PASS")
    vals, capv = _arr(cv.get("values", [])), _arr(cv.get("capability_Nm", []))
    if vals.size >= 2 and base is not None and np.isfinite(capv).sum() >= 2:
        k = int(np.argmin(np.abs(vals - base)))
        a, b = (k - 1, k) if k > 0 else (k, k + 1)
        if b < len(vals) and np.isfinite(capv[a]) and np.isfinite(capv[b]) and vals[b] != vals[a]:
            slope = (capv[b] - capv[a]) / (vals[b] - vals[a])
            s = ins.section(tr("기준점 근처 민감도", "sensitivity near the baseline"))
            s.add(tr(f"capability 기울기 ≈ {num(slope, 3)} N·m / {unit or '단위'} (표본 {num(vals[a])} → {num(vals[b])} 사이의 차분)",
                     f"capability slope ≈ {num(slope, 3)} N·m per {unit or 'unit'} (difference between samples "
                     f"{num(vals[a])} → {num(vals[b])})"), "info")
            if np.isfinite(capv[k]):
                s.add(tr(f"기준값 부근 표본 {num(vals[k])} {unit}: capability {q(capv[k], 'N·m')} (요구 {q(T, 'N·m')})",
                         f"sample near the baseline {num(vals[k])} {unit}: capability {q(capv[k], 'N·m')} (request "
                         f"{q(T, 'N·m')})"), "ok" if capv[k] >= abs(T or 0) else "warn")
    s = ins.section(tr("이 결과가 말하지 않는 것", "what this result does not cover"))
    s.add(tr(f"변경 종류: {esc(p.get('change_kind', ''))} — diagnostic 변경은 원인 진단용이며 실현 가능한 설계안이 아닙니다",
             f"change kind: {esc(p.get('change_kind', ''))} — a diagnostic change explains the cause and is not a "
             f"realisable design"), "info")
    for n in sz.get("notes") or []:
        s.add(esc(engine_text(n)), "info")
    return ins.nonempty()


def bottleneck_insight(dom: dict, rel: dict | None, T: float) -> Insight:
    base = dom.get("base_policy_capability_Nm")
    act = ", ".join(constraint_label(n) for n in dom.get("active_constraints_at_base_witness") or []) or "—"
    ok = base is not None and (abs(base) >= abs(T))
    ins = Insight(headline=tr(f"요구 {q(T, 'N·m')}: 기준 capability {q(base, 'N·m')} — {('달성 가능' if ok else '부족')}; "
                              f"기준점에서 활성 {act}",
                              f"request {q(T, 'N·m')}: baseline capability {q(base, 'N·m')} — "
                              f"{('achievable' if ok else 'short')}; active at the base: {act}"),
                  verdict="PASS" if ok else "FAIL" if base is not None else None)
    dominance_section(ins, dom, T)
    if rel:
        fix_section(ins, {"relaxation": rel}, "PASS" if ok else "FAIL")
        s = ins.section(tr("주의", "notes"))
        for n in rel.get("notes") or []:
            s.add(esc(engine_text(n)), "info")
    return ins.nonempty()


# ---------------------------------------------------------------------- requirement set
def requirement_set_insight(st: dict) -> Insight:
    sm = st["summary"]
    rows = st["rows"]
    ins = Insight(headline=tr(f"요구 {sm['total']}건: 만족 {sm['PASS']} · 불만족 {sm['FAIL']} · 미확정 {sm['UNKNOWN']} "
                              f"(모델 {st['drive']['drive_id']}, {st['drive']['origin']} 데이터)",
                              f"{sm['total']} requirements: met {sm['PASS']} · not met {sm['FAIL']} · open {sm['UNKNOWN']} "
                              f"(model {st['drive']['drive_id']}, {st['drive']['origin']} data)"),
                  verdict="FAIL" if sm["FAIL"] else "UNKNOWN" if sm["UNKNOWN"] else "PASS")
    ins.metrics += [(tr("만족", "met"), str(sm["PASS"]), "ok"), (tr("불만족", "not met"), str(sm["FAIL"]), "bad"),
                    (tr("미확정", "open"), str(sm["UNKNOWN"]), "open")]
    passing = [r for r in rows if r["verdict"] == "PASS" and r.get("margin_Nm") is not None]
    if passing:
        r = min(passing, key=lambda r: r["margin_Nm"])
        ins.metrics.append((tr("가장 작은 여유 (만족 중)", "smallest margin (met)"), f"{r['id']}: {num(r['margin_Nm'])} N·m", "warn"))
    s = ins.section(tr("요구별 결론", "per requirement"))
    for r in rows:
        m = r.get("margin_Nm")
        txt = f"<b>{esc(r['id'])}</b> — {esc(r.get('text', ''))}: {state_label(r['verdict'])}"
        if m is not None:
            txt += tr(f", 여유 {num(m)} N·m", f", margin {num(m)} N·m")
        txt += f" · {tr(r['class_label_ko'], r['class_label_en'])}"
        s.add(txt, status_level(r["verdict"]), esc(engine_text(r.get("limiting") or "")))
    counts: dict = {}
    for r in rows:
        for line in r.get("limiting_all") or []:
            for code in ("VOLTAGE", "CURRENT", "DC_DISCHARGE_POWER", "DC_DISCHARGE_CURRENT", "DC_CHARGE_POWER",
                         "DC_CHARGE_CURRENT"):
                if code in line:
                    counts.setdefault(code, set()).add(r["id"])
    if counts:
        s = ins.section(tr("여러 요구에 공통인 한계", "limits shared by several requirements"),
                        tr("한 한계가 여러 요구를 동시에 막으면, 그 한계를 바꾸는 후보가 여러 요구를 함께 바꿉니다 (후보 재판정으로 확인).",
                           "a limit that binds several requirements is one change that moves them together (check with "
                           "the candidates)."))
        for code, ids in sorted(counts.items(), key=lambda kv: -len(kv[1])):
            s.add(f"<b>{constraint_label(code)}</b>: {', '.join(sorted(ids))}", "warn" if len(ids) > 1 else "info")
    prio = st.get("priorities") or []
    if prio:
        s = ins.section(tr("미확정을 풀 다음 자료 (적은 노력 순)", "next data for the open answers (least effort first)"))
        for e in prio:
            s.add(f"<b>{tr(e['label_ko'], e['label_en'])}</b> ({esc(e.get('effort') or '')}): "
                  f"{', '.join(e.get('requirements') or [])} — {esc(engine_text(e.get('hint', '')))}", "open",
                  tr(f"해결 시 결론이 정해지는 요구: {', '.join(e.get('settles') or []) or '—'}",
                     f"settles: {', '.join(e.get('settles') or []) or '—'}"))
    s = ins.section(tr("이 결과가 말하지 않는 것", "what this result does not cover"))
    s.add(tr("모든 판정은 이 모델·데이터에 대한 모델 판정입니다 — 제품 적격성(qualification)은 별도 층입니다",
             "every verdict is a model verdict for this data — product qualification is a separate layer"), "info")
    return ins.nonempty()


def candidates_insight(cands: dict | None) -> list[tuple[str, str]]:
    """(candidate, one-line reading) for the candidates table."""
    out = []
    for c in (cands or {}).get("candidates") or []:
        imp, wor = c.get("improves") or [], c.get("worsens") or []
        txt = (tr(f"개선 {', '.join(imp) or '없음'} · 악화 {', '.join(wor) or '없음'}",
                  f"improves {', '.join(imp) or 'none'} · worsens {', '.join(wor) or 'none'}"))
        if wor and not imp:
            txt += tr(" — 이 후보는 채택할 이유가 없습니다", " — no reason to adopt this candidate")
        elif imp and not wor:
            txt += tr(" — 다른 요구를 해치지 않고 개선합니다", " — improves without hurting another requirement")
        out.append((c.get("name", ""), txt))
    return out

