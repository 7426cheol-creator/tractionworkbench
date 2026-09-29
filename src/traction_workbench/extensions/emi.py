"""Conducted EMI of the HV DC port: source -> path -> receiver (handoff section 8.7 / 11, P1-C; OEW addendum 5.1-5.2;
independent review R2, EMC-01..04).

What this module is - and is not:

* the **requirement profile** (standard / edition, customer revision, class or approved curve, port, method,
  detector, RBW per frequency segment, artificial network, fixture, operating condition, design reserve, agreed
  decision rule) is an input; a profile missing a decisive field is REQUIREMENT_INCOMPLETE and nothing is judged
  against it.  No standard limit values are built in: the limit curve is entered from the approved source, and a band
  the approved curve leaves without a requirement is DECLARED as a gap - any other hole is undefined, never a pass;
* **source**: the exact line spectrum of one synchronous fundamental period of the leg sequences built from the gate
  commands with the dead time inserted as a turn-on delay and the diode clamp while both switches are off (a gate
  pulse shorter than the dead time vanishes - pulses never reverse), with declared (not estimated) rise / fall
  times.  Overmodulation, overlapping edge ramps, an undeclared minimum-pulse handling and an asynchronous carrier
  whose ratio is not an integer are outside the source validity (screening only);
* **path**: a declared lumped network (DC link with ESR / ESL, Y capacitors with mounting inductance, switch-node /
  motor-to-chassis capacitance, harness, optional common-mode choke as coupled inductors, the artificial network) is
  solved by nodal analysis at every line, both sources together (coherent, phases kept); CM and DM are not forced
  to be independent;
* **receiver**: the lines inside a rectangular RBW window are summed in magnitude (a line-sum estimate, never a CISPR
  reading: IF shape, QP / AV weighting and dwell are not modelled).  While the receiver is tuned the window content
  changes only when a line enters or leaves it, so the estimate over a CONTINUOUS band is piecewise constant: its
  supremum and the minimum margin to a log-linear limit are computed EXACTLY by enumerating every window change and
  every limit vertex - the display grid is a plot, never the claim;
* **claims**: without an applicable calibration record the result is SCREENING (UNKNOWN): margins, the required
  attenuation A = max(0, E_U + M_d - L) and the dominant CM / DM path, "predicted exceedance" instead of FAIL.  A
  calibration record is complete only with its evidence, hold-out, acquisition, error model, finite model-error
  bounds (a zero bound needs its reason), frequency intervals, measurement set-up, path-network identity and source
  ranges, and it applies only when set-up, network and source match THIS evaluation (re-checked on every run).  The
  claim domain is the band intersected with the calibrated intervals, the network validity, the RBW definition and
  the limit coverage: FEASIBLE when E + U_upper <= L - M_d at every receiver frequency of the band (declared gaps
  excepted), INFEASIBLE only with a witness frequency where E - U_lower > L - M_d, otherwise UNKNOWN - an exceedance
  of the upper bound alone is not a violation.  A measured receiver trace is judged against the same profile with
  its OWN acquisition metadata (representation, detector, RBW, IF shape, dwell, set-up), coverage between the
  readings included.

Conventions (two conductors, currents in the same spatial direction):
v_CM = (v+ + v-)/2, v_DM = v+ - v-, i_CM = i+ + i-, i_DM = (i+ - i-)/2, so v+ i+ + v- i- = v_CM i_CM + v_DM i_DM.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from dataclasses import fields as dc_fields

import numpy as np

from .. import progress
from ..errors import InputValidationError
from ..identity import content_sha256
from ..modulation import duties
from ..status import Claim, Evidence, EvidenceKind, Reason, Status
from ..validation import finite as _finite

TWO_PI = 2.0 * math.pi
PROFILE_FIELDS = ("standard", "edition", "customer_revision", "curve_id", "port", "method", "detector", "rbw_Hz",
                  "network", "fixture", "operating_condition")
DETECTORS = ("peak", "quasi_peak", "average")
DETECTOR_RANK = {"average": 0, "quasi_peak": 1, "peak": 2}     # peak >= QP >= AV for the same signal and RBW
METHODS = {"voltage_AN": "voltage at the artificial-network measuring port",
           "current_probe": "current probe on the HV line"}
MODEL_METHOD, MODEL_UNIT = "voltage_AN", "dBuV"                 # what the source-path model predicts
CARRIERS = ("asynchronous", "synchronous")
MIN_PULSE_POLICIES = ("none", "drop", "unknown")
SOURCE_RANGE_KEYS = ("Vdc_V", "I_pk_A", "fe_Hz", "fsw_Hz", "m", "beta_rad", "alpha_rad", "t_rise_s", "t_fall_s",
                     "t_dead_s", "min_pulse_s")
SOURCE_MODE_KEYS = ("modulation", "carrier", "min_pulse_policy")
SETUP_KEYS = ("port", "method", "detector", "unit", "rbw_Hz", "network", "fixture")
CAL_TEXT_KEYS = ("evidence", "holdout", "acquisition", "error_model")
TRACE_REPRESENTATIONS = ("raw_sweep", "max_envelope", "final_list")
IF_SHAPES = ("gaussian", "rectangular")
TRACE_SETUP_KEYS = ("port", "method", "network", "fixture", "operating_condition")
CERT_BUDGET = 2.0e9          # line x edge products the exact receiver-band evaluation may spend
WINDOW_TOL = 1e-7            # harmonic-index slack at a window edge: wider for bounds, narrower for witnesses
NUM_ALLOW_DB = 1e-6          # floating-point allowance of the exact band evaluation (checked by the solve residual)
RESIDUAL_MAX = 1e-9          # largest accepted relative residual of the nodal solve in a certified evaluation


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


def _db_or_nan(v_rms) -> np.ndarray:
    """dBuV where a line was summed, NaN where the window held no line (nothing to read, not -600 dB)."""
    v = np.asarray(v_rms, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(v > 0, 20.0 * np.log10(v / 1e-6), np.nan)


# --------------------------------------------------------------------------------------------- requirement profile

@dataclass(frozen=True)
class LimitCurve:
    """Approved limit points (f_Hz, level) in dBuV or dBuA for one detector; log-frequency linear interpolation, steps
    by repeating a frequency (the stricter level holds AT the step); nothing outside the declared points (no
    extrapolation).  ``gaps`` are open bands (f_lo, f_hi) the approved curve explicitly leaves without a requirement;
    any other hole in an analysis band is UNDEFINED, never a pass."""

    points: tuple
    unit: str = "dBuV"
    detector: str = "peak"
    source: str = ""
    gaps: tuple = ()

    def __post_init__(self):
        try:
            pts = tuple((float(f), float(v)) for f, v in self.points)
        except (TypeError, ValueError):
            raise InputValidationError("limit points must be (f_Hz, level) pairs", field="limit") from None
        if (len(pts) < 2 or any(not (math.isfinite(f) and math.isfinite(v)) for f, v in pts)
                or any(f <= 0 for f, _ in pts) or any(b[0] < a[0] for a, b in zip(pts, pts[1:]))):
            raise InputValidationError("limit curve needs >= 2 finite points with non-decreasing frequency > 0",
                                       field="limit")
        if self.unit not in ("dBuV", "dBuA"):
            raise InputValidationError("limit unit must be dBuV or dBuA", field="limit.unit")
        if self.detector not in DETECTORS:
            raise InputValidationError(f"detector must be one of {DETECTORS}", field="limit.detector")
        if not self.source.strip():
            raise InputValidationError("a limit curve needs its source (standard / customer document, revision)",
                                       field="limit.source")
        gaps = []
        for g in self.gaps or ():
            try:
                lo, hi = (float(x) for x in g)
            except (TypeError, ValueError):
                raise InputValidationError("a limit gap is (f_lo_Hz, f_hi_Hz)", field="limit.gaps") from None
            if not (math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi):
                raise InputValidationError("a limit gap needs finite 0 < f_lo < f_hi", field="limit.gaps")
            gaps.append((lo, hi))
        gaps.sort()
        if any(b[0] < a[1] for a, b in zip(gaps, gaps[1:])):
            raise InputValidationError("limit gaps must not overlap", field="limit.gaps")
        object.__setattr__(self, "points", pts)
        object.__setattr__(self, "gaps", tuple(gaps))

    @property
    def span(self) -> tuple:
        return self.points[0][0], self.points[-1][0]

    def vertices(self) -> np.ndarray:
        """Every frequency where the curve's slope or definition changes (points and gap edges)."""
        return np.unique(np.array([f for f, _ in self.points] + [x for g in self.gaps for x in g], dtype=float))

    def at(self, f) -> np.ndarray:
        f = np.atleast_1d(np.asarray(f, dtype=float))
        out = np.full(f.shape, np.inf)
        with np.errstate(divide="ignore", invalid="ignore"):
            lf = np.log10(np.where(f > 0, f, np.nan))
        for (f0, l0), (f1, l1) in zip(self.points, self.points[1:]):
            m = (f >= f0) & (f <= f1)
            if not m.any():
                continue
            if f1 == f0:
                val = np.full(int(m.sum()), min(l0, l1))
            else:
                w = (lf[m] - math.log10(f0)) / (math.log10(f1) - math.log10(f0))
                val = l0 + w * (l1 - l0)
            out[m] = np.minimum(out[m], val)
        out[~np.isfinite(out)] = np.nan
        for lo, hi in self.gaps:
            out[(f > lo) & (f < hi)] = np.nan
        return out

    def min_over(self, a: float, b: float) -> float:
        """Smallest level on the closed interval [a, b] (end points and interior vertices; NaN parts ignored)."""
        v = self.vertices()
        pts = np.concatenate([[a, b], v[(v > a) & (v < b)]])
        vals = self.at(pts)
        return float(np.nanmin(vals)) if np.any(np.isfinite(vals)) else math.nan

    def partition(self, lo: float, hi: float) -> list:
        """[(a, b, 'covered' | 'gap' | 'undefined')] tiling [lo, hi]."""
        f0, f1 = self.span
        cuts = {lo, hi} | {x for x in (f0, f1) if lo < x < hi} | {x for g in self.gaps for x in g if lo < x < hi}
        cuts = sorted(cuts)
        out = []
        for a, b in zip(cuts, cuts[1:]):
            mid = math.sqrt(a * b)
            if any(g0 < mid < g1 for g0, g1 in self.gaps):
                kind = "gap"
            elif f0 <= mid <= f1:
                kind = "covered"
            else:
                kind = "undefined"
            out.append((a, b, kind))
        return out


