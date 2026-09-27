"""Conducted EMI of the HV DC port: source -> path -> receiver (handoff section 8.7 / 11, P1-C; OEW addendum 5.1-5.2).

What this module is - and is not:

* the **requirement profile** (standard / edition, customer revision, class or approved curve, port, method,
  detector, RBW, artificial network, fixture, operating condition, design reserve) is an input; a profile missing a
  decisive field is REQUIREMENT_INCOMPLETE and nothing is judged against it.  No standard limit values are built
  in: the limit curve is entered from the approved source;
* **source**: the exact line spectrum of the ideal-switch PWM with declared (not estimated) rise / fall times and
  dead time (edge timing follows the current sign), from a sum over every switching edge of one synchronous
  fundamental period - common-mode switch-node voltage and the inverter DC input current, with their phases;
* **path**: a declared lumped network (DC link with ESR / ESL, Y capacitors with mounting inductance, switch-node /
  motor-to-chassis capacitance, harness, optional common-mode choke as coupled inductors, the artificial network) is
  solved by nodal analysis at every line, both sources together (coherent, phases kept); CM and DM are not forced
  to be independent;
* **receiver**: lines inside the resolution bandwidth are summed in magnitude (an upper estimate of a peak-detector
  reading of these lines); quasi-peak / average weighting, IF filter shape and dwell are not modelled - the result
  is labelled a line-sum estimate, never a CISPR reading;
* **claims**: without declared calibration evidence the result is SCREENING (UNKNOWN): margins, the required
  attenuation A = max(0, E_U + M_d - L) and the dominant CM / DM path per band, "predicted exceedance" instead of
  FAIL.  A measured receiver trace is judged PASS / FAIL / INDETERMINATE against the same profile (test
  representativeness and approval stay separate).

Conventions (two conductors, currents in the same spatial direction):
v_CM = (v+ + v-)/2, v_DM = v+ - v-, i_CM = i+ + i-, i_DM = (i+ - i-)/2, so v+ i+ + v- i- = v_CM i_CM + v_DM i_DM.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..errors import InputValidationError
from ..models.flux import _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

TWO_PI = 2.0 * math.pi
PROFILE_FIELDS = ("standard", "edition", "customer_revision", "curve_id", "port", "method", "detector", "rbw_Hz",
                  "network", "fixture", "operating_condition")
DETECTORS = ("peak", "quasi_peak", "average")


# --------------------------------------------------------------------------------------------- conventions

def cm_dm(v_plus, v_minus, i_plus=None, i_minus=None) -> dict:
    """v_CM = (v+ + v-)/2, v_DM = v+ - v-, i_CM = i+ + i-, i_DM = (i+ - i-)/2 (power-consistent pair)."""
    out = {"v_CM": 0.5 * (np.asarray(v_plus) + np.asarray(v_minus)), "v_DM": np.asarray(v_plus) - np.asarray(v_minus)}
    if i_plus is not None and i_minus is not None:
        out["i_CM"] = np.asarray(i_plus) + np.asarray(i_minus)
        out["i_DM"] = 0.5 * (np.asarray(i_plus) - np.asarray(i_minus))
    return out


def db_uv(v_amp_rms) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(np.asarray(v_amp_rms, dtype=float), 1e-30) / 1e-6)


# --------------------------------------------------------------------------------------------- requirement profile

@dataclass(frozen=True)
class LimitCurve:
    """Approved limit points (f_Hz, level) in dBuV or dBuA for one detector; log-frequency linear interpolation,
    steps by repeating a frequency; nothing outside the declared points (no extrapolation)."""

    points: tuple
    unit: str = "dBuV"
    detector: str = "peak"
    source: str = ""

    def __post_init__(self):
        pts = tuple((float(f), float(v)) for f, v in self.points)
        if len(pts) < 2 or any(f <= 0 for f, _ in pts) or any(b[0] < a[0] for a, b in zip(pts, pts[1:])):
            raise InputValidationError("limit curve needs >= 2 points with non-decreasing frequency > 0", field="limit")
        if self.unit not in ("dBuV", "dBuA"):
            raise InputValidationError("limit unit must be dBuV or dBuA", field="limit.unit")
        if self.detector not in DETECTORS:
            raise InputValidationError(f"detector must be one of {DETECTORS}", field="limit.detector")
        if not self.source.strip():
            raise InputValidationError("a limit curve needs its source (standard / customer document, revision)",
                                       field="limit.source")
        object.__setattr__(self, "points", pts)

    def at(self, f) -> np.ndarray:
        f = np.atleast_1d(np.asarray(f, dtype=float))
        out = np.full(f.shape, np.nan)
        pts = self.points
        for i, x in enumerate(f):
            if x < pts[0][0] or x > pts[-1][0]:
                continue
            best = math.inf
            for (f0, l0), (f1, l1) in zip(pts, pts[1:]):
                if f0 <= x <= f1:
                    if f1 == f0:
                        best = min(best, l0, l1)
                    else:
                        w = (math.log10(x) - math.log10(f0)) / (math.log10(f1) - math.log10(f0))
                        best = min(best, l0 + w * (l1 - l0))
            out[i] = best if math.isfinite(best) else np.nan
        return out


@dataclass(frozen=True)
class EmiProfile:
    fields: dict = field(default_factory=dict)
    limit: LimitCurve | None = None
    design_reserve_dB: float = 0.0

    def missing(self) -> list:
        miss = [k for k in PROFILE_FIELDS if not str(self.fields.get(k, "")).strip()]
        if self.limit is None:
            miss.append("limit curve")
        elif str(self.fields.get("detector", "")).strip() and self.fields.get("detector") != self.limit.detector:
            miss.append(f"detector mismatch (profile {self.fields.get('detector')}, curve {self.limit.detector})")
        return miss


# --------------------------------------------------------------------------------------------- source

@dataclass(frozen=True)
class SwitchingSource:
    Vdc_V: float
    I_pk_A: float
    beta_rad: float              # phase-current angle in the electrical frame: i_a = I cos(theta + beta)
    m: float                     # modulation index V_pk / (Vdc / 2)
    alpha_rad: float             # voltage angle: v_a = V cos(theta + alpha)
    fe_Hz: float
    fsw_Hz: float
    t_rise_s: float
    t_fall_s: float
    t_dead_s: float = 0.0
    modulation: str = "svpwm"
    basis: str = ""              # where tr / tf / dead time come from (measured, gate setting ...)

    def __post_init__(self):
        for name in ("Vdc_V", "fe_Hz", "fsw_Hz", "t_rise_s", "t_fall_s"):
            if _finite(name, getattr(self, name)) <= 0:
                raise InputValidationError(f"{name} must be > 0 (rise / fall times are declared, never estimated "
                                           f"from power or gate resistance)", field=name)
        if _finite("t_dead_s", self.t_dead_s) < 0:
            raise InputValidationError("dead time must be >= 0", field="t_dead_s")
        if self.modulation not in ("svpwm", "spwm"):
            raise InputValidationError("modulation must be svpwm or spwm", field="modulation")


def _duties(theta, m, alpha, modulation):
    v = np.vstack([0.5 * m * np.cos(theta + alpha - TWO_PI * k / 3.0) for k in range(3)])
    if modulation == "svpwm":
        v = v - 0.5 * (v.max(axis=0) + v.min(axis=0))
    return 0.5 + v


def pwm_edges(src: SwitchingSource) -> dict:
    """Every switching edge of one fundamental period (synchronous carrier, symmetric regular sampling at the carrier
    centre; dead time shifts the edge according to the current sign)."""
    ratio = max(1, int(round(src.fsw_Hz / src.fe_Hz)))
    T = 1.0 / src.fe_Hz
    Ts = T / ratio
    tc = (np.arange(ratio) + 0.5) * Ts
    th = TWO_PI * src.fe_Hz * tc
    d = _duties(th, src.m, src.alpha_rad, src.modulation)
    over = bool(np.any(d < -1e-9) or np.any(d > 1 + 1e-9))
    d = np.clip(d, 0.0, 1.0)
    legs = []
    for k in range(3):
        for j in range(ratio):
            dk = d[k, j]
            if dk <= 0.0 or dk >= 1.0:
                continue                                   # clamped for this carrier period: no edges
            tr_cmd = tc[j] - 0.5 * dk * Ts
            tf_cmd = tc[j] + 0.5 * dk * Ts
            i_r = src.I_pk_A * math.cos(TWO_PI * src.fe_Hz * tr_cmd + src.beta_rad - TWO_PI * k / 3.0)
            i_f = src.I_pk_A * math.cos(TWO_PI * src.fe_Hz * tf_cmd + src.beta_rad - TWO_PI * k / 3.0)
            t_up = tr_cmd + (src.t_dead_s if i_r > 0 else 0.0)            # i > 0: lower diode holds the pole low
            t_dn = tf_cmd + (src.t_dead_s if i_f < 0 else 0.0)            # i < 0: upper diode holds it high
            legs.append((k, t_up, +1, src.t_rise_s, i_r))
            legs.append((k, t_dn, -1, src.t_fall_s, i_f))
    return {"edges": legs, "period_s": T, "carrier_ratio": ratio, "fsw_used_Hz": ratio * src.fe_Hz,
            "overmodulation": over}


def _edge_lines(t0, tau, dv, freqs, T):
    """One-sided complex line amplitudes (peak) of a periodic piecewise-linear signal made of ramps.

    c_n = 1/(j 2 pi f_n T) * sum dv_e exp(-j 2 pi f_n (t_e + tau_e/2)) sinc(f_n tau_e); line amplitude = 2 c_n.
    """
    f = np.asarray(freqs, dtype=float)[:, None]
    ph = np.exp(-1j * TWO_PI * f * (np.asarray(t0)[None, :] + 0.5 * np.asarray(tau)[None, :]))
    sinc = np.sinc(f * np.asarray(tau)[None, :])
    s = (ph * sinc * np.asarray(dv)[None, :]).sum(axis=1)
    return 2.0 * s / (1j * TWO_PI * f[:, 0] * T)


def source_lines(src: SwitchingSource, freqs) -> dict:
    """Complex line amplitudes (peak) at the given harmonics of fe: switch-node CM voltage (poles averaged, relative
    to the DC midpoint) and the inverter DC input current (positive into the inverter from DC+).

    The DC current is taken as steps of +-i_k(t_edge) at the edges (the slow variation of i_k between edges is
    neglected: an error of order (di/dt) / (2 pi f)^2, negligible in the conducted-emission band, visible near fsw).
    """
    e = pwm_edges(src)
    T = e["period_s"]
    if not e["edges"]:
        z = np.zeros(len(freqs), dtype=complex)
        return {"v_cm": z, "i_dm": z, **{k: e[k] for k in ("carrier_ratio", "fsw_used_Hz", "overmodulation")}}
    k, t0, sgn, tau, cur = (np.array(x) for x in zip(*e["edges"]))
    v_cm = _edge_lines(t0, tau, sgn * src.Vdc_V / 3.0, freqs, T)
    i_dm = _edge_lines(t0, tau, sgn * cur, freqs, T)
    return {"v_cm": v_cm, "i_dm": i_dm, "carrier_ratio": e["carrier_ratio"], "fsw_used_Hz": e["fsw_used_Hz"],
            "overmodulation": e["overmodulation"], "edges": len(t0)}


def sampled_waveforms(src: SwitchingSource, n_per_carrier: int = 400) -> dict:
    """Independent time-domain reconstruction of the same edges (used to cross-check the edge-sum spectrum)."""
    e = pwm_edges(src)
    T = e["period_s"]
    n = e["carrier_ratio"] * n_per_carrier
    t = (np.arange(n) + 0.5) * T / n
    poles = np.zeros((3, n))
    iinv = np.zeros(n)
    # level of each leg: start low at t = 0 unless the leg is clamped high; accumulate ramps
    d0 = _duties(np.array([TWO_PI * src.fe_Hz * (0.5 * T / e["carrier_ratio"])]), src.m, src.alpha_rad, src.modulation)
    for kk in range(3):
        if d0[kk, 0] >= 1.0:
            poles[kk] += 1.0
    ia = [src.I_pk_A * np.cos(TWO_PI * src.fe_Hz * t + src.beta_rad - TWO_PI * kk / 3.0) for kk in range(3)]
    for (kk, t0, sgn, tau, cur) in e["edges"]:
        ramp = np.clip((t - t0) / tau, 0.0, 1.0) if tau > 0 else (t >= t0).astype(float)
        poles[kk] += sgn * ramp
    v_cm = src.Vdc_V * (poles.mean(axis=0) - 0.5)
    for kk in range(3):
        iinv += np.clip(poles[kk], 0, 1) * ia[kk]
    return {"t_s": t, "v_cm_V": v_cm, "i_inv_A": iinv, "poles": poles, "period_s": T}


# --------------------------------------------------------------------------------------------- path network

@dataclass(frozen=True)
class HvNetwork:
    """Declared lumped HV network between the inverter DC terminals and the artificial network (per line)."""

    C_dc_F: float
    ESR_dc_ohm: float = 0.0
    ESL_dc_H: float = 0.0
    C_y_F: float = 0.0               # per rail to chassis (0 = none)
    L_y_H: float = 0.0
    R_y_ohm: float = 0.0
    C_par_F: float = 0.0             # switch-node + motor/cable to chassis (CM source coupling)
    R_par_ohm: float = 0.0
    L_par_H: float = 0.0
    R_h_ohm: float = 0.0             # harness per line
    L_h_H: float = 0.0
    L_ch_H: float = 0.0              # CM choke winding inductance (0 = none)
    k_ch: float = 0.0                # choke coupling (L_dm leakage = (1 - k) L_ch)
    an_L_H: float = 5e-6             # artificial network (declare per your standard)
    an_C_coup_F: float = 0.1e-6
    an_R_meas_ohm: float = 50.0
    an_R_par_ohm: float = 1000.0
    an_C_sup_F: float = 1e-6
    R_bat_ohm: float = 0.01          # supply-side source impedance between the AN supply terminals
    L_bat_H: float = 0.0
    basis: str = ""
    validated_up_to_Hz: float | None = None

    def __post_init__(self):
        for name in ("C_dc_F", "an_L_H", "an_C_coup_F", "an_R_meas_ohm", "an_C_sup_F"):
            if _finite(name, getattr(self, name)) <= 0:
                raise InputValidationError(f"{name} must be > 0", field=name)
        for name in ("ESR_dc_ohm", "ESL_dc_H", "C_y_F", "L_y_H", "R_y_ohm", "C_par_F", "R_par_ohm", "L_par_H",
                     "R_h_ohm", "L_h_H", "L_ch_H", "an_R_par_ohm", "R_bat_ohm", "L_bat_H"):
            if _finite(name, getattr(self, name)) < 0:
                raise InputValidationError(f"{name} must be >= 0", field=name)
        if not (0.0 <= _finite("k_ch", self.k_ch) < 1.0):
            raise InputValidationError("choke coupling must satisfy 0 <= k < 1", field="k_ch")


def solve_network(net: HvNetwork, f, v_cm, i_dm) -> dict:
    """Nodal analysis at every frequency with both sources active (coherent).

    Nodes: 1 inv+, 2 inv-, 3 AN+ (EUT side), 4 AN-, 5 meas+, 6 meas-, 7 sup+, 8 sup-, 9 switch-node CM source;
    ground = chassis.  The CM source is referenced to the DC midpoint without creating a DM path:
    V9 - (V1 + V2)/2 = v_cm, its current returns half into each rail (equal split: an assumption about the HF
    symmetry of the DC link).  Extra unknowns: the CM source current and the two harness / choke branch currents
    (coupled inductors).  Returns the measurement-port voltages and branch currents.
    """
    f = np.atleast_1d(np.asarray(f, dtype=float))
    w = TWO_PI * f
    jw = 1j * w
    nf = f.size
    N = 9
    X = N + 3                                   # + i_src(cm), i_line+, i_line-
    A = np.zeros((nf, X, X), dtype=complex)
    b = np.zeros((nf, X), dtype=complex)

    def adm(n1, n2, yv):
        for n in (n1, n2):
            if n:
                A[:, n - 1, n - 1] += yv
        if n1 and n2:
            A[:, n1 - 1, n2 - 1] -= yv
            A[:, n2 - 1, n1 - 1] -= yv

    z_dc = net.ESR_dc_ohm + jw * net.ESL_dc_H + 1.0 / (jw * net.C_dc_F)
    adm(1, 2, 1.0 / z_dc)
    if net.C_y_F > 0:
        z_y = net.R_y_ohm + jw * net.L_y_H + 1.0 / (jw * net.C_y_F)
        adm(1, 0, 1.0 / z_y)
        adm(2, 0, 1.0 / z_y)
    if net.C_par_F > 0:
        z_p = net.R_par_ohm + jw * net.L_par_H + 1.0 / (jw * net.C_par_F)
        adm(9, 0, 1.0 / z_p)
    else:
        adm(9, 0, np.full(nf, 1e-12))
    # AN per line: EUT terminal -> C_coup -> measurement port (R_meas || R_par); EUT terminal -> L_an -> supply node
    for eut, meas, sup in ((3, 5, 7), (4, 6, 8)):
        adm(eut, meas, jw * net.an_C_coup_F)
        adm(meas, 0, np.full(nf, 1.0 / net.an_R_meas_ohm + (1.0 / net.an_R_par_ohm if net.an_R_par_ohm > 0 else 0.0)))
        adm(eut, sup, 1.0 / (jw * net.an_L_H))
        adm(sup, 0, jw * net.an_C_sup_F)
    z_bat = net.R_bat_ohm + jw * net.L_bat_H
    adm(7, 8, 1.0 / np.where(np.abs(z_bat) > 0, z_bat, 1e-9))
    # inverter DM current: out of node 1 into the inverter, back into node 2
    b[:, 0] -= i_dm
    b[:, 1] += i_dm
    # CM source: current i_s from the DC midpoint (half from each rail) through the source into node 9
    s = N
    A[:, 0, s] += 0.5          # leaves node 1
    A[:, 1, s] += 0.5          # leaves node 2
    A[:, 8, s] -= 1.0          # enters node 9
    A[:, s, 8] += 1.0
    A[:, s, 0] -= 0.5
    A[:, s, 1] -= 0.5
    b[:, s] = v_cm
    # harness + choke: coupled branches 1 -> 3 (i1) and 2 -> 4 (i2)
    L1 = net.L_h_H + net.L_ch_H
    M = net.k_ch * net.L_ch_H
    for idx, (na, nb) in enumerate(((1, 3), (2, 4))):
        col = N + 1 + idx
        A[:, na - 1, col] += 1.0     # current leaves na
        A[:, nb - 1, col] -= 1.0     # enters nb
        A[:, col, na - 1] += 1.0
        A[:, col, nb - 1] -= 1.0
        A[:, col, col] -= net.R_h_ohm + jw * L1
        A[:, col, N + 1 + (1 - idx)] -= jw * M
    x = np.linalg.solve(A, b[..., None])[..., 0]
    V = x[:, :N]
    return {"f_Hz": f, "v_meas_plus": V[:, 4], "v_meas_minus": V[:, 5], "i_line_plus": x[:, N + 1],
            "i_line_minus": x[:, N + 2], "i_cm_source": x[:, N], "v_inv_plus": V[:, 0], "v_inv_minus": V[:, 1]}


# --------------------------------------------------------------------------------------------- receiver + screening

def receiver_grid(f_lo: float, f_hi: float, n: int = 240) -> np.ndarray:
    return np.geomspace(f_lo, f_hi, n)


def line_sum_estimate(grid, rbw_Hz: float, fe_Hz: float, value_fn) -> tuple[np.ndarray, int]:
    """Sum of |line| (peak amplitudes) of every harmonic of fe inside +-RBW/2 around each grid frequency, reported as
    an RMS-calibrated reading (/sqrt2).  value_fn(freqs) -> complex peak amplitudes."""
    out = np.zeros(len(grid))
    total = 0
    for i, fr in enumerate(grid):
        n0 = max(1, int(math.ceil((fr - 0.5 * rbw_Hz) / fe_Hz)))
        n1 = int(math.floor((fr + 0.5 * rbw_Hz) / fe_Hz))
        if n1 < n0:
            n1 = n0 = max(1, int(round(fr / fe_Hz)))
        freqs = np.arange(n0, n1 + 1) * fe_Hz
        total += freqs.size
        out[i] = float(np.sum(np.abs(value_fn(freqs)))) / math.sqrt(2.0)
    return out, total


def conducted_emission_screening(src: SwitchingSource, net: HvNetwork, profile: EmiProfile, f_lo: float = 150e3,
                                 f_hi: float = 30e6, n_grid: int = 160, calibration: dict | None = None) -> dict:
    """Line-sum estimate of the AN measurement-port voltages, margins and the required attenuation (screening
    unless ``calibration`` declares validated evidence and an uncertainty bound)."""
    rbw = float(profile.fields.get("rbw_Hz") or 9e3)
    grid = receiver_grid(f_lo, f_hi, n_grid)
    cache = {}

    def lines(freqs):
        key = (float(freqs[0]), freqs.size)
        if key not in cache:
            sl = source_lines(src, freqs)
            nw = solve_network(net, freqs, sl["v_cm"], sl["i_dm"])
            both = cm_dm(nw["v_meas_plus"], nw["v_meas_minus"])
            cm_only = solve_network(net, freqs, sl["v_cm"], 0 * sl["i_dm"])
            dm_only = solve_network(net, freqs, 0 * sl["v_cm"], sl["i_dm"])
            cache[key] = (nw["v_meas_plus"], nw["v_meas_minus"], both["v_CM"], both["v_DM"],
                          cm_only["v_meas_plus"], dm_only["v_meas_plus"])
        return cache[key]
    vals = {}
    for name, idx in (("plus", 0), ("minus", 1), ("cm", 2), ("dm", 3), ("from_cm_source", 4), ("from_dm_source", 5)):
        est, nlines = line_sum_estimate(grid, rbw, src.fe_Hz, lambda fr, i=idx: lines(fr)[i])
        vals[name] = est
    E = db_uv(np.maximum(vals["plus"], vals["minus"]))
    unc = float((calibration or {}).get("uncertainty_dB") or 0.0)
    EU = E + unc
    L = profile.limit.at(grid) if profile.limit is not None else np.full(grid.size, np.nan)
    Md = profile.design_reserve_dB
    margin = L - Md - EU
    A_req = np.where(np.isfinite(L), np.maximum(0.0, EU + Md - L), np.nan)
    dom = np.where(vals["from_cm_source"] >= vals["from_dm_source"], "CM", "DM")
    missing = profile.missing()
    valid_hi = net.validated_up_to_Hz
    notes = ["line-sum estimate of the modelled lines: not a CISPR receiver reading (QP / AV weighting, IF filter "
             "shape and dwell not modelled)", "ideal-switch edges with declared rise / fall and dead time; ringing, "
             "reverse recovery and gate-loop effects are outside this source"]
    calibrated = bool(calibration and str(calibration.get("evidence", "")).strip())
    q = "conducted emission at the declared HV port within the approved limit and design reserve"
    scope = (f"source-path-receiver screening {f_lo / 1e6:g}-{f_hi / 1e6:g} MHz, RBW {rbw / 1e3:g} kHz; "
             f"{'calibrated (declared evidence)' if calibrated else 'unvalidated lumped network'}")
    band_note = ""
    if valid_hi is not None and f_hi > valid_hi:
        band_note = f"network validated only up to {valid_hi / 1e6:g} MHz: nothing is claimed above"
    if missing:
        claim = Claim("conducted_emission", Status.UNKNOWN, q, scope, reasons=(Reason.REQUIREMENT_INCOMPLETE,),
                      detail="requirement profile incomplete: " + ", ".join(missing) + " (no PASS / FAIL)")
    else:
        worst = float(np.nanmin(margin)) if np.any(np.isfinite(margin)) else None
        exceed = np.isfinite(margin) & (margin < 0)
        if not calibrated:
            detail = (f"SCREENING - predicted exceedance up to {float(np.nanmax(A_req)):.1f} dB in "
                      f"{int(exceed.sum())} of {grid.size} grid points" if exceed.any() else
                      f"SCREENING - screening margin >= {worst:.1f} dB (not a pass: unvalidated source / path / "
                      f"receiver)")
            claim = Claim("conducted_emission", Status.UNKNOWN, q, scope, reasons=(Reason.SCREENING_ONLY,),
                          evidence=(Evidence.make(EvidenceKind.SAMPLED, "line-sum screening on a log grid"),),
                          qualifiers=("predicted exceedance" if exceed.any() else "screening margin",),
                          detail=detail + ("; " + band_note if band_note else ""))
        elif exceed.any():
            claim = Claim("conducted_emission", Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,),
                          evidence=(Evidence.make(EvidenceKind.NUMERICAL_WITNESS, str(calibration.get("evidence"))),),
                          detail=f"calibrated prediction exceeds the limit - reserve by up to {float(np.nanmax(A_req)):.1f} dB")
        elif worst is not None and worst >= 0 and not band_note:
            claim = Claim("conducted_emission", Status.FEASIBLE, q, scope,
                          evidence=(Evidence.make(EvidenceKind.VALIDATED_DOMAIN, str(calibration.get("evidence"))),),
                          qualifiers=("within the declared model / domain; not a certification pass",),
                          detail=f"minimum margin {worst:.1f} dB incl. {unc:g} dB model uncertainty")
        else:
            claim = Claim("conducted_emission", Status.UNKNOWN, q, scope, reasons=(Reason.UNCERTAINTY_OVERLAP,),
                          detail=band_note or "margin not established over the band")
    return {"grid_Hz": grid, "E_dBuV": E, "E_upper_dBuV": EU, "plus_dBuV": db_uv(vals["plus"]),
            "minus_dBuV": db_uv(vals["minus"]), "cm_dBuV": db_uv(vals["cm"]), "dm_dBuV": db_uv(vals["dm"]),
            "from_cm_source_dBuV": db_uv(vals["from_cm_source"]), "from_dm_source_dBuV": db_uv(vals["from_dm_source"]),
            "dominant_source": dom, "limit_dBuV": L, "margin_dB": margin, "required_attenuation_dB": A_req,
            "claim": claim.to_dict(), "profile_missing": missing, "rbw_Hz": rbw, "notes": notes,
            "carrier_ratio": source_lines(src, [src.fe_Hz])["carrier_ratio"], "calibrated": calibrated}


def measured_trace_verdict(f_Hz, level, profile: EmiProfile, U_meas_dB: float, noise_floor=None,
                           band: tuple | None = None) -> dict:
    """A measured receiver trace against the SAME approved profile: FAIL when a point exceeds the limit by more than
    the measurement uncertainty; PASS when every point (and the band coverage) is below limit - reserve - U;
    otherwise INDETERMINATE.  Representativeness of the test and approval stay separate."""
    f = np.asarray(f_Hz, dtype=float)
    x = np.asarray(level, dtype=float)
    if f.size < 2 or f.size != x.size or np.any(np.diff(f) <= 0):
        raise InputValidationError("trace needs increasing frequencies and matching levels", field="trace")
    missing = profile.missing()
    if missing:
        return {"verdict": "INDETERMINATE", "reason": "REQUIREMENT_INCOMPLETE: " + ", ".join(missing)}
    L = profile.limit.at(f)
    lo, hi = band or (profile.limit.points[0][0], profile.limit.points[-1][0])
    inb = (f >= lo) & (f <= hi) & np.isfinite(L)
    step = np.diff(f[inb])
    rbw = float(profile.fields.get("rbw_Hz") or 0.0)
    coverage_gap = bool(step.size and rbw > 0 and np.max(step / np.maximum(f[inb][:-1], 1.0)) > 0.05 and
                        np.max(step) > 10 * rbw)
    nf = None if noise_floor is None else np.asarray(noise_floor, dtype=float)
    M = profile.design_reserve_dB
    over = inb & (x - U_meas_dB > L)
    clear = inb & (x + U_meas_dB <= L - M)
    floor_bad = (nf is not None) and bool(np.any(inb & (nf >= L - M)))
    if over.any():
        v, why = "FAIL", f"{int(over.sum())} point(s) above the limit by more than U = {U_meas_dB:g} dB"
    elif clear[inb].all() and not coverage_gap and not floor_bad and (f[inb].min() <= lo * 1.001 and f[inb].max() >= hi * 0.999):
        v, why = "PASS", f"every in-band point below limit - {M:g} dB reserve - {U_meas_dB:g} dB"
    else:
        reasons = []
        if not clear[inb].all():
            reasons.append("points within the uncertainty / reserve band")
        if coverage_gap:
            reasons.append("frequency coverage too sparse for the RBW")
        if floor_bad:
            reasons.append("noise floor not below limit - reserve")
        if not (f[inb].size and f[inb].min() <= lo * 1.001 and f[inb].max() >= hi * 0.999):
            reasons.append("trace does not cover the whole band")
        v, why = "INDETERMINATE", "; ".join(reasons)
    return {"verdict": v, "reason": why, "margin_dB": (L - M - x).tolist(), "limit": L.tolist(),
            "points_in_band": int(inb.sum()), "U_meas_dB": U_meas_dB,
            "note": "a measured-trace verdict for this profile and set-up only; test representativeness and approval "
                    "are separate"}


def coupling_checks(net: HvNetwork, Vdc_V: float, src: SwitchingSource | None = None, E_y_allowed_J: float | None = None,
                    f_control_Hz: float | None = None) -> dict:
    """System couplings of section 11.5 that follow from the declared network (no PASS where evidence is missing)."""
    out = {}
    Ey = 0.5 * net.C_y_F * Vdc_V ** 2
    out["y_capacitor"] = {"C_y_per_rail_F": net.C_y_F, "C_y_total_F": 2 * net.C_y_F,
                          "energy_per_rail_at_Vdc_J": Ey,
                          "status": ("UNKNOWN" if E_y_allowed_J is None else ("FEASIBLE" if Ey <= E_y_allowed_J else "INFEASIBLE")),
                          "note": "stored energy with the full Vdc across one Y capacitor (insulation fault on the "
                                  "other rail); compare with the declared touch-energy requirement; the total "
                                  "HV-to-chassis capacitance also sets the IMD response - check against the IMD spec"}
    L_loop = 2 * (net.L_h_H + (1 - net.k_ch) * net.L_ch_H) + net.an_L_H * 0 + net.L_bat_H
    if L_loop > 0:
        fr = 1.0 / (TWO_PI * math.sqrt(L_loop * net.C_dc_F))
        R = 2 * net.R_h_ohm + net.R_bat_ohm + net.ESR_dc_ohm
        Q = math.sqrt(L_loop / net.C_dc_F) / R if R > 0 else math.inf
        flag = f_control_Hz is not None and fr < 10 * f_control_Hz
        out["dm_resonance"] = {"f_res_Hz": fr, "Q": Q, "loop_L_H": L_loop,
                               "status": "UNKNOWN",
                               "note": ("resonance within a decade of the declared control bandwidth: check the "
                                        "source / load incremental-impedance stability and damping" if flag else
                                        "DC-link / harness resonance: check damping against fsw harmonics and "
                                        "load steps (stability needs the incremental impedances)")}
    if src is not None:
        dvdt = src.Vdc_V / min(src.t_rise_s, src.t_fall_s)
        out["common_mode"] = {"v_cm_step_V": src.Vdc_V / 3.0, "dv_dt_V_per_s": dvdt,
                              "i_Cpar_sanity_A": net.C_par_F * dvdt / 3.0,
                              "note": "C dv/dt is a local sanity value, not an AN emission; the motor CM voltage drives "
                                      "shaft / bearing currents - no bearing-life claim without a motor / bearing "
                                      "network"}
    return out


# --------------------------------------------------------------------------------------------- OEW common mode

def _pair_edges(V, U, alpha, split, fe, fsw, carrier_shift, u0_cmd=None):
    """Regular-sampled centred PWM edges of both bridges of a common-bus OEW (duties from the co-linear split with
    centred offsets, as in extensions.oew)."""
    ratio = max(1, int(round(fsw / fe)))
    T = 1.0 / fe
    Ts = T / ratio
    edges = {"A": [], "B": []}
    duties = []
    for j in range(ratio):
        tc = (j + 0.5) * Ts
        th = TWO_PI * fe * tc
        u = np.array([U * math.cos(th + alpha - TWO_PI * k / 3.0) for k in range(3)])
        u0 = 0.0 if u0_cmd is None else float(u0_cmd(th))
        a, b = split * u, -(1 - split) * u
        lo = max(-a.min(), -b.min() + u0)
        hi = min(V - a.max(), V - b.max() + u0)
        cA = 0.5 * (lo + hi)
        cB = cA - u0
        dA = np.clip((a + cA) / V, 0, 1)
        dB = np.clip((b + cB) / V, 0, 1)
        duties.append((dA, dB))
        for tag, dd, sh in (("A", dA, 0.0), ("B", dB, carrier_shift)):
            for k, x in enumerate(dd):
                if 0 < x < 1:
                    c0 = j * Ts + (0.5 + sh) * Ts
                    edges[tag].append((k, (c0 - 0.5 * x * Ts) % T, +1))
                    edges[tag].append((k, (c0 + 0.5 * x * Ts) % T, -1))
    return edges, T, ratio


def oew_common_mode(V: float, U_pk: float, alpha_rad: float, fe_Hz: float, fsw_Hz: float, t_edge_s: float,
                    split_A: float = 0.5, carrier_shift: float = 0.0, u0_cmd=None, harmonics: int = 60,
                    i_pk_A: float | None = None, beta_rad: float = 0.0) -> dict:
    """Common-bus OEW: winding zero sequence u0 = v_cmA - v_cmB versus the chassis common mode v_cm6 = (v_cmA +
    v_cmB)/2 (poles relative to the DC midpoint), as line spectra at k*fsw sidebands (C-02), and - with the phase
    current - both bridges' DC currents with S_sum = S_AA + S_BB + 2 Re S_AB (C-01)."""
    for name, v in (("V", V), ("fe_Hz", fe_Hz), ("fsw_Hz", fsw_Hz), ("t_edge_s", t_edge_s)):
        if _finite(name, v) <= 0:
            raise InputValidationError(f"{name} must be > 0", field=name)
    edges, T, ratio = _pair_edges(V, U_pk, alpha_rad, split_A, fe_Hz, fsw_Hz, carrier_shift, u0_cmd)
    fs = ratio * fe_Hz
    freqs = np.array([n * fe_Hz for n in range(1, harmonics * ratio + 1)])
    tau = t_edge_s
    out = {"carrier_ratio": ratio, "fsw_used_Hz": fs, "carrier_shift": carrier_shift, "f_Hz": freqs}
    lines = {}
    for tag in ("A", "B"):
        if edges[tag]:
            k, t0, sg = (np.array(x) for x in zip(*edges[tag]))
            lines[tag] = _edge_lines(t0, np.full(t0.size, tau), sg * V / 3.0, freqs, T)
        else:
            lines[tag] = np.zeros(freqs.size, dtype=complex)
    u0 = lines["A"] - lines["B"]
    cm6 = 0.5 * (lines["A"] + lines["B"])
    out.update({"u0_amp_V": np.abs(u0), "cm6_amp_V": np.abs(cm6), "cmA_amp_V": np.abs(lines["A"]),
                "cmB_amp_V": np.abs(lines["B"]),
                "u0_rms_V": float(np.sqrt(0.5 * np.sum(np.abs(u0) ** 2))),
                "cm6_rms_V": float(np.sqrt(0.5 * np.sum(np.abs(cm6) ** 2)))})
    if i_pk_A is not None:
        cur = {}
        for tag, sgn_i in (("A", 1.0), ("B", -1.0)):
            if edges[tag]:
                k, t0, sg = (np.array(x) for x in zip(*edges[tag]))
                ik = np.array([i_pk_A * math.cos(TWO_PI * fe_Hz * t + beta_rad - TWO_PI * kk / 3.0)
                               for kk, t in zip(k, t0)])
                cur[tag] = _edge_lines(t0, np.full(t0.size, tau), sg * sgn_i * ik, freqs, T)
            else:
                cur[tag] = np.zeros(freqs.size, dtype=complex)
        S_AA, S_BB = np.abs(cur["A"]) ** 2, np.abs(cur["B"]) ** 2
        S_AB = cur["A"] * np.conj(cur["B"])
        S_sum = np.abs(cur["A"] + cur["B"]) ** 2
        out["dc_currents"] = {"I_A_rms_A": float(np.sqrt(0.5 * S_AA.sum())), "I_B_rms_A": float(np.sqrt(0.5 * S_BB.sum())),
                              "I_sum_rms_A": float(np.sqrt(0.5 * S_sum.sum())),
                              "identity_residual": float(np.max(np.abs(S_sum - (S_AA + S_BB + 2 * S_AB.real)))),
                              "IA_amp_A": np.abs(cur["A"]), "IB_amp_A": np.abs(cur["B"]),
                              "Isum_amp_A": np.abs(cur["A"] + cur["B"]),
                              "note": "the combined terminal ripple can cancel while each bridge's local capacitor "
                                      "current does not - size local capacitors on their own branch spectrum"}
    out["meaning"] = ("u0 drives the winding zero-sequence current through L0; v_cm6 drives displacement current "
                      "into the chassis through the parasitic network - suppressing one is not suppressing the other")
    return out


def zsv_free_sequence_example(V: float) -> dict:
    """A sequence of zero-u0 state pairs (sum sA = sum sB): the winding sees no zero sequence, the chassis-referenced
    common mode still steps by V/3 (C-02)."""
    seq = [("000", "000"), ("100", "010"), ("110", "011"), ("111", "111"), ("110", "011"), ("100", "010"), ("000", "000")]
    rows = []
    for a, b in seq:
        sa, sb = [int(c) for c in a], [int(c) for c in b]
        u = [V * (x - y) for x, y in zip(sa, sb)]
        u0 = sum(u) / 3.0
        cmA = V * sum(sa) / 3.0 - V / 2
        cmB = V * sum(sb) / 3.0 - V / 2
        rows.append({"sA": a, "sB": b, "u_V": u, "u0_V": u0, "v_cm6_V": 0.5 * (cmA + cmB)})
    return {"rows": rows, "u0_max_V": max(abs(r["u0_V"]) for r in rows),
            "v_cm6_steps_V": sorted({round(r["v_cm6_V"], 9) for r in rows}),
            "note": "zero-sequence suppression (u0 = 0) is not chassis common-mode suppression: EMC, bearing and "
                    "insulation stress need the chassis network and their own evidence"}
