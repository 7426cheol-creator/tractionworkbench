"""The built-in example reference package: a neutral catalogue that exercises every check on the synthetic project.

It is the demonstration and the test fixture of the reference verification (``refpkg``): a scenario library on the
built-in synthetic project (every scenario a fault-simulation scenario, with the system components and the
additional mechanisms it needs declared inline, so the project data stay untouched) and example items with their
checks.  A specification or study package (loaded from a file) uses the same scenario and check vocabulary; its items,
provenance and parameter values are its own.  Every value here is an example (synthetic), never a requirement of a
product.
"""

from __future__ import annotations

import copy

# ------------------------------------------------------------------------------------------------- building blocks

# the signed-window torque monitors: the request scaled by a limit factor with a speed-dependent tolerance (time /
# debounce and integral), the oscillation power and energy monitors
MONITORS = [
    {"id": "SM-TWIN", "kind": "torque_window_signed", "path": "SW", "reaction": "safe_state", "period_ms": 1.0,
     "resources": ["MCU"], "text": "signed torque window (limit factor, speed-dependent tolerance), time / debounce",
     "params": {"limit_source": "factor", "limit_factor": 1.1, "abs_Nm": 10.0, "tol_rule": "widen",
                "tol_map": [[0.0, 5.0], [6000.0, 7.5], [12000.0, 10.0]], "debounce_ms": 5.0}},
    {"id": "SM-TINT", "kind": "torque_integral", "path": "SW", "reaction": "safe_state", "period_ms": 1.0,
     "resources": ["MCU"], "text": "signed torque window, integral of the excess",
     "params": {"limit_source": "factor", "limit_factor": 1.1, "abs_Nm": 10.0, "tol_rule": "widen",
                "tol_map": [[0.0, 5.0], [6000.0, 7.5], [12000.0, 10.0]], "limit_Nms": 0.3, "leak_per_s": 0.5}},
    {"id": "SM-OSCP", "kind": "osc_power", "path": "SW", "reaction": "safe_state", "period_ms": 1.0,
     "resources": ["MCU"], "text": "oscillating power (estimate minus request) above a threshold for a debounce time",
     "params": {"f_hp_Hz": 3.0, "tau_env_ms": 20.0, "threshold_W": 4000.0, "debounce_ms": 60.0}},
    {"id": "SM-OSCE", "kind": "osc_energy", "path": "SW", "reaction": "safe_state", "period_ms": 1.0,
     "resources": ["MCU"], "text": "accumulated oscillation energy (estimate minus request)",
     "params": {"f_hp_Hz": 3.0, "allow_W": 2000.0, "limit_J": 400.0, "leak_per_s": 0.5}},
]
NO_TQ = {"mechanisms.SM-TQ.enabled": False}          # the plain torque monitor off (the signed-window ones judge)

# redundant channels: a second resolver channel, a monitor DC-voltage channel, a DC-current sensor, and the
# mechanisms that compare them; PWM feedback; the estimator's qualified domain
PLAUSIBILITY = {
    "sensors": [{"name": "RES2", "kind": "position", "offset_tol_deg": 0.5, "delay_us": 20.0, "quant_deg": 0.05,
                 "los_detect_ms": 1.0, "resources": ["RDC2"]},
                {"name": "VDC_MON", "kind": "voltage", "gain_tol": 0.01, "offset_tol_V": 2.0, "quant_V": 0.25,
                 "valid_range_V": [0.0, 1000.0], "resources": []},
                {"name": "IDC", "kind": "dc_current", "quant_A": 0.5, "resources": []}],
    "roles": {"position_monitor": "RES2", "vdc_monitor": "VDC_MON", "dc_current": "IDC"},
    "mechanisms": [
        {"id": "SM-RP", "kind": "rotor_plausibility", "path": "SW", "reaction": "safe_state", "period_ms": 0.1,
         "resources": ["MCU"], "params": {"threshold_deg": 20.0, "debounce_ms": 1.0},
         "text": "control vs monitor resolver channel"},
        {"id": "SM-VP", "kind": "vdc_plausibility", "path": "SW", "reaction": "safe_state", "period_ms": 1.0,
         "resources": ["MCU"], "params": {"threshold_V": 30.0, "debounce_ms": 2.0},
         "text": "control vs monitor DC-voltage channel"},
        {"id": "SM-DCOC", "kind": "dc_overcurrent_hw", "path": "HW", "reaction": "report_only",
         "resources": ["CPLD", "HW_REF"], "params": {"threshold_A": 300.0, "filter_us": 2.0},
         "text": "fast DC over-current comparator (detection only)"},
        {"id": "SM-PWMF", "kind": "pwm_feedback", "path": "SW", "reaction": "safe_state", "period_ms": 0.1,
         "resources": ["MCU"], "params": {"debounce_ms": 0.2}, "text": "observed vs commanded PWM"},
        {"id": "SM-EST", "kind": "estimator_domain", "path": "SW", "reaction": "safe_state", "period_ms": 1.0,
         "resources": ["MCU"], "params": {"speed_max_rpm": 11000.0, "current_max_A": 1000.0},
         "text": "the torque estimate's qualified domain"}]}

RX_MON = {"id": "SM-RX", "kind": "rx_monitor", "path": "SW", "reaction": "safe_state", "period_ms": 5.0,
          "resources": ["MCU"], "params": {"max_age_ms": 30.0}, "text": "age of the last accepted torque frame"}
E2E = {"period_ms": 10.0, "latency_ms": 0.5, "counter_mod": 16, "max_delta": 2}

SUPERVISOR = {"path": "SW", "reaction": "safe_state", "rearm": "kl15_sw_hw_off_on", "latch_retention": "nvm",
              "paths": {"kl15_hw_off": "HW"}, "reactions": {"no_torque": "six_switch_off"}}

SUPPLY = {"hv_min_V": 40.0, "hv_hyst_V": 2.0, "rails": [
    {"name": "MCU", "sources": ["LV"], "hold_up_ms": 2.0, "restart_ms": 5.0, "resources": ["MCU"]},
    {"name": "GATE", "sources": ["LV", "HV"], "hold_up_ms": 0.5, "restart_ms": 0.5,
     "resources": ["GATE_UPPER", "GATE_LOWER"]},
    {"name": "LOGIC", "sources": ["LV", "HV"], "hold_up_ms": 1.0, "restart_ms": 1.0, "resources": ["CPLD", "HW_REF",
                                                                                                  "WD"]}]}
SUPPLY_LV_GATES = copy.deepcopy(SUPPLY)
SUPPLY_LV_GATES["rails"][1]["sources"] = ["LV"]

GDE_MONITOR = {"id": "SM-GDE", "kind": "gde_monitor", "path": "HWGD", "reaction": "safe_state",
               "params": {"delay_us": 1.0}, "resources": ["CPLD"], "text": "hardware GDE monitor (logic device)"}
GDE_SEQ = {
    # two readings of the same hardware sequence: kept as variants until the source binds one
    "SEQ_A": {"id": "GDE_SEQ_A", "fallback": "six_switch_off",
              "text": "six-switch-off for 100 us, then the HV-dependent selection (ASC above X_upp)",
              "steps": [{"action": "six_switch_off", "exit": "time", "value": 0.1},
                        {"action": "hv_select_low", "exit": "none", "params": {"v_upp_V": "$X_UPP", "v_low_V": "$X_LOW"}}]},
    "SEQ_B": {"id": "GDE_SEQ_B", "fallback": "six_switch_off",
              "text": "the lower three devices on (ASC) at once, six-switch-off after 100 us",
              "steps": [{"action": "asc_low", "exit": "time", "value": 0.1},
                        {"action": "six_switch_off", "exit": "none"}]}}


def gde_additions(seq: str) -> dict:
    s = GDE_SEQ[seq]
    return {"mechanisms": [GDE_MONITOR], "strategies": [s],
            "paths": [{"id": "HWGD", "delay_us": 2.0, "resources": ["CPLD"], "fixed_reaction": s["id"]}]}


VEHICLE = {"mass_kg": 2000.0, "wheel_radius_m": 0.33, "ratio": 9.0, "efficiency": 0.95, "c_rr": 0.012,
           "cdA_m2": 0.7}
EM2 = {"power_W_ms": [[0, -100000.0]], "speed_rpm": 9000, "p": 4, "psi_Wb": 0.08, "Ld_uH": 200, "Lq_uH": 400,
       "Rs_mohm": 10, "delay_ms": 1.0, "state": "asc"}
ENVELOPE = {"max_Nm": 100.0, "min_Nm": -150.0}
FALLBACK = {"envelope": ENVELOPE, "fallback": {"safe_value_Nm": "$SAFE_VALUE"}}
EXTENDED = {"envelope": ENVELOPE, "extended": {"c_m_min_Nm": "$C_M_MIN", "c_t_imax_ms": "$C_T_IMAX"}}
OPSTATE = {"initial": "POWER_OFF", "hv_low_V": 60.0, "spd_low_rpm": 300.0, "guard_stndby_idle_ms": 50.0,
           "complete_stndby_idle_ms": 150.0, "persist_idle_stndby_ms": 1000.0, "aps_above_rpm": 3000.0}


def _cs_offset(t=10.0, value=150.0, dur=None):
    p = {"target": "CS_A", "mode": "offset", "value": value}
    if dur is not None:
        p["duration_ms"] = dur
    return {"kind": "sensor", "t_ms": t, "params": p}


def _cmd(reaction, t=2.0, path=None):
    p = {"reaction": reaction}
    if path:
        p["path"] = path
    return {"kind": "command_reaction", "t_ms": t, "params": p}


def _inp(t, sig, val):
    return {"t_ms": t, "signal": sig, "value": val}


_CHATTER = [{"kind": "torque_command", "t_ms": 10.0 + 6.0 * k,
             "params": {"mode": "offset", "value": 80.0, "paths": "control", "duration_ms": 3.0}} for k in range(10)]

# ------------------------------------------------------------------------------------------------- scenarios