def _rbw_segments(v, name="rbw_Hz") -> tuple:
    """A scalar RBW (whole spectrum) or segments [(f_lo, f_hi, rbw)] - validated, sorted, not overlapping."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return ()
    if isinstance(v, (list, tuple)):
        segs = []
        for s in v:
            try:
                lo, hi, r = s
                lo, hi, r = float(lo), (math.inf if hi is None else float(hi)), float(r)
            except (TypeError, ValueError):
                raise InputValidationError("an RBW segment is (f_lo_Hz, f_hi_Hz or None, rbw_Hz)", field=name) from None
            if not (math.isfinite(lo) and lo >= 0 and hi > lo and math.isfinite(r) and r > 0):
                raise InputValidationError("an RBW segment needs 0 <= f_lo < f_hi and a finite RBW > 0", field=name)
            segs.append((lo, hi, r))
        segs.sort()
        if any(b[0] < a[1] for a, b in zip(segs, segs[1:])):
            raise InputValidationError("RBW segments must not overlap", field=name)
        return tuple(segs)
    try:
        r = float(v)
    except (TypeError, ValueError):
        raise InputValidationError(f"RBW must be a number or segments, got {v!r}", field=name) from None
    if not (math.isfinite(r) and r > 0):
        raise InputValidationError("RBW must be finite and > 0", field=name)
    return ((0.0, math.inf, r),)


def _rbw_at(segs: tuple, f) -> np.ndarray:
    f = np.atleast_1d(np.asarray(f, dtype=float))
    out = np.full(f.shape, np.nan)
    for lo, hi, r in segs:
        m = np.isnan(out) & (f >= lo) & (f <= hi)
        out[m] = r
    return out


def _same_segments(a: tuple, b: tuple) -> bool:
    return len(a) == len(b) and all(
        all((x == y) or (math.isfinite(x) and math.isfinite(y) and abs(x - y) <= 1e-9 * max(1.0, abs(x), abs(y)))
            for x, y in zip(sa, sb)) for sa, sb in zip(a, b))


@dataclass(frozen=True)
class EmiProfile:
    """The approved requirement: identity fields (PROFILE_FIELDS; ``method`` is a key of METHODS, ``rbw_Hz`` a
    scalar or [(f_lo, f_hi, rbw)] segments), the limit curve, the design reserve; optional ``decision_rule`` (the
    agreed rule for measured traces) and ``min_dwell_s``."""

    fields: dict = field(default_factory=dict)
    limit: LimitCurve | None = None
    design_reserve_dB: float = 0.0

    def __post_init__(self):
        if _finite("design_reserve_dB", self.design_reserve_dB) < 0:
            raise InputValidationError("the design reserve must be >= 0 dB", field="design_reserve_dB")
        self.rbw_segments()

    def rbw_segments(self) -> tuple:
        return _rbw_segments(self.fields.get("rbw_Hz"))

    def rbw_at(self, f) -> np.ndarray:
        return _rbw_at(self.rbw_segments(), f)

    @property
    def method(self) -> str:
        return str(self.fields.get("method", "") or "").strip()

    @property
    def detector(self) -> str:
        return str(self.fields.get("detector", "") or "").strip()

    def decision_rule(self) -> str:
        return str(self.fields.get("decision_rule", "") or "").strip()

    def missing(self) -> list:
        miss = []
        for k in PROFILE_FIELDS:
            if k == "rbw_Hz":
                if not self.rbw_segments():
                    miss.append(k)
            elif not str(self.fields.get(k, "") or "").strip():
                miss.append(k)
        if self.limit is None:
            miss.append("limit curve")
        elif self.detector and self.detector != self.limit.detector:
            miss.append(f"detector mismatch (profile {self.detector}, curve {self.limit.detector})")
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
    fsw_Hz: float                # requested carrier frequency
    t_rise_s: float
    t_fall_s: float
    t_dead_s: float = 0.0
    modulation: str = "svpwm"
    basis: str = ""              # where tr / tf / dead time come from (measured, gate setting ...)
    carrier: str = "asynchronous"   # asynchronous: fixed fsw - the synchronous line model is exact only when
    #                                  fsw / fe is an integer; synchronous: fsw locked to an integer multiple of fe
    min_pulse_s: float = 0.0     # controller minimum gate pulse
    min_pulse_policy: str = "none"  # none (every pulse issued; min_pulse_s = 0) | drop (duty clamped per carrier
    #                                  period) | unknown (a minimum exists, its handling is not declared)

    def __post_init__(self):
        for name in ("Vdc_V", "fe_Hz", "fsw_Hz", "t_rise_s", "t_fall_s"):
            if _finite(name, getattr(self, name)) <= 0:
                raise InputValidationError(f"{name} must be > 0 (rise / fall times are declared, never estimated "
                                           f"from power or gate resistance)", field=name)
        for name in ("I_pk_A", "m"):
            if _finite(name, getattr(self, name)) < 0:
                raise InputValidationError(f"{name} must be >= 0", field=name)
        for name in ("beta_rad", "alpha_rad"):
            _finite(name, getattr(self, name))
        if _finite("t_dead_s", self.t_dead_s) < 0:
            raise InputValidationError("dead time must be >= 0", field="t_dead_s")
        if self.modulation not in ("svpwm", "spwm"):
            raise InputValidationError("modulation must be svpwm or spwm", field="modulation")
        if self.carrier not in CARRIERS:
            raise InputValidationError(f"carrier must be one of {CARRIERS}", field="carrier")
        if self.min_pulse_policy not in MIN_PULSE_POLICIES:
            raise InputValidationError(f"minimum-pulse policy must be one of {MIN_PULSE_POLICIES}",
                                       field="min_pulse_policy")
        if _finite("min_pulse_s", self.min_pulse_s) < 0:
            raise InputValidationError("minimum pulse must be >= 0", field="min_pulse_s")
        if self.min_pulse_policy == "none" and self.min_pulse_s > 0:
            raise InputValidationError("a declared minimum pulse needs its handling (drop, or unknown)",
                                       field="min_pulse_policy")
        Ts = self.carrier_period_s
        if 2.0 * self.t_dead_s + self.t_rise_s + self.t_fall_s >= Ts:
            raise InputValidationError(
                f"dead time and edge times do not fit the carrier period: 2 td + tr + tf = "
                f"{(2 * self.t_dead_s + self.t_rise_s + self.t_fall_s) * 1e6:.4g} us >= Ts = {Ts * 1e6:.4g} us",
                field="t_dead_s")
        if 2.0 * self.min_pulse_s >= Ts:
            raise InputValidationError("the minimum pulse must be shorter than half the carrier period",
                                       field="min_pulse_s")

    @property
    def carrier_ratio(self) -> int:
        return max(1, int(round(self.fsw_Hz / self.fe_Hz)))

    @property
    def fsw_used_Hz(self) -> float:
        return self.carrier_ratio * self.fe_Hz

    @property
    def carrier_period_s(self) -> float:
        return 1.0 / self.fsw_used_Hz

    def values(self) -> dict:
        """The quantities a calibration's source ranges are checked against (fsw is the one evaluated)."""
        return {"Vdc_V": self.Vdc_V, "I_pk_A": self.I_pk_A, "fe_Hz": self.fe_Hz, "fsw_Hz": self.fsw_used_Hz,
                "m": self.m, "beta_rad": self.beta_rad, "alpha_rad": self.alpha_rad, "t_rise_s": self.t_rise_s,
                "t_fall_s": self.t_fall_s, "t_dead_s": self.t_dead_s, "min_pulse_s": self.min_pulse_s}

    def modes(self) -> dict:
        return {"modulation": self.modulation, "carrier": self.carrier, "min_pulse_policy": self.min_pulse_policy}


def _duties(theta, m, alpha, modulation):
    """Leg duty cycles (3 x N, unclipped) from the shared modulation law."""
    return duties(theta, m, alpha, modulation)


def _merge_points(p: np.ndarray, tol: float) -> np.ndarray:
    p = np.sort(p)
    if p.size == 0:
        return p
    keep = np.concatenate([[True], np.diff(p) > tol])
    return p[keep]


def _commanded(d_row, tc, Ts, T):
    """Commanded upper-switch transitions of one leg over one period: sorted [(t in [0, T), +1 on / -1 off)], or the
    constant commanded state when there is none (centre-aligned regular-sampled PWM; touching pulses merge)."""
    eps = 1e-12 * Ts
    ivs = []
    for j, dj in enumerate(d_row):
        if dj >= 1.0:
            a, b = j * Ts, (j + 1) * Ts
        elif dj > 0.0:
            a, b = tc[j] - 0.5 * dj * Ts, tc[j] + 0.5 * dj * Ts
        else:
            continue
        if ivs and a <= ivs[-1][1] + eps:
            ivs[-1][1] = b
        else:
            ivs.append([a, b])
    if not ivs:
        return [], 0
    if len(ivs) == 1 and ivs[0][0] <= eps and ivs[0][1] >= T - eps:
        return [], 1
    if len(ivs) > 1 and ivs[0][0] <= eps and ivs[-1][1] >= T - eps:      # the on-interval wraps the period
        first = ivs.pop(0)
        ivs[-1][1] = T + first[1]
    out = []
    for a, b in ivs:
        out.append((0.0 if a <= eps else a % T, +1))
        out.append((0.0 if abs(b - T) <= eps else b % T, -1))
    out.sort()
    return out, None


def _leg_sequence(trans, const, T, td, i_fn, zeros, tol):
    """Pole transitions of one leg: dead time inserted as a turn-on delay of both switches, pole = 1 while the upper
    switch conducts, 0 while the lower one does, and while both are off the diode clamp: i > 0 (out of the leg) ->
    lower diode, pole 0; i < 0 -> upper diode, pole 1.  Returns ([(t, +-1, kind)], state just before t = 0, i.e.
    after the previous period's last edge - an edge AT t = 0 is one of the returned transitions)."""
    if not trans:
        return [], const
    tc = np.array([t for t, _ in trans])
    sc = np.array([s for _, s in trans])
    bps = _merge_points(np.concatenate([tc, np.mod(tc + td, T), np.mod(zeros, T), [0.0]]), tol)
    ends = np.append(bps[1:], bps[0] + T)
    states = []
    for a, b in zip(bps, ends):
        if b - a <= tol:
            states.append(None)
            continue
        mid = (0.5 * (a + b)) % T
        idx = int(np.searchsorted(tc, mid, side="right")) - 1
        delta = mid - tc[idx] if idx >= 0 else mid - tc[-1] + T
        c = 1 if sc[idx] > 0 else 0
        if delta >= td:
            s = c                                      # the commanded switch conducts (or its diode)
        else:
            s = 1 if i_fn(mid) < 0 else 0              # both off: the diode that takes the current
        states.append(s)
    # fill slivers with the previous state (a zero-length interval carries no state)
    last = next((s for s in reversed(states) if s is not None), 0)
    filled = []
    for s in states:
        last = last if s is None else s
        filled.append(last)
    gate = _merge_points(np.concatenate([tc, np.mod(tc + td, T)]), tol)
    out = []
    for i, t in enumerate(bps):
        prev = filled[i - 1] if i > 0 else filled[-1]
        if filled[i] != prev:
            kind = "gate" if np.any(np.abs(np.mod(gate - t + 0.5 * T, T) - 0.5 * T) <= tol) else "zero_crossing"
            out.append((float(t), +1 if filled[i] > prev else -1, kind))
    return out, filled[-1]


