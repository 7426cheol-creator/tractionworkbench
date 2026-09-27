"""V0: input validation and unit/definition normalisation (UC00)."""

import math

import pytest

from traction_workbench.errors import InputValidationError
from traction_workbench.io import case_from_dict, drive_from_dict
from traction_workbench.models import (ConstantFluxModel, InverterLossModel, MotorModel, OperatingDomain,
                                       RotationalLossModel, VoltageModel)
from traction_workbench.units import Conversions, current_peak, resistance_per_phase, voltage_phase_peak


def base_drive():
    return {
        "drive_id": "UNIT_TEST", "revision": "1",
        "provenance": {"origin": "synthetic", "source": "test", "revision": "1", "validation_status": "none"},
        "motor": {"model": "constant_dq", "pole_pairs": 4, "connection": "wye",
                  "Rs": {"value": 15, "unit": "mohm", "reference": "per_phase"},
                  "psi_pm": {"value": 100, "unit": "mWb", "basis": "phase_peak"},
                  "Ld": {"value": 0.2, "unit": "mH"}, "Lq": {"value": 0.4, "unit": "mH"},
                  "rotational_loss": {"viscous": {"value": 0.002, "unit": "N*m/(rad/s)"}}},
        "inverter": {"current_limit": {"value": 600, "unit": "A", "basis": "fundamental_peak"},
                     "voltage_reserve_fraction": 0.05,
                     "loss": {"offset": {"value": 200, "unit": "W"}, "coeff": {"value": 0.008, "unit": "W/A^2"},
                              "current_basis": "fundamental_peak", "symmetric_motoring_regen": True}},
        "domain": {"id": {"value": [-500, 0], "unit": "A", "basis": "fundamental_peak"},
                   "iq": {"value": [-600, 600], "unit": "A", "basis": "fundamental_peak"},
                   "speed": {"value": [-16000, 16000], "unit": "rpm", "kind": "mechanical"}},
    }


def test_json_drive_equals_builtin():
    from traction_workbench import spec_fixtures as sf
    d = drive_from_dict(base_drive())
    b = sf.synthetic_drive()
    assert d.motor.Rs_ohm == pytest.approx(b.motor.Rs_ohm)
    assert d.motor.flux.psi_pm_Wb == pytest.approx(b.motor.flux.psi_pm_Wb)
    assert d.motor.flux.Ld_H == pytest.approx(b.motor.flux.Ld_H)


def test_current_basis_required():
    c = Conversions()
    with pytest.raises(InputValidationError, match="fundamental_peak"):
        current_peak({"value": 600, "unit": "A"}, "limit", c)
    with pytest.raises(InputValidationError, match="cannot be used"):
        current_peak({"value": 600, "unit": "A", "basis": "total_rms"}, "limit", c)
    assert current_peak({"value": 600, "unit": "A", "basis": "fundamental_rms"}, "limit", c) == pytest.approx(600 * math.sqrt(2))
    assert c.records and "sqrt 2" in c.records[-1]["rule"]


def test_voltage_and_resistance_definitions():
    c = Conversions()
    assert voltage_phase_peak({"value": 400, "unit": "V", "basis": "line_line_rms"}, "v", c) == pytest.approx(400 * math.sqrt(2 / 3))
    with pytest.raises(InputValidationError):
        voltage_phase_peak({"value": 400, "unit": "V"}, "v", c)
    assert resistance_per_phase({"value": 30, "unit": "mohm", "reference": "line_to_line"}, "wye", "Rs", c) == pytest.approx(0.015)
    with pytest.raises(InputValidationError, match="per_phase"):
        resistance_per_phase({"value": 30, "unit": "mohm"}, "wye", "Rs", c)
    with pytest.raises(InputValidationError, match="wye"):
        resistance_per_phase({"value": 30, "unit": "mohm", "reference": "line_to_line"}, "wye_equivalent", "Rs", c)


def test_raw_delta_rejected():
    d = base_drive()
    d["motor"]["connection"] = "delta"
    d["motor"]["Rs"]["reference"] = "per_phase"
    with pytest.raises(InputValidationError, match="delta"):
        drive_from_dict(d)


