"""Independent references for the fault-simulation plant (validation, not the simulation itself).

The plant (``plant.py``) is a dq machine with an ideal-diode complementarity bridge integrated by RK4 with located
events.  Agreement with the SAME equations under another integrator would only verify the integrator, so the
references here are other formulations:

* ``AbcReference`` - the machine in phase coordinates with the textbook position-dependent inductance matrix
  (L_jj = L_A - L_B cos 2(theta - phi_j), M_jk = -L_A / 2 - L_B cos 2(theta - (phi_j + phi_k) / 2), L_A = (L_d + L_q)
  / 3, L_B = (L_q - L_d) / 3 for a star winding without zero sequence), the isolated star point as an explicit
  neutral voltage, and every device as a monotone piecewise-linear conductance (on-conductance G_on, off-leakage
  G_off) - no complementarity, no events: the pole voltage is the inverse of the node's device characteristic.  A
  stiff implicit solver (scipy Radau) integrates it.  With G_on -> inf and G_off -> 0 it tends to the ideal bridge,
  so a difference to the plant that shrinks with the device idealisation is a modelling check of the bridge logic
  (six-switch-off rectification, open circuit, partial short circuits);
* closed forms: the steady active-short-circuit currents (v_d = v_q = 0), the uncontrolled-rectification onset
  sqrt(3) w_e psi = V_dc (no current below it), the capacitor energy balance;
* the matrix exponential of the linear ASC transient (``asc_transient``) - a numerics check of the same equations,
  reported as such.

``validation_suite`` runs the cases and returns every comparison with its tolerance and reason.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.integrate import solve_ivp

from .plant import (DcParams, LegCommand, MachineParams, Plant, integrate, phase_currents, E_BAT, E_BLEED, E_CU,
                    E_INV, E_MECH, ID, IQ, TH, VDC)

TWO_PI = 2.0 * math.pi
PHI = (0.0, TWO_PI / 3.0, -TWO_PI / 3.0)        # phase axes a, b, c


class AbcReference:
    """Phase-coordinate machine + conductance bridge + DC link (independent of the dq complementarity plant)."""

    def __init__(self, m: MachineParams, dc: DcParams, legs, G_on: float = 1e4, G_off: float = 1e-6):
        self.m, self.dc = m, dc
        self.legs = list(legs)                   # per leg: "off" | "upper_on" | "lower_on"
        self.G_on, self.G_off = G_on, G_off
        self.LA = (m.Ld + m.Lq) / 3.0
        self.LB = (m.Lq - m.Ld) / 3.0

    # -- machine ---------------------------------------------------------------------------------------------
    def L(self, th):
        LA, LB = self.LA, self.LB
        M = np.empty((3, 3))
        for j in range(3):
            for k in range(3):
                if j == k:
                    M[j, k] = LA - LB * math.cos(2.0 * (th - PHI[j]))
                else:
                    M[j, k] = -0.5 * LA - LB * math.cos(2.0 * (th - 0.5 * (PHI[j] + PHI[k])))
        return M

    def dL(self, th):
        LB = self.LB
        M = np.empty((3, 3))
        for j in range(3):
            for k in range(3):
                ang = th - PHI[j] if j == k else th - 0.5 * (PHI[j] + PHI[k])
                M[j, k] = 2.0 * LB * math.sin(2.0 * ang)
        return M

    def psi_pm(self, th):
        return np.array([self.m.psi * math.cos(th - PHI[k]) for k in range(3)])

    def dpsi_pm(self, th):
        return np.array([-self.m.psi * math.sin(th - PHI[k]) for k in range(3)])

    # -- devices: node current into the machine as a function of the pole voltage (w.r.t. the - rail) ---------
    def node_current(self, k, v, vdc):
        """Current delivered by leg k's devices into the phase at pole voltage v (monotone decreasing in v)."""
        Gon, Goff = self.G_on, self.G_off
        i = 0.0
        # upper diode: node -> + rail when v > vdc ; upper switch: + rail <-> node
        i -= Gon * max(0.0, v - vdc) + Goff * (v - vdc)
        # lower diode: - rail -> node when v < 0 ; lower switch
        i += Gon * max(0.0, -v) + Goff * (-v)
        if self.legs[k] == "upper_on":
            i += Gon * (vdc - v)
        elif self.legs[k] == "lower_on":
            i += Gon * (0.0 - v)
        return i

    def pole_voltage(self, k, i_k, vdc):
        """Invert the node characteristic: the pole voltage at which the devices deliver i_k (exact, piecewise
        linear, strictly monotone thanks to the off-leakage)."""
        # breakpoints 0 and vdc; the function is linear between them
        pts = [-1e9, 0.0, vdc, 1e9]
        vals = [self.node_current(k, p, vdc) for p in pts]
        for a, b, fa, fb in zip(pts[:-1], pts[1:], vals[:-1], vals[1:]):
            if (fa - i_k) * (fb - i_k) <= 0.0 and fa != fb:
                return a + (i_k - fa) * (b - a) / (fb - fa)
        return 0.0

    # -- ODE -------------------------------------------------------------------------------------------------
    def rhs(self, t, y, w_e, th0):
        ia, ib, vdc, ibat = y[0], y[1], y[2], y[3]
        i = np.array([ia, ib, -ia - ib])
        th = th0 + w_e * t
        v = np.array([self.pole_voltage(k, i[k], vdc) for k in range(3)])
        L, dL = self.L(th), self.dL(th)
        # v_k - v_n = R i_k + L di/dt + w dL i + w dpsi_pm ; sum di/dt = 0
        A = np.zeros((4, 4))
        A[:3, :3] = L
        A[:3, 3] = 1.0                               # + v_n on the left: L di + v_n = v - R i - w(dL i + dpsi)
        A[3, :3] = 1.0
        rhs = np.zeros(4)
        rhs[:3] = v - self.m.Rs * i - w_e * (dL @ i) - w_e * self.dpsi_pm(th)
        sol = np.linalg.solve(A, rhs)
        di = sol[:3]
        # DC side: current drawn from the + rail by the three legs
        i_plus = 0.0
        for k in range(3):
            up = -(self.G_on * max(0.0, v[k] - vdc) + self.G_off * (v[k] - vdc))   # into node from + rail
            if self.legs[k] == "upper_on":
                up += self.G_on * (vdc - v[k])
            i_plus += up
        dc = self.dc
        if dc.contactor_closed:
            if dc.inductive:
                dibat = (dc.V_oc - dc.R_bat * ibat - vdc) / dc.L_bat
                i_bat = ibat
            else:
                i_bat = (dc.V_oc - vdc) / dc.R_bat
                dibat = -ibat                    # unused state, kept at its exact value 0 (a nonzero Jacobian
        else:                                    # column keeps the solver's finite-difference step bounded)
            i_bat, dibat = 0.0, -ibat
        i_bl = vdc / dc.R_bleed if dc.R_bleed else 0.0
        dv = (i_bat - i_plus - i_bl) / dc.C
        return [di[0], di[1], dv, dibat]

    def run(self, speed_rpm, th0, x0_dq, vdc0, t_end, n_out=2001, rtol=1e-8, atol=1e-8):
        w_e = self.m.p * speed_rpm * TWO_PI / 60.0
        ia0 = phase_currents(x0_dq[0], x0_dq[1], th0)
        ibat0 = 0.0
        if self.dc.contactor_closed and self.dc.inductive:
            ibat0 = (self.dc.V_oc - vdc0) / self.dc.R_bat
        y0 = [ia0[0], ia0[1], vdc0, ibat0]
        tt = np.linspace(0.0, t_end, n_out)
        sol = solve_ivp(self.rhs, (0.0, t_end), y0, method="Radau", t_eval=tt, args=(w_e, th0), rtol=rtol,
                        atol=atol, max_step=2e-6)
        if not sol.success:
            raise RuntimeError(f"reference integration failed: {sol.message}")
        ia, ib = sol.y[0], sol.y[1]
        ic = -ia - ib
        th = th0 + w_e * sol.t
        # back to dq (amplitude invariant) for the torque
        i_al = (2 * ia - ib - ic) / 3.0
        i_be = (ib - ic) / math.sqrt(3.0)
        i_d = i_al * np.cos(th) + i_be * np.sin(th)
        i_q = -i_al * np.sin(th) + i_be * np.cos(th)
        m = self.m
        T = 1.5 * m.p * (m.psi * i_q + (m.Ld - m.Lq) * i_d * i_q)
        return {"t": sol.t, "i_a": ia, "i_b": ib, "i_c": ic, "i_d": i_d, "i_q": i_q, "T_em": T, "v_dc": sol.y[2],
                "nfev": sol.nfev}


