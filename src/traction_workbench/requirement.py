"""Customer requirement objects.

A requirement keeps its original wording and is never modified by a
scenario.  In this MVP the supported quantity is shaft torque at the motor
shaft port; the operator is either ``achieve`` (deliver the signed target
torque) or ``band`` (any torque within target +- band).  A missing duration
means the requirement is interpreted as a static item only - the duration
aspect stays undetermined and is never read as "continuous".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .errors import InputValidationError
from .models.flux import _finite


@dataclass(frozen=True)
class Requirement:
    req_id: str
    text: str
    target_Nm: float
    speed_rpm: float
    Vdc_V: float | tuple[float, float]
    revision: str = "A"
    quantity: str = "shaft_torque"
    port: str = "motor_shaft"
    operator: str = "achieve"
    band_Nm: float = 0.0
    Vdc_quantifier: str = "at_point"
    duration_s: float | None = None
    initial_state: str | None = None
    coolant_temp_C: float | None = None
    winding_temp_C: float | None = None
    magnet_temp_C: float | None = None
    switching_frequency_Hz: float | None = None
    exclusions: tuple[str, ...] = ()
    source: str = ""

    def __post_init__(self):
        if not str(self.req_id).strip():
            raise InputValidationError("requirement id is required", field="req_id")
        if self.quantity != "shaft_torque" or self.port != "motor_shaft":
            raise InputValidationError(
                "only shaft torque at the motor shaft port is supported in this MVP "
                f"(got quantity={self.quantity!r}, port={self.port!r})", field="quantity")
        object.__setattr__(self, "target_Nm", _finite("target_Nm", self.target_Nm))
        object.__setattr__(self, "speed_rpm", _finite("speed_rpm", self.speed_rpm))
        if self.operator not in ("achieve", "band"):
            raise InputValidationError("operator must be 'achieve' or 'band'", field="operator")
        band = _finite("band_Nm", self.band_Nm)
        if band < 0 or (self.operator == "band" and band == 0):
            raise InputValidationError("band must be > 0 for operator 'band' and >= 0 otherwise", field="band_Nm")
        object.__setattr__(self, "band_Nm", band)
        v = self.Vdc_V
        if isinstance(v, (list, tuple)):
            lo, hi = (_finite("Vdc_V", x) for x in v)
            if lo <= 0 or hi <= 0:
                raise InputValidationError("inverter DC terminal voltage must be strictly positive", field="Vdc_V")
            if lo > hi:
                raise InputValidationError("Vdc range must be [low, high]", field="Vdc_V")
            object.__setattr__(self, "Vdc_V", (lo, hi))
            if self.Vdc_quantifier not in ("for_all",):
                raise InputValidationError("a Vdc range needs Vdc_quantifier='for_all'", field="Vdc_quantifier")
        else:
            x = _finite("Vdc_V", v)
            if x <= 0:
                raise InputValidationError("inverter DC terminal voltage must be strictly positive", field="Vdc_V")
            object.__setattr__(self, "Vdc_V", x)
            if self.Vdc_quantifier != "at_point":
                raise InputValidationError("a single Vdc value needs Vdc_quantifier='at_point'", field="Vdc_quantifier")
        if self.duration_s is not None:
            d = float(self.duration_s)
            if math.isnan(d) or d <= 0:
                raise InputValidationError("duration must be > 0 seconds or math.inf for continuous", field="duration_s")
            object.__setattr__(self, "duration_s", d)
        for name in ("coolant_temp_C", "winding_temp_C", "magnet_temp_C", "switching_frequency_Hz"):
            val = getattr(self, name)
            if val is not None:
                object.__setattr__(self, name, _finite(name, val))

    @property
    def is_range(self) -> bool:
        return isinstance(self.Vdc_V, tuple)

    @property
    def direction(self) -> int:
        return 1 if self.target_Nm >= 0 else -1

    def duration_text(self) -> str:
        if self.duration_s is None:
            return "not stated: interpreted as a static item; the duration aspect is undetermined (not 'continuous')"
        if math.isinf(self.duration_s):
            return "continuous"
        return f"{self.duration_s:g} s"

    def describe(self) -> dict:
        return {
            "req_id": self.req_id,
            "revision": self.revision,
            "original_text": self.text,
            "quantity": "shaft torque (not electromagnetic torque)",
            "port": "motor shaft (positive torque with positive speed = motoring)",
            "operator": self.operator,
            "target_Nm": self.target_Nm,
            "band_Nm": self.band_Nm if self.operator == "band" else None,
            "conditions": {
                "speed_rpm_mechanical": self.speed_rpm,
                "Vdc_V_inverter_dc_terminal": list(self.Vdc_V) if self.is_range else self.Vdc_V,
                "Vdc_quantifier": self.Vdc_quantifier,
                "coolant_temp_C": self.coolant_temp_C,
                "winding_temp_C": self.winding_temp_C,
                "magnet_temp_C": self.magnet_temp_C,
                "switching_frequency_Hz": self.switching_frequency_Hz,
            },
            "duration": self.duration_text(),
            "initial_state": self.initial_state,
            "exclusions": list(self.exclusions),
            "source": self.source,
        }
