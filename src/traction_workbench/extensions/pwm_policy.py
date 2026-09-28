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
from dataclasses import dataclass, field, replace

import numpy as np

from ..errors import InputValidationError
from ..modulation import duties as _duties
from ..validation import finite as _finite
from .emi import SwitchingSource, edge_lines, pwm_edges

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


def chatter_risk(schedule: FswSchedule, noise_pp: dict | None) -> dict:
    """Threshold chatter from measurement noise: a rule entered at a boundary is left only after the measurement moves
    by the hysteresis, so a peak-to-peak noise at least as large as the hysteresis can toggle the schedule on noise
    alone (the dwell only limits the toggle RATE).  Noise not declared for a measurement a rule uses: not evaluated."""
    used = sorted({v for r in schedule.rules for v in VARS if getattr(r, v) is not None})
    hyst = dict(schedule.hysteresis)
    rows, viol, unknown = [], [], []
    for v in used:
        n = None if noise_pp is None else noise_pp.get(v)
        h = float(hyst.get(v, 0.0))
        if n is None:
            unknown.append(f"{v}: measurement noise not declared (threshold chatter not evaluated)")
            rows.append({"measurement": v, "hysteresis": h, "noise_pp": None, "risk": None})
            continue
        risk = float(n) >= h
        rows.append({"measurement": v, "hysteresis": h, "noise_pp": float(n), "risk": risk})
        if risk:
            viol.append(f"threshold chatter: {v} noise {float(n):g} (peak-peak) >= hysteresis {h:g} - the schedule can "
                        f"change on noise alone (dwell {schedule.min_dwell_s:g} s only limits the rate)")
    return {"rows": rows, "violations": viol, "unknown": unknown}


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


# --------------------------------------------------------------------------------------------- current sampling

SENSING_KINDS = ("inline_phase", "leg_shunt", "dc_link_shunt")


@dataclass(frozen=True)
class SensingConfig:
    """Declared phase-current acquisition (addendum 4.4 items 3-4).  A sample is VALID only when its declared window
    is free of switching disturbance; an invalid sample is never replaced by the ideal true phase current.

    inline_phase : phase sensors, sampled at the carrier valley (centre of the 000 vector) and / or peak (111);
    leg_shunt    : low-side shunt per leg: a leg is measurable only while its lower switch conducts (valley only);
    dc_link_shunt: single shunt: two active vectors per half carrier period, each must be long enough."""

    kind: str
    settle_s: float                        # disturbance settling after a switching edge (ringing, amplifier)
    aperture_s: float                      # sample-and-hold aperture
    sample_points: str = "valley"          # "valley" | "peak" | "valley_and_peak" (inline); leg shunts: valley
    edge_noise: str = "any_leg"            # "any_leg" (conservative) | "own_leg" (needs a layout / filter basis)
    reconstruct_from_two: bool = True      # three sensors installed: any two valid legs + Kirchhoff i_a+i_b+i_c = 0
                                           # (two installed sensors: declare False - all legs must be valid)
    channel_skew_s: float = 0.0            # time between the phase channels' samples (sequential conversion)
    invalid_policy: str = "none"           # "none" | "hold" (last valid sample) | "predict" (declared predictor)
    predict_error_fraction: float | None = None   # "predict": residual error / hold error (from its validation)
    max_sample_age_s: float | None = None  # declared maximum acceptable age of a held / predicted sample
    current_error_max_A: float | None = None      # declared allowed current-reconstruction error
    basis: str = ""

    def __post_init__(self):
        if self.kind not in SENSING_KINDS:
            raise InputValidationError(f"sensing kind must be one of {SENSING_KINDS}", field="kind")
        for n in ("settle_s", "aperture_s", "channel_skew_s"):
            if _finite(n, getattr(self, n)) < 0:
                raise InputValidationError(f"{n} must be >= 0", field=n)
        if self.sample_points not in ("valley", "peak", "valley_and_peak"):
            raise InputValidationError("sample points must be valley, peak or valley_and_peak", field="sample_points")
        if self.kind == "leg_shunt" and self.sample_points != "valley":
            raise InputValidationError("low-side leg shunts measure only while the lower switch conducts: sample at "
                                       "the valley", field="sample_points")
        if self.edge_noise not in ("any_leg", "own_leg"):
            raise InputValidationError("edge noise must be any_leg or own_leg", field="edge_noise")
        if self.invalid_policy not in ("none", "hold", "predict"):
            raise InputValidationError("invalid-sample policy must be none, hold or predict", field="invalid_policy")
        if self.invalid_policy == "predict" and not (self.predict_error_fraction is not None
                                                     and 0.0 <= self.predict_error_fraction <= 1.0):
            raise InputValidationError("a predictor needs its validated residual error fraction in [0, 1]",
                                       field="predict_error_fraction")
        for n in ("max_sample_age_s", "current_error_max_A"):
            v = getattr(self, n)
            if v is not None and _finite(n, v) <= 0:
                raise InputValidationError(f"{n} must be > 0", field=n)
        if not self.basis.strip():
            raise InputValidationError("the acquisition timing needs its basis (target ADC / trigger configuration)",
                                       field="basis")


def _periodic_age(valid: np.ndarray, t: np.ndarray, period: float) -> tuple[int, float]:
    """Longest run of invalid samples and the largest age of the last valid sample over a periodic sequence."""
    if valid.all():
        return 0, 0.0
    if not valid.any():
        return int(valid.size), math.inf
    k0 = int(np.argmax(valid))
    run = best = 0
    age = 0.0
    last = t[k0]
    for i in range(1, valid.size + 1):
        j = (k0 + i) % valid.size
        tj = t[j] + (period if j <= k0 else 0.0)
        if valid[j]:
            last = tj
            run = 0
        else:
            run += 1
            best = max(best, run)
            age = max(age, tj - last)
    return best, age


