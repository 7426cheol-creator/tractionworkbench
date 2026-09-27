"""Lagrangian upper-bound certificates for the constant-parameter model.

Every quantity of the constant model is a quadratic in x = (id, iq):

    f(x) = 1/2 x^T H x + g^T x + c

For the problem  max T_shaft(x)  s.t.  g_j(x) <= 0  and any multipliers
lambda_j >= 0, the Lagrangian  L = T - sum lambda_j g_j  satisfies T(x) <= L(x)
on the feasible set.  If the Hessian of L is negative definite, L has a
unique global maximum L(x_s) = c_L - 1/2 g_L^T H_L^{-1} g_L, which is therefore
an upper bound of the maximum torque (weak duality).  This is valid whatever
the convexity of the constraints; it is only *tight* when the multipliers are
good.  Multipliers start from the KKT conditions at a feasible witness and are
then improved by a small derivative-free search over log(lambda).

The minimum torque is handled as the maximum of -T.  With a charging (lower
DC-power) constraint active the Lagrangian is generally not concave, which is
exactly the situation where deliberately raising losses changes the answer;
no certificate is claimed then.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

from ..physics import DriveKernel

J = np.array([[0.0, 1.0], [1.0, 0.0]])


@dataclass(frozen=True)
class Quad:
    name: str
    H: np.ndarray
    g: np.ndarray
    c: float

    def value(self, x) -> float:
        x = np.asarray(x, dtype=float)
        return float(0.5 * x @ self.H @ x + self.g @ x + self.c)

    def grad(self, x) -> np.ndarray:
        return self.H @ np.asarray(x, dtype=float) + self.g


def constant_model_quads(k: DriveKernel, include_dc: bool = True) -> tuple[Quad, list[Quad]]:
    """Objective T_shaft and constraint quadratics (g <= 0) at the kernel's scenario."""
    if k.kind != "constant_dq":
        raise TypeError("quadratic certificates need the constant-parameter model")
    kp = 1.5 * k.p
    psi, dl = k.psi, k.Ld - k.Lq
    T = Quad("T_shaft", kp * dl * J, np.array([0.0, kp * psi]), -k.tau_rot_or_zero)
    rv = k.Rs + k.R_drop
    we = k.omega_e
    A = np.array([[rv, -we * k.Lq], [we * k.Ld, rv]])
    b0 = np.array([0.0, we * psi])
    cons = [
        Quad("VOLTAGE", 2.0 * A.T @ A, 2.0 * A.T @ b0, float(b0 @ b0 - k.Vb ** 2)),
        Quad("CURRENT", 2.0 * np.eye(2), np.zeros(2), -k.Imax ** 2),
        Quad("ID_MIN", np.zeros((2, 2)), np.array([-1.0, 0.0]), k.domain.id_A[0]),
        Quad("ID_MAX", np.zeros((2, 2)), np.array([1.0, 0.0]), -k.domain.id_A[1]),
        Quad("IQ_MIN", np.zeros((2, 2)), np.array([0.0, -1.0]), k.domain.iq_A[0]),
        Quad("IQ_MAX", np.zeros((2, 2)), np.array([0.0, 1.0]), -k.domain.iq_A[1]),
    ]
    if include_dc and k.inv_loss is not None:
        c2 = 1.5 * k.Rs + k.inv_loss.ipk2_coeff_W_per_A2
        Hp = k.omega_m * kp * dl * J + 2.0 * c2 * np.eye(2)
        gp = np.array([0.0, k.omega_m * kp * psi])
        a0 = k.inv_loss.offset_W
        if k.P_dis_eff is not None:
            cons.append(Quad("DC_DISCHARGE", Hp, gp, a0 - k.P_dis_eff))
        if k.P_chg_eff is not None:
            cons.append(Quad("DC_CHARGE", -Hp, -gp, -k.P_chg_eff - a0))
    return T, cons


@dataclass(frozen=True)
class Certificate:
    valid: bool
    upper_bound: float | None
    multipliers: tuple
    hessian_eigenvalues: tuple
    stationarity_residual: float | None
    witness_value: float
    gap: float | None
    note: str

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "upper_bound": self.upper_bound,
            "multipliers": dict(self.multipliers),
            "lagrangian_hessian_eigenvalues": list(self.hessian_eigenvalues),
            "kkt_stationarity_residual": self.stationarity_residual,
            "witness_value": self.witness_value,
            "gap": self.gap,
            "note": self.note,
        }


