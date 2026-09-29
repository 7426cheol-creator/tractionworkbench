"""Engineering readings of the PWM / driveline page: variable-PWM policies, the current-loop timing over fsw, the
ripple over fsw, the carrier-change transients and current sampling, the anti-jerk variants and their stability.

Every number is the result's; margins are against the limits the user declared (no new threshold)."""

from __future__ import annotations

import math

from ..i18n import tr
from . import Insight, esc, num, pct, q
from .generic import notes_section
from .texts import engine_text


def _kj(J, sig: int = 4) -> str:
    return "—" if J is None else f"{num(J / 1e3, sig)} kJ"


def _z(v, tol: float):
    """Numerical noise below ``tol`` shown as 0 (e.g. a 1e-15 A excursion of an exact bumpless change)."""
    return 0.0 if v is not None and abs(v) < tol else v


def _us(s, sig: int = 4) -> str:
    return "—" if s is None else f"{num(_z(s, 1e-12) * 1e6, sig)} µs"


def _ms(s, sig: int = 4) -> str:
    return "—" if s is None else f"{num(_z(s, 1e-12) * 1e3, sig)} ms"


def _u(v, unit: str, sig: int = 4) -> str:
    """A value with its unit: no space before the degree sign, none for a dimensionless ratio."""
    return f"{num(v, sig)}{unit}" if unit in ("°", "") else f"{num(v, sig)} {unit}"


_MEAS = {"sensor_temp_C": ("센서 온도 [°C]", "sensor temperature [°C]"), "torque_abs_Nm": ("|토크| [N·m]", "|torque| [N·m]"),
         "speed_rpm": ("속도 [rpm]", "speed [rpm]"), "Vdc_V": ("Vdc [V]", "Vdc [V]")}


# declared limit -> (policy field, label, upper limit?)
_LIMITS = (("Tj_max_C", "Tj_max_C", ("모듈 T_j", "module T_j"), "°C", True),
           ("i_peak_incl_ripple_max_A", "i_peak_bound_max_A", ("피크 전류 보수 상한", "peak-current bound"), "A", True),
           ("cap_rms_max_A", "I_cap_rms_max_A", ("커패시터 RMS 전류", "capacitor RMS current"), "A", True),
           ("phase_margin_min_deg", "phase_margin_min_deg", ("전류 루프 위상 여유", "current-loop phase margin"), "°", False),
           ("pulse_ratio_min", "pulse_ratio_min", ("펄스 비 (fsw/f_e)", "pulse ratio (fsw/f_e)"), "", False))


def _margins(p: dict, limits: dict) -> list:
    """(label, value, limit, unit, margin, relative margin) of each declared limit the policy reports."""
    out = []
    for lk, pk, lab, unit, upper in _LIMITS:
        lim, val = limits.get(lk), p.get(pk)
        if lim is None or val is None:
            continue
        m = (lim - val) if upper else (val - lim)
        out.append((tr(*lab), val, lim, unit, m, m / abs(lim) if lim else None, upper))
    return out


