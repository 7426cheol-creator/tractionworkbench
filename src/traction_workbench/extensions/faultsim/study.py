"""Studies on top of the causal simulation: one scenario explained, protection on / off and reaction candidates
compared from the same initial condition, and the representative scenarios of the synthetic example.

Every study result carries: the scenario, the trace (truth, measurements, estimates, commands, actual bridge), the
event timeline, the requirement verdicts with scope and evidence, the timing chain (t_F, t_D, t_R, t_S), the model
information (bases, overrides, precision diagnostics) and the identities of project, code and scenario.
"""

from __future__ import annotations

import copy
import hashlib
import json

from ... import progress
from .campaign import code_identity, project_identity, run_one
from .safety import FAIL, NA, PASS, UNKNOWN

CANDIDATES = ("policy", "none", "asc_low", "asc_high", "six_switch_off", "torque_zero")

# the representative scenarios: every category the tool must reproduce, on the synthetic project
SCENARIOS = [
    {"key": "normal_hs", "category": "normal operation",
     "title": {"ko": "정상 운전 · 12,000 rpm 150 N·m", "en": "normal · 12,000 rpm 150 N·m"},
     "hint": {"ko": "고장 없음: 오검출·반응 없이 모든 요구 PASS (정상 결과 보존)",
              "en": "no fault: no false detection or reaction, every requirement passes"},
     "scenario": {"speed_rpm": 12000, "torque_Nm": 150, "horizon_ms": 60}},
    {"key": "step_ok", "category": "normal transient",
     "title": {"ko": "정상 과도 · 토크 스텝 0→200 N·m", "en": "normal transient · torque step 0→200 N·m"},
     "hint": {"ko": "감시기 창이 정상 응답을 덮어 오검출 없음", "en": "the monitor's window covers the healthy response"},
     "scenario": {"speed_rpm": 6000, "request": {"kind": "step", "T0_Nm": 0, "T1_Nm": 200, "t0_ms": 10},
                  "horizon_ms": 60}},
    {"key": "false_trip", "category": "false detection",
     "title": {"ko": "오검출 · 좁은 감시 창의 토크 스텝", "en": "false detection · tight monitor on a torque step"},
     "hint": {"ko": "지연·응답 허용이 없는 감시기가 정상 과도에서 안전 상태로 전환 (가용성 FAIL)",
              "en": "a monitor without delay / response allowance trips on a healthy transient (availability FAIL)"},
     "scenario": {"speed_rpm": 6000, "request": {"kind": "step", "T0_Nm": 0, "T1_Nm": 200, "t0_ms": 10},
                  "horizon_ms": 60, "overrides": {"mechanisms.SM-TQ.params.response_tau_ms": 0.0,
                                                  "mechanisms.SM-TQ.params.delay_ms": 0.0,
                                                  "mechanisms.SM-TQ.params.ramp_Nm_per_ms": None,
                                                  "mechanisms.SM-TQ.params.debounce_ms": 1.0}}},
    {"key": "cs_offset", "category": "protection success",
     "title": {"ko": "전류 센서 오프셋 +150 A · 12,000 rpm", "en": "current sensor offset +150 A · 12,000 rpm"},
     "hint": {"ko": "세 상 합 감시가 0.9 ms에 검출 → ASC → 안전 조건 도달·유지",
              "en": "the current-sum check detects in 0.9 ms → ASC → safe condition reached and held"},
     "scenario": {"speed_rpm": 12000, "torque_Nm": 150, "horizon_ms": 80,
                  "faults": [{"kind": "sensor", "t_ms": 10, "params": {"target": "CS_A", "mode": "offset",
                                                                        "value": 150}}]}},
    {"key": "ov_regen", "category": "protection success",
     "title": {"ko": "회생 중 배터리 차단 · 12,000 rpm −80 N·m", "en": "battery disconnect in regen · 12,000 rpm −80 N·m"},
     "hint": {"ko": "HW 과전압 비교기 → ASC, DC-link peak가 부품 한계 아래", "en": "HW over-voltage comparator → ASC, "
                                                                          "DC-link peak below the rating"},
     "scenario": {"speed_rpm": 12000, "torque_Nm": -80, "horizon_ms": 80,
                  "faults": [{"kind": "battery_disconnect", "t_ms": 10}]}},
    {"key": "ov_path_lost", "category": "lost protection path",
     "title": {"ko": "HW 반응 경로 잠재 상실 + 배터리 차단", "en": "latent loss of the HW path + battery disconnect"},
     "hint": {"ko": "HW 검출은 되지만 경로가 없어 반응 차단 → SW 백업이 늦게 반응 (여유 감소)",
              "en": "the HW comparator detects but its path is gone → the software backup reacts later (less margin)"},
     "scenario": {"speed_rpm": 12000, "torque_Nm": -80, "horizon_ms": 80,
                  "faults": [{"kind": "path_lost", "t_ms": 0, "params": {"path": "HW"}},
                             {"kind": "battery_disconnect", "t_ms": 10}]}},
    {"key": "ov_slow", "category": "protection delay",
     "title": {"ko": "보호 지연 · HW OV 비활성 + SW 디바운스 2 ms", "en": "protection delay · HW OV disabled + 2 ms SW debounce"},
     "hint": {"ko": "검출이 늦어 DC-link가 부품 한계 850 V를 넘음 (FAIL)",
              "en": "late detection: the DC link exceeds the 850 V rating (FAIL)"},
     "scenario": {"speed_rpm": 12000, "torque_Nm": -80, "horizon_ms": 80,
                  "faults": [{"kind": "mechanism_disabled", "t_ms": 0, "params": {"mechanism": "SM-OV"}},
                             {"kind": "battery_disconnect", "t_ms": 10}],
                  "overrides": {"mechanisms.SM-OVSW.params.debounce_ms": 2.0}}},
    {"key": "res_lost", "category": "wrong reaction",
     "title": {"ko": "레졸버 신호 소실 · 12,000 rpm", "en": "resolver signal loss · 12,000 rpm"},
     "hint": {"ko": "얼어붙은 각도로 속도 추정이 0 → 정책이 6SO 선택(실제 고속) → 비제어 정류 → BMS 차단 → 과전압",
              "en": "the frozen angle drives the speed estimate to 0 → the policy picks 6SO at high speed → "
                    "uncontrolled rectification → BMS opens → over-voltage"},
     "scenario": {"speed_rpm": 12000, "torque_Nm": 150, "horizon_ms": 60,
                  "faults": [{"kind": "sensor", "t_ms": 10, "params": {"target": "RES", "mode": "lost"}}]}},
    {"key": "sw_short", "category": "protection success",
     "title": {"ko": "상단 스위치 단락 · 3,000 rpm", "en": "upper switch short · 3,000 rpm"},
     "hint": {"ko": "하단 스위치 desat → 단락된 쪽의 ASC(ASC-high) 선택", "en": "the lower switch desaturates → the ASC "
                                                                    "of the shorted side (ASC-high)"},
     "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 80,
                  "faults": [{"kind": "switch_short", "t_ms": 10, "params": {"leg": "a", "device": "upper"}}]}},
    {"key": "sw_short_wrong", "category": "wrong reaction",
     "title": {"ko": "상단 스위치 단락 + desat 규칙 없는 정책", "en": "upper switch short + policy without the desat rules"},
     "hint": {"ko": "속도 규칙만 보는 정책이 반대쪽 ASC(ASC-low)를 명령 → 부분 단락과 큰 전류",
              "en": "a speed-only policy commands the opposite ASC (ASC-low) → partial short and large currents"},
     "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 80,
                  "faults": [{"kind": "switch_short", "t_ms": 10, "params": {"leg": "a", "device": "upper"}}],
                  "overrides": {"policy.rules": [{"if": {"speed_above_rpm": 2000.0}, "then": "asc_low"},
                                                 {"else": "six_switch_off"}]}}},
    {"key": "gate_supply", "category": "protection success",
     "title": {"ko": "하단 게이트 전원 상실 · 12,000 rpm", "en": "lower gate supply loss · 12,000 rpm"},
     "hint": {"ko": "UVLO 보고 → 살아 있는 쪽의 ASC(ASC-high)", "en": "UVLO report → the ASC of the healthy side"},
     "scenario": {"speed_rpm": 12000, "torque_Nm": 150, "horizon_ms": 80,
                  "faults": [{"kind": "gate_supply_loss", "t_ms": 10, "params": {"side": "lower"}}]}},
    {"key": "sens_supply", "category": "common cause",
     "title": {"ko": "공통 원인 · 전류 센서 전원 상실", "en": "common cause · current-sensor supply loss"},
     "hint": {"ko": "제어와 HW 과전류 비교기가 같은 센서 → 비교기도 눈이 멂 → desat만 반응, 소자 전류 한계 초과",
              "en": "control and the HW over-current comparator share the sensors → both blind → only desat reacts, "
                    "device current limit exceeded"},
     "scenario": {"speed_rpm": 12000, "torque_Nm": 150, "horizon_ms": 60,
                  "faults": [{"kind": "resource_loss", "t_ms": 10, "params": {"resource": "SENS_5V"}}]}},
    {"key": "stale_indep", "category": "protection success",
     "title": {"ko": "토크 명령 고착 · 감시기 독립 메시지", "en": "stale torque command · independent monitor message"},
     "hint": {"ko": "요청 150→0인데 구동은 150 유지 → 독립 메시지로 감시기 검출 → 안전 상태",
              "en": "request 150→0 while the drive keeps 150 → the monitor's own message detects it"},
     "scenario": {"speed_rpm": 6000, "request": {"kind": "step", "T0_Nm": 150, "T1_Nm": 0, "t0_ms": 20},
                  "horizon_ms": 80, "faults": [{"kind": "torque_command", "t_ms": 15, "params": {"mode": "stale"}}]}},
    {"key": "stale_common", "category": "common cause",
     "title": {"ko": "토크 명령 고착 · 감시기가 같은 메시지 사용", "en": "stale torque command · monitor on the same message"},
     "hint": {"ko": "공통 원인: 감시기도 같은 오래된 값을 봄 → 미검출, 의도치 않은 가속 토크 (FAIL)",
              "en": "common cause: the monitor sees the same stale value → undetected unintended acceleration (FAIL)"},
     "scenario": {"speed_rpm": 6000, "request": {"kind": "step", "T0_Nm": 150, "T1_Nm": 0, "t0_ms": 20},
                  "horizon_ms": 100, "faults": [{"kind": "torque_command", "t_ms": 15,
                                                 "params": {"mode": "stale", "paths": "both"}}]}},
    {"key": "restart_flying", "category": "recovery / restart",
     "title": {"ko": "MCU 리셋 2 ms → flying 재시동", "en": "MCU reset 2 ms → flying restart"},
     "hint": {"ko": "속도를 다시 잡고 램프로 복귀: 반응 없이 토크 회복", "en": "speed re-established, torque ramps back"},
     "scenario": {"speed_rpm": 7000, "torque_Nm": 150, "horizon_ms": 60,
                  "faults": [{"kind": "mcu_reset", "t_ms": 10, "params": {"duration_ms": 2}}],
                  "overrides": {"policy.restart": "flying", "control.boot_ms": 2.0}}},
    {"key": "restart_cold", "category": "recovery failure",
     "title": {"ko": "MCU 리셋 2 ms → cold 재시동 실패", "en": "MCU reset 2 ms → cold restart fails"},
     "hint": {"ko": "속도 추정이 0에서 시작 → 큰 과도 → 감시기·과전류 → 안전 상태로 끝남",
              "en": "the speed estimate starts from 0 → large transient → monitor / over-current → safe state"},
     "scenario": {"speed_rpm": 7000, "torque_Nm": 150, "horizon_ms": 60,
                  "faults": [{"kind": "mcu_reset", "t_ms": 10, "params": {"duration_ms": 2}}],
                  "overrides": {"policy.restart": "cold", "control.boot_ms": 2.0}}},
    {"key": "recovery_fail", "category": "recovery failure",
     "title": {"ko": "복귀 실패 · PWM 출력 고착 + 비래치 정책", "en": "recovery failure · stuck PWM output, non-latched policy"},
     "hint": {"ko": "조건 해제 후 재시동 → 고장이 남아 다시 트립, 시도 소진 후 래치",
              "en": "restart after the condition clears → the fault is still there, trips again, latched after the "
                    "attempts"},
     "scenario": {"speed_rpm": 3000, "torque_Nm": 150, "horizon_ms": 120,
                  "faults": [{"kind": "pwm_output", "t_ms": 10, "params": {"leg": "a", "mode": "upper_on"}}],
                  "overrides": {"policy.latch": False, "policy.recovery_after_ms": 10.0,
                                "policy.recovery_max_attempts": 2}}},
    {"key": "no_safe_reaction", "category": "no executable safe reaction",
     "title": {"ko": "안전 반응 없음 · 하단 게이트 전원 + 상단 a 개방 + 배터리 차단", "en": "no safe reaction · lower gates + "
                                                                              "upper a open + battery off"},
     "hint": {"ko": "ASC-low 불가, ASC-high는 부분 단락, 6SO는 과전압 → 후보 비교에서 모두 FAIL",
              "en": "ASC-low impossible, ASC-high partial, 6SO over-voltage → every candidate fails"},
     "scenario": {"speed_rpm": 12000, "torque_Nm": 150, "horizon_ms": 60,
                  "faults": [{"kind": "switch_open", "t_ms": 0, "params": {"leg": "a", "device": "upper"}},
                             {"kind": "battery_disconnect", "t_ms": 10},
                             {"kind": "gate_supply_loss", "t_ms": 10, "params": {"side": "lower"}}]}},
]