SCENARIOS = {
    # -- signed torque window: the four quadrants, a healthy step, deviations, oscillations ------------------------
    "S-QUAD": {"title": "normal operation in the four quadrants (signed-window monitors added)",
               "scenario": {"speed_rpm": 6000, "torque_Nm": 100, "horizon_ms": 80, "overrides": NO_TQ,
                            "additions": {"mechanisms": MONITORS}},
               "variants": {"motoring": {}, "regen": {"torque_Nm": -100},
                            "reverse_motoring": {"speed_rpm": -3000, "torque_Nm": -100},
                            "reverse_regen": {"speed_rpm": -3000, "torque_Nm": 100},
                            "step": {"torque_Nm": None, "request": {"kind": "step", "T0_Nm": 0, "T1_Nm": 200,
                                                                    "t0_ms": 10}}}},
    "S-DEV": {"title": "torque deviations: small-long, large-short, boundary chatter (1 ms command messages)",
              "scenario": {"speed_rpm": 6000, "torque_Nm": 100, "horizon_ms": 100,
                           "overrides": dict(NO_TQ, **{"control.command_period_ms": 1.0}),
                           "additions": {"mechanisms": MONITORS}},
              "variants": {
                  "small_long": {"faults": [{"kind": "torque_command", "t_ms": 10,
                                             "params": {"mode": "offset", "value": 35.0, "paths": "control"}}]},
                  "large_short": {"faults": [{"kind": "torque_command", "t_ms": 10, "params": {
                      "mode": "offset", "value": 150.0, "paths": "control", "duration_ms": 4.0}}]},
                  "chatter": {"faults": _CHATTER},
                  "sign_flip": {"faults": [{"kind": "torque_command", "t_ms": 10,
                                            "params": {"mode": "sign_flip", "paths": "control"}}]}}},
    "S-ENV": {"title": "the received torque envelope (maximum / minimum) - contradiction",
              "scenario": {"speed_rpm": 6000, "torque_Nm": 100, "horizon_ms": 60, "overrides": NO_TQ,
                           "additions": {"mechanisms": [dict(MONITORS[0], params=dict(
                               MONITORS[0]["params"], limit_source="envelope", env_above_Nm=40.0,
                               env_below_Nm=40.0))]},
                           "faults": [{"kind": "envelope", "t_ms": 20, "params": {"mode": "contradiction",
                                                                                   "value": 10.0}}]}},
    "S-OSC": {"title": "torque oscillation of 12 N*m on the command (inside the window; the monitor sees its own "
                       "message)",
              "scenario": {"speed_rpm": 6000, "torque_Nm": 100, "horizon_ms": 300,
                           "overrides": dict(NO_TQ, **{"control.command_period_ms": 1.0}),
                           "additions": {"mechanisms": MONITORS}},
              "variants": {f"f{f}": {"faults": [{"kind": "torque_command", "t_ms": 10, "params": {
                  "mode": "oscillation", "value": 12.0, "freq_Hz": float(f), "paths": "control"}}]}
                  for f in (5, 20, 80)}},
    # -- the physical safe state of the bridge reactions ----------------------------------------------------------
    "S-REACT": {"title": "a reaction commanded at 2 ms (speed held, no protection), 40 ms",
                "scenario": {"speed_rpm": 12000, "torque_Nm": 0.0, "horizon_ms": 40, "protection": False},
                "variants": {"fw_12000": {"faults": [_cmd("six_switch_off")]},
                             "asc_12000": {"faults": [_cmd("asc_low")]},
                             "fw_3000": {"speed_rpm": 3000, "faults": [_cmd("six_switch_off")]},
                             "asc_3000": {"speed_rpm": 3000, "faults": [_cmd("asc_low")]},
                             "fw_12000_hv_off": {"faults": [{"kind": "battery_disconnect", "t_ms": 2.0},
                                                            _cmd("six_switch_off")]},
                             "asc_reverse": {"speed_rpm": -6000, "faults": [_cmd("asc_low")]},
                             "soft_asc_6000": {"speed_rpm": 6000, "torque_Nm": 150,
                                               "faults": [_cmd("SOFT_ASC_V", path="SW")]}}},
    "S-FAULT": {"title": "current-sensor offset at 12,000 rpm, 150 N*m (the project's protection)",
                "scenario": {"speed_rpm": 12000, "torque_Nm": 150, "horizon_ms": 80, "faults": [_cs_offset()]}},
    # -- hardware paths ----------------------------------------------------------------------------------------------
    "S-GDE": {"title": "gate-driver enable withdrawn: hardware HV-dependent selection",
              "scenario": {"speed_rpm": 12000, "torque_Nm": 50, "horizon_ms": 40,
                           "faults": [{"kind": "gde_disable", "t_ms": 10}]},
              "variants": {"SEQ_A": {"additions": gde_additions("SEQ_A")},
                           "SEQ_B": {"additions": gde_additions("SEQ_B")},
                           # a DC link below X_Low (60 V) is an HV system that is disconnected: the relay opens
                           # with the GDE (an event at the drive's interface) and the link holds 50 V - the battery
                           # system's own protection is not assumed (a connected 50 V source took the rectified
                           # current until a BMS opened it)
                           "SEQ_A_low_hv": {"speed_rpm": 3000, "torque_Nm": 5, "Voc_V": 50.0,
                                            "faults": [{"kind": "battery_disconnect", "t_ms": 10},
                                                       {"kind": "gde_disable", "t_ms": 10}],
                                            "additions": gde_additions("SEQ_A")},
                           "SEQ_B_low_hv": {"speed_rpm": 3000, "torque_Nm": 5, "Voc_V": 50.0,
                                            "faults": [{"kind": "battery_disconnect", "t_ms": 10},
                                                       {"kind": "gde_disable", "t_ms": 10}],
                                            "additions": gde_additions("SEQ_B")}}},
    "S-FOV": {"title": "battery disconnected in regeneration (fast over-voltage comparator)",
              "scenario": {"speed_rpm": 12000, "torque_Nm": -80, "horizon_ms": 60,
                           "faults": [{"kind": "battery_disconnect", "t_ms": 10}]}},
    "S-ACFOC": {"title": "phase over-current: a PWM output stuck on (fast AC over-current comparator)",
                "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 30,
                             "faults": [{"kind": "pwm_output", "t_ms": 10, "params": {"leg": "a", "mode": "upper_on"}}]},
                "variants": {"thr_nominal": {}, "thr_low": {"overrides": {"mechanisms.SM-OC.params.threshold_A": 810.0}},
                             "thr_high": {"overrides": {"mechanisms.SM-OC.params.threshold_A": 990.0}}}},
    "S-ACMAX": {"title": "the highest normal phase current (no over-current trip at any threshold tolerance)",
                "scenario": {"speed_rpm": 6000, "torque_Nm": 250, "horizon_ms": 30},
                "variants": {"thr_low": {"overrides": {"mechanisms.SM-OC.params.threshold_A": 810.0}},
                             "thr_high": {"overrides": {"mechanisms.SM-OC.params.threshold_A": 990.0}}}},
    "S-DCFOC": {"title": "DC over-current on a torque step (detection only, no reaction invented)",
                "scenario": {"speed_rpm": 9000, "horizon_ms": 30, "overrides": NO_TQ,
                             "request": {"kind": "step", "T0_Nm": 50, "T1_Nm": 250, "t0_ms": 10},
                             "additions": PLAUSIBILITY}},
    # -- supplies ----------------------------------------------------------------------------------------------------
    "S-LV": {"title": "terminal-30 (low-voltage) loss at 12,000 rpm",
             "scenario": {"speed_rpm": 12000, "torque_Nm": 150, "horizon_ms": 80,
                          "faults": [{"kind": "lv_loss", "t_ms": 10}], "system": {"supply": SUPPLY}},
             "variants": {"redundant": {}, "lv_gates": {"system": {"supply": SUPPLY_LV_GATES}},
                          "estop": {"faults": [{"kind": "lv_loss", "t_ms": 10, "label": "emergency stop (terminal 30)"}]}}},
    "S-HV40": {"title": "the HV-derived supply around its 40 V minimum with the low voltage lost",
               "scenario": {"speed_rpm": 3000, "torque_Nm": 0.0, "horizon_ms": 60, "protection": False,
                            "faults": [_cmd("asc_low", t=5.0, path="HW"), {"kind": "lv_loss", "t_ms": 10}],
                            "system": {"supply": SUPPLY}},
               "variants": {"hv_45V": {"Voc_V": 45.0}, "hv_38V": {"Voc_V": 38.0}}},
    # -- supervisor --------------------------------------------------------------------------------------------------
    "S-KL15": {"title": "terminal 15 software / hardware off and on at 3000 rpm, 100 N*m",
               "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 120,
                            "system": {"supervisor": SUPERVISOR}},
               "variants": {"sw": {"system": {"inputs": [_inp(10, "kl15_sw", 0), _inp(60, "kl15_sw", 1)]}},
                            "hw": {"system": {"inputs": [_inp(10, "kl15_hw", 0), _inp(60, "kl15_hw", 1)]}},
                            "sw_12000": {"speed_rpm": 12000, "system": {
                                "inputs": [_inp(10, "kl15_sw", 0), _inp(80, "kl15_sw", 1)]}}}},
    "S-DEFERR": {"title": "default error response and its re-arm (current-sensor offset for 20 ms at 10 ms)",
                 "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 120,
                              "faults": [_cs_offset(dur=20.0)], "system": {"supervisor": SUPERVISOR}},
                 "variants": {
                     "no_rearm": {},
                     "sw_only": {"system": {"inputs": [_inp(60, "kl15_sw", 0), _inp(70, "kl15_sw", 1)]}},
                     "both": {"system": {"inputs": [_inp(60, "kl15_sw", 0), _inp(62, "kl15_hw", 0),
                                                    _inp(70, "kl15_sw", 1), _inp(75, "kl15_hw", 1)]}},
                     "both_gap": {"system": {"supervisor": {"rearm_max_gap_ms": 20.0},
                                             "inputs": [_inp(40, "kl15_sw", 0), _inp(45, "kl15_sw", 1),
                                                        _inp(60, "kl15_hw", 0), _inp(100, "kl15_hw", 1)]}},
                     "level_and": {"system": {"supervisor": {"rearm": "kl15_level_and"}}},
                     "dtc_clear": {"system": {"inputs": [_inp(60, "dtc_clear", 1)]}},
                     "dtc_clear_releases": {"system": {"supervisor": {"dtc_clear_releases": True},
                                                       "inputs": [_inp(60, "dtc_clear", 1)]}},
                     "reset_nvm": {"system": {"inputs": [_inp(60, "reset_request", 1)]}},
                     "reset_ram": {"system": {"supervisor": {"latch_retention": "ram"},
                                              "inputs": [_inp(60, "reset_request", 1)]}},
                     "corrupt_protected": {"faults": [_cs_offset(dur=20.0), {"kind": "latch_corruption", "t_ms": 60}]},
                     "corrupt_unprotected": {"system": {"supervisor": {"latch_protection": False}},
                                             "faults": [_cs_offset(dur=20.0), {"kind": "latch_corruption",
                                                                               "t_ms": 60}]}}},
    "S-MODE": {"title": "bus target modes (standby, no-torque) and an isolated terminal-30 condition at 6000 rpm",
               "scenario": {"speed_rpm": 6000, "torque_Nm": 100, "horizon_ms": 120,
                            "system": {"supervisor": SUPERVISOR}},
               "variants": {"standby": {"system": {"inputs": [_inp(20, "target_mode", "standby"),
                                                              _inp(80, "target_mode", "run")]}},
                            "no_torque": {"system": {"inputs": [_inp(20, "target_mode", "no_torque"),
                                                                _inp(80, "target_mode", "run")]}},
                            "t30_uv": {"system": {"inputs": [_inp(20, "t30_state", "uv"),
                                                             _inp(60, "t30_state", "normal")]}}}},
    "S-RESET": {"title": "MCU reset (5 ms) at 10 ms",
                "scenario": {"torque_Nm": 0.0, "horizon_ms": 60,
                             "faults": [{"kind": "mcu_reset", "t_ms": 10, "params": {"duration_ms": 5.0}}],
                             "system": {"supervisor": SUPERVISOR}},
                "variants": {"standstill": {"speed_rpm": 0.0}, "rotating": {"speed_rpm": 12000.0}}},
    "S-OPSTATE": {"title": "operating states: power-up, idle, run, roll-out, discharge, standby (standstill)",
                  "scenario": {"speed_rpm": 0.0, "torque_Nm": 0.0, "horizon_ms": 1500, "h_max_us": 50,
                               "request": {"kind": "step", "T0_Nm": 0, "T1_Nm": 80, "t0_ms": 210},
                               "system": {"supervisor": SUPERVISOR, "opstate": OPSTATE,
                                          "discharge": {"v_limit_V": 60.0, "confirm_ms": 10},
                                          "initial": {"trq_gen_rq": 0},
                                          "inputs": [_inp(200, "trq_gen_rq", 1), _inp(300, "trq_gen_rq", 0),
                                                     _inp(320, "ignition", 0), _inp(330, "discharge_request", 1)]}}},
    "S-CHANGE": {"title": "controlled changeover RUN -> IDLE at speed (torque request withdrawn)",
                 "scenario": {"speed_rpm": 6000, "torque_Nm": 100, "horizon_ms": 60,
                              "system": {"supervisor": SUPERVISOR, "opstate": dict(OPSTATE, initial="RUN"),
                                         "inputs": [_inp(20, "trq_gen_rq", 0)]}}},
    # -- vehicle -------------------------------------------------------------------------------------------------------
    "S-VEH": {"title": "standstill: an unintended torque command of 250 N*m at 10 ms",
              "scenario": {"torque_Nm": 0.0, "horizon_ms": 150, "vehicle": dict(VEHICLE, v0_kph=0.0),
                           "faults": [{"kind": "torque_command", "t_ms": 10,
                                       "params": {"mode": "value", "value": 250.0, "paths": "control"}}]},
              "variants": {"monitor_on": {},
                           "monitor_off": {"faults": [{"kind": "mechanism_disabled", "t_ms": 0,
                                                       "params": {"mechanism": "SM-TQ"}},
                                                      {"kind": "torque_command", "t_ms": 10, "params": {
                                                          "mode": "value", "value": 250.0, "paths": "control"}}]},
                           "p1_k0_open": {"vehicle": {"position": "P1", "k0_closed": False},
                                          "faults": [{"kind": "mechanism_disabled", "t_ms": 0,
                                                      "params": {"mechanism": "SM-TQ"}},
                                                     {"kind": "torque_command", "t_ms": 10, "params": {
                                                         "mode": "value", "value": 250.0, "paths": "control"}}]}}},
    "S-VEH60": {"title": "60 km/h: an unintended torque command of 250 N*m (vehicle speed domain above 40 km/h)",
                "scenario": {"torque_Nm": 0.0, "horizon_ms": 150, "vehicle": dict(VEHICLE, v0_kph=60.0),
                             "faults": [{"kind": "torque_command", "t_ms": 10,
                                         "params": {"mode": "value", "value": 250.0, "paths": "control"}}]}},
    # -- torque interface -------------------------------------------------------------------------------------------
    "S-ITF": {"title": "vehicle torque interface: envelope 100 / -150 N*m, extension, speed-limit fallback",
              "scenario": {"speed_rpm": 3000, "horizon_ms": 260, "overrides": NO_TQ,
                           "request": {"kind": "step", "T0_Nm": 100, "T1_Nm": 200, "t0_ms": 5},
                           "system": {"interface": {"envelope": ENVELOPE}}},
              "variants": {
                  "extension": {"system": {"interface": EXTENDED, "inputs": [_inp(20, "ext_request", 1)]}},
                  "extension_voltage_mode": {"system": {"interface": EXTENDED,
                                                        "inputs": [_inp(20, "ext_request", 1),
                                                                   _inp(0, "voltage_control_mode", 1)]}},
                  "negative": {"request": {"kind": "step", "T0_Nm": -100, "T1_Nm": -200, "t0_ms": 5},
                               "system": {"interface": EXTENDED, "inputs": [_inp(20, "ext_request", 1)]}},
                  "fallback_qualifier": {"system": {"interface": FALLBACK,
                                                    "inputs": [_inp(200, "speed_limit_qualifier", "limited")]}},
                  "fallback_wheel_invalid": {"system": {"interface": FALLBACK,
                                                        "inputs": [_inp(200, "wheel_speed_valid", 0)]}},
                  "fallback_request_invalid": {"system": {"interface": FALLBACK,
                                                          "inputs": [_inp(200, "speed_limit_valid", 0)]}},
                  "fallback_over_limit": {"system": {"interface": FALLBACK,
                                                     "inputs": [_inp(200, "wheel_speed_kph", 130.0),
                                                                _inp(200, "speed_limit_kph", 120.0)]}},
                  "intervention": {"system": {"inputs": [_inp(100, "intervention_Nm", -60.0)]}}}},
    "S-NORMAL1ST": {"title": "normal function first: the application limit acts, the monitor does not",
                    "scenario": {"speed_rpm": 3000, "torque_Nm": 150, "horizon_ms": 80, "overrides": NO_TQ,
                                 "additions": {"mechanisms": [dict(MONITORS[0], params=dict(
                                     MONITORS[0]["params"], limit_source="envelope", env_above_Nm=20.0,
                                     env_below_Nm=400.0))]},
                                 "system": {"interface": {"envelope": {"max_Nm": 120.0, "min_Nm": -150.0}}}},
                    "variants": {"healthy": {},
                                 "app_failed": {"faults": [{"kind": "application_limit_fail", "t_ms": 20},
                                                           {"kind": "torque_command", "t_ms": 20, "params": {
                                                               "mode": "offset", "value": 60.0, "paths": "control"}}]}}},
    # -- energy ------------------------------------------------------------------------------------------------------
    "S-DISCH": {"title": "active discharge request at 10 ms",
                "scenario": {"speed_rpm": 0.0, "torque_Nm": 0.0, "horizon_ms": 200, "h_max_us": 50,
                             "system": {"supervisor": SUPERVISOR, "opstate": dict(OPSTATE, initial="IDLE"),
                                        "discharge": {"v_limit_V": "$V_DISCHARGE", "confirm_ms": 10},
                                        "inputs": [_inp(10, "discharge_request", 1)]}},
                "variants": {"nominal": {},
                             "sensor_stuck_low": {"faults": [{"kind": "sensor", "t_ms": 5, "params": {
                                 "target": "VDC_MAIN", "mode": "stuck", "value": 20.0}}]},
                             "rotating": {"speed_rpm": 12000.0, "h_max_us": 10, "horizon_ms": 120,
                                          "system": {"opstate": dict(OPSTATE, initial="IDLE", aps_above_rpm=20000.0)}}}},
    "S-DUAL": {"title": "second machine regenerating 100 kW on the shared DC link; this machine's fault and a battery "
                        "disconnection",
               "scenario": {"speed_rpm": 12000, "torque_Nm": 150, "horizon_ms": 60,
                            "faults": [_cs_offset(), {"kind": "battery_disconnect", "t_ms": 12}]},
               "variants": {"global": {"system": {"em2": dict(EM2, contract="global")}},
                            "local": {"system": {"em2": dict(EM2, contract="local")}}}},
    # -- tests and diagnostics ------------------------------------------------------------------------------------
    "S-SELFTEST": {"title": "gate test pulse (20 us, lower switches) in STNDBY",
                   "scenario": {"torque_Nm": 0.0, "horizon_ms": 40,
                                "system": {"supervisor": SUPERVISOR,
                                           "opstate": dict(OPSTATE, initial="STNDBY", aps_above_rpm=6000.0),
                                           "selftest": {"tests": [{"t_ms": 10, "legs": ["a", "b", "c"],
                                                                   "side": "lower", "pulse_us": 20,
                                                                   "speed_max_rpm": 500,
                                                                   "states": ["STNDBY", "IDLE"]}]}}},
                   "variants": {"standstill": {"speed_rpm": 0.0}, "rotating": {"speed_rpm": 3000.0}}},
    "S-PATHTEST": {"title": "latent loss of the hardware path; the latent-path test at start",
                   "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 30,
                                "faults": [{"kind": "path_lost", "t_ms": 0, "params": {"path": "HW"}}],
                                "system": {"supervisor": SUPERVISOR, "selftest": {"path_test_at_start": True}}}},
    "S-E2E": {"title": "end-to-end protected torque message (10 ms), receive monitor at 5 ms",
              "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 100,
                           "additions": {"mechanisms": [RX_MON]}, "system": {"interface": {"e2e": E2E}}},
              "variants": {"normal": {},
                           "per_task_check": {"additions": {"mechanisms": [dict(RX_MON, params={
                               "repeat_check": "per_task"})]}},
                           **{m: {"faults": [{"kind": "e2e", "t_ms": 20, "params": {"mode": m, "value": 5.0}}]}
                              for m in ("counter_repeat", "crc", "data_id", "em_swap", "loss", "counter_jump")}}},
    "S-INPUT": {"title": "input faults at 6000 rpm, 150 N*m (redundant channels added)",
                "scenario": {"speed_rpm": 6000, "torque_Nm": 150, "horizon_ms": 40, "additions": PLAUSIBILITY},
                "variants": {
                    "none": {},
                    "current_offset": {"faults": [_cs_offset()]},
                    "current_common_gain": {"faults": [{"kind": "sensor", "t_ms": 10, "params": {
                        "target": n, "mode": "gain", "value": 0.3}} for n in ("CS_A", "CS_B", "CS_C")]},
                    "resolver_offset": {"faults": [{"kind": "sensor", "t_ms": 10, "params": {
                        "target": "RES", "mode": "offset", "value": 0.8}}]},
                    "resolver_freeze": {"faults": [{"kind": "sensor", "t_ms": 10, "params": {
                        "target": "RES", "mode": "stuck_last"}}]},
                    "resolver_loss": {"faults": [{"kind": "sensor", "t_ms": 10, "params": {
                        "target": "RES", "mode": "lost"}}]},
                    "vdc_offset": {"faults": [{"kind": "sensor", "t_ms": 10, "params": {
                        "target": "VDC_MAIN", "mode": "offset", "value": -80.0}}]},
                    "sensor_supply": {"faults": [{"kind": "resource_loss", "t_ms": 10,
                                                  "params": {"resource": "SENS_5V"}}]},
                    "current_delay": {"faults": [{"kind": "sensor", "t_ms": 10, "params": {
                        "target": "CS_A", "mode": "delay", "value": 0.0002}}]},
                    "out_of_domain": {"speed_rpm": 12000.0}}},
    "S-PWM": {"title": "PWM / gate / switch faults at 6000 rpm, 150 N*m",
              "scenario": {"speed_rpm": 6000, "torque_Nm": 150, "horizon_ms": 40, "additions": PLAUSIBILITY},
              "variants": {"leg_off": {"faults": [{"kind": "pwm_output", "t_ms": 10, "params": {"leg": "a", "mode": "off"}}]},
                           "leg_stuck": {"faults": [{"kind": "pwm_output", "t_ms": 10, "params": {
                               "leg": "a", "mode": "stuck_duty", "value": 0.9}}]},
                           "gate_lower_lost": {"faults": [{"kind": "gate_supply_loss", "t_ms": 10,
                                                           "params": {"side": "lower"}}]},
                           "switch_short": {"faults": [{"kind": "switch_short", "t_ms": 10, "params": {
                               "leg": "a", "device": "lower"}}]},
                           "switch_short_no_desat": {"faults": [
                               {"kind": "mechanism_disabled", "t_ms": 0, "params": {"mechanism": "SM-DSAT"}},
                               {"kind": "switch_short", "t_ms": 10, "params": {"leg": "a", "device": "lower"}}]}}},
    "S-TASK": {"title": "the safety task stops at 10 ms, a current-sensor offset at 20 ms",
               "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 60,
                            "faults": [{"kind": "safety_task_stop", "t_ms": 10}, _cs_offset(t=20.0)]},
               "variants": {"wd_by_control": {},
                            "wd_by_safety": {"overrides": {"mechanisms.SM-WD.params.service_by": "safety_task"}}}},
    "S-CONFIRM": {"title": "reaction confirmation: ASC requested with the lower gate supply lost (12,000 rpm)",
                  "scenario": {"speed_rpm": 12000, "torque_Nm": 100, "horizon_ms": 40,
                               "overrides": {"mechanisms.SM-UVLO.enabled": False},
                               "faults": [{"kind": "resource_loss", "t_ms": 0, "params": {"resource": "GATE_LOWER"}}],
                               "system": {"supervisor": SUPERVISOR}},
                  "variants": {"gate_lost": {}, "healthy": {"faults": [_cs_offset()]}}},
}