def pwm_edges(src: SwitchingSource) -> dict:
    """Every pole transition of one fundamental period (synchronous carrier, symmetric regular sampling at the carrier
    centre), built as a switching-state sequence: dead time as a turn-on delay, diode clamp by the current sign,
    optional minimum-pulse handling.  Edges are (leg, t, +1 / -1, ramp time, phase current at t); ``validity`` lists
    what puts the waveform outside the trapezoidal-edge source; ``initial_state`` is each pole's state just before
    t = 0 (state + every edge in [0, T) in order = the sequence)."""
    ratio = src.carrier_ratio
    T = 1.0 / src.fe_Hz
    Ts = T / ratio
    tc = (np.arange(ratio) + 0.5) * Ts
    th = TWO_PI * src.fe_Hz * tc
    d = _duties(th, src.m, src.alpha_rad, src.modulation)
    over = bool(np.any(d < -1e-9) or np.any(d > 1 + 1e-9))
    d = np.clip(d, 0.0, 1.0)
    below_min = 0
    if src.min_pulse_s > 0:
        dm = src.min_pulse_s / Ts
        short = ((d > 0) & (d < dm)) | ((d < 1) & (d > 1 - dm))
        if src.min_pulse_policy == "drop":
            d = np.where(d < dm, 0.0, np.where(d > 1 - dm, 1.0, d))
        else:
            below_min = int(np.count_nonzero(short))
    tol = 1e-12 * Ts
    w = TWO_PI * src.fe_Hz
    legs, init, suppressed, zc = [], [], 0, 0
    for k in range(3):
        ph = src.beta_rad - TWO_PI * k / 3.0
        trans, const = _commanded(d[k], tc, Ts, T)
        if trans and src.t_dead_s > 0:                 # gate pulses (on or off) no longer than the dead time vanish
            tt = np.array([t for t, _ in trans])
            widths = np.diff(np.append(tt, tt[0] + T))
            suppressed += int(np.count_nonzero(widths <= src.t_dead_s + tol))
        zeros = (np.array([0.5 * math.pi, 1.5 * math.pi]) - ph) / w

        def i_fn(t, ph=ph):
            return src.I_pk_A * math.cos(w * t + ph)
        seq, s0 = _leg_sequence(trans, const, T, src.t_dead_s, i_fn, zeros, tol)
        init.append(int(s0))
        for t, sg, kind in seq:
            if kind == "zero_crossing":
                zc += 1
            legs.append((k, t, sg, src.t_rise_s if sg > 0 else src.t_fall_s, i_fn(t)))
    # ordered, non-overlapping ramps per leg: each ramp must end before the next edge of the same leg starts
    short_pulses, min_width, min_slack = 0, math.inf, math.inf
    for k in range(3):
        ek = sorted((e for e in legs if e[0] == k), key=lambda e: e[1])
        if len(ek) < 2:
            continue
        tt = np.array([e[1] for e in ek])
        gaps = np.diff(np.append(tt, tt[0] + T))
        ramps = np.array([e[3] for e in ek])
        min_width = min(min_width, float(gaps.min()))
        slack = gaps - ramps
        min_slack = min(min_slack, float(slack.min()))
        short_pulses += int(np.count_nonzero(slack < 0))
    fsw_used = ratio * src.fe_Hz
    problems, notes = [], []
    if over:
        problems.append("overmodulation: duties clipped (not a supported source)")
    if short_pulses:
        problems.append(f"{short_pulses} pole pulse(s) shorter than their edge ramp (tr / tf overlap): partial swings "
                        f"are outside the trapezoidal-edge source")
    if below_min:
        problems.append(f"{below_min} commanded pulse(s) below the declared minimum pulse "
                        f"{src.min_pulse_s * 1e6:g} us with an undeclared handling")
    ratio_exact = abs(src.fsw_Hz / src.fe_Hz - ratio) <= 1e-9 * ratio
    if src.carrier == "asynchronous" and not ratio_exact:
        problems.append(f"asynchronous carrier with fsw / fe = {src.fsw_Hz / src.fe_Hz:.6g}: the synchronous line model "
                        f"(ratio {ratio}, {fsw_used / 1e3:.6g} kHz) moves every carrier group - not the declared "
                        f"operation")
    if src.I_pk_A == 0.0 and src.t_dead_s > 0:
        problems.append("zero phase current with dead time: no diode defines the pole during the dead time")
    if suppressed:
        notes.append(f"{suppressed} gate pulse(s) no longer than the dead time vanish (dead-time insertion)")
    if zc:
        notes.append(f"{zc} diode commutation(s) at a current zero crossing inside a dead time, modelled as an ideal "
                     f"transition with the declared ramp")
    if not ratio_exact:
        notes.append(f"requested fsw {src.fsw_Hz / 1e3:.6g} kHz, evaluated {fsw_used / 1e3:.6g} kHz "
                     f"({src.carrier} carrier)")
    return {"edges": legs, "period_s": T, "carrier_ratio": ratio, "fsw_used_Hz": fsw_used,
            "fsw_requested_Hz": src.fsw_Hz, "overmodulation": over, "initial_state": init,
            "validity": {"ok": not problems, "problems": problems, "notes": notes, "short_pulses": short_pulses,
                         "min_pulse_width_s": None if math.isinf(min_width) else min_width,
                         "min_ramp_slack_s": None if math.isinf(min_slack) else min_slack,
                         "suppressed_gate_pulses": suppressed, "zero_crossing_commutations": zc,
                         "below_min_pulse": below_min, "ratio_exact": ratio_exact}}


LINE_BLOCK = 1 << 18        # lines x edges evaluated at a time (4 MB of complex terms)


