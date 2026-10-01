"""Reading of the causal fault simulation: what happened, who detected it and when, what was commanded and what the
bridge actually did, what the truth did against each requirement, where it failed and what would change it.

Every number comes from the result.  The suggestions name the mechanism that the result shows to be decisive (an
undetected fault, a blocked path, a late detection, a reaction that the bridge could not execute, a policy that chose
from a faulty measurement); they are not new verdicts.
"""

from __future__ import annotations

from ..extensions.faultsim.labels import param_label, value_label
from ..i18n import tr
from . import Insight, esc, num, q

VL = {"PASS": "ok", "FAIL": "bad", "UNKNOWN": "open", "NOT_APPLICABLE": "info"}


def bridge_cycling(res: dict, min_changes: int = 4) -> dict | None:
    """The commanded bridge switching back and forth between the same two states after the first fault (at least
    ``min_changes`` changes): the states, the number of changes, the period (median time between entering the same
    state) and the DC-link and torque range while it cycles; None otherwise."""
    import numpy as np
    from ..extensions.faultsim.engine import BRIDGE_CODES
    tr_ = res.get("trace") or {}
    t = np.asarray(tr_.get("t", []), dtype=float)
    br = np.asarray(tr_.get("bridge", []), dtype=float)
    if t.size < 3 or br.size != t.size:
        return None
    tf = min((e["t"] for e in res.get("events") or [] if e["kind"] == "fault"), default=float(t[0]))
    k = np.where(t >= tf - 1e-12)[0]
    b = br[k].astype(int)
    ch = np.where(np.diff(b) != 0)[0] + 1
    if len(ch) < min_changes:
        return None
    entered = b[ch]
    pair = sorted(set(int(x) for x in entered[-min_changes:]))
    if len(pair) != 2:
        return None
    inside = np.isin(b[ch - 1], pair) & np.isin(entered, pair)          # a change between the two states
    j = len(ch)
    while j > 0 and inside[j - 1]:                                     # the run of such changes up to the end
        j -= 1
    ch, entered = ch[j:], entered[j:]
    if len(ch) < min_changes:
        return None
    names = {v: n for n, v in BRIDGE_CODES.items()}
    tt = t[k]
    first = entered[-1]
    starts = tt[ch][entered == first]
    per = float(np.median(np.diff(starts))) if starts.size >= 2 else None
    if per:                                    # the steady cycle starts after the last interval longer than it
        gaps = np.diff(tt[ch])
        long_ = np.where(gaps > 1.5 * per)[0]
        if long_.size:
            ch, entered = ch[long_[-1] + 1:], entered[long_[-1] + 1:]
        if len(ch) < min_changes:
            return None
    w = (t >= tt[ch[0]])
    v = np.asarray(tr_.get("v_dc", []), dtype=float)
    T = np.asarray(tr_.get("T_shaft", []), dtype=float)
    return {"states": [names.get(pair[0], str(pair[0])), names.get(pair[1], str(pair[1]))], "changes": int(len(ch)),
            "first_s": float(tt[ch[0]]), "period_ms": None if per is None else per * 1e3,
            "frequency_Hz": None if not per else 1.0 / per,
            "v_min_V": float(v[w].min()) if v.size == t.size else None,
            "v_max_V": float(v[w].max()) if v.size == t.size else None,
            "T_min_Nm": float(T[w].min()) if T.size == t.size else None,
            "T_max_Nm": float(T[w].max()) if T.size == t.size else None}


def react(r) -> str:
    """A reaction or bridge command as people name it (in the current language)."""
    return {"asc_low": "ASC-low", "asc_high": "ASC-high", "six_switch_off": "6SO", "torque_zero": tr("토크 0", "zero torque"),
            "pwm": "PWM", "off": tr("게이트 차단", "gates off"), "safe_state": tr("안전 상태(정책)", "safe state (policy)"),
            "report_only": tr("보고만", "report only"),
            "none": tr("보호 반응 끔 (드라이버 자체 desat 차단은 남음)", "reactions off (the drivers' own desaturation "
                                                                  "turn-off stays)"),
            "policy": tr("프로젝트 정책", "project policy"),
            "torque_ramp": tr("토크 램프 → 0", "torque ramp to zero"),
            "current_to_asc": tr("전류 사전 조정 → ASC 점", "current pre-conditioning"),
            "voltage_ramp": tr("전압 램프 → 0", "voltage ramp to zero"),
            "sequential_asc_low": tr("상별 순차 ASC-low", "phase-sequential ASC-low"),
            "sequential_asc_high": tr("상별 순차 ASC-high", "phase-sequential ASC-high"),
            "seq_asc_low": tr("상별 순차 ASC-low", "phase-sequential ASC-low"),
            "seq_asc_high": tr("상별 순차 ASC-high", "phase-sequential ASC-high"),
            "vdc_hysteresis_low": tr("Vdc 히스테리시스 6SO↔ASC-low", "Vdc hysteresis 6SO / ASC-low"),
            "vdc_hysteresis_high": tr("Vdc 히스테리시스 6SO↔ASC-high", "Vdc hysteresis 6SO / ASC-high")}.get(r, str(r))


def strategy_log_text(log) -> str:
    """The steps a reaction strategy took on a trajectory (time, action; a fallback with its reason)."""
    parts = []
    for e in log or []:
        t = f"{num(e['t'] * 1e3, 4)} ms"
        if e.get("fallback"):
            parts.append(tr(f"{t} 대체 상태 {esc(react(e['action']))} — {esc(e['fallback'])}",
                            f"{t} fallback {esc(react(e['action']))} — {esc(e['fallback'])}"))
        else:
            parts.append(f"{t} {esc(react(e['action']))}")
    return " → ".join(parts)


def _ms(t, sig=4):
    return "—" if t is None else f"{num(t * 1e3, sig)} ms"


def _overall(verdicts: dict) -> str:
    vs = [v for v in verdicts.values() if v != "NOT_APPLICABLE"]
    return "FAIL" if "FAIL" in vs else "UNKNOWN" if "UNKNOWN" in vs else "PASS" if vs else "UNKNOWN"


def verdict_ko(v: str) -> str:
    return {"PASS": tr("통과", "PASS"), "FAIL": tr("실패", "FAIL"), "UNKNOWN": tr("판단불가", "UNKNOWN"),
            "NOT_APPLICABLE": tr("해당 없음", "not applicable")}.get(v, v)


def fault_word(k: str) -> str:
    """A fault kind as people name it (in the current language)."""
    return {"sensor": tr("센서", "sensor"), "torque_command": tr("토크 명령", "torque command"),
             "control_task_stop": tr("제어 태스크 정지", "control task stop"), "mcu_reset": tr("MCU 리셋", "MCU reset"),
             "pwm_output": tr("PWM 출력 고장", "PWM output fault"), "switch_open": tr("스위치 개방", "switch open"),
             "switch_short": tr("스위치 단락", "switch short"), "diode_open": tr("다이오드 개방", "diode open"),
             "phase_open": tr("상 개방", "phase open"), "gate_supply_loss": tr("게이트 전원 상실", "gate supply loss"),
             "battery_disconnect": tr("배터리 차단", "battery disconnect"),
             "contactor_stuck": tr("접촉기 융착", "contactor welded"),
             "charge_acceptance_loss": tr("충전 수용 상실", "loss of charge acceptance"),
             "mechanism_disabled": tr("감시 메커니즘 잠재 고장", "latent mechanism fault"),
             "path_lost": tr("반응 경로 잠재 상실", "latent path loss"),
             "resource_loss": tr("공유 자원 상실", "shared resource loss")}.get(k, k)