# the second part of the library: the received envelope as the window, supply transfer, the no-energy-flow request,
# a clock fault, a coupling change with an overspeed monitor, a regeneration limit sent by the vehicle, a legitimate
# torque at standstill with the clutch open
_ENV_TWIN = dict(MONITORS[0], params=dict(MONITORS[0]["params"], limit_source="envelope", env_above_Nm=40.0,
                                          env_below_Nm=40.0))
OVERSPEED = {"id": "SM-OS", "kind": "overspeed_sw", "path": "SW", "reaction": "safe_state", "period_ms": 1.0,
             "resources": ["MCU"], "params": {"threshold_rpm": 3500.0, "debounce_ms": 1.0},
             "text": "measured speed above the overspeed threshold"}
SCENARIOS.update({
    "S-ENVX": {"title": "the received torque envelope as the window: an actual torque 80 N*m above the request",
               "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 60, "overrides": NO_TQ,
                            "additions": {"mechanisms": [_ENV_TWIN]},
                            "faults": [{"kind": "torque_command", "t_ms": 10, "params": {
                                "mode": "offset", "value": 80.0, "paths": "control"}}]},
               "variants": {"received": {},
                            "widened_no_bound": {"additions": {"mechanisms": [dict(_ENV_TWIN, params=dict(
                                _ENV_TWIN["params"], env_above_Nm=300.0))]}},
                            "widened_bounded": {"additions": {"mechanisms": [dict(_ENV_TWIN, params=dict(
                                _ENV_TWIN["params"], env_above_Nm=300.0, independent_max_Nm=160.0))]}}}},
    "S-DEVF": {"title": "a small long deviation (35 N*m) against the window with the limit factor as a parameter",
               "scenario": {"speed_rpm": 6000, "torque_Nm": 100, "horizon_ms": 60,
                            "overrides": dict(NO_TQ, **{"control.command_period_ms": 1.0}),
                            "additions": {"mechanisms": [dict(MONITORS[0], params=dict(
                                MONITORS[0]["params"], limit_factor="$LIMIT_FACTOR"))]},
                            "faults": [{"kind": "torque_command", "t_ms": 10,
                                        "params": {"mode": "offset", "value": 35.0, "paths": "control"}}]}},
    "S-SUPPLY": {"title": "supply faults with both sources declared (6000 rpm, 100 N*m)",
                 "scenario": {"speed_rpm": 6000, "torque_Nm": 100, "horizon_ms": 40, "system": {"supply": SUPPLY}},
                 "variants": {"hv_source_fault": {"faults": [{"kind": "hv_supply_fault", "t_ms": 10}]},
                              "transfer_seamless": {"faults": [{"kind": "lv_loss", "t_ms": 10}]},
                              "transfer_gap": {"system": {"supply": dict(SUPPLY, transfer_gap_us=1000.0)},
                                               "faults": [{"kind": "lv_loss", "t_ms": 10}]}}},
    "S-EFB": {"title": "the vehicle requests no AC / DC energy flow at 20 ms (RUN, 100 N*m)",
              "scenario": {"torque_Nm": 100, "horizon_ms": 60,
                           "system": {"supervisor": SUPERVISOR, "opstate": dict(OPSTATE, initial="RUN"),
                                      "inputs": [_inp(20, "energy_flow_block", 1)]}},
              "variants": {"above": {"speed_rpm": 6000.0}, "below": {"speed_rpm": 1000.0}}},
    "S-CLOCK": {"title": "the MCU clock fails at 10 ms (3000 rpm, 100 N*m)",
                "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 40},
                "variants": {"stop": {"faults": [{"kind": "clock", "t_ms": 10, "params": {"mode": "stop"}}]},
                             "drift_half": {"faults": [{"kind": "clock", "t_ms": 10,
                                                        "params": {"mode": "drift", "value": 0.5}}]}}},
    "S-COUPLING": {"title": "a coupling opens at 10 ms under 100 N*m (the machine alone: 0.05 kg*m^2)",
                   "scenario": {"speed_rpm": 3000, "torque_Nm": 100, "horizon_ms": 60,
                                "additions": {"mechanisms": [OVERSPEED]},
                                "faults": [{"kind": "coupling", "t_ms": 10, "params": {"J_kgm2": 0.05}}]},
                   "variants": {"runaway": {},
                                "runaway_position_frozen": {"faults": [
                                    {"kind": "sensor", "t_ms": 10, "params": {"target": "RES", "mode": "stuck_last"}},
                                    {"kind": "coupling", "t_ms": 10, "params": {"J_kgm2": 0.05}}]}}},
    "S-REGEN": {"title": "regeneration at 6000 rpm; the vehicle lowers the minimum torque to -40 N*m at 20 ms",
                "scenario": {"speed_rpm": 6000, "torque_Nm": -150, "horizon_ms": 60, "overrides": NO_TQ,
                             "system": {"interface": {"envelope": {"max_Nm": 200.0, "min_Nm": -200.0}},
                                        "inputs": [_inp(20, "env_min_Nm", -40.0)]}}},
    "S-START": {"title": "standstill, clutch open: a legitimate 120 N*m start torque of the machine at 10 ms",
                "scenario": {"torque_Nm": None, "horizon_ms": 100,
                             "request": {"kind": "step", "T0_Nm": 0, "T1_Nm": 120, "t0_ms": 10},
                             "vehicle": dict(VEHICLE, v0_kph=0.0, position="P1", k0_closed=False)}},
})
SCENARIOS["S-LV"]["variants"]["short_during_lv_loss"] = {
    "faults": [{"kind": "lv_loss", "t_ms": 10}, {"kind": "switch_short", "t_ms": 13,
                                                  "params": {"leg": "a", "device": "upper"}}]}