# ------------------------------------------------------------------------------------------ plant-side runner

def plant_run(m: MachineParams, dc: DcParams, legs, speed_rpm, th0, x0_dq, vdc0, t_end, h_max=10e-6, n_out=2001):
    """The fault-simulation plant with fixed leg commands (no control), sampled on a uniform grid."""
    pl = Plant(m, DcParams(**dc.__dict__), 0.0)
    for k in range(3):
        pl.cmd[k] = LegCommand(legs[k])
    w_m = speed_rpm * TWO_PI / 60.0
    ibat0 = (dc.V_oc - vdc0) / dc.R_bat if (dc.contactor_closed and dc.inductive) else 0.0
    x = [x0_dq[0], x0_dq[1], th0, w_m, vdc0, ibat0, 0.0, 0.0, 0.0, 0.0, 0.0]
    modes = pl.decide_modes(x)
    tt = np.linspace(0.0, t_end, n_out)
    out = {"t": tt, "i_a": [], "i_b": [], "i_c": [], "i_d": [], "i_q": [], "T_em": [], "v_dc": []}
    t = 0.0
    for tk in tt:
        if tk > t:
            x, modes, t, _ = integrate(pl, x, modes, t, tk, h_max)
        ia = phase_currents(x[ID], x[IQ], x[TH])
        out["i_a"].append(ia[0])
        out["i_b"].append(ia[1])
        out["i_c"].append(ia[2])
        out["i_d"].append(x[ID])
        out["i_q"].append(x[IQ])
        out["T_em"].append(pl.torque(x[ID], x[IQ]))
        out["v_dc"].append(x[VDC])
    res = {k: np.asarray(v, dtype=float) for k, v in out.items()}
    E1_cap = 0.5 * dc.C * x[VDC] ** 2
    E0_cap = 0.5 * dc.C * vdc0 ** 2
    E1_mag = pl.magnetic_energy(x[ID], x[IQ])
    E0_mag = pl.magnetic_energy(x0_dq[0], x0_dq[1])
    res["energy"] = {"dc_residual_J": x[E_BAT] - x[E_BLEED] - x[E_INV] - (E1_cap - E0_cap),
                     "machine_residual_J": x[E_INV] - x[E_CU] - x[E_MECH] - (E1_mag - E0_mag),
                     "throughput_J": max(abs(x[E_BAT]), abs(x[E_INV]), abs(x[E_CU]), abs(x[E_MECH]), 1e-12)}
    res["zeno"] = dict(pl.zeno)
    return res


