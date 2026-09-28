"""Maps: T-n performance maps and the id-iq constraint plane.

T-n maps evaluate the minimum-current policy point at every grid node
(efficiency, losses, current, modulation, power factor, ...).  A node is shown
only if its policy point exists; nodes whose policy point violates a DC limit
are kept but flagged (status DC).  Efficiency uses the production definitions
(motoring P_shaft/P_dc, regenerating |P_dc|/|P_shaft|, N/A at zero shaft power
or standstill).

The id-iq plane shows the model's own constraint functions on a grid: command
voltage magnitude vs budget, current circle, declared domain box, DC power
limits, constant-torque contours, the MTPA locus (numerical: extreme torque
per current magnitude) and, for constant-parameter models, the MTPV locus
(extreme torque per flux-linkage magnitude, stator resistance neglected; a
guide line only).
"""

from __future__ import annotations

import math

import numpy as np

from ..models.components import DriveModel
from ..physics import DriveKernel
from ..scenario import DcSourceLimits, Scenario
from ..solvers.common import dc_ok, electrical_ok
from ..solvers.policy import PolicyEvaluator
from .sweeps import DC, FIELDS, NONE, OK, UNKNOWN, Progress, _tick, point_fields, policy_point

MAP_FIELDS = FIELDS


def tn_map(drive: DriveModel, limits: DcSourceLimits, Vdc_V: float, speeds, torques,
           progress: Progress = None, magnet_temp_C: float | None = None) -> dict:
    speeds = np.asarray(speeds, float)
    torques = np.asarray(torques, float)
    shape = (torques.size, speeds.size)
    grids = {f: np.full(shape, np.nan) for f in MAP_FIELDS}
    status = np.full(shape, UNKNOWN, dtype=int)
    fw = np.zeros(shape, dtype=bool)
    total = speeds.size
    for j, s in enumerate(speeds):
        ev = PolicyEvaluator(drive, Scenario("map", float(s), float(Vdc_V), limits, magnet_temp_C=magnet_temp_C))
        for i, T in enumerate(torques):
            st, pt = policy_point(ev, float(T))
            status[i, j] = st
            if pt is None:
                continue
            for key, v in point_fields(pt).items():
                if v is not None:
                    grids[key][i, j] = v
            c = pt.constraint("VOLTAGE")
            fw[i, j] = c is not None and c.state == "ACTIVE"
        _tick(progress, (j + 1) / total, f"n = {s:.0f} rpm")
    tq_fine = np.linspace(torques.min(), torques.max(), 400)
    return {"speeds": speeds, "torques": torques, "grids": grids, "status": status, "voltage_active": fw,
            "Vdc_V": float(Vdc_V), "base_speed": base_speed_curve(drive, limits, Vdc_V, tq_fine)}


