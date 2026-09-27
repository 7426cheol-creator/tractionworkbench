"""Adapters from the v1.0 reference package to production objects.

The reference JSON files are immutable expected fixtures.  Their layout is not
the production data format; this module only maps their physical meaning onto
the production model objects and records provenance (file + SHA-256).
Expected values are read for comparison only and are never written back.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from .errors import InputValidationError
from .models import (
    ConstantFluxModel,
    DataOrigin,
    DriveModel,
    Fidelity,
    FluxMapModel,
    FluxMapPlane,
    InverterLossModel,
    InverterModel,
    MotorModel,
    OperatingDomain,
    Provenance,
    RotationalLossModel,
    VoltageModel,
)
from .scenario import DcSourceLimits, Scenario

SPEC_DIRNAME = "traction_workbench_spec_v1"


def spec_dir() -> Path:
    env = os.environ.get("TWB_SPEC_DIR")
    if env:
        p = Path(env)
    else:
        p = Path(__file__).resolve().parents[2] / "reference" / SPEC_DIRNAME
    if not (p / "manifest.json").is_file():
        raise FileNotFoundError(
            f"reference package not found at {p}; set TWB_SPEC_DIR to the traction_workbench_spec_v1 directory")
    return p


@lru_cache(maxsize=None)
def _manifest(path: str) -> dict:
    return json.loads((Path(path) / "manifest.json").read_text(encoding="utf-8"))


def load(name: str) -> dict:
    return json.loads((spec_dir() / name).read_text(encoding="utf-8"))


def file_sha256(name: str) -> str:
    return hashlib.sha256((spec_dir() / name).read_bytes()).hexdigest()


def verify_manifest() -> list[dict]:
    d = spec_dir()
    out = []
    for f in _manifest(str(d))["files"]:
        data = (d / f["name"]).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        out.append({"name": f["name"], "expected_sha256": f["sha256"], "sha256": digest,
                    "ok": digest == f["sha256"] and len(data) == f["size_bytes"]})
    return out


def _provenance(name: str, meta: dict) -> Provenance:
    return Provenance(
        origin=DataOrigin.SYNTHETIC,
        source=f"{SPEC_DIRNAME}/{name}",
        revision=str(meta.get("version", "1.0")),
        validation_status=meta.get("verification_status",
                                   "no hardware or external-simulator validation performed"),
        sha256=file_sha256(name),
        notes=(meta.get("origin", ""), meta.get("scope", ""), meta.get("convention", "")),
    )


def synthetic_limits() -> DcSourceLimits:
    lim = load("synthetic_drive.json")["source_limits"]
    return DcSourceLimits(
        discharge_power_max_W=lim["discharge_power_max_W"],
        charge_power_max_W=lim["charge_power_max_W"],
        discharge_current_max_A=lim["discharge_average_current_max_A"],
        charge_current_max_A=lim["charge_average_current_max_A"],
        source=f"{SPEC_DIRNAME}/synthetic_drive.json source_limits (synthetic)",
    )


def synthetic_drive(overrides: dict | None = None) -> DriveModel:
    """SYNTH_IPMSM_200KW_REF_V1 as a production DriveModel.

    ``overrides`` uses the fixture's parameter names (e.g. the F05 SPMSM case).
    An override that sets rotational/inverter loss to zero is recorded as an
    explicit analytic-fixture zero, never as a silent default.
    """
    fx = load("synthetic_drive.json")
    par = dict(fx["parameters"])
    known = set(par)
    for key in (overrides or {}):
        if key not in known:
            raise InputValidationError(f"unknown fixture parameter {key!r}", field="overrides")
    par.update(overrides or {})
    dom = fx["declared_operating_domain"]
    prov = _provenance("synthetic_drive.json", fx["metadata"])
    b = par["drag_coefficient_Nm_per_rad_s"]
    rot = RotationalLossModel(
        viscous_Nm_per_rad_s=b,
        basis="analytic_fixture_zero" if b == 0 else "synthetic fixture tau_rot = b*omega_m",
        description="synthetic rotational loss torque (fixture)",
    )
    a0 = par["inverter_loss_offset_W"]
    a2 = par["inverter_loss_Ipk2_coefficient_ohm"]
    loss = InverterLossModel(
        offset_W=a0,
        ipk2_coeff_W_per_A2=a2,
        symmetric_motoring_regen=True,
        kind="zero_analytic_fixture" if (a0 == 0 and a2 == 0) else "quadratic_current_surrogate",
        description=fx.get("loss_voltage_approximation", ""),
    )
    motor = MotorModel(
        motor_id=fx["model_id"] + ("" if not overrides else "+overrides"),
        pole_pairs=int(par["pole_pairs"]),
        flux=ConstantFluxModel(par["psi_pm_Wb"], par["Ld_H"], par["Lq_H"]),
        Rs_ohm=par["Rs_phase_ohm"],
        rotational_loss=rot,
        connection="wye",
        fidelity=Fidelity.D1,
    )
    inverter = InverterModel(
        inverter_id="SYNTH_2L_VSI_REF_V1",
        current_limit_A_peak=dom["I_peak_max_A"],
        voltage=VoltageModel(
            reserve_fraction=par["voltage_reserve_fraction"],
            voltage_error="ideal",
            mapping_note="ideal command-to-terminal mapping (dv_inv = 0) as defined by the synthetic fixture",
        ),
        loss=loss,
        switching_frequency_context_Hz=par.get("switching_frequency_context_Hz"),
    )
    domain = OperatingDomain(
        id_A=tuple(dom["id_A_peak"]),
        iq_A=tuple(dom["iq_A_peak"]),
        speed_rpm=tuple(dom["speed_rpm"]),
        kind="allowed_operating_limit",
        interpretation=dom["interpretation"],
    )
    notes = tuple(f"not defined: {x}" for x in fx.get("not_defined", ()))
    if overrides:
        notes += (f"parameter overrides applied: {json.dumps(overrides, sort_keys=True)}",)
    return DriveModel(
        drive_id=fx["model_id"] + ("" if not overrides else "+overrides"),
        revision=str(fx["metadata"]["version"]),
        motor=motor,
        inverter=inverter,
        domain=domain,
        provenance=prov,
        notes=notes,
    )


def synthetic_scenario(speed_rpm: float, Vdc_V: float, scenario_id: str | None = None, **kw) -> Scenario:
    return Scenario(
        scenario_id=scenario_id or f"n{speed_rpm:g}_Vdc{Vdc_V:g}",
        speed_rpm=speed_rpm,
        Vdc_V=Vdc_V,
        source_limits=kw.pop("source_limits", None) or synthetic_limits(),
        **kw,
    )


def manufactured_flux_plane() -> FluxMapPlane:
    fx = load("manufactured_flux_map.json")
    if fx.get("array_order") != "row=id index, column=iq index":
        raise InputValidationError(f"unexpected array order {fx.get('array_order')!r}", field="array_order")
    return FluxMapPlane(
        id_axis_A=np.array(fx["axes"]["id_A_peak"], dtype=float),
        iq_axis_A=np.array(fx["axes"]["iq_A_peak"], dtype=float),
        psi_d_Wb=np.array(fx["psi_d_Wb"], dtype=float),
        psi_q_Wb=np.array(fx["psi_q_Wb"], dtype=float),
        valid=np.array(fx["validity_mask"], dtype=bool),
        magnet_temp_C=None,
        label=fx["fixture_id"],
    )


def manufactured_flux_model() -> FluxMapModel:
    return FluxMapModel(planes=(manufactured_flux_plane(),), conservative=True)


def manufactured_map_drive(flux: FluxMapModel | None = None, *, i_max: float = 200.0,
                           id_range=(-200.0, 0.0), iq_range=(-200.0, 200.0)) -> DriveModel:
    """A *test configuration* around the manufactured flux map.

    The map fixture defines only the magnetic model and p.  Resistance, losses,
    voltage reserve and the allowed domain here are borrowed from the synthetic
    drive for exercising the nonlinear solvers; this is not a second reference
    motor and must not be mixed with the linear fixture results.
    """
    fx = load("manufactured_flux_map.json")
    base = synthetic_drive()
    flux = flux or manufactured_flux_model()
    prov = _provenance("manufactured_flux_map.json", fx["metadata"])
    motor = MotorModel(
        motor_id=fx["fixture_id"] + "_TEST_DRIVE",
        pole_pairs=int(fx["pole_pairs"]),
        flux=flux,
        Rs_ohm=base.motor.Rs_ohm,
        rotational_loss=base.motor.rotational_loss,
        connection="wye",
        fidelity=Fidelity.D2,
    )
    inverter = InverterModel(
        inverter_id="SYNTH_2L_VSI_REF_V1",
        current_limit_A_peak=i_max,
        voltage=base.inverter.voltage,
        loss=base.inverter.loss,
        switching_frequency_context_Hz=base.inverter.switching_frequency_context_Hz,
    )
    domain = OperatingDomain(id_A=tuple(id_range), iq_A=tuple(iq_range), speed_rpm=base.domain.speed_rpm,
                             kind="allowed_operating_limit",
                             interpretation="test configuration domain for the manufactured map")
    return DriveModel(
        drive_id=fx["fixture_id"] + "_TEST_DRIVE",
        revision=str(fx["metadata"]["version"]),
        motor=motor,
        inverter=inverter,
        domain=domain,
        provenance=prov,
        notes=("manufactured magnetic potential for interpolation/reciprocity verification; not a measured motor",
               "Rs, losses and voltage reserve borrowed from the synthetic drive for solver exercise only"),
    )