SCENARIOS["S-GDE"]["variants"].update({
    f"{seq}_mcu_reset": {"additions": gde_additions(seq), "faults": [
        {"kind": "mcu_reset", "t_ms": 9, "params": {"duration_ms": 10.0}}, {"kind": "gde_disable", "t_ms": 10}]}
    for seq in ("SEQ_A", "SEQ_B")})


def hw_vdc_additions(v_low="$X_LOW", v_upp="$X_UPP") -> dict:
    """The processor lost while rolling: the hardware GDE monitor and a path that selects the bridge by the hardware
    DC voltage only (ASC above ``v_upp``, six-switch-off below ``v_low``; ``v_low`` 0 V: the ASC is held)."""
    s = {"id": "HW_VDC_SELECT", "fallback": "six_switch_off",
         "text": "hardware selection by the DC-link voltage: ASC above X_upp, six-switch-off below X_low",
         "steps": [{"action": "hv_select_low", "exit": "none", "params": {"v_upp_V": v_upp, "v_low_V": v_low}}]}
    return {"mechanisms": [GDE_MONITOR], "strategies": [s],
            "paths": [{"id": "HWGD", "delay_us": 2.0, "resources": ["CPLD"], "fixed_reaction": "HW_VDC_SELECT"}]}


SCENARIOS["S-HWVDC"] = {
    "title": "the processor lost and the battery disconnected while rolling at 6000 rpm: the hardware selects the "
             "bridge by the DC voltage",
    "scenario": {"speed_rpm": 6000, "torque_Nm": 50, "horizon_ms": 120,
                 "faults": [{"kind": "mcu_reset", "t_ms": 10, "params": {"duration_ms": 5000.0}},
                            {"kind": "gde_disable", "t_ms": 10}, {"kind": "battery_disconnect", "t_ms": 10}],
                 "additions": hw_vdc_additions()},
    "variants": {"cycling": {}, "latched": {"additions": hw_vdc_additions(v_low=0.0)}}}
SCENARIOS["S-REACT"]["variants"]["fw_low_hv_3000"] = {        # a connected 50 V source accepts the charge (no
    "speed_rpm": 3000, "Voc_V": 50.0, "faults": [_cmd("six_switch_off")]}     # battery-system protection assumed)
SCENARIOS["S-OPSTATE"]["variants"] = {
    "nominal": {},
    "kl15_off": {"system": {"inputs": [_inp(150, "kl15_sw", 0), _inp(200, "trq_gen_rq", 1),
                                       _inp(300, "trq_gen_rq", 0), _inp(320, "ignition", 0),
                                       _inp(330, "discharge_request", 1)]}}}

# ------------------------------------------------------------------------------------------------- parameters

