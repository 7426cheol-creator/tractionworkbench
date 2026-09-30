"""How the causal simulation names reactions, faults, quantities and parameters in the texts people read (events,
verdict details, scope statements).  The results keep the codes in their own fields; only the prose uses these."""

from __future__ import annotations

REACTION_LABELS = {"safe_state": "safe state (policy)", "asc_low": "ASC-low", "asc_high": "ASC-high",
                   "six_switch_off": "6SO", "torque_zero": "zero torque", "report_only": "report only", "pwm": "PWM",
                   "off": "gates off"}
FAULT_LABELS = {"sensor": "sensor fault", "torque_command": "torque command fault",
                "control_task_stop": "control task stop", "mcu_reset": "MCU reset", "pwm_output": "PWM output fault",
                "switch_open": "switch open", "switch_short": "switch short", "diode_open": "diode open",
                "phase_open": "phase open", "gate_supply_loss": "gate supply loss",
                "battery_disconnect": "battery disconnect", "contactor_stuck": "contactor welded",
                "charge_acceptance_loss": "loss of charge acceptance", "mechanism_disabled": "latent mechanism fault",
                "path_lost": "latent reaction-path loss", "resource_loss": "shared resource loss"}
QUANTITY_LABELS = {"v_dc": "V_dc", "i_phase_abs": "|i_phase| (largest of the three phases)",
                   "i_bat_charge": "battery charging current", "torque": "shaft torque", "torque_abs": "|shaft torque|",
                   "speed": "speed", "bridge_in": "bridge state"}
PARAM_LABELS = {"duration_s": "duration [s]", "duration_ms": "duration [ms]", "limit_A": "limit [A]",
                "threshold_A": "threshold [A]", "threshold_V": "threshold [V]", "debounce_ms": "debounce [ms]",
                "delay_us": "delay [us]", "filter_us": "filter [us]", "timeout_ms": "timeout [ms]"}
VALUE_LABELS = {**REACTION_LABELS, "upper_on": "upper switch on", "lower_on": "lower switch on",
                "stuck_duty": "stuck duty", "stuck_last": "stuck at the last value", "sign_flip": "sign flip"}


def reaction_label(r) -> str:
    return REACTION_LABELS.get(r, str(r))


def fault_label(kind) -> str:
    return FAULT_LABELS.get(kind, str(kind).replace("_", " "))


def quantity_label(q) -> str:
    return QUANTITY_LABELS.get(q, str(q))


def param_label(k) -> str:
    return PARAM_LABELS.get(k, str(k).replace("_", " "))


def value_label(v) -> str:
    return VALUE_LABELS.get(v, v.replace("_", " ")) if isinstance(v, str) else str(v)


def params_text(params: dict | None) -> str:
    """``key=value`` pairs as people read them (no code names)."""
    return ", ".join(f"{param_label(k)}={value_label(v)}" for k, v in (params or {}).items())


def fault_text(kind, params: dict | None = None) -> str:
    p = params_text(params)
    return fault_label(kind) + (f" ({p})" if p else "")