# ---------------------------------------------------------------------------------------------------- policies
def pwm_policies_insight(res: dict) -> Insight:
    pols = res.get("policies") or []
    lim = res.get("limits") or {}
    adm = [p for p in pols if p.get("admissible")]
    bad = [p for p in pols if not p.get("admissible")]
    best = res.get("best_inverter_energy_among_evaluated")
    bp = next((p for p in pols if p["policy"]["name"] == best), None)
    head = tr(f"PWM 정책 {len(pols)}개: 허용 {len(adm)}개", f"{len(pols)} PWM policies: {len(adm)} admissible")
    if bad:
        head += tr(" · 불허 ", " · not admissible ") + ", ".join(esc(p["policy"]["name"]) for p in bad)
    if bp is not None:
        vb = bp.get("versus_baseline") or {}
        head += tr(f" — 인버터 에너지 최선: {esc(best)}", f" — lowest inverter energy: {esc(best)}")
        if vb.get("delta_E_inv_J") is not None:
            head += f" ({_kj(vb['delta_E_inv_J'])}, {pct(vb['delta_E_inv_J'], bp['E_inv_J'] - vb['delta_E_inv_J'], 1)})"
    be = res.get("best_policy_energy_among_evaluated") or {}
    if be.get("status") == "UNDECIDED":
        head += tr("; 모터+인버터 합은 구간이 겹쳐 순위 미정", "; motor+inverter: the intervals overlap, no ranking")
    elif be.get("policy"):
        head += tr(f"; 모터+인버터 합 최선 {esc(be['policy'])}", f"; lowest motor+inverter energy {esc(be['policy'])}")
    ins = Insight(headline=head, verdict=None)
    ins.metrics += [(tr("허용 정책", "admissible"), f"{len(adm)} / {len(pols)}", "ok" if adm else "bad"),
                    ("Pareto", ", ".join(esc(x) for x in res.get("pareto") or []) or "—", "info")]
    # the trade across the policies
    s = ins.section(tr("정책 간 맞바꿈 (인버터 에너지 ↔ 제어·파형 여유)", "the trade across policies (inverter energy ↔ control and "
                                                                "waveform margins)"),
                    tr("fsw를 낮추면 스위칭 에너지가 줄지만 PWM 동손·위상 여유·펄스 비가 나빠집니다 — 아래는 계산된 값의 비교입니다.",
                       "a lower fsw saves switching energy but costs PWM copper, phase margin and pulse ratio — below are "
                       "the computed values side by side."))
    for p in sorted(pols, key=lambda p: p.get("E_inv_J") or math.inf):
        cu = (_kj(p["E_cu_pwm_J"]) if p.get("E_cu_pwm_J") is not None else
              f"≥ {_kj(p.get('E_cu_pwm_lower_bound_J'))}")
        s.add(tr(f"<b>{esc(p['policy']['name'])}</b>: 인버터 {_kj(p.get('E_inv_J'))} · PWM 동손 {cu} · 위상 여유 최소 "
                 f"{num(p.get('phase_margin_min_deg'), 3)}° · 펄스 비 최소 {num(p.get('pulse_ratio_min'), 3)} · T_j 최대 "
                 f"{num(p.get('Tj_max_C'))} °C",
                 f"<b>{esc(p['policy']['name'])}</b>: inverter {_kj(p.get('E_inv_J'))} · PWM copper {cu} · phase margin min "
                 f"{num(p.get('phase_margin_min_deg'), 3)}° · pulse ratio min {num(p.get('pulse_ratio_min'), 3)} · T_j max "
                 f"{num(p.get('Tj_max_C'))} °C"), "ok" if p.get("admissible") else "bad")
    for p in pols:
        name = esc(p["policy"]["name"])
        s = ins.section(name + (tr(" — 허용", " — admissible") if p.get("admissible") else tr(" — 허용 안 됨", " — not admissible")))
        for v in p.get("violations") or []:
            s.add(esc(engine_text(v)), "bad")
        ms = _margins(p, lim)
        if ms:
            tight = min((m for m in ms if m[5] is not None), key=lambda m: m[5], default=None)
            for lab, val, limv, unit, m, rel, upper in ms:
                s.add(tr(f"{lab}: {_u(val, unit)} vs {'한계' if upper else '최소'} {_u(limv, unit)} → "
                         f"{'여유' if m >= 0 else '위반'} {_u(abs(m), unit, 3)} ({pct(abs(m), abs(limv), 0)})"
                         + (" — 가장 빠듯함" if tight is not None and lab == tight[0] else ""),
                         f"{lab}: {_u(val, unit)} vs {'limit' if upper else 'minimum'} {_u(limv, unit)} → "
                         f"{'margin' if m >= 0 else 'violated by'} {_u(abs(m), unit, 3)} ({pct(abs(m), abs(limv), 0)})"
                         + (" — the tightest" if tight is not None and lab == tight[0] else "")),
                      "ok" if m >= 0 else "bad")
        e = p.get("energy") or {}
        mag = (f"≤ {_kj(p['E_mag_hf_bound_J'])}" if p.get("E_mag_hf_bound_J") is not None else tr("상한 없음", "no bound"))
        s.add(tr(f"에너지 구간 [{_kj(e.get('lower_J'))}, {_kj(e.get('upper_J')) if e.get('upper_J') is not None else '∞'}] = 인버터 "
                 f"{_kj(p.get('E_inv_J'))} + PWM 동손 + 고주파 철·자석 손실 {mag} (상한은 기대값 아님)",
                 f"energy interval [{_kj(e.get('lower_J'))}, {_kj(e.get('upper_J')) if e.get('upper_J') is not None else '∞'}] = "
                 f"inverter {_kj(p.get('E_inv_J'))} + PWM copper + HF iron/magnet {mag} (the bound is not an expected value)"),
              "info", tr(f"DC 링크 ESR {_kj(p.get('E_cap_J'))} (별도 — 소유 경계 미선언)", f"DC-link ESR {_kj(p.get('E_cap_J'))} "
                         f"(separate — ownership not declared)") if p.get("E_cap_J") else "")
        vb = p.get("versus_baseline") or {}
        if vb.get("delta_E_inv_J") is not None:
            tot = vb.get("total") or {}
            s.add(tr(f"기준 대비 인버터 {'+' if vb['delta_E_inv_J'] >= 0 else ''}{_kj(vb['delta_E_inv_J'])} "
                     f"({'+' if (vb.get('relative_inv') or 0) >= 0 else ''}{num(100 * (vb.get('relative_inv') or 0), 3)} %)"
                     + (f"; 모터+인버터 합: {'순위 미정 — 구간이 겹침' if tot.get('status') == 'UNDECIDED' else esc(tot.get('status', ''))}"
                        if tot else ""),
                     f"inverter vs baseline {'+' if vb['delta_E_inv_J'] >= 0 else ''}{_kj(vb['delta_E_inv_J'])} "
                     f"({'+' if (vb.get('relative_inv') or 0) >= 0 else ''}{num(100 * (vb.get('relative_inv') or 0), 3)} %)"
                     + (f"; motor+inverter: {'undecided — the intervals overlap' if tot.get('status') == 'UNDECIDED' else esc(tot.get('status', ''))}"
                        if tot else "")), "info",
                  (f"[{_kj((tot.get('interval_candidate_J') or [None])[0])}, {_kj((tot.get('interval_candidate_J') or [None, None])[1])}] vs "
                   f"[{_kj((tot.get('interval_baseline_J') or [None])[0])}, {_kj((tot.get('interval_baseline_J') or [None, None])[1])}]")
                  if tot.get("interval_candidate_J") else "")
        trs = [x["transient"] for x in p.get("transitions") or [] if x.get("carrier_change") and x.get("transient")]
        if trs:
            ev = [x for x in trs if x.get("evaluated")]
            mx = _z(max((x["excursion_A"] for x in ev), default=None), 1e-9)
            s.add(tr(f"fsw 전환 {len(trs)}회: bumpless {sum(bool(x.get('bumpless')) for x in ev)}/{len(ev)}, 최대 전류 편차 {q(mx, 'A', 3)}"
                     + (f" (한계 {q(lim.get('transition_excursion_max_A'), 'A')})" if lim.get("transition_excursion_max_A") is not None else ""),
                     f"{len(trs)} fsw changes: bumpless {sum(bool(x.get('bumpless')) for x in ev)}/{len(ev)}, largest current "
                     f"excursion {q(mx, 'A', 3)}" + (f" (limit {q(lim.get('transition_excursion_max_A'), 'A')})"
                                                     if lim.get("transition_excursion_max_A") is not None else "")),
                  "ok" if ev and all(x.get("bumpless") for x in ev) else "info")
        for c in (p.get("chatter") or {}).get("rows") or []:
            ratio = (c["hysteresis"] / c["noise_pp"]) if c.get("noise_pp") else None
            meas = tr(*_MEAS[c["measurement"]]) if c.get("measurement") in _MEAS else esc(c.get("measurement", ""))
            s.add(tr(f"임계 채터 {meas}: 히스테리시스 {num(c['hysteresis'])} / 노이즈 pp {num(c.get('noise_pp'))}"
                     + (f" = {num(ratio, 3)}배" if ratio else "") + (" — 위험" if c.get("risk") else ""),
                     f"threshold chatter {meas}: hysteresis {num(c['hysteresis'])} / noise pp "
                     f"{num(c.get('noise_pp'))}" + (f" = {num(ratio, 3)}×" if ratio else "") + (" — at risk" if c.get("risk") else "")),
                  "bad" if c.get("risk") else ("ok" if c.get("risk") is False else "open"))
        for u in p.get("unverified") or []:
            s.add(esc(engine_text(u)), "open")
    cv = res.get("energy_control_volume") or {}
    s = ins.section(tr("이 비교가 말하지 않는 것", "what this comparison does not say"))
    if cv.get("excluded"):
        s.add(tr("에너지 경계 밖: ", "outside the energy boundary: ") + "; ".join(esc(engine_text(x)) for x in cv["excluded"]), "info")
    for k in ("peak_current_meaning", "thermal_scope"):
        if res.get(k):
            s.add(esc(engine_text(res[k])), "info")
    for x in res.get("not_evaluated") or []:
        s.add(esc(engine_text(x)), "open")
    if res.get("meaning"):
        s.add(esc(engine_text(res["meaning"])), "info")
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- timing
def pwm_timing_insight(res: dict) -> Insight:
    rows = sorted(res.get("rows") or [], key=lambda x: x.get("fsw_Hz") or 0)
    ok = [x for x in rows if x.get("deadline_ok")]
    pm = [x for x in ok if x.get("phase_margin_deg") is not None]
    head = tr(f"전류 루프 타이밍 (fsw {num((rows[0]['fsw_Hz'] if rows else 0) / 1e3)}–{num((rows[-1]['fsw_Hz'] if rows else 0) / 1e3)} kHz): "
              f"deadline 충족 {len(ok)}/{len(rows)}", f"current-loop timing (fsw {num((rows[0]['fsw_Hz'] if rows else 0) / 1e3)}–"
              f"{num((rows[-1]['fsw_Hz'] if rows else 0) / 1e3)} kHz): deadline met {len(ok)}/{len(rows)}")
    if pm:
        head += tr(f"; 위상 여유 {num(pm[0]['phase_margin_deg'], 3)}° @ {num(pm[0]['fsw_Hz'] / 1e3)} kHz → {num(pm[-1]['phase_margin_deg'], 3)}° "
                   f"@ {num(pm[-1]['fsw_Hz'] / 1e3)} kHz", f"; phase margin {num(pm[0]['phase_margin_deg'], 3)}° @ "
                   f"{num(pm[0]['fsw_Hz'] / 1e3)} kHz → {num(pm[-1]['phase_margin_deg'], 3)}° @ {num(pm[-1]['fsw_Hz'] / 1e3)} kHz")
    ins = Insight(headline=head)
    s = ins.section(tr("fsw에 따른 지연과 위상 여유", "delay and phase margin over fsw"),
                    tr("갱신 주기와 변조기 지연이 1/fsw에 비례해 늘어, 교차 주파수에서의 지연 위상만큼 위상 여유가 줄어듭니다.",
                       "the update period and modulator delay grow with 1/fsw, and the phase margin shrinks by the delay's "
                       "phase at the crossover."))
    for x in rows:
        if not x.get("deadline_ok"):
            s.add(tr(f"<b>{num(x['fsw_Hz'] / 1e3)} kHz</b>: 제어 deadline 미준수", f"<b>{num(x['fsw_Hz'] / 1e3)} kHz</b>: control "
                     f"deadline missed"), "bad")
            continue
        s.add(tr(f"<b>{num(x['fsw_Hz'] / 1e3)} kHz</b>: 총 지연 {_us(x.get('total_delay_s'))} → 교차 {q(x.get('crossover_Hz'), 'Hz')}에서 지연 위상 "
                 f"{num(x.get('phase_at_crossover_deg'), 3)}°, 위상 여유 {num(x.get('phase_margin_deg'), 3)}° ({esc(x.get('binding_axis') or '—')}축)",
                 f"<b>{num(x['fsw_Hz'] / 1e3)} kHz</b>: total delay {_us(x.get('total_delay_s'))} → delay phase "
                 f"{num(x.get('phase_at_crossover_deg'), 3)}° at the {q(x.get('crossover_Hz'), 'Hz')} crossover, phase margin "
                 f"{num(x.get('phase_margin_deg'), 3)}° ({esc(x.get('binding_axis') or '—')} axis)"),
              "ok" if x.get("sampled_stable") else "bad",
              tr(f"deadline 여유 {_us(x.get('deadline_margin_s'))} · 연속 근사 위상 여유 {num(x.get('continuous_screen_phase_margin_deg'), 3)}° "
                 f"(표본 루프가 판정 기준)", f"deadline slack {_us(x.get('deadline_margin_s'))} · continuous-screen margin "
                 f"{num(x.get('continuous_screen_phase_margin_deg'), 3)}° (the sampled loop is the judge)"))
    if rows and res.get("mode_frequency_Hz"):
        ph = [x.get("phase_at_mode_deg") for x in rows if x.get("phase_at_mode_deg") is not None]
        if ph:
            s.add(tr(f"구동계 모드 {q(res['mode_frequency_Hz'], 'Hz')}에서의 전류 루프 지연 위상 {num(min(ph), 3)}–{num(max(ph), 3)}°",
                     f"current-loop delay phase at the driveline mode {q(res['mode_frequency_Hz'], 'Hz')}: "
                     f"{num(min(ph), 3)}–{num(max(ph), 3)}°"), "info")
    pl, pp = res.get("plant_L_H") or {}, res.get("plant_point") or {}
    if pl:
        s.add(tr(f"플랜트: 기계의 차동 인덕턴스 L_d {q((pl.get('d') or 0) * 1e6, 'µH')}, L_q {q((pl.get('q') or 0) * 1e6, 'µH')} @ "
                 f"{q(pp.get('speed_rpm'), 'rpm')}, {q(pp.get('torque_Nm'), 'N·m')}",
                 f"plant: the machine's differential inductance L_d {q((pl.get('d') or 0) * 1e6, 'µH')}, L_q "
                 f"{q((pl.get('q') or 0) * 1e6, 'µH')} @ {q(pp.get('speed_rpm'), 'rpm')}, {q(pp.get('torque_Nm'), 'N·m')}"), "info")
    s = ins.section(tr("fsw 전환 순간의 갱신 방식", "how the new period is loaded at a change"))
    for key in ("transition_shadow", "transition_immediate"):
        t = res.get(key) or {}
        if not t:
            continue
        up = {"shadow (atomic reload)": tr("섀도 (원자적 reload)", "shadow (atomic reload)"),
              "immediate write": tr("즉시 쓰기", "immediate write")}.get(t.get("update"), esc(t.get("update", "")))
        err = t.get("worst_period_duty_error")
        s.add(tr(f"<b>{up}</b>: 최악 주기의 듀티 오차 {pct(err, 1, 3)}, 불규칙 주기 {t.get('irregular_periods')}개",
                 f"<b>{up}</b>: worst-period duty error {pct(err, 1, 3)}, {t.get('irregular_periods')} irregular period(s)"),
              "ok" if t.get("ok") else "bad", esc("; ".join((t.get("gate_events") or {}).get("problems") or [])))
    tm = res.get("timing") or {}
    if tm:
        s = ins.section(tr("지연 사슬 (선언)", "the delay chain (declared)"))
        s.add(tr(f"샘플→래치 {_us(tm.get('sample_to_latch_s'))} · 필터 {_us(tm.get('filter_delay_s'))} · 주기당 갱신 "
                 f"{tm.get('updates_per_period')} · 변조기 지연 {num(tm.get('modulator_delay_fraction'))} 주기 · 최소 펄스 "
                 f"{_us(tm.get('min_pulse_s'))}", f"sample→latch {_us(tm.get('sample_to_latch_s'))} · filter "
                 f"{_us(tm.get('filter_delay_s'))} · updates per period {tm.get('updates_per_period')} · modulator delay "
                 f"{num(tm.get('modulator_delay_fraction'))} period · minimum pulse {_us(tm.get('min_pulse_s'))}"), "info",
              esc(tm.get("basis", "")))
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- ripple
def pwm_ripple_insight(res: dict) -> Insight:
    rows = sorted(res.get("rows") or [], key=lambda x: x.get("fsw_Hz") or 0)
    pt = res.get("point") or {}
    i_rms = (pt.get("i_peak_A") or 0) / math.sqrt(2) if pt.get("i_peak_A") else None
    head = tr(f"상전류 리플 @ {q(pt.get('speed_rpm'), 'rpm')} · {q(pt.get('torque_Nm'), 'N·m')}", f"phase-current ripple @ "
              f"{q(pt.get('speed_rpm'), 'rpm')} · {q(pt.get('torque_Nm'), 'N·m')}")
    if len(rows) >= 2:
        a, b = rows[0], rows[-1]
        head += tr(f": {num(a['fsw_Hz'] / 1e3)} kHz {q(a['ripple_rms_A'], 'A')} rms → {num(b['fsw_Hz'] / 1e3)} kHz "
                   f"{q(b['ripple_rms_A'], 'A')} rms (fsw ×{num(b['fsw_Hz'] / a['fsw_Hz'], 3)} → 리플 ×{num(b['ripple_rms_A'] / a['ripple_rms_A'], 3)})",
                   f": {num(a['fsw_Hz'] / 1e3)} kHz {q(a['ripple_rms_A'], 'A')} rms → {num(b['fsw_Hz'] / 1e3)} kHz "
                   f"{q(b['ripple_rms_A'], 'A')} rms (fsw ×{num(b['fsw_Hz'] / a['fsw_Hz'], 3)} → ripple "
                   f"×{num(b['ripple_rms_A'] / a['ripple_rms_A'], 3)})")
    ins = Insight(headline=head)
    s = ins.section(tr("fsw별 리플", "ripple per fsw"),
                    tr(f"운전점: 상전류 {q(pt.get('i_peak_A'), 'A')} peak, 변조지수 {num(pt.get('m'), 3)}, f_e {q(pt.get('f_e_Hz'), 'Hz')}, "
                       f"L_hf {q((res.get('L_hf_H') or 0) * 1e6, 'µH')}",
                       f"point: phase {q(pt.get('i_peak_A'), 'A')} peak, modulation index {num(pt.get('m'), 3)}, f_e "
                       f"{q(pt.get('f_e_Hz'), 'Hz')}, L_hf {q((res.get('L_hf_H') or 0) * 1e6, 'µH')}"))
    for x in rows:
        s.add(tr(f"<b>{num(x['fsw_Hz'] / 1e3)} kHz</b>: {q(x['ripple_rms_A'], 'A')} rms"
                 + (f" (기본파 rms의 {pct(x['ripple_rms_A'], i_rms, 1)})" if i_rms else "")
                 + f", pp {q(x.get('ripple_pp_A'), 'A')}; 펄스 비 {num(x.get('pulse_ratio'), 3)}, 가장 좁은 펄스 {_us(x.get('narrowest_pulse_s'), 3)}",
                 f"<b>{num(x['fsw_Hz'] / 1e3)} kHz</b>: {q(x['ripple_rms_A'], 'A')} rms"
                 + (f" ({pct(x['ripple_rms_A'], i_rms, 1)} of the fundamental rms)" if i_rms else "")
                 + f", pp {q(x.get('ripple_pp_A'), 'A')}; pulse ratio {num(x.get('pulse_ratio'), 3)}, narrowest pulse "
                   f"{_us(x.get('narrowest_pulse_s'), 3)}"),
              "ok" if x.get("min_pulse_ok") else "bad",
              tr(f"시간 영역 rms와 스펙트럼 rms 차 {q(abs(x['ripple_rms_A'] - x['ripple_rms_spectrum_A']), 'A', 2)} (수치 일치 확인)",
                 f"time-domain vs spectrum rms differ by {q(abs(x['ripple_rms_A'] - x['ripple_rms_spectrum_A']), 'A', 2)} "
                 f"(numerical cross-check)") if x.get("ripple_rms_spectrum_A") is not None else "")
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- transients
_VARIANT = {"declared": ("선언된 구현", "declared implementation"),
            "bumpless (volts, Ki*Ts remapped)": ("bumpless (적분기를 전압으로 저장, Ki·Ts 재매핑)", "bumpless (integrator in volts, Ki·Ts remapped)"),
            "error-sum integrator, Ki*Ts remapped": ("오차합 적분기, Ki·Ts 재매핑", "error-sum integrator, Ki·Ts remapped"),
            "integrator reset": ("적분기 리셋", "integrator reset"),
            "fixed discrete gains": ("고정 이산 이득", "fixed discrete gains")}
