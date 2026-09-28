"""Unit and definition normalisation (UC00).

Every input quantity carries an explicit unit and, where it matters, its
definition: peak/RMS, phase/line-to-line, mechanical/electrical speed,
per-phase/line-to-line resistance, fundamental/total/pulse current.  A value
whose definition is ambiguous is rejected (INVALID_INPUT) instead of being
guessed; every accepted conversion is recorded (field, given, converted,
rule) and ends up in the decision record.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .errors import InputValidationError

SCALE = {
    "current": {"A": 1.0, "kA": 1e3, "mA": 1e-3},
    "voltage": {"V": 1.0, "kV": 1e3, "mV": 1e-3},
    "resistance": {"ohm": 1.0, "Ohm": 1.0, "Ω": 1.0, "mohm": 1e-3, "mOhm": 1e-3, "mΩ": 1e-3, "uohm": 1e-6},
    "inductance": {"H": 1.0, "mH": 1e-3, "uH": 1e-6, "µH": 1e-6},
    "flux": {"Wb": 1.0, "mWb": 1e-3, "Vs": 1.0, "V*s": 1.0},
    "power": {"W": 1.0, "kW": 1e3, "MW": 1e6},
    "torque": {"N*m": 1.0, "Nm": 1.0, "N·m": 1.0, "N m": 1.0, "kN*m": 1e3},
    "frequency": {"Hz": 1.0, "kHz": 1e3},
    "time": {"s": 1.0, "ms": 1e-3, "us": 1e-6, "min": 60.0},
    "temperature": {"degC": 1.0, "C": 1.0, "°C": 1.0},
    "drag": {"N*m/(rad/s)": 1.0, "N*m*s/rad": 1.0, "Nm/(rad/s)": 1.0, "Nms": 1.0},
    "loss_coeff": {"W/A^2": 1.0, "W/A2": 1.0, "ohm": 1.0},
    "capacitance": {"F": 1.0, "mF": 1e-3, "uF": 1e-6, "µF": 1e-6},
    "energy": {"J": 1.0, "kJ": 1e3},
    "thermal_resistance": {"K/W": 1.0},
    "thermal_capacity": {"J/K": 1.0, "kJ/K": 1e3},
}


@dataclass
class Conversions:
    records: list = field(default_factory=list)

    def add(self, fieldname: str, given, converted, rule: str) -> None:
        self.records.append({"field": fieldname, "given": given, "converted": converted, "rule": rule})


def _num(fieldname: str, v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise InputValidationError(f"expected a number, got {v!r}", field=fieldname) from None
    if not math.isfinite(x):
        raise InputValidationError(f"non-finite value {v!r}", field=fieldname)
    return x


def quantity(obj, kind: str, fieldname: str, conv: Conversions | None = None, allow_list: bool = False):
    """{'value': x, 'unit': u} -> SI float (or list of floats)."""
    if not isinstance(obj, dict) or "value" not in obj or "unit" not in obj:
        raise InputValidationError("expected {'value': ..., 'unit': ...} with an explicit unit", field=fieldname)
    unit = str(obj["unit"]).strip()
    table = SCALE[kind]
    if unit not in table:
        raise InputValidationError(f"unit {unit!r} not accepted for {kind} (accepted: {', '.join(table)})",
                                   field=fieldname)
    k = table[unit]
    v = obj["value"]
    if isinstance(v, (list, tuple)):
        if not allow_list:
            raise InputValidationError("a single value is expected", field=fieldname)
        out = [_num(fieldname, x) * k for x in v]
    else:
        out = _num(fieldname, v) * k
    if conv is not None and k != 1.0:
        conv.add(fieldname, obj, out, f"{unit} -> SI (x{k:g})")
    return out


def current_peak(obj, fieldname: str, conv: Conversions, allow_list: bool = False):
    """Fundamental phase-peak current; RMS must be declared, total/pulse values are rejected."""
    basis = obj.get("basis") if isinstance(obj, dict) else None
    if basis is None:
        raise InputValidationError(
            "current definition missing: declare basis 'fundamental_peak' or 'fundamental_rms' "
            "(a plain '600 A' could be fundamental peak, RMS or a pulse value)", field=fieldname)
    val = quantity(obj, "current", fieldname, conv, allow_list)
    if basis == "fundamental_peak":
        return val
    if basis == "fundamental_rms":
        out = [x * math.sqrt(2) for x in val] if isinstance(val, list) else val * math.sqrt(2)
        conv.add(fieldname, obj, out, "fundamental RMS -> fundamental phase peak (x sqrt 2)")
        return out
    if basis in ("total_rms", "pulse_peak", "instantaneous_peak"):
        raise InputValidationError(
            f"{basis} cannot be used as a fundamental-amplitude limit: the fundamental model does not approve PWM "
            f"ripple, pulse peaks, SOA or OC overshoot", field=fieldname)
    raise InputValidationError(f"unknown current basis {basis!r}", field=fieldname)


def voltage_phase_peak(obj, fieldname: str, conv: Conversions) -> float:
    basis = obj.get("basis") if isinstance(obj, dict) else None
    val = quantity(obj, "voltage", fieldname, conv)
    rules = {
        "phase_peak": (1.0, None),
        "phase_rms": (math.sqrt(2), "phase RMS -> phase peak (x sqrt 2)"),
        "line_line_rms": (math.sqrt(2.0 / 3.0), "line-to-line RMS -> phase peak (x sqrt(2/3))"),
        "line_line_peak": (1.0 / math.sqrt(3), "line-to-line peak -> phase peak (/ sqrt 3)"),
    }
    if basis not in rules:
        raise InputValidationError("voltage definition missing or unknown: declare basis phase_peak, phase_rms, "
                                   "line_line_rms or line_line_peak", field=fieldname)
    k, rule = rules[basis]
    if rule:
        conv.add(fieldname, obj, val * k, rule)
    return val * k


def resistance_per_phase(obj, connection: str, fieldname: str, conv: Conversions) -> float:
    ref = obj.get("reference") if isinstance(obj, dict) else None
    val = quantity(obj, "resistance", fieldname, conv)
    if ref == "per_phase":
        return val
    if ref == "line_to_line":
        if connection != "wye":
            raise InputValidationError("line-to-line resistance can only be converted for a confirmed wye winding",
                                       field=fieldname)
        conv.add(fieldname, obj, val / 2, "wye: line-to-line resistance -> per-phase (/ 2)")
        return val / 2
    raise InputValidationError("resistance definition missing: declare reference 'per_phase' or 'line_to_line' "
                               "(a line-to-line measurement must not be used as per-phase)", field=fieldname)


def speed_rpm(obj, fieldname: str, conv: Conversions, pole_pairs: int | None = None, allow_list: bool = False):
    if not isinstance(obj, dict) or "unit" not in obj:
        raise InputValidationError("speed needs an explicit unit", field=fieldname)
    unit = obj["unit"]
    kind = obj.get("kind", "mechanical" if unit == "rpm" else None)
    v = obj.get("value")
    vals = v if isinstance(v, (list, tuple)) else [v]
    if isinstance(v, (list, tuple)) and not allow_list:
        raise InputValidationError("a single value is expected", field=fieldname)
    vals = [_num(fieldname, x) for x in vals]
    if kind not in ("mechanical", "electrical"):
        raise InputValidationError("speed kind must be 'mechanical' or 'electrical'", field=fieldname)
    if unit == "rpm":
        out = vals
    elif unit == "rad/s":
        out = [x * 60.0 / (2 * math.pi) for x in vals]
        conv.add(fieldname, obj, out, "rad/s -> rpm")
    elif unit == "Hz":
        out = [x * 60.0 for x in vals]
        conv.add(fieldname, obj, out, "Hz -> rpm")
    else:
        raise InputValidationError(f"speed unit {unit!r} not accepted (rpm, rad/s, Hz)", field=fieldname)
    if kind == "electrical":
        if not pole_pairs:
            raise InputValidationError("electrical speed needs the pole-pair count to convert", field=fieldname)
        out = [x / pole_pairs for x in out]
        conv.add(fieldname, obj, out, f"electrical -> mechanical (/ p = {pole_pairs})")
    return out if isinstance(v, (list, tuple)) else out[0]


KT_UNITS = {"N*m/A": 1.0, "Nm/A": 1.0, "N·m/A": 1.0, "N m/A": 1.0, "mN*m/A": 1e-3, "mNm/A": 1e-3,
            "kN*m/A": 1e3, "kNm/A": 1e3}


def pm_flux_linkage(motor: dict, pole_pairs: int, conv: Conversions, connection: str = "wye") -> float:
    """psi_PM (amplitude-invariant phase peak) from psi_pm, Ke or Kt with complete conventions only.

    Kt (independent review F09) needs an explicit unit from ``KT_UNITS`` (mN*m/A, kN*m/A, ... are converted,
    anything else is rejected - never read as N*m/A), a current basis (fundamental peak / RMS) and the
    current reference: ``line`` (= the wye-equivalent phase current the dq model uses) or ``winding_phase``
    (accepted for a wye winding; for a declared wye-equivalent of another winding the physical winding
    current differs from the line current and the value is rejected).
    """
    if "psi_pm" in motor:
        obj = motor["psi_pm"]
        if obj.get("basis") != "phase_peak":
            raise InputValidationError("psi_pm must declare basis 'phase_peak' (amplitude-invariant dq)",
                                       field="motor.psi_pm")
        return quantity(obj, "flux", "motor.psi_pm", conv)
    if "Ke" in motor:
        obj = motor["Ke"]
        per = obj.get("per_speed")
        if not isinstance(per, dict):
            raise InputValidationError("Ke needs per_speed {'value','unit','kind'}; its convention is otherwise "
                                       "ambiguous and is not converted automatically", field="motor.Ke")
        if connection != "wye" and obj.get("basis") in ("phase_peak", "phase_rms"):
            raise InputValidationError("a phase-basis Ke is ambiguous for a wye-equivalent of another winding: give "
                                       "the line-to-line value", field="motor.Ke")
        v_pk = voltage_phase_peak({k: obj[k] for k in ("value", "unit", "basis") if k in obj}, "motor.Ke", conv)
        n = speed_rpm(per, "motor.Ke.per_speed", conv, pole_pairs)
        we = pole_pairs * 2 * math.pi * n / 60.0
        if we == 0:
            raise InputValidationError("Ke reference speed must be non-zero", field="motor.Ke.per_speed")
        psi = v_pk / we
        conv.add("motor.Ke", obj, psi, "back-EMF constant -> psi_PM = V_phase_peak / omega_e")
        return psi
    if "Kt" in motor:
        obj = motor["Kt"]
        if obj.get("definition") != "shaft_or_em_torque_per_current_at_id0" or obj.get("torque") != "electromagnetic":
            raise InputValidationError("Kt converts to psi_PM only when defined as electromagnetic torque per "
                                       "current at id = 0 (definition 'shaft_or_em_torque_per_current_at_id0', "
                                       "torque 'electromagnetic')", field="motor.Kt")
        unit = str(obj.get("unit", "")).strip()
        if unit not in KT_UNITS:
            raise InputValidationError(f"Kt unit {unit!r} not accepted (accepted: {', '.join(KT_UNITS)}); a torque "
                                       f"constant without an explicit SI-convertible unit is not guessed",
                                       field="motor.Kt")
        ref = obj.get("current_reference")
        if ref not in ("line", "winding_phase"):
            raise InputValidationError("Kt needs current_reference 'line' (wye-equivalent phase current) or "
                                       "'winding_phase'", field="motor.Kt")
        if ref == "winding_phase" and connection != "wye":
            raise InputValidationError("Kt per winding-phase current is ambiguous for a wye-equivalent of another "
                                       "winding (winding current != line current): give it per line current",
                                       field="motor.Kt")
        basis = obj.get("current_basis")
        k = _num("motor.Kt", obj.get("value")) * KT_UNITS[unit]
        if KT_UNITS[unit] != 1.0:
            conv.add("motor.Kt", obj, k, f"{unit} -> N*m/A (x{KT_UNITS[unit]:g})")
        if basis == "fundamental_rms":
            k = k / math.sqrt(2)
        elif basis != "fundamental_peak":
            raise InputValidationError("Kt needs current_basis fundamental_peak or fundamental_rms", field="motor.Kt")
        if k <= 0:
            raise InputValidationError("Kt must be > 0", field="motor.Kt")
        psi = k / (1.5 * pole_pairs)
        conv.add("motor.Kt", obj, psi, "Kt (per phase-peak A, id = 0) -> psi_PM = Kt / (1.5 p)")
        return psi
    raise InputValidationError("PM flux linkage missing (psi_pm, Ke or Kt)", field="motor")
