"""The synthetic protection architecture and safety requirements of the built-in example project.

Demonstration data, not a product: every value is an example of what a project declares (sensors and the resources
they need, the sensor roles each consumer reads, reaction paths, safety mechanisms, the safe-state policy, SG / FSR /
TSR).  The built-in project carries it as its ``fault_sim`` section.

Scope: the drive system (inverter and motor).  The battery system's own protection (its current limit and contactor
decisions) and faults of other controllers and of the vehicle network (BMS, VCU, CAN reception) are not assumed: the
battery stays connected unless a scenario opens the relay - an event at the drive's interface that combines with any
drive fault.
"""

from .strategy import TEMPLATES as _TEMPLATES

FAULT_SIM = {
    "topology": "two_level_vsi_star",
    "basis": "synthetic protection architecture (not a product): one MCU runs control, the monitor task and the "
             "software reaction path; a protection logic device (CPLD) forces the gates on hardware trips with the "
             "safe state the MCU pre-selects; gate drivers with desaturation and UVLO; external watchdog",
    "scope": {"item": "drive system: inverter (power stage, gate drivers, control and monitoring MCU, protection "
                      "logic, its sensors) and the motor",
              "interface_events": "the battery relay opening, the low-voltage supply lost and a coupling change are "
                                  "scenario events at the drive's interfaces - alone or combined with drive faults",
              "not_assumed": "faults of other controllers and of the vehicle network (BMS, VCU, CAN reception / "
                             "end-to-end) and the battery system's own protection (its current limit, its contactor "
                             "decisions): the battery stays connected unless a scenario opens the relay"},
    "battery": {"L_uH": 2.0},
    "dc_link": {"bleeder_ohm": 100e3, "active_discharge_ohm": 30.0, "active_discharge_delay_ms": 5.0},
    "control": {"command_period_ms": 10.0, "command_latency_ms": 0.5, "comm_timeout_ms": 50.0,
                "timeout_ramp_Nm_per_ms": 5.0, "speed_filter_ms": 0.5, "v_limit_fraction": 1.0,
                "angle_comp_periods": 1.5, "three_sensors": False, "torque_rate_Nm_per_ms": None,
                "reset_output": "off", "boot_ms": 20.0},
    "sensors": [
        {"name": "CS_A", "kind": "current", "gain_tol": 0.01, "offset_tol_A": 2.0, "delay_us": 0.0, "quant_A": 0.5,
         "valid_range_A": [-1500.0, 1500.0], "lost_value_A": 0.0, "resources": ["SENS_5V"]},
        {"name": "CS_B", "kind": "current", "gain_tol": 0.01, "offset_tol_A": 2.0, "delay_us": 0.0, "quant_A": 0.5,
         "valid_range_A": [-1500.0, 1500.0], "lost_value_A": 0.0, "resources": ["SENS_5V"]},
        {"name": "CS_C", "kind": "current", "gain_tol": 0.01, "offset_tol_A": 2.0, "delay_us": 0.0, "quant_A": 0.5,
         "valid_range_A": [-1500.0, 1500.0], "lost_value_A": 0.0, "resources": ["SENS_5V"]},
        {"name": "RES", "kind": "position", "offset_tol_deg": 0.5, "delay_us": 20.0, "quant_deg": 0.05,
         "los_detect_ms": 1.0, "resources": ["RDC"]},
        {"name": "VDC_MAIN", "kind": "voltage", "gain_tol": 0.01, "offset_tol_V": 2.0, "quant_V": 0.25,
         "valid_range_V": [0.0, 1000.0], "resources": []},
        {"name": "VDC_HW", "kind": "voltage", "gain_tol": 0.02, "offset_tol_V": 5.0, "resources": ["HW_REF"]},
    ],
    "roles": {"current_a": "CS_A", "current_b": "CS_B", "current_c": "CS_C", "position_control": "RES",
              "vdc_control": "VDC_MAIN", "vdc_hw": "VDC_HW"},
    "resources": {"MCU": "main microcontroller: control task, monitor task, software reaction path",
                  "SENS_5V": "current-sensor supply", "RDC": "resolver-to-digital converter and excitation",
                  "GATE_UPPER": "upper gate-driver supply", "GATE_LOWER": "lower gate-driver supply",
                  "CPLD": "protection logic device (forces the gates)", "HW_REF": "hardware comparator reference",
                  "WD": "external watchdog"},
    "paths": [
        {"id": "SW", "delay_us": 100.0, "resources": ["MCU"],
         "basis": "the MCU writes the reaction to the PWM unit (one control period)"},
        {"id": "HW", "delay_us": 5.0, "resources": ["CPLD"],
         "basis": "the CPLD forces the gates with the safe state the MCU pre-selects"},
    ],
    "mechanisms": [
        {"id": "SM-TQ", "kind": "torque_monitor", "path": "SW", "reaction": "safe_state", "period_ms": 1.0,
         "resources": ["MCU"],
         "params": {"abs_Nm": 30.0, "rel": 0.15, "response_tau_ms": 2.0, "delay_ms": 1.0, "debounce_ms": 3.0,
                    "ramp_Nm_per_ms": 50.0, "request_input": "monitor_message"},
         "text": "torque plausibility: torque estimated from the measured currents and angle vs the request "
                 "(own copy of the request message)", "latent_test": "power-up: injected torque error",
         "coverage": "medium"},
        {"id": "SM-OC", "kind": "overcurrent_hw", "path": "HW", "reaction": "safe_state",
         "params": {"threshold_A": 900.0, "filter_us": 2.0}, "resources": ["CPLD", "HW_REF"],
         "text": "hardware over-current comparators on the phase-current sensor outputs",
         "latent_test": "power-up: comparator test pulse", "coverage": "high"},
        {"id": "SM-OV", "kind": "overvoltage_hw", "path": "HW", "reaction": "safe_state",
         "params": {"threshold_V": 780.0, "filter_us": 5.0}, "resources": ["CPLD", "HW_REF"],
         "text": "hardware DC-link over-voltage comparator (own divider)",
         "latent_test": "power-up: comparator test pulse", "coverage": "high"},
        {"id": "SM-DSAT", "kind": "desat", "path": "HW", "reaction": "safe_state",
         "params": {"threshold_A": 1500.0, "turnoff_us": 2.0}, "resources": [],
         "text": "gate-driver desaturation detection with soft turn-off (per switch)",
         "latent_test": "gate-driver self-test at power-up", "coverage": "high"},
        {"id": "SM-UVLO", "kind": "gate_uvlo", "path": "HW", "reaction": "safe_state", "params": {"delay_us": 2.0},
         "resources": [], "text": "gate-driver under-voltage lockout report",
         "latent_test": "gate-driver self-test at power-up", "coverage": "high"},
        {"id": "SM-SUM", "kind": "current_plausibility", "path": "SW", "reaction": "safe_state", "period_ms": 0.1,
         "resources": ["MCU"], "params": {"threshold_A": 60.0, "debounce_ms": 1.0},
         "text": "sum of the three measured phase currents", "latent_test": "power-up: sum check with a test offset",
         "coverage": "medium"},
        {"id": "SM-LOS", "kind": "position_los", "path": "SW", "reaction": "safe_state", "period_ms": 0.1,
         "resources": ["MCU"], "params": {}, "text": "resolver loss-of-signal flag",
         "latent_test": "RDC built-in test at power-up", "coverage": "high"},
        {"id": "SM-OVSW", "kind": "overvoltage_sw", "path": "SW", "reaction": "safe_state", "period_ms": 0.1,
         "resources": ["MCU"], "params": {"threshold_V": 760.0, "debounce_ms": 0.2},
         "text": "software DC-link over-voltage (control voltage sensor)", "coverage": "medium"},
        {"id": "SM-TO", "kind": "command_timeout", "path": "SW", "reaction": "torque_zero", "period_ms": 10.0,
         "resources": ["MCU"], "params": {"timeout_ms": 50.0}, "text": "torque command timeout",
         "coverage": "high"},
        {"id": "SM-WD", "kind": "watchdog", "path": "HW", "reaction": "safe_state", "params": {"timeout_ms": 5.0},
         "resources": ["WD", "CPLD"], "text": "external watchdog on the control task's alive signal",
         "latent_test": "power-up: forced watchdog expiry", "coverage": "medium"},
    ],
    "policy": {
        "rules": [{"if": {"uvlo": "lower"}, "then": "asc_high"},
                  {"if": {"uvlo": "upper"}, "then": "asc_low"},
                  {"if": {"detected_by": "desat", "device": "lower"}, "then": "asc_high"},
                  {"if": {"detected_by": "desat", "device": "upper"}, "then": "asc_low"},
                  {"if": {"speed_above_rpm": 6000.0}, "then": "asc_low"},
                  {"else": "six_switch_off"}],
        "priority": ["asc_low", "asc_high", "six_switch_off", "torque_zero"],
        "latch": True, "recovery_after_ms": 50.0, "recovery_max_attempts": 1, "restart": "flying",
        "restart_ramp_Nm_per_ms": 50.0, "after_reset": "restart", "speed_hysteresis_rpm": 300.0,
        "replace_unexecutable": True,
        "basis": "synthetic policy: above 6000 rpm (the six-switch-off rectification onset is 8270 rpm at 600 V, "
                 "about 6900 rpm at 500 V) the active short circuit; a lost gate supply or a desaturated device "
                 "selects the ASC of the other side"},
    "vehicle": {"from_driveline": True},
    # reaction strategies (reactions as step sequences, see strategy.py): the representative ways of shaping the
    # transition into the safe state - available to the policy, the mechanisms and the candidate comparison
    "strategies": [{k: v for k, v in t.items() if k != "text_ko"} for t in _TEMPLATES.values()],
    "requirements": {
        "basis": "synthetic safety requirements for the demonstration (values are examples, not an item's HARA)",
        "safety_goals": [
            {"id": "SG-01", "text": "avoid unintended acceleration torque", "asil": "C", "ftti_ms": 100.0,
             "hazard": "accel", "vehicle": {"max_delta_v_mps": 0.5},
             "safe_state": "torque-free drive (|T| small) held until the next power cycle",
             "situation": "driving, any speed and request"},
            {"id": "SG-02", "text": "avoid unintended deceleration torque", "asil": "C", "ftti_ms": 100.0,
             "hazard": "decel", "vehicle": {"max_delta_v_mps": 0.5},
             "safe_state": "torque-free drive; the transition itself limited in braking torque",
             "situation": "driving at speed, low-friction road included"},
            {"id": "SG-03", "text": "avoid HV component overstress (DC-link over-voltage, device over-current, magnet "
                                    "demagnetisation)",
             "asil": "B", "ftti_ms": 10.0, "hazard": "component",
             "safe_state": "DC link, phase currents and d-axis current inside the component ratings",
             "situation": "any operation incl. battery disconnection and regeneration"},
        ],
        "fsr": [
            {"id": "FSR-01", "sg": ["SG-01", "SG-02"], "asil": "C",
             "text": "detect a torque deviation and bring the drive into a torque-free safe state within the FHTI",
             "mechanisms": ["SM-TQ", "SM-OC", "SM-DSAT", "SM-SUM", "SM-LOS", "SM-WD", "SM-UVLO"],
             "fdti_budget_ms": 20.0, "frti_budget_ms": 30.0, "safe_state": "TSR-03",
             "warning": "warning lamp and propulsion-loss message to the driver; no automatic restart",
             "allocation": ["MCU monitor task", "CPLD", "gate drivers"],
             "verification": ["simulation", "fault injection", "HIL"]},
            {"id": "FSR-02", "sg": ["SG-03"], "asil": "B",
             "text": "limit DC-link voltage, phase current and d-axis current to the component ratings",
             "mechanisms": ["SM-OV", "SM-OVSW", "SM-OC", "SM-DSAT"],
             "fdti_budget_ms": 1.0, "frti_budget_ms": 2.0, "safe_state": None,
             "warning": "HV system fault message to the vehicle (the battery system decides on its contactor)",
             "allocation": ["CPLD", "HW comparators", "gate drivers", "MCU monitor task"],
             "verification": ["simulation", "fault injection", "bench test"]},
        ],
        "tsr": [
            {"id": "TSR-01", "fsr": "FSR-01", "text": "no unintended acceleration torque beyond the dynamic window "
                                                      "for longer than 50 ms",
             "criterion": {"type": "torque_window", "side": "accel", "abs_Nm": 50.0, "rel": 0.2,
                           "delay_ms": 11.0, "response_tau_ms": 2.0, "tolerance_ms": 50.0, "reduction_allowed": True,
                           "origin": "t0"},
             "asil": "C", "allocation": "SW monitor task (SM-TQ) + reaction manager", "verification": ["simulation", "HIL"],
             "rationale": "synthetic example: the controllability window of the item's HARA"},
            {"id": "TSR-02", "fsr": "FSR-01", "text": "no unintended deceleration torque beyond the dynamic window "
                                                      "for longer than 50 ms",
             "criterion": {"type": "torque_window", "side": "decel", "abs_Nm": 50.0, "rel": 0.2,
                           "delay_ms": 11.0, "response_tau_ms": 2.0, "tolerance_ms": 50.0, "reduction_allowed": True,
                           "origin": "t0"},
             "asil": "C", "allocation": "SW monitor task (SM-TQ) + reaction manager", "verification": ["simulation", "HIL"],
             "rationale": "synthetic example: the controllability window of the item's HARA"},
            {"id": "TSR-03", "fsr": "FSR-01", "text": "after a detection the drive reaches |T| <= 60 N*m within 30 ms "
                                                      "and holds it 20 ms",
             "criterion": {"type": "safe_state", "origin": "detection", "within_ms": 30.0, "hold_ms": 20.0,
                           "conditions": [{"quantity": "torque_abs", "max": 60.0}]},
             "asil": "C", "allocation": "reaction manager + bridge (ASC / 6SO)", "verification": ["simulation", "HIL"],
             "rationale": "synthetic example: FRTI budget of FSR-01; 60 N*m residual torque the driver controls"},
            {"id": "TSR-04", "fsr": "FSR-01", "text": "FDTI / FRTI within the budgets, FHTI within the FTTI",
             "criterion": {"type": "timing"},
             "asil": "C", "allocation": "safety architecture", "verification": ["simulation", "analysis"],
             "rationale": "FDTI + FRTI budgets of FSR-01 inside the FTTI of SG-01 / SG-02"},
            {"id": "TSR-05", "fsr": "FSR-01", "text": "no detection and no reaction in fault-free operation",
             "criterion": {"type": "no_false_reaction"},
             "asil": "QM", "allocation": "monitor thresholds and debounce", "verification": ["simulation", "vehicle test"],
             "rationale": "availability: a safety mechanism shall not trip in fault-free operation"},
            {"id": "TSR-06", "fsr": "FSR-02", "level": "component", "text": "DC-link voltage <= 850 V",
             "criterion": {"type": "bound", "quantity": "v_dc", "max": 850.0, "origin": "t0"},
             "asil": "B", "allocation": "HW over-voltage comparator + CPLD", "verification": ["simulation", "bench test"],
             "rationale": "synthetic example: DC-link capacitor and module voltage rating"},
            {"id": "TSR-07", "fsr": "FSR-02", "level": "component", "text": "phase current <= 1600 A peak",
             "criterion": {"type": "bound", "quantity": "i_phase_abs", "max": 1600.0, "origin": "t0"},
             "asil": "B", "allocation": "HW over-current comparator + desaturation", "verification": ["simulation",
                                                                                                    "bench test"],
             "rationale": "synthetic example: module peak current rating"},
            {"id": "TSR-08", "fsr": "FSR-01", "text": "the transition into the safe state produces no braking torque "
                                                      "above 450 N*m at the motor shaft",
             "criterion": {"type": "bound", "quantity": "torque_brake", "max": 450.0, "origin": "t0"},
             "asil": "C", "allocation": "SW reaction manager (reaction strategy)",
             "verification": ["simulation", "HIL"],
             "rationale": "synthetic example: the braking torque the vehicle stability analysis allows at the "
                          "motor shaft on a low-friction road (an item value, not derived here)"},
            {"id": "TSR-09", "fsr": "FSR-02", "level": "component",
             "text": "the d-axis current stays above the irreversible demagnetisation limit -1000 A",
             "criterion": {"type": "bound", "quantity": "i_d", "min": -1000.0, "origin": "t0"},
             "asil": "B", "allocation": "reaction strategy + HW over-current protection",
             "verification": ["simulation", "analysis"],
             "rationale": "synthetic example: the magnet supplier's knee at the maximum magnet temperature, less a "
                          "margin (an item value, not derived here)"},
        ],
    },
}