def scenario(key: str) -> dict:
    for s in SCENARIOS:
        if s["key"] == key:
            return copy.deepcopy(s["scenario"])
    raise KeyError(key)


def scenario_digest(sc: dict) -> str:
    return hashlib.sha256(json.dumps(sc, sort_keys=True, default=str).encode()).hexdigest()[:12]


def explain(product, sc: dict) -> dict:
    """One scenario: trace, events, verdicts, timing chain, model information and identities."""
    r = run_one(product, sc, keep_trace=True)
    res, ev, setup, info = r["result"], r["evaluation"], r["setup"], r["info"]
    return {"scenario": copy.deepcopy(sc), "scenario_digest": scenario_digest(sc), "trace": res.trace,
            "events": res.events, "status": res.status, "stop_reason": res.stop_reason, "summary": res.summary,
            "evaluation": ev, "info": info, "verdicts": r["verdicts"], "metrics": r["metrics"],
            "mechanisms": [{"id": m.mech_id, "kind": m.kind, "path": m.path, "reaction": m.reaction,
                            "resources": list(m.resources), "text": m.text} for m in setup.mechanisms],
            "paths": {k: {"delay_s": p.delay_s, "resources": list(p.resources), "fixed": p.fixed_reaction,
                          "basis": p.basis} for k, p in setup.paths.items()},
            "sensors": [{"name": s.name, "kind": s.kind, "delay_s": s.delay_s, "resources": list(s.resources)}
                        for s in setup.sensors],
            "roles": {k: setup.role(k) for k in ("current_a", "current_b", "current_c", "current_hw_a",
                                                "current_mon_a", "position_control", "position_monitor",
                                                "vdc_control", "vdc_monitor", "vdc_hw")},
            "project": project_identity(product.project), "code": code_identity(),
            "precision": {"pwm_model": setup.pwm_model, "h_max_s": setup.h_max_s, "zeno": res.summary.get("zeno"),
                          "energy_relative_residual": res.summary["energy"]["relative_residual"],
                          "notes": precision_notes(setup)}}