def sampling_validity(m: float, modulation: str, fsw_Hz: float, fe_Hz: float, sens: SensingConfig,
                      deadtime_s: float = 0.0, I_pk_A: float = 0.0, V1_pk_V: float = 0.0,
                      L_hf_H: float | None = None) -> dict:
    """Validity of every current sample over one fundamental period (synchronous regular-sampled carrier, pulses
    centred at the carrier peak as in the edge generator).  Windows per carrier period j (duties d, period Ts):

    valley (lower switches conduct): previous edge (1 - d_prev) Ts/2 (+ dead time on the low side) before, next edge
    (1 - d_next) Ts/2 after; peak (upper switches conduct): d Ts/2 on both sides; single DC-link shunt: the two
    active vectors (d_max - d_mid) Ts/2 and (d_mid - d_min) Ts/2 per half period.  A window must hold the declared
    settling after the edge plus the aperture.

    Invalid samples are held / predicted (declared policy) with an error bound from the fundamental slope
    omega_e I_pk times the age; at (near) standstill an invalid angle can persist, so the age is unbounded.
    Channel skew between the phase samples adds skew * (V1_pk / L_hf + omega_e I_pk) (zero-vector slope of the
    phase current plus the fundamental slope)."""
    fe_true = abs(float(fe_Hz))
    quasi_static = fe_true < fsw_Hz / 400.0
    fe = max(fe_true, fsw_Hz / 400.0)
    ratio = max(1, int(round(fsw_Hz / fe)))
    period = 1.0 / fe
    Ts = period / ratio
    tc = (np.arange(ratio) + 0.5) * Ts
    d = np.clip(_duties(TWO_PI * fe * tc, m, 0.0, modulation), 0.0, 1.0)
    before = sens.settle_s + 0.5 * sens.aperture_s
    after = 0.5 * sens.aperture_s
    t_s, ok, window = [], [], []
    for j in range(ratio):
        dp, dn = d[:, j - 1], d[:, j]
        if sens.kind == "dc_link_shunt":
            s = np.sort(dn)[::-1]
            w = min(s[0] - s[1], s[1] - s[2]) * Ts / 2.0
            t_s.append(tc[j] - 0.5 * Ts)
            ok.append(w >= sens.settle_s + sens.aperture_s)
            window.append(w)
            continue
        need = 2 if sens.reconstruct_from_two else 3
        if sens.sample_points in ("valley", "valley_and_peak"):
            eb = np.where((dp > 0.0) & (dp < 1.0), (1.0 - dp) * Ts / 2.0, np.inf)    # to each leg's last edge
            ea = np.where((dn > 0.0) & (dn < 1.0), (1.0 - dn) * Ts / 2.0, np.inf)    # ... and its next edge
            if sens.kind == "leg_shunt":                                              # own low-side conduction
                lb = (1.0 - dp) * Ts / 2.0 - deadtime_s
                la = (1.0 - dn) * Ts / 2.0
                own = (lb >= before) & (la >= after) & (dp < 1.0) & (dn < 1.0)
                own_w = np.minimum(lb, la)
            else:
                own = (eb >= before) & (ea >= after)
                own_w = np.minimum(eb, ea)
            w = float(np.sort(own_w)[::-1][need - 1])
            if sens.edge_noise == "any_leg":                  # every leg's edge disturbs every channel
                own = own & bool(eb.min() >= before and ea.min() >= after)
                w = min(w, float(eb.min()), float(ea.min()))
            t_s.append(j * Ts)
            ok.append(bool(own.sum() >= need))
            window.append(w)
        if sens.sample_points in ("peak", "valley_and_peak"):
            ep = np.where((dn > 0.0) & (dn < 1.0), dn * Ts / 2.0, np.inf)              # upper conduction
            if sens.edge_noise == "any_leg":
                good, w = bool(ep.min() >= before), float(ep.min())
            else:
                good, w = bool((ep >= before).sum() >= need), float(np.sort(ep)[::-1][need - 1])
            t_s.append(tc[j])
            ok.append(good)
            window.append(w)
    t_s, ok, window = np.array(t_s), np.array(ok, bool), np.array(window)
    order = np.argsort(t_s, kind="stable")
    t_s, ok, window = t_s[order], ok[order], window[order]
    run, age = _periodic_age(ok, t_s, period)
    if quasi_static and not ok.all():
        age = math.inf                               # the invalid angle can persist at (near) standstill
    w_e = TWO_PI * fe_true
    hold_err = w_e * I_pk_A * age if math.isfinite(age) else math.inf
    if sens.invalid_policy == "predict":
        err = hold_err * float(sens.predict_error_fraction)
    else:
        err = hold_err
    slope = (V1_pk_V / L_hf_H if (L_hf_H and L_hf_H > 0) else 0.0) + w_e * I_pk_A
    skew_err = sens.channel_skew_s * slope if sens.kind != "dc_link_shunt" else 0.0
    viol, unknown = [], []
    n_bad = int((~ok).sum())
    if n_bad:
        what = (f"{n_bad} of {ok.size} current samples fall in a disturbed / too-short window "
                f"(tightest window {1e6 * float(np.min(window)):.3g} us vs settle {1e6 * sens.settle_s:.3g} us + "
                f"aperture {1e6 * sens.aperture_s:.3g} us)")
        if sens.invalid_policy == "none":
            viol.append(what + "; no fallback declared: the controller would use disturbed samples")
        elif not math.isfinite(age):
            viol.append(what + ("; at (near) standstill the invalid angle can persist: the held value may never "
                                "refresh" if quasi_static else "; no valid sample in the fundamental period"))
        else:
            if sens.max_sample_age_s is None:
                unknown.append("held / predicted sample age: no limit declared")
            elif age > sens.max_sample_age_s:
                viol.append(f"held sample age {1e6 * age:.4g} us exceeds the declared {1e6 * sens.max_sample_age_s:.4g} us")
            if sens.current_error_max_A is None:
                unknown.append("reconstruction error of held samples: no limit declared")
            elif err > sens.current_error_max_A:
                viol.append(f"held-sample error bound {err:.4g} A exceeds the declared {sens.current_error_max_A:g} A")
    if skew_err > 0:
        if sens.current_error_max_A is None:
            unknown.append("channel-skew error: no reconstruction error limit declared")
        elif skew_err > sens.current_error_max_A:
            viol.append(f"channel skew {1e9 * sens.channel_skew_s:.4g} ns gives up to {skew_err:.4g} A reconstruction "
                        f"error > {sens.current_error_max_A:g} A")
    return {"kind": sens.kind, "valid_fraction": float(ok.mean()), "n_samples": int(ok.size), "n_invalid": n_bad,
            "longest_invalid_run": run, "max_age_s": age, "hold_error_bound_A": hold_err, "error_bound_A": err,
            "skew_error_bound_A": skew_err, "min_window_s": float(np.min(window)), "quasi_static": quasi_static,
            "t_s": t_s, "valid": ok, "window_s": window, "violations": viol, "unknown": unknown,
            "status": "VIOLATION" if viol else ("UNKNOWN" if unknown else "OK"),
            "note": "valid windows from the declared settle / aperture / dead time; invalid samples are held or "
                    "predicted by the declared policy, never replaced by the true current"}


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
    integrator_storage: str = "output"        # "output": state in volts (bumpless when Ki*Ts changes)
                                              # "error_sum": sum of errors, output = Ki_disc * sum (jumps with Ki_disc)
    on_transition: str = "keep"               # "keep" | "reset" (integrator cleared at a carrier-frequency change)
    anti_windup: bool = True                  # conditional integration while the voltage is saturated

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
        if self.integrator_storage not in ("output", "error_sum"):
            raise InputValidationError("integrator storage must be output or error_sum", field="integrator_storage")
        if self.on_transition not in ("keep", "reset"):
            raise InputValidationError("integrator handling at a transition must be keep or reset",
                                       field="on_transition")

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


def axis_loops(loop) -> dict:
    """{'d': CurrentLoop, 'q': CurrentLoop}: a single declared loop applies the SAME gains to both axes."""
    if loop is None:
        return {}
    return dict(loop) if isinstance(loop, dict) else {"d": loop, "q": loop}


def differential_inductances(drive, scenario, id_A: float, iq_A: float, dI_A: float = 1.0) -> dict | None:
    """L_d,diff = dpsi_d/di_d and L_q,diff = dpsi_q/di_q of the MACHINE model at the operating point (central
    differences; for the constant model the declared L_d, L_q).  The current-loop plant, not its design value."""
    from ..physics import forward_evaluation
    pts = [forward_evaluation(drive, scenario, id_A + a, iq_A + b).point
           for a, b in ((dI_A, 0.0), (-dI_A, 0.0), (0.0, dI_A), (0.0, -dI_A))]
    if any(x is None for x in pts):
        return None
    Ld = (pts[0].psi_d_Wb - pts[1].psi_d_Wb) / (2 * dI_A)
    Lq = (pts[2].psi_q_Wb - pts[3].psi_q_Wb) / (2 * dI_A)
    if Ld <= 0 or Lq <= 0:
        return None
    return {"d": Ld, "q": Lq}


def sampled_loop(loop: CurrentLoop, fsw_Hz: float, tc: "TimingConfig", plant_L_H: float | None = None) -> dict:
    """The IMPLEMENTED discrete current loop of one axis (review R2 CT-06).

    PI as executed: v_k = Kp e_k + x_k, x_{k+1} = x_k + Ki_d e_k at the update period T (Ki_d = Ki T, or the fixed
    Ki T_ref); every command applied after the declared delay D = filter + (1 + modulator fraction) T - the SAME
    chain as the transition replay; the RL plant integrated exactly between applications (the fractional part of D
    splits the period).  Stability is the spectral radius of the augmented state matrix (< 1); the discrete phase
    margin is read on the unit circle.  A continuous PI + RL + pure-delay margin is only a screen."""
    upd = tc.updates_per_period
    T = 1.0 / (fsw_Hz * upd)
    if tc.sample_to_latch_s > T:
        return {"evaluated": False, "reason": "control deadline missed: no sampled loop"}
    L, R, Kp = (plant_L_H or loop.L_H), loop.R_ohm, loop.Kp
    Tref = None if loop.reference_fsw_Hz is None else 1.0 / (loop.reference_fsw_Hz * upd)
    Ki_d = loop.Ki * (T if loop.gain_mapping == "continuous" else Tref)
    D = tc.filter_delay_s + (1.0 + tc.modulator_delay_fraction) * T
    d = max(1, int(math.floor(D / T + 1e-12)))
    delta = min(max(D - d * T, 0.0), T)
    a = math.exp(-R * T / L)

    def phi(h):
        return (1.0 - math.exp(-R * h / L)) / R if R > 0 else h / L
    b2 = phi(T - delta)                                   # v_{k-d} over the last (T - delta)
    b1 = phi(delta) * math.exp(-R * (T - delta) / L)      # v_{k-d-1} over the first delta, then decaying
    n = 3 + d                                             # [i_k, x_k, v_{k-1} .. v_{k-d-1}]
    M = np.zeros((n, n))
    M[0, 0] = a
    M[0, d + 1] += b2
    M[0, d + 2] += b1
    M[1, 0], M[1, 1] = -Ki_d, 1.0
    M[2, 0], M[2, 1] = -Kp, 1.0
    for j in range(1, d + 1):
        M[2 + j, 1 + j] = 1.0
    eig = np.linalg.eigvals(M)
    rho = float(np.max(np.abs(eig)))

    def Lz(w):
        z = np.exp(1j * w * T)
        return (Kp + Ki_d / (z - 1.0)) * (b2 + b1 / z) * z ** (-d) / (z - a)

    def phase(w):
        z = np.exp(1j * w * T)
        return (np.angle(Kp + Ki_d / (z - 1.0)) + np.angle(b2 + b1 / z) - d * w * T - np.angle(z - a))
    w = np.logspace(math.log10(1e-6 * math.pi / T), math.log10(math.pi / T * (1 - 1e-9)), 4000)
    mag = np.abs(Lz(w))
    idx = np.nonzero((mag[:-1] >= 1.0) & (mag[1:] < 1.0))[0]
    pm = wc = None
    if idx.size:
        lo_, hi_ = w[idx[0]], w[idx[0] + 1]
        for _ in range(80):
            mid = math.sqrt(lo_ * hi_)
            if abs(Lz(mid)) >= 1.0:
                lo_ = mid
            else:
                hi_ = mid
        wc = math.sqrt(lo_ * hi_)
        pm = 180.0 + math.degrees(phase(wc))
    return {"evaluated": True, "spectral_radius": rho, "stable": rho < 1.0 - 1e-12, "phase_margin_deg": pm,
            "crossover_Hz": None if wc is None else wc / TWO_PI, "update_period_s": T, "delay_s": D,
            "delay_samples": d, "delay_fraction": delta / T, "matrix": M.tolist(),
            "basis": "discrete PI as executed, exact ZOH RL plant with the declared fractional delay"}


