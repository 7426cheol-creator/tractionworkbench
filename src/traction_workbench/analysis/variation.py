"""Named, traceable parameter changes of a (drive, scenario) pair.

Every parameter is labelled with what kind of change it is:

* ``boundary``     - scenario/source boundary (Vdc, DC limits);
* ``hardware``     - a realisable component change (current rating);
* ``design``       - a design/control choice (voltage reserve, declared id domain);
* ``diagnostic``   - constraint relaxation for cause diagnosis only (voltage
                     budget scale) or a motor parameter that is not an
                     independent design knob (psi_PM, Ld, Lq);
* ``data``         - data uncertainty (Rs, loss scales).

Changing Vdc updates the voltage budget and the DC-current power conversion
together because both are computed from the scenario inside the kernel.
"""

from __future__ import annotations

from dataclasses import replace

from ..errors import InputValidationError
from ..models.components import DriveModel
from ..models.flux import ConstantFluxModel
from ..scenario import Scenario

PARAMETERS = {
    "Vdc_V": ("boundary", "V", "inverter DC terminal voltage (voltage budget and DC-current conversion move together)"),
    "I_peak_max_A": ("hardware", "A", "inverter fundamental current rating (phase peak)"),
    "voltage_reserve_fraction": ("design", "-", "command-voltage reserve r_v"),
    "voltage_budget_scale": ("diagnostic", "-", "command-voltage budget scale (constraint relaxation, not hardware)"),
    "discharge_power_max_W": ("boundary", "W", "DC discharge power limit"),
    "charge_power_max_W": ("boundary", "W", "DC charge power limit"),
    "discharge_current_max_A": ("boundary", "A", "DC discharge average-current limit"),
    "charge_current_max_A": ("boundary", "A", "DC charge average-current limit"),
    "id_min_A": ("design", "A", "declared lower id bound (allowed domain, not a demagnetisation certification)"),
    "psi_pm_Wb": ("diagnostic", "Wb", "PM flux linkage (not an independent design knob)"),
    "Ld_H": ("diagnostic", "H", "d-axis inductance (not an independent design knob)"),
    "Lq_H": ("diagnostic", "H", "q-axis inductance (not an independent design knob)"),
    "Rs_ohm": ("data", "ohm", "per-phase resistance"),
    "inverter_loss_scale": ("data", "-", "scale of the inverter loss surrogate (offset and coefficient)"),
    "rotational_loss_scale": ("data", "-", "scale of the rotational loss torque"),
}


def describe_parameter(name: str) -> dict:
    kind, unit, text = PARAMETERS[name]
    return {"parameter": name, "change_kind": kind, "unit": unit, "meaning": text}


def get_value(drive: DriveModel, scenario: Scenario, name: str) -> float:
    lim = scenario.source_limits
    m = drive.motor
    if name == "Vdc_V":
        return scenario.Vdc_V
    if name == "I_peak_max_A":
        return drive.inverter.current_limit_A_peak
    if name == "voltage_reserve_fraction":
        return drive.inverter.voltage.reserve_fraction
    if name == "voltage_budget_scale":
        return drive.inverter.voltage.diagnostic_budget_scale
    if name in ("discharge_power_max_W", "charge_power_max_W"):
        return getattr(lim, name)
    if name == "discharge_current_max_A":
        return lim.discharge_current_max_A
    if name == "charge_current_max_A":
        return lim.charge_current_max_A
    if name == "id_min_A":
        return drive.domain.id_A[0]
    if name in ("psi_pm_Wb", "Ld_H", "Lq_H"):
        if not isinstance(m.flux, ConstantFluxModel):
            raise InputValidationError(f"{name} applies to the constant-parameter model only", field=name)
        return getattr(m.flux, name)
    if name == "Rs_ohm":
        return m.Rs_ohm
    if name in ("inverter_loss_scale", "rotational_loss_scale"):
        return 1.0
    raise InputValidationError(f"unknown parameter {name!r}", field="parameter")


def apply(drive: DriveModel, scenario: Scenario, name: str, value: float) -> tuple[DriveModel, Scenario]:
    if name not in PARAMETERS:
        raise InputValidationError(f"unknown parameter {name!r}; known: {', '.join(PARAMETERS)}", field="parameter")
    v = float(value)
    m = drive.motor
    inv = drive.inverter
    lim = scenario.source_limits
    if name == "Vdc_V":
        return drive, scenario.with_(Vdc_V=v)
    if name == "I_peak_max_A":
        return replace(drive, inverter=replace(inv, current_limit_A_peak=v)), scenario
    if name == "voltage_reserve_fraction":
        return replace(drive, inverter=replace(inv, voltage=replace(inv.voltage, reserve_fraction=v))), scenario
    if name == "voltage_budget_scale":
        return replace(drive, inverter=replace(inv, voltage=replace(inv.voltage, diagnostic_budget_scale=v))), scenario
    if name in ("discharge_power_max_W", "charge_power_max_W", "discharge_current_max_A", "charge_current_max_A"):
        return drive, scenario.with_(source_limits=replace(lim, **{name: v}))
    if name == "id_min_A":
        return replace(drive, domain=replace(drive.domain, id_A=(v, drive.domain.id_A[1]))), scenario
    if name in ("psi_pm_Wb", "Ld_H", "Lq_H"):
        if not isinstance(m.flux, ConstantFluxModel):
            raise InputValidationError(f"{name} applies to the constant-parameter model only", field=name)
        return replace(drive, motor=replace(m, flux=replace(m.flux, **{name: v}))), scenario
    if name == "Rs_ohm":
        return replace(drive, motor=replace(m, Rs_ohm=v)), scenario
    if name == "inverter_loss_scale":
        if inv.loss is None:
            raise InputValidationError("no inverter loss model to scale", field=name)
        loss = replace(inv.loss, offset_W=inv.loss.offset_W * v, ipk2_coeff_W_per_A2=inv.loss.ipk2_coeff_W_per_A2 * v)
        return replace(drive, inverter=replace(inv, loss=loss)), scenario
    if name == "rotational_loss_scale":
        if m.rotational_loss is None:
            raise InputValidationError("no rotational loss model to scale", field=name)
        rl = replace(m.rotational_loss, viscous_Nm_per_rad_s=m.rotational_loss.viscous_Nm_per_rad_s * v,
                     quadratic_Nm_per_rad2_s2=m.rotational_loss.quadratic_Nm_per_rad2_s2 * v)
        return replace(drive, motor=replace(m, rotational_loss=rl)), scenario
    raise AssertionError(name)