def precision_notes(setup) -> list:
    notes = ["constant-parameter machine (no saturation, cross-coupling, demagnetisation or temperature change in "
             "the horizon): currents far above the rating need a dynamically qualified flux model",
             "ideal switches and diodes (no forward drop, switching loss, ringing or device survival): device SOA and "
             "short-circuit withstand need supplier data",
             "battery as Thevenin source with series inductance; the contactor arc is booked, not modelled"]
    if setup.pwm_model == "averaged":
        notes.insert(0, "averaged PWM: currents without switching ripple; comparator trips and peaks near a limit need "
                        "the switched model")
    return notes


def independence(project) -> dict:
    """Static dependency analysis of the architecture: per resource, which sensors, mechanisms and paths need it
    (a common cause), and per FSR whether a single resource removes every allocated mechanism or every path."""
    from .configure import FAULT_SIM_EXAMPLE, mechanisms_from, paths_from, sensors_from
    data = project.data("fault_sim") if project.has("fault_sim") else FAULT_SIM_EXAMPLE
    sens = sensors_from(data, {})
    mechs = mechanisms_from(data)
    paths = paths_from(data, {})
    roles = dict(data.get("roles") or {})
    from .engine import ROLE_DEFAULTS

    def role(r):
        return roles.get(r) or roles.get(ROLE_DEFAULTS.get(r, ""), "")
    reads = {"torque_monitor": ["current_mon_a", "current_mon_b", "position_monitor"],
             "current_plausibility": ["current_mon_a", "current_mon_b", "current_mon_c"],
             "overcurrent_sw": ["current_mon_a", "current_mon_b", "current_mon_c"],
             "overvoltage_sw": ["vdc_monitor"], "undervoltage_sw": ["vdc_monitor"],
             "position_los": ["position_monitor"], "overcurrent_hw": ["current_hw_a", "current_hw_b", "current_hw_c"],
             "overvoltage_hw": ["vdc_hw"]}
    control_reads = {role(r) for r in ("current_a", "current_b", "current_c", "position_control", "vdc_control")}
    sres = {s.name: set(s.resources) for s in sens}
    rows = []
    for m in mechs:
        used = {role(r) for r in reads.get(m.kind, [])}
        needs = set(m.resources) | set(paths[m.path].resources) | set().union(*[sres.get(n, set()) for n in used])
        shared = sorted(used & control_reads)
        rows.append({"mechanism": m.mech_id, "kind": m.kind, "path": m.path, "sensors": sorted(used),
                     "shares_sensors_with_control": shared, "needs": sorted(needs)})
    resources = sorted(set().union(*[set(r["needs"]) for r in rows]) | set(data.get("resources") or {}))
    per_res = []
    for res_ in resources:
        per_res.append({"resource": res_, "text": (data.get("resources") or {}).get(res_, ""),
                        "sensors": sorted(n for n, rs in sres.items() if res_ in rs),
                        "mechanisms": sorted(r["mechanism"] for r in rows if res_ in r["needs"]),
                        "paths": sorted(k for k, p in paths.items() if res_ in p.resources)})
    fsr = []
    from .safety import requirements_from_dict
    reqs = requirements_from_dict(data.get("requirements") or {})
    for f in reqs.fsrs:
        alloc = [r for r in rows if r["mechanism"] in f.mechanisms]
        single = [res_ for res_ in resources if alloc and all(res_ in r["needs"] for r in alloc)]
        fsr.append({"fsr": f.fsr_id, "mechanisms": [r["mechanism"] for r in alloc],
                    "single_points": single,
                    "statement": ("one resource removes every allocated mechanism: " + ", ".join(single)) if single
                    else "no single declared resource removes every allocated mechanism (declared dependencies "
                         "only; undeclared couplings - common software, clock, ground - are not visible here)"})
    return {"mechanisms": rows, "resources": per_res, "fsr": fsr,
            "note": "static view of the DECLARED dependencies; the simulation confirms or refutes a common cause "
                    "dynamically (inject the resource loss with a primary fault)"}


