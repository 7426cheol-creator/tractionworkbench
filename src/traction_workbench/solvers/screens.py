"""Necessary-condition screens.

Each screen is an independent analytic argument.  A violated screen proves
infeasibility regardless of the optimiser, the control policy (and, where
stated, the magnetic model); a passed screen proves nothing.  Screens are
reported as evidence next to the solver result, never instead of it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..physics import DriveKernel


@dataclass(frozen=True)
class ScreenResult:
    name: str
    applicable: bool
    violated: bool
    statement: str
    scope: str
    values: tuple = ()

    def to_dict(self) -> dict:
        return {"name": self.name, "applicable": self.applicable, "violated": self.violated,
                "statement": self.statement, "scope": self.scope, "values": dict(self.values)}


def shaft_power_vs_discharge(k: DriveKernel, T_shaft: float) -> ScreenResult:
    """P_dc = P_shaft + passive losses >= P_shaft: motoring beyond the discharge cap is impossible."""
    name = "shaft_power_exceeds_discharge_cap"
    cap = k.P_dis_eff
    if cap is None:
        return ScreenResult(name, False, False, "discharge limits not declared", "")
    p = T_shaft * k.omega_m
    tol = max(k.settings.power_abs_tol_W, k.settings.constraint_rel_tol * abs(cap))
    violated = p > cap + tol
    return ScreenResult(
        name, True, violated,
        (f"requested shaft power {p:.6g} W exceeds the effective discharge cap {cap:.6g} W even with zero losses"
         if violated else f"requested shaft power {p:.6g} W does not exceed the discharge cap {cap:.6g} W"),
        "independent of motor model, control policy and loss values (losses are passive)",
        (("P_shaft_W", p), ("effective_discharge_cap_W", cap),
         ("discharge_power_max_W", k.limits.discharge_power_max_W),
         ("Vdc_times_Idis_W", None if k.limits.discharge_current_max_A is None else k.Vdc * k.limits.discharge_current_max_A)),
    )


def regen_max_loss_vs_charge(k: DriveKernel, T_shaft: float) -> ScreenResult:
    """P_dc <= P_shaft + L_max: if even the largest possible loss cannot lift P_dc to -P_chg, regen is impossible."""
    name = "charge_cap_unreachable_even_with_maximum_loss"
    cap = k.P_chg_eff
    if cap is None or k.P_rot is None or not k.has_inverter_loss:
        return ScreenResult(name, False, False, "charge limit or loss models not declared", "")
    if k.i2_dc is None:
        return ScreenResult(name, False, False, f"the {k.loss_label} has no closed-form maximum over the current "
                                                "disk: screen not applied", "")
    p = T_shaft * k.omega_m
    lmax = k.P_rot + k.i2_dc.a0_W + k.i2_dc.c2_W_per_A2 * k.Imax ** 2
    pmax = p + lmax
    tol = max(k.settings.power_abs_tol_W, k.settings.constraint_rel_tol * abs(cap))
    violated = pmax < -cap - tol
    return ScreenResult(
        name, True, violated,
        (f"even with the maximum loss at |i| = {k.Imax:g} A the least negative P_dc is {pmax:.6g} W < {-cap:.6g} W"
         if violated else f"maximum-loss bound P_dc <= {pmax:.6g} W does not exclude the charge cap {-cap:.6g} W"),
        "independent of control policy and magnetic model; uses the declared loss models and current limit",
        (("P_shaft_W", p), ("max_total_loss_W", lmax), ("max_possible_Pdc_W", pmax), ("charge_cap_W", -cap)),
    )


def em_torque_vs_current_limit(k: DriveKernel, T_shaft: float) -> ScreenResult:
    """Constant model: |T_em| cannot exceed the MTPA torque at the current limit."""
    name = "torque_exceeds_current_limited_maximum"
    if k.kind != "constant_dq" or k.tau_rot is None:
        return ScreenResult(name, False, False, "constant-parameter model and rotational loss required", "")
    psi, dl, imax, p = k.psi, k.Ld - k.Lq, k.Imax, k.p
    if dl == 0:
        tmax = 1.5 * p * psi * imax
    else:
        # MTPA at |i| = Imax without voltage/domain restriction (so an upper bound):
        # d/d(id) of (psi + dl*id)*sqrt(Imax^2 - id^2) = 0  <=>  2*dl*id^2 + psi*id - dl*Imax^2 = 0.
        # Both roots and id = 0 are evaluated; the largest |T_em| is kept.
        cands = []
        disc = math.sqrt(psi * psi + 8.0 * dl * dl * imax * imax)
        for d in ((-psi + disc) / (4.0 * dl), (-psi - disc) / (4.0 * dl), 0.0):
            if abs(d) <= imax:
                q = math.sqrt(max(imax * imax - d * d, 0.0))
                cands.append(abs(1.5 * p * (psi + dl * d) * q))
        tmax = max(cands)
    tem = abs(T_shaft + k.tau_rot)
    tol = max(k.settings.torque_abs_tol_Nm, k.settings.constraint_rel_tol * tmax)
    violated = tem > tmax + tol
    return ScreenResult(
        name, True, violated,
        (f"required |T_em| {tem:.6g} N*m exceeds the maximum {tmax:.6g} N*m obtainable at |i| = {imax:g} A"
         if violated else f"required |T_em| {tem:.6g} N*m is within the current-limited maximum {tmax:.6g} N*m"),
        "constant-parameter model; ignores voltage and declared id range (so it is only a necessary condition)",
        (("required_abs_Tem_Nm", tem), ("max_abs_Tem_at_Imax_Nm", tmax)),
    )


def d_axis_voltage_bound(k: DriveKernel, T_shaft: float) -> ScreenResult:
    """Constant model, the reference-case argument for I03 generalised.

    In the declared id range k(id) = psi + (Ld-Lq)*id <= k_max, so |iq| >= |a|/k_max.
    When R*id and -omega_e*Lq*iq cannot have opposite signs on the domain,
    |v_d| >= |omega_e|*Lq*|iq|_min, and |v| >= |v_d|.
    """
    name = "d_axis_voltage_exceeds_budget"
    if k.kind != "constant_dq" or k.tau_rot is None:
        return ScreenResult(name, False, False, "constant-parameter model and rotational loss required", "")
    d_lo, d_hi = k.domain.id_A
    psi, dl = k.psi, k.Ld - k.Lq
    kvals = [psi + dl * d_lo, psi + dl * d_hi]
    if min(kvals) <= 0:
        return ScreenResult(name, False, False, "k(id) changes sign on the declared id range", "")
    a = (T_shaft + k.tau_rot) / (1.5 * k.p)
    if a == 0:
        return ScreenResult(name, False, False, "zero electromagnetic torque", "")
    iq_min = abs(a) / max(kvals)
    rv = k.Rs + k.R_drop
    we = k.omega_e
    sign_q = 1.0 if a > 0 else -1.0
    # sign of -we*Lq*iq is -sign(we)*sign_q; sign of rv*id is sign(id) (id range)
    s_q = -math.copysign(1.0, we) * sign_q if we != 0 else 0.0
    same_sign = (s_q <= 0 and d_hi <= 0) or (s_q >= 0 and d_lo >= 0) or rv == 0
    if not same_sign:
        return ScreenResult(name, False, False, "sign condition of the d-axis bound not met", "")
    vd_lb = abs(we) * k.Lq * iq_min
    tol = max(k.settings.voltage_abs_tol_V, k.settings.constraint_rel_tol * k.Vb)
    violated = vd_lb > k.Vb + tol
    return ScreenResult(
        name, True, violated,
        (f"|v_d| >= omega_e*Lq*iq_min = {vd_lb:.10g} V exceeds the command budget {k.Vb:.10g} V "
         f"(iq_min = {iq_min:.10g} A from the declared id range)"
         if violated else f"d-axis bound {vd_lb:.6g} V does not exceed the budget {k.Vb:.6g} V"),
        "constant-parameter model inside the declared id range only (not a general statement about the motor)",
        (("iq_min_A", iq_min), ("abs_vd_lower_bound_V", vd_lb), ("voltage_budget_V", k.Vb),
         ("k_max_Wb", max(kvals))),
    )


def run_screens(k: DriveKernel, T_shaft: float) -> tuple[ScreenResult, ...]:
    out = []
    for fn in (shaft_power_vs_discharge, regen_max_loss_vs_charge, em_torque_vs_current_limit, d_axis_voltage_bound):
        r = fn(k, T_shaft)
        if r.applicable:
            out.append(r)
    return tuple(out)
