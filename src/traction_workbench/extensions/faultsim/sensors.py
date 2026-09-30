"""Sensors: the only path from the plant truth to the controller, the monitors and the hardware comparators.

Every sensor has a nominal characteristic (a gain and an offset - a tolerance corner or zero -, a transport delay, a
quantisation step) and, from its fault time on, one fault mode:

    offset      reading + value
    gain        reading * (1 + value)
    stuck       a fixed value
    stuck_last  frozen at the reading it had when the fault occurred
    lost        the declared lost-signal reading (e.g. 0 A, or the rail a Hall sensor output falls to); an angle
                sensor freezes (the converter's tracking loop loses its input)
    delay       ``value`` seconds of extra transport delay

Conversions happen at the ADC trigger instants (the control sample grid) and see the truth ``delay`` earlier (the
transport delay of the signal chain: filter, tracking converter; the engine interpolates its state history); every
consumer (the control task, a monitor task of another period) reads the newest conversion - so a slower monitor task
sees the same data the ADC produced, not a private sample.  The analog output (what a hardware comparator sees) is
continuous, without sampling delay or quantisation.

A sensor exposes the diagnostics its hardware has and nothing else: an out-of-range flag when the reading leaves the
declared valid range (the ADC / converter check) and, for a position sensor, the converter's loss-of-signal flag
after its declared detection time.  No consumer reads the simulation's fault list.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from ...errors import InputValidationError

FAULT_MODES = ("offset", "gain", "stuck", "stuck_last", "lost", "delay")
KINDS = ("current", "position", "voltage", "temperature", "dc_current")
TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class SensorSpec:
    name: str
    kind: str
    gain_err: float = 0.0             # nominal relative gain error (a tolerance corner or 0)
    offset: float = 0.0               # nominal offset (A, rad, V, degC)
    delay_s: float = 0.0              # transport delay of the digital reading
    quant: float = 0.0                # quantisation step of the digital reading (0: none)
    lost_value: float = 0.0           # the reading of a lost signal
    valid_range: tuple = (-math.inf, math.inf)   # the converter's own out-of-range check
    los_detect_s: float | None = None             # position: loss-of-signal flag delay of the converter
    resources: tuple = ()             # supplies / converters it needs (common-cause coupling)
    basis: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise InputValidationError(f"sensor kind must be one of {KINDS}", field=f"sensors.{self.name}.kind")
        if not (self.delay_s >= 0.0 and math.isfinite(self.delay_s)):
            raise InputValidationError("delay must be finite and >= 0", field=f"sensors.{self.name}.delay_s")
        if not (self.quant >= 0.0):
            raise InputValidationError("quantisation step must be >= 0", field=f"sensors.{self.name}.quant")


class Sensor:
    def __init__(self, spec: SensorSpec):
        self.spec = spec
        self.mode: str | None = None
        self.value = 0.0
        self.t_fault: float | None = None
        self.frozen: float | None = None
        self.lost_since: float | None = None
        self.buf: deque = deque(maxlen=4096)     # (t_available, value)

    # -- fault injection ------------------------------------------------------------------------------------
    def inject(self, t: float, mode: str, value: float, truth: float):
        if mode not in FAULT_MODES:
            raise InputValidationError(f"sensor fault mode must be one of {FAULT_MODES}", field="fault.mode")
        if mode == "delay" and not (value >= 0.0 and math.isfinite(value)):
            raise InputValidationError("an extra delay must be finite and >= 0 s", field="fault.value")
        self.frozen = self.analog(truth) if mode == "stuck_last" else None
        self.mode, self.value, self.t_fault = mode, float(value), t
        if mode == "lost":
            self.lost_since = t

    def lose(self, t: float, truth: float = 0.0):
        """The sensor's supply / converter is gone (a resource fault): it reads as lost from now on."""
        if self.mode != "lost":
            self.mode, self.t_fault, self.lost_since = "lost", t, t

    def _nominal(self, truth: float) -> float:
        s = self.spec
        return truth * (1.0 + s.gain_err) + s.offset

    # -- outputs --------------------------------------------------------------------------------------------
    def analog(self, truth: float) -> float:
        """Continuous output (what a hardware comparator sees)."""
        y = self._nominal(truth)
        md = self.mode
        if md is None or md == "delay":
            return y
        if md == "offset":
            return y + self.value
        if md == "gain":
            return y * (1.0 + self.value)
        if md == "stuck":
            return self.value
        if md == "stuck_last":
            return self.frozen
        return self.spec.lost_value                              # lost

    def delay(self) -> float:
        return self.spec.delay_s + (self.value if self.mode == "delay" else 0.0)

    def convert(self, t: float, truth_delayed: float):
        """An ADC conversion at t of the truth ``delay()`` earlier (quantised)."""
        y = self.analog(truth_delayed)
        q = self.spec.quant
        if q > 0:
            y = round(y / q) * q
        self.buf.append((t, y))

    def read(self, t: float) -> float | None:
        """The newest conversion at or before t (None before the first one)."""
        for ta, y in reversed(self.buf):
            if ta <= t + 1e-15:
                return y
        return None

    def flags(self, t: float, reading: float | None) -> dict:
        """The sensor hardware's own diagnostics at t (never the simulation's fault list)."""
        lo, hi = self.spec.valid_range
        out = {"out_of_range": reading is not None and not (lo <= reading <= hi)}
        if self.spec.kind == "position":
            los = self.spec.los_detect_s
            out["loss_of_signal"] = (self.mode == "lost" and los is not None and self.lost_since is not None
                                     and t - self.lost_since >= los - 1e-12)
        return out


class AngleSensor(Sensor):
    """Rotor position (electrical angle): offset / stuck / lost act on the angle; the reading wraps to [0, 2 pi)."""

    def _nominal(self, truth: float) -> float:
        return truth + self.spec.offset

    def analog(self, truth: float) -> float:
        md = self.mode
        y = truth + self.spec.offset
        if md == "offset":
            y += self.value
        elif md == "gain":                                      # an angle-scale error (e.g. wrong pole-pair count)
            y *= (1.0 + self.value)
        elif md == "stuck":
            y = self.value
        elif md in ("stuck_last", "lost"):
            y = self.frozen if self.frozen is not None else y
        return y % TWO_PI

    def inject(self, t: float, mode: str, value: float, truth: float):
        super().inject(t, mode, value, truth)
        if mode in ("stuck_last", "lost"):
            self.frozen = (truth + self.spec.offset) % TWO_PI

    def lose(self, t: float, truth: float = 0.0):
        if self.mode != "lost":
            super().lose(t, truth)
            self.frozen = (truth + self.spec.offset) % TWO_PI

    def convert(self, t: float, truth_delayed: float):
        y = self.analog(truth_delayed)
        q = self.spec.quant
        if q > 0:
            y = (round(y / q) * q) % TWO_PI
        self.buf.append((t, y))


def unwrap_delta(a_new: float, a_old: float) -> float:
    d = a_new - a_old
    return (d + math.pi) % TWO_PI - math.pi


def make_sensor(spec: SensorSpec) -> Sensor:
    return AngleSensor(spec) if spec.kind == "position" else Sensor(spec)