def reason_word(r: str) -> str:
    """Why a verdict is UNKNOWN / not applicable, as people read it."""
    return {"WINDOW_NOT_OBSERVED": tr("판정 창을 끝까지 관측하지 못함", "the judgement window was not observed to its end"),
            "WITHIN_NUMERICAL_ALLOWANCE": tr("여유가 샘플링·모델 허용 오차 안", "margin inside the sampling / model "
                                                                          "allowance"),
            "HOLD_NOT_OBSERVED": tr("유지 시간을 끝까지 관측하지 못함", "the hold time was not observed to its end"),
            "NOT_TRIGGERED": tr("기점 사건이 일어나지 않음", "the triggering event did not occur")}.get(r, esc(r))


METRIC_WORDS = {"i_phase_peak_A": ("최대 |상전류| [A]", "peak |phase current| [A]"),
                "v_dc_max_V": ("최대 V_dc [V]", "max V_dc [V]"), "v_dc_min_V": ("최소 V_dc [V]", "min V_dc [V]"),
                "i_bat_charge_max_A": ("최대 HV 역방향 전류 [A]", "max reverse HV current [A]"),
                "T_shaft_max_Nm": ("최대 축 토크 [N·m]", "max shaft torque [N·m]"),
                "T_shaft_min_Nm": ("최소 축 토크 [N·m]", "min shaft torque [N·m]"),
                "first_detection_ms": ("첫 검출 시각 [ms]", "first detection [ms]"),
                "FDTI_ms": ("FDTI [ms]", "FDTI [ms]"), "FRTI_ms": ("FRTI [ms]", "FRTI [ms]"),
                "FHTI_ms": ("FHTI [ms]", "FHTI [ms]"),
                "FDTI_plus_FRTI_bound_ms": ("FDTI + FRTI 상한 [ms]", "FDTI + FRTI bound [ms]")}


def metric_word(k: str) -> str:
    w = METRIC_WORDS.get(k)
    return tr(*w) if w else esc(str(k).replace("_", " "))


def axis_word(path: str) -> str:
    """A campaign axis (a scenario path) as people read it."""
    import re
    fixed = {"speed_rpm": ("속도 [rpm]", "speed [rpm]"), "torque_Nm": ("토크 요청 [N·m]", "torque request [N·m]"),
             "theta0_deg": ("초기 전기각 [°]", "initial electrical angle [°]"),
             "horizon_ms": ("시뮬레이션 길이 [ms]", "horizon [ms]"), "h_max_us": ("최대 스텝 [µs]", "max step [µs]")}
    if path in fixed:
        return tr(*fixed[path])
    m = re.fullmatch(r"faults\.(\d+)\.t_ms", path)
    if m:
        return tr(f"고장 {int(m.group(1)) + 1} 시각 [ms]", f"fault {int(m.group(1)) + 1} instant [ms]")
    m = re.fullmatch(r"faults\.(\d+)\.params\.(\w+)", path)
    if m:
        return tr(f"고장 {int(m.group(1)) + 1} {param_label(m.group(2))}", f"fault {int(m.group(1)) + 1} "
                                                                        f"{param_label(m.group(2))}")
    m = re.fullmatch(r"overrides\.mechanisms\.([\w-]+)\.params\.(\w+)", path)
    if m:
        return f"{m.group(1)} {param_label(m.group(2))}"
    m = re.fullmatch(r"overrides\.paths\.([\w-]+)\.(\w+)", path)
    if m:
        return tr(f"경로 {m.group(1)} {param_label(m.group(2))}", f"path {m.group(1)} {param_label(m.group(2))}")
    m = re.fullmatch(r"tolerances\.([\w-]+)\.(\w+)", path)
    if m:
        return tr(f"공차 {m.group(1)} {param_label(m.group(2))}", f"tolerance {m.group(1)} {param_label(m.group(2))}")
    return esc(path.replace("_", " ").replace(".", " › "))


def point_text(point: dict) -> str:
    return ", ".join(f"{axis_word(k)} = {num(v, 6) if isinstance(v, (int, float)) else esc(v)}"
                     for k, v in (point or {}).items())


def scenario_text(sc: dict) -> str:
    """A scenario in one line: operating point, request, faults, protection and variants."""
    if not sc:
        return ""
    rq = sc.get("request")
    req = (tr(f"요청 {num(sc.get('torque_Nm', 0))} N·m", f"request {num(sc.get('torque_Nm', 0))} N·m") if not rq else
           tr(f"요청 {num(rq.get('T0_Nm'))} → {num(rq.get('T1_Nm'))} N·m", f"request {num(rq.get('T0_Nm'))} → "
                                                                          f"{num(rq.get('T1_Nm'))} N·m"))
    fs = "; ".join(_fault_text(f) + f" @ {num(f.get('t_ms'), 4)} ms" for f in sc.get("faults") or []) or \
        tr("고장 없음", "no fault")
    extra = []
    if not sc.get("protection", True):
        extra.append(tr("보호 끔", "protection off"))
    if sc.get("reaction_override"):
        extra.append(tr(f"반응 강제 {react(sc['reaction_override'])}", f"forced reaction {react(sc['reaction_override'])}"))
    if sc.get("overrides"):
        extra.append(tr(f"설계 변형 {len(sc['overrides'])}건", f"{len(sc['overrides'])} design variant(s)"))
    if sc.get("tolerances"):
        extra.append(tr("공차 적용", "tolerances applied"))
    return (f"{num(sc.get('speed_rpm'))} rpm · {req} · {fs}" + (f" · {', '.join(extra)}" if extra else "")
            + tr(f" · 초기각 {num(sc.get('theta0_deg', 0.0))}°", f" · initial angle {num(sc.get('theta0_deg', 0.0))}°"))


def _fault_text(f: dict) -> str:
    p = f.get("params") or {}
    ps = ", ".join(f"{esc(param_label(a))}={esc(value_label(b))}" for a, b in p.items())
    return esc(fault_word(f["kind"])) + (f" ({ps})" if ps else "")