_SENSE = {"inline_phase": ("상전류 인라인 센서", "inline phase sensor"), "leg_shunt": ("레그 션트", "leg shunt"),
          "dc_link_shunt": ("DC 링크 단일 션트", "single DC-link shunt")}


def pwm_transients_insight(res: dict) -> Insight:
    pt = res.get("point") or {}
    h = res.get("sampling_here") or {}
    trn = res.get("transition") or {}
    lim = trn.get("excursion_limit_A")
    vs = trn.get("variants") or {}
    over = [n for n, v in vs.items() if v.get("evaluated") and lim is not None and v.get("excursion_A", 0) > lim]
    head = tr(f"@ {q(pt.get('speed_rpm'), 'rpm')} · {q(pt.get('torque_Nm'), 'N·m')} (m {num(pt.get('m'), 3)}, fsw {num((pt.get('fsw_Hz') or 0) / 1e3)} kHz): "
              f"전류 샘플링 {esc(h.get('status', '—'))} (유효 {pct(h.get('valid_fraction'), 1, 1)})",
              f"@ {q(pt.get('speed_rpm'), 'rpm')} · {q(pt.get('torque_Nm'), 'N·m')} (m {num(pt.get('m'), 3)}, fsw "
              f"{num((pt.get('fsw_Hz') or 0) / 1e3)} kHz): current sampling {esc(h.get('status', '—'))} (valid "
              f"{pct(h.get('valid_fraction'), 1, 1)})")
    if trn:
        head += tr(f"; fsw {num((trn.get('from_Hz') or 0) / 1e3)} → {num((trn.get('to_Hz') or 0) / 1e3)} kHz 전환: 한계 초과 변형 {len(over)}/{len(vs)}",
                   f"; fsw {num((trn.get('from_Hz') or 0) / 1e3)} → {num((trn.get('to_Hz') or 0) / 1e3)} kHz change: "
                   f"{len(over)}/{len(vs)} variants above the limit")
    ins = Insight(headline=head)
    s = ins.section(tr("이 운전점의 전류 샘플링", "current sampling at this point"))
    s.add(tr(f"{tr(*_SENSE.get(h.get('kind'), (h.get('kind', ''), h.get('kind', ''))))}: 샘플 {h.get('n_samples')}개 중 무효 {h.get('n_invalid')}개, "
             f"최장 무효 연속 {h.get('longest_invalid_run')}, 최대 나이 {_us(h.get('max_age_s'))}, 유지 오차 상한 {q(h.get('error_bound_A'), 'A', 3)}, "
             f"채널 skew 오차 상한 {q(h.get('skew_error_bound_A'), 'A', 3)}",
             f"{tr(*_SENSE.get(h.get('kind'), (h.get('kind', ''), h.get('kind', ''))))}: {h.get('n_invalid')} of {h.get('n_samples')} "
             f"samples invalid, longest invalid run {h.get('longest_invalid_run')}, max age {_us(h.get('max_age_s'))}, hold "
             f"error bound {q(h.get('error_bound_A'), 'A', 3)}, channel-skew error bound {q(h.get('skew_error_bound_A'), 'A', 3)}"),
          "ok" if h.get("status") == "OK" else "bad", tr(f"가장 짧은 유효 창 {_us(h.get('min_window_s'), 3)}",
                                                       f"shortest valid window {_us(h.get('min_window_s'), 3)}"))
    for v in h.get("violations") or []:
        s.add(esc(engine_text(v)), "bad")
    for v in h.get("unknown") or []:
        s.add(esc(engine_text(v)), "open")
    curves = res.get("sampling_curves") or {}
    if curves:
        s = ins.section(tr("센서 방식별: 변조지수에 따른 유효 샘플", "per sensing method: valid samples over the modulation index"))
        for k, c in curves.items():
            m = list(c.get("m") or [])
            vf = list(c.get("valid_fraction") or [])
            if not m or len(vf) != len(m):
                continue
            low = [(mi, v) for mi, v in zip(m, vf) if v is not None and v < 1.0]
            name = tr(*_SENSE.get(k, (k, k)))
            if not low:
                s.add(tr(f"{name}: m {num(m[0], 3)}–{num(m[-1], 3)} 전 구간 유효 100 %", f"{name}: 100 % valid over m "
                         f"{num(m[0], 3)}–{num(m[-1], 3)}"), "ok")
            else:
                worst = min(low, key=lambda t: t[1])
                span = (f"{num(low[0][0], 3)}" if len(low) == 1 else f"{num(low[0][0], 3)}–{num(low[-1][0], 3)}")
                s.add(tr(f"{name}: m {span}에서 유효 100 % 미만 — 최저 {pct(worst[1], 1, 0)} @ m {num(worst[0], 3)}",
                         f"{name}: below 100 % valid at m {span} — lowest {pct(worst[1], 1, 0)} @ m {num(worst[0], 3)}"), "warn")
    if vs:
        s = ins.section(tr(f"fsw 전환 과도 (구현 방식별, 한계 {q(lim, 'A')})" if lim is not None else "fsw 전환 과도 (구현 방식별)",
                           f"the fsw-change transient (per implementation, limit {q(lim, 'A')})" if lim is not None else
                           "the fsw-change transient (per implementation)"),
                        tr("같은 전환이라도 적분기 저장 방식·이득 재매핑에 따라 전압 출력이 점프하고 전류가 튑니다.",
                           "the same change can jump the voltage output and kick the current, depending on how the integrator "
                           "is stored and the gains are remapped."))
        for n, v in vs.items():
            lab = tr(*_VARIANT.get(n, (n, n)))
            if not v.get("evaluated"):
                s.add(f"<b>{lab}</b>: " + tr("평가 안 됨", "not evaluated"), "open", esc(engine_text(v.get("reason") or "")))
                continue
            exc = _z(v.get("excursion_A") or 0, 1e-9)
            lvl = "ok" if v.get("bumpless") else ("bad" if lim is not None and exc > lim else "warn")
            s.add(tr(f"<b>{lab}</b>: 출력 점프 {q(v.get('output_jump_V'), 'V')} → 전류 편차 {q(exc, 'A')}"
                     + (f", 정착 {_ms(v.get('settle_s'), 3)}" if v.get("settle_s") else "")
                     + (f", 유효 Ki ×{num(v.get('Ki_eff_ratio'), 3)}" if (v.get("Ki_eff_ratio") or 1) != 1 else "")
                     + (" — bumpless" if v.get("bumpless") else ""),
                     f"<b>{lab}</b>: output jump {q(v.get('output_jump_V'), 'V')} → current excursion {q(exc, 'A')}"
                     + (f", settles in {_ms(v.get('settle_s'), 3)}" if v.get("settle_s") else "")
                     + (f", effective Ki ×{num(v.get('Ki_eff_ratio'), 3)}" if (v.get("Ki_eff_ratio") or 1) != 1 else "")
                     + (" — bumpless" if v.get("bumpless") else "")), lvl,
                  tr(f"포화 샘플 {v.get('saturated_samples')}", f"saturated samples {v.get('saturated_samples')}"))
    notes_section(ins, [h.get("note")])
    return ins.nonempty()