def _cmp(name, quantity, plant_v, ref_v, tol, basis, kind="independent formulation"):
    err = abs(plant_v - ref_v)
    return {"case": name, "quantity": quantity, "plant": float(plant_v), "reference": float(ref_v),
            "abs_error": float(err), "tolerance": float(tol), "pass": bool(err <= tol), "reference_kind": kind,
            "basis": basis}


def _wave_rows(name, pr, refs, basis, per):
    """Waveform agreement of the plant with the abc reference at increasing device idealisation."""
    rows = []
    pk = max(1.0, float(np.max(np.abs(refs[-1][1]["i_a"]))))
    errs = []
    for label, ab in refs:
        e = float(np.max(np.abs(pr["i_a"] - ab["i_a"])))
        errs.append(e)
        rows.append({"case": name, "quantity": f"max |i_a plant - i_a reference ({label})| [A]", "plant": e,
                     "reference": 0.0, "abs_error": e, "tolerance": 0.02 * pk, "pass": e <= 0.02 * pk,
                     "reference_kind": "independent formulation", "basis": basis})
    if len(errs) > 1:
        rows.append({"case": name, "quantity": "difference shrinks as the reference devices approach the ideal",
                     "plant": errs[-1], "reference": errs[0], "abs_error": errs[-1],
                     "tolerance": errs[0], "pass": errs[-1] <= errs[0] + 1e-9,
                     "reference_kind": "idealisation trend",
                     "basis": "the plant is the ideal-device limit: its distance to the reference must fall with "
                              "G_on up and G_off down"})
    ab = refs[-1][1]
    sel = pr["t"] >= pr["t"][-1] - 2 * per
    Tp, Tr = float(np.mean(pr["T_em"][sel])), float(np.mean(ab["T_em"][sel]))
    rows.append(_cmp(name, "mean air-gap torque over the last 2 electrical periods [N*m]", Tp, Tr,
                     0.02 * max(abs(Tr), 10.0), basis))
    return rows