def axis_margins(loop, tau_s: float, fsw_Hz: float, plant_L: dict | None = None, updates_per_period: int = 1,
                 timing: "TimingConfig | None" = None) -> dict:
    """Per axis with the DECLARED gains on the PLANT inductance (the machine's differential inductance at the
    operating point when given, else the design value).  With the timing chain the sampled loop decides: its
    stability (spectral radius) and discrete phase margin are the claim; the continuous margin is a screen."""
    out = {}
    for ax, lp in axis_loops(loop).items():
        Lp = (plant_L or {}).get(ax, lp.L_H)
        mg = replace(lp, L_H=Lp).margins(tau_s, lp.effective_Ki(fsw_Hz, updates_per_period))
        row = {**mg, "plant_L_H": Lp, "design_L_H": lp.L_H, "continuous_screen_phase_margin_deg":
               mg.get("phase_margin_deg")}
        if timing is not None:
            sl = sampled_loop(lp, fsw_Hz, timing, Lp)
            if sl["evaluated"]:
                row.update(phase_margin_deg=sl["phase_margin_deg"], crossover_Hz=sl["crossover_Hz"],
                           sampled_stable=sl["stable"], spectral_radius=sl["spectral_radius"],
                           margin_basis=sl["basis"])
            else:
                row.update(phase_margin_deg=None, sampled_stable=None, margin_basis=sl["reason"])
        out[ax] = row
    pms = [v["phase_margin_deg"] for v in out.values() if v.get("phase_margin_deg") is not None]
    st = [v.get("sampled_stable") for v in out.values()]
    return {"axes": out, "phase_margin_deg": min(pms) if pms else None,
            "sampled_stable": None if timing is None or any(x is None for x in st) else all(st),
            "binding_axis": min(out, key=lambda a: out[a]["phase_margin_deg"] if out[a].get("phase_margin_deg")
                                is not None else math.inf) if out else None}