# ---------------------------------------------------------------------------------------------------- driveline
_DL = {"off": ("감쇠 없음", "no damping"), "shaping": ("토크 성형", "torque shaping"), "feedback": ("속도 피드백", "speed feedback"),
       "combined": ("성형 + 피드백", "shaping + feedback")}


def driveline_insight(res: dict) -> Insight:
    md = res.get("modal") or {}
    req = res.get("requirement") or {}
    vs = res.get("variants") or {}
    ok = [n for n, v in vs.items() if v.get("status") == "FEASIBLE"]
    man = res.get("maneuver") or {}
    head = tr(f"구동계 비틀림 모드 {q(md.get('f_n_Hz'), 'Hz', 3)} (감쇠비 ζ {num(md.get('zeta'), 3)}): {len(vs)}개 변형 중 요구 충족 "
              + (", ".join(tr(*_DL.get(n, (n, n))) for n in ok) or "없음"),
              f"driveline torsional mode {q(md.get('f_n_Hz'), 'Hz', 3)} (damping ratio ζ {num(md.get('zeta'), 3)}): of "
              f"{len(vs)} variants the requirement is met by " + (", ".join(tr(*_DL.get(n, (n, n))) for n in ok) or "none"))
    ins = Insight(headline=head, verdict="PASS" if ok else ("FAIL" if vs and all(v.get("status") == "INFEASIBLE" for v in vs.values())
                                                           else "UNKNOWN"))
    ins.metrics += [(tr("모드", "mode"), q(md.get("f_n_Hz"), "Hz", 3), "info"), ("ζ", num(md.get("zeta"), 3), "info")]
    if req:
        ins.metrics.append((tr("요구 (t90 · 저크 · 정착)", "requirement (t90 · jerk · settle)"),
                            f"{_ms(req.get('t_to_90_max_s'), 3)} · {num(req.get('peak_vehicle_jerk_max_m_s3'))} m/s³ · "
                            f"{_ms(req.get('settle_max_s'), 3)}", "info"))
    s = ins.section(tr("변형별 응답 (같은 조작, 같은 요구)", "response per variant (same maneuver, same requirement)"),
                    tr(f"{q(man.get('T0_Nm'), 'N·m')} → {q(man.get('T1_Nm'), 'N·m')} 계단 @ {q(man.get('speed_rpm'), 'rpm')}",
                       f"{q(man.get('T0_Nm'), 'N·m')} → {q(man.get('T1_Nm'), 'N·m')} step @ {q(man.get('speed_rpm'), 'rpm')}"))
    base = (vs.get("off") or {}).get("metrics") or {}
    for n, v in vs.items():
        m = v.get("metrics") or {}
        st = v.get("stability") or {}
        lab = tr(*_DL.get(n, (n, n)))
        jerk, t90, settle = m.get("peak_vehicle_jerk_m_s3"), m.get("t_to_90_s"), m.get("t_settle_s")
        s.add(tr(f"<b>{lab}</b>: 저크 {num(jerk, 3)} m/s³ · t90 {_ms(t90, 3)} · 정착 {_ms(settle, 3)} · 오버슈트 {pct(m.get('overshoot'), 1, 0)} · "
                 f"축 토크 최대 {q(m.get('shaft_torque_peak_Nm'), 'N·m')}" + (f" · 폐루프 ζ {num(st['dominant_zeta'], 3)}" if st.get("dominant_zeta") is not None else ""),
                 f"<b>{lab}</b>: jerk {num(jerk, 3)} m/s³ · t90 {_ms(t90, 3)} · settle {_ms(settle, 3)} · overshoot "
                 f"{pct(m.get('overshoot'), 1, 0)} · peak shaft torque {q(m.get('shaft_torque_peak_Nm'), 'N·m')}"
                 + (f" · closed-loop ζ {num(st['dominant_zeta'], 3)}" if st.get("dominant_zeta") is not None else "")),
              {"FEASIBLE": "ok", "INFEASIBLE": "bad"}.get(v.get("status"), "open"),
              "; ".join(esc(engine_text(r)) for r in v.get("reasons") or []))
    if base and len(vs) > 1:
        s = ins.section(tr("감쇠 없음 대비 무엇이 바뀌나", "what changes against no damping"))
        for n, v in vs.items():
            if n == "off":
                continue
            m = v.get("metrics") or {}
            parts = []
            for key, lab in (("peak_vehicle_jerk_m_s3", tr("저크", "jerk")), ("t_to_90_s", "t90"), ("t_settle_s", tr("정착", "settle"))):
                a, b = base.get(key), m.get(key)
                if a and b is not None:
                    parts.append(f"{lab} {'+' if b >= a else ''}{pct(b - a, a, 0)}")
            cost = []
            if v.get("work_difference_J") is not None:
                cost.append(tr(f"같은 창에서 전달 일 {q(v['work_difference_J'], 'J')}", f"work delivered in the window "
                               f"{q(v['work_difference_J'], 'J')}"))
            m_rms = m.get("correction_rms_Nm")
            if m_rms:
                cost.append(tr(f"보정 토크 rms {q(m_rms, 'N·m', 3)} (평균 {q(m.get('correction_mean_Nm'), 'N·m', 3)})",
                               f"correction rms {q(m_rms, 'N·m', 3)} (mean {q(m.get('correction_mean_Nm'), 'N·m', 3)})"))
            s.add(f"<b>{tr(*_DL.get(n, (n, n)))}</b>: " + ", ".join(parts), "info",
                  " · ".join(cost) + (" — " + esc(engine_text(v.get("loss_note", ""))) if v.get("loss_note") else ""))
    sl = res.get("slew_limits") or {}
    if sl.get("established"):
        s = ins.section(tr("전압 여유가 허용하는 토크 변화율", "torque slew allowed by the voltage headroom"))
        s.add(tr(f"q축 여유 +{q(sl.get('headroom_up_V'), 'V')} / −{q(sl.get('headroom_down_V'), 'V')}, k_t {num(sl.get('kt_Nm_per_A'), 3)} N·m/A, "
                 f"L_q,diff {q((sl.get('Lq_diff_H') or 0) * 1e6, 'µH')} → 상승 {num(sl.get('pos_Nm_per_s'))} / 하강 {num(sl.get('neg_Nm_per_s'))} N·m/s",
                 f"q-axis headroom +{q(sl.get('headroom_up_V'), 'V')} / −{q(sl.get('headroom_down_V'), 'V')}, k_t "
                 f"{num(sl.get('kt_Nm_per_A'), 3)} N·m/A, L_q,diff {q((sl.get('Lq_diff_H') or 0) * 1e6, 'µH')} → up "
                 f"{num(sl.get('pos_Nm_per_s'))} / down {num(sl.get('neg_Nm_per_s'))} N·m/s"), "info")
        for n, v in vs.items():
            w = v.get("slew") or {}
            if w:
                s.add(tr(f"{tr(*_DL.get(n, (n, n)))}: 최대 상승 {num(w.get('max_up_Nm_per_s'))} N·m/s (허용의 "
                         f"{pct(w.get('max_up_Nm_per_s'), w.get('limit_up_Nm_per_s'), 1)})", f"{tr(*_DL.get(n, (n, n)))}: fastest rise {num(w.get('max_up_Nm_per_s'))} N·m/s "
                         f"({pct(w.get('max_up_Nm_per_s'), w.get('limit_up_Nm_per_s'), 1)} of the allowed)"),
                      "ok" if (w.get("max_up_Nm_per_s") or 0) <= (w.get("limit_up_Nm_per_s") or math.inf) else "warn")
    for n, v in vs.items():
        for note in v.get("notes") or []:
            ins.section(tr("주석", "notes")).add(f"{tr(*_DL.get(n, (n, n)))}: {esc(engine_text(note))}", "info")
    notes_section(ins, [res.get("meaning")])
    return ins.nonempty()


