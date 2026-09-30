"""DC-link energy screening: active and passive discharge, regenerative overvoltage.

Capacitor energy E_C = 1/2 C V^2 is the common basis.

Active discharge through a resistor R (battery disconnected):
    V(t) = V0 exp(-t / (R C)),  t_reach = R C ln(V0 / Vf)
    to reach Vf within t:  R <= -t / (C ln(Vf / V0))
    I0 = V0 / R,  P0 = V0^2 / R,  E_R = 1/2 C (V0^2 - Vf^2)
A spinning PM motor behind an open inverter can rectify its back-EMF into the
DC link once the link voltage falls below the line-to-line back-EMF peak
sqrt(3)*omega_e*psi (independent review F08b: this is a *rectification risk*,
not a voltage floor independent of R).  The diode bridge can only charge the
link, so the RC time is a lower bound of the true time (a too-slow RC design
stays INFEASIBLE); whether the target is reached with the machine feeding the
link depends on the coupled source/load (machine impedance vs R) and is
UNKNOWN (COUPLED_MODEL_REQUIRED).  A fundamental-harmonic screening estimate
of the link voltage the machine would hold across R is reported for the
constant-parameter model (diode bridge as R_eq = pi^2/18 * R per phase).

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
from ..models.flux import ConstantFluxModel
from ..validation import finite as _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status
from ..units import shown

SQRT3 = math.sqrt(3.0)


def _pos(name, v):
    x = _finite(name, v)
    if x <= 0:
        raise InputValidationError("must be > 0", field=name)
    return x


def _flux_state(drive: DriveModel, magnet_temp_C: float | None):
    """(psi(0,0) or None, note) at the stated magnet temperature - never a silently chosen plane."""
    from ..physics import DriveKernel
    from ..scenario import DcSourceLimits, Scenario
    k = DriveKernel(drive, Scenario("emf", 0.0, 1.0, DcSourceLimits(), magnet_temp_C=magnet_temp_C))
    bad = [i for i in k.issues if "magnet" in i.message or "flux-map" in i.message or "psi" in i.message]
    if bad or not k.evaluable:
        return None, "; ".join(i.message for i in (bad or k.issues)) or "flux model not evaluable"
    if isinstance(drive.motor.flux, ConstantFluxModel):
        return k.psi, f"constant model psi_PM = {k.psi:.6g} Wb"
    psd, _, ok = k.plane.interpolate(0.0, 0.0)
    if not bool(ok):
        return None, "flux at zero current not covered by the map"
    return float(psd), k.notes[-1] if k.notes else "flux-map plane"


def pm_flux_at_zero_current(drive: DriveModel, magnet_temp_C: float | None = None) -> float | None:
    """psi_d(0, 0) at the stated magnet temperature (a multi-plane map needs the temperature)."""
    return _flux_state(drive, magnet_temp_C)[0]


def back_emf_ll_peak(drive: DriveModel, speed_rpm: float, magnet_temp_C: float | None = None) -> float | None:
    """Open-circuit fundamental line-to-line back-EMF peak sqrt(3)*omega_e*psi(0, 0)."""
    psi = pm_flux_at_zero_current(drive, magnet_temp_C)
    if psi is None:
        return None
    we = drive.motor.pole_pairs * 2 * math.pi * abs(speed_rpm) / 60.0
    return SQRT3 * we * psi


def speed_for_back_emf(drive: DriveModel, v_ll_peak: float, magnet_temp_C: float | None = None) -> float | None:
    psi = pm_flux_at_zero_current(drive, magnet_temp_C)
    if not psi:
        return None
    we = v_ll_peak / (SQRT3 * psi)
    return we / drive.motor.pole_pairs * 60 / (2 * math.pi)


def rectified_link_voltage_screening(drive: DriveModel, speed_rpm: float, R_ohm: float,
                                     magnet_temp_C: float | None = None) -> dict | None:
    """Fundamental-harmonic screening of the link voltage a spinning machine holds across R through the diodes.

    Diode bridge + R as a per-phase resistance R_eq = (pi^2/18) R (inductive, current-fed source); the
    steady dq equations with v = -R_eq * i give the ASC-like solution with Rs -> Rs + R_eq, and
    V_dc = (3 sqrt(3)/pi) * |v_phase_peak|.  Commutation overlap, saliency harmonics, diode drops and the
    speed decay are neglected: a screening estimate, not a bound.  Constant-parameter model only.
    """
    flux = drive.motor.flux
    if not isinstance(flux, ConstantFluxModel):
        return None
    psi = pm_flux_at_zero_current(drive, magnet_temp_C)
    if psi is None:
        return None
    we = drive.motor.pole_pairs * 2 * math.pi * abs(speed_rpm) / 60.0
    r_eq = math.pi ** 2 / 18.0 * R_ohm
    rt = drive.motor.Rs_ohm + r_eq
    den = rt * rt + we * we * flux.Ld_H * flux.Lq_H
    if den <= 0:
        return None
    iq = -we * psi * rt / den
    idd = -we * we * psi * flux.Lq_H / den
    i_pk = math.hypot(idd, iq)
    v_pk = r_eq * i_pk
    vdc = 3.0 * SQRT3 / math.pi * v_pk
    return {"V_dc_V": vdc, "I_dc_A": vdc / R_ohm, "P_W": vdc * vdc / R_ohm, "phase_current_A_peak": i_pk,
            "R_eq_per_phase_ohm": r_eq,
            "method": "fundamental-harmonic diode-bridge equivalent (R_eq = pi^2/18 R), constant-parameter machine; "
                      "screening estimate, not a bound"}


def _emf_assessment(drive, speed_rpm, Vf, R, magnet_temp_C, out, status, reasons, problems, ev):
    """Shared back-EMF handling for active/passive discharge (review F08b)."""
    psi, note = _flux_state(drive, magnet_temp_C)
    vll = None if psi is None else SQRT3 * drive.motor.pole_pairs * 2 * math.pi * abs(speed_rpm) / 60.0 * psi
    out["back_emf_ll_peak_V"] = vll
    out["back_emf_basis"] = note
    out["max_speed_for_target_rpm"] = None if not psi else speed_for_back_emf(drive, Vf, magnet_temp_C)
    if vll is None:
        if status is Status.FEASIBLE:
            status = Status.UNKNOWN
        reasons.append(Reason.MISSING_INPUT)
        problems.append(f"back-EMF unknown ({note})")
        return status
    if vll <= Vf:
        out["rectification_risk"] = False
        return status
    out["rectification_risk"] = True
    est = rectified_link_voltage_screening(drive, speed_rpm, R, magnet_temp_C)
    out["rectified_link_screening"] = est
    ev.append(Evidence.make(EvidenceKind.ANALYTIC_BOUND,
                            "open-inverter back-EMF sqrt(3)*omega_e*psi above Vf: the diodes can feed the link "
                            "(the RC time is a lower bound of the true time)"))
    msg = (f"rectification risk at {speed_rpm:g} rpm: line-line back-EMF peak {vll:.4g} V > {Vf:g} V, so the machine "
           f"can feed the link through the diodes below {vll:.4g} V; the discharge is slower than RC and whether "
           f"{Vf:g} V is reached depends on the machine impedance vs R (coupled source/load model required)")
    if est is not None:
        msg += (f"; screening estimate of the link voltage held across R: {est['V_dc_V']:.4g} V "
                f"({'above' if est['V_dc_V'] > Vf else 'below'} the target, not a bound)")
    problems.append(msg)
    if status is Status.FEASIBLE:
        status = Status.UNKNOWN
    reasons.append(Reason.COUPLED_MODEL_REQUIRED)
    return status


def active_discharge(C_F: float, V0_V: float, Vf_V: float, t_target_s: float, R_ohm: float | None = None,
                     resistor_peak_power_W: float | None = None, resistor_energy_J: float | None = None,
                     drive: DriveModel | None = None, speed_rpm: float | None = None,
                     magnet_temp_C: float | None = None) -> dict:
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
        problems = []
        status = _emf_assessment(drive, _finite("speed_rpm", speed_rpm), Vf, R, magnet_temp_C, out, status, reasons,
                                 problems, ev)
        if problems:
            detail += "; " + "; ".join(problems)
    claim = Claim("active_discharge", status, f"DC link {V0:g} V -> {Vf:g} V within {t:g} s", scope,
                  reasons=tuple(dict.fromkeys(reasons)), evidence=tuple(ev),
                  qualifiers=("screening: ideal RC, battery disconnected",), detail=detail)
    out["claim"] = claim.to_dict()
    return out


def passive_discharge(C_F: float, V0_V: float, Vf_V: float, t_target_s: float, R_ohm: float | None = None,
                      V_nom_V: float | None = None, V_max_V: float | None = None, P_allow_W: float | None = None,
                      active_R_ohm: float | None = None, drive: DriveModel | None = None,
                      speed_rpm: float | None = None, magnet_temp_C: float | None = None) -> dict:
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
        status = _emf_assessment(drive, _finite("speed_rpm", speed_rpm), Vf, R, magnet_temp_C, out, status, reasons,
                                 problems, ev)
    detail = (f"reaches {Vf:g} V in {t_reach:.4g} s (target {t:g} s); continuous loss {vnom * vnom / R:.4g} W at {vnom:g} V"
              + ("; " + "; ".join(problems) if problems else ""))
    claim = Claim("passive_discharge", status, f"DC link {V0:g} V -> {Vf:g} V within {t:g} s by the bleeder alone", scope,
                  reasons=tuple(dict.fromkeys(reasons)), evidence=tuple(ev),
                  qualifiers=("screening: ideal RC, battery disconnected, bleeder always connected",), detail=detail)
    out["claim"] = claim.to_dict()
    return out


REACTIONS = ("unspecified", "asc", "freewheel")


def regen_disconnect_overvoltage(C_F: float, V1_V: float, P_in_W: float, V_limit_V: float,
                                 reaction_time_s: float | None = None, profile: str = "constant",
                                 drive: DriveModel | None = None, speed_rpm: float | None = None,
                                 ramp_s: float | None = None, magnet_temp_C: float | None = None,
                                 reaction: str = "unspecified", P_in_upper_W: float | None = None) -> dict:
    """DC-link rise when the battery disconnects while regenerating P_in (W into the link).

    Profiles: ``constant`` (P_in until the reaction removes it at reaction_time_s),
    ``linear_ramp_down`` (a ramp from P_in to 0 that starts immediately - optimistic when a detection delay
    precedes the ramp), ``delay_then_ramp`` (constant P_in for reaction_time_s, then a ramp to 0 over ramp_s:
    E = P t_delay + P t_ramp / 2).  Negative times are invalid input.

    ``reaction`` (engineering review 2 of 63a2b61, F-09): what removes the inflow - ``asc`` (active short circuit:
    no DC power), ``freewheel`` (all switches open: the diodes rectify the back-EMF, which alone charges the isolated
    link towards the line-line peak) or ``unspecified``.  Above the uncontrolled-generation speed (line-line back-EMF
    peak > V_limit) freewheel is a proven violation and an unspecified reaction is not a pass.  ``P_in_upper_W``: the
    inflow at V_limit (with the I^2-form loss the regenerated power only grows with the link voltage: less
    field-weakening current, less loss), which turns the constant-inflow estimate into a bound."""
    C = _pos("C_F", C_F)
    V1 = _pos("V1_V", V1_V)
    P = _pos("P_in_W", P_in_W)
    Vlim = _pos("V_limit_V", V_limit_V)
    if Vlim <= V1:
        raise InputValidationError("voltage limit must exceed the initial link voltage", field="V_limit_V")
    if profile not in ("constant", "linear_ramp_down", "delay_then_ramp"):
        raise InputValidationError("profile must be 'constant', 'linear_ramp_down' or 'delay_then_ramp'",
                                   field="profile")
    if reaction not in REACTIONS:
        raise InputValidationError(f"reaction must be one of {REACTIONS}", field="reaction")
    P_up = None if P_in_upper_W is None else max(P, _pos("P_in_upper_W", P_in_upper_W))
    tramp = 0.0
    if profile == "delay_then_ramp":
        if ramp_s is None:
            raise InputValidationError("delay_then_ramp needs ramp_s", field="ramp_s")
        tramp = _finite("ramp_s", ramp_s)
        if tramp < 0:
            raise InputValidationError("ramp time must be >= 0", field="ramp_s")
    headroom_J = 0.5 * C * (Vlim ** 2 - V1 ** 2)
    t_ov_const = headroom_J / P
    if profile == "constant":
        t_allow = t_ov_const
    elif profile == "linear_ramp_down":
        t_allow = 2 * t_ov_const
    else:
        t_allow = t_ov_const - 0.5 * tramp          # allowed delay before the ramp starts
    ramp_alone_exceeds = t_allow < 0                # the ramp's own energy is more than the headroom
    out = {
        "C_F": C, "V1_V": V1, "P_in_W": P, "V_limit_V": Vlim, "profile": profile, "reaction": reaction,
        "energy_headroom_J": headroom_J,
        "dVdt_initial_V_per_s": P / (C * V1),
        "time_to_limit_constant_power_s": t_ov_const,
        "max_reaction_time_s": max(0.0, t_allow),
        **({"max_ramp_s": 2.0 * headroom_J / P} if profile == "delay_then_ramp" else {}),
        **({"P_in_upper_W": P_up} if P_up is not None else {}),
        "assumptions": ["lossless energy balance 1/2 C (V2^2 - V1^2) = E_in", "no other DC loads, ESR or stray L",
                        f"regenerated power profile: {profile} from P_in to 0 over the reaction time",
                        ("inflow bounded by its value at the voltage limit (it grows with the link voltage)"
                         if P_up is not None else "inflow held at its value at V1: an estimate (the regenerated "
                                                  "power grows with the link voltage)")],
    }
    scope = "capacitor energy-balance screening after battery disconnect"
    notes = []
    vll = None
    if drive is not None and speed_rpm is not None:
        vll = back_emf_ll_peak(drive, speed_rpm, magnet_temp_C)
        out["back_emf_ll_peak_V"] = vll
        if vll is not None and vll > Vlim:
            notes.append(f"if the inverter opens (freewheel) at {speed_rpm:g} rpm the rectified back-EMF "
                         f"({vll:.4g} V line-line peak) can drive the isolated link above {Vlim:g} V: freewheel is not "
                         f"a sufficient reaction by itself at this speed")
    ucg = vll is not None and vll > Vlim
    q = f"DC link stays below {Vlim:g} V after disconnect"
    if reaction == "asc":
        notes.append("reaction: active short circuit within the reaction time - its phase-current transient and braking "
                     "torque are separate checks (ASC page)")
    if ucg and reaction == "freewheel":
        claim = Claim("regen_overvoltage", Status.INFEASIBLE, q, scope, reasons=(Reason.NECESSARY_CONDITION_VIOLATED,),
                      evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND,
                                              f"line-line back-EMF peak {vll:.4g} V > {Vlim:g} V"),),
                      detail=f"freewheel at {speed_rpm:g} rpm: the diodes rectify the back-EMF, which alone charges the "
                             f"isolated link towards {vll:.4g} V (line-line peak) > {Vlim:g} V - whatever the reaction "
                             f"time")
        out["claim"] = claim.to_dict()
        out["notes"] = notes
        return out
    if reaction_time_s is None:
        if ramp_alone_exceeds:
            claim = Claim("regen_overvoltage", Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,),
                          evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND,
                                                  f"ramp energy P t_ramp / 2 = {0.5 * P * tramp:.4g} J > headroom "
                                                  f"{headroom_J:.4g} J"),),
                          detail=f"the ramp alone ({tramp * 1e3:.3g} ms) brings more energy than the headroom: no "
                                 f"delay is allowed and the ramp must be shorter than "
                                 f"{2e3 * headroom_J / P:.3g} ms")
        else:
            claim = Claim("regen_overvoltage", Status.UNKNOWN, q, scope, reasons=(Reason.MISSING_INPUT,),
                          evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND,
                                                  f"the regen power must be removed within {t_allow * 1e3:.3g} ms"),),
                          detail="reaction time not given: the result is the maximum allowed reaction time")
    else:
        tr = _finite("reaction_time_s", reaction_time_s)
        if tr < 0:
            raise InputValidationError("reaction time must be >= 0 (a negative time would inject negative energy)",
                                       field="reaction_time_s")

        def energy(p):
            return p * tr if profile == "constant" else (0.5 * p * tr if profile == "linear_ramp_down"
                                                         else p * tr + 0.5 * p * tramp)
        e_in = energy(P)
        v2 = math.sqrt(V1 ** 2 + 2 * e_in / C)
        v_hi = None if P_up is None else math.sqrt(V1 ** 2 + 2 * energy(P_up) / C)
        out["reaction_time_s"] = tr
        out["energy_in_J"] = e_in
        out["V_peak_V"] = v2                                   # estimate: inflow held at its value at V1
        if v_hi is not None:
            out["V_peak_bound_V"] = v_hi                       # bound: inflow at the voltage limit
        if v2 > Vlim:
            st, why = Status.INFEASIBLE, (Reason.CONSTRAINT_VIOLATION,)
        elif v_hi is not None and v_hi > Vlim:
            st, why = Status.UNKNOWN, (Reason.BOUND_INCONCLUSIVE,)
        else:
            st, why = Status.FEASIBLE, ()
        quals = ["screening: declared power profile and reaction time"]
        if v_hi is None and st is Status.FEASIBLE:
            quals.append("estimate for a constant inflow at V1: the regenerated power grows with the link voltage")
        if ucg and reaction == "unspecified" and st is not Status.INFEASIBLE:
            # above the UCG speed the pass holds only for a reaction that stops the inflow
            st, why = Status.UNKNOWN, (Reason.COUPLED_MODEL_REQUIRED,)
            quals.append(f"reaction path not stated: the peak holds only if the reaction stops the inflow (active short "
                         f"circuit); a freewheel reaction charges the link towards the {vll:.4g} V back-EMF peak")
        claim = Claim("regen_overvoltage", st, q, scope, reasons=why,
                      evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND,
                                              f"V_peak = sqrt(V1^2 + 2 E_in / C) = {shown(v2, 'voltage')} V"
                                              + ("" if v_hi is None else f" (bound {shown(v_hi, 'voltage')} V)")),),
                      qualifiers=tuple(quals),
                      detail=f"peak {shown(v2, 'voltage')} V" + (
                          "" if v_hi is None else f" (bound {shown(v_hi, 'voltage')} V with the inflow at {Vlim:g} V)")
                             + f" vs limit {Vlim:g} V (allowed reaction {max(0.0, t_allow) * 1e3:.3g} ms)")
    out["claim"] = claim.to_dict()
    out["notes"] = notes
    return out