def default_axes(drive: DriveModel, limits: DcSourceLimits, Vdc_V: float, n_speed: int, n_torque: int,
                 T_max: float | None = None, T_min: float | None = None,
                 magnet_temp_C: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    hi = max(abs(drive.domain.speed_rpm[0]), abs(drive.domain.speed_rpm[1]))
    speeds = np.linspace(0.01 * hi, hi, n_speed)          # efficiency is N/A at standstill
    n_torque += n_torque % 2                              # even count: T = 0 (efficiency N/A) is not a node
    if T_max is None or T_min is None:
        k = DriveKernel(drive, Scenario("axes", 0.0, float(Vdc_V), limits, magnet_temp_C=magnet_temp_C))
        from .sweeps import grid_extreme
        g_hi = grid_extreme(k, +1, include_dc=False)
        g_lo = grid_extreme(k, -1, include_dc=False)
        T_max = T_max if T_max is not None else (g_hi[0] if g_hi else 1.0)
        T_min = T_min if T_min is not None else (g_lo[0] if g_lo else -1.0)
    torques = np.linspace(T_min, T_max, n_torque)
    return speeds, torques


# ---------------------------------------------------------------------------
# id-iq constraint plane
# ---------------------------------------------------------------------------

def _plane_box(k: DriveKernel) -> tuple[float, float, float, float]:
    d0, d1 = -1.9 * k.Imax, 1.1 * k.Imax        # generous so an equal-aspect view can fill any canvas shape
    q0, q1 = -1.5 * k.Imax, 1.5 * k.Imax
    span = 1.1 * k.Imax
    dom = k.domain
    d0 = min(d0, dom.id_A[0] - 0.05 * span)
    if k.kind == "flux_map" and k.plane is not None:
        d0, d1 = float(k.plane.id_axis_A[0]), float(k.plane.id_axis_A[-1])
        q0, q1 = float(k.plane.iq_axis_A[0]), float(k.plane.iq_axis_A[-1])
    return d0, d1, q0, q1


def mtpa_locus(k: DriveKernel, n_current: int = 90, n_angle: int = 1441) -> dict:
    """Extreme torque per current magnitude (motoring and braking branches).

    Constant-parameter model: closed form id*psi + (Ld - Lq)*(id^2 - iq^2) = 0.
    Flux map: angle scan per current magnitude with parabolic refinement.
    """
    if not k.evaluable:
        return {"motoring": (np.array([]), np.array([])), "braking": (np.array([]), np.array([]))}
    if k.kind == "constant_dq":
        dl = k.Ld - k.Lq
        iq = np.linspace(0.0, k.Imax, 4 * n_current)
        if abs(dl) < 1e-15:
            idv = np.zeros_like(iq)
        else:
            idv = (-k.psi + np.sqrt(k.psi ** 2 + 4.0 * dl * dl * iq * iq)) / (2.0 * dl)
        keep = np.hypot(idv, iq) <= k.Imax * (1 + 1e-12)
        return {"motoring": (idv[keep], iq[keep]), "braking": (idv[keep], -iq[keep])}
    I = np.linspace(k.Imax / n_current, k.Imax, n_current)[:, None]
    b = np.linspace(-math.pi, math.pi, n_angle)[None, :]
    D, Q = I * np.cos(b), I * np.sin(b)
    e = k.evaluate(D, Q)
    T = np.where(np.asarray(e["ok"], bool), e["tem"], np.nan)
    out = {}
    for tag, sign in (("motoring", 1), ("braking", -1)):
        score = np.where(np.isfinite(T), sign * T, -np.inf)
        j = np.clip(np.argmax(score, axis=1), 1, n_angle - 2)
        rows = np.arange(I.shape[0])
        f0, f1, f2 = score[rows, j - 1], score[rows, j], score[rows, j + 1]
        with np.errstate(invalid="ignore", divide="ignore"):
            den = f0 - 2.0 * f1 + f2
            off = np.where(np.isfinite(den) & (den < 0), 0.5 * (f0 - f2) / den, 0.0)
        ang = b[0, j] + np.clip(off, -1.0, 1.0) * (b[0, 1] - b[0, 0])
        good = np.isfinite(f1) & (f1 > 0)
        out[tag] = ((I[:, 0] * np.cos(ang))[good], (I[:, 0] * np.sin(ang))[good])
    return out


def mtpv_locus(k: DriveKernel, n_levels: int = 80, n_angle: int = 2001) -> dict | None:
    """Constant-parameter model only; stator resistance neglected (guide line)."""
    if k.kind != "constant_dq" or not k.psi or k.Ld is None or k.Ld <= 0 or k.Lq <= 0:
        return None
    psi, ld, lq, p = k.psi, k.Ld, k.Lq, k.p
    lam_max = psi + ld * 1.2 * k.Imax
    lam = np.linspace(lam_max / n_levels, lam_max, n_levels)[:, None]
    g = np.linspace(0.0, math.pi, n_angle)[None, :]
    psd, psq = lam * np.cos(g), lam * np.sin(g)
    d = (psd - psi) / ld
    q = psq / lq
    T = 1.5 * p * (psd * q - psq * d)
    j = np.argmax(T, axis=1)
    rows = np.arange(lam.shape[0])
    dd, qq = d[rows, j], q[rows, j]
    inside = np.hypot(dd, qq) <= 1.2 * k.Imax
    return {"motoring": (dd[inside], qq[inside]), "braking": (dd[inside], -qq[inside]),
            "note": "MTPV guide (Rs neglected); constant-parameter model"}


def idiq_plane(drive: DriveModel, limits: DcSourceLimits, speed_rpm: float, Vdc_V: float,
               T_request: float | None = None, resolution: int = 301, extra_speeds=(),
               scenario: Scenario | None = None) -> dict:
    """``scenario`` (optional) carries temperatures etc.; its speed/Vdc are replaced by the arguments."""
    if scenario is not None:
        sc = scenario.with_(speed_rpm=float(speed_rpm), Vdc_V=float(Vdc_V))
    else:
        sc = Scenario("plane", float(speed_rpm), float(Vdc_V), limits)
    k = DriveKernel(drive, sc)
    d0, d1, q0, q1 = _plane_box(k)
    x = np.linspace(d0, d1, resolution)
    y = np.linspace(q0, q1, resolution)
    X, Y = np.meshgrid(x, y, indexing="xy")          # shape (len(y), len(x)) for matplotlib
    e = k.evaluate(X, Y)
    ok = np.asarray(e["ok"], bool)
    nan = lambda A: np.where(ok, A, np.nan)
    out = {
        "x": x, "y": y, "X": X, "Y": Y, "kind": k.kind,
        "view": (max(d0, -1.15 * k.Imax), min(d1, 0.35 * k.Imax), max(q0, -1.1 * k.Imax), min(q1, 1.1 * k.Imax)),
        "V": nan(np.sqrt(e["vcmd2"])), "I": np.sqrt(e["i2"]),
        "T": nan(e["tsh"] if k.tau_rot is not None else e["tem"]),
        "T_is_shaft": k.tau_rot is not None,
        "Pdc": nan(e["pdc"]) if k.i2_dc is not None else None,
        "covered": ok,
        "electrical_ok": electrical_ok(k, X, Y, e),
        "budget_V": k.Vb, "ceiling_V": k.V_ceiling, "Imax_A": k.Imax,
        "domain_id_A": tuple(drive.domain.id_A), "domain_iq_A": tuple(drive.domain.iq_A),
        "P_dis_eff_W": k.P_dis_eff, "P_chg_eff_W": k.P_chg_eff,
        "speed_rpm": float(speed_rpm), "Vdc_V": float(Vdc_V), "T_request_Nm": T_request,
        "mtpa": mtpa_locus(k), "mtpv": mtpv_locus(k),
    }
    # grid P_dc exists only through the surrogate's I^2 identity; with a pointwise loss model the DC limits are not
    # evaluated on the grid (None = not evaluated, never 'violated') and are judged at the policy point instead
    out["all_ok"] = out["electrical_ok"] & dc_ok(k, e["pdc"]) if (k.dc_defined and k.i2_dc is not None) else None
    out["dc_grid_note"] = (f"DC limits not evaluated on the grid: the {k.loss_label} is evaluated point by point; "
                           f"they are judged at the policy point" if k.pointwise_loss else None)
    if k.kind == "constant_dq":
        out["characteristic_current_A"] = (-k.psi / k.Ld, 0.0) if k.Ld else None
    out["policy_point"] = None
    out["policy_status"] = None
    if T_request is not None:
        ev = PolicyEvaluator(drive, sc)
        st, pt = policy_point(ev, float(T_request))
        out["policy_status"] = st
        if pt is not None:
            out["policy_point"] = (pt.id_A, pt.iq_A)
    ellipses = []
    for s in extra_speeds:
        ks = DriveKernel(drive, sc.with_(speed_rpm=float(s)))
        es = ks.evaluate(X, Y)
        ellipses.append((float(s), np.where(np.asarray(es["ok"], bool), np.sqrt(es["vcmd2"]), np.nan), ks.Vb))
    out["ellipses"] = ellipses
    return out


def base_speed_curve(drive: DriveModel, limits: DcSourceLimits, Vdc_V: float, torques, iterations: int = 12) -> dict:
    """Speed at which the MTPA point of each shaft torque reaches the command-voltage budget.

    Below this speed the minimum-current point is the MTPA point (voltage not binding); above it the
    voltage constraint is active (field weakening).  At fixed MTPA currents |v_cmd(w_e)|^2 = Vb^2 is a
    quadratic in w_e; the rotational loss torque (T_em = T_shaft + tau_rot(w_m)) is handled by a
    fixed-point iteration, so the curve follows the model without speed sampling.
    """
    k = DriveKernel(drive, Scenario("base-speed", 0.0, float(Vdc_V), limits))
    torques = np.asarray(torques, float)
    out = np.full(torques.size, np.nan)
    if not k.evaluable:
        return {"torques": torques, "speed_rpm": out}
    loc = mtpa_locus(k)
    r = k.Rs + k.R_drop
    rot = k.rot
    for tag, sign in (("motoring", 1), ("braking", -1)):
        d, q = (np.asarray(a, float) for a in loc[tag])
        if d.size < 2:
            continue
        psd, psq, _ok = k.flux(d, q)
        tem = np.asarray(k.evaluate(d, q)["tem"], float)
        order = np.argsort(sign * tem)
        tem, d, q, psd, psq = (np.asarray(a, float)[order] for a in (tem, d, q, psd, psq))
        sel = np.flatnonzero(sign * torques > 0)
        if sel.size == 0:
            continue
        T = torques[sel]
        t_em = T.copy()
        we = np.full(T.size, np.nan)
        for _ in range(iterations):
            x = sign * t_em
            inside = (x >= sign * tem[0]) & (x <= sign * tem[-1])
            di, qi = np.interp(x, sign * tem, d), np.interp(x, sign * tem, q)
            pdi, pqi = np.interp(x, sign * tem, psd), np.interp(x, sign * tem, psq)
            a = pdi ** 2 + pqi ** 2
            b = 2.0 * r * (qi * pdi - di * pqi)
            c = r * r * (di * di + qi * qi) - k.Vb ** 2
            disc = b * b - 4 * a * c
            we = np.where(inside & (disc >= 0) & (a > 0), (-b + np.sqrt(np.maximum(disc, 0.0))) / np.where(a > 0, 2 * a, 1.0),
                          np.nan)
            if rot is None:
                break
            wm = we / k.p
            t_em = T + np.array([rot.torque(float(w)) if np.isfinite(w) else 0.0 for w in wm])
        out[sel] = we / k.p * 60.0 / (2.0 * math.pi)
    return {"torques": torques, "speed_rpm": out}


def status_counts(status: np.ndarray) -> dict:
    return {"ok": int((status == OK).sum()), "dc": int((status == DC).sum()), "none": int((status == NONE).sum()),
            "unknown": int((status == UNKNOWN).sum()), "total": int(status.size)}
