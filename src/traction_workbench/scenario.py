"""Scenario boundaries: speed, DC terminal, temperatures, control policy.

In this MVP speed, Vdc and temperatures are *given* scenario boundaries; the
coupled algebraic problem is solved at those boundaries.  DC source limits
are average power/current limits at the inverter DC terminal, not capacitor
ripple RMS or cable transient peaks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .errors import InputValidationError
from .models.flux import _finite


def _opt_nonneg(name: str, value):
    """None = not declared (never read as unlimited); math.inf = explicitly declared unlimited."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise InputValidationError(f"expected a number, got {value!r}", field=name) from None
    if math.isinf(v) and v > 0:
        return v
    v = _finite(name, value)
    if v < 0:
        raise InputValidationError("limit magnitudes must be >= 0", field=name)
    return v


@dataclass(frozen=True)
class DcSourceLimits:
    discharge_power_max_W: float | None = None
    charge_power_max_W: float | None = None
    discharge_current_max_A: float | None = None
    charge_current_max_A: float | None = None
    source: str = ""

    def __post_init__(self):
        for name in ("discharge_power_max_W", "charge_power_max_W", "discharge_current_max_A", "charge_current_max_A"):
            object.__setattr__(self, name, _opt_nonneg(name, getattr(self, name)))

    @property
    def complete(self) -> bool:
        return all(v is not None for v in (self.discharge_power_max_W, self.charge_power_max_W,
                                           self.discharge_current_max_A, self.charge_current_max_A))

    @property
    def any_declared(self) -> bool:
        """At least one DC limit was declared (a finite value or an explicit math.inf)."""
        return any(v is not None for v in (self.discharge_power_max_W, self.charge_power_max_W,
                                           self.discharge_current_max_A, self.charge_current_max_A))

    def effective_discharge_W(self, vdc: float) -> float | None:
        """Tightest finite discharge-side power bound; None when no finite limit binds."""
        vals = [v for v in (self.discharge_power_max_W,
                            None if self.discharge_current_max_A is None else vdc * self.discharge_current_max_A)
                if v is not None and math.isfinite(v)]
        return min(vals) if vals else None

    def effective_charge_W(self, vdc: float) -> float | None:
        vals = [v for v in (self.charge_power_max_W,
                            None if self.charge_current_max_A is None else vdc * self.charge_current_max_A)
                if v is not None and math.isfinite(v)]
        return min(vals) if vals else None

    def describe(self) -> dict:
        return {
            "discharge_power_max_W": self.discharge_power_max_W,
            "charge_power_max_W": self.charge_power_max_W,
            "discharge_average_current_max_A": self.discharge_current_max_A,
            "charge_average_current_max_A": self.charge_current_max_A,
            "meaning": "average power/current at the inverter DC terminal (not ripple RMS or transient peak); "
                       "null = not declared (never read as unlimited), Infinity = declared unlimited",
            "source": self.source,
        }


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    speed_rpm: float
    Vdc_V: float
    source_limits: DcSourceLimits
    winding_temp_C: float | None = None
    magnet_temp_C: float | None = None
    coolant_temp_C: float | None = None
    initial_state: str | None = None
    switching_frequency_Hz: float | None = None
    description: str = ""

    def __post_init__(self):
        n = _finite("speed_rpm", self.speed_rpm)
        v = _finite("Vdc_V", self.Vdc_V)
        if v <= 0:
            raise InputValidationError("inverter DC terminal voltage must be strictly positive", field="Vdc_V")
        object.__setattr__(self, "speed_rpm", n)
        object.__setattr__(self, "Vdc_V", v)
        for name in ("winding_temp_C", "magnet_temp_C", "coolant_temp_C", "switching_frequency_Hz"):
            val = getattr(self, name)
            if val is not None:
                object.__setattr__(self, name, _finite(name, val))
        if self.switching_frequency_Hz is not None and self.switching_frequency_Hz <= 0:
            raise InputValidationError("switching frequency must be > 0", field="switching_frequency_Hz")
        if not isinstance(self.source_limits, DcSourceLimits):
            raise InputValidationError("source_limits must be DcSourceLimits", field="source_limits")

    @property
    def omega_m(self) -> float:
        return 2.0 * math.pi * self.speed_rpm / 60.0

    def with_(self, **changes) -> "Scenario":
        return replace(self, **changes)

    def describe(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "speed_rpm_mechanical": self.speed_rpm,
            "Vdc_V_inverter_dc_terminal": self.Vdc_V,
            "dc_source_limits": self.source_limits.describe(),
            "winding_temp_C": self.winding_temp_C,
            "magnet_temp_C": self.magnet_temp_C,
            "coolant_temp_C": self.coolant_temp_C,
            "initial_state": self.initial_state,
            "switching_frequency_Hz": self.switching_frequency_Hz,
            "description": self.description,
        }