def edge_lines(t0, tau, dv, freqs, T):
    """One-sided complex line amplitudes (peak) of a periodic piecewise-linear signal made of ramps.

    c_n = 1/(j 2 pi f_n T) * sum dv_e exp(-j 2 pi f_n (t_e + tau_e/2)) sinc(f_n tau_e); line amplitude = 2 c_n.

    Evaluated a block of lines at a time: each line is the same sum over the edges as in one lines x edges matrix
    (bit for bit), without that matrix (at standstill, fe = fsw/400, 24000 x 2400 terms: 3.2 GB at its peak).
    """
    f_all = np.asarray(freqs, dtype=float)
    t0, tau, dv = np.asarray(t0), np.asarray(tau), np.asarray(dv)
    out = np.empty(f_all.size, dtype=complex)
    rows = max(1, LINE_BLOCK // max(1, t0.size))
    with progress.span(-(-f_all.size // rows), "line spectrum") as sp:
        for a in range(0, f_all.size, rows):
            sp.step()
            f = f_all[a:a + rows, None]
            ph = np.exp(-1j * TWO_PI * f * (t0[None, :] + 0.5 * tau[None, :]))
            sinc = np.sinc(f * tau[None, :])
            s = (ph * sinc * dv[None, :]).sum(axis=1)
            out[a:a + rows] = 2.0 * s / (1j * TWO_PI * f[:, 0] * T)
    return out


def source_lines(src: SwitchingSource, freqs, edges: dict | None = None) -> dict:
    """Complex line amplitudes (peak) at the given harmonics of fe: switch-node CM voltage (poles averaged, relative
    to the DC midpoint) and the inverter DC input current (positive into the inverter from DC+).

    The DC current is taken as steps of +-i_k(t_edge) at the edges (the slow variation of i_k between edges is
    neglected: an error of order (di/dt) / (2 pi f)^2, negligible in the conducted-emission band, visible near fsw).
    """
    e = edges or pwm_edges(src)
    T = e["period_s"]
    meta = {k: e[k] for k in ("carrier_ratio", "fsw_used_Hz", "overmodulation")}
    if not e["edges"]:
        z = np.zeros(len(freqs), dtype=complex)
        return {"v_cm": z, "i_dm": z, **meta, "edges": 0}
    k, t0, sgn, tau, cur = (np.array(x) for x in zip(*e["edges"]))
    v_cm = edge_lines(t0, tau, sgn * src.Vdc_V / 3.0, freqs, T)
    i_dm = edge_lines(t0, tau, sgn * cur, freqs, T)
    return {"v_cm": v_cm, "i_dm": i_dm, **meta, "edges": len(t0)}


def sampled_waveforms(src: SwitchingSource, n_per_carrier: int = 400) -> dict:
    """Time-domain reconstruction of the same edges (used to cross-check the edge-sum spectrum): pole = state at 0 plus
    every ramp, the ramps of the previous period that run past t = 0 included."""
    e = pwm_edges(src)
    T = e["period_s"]
    n = e["carrier_ratio"] * n_per_carrier
    t = (np.arange(n) + 0.5) * T / n
    poles = np.tile(np.asarray(e["initial_state"], dtype=float)[:, None], (1, n))
    ia = [src.I_pk_A * np.cos(TWO_PI * src.fe_Hz * t + src.beta_rad - TWO_PI * kk / 3.0) for kk in range(3)]
    for (kk, t0, sgn, tau, cur) in e["edges"]:
        r0 = np.clip((t - t0) / tau, 0.0, 1.0)
        r1 = np.clip((t - t0 + T) / tau, 0.0, 1.0)
        poles[kk] += sgn * (r0 + r1 - 1.0)
    v_cm = src.Vdc_V * (poles.mean(axis=0) - 0.5)
    iinv = np.zeros(n)
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
        if self.validated_up_to_Hz is not None and _finite("validated_up_to_Hz", self.validated_up_to_Hz) <= 0:
            raise InputValidationError("the validated upper frequency must be > 0", field="validated_up_to_Hz")


NETWORK_PARAMS = tuple(f.name for f in dc_fields(HvNetwork) if f.name not in ("basis", "validated_up_to_Hz"))


def network_identity(net: HvNetwork) -> str:
    """Content hash of the network's physics (every component value; the basis text and validity excluded): a
    calibration made on another network does not apply."""
    return content_sha256({k: getattr(net, k) for k in NETWORK_PARAMS})


_N_NODES = 9
_X = _N_NODES + 3                       # + i_src(cm), i_line+, i_line-


def _network_matrix(net: HvNetwork, f: np.ndarray) -> np.ndarray:
    """Nodal matrix at every frequency (nodes 1 inv+, 2 inv-, 3 AN+ (EUT side), 4 AN-, 5 meas+, 6 meas-, 7 sup+,
    8 sup-, 9 switch-node CM source; ground = chassis; extra unknowns: the CM source current and the two coupled
    harness / choke branch currents)."""
    w = TWO_PI * f
    jw = 1j * w
    nf = f.size
    N = _N_NODES
    A = np.zeros((nf, _X, _X), dtype=complex)

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
    # CM source: current i_s from the DC midpoint (half from each rail) through the source into node 9
    s = N
    A[:, 0, s] += 0.5          # leaves node 1
    A[:, 1, s] += 0.5          # leaves node 2
    A[:, 8, s] -= 1.0          # enters node 9
    A[:, s, 8] += 1.0
    A[:, s, 0] -= 0.5
    A[:, s, 1] -= 0.5
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
    return A


def _unit_sources() -> np.ndarray:
    """Right-hand sides (X x 2) of a unit CM source voltage and a unit inverter DM current (out of node 1, into 2)."""
    B = np.zeros((_X, 2), dtype=complex)
    B[_N_NODES, 0] = 1.0
    B[0, 1] = -1.0
    B[1, 1] = 1.0
    return B


def solve_network(net: HvNetwork, f, v_cm, i_dm) -> dict:
    """Nodal analysis at every frequency with both sources active (coherent).

    The CM source is referenced to the DC midpoint without creating a DM path: V9 - (V1 + V2)/2 = v_cm, its current
    returns half into each rail (equal split: an assumption about the HF symmetry of the DC link).  Returns the
    measurement-port voltages and branch currents.
    """
    f = np.atleast_1d(np.asarray(f, dtype=float))
    A = _network_matrix(net, f)
    B = _unit_sources()
    b = np.asarray(v_cm)[:, None] * B[None, :, 0] + np.asarray(i_dm)[:, None] * B[None, :, 1]
    x = np.linalg.solve(A, b[..., None])[..., 0]
    N = _N_NODES
    V = x[:, :N]
    return {"f_Hz": f, "v_meas_plus": V[:, 4], "v_meas_minus": V[:, 5], "i_line_plus": x[:, N + 1],
            "i_line_minus": x[:, N + 2], "i_cm_source": x[:, N], "v_inv_plus": V[:, 0], "v_inv_minus": V[:, 1]}


def port_transfer(net: HvNetwork, f) -> tuple:
    """Measuring-port voltages per unit source: (H_plus, H_minus, residual), H = [per unit v_cm, per unit i_dm]
    (n x 2), residual = the largest relative residual |A x - b| / (|A| |x| + |b|) of the solve."""
    f = np.atleast_1d(np.asarray(f, dtype=float))
    A = _network_matrix(net, f)
    B = np.broadcast_to(_unit_sources(), (f.size, _X, 2))
    x = np.linalg.solve(A, B)
    r = np.abs(A @ x - B).max(axis=(1, 2))
    scale = np.abs(A).max(axis=(1, 2)) * np.abs(x).max(axis=(1, 2)) + 1.0
    return x[:, 4, :], x[:, 5, :], float(np.max(r / scale)) if f.size else 0.0


# --------------------------------------------------------------------------------------------- calibration record

@dataclass(frozen=True)
class EmiCalibration:
    """Evidence that qualifies the source-path-receiver prediction: E_true - E_model in [-U_lower, +U_upper] on the
    declared frequency intervals, for the declared measurement set-up, path network (content identity) and source
    ranges - nothing else."""

    evidence: str
    holdout: str
    acquisition: str
    error_model: str
    U_upper_dB: float
    U_lower_dB: float
    f_intervals_Hz: tuple
    setup: dict
    network_sha256: str
    source_ranges: dict
    source_modes: dict
    zero_uncertainty_basis: str = ""

    def to_dict(self) -> dict:
        return {"evidence": self.evidence, "holdout": self.holdout, "acquisition": self.acquisition,
                "error_model": self.error_model, "U_upper_dB": self.U_upper_dB, "U_lower_dB": self.U_lower_dB,
                "f_intervals_Hz": [list(x) for x in self.f_intervals_Hz], "setup": dict(self.setup),
                "network_sha256": self.network_sha256,
                "source_ranges": {k: list(v) for k, v in self.source_ranges.items()},
                "source_modes": dict(self.source_modes), "zero_uncertainty_basis": self.zero_uncertainty_basis}


def calibration_from_dict(d: dict) -> tuple:
    """(EmiCalibration, []) for a complete record, else (None, problems): a missing field is never a zero bound."""
    problems = []
    if not isinstance(d, dict):
        return None, ["calibration must be a record (dict)"]

    def text(k):
        v = str(d.get(k) or "").strip()
        if not v:
            problems.append(f"{k} missing")
        return v
    ev, ho, acq, em = (text(k) for k in CAL_TEXT_KEYS)

    def bound(k):
        v = d.get(k)
        try:
            x = float(v)
        except (TypeError, ValueError):
            problems.append(f"{k} must be a number (got {v!r}): a missing model-error bound is not 0 dB")
            return None
        if not math.isfinite(x) or x < 0:
            problems.append(f"{k} must be finite and >= 0 dB (got {v!r})")
            return None
        return x
    if "U_upper_dB" in d or "U_lower_dB" in d:
        up, lo = bound("U_upper_dB"), bound("U_lower_dB")
    else:
        up = lo = bound("uncertainty_dB")
    zb = str(d.get("zero_uncertainty_basis") or "").strip()
    if (up == 0.0 or lo == 0.0) and not zb:
        problems.append("a zero model-error bound needs zero_uncertainty_basis (why the model error vanishes)")
    ivs = []
    raw = d.get("f_intervals_Hz")
    if not raw:
        problems.append("f_intervals_Hz missing (the frequency intervals the correlation covers)")
    else:
        for iv in raw:
            try:
                a, b = (float(x) for x in iv)
            except (TypeError, ValueError):
                problems.append(f"f_intervals_Hz entry {iv!r} is not (f_lo, f_hi)")
                continue
            if not (math.isfinite(a) and math.isfinite(b) and 0 < a < b):
                problems.append(f"f_intervals_Hz entry {iv!r} needs finite 0 < f_lo < f_hi")
                continue
            ivs.append((a, b))
    setup = d.get("setup") or {}
    for k in SETUP_KEYS:
        v = setup.get(k)
        if v is None or (isinstance(v, (str, list, tuple)) and not v):
            problems.append(f"setup.{k} missing")
    if setup.get("rbw_Hz") not in (None, "", [], ()):
        try:
            _rbw_segments(setup.get("rbw_Hz"), "calibration.setup.rbw_Hz")
        except InputValidationError as exc:
            problems.append(f"setup.rbw_Hz: {exc}")
    nsha = str(d.get("network_sha256") or "").strip()
    if not nsha:
        problems.append("network_sha256 missing (the path network the correlation was made with)")
    rng = d.get("source_ranges") or {}
    ranges = {}
    for k in SOURCE_RANGE_KEYS:
        v = rng.get(k)
        try:
            a, b = (float(x) for x in v)
        except (TypeError, ValueError):
            problems.append(f"source_ranges.{k} missing or not (lo, hi)")
            continue
        if not (math.isfinite(a) and math.isfinite(b) and a <= b):
            problems.append(f"source_ranges.{k} needs finite lo <= hi")
            continue
        ranges[k] = (a, b)
    modes = d.get("source_modes") or {}
    for k in SOURCE_MODE_KEYS:
        if not str(modes.get(k) or "").strip():
            problems.append(f"source_modes.{k} missing")
    if problems:
        return None, problems
    return EmiCalibration(ev, ho, acq, em, up, lo, tuple(ivs), dict(setup), nsha, ranges,
                          {k: str(modes[k]) for k in SOURCE_MODE_KEYS}, zb), []


def calibration_mismatches(cal: EmiCalibration, profile: EmiProfile, net: HvNetwork, src: SwitchingSource) -> list:
    """Why a complete record does not apply to THIS evaluation (re-checked on every run: a changed waveform, filter,
    fixture or detector is caught here, whatever the record's name)."""
    out = []
    lim = profile.limit
    want = {"port": profile.fields.get("port"), "method": profile.method, "detector": profile.detector,
            "unit": None if lim is None else lim.unit, "network": profile.fields.get("network"),
            "fixture": profile.fields.get("fixture")}
    for k, v in want.items():
        have = str(cal.setup.get(k, "") or "").strip()
        if have != str(v or "").strip():
            out.append(f"{k}: calibration {have!r} vs this evaluation {str(v or '')!r}")
    try:
        if not _same_segments(_rbw_segments(cal.setup.get("rbw_Hz")), profile.rbw_segments()):
            out.append("RBW differs from the calibration's receiver setting")
    except InputValidationError as exc:
        out.append(f"calibration RBW invalid: {exc}")
    if cal.network_sha256 != network_identity(net):
        out.append("path network differs from the one the correlation was made with (network identity)")
    vals = src.values()
    for k, (lo, hi) in cal.source_ranges.items():
        v = vals[k]
        tol = 1e-9 * max(1.0, abs(lo), abs(hi))
        if not (lo - tol <= v <= hi + tol):
            out.append(f"source {k} = {v:.6g} outside the calibrated range [{lo:.6g}, {hi:.6g}]")
    for k, v in src.modes().items():
        if str(cal.source_modes.get(k)) != v:
            out.append(f"source {k} {v!r} vs calibration {cal.source_modes.get(k)!r}")
    return out


def configuration_snapshot(profile: EmiProfile, net: HvNetwork, src: SwitchingSource) -> dict:
    """What a calibration record made for THIS evaluation binds to (degenerate source ranges at this point)."""
    lim = profile.limit
    return {"setup": {"port": profile.fields.get("port"), "method": profile.method, "detector": profile.detector,
                      "unit": None if lim is None else lim.unit,
                      "rbw_Hz": [[lo, None if math.isinf(hi) else hi, r] for lo, hi, r in profile.rbw_segments()]
                      if len(profile.rbw_segments()) != 1 or not math.isinf(profile.rbw_segments()[0][1])
                      else profile.rbw_segments()[0][2],
                      "network": profile.fields.get("network"), "fixture": profile.fields.get("fixture")},
            "network_sha256": network_identity(net),
            "source_ranges": {k: [v, v] for k, v in src.values().items()}, "source_modes": src.modes()}


# --------------------------------------------------------------------------------------------- receiver

def receiver_grid(f_lo: float, f_hi: float, n: int = 240) -> np.ndarray:
    return np.geomspace(f_lo, f_hi, n)


def _window(fr, h, fe, wide=True):
    """Harmonic indices [n0, n1] inside the closed window [fr - h, fr + h]: ``wide`` includes a line within rounding
    of an edge (upper bounds), narrow excludes it (witnesses)."""
    tol = WINDOW_TOL if wide else -WINDOW_TOL
    fr = np.asarray(fr, dtype=float)
    n0 = np.ceil((fr - h) / fe - tol).astype(np.int64)
    n1 = np.floor((fr + h) / fe + tol).astype(np.int64)
    return np.maximum(n0, 1), n1


def line_sum_estimate(grid, rbw_Hz, fe_Hz: float, value_fn) -> tuple[np.ndarray, int]:
    """Sum of |line| (peak amplitudes) of every harmonic of fe inside the closed window [fr - RBW/2, fr + RBW/2],
    reported as an RMS-calibrated reading (/sqrt2); 0 when no line falls inside, NaN where the RBW is undefined.
    ``rbw_Hz`` is a scalar or one value per grid frequency; value_fn(freqs) -> complex peak amplitudes."""
    rb = np.broadcast_to(np.asarray(rbw_Hz, dtype=float), (len(grid),))
    out = np.zeros(len(grid))
    total = 0
    for i, fr in enumerate(grid):
        if not np.isfinite(rb[i]):
            out[i] = np.nan
            continue
        n0, n1 = _window(fr, 0.5 * rb[i], fe_Hz)
        if n1 < n0:
            continue
        freqs = np.arange(int(n0), int(n1) + 1) * fe_Hz
        total += freqs.size
        out[i] = float(np.sum(np.abs(value_fn(freqs)))) / math.sqrt(2.0)
    return out, total


def _display_lines(src, e, net, grid, rbw):
    """Line sums on the display grid: both ports, CM / DM of the pair and the CM- / DM-source shares at HV+."""
    fe = src.fe_Hz
    ok = np.isfinite(rbw)
    n0, n1 = _window(grid, 0.5 * np.where(ok, rbw, 0.0), fe)
    ns = [np.arange(a, b + 1) for a, b, o in zip(n0, n1, ok) if o and b >= a]
    allh = np.unique(np.concatenate(ns)) if ns else np.zeros(0, dtype=np.int64)
    out = {k: np.full(grid.size, np.nan) for k in ("plus", "minus", "cm", "dm", "from_cm_source", "from_dm_source")}
    if allh.size == 0:
        return out, 0.0
    f = allh * fe
    sl = source_lines(src, f, e)
    Hp, Hm, res = port_transfer(net, f)
    vp_cm, vp_dm = Hp[:, 0] * sl["v_cm"], Hp[:, 1] * sl["i_dm"]
    vp = vp_cm + vp_dm
    vm = Hm[:, 0] * sl["v_cm"] + Hm[:, 1] * sl["i_dm"]
    pair = cm_dm(vp, vm)
    mags = {"plus": np.abs(vp), "minus": np.abs(vm), "cm": np.abs(pair["v_CM"]), "dm": np.abs(pair["v_DM"]),
            "from_cm_source": np.abs(vp_cm), "from_dm_source": np.abs(vp_dm)}
    for i in range(grid.size):
        if not ok[i]:
            continue
        if n1[i] < n0[i]:
            for k in out:
                out[k][i] = 0.0
            continue
        a, b = np.searchsorted(allh, [n0[i], n1[i] + 1])
        for k in out:
            out[k][i] = float(mags[k][a:b].sum()) / math.sqrt(2.0)
    return out, res


def _band_lines(src, e, net, n_lo, n_hi, block_elems=4_000_000, net_block=8192):
    """|V+|, |V-| (peak) of every harmonic n_lo..n_hi at the AN measuring ports: exact edge sums in harmonic blocks
    (exp(-j 2 pi n x) = exp(-j 2 pi n0 x) exp(-j 2 pi k x), the second factor computed once), then the nodal solve."""
    T = e["period_s"]
    fe = src.fe_Hz
    N = n_hi - n_lo + 1
    Ap, Am = np.zeros(N), np.zeros(N)
    if not e["edges"] or N <= 0:
        return Ap, Am, 0.0
    k, t0, sgn, tau, cur = (np.asarray(x, dtype=float) for x in zip(*e["edges"]))
    x = (t0 + 0.5 * tau) / T
    rising = sgn > 0
    W = np.stack([sgn * src.Vdc_V / 3.0, sgn * cur], axis=1).astype(complex)
    K = int(max(64, min(1 << 16, block_elems // max(x.size, 1))))
    kk_all = np.arange(K)[:, None]
    Mr = np.exp(-2j * np.pi * kk_all * x[rising][None, :])
    Mf = np.exp(-2j * np.pi * kk_all * x[~rising][None, :])
    res_max = 0.0
    for s in range(0, N, K):
        n0 = n_lo + s
        kk = min(K, N - s)
        B = np.exp(-2j * np.pi * np.mod(n0 * x, 1.0))
        Wb = B[:, None] * W
        Sr = Mr[:kk] @ Wb[rising]
        Sf = Mf[:kk] @ Wb[~rising]
        fn = (n0 + np.arange(kk)) * fe
        c = (np.sinc(fn * src.t_rise_s)[:, None] * Sr + np.sinc(fn * src.t_fall_s)[:, None] * Sf) \
            * (2.0 / (1j * TWO_PI * fn * T))[:, None]
        for q in range(0, kk, net_block):
            r = slice(q, min(kk, q + net_block))
            Hp, Hm, res = port_transfer(net, fn[r])
            res_max = max(res_max, res)
            Ap[s + r.start: s + r.stop] = np.abs(np.sum(Hp * c[r], axis=1))
            Am[s + r.start: s + r.stop] = np.abs(np.sum(Hm * c[r], axis=1))
    return Ap, Am, res_max


def _domain(f_lo, f_hi, profile: EmiProfile, net: HvNetwork, cal: EmiCalibration | None) -> list:
    """Partition of the band: requirement (covered / declared gap / undefined), RBW, and why a part cannot carry a
    claim (network validity, calibrated intervals)."""
    cuts = {f_lo, f_hi}
    lim = profile.limit
    if lim is not None:
        for a, b, _ in lim.partition(f_lo, f_hi):
            cuts |= {a, b}
    for lo, hi, _ in profile.rbw_segments():
        cuts |= {x for x in (lo, hi) if f_lo < x < f_hi}
    v = net.validated_up_to_Hz
    if v is not None and f_lo < v < f_hi:
        cuts.add(v)
    if cal is not None:
        cuts |= {x for iv in cal.f_intervals_Hz for x in iv if f_lo < x < f_hi}
    cuts = sorted(cuts)
    rows = []
    for a, b in zip(cuts, cuts[1:]):
        mid = math.sqrt(a * b)
        if lim is None:
            kind = "undefined"
        else:
            kind = next((k for x0, x1, k in lim.partition(a, b) if x0 <= mid <= x1), "undefined")
        rbw = float(profile.rbw_at(mid)[0])
        reasons = []
        if kind == "undefined":
            reasons.append("no approved limit here (not a declared gap)")
        if not math.isfinite(rbw):
            reasons.append("RBW not defined")
        claim_reasons = []
        if v is not None and mid > v:
            claim_reasons.append(f"network validated only up to {v / 1e6:g} MHz")
        if cal is not None and not any(lo <= mid <= hi for lo, hi in cal.f_intervals_Hz):
            claim_reasons.append("outside the calibrated frequency intervals")
        row = {"lo_Hz": a, "hi_Hz": b, "requirement": kind, "rbw_Hz": rbw if math.isfinite(rbw) else None,
               "estimate": kind == "covered" and math.isfinite(rbw), "reasons": reasons,
               "claim_reasons": claim_reasons}
        prev = rows[-1] if rows else None
        if prev and all(prev[k] == row[k] for k in ("requirement", "rbw_Hz", "estimate", "reasons", "claim_reasons")):
            prev["hi_Hz"] = b
        else:
            rows.append(row)
    return rows


def _evaluate_rows(src, e, net, profile, rows, U_up, U_lo, grid, budget):
    """Exact receiver-band evaluation of every row with an estimate (see the module note): sup of E, the minimum
    margin L - M_d - (E + U_up) and the largest lower-bound exceedance (E - U_lo) - (L - M_d), with the frequencies.
    Returns (rows updated, summary, envelope on the display grid)."""
    fe = src.fe_Hz
    lim = profile.limit
    Md = profile.design_reserve_dB
    ne = max(len(e["edges"]), 1)
    ranges = []
    for r in rows:
        if not r["estimate"]:
            continue
        h = 0.5 * r["rbw_Hz"]
        a = max(1, int(math.ceil((r["lo_Hz"] - h) / fe - WINDOW_TOL)))
        b = int(math.floor((r["hi_Hz"] + h) / fe + WINDOW_TOL))
        ranges.append((r, a, b))
    cost = sum(max(0, b - a + 1) for _, a, b in ranges) * ne
    if not ranges:
        return rows, {"certified": False, "reason": "no part of the band has an approved limit and an RBW"}, None
    if cost > budget:
        return rows, {"certified": False, "cost": float(cost), "budget": float(budget),
                      "reason": f"exact receiver-band evaluation needs {cost:.3g} line x edge products (budget "
                                f"{budget:.3g}): narrow the band or raise the budget"}, None
    env = np.full(grid.size, -np.inf)
    edges_g = np.sqrt(grid[1:] * grid[:-1])
    lines_total, res_max = 0, 0.0
    verts = lim.vertices()
    for r, a, b in ranges:
        h = 0.5 * r["rbw_Hz"]
        Ap, Am, res = _band_lines(src, e, net, a, b)
        res_max = max(res_max, res)
        lines_total += Ap.size
        Pp = np.concatenate([[0.0], np.cumsum(Ap)])
        Pm = np.concatenate([[0.0], np.cumsum(Am)])
        # rounding bound of a window sum from two prefix values (recursive summation: n u sum|x|)
        err = 2.0 * Ap.size * 1.12e-16 * max(Pp[-1], Pm[-1])
        lo_f, hi_f = r["lo_Hz"], r["hi_Hz"]
        fn = np.arange(a, b + 1) * fe
        bp = np.concatenate([fn - h, fn + h])
        vv = verts[(verts >= lo_f) & (verts <= hi_f)]
        gg = grid[(grid >= lo_f) & (grid <= hi_f)]
        c = np.unique(np.concatenate([[lo_f, hi_f], bp[(bp >= lo_f) & (bp <= hi_f)], vv, gg]))
        # the closed windows AT the breakpoints dominate the open pieces (upper side); a witness needs a point
        # inside each piece as well (a narrow window at a breakpoint drops the lines on its edges)
        c = np.unique(np.concatenate([c, 0.5 * (c[1:] + c[:-1])]))

        def wsum(P, n0, n1):
            i0 = np.clip(n0 - a, 0, P.size - 1)
            i1 = np.clip(n1 - a + 1, 0, P.size - 1)
            return np.where(n1 >= n0, P[i1] - P[i0], 0.0)
        n0w, n1w = _window(c, h, fe, True)
        n0n, n1n = _window(c, h, fe, False)
        Sw = np.maximum(wsum(Pp, n0w, n1w), wsum(Pm, n0w, n1w)) + err
        Sn = np.maximum(np.maximum(wsum(Pp, n0n, n1n), wsum(Pm, n0n, n1n)) - err, 0.0)
        Ew = _db_or_nan(Sw / math.sqrt(2.0)) + NUM_ALLOW_DB
        En = _db_or_nan(Sn / math.sqrt(2.0)) - NUM_ALLOW_DB
        L = lim.at(c)
        up = L - Md - (np.nan_to_num(Ew, nan=-np.inf) + U_up)
        exc = (np.nan_to_num(En, nan=-np.inf) - U_lo) - (L - Md)
        i_up = int(np.nanargmin(up)) if np.any(np.isfinite(up)) else None
        i_ex = int(np.nanargmax(exc)) if np.any(np.isfinite(exc)) else None
        i_sup = int(np.nanargmax(Ew)) if np.any(np.isfinite(Ew)) else None
        r.update({"lines": int(Ap.size), "candidates": int(c.size),
                  "E_sup_dBuV": None if i_sup is None else float(Ew[i_sup]),
                  "f_E_sup_Hz": None if i_sup is None else float(c[i_sup]),
                  "min_margin_dB": None if i_up is None else float(up[i_up]),
                  "f_min_margin_Hz": None if i_up is None else float(c[i_up]),
                  "max_lower_exceedance_dB": None if i_ex is None else float(exc[i_ex]),
                  "f_witness_Hz": None if i_ex is None else float(c[i_ex]),
                  "witness": None if (i_ex is None or not exc[i_ex] > 0) else {
                      "f_Hz": float(c[i_ex]), "E_dBuV": float(En[i_ex] + NUM_ALLOW_DB),
                      "E_lower_dBuV": float(En[i_ex] - U_lo), "limit_dBuV": float(L[i_ex]),
                      "limit_minus_reserve_dBuV": float(L[i_ex] - Md),
                      "window_Hz": [float(c[i_ex] - h), float(c[i_ex] + h)]}})
        if grid.size > 1:
            idx = np.searchsorted(edges_g, c)
            np.maximum.at(env, idx, np.nan_to_num(Ew, nan=-np.inf))
    env = np.where(np.isfinite(env), env, np.nan)
    ok = res_max <= RESIDUAL_MAX
    summary = {"certified": ok, "lines": lines_total, "solve_residual": res_max,
               "method": "exact enumeration of every receiver window change and limit vertex (piecewise-constant "
                         "line sum); wide windows and a summation-rounding bound for the upper side, narrow windows "
                         "for a witness",
               "numerical_allowance_dB": NUM_ALLOW_DB}
    if not ok:
        summary["reason"] = f"nodal solve residual {res_max:.2e} above {RESIDUAL_MAX:.0e}: the network is too " \
                            f"ill-conditioned for a certified evaluation"
    return rows, summary, env


def conducted_emission_screening(src: SwitchingSource, net: HvNetwork, profile: EmiProfile, f_lo: float = 150e3,
                                 f_hi: float = 30e6, n_grid: int = 160, calibration=None,
                                 budget: float = CERT_BUDGET) -> dict:
    """Line-sum estimate of the AN measurement-port voltages over a continuous receiver band: exact band evaluation,
    display grid, margins and the required attenuation; a claim only with an applicable calibration record (see the
    module note)."""
    f_lo, f_hi = _finite("f_lo", f_lo), _finite("f_hi", f_hi)
    if not (0 < f_lo < f_hi):
        raise InputValidationError("the band needs 0 < f_lo < f_hi", field="band")
    if isinstance(n_grid, bool) or int(n_grid) != n_grid or n_grid < 2:
        raise InputValidationError("the display grid needs an integer >= 2 points", field="n_grid")
    e = pwm_edges(src)
    validity = e["validity"]
    missing = profile.missing()
    lim = profile.limit
    comparable_problems = []
    if profile.method != MODEL_METHOD:
        comparable_problems.append(
            f"method {profile.method or '(none)'!r}: the model predicts the {METHODS[MODEL_METHOD]} (method "
            f"{MODEL_METHOD!r}); another method needs its own transfer - units are never renamed")
    if lim is not None and lim.unit != MODEL_UNIT:
        comparable_problems.append(f"limit unit {lim.unit}: the model predicts {MODEL_UNIT} at the AN measuring port")
    comparable = not comparable_problems
    cal, cal_problems = None, []
    declared = calibration is not None and calibration != {}
    if isinstance(calibration, EmiCalibration):
        cal = calibration
    elif declared:
        cal, cal_problems = calibration_from_dict(calibration)
    mismatches = calibration_mismatches(cal, profile, net, src) if cal is not None else []
    applicable = cal is not None and not mismatches
    U_up = cal.U_upper_dB if applicable else 0.0
    U_lo = cal.U_lower_dB if applicable else 0.0
    Md = profile.design_reserve_dB

    grid = receiver_grid(f_lo, f_hi, int(n_grid))
    rbw_g = profile.rbw_at(grid)
    vals, _ = _display_lines(src, e, net, grid, rbw_g)
    E = _db_or_nan(np.fmax(vals["plus"], vals["minus"]))
    EU, EL = E + U_up, E - U_lo
    L = lim.at(grid) if (lim is not None and comparable) else np.full(grid.size, np.nan)
    margin = L - Md - EU
    A_req = np.where(np.isfinite(L) & np.isfinite(EU), np.maximum(0.0, EU + Md - L), np.nan)
    dom = np.where(np.nan_to_num(vals["from_cm_source"]) >= np.nan_to_num(vals["from_dm_source"]), "CM", "DM")

    rows = _domain(f_lo, f_hi, profile, net, cal if applicable else None)
    band, env = None, None
    if comparable and lim is not None:
        rows, band, env = _evaluate_rows(src, e, net, profile, rows, U_up, U_lo, grid, budget)
    for r in rows:
        if r["requirement"] == "gap":
            r["status"] = "no requirement (declared gap)"
        elif not r["estimate"]:
            r["status"] = "undefined"
        elif applicable and not r["claim_reasons"]:
            r["status"] = "evaluated"
        else:
            r["status"] = "screened"

    notes = ["line-sum estimate of the modelled lines: not a CISPR receiver reading (QP / AV weighting, IF filter "
             "shape and dwell not modelled)", "ideal-switch edges with declared rise / fall and dead time; ringing, "
             "reverse recovery and gate-loop effects are outside this source"]
    notes += validity["notes"]
    rbw_txt = ", ".join(f"{r / 1e3:g} kHz" + ("" if math.isinf(hi) and lo == 0 else f" ({lo / 1e6:g}-{hi / 1e6:g} MHz)")
                        for lo, hi, r in profile.rbw_segments()) or "undefined"
    q = ("conducted emission at the declared HV port within the approved limit and design reserve at every receiver "
         "frequency of the band")
    scope = (f"source-path-receiver line sum {f_lo / 1e6:g}-{f_hi / 1e6:g} MHz, RBW {rbw_txt}; "
             f"{'calibrated (applicable record)' if applicable else 'unvalidated lumped network'}")
    certified = bool(band and band.get("certified"))
    estimate_rows = [r for r in rows if r["estimate"] and r.get("min_margin_dB") is not None]

    def screening_detail():
        if certified and estimate_rows:
            worst = min(estimate_rows, key=lambda r: r["min_margin_dB"])
            m, fm = worst["min_margin_dB"], worst["f_min_margin_Hz"]
            txt = (f"predicted exceedance up to {-m:.1f} dB at {fm / 1e6:.4g} MHz (exact over the covered band)"
                   if m < 0 else f"screening margin >= {m:.1f} dB (exact over the covered band; not a pass)")
        else:
            ex = np.isfinite(margin) & (margin < 0)
            worst = float(np.nanmin(margin)) if np.any(np.isfinite(margin)) else None
            txt = (f"predicted exceedance up to {float(np.nanmax(A_req)):.1f} dB in {int(ex.sum())} of {grid.size} "
                   f"sampled grid points" if ex.any() else
                   (f"sampled screening margin >= {worst:.1f} dB (grid only, not a pass)" if worst is not None
                    else "no margin evaluated"))
        und = [r for r in rows if r["status"] == "undefined"]
        if und:
            txt += "; undefined: " + ", ".join(f"{r['lo_Hz'] / 1e6:g}-{r['hi_Hz'] / 1e6:g} MHz ({'; '.join(r['reasons'])})"
                                               for r in und)
        return txt

    ev_screen = (Evidence.make(EvidenceKind.EXACT_ENUMERATION, "exact receiver-band line-sum screening")
                 if certified else Evidence.make(EvidenceKind.SAMPLED, "line-sum screening on a log grid"))
    if missing:
        claim = Claim("conducted_emission", Status.UNKNOWN, q, scope, reasons=(Reason.REQUIREMENT_INCOMPLETE,),
                      detail="requirement profile incomplete: " + ", ".join(missing) + " (no PASS / FAIL)")
    elif not comparable:
        claim = Claim("conducted_emission", Status.UNKNOWN, q, scope, reasons=(Reason.OUTSIDE_MODEL_DOMAIN,),
                      detail="the prediction is not comparable with this requirement: " + "; ".join(comparable_problems))
    elif cal is None and not cal_problems:
        claim = Claim("conducted_emission", Status.UNKNOWN, q, scope, reasons=(Reason.SCREENING_ONLY,),
                      evidence=(ev_screen,), qualifiers=("screening margin",),
                      detail="SCREENING - " + screening_detail())
    elif cal_problems:
        claim = Claim("conducted_emission", Status.UNKNOWN, q, scope,
                      reasons=(Reason.SCREENING_ONLY, Reason.MISSING_INPUT), evidence=(ev_screen,),
                      detail="calibration record not usable (" + "; ".join(cal_problems) + ") - SCREENING - "
                             + screening_detail())
    elif mismatches:
        claim = Claim("conducted_emission", Status.UNKNOWN, q, scope,
                      reasons=(Reason.SCREENING_ONLY, Reason.OUTSIDE_MODEL_DOMAIN), evidence=(ev_screen,),
                      detail="calibration record does not apply to this evaluation (" + "; ".join(mismatches)
                             + ") - SCREENING - " + screening_detail())
    elif not validity["ok"]:
        claim = Claim("conducted_emission", Status.UNKNOWN, q, scope, reasons=(Reason.OUTSIDE_MODEL_DOMAIN,),
                      evidence=(ev_screen,),
                      detail="source outside its validity (" + "; ".join(validity["problems"]) + ")")
    elif not certified:
        claim = Claim("conducted_emission", Status.UNKNOWN, q, scope, reasons=(Reason.NUMERICAL_UNRESOLVED,),
                      detail="band not evaluated exactly: " + (band or {}).get("reason", "no evaluation"))
    else:
        claim_rows = [r for r in rows if r["status"] == "evaluated"]
        req_rows = [r for r in rows if r["requirement"] != "gap"]
        wit = [r for r in claim_rows if r.get("witness")]
        if wit:
            best = max(wit, key=lambda r: r["max_lower_exceedance_dB"])
            w = best["witness"]
            claim = Claim("conducted_emission", Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,),
                          evidence=(Evidence.make(EvidenceKind.NUMERICAL_WITNESS,
                                                  f"receiver at {w['f_Hz'] / 1e6:.6g} MHz: E - U_lower = "
                                                  f"{w['E_lower_dBuV']:.2f} dBuV > L - M_d = "
                                                  f"{w['limit_minus_reserve_dBuV']:.2f} dBuV", **w),
                                    Evidence.make(EvidenceKind.VALIDATED_DOMAIN, cal.evidence)),
                          detail=f"the calibrated LOWER bound exceeds the limit - reserve by "
                                 f"{best['max_lower_exceedance_dB']:.2f} dB at {w['f_Hz'] / 1e6:.6g} MHz")
        elif not req_rows:
            claim = Claim("conducted_emission", Status.UNKNOWN, q, scope, reasons=(Reason.REQUIREMENT_INCOMPLETE,),
                          detail="the band holds no requirement (declared gaps only)")
        elif (len(claim_rows) == len(req_rows)
              and all(r["min_margin_dB"] is not None and r["min_margin_dB"] >= 0 for r in claim_rows)):
            worst = min(claim_rows, key=lambda r: r["min_margin_dB"])
            claim = Claim("conducted_emission", Status.FEASIBLE, q, scope,
                          evidence=(Evidence.make(EvidenceKind.EXACT_ENUMERATION, band["method"],
                                                  lines=band["lines"]),
                                    Evidence.make(EvidenceKind.VALIDATED_DOMAIN, cal.evidence)),
                          qualifiers=("within the declared model, calibration domain and set-up; not a certification "
                                      "pass",),
                          detail=f"certified minimum margin {worst['min_margin_dB']:.2f} dB (E + {U_up:g} dB model "
                                 f"bound) at {worst['f_min_margin_Hz'] / 1e6:.6g} MHz over every receiver frequency")
        else:
            reasons, parts = [], []
            over = [r for r in claim_rows if (r["min_margin_dB"] or 0) < 0]
            if over:
                reasons.append(Reason.UNCERTAINTY_OVERLAP)
                w = min(over, key=lambda r: r["min_margin_dB"])
                parts.append(f"E + U_upper exceeds L - M_d by up to {-w['min_margin_dB']:.2f} dB at "
                             f"{w['f_min_margin_Hz'] / 1e6:.6g} MHz while E - U_lower stays below (no violation "
                             f"witness)")
            outside = [r for r in req_rows if r["status"] != "evaluated"]
            if outside:
                reasons.append(Reason.OUTSIDE_MODEL_DOMAIN)
                parts.append("not evaluated: " + ", ".join(
                    f"{r['lo_Hz'] / 1e6:g}-{r['hi_Hz'] / 1e6:g} MHz ({'; '.join(r['reasons'] + r['claim_reasons'])})"
                    for r in outside))
            claim = Claim("conducted_emission", Status.UNKNOWN, q, scope, reasons=tuple(reasons),
                          evidence=(Evidence.make(EvidenceKind.EXACT_ENUMERATION, band["method"]),),
                          detail="; ".join(parts))
    return {"grid_Hz": grid, "E_dBuV": E, "E_upper_dBuV": EU, "E_lower_dBuV": EL,
            "E_envelope_dBuV": env, "plus_dBuV": _db_or_nan(vals["plus"]), "minus_dBuV": _db_or_nan(vals["minus"]),
            "cm_dBuV": _db_or_nan(vals["cm"]), "dm_dBuV": _db_or_nan(vals["dm"]),
            "from_cm_source_dBuV": _db_or_nan(vals["from_cm_source"]),
            "from_dm_source_dBuV": _db_or_nan(vals["from_dm_source"]), "dominant_source": dom, "limit_dBuV": L,
            "margin_dB": margin, "required_attenuation_dB": A_req, "claim": claim.to_dict(),
            "profile_missing": missing, "rbw_grid_Hz": rbw_g,
            "rbw_Hz": [[lo, None if math.isinf(hi) else hi, r] for lo, hi, r in profile.rbw_segments()],
            "notes": notes, "carrier_ratio": e["carrier_ratio"], "fsw_used_Hz": e["fsw_used_Hz"],
            "fsw_requested_Hz": e["fsw_requested_Hz"], "calibrated": applicable, "domain": rows, "band": band,
            "source_validity": validity, "comparable": {"ok": comparable, "problems": comparable_problems},
            "calibration": {"declared": declared, "problems": cal_problems, "mismatches": mismatches,
                            "applicable": applicable, "U_upper_dB": U_up if applicable else None,
                            "U_lower_dB": U_lo if applicable else None,
                            "record": None if cal is None else cal.to_dict()},
            "configuration": configuration_snapshot(profile, net, src)}


# --------------------------------------------------------------------------------------------- measured trace

def _scallop_dB(step_Hz: float, rbw_Hz: float, shape: str) -> float:
    """Largest loss of a CW line between two readings ``step`` apart (it is at most step / 2 from one of them):
    Gaussian IF of 6-dB bandwidth RBW: 6.02 (step / RBW)^2 dB; rectangular passband RBW wide: 0 when step <= RBW,
    else the line can be missed entirely (infinite)."""
    if step_Hz <= 0:
        return 0.0
    if shape == "gaussian":
        return 6.0206 * (step_Hz / rbw_Hz) ** 2
    return 0.0 if step_Hz <= rbw_Hz * (1.0 + 1e-12) else math.inf


def _trace_meta(meta: dict, profile: EmiProfile, f_in: np.ndarray) -> dict:
    """Comparability of the trace's own acquisition metadata with the profile: which verdicts it can support."""
    probs, pass_block = [], []
    lim = profile.limit
    rep = str(meta.get("representation") or "").strip()
    det = str(meta.get("detector") or "").strip()
    unit = str(meta.get("unit") or "").strip()
    if rep not in TRACE_REPRESENTATIONS:
        probs.append(f"representation must be one of {TRACE_REPRESENTATIONS} (got {rep or 'none'!r})")
    if det not in DETECTORS:
        probs.append(f"trace detector must be one of {DETECTORS} (got {det or 'none'!r})")
    if unit != lim.unit:
        probs.append(f"trace unit {unit or 'none'!r} vs limit unit {lim.unit!r}")
    try:
        segs = _rbw_segments(meta.get("rbw_Hz"), "trace.rbw_Hz")
    except InputValidationError as exc:
        segs = ()
        probs.append(str(exc))
    if not segs:
        probs.append("trace RBW not declared")
    elif f_in.size and not np.allclose(_rbw_at(segs, f_in), profile.rbw_at(f_in), rtol=1e-9, equal_nan=False):
        probs.append("trace RBW differs from the profile RBW")
    for k in TRACE_SETUP_KEYS:
        have, want = str(meta.get(k) or "").strip(), str(profile.fields.get(k) or "").strip()
        if have != want:
            probs.append(f"trace {k} {have or 'none'!r} vs profile {want!r}")
    try:
        dwell = float(meta.get("dwell_s"))
    except (TypeError, ValueError):
        dwell = math.nan
    if not (math.isfinite(dwell) and dwell > 0):
        probs.append("dwell / measurement time not declared")
    if not str(meta.get("corrections") or "").strip():
        probs.append("corrections not declared (AN / probe / cable / limiter factors applied, or why none apply)")
    fail_ok = pass_ok = not probs
    if det in DETECTOR_RANK and lim.detector in DETECTOR_RANK and not probs:
        # peak >= QP >= AV: a reading of a higher-ranked detector below the limit bounds the lower one (PASS);
        # a lower-ranked reading above the limit proves the higher one above it (FAIL)
        pass_ok = DETECTOR_RANK[det] >= DETECTOR_RANK[lim.detector]
        fail_ok = DETECTOR_RANK[det] <= DETECTOR_RANK[lim.detector]
        if not pass_ok:
            pass_block.append(f"{det} readings cannot show compliance with a {lim.detector} limit")
        if not fail_ok:
            pass_block.append(f"{det} readings above a {lim.detector} limit do not prove a {lim.detector} exceedance")
    shape = str(meta.get("if_shape") or "").strip()
    if rep in ("raw_sweep", "max_envelope") and shape not in IF_SHAPES:
        pass_ok = False
        pass_block.append(f"IF shape not declared ({'/'.join(IF_SHAPES)}): the loss between readings is unbounded")
    raw_step = None
    if rep == "max_envelope":
        try:
            raw_step = float(meta.get("raw_step_Hz"))
        except (TypeError, ValueError):
            raw_step = math.nan
        if not (math.isfinite(raw_step) and raw_step > 0):
            pass_ok = False
            pass_block.append("a max-envelope trace needs the step of the raw scan it compresses")
        if not str(meta.get("peak_preservation") or "").strip():
            pass_ok = False
            pass_block.append("a max-envelope trace needs its peak-preservation evidence")
    if rep == "final_list":
        pass_ok = False
        pass_block.append("a final-measurement list does not establish coverage (judge it with the scan that "
                          "selected it)")
    md = profile.fields.get("min_dwell_s")
    if md not in (None, "") and math.isfinite(dwell) and dwell < float(md):
        pass_ok = False
        pass_block.append(f"dwell {dwell:g} s below the required {float(md):g} s")
    return {"problems": probs, "pass_ok": pass_ok, "fail_ok": fail_ok, "pass_block": pass_block,
            "representation": rep, "if_shape": shape, "raw_step_Hz": raw_step, "detector": det, "rbw": segs}


def _trace_coverage(f, x, lim: LimitCurve, profile: EmiProfile, mt: dict, lo: float, hi: float, U: float, M: float):
    """Upper bound of the true level between the readings against L (compliance) and L - M (reserve) over every
    requirement part of [lo, hi]; returns (compliance_ok, reserve_ok, allowance_max, uncovered, min margins)."""
    comp_ok = res_ok = True
    allow_max, uncovered = 0.0, []
    m_comp = m_res = math.inf
    shape, rep = mt["if_shape"], mt["representation"]
    parts = [(a, b) for a, b, k in lim.partition(lo, hi) if k == "covered"]
    undefined = [(a, b) for a, b, k in lim.partition(lo, hi) if k == "undefined"]
    if undefined:
        comp_ok = res_ok = False
        uncovered += [[a, b, "no approved limit (not a declared gap)"] for a, b in undefined]
    for a, b in parts:
        inside = (f >= a) & (f <= b)
        left = np.nonzero(f < a)[0]
        right = np.nonzero(f > b)[0]
        idx = np.concatenate([left[-1:], np.nonzero(inside)[0], right[:1]]).astype(int)
        if idx.size == 0:
            comp_ok = res_ok = False
            uncovered.append([a, b, "no reading"])
            continue
        fs, xs = f[idx], x[idx]
        segs = []                                                    # (lo, hi, bound level, allowance)
        if rep == "max_envelope":
            edges = np.concatenate([[fs[0]], 0.5 * (fs[1:] + fs[:-1]), [fs[-1]]])
            for j in range(fs.size):
                nb = xs[max(0, j - 1): j + 2]
                rbw = float(profile.rbw_at(fs[j])[0])
                al = _scallop_dB(mt["raw_step_Hz"], rbw, shape)
                segs.append((edges[j], edges[j + 1], float(np.max(nb)), al))
            if fs[0] > a or fs[-1] < b:
                comp_ok = res_ok = False
                uncovered.append([a, b, "the envelope does not span the band"])
        else:
            for j in range(fs.size - 1):
                g = fs[j + 1] - fs[j]
                rbw = float(profile.rbw_at(0.5 * (fs[j] + fs[j + 1]))[0])
                segs.append((fs[j], fs[j + 1], float(max(xs[j], xs[j + 1])), _scallop_dB(g, rbw, shape)))
            if fs[0] > a:
                rbw = float(profile.rbw_at(fs[0])[0])
                segs.append((a, fs[0], float(xs[0]), _scallop_dB(2.0 * (fs[0] - a), rbw, shape)))
            if fs[-1] < b:
                rbw = float(profile.rbw_at(fs[-1])[0])
                segs.append((fs[-1], b, float(xs[-1]), _scallop_dB(2.0 * (b - fs[-1]), rbw, shape)))
        for s0, s1, lvl, al in segs:
            c0, c1 = max(s0, a), min(s1, b)
            if c1 < c0:
                continue
            Lmin = lim.min_over(c0, c1)
            allow_max = max(allow_max, al)
            bound = lvl + al + U
            if not math.isfinite(bound):
                comp_ok = res_ok = False
                uncovered.append([c0, c1, "a line between the readings can be missed (step > RBW)"])
                continue
            m_comp = min(m_comp, Lmin - bound)
            m_res = min(m_res, Lmin - M - bound)
            if bound > Lmin:
                comp_ok = False
            if bound > Lmin - M:
                res_ok = False
    return comp_ok, res_ok, allow_max, uncovered, m_comp, m_res


def measured_trace_verdict(f_Hz, level, profile: EmiProfile, U_meas_dB: float, noise_floor=None,
                           band: tuple | None = None, meta: dict | None = None) -> dict:
    """A measured receiver trace against the SAME approved profile, with the trace's OWN acquisition metadata
    (``meta``: representation raw_sweep | max_envelope | final_list, detector, unit, rbw_Hz, if_shape, dwell_s,
    corrections, port / method / network / fixture / operating_condition, raw_step_Hz and peak_preservation for an
    envelope).

    Compliance (against L): FAIL when a reading exceeds L by more than U (a witness at that frequency); PASS when the
    upper bound of the true level - reading + U + the loss between readings for the declared IF shape and step -
    stays below L over every requirement part of the band.  Reserve (against L - M_d) likewise.  PASS overall needs
    both; anything else is INDETERMINATE.  Representativeness of the test and approval stay separate."""
    f = np.asarray(f_Hz, dtype=float)
    x = np.asarray(level, dtype=float)
    if f.ndim != 1 or f.size < 2 or f.size != x.size or not np.all(np.isfinite(f)) or np.any(np.diff(f) <= 0):
        raise InputValidationError("trace needs finite, strictly increasing frequencies and one level per frequency",
                                   field="trace")
    try:
        U = float(U_meas_dB)
    except (TypeError, ValueError):
        raise InputValidationError("measurement uncertainty U must be a number", field="U_meas_dB") from None
    if not math.isfinite(U) or U < 0:
        raise InputValidationError("measurement uncertainty U must be finite and >= 0 dB", field="U_meas_dB")
    nf = None if noise_floor is None else np.asarray(noise_floor, dtype=float)
    if nf is not None and nf.shape != f.shape:
        raise InputValidationError("noise floor needs one value per frequency", field="noise_floor")
    meta = dict(meta or {})
    rule = profile.decision_rule()
    decision = {"rule": rule or "workbench default (guarded): PASS needs reading + U + between-reading loss <= L - "
                                "M_d everywhere, FAIL needs reading - U > L somewhere",
                "agreed": bool(rule), "uncertainty": "applied against acceptance and rejection"}
    base = {"U_meas_dB": U, "meta": meta, "decision_rule": decision,
            "note": "a measured-trace verdict for this profile and set-up only; test representativeness and approval "
                    "are separate"}
    missing = profile.missing()
    if missing:
        return {**base, "verdict": "INDETERMINATE", "reason": "REQUIREMENT_INCOMPLETE: " + ", ".join(missing),
                "compliance": {"verdict": "INDETERMINATE"}, "reserve": {"verdict": "INDETERMINATE"},
                "margin_dB": [], "limit": [], "points_in_band": 0}
    lim = profile.limit
    lo, hi = (float(band[0]), float(band[1])) if band else lim.span
    if not (math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi):
        raise InputValidationError("trace band needs finite 0 < lo < hi", field="band")
    L = lim.at(f)
    M = profile.design_reserve_dB
    fin = np.isfinite(x)
    inb = (f >= lo) & (f <= hi)
    req = inb & np.isfinite(L) & fin
    mt = _trace_meta(meta, profile, f[inb])
    reasons = list(mt["problems"])
    witness = None
    over = req & (x - U > L)
    over_res = req & (x - U > L - M)
    comp, res = "INDETERMINATE", "INDETERMINATE"
    if mt["fail_ok"] and over.any():
        j = int(np.argmax(np.where(over, x - U - L, -np.inf)))
        witness = {"f_Hz": float(f[j]), "level_dB": float(x[j]), "limit_dB": float(L[j]),
                   "exceedance_dB": float(x[j] - U - L[j])}
        comp = "FAIL"
    if mt["fail_ok"] and over_res.any():
        res = "NOT_MET"
    allow, uncovered, mc, mr = None, [], None, None
    if not np.any(inb & fin):
        reasons.append("no finite reading inside the band")
    elif mt["pass_ok"] and comp != "FAIL":
        c_ok, r_ok, allow, uncovered, mc, mr = _trace_coverage(f[fin], x[fin], lim, profile, mt, lo, hi, U, M)
        floor_bad = nf is not None and bool(np.any(inb & np.isfinite(L) & (nf >= L - M)))
        if floor_bad:
            reasons.append("noise floor not below limit - reserve")
        if c_ok and not floor_bad:
            comp = "PASS"
        if r_ok and not floor_bad and res != "NOT_MET":
            res = "MET"
        if uncovered:
            shown = "; ".join(f"{a / 1e6:.4g}-{b / 1e6:.4g} MHz {why}" for a, b, why in uncovered[:3])
            more = f" (+{len(uncovered) - 3} more)" if len(uncovered) > 3 else ""
            reasons.append(f"coverage: {len(uncovered)} uncovered part(s): {shown}{more}")
        if not c_ok and not uncovered:
            reasons.append(f"the level between readings is bounded only to reading + U + {allow:.3g} dB (loss "
                           f"between readings for the declared IF shape and step) - above the limit")
        if c_ok and not r_ok and not uncovered:
            reasons.append("points within the uncertainty / reserve band")
    else:
        reasons += mt["pass_block"]
    n_bad = int(np.count_nonzero(inb & ~fin))
    if n_bad:
        reasons.append(f"{n_bad} non-finite reading(s) treated as unmeasured")
    if comp == "FAIL":
        v, why = "FAIL", f"{int(over.sum())} point(s) above the limit by more than U = {U:g} dB (e.g. " \
                         f"{witness['exceedance_dB']:.2f} dB at {witness['f_Hz'] / 1e6:.6g} MHz)"
    elif comp == "PASS" and res == "MET":
        v, why = "PASS", (f"every requirement frequency below limit - {M:g} dB reserve - {U:g} dB incl. up to "
                          f"{allow:.2f} dB between readings ({mt['representation']}, {mt['if_shape']} IF)")
    else:
        v, why = "INDETERMINATE", "; ".join(dict.fromkeys(reasons)) or "reserve not established"
    return {**base, "verdict": v, "reason": why,
            "compliance": {"verdict": comp, "margin_dB": float(np.nanmin(np.where(req, L - x, np.nan)))
                           if req.any() else None},
            "reserve": {"verdict": res, "margin_dB": float(np.nanmin(np.where(req, L - M - x, np.nan)))
                        if req.any() else None},
            "witness": witness, "coverage": {"representation": mt["representation"], "if_shape": mt["if_shape"],
                                             "allowance_max_dB": allow, "uncovered": uncovered,
                                             "bound_margin_compliance_dB": None if mc is None or math.isinf(mc) else mc,
                                             "bound_margin_reserve_dB": None if mr is None or math.isinf(mr) else mr,
                                             "max_step_Hz": float(np.max(np.diff(f[inb & fin])))
                                             if np.count_nonzero(inb & fin) > 1 else None},
            "meta_problems": mt["problems"], "margin_dB": (L - M - x).tolist(), "limit": L.tolist(),
            "points_in_band": int((inb & fin).sum())}


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
            lines[tag] = edge_lines(t0, np.full(t0.size, tau), sg * V / 3.0, freqs, T)
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
                cur[tag] = edge_lines(t0, np.full(t0.size, tau), sg * sgn_i * ik, freqs, T)
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


_edge_lines = edge_lines          # former private name (compatibility)