def fault_insight(res: dict) -> Insight:
    sc = res.get("scenario") or {}
    ev = res.get("evaluation") or {}
    overall = _overall({x["id"]: x["verdict"] for x in ev.get("tsr") or []})
    s = res.get("summary") or {}
    faults = sc.get("faults") or []
    what = "; ".join(_fault_text(f) + f" @ {num(f.get('t_ms'), 4)} ms" for f in faults) or tr("고장 없음", "no fault")
    fails = [x for x in ev.get("tsr") or [] if x["verdict"] == "FAIL"]
    head = (tr(f"{num(sc.get('speed_rpm'))} rpm · {what}: ", f"{num(sc.get('speed_rpm'))} rpm · {what}: ")
            + (tr("요구 위반 — ", "requirements violated — ") + ", ".join(esc(x["id"]) for x in fails) if fails else
               tr("모든 적용 요구 통과", "every applicable requirement met") if overall == "PASS" else
               tr("판단불가 항목 있음", "undecided items")))
    cyc = bridge_cycling(res)
    if cyc:
        head += tr(f" — 단, 브리지가 {react(cyc['states'][0])} ↔ {react(cyc['states'][1])} 순환 "
                   f"(주기 ≈ {num(cyc['period_ms'], 3)} ms)",
                   f" — but the bridge cycles {react(cyc['states'][0])} ↔ {react(cyc['states'][1])} "
                   f"(period ≈ {num(cyc['period_ms'], 3)} ms)")
    ins = Insight(headline=head, verdict=overall)
    det = [e for e in res.get("events") or [] if e["kind"] == "detection"]
    act = [e for e in res.get("events") or [] if e["kind"] == "actuation"]
    ins.metrics += [
        (tr("첫 검출", "first detection"), f"{esc(det[0]['source'])} @ {_ms(det[0]['t'])}" if det else tr("없음", "none"),
         "ok" if det else ("bad" if faults else "info")),
        (tr("첫 반응", "first reaction"), f"{esc(react(act[0].get('reaction')))} @ "
                                          f"{_ms(act[0]['t'])}" if act else tr("없음", "none"), "info"),
        (tr("최대 상전류", "peak phase current"), q(s.get("i_phase_peak_A"), "A"), "info"),
        (tr("V_dc 최대", "max V_dc"), q(s.get("v_dc_max_V"), "V"), "info"),
        (tr("축 토크 범위", "shaft torque range"), f"{num(s.get('T_shaft_min_Nm'))} … {num(s.get('T_shaft_max_Nm'))} N·m",
         "info")]
    # 0) a bridge that keeps switching between two states after the fault (e.g. a hardware selection by the DC
    #     voltage that freewheels, recharges the link and short-circuits again)
    if cyc:
        sc_ = ins.section(tr("브리지 반복 전환 (순환)", "the bridge keeps switching (cycling)"),
                          tr("같은 두 상태 사이를 고장 뒤 계속 오갑니다: 전환마다 브리지와 DC-link가 다시 과도 상태가 됩니다.",
                             "it goes back and forth between the same two states after the fault: every change starts a "
                             "new transient of the bridge and the DC link."))
        a, b_ = cyc["states"]
        sc_.add(tr(f"{esc(react(a))} ↔ {esc(react(b_))}: 전환 {cyc['changes']}회, 주기 ≈ {num(cyc['period_ms'], 3)} ms "
                   f"({num(cyc['frequency_Hz'], 3)} Hz), 첫 전환 {_ms(cyc['first_s'])}",
                   f"{esc(react(a))} ↔ {esc(react(b_))}: {cyc['changes']} changes, period ≈ "
                   f"{num(cyc['period_ms'], 3)} ms ({num(cyc['frequency_Hz'], 3)} Hz), first at {_ms(cyc['first_s'])}"),
                "warn")
        sc_.add(tr(f"순환 중 DC-link {num(cyc['v_min_V'], 4)} … {num(cyc['v_max_V'], 4)} V, 축 토크 "
                   f"{num(cyc['T_min_Nm'], 4)} … {num(cyc['T_max_Nm'], 4)} N·m",
                   f"while cycling the DC link spans {num(cyc['v_min_V'], 4)} … {num(cyc['v_max_V'], 4)} V, the shaft "
                   f"torque {num(cyc['T_min_Nm'], 4)} … {num(cyc['T_max_Nm'], 4)} N·m"), "warn",
                tr("6SO 구간마다 역기전력이 링크를 다시 충전합니다(에너지가 HV 쪽으로: C2). 이 설계의 요구는 순환 자체나 링크가 "
                   "방전 한계 아래에 머무는지를 묻지 않습니다 — 요구가 빠진 곳입니다.",
                   "every six-switch-off interval lets the back-EMF recharge the link (energy fed to the HV side: C2). "
                   "No requirement of this design asks about the cycling itself or whether the link stays below the "
                   "discharge limit - a missing requirement.") if "six_switch_off" in cyc["states"] else "")
    # 1) the causal chain
    sec = ins.section(tr("인과 사슬: 고장 → 측정·추정 → 감시·검출 → 반응 명령 → 실제 브리지 → 결과",
                         "causal chain: fault → measurement / estimate → detection → reaction command → actual bridge → "
                         "result"))
    lev = {"fault": "bad", "detection": "warn", "actuation": "info", "reaction_blocked": "bad",
           "reaction_conflict": "warn", "recovery": "info", "fault_cleared": "ok", "out_of_model": "open",
           "plant": "info", "controller": "info", "driver": "warn", "reaction_kept": "info"}
    kind_word = {"fault": tr("고장", "fault"), "detection": tr("검출", "detection"), "actuation": tr("반응", "reaction"),
                 "reaction_blocked": tr("반응 차단", "reaction blocked"), "reaction_conflict": tr("반응 충돌", "conflict"),
                 "reaction_kept": tr("유지", "kept"), "recovery": tr("복귀", "recovery"),
                 "fault_cleared": tr("고장 소멸", "fault cleared"), "out_of_model": tr("모델 밖", "out of model"),
                 "plant": tr("플랜트", "plant"), "controller": tr("제어기", "controller"), "driver": tr("게이트 드라이버",
                                                                                                      "gate driver")}
    shown = 0
    for e in res.get("events") or []:
        if e["kind"] in ("bridge",):
            continue
        if shown >= 24:
            sec.add(tr("… (나머지는 사건 타임라인 표)", "… (the rest in the event table)"), "info")
            break
        fault = e["kind"] in ("fault", "fault_cleared")
        src = fault_word(e["source"]) if fault else e["source"]
        txt = _fault_text(e["fault"]) if e.get("fault") else esc(e["text"])
        sec.add(f"<b>{_ms(e['t'], 5)}</b> · {kind_word.get(e['kind'], esc(e['kind']))} · {esc(src)}",
                lev.get(e["kind"], "info"), txt)
        shown += 1
    # 2) commanded vs actual
    fin = s.get("final_actual") or []
    if s.get("final_bridge") and fin:
        s2 = ins.section(tr("명령 vs 실제 (끝 시점의 각 다리)", "commanded vs actual (each leg at the end)"))
        s2.add(tr(f"브리지 명령: <b>{esc(react(s['final_bridge']))}</b>",
                  f"bridge command: <b>{esc(react(s['final_bridge']))}</b>"), "info")
        for ph, a in zip("abc", fin):
            bad = any(w in a for w in ("unavailable", "open", "short"))
            s2.add(tr(f"다리 {ph}: {esc(a)}", f"leg {ph}: {esc(a)}"), "warn" if bad else "info")
    # 3) requirements
    s3 = ins.section(tr("요구별 판정 (참값 기준: SG → FSR → TSR)", "per requirement (judged on the truth: SG → FSR → TSR)"),
                     tr("감시 메커니즘은 측정만 봅니다; 요구 판정은 플랜트 참값으로 합니다.",
                        "mechanisms see measurements only; requirements are judged on the plant truth."))
    for g in ev.get("sg") or []:
        s3.add(tr(f"<b>{esc(g['id'])}</b> {esc(g['text'])} (ASIL {esc(g.get('asil') or '—')}, FTTI "
                  f"{_ms(g.get('ftti_s'), 3)}): 인버터 근거 {verdict_ko(g['inverter_evidence'])}",
                  f"<b>{esc(g['id'])}</b> {esc(g['text'])} (ASIL {esc(g.get('asil') or '—')}, FTTI "
                  f"{_ms(g.get('ftti_s'), 3)}): inverter evidence {verdict_ko(g['inverter_evidence'])}"),
               VL.get(g["inverter_evidence"], "info"),
               tr("차량 수준 안전 승인이 아님 (차량 동역학·운전자 통제 가능성은 이 시뮬레이션 밖)",
                  "not a vehicle safety approval (vehicle dynamics and controllability are outside this simulation)")
               + (f" · {tr('차량 지표', 'vehicle indicator')}: Δv {num(g['vehicle'].get('delta_v_mps'), 3)} m/s "
                  f"({esc(g['vehicle'].get('criterion', ''))})" if (g.get("vehicle") or {}).get("status") == "INDICATOR"
                  else ""))
    for x in ev.get("tsr") or []:
        if x["verdict"] == "NOT_APPLICABLE":
            continue
        s3.add(f"<b>{esc(x['id'])}</b> ({esc(x['fsr'])} → {esc(', '.join(x['sg']))}): {esc(x['text'])} — "
               f"<b>{verdict_ko(x['verdict'])}</b>", VL.get(x["verdict"], "info"),
               esc(x.get("detail", "")) + (f" [{reason_word(x['reason'])}]" if x.get("reason") else ""))
    # 4) timing chain
    s4 = ins.section(tr("시간 사슬 (사건 정의: t_F 고장, t_D 할당된 메커니즘의 검출, t_S 안전 조건 도달·유지)",
                        "timing chain (t_F fault, t_D detection by an allocated mechanism, t_S safe condition reached "
                        "and held)"))
    for f in ev.get("fsr") or []:
        tl = f["timeline"]
        b = f.get("budgets") or {}
        if tl.get("t_D") is None:
            s4.add(tr(f"<b>{esc(f['id'])}</b>: {esc(tl.get('why', ''))}", f"<b>{esc(f['id'])}</b>: {esc(tl.get('why', ''))}"),
                   "bad" if tl.get("undetected") else "info")
            continue
        s4.add(tr(f"<b>{esc(f['id'])}</b>: FDTI {_ms(tl.get('FDTI'))} (예산 {_ms(b.get('FDTI_s'))}) · FRTI "
                  f"{_ms(tl.get('FRTI'))} (예산 {_ms(b.get('FRTI_s'))}) · FHTI {_ms(tl.get('FHTI'))}",
                  f"<b>{esc(f['id'])}</b>: FDTI {_ms(tl.get('FDTI'))} (budget {_ms(b.get('FDTI_s'))}) · FRTI "
                  f"{_ms(tl.get('FRTI'))} (budget {_ms(b.get('FRTI_s'))}) · FHTI {_ms(tl.get('FHTI'))}"),
               VL.get(f["verdict"], "info"), esc(tl.get("why", "")))
    # 5) where it failed and what would change it
    s5 = ins.section(tr("어디서 실패했고 무엇을 바꾸면 되는가", "where it failed and what would change it"))
    _suggest(s5, res)
    # 6) precision and scope
    s6 = ins.section(tr("모델 정밀도·데이터 의존성·적용 범위", "model precision, data dependencies, scope"))
    pr = res.get("precision") or {}
    z = pr.get("zeno") or {}
    s6.add(tr(f"PWM 모델 {esc(pr.get('pwm_model'))}, 최대 스텝 {num((pr.get('h_max_s') or 0) * 1e6)} µs, 에너지 수지 잔차 "
              f"{num(pr.get('energy_relative_residual'), 2)} (처리량 대비)",
              f"PWM model {esc(pr.get('pwm_model'))}, max step {num((pr.get('h_max_s') or 0) * 1e6)} µs, energy ledger "
              f"residual {num(pr.get('energy_relative_residual'), 2)} (of the throughput)"), "info")
    if z.get("intervals"):
        s6.add(tr(f"경계에서 모드가 빠르게 바뀌는 구간 {z['intervals']}개({num(z['time_s'] * 1e6)} µs)는 1 µs 시간 스텝으로 적분 "
                  f"(사건 위치 정밀도 1 µs)", f"{z['intervals']} interval(s) of rapid mode changes ({num(z['time_s'] * 1e6)} µs) "
                                               f"integrated with 1 µs time steps (event location to 1 µs)"), "warn")
    for n in pr.get("notes") or []:
        s6.add(esc(n), "info")
    s6.add(tr("판정 범위: 이 궤적 하나 (운전점·고장 시점·초기각·공차가 다르면 다시 실행하거나 캠페인으로 탐색)",
              "scope: this one trajectory (another operating point, fault instant, initial angle or tolerance needs its "
              "own run or a campaign)"), "info", esc((ev.get("scope") or {}).get("text", "")))
    return ins.nonempty()


