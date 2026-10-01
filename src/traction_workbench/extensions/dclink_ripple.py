"""DC-link ripple, capacitor current, ESR loss and lifetime gating (independent review, section 8.9).

A customer "DC ripple <= X" is decomposed into location, quantity, measurement definition, operating condition
and limit; a requirement that leaves one of them open is REQUIREMENT_INCOMPLETE.  Quantities are kept apart:
average DC current, inverter input-current AC RMS, capacitor-branch RMS, voltage peak-to-peak and RMS.

Inverter input current (ideal 2-level, 3-phase, fundamental phase currents; ripple current neglected):

    i_inv(t) = s_a i_a + s_b i_b + s_c i_c                     (s_x in {0, 1}, centre-aligned carrier)

* exact carrier-period average model: with sorted duties d1 >= d2 >= d3 the per-period moments are
  <i_inv> = sum d_x i_x and <i_inv^2> = (d1 - d2) i1^2 + (d2 - d3) i3^2  (i1 + i2 = -i3);
  for SPWM this reproduces the classical closed form
  I_C,rms = I_pk sqrt(m [sqrt(3)/(4 pi) + cos^2(phi) (sqrt(3)/pi - 9 m / 16)]);
* switching-function time simulation over one fundamental period (synchronous carrier ratio, stated) and its
  spectrum; Parseval is checked between the time and the frequency domain;
* branch split per harmonic between the source impedance Z_s = R_s + j w L_s and the capacitor
  Z_C = ESR + j w ESL + 1/(j w C) (the DC component flows in the source); resonance can make the capacitor
  current exceed the inverter AC current - neither "ideal voltage source" nor "all AC into the capacitor" is
  assumed silently (without a declared source impedance the capacitor current is labelled an assumption);
* capacitor loss P = sum_h I_C,h,rms^2 ESR(f_h, T) with a frequency-dependent ESR table (no extrapolation outside
  the table band: the uncovered share is reported and the loss is not established);
* hotspot T_hot = T_ref + R_th P (ESR(T) feedback iterated when the table has temperatures);
* lifetime only from supplier life data for the exact series (hours vs hotspot temperature and voltage);
  otherwise "electrical FEASIBLE / lifetime UNKNOWN".  No "10 K halves the life" default is provided.

Dead time, device edges, ringing, overmodulation and the average-PWM ripple of the phase current are not in
this model; switching-frequency spectra are sampled, not a continuous proof over operating points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..errors import InputValidationError
from ..modulation import MODULATIONS, duties, overmodulated
from ..validation import finite as _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status
from ..units import shown

TWO_PI = 2.0 * math.pi


def _duty_set(theta: np.ndarray, m: float, modulation: str) -> np.ndarray:
    """Duties (3 x N) for the phase references m*cos(theta - 2pi k/3) (m = V_pk / (Vdc/2)), from the shared law."""
    if modulation not in MODULATIONS:
        raise InputValidationError("modulation must be spwm, svpwm or dpwm1", field="modulation")
    d = duties(theta, m, 0.0, modulation)
    if overmodulated(d):
        raise InputValidationError(f"overmodulation (m = {m:.4g} for {modulation}): the linear PWM model does not "
                                   f"apply", field="modulation_index")
    return np.clip(d, 0.0, 1.0)


def _currents(theta: np.ndarray, I_pk: float, phi: float) -> np.ndarray:
    return np.vstack([I_pk * np.cos(theta - phi - TWO_PI * k / 3) for k in range(3)])


def average_model(I_pk: float, m: float, phi_rad: float, modulation: str = "svpwm", n: int = 7200) -> dict:
    """Exact carrier-period moments of i_inv integrated over the fundamental period."""
    theta = (np.arange(n) + 0.5) * TWO_PI / n
    d = _duty_set(theta, m, modulation)
    i = _currents(theta, I_pk, phi_rad)
    mean_k = np.sum(d * i, axis=0)
    order = np.argsort(-d, axis=0)
    ds = np.take_along_axis(d, order, axis=0)
    is_ = np.take_along_axis(i, order, axis=0)
    msq_k = (ds[0] - ds[1]) * is_[0] ** 2 + (ds[1] - ds[2]) * is_[2] ** 2
    idc = float(np.mean(mean_k))
    total_ms = float(np.mean(msq_k))
    ac_rms = math.sqrt(max(total_ms - idc * idc, 0.0))
    low_rms = math.sqrt(max(float(np.mean(mean_k ** 2)) - idc * idc, 0.0))           # carrier-averaged (6 f_e) part
    sw_rms = math.sqrt(max(total_ms - float(np.mean(mean_k ** 2)), 0.0))             # switching-frequency part
    return {"I_dc_A": idc, "I_inv_rms_total_A": math.sqrt(total_ms), "I_inv_ac_rms_A": ac_rms,
            "low_frequency_ac_rms_A": low_rms, "switching_ac_rms_A": sw_rms, "modulation": modulation,
            "method": "exact carrier-period moments (centre-aligned PWM, ripple current neglected)"}


def kolar_spwm_cap_rms(I_pk: float, m: float, phi_rad: float) -> float:
    """Closed form for SPWM with a stiff source (all AC into the capacitor)."""
    return I_pk * math.sqrt(m * (math.sqrt(3) / (4 * math.pi) + math.cos(phi_rad) ** 2
                                 * (math.sqrt(3) / math.pi - 9 * m / 16)))


def switching_waveform(I_pk: float, m: float, phi_rad: float, f_e_Hz: float, fsw_Hz: float,
                       modulation: str = "svpwm", samples_per_carrier: int = 128) -> dict:
    """Time-domain i_inv(t) over one fundamental period with a synchronous carrier (ratio rounded, stated)."""
    fe = _finite("f_e_Hz", f_e_Hz)
    fs = _finite("fsw_Hz", fsw_Hz)
    if fe <= 0 or fs <= 0:
        raise InputValidationError("electrical and switching frequency must be > 0 (standstill ripple needs the "
                                   "DC-current analysis)", field="f_e_Hz")
    ratio = max(1, int(round(fs / fe)))
    n = ratio * int(samples_per_carrier)
    t = (np.arange(n) + 0.5) / (n * fe)
    theta = TWO_PI * fe * t
    d = _duty_set(theta, m, modulation)
    i = _currents(theta, I_pk, phi_rad)
    phase = (t * ratio * fe) % 1.0
    tri = 1.0 - 2.0 * np.abs(phase - 0.5)                   # 0..1..0 centre-aligned carrier (peak at centre)
    s = (tri >= 1.0 - d).astype(float)                      # leg on for a centred interval of length d
    iinv = np.sum(s * i, axis=0)
    return {"t_s": t, "i_inv_A": iinv, "carrier_ratio": ratio, "carrier_ratio_requested": fs / fe,
            "f_e_Hz": fe, "fsw_used_Hz": ratio * fe}


def spectrum(x: np.ndarray, f0_Hz: float) -> dict:
    """One-sided harmonic RMS amplitudes of a periodic signal sampled over exactly one period of f0."""
    n = x.size
    X = np.fft.rfft(x) / n
    rms = np.abs(X) * math.sqrt(2.0)
    rms[0] = abs(X[0].real)                                 # DC component (mean)
    if n % 2 == 0:
        rms[-1] = abs(X[-1])                                # Nyquist bin is not doubled
    f = np.arange(rms.size) * f0_Hz
    parseval = float(np.sqrt(np.sum(rms ** 2))) - float(np.sqrt(np.mean(x ** 2)))
    return {"f_Hz": f, "rms_A": rms, "complex": X, "parseval_residual": parseval}


RTH_BASES = ("", "per_capacitor", "bank")


@dataclass(frozen=True)
class CapacitorBank:
    """``count`` identical capacitors in parallel; C_F, ESR_ohm_table and ESL_H are PER CAPACITOR.

    The ESR table band [f_min, f_max] is the frequency range over which the capacitor impedance is characterised.
    Outside it the branch impedance is not known and the table edge value is never used as data: the ESR loss is
    not established there and ripple quantities are bounded over every passive capacitor impedance.
    ESR(T) = ESR_table(f) (1 + a (T - T_ref_C)) holds on the declared temperature domain T_valid_C (required when
    a != 0; the law must stay positive on it).  Rth is the hotspot-to-boundary resistance with a declared basis:
    "per_capacitor" (one capacitor, heated by its share of the loss) or "bank" (heated by the bank loss)."""

    C_F: float
    ESR_ohm_table: tuple                  # ((f_Hz, ESR_ohm), ...) per capacitor, at T_ref_C
    ESL_H: float = 0.0
    Rth_K_per_W: float | None = None      # hotspot-to-boundary thermal resistance (basis: Rth_basis)
    ESR_temp_coeff_per_K: float = 0.0     # ESR(T) = ESR_ref * (1 + a (T - T_ref)), declared
    T_ref_C: float = 25.0                 # temperature at which the ESR table holds
    life_hours_table: tuple = ()          # ((T_hot_C, hours), ...) supplier life data for this exact series
    life_voltage_V: float | None = None   # voltage at which the life table holds
    life_basis: str = ""                  # supplier document / revision / failure criterion
    count: int = 1                        # identical capacitors in parallel (equal split only with symmetry declared)
    symmetric_layout: bool = False
    T_valid_C: tuple | None = None        # declared temperature domain of the ESR(T) law and the capacitor data
    Rth_basis: str = ""                   # "per_capacitor" | "bank" (required when count > 1)

    def __post_init__(self):
        if _finite("C_F", self.C_F) <= 0:
            raise InputValidationError("capacitance must be > 0", field="C_F")
        try:
            tab = tuple((float(f), float(r)) for f, r in self.ESR_ohm_table)
        except (TypeError, ValueError):
            raise InputValidationError("ESR table rows are (f_Hz, ESR_ohm)", field="ESR_ohm_table") from None
        if (len(tab) < 1 or any(not (math.isfinite(f) and math.isfinite(r)) or f <= 0 or r <= 0 for f, r in tab)
                or any(b[0] <= a[0] for a, b in zip(tab, tab[1:]))):
            raise InputValidationError("ESR table needs increasing finite frequencies > 0 and ESR > 0",
                                       field="ESR_ohm_table")
        object.__setattr__(self, "ESR_ohm_table", tab)
        if _finite("ESL_H", self.ESL_H) < 0:
            raise InputValidationError("ESL must be >= 0", field="ESL_H")
        if not isinstance(self.count, int) or self.count < 1:
            raise InputValidationError("count must be an integer >= 1", field="count")
        if self.count > 1 and not self.symmetric_layout:
            raise InputValidationError("several capacitors: an equal current split needs a declared symmetric layout "
                                       "(otherwise give each branch)", field="symmetric_layout")
        t_ref = _finite("T_ref_C", self.T_ref_C)
        a = _finite("ESR_temp_coeff_per_K", self.ESR_temp_coeff_per_K)
        if self.Rth_K_per_W is not None and _finite("Rth_K_per_W", self.Rth_K_per_W) < 0:
            raise InputValidationError("Rth must be >= 0", field="Rth_K_per_W")
        if self.Rth_basis not in RTH_BASES:
            raise InputValidationError(f"Rth basis must be one of {RTH_BASES[1:]}", field="Rth_basis")
        if self.count > 1 and self.Rth_K_per_W is not None and not self.Rth_basis:
            raise InputValidationError("several capacitors: state whether Rth belongs to one capacitor (heated by its "
                                       "share of the loss) or to the bank (heated by the bank loss)",
                                       field="Rth_basis")
        if self.T_valid_C is not None:
            try:
                lo, hi = (float(v) for v in self.T_valid_C)
            except (TypeError, ValueError):
                raise InputValidationError("temperature domain is (T_min_C, T_max_C)", field="T_valid_C") from None
            if not (math.isfinite(lo) and math.isfinite(hi) and -273.15 < lo < hi):
                raise InputValidationError("temperature domain needs finite -273.15 < T_min < T_max",
                                           field="T_valid_C")
            if not lo <= t_ref <= hi:
                raise InputValidationError("the ESR table temperature must lie in the declared domain",
                                           field="T_valid_C")
            if a and min(1.0 + a * (lo - t_ref), 1.0 + a * (hi - t_ref)) <= 0:
                raise InputValidationError("the declared ESR(T) law is not positive over its declared domain",
                                           field="ESR_temp_coeff_per_K")
            object.__setattr__(self, "T_valid_C", (lo, hi))
        elif a:
            raise InputValidationError("an ESR temperature coefficient needs the temperature domain it holds on "
                                       "(T_valid_C)", field="T_valid_C")
        for row in self.life_hours_table:
            T, h = (float(v) for v in row)
            if not (math.isfinite(T) and math.isfinite(h) and h > 0):
                raise InputValidationError("life table rows are finite (T_hot_C, hours > 0)", field="life_hours_table")

    def esr_factor(self, T_C: float | None) -> float:
        """ESR(T) / ESR(T_ref_C) of the declared law (NaN where the law is not positive: not a physical ESR)."""
        if T_C is None or not self.ESR_temp_coeff_per_K:
            return 1.0
        k = 1.0 + self.ESR_temp_coeff_per_K * (T_C - self.T_ref_C)
        return k if k > 0 else math.nan

    def esr(self, f: np.ndarray, T_C: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Bank ESR at f (log-frequency interpolation inside the table) and the coverage mask.  Outside the table
        band the returned value is the edge value and is NOT data: callers must honour the mask."""
        fs = np.array([a for a, _ in self.ESR_ohm_table])
        rs = np.array([b for _, b in self.ESR_ohm_table])
        f = np.asarray(f, dtype=float)
        inside = (f >= fs[0] * (1 - 1e-12)) & (f <= fs[-1] * (1 + 1e-12))
        if fs.size == 1:
            r = np.full_like(f, rs[0])
        else:
            r = np.interp(np.log(np.clip(f, fs[0], fs[-1])), np.log(fs), rs)
        return r * self.esr_factor(T_C) / self.count, inside

    def impedance(self, f: np.ndarray, T_C: float | None = None) -> np.ndarray:
        w = TWO_PI * np.asarray(f, dtype=float)
        esr, _ = self.esr(f, T_C)
        with np.errstate(divide="ignore"):
            return esr + 1j * w * self.ESL_H / self.count + 1.0 / (1j * w * self.C_F * self.count)