PARAMETERS = [
    {"id": "T_SHUTOFF", "unit": "ms", "provenance": "OPEN", "value": None, "illustrative": 50.0,
     "note": "criterion -> safe state (default error response)"},
    {"id": "FTTI_TORQUE", "unit": "ms", "provenance": "OPEN", "value": None, "illustrative": 100.0,
     "note": "torque-window deviation -> safe state"},
    {"id": "T_KL15_SW", "unit": "ms", "provenance": "OPEN", "value": None, "illustrative": 20.0,
     "note": "terminal 15 software off -> safe state"},
    {"id": "T_KL15_HW", "unit": "ms", "provenance": "OPEN", "value": None, "illustrative": 20.0,
     "note": "terminal 15 hardware off -> safe state"},
    {"id": "FTTI_STANDBY", "unit": "ms", "provenance": "OPEN", "value": None, "illustrative": 50.0,
     "note": "standby / no-torque target mode -> its obligation"},
    {"id": "T_TOL", "unit": "N*m", "provenance": "OPEN", "value": None, "illustrative": 5.0,
     "note": "torque below which no torque counts as generated (safe-state judge)"},
    {"id": "P_TOL", "unit": "W", "provenance": "OPEN", "value": None, "illustrative": 500.0,
     "note": "DC power below which no energy exchange counts (safe-state judge)"},
    {"id": "TLSR_T_MIN", "unit": "N*m", "provenance": "OPEN", "value": None, "illustrative": -450.0,
     "note": "lowest shaft torque a requirement allows in the safe state (C4)"},
    {"id": "TLSR_T_MAX", "unit": "N*m", "provenance": "OPEN", "value": None, "illustrative": 450.0,
     "note": "highest shaft torque a requirement allows in the safe state (C4)"},
    {"id": "SS_TRANSITION", "unit": "ms", "provenance": "OPEN", "value": None, "illustrative": 20.0,
     "note": "transition allowance before the safe-state conditions are judged"},
    {"id": "GDE_TO_3PS", "unit": "us", "provenance": "PAST-PROJECT", "value": 150.0},
    {"id": "FAST_REACT", "unit": "us", "provenance": "PAST-PROJECT", "value": 20.0},
    {"id": "X_LOW", "unit": "V", "provenance": "PAST-PROJECT", "value": None, "illustrative": 60.0,
     "note": "HV threshold below which the hardware selects freewheeling (minimum 60 V; nominal OPEN)"},
    {"id": "X_UPP", "unit": "V", "provenance": "OPEN", "value": None, "illustrative": 100.0,
     "note": "HV threshold above which the hardware selects the ASC"},
    {"id": "SEQ", "unit": "-", "provenance": "CONFLICT", "value": None,
     "variants": {"SEQ_A": "six-switch-off 100 us, then ASC", "SEQ_B": "ASC (lower), six-switch-off after 100 us"},
     "note": "two records of the hardware sequence disagree"},
    {"id": "HV_SUPPLY_MIN", "unit": "V", "provenance": "PAST-PROJECT", "value": 40.0},
    {"id": "GUARD_STNDBY_IDLE", "unit": "ms", "provenance": "PAST-PROJECT", "value": 50.0},
    {"id": "MAX_STNDBY_IDLE", "unit": "ms", "provenance": "PAST-PROJECT", "value": 150.0},
    {"id": "PERSIST_IDLE_STNDBY", "unit": "ms", "provenance": "PAST-PROJECT", "value": 1000.0},
    {"id": "T_CHANGEOVER", "unit": "ms", "provenance": "OPEN", "value": None, "illustrative": 50.0,
     "note": "controlled changeover completion"},
    {"id": "STANDSTILL_DIST", "unit": "m", "provenance": "PAST-PROJECT", "value": 2.0},
    {"id": "BRAKE_DECEL", "unit": "m/s^2", "provenance": "PAST-PROJECT", "value": 5.0},
    {"id": "T_REACT_DRIVER", "unit": "s", "provenance": "PAST-PROJECT", "value": 1.0},
    {"id": "ACCEL_CURVE", "unit": "(km/h, m/s^2)", "provenance": "OPEN", "value": None,
     "illustrative": [[0, 2.0], [40, 2.0], [120, 1.0]], "note": "vehicle acceleration target curve"},
    {"id": "SAFE_VALUE", "unit": "N*m", "provenance": "OPEN", "value": None, "illustrative": 50.0,
     "note": "positive torque safe value of the speed-limit fallback"},
    {"id": "C_M_MIN", "unit": "N*m", "provenance": "OPEN", "value": None, "illustrative": 150.0,
     "note": "minimum positive torque of the extension"},
    {"id": "C_T_IMAX", "unit": "ms", "provenance": "OPEN", "value": None, "illustrative": 100.0,
     "note": "maximum duration of the extension"},
    {"id": "V_DISCHARGE", "unit": "V", "provenance": "PROJECT", "value": 60.0},
    {"id": "T_DISCHARGE", "unit": "ms", "provenance": "PROJECT", "value": 2000.0},
    {"id": "MAX_RX_AGE", "unit": "ms", "provenance": "OPEN", "value": None, "illustrative": 40.0,
     "note": "maximum age of the last accepted update"},
    {"id": "MAX_SAFETY_TASK", "unit": "ms", "provenance": "OPEN", "value": None, "illustrative": 10.0,
     "note": "maximum safety task time"},
    {"id": "TEST_ENVELOPE_T", "unit": "N*m", "provenance": "OPEN", "value": None, "illustrative": 20.0,
     "note": "torque a diagnostic test may produce"},
    {"id": "V_LINK_MAX", "unit": "V", "provenance": "DERIVED", "value": 850.0, "note": "DC-link rating (synthetic)"},
    {"id": "SPFM_TARGET", "unit": "-", "provenance": "OPEN", "value": None, "illustrative": 0.97},
    {"id": "LFM_TARGET", "unit": "-", "provenance": "OPEN", "value": None, "illustrative": 0.8},
    {"id": "PMHF_TARGET", "unit": "FIT", "provenance": "OPEN", "value": None, "illustrative": 100.0},
    {"id": "MAX_ASIL", "unit": "ASIL", "provenance": "OPEN", "value": None, "note": "the maximum ASIL parameter"},
    {"id": "LIMIT_FACTOR", "unit": "-", "provenance": "OPEN", "value": None, "illustrative": 1.1,
     "note": "limit factor of the torque window"},
    {"id": "EST_ERROR_BOUND", "unit": "N*m", "provenance": "OPEN", "value": None, "illustrative": 15.0,
     "note": "accuracy of the safety torque estimate against the transmission-side torque"},
    {"id": "REGEN_POWER_MIN", "unit": "W", "provenance": "OPEN", "value": None, "illustrative": -30000.0,
     "note": "lowest DC power (regeneration is negative) the interface accepts after the vehicle lowers its minimum "
             "torque"},
    {"id": "T_PASSIVE_DISCHARGE", "unit": "s", "provenance": "PROJECT", "value": 120.0},
    {"id": "OC_RATIO", "unit": "x I_max", "provenance": "PAST-PROJECT", "value": 1.5,
     "note": "fast over-current threshold relative to the maximum current (outside the normal range)"},
    {"id": "OC_RATIO_TOL", "unit": "-", "provenance": "PAST-PROJECT", "value": 0.1},
]

SS = {"torque_tol_Nm": "$T_TOL", "power_tol_W": "$P_TOL", "tlsr_min_Nm": "$TLSR_T_MIN", "tlsr_max_Nm": "$TLSR_T_MAX",
      "transition_ms": "$SS_TRANSITION"}


def _c(check, scenario=None, variant=None, variants=None, label="", mode=None, **params):
    c = {"check": check, "params": params}
    if scenario:
        c["scenario"] = scenario
    if variant:
        c["variant"] = variant
    if variants:
        c["variants"] = list(variants)
    if mode:
        c["mode"] = mode
    if label:
        c["label"] = label
    return c


FMEDA = [
    {"element": "phase current sensor", "mode": "offset / gain", "fit": 20.0, "violates": ["TQ"], "dc_spf": 0.9,
     "dc_latent": 0.9, "partner_fit": 10.0},
    {"element": "resolver", "mode": "angle error", "fit": 30.0, "violates": ["TQ", "DS"], "dc_spf": 0.95,
     "dc_latent": 0.9, "partner_fit": 10.0},
    {"element": "MCU core", "mode": "wrong computation", "fit": 50.0, "violates": ["TQ", "DS"], "dc_spf": 0.99,
     "dc_latent": 0.9, "partner_fit": 20.0},
    {"element": "gate driver", "mode": "stuck output", "fit": 15.0, "violates": ["TQ"], "dc_spf": 0.9,
     "dc_latent": 0.6, "partner_fit": 10.0},
    {"element": "watchdog", "mode": "no trip", "fit": 5.0, "mpf": True, "mpf_of": ["TQ", "DS"], "dc_latent": 0.9,
     "partner_fit": 50.0},
    {"element": "DC-link capacitor", "mode": "open", "fit": 2.0, "safety_related": False},
]