def _suggest(sec, res):
    ev = res.get("evaluation") or {}
    evs = res.get("events") or []
    s = res.get("summary") or {}
    said = False
    for f in ev.get("fsr") or []:
        tl = f["timeline"]
        if tl.get("undetected"):
            sec.add(tr(f"<b>{esc(f['id'])}</b>: 위험 관련 요구가 위반됐는데 할당된 메커니즘이 아무도 검출하지 못함 — 제어와 "
                       f"독립된 측정(센서·메시지 사본·위치 추정)을 가진 메커니즘이 필요합니다",
                       f"<b>{esc(f['id'])}</b>: a hazard-related requirement is violated and no allocated mechanism "
                       f"detected it — a mechanism with a measurement independent of control (sensor, message copy, "
                       f"position estimate) is needed"), "bad", esc(tl.get("why", "")))
            said = True
    blocked = [e for e in evs if e["kind"] == "reaction_blocked" and "disabled" not in e["text"]]
    if blocked:
        sec.add(tr(f"반응이 차단됨: {esc(blocked[0]['text'])} — 경로의 잠재 고장 진단(주기 시험) 또는 독립 경로",
                   f"reaction blocked: {esc(blocked[0]['text'])} — diagnose the path's latent faults (periodic test) or "
                   f"add an independent path"), "bad")
        said = True
    conf = [e for e in evs if e["kind"] == "reaction_conflict"]
    if conf:
        sec.add(tr(f"반응 충돌: {esc(conf[0]['text'])} — HW·SW 경로가 같은 정보(속도 출처·히스테리시스)로 결정하는지 확인",
                   f"reaction conflict: {esc(conf[0]['text'])} — make the HW and SW paths decide from the same information "
                   f"(speed source, hysteresis)"), "warn")
        said = True
    fails = {x["id"]: x for x in ev.get("tsr") or [] if x["verdict"] == "FAIL"}
    for x in fails.values():
        c = x.get("criterion") or {}
        if x["type"] == "bound" and c.get("quantity") == "v_dc":
            fin = s.get("final_bridge")
            if fin == "six_switch_off":
                sec.add(tr("DC-link 과전압이 6SO에서 발생: 정류 개시 속도 위에서는 6SO가 링크를 충전합니다 — 정책의 속도 판단 출처"
                           "(고장 난 센서에서 온 속도인지)와 ASC 선택 규칙을 확인", "DC-link over-voltage under 6SO: above the "
                           "rectification onset six-switch-off charges the link — check the policy's speed source (a "
                           "speed from the faulty sensor?) and the ASC rule"), "bad")
            else:
                sec.add(tr("DC-link 과전압: 검출·경로 지연(디바운스, 태스크 주기, 필터)을 줄이거나 임계값을 낮추기 — 캠페인으로 "
                           "임계값·지연의 실패 경계를 찾을 수 있습니다", "DC-link over-voltage: shorten detection and path "
                           "delays (debounce, task period, filter) or lower the threshold — a campaign finds the failure "
                           "boundary of threshold and delay"), "bad")
            said = True
        elif x["type"] == "bound" and c.get("quantity") == "i_phase_abs":
            dets = [e["source"] for e in evs if e["kind"] == "detection"]
            sec.add(tr(f"상전류가 소자 한계를 넘음 (검출: {esc(', '.join(dict.fromkeys(dets)) or '없음')}) — 과전류 비교기가 제어와 "
                       f"같은 센서·전원을 쓰는지(공통 원인), 반응이 단락 쪽 ASC인지 확인", f"phase current above the device limit "
                       f"(detected by: {esc(', '.join(dict.fromkeys(dets)) or 'none')}) — check whether the over-current "
                       f"comparator shares sensors / supply with control (common cause) and whether the reaction is the "
                       f"ASC of the shorted side"), "bad")
            said = True
        elif x["type"] == "no_false_reaction":
            sec.add(tr("고장 없이 반응: 감시기의 동적 허용(지연·응답 시정수·램프)이나 디바운스가 정상 과도를 덮지 못함",
                       "reaction without a fault: the monitor's dynamic allowance (delay, response, ramp) or debounce does "
                       "not cover the healthy transient"), "bad")
            said = True
        elif x["type"] == "safe_state":
            rec = [e for e in evs if e["kind"] == "recovery"]
            sec.add(tr("안전 조건에 제시간에 도달·유지하지 못함" + (" — 복귀 정책이 유지 시간보다 빨리 재시동" if rec else
                                                        " — 반응 후보 비교로 이 운전점에서 유효한 반응을 확인"),
                       "the safe condition is not reached and held in time" + (
                           " — the recovery policy restarts before the hold time" if rec else
                           " — compare the reaction candidates at this operating point")), "bad")
            said = True
        elif x["type"] == "torque_window":
            sec.add(tr(f"{esc(x['id'])}: 허용 창 밖 토크가 허용 시간보다 오래 지속 — 검출(FDTI)과 반응(FRTI)을 창 허용 시간 안으로",
                       f"{esc(x['id'])}: torque outside its window longer than tolerated — bring detection (FDTI) and "
                       f"reaction (FRTI) inside the tolerated time"), "bad")
            said = True
    rec = [e for e in evs if e["kind"] == "recovery" and "exhausted" in e["text"]]
    if rec:
        sec.add(tr("복귀 시도가 모두 다시 트립: 고장이 남아 있는 동안의 재시동 — 재시동 전에 고장 원인 진단을 요구하거나 래치",
                   "every recovery attempt tripped again: restarting while the fault persists — require a diagnosis before "
                   "restarting, or latch"), "warn")
        said = True
    if res.get("status") != "completed":
        sec.add(tr("시뮬레이션이 모델 밖에서 멈춤: 그 이후 창은 판단불가 (필요한 데이터가 메시지에 적혀 있음)",
                   "the simulation left the model: every window after that point is UNKNOWN (the message names the data "
                   "needed)"), "open", esc(res.get("stop_reason") or ""))
        said = True
    if not said:
        sec.add(tr("이 궤적에서는 실패한 요구가 없습니다 — 다른 조건(고장 시점·크기·운전점·공차)은 캠페인으로 확인",
                   "no requirement fails on this trajectory — other conditions (fault instant, size, operating point, "
                   "tolerances) need a campaign"), "ok")


