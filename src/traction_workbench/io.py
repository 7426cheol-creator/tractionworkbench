"""Production input format (JSON with explicit units and definitions).

A *case file* bundles a drive (inline or ``{"builtin": ...}``), one
requirement, an optional scenario template (DC limits, temperatures), rating
envelopes and requested analyses.  Non-finite tokens (NaN, Infinity) are
rejected at parse time.  All unit/definition conversions are recorded.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .errors import InputValidationError
from .models import (
    ConstantFluxModel,
    CurrentBox,
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
    TemperatureDependence,
    VoltageModel,
    WindingDefinition,
)
from .requirement import Requirement
from .scenario import DcSourceLimits, Scenario
from .units import Conversions, current_peak, pm_flux_linkage, quantity, resistance_per_phase, speed_rpm

BUILTIN_DRIVES = ("SYNTH_IPMSM_200KW_REF_V1", "MANUFACTURED_FLUX_MAP_TEST_DRIVE")


def _reject_constant(token: str):
    raise InputValidationError(f"non-finite JSON token {token!r} is not a valid number", field="json")


def parse_json(text: str) -> dict:
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise InputValidationError(f"invalid JSON: {exc}", field="json") from None


def load_json_file(path) -> dict:
    return parse_json(Path(path).read_text(encoding="utf-8"))


def _req(d: dict, key: str, where: str):
    if key not in d:
        raise InputValidationError(f"missing required field {key!r}", field=where)
    return d[key]


def provenance_from_dict(d: dict | None, where: str) -> Provenance:
    if not d:
        raise InputValidationError("provenance is required (origin, source, revision, validation_status)", field=where)
    try:
        origin = DataOrigin(_req(d, "origin", where))
    except ValueError:
        raise InputValidationError(f"origin must be one of {[o.value for o in DataOrigin]}", field=where) from None
    return Provenance(origin, str(_req(d, "source", where)), str(_req(d, "revision", where)),
                      str(_req(d, "validation_status", where)), d.get("sha256"), tuple(d.get("notes", ())))


def _flux_map_from_dict(fm: dict, conv: Conversions) -> FluxMapModel:
    """Flux-map import with the frame fixed at import time (review P0-B).

    Required declarations: ``axis_convention`` 'd_on_PM' (PM flux on +d; an SR/"d on max permeance" map must be
    converted before import), ``park`` 'amplitude_invariant' (phase-peak dq values) and each plane's
    ``array_order``.  Anything ambiguous is rejected instead of guessed.
    """
    conv_ax = fm.get("axis_convention")
    if conv_ax != "d_on_PM":
        raise InputValidationError("flux_map.axis_convention must be declared 'd_on_PM' (PM flux on +d); maps in "
                                   "another rotor frame (e.g. SR convention) must be converted before import",
                                   field="motor.flux_map.axis_convention")
    if fm.get("park") != "amplitude_invariant":
        raise InputValidationError("flux_map.park must be declared 'amplitude_invariant' (phase-peak dq values)",
                                   field="motor.flux_map.park")
    planes = []
    for i, p in enumerate(_req(fm, "planes", "motor.flux_map")):
        w = f"motor.flux_map.planes[{i}]"
        order = str(_req(p, "array_order", w)).replace(" ", "")
        if order not in ("row=id,column=iq", "row=iq,column=id"):
            raise InputValidationError("array_order must be declared as 'row=id, column=iq' or 'row=iq, column=id' "
                                       "(a square map cannot be checked by its shape)", field=w)
        ida = current_peak(_req(p, "id_axis", w), w + ".id_axis", conv, allow_list=True)
        iqa = current_peak(_req(p, "iq_axis", w), w + ".iq_axis", conv, allow_list=True)
        psd = _flux_array(_req(p, "psi_d", w), w + ".psi_d")
        psq = _flux_array(_req(p, "psi_q", w), w + ".psi_q")
        valid = None if p.get("valid") is None else np.array(p["valid"], dtype=bool)
        if order == "row=iq,column=id":
            # e.g. MATLAB meshgrid(id, iq) layouts: transposed once, explicitly, and recorded
            psd, psq = psd.T, psq.T
            valid = None if valid is None else valid.T
            conv.add(w + ".array_order", order, "row=id, column=iq", "declared row=iq layout transposed to row=id")
        planes.append(FluxMapPlane(np.array(ida), np.array(iqa), psd, psq, valid,
                                   p.get("magnet_temp_C"), p.get("label", "")))
    return FluxMapModel(tuple(planes), conservative=bool(fm.get("conservative", True)), symmetry=fm.get("symmetry"),
                        temperature_interpolation=fm.get("temperature_interpolation"),
                        temperature_interpolation_basis=fm.get("temperature_interpolation_basis", ""),
                        reciprocity_rel_tol=float(fm.get("reciprocity_rel_tol", 1e-3)))


def _flux_array(obj, where):
    if not isinstance(obj, dict) or obj.get("unit") not in ("Wb", "mWb", "Vs"):
        raise InputValidationError("flux arrays need {'value': [[...]], 'unit': 'Wb'|'mWb'}", field=where)
    k = 1e-3 if obj["unit"] == "mWb" else 1.0
    try:
        arr = np.array(obj["value"], dtype=float) * k
    except (TypeError, ValueError):
        raise InputValidationError("flux array must be numeric (use the validity mask for holes)", field=where) from None
    return arr


SINGLE_VSI_NAMES = ("single_vsi", "single three-phase two-level vsi", "2l_vsi", "vsi")


def drive_from_dict(d: dict, conv: Conversions | None = None) -> DriveModel:
    conv = conv or Conversions()
    if "builtin" in d:
        from . import spec_fixtures as sf
        name = d["builtin"]
        if name == "SYNTH_IPMSM_200KW_REF_V1":
            return sf.synthetic_drive(d.get("overrides"))
        if name == "MANUFACTURED_FLUX_MAP_TEST_DRIVE":
            return sf.manufactured_map_drive()
        raise InputValidationError(f"unknown builtin drive {name!r}; known: {', '.join(BUILTIN_DRIVES)}",
                                   field="drive.builtin")
    prov = provenance_from_dict(d.get("provenance"), "drive.provenance")
    m = _req(d, "motor", "drive")
    connection = m.get("connection", "wye")
    p = _req(m, "pole_pairs", "drive.motor")
    if not isinstance(p, int) or isinstance(p, bool):
        raise InputValidationError("pole_pairs must be an integer (pole pairs, not poles)", field="drive.motor.pole_pairs")
    rs = resistance_per_phase(_req(m, "Rs", "drive.motor"), connection, "drive.motor.Rs", conv)
    model = _req(m, "model", "drive.motor")
    if model == "constant_dq":
        psi = pm_flux_linkage(m, p, conv, connection)
        ld = quantity(_req(m, "Ld", "drive.motor"), "inductance", "drive.motor.Ld", conv)
        lq = quantity(_req(m, "Lq", "drive.motor"), "inductance", "drive.motor.Lq", conv)
        val = m.get("parameter_validity")
        box = None
        if val:
            box = CurrentBox(tuple(current_peak(val["id"], "drive.motor.parameter_validity.id", conv, True)),
                             tuple(current_peak(val["iq"], "drive.motor.parameter_validity.iq", conv, True)))
        flux = ConstantFluxModel(psi, ld, lq, validity=box)
        fid = Fidelity.D1
    elif model == "flux_map":
        flux = _flux_map_from_dict(_req(m, "flux_map", "drive.motor"), conv)
        fid = Fidelity.D2
    else:
        raise InputValidationError("motor.model must be 'constant_dq' or 'flux_map'", field="drive.motor.model")
    rl = m.get("rotational_loss")
    rot = None
    if rl is not None:
        rot = RotationalLossModel(
            viscous_Nm_per_rad_s=quantity(rl["viscous"], "drag", "rotational_loss.viscous", conv) if "viscous" in rl else 0.0,
            quadratic_Nm_per_rad2_s2=float(rl.get("quadratic_Nm_per_rad2_s2", 0.0)),
            includes_iron_loss=bool(rl.get("includes_iron_loss", False)),
            basis=rl.get("basis", "declared"), description=rl.get("description", ""))
    temps = m.get("reference_temperatures", {}) or {}
    rs_t = m.get("rs_temperature")
    psi_t = m.get("psi_temperature")
    motor = MotorModel(
        motor_id=str(m.get("motor_id", d.get("drive_id", "MOTOR"))), pole_pairs=p, flux=flux, Rs_ohm=rs,
        rotational_loss=rot, connection=connection, connection_note=m.get("connection_note", ""),
        reference_winding_temp_C=temps.get("winding_C"), reference_magnet_temp_C=temps.get("magnet_C"),
        rs_temperature=None if not rs_t else TemperatureDependence(rs_t["coeff_per_K"], tuple(rs_t["valid_C"]), rs_t["basis"]),
        psi_temperature=None if not psi_t else TemperatureDependence(psi_t["coeff_per_K"], tuple(psi_t["valid_C"]), psi_t["basis"]),
        fidelity=Fidelity(m.get("fidelity", fid.value)),
        winding=None if not m.get("winding") else WindingDefinition(
            **{k: m["winding"][k] for k in ("Q", "p", "y", "parallel_paths", "turns_per_coil") if k in m["winding"]},
            basis=str(m["winding"].get("basis", ""))))
    iv = _req(d, "inverter", "drive")
    topo = str(iv.get("topology", "single_vsi")).strip().lower()
    if topo not in SINGLE_VSI_NAMES:
        # topology identity (OEW/HEV addendum P0): a dual-bridge / open-end-winding / multi-machine declaration is
        # never solved as a single VSI; those circuits have their own equations (extensions.oew / extensions.hev)
        raise InputValidationError(
            f"inverter topology {iv.get('topology')!r} is not a single three-phase two-level VSI; the single-VSI "
            f"solver never re-interprets it (use the OEW / HEV analyses with an explicit topology)",
            field="drive.inverter.topology")
    imax = current_peak(_req(iv, "current_limit", "drive.inverter"), "drive.inverter.current_limit", conv)
    ls = iv.get("loss")
    loss = None
    if ls is not None:
        cb = ls.get("current_basis")
        coeff = quantity(ls["coeff"], "loss_coeff", "inverter.loss.coeff", conv)
        if cb == "fundamental_rms":
            conv.add("inverter.loss.coeff", ls["coeff"], coeff / 2, "W per A_rms^2 -> W per A_peak^2 (/ 2)")
            coeff /= 2
        elif cb != "fundamental_peak":
            raise InputValidationError("inverter loss coefficient needs current_basis fundamental_peak or "
                                       "fundamental_rms", field="drive.inverter.loss.current_basis")
        loss = InverterLossModel(
            offset_W=quantity(ls["offset"], "power", "inverter.loss.offset", conv), ipk2_coeff_W_per_A2=coeff,
            symmetric_motoring_regen=ls.get("symmetric_motoring_regen"), kind=ls.get("kind", "quadratic_current_surrogate"),
            valid_Vdc_V=tuple(ls["valid_Vdc_V"]) if ls.get("valid_Vdc_V") else None, description=ls.get("description", ""))
    fsw = iv.get("switching_frequency")
    inverter = InverterModel(
        inverter_id=str(iv.get("inverter_id", "INVERTER")), current_limit_A_peak=imax,
        voltage=VoltageModel(reserve_fraction=_req(iv, "voltage_reserve_fraction", "drive.inverter"),
                             voltage_error=iv.get("voltage_error", "ideal"),
                             resistive_drop_ohm=float(iv.get("resistive_drop_ohm", 0.0)),
                             mapping_note=iv.get("voltage_mapping_note", "")),
        loss=loss, switching_frequency_context_Hz=None if fsw is None else quantity(fsw, "frequency", "inverter.fsw", conv))
    dm = _req(d, "domain", "drive")
    domain = OperatingDomain(
        id_A=tuple(current_peak(_req(dm, "id", "drive.domain"), "drive.domain.id", conv, True)),
        iq_A=tuple(current_peak(_req(dm, "iq", "drive.domain"), "drive.domain.iq", conv, True)),
        speed_rpm=tuple(speed_rpm(_req(dm, "speed", "drive.domain"), "drive.domain.speed", conv, p, True)),
        kind=dm.get("kind", "allowed_operating_limit"), interpretation=dm.get("interpretation", ""))
    return DriveModel(str(_req(d, "drive_id", "drive")), str(_req(d, "revision", "drive")), motor, inverter, domain,
                      prov, tuple(d.get("notes", ())))


def inverter_to_dict(inv: InverterModel) -> dict:
    """An inverter model in the declared-units file form (SI values, fundamental phase-peak currents): what
    ``drive_from_dict`` reads back to the same model.  A datasheet module loss model is not part of this form (it
    lives in the project's module section) - the caller says so."""
    v = inv.voltage
    out = {"inverter_id": inv.inverter_id, "topology": "single_vsi",
           "current_limit": {"value": inv.current_limit_A_peak, "unit": "A", "basis": "fundamental_peak"},
           "voltage_reserve_fraction": v.reserve_fraction, "voltage_error": v.voltage_error,
           "resistive_drop_ohm": v.resistive_drop_ohm, "voltage_mapping_note": v.mapping_note}
    if inv.loss is not None:
        ls = inv.loss
        out["loss"] = {"offset": {"value": ls.offset_W, "unit": "W"},
                       "coeff": {"value": ls.ipk2_coeff_W_per_A2, "unit": "W/A^2"},
                       "current_basis": "fundamental_peak", "symmetric_motoring_regen": ls.symmetric_motoring_regen,
                       "kind": ls.kind, "description": ls.description}
        if ls.valid_Vdc_V is not None:
            out["loss"]["valid_Vdc_V"] = list(ls.valid_Vdc_V)
    if inv.switching_frequency_context_Hz is not None:
        out["switching_frequency"] = {"value": inv.switching_frequency_context_Hz, "unit": "Hz"}
    return out


def domain_to_dict(dom: OperatingDomain) -> dict:
    """An operating domain in the declared-units file form (phase-peak currents, mechanical rpm)."""
    return {"id": {"value": list(dom.id_A), "unit": "A", "basis": "fundamental_peak"},
            "iq": {"value": list(dom.iq_A), "unit": "A", "basis": "fundamental_peak"},
            "speed": {"value": list(dom.speed_rpm), "unit": "rpm", "kind": "mechanical"},
            "kind": dom.kind, "interpretation": dom.interpretation}


def limits_from_dict(d: dict | None, conv: Conversions) -> DcSourceLimits:
    if not d:
        return DcSourceLimits()
    def g(key, kind):
        return None if d.get(key) is None else quantity(d[key], kind, f"source_limits.{key}", conv)
    return DcSourceLimits(discharge_power_max_W=g("discharge_power_max", "power"),
                          charge_power_max_W=g("charge_power_max", "power"),
                          discharge_current_max_A=g("discharge_current_max", "current"),
                          charge_current_max_A=g("charge_current_max", "current"),
                          source=d.get("source", "case file"))


def requirement_from_dict(d: dict, conv: Conversions, pole_pairs: int | None = None) -> Requirement:
    t = _req(d, "target", "requirement")
    if t.get("torque", "shaft") != "shaft":
        raise InputValidationError("requirement torque must be shaft torque (declare 'torque': 'shaft')",
                                   field="requirement.target")
    target = quantity(t, "torque", "requirement.target", conv)
    c = _req(d, "conditions", "requirement")
    n = speed_rpm(_req(c, "speed", "requirement.conditions"), "requirement.conditions.speed", conv, pole_pairs)
    vd = _req(c, "Vdc", "requirement.conditions")
    if vd.get("port", "inverter_dc_terminal") != "inverter_dc_terminal":
        raise InputValidationError("Vdc must refer to the inverter DC terminal (battery-side values need a "
                                   "source model)", field="requirement.conditions.Vdc")
    v = quantity(vd, "voltage", "requirement.conditions.Vdc", conv, allow_list=True)
    dur = d.get("duration")
    dsec = None
    if dur is not None:
        if dur == "continuous":
            dsec = math.inf
        else:
            dsec = quantity(dur, "time", "requirement.duration", conv)
    def opt_t(key):
        x = c.get(key)
        return None if x is None else quantity(x, "temperature", f"requirement.conditions.{key}", conv)
    fsw = c.get("switching_frequency")
    return Requirement(
        req_id=str(_req(d, "id", "requirement")), text=str(_req(d, "text", "requirement")), target_Nm=target,
        speed_rpm=n, Vdc_V=tuple(v) if isinstance(v, list) else v, revision=str(d.get("revision", "A")),
        operator=d.get("operator", "achieve"),
        band_Nm=0.0 if d.get("band") is None else quantity(d["band"], "torque", "requirement.band", conv),
        Vdc_quantifier="for_all" if isinstance(v, list) else "at_point", duration_s=dsec,
        initial_state=c.get("initial_state"), coolant_temp_C=opt_t("coolant_temp"),
        winding_temp_C=opt_t("winding_temp"), magnet_temp_C=opt_t("magnet_temp"),
        switching_frequency_Hz=None if fsw is None else quantity(fsw, "frequency", "requirement.fsw", conv),
        exclusions=tuple(d.get("exclusions", ())), source=d.get("source", ""))


def rating_from_dict(d: dict, conv: Conversions):
    from .analysis.rating import RatingApproval, RatingEnvelope
    dur = _req(d, "duration", "rating")
    dsec = math.inf if dur == "continuous" else quantity(dur, "time", "rating.duration", conv)
    sp = speed_rpm(_req(d, "speed", "rating"), "rating.speed", conv, None, True)
    tm = quantity(_req(d, "max_motoring_torque", "rating"), "torque", "rating.max_motoring_torque", conv, True)
    tb = d.get("min_braking_torque")
    conds = []
    for k, v in (d.get("conditions") or {}).items():
        conds.append((k, tuple(v) if isinstance(v, list) else v))
    return RatingEnvelope(
        envelope_id=str(_req(d, "id", "rating")), revision=str(d.get("revision", "A")), duration_s=dsec,
        speed_rpm=tuple(sp), max_motoring_torque_Nm=tuple(tm),
        provenance=provenance_from_dict(d.get("provenance"), "rating.provenance"),
        min_braking_torque_Nm=None if tb is None else tuple(quantity(tb, "torque", "rating.min_braking_torque", conv, True)),
        conditions=tuple(conds), condition_tolerances=tuple((d.get("condition_tolerances") or {}).items()),
        interpolation=d.get("interpolation", "conservative"), evidence_kind=d.get("evidence_kind", "supplier_rated"),
        approval=None if not d.get("approval") else RatingApproval(**{
            k: v for k, v in d["approval"].items()
            if k in ("state", "evidence_id", "evidence_revision", "intended_use", "approved_by")}))


@dataclass
class Case:
    drive: DriveModel
    requirement: Requirement
    scenario: Scenario | None
    limits: DcSourceLimits
    ratings: tuple
    analyses: dict
    conversions: Conversions
    raw: dict = field(default_factory=dict)


def case_from_dict(d: dict) -> Case:
    conv = Conversions()
    drive = drive_from_dict(_req(d, "drive", "case"), conv)
    req = requirement_from_dict(_req(d, "requirement", "case"), conv, drive.motor.pole_pairs)
    sc_raw = d.get("scenario") or {}
    limits = limits_from_dict(sc_raw.get("source_limits"), conv)
    if not sc_raw.get("source_limits") and "builtin" in d["drive"] and d["drive"]["builtin"] in BUILTIN_DRIVES:
        from . import spec_fixtures as sf
        limits = sf.synthetic_limits()
        conv.add("scenario.source_limits", None, limits.describe(), "builtin synthetic fixture source limits")
    ratings = tuple(rating_from_dict(r, conv) for r in d.get("ratings", ()))
    scen = None
    if sc_raw:
        def opt_t(key):
            x = sc_raw.get(key)
            return None if x is None else quantity(x, "temperature", f"scenario.{key}", conv)
        scen = Scenario(scenario_id=sc_raw.get("id", "template"), speed_rpm=req.speed_rpm,
                        Vdc_V=req.Vdc_V[0] if req.is_range else req.Vdc_V, source_limits=limits,
                        winding_temp_C=opt_t("winding_temp"), magnet_temp_C=opt_t("magnet_temp"),
                        coolant_temp_C=opt_t("coolant_temp"), initial_state=sc_raw.get("initial_state"),
                        description=sc_raw.get("description", ""))
    return Case(drive, req, scen, limits, ratings, dict(d.get("analyses", {})), conv, d)


def load_case(path) -> Case:
    return case_from_dict(load_json_file(path))