@dataclass(frozen=True)
class SourceImpedance:
    R_ohm: float
    L_H: float
    basis: str = ""

    def __post_init__(self):
        if _finite("R_ohm", self.R_ohm) < 0 or _finite("L_H", self.L_H) < 0:
            raise InputValidationError("source resistance and inductance must be >= 0", field="source")
        if self.R_ohm == 0 and self.L_H == 0:
            raise InputValidationError("a zero source impedance is an ideal voltage source (no capacitor current): "
                                       "declare the battery / harness impedance", field="source")

    def z(self, f: np.ndarray) -> np.ndarray:
        return self.R_ohm + 1j * TWO_PI * np.asarray(f, dtype=float) * self.L_H


REQUIRED_REQ_FIELDS = ("location", "quantity", "limit", "bandwidth_Hz")
MODEL_BAND_CARRIERS = 10          # ideal-switch spectrum used up to 10 carrier groups (edges/ringing beyond)
LOCATIONS = ("dc_link_bus", "capacitor_branch", "inverter_dc_input", "battery_terminal")
QUANTITIES = ("voltage_pp", "voltage_ac_rms", "current_ac_rms", "capacitor_current_rms")
# location x quantity -> branch / node of the lumped network.  The network has ONE bus node: the capacitor branch
# and the inverter input terminals are the same node (no busbar impedance between them is modelled).
BRANCH_MAP = {
    ("capacitor_branch", "current_ac_rms"): "capacitor_current",
    ("capacitor_branch", "capacitor_current_rms"): "capacitor_current",
    ("inverter_dc_input", "current_ac_rms"): "inverter_current",
    ("battery_terminal", "current_ac_rms"): "source_current",
    **{(loc, q): "bus_voltage" for loc in ("dc_link_bus", "capacitor_branch", "inverter_dc_input")
       for q in ("voltage_pp", "voltage_ac_rms")},
}
UNSUPPORTED = {
    ("dc_link_bus", "current_ac_rms"): (
        Reason.REQUIREMENT_INCOMPLETE, "a bus node carries no single current: name the branch (capacitor_branch, "
                                       "inverter_dc_input or battery_terminal)"),
    **{("battery_terminal", q): (
        Reason.OUTSIDE_MODEL_DOMAIN, "the declared source impedance lumps the battery and the harness: the battery "
                                     "terminal voltage is not a node of this network (declare the split)")
       for q in ("voltage_pp", "voltage_ac_rms")},
    **{(loc, "capacitor_current_rms"): (
        Reason.REQUIREMENT_INCOMPLETE, "capacitor_current_rms is the capacitor branch current: its location is "
                                       "capacitor_branch")
       for loc in ("dc_link_bus", "inverter_dc_input", "battery_terminal")},
}
BRANCH_TEXT = {"capacitor_current": "capacitor branch current I_C",
               "inverter_current": "inverter input current I_inv (independent of the network)",
               "source_current": "source (battery + harness) branch current I_s",
               "bus_voltage": "bus node voltage (across the capacitor branch = inverter input terminals)"}
