"""Drive components: motor, loss closures, inverter voltage model, domain.

All quantities are SI.  dq currents/voltages are fundamental phase-peak
values in the amplitude-invariant Park frame with d aligned to the PM flux.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..errors import InputValidationError
from .flux import ConstantFluxModel, CurrentBox, FluxMapModel
from ..validation import finite as _finite, interval as _interval
from .provenance import DataOrigin, Fidelity, Provenance

SQRT3 = math.sqrt(3.0)


@dataclass(frozen=True)
class RotationalLossModel:
    """Loss-equivalent resisting torque tau_rot(omega_m) opposing rotation.

    tau_rot = b*omega_m + c*omega_m*|omega_m|, so tau_rot(0) = 0 is an explicit
    zero-speed limit and P_rot = omega_m*tau_rot >= 0.  When iron loss is
    included this is a loss-equivalent torque approximation, not a dq
    iron-loss-current model.  Static friction / stiction and zero-speed
    electrical losses are outside this closure.
    """

    viscous_Nm_per_rad_s: float = 0.0
    quadratic_Nm_per_rad2_s2: float = 0.0
    includes_iron_loss: bool = False
    basis: str = "declared"
    description: str = ""

    def __post_init__(self):
        b = _finite("viscous_Nm_per_rad_s", self.viscous_Nm_per_rad_s)
        c = _finite("quadratic_Nm_per_rad2_s2", self.quadratic_Nm_per_rad2_s2)
        if b < 0 or c < 0:
            raise InputValidationError("rotational loss coefficients must be >= 0 (passive loss)",
                                       field="rotational_loss")
        object.__setattr__(self, "viscous_Nm_per_rad_s", b)
        object.__setattr__(self, "quadratic_Nm_per_rad2_s2", c)

    def torque(self, omega_m: float) -> float:
        return self.viscous_Nm_per_rad_s * omega_m + self.quadratic_Nm_per_rad2_s2 * omega_m * abs(omega_m)

    def power(self, omega_m: float) -> float:
        return omega_m * self.torque(omega_m)

    def describe(self) -> dict:
        return {
            "form": "tau_rot = b*omega_m + c*omega_m*|omega_m| (opposes rotation, zero at standstill)",
            "b_Nm_per_rad_s": self.viscous_Nm_per_rad_s,
            "c_Nm_per_rad2_s2": self.quadratic_Nm_per_rad2_s2,
            "includes_iron_loss": self.includes_iron_loss,
            "approximation": "loss-equivalent resisting torque; not a dq iron-loss-current model"
            if self.includes_iron_loss else "mechanical/rotational loss torque",
            "basis": self.basis,
            "description": self.description,
        }


@dataclass(frozen=True)
class InverterLossModel:
    """Total inverter loss P_inv = offset + coeff*Ipk^2 (W), P_inv >= 0.

    ``kind`` states what the numbers are: a validated-condition surrogate, an
    explicit zero for an analytic fixture, or an explicit optimistic bound.
    Motoring/regeneration symmetry must be declared, never assumed.
    """

    offset_W: float
    ipk2_coeff_W_per_A2: float
    symmetric_motoring_regen: bool
    kind: str = "quadratic_current_surrogate"
    valid_Vdc_V: tuple[float, float] | None = None
    description: str = ""

    KINDS = ("quadratic_current_surrogate", "zero_analytic_fixture", "zero_optimistic_bound")

    def __post_init__(self):
        a0 = _finite("inverter_loss.offset_W", self.offset_W)
        a2 = _finite("inverter_loss.ipk2_coeff_W_per_A2", self.ipk2_coeff_W_per_A2)
        if a0 < 0 or a2 < 0:
            raise InputValidationError("inverter loss coefficients must be >= 0 (passive loss)", field="inverter_loss")
        if self.kind not in self.KINDS:
            raise InputValidationError(f"unknown loss kind {self.kind!r}", field="inverter_loss.kind")
        if self.kind.startswith("zero") and (a0 != 0 or a2 != 0):
            raise InputValidationError("a zero-loss kind must have zero coefficients", field="inverter_loss")
        if self.symmetric_motoring_regen is not True:
            raise InputValidationError(
                "only an explicitly symmetric motoring/regeneration loss surrogate is supported in this MVP",
                field="inverter_loss.symmetric_motoring_regen")
        if self.valid_Vdc_V is not None:
            object.__setattr__(self, "valid_Vdc_V", _interval("inverter_loss.valid_Vdc_V", self.valid_Vdc_V))
        object.__setattr__(self, "offset_W", a0)
        object.__setattr__(self, "ipk2_coeff_W_per_A2", a2)

    def loss(self, i2):
        return self.offset_W + self.ipk2_coeff_W_per_A2 * i2

    def describe(self) -> dict:
        return {
            "form": "P_inv = offset + coeff*Ipk^2",
            "offset_W": self.offset_W,
            "coeff_W_per_A2": self.ipk2_coeff_W_per_A2,
            "kind": self.kind,
            "symmetric_motoring_regen": self.symmetric_motoring_regen,
            "valid_Vdc_V": list(self.valid_Vdc_V) if self.valid_Vdc_V else None,
            "note": "synthetic energy-loss surrogate; not a device conduction/switching model"
            if self.kind == "quadratic_current_surrogate" else self.kind,
            "description": self.description,
        }


@dataclass(frozen=True)
class VoltageModel:
    """Linear SVPWM command-voltage budget.

    |v_motor + dv_inv| <= (1 - r_v) * Vdc / sqrt(3).  r_v is a design/control
    reserve, never a numerical tolerance.  ``voltage_error='ideal'`` means
    dv_inv = 0 (ideal command-to-terminal mapping / optimistic screening);
    ``'resistive'`` means dv_inv = R_drop * i_dq.  The same non-ideality must
    not be subtracted both in the reserve and in the drop model.
    """

    reserve_fraction: float
    modulation: str = "linear_svpwm"
    voltage_error: str = "ideal"
    resistive_drop_ohm: float = 0.0
    mapping_note: str = ""
    diagnostic_budget_scale: float = 1.0

    def __post_init__(self):
        sc = _finite("diagnostic_budget_scale", self.diagnostic_budget_scale)
        if sc <= 0:
            raise InputValidationError("budget scale must be > 0", field="diagnostic_budget_scale")
        object.__setattr__(self, "diagnostic_budget_scale", sc)
        rv = _finite("voltage_reserve_fraction", self.reserve_fraction)
        if not (0.0 <= rv < 1.0):
            raise InputValidationError("voltage reserve fraction must satisfy 0 <= r_v < 1",
                                       field="voltage_reserve_fraction")
        if self.modulation != "linear_svpwm":
            raise InputValidationError(
                f"modulation {self.modulation!r} is outside the MVP (linear SVPWM only; no overmodulation/six-step)",
                field="modulation")
        if self.voltage_error not in ("ideal", "resistive"):
            raise InputValidationError("voltage_error must be 'ideal' or 'resistive'", field="voltage_error")
        r = _finite("resistive_drop_ohm", self.resistive_drop_ohm)
        if r < 0:
            raise InputValidationError("resistive drop must be >= 0", field="resistive_drop_ohm")
        if self.voltage_error == "ideal" and r != 0:
            raise InputValidationError("ideal voltage mapping cannot have a drop resistance", field="resistive_drop_ohm")
        object.__setattr__(self, "reserve_fraction", rv)
        object.__setattr__(self, "resistive_drop_ohm", r)

    def hardware_ceiling_V(self, vdc: float) -> float:
        return vdc / SQRT3

    def command_budget_V(self, vdc: float) -> float:
        return self.diagnostic_budget_scale * (1.0 - self.reserve_fraction) * vdc / SQRT3

    def describe(self) -> dict:
        return {
            "modulation": "linear SVPWM, ideal fundamental phase-peak ceiling Vdc/sqrt(3)",
            "reserve_fraction_r_v": self.reserve_fraction,
            "diagnostic_budget_scale": (None if self.diagnostic_budget_scale == 1.0 else
                                        f"{self.diagnostic_budget_scale} (diagnostic constraint relaxation, "
                                        f"not a realisable hardware change)"),
            "voltage_error_model": "dv_inv = 0 (ideal mapping; optimistic screening unless an agreed envelope)"
            if self.voltage_error == "ideal" else f"dv_inv = {self.resistive_drop_ohm} ohm * i_dq",
            "mapping_note": self.mapping_note,
        }


@dataclass(frozen=True)
class InverterModel:
    inverter_id: str
    current_limit_A_peak: float
    voltage: VoltageModel
    loss: InverterLossModel | None
    switching_frequency_context_Hz: float | None = None
    current_limit_basis: str = "fundamental phase peak (dq norm)"
    module_loss: object | None = None        # extensions.module_loss.ModuleLossModel (datasheet-based)
    module_Tj_C: float | None = None         # junction temperature at which the datasheet curves are evaluated

    def __post_init__(self):
        if self.module_loss is not None:
            if self.loss is not None:
                raise InputValidationError("declare one inverter loss model: the quadratic surrogate OR the "
                                           "datasheet module model (never both summed)", field="inverter.loss")
            if self.module_Tj_C is None:
                raise InputValidationError("a datasheet module loss model needs the evaluation junction temperature "
                                           "(module_Tj_C)", field="inverter.module_Tj_C")
            object.__setattr__(self, "module_Tj_C", _finite("module_Tj_C", self.module_Tj_C))
        imax = _finite("current_limit_A_peak", self.current_limit_A_peak)
        if imax <= 0:
            raise InputValidationError("current limit must be > 0", field="current_limit_A_peak")
        object.__setattr__(self, "current_limit_A_peak", imax)
        if self.switching_frequency_context_Hz is not None:
            f = _finite("switching_frequency_context_Hz", self.switching_frequency_context_Hz)
            if f <= 0:
                raise InputValidationError("switching frequency must be > 0", field="switching_frequency_context_Hz")
            object.__setattr__(self, "switching_frequency_context_Hz", f)

    def describe(self) -> dict:
        return {
            "inverter_id": self.inverter_id,
            "topology": "single three-phase two-level VSI",
            "current_limit_A_peak": self.current_limit_A_peak,
            "current_limit_basis": self.current_limit_basis,
            "current_limit_note": "fundamental amplitude only; PWM ripple, pulse peak, OC overshoot and SOA are not covered",
            "voltage": self.voltage.describe(),
            "loss": None if self.loss is None else self.loss.describe(),
            "module_loss": None if self.module_loss is None else {
                "technology": self.module_loss.device.technology, "fsw_Hz": self.module_loss.fsw_Hz,
                "modulation": self.module_loss.modulation, "deadtime_s": self.module_loss.deadtime_s,
                "parallel": self.module_loss.parallel, "energy_basis": self.module_loss.device.energy_basis,
                "value_kind": self.module_loss.device.value_kind, "v_test_V": self.module_loss.device.v_test_V,
                "evaluation_Tj_C": self.module_Tj_C, "source": self.module_loss.device.source,
                "note": "datasheet-based average model at the evaluation Tj; the quadratic I^2 certificates do not "
                        "apply - DC claims rest on direct witnesses"},
            "switching_frequency_context_Hz": self.switching_frequency_context_Hz,
        }


@dataclass(frozen=True)
class OperatingDomain:
    """Explicit control/speed domain.

    ``kind='allowed_operating_limit'``: a declared restriction; a requirement
    outside it is INFEASIBLE *within the declared domain*.
    ``kind='model_validity'``: outside it the model says nothing (UNKNOWN).
    """

    id_A: tuple[float, float]
    iq_A: tuple[float, float]
    speed_rpm: tuple[float, float]
    kind: str = "allowed_operating_limit"
    interpretation: str = ""

    def __post_init__(self):
        object.__setattr__(self, "id_A", _interval("domain.id_A", self.id_A))
        object.__setattr__(self, "iq_A", _interval("domain.iq_A", self.iq_A))
        object.__setattr__(self, "speed_rpm", _interval("domain.speed_rpm", self.speed_rpm))
        if self.kind not in ("allowed_operating_limit", "model_validity"):
            raise InputValidationError("domain kind must be 'allowed_operating_limit' or 'model_validity'",
                                       field="domain.kind")

    @property
    def current_box(self) -> CurrentBox:
        return CurrentBox(self.id_A, self.iq_A)

    def describe(self) -> dict:
        return {
            "id_A_peak": list(self.id_A),
            "iq_A_peak": list(self.iq_A),
            "speed_rpm_mechanical": list(self.speed_rpm),
            "kind": self.kind,
            "interpretation": self.interpretation,
        }


@dataclass(frozen=True)
class TemperatureDependence:
    """Linear coefficient with the range where it is supported by data."""

    coeff_per_K: float
    valid_C: tuple[float, float]
    basis: str

    def __post_init__(self):
        object.__setattr__(self, "coeff_per_K", _finite("coeff_per_K", self.coeff_per_K))
        object.__setattr__(self, "valid_C", _interval("valid_C", self.valid_C))
        if not self.basis.strip():
            raise InputValidationError("temperature coefficient needs a stated basis", field="basis")


@dataclass(frozen=True)
class MotorModel:
    motor_id: str
    pole_pairs: int
    flux: ConstantFluxModel | FluxMapModel
    Rs_ohm: float
    rotational_loss: RotationalLossModel | None
    connection: str = "wye"
    connection_note: str = ""
    reference_winding_temp_C: float | None = None
    reference_magnet_temp_C: float | None = None
    rs_temperature: TemperatureDependence | None = None
    psi_temperature: TemperatureDependence | None = None
    fidelity: Fidelity = Fidelity.D1

    def __post_init__(self):
        p = self.pole_pairs
        if isinstance(p, bool) or not isinstance(p, int):
            if isinstance(p, float) and p.is_integer():
                p = int(p)
            else:
                raise InputValidationError(f"pole pairs must be a positive integer, got {self.pole_pairs!r}",
                                           field="pole_pairs")
        if p <= 0:
            raise InputValidationError("pole pairs must be a positive integer", field="pole_pairs")
        object.__setattr__(self, "pole_pairs", p)
        rs = _finite("Rs_ohm", self.Rs_ohm)
        if rs < 0:
            raise InputValidationError("per-phase resistance must be >= 0", field="Rs_ohm")
        object.__setattr__(self, "Rs_ohm", rs)
        if self.connection == "raw_delta" or self.connection == "delta":
            raise InputValidationError(
                "raw delta-winding data are not converted automatically; supply a documented wye-equivalent",
                field="connection")
        if self.connection not in ("wye", "wye_equivalent"):
            raise InputValidationError(f"unknown connection {self.connection!r}", field="connection")
        if self.connection == "wye_equivalent" and not self.connection_note.strip():
            raise InputValidationError("a wye-equivalent must document its normalisation", field="connection_note")
        if not isinstance(self.flux, (ConstantFluxModel, FluxMapModel)):
            raise InputValidationError("flux must be a ConstantFluxModel or FluxMapModel", field="flux")
        if isinstance(self.flux, FluxMapModel) and self.psi_temperature is not None:
            raise InputValidationError("flux-map temperature dependence must come from map planes", field="psi_temperature")
        for name in ("reference_winding_temp_C", "reference_magnet_temp_C"):
            v = getattr(self, name)
            if v is not None:
                object.__setattr__(self, name, _finite(name, v))
        if self.rs_temperature is not None and self.reference_winding_temp_C is None:
            raise InputValidationError("Rs temperature coefficient needs a reference winding temperature",
                                       field="rs_temperature")
        if self.psi_temperature is not None and self.reference_magnet_temp_C is None:
            raise InputValidationError("PM flux temperature coefficient needs a reference magnet temperature",
                                       field="psi_temperature")

    def describe(self) -> dict:
        return {
            "motor_id": self.motor_id,
            "pole_pairs": self.pole_pairs,
            "connection": self.connection,
            "connection_note": self.connection_note,
            "Rs_phase_ohm": self.Rs_ohm,
            "flux_model": self.flux.describe(),
            "rotational_loss": None if self.rotational_loss is None else self.rotational_loss.describe(),
            "reference_winding_temp_C": self.reference_winding_temp_C,
            "reference_magnet_temp_C": self.reference_magnet_temp_C,
            "rs_temperature": None if self.rs_temperature is None else vars(self.rs_temperature),
            "psi_temperature": None if self.psi_temperature is None else vars(self.psi_temperature),
            "fidelity": self.fidelity.value,
        }


@dataclass(frozen=True)
class DriveModel:
    drive_id: str
    revision: str
    motor: MotorModel
    inverter: InverterModel
    domain: OperatingDomain
    provenance: Provenance
    notes: tuple[str, ...] = ()

    @property
    def fidelity(self) -> Fidelity:
        return self.motor.fidelity

    def describe(self) -> dict:
        return {
            "drive_id": self.drive_id,
            "revision": self.revision,
            "fidelity": self.fidelity.value,
            "provenance": self.provenance.to_dict(),
            "motor": self.motor.describe(),
            "inverter": self.inverter.describe(),
            "operating_domain": self.domain.describe(),
            "notes": list(self.notes),
        }
