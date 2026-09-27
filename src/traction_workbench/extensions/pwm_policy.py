"""Variable switching-frequency (PWM carrier) policy evaluation (variable-PWM / anti-jerk addendum, P1-PWM).

"Variable frequency" here is the PWM carrier / switching frequency changed per operating point - not the electrical
fundamental and not the motor speed.  The question is not "which fsw is optimal" but: under the SAME shaft demand,
source and cooling, does an admissible, CAUSAL policy keep current / voltage / thermal / timing / ripple constraints
and improve energy or margins?

Separate quantities are kept separate: carrier frequency, actual switching-event rate, ADC sample / current-loop
update rate, the delay ledger, the electrical frequency f_e = p |omega_m| / (2 pi) and the pulse ratio
N_p = f_carrier / f_e (N/A at standstill, no universal approval threshold).

Physics is reused, not duplicated: device losses come from the datasheet module model at the scheduled frequency
(coupled electrothermal point through the drive), the switch-node edges from the edge generator of the EMI module,
the capacitor current from the DC-link ripple analysis.  What is added here: the stateful schedule (hysteresis,
dwell, fallback), the phase-current ripple of an RL load (time integration AND independent line spectrum), the
minimum-pulse and gate-event legality checks, the delay ledger with deadlines, the current-loop phase margin, and
the comparison that never trades a mandatory constraint for efficiency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..errors import InputValidationError
from ..models.flux import _finite
from .emi import SwitchingSource, _duties, _edge_lines, pwm_edges

TWO_PI = 2.0 * math.pi
VARS = ("speed_rpm", "torque_abs_Nm", "Vdc_V", "sensor_temp_C")


# --------------------------------------------------------------------------------------------- policy

@dataclass(frozen=True)
class FswRule:
    """One region of a deterministic operating-point schedule. None = no condition on that measurement."""

    name: str
    fsw_Hz: float
    speed_rpm: tuple | None = None
    torque_abs_Nm: tuple | None = None
    Vdc_V: tuple | None = None
    sensor_temp_C: tuple | None = None        # an OBSERVABLE temperature (e.g. module NTC), never the true Tj
    protective: bool = False                  # a protective rule pre-empts the dwell time (e.g. hot -> lower fsw)

    def __post_init__(self):
        if _finite("fsw_Hz", self.fsw_Hz) <= 0:
            raise InputValidationError("rule fsw must be > 0", field="fsw_Hz")
        for v in VARS:
            r = getattr(self, v)
            if r is not None and (len(r) != 2 or float(r[0]) > float(r[1])):
                raise InputValidationError(f"{v} range must be (low, high)", field=v)

    def contains(self, meas: dict, widen: dict | None = None) -> bool:
        for v in VARS:
            r = getattr(self, v)
            if r is None:
                continue
            x = meas.get(v)
            if x is None:
                return False                  # a condition on an unavailable measurement cannot be evaluated
            w = (widen or {}).get(v, 0.0)
            if not (r[0] - w <= x <= r[1] + w):
                return False
        return True


@dataclass(frozen=True)
class FswSchedule:
    """Stateful causal policy q_next = policy(q, available measurements): first matching rule in priority order;
    the active rule is kept while the measurements stay within its ranges widened by the hysteresis; a change
    waits for the minimum dwell unless the new rule is protective; no match -> fallback."""

    name: str
    rules: tuple
    fallback_fsw_Hz: float
    hysteresis: tuple = ()                    # (("speed_rpm", 150.0), ("torque_abs_Nm", 8.0), ...)
    min_dwell_s: float = 0.0
    revision: str = "1"
    basis: str = ""

    def __post_init__(self):
        if not self.rules:
            raise InputValidationError("a schedule needs at least one rule", field="rules")
        if _finite("fallback_fsw_Hz", self.fallback_fsw_Hz) <= 0:
            raise InputValidationError("fallback fsw must be > 0", field="fallback_fsw_Hz")
        if _finite("min_dwell_s", self.min_dwell_s) < 0:
            raise InputValidationError("minimum dwell must be >= 0", field="min_dwell_s")
        for k, w in self.hysteresis:
            if k not in VARS or _finite(k, w) < 0:
                raise InputValidationError(f"hysteresis {k!r} must be a measurement name with a margin >= 0",
                                           field="hysteresis")

    @property
    def is_fixed(self) -> bool:
        return len(self.rules) == 1 and all(getattr(self.rules[0], v) is None for v in VARS)

    def step(self, state: dict | None, meas: dict, t_s: float) -> dict:
        """state = {"rule": index or -1 (fallback), "since_s": time of the last change}."""
        hyst = dict(self.hysteresis)
        strict = next((i for i, r in enumerate(self.rules) if r.contains(meas)), -1)
        if state is None:
            return {"rule": strict, "since_s": t_s, "changed": False, "reason": "initial"}
        cur = state["rule"]
        if cur >= 0 and self.rules[cur].contains(meas, hyst):
            # stay, unless a higher-priority rule matches strictly
            if strict == -1 or strict >= cur:
                return {**state, "changed": False, "reason": "held (inside the hysteresis band)"}
        target = strict
        if target == cur:
            return {**state, "changed": False, "reason": "unchanged"}
        protective = target >= 0 and self.rules[target].protective
        if t_s - state["since_s"] < self.min_dwell_s - 1e-12 and not protective:
            return {**state, "changed": False, "reason": "change deferred (minimum dwell)", "pending": target}
        return {"rule": target, "since_s": t_s, "changed": True,
                "reason": "protective pre-emption" if protective else "rule change"}

    def fsw(self, state: dict) -> float:
        return self.fallback_fsw_Hz if state["rule"] < 0 else self.rules[state["rule"]].fsw_Hz

    def describe(self) -> dict:
        return {"name": self.name, "revision": self.revision, "fixed": self.is_fixed, "min_dwell_s": self.min_dwell_s,
                "hysteresis": dict(self.hysteresis), "fallback_fsw_Hz": self.fallback_fsw_Hz, "basis": self.basis,
                "rules": [{"name": r.name, "fsw_Hz": r.fsw_Hz, "protective": r.protective,
                           **{v: (None if getattr(r, v) is None else list(getattr(r, v))) for v in VARS}}
                          for r in self.rules]}


def fixed_schedule(fsw_Hz: float, name: str | None = None) -> FswSchedule:
    return FswSchedule(name or f"fixed {fsw_Hz / 1e3:g} kHz", (FswRule("fixed", fsw_Hz),), fsw_Hz,
                       basis="fixed-frequency baseline")


def replay_schedule(schedule: FswSchedule, t_s, meas_series: list[dict], chatter_window_s: float = 0.01) -> dict:
    """The policy on a measured / simulated sequence: fsw(t), transitions, deferred changes and chatter (a change
    faster than the declared dwell, or faster than ``chatter_window_s`` - a declared review window, not a universal
    threshold)."""
    t = np.asarray(t_s, float)
    state = None
    fsw, rules, events = [], [], []
    deferred = 0
    for k, (tk, m) in enumerate(zip(t, meas_series)):
        state = schedule.step(state, m, float(tk))
        if state.get("changed"):
            events.append({"t_s": float(tk), "to": state["rule"], "reason": state["reason"]})
        if "deferred" in state.get("reason", ""):
            deferred += 1
        fsw.append(schedule.fsw(state))
        rules.append(state["rule"])
    dwell = np.diff([0.0] + [e["t_s"] for e in events]) if events else np.array([])
    tol = 1e-9 * max(1.0, float(t[-1]) if t.size else 1.0)
    short = [e for e, d in zip(events[1:], dwell[1:]) if d < schedule.min_dwell_s - tol]
    min_int = float(dwell[1:].min()) if dwell.size > 1 else None
    span = float(t[-1] - t[0]) if t.size > 1 else 0.0
    return {"fsw_Hz": np.array(fsw), "rule": np.array(rules), "transitions": events, "n_transitions": len(events),
            "transition_rate_per_s": len(events) / span if span > 0 else None,
            "deferred_samples": deferred, "min_interval_s": min_int, "dwell_violations": len(short),
            "chatter": bool(short) or (min_int is not None and min_int < chatter_window_s - tol),
            "chatter_window_s": chatter_window_s}


# --------------------------------------------------------------------------------------------- timing

@dataclass(frozen=True)
class TimingConfig:
    """Declared delay chain of the current controller (never inferred from a Python run time)."""

    sample_to_latch_s: float                  # ADC aperture + conversion + channel skew + WCET + register write
    filter_delay_s: float = 0.0               # analog / anti-alias group delay at the loop frequencies
    updates_per_period: int = 1               # duty updates per carrier period (1 single, 2 double update)
    modulator_delay_fraction: float = 0.5     # equivalent modulator delay as a fraction of the update period
    min_pulse_s: float = 0.0                  # shortest gate pulse the driver / device accepts (declared)
    basis: str = ""
    wcet_source: str = "declared estimate"    # "measured on target" | "declared estimate"

    def __post_init__(self):
        for n in ("sample_to_latch_s", "filter_delay_s", "min_pulse_s"):
            if _finite(n, getattr(self, n)) < 0:
                raise InputValidationError(f"{n} must be >= 0", field=n)
        if self.updates_per_period not in (1, 2):
            raise InputValidationError("updates per period must be 1 or 2", field="updates_per_period")
        if not (0.0 <= _finite("modulator_delay_fraction", self.modulator_delay_fraction) <= 1.0):
            raise InputValidationError("modulator delay fraction must be in [0, 1]", field="modulator_delay_fraction")
        if not self.basis.strip():
            raise InputValidationError("the delay chain needs its basis (target measurement / timer configuration)",
                                       field="basis")


def delay_ledger(fsw_Hz: float, tc: TimingConfig) -> dict:
    """Sample -> latch -> reload -> modulator; each delay counted once (no ZOH on top of a lumped lag)."""
    T_upd = 1.0 / (fsw_Hz * tc.updates_per_period)
    deadline_ok = tc.sample_to_latch_s <= T_upd
    transport = T_upd if deadline_ok else None
    tau = None if transport is None else tc.filter_delay_s + transport + tc.modulator_delay_fraction * T_upd
    return {"fsw_Hz": fsw_Hz, "update_period_s": T_upd, "sample_to_latch_s": tc.sample_to_latch_s,
            "deadline_s": T_upd, "deadline_margin_s": T_upd - tc.sample_to_latch_s, "deadline_ok": deadline_ok,
            "items": [("filter", tc.filter_delay_s), ("sample -> applied at the next reload", transport),
                      ("modulator (declared fraction)", None if transport is None else tc.modulator_delay_fraction * T_upd)],
            "total_delay_s": tau, "wcet_source": tc.wcet_source,
            "note": "the delay chain is the declared target configuration; a missed deadline is a timing violation "
                    "(the result would slip by one update), not a longer delay to be tuned around"}


def phase_lag_deg(f_Hz: float, tau_s: float) -> float:
    return 360.0 * f_Hz * tau_s


@dataclass(frozen=True)
class CurrentLoop:
    """PI current loop on one axis: C(s) = Kp + Ki/s, plant 1 / (R + s L_diff), pure delay tau."""

    L_H: float
    R_ohm: float
    Kp: float
    Ki: float
    gain_mapping: str = "continuous"          # "continuous": Ki*Ts remapped at every period; "fixed_discrete": not
    reference_fsw_Hz: float | None = None     # the period at which fixed discrete gains were tuned
    basis: str = ""

    def __post_init__(self):
        for n in ("L_H", "Kp"):
            if _finite(n, getattr(self, n)) <= 0:
                raise InputValidationError(f"{n} must be > 0", field=n)
        for n in ("R_ohm", "Ki"):
            if _finite(n, getattr(self, n)) < 0:
                raise InputValidationError(f"{n} must be >= 0", field=n)
        if self.gain_mapping not in ("continuous", "fixed_discrete"):
            raise InputValidationError("gain mapping must be continuous or fixed_discrete", field="gain_mapping")
        if self.gain_mapping == "fixed_discrete" and not self.reference_fsw_Hz:
            raise InputValidationError("fixed discrete gains need the reference frequency they were tuned at",
                                       field="reference_fsw_Hz")

    def effective_Ki(self, fsw_Hz: float, updates_per_period: int = 1) -> float:
        if self.gain_mapping == "continuous":
            return self.Ki
        # K_i,disc = Ki * Ts_ref is kept; applied at Ts it acts as Ki * Ts_ref / Ts
        return self.Ki * (fsw_Hz / self.reference_fsw_Hz)

    def margins(self, tau_s: float, Ki: float | None = None) -> dict:
        Ki = self.Ki if Ki is None else Ki

        def L(w):
            return (self.Kp + Ki / (1j * w)) * np.exp(-1j * w * tau_s) / (self.R_ohm + 1j * w * self.L_H)
        w = np.logspace(0, 7, 4000)
        mag = np.abs(L(w))
        idx = np.nonzero((mag[:-1] >= 1.0) & (mag[1:] < 1.0))[0]
        if idx.size == 0:
            return {"crossover_Hz": None, "phase_margin_deg": None, "note": "no gain crossover in 0.16 Hz - 1.6 MHz"}
        a, b = w[idx[0]], w[idx[0] + 1]
        for _ in range(80):
            m = math.sqrt(a * b)
            if abs(L(m)) >= 1.0:
                a = m
            else:
                b = m
        wc = math.sqrt(a * b)
        # the delay phase is unwrapped explicitly: arg L = arg(PI) - w tau - arg(R + j w L)
        arg = np.angle(self.Kp + Ki / (1j * wc)) - wc * tau_s - np.angle(self.R_ohm + 1j * wc * self.L_H)
        pm = 180.0 + math.degrees(arg)
        return {"crossover_Hz": wc / TWO_PI, "phase_margin_deg": pm, "delay_phase_at_crossover_deg":
                math.degrees(wc * tau_s)}


# --------------------------------------------------------------------------------------------- waveform checks

def _source(Vdc, I_pk, beta, m, alpha, fe, fsw, t_dead=0.0, modulation="svpwm"):
    return SwitchingSource(Vdc, I_pk, beta, m, alpha, fe, fsw, 1e-9, 1e-9, t_dead, modulation, "PWM policy check")


def phase_ripple(Vdc_V: float, m: float, alpha_rad: float, fe_Hz: float, fsw_Hz: float, L_hf_H: float,
                 modulation: str = "svpwm", n_per_carrier: int = 256) -> dict:
    """Non-fundamental phase current of an RL load (star, isolated neutral), synchronous regular-sampled carrier.

    Time domain: i_h = (1/L_hf) * integral of (v_an - v_an,1), integrated EXACTLY between the switching edges
    (v_an is piecewise constant) and minus the exact fundamental, then sampled; zero mean.  Independent frequency
    domain: Parseval over the analytic edge-sum lines, |I_nu| = |V_nu| / (2 pi f_nu L_hf), nu >= 2.  R, back-EMF
    harmonics and the saturation of L_hf are neglected at carrier frequencies (a screening of the switching ripple;
    L_hf is DECLARED - e.g. the differential inductance at the carrier frequency)."""
    src = _source(Vdc_V, 1.0, 0.0, m, alpha_rad, fe_Hz, fsw_Hz, 0.0, modulation)
    e = pwm_edges(src)
    N = e["carrier_ratio"]
    T = e["period_s"]
    edges = e["edges"]
    nu = np.arange(1, int(60 * N) + 1)
    f = nu / T
    if edges:
        kk, t0, sgn, tau, _cur = (np.array(z) for z in zip(*edges))
        wgt = np.where(kk == 0, 2.0 / 3.0, -1.0 / 3.0)
        Vl = _edge_lines(t0, tau, sgn * wgt * Vdc_V, f, T)
    else:
        kk = t0 = sgn = wgt = np.array([])
        Vl = np.zeros(f.size, complex)
    # exact integral of v_an: piecewise constant between the (ideal) edges; initial pole levels of clamped legs
    Ts = T / N
    d0 = np.clip(_duties(np.array([TWO_PI * fe_Hz * 0.5 * Ts]), m, alpha_rad, modulation), 0.0, 1.0)[:, 0]
    level = np.array([1.0 if d0[j] >= 1.0 else 0.0 for j in range(3)])
    order = np.argsort(t0, kind="stable")
    tb, Fb = [0.0], [0.0]
    v = Vdc_V * (level[0] - level.mean())
    for idx in order:
        te = float(t0[idx])
        Fb.append(Fb[-1] + v * (te - tb[-1]))
        tb.append(te)
        level[int(kk[idx])] += float(sgn[idx])
        v = Vdc_V * (level[0] - level.mean())
    Fb.append(Fb[-1] + v * (T - tb[-1]))
    tb.append(T)
    n = N * n_per_carrier
    t = (np.arange(n) + 0.5) * T / n
    F = np.interp(t, tb, Fb)
    c1 = Vl[0]                                             # fundamental line (peak, complex): v1 = Re(c1 e^{jwt})
    w = TWO_PI * fe_Hz
    G = np.real(c1 * (np.exp(1j * w * t) - 1.0) / (1j * w))
    di = (F - G) / L_hf_H
    di = di - di.mean()
    rms_t = float(np.sqrt(np.mean(di ** 2)))
    I = np.abs(Vl[1:]) / (TWO_PI * f[1:] * L_hf_H)
    rms_f = float(np.sqrt(0.5 * np.sum(I ** 2)))
    return {"ripple_rms_A": rms_t, "ripple_rms_spectrum_A": rms_f, "ripple_pp_A": float(di.max() - di.min()),
            "ripple_peak_A": float(np.abs(di).max()), "carrier_ratio": N, "fsw_used_Hz": e["fsw_used_Hz"],
            "harmonic_f_Hz": f[1:], "harmonic_I_pk_A": I, "t_s": t, "di_A": di,
            "fundamental_V_pk": float(abs(c1)), "L_hf_H": L_hf_H, "overmodulation": e["overmodulation"],
            "note": "RL switching ripple (R, back-EMF harmonics and saturation of L_hf neglected); exact time "
                    "integration between the edges and the edge-sum spectrum are independent computations"}


def minimum_pulse(m: float, modulation: str, fsw_Hz: float, min_pulse_s: float, n_theta: int = 3600) -> dict:
    """Narrowest commanded gate pulse over the fundamental period (centre-aligned PWM: on d Ts, off (1-d) Ts)."""
    th = np.linspace(0.0, TWO_PI, n_theta, endpoint=False)
    d = np.clip(_duties(th, m, 0.0, modulation), 0.0, 1.0)
    sw = (d > 1e-12) & (d < 1 - 1e-12)
    Ts = 1.0 / fsw_Hz
    widths = np.where(sw, np.minimum(d, 1 - d) * Ts, np.inf)
    w = float(widths.min())
    return {"narrowest_pulse_s": None if math.isinf(w) else w, "min_pulse_s": min_pulse_s,
            "ok": math.isinf(w) or w >= min_pulse_s,
            "note": "a pulse narrower than the declared minimum is dropped or limited in hardware: the delivered "
                    "voltage and current leave the linear averaged model"}


def check_gate_events(edges_hi: list, edges_lo: list, min_pulse_s: float, deadtime_s: float, t_end_s: float) -> dict:
    """Legality of complementary gate signals of one leg (imported or generated): edges are (t, +1/-1).

    Detects missing / duplicate edges (two rises in a row), pulses shorter than the minimum and dead-time
    violations (upper on while lower on, or less than the dead time between them)."""
    problems = []

    def pulses(edges, name):
        out, level, t_on = [], 0, None
        for t, s in sorted(edges):
            if s == +1:
                if level == 1:
                    problems.append(f"{name}: duplicate rising edge at {t:.9g} s")
                level, t_on = 1, t
            else:
                if level == 0:
                    problems.append(f"{name}: falling edge without a rising edge at {t:.9g} s")
                else:
                    out.append((t_on, t))
                level = 0
        if level == 1:
            out.append((t_on, t_end_s))
        return out
    hi, lo = pulses(edges_hi, "upper"), pulses(edges_lo, "lower")
    for name, ps in (("upper", hi), ("lower", lo)):
        for a, b in ps:
            if b - a < min_pulse_s - 1e-15:
                problems.append(f"{name}: pulse {1e9 * (b - a):.1f} ns shorter than the minimum {1e9 * min_pulse_s:.1f} ns "
                                f"at {a:.9g} s")
    for a, b in hi:
        for c, d in lo:
            gap = max(c - b, a - d)                 # < 0: overlap (shoot-through); < dead time: violation
            if gap < deadtime_s - 1e-15:
                problems.append(f"dead time {1e9 * gap:.1f} ns < {1e9 * deadtime_s:.1f} ns between upper "
                                f"[{a:.9g}, {b:.9g}] and lower [{c:.9g}, {d:.9g}]")
    return {"ok": not problems, "problems": problems, "pulses_upper": len(hi), "pulses_lower": len(lo)}


def counter_pwm(writes: list, t_end_s: float, f_clk_Hz: float, deadtime_s: float, shadow: bool = True,
                counter_bits: int = 16) -> dict:
    """Up-down (centre-aligned) timer with action qualifiers and a dead-band unit, at the clock level.

    ``writes``: [(t_s, period_counts, compare_counts)] register writes (the first at t = 0).  The counter counts
    0 -> PRD -> 0; the PWM output is set on the up-count match counter == CMP and cleared on the down-count match.
    ``shadow=True``: PRD and CMP load at counter == 0 (atomic reload); ``False``: they take effect when written, so
    a write can skip a match (missing / never-ending pulse) or put PRD below a counter already counting up (the
    counter runs to its wrap).  The dead-band unit delays each rising edge of the upper and lower gate by the dead
    time; a PWM pulse shorter than the dead time disappears."""
    tick = 1.0 / f_clk_Hz
    n = int(round(t_end_s / tick))
    wmax = (1 << counter_bits) - 1
    ws = sorted(writes)
    wi = 0
    prd, cmp_ = ws[0][1], ws[0][2]
    pend = None
    wi = 1
    c, up, out = 0, True, 0
    pwm_edges_ = []
    per_period = []
    t_period = 0.0
    on_time = 0.0
    for i in range(n):
        t = i * tick
        while wi < len(ws) and ws[wi][0] <= t:
            if shadow:
                pend = (ws[wi][1], ws[wi][2])
            else:
                prd, cmp_ = ws[wi][1], ws[wi][2]
            wi += 1
        # action qualifiers at this count
        if up and c == cmp_ and out == 0:
            out = 1
            pwm_edges_.append((t, +1))
        elif (not up) and c == cmp_ and out == 1:
            out = 0
            pwm_edges_.append((t, -1))
        on_time += out * tick
        # advance the counter
        if up:
            if c >= prd:
                up = False
                c -= 1
            elif c >= wmax:
                c = 0                          # wrap (only reachable after an immediate PRD below the count)
            else:
                c += 1
        else:
            c -= 1
            if c <= 0:
                c, up = 0, True
                per_period.append({"t_start_s": t_period, "t_end_s": t + tick, "on_s": on_time})
                t_period, on_time = t + tick, 0.0
                if pend is not None:
                    prd, cmp_ = pend
                    pend = None
    # dead-band unit
    hi, lo = [], []
    level = 0
    for t, sgn in pwm_edges_:
        if sgn == +1:
            lo.append((t, -1)) if level == 0 else None
            hi.append((t + deadtime_s, +1))
        else:
            hi.append((t, -1))
            lo.append((t + deadtime_s, +1))
        level = 1 if sgn == +1 else 0
    # a pulse shorter than the dead time cancels: drop inverted (on after off) pairs
    def clean(ed):
        ed = sorted(ed)
        out_, i = [], 0
        while i < len(ed):
            if i + 1 < len(ed) and ed[i][1] == +1 and ed[i + 1][1] == -1 and ed[i + 1][0] <= ed[i][0]:
                i += 2
                continue
            out_.append(ed[i])
            i += 1
        return out_
    lo = [(0.0, +1)] + lo
    return {"upper": clean(hi), "lower": clean(lo), "pwm": pwm_edges_, "periods": per_period, "t_end_s": n * tick}


def transition_check(fsw_from_Hz: float, fsw_to_Hz: float, duty: float, deadtime_s: float, min_pulse_s: float,
                     f_clk_Hz: float = 100e6, write_fraction: float = 0.3, shadow: bool = True) -> dict:
    """One period change of one leg at the clock level: event legality and per-period duty error."""
    prd0 = int(round(f_clk_Hz / (2 * fsw_from_Hz)))
    prd1 = int(round(f_clk_Hz / (2 * fsw_to_Hz)))
    T0 = 2 * prd0 / f_clk_Hz
    writes = [(0.0, prd0, int(round((1 - duty) * prd0))),
              (T0 * (1 + write_fraction), prd1, int(round((1 - duty) * prd1)))]
    t_end = 3 * T0 + 4 * 2 * prd1 / f_clk_Hz
    sim = counter_pwm(writes, t_end, f_clk_Hz, deadtime_s, shadow)
    chk = check_gate_events(sim["upper"], sim["lower"], min_pulse_s, deadtime_s, sim["t_end_s"])
    duty_err = []
    for p in sim["periods"]:
        T = p["t_end_s"] - p["t_start_s"]
        duty_err.append(abs(p["on_s"] / T - duty) if T > 0 else None)
    worst = max((x for x in duty_err if x is not None), default=None)
    bad_period = [p for p in sim["periods"] if abs((p["t_end_s"] - p["t_start_s"]) - T0) > 2 / f_clk_Hz and
                  abs((p["t_end_s"] - p["t_start_s"]) - 2 * prd1 / f_clk_Hz) > 2 / f_clk_Hz]
    ok = chk["ok"] and (worst is None or worst <= 0.02) and not bad_period
    return {"ok": ok, "gate_events": chk, "worst_period_duty_error": worst, "irregular_periods": len(bad_period),
            "update": "shadow (atomic reload)" if shadow else "immediate write", "sim": sim}


# --------------------------------------------------------------------------------------------- harmonic losses

@dataclass(frozen=True)
class HarmonicLossData:
    """Declared motor data for PWM harmonics: R_ac/R_dc vs frequency (copper) and an optional upper bound of the
    iron / magnet harmonic loss per carrier frequency.  Without the bound the motor+inverter total is UNKNOWN."""

    f_Hz: tuple
    rac_over_rdc: tuple
    iron_bound_W: tuple = ()                  # ((fsw_Hz, W upper bound at the operating points of interest), ...)
    basis: str = ""

    def __post_init__(self):
        f = np.asarray(self.f_Hz, float)
        r = np.asarray(self.rac_over_rdc, float)
        if f.ndim != 1 or f.size < 2 or np.any(np.diff(f) <= 0) or f[0] < 0 or r.shape != f.shape or np.any(r < 1.0):
            raise InputValidationError("R_ac/R_dc table: increasing frequencies, ratios >= 1", field="rac_over_rdc")
        if not self.basis.strip():
            raise InputValidationError("harmonic loss data need their basis (FEA / measurement)", field="basis")

    def rac(self, f):
        f = np.asarray(f, float)
        fa = np.asarray(self.f_Hz, float)
        out = np.interp(f, fa, np.asarray(self.rac_over_rdc, float))
        return np.where((f >= fa[0]) & (f <= fa[-1]), out, np.nan)

    def iron_bound(self, fsw_Hz: float) -> float | None:
        for f, w in self.iron_bound_W:
            if abs(f - fsw_Hz) <= 1e-6 * f:
                return float(w)
        return None


def harmonic_copper_loss(rip: dict, Rs_ohm: float, data: HarmonicLossData | None) -> dict:
    """3 sum I_nu,rms^2 R_ac(f_nu) of the switching harmonics (the fundamental copper loss is counted separately
    by the drive model - never both from a total RMS)."""
    if data is None:
        return {"W": None, "reason": "no R_ac(f) data declared"}
    f = rip["harmonic_f_Hz"]
    I = rip["harmonic_I_pk_A"]
    ratio = data.rac(f)
    sig = I > 1e-6 * max(float(I.max()), 1e-12)
    if np.any(np.isnan(ratio[sig])):
        return {"W": None, "reason": "harmonics outside the declared R_ac(f) table (no extrapolation)"}
    P = 3.0 * float(np.sum(0.5 * I[sig] ** 2 * Rs_ohm * ratio[sig]))
    return {"W": P, "reason": ""}


# --------------------------------------------------------------------------------------------- policy comparison

@dataclass(frozen=True)
class PwmLimits:
    """Mandatory constraints of the comparison (declared; a missing limit is not a pass)."""

    Tj_max_C: float | None = None
    i_peak_incl_ripple_max_A: float | None = None     # device / over-current protection peak incl. switching ripple
    cap_rms_max_A: float | None = None
    phase_margin_min_deg: float | None = None
    pulse_ratio_min: float | None = None              # declared lower bound of the qualified PWM family (no default)


def _segment_eval(base_drive, cand, seg: dict, fsw: float, coolant_C: float, limits_dc, timing: TimingConfig,
                  loop: CurrentLoop | None, L_hf_H: float, harmonic: HarmonicLossData | None, bank, source,
                  modulation: str) -> dict:
    from ..analysis.efficiency import _module_point
    from ..scenario import Scenario
    from .dclink_ripple import ripple_analysis
    sc = Scenario("pwm", float(seg["speed_rpm"]), float(seg["Vdc_V"]), limits_dc, coolant_temp_C=coolant_C)
    r = _module_point(base_drive, cand, sc, float(seg["torque_Nm"]), coolant_C, fsw, None, None)
    out = {"fsw_Hz": fsw, "status": r["status"], "reason": r.get("reason", ""), "Tj_C": r.get("Tj_C")}
    p = r.get("point")
    led = delay_ledger(fsw, timing)
    out["timing"] = {k: led[k] for k in ("deadline_ok", "deadline_margin_s", "total_delay_s", "update_period_s")}
    if loop is not None and led["total_delay_s"] is not None:
        mg = loop.margins(led["total_delay_s"], loop.effective_Ki(fsw, timing.updates_per_period))
        out["timing"].update({"phase_margin_deg": mg["phase_margin_deg"], "crossover_Hz": mg["crossover_Hz"]})
    if p is None:
        return out
    d = r["detail"] or {}
    vdc = float(seg["Vdc_V"])
    m = float(d.get("modulation_index") or 0.0)
    fe_true = abs(float(seg["speed_rpm"])) * base_drive.motor.pole_pairs / 60.0
    fe = max(fe_true, fsw / 400.0)
    rip = phase_ripple(vdc, m, 0.0, fe, fsw, L_hf_H, modulation, n_per_carrier=128)
    ipk = p["i_peak_A"] + rip["ripple_peak_A"]
    mp = minimum_pulse(m, modulation, fsw, timing.min_pulse_s)
    hcu = harmonic_copper_loss(rip, base_drive.motor.Rs_ohm, harmonic)
    out.update({"P_inv_W": p["Pinv_W"], "P_cu_fund_W": p["Pcu_W"], "P_dc_W": p["Pdc_W"], "i_peak_A": p["i_peak_A"],
                "ripple_rms_A": rip["ripple_rms_A"], "ripple_peak_A": rip["ripple_peak_A"], "i_peak_incl_ripple_A": ipk,
                "ripple_quasi_static": fe_true < fsw / 400.0, "modulation_index": m,
                "pulse_ratio": (fsw / fe_true) if fe_true > 0 else None, "narrowest_pulse_s": mp["narrowest_pulse_s"],
                "min_pulse_ok": mp["ok"], "P_cu_harm_W": hcu["W"], "P_cu_harm_reason": hcu["reason"],
                "P_iron_harm_bound_W": None if harmonic is None else harmonic.iron_bound(fsw)})
    if bank is not None and fe_true > 0:
        pf = d.get("power_factor")
        phi = math.acos(max(-1.0, min(1.0, float(pf)))) if (pf is not None and math.isfinite(pf)) else 0.0
        cr = ripple_analysis(p["i_peak_A"], m, phi, fe_true, fsw, vdc, bank, source, modulation)
        out["I_cap_rms_A"] = cr["I_cap_rms_A"]
        out["P_cap_W"] = cr["P_cap_W"]
    return out


def evaluate_policies(base_drive, cand, segments: list[dict], policies: list, coolant_C: float, limits_dc,
                      timing: TimingConfig, L_hf_H: float, loop: CurrentLoop | None = None,
                      harmonic: HarmonicLossData | None = None, bank=None, source=None, modulation: str = "svpwm",
                      limits: PwmLimits | None = None) -> dict:
    """Every policy on the SAME trajectory, source and coolant; the first policy is the baseline.

    ``cand``: efficiency.ModuleCandidate (module data, own thermal path).  Segments carry duration_s, speed_rpm,
    torque_Nm, Vdc_V and optionally sensor_temp_C (the observable temperature the schedule may use)."""
    if not policies:
        raise InputValidationError("no policies", field="policies")
    lim = limits or PwmLimits()
    out = []
    for pol in policies:
        state, t_now, rows = None, 0.0, []
        events = []
        for seg in segments:
            meas = {"speed_rpm": abs(float(seg["speed_rpm"])), "torque_abs_Nm": abs(float(seg["torque_Nm"])),
                    "Vdc_V": float(seg["Vdc_V"]), "sensor_temp_C": seg.get("sensor_temp_C")}
            state = pol.step(state, meas, t_now)
            if state.get("changed"):
                events.append({"t_s": t_now, "to_fsw_Hz": pol.fsw(state), "reason": state["reason"]})
            fsw = pol.fsw(state)
            ev = _segment_eval(base_drive, cand, seg, fsw, coolant_C, limits_dc, timing, loop, L_hf_H, harmonic, bank,
                               source, modulation)
            ev["duration_s"] = float(seg["duration_s"])
            rows.append(ev)
            t_now += float(seg["duration_s"])
        out.append(_aggregate(pol, rows, events, lim))
    base = out[0]
    for o in out[1:]:
        o["versus_baseline"] = _versus(base, o)
    adm = [o for o in out if o["admissible"]]
    pareto = _pareto(adm)
    best = min(adm, key=lambda o: o["E_inv_J"]) if adm and all(o["E_inv_J"] is not None for o in adm) else None
    return {"policies": out, "pareto": [o["policy"]["name"] for o in pareto],
            "best_inverter_energy_among_evaluated": None if best is None else best["policy"]["name"],
            "limits": lim.__dict__, "coolant_C": coolant_C, "modulation": modulation,
            "meaning": "evaluated candidates only (no global optimum claimed); a mandatory violation is never traded "
                       "for efficiency; an inverter-loss gain is not a motor+inverter gain without a harmonic-loss "
                       "bound"}


def _aggregate(pol, rows, events, lim: PwmLimits) -> dict:
    viol, unknown = [], []
    delivered = all(r["status"] == "FEASIBLE" for r in rows)
    if not delivered:
        viol.append("requirement not delivered in every segment")

    def tot(key):
        vals = [r.get(key) for r in rows]
        if any(v is None for v in vals):
            return None
        return float(sum(v * r["duration_s"] for v, r in zip(vals, rows)))
    E_inv, E_cu, E_h = tot("P_inv_W"), tot("P_cu_fund_W"), tot("P_cu_harm_W")
    E_fe = tot("P_iron_harm_bound_W")
    tj = [r["Tj_C"] for r in rows if r.get("Tj_C") is not None]
    ipk = [r["i_peak_incl_ripple_A"] for r in rows if r.get("i_peak_incl_ripple_A") is not None]
    icap = [r["I_cap_rms_A"] for r in rows if r.get("I_cap_rms_A") is not None]
    pm = [r["timing"].get("phase_margin_deg") for r in rows if r["timing"].get("phase_margin_deg") is not None]
    np_ = [r["pulse_ratio"] for r in rows if r.get("pulse_ratio") is not None]
    if any(not r["timing"]["deadline_ok"] for r in rows):
        viol.append("control deadline missed at the scheduled frequency")
    if any(r.get("min_pulse_ok") is False for r in rows):
        viol.append("commanded pulse narrower than the declared minimum")
    for name, vals, limit, hi in (("Tj", tj, lim.Tj_max_C, True), ("peak current incl. ripple", ipk,
                                                                    lim.i_peak_incl_ripple_max_A, True),
                                  ("capacitor RMS current", icap, lim.cap_rms_max_A, True),
                                  ("current-loop phase margin", pm, lim.phase_margin_min_deg, False),
                                  ("pulse ratio", np_, lim.pulse_ratio_min, False)):
        if limit is None:
            unknown.append(f"{name}: no limit declared")
            continue
        if not vals:
            unknown.append(f"{name}: not evaluated")
            continue
        worst = max(vals) if hi else min(vals)
        if (worst > limit) if hi else (worst < limit):
            viol.append(f"{name} {worst:.4g} vs limit {limit:g}")
    admissible = not viol
    return {"policy": pol.describe(), "segments": rows, "transitions": events, "violations": viol,
            "unverified": unknown, "admissible": admissible, "delivered": delivered,
            "E_inv_J": E_inv, "E_cu_fund_J": E_cu, "E_cu_harm_J": E_h, "E_iron_harm_bound_J": E_fe,
            "Tj_max_C": max(tj) if tj else None, "i_peak_incl_ripple_max_A": max(ipk) if ipk else None,
            "I_cap_rms_max_A": max(icap) if icap else None, "phase_margin_min_deg": min(pm) if pm else None,
            "pulse_ratio_min": min(np_) if np_ else None}


def _versus(base: dict, o: dict) -> dict:
    """Inverter-loss change (established when both deliver) and the motor+inverter change as an interval."""
    if not (base["delivered"] and o["delivered"]):
        return {"status": "NOT_COMPARABLE", "reason": "both policies must deliver the same trajectory"}
    d_inv = o["E_inv_J"] - base["E_inv_J"]
    res = {"delta_E_inv_J": d_inv, "relative_inv": d_inv / base["E_inv_J"] if base["E_inv_J"] else None}
    lo_b = base["E_inv_J"] + base["E_cu_fund_J"] + (base["E_cu_harm_J"] or 0.0)
    lo_o = o["E_inv_J"] + o["E_cu_fund_J"] + (o["E_cu_harm_J"] or 0.0)
    if base["E_cu_harm_J"] is None or o["E_cu_harm_J"] is None:
        res["total"] = {"status": "UNKNOWN", "reason": "motor PWM harmonic copper loss not evaluated (no R_ac(f) data): "
                                                       "the inverter-loss change is not a motor+inverter change"}
        return res
    if base["E_iron_harm_bound_J"] is None or o["E_iron_harm_bound_J"] is None:
        res["total"] = {"status": "UNKNOWN", "lower_delta_J": lo_o - lo_b,
                        "reason": "iron / magnet harmonic loss bound not declared for both frequencies"}
        return res
    hi_b, hi_o = lo_b + base["E_iron_harm_bound_J"], lo_o + o["E_iron_harm_bound_J"]
    if hi_o < lo_b:
        st = "IMPROVED"
    elif lo_o > hi_b:
        st = "WORSE"
    else:
        st = "UNDECIDED"
    res["total"] = {"status": st, "interval_candidate_J": [lo_o, hi_o], "interval_baseline_J": [lo_b, hi_b],
                    "reason": "motor+inverter energy as [known, known + declared harmonic iron bound] intervals"}
    return res


def _pareto(rows: list) -> list:
    """Non-dominated admissible policies on (inverter energy, peak Tj, peak current incl. ripple, capacitor current,
    -phase margin); objectives that are None for any policy are left out."""
    keys = [("E_inv_J", 1), ("Tj_max_C", 1), ("i_peak_incl_ripple_max_A", 1), ("I_cap_rms_max_A", 1),
            ("phase_margin_min_deg", -1)]
    keys = [(k, s) for k, s in keys if all(r.get(k) is not None for r in rows)]
    out = []
    for a in rows:
        dom = False
        for b in rows:
            if a is b:
                continue
            le = all(s * b[k] <= s * a[k] for k, s in keys)
            lt = any(s * b[k] < s * a[k] for k, s in keys)
            if le and lt:
                dom = True
                break
        if not dom:
            out.append(a)
    return out