def compare_insight(cmp: dict) -> Insight:
    ok = cmp.get("passing_candidates") or []
    rows = [r for r in cmp.get("rows") or [] if r["candidate"] != "none"]
    open_ = [r["candidate"] for r in rows if r["overall"] == "UNKNOWN"]
    if ok:
        head, verdict = (tr("모든 요구를 만족하는 반응: ", "reactions meeting every requirement: ")
                         + ", ".join(esc(react(c)) for c in ok)), "PASS"
    elif open_:
        head, verdict = (tr("모든 요구를 만족한다고 보인 반응 없음 — 판단불가 후보: ", "no reaction shown to meet every "
                                                                        "requirement — undecided: ")
                         + ", ".join(esc(react(c)) for c in open_)), "UNKNOWN"
    else:
        head, verdict = tr("모든 반응 후보가 요구를 위반 — 이 조건에서 실행 가능한 안전 반응이 없습니다",
                           "every reaction candidate violates a requirement — no executable safe reaction under this "
                           "condition"), "FAIL"
    ins = Insight(headline=head, verdict=verdict)
    sec = ins.section(tr("같은 초기 조건·같은 고장에서 후보별", "per candidate, same initial condition and fault"))
    for r in cmp["rows"]:
        m = r["metrics"]
        steps = strategy_log_text(r.get("strategy_log"))
        sec.add(f"<b>{esc(react(r['candidate']))}</b>: "
                f"{verdict_ko(r['overall'])}" + (tr(f" — 위반 {esc(', '.join(r['failing']))}", f" — violated "
                                                                                       f"{esc(', '.join(r['failing']))}")
                                                 if r["failing"] else ""),
                VL.get(r["overall"], "info"),
                tr(f"최대 상전류 {q(m['i_phase_peak_A'], 'A')}, 최저 i_d {q(m.get('i_d_min_A'), 'A')}, 최대 제동 토크 "
                   f"{q(m.get('T_brake_peak_Nm'), 'N·m')}, V_dc 최대 {q(m['v_dc_max_V'], 'V')}; 실제 다리: "
                   f"{esc('; '.join(r['final_actual']))}",
                   f"peak phase current {q(m['i_phase_peak_A'], 'A')}, min i_d {q(m.get('i_d_min_A'), 'A')}, peak "
                   f"braking torque {q(m.get('T_brake_peak_Nm'), 'N·m')}, max V_dc {q(m['v_dc_max_V'], 'V')}; actual "
                   f"legs: {esc('; '.join(r['final_actual']))}")
                + (tr(f"<br>단계: {steps}", f"<br>steps: {steps}") if steps else ""))
    return ins.nonempty()


