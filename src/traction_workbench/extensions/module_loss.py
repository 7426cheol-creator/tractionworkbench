"""Datasheet-based power-module losses for a 2-level, 3-phase inverter (independent review, section 8.8).

Per switch position j over one fundamental period (average PWM model):

    P_cond,j = (1/2pi) * integral over the conduction set of  v_on,j(i, Tj) * |i| * (time fraction) dtheta
    P_sw,j   = f_sw * (1/2pi) * integral over the switching set of  E_j(|i|, V, Tj) dtheta

* phase current i(theta) = I_pk cos(theta - phi); the leg duty follows the phase reference and the declared
  zero-sequence (SVPWM min-max, SPWM none, DPWM1 60-degree clamping of the largest phase);
* i > 0 (out of the leg): upper switch conducts d, the lower path conducts (1 - d); i < 0 mirrored;
* IGBT: the reverse current flows in the anti-parallel diode (V_F curve), never in the IGBT channel;
  SiC MOSFET: gated-on reverse current flows in the channel (synchronous rectification, own curve or the
  declared symmetric channel), the body diode conducts only during the dead times (2 t_d f_sw per period);
  the same current is never assigned to both paths;
* switching energies are interpolated in current and temperature from the tables at the datasheet test
  voltage; a different DC voltage is used only with a declared scaling law and its basis/validity - otherwise
  the switching loss is NOT ESTABLISHED (never silently scaled with E ~ V);
* ``energy_basis='per_device'``: E_on/E_off belong to the switch, E_rr to the recovering diode (added
  separately); ``'commutation_pair_total'``: the tabulated energy already covers the commutation pair, so no
  E_rr is added on top (no double counting of recovery / C_oss energy);
* tables are interpolated, never extrapolated: a current or temperature outside the data gives an explicit
  'not established' result instead of a number.

Losses are the positive dissipated power in either power direction (regeneration moves conduction from the
switches to the diodes, it never makes a loss negative).  At standstill the phase currents are DC; the hottest
device is found over the electrical angle - the module total divided by six is not a junction-temperature
input.  Ripple current, dead-time voltage error, minimum pulses and ringing are not modelled (reported).
Datasheet typical values are not guaranteed upper bounds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..errors import InputValidationError
from ..models.flux import _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class Table2D:
    """value(I, Tj): one current curve per junction temperature; linear interpolation, no extrapolation."""

    temps_C: tuple
    currents_A: tuple
    values: tuple            # values[k][n] at temps_C[k], currents_A[n]
    unit: str
    source: str = ""         # datasheet revision / page / figure / file hash

    def __post_init__(self):
        t = tuple(_finite("temps_C", x) for x in self.temps_C)
        c = tuple(_finite("currents_A", x) for x in self.currents_A)
        if not t or any(b <= a for a, b in zip(t, t[1:])):
            raise InputValidationError("table temperatures must be strictly increasing", field="temps_C")
        if len(c) < 2 or any(b <= a for a, b in zip(c, c[1:])) or c[0] < 0:
            raise InputValidationError("table currents must be >= 0 and strictly increasing (>= 2 points)",
                                       field="currents_A")
        v = np.array(self.values, dtype=float)
        if v.shape != (len(t), len(c)) or not np.all(np.isfinite(v)):
            raise InputValidationError(f"table values must be finite with shape {(len(t), len(c))}", field="values")
        if self.unit not in ("V", "mJ", "uJ", "J"):
            raise InputValidationError("table unit must be V, mJ, uJ or J", field="unit")
        object.__setattr__(self, "temps_C", t)
        object.__setattr__(self, "currents_A", c)
        object.__setattr__(self, "values", tuple(tuple(float(x) for x in row) for row in v))

    @property
    def scale(self) -> float:
        return {"V": 1.0, "J": 1.0, "mJ": 1e-3, "uJ": 1e-6}[self.unit]

    def covers(self, i_max: float, T: float) -> tuple[bool, str]:
        if i_max > self.currents_A[-1] * (1 + 1e-12):
            return False, f"current {i_max:.4g} A above the table ({self.currents_A[-1]:g} A): no extrapolation"
        t = self.temps_C
        if len(t) == 1:
            if abs(T - t[0]) > 1e-6:
                return False, f"table given only at {t[0]:g} degC, requested {T:g} degC"
        elif not (t[0] - 1e-9 <= T <= t[-1] + 1e-9):
            return False, f"temperature {T:g} degC outside the table range [{t[0]:g}, {t[-1]:g}] degC"
        return True, ""

    def eval(self, i: np.ndarray, T: float) -> np.ndarray:
        """SI value (V or J) at |i| and T (caller checked coverage)."""
        a = np.abs(np.asarray(i, dtype=float))
        c = np.array(self.currents_A)
        v = np.array(self.values)
        t = self.temps_C
        if len(t) == 1:
            row = v[0]
        else:
            k = int(np.clip(np.searchsorted(t, T, side="right") - 1, 0, len(t) - 2))
            w = (T - t[k]) / (t[k + 1] - t[k])
            row = (1 - w) * v[k] + w * v[k + 1]
        if c[0] > 0:        # below the first tabulated current the curve is not given
            out = np.where(a < c[0], np.nan, np.interp(a, c, row))
        else:
            out = np.interp(a, c, row)
        return out * self.scale

    def describe(self) -> dict:
        return {"temps_C": list(self.temps_C), "currents_A": [self.currents_A[0], self.currents_A[-1],
                                                             len(self.currents_A)],
                "unit": self.unit, "source": self.source}


def linear_table(v0: float, r: float, i_max: float, temps_C=(25.0,), unit="V", source="") -> Table2D:
    """Convenience: v = v0 + r*i (or E = v0 + r*i) on [0, i_max] at the given temperatures (same curve)."""
    cur = (0.0, float(i_max))
    return Table2D(tuple(temps_C), cur, tuple((v0, v0 + r * i_max) for _ in temps_C), unit, source)


@dataclass(frozen=True)
class SwitchDevice:
    technology: str                        # "IGBT" or "SiC_MOSFET"
    v_on: Table2D                          # IGBT V_CE(I, Tj); MOSFET V_DS(I, Tj) first quadrant  [V]
    v_rev: Table2D                         # IGBT diode V_F; MOSFET body diode V_SD (gate off)   [V]
    e_on: Table2D                          # turn-on energy at the test voltage
    e_off: Table2D                         # turn-off energy at the test voltage
    e_rr: Table2D | None = None            # reverse-recovery energy of the diode / body diode
    v_channel_rev: Table2D | None = None   # MOSFET third-quadrant channel with gate on (sync. rectification)
    energy_basis: str = "per_device"       # "per_device" | "commutation_pair_total"
    v_test_V: float = 600.0
    vdc_scaling_exponent: float | None = None
    vdc_scaling_basis: str = ""
    vdc_scaling_valid_V: tuple | None = None
    value_kind: str = "typical"            # "typical" | "max"  (typical is not a guaranteed bound)
    test_conditions: tuple = ()            # (("Rg_on_ohm", 2.0), ("Vge_V", 15.0), ("deadtime_s", 1e-6), ...)
    source: str = ""

    def __post_init__(self):
        if self.technology not in ("IGBT", "SiC_MOSFET"):
            raise InputValidationError("technology must be IGBT or SiC_MOSFET", field="technology")
        if self.energy_basis not in ("per_device", "commutation_pair_total"):
            raise InputValidationError("energy_basis must be per_device or commutation_pair_total",
                                       field="energy_basis")
        if self.energy_basis == "per_device" and self.e_rr is None:
            raise InputValidationError("per-device energies need E_rr of the recovering diode (declare a zero "
                                       "table explicitly if the supplier states it is negligible)", field="e_rr")
        if self.value_kind not in ("typical", "max"):
            raise InputValidationError("value_kind must be typical or max", field="value_kind")
        _finite("v_test_V", self.v_test_V)
        if self.vdc_scaling_exponent is not None:
            if not self.vdc_scaling_basis.strip():
                raise InputValidationError("a switching-energy voltage scaling needs a stated basis (product/circuit "
                                           "evidence); E ~ V is not a universal law", field="vdc_scaling_basis")
            if self.vdc_scaling_valid_V is None:
                raise InputValidationError("a switching-energy voltage scaling needs its validity range",
                                           field="vdc_scaling_valid_V")

    def energy_scale(self, vdc: float) -> tuple[float | None, str]:
        if abs(vdc - self.v_test_V) <= 1e-6 * max(1.0, self.v_test_V):
            return 1.0, "at the datasheet test voltage"
        if self.vdc_scaling_exponent is None:
            return None, (f"Vdc {vdc:g} V differs from the switching test voltage {self.v_test_V:g} V and no scaling "
                          f"law with a basis is declared: switching loss not established")
        lo, hi = self.vdc_scaling_valid_V
        if not (lo <= vdc <= hi):
            return None, f"Vdc {vdc:g} V outside the declared scaling validity [{lo:g}, {hi:g}] V"
        k = (vdc / self.v_test_V) ** self.vdc_scaling_exponent
        return k, (f"E x (Vdc/V_test)^{self.vdc_scaling_exponent:g} = x{k:.4g} ({self.vdc_scaling_basis})")


@dataclass(frozen=True)
class ModuleLossModel:
    device: SwitchDevice
    fsw_Hz: float
    modulation: str = "svpwm"          # svpwm | spwm | dpwm1
    deadtime_s: float = 0.0
    parallel: int = 1                  # external modules in parallel per switch position
    sharing_error: float = 0.0         # relative current-sharing error bound between parallel modules
    driver_aux_W: float = 0.0          # gate-driver / auxiliary loss (separate heat destination)
    aux_from_hv_dc: bool = False       # True: the auxiliary power is drawn from the HV DC link
    n_angle: int = 720

    def __post_init__(self):
        f = _finite("fsw_Hz", self.fsw_Hz)
        if f < 0:
            raise InputValidationError("switching frequency must be >= 0", field="fsw_Hz")
        if self.modulation not in ("svpwm", "spwm", "dpwm1"):
            raise InputValidationError("modulation must be svpwm, spwm or dpwm1", field="modulation")
        if _finite("deadtime_s", self.deadtime_s) < 0:
            raise InputValidationError("dead time must be >= 0", field="deadtime_s")
        if not isinstance(self.parallel, int) or self.parallel < 1:
            raise InputValidationError("parallel must be an integer >= 1", field="parallel")
        if not (0.0 <= _finite("sharing_error", self.sharing_error) < 1.0):
            raise InputValidationError("sharing error must be in [0, 1)", field="sharing_error")
        if self.n_angle < 36:
            raise InputValidationError("n_angle must be >= 36", field="n_angle")


def _duties(theta: np.ndarray, V_pk: float, Vdc: float, modulation: str):
    """Leg-a duty, a 'switching' mask and an overmodulation flag for the declared zero-sequence."""
    va = V_pk * np.cos(theta)
    vb = V_pk * np.cos(theta - TWO_PI / 3)
    vc = V_pk * np.cos(theta + TWO_PI / 3)
    stack = np.vstack([va, vb, vc])
    if modulation == "spwm":
        v0 = np.zeros_like(va)
    elif modulation == "svpwm":
        v0 = -0.5 * (stack.max(axis=0) + stack.min(axis=0))
    else:   # dpwm1: clamp the phase with the largest magnitude to its rail
        k = np.argmax(np.abs(stack), axis=0)
        vmax = stack[k, np.arange(stack.shape[1])]
        v0 = np.sign(vmax) * 0.5 * Vdc - vmax
    d = 0.5 + (va + v0) / Vdc
    over = bool(np.any(d < -1e-9) or np.any(d > 1 + 1e-9))
    clamped = (d <= 1e-12) | (d >= 1 - 1e-12)
    return np.clip(d, 0.0, 1.0), ~clamped, over


def leg_losses(model: ModuleLossModel, I_pk: float, phi_rad: float, V_pk: float, Vdc: float, Tj_C: float,
               n_angle: int | None = None) -> dict:
    """Average losses of the four positions of one leg over a fundamental period (per parallel module)."""
    n = n_angle or model.n_angle
    theta = (np.arange(n) + 0.5) * TWO_PI / n
    i = I_pk * np.cos(theta - phi_rad) / model.parallel
    d, _sw, over = _duties(theta, V_pk, Vdc, model.modulation)
    problems = []
    if over:
        problems.append(f"overmodulation (V_pk {V_pk:.4g} V at Vdc {Vdc:g} V, {model.modulation}): the linear average "
                        f"PWM model is not valid - overmodulation / six-step is not supported")
    return leg_losses_trajectory(model, i, d, Vdc, Tj_C, problems)


def leg_losses_trajectory(model: ModuleLossModel, i: np.ndarray, d: np.ndarray, Vdc: float, Tj_C: float,
                          problems: list | None = None) -> dict:
    """Average losses of one leg for a uniformly sampled period.

    ``i``: leg output current per parallel module (+ = out of the leg into the load); ``d``: upper-switch duty in
    [0, 1] at the same samples.  The leg switches wherever 0 < d < 1 (a clamped leg does not switch but still
    conducts).  Used for the single VSI (declared modulation) and for each bridge of a dual inverter (duty
    trajectories from the voltage allocation).
    """
    dev = model.device
    i = np.asarray(i, dtype=float)
    d = np.clip(np.asarray(d, dtype=float), 0.0, 1.0)
    sw = (d > 1e-12) & (d < 1.0 - 1e-12)
    ipos, ineg = i > 0, i < 0
    a = np.abs(i)
    problems = list(problems or [])
    for name, tab in (("v_on", dev.v_on), ("v_rev", dev.v_rev), ("e_on", dev.e_on), ("e_off", dev.e_off),
                      ("e_rr", dev.e_rr), ("v_channel_rev", dev.v_channel_rev)):
        if tab is None:
            continue
        ok, why = tab.covers(float(a.max()) if a.size else 0.0, Tj_C)
        if not ok:
            problems.append(f"{name}: {why}")
    von = dev.v_on.eval(a, Tj_C)
    vrev = dev.v_rev.eval(a, Tj_C)
    td_frac = min(1.0, 2.0 * model.deadtime_s * model.fsw_Hz)
    # conduction: forward path of the gated switch, reverse path of the complementary position
    fwd = d * von * a                                     # upper switch when i > 0, lower switch mirrored
    if dev.technology == "IGBT":
        rev = (1.0 - d) * vrev * a                        # anti-parallel diode carries the reverse current
        rev_parts = {"diode": rev}
    else:
        vch = (dev.v_channel_rev.eval(a, Tj_C) if dev.v_channel_rev is not None else von)
        t_bd = np.minimum(1.0 - d, td_frac)               # body diode only during the dead times
        t_ch = np.clip(1.0 - d - td_frac, 0.0, None)      # channel (synchronous rectification) for the rest
        rev_parts = {"body_diode": t_bd * vrev * a, "channel_reverse": t_ch * vch * a}
        rev = rev_parts["body_diode"] + rev_parts["channel_reverse"]
    # by symmetry: i > 0 -> upper switch fwd, lower reverse; i < 0 -> lower switch fwd with duty (1-d)
    fwd_lo = (1.0 - d) * von * a
    if dev.technology == "IGBT":
        rev_hi = d * vrev * a
    else:
        t_bd_hi = np.minimum(d, td_frac)
        t_ch_hi = np.clip(d - td_frac, 0.0, None)
        rev_hi = t_bd_hi * vrev * a + t_ch_hi * vch * a
    P = lambda arr, mask: float(np.mean(np.where(mask, arr, 0.0)))
    cond = {"upper_switch": P(fwd, ipos), "lower_reverse": P(rev, ipos),
            "lower_switch": P(fwd_lo, ineg), "upper_reverse": P(rev_hi, ineg)}
    # switching events (only while the leg switches)
    k, knote = dev.energy_scale(Vdc)
    if k is None:
        problems.append(knote)
        swl = {"upper_switch": math.nan, "lower_switch": math.nan, "upper_recovery": math.nan,
               "lower_recovery": math.nan}
    else:
        eon, eoff = dev.e_on.eval(a, Tj_C) * k, dev.e_off.eval(a, Tj_C) * k
        err = dev.e_rr.eval(a, Tj_C) * k if (dev.e_rr is not None and dev.energy_basis == "per_device") else 0.0 * a
        f = model.fsw_Hz
        swl = {"upper_switch": f * P(eon + eoff, ipos & sw), "lower_switch": f * P(eon + eoff, ineg & sw),
               "lower_recovery": f * P(err, ipos & sw), "upper_recovery": f * P(err, ineg & sw)}
    if np.any(np.isnan(von)) or np.any(np.isnan(vrev)):
        problems.append("conduction curve not given down to zero current")
    return {"conduction_W": cond, "switching_W": swl, "problems": problems, "energy_scaling": knote,
            "switching_fraction": float(np.mean(sw)), "deadtime_fraction": td_frac}


def _positions(leg: dict) -> dict:
    c, s = leg["conduction_W"], leg["switching_W"]
    return {"upper_switch": c["upper_switch"] + s["upper_switch"],
            "upper_diode_or_reverse": c["upper_reverse"] + s["upper_recovery"],
            "lower_switch": c["lower_switch"] + s["lower_switch"],
            "lower_diode_or_reverse": c["lower_reverse"] + s["lower_recovery"]}


def inverter_losses(model: ModuleLossModel, id_A: float, iq_A: float, vd_V: float, vq_V: float, Vdc_V: float,
                    Tj_C: float, refine_check: bool = True) -> dict:
    """Module losses at one dq operating point (fundamental phase-peak values)."""
    I_pk = math.hypot(id_A, iq_A)
    V_pk = math.hypot(vd_V, vq_V)
    phi = (math.atan2(vq_V, vd_V) - math.atan2(iq_A, id_A)) if (I_pk > 0 and V_pk > 0) else 0.0
    m = V_pk / (0.5 * Vdc_V)
    leg = leg_losses(model, I_pk, phi, V_pk, Vdc_V, Tj_C)
    pos = _positions(leg)
    per_leg = sum(pos.values())
    total_semis = 3.0 * model.parallel * per_leg
    cond_total = 3.0 * model.parallel * sum(leg["conduction_W"].values())
    sw_total = 3.0 * model.parallel * sum(leg["switching_W"].values())
    established = not leg["problems"]
    # hotspot: the most loaded position, with the current-sharing error on the hottest parallel module
    worst_pos = max(pos, key=lambda k: -1 if math.isnan(pos[k]) else pos[k])
    share = 1.0 + model.sharing_error
    hot = None
    if model.sharing_error > 0 and established:
        lh = leg_losses(model, I_pk * share, phi, V_pk, Vdc_V, Tj_C)
        hot = max(_positions(lh).values())
    out = {"I_pk_A": I_pk, "V_pk_V": V_pk, "modulation_index": m, "phi_deg": math.degrees(phi),
           "power_factor": math.cos(phi), "Tj_eval_C": Tj_C, "fsw_Hz": model.fsw_Hz, "modulation": model.modulation,
           "per_position_W": pos, "leg": leg, "conduction_W": cond_total, "switching_W": sw_total,
           "semiconductor_W": total_semis, "driver_aux_W": model.driver_aux_W,
           "dc_side_W": total_semis + (model.driver_aux_W if model.aux_from_hv_dc else 0.0),
           "hottest_position": worst_pos, "hottest_position_W": pos[worst_pos],
           "hottest_with_sharing_error_W": hot, "established": established, "problems": leg["problems"],
           "value_kind": model.device.value_kind,
           "not_modelled": ["current ripple", "dead-time voltage error", "minimum pulse / pulse dropping",
                            "ringing, C_oss hard-commutation at zero current", "die-level sharing inside a module"]}
    if refine_check and established:
        coarse = leg_losses(model, I_pk, phi, V_pk, Vdc_V, Tj_C, n_angle=max(36, model.n_angle // 2))
        c_tot = 3.0 * model.parallel * sum(_positions(coarse).values())
        out["angle_refinement_rel_diff"] = abs(c_tot - total_semis) / max(total_semis, 1e-12)
    return out


def standstill_hotspot(model: ModuleLossModel, I_pk: float, Vdc_V: float, Tj_C: float, n_angle: int = 720) -> dict:
    """DC phase currents at standstill: per-device loss vs electrical angle (sampled), max over the angle."""
    dev = model.device
    best = None
    rows = []
    for th in (np.arange(n_angle) + 0.5) * TWO_PI / n_angle:
        worst_here = 0.0
        total_here = 0.0
        for off in (0.0, -TWO_PI / 3, TWO_PI / 3):
            i = I_pk * math.cos(th + off) / model.parallel
            a = abs(i)
            d = 0.5                                     # negligible voltage reference at standstill
            ok, why = dev.v_on.covers(a, Tj_C)
            if not ok:
                return {"established": False, "problem": why}
            von = float(dev.v_on.eval(np.array([a]), Tj_C)[0])
            vrev = float(dev.v_rev.eval(np.array([a]), Tj_C)[0])
            k, _ = dev.energy_scale(Vdc_V)
            esw = 0.0 if k is None else float((dev.e_on.eval(np.array([a]), Tj_C)[0] +
                                               dev.e_off.eval(np.array([a]), Tj_C)[0]) * k) * model.fsw_Hz
            err = 0.0 if (k is None or dev.e_rr is None or dev.energy_basis != "per_device") else \
                float(dev.e_rr.eval(np.array([a]), Tj_C)[0] * k) * model.fsw_Hz
            p_sw_dev = d * von * a + esw
            p_rev_dev = (1 - d) * vrev * a + err if dev.technology == "IGBT" else \
                min(1 - d, 2 * model.deadtime_s * model.fsw_Hz) * vrev * a + err
            worst_here = max(worst_here, p_sw_dev, p_rev_dev)
            total_here += (p_sw_dev + p_rev_dev) * model.parallel
        rows.append((float(th), worst_here, total_here))
        if best is None or worst_here > best[1]:
            best = rows[-1]
    return {"established": True, "hottest_device_W": best[1], "angle_at_max_deg": math.degrees(best[0]),
            "module_total_W": best[2], "total_over_six_W": best[2] / 6.0,
            "note": "sampled over the electrical angle; total/6 is not a junction-temperature input at standstill",
            "curve": rows}


def electrothermal_fixed_point(model: ModuleLossModel, op: dict, Rth_K_per_W: float, T_ref_C: float,
                               max_iter: int = 60, tol_K: float = 1e-3) -> dict:
    """Steady junction temperature of the hottest position: Tj = T_ref + Rth * P(Tj) (damped fixed point).

    A non-converging iteration is reported as 'no steady state found numerically', not as thermal runaway.
    """
    Rth = _finite("Rth_K_per_W", Rth_K_per_W)
    T = float(T_ref_C)
    hist = []
    for it in range(max_iter):
        r = inverter_losses(model, op["id_A"], op["iq_A"], op["vd_V"], op["vq_V"], op["Vdc_V"], T, refine_check=False)
        if not r["established"]:
            return {"converged": False, "reason": "; ".join(r["problems"]), "history": hist}
        P = r["hottest_position_W"]
        T_new = T_ref_C + Rth * P
        hist.append((T, P, T_new))
        if abs(T_new - T) <= tol_K:
            return {"converged": True, "Tj_C": T_new, "P_hot_W": P, "iterations": it + 1, "history": hist}
        T = T + 0.7 * (T_new - T)
    return {"converged": False, "reason": "fixed-point iteration did not converge (numerical; not a proof of "
                                          "thermal runaway)", "history": hist}


def loss_claim(result: dict, P_allow_W: float | None = None) -> dict:
    """What may be concluded from a loss evaluation: established numbers vs missing parts."""
    if not result.get("established", False):
        return Claim("module_loss", Status.UNKNOWN, "semiconductor losses at the operating point",
                     "datasheet-based average model", reasons=(Reason.MISSING_INPUT,),
                     detail="not established: " + "; ".join(result.get("problems", []))).to_dict()
    ev = Evidence.make(EvidenceKind.DIRECT_EVALUATION,
                       f"{result['semiconductor_W']:.4g} W semiconductor loss ({result['value_kind']} datasheet values)",
                       conduction_W=result["conduction_W"], switching_W=result["switching_W"])
    quals = ("datasheet typical values are not guaranteed upper bounds",) if result["value_kind"] == "typical" else ()
    if P_allow_W is None:
        return Claim("module_loss", Status.UNKNOWN, "semiconductor losses at the operating point",
                     "datasheet-based average model", reasons=(Reason.REQUIREMENT_INCOMPLETE,), evidence=(ev,),
                     qualifiers=quals, detail="loss computed; no allowed loss stated to judge against").to_dict()
    st = Status.FEASIBLE if result["semiconductor_W"] <= P_allow_W else Status.INFEASIBLE
    if st is Status.FEASIBLE and result["value_kind"] == "typical":
        st = Status.UNKNOWN
        quals += ("typical values: the margin is not a guaranteed bound",)
    return Claim("module_loss", st, f"semiconductor losses <= {P_allow_W:g} W", "datasheet-based average model",
                 reasons=() if st is Status.FEASIBLE else ((Reason.CONSTRAINT_VIOLATION,) if st is Status.INFEASIBLE
                                                          else (Reason.UNCERTAINTY_OVERLAP,)),
                 evidence=(ev,), qualifiers=quals, detail=f"{result['semiconductor_W']:.4g} W vs {P_allow_W:g} W").to_dict()
