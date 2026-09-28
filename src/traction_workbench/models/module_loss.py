"""Datasheet-based power-module losses for a 2-level, 3-phase inverter (independent review, section 8.8).

Part of the drive model: ``InverterModel.module_loss`` is the inverter loss model the kernel evaluates at every
operating point (P_dc, DC claims, capability, sizing, maps), exclusive with the quadratic surrogate.  It is a
POINTWISE model - there is no closed-form P_dc(T_em, I^2) identity, so DC claims with it rest on directly evaluated
witnesses (``physics.DriveKernel.loss_kind`` / ``i2_dc``), never on the I^2 certificates of the surrogate.

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
from ..modulation import duties, overmodulated
from ..validation import finite as _finite
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
    # physical owner of the reverse current (review R2 PT-01): IGBT -> its anti-parallel diode (a separate die);
    # SiC MOSFET -> 'intrinsic' (third-quadrant channel AND body diode heat the MOSFET die) or 'external_diode'
    # (a declared anti-parallel diode takes the dead-time conduction and the recovery: a separate die)
    reverse_path: str = ""

    def __post_init__(self):
        if self.technology not in ("IGBT", "SiC_MOSFET"):
            raise InputValidationError("technology must be IGBT or SiC_MOSFET", field="technology")
        rp = (self.reverse_path or ("antiparallel_diode" if self.technology == "IGBT" else "intrinsic")).strip()
        allowed = ("antiparallel_diode",) if self.technology == "IGBT" else ("intrinsic", "external_diode")
        if rp not in allowed:
            raise InputValidationError(f"reverse_path of a {self.technology} must be one of {allowed}",
                                       field="reverse_path")
        object.__setattr__(self, "reverse_path", rp)
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
    d = duties(theta, V_pk / (0.5 * Vdc), 0.0, modulation)[0]            # the shared modulation law
    over = overmodulated(d)
    clamped = (d <= 1e-12) | (d >= 1 - 1e-12)
    return np.clip(d, 0.0, 1.0), ~clamped, over


def leg_losses(model: ModuleLossModel, I_pk: float, phi_rad: float, V_pk: float, Vdc: float, Tj_C: float,
               n_angle: int | None = None, share: float = 1.0) -> dict:
    """Average losses of the four positions of one leg over a fundamental period (per parallel module; ``share``
    scales this module's current, e.g. 1 + sharing error for the hottest parallel module)."""
    n = n_angle or model.n_angle
    theta = (np.arange(n) + 0.5) * TWO_PI / n
    i = I_pk * np.cos(theta - phi_rad) / model.parallel * share
    d, _sw, over = _duties(theta, V_pk, Vdc, model.modulation)
    problems = []
    if over:
        problems.append(f"overmodulation (V_pk {V_pk:.4g} V at Vdc {Vdc:g} V, {model.modulation}): the linear average "
                        f"PWM model is not valid - overmodulation / six-step is not supported")
    return leg_losses_trajectory(model, i, d, Vdc, Tj_C, problems)


def _leg_samples(model: ModuleLossModel, i: np.ndarray, d: np.ndarray, Vdc: float, Tj_C: float,
                 problems: list | None = None) -> dict:
    """Heat of every conduction path and switching event of one leg AT EACH SAMPLE (per parallel module).

    The one evaluator of the model (review R2 PT-02): the rotating period average and the standstill per-angle
    evaluation both use it, with the same data applicability gate - every active curve is checked over the current
    and temperature it is read at, and switching energies away from the test voltage need a declared scaling law
    (otherwise NaN + a problem: a missing loss is never zero).
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
    z = np.zeros_like(a)
    reverse_parts = None
    if dev.technology == "IGBT":
        lo_rev = (1.0 - d) * vrev * a                     # anti-parallel diode carries the reverse current
        up_rev = d * vrev * a
    else:
        vch = (dev.v_channel_rev.eval(a, Tj_C) if dev.v_channel_rev is not None else von)
        t_bd, t_ch = np.minimum(1.0 - d, td_frac), np.clip(1.0 - d - td_frac, 0.0, None)
        t_bd_hi, t_ch_hi = np.minimum(d, td_frac), np.clip(d - td_frac, 0.0, None)
        lo_rev = t_bd * vrev * a + t_ch * vch * a         # body diode in the dead times, channel otherwise
        up_rev = t_bd_hi * vrev * a + t_ch_hi * vch * a
        reverse_parts = {"lower_deadtime": np.where(ipos, t_bd * vrev * a, z),
                         "lower_channel": np.where(ipos, t_ch * vch * a, z),
                         "upper_deadtime": np.where(ineg, t_bd_hi * vrev * a, z),
                         "upper_channel": np.where(ineg, t_ch_hi * vch * a, z)}
    # i > 0: upper switch forward (duty d), lower reverse (1 - d); i < 0 mirrored
    cond = {"upper_switch": np.where(ipos, d * von * a, z), "lower_reverse": np.where(ipos, lo_rev, z),
            "lower_switch": np.where(ineg, (1.0 - d) * von * a, z), "upper_reverse": np.where(ineg, up_rev, z)}
    k, knote = dev.energy_scale(Vdc)
    if k is None:
        problems.append(knote)
        nan = np.full_like(a, np.nan)
        swl = {"upper_switch": nan, "lower_switch": nan, "upper_recovery": nan, "lower_recovery": nan}
    else:
        f = model.fsw_Hz
        eonoff = (dev.e_on.eval(a, Tj_C) + dev.e_off.eval(a, Tj_C)) * k
        err = dev.e_rr.eval(a, Tj_C) * k if (dev.e_rr is not None and dev.energy_basis == "per_device") else z
        swl = {"upper_switch": np.where(ipos & sw, f * eonoff, z), "lower_switch": np.where(ineg & sw, f * eonoff, z),
               "lower_recovery": np.where(ipos & sw, f * err, z), "upper_recovery": np.where(ineg & sw, f * err, z)}
    if np.any(np.isnan(von)) or np.any(np.isnan(vrev)):
        problems.append("conduction curve not given down to zero current")
    return {"conduction": cond, "switching": swl, "reverse_parts": reverse_parts, "problems": problems,
            "energy_scaling": knote, "sw_mask": sw, "deadtime_fraction": td_frac}


def leg_losses_trajectory(model: ModuleLossModel, i: np.ndarray, d: np.ndarray, Vdc: float, Tj_C: float,
                          problems: list | None = None) -> dict:
    """Average losses of one leg for a uniformly sampled period.

    ``i``: leg output current per parallel module (+ = out of the leg into the load); ``d``: upper-switch duty in
    [0, 1] at the same samples.  The leg switches wherever 0 < d < 1 (a clamped leg does not switch but still
    conducts).  Used for the single VSI (declared modulation) and for each bridge of a dual inverter (duty
    trajectories from the voltage allocation).
    """
    smp = _leg_samples(model, i, d, Vdc, Tj_C, problems)
    mean = lambda arr: float(np.mean(arr))   # noqa: E731
    rp = smp["reverse_parts"]
    return {"conduction_W": {k: mean(v) for k, v in smp["conduction"].items()},
            "switching_W": {k: mean(v) for k, v in smp["switching"].items()},
            "reverse_parts_W": None if rp is None else {k: mean(v) for k, v in rp.items()},
            "problems": smp["problems"], "energy_scaling": smp["energy_scaling"],
            "switching_fraction": float(np.mean(smp["sw_mask"])) if np.size(smp["sw_mask"]) else 0.0,
            "deadtime_fraction": smp["deadtime_fraction"]}


def positions(leg: dict) -> dict:
    """Current-DIRECTION contributions of one leg (forward / reverse per position).  Not thermal owners: with a SiC
    MOSFET the forward and the reverse contribution of a position heat the same die (use ``dies``)."""
    c, s = leg["conduction_W"], leg["switching_W"]
    return {"upper_switch": c["upper_switch"] + s["upper_switch"],
            "upper_diode_or_reverse": c["upper_reverse"] + s["upper_recovery"],
            "lower_switch": c["lower_switch"] + s["lower_switch"],
            "lower_diode_or_reverse": c["lower_reverse"] + s["lower_recovery"]}


def dies(leg: dict, device: SwitchDevice) -> tuple[dict, str]:
    """Heat per PHYSICAL die of one leg (review R2 PT-01): every conduction path and switching event is summed by the
    die that owns it.  IGBT: transistor and anti-parallel diode are separate dies.  SiC MOSFET with an intrinsic
    reverse path: first- and third-quadrant channel conduction, body-diode conduction and its recovery heat ONE die.
    With a declared external diode the dead-time conduction and the recovery belong to that diode.  Returns the dies
    and the basis; with commutation-pair-total energies the per-die numbers are a conservative BOUND (the pair energy
    is charged to both dies of the commutation) and do not sum to the total."""
    c, s = leg["conduction_W"], leg["switching_W"]
    pair = device.energy_basis == "commutation_pair_total"
    basis = ("conservative bound: pair-total switching energy charged to both dies of each commutation" if pair
             else "per-die heat; the dies sum to the leg's semiconductor loss")
    if device.technology == "IGBT":
        out = {"upper_igbt": c["upper_switch"] + s["upper_switch"],
               "upper_diode": c["upper_reverse"] + s["upper_recovery"] + (s["lower_switch"] if pair else 0.0),
               "lower_igbt": c["lower_switch"] + s["lower_switch"],
               "lower_diode": c["lower_reverse"] + s["lower_recovery"] + (s["upper_switch"] if pair else 0.0)}
        return out, basis
    rp = leg.get("reverse_parts_W") or {}
    if device.reverse_path == "intrinsic":
        out = {"upper_mosfet": c["upper_switch"] + s["upper_switch"] + c["upper_reverse"] + s["upper_recovery"],
               "lower_mosfet": c["lower_switch"] + s["lower_switch"] + c["lower_reverse"] + s["lower_recovery"]}
        if pair:
            out = {"upper_mosfet": out["upper_mosfet"] + s["lower_switch"],
                   "lower_mosfet": out["lower_mosfet"] + s["upper_switch"]}
        return out, basis
    out = {"upper_mosfet": c["upper_switch"] + s["upper_switch"] + rp.get("upper_channel", 0.0),
           "upper_diode": rp.get("upper_deadtime", 0.0) + s["upper_recovery"] + (s["lower_switch"] if pair else 0.0),
           "lower_mosfet": c["lower_switch"] + s["lower_switch"] + rp.get("lower_channel", 0.0),
           "lower_diode": rp.get("lower_deadtime", 0.0) + s["lower_recovery"] + (s["upper_switch"] if pair else 0.0)}
    return out, basis


def _hottest(d: dict) -> tuple[str, float]:
    name = max(d, key=lambda k: -1.0 if math.isnan(d[k]) else d[k])
    return name, d[name]


def _sharing(model: ModuleLossModel) -> tuple[float, float] | None:
    """Current shares of the hottest and of the other parallel modules under the declared sharing error: the hot
    module carries (1 + e) of its nominal share, the others the rest - the currents still sum to the phase current
    (review R2 PT-01; a positive error on every module would create current)."""
    n, e = model.parallel, model.sharing_error
    if n <= 1 or e <= 0:
        return None
    return 1.0 + e, 1.0 - e / (n - 1)


def inverter_losses(model: ModuleLossModel, id_A: float, iq_A: float, vd_V: float, vq_V: float, Vdc_V: float,
                    Tj_C: float, refine_check: bool = True) -> dict:
    """Module losses at one dq operating point (fundamental phase-peak values), fundamental-period average.

    ``hottest_position`` / ``hottest_position_W`` name the hottest PHYSICAL die (review R2 PT-01; with a declared
    sharing error, the die of the hottest parallel module) - the input of every junction-temperature use.
    ``per_position_W`` keeps the current-direction contributions for reference; they are not thermal owners.
    """
    I_pk = math.hypot(id_A, iq_A)
    V_pk = math.hypot(vd_V, vq_V)
    phi = (math.atan2(vq_V, vd_V) - math.atan2(iq_A, id_A)) if (I_pk > 0 and V_pk > 0) else 0.0
    m = V_pk / (0.5 * Vdc_V)
    leg = leg_losses(model, I_pk, phi, V_pk, Vdc_V, Tj_C)
    pos = positions(leg)
    per_die, die_basis = dies(leg, model.device)
    per_leg = sum(pos.values())
    total_semis = 3.0 * model.parallel * per_leg
    cond_total = 3.0 * model.parallel * sum(leg["conduction_W"].values())
    sw_total = 3.0 * model.parallel * sum(leg["switching_W"].values())
    problems = list(leg["problems"])
    hot_name, hot_W = _hottest(per_die)
    per_die_thermal = dict(per_die)
    sharing = None
    sh = _sharing(model)
    if sh is not None:
        lh = leg_losses(model, I_pk, phi, V_pk, Vdc_V, Tj_C, share=sh[0])
        lo = leg_losses(model, I_pk, phi, V_pk, Vdc_V, Tj_C, share=sh[1])
        problems += [f"hottest parallel module (current x{sh[0]:g}): {q}" for q in lh["problems"] if q not in problems]
        dh, _ = dies(lh, model.device)
        nominal = hot_W
        hot_name, hot_W = _hottest(dh)
        per_die_thermal = dict(dh)
        sharing = {"hot_module_current_share": sh[0] / model.parallel,
                   "other_module_current_share": sh[1] / model.parallel,
                   "semiconductor_W": 3.0 * (sum(positions(lh).values()) + (model.parallel - 1) * sum(positions(lo).values())),
                   "hottest_die_equal_sharing_W": nominal,
                   "note": "admissible allocation: the hottest module at (1 + e) of its share, the others at the "
                           "rest; currents sum to the phase current"}
    established = not problems
    out = {"I_pk_A": I_pk, "V_pk_V": V_pk, "modulation_index": m, "phi_deg": math.degrees(phi),
           "power_factor": math.cos(phi), "Tj_eval_C": Tj_C, "fsw_Hz": model.fsw_Hz, "modulation": model.modulation,
           "per_position_W": pos, "per_die_W": per_die, "per_die_thermal_W": per_die_thermal, "die_basis": die_basis,
           "leg": leg,
           "conduction_W": cond_total, "switching_W": sw_total,
           "semiconductor_W": total_semis, "driver_aux_W": model.driver_aux_W,
           "dc_side_W": total_semis + (model.driver_aux_W if model.aux_from_hv_dc else 0.0),
           "hottest_position": hot_name, "hottest_position_W": hot_W,
           "hottest_with_sharing_error_W": None if sharing is None else hot_W, "sharing": sharing,
           "established": established, "problems": problems,
           "value_kind": model.device.value_kind,
           "not_modelled": ["current ripple", "dead-time voltage error", "minimum pulse / pulse dropping",
                            "ringing, C_oss hard-commutation at zero current", "die-level sharing inside a module",
                            "low-frequency junction ripple within the fundamental period (average model)"]}
    if refine_check and established:
        coarse = leg_losses(model, I_pk, phi, V_pk, Vdc_V, Tj_C, n_angle=max(36, model.n_angle // 2))
        c_tot = 3.0 * model.parallel * sum(positions(coarse).values())
        out["angle_refinement_rel_diff"] = abs(c_tot - total_semis) / max(total_semis, 1e-12)
    return out


def standstill_losses(model: ModuleLossModel, I_pk: float, Vdc_V: float, Tj_C: float, V_pk: float = 0.0,
                      phi_rad: float = 0.0, n_angle: int = 720, share: float = 1.0) -> dict:
    """DC phase currents at standstill (review R2 PT-02).

    For each sampled electrical angle every phase carries a constant current and its leg the ACTUAL duty of the
    declared modulation for the (resistive) terminal voltage; heat per physical die from the same per-sample
    evaluator as the rotating model (SiC: gate-on reverse channel and body diode on the same die) and the same data
    gate (every active curve, the Vdc scaling of the energies - missing is UNKNOWN, never zero).  The electrical
    angle at standstill is not known: the result is the worst sampled angle, reported with the angle range.
    """
    thetas = (np.arange(n_angle) + 0.5) * TWO_PI / n_angle
    m = V_pk / (0.5 * Vdc_V)
    d_raw = duties(thetas + phi_rad, m, 0.0, model.modulation)
    problems = []
    if overmodulated(d_raw):
        problems.append(f"overmodulation at standstill (V_pk {V_pk:.4g} V at Vdc {Vdc_V:g} V): not supported")
    d3 = np.clip(d_raw, 0.0, 1.0)
    worst = np.zeros(n_angle)
    worst_name = np.array([""] * n_angle, dtype=object)
    die_max: dict = {}
    total = np.zeros(n_angle)
    cond = np.zeros(n_angle)
    swit = np.zeros(n_angle)
    die_basis = ""
    for k in range(3):
        i_k = I_pk * np.cos(thetas - TWO_PI * k / 3.0) / model.parallel * share
        smp = _leg_samples(model, i_k, d3[k], Vdc_V, Tj_C)
        problems += [q for q in smp["problems"] if q not in problems]
        leg = {"conduction_W": smp["conduction"], "switching_W": smp["switching"],
               "reverse_parts_W": smp["reverse_parts"]}
        dd, die_basis = dies(leg, model.device)
        for name, arr in dd.items():
            die_max[name] = max(die_max.get(name, 0.0), float(np.max(arr)))
            better = arr > worst
            worst = np.where(better, arr, worst)
            worst_name = np.where(better, f"phase {'abc'[k]} {name}", worst_name)
        c_k = sum(smp["conduction"].values())
        s_k = sum(smp["switching"].values())
        cond += c_k * model.parallel
        swit += s_k * model.parallel
        total += (c_k + s_k) * model.parallel
    if problems:
        return {"established": False, "problems": problems, "problem": "; ".join(problems)}
    j = int(np.argmax(worst))
    return {"established": True, "problems": [], "hottest_device_W": float(worst[j]), "hottest_die": str(worst_name[j]),
            "angle_at_max_deg": math.degrees(thetas[j]), "module_total_W": float(total[j]),
            "conduction_W": float(cond[j]), "switching_W": float(swit[j]),
            "total_max_over_angle_W": float(total.max()), "total_min_over_angle_W": float(total.min()),
            "total_over_six_W": float(total[j]) / 6.0, "die_basis": die_basis,
            "per_die_max_over_angle_W": die_max,
            "note": "sampled over the unknown electrical angle (worst angle reported); total/6 is not a "
                    "junction-temperature input at standstill",
            "curve": [(float(t), float(w), float(tt)) for t, w, tt in zip(thetas, worst, total)]}


def standstill_hotspot(model: ModuleLossModel, I_pk: float, Vdc_V: float, Tj_C: float, n_angle: int = 720) -> dict:
    """Former interface: standstill with a negligible terminal voltage (duty of the declared modulation at m = 0)."""
    return standstill_losses(model, I_pk, Vdc_V, Tj_C, n_angle=n_angle)


def point_losses(model: ModuleLossModel, id_A: float, iq_A: float, vd_V: float, vq_V: float, Vdc_V: float,
                 Tj_C: float, standstill: bool, refine_check: bool = False) -> dict:
    """THE module-loss evaluation of an operating point for every consumer (kernel, API, pages): the rotating
    period average, or at standstill the per-angle DC-current evaluation - never one for the other."""
    if not standstill:
        return inverter_losses(model, id_A, iq_A, vd_V, vq_V, Vdc_V, Tj_C, refine_check=refine_check)
    I_pk = math.hypot(id_A, iq_A)
    V_pk = math.hypot(vd_V, vq_V)
    phi = (math.atan2(vq_V, vd_V) - math.atan2(iq_A, id_A)) if (I_pk > 0 and V_pk > 0) else 0.0
    h = standstill_losses(model, I_pk, Vdc_V, Tj_C, V_pk, phi)
    if not h["established"]:
        return {"established": False, "problems": h["problems"], "value_kind": model.device.value_kind,
                "Tj_eval_C": Tj_C, "fsw_Hz": model.fsw_Hz, "standstill": True}
    hot_name, hot_W, sharing = (h["hottest_die"] or "none (no current)"), h["hottest_device_W"], None
    sh = _sharing(model)
    if sh is not None:
        hh = standstill_losses(model, I_pk, Vdc_V, Tj_C, V_pk, phi, share=sh[0])
        if not hh["established"]:
            return {"established": False, "problems": [f"hottest parallel module (current x{sh[0]:g}): {q}"
                                                       for q in hh["problems"]],
                    "value_kind": model.device.value_kind, "Tj_eval_C": Tj_C, "fsw_Hz": model.fsw_Hz,
                    "standstill": True}
        sharing = {"hottest_die_equal_sharing_W": hot_W, "note": "hottest parallel module at (1 + e) of its share"}
        hot_name, hot_W = hh["hottest_die"], hh["hottest_device_W"]
    tot = h["total_max_over_angle_W"]
    # the per-die heat at standstill depends on the unknown electrical angle: a die's history is known only without
    # current (every die at zero); otherwise only the bound over the angle is (a heat bound is not a cycle bound)
    zero = I_pk == 0.0
    return {"established": True, "semiconductor_W": tot, "conduction_W": h["conduction_W"],
            "switching_W": h["switching_W"], "per_position_W": {}, "per_die_W": {},
            "per_die_thermal_W": ({k: 0.0 for k in h["per_die_max_over_angle_W"]} if zero else {}),
            "per_die_bound_over_angle_W": h["per_die_max_over_angle_W"], "die_basis": h["die_basis"],
            "hottest_position": f"{hot_name} (worst angle)", "hottest_position_W": hot_W, "sharing": sharing,
            "modulation_index": V_pk / (0.5 * Vdc_V), "power_factor": math.cos(phi), "Tj_eval_C": Tj_C,
            "fsw_Hz": model.fsw_Hz, "value_kind": model.device.value_kind, "problems": [],
            "dc_side_W": tot + (model.driver_aux_W if model.aux_from_hv_dc else 0.0),
            "driver_aux_W": model.driver_aux_W,
            "standstill": {"angle_at_max_deg": h["angle_at_max_deg"], "total_over_six_W": h["total_over_six_W"],
                           "total_range_over_angle_W": [h["total_min_over_angle_W"], h["total_max_over_angle_W"]],
                           "note": "DC phase currents at the unknown electrical angle: the worst sampled angle; "
                                   "total/6 is not a device-level input"}}


def electrothermal_fixed_point(model: ModuleLossModel, op: dict, Rth_K_per_W: float, T_ref_C: float,
                               max_iter: int = 60, tol_K: float = 1e-3) -> dict:
    """Steady junction temperature of the hottest position: Tj = T_ref + Rth * P(Tj) (damped fixed point).

    A non-converging iteration is reported as 'no steady state found numerically', not as thermal runaway.
    """
    Rth = _finite("Rth_K_per_W", Rth_K_per_W)
    T = float(T_ref_C)
    hist = []
    for it in range(max_iter):
        r = point_losses(model, op["id_A"], op["iq_A"], op["vd_V"], op["vq_V"], op["Vdc_V"], T,
                         standstill=bool(op.get("standstill", False)))
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


_positions = positions          # former private name (compatibility)
