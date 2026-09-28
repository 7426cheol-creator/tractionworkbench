"""Shaft-torque capability at one scenario.

Two different questions (Blueprint 7.1/7.2):

* physical capability  - max/min shaft torque over *every* control in the
  allowed domain meeting voltage, current, domain (and optionally DC) limits;
* policy capability    - the largest/most negative requested torque for which
  the minimum-current policy is FEASIBLE including the DC source limits.

A feasible witness gives one side of the bound (achieved value); the other
side needs a certificate (Lagrangian for the constant model, cell bounds for
flux maps).  Without one the result is reported as sampled.  The regenerative
policy boundary is not the most negative torque obtainable by deliberately
raising losses; that is the physical capability, reported separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize_scalar

from ..errors import OutsideModelDomain
from ..models.components import DriveModel
from ..physics import DriveKernel, OperatingPoint, evaluate_point
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..status import Evidence, EvidenceKind
from .certificate import certify_torque
from .common import dc_ok, electrical_ok
from .gate import check_witness
from .policy import PolicyEvaluator, scope_text


@dataclass(frozen=True)
class CapabilityResult:
    kind: str
    direction: int
    speed_rpm: float
    Vdc_V: float
    include_dc: bool
    value_Nm: float | None
    bound_Nm: float | None
    certified: bool
    gap_tolerance_Nm: float
    witness: OperatingPoint | None
    segments: tuple = ()
    evidence: tuple = ()
    scope: str = ""
    notes: tuple = ()
    gate_messages: tuple = ()          # why the value is a diagnostic only (model validity, missing DC limit, ...)

    @property
    def accepted(self) -> bool:
        """The value may be used as evidence (it passed the common witness gate)."""
        return self.value_Nm is not None and not self.gate_messages

    @property
    def gap_Nm(self) -> float | None:
        if self.value_Nm is None or self.bound_Nm is None:
            return None
        return abs(self.bound_Nm - self.value_Nm)

    @property
    def active_constraints(self) -> tuple[str, ...]:
        if self.witness is None:
            return ()
        return tuple(c.name for c in self.witness.active())

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "direction": "maximum (motoring side)" if self.direction > 0 else "minimum (braking side)",
            "speed_rpm": self.speed_rpm,
            "Vdc_V": self.Vdc_V,
            "include_dc_limits": self.include_dc,
            "achieved_value_Nm": self.value_Nm,
            "certified_opposite_bound_Nm": self.bound_Nm,
            "gap_Nm": self.gap_Nm,
            "gap_tolerance_Nm": self.gap_tolerance_Nm,
            "certified": self.certified,
            "active_constraints_at_witness": list(self.active_constraints),
            "policy_feasible_torque_segments_Nm": [list(s) for s in self.segments],
            "witness": None if self.witness is None else self.witness.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
            "scope": self.scope,
            "notes": list(self.notes),
            "accepted_as_evidence": self.accepted,
            "diagnostic_only_because": list(self.gate_messages),
        }


def gap_tolerance(k: DriveKernel) -> float:
    s = k.settings
    return max(s.capability_gap_abs_Nm, s.capability_gap_rel * k.torque_scale())


# ---------------------------------------------------------------------------
# physical / electrical capability
# ---------------------------------------------------------------------------

def _quad_interval(A: float, B: float, C: float, tol: float):
    """{q : A q^2 + B q + C <= 0} for A >= 0, as a list of closed intervals."""
    if A <= 0:
        if B == 0:
            return [(-math.inf, math.inf)] if C <= tol else []
        r = -C / B
        return [(-math.inf, r)] if B > 0 else [(r, math.inf)]
    disc = B * B - 4 * A * C
    if disc < 0:
        if disc > -tol * max(B * B, 4 * abs(A * C), 1e-300):
            disc = 0.0
        else:
            return []
    # numerically stable roots: t = -(B + sign(B) sqrt(disc))/2, q1 = t/A, q2 = C/t
    t = -0.5 * (B + math.copysign(math.sqrt(disc), B))
    if t == 0.0:
        return [(0.0, 0.0)]
    q1, q2 = t / A, C / t
    return [(min(q1, q2), max(q1, q2))]


def _intersect(a, b):
    out = []
    for lo1, hi1 in a:
        for lo2, hi2 in b:
            lo, hi = max(lo1, lo2), min(hi1, hi2)
            if lo <= hi:
                out.append((lo, hi))
    return out


def _iq_sets_constant(k: DriveKernel, d: float, include_dc: bool):
    """Exact feasible iq set at fixed id for the constant model (union of intervals)."""
    rv = k.Rs + k.R_drop
    we = k.omega_e
    kd = k.psi + (k.Ld - k.Lq) * d
    ivs = [(k.domain.iq_A[0], k.domain.iq_A[1])]
    r2 = k.Imax ** 2 - d * d
    if r2 < 0:
        return []
    ivs = _intersect(ivs, [(-math.sqrt(r2), math.sqrt(r2))])
    A = (we * k.Lq) ** 2 + rv * rv
    B = 2 * rv * we * kd
    C = rv * rv * d * d + (we * (k.psi + k.Ld * d)) ** 2 - k.Vb ** 2
    ivs = _intersect(ivs, _quad_interval(A, B, C, 1e-12))
    if include_dc and k.i2_dc is not None and ivs:
        c2 = k.i2_dc.c2_W_per_A2
        base = c2 * d * d + k.i2_dc.a0_W
        Bp = 1.5 * we * kd
        if k.P_dis_eff is not None:
            ivs = _intersect(ivs, _quad_interval(c2, Bp, base - k.P_dis_eff, 1e-12))
        if k.P_chg_eff is not None and ivs:
            # P_dc >= -P_chg  <=>  NOT (c2 q^2 + Bp q + base + P_chg < 0)
            hole = _quad_interval(c2, Bp, base + k.P_chg_eff, 0.0)
            if hole:
                h0, h1 = hole[0]
                new = []
                for lo, hi in ivs:
                    if hi < h0 or lo > h1:
                        new.append((lo, hi))
                        continue
                    if lo <= h0:
                        new.append((lo, h0))
                    if hi >= h1:
                        new.append((h1, hi))
                ivs = new
    return ivs


def _best_q(k: DriveKernel, d: float, direction: int, include_dc: bool):
    ivs = _iq_sets_constant(k, d, include_dc)
    if not ivs:
        return None
    kd = k.psi + (k.Ld - k.Lq) * d
    ends = [x for iv in ivs for x in iv if math.isfinite(x)]
    if not ends:
        return None
    kp = 1.5 * k.p
    vals = [(direction * (kp * kd * q), q) for q in ends]
    return max(vals)[1]


def _torque(k: DriveKernel, d: float, q: float) -> float:
    return 1.5 * k.p * (k.psi + (k.Ld - k.Lq) * d) * q - k.tau_rot_or_zero


def _verify(k: DriveKernel, d: float, q: float, include_dc: bool, direction: int):
    """Evaluate a witness; if rounding lands it marginally outside, pull it towards the origin."""
    for shrink in (0.0, 1e-12, 1e-10, 1e-8):
        dd, qq = d * (1 - shrink), q * (1 - shrink)
        try:
            pt = evaluate_point(k, dd, qq)
        except OutsideModelDomain:
            continue
        groups = ("VOLTAGE", "CURRENT", "DOMAIN") + (("DISCHARGE_SOURCE", "CHARGE_SOURCE") if include_dc else ())
        if pt.all_satisfied(groups):
            return pt
    return None


def _physical_constant(k: DriveKernel, direction: int, include_dc: bool):
    s = k.settings
    d_lo, d_hi = k.domain.id_A
    d_lo, d_hi = max(d_lo, -k.Imax), min(d_hi, k.Imax)
    if d_lo > d_hi:
        return None, "empty id range"
    ids = np.linspace(d_lo, d_hi, s.physical_grid_points)
    vals = np.full(ids.size, -math.inf)
    qs = np.full(ids.size, math.nan)
    for i, d in enumerate(ids):
        q = _best_q(k, float(d), direction, include_dc)
        if q is not None:
            vals[i] = direction * _torque(k, float(d), q)
            qs[i] = q
    if not np.any(np.isfinite(vals)):
        return None, f"no feasible point on {ids.size} id samples"
    # refine the best few local maxima
    cand = []
    finite = np.isfinite(vals)
    for i in np.flatnonzero(finite):
        left = vals[i - 1] if i > 0 else -math.inf
        right = vals[i + 1] if i + 1 < vals.size else -math.inf
        if vals[i] >= left and vals[i] >= right:
            cand.append(i)
    cand = sorted(cand, key=lambda i: -vals[i])[:4]
    best = None
    for i in cand:
        a = ids[max(i - 1, 0)]
        b = ids[min(i + 1, ids.size - 1)]

        def f(d):
            q = _best_q(k, d, direction, include_dc)
            return math.inf if q is None else -direction * _torque(k, d, q)

        pts = [(ids[i], qs[i])]
        if b > a:
            r = minimize_scalar(f, bounds=(a, b), method="bounded", options={"xatol": 1e-11})
            q = _best_q(k, r.x, direction, include_dc)
            if q is not None:
                pts.append((r.x, q))
        for d, q in pts:
            pt = _verify(k, float(d), float(q), include_dc, direction)
            if pt is not None and (best is None or direction * pt.Tshaft_Nm > direction * best.Tshaft_Nm):
                best = pt
    return best, f"exact iq intervals on {ids.size} id samples + bounded refinement of local extrema"


def _physical_map(ev: PolicyEvaluator, direction: int, include_dc: bool):
    k = ev.k
    tr = ev.tracer
    if tr.empty_region:
        return None, "covered control region is empty"
    D, Q = np.meshgrid(tr.ids, tr.iqs, indexing="ij")
    e = k.evaluate(D, Q)
    ok = electrical_ok(k, D, Q, e)
    if include_dc and k.i2_dc is not None:
        ok &= dc_ok(k, e["pdc"])
    if not np.any(ok):
        return None, "no feasible grid node"
    t = np.where(ok, direction * e["tsh"], -np.inf)
    order = np.argsort(t, axis=None)[::-1][:6]
    best = None
    for flat in order:
        i, j = np.unravel_index(flat, t.shape)
        d = float(D[i, j])
        q = float(Q[i, j])
        # push along iq towards larger direction*T until a constraint binds (bisection)
        step = tr.dq_step
        cand = [(d, q)]
        for sgn in (1.0, -1.0):
            q_out = q + sgn * step
            if not (tr.region[2] <= q_out <= tr.region[3]):
                continue
            pe = k.evaluate(d, q_out)
            ok_out = bool(electrical_ok(k, np.array([d]), np.array([q_out]))[0]) and \
                (not include_dc or bool(dc_ok(k, np.array([float(pe["pdc"])]))[0]))
            if ok_out:
                continue
            a, b = q, q_out
            for _ in range(60):
                m = 0.5 * (a + b)
                pm = k.evaluate(d, m)
                okm = bool(electrical_ok(k, np.array([d]), np.array([m]))[0]) and \
                    (not include_dc or bool(dc_ok(k, np.array([float(pm["pdc"])]))[0]))
                a, b = (m, b) if okm else (a, m)
            cand.append((d, a))
        for dd, qq in cand:
            pt = _verify(k, dd, qq, include_dc, direction)
            if pt is not None and (best is None or direction * pt.Tshaft_Nm > direction * best.Tshaft_Nm):
                best = pt
    return best, f"grid {tr.ids.size} x {tr.iqs.size} + boundary bisection along iq (sampled)"


def physical_capability(ev: PolicyEvaluator, direction: int, include_dc: bool = True) -> CapabilityResult:
    k = ev.k
    tol = gap_tolerance(k)
    scope = scope_text(k)
    kind = "physical" if include_dc else "electrical"
    if not ev.speed_in_domain or not k.evaluable or k.tau_rot is None:
        why = ("speed outside the declared domain" if not ev.speed_in_domain else
               "model not evaluable at this scenario" if not k.evaluable else "rotational loss model missing")
        return CapabilityResult(kind, direction, k.speed_rpm, k.Vdc, include_dc, None, None, False, tol, None,
                                scope=scope, notes=(why,))
    if k.kind == "constant_dq":
        wit, how = _physical_constant(k, direction, include_dc)
    else:
        wit, how = _physical_map(ev, direction, include_dc)
    evidence = []
    notes = [how]
    if include_dc and k.pointwise_loss:
        notes.append(f"the {k.loss_label} is evaluated point by point: the witness search and the upper bound do "
                     "not contain the DC limits (the bound is the electrical one, still an upper bound); the witness "
                     "itself is checked against them directly")
    bound = None
    certified = False
    if wit is None:
        return CapabilityResult(kind, direction, k.speed_rpm, k.Vdc, include_dc, None, None, False, tol, None,
                                evidence=(Evidence.make(EvidenceKind.SAMPLED, how),), scope=scope, notes=tuple(notes))
    val = wit.Tshaft_Nm
    evidence.append(Evidence.make(EvidenceKind.NUMERICAL_WITNESS,
                                  f"feasible point id = {wit.id_A:.6f} A, iq = {wit.iq_A:.6f} A gives {val:.6f} N*m"))
    if k.kind == "constant_dq":
        cert = certify_torque(k, direction, (wit.id_A, wit.iq_A), include_dc)
        if cert.valid:
            bound = cert.upper_bound
            evidence.append(Evidence.make(EvidenceKind.CERTIFIED_BOUND, cert.note, **cert.to_dict()))
    if bound is None or abs(bound - val) > tol:
        cb = ev.bounds.torque_bound(direction, val, include_dc)
        if cb.bound is not None and (bound is None or direction * cb.bound < direction * bound):
            bound = cb.bound
            evidence.append(Evidence.make(EvidenceKind.BOUNDED_SEARCH, cb.note, **cb.to_dict()))
    cov_limited = ev.tracer.cov != math.inf if k.kind == "flux_map" else (k.drive.motor.flux.validity is not None)
    if bound is not None:
        certified = abs(bound - val) <= tol and not cov_limited
    if cov_limited:
        notes.append("allowed domain not fully covered by model data: bounds hold within covered data only")
    gate = check_witness(k, wit.id_A, wit.iq_A, T_request=val, require_dc=include_dc, point=wit)
    gate_msgs = tuple(gate.messages)
    if gate_msgs:
        certified = False
        notes.append("diagnostic only (failed the common witness gate): " + "; ".join(gate_msgs))
    return CapabilityResult(kind, direction, k.speed_rpm, k.Vdc, include_dc, val, bound, certified, tol, wit,
                            evidence=tuple(evidence), scope=scope, notes=tuple(notes), gate_messages=gate_msgs)


# ---------------------------------------------------------------------------
# policy capability
# ---------------------------------------------------------------------------

def policy_capability(ev: PolicyEvaluator, direction: int, samples: int | None = None,
                      certify: bool = True) -> CapabilityResult:
    k = ev.k
    s = ev.settings
    tol = gap_tolerance(k)
    scope = scope_text(k)
    n = samples or s.capability_scan_samples
    if not ev.speed_in_domain or not k.evaluable or k.tau_rot is None:
        return CapabilityResult("policy", direction, k.speed_rpm, k.Vdc, True, None, None, False, tol, None,
                                scope=scope, notes=("scenario outside the declared domain or model incomplete",))
    if k.issues:
        msgs = tuple(f"model validity: {i.message}" for i in k.issues)
        return CapabilityResult("policy", direction, k.speed_rpm, k.Vdc, True, None, None, False, tol, None,
                                scope=scope, notes=("the model-validity gate failed: no policy capability is claimed",),
                                gate_messages=msgs)
    elec = physical_capability(ev, direction, include_dc=False)
    if elec.value_Nm is None:
        return CapabilityResult("policy", direction, k.speed_rpm, k.Vdc, True, None, None, False, tol, None,
                                scope=scope, notes=("no electrically feasible torque at this scenario",))
    t_end = elec.value_Nm
    t_start = 0.0
    if direction * t_end < 0:
        # even the extreme torque has the 'wrong' sign: scan the whole electrical range
        other = physical_capability(ev, -direction, include_dc=False)
        t_start = other.value_Nm if other.value_Nm is not None else t_end
    grid = np.linspace(t_start, t_end, n)
    st = [ev.quick_status(float(t))[0] for t in grid]
    rel = s.capability_bisection_rel_tol * max(abs(t_end), 1.0)

    def bisect(a, b):
        # a has status FEASIBLE, b does not
        for _ in range(200):
            if abs(b - a) <= rel:
                break
            m = 0.5 * (a + b)
            if ev.quick_status(m)[0] == "FEASIBLE":
                a = m
            else:
                b = m
        return a, b

    segments = []
    i = 0
    while i < n:
        if st[i] != "FEASIBLE":
            i += 1
            continue
        j = i
        while j + 1 < n and st[j + 1] == "FEASIBLE":
            j += 1
        a = grid[i] if i == 0 else bisect(grid[i], grid[i - 1])[0]
        b = grid[j] if j == n - 1 else bisect(grid[j], grid[j + 1])[0]
        segments.append((float(min(a, b)), float(max(a, b)), i == 0))
        i = j + 1
    notes = [f"torque scan: {n} samples between {t_start:.6g} and {t_end:.6g} N*m (electrical extreme), "
             f"transitions bisected to {rel:.1e} N*m; contiguity between samples is assumed (sampled)"]
    unknown = sum(1 for x in st if x == "UNKNOWN")
    if unknown:
        notes.append(f"{unknown} scan samples UNKNOWN (not counted as feasible)")
    if not segments:
        return CapabilityResult("policy", direction, k.speed_rpm, k.Vdc, True, None, None, False, tol, None,
                                segments=(), scope=scope, notes=tuple(notes + ["policy infeasible over the whole scan"]))
    first = next((sg for sg in segments if sg[2]), None)
    if first is None:
        notes.append("the policy is not feasible near zero torque; the reported value is the extreme feasible segment")
        seg = max(segments, key=lambda sg: direction * (sg[1] if direction > 0 else sg[0]))
    else:
        seg = first
        if len(segments) > 1:
            notes.append("policy-feasible torque set is not contiguous: requests between segments fail")
    val = seg[1] if direction > 0 else seg[0]
    sol = ev.solve(val)
    wit = sol.point
    evidence = [Evidence.make(EvidenceKind.SAMPLED, notes[0])]
    if wit is not None:
        evidence.append(Evidence.make(EvidenceKind.NUMERICAL_WITNESS,
                                      f"policy point at {val:.6f} N*m: id = {wit.id_A:.6f} A, iq = {wit.iq_A:.6f} A, "
                                      f"P_dc = {wit.Pdc_W:.6g} W"))
    bound = None
    certified = False
    if certify:
        phys = physical_capability(ev, direction, include_dc=True)
        if phys.bound_Nm is not None:
            bound = phys.bound_Nm
            certified = phys.certified and abs(bound - val) <= tol
            evidence.append(Evidence.make(
                EvidenceKind.CERTIFIED_BOUND if phys.certified else EvidenceKind.BOUNDED_SEARCH,
                f"physical capability bound {bound:.6f} N*m (any control, all limits) bounds the policy capability",
                physical_witness_Nm=phys.value_Nm))
            if direction < 0 and phys.value_Nm is not None and phys.value_Nm < val - tol:
                notes.append(f"any-control braking capability reaches {phys.value_Nm:.6f} N*m by raising losses; "
                             "the minimum-current (energy-recovering) policy boundary is less negative")
    segs = tuple((a, b) for a, b, _ in segments)
    gate_msgs = ()
    if wit is None or sol.policy_claim.status.value != "FEASIBLE":
        gate_msgs = (f"the full policy solve at the scanned boundary {val:.6g} N*m is "
                     f"{sol.policy_claim.status.value}, not FEASIBLE",)
    else:
        g = check_witness(k, wit.id_A, wit.iq_A, T_request=val, require_dc=True, point=wit)
        gate_msgs = tuple(g.messages)
    if gate_msgs:
        certified = False
        notes.append("diagnostic only: " + "; ".join(gate_msgs))
    return CapabilityResult("policy", direction, k.speed_rpm, k.Vdc, True, val, bound, certified, tol, wit,
                            segments=segs, evidence=tuple(evidence), scope=scope, notes=tuple(notes),
                            gate_messages=gate_msgs)


def capability(drive: DriveModel, scenario: Scenario, direction: int = 1, kind: str = "policy",
               settings: NumericalSettings = DEFAULT_SETTINGS) -> CapabilityResult:
    ev = PolicyEvaluator(drive, scenario, settings)
    if kind == "policy":
        return policy_capability(ev, direction)
    if kind == "physical":
        return physical_capability(ev, direction, include_dc=True)
    if kind == "electrical":
        return physical_capability(ev, direction, include_dc=False)
    raise ValueError(f"unknown capability kind {kind!r}")