def compare(product, sc: dict, candidates=CANDIDATES) -> dict:
    """Protection on / off and every reaction candidate from the same initial condition and fault."""
    rows = []
    with progress.span(len(candidates), "reaction candidates") as sp:
        for c in candidates:
            s2 = copy.deepcopy(sc)
            if c == "none":
                s2["protection"] = False
            elif c != "policy":
                s2["reaction_override"] = c
            r = run_one(product, s2, keep_trace=True)
            res = r["result"]
            vs = [v for v in r["verdicts"].values() if v != NA]
            overall = FAIL if FAIL in vs else UNKNOWN if UNKNOWN in vs else PASS if vs else NA
            acts = [e for e in res.events if e["kind"] in ("actuation", "bridge")]
            rows.append({"candidate": c, "overall": overall,
                         "failing": sorted(k for k, v in r["verdicts"].items() if v == FAIL),
                         "unknown": sorted(k for k, v in r["verdicts"].items() if v == UNKNOWN),
                         "metrics": r["metrics"], "final_bridge": res.summary["final_bridge"],
                         "final_actual": res.summary["final_actual"], "first_actuation": acts[0] if acts else None,
                         "status": res.status, "stop_reason": res.stop_reason,
                         "trace": {k: res.trace[k] for k in ("t", "T_shaft", "T_request", "i_a", "i_b", "i_c",
                                                            "v_dc", "i_bat")}})
            sp.step(c)
    ok = [r["candidate"] for r in rows if r["overall"] == PASS and r["candidate"] not in ("none",)]
    return {"scenario": copy.deepcopy(sc), "rows": rows, "passing_candidates": ok,
            "statement": ("candidates that meet every requirement here: " + ", ".join(ok)) if ok else
            "no candidate meets every requirement in this scenario: no executable safe reaction among "
            + ", ".join(c for c in candidates if c not in ("none",)),
            "project": project_identity(product.project), "code": code_identity()}