def stability_insight(res: dict) -> Insight:
    rows = res.get("continuous_relative_speed") or []
    md = res.get("modal") or {}
    head = tr(f"감쇠 이득 Kd와 지연 (모드 {q(md.get('f_n_Hz'), 'Hz', 3)}, 샘플 {q(res.get('sample_ms'), 'ms')})",
              f"damping gain Kd vs delay (mode {q(md.get('f_n_Hz'), 'Hz', 3)}, sample {q(res.get('sample_ms'), 'ms')})")
    if len(rows) >= 2:
        a, b = rows[0], rows[-1]
        head += tr(f": Kd {num(a['Kd'])} → {num(b['Kd'])}에서 감쇠 ζ {num(a['zeta_undelayed'], 3)} → {num(b['zeta_undelayed'], 3)}, "
                   f"불안정해지는 지연 {_ms((a.get('first_destabilising_delay_ms') or 0) / 1e3, 3)} → {_ms((b.get('first_destabilising_delay_ms') or 0) / 1e3, 3)}",
                   f": Kd {num(a['Kd'])} → {num(b['Kd'])} raises ζ {num(a['zeta_undelayed'], 3)} → {num(b['zeta_undelayed'], 3)} but "
                   f"the destabilising delay falls {_ms((a.get('first_destabilising_delay_ms') or 0) / 1e3, 3)} → "
                   f"{_ms((b.get('first_destabilising_delay_ms') or 0) / 1e3, 3)}")
    ins = Insight(headline=head)
    s = ins.section(tr("감쇠와 지연 내성의 맞바꿈 (이상적 상대속도 피드백 기준)", "damping vs delay tolerance (ideal relative-speed "
                                                                     "feedback reference)"))
    for c in rows:
        s.add(tr(f"Kd {num(c['Kd'])}: 지연 없을 때 ζ {num(c.get('zeta_undelayed'), 3)} · 불안정해지는 첫 지연 "
                 f"{_ms((c.get('first_destabilising_delay_ms') or 0) / 1e3, 3) if c.get('first_destabilising_delay_ms') is not None else '없음'}",
                 f"Kd {num(c['Kd'])}: ζ without delay {num(c.get('zeta_undelayed'), 3)} · first destabilising delay "
                 f"{_ms((c.get('first_destabilising_delay_ms') or 0) / 1e3, 3) if c.get('first_destabilising_delay_ms') is not None else 'none'}"),
              "info")
    grid = res.get("grid") or []
    if grid:
        s = ins.section(tr("표본 루프 격자 (Kd × 지연)", "sampled-loop grid (Kd × delay)"))
        for row in grid:
            if not row:
                continue
            st = [c for c in row if c.get("stable")]
            un = [c for c in row if not c.get("stable")]
            kd = row[0].get("Kd")
            s.add(tr(f"Kd {num(kd)}: 안정 지연 {', '.join(num(c['delay_ms']) for c in st) or '없음'} ms"
                     + (f" · 불안정 {', '.join(num(c['delay_ms']) for c in un)} ms" if un else ""),
                     f"Kd {num(kd)}: stable at delays {', '.join(num(c['delay_ms']) for c in st) or 'none'} ms"
                     + (f" · unstable at {', '.join(num(c['delay_ms']) for c in un)} ms" if un else "")),
                  "ok" if not un else "warn")
    notes_section(ins, [res.get("meaning")])
    return ins.nonempty()

