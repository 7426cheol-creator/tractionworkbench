"""DC-link energy screening: active and passive discharge, regenerative overvoltage.

Capacitor energy E_C = 1/2 C V^2 is the common basis.

Active discharge through a resistor R (battery disconnected):
    V(t) = V0 exp(-t / (R C)),  t_reach = R C ln(V0 / Vf)
    to reach Vf within t:  R <= -t / (C ln(Vf / V0))
    I0 = V0 / R,  P0 = V0^2 / R,  E_R = 1/2 C (V0^2 - Vf^2)
A spinning PM motor behind an open inverter rectifies its back-EMF into the
DC link: while the line-to-line back-EMF peak sqrt(3)*omega_e*psi exceeds Vf
the target cannot be reached, whatever R is.

Passive discharge through a bleeder R_p permanently across the link (the
backup when the active discharge is unavailable):
    V(t) = V0 exp(-t / (R_p C)),  t_reach = R_p C ln(V0 / Vf)
    continuous loss while the contactors are closed:  P_cont = V^2 / R_p
    trade-off:  P_cont * t_reach = C V^2 ln(V0 / Vf)   (independent of R_p)
    design window:  V_max^2 / P_allow  <=  R_p  <=  t_target / (C ln(V0 / Vf))
With an active resistor R_a switched in parallel the time constant is
(R_a || R_p) C.

Battery disconnect during regeneration (power P into the link, no sink):
    1/2 C (V2^2 - V1^2) = E_in,  t_ov = C (V_lim^2 - V1^2) / (2 P)
with E_in = P t_r (constant power until the reaction removes it) or
P t_r / 2 (linear ramp-down).

Screening assumptions: ideal capacitor (no ESR, no stray inductance), no other
DC loads or sources, lossless energy transfer, the declared power profile and
reaction time.  Not a transient circuit simulation or device SOA check.
"""

from __future__ import annotations

import math

from ..errors import InputValidationError
from ..models.components import DriveModel
from ..models.flux import ConstantFluxModel, _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

SQRT3 = math.sqrt(3.0)


def _pos(name, v):
    x = _finite(name, v)
    if x <= 0:
        raise InputValidationError("must be > 0", field=name)
    return x


def pm_flux_at_zero_current(drive: DriveModel) -> float | None:
    flux = drive.motor.flux
    if isinstance(flux, ConstantFluxModel):
        return flux.psi_pm_Wb
    try:
        plane = flux.planes[0]
        psd, _, ok = plane.interpolate(0.0, 0.0)
        return float(psd) if bool(ok) else None
    except Exception:  # pragma: no cover - defensive
        return None


def back_emf_ll_peak(drive: DriveModel, speed_rpm: float) -> float | None:
    """Open-circuit fundamental line-to-line back-EMF peak sqrt(3)*omega_e*psi(0, 0)."""
    psi = pm_flux_at_zero_current(drive)
    if psi is None:
        return None
    we = drive.motor.pole_pairs * 2 * math.pi * abs(speed_rpm) / 60.0
    return SQRT3 * we * psi


def speed_for_back_emf(drive: DriveModel, v_ll_peak: float) -> float | None:
    psi = pm_flux_at_zero_current(drive)
    if not psi:
        return None
    we = v_ll_peak / (SQRT3 * psi)
    return we / drive.motor.pole_pairs * 60 / (2 * math.pi)