def campaign_insight(camp: dict) -> Insight:
    sc = camp.get("scope") or {}
    summ = camp.get("summary") or {}
    o = summ.get("overall") or {}
    head = tr(f"캠페인 {summ.get('n', 0)}회: 통과 {o.get('PASS', 0)}, 실패 {o.get('FAIL', 0)}, 판단불가 {o.get('UNKNOWN', 0)} — "
              f"탐색 집합 {verdict_ko(sc.get('explored_set', 'UNKNOWN'))}, 연속 영역은 보장하지 않음",
              f"campaign of {summ.get('n', 0)} runs: PASS {o.get('PASS', 0)}, FAIL {o.get('FAIL', 0)}, UNKNOWN "
              f"{o.get('UNKNOWN', 0)} — explored set {sc.get('explored_set')}, the continuous region is not established")
    ins = Insight(headline=head, verdict=sc.get("explored_set"))
    s1 = ins.section(tr("실패 경계 (축을 따라 이분 탐색한 구간)", "failure boundaries (bisected along the axis)"))
    for b in camp.get("boundaries") or []:
        others = point_text(b.get("others") or {})
        s1.add(tr(f"{axis_word(b['axis'])}: {verdict_ko(b['verdicts'][0])} → {verdict_ko(b['verdicts'][1])} 사이 "
                  f"[{num(b['between'][0], 6)}, {num(b['between'][1], 6)}]" + (f" (나머지 {others})" if others else ""),
                  f"{axis_word(b['axis'])}: {verdict_ko(b['verdicts'][0])} → {verdict_ko(b['verdicts'][1])} within "
                  f"[{num(b['between'][0], 6)}, {num(b['between'][1], 6)}]" + (f" (others {others})" if others else "")),
               "warn")
    if not camp.get("boundaries"):
        s1.add(tr("판정이 바뀌는 이웃 격자점이 없음 (격자 안에서는 경계를 찾지 못함 — 경계가 없다는 뜻은 아님)",
                  "no neighbouring grid points with different verdicts (no boundary found on this grid — not a proof "
                  "that none exists)"), "info")
    s2 = ins.section(tr("최악값 — 각각 한 실행의 궤적에서", "worst values — each from ONE run's trajectory"))
    for k, w in (camp.get("worst") or {}).items():
        if w.get("run") is None:
            s2.add(tr(f"{metric_word(k)} = {num(w['value'])}: 서로 다른 실행의 값을 더한 상한일 뿐, 실제 궤적이 아님 — 한 실행의 "
                      f"최악 FHTI와 비교", f"{metric_word(k)} = {num(w['value'])}: a sum over possibly different runs — a "
                                         f"bound, not a trajectory; compare with the worst FHTI of one run"), "warn")
        else:
            s2.add(tr(f"{metric_word(k)} = {num(w['value'])} (실행 {w['run']}: {point_text(w['point'])})",
                      f"{metric_word(k)} = {num(w['value'])} (run {w['run']}: {point_text(w['point'])})"), "info")
    s3 = ins.section(tr("민감도 (축을 따라 움직인 폭)", "sensitivity (how much a quantity moves along an axis)"))
    top = sorted(camp.get("sensitivity") or [], key=lambda x: -abs(x.get("mean_range", x.get("spearman", 0.0)) or 0.0))
    for x in top[:10]:
        val = x.get("mean_range", x.get("spearman"))
        how = (tr("다른 축 고정, 축을 따라 변한 폭의 평균", "mean range along the axis, others fixed") if "mean_range" in x
               else tr("순위 상관", "rank correlation"))
        s3.add(f"{axis_word(x['axis'])} → {metric_word(x['metric'])}: {num(val)} ({how})", "info")
    s4 = ins.section(tr("반례 (저장·재실행 가능)", "counterexamples (can be saved and re-run)"))
    for c in camp.get("counterexamples") or []:
        s4.add(f"<b>{esc(c['id'])}</b>: {esc(', '.join(c['failing']))}", "bad", scenario_text(c.get("scenario")))
    s5 = ins.section(tr("적용 범위", "scope"))
    s5.add(esc(sc.get("explored_text", "")), VL.get(sc.get("explored_set"), "info"))
    s5.add(tr("연속 운전영역은 표본으로 보장되지 않습니다 (반응 임계값에서 결과가 불연속)",
              "the continuous operating region is not established by samples (the outcome is discontinuous at reaction "
              "thresholds)"), "open")
    return ins.nonempty()


def validation_insight(val: dict) -> Insight:
    head = tr(f"플랜트 검증 {val['passed']}/{val['total']} 통과 — 폐형식, 독립 abc 정식화, 에너지 수지, 수치 수렴",
              f"plant validation {val['passed']}/{val['total']} passed — closed forms, an independent abc formulation, "
              f"energy balance, numerical convergence")
    ins = Insight(headline=head, verdict="PASS" if val["passed"] == val["total"] else "FAIL")
    sec = ins.section(tr("비교 항목", "comparisons"),
                      tr("같은 방정식을 다른 적분기로 푼 비교(행렬 지수)는 수치 검증일 뿐, 물리 검증은 독립 정식화·폐형식으로 합니다.",
                         "a comparison with the same equations under another solver (matrix exponential) checks the "
                         "numerics only; the physics is checked by an independent formulation and closed forms."))
    for r in val["rows"]:
        sec.add(f"{esc(r['case'])} · {esc(r['quantity'])}: {num(r['plant'], 5)} vs {num(r['reference'], 5)} "
                f"(|Δ| {num(r['abs_error'], 3)} ≤ {num(r['tolerance'], 3)})", "ok" if r["pass"] else "bad",
                f"{esc(r['reference_kind'])} — {esc(r['basis'])}")
    return ins.nonempty()


CHECK_WORDS = {"ASIL": ("ASIL", "ASIL"), "FTTI": ("FTTI", "FTTI"), "safe state": ("안전 상태", "safe state"),
               "refined by an FSR": ("FSR로 구체화", "refined by an FSR"),
               "ASIL inheritance": ("ASIL 상속", "ASIL inheritance"),
               "FDTI / FRTI budgets": ("FDTI·FRTI 예산", "FDTI / FRTI budgets"),
               "budgets vs FTTI": ("예산 합 vs FTTI", "budgets vs FTTI"),
               "allocated mechanisms": ("할당 메커니즘", "allocated mechanisms"),
               "detection latency vs FDTI budget": ("검출 지연 상한 vs FDTI 예산", "detection latency vs FDTI budget"),
               "refined by TSRs": ("TSR로 구체화", "refined by TSRs"),
               "hazard-related TSR": ("위험 판정 TSR", "hazard-related TSR"),
               "safe-state TSR": ("안전 상태 TSR", "safe-state TSR"),
               "warning / degradation": ("경고·성능 저하", "warning / degradation"),
               "verification methods": ("검증 방법", "verification methods"),
               "allocation": ("HW/SW 할당", "allocation"), "rationale of the value": ("한계값 근거", "rationale"),
               "tolerance vs FTTI": ("허용 시간 vs FTTI", "tolerance vs FTTI"),
               "within vs FRTI budget": ("도달 시간 vs FRTI 예산", "within vs FRTI budget"),
               "hold time": ("유지 시간", "hold time"), "traced to an FSR": ("FSR 추적", "traced to an FSR"),
               "latent-fault test": ("잠재 고장 시험", "latent-fault test"),
               "diagnostic coverage claim": ("진단 커버리지 주장", "diagnostic coverage claim"),
               "executable on its paths": ("경로에서 실행 가능", "executable on its paths"),
               "used": ("사용 여부", "used"), "bounded step": ("단계 시간 상한", "bounded step"),
               "measured exit on a hardware path": ("HW 경로의 측정 종료 조건", "measured exit on a hardware path"),
               "reaction latency vs FRTI budget": ("반응 지연 vs FRTI 예산", "reaction latency vs FRTI budget"),
               "default rule": ("기본 규칙", "default rule"), "priority list": ("우선순위 목록", "priority list"),
               "no single declared common cause": ("단일 공통 원인 없음", "no single declared common cause"),
               "budgets judged on trajectories": ("예산의 궤적 판정", "budgets judged on trajectories")}
