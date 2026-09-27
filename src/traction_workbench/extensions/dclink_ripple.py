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
from ..models.flux import _finite
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

TWO_PI = 2.0 * math.pi


def _duty_set(theta: np.ndarray, m: float, modulation: str) -> np.ndarray:
    """Duties (3 x N) for the phase references m*cos(theta - 2pi k/3) (m = V_pk / (Vdc/2))."""
    v = np.vstack([m * np.cos(theta - TWO_PI * k / 3) for k in range(3)])     # in units of Vdc/2
    if modulation == "spwm":
        v0 = 0.0 * theta
    elif modulation == "svpwm":
        v0 = -0.5 * (v.max(axis=0) + v.min(axis=0))
    elif modulation == "dpwm1":
        k = np.argmax(np.abs(v), axis=0)
        vm = v[k, np.arange(v.shape[1])]
        v0 = np.sign(vm) * 1.0 - vm
    else:
        raise InputValidationError("modulation must be spwm, svpwm or dpwm1", field="modulation")
    d = 0.5 * (1.0 + v + v0)
    if np.any(d < -1e-9) or np.any(d > 1 + 1e-9):
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


@dataclass(frozen=True)
class CapacitorBank:
    C_F: float
    ESR_ohm_table: tuple                  # ((f_Hz, ESR_ohm), ...) at the reference temperature
    ESL_H: float = 0.0
    Rth_K_per_W: float | None = None      # hotspot-to-reference thermal resistance
    ESR_temp_coeff_per_K: float = 0.0     # ESR(T) = ESR_ref * (1 + a (T - T_ref)), declared
    T_ref_C: float = 25.0
    life_hours_table: tuple = ()          # ((T_hot_C, hours), ...) supplier life data for this exact series
    life_voltage_V: float | None = None   # voltage at which the life table holds
    life_basis: str = ""                  # supplier document / revision / failure criterion
    count: int = 1                        # identical capacitors in parallel (equal split only with symmetry declared)
    symmetric_layout: bool = False

    def __post_init__(self):
        if _finite("C_F", self.C_F) <= 0:
            raise InputValidationError("capacitance must be > 0", field="C_F")
        tab = tuple((float(f), float(r)) for f, r in self.ESR_ohm_table)
        if len(tab) < 1 or any(f <= 0 or r <= 0 for f, r in tab) or any(b[0] <= a[0] for a, b in zip(tab, tab[1:])):
            raise InputValidationError("ESR table needs increasing frequencies > 0 and ESR > 0", field="ESR_ohm_table")
        object.__setattr__(self, "ESR_ohm_table", tab)
        if _finite("ESL_H", self.ESL_H) < 0:
            raise InputValidationError("ESL must be >= 0", field="ESL_H")
        if not isinstance(self.count, int) or self.count < 1:
            raise InputValidationError("count must be an integer >= 1", field="count")
        if self.count > 1 and not self.symmetric_layout:
            raise InputValidationError("several capacitors: an equal current split needs a declared symmetric layout "
                                       "(otherwise give each branch)", field="symmetric_layout")

    def esr(self, f: np.ndarray, T_C: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        """ESR at f (log-frequency interpolation inside the table) and a coverage mask (no extrapolation)."""
        fs = np.array([a for a, _ in self.ESR_ohm_table])
        rs = np.array([b for _, b in self.ESR_ohm_table])
        f = np.asarray(f, dtype=float)
        inside = (f >= fs[0] * (1 - 1e-12)) & (f <= fs[-1] * (1 + 1e-12))
        if fs.size == 1:
            r = np.full_like(f, rs[0])
        else:
            r = np.interp(np.log(np.clip(f, fs[0], fs[-1])), np.log(fs), rs)
        if T_C is not None and self.ESR_temp_coeff_per_K:
            r = r * (1.0 + self.ESR_temp_coeff_per_K * (T_C - self.T_ref_C))
        return r / self.count, inside

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

    def z(self, f: np.ndarray) -> np.ndarray:
        return self.R_ohm + 1j * TWO_PI * np.asarray(f, dtype=float) * self.L_H


REQUIRED_REQ_FIELDS = ("location", "quantity", "limit", "bandwidth_Hz")
MODEL_BAND_CARRIERS = 10          # ideal-switch spectrum used up to 10 carrier groups (edges/ringing beyond)
LOCATIONS = ("dc_link_bus", "capacitor_branch", "inverter_dc_input", "battery_terminal")
QUANTITIES = ("voltage_pp", "voltage_ac_rms", "current_ac_rms", "capacitor_current_rms")


def ripple_analysis(I_pk: float, m: float, phi_rad: float, f_e_Hz: float, fsw_Hz: float, Vdc_V: float,
                    bank: CapacitorBank, source: SourceImpedance | None = None, modulation: str = "svpwm",
                    requirement: dict | None = None, T_ref_C: float | None = None,
                    samples_per_carrier: int = 128) -> dict:
    wave = switching_waveform(I_pk, m, phi_rad, f_e_Hz, fsw_Hz, modulation, samples_per_carrier)
    sp = spectrum(wave["i_inv_A"], wave["f_e_Hz"])
    f = sp["f_Hz"]
    X = sp["complex"]
    ac = np.arange(f.size) > 0
    Zc = np.where(ac, bank.impedance(np.where(ac, f, 1.0), T_ref_C), np.inf)
    assumption = None
    if source is None:
        k = np.where(ac, 1.0 + 0j, 0.0)                      # stiff current source: all AC into the capacitor
        assumption = ("source impedance not declared: capacitor current computed for a stiff current-source "
                      "battery (all AC into the capacitor) - an assumption, not a bound (resonance can exceed it)")
    else:
        Zs = source.z(f)
        k = np.where(ac, Zs / (Zs + Zc), 0.0)
    Ic = X * k                                                # complex capacitor-branch current (one-sided)
    Is = X * (1 - k)                                          # source current (incl. DC)
    ic_rms_h = np.abs(Ic) * math.sqrt(2.0)
    ic_rms_h[0] = 0.0
    I_c_rms = float(np.sqrt(np.sum(ic_rms_h ** 2)))
    is_rms_h = np.abs(Is) * math.sqrt(2.0)
    is_rms_h[0] = 0.0
    I_s_ac = float(np.sqrt(np.sum(is_rms_h ** 2)))
    # capacitor dielectric voltage and bus terminal voltage ripple (time domain via inverse FFT)
    n = wave["i_inv_A"].size
    Vc = -Ic * np.where(ac, Zc, 0.0)                          # current out of the capacitor into the bridge
    Vc[0] = 0.0
    v_full = np.fft.irfft(Vc * n, n)
    v_pp = float(v_full.max() - v_full.min())
    v_rms = float(np.sqrt(np.mean(v_full ** 2)))
    esr_h, inside = bank.esr(np.where(ac, f, 1.0), T_ref_C)
    covered = inside | ~ac
    share_out = float(np.sum(ic_rms_h[~covered] ** 2) / max(np.sum(ic_rms_h ** 2), 1e-300))
    loss_h = ic_rms_h ** 2 * esr_h
    p_cap = float(np.sum(loss_h[covered]))
    loss_established = share_out <= 1e-6
    hot = None
    if bank.Rth_K_per_W is not None and loss_established and T_ref_C is not None:
        T = T_ref_C
        for _ in range(50):
            esr_T, _ = bank.esr(np.where(ac, f, 1.0), T)
            P = float(np.sum((ic_rms_h ** 2 * esr_T)[covered]))
            T_new = T_ref_C + bank.Rth_K_per_W * P
            if abs(T_new - T) < 1e-4:
                break
            T = T_new
        hot = {"T_hot_C": T_new, "P_W": P, "converged": abs(T_new - T) < 1e-4}
    life = {"status": "UNKNOWN", "reason": "no supplier life data for this capacitor series (no generic '10 K halves "
                                            "the life' rule is applied)"}
    if bank.life_hours_table and hot is not None:
        tab = sorted(bank.life_hours_table)
        Ts = [a for a, _ in tab]
        if Ts[0] <= hot["T_hot_C"] <= Ts[-1]:
            hrs = float(np.exp(np.interp(hot["T_hot_C"], Ts, [math.log(b) for _, b in tab])))
            vok = bank.life_voltage_V is None or Vdc_V <= bank.life_voltage_V
            life = {"status": "CONDITIONAL" if vok else "UNKNOWN",
                    "hours_at_hotspot": hrs if vok else None,
                    "basis": bank.life_basis or "supplier life table (basis not stated)",
                    "note": ("supplier table interpolated at the hotspot for constant conditions; a variable mission "
                             "needs the supplier's accumulation rule" if vok else
                             f"Vdc {Vdc_V:g} V above the life-table voltage {bank.life_voltage_V:g} V")}
        else:
            life = {"status": "UNKNOWN", "reason": f"hotspot {hot['T_hot_C']:.4g} degC outside the life table "
                                                   f"[{Ts[0]:g}, {Ts[-1]:g}] degC (no extrapolation)"}
    out = {
        "operating": {"I_pk_A": I_pk, "modulation_index": m, "phi_deg": math.degrees(phi_rad), "f_e_Hz": f_e_Hz,
                      "fsw_requested_Hz": fsw_Hz, "fsw_used_Hz": wave["fsw_used_Hz"],
                      "carrier_ratio": wave["carrier_ratio"], "modulation": modulation, "Vdc_V": Vdc_V},
        "average_model": average_model(I_pk, m, phi_rad, modulation),
        "I_dc_A": float(X[0].real), "I_inv_ac_rms_A": float(np.sqrt(np.sum(np.abs(X[1:]) ** 2) * 2.0)),
        "I_cap_rms_A": I_c_rms, "I_source_ac_rms_A": I_s_ac,
        "V_ripple_pp_V": v_pp, "V_ripple_ac_rms_V": v_rms,
        "P_cap_W": p_cap if loss_established else None, "P_cap_covered_W": p_cap,
        "current_share_outside_ESR_band": share_out, "hotspot": hot, "life": life,
        "parseval_residual_A": sp["parseval_residual"], "assumption": assumption,
        "spectrum": {"f_Hz": f[: min(f.size, 4 * wave["carrier_ratio"] + 8)].tolist(),
                     "I_inv_rms_A": (np.abs(X) * math.sqrt(2.0))[: min(f.size, 4 * wave["carrier_ratio"] + 8)].tolist(),
                     "I_cap_rms_A": ic_rms_h[: min(f.size, 4 * wave["carrier_ratio"] + 8)].tolist()},
        "waveform": {"t_s": wave["t_s"][:: max(1, n // 4000)].tolist(),
                     "i_inv_A": wave["i_inv_A"][:: max(1, n // 4000)].tolist(),
                     "v_ripple_V": v_full[:: max(1, n // 4000)].tolist()},
        "not_modelled": ["phase-current ripple", "dead time", "device edges / ringing", "overmodulation",
                         "asynchronous-carrier sidebands (synchronous ratio used)"],
    }
    def band_value(qty: str, loc: str, bw: float) -> float:
        """Requirement quantity with harmonics up to the measurement bandwidth (ideal brick-wall filter)."""
        keep = f <= bw * (1 + 1e-12)
        if qty in ("voltage_pp", "voltage_ac_rms"):
            vb = np.where(keep, Vc, 0.0)
            vt = np.fft.irfft(vb * n, n)
            return float(vt.max() - vt.min()) if qty == "voltage_pp" else float(np.sqrt(np.mean(vt ** 2)))
        if qty == "capacitor_current_rms":
            return float(np.sqrt(np.sum(np.where(keep, ic_rms_h, 0.0) ** 2)))
        src = is_rms_h if loc == "battery_terminal" else np.abs(X) * math.sqrt(2.0)
        return float(np.sqrt(np.sum(np.where(keep & ac, src, 0.0) ** 2)))

    out["band_value"] = band_value
    out["claims"] = _claims(out, requirement, source, loss_established)
    del out["band_value"]
    return out


def _claims(out: dict, req: dict | None, source, loss_established: bool) -> dict:
    claims = {}
    if not req:
        claims["ripple_requirement"] = Claim("ripple_requirement", Status.UNKNOWN, "customer DC ripple requirement",
                                             "not stated", reasons=(Reason.REQUIREMENT_INCOMPLETE,),
                                             detail="no ripple requirement stated").to_dict()
    else:
        missing = [k for k in REQUIRED_REQ_FIELDS if req.get(k) in (None, "")]
        loc, qty = req.get("location"), req.get("quantity")
        if loc not in (None, "") and loc not in LOCATIONS:
            missing.append(f"location one of {LOCATIONS}")
        if qty not in (None, "") and qty not in QUANTITIES:
            missing.append(f"quantity one of {QUANTITIES}")
        if missing:
            claims["ripple_requirement"] = Claim(
                "ripple_requirement", Status.UNKNOWN, "customer DC ripple requirement", "requirement definition",
                reasons=(Reason.REQUIREMENT_INCOMPLETE,),
                detail="requirement incomplete: " + ", ".join(missing) + " (a limit without location, quantity and "
                       "measurement bandwidth cannot be judged)").to_dict()
        else:
            lim = float(req["limit"])
            bw = float(req["bandwidth_Hz"])
            val = out["band_value"](qty, loc, bw)
            needs_source = loc in ("battery_terminal", "dc_link_bus") or qty in ("voltage_pp", "voltage_ac_rms",
                                                                                "capacitor_current_rms")
            q = f"{qty} at {loc} <= {lim:g} (harmonics up to {bw:g} Hz)"
            out["requirement_value"] = val
            if needs_source and source is None:
                st, rs = Status.UNKNOWN, (Reason.MISSING_INPUT,)
                det = f"model value {val:.4g} computed with an assumed stiff source: the source impedance is needed"
            elif bw > out["operating"]["fsw_used_Hz"] * MODEL_BAND_CARRIERS:
                st, rs = Status.UNKNOWN, (Reason.OUTSIDE_MODEL_DOMAIN,)
                det = (f"measurement bandwidth {bw:g} Hz reaches beyond {MODEL_BAND_CARRIERS} carrier groups of the "
                       f"ideal-switch model (edges / ringing / parasitics not modelled)")
            else:
                st = Status.FEASIBLE if val <= lim else Status.INFEASIBLE
                rs = () if st is Status.FEASIBLE else (Reason.CONSTRAINT_VIOLATION,)
                det = (f"{val:.4g} vs limit {lim:g} (declared network; ideal brick-wall measurement filter at "
                       f"{bw:g} Hz; sampled operating point)")
            claims["ripple_requirement"] = Claim("ripple_requirement", st, q, "switching-function network model",
                                                 reasons=rs, evidence=(Evidence.make(EvidenceKind.DIRECT_EVALUATION,
                                                                                     det),), detail=det).to_dict()
    if loss_established:
        claims["capacitor_loss"] = Claim("capacitor_loss", Status.FEASIBLE if out["hotspot"] is None or
                                         out["hotspot"]["converged"] else Status.UNKNOWN,
                                         "capacitor ESR loss", "harmonic sum with ESR(f)",
                                         detail=f"{out['P_cap_W']:.4g} W").to_dict()
    else:
        claims["capacitor_loss"] = Claim("capacitor_loss", Status.UNKNOWN, "capacitor ESR loss",
                                         "harmonic sum with ESR(f)", reasons=(Reason.OUTSIDE_MODEL_DOMAIN,),
                                         detail=f"{out['current_share_outside_ESR_band'] * 100:.3g} % of the "
                                                f"current-squared lies outside the ESR table band: not established"
                                         ).to_dict()
    life = out["life"]
    claims["capacitor_life"] = Claim("capacitor_life", Status.UNKNOWN if life["status"] != "CONDITIONAL" else
                                     Status.FEASIBLE, "capacitor life at this operating point",
                                     "supplier life data only", reasons=() if life["status"] == "CONDITIONAL" else
                                     (Reason.MISSING_INPUT,),
                                     detail=life.get("note") or life.get("reason", "")).to_dict()
    return claims