RMS_FLOOR = 1e-9                  # harmonics below this share of the inverter AC RMS are rounding level (no current)
# harmonics outside the ESR table band gate the loss only when their share of the inverter AC current-squared
# exceeds this: an energy criterion, not the FFT's rounding level - a 1e-9 RMS floor made the verdict follow the FFT
# length and the switching frequency (engineering review 2 of 63a2b61, F-10).  Below it their heating is carried at
# the table's largest ESR (an assumption, stated), so the loss is not understated either
UNCOVERED_I2_SHARE = 0.05
THERMAL_TOL_K = 1e-9              # bracket width at which the equilibrium temperature is accepted
RESIDUAL_BUDGET_K = 1e-6          # |T - T_b - Rth P(T)| re-evaluated independently at the returned state
SCAN_STEP_K = 0.5                 # scan step for the first equilibrium above the boundary temperature


def _rms_weights(n_time: int, n_bins: int) -> np.ndarray:
    """One-sided RMS weight of each rfft bin: DC and the Nyquist bin (even n) count once, the others sqrt(2)."""
    w = np.full(n_bins, math.sqrt(2.0))
    w[0] = 1.0
    if n_time % 2 == 0:
        w[-1] = 1.0
    return w


class _Network:
    """Frequency-wise split of the inverter current between the source branch and the capacitor branch.

    KCL per harmonic I_inv,h = I_C,h + I_s,h with the node voltage v_h = -I_C,h Z_C,h = -I_s,h Z_s,h; the DC
    component flows in the source.  Z_C depends on the capacitor temperature through ESR(T), so every quantity is
    evaluated at ONE stated temperature (``at``).  Outside the ESR table band the capacitor impedance is unknown;
    ``bounds`` gives each harmonic's range over every passive capacitor impedance (Re Z_C >= 0)."""

    def __init__(self, wave: dict, bank: CapacitorBank, source: SourceImpedance | None):
        x = wave["i_inv_A"]
        sp = spectrum(x, wave["f_e_Hz"])
        self.n = x.size
        self.f, self.X = sp["f_Hz"], sp["complex"]
        self.parseval_residual = sp["parseval_residual"]
        self.bank, self.Zs = bank, (None if source is None else source.z(sp["f_Hz"]))
        nb = self.f.size
        self.ac = np.arange(nb) > 0
        self.wt = _rms_weights(self.n, nb)
        self.amp = np.where(self.wt > 1.0, math.sqrt(2.0), 1.0)          # peak amplitude / RMS of each bin
        fa = np.where(self.ac, self.f, 1.0)
        esr_ref, inside = bank.esr(fa)
        self.esr_ref = np.where(self.ac, esr_ref, 0.0)                     # bank ESR at the table temperature
        w = TWO_PI * fa
        self.B = np.where(self.ac, w * bank.ESL_H / bank.count - 1.0 / (w * bank.C_F * bank.count), 0.0)
        self.inv_rms_h = np.abs(self.X) * self.wt
        self.I_inv_ac = math.sqrt(float(np.sum(self.inv_rms_h[self.ac] ** 2)))
        # every AC harmonic up to Nyquist is assessed (the FFT reaches twice the ESR table's top frequency: samples
        # per carrier raised in ripple_analysis, so nothing follows the FFT length).  Outside the table band the ESR
        # is not data: that content gates the loss only above UNCOVERED_I2_SHARE of the inverter AC current-squared
        # (the inverter current bounds the capacitor current at every passive split: a conservative proxy)
        self.f_model_Hz = float(bank.ESR_ohm_table[-1][0])
        self.assessed = self.ac
        I2 = max(self.I_inv_ac ** 2, 1e-300)
        self.above_share = float(np.sum(self.inv_rms_h[self.ac & (self.f > self.f_model_Hz * (1 + 1e-12))] ** 2) / I2)
        self.uncovered = self.ac & ~inside
        self.uncovered_share = float(np.sum(self.inv_rms_h[self.uncovered] ** 2) / I2)
        gate = self.uncovered_share > UNCOVERED_I2_SHARE
        self.carrying = self.uncovered & (self.inv_rms_h > RMS_FLOOR * max(self.I_inv_ac, 1e-300)) & gate
        self.complete = not gate
        self.esr_max_ref = max(r for _, r in bank.ESR_ohm_table) / bank.count   # the table's largest (bank) ESR

    def loss(self, T_C: float | None) -> float:
        """ESR loss over the characterised harmonics with the capacitor at T_C (current split re-solved at T_C)."""
        esr = self.esr_ref * self.bank.esr_factor(T_C)
        if self.Zs is None:
            ic = np.abs(self.X)
        else:
            ic = np.abs(self.X * self.Zs / (self.Zs + esr + 1j * self.B))
        cov = self.assessed & ~self.uncovered
        ind = (ic * self.wt) ** 2 * self.esr_max_ref * self.bank.esr_factor(T_C)
        return float(np.sum(((ic * self.wt) ** 2 * esr)[cov]) + (np.sum(ind[self.uncovered]) if self.complete else 0.0))

    def at(self, T_C: float | None) -> dict:
        """Branch phasors, node voltage and ESR loss with the capacitor at T_C (None: the ESR table temperature)."""
        esr = self.esr_ref * self.bank.esr_factor(T_C)
        Zc = esr + 1j * self.B
        X, ac = self.X, self.ac
        if self.Zs is None:                   # stiff current source: every AC harmonic into the capacitor
            Ic = np.where(ac, X, 0.0)
            Is = np.where(ac, 0.0, X)
        else:
            Zs = self.Zs
            with np.errstate(divide="ignore", invalid="ignore"):
                Ic = np.where(ac, X * Zs / (Zs + Zc), 0.0)
                Is = np.where(ac, Ic * Zc / Zs, X)                   # from the node voltage, not from KCL
        V = np.where(ac, -Ic * Zc, 0.0)
        ic_h = np.abs(Ic) * self.wt
        cov = self.assessed & ~self.uncovered
        kcl = float(np.max(np.abs(X - Ic - Is)[ac], initial=0.0)) / max(float(np.max(np.abs(X))), 1e-300)
        p_ind = (float(np.sum((ic_h ** 2)[self.uncovered]) * self.esr_max_ref * self.bank.esr_factor(T_C))
                 if self.complete else 0.0)
        return {"T_C": T_C, "esr": esr, "Ic": Ic, "Is": Is, "V": V, "ic_rms_h": ic_h,
                "P_W": float(np.sum((ic_h ** 2 * esr)[cov])) + p_ind, "P_indicative_W": p_ind,
                "kcl_residual_rel": kcl}

    def bounds(self, branch: str) -> tuple[np.ndarray, np.ndarray]:
        """Per-harmonic RMS range of a branch quantity over every passive capacitor impedance.

        With w = Z_s + Z_C in {Re w >= R_s}, 1/w lies in the disk centred 1/(2 R_s) with radius 1/(2 R_s), so
        |I_C| <= |I_inv| |Z_s| / R_s (reached by a lossless branch resonating the source reactance) with infimum 0,
        and I_s / I_inv = 1 - Z_s / w lies in the disk centred 1 - Z_s / (2 R_s) with radius |Z_s| / (2 R_s);
        |v| = |Z_s| |I_s|.  A lossless source gives no bound."""
        a = self.inv_rms_h
        if branch == "inverter_current":
            return a, a
        if self.Zs is None:
            if branch == "capacitor_current":
                return a, a
            if branch == "source_current":
                return np.zeros_like(a), np.zeros_like(a)
            return np.zeros_like(a), np.full_like(a, np.inf)
        Rs, mag = self.Zs.real, np.abs(self.Zs)
        pos = Rs > 0
        with np.errstate(divide="ignore", invalid="ignore"):
            if branch == "capacitor_current":
                return np.zeros_like(a), np.where(pos, a * mag / Rs, np.inf)
            c = np.abs(1.0 - self.Zs / (2.0 * Rs))
            rho = mag / (2.0 * Rs)
            lo = np.where(pos, a * np.maximum(c - rho, 0.0), 0.0)
            hi = np.where(pos, a * (c + rho), np.inf)
        if branch == "source_current":
            return lo, hi
        return lo * mag, hi * mag