def active_discharge(C_F: float, V0_V: float, Vf_V: float, t_target_s: float, R_ohm: float | None = None,
                     resistor_peak_power_W: float | None = None, resistor_energy_J: float | None = None,
                     drive: DriveModel | None = None, speed_rpm: float | None = None) -> dict:
    C = _pos("C_F", C_F)
    V0 = _pos("V0_V", V0_V)
    Vf = _pos("Vf_V", Vf_V)
    t = _pos("t_target_s", t_target_s)
    if Vf >= V0:
        raise InputValidationError("target voltage must be below the initial voltage", field="Vf_V")
    r_max = -t / (C * math.log(Vf / V0))
    R = r_max if R_ohm is None else _pos("R_ohm", R_ohm)
    t_reach = R * C * math.log(V0 / Vf)
    out = {
        "C_F": C, "V0_V": V0, "Vf_V": Vf, "t_target_s": t,
        "stored_energy_J": 0.5 * C * V0 * V0,
        "R_max_ohm": r_max,
        "R_used_ohm": R,
        "tau_s": R * C,
        "t_reach_s": t_reach,
        "I0_A": V0 / R,
        "P0_W": V0 * V0 / R,
        "E_R_J": 0.5 * C * (V0 * V0 - Vf * Vf),
        "assumptions": ["battery disconnected", "ideal capacitor (no ESR)", "constant resistance",
                        "no other DC loads or sources", "motor back-EMF below Vf (checked if speed given)"],
    }
    scope = "reduced-order RC discharge screening"
    ev = [Evidence.make(EvidenceKind.ANALYTIC_BOUND, f"t_reach = R C ln(V0/Vf) = {t_reach:.6g} s with R = {R:.6g} ohm")]
    reasons = []
    status = Status.FEASIBLE if t_reach <= t * (1 + 1e-12) else Status.INFEASIBLE
    detail = (f"reaches {Vf:g} V in {t_reach:.4g} s (target {t:g} s)" if status is Status.FEASIBLE
              else f"needs {t_reach:.4g} s > {t:g} s: R must be <= {r_max:.6g} ohm")
    if status is Status.INFEASIBLE:
        reasons.append(Reason.CONSTRAINT_VIOLATION)
    stress = []
    if resistor_peak_power_W is not None and out["P0_W"] > resistor_peak_power_W:
        stress.append(f"initial power {out['P0_W']:.4g} W exceeds the resistor peak rating {resistor_peak_power_W:g} W")
    if resistor_energy_J is not None and out["E_R_J"] > resistor_energy_J:
        stress.append(f"pulse energy {out['E_R_J']:.4g} J exceeds the resistor energy rating {resistor_energy_J:g} J")
    if stress:
        status = Status.INFEASIBLE
        reasons.append(Reason.CONSTRAINT_VIOLATION)
        detail += "; " + "; ".join(stress)
    if drive is not None and speed_rpm is not None:
        vll = back_emf_ll_peak(drive, speed_rpm)
        out["back_emf_ll_peak_V"] = vll
        out["max_speed_for_target_rpm"] = speed_for_back_emf(drive, Vf)
        if vll is None:
            status, detail = Status.UNKNOWN, detail + "; back-EMF unknown (flux at zero current not covered)"
            reasons.append(Reason.MISSING_INPUT)
        elif vll > Vf:
            status = Status.INFEASIBLE
            reasons.append(Reason.NECESSARY_CONDITION_VIOLATED)
            detail += (f"; at {speed_rpm:g} rpm the rectified back-EMF ({vll:.4g} V line-line peak) keeps the link "
                       f"above {Vf:g} V - discharge cannot finish until |n| < {out['max_speed_for_target_rpm']:.5g} rpm")
            ev.append(Evidence.make(EvidenceKind.ANALYTIC_BOUND, "open-inverter back-EMF sqrt(3)*omega_e*psi vs Vf"))
    claim = Claim("active_discharge", status, f"DC link {V0:g} V -> {Vf:g} V within {t:g} s", scope,
                  reasons=tuple(dict.fromkeys(reasons)), evidence=tuple(ev),
                  qualifiers=("screening: ideal RC, battery disconnected",), detail=detail)
    out["claim"] = claim.to_dict()
    return out