def transition_transient(loop: CurrentLoop, tc: TimingConfig, fsw_from_Hz: float, fsw_to_Hz: float, i_ref_A: float,
                         e_V: float, V_max_V: float, band_A: float | None = None, t_after_s: float | None = None,
                         n_before: int = 40, plant_L_H: float | None = None) -> dict:
    """One current-loop axis across a carrier-frequency change at a CONSTANT operating point (addendum 4.4 items 5-6).

    Sampled PI with the declared gain mapping, integrator storage and transition handling, voltage saturation with
    optional conditional-integration anti-windup; the declared delay chain per period (filter + one update +
    modulator fraction); plant L di/dt = v - R i - e integrated exactly between events.  The run starts at the exact
    steady state, so any excursion is caused by the transition itself: a bumpless mapping gives none; an error-sum
    integrator under Ki*Ts remapping jumps by (Ts_to / Ts_from - 1) v_ss; a reset drops v_ss; fixed discrete gains
    change the effective Ki (the loop dynamics) without a jump at a constant point.  One decoupled axis: a screening
    of the mechanism, not the full dq transient."""
    upd = tc.updates_per_period
    T0, T1 = 1.0 / (fsw_from_Hz * upd), 1.0 / (fsw_to_Hz * upd)
    for T, f in ((T0, fsw_from_Hz), (T1, fsw_to_Hz)):
        if tc.sample_to_latch_s > T:
            return {"evaluated": False, "reason": f"control deadline missed at {f / 1e3:g} kHz: the transition is a "
                                                  "timing violation, not evaluated as a transient"}
    if loop.Ki <= 0:
        return {"evaluated": False, "reason": "no integral action: no integrator state to map"}
    Tref = None if loop.reference_fsw_Hz is None else 1.0 / (loop.reference_fsw_Hz * upd)

    def ki_disc(T):
        return loop.Ki * (T if loop.gain_mapping == "continuous" else Tref)
    L, R, e = (plant_L_H or loop.L_H), loop.R_ohm, float(e_V)            # plant; the gains stay the declared ones
    v_ss = R * i_ref_A + e
    if abs(v_ss) > V_max_V:
        return {"evaluated": False, "reason": f"steady voltage {abs(v_ss):.4g} V above the limit {V_max_V:.4g} V"}
    band = band_A if band_A is not None else max(0.01 * abs(i_ref_A), 0.5)
    t_after = t_after_s if t_after_s is not None else min(0.25, max(40.0 * L / loop.Kp, 5.0 * loop.L_H / R if R > 0
                                                                     else 0.0))
    t_sw = n_before * T0
    t_end = t_sw + t_after

    def advance(i, h, v):
        if h <= 0.0:
            return i
        if R > 0.0:
            i_inf = (v - e) / R
            return i_inf + (i - i_inf) * math.exp(-R * h / L)
        return i + (v - e) * h / L
    i, t, v_app = i_ref_A, 0.0, v_ss
    xI = v_ss                                        # integrator in volts ("output" storage)
    S = v_ss / ki_disc(T0)                           # sum of errors ("error_sum" storage)
    pending, last_issue = [], -1
    tr_t, tr_i, tr_v = [0.0], [i], [v_app]
    tk, k, prev_v, jump, sat_after = 0.0, 0, v_ss, None, 0
    while tk <= t_end + 1e-15:
        while pending and pending[0][0] <= tk:
            ta, vnew, idx = pending.pop(0)
            i = advance(i, ta - t, v_app)
            t = ta
            if idx > last_issue:                     # a newer command already applied wins (shadow registers)
                v_app, last_issue = vnew, idx
            tr_t.append(t)
            tr_i.append(i)
            tr_v.append(v_app)
        i = advance(i, tk - t, v_app)
        t = tk
        tr_t.append(t)
        tr_i.append(i)
        tr_v.append(v_app)
        new = tk >= t_sw - 1e-15
        T = T1 if new else T0
        first = new and jump is None
        if first and loop.on_transition == "reset":
            xI, S = 0.0, 0.0
        err = i_ref_A - i
        v = loop.Kp * err + (xI if loop.integrator_storage == "output" else ki_disc(T) * S)
        vc = max(-V_max_V, min(V_max_V, v))
        saturated = vc != v
        if new and saturated:
            sat_after += 1
        if not (loop.anti_windup and saturated and (err > 0) == (v > 0)):
            if loop.integrator_storage == "output":
                xI += ki_disc(T) * err
            else:
                S += err
        if first:
            jump = vc - prev_v
        prev_v = vc
        pending.append((tk + tc.filter_delay_s + (1.0 + tc.modulator_delay_fraction) * T, vc, k))
        pending.sort()
        k += 1
        tk += T
    tt, ii, vv = np.array(tr_t), np.array(tr_i), np.array(tr_v)
    post = tt >= t_sw
    dev = np.abs(ii - i_ref_A)
    out_band = np.nonzero(post & (dev > band))[0]
    settled = not (out_band.size and tt[out_band[-1]] > t_end - 0.02 * t_after)
    step = max(1, tt.size // 4000)
    return {"evaluated": True, "fsw_from_Hz": fsw_from_Hz, "fsw_to_Hz": fsw_to_Hz, "i_ref_A": i_ref_A, "e_V": e,
            "v_ss_V": v_ss, "V_max_V": V_max_V, "output_jump_V": float(jump), "bumpless": abs(jump) <= 1e-9 * max(1.0, abs(v_ss)),
            "excursion_A": float(dev[post].max()), "band_A": band,
            "settle_s": (float(tt[out_band[-1]] - t_sw) if out_band.size else 0.0) if settled else None,
            "horizon_s": t_after, "saturated_samples": sat_after,
            "Ki_eff_ratio": (ki_disc(T1) / T1) / (ki_disc(T0) / T0),
            "mapping": {"gain_mapping": loop.gain_mapping, "integrator_storage": loop.integrator_storage,
                        "on_transition": loop.on_transition, "anti_windup": loop.anti_windup},
            "t_s": tt[::step] - t_sw, "i_err_A": (ii - i_ref_A)[::step], "v_V": vv[::step],
            "note": "one decoupled axis at a constant operating point, exact RL between events, declared delay chain; "
                    "the transition's own transient (not the full dq response)"}


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
        Vl = edge_lines(t0, tau, sgn * wgt * Vdc_V, f, T)
    else:
        kk = t0 = sgn = wgt = np.array([])
        Vl = np.zeros(f.size, complex)
    # exact integral of v_an: piecewise constant between the (ideal) edges, from each pole's state before t = 0
    level = np.asarray(e["initial_state"], dtype=float)
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


def check_gate_events(edges_hi: list, edges_lo: list, min_pulse_s: float, deadtime_s: float, t_end_s: float,
                      t_start_s: float = 0.0) -> dict:
    """Legality of complementary gate signals of one leg (imported or generated): edges are (t, +1/-1).

    Detects missing / duplicate edges (two rises in a row), pulses shorter than the minimum and dead-time
    violations (upper on while lower on, or less than the dead time between them).  A pulse the observation window
    cuts (still on at t_end, or on from t_start) is not a complete pulse: its width is not judged against the
    minimum (it is counted as open), its overlap with the other switch still is."""
    problems = []
    open_pulses = 0

    def pulses(edges, name):
        nonlocal open_pulses
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
                    out.append((t_on, t, t_on <= t_start_s + 1e-15))
                level = 0
        if level == 1:
            out.append((t_on, t_end_s, True))
        open_pulses += sum(1 for p in out if p[2])
        return out
    hi, lo = pulses(edges_hi, "upper"), pulses(edges_lo, "lower")
    for name, ps in (("upper", hi), ("lower", lo)):
        for a, b, cut in ps:
            if not cut and b - a < min_pulse_s - 1e-15:
                problems.append(f"{name}: pulse {1e9 * (b - a):.1f} ns shorter than the minimum {1e9 * min_pulse_s:.1f} ns "
                                f"at {a:.9g} s")
    for a, b, _ in hi:
        for c, d, _ in lo:
            gap = max(c - b, a - d)                 # < 0: overlap (shoot-through); < dead time: violation
            if gap < deadtime_s - 1e-15:
                problems.append(f"dead time {1e9 * gap:.1f} ns < {1e9 * deadtime_s:.1f} ns between upper "
                                f"[{a:.9g}, {b:.9g}] and lower [{c:.9g}, {d:.9g}]")
    return {"ok": not problems, "problems": problems, "pulses_upper": len(hi), "pulses_lower": len(lo),
            "open_pulses": open_pulses}


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
    """Declared motor data for PWM harmonics: R_ac/R_dc vs frequency (copper) and an optional declared UPPER BOUND of
    the PWM-induced high-frequency magnetic loss per carrier frequency (stator / rotor iron + PM eddy current within
    the declared basis - never split into Fe and PM without separate data, never interpolated between anchor
    frequencies without a basis).  ``iron_bound_W`` is the backward-compatible alias of ``magnetic_hf_loss_bound_W``.
    Without the bound the motor+inverter comparison stays open (UNKNOWN / an open interval)."""

    f_Hz: tuple
    rac_over_rdc: tuple
    magnetic_hf_loss_bound_W: tuple = ()      # ((fsw_Hz, W upper bound at the operating points of interest), ...)
    basis: str = ""
    iron_bound_W: tuple = ()                  # alias (older files): the same declared bound

    def __post_init__(self):
        f = np.asarray(self.f_Hz, float)
        r = np.asarray(self.rac_over_rdc, float)
        if f.ndim != 1 or f.size < 2 or np.any(np.diff(f) <= 0) or f[0] < 0 or r.shape != f.shape or np.any(r < 1.0):
            raise InputValidationError("R_ac/R_dc table: increasing frequencies, ratios >= 1", field="rac_over_rdc")
        if not self.basis.strip():
            raise InputValidationError("harmonic loss data need their basis (FEA / measurement)", field="basis")
        a, b = tuple(tuple(x) for x in self.magnetic_hf_loss_bound_W), tuple(tuple(x) for x in self.iron_bound_W)
        if a and b and a != b:
            raise InputValidationError("magnetic_hf_loss_bound_W and its alias iron_bound_W disagree - declare one",
                                       field="magnetic_hf_loss_bound_W")
        bound = a or b
        if any(not (math.isfinite(float(w)) and float(w) >= 0.0 and float(fq) > 0.0) for fq, w in bound):
            raise InputValidationError("magnetic HF loss bound: (fsw_Hz > 0, W >= 0) pairs", field="magnetic_hf_loss_bound_W")
        object.__setattr__(self, "magnetic_hf_loss_bound_W", tuple((float(fq), float(w)) for fq, w in bound))
        object.__setattr__(self, "iron_bound_W", self.magnetic_hf_loss_bound_W)

    def rac(self, f):
        f = np.asarray(f, float)
        fa = np.asarray(self.f_Hz, float)
        out = np.interp(f, fa, np.asarray(self.rac_over_rdc, float))
        return np.where((f >= fa[0]) & (f <= fa[-1]), out, np.nan)

    def magnetic_hf_bound(self, fsw_Hz: float) -> float | None:
        """The declared bound at this carrier frequency (anchor frequencies only: no interpolation without basis)."""
        for f, w in self.magnetic_hf_loss_bound_W:
            if abs(f - fsw_Hz) <= 1e-6 * f:
                return float(w)
        return None

    iron_bound = magnetic_hf_bound            # alias


def harmonic_copper_loss(rip: dict, Rs_ohm: float, data: HarmonicLossData | None) -> dict:
    """Motor PWM harmonic copper loss of the switching lines (the fundamental copper loss is counted separately by the
    drive model - never both from a total RMS).

    * ``lower_bound_W`` = 3 R_s(T) sum I_nu,rms^2 over the EVALUATED lines: R_ac(f) >= R_dc for a passive conductor,
      so it holds without any R_ac data (never 0 W for 'no data'); it is not a bound on lines beyond the evaluated
      bandwidth.
    * ``W`` = 3 sum I_nu,rms^2 R_s k_ac(f_nu) with a declared R_ac/R_dc table covering every significant line; a
      significant line outside the table leaves it None (no extrapolation) while the lower bound stays.
    * ``rac_coverage_I2_fraction``: share of the harmonic current energy (sum I^2) inside the declared table."""
    f = np.asarray(rip["harmonic_f_Hz"], float)
    I = np.asarray(rip["harmonic_I_pk_A"], float)
    i2 = 0.5 * I ** 2                                        # I_rms^2 per line
    lb = 3.0 * Rs_ohm * float(i2.sum())
    out = {"lower_bound_W": lb, "Rs_ohm": Rs_ohm, "bandwidth_Hz": float(f.max()) if f.size else 0.0,
           "lines": int(f.size), "basis": "R_dc lower bound over the evaluated lines (R_ac >= R_dc)"}
    if data is None:
        return {**out, "W": None, "status": "LOWER_BOUND_ONLY", "rac_coverage_I2_fraction": None,
                "reason": "no R_ac(f) data declared: R_dc lower bound only"}
    ratio = data.rac(f)
    inside = ~np.isnan(ratio)
    tot = float(i2.sum())
    cov = float(i2[inside].sum() / tot) if tot > 0 else 1.0
    sig = I > 1e-6 * max(float(I.max()) if I.size else 0.0, 1e-12)
    out.update(rac_coverage_I2_fraction=cov, rac_basis=data.basis)
    if np.any(~inside & sig):
        return {**out, "W": None, "status": "LOWER_BOUND_ONLY",
                "reason": f"significant harmonics outside the declared R_ac(f) table (coverage {cov:.3%} of sum I^2; "
                          f"no extrapolation): R_dc lower bound only"}
    k = np.where(inside, ratio, 1.0)                  # negligible lines (< 1e-6 of the largest) outside: at R_dc
    return {**out, "W": 3.0 * Rs_ohm * float(np.sum(i2 * k)), "status": "ESTABLISHED",
            "reason": "", "basis": f"R_ac(f) table ({data.basis})"}


def motor_hf_interval(cu: dict, mag_bound_W: float | None) -> list:
    """[lower, upper] of the additional motor PWM loss: copper (exact or its R_dc lower bound) plus the declared
    Fe+PM HF bound; the upper end is None (open) without an exact copper value or without the bound."""
    lo = cu["W"] if cu.get("W") is not None else cu["lower_bound_W"]
    hi = None if (cu.get("W") is None or mag_bound_W is None) else cu["W"] + mag_bound_W
    return [lo, hi]


def point_hf_losses(drive, scenario, pt, fsw_Hz: float, L_hf_H: float, modulation: str = "svpwm",
                    harmonic: HarmonicLossData | None = None) -> dict:
    """Motor PWM harmonic losses at ONE operating point with the same models as the policy comparison: the RL
    switching ripple (declared L_hf, synchronous carrier), the copper loss (exact with R_ac(f) coverage, else its
    R_dc lower bound at the scenario's R_s(T)) and the declared Fe+PM HF bound -> the additional motor loss as an
    interval [lower, upper] (upper None = open)."""
    from ..physics import DriveKernel
    vdc = float(scenario.Vdc_V)
    m = float(pt.v_peak_V) / (0.5 * vdc)
    fe_true = abs(float(pt.f_e_Hz))
    fe = max(fe_true, fsw_Hz / 400.0)                     # standstill / very low speed: quasi-static ripple
    rip = phase_ripple(vdc, m, 0.0, fe, fsw_Hz, L_hf_H, modulation, n_per_carrier=128)
    base = {"fsw_requested_Hz": fsw_Hz, "fsw_waveform_used_Hz": rip["fsw_used_Hz"],
            "fsw_error_percent": 100.0 * (rip["fsw_used_Hz"] - fsw_Hz) / fsw_Hz, "L_hf_H": L_hf_H,
            "modulation_index": m, "modulation": modulation, "ripple_rms_A": rip["ripple_rms_A"],
            "ripple_quasi_static": fe_true < fsw_Hz / 400.0,
            "basis": "declared L_hf (RL switching ripple, R / back-EMF harmonics / saturation neglected)"
                     + ("" if harmonic is None else f"; harmonic data: {harmonic.basis}")}
    if rip["overmodulation"]:
        return {**base, "status": "UNKNOWN", "copper": None, "magnetic_hf_bound_W": None, "interval_W": [None, None],
                "reason": "modulation beyond the linear range of the declared PWM family (not evaluated)"}
    cu = harmonic_copper_loss(rip, DriveKernel(drive, scenario).Rs, harmonic)
    mag = None if harmonic is None else harmonic.magnetic_hf_bound(fsw_Hz)
    iv = motor_hf_interval(cu, mag)
    return {**base, "status": "BOUNDED" if iv[1] is not None else "OPEN", "copper": cu, "magnetic_hf_bound_W": mag,
            "interval_W": iv, "reason": "; ".join(x for x in (
                "" if cu["W"] is not None else cu["reason"],
                "" if mag is not None else "Fe+PM HF: no declared bound at this carrier frequency") if x)}


def point_pwm_risk(drive, scenario, pt, fsw_Hz: float, L_hf_H: float, modulation: str = "svpwm",
                   harmonic: HarmonicLossData | None = None, bank=None, source=None, T_ref_C: float | None = None,
                   peak_limit_A: float | None = None, n_lines: int = 5) -> dict:
    """PWM consequences at ONE decision operating point (engineering review 6198099, priority 3), with the same
    modulation and carrier as the losses: the fundamental current limit (a dq-norm limit on the fundamental) kept
    apart from the conservative instantaneous peak bound I_fund,pk + max|di| (compared only with a DECLARED
    device / over-current peak limit), the added RMS, the dominant switching lines of the phase current, the
    DC-link capacitor burden and the motor PWM copper / Fe+PM bound.  No NVH, no exact pulse peak."""
    from ..physics import DriveKernel
    vdc = float(scenario.Vdc_V)
    m = float(pt.v_peak_V) / (0.5 * vdc)
    fe_true = abs(float(pt.f_e_Hz))
    fe = max(fe_true, fsw_Hz / 400.0)
    rip = phase_ripple(vdc, m, 0.0, fe, fsw_Hz, L_hf_H, modulation, n_per_carrier=128)
    k = DriveKernel(drive, scenario)
    base = {"fsw_requested_Hz": fsw_Hz, "fsw_waveform_used_Hz": rip["fsw_used_Hz"],
            "fsw_error_percent": 100.0 * (rip["fsw_used_Hz"] - fsw_Hz) / fsw_Hz, "modulation": modulation,
            "modulation_index": m, "L_hf_H": L_hf_H, "i_fund_peak_A": pt.i_peak_A,
            "current_limit_A": k.Imax, "ripple_quasi_static": fe_true < fsw_Hz / 400.0}
    if rip["overmodulation"]:
        return {**base, "status": "UNKNOWN",
                "reason": "modulation beyond the linear range of the declared PWM family (ripple not evaluated)"}
    I_f = float(pt.i_peak_A)
    rms_f = I_f / math.sqrt(2.0)
    rr = float(rip["ripple_rms_A"])
    bound = I_f + float(rip["ripple_peak_A"])
    f, I = np.asarray(rip["harmonic_f_Hz"]), np.asarray(rip["harmonic_I_pk_A"])
    top = np.argsort(I)[::-1][:n_lines]
    peak = {"bound_A": bound, "meaning": "conservative bound I_fund,pk + max|di| (fundamental and ripple peaks "
                                         "assumed aligned) - not the exact pulse peak"}
    if peak_limit_A is None:
        peak.update(status="UNKNOWN", reason="no device / over-current peak limit declared (the fundamental current "
                                             "limit is not a peak limit)")
    else:
        peak.update(limit_A=float(peak_limit_A), status="WITHIN" if bound <= peak_limit_A else "EXCEEDS_BOUND",
                    reason="" if bound <= peak_limit_A else "the conservative bound exceeds the declared peak limit "
                                                            "(the exact peak may still be lower)")
    out = {**base, "status": "EVALUATED",
           "fundamental": {"i_peak_A": I_f, "limit_A": k.Imax, "margin_A": k.Imax - I_f,
                           "meaning": "the policy's current limit: the fundamental dq norm"},
           "instantaneous_peak": peak,
           "rms": {"fundamental_A": rms_f, "ripple_A": rr, "total_A": math.sqrt(rms_f ** 2 + rr ** 2),
                   "added_percent": 100.0 * (math.sqrt(rms_f ** 2 + rr ** 2) / rms_f - 1.0) if rms_f > 0 else None},
           "lines": [{"f_Hz": float(f[i]), "I_pk_A": float(I[i]), "order": float(f[i] / fe)} for i in top],
           "motor_pwm_loss": point_hf_losses(drive, scenario, pt, fsw_Hz, L_hf_H, modulation, harmonic)}
    if bank is not None and fe_true > 0:
        from .dclink_ripple import ripple_analysis
        phi = math.atan2(pt.vq_V, pt.vd_V) - math.atan2(pt.iq_A, pt.id_A)
        cr = ripple_analysis(I_f, m, phi, fe_true, fsw_Hz, vdc, bank, source, modulation, T_ref_C=T_ref_C)
        out["dc_link"] = {"I_cap_rms_A": cr["I_cap_rms_A"], "P_cap_W": cr["P_cap_W"],
                          "V_ripple_pp_V": cr["V_ripple_pp_V"], "I_dc_avg_A": cr["I_dc_A"],
                          "fsw_used_Hz": (cr.get("operating") or {}).get("fsw_used_Hz"),
                          "assumption": cr.get("assumption")}
    else:
        out["dc_link"] = None
    out["not_evaluated"] = ["NVH / torque ripple orders", "bearing current / common-mode stress",
                            "exact pulse peak (only the conservative bound)"]
    return out


# --------------------------------------------------------------------------------------------- policy comparison

@dataclass(frozen=True)
class PwmLimits:
    """Mandatory constraints of the comparison (declared; a missing limit is not a pass).

    Every mandatory check is REQUIRED unless the project declares it not applicable (``not_applicable``, names from
    ``NA_CHECKS``): a required check that is not established makes the policy UNKNOWN - never ADMISSIBLE, never a
    Pareto member or 'best' (review R2 CT-01)."""

    Tj_max_C: float | None = None
    i_peak_incl_ripple_max_A: float | None = None     # device / over-current protection peak incl. switching ripple
    cap_rms_max_A: float | None = None
    phase_margin_min_deg: float | None = None
    pulse_ratio_min: float | None = None              # declared lower bound of the qualified PWM family (no default)
    transition_excursion_max_A: float | None = None   # allowed current excursion caused by a carrier-frequency change
    not_applicable: tuple = ()                        # mandatory checks declared not applicable for this project

    def __post_init__(self):
        na = tuple(str(x) for x in (self.not_applicable or ()))
        bad = [x for x in na if x not in NA_CHECKS]
        if bad:
            raise InputValidationError(f"not-applicable checks must be among {NA_CHECKS}: {bad}",
                                       field="not_applicable")
        object.__setattr__(self, "not_applicable", na)


NA_CHECKS = ("current_sampling", "Tj", "peak_current", "capacitor_rms", "phase_margin", "pulse_ratio",
             "transitions", "chatter")


def _segment_eval(base_drive, cand, seg: dict, fsw: float, coolant_C: float, limits_dc, timing: TimingConfig,
                  loop: CurrentLoop | None, L_hf_H: float, harmonic: HarmonicLossData | None, bank, source,
                  modulation: str, sensing: SensingConfig | None = None) -> dict:
    from ..analysis.efficiency import module_point
    from ..scenario import Scenario
    from .dclink_ripple import ripple_analysis
    sc = Scenario("pwm", float(seg["speed_rpm"]), float(seg["Vdc_V"]), limits_dc, coolant_temp_C=coolant_C)
    r = module_point(base_drive, cand, sc, float(seg["torque_Nm"]), coolant_C, fsw, None, None)
    out = {"fsw_Hz": fsw, "status": r["status"], "reason": r.get("reason", ""), "Tj_C": r.get("Tj_C")}
    p = r.get("point")
    led = delay_ledger(fsw, timing)
    out["timing"] = {k: led[k] for k in ("deadline_ok", "deadline_margin_s", "total_delay_s", "update_period_s")}
    plant = None
    if p is not None:
        plant = differential_inductances(base_drive, sc, p["id_A"], p["iq_A"])
        out["plant_L_H"] = plant
    if loop is not None and led["total_delay_s"] is not None:
        am = axis_margins(loop, led["total_delay_s"], fsw, plant, timing.updates_per_period, timing)
        out["timing"].update({"phase_margin_deg": am["phase_margin_deg"], "binding_axis": am["binding_axis"],
                              "sampled_stable": am["sampled_stable"],
                              "axes": {a: {k: v.get(k) for k in ("phase_margin_deg", "crossover_Hz", "plant_L_H",
                                                                  "design_L_H", "sampled_stable", "spectral_radius",
                                                                  "continuous_screen_phase_margin_deg")}
                                       for a, v in am["axes"].items()},
                              "plant": "machine differential inductance at the operating point" if plant else
                                       "design inductance (operating point not established)"})
    if p is None:
        return out
    d = r["detail"] or {}
    vdc = float(seg["Vdc_V"])
    m = float(d.get("modulation_index") or 0.0)
    fe_true = abs(float(seg["speed_rpm"])) * base_drive.motor.pole_pairs / 60.0
    fe = max(fe_true, fsw / 400.0)
    rip = phase_ripple(vdc, m, 0.0, fe, fsw, L_hf_H, modulation, n_per_carrier=128)
    # conservative bound: the fundamental peak and the ripple peak assumed aligned (not the exact pulse peak)
    ipk = p["i_peak_A"] + rip["ripple_peak_A"]
    mp = minimum_pulse(m, modulation, fsw, timing.min_pulse_s)
    from ..physics import DriveKernel
    Rs_T = DriveKernel(base_drive, sc).Rs                 # the same R_s(T) as the fundamental copper loss
    hcu = harmonic_copper_loss(rip, Rs_T, harmonic)
    mag = None if harmonic is None else harmonic.magnetic_hf_bound(fsw)
    out.update({"P_inv_W": p["Pinv_W"], "P_cu_fund_W": p["Pcu_W"], "P_dc_W": p["Pdc_W"], "i_peak_A": p["i_peak_A"],
                "ripple_rms_A": rip["ripple_rms_A"], "ripple_rms_spectrum_A": rip["ripple_rms_spectrum_A"],
                "ripple_peak_A": rip["ripple_peak_A"], "i_peak_bound_A": ipk,
                "ripple_quasi_static": fe_true < fsw / 400.0, "modulation_index": m,
                "pulse_ratio": (fsw / fe_true) if fe_true > 0 else None, "narrowest_pulse_s": mp["narrowest_pulse_s"],
                "min_pulse_ok": mp["ok"],
                "P_cu_pwm_W": hcu["W"], "P_cu_pwm_lower_bound_W": hcu["lower_bound_W"],
                "P_cu_pwm_status": hcu["status"], "P_cu_pwm_reason": hcu["reason"],
                "rac_coverage_I2_fraction": hcu["rac_coverage_I2_fraction"], "harmonic_bandwidth_Hz": hcu["bandwidth_Hz"],
                "Rs_T_ohm": Rs_T, "P_mag_hf_bound_W": mag,
                "motor_hf_interval_W": motor_hf_interval(hcu, mag),
                "fsw_requested_Hz": fsw, "fsw_waveform_used_Hz": rip["fsw_used_Hz"],
                "fsw_error_percent": 100.0 * (rip["fsw_used_Hz"] - fsw) / fsw,
                "linear_modulation": not rip["overmodulation"],
                "iq_A": p.get("iq_A"), "vq_V": p.get("vq_V"), "voltage_budget_V": p.get("voltage_budget_V")})
    if sensing is not None:
        sv = sampling_validity(m, modulation, fsw, fe_true, sensing, getattr(cand.model, "deadtime_s", 0.0) or 0.0,
                               p["i_peak_A"], 0.5 * m * vdc, L_hf_H)
        out["sampling"] = {k: sv[k] for k in ("status", "valid_fraction", "n_invalid", "n_samples", "max_age_s",
                                              "error_bound_A", "skew_error_bound_A", "min_window_s", "violations",
                                              "unknown", "quasi_static")}
    if bank is not None and fe_true > 0:
        pf = d.get("power_factor")
        phi = math.acos(max(-1.0, min(1.0, float(pf)))) if (pf is not None and math.isfinite(pf)) else 0.0
        cr = ripple_analysis(p["i_peak_A"], m, phi, fe_true, fsw, vdc, bank, source, modulation,
                             T_ref_C=coolant_C)             # the capacitor's boundary: the declared coolant
        out["I_cap_rms_A"] = cr["I_cap_rms_A"]
        out["P_cap_W"] = cr["P_cap_W"]
        out["fsw_capacitor_used_Hz"] = (cr.get("operating") or {}).get("fsw_used_Hz")
    return out


def evaluate_policies(base_drive, cand, segments: list[dict], policies: list, coolant_C: float, limits_dc,
                      timing: TimingConfig, L_hf_H: float, loop: CurrentLoop | None = None,
                      harmonic: HarmonicLossData | None = None, bank=None, source=None, modulation: str = "svpwm",
                      limits: PwmLimits | None = None, sensing: SensingConfig | None = None,
                      measurement_noise: dict | None = None) -> dict:
    """Every policy on the SAME trajectory, source and coolant; the first policy is the baseline.

    ``cand``: efficiency.ModuleCandidate (module data, own thermal path).  Segments carry duration_s, speed_rpm,
    torque_Nm, Vdc_V and optionally sensor_temp_C (the observable temperature the schedule may use).
    ``sensing``: declared current acquisition (sample validity per segment); ``measurement_noise``: peak-to-peak
    noise of the schedule's measurements (threshold chatter).  Each carrier-frequency change is replayed on the
    current loop at the new segment's operating point (gain / integrator mapping, saturation)."""
    if not policies:
        raise InputValidationError("no policies", field="policies")
    if modulation not in ("svpwm", "spwm"):
        raise InputValidationError("the policy evaluation's ripple / sampling / edge models support svpwm and spwm",
                                   field="modulation")
    declared_mod = getattr(cand.model, "modulation", modulation)
    if declared_mod != modulation:                   # ONE pulse pattern for losses, ripple, sampling and capacitor
        cand = replace(cand, model=replace(cand.model, modulation=modulation))
    lim = limits or PwmLimits()
    out = []
    for pol in policies:
        state, t_now, rows = None, 0.0, []
        events = []
        prev_fsw = None
        for seg in segments:
            meas = {"speed_rpm": abs(float(seg["speed_rpm"])), "torque_abs_Nm": abs(float(seg["torque_Nm"])),
                    "Vdc_V": float(seg["Vdc_V"]), "sensor_temp_C": seg.get("sensor_temp_C")}
            state = pol.step(state, meas, t_now)
            fsw = pol.fsw(state)
            ev = _segment_eval(base_drive, cand, seg, fsw, coolant_C, limits_dc, timing, loop, L_hf_H, harmonic, bank,
                               source, modulation, sensing)
            ev["duration_s"] = float(seg["duration_s"])
            if state.get("changed") and prev_fsw is not None:
                e = {"t_s": t_now, "from_fsw_Hz": prev_fsw, "to_fsw_Hz": fsw, "reason": state["reason"],
                     "carrier_change": fsw != prev_fsw}
                if fsw != prev_fsw and loop is not None and ev.get("iq_A") is not None and ev.get("vq_V") is not None:
                    lq = axis_loops(loop)["q"]
                    tr = transition_transient(lq, timing, prev_fsw, fsw, ev["iq_A"],
                                              ev["vq_V"] - lq.R_ohm * ev["iq_A"], ev["voltage_budget_V"],
                                              plant_L_H=(ev.get("plant_L_H") or {}).get("q"))
                    e["transient"] = {k: tr.get(k) for k in ("evaluated", "reason", "output_jump_V", "excursion_A",
                                                             "band_A", "settle_s", "saturated_samples", "Ki_eff_ratio",
                                                             "bumpless", "horizon_s")}
                events.append(e)
            prev_fsw = fsw
            rows.append(ev)
            t_now += float(seg["duration_s"])
        agg = _aggregate(pol, rows, events, lim, loop)
        ch = chatter_risk(pol, measurement_noise) if not pol.is_fixed else {"rows": [], "violations": [], "unknown": []}
        agg["chatter"] = ch
        agg["violations"] += ch["violations"]
        ch_open = [] if "chatter" in lim.not_applicable else ch["unknown"]
        agg["unverified"] += ch_open
        agg["unverified_required"] += ch_open
        agg["status"] = _status(agg["violations"], agg["unverified_required"],
                                [r for r in rows if r["status"] not in ("FEASIBLE", "INFEASIBLE")], agg["delivered"])
        agg["admissible"] = agg["status"] == "ADMISSIBLE"
        out.append(agg)
    base = out[0]
    for o in out[1:]:
        o["versus_baseline"] = _versus(base, o)
    adm = [o for o in out if o["admissible"]]
    pareto = _pareto(adm)
    best = min(adm, key=lambda o: o["E_inv_J"]) if adm and all(o["E_inv_J"] is not None for o in adm) else None
    return {"policies": out, "pareto": [o["policy"]["name"] for o in pareto],
            "best_inverter_energy_among_evaluated": None if best is None else best["policy"]["name"],
            "best_policy_energy_among_evaluated": _best_interval(adm),
            "limits": lim.__dict__, "coolant_C": coolant_C, "modulation": modulation,
            "module_modulation": {"declared": declared_mod, "used": modulation,
                                  "note": None if declared_mod == modulation else
                                  "the module data's declared modulation is replaced by the policy's: one pulse pattern "
                                  "for losses, ripple, sampling and capacitor current"},
            "energy_control_volume": ENERGY_CONTROL_VOLUME,
            "peak_current_meaning": "conservative bound I_fund,pk + max|di|: the fundamental peak and the ripple peak "
                                    "assumed aligned - not the exact pulse peak",
            "thermal_scope": THERMAL_SCOPE,
            "not_evaluated": ["EMI / NVH / bearing-current impact of the policy (the conducted-EMI page evaluates one "
                              "carrier frequency at a time)"],
            "meaning": "best among the evaluated admissible candidates only (no global or production optimum claimed); "
                       "a mandatory violation is never traded for efficiency; an inverter-loss gain is not a "
                       "motor+inverter gain - that is an interval comparison with the PWM copper and the declared "
                       "Fe+PM HF bound"}


ENERGY_CONTROL_VOLUME = {
    "name": "inverter semiconductors + motor PWM harmonic loss (policy-sensitive losses between the inverter HV "
            "terminal and the motor shaft)",
    "included": ["inverter semiconductor loss (module model, established)",
                 "motor PWM harmonic copper (exact with R_ac(f) data, else its R_dc lower bound)",
                 "motor Fe+PM HF magnetic loss as its declared upper bound (interval end, never an expected value)"],
    "excluded": ["fundamental copper: the same operating point for every policy (kept in each policy's ledger)",
                 "DC-link capacitor ESR loss: ownership not declared - shown separately (E_cap_J)",
                 "gate drive / controller LV power: external LV supply, not an HV efficiency term"]}

THERMAL_SCOPE = ("module Tj per segment is a steady electrothermal fixed point at the declared coolant (Tj = T_coolant "
                 "+ Rth P_hot); the schedule's sensor temperature T_ntc is the SUPPLIED trajectory (an input "
                 "observable) - not a closed-loop mission thermal simulation (loss -> thermal network -> NTC -> "
                 "scheduler -> fsw)")


def _best_interval(adm: list) -> dict:
    """The admissible policy whose whole policy-energy interval lies below every other's lower end, if any."""
    if not adm:
        return {"policy": None, "status": "NONE", "reason": "no admissible policy"}
    if len(adm) == 1:
        return {"policy": adm[0]["policy"]["name"], "status": "ONLY_ADMISSIBLE", "reason": "the only admissible policy"}
    for a in adm:
        hi = a["energy"]["upper_J"]
        if hi is not None and all(b is a or (b["energy"]["lower_J"] is not None and hi < b["energy"]["lower_J"])
                                  for b in adm):
            return {"policy": a["policy"]["name"], "status": "SEPARATED",
                    "reason": "its whole energy interval lies below every other admissible policy's lower end"}
    open_ = [a["policy"]["name"] for a in adm if a["energy"]["upper_J"] is None]
    return {"policy": None, "status": "UNKNOWN" if open_ else "UNDECIDED",
            "reason": (f"open energy interval(s): {', '.join(open_)}" if open_ else "the energy intervals overlap")}


def _status(viol, required_unknown, open_, delivered) -> str:
    """VIOLATION on any violated mandatory check; UNKNOWN while a required check or a segment is not established;
    ADMISSIBLE only when every required check holds (review R2 CT-01)."""
    if viol:
        return "VIOLATION"
    if required_unknown or open_ or not delivered:
        return "UNKNOWN"
    return "ADMISSIBLE"


def _aggregate(pol, rows, events, lim: PwmLimits, loop: CurrentLoop | None = None) -> dict:
    viol, unknown, advisory = [], [], []
    na = set(lim.not_applicable)

    def need(check: str, text: str):
        """A required check that is not established (skipped only when declared not applicable)."""
        if check not in na:
            unknown.append(text)

    delivered = all(r["status"] == "FEASIBLE" for r in rows)
    bad = [k for k, r in enumerate(rows) if r["status"] == "INFEASIBLE"]
    open_ = [k for k, r in enumerate(rows) if r["status"] not in ("FEASIBLE", "INFEASIBLE")]
    if bad:
        viol.append("requirement not delivered (INFEASIBLE) in segment(s) " + ", ".join(str(k + 1) for k in bad))
    for k in open_:
        unknown.append(f"segment {k + 1}: requirement not established ({rows[k]['status']}: "
                       f"{rows[k].get('reason') or 'no reason given'})")
    for k, r in enumerate(rows):
        if r.get("linear_modulation") is False:
            unknown.append(f"segment {k + 1}: modulation index beyond the linear range of the declared PWM family "
                           "(overmodulation / six-step not supported: ripple, loss and timing not evaluated)")
        smp = r.get("sampling")
        if smp is None:
            continue
        viol += [f"segment {k + 1} current sampling: {x}" for x in smp["violations"]]
        for x in smp["unknown"]:
            need("current_sampling", f"segment {k + 1} current sampling: {x}")
    if rows and all(r.get("sampling") is None for r in rows):
        need("current_sampling", "current-sampling validity not evaluated (no acquisition configuration declared)")
    for e in events:
        if not e.get("carrier_change"):
            continue                                 # a rule change at the same carrier frequency
        tr = e.get("transient")
        tag = f"transition {e['from_fsw_Hz'] / 1e3:g} -> {e['to_fsw_Hz'] / 1e3:g} kHz at {e['t_s']:g} s"
        if tr is None:
            need("transitions", f"{tag}: not replayed (no current loop / operating voltage declared)")
            continue
        if not tr.get("evaluated"):
            viol.append(f"{tag}: {tr.get('reason')}") if "deadline" in str(tr.get("reason")) else \
                need("transitions", f"{tag}: {tr.get('reason')}")
            continue
        if tr["excursion_A"] <= tr.get("band_A", 0.0):
            pass                                     # stays inside the settling band: nothing to judge
        elif lim.transition_excursion_max_A is None:
            need("transitions", f"{tag}: current excursion {tr['excursion_A']:.4g} A - no allowed excursion declared")
        elif tr["excursion_A"] > lim.transition_excursion_max_A:
            why = "integrator reset" if tr.get("output_jump_V") and loop is not None and \
                axis_loops(loop)["q"].on_transition == "reset" \
                else ("error-sum integrator under Ki*Ts remapping" if not tr.get("bumpless") else "loop dynamics")
            viol.append(f"{tag}: current excursion {tr['excursion_A']:.4g} A > {lim.transition_excursion_max_A:g} A "
                        f"({why}; output jump {tr['output_jump_V']:.4g} V)")
        if tr.get("Ki_eff_ratio") is not None and abs(tr["Ki_eff_ratio"] - 1.0) > 1e-9:
            advisory.append(f"{tag}: fixed discrete gains change the effective Ki by x{tr['Ki_eff_ratio']:.3g} "
                            "(gain-state jump of the loop dynamics; judged by the phase margin per segment)")

    def tot(key):
        vals = [r.get(key) for r in rows]
        if any(v is None for v in vals):
            return None
        return float(sum(v * r["duration_s"] for v, r in zip(vals, rows)))
    E_inv, E_cu, E_h = tot("P_inv_W"), tot("P_cu_fund_W"), tot("P_cu_pwm_W")
    E_h_lb, E_mag, E_cap = tot("P_cu_pwm_lower_bound_W"), tot("P_mag_hf_bound_W"), tot("P_cap_W")
    tj = [r["Tj_C"] for r in rows if r.get("Tj_C") is not None]
    ipk = [r["i_peak_bound_A"] for r in rows if r.get("i_peak_bound_A") is not None]
    ferr = [abs(r["fsw_error_percent"]) for r in rows if r.get("fsw_error_percent") is not None]
    if ferr and max(ferr) > 0.5:
        k = max(range(len(rows)), key=lambda j: abs(rows[j].get("fsw_error_percent") or 0.0))
        advisory.append(f"segment {k + 1}: waveform models (ripple, sampling, capacitor) use the synchronous carrier "
                        f"{rows[k]['fsw_waveform_used_Hz'] / 1e3:.4g} kHz, the module loss and the schedule the "
                        f"requested {rows[k]['fsw_requested_Hz'] / 1e3:.4g} kHz "
                        f"({rows[k]['fsw_error_percent']:+.3g} %)")
    icap = [r["I_cap_rms_A"] for r in rows if r.get("I_cap_rms_A") is not None]
    pm = [r["timing"].get("phase_margin_deg") for r in rows if r["timing"].get("phase_margin_deg") is not None]
    np_ = [r["pulse_ratio"] for r in rows if r.get("pulse_ratio") is not None]
    if any(not r["timing"]["deadline_ok"] for r in rows):
        viol.append("control deadline missed at the scheduled frequency")
    unstable = [k for k, r in enumerate(rows) if r["timing"].get("sampled_stable") is False]
    if unstable:
        viol.append("the implemented (sampled) current loop is unstable in segment(s) " +
                    ", ".join(str(k + 1) for k in unstable) + " - a positive continuous margin is not sufficient")
    if any(r.get("min_pulse_ok") is False for r in rows):
        viol.append("commanded pulse narrower than the declared minimum")
    for check, name, vals, limit, hi in (("Tj", "Tj", tj, lim.Tj_max_C, True),
                                         ("peak_current", "peak-current conservative bound (I_fund,pk + max|di|)",
                                          ipk, lim.i_peak_incl_ripple_max_A, True),
                                         ("capacitor_rms", "capacitor RMS current", icap, lim.cap_rms_max_A, True),
                                         ("phase_margin", "current-loop phase margin", pm, lim.phase_margin_min_deg,
                                          False),
                                         ("pulse_ratio", "pulse ratio", np_, lim.pulse_ratio_min, False)):
        if check in na:
            continue
        if limit is None:
            need(check, f"{name}: no limit declared")
            continue
        if not vals or len(vals) < len(rows):
            need(check, f"{name}: not evaluated in every segment")
            if not vals:
                continue
        worst = max(vals) if hi else min(vals)
        if (worst > limit) if hi else (worst < limit):
            viol.append(f"{name} {worst:.4g} vs limit {limit:g}")
    status = _status(viol, unknown, open_, delivered)
    return {"policy": pol.describe(), "segments": rows, "transitions": events, "violations": viol,
            "unverified": unknown + advisory, "unverified_required": unknown, "advisory": advisory,
            "not_applicable_declared": sorted(na), "status": status, "admissible": status == "ADMISSIBLE",
            "delivered": delivered,
            "E_inv_J": E_inv, "E_cu_fund_J": E_cu, "E_cu_pwm_J": E_h, "E_cu_pwm_lower_bound_J": E_h_lb,
            "E_mag_hf_bound_J": E_mag, "E_cap_J": E_cap, "energy": policy_energy(E_inv, E_h, E_h_lb, E_mag),
            "Tj_max_C": max(tj) if tj else None, "i_peak_bound_max_A": max(ipk) if ipk else None,
            "I_cap_rms_max_A": max(icap) if icap else None, "phase_margin_min_deg": min(pm) if pm else None,
            "pulse_ratio_min": min(np_) if np_ else None,
            "fsw_waveform_error_max_percent": max(ferr) if ferr else None}


def policy_energy(E_inv, E_cu_pwm, E_cu_pwm_lb, E_mag) -> dict:
    """Policy-sensitive energy inside the control volume 'inverter semiconductors + motor PWM harmonic loss' as an
    interval: known = inverter energy (established); the motor PWM copper is exact with R_ac(f) data, otherwise only
    its R_dc lower bound; the Fe+PM HF part is at most its declared bound.  The upper end is open (None) while the
    copper is only lower-bounded or the bound is missing - an unknown is never filled with 0."""
    if E_inv is None:
        return {"lower_J": None, "upper_J": None, "status": "UNKNOWN", "reason": "inverter energy not established"}
    cu_lo = E_cu_pwm if E_cu_pwm is not None else E_cu_pwm_lb
    lo = None if cu_lo is None else E_inv + cu_lo
    hi = None if (E_cu_pwm is None or E_mag is None) else E_inv + E_cu_pwm + E_mag
    why = []
    if E_cu_pwm is None:
        why.append("motor PWM copper: R_dc lower bound only (no R_ac(f) coverage)")
    if E_mag is None:
        why.append("Fe+PM HF magnetic loss: no declared bound at every carrier frequency")
    return {"lower_J": lo, "upper_J": hi, "known_J": E_inv, "status": "BOUNDED" if hi is not None else "OPEN",
            "reason": "; ".join(why)}


def _versus(base: dict, o: dict) -> dict:
    """Inverter-loss change (established when both deliver) and the motor+inverter change as an interval comparison:
    [E_inv + E_cu,fund + PWM copper (exact or lower bound), ... + exact copper + declared Fe+PM HF bound]."""
    if not (base["delivered"] and o["delivered"]):
        return {"status": "NOT_COMPARABLE", "reason": "both policies must deliver the same trajectory"}
    d_inv = o["E_inv_J"] - base["E_inv_J"]
    res = {"delta_E_inv_J": d_inv, "relative_inv": d_inv / base["E_inv_J"] if base["E_inv_J"] else None}

    def iv(x):
        e = x["energy"]
        add = x["E_cu_fund_J"] or 0.0
        return (None if e["lower_J"] is None else e["lower_J"] + add, None if e["upper_J"] is None else e["upper_J"] + add)
    lo_b, hi_b = iv(base)
    lo_o, hi_o = iv(o)
    res["total"] = {"interval_candidate_J": [lo_o, hi_o], "interval_baseline_J": [lo_b, hi_b],
                    **compare_intervals((lo_o, hi_o), (lo_b, hi_b)),
                    "open_ends": "; ".join(x for x in (base["energy"]["reason"], o["energy"]["reason"]) if x),
                    "meaning": "motor+inverter energy as [known, known + open parts] intervals; the Fe+PM bound is "
                               "never added as an expected value"}
    return res


def compare_intervals(a: tuple, b: tuple) -> dict:
    """a vs b (lower is better): IMPROVED when a's upper end is below b's lower end, WORSE when a's lower end is
    above b's upper end; UNDECIDED when both are finite and overlap; UNKNOWN when an open end prevents a decision."""
    lo_a, hi_a = a
    lo_b, hi_b = b
    if lo_a is None or lo_b is None:
        return {"status": "UNKNOWN", "reason": "a lower end is not established"}
    if hi_a is not None and hi_a < lo_b:
        return {"status": "IMPROVED", "reason": "the whole candidate interval is below the baseline interval"}
    if hi_b is not None and lo_a > hi_b:
        return {"status": "WORSE", "reason": "the whole candidate interval is above the baseline interval"}
    if hi_a is not None and hi_b is not None:
        return {"status": "UNDECIDED", "reason": "the intervals overlap"}
    return {"status": "UNKNOWN", "reason": "an interval is open (exact PWM copper or the Fe+PM bound missing)"}


def _pareto(rows: list) -> list:
    """Non-dominated admissible policies on (inverter energy, motor+inverter policy energy as an INTERVAL, peak Tj,
    peak-current bound, capacitor current, -phase margin).  Point objectives that are None for any policy are left
    out; the interval objective dominates only by separation (a's upper end <= b's lower end) - overlapping or open
    intervals never decide, the Fe+PM bound is never an expected value."""
    keys = [("E_inv_J", 1), ("Tj_max_C", 1), ("i_peak_bound_max_A", 1), ("I_cap_rms_max_A", 1),
            ("phase_margin_min_deg", -1)]
    keys = [(k, s) for k, s in keys if all(r.get(k) is not None for r in rows)]

    def e_le(a, b):              # a <= b on the energy interval (proven), and strictly
        lo_b, hi_a = b["energy"]["lower_J"], a["energy"]["upper_J"]
        if lo_b is None or hi_a is None:
            return False, False
        return hi_a <= lo_b, hi_a < lo_b
    out = []
    for a in rows:
        dom = False
        for b in rows:
            if a is b:
                continue
            le = all(s * b[k] <= s * a[k] for k, s in keys)
            lt = any(s * b[k] < s * a[k] for k, s in keys)
            e_le_ba, e_lt_ba = e_le(b, a)
            same_energy = (b["energy"]["lower_J"], b["energy"]["upper_J"]) == (a["energy"]["lower_J"],
                                                                                a["energy"]["upper_J"])
            if le and (e_le_ba or same_energy) and (lt or e_lt_ba):
                dom = True
                break
        if not dom:
            out.append(a)
    return out