def _band_quantity(net: _Network, st: dict, branch: str, qty: str, keep: np.ndarray) -> dict:
    """A band-limited branch quantity: exact where the capacitor impedance is characterised, bounded over every
    passive capacitor impedance at the harmonics outside the ESR table (the edge value is never used)."""
    phasor = {"inverter_current": net.X, "capacitor_current": st["Ic"], "source_current": st["Is"],
              "bus_voltage": st["V"]}[branch]
    independent = branch == "inverter_current" or (branch == "capacitor_current" and net.Zs is None)
    unc = np.zeros_like(keep) if independent else (keep & net.uncovered)
    exact = keep & ~unc
    lo_h, hi_h = net.bounds(branch)
    unknown = bool(np.any(unc & net.carrying))
    if qty == "voltage_pp":
        def pp(mask):
            vt = np.fft.irfft(np.where(mask, phasor, 0.0) * net.n, net.n)
            return float(vt.max() - vt.min())
        pp_e = pp(exact)
        amp = float(np.sum((hi_h * net.amp)[unc]))
        lo, hi = max(pp_e - 2.0 * amp, 0.0), pp_e + 2.0 * amp
        value = None if unknown else pp(keep)
    else:
        mag = np.abs(phasor) * net.wt
        e2 = float(np.sum(mag[exact] ** 2))
        lo = math.sqrt(e2 + float(np.sum(lo_h[unc] ** 2)))
        hi = math.sqrt(e2 + float(np.sum(hi_h[unc] ** 2)))
        value = None if unknown else math.sqrt(e2 + float(np.sum(mag[unc] ** 2)))
    f_unc = net.f[unc & net.carrying]
    return {"value": value, "lo": lo, "hi": hi, "uncovered_harmonics": int(f_unc.size),
            "uncovered_band_Hz": [float(f_unc.min()), float(f_unc.max())] if f_unc.size else None}