def _concavity_margin(obj: Quad, cons: list[Quad]) -> float:
    """Required distance of the largest Lagrangian eigenvalue from zero.

    A nearly singular Lagrangian Hessian makes the closed-form maximum
    ill-conditioned, so a certificate is only accepted with a relative margin.
    """
    h_obj = float(np.abs(obj.H).max())
    h_con = max((float(np.abs(c.H).max()) for c in cons), default=0.0)
    return 1e-6 * max(h_obj, 1e-12 * h_con, 1e-300)


def _dual_value(obj: Quad, cons: list[Quad], lam: np.ndarray) -> tuple[float, np.ndarray]:
    H = obj.H - sum(l * c.H for l, c in zip(lam, cons))
    g = obj.g - sum(l * c.g for l, c in zip(lam, cons))
    c0 = obj.c - sum(l * c.c for l, c in zip(lam, cons))
    eig = np.linalg.eigvalsh(H)
    if eig.max() > -_concavity_margin(obj, cons):
        return math.inf, eig
    ub = c0 - 0.5 * float(g @ np.linalg.solve(H, g))
    return ub, eig


def certify_max(obj: Quad, cons: list[Quad], x_w, active_tol: float = 1e-7) -> Certificate:
    """Upper bound of max obj over {cons <= 0}, starting from the feasible witness x_w."""
    x_w = np.asarray(x_w, dtype=float)
    fw = obj.value(x_w)
    scale = [max(1.0, abs(c.c), float(np.abs(c.H).max(initial=0.0)) * float(x_w @ x_w)) for c in cons]
    active = [i for i, c in enumerate(cons) if abs(c.value(x_w)) <= active_tol * scale[i]]
    lam = np.zeros(len(cons))
    stat = None
    if active:
        G = np.column_stack([cons[i].grad(x_w) for i in active])
        sol, *_ = np.linalg.lstsq(G, obj.grad(x_w), rcond=None)
        lam[active] = np.maximum(sol, 0.0)
        stat = float(np.max(np.abs(obj.grad(x_w) - G @ lam[active])))
    best_ub, best_eig = _dual_value(obj, cons, lam)
    best_lam = lam.copy()
    # derivative-free improvement over log-multipliers of the quadratic (curvature-carrying) constraints
    curv = [i for i, c in enumerate(cons) if np.abs(c.H).max() > 0]
    if curv:
        base = np.array([max(best_lam[i], 1e-12) for i in curv])

        def f(z):
            trial = best_lam.copy()
            trial[curv] = np.exp(z)
            ub, _ = _dual_value(obj, cons, trial)
            return ub if math.isfinite(ub) else 1e30

        starts = [np.log(base)]
        if not math.isfinite(best_ub):
            # make the Lagrangian concave with the current-limit multiplier first
            ic = next((i for i in curv if cons[i].name == "CURRENT"), None)
            if ic is not None:
                trial = base.copy()
                trial[curv.index(ic)] = max(1e-6, 0.6 * float(np.abs(np.linalg.eigvalsh(obj.H)).max()))
                starts.append(np.log(trial))
        for z0 in starts:
            res = minimize(f, z0, method="Nelder-Mead",
                           options={"xatol": 1e-10, "fatol": 1e-12, "maxiter": 4000, "maxfev": 8000})
            if res.fun < best_ub:
                trial = best_lam.copy()
                trial[curv] = np.exp(res.x)
                ub, eig = _dual_value(obj, cons, trial)
                if ub < best_ub:
                    best_ub, best_eig, best_lam = ub, eig, trial
    valid = math.isfinite(best_ub)
    mult = tuple((c.name, float(l)) for c, l in zip(cons, best_lam) if l > 0)
    return Certificate(
        valid=valid,
        upper_bound=best_ub if valid else None,
        multipliers=mult,
        hessian_eigenvalues=tuple(float(e) for e in best_eig),
        stationarity_residual=stat,
        witness_value=fw,
        gap=(best_ub - fw) if valid else None,
        note=("concave quadratic Lagrangian with nonnegative multipliers: global upper bound (weak duality)"
              if valid else "no nonnegative multipliers found that make the Lagrangian concave; no certificate"),
    )


def certify_torque(k: DriveKernel, direction: int, x_w, include_dc: bool = True) -> Certificate:
    """Certificate for max (direction=+1) or min (direction=-1) shaft torque."""
    T, cons = constant_model_quads(k, include_dc)
    if direction < 0:
        T = Quad("-T_shaft", -T.H, -T.g, -T.c)
    cert = certify_max(T, cons, x_w)
    if direction < 0 and cert.valid:
        return Certificate(cert.valid, -cert.upper_bound, cert.multipliers, cert.hessian_eigenvalues,
                           cert.stationarity_residual, -cert.witness_value, cert.gap,
                           cert.note + " (applied to -T_shaft: the bound is a lower bound of the minimum torque)")
    return cert
