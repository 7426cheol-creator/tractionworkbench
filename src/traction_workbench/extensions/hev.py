"""Hybrid (HEV / PHEV) electric drive system on one DC bus (OEW/HEV addendum section 6, P1-H).

One topology family at a time, from its declared connection graph - the position labels P0..P4 are shorthand only:

* machines EM_k (each a single-VSI ``DriveModel``; an OEW machine is analysed by ``extensions.oew``) on ONE common
  DC bus with one source (battery, optionally behind a boost converter);
* mechanical coupling through declared fixed ratios or one simple planetary set (Willis equation), torques
  positive INTO the gear set;
* joint capability K = {(T_1, T_2, ...): every component AND the common constraints hold together} - never the
  Cartesian box of the separate maxima; branch stress (each machine's DC current, losses) is kept next to the net
  source power (a small net does not hide an 80 / 70 kW branch pair);
* the common bus voltage is part of the solution (review R2 CT-02): without a converter the bus IS the battery
  terminal, V = OCV - R I with V I = sum P_dc,k(V) + aux + bus loss solved together with every machine; a declared
  boost regulates the bus and is solved on the battery side with the battery's own sag (duty, inductor current,
  loss, direction, UV at the battery);
* engine cranking as a time-domain replay of a declared crank-angle load (compression peak, initial angle, Vdc sag,
  traction reserve) - "cranking requirement met" is not "engine start guaranteed" (combustion needs engine evidence);
* load rejection: a generator that keeps feeding the bus after a traction trip; the common capacitor energy is
  counted once.

Every result is a model claim for the declared mode, initial state and policy; sampled sweeps are coverage evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..errors import InputValidationError
from ..models.components import DriveModel
from ..validation import finite as _finite
from ..scenario import DcSourceLimits, Scenario
from ..solvers.capability import policy_capability
from ..solvers.policy import PolicyEvaluator
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

TWO_PI = 2.0 * math.pi
UNLIMITED = DcSourceLimits(math.inf, math.inf, math.inf, math.inf)


# --------------------------------------------------------------------------------------------- mechanical graph

def planetary_speeds(Ns: int, Nr: int, sun=None, ring=None, carrier=None) -> dict:
    """Willis: Ns w_s + Nr w_r = (Ns + Nr) w_c; exactly one of the three speeds unknown (None)."""
    if Ns <= 0 or Nr <= 0 or Nr <= Ns:
        raise InputValidationError("simple planetary needs 0 < Ns < Nr (teeth)", field="planetary")
    known = [x is not None for x in (sun, ring, carrier)]
    if sum(known) != 2:
        raise InputValidationError("give exactly two of the three planetary speeds", field="planetary")
    if sun is None:
        sun = ((Ns + Nr) * carrier - Nr * ring) / Ns
    elif ring is None:
        ring = ((Ns + Nr) * carrier - Ns * sun) / Nr
    else:
        carrier = (Ns * sun + Nr * ring) / (Ns + Nr)
    return {"sun": float(sun), "ring": float(ring), "carrier": float(carrier)}


def planetary_torques(Ns: int, Nr: int, port: str, torque_Nm: float) -> dict:
    """Ideal (massless, lossless) planetary, torques INTO the gear set: (Ts, Tr, Tc) = lambda (Ns, Nr, -(Ns + Nr))."""
    n = {"sun": Ns, "ring": Nr, "carrier": -(Ns + Nr)}
    if port not in n:
        raise InputValidationError("port must be sun, ring or carrier", field="port")
    lam = torque_Nm / n[port]
    return {k: lam * v for k, v in n.items()}


def planetary_check(Ns: int, Nr: int, speeds_rpm: dict, torques_Nm: dict, limits_rpm: dict | None = None,
                    tol: float = 1e-6) -> dict:
    """H-04: kinematic (Willis) residual, torque-ratio residual and power residual of a proposed operating state.

    Speed and power balance alone do not fix the torque split: a distribution that closes the power balance but not
    the torque ratio is rejected (the gear set cannot hold it without acceleration or losses).
    """
    ws, wr, wc = (speeds_rpm[k] for k in ("sun", "ring", "carrier"))
    kin = Ns * ws + Nr * wr - (Ns + Nr) * wc
    Ts, Tr, Tc = (torques_Nm[k] for k in ("sun", "ring", "carrier"))
    lam = Ts / Ns
    ratio_res = max(abs(Tr - lam * Nr), abs(Tc + lam * (Ns + Nr)))
    w = {k: v * TWO_PI / 60 for k, v in speeds_rpm.items()}
    pwr = Ts * w["sun"] + Tr * w["ring"] + Tc * w["carrier"]
    scale_w = max(1.0, *(abs(v) for v in speeds_rpm.values()))
    scale_t = max(1.0, *(abs(v) for v in torques_Nm.values()))
    over = []
    for k, lim in (limits_rpm or {}).items():
        if k in speeds_rpm and abs(speeds_rpm[k]) > lim:
            over.append(f"{k}: |{speeds_rpm[k]:.0f}| rpm > {lim:.0f} rpm")
    ok_kin = abs(kin) <= tol * scale_w * (Ns + Nr)
    ok_tq = ratio_res <= tol * scale_t * (Ns + Nr)
    if not ok_kin or not ok_tq or over:
        st = Status.INFEASIBLE
        why = ([] if ok_kin else [f"Willis residual {kin:.4g} rpm*teeth"]) + \
              ([] if ok_tq else [f"torque-ratio residual {ratio_res:.4g} N*m (a speed/power-consistent split is not "
                                 f"enough)"]) + over
    else:
        st, why = Status.FEASIBLE, ["kinematics, ideal torque ratio and power balance close"]
    return {"status": st.value, "willis_residual": kin, "torque_ratio_residual_Nm": ratio_res,
            "power_residual_W": pwr, "detail": "; ".join(why),
            "assumptions": "massless, lossless simple planetary; torques positive into the gear set"}


# --------------------------------------------------------------------------------------------- DC source

@dataclass(frozen=True)
class Battery:
    ocv_V: float
    R_int_ohm: float
    limits: DcSourceLimits
    uv_min_V: float | None = None
    basis: str = ""

    def terminal(self, P_W: float) -> tuple[float | None, float | None]:
        """Terminal voltage and current for a terminal power P (discharge +): V = (OCV + sqrt(OCV^2 - 4 R P)) / 2."""
        if self.R_int_ohm == 0:
            return self.ocv_V, P_W / self.ocv_V
        disc = self.ocv_V ** 2 - 4.0 * self.R_int_ohm * P_W
        if disc < 0:
            return None, None
        v = 0.5 * (self.ocv_V + math.sqrt(disc))
        return v, P_W / v


@dataclass(frozen=True)
class BoostStage:
    """HV boost between battery and bus.  V_dc = V_b / (1 - D) (ideal CCM) is a sanity relation, the limits are the
    declared duty, inductor current, direction and a loss P = a0 + a2 I_L^2 with its basis."""

    D_max: float
    I_L_max_A: float
    a0_W: float = 0.0
    a2_W_per_A2: float = 0.0
    bidirectional: bool = True
    basis: str = ""

    def __post_init__(self):
        if not (0.0 <= _finite("D_max", self.D_max) < 1.0):
            raise InputValidationError("boost duty limit must be in [0, 1)", field="D_max")
        if _finite("I_L_max_A", self.I_L_max_A) <= 0:
            raise InputValidationError("inductor current limit must be > 0", field="I_L_max_A")

    def battery_side(self, P_bus_W: float, V_bus_V: float, V_bat_V: float) -> dict:
        D = 1.0 - V_bat_V / V_bus_V
        out = {"duty": D, "problems": []}
        if D < -1e-12:
            out["problems"].append(f"V_bus {V_bus_V:g} V below the battery voltage {V_bat_V:g} V (boost cannot buck)")
        if D > self.D_max:
            out["problems"].append(f"duty {D:.4f} > D_max {self.D_max:g}")
        if P_bus_W < 0 and not self.bidirectional:
            out["problems"].append("regenerative bus power but the boost is not bidirectional")
        a2, a0 = self.a2_W_per_A2, self.a0_W
        if a2 > 0:
            disc = V_bat_V ** 2 - 4 * a2 * (P_bus_W + a0)
            if disc < 0:
                out["problems"].append("boost loss model cannot deliver this bus power")
                out.update({"I_L_A": None, "P_bat_W": None, "P_loss_W": None})
                return out
            iL = (V_bat_V - math.sqrt(disc)) / (2 * a2)
        else:
            iL = (P_bus_W + a0) / V_bat_V
        loss = a0 + a2 * iL * iL
        if abs(iL) > self.I_L_max_A:
            out["problems"].append(f"|I_L| {abs(iL):.4g} A > {self.I_L_max_A:g} A")
        out.update({"I_L_A": iL, "P_bat_W": P_bus_W + loss, "P_loss_W": loss})
        return out

    def solve(self, P_bus_W: float, V_bus_V: float, battery: "Battery") -> dict:
        """Battery side of a regulated bus WITH the battery's own sag (review R2 CT-02):
        V_bat = OCV - R I_L and V_bat I_L = P_bus + a0 + a2 I_L^2, so (R + a2) I_L^2 - OCV I_L + P_bus + a0 = 0
        (the smaller root is the normal operating branch); D = 1 - V_bat / V_bus."""
        ocv, R = battery.ocv_V, battery.R_int_ohm
        k, c = R + self.a2_W_per_A2, P_bus_W + self.a0_W
        out = {"problems": [], "collapse": False, "duty": None, "I_L_A": None, "V_bat_V": None, "P_bat_W": None,
               "P_loss_W": None, "residual_W": None}
        if k == 0:
            iL = c / ocv
        else:
            disc = ocv * ocv - 4.0 * k * c
            if disc < 0:
                out["problems"].append(f"battery + boost cannot deliver {P_bus_W:.4g} W to the bus (battery-side "
                                       f"voltage collapse: OCV^2 < 4 (R + a2) (P + a0))")
                out["collapse"] = True
                return out
            iL = (ocv - math.sqrt(disc)) / (2.0 * k)
        vbat = ocv - R * iL
        loss = self.a0_W + self.a2_W_per_A2 * iL * iL
        D = 1.0 - vbat / V_bus_V
        if D < -1e-12:
            out["problems"].append(f"bus {V_bus_V:g} V below the battery terminal {vbat:.4g} V (boost cannot buck)")
        if D > self.D_max:
            out["problems"].append(f"duty {D:.4f} > D_max {self.D_max:g}")
        if P_bus_W < 0 and not self.bidirectional:
            out["problems"].append("regenerative bus power but the boost is not bidirectional")
        if abs(iL) > self.I_L_max_A:
            out["problems"].append(f"|I_L| {abs(iL):.4g} A > {self.I_L_max_A:g} A")
        out.update({"duty": D, "I_L_A": iL, "V_bat_V": vbat, "P_bat_W": vbat * iL, "P_loss_W": loss,
                    "residual_W": vbat * iL - (P_bus_W + loss)})
        return out


def _source_check(P_W: float, V_V: float, lim: DcSourceLimits) -> tuple[Status, str]:
    I = P_W / V_V
    rows = []
    if P_W > 0:
        rows = [("discharge power", lim.discharge_power_max_W, P_W), ("discharge current", lim.discharge_current_max_A, I)]
    elif P_W < 0:
        rows = [("charge power", lim.charge_power_max_W, -P_W), ("charge current", lim.charge_current_max_A, -I)]
    miss = [r[0] for r in rows if r[1] is None]
    bad = [f"{r[0]} {r[2]:.4g} > {r[1]:.4g}" for r in rows if r[1] is not None and not math.isinf(r[1])
           and r[2] > r[1] * (1 + 1e-9) + 1e-9]
    if bad:
        return Status.INFEASIBLE, "; ".join(bad)
    if miss:
        return Status.UNKNOWN, "undeclared limit(s) that can bind: " + ", ".join(miss)
    return Status.FEASIBLE, "within the declared source limits"


# --------------------------------------------------------------------------------------------- joint capability

@dataclass(frozen=True)
class BusMachine:
    name: str
    drive: DriveModel
    speed_rpm: float
    role: str = ""               # a name only: 'generator' does not guarantee generation


def _machine_point(m: BusMachine, T: float, V: float, cache: dict) -> dict:
    key = (m.name, float(T), float(V))
    r = cache.get(key)
    if r is None:
        sol = PolicyEvaluator(m.drive, Scenario(m.name, m.speed_rpm, V, UNLIMITED)).solve(float(T))
        pt = sol.point
        r = {"T_Nm": float(T), "status": sol.policy_claim.status.value, "electrical": sol.electrical.status.value,
             "P_dc_W": None if pt is None else pt.Pdc_W, "I_peak_A": None if pt is None else pt.i_peak_A,
             "loss_W": None if (pt is None or pt.Pdc_W is None or pt.Pshaft_W is None) else pt.Pdc_W - pt.Pshaft_W,
             "detail": sol.policy_claim.detail}
        cache[key] = r
    return r


def _machine_table(m: BusMachine, Vdc: float, torques, cache: dict | None = None) -> list[dict]:
    cache = {} if cache is None else cache
    return [dict(_machine_point(m, float(T), Vdc, cache)) for T in torques]


def machine_range(m: BusMachine, Vdc: float) -> tuple[float | None, float | None]:
    ev = PolicyEvaluator(m.drive, Scenario(m.name, m.speed_rpm, Vdc, UNLIMITED))
    hi = policy_capability(ev, +1)
    lo = policy_capability(ev, -1)
    return (lo.value_Nm if lo.accepted else None), (hi.value_Nm if hi.accepted else None)


VBUS_REL_TOL = 1e-7          # bus-voltage identity tolerance (relative to OCV)


def unregulated_bus(machines: list, torques: list, battery: Battery, aux_W: float = 0.0, bus_loss_W: float = 0.0,
                    cache: dict | None = None, max_iter: int = 40) -> dict:
    """Bus = battery terminal (no converter): V and every machine point solved together (review R2 CT-02).

    V = OCV - R I and V I = sum_k P_dc,k(V) + aux + bus loss.  Impossibility proof: every machine is passive
    (P_dc >= T w), so the bus cannot exceed V_ub = V_terminal(sum T w + aux + bus loss); a machine without an
    electrical solution at V_ub has none at any attainable bus voltage (a lower bus voltage only tightens its
    voltage limit), and V_ub below UV or no terminal voltage at all is a collapse.  A witness is a converged fixed
    point re-evaluated independently: voltage identity and power identity within tolerance.  Anything else is
    UNKNOWN (not proved impossible)."""
    cache = {} if cache is None else cache
    ocv, R = battery.ocv_V, battery.R_int_ohm
    tol = VBUS_REL_TOL * ocv
    p_lb = sum(float(T) * m.speed_rpm * TWO_PI / 60.0 for m, T in zip(machines, torques)) + aux_W + bus_loss_W
    v_ub, _ = battery.terminal(p_lb)
    base = {"V_ub_V": v_ub, "P_lower_bound_W": p_lb, "V_bus_V": None, "I_bat_A": None, "P_bus_W": None,
            "rows": None, "residual_V": None, "residual_P_W": None, "iterations": 0}
    if v_ub is None:
        return {**base, "status": "INFEASIBLE",
                "reason": f"the battery cannot deliver even the mechanical power + aux {p_lb:.4g} W "
                          f"(OCV^2 < 4 R P: voltage collapse)"}
    if battery.uv_min_V is not None and v_ub < battery.uv_min_V:
        return {**base, "status": "INFEASIBLE",
                "reason": f"the highest attainable bus voltage {v_ub:.4g} V (mechanical power only) is below the "
                          f"UV limit {battery.uv_min_V:g} V"}
    lim = battery.limits
    if p_lb > 0:
        for what, cap, low in (("discharge power", lim.discharge_power_max_W, p_lb),
                               ("discharge current", lim.discharge_current_max_A, p_lb / v_ub)):
            if cap is not None and not math.isinf(cap) and low > cap * (1 + 1e-9) + 1e-9:
                return {**base, "status": "INFEASIBLE",
                        "reason": f"{what} is at least {low:.4g} (mechanical power + aux at the highest attainable "
                                  f"bus voltage) > {cap:.4g}"}
    for m, T in zip(machines, torques):
        r = _machine_point(m, T, v_ub, cache)
        if r["electrical"] == "INFEASIBLE":
            return {**base, "status": "INFEASIBLE",
                    "reason": f"{m.name}: no electrical solution at {v_ub:.4g} V, the highest bus voltage attainable "
                              f"while the machines deliver their mechanical power (a lower bus voltage only tightens "
                              f"the voltage limit)"}
    V, it = v_ub, 0
    for it in range(1, max_iter + 1):
        rows = [_machine_point(m, T, V, cache) for m, T in zip(machines, torques)]
        bad = [m.name for m, r in zip(machines, rows) if r["status"] != "FEASIBLE" or r["P_dc_W"] is None]
        if bad:
            return {**base, "status": "UNKNOWN", "iterations": it,
                    "reason": f"coupled bus: {', '.join(bad)} not feasible at the iterate {V:.6g} V - no consistent "
                              f"bus voltage witnessed (not proved impossible)"}
        P = sum(r["P_dc_W"] for r in rows) + aux_W + bus_loss_W
        Vn, _ = battery.terminal(P)
        if Vn is None:
            return {**base, "status": "UNKNOWN", "iterations": it,
                    "reason": f"coupled bus: terminal-voltage collapse at the iterate ({P:.4g} W)"}
        done = abs(Vn - V) <= tol
        V = Vn
        if done:
            break
    else:
        return {**base, "status": "UNKNOWN", "iterations": it, "reason": "coupled bus iteration not converged"}
    rows = [_machine_point(m, T, V, cache) for m, T in zip(machines, torques)]
    if any(r["status"] != "FEASIBLE" or r["P_dc_W"] is None for r in rows):
        return {**base, "status": "UNKNOWN", "iterations": it,
                "reason": "coupled bus: a machine is not feasible at the converged voltage"}
    P = sum(r["P_dc_W"] for r in rows) + aux_W + bus_loss_W
    Vt, I = battery.terminal(P)
    res_v = None if Vt is None else V - Vt
    res_p = (V * (ocv - V) / R - P) if R > 0 else 0.0
    if Vt is None or abs(res_v) > tol:
        return {**base, "status": "UNKNOWN", "iterations": it, "residual_V": res_v,
                "reason": "coupled bus: the voltage identity does not close at the returned state"}
    return {**base, "status": "WITNESS", "V_bus_V": V, "I_bat_A": I, "P_bus_W": P, "rows": rows, "residual_V": res_v,
            "residual_P_W": res_p, "iterations": it, "reason": ""}


def joint_torque_set(machines: list, Vdc_V: float, battery: Battery, aux_W: float = 0.0, bus_loss_W: float = 0.0,
                     boost: BoostStage | None = None, n_levels: int = 21, request: tuple | None = None,
                     cooling_heat_max_W: float | None = None) -> dict:
    """Jointly feasible torque pairs of two machines on one bus (H-01, H-02; review R2 CT-02).

    Each machine runs its minimum-current policy at its own speed (a fixed policy: an INFEASIBLE cell is a policy
    counterexample, not a proof over every control); the common constraints are the one source (battery, through the
    boost when declared) and an optional shared coolant heat budget.  The same source power is never allocated to
    both branches.  With a boost the bus is regulated at ``Vdc_V`` and the battery side is solved with its sag;
    WITHOUT a boost the bus is the battery terminal and is solved with the machines (``Vdc_V`` is then not an
    independent input: the machines see the coupled voltage).
    """
    if len(machines) != 2:
        raise InputValidationError("the joint torque set is computed for two machines on one bus", field="machines")
    m1, m2 = machines
    regulated = boost is not None
    V_grid = float(Vdc_V) if regulated else battery.ocv_V
    rng1, rng2 = machine_range(m1, V_grid), machine_range(m2, V_grid)
    if None in rng1 or None in rng2:
        raise InputValidationError("machine capability not established at this speed / Vdc (check the model issues)",
                                   field="machines")
    t1 = np.linspace(rng1[0], rng1[1], n_levels)
    t2 = np.linspace(rng2[0], rng2[1], n_levels)
    if request is not None:
        t1 = np.unique(np.append(t1, request[0]))
        t2 = np.unique(np.append(t2, request[1]))
    cache: dict = {}
    tab1, tab2 = _machine_table(m1, V_grid, t1, cache), _machine_table(m2, V_grid, t2, cache)
    status = np.empty((t1.size, t2.size), dtype=object)
    psrc = np.full((t1.size, t2.size), np.nan)
    vbus = np.full((t1.size, t2.size), np.nan)
    reason = np.empty((t1.size, t2.size), dtype=object)
    cells = {}
    for i, r1 in enumerate(tab1):
        for j, r2 in enumerate(tab2):
            probs, info = [], {}
            if regulated:
                if r1["status"] == "INFEASIBLE" or r2["status"] == "INFEASIBLE":
                    status[i, j], reason[i, j] = "INFEASIBLE", "component"
                    continue
                if (r1["status"] != "FEASIBLE" or r2["status"] != "FEASIBLE" or r1["P_dc_W"] is None
                        or r2["P_dc_W"] is None):
                    status[i, j], reason[i, j] = "UNKNOWN", "component unresolved"
                    continue
                rows = [r1, r2]
                p_bus = r1["P_dc_W"] + r2["P_dc_W"] + aux_W + bus_loss_W
                bs = boost.solve(p_bus, float(Vdc_V), battery)
                if bs["collapse"]:
                    status[i, j], reason[i, j] = "INFEASIBLE", "; ".join(bs["problems"])
                    continue
                probs += bs["problems"]
                p_bat, v_src, v_bus = bs["P_bat_W"], bs["V_bat_V"], float(Vdc_V)
                info = {"boost": {k: bs[k] for k in ("duty", "I_L_A", "V_bat_V", "P_bat_W", "P_loss_W",
                                                     "residual_W")}}
            else:
                u = unregulated_bus([m1, m2], [float(t1[i]), float(t2[j])], battery, aux_W, bus_loss_W, cache)
                if u["status"] != "WITNESS":
                    status[i, j], reason[i, j] = u["status"], u["reason"]
                    cells[(i, j)] = u
                    continue
                rows = u["rows"]
                p_bus = p_bat = u["P_bus_W"]
                v_src = v_bus = u["V_bus_V"]
                info = {"coupled_bus": {k: u[k] for k in ("V_bus_V", "I_bat_A", "residual_V", "residual_P_W",
                                                          "iterations", "V_ub_V")}}
            psrc[i, j], vbus[i, j] = p_bat, v_bus
            st, why = _source_check(p_bat, v_src, battery.limits)
            if st is Status.INFEASIBLE:
                probs.append(why)
            if battery.uv_min_V is not None and v_src < battery.uv_min_V:
                probs.append(f"battery terminal {v_src:.4g} V < UV {battery.uv_min_V:g} V")
            if cooling_heat_max_W is not None and rows[0]["loss_W"] is not None and rows[1]["loss_W"] is not None:
                if rows[0]["loss_W"] + rows[1]["loss_W"] > cooling_heat_max_W:
                    probs.append("shared coolant heat budget exceeded")
            cells[(i, j)] = {"rows": rows, "P_bus_W": p_bus, "V_bus_V": v_bus, **info}
            if probs:
                # with a regulated bus the machine points do not depend on the source: a violated common constraint
                # is a counterexample of this policy; on an unregulated bus the witness state is the unique upper-
                # branch fixed point found - a violation there is reported, not claimed over every other state
                status[i, j] = "INFEASIBLE" if regulated else "UNKNOWN"
                reason[i, j] = "; ".join(probs)
            elif st is Status.UNKNOWN:
                status[i, j], reason[i, j] = "UNKNOWN", why
            else:
                status[i, j], reason[i, j] = "FEASIBLE", ""
    # conditional envelope: for every T1 the feasible T2 segments (a set, possibly disconnected)
    envelope = []
    for i in range(t1.size):
        segs, cur = [], None
        for j in range(t2.size):
            if status[i, j] == "FEASIBLE":
                cur = [float(t2[j]), float(t2[j])] if cur is None else [cur[0], float(t2[j])]
            elif cur is not None:
                segs.append(cur)
                cur = None
        if cur is not None:
            segs.append(cur)
        envelope.append({"T1_Nm": float(t1[i]), "T2_segments_Nm": segs})
    alone1 = [r["T_Nm"] for r in tab1 if r["status"] == "FEASIBLE"]
    alone2 = [r["T_Nm"] for r in tab2 if r["status"] == "FEASIBLE"]
    box = {"T1": [min(alone1), max(alone1)] if alone1 else None, "T2": [min(alone2), max(alone2)] if alone2 else None}
    box_cells = sum(1 for i in range(t1.size) for j in range(t2.size)
                    if tab1[i]["status"] == "FEASIBLE" and tab2[j]["status"] == "FEASIBLE")
    joint_cells = int(np.sum(status == "FEASIBLE"))
    bus = ({"topology": "regulated by the declared boost", "V_bus_V": float(Vdc_V),
            "note": "the battery side is solved with its sag: duty, inductor current, loss, UV at the battery"}
           if regulated else
           {"topology": "battery terminal (no converter)", "Vdc_input_V_not_used": float(Vdc_V),
            "note": "V = OCV - R I and V I = sum P_dc,k(V) + aux + bus loss solved with every machine; the machine "
                    "tables here are at the open-circuit voltage (the 'alone' reference)"})
    out = {"machines": [{"name": m.name, "speed_rpm": m.speed_rpm, "role": m.role, "drive_id": m.drive.drive_id,
                         "range_Nm": list(r)} for m, r in ((m1, rng1), (m2, rng2))],
           "T1_Nm": t1.tolist(), "T2_Nm": t2.tolist(), "status": status.tolist(), "reason": reason.tolist(),
           "P_source_W": np.where(np.isnan(psrc), None, psrc).tolist(),
           "V_bus_V": np.where(np.isnan(vbus), None, vbus).tolist(),
           "tables": {m1.name: tab1, m2.name: tab2}, "conditional_envelope": envelope,
           "separate_maxima_box": box, "box_cells_feasible_separately": box_cells, "joint_cells_feasible": joint_cells,
           "Vdc_V": V_grid, "bus": bus, "aux_W": aux_W, "bus_loss_W": bus_loss_W, "boost": regulated,
           "policy": "minimum-current per machine (fixed policy); common source counted once",
           "meaning": "sampled joint set: FEASIBLE cells are witnesses (every port's voltage identity closed); the "
                      "rectangle of separate maxima is not an available torque set"}
    if request is not None:
        i = int(np.argmin(np.abs(t1 - request[0])))
        j = int(np.argmin(np.abs(t2 - request[1])))
        c = cells.get((i, j), {})
        rows = c.get("rows") or [tab1[i], tab2[j]]
        branch = [rows[0]["P_dc_W"], rows[1]["P_dc_W"]]
        v_b = c.get("V_bus_V")
        net = None if None in branch else branch[0] + branch[1]
        circ = None if None in branch else (min(abs(branch[0]), abs(branch[1])) if branch[0] * branch[1] < 0 else 0.0)
        out["request"] = {"T1_Nm": float(t1[i]), "T2_Nm": float(t2[j]), "status": status[i, j], "reason": reason[i, j],
                          "V_bus_V": v_b, "branch_P_dc_W": branch,
                          "branch_I_dc_A": [None if (b is None or v_b is None) else b / v_b for b in branch],
                          "net_machines_W": net, "circulating_W": circ,
                          "P_source_W": None if np.isnan(psrc[i, j]) else float(psrc[i, j]),
                          "coupled_bus": c.get("coupled_bus"), "boost_state": c.get("boost"),
                          "proof_or_diagnosis": None if status[i, j] == "FEASIBLE" else c.get("reason", reason[i, j]),
                          "note": "branch stress (currents, losses, capacitor ripple) follows the branch powers, not "
                                  "the net"}
    return out


# --------------------------------------------------------------------------------------------- cranking replay

@dataclass(frozen=True)
class CrankLoad:
    """Declared crank-angle resisting torque (signed: compression returns energy after TDC), periodic in the crank
    angle, plus friction T_f = f0 + f1 w (+ f2 w^2), accessory torque and the reflected inertia at the crank."""

    angle_deg: tuple                 # table axis over one period
    torque_Nm: tuple                 # compression / pumping torque (+ resists forward rotation)
    period_deg: float = 180.0        # 4-cylinder four-stroke: one compression event per 180 deg
    f0_Nm: float = 0.0
    f1_Nm_s: float = 0.0
    f2_Nm_s2: float = 0.0
    aux_Nm: float = 0.0
    J_kgm2: float = 0.2
    basis: str = ""

    def __post_init__(self):
        if len(self.angle_deg) != len(self.torque_Nm) or len(self.angle_deg) < 2:
            raise InputValidationError("crank load table needs matching angle / torque rows", field="crank_load")
        if any(b <= a for a, b in zip(self.angle_deg, self.angle_deg[1:])):
            raise InputValidationError("crank angles must increase", field="crank_load.angle_deg")
        if _finite("J_kgm2", self.J_kgm2) <= 0:
            raise InputValidationError("inertia must be > 0", field="J_kgm2")
        if not self.basis.strip():
            raise InputValidationError("the crank load needs its basis (engine test trace / supplier envelope)",
                                       field="crank_load.basis")

    def comp(self, theta_deg: float) -> float:
        x = theta_deg % self.period_deg
        ang = np.asarray(self.angle_deg, dtype=float)
        tq = np.asarray(self.torque_Nm, dtype=float)
        return float(np.interp(x, ang, tq, period=self.period_deg))

    def friction(self, w: float) -> float:
        s = 1.0 if w > 0 else (-1.0 if w < 0 else 0.0)
        return s * self.f0_Nm + self.f1_Nm_s * w + s * self.f2_Nm_s2 * w * w


def _starter_tables(drive: DriveModel, ratio: float, n_crank_max: float, V_floor: float, n_speed: int = 9,
                    n_torque: int = 7) -> dict:
    """Starter capability and DC power at the UV floor on a speed grid (policy solves at grid points)."""
    speeds = np.linspace(0.0, n_crank_max * ratio * 1.1, n_speed)
    cap, grid = [], []
    for n in speeds:
        ev = PolicyEvaluator(drive, Scenario("crank", float(n), V_floor, UNLIMITED))
        c = policy_capability(ev, +1)
        tmax = c.value_Nm if c.accepted else None
        cap.append(tmax)
        row = []
        if tmax is not None:
            for T in np.linspace(0.0, tmax, n_torque):
                sol = ev.solve(float(T))
                pt = sol.point
                row.append((float(T), None if pt is None else pt.Pdc_W, None if pt is None else pt.i_peak_A))
        grid.append(row)
    return {"speeds_rpm": speeds, "Tmax_Nm": cap, "grid": grid}


def _interp_tables(tab: dict, n_em: float, T: float) -> tuple[float, float | None, float | None]:
    """Capability: the lower of the two neighbouring speed samples (a SCREENING envelope - an interior capability
    valley is not excluded); P_dc and current: bilinear on the solved grid.  Outside the solved speed range nothing
    is returned (no clamp to the table edge)."""
    sp = tab["speeds_rpm"]
    n = max(n_em, 0.0)
    if n > sp[-1] * (1 + 1e-12):
        return 0.0, None, None
    j = int(np.clip(np.searchsorted(sp, n, side="right") - 1, 0, sp.size - 2))
    caps = [tab["Tmax_Nm"][j], tab["Tmax_Nm"][j + 1]]
    if None in caps:
        return 0.0, None, None
    tcap = min(caps)
    Tc = min(T, tcap)
    vals = []
    for jj in (j, j + 1):
        row = tab["grid"][jj]
        ts = np.array([r[0] for r in row])
        ps = [r[1] for r in row]
        cs = [r[2] for r in row]
        if None in ps or None in cs:
            return Tc, None, None
        vals.append((float(np.interp(Tc, ts, ps)), float(np.interp(Tc, ts, cs))))
    w = (n - sp[j]) / (sp[j + 1] - sp[j])
    return Tc, (1 - w) * vals[0][0] + w * vals[1][0], (1 - w) * vals[0][1] + w * vals[1][1]


def cranking_replay(drive: DriveModel, ratio: float, load: CrankLoad, battery: Battery, T_cmd_Nm: float,
                    n_target_rpm: float, t_max_s: float, V_floor_V: float, theta0_deg=None, traction_reserve_W: float = 0.0,
                    aux_elec_W: float = 0.0, dt_s: float = 5e-4, T_comb=None, backstop: bool = False) -> dict:
    """Engine cranking replay (H-03): J w' = T_EM->e + T_comb - T_comp(theta) - T_f(w) - T_aux, theta' = w.

    ``ratio``: machine speed / crank speed (fixed, declared engaged mode; a slipping clutch needs its own state).
    The starter torque is limited by its capability at the declared UV floor ``V_floor_V`` (the replay checks that
    the bus stays above it); the battery supplies the starter DC power plus ``aux_elec_W`` while holding
    ``traction_reserve_W`` for traction.  Initial crank angles are sampled (coverage evidence).
    Coulomb friction sticks at rest; the crank may turn backwards under compression (rebound) unless a one-way
    ``backstop`` is declared.  Failures are classified (review R2): a violated source / floor / time requirement of
    the declared model is a counterexample (source violations re-verified with an exact starter solve); a starter
    point that is not established, a speed outside the solved table or a miss while the torque was clipped by the
    screening capability envelope is UNRESOLVED - never an INFEASIBLE.
    """
    for name, v in (("ratio", ratio), ("n_target_rpm", n_target_rpm), ("t_max_s", t_max_s), ("V_floor_V", V_floor_V),
                    ("dt_s", dt_s)):
        if _finite(name, v) <= 0:
            raise InputValidationError(f"{name} must be > 0", field=name)
    thetas = list(theta0_deg) if theta0_deg is not None else [float(x) for x in
                                                            np.linspace(0.0, load.period_deg, 12, endpoint=False)]
    tab = _starter_tables(drive, ratio, n_target_rpm, V_floor_V)
    runs = []
    wt = n_target_rpm * TWO_PI / 60

    def exact_source(n_em, Tem, p_bat_interp):
        """Re-verify a source-side failure with an exact starter solve at the declared floor voltage."""
        sol = PolicyEvaluator(drive, Scenario("crank", float(n_em), V_floor_V, UNLIMITED)).solve(float(Tem))
        if sol.point is None or sol.point.Pdc_W is None:
            return None
        return sol.point.Pdc_W + aux_elec_W

    for th0 in thetas:
        th, w, t = math.radians(th0), 0.0, 0.0
        tr = {"t_s": [], "n_rpm": [], "T_em_Nm": [], "T_comp_Nm": [], "P_dc_W": [], "V_bus_V": [], "I_peak_A": []}
        reached, fail, kind = None, [], None
        clipped = reversed_ = False
        vmin, pmax, imax, energy = math.inf, -math.inf, 0.0, 0.0
        steps = int(math.ceil(t_max_s / dt_s))
        for _ in range(steps):
            n_em = abs(w) * 60 / TWO_PI * ratio
            Tem, pdc, ipk = _interp_tables(tab, n_em, T_cmd_Nm)
            if pdc is None:
                fail.append(f"starter point not established at {n_em:.0f} rpm (outside the solved table or no "
                            f"solution)")
                kind = "unresolved"
                break
            clipped = clipped or Tem < T_cmd_Nm - 1e-12
            if w < 0:
                # braking quadrant (forward torque, backward rotation): same current magnitude, generating
                pdc = pdc - 2.0 * Tem * abs(w) * ratio
            p_bat = pdc + aux_elec_W

            def source_state(p):
                vv, _ = battery.terminal(p + traction_reserve_W)
                if vv is None:
                    return None, Status.INFEASIBLE, "battery cannot supply starter + reserve (terminal voltage collapse)"
                stt, wh = _source_check(p + traction_reserve_W, vv, battery.limits)
                if stt is Status.INFEASIBLE:
                    return vv, stt, (f"t = {t:.3f} s: {wh} (starter {p:.4g} W + reserve {traction_reserve_W:.4g} W)")
                if vv < V_floor_V:
                    return vv, Status.INFEASIBLE, (f"t = {t:.3f} s: bus {vv:.4g} V < floor {V_floor_V:g} V (the "
                                                   f"starter capability assumed the floor)")
                return vv, stt, ""

            v, st, why = source_state(p_bat)
            if st is Status.INFEASIBLE:
                # the tables interpolate between exact solves: a crossing counts only when the exact solve confirms it
                pe = exact_source(n_em, Tem, p_bat) if w >= 0 else None
                if pe is None:
                    fail += [why, "not confirmed: no exact starter solve at this state"]
                    kind = "unresolved"
                    break
                ve, ste, whye = source_state(pe)
                if ste is Status.INFEASIBLE:
                    fail.append(whye + " (exact starter solve)")
                    kind = "violation"
                    break
                p_bat, v = pe, ve
            tcomb = 0.0 if T_comb is None else float(T_comb(math.degrees(th), w))
            Tc = load.comp(math.degrees(th))
            drive_T = Tem * ratio + tcomb - Tc - load.aux_Nm
            if w == 0.0:
                if abs(drive_T) <= load.f0_Nm or (backstop and drive_T < 0):
                    acc = 0.0                                  # static friction holds (or the declared backstop)
                else:
                    acc = (drive_T - math.copysign(load.f0_Nm, drive_T)) / load.J_kgm2
            else:
                acc = (drive_T - load.friction(w)) / load.J_kgm2
            w_new = w + acc * dt_s
            if w != 0.0 and w_new * w < 0:
                w_new = 0.0                                    # friction stops the crank before it reverses
            if backstop and w_new < 0:
                w_new = 0.0
            if w_new < 0:
                reversed_ = True
            w = w_new
            th += w * dt_s
            t += dt_s
            energy += p_bat * dt_s
            vmin, pmax, imax = min(vmin, v), max(pmax, p_bat), max(imax, ipk)
            if len(tr["t_s"]) < 4000:
                for key, val in (("t_s", t), ("n_rpm", w * 60 / TWO_PI), ("T_em_Nm", Tem), ("T_comp_Nm", Tc),
                                 ("P_dc_W", pdc), ("V_bus_V", v), ("I_peak_A", ipk)):
                    tr[key].append(val)
            if w >= wt and reached is None:
                reached = t
                break
        if reached is None and not fail:
            fail.append(f"target {n_target_rpm:g} rpm not reached within {t_max_s:g} s")
            kind = "unresolved" if clipped else "violation"
            if clipped:
                fail.append("the starter torque was clipped by the screening capability envelope (lower of the "
                            "neighbouring speed samples): not a counterexample")
        ok = reached is not None and not fail
        runs.append({"theta0_deg": th0, "reached_s": reached, "ok": ok, "failures": fail, "failure_kind": kind,
                     "reversed": reversed_, "torque_clipped": clipped, "V_bus_min_V": vmin, "P_bat_max_W": pmax,
                     "I_peak_max_A": imax, "energy_J": energy, "trace": tr})
    bad = [r for r in runs if r["failure_kind"] == "violation"]
    open_ = [r for r in runs if r["failure_kind"] == "unresolved"]
    worst = max(runs, key=lambda r: (r["reached_s"] is None, r["reached_s"] or 0.0))
    q = f"crank to {n_target_rpm:g} rpm within {t_max_s:g} s from every sampled initial crank angle"
    scope = "cranking replay with the declared crank-angle load (not an engine-start guarantee)"
    if bad:
        b = bad[0]
        claim = Claim("cranking", Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,),
                      evidence=(Evidence.make(EvidenceKind.NUMERICAL_WITNESS,
                                              f"initial angle {b['theta0_deg']:g} deg: " + "; ".join(b["failures"])),),
                      detail=f"{len(bad)} of {len(runs)} sampled initial angles fail (a counterexample under the "
                             f"declared load and battery)")
    elif open_:
        b = open_[0]
        claim = Claim("cranking", Status.UNKNOWN, q, scope, reasons=(Reason.NUMERICAL_UNRESOLVED,),
                      detail=f"{len(open_)} of {len(runs)} sampled initial angles unresolved (initial angle "
                             f"{b['theta0_deg']:g} deg: " + "; ".join(b["failures"]) + ")")
    else:
        claim = Claim("cranking", Status.UNKNOWN, q, scope, reasons=(Reason.SAMPLED_COVERAGE,),
                      evidence=(Evidence.make(EvidenceKind.SAMPLED, f"{len(runs)} initial angles"),),
                      qualifiers=(f"met at all {len(runs)} sampled initial angles (worst {worst['reached_s']:.3f} s at "
                                  f"{worst['theta0_deg']:g} deg)",),
                      detail="sampled initial angles are coverage evidence, not a proof over the continuum; "
                             "combustion onset needs engine evidence")
    return {"claim": claim.to_dict(), "runs": runs, "worst": {k: worst[k] for k in ("theta0_deg", "reached_s", "ok")},
            "starter_capability": {"speeds_rpm": tab["speeds_rpm"].tolist(), "Tmax_Nm": tab["Tmax_Nm"],
                                   "V_floor_V": V_floor_V},
            "inputs": {"ratio": ratio, "T_cmd_Nm": T_cmd_Nm, "n_target_rpm": n_target_rpm, "t_max_s": t_max_s,
                       "traction_reserve_W": traction_reserve_W, "aux_elec_W": aux_elec_W, "J_kgm2": load.J_kgm2,
                       "period_deg": load.period_deg, "load_basis": load.basis, "backstop": backstop},
            "notes": ["starter capability evaluated at the UV floor and taken as the lower of neighbouring speed samples "
                      "(a screening envelope: an interior capability valley is not excluded); DC power / current "
                      "interpolated between exact policy solves",
                      "Coulomb friction holds the crank at rest; compression rebound is modelled unless a one-way "
                      "backstop is declared",
                      "engaged fixed-ratio connection assumed; clutch slip, combustion, NVH are outside this replay",
                      "result named 'cranking requirement', not 'engine start'"]}


# --------------------------------------------------------------------------------------------- load rejection

def load_rejection(C_uF: float, V0_V: float, V_max_V: float, sources_W: list, sinks_W: list, t_react_s: float,
                   t_ramp_s: float = 0.0, horizon_s: float | None = None, n: int = 400) -> dict:
    """Energy ledger after a traction trip (H-05; review R2 CT-05).

    Generators G = sum(sources) keep feeding the bus for ``t_react_s``, then ramp linearly to zero over
    ``t_ramp_s``; the sinks S = sum(sinks) keep drawing; the one common capacitor absorbs the excess:
    1/2 C (V^2 - V0^2) = E(t) = integral of (G g(t) - S).  ONE piecewise function gives the peak, the events and the
    trace: for G > S >= 0 the excess ends at t* = td + tr (G - S) / G and E_peak = (G - S) td + tr (G - S)^2 / (2 G);
    the threshold crossing is solved on the same function.  A constant-power sink that would empty the capacitor
    ends the model domain (stated), it is not continued through zero energy.
    """
    C = _finite("C_uF", C_uF) * 1e-6
    for name, v in (("V0_V", V0_V), ("V_max_V", V_max_V)):
        if _finite(name, v) <= 0:
            raise InputValidationError(f"{name} must be > 0", field=name)
    if V_max_V <= V0_V:
        raise InputValidationError("V_max must exceed V0", field="V_max_V")
    td, tr = _finite("t_react_s", t_react_s), _finite("t_ramp_s", t_ramp_s)
    if td < 0 or tr < 0:
        raise InputValidationError("reaction / ramp times must be >= 0", field="t_react_s")
    G, S = float(sum(sources_W)), float(sum(sinks_W))
    if G < 0 or S < 0:
        raise InputValidationError("source and sink powers are magnitudes >= 0", field="sources_W")
    P_ex = G - S
    E_margin = 0.5 * C * (V_max_V ** 2 - V0_V ** 2)
    E_empty = -0.5 * C * V0_V ** 2

    def energy(t: float) -> float:
        if t <= td:
            return P_ex * t
        E1 = P_ex * td
        if tr == 0.0:
            return E1 - S * (t - td)
        if t <= td + tr:
            tau = t - td
            return E1 + G * (tau - tau * tau / (2.0 * tr)) - S * tau
        return E1 + 0.5 * G * tr - S * (t - td)

    def excess(t: float) -> float:
        if t <= td:
            return P_ex
        if tr > 0.0 and t <= td + tr:
            return G * (1.0 - (t - td) / tr) - S
        return -S

    if P_ex > 0:
        t_peak = td + (tr * P_ex / G if tr > 0 else 0.0)
        E_peak = P_ex * td + (tr * P_ex * P_ex / (2.0 * G) if tr > 0 else 0.0)
    else:
        t_peak, E_peak = 0.0, 0.0
    V_peak = math.sqrt(V0_V ** 2 + 2.0 * E_peak / C)
    t_lim = None
    if E_peak > E_margin:
        if P_ex * td >= E_margin:
            t_lim = E_margin / P_ex
        else:
            a, b, c = G / (2.0 * tr), P_ex, E_margin - P_ex * td       # a tau^2 - b tau + c = 0 (smaller root)
            t_lim = td + (b - math.sqrt(max(b * b - 4.0 * a * c, 0.0))) / (2.0 * a)
    # the constant-power sink empties the capacitor after the source is gone: the model domain ends there
    E_end = energy(td + tr)
    t_empty = (td + tr + (E_end - E_empty) / S) if S > 0 else math.inf
    horizon = horizon_s or max(2.0 * (td + tr), 1e-4)
    domain_end = t_empty < horizon
    t_stop = min(horizon, t_empty)
    marks = [0.0, td, td + tr, t_peak] + ([t_lim] if t_lim is not None else [])
    grid = np.unique(np.concatenate([np.linspace(0.0, t_stop, n), [m for m in marks if 0.0 <= m <= t_stop]]))
    ex = np.array([excess(float(x)) for x in grid])
    if tr == 0.0 and 0.0 < td < t_stop:
        # the source is cut at td: the step is represented by both values at the same instant, so the trace's
        # piecewise-linear integral equals the ledger exactly
        k = int(np.searchsorted(grid, td)) + 1
        grid, ex = np.insert(grid, k, td), np.insert(ex, k, -S)
    E = np.array([energy(float(x)) for x in grid])
    V = np.sqrt(np.maximum(V0_V ** 2 + 2.0 * E / C, 0.0))
    q = f"DC bus stays below {V_max_V:g} V after the load rejection"
    if P_ex <= 0:
        st, det = Status.FEASIBLE, "the remaining sinks absorb the generated power: no excess energy"
    elif V_peak <= V_max_V:
        st, det = Status.FEASIBLE, (f"peak {V_peak:.6g} V at {t_peak * 1e6:.4g} us (the excess ends when the ramping "
                                    f"source falls to the sink power): below {V_max_V:g} V")
    else:
        st, det = Status.INFEASIBLE, (f"the {E_margin:.4g} J capacitor margin is used at {t_lim * 1e6:.4g} us, before "
                                      f"the excess ends at {t_peak * 1e6:.4g} us: peak {V_peak:.6g} V")
    claim = Claim("load_rejection", st, q, "lossless energy ledger of the one common capacitor",
                  reasons=() if st is Status.FEASIBLE else (Reason.CONSTRAINT_VIOLATION,),
                  evidence=(Evidence.make(EvidenceKind.DIRECT_EVALUATION,
                                          "exact integral of the declared piecewise source / sink power"),),
                  qualifiers=("screening: declared powers and reaction; ESR, inductance, protection clamps and "
                              "converter dynamics not modelled",), detail=det)
    return {"claim": claim.to_dict(), "P_excess_W": P_ex, "E_margin_J": E_margin, "E_peak_J": E_peak,
            "t_peak_s": t_peak, "time_to_limit_s": t_lim if t_lim is not None else math.inf,
            "V_peak_V": V_peak, "domain_end_s": t_empty if domain_end else None,
            "trace": {"t_s": grid.tolist(), "V_V": V.tolist(), "P_excess_W": ex.tolist()},
            "ledger": {"sources_W": list(sources_W), "sinks_W": list(sinks_W), "capacitor_uF": C_uF,
                       "note": "the common capacitor margin is counted once for all fault branches" +
                               ("; the constant-power sink empties the capacitor at the end of the trace (model "
                                "domain end)" if domain_end else "")}}
