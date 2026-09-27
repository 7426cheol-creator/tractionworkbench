"""One operating point, several engineering views.

* phase waveforms by the amplitude-invariant inverse Park transform (d axis
  aligned with phase a at theta = 0, q leading d by 90 deg; the model's dq
  quantities are phase peaks),
* SVPWM phase-leg duty cycles as an average model (min-max zero-sequence
  injection, equivalent to SVPWM in the linear range; no switching ripple,
  dead time or device drop),
* dq phasor (vector) diagram, space-vector hexagon, power chain and
  constraint utilisation.

All numbers come from ``evaluate_point``; the views only re-express them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..models.components import DriveModel
from ..physics import ACTIVE, NOT_EVALUATED, VIOLATED, DriveKernel, OperatingPoint, evaluate_point
from ..scenario import Scenario

SQRT3 = math.sqrt(3.0)
TWO_PI_3 = 2.0 * math.pi / 3.0
PHASES = ("a", "b", "c")


@dataclass(frozen=True)
class PointView:
    kernel: DriveKernel
    point: OperatingPoint
    T_request_Nm: float | None = None
    label: str = ""

    @property
    def standstill(self) -> bool:
        return abs(self.point.omega_m) <= self.kernel.settings.speed_zero_tol_rad_s


def point_view(drive: DriveModel, scenario: Scenario, id_A: float, iq_A: float, T_request: float | None = None,
               label: str = "") -> PointView:
    k = DriveKernel(drive, scenario)
    return PointView(k, evaluate_point(k, id_A, iq_A), T_request, label)


def inverse_park(xd, xq, theta) -> np.ndarray:
    """Amplitude-invariant inverse Park; rows a, b, c (b lags a by 120 deg)."""
    th = np.asarray(theta, dtype=float)
    return np.stack([xd * np.cos(th - s) - xq * np.sin(th - s) for s in (0.0, TWO_PI_3, -TWO_PI_3)])


def command_voltage(pv: PointView) -> tuple[float, float]:
    """Modulator command (v_motor + dv_inv); equals the terminal voltage for the ideal mapping."""
    pt, r = pv.point, pv.kernel.R_drop
    return pt.vd_V + r * pt.id_A, pt.vq_V + r * pt.iq_A


def svpwm_duty(vc_abc: np.ndarray, Vdc: float) -> tuple[np.ndarray, np.ndarray]:
    """Average phase-leg duty cycles with min-max zero-sequence injection."""
    v0 = -0.5 * (vc_abc.max(axis=0) + vc_abc.min(axis=0))
    return 0.5 + (vc_abc + v0) / Vdc, v0


def waveforms(pv: PointView, periods: float = 2.0, samples: int = 1201) -> dict:
    pt, k = pv.point, pv.kernel
    if pv.standstill:
        theta = np.linspace(0.0, 2.0 * math.pi * periods, samples)
        x, x_kind, t = np.degrees(theta), "angle", None
    else:
        t = np.linspace(0.0, periods / pt.f_e_Hz, samples)
        theta = pt.omega_e * t                        # sign kept: reverse rotation gives the a-c-b sequence
        x, x_kind = t * 1e3, "time"
    i = inverse_park(pt.id_A, pt.iq_A, theta)
    v = inverse_park(pt.vd_V, pt.vq_V, theta)
    vcd, vcq = command_voltage(pv)
    vc = inverse_park(vcd, vcq, theta)
    psi = inverse_park(pt.psi_d_Wb, pt.psi_q_Wb, theta)
    v_ll = np.stack([v[0] - v[1], v[1] - v[2], v[2] - v[0]])
    p_inst = (v * i).sum(axis=0)
    duty, v0 = svpwm_duty(vc, pt.Vdc_V)
    r = k.reserve_fraction
    return {
        "x": x, "x_kind": x_kind, "t_s": t, "theta_rad": theta,
        "i_abc": i, "v_abc": v, "vcmd_abc": vc, "psi_abc": psi, "v_ll": v_ll, "p_inst_W": p_inst,
        "duty_abc": duty, "zero_sequence_V": v0,
        "i_peak_A": pt.i_peak_A, "i_rms_A": pt.i_phase_rms_A, "v_peak_V": pt.v_peak_V,
        "v_ll_peak_V": SQRT3 * pt.v_peak_V, "v_ll_rms_V": pt.v_LL_rms_V,
        "Pac_W": pt.Pac_W, "f_e_Hz": pt.f_e_Hz, "Vdc_V": pt.Vdc_V,
        "current_limit_A": k.Imax, "budget_V": k.Vb, "ceiling_V": k.V_ceiling,
        "m_linear": pt.v_cmd_peak_V / (pt.Vdc_V / SQRT3),      # 1.0 = end of the linear SVPWM range
        "m_sine": pt.v_cmd_peak_V / (pt.Vdc_V / 2.0),         # classic index, linear SVPWM up to 2/sqrt(3)
        "duty_reserve_band": (0.5 * r, 1.0 - 0.5 * r),
        "duty_extremes": (float(duty.min()), float(duty.max())),
        "reverse_rotation": pt.omega_e < 0,
        "standstill": pv.standstill,
        "model_note": "fundamental steady state; average PWM model (no switching ripple, dead time or device drop)",
    }


def _wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def phasor(pv: PointView) -> dict:
    pt, k = pv.point, pv.kernel
    we, rs = pt.omega_e, k.Rs
    vcd, vcq = command_voltage(pv)
    if k.kind == "constant_dq":
        psd0, psq0 = k.psi, 0.0
    else:
        a, b, ok = k.flux(0.0, 0.0)
        psd0, psq0 = (float(a), float(b)) if bool(ok) else (None, None)
    out = {
        "i": (pt.id_A, pt.iq_A),
        "psi": (pt.psi_d_Wb, pt.psi_q_Wb),
        "Rs_i": (rs * pt.id_A, rs * pt.iq_A),
        "jw_psi": (-we * pt.psi_q_Wb, we * pt.psi_d_Wb),
        "v": (pt.vd_V, pt.vq_V),
        "v_cmd": (vcd, vcq),
        "R_drop_ohm": k.R_drop,
        "e0": None if psd0 is None else (-we * psq0, we * psd0),
        "budget_V": k.Vb, "ceiling_V": k.V_ceiling,
        "angle_i_deg": math.degrees(math.atan2(pt.iq_A, pt.id_A)) if pt.i_peak_A > 0 else None,
        "angle_v_deg": math.degrees(math.atan2(pt.vq_V, pt.vd_V)) if pt.v_peak_V > 0 else None,
    }
    if out["angle_i_deg"] is not None and out["angle_v_deg"] is not None:
        phi = _wrap_deg(out["angle_v_deg"] - out["angle_i_deg"])
        out["phi_deg"] = phi
        out["power_factor"] = math.cos(math.radians(phi))
        out["power_factor_check"] = pt.Pac_W / (1.5 * pt.v_peak_V * pt.i_peak_A)
    else:
        out["phi_deg"] = out["power_factor"] = out["power_factor_check"] = None
    if k.kind == "constant_dq":
        c = 1.5 * k.p
        out["torque_split_Nm"] = {"magnet": c * k.psi * pt.iq_A, "reluctance": c * (k.Ld - k.Lq) * pt.id_A * pt.iq_A}
        # classic IPMSM voltage decomposition: v = e0 + j*we*Ld*id + (-we*Lq*iq) + Rs*i (+ R_drop*i for the command)
        out["chain"] = [("e0", (0.0, we * k.psi)), ("jwLd_id", (0.0, we * k.Ld * pt.id_A)),
                        ("wLq_iq", (-we * k.Lq * pt.iq_A, 0.0)), ("Rs_i", (rs * pt.id_A, rs * pt.iq_A))]
    else:
        out["torque_split_Nm"] = None
        out["chain"] = [("jw_psi", (-we * pt.psi_q_Wb, we * pt.psi_d_Wb)), ("Rs_i", (rs * pt.id_A, rs * pt.iq_A))]
    return out


def hexagon(pv: PointView, theta0_rad: float = 0.0) -> dict:
    pt, k = pv.point, pv.kernel
    vdc = pt.Vdc_V
    ang = np.radians(np.arange(0, 361, 60))
    vcd, vcq = command_voltage(pv)
    c, s = math.cos(theta0_rad), math.sin(theta0_rad)
    return {
        "hex_xy": (2.0 * vdc / 3.0 * np.cos(ang), 2.0 * vdc / 3.0 * np.sin(ang)),
        "vertex_labels": ("V1 (100)", "V2 (110)", "V3 (010)", "V4 (011)", "V5 (001)", "V6 (101)"),
        "linear_limit_V": vdc / SQRT3, "ceiling_V": k.V_ceiling, "budget_V": k.Vb,
        "command_V": pt.v_cmd_peak_V,
        "v_alpha_beta": (vcd * c - vcq * s, vcd * s + vcq * c),
        "i_alpha_beta": (pt.id_A * c - pt.iq_A * s, pt.id_A * s + pt.iq_A * c),
        "theta0_deg": math.degrees(theta0_rad),
    }


def power_chain(pt: OperatingPoint) -> list[dict] | None:
    """DC -> shaft chain; losses are subtracted in the direction DC -> shaft for both energy directions."""
    if pt.Pdc_W is None or pt.Pshaft_W is None:
        return None
    p_em = pt.Te_Nm * pt.omega_m
    return [
        {"key": "P_dc", "value_W": pt.Pdc_W, "kind": "level"},
        {"key": "P_inv", "value_W": -(pt.Pinv_W or 0.0), "kind": "loss"},
        {"key": "P_ac", "value_W": pt.Pac_W, "kind": "level"},
        {"key": "P_cu", "value_W": -pt.Pcu_W, "kind": "loss"},
        {"key": "P_em", "value_W": p_em, "kind": "level"},
        {"key": "P_rot", "value_W": -(pt.Prot_W or 0.0), "kind": "loss"},
        {"key": "P_shaft", "value_W": pt.Pshaft_W, "kind": "level"},
    ]


def constraint_rows(pt: OperatingPoint) -> list[dict]:
    rows = []
    for c in pt.constraints:
        util = None
        if c.limit not in (None, 0.0) and c.demand is not None:
            ratio = c.demand / c.limit
            util = max(ratio, 0.0)
        rows.append({"name": c.name, "group": c.group, "state": c.state, "demand": c.demand, "limit": c.limit,
                     "slack": c.slack, "unit": c.unit, "utilization": util, "kind": c.kind})
    return rows


def headline(pt: OperatingPoint) -> dict:
    """Compact numbers for a summary panel (full precision is in the record)."""
    active = [c.name for c in pt.constraints if c.state == ACTIVE]
    violated = [c.name for c in pt.constraints if c.state == VIOLATED]
    return {
        "id_A": pt.id_A, "iq_A": pt.iq_A, "i_peak_A": pt.i_peak_A, "i_rms_A": pt.i_phase_rms_A,
        "v_cmd_V": pt.v_cmd_peak_V, "v_budget_V": pt.voltage_budget_V, "v_margin_V": pt.voltage_margin_V,
        "Te_Nm": pt.Te_Nm, "Tshaft_Nm": pt.Tshaft_Nm, "Pshaft_W": pt.Pshaft_W, "Pdc_W": pt.Pdc_W, "Idc_A": pt.Idc_A,
        "efficiency": pt.efficiency, "energy_mode": pt.energy_mode, "f_e_Hz": pt.f_e_Hz,
        "active": active, "violated": violated,
        "not_evaluated": [c.name for c in pt.constraints if c.state == NOT_EVALUATED],
    }