ITEMS = [
    # -- safe state judged physically --------------------------------------------------------------------------------
    {"id": "EX-SS-01", "group": "safe state", "kind": "requirement", "provenance": "CONFIRMED",
     "title": "the safe state is judged on the physical outcome (C1..C4), never on the command",
     "checks": [_c("safe_state", "S-REACT", "asc_12000", origin="gate", **SS),
                _c("safe_state", "S-REACT", "fw_12000", origin="gate", expect_reached=False, **SS,
                   label="freewheeling at 12,000 rpm rectifies into the DC link (C2)")]},
    {"id": "EX-SS-02", "group": "safe state", "kind": "requirement", "provenance": "DERIVED",
     "title": "the transition into the safe state: a soft ASC is judged with its pulsing steps (C3)",
     "checks": [_c("safe_state", "S-REACT", "soft_asc_6000", origin="gate", **SS)]},
    {"id": "EX-TL-01", "group": "timing", "kind": "timing", "provenance": "DERIVED",
     "title": "fault -> criterion -> detection -> request -> gate -> physical safe state on one clock",
     "checks": [_c("timeline", "S-FAULT", expect_recorded=True, ftti_ms="$FTTI_TORQUE", shutoff_ms="$T_SHUTOFF",
                   torque_tol_Nm="$T_TOL", power_tol_W="$P_TOL")]},
    # -- torque monitors -------------------------------------------------------------------------------------------
    {"id": "EX-TQ-01", "group": "torque window", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "no false trip in the four quadrants and on a healthy step (signed limits)",
     "checks": [_c("no_detection", "S-QUAD", variants=("motoring", "regen", "reverse_motoring", "reverse_regen",
                                                        "step"), mode="all")]},
    {"id": "EX-TQ-02", "group": "torque window", "kind": "requirement", "provenance": "CONFIRMED",
     "title": "time and integral monitoring: a small-long deviation and a short repeated one are both detected",
     "checks": [_c("detected", "S-DEV", "small_long", by=["SM-TWIN"], label="time monitor on a long deviation"),
                _c("detected", "S-DEV", "chatter", by=["SM-TINT"], label="integral monitor on the boundary chatter"),
                _c("detected", "S-DEV", "chatter", by=["SM-TWIN"], expect=False,
                   label="the time monitor alone misses the chatter")]},
    {"id": "EX-TQ-03", "group": "torque window", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "a contradictory received envelope (maximum <= minimum) triggers the default error response",
     "checks": [_c("detected", "S-ENV", by=["SM-TWIN"])]},
    {"id": "EX-OSC-01", "group": "oscillation", "kind": "requirement", "provenance": "CONFIRMED",
     "title": "torque oscillation detected by the oscillating-power and energy monitors",
     "checks": [_c("detected", "S-OSC", variants=("f5", "f20", "f80"), mode="all", by=["SM-OSCP", "SM-OSCE"])]},
    # -- hardware paths --------------------------------------------------------------------------------------------
    {"id": "EX-HW-01", "group": "hardware path", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "gate-driver enable withdrawn -> the ASC within 150 us (both recorded sequences)",
     "checks": [_c("state_time", "S-GDE", variants=("SEQ_A", "SEQ_B"), origin="fault", max_us="$GDE_TO_3PS"),
                # the physical safe state within the same budget and held (no transition allowance): the sequence
                # that leaves the ASC for six-switch-off at 12,000 rpm rectifies into the battery (C2)
                _c("safe_state", "S-GDE", variants=("SEQ_A", "SEQ_B"), origin="fault", deadline_us="$GDE_TO_3PS",
                   torque_tol_Nm="$T_TOL", power_tol_W="$P_TOL", tlsr_min_Nm="$TLSR_T_MIN",
                   tlsr_max_Nm="$TLSR_T_MAX")]},
    {"id": "EX-HW-02", "group": "hardware path", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "fast over-voltage: detection and reaction within 20 us",
     "checks": [_c("hw_timing", "S-FOV", mech="SM-OV", quantity="v_dc", threshold=780.0,
                   detect_max_us="$FAST_REACT", react_max_us="$FAST_REACT")]},
    {"id": "EX-HW-03", "group": "hardware path", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "fast DC over-current: detection within 20 us, no reaction invented",
     "checks": [_c("hw_timing", "S-DCFOC", mech="SM-DCOC", quantity="i_bat", threshold=300.0,
                   detect_max_us="$FAST_REACT"),
                _c("bound", "S-DCFOC", quantity="bridge", max=0.0, label="the bridge stays in PWM")]},
    # -- supplies ----------------------------------------------------------------------------------------------------
    {"id": "EX-PW-01", "group": "supply", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the safe state survives the low-voltage loss (redundant gate supply); not with LV-only gates",
     "checks": [_c("safe_state", "S-LV", "redundant", origin="fault", **SS),
                _c("safe_state", "S-LV", "lv_gates", origin="fault", expect_reached=False, **SS)]},
    {"id": "EX-PW-02", "group": "supply", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "an emergency stop by terminal-30 loss ends in the same safe state as a low-voltage loss",
     "checks": [_c("equivalent", "S-LV", "estop", other="S-LV", other_variant="redundant")]},
    # -- supervisor ----------------------------------------------------------------------------------------------------
    {"id": "EX-SUP-01", "group": "supervisor", "kind": "requirement", "provenance": "CONFIRMED",
     "title": "terminal 15 software off: safe state in time, held until the next software on",
     "checks": [_c("safe_state", "S-KL15", "sw", origin="input:kl15_sw=0", to="input:kl15_sw=1",
                   deadline_ms="$T_KL15_SW",
                   torque_tol_Nm="$T_TOL", power_tol_W="$P_TOL", tlsr_min_Nm="$TLSR_T_MIN",
                   tlsr_max_Nm="$TLSR_T_MAX"),
                _c("permit", "S-KL15", "sw", **{"from": "input:kl15_sw=0", "to": "input:kl15_sw=1"})]},
    {"id": "EX-SUP-02", "group": "supervisor", "kind": "requirement", "provenance": "CONFIRMED",
     "title": "the default-error latch is released only by the qualified terminal-15 re-arm",
     "checks": [_c("rearm", "S-DEFERR", "no_rearm", expect_release=False),
                _c("rearm", "S-DEFERR", "sw_only", expect_release=False),
                _c("rearm", "S-DEFERR", "both", qualified="input:kl15_hw=1"),
                _c("rearm", "S-DEFERR", "dtc_clear", expect_release=False),
                _c("rearm", "S-DEFERR", "reset_nvm", expect_release=False)]},
    {"id": "EX-SUP-03", "group": "supervisor", "kind": "rule", "provenance": "DERIVED",
     "title": "non-approved variants release too early (level AND, DTC clear, RAM latch)",
     "checks": [_c("rearm", "S-DEFERR", "level_and", expect_premature=True),
                _c("rearm", "S-DEFERR", "dtc_clear_releases", expect_premature=True),
                _c("rearm", "S-DEFERR", "reset_ram", expect_premature=True)]},
    {"id": "EX-SUP-04", "group": "supervisor", "kind": "requirement", "provenance": "CONFIRMED",
     "title": "an isolated terminal-30 condition is a supply reason, not a default error (released without re-arm)",
     "checks": [_c("rearm", "S-MODE", "t30_uv", expect_latch=False),
                _c("permit", "S-MODE", "t30_uv", **{"from": "input:t30_state=uv", "to": "input:t30_state=normal"},
                   restored_within_ms=50.0)]},
    {"id": "EX-OPS-01", "group": "operating states", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "power-up, idle, run, roll-out and standby with their guard and completion times",
     "checks": [_c("opstate", "S-OPSTATE", sequence=[["POWER_OFF", "STNDBY"], ["STNDBY", "IDLE"], ["IDLE", "RUN"],
                                                     ["RUN", "IDLE"], ["IDLE", "STNDBY"]],
                   timing=[{"from": "STNDBY", "to": "IDLE", "origin": "event:opstate:POWER_OFF -> STNDBY",
                            "min_ms": "$GUARD_STNDBY_IDLE", "max_ms": "$MAX_STNDBY_IDLE"}]),
                _c("guard_table", guard="IDLE->RUN", formula="hv_enable & spt_done & aps_release & trq_gen_rq"),
                _c("guard_table", guard="RUN->IDLE", formula="~hv_enable | rol_actv | pwr_stg_ctrl | ~trq_gen_rq")]},
    {"id": "EX-OPS-02", "group": "operating states", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "controlled changeover RUN -> IDLE at speed",
     "checks": [_c("changeover", "S-CHANGE", **{"from": "RUN", "to": "IDLE"}, max_ms="$T_CHANGEOVER")]},
    # -- vehicle ---------------------------------------------------------------------------------------------------------
    {"id": "EX-VEH-01", "group": "vehicle", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "standstill to standstill <= 2 m with 5 m/s^2 braking after a 1 s reaction",
     "checks": [_c("vehicle", "S-VEH", "monitor_on", distance_max_m="$STANDSTILL_DIST", t_react_s="$T_REACT_DRIVER",
                   a_brake_mps2="$BRAKE_DECEL"),
                _c("vehicle", "S-VEH", "monitor_off", distance_max_m="$STANDSTILL_DIST", expect_violation=True,
                   t_react_s="$T_REACT_DRIVER", a_brake_mps2="$BRAKE_DECEL",
                   label="counterexample: the undetected fault exceeds the distance")]},
    # -- interface -------------------------------------------------------------------------------------------------------
    {"id": "EX-ITF-01", "group": "interface", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the signed arbitration of the request and the intervention torque",
     "checks": [_c("arbitration")]},
    {"id": "EX-ITF-02", "group": "interface", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "extended torque: positive side only, active flag, timed reversion",
     "checks": [_c("extended", "S-ITF", "extension", c_t_imax_ms="$C_T_IMAX"),
                _c("extended", "S-ITF", "extension_voltage_mode", expect_active=False),
                _c("extended", "S-ITF", "negative", neg_limit_Nm=-150.0)]},
    {"id": "EX-ITF-03", "group": "interface", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the speed-limit fallback limits the positive torque to the safe value",
     "checks": [_c("fallback", "S-ITF", v, origin="t0", safe_value_Nm="$SAFE_VALUE", settle_ms=220.0)
                for v in ("fallback_qualifier", "fallback_wheel_invalid", "fallback_request_invalid",
                          "fallback_over_limit")]},
    # -- energy ------------------------------------------------------------------------------------------------------------
    {"id": "EX-DIS-01", "group": "discharge", "kind": "requirement", "provenance": "PROJECT",
     "title": "active discharge below the limit in time, no false completion, rotating recharge shown",
     "checks": [_c("discharge", "S-DISCH", "nominal", v_limit_V="$V_DISCHARGE", deadline_ms="$T_DISCHARGE"),
                _c("discharge", "S-DISCH", "sensor_stuck_low", v_limit_V="$V_DISCHARGE", expect_false_complete=True),
                _c("discharge", "S-DISCH", "rotating", v_limit_V="$V_DISCHARGE", expect_recharge=True)]},
    {"id": "EX-DUAL-01", "group": "dual machine", "kind": "requirement", "provenance": "DERIVED",
     "title": "the shared DC link under the global and the local safe-state contract",
     "checks": [_c("bound", "S-DUAL", "global", quantity="v_dc", max="$V_LINK_MAX"),
                _c("bound", "S-DUAL", "local", quantity="v_dc", max="$V_LINK_MAX", expect_violation=True,
                   label="counterexample: the local contract lets the second machine charge the open link")]},
    # -- tests, receive path, inputs -----------------------------------------------------------------------------------
    {"id": "EX-TST-01", "group": "diagnostics", "kind": "requirement", "provenance": "DERIVED",
     "title": "a gate test only under its entry conditions, within its envelope, never enabling PWM",
     "checks": [_c("selftest", "S-SELFTEST", "standstill", expect_refused=False, torque_abs_max_Nm="$TEST_ENVELOPE_T"),
                _c("selftest", "S-SELFTEST", "rotating", expect_refused=True)]},
    {"id": "EX-TST-02", "group": "diagnostics", "kind": "requirement", "provenance": "DERIVED",
     "title": "the latent-path test blocks the torque permit when a reaction path is lost",
     "checks": [_c("detected", "S-PATHTEST", by=["SYS:path_test"], origin="t0"),
                _c("permit", "S-PATHTEST", **{"from": 2.0})]},
    {"id": "EX-E2E-01", "group": "receive path", "kind": "requirement", "provenance": "DERIVED",
     "title": "rejected or repeated frames do not refresh the age; half-rate task runs are not repetitions",
     "checks": [_c("e2e", "S-E2E", "normal", expect_detection=False),
                _c("e2e", "S-E2E", "per_task_check", expect_detection=True,
                   label="a per-task repetition check reports the normal half-rate reception (pitfall)"),
                _c("e2e", "S-E2E", variants=("counter_repeat", "crc", "data_id", "em_swap", "loss"), mode="all",
                   within_ms="$MAX_RX_AGE")]},
    {"id": "EX-IN-01", "group": "inputs", "kind": "requirement", "provenance": "DERIVED",
     "title": "input faults detected by the independent channel; a common gain is a blind spot of the sum check",
     "checks": [_c("detected", "S-INPUT", "current_offset", by=["SM-SUM"]),
                _c("detected", "S-INPUT", "current_common_gain", by=["SM-SUM"], expect=False),
                _c("detected", "S-INPUT", "resolver_offset", by=["SM-RP"]),
                _c("detected", "S-INPUT", "resolver_loss", by=["SM-LOS", "SM-RP"]),
                _c("detected", "S-INPUT", "vdc_offset", by=["SM-VP"]),
                _c("detected", "S-INPUT", "out_of_domain", by=["SM-EST"], origin="t0")]},
    {"id": "EX-PWM-01", "group": "output path", "kind": "requirement", "provenance": "DERIVED",
     "title": "the PWM actually realised is monitored; a masked desaturation leaves a leg short uncontrolled",
     "checks": [_c("detected", "S-PWM", "leg_off", by=["SM-PWMF"]),
                _c("bound", "S-PWM", "switch_short", quantity="i_phase_abs", max=1600.0),
                _c("bound", "S-PWM", "switch_short_no_desat", quantity="i_phase_abs", max=1600.0, expect_stop=True,
                   label="without desaturation the leg short is uncontrolled (outside the plant)")]},
    {"id": "EX-WD-01", "group": "execution", "kind": "requirement", "provenance": "DERIVED",
     "title": "a stopped safety task is seen only by a watchdog serviced by the safety task's checkpoints",
     "checks": [_c("detected", "S-TASK", "wd_by_control", by=["SM-WD"], expect=False),
                _c("detected", "S-TASK", "wd_by_safety", by=["SM-WD"])]},
    {"id": "EX-CONF-01", "group": "confirmation", "kind": "requirement", "provenance": "DERIVED",
     "title": "the reaction is reported as confirmed only when the observations show it",
     "checks": [_c("confirmation", "S-CONFIRM", "gate_lost", expect_confirmed=False),
                _c("confirmation", "S-CONFIRM", "healthy", expect_confirmed=True)]},
    # -- static ------------------------------------------------------------------------------------------------------------
    {"id": "EX-CAL-01", "group": "calibration", "kind": "requirement", "provenance": "DERIVED",
     "title": "only a complete, validated parameter set becomes active",
     "checks": [_c("calibration", updates=[
         {"set_id": "good", "em_id": "EM1", "crc_ok": True, "complete": True,
          "values": {"mechanisms.SM-TQ.params.debounce_ms": 2.0}},
         {"set_id": "wrong_em", "em_id": "EM2", "crc_ok": True, "complete": True, "values": {}},
         {"set_id": "partial", "em_id": "EM1", "crc_ok": True, "complete": False, "values": {}},
         {"set_id": "out_of_range", "em_id": "EM1", "crc_ok": True, "complete": True,
          "values": {"mechanisms.SM-TQ.params.debounce_ms": -1.0}},
         {"set_id": "contradiction", "em_id": "EM1", "crc_ok": True, "complete": True,
          "values": {"mechanisms.SM-TQ.params.debounce_ms": 25.0}}],
                   target={"em_id": "EM1"}, expect=["ACCEPTED", "REJECTED", "REJECTED", "REJECTED", "REJECTED"])]},
    {"id": "EX-TSK-01", "group": "timing", "kind": "requirement", "provenance": "CONFIRMED",
     "title": "the safety task cycle within min(Tx, Rx / 2, the maximum task time)",
     "checks": [_c("task_cycle", tx_cycles_ms=[10.0], rx_cycles_ms=[10.0, 20.0], max_task_ms="$MAX_SAFETY_TASK",
                   mechanisms=["SM-TQ", "SM-SUM", "SM-LOS", "SM-OVSW"])]},
    {"id": "EX-META-01", "group": "provenance", "kind": "rule", "provenance": "DERIVED",
     "title": "the source's statements and study assumptions kept apart; OPEN values stay empty",
     "checks": [_c("provenance"), _c("asil_binding")]},
    {"id": "EX-MET-01", "group": "metrics", "kind": "requirement", "provenance": "OPEN",
     "title": "architectural metrics per metric set (never added across sets)",
     "checks": [_c("metrics", sets={"TORQUE": {"requirements": ["TQ"], "SPFM_min": "$SPFM_TARGET",
                                               "LFM_min": "$LFM_TARGET", "PMHF_max_fit": "$PMHF_TARGET"},
                                    "DESTAB": {"requirements": ["DS"], "SPFM_min": "$SPFM_TARGET",
                                               "LFM_min": "$LFM_TARGET", "PMHF_max_fit": "$PMHF_TARGET"}})]},
    {"id": "EX-MAP-01", "group": "feasibility", "kind": "output", "provenance": "DERIVED",
     "title": "safe-state feasibility of freewheeling and ASC over speed x DC voltage",
     "checks": [_c("feasibility", speeds_rpm=[3000.0, 9000.0, 12000.0], vdcs_V=[400.0, 600.0],
                   torque_tol_Nm="$T_TOL", power_tol_W="$P_TOL", tlsr_min_Nm="$TLSR_T_MIN",
                   tlsr_max_Nm="$TLSR_T_MAX", expect_counterexample={"reaction": "six_switch_off"})]},
    {"id": "EX-RES-01", "group": "research", "kind": "research", "provenance": "RESEARCH",
     "title": "the torque impulse before the recognition (a study metric, computed for this machine)",
     "checks": [_c("research", "S-FAULT", metric="impulse_before_detection", limit_Nm=180.0,
                   reference="0.8835 N*m*s in another study")]},
    {"id": "EX-MOD-01", "group": "modules", "kind": "module", "provenance": "DERIVED",
     "title": "the evidence recorder: synchronized trace with the supervisor's channels",
     "checks": [_c("trace_channels", "S-KL15", "sw", channels=["t", "T_em", "T_shaft", "v_dc", "i_dc", "bridge",
                                                               "sys_permit", "sys_reasons", "sys_confirmed"],
                   events=["input", "safe_state_request", "actuation", "supervisor"])]},
    {"id": "EX-MAN-01", "group": "organisation", "kind": "action", "provenance": "DERIVED",
     "title": "input baseline and responsibilities agreed with the requirement owner",
     "manual": "organisational: record the agreement (document, revision) as evidence",
     "checks": [_c("manual", reason="organisational: record the agreement (document, revision) as evidence")]},
    # -- the second part: formula, envelope, supplies, states, clock, coupling, regeneration, questions ----------------
    {"id": "EX-TQ-04", "group": "torque window", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the implemented signed window is the declared formula (for every limit factor and tolerance)",
     "checks": [_c("window_semantics", high="max(T*F, T/F) + A", low="min(T*F, T/F) - A", rule="add")]},
    {"id": "EX-TQ-05", "group": "torque window", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the small long deviation is detected - for which limit factors (break-even of an OPEN value)",
     "checks": [_c("sweep", "S-DEVF", param="LIMIT_FACTOR", values=[1.05, 1.1, 1.3, 1.6], inner="detected",
                   params={"by": ["SM-TWIN"]})]},
    {"id": "EX-TQ-06", "group": "torque window", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the received envelope bounds the actual torque; a widened capability input cannot widen the window",
     "checks": [_c("detected", "S-ENVX", "received", by=["SM-TWIN"]),
                _c("detected", "S-ENVX", "widened_no_bound", by=["SM-TWIN"], expect=False,
                   label="counterexample: a widened envelope hides the deviation"),
                _c("detected", "S-ENVX", "widened_bounded", by=["SM-TWIN"],
                   label="the independent bound keeps the window")]},
    {"id": "EX-EST-01", "group": "torque window", "kind": "requirement", "provenance": "DERIVED",
     "title": "the safety torque estimate against the transmission-side torque (all quadrants)",
     "checks": [_c("bound", "S-QUAD", v, quantity="est_error_abs", max="$EST_ERROR_BOUND", **{"from": 20.0})
                for v in ("motoring", "regen", "reverse_motoring", "reverse_regen")]},
    {"id": "EX-SS-03", "group": "safe state", "kind": "rule", "provenance": "PROJECT",
     "title": "a low DC voltage does not make freewheeling safe (back-EMF above the link at 3000 rpm, 50 V)",
     "checks": [_c("safe_state", "S-REACT", "fw_low_hv_3000", origin="gate", expect_reached=False, **SS)]},
    {"id": "EX-SS-04", "group": "safe state", "kind": "requirement", "provenance": "CONFIRMED",
     "title": "a no-torque target mode forbids torque by active pulsing (C3 only - not the full safe state)",
     "checks": [_c("safe_state", "S-MODE", "no_torque", origin="input:target_mode=no_torque",
                   to="input:target_mode=run", conditions=["C3"], deadline_ms="$FTTI_STANDBY",
                   transition_ms="$SS_TRANSITION")]},
    {"id": "EX-HW-04", "group": "hardware path", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the hardware sequence works while the MCU is in reset",
     "checks": [_c("state_time", "S-GDE", variants=("SEQ_A_mcu_reset", "SEQ_B_mcu_reset"),
                   origin="event:fault:gde_disable", max_us="$GDE_TO_3PS")]},
    {"id": "EX-HW-05", "group": "hardware path", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the fast over-current threshold lies outside the normal range at 1.5 x I_max +-10 %",
     "checks": [_c("threshold_ratio", mech="SM-OC", reference="inverter_current_limit", ratio="$OC_RATIO",
                   tol="$OC_RATIO_TOL"),
                _c("hw_timing", "S-ACFOC", variants=("thr_nominal", "thr_low", "thr_high"), mode="all", mech="SM-OC",
                   quantity="i_phase_abs", detect_max_us="$FAST_REACT", react_max_us="$FAST_REACT",
                   label="detection and reaction with the threshold tolerance")]},
    {"id": "EX-PW-03", "group": "supply", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "a leg short is still detected by the desaturation protection during the low-voltage loss",
     "checks": [_c("detected", "S-LV", "short_during_lv_loss", by=["SM-DSAT"], origin=13.0)]},
    {"id": "EX-PW-04", "group": "supply", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the transfer between the sources is seamless; a transfer gap longer than the hold-up loses the gates",
     "checks": [_c("bound", "S-SUPPLY", "transfer_seamless", quantity="rail_GATE", min=1.0),
                _c("bound", "S-SUPPLY", "transfer_gap", quantity="rail_GATE", min=1.0, expect_violation=True,
                   label="counterexample: a 1 ms gap is longer than the 0.5 ms hold-up")]},
    {"id": "EX-PW-05", "group": "supply", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "a failed redundant source is flagged; the drive keeps its supplies from the other source",
     "checks": [_c("event", "S-SUPPLY", "hv_source_fault", kind="supply", source="HV_fault", origin="fault"),
                _c("no_detection", "S-SUPPLY", "hv_source_fault")]},
    {"id": "EX-OPS-03", "group": "operating states", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "an active state is entered between passive ones only while torque production is allowed",
     "checks": [_c("opstate", "S-OPSTATE", "kl15_off", absent=[["IDLE", "RUN"]])]},
    {"id": "EX-OPS-04", "group": "operating states", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "no AC / DC energy flow: APS above the speed threshold, ASO below it",
     "checks": [_c("state_time", "S-EFB", "above", origin="input:energy_flow_block=1", states=["asc_low"],
                   max_us=2000.0),
                _c("state_time", "S-EFB", "below", origin="input:energy_flow_block=1", states=["six_switch_off"],
                   max_us=2000.0)]},
    {"id": "EX-OPS-05", "group": "operating states", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the power-stage states each operating state allows, and the stage names",
     "checks": [_c("model_tables", allow={"STNDBY": ["ASO", "APS"], "IDLE": ["ASO", "APS"],
                                          "DIAG": ["ASO", "APS", "PWM"], "RUN": ["ASO", "PWM"],
                                          "LHOM": ["ASO", "PWM"], "FAULT": ["ASO", "APS"]},
                   stages={"six_switch_off": "ASO", "asc_low": "APS", "asc_high": "APS", "pwm": "PWM"})]},
    {"id": "EX-CLK-01", "group": "execution", "kind": "requirement", "provenance": "DERIVED",
     "title": "a stopped MCU clock is caught by the external watchdog; a 50 % drift is a blind spot of a timeout "
              "watchdog",
     "checks": [_c("detected", "S-CLOCK", "stop", by=["SM-WD"]),
                _c("detected", "S-CLOCK", "drift_half", by=["SM-WD"], expect=False)]},
    {"id": "EX-OS-01", "group": "vehicle", "kind": "requirement", "provenance": "DERIVED",
     "title": "an overspeed after a coupling opens is detected; a frozen position hides it",
     "checks": [_c("detected", "S-COUPLING", "runaway", by=["SM-OS"]),
                _c("detected", "S-COUPLING", "runaway_position_frozen", by=["SM-OS"], expect=False)]},
    {"id": "EX-REG-01", "group": "interface", "kind": "requirement", "provenance": "DERIVED",
     "title": "the regenerative power follows the lowered minimum torque the vehicle sends",
     "checks": [_c("bound", "S-REGEN", quantity="p_dc", min="$REGEN_POWER_MIN", **{"from": 30.0})]},
    {"id": "EX-DIS-02", "group": "discharge", "kind": "requirement", "provenance": "PROJECT",
     "title": "passive discharge below the limit within its time by the bleeder alone",
     "checks": [_c("passive_discharge", v_limit_V="$V_DISCHARGE", deadline_s="$T_PASSIVE_DISCHARGE")]},
    {"id": "EX-VEH-02", "group": "vehicle", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "a legitimate start torque at standstill with the clutch open: no false trip, no vehicle motion",
     "checks": [_c("no_detection", "S-START"),
                _c("vehicle", "S-START", expect_no_motion=True)]},
    {"id": "EX-META-02", "group": "provenance", "kind": "rule", "provenance": "DERIVED",
     "title": "OPEN values kept open; every item has a verdict row",
     "checks": [_c("open_kept", params=["T_SHUTOFF", "FTTI_TORQUE", "X_UPP", "TLSR_T_MAX"]),
                _c("package_coverage")]},
    {"id": "EX-MOD-02", "group": "modules", "kind": "module", "provenance": "DERIVED",
     "title": "the interfaces and fault classes a source lists are represented in the model",
     "checks": [_c("model_interfaces", interfaces={
         "terminal 15 hardware": ["signal:kl15_hw"], "terminal 30": ["signal:t30_state", "fault:lv_loss"],
         "rotor sensor": ["role:position_control"], "wheel speed": ["signal:wheel_speed_kph"],
         "clock": ["fault:clock"], "mechanical coupling": ["fault:coupling"], "bus": ["fault:e2e"]})]},
    {"id": "EX-RST-01", "group": "supervisor", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "an MCU reset: no PWM authority until the MCU has booted again (rotating and at standstill)",
     "checks": [_c("no_pwm", "S-RESET", "rotating", **{"from": "event:fault:mcu_reset", "to": "event::MCU booted"}),
                _c("no_pwm", "S-RESET", "standstill", **{"from": "event:fault:mcu_reset", "to": "event::MCU booted"})]},
    {"id": "EX-NF-01", "group": "torque window", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "the normal function limits first (the monitor stays silent); with it failed the monitor reacts in time",
     "checks": [_c("normal_first", "S-NORMAL1ST", "healthy", failed_variant="app_failed",
                   deadline_ms="$FTTI_TORQUE")]},
    {"id": "EX-SM-01", "group": "hardware path", "kind": "safety_mechanism", "provenance": "DERIVED",
     "title": "fast DC-link over-voltage comparator: a complete specification and a demonstrated path",
     "spec": {"target_fault": "DC-link over-voltage (battery disconnected in regeneration)",
              "principle": "analog comparator on the hardware DC-link voltage, 5 us filter",
              "reaction": "ASC by the hardware path (speed-dependent rule), independent of the processor",
              "timing": "detection and reaction within $FAST_REACT", "asil": "as the safety goal it serves",
              "allocation": "logic device and gate drivers (HW)", "verification": "EX-HW-02 (simulation), bench test",
              "shared": "HV measurement divider shared with the control sensor"},
     "checks": [_c("sm_spec", demonstrated_by="EX-HW-02")]},
    {"id": "EX-Q-01", "group": "questions", "kind": "question", "provenance": "DERIVED",
     "title": "which tolerance and transition time decide the physical safe state?",
     "checks": [_c("question", closes=["T_TOL", "P_TOL", "SS_TRANSITION"])]},
]

