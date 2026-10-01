"""Integrated charging (system view, item 9): the drive inverter and the motor windings as a boost converter from
a lower-voltage DC charger.

Topology.  The charger's positive terminal is connected to the motor's star point (neutral access, a design
statement), its negative terminal to the DC link's negative rail; the battery stays on the DC link.  Each phase
winding is the inductor of one boost leg: the lower switch stores, the upper path (diode, or the MOSFET channel in
synchronous rectification) delivers to the battery.  The rotor stands still (parking lock).

Waveforms (one switching period, exact for ideal switches).  Upper-path duty d = (V_c - R_ph I_k) / V_b with the
per-phase current I_k = I_charge / 3 (the legs share equally; the controller's balancing is assumed); the legs'
carriers are 120 deg apart (interleaved) or in phase.  The phase voltages in the current direction
v_k = V_c - V_b s_k(t) - R_ph I_k are split into the zero sequence (seen through the zero-sequence inductance L0 -
mostly leakage, a declared value: it is not derivable from L_d / L_q) and the d / q components at the rotor angle
(seen through the incremental L_d, L_q at zero dq current).  Between switching instants every component is linear
in time; the zero-mean ripple is integrated exactly between the instants (the phase resistance is neglected for the
ripple - stated with the ratio omega_sw L / R).  With interleaving the zero-sequence voltage of the three legs
cancels at d = 1/3 and 2/3, so the charger current ripple vanishes there while the phase currents still ripple
through L_d / L_q.

Losses.  From the module's own datasheet curves at each sample of the period: the forward switch, the reverse path
(IGBT: anti-parallel diode; SiC: channel, the body diode in the two dead times) and, at each switching instant, the
hard-switching energies at the INSTANTANEOUS current (turn-on at the valley, turn-off at the peak) and the recovery of
the diode that turns off - the same data gates as the drive: no extrapolation, a switching energy away from the test
voltage only with a declared scaling law.  The hottest die gets the module's junction-to-coolant Rth (electrothermal
fixed point; coupling between dies not modelled).  Motor copper R_ph sum(i_k^2), the DC-link capacitor current (the
AC part of the upper-path current, divided between the capacitor and the declared source impedance) and its ESR loss.

Limits.  Charger current and power, the battery's charge power and current (the DC source limits), the phase peak
current, the neutral-connection RMS current, the junction temperature and the stator copper loss, each where it is
declared; the charging-power capability is the largest charger current that keeps every declared limit (bisection;
every quantity grows with the current), with the limiter named.  A limit that is not declared is listed as not
checked - never assumed satisfied.  Discharging (V2X DC, I < 0) is evaluated per point with the same equations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .. import progress
from ..errors import InputValidationError
from ..validation import finite as _finite

PASS, FAIL, UNKNOWN, NOT_APPLICABLE = "PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"
SQ3 = math.sqrt(3.0)
MODEL_BAND_CARRIERS = 10           # harmonics of the switching frequency used for the capacitor (as the ripple page)


@dataclass(frozen=True)
class ChargingPath:
    """Product data of the charging path (the project's ``charging`` section)."""

    neutral_access: bool
    L0_H: float | None = None                   # zero-sequence inductance per phase (neutral current path)
    L0_basis: str = ""
    neutral_current_max_A: float | None = None  # RMS rating of the neutral connection / contactor
    phase_current_peak_max_A: float | None = None
    Tj_max_C: float | None = None
    winding_loss_max_W: float | None = None     # stator copper loss the cooled standstill winding may carry
    interleave: str = "120deg"                  # "120deg" | "none"
    basis: str = ""

    def __post_init__(self):
        if self.interleave not in ("120deg", "none"):
            raise InputValidationError("interleave must be '120deg' or 'none'", field="charging.interleave")
        if self.L0_H is not None:
            if _finite("L0_H", self.L0_H) <= 0:
                raise InputValidationError("L0 must be > 0", field="charging.L0_uH")
            if not str(self.L0_basis).strip():
                raise InputValidationError("the zero-sequence inductance needs its basis (measurement / FEA)",
                                           field="charging.L0_basis")
        for k in ("neutral_current_max_A", "phase_current_peak_max_A", "winding_loss_max_W"):
            v = getattr(self, k)
            if v is not None and _finite(k, v) <= 0:
                raise InputValidationError(f"{k} must be > 0", field=f"charging.{k}")
        if self.Tj_max_C is not None:
            _finite("Tj_max_C", self.Tj_max_C)


def path_from_dict(d: dict | None) -> ChargingPath:
    if not d:
        raise InputValidationError("no charging-path data (neutral access, L0, ratings)", field="charging")
    g = lambda k: None if d.get(k) in (None, "") else float(d[k])   # noqa: E731
    L0 = g("L0_uH")
    return ChargingPath(bool(d.get("neutral_access")), None if L0 is None else L0 * 1e-6, str(d.get("L0_basis", "")),
                        g("neutral_current_max_A"), g("phase_current_peak_max_A"), g("Tj_max_C"),
                        g("winding_loss_max_W"), str(d.get("interleave", "120deg")), str(d.get("basis", "")))


def validate_path(d: dict) -> None:
    path_from_dict(d)


# --------------------------------------------------------------------------------------------- waveforms

def _upper_on(t: np.ndarray, T: float, d: float, center: float) -> np.ndarray:
    """1 where the upper path conducts: a pulse of width d T centred at ``center`` (periodic)."""
    x = np.mod(t - center + 0.5 * T, T) - 0.5 * T
    return (np.abs(x) < 0.5 * d * T).astype(float)


def _events(T: float, d: float, centers) -> list:
    """(time, leg, kind): kind 'up' = upper path starts (lower turns off), 'down' = lower turns on."""
    ev = []
    for k, c in enumerate(centers):
        ev.append((np.mod(c - 0.5 * d * T, T), k, "up"))
        ev.append((np.mod(c + 0.5 * d * T, T), k, "down"))
    return sorted(ev)


def _ripple(t: np.ndarray, v: np.ndarray, L: float) -> np.ndarray:
    """Zero-mean periodic current of L di/dt = v for v piecewise constant on the (fine, event-aligned) grid t."""
    dt = np.diff(t)
    i = np.concatenate(([0.0], np.cumsum(0.5 * (v[:-1] + v[1:]) * dt))) / L
    # remove the drift from rounding (the mean voltage is zero by construction) and the mean
    i -= (t - t[0]) / (t[-1] - t[0]) * (i[-1] - i[0])
    mean = np.sum(0.5 * (i[:-1] + i[1:]) * dt) / (t[-1] - t[0])
    return i - mean


def period_waveforms(V_c: float, V_b: float, I_charge: float, R_ph: float, L0: float, Ld: float, Lq: float,
                     theta_e: float, fsw: float, interleave: str = "120deg", n: int = 3600) -> dict:
    """Phase, neutral and DC-rail currents over one period (exact piecewise-linear ripple, see the module text)."""
    T = 1.0 / fsw
    Ik = I_charge / 3.0
    d = (V_c - R_ph * Ik) / V_b
    if not (0.0 < d < 1.0):
        raise InputValidationError(f"upper-path duty {d:.4g} outside (0, 1): the boost cannot reach V_b = {V_b:g} V "
                                   f"from V_c = {V_c:g} V at {I_charge:g} A", field="V_charger")
    centers = [0.0, T / 3.0, 2.0 * T / 3.0] if interleave == "120deg" else [0.0, 0.0, 0.0]
    ev = _events(T, d, centers)
    # an event-aligned grid: the uniform samples plus every switching instant (twice, for the step)
    tu = np.linspace(0.0, T, n + 1)
    te = np.array([e[0] for e in ev])
    t = np.unique(np.concatenate((tu, te)))
    # the switching state just after each grid point (piecewise constant between points): evaluate at midpoints
    tm = 0.5 * (t[:-1] + t[1:])
    s = np.vstack([_upper_on(tm, T, d, c) for c in centers])               # (3, m)
    vk = V_c - V_b * s - R_ph * Ik                                          # (3, m), mean 0 by construction
    v0 = vk.mean(axis=0)
    va = (2.0 / 3.0) * (vk[0] - 0.5 * (vk[1] + vk[2]))
    vb = (vk[1] - vk[2]) / SQ3
    c, s_ = math.cos(theta_e), math.sin(theta_e)
    vd = va * c + vb * s_
    vq = -va * s_ + vb * c

    def integ(vseg, L):
        # value at the grid points from the piecewise-constant segment voltages
        i = np.concatenate(([0.0], np.cumsum(vseg * np.diff(t)))) / L
        i -= (t - t[0]) / T * (i[-1] - i[0])
        mean = np.sum(0.5 * (i[:-1] + i[1:]) * np.diff(t)) / T
        return i - mean
    i0, idd, iqq = integ(v0, L0), integ(vd, Ld), integ(vq, Lq)
    ial = idd * c - iqq * s_
    ibe = idd * s_ + iqq * c
    ia = Ik + ial + i0
    ib = Ik - 0.5 * ial + 0.5 * SQ3 * ibe + i0
    ic = Ik - 0.5 * ial - 0.5 * SQ3 * ibe + i0
    ph = np.vstack((ia, ib, ic))
    # states at the grid points (for losses): the state of the segment that starts there
    s_pts = np.hstack((s, s[:, :1]))
    return {"t": t, "T": T, "d_upper": d, "d_lower": 1.0 - d, "phase": ph, "i0": i0, "id": idd, "iq": iqq,
            "neutral": ph.sum(axis=0), "upper_state": s_pts, "events": ev, "centers": centers, "I_phase_dc": Ik}


def _avg(t, y):
    """Exact time average of a piecewise-linear sample set (trapezoid)."""
    dt = np.diff(t)
    return float(np.sum(0.5 * (y[:-1] + y[1:]) * dt) / (t[-1] - t[0]))


def _rms(t, y):
    """Exact RMS of a piecewise-linear signal."""
    dt = np.diff(t)
    a, b = y[:-1], y[1:]
    return math.sqrt(float(np.sum((a * a + a * b + b * b) / 3.0 * dt) / (t[-1] - t[0])))


# --------------------------------------------------------------------------------------------- losses

def _device_losses(model, wf: dict, V_b: float, Tj: float) -> dict:
    """Heat per die of the three legs over the period (W per parallel module), from the module's own curves."""
    dev = model.device
    t, T = wf["t"], wf["T"]
    fsw = 1.0 / T
    td = model.deadtime_s
    problems = []
    ph = wf["phase"] / model.parallel
    amax = float(np.max(np.abs(ph)))
    for name, tab in (("v_on", dev.v_on), ("v_rev", dev.v_rev), ("e_on", dev.e_on), ("e_off", dev.e_off),
                      ("e_rr", dev.e_rr), ("v_channel_rev", dev.v_channel_rev)):
        if tab is None:
            continue
        ok, why = tab.covers(amax, Tj)
        if not ok:
            problems.append(f"{name}: {why}")
    k, knote = dev.energy_scale(V_b)
    if k is None:
        problems.append(knote)
    legs = []
    dt = np.diff(t)
    for leg in range(3):
        i = ph[leg]                          # + = from the winding into the leg (charging)
        a = np.abs(i)
        su = wf["upper_state"][leg][:-1] > 0.5   # per SEGMENT: the state is constant on [t_j, t_j+1]
        lo = ~su
        von = dev.v_on.eval(a, Tj)
        vrev = dev.v_rev.eval(a, Tj)
        vch = dev.v_channel_rev.eval(a, Tj) if dev.v_channel_rev is not None else von

        def seg_avg(pw, mask_seg, sign):
            """Average over the period of pw (at the points) on the segments in mask_seg where the current has
            ``sign`` (evaluated per end point: a segment crossing zero contributes its own-sign ends)."""
            l, r = pw[:-1], pw[1:]
            il, ir = i[:-1], i[1:]
            l = np.where(np.sign(il) == sign, l, 0.0)
            r = np.where(np.sign(ir) == sign, r, 0.0)
            return float(np.sum(np.where(mask_seg, 0.5 * (l + r) * dt, 0.0)) / T)
        p_lo_sw = seg_avg(von * a, lo, 1)            # lower switch forward (charging)
        p_up_sw = seg_avg(von * a, su, -1)           # upper switch forward (discharging)
        if dev.technology == "IGBT":
            p_up_rev = seg_avg(vrev * a, su, 1)
            p_lo_rev = seg_avg(vrev * a, lo, -1)
            dead_up = dead_lo = 0.0
        else:
            # the channel conducts the reverse current except in the two dead times per period (body diode)
            frac = min(1.0, 2.0 * td * fsw)
            up_on = max(wf["d_upper"], 1e-12)
            lo_on = max(wf["d_lower"], 1e-12)
            w_bd_up, w_bd_lo = min(1.0, frac / up_on), min(1.0, frac / lo_on)
            p_up_rev = seg_avg(((1 - w_bd_up) * vch + w_bd_up * vrev) * a, su, 1)
            p_lo_rev = seg_avg(((1 - w_bd_lo) * vch + w_bd_lo * vrev) * a, lo, -1)
            dead_up, dead_lo = w_bd_up, w_bd_lo
        cond = {"lower_switch": p_lo_sw, "upper_switch": p_up_sw, "upper_reverse": p_up_rev,
                "lower_reverse": p_lo_rev}
        sw = {"lower_switch": 0.0, "upper_switch": 0.0, "upper_recovery": 0.0, "lower_recovery": 0.0}
        events = []
        for (te, kk, kind) in wf["events"]:
            if kk != leg:
                continue
            ie = float(np.interp(te, t, i))
            ae = abs(ie)
            if k is None:
                continue
            Eon = float(dev.e_on.eval(np.array([ae]), Tj)[0]) * k
            Eoff = float(dev.e_off.eval(np.array([ae]), Tj)[0]) * k
            Err = (float(dev.e_rr.eval(np.array([ae]), Tj)[0]) * k
                   if (dev.e_rr is not None and dev.energy_basis == "per_device") else 0.0)
            if kind == "down":               # upper path ends, lower turns on
                if ie > 0:                   # charging: lower switch hard on, upper diode recovers
                    sw["lower_switch"] += Eon * fsw
                    sw["upper_recovery"] += Err * fsw
                else:                        # discharging: upper switch hard off
                    sw["upper_switch"] += Eoff * fsw
            else:                            # lower ends, upper path starts
                if ie > 0:                   # charging: lower switch hard off
                    sw["lower_switch"] += Eoff * fsw
                else:                        # discharging: upper switch hard on, lower diode recovers
                    sw["upper_switch"] += Eon * fsw
                    sw["lower_recovery"] += Err * fsw
            events.append({"t_us": te * 1e6, "kind": kind, "i_A": ie})
        if dev.technology == "IGBT":
            dies = {"upper_igbt": cond["upper_switch"] + sw["upper_switch"],
                    "upper_diode": cond["upper_reverse"] + sw["upper_recovery"],
                    "lower_igbt": cond["lower_switch"] + sw["lower_switch"],
                    "lower_diode": cond["lower_reverse"] + sw["lower_recovery"]}
        elif dev.reverse_path == "external_diode":
            dies = {"upper_mosfet": cond["upper_switch"] + sw["upper_switch"] + (1 - dead_up) * cond["upper_reverse"],
                    "upper_diode": dead_up * cond["upper_reverse"] + sw["upper_recovery"],
                    "lower_mosfet": cond["lower_switch"] + sw["lower_switch"] + (1 - dead_lo) * cond["lower_reverse"],
                    "lower_diode": dead_lo * cond["lower_reverse"] + sw["lower_recovery"]}
        else:
            dies = {"upper_mosfet": cond["upper_switch"] + sw["upper_switch"] + cond["upper_reverse"]
                    + sw["upper_recovery"],
                    "lower_mosfet": cond["lower_switch"] + sw["lower_switch"] + cond["lower_reverse"]
                    + sw["lower_recovery"]}
        legs.append({"conduction_W": cond, "switching_W": sw, "dies_W": dies, "events": events})
    if any(np.any(np.isnan(dev.v_on.eval(np.abs(wf["phase"][j]) / model.parallel, Tj))) for j in range(3)):
        problems.append("conduction curve not given down to the smallest instantaneous current")
    return {"legs": legs, "problems": problems, "energy_scaling": knote}


def _cap_current(wf: dict, capacitor, source) -> dict:
    """The DC-rail current of the upper paths, its AC part split between the capacitor and the source branch."""
    t, T = wf["t"], wf["T"]
    tu = np.linspace(0.0, T, 2049)[:-1]
    s = np.vstack([_upper_on(tu + 1e-15, T, wf["d_upper"], c) for c in wf["centers"]])
    ph = np.vstack([np.interp(tu, t, wf["phase"][k]) for k in range(3)])
    iP = np.sum(ph * s, axis=0)                       # into the positive rail from the legs
    ac = iP - iP.mean()
    out = {"I_rail_mean_A": float(iP.mean()), "I_rail_ac_rms_A": float(np.sqrt(np.mean(ac * ac)))}
    if capacitor is None:
        out.update(cap_rms_A=None, cap_loss_W=None, reason="no DC-link capacitor declared")
        return out
    X = np.fft.rfft(ac) / ac.size
    f = np.arange(X.size) / T
    amp_all = 2.0 * np.abs(X[1:])                      # peak amplitude per harmonic
    # the ideal-switch spectrum is used up to MODEL_BAND_CARRIERS carrier multiples (edges / ringing beyond: not
    # modelled - the same model band as the DC-link ripple analysis); the content beyond is reported
    nb = min(amp_all.size, MODEL_BAND_CARRIERS)
    amp, fh = amp_all[:nb], f[1:nb + 1]
    beyond = float(np.sqrt(np.sum(0.5 * amp_all[nb:] ** 2)))
    out["beyond_model_band_rms_A"] = beyond
    zc = capacitor.impedance(fh)
    if source is not None:
        zs = source.z(fh)
        icap = amp * np.abs(zs / (zs + zc))
    else:
        icap = amp                                     # ideal source impedance unknown: the capacitor takes it all
    esr, inside = capacitor.esr(fh)
    keep = amp > 1e-9 * max(1.0, float(amp.max()) if amp.size else 1.0)
    covered = bool(np.all(inside[keep])) if np.any(keep) else True
    loss = float(np.sum(0.5 * icap ** 2 * esr))
    out.update(cap_rms_A=float(math.sqrt(np.sum(0.5 * icap ** 2))), cap_loss_W=loss if covered else None,
               reason="" if covered else "ripple harmonics outside the capacitor's ESR table band: ESR loss not "
                                         "established", split="capacitor / source current divider"
               if source is not None else "no source impedance declared: the capacitor carries all of it")
    return out


# --------------------------------------------------------------------------------------------- one point

def _machine(drive, winding_temp_C):
    """Phase resistance, incremental L_d / L_q at zero dq current, pole pairs, torque evaluator at standstill."""
    from ..models.flux import ConstantFluxModel
    m = drive.motor
    R = m.Rs_ohm
    note = "phase resistance as supplied"
    if winding_temp_C is not None and m.rs_temperature is not None and m.reference_winding_temp_C is not None:
        dep = m.rs_temperature
        if dep.valid_C[0] <= winding_temp_C <= dep.valid_C[1]:
            R = m.Rs_ohm * (1 + dep.coeff_per_K * (winding_temp_C - m.reference_winding_temp_C))
            note = f"R at {winding_temp_C:g} degC ({dep.basis})"
    if isinstance(m.flux, ConstantFluxModel):
        return R, m.flux.Ld_H, m.flux.Lq_H, m.pole_pairs, note
    plane, _n = m.flux.plane_for(None)
    h = 1.0
    pd0, pq0, _ = plane.interpolate(np.array([0.0]), np.array([0.0]))
    pd1, _x, _ = plane.interpolate(np.array([-h]), np.array([0.0]))
    _y, pq1, _ = plane.interpolate(np.array([0.0]), np.array([h]))
    return R, float((pd0[0] - pd1[0]) / h), float((pq1[0] - pq0[0]) / h), m.pole_pairs, note


def charging_point(drive, model, path: ChargingPath, *, V_c: float, V_b: float, I_charge: float, coolant_C: float,
                   Rth_K_per_W: float, rotor_angle_deg: float = 0.0, capacitor=None, source=None,
                   winding_temp_C: float | None = None, limits: dict | None = None, n: int = 3600) -> dict:
    """One charging (I > 0) or discharging (I < 0) operating point: waveforms, losses, Tj, checks."""
    if not path.neutral_access:
        return {"status": NOT_APPLICABLE, "reason": "the machine has no neutral access: the windings cannot carry the "
                                                    "charger current (declare neutral_access with its design basis)"}
    if V_c >= V_b:
        return {"status": NOT_APPLICABLE, "reason": f"V_charger {V_c:g} V >= V_battery {V_b:g} V: no boost needed (direct "
                                                    f"DC charging, outside this analysis)"}
    if path.L0_H is None:
        return {"status": UNKNOWN, "reason": "zero-sequence inductance L0 not declared: the neutral-current ripple, the "
                                             "peak currents and the switching instants are not established"}
    R, Ld, Lq, p, rnote = _machine(drive, winding_temp_C)
    fsw = model.fsw_Hz
    wf = period_waveforms(V_c, V_b, I_charge, R, path.L0_H, Ld, Lq, math.radians(rotor_angle_deg) * p, fsw,
                          path.interleave, n)
    t = wf["t"]
    ph = wf["phase"]
    # electrothermal fixed point on the hottest die
    Tj = float(coolant_C)
    hist = []
    for _ in range(60):
        dl = _device_losses(model, wf, V_b, Tj)
        dies_all = [(f"{'abc'[j]}:{k}", v) for j, leg in enumerate(dl["legs"]) for k, v in leg["dies_W"].items()]
        hot_name, P_hot = max(dies_all, key=lambda kv: kv[1])
        Tn = coolant_C + Rth_K_per_W * P_hot
        hist.append((Tj, P_hot))
        if not math.isfinite(Tn):
            break
        if abs(Tn - Tj) < 1e-3:
            Tj = Tn
            break
        Tj = Tj + 0.7 * (Tn - Tj)
    dl = _device_losses(model, wf, V_b, Tj)
    dev_total = model.parallel * sum(sum(leg["conduction_W"].values()) + sum(leg["switching_W"].values())
                                     for leg in dl["legs"])
    established = not dl["problems"]
    Pcu = sum(R * _rms(t, ph[k]) ** 2 for k in range(3))
    neu = wf["neutral"]
    cap = _cap_current(wf, capacitor, source)
    P_in = V_c * I_charge
    losses = {"devices": dev_total if established else None, "motor_copper": Pcu,
              "dc_link_capacitor": cap.get("cap_loss_W")}
    known = sum(v for v in losses.values() if v is not None)
    P_out = P_in - known                       # power into the battery (negative when discharging)
    # torque from the dq ripple at standstill (the zero sequence makes none)
    from ..physics import DriveKernel
    from ..scenario import DcSourceLimits, Scenario
    k = DriveKernel(drive, Scenario("charging", 0.0, V_b, DcSourceLimits(), winding_temp_C=winding_temp_C))
    tq = k.evaluate(wf["id"], wf["iq"])["tem"] if k.evaluable else np.full_like(t, np.nan)
    phase_pk = float(np.max(np.abs(ph)))
    lim = limits or {}
    checks = []

    def chk(cid, text, value, limit, unit, kind="max"):
        if limit is None or value is None:
            checks.append({"id": cid, "check": text, "value": value, "limit": limit, "unit": unit,
                           "status": UNKNOWN, "reason": "limit not declared" if limit is None else "value not established"})
            return
        ok = value <= limit if kind == "max" else value >= limit
        checks.append({"id": cid, "check": text, "value": value, "limit": limit, "unit": unit,
                       "status": PASS if ok else FAIL})
    chk("charger_current", "charger current", abs(I_charge), lim.get("charger_current_max_A"), "A")
    chk("charger_power", "charger power", abs(P_in), lim.get("charger_power_max_W"), "W")
    chk("battery_power", "battery charge power", P_out if I_charge >= 0 else None, lim.get("battery_charge_power_max_W"),
        "W")
    chk("battery_current", "battery charge current", (P_out / V_b) if I_charge >= 0 else None,
        lim.get("battery_charge_current_max_A"), "A")
    chk("phase_peak", "phase peak current", phase_pk, path.phase_current_peak_max_A, "A")
    chk("neutral_rms", "neutral RMS current", _rms(t, neu), path.neutral_current_max_A, "A")
    chk("junction", "hottest junction temperature", Tj if established else None, path.Tj_max_C, "degC")
    chk("winding", "stator copper loss", Pcu, path.winding_loss_max_W, "W")
    not_checked = [c["id"] for c in checks if c.get("reason") == "limit not declared"]
    worst = FAIL if any(c["status"] == FAIL for c in checks) else (
        UNKNOWN if (not established or any(c["status"] == UNKNOWN and c["id"] not in not_checked for c in checks))
        else PASS)
    ripple_ratio = 2 * math.pi * fsw * min(path.L0_H, Ld, Lq) / R if R > 0 else math.inf
    return {
        "status": worst, "established": established, "problems": dl["problems"], "not_checked": not_checked,
        "inputs": {"V_charger_V": V_c, "V_battery_V": V_b, "I_charge_A": I_charge, "coolant_C": coolant_C,
                   "rotor_angle_deg": rotor_angle_deg, "fsw_Hz": fsw, "interleave": path.interleave,
                   "R_phase_ohm": R, "R_note": rnote, "L0_H": path.L0_H, "Ld_H": Ld, "Lq_H": Lq,
                   "technology": model.device.technology, "parallel": model.parallel, "deadtime_s": model.deadtime_s},
        "duty_upper": wf["d_upper"], "duty_lower": wf["d_lower"],
        "currents": {"phase_dc_A": wf["I_phase_dc"], "phase_peak_A": phase_pk,
                     "phase_rms_A": [_rms(t, ph[j]) for j in range(3)],
                     "phase_ripple_pp_A": [float(ph[j].max() - ph[j].min()) for j in range(3)],
                     "neutral_mean_A": _avg(t, neu), "neutral_rms_A": _rms(t, neu),
                     "neutral_ripple_pp_A": float(neu.max() - neu.min()),
                     "zero_sequence_ripple_pp_A": float(wf["i0"].max() - wf["i0"].min()),
                     "dq_ripple_pp_A": [float(wf["id"].max() - wf["id"].min()), float(wf["iq"].max() - wf["iq"].min())]},
        "dc_link": cap, "Tj_C": Tj if established else None, "hottest_die": hot_name, "Tj_history": hist,
        "legs": dl["legs"], "energy_scaling": dl["energy_scaling"],
        "losses_W": losses, "P_charger_W": P_in, "P_battery_W": P_out,
        "efficiency": (P_out / P_in if (I_charge > 0 and losses["devices"] is not None) else
                       (P_in / P_out if (I_charge < 0 and losses["devices"] is not None and P_out) else None)),
        "torque": {"mean_Nm": _avg(t, tq) if np.all(np.isfinite(tq)) else None,
                   "peak_abs_Nm": float(np.max(np.abs(tq))) if np.all(np.isfinite(tq)) else None,
                   "note": "zero-sequence current makes no torque; the d / q ripple at the rotor angle does (standstill, "
                           "held by the parking lock)"},
        "checks": checks,
        "ripple_resistance_ratio": ripple_ratio,
        "waveform": {"t_us": (t * 1e6).tolist(), "ia_A": ph[0].tolist(), "ib_A": ph[1].tolist(), "ic_A": ph[2].tolist(),
                     "neutral_A": neu.tolist(), "upper_a": wf["upper_state"][0].tolist(),
                     "upper_b": wf["upper_state"][1].tolist(), "upper_c": wf["upper_state"][2].tolist(),
                     "torque_Nm": tq.tolist() if np.all(np.isfinite(tq)) else None},
        "notes": [f"ripple integrated without the phase resistance (omega_sw L / R = {ripple_ratio:.3g})",
                  "the legs share the charger current equally (current balancing assumed); ideal switches for the "
                  "waveforms (device drops enter the losses, not the duty)",
                  "the declared module Rth (hottest position) is applied to the hottest die; die-to-die coupling not "
                  "modelled", f"switching energies: {dl['energy_scaling']}"],
    }


# --------------------------------------------------------------------------------------------- capability

def _limiting(res: dict) -> list:
    return [c["id"] for c in res.get("checks", []) if c["status"] == FAIL]


def capability(drive, model, path, *, V_c: float, V_b: float, coolant_C: float, Rth_K_per_W: float, limits: dict,
               capacitor=None, source=None, winding_temp_C=None, rotor_angle_deg: float = 0.0, I_hi: float | None = None,
               tol_A: float = 0.5) -> dict:
    """The largest charger current (and power) at (V_c, V_b) that keeps every declared limit."""
    kw = dict(V_c=V_c, V_b=V_b, coolant_C=coolant_C, Rth_K_per_W=Rth_K_per_W, rotor_angle_deg=rotor_angle_deg,
              capacitor=capacitor, source=source, winding_temp_C=winding_temp_C, limits=limits, n=1200)
    probe = charging_point(drive, model, path, I_charge=1.0, **kw)
    if probe["status"] in (NOT_APPLICABLE,) or ("checks" not in probe):
        return {"V_charger_V": V_c, "V_battery_V": V_b, "status": probe["status"], "reason": probe.get("reason"),
                "I_max_A": None, "P_max_W": None, "limiting": []}
    if not probe["established"]:
        return {"V_charger_V": V_c, "V_battery_V": V_b, "status": UNKNOWN, "reason": "; ".join(probe["problems"]),
                "I_max_A": None, "P_max_W": None, "limiting": []}
    hi = I_hi or (limits.get("charger_current_max_A") or 3.0 * (path.phase_current_peak_max_A or 600.0))
    not_checked = [c["id"] for c in probe["checks"] if c["status"] == UNKNOWN]

    def ok(I):
        r = charging_point(drive, model, path, I_charge=I, **kw)
        if not r["established"] or "checks" not in r:
            return None, r
        return not any(c["status"] == FAIL for c in r["checks"]), r
    good, r_hi = ok(hi)
    if good is None:
        return {"V_charger_V": V_c, "V_battery_V": V_b, "status": UNKNOWN,
                "reason": "; ".join(r_hi.get("problems") or [r_hi.get("reason", "")]), "I_max_A": None,
                "P_max_W": None, "limiting": []}
    if good:
        best, why = hi, ["search bound"]
    else:
        lo, best_r = 0.0, None
        a, b = lo, hi
        why = _limiting(r_hi)
        while b - a > tol_A:
            m = 0.5 * (a + b)
            g, r = ok(m)
            if g is None:
                b = m
                continue
            if g:
                a, best_r = m, r
            else:
                b, why = m, _limiting(r)
        best = a
    P = V_c * best
    return {"V_charger_V": V_c, "V_battery_V": V_b, "status": PASS if best > 0 else FAIL, "I_max_A": best,
            "P_max_W": P, "limiting": why, "not_checked": not_checked}


def capability_map(drive, model, path, *, V_cs, V_bs, **kw) -> dict:
    rows = []
    with progress.span(len(V_cs) * len(V_bs), "charging capability") as sp:
        for vc in V_cs:
            for vb in V_bs:
                sp.step()
                rows.append(capability(drive, model, path, V_c=float(vc), V_b=float(vb), **kw))
    return {"V_chargers_V": list(map(float, V_cs)), "V_batteries_V": list(map(float, V_bs)), "rows": rows}