def test_ambiguous_ke_rejected_and_complete_ke_converted():
    d = base_drive()
    del d["motor"]["psi_pm"]
    d["motor"]["Ke"] = {"value": 50, "unit": "V", "basis": "line_line_rms"}
    with pytest.raises(InputValidationError, match="per_speed"):
        drive_from_dict(d)
    # 1000 rpm mechanical, p=4: V_LL,rms = sqrt(3/2)*omega_e*psi -> psi = 0.1 Wb gives 51.3 V
    we = 4 * 2 * math.pi * 1000 / 60
    d["motor"]["Ke"] = {"value": math.sqrt(1.5) * we * 0.1, "unit": "V", "basis": "line_line_rms",
                        "per_speed": {"value": 1000, "unit": "rpm", "kind": "mechanical"}}
    c = Conversions()
    dr = drive_from_dict(d, c)
    assert dr.motor.flux.psi_pm_Wb == pytest.approx(0.1, rel=1e-12)
    assert any("psi_PM" in r["rule"] for r in c.records)


def test_units_and_values_rejected():
    d = base_drive()
    d["motor"]["Ld"] = {"value": 0.2, "unit": "millihenry"}
    with pytest.raises(InputValidationError, match="unit"):
        drive_from_dict(d)
    d = base_drive()
    d["motor"]["pole_pairs"] = 4.5
    with pytest.raises(InputValidationError, match="pole_pairs"):
        drive_from_dict(d)


@pytest.mark.parametrize("kwargs, match", [
    (dict(psi_pm_Wb=0.1, Ld_H=0.0, Lq_H=0.0004), "Ld"),
    (dict(psi_pm_Wb=0.1, Ld_H=0.0002, Lq_H=-1e-4), "Lq"),
    (dict(psi_pm_Wb=0.0, Ld_H=0.0003, Lq_H=0.0003), "no torque"),
    (dict(psi_pm_Wb=float("nan"), Ld_H=0.0003, Lq_H=0.0003), "non-finite"),
])
def test_flux_model_validation(kwargs, match):
    with pytest.raises(InputValidationError, match=match):
        ConstantFluxModel(**kwargs)


def test_component_validation():
    with pytest.raises(InputValidationError):
        VoltageModel(reserve_fraction=1.0)
    with pytest.raises(InputValidationError, match="overmodulation"):
        VoltageModel(reserve_fraction=0.05, modulation="six_step")
    with pytest.raises(InputValidationError):
        RotationalLossModel(viscous_Nm_per_rad_s=-0.1)
    with pytest.raises(InputValidationError, match="symmetric"):
        InverterLossModel(200, 0.008, symmetric_motoring_regen=None)
    with pytest.raises(InputValidationError):
        MotorModel("m", 0, ConstantFluxModel(0.1, 2e-4, 4e-4), 0.015, None)
    with pytest.raises(InputValidationError):
        OperatingDomain((0, -500), (-600, 600), (-1, 1))


def test_requirement_torque_must_be_shaft():
    case = {"drive": {"builtin": "SYNTH_IPMSM_200KW_REF_V1"},
            "requirement": {"id": "R", "text": "t", "target": {"value": 150, "unit": "N*m", "torque": "electromagnetic"},
                            "conditions": {"speed": {"value": 12000, "unit": "rpm"}, "Vdc": {"value": 600, "unit": "V"}}}}
    with pytest.raises(InputValidationError, match="shaft"):
        case_from_dict(case)


def test_electrical_speed_needs_pole_pairs_and_converts():
    case = {"drive": {"builtin": "SYNTH_IPMSM_200KW_REF_V1"},
            "requirement": {"id": "R", "text": "t", "target": {"value": 150, "unit": "N*m"},
                            "conditions": {"speed": {"value": 800, "unit": "Hz", "kind": "electrical"},
                                           "Vdc": {"value": 600, "unit": "V"}}}}
    c = case_from_dict(case)
    assert c.requirement.speed_rpm == pytest.approx(800 * 60 / 4)