# ------------------------------------------------------------------------------------------------- the hierarchy
# the example's structure: two vehicle goals, three top-level requirements (one whose text was not provided), the
# functional requirements below them, the technical requirements, a mechanism - and a proposal (a DERIVED addition
# the simulation shows is missing: never a source requirement)
ROLLUP = "judged through the requirements traced to it (the roll-up)"
ITEMS[:0] = [
    {"id": "EX-SG-01", "level": "SG", "group": "goals", "kind": "requirement", "provenance": "PAST-PROJECT",
     "title": "no unintended vehicle acceleration or deceleration from the electric drive beyond what the driver "
              "controls", "manual": ROLLUP},
    {"id": "EX-SG-02", "level": "SG", "group": "goals", "kind": "requirement", "provenance": "PROJECT",
     "title": "no electric shock from energy left in the high-voltage system after the drive is switched off",
     "manual": ROLLUP},
    {"id": "EX-TLSR-01", "level": "TLSR", "group": "goals", "kind": "requirement", "provenance": "CONFIRMED",
     "title": "prevent unintended torque at standstill", "traces_to": ["EX-SG-01"], "manual": ROLLUP},
    {"id": "EX-TLSR-02", "level": "TLSR", "group": "goals", "kind": "requirement", "provenance": "CONFIRMED",
     "title": "prevent unintended excessive torque while driving", "traces_to": ["EX-SG-01"], "manual": ROLLUP},
    {"id": "EX-TLSR-03", "level": "TLSR", "group": "goals", "kind": "requirement", "provenance": "OPEN",
     "title": "drivetrain destabilisation (the text was not provided)", "traces_to": ["EX-SG-01"],
     "manual": "the requirement text is not provided: ask for it before anything is derived from it"},
]
ITEMS.append(
    {"id": "EX-PROP-01", "level": "TSR", "group": "proposals", "kind": "requirement", "provenance": "DERIVED",
     "proposed": True, "traces_to": ["EX-DIS-01"], "traces_to_inferred": ["EX-HW-04"],
     "title": "with the processor lost while the machine turns and the battery disconnected, the hardware selection "
              "by the DC voltage shall not cycle between freewheeling and ASC: where the back-EMF exceeds X_LOW it "
              "holds the ASC (a speed- or back-EMF-dependent latch)",
     "rationale": "the selection by the DC voltage alone (ASC above X_upp, six-switch-off below X_low) cycles at "
                  "6000 rpm once the active discharge has pulled the link to X_low: every six-switch-off interval lets "
                  "the back-EMF recharge it (energy fed back, C2), the link never stays below the discharge limit, and "
                  "no requirement of the example asks about it; holding the ASC keeps it below",
     "checks": [_c("event", "S-HWVDC", "cycling", kind="strategy", text="hardware HV selection", min_count=6,
                   label="the selection by the DC voltage alone cycles (demonstration)"),
                _c("bound", "S-HWVDC", "cycling", quantity="v_dc", max="$V_DISCHARGE", expect_violation=True,
                   label="... and the link does not stay below the discharge limit (counterexample)",
                   **{"from": 80.0}),
                _c("event", "S-HWVDC", "latched", kind="strategy", text="hardware HV selection", expect=False,
                   label="the ASC held: no cycling"),
                _c("bound", "S-HWVDC", "latched", quantity="v_dc", max="$V_DISCHARGE",
                   label="the ASC held: the link stays below the discharge limit", **{"from": 80.0})]})
