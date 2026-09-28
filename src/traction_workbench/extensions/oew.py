"""Open-end winding (OEW) dual-inverter drive: topology identity, voltage sets, dq0 power and port accounting.

Scope (OEW/HEV addendum sections 4-5, first support domain P1-O): two ideal two-level bridges A and B drive the two
ends of ONE three-phase winding (six terminals).  Two DC topologies, each from its own circuit equations - never by
scaling the single-VSI budget (``diagnostic_budget_scale`` or a Vdc multiple):

* ``common_bus``: both bridges on the same + / - rails (delta = v_NA - v_NB = 0).  The winding zero sequence
  u0 = (VA sum sA - VB sum sB) / 3 has a conductive return, so i0 flows whenever u0 differs from the zero-sequence
  back-EMF; the zero-sequence command shares the per-phase range |u_k| <= V with the fundamental.
* ``isolated``: two galvanically separated sources with no low-frequency return between them; island KCL forces
  ia + ib + ic = 0 and the floating offset delta takes up the zero sequence (i0 = 0 does not mean v0 = 0).
  High-frequency chassis paths are outside this model (no EMC claim).

Conventions (kept in every output): phase current positive A -> winding -> B; u_k = v_Ak - v_Bk; amplitude-invariant
Clarke / Park with x0 = (xa + xb + xc) / 3; winding power p = 3/2 (vd id + vq iq) + 3 v0 i0; copper loss
3/2 Rs (id^2 + iq^2) + 3 R0 i0^2; DC currents positive source -> bridge: I_dc,A = sA^T i, I_dc,B = -sB^T i.
Bridge B carries -i: the torque current is NOT split between the bridges, both carry the full winding current.

The machine-side dq quantities come from the validated dq model of the drive (its i0 ~ 0 region); the zero-sequence
inductance and the triplen PM flux must be declared separately and are never assumed zero.  Everything here is an
ideal-switch average model at a static / periodic steady state: switching ripple (see
``zero_sequence_switching_ripple``), dead time, minimum pulse, chassis common mode, insulation stress and EMC are
outside it.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np

from ..errors import InputValidationError, OutsideModelDomain
from ..models.components import DriveModel
from ..models.module_loss import leg_losses_trajectory, positions
from ..validation import finite as _finite
from ..physics import DriveKernel
from ..scenario import DcSourceLimits, Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

SQRT3 = math.sqrt(3.0)
TWO_PI = 2.0 * math.pi
TOPOLOGIES = ("common_bus", "isolated")
ZS_POLICIES = ("regulate_i0", "no_compensation")
BRIDGE_STATES = ("pwm", "asc_top", "asc_bottom", "off")


# --------------------------------------------------------------------------------------------- transforms

def clarke(a, b, c):
    """Amplitude-invariant Clarke with zero sequence: (alpha, beta, 0) where x0 = (a + b + c) / 3."""
    a, b, c = (np.asarray(x, dtype=float) for x in (a, b, c))
    return (2.0 / 3.0) * (a - 0.5 * (b + c)), (b - c) / SQRT3, (a + b + c) / 3.0


def dq0_to_abc(d, q, z, theta):
    """Inverse amplitude-invariant Park: x_k = d cos(th_k) - q sin(th_k) + z, th_k = theta - 2 pi k / 3."""
    out = []
    for k in range(3):
        th = np.asarray(theta, dtype=float) - TWO_PI * k / 3.0
        out.append(np.asarray(d) * np.cos(th) - np.asarray(q) * np.sin(th) + np.asarray(z))
    return tuple(out)


def abc_to_dq0(a, b, c, theta):
    th = np.asarray(theta, dtype=float)
    a, b, c = (np.asarray(x, dtype=float) for x in (a, b, c))
    d = (2.0 / 3.0) * (a * np.cos(th) + b * np.cos(th - TWO_PI / 3) + c * np.cos(th + TWO_PI / 3))
    q = -(2.0 / 3.0) * (a * np.sin(th) + b * np.sin(th - TWO_PI / 3) + c * np.sin(th + TWO_PI / 3))
    return d, q, (a + b + c) / 3.0


def winding_power_dq0(vd, vq, v0, id_, iq, i0):
    """p = 3/2 (vd id + vq iq) + 3 v0 i0 (amplitude-invariant; the factor 3 on the zero sequence is not 3/2)."""
    return 1.5 * (np.asarray(vd) * id_ + np.asarray(vq) * iq) + 3.0 * np.asarray(v0) * i0


def abc_waveform_metrics(id_A: float, iq_A: float, i0_A, Rs_ohm: float, n: int = 3600) -> dict:
    """Phase peak / RMS and copper loss from the abc reconstruction versus the dq-only values.

    ``i0_A``: a constant or a callable of the electrical angle.  The dq norm is not the phase peak once i0 != 0,
    and 3 Rs <i0^2> is not zero even when <i0> = 0.
    """
    th = (np.arange(n) + 0.5) * TWO_PI / n
    i0 = i0_A(th) if callable(i0_A) else np.full(n, float(i0_A))
    ia, ib, ic = dq0_to_abc(id_A, iq_A, i0, th)
    ph = np.vstack([ia, ib, ic])
    rms = np.sqrt(np.mean(ph ** 2, axis=1))
    # the peak is evaluated on the grid plus the analytic peak angles of the fundamental (a sampled grid alone misses
    # a peak that falls between samples)
    b = math.atan2(iq_A, id_A)
    extra = np.array([(-b + TWO_PI * k / 3.0 + m * math.pi) % TWO_PI for k in range(3) for m in (0, 1)])
    i0x = i0_A(extra) if callable(i0_A) else np.full(extra.size, float(i0_A))
    px = np.vstack(dq0_to_abc(id_A, iq_A, i0x, extra))
    peak = max(float(np.max(np.abs(ph))), float(np.max(np.abs(px))))
    p_abc = float(np.mean(Rs_ohm * np.sum(ph ** 2, axis=0)))
    p_dq = 1.5 * Rs_ohm * (id_A ** 2 + iq_A ** 2)
    return {"phase_peak_A": peak, "phase_rms_A": rms.tolist(), "dq_norm_A": math.hypot(id_A, iq_A),
            "i0_mean_A": float(np.mean(i0)), "i0_rms_A": float(np.sqrt(np.mean(i0 ** 2))),
            "copper_abc_W": p_abc, "copper_dq_only_W": p_dq, "copper_missed_by_dq_W": p_abc - p_dq,
            "copper_zero_sequence_W": 3.0 * Rs_ohm * float(np.mean(i0 ** 2))}


# --------------------------------------------------------------------------------------------- data

@dataclass(frozen=True)
class ZeroSequenceModel:
    """Screening zero-sequence model psi0 = L0 i0 + psi_PM,0(theta_e); L0 is measured / derived, never Ld or Lq.

    ``psi0_harmonics``: ((order n, amplitude Wb, phase rad), ...) of psi_PM,0 in the electrical angle; the orders are
    triplen (3, 9, 15, ...).  ``R0_ohm``: zero-sequence resistance (default: Rs).
    """

    L0_H: float
    psi0_harmonics: tuple = ()
    R0_ohm: float | None = None
    basis: str = ""

    def __post_init__(self):
        l0 = _finite("L0_H", self.L0_H)
        if l0 <= 0:
            raise InputValidationError("L0 must be > 0 (a measured / derived zero-sequence inductance)", field="L0_H")
        if not self.basis.strip():
            raise InputValidationError("the zero-sequence data need their basis (measurement / FEA report)",
                                       field="zero_sequence.basis")
        harm = []
        for h in self.psi0_harmonics:
            n, amp, ph = int(h[0]), _finite("psi0 amplitude", h[1]), _finite("psi0 phase", h[2] if len(h) > 2 else 0.0)
            if n <= 0 or n % 3:
                raise InputValidationError(f"zero-sequence PM flux harmonic order {n} is not triplen (3, 9, ...)",
                                           field="zero_sequence.psi0_harmonics")
            harm.append((n, amp, ph))
        object.__setattr__(self, "L0_H", l0)
        object.__setattr__(self, "psi0_harmonics", tuple(harm))
        if self.R0_ohm is not None:
            r0 = _finite("R0_ohm", self.R0_ohm)
            if r0 < 0:
                raise InputValidationError("R0 must be >= 0", field="R0_ohm")
            object.__setattr__(self, "R0_ohm", r0)

    def psi0(self, theta):
        th = np.asarray(theta, dtype=float)
        return sum(a * np.cos(n * th + ph) for n, a, ph in self.psi0_harmonics) + 0.0 * th

    def dpsi0(self, theta):
        th = np.asarray(theta, dtype=float)
        return sum(-n * a * np.sin(n * th + ph) for n, a, ph in self.psi0_harmonics) + 0.0 * th

    def describe(self) -> dict:
        return {"L0_H": self.L0_H, "psi0_harmonics": [list(h) for h in self.psi0_harmonics], "R0_ohm": self.R0_ohm,
                "basis": self.basis, "model": "psi0 = L0 i0 + psi_PM,0(theta_e) (screening; no cross-saturation)"}


@dataclass(frozen=True)
class OewTopology:
    kind: str                                   # common_bus | isolated
    VA_V: float
    VB_V: float | None = None                   # isolated: source B; common_bus: None or equal to VA
    zero_sequence: ZeroSequenceModel | None = None
    zs_policy: str = "regulate_i0"              # common bus: regulate_i0 | no_compensation
    power_split_A: float | None = None          # share s of the winding voltage (and power) from bridge A
    limits_A: DcSourceLimits | None = None      # common bus: the one shared source; isolated: source A
    limits_B: DcSourceLimits | None = None      # isolated only
    bridge_current_limit_A: float | None = None  # per bridge; default: the drive's inverter limit (never doubled)
    reserve_fraction: float | None = None       # default: the drive's inverter voltage reserve
    revision: str = ""
    basis: str = ""                             # circuit / schematic reference

    def __post_init__(self):
        if self.kind not in TOPOLOGIES:
            raise InputValidationError(f"OEW topology must be one of {TOPOLOGIES} (split / floating-capacitor / "
                                       f"multilevel / CEW switching need their own circuit contract)", field="kind")
        va = _finite("VA_V", self.VA_V)
        if va <= 0:
            raise InputValidationError("VA must be > 0", field="VA_V")
        object.__setattr__(self, "VA_V", va)
        if self.kind == "common_bus":
            if self.VB_V is not None and abs(float(self.VB_V) - va) > 1e-9 * max(1.0, va):
                raise InputValidationError(
                    "a common bus has one DC voltage for both bridges; an asymmetric supply needs its circuit declared "
                    "(tap, DC-DC or isolated source) - two capacitors do not mean galvanic isolation", field="VB_V")
            object.__setattr__(self, "VB_V", va)
            if self.limits_B is not None:
                raise InputValidationError("a common bus has one source: declare its limits once (limits_A); the same "
                                           "battery cap is never applied to each port separately", field="limits_B")
            if self.zs_policy not in ZS_POLICIES:
                raise InputValidationError(f"zero-sequence policy must be one of {ZS_POLICIES}", field="zs_policy")
        else:
            if self.VB_V is None:
                raise InputValidationError("an isolated OEW needs the second source voltage VB", field="VB_V")
            vb = _finite("VB_V", self.VB_V)
            if vb <= 0:
                raise InputValidationError("VB must be > 0", field="VB_V")
            object.__setattr__(self, "VB_V", vb)
            object.__setattr__(self, "zs_policy", "island_kcl")
        if self.power_split_A is not None:
            object.__setattr__(self, "power_split_A", _finite("power_split_A", self.power_split_A))
        for name in ("bridge_current_limit_A", "reserve_fraction"):
            v = getattr(self, name)
            if v is not None:
                object.__setattr__(self, name, _finite(name, v))
        if self.reserve_fraction is not None and not (0.0 <= self.reserve_fraction < 1.0):
            raise InputValidationError("voltage reserve must satisfy 0 <= r_v < 1", field="reserve_fraction")
        if self.bridge_current_limit_A is not None and self.bridge_current_limit_A <= 0:
            raise InputValidationError("bridge current limit must be > 0", field="bridge_current_limit_A")

    @property
    def split(self) -> float:
        if self.power_split_A is not None:
            return self.power_split_A
        return 0.5 if self.kind == "common_bus" else self.VA_V / (self.VA_V + self.VB_V)

    def describe(self) -> dict:
        return {"kind": self.kind, "VA_V": self.VA_V, "VB_V": self.VB_V, "zs_policy": self.zs_policy,
                "power_split_A": self.split, "split_declared": self.power_split_A is not None,
                "zero_sequence": None if self.zero_sequence is None else self.zero_sequence.describe(),
                "bridge_current_limit_A": self.bridge_current_limit_A, "reserve_fraction": self.reserve_fraction,
                "revision": self.revision, "basis": self.basis,
                "conventions": "i positive A->winding->B; u = vA - vB; amplitude-invariant dq0 with x0 = sum/3; "
                               "p = 3/2 (vd id + vq iq) + 3 v0 i0; I_dc positive source->bridge"}


# --------------------------------------------------------------------------------------------- switch-state geometry

def hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _radii(points) -> tuple[float, float]:
    """(circumradius, inradius about the origin) of the convex hull of alpha-beta points."""
    h = hull(points)
    rc = max(math.hypot(x, y) for x, y in h)
    ri = math.inf
    for (x1, y1), (x2, y2) in zip(h, h[1:] + h[:1]):
        L = math.hypot(x2 - x1, y2 - y1)
        if L > 0:
            ri = min(ri, abs(x1 * y2 - x2 * y1) / L)
    return rc, ri


def switch_state_geometry(VA_V: float, VB_V: float | None = None, kind: str = "common_bus", decimals: int = 9) -> dict:
    """All 64 ideal state pairs (sA, sB): winding alpha-beta vectors, zero sequence and the admissible hull.

    common_bus: only the pairs with u0 = 0 (sum sA = sum sB for equal voltages) are admissible without driving
    zero-sequence current; the hull of the full projection (2V/sqrt3 inradius) hides a v0 that is not allowed.
    isolated: the floating offset absorbs u0; every pair is admissible at low frequency.
    """
    VB_V = VA_V if VB_V is None else VB_V
    rows, pts_all, pts_adm = [], [], []
    for sa in itertools.product((0, 1), repeat=3):
        for sb in itertools.product((0, 1), repeat=3):
            u = [VA_V * x - VB_V * y for x, y in zip(sa, sb)]
            al, be, z = (float(v) for v in clarke(*u))
            v0 = z if kind == "common_bus" else None
            adm = (abs(z) <= 1e-12 * max(VA_V, VB_V)) if kind == "common_bus" else True
            key = (round(al, decimals), round(be, decimals))
            rows.append({"sA": "".join(map(str, sa)), "sB": "".join(map(str, sb)), "u_V": u, "alpha_V": al,
                         "beta_V": be, "u0_V": v0, "admissible": adm})
            pts_all.append(key)
            if adm:
                pts_adm.append(key)
    rc_all, ri_all = _radii(pts_all)
    rc_adm, ri_adm = _radii(pts_adm)
    out = {"kind": kind, "VA_V": VA_V, "VB_V": VB_V, "pairs": len(rows), "unique_alphabeta": len(set(pts_all)),
           "admissible_pairs": len(pts_adm), "admissible_unique_alphabeta": len(set(pts_adm)),
           "hull_circumradius_V": rc_adm, "hull_inradius_V": ri_adm,
           "unconstrained_hull_inradius_V": ri_all, "unconstrained_hull_circumradius_V": rc_all,
           "single_vsi_inradius_V": VA_V / SQRT3,
           "admissible_points": sorted(set(pts_adm)), "all_points": sorted(set(pts_all)), "rows": rows,
           "meaning": ("guaranteed phase-fundamental peak in every direction (linear average, ideal switches, no reserve, "
                       "dead time or drop) = hull inradius")}
    if kind == "common_bus":
        out["note"] = ("common bus: u0 = (VA sum sA - VB sum sB)/3 drives zero-sequence current; the zero-u0 subset "
                       "(20 pairs, 7 alpha-beta points for equal voltages) gives V, not 2V/sqrt3 - the gain over a "
                       "single VSI is sqrt3, not 2")
    else:
        out["note"] = "isolated: the floating offset takes up u0 (island KCL); ideal envelope (VA + VB)/sqrt3"
    return out


def b_clamp_vs_floating_star(V: float, sA: str = "100") -> dict:
    """A common-bus bridge B held at 000 ties the winding ends to DC-: not a floating star (O-04)."""
    s = [int(ch) for ch in sA]
    clamp = [V * x for x in s]
    mean = sum(clamp) / 3.0
    star = [v - mean for v in clamp]
    return {"sA": sA, "B_000_common_bus_V": clamp, "B_000_u0_V": mean, "floating_star_V": star, "floating_star_u0_V": 0.0,
            "note": "B = 000 on a common bus connects the winding ends to DC-; a CEW (floating-star) result for the "
                    "same A state differs - a mode is judged from its connection graph, not its name"}


# --------------------------------------------------------------------------------------------- voltage allocation

def _allocation_numerators(u, s, u0, VA, VB, kind):
    """Per-sample constraint numerators whose value must stay <= the matching voltage (see module docstring)."""
    a = s * u
    b = -(1.0 - s) * u
    amax, amin = a.max(axis=0), a.min(axis=0)
    bmax, bmin = b.max(axis=0), b.min(axis=0)
    if kind == "isolated":
        return {"spread_A": (amax - amin, VA), "spread_B": (bmax - bmin, VB)}
    return {"spread_A": (amax - amin, VA), "spread_B": (bmax - bmin, VB),
            "upper_offset": (amax - bmin + u0, VA), "lower_offset": (bmax - amin - u0, VA)}


def voltage_allocation(U_pk: float, alpha_rad: float, topo: OewTopology, reserve: float, u0_cmd=None,
                       dpsi_bound: float = 0.0, n: int = 2048) -> dict:
    """Can the pair realise the winding voltage u_k = U cos(theta + alpha - 2 pi k/3) (+ u0*) at every angle?

    Policy: co-linear split (bridge A supplies s u, bridge B -(1 - s) u) with free common-mode offsets; on a common
    bus the offsets must differ by the zero-sequence command u0*(theta).  The angle is sampled on n points and a
    Lipschitz bound turns the sampled maximum into a statement over the continuum:
    FEASIBLE if max + L h/2 <= limit, INFEASIBLE (witness angle, this policy) if a sample exceeds the limit, else
    UNKNOWN.  ``dpsi_bound``: bound on |d u0*/d theta|.
    """
    s = topo.split
    VA = (1.0 - reserve) * topo.VA_V
    VB = (1.0 - reserve) * topo.VB_V
    th = (np.arange(n) + 0.5) * TWO_PI / n
    u = np.vstack([U_pk * np.cos(th + alpha_rad - TWO_PI * k / 3.0) for k in range(3)])
    u0 = np.zeros(n) if u0_cmd is None else np.asarray(u0_cmd(th), dtype=float)
    if topo.kind == "isolated":
        u0 = np.zeros(n)
    nums = _allocation_numerators(u, s, u0, VA, VB, topo.kind)
    L = max(2 * abs(s) * U_pk, 2 * abs(1 - s) * U_pk, (abs(s) + abs(1 - s)) * U_pk + dpsi_bound)
    h = TWO_PI / n
    margin = L * h / 2.0
    worst, util, binding, theta_w = -math.inf, 0.0, None, None
    for name, (num, lim) in nums.items():
        j = int(np.argmax(num))
        r = float(num[j]) / lim
        if r > util:
            util, binding, theta_w = r, name, float(th[j])
        worst = max(worst, float(num[j]) - lim)
    lim_min = min(lim for _, lim in nums.values())
    if worst > 0:
        st = Status.INFEASIBLE
    elif worst + margin <= 0:
        st = Status.FEASIBLE
    else:
        st = Status.UNKNOWN
    return {"status": st.value, "utilisation": util, "binding": binding, "worst_theta_deg": math.degrees(theta_w or 0.0),
            "worst_excess_V": worst, "lipschitz_margin_V": margin, "samples": n, "split_A": s,
            "limits_V": {"A": VA, "B": VB}, "policy": (f"co-linear split s = {s:.4g}; "
                                                      + ("common-mode offsets differ by u0*" if topo.kind == "common_bus"
                                                         else "independent offsets (floating delta)")),
            "closed_form_limit_V": _closed_form_limit(topo, reserve) if u0_cmd is None else None,
            "limit_min_V": lim_min}


def _closed_form_limit(topo: OewTopology, reserve: float) -> float:
    """Balanced fundamental without zero-sequence command: phase-peak limit of the co-linear split policy."""
    s = topo.split
    VA, VB = (1.0 - reserve) * topo.VA_V, (1.0 - reserve) * topo.VB_V
    if topo.kind == "isolated":
        cands = []
        if s != 0:
            cands.append(VA / (SQRT3 * abs(s)))
        if s != 1:
            cands.append(VB / (SQRT3 * abs(1 - s)))
        return min(cands)
    if 0.0 <= s <= 1.0:
        return min(VA, VA / (SQRT3 * max(s, 1 - s)) if max(s, 1 - s) > 0 else math.inf)
    return math.nan        # circulation (s outside [0, 1]): use the sampled check


# --------------------------------------------------------------------------------------------- operating point

def _check_drive_for_oew(drive: DriveModel):
    if drive.motor.connection != "wye":
        raise InputValidationError(
            "an OEW drive needs the phase-winding parameters of the winding that is opened; a wye-equivalent of "
            f"another connection ({drive.motor.connection}: {drive.motor.connection_note}) cannot be opened without a "
            "declared winding mapping", field="motor.connection")


def _i0_trajectory(zs: ZeroSequenceModel, omega_e: float, Rs: float, th: np.ndarray):
    """Periodic i0 for u0 = 0 (no compensation): L0 di0/dt + R0 i0 = -e0, e0 = omega_e dpsi0/dtheta."""
    R0 = Rs if zs.R0_ohm is None else zs.R0_ohm
    i0 = np.zeros_like(th)
    for n, amp, ph in zs.psi0_harmonics:
        E = 1j * n * omega_e * amp * np.exp(1j * ph)          # e0_n(theta) = Re{E e^{j n theta}}
        Z = R0 + 1j * n * omega_e * zs.L0_H
        if abs(Z) == 0:
            continue
        I = -E / Z
        i0 = i0 + np.real(I * np.exp(1j * n * th))
    return i0, R0


def _bridge_losses(drive: DriveModel, legs_i: np.ndarray, duties: np.ndarray, V: float, i_dq_peak: float):
    """Per-bridge semiconductor loss: datasheet module model on the actual duty/current trajectories, else the
    quadratic surrogate per bridge as a screening value (not established)."""
    inv = drive.inverter
    if inv.module_loss is not None:
        mdl = inv.module_loss
        tot, problems, pos_all = 0.0, [], {}
        cond = sw = 0.0
        for k in range(3):
            leg = leg_losses_trajectory(mdl, legs_i[k] / mdl.parallel, duties[k], V, inv.module_Tj_C)
            problems += [f"leg {'abc'[k]}: {p}" for p in leg["problems"]]
            pos = positions(leg)
            tot += mdl.parallel * sum(pos.values())
            cond += mdl.parallel * sum(leg["conduction_W"].values())
            sw += mdl.parallel * sum(leg["switching_W"].values())
            for name, v in pos.items():
                pos_all[f"{'abc'[k]}_{name}"] = v
        hot = max(pos_all, key=lambda x: -1 if math.isnan(pos_all[x]) else pos_all[x])
        est = not problems
        aux = mdl.driver_aux_W if mdl.aux_from_hv_dc else 0.0
        return {"model": "datasheet module (duty/current trajectory of this bridge)", "established": est,
                "semiconductor_W": tot if est else None, "conduction_W": cond if est else None,
                "switching_W": sw if est else None, "dc_side_W": (tot + aux) if est else None,
                "hottest_position": hot, "hottest_position_W": pos_all[hot] if est else None,
                "problems": problems, "Tj_eval_C": inv.module_Tj_C}
    if inv.loss is not None:
        val = inv.loss.loss(i_dq_peak ** 2)
        return {"model": "quadratic surrogate a0 + a2 I^2 per bridge (declared for the single VSI; screening)",
                "established": False, "semiconductor_W": None, "screening_W": float(val), "dc_side_W": None,
                "problems": ["per-bridge OEW losses need the datasheet module model (the surrogate has no duty / "
                             "modulation dependence and was declared for the single VSI)"]}
    return {"model": None, "established": False, "semiconductor_W": None, "dc_side_W": None,
            "problems": ["no inverter loss model: DC power undefined (a missing loss is never zero)"]}


def _port_claim(name: str, p_dc: float | None, V: float, lim: DcSourceLimits | None, tol: float) -> tuple[Claim, dict]:
    q = f"{name}: DC power / current within the declared source limits"
    info = {"P_dc_W": p_dc, "I_dc_A": None if p_dc is None else p_dc / V, "V_V": V}
    if p_dc is None:
        return Claim(name, Status.UNKNOWN, q, "OEW port accounting", reasons=(Reason.MISSING_INPUT,),
                     detail="port DC power undefined (bridge losses not established)"), info
    if lim is None:
        return Claim(name, Status.UNKNOWN, q, "OEW port accounting", reasons=(Reason.MISSING_INPUT,),
                     detail="no source limits declared for this port"), info
    i_dc = p_dc / V
    checks, missing = [], []
    if p_dc > tol:
        pairs = (("discharge power", lim.discharge_power_max_W, p_dc), ("discharge current", lim.discharge_current_max_A, i_dc))
    elif p_dc < -tol:
        pairs = (("charge power", lim.charge_power_max_W, -p_dc), ("charge current", lim.charge_current_max_A, -i_dc))
    else:
        pairs = ()
    for label, cap, val in pairs:
        if cap is None:
            missing.append(label)
        elif not math.isinf(cap):
            checks.append((label, val, cap))
    bad = [c for c in checks if c[1] > c[2] * (1 + 1e-9) + 1e-9]
    info["checks"] = [{"limit": c[0], "value": c[1], "cap": c[2]} for c in checks]
    if bad:
        return Claim(name, Status.INFEASIBLE, q, "OEW port accounting", reasons=(Reason.CONSTRAINT_VIOLATION,),
                     detail="; ".join(f"{c[0]} {c[1]:.6g} > {c[2]:.6g}" for c in bad)), info
    if missing:
        return Claim(name, Status.UNKNOWN, q, "OEW port accounting", reasons=(Reason.MISSING_INPUT,),
                     detail="limit(s) that can bind are not declared: " + ", ".join(missing)
                            + " (a missing limit is not 'unlimited'; declare math.inf for no limit)"), info
    return Claim(name, Status.FEASIBLE, q, "OEW port accounting",
                 detail="within " + (", ".join(f"{c[0]} {c[1]:.4g}/{c[2]:.4g}" for c in checks) or "declared limits")), info


def oew_point(drive: DriveModel, topo: OewTopology, speed_rpm: float, id_A: float, iq_A: float,
              T_request_Nm: float | None = None, magnet_temp_C: float | None = None,
              winding_temp_C: float | None = None, settings: NumericalSettings = DEFAULT_SETTINGS,
              n_theta: int = 720, waveforms: bool = True) -> dict:
    """Forward evaluation of one OEW operating point: allocation, currents incl. i0, copper, torque, bridge losses,
    port powers and the claims (each with its evidence level)."""
    _check_drive_for_oew(drive)
    sc = Scenario("oew", speed_rpm, topo.VA_V, DcSourceLimits(), winding_temp_C=winding_temp_C,
                  magnet_temp_C=magnet_temp_C)
    k = DriveKernel(drive, sc, settings)
    if not k.evaluable:
        raise OutsideModelDomain("model not evaluable for this scenario",
                                 detail={"issues": [i.to_dict() for i in k.issues]})
    ev = k.evaluate(np.array([float(id_A)]), np.array([float(iq_A)]))
    if not bool(ev["ok"][0]):
        raise OutsideModelDomain(f"(id={id_A:g} A, iq={iq_A:g} A) is outside the model's covered/valid domain; "
                                 f"no extrapolation", detail={"id_A": id_A, "iq_A": iq_A})
    f = {key: float(np.asarray(v)[0]) for key, v in ev.items() if key != "ok"}
    we, wm, p = k.omega_e, k.omega_m, k.p
    reserve = drive.inverter.voltage.reserve_fraction if topo.reserve_fraction is None else topo.reserve_fraction
    Rdrop = drive.inverter.voltage.resistive_drop_ohm
    I = math.hypot(id_A, iq_A)
    beta = math.atan2(iq_A, id_A) if I > 0 else 0.0
    U = math.hypot(f["vd"], f["vq"])
    alpha = math.atan2(f["vq"], f["vd"]) if U > 0 else 0.0
    ucd, ucq = f["vd"] + 2.0 * Rdrop * id_A, f["vq"] + 2.0 * Rdrop * iq_A     # both bridges' drops act on the winding
    Uc = math.hypot(ucd, ucq)
    alpha_c = math.atan2(ucq, ucd) if Uc > 0 else 0.0
    th = (np.arange(n_theta) + 0.5) * TWO_PI / n_theta
    zs = topo.zero_sequence
    notes, reasons_cond = [], []
    # ---------------- zero sequence
    i0 = np.zeros(n_theta)
    u0_fn, dpsi_bound, zs_info = None, 0.0, {}
    R0 = k.Rs
    if topo.kind == "isolated":
        zs_info = {"policy": "island KCL: i0 = 0 at low frequency; the floating offset delta takes up v0",
                   "v0_winding": "e0(theta) (floating)" if zs else "floating, value unknown (psi0 not declared)",
                   "status": "FEASIBLE"}
    elif zs is None:
        zs_info = {"policy": topo.zs_policy, "status": "UNKNOWN",
                   "detail": "L0 / psi_PM,0 not declared for a common-bus OEW: zero-sequence current, its voltage "
                             "headroom and loss are unknown (never assumed zero)"}
        reasons_cond.append(Reason.MISSING_INPUT)
        notes.append("common bus without zero-sequence data: voltage / current / loss claims are conditional on "
                     "e0 = 0 and stay UNKNOWN")
    elif topo.zs_policy == "regulate_i0":
        e0 = lambda t: we * zs.dpsi0(t)
        u0_fn = e0
        dpsi_bound = abs(we) * sum(n * n * a for n, a, _ in zs.psi0_harmonics)
        e0v = e0(th)
        zs_info = {"policy": "regulate_i0: u0* = e0 (ideal zero-sequence current control)", "status": "FEASIBLE",
                   "e0_peak_V": float(np.max(np.abs(e0v))), "i0_rms_A": 0.0,
                   "note": "u0* shares the per-phase voltage range with the fundamental (no final clipping)"}
    else:
        i0, R0 = _i0_trajectory(zs, we, k.Rs, th)
        zs_info = {"policy": "no_compensation: u0 = 0 average, i0 driven by the triplen EMF", "status": "FEASIBLE",
                   "e0_peak_V": float(np.max(np.abs(we * zs.dpsi0(th)))),
                   "i0_peak_A": float(np.max(np.abs(i0))), "i0_rms_A": float(np.sqrt(np.mean(i0 ** 2)))}
    # ---------------- voltage allocation (command voltage incl. drop)
    va = voltage_allocation(Uc, alpha_c, topo, reserve, u0_fn, dpsi_bound, n=max(1024, n_theta))
    # ---------------- currents
    ia, ib, ic = dq0_to_abc(id_A, iq_A, i0, th)
    ph = np.vstack([ia, ib, ic])
    i_peak = float(np.max(np.abs(ph)))
    i_rms = np.sqrt(np.mean(ph ** 2, axis=1))
    Ilim = topo.bridge_current_limit_A or drive.inverter.current_limit_A_peak
    # ---------------- copper / torque with the zero sequence
    pcu_dq = 1.5 * k.Rs * I * I
    pcu0 = 3.0 * R0 * float(np.mean(i0 ** 2))
    T0 = 3.0 * p * (zs.dpsi0(th) if zs is not None else 0.0 * th) * i0
    T0_mean = float(np.mean(T0))
    t_em = f["tem"] + T0_mean
    t_sh = (t_em - k.tau_rot) if k.tau_rot is not None else None
    # ---------------- pole duties (centered offsets inside the feasible interval)
    s = topo.split
    uk = np.vstack(dq0_to_abc(f["vd"], f["vq"], 0.0, th))
    u0c = u0_fn(th) if u0_fn is not None else np.zeros(n_theta)
    a, b = s * uk, -(1.0 - s) * uk
    VA, VB = topo.VA_V, topo.VB_V
    loA, hiA = -a.min(axis=0), VA - a.max(axis=0)
    loB, hiB = -b.min(axis=0), VB - b.max(axis=0)
    if topo.kind == "common_bus":
        lo = np.maximum(loA, loB + u0c)
        hi = np.minimum(hiA, hiB + u0c)
        cA = 0.5 * (lo + hi)
        cB = cA - u0c
    else:
        cA, cB = 0.5 * (loA + hiA), 0.5 * (loB + hiB)
    dA = (a + cA) / VA
    dB = (b + cB) / VB
    legA, legB = ph, -ph
    # ideal bridge AC power = V * <sum d i> (per sample exact for the average model)
    PA_ac = VA * float(np.mean(np.sum(np.clip(dA, 0, 1) * legA, axis=0)))
    PB_ac = VB * float(np.mean(np.sum(np.clip(dB, 0, 1) * legB, axis=0)))
    u_w = (a + cA) - (b + cB)
    P_wind = float(np.mean(np.sum(u_w * ph, axis=0)))
    P_wind_dq0 = 1.5 * (f["vd"] * id_A + f["vq"] * iq_A) + 3.0 * float(np.mean(u0c * i0))
    res_ports = PA_ac + PB_ac - P_wind
    lossA = _bridge_losses(drive, legA, dA, VA, I)
    lossB = _bridge_losses(drive, legB, dB, VB, I)
    pdcA = None if lossA["dc_side_W"] is None else PA_ac + lossA["dc_side_W"]
    pdcB = None if lossB["dc_side_W"] is None else PB_ac + lossB["dc_side_W"]
    tol = max(settings.power_abs_tol_W, 1e-9 * max(abs(PA_ac), abs(PB_ac), 1.0))
    # ---------------- claims
    claims = []
    cond = bool(k.issues) or bool(reasons_cond)
    cond_reasons = tuple(dict.fromkeys([i.reason for i in k.issues] + reasons_cond))
    cq = ("assumed-data conditional",) if cond else ()
    scope = (f"OEW {topo.kind}, {drive.drive_id} rev {drive.revision}; n = {speed_rpm:g} rpm; ideal-switch average "
             f"model, static periodic steady state")

    raw: dict = {}

    def mk(name, st, q, detail, reasons=(), ev=()):
        raw[name] = st.value                          # before the missing-data downgrade (search bookkeeping only)
        if cond and st is Status.FEASIBLE:
            st, reasons = Status.UNKNOWN, cond_reasons
        return Claim(name, st, q, scope, reasons=tuple(reasons), evidence=tuple(ev), qualifiers=cq, detail=detail)
    vst = Status(va["status"])
    claims.append(mk("oew_voltage_allocation", vst, "winding voltage realisable by the bridge pair at every angle",
                     f"utilisation {va['utilisation']:.4f} ({va['binding']}) at theta = {va['worst_theta_deg']:.1f} deg; "
                     f"{va['policy']}", reasons=(() if vst is Status.FEASIBLE else
                                                 (Reason.CONSTRAINT_VIOLATION,) if vst is Status.INFEASIBLE
                                                 else (Reason.BOUNDARY_WITHIN_TOLERANCE,)),
                     ev=(Evidence.make(EvidenceKind.CERTIFIED_BOUND if vst is Status.FEASIBLE else
                                       EvidenceKind.NUMERICAL_WITNESS,
                                       f"{va['samples']} angles + Lipschitz margin {va['lipschitz_margin_V']:.3g} V"),)))
    cst = Status.FEASIBLE if i_peak <= Ilim * (1 + 1e-12) else Status.INFEASIBLE
    claims.append(mk("oew_bridge_current", cst, "phase-current peak (incl. i0) within the per-bridge limit",
                     f"phase peak {i_peak:.5g} A vs {Ilim:.5g} A per bridge (each bridge carries the full winding "
                     f"current; never halved)", reasons=() if cst is Status.FEASIBLE else (Reason.CONSTRAINT_VIOLATION,)))
    dom = drive.domain
    in_dom = dom.id_A[0] <= id_A <= dom.id_A[1] and dom.iq_A[0] <= iq_A <= dom.iq_A[1] and \
        dom.speed_rpm[0] <= speed_rpm <= dom.speed_rpm[1]
    claims.append(mk("oew_domain", Status.FEASIBLE if in_dom else Status.INFEASIBLE, "inside the declared domain",
                     "id/iq/speed inside the declared domain" if in_dom else "outside the declared operating domain",
                     reasons=() if in_dom else (Reason.OUTSIDE_ALLOWED_OPERATING_DOMAIN,)))
    ports = {}
    if topo.kind == "common_bus":
        psrc = None if (pdcA is None or pdcB is None) else pdcA + pdcB
        c, info = _port_claim("oew_shared_source", psrc, VA, topo.limits_A, tol)
        claims.append(mk(c.name, c.status, c.quantity, c.detail, c.reasons))
        ports["shared_source"] = info
    else:
        for tag, pdc, V, lim in (("A", pdcA, VA, topo.limits_A), ("B", pdcB, VB, topo.limits_B)):
            c, info = _port_claim(f"oew_port_{tag}", pdc, V, lim, tol)
            claims.append(mk(c.name, c.status, c.quantity, c.detail, c.reasons))
            ports[tag] = info
    if T_request_Nm is not None:
        tq = t_sh if t_sh is not None else None
        ttol = max(settings.torque_residual_abs_Nm, settings.torque_residual_rel * k.torque_scale())
        if tq is None:
            claims.append(mk("oew_torque", Status.UNKNOWN, f"shaft torque {T_request_Nm:g} N*m",
                             "shaft torque undefined (rotational loss missing)", reasons=(Reason.MISSING_INPUT,)))
        else:
            ok = abs(tq - T_request_Nm) <= ttol
            claims.append(mk("oew_torque", Status.FEASIBLE if ok else Status.UNKNOWN, f"shaft torque {T_request_Nm:g} N*m",
                             f"T_shaft {tq:.6g} N*m (residual {tq - T_request_Nm:+.3g}, budget {ttol:.2g})",
                             reasons=() if ok else (Reason.NUMERICAL_UNRESOLVED,)))
    sts = [c.status for c in claims]
    overall = Status.INFEASIBLE if Status.INFEASIBLE in sts else Status.FEASIBLE if all(
        x is Status.FEASIBLE for x in sts) else Status.UNKNOWN
    circ = (min(abs(PA_ac), abs(PB_ac)) if PA_ac * PB_ac < 0 else 0.0)
    out = {
        "topology": topo.describe(), "drive_id": drive.drive_id, "speed_rpm": speed_rpm,
        "operating_point": {"id_A": id_A, "iq_A": iq_A, "i_dq_A": I, "vd_V": f["vd"], "vq_V": f["vq"], "U_phase_pk_V": U,
                            "U_cmd_pk_V": Uc, "Te_dq_Nm": f["tem"], "T0_mean_Nm": T0_mean,
                            "T0_ripple_pp_Nm": float(np.max(T0) - np.min(T0)), "Tem_Nm": t_em, "Tshaft_Nm": t_sh,
                            "P_winding_W": P_wind_dq0, "Pcu_dq_W": pcu_dq, "Pcu_zero_seq_W": pcu0},
        "voltage_allocation": va, "zero_sequence": zs_info,
        "currents": {"phase_peak_A": i_peak, "phase_rms_A": i_rms.tolist(), "dq_norm_A": I, "bridge_limit_A": Ilim,
                     "i0_rms_A": float(np.sqrt(np.mean(i0 ** 2))), "i0_peak_A": float(np.max(np.abs(i0)))},
        "bridges": {"A": {"P_ac_W": PA_ac, "loss": lossA, "P_dc_W": pdcA, "V_V": VA,
                          "duty_min": float(dA.min()), "duty_max": float(dA.max())},
                    "B": {"P_ac_W": PB_ac, "loss": lossB, "P_dc_W": pdcB, "V_V": VB,
                          "duty_min": float(dB.min()), "duty_max": float(dB.max())}},
        "ports": ports, "circulating_power_W": circ,
        "identities": {"ports_minus_winding_W": res_ports, "winding_abc_minus_dq0_W": P_wind - P_wind_dq0},
        "claims": [c.to_dict() for c in claims], "status": overall.value, "unconditioned_status": raw,
        "conditional": cond,
        "not_modelled": ["switching ripple (see the switched zero-sequence ripple)", "dead time, minimum pulse",
                         "chassis common mode / EMC / insulation stress", "cross-saturation with i0",
                         "bridge transitions and fault transients"],
        "notes": notes + list(k.notes)}
    if waveforms:
        step = max(1, n_theta // 360)
        out["waveforms"] = {"theta_deg": np.degrees(th[::step]).tolist(), "u_a_V": (u_w[0][::step]).tolist(),
                            "i_a_A": ia[::step].tolist(), "i0_A": i0[::step].tolist(),
                            "dA_a": dA[0][::step].tolist(), "dB_a": dB[0][::step].tolist(),
                            "u0_cmd_V": u0c[::step].tolist(), "T0_Nm": T0[::step].tolist()}
    return out


# --------------------------------------------------------------------------------------------- inverse / capability

def _grid(drive: DriveModel, k: DriveKernel, n_id: int, n_iq: int):
    dom = drive.domain
    d = np.linspace(dom.id_A[0], dom.id_A[1], n_id)
    q = np.linspace(dom.iq_A[0], dom.iq_A[1], n_iq)
    D, Q = np.meshgrid(d, q, indexing="ij")
    ev = k.evaluate(D, Q)
    return D, Q, ev


def _feasible_mask(D, Q, ev, drive, topo: OewTopology | None, k: DriveKernel, V_single: float | None = None,
                   n_theta: int = 180):
    """Voltage (certified over the angle) + current feasibility on a grid; topo None = single VSI at V_single."""
    Imax = drive.inverter.current_limit_A_peak if topo is None or topo.bridge_current_limit_A is None \
        else topo.bridge_current_limit_A
    I = np.hypot(D, Q)
    vr = drive.inverter.voltage
    ok = np.asarray(ev["ok"], dtype=bool) & (I <= Imax)
    if topo is None:
        vb = (1.0 - vr.reserve_fraction) * V_single / SQRT3
        return ok & (np.sqrt(ev["vcmd2"]) <= vb)
    reserve = vr.reserve_fraction if topo.reserve_fraction is None else topo.reserve_fraction
    ucd = ev["vd"] + 2.0 * vr.resistive_drop_ohm * D
    ucq = ev["vq"] + 2.0 * vr.resistive_drop_ohm * Q
    Uc = np.hypot(ucd, ucq)
    zs = topo.zero_sequence
    sp = topo.split
    if topo.kind == "common_bus" and zs is not None and topo.zs_policy == "no_compensation":
        i0, _ = _i0_trajectory(zs, k.omega_e, k.Rs, (np.arange(720) + 0.5) * TWO_PI / 720)
        ok = ok & (I + float(np.max(np.abs(i0))) <= Imax)          # conservative bound on the phase peak
    regulated = topo.kind == "common_bus" and zs is not None and topo.zs_policy == "regulate_i0" and \
        bool(zs.psi0_harmonics)
    if topo.kind == "isolated" or (not regulated and 0.0 <= sp <= 1.0):
        lim = _closed_form_limit(topo, reserve)
        return ok & (Uc <= lim * (1 - 1e-12))
    # angle check (sampled + Lipschitz margin): regulated zero sequence and / or a circulating split
    VA = (1.0 - reserve) * topo.VA_V
    th = (np.arange(n_theta) + 0.5) * TWO_PI / n_theta
    al = np.arctan2(ucq, ucd)
    u0 = k.omega_e * zs.dpsi0(th) if regulated else np.zeros(n_theta)
    dpsi = abs(k.omega_e) * sum(n * n * a for n, a, _ in zs.psi0_harmonics) if regulated else 0.0
    sa, sb = sp, sp - 1.0                                   # a = s u, b = (s - 1) u
    mx = lambda c, umax, umin: c * umax if c >= 0 else c * umin
    mn = lambda c, umax, umin: c * umin if c >= 0 else c * umax
    worst = np.full(D.shape, -np.inf)
    for j, t in enumerate(th):
        uk = [Uc * np.cos(t + al - TWO_PI * m / 3.0) for m in range(3)]
        umax, umin = np.maximum.reduce(uk), np.minimum.reduce(uk)
        spread = umax - umin
        cand = np.maximum.reduce([abs(sa) * spread - VA, abs(sb) * spread - VA,
                                  mx(sa, umax, umin) - mn(sb, umax, umin) + u0[j] - VA,
                                  mx(sb, umax, umin) - mn(sa, umax, umin) - u0[j] - VA])
        worst = np.maximum(worst, cand)
    L = np.maximum(2 * max(abs(sa), abs(sb)) * Uc, (abs(sa) + abs(sb)) * Uc + dpsi)
    return ok & (worst + L * (TWO_PI / n_theta) / 2.0 <= 0)


def oew_min_current_point(drive: DriveModel, topo: OewTopology, speed_rpm: float, T_request_Nm: float,
                          magnet_temp_C: float | None = None, winding_temp_C: float | None = None,
                          settings: NumericalSettings = DEFAULT_SETTINGS, n_id: int = 241, n_iq: int = 401) -> dict:
    """Minimum-current OEW point for a shaft torque: grid search on the torque curve + local refinement, then the
    witness is re-verified by ``oew_point``.  No witness -> UNKNOWN unless a topology-independent necessary
    condition (current-limited torque) excludes the request."""
    _check_drive_for_oew(drive)
    sc = Scenario("oew", speed_rpm, topo.VA_V, DcSourceLimits(), winding_temp_C=winding_temp_C,
                  magnet_temp_C=magnet_temp_C)
    k = DriveKernel(drive, sc, settings)
    if not k.evaluable:
        return {"status": "UNKNOWN", "reason": "model not evaluable: " + "; ".join(i.message for i in k.issues),
                "witness": None}
    if k.tau_rot is None:
        return {"status": "UNKNOWN", "reason": "rotational loss model missing: shaft torque undefined", "witness": None}
    D, Q, ev = _grid(drive, k, n_id, n_iq)
    T0_mean = 0.0
    if topo.kind == "common_bus" and topo.zero_sequence is not None and topo.zs_policy == "no_compensation":
        th = (np.arange(720) + 0.5) * TWO_PI / 720
        i0, _ = _i0_trajectory(topo.zero_sequence, k.omega_e, k.Rs, th)
        T0_mean = float(np.mean(3.0 * k.p * topo.zero_sequence.dpsi0(th) * i0))
    tsh = ev["tem"] + T0_mean - k.tau_rot
    feas = _feasible_mask(D, Q, ev, drive, topo, k)
    # torque-curve crossings along iq for every id (linear interpolation, then scalar refinement)
    cands = []
    err = tsh - T_request_Nm
    for i in range(D.shape[0]):
        e = err[i]
        okrow = np.asarray(ev["ok"][i], dtype=bool)
        idx = np.nonzero((np.sign(e[:-1]) != np.sign(e[1:])) & okrow[:-1] & okrow[1:])[0]
        for j in idx:
            if not (feas[i, j] or feas[i, j + 1]):
                continue
            q0, q1, e0, e1 = Q[i, j], Q[i, j + 1], e[j], e[j + 1]
            for _ in range(40):
                qm = q0 - e0 * (q1 - q0) / (e1 - e0) if e1 != e0 else 0.5 * (q0 + q1)
                em = float(k.evaluate(np.array([D[i, j]]), np.array([qm]))["tem"][0]) + T0_mean - k.tau_rot - T_request_Nm
                if abs(em) < 1e-10 * max(1.0, abs(T_request_Nm)):
                    break
                if np.sign(em) == np.sign(e0):
                    q0, e0 = qm, em
                else:
                    q1, e1 = qm, em
            cands.append((math.hypot(D[i, j], qm), float(D[i, j]), float(qm)))
    cands.sort()
    tried = 0
    for _I, d, q in cands[:40]:
        tried += 1
        try:
            r = oew_point(drive, topo, speed_rpm, d, q, T_request_Nm, magnet_temp_C, winding_temp_C, settings)
        except OutsideModelDomain:
            continue
        hard = r["unconditioned_status"]
        if all(hard.get(n) == "FEASIBLE" for n in ("oew_voltage_allocation", "oew_bridge_current", "oew_domain",
                                                     "oew_torque")):
            r["search"] = {"method": f"grid {n_id} x {n_iq} on the declared domain, torque-curve crossings refined; "
                                     f"minimum-current candidate re-verified", "candidates": len(cands),
                           "conditional": r["conditional"],
                           "verified": tried, "delta_id_A": float(D[1, 0] - D[0, 0]),
                           "meaning": "a witness (all electrical constraints verified at this point); minimality is "
                                      "within the grid resolution, not certified"}
            return {"status": r["status"], "witness": r, "reason": ""}
    # necessary condition independent of the topology: torque above the current-limited maximum
    from ..solvers.screens import run_screens
    scr = [x for x in run_screens(k, T_request_Nm) if x.violated and x.name == "torque_exceeds_current_limited_maximum"]
    if scr and not k.issues:
        return {"status": "INFEASIBLE", "witness": None, "reason": scr[0].statement,
                "evidence": "analytic necessary condition (current limit; independent of the voltage topology)"}
    return {"status": "UNKNOWN", "witness": None,
            "reason": f"no witness in the sampled search ({len(cands)} torque-curve candidates); no infeasibility proof "
                      f"(a search failure is not physical infeasibility)"}


def capability_comparison(drive: DriveModel, VA_V: float, speeds_rpm, zero_sequence: ZeroSequenceModel | None = None,
                          zs_policy: str = "regulate_i0", VB_V: float | None = None, direction: int = 1,
                          magnet_temp_C: float | None = None, winding_temp_C: float | None = None,
                          settings: NumericalSettings = DEFAULT_SETTINGS, n_id: int = 161, n_iq: int = 241) -> dict:
    """Same motor, same grid method: single VSI at VA, common-bus OEW at VA, isolated OEW VA + VB, and a single VSI
    on the same total stack VA + VB (different device blocking voltage).  Values are witnessed grid maxima of the
    electrical capability (voltage + current), a lower bound per configuration - not certified maxima; DC-source
    limits are not applied."""
    _check_drive_for_oew(drive)
    VB_V = VA_V if VB_V is None else VB_V
    cb = OewTopology("common_bus", VA_V, zero_sequence=zero_sequence, zs_policy=zs_policy)
    iso = OewTopology("isolated", VA_V, VB_V)
    rows = []
    for n in speeds_rpm:
        sc = Scenario("cmp", float(n), VA_V, DcSourceLimits(), winding_temp_C=winding_temp_C, magnet_temp_C=magnet_temp_C)
        k = DriveKernel(drive, sc, settings)
        if not k.evaluable or k.tau_rot is None:
            rows.append({"speed_rpm": float(n), "note": "model not evaluable / shaft torque undefined"})
            continue
        D, Q, ev = _grid(drive, k, n_id, n_iq)
        tsh = ev["tem"] - k.tau_rot
        row = {"speed_rpm": float(n)}
        for key, topo, V in (("single_vsi_Nm", None, VA_V), ("oew_common_bus_Nm", cb, None),
                             ("oew_isolated_Nm", iso, None), ("single_vsi_same_stack_Nm", None, VA_V + VB_V)):
            m = _feasible_mask(D, Q, ev, drive, topo, k, V_single=V)
            if topo is cb and zero_sequence is None:
                row["oew_common_bus_conditional"] = "e0 = 0 assumed (zero-sequence data not declared)"
            vals = np.where(m, direction * tsh, -np.inf)
            j = np.unravel_index(int(np.argmax(vals)), vals.shape)
            row[key] = None if not np.isfinite(vals[j]) else float(direction * vals[j])
            row[key.replace("_Nm", "_at")] = None if not np.isfinite(vals[j]) else [float(D[j]), float(Q[j])]
        rows.append(row)
    return {"rows": rows, "VA_V": VA_V, "VB_V": VB_V, "direction": direction,
            "grid": {"n_id": n_id, "n_iq": n_iq}, "zero_sequence_declared": zero_sequence is not None,
            "zs_policy": zs_policy,
            "meaning": "witnessed grid maxima (same method for every configuration): lower bounds of the electrical "
                       "capability; DC-source limits, losses, thermal and i0 transients not applied",
            "comparison_basis": {"single_vsi": f"one {VA_V:g} V bridge (ideal ceiling V/sqrt3)",
                                 "oew_common_bus": f"two bridges on one {VA_V:g} V bus (zero-u0 hexagon: V)",
                                 "oew_isolated": f"{VA_V:g} V + {VB_V:g} V isolated sources ((VA+VB)/sqrt3)",
                                 "single_vsi_same_stack": f"one {VA_V + VB_V:g} V bridge (same total stack, "
                                                          f"higher device blocking voltage)"}}


# --------------------------------------------------------------------------------------------- paired safe states

def paired_state_screen(topo: OewTopology, stateA: str, stateB: str, emf_phase_peak_V: float | None = None,
                        L0_H: float | None = None) -> dict:
    """Screening of one (bridge A state, bridge B state) pair; a safe state is a property of the pair.

    States: pwm, asc_top (all phases to +), asc_bottom (all phases to -), off (gates off, diodes remain).
    Status answers "is this pair a de-energising safe state by this screening": FEASIBLE (no excitation / conduction
    path at steady state), INFEASIBLE (a counterexample: excitation or torque not removed), UNKNOWN (a coupled
    transient / source model is needed).  Gate command is not conduction state: diodes stay in every state.
    """
    for st in (stateA, stateB):
        if st not in BRIDGE_STATES:
            raise InputValidationError(f"bridge state must be one of {BRIDGE_STATES}", field="state")
    VA, VB = topo.VA_V, topo.VB_V
    rail = {"asc_top": 1, "asc_bottom": 0}
    out = {"topology": topo.kind, "A": stateA, "B": stateB}
    if stateA in rail and stateB in rail:
        sa, sb = rail[stateA], rail[stateB]
        if topo.kind == "common_bus":
            u = VA * sa - VB * sb
            out["winding_voltage_V"] = [u, u, u]
            out["u0_V"] = u
            if u != 0:
                out["status"] = "INFEASIBLE"
                out["reason"] = ("opposite-rail clamps apply a DC zero-sequence voltage |u0| = V to the winding through "
                                 "the switches: alpha-beta = 0 so a dq-only model sees nothing - large i0 builds up")
                if L0_H:
                    out["di0_dt_initial_A_per_s"] = u / L0_H
                    out["note"] = (f"initial di0/dt = V/L0 = {u / L0_H:.4g} A/s (resistance, saturation and source "
                                   f"impedance neglected: a counterexample, not a peak prediction)")
            else:
                out["status"] = "UNKNOWN"
                out["reason"] = ("same-rail clamps short the winding (u = 0): PM short-circuit current transient and a "
                                 "zero-sequence circulating current from the triplen EMF - a winding short is not "
                                 "automatically safe (ASC transient / demag / device survival need their models)")
                out["reason_code"] = "COUPLED_MODEL_REQUIRED"
        else:
            out["status"] = "UNKNOWN"
            out["reason"] = ("isolated islands: clamping each bridge to one of its rails shorts the winding "
                             "differentially (the floating offset takes up the rail difference); transient as for an "
                             "ASC - check the rail references before reusing the common-bus counterexample")
            out["reason_code"] = "COUPLED_MODEL_REQUIRED"
        return out
    if "pwm" in (stateA, stateB) and (stateA in rail or stateB in rail or "off" in (stateA, stateB)):
        other = stateB if stateA == "pwm" else stateA
        out["status"] = "INFEASIBLE" if other in rail else "UNKNOWN"
        out["reason"] = (f"one bridge {other}, the other still switching: the PWM bridge keeps driving the winding "
                         f"('ASC entered' is not 'torque removed')" if other in rail else
                         "one bridge off, the other switching: diode paths of the off bridge stay active; not a "
                         "de-energised state")
        return out
    if stateA == "pwm" and stateB == "pwm":
        out["status"] = "INFEASIBLE"
        out["role"] = "normal operation"
        out["reason"] = "normal operation: not a safe state"
        return out
    # at least one bridge off, the other off or clamped
    if emf_phase_peak_V is None:
        out["status"] = "UNKNOWN"
        out["reason"] = "back-EMF not known at this speed / temperature: rectification threshold not evaluable"
        out["reason_code"] = "MISSING_INPUT"
        return out
    if stateA == "off" and stateB == "off":
        thr = VA if topo.kind == "common_bus" else (VA + VB) / SQRT3
        path = ("per phase through the A and B diodes into the one bus (phase EMF peak vs V)" if topo.kind == "common_bus"
                else "two phases in series through both islands (line EMF peak vs VA + VB)")
    else:
        clamped = stateB if stateA == "off" else stateA
        if topo.kind == "common_bus":
            out["status"] = "UNKNOWN"
            out["reason"] = (f"one bridge {clamped}, the other off: the off bridge's diode to the clamped rail shorts the "
                             f"winding for one EMF polarity (half-wave short) and rectifies the other polarity into the "
                             f"bus above V - braking current and heating; coupled transient model required")
            out["reason_code"] = "COUPLED_MODEL_REQUIRED"
            return out
        thr = (VA if stateA == "off" else VB) / SQRT3
        path = "two phases through the off bridge's diodes into its own island (line EMF peak vs that island's V)"
    out["threshold_phase_peak_V"] = thr
    out["emf_phase_peak_V"] = emf_phase_peak_V
    out["path"] = path
    if emf_phase_peak_V > thr:
        out["status"] = "UNKNOWN"
        out["reason"] = (f"back-EMF phase peak {emf_phase_peak_V:.4g} V > {thr:.4g} V: diode rectification charges the "
                         f"DC link(s) - with a source disconnected this is an overvoltage risk")
        out["reason_code"] = "COUPLED_MODEL_REQUIRED"
    else:
        out["status"] = "FEASIBLE"
        out["reason"] = (f"back-EMF phase peak {emf_phase_peak_V:.4g} V <= {thr:.4g} V: no steady diode conduction "
                         f"(ideal diodes; transients and stray paths not covered)")
    return out


def paired_state_table(topo: OewTopology, emf_phase_peak_V: float | None = None, L0_H: float | None = None) -> list:
    rows = []
    for a, b in itertools.product(BRIDGE_STATES, BRIDGE_STATES):
        rows.append(paired_state_screen(topo, a, b, emf_phase_peak_V, L0_H))
    return rows


# --------------------------------------------------------------------------------------------- switched i0 ripple

def zero_sequence_switching_ripple(V: float, U_pk: float, alpha_rad: float, fsw_Hz: float, fe_Hz: float, L0_H: float,
                                   R0_ohm: float, split_A: float = 0.5, carrier_shift: float = 0.0,
                                   u0_cmd=None, periods: int = 3) -> dict:
    """Common-bus OEW with ideal switches: instantaneous u0 = V (sum sA - sum sB)/3 from triangle-carrier PWM of the
    centered duties; i0 integrated exactly between switching edges (duties held per carrier period).

    Shows that <u0> = u0* per carrier period while i0 still ripples (L0 small), and how the carrier phase shift
    between the bridges changes it.  The triplen back-EMF is not included here (ripple part only).
    """
    for name, v in (("V", V), ("fsw_Hz", fsw_Hz), ("L0_H", L0_H)):
        if _finite(name, v) <= 0:
            raise InputValidationError(f"{name} must be > 0", field=name)
    fe = abs(fe_Hz)
    Ts = 1.0 / fsw_Hz
    n_car = int(round(fsw_Hz / fe)) if fe > 0 else 40
    n_car = max(n_car, 6)
    Te = n_car * Ts
    s = split_A
    tau = L0_H / R0_ohm if R0_ohm > 0 else math.inf
    i0 = 0.0
    t_list, i_list, v_list = [0.0], [0.0], []
    t_now = 0.0
    avg_err = 0.0
    for m in range(periods * n_car):
        tc = (m + 0.5) * Ts
        th = TWO_PI * (tc % Te) / Te
        u = np.array([U_pk * math.cos(th + alpha_rad - TWO_PI * kk / 3.0) for kk in range(3)])
        u0c = 0.0 if u0_cmd is None else float(u0_cmd(th))
        a, b = s * u, -(1 - s) * u
        lo = max(-a.min(), -b.min() + u0c)
        hi = min(V - a.max(), V - b.max() + u0c)
        cA = 0.5 * (lo + hi)
        cB = cA - u0c
        dA = np.clip((a + cA) / V, 0, 1)
        dB = np.clip((b + cB) / V, 0, 1)
        # edges: leg high on a centered window of width d*Ts (bridge B shifted by carrier_shift*Ts)
        edges = [0.0, Ts]
        wins = []
        for dd, sh in ((dA, 0.0), (dB, carrier_shift)):
            wl = []
            for x in dd:
                c0 = (0.5 + sh) * Ts
                w0, w1 = c0 - 0.5 * x * Ts, c0 + 0.5 * x * Ts
                wl.append((w0, w1))
                for e in (w0, w1):
                    e_mod = e % Ts
                    edges.append(e_mod)
            wins.append(wl)
        edges = sorted(set(round(e, 15) for e in edges))

        def high(w, t):
            w0, w1 = w
            for off in (-Ts, 0.0, Ts):
                if w0 + off <= t < w1 + off:
                    return 1
            return 0
        vsum = 0.0
        for e0, e1 in zip(edges[:-1], edges[1:]):
            if e1 - e0 <= 0:
                continue
            tm = 0.5 * (e0 + e1)
            sA = sum(high(w, tm) for w in wins[0])
            sB = sum(high(w, tm) for w in wins[1])
            v0 = V * (sA - sB) / 3.0
            dt = e1 - e0
            vsum += v0 * dt
            if math.isinf(tau):
                i0 = i0 + v0 / L0_H * dt
            else:
                ifin = v0 / R0_ohm
                i0 = ifin + (i0 - ifin) * math.exp(-dt / tau)
            t_now += dt
            t_list.append(t_now)
            i_list.append(i0)
            v_list.append(v0)
        avg_err = max(avg_err, abs(vsum / Ts - u0c))
    t = np.array(t_list)
    i = np.array(i_list)
    last = t >= (periods - 1) * Te
    il = i[last]
    tl = t[last]
    # time-weighted RMS over the last fundamental period (piecewise-exponential samples at every edge)
    w = np.diff(tl)
    tw = max(float(np.sum(w)), 1e-30)
    rms = float(math.sqrt(np.sum(0.5 * (il[:-1] ** 2 + il[1:] ** 2) * w) / tw)) if w.size else 0.0
    mean_w = float(np.sum(0.5 * (il[:-1] + il[1:]) * w) / tw) if w.size else 0.0
    ac_rms = float(math.sqrt(max(rms ** 2 - mean_w ** 2, 0.0)))
    return {"V_V": V, "U_pk_V": U_pk, "fsw_Hz": fsw_Hz, "fe_Hz": fe, "L0_H": L0_H, "R0_ohm": R0_ohm,
            "carrier_shift": carrier_shift, "split_A": split_A, "carrier_periods_per_fundamental": n_car,
            "i0_pp_A": float(il.max() - il.min()), "i0_rms_A": rms, "i0_mean_A": mean_w, "i0_ripple_rms_A": ac_rms,
            "max_carrier_average_u0_error_V": avg_err,
            "copper_zero_sequence_W": 3.0 * R0_ohm * rms ** 2,
            "trace": {"t_s": (tl - tl[0]).tolist()[:4000], "i0_A": il.tolist()[:4000]},
            "meaning": "u0 averages to the command in every carrier period, yet the instantaneous u0 steps drive i0 "
                       "ripple through the small L0 - an average model cannot show it; triplen EMF not included"}


def two_sensor_reconstruction(ia, ib, ic) -> dict:
    """ic = -ia - ib (two current sensors) forces the reconstructed i0 to zero: a monitor built on it cannot see the
    circulating zero-sequence current of a common-bus OEW (O-07)."""
    ia, ib, ic = (np.asarray(x, dtype=float) for x in (ia, ib, ic))
    i0_true = (ia + ib + ic) / 3.0
    ic_rec = -ia - ib
    i0_rec = (ia + ib + ic_rec) / 3.0
    return {"i0_true_rms_A": float(np.sqrt(np.mean(i0_true ** 2))), "i0_reconstructed_rms_A": float(np.sqrt(np.mean(i0_rec ** 2))),
            "ic_error_rms_A": float(np.sqrt(np.mean((ic - ic_rec) ** 2))),
            "note": "three independent phase sensors (or a validated residual measurement) are needed to observe i0"}


_hull = hull          # former private name (compatibility)