def _thermal_state(net: _Network, bank: CapacitorBank, T_b: float | None, max_iter: int) -> dict | None:
    """Capacitor hotspot with ESR(T), the current split and the heat solved at ONE temperature.

    g(T) = T - T_b - Rth_eff P_cap(T).  g(T_b) <= 0, and a scalar thermal state starting at the boundary temperature
    rises until the FIRST zero of g above T_b and cannot pass it: that is the settled state.  It is located by a
    scan over the declared domain and refined by bisection; the residual is re-evaluated independently at the
    returned temperature.  Termination is explicit - converged / closed_form, max_iter, nonfinite, out_of_domain,
    no_equilibrium_in_domain, loss_not_established, no_boundary_temperature - and only a converged state carries a
    temperature (non-convergence is not asserted as physical runaway)."""
    if bank.Rth_K_per_W is None:
        return None
    per_cap = bank.Rth_basis == "per_capacitor"
    R = float(bank.Rth_K_per_W) / (bank.count if per_cap else 1)
    dom = bank.T_valid_C
    out = {"T_hot_C": None, "P_W": None, "converged": False, "termination": None, "residual_K": None,
           "iterations": 0, "scan_points": 0, "boundary_T_C": T_b, "domain_C": None if dom is None else list(dom),
           "Rth_basis": bank.Rth_basis or "bank", "Rth_eff_K_per_W": R, "stable_from_below": None,
           "analytic_fixed_current": None,
           "equation": "T = T_b + Rth_eff P_cap(T); P_cap(T) = sum_h |I_C,h(T)|^2 ESR(f_h, T); "
                       "I_C,h(T) = I_inv,h Z_s,h / (Z_s,h + Z_C,h(T))"}
    if T_b is None:
        return {**out, "termination": "no_boundary_temperature"}
    if not net.complete:
        return {**out, "termination": "loss_not_established"}
    lo, hi = dom if dom is not None else (-math.inf, math.inf)
    if not lo <= T_b <= hi:
        return {**out, "termination": "out_of_domain"}
    a = bank.ESR_temp_coeff_per_K

    def g(T: float) -> float:
        return T - T_b - R * net.loss(T)

    if net.Zs is None and a:
        P_ref = net.loss(bank.T_ref_C)
        s = R * a * P_ref
        T_an = (T_b + R * P_ref * (1.0 - a * bank.T_ref_C)) / (1.0 - s) if s < 1.0 else None
        out["analytic_fixed_current"] = {
            "slope": s, "equilibrium_exists": s < 1.0, "T_hot_C": T_an,
            "within_domain": T_an is not None and lo <= T_an <= hi,
            "basis": "stiff source: the capacitor current does not depend on its ESR, so P(T) is affine and "
                     "dT = Rth P(T_b) / (1 - Rth a P_ref) exists only for Rth a P_ref < 1"}
    if not a:
        T = T_b + R * net.loss(T_b)
        if not math.isfinite(T):
            return {**out, "termination": "nonfinite"}
        if T > hi:
            return {**out, "termination": "out_of_domain", "T_unconstrained_C": T}
        term, it, pts = "closed_form", 0, 1
    else:
        n_scan = max(1, math.ceil((hi - T_b) / SCAN_STEP_K))
        grid = np.linspace(T_b, hi, n_scan + 1)
        g0, pts = g(T_b), 1
        if not math.isfinite(g0):
            return {**out, "termination": "nonfinite", "scan_points": pts}
        it = 0
        if g0 >= 0.0:
            T, term = T_b, "converged"
        else:
            bracket, T0 = None, T_b
            for T1 in grid[1:]:
                g1 = g(float(T1))
                pts += 1
                if not math.isfinite(g1):
                    return {**out, "termination": "nonfinite", "scan_points": pts, "T_last_C": float(T1)}
                if g1 >= 0.0:
                    bracket = (T0, float(T1))
                    break
                T0 = float(T1)
            if bracket is None:
                return {**out, "termination": "no_equilibrium_in_domain", "scan_points": pts,
                        "note": f"T - T_b - Rth P(T) < 0 at every scanned temperature up to {hi:g} degC: from the "
                                f"boundary temperature the hotspot heats past the declared domain in this model "
                                f"(no settled state; not asserted as physical runaway)"}
            a_, b_ = bracket
            while b_ - a_ > THERMAL_TOL_K and it < max_iter:
                mid = 0.5 * (a_ + b_)
                gm = g(mid)
                it += 1
                if not math.isfinite(gm):
                    return {**out, "termination": "nonfinite", "iterations": it, "scan_points": pts}
                if gm < 0.0:
                    a_ = mid
                else:
                    b_ = mid
            if b_ - a_ > THERMAL_TOL_K:
                return {**out, "termination": "max_iter", "iterations": it, "scan_points": pts,
                        "bracket_C": [a_, b_]}
            T, term = 0.5 * (a_ + b_), "converged"
    st = net.at(T)
    r = T - T_b - R * st["P_W"]
    ok = math.isfinite(r) and abs(r) <= RESIDUAL_BUDGET_K
    return {**out, "T_hot_C": T if ok else None, "P_W": st["P_W"] if ok else None, "converged": ok,
            "termination": term if ok else "residual", "residual_K": r, "iterations": it, "scan_points": pts,
            "stable_from_below": True, "kcl_residual_rel": st["kcl_residual_rel"],
            "scan_step_K": None if not a else SCAN_STEP_K}


def _state(hot: dict | None, bank: CapacitorBank, T_b: float | None) -> tuple[float, str, bool]:
    """Temperature the network is reported at, its basis, and whether the ESR there is established."""
    dom = bank.T_valid_C
    if hot is not None and hot["converged"]:
        return hot["T_hot_C"], "settled hotspot (ESR(T), current split and heat at one temperature)", True
    T = bank.T_ref_C if T_b is None else T_b
    what = "ESR table temperature (no temperature given)" if T_b is None else "boundary temperature"
    inside = dom is None or dom[0] <= T <= dom[1]
    if hot is None:
        return T, what + " - self-heating not modelled (no Rth)", (not bank.ESR_temp_coeff_per_K) and inside
    return (T, f"{what} - thermal state not established ({hot['termination']})",
            (not bank.ESR_temp_coeff_per_K) and dom is None)


_NUMERICAL = ("max_iter", "nonfinite", "residual")