def validation_suite(m: MachineParams, dc: DcParams, quick: bool = False, progress=None) -> list:
    """Every plant validation comparison: (case, quantity, plant, reference, error, tolerance, pass, basis)."""
    rows = []
    say = progress or (lambda f, msg: None)
    w = lambda rpm: m.p * rpm * TWO_PI / 60.0            # noqa: E731
    stiff = DcParams(C=dc.C, V_oc=dc.V_oc, R_bat=dc.R_bat, L_bat=None, R_bleed=None)
    ideal = ((("G_on 1e4 S, G_off 1e-5 S"), 1e4, 1e-5), (("G_on 1e5 S, G_off 1e-6 S"), 1e5, 1e-6))
    if quick:
        ideal = ideal[1:]
    # 1) steady ASC: closed form
    say(0.02, "ASC steady state (closed form)")
    for rpm in (3000.0, 12000.0):
        we = w(rpm)
        den = m.Rs ** 2 + we ** 2 * m.Ld * m.Lq
        idc, iqc = -we ** 2 * m.Lq * m.psi / den, -we * m.Rs * m.psi / den
        t_end = 12.0 * max(m.Ld, m.Lq) / m.Rs                  # many time constants: the transient has died out
        pr = plant_run(m, stiff, ("lower_on",) * 3, rpm, 0.3, (0.0, 0.0), dc.V_oc, t_end, h_max=10e-6, n_out=401)
        rows.append(_cmp(f"ASC steady state {rpm:g} rpm", "i_d [A]", pr["i_d"][-1], idc, 1e-3 * abs(idc) + 0.05,
                         "v_d = v_q = 0: i_d = -w^2 L_q psi / (R^2 + w^2 L_d L_q)", "closed form"))
        rows.append(_cmp(f"ASC steady state {rpm:g} rpm", "i_q [A]", pr["i_q"][-1], iqc, 1e-3 * abs(idc) + 0.05,
                         "i_q = -w R psi / (R^2 + w^2 L_d L_q)", "closed form"))
    # 2) ASC transient: matrix exponential of the same linear equations (numerics) and the abc reference
    say(0.1, "ASC transient (matrix exponential, abc reference)")
    rpm, x0 = 6000.0, (-150.0, 250.0)
    we = w(rpm)
    A = np.array([[-m.Rs / m.Ld, we * m.Lq / m.Ld], [-we * m.Ld / m.Lq, -m.Rs / m.Lq]])
    bvec = np.array([0.0, -we * m.psi / m.Lq])
    xss = -np.linalg.solve(A, bvec)
    from scipy.linalg import expm
    t_end = 5e-3
    pr = plant_run(m, stiff, ("lower_on",) * 3, rpm, 0.3, x0, dc.V_oc, t_end, h_max=10e-6, n_out=201)
    ex = np.array([xss + expm(A * tk) @ (np.array(x0) - xss) for tk in pr["t"]])
    err = float(np.max(np.hypot(pr["i_d"] - ex[:, 0], pr["i_q"] - ex[:, 1])))
    rows.append({"case": "ASC transient 6000 rpm (5 ms)", "quantity": "max |i_dq - exact| [A]", "plant": err,
                 "reference": 0.0, "abs_error": err, "tolerance": 1e-3, "pass": err <= 1e-3,
                 "reference_kind": "same equations, matrix exponential (numerics only)",
                 "basis": "linear ASC dynamics solved exactly; verifies the integrator and the ASC leg state"})
    refs = [(lab, AbcReference(m, stiff, ("lower_on",) * 3, G_on=g1, G_off=g0).run(rpm, 0.3, x0, dc.V_oc, t_end, 201))
            for lab, g1, g0 in ideal]
    rows += _wave_rows("ASC transient 6000 rpm (5 ms)", pr, refs, "abc machine, switches as conductances",
                       TWO_PI / we)
    # 3) open circuit below the rectification onset
    say(0.3, "open circuit below the rectification onset")
    onset = dc.V_oc / (math.sqrt(3.0) * m.psi * m.p) * 60.0 / TWO_PI
    rpm = 0.8 * onset
    pr = plant_run(m, stiff, ("off",) * 3, rpm, 0.3, (0.0, 0.0), dc.V_oc, 5e-3, n_out=201)
    rows.append(_cmp(f"open circuit {rpm:.0f} rpm (80 % of the onset {onset:.0f} rpm)", "max |i_phase| [A]",
                     float(np.max(np.abs(pr["i_a"]))), 0.0, 1e-6,
                     "sqrt(3) w_e psi < V_dc: the diodes never conduct", "closed form"))
    # 4) six-switch-off rectification above the onset
    t_w = 10e-3 if quick else 20e-3
    for j, f in enumerate((1.5,) if quick else (1.2, 1.5)):
        rpm = f * onset
        say(0.35 + 0.2 * j, f"six-switch-off {rpm:.0f} rpm")
        pr = plant_run(m, stiff, ("off",) * 3, rpm, 0.3, (0.0, 0.0), dc.V_oc, t_w, n_out=801)
        refs = [(lab, AbcReference(m, stiff, ("off",) * 3, G_on=g1, G_off=g0).run(rpm, 0.3, (0.0, 0.0), dc.V_oc,
                                                                                    t_w, 801))
                for lab, g1, g0 in ideal]
        rows += _wave_rows(f"six-switch-off {rpm:.0f} rpm", pr, refs, "abc machine, diodes as conductances: "
                                                                       "uncontrolled rectification", TWO_PI / w(rpm))
        e = pr["energy"]
        rel = (abs(e["dc_residual_J"]) + abs(e["machine_residual_J"])) / e["throughput_J"]
        rows.append({"case": f"six-switch-off {rpm:.0f} rpm", "quantity": "energy ledger residual / throughput",
                     "plant": rel, "reference": 0.0, "abs_error": rel, "tolerance": 1e-5, "pass": rel <= 1e-5,
                     "reference_kind": "energy balance",
                     "basis": "battery - bleeder - bridge = d(1/2 C v^2); bridge = copper + air gap + d(magnetic)"})
    # 5) asymmetric partial short: leg a upper on, b and c passive
    say(0.75, "partial short (asymmetric)")
    rpm = 3000.0
    pr = plant_run(m, stiff, ("upper_on", "off", "off"), rpm, 0.3, (0.0, 0.0), dc.V_oc, t_w, n_out=801)
    refs = [(lab, AbcReference(m, stiff, ("upper_on", "off", "off"), G_on=g1, G_off=g0).run(rpm, 0.3, (0.0, 0.0),
                                                                                             dc.V_oc, t_w, 801))
            for lab, g1, g0 in ideal]
    rows += _wave_rows("partial short (a upper on, b / c passive) 3000 rpm", pr, refs,
                       "asymmetric bridge state without a symmetric steady state; abc reference", TWO_PI / w(rpm))
    # 6) convergence: a quarter of the step changes the six-switch-off current by less than the tolerance
    say(0.9, "step convergence")
    rpm = 1.5 * onset
    a = plant_run(m, stiff, ("off",) * 3, rpm, 0.3, (0.0, 0.0), dc.V_oc, 4e-3, h_max=10e-6, n_out=801)
    b = plant_run(m, stiff, ("off",) * 3, rpm, 0.3, (0.0, 0.0), dc.V_oc, 4e-3, h_max=2.5e-6, n_out=801)
    d = float(np.max(np.abs(a["i_a"] - b["i_a"])))
    rows.append({"case": f"step convergence six-switch-off {rpm:.0f} rpm", "quantity": "max |i_a(h) - i_a(h/4)| [A]",
                 "plant": d, "reference": 0.0, "abs_error": d, "tolerance": 0.05, "pass": d <= 0.05,
                 "reference_kind": "numerical convergence", "basis": "h = 10 us vs 2.5 us, events located to 1 ns"})
    say(1.0, "done")
    return rows