HIERARCHY = {
    # level, traces_to (as the example states it)
    "EX-SS-01": ("FSR", ["EX-TLSR-01", "EX-TLSR-02"]), "EX-SS-04": ("FSR", ["EX-TLSR-01"]),
    "EX-TQ-01": ("FSR", ["EX-TLSR-02"]), "EX-TQ-02": ("FSR", ["EX-TLSR-02"]), "EX-TQ-03": ("FSR", ["EX-TLSR-02"]),
    "EX-OSC-01": ("FSR", ["EX-TLSR-03"]), "EX-NF-01": ("FSR", ["EX-TLSR-02"]),
    "EX-SUP-01": ("FSR", ["EX-TLSR-01"]), "EX-SUP-02": ("FSR", ["EX-TLSR-01"]), "EX-SUP-04": ("FSR", ["EX-TLSR-01"]),
    "EX-RST-01": ("FSR", ["EX-TLSR-01"]),
    "EX-OPS-01": ("FSR", ["EX-TLSR-01"]), "EX-OPS-02": ("FSR", ["EX-TLSR-02"]), "EX-OPS-03": ("FSR", ["EX-TLSR-01"]),
    "EX-OPS-04": ("FSR", ["EX-TLSR-02"]), "EX-OPS-05": ("FSR", ["EX-TLSR-01"]),
    "EX-VEH-01": ("FSR", ["EX-SG-01"]), "EX-VEH-02": ("FSR", ["EX-TLSR-01"]), "EX-OS-01": ("FSR", ["EX-TLSR-02"]),
    "EX-ITF-01": ("FSR", ["EX-TLSR-02"]), "EX-ITF-02": ("FSR", ["EX-TLSR-02"]), "EX-ITF-03": ("FSR", ["EX-TLSR-02"]),
    "EX-REG-01": ("FSR", ["EX-TLSR-02"]), "EX-DUAL-01": ("FSR", ["EX-TLSR-02"]),
    "EX-DIS-01": ("FSR", ["EX-SG-02"]), "EX-DIS-02": ("FSR", ["EX-SG-02"]),
    "EX-TQ-04": ("TSR", ["EX-TQ-01"]), "EX-TQ-05": ("TSR", ["EX-TQ-02"]), "EX-TQ-06": ("TSR", ["EX-TQ-03"]),
    "EX-EST-01": ("TSR", ["EX-TQ-01"]), "EX-SS-02": ("TSR", ["EX-SS-01"]), "EX-TSK-01": ("TSR", ["EX-TQ-02"]),
    "EX-HW-01": ("TSR", ["EX-SS-01"]), "EX-HW-02": ("TSR", ["EX-SS-01"]), "EX-HW-03": ("TSR", ["EX-SS-01"]),
    "EX-HW-04": ("TSR", ["EX-HW-01"]), "EX-HW-05": ("TSR", ["EX-HW-03"]),
    "EX-PW-01": ("TSR", ["EX-SS-01"]), "EX-PW-02": ("TSR", ["EX-SS-01"]), "EX-PW-03": ("TSR", ["EX-SS-01"]),
    "EX-PW-04": ("TSR", ["EX-SS-01"]), "EX-PW-05": ("TSR", ["EX-SS-01"]),
    "EX-TST-01": ("TSR", ["EX-SS-01"]), "EX-TST-02": ("TSR", ["EX-SS-01"]), "EX-E2E-01": ("TSR", ["EX-TQ-03"]),
    "EX-IN-01": ("TSR", ["EX-TQ-02"]), "EX-PWM-01": ("TSR", ["EX-SS-01"]), "EX-WD-01": ("TSR", ["EX-TSK-01"]),
    "EX-CLK-01": ("TSR", ["EX-TSK-01"]), "EX-CONF-01": ("TSR", ["EX-SS-01"]), "EX-CAL-01": ("TSR", ["EX-TQ-01"]),
    "EX-MET-01": ("TSR", ["EX-TLSR-01", "EX-TLSR-02"]),
    "EX-SM-01": ("SM", ["EX-HW-02"]),
    "EX-TL-01": ("DEF", []), "EX-SS-03": ("RULE", ["EX-SS-01"]), "EX-SUP-03": ("RULE", ["EX-SUP-02"]),
    "EX-META-01": ("RULE", []), "EX-META-02": ("RULE", []), "EX-MAP-01": ("OUT", ["EX-SS-01"]),
    "EX-RES-01": ("RES", []), "EX-MOD-01": ("MOD", []), "EX-MOD-02": ("MOD", []), "EX-MAN-01": ("ACT", []),
    "EX-Q-01": ("Q", ["EX-SS-01", "EX-TLSR-03"]),
}
for _it in ITEMS:
    if _it["id"] in HIERARCHY:
        _it["level"], _t = HIERARCHY[_it["id"]]
        if _t:
            _it["traces_to"] = list(_t)

REFERENCE_EXAMPLE = {
    "schema": "twb-reference/1",
    "meta": {"title": "Example reference package (synthetic)", "date": "2026-09-30",
             "note": "a neutral demonstration of every check on the built-in synthetic project - not a real "
                     "specification; every value is an example"},
    "provenance": {"CONFIRMED": "confirmed from the source text",
                   "PAST-PROJECT": "a requirement of an earlier project, not re-verified against the source",
                   "PROJECT": "a project condition", "DERIVED": "derived internally",
                   "RESEARCH": "a study value, never a requirement", "OPEN": "undecided: no value",
                   "CONFLICT": "records disagree: variants kept"},
    "customer_tags": ["CONFIRMED", "PAST-PROJECT", "PROJECT"],
    "parameters": PARAMETERS,
    "asil_binding": {"MAX": "MAX_ASIL"},
    "fmeda": FMEDA,
    "scenarios": SCENARIOS,
    "items": ITEMS,
}