def ripple_analysis(I_pk: float, m: float, phi_rad: float, f_e_Hz: float, fsw_Hz: float, Vdc_V: float,
                    bank: CapacitorBank, source: SourceImpedance | None = None, modulation: str = "svpwm",
                    requirement: dict | None = None, T_ref_C: float | None = None,
                    samples_per_carrier: int = 128, max_iter: int = 100,
                    required_life_h: float | None = None) -> dict:
    """Capacitor current, ripple, ESR loss, hotspot and life at one operating point.

    ``T_ref_C`` is the boundary temperature Rth refers to (coolant / ambient).  Every reported current, voltage and
    loss belongs to ONE capacitor temperature (``state``): the settled hotspot when the thermal state is solved,
    otherwise the stated temperature with the claims that depend on the ESR there gated."""
    # the FFT must reach well past the ESR table's top frequency: the samples per carrier follow it (doubling from
    # the requested count), so the assessed band never depends on the FFT length (engineering review 2, F-10)
    f_top = float(bank.ESR_ohm_table[-1][0])
    spc = int(samples_per_carrier)
    while spc * fsw_Hz / 2.0 < 2.0 * f_top and spc < 8192:     # Nyquist at twice the top: resolved near it
        spc *= 2
    wave = switching_waveform(I_pk, m, phi_rad, f_e_Hz, fsw_Hz, modulation, spc)
    net = _Network(wave, bank, source)
    T_b = None if T_ref_C is None else _finite("T_ref_C", T_ref_C)
    hot = _thermal_state(net, bank, T_b, int(max_iter))
    T_state, basis, known = _state(hot, bank, T_b)
    st = net.at(T_state)
    f, X, ac, n = net.f, net.X, net.ac, net.n
    assumption = None
    if source is None:
        assumption = ("source impedance not declared: capacitor current computed for a stiff current-source "
                      "battery (all AC into the capacitor) - an assumption, not a bound (resonance can exceed it)")
    # a stiff source puts every AC harmonic into the capacitor whatever its impedance: that RMS current needs no ESR
    # and is the full ideal-waveform value; everything that needs Z_C is taken over the assessed band
    full = {k: _band_quantity(net, st, br, q, net.ac if (br == "capacitor_current" and source is None)
                              else net.assessed) for k, br, q in
            (("icap", "capacitor_current", "current_ac_rms"), ("isrc", "source_current", "current_ac_rms"),
             ("vpp", "bus_voltage", "voltage_pp"), ("vrms", "bus_voltage", "voltage_ac_rms"))}
    # operating-state values: a quantity that depends on the capacitor impedance is reported only where the ESR at
    # the stated temperature is established (the values at an unsettled temperature stay a labelled diagnostic)
    op = {k: v if (known or (k == "icap" and source is None)) else {"value": None, "lo": None, "hi": None}
          for k, v in full.items()}
    unc_share = net.uncovered_share
    loss_ok = net.complete and known
    k_show = min(f.size, 4 * wave["carrier_ratio"] + 8)
    ic_show = np.where(net.uncovered & (net.Zs is not None), np.nan, st["ic_rms_h"])
    step = max(1, n // 4000)
    v_mask = net.assessed if full["vpp"]["value"] is not None else (net.assessed & ~net.uncovered)
    v_full = np.fft.irfft(np.where(v_mask, st["V"], 0.0) * n, n)
    # capacitor life (engineering review 2 of 63a2b61, F-16): the supplier table at the settled hotspot, the voltage
    # check with the ripple peak on top of Vdc, and a verdict only against a stated required life
    v_ripple_pk = (float(np.max(v_full)) if full["vpp"]["value"] is not None else
                   (0.5 * op["vpp"]["hi"] if op["vpp"]["hi"] is not None and math.isfinite(op["vpp"]["hi"]) else None))
    V_life = Vdc_V + (v_ripple_pk or 0.0)
    req_life = None if required_life_h in (None, 0, 0.0) else float(required_life_h)
    if req_life is not None and not (math.isfinite(req_life) and req_life > 0):
        raise InputValidationError("the required capacitor life must be > 0 h", field="required_life_h")
    life = {"status": "UNKNOWN", "reason": "no supplier life data for this capacitor series (no generic '10 K halves "
                                            "the life' rule is applied)", "required_h": req_life}
    if bank.life_hours_table:
        if hot is None or not hot["converged"]:
            life = {"status": "UNKNOWN", "required_h": req_life,
                    "reason": "no settled hotspot temperature (" + ("no Rth declared" if hot is None else
                                                                    hot["termination"]) + "): no life at an unsettled state"}
        else:
            tab = sorted(bank.life_hours_table)
            Ts = [a for a, _ in tab]
            if Ts[0] <= hot["T_hot_C"] <= Ts[-1]:
                hrs = float(np.exp(np.interp(hot["T_hot_C"], Ts, [math.log(b) for _, b in tab])))
                vok = bank.life_voltage_V is None or V_life <= bank.life_voltage_V
                life = {"status": "CONDITIONAL" if vok else "UNKNOWN", "required_h": req_life,
                        "hours_at_hotspot": hrs if vok else None, "V_with_ripple_peak_V": V_life,
                        "basis": bank.life_basis or "supplier life table (basis not stated)",
                        "note": ("supplier table interpolated at the hotspot for constant conditions; a variable "
                                 "mission needs the supplier's accumulation rule" if vok else
                                 f"Vdc + ripple peak {V_life:.4g} V above the life-table voltage "
                                 f"{bank.life_voltage_V:g} V")}
            else:
                life = {"status": "UNKNOWN", "required_h": req_life,
                        "reason": f"hotspot {hot['T_hot_C']:.3g} degC outside the life table [{Ts[0]:g}, {Ts[-1]:g}] "
                                  f"degC (no extrapolation)"}
    esr_band = (bank.ESR_ohm_table[0][0], bank.ESR_ohm_table[-1][0])
    out = {
        "operating": {"I_pk_A": I_pk, "modulation_index": m, "phi_deg": math.degrees(phi_rad), "f_e_Hz": f_e_Hz,
                      "fsw_requested_Hz": fsw_Hz, "fsw_used_Hz": wave["fsw_used_Hz"],
                      "carrier_ratio": wave["carrier_ratio"], "modulation": modulation, "Vdc_V": Vdc_V},
        "average_model": average_model(I_pk, m, phi_rad, modulation),
        "state": {"T_C": T_state, "basis": basis, "esr_established": known},
        "I_dc_A": float(X[0].real), "I_inv_ac_rms_A": net.I_inv_ac,
        "I_cap_rms_A": op["icap"]["value"], "I_cap_rms_bounds_A": [op["icap"]["lo"], op["icap"]["hi"]],
        "I_source_ac_rms_A": op["isrc"]["value"], "I_source_ac_rms_bounds_A": [op["isrc"]["lo"], op["isrc"]["hi"]],
        "V_ripple_pp_V": op["vpp"]["value"], "V_ripple_pp_bounds_V": [op["vpp"]["lo"], op["vpp"]["hi"]],
        "V_ripple_ac_rms_V": op["vrms"]["value"], "V_ripple_ac_rms_bounds_V": [op["vrms"]["lo"], op["vrms"]["hi"]],
        "P_cap_W": st["P_W"] if loss_ok else None, "P_cap_covered_W": st["P_W"] - st["P_indicative_W"],
        "P_cap_indicative_W": st["P_indicative_W"],
        "at_stated_temperature": None if known else {
            "T_C": T_state, "note": "diagnostic only: the ESR at the operating temperature is not established",
            "I_cap_rms_A": full["icap"]["value"], "I_source_ac_rms_A": full["isrc"]["value"],
            "V_ripple_pp_V": full["vpp"]["value"], "V_ripple_ac_rms_V": full["vrms"]["value"],
            "P_cap_covered_W": st["P_W"]},
        "esr_coverage": {"table_band_Hz": list(esr_band), "complete": net.complete,
                         "uncovered_harmonics": int(np.count_nonzero(net.uncovered & net.carrying)),
                         "inverter_I2_share_outside": unc_share, "gate_I2_share": UNCOVERED_I2_SHARE},
        "model_band": {"f_max_Hz": net.f_model_Hz, "samples_per_carrier": spc,
                       "inverter_I2_share_above": net.above_share, "inverter_I2_share_outside": net.uncovered_share,
                       "gate_share": UNCOVERED_I2_SHARE,
                       "note": (f"{100 * net.uncovered_share:.2g} % of the ideal-switch inverter AC current-squared "
                                f"lies outside the ESR table band ({100 * net.above_share:.2g} % above "
                                f"{net.f_model_Hz:.4g} Hz, where real edges and ringing also decide): "
                                + ("its heating is carried at the table's largest ESR (an assumption, not data)"
                                   if net.complete else f"more than {100 * UNCOVERED_I2_SHARE:g} %: the loss is not "
                                                        f"established"))},
        "current_share_outside_ESR_band": unc_share,
        "hotspot": hot, "life": life, "kcl_residual_rel": st["kcl_residual_rel"],
        "parseval_residual_A": net.parseval_residual, "assumption": assumption,
        "spectrum": {"f_Hz": f[:k_show].tolist(), "I_inv_rms_A": net.inv_rms_h[:k_show].tolist(),
                     "I_cap_rms_A": [None if not math.isfinite(v) else float(v) for v in ic_show[:k_show]],
                     "ESR_covered": (~net.uncovered[:k_show]).tolist()},
        "waveform": {"t_s": wave["t_s"][::step].tolist(), "i_inv_A": wave["i_inv_A"][::step].tolist(),
                     "v_ripple_V": v_full[::step].tolist(),
                     "v_basis": ("all harmonics in the model band" if v_mask is net.assessed else
                                 "harmonics inside the ESR table band only (impedance unknown elsewhere)")},
        "not_modelled": ["phase-current ripple", "dead time", "device edges / ringing", "overmodulation",
                         "asynchronous-carrier sidebands (synchronous ratio used)",
                         "C / ESL / ESR tolerances", "busbar impedance between capacitor and inverter terminals"],
    }

    fine = {}

    def refined() -> "_Network":
        """The same operating point at twice the samples per carrier (built once, on demand)."""
        if "net" not in fine:
            fine["net"] = _Network(switching_waveform(I_pk, m, phi_rad, f_e_Hz, fsw_Hz, modulation, 2 * spc),
                                   bank, source)
        return fine["net"]

    def refine(branch: str, qty: str, bw: float) -> dict:
        net2 = refined()
        return _band_quantity(net2, net2.at(T_state), branch, qty, net2.ac & (net2.f <= bw * (1 + 1e-12)))

    # the sampling resolution of the loss and the hotspot, as for the requirement quantity (review 2, F12): the change
    # of the value at twice the samples per carrier
    out["P_cap_resolution_W"] = abs(refined().at(T_state)["P_W"] - st["P_W"]) if loss_ok else None
    out["hotspot_resolution_K"] = (bank.Rth_K_per_W * out["P_cap_resolution_W"]
                                   if loss_ok and hot is not None and hot.get("converged") else None)
    out["claims"] = _claims(out, requirement, net, st, source, refine)
    return out


def _state_reason(hot: dict | None) -> Reason:
    if hot is not None and hot["termination"] in _NUMERICAL:
        return Reason.NUMERICAL_UNRESOLVED
    if hot is not None and hot["termination"] == "no_boundary_temperature":
        return Reason.MISSING_INPUT
    if hot is None:
        return Reason.MISSING_INPUT
    return Reason.OUTSIDE_MODEL_DOMAIN


def _requirement_claim(out: dict, req: dict | None, net: _Network, st: dict, source, refine) -> dict:
    title = "DC ripple requirement"
    if not req:
        return Claim("ripple_requirement", Status.UNKNOWN, title, "not stated",
                     reasons=(Reason.REQUIREMENT_INCOMPLETE,), detail="no ripple requirement stated").to_dict()
    missing = [k for k in REQUIRED_REQ_FIELDS if req.get(k) in (None, "")]
    loc, qty = req.get("location"), req.get("quantity")
    if loc not in (None, "") and loc not in LOCATIONS:
        missing.append(f"location one of {LOCATIONS}")
    if qty not in (None, "") and qty not in QUANTITIES:
        missing.append(f"quantity one of {QUANTITIES}")
    lim = bw = None
    if not missing:
        try:
            lim, bw = float(req["limit"]), float(req["bandwidth_Hz"])
        except (TypeError, ValueError):
            missing.append("numeric limit and bandwidth")
        else:
            if not (math.isfinite(lim) and lim >= 0 and math.isfinite(bw) and bw > 0):
                missing.append("a finite limit >= 0 and a bandwidth > 0")
    if missing:
        return Claim("ripple_requirement", Status.UNKNOWN, title, "requirement definition",
                     reasons=(Reason.REQUIREMENT_INCOMPLETE,),
                     detail="requirement incomplete: " + ", ".join(missing) + " (a limit without location, quantity "
                            "and measurement bandwidth cannot be judged)").to_dict()
    q = f"{qty} at {loc} <= {lim:g} (harmonics up to {bw:g} Hz)"
    if (loc, qty) in UNSUPPORTED:
        rs, why = UNSUPPORTED[(loc, qty)]
        return Claim("ripple_requirement", Status.UNKNOWN, q, "location x quantity mapping", reasons=(rs,),
                     detail=why).to_dict()
    branch = BRANCH_MAP[(loc, qty)]
    method = f"switching-function network model: {BRANCH_TEXT[branch]}"
    state = out["state"]
    depends = branch != "inverter_current"
    if bw > out["operating"]["fsw_used_Hz"] * MODEL_BAND_CARRIERS:
        return Claim("ripple_requirement", Status.UNKNOWN, q, method, reasons=(Reason.OUTSIDE_MODEL_DOMAIN,),
                     detail=f"measurement bandwidth {bw:g} Hz reaches beyond {MODEL_BAND_CARRIERS} carrier groups of "
                            f"the ideal-switch model (edges / ringing / parasitics not modelled)").to_dict()
    if depends and source is None:
        return Claim("ripple_requirement", Status.UNKNOWN, q, method, reasons=(Reason.MISSING_INPUT,),
                     detail="the branch split needs the source impedance: a stiff source is an assumption, not a "
                            "bound (resonance can exceed it)").to_dict()
    if depends and not state["esr_established"]:
        return Claim("ripple_requirement", Status.UNKNOWN, q, method, reasons=(_state_reason(out["hotspot"]),),
                     detail=f"the capacitor impedance depends on its temperature and the operating temperature is not "
                            f"established ({state['basis']})").to_dict()
    b = _band_quantity(net, st, branch, qty, net.ac & (net.f <= bw * (1 + 1e-12)))
    b2 = refine(branch, qty, bw)
    delta = max((abs(b2[k] - b[k]) for k in ("lo", "hi") if math.isfinite(b[k]) and math.isfinite(b2[k])),
                default=0.0)
    lo, hi = b["lo"], b["hi"]
    rng = (shown(b["value"], "ripple") if b["value"] is not None else "impedance-dependent") + \
          (f" in [{shown(lo, 'ripple')}, {shown(hi, 'ripple')}]" if b["uncovered_harmonics"] else "")
    unc_txt = (f"; {b['uncovered_harmonics']} harmonics in the band ({b['uncovered_band_Hz'][0]:g}-"
               f"{b['uncovered_band_Hz'][1]:g} Hz) lie outside the ESR table: bounded over every passive capacitor "
               f"impedance" if b["uncovered_harmonics"] else "")
    det = (f"{rng} vs limit {lim:g} at {shown(state['T_C'], 'temperature')} degC ({state['basis']}); sampling resolution "
           f"{delta:.2g} (value change at twice the samples per carrier){unc_txt}; ideal brick-wall filter at "
           f"{bw:g} Hz; sampled operating point")
    if hi + delta <= lim:
        st_, rs = Status.FEASIBLE, ()
    elif lo - delta > lim:
        st_, rs = Status.INFEASIBLE, (Reason.CONSTRAINT_VIOLATION,)
    else:
        st_ = Status.UNKNOWN
        rs = (Reason.BOUND_INCONCLUSIVE,) if b["uncovered_harmonics"] else (Reason.UNCERTAINTY_OVERLAP,)
    kind = EvidenceKind.CERTIFIED_BOUND if b["uncovered_harmonics"] else EvidenceKind.DIRECT_EVALUATION
    out["requirement_value"] = b["value"]
    out["requirement"] = {"location": loc, "quantity": qty, "branch": branch, "bandwidth_Hz": bw, "limit": lim,
                          "value": b["value"], "bounds": [lo, hi], "resolution_delta": delta,
                          "uncovered_harmonics_in_band": b["uncovered_harmonics"],
                          "uncovered_band_Hz": b["uncovered_band_Hz"], "state_T_C": state["T_C"]}
    return Claim("ripple_requirement", st_, q, method, reasons=rs, evidence=(Evidence.make(kind, det),),
                 detail=det).to_dict()


def _claims(out: dict, req: dict | None, net: _Network, st: dict, source, refine) -> dict:
    claims = {"ripple_requirement": _requirement_claim(out, req, net, st, source, refine)}
    hot, state = out["hotspot"], out["state"]
    title, method = "capacitor ESR loss", "harmonic sum with ESR(f, T) at the stated capacitor temperature"
    if not net.complete:
        cov = out["esr_coverage"]
        claims["capacitor_loss"] = Claim(
            "capacitor_loss", Status.UNKNOWN, title, method, reasons=(Reason.OUTSIDE_MODEL_DOMAIN,),
            detail=f"{cov['uncovered_harmonics']} harmonics lie outside the ESR table "
                   f"[{cov['table_band_Hz'][0]:g}, {cov['table_band_Hz'][1]:g}] Hz "
                   f"({cov['inverter_I2_share_outside'] * 100:.3g} % of the inverter AC current-squared, more than "
                   f"{100 * UNCOVERED_I2_SHARE:g} %): the ESR where the current is is unknown - not "
                   f"established").to_dict()
    elif not state["esr_established"]:
        claims["capacitor_loss"] = Claim(
            "capacitor_loss", Status.UNKNOWN, title, method, reasons=(_state_reason(hot),),
            detail=f"ESR depends on the capacitor temperature, which is not established ({state['basis']})").to_dict()
    else:
        mb, pi = out["model_band"], out["P_cap_indicative_W"]
        claims["capacitor_loss"] = Claim(
            "capacitor_loss", Status.FEASIBLE, title, method,
            qualifiers=(f"includes {pi:.2g} W ({100 * pi / max(out['P_cap_W'], 1e-300):.2g} % of the loss) for the "
                        f"{100 * mb['inverter_I2_share_outside']:.2g} % of the inverter AC current-squared outside the "
                        f"ESR table, taken at the table's largest ESR (an assumption, not data)",)
            if pi > 1e-12 else (),
            detail=f"{shown(out['P_cap_W'], 'loss')} W at {shown(state['T_C'], 'temperature')} degC ({state['basis']}); "
                   f"sampling resolution {out['P_cap_resolution_W']:.2g} W"
                   + ("" if out.get("hotspot_resolution_K") is None else
                      f" ({out['hotspot_resolution_K']:.2g} K at the hotspot)")
                   + " (value change at twice the samples per carrier)").to_dict()
    life = out["life"]
    q_life = "capacitor life at this operating point"
    if life["status"] != "CONDITIONAL":
        claims["capacitor_life"] = Claim("capacitor_life", Status.UNKNOWN, q_life, "supplier life data only",
                                         reasons=(Reason.MISSING_INPUT,),
                                         detail=life.get("note") or life.get("reason", "")).to_dict()
    elif life.get("required_h") is None:
        # an expected life is not a pass: 13 h showed as PASS (engineering review 2 of 63a2b61, F-16)
        claims["capacitor_life"] = Claim(
            "capacitor_life", Status.UNKNOWN, q_life, "supplier life data only",
            reasons=(Reason.REQUIREMENT_INCOMPLETE,),
            detail=f"expected life {life['hours_at_hotspot']:.3g} h at the hotspot (supplier table); no required life "
                   f"stated - nothing to compare it with").to_dict()
    else:
        met = life["hours_at_hotspot"] >= life["required_h"]
        claims["capacitor_life"] = Claim(
            "capacitor_life", Status.FEASIBLE if met else Status.INFEASIBLE, q_life, "supplier life data only",
            reasons=() if met else (Reason.RATING_NOT_MET,),
            qualifiers=("nominal supplier life (no scatter declared); constant conditions at this operating point",),
            detail=f"expected {life['hours_at_hotspot']:.3g} h vs required {life['required_h']:.3g} h at "
                   f"{out['hotspot']['T_hot_C']:.3g} degC").to_dict()
    return claims