def passive_discharge(C_F: float, V0_V: float, Vf_V: float, t_target_s: float, R_ohm: float | None = None,
                      V_nom_V: float | None = None, V_max_V: float | None = None, P_allow_W: float | None = None,
                      active_R_ohm: float | None = None, drive: DriveModel | None = None,
                      speed_rpm: float | None = None) -> dict:
    """Bleeder resistor permanently across the DC link: discharge time vs continuous loss."""
    C = _pos("C_F", C_F)
    V0 = _pos("V0_V", V0_V)
    Vf = _pos("Vf_V", Vf_V)
    t = _pos("t_target_s", t_target_s)
    if Vf >= V0:
        raise InputValidationError("target voltage must be below the initial voltage", field="Vf_V")
    ln = math.log(V0 / Vf)
    r_max = t / (C * ln)
    R = r_max if R_ohm is None else _pos("R_ohm", R_ohm)
    vnom = V0 if V_nom_V is None else _pos("V_nom_V", V_nom_V)
    vmax = max(V0, vnom) if V_max_V is None else _pos("V_max_V", V_max_V)
    tau = R * C
    t_reach = tau * ln
    out = {
        "C_F": C, "V0_V": V0, "Vf_V": Vf, "t_target_s": t,
        "R_max_ohm": r_max, "R_used_ohm": R, "tau_s": tau, "t_reach_s": t_reach,
        "V_nom_V": vnom, "V_max_V": vmax,
        "P_cont_nom_W": vnom * vnom / R, "P_cont_max_W": vmax * vmax / R,
        "I_cont_nom_A": vnom / R,
        "E_R_J": 0.5 * C * (V0 * V0 - Vf * Vf),
        "loss_time_product_Ws": C * vnom * vnom * ln,
        "P_allow_W": P_allow_W,
        "R_min_ohm": None if P_allow_W is None else vmax * vmax / _pos("P_allow_W", P_allow_W),
        "assumptions": ["bleeder permanently across the DC link (no switch)", "battery disconnected during the discharge",
                        "ideal capacitor (no ESR)", "constant resistance (no temperature coefficient)",
                        "continuous loss P = V^2/R_p whenever the link is energised (contactors closed)",
                        "resistor derating, voltage rating of the resistor chain and creepage are not checked"],
    }
    scope = "reduced-order RC passive discharge screening"
    ev = [Evidence.make(EvidenceKind.ANALYTIC_BOUND, f"t_reach = R_p C ln(V0/Vf) = {t_reach:.6g} s with R_p = {R:.6g} ohm; "
                                                     f"P_cont = V^2/R_p = {vnom * vnom / R:.4g} W at {vnom:g} V")]
    reasons = []
    problems = []
    status = Status.FEASIBLE
    if t_reach > t * (1 + 1e-12):
        status = Status.INFEASIBLE
        reasons.append(Reason.CONSTRAINT_VIOLATION)
        problems.append(f"needs {t_reach:.4g} s > {t:g} s: R_p must be <= {r_max:.6g} ohm")
    if P_allow_W is not None:
        window_ok = out["R_min_ohm"] <= r_max * (1 + 1e-12)
        out["window_ohm"] = [out["R_min_ohm"], r_max] if window_ok else None
        if out["P_cont_max_W"] > P_allow_W * (1 + 1e-12):
            status = Status.INFEASIBLE
            reasons.append(Reason.CONSTRAINT_VIOLATION)
            problems.append(f"continuous loss {out['P_cont_max_W']:.4g} W at {vmax:g} V exceeds the allowed "
                            f"{P_allow_W:g} W: R_p must be >= {out['R_min_ohm']:.6g} ohm")
        if not window_ok:
            problems.append(f"no passive-only design: the loss limit needs R_p >= {out['R_min_ohm']:.6g} ohm but the time "
                            f"needs R_p <= {r_max:.6g} ohm (active discharge or a lower loss-time demand required)")
    if active_R_ohm is not None:
        ra = _pos("active_R_ohm", active_R_ohm)
        r_par = ra * R / (ra + R)
        out["with_active"] = {"R_active_ohm": ra, "R_parallel_ohm": r_par, "tau_s": r_par * C, "t_reach_s": r_par * C * ln}
    if drive is not None and speed_rpm is not None:
        vll = back_emf_ll_peak(drive, speed_rpm)
        out["back_emf_ll_peak_V"] = vll
        out["max_speed_for_target_rpm"] = speed_for_back_emf(drive, Vf)
        if vll is None:
            if status is Status.FEASIBLE:
                status = Status.UNKNOWN
            reasons.append(Reason.MISSING_INPUT)
            problems.append("back-EMF unknown (flux at zero current not covered)")
        elif vll > Vf:
            status = Status.INFEASIBLE
            reasons.append(Reason.NECESSARY_CONDITION_VIOLATED)
            problems.append(f"at {speed_rpm:g} rpm the rectified back-EMF ({vll:.4g} V line-line peak) keeps the link "
                            f"above {Vf:g} V until |n| < {out['max_speed_for_target_rpm']:.5g} rpm")
            ev.append(Evidence.make(EvidenceKind.ANALYTIC_BOUND, "open-inverter back-EMF sqrt(3)*omega_e*psi vs Vf"))
    detail = (f"reaches {Vf:g} V in {t_reach:.4g} s (target {t:g} s); continuous loss {vnom * vnom / R:.4g} W at {vnom:g} V"
              + ("; " + "; ".join(problems) if problems else ""))
    claim = Claim("passive_discharge", status, f"DC link {V0:g} V -> {Vf:g} V within {t:g} s by the bleeder alone", scope,
                  reasons=tuple(dict.fromkeys(reasons)), evidence=tuple(ev),
                  qualifiers=("screening: ideal RC, battery disconnected, bleeder always connected",), detail=detail)
    out["claim"] = claim.to_dict()
    return out