FINDING_WORDS = {"OK": ("OK", "OK"), "INCONSISTENT": ("모순", "inconsistent"), "MISSING": ("누락", "missing"),
                 "WARNING": ("경고", "warning"), "NOTE": ("참고", "note")}


def check_word(c: str) -> str:
    """A static-review check as people name it (in the current language)."""
    w = CHECK_WORDS.get(c)
    return tr(*w) if w else esc(c)


def finding_word(s: str) -> str:
    w = FINDING_WORDS.get(s)
    return tr(*w) if w else esc(s)


def safety_case_insight(review: dict | None, matrix: dict | None = None) -> Insight:
    """The assessor's reading of the evidence: the static review first (contradictions and gaps, then warnings), then
    the verification matrix (requirements failing or never exercised, the worst timings against the budgets, faults
    no mechanism detected).  Every statement comes from the two results; a PASS is about the simulated trajectories
    only."""
    review = review or {}
    titles = {}
    for r in list((matrix or {}).get("rows") or []) + list((matrix or {}).get("skipped") or []):
        t = r.get("title") or {}
        titles[r["key"]] = t.get(tr("ko", "en")) or t.get("en") or r["key"] if isinstance(t, dict) else str(t)

    def sc(k):                                      # a scenario as people name it (the key stays in the tables)
        return esc(titles.get(k, k))
    c = review.get("counts") or {}
    n_bad = c.get("INCONSISTENT", 0) + c.get("MISSING", 0)
    rows = (matrix or {}).get("rows") or []
    cov = (matrix or {}).get("coverage") or {}
    failing = {q: v["failing_in"] for q, v in cov.items() if v.get("fail")}
    never = list((matrix or {}).get("not_exercised") or [])
    if not review and not rows:
        return Insight(headline=tr("정적 설계 검토 또는 검증 매트릭스를 실행하세요.",
                                   "Run the static design review or the verification matrix."))
    if n_bad:
        head, verdict = tr(f"설계 근거에 모순 {c.get('INCONSISTENT', 0)}건 · 누락 {c.get('MISSING', 0)}건 — 심사 전에 "
                           f"해결할 항목", f"{c.get('INCONSISTENT', 0)} contradiction(s) and {c.get('MISSING', 0)} gap(s) "
                                       f"in the design evidence — to resolve before an assessment"), "FAIL"
    elif failing:
        head, verdict = tr(f"정적 검토의 모순·누락은 없지만 검증 매트릭스에서 요구 {len(failing)}개가 실패 — "
                           f"{', '.join(sorted(failing))}",
                           f"no contradiction or gap in the static review, but {len(failing)} requirement(s) fail in "
                           f"the verification matrix — {', '.join(sorted(failing))}"), "FAIL"
    elif c.get("WARNING", 0) or never or not rows or not review:
        what = []
        if c.get("WARNING", 0):
            what.append(tr(f"경고 {c['WARNING']}건", f"{c['WARNING']} warning(s)"))
        if never:
            what.append(tr(f"한 번도 발동되지 않은 요구 {len(never)}개", f"{len(never)} requirement(s) never exercised"))
        if not rows:
            what.append(tr("검증 매트릭스 미실행", "verification matrix not run"))
        if not review:
            what.append(tr("정적 검토 미실행", "static review not run"))
        head, verdict = tr("모순·누락·실패 없음 — 남은 항목: ", "no contradiction, gap or failure — open: ") + \
            ", ".join(what), "UNKNOWN"
    else:
        head, verdict = tr(f"모순·누락 없음, 검증 매트릭스 {len(rows)}개 궤적에서 실패한 요구 없음 (이 궤적들에 대해서만)",
                           f"no contradiction or gap; no requirement fails on the {len(rows)} trajectories of the "
                           f"verification matrix (for these trajectories only)"), "PASS"
    ins = Insight(headline=head, verdict=verdict)
    if review:
        ins.metrics += [(tr("모순", "inconsistent"), str(c.get("INCONSISTENT", 0)),
                         "bad" if c.get("INCONSISTENT") else "ok"),
                        (tr("누락", "missing"), str(c.get("MISSING", 0)), "bad" if c.get("MISSING") else "ok"),
                        (tr("경고", "warnings"), str(c.get("WARNING", 0)), "warn" if c.get("WARNING") else "ok")]
    if rows:
        ins.metrics += [(tr("궤적", "trajectories"), str(len(rows)), "info"),
                        (tr("실패 요구", "failing requirements"), str(len(failing)), "bad" if failing else "ok"),
                        (tr("미발동 요구", "never exercised"), str(len(never)), "warn" if never else "ok")]
    finds = review.get("findings") or []
    s1 = ins.section(tr("모순·누락 — 선언된 데이터끼리 맞지 않거나 사슬에 빠진 것", "contradictions and gaps — declared data "
                                                                     "that disagree or are missing from the chain"))
    for f in finds:
        if f["status"] in ("INCONSISTENT", "MISSING"):
            s1.add(f"<b>{esc(f['element'])}</b> · {check_word(f['check'])} ({finding_word(f['status'])})", "bad",
                   esc(f["detail"]))
    s2 = ins.section(tr("경고 — 허용되지만 약한 곳", "warnings — allowed but weak"))
    for f in finds:
        if f["status"] == "WARNING":
            s2.add(f"<b>{esc(f['element'])}</b> · {check_word(f['check'])}", "warn", esc(f["detail"]))
    budgets = {}
    for r in review.get("latency") or []:
        budgets.setdefault(r["fsr"], (r.get("fdti_budget_s"), r.get("frti_budget_s")))
    s3 = ins.section(tr("검출 지연 범위 (선언된 매개변수, 큰 계단 고장)", "detection latency bounds (declared parameters, "
                                                                "gross step fault)"),
                     tr("디바운스 + 센서 지연(+ 시간 제한) … 여기에 태스크 주기 하나(+ 창 지연)까지. 최소가 예산을 넘으면 모순, 최대만 "
                        "넘으면 고장 시점에 따라 늦음(경고). 경계 근처 고장은 더 늦게 검출됩니다(캠페인).",
                        "debounce + sensor delay (+ timeout) … plus up to one task period (+ window delay). The minimum "
                        "above the budget is a contradiction, only the maximum a warning (the fault instant decides). "
                        "A marginal fault is detected later (campaign)."))
    for r in review.get("latency") or []:
        hi, lo, bud = r["detection_s"], r.get("detection_min_s", r["detection_s"]), r["fdti_budget_s"]
        level = ("ok" if hi is None or bud is None or hi <= bud + 1e-12 else
                 "bad" if lo is not None and lo > bud + 1e-12 else "warn")
        span = _ms(hi) if lo is None or abs(hi - lo) < 1e-12 else f"{_ms(lo)} … {_ms(hi)}"
        s3.add(f"{esc(r['fsr'])} / <b>{esc(r['mechanism'])}</b>: {span} "
               + tr(f"(FDTI 예산 {_ms(bud)})", f"(FDTI budget {_ms(bud)})"), level, esc(r["detection_basis"]))
    if rows:
        s4 = ins.section(tr("검증 매트릭스 — 요구별", "verification matrix — per requirement"),
                         esc(matrix.get("statement", "")))
        for q_, v in cov.items():
            if v.get("fail"):
                s4.add(tr(f"<b>{esc(q_)}</b> 실패 — {'; '.join(sc(k) for k in v['failing_in'])} (재현 가능한 반례)",
                          f"<b>{esc(q_)}</b> fails — {'; '.join(sc(k) for k in v['failing_in'])} (reproducible "
                          f"counterexamples)"), "bad",
                       tr(f"발동 {v['exercised']}/{len(rows)} · 통과 {v['pass']} · 판단불가 {v['unknown']}",
                          f"exercised {v['exercised']}/{len(rows)} · pass {v['pass']} · unknown {v['unknown']}"))
        for q_ in never:
            s4.add(tr(f"<b>{esc(q_)}</b>: 어느 시나리오에서도 발동되지 않음 — 이 요구를 발동하는 시나리오가 필요",
                      f"<b>{esc(q_)}</b>: exercised by no scenario — a scenario that triggers it is needed"), "open")
        for q_, v in cov.items():
            if not v.get("fail") and v["exercised"] and v.get("unknown"):
                s4.add(tr(f"<b>{esc(q_)}</b>: 판단불가 {v['unknown']}건 (관측 창·허용 오차) — 통과 {v['pass']}",
                          f"<b>{esc(q_)}</b>: {v['unknown']} undecided (window, allowance) — pass {v['pass']}"), "open")
        ok_ = [q_ for q_, v in cov.items() if v["exercised"] and not v.get("fail") and not v.get("unknown")]
        if ok_:
            s4.add(tr(f"발동된 모든 궤적에서 통과: {', '.join(esc(x) for x in ok_)}",
                      f"pass on every trajectory that exercised them: {', '.join(esc(x) for x in ok_)}"), "ok")
        s5 = ins.section(tr("FSR별 최악 시간 — 각각 한 궤적", "worst times per FSR — each from one trajectory"),
                         tr("FDTI = t_D − t_F (첫 주 고장부터 할당 메커니즘의 첫 검출까지). 시간 판정 TSR이 있는 FSR만 판정에 들어갑니다.",
                            "FDTI = t_D − t_F (first primary fault to the first detection by an allocated mechanism). "
                            "Only an FSR with a timing TSR has it judged."))
        judged = {f["id"]: f.get("timing_tsr") or [] for g in review.get("trace") or [] for f in g["fsr"]}
        for fid, d in (matrix.get("timing") or {}).items():
            fd, fr = budgets.get(fid, (None, None))
            parts, over_any = [], False
            for key, bud in (("FDTI", fd), ("FRTI", fr), ("FHTI", None)):
                if key in d:
                    v = d[key]["value_s"]
                    over = bud is not None and v > bud + 1e-12
                    over_any = over_any or over
                    parts.append(f"{key} {_ms(v)} ({sc(d[key]['scenario'])})"
                                 + (tr(f" > 예산 {_ms(bud)}", f" > budget {_ms(bud)}") if over else ""))
            if not parts:
                continue
            j = judged.get(fid)
            note = ("" if not review else tr(f"판정: {', '.join(j)}", f"judged by {', '.join(j)}") if j else
                    tr("이 FSR에는 시간 판정 TSR이 없어 예산 초과가 판정에 들어가지 않음 — 시간 판정 TSR을 두거나 예산의 의미를 "
                       "밝히세요", "no timing TSR judges this FSR's budgets on trajectories — declare one or state "
                                   "what the budget means"))
            s5.add(f"<b>{esc(fid)}</b>: " + " · ".join(parts),
                   ("bad" if (j or not review) else "warn") if over_any else "info", note)
        s6 = ins.section(tr("고장 모드 (시뮬레이션 FMEA)", "failure modes (simulation-based FMEA)"))
        for r in rows:
            fm = r["fmea"]
            if fm["fault"] != "no fault" and not fm["detected_by"]:
                s6.add(tr(f"<b>{sc(r['key'])}</b>: 고장을 어느 메커니즘도 검출하지 못함 — {esc(fm['fault'])}",
                          f"<b>{sc(r['key'])}</b>: no mechanism detected the fault — {esc(fm['fault'])}"),
                       "bad" if fm["failing"] else "warn",
                       tr(f"실패 요구: {', '.join(fm['failing']) or '없음'}", f"failing: {', '.join(fm['failing']) or 'none'}"))
            elif fm["failing"]:
                rx = esc(react(fm["reaction"])) if fm["reaction"] else "—"
                s6.add(tr(f"<b>{sc(r['key'])}</b>: {esc(fm['detected_by'] or '—')} 검출 → {rx} → "
                          f"실패 {', '.join(fm['failing'])}", f"<b>{sc(r['key'])}</b>: detected by "
                                                              f"{esc(fm['detected_by'] or '—')} → {rx} "
                                                              f"→ failing {', '.join(fm['failing'])}"), "bad",
                       tr(f"최대 |i| {q(fm['i_phase_peak_A'], 'A')}, 최저 i_d {q(fm['i_d_min_A'], 'A')}, 최대 제동 토크 "
                          f"{q(fm['T_brake_max_Nm'], 'N·m')}, 최대 V_dc {q(fm['v_dc_max_V'], 'V')}",
                          f"peak |i| {q(fm['i_phase_peak_A'], 'A')}, min i_d {q(fm['i_d_min_A'], 'A')}, peak braking "
                          f"torque {q(fm['T_brake_max_Nm'], 'N·m')}, max V_dc {q(fm['v_dc_max_V'], 'V')}"))
        for s_ in matrix.get("skipped") or []:
            s6.add(tr(f"{sc(s_['key'])}: {sc(s_['same_as'])}와 같은 궤적이라 뺌 (자기 설계 변형을 빼면 중복)",
                      f"{sc(s_['key'])}: skipped, same trajectory as {sc(s_['same_as'])} (its own design variant "
                      f"removed)"), "info")
    s7 = ins.section(tr("근거의 범위", "scope of the evidence"))
    if review.get("statement"):
        s7.add(esc(review["statement"]), "info")
    s7.add(tr("인버터 수준의 엔지니어링 근거 — 차량 수준 제어 가능성, 하드웨어 지표(SPFM/LFM/PMHF), 프로세스 산출물은 범위 밖이며 "
              "ISO 26262 적합성 판정이 아닙니다.", "inverter-level engineering evidence — vehicle controllability, hardware "
                                             "metrics (SPFM / LFM / PMHF) and process work products are outside it; "
                                             "not an ISO 26262 compliance verdict."), "info")
    return ins.nonempty()
