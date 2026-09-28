"""Operating trajectories: torque sweep at fixed speed, speed sweep at fixed torque, capability envelope.

Points are minimum-current policy points (``PolicyEvaluator.curve(T).min_point``)
evaluated with ``evaluate_point``; each point carries a status:

    OK       policy point exists and satisfies the DC source limits
    DC       policy point exists but violates a DC source limit
    NONE     no electrically feasible point (proven for constant-parameter models)
    UNKNOWN  not established (outside the model/speed domain or map coverage)

Envelope values come from ``policy_capability``/``physical_capability`` for
constant-parameter models.  For flux-map models the rigorous capability search
is slow, so the envelope is a dense-grid estimate (labelled ``grid``) intended
for visualisation only; decisions always use the rigorous solvers.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np

from ..models.components import DriveModel
from ..physics import ACTIVE, NOT_EVALUATED, DriveKernel, OperatingPoint, evaluate_point
from ..scenario import DcSourceLimits, Scenario
from ..service import CURVE_SETTINGS
from ..solvers.capability import physical_capability, policy_capability
from ..solvers.common import dc_ok, electrical_ok
from ..solvers.policy import PolicyEvaluator

OK, DC, NONE, UNKNOWN = 0, 1, 2, 3
STATUS_NAMES = {OK: "OK", DC: "DC_LIMIT", NONE: "NO_SOLUTION", UNKNOWN: "UNKNOWN"}
DC_GROUPS = ("DISCHARGE_SOURCE", "CHARGE_SOURCE")

FIELDS = ("id_A", "iq_A", "I_peak_A", "I_rms_A", "v_cmd_V", "v_margin_V", "m_linear", "Te_Nm", "Tshaft_Nm",
          "Pshaft_W", "Pac_W", "Pdc_W", "Idc_A", "Pcu_W", "Pinv_W", "Prot_W", "P_loss_W", "P_loss_known_W", "eta",
          "eta_motor", "eta_inverter", "pf", "psi_d_Wb", "psi_q_Wb")

Progress = Callable[[float, str], None] | None


class Cancelled(Exception):
    pass


def _tick(progress: Progress, frac: float, msg: str = "") -> None:
    if progress is not None:
        progress(frac, msg)


def point_fields(pt: OperatingPoint) -> dict:
    """Map / sweep fields of one point.

    Losses: ``P_loss_W`` is the total only when every loss term is known; otherwise None, with the known subtotal
    in ``P_loss_known_W`` (a missing loss is never summed as zero).  Efficiencies are judged per boundary on their
    own two ports (inverter P_dc <-> P_ac, motor P_ac <-> P_shaft, inverter+motor P_dc <-> P_shaft), independently
    of the overall energy mode: a missing inverter loss leaves the motor efficiency defined, and a standstill point
    keeps the inverter DC -> AC terminal ratio."""
    from ..analysis.efficiency import DEFINED, boundary_eta
    terms = (pt.Pcu_W, pt.Pinv_W, pt.Prot_W)
    known = sum(v for v in terms if v is not None)
    loss = known if all(v is not None for v in terms) else None
    tol = 1e-6 * max(1.0, abs(pt.Pac_W))

    def eta(a, b):
        r = boundary_eta(a, b, tol)
        return r["eta"] if r["status"] == DEFINED else None
    eta_i = eta(pt.Pdc_W, pt.Pac_W)
    eta_m = eta(pt.Pac_W, pt.Pshaft_W)
    denom = 1.5 * pt.v_peak_V * pt.i_peak_A
    return {
        "id_A": pt.id_A, "iq_A": pt.iq_A, "I_peak_A": pt.i_peak_A, "I_rms_A": pt.i_phase_rms_A,
        "v_cmd_V": pt.v_cmd_peak_V, "v_margin_V": pt.voltage_margin_V,
        "m_linear": pt.v_cmd_peak_V / (pt.Vdc_V / math.sqrt(3.0)),
        "Te_Nm": pt.Te_Nm, "Tshaft_Nm": pt.Tshaft_Nm, "Pshaft_W": pt.Pshaft_W, "Pac_W": pt.Pac_W,
        "Pdc_W": pt.Pdc_W, "Idc_A": pt.Idc_A, "Pcu_W": pt.Pcu_W, "Pinv_W": pt.Pinv_W, "Prot_W": pt.Prot_W,
        "P_loss_W": loss, "P_loss_known_W": known, "eta": pt.efficiency, "eta_motor": eta_m, "eta_inverter": eta_i,
        "pf": (pt.Pac_W / denom) if denom > 0 else None,
        "psi_d_Wb": pt.psi_d_Wb, "psi_q_Wb": pt.psi_q_Wb,
    }


def policy_point(ev: PolicyEvaluator, T: float) -> tuple[int, OperatingPoint | None]:
    k = ev.k
    if not ev.speed_in_domain or not k.evaluable:
        return UNKNOWN, None
    c = ev.curve(float(T))
    if c.empty:
        return (NONE if (c.exact and not c.coverage_limited) else UNKNOWN), None
    p = c.min_point
    pt = evaluate_point(k, float(p.id_A), float(p.iq_A))
    dc = [x for x in pt.constraints if x.group in DC_GROUPS]
    if pt.Pdc_W is None or any(x.state == NOT_EVALUATED for x in dc):
        return UNKNOWN, pt
    return (OK if all(x.ok for x in dc) else DC), pt


class _Table:
    def __init__(self, n: int):
        self.n = n
        self.cols = {f: np.full(n, np.nan) for f in FIELDS}
        self.status = np.full(n, UNKNOWN, dtype=int)
        self.voltage_active = np.zeros(n, dtype=bool)
        self.current_active = np.zeros(n, dtype=bool)
        self.active: list[list[str]] = [[] for _ in range(n)]

    def put(self, i: int, status: int, pt: OperatingPoint | None):
        self.status[i] = status
        if pt is None:
            return
        for key, v in point_fields(pt).items():
            if v is not None:
                self.cols[key][i] = v
        act = [c.name for c in pt.constraints if c.state == ACTIVE]
        self.active[i] = act
        self.voltage_active[i] = "VOLTAGE" in act
        self.current_active[i] = "CURRENT" in act

    def out(self, **extra) -> dict:
        d = {k: v for k, v in self.cols.items()}
        d.update(status=self.status, voltage_active=self.voltage_active, current_active=self.current_active,
                 active=self.active, **extra)
        return d


# ---------------------------------------------------------------------------
# grid estimate of torque extremes (fast, visualisation only)
# ---------------------------------------------------------------------------

def _box(k: DriveKernel) -> tuple[float, float, float, float]:
    dom = k.domain
    d0, d1 = max(dom.id_A[0], -k.Imax), min(dom.id_A[1], k.Imax)
    q0, q1 = max(dom.iq_A[0], -k.Imax), min(dom.iq_A[1], k.Imax)
    if k.kind == "flux_map" and k.plane is not None:
        d0, d1 = max(d0, float(k.plane.id_axis_A[0])), min(d1, float(k.plane.id_axis_A[-1]))
        q0, q1 = max(q0, float(k.plane.iq_axis_A[0])), min(q1, float(k.plane.iq_axis_A[-1]))
    return d0, d1, q0, q1


def grid_extreme(k: DriveKernel, direction: int, include_dc: bool, n: int = 241, refine: int = 2):
    """(T, id, iq) maximising direction*T over the feasible grid; None if no grid point is feasible."""
    if not k.evaluable:
        return None
    d0, d1, q0, q1 = _box(k)
    best = None
    for level in range(refine + 1):
        X, Y = np.meshgrid(np.linspace(d0, d1, n), np.linspace(q0, q1, n), indexing="ij")
        e = k.evaluate(X, Y)
        T = e["tsh"] if k.tau_rot is not None else e["tem"]
        m = electrical_ok(k, X, Y, e)
        if include_dc and k.dc_defined:
            m &= dc_ok(k, e["pdc"])
        if not m.any():
            break
        score = np.where(m, direction * T, -np.inf)
        i, j = np.unravel_index(int(np.argmax(score)), score.shape)
        cand = (float(T[i, j]), float(X[i, j]), float(Y[i, j]))
        if best is None or direction * cand[0] > direction * best[0]:
            best = cand
        hd, hq = 3 * (d1 - d0) / (n - 1), 3 * (q1 - q0) / (n - 1)
        D0, D1, Q0, Q1 = _box(k)
        d0, d1 = max(D0, cand[1] - hd), min(D1, cand[1] + hd)
        q0, q1 = max(Q0, cand[2] - hq), min(Q1, cand[2] + hq)
        n = 81
    return best


def electrical_torque_range(k: DriveKernel) -> tuple[float, float] | None:
    lo = grid_extreme(k, -1, include_dc=False)
    hi = grid_extreme(k, +1, include_dc=False)
    if lo is None or hi is None:
        return None
    return lo[0], hi[0]


# ---------------------------------------------------------------------------
# sweeps
# ---------------------------------------------------------------------------

def torque_sweep(drive: DriveModel, limits: DcSourceLimits, speed_rpm: float, Vdc_V: float, n: int = 81,
                 T_range: tuple[float, float] | None = None, progress: Progress = None) -> dict:
    ev = PolicyEvaluator(drive, Scenario("torque-sweep", float(speed_rpm), float(Vdc_V), limits))
    k = ev.k
    caps = {}
    if k.kind == "constant_dq" and ev.speed_in_domain and k.evaluable:
        ev_c = PolicyEvaluator(drive, ev.scenario, CURVE_SETTINGS)
        for direction, tag in ((1, "max"), (-1, "min")):
            pc = policy_capability(ev_c, direction, certify=False)
            el = physical_capability(ev_c, direction, include_dc=False)
            caps[f"policy_{tag}_Nm"] = pc.value_Nm
            caps[f"electrical_{tag}_Nm"] = el.value_Nm
    if T_range is None:
        if "electrical_max_Nm" in caps and caps["electrical_max_Nm"] is not None:
            T_range = (caps["electrical_min_Nm"], caps["electrical_max_Nm"])
        else:
            T_range = electrical_torque_range(k) or (-1.0, 1.0)
    base = np.linspace(T_range[0], T_range[1], n)
    extra = [v for v in (0.0, caps.get("policy_max_Nm"), caps.get("policy_min_Nm")) if v is not None
             and T_range[0] <= v <= T_range[1]]
    Ts = np.unique(np.concatenate([base, np.asarray(extra, float)]))
    tab = _Table(Ts.size)
    for i, T in enumerate(Ts):
        st, pt = policy_point(ev, float(T))
        tab.put(i, st, pt)
        _tick(progress, (i + 1) / Ts.size, f"T = {T:.1f} N*m")
    return tab.out(x=Ts, x_kind="torque", speed_rpm=float(speed_rpm), Vdc_V=float(Vdc_V), caps=caps,
                   current_limit_A=k.Imax, voltage_budget_V=k.Vb, kind=k.kind,
                   P_dis_eff_W=k.P_dis_eff, P_chg_eff_W=k.P_chg_eff)


def speed_sweep(drive: DriveModel, limits: DcSourceLimits, T_Nm: float, Vdc_V: float, speeds=None, n: int = 81,
                progress: Progress = None) -> dict:
    if speeds is None:
        hi = max(abs(drive.domain.speed_rpm[0]), abs(drive.domain.speed_rpm[1]))
        speeds = np.linspace(0.0, hi, n)
    speeds = np.asarray(speeds, float)
    tab = _Table(speeds.size)
    k = None
    for i, s in enumerate(speeds):
        ev = PolicyEvaluator(drive, Scenario("speed-sweep", float(s), float(Vdc_V), limits))
        k = ev.k
        st, pt = policy_point(ev, float(T_Nm))
        tab.put(i, st, pt)
        _tick(progress, (i + 1) / speeds.size, f"n = {s:.0f} rpm")
    return tab.out(x=speeds, x_kind="speed", T_Nm=float(T_Nm), Vdc_V=float(Vdc_V),
                   current_limit_A=None if k is None else k.Imax, kind=None if k is None else k.kind,
                   voltage_budget_V=None if k is None else k.Vb,
                   P_dis_eff_W=None if k is None else k.P_dis_eff, P_chg_eff_W=None if k is None else k.P_chg_eff)


def envelope(drive: DriveModel, limits: DcSourceLimits, Vdc_V: float, speeds=None, n: int = 41,
             directions=(1, -1), method: str = "auto", progress: Progress = None) -> dict:
    """Capability vs speed with the witness point at every speed (policy incl. DC, and electrical only)."""
    if speeds is None:
        hi = max(abs(drive.domain.speed_rpm[0]), abs(drive.domain.speed_rpm[1]))
        speeds = np.linspace(0.0, hi, n)
    speeds = np.asarray(speeds, float)
    kind = DriveKernel(drive, Scenario("probe", float(speeds[0]), float(Vdc_V), limits)).kind
    if method == "auto":
        method = "solver" if kind == "constant_dq" else "grid"
    out = {"x": speeds, "x_kind": "speed", "Vdc_V": float(Vdc_V), "method": method, "kind": kind}
    total = speeds.size * len(directions)
    step = 0
    for direction in directions:
        tag = "max" if direction > 0 else "min"
        pol = _Table(speeds.size)
        el = np.full(speeds.size, np.nan)
        pol_T = np.full(speeds.size, np.nan)
        for i, s in enumerate(speeds):
            sc = Scenario("envelope", float(s), float(Vdc_V), limits)
            if method == "solver":
                ev = PolicyEvaluator(drive, sc, CURVE_SETTINGS)
                pc = policy_capability(ev, direction, certify=False)
                ec = physical_capability(ev, direction, include_dc=False)
                if pc.value_Nm is not None:
                    pol_T[i] = pc.value_Nm
                if pc.witness is not None:
                    pol.put(i, OK, pc.witness)
                if ec.value_Nm is not None:
                    el[i] = ec.value_Nm
            else:
                k = DriveKernel(drive, sc)
                g = grid_extreme(k, direction, include_dc=True)
                if g is not None:
                    pol_T[i] = g[0]
                    pol.put(i, OK, evaluate_point(k, g[1], g[2]))
                ge = grid_extreme(k, direction, include_dc=False)
                if ge is not None:
                    el[i] = ge[0]
            step += 1
            _tick(progress, step / total, f"{tag}: n = {s:.0f} rpm")
        out[tag] = pol.out(T_Nm=pol_T, electrical_T_Nm=el)
    k0 = DriveKernel(drive, Scenario("probe", float(speeds[0]), float(Vdc_V), limits))
    out.update(current_limit_A=k0.Imax, voltage_budget_V=k0.Vb, P_dis_eff_W=k0.P_dis_eff, P_chg_eff_W=k0.P_chg_eff)
    return out


def power_curve(env: dict, tag: str = "max") -> np.ndarray:
    """Shaft power along the envelope [W] (T * omega_m)."""
    return env[tag]["T_Nm"] * env["x"] * 2.0 * math.pi / 60.0