def regen_disconnect_overvoltage(C_F: float, V1_V: float, P_in_W: float, V_limit_V: float,
                                 reaction_time_s: float | None = None, profile: str = "constant",
                                 drive: DriveModel | None = None, speed_rpm: float | None = None) -> dict:
    """DC-link rise when the battery disconnects while regenerating P_in (W into the link)."""
    C = _pos("C_F", C_F)
    V1 = _pos("V1_V", V1_V)
    P = _pos("P_in_W", P_in_W)
    Vlim = _pos("V_limit_V", V_limit_V)
    if Vlim <= V1:
        raise InputValidationError("voltage limit must exceed the initial link voltage", field="V_limit_V")
    if profile not in ("constant", "linear_ramp_down"):
        raise InputValidationError("profile must be 'constant' or 'linear_ramp_down'", field="profile")
    headroom_J = 0.5 * C * (Vlim ** 2 - V1 ** 2)
    t_ov_const = headroom_J / P
    t_allow = t_ov_const if profile == "constant" else 2 * t_ov_const
    out = {
        "C_F": C, "V1_V": V1, "P_in_W": P, "V_limit_V": Vlim, "profile": profile,
        "energy_headroom_J": headroom_J,
        "dVdt_initial_V_per_s": P / (C * V1),
        "time_to_limit_constant_power_s": t_ov_const,
        "max_reaction_time_s": t_allow,
        "assumptions": ["lossless energy balance 1/2 C (V2^2 - V1^2) = E_in", "no other DC loads, ESR or stray L",
                        f"regenerated power profile: {profile} from P_in to 0 over the reaction time"],
    }
    scope = "capacitor energy-balance screening after battery disconnect"
    notes = []
    if drive is not None and speed_rpm is not None:
        vll = back_emf_ll_peak(drive, speed_rpm)
        out["back_emf_ll_peak_V"] = vll
        if vll is not None and vll > Vlim:
            notes.append(f"if the inverter opens (freewheel) at {speed_rpm:g} rpm the rectified back-EMF "
                         f"({vll:.4g} V line-line peak) can drive the isolated link above {Vlim:g} V: freewheel is not "
                         f"a sufficient reaction by itself at this speed")
    if reaction_time_s is None:
        claim = Claim("regen_overvoltage", Status.UNKNOWN, f"DC link stays below {Vlim:g} V after disconnect", scope,
                      reasons=(Reason.MISSING_INPUT,),
                      evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND,
                                              f"the regen power must be removed within {t_allow * 1e3:.4g} ms"),),
                      detail="reaction time not given: the result is the maximum allowed reaction time")
    else:
        tr = _finite("reaction_time_s", reaction_time_s)
        e_in = P * tr if profile == "constant" else 0.5 * P * tr
        v2 = math.sqrt(V1 ** 2 + 2 * e_in / C)
        out["reaction_time_s"] = tr
        out["energy_in_J"] = e_in
        out["V_peak_V"] = v2
        st = Status.FEASIBLE if v2 <= Vlim else Status.INFEASIBLE
        claim = Claim("regen_overvoltage", st, f"DC link stays below {Vlim:g} V after disconnect", scope,
                      reasons=() if st is Status.FEASIBLE else (Reason.CONSTRAINT_VIOLATION,),
                      evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND,
                                              f"V_peak = sqrt(V1^2 + 2 E_in / C) = {v2:.6g} V"),),
                      qualifiers=("screening: declared power profile and reaction time",),
                      detail=f"peak {v2:.5g} V vs limit {Vlim:g} V (allowed reaction {t_allow * 1e3:.4g} ms)")
    out["claim"] = claim.to_dict()
    out["notes"] = notes
    return out
