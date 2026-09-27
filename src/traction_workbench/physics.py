"""Steady-state fundamental physics: kernel, forward evaluation, constraints.

Contract (02_Implementation_Handoff, H4):

    omega_m = 2*pi*n/60, omega_e = p*omega_m
    v_d = Rs*i_d - omega_e*psi_q,  v_q = Rs*i_q + omega_e*psi_d
    T_em = 1.5*p*(psi_d*i_q - psi_q*i_d)
    T_shaft = T_em - tau_rot(omega_m),  P_rot = omega_m*tau_rot >= 0
    P_cu = 1.5*Rs*(i_d^2 + i_q^2)
    P_ac = 1.5*(v_d*i_d + v_q*i_q) = T_em*omega_m + P_cu = P_shaft + P_cu + P_rot
    P_dc = P_ac + P_inv,  I_dc = P_dc / Vdc
    |v_motor + dv_inv| <= (1 - r_v) * Vdc / sqrt(3)

Torque, current and voltage are always computed at the same candidate point.
A forward evaluation never moves the point; a violating point is returned as a
diagnostic, never as a feasible witness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .errors import InputValidationError, OutsideModelDomain
from .models.components import DriveModel
from .models.flux import ConstantFluxModel, FluxMapModel, _finite
from .scenario import Scenario
from .settings import DEFAULT_SETTINGS, NumericalSettings
from .status import Reason

SQRT2 = math.sqrt(2.0)
SQRT3 = math.sqrt(3.0)
SQRT_3_2 = math.sqrt(1.5)

TEMP_MATCH_TOL_C = 0.01


@dataclass(frozen=True)
class ModelIssue:
    reason: Reason
    message: str

    def to_dict(self) -> dict:
        return {"reason": self.reason.value, "message": self.message}


class DriveKernel:
    """Vectorised model of one drive at one scenario boundary (n, Vdc, temperatures)."""

    def __init__(self, drive: DriveModel, scenario: Scenario, settings: NumericalSettings = DEFAULT_SETTINGS):
        self.drive = drive
        self.scenario = scenario
        self.settings = settings
        motor = drive.motor
        inv = drive.inverter
        self.p = motor.pole_pairs
        self.omega_m = scenario.omega_m
        self.omega_e = self.p * self.omega_m
        self.speed_rpm = scenario.speed_rpm
        self.Vdc = scenario.Vdc_V
        self.V_ceiling = inv.voltage.hardware_ceiling_V(self.Vdc)
        self.Vb = inv.voltage.command_budget_V(self.Vdc)
        self.reserve_fraction = inv.voltage.reserve_fraction
        self.R_drop = inv.voltage.resistive_drop_ohm
        self.Imax = inv.current_limit_A_peak
        self.domain = drive.domain
        self.limits = scenario.source_limits
        self.notes: list[str] = []
        self.issues: list[ModelIssue] = []
        self.Rs = self._resolve_rs(motor, scenario)
        self.plane = None
        self.psi = None
        if isinstance(motor.flux, ConstantFluxModel):
            self.kind = "constant_dq"
            self.psi = self._resolve_psi(motor, scenario)
            self.Ld = motor.flux.Ld_H
            self.Lq = motor.flux.Lq_H
        else:
            self.kind = "flux_map"
            self.Ld = self.Lq = None
            try:
                self.plane, note = motor.flux.plane_for(scenario.magnet_temp_C)
                self.notes.append(note)
            except OutsideModelDomain as exc:
                reason = Reason.MISSING_INPUT if exc.detail.get("reason") == "MISSING_INPUT" else Reason.OUTSIDE_MODEL_DOMAIN
                self.issues.append(ModelIssue(reason, str(exc)))
        self.rot = motor.rotational_loss
        if self.rot is None:
            self.tau_rot = None
            self.P_rot = None
        else:
            self.tau_rot = self.rot.torque(self.omega_m)
            self.P_rot = self.rot.power(self.omega_m)
        self.inv_loss = inv.loss
        if self.inv_loss is not None and self.inv_loss.valid_Vdc_V is not None:
            lo, hi = self.inv_loss.valid_Vdc_V
            if not (lo <= self.Vdc <= hi):
                self.issues.append(ModelIssue(
                    Reason.OUTSIDE_MODEL_DOMAIN,
                    f"inverter loss surrogate validated for Vdc in [{lo:g}, {hi:g}] V, scenario Vdc={self.Vdc:g} V"))
        self.P_dis_eff = self.limits.effective_discharge_W(self.Vdc)
        self.P_chg_eff = self.limits.effective_charge_W(self.Vdc)
        if abs(self.omega_m) <= settings.speed_zero_tol_rad_s:
            self.notes.append(
                "standstill: dq currents are DC phase currents distributed by the (unknown) electrical angle; "
                "RMS values are equivalent sinusoidal RMS, not individual phase RMS; stall thermal capability is not inferred")

    # -- temperature resolution -------------------------------------------

    def _resolve_rs(self, motor, scenario) -> float:
        t = scenario.winding_temp_C
        if t is None:
            ref = motor.reference_winding_temp_C
            self.notes.append(
                "winding temperature not stated in scenario; Rs used as supplied"
                + (f" (reference {ref:g} degC)" if ref is not None else " (reference temperature not declared)"))
            return motor.Rs_ohm
        ref = motor.reference_winding_temp_C
        if ref is None:
            self.issues.append(ModelIssue(
                Reason.MISSING_INPUT,
                f"scenario winding temperature {t:g} degC stated but the Rs reference temperature is not declared"))
            return motor.Rs_ohm
        if abs(t - ref) <= TEMP_MATCH_TOL_C:
            return motor.Rs_ohm
        dep = motor.rs_temperature
        if dep is None or not (dep.valid_C[0] <= t <= dep.valid_C[1]):
            self.issues.append(ModelIssue(
                Reason.OUTSIDE_MODEL_DOMAIN,
                f"Rs defined at {ref:g} degC; no validated temperature dependence covers {t:g} degC"))
            return motor.Rs_ohm
        rs = motor.Rs_ohm * (1.0 + dep.coeff_per_K * (t - ref))
        self.notes.append(f"Rs adjusted {motor.Rs_ohm:g} -> {rs:g} ohm for winding {t:g} degC ({dep.basis})")
        return rs

    def _resolve_psi(self, motor, scenario) -> float:
        psi0 = motor.flux.psi_pm_Wb
        t = scenario.magnet_temp_C
        if t is None:
            ref = motor.reference_magnet_temp_C
            self.notes.append(
                "magnet temperature not stated in scenario; psi_PM used as supplied"
                + (f" (reference {ref:g} degC)" if ref is not None else " (reference temperature not declared)"))
            return psi0
        ref = motor.reference_magnet_temp_C
        if ref is None:
            self.issues.append(ModelIssue(
                Reason.MISSING_INPUT,
                f"scenario magnet temperature {t:g} degC stated but the psi_PM reference temperature is not declared"))
            return psi0
        if abs(t - ref) <= TEMP_MATCH_TOL_C:
            return psi0
        dep = motor.psi_temperature
        if dep is None or not (dep.valid_C[0] <= t <= dep.valid_C[1]):
            self.issues.append(ModelIssue(
                Reason.OUTSIDE_MODEL_DOMAIN,
                f"psi_PM defined at {ref:g} degC; no validated temperature dependence covers {t:g} degC"))
            return psi0
        psi = psi0 * (1.0 + dep.coeff_per_K * (t - ref))
        self.notes.append(f"psi_PM adjusted {psi0:g} -> {psi:g} Wb for magnet {t:g} degC ({dep.basis})")
        return psi

    # -- model evaluation ---------------------------------------------------

    @property
    def evaluable(self) -> bool:
        return self.kind == "constant_dq" or self.plane is not None

    @property
    def shaft_defined(self) -> bool:
        return self.tau_rot is not None

    @property
    def dc_defined(self) -> bool:
        return self.inv_loss is not None and self.tau_rot is not None

    @property
    def tau_rot_or_zero(self) -> float:
        return 0.0 if self.tau_rot is None else self.tau_rot

    def flux(self, d, q):
        if self.kind == "constant_dq":
            return self.drive.motor.flux.flux(d, q, psi_pm_Wb=self.psi)
        if self.plane is None:
            raise OutsideModelDomain("flux map not available for this scenario",
                                     detail={"issues": [i.message for i in self.issues]})
        return self.plane.interpolate(d, q)

    def uncovered_rectangles(self):
        if self.kind == "constant_dq":
            return self.drive.motor.flux.uncovered_rectangles()
        return self.plane.uncovered_rectangles()

    def evaluate(self, d, q) -> dict:
        d = np.asarray(d, dtype=float)
        q = np.asarray(q, dtype=float)
        psd, psq, ok = self.flux(d, q)
        we, rs = self.omega_e, self.Rs
        vd = rs * d - we * psq
        vq = rs * q + we * psd
        vcd = vd + self.R_drop * d
        vcq = vq + self.R_drop * q
        i2 = d * d + q * q
        tem = 1.5 * self.p * (psd * q - psq * d)
        pac = 1.5 * (vd * d + vq * q)
        pcu = 1.5 * rs * i2
        tsh = tem - self.tau_rot if self.tau_rot is not None else np.full_like(tem, np.nan)
        pinv = self.inv_loss.loss(i2) if self.inv_loss is not None else np.full_like(tem, np.nan)
        pdc = pac + pinv
        return {"psd": psd, "psq": psq, "ok": ok, "vd": vd, "vq": vq, "vcd": vcd, "vcq": vcq,
                "vcmd2": vcd * vcd + vcq * vcq, "i2": i2, "tem": tem, "tsh": tsh, "pac": pac,
                "pcu": pcu, "pinv": pinv, "pdc": pdc}

    def torque_scale(self) -> float:
        s = self.settings.torque_scale_Nm
        if s is not None:
            return float(s)
        if self.kind == "constant_dq":
            psi = self.psi if self.psi is not None else 0.0
            dl = abs(self.Ld - self.Lq)
            return max(1.0, 1.5 * self.p * (psi * self.Imax + dl * self.Imax ** 2 / 2.0))
        plane = self.plane
        d, q = np.meshgrid(plane.id_axis_A, plane.iq_axis_A, indexing="ij")
        tem = 1.5 * self.p * (plane.psi_d_Wb * q - plane.psi_q_Wb * d)
        return max(1.0, float(np.nanmax(np.abs(np.where(plane.valid, tem, np.nan)))))

    def describe(self) -> dict:
        return {
            "speed_rpm": self.speed_rpm,
            "omega_m_rad_s": self.omega_m,
            "omega_e_rad_s": self.omega_e,
            "Vdc_V": self.Vdc,
            "voltage_hardware_ceiling_V_peak": self.V_ceiling,
            "voltage_reserve_fraction": self.reserve_fraction,
            "voltage_command_budget_V_peak": self.Vb,
            "current_limit_A_peak": self.Imax,
            "Rs_ohm_used": self.Rs,
            "psi_pm_Wb_used": self.psi,
            "tau_rot_Nm": self.tau_rot,
            "P_rot_W": self.P_rot,
            "effective_discharge_limit_W": self.P_dis_eff,
            "effective_charge_limit_W": self.P_chg_eff,
            "notes": list(self.notes),
            "issues": [i.to_dict() for i in self.issues],
        }


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

SATISFIED = "SATISFIED"
ACTIVE = "ACTIVE"
VIOLATED = "VIOLATED"
NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True)
class ConstraintResult:
    name: str
    group: str
    sense: str               # "upper" (demand <= limit) or "lower" (demand >= limit)
    limit: float | None
    demand: float | None
    unit: str
    tolerance: float
    source: str
    kind: str = "hard_limit"  # hard_limit | allowed_domain | model_validity
    details: tuple = ()

    @property
    def slack(self) -> float | None:
        if self.limit is None or self.demand is None:
            return None
        return self.limit - self.demand if self.sense == "upper" else self.demand - self.limit

    @property
    def state(self) -> str:
        s = self.slack
        if s is None or not math.isfinite(s):
            return NOT_EVALUATED
        if s < -self.tolerance:
            return VIOLATED
        if s <= self.tolerance:
            return ACTIVE
        return SATISFIED

    @property
    def ok(self) -> bool:
        return self.state in (SATISFIED, ACTIVE)

    def normalized_violation(self) -> float:
        s = self.slack
        if s is None:
            return 0.0
        return max(0.0, -s) / max(abs(self.limit), 1e-300) if self.limit else max(0.0, -s)

    def to_dict(self) -> dict:
        out = {
            "name": self.name,
            "group": self.group,
            "sense": self.sense,
            "limit": self.limit,
            "demand": self.demand,
            "slack": self.slack,
            "unit": self.unit,
            "tolerance": self.tolerance,
            "state": self.state,
            "kind": self.kind,
            "source": self.source,
        }
        if self.details:
            out["details"] = dict(self.details)
        return out


def _tol(settings: NumericalSettings, limit: float, abs_floor: float) -> float:
    return max(abs_floor, settings.constraint_rel_tol * abs(limit))


def build_constraints(k: DriveKernel, *, id_A: float, iq_A: float, v_cmd_peak: float, i_peak: float,
                      p_dc: float | None, i_dc: float | None) -> tuple[ConstraintResult, ...]:
    s = k.settings
    dom = k.domain
    dom_kind = "allowed_domain" if dom.kind == "allowed_operating_limit" else "model_validity"
    out = [
        ConstraintResult(
            "VOLTAGE", "VOLTAGE", "upper", k.Vb, v_cmd_peak, "V (phase peak)",
            _tol(s, k.Vb, s.voltage_abs_tol_V),
            "linear SVPWM command budget (1 - r_v)*Vdc/sqrt(3)",
            details=(("hardware_ceiling_V", k.V_ceiling), ("reserve_fraction", k.reserve_fraction),
                     ("reserve_V", k.V_ceiling - k.Vb), ("command_budget_V", k.Vb),
                     ("voltage_error_model", "ideal (dv_inv = 0)" if k.R_drop == 0 else f"resistive {k.R_drop} ohm"),
                     ("note", "remaining command margin; the reserve is already subtracted"))),
        ConstraintResult(
            "CURRENT", "CURRENT", "upper", k.Imax, i_peak, "A (fundamental phase peak)",
            _tol(s, k.Imax, s.current_abs_tol_A),
            "inverter fundamental current limit (dq norm)",
            details=(("note", "does not cover PWM ripple, pulse peak, OC overshoot or SOA"),)),
        ConstraintResult("ID_MIN", "DOMAIN", "lower", dom.id_A[0], id_A, "A (peak)",
                         _tol(s, dom.id_A[0], s.current_abs_tol_A), "declared operating domain", dom_kind),
        ConstraintResult("ID_MAX", "DOMAIN", "upper", dom.id_A[1], id_A, "A (peak)",
                         _tol(s, dom.id_A[1], s.current_abs_tol_A), "declared operating domain", dom_kind),
        ConstraintResult("IQ_MIN", "DOMAIN", "lower", dom.iq_A[0], iq_A, "A (peak)",
                         _tol(s, dom.iq_A[0], s.current_abs_tol_A), "declared operating domain", dom_kind),
        ConstraintResult("IQ_MAX", "DOMAIN", "upper", dom.iq_A[1], iq_A, "A (peak)",
                         _tol(s, dom.iq_A[1], s.current_abs_tol_A), "declared operating domain", dom_kind),
        ConstraintResult("SPEED_MIN", "DOMAIN", "lower", dom.speed_rpm[0], k.speed_rpm, "rpm (mechanical)",
                         _tol(s, dom.speed_rpm[0], s.speed_abs_tol_rpm), "declared operating domain", dom_kind),
        ConstraintResult("SPEED_MAX", "DOMAIN", "upper", dom.speed_rpm[1], k.speed_rpm, "rpm (mechanical)",
                         _tol(s, dom.speed_rpm[1], s.speed_abs_tol_rpm), "declared operating domain", dom_kind),
    ]
    lim = k.limits
    dc_src = lim.source or "DC source limit (scenario)"
    for name, group, sense, limit, demand, unit, floor in (
        ("DC_DISCHARGE_POWER", "DISCHARGE_SOURCE", "upper", lim.discharge_power_max_W, p_dc, "W", s.power_abs_tol_W),
        ("DC_CHARGE_POWER", "CHARGE_SOURCE", "lower",
         None if lim.charge_power_max_W is None else -lim.charge_power_max_W, p_dc, "W", s.power_abs_tol_W),
        ("DC_DISCHARGE_CURRENT", "DISCHARGE_SOURCE", "upper", lim.discharge_current_max_A, i_dc, "A (average)",
         s.current_abs_tol_A),
        ("DC_CHARGE_CURRENT", "CHARGE_SOURCE", "lower",
         None if lim.charge_current_max_A is None else -lim.charge_current_max_A, i_dc, "A (average)",
         s.current_abs_tol_A),
    ):
        if limit is None:
            continue
        out.append(ConstraintResult(name, group, sense, limit, demand, unit, _tol(s, limit, floor), dc_src,
                                    details=(("note", "average DC quantity; not ripple RMS or transient peak"),)))
    return tuple(out)


# ---------------------------------------------------------------------------
# Energy mode
# ---------------------------------------------------------------------------

def classify_energy(p_shaft: float | None, p_dc: float | None, omega_m: float,
                    settings: NumericalSettings) -> tuple[str, float | None, str]:
    tol = settings.power_zero_tol_W
    if p_shaft is None or p_dc is None:
        return "UNDETERMINED", None, "shaft or DC power undefined (loss model missing)"
    if abs(omega_m) <= settings.speed_zero_tol_rad_s:
        return "STANDSTILL", None, f"efficiency N/A at zero speed (|omega_m| <= {settings.speed_zero_tol_rad_s} rad/s); losses are reported"
    if abs(p_shaft) <= tol:
        return "ZERO_SHAFT_POWER", None, f"efficiency N/A at zero shaft power (|P_shaft| <= {tol} W)"
    if p_shaft > tol:
        if p_dc <= tol:
            return "ACCOUNTING_INCONSISTENCY", None, "positive shaft power without DC input: check loss signs/definitions"
        eta = p_shaft / p_dc
        if not (0.0 <= eta <= 1.0):
            return "ACCOUNTING_INCONSISTENCY", eta, "motoring efficiency outside [0, 1]: check definitions (not clamped)"
        return "MOTORING", eta, "eta_mot = P_shaft / P_dc"
    if p_dc < -tol:
        eta = abs(p_dc) / abs(p_shaft)
        if not (0.0 <= eta <= 1.0):
            return "ACCOUNTING_INCONSISTENCY", eta, "regenerating efficiency outside [0, 1]: check definitions (not clamped)"
        return "REGENERATING", eta, "eta_regen = |P_dc| / |P_shaft| (net DC energy recovery)"
    return ("BRAKING_WITHOUT_NET_DC_RECOVERY", None,
            "mechanical braking (P_shaft < 0) but P_dc >= 0: no net DC energy recovery; efficiency N/A")


# ---------------------------------------------------------------------------
# Operating point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OperatingPoint:
    id_A: float
    iq_A: float
    speed_rpm: float
    omega_m: float
    omega_e: float
    f_e_Hz: float
    Vdc_V: float
    psi_d_Wb: float
    psi_q_Wb: float
    vd_V: float
    vq_V: float
    v_peak_V: float
    v_LL_rms_V: float
    v_cmd_peak_V: float
    i_peak_A: float
    i_phase_rms_A: float
    rms_interpretation: str
    Te_Nm: float
    tau_rot_Nm: float | None
    Tshaft_Nm: float | None
    Pshaft_W: float | None
    Pcu_W: float
    Prot_W: float | None
    Pac_W: float
    Pinv_W: float | None
    Pdc_W: float | None
    Idc_A: float | None
    residual_pac_tem_W: float
    residual_pac_shaft_W: float | None
    residual_dc_W: float | None
    identity_tolerance_W: float
    energy_mode: str
    efficiency: float | None
    efficiency_note: str
    voltage_budget_V: float
    voltage_ceiling_V: float
    voltage_margin_V: float
    constraints: tuple[ConstraintResult, ...]
    pwm_ratio: float | None
    notes: tuple[str, ...] = ()

    @property
    def identities_ok(self) -> bool:
        vals = [self.residual_pac_tem_W, self.residual_pac_shaft_W, self.residual_dc_W]
        return all(v is None or abs(v) <= self.identity_tolerance_W for v in vals)

    def violations(self) -> tuple[ConstraintResult, ...]:
        return tuple(c for c in self.constraints if c.state == VIOLATED)

    def violated_groups(self) -> list[str]:
        out: list[str] = []
        for c in self.violations():
            if c.group not in out:
                out.append(c.group)
        return out

    def active(self) -> tuple[ConstraintResult, ...]:
        return tuple(c for c in self.constraints if c.state == ACTIVE)

    def constraint(self, name: str) -> ConstraintResult | None:
        for c in self.constraints:
            if c.name == name:
                return c
        return None

    def all_satisfied(self, groups: tuple[str, ...] | None = None) -> bool:
        return all(c.ok for c in self.constraints if groups is None or c.group in groups)

    def max_normalized_violation(self, groups: tuple[str, ...] | None = None) -> float:
        vals = [c.normalized_violation() for c in self.constraints if groups is None or c.group in groups]
        return max(vals) if vals else 0.0

    def to_dict(self) -> dict:
        return {
            "id_A_peak": self.id_A,
            "iq_A_peak": self.iq_A,
            "speed_rpm_mechanical": self.speed_rpm,
            "omega_m_rad_s": self.omega_m,
            "omega_e_rad_s": self.omega_e,
            "f_e_Hz": self.f_e_Hz,
            "Vdc_V": self.Vdc_V,
            "psi_d_Wb": self.psi_d_Wb,
            "psi_q_Wb": self.psi_q_Wb,
            "vd_V_peak": self.vd_V,
            "vq_V_peak": self.vq_V,
            "v_phase_peak_V": self.v_peak_V,
            "v_LL_rms_V": self.v_LL_rms_V,
            "v_command_peak_V": self.v_cmd_peak_V,
            "i_peak_A": self.i_peak_A,
            "i_phase_rms_A": self.i_phase_rms_A,
            "rms_interpretation": self.rms_interpretation,
            "Te_Nm": self.Te_Nm,
            "tau_rot_Nm": self.tau_rot_Nm,
            "Tshaft_Nm": self.Tshaft_Nm,
            "Pshaft_W": self.Pshaft_W,
            "Pcu_W": self.Pcu_W,
            "Prot_W": self.Prot_W,
            "Pac_W": self.Pac_W,
            "Pinv_W": self.Pinv_W,
            "Pdc_W": self.Pdc_W,
            "Idc_A_average": self.Idc_A,
            "loss_breakdown_W": {"copper": self.Pcu_W, "rotational": self.Prot_W, "inverter": self.Pinv_W},
            "power_identity_residuals_W": {
                "Pac - (Te*wm + Pcu)": self.residual_pac_tem_W,
                "Pac - (Pshaft + Pcu + Prot)": self.residual_pac_shaft_W,
                "Pdc - (Pac + Pinv)": self.residual_dc_W,
                "tolerance": self.identity_tolerance_W,
                "ok": self.identities_ok,
            },
            "energy_mode": self.energy_mode,
            "efficiency": self.efficiency,
            "efficiency_note": self.efficiency_note,
            "voltage": {
                "hardware_ceiling_V_peak": self.voltage_ceiling_V,
                "command_budget_V_peak": self.voltage_budget_V,
                "reserve_V": self.voltage_ceiling_V - self.voltage_budget_V,
                "command_demand_V_peak": self.v_cmd_peak_V,
                "remaining_command_margin_V": self.voltage_margin_V,
            },
            "constraints": [c.to_dict() for c in self.constraints],
            "violated_groups": self.violated_groups(),
            "pwm_to_electrical_frequency_ratio": self.pwm_ratio,
            "pwm_ratio_note": "reported only; no universal threshold is applied",
            "notes": list(self.notes),
        }


def _check_current(name: str, value: float) -> float:
    return _finite(name, value)


def evaluate_point(kernel: DriveKernel, id_A: float, iq_A: float) -> OperatingPoint:
    """Forward evaluation of the given (id, iq) at the kernel's scenario.

    Raises ``OutsideModelDomain`` if the flux model does not define the point.
    """
    d = _check_current("id_A", id_A)
    q = _check_current("iq_A", iq_A)
    k = kernel
    s = k.settings
    if not k.evaluable:
        raise OutsideModelDomain("model not evaluable for this scenario",
                                 detail={"issues": [i.to_dict() for i in k.issues]})
    ev = k.evaluate(d, q)
    if not bool(ev["ok"]):
        raise OutsideModelDomain(
            f"(id={d:g} A, iq={q:g} A) is outside the model's covered/valid domain; no extrapolation",
            detail={"id_A": d, "iq_A": q})
    f = {key: float(v) for key, v in ev.items() if key != "ok"}
    i_peak = math.sqrt(f["i2"])
    v_peak = math.hypot(f["vd"], f["vq"])
    v_cmd = math.sqrt(f["vcmd2"])
    wm = k.omega_m
    tsh = f["tsh"] if k.shaft_defined else None
    p_shaft = tsh * wm if tsh is not None else None
    p_rot = k.P_rot
    p_inv = f["pinv"] if k.inv_loss is not None else None
    p_dc = (f["pac"] + p_inv) if (p_inv is not None) else None
    i_dc = p_dc / k.Vdc if p_dc is not None else None
    res1 = f["pac"] - (f["tem"] * wm + f["pcu"])
    res2 = f["pac"] - (p_shaft + f["pcu"] + p_rot) if p_shaft is not None else None
    res3 = p_dc - (f["pac"] + p_inv) if p_dc is not None else None
    scale = max(abs(f["pac"]), abs(f["pcu"]), abs(p_dc or 0.0), abs(p_shaft or 0.0), abs(f["tem"] * wm))
    id_tol = max(s.power_identity_abs_W, s.power_identity_rel * scale)
    mode, eta, eta_note = classify_energy(p_shaft, p_dc, wm, s)
    standstill = abs(wm) <= s.speed_zero_tol_rad_s
    rms_note = ("equivalent sinusoidal RMS (standstill: individual phase RMS depends on the electrical angle)"
                if standstill else "phase RMS of the balanced fundamental")
    fe = abs(k.omega_e) / (2.0 * math.pi)
    fsw = k.scenario.switching_frequency_Hz or k.drive.inverter.switching_frequency_context_Hz
    pwm_ratio = (fsw / fe) if (fsw and fe > 0) else None
    constraints = build_constraints(k, id_A=d, iq_A=q, v_cmd_peak=v_cmd, i_peak=i_peak, p_dc=p_dc, i_dc=i_dc)
    notes = list(k.notes)
    if not k.shaft_defined:
        notes.append("rotational loss model missing: shaft torque/power undefined (electromagnetic values only)")
    if k.inv_loss is None:
        notes.append("inverter loss model missing: DC power/current undefined")
    return OperatingPoint(
        id_A=d, iq_A=q, speed_rpm=k.speed_rpm, omega_m=wm, omega_e=k.omega_e, f_e_Hz=fe, Vdc_V=k.Vdc,
        psi_d_Wb=f["psd"], psi_q_Wb=f["psq"], vd_V=f["vd"], vq_V=f["vq"], v_peak_V=v_peak,
        v_LL_rms_V=SQRT_3_2 * v_peak, v_cmd_peak_V=v_cmd, i_peak_A=i_peak, i_phase_rms_A=i_peak / SQRT2,
        rms_interpretation=rms_note, Te_Nm=f["tem"], tau_rot_Nm=k.tau_rot, Tshaft_Nm=tsh, Pshaft_W=p_shaft,
        Pcu_W=f["pcu"], Prot_W=p_rot, Pac_W=f["pac"], Pinv_W=p_inv, Pdc_W=p_dc, Idc_A=i_dc,
        residual_pac_tem_W=res1, residual_pac_shaft_W=res2, residual_dc_W=res3, identity_tolerance_W=id_tol,
        energy_mode=mode, efficiency=eta, efficiency_note=eta_note, voltage_budget_V=k.Vb,
        voltage_ceiling_V=k.V_ceiling, voltage_margin_V=k.Vb - v_cmd, constraints=constraints,
        pwm_ratio=pwm_ratio, notes=tuple(notes),
    )


@dataclass(frozen=True)
class ForwardResult:
    """H5.1: the given (id, iq) evaluated as-is."""

    point: OperatingPoint | None
    evaluable: bool
    reason: Reason | None
    message: str
    issues: tuple = ()

    @property
    def all_constraints_ok(self) -> bool | None:
        return None if self.point is None else self.point.all_satisfied()

    def to_dict(self) -> dict:
        return {
            "query": "forward_evaluation",
            "evaluable": self.evaluable,
            "reason": None if self.reason is None else self.reason.value,
            "message": self.message,
            "model_issues": [i.to_dict() for i in self.issues],
            "all_constraints_satisfied": self.all_constraints_ok,
            "semantics": "the point is evaluated as given and never moved; a violating point is a diagnostic, "
                         "not a feasible witness",
            "operating_point": None if self.point is None else self.point.to_dict(),
        }


def forward_evaluation(drive: DriveModel, scenario: Scenario, id_A: float, iq_A: float,
                       settings: NumericalSettings = DEFAULT_SETTINGS) -> ForwardResult:
    k = DriveKernel(drive, scenario, settings)
    try:
        pt = evaluate_point(k, id_A, iq_A)
    except OutsideModelDomain as exc:
        return ForwardResult(None, False, Reason.OUTSIDE_MODEL_DOMAIN, str(exc), tuple(k.issues))
    msg = "all evaluated constraints satisfied" if pt.all_satisfied() else (
        "constraint violation(s): " + ", ".join(c.name for c in pt.violations()))
    return ForwardResult(pt, True, None, msg, tuple(k.issues))
